"""반입한 hanword 편집기(vendor/hanword) 연결 — 정본 HTML 정화·용지 검증·HWPX/DOCX 변환.

hanword_embed 는 표준 라이브러리만 쓰며, 자기 폴더를 sys.path 에 넣고 형제 모듈(hanword_core 등)을 import 한다.
문서 HTML 의 data-field/data-row/data-sum 은 이름(목록)일 뿐 — 여기서도 doctemplate 에서도 평가하지 않는다.
"""
from __future__ import annotations

import functools
import json
import sys
from pathlib import Path

VENDOR = Path(__file__).resolve().parent / "vendor" / "hanword"
GOV = "gov"  # 공문서 A4: 위30·왼20·오15·아래15 mm (우리 출력 용지와 같음)
MAX_PAGE_JSON = 1000


def available() -> bool:
    return (VENDOR / "hanword_embed.py").is_file() and (VENDOR / "embed.html").is_file()


@functools.lru_cache(maxsize=1)
def embed():
    if not available():
        raise RuntimeError("hanword 편집기가 반입되지 않았습니다(scripts/vendor_hanword.py)")
    p = str(VENDOR)
    if p not in sys.path:
        sys.path.insert(0, p)
    import hanword_embed  # noqa: E402  (vendor 폴더)
    return hanword_embed


def canonical_html(html) -> str:
    """편집기/외부 HTML → 저장할 정본: hanword sanitize_html(편집기와 같은 규칙) 다음 우리 clean_html(같은 허용 목록,
    2MB·중첩 제한). 두 번째 통과는 hanword 출력을 바꾸지 않는다(시험으로 확인)."""
    from .sanitize import clean_html
    s = "" if html is None else str(html)
    if available():
        s = embed().sanitize_html(s)
    return clean_html(s)


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
