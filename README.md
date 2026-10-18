**English** | [简体中文](README.zh-CN.md)

# HyperOS Launcher for POCO

A KernelSU / Magisk module that replaces POCO Launcher with the stock **HyperOS launcher**
(`com.miui.home`) on rooted POCO phones, so Theme Store themes apply fully. Gestures and Recents keep
working. POCO Launcher stays installed: disable the module and it comes back exactly as it was.

> No Xiaomi files are included. You build the module from an official Xiaomi ROM yourself. Not affiliated
> with Xiaomi.

## Instructions

**You need:** a rooted POCO phone on HyperOS 3 / Android 16 (KernelSU tested, Magisk untested), and a PC
with Git, Python 3.10+ and adb. On macOS and Linux, use `python3` in the commands below.

### Build

1. Get this repository:
   ```bash
   git clone https://github.com/haoyu-UT/HyperOS-Launcher-for-POCO.git
   cd HyperOS-Launcher-for-POCO
   ```
   Without Git, use **Code → Download ZIP** on GitHub and unpack it.
2. Download the official Xiaomi **China fastboot ROM** for your device codename, on the same HyperOS and
   Android version as your phone, and unpack it. ROMs re-signed by third parties, such as xiaomi.eu, are
   refused.
3. Build the module:
   ```bash
   python tools/build.py --rom path/to/rom/images
   ```

### Install

1. Disable any other module that replaces the launcher.
2. Copy the zip to the phone:
   ```bash
   adb push out/poco-hyperos-launcher-*.zip /sdcard/Download/
   ```
3. Flash it in the KernelSU manager and reboot.
4. Open the Theme Store and apply **Default** to load the China theme. Skip this if you built with
   `--no-theme`.

### While using it

- Don't set POCO Launcher as the default home app; it will crash. If it happens, reboot and the module
  switches back.

### Undo

- Disable the module in the KernelSU manager and reboot. POCO Launcher returns with its layout intact.
- Stuck on a black screen? Run:
  ```bash
  adb shell su -c "touch /data/adb/modules/poco_hyperos_launcher/disable"
  adb reboot
  ```

## Troubleshooting and bug reports

After installing and rebooting, these should hold:

| Command | Expected output |
| --- | --- |
| `adb shell cmd role get-role-holders android.app.role.HOME` | `com.miui.home` |
| `adb shell cmd overlay lookup android android:string/config_recentsComponentName` | `com.miui.home/com.miui.home.recents.RecentsActivity` |
| `adb logcat -b crash -d` | nothing |

When reporting a problem, include the module's build details and the crash log:

```bash
adb shell su -c "cat /data/adb/modules/poco_hyperos_launcher/module.prop"
adb logcat -b crash -d
```

`module.prop` records the launcher version and signer, plus the ROM build, region, device and Android
version it came from. `python tools/device_check.py` writes a fuller device report to
`out/device-info.txt`; remove anything personal before sharing it.

## Build options

| Option | Effect |
| --- | --- |
| `--rom DIR` | Take the launcher, theme folder and build details from a fastboot ROM's `images/` folder. |
| `--no-theme` | Don't include the ROM's theme folder; keep the phone's own. |
| `--apk FILE` | Use this launcher APK instead of `--rom`. Default: `apk/MiuiHome.apk`. |
| `--theme DIR` | Use this theme folder. Default without `--rom`: `theme/cn`, if it exists. |
| `--rebuild-overlay` | Build the fallback Recents overlay instead of using the pre-built one. Needs the Android SDK; see Advanced. |
| `--allow-any-signer` | Accept a launcher not signed by Xiaomi. Theming will likely break. |

The zip is written to `out/`. In Git Bash on Windows, run `export MSYS_NO_PATHCONV=1` first if you pass
paths starting with `/`.

## How it works

See [docs/how-it-works.md](docs/how-it-works.md): what the module changes, why each piece is needed, and
the Xiaomi-specific behaviour it works around.

## Advanced

<details>
<summary>Extracting files from a ROM manually</summary>

`tools/romfs.py` reads files straight out of a ROM's `super.img` (sparse, or split into `super.img.0`,
`.1`, …), without unpacking it:

```bash
python tools/romfs.py path/to/rom/images partitions
python tools/romfs.py path/to/rom/images get product /priv-app/MiuiHome/MiuiHome.apk apk/MiuiHome.apk
python tools/romfs.py path/to/rom/images get product /media/theme theme/cn
python tools/apkinfo.py apk/MiuiHome.apk
```

`build.py` without `--rom` then uses `apk/MiuiHome.apk` and `theme/cn`. In the `apkinfo.py` output,
`signer sha256` must start with `c9009d01` (Xiaomi's key).

</details>

<details>
<summary>Rebuilding the fallback overlay</summary>

POCO ROMs normally move Recents to the HyperOS launcher with Xiaomi's own overlay, and the installer then
drops the module's fallback overlay. The fallback ships pre-built in `overlay/prebuilt/`, pointing Recents
at `com.miui.home/com.miui.home.recents.RecentsActivity`. If a launcher uses a different Recents
activity, `build.py` builds a matching overlay instead, which needs:

- Android SDK **Build-Tools** (`aapt2`, `zipalign`, `apksigner`) and an SDK **Platform** (`android.jar`);
  version 36 is tested;
- a **Java runtime** 17 or newer, e.g. [Eclipse Temurin](https://adoptium.net/).

Install them with `sdkmanager "build-tools;36.1.0" "platforms;android-36"`, or unpack the official zips
into `toolchain/`, where `build.py` looks first:

```
toolchain/
├── sdk/build-tools/36.1.0/   ← build-tools_r36.1_<os>.zip    (its top folder is named "android-16")
├── sdk/platforms/android-36/ ← platform-36_r02.zip
└── jre/                      ← a Java runtime, with bin/java and bin/keytool
```

Installing the Android SDK packages means accepting the
[Android Software Development Kit License Agreement](https://developer.android.com/studio/terms).
`build.py` also checks `ANDROID_HOME`, `ANDROID_SDK_ROOT`, `%LOCALAPPDATA%\Android\Sdk`, `JAVA_HOME` and
`PATH`. Overlays you build are signed with your own `keys/overlay.jks`, created on first use.
`--update-prebuilt-overlay` also replaces the files in `overlay/prebuilt/` (for maintainers).

</details>

## Disclaimer and license

This repository contains no Xiaomi software. The launcher and theme files you extract from a ROM
remain the property of Xiaomi or its licensors; don't redistribute them, or modules built from them. This
project is not affiliated with or endorsed by Xiaomi. POCO, HyperOS and MIUI are trademarks of Xiaomi.
Modifying your system carries risk; use at your own risk.

The code is released under the [MIT License](LICENSE).
