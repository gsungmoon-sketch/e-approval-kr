"""반입한 hanword 편집기(vendor/hanword) 연결 — 정본 HTML 정화·용지 검증·HWPX/DOCX 변환.

hanword_embed 는 표준 라이브러리만 쓰며, 자기 폴더를 sys.path 에 넣고 형제 모듈(hanword_core 등)을 import 한다.
문서 HTML 의 data-field/data-row/data-sum 은 이름(목록)일 뿐 — 여기서도 doctemplate 에서도 평가하지 않는다.
"""
from __future__ import annotations

import hashlib
import io
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
_embed_lock = threading.RLock()   # embed() 1회 초기화·set_cache_dir 를 직렬화
_embed_mod = None                  # 적재된 hanword_embed
_loaded_base = None                # .pyz: 변환기를 적재한 캐시의 상위 폴더(set_cache_dir 변경 거절용)
_KEY_PART = re.compile(r"[A-Za-z0-9_\-]+(?:\.[A-Za-z0-9_\-]+)*")
_MANIFEST_LINE = re.compile(r"^([0-9a-f]{64})  (\S+)$")


def set_cache_dir(path) -> None:
    """.pyz 일 때 반입본을 풀 폴더(App 이 <데이터>/cache 로 지정). 폴더 설치에서는 쓰지 않음.
    변환기를 이미 어떤 캐시에서 적재했다면 다른 폴더로 바꾸지 않는다(RuntimeError): 적재된 모듈·finder 는
    그 폴더에 묶여 있어서, 바꾸면 '설정은 새 폴더, 실제 읽기는 옛 폴더'가 된다. 같은 폴더 재지정은 그대로 둔다.
    즉 .pyz 프로세스 하나는 데이터 폴더 하나(변환 뒤 다른 데이터 폴더로 App 을 만들면 거절). 지금은 복원(restore)도
    새 CLI 프로세스라 해당 없음."""
    global _cache_base
    p = Path(path)
    with _embed_lock:
        if _loaded_base is not None and _same_dir(p, _loaded_base) is False:
            raise RuntimeError(f"hanword 변환기가 이미 다른 캐시 폴더에서 적재되었습니다: {_loaded_base}")
        _cache_base = p


def _same_dir(a: Path, b: Path):
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return a == b


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
            _try_private(dest)  # 0.2.5 이전에 푼 캐시도 나만 쓰게(내용은 위에서 해시로 확인)
            return dest
        base.mkdir(parents=True, exist_ok=True)
        _try_private(base)
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
                fp.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                fp.write_bytes(data)
                _try_private(fp, 0o600)
            (tmp / "VENDOR.md").write_bytes(md)
            _try_private(tmp / "VENDOR.md", 0o600)
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


def _try_private(p: Path, mode: int = 0o700) -> None:
    """우리가 만든 캐시 폴더·파일을 나만 쓰게(best effort; Windows·권한 없는 FS 는 그냥 둠)."""
    try:
        os.chmod(p, mode)
    except OSError:
        pass


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
    """.pyz 캐시 폴더 검사(심층 방어): 목록 밖 항목 금지, POSIX 는 소유자=나 그리고 전체(other) 쓰기 없음(폴더와 상위).
    그룹 쓰기는 umask 002(사용자 전용 그룹) 환경의 정상값이라 허용 — 새로 풀 때는 0700/0600 으로 만든다.

    상위 폴더를 신뢰 루트('/')까지 거슬러 검사하지 않는 이유: 무결성은 경로·권한이 아니라 내용 해시로 지킨다.
    실행 중 캐시에서 읽는 바이트는 (1) 반입 .py — _VendorFinder 가 한 번 읽은 바이트를 해시 대조 뒤 compile,
    (2) 데이터 파일 — hwpx_skeleton.hwpx 는 embed() 가 해시 대조한 바이트를 메모리에 두고 변환기가 그걸 연다
    (_DATA_FILES), 둘뿐이다(글꼴·편집기 정적 파일은 캐시가 아니라 패키지에서 pkgfiles 로 낸다).
    범위: gyeoljae 가 부르는 sanitize_html·normalize_page·MARGIN_PRESETS·html_to_hwpx·html_to_docx 기준이다.
    반입본의 html_to_pdf(APP/htmlpdf.js·APP/font)는 검증 없이 경로로 읽으므로 쓰지 않는다(export 는 hwpx/docx 만).
    그래서 조상 폴더를 남이 바꿔(이름 바꾸기·심볼릭 링크) 캐시 경로를 가로채도 검증 안 된 바이트는 실행·사용되지 않고
    최악이 변환 실패(서비스 거부)다. 조상 검사는 그 위에 TOCTOU 를 막지 못하면서 umask 002 홈(~/.local 0775 등)을
    거절하게 되므로 하지 않는다."""
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


class _VerifiedBlob:
    """해시 대조를 마친 데이터 파일 바이트를 읽기 전용 파일처럼. zipfile.ZipFile(hwpx_std.SKELETON) 이 경로 대신
    이걸 열게 한다 — 그러면 변환 때마다 디스크(캐시)를 다시 읽지 않아 검사 뒤 바꿔치기(TOCTOU)가 소용없다.
    위치(seek)는 스레드마다 따로(동시 변환), 같은 스레드에서는 ZipFile 이 읽기마다 seek 하므로 공유해도 된다."""

    def __init__(self, data: bytes, name: str):
        self._data, self.name, self._tl = bytes(data), name, threading.local()

    def _f(self):
        f = getattr(self._tl, "f", None)
        if f is None:
            f = self._tl.f = io.BytesIO(self._data)
        return f

    def seek(self, *a):
        return self._f().seek(*a)

    def tell(self):
        return self._f().tell()

    def read(self, *a):
        return self._f().read(*a)

    def seekable(self):
        return True


# 반입 변환기가 자기 폴더에서 경로로 여는 데이터 파일: (모듈, 속성) → VENDOR.md 키. 적재 때 해시 대조한 바이트로 바꾼다.
_DATA_FILES = {("hwpx_std", "SKELETON"): "hwpx_skeleton.hwpx"}


def _pin_data_files(d: Path, man: dict) -> None:
    import importlib
    for (mod, attr), key in _DATA_FILES.items():
        m = importlib.import_module(mod)  # _VendorFinder 가 검증된 소스로 적재
        cur = getattr(m, attr)
        if isinstance(cur, _VerifiedBlob):
            continue
        if Path(cur) != d.joinpath(*PurePosixPath(key).parts):
            raise RuntimeError(f"hanword {mod}.{attr} 경로가 예상과 다릅니다(반입본 갱신 시 _DATA_FILES 확인)")
        data = d.joinpath(*PurePosixPath(key).parts).read_bytes()
        if hashlib.sha256(data).hexdigest() != man.get(key):
            raise RuntimeError(f"반입한 hanword 데이터 파일이 VENDOR.md 와 다릅니다: {key}")
        setattr(m, attr, _VerifiedBlob(data, key))


def embed():
    """검증된 hanword_embed 모듈(프로세스당 1회 적재). 동시 첫 호출도 _embed_lock 으로 한 번만 초기화 —
    finder 중복 등록·캐시 동시 풀기 없음."""
    m = _embed_mod
    if m is not None:
        return m
    with _embed_lock:
        return _embed_mod if _embed_mod is not None else _load_embed()


def _load_embed():
    global _embed_mod, _loaded_base
    if not available():
        raise RuntimeError("hanword 편집기가 반입되지 않았습니다(scripts/vendor_hanword.py)")
    d = vendor_dir()
    if not pkgfiles.on_disk():
        _loaded_base = d.parent  # import 전에 고정: 도중 실패해도 sys.modules 에 남은 모듈이 이 폴더 것이므로
    man = _trusted_manifest()
    if not pkgfiles.on_disk():
        _check_cache_dir(d, {k: h for k, h in man.items() if not k.startswith("font/")})
    mods = {k[:-3]: h for k, h in man.items() if "/" not in k and k.endswith(".py")}
    # finder 는 늘 하나, 지금 폴더(d)용. 예전 것(다른 폴더)이 남아 있으면 뺀다.
    sys.meta_path[:] = [f for f in sys.meta_path if not isinstance(f, _VendorFinder)]
    sys.meta_path.insert(0, _VendorFinder(d, mods))
    p = str(d)
    if not isinstance(sys.path_importer_cache.get(p), _NoPathEntry):
        sys.path_importer_cache[p] = _NoPathEntry()  # 이 폴더에서 경로 기반 import 금지(반입 모듈은 위 finder 만)
    if p not in sys.path:
        sys.path.append(p)  # 맨 뒤: 표준 라이브러리·설치 패키지가 늘 먼저
    import hanword_embed  # noqa: E402  (_VendorFinder 가 검증된 소스로 적재)
    _pin_data_files(d, man)
    _embed_mod = hanword_embed
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
