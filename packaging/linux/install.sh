#!/usr/bin/env bash
# e-approval-kr Linux installer (run from the extracted tarball directory).
#
#   sudo ./install.sh                 # install/upgrade, start service on 127.0.0.1:8130 (this machine only)
#   sudo ./install.sh --lan           # also listen on all interfaces + ufw rule for local private subnets
#   sudo ./install.sh --port 8140     # different port
#   sudo ./install.sh --no-start      # install but do not enable/start the service
#   ./install.sh --dest /tmp/stage    # test mode: stage files under a directory, no user/systemd changes
#
# Layout: /opt/e-approval-kr (program, manual), /var/lib/e-approval-kr (data, 0700, owned by the
# system user e-approval-kr), /etc/systemd/system/e-approval-kr.service,
# /usr/local/bin/e-approval-kr (symlink), /usr/local/bin/e-approval-kr-admin (CLI as service user).
# Upgrades keep the data directory. Remove with /opt/e-approval-kr/uninstall.sh [--purge].
set -euo pipefail

NAME=e-approval-kr
SVC_USER=e-approval-kr
PREFIX=/opt/e-approval-kr
DATA=/var/lib/e-approval-kr
UNIT=/etc/systemd/system/$NAME.service
DROPIN_DIR=/etc/systemd/system/$NAME.service.d
BINDIR=/usr/local/bin

DEST=""
LAN=0
PORT=""
START=1

die() { echo "error: $*" >&2; exit 1; }
usage() { sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
    case "$1" in
        --lan) LAN=1 ;;
        --port) shift; PORT="${1:-}" ;;
        --port=*) PORT="${1#--port=}" ;;
        --no-start) START=0 ;;
        --dest) shift; DEST="${1:-}"; [ -n "$DEST" ] || die "--dest needs a directory" ;;
        --dest=*) DEST="${1#--dest=}" ;;
        -h|--help) usage; exit 0 ;;
        *) die "unknown option: $1 (see --help)" ;;
    esac
    shift
done
if [ -n "$PORT" ]; then
    case "$PORT" in *[!0-9]*|"") die "--port must be a number" ;; esac
    [ "$PORT" -ge 1 ] && [ "$PORT" -le 65535 ] || die "--port out of range"
fi

HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$HERE/$NAME"
[ -x "$SRC/$NAME" ] || die "program directory not found: $SRC (run install.sh from the extracted tarball)"

if [ -n "$DEST" ]; then
    mkdir -p "$DEST"
    DEST="$(cd "$DEST" && pwd)"
    echo "TEST MODE: staging under $DEST (no user, no systemd, no firewall changes)"
else
    [ "$(id -u)" -eq 0 ] || die "run as root: sudo $0 $*"
    [ "$(uname -s)" = Linux ] || die "Linux only"
    command -v systemctl >/dev/null 2>&1 || die "systemd (systemctl) is required"
fi
R="$DEST"   # path prefix: empty for a real install

# ---- 1. system user -------------------------------------------------------------------------
if [ -z "$DEST" ] && ! id -u "$SVC_USER" >/dev/null 2>&1; then
    NOLOGIN="$(command -v nologin || echo /usr/sbin/nologin)"
    [ -x "$NOLOGIN" ] || NOLOGIN=/bin/false
    useradd --system --user-group --home-dir "$DATA" --no-create-home --shell "$NOLOGIN" \
        --comment "e-approval-kr server" "$SVC_USER"
    echo "created system user $SVC_USER"
fi

# ---- 2. stop a running instance (upgrade) ----------------------------------------------------
WAS_ACTIVE=0
if [ -z "$DEST" ] && systemctl is-active --quiet "$NAME" 2>/dev/null; then
    WAS_ACTIVE=1
    systemctl stop "$NAME"
fi

# ---- 3. program files (replace atomically, keep nothing user-owned in PREFIX) ------------------
mkdir -p "$R$(dirname "$PREFIX")"
rm -rf "$R$PREFIX.new"
cp -a "$SRC" "$R$PREFIX.new"
cp "$HERE/uninstall.sh" "$HERE/install.sh" "$R$PREFIX.new/"
cp "$HERE/$NAME-admin" "$HERE/$NAME.service.in" "$R$PREFIX.new/"
[ -f "$R$PREFIX/.ufw-rules" ] && cp "$R$PREFIX/.ufw-rules" "$R$PREFIX.new/.ufw-rules"
chmod -R u=rwX,go=rX "$R$PREFIX.new"
chmod 0755 "$R$PREFIX.new/$NAME" "$R$PREFIX.new/uninstall.sh" "$R$PREFIX.new/install.sh" "$R$PREFIX.new/$NAME-admin"
[ -z "$DEST" ] && chown -R root:root "$R$PREFIX.new"
rm -rf "$R$PREFIX.old"
[ -e "$R$PREFIX" ] && mv "$R$PREFIX" "$R$PREFIX.old"
mv "$R$PREFIX.new" "$R$PREFIX"
rm -rf "$R$PREFIX.old"

mkdir -p "$R$BINDIR"
ln -sfn "$PREFIX/$NAME" "$R$BINDIR/$NAME"
install -m 0755 "$HERE/$NAME-admin" "$R$BINDIR/$NAME-admin"

# ---- 4. data directory -----------------------------------------------------------------------
mkdir -p "$R$DATA"
chmod 0700 "$R$DATA"
[ -z "$DEST" ] && chown "$SVC_USER:$SVC_USER" "$R$DATA"

# ---- 5. systemd unit (+ optional drop-in for --lan/--port) -------------------------------------
mkdir -p "$R$(dirname "$UNIT")"
sed -e "s|@PREFIX@|$PREFIX|g" -e "s|@DATA@|$DATA|g" -e "s|@USER@|$SVC_USER|g" \
    "$HERE/$NAME.service.in" > "$R$UNIT"
chmod 0644 "$R$UNIT"
if [ "$LAN" = 1 ] || [ -n "$PORT" ]; then
    HOST=127.0.0.1; [ "$LAN" = 1 ] && HOST=0.0.0.0
    mkdir -p "$R$DROPIN_DIR"
    cat > "$R$DROPIN_DIR/listen.conf" <<EOF
# written by install.sh ($( [ "$LAN" = 1 ] && echo "--lan " )${PORT:+--port $PORT}). Overrides server.json host/port.
# Remove this file and run 'systemctl daemon-reload && systemctl restart $NAME' to use server.json again.
[Service]
ExecStart=
ExecStart=$PREFIX/$NAME serve --host $HOST --port ${PORT:-8130}
EOF
fi
PORT_EFF="${PORT:-8130}"

# ---- 6. firewall (only with --lan, only ufw, only private IPv4 subnets) ------------------------
private_subnets() {
    # prints network/prefix for every global IPv4 address in a private (RFC 1918) range
    ip -o -4 addr show scope global 2>/dev/null | awk '{print $4}' | while IFS=/ read -r addr len; do
        IFS=. read -r a b c d <<<"$addr"
        priv=0
        [ "$a" -eq 10 ] && priv=1
        [ "$a" -eq 172 ] && [ "$b" -ge 16 ] && [ "$b" -le 31 ] && priv=1
        [ "$a" -eq 192 ] && [ "$b" -eq 168 ] && priv=1
        [ "$priv" = 1 ] || continue
        ipn=$(( (a << 24) | (b << 16) | (c << 8) | d ))
        mask=$(( len == 0 ? 0 : (0xFFFFFFFF << (32 - len)) & 0xFFFFFFFF ))
        net=$(( ipn & mask ))
        echo "$(( (net >> 24) & 255 )).$(( (net >> 16) & 255 )).$(( (net >> 8) & 255 )).$(( net & 255 ))/$len"
    done | sort -u
}
if [ "$LAN" = 1 ]; then
    echo
    echo "WARNING: --lan serves plain HTTP to your local network. Passwords and documents can be"
    echo "         read by anyone on that network. Use HTTPS (reverse proxy or 'serve --https') - see"
    echo "         the manual. Never port-forward this server to the internet."
    if [ -z "$DEST" ] && command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
        : > "$R$PREFIX/.ufw-rules"
        for net in $(private_subnets); do
            ufw allow from "$net" to any port "$PORT_EFF" proto tcp comment "$NAME" >/dev/null
            echo "$net $PORT_EFF" >> "$R$PREFIX/.ufw-rules"
            echo "ufw: allowed $net -> tcp/$PORT_EFF"
        done
        [ -s "$R$PREFIX/.ufw-rules" ] || echo "ufw: no private IPv4 subnet found - add a rule yourself"
    else
        echo "ufw not active: allow tcp/$PORT_EFF from your LAN only in your firewall, e.g."
        echo "  sudo ufw allow from <your-lan>/24 to any port $PORT_EFF proto tcp"
    fi
fi

# ---- 7. enable/start ------------------------------------------------------------------------
if [ -z "$DEST" ]; then
    systemctl daemon-reload
    if [ "$START" = 1 ]; then
        systemctl enable "$NAME" >/dev/null 2>&1
        systemctl restart "$NAME"
    elif [ "$WAS_ACTIVE" = 1 ]; then
        echo "note: service was running before the upgrade and is now stopped (--no-start)"
    fi
fi

VER="$(cat "$R$PREFIX/VERSION" 2>/dev/null || echo '?')"
cat <<EOF

e-approval-kr $VER installed.
  program : $PREFIX        data: $DATA (0700, user $SVC_USER)
  service : systemctl status $NAME      logs: journalctl -u $NAME
  manual  : $PREFIX/manual/index.html

First-time setup:
  - On this machine, open http://127.0.0.1:$PORT_EFF/setup in a browser (setup wizard), or
  - from a shell (e.g. over SSH): sudo $NAME-admin setup
Uninstall: sudo $PREFIX/uninstall.sh   (add --purge to also delete all data)
EOF
