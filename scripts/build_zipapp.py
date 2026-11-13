#!/usr/bin/env python3
"""Build e-approval-kr-<version>.pyz: a single-file zipapp for machines that already have Python 3.10+.

    python scripts/build_zipapp.py [--version X.Y.Z] [--out dist]
    python3 dist/e-approval-kr-<ver>.pyz serve            # run it (Windows: py e-approval-kr-<ver>.pyz serve)

Pure standard library: the archive holds only the gyeoljae package (no compiled code), so it
runs unchanged on Windows, macOS and Linux; Python
imports it straight from the zip (nothing is extracted).
"""
import argparse
import re
import shutil
import sys
import tempfile
import zipapp
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = '''import sys
if sys.version_info < (3, 10):
    sys.exit("e-approval-kr needs Python 3.10 or newer (found %d.%d)" % sys.version_info[:2])
from gyeoljae.cli import main
sys.exit(main())
'''


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", help="version in the file name (default: src/gyeoljae/__init__.py)")
    ap.add_argument("--out", default=str(ROOT / "dist"))
    a = ap.parse_args()
    ver = a.version
    if not ver:
        m = re.search(r'__version__\s*=\s*["\']([^"\']+)', (ROOT / "src/gyeoljae/__init__.py").read_text(encoding="utf-8"))
        ver = m.group(1)
    ver = ver.lstrip("v")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"e-approval-kr-{ver}.pyz"
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp) / "app"
        shutil.copytree(ROOT / "src" / "gyeoljae", stage / "gyeoljae",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
        shutil.copy2(ROOT / "LICENSE", stage / "LICENSE.txt")
        (stage / "__main__.py").write_text(MAIN, encoding="utf-8")
        zipapp.create_archive(stage, target, interpreter="/usr/bin/env python3", compressed=True)
    print(f"built {target} ({target.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
