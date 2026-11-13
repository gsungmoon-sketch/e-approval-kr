#!/bin/sh
# Build the macOS installer package from the PyInstaller output (run packaging/build.py first).
#   packaging/macos/build_pkg.sh <version> [dist-dir]
# -> <dist-dir>/e-approval-kr-<version>-macos-<arm64|x64>.pkg   (unsigned; see packaging/README.md)
set -eu
VERSION="${1:?usage: build_pkg.sh <version> [dist-dir]}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
DIST="${2:-$ROOT/dist}"
APP="$DIST/e-approval-kr"
[ -x "$APP/e-approval-kr" ] || { echo "missing $APP/e-approval-kr - run packaging/build.py first" >&2; exit 1; }

case "$(uname -m)" in
    arm64) ARCH=arm64; HOSTARCH=arm64; MINOS=11.0 ;;
    x86_64) ARCH=x64; HOSTARCH=x86_64; MINOS=10.15 ;;
    *) echo "unsupported arch $(uname -m)" >&2; exit 1 ;;
esac

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
PAYLOAD="$WORK/root"
mkdir -p "$PAYLOAD/usr/local/lib" "$PAYLOAD/usr/local/bin" "$PAYLOAD/Library/LaunchDaemons" "$WORK/resources"
cp -R "$APP" "$PAYLOAD/usr/local/lib/e-approval-kr"
install -m 0755 "$HERE/uninstall.sh" "$PAYLOAD/usr/local/lib/e-approval-kr/uninstall.sh"
install -m 0755 "$HERE/e-approval-kr-admin" "$PAYLOAD/usr/local/bin/e-approval-kr-admin"
install -m 0644 "$HERE/com.e-approval-kr.server.plist" "$PAYLOAD/Library/LaunchDaemons/"
plutil -lint "$PAYLOAD/Library/LaunchDaemons/com.e-approval-kr.server.plist"

pkgbuild --root "$PAYLOAD" --scripts "$HERE/scripts" --identifier com.e-approval-kr.server \
    --version "$VERSION" --install-location / --ownership recommended "$WORK/component.pkg"

cp "$ROOT/LICENSE" "$WORK/resources/LICENSE.txt"
cp "$HERE/resources/conclusion.html" "$WORK/resources/"
sed -e "s/@VERSION@/$VERSION/g" -e "s/@HOSTARCH@/$HOSTARCH/g" -e "s/@MINOS@/$MINOS/g" \
    "$HERE/distribution.xml.in" > "$WORK/distribution.xml"
OUT="$DIST/e-approval-kr-$VERSION-macos-$ARCH.pkg"
productbuild --distribution "$WORK/distribution.xml" --resources "$WORK/resources" \
    --package-path "$WORK" "$OUT"
echo "built $OUT"
