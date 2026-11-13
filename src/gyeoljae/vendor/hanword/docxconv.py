#!/usr/bin/env python3
# docxconv.py — MS Word .docx(OOXML, ECMA-376 WordprocessingML) ↔ 한워드 에디터 HTML.
#   표준 라이브러리만 사용(zipfile + xml.etree). 외부 의존 없음.
#   export: 에디터 HTML → hwpxconv.P 문서모델(문단/런/표/그림/미주/머리말) → document.xml 등 패키지 조립
#   import: .docx → 에디터 HTML(hwpx import와 같은 HTML 규약: h1~h3·ul/ol·table·img data URI·sup.fn·#wpmeta)
import io, re, zipfile, base64, posixpath, html as htmlmod
import xml.etree.ElementTree as ET
import pyexpat as _expat
from hwpxconv import (P, _PP, ListHTML, MAX_LIST_LEVEL, cell_paras, _extract_meta, _extract_fnotes, _safe_link_url, HwpxInputError, check_zip_actual, zip_put,
                      _imgsize, rgb2hex, _safecolor, _meta_page, _page_meta_attrs)

class DocxInputError(HwpxInputError):
    """손상/악성 docx 입력(서버가 폴백 없이 즉시 거부)."""
    pass

# ───────────────────────── 공통 ─────────────────────────
NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
    "v": "urn:schemas-microsoft-com:vml",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}
W = "{%s}" % NS["w"]; R = "{%s}" % NS["r"]
RT = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
_BADXML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")

def _isint(s): return bool(re.fullmatch(r"[0-9]{1,9}", s or ""))   # str.isdigit은 '²' 등 유니코드 숫자도 참 → int() ValueError

def _x(s):   # XML 텍스트/속성 이스케이프 + XML 1.0 금지문자 제거
    return htmlmod.escape(_BADXML.sub("", s or ""), quote=True)

_IMG_MAGIC = ((b"\x89PNG\r\n\x1a\n", "png"), (b"\xff\xd8\xff", "jpeg"), (b"GIF8", "gif"), (b"BM", "bmp"))
def _sniff(data):
    for m, f in _IMG_MAGIC:
        if data[:len(m)] == m: return f
    return None

def _datauri(src):   # data:image/...;base64 → (bytes, fmt) — 실제 바이트 매직으로 형식 확정(위장 차단)
    m = re.match(r"^data:image/[a-z0-9.+-]+;base64,([A-Za-z0-9+/=\s]+)$", (src or "").strip(), re.I)
    if not m: return None
    try: d = base64.b64decode(re.sub(r"\s+", "", m.group(1)), validate=False)
    except Exception: return None
    f = _sniff(d)
    return (d, f) if f else None

# 용지(mm) — 에디터 PAPERS와 동일
PAPERS = {"A3": (297, 420), "A4": (210, 297), "A5": (148, 210), "A6": (105, 148), "B4": (257, 364),
          "B5": (182, 257), "B6": (128, 182), "Letter": (215.9, 279.4), "Legal": (215.9, 355.6), "Tabloid": (279.4, 431.8)}
def _tw_mm(mm): return int(round(float(mm) * 1440 / 25.4))
def _num(v, lo, hi, dflt):
    try: v = float(v)
    except (TypeError, ValueError): return dflt
    if v != v or v in (float("inf"), float("-inf")): return dflt   # NaN/Infinity → 기본값
    return min(hi, max(lo, v))

def page_setup(page=None):
    """page={paper,orient,w,h,ml,mr,mt,mb}(mm) → twips dict. 누락/이상값은 A4·20mm 기본."""
    page = page or {}
    pw, ph = PAPERS.get(str(page.get("paper") or ""), PAPERS["A4"])
    if page.get("w") and page.get("h"):
        pw = _num(page.get("w"), 50, 1000, pw); ph = _num(page.get("h"), 50, 1000, ph)
    elif page.get("orient") == "landscape": pw, ph = ph, pw
    mg = {k: _num(page.get(k), 0, 100, 20) for k in ("ml", "mr", "mt", "mb")}
    for a, b, size in (("ml", "mr", pw), ("mt", "mb", ph)):   # 본문 폭/높이 최소 20mm 보장(음수 폭=잘못된 문서)
        if mg[a] + mg[b] > size - 20:
            k = max(0.0, size - 20) / max(1e-6, mg[a] + mg[b]); mg[a] *= k; mg[b] *= k
    return {"w": _tw_mm(pw), "h": _tw_mm(ph), "ml": _tw_mm(mg["ml"]), "mr": _tw_mm(mg["mr"]),
            "mt": _tw_mm(mg["mt"]), "mb": _tw_mm(mg["mb"]), "landscape": pw > ph}

# ───────────────────────── export: HTML → DOCX ─────────────────────────
MAX_TCOLS = 256; MAX_TROWS = 20000; MAX_TCELLS = 30000   # 표 격자 상한(병합 증폭 DoS 차단)
_ALIGN = {"LEFT": "left", "CENTER": "center", "RIGHT": "right", "JUSTIFY": "both"}
_BSTY = {"solid": "single", "dashed": "dashed", "dotted": "dotted", "double": "double", "hidden": "nil", "none": "nil"}
_PNFMT = {"1": "decimal", "circ": "decimalEnclosedCircle", "roman": "lowerRoman", "hangul": "ganada"}
_GENERIC = {"sans-serif", "serif", "monospace", "cursive", "fantasy", "system-ui", "notokr", "inherit"}

def _hex(c):   # rgb2hex 결과 → 6자리 대문자(무효면 None)
    c = (c or "").lstrip("#")
    if re.match(r"^[0-9A-Fa-f]{3}$", c): c = "".join(ch * 2 for ch in c)
    return c.upper() if re.match(r"^[0-9A-Fa-f]{6}$", c) else None

def _font1(css):   # font-family 목록 → 첫 실제 글꼴명
    for f in (css or "").split(","):
        f = f.strip().strip("'\"").strip()
        if f and f.lower() not in _GENERIC: return f[:64]
    return None

class _Pkg:
    def __init__(self):
        self.rels = []        # (rid, type, target, external)
        self.media = []       # (name, bytes)
        self.ctypes = set()   # 이미지 확장자
        self.notes = []       # 미주 텍스트
        self.numdefs = []     # (numId, {수준: bullet|number}) — 목록 묶음마다 abstractNum 하나(9수준)
        self.docpr = 0
    def rel(self, typ, target, external=False):
        rid = "rId%d" % (len(self.rels) + 10)
        self.rels.append((rid, typ, target, external)); return rid
    def image(self, data, fmt):
        ext = {"jpeg": "jpg"}.get(fmt, fmt)
        name = "image%d.%s" % (len(self.media) + 1, ext)
        self.media.append((name, data)); self.ctypes.add(ext)
        return self.rel(RT + "image", "media/" + name)
    def newnum(self, kinds):   # 상한(MAX_NUMS) 넘으면 마지막 것을 재사용(출력 증폭 차단 — 서식만 어긋날 뿐)
        if len(self.numdefs) >= MAX_NUMS: return self.numdefs[-1]
        d = (len(self.numdefs) + 1, kinds); self.numdefs.append(d); return d

def _rpr(r, hsize=None, rstyle=None):
    o = ['<w:rStyle w:val="%s"/>' % rstyle] if rstyle else []
    f = _font1(r.get("font"))
    if f: o.append('<w:rFonts w:ascii="%s" w:hAnsi="%s" w:eastAsia="%s" w:cs="%s"/>' % ((_x(f),) * 4))
    if r.get("bold"): o.append("<w:b/><w:bCs/>")
    if r.get("italic"): o.append("<w:i/><w:iCs/>")
    if r.get("strike"): o.append("<w:strike/>")
    c = _hex(r.get("color"))
    if c: o.append('<w:color w:val="%s"/>' % c)
    sz = r.get("size")
    if sz and not hsize:
        try: o.append('<w:sz w:val="%d"/><w:szCs w:val="%d"/>' % ((max(1, min(800, int(sz) * 2)),) * 2))
        except (TypeError, ValueError): pass
    if r.get("underline"): o.append('<w:u w:val="single"/>')
    h = _hex(r.get("highlight"))
    if h: o.append('<w:shd w:val="clear" w:color="auto" w:fill="%s"/>' % h)
    if r.get("sup"): o.append('<w:vertAlign w:val="superscript"/>')   # 스키마 순서: shd 뒤
    elif r.get("sub"): o.append('<w:vertAlign w:val="subscript"/>')
    return ("<w:rPr>%s</w:rPr>" % "".join(o)) if o else ""

def _textrun(text, rpr):
    text = _BADXML.sub("", text.replace("\r", "").replace("\n", " ").replace("\t", " "))
    if not text: return ""
    return '<w:r>%s<w:t xml:space="preserve">%s</w:t></w:r>' % (rpr, _x(text.replace("\xa0", " ")))

def _drawing(pkg, data, fmt, wpx=None, maxpx=640):
    rid = pkg.image(data, fmt)
    sz = _imgsize(data, fmt) or (200, 150)
    w0, h0 = max(1, sz[0]), max(1, sz[1])
    w = int(wpx) if wpx else min(w0, maxpx)
    w = max(4, min(w, maxpx)); h = max(4, int(round(w * h0 / w0)))
    cx, cy = w * 9525, h * 9525
    pkg.docpr += 1; i = pkg.docpr
    return ('<w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">'
            '<wp:extent cx="%d" cy="%d"/><wp:docPr id="%d" name="그림 %d"/>'
            '<wp:cNvGraphicFramePr><a:graphicFrameLocks noChangeAspect="1"/></wp:cNvGraphicFramePr>'
            '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            '<pic:pic><pic:nvPicPr><pic:cNvPr id="%d" name="image%d"/><pic:cNvPicPr/></pic:nvPicPr>'
            '<pic:blipFill><a:blip r:embed="%s"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
            '<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="%d" cy="%d"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>'
            '</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r>') % (cx, cy, i, i, i, i, rid, cx, cy)

def _runs_xml(pkg, runs, notes, hsize=None, maxpx=640):
    out = []; i = 0
    while i < len(runs):
        r = runs[i]
        if r.get("endnote") is not None:
            txt = (notes or {}).get(r.get("endnote"), "")
            pkg.notes.append(txt); nid = len(pkg.notes) + 1   # 0/1 = 구분선 예약
            out.append('<w:r><w:rPr><w:rStyle w:val="EndnoteReference"/></w:rPr><w:endnoteReference w:id="%d"/></w:r>' % nid)
            i += 1; continue
        if r.get("img"):
            d = _datauri(r["img"])
            if d: out.append(_drawing(pkg, d[0], d[1], r.get("width"), maxpx))
            i += 1; continue
        if r.get("link"):   # 같은 링크의 연속 런 → w:hyperlink 하나
            url = r["link"]; grp = []
            while i < len(runs) and runs[i].get("link") == url and not runs[i].get("img") and runs[i].get("endnote") is None:
                grp.append(runs[i]); i += 1
            rid = pkg.rel(RT + "hyperlink", url, external=True)
            inner = "".join(_textrun(g.get("text", ""), _rpr(g, hsize, rstyle="Hyperlink")) for g in grp)
            if inner: out.append('<w:hyperlink r:id="%s" w:history="1">%s</w:hyperlink>' % (rid, inner))
            continue
        out.append(_textrun(r.get("text", ""), _rpr(r, hsize)))
        i += 1
    return "".join(out)

def _ppr(style=None, align=None, indent_px=0, num=None, keep_empty=False, ilvl=0):
    o = []
    if style: o.append('<w:pStyle w:val="%s"/>' % style)
    if num: o.append('<w:numPr><w:ilvl w:val="%d"/><w:numId w:val="%d"/></w:numPr>' % (max(0, min(MAX_LIST_LEVEL, int(ilvl or 0))), num))
    if indent_px and not num:
        try: o.append('<w:ind w:left="%d"/>' % max(0, min(31680, int(indent_px) * 15)))
        except (TypeError, ValueError): pass
    a = _ALIGN.get(align or "")
    if a: o.append('<w:jc w:val="%s"/>' % a)
    return ("<w:pPr>%s</w:pPr>" % "".join(o)) if o else ""

def _table_xml(pkg, t, notes, textw):
    rows = t.get("rows") or []
    if len(rows) > MAX_TROWS: raise HwpxInputError("표 행이 너무 많습니다")
    occ = {}; ncols = 0
    for ri, row in enumerate(rows):
        c = 0
        for cell in row:
            while (ri, c) in occ: c += 1
            if c >= MAX_TCOLS: break
            cs = max(1, min(64, MAX_TCOLS - c, int(cell.get("colspan") or 1))); rs = max(1, min(len(rows) - ri, int(cell.get("rowspan") or 1)))
            if len(occ) + cs * rs > MAX_TCELLS: raise HwpxInputError("표가 너무 큽니다(셀 수 상한)")
            for dr in range(rs):
                occ[(ri + dr, c)] = ("cell", cell, dr == 0, cs, rs)
                for k in range(1, cs): occ[(ri + dr, c + k)] = ("cov",)
            c += cs; ncols = max(ncols, c)
    ncols = max(1, ncols)
    # 열 폭: colspan=1 셀의 width(px) 우선, 없으면 균등
    widths = [None] * ncols
    for (ri, c), e in occ.items():
        if e[0] == "cell" and e[2] and e[3] == 1 and e[1].get("width") and widths[c] is None:
            widths[c] = int(e[1]["width"]) * 15
    known = sum(w for w in widths if w); unk = [i for i, w in enumerate(widths) if not w]
    rest = max(ncols * 300, textw - known) if unk else 0
    for i in unk: widths[i] = max(300, rest // len(unk))
    tot = sum(widths)
    if tot > textw * 1.02:   # 본문 폭 초과 → 비례 축소
        widths = [max(200, w * textw // tot) for w in widths]
    o = ['<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/><w:tblW w:w="%d" w:type="dxa"/>' % sum(widths)]
    if t.get("objalign") in ("CENTER", "RIGHT"): o.append('<w:jc w:val="%s"/>' % t["objalign"].lower())
    o.append('<w:tblLayout w:type="fixed"/><w:tblLook w:val="04A0" w:firstRow="0" w:lastRow="0" w:firstColumn="0" w:lastColumn="0" w:noHBand="0" w:noVBand="1"/></w:tblPr><w:tblGrid>')
    o.extend('<w:gridCol w:w="%d"/>' % w for w in widths)
    o.append("</w:tblGrid>")
    rowh = t.get("rowh") or []
    for ri in range(len(rows)):
        o.append("<w:tr>")
        if ri < len(rowh) and rowh[ri]:
            o.append('<w:trPr><w:trHeight w:val="%d"/></w:trPr>' % (int(rowh[ri]) * 15))
        c = 0
        while c < ncols:
            e = occ.get((ri, c))
            if e is None or e[0] == "cov":
                o.append('<w:tc><w:tcPr><w:tcW w:w="%d" w:type="dxa"/></w:tcPr><w:p/></w:tc>' % widths[c]); c += 1; continue
            _, cell, start, cs, rs = e
            tcpr = ['<w:tcW w:w="%d" w:type="dxa"/>' % sum(widths[c:c + cs])]
            if cs > 1: tcpr.append('<w:gridSpan w:val="%d"/>' % cs)
            if rs > 1: tcpr.append('<w:vMerge w:val="restart"/>' if start else "<w:vMerge/>")
            bd = cell.get("borders") or {}
            if bd:
                bx = []
                for side, wside in (("top", "top"), ("left", "left"), ("bottom", "bottom"), ("right", "right")):
                    if side in bd:
                        sty, px, col = bd[side]
                        val = _BSTY.get(sty, "single")
                        if val == "nil": bx.append('<w:%s w:val="nil"/>' % wside)
                        else: bx.append('<w:%s w:val="%s" w:sz="%d" w:space="0" w:color="%s"/>' % (wside, val, max(2, min(96, int(px or 1) * 6)), _hex(col) or "000000"))
                if bx: tcpr.append("<w:tcBorders>%s</w:tcBorders>" % "".join(bx))
            bg = _hex(cell.get("bg"))
            if bg and bg != "FFFFFF": tcpr.append('<w:shd w:val="clear" w:color="auto" w:fill="%s"/>' % bg)
            o.append("<w:tc><w:tcPr>%s</w:tcPr>" % "".join(tcpr))
            if start:
                cw = sum(widths[c:c + cs]) // 15
                cps = cell_paras(cell)   # 셀 여러 문단 → w:p 여러 개(문단별 정렬, 그림은 마지막 문단)
                for pi, (pruns, pal) in enumerate(cps):
                    runs = [r for r in pruns if r.get("text") or r.get("img")]
                    body = _runs_xml(pkg, runs, notes, maxpx=max(16, cw - 8))
                    if pi == len(cps) - 1:
                        for src, wpx in cell.get("images") or []:
                            d = _datauri(src)
                            if d: body += _drawing(pkg, d[0], d[1], wpx, max(16, cw - 8))
                    o.append("<w:p>%s%s</w:p>" % (_ppr(align=pal or cell.get("align")), body))
            else:
                o.append("<w:p/>")
            o.append("</w:tc>")
            c += cs
        o.append("</w:tr>")
    o.append("</w:tbl>")
    return "".join(o)

def parse_model(html):
    """에디터 HTML → (meta, notes, blocks). blocks = hwpxconv.P 문단모델 + pgbreak 표식. CLI/DOCX/HWPX 공용."""
    meta, html = _extract_meta(html)
    notes, html = _extract_fnotes(html)
    p = _PP(); p.feed(html); p.close(); p.flush()
    return meta, notes, p.paras

def _hf_xml(kind, text=None, pagenum=None):
    tag = "w:hdr" if kind == "header" else "w:ftr"
    if pagenum:
        al = {"center": "center", "right": "right", "left": "left"}[pagenum]
        body = ('<w:p><w:pPr><w:jc w:val="%s"/></w:pPr><w:r><w:fldChar w:fldCharType="begin"/></w:r>'
                '<w:r><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r><w:r><w:fldChar w:fldCharType="separate"/></w:r>'
                '<w:r><w:t>1</w:t></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>') % al
    else:
        body = "".join('<w:p><w:pPr><w:jc w:val="center"/></w:pPr>%s</w:p>' % _textrun(ln, "") for ln in (text or "").split("\n"))
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<%s xmlns:w="%s" xmlns:r="%s">%s</%s>'
            % (tag, NS["w"], NS["r"], body, tag))

_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="맑은 고딕" w:hAnsi="맑은 고딕" w:eastAsia="맑은 고딕" w:cs="맑은 고딕"/><w:sz w:val="20"/><w:szCs w:val="20"/><w:lang w:val="en-US" w:eastAsia="ko-KR"/></w:rPr></w:rPrDefault>
<w:pPrDefault><w:pPr><w:spacing w:after="0" w:line="384" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:spacing w:before="240" w:after="120"/><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:sz w:val="40"/><w:szCs w:val="40"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:spacing w:before="200" w:after="100"/><w:outlineLvl w:val="1"/></w:pPr><w:rPr><w:b/><w:sz w:val="32"/><w:szCs w:val="32"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:spacing w:before="160" w:after="80"/><w:outlineLvl w:val="2"/></w:pPr><w:rPr><w:b/><w:sz w:val="26"/><w:szCs w:val="26"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="ListParagraph"><w:name w:val="List Paragraph"/><w:basedOn w:val="Normal"/><w:qFormat/><w:pPr><w:ind w:left="800"/></w:pPr></w:style>
<w:style w:type="paragraph" w:styleId="EndnoteText"><w:name w:val="endnote text"/><w:basedOn w:val="Normal"/><w:rPr><w:sz w:val="18"/></w:rPr></w:style>
<w:style w:type="character" w:default="1" w:styleId="DefaultParagraphFont"><w:name w:val="Default Paragraph Font"/><w:uiPriority w:val="1"/><w:semiHidden/></w:style>
<w:style w:type="character" w:styleId="EndnoteReference"><w:name w:val="endnote reference"/><w:rPr><w:vertAlign w:val="superscript"/></w:rPr></w:style>
<w:style w:type="character" w:styleId="Hyperlink"><w:name w:val="Hyperlink"/><w:rPr><w:color w:val="0563C1"/><w:u w:val="single"/></w:rPr></w:style>
<w:style w:type="table" w:default="1" w:styleId="TableNormal"><w:name w:val="Normal Table"/><w:semiHidden/><w:tblPr><w:tblInd w:w="0" w:type="dxa"/><w:tblCellMar><w:top w:w="0" w:type="dxa"/><w:left w:w="108" w:type="dxa"/><w:bottom w:w="0" w:type="dxa"/><w:right w:w="108" w:type="dxa"/></w:tblCellMar></w:tblPr></w:style>
<w:style w:type="table" w:styleId="TableGrid"><w:name w:val="Table Grid"/><w:basedOn w:val="TableNormal"/><w:pPr><w:spacing w:line="240" w:lineRule="auto"/></w:pPr><w:tblPr><w:tblBorders><w:top w:val="single" w:sz="4" w:space="0" w:color="000000"/><w:left w:val="single" w:sz="4" w:space="0" w:color="000000"/><w:bottom w:val="single" w:sz="4" w:space="0" w:color="000000"/><w:right w:val="single" w:sz="4" w:space="0" w:color="000000"/><w:insideH w:val="single" w:sz="4" w:space="0" w:color="000000"/><w:insideV w:val="single" w:sz="4" w:space="0" w:color="000000"/></w:tblBorders></w:tblPr></w:style>
</w:styles>"""

_BULLETS = ("•", "◦", "▪")             # 수준별 글머리표 • ◦ ▪ 순환
_NUMFMTS = ("decimal", "lowerLetter", "lowerRoman")   # 수준별 번호 1. a. i. 순환

MAX_NUMS = 5000   # w:num 개수 상한

def _sig(kinds):   # abstractNum 서명 = 수준별 실제 종류(안 쓴 수준은 바로 위 수준 종류를 이어받음)
    o = []; k = "bullet"
    for lv in range(MAX_LIST_LEVEL + 1): k = kinds.get(lv, k); o.append(k)
    return tuple(o)

def _abstract(aid, kinds):   # 9수준 abstractNum — 수준별 bullet/번호 형식·들여쓰기. 안 쓴 수준은 바로 위 수준 종류를 이어받음
    o = ['<w:abstractNum w:abstractNumId="%d"><w:multiLevelType w:val="hybridMultilevel"/>' % aid]
    k = "bullet"
    for lv in range(MAX_LIST_LEVEL + 1):
        k = kinds.get(lv, k)
        if k == "bullet": f, t = "bullet", _BULLETS[lv % 3]
        else: f, t = _NUMFMTS[lv % 3], "%%%d." % (lv + 1)
        o.append('<w:lvl w:ilvl="%d"><w:start w:val="1"/><w:numFmt w:val="%s"/><w:lvlText w:val="%s"/><w:lvlJc w:val="left"/>'
                 '<w:pPr><w:ind w:left="%d" w:hanging="400"/></w:pPr></w:lvl>' % (lv, f, t, 800 + 400 * lv))
    o.append("</w:abstractNum>")
    return "".join(o)

def _numbering(pkg):   # abstractNum은 종류 서명마다 하나(중복 제거), 목록마다 작은 w:num + 번호 수준만 startOverride(1부터)
    absid = {}; ab = []; nums = []
    for nid, kinds in pkg.numdefs:
        sg = _sig(kinds)
        if sg not in absid:
            absid[sg] = len(absid); ab.append(_abstract(absid[sg], dict(enumerate(sg))))
        ov = "".join('<w:lvlOverride w:ilvl="%d"><w:startOverride w:val="1"/></w:lvlOverride>' % lv for lv in sorted(kinds) if kinds[lv] == "number")
        nums.append('<w:num w:numId="%d"><w:abstractNumId w:val="%d"/>%s</w:num>' % (nid, absid[sg], ov))
    ab = "".join(ab); nums = "".join(nums)
    return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:numbering xmlns:w="%s">%s%s</w:numbering>' % (NS["w"], ab, nums)

def _endnotes(pkg):
    o = ['<w:endnote w:type="separator" w:id="0"><w:p><w:r><w:separator/></w:r></w:p></w:endnote>',
         '<w:endnote w:type="continuationSeparator" w:id="1"><w:p><w:r><w:continuationSeparator/></w:r></w:p></w:endnote>']
    for i, t in enumerate(pkg.notes):
        o.append('<w:endnote w:id="%d"><w:p><w:pPr><w:pStyle w:val="EndnoteText"/></w:pPr>'
                 '<w:r><w:rPr><w:rStyle w:val="EndnoteReference"/></w:rPr><w:endnoteRef/></w:r>%s</w:p></w:endnote>'
                 % (i + 2, _textrun(" " + t, "")))
    return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:endnotes xmlns:w="%s" xmlns:r="%s">%s</w:endnotes>' % (NS["w"], NS["r"], "".join(o))

def html_to_docx(html, page=None):
    """에디터 HTML → .docx 바이트. page=용지 dict(mm, 선택)."""
    meta, notes, blocks = parse_model(html)
    ps = page_setup(page or _meta_page(meta))   # 명시 용지(클라 paperInfo) 우선, 없으면 문서 메타(wpmeta data-paper/orient/margins)
    textw = ps["w"] - ps["ml"] - ps["mr"]; maxpx = max(64, textw // 15)
    pkg = _Pkg(); body = []
    cur_num = None; lvnum = {}   # lvnum[수준] = 그 수준이 지금 쓰는 (numId, kinds)
    for b in blocks:
        tag = b.get("tag")
        lk = b.get("list") if tag == "li" else None
        lv = max(0, min(MAX_LIST_LEVEL, int(b.get("level") or 0))) if lk else 0
        if not lk: cur_num = None; lvnum = {}
        else:
            for k in [k for k in lvnum if k > lv]: del lvnum[k]   # 얕은 수준 복귀 → 깊은 수준 기록 해제(형제 하위목록은 다시 판정)
            d = lvnum.get(lv) or lvnum.get(max([k for k in lvnum if k < lv], default=-1))   # 이 수준 또는 가장 가까운 상위의 목록
            if d is None or d[1].get(lv, lk) != lk:
                d = pkg.newnum({})        # 최상위 새 목록, 또는 하위목록 종류 충돌 → 하위목록만 별도 numId(상위 번호는 계속)
                if lv == 0: lvnum = {}
            d[1].setdefault(lv, lk); lvnum[lv] = d; cur_num = d[0]
        if tag == "pgbreak":
            body.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>'); continue
        if tag == "table":
            body.append(_table_xml(pkg, b, notes, textw)); continue
        if tag == "img":
            d = _datauri(b.get("src"))
            if d: body.append("<w:p>%s%s</w:p>" % (_ppr(align=b.get("align")), _drawing(pkg, d[0], d[1], b.get("width"), maxpx)))
            continue
        hs = {"h1": "Heading1", "h2": "Heading2", "h3": "Heading3"}.get(tag)
        runs = [r for r in b.get("runs") or [] if r.get("text", "") != "" or r.get("endnote") is not None or r.get("img")]
        style = hs or ("ListParagraph" if lk else None)
        body.append("<w:p>%s%s</w:p>" % (_ppr(style, b.get("align"), b.get("indent"), cur_num if lk else None, ilvl=lv),
                                          _runs_xml(pkg, runs, notes, hs, maxpx)))
    if not body or body[-1].startswith("<w:tbl>"): body.append("<w:p/>")
    # 머리말/꼬리말
    hf_refs = []; parts = {}
    odd, even = meta.get("header_odd"), meta.get("header_even")
    even_on = bool(odd or even)
    hdefault = odd if even_on else meta.get("header")
    if hdefault:
        parts["word/header1.xml"] = _hf_xml("header", hdefault)
        hf_refs.append('<w:headerReference w:type="default" r:id="%s"/>' % pkg.rel(RT + "header", "header1.xml"))
    if even_on and even:
        parts["word/header2.xml"] = _hf_xml("header", even)
        hf_refs.append('<w:headerReference w:type="even" r:id="%s"/>' % pkg.rel(RT + "header", "header2.xml"))
    pn = meta.get("pagenum") if meta.get("pagenum") in ("center", "right", "left") else None
    if pn or meta.get("footer"):
        parts["word/footer1.xml"] = _hf_xml("footer", meta.get("footer"), pn)
        rid = pkg.rel(RT + "footer", "footer1.xml")
        hf_refs.append('<w:footerReference w:type="default" r:id="%s"/>' % rid)
        if even_on: hf_refs.append('<w:footerReference w:type="even" r:id="%s"/>' % rid)
    pgnum = ""
    fmt = _PNFMT.get(meta.get("pagenum_format") or "1", "decimal")
    st = meta.get("pagenum_start") or ""
    if fmt != "decimal" or (_isint(st) and int(st) >= 2):
        pgnum = '<w:pgNumType w:fmt="%s"%s/>' % (fmt, (' w:start="%d"' % min(int(st), 99999)) if _isint(st) and int(st) >= 2 else "")
    sect = ('<w:sectPr>%s%s<w:pgSz w:w="%d" w:h="%d"%s/><w:pgMar w:top="%d" w:right="%d" w:bottom="%d" w:left="%d" w:header="567" w:footer="567" w:gutter="0"/>%s</w:sectPr>'
            % ("".join(hf_refs), '<w:endnotePr><w:numFmt w:val="decimal"/></w:endnotePr>' if pkg.notes else "", ps["w"], ps["h"], ' w:orient="landscape"' if ps["landscape"] else "",
               ps["mt"], ps["mr"], ps["mb"], ps["ml"], pgnum))
    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
           '<w:document xmlns:w="%s" xmlns:r="%s" xmlns:wp="%s" xmlns:a="%s" xmlns:pic="%s"><w:body>%s%s</w:body></w:document>'
           % (NS["w"], NS["r"], NS["wp"], NS["a"], NS["pic"], "".join(body), sect))
    # 고정 파트 관계
    pkg.rel(RT + "styles", "styles.xml"); pkg.rel(RT + "settings", "settings.xml")
    if pkg.numdefs: pkg.rel(RT + "numbering", "numbering.xml")
    if pkg.notes: pkg.rel(RT + "endnotes", "endnotes.xml")
    settings = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:settings xmlns:w="%s"><w:defaultTabStop w:val="800"/>%s%s'
                '<w:compat><w:compatSetting w:name="compatibilityMode" w:uri="http://schemas.microsoft.com/office/word" w:val="15"/></w:compat></w:settings>'
                % (NS["w"], "<w:evenAndOddHeaders/>" if even_on else "",
                   '<w:endnotePr><w:numFmt w:val="decimal"/><w:endnote w:id="0"/><w:endnote w:id="1"/></w:endnotePr>' if pkg.notes else ""))
    ct = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
          '<Default Extension="xml" ContentType="application/xml"/>']
    for ext in sorted(pkg.ctypes):
        ct.append('<Default Extension="%s" ContentType="image/%s"/>' % (ext, {"jpg": "jpeg"}.get(ext, ext)))
    WM = "application/vnd.openxmlformats-officedocument.wordprocessingml."
    ov = [("/word/document.xml", WM + "document.main+xml"), ("/word/styles.xml", WM + "styles+xml"),
          ("/word/settings.xml", WM + "settings+xml"), ("/docProps/core.xml", "application/vnd.openxmlformats-package.core-properties+xml"),
          ("/docProps/app.xml", "application/vnd.openxmlformats-officedocument.extended-properties+xml")]
    if pkg.numdefs: ov.append(("/word/numbering.xml", WM + "numbering+xml"))
    if pkg.notes: ov.append(("/word/endnotes.xml", WM + "endnotes+xml"))
    for pth in parts: ov.append(("/" + pth, WM + ("header+xml" if "header" in pth else "footer+xml")))
    ct.extend('<Override PartName="%s" ContentType="%s"/>' % o for o in ov)
    ct.append("</Types>")
    rels = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships xmlns="%s">' % NS["rel"]]
    for rid, typ, tgt, ext in pkg.rels:
        rels.append('<Relationship Id="%s" Type="%s" Target="%s"%s/>' % (rid, typ, _x(tgt), ' TargetMode="External"' if ext else ""))
    rels.append("</Relationships>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        zip_put(z, "[Content_Types].xml", "".join(ct))
        zip_put(z, "_rels/.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships xmlns="%s">'
                   '<Relationship Id="rId1" Type="%sofficeDocument" Target="word/document.xml"/>'
                   '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
                   '<Relationship Id="rId3" Type="%sextended-properties" Target="docProps/app.xml"/></Relationships>' % (NS["rel"], RT, RT))
        zip_put(z, "docProps/core.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
                   'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:creator>한워드</dc:creator></cp:coreProperties>')
        zip_put(z, "docProps/app.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"><Application>Hanword</Application></Properties>')
        zip_put(z, "word/document.xml", doc)
        zip_put(z, "word/styles.xml", _STYLES)
        zip_put(z, "word/settings.xml", settings)
        if pkg.numdefs: zip_put(z, "word/numbering.xml", _numbering(pkg))
        if pkg.notes: zip_put(z, "word/endnotes.xml", _endnotes(pkg))
        for pth, xml in parts.items(): zip_put(z, pth, xml)
        zip_put(z, "word/_rels/document.xml.rels", "".join(rels))
        for name, data in pkg.media: zip_put(z, "word/media/" + name, data)
    return buf.getvalue()

# ───────────────────────── import: DOCX → HTML ─────────────────────────
_MAXBYTES = 200 * 1024 * 1024; _MAXFILES = 2000; _RATIO = 200
MAX_IMG_BYTES = 80 * 1024 * 1024; MAX_IMG_COUNT = 2000; MAX_DEPTH = 60

def guard_docx(data):
    """zip폭탄(총량·개수·압축비)·XXE(DTD/ENTITY) 사전 차단 + 필수 파트 확인. 반환=ZipFile."""
    try: zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile: raise DocxInputError("docx(zip) 형식이 아님")
    infos = zf.infolist()
    if len(infos) > _MAXFILES: raise DocxInputError("엔트리 과다")
    check_zip_actual(zf, _MAXBYTES, _RATIO, DocxInputError)   # 선언 크기 불신·실측, STORED/DEFLATED만
    if "word/document.xml" not in zf.namelist(): raise DocxInputError("word/document.xml 없음(docx 아님)")
    return zf

def _xml(zf, name):
    try: raw = zf.read(name)
    except KeyError: return None
    low = raw[:65536].lower()
    if b"<!doctype" in low or b"<!entity" in low: raise DocxInputError("DTD/엔티티 선언 금지(XXE 차단)")
    return _safe_parse(raw, name)

def _no_dtd(*a): raise DocxInputError("DTD/엔티티 선언 금지(XXE 차단)")

def _safe_parse(raw, name=""):
    """DOCTYPE/ENTITY 선언 거부 후 파싱. ET(C 가속)는 expat 핸들러를 노출하지 않으므로 pyexpat로 먼저 한 번 훑는다
       (앞부분 바이트 검사는 주석 패딩·UTF-16으로 우회 가능 — 감사 2026-11-13)."""
    pp = _expat.ParserCreate()
    pp.StartDoctypeDeclHandler = _no_dtd; pp.EntityDeclHandler = _no_dtd
    try: pp.Parse(raw, True)
    except _expat.ExpatError: raise DocxInputError("XML 손상: %s" % name)
    try: return ET.fromstring(raw)
    except ET.ParseError: raise DocxInputError("XML 손상: %s" % name)

def _on(el):   # 토글 속성(w:b 등): val 없음/true/1/on = 켜짐
    if el is None: return False
    v = (el.get(W + "val") or "true").lower()
    return v not in ("0", "false", "off", "none")

_HL = {"yellow": "#FFFF00", "green": "#00FF00", "cyan": "#00FFFF", "magenta": "#FF00FF", "blue": "#0000FF", "red": "#FF0000",
       "darkBlue": "#000080", "darkCyan": "#008080", "darkGreen": "#008000", "darkMagenta": "#800080", "darkRed": "#800000",
       "darkYellow": "#808000", "darkGray": "#808080", "lightGray": "#C0C0C0", "black": "#000000"}

def _hexok(v):
    v = (v or "").strip()
    return ("#" + v.upper()) if re.match(r"^[0-9A-Fa-f]{6}$", v) else None

def _esc(s): return htmlmod.escape(s or "", quote=True)
PGB = "\x00PGBREAK\x00"

class _DocxReader:
    def __init__(self, zf):
        self.zf = zf
        self.rels = self._rels("word/_rels/document.xml.rels")
        self.styles = {}; self._load_styles(); self.nums = {}; self._load_numbering()
        self.notes = []; self.notemap = {}
        self.fn = self._notes("word/footnotes.xml", "footnote"); self.en = self._notes("word/endnotes.xml", "endnote")
        self.imgcache = {}; self.img_bytes = 0; self.img_count = 0
        self.depth = 0

    def _rels(self, name):
        out = {}; root = _xml(self.zf, name)
        if root is None: return out
        for r in root:
            out[r.get("Id")] = (r.get("Type") or "", r.get("Target") or "", r.get("TargetMode") or "")
        return out

    def _load_styles(self):
        root = _xml(self.zf, "word/styles.xml")
        if root is None: return
        raw = {}
        for s in root.findall(W + "style"):
            sid = s.get(W + "styleId"); nm = s.find(W + "name")
            based = s.find(W + "basedOn"); ol = s.find(W + "pPr/" + W + "outlineLvl")
            rpr = s.find(W + "rPr")
            raw[sid] = {"name": (nm.get(W + "val") if nm is not None else sid or "").lower(),
                        "based": based.get(W + "val") if based is not None else None,
                        "ol": ol.get(W + "val") if ol is not None else None,
                        "bold": _on(rpr.find(W + "b")) if rpr is not None and rpr.find(W + "b") is not None else None,
                        "italic": _on(rpr.find(W + "i")) if rpr is not None and rpr.find(W + "i") is not None else None}
        def level(sid, depth=0):
            st = raw.get(sid)
            if not st or depth > 10: return None
            m = re.match(r"^heading (\d)$", st["name"])
            if m: return int(m.group(1))
            if st["name"] == "title": return 1
            if st["ol"] is not None and _isint(st["ol"]) and int(st["ol"]) < 9: return int(st["ol"]) + 1
            return level(st["based"], depth + 1)
        for sid, st in raw.items():
            self.styles[sid] = {"h": level(sid), "bold": st["bold"], "italic": st["italic"]}

    def _load_numbering(self):
        root = _xml(self.zf, "word/numbering.xml")
        if root is None: return
        absf = {}
        for a in root.findall(W + "abstractNum"):
            lv = {}
            for l in a.findall(W + "lvl"):
                f = l.find(W + "numFmt")
                lv[l.get(W + "ilvl")] = (f.get(W + "val") if f is not None else "decimal")
            absf[a.get(W + "abstractNumId")] = lv
        for n in root.findall(W + "num"):
            a = n.find(W + "abstractNumId")
            self.nums[n.get(W + "numId")] = absf.get(a.get(W + "val") if a is not None else None, {})

    def _notes(self, name, kind):
        out = {}; root = _xml(self.zf, name)
        if root is None: return out
        for n in root.findall(W + kind):
            if n.get(W + "type") in ("separator", "continuationSeparator", "continuationNotice"): continue
            out[n.get(W + "id")] = " ".join("".join(t.text or "" for t in p.iter(W + "t")) for p in n.iter(W + "p")).strip()
        return out

    def img_tag(self, im, style):   # 출력 data URI 총량·개수 상한(같은 그림 수천 번 참조 → 수 GB HTML 증폭 차단)
        self.img_count += 1; self.img_bytes += len(im[0]) * 4 // 3
        if self.img_count > MAX_IMG_COUNT or self.img_bytes > MAX_IMG_BYTES:
            raise DocxInputError("그림이 너무 많거나 큽니다")
        return '<img src="data:image/%s;base64,%s" style="%smax-width:100%%">' % (im[1], base64.b64encode(im[0]).decode(), style)

    def image(self, rid):
        if rid in self.imgcache: return self.imgcache[rid]
        res = None; rel = self.rels.get(rid)
        if rel and rel[2] != "External":
            tgt = rel[1].lstrip("/")
            path = tgt if tgt.startswith("word/") else "word/" + tgt
            path = posixpath.normpath(path).lstrip("/")
            try:
                d = self.zf.read(path); f = _sniff(d)
                if f: res = (d, f)
            except KeyError: pass
        self.imgcache[rid] = res; return res

    # ── 런/문단 ──
    def run_html(self, r, pst):
        rp = r.find(W + "rPr")
        g = lambda t: rp.find(W + t) if rp is not None else None
        sb = (self.styles.get(pst) or {}) if pst else {}
        if sb.get("h"): sb = {}            # 제목 스타일의 굵게는 h1~h3 태그가 표현(중복 <b> 방지)
        bold = _on(g("b")) if g("b") is not None else bool(sb.get("bold"))
        ital = _on(g("i")) if g("i") is not None else bool(sb.get("italic"))
        und = g("u") is not None and (g("u").get(W + "val") or "single") != "none"
        strike = _on(g("strike")) or _on(g("dstrike"))
        va = g("vertAlign"); va = va.get(W + "val") if va is not None else ""
        style = ""
        c = g("color")
        if c is not None:
            col = _hexok(c.get(W + "val"))
            if col and col != "#000000": style += "color:%s;" % col
        sz = g("sz")
        if sz is not None and _isint(sz.get(W + "val")):
            pt = int(sz.get(W + "val")) / 2
            if 1 <= pt <= 400 and pt != 10: style += "font-size:%gpt;" % pt
        hl = g("highlight"); bg = None
        if hl is not None: bg = _HL.get(hl.get(W + "val") or "")
        shd = g("shd")
        if bg is None and shd is not None: bg = _hexok(shd.get(W + "fill"))
        if bg and bg != "#FFFFFF": style += "background-color:%s;" % bg
        rf = g("rFonts")
        if rf is not None:
            fn = rf.get(W + "eastAsia") or rf.get(W + "ascii") or rf.get(W + "hAnsi")
            if fn and re.match(r"^[\w\s가-힣.-]{1,40}$", fn) and fn not in ("맑은 고딕", "Malgun Gothic", "Calibri", "Times New Roman"):
                style += "font-family:'%s','NotoKR',sans-serif;" % fn
        parts = []
        for ch in r:
            t = ch.tag
            if t == W + "t": parts.append(_esc(ch.text or ""))
            elif t == W + "tab": parts.append("&nbsp;&nbsp;&nbsp;&nbsp;")
            elif t in (W + "br", W + "cr"):
                parts.append("\x00PGBREAK\x00" if ch.get(W + "type") == "page" else "<br>")
            elif t == W + "noBreakHyphen": parts.append("-")
            elif t == W + "sym": parts.append("")
            elif t in (W + "footnoteReference", W + "endnoteReference"):
                src = self.fn if t == W + "footnoteReference" else self.en
                self.notes.append(src.get(ch.get(W + "id"), "")); n = len(self.notes)
                parts.append('<sup class="fn" contenteditable="false" data-fn="%d">%d)</sup>' % (n, n))
            elif t == W + "drawing":
                blip = ch.find(".//{%s}blip" % NS["a"]); ext = ch.find(".//{%s}extent" % NS["wp"])
                im = self.image(blip.get(R + "embed")) if blip is not None else None
                if im:
                    ws = ""
                    try:
                        if ext is not None: ws = "width:%dpx;" % max(4, min(2000, int(ext.get("cx")) // 9525))
                    except (TypeError, ValueError): pass
                    parts.append(self.img_tag(im, ws))
            elif t == W + "pict" or t == W + "object":
                idata = ch.find(".//{%s}imagedata" % NS["v"])
                im = self.image(idata.get(R + "id")) if idata is not None else None
                if im: parts.append(self.img_tag(im, ""))
        def wrap(h):
            if not h: return ""
            if va == "superscript" and "<sup" not in h: h = "<sup>%s</sup>" % h
            elif va == "subscript": h = "<sub>%s</sub>" % h
            if strike: h = "<s>%s</s>" % h
            if und: h = "<u>%s</u>" % h
            if ital: h = "<i>%s</i>" % h
            if bold: h = "<b>%s</b>" % h
            if style: h = '<span style="%s">%s</span>' % (style, h)
            return h
        return PGB.join(wrap(s) for s in "".join(parts).split(PGB))   # 쪽 나누기는 서식 밖(태그 균형)

    def inline(self, el, pst, out):
        self.depth += 1
        try:
            if self.depth > MAX_DEPTH: raise DocxInputError("중첩이 너무 깊습니다")
            self._inline(el, pst, out)
        finally: self.depth -= 1

    def _inline(self, el, pst, out):
        for ch in el:
            t = ch.tag
            if t == W + "r": out.append(self.run_html(ch, pst))
            elif t == W + "hyperlink":
                rel = self.rels.get(ch.get(R + "id")); url = _safe_link_url(rel[1]) if rel and rel[2] == "External" else None
                sub = []; self.inline(ch, pst, sub)
                inner = "".join(sub)
                if url and inner:
                    out.append(PGB.join(('<a href="%s" rel="noopener noreferrer" title="%s">%s</a>' % (_esc(url), _esc(url), s)) if s else ""
                                        for s in inner.split(PGB)))
                else: out.append(inner)
            elif t in (W + "ins", W + "smartTag", W + "fldSimple", W + "customXml", W + "bdo", W + "dir"):
                self.inline(ch, pst, out)
            elif t == W + "sdt":
                c = ch.find(W + "sdtContent")
                if c is not None: self.inline(c, pst, out)
            # w:del, w:proofErr, w:bookmark*, w:pPr 등은 무시

    def para(self, p):
        ppr = p.find(W + "pPr"); pst = None; align = None; num = None; indent = 0; olv = None; numid = None; lvl = 0
        if ppr is not None:
            ps = ppr.find(W + "pStyle"); pst = ps.get(W + "val") if ps is not None else None
            jc = ppr.find(W + "jc")
            if jc is not None:
                align = {"center": "center", "right": "right", "end": "right", "both": "justify", "distribute": "justify"}.get(jc.get(W + "val"))
            np_ = ppr.find(W + "numPr")
            if np_ is not None:
                nid = np_.find(W + "numId"); il = np_.find(W + "ilvl")
                nid = nid.get(W + "val") if nid is not None else None
                if nid and nid != "0":
                    numid = nid
                    ilv = il.get(W + "val") if il is not None else "0"
                    if not _isint(ilv) or int(ilv) > MAX_LIST_LEVEL: ilv = "0"
                    lvl = int(ilv)
                    fmt = (self.nums.get(nid) or {}).get(ilv, "decimal")
                    num = "ul" if fmt in ("bullet", "none") else "ol"
            ind = ppr.find(W + "ind")
            if ind is not None:
                try: indent = int(ind.get(W + "left") or ind.get(W + "start") or 0) // 15
                except ValueError: indent = 0
            ol = ppr.find(W + "outlineLvl")
            if ol is not None and _isint(ol.get(W + "val")) and int(ol.get(W + "val")) < 3: olv = int(ol.get(W + "val")) + 1
        h = (self.styles.get(pst) or {}).get("h") if pst else None
        h = h or olv
        out = []; self.inline(p, pst, out)
        inner = "".join(out)
        sty = ""
        if align: sty += "text-align:%s;" % align
        if indent > 0 and not num: sty += "margin-left:%dpx;" % min(indent, 600)
        return {"h": min(h, 3) if h else None, "num": num, "numid": numid, "lvl": lvl, "style": sty, "inner": inner}

    def table(self, tbl):
        grid = [int(g.get(W + "w") or 0) // 15 for g in tbl.findall(W + "tblGrid/" + W + "gridCol") if _isint(g.get(W + "w"))]
        rows = []; vm = {}   # grid col → 시작 셀 dict(세로병합 누적)
        for tr in tbl.findall(W + "tr"):
            row = []; col = 0; trh = tr.find(W + "trPr/" + W + "trHeight")
            rh = 0
            if trh is not None and _isint(trh.get(W + "val")): rh = int(trh.get(W + "val")) // 15
            for tc in tr.findall(W + "tc"):
                pr = tc.find(W + "tcPr")
                gs = pr.find(W + "gridSpan") if pr is not None else None
                span = int(gs.get(W + "val")) if gs is not None and _isint(gs.get(W + "val")) else 1
                span = max(1, min(64, span))
                vme = pr.find(W + "vMerge") if pr is not None else None
                if vme is not None and (vme.get(W + "val") or "continue") == "continue" and col in vm:
                    vm[col]["rowspan"] += 1; col += span; continue
                cell = {"colspan": span, "rowspan": 1, "style": "", "html": ""}
                if vme is not None: vm[col] = cell
                else: vm.pop(col, None)
                if pr is not None:
                    shd = pr.find(W + "shd"); bg = _hexok(shd.get(W + "fill")) if shd is not None else None
                    if bg and bg != "#FFFFFF": cell["style"] += "background-color:%s;" % bg
                    tw = pr.find(W + "tcW")
                    if tw is not None and tw.get(W + "type") in (None, "dxa") and _isint(tw.get(W + "w")):
                        cell["style"] += "width:%dpx;" % max(8, int(tw.get(W + "w")) // 15)
                    bd = pr.find(W + "tcBorders")
                    if bd is not None:
                        for side in ("top", "left", "bottom", "right"):
                            e = bd.find(W + side)
                            if e is None: continue
                            v = e.get(W + "val")
                            if v in ("nil", "none"): cell["style"] += "border-%s:1px hidden;" % side; continue
                            css = {"dashed": "dashed", "dotted": "dotted", "double": "double"}.get(v)
                            if not css: continue
                            try: px = max(1, min(3, int(e.get(W + "sz") or 4) // 6))
                            except ValueError: px = 1
                            cell["style"] += "border-%s:%dpx %s %s;" % (side, 3 if css == "double" else px, css, _hexok(e.get(W + "color")) or "#000000")
                ps = []; first_align = None   # ps=[(inner, 정렬)]
                for ch in tc:
                    if ch.tag == W + "p":
                        d = self.para(ch)
                        al = re.search(r"text-align:(\w+);", d["style"])
                        al = al.group(1) if al else None
                        if first_align is None: first_align = al or ""
                        ps.append((d["inner"].replace("\x00PGBREAK\x00", ""), al))
                    elif ch.tag == W + "tbl":   # 중첩 표 → 셀 텍스트로 평탄화
                        ps.append((_esc(" ".join("".join(t.text or "" for t in p.iter(W + "t")) for p in ch.iter(W + "p")).strip()), None))
                if first_align: cell["style"] += "text-align:%s;" % first_align
                if len(ps) > 1:   # 셀 여러 문단 → <p> 각각(셀 정렬과 다른 문단만 정렬 명시)
                    cell["html"] = "".join("<p%s>%s</p>" % ((' style="text-align:%s;"' % (a or "left")) if (a or "") != (first_align or "") else "", h.strip() or "<br>")
                                           for h, a in ps)
                else: cell["html"] = (ps[0][0] if ps else "").strip() or "<br>"
                row.append(cell); col += span
            rows.append((rh, row))
        o = ["<table>"]
        for rh, row in rows:
            o.append('<tr style="height:%dpx">' % rh if rh > 0 else "<tr>")
            for c in row:
                at = ""
                if c["colspan"] > 1: at += ' colspan="%d"' % c["colspan"]
                if c["rowspan"] > 1: at += ' rowspan="%d"' % c["rowspan"]
                if c["style"]: at += ' style="%s"' % c["style"]
                o.append("<td%s>%s</td>" % (at, c["html"]))
            o.append("</tr>")
        o.append("</table>")
        return "".join(o)

    def body(self, el, out, state):
        self.depth += 1
        try:
            if self.depth > MAX_DEPTH: raise DocxInputError("중첩이 너무 깊습니다")
            self._body(el, out, state)
        finally: self.depth -= 1

    def _body(self, el, out, state):
        for ch in el:
            if ch.tag == W + "p":
                d = self.para(ch)
                segs = d["inner"].split("\x00PGBREAK\x00")
                for k, seg in enumerate(segs):
                    if k > 0:
                        self._close_list(out, state)
                        out.append('<div class="pgbreak" contenteditable="false"><br></div>')
                    if not seg.strip() and len(segs) > 1 and (k == 0 or k == len(segs) - 1): continue   # 나누기만 있는 문단
                    if d["num"]:   # 수준(ilvl)별 중첩 목록. 다른 목록(numId) = 새 목록
                        if "lh" not in state: state["lh"] = ListHTML(out)
                        state["lh"].item(d["num"], d["lvl"], seg or "<br>", key=d["numid"])
                        continue
                    self._close_list(out, state)
                    tag = ("h%d" % d["h"]) if d["h"] else "p"
                    out.append("<%s%s>%s</%s>" % (tag, (' style="%s"' % d["style"]) if d["style"] else "", seg or "<br>", tag))
            elif ch.tag == W + "tbl":
                self._close_list(out, state); out.append(self.table(ch))
            elif ch.tag in (W + "sdt",):
                c = ch.find(W + "sdtContent")
                if c is not None: self.body(c, out, state)
            elif ch.tag in (W + "customXml", W + "ins"):
                self.body(ch, out, state)

    @staticmethod
    def _close_list(out, state):
        if state.get("lh"): state["lh"].close()

    def hf_text(self, rid):
        rel = self.rels.get(rid)
        if not rel: return None, False
        root = _xml(self.zf, "word/" + rel[1].lstrip("/").replace("word/", "", 1))
        if root is None: return None, False
        has_page = any("PAGE" in (it.text or "").upper().split() for it in root.iter(W + "instrText")) or \
                   any("PAGE" in (f.get(W + "instr") or "").upper().split() for f in root.iter(W + "fldSimple"))
        lines = []
        for p in root.iter(W + "p"):
            txt = []
            for r in p.iter(W + "r"):
                if r.find(W + "instrText") is not None: continue
                txt.extend(t.text or "" for t in r.iter(W + "t"))
            s = "".join(txt).strip()
            if s: lines.append(s)
        return "\n".join(lines), has_page

    def meta_html(self, sect):
        if sect is None: return ""
        attrs = {}; jc = "center"
        hdr = {}; ftr = {}
        for hr in sect.findall(W + "headerReference"): hdr[hr.get(W + "type") or "default"] = hr.get(R + "id")
        for fr in sect.findall(W + "footerReference"): ftr[fr.get(W + "type") or "default"] = fr.get(R + "id")
        evenon = _xml(self.zf, "word/settings.xml")
        evenon = evenon is not None and evenon.find(W + "evenAndOddHeaders") is not None
        if hdr.get("default"):
            t, _ = self.hf_text(hdr["default"])
            if t:
                if evenon and hdr.get("even"): attrs["header-odd"] = t
                else: attrs["header"] = t
        if evenon and hdr.get("even"):
            t, _ = self.hf_text(hdr["even"])
            if t: attrs["header-even"] = t
        if ftr.get("default"):
            rel = self.rels.get(ftr["default"])
            t, pg = self.hf_text(ftr["default"])
            if pg:
                root = _xml(self.zf, "word/" + rel[1].lstrip("/").replace("word/", "", 1))
                j = root.find(".//" + W + "jc") if root is not None else None
                v = j.get(W + "val") if j is not None else "center"
                attrs["pagenum"] = {"right": "right", "end": "right", "left": "left", "start": "left"}.get(v, "center")
            elif t: attrs["footer"] = t
        pg = sect.find(W + "pgNumType")
        if pg is not None:
            f = {"decimalEnclosedCircle": "circ", "lowerRoman": "roman", "upperRoman": "roman", "ganada": "hangul", "chosung": "hangul"}.get(pg.get(W + "fmt") or "")
            if f: attrs["pagenum-format"] = f
            s = pg.get(W + "start") or ""
            if _isint(s) and int(s) >= 2: attrs["pagenum-start"] = str(min(int(s), 99999))
        sz = sect.find(W + "pgSz"); mg = sect.find(W + "pgMar")   # 용지·여백(twips) → data-paper/orient/margins
        if sz is not None:
            try:
                tw = lambda v: int(v) * 25.4 / 1440
                w, h = tw(sz.get(W + "w")), tw(sz.get(W + "h"))
                m = [abs(tw(mg.get(W + k) or 0)) for k in ("top", "right", "bottom", "left")] if mg is not None else [20] * 4
                attrs.update(_page_meta_attrs(w, h, *m))
            except (TypeError, ValueError): pass
        if not attrs: return ""
        return '<div id="wpmeta" hidden=""%s></div>' % "".join(' data-%s="%s"' % (k, _esc(v)) for k, v in attrs.items())

def page_info(data):
    """docx sectPr → 용지 dict(mm) — 에디터 용지 설정용(선택)."""
    zf = guard_docx(data); root = _xml(zf, "word/document.xml")
    s = root.find(W + "body/" + W + "sectPr")
    if s is None: return {}
    sz = s.find(W + "pgSz"); mg = s.find(W + "pgMar"); out = {}
    try:
        if sz is not None:
            out["w"] = round(int(sz.get(W + "w")) * 25.4 / 1440, 1); out["h"] = round(int(sz.get(W + "h")) * 25.4 / 1440, 1)
        if mg is not None:
            for k, a in (("mt", "top"), ("mb", "bottom"), ("ml", "left"), ("mr", "right")):
                out[k] = round(abs(int(mg.get(W + a) or 0)) * 25.4 / 1440, 1)
    except (TypeError, ValueError): pass
    return out

def docx_to_html(data):
    """.docx 바이트 → 에디터 HTML. 모든 내부 오류는 DocxInputError(사용자 입력 문제)로 — 서버가 원격 폴백하지 않게."""
    try: return _docx_to_html(data)
    except DocxInputError: raise
    except (RecursionError, MemoryError): raise DocxInputError("문서 구조가 너무 복잡합니다")
    except Exception as e: raise DocxInputError("docx 해석 실패(%s)" % type(e).__name__)

def _docx_to_html(data):
    zf = guard_docx(data)
    rd = _DocxReader(zf)
    root = _xml(zf, "word/document.xml")
    body = root.find(W + "body")
    if body is None: raise DocxInputError("본문(w:body) 없음")
    out = []; state = {}
    rd.body(body, out, state); rd._close_list(out, state)
    html = "".join(out).replace("\x00PGBREAK\x00", "")
    if rd.notes:
        ns = "".join('<p class="fn-e" data-fn="%d"><span class="fn-no" contenteditable="false">%d)</span> %s</p>' % (i + 1, i + 1, _esc(t))
                     for i, t in enumerate(rd.notes))
        html += '<div class="fnotes"><p class="fn-t" contenteditable="false">주석</p>' + ns + "</div>"
    html += rd.meta_html(body.find(W + "sectPr"))
    return html or "<p><br></p>"
