#!/usr/bin/env python3
"""Build the e-approval-kr server binary (PyInstaller, one-dir) and the Linux tarball.

Usage (from the repository root, inside a venv with `pip install pyinstaller==6.22.3`):
    python packaging/build.py                 # -> dist/e-approval-kr/  (binary + manual + LICENSE)
    python packaging/build.py --tarball       # Linux: also dist/e-approval-kr-<ver>-linux-<arch>.tar.gz
    python packaging/build.py --version 1.2.3 # override (CI passes the tag without the leading 'v')

One-dir (not one-file) is used on purpose: it starts faster (nothing is unpacked to a temp
dir on every service start) and is far less likely to be flagged by antivirus heuristics.
The installers (Inno Setup / pkgbuild / install.sh) copy the whole directory.
"""
import argparse
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "packaging"
NAME = "e-approval-kr"


def version_from_source() -> str:
    text = (ROOT / "src" / "gyeoljae" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', text)
    if not m:
        sys.exit("cannot find __version__ in src/gyeoljae/__init__.py")
    return m.group(1)


def arch_tag() -> str:
    m = platform.machine().lower()
    return {"x86_64": "x64", "amd64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(m, m)


def os_tag() -> str:
    return {"win32": "win", "darwin": "macos"}.get(sys.platform, "linux")


def run_pyinstaller(work: Path, dist: Path) -> Path:
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--console",
           "--name", NAME, "--paths", str(ROOT / "src"), "--collect-submodules", "gyeoljae",
           "--exclude-module", "tkinter", "--exclude-module", "_tkinter",
           "--distpath", str(dist), "--workpath", str(work / "work"), "--specpath", str(work),
           str(PKG / "entry.py")]
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=ROOT)
    return dist / NAME


def add_docs(app: Path, version: str, require_manual: bool) -> None:
    shutil.copy2(ROOT / "LICENSE", app / "LICENSE.txt")
    shutil.copy2(PKG / "THIRD-PARTY-NOTICES.txt", app / "THIRD-PARTY-NOTICES.txt")
    (app / "VERSION").write_text(version + "\n", encoding="utf-8")
    man = ROOT / "docs" / "manual"
    if man.is_dir():
        shutil.copytree(man, app / "manual", dirs_exist_ok=True)
    if not (app / "manual" / "index.html").is_file():
        msg = "docs/manual/index.html not found - package has no HTML manual"
        if require_manual:
            sys.exit("error: " + msg)
        print("warning: " + msg, file=sys.stderr)


def make_tarball(app: Path, version: str, out: Path) -> Path:
    base = f"{NAME}-{version}-linux-{arch_tag()}"
    tgz = out / f"{base}.tar.gz"
    lin = PKG / "linux"

    def norm(ti: tarfile.TarInfo) -> tarfile.TarInfo:
        ti.uid = ti.gid = 0
        ti.uname = ti.gname = "root"
        return ti

    with tarfile.open(tgz, "w:gz") as tf:
        tf.add(app, arcname=f"{base}/{NAME}", filter=norm)
        for f in ("install.sh", "uninstall.sh", "e-approval-kr.service.in", "e-approval-kr-admin"):
            ti = norm(tf.gettarinfo(str(lin / f), arcname=f"{base}/{f}"))
            ti.mode = 0o755 if not f.endswith(".in") else 0o644
            with open(lin / f, "rb") as fh:
                tf.addfile(ti, fh)
        for src, arc in ((ROOT / "LICENSE", "LICENSE.txt"), (lin / "README.txt", "README.txt")):
            tf.add(src, arcname=f"{base}/{arc}", filter=norm)
    return tgz


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", default=None, help="package version (default: src/gyeoljae/__init__.py)")
    ap.add_argument("--out", default=str(ROOT / "dist"), help="output directory (default: dist/)")
    ap.add_argument("--tarball", action="store_true", help="Linux: also build the install tarball")
    ap.add_argument("--require-manual", action="store_true", help="fail if docs/manual/index.html is missing")
    a = ap.parse_args()
    version = (a.version or version_from_source()).lstrip("v")
    out = Path(a.out).resolve()
    work = ROOT / "build" / "pyinstaller"
    shutil.rmtree(out / NAME, ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)
    app = run_pyinstaller(work, out)
    add_docs(app, version, a.require_manual)
    exe = app / (NAME + (".exe" if os.name == "nt" else ""))
    r = subprocess.run([str(exe), "--help"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        sys.exit(f"built binary failed `--help` (rc={r.returncode}):\n{r.stdout}\n{r.stderr}")
    print(f"built {exe} ({os_tag()}-{arch_tag()}, version {version})")
    if a.tarball:
        if os_tag() != "linux":
            sys.exit("--tarball is for Linux only")
        print("tarball:", make_tarball(app, version, out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
