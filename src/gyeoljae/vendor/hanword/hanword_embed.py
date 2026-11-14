#!/usr/bin/env python3
# hanword_embed.py — 임베드(전자결재 등 호스트 앱)용 변환·정제 API. 서버 없이 import 해서 쓴다(표준 라이브러리만,
#   PDF만 node+Playwright 필요). 편집기 {type:'html', html, page} 응답을 그대로 넘기면 된다. 문서: docs/embed.md
#
#   from hanword_embed import html_to_hwpx, html_to_docx, html_to_pdf, sanitize_html, GOV_PAGE
#   data = html_to_hwpx(html, page=GOV_PAGE)
import os, re, sys, threading, functools, html as htmlmod
from html.parser import HTMLParser
from hanword_htmlparse import EofSafeParser

APP = os.path.dirname(os.path.abspath(__file__))
if APP not in sys.path: sys.path.insert(0, APP)

__all__ = ["html_to_hwpx", "html_to_docx", "html_to_pdf", "sanitize_html", "normalize_page",
           "page_preset", "GOV_PAGE", "MARGIN_PRESETS", "InputError"]

# ───────────── 용지 ─────────────
PAPERS = ("A3", "A4", "A5", "A6", "B4", "B5", "B6", "Letter", "Legal", "Tabloid")
MARGIN_PRESETS = {"default": {"mt": 20, "mr": 20, "mb": 20, "ml": 20},
                  "gov": {"mt": 30, "ml": 20, "mr": 15, "mb": 15}}     # 공문서(기안문) A4: 위30·왼20·오15·아래15 mm
GOV_PAGE = {"paper": "A4", "orient": "portrait", "mt": 30, "mr": 15, "mb": 15, "ml": 20}

def page_preset(name):
    """미리 설정 이름("gov"|"default") → 용지 dict(A4 세로)."""
    if name not in MARGIN_PRESETS: raise ValueError("unknown preset: %r" % (name,))
    return dict({"paper": "A4", "orient": "portrait"}, **MARGIN_PRESETS[name])

def normalize_page(page):
    """편집기 page 값("gov" | {preset, paper, orient, mt, mr, mb, ml}) → 검증된 dict(mm). 허용 밖 값 = 기본(A4·20mm). None → None."""
    if page is None: return None
    if isinstance(page, str): page = {"preset": page}
    if not isinstance(page, dict): return None
    out = {"paper": "A4", "orient": "portrait", "mt": 20.0, "mr": 20.0, "mb": 20.0, "ml": 20.0}
    pr = page.get("preset")
    if isinstance(pr, str) and pr in MARGIN_PRESETS: out.update({k: float(v) for k, v in MARGIN_PRESETS[pr].items()})
    if page.get("paper") in PAPERS: out["paper"] = page["paper"]
    if page.get("orient") in ("portrait", "landscape"): out["orient"] = page["orient"]
    for k in ("mt", "mr", "mb", "ml"):
        v = page.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and 0 <= v <= 100: out[k] = round(float(v), 1)
    return out

def _meta_attrs(pg):
    """용지 dict → wpmeta 속성 문자열(편집기 writePaperMeta 와 같은 규칙)."""
    a = []
    if pg["paper"] != "A4": a.append(' data-paper="%s"' % pg["paper"])
    if pg["orient"] == "landscape": a.append(' data-orient="landscape"')
    mg = ",".join(("%g" % pg[k]) for k in ("mt", "mr", "mb", "ml"))
    if mg != "20,20,20,20": a.append(' data-margins="%s"' % mg)
    return "".join(a)

_WPMETA_RE = re.compile(r'<div id="wpmeta"([^>]*)>', re.I)
def _with_page_meta(html, pg):
    """본문 wpmeta 용지 속성을 pg로 교체(없으면 추가) — PDF(htmlpdf.js)는 wpmeta에서 용지를 읽는다."""
    def fix(m):
        rest = re.sub(r'\s+data-(paper|orient|margins)="[^"]*"', "", m.group(1))
        return '<div id="wpmeta"%s%s>' % (rest, _meta_attrs(pg))
    if _WPMETA_RE.search(html): return _WPMETA_RE.sub(fix, html, count=1)
    return html + '<div id="wpmeta" hidden=""%s></div>' % _meta_attrs(pg)

# ───────────── 변환 ─────────────
@functools.lru_cache(maxsize=1)
def _core():
    import hanword_core
    return hanword_core

class InputError(ValueError):
    """변환할 수 없는 입력(손상·악성·한도 초과)."""

def _html_str(html):
    if isinstance(html, bytes): html = html.decode("utf-8", "replace")
    if not isinstance(html, str): raise TypeError("html must be str")
    return html

def html_to_hwpx(html, page=None, sanitize=True):
    """편집기 HTML → .hwpx 바이트. page = 편집기 page 값(없으면 문서 wpmeta → A4·20mm).
       sanitize=True(기본) = sanitize_html 결과를 변환(편집기에 보이는 것 = 기록되는 것). False = 신뢰한 편집기 출력 그대로."""
    html, pg = _prep(html, page, sanitize)
    try: return _core().export_html(html, "hwpx", page=pg)
    except ValueError as e:
        if type(e).__name__.endswith("InputError"): raise InputError(str(e)) from e
        raise

def html_to_docx(html, page=None, sanitize=True):
    """편집기 HTML → .docx 바이트. page·sanitize 규칙은 html_to_hwpx 와 같음."""
    html, pg = _prep(html, page, sanitize)
    try: return _core().export_html(html, "docx", page=pg)
    except ValueError as e:
        if type(e).__name__.endswith("InputError"): raise InputError(str(e)) from e
        raise

def html_to_pdf(html, page=None, timeout=90, node_path=None, sanitize=True):
    """편집기 HTML → PDF 바이트(Chromium/Playwright). node + playwright 필요(node_path 또는 WP_NODE_PATH).
       폰트는 이 함수가 127.0.0.1 임시 포트에 font/ 폴더만 잠깐 띄워 공급(서버 불필요). 실패 시 RuntimeError."""
    html, pg = _prep(html, page, sanitize)
    if pg: html = _with_page_meta(html, pg)
    fontdir = os.environ.get("WP_FONTDIR", os.path.join(APP, "font"))
    with _font_lock:   # WP_FONT_URL 환경변수를 잠깐 바꾸므로 직렬화
        srv = _font_server(fontdir)
        old = os.environ.get("WP_FONT_URL")
        try:
            os.environ["WP_FONT_URL"] = "http://127.0.0.1:%d/font" % srv.server_address[1]
            return _core().html_to_pdf(html, node_path=node_path, timeout=timeout)
        finally:
            if old is None: os.environ.pop("WP_FONT_URL", None)
            else: os.environ["WP_FONT_URL"] = old
            srv.shutdown(); srv.server_close()

_font_lock = threading.Lock()
def _font_server(fontdir):
    """font/ 폴더의 파일명 단독 GET만 응답하는 일회용 로컬 서버(경로 이동 불가, 127.0.0.1)."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    CT = {"woff2": "font/woff2", "woff": "font/woff", "ttf": "font/ttf", "otf": "font/otf"}
    real = os.path.realpath(fontdir)
    class FH(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            p = self.path.split("?", 1)[0]
            fn = p[len("/font/"):] if p.startswith("/font/") else ""
            fp = os.path.realpath(os.path.join(real, fn))
            ext = fn.rsplit(".", 1)[-1].lower() if "." in fn else ""
            if not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", fn) or os.path.dirname(fp) != real or ext not in CT or not os.path.isfile(fp):
                self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers(); return
            with open(fp, "rb") as f: data = f.read()
            self.send_response(200); self.send_header("Content-Type", CT[ext]); self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*"); self.end_headers(); self.wfile.write(data)
    s = ThreadingHTTPServer(("127.0.0.1", 0), FH); s.daemon_threads = True
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s

# ───────────── 정제(편집기 wp-embed.js sanitizeEmbed 와 같은 허용목록) ─────────────
TAGS = set(("p div span br hr b strong i em u s strike del sub sup h1 h2 h3 h4 h5 h6 ul ol li "
            "table thead tbody tfoot tr td th caption colgroup col a img blockquote pre code font").split())
DROP = set(("script style template iframe frame frameset object embed applet noscript noembed noframes svg math head title meta link base "
            "form input button select option optgroup textarea datalist output video audio source track canvas map area xmp plaintext listing "
            "dialog slot portal marquee").split())
VOID = {"br", "hr", "img", "col"}
COMMON = {"style", "class", "title", "align"}
PER_TAG = {"td": {"colspan", "rowspan", "valign", "width", "height"}, "th": {"colspan", "rowspan", "valign", "width", "height"},
           "tr": {"valign", "height"}, "table": {"border", "cellpadding", "cellspacing", "width"},
           "col": {"span", "width"}, "colgroup": {"span", "width"}, "img": {"src", "alt", "width", "height"}, "a": {"href"},
           "font": {"face", "size", "color"}, "ol": {"start", "type"}, "li": {"value"}}
DATA_OK = {"data-field", "data-row", "data-sum"}
DATA_TAGS = {"td", "th", "tr", "table", "span"}
_DV = r"[A-Za-z0-9_.\-\u3131-\u318E\uAC00-\uD7A3]{1,64}"
DATA_VAL = re.compile(_DV + r"(," + _DV + r"){0,15}")   # 이름 또는 쉼표 이름 목록(식 아님 — 호스트도 eval 금지). wp-embed.js 와 동일
CLASS_OK = {"pgbreak", "objline", "wp-hr", "caption"}
NUM = re.compile(r"\d{1,4}(\.\d{1,2})?(%|px)?")
CSS_PROP = re.compile(r"(color|background-color|font|font-(family|size|weight|style|variant)|text-(align|indent|decoration|decoration-line|decoration-style|decoration-color|transform)|line-height|letter-spacing|word-spacing|vertical-align|white-space|word-break|margin(-(top|right|bottom|left))?|padding(-(top|right|bottom|left))?|border(-(top|right|bottom|left))?(-(width|style|color))?|border-collapse|border-spacing|width|height|min-width|max-width|min-height|max-height|table-layout|list-style-type|page-break-(before|after|inside)|break-(before|after|inside))")
CSS_BAD = re.compile(r"url\s*\(|expression|javascript:|vbscript:|@|\\|<|>|/\*|\*/|image-set|var\s*\(|attr\s*\(", re.I)
CTRL = re.compile(r"[\x00-\x1f\x7f]")

def _clean_style(v):
    out = []
    for decl in v.split(";"):
        k, sep, val = decl.partition(":")
        k = k.strip().lower(); val = val.strip()
        if not sep or not CSS_PROP.fullmatch(k) or not val or len(val) > 200 or CSS_BAD.search(val) or CTRL.search(val): continue
        out.append(k + ":" + val)
    return ";".join(out)

def _clean_attr(tag, name, v):
    if name == "style": return _clean_style(v) or None
    if name == "class":
        cl = [c for c in v.split() if c in CLASS_OK]; return " ".join(cl) or None
    if name in ("href", "src"):
        u = re.sub(r"[\x00-\x20\x7f-\x9f]+", "", v)
        if tag == "img": return u if re.fullmatch(r"data:image/(png|jpe?g|gif|webp|bmp);base64,[A-Za-z0-9+/=]+", u, re.I) else None
        return u if re.match(r"(https?://|mailto:|#)", u, re.I) else None
    if name in ("colspan", "rowspan", "span", "start", "value", "border", "cellpadding", "cellspacing", "size"):
        return v if re.fullmatch(r"\d{1,4}", v) else None
    if name in ("width", "height"): return v if NUM.fullmatch(v) else None
    if name in ("align", "valign"): return v if re.fullmatch(r"(?i)left|right|center|justify|top|middle|bottom|baseline", v) else None
    if name == "type": return v if re.fullmatch(r"[1aAiI]", v) else None
    if name == "color": return v if re.fullmatch(r"(?i)#[0-9a-f]{3,8}|[a-z]{3,20}", v) else None
    if name == "face": return v if re.fullmatch(r"[^<>\"'`;\\]{1,80}", v) else None
    if name in ("title", "alt"): return v[:300]
    return None

class _San(EofSafeParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []; self.stack = []; self.skip = 0; self.skip_tag = None
    def handle_starttag(self, tag, attrs, self_closing=False):
        if self.skip:
            if tag == self.skip_tag and tag not in VOID: self.skip += 1
            return
        if tag in DROP:   # 브라우저처럼 "/>" 는 무시 — 짝 닫는 태그까지 통째 버림(<title/>…</title> 숨김 글 차단)
            if tag not in VOID: self.skip = 1; self.skip_tag = tag
            return
        if dict(attrs).get("id") == "wpmeta":   # 편집기 메타는 버림(용지는 page 인자로)
            if tag not in VOID and not self_closing: self.skip = 1; self.skip_tag = tag
            return
        if tag not in TAGS or len(self.stack) > 200: return
        a = []
        pt = PER_TAG.get(tag, ())
        for k, v in attrs:
            k = k.lower(); v = "" if v is None else v
            if k in DATA_OK:
                if tag in DATA_TAGS and DATA_VAL.fullmatch(v): a.append((k, v))
                continue
            if k not in COMMON and k not in pt: continue
            cv = _clean_attr(tag, k, v)
            if cv: a.append((k, cv))
        if tag == "a" and any(k == "href" for k, _ in a): a.append(("rel", "noopener noreferrer"))
        self.out.append("<" + tag + "".join(' %s="%s"' % (k, htmlmod.escape(v, quote=True)) for k, v in a) + ">")
        if tag not in VOID and not self_closing: self.stack.append(tag)
    def handle_startendtag(self, tag, attrs): self.handle_starttag(tag, attrs, True)
    def handle_endtag(self, tag):
        if self.skip:
            if tag == self.skip_tag: self.skip -= 1
            return
        if tag in VOID or tag not in self.stack: return
        while self.stack:   # 안 닫힌 자식도 함께 닫음(균형 유지)
            t = self.stack.pop(); self.out.append("</%s>" % t)
            if t == tag: break
    def handle_data(self, d):
        if not self.skip: self.out.append(htmlmod.escape(d, quote=False))
    def result(self):
        while self.stack: self.out.append("</%s>" % self.stack.pop())
        return "".join(self.out)

def _cut_raw(html):
    """script/style: 첫 여는 태그 ~ 마지막 닫는 태그(없으면 끝)까지 통째 제거 — 브라우저 이중 이스케이프 규칙과의 차이 차단."""
    for t in ("script", "style"):
        while True:
            m = re.search(r"<%s(?=[\s/>])" % t, html, re.I)
            if not m: break
            ends = list(re.finditer(r"</%s(?=[\s/>])[^>]*>" % t, html[m.start():], re.I))
            html = html[:m.start()] + (html[m.start() + ends[-1].end():] if ends else "")
    return html
def sanitize_html(html):
    """신뢰할 수 없는 HTML → 편집기 허용목록만 남긴 HTML. 호스트는 이 결과를 정본으로 저장하고 그것만 표시·변환할 것
       (출력엔 원문 텍스트 요소가 없어 브라우저가 다시 읽어도 같은 내용). 편집기도 load 때 같은 규칙으로 정제한다."""
    html = _cut_raw(_html_str(html))   # script/style 은 첫 여는 태그~마지막 닫는 태그 통째 제거(이중 이스케이프 차이 차단)
    p = _San(); p.feed(html); p.close()
    return p.result()

_META_RE = re.compile(r'<div\s+id="wpmeta"([^>]*)>', re.I)
def _meta_page_of(html):
    """편집기 html 의 #wpmeta 용지 속성 → 검증된 page dict 또는 None."""
    m = _META_RE.search(html)
    if not m: return None
    g = lambda k: (re.search(r'\sdata-%s="([^"]*)"' % k, m.group(1)) or [None, ""])[1]
    pg = {}
    if g("paper") in PAPERS: pg["paper"] = g("paper")
    if g("orient") == "landscape": pg["orient"] = "landscape"
    mg = g("margins")
    if re.fullmatch(r"\d{1,3}(\.\d)?(,\d{1,3}(\.\d)?){3}", mg):
        a = [float(x) for x in mg.split(",")]
        if all(0 <= x <= 100 for x in a): pg.update(mt=a[0], mr=a[1], mb=a[2], ml=a[3])
    return normalize_page(pg) if pg else None

def _prep(html, page, sanitize):
    """변환 입력 정리: page 결정(인자 → 문서 wpmeta → None) 후 정제(기본)."""
    html = _html_str(html)
    pg = normalize_page(page) if page is not None else _meta_page_of(html)
    return (sanitize_html(html) if sanitize else html), pg
