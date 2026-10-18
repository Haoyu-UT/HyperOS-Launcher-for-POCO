**English** | [简体中文](how-it-works.zh-CN.md)

# How it works

This page explains what the module changes, why each piece is there, and the HyperOS behaviour it works
around. Everything was found on a POCO F8 Ultra running global HyperOS 3 (Android 16). Other POCO
models may differ.

## What the module installs

| Path in the module | Purpose |
| --- | --- |
| `system/product/priv-app/HyperOSLauncher/HyperOSLauncher.apk` | The launcher, as a privileged system app. Native libraries are unpacked to `lib/arm64/` if the APK stores them compressed. |
| `system/product/etc/permissions/privapp-permissions-miuihome.xml` | Whitelist of the privileged permissions the launcher requests, generated from its manifest. |
| `system/product/overlay/MiuiHomeRecentsOverlay/MiuiHomeRecentsOverlay.apk` | Fallback static framework overlay (priority 9999) that sets `config_recentsComponentName` to the launcher's `RecentsActivity`. The installer removes it when the ROM has Xiaomi's own `MiuiHomeLauncherResOverlay`. |
| `system.prop` | `ro.miui.product.home=com.miui.home`. Also switches the ROM's Recents setting over to the HyperOS launcher. |
| `service.sh` | Makes `com.miui.home` the home app on every boot, as early as possible, then installs the guard and disables POCO Launcher. |
| `launcher-guard.sh` | Copied to `/data/adb/service.d/poco_hyperos_launcher_guard.sh`. Enables POCO Launcher again whenever the module is disabled or removed. |
| `uninstall.sh` | Makes sure the guard is in place when the module is removed. |
| `customize.sh` | Install-time checks: required files, other enabled modules that replace the launcher, and whether the phone matches the device and Android version the launcher came from. Creates theme whiteouts under KernelSU. |
| `system/product/media/theme/` + `.replace` | Optional: a ROM's whole theme folder, such as the China default theme. |
| `module.prop` build details | `launcherVersionName`, `launcherVersionCode`, `launcherCert`, `romBuild`, `romRegion`, `romDevice`, `romAndroid`, `romSdk`, `themeSource`, `builtAt`. KernelSU and Magisk ignore these keys; they're there for bug reports and the installer's checks. |

Nothing is written to the system partitions. KernelSU or Magisk mounts these files at boot, and
disabling or removing the module undoes all of it. The one change outside the module, POCO Launcher
being disabled, is undone by the guard on the next boot; see below.

## Why each piece is needed

### A different folder name: `HyperOSLauncher`, not `MiuiHome`

Xiaomi's package manager extension, `PackageManagerServiceImpl.initIgnoreApps` in `miui-services.jar`,
contains this rule:

> If both `/product/priv-app/MiuiHome` and `/product/priv-app/MiLauncherGlobal` exist, ignore
> `MiuiHome` when the brand is `POCO`, otherwise ignore `MiLauncherGlobal`.

The rule matches on these exact folder names. A launcher installed at `/product/priv-app/MiuiHome` on a
POCO phone is never scanned, and the log shows:

```
PKMSImpl: Skip scanning package: com.miui.home, path=/product/priv-app/MiuiHome, reason: boot scanning
```

Any other folder name avoids the rule, and POCO Launcher can stay where it is.

### The Recents overlay, and why home and Recents must be the same app

Gesture navigation needs an input consumer, created through `IWindowManager.createInputConsumer`. Android
only allows the app named in `config_recentsComponentName` (or one holding `INPUT_CONSUMER`) to create
it. Both POCO Launcher and the HyperOS launcher create one as soon as they start as home.

- If the overlay points Recents at `com.miui.home` but POCO Launcher is still home, POCO Launcher crashes
  on every start: `SecurityException: createInputConsumer requires INPUT_CONSUMER permission`. You get a
  black screen.
- Without the overlay, the HyperOS launcher crashes the same way.

So Recents must always point at `com.miui.home`, and `service.sh` makes sure the home app matches it.

POCO ROMs already contain the switch for this. They ship two static framework overlays, each enabled only
when `ro.miui.product.home` has a particular value (`requiredSystemPropertyName` /
`requiredSystemPropertyValue` in their manifests):

| Xiaomi overlay | Enabled when `ro.miui.product.home` = | Sets Recents to |
| --- | --- | --- |
| `MiuiPocoLauncherResOverlay.apk` (`android.miui.poco.launcher.res`) | `com.mi.android.globallauncher` (stock) | `com.mi.android.globallauncher/com.miui.home.recents.RecentsActivity` |
| `MiuiHomeLauncherResOverlay.apk` (`android.miui.home.launcher.res`) | `com.miui.home` | `com.miui.home/com.miui.home.recents.RecentsActivity` |

Setting the property in `system.prop` is therefore enough on such a ROM: Xiaomi's own overlay moves
Recents to the HyperOS launcher. The module still carries its own overlay as a **fallback** for ROMs that
lack `MiuiHomeLauncherResOverlay.apk`. `customize.sh` checks for it at install time and removes the
fallback if it's there, so the two never coexist. When the fallback is used, its priority of 9999 puts it
above any other launcher overlay.

The fallback overlay contains only a manifest and that one string, none of Xiaomi's code, so it ships pre-built in
`overlay/prebuilt/` with an `overlay.json` recording its component and SHA-256. `build.py` uses it when
the launcher's `RecentsActivity` matches, and only otherwise builds one, which needs the Android SDK and
Java. Any signing key works for an overlay on a system partition.

### Setting the home app early

`service.sh` doesn't wait for `sys.boot_completed`. It retries until the role service answers and
`com.miui.home` is installed, then sets the `android.app.role.HOME` holder. The role service starts
before the system first launches a home screen, so on the tested phone the first home launch after boot
was already the HyperOS launcher. It runs on every boot, because POCO Launcher can't work as home while
Recents points at `com.miui.home` anyway.

### Disabling POCO Launcher

POCO Launcher can't work while the module is active, but other apps still start its process, for
example by querying its content providers. It then sits frozen in the background, using memory, and
handles settings changes whenever Xiaomi thaws it. Both launchers watch `secure navigation_mode` and set
`global force_fsg_nav_bar` to match it. A frozen POCO Launcher can make that write many seconds later
and turn gestures off after something else has turned them back on.

So once `com.miui.home` holds the home role, `service.sh` runs `pm disable-user --user 0
com.mi.android.globallauncher`. Disabling keeps the app's data, so its layout survives. Hiding its APK
from the module would not: Android drops a system app whose APK disappears, along with its data.

Disabling or removing a module runs none of its scripts, so the module can't re-enable POCO Launcher
itself. Instead, `service.sh` first installs a guard to `/data/adb/service.d/`, which KernelSU and
Magisk run on every boot. While the module is active, the guard does nothing. When the module is
disabled or gone, it runs `pm enable`, gives the home role back to POCO Launcher if its holder no
longer exists, and deletes itself once the module is gone.

### The privileged permission whitelist

HyperOS enforces privileged permission whitelisting (`ro.control_privapp_permissions=enforce`). A
privileged app that requests a privileged permission missing from the whitelist stops the phone from
booting. `build.py` whitelists every permission the launcher requests, except the ones it defines
itself. Extra non-privileged entries are ignored by Android. On the tested build this covers all 28
permissions Xiaomi's own ROM grants the launcher.

### `ro.miui.product.home`

HyperOS apps read this property to find the system launcher. The Theme Store's code references it, and
POCO ROMs set it to `com.mi.android.globallauncher`. It also selects which of Xiaomi's launcher overlays
is enabled; see above.

### Xiaomi's signature

The Theme Store isn't a privileged app. It reaches the launcher through permissions such as
`com.android.launcher.permission.READ_SETTINGS`, which are `signature|privileged`, so it only gets them
when its signer matches the launcher's. POCO Launcher, the Theme Store and the official `com.miui.home`
are all signed with Xiaomi's MIUI key (certificate SHA-256 `c9009d01…9025`). A launcher re-signed with
another key, as xiaomi.eu does, would boot fine but lose this access. `build.py` checks the certificate.

The APK's `sharedUserId` is a resource reference that resolves to an empty string, so the launcher has
no shared UID and installs as a normal system app.

### China launcher on a global ROM

The China `com.miui.home` and POCO Launcher come from the same code base. Both contain the China
features (China App Vault, China search, Xiao AI) and the global ones (Google Discover feed, global App
Vault), and choose between them at runtime with `IS_INTERNATIONAL_BUILD`. On a global ROM the China
launcher takes the global paths.

### Themes and POCO Launcher

Both launchers have the same theme engine (icons, MAML widgets, gadgets). Launcher UI styling, however,
is applied per package: the active theme in `/data/system/theme/` has a `com.miui.home` component, which
only applies to the app with that exact package name. POCO Launcher (`com.mi.android.globallauncher`)
never picks it up, which is why themes looked incomplete there.

### The theme folder

Both China and global ROMs ship a single built-in theme, `default`, in `/product/media/theme`. The China
copy is a fuller version of the same slot (106.5 MiB vs. 31.5 MiB on the tested phone): China icons,
dynamic icons, newer MAML clock and weather widgets, and a different lock-screen wallpaper. It replaces
the global folder rather than adding a theme.
Files carry the same `system_file` SELinux label as the stock ones.

The China folder also holds an optional font package, `MILanProVF.mtz` (小米兰亭 Pro). Nothing in the
Default theme refers to it, and the global Theme Store doesn't list it, neither from this folder nor
from `/sdcard/MIUI/theme/`, so `build.py` leaves it out.

Magisk honours the `.replace` marker and swaps the whole folder. Some KernelSU metamodules, such as
`meta-overlayfsx`, ignore it and merge the folders, which left 19 global files visible on the tested phone.
Under KernelSU the installer therefore creates an overlayfs whiteout (a `0:0` character device) in the
module for every stock file it doesn't ship. It finds them by listing `/product/media/theme` at install
time. If an older version of the module is mounted, that view is stock plus module files, so anything the
new module doesn't ship is still a stock file. It also carries over whiteouts from the installed version.

## The tools

All tools use only the Python standard library.

| Tool | What it does |
| --- | --- |
| `tools/romfs.py` | Reads files straight out of a fastboot ROM: Android sparse images (one or split), the dynamic-partition table (liblp), and EROFS including compact LZ4 compression. It doesn't support big pclusters, tail packing, fragments or LZMA, and fails loudly on them. |
| `tools/apkinfo.py` | Parses an APK's binary manifest and `resources.arsc`, reads the signer certificate from the APK Signing Block, and generates the privapp whitelist. |
| `tools/build.py` | With `--rom`, takes the launcher, theme folder and build details (from `mi_ext`, `product` and `system` `build.prop`) out of the ROM. Checks the APK (package, signer, HOME and Recents activities), picks the pre-built overlay or builds one, stages the module and writes the zip. |
| `tools/device_check.py` | Collects device facts over adb into `out/device-info.txt`. |
