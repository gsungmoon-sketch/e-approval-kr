"""반입한 hanword 편집기(vendor/hanword) 연결 — 정본 HTML 정화·용지 검증·HWPX/DOCX 변환.

hanword_embed 는 표준 라이브러리만 쓰며, 자기 폴더를 sys.path 에 넣고 형제 모듈(hanword_core 등)을 import 한다.
문서 HTML 의 data-field/data-row/data-sum 은 이름(목록)일 뿐 — 여기서도 doctemplate 에서도 평가하지 않는다.
"""
from __future__ import annotations

import functools
import hashlib
import json
import os
import re
import secrets
import shutil
import sys
import threading
from pathlib import Path, PurePath, PurePosixPath

from . import pkgfiles

VENDOR = Path(__file__).resolve().parent / "vendor" / "hanword"
VENDOR_BASE = "vendor/hanword"   # 패키지 기준(pkgfiles)
GOV = "gov"  # 공문서 A4: 위30·왼20·오15·아래15 mm (우리 출력 용지와 같음)
MAX_PAGE_JSON = 1000


def vendor_key(path: PurePath, base: PurePath = VENDOR) -> str:
    """반입 파일 경로 → VENDOR.md·URL 에 쓰는 키('font/x.woff2'). OS 구분자(Windows '\\')와 무관하게 '/'."""
    return path.relative_to(base).as_posix()


def available() -> bool:
    if pkgfiles.on_disk():
        return (VENDOR / "hanword_embed.py").is_file() and (VENDOR / "embed.html").is_file()
    return all(pkgfiles.find(VENDOR_BASE, n) is not None for n in ("hanword_embed.py", "embed.html"))


# ------------------------------------------------------------ .pyz: 변환기용 반입본을 데이터 폴더 캐시로 1회 풀기
# hanword 변환기는 자기 __file__ 기준으로 hwpx_skeleton.hwpx 등을 open() 하므로 zip 안에서는 못 읽는다.
# 그래서 .pyz 로 돌 때만 반입 파일(글꼴 제외)을 <데이터>/cache/hanword-<VENDOR.md 해시>/ 에 그대로 복사해 쓴다.
# 쓰기 전·다시 쓸 때마다 VENDOR.md 의 sha256 과 대조한다(반입본 파일은 바꾸지 않음, 바이트 그대로).
_cache_base = None
_extract_lock = threading.Lock()
_KEY_PART = re.compile(r"[A-Za-z0-9_\-]+(?:\.[A-Za-z0-9_\-]+)*")
_MANIFEST_LINE = re.compile(r"^([0-9a-f]{64})  (\S+)$")


def set_cache_dir(path) -> None:
    """.pyz 일 때 반입본을 풀 폴더(App 이 <데이터>/cache 로 지정). 폴더 설치에서는 쓰지 않음."""
    global _cache_base
    _cache_base = Path(path)


def vendor_manifest(text: str) -> dict:
    """VENDOR.md '## Files (sha256)' 표 → {키('font/x.woff2'): sha256}. 이상한 키는 ValueError."""
    out, on = {}, False
    for line in text.splitlines():
        if line.startswith("## "):
            on = line.strip() == "## Files (sha256)"
            continue
        m = _MANIFEST_LINE.match(line.strip()) if on else None
        if m:
            key = m.group(2)
            parts = PurePosixPath(key).parts
            if not parts or key.startswith("/") or "\\" in key or ":" in key or \
                    not all(_KEY_PART.fullmatch(x) for x in parts):
                raise ValueError(f"VENDOR.md 키 형식 오류: {key!r}")
            out[key] = m.group(1)
    if not out:
        raise ValueError("VENDOR.md 에 파일 목록이 없습니다")
    return out


def _cache_ok(d: Path, man: dict) -> bool:
    try:
        return all(hashlib.sha256(d.joinpath(*PurePosixPath(k).parts).read_bytes()).hexdigest() == h
                   for k, h in man.items())
    except OSError:
        return False


def _extract_vendor() -> Path:
    from importlib.resources import files
    src = files(__package__).joinpath("vendor").joinpath("hanword")
    md = src.joinpath("VENDOR.md").read_bytes()
    man = {k: h for k, h in vendor_manifest(md.decode("utf-8")).items() if not k.startswith("font/")}
    if _cache_base is None:
        from .core_base import default_data_dir
        base = default_data_dir() / "cache"
    else:
        base = _cache_base
    dest = base / ("hanword-" + hashlib.sha256(md).hexdigest()[:16])
    with _extract_lock:
        if _cache_ok(dest, man):
            return dest
        base.mkdir(parents=True, exist_ok=True)
        tmp = base / (".tmp-" + dest.name + "-" + secrets.token_hex(4))
        tmp.mkdir(mode=0o700)
        try:
            for k, h in man.items():
                t = src
                for part in PurePosixPath(k).parts:
                    t = t.joinpath(part)
                data = t.read_bytes()
                if hashlib.sha256(data).hexdigest() != h:
                    raise RuntimeError(f"반입한 hanword 파일이 VENDOR.md 와 다릅니다: {k}")
                fp = tmp.joinpath(*PurePosixPath(k).parts)
                fp.parent.mkdir(parents=True, exist_ok=True)
                fp.write_bytes(data)
            (tmp / "VENDOR.md").write_bytes(md)
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True)
            try:
                os.replace(tmp, dest)
            except OSError:  # 다른 프로세스가 먼저 만든 경우
                if not _cache_ok(dest, man):
                    raise
        finally:
            if tmp.exists():
                shutil.rmtree(tmp, ignore_errors=True)
        if not _cache_ok(dest, man):
            raise RuntimeError("hanword 캐시 검증 실패")
        return dest


def vendor_dir() -> Path:
    """변환기(hanword_embed)를 import 할 폴더: 폴더 설치는 반입 폴더 그대로, .pyz 는 검증된 캐시."""
    return VENDOR if pkgfiles.on_disk() else _extract_vendor()


# ------------------------------------------------------------ 변환기 import 를 안전하게
# 예전에는 반입 폴더(.pyz 면 데이터 폴더 캐시)를 sys.path 맨 앞에 넣었다. 그 폴더에 누가 json.py·hwpx.py 같은
# 파일이나 __pycache__/*.pyc 를 심으면 표준 라이브러리보다 먼저 실행된다. 이제는
#  1) 반입 모듈(VENDOR.md 의 최상위 *.py)만 전용 finder 로, VENDOR.md sha256 과 맞는 소스 바이트에서 직접 컴파일(.pyc 안 씀)
#  2) 폴더는 sys.path 맨 뒤에 붙인다(반입 모듈의 'if APP not in sys.path: insert(0)' 도 그래서 건너뜀).
#     단 그 폴더의 path entry finder 는 _NoPathEntry 로 고정 — 경로 기반 import 는 거기서 아무것도 못 찾는다
#  3) .pyz 캐시는 목록에 없는 항목이 있거나, POSIX 에서 남의 소유·전체(other) 쓰기 가능이면 거절
class _VendorFinder:
    """VENDOR.md 에 적힌 최상위 모듈만 맡는 meta path finder. 소스는 해시 대조 뒤 compile, 바이트코드 캐시 무시."""

    def __init__(self, d: Path, mods: dict):
        self.dir, self.mods = d, mods  # {모듈 이름: sha256}

    def find_spec(self, name, path=None, target=None):
        if path is not None or name not in self.mods:
            return None
        import importlib.util
        return importlib.util.spec_from_file_location(name, str(self.dir / (name + ".py")), loader=self)

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        name = module.__spec__.name
        src = (self.dir / (name + ".py")).read_bytes()
        if hashlib.sha256(src).hexdigest() != self.mods[name]:
            raise ImportError(f"hanword 모듈이 VENDOR.md 와 다릅니다: {name}")
        exec(compile(src, module.__spec__.origin, "exec", dont_inherit=True), module.__dict__)


class _NoPathEntry:
    """sys.path_importer_cache 에 반입 폴더용으로 넣는 '아무것도 못 찾는' path entry finder.
    반입 코드의 'if APP not in sys.path' 를 만족시키려고 폴더를 sys.path 에 두지만, 경로 기반 import
    (예: 반입 코드의 선택적 'from hwpx import'·'from lxml import', 앱 다른 곳의 try-import)가
    그 폴더에서 목록 밖 파일을 찾는 일은 없게 한다. 검사 뒤에 심은 파일(TOCTOU)·Windows(권한 검사 생략)도 마찬가지.
    반입 모듈 자체는 meta path 의 _VendorFinder 가 해시 대조 뒤 적재한다."""

    def find_spec(self, name, target=None):
        return None

    def invalidate_caches(self):
        pass


def _check_cache_dir(d: Path, man: dict) -> None:
    """.pyz 캐시 폴더 검사: 목록 밖 항목 금지, POSIX 는 소유자=나 그리고 전체(other) 쓰기 없음(폴더와 상위).
    그룹 쓰기는 umask 002(사용자 전용 그룹) 환경의 정상값이라 허용 — 새로 풀 때는 0700 으로 만든다."""
    allowed = {PurePosixPath(k).parts[0] for k in man} | {"VENDOR.md", "__pycache__"}
    extra = sorted(x.name for x in d.iterdir() if x.name not in allowed)
    if extra:
        raise RuntimeError(f"hanword 캐시에 모르는 파일이 있습니다: {extra[:5]}")
    getuid = getattr(os, "getuid", None)
    if getuid is None:  # Windows: 사용자 프로필 아래 데이터 폴더, ACL 은 OS 몫
        return
    for x in (d, d.parent):
        st = x.stat()
        if st.st_uid != getuid() or st.st_mode & 0o002:
            raise RuntimeError(f"hanword 캐시 폴더 권한이 안전하지 않습니다(소유자·쓰기 권한): {x}")


def _trusted_manifest() -> dict:
    """VENDOR.md 는 캐시 사본이 아니라 패키지(폴더 또는 .pyz 안)에서 읽는다."""
    if pkgfiles.on_disk():
        return vendor_manifest((VENDOR / "VENDOR.md").read_text(encoding="utf-8"))
    from importlib.resources import files
    return vendor_manifest(files(__package__).joinpath("vendor").joinpath("hanword")
                           .joinpath("VENDOR.md").read_bytes().decode("utf-8"))


@functools.lru_cache(maxsize=1)
def embed():
    if not available():
        raise RuntimeError("hanword 편집기가 반입되지 않았습니다(scripts/vendor_hanword.py)")
    d = vendor_dir()
    man = _trusted_manifest()
    if not pkgfiles.on_disk():
        _check_cache_dir(d, {k: h for k, h in man.items() if not k.startswith("font/")})
    mods = {k[:-3]: h for k, h in man.items() if "/" not in k and k.endswith(".py")}
    if not any(isinstance(f, _VendorFinder) for f in sys.meta_path):
        sys.meta_path.insert(0, _VendorFinder(d, mods))
    p = str(d)
    if not isinstance(sys.path_importer_cache.get(p), _NoPathEntry):
        sys.path_importer_cache[p] = _NoPathEntry()  # 이 폴더에서 경로 기반 import 금지(반입 모듈은 위 finder 만)
    if p not in sys.path:
        sys.path.append(p)  # 맨 뒤: 표준 라이브러리·설치 패키지가 늘 먼저
    import hanword_embed  # noqa: E402  (_VendorFinder 가 검증된 소스로 적재)
    return hanword_embed


def canonical_html(html, limit=True) -> str:
    """편집기/외부 HTML → 저장할 정본: hanword sanitize_html(편집기와 같은 규칙) 다음 우리 clean_html(같은 허용 목록,
    사진 뺀 글 2MB·사진 포함 8MB·중첩 제한). 두 번째 통과는 hanword 출력을 바꾸지 않는다(시험으로 확인).
    limit=False: 크기로 거절된 입력을 그 사용자에게 다시 보여 줄 때만(정화는 같음, 저장 금지)."""
    from .sanitize import check_size, clean_html
    s = "" if html is None else str(html)
    if limit:
        check_size(s)  # hanword 정화 전에 먼저(큰 입력을 두 번 파싱하지 않게)
    if available():
        s = embed().sanitize_html(s)
    return clean_html(s, limit=limit)


def parse_page(raw):
    """편집기 page(JSON 문자열 또는 dict/"gov") → 검증된 dict 또는 None(없음). 잘못된 값은 ValueError."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, str):
        if len(raw) > MAX_PAGE_JSON:
            raise ValueError("용지 설정이 너무 깁니다")
        try:
            raw = json.loads(raw) if raw.strip()[:1] in "{\"" else raw.strip()
        except ValueError:
            raise ValueError("용지 설정 형식이 잘못되었습니다")
    if not isinstance(raw, (dict, str)) or (isinstance(raw, str) and raw not in embed().MARGIN_PRESETS):
        raise ValueError("용지 설정 형식이 잘못되었습니다")
    if isinstance(raw, dict) and (len(raw) > 8 or not all(isinstance(k, str) for k in raw)):
        raise ValueError("용지 설정 형식이 잘못되었습니다")
    pg = embed().normalize_page(raw)
    if pg is None:
        raise ValueError("용지 설정 형식이 잘못되었습니다")
    return pg


def export(html, fmt, page=None) -> bytes:
    """정본 HTML → .hwpx/.docx 바이트(hanword 변환기, 표준 라이브러리만). 잘못된 입력은 InputError(ValueError)."""
    e = embed()
    if fmt == "hwpx":
        return e.html_to_hwpx(html, page=page or GOV)
    if fmt == "docx":
        return e.html_to_docx(html, page=page or GOV)
    raise ValueError("지원하지 않는 형식")
