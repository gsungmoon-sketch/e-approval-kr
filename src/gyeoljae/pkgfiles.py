"""패키지 안 데이터 파일(편집기·정적 파일) 찾기 — 폴더 설치와 .pyz(zipapp) 모두.

폴더 설치(소스·pip·PyInstaller one-dir): 지금처럼 실제 경로를 resolve 해서 기준 폴더 밖이면 거절.
.pyz: 패키지 폴더가 디스크에 없으므로 importlib.resources(files → zipfile.Path)로 읽는다.
이름은 두 경우 모두 먼저 NAME_RE 로 거른다: '/' 구분 상대 경로만, 점은 이름 안에만('..'·숨김 파일 불가),
역슬래시·드라이브 문자(':')·절대 경로 불가 — 그래서 zip 안에서도 기준 폴더 밖으로 나갈 수 없다.
"""
from __future__ import annotations

import functools
import re
from importlib.resources import files
from pathlib import Path, PurePosixPath

PKG_DIR = Path(__file__).resolve().parent
_SEG = r"[A-Za-z0-9_\-]+(?:\.[A-Za-z0-9_\-]+)*"
NAME_RE = re.compile(rf"(?:{_SEG}/)*{_SEG}\.[a-z0-9]{{1,5}}")


def on_disk() -> bool:
    """패키지가 실제 폴더에 있으면 True(.pyz 안이면 False)."""
    return PKG_DIR.is_dir()


@functools.lru_cache(maxsize=1)
def _root():
    return files(__package__)


def safe_name(name) -> bool:
    return (isinstance(name, str) and bool(NAME_RE.fullmatch(name)) and "\\" not in name and ":" not in name
            and ".." not in name and not name.startswith("/"))


def find(base: str, name: str):
    """기준 폴더 base('static', 'vendor/hanword' — 패키지 기준 '/' 경로) 아래 파일 name.
    있으면 read_bytes()·name 이 있는 객체(Path 또는 Traversable), 없거나 이름이 안전하지 않으면 None."""
    if not safe_name(name):
        return None
    if on_disk():
        b = PKG_DIR.joinpath(*base.split("/")).resolve()
        fp = (b / name).resolve()
        return fp if b in fp.parents and fp.is_file() else None
    t = _root()
    for part in PurePosixPath(base, name).parts:
        t = t.joinpath(part)
    try:
        return t if t.is_file() else None
    except (OSError, KeyError, ValueError):
        return None
