#!/system/bin/sh
# Make the HyperOS launcher the home app, as early in boot as possible and on every boot.
#
# Recents points at com.miui.home (Xiaomi's overlay or this module's fallback), and only that may
# open the gesture input channel. Any other launcher running as home (POCO Launcher included)
# crash-loops, so there is no working choice to preserve. Waiting for sys.boot_completed would
# let POCO Launcher start first; the role service is up well before the first home launch.

MODDIR=${0%/*}
HOME_PKG=com.miui.home
POCO_PKG=com.mi.android.globallauncher
ROLE=android.app.role.HOME
GUARD=/data/adb/service.d/poco_hyperos_launcher_guard.sh

i=0
while [ $i -lt 120 ]; do
  if pm path $HOME_PKG >/dev/null 2>&1; then
    holder=$(cmd role get-role-holders --user 0 $ROLE 2>/dev/null)
    [ "$holder" = "$HOME_PKG" ] && break
    cmd role add-role-holder --user 0 $ROLE $HOME_PKG >/dev/null 2>&1
  fi
  sleep 1
  i=$((i + 1))
done
[ "$holder" = "$HOME_PKG" ] || exit 0

# POCO Launcher can't work while this module is active, yet other apps still start its process.
# It then sits frozen in the background and reacts late to settings changes (for example, it
# turns gestures off long after a navigation-mode change). Disabling it keeps its data, so the
# layout survives. The guard, a boot script outside the module, enables it again whenever this
# module is disabled or removed. It goes in first, so POCO Launcher is never disabled without a
# way back.
mkdir -p ${GUARD%/*}
cp "$MODDIR/launcher-guard.sh" $GUARD && chmod 755 $GUARD || exit 0
pm disable-user --user 0 $POCO_PKG >/dev/null 2>&1
