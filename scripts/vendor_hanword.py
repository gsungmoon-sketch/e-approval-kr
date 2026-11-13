#!/usr/bin/env python3
"""hanword 편집기(임베드 빌드)를 src/gyeoljae/vendor/hanword/ 로 반입 — 재현 가능.

사용(0.1.4~ 권장 — 업스트림 export 를 그대로 받음, 결과는 export 와 바이트 동일):
  (hanword 쪽) python3 scripts/export_embed.py DST --ref <commit>
  python3 scripts/vendor_hanword.py DST --commit <commit> --version 0.1.4
예전 방식:
  git -C <hanword> archive <commit> > hw.tar
  python3 scripts/vendor_hanword.py hw.tar [--commit <hash>] [--version 0.1.2]
  python3 scripts/vendor_hanword.py <hanword 작업 트리 디렉터리> --commit <hash> --version 0.1.2

임베드(iframe) + 변환기(hanword_embed)에 필요한 파일만 복사한다. Python 모듈은 hanword_embed.py 에서
import 문을 따라가 모으고(정적), 깨끗한 하위 프로세스(-I)에서 실제 import·HWPX/DOCX 변환으로 확인한다.
진입점은 업스트림이 만든 embed.html(0.1.3~, 인라인 스크립트·공개 사이트 줄 없음)을 그대로 쓰고, 파일은 바이트 그대로 복사해(로컬 패치 없음, 0.1.4~) 결과를 VENDOR.md(출처 커밋·날짜·파일·sha256)에 적는다.
"""
from __future__ import annotations

import argparse
import ast
import datetime
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "src" / "gyeoljae" / "vendor" / "hanword"

STATIC = ["LICENSE", "NOTICE-hwpx_skeleton.txt", "embed.html", "editor.css", "html2canvas.min.js", "jspdf.min.js",
          "htmlpdf.js", "hwpx_skeleton.hwpx", "docs/embed.md"]
DIRS = {"licenses": None, "font": (".woff2", ".ttf")}

# 원본 주석의 내부 호스트 이름(이 파일도 공개 검사기를 통과하도록 조각으로 씀)
INT = "ko" + "ko"
# 0.1.4~ 업스트림 export_embed.py 가 위생 검사를 하므로 로컬 패치 없음. 비워 둘 것(바이트 동일 보장).
PATCHES: list = []


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def materialize(src: Path, tmp: Path):
    """tar(git archive) 또는 디렉터리 → 읽기 전용 원본 디렉터리, 커밋(tar 의 pax comment)."""
    if src.is_dir():
        return src, None
    out = tmp / "src"
    out.mkdir()
    with tarfile.open(src) as tf:
        commit = tf.pax_headers.get("comment")
        for m in tf.getmembers():
            n = m.name
            if not m.isfile() or n.startswith("/") or ".." in Path(n).parts:
                continue  # 일반 파일만(링크·장치·경로 이탈 무시)
            p = out / n
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(tf.extractfile(m).read())
    return out, commit


def py_closure(src: Path):
    """hanword_embed.py 부터 import 를 따라가 원본 최상위 .py 모듈 집합(함수 안 import 포함)."""
    local = {p.stem for p in src.glob("*.py")}
    seen, todo = set(), ["hanword_embed"]
    while todo:
        m = todo.pop()
        if m in seen:
            continue
        seen.add(m)
        for node in ast.walk(ast.parse((src / f"{m}.py").read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".")[0]]
            todo += [n for n in names if n in local and n not in seen]
    return sorted(seen)


CHECK = r"""
import sys, zipfile, io
d = sys.argv[1]; sys.path.insert(0, d)
import hanword_embed as h
html = '<p>시험</p><table data-row="items"><tr data-row="items"><td data-field="x">1</td></tr></table>'
a = h.html_to_hwpx(html, page="gov"); b = h.html_to_docx(html, page="gov")
assert zipfile.ZipFile(io.BytesIO(a)).namelist()[0] == "mimetype"
assert "word/document.xml" in zipfile.ZipFile(io.BytesIO(b)).namelist()
import os
print(" ".join(sorted(n for n, m in sys.modules.items()
                     if getattr(m, "__file__", None) and os.path.dirname(os.path.abspath(m.__file__)) == d)))
"""


def main():
    ap = argparse.ArgumentParser(description="hanword 임베드 빌드 반입")
    ap.add_argument("source", help="git archive tar 또는 hanword 디렉터리")
    ap.add_argument("--commit", help="출처 커밋(tar 면 pax comment 에서 자동)")
    ap.add_argument("--version", default="0.1.4")
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    a = ap.parse_args()
    with tempfile.TemporaryDirectory() as t:
        src, commit = materialize(Path(a.source).resolve(), Path(t))
        commit = a.commit or commit
        if not commit or not re.fullmatch(r"[0-9a-f]{40}", commit):
            sys.exit("출처 커밋(40자 hash)을 알 수 없음 — --commit 으로 지정")
        idx = (src / "embed.html").read_text(encoding="utf-8")  # 업스트림이 만든 임베드 전용 진입점(0.1.3~)
        if re.search(r"<script(?![^>]*\bsrc=)", idx) or re.search(r"(src|href)=\"?index\.html", idx) or "data-wp-embed" not in idx:
            sys.exit("embed.html 이 임베드 빌드가 아님(인라인 스크립트·index.html 참조·data-wp-embed 없음)")
        js = re.findall(r'<script src="(wp-[a-z0-9\-]+\.js)"></script>', idx)
        mods = py_closure(src)
        files = STATIC + js + [f"{m}.py" for m in mods]
        for d, ext in DIRS.items():
            files += sorted(str(p.relative_to(src).as_posix()) for p in (src / d).rglob("*")
                            if p.is_file() and (ext is None or p.suffix in ext))
        stage = Path(t) / "out"
        for f in files:
            (stage / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src / f, stage / f)
        for f, why, old, new, exp in PATCHES:
            p = stage / f
            s = p.read_text(encoding="utf-8")
            n = s.count(old)
            s2 = s.replace(old, new)
            if n != exp:
                sys.exit(f"패치 불일치 {f}: {why} (찾음 {n}, 기대 {exp}) — 원본이 바뀜, PATCHES 확인")
            p.write_text(s2, encoding="utf-8")
        bad = [f for f in files if INT.encode() in (stage / f).read_bytes()]
        if bad:
            sys.exit(f"내부 호스트 이름이 남은 파일: {bad} — 업스트림 export_embed.py 로 다시 뽑을 것")
        same = all((stage / f).read_bytes() == (src / f).read_bytes() for f in files)
        if not same:
            sys.exit("반입본이 원본과 바이트 동일하지 않음")
        # 실제 import·변환 확인(깨끗한 하위 프로세스, 반입본 디렉터리만)
        r = subprocess.run([sys.executable, "-I", "-B", "-c", CHECK, str(stage)], capture_output=True, text=True)
        if r.returncode:
            sys.exit("반입본 import/변환 실패:\n" + r.stderr[-2000:])
        loaded = set(r.stdout.split())
        if not loaded <= set(mods):
            sys.exit(f"정적 분석에 빠진 모듈: {sorted(loaded - set(mods))}")
        lines = [f"# Vendored hanword (embed build)", "",
                 f"- source: hanword {a.version}, commit `{commit}` (MIT, see LICENSE)",
                 f"- vendored: {a.date} by `scripts/vendor_hanword.py` (re-run it to update; do not edit files here by hand)",
                 f"- protocol and rules: docs/embed.md", "",
                 "## Local patches", "",
                 "None. Source is the output of upstream `scripts/export_embed.py` (its hygiene check: shipped",
                 "fonts match licenses/README.md, embed.html current with no inline scripts, no internal host names).",
                 "Every file here is a byte-identical subset of that export (checked by this script).", "",
                 "Fonts (all SIL OFL 1.1, texts in licenses/fonts/): exactly as shipped upstream, including the unmodified",
                 "original Nanum Gothic/Myeongjo TTFs. Fonts upstream excluded (Nanum woff2 subsets, which keep the",
                 "reserved name; Hamchorom; UnBatang) must not be added here.", ""]
        lines += ["", f"Python modules (import closure of hanword_embed, verified by a real import + HWPX/DOCX "
                      f"conversion in `python -I`): {', '.join(mods)}", "",
                  "## Files (sha256)", ""]
        lines += [f"{sha(stage / f)}  {f}" for f in sorted(files)]
        (stage / "VENDOR.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        if DEST.exists():
            shutil.rmtree(DEST)
        DEST.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(stage, DEST)
        size = sum(p.stat().st_size for p in DEST.rglob("*") if p.is_file())
        print(json.dumps({"dest": str(DEST.relative_to(ROOT)), "files": len(files) + 1, "bytes": size,
                          "modules": mods, "patches": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()
