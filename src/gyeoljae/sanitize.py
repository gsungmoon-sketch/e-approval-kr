"""편집기(웹 워드프로세서) 본문 HTML 정화 — 허용 목록 방식, 표준 라이브러리 html.parser 만.

clean_html(s) → 안전한 HTML 문자열. 허용 안 된 태그는 버리고 글자는 남긴다. script·style·iframe·object·embed·form·
input·svg·math 등은 내용까지 통째로 버린다. 속성도 허용 목록, style 은 CSS 속성 허용 목록으로 다시 쓴다.
허용 목록은 hanword 편집기(vendor/hanword/docs/embed.md, hanword_embed.sanitize_html)와 같거나 넓다 — 정본은
hanword.canonical_html(= hanword 정화 → 이 정화)이고, 이 두 번째 통과가 hanword 출력을 바꾸지 않아야 한다.
img src 는 data:image/(png|jpeg|gif|webp|bmp);base64 만(같은 출처 URL 도 금지). a href 는 http(s)/mailto/# 만.
data-field/data-row/data-sum 은 td·th·tr·table·span 에만, 이름(목록)으로만 — 평가하지 않음. data-formula(표 계산식,
hanword 0.1.10~)는 td·th 에만, hanword 와 같은 고정 문법 정규식(FORMULA_RE)으로만 — 서버는 해석·계산하지 않고 칸 글자(편집기가
계산해 넣은 값)를 그대로 저장한다. 크기는 사진(img src data:) 뺀 글 2MB·사진 포함 8MB(정화 결과 기준)·중첩 64단 제한.
"""
from __future__ import annotations

import html
import re
from html.parser import HTMLParser

MAX_SIZE = 2 * 1024 * 1024        # 사진 없는 본문
MAX_SIZE_IMG = 8 * 1024 * 1024    # 사진(data:image) 이 든 본문 — 편집기 화면이 사진을 1600px·JPEG 로 줄여 보냄
_HAS_IMG = re.compile(r"<img\b[^>]*?\bsrc\s*=\s*[\"']?\s*data:image/", re.I)
MAX_DEPTH = 64

ALLOWED = {"p", "div", "span", "br", "hr", "b", "strong", "i", "em", "u", "s", "strike", "del", "sub", "sup",
           "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "table", "thead", "tbody", "tfoot", "tr", "td", "th",
           "caption", "colgroup", "col", "a", "img", "blockquote", "pre", "code", "font"}
VOID = {"br", "hr", "img", "col"}
# 내용까지 버림(스크립트·외부 개체·폼·벡터/수식, 그리고 원문 텍스트로 해석되는 요소)
DROP = {"script", "style", "iframe", "object", "embed", "form", "input", "svg", "math", "frame", "frameset",
        "noscript", "noembed", "noframes", "template", "textarea", "select", "option", "optgroup", "datalist",
        "output", "button", "title", "xmp", "plaintext", "listing", "applet", "audio", "video", "source", "track",
        "canvas", "map", "area", "dialog", "slot", "portal", "marquee", "head", "meta", "link", "base"}
DROP_VOID = {"input", "embed", "meta", "link", "base", "source", "track", "area", "frame"}  # 브라우저 void: 짝 닫는 태그를 기다리지 않음

COMMON = {"style", "class", "title", "align"}
PER_TAG = {"td": {"colspan", "rowspan", "valign", "width", "height"}, "th": {"colspan", "rowspan", "valign", "width", "height"},
           "tr": {"valign", "height"}, "table": {"border", "cellpadding", "cellspacing", "width"},
           "col": {"span", "width"}, "colgroup": {"span", "width"}, "img": {"src", "alt", "width", "height"},
           "a": {"href", "rel"}, "font": {"face", "size", "color"}, "ol": {"start", "type"}, "li": {"value"}}
DATA_ATTRS = {"data-field", "data-row", "data-sum"}
DATA_TAGS = {"td", "th", "tr", "table", "span"}
_DV = r"[A-Za-z0-9_.\-\u3131-\u318E\uAC00-\uD7A3]{1,64}"
DATA_RE = re.compile(_DV + r"(?:," + _DV + r"){0,15}")  # 이름 또는 쉼표 이름 목록(식 아님)
# 표 계산식 — hanword_embed.FORMULA_VAL·wp-embed.js FORMULA_VAL 과 같은 정규식(fullmatch). 편집기만 해석, 서버는 문자열로만 다룸
FORMULA_RE = re.compile(r"(SUM|AVG)\((ABOVE|BELOW|LEFT|RIGHT|[A-Z]{1,2}[1-9][0-9]{0,3}(:[A-Z]{1,2}[1-9][0-9]{0,3})?)\)")
FORMULA_TAGS = {"td", "th"}
# 편집기 계산 비용 상한(방어 겹: hanword 0.1.11~ 편집기도 자체 예산으로 멈춤 없이 이전 값을 둠).
# 표 하나의 계산식 칸 수(hanword FORMULA_MAX 와 비슷한 기준), 표마다 (계산식 칸 × 그 표의 칸) 합,
# 그리고 문서 전체 뒷받침(서버의 표 구분은 브라우저의 표 재구성과 다를 수 있음 — <table> 안 <table> 등):
# 문서 전체 계산식 칸 수, 문서 전체 계산식 칸 × 전체 칸.
# 넘는 문서는 계산식 속성을 모두 버린다(값 = 칸 글자는 그대로). 계산식 칸이 여러 표에 나뉜 보통 문서는 그대로 둠.
FORMULA_MAX = 300
FORMULA_WORK = 2_000_000
FORMULA_TOTAL = 3000
FORMULA_TOTAL_WORK = 20_000_000
CSS_PROPS = {"text-align", "text-indent", "text-decoration", "text-decoration-line", "text-decoration-style",
             "text-decoration-color", "text-transform", "font", "font-family", "font-weight", "font-style",
             "font-variant", "font-size", "width", "height", "min-width", "max-width", "min-height", "max-height",
             "vertical-align", "background-color", "color", "line-height", "letter-spacing", "word-spacing",
             "white-space", "word-break", "border-collapse", "border-spacing", "table-layout", "list-style-type",
             "page-break-before", "page-break-after", "page-break-inside", "break-before", "break-after",
             "break-inside"}
CSS_PREFIX = ("border", "padding", "margin")
CSS_BAD = re.compile(r"url\s*\(|expression|javascript|vbscript|@import|behavior|-moz-binding|image-set|var\s*\(|"
                     r"attr\s*\(|[\\<>{}@]|/\*|\*/|&", re.I)
CSS_VAL = re.compile(r"^[#\w\s.,%()+\-'\"!/]*$")
CLASS_RE = re.compile(r"^[A-Za-z0-9_\- ]{0,200}$")
SRC_DATA = re.compile(r"^data:image/(png|jpe?g|gif|webp|bmp);base64,[A-Za-z0-9+/=]+$", re.I)
NUM = re.compile(r"\d{1,4}(\.\d{1,2})?(%|px)?")
REL = "noopener noreferrer"


def clean_style(v):
    """style 값 → 허용 속성만 'prop:value;' 로 다시 씀. 위험 토큰이 하나라도 있으면 전체 버림."""
    if not v or CSS_BAD.search(v) or any(ord(ch) < 32 and ch not in "\t\n\r" for ch in v):
        return ""
    out = []
    for decl in v.split(";"):
        if ":" not in decl:
            continue
        p, _, val = decl.partition(":")
        p, val = p.strip().lower(), " ".join(val.split())
        if not val or len(val) > 200 or not (p in CSS_PROPS or (p.startswith(CSS_PREFIX) and re.fullmatch(r"[a-z\-]+", p))):
            continue
        if not CSS_VAL.match(val):
            continue
        out.append(f"{p}:{val}")
    return ";".join(out)


def _clean_attr(tag, k, v):
    v = "" if v is None else v
    if k in DATA_ATTRS:
        return v if tag in DATA_TAGS and DATA_RE.fullmatch(v) else None
    if k == "data-formula":
        return v if tag in FORMULA_TAGS and FORMULA_RE.fullmatch(v) else None
    if k not in COMMON and k not in PER_TAG.get(tag, ()):
        return None
    if k == "style":
        return clean_style(v) or None
    if k == "class":
        return v if CLASS_RE.match(v) else None
    if k == "src":
        v = re.sub(r"[\x00-\x20\x7f-\x9f]+", "", v)
        return v if SRC_DATA.match(v) else None
    if k == "href":
        v = re.sub(r"[\x00-\x20\x7f-\x9f]+", "", v)
        return v if re.match(r"(https?://|mailto:|#)", v, re.I) else None
    if k == "rel":
        return REL if v == REL else None
    if k in ("colspan", "rowspan", "span", "start", "value", "border", "cellpadding", "cellspacing", "size"):
        return v if re.fullmatch(r"\d{1,4}", v) else None
    if k in ("width", "height"):
        return v if NUM.fullmatch(v) else None
    if k in ("align", "valign"):
        return v if re.fullmatch(r"(?i)left|right|center|justify|top|middle|bottom|baseline", v) else None
    if k == "type":
        return v if re.fullmatch(r"[1aAiI]", v) else None
    if k == "color":
        return v if re.fullmatch(r"(?i)#[0-9a-f]{3,8}|[a-z]{3,20}", v) else None
    if k == "face":
        return v if re.fullmatch(r"[^<>\"'`;\\]{1,80}", v) else None
    if k in ("title", "alt"):
        return v[:300]
    return None


class _Cleaner(HTMLParser):
    def __init__(self, formulas=True):
        super().__init__(convert_charrefs=True)
        self.out, self.stack, self.skip = [], [], []
        self.formulas = formulas
        self.tables = [[0, 0]]  # 표마다 [칸 수, 계산식 칸 수] — 0번 = 표 밖 떠돌이 칸
        self.open_tables = []  # 열린 table 의 self.tables 번호(안쪽이 끝)
        self.img_bytes = 0  # 남긴 img src(data:image) 바이트 — 본문 글 크기(2MB)에서 뺌

    def _start(self, tag, attrs, closed):
        if self.skip:
            if tag == "plaintext":  # 건너뛰는 중에도: 파서 판(버전)과 무관하게 끝까지 버림
                self.skip.append("\0plaintext")
            elif tag == self.skip[-1] and tag not in DROP_VOID:  # 브라우저처럼 "/>" 는 무시
                self.skip.append(tag)
            return
        if tag == "plaintext":  # 브라우저: 이후 전부 글자(닫는 태그 없음) → hanword 처럼 끝까지 버림
            self.skip.append("\0plaintext")  # 어떤 닫는 태그와도 안 맞는 표지 — 끝까지 건너뜀
            return
        if tag in DROP:  # 브라우저·hanword 처럼 "/>" 는 무시 — 짝 닫는 태그까지 통째 버림(<title/>…</title> 숨김 글)
            if tag not in DROP_VOID:
                self.skip.append(tag)
            return
        if tag not in ALLOWED:
            return
        if tag not in VOID and len(self.stack) >= MAX_DEPTH:
            return
        kept, seen = [], set()
        for k, v in attrs:
            k = (k or "").lower()
            if k in seen:  # 같은 이름 = 브라우저처럼 첫 것만(첫 값이 틀려 버려져도 뒤 것을 쓰지 않음)
                continue
            seen.add(k)
            if k == "data-formula" and not self.formulas:
                continue
            cv = _clean_attr(tag, k, v)
            if cv is not None:
                kept.append((k, cv))
        if tag in FORMULA_TAGS:
            t = self.tables[self.open_tables[-1] if self.open_tables else 0]
            t[0] += 1
            t[1] += any(k == "data-formula" for k, _ in kept)
        if tag == "img":
            src = next((v for k, v in kept if k == "src"), None)
            if src is None:
                return
            self.img_bytes += len(src)  # SRC_DATA 통과 = ASCII 라 글자 수 = 바이트
        if tag == "a":  # 링크는 새 창에서도 opener 없이(hanword 와 같음), href 없으면 rel 도 없음
            kept = [(k, v) for k, v in kept if k != "rel"]
            if any(k == "href" for k, _ in kept):
                kept.append(("rel", REL))
        a = "".join(f' {k}="{html.escape(v, quote=True)}"' for k, v in kept)
        self.out.append(f"<{tag}{a}>")
        if tag not in VOID:
            self.stack.append(tag)
            if tag == "table":
                self.open_tables.append(len(self.tables))
                self.tables.append([0, 0])

    def formula_heavy(self):
        """계산식이 편집기 계산 상한을 넘는가(표당 칸 수 또는 표마다 계산식 × 칸의 합)."""
        nf = sum(f for _, f in self.tables)
        return (nf > FORMULA_TOTAL or nf * sum(c for c, _ in self.tables) > FORMULA_TOTAL_WORK
                or any(f > FORMULA_MAX for _, f in self.tables)
                or sum(c * f for c, f in self.tables) > FORMULA_WORK)

    def handle_starttag(self, tag, attrs):
        self._start(tag, attrs, False)

    def handle_startendtag(self, tag, attrs):
        self._start(tag, attrs, True)

    def handle_endtag(self, tag):
        if self.skip:
            if tag == self.skip[-1]:
                self.skip.pop()
            return
        if tag in VOID or tag not in self.stack:
            return
        while self.stack:
            t = self.stack.pop()
            self.out.append(f"</{t}>")
            if t == "table" and self.open_tables:
                self.open_tables.pop()
            if t == tag:
                break

    def handle_data(self, data):
        if not self.skip:
            self.out.append(html.escape(data, quote=False))

    def handle_comment(self, data):
        pass

    def handle_decl(self, decl):
        pass

    def handle_pi(self, data):
        pass

    def unknown_decl(self, data):
        pass

    def result(self):
        while self.stack:
            self.out.append(f"</{self.stack.pop()}>")
        return "".join(self.out)


def size_limit(s: str) -> int:
    """이 본문의 크기 상한(바이트): 사진(img data:image)이 있으면 8MB, 없으면 2MB."""
    return MAX_SIZE_IMG if _HAS_IMG.search(s) else MAX_SIZE


def check_size(s: str) -> None:
    """상한을 넘으면 ValueError(사용자에게 그대로 보이는 한국어 안내)."""
    n = len(s.encode("utf-8", "surrogatepass"))
    if n <= MAX_SIZE:
        return
    if size_limit(s) == MAX_SIZE_IMG:
        if n > MAX_SIZE_IMG:
            raise ValueError("본문이 너무 큽니다(사진 포함 최대 8MB). 사진 수나 크기를 줄여 주세요.")
        return
    raise ValueError("본문이 너무 큽니다(사진 없이 최대 2MB). 내용을 여러 문서로 나누어 주세요.")


def clean_html(s, limit=True) -> str:
    """편집기 HTML → 허용 목록만 남긴 안전한 HTML. 크기 상한 초과는 ValueError.
    입력은 check_size(거친 문턱), 결과는 정확히: 사진(남은 img src) 뺀 본문 글 2MB·전체 8MB.
    limit=False 는 거절된 입력을 사용자에게 다시 보여 줄 때만(저장 금지)."""
    s = "" if s is None else str(s)
    if limit:
        check_size(s)
    s = s.replace("\x00", "")
    p = _Cleaner()
    p.feed(s)
    p.close()
    if p.formula_heavy():
        p = _Cleaner(formulas=False)  # 계산식이 너무 많거나 표가 너무 큼 → 계산식만 버리고 다시
        p.feed(s)
        p.close()
    out = p.result()
    if limit:
        n = len(out.encode("utf-8", "surrogatepass"))
        if n - p.img_bytes > MAX_SIZE:
            raise ValueError("본문 글이 너무 큽니다(사진 말고 최대 2MB). 내용을 여러 문서로 나누어 주세요.")
        if n > MAX_SIZE_IMG:
            raise ValueError("본문이 너무 큽니다(사진 포함 최대 8MB). 사진 수나 크기를 줄여 주세요.")
    return out
