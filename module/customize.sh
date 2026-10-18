#!/system/bin/sh
# Runs inside the KernelSU / Magisk installer; MODPATH, API, KSU, ui_print and abort come from it.

APP_APK=$MODPATH/system/product/priv-app/HyperOSLauncher/HyperOSLauncher.apk
PERM_XML=$MODPATH/system/product/etc/permissions/privapp-permissions-miuihome.xml
OVERLAY_APK=$MODPATH/system/product/overlay/MiuiHomeRecentsOverlay/MiuiHomeRecentsOverlay.apk
XIAOMI_OVERLAY=/product/overlay/MiuiHomeLauncherResOverlay.apk
THEME=$MODPATH/system/product/media/theme
MY_ID=poco_hyperos_launcher

prop() { sed -n "s/^$1=//p" "$MODPATH/module.prop" | head -n 1; }

device=$(getprop ro.product.device)
ui_print "- Device: $device, API $API"
ui_print "- Launcher: $(prop launcherVersionName)"

# Compare the phone with the ROM the launcher came from (recorded by build.py --rom).
rom_device=$(prop romDevice)
rom_sdk=$(prop romSdk)
if [ -n "$rom_sdk" ] && [ "$rom_sdk" != "$API" ]; then
  ui_print "! The launcher comes from an Android API $rom_sdk ROM, but this phone runs API $API."
  ui_print "  It will probably crash. Build from a ROM with the same Android version."
fi
if [ -n "$rom_device" ] && [ "$rom_device" != "$device" ]; then
  ui_print "! The launcher comes from a '$rom_device' ROM, but this phone is '$device'."
  ui_print "  It may still work on the same HyperOS and Android version, but that's untested."
fi

[ -f "$APP_APK" ] || abort "! HyperOSLauncher.apk is missing from the zip"
# Without the whitelist an enforcing ROM refuses to boot, so never install without it.
[ -f "$PERM_XML" ] || abort "! privapp permission whitelist is missing from the zip"

# Recents must point at com.miui.home, or the launcher can't open the gesture input channel and
# crash-loops. POCO ROMs normally ship Xiaomi's own overlay for that, enabled by the
# ro.miui.product.home=com.miui.home this module sets (system.prop). This module's overlay is only
# a fallback for ROMs without it; installing both would leave ours idle at best.
if [ -f "$XIAOMI_OVERLAY" ]; then
  rm -rf "${OVERLAY_APK%/*}"
  rmdir "$MODPATH/system/product/overlay" 2>/dev/null
  ui_print "- Recents: Xiaomi's ${XIAOMI_OVERLAY##*/}, enabled by ro.miui.product.home"
else
  [ -f "$OVERLAY_APK" ] || abort "! This ROM has no ${XIAOMI_OVERLAY##*/} and the zip lacks the fallback overlay"
  ui_print "- Recents: this module's fallback overlay (the ROM has no ${XIAOMI_OVERLAY##*/})"
fi

# Another enabled module that swaps launchers would fight over Recents and the home role.
for dir in /data/adb/modules/*; do
  id=${dir##*/}
  [ "$id" = "$MY_ID" ] && continue
  [ -f "$dir/disable" ] || [ -f "$dir/remove" ] && continue
  for p in priv-app/MiLauncherGlobal priv-app/MiuiHome overlay/MiuiHomeLauncherResOverlay.apk; do
    if [ -e "$dir/system/product/$p" ] || [ -e "$dir/product/$p" ]; then
      abort "! Module '$id' also replaces the launcher ($p). Disable or remove it first."
    fi
  done
done

if [ -f "$THEME/.replace" ]; then
  ui_print "- Replaces /product/media/theme (default theme, icons, widgets)."
  # KernelSU's overlayfs metamodules may merge folders instead of honouring .replace, leaving stock
  # files visible next to the module's. Hide each one with a whiteout (a 0:0 character device).
  # Magisk honours .replace and doesn't understand whiteouts, so this is KernelSU only.
  if [ -n "$KSU" ]; then
    list=$MODPATH/.theme_files
    : > "$list"
    # Stock files visible now. With an older version of this module mounted, the view is stock plus
    # module files, so anything the new module doesn't ship is a stock file, or one an older version
    # shipped and this one dropped; hiding the latter is harmless.
    (cd /product/media/theme 2>/dev/null && find . -type f) >> "$list"
    # Files an installed version already hides don't show up above; carry its whiteouts over.
    for old in "/data/adb/modules/$MY_ID/product/media/theme" "/data/adb/modules/$MY_ID/system/product/media/theme"; do
      [ -d "$old" ] && (cd "$old" && find . -type c) >> "$list"
    done
    count=0
    sed 's#^\./##' "$list" | sort -u > "$list.sorted"
    while IFS= read -r rel; do
      [ -z "$rel" ] || [ "$rel" = ".replace" ] && continue
      [ -e "$THEME/$rel" ] && continue
      mkdir -p "$(dirname "$THEME/$rel")"
      mknod "$THEME/$rel" c 0 0 && count=$((count + 1))
    done < "$list.sorted"
    rm -f "$list" "$list.sorted"
    [ "$count" -gt 0 ] && ui_print "  Hid $count stock theme file(s) the module doesn't ship."
  fi
  ui_print "  After rebooting, re-apply Default in the Theme Store to load it."
fi

ui_print "- POCO Launcher stays installed; disable this module to get it back as it was"
