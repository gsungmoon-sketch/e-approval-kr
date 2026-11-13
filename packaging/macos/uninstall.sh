#!/bin/sh
# e-approval-kr macOS uninstaller (installed as /usr/local/lib/e-approval-kr/uninstall.sh).
#   sudo /usr/local/lib/e-approval-kr/uninstall.sh          # remove program + daemon, KEEP data
#   sudo /usr/local/lib/e-approval-kr/uninstall.sh --purge  # also delete data, logs and the _eapprovalkr user
set -u
LABEL=com.e-approval-kr.server
DATA="/Library/Application Support/e-approval-kr"
U=_eapprovalkr
PURGE=0
[ "${1:-}" = "--purge" ] && PURGE=1
if [ "$(id -u)" -ne 0 ]; then
    exec sudo -- "$0" "$@"
fi
launchctl bootout "system/$LABEL" >/dev/null 2>&1 || true
rm -f "/Library/LaunchDaemons/$LABEL.plist"
if [ "$(readlink /usr/local/bin/e-approval-kr 2>/dev/null)" = /usr/local/lib/e-approval-kr/e-approval-kr ]; then
    rm -f /usr/local/bin/e-approval-kr
fi
rm -f /usr/local/bin/e-approval-kr-admin
rm -rf /usr/local/lib/e-approval-kr
pkgutil --forget "$LABEL" >/dev/null 2>&1 || true
if [ "$PURGE" = 1 ]; then
    rm -rf "$DATA" /Library/Logs/e-approval-kr
    dscl . -delete "/Users/$U" >/dev/null 2>&1 || true
    dscl . -delete "/Groups/$U" >/dev/null 2>&1 || true
    echo "e-approval-kr removed, including all data."
else
    echo "e-approval-kr removed. Data kept in $DATA (logs: /Library/Logs/e-approval-kr)."
fi
exit 0
