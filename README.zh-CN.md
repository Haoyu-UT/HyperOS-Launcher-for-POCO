[English](README.md) | **简体中文**

# HyperOS Launcher for POCO

一个 KernelSU / Magisk 模块：在已 root 的 POCO 手机上，用原版 **HyperOS 桌面**（`com.miui.home`）替换
POCO 桌面，让主题商店的主题完整生效。全面屏手势和最近任务照常可用。模块启用期间 POCO 桌面会被隐藏：停用或移除模块后，它会回来，但桌面布局会重置。

> 本项目不包含任何小米文件。模块需要你用小米官方 ROM 自行构建。本项目与小米无关。

## 使用说明

**你需要：**一部运行 HyperOS 3 / Android 16 并已 root 的 POCO 手机（KernelSU 已测试，Magisk 未测试），以及一台装有
Git、Python 3.10+ 和 adb 的电脑。macOS 和 Linux 上，请把下面命令里的 `python` 换成 `python3`。

### 构建

1. 获取本仓库：
   ```bash
   git clone https://github.com/haoyu-UT/HyperOS-Launcher-for-POCO.git
   cd HyperOS-Launcher-for-POCO
   ```
   没有 Git 的话，在 GitHub 页面点击 **Code → Download ZIP** 下载并解压。
2. 下载与你的设备代号对应、HyperOS 和 Android 版本与手机一致的小米官方**国行线刷包**，并解压。经第三方重新签名的
   ROM（例如 xiaomi.eu）会被拒绝。
3. 构建模块：
   ```bash
   python tools/build.py --rom path/to/rom/images
   ```

### 安装

1. 停用其他替换桌面的模块。
2. 把生成的 zip 复制到手机：
   ```bash
   adb push out/poco-hyperos-launcher-*.zip /sdcard/Download/
   ```
3. 在 KernelSU 管理器中刷入，然后重启。
4. 打开主题商店，应用**默认**主题，以加载国行主题。如果构建时用了 `--no-theme`，跳过这一步。

### 日常使用

- 不要把 POCO 桌面设为默认桌面应用，否则它会崩溃。万一设了，重启即可，模块会自动切换回来。

### 恢复

- 在 KernelSU 管理器中停用或移除模块并重启。POCO 桌面会回来，但桌面布局会重新开始：隐藏期间 Android 会删除它的数据。
- 卡在黑屏？运行：
  ```bash
  adb shell su -c "touch /data/adb/modules/poco_hyperos_launcher/disable"
  adb reboot
  ```

## 故障排查与问题反馈

安装并重启后，以下结果应当成立：

| 命令 | 预期输出 |
| --- | --- |
| `adb shell cmd role get-role-holders android.app.role.HOME` | `com.miui.home` |
| `adb shell cmd overlay lookup android android:string/config_recentsComponentName` | `com.miui.home/com.miui.home.recents.RecentsActivity` |
| `adb logcat -b crash -d` | 无输出 |

反馈问题时，请附上模块的构建信息和崩溃日志：

```bash
adb shell su -c "cat /data/adb/modules/poco_hyperos_launcher/module.prop"
adb logcat -b crash -d
```

`module.prop` 记录了桌面的版本和签名，以及它来自的 ROM 的版本号、地区、设备和 Android 版本。
`python tools/device_check.py` 会把更完整的设备报告写入 `out/device-info.txt`；分享前请删去个人信息。

## 构建选项

| 选项 | 作用 |
| --- | --- |
| `--rom DIR` | 从线刷包的 `images/` 文件夹中提取桌面、主题文件夹和构建信息。 |
| `--no-theme` | 不打包 ROM 的主题文件夹，保留手机自带的。 |
| `--apk FILE` | 使用指定的桌面 APK，代替 `--rom`。默认：`apk/MiuiHome.apk`。 |
| `--theme DIR` | 使用指定的主题文件夹。不带 `--rom` 时默认为 `theme/cn`（如果存在）。 |
| `--rebuild-overlay` | 不用预构建的版本，自行构建备用的最近任务 overlay。需要 Android SDK，见“进阶”。 |
| `--allow-any-signer` | 接受非小米签名的桌面。主题很可能无法正常生效。 |

zip 会写入 `out/`。在 Windows 的 Git Bash 中，如果参数里的路径以 `/` 开头，请先运行
`export MSYS_NO_PATHCONV=1`。

## 工作原理

见 [docs/how-it-works.zh-CN.md](docs/how-it-works.zh-CN.md)：模块改了什么、每一部分为什么必要，以及它绕开了哪些小米特有的行为。

## 进阶

<details>
<summary>手动从 ROM 中提取文件</summary>

`tools/romfs.py` 可以直接从 ROM 的 `super.img`（稀疏镜像，或拆分为 `super.img.0`、`.1`……）中读取文件，无需解包：

```bash
python tools/romfs.py path/to/rom/images partitions
python tools/romfs.py path/to/rom/images get product /priv-app/MiuiHome/MiuiHome.apk apk/MiuiHome.apk
python tools/romfs.py path/to/rom/images get product /media/theme theme/cn
python tools/apkinfo.py apk/MiuiHome.apk
```

之后不带 `--rom` 运行 `build.py`，就会使用 `apk/MiuiHome.apk` 和 `theme/cn`。`apkinfo.py` 输出中的
`signer sha256` 必须以 `c9009d01` 开头（小米的签名）。

</details>

<details>
<summary>重新构建备用 overlay</summary>

POCO 的 ROM 通常会用小米自己的 overlay 把最近任务切换到 HyperOS 桌面，此时安装程序会去掉模块自带的备用 overlay。
备用 overlay 已预构建在 `overlay/prebuilt/` 中，把最近任务指向 `com.miui.home/com.miui.home.recents.RecentsActivity`。
如果某个桌面使用不同的最近任务 Activity，`build.py` 会改为构建一个匹配的 overlay，这需要：

- Android SDK **Build-Tools**（`aapt2`、`zipalign`、`apksigner`）和一个 SDK **Platform**（`android.jar`），已测试版本 36；
- **Java 运行时** 17 或更高版本，例如 [Eclipse Temurin](https://adoptium.net/)。

可以用 `sdkmanager "build-tools;36.1.0" "platforms;android-36"` 安装，或者把官方 zip 解压到 `toolchain/`，
`build.py` 会优先在这里查找：

```
toolchain/
├── sdk/build-tools/36.1.0/   ← build-tools_r36.1_<os>.zip    （其顶层文件夹名为 "android-16"）
├── sdk/platforms/android-36/ ← platform-36_r02.zip
└── jre/                      ← Java 运行时，包含 bin/java 和 bin/keytool
```

安装 Android SDK 组件即表示接受
[Android 软件开发套件许可协议](https://developer.android.com/studio/terms)。`build.py` 还会检查
`ANDROID_HOME`、`ANDROID_SDK_ROOT`、`%LOCALAPPDATA%\Android\Sdk`、`JAVA_HOME` 和 `PATH`。你构建的 overlay
会用你自己的 `keys/overlay.jks` 签名，该文件在首次使用时创建。`--update-prebuilt-overlay` 还会替换
`overlay/prebuilt/` 中的文件（供维护者使用）。

</details>

## 免责声明与许可证

本仓库不包含任何小米软件。你从 ROM 中提取的桌面和主题文件仍归小米或其授权方所有；请勿再分发这些文件，也不要分发用它们构建的模块。
本项目与小米无关，也未获得小米认可。POCO、HyperOS 和 MIUI 是小米的商标。修改系统存在风险，后果自负。

代码以 [MIT 许可证](LICENSE)发布（许可证原文为英文，以英文版本为准）。
