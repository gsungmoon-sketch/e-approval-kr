# Packaging / 패키징

Self-contained server packages so end users can install e-approval-kr **without Python**.
Releases are built by `.github/workflows/release.yml` on every `v*` tag; this page explains the
layout and how to build locally.

| OS | Release asset | Installs | Service | Data |
|---|---|---|---|---|
| Windows 10/11 x64 | `e-approval-kr-setup-<ver>-win-x64.exe` (Inno Setup) | `C:\Program Files\e-approval-kr` | Windows service `e-approval-kr` (WinSW), account `NT AUTHORITY\LocalService` | `%ProgramData%\e-approval-kr` (SYSTEM / Administrators / LOCAL SERVICE only) |
| macOS arm64 / x64 | `e-approval-kr-<ver>-macos-<arch>.pkg` | `/usr/local/lib/e-approval-kr` (+ `/usr/local/bin/e-approval-kr`) | LaunchDaemon `com.e-approval-kr.server`, hidden user `_eapprovalkr` | `/Library/Application Support/e-approval-kr` (0700) |
| Linux x64 (glibc 2.35+, systemd) | `e-approval-kr-<ver>-linux-x64.tar.gz` → `sudo ./install.sh` | `/opt/e-approval-kr` (+ `/usr/local/bin/e-approval-kr`) | systemd `e-approval-kr.service` (hardened), user `e-approval-kr` | `/var/lib/e-approval-kr` (0700) |
| any OS with Python 3.10+ | `e-approval-kr-<ver>.pyz` | nothing (run it) | – | `GYEOLJAE_DATA` or OS default |

Every package carries `LICENSE.txt`, `THIRD-PARTY-NOTICES.txt` and the HTML manual (`manual/index.html`).
Services run `e-approval-kr serve` **without** `--host/--port`, so the address chosen in the web setup
wizard (`server.json` in the data directory) is used; the default is `127.0.0.1:8130` (this machine only).
After installing, open `http://127.0.0.1:8130/setup` on the same machine (Windows and macOS GUI installs
open it automatically). Headless: `sudo e-approval-kr-admin setup` (Linux/macOS) or the
"관리 도구 (Admin console)" Start-menu entry on Windows.

## Files

| Path | Purpose |
|---|---|
| `entry.py` | PyInstaller entry → `gyeoljae.cli.main` (all arguments passed through) |
| `build.py` | PyInstaller **one-dir** build → `dist/e-approval-kr/`; `--tarball` makes the Linux tarball |
| `THIRD-PARTY-NOTICES.txt` | licences of bundled components (Python, SQLite, OpenSSL, PyInstaller bootloader, WinSW) |
| `smoke_test.py` | CI: silent install → `/healthz` → `setup` → login → uninstall (installs a real service!) |
| `windows/e-approval-kr.iss` | Inno Setup script; `admin-console.cmd` = elevated prompt with `GYEOLJAE_DATA` set |
| `macos/build_pkg.sh` | pkgbuild + productbuild; `scripts/pre|postinstall`, LaunchDaemon plist, `uninstall.sh` |
| `linux/install.sh`, `uninstall.sh` | installer (`--lan`, `--port N`, `--no-start`, test mode `--dest DIR`); `uninstall.sh [--purge]` |
| `linux/e-approval-kr.service.in` | systemd unit template; `e-approval-kr-admin` = CLI as the service user |
| `../scripts/build_zipapp.py` | `.pyz` (stdlib `zipapp`) |

One-dir instead of one-file: faster start (nothing unpacked to a temp dir on each service start)
and much fewer antivirus false positives. Installers copy the whole directory.

## Build locally

```sh
python -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install pyinstaller==6.22.3
python packaging/build.py                    # -> dist/e-approval-kr/   (Linux: add --tarball)
python scripts/build_zipapp.py               # -> dist/e-approval-kr-<ver>.pyz
```
- **Windows**: put `WinSW.NET461.exe` and WinSW's `LICENSE.txt` (v2.12.0, sha256 values in
  `release.yml`) into `build\winsw\`, then `iscc /DAppVersion=<ver> packaging\windows\e-approval-kr.iss`.
- **macOS**: `packaging/macos/build_pkg.sh <ver>` (builds for the machine's architecture).
- **Linux test without touching the system**: `./install.sh --dest /tmp/stage` stages all files under
  `/tmp/stage` (no user, no systemd, no firewall).

PyInstaller binaries only run on the OS/architecture they were built on, and on Linux only on glibc
≥ the build machine's (CI builds on Ubuntu 22.04 → glibc 2.35). Musl (Alpine) is not supported; use the `.pyz`.

## Windows notes
- Service wrapper: [WinSW](https://github.com/winsw/winsw) v2.12.0 (MIT, `WinSW-LICENSE.txt`), .NET
  Framework 4.6.1+ build (built into Windows 10/11). Its XML is generated at install time.
- Fallback without WinSW (e.g. blocked by policy): Task Scheduler —
  `schtasks /Create /TN e-approval-kr /SC ONSTART /RU "NT AUTHORITY\LOCALSERVICE" /TR "\"C:\Program Files\e-approval-kr\e-approval-kr.exe\" --data C:\ProgramData\e-approval-kr serve"`.
- The firewall task (off by default) allows TCP 8130 from the **local subnet** for the Private/Domain
  profiles only. It only matters after you change the listen address to `0.0.0.0` in the setup wizard.
- Silent: `setup.exe /VERYSILENT /SUPPRESSMSGBOXES [/TASKS=firewall]`; uninstall
  `unins000.exe /VERYSILENT [/PURGEDATA]` (data is kept unless `/PURGEDATA`, or "Yes" in the GUI prompt).
- Logs: `%ProgramData%\e-approval-kr\logs`.

## Signing / notarization (not done yet)
The packages are **unsigned**. Windows SmartScreen shows "Windows protected your PC" → *More info* →
*Run anyway*. macOS Gatekeeper blocks a double-clicked unsigned `.pkg`: right-click (Control-click) the
file → *Open* → *Open*; on macOS 15+ use System Settings → Privacy & Security → *Open Anyway*; or install
from Terminal: `sudo installer -pkg e-approval-kr-<ver>-macos-arm64.pkg -target /`. Always compare the
file with `SHA256SUMS` from the release first. Future work: Authenticode signing (`signtool`) and
Apple Developer ID signing + `notarytool` / `stapler`.

---

## 한국어 요약
- 사용자는 Release 에서 OS 에 맞는 파일 하나만 받으면 됩니다(파이썬 불필요). 설치 후 **같은 컴퓨터**
  브라우저에서 `http://127.0.0.1:8130/setup` 으로 첫 관리자를 만듭니다.
- 서비스는 권한 없는 전용 계정으로 돌고(Windows LocalService / macOS `_eapprovalkr` / Linux `e-approval-kr`),
  데이터 폴더는 그 계정과 관리자만 접근합니다. 제거해도 데이터는 남습니다(Windows 제거 시 "예",
  `/PURGEDATA`, Linux·macOS `uninstall.sh --purge` 로만 삭제).
- 직접 빌드: `pip install pyinstaller==6.22.3` → `python packaging/build.py` (Linux `--tarball`),
  Windows 는 WinSW 를 `build\winsw\` 에 두고 `iscc`, macOS 는 `packaging/macos/build_pkg.sh <버전>`.
- 서명 전 패키지라 Windows SmartScreen("추가 정보 → 실행"), macOS Gatekeeper("우클릭 → 열기" 또는
  설정 → 개인정보 보호 및 보안 → "그래도 열기") 안내가 필요합니다. 반드시 `SHA256SUMS` 로 확인하세요.
- 사내 LAN 공개는 평문 HTTP 위험이 있습니다. 설명서의 HTTPS(리버스 프록시 또는 `serve --https`)를 따르세요.
