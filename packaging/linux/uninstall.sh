#!/usr/bin/env bash
# e-approval-kr Linux uninstaller.
#
#   sudo /opt/e-approval-kr/uninstall.sh            # remove program + service, KEEP data and the system user
#   sudo /opt/e-approval-kr/uninstall.sh --purge    # also delete /var/lib/e-approval-kr and the system user
#   ./uninstall.sh --dest /tmp/stage [--purge]      # test mode (counterpart of install.sh --dest)
set -euo pipefail

NAME=e-approval-kr
SVC_USER=e-approval-kr
PREFIX=/opt/e-approval-kr
DATA=/var/lib/e-approval-kr
UNIT=/etc/systemd/system/$NAME.service
DROPIN_DIR=/etc/systemd/system/$NAME.service.d
BINDIR=/usr/local/bin

DEST=""
PURGE=0
while [ $# -gt 0 ]; do
    case "$1" in
        --purge) PURGE=1 ;;
        --dest) shift; DEST="${1:-}" ;;
        --dest=*) DEST="${1#--dest=}" ;;
        -h|--help) sed -n '2,7p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "error: unknown option: $1" >&2; exit 1 ;;
    esac
    shift
done
if [ -z "$DEST" ] && [ "$(id -u)" -ne 0 ]; then
    echo "error: run as root: sudo $0 $*" >&2; exit 1
fi
R="$DEST"

if [ -z "$DEST" ] && command -v systemctl >/dev/null 2>&1; then
    systemctl disable --now "$NAME" >/dev/null 2>&1 || true
fi
rm -f "$R$UNIT"
rm -rf "$R$DROPIN_DIR"
[ -z "$DEST" ] && command -v systemctl >/dev/null 2>&1 && systemctl daemon-reload || true

# firewall rules added by install.sh --lan
if [ -z "$DEST" ] && [ -s "$R$PREFIX/.ufw-rules" ] && command -v ufw >/dev/null 2>&1; then
    while read -r net port; do
        [ -n "$net" ] && ufw delete allow from "$net" to any port "$port" proto tcp >/dev/null 2>&1 || true
    done < "$R$PREFIX/.ufw-rules"
fi

# only remove the symlink if it still points to us
if [ -L "$R$BINDIR/$NAME" ] && [ "$(readlink "$R$BINDIR/$NAME")" = "$PREFIX/$NAME" ]; then
    rm -f "$R$BINDIR/$NAME"
fi
rm -f "$R$BINDIR/$NAME-admin"
rm -rf "$R$PREFIX" "$R$PREFIX.new" "$R$PREFIX.old"

if [ "$PURGE" = 1 ]; then
    rm -rf "$R$DATA"
    if [ -z "$DEST" ] && id -u "$SVC_USER" >/dev/null 2>&1; then
        userdel "$SVC_USER" >/dev/null 2>&1 || true
        getent group "$SVC_USER" >/dev/null 2>&1 && groupdel "$SVC_USER" >/dev/null 2>&1 || true
    fi
    echo "e-approval-kr removed, including all data."
else
    echo "e-approval-kr removed. Data kept in $DATA (delete it with: sudo rm -rf $DATA)."
    echo "Reinstalling later reuses that data."
fi
