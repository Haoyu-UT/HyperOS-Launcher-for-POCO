"""Collect what the module depends on from the phone over adb, into out/device-info.txt.

Usage: python tools/device_check.py
The Magisk line needs root granted to "Shell" in the Magisk app; everything else runs without it.
"""
import subprocess
from pathlib import Path

CHECKS = [
    ("device", "getprop ro.product.device"),
    ("HyperOS build", "getprop ro.mi.os.version.incremental; getprop ro.build.version.incremental"),
    ("SDK", "getprop ro.build.version.sdk"),
    ("privapp enforcement", "getprop ro.control_privapp_permissions"),
    ("home role holder", "cmd role get-role-holders android.app.role.HOME"),
    ("POCO Launcher", "dumpsys package com.mi.android.globallauncher | grep -E 'codePath|versionName|sharedUser'"),
    ("com.miui.home already present", "pm path com.miui.home || echo no"),
    ("Recents component", "cmd overlay lookup android android:string/config_recentsComponentName"),
    ("framework overlays", "cmd overlay list android"),
    ("Theme Store", "dumpsys package com.android.thememanager | grep -E 'codePath|versionName'"),
    ("launcher dirs", "ls -d /product/priv-app/* /product/app/* /system_ext/priv-app/* 2>/dev/null | grep -i -E 'launch|home|poco'"),
    ("shared libraries", "pm list libraries"),
    ("Magisk", "su -c 'magisk -v; magisk -V'"),
]


def main():
    out = Path(__file__).resolve().parent.parent / "out" / "device-info.txt"
    out.parent.mkdir(exist_ok=True)
    sections = []
    for title, cmd in CHECKS:
        r = subprocess.run(["adb", "shell", cmd], capture_output=True, text=True, encoding="utf-8", errors="replace")
        sections.append(f"## {title}\n$ {cmd}\n{(r.stdout + r.stderr).strip()}\n")
    out.write_text("\n".join(sections), encoding="utf-8")
    print("\n".join(sections))
    print(f"Saved to {out}")


if __name__ == "__main__":
    main()
