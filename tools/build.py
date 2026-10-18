"""Build the module zip.

    python tools/build.py --rom <rom>/images   # take the launcher (and theme) straight from a fastboot ROM
    python tools/build.py                      # use apk/MiuiHome.apk (and theme/cn, if present)

The launcher and the Recents overlay always ship together: only the Recents component may open
the gesture input channel, so whichever launcher is home without it crash-loops. POCO Launcher
stays installed and untouched, so disabling the module restores it with its layout.

The overlay ships pre-built in overlay/prebuilt/. Building it needs aapt2, zipalign and apksigner
(Android SDK build-tools), an android.jar (SDK platforms) and keytool (a JDK or JRE); that only
happens when the launcher's Recents component differs from the pre-built one, or with
--rebuild-overlay / --update-prebuilt-overlay. The tools are looked up in toolchain/ first, then
ANDROID_HOME, ANDROID_SDK_ROOT, %LOCALAPPDATA%\\Android\\Sdk, JAVA_HOME and PATH.
"""
import argparse
import datetime
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import apkinfo  # noqa: E402
import romfs  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LAUNCHER_PKG = "com.miui.home"
# Not "MiuiHome": on POCO-branded ROMs Xiaomi's PackageManagerServiceImpl.initIgnoreApps skips
# /product/priv-app/MiuiHome whenever /product/priv-app/MiLauncherGlobal also exists.
APP_DIR = "system/product/priv-app/HyperOSLauncher"
APP_APK = "HyperOSLauncher.apk"
PERM_XML = "system/product/etc/permissions/privapp-permissions-miuihome.xml"
OVERLAY_DIR = "system/product/overlay/MiuiHomeRecentsOverlay"
OVERLAY_APK = "MiuiHomeRecentsOverlay.apk"
PREBUILT_DIR = ROOT / "overlay" / "prebuilt"
PREBUILT_APK = PREBUILT_DIR / OVERLAY_APK
PREBUILT_INFO = PREBUILT_DIR / "overlay.json"
KEYSTORE = ROOT / "keys" / "overlay.jks"
KEY_PASS = "android"  # only signs the local overlay; not a secret
# Xiaomi's MIUI app key: the one POCO Launcher and the Theme Store are signed with on stock ROMs.
# The Theme Store reaches the launcher through signature-protected permissions, so the launcher must match.
MIUI_CERT_SHA256 = "c9009d01ebf9f5d0302bc71b2fe9aa9a47a432bba17308a3111b75d7b2149025"
# Only the module's own scripts get LF line endings; theme packages include extensionless binary
# zips (default/icons, lock_wallpaper, ...) that must be copied byte for byte.
LF_FILES = {"customize.sh", "service.sh", "module.prop", "system.prop", "update-binary", "updater-script"}
# Replaces the ROM's whole theme folder (".replace"), so global and China assets never mix.
THEME_DIR = "system/product/media/theme"
# Left out of the theme folder: the China ROM's optional 小米兰亭 Pro font package. Nothing in the
# Default theme refers to it, and the global Theme Store doesn't list it, so it would be dead weight.
THEME_EXCLUDE = ["MILanProVF.mtz"]

# Where Xiaomi ROMs keep things, in order of preference.
ROM_LAUNCHER = [("product", "/priv-app/MiuiHome/MiuiHome.apk"),
                ("system_ext", "/priv-app/MiuiHome/MiuiHome.apk"),
                ("system", "/system/priv-app/MiuiHome/MiuiHome.apk")]
ROM_THEME = [("product", "/media/theme"), ("system", "/system/media/theme")]
ROM_PROPS = [("mi_ext", "/etc/build.prop"), ("product", "/etc/build.prop"), ("system", "/system/build.prop")]

TOOLCHAIN = ROOT / "toolchain"
LOCAL_JRE = TOOLCHAIN / "jre"


def sdk_dirs():
    if (TOOLCHAIN / "sdk").is_dir():
        yield TOOLCHAIN / "sdk"
    for var in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        if os.environ.get(var):
            yield Path(os.environ[var])
    if os.environ.get("LOCALAPPDATA"):
        yield Path(os.environ["LOCALAPPDATA"]) / "Android" / "Sdk"


def newest(paths):
    paths = sorted(paths, key=lambda p: [int(x) if x.isdigit() else 0 for x in p.parent.name.replace("android-", "").split(".")])
    return paths[-1] if paths else None


def find_build_tool(name):
    exe = shutil.which(name)
    if exe:
        return Path(exe)
    for sdk in sdk_dirs():
        for ext in ("", ".exe", ".bat"):
            hit = newest(sdk.glob(f"build-tools/*/{name}{ext}"))
            if hit:
                return hit
    return None


def find_android_jar():
    for sdk in sdk_dirs():
        hit = newest(sdk.glob("platforms/android-*/android.jar"))
        if hit:
            return hit
    return None


def java_home():
    if (LOCAL_JRE / "bin").is_dir():
        return LOCAL_JRE
    return Path(os.environ["JAVA_HOME"]) if os.environ.get("JAVA_HOME") else None


def find_keytool():
    home = java_home()
    if home:
        for ext in ("", ".exe"):
            p = home / "bin" / f"keytool{ext}"
            if p.exists():
                return p
    exe = shutil.which("keytool")
    return Path(exe) if exe else None


def run(*cmd):
    # apksigner.bat finds Java through JAVA_HOME / PATH; hand it the chosen runtime for this call only.
    env = os.environ.copy()
    home = java_home()
    if home:
        env["JAVA_HOME"] = str(home)
        env["PATH"] = str(home / "bin") + os.pathsep + env.get("PATH", "")
    subprocess.run([str(c) for c in cmd], check=True, env=env)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# --- Taking the launcher, theme and build details out of a ROM --------------------------------

def rom_fs(parts, name):
    for n in (f"{name}_a", name, f"{name}_b"):
        if n in parts and parts[n].size:
            return romfs.Erofs(parts[n])
    return None


def rom_find(parts, candidates):
    """First (fs, inode, label) among (partition, path) candidates, or None."""
    for pname, path in candidates:
        fs = rom_fs(parts, pname)
        if fs is None:
            continue
        try:
            return fs, fs.lookup(path), f"{pname}{path}"
        except KeyError:
            continue
    return None


def rom_details(parts):
    props = {}
    for candidate in reversed(ROM_PROPS):  # earlier files win
        hit = rom_find(parts, [candidate])
        if hit:
            for line in hit[1].data().decode("utf-8", "replace").splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    key, value = line.split("=", 1)
                    props[key.strip()] = value.strip()
    # mod_device is "myron" on official ROMs and e.g. "myron_xiaomieu_global" on xiaomi.eu.
    mod = props.get("ro.product.mod_device") or props.get("ro.product.product.name", "").removeprefix("miproduct_")
    details = {
        "romBuild": props.get("ro.mi.os.version.incremental") or props.get("ro.build.version.incremental"),
        "romRegion": props.get("ro.miui.build.region"),
        "romDevice": mod.split("_")[0] if mod else None,
        "romAndroid": props.get("ro.build.version.release"),
        "romSdk": props.get("ro.build.version.sdk"),
    }
    return {k: v for k, v in details.items() if v}


def extract_from_rom(images, dest, want_theme):
    if not glob.glob(os.path.join(images, "super.img*")):
        sys.exit(f"No super.img in {images}; point --rom at the ROM's images/ folder")
    parts, _ = romfs.open_partition(str(images), "")
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)

    hit = rom_find(parts, ROM_LAUNCHER)
    if hit is None:
        sys.exit("No MiuiHome.apk in this ROM (looked in " + ", ".join(p + f for p, f in ROM_LAUNCHER) + ")")
    apk = dest / "MiuiHome.apk"
    apk.write_bytes(hit[1].data())
    print(f"- Launcher from ROM {hit[2]}")

    theme = None
    if want_theme:
        found = rom_find(parts, ROM_THEME)
        if found:
            theme = dest / "theme"
            n = romfs.copy_tree(found[0], found[1], str(theme), quiet=True)
            print(f"- Theme folder from ROM {found[2]} ({n} files)")
        else:
            print("- No theme folder in this ROM; keeping the phone's own")
    return apk, theme, rom_details(parts)


# --- The Recents overlay ----------------------------------------------------------------------

def build_overlay(component, work):
    tools = {
        "aapt2": find_build_tool("aapt2"),
        "zipalign": find_build_tool("zipalign"),
        "apksigner": find_build_tool("apksigner"),
        "android.jar": find_android_jar(),
        "keytool": find_keytool(),
    }
    missing = [k for k, v in tools.items() if v is None]
    if missing:
        sys.exit(f"Building the overlay needs {', '.join(missing)}. Install the Android SDK build-tools, "
                 "a platform and a JDK or JRE (see README).")

    src = work / "overlay"
    shutil.copytree(ROOT / "overlay", src, ignore=shutil.ignore_patterns("prebuilt"))
    config = src / "res" / "values" / "config.xml"
    config.write_text(config.read_text(encoding="utf-8").replace("@RECENTS_COMPONENT@", component),
                      encoding="utf-8", newline="\n")

    run(tools["aapt2"], "compile", "--dir", src / "res", "-o", work / "res.zip")
    run(tools["aapt2"], "link", "-I", tools["android.jar"], "--manifest", src / "AndroidManifest.xml",
        "--min-sdk-version", "29", "--target-sdk-version", "36", "-o", work / "unsigned.apk", work / "res.zip")
    run(tools["zipalign"], "-f", "-p", "4", work / "unsigned.apk", work / "aligned.apk")

    if not KEYSTORE.exists():
        KEYSTORE.parent.mkdir(exist_ok=True)
        run(tools["keytool"], "-genkeypair", "-keystore", KEYSTORE, "-alias", "overlay", "-keyalg", "RSA",
            "-keysize", "2048", "-validity", "10000", "-storepass", KEY_PASS, "-keypass", KEY_PASS,
            "-dname", "CN=MiuiHome Recents overlay")
    signed = work / OVERLAY_APK
    run(tools["apksigner"], "sign", "--ks", KEYSTORE, "--ks-pass", f"pass:{KEY_PASS}",
        "--key-pass", f"pass:{KEY_PASS}", "--out", signed, work / "aligned.apk")
    return signed


def prebuilt_problem(component):
    """Why the pre-built overlay can't be used for this launcher, or None if it can."""
    if not (PREBUILT_APK.exists() and PREBUILT_INFO.exists()):
        return "there is no pre-built overlay"
    info = json.loads(PREBUILT_INFO.read_text(encoding="utf-8"))
    if info.get("component") != component:
        return f"this launcher's Recents is {component}, the pre-built overlay targets {info.get('component')}"
    if info.get("sha256") != sha256(PREBUILT_APK):
        return "the pre-built overlay doesn't match the checksum in overlay.json"
    return None


def get_overlay(component, work, mode):
    """mode: "auto" (pre-built when it fits), "rebuild" or "update" (rebuild and refresh overlay/prebuilt)."""
    if mode == "auto":
        problem = prebuilt_problem(component)
        if problem is None:
            print("- Using the pre-built Recents overlay")
            return PREBUILT_APK
        print(f"- Building the Recents overlay: {problem}")
    apk = build_overlay(component, work)
    if mode == "update":
        PREBUILT_DIR.mkdir(exist_ok=True)
        shutil.copy2(apk, PREBUILT_APK)
        PREBUILT_INFO.write_text(json.dumps({"component": component, "sha256": sha256(PREBUILT_APK)}, indent=2) + "\n",
                                 encoding="utf-8", newline="\n")
        print(f"- Updated {PREBUILT_APK.relative_to(ROOT).as_posix()} and overlay.json")
    return apk


# --- Staging -----------------------------------------------------------------------------------

def check_apk(apk, info, allow_any_signer):
    if info["package"] != LAUNCHER_PKG:
        sys.exit(f"Expected {LAUNCHER_PKG}, got {info['package']}")
    cert = apkinfo.signer_cert_sha256(apk)
    if cert != MIUI_CERT_SHA256:
        msg = (f"APK signer {cert or 'unknown'} is not Xiaomi's MIUI key; the Theme Store relies on "
               "signature-protected launcher permissions. Take the launcher from an official Xiaomi ROM")
        if not allow_any_signer:
            sys.exit(msg + " (or pass --allow-any-signer for a test build).")
        print("! " + msg)
    if info["sharedUserId"]:
        print(f"! sharedUserId={info['sharedUserId']}: this only installs if the APK is signed with the "
              "key that owns that UID on your ROM. Expect it to be skipped otherwise.")
    if not info["home_activities"]:
        sys.exit("No HOME activity in the APK; is this really the launcher?")
    if len(info["recents_activities"]) != 1:
        sys.exit(f"Expected one RecentsActivity, found {info['recents_activities']}")
    return cert


def stage_app(apk, info, stage):
    app = stage / APP_DIR
    app.mkdir(parents=True)
    shutil.copy2(apk, app / APP_APK)
    # A read-only partition can't extract compressed native libs at install time, so ship them unpacked.
    arm64 = [(f, c) for f, c in info["native_libs"] if f.startswith("lib/arm64-v8a/")]
    if any(c for _, c in arm64):
        lib_dir = app / "lib" / "arm64"
        lib_dir.mkdir(parents=True)
        with zipfile.ZipFile(apk) as z:
            for f, _ in arm64:
                (lib_dir / Path(f).name).write_bytes(z.read(f))
        print(f"- Extracted {len(arm64)} arm64 native libs")


def stage_theme(src, stage):
    """Copy a ROM's /product/media/theme (e.g. the China default theme) over the stock one."""
    required = [src / ".data" / "meta" / "theme" / "default.mrm", src / "default.mtz"]
    missing = [p for p in required if not p.exists()]
    if missing:
        sys.exit(f"{src} doesn't look like a /product/media/theme copy (missing {missing[0].relative_to(src)})")
    dest = stage / THEME_DIR
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns(*THEME_EXCLUDE))
    skipped = [n for n in THEME_EXCLUDE if (src / n).exists()]
    if skipped:
        print(f"- Left out of the theme folder: {', '.join(skipped)}")
    (dest / ".replace").touch()
    files = [p for p in dest.rglob("*") if p.is_file() and p.name != ".replace"]
    print(f"- Theme folder: {len(files)} files, {sum(p.stat().st_size for p in files) / 2**20:.1f} MiB")


def short_version(version_name):
    m = re.search(r"\d+(?:\.\d+)+", str(version_name))
    return m.group(0) if m else str(version_name)


def write_module_prop(path, info, cert, details, theme_source):
    """Fill in the template and append build details (extra keys are ignored by KernelSU / Magisk)."""
    launcher = short_version(info["versionName"])
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("version="):
            line += f" (launcher {launcher}" + (f", {details['romBuild']})" if "romBuild" in details else ")")
        lines.append(line.replace("@LAUNCHER_VERSION@", launcher))
    lines += [f"launcherVersionName={info['versionName']}",
              f"launcherVersionCode={info['versionCode']}",
              f"launcherCert={cert}"]
    lines += [f"{k}={details[k]}" for k in ("romBuild", "romRegion", "romDevice", "romAndroid", "romSdk") if k in details]
    lines += [f"themeSource={theme_source}", f"builtAt={datetime.date.today().isoformat()}"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def write_zip(stage, out):
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(stage.rglob("*")):
            if path.is_dir():
                continue
            arc = path.relative_to(stage).as_posix()
            if path.name in LF_FILES:
                z.writestr(arc, path.read_bytes().replace(b"\r\n", b"\n"))
            else:
                z.write(path, arc)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rom", type=Path, help="a fastboot ROM's images/ folder to take the launcher and theme from")
    ap.add_argument("--apk", type=Path, help="launcher APK to use instead of --rom (default: apk/MiuiHome.apk)")
    ap.add_argument("--theme", type=Path, help="theme folder to use instead of --rom's (default: theme/cn, if present)")
    ap.add_argument("--no-theme", action="store_true", help="keep the phone's own theme folder")
    ap.add_argument("--rebuild-overlay", action="store_true", help="build the Recents overlay even if the pre-built one fits")
    ap.add_argument("--update-prebuilt-overlay", action="store_true",
                    help="rebuild the overlay and replace overlay/prebuilt with it (maintainers)")
    ap.add_argument("--allow-any-signer", action="store_true",
                    help="accept a launcher not signed with Xiaomi's MIUI key (theming will likely break)")
    args = ap.parse_args()

    out_dir = ROOT / "out"
    details, theme, theme_source = {}, None, "stock"
    if args.rom:
        if args.apk:
            sys.exit("Use either --rom or --apk, not both")
        apk, rom_theme, details = extract_from_rom(args.rom, out_dir / "rom-extract", not args.no_theme and not args.theme)
        if rom_theme:
            theme, theme_source = rom_theme, details.get("romBuild", "rom") + (f" ({details['romRegion']})" if "romRegion" in details else "")
        print("- ROM: " + ", ".join(f"{k}={v}" for k, v in details.items()))
    else:
        apk = args.apk or ROOT / "apk" / "MiuiHome.apk"
        if not apk.exists():
            sys.exit(f"{apk} not found; use --rom <rom>/images or extract it first (see README)")
    if not args.no_theme and args.theme:
        theme, theme_source = args.theme, args.theme.name
    elif not args.no_theme and not args.rom and (ROOT / "theme" / "cn").is_dir():
        theme, theme_source = ROOT / "theme" / "cn", "theme/cn"
    if args.no_theme:
        theme, theme_source = None, "stock"

    info = apkinfo.read_apk(apk)
    cert = check_apk(apk, info, args.allow_any_signer)
    component = f"{LAUNCHER_PKG}/{info['recents_activities'][0]}"
    print(f"- {LAUNCHER_PKG} {info['versionName']}, Recents -> {component}")
    optional = [n for n, required in info["libraries"] if not required]
    required = [n for n, required in info["libraries"] if required]
    if required:
        print(f"! Required shared libraries (the phone must have them): {', '.join(required)}")
    if optional:
        print(f"- Optional shared libraries: {', '.join(optional)}")

    stage = out_dir / "stage"
    work = out_dir / "work"
    for d in (stage, work):
        shutil.rmtree(d, ignore_errors=True)
    shutil.copytree(ROOT / "module", stage)
    work.mkdir(parents=True)

    stage_app(apk, info, stage)
    perm = stage / PERM_XML
    perm.parent.mkdir(parents=True)
    perm.write_text(apkinfo.privapp_xml(info), encoding="utf-8", newline="\n")

    mode = "update" if args.update_prebuilt_overlay else "rebuild" if args.rebuild_overlay else "auto"
    overlay_dir = stage / OVERLAY_DIR
    overlay_dir.mkdir(parents=True)
    shutil.copy2(get_overlay(component, work, mode), overlay_dir / OVERLAY_APK)

    if theme:
        stage_theme(theme, stage)

    prop = stage / "module.prop"
    module_version = next(l.split("=", 1)[1] for l in prop.read_text(encoding="utf-8").splitlines()
                          if l.startswith("version="))
    write_module_prop(prop, info, cert, details, theme_source)

    name = f"poco-hyperos-launcher-{module_version}-{short_version(info['versionName'])}"
    if "romBuild" in details:
        name += f"-{details['romBuild']}"
    out = out_dir / f"{name}.zip"
    write_zip(stage, out)
    print(f"- Wrote {out}")


if __name__ == "__main__":
    main()
