#!/system/bin/sh
# Runs when the module is removed, normally early in boot (post-fs-data) before pm works. The
# guard (see launcher-guard.sh) enables POCO Launcher later in that boot and then deletes itself;
# make sure it's there even if someone deleted it.

MODDIR=${0%/*}
GUARD=/data/adb/service.d/poco_hyperos_launcher_guard.sh

mkdir -p ${GUARD%/*}
cp "$MODDIR/launcher-guard.sh" $GUARD && chmod 755 $GUARD

# Some managers run this with the system up; then give POCO Launcher back right away.
[ "$(getprop sys.boot_completed)" = "1" ] && pm enable --user 0 com.mi.android.globallauncher >/dev/null 2>&1
