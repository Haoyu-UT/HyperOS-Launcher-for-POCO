[English](how-it-works.md) | **简体中文**

# 工作原理

本文说明模块改了什么、每一部分为什么必要，以及它绕开了哪些 HyperOS 行为。以下内容均在一台运行国际版
HyperOS 3（Android 16）的 POCO F8 Ultra 上发现，其他 POCO 机型可能有所不同。

## 模块安装的内容

| 模块内路径 | 作用 |
| --- | --- |
| `system/product/priv-app/HyperOSLauncher/HyperOSLauncher.apk` | 桌面本体，作为特权系统应用安装。如果 APK 中的原生库是压缩存储的，会解压到 `lib/arm64/`。 |
| `system/product/etc/permissions/privapp-permissions-miuihome.xml` | 桌面所申请特权权限的白名单，根据其清单文件生成。 |
| `system/product/overlay/MiuiHomeRecentsOverlay/MiuiHomeRecentsOverlay.apk` | 备用的静态框架 overlay（优先级 9999），把 `config_recentsComponentName` 设为桌面的 `RecentsActivity`。如果 ROM 自带小米的 `MiuiHomeLauncherResOverlay`，安装程序会去掉它。 |
| `system.prop` | `ro.miui.product.home=com.miui.home`。同时会把 ROM 的最近任务设置切换到 HyperOS 桌面。 |
| `service.sh` | 每次开机时尽早把 `com.miui.home` 设为默认桌面，然后安装守护脚本并停用 POCO 桌面。 |
| `launcher-guard.sh` | 复制到 `/data/adb/service.d/poco_hyperos_launcher_guard.sh`。模块被停用或移除时，重新启用 POCO 桌面。 |
| `uninstall.sh` | 移除模块时确保守护脚本存在。 |
| `customize.sh` | 安装时检查：必需文件是否齐全、是否有其他已启用的模块也在替换桌面，以及手机与桌面来源 ROM 的设备和 Android 版本是否一致。在 KernelSU 下创建主题 whiteout。 |
| `system/product/media/theme/` + `.replace` | 可选：某个 ROM 的完整主题文件夹，例如国行默认主题。 |
| `module.prop` 中的构建信息 | `launcherVersionName`、`launcherVersionCode`、`launcherCert`、`romBuild`、`romRegion`、`romDevice`、`romAndroid`、`romSdk`、`themeSource`、`builtAt`。KernelSU 和 Magisk 会忽略这些键；它们用于问题反馈和安装时的检查。 |

模块不会写入系统分区。这些文件由 KernelSU 或 Magisk 在开机时挂载，停用或移除模块即可全部撤销。
模块之外唯一的改动是停用 POCO 桌面，下次开机时由守护脚本撤销，见下文。

## 每一部分为什么必要

### 换一个文件夹名：用 `HyperOSLauncher`，而不是 `MiuiHome`

小米的包管理器扩展（`miui-services.jar` 中的 `PackageManagerServiceImpl.initIgnoreApps`）包含这样一条规则：

> 如果 `/product/priv-app/MiuiHome` 和 `/product/priv-app/MiLauncherGlobal` 同时存在：品牌为 `POCO` 时忽略
> `MiuiHome`，否则忽略 `MiLauncherGlobal`。

这条规则按这两个文件夹名精确匹配。在 POCO 手机上，安装在 `/product/priv-app/MiuiHome` 的桌面永远不会被扫描，日志中会出现：

```
PKMSImpl: Skip scanning package: com.miui.home, path=/product/priv-app/MiuiHome, reason: boot scanning
```

换成任何其他文件夹名都能避开这条规则，POCO 桌面也可以原地保留。

### 最近任务 overlay，以及为什么默认桌面和最近任务必须是同一个应用

全面屏手势需要一个 input consumer，通过 `IWindowManager.createInputConsumer` 创建。Android 只允许
`config_recentsComponentName` 指定的应用（或持有 `INPUT_CONSUMER` 权限的应用）创建它。POCO 桌面和 HyperOS
桌面一旦作为默认桌面启动，都会立即创建一个。

- 如果最近任务指向 `com.miui.home`，而默认桌面仍是 POCO 桌面，POCO 桌面每次启动都会崩溃：
  `SecurityException: createInputConsumer requires INPUT_CONSUMER permission`，屏幕会黑屏。
- 反过来，如果最近任务没有指向它，HyperOS 桌面也会以同样的方式崩溃。

所以最近任务必须始终指向 `com.miui.home`，并由 `service.sh` 确保默认桌面与之一致。

POCO 的 ROM 其实已经内置了这个切换开关。它带有两个静态框架 overlay，每个只在 `ro.miui.product.home`
取特定值时才启用（由清单中的 `requiredSystemPropertyName` / `requiredSystemPropertyValue` 控制）：

| 小米 overlay | 启用条件：`ro.miui.product.home` = | 最近任务指向 |
| --- | --- | --- |
| `MiuiPocoLauncherResOverlay.apk`（`android.miui.poco.launcher.res`） | `com.mi.android.globallauncher`（原厂） | `com.mi.android.globallauncher/com.miui.home.recents.RecentsActivity` |
| `MiuiHomeLauncherResOverlay.apk`（`android.miui.home.launcher.res`） | `com.miui.home` | `com.miui.home/com.miui.home.recents.RecentsActivity` |

因此在这类 ROM 上，只要通过 `system.prop` 设置该属性就够了：小米自己的 overlay 会把最近任务切换到 HyperOS
桌面。模块仍自带一个 overlay，作为缺少 `MiuiHomeLauncherResOverlay.apk` 的 ROM 的**备用**方案。
`customize.sh` 在安装时检查小米的 overlay 是否存在，存在就去掉备用方案，两者不会同时存在。使用备用 overlay
时，它 9999 的优先级高于其他所有桌面 overlay。

备用 overlay 只包含一个清单文件和那一条字符串，不含任何小米代码，所以以预构建形式放在 `overlay/prebuilt/`
中，并附带记录其目标组件和 SHA-256 的 `overlay.json`。如果桌面的 `RecentsActivity` 与之匹配，`build.py`
就直接使用它；否则才会自行构建，这需要 Android SDK 和 Java。系统分区上的 overlay 可以用任意密钥签名。

### 尽早设置默认桌面

`service.sh` 不等待 `sys.boot_completed`。它会反复尝试，直到角色服务（role service）可用且 `com.miui.home`
已安装，然后设置 `android.app.role.HOME` 的持有者。角色服务早于系统第一次启动桌面，因此在测试手机上，开机后
第一次启动的桌面就已经是 HyperOS 桌面。它每次开机都会运行，因为只要最近任务指向 `com.miui.home`，POCO
桌面本来就无法作为默认桌面工作。

### 停用 POCO 桌面

模块启用期间 POCO 桌面无法工作，但其他应用仍会拉起它的进程，例如查询它的 content provider。之后它在后台被冻结，
占用内存，并在小米解冻它时才处理设置变化。两个桌面都会监听 `secure navigation_mode`，并据此设置
`global force_fsg_nav_bar`。被冻结的 POCO 桌面可能在许多秒之后才执行这次写入，在别的程序重新打开手势之后又把它关掉。

因此，一旦 `com.miui.home` 成为默认桌面，`service.sh` 就会运行 `pm disable-user --user 0
com.mi.android.globallauncher`。停用会保留应用数据，布局因此得以保存。用模块隐藏它的 APK 则不行：系统应用的
APK 消失后，Android 会连同数据一起删除它。

停用或移除模块时，模块自己的脚本都不会运行，所以模块无法自行重新启用 POCO 桌面。为此，`service.sh` 会先把一个
守护脚本安装到 `/data/adb/service.d/`，KernelSU 和 Magisk 每次开机都会运行它。模块启用时，守护脚本什么也不做；
模块被停用或已移除时，它运行 `pm enable`，在默认桌面的持有者已不存在时把默认桌面交还给 POCO 桌面，并在模块移除
后删除自己。

### 特权权限白名单

HyperOS 强制执行特权权限白名单（`ro.control_privapp_permissions=enforce`）。如果一个特权应用申请了白名单中
没有的特权权限，手机将无法开机。`build.py` 会把桌面申请的所有权限（它自己定义的除外）都加入白名单，多余的
非特权条目会被 Android 忽略。在测试的版本上，这覆盖了小米官方 ROM 授予该桌面的全部 28 项权限。

### `ro.miui.product.home`

HyperOS 的应用通过这个属性确定系统桌面。主题商店的代码引用了它，而 POCO 的 ROM 把它设为
`com.mi.android.globallauncher`。它还决定启用小米的哪一个桌面 overlay，见上文。

### 小米签名

主题商店不是特权应用。它通过 `com.android.launcher.permission.READ_SETTINGS` 等权限访问桌面，这些权限的保护
级别是 `signature|privileged`，所以只有签名与桌面一致时它才能获得。POCO 桌面、主题商店和官方的
`com.miui.home` 都使用小米的 MIUI 密钥签名（证书 SHA-256 为 `c9009d01…9025`）。用其他密钥重新签名的桌面
（例如 xiaomi.eu 的做法）可以正常开机，但会失去这种访问权限。`build.py` 会检查证书。

该 APK 的 `sharedUserId` 是一个资源引用，解析结果为空字符串，因此桌面没有共享 UID，会作为普通系统应用安装。

### 国行桌面用于国际版 ROM

国行的 `com.miui.home` 和 POCO 桌面出自同一套代码。两者都同时包含国行功能（国行负一屏、国行搜索、小爱同学）
和国际版功能（Google Discover 信息流、国际版负一屏），并在运行时通过 `IS_INTERNATIONAL_BUILD` 选择。
在国际版 ROM 上，国行桌面会走国际版的逻辑。

### 主题与 POCO 桌面

两款桌面的主题引擎相同（图标、MAML 小部件、小工具）。但桌面界面的样式是按包名应用的：`/data/system/theme/`
中当前主题的 `com.miui.home` 组件，只作用于包名完全一致的应用。POCO 桌面（`com.mi.android.globallauncher`）
永远用不上它，这就是主题在 POCO 桌面上显得不完整的原因。

### 主题文件夹

国行和国际版 ROM 都在 `/product/media/theme` 中内置唯一一个主题 `default`。国行版是同一位置的更完整版本
（在测试手机上为 106.5 MiB 对 31.5 MiB）：国行图标、动态图标、更新的 MAML 时钟和天气小部件，以及不同的锁屏壁纸。
它替换国际版的文件夹，而不是额外增加一个主题。这些文件带有与原厂文件相同的 `system_file` SELinux 标签。

国行文件夹里还有一个可选的字体包 `MILanProVF.mtz`（小米兰亭 Pro）。默认主题并未引用它，国际版主题商店也不会列出它
（无论放在这个文件夹还是 `/sdcard/MIUI/theme/` 中），因此 `build.py` 不会打包它。

Magisk 会遵循 `.replace` 标记，整体替换该文件夹。而部分 KernelSU 元模块（例如 `meta-overlayfsx`）会忽略它，
把两个文件夹合并，在测试手机上因此残留了 19 个国际版文件。所以在 KernelSU 下，安装程序会为模块未包含的每个
原厂文件创建一个 overlayfs whiteout（一个 `0:0` 字符设备）。它在安装时列出 `/product/media/theme` 来找出这些
文件：如果已挂载旧版本模块，看到的是原厂文件加模块文件，因此新模块中没有的文件一定是原厂文件。它还会沿用已安装
版本中的 whiteout。

## 工具

所有工具都只使用 Python 标准库。

| 工具 | 作用 |
| --- | --- |
| `tools/romfs.py` | 直接从线刷包中读取文件：Android 稀疏镜像（单个或拆分）、动态分区表（liblp），以及 EROFS（含 compact LZ4 压缩）。不支持 big pcluster、tail packing、fragments 和 LZMA，遇到时会明确报错。 |
| `tools/apkinfo.py` | 解析 APK 的二进制清单和 `resources.arsc`，从 APK 签名块中读取签名证书，并生成特权权限白名单。 |
| `tools/build.py` | 使用 `--rom` 时，从 ROM 中提取桌面、主题文件夹和构建信息（来自 `mi_ext`、`product` 和 `system` 的 `build.prop`）。检查 APK（包名、签名、桌面和最近任务 Activity），选用预构建 overlay 或自行构建，组装模块并输出 zip。 |
| `tools/device_check.py` | 通过 adb 收集设备信息，写入 `out/device-info.txt`。 |
