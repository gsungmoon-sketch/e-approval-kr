#!/usr/bin/env python3
# hwpx_std.py — 표준 라이브러리만으로 HWPX(OWPML, KS X 6101) 읽기/쓰기. (2026-11-13, Maru D2: stdlib only)
#   쓰기: hwpx_skeleton.hwpx(한컴 오피스 저장본 골격, python-hwpx 배포물 Apache-2.0 — NOTICE 참조)에
#         header.xml 속성(charPr/paraPr/borderFill/글꼴/글머리표)을 덧붙이고 section0.xml을 생성.
#   읽기: zipfile + xml.etree(DTD/ENTITY는 pyexpat 선검사로 거부) → 에디터 HTML(python-hwpx 경로와 같은 규약).
#   python-hwpx/lxml이 있으면 hwpxconv가 실패 시 폴백으로만 사용.
import io, os, re, zipfile, base64, html as htmlmod
import xml.etree.ElementTree as ET
import pyexpat
from hwpxconv import ListHTML, MAX_LIST_LEVEL, zip_put   # 목록 수준 공용(hwpxconv 최상위는 표준 라이브러리만)

APP = os.path.dirname(os.path.abspath(__file__))
SKELETON = os.path.join(APP, "hwpx_skeleton.hwpx")
HU = 75                       # 1px(96dpi) = 75 HWPUNIT
NS = {"hp": "http://www.hancom.co.kr/hwpml/2011/paragraph", "hh": "http://www.hancom.co.kr/hwpml/2011/head",
      "hc": "http://www.hancom.co.kr/hwpml/2011/core", "hs": "http://www.hancom.co.kr/hwpml/2011/section",
      "opf": "http://www.idpf.org/2007/opf/"}
HP = "{%s}" % NS["hp"]; HH = "{%s}" % NS["hh"]; HC = "{%s}" % NS["hc"]
_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿\ud800-\udfff]")
MAX_CELLS = 30000; MAX_IMG_BYTES = 80 * 1024 * 1024; MAX_IMGS = 2000; MAX_DEPTH = 40
MAX_XML = 32 * 1024 * 1024; MAX_ELEMS = 1500000      # XML 파트 하나 크기·요소 수 상한(메모리 DoS 차단)
MAX_READ_IMG = 50 * 1024 * 1024                      # 읽기: 출력 data URI 총량(같은 그림 반복 참조 포함)
MAX_CHARPR = 2000; MAX_FONTS = 64                    # 쓰기: 새 글자모양·글꼴 상한(초과=기본값)
MAX_HU = 2 ** 31 // HU - 1

class HwpxStdError(ValueError):
    """손상/악성/미지원 입력."""

def _x(s): return htmlmod.escape(_BAD.sub("", s or ""), quote=True)
def _e(s): return htmlmod.escape(s or "", quote=True)

def safe_parse(raw):
    if len(raw) > MAX_XML: raise HwpxStdError("XML 파트가 너무 큽니다")
    p = pyexpat.ParserCreate(); cnt = [0]
    def no(*a): raise HwpxStdError("DTD/엔티티 선언 금지(XXE 차단)")
    def st(*a):
        cnt[0] += 1
        if cnt[0] > MAX_ELEMS: raise HwpxStdError("XML 요소가 너무 많습니다")
    p.StartDoctypeDeclHandler = no; p.EntityDeclHandler = no; p.StartElementHandler = st
    try: p.Parse(raw, True)
    except pyexpat.ExpatError: raise HwpxStdError("XML 손상")
    return ET.fromstring(raw)

_IMG_MAGIC = ((b"\x89PNG\r\n\x1a\n", "png"), (b"\xff\xd8\xff", "jpeg"), (b"GIF8", "gif"), (b"BM", "bmp"))
def _sniff(d):
    for m, f in _IMG_MAGIC:
        if d[:len(m)] == m: return f
    return None

# ═════════════════════════════ 쓰기 ═════════════════════════════
_BTYPE = {"solid": "SOLID", "dotted": "DOT", "dashed": "DASH", "double": "DOUBLE_SLIM", "hidden": "NONE", "none": "NONE"}
_MMW = {1: "0.12 mm", 2: "0.4 mm", 3: "0.7 mm"}
_ALIGN = {"LEFT": "LEFT", "CENTER": "CENTER", "RIGHT": "RIGHT", "JUSTIFY": "JUSTIFY"}
_PNFMT = {"1": "DIGIT", "circ": "CIRCLED_DIGIT", "roman": "ROMAN_SMALL", "hangul": "HANGUL_SYLLABLE"}
HEAD = {"h1": 20, "h2": 16, "h3": 13}
_GENERIC = {"sans-serif", "serif", "monospace", "cursive", "fantasy", "system-ui", "notokr", "inherit"}

def _hex6(c):
    c = (c or "").strip().lstrip("#")
    if re.fullmatch(r"[0-9A-Fa-f]{3}", c): c = "".join(ch * 2 for ch in c)
    return ("#" + c.upper()) if re.fullmatch(r"[0-9A-Fa-f]{6}", c) else None

def _font1(css):
    for f in (css or "").split(","):
        f = f.strip().strip("'\"").strip()
        if f and f.lower() not in _GENERIC and re.fullmatch(r"[\w\s가-힣.\-]{1,40}", f): return f
    return None

def _datauri(src):
    m = re.match(r"^data:image/[a-z0-9.+-]+;base64,([A-Za-z0-9+/=\s]+)$", (src or "").strip(), re.I)
    if not m: return None
    try: d = base64.b64decode(re.sub(r"\s+", "", m.group(1)))
    except Exception: return None
    f = _sniff(d)
    return (d, f) if f else None

def _imgsize(d, f):
    try:
        if f == "png": return int.from_bytes(d[16:20], "big"), int.from_bytes(d[20:24], "big")
        if f == "gif": return int.from_bytes(d[6:8], "little"), int.from_bytes(d[8:10], "little")
        if f == "bmp": return int.from_bytes(d[18:22], "little"), abs(int.from_bytes(d[22:26], "little", signed=True))
        if f == "jpeg":
            i = 2
            while i < len(d) - 9:
                if d[i] != 0xFF: i += 1; continue
                if d[i + 1] in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB):
                    return int.from_bytes(d[i + 7:i + 9], "big"), int.from_bytes(d[i + 5:i + 7], "big")
                i += 2 + int.from_bytes(d[i + 2:i + 4], "big")
    except Exception: pass
    return None

def _cl(v, lo=-MAX_HU, hi=MAX_HU):
    try: return max(lo, min(hi, int(v or 0)))
    except (TypeError, ValueError, OverflowError): return 0

class _Writer:
    def __init__(self):
        with zipfile.ZipFile(SKELETON) as z:
            self.files = {n: z.read(n) for n in z.namelist()}
            self.order = z.namelist()
        self.hdr = self.files["Contents/header.xml"].decode("utf-8")
        self.cp0 = re.search(r'<hh:charPr id="0".*?</hh:charPr>', self.hdr, re.S).group(0)
        self.pp0 = re.search(r'<hh:paraPr id="0".*?</hh:paraPr>', self.hdr, re.S).group(0)
        self.next_cp = int(re.search(r'<hh:charProperties itemCnt="(\d+)"', self.hdr).group(1))
        self.next_pp = int(re.search(r'<hh:paraProperties itemCnt="(\d+)"', self.hdr).group(1))
        self.next_bf = int(re.search(r'<hh:borderFills itemCnt="(\d+)"', self.hdr).group(1)) + 1
        self.next_font = int(re.search(r'<hh:fontface lang="HANGUL" fontCnt="(\d+)"', self.hdr).group(1))
        self.cps = {}; self.pps = {}; self.bfs = {}; self.fonts = {}
        self.new_cp = []; self.new_pp = []; self.new_bf = []; self.new_font = []
        self.bins = []; self.ids = 1000; self.img_bytes = 0; self.cells = 0
        self.tbl_bf = self.borderfill({}, None)
        self.textw = 42520

    def nid(self):
        self.ids += 1; return self.ids

    # ── header 항목 ──
    def font(self, name):
        if name in self.fonts: return self.fonts[name]
        if len(self.fonts) >= MAX_FONTS: return None
        fid = self.next_font; self.next_font += 1; self.fonts[name] = fid; self.new_font.append((fid, name))
        return fid

    def charpr(self, r, hsize=None):
        bold = bool(r.get("bold")) or bool(hsize); italic = bool(r.get("italic"))
        und = bool(r.get("underline")); strike = bool(r.get("strike"))
        sup = bool(r.get("sup")); sub = bool(r.get("sub")) and not sup
        size = hsize or r.get("size") or 10
        try: height = max(100, min(40000, int(round(float(size) * 100))))
        except (TypeError, ValueError): height = 1000
        color = _hex6(r.get("color")) or "#000000"; shade = _hex6(r.get("highlight")) or "none"
        fname = _font1(r.get("font")); fid = self.font(fname) if fname else None
        key = (bold, italic, und, strike, height, color, shade, fid, sup, sub)
        if key == (False, False, False, False, 1000, "#000000", "none", None, False, False): return 0
        if key in self.cps: return self.cps[key]
        if len(self.cps) >= MAX_CHARPR: return 0                 # 상한 초과=기본 글자모양
        cid = self.next_cp; self.next_cp += 1; self.cps[key] = cid
        s = re.sub(r'<hh:charPr id="0"', '<hh:charPr id="%d"' % cid, self.cp0, count=1)
        s = re.sub(r'height="\d+"', 'height="%d"' % height, s, count=1)
        s = re.sub(r'textColor="[^"]*"', 'textColor="%s"' % color, s, count=1)
        s = re.sub(r'shadeColor="[^"]*"', 'shadeColor="%s"' % shade, s, count=1)
        if fid is not None:
            s = re.sub(r'<hh:fontRef [^>]*/>', '<hh:fontRef hangul="%d" latin="%d" hanja="%d" japanese="%d" other="%d" symbol="%d" user="%d"/>' % ((fid,) * 7), s, count=1)
        ins = ("<hh:italic/>" if italic else "") + ("<hh:bold/>" if bold else "")
        if ins: s = s.replace("<hh:underline", ins + "<hh:underline", 1)
        if und: s = re.sub(r'<hh:underline type="NONE"', '<hh:underline type="BOTTOM"', s, count=1)
        if strike: s = re.sub(r'<hh:strikeout shape="NONE"', '<hh:strikeout shape="SOLID"', s, count=1)
        if sup or sub: s = s.replace("</hh:charPr>", "<hh:supscript/></hh:charPr>" if sup else "<hh:subscript/></hh:charPr>", 1)   # OWPML 순서: shadow 뒤
        self.new_cp.append(s)
        return cid

    def parapr(self, align=None, heading=None, indent_px=0):
        al = _ALIGN.get((align or "").upper(), "JUSTIFY")
        ind = max(0, min(200000, int(indent_px or 0) * HU))
        key = (al, heading, ind)
        if key == ("JUSTIFY", None, 0): return 0
        if key in self.pps: return self.pps[key]
        if len(self.pps) >= MAX_CHARPR: return 0
        pid = self.next_pp; self.next_pp += 1; self.pps[key] = pid
        s = re.sub(r'<hh:paraPr id="0"', '<hh:paraPr id="%d"' % pid, self.pp0, count=1)
        s = re.sub(r'<hh:align horizontal="\w+"', '<hh:align horizontal="%s"' % al, s, count=1)
        if heading:
            t, ref, lv = heading
            s = re.sub(r'<hh:heading [^>]*/>', '<hh:heading type="%s" idRef="%d" level="%d"/>' % (t, ref, lv), s, count=1)
        if ind: s = s.replace('<hc:left value="0" unit="HWPUNIT"/>', '<hc:left value="%d" unit="HWPUNIT"/>' % ind)
        self.new_pp.append(s)
        return pid

    def borderfill(self, borders, bg):
        key = (tuple(sorted((k, (v[0], round(float(v[1] or 1)), v[2])) for k, v in (borders or {}).items())), bg)
        if key in self.bfs: return self.bfs[key]
        if len(self.bfs) >= MAX_CHARPR: return self.tbl_bf
        bid = self.next_bf; self.next_bf += 1; self.bfs[key] = bid
        sides = []
        for side in ("left", "right", "top", "bottom"):
            t, w, c = "SOLID", "0.12 mm", "#000000"
            if side in (borders or {}):
                st, px, col = borders[side]
                t = _BTYPE.get(st, "SOLID")
                try: w = _MMW.get(max(1, min(3, int(round(float(px or 1))))), "0.12 mm")
                except (TypeError, ValueError): pass
                c = _hex6(col) or "#000000"
            sides.append('<hh:%sBorder type="%s" width="%s" color="%s"/>' % (side, t, w, c))
        fill = ('<hc:fillBrush><hc:winBrush faceColor="%s" hatchColor="#FF000000" alpha="0"/></hc:fillBrush>' % bg) if bg else ""
        self.new_bf.append('<hh:borderFill id="%d" threeD="0" shadow="0" centerLine="NONE" breakCellSeparateLine="0">'
                           '<hh:slash type="NONE" Crooked="0" isCounter="0"/><hh:backSlash type="NONE" Crooked="0" isCounter="0"/>%s'
                           '<hh:diagonal type="SOLID" width="0.1 mm" color="#000000"/>%s</hh:borderFill>' % (bid, "".join(sides), fill))
        return bid

    def binitem(self, data, fmt):
        self.img_bytes += len(data)
        if self.img_bytes > MAX_IMG_BYTES or len(self.bins) >= MAX_IMGS: raise HwpxStdError("그림이 너무 많거나 큽니다")
        ext = {"jpeg": "jpg"}.get(fmt, fmt)
        bid = "BIN%04X" % (len(self.bins) + 1)
        self.bins.append((bid, "BinData/%s.%s" % (bid, ext), data, "image/" + fmt))
        return bid

    # ── 본문 요소 ──
    def pos_xml(self, place, left=0, top=0, align=None, z=0):
        if place == "fixed":
            return ('<hp:pos treatAsChar="0" affectLSpacing="0" flowWithText="0" allowOverlap="1" holdAnchorAndSO="0" vertRelTo="PAPER" horzRelTo="PAPER" '
                    'vertAlign="TOP" horzAlign="LEFT" vertOffset="%d" horzOffset="%d"/>' % (_cl(top) * HU, _cl(left) * HU)), ("BEHIND_TEXT" if _cl(z) < 0 else "IN_FRONT_OF_TEXT")
        if place == "float":
            return ('<hp:pos treatAsChar="0" affectLSpacing="0" flowWithText="1" allowOverlap="0" holdAnchorAndSO="0" vertRelTo="PARA" horzRelTo="COLUMN" '
                    'vertAlign="TOP" horzAlign="%s" vertOffset="0" horzOffset="0"/>' % ("RIGHT" if align == "RIGHT" else "LEFT")), "SQUARE"
        return ('<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="0" holdAnchorAndSO="0" vertRelTo="PARA" horzRelTo="COLUMN" '
                'vertAlign="TOP" horzAlign="%s" vertOffset="0" horzOffset="0"/>' % (align if align in ("CENTER", "RIGHT") else "LEFT")), "TOP_AND_BOTTOM"

    def pic(self, src, wpx=None, place="char", left=0, top=0, z=0, maxpx=567):
        d = _datauri(src)
        if not d: return ""
        data, fmt = d; bid = self.binitem(data, fmt)
        sz = _imgsize(data, fmt) or (200, 150)
        w0, h0 = max(1, sz[0]), max(1, sz[1])
        try: w = int(wpx) if wpx else min(w0, maxpx)
        except (TypeError, ValueError): w = min(w0, maxpx)
        w = max(4, min(w, 5000)); h = max(4, int(round(w * h0 / w0)))
        W, H = w * HU, h * HU; W0, H0 = w0 * HU, h0 * HU; i = self.nid()
        pos, wrap = self.pos_xml(place, left, top, None, z)
        if place != "fixed": wrap = "SQUARE" if place == "float" else "TOP_AND_BOTTOM"
        zo = _cl(_cl(z, -1000, 1000) + 100, 0, 10000) if place == "fixed" else 0
        return ('<hp:pic textWrap="%s" textFlow="BOTH_SIDES" reverse="0" id="%d" zOrder="%d" numberingType="PICTURE" lock="0" dropcapstyle="None" href="" groupLevel="0" instid="%d">'
                '<hp:offset x="%d" y="%d"/><hp:orgSz width="%d" height="%d"/><hp:curSz width="%d" height="%d"/><hp:flip horizontal="0" vertical="0"/>'
                '<hp:rotationInfo angle="0" centerX="%d" centerY="%d" rotateimage="1"/><hp:renderingInfo><hc:transMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>'
                '<hc:scaMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/><hc:rotMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/></hp:renderingInfo>'
                '<hp:imgRect><hc:pt0 x="0" y="0"/><hc:pt1 x="%d" y="0"/><hc:pt2 x="%d" y="%d"/><hc:pt3 x="0" y="%d"/></hp:imgRect>'
                '<hp:imgClip left="0" right="%d" top="0" bottom="%d"/><hp:inMargin left="0" right="0" top="0" bottom="0"/><hp:imgDim dimwidth="%d" dimheight="%d"/>'
                '<hc:img binaryItemIDRef="%s" bright="0" contrast="0" effect="REAL_PIC" alpha="0"/><hp:effects/>'
                '<hp:sz width="%d" height="%d" widthRelTo="ABSOLUTE" heightRelTo="ABSOLUTE" protect="0"/>%s'
                '<hp:outMargin left="0" right="0" top="0" bottom="0"/><hp:shapeComment/></hp:pic>'
                   % (wrap, i, zo, i, _cl(left) * HU if place == "fixed" else 0, _cl(top) * HU if place == "fixed" else 0,
                   W0, H0, W, H, W // 2, H // 2, W, W, H, H, W0, H0, W0, H0, bid, W, H, pos))

    def runs_xml(self, runs, hsize=None, notes=None):
        out = []; lk = None; fid = None
        def close():
            nonlocal lk, fid
            if lk is not None:
                out.append('<hp:run charPrIDRef="0"><hp:ctrl><hp:fieldEnd beginIDRef="%d" fieldid="%d"/></hp:ctrl></hp:run>' % (fid, fid))
                lk = None
        for r in runs:
            link = r.get("link")
            if link != lk:
                close()
                if link:
                    fid = self.nid(); lk = link
                    out.append('<hp:run charPrIDRef="0"><hp:ctrl><hp:fieldBegin id="%d" type="HYPERLINK" name="%s" editable="false" dirty="false" zorder="-1" fieldid="%d">%s</hp:fieldBegin></hp:ctrl></hp:run>'
                               % (fid, _x(link), fid, _link_params(link)))
            if r.get("endnote") is not None:
                txt = (notes or {}).get(r["endnote"], "")
                out.append('<hp:run charPrIDRef="0"><hp:ctrl><hp:endNote number="0" suffixChar="41" instId="%d"><hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" vertAlign="TOP" '
                           'linkListIDRef="0" linkListNextIDRef="0" textWidth="0" textHeight="0" hasTextRef="0" hasNumRef="0"><hp:p id="%d" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">'
                           '<hp:run charPrIDRef="0"><hp:t>%s</hp:t></hp:run></hp:p></hp:subList></hp:endNote></hp:ctrl></hp:run>' % (self.nid(), self.nid(), _x(txt)))
                continue
            if r.get("img"):
                p = self.pic(r["img"], r.get("width"))
                if p: out.append('<hp:run charPrIDRef="0">%s</hp:run>' % p)
                continue
            t = r.get("text", "")
            if not t: continue
            t = t.replace("\r", "").replace("\n", " ").replace("\xa0", " ")
            out.append('<hp:run charPrIDRef="%d"><hp:t>%s</hp:t></hp:run>' % (self.charpr(r, hsize), _x(t).replace("&quot;", '"').replace("&#x27;", "'")))
        close()
        return "".join(out)

    def para(self, inner, ppr=0, pgbreak=False):
        return '<hp:p id="%d" paraPrIDRef="%d" styleIDRef="0" pageBreak="%d" columnBreak="0" merged="0">%s</hp:p>' % (
            self.nid(), ppr, 1 if pgbreak else 0, inner or '<hp:run charPrIDRef="0"><hp:t/></hp:run>')

    def table(self, t, notes):
        rows = t.get("rows") or []
        if not rows: return ""
        R = len(rows); occ = set(); cells = []; ncols = 0
        for ri, row in enumerate(rows):
            c = 0
            for cell in row:
                while (ri, c) in occ: c += 1
                if c >= 256: break
                try:
                    cs = max(1, min(64, 256 - c, int(cell.get("colspan") or 1))); rs = max(1, min(R - ri, int(cell.get("rowspan") or 1)))
                except (TypeError, ValueError): cs = rs = 1
                self.cells += cs * rs
                if self.cells > MAX_CELLS: raise HwpxStdError("표가 너무 큽니다(셀 수 상한)")
                for dr in range(rs):
                    for dc in range(cs): occ.add((ri + dr, c + dc))
                cells.append((ri, c, cs, rs, cell)); c += cs; ncols = max(ncols, c)
        C = max(1, ncols)
        colw = [0] * C
        for ri, c, cs, rs, cell in cells:
            if cs == 1 and cell.get("width") and not colw[c]: colw[c] = _cl(cell["width"], 0, 20000) * HU
        rest = [i for i in range(C) if not colw[i]]
        if rest:
            remain = max(len(rest) * 1500, self.textw - sum(colw))
            for i in rest: colw[i] = remain // len(rest)
        rowh = [(_cl(h, 0, 20000) * HU if h else 0) or 1800 for h in (t.get("rowh") or [])] + [1800] * R
        tw = sum(colw); th = sum(rowh[:R])
        pos, wrap = self.pos_xml(t.get("place") or "char", t.get("left"), t.get("top"), t.get("objalign"))
        o = ['<hp:tbl id="%d" zOrder="0" numberingType="TABLE" textWrap="%s" textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" pageBreak="CELL" repeatHeader="0" '
             'rowCnt="%d" colCnt="%d" cellSpacing="0" borderFillIDRef="%d" noAdjust="0"><hp:sz width="%d" widthRelTo="ABSOLUTE" height="%d" heightRelTo="ABSOLUTE" protect="0"/>%s'
             '<hp:outMargin left="0" right="0" top="0" bottom="0"/><hp:inMargin left="141" right="141" top="141" bottom="141"/>'
             % (self.nid(), wrap, R, C, self.tbl_bf, tw, th, pos)]
        byrow = {}
        for e in cells: byrow.setdefault(e[0], []).append(e)
        for ri in range(R):
            o.append("<hp:tr>")
            for _, c, cs, rs, cell in byrow.get(ri, []):
                bg = _hex6(cell.get("bg"))
                if bg == "#FFFFFF": bg = None
                bf = self.borderfill(cell.get("borders") or {}, bg) if (bg or cell.get("borders")) else self.tbl_bf
                cw = sum(colw[c:c + cs]); ch = sum(rowh[ri:ri + rs])
                cps = [(p.get("runs") or [], p.get("align")) for p in cell.get("paras") or []] or [(cell.get("runs") or [], None)]   # 셀 여러 문단 → hp:p 여러 개
                body = []
                for pi, (pruns, pal) in enumerate(cps):
                    inner = self.runs_xml([r for r in pruns if r.get("text")])
                    if pi == len(cps) - 1:   # 셀 그림=마지막 문단
                        for src, w in cell.get("images") or []:
                            p = self.pic(src, w, maxpx=max(16, cw // HU - 8))
                            if p: inner += '<hp:run charPrIDRef="0">%s</hp:run>' % p
                    body.append(self.para(inner, self.parapr(pal or cell.get("align"))))
                o.append('<hp:tc name="" header="0" hasMargin="0" protect="0" editable="0" dirty="0" borderFillIDRef="%d"><hp:subList id="" textDirection="HORIZONTAL" '
                         'lineWrap="BREAK" vertAlign="CENTER" linkListIDRef="0" linkListNextIDRef="0" textWidth="0" textHeight="0" hasTextRef="0" hasNumRef="0">%s</hp:subList>'
                         '<hp:cellAddr colAddr="%d" rowAddr="%d"/><hp:cellSpan colSpan="%d" rowSpan="%d"/><hp:cellSz width="%d" height="%d"/>'
                         '<hp:cellMargin left="141" right="141" top="141" bottom="141"/></hp:tc>'
                         % (bf, "".join(body), c, ri, cs, rs, cw, ch))
            o.append("</hp:tr>")
        o.append("</hp:tbl>")
        return "".join(o)

    def hf_ctrls(self, meta):
        o = []
        def hf(tag, text, ptype, vert):
            return ('<hp:ctrl><hp:%s id="%d" applyPageType="%s"><hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" vertAlign="%s" linkListIDRef="0" '
                    'linkListNextIDRef="0" textWidth="%d" textHeight="4252" hasTextRef="0" hasNumRef="0">%s</hp:subList></hp:%s></hp:ctrl>'
                    % (tag, self.nid(), ptype, vert, self.textw, text, tag))
        def tp(text, ppr=0): return self.para('<hp:run charPrIDRef="0"><hp:t>%s</hp:t></hp:run>' % _x(text), ppr)
        odd, even = meta.get("header_odd"), meta.get("header_even")
        if odd or even:
            if odd: o.append(hf("header", tp(odd), "ODD", "TOP"))
            if even: o.append(hf("header", tp(even), "EVEN", "TOP"))
        elif meta.get("header"):
            o.append(hf("header", tp(meta["header"]), "BOTH", "TOP"))
        pn = meta.get("pagenum")
        if pn in ("center", "right", "left"):
            fmt = _PNFMT.get(meta.get("pagenum_format") or "1", "DIGIT")
            o.append('<hp:ctrl><hp:pageNum pos="BOTTOM_%s" formatType="%s" sideChar="-"/></hp:ctrl>' % (pn.upper(), fmt))
        elif meta.get("footer"):
            o.append(hf("footer", tp(meta["footer"], self.parapr("CENTER")), "BOTH", "BOTTOM"))
        return "".join(o)

    def build(self, blocks, meta, notes, page=None):
        body = []; first_inner = None; pg = False
        ctrls = self.hf_ctrls(meta)
        for b in blocks:
            t = b.get("tag")
            if t == "pgbreak":
                if body or first_inner is not None:
                    if pg: body.append(self.para("", 0, True))
                    pg = True
                continue
            if t == "table":
                inner = '<hp:run charPrIDRef="0">%s</hp:run>' % self.table(b, notes)
                ppr = self.parapr(b.get("objalign") if (b.get("place") or "char") == "char" else None)
            elif t == "img":
                p = self.pic(b.get("src"), b.get("width"), b.get("place") or "char", b.get("left"), b.get("top"), b.get("z"))
                if not p: continue
                inner = '<hp:run charPrIDRef="0">%s</hp:run>' % p; ppr = 0
            else:
                hs = HEAD.get(t)
                runs = [r for r in b.get("runs") or [] if r.get("text", "") != "" or r.get("endnote") is not None or r.get("img")]
                inner = self.runs_xml(runs, hs, notes)
                heading = None
                if hs: heading = ("OUTLINE", 1, {"h1": 0, "h2": 1, "h3": 2}[t])
                elif b.get("list"):   # 목록 수준 = paraPr heading level(0..8). 한글은 수준별 여백을 따로 안 주므로 왼쪽 여백도 수준×20px
                    lv = max(0, min(MAX_LIST_LEVEL, int(b.get("level") or 0)))
                    heading = ("BULLET" if b["list"] == "bullet" else "NUMBER", 1, lv)
                ppr = self.parapr(b.get("align"), heading, (b.get("indent") or 0) + (20 * heading[2] if heading and heading[0] != "OUTLINE" else 0))
            if first_inner is None and not body and not pg and t not in ("table", "img"):
                first_inner = (inner, ppr); continue
            body.append(self.para(inner, ppr, pg)); pg = False
        if pg: body.append(self.para("", 0, True))
        # 첫 문단(secPr 보유) — 골격 첫 문단 재사용
        sec = self.files["Contents/section0.xml"].decode("utf-8")
        i0 = sec.index("<hp:p "); i1 = sec.index("</hs:sec>")
        firstp = sec[i0:i1]
        secrun_end = firstp.index("</hp:run>") + len("</hp:run>")
        secrun = firstp[:secrun_end]
        if ctrls: secrun = secrun[:-len("</hp:run>")] + ctrls + "</hp:run>"
        if page: secrun = self._page(secrun, page)
        fi, fp = first_inner if first_inner else ('<hp:run charPrIDRef="0"><hp:t/></hp:run>', 0)
        secrun = re.sub(r'paraPrIDRef="\d+"', 'paraPrIDRef="%d"' % fp, secrun, count=1)
        first = secrun + fi + "</hp:p>"
        self.files["Contents/section0.xml"] = (sec[:i0] + first + "".join(body) + sec[i1:]).encode("utf-8")
        self._finish_header(meta)
        return self._zip()

    def _page(self, secrun, page):
        import math, hwpxconv
        P = hwpxconv.PAPERS_MM   # 용지 이름표 단일 원천(편집기 PAPERS·wpmeta data-paper와 동일)
        try:
            w, h = P.get(str(page.get("paper") or ""), P["A4"])
            if page.get("w") and page.get("h"): w, h = float(page["w"]), float(page["h"])
            elif page.get("orient") == "landscape": w, h = h, w
            mg = {k: float(page.get(k, 20) or 0) for k in ("ml", "mr", "mt", "mb")}
            if not all(math.isfinite(v) for v in (w, h, *mg.values())): return secrun   # NaN/Infinity(JSON) → 기본 골격
            w = max(50, min(1000, w)); h = max(50, min(1000, h))
            mg = {k: max(0.0, min(100.0, v)) for k, v in mg.items()}
        except (TypeError, ValueError): return secrun
        mm = lambda v: int(round(v * 7200 / 25.4))
        secrun = re.sub(r'<hp:pagePr [^>]*>', '<hp:pagePr landscape="%s" width="%d" height="%d" gutterType="LEFT_ONLY">' % ("NARROWLY" if w > h else "WIDELY", mm(w), mm(h)), secrun, count=1)
        secrun = re.sub(r'<hp:margin [^>]*/>', '<hp:margin header="4252" footer="4252" gutter="0" left="%d" right="%d" top="%d" bottom="%d"/>'
                        % (mm(mg["ml"]), mm(mg["mr"]), mm(mg["mt"]), mm(mg["mb"])), secrun, count=1)
        self.textw = max(2000, mm(w) - mm(mg["ml"]) - mm(mg["mr"]))
        return secrun

    def _finish_header(self, meta):
        h = self.hdr
        def add(tag, items):
            nonlocal h
            if not items: return
            h = h.replace("</hh:%s>" % tag, "".join(items) + "</hh:%s>" % tag, 1)
            h = re.sub(r'<hh:%s itemCnt="(\d+)"' % tag, lambda m: '<hh:%s itemCnt="%d"' % (tag, int(m.group(1)) + len(items)), h, count=1)
        add("charProperties", self.new_cp); add("paraProperties", self.new_pp); add("borderFills", self.new_bf)
        if self.new_font:
            for lang in ("HANGUL", "LATIN", "HANJA", "JAPANESE", "OTHER", "SYMBOL", "USER"):
                m = re.search(r'<hh:fontface lang="%s" fontCnt="(\d+)">(.*?)</hh:fontface>' % lang, h, re.S)
                if not m: continue
                fonts = "".join('<hh:font id="%d" face="%s" type="TTF" isEmbedded="0"><hh:typeInfo familyType="FCAT_GOTHIC" weight="6" proportion="4" contrast="0" '
                                'strokeVariation="1" armStyle="1" letterform="1" midline="1" xHeight="1"/></hh:font>' % (fid, _x(name)) for fid, name in self.new_font)
                h = h[:m.start()] + '<hh:fontface lang="%s" fontCnt="%d">%s%s</hh:fontface>' % (lang, int(m.group(1)) + len(self.new_font), m.group(2), fonts) + h[m.end():]
        if "<hh:bullets" not in h:
            h = h.replace("</hh:numberings>", '</hh:numberings><hh:bullets itemCnt="1"><hh:bullet id="1" char="•" useImage="0"><hh:paraHead level="0" align="LEFT" useInstWidth="0" '
                          'autoIndent="1" widthAdjust="0" textOffsetType="PERCENT" textOffset="50" numFormat="DIGIT" charPrIDRef="4294967295" checkable="0"/></hh:bullet></hh:bullets>', 1)
        if self.bins and "<hh:binDataList" not in h:   # 구버전 호환: header refList에도 바이너리 목록(python-hwpx·한글 2010~)
            h = h.replace("</hh:refList>", '<hh:binDataList itemCnt="%d">%s</hh:binDataList></hh:refList>' % (len(self.bins), "".join(
                '<hh:binItem id="%d" Type="Embedding" BinData="%s" Format="%s"/>' % (i, p.split("/", 1)[1], p.rsplit(".", 1)[1]) for i, (_, p, _, _) in enumerate(self.bins))), 1)
        st = meta.get("pagenum_start") or ""
        if re.fullmatch(r"\d{1,5}", st) and int(st) >= 2:
            h = re.sub(r'(<hh:beginNum page=")\d+"', r'\g<1>%d"' % int(st), h, count=1)
        self.files["Contents/header.xml"] = h.encode("utf-8")

    def _zip(self):
        hpf = self.files["Contents/content.hpf"].decode("utf-8")
        items = "".join('<opf:item id="%s" href="%s" media-type="%s" isEmbeded="1"/>' % (bid, path, mt) for bid, path, _, mt in self.bins)
        if items: hpf = hpf.replace("</opf:manifest>", items + "</opf:manifest>", 1)
        self.files["Contents/content.hpf"] = hpf.encode("utf-8")
        for bid, path, data, _ in self.bins: self.files[path] = data
        self.files.pop("Preview/PrvImage.png", None)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            zip_put(z, "mimetype", self.files.pop("mimetype"), zipfile.ZIP_STORED)
            for n in [n for n in self.order if n in self.files] + [n for n in self.files if n not in self.order]:
                zip_put(z, n, self.files[n], zipfile.ZIP_STORED if n.startswith("BinData/") else zipfile.ZIP_DEFLATED)
        return buf.getvalue()

def write(blocks, meta, notes, page=None):
    """문서모델 블록(hwpxconv._PP 결과) + meta(_extract_meta) + notes → .hwpx 바이트."""
    return _Writer().build(blocks, meta or {}, notes or {}, page)

# ═════════════════════════════ 읽기 ═════════════════════════════
_BTYPE_REV = {"SOLID": "solid", "DASH": "dashed", "DOT": "dotted", "DOUBLE_SLIM": "double", "DOUBLE": "double", "NONE": "hidden"}
_DEFAULT_FACES = {"함초롬돋움", "함초롬바탕"}

def _safecolor(c):
    c = (c or "").strip()
    return c if re.fullmatch(r"#[0-9A-Fa-f]{6}", c) else None

def _ln(e): return e.tag.rsplit("}", 1)[-1] if isinstance(e.tag, str) else ""

class _Reader:
    def __init__(self, z):
        self.z = z; names = set(z.namelist())
        self.names = names
        self.hdr = safe_parse(z.read("Contents/header.xml")) if "Contents/header.xml" in names else None
        self.fonts = {}; self.cp = {}; self.pp = {}; self.bf = {}
        if self.hdr is not None: self._header()
        self.bin = {}
        hpf = "Contents/content.hpf"
        if hpf in names:
            for it in safe_parse(z.read(hpf)).iter():
                if _ln(it) == "item" and it.get("id") and it.get("href"): self.bin[it.get("id")] = it.get("href")
        self.notes = []; self.img_bytes = 0; self.img_n = 0; self.cells = 0; self.depth = 0; self.imgcache = {}

    def _header(self):
        for ff in self.hdr.iter(HH + "fontface"):
            if ff.get("lang") == "HANGUL":
                for f in ff.findall(HH + "font"): self.fonts[f.get("id")] = f.get("face") or ""
        for c in self.hdr.iter(HH + "charPr"):
            d = {"height": c.get("height"), "color": c.get("textColor"), "shade": c.get("shadeColor")}
            for ch in c:
                n = _ln(ch)
                if n == "bold": d["bold"] = True
                elif n == "italic": d["italic"] = True
                elif n == "underline": d["underline"] = (ch.get("type") or "NONE") != "NONE"
                elif n == "strikeout": d["strike"] = (ch.get("shape") or "NONE") not in ("NONE", "3D")
                elif n == "supscript": d["sup"] = True
                elif n == "subscript": d["sub"] = True
                elif n == "fontRef": d["font"] = self.fonts.get(ch.get("hangul"))
            self.cp[c.get("id")] = d
        for p in self.hdr.iter(HH + "paraPr"):
            d = {}
            for ch in p.iter():
                n = _ln(ch)
                if n == "align": d["align"] = ch.get("horizontal")
                elif n == "heading": d["heading"] = (ch.get("type"), int(ch.get("level")) if re.fullmatch(r"[0-9]{1,2}", ch.get("level") or "") else 0)
                elif n == "left" and "left" not in d:
                    try: d["left"] = int(ch.get("value") or 0)
                    except ValueError: pass
            self.pp[p.get("id")] = d
        for b in self.hdr.iter(HH + "borderFill"):
            ent = {}
            for ch in b.iter():
                n = _ln(ch)
                if n in ("leftBorder", "rightBorder", "topBorder", "bottomBorder"):
                    ent[n[:-6]] = (ch.get("type"), ch.get("width"), ch.get("color"))
                elif n == "winBrush":
                    ent["bg"] = ch.get("faceColor")
            self.bf[b.get("id")] = ent

    def img(self, pic):
        im = None
        for e in pic.iter():
            if _ln(e) == "img": im = e; break
        if im is None: return ""
        bref = im.get("binaryItemIDRef")
        if bref not in self.imgcache:
            href = self.bin.get(bref); res = None
            if href and href.lstrip("/") in self.names:
                d = self.z.read(href.lstrip("/")); f = _sniff(d)
                if f: res = (f, base64.b64encode(d).decode())
            self.imgcache[bref] = res
        if not self.imgcache[bref]: return ""
        f, b64 = self.imgcache[bref]
        self.img_bytes += len(b64); self.img_n += 1             # 참조마다 출력 크기 누적(반복 참조 증폭 차단)
        if self.img_bytes > MAX_READ_IMG or self.img_n > MAX_IMGS: raise HwpxStdError("그림이 너무 많거나 큽니다")
        w = 0
        for e in pic:
            if _ln(e) == "curSz":
                try: w = int(int(e.get("width") or 0) / HU)
                except ValueError: w = 0
        place, left, top, z = "char", 0, 0, 0
        pos = next((e for e in pic if _ln(e) == "pos"), None); tw = pic.get("textWrap")
        if pos is not None and pos.get("treatAsChar") == "0":
            if tw in ("IN_FRONT_OF_TEXT", "BEHIND_TEXT"):
                place = "fixed"; z = -1 if tw == "BEHIND_TEXT" else 0
                off = next((e for e in pic if _ln(e) == "offset"), None)
                try:
                    if off is not None: left = int(round(int(off.get("x") or 0) / HU)); top = int(round(int(off.get("y") or 0) / HU))
                except ValueError: pass
            else: place = "float"
        ws = ("width:%dpx;" % w) if w >= 8 else ""
        if place == "fixed":
            return '<img src="data:image/%s;base64,%s" data-place="fixed" style="left:%dpx;top:%dpx;%s%smax-width:100%%">' % (f, b64, max(0, left), max(0, top), ("z-index:%d;" % z) if z else "", ws)
        if place == "float":
            return '<img src="data:image/%s;base64,%s" data-place="float" style="%smax-width:100%%">' % (f, b64, ws)
        return '<img src="data:image/%s;base64,%s" style="%smax-width:100%%">' % (f, b64, ws)

    def runs(self, p, heading=False, fn=True, out_blocks=None):
        """문단의 run들 → (inner html, 블록개체 목록[(kind, html)])."""
        parts = []; lk = False; objs = []
        for run in p:
            if _ln(run) != "run": continue
            cp = self.cp.get(run.get("charPrIDRef")) or {}
            for ch in run:
                n = _ln(ch)
                if n == "t":
                    txt = [_e(ch.text or "")]
                    for sub in ch:
                        sn = _ln(sub)
                        if sn == "tab": txt.append("&nbsp;&nbsp;&nbsp;&nbsp;")
                        elif sn == "lineBreak": txt.append("<br>")
                        elif sn in ("nbSpace", "fwSpace"): txt.append(" ")
                        elif sn == "hyphen": txt.append("-")
                        txt.append(_e(sub.tail or ""))
                    t = "".join(txt)
                    if t: parts.append(self.fmt(t, cp, heading))
                elif n == "ctrl":
                    for c in ch:
                        cn = _ln(c)
                        if cn == "fieldBegin" and c.get("type") == "HYPERLINK":
                            import hwpxconv   # 한컴형(parameters Path/Command)·name형 공통 판독 + 스킴 화이트리스트
                            u = hwpxconv._field_link_url(c)
                            if u and not lk: parts.append('<a href="%s" rel="noopener noreferrer" title="%s">' % (_e(u), _e(u))); lk = True
                        elif cn == "fieldEnd" and lk: parts.append("</a>"); lk = False
                        elif cn in ("endNote", "footNote") and fn: parts.append(self.note(c))
                elif n in ("endNote", "footNote") and fn: parts.append(self.note(ch))
                elif n == "tbl": objs.append(("table", self.table(ch)))
                elif n == "pic":
                    h = self.img(ch)
                    if h:
                        if 'data-place=' in h: objs.append(("img", h))
                        else: parts.append(h)
                elif n in ("rect", "ellipse", "line"):
                    try:
                        import hwpxconv
                        parts.append(hwpxconv._shape_el_to_html(ch))
                    except Exception: pass
        if lk: parts.append("</a>")
        return "".join(parts), objs

    def note(self, el):
        txt = "".join((t.text or "") for t in el.iter() if _ln(t) == "t")
        self.notes.append(txt); n = len(self.notes)
        return '<sup class="fn" contenteditable="false" data-fn="%d">%d)</sup>' % (n, n)

    def fmt(self, t, cp, heading):
        style = ""
        col = _safecolor(cp.get("color"))
        if col and col.upper() != "#000000": style += "color:%s;" % col
        h = cp.get("height")
        if h and h != "1000" and not heading and str(h).isascii() and str(h).isdigit(): style += "font-size:%dpt;" % round(int(h) / 100)
        sh = _safecolor(cp.get("shade"))
        if sh and sh.upper() != "#FFFFFF": style += "background-color:%s;" % sh
        fname = cp.get("font")
        if fname and fname not in _DEFAULT_FACES and re.fullmatch(r"[\w\s가-힣.\-]{1,40}", fname):
            style += "font-family:'%s','NotoKR',sans-serif;" % fname
        if cp.get("sup"): t = "<sup>%s</sup>" % t
        elif cp.get("sub"): t = "<sub>%s</sub>" % t
        if cp.get("strike"): t = "<s>%s</s>" % t
        if cp.get("bold") and not heading: t = "<b>%s</b>" % t
        if cp.get("italic"): t = "<i>%s</i>" % t
        if cp.get("underline"): t = "<u>%s</u>" % t
        return ('<span style="%s">%s</span>' % (style, t)) if style else t

    def table(self, tbl):
        self.depth += 1
        if self.depth > MAX_DEPTH: raise HwpxStdError("중첩이 너무 깊습니다")
        try: return self._table(tbl)
        finally: self.depth -= 1

    def _table(self, tbl):
        place, left, top, align = "char", 0, 0, None
        pos = next((e for e in tbl if _ln(e) == "pos"), None); tw = tbl.get("textWrap")
        if pos is not None:
            if pos.get("treatAsChar") == "0":
                if tw == "IN_FRONT_OF_TEXT" or pos.get("horzRelTo") == "PAPER":
                    place = "fixed"
                    try: left = int(round(int(pos.get("horzOffset") or 0) / HU)); top = int(round(int(pos.get("vertOffset") or 0) / HU))
                    except ValueError: pass
                else: place = "float"; align = pos.get("horzAlign")
            else: align = pos.get("horzAlign")
        covered = set(); rows = []
        for tr in tbl:
            if _ln(tr) != "tr": continue
            row = []
            for tc in tr:
                if _ln(tc) != "tc": continue
                addr = next((e for e in tc if _ln(e) == "cellAddr"), None); span = next((e for e in tc if _ln(e) == "cellSpan"), None)
                sz = next((e for e in tc if _ln(e) == "cellSz"), None)
                try:
                    r0 = int(addr.get("rowAddr")) if addr is not None else len(rows); c0 = int(addr.get("colAddr")) if addr is not None else len(row)
                    cs = max(1, min(256, int(span.get("colSpan") or 1))) if span is not None else 1
                    rs = max(1, min(5000, int(span.get("rowSpan") or 1))) if span is not None else 1
                except (TypeError, ValueError): r0, c0, cs, rs = len(rows), len(row), 1, 1
                if (r0, c0) in covered: continue                  # 병합으로 가려진 칸(python-hwpx 잔여 셀) 생략
                self.cells += cs * rs
                if self.cells > MAX_CELLS: raise HwpxStdError("표가 너무 큽니다(셀 수 상한)")
                for dr in range(rs):
                    for dc in range(cs):
                        if dr or dc: covered.add((r0 + dr, c0 + dc))
                ent = self.bf.get(tc.get("borderFillIDRef")) or {}
                st = ""
                bg = _safecolor(ent.get("bg"))
                if bg and bg.upper() != "#FFFFFF": st += "background-color:%s;" % bg
                try:
                    wpx = int(int(sz.get("width") or 0) / HU) if sz is not None else 0
                    if wpx > 0: st += "width:%dpx;" % wpx
                except ValueError: pass
                st += _edge_css(ent)
                paras = []; hal = None
                for sl in tc:
                    if _ln(sl) != "subList": continue
                    for p in sl:
                        if _ln(p) != "p": continue
                        pal = (self.pp.get(p.get("paraPrIDRef")) or {}).get("align")
                        if hal is None: hal = pal
                        inner, objs = self.runs(p, fn=False)
                        for kind, h in objs:
                            inner += h if kind == "img" else re.sub(r"<[^>]+>", " ", h)   # 중첩 표=글자로
                        paras.append((inner, pal))
                if hal in ("CENTER", "RIGHT"): st = "text-align:%s;" % hal.lower() + st
                at = (' colspan="%d"' % cs if cs > 1 else "") + (' rowspan="%d"' % rs if rs > 1 else "") + (' style="%s"' % st if st else "")
                if len(paras) > 1:   # 셀 여러 문단 → <p> 각각(셀 정렬과 다른 문단만 정렬 명시)
                    ca = hal if hal in ("CENTER", "RIGHT") else None
                    body = "".join("<p%s>%s</p>" % ((' style="text-align:%s;"' % (a.lower() if a in ("CENTER", "RIGHT") else "justify" if a == "JUSTIFY" else "left"))
                                                    if (a if a in ("CENTER", "RIGHT") else None) != ca else "", x or "<br>") for x, a in paras)
                else: body = paras[0][0] if paras else ""
                row.append("<td%s>%s</td>" % (at, body or "<br>"))
            rows.append(row)
        o = ["<table" + (' data-place="%s"' % place if place != "char" else "") + (' style="left:%dpx;top:%dpx"' % (max(0, left), max(0, top)) if place == "fixed" else "") + ">"]
        for row in rows: o.append("<tr>" + "".join(row) + "</tr>")
        o.append("</table>")
        res = "".join(o)
        if place == "char" and align in ("CENTER", "RIGHT"):
            res = '<div class="objline" style="text-align:%s">%s</div>' % (align.lower(), res)
        return res

    def section(self, sec, out, state):
        for p in sec:
            if _ln(p) != "p": continue
            if p.get("pageBreak") == "1" and out:
                self._close(out, state); out.append('<div class="pgbreak" contenteditable="false"><br></div>')
            pr = self.pp.get(p.get("paraPrIDRef")) or {}
            ht, lv = pr.get("heading") or (None, 0)
            heading = ht == "OUTLINE"
            inner, objs = self.runs(p, heading)
            if objs:
                if inner.strip():
                    self._close(out, state); out.append("<p>%s</p>" % inner)
                for kind, h in objs:
                    self._close(out, state)
                    out.append(h if (kind == "table" or "data-place=" in h) else "<p>%s</p>" % h)
                continue
            tag = "h%d" % (min(lv, 2) + 1) if heading else "p"
            sty = ""
            al = pr.get("align")
            if al in ("CENTER", "RIGHT"): sty += "text-align:%s;" % al.lower()
            elif al in ("JUSTIFY",): pass
            left = pr.get("left") or 0
            if left > 0 and ht not in ("BULLET", "NUMBER"): sty += "margin-left:%dpx;" % min(2000, left // HU)
            if ht in ("BULLET", "NUMBER"):   # heading level = 목록 수준 → 중첩 목록
                if "lh" not in state: state["lh"] = ListHTML(out)
                state["lh"].item("ul" if ht == "BULLET" else "ol", lv, inner or "<br>", (' style="%s"' % sty) if sty else ""); continue
            self._close(out, state)
            out.append("<%s%s>%s</%s>" % (tag, (' style="%s"' % sty) if sty else "", inner or "<br>", tag))

    @staticmethod
    def _close(out, state):
        if state.get("lh"): state["lh"].close()

def _edge_css(ent):
    css = ""
    for side in ("top", "bottom", "left", "right"):
        v = ent.get(side)
        if not v: continue
        st = _BTYPE_REV.get(v[0])
        if st is None: continue
        try:
            mm = float(re.match(r"[\d.]+", v[1] or "0.12").group(0))
        except (AttributeError, ValueError): mm = 0.12
        px = 1 if mm < 0.3 else (2 if mm < 0.55 else 3)
        if st == "solid" and px <= 1: continue
        if st == "hidden": css += "border-%s:1px hidden;" % side
        else: css += "border-%s:%dpx %s %s;" % (side, px, st, _safecolor(v[2]) or "#555555")
    return css

def _link_params(url):   # 한컴 호환 하이퍼링크 매개변수(Path=원문, Command='\\'·':'·';' 이스케이프+옵션 꼬리). http/https/mailto만 — 그 외(tel)는 name 속성만
    low = re.sub(r"[\s\x00-\x1f]+", "", url or "").lower()
    if not re.match(r"^(https?:|mailto:)", low): return ""
    cmd = (url or "").replace("\\", "\\\\").replace(":", "\\:").replace(";", "\\;")
    cat = "HWPHYPERLINK_TYPE_EMAIL" if low.startswith("mailto:") else "HWPHYPERLINK_TYPE_URL"
    return ('<hp:parameters cnt="6" name=""><hp:integerParam name="Prop">0</hp:integerParam>'
            '<hp:stringParam name="Command">%s;1;0;0;</hp:stringParam><hp:stringParam name="Path">%s</hp:stringParam>'
            '<hp:stringParam name="Category">%s</hp:stringParam><hp:stringParam name="TargetType">HWPHYPERLINK_TARGET_HYPERLINK</hp:stringParam>'
            '<hp:stringParam name="DocOpenType">HWPHYPERLINK_JUMP_DONTCARE</hp:stringParam></hp:parameters>' % (_x(cmd), _x(url), cat))

def _safe_link(u):
    u = (u or "").strip()
    low = re.sub(r"[\s\x00-\x1f]+", "", u).lower()
    return u if re.match(r"^(https?:|mailto:|tel:)", low) else None

def read(data):
    """.hwpx 바이트 → 에디터 HTML(머리말·꼬리말 메타는 호출부 _hwpx_meta_html이 덧붙임)."""
    try: z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile: raise HwpxStdError("hwpx(zip) 형식이 아님")
    rd = _Reader(z)
    secs = sorted((n for n in rd.names if re.fullmatch(r"Contents/section\d+\.xml", n)), key=lambda n: int(re.search(r"\d+", n.rsplit("/", 1)[1]).group(0)))
    if not secs: raise HwpxStdError("본문(section) 없음")
    out = []; state = {}
    for n in secs:
        rd.section(safe_parse(z.read(n)), out, state)
    rd._close(out, state)
    if rd.notes:
        ns = "".join('<p class="fn-e" data-fn="%d"><span class="fn-no" contenteditable="false">%d)</span> %s</p>' % (i + 1, i + 1, _e(t)) for i, t in enumerate(rd.notes))
        out.append('<div class="fnotes"><p class="fn-t" contenteditable="false">주석</p>' + ns + "</div>")
    html = "".join(out) or "<p><br></p>"
    return re.sub(r'^<p><br></p>(?=<(?:h[1-3]|table|ul|ol|img|div class="objline"))', '', html)
