"""편집기(웹 워드프로세서) 본문 HTML 정화 — 허용 목록 방식, 표준 라이브러리 html.parser 만.

clean_html(s) → 안전한 HTML 문자열. 허용 안 된 태그는 버리고 글자는 남긴다. script·style·iframe·object·embed·form·
input·svg·math 등은 내용까지 통째로 버린다. 속성도 허용 목록, style 은 CSS 속성 허용 목록으로 다시 쓴다.
허용 목록은 hanword 편집기(vendor/hanword/docs/embed.md, hanword_embed.sanitize_html)와 같거나 넓다 — 정본은
hanword.canonical_html(= hanword 정화 → 이 정화)이고, 이 두 번째 통과가 hanword 출력을 바꾸지 않아야 한다.
img src 는 data:image/(png|jpeg|gif|webp|bmp);base64 만(같은 출처 URL 도 금지). a href 는 http(s)/mailto/# 만.
data-field/data-row/data-sum 은 td·th·tr·table·span 에만, 이름(목록)으로만 — 평가하지 않음. 2MB·중첩 64단 제한.
"""
from __future__ import annotations

import html
import re
from html.parser import HTMLParser

MAX_SIZE = 2 * 1024 * 1024
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
DROP_VOID = {"input", "embed", "meta", "link", "base", "source", "track", "area"}

COMMON = {"style", "class", "title", "align"}
PER_TAG = {"td": {"colspan", "rowspan", "valign", "width", "height"}, "th": {"colspan", "rowspan", "valign", "width", "height"},
           "tr": {"valign", "height"}, "table": {"border", "cellpadding", "cellspacing", "width"},
           "col": {"span", "width"}, "colgroup": {"span", "width"}, "img": {"src", "alt", "width", "height"},
           "a": {"href", "rel"}, "font": {"face", "size", "color"}, "ol": {"start", "type"}, "li": {"value"}}
DATA_ATTRS = {"data-field", "data-row", "data-sum"}
DATA_TAGS = {"td", "th", "tr", "table", "span"}
_DV = r"[A-Za-z0-9_.\-\u3131-\u318E\uAC00-\uD7A3]{1,64}"
DATA_RE = re.compile(_DV + r"(?:," + _DV + r"){0,15}")  # 이름 또는 쉼표 이름 목록(식 아님)
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
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.stack, self.skip = [], [], []

    def _start(self, tag, attrs, closed):
        if self.skip:
            if tag == self.skip[-1] and not closed and tag not in DROP_VOID:
                self.skip.append(tag)
            return
        if tag in DROP:
            if not closed and tag not in DROP_VOID:
                self.skip.append(tag)
            return
        if tag not in ALLOWED:
            return
        if tag not in VOID and len(self.stack) >= MAX_DEPTH:
            return
        kept = []
        for k, v in attrs:
            k = (k or "").lower()
            if any(k == x for x, _ in kept):
                continue
            cv = _clean_attr(tag, k, v)
            if cv is not None:
                kept.append((k, cv))
        if tag == "img" and not any(k == "src" for k, _ in kept):
            return
        if tag == "a":  # 링크는 새 창에서도 opener 없이(hanword 와 같음), href 없으면 rel 도 없음
            kept = [(k, v) for k, v in kept if k != "rel"]
            if any(k == "href" for k, _ in kept):
                kept.append(("rel", REL))
        a = "".join(f' {k}="{html.escape(v, quote=True)}"' for k, v in kept)
        self.out.append(f"<{tag}{a}>")
        if tag not in VOID:
            self.stack.append(tag)

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


def clean_html(s) -> str:
    """편집기 HTML → 허용 목록만 남긴 안전한 HTML. 2MB 초과는 ValueError."""
    s = "" if s is None else str(s)
    if len(s.encode("utf-8", "surrogatepass")) > MAX_SIZE:
        raise ValueError("본문 HTML 이 너무 큽니다(최대 2MB)")
    s = s.replace("\x00", "")
    p = _Cleaner()
    p.feed(s)
    p.close()
    return p.result()
