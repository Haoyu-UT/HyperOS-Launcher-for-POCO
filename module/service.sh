#!/system/bin/sh
# Make the HyperOS launcher the home app, as early in boot as possible and on every boot.
#
# Recents points at com.miui.home (Xiaomi's overlay or this module's fallback), and only that may
# open the gesture input channel. Any other launcher running as home (POCO Launcher included)
# crash-loops, so there is no working choice to preserve. Waiting for sys.boot_completed would
# let POCO Launcher start first; the role service is up well before the first home launch.

HOME_PKG=com.miui.home
ROLE=android.app.role.HOME

i=0
while [ $i -lt 120 ]; do
  if pm path $HOME_PKG >/dev/null 2>&1; then
    holder=$(cmd role get-role-holders --user 0 $ROLE 2>/dev/null)
    [ "$holder" = "$HOME_PKG" ] && exit 0
    cmd role add-role-holder --user 0 $ROLE $HOME_PKG >/dev/null 2>&1
  fi
  sleep 1
  i=$((i + 1))
done
