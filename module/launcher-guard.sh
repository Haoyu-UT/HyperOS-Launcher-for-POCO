#!/system/bin/sh
# Installed to /data/adb/service.d/ by the poco_hyperos_launcher module, which disables POCO
# Launcher while it is active. Disabling or removing a module runs none of its own scripts, so
# this runs on every boot instead: whenever the module is off, it enables POCO Launcher again, and
# once the module is gone for good, it deletes itself.

MOD=/data/adb/modules/poco_hyperos_launcher
POCO_PKG=com.mi.android.globallauncher
ROLE=android.app.role.HOME

[ -d $MOD ] && [ ! -f $MOD/disable ] && [ ! -f $MOD/remove ] && exit 0

i=0
until pm path $POCO_PKG >/dev/null 2>&1; do
  i=$((i + 1))
  [ $i -ge 120 ] && exit 1
  sleep 1
done

pm enable --user 0 $POCO_PKG >/dev/null 2>&1
# The home role went to com.miui.home, which no longer exists; hand it back if nothing holds it.
holder=$(cmd role get-role-holders --user 0 $ROLE 2>/dev/null)
if [ -z "$holder" ] || ! pm path "$holder" >/dev/null 2>&1; then
  cmd role add-role-holder --user 0 $ROLE $POCO_PKG >/dev/null 2>&1
fi

[ -d $MOD ] || rm -f "$0"
