#!/usr/bin/env python3
# hanword_model.py — 한워드 문서모델(JSON) · 렌더(model→에디터 HTML) · 편집 연산(ops).
#   표준 라이브러리만(hwpxconv의 HTML 파서 P/_PP 재사용 — python-hwpx는 import 시 선택적).
#   네이티브 편집기(maru-ui richtext)와 웹 편집기가 같은 모델을 공유하기 위한 계층.
#
#   model = {"version":1, "meta":{k:str}, "notes":{id:str}, "blocks":[block...]}
#   block(글 블록) = {"type":"p|h1|h2|h3|li|div", "runs":[run...], "align"?, "list"?(li: bullet|number), "level"?(li: 0..8), "indent"?(px)}
#       li.level = 목록 중첩 수준(없으면 0). 연속 li가 한 목록 묶음 — 수준마다 bullet/number 혼합 가능.
#       render는 표준 중첩형 <ul><li>a<ul><li>b</li></ul></li></ul>, model은 형제형(<ul><li>a</li><ul><li>b</li></ul></ul>)도 수용.
#   run = {"text":str, "bold"?, "italic"?, "underline"?, "strike"?, "sup"?, "sub"?, "color"?, "highlight"?(#RRGGBB), "size"?(pt), "font"?, "link"?}
#       sup/sub = 위/아래 첨자(서로 배타 — 하나를 켜면 다른 하나는 꺼짐). HTML <sup>/<sub>·vertical-align:super|sub,
#       DOCX w:vertAlign superscript|subscript, HWPX charPr hh:supscript|hh:subscript 왕복.
#       | {"img":"data:image/..;base64,..", "width"?, "shape"?}  (글자처럼 그림 — 위치 1칸)
#       | {"endnote":"id"}                                        (미주 표식 — 위치 1칸)
#   block(잠금, P1) = {"type":"table",...} | {"type":"img",...} | {"type":"pgbreak"}
#   위치 = [블록 index, 글자 offset]. offset은 글 블록의 text 길이 기준(개체 run=1칸).
#       표 칸 위치(글 연산 insert_text·delete_range·set_style·split_paragraph) = [표 블록, 줄, 칸, 칸 문단, offset]
#       — 줄·칸 = 격자 좌표(왼쪽 위 0,0, 병합 칸은 왼쪽 위 자리), 칸 문단 = paras 순번(runs 형식 칸은 0).
#   표 연산(D11): insert_table·table_insert/delete_rows·table_insert/delete_cols·table_merge·table_split·table_cell
#       — 격자 좌표 기준. 구조 연산은 짧은 줄 끝 빈 자리를 빈 칸으로 채운 직사각형 격자에서 동작(겹친 병합 = 거절).
#   그림·용지 연산: insert_image·set_image·set_page. 그림 지우기 = delete_block.
import re, json, copy, inspect, threading, html as htmlmod
from html.parser import HTMLParser

import hwpxconv
from hwpxconv import _PP, _extract_fnotes, _safe_link_url, ListHTML, MAX_LIST_LEVEL, PAPERS_MM, _parse_margins

VERSION = 1

class ModelError(ValueError):
    """모델/연산 입력 검증 실패(사용자 입력 문제)."""

# ───────────── 한도 ─────────────
MAX_BLOCKS = 100000
MAX_RUNS = 20000          # 블록당
MAX_TEXT = 1000000        # run 하나
MAX_INSERT = 100000       # insert_text 한 번
MAX_TOTAL = 64 * 1024 * 1024   # 모델 전체(글+그림 data URI) 바이트
MAX_OPS = 10000
MAX_TROWS = 5000; MAX_TCOLS = 256; MAX_TCELLS = 30000
MAX_CPARAS = hwpxconv.MAX_CPARAS   # 셀 하나의 문단 수(파서와 공용)
MAX_WORK = 3000000        # apply 한 번의 run 처리 총량(이차 시간 DoS 차단)
_TL = threading.local()

def _charge(n):
    w = getattr(_TL, "work", None)
    if w is None: return
    _TL.work = w + n
    if _TL.work > MAX_WORK: _fail("작업량 상한 초과(연산 수/문서 크기를 줄이세요)")

TEXT_TYPES = ("p", "h1", "h2", "h3", "li", "div")
LOCKED_TYPES = ("table", "img", "pgbreak")
ALIGNS = ("left", "center", "right", "justify")
BOOL_ATTRS = ("bold", "italic", "underline", "strike", "sup", "sub")
_EXCL = {"sup": "sub", "sub": "sup"}   # 위/아래 첨자 = 서로 배타
STYLE_ATTRS = BOOL_ATTRS + ("color", "highlight", "size", "font", "link")
BSTYLES = ("solid", "dashed", "dotted", "double", "hidden", "none")
_HEX = re.compile(r"^#[0-9A-F]{6}$")
_FONT = re.compile(r"^[^;\"<>\\{}\x00-\x1f]{1,100}$")
_IMG = re.compile(r"^data:image/(png|jpeg|gif|webp|bmp);base64,[A-Za-z0-9+/=]+$")
_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_MKEY = re.compile(r"^[a-z0-9_-]{1,40}$")
_CTRL = re.compile(r"[\x00-\x1f\x7f\ud800-\udfff]")   # 제어문자·고립 서로게이트
_SURR = re.compile(r"[\ud800-\udfff\x00]")
_WS = re.compile(r"[ \t\n\r\f]+")

def _hex6(c):
    c = (c or "").strip().upper()
    if re.match(r"^#[0-9A-F]{3}$", c): c = "#" + "".join(ch * 2 for ch in c[1:])
    return c if _HEX.fullmatch(c) else None

def _align(a):
    a = (a or "").strip().lower()
    return {"start": "left", "end": "right"}.get(a, a) if a in ALIGNS + ("start", "end") else None

# ───────────── HTML → 모델 ─────────────
class _MetaGrab(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True); self.meta = None
    def handle_starttag(self, tag, attrs):
        if self.meta is None and tag == "div" and dict(attrs).get("id") == "wpmeta":
            self.meta = {k[5:]: (v or "") for k, v in attrs if k.startswith("data-") and _MKEY.fullmatch(k[5:])}

def _strip_meta(html):   # 빈 #wpmeta div 전부 제거 — 따옴표 인지 선형 스캔(정규식 역추적 제거, 2026-11-13). 내용 있거나 안 닫힌 것은 보존
    out = []; pos = 0
    for mo in re.finditer(r'<div\s+id="wpmeta"', html):
        i = mo.start()
        if i < pos: continue
        e = hwpxconv._tag_end(html, i)
        if e < 0: break
        k = html.find("</div>", e + 1)
        if k < 0: break
        if html[e + 1:k].strip(): continue
        out.append(html[pos:i]); pos = k + 6
    out.append(html[pos:])
    return "".join(out)

def _run_norm(r):
    if r.get("endnote") is not None:
        return {"endnote": _nid(r["endnote"])}
    if r.get("img"):
        o = {"img": r["img"]}
        if r.get("width"): o["width"] = _clamp(r["width"], 1, 5000)
        if isinstance(r.get("shape"), dict):   # P가 style에서 다시 읽는 w/h는 제외(왕복 동일성)
            o["shape"] = {k: str(v)[:40] for k, v in r["shape"].items() if k in ("kind", "fill", "stroke", "sw")}
        return o
    o = {"text": _CTRL.sub("", _WS.sub(" ", r.get("text", "")).replace("\xa0", " "))[:MAX_TEXT]}
    for k in BOOL_ATTRS:
        if r.get(k): o[k] = True
    if o.get("sup"): o.pop("sub", None)
    c = _hex6(r.get("color"))
    if c: o["color"] = c
    h = _hex6(r.get("highlight"))
    if h and h != "#FFFFFF": o["highlight"] = h
    if r.get("size"):
        try:
            sz = int(r["size"])
            if 1 <= sz <= 400: o["size"] = sz
        except (TypeError, ValueError): pass
    f = (r.get("font") or "").strip()
    if f and _FONT.fullmatch(f): o["font"] = f
    if r.get("link"): o["link"] = r["link"]
    return o

def _clamp(v, lo, hi):
    try: return max(lo, min(hi, int(v)))
    except (TypeError, ValueError): return lo

def _nid(v): return (re.sub(r"[^A-Za-z0-9_-]", "_", str(v)) or "x")[:64]

def _attrs(r): return {k: v for k, v in r.items() if k != "text"}

def normalize_runs(runs):
    """빈 text run 제거 + 같은 서식 연속 run 병합(개체 run은 그대로). 선형 시간."""
    _charge(len(runs))
    out = []; buf = []; cur = None
    def flush():
        if cur is not None: out.append(dict(cur, text="".join(buf)))
    for r in runs:
        if "text" in r:
            if r["text"] == "": continue
            if cur is not None and _attrs(cur) == _attrs(r):
                buf.append(r["text"]); continue
            flush(); cur = r; buf = [r["text"]]; continue
        flush(); cur = None; buf = []
        out.append(r)
    flush()
    return out

def _cruns(runs):
    rs = normalize_runs([_run_norm(r) for r in runs or [] if not r.get("img") and r.get("endnote") is None])
    return rs if "".join(r.get("text", "") for r in rs).strip() else []   # 빈 셀/문단 자리표시(<br>·공백)=빈

def _cell_norm(c):
    o = {"colspan": _clamp(c.get("colspan") or 1, 1, MAX_TCOLS), "rowspan": _clamp(c.get("rowspan") or 1, 1, MAX_TROWS),
         "runs": _cruns(c.get("runs"))}
    bg = _hex6(c.get("bg"))
    if bg: o["bg"] = bg
    al = _align(c.get("align"))
    if al: o["align"] = al
    ps = c.get("paras") or []
    if 2 <= len(ps) <= MAX_CPARAS and o["runs"]:   # 셀 여러 문단(2개 이상만 — 1개·상한 초과는 구형 평탄 runs)
        pn = []
        for p in ps:
            q = {"runs": _cruns(p.get("runs"))}
            pa = _align(p.get("align"))
            if pa and pa != al: q["align"] = pa
            pn.append(q)
        del o["runs"]; o["paras"] = pn
    if c.get("width") and int(c["width"]) > 0: o["width"] = _clamp(c["width"], 0, 10000)
    bd = {}
    for side, v in (c.get("borders") or {}).items():
        if side in ("top", "right", "bottom", "left") and v and v[0] in BSTYLES:
            bd[side] = [v[0], round(max(0.0, min(10.0, float(v[1]) if v[1] is not None else 1.0)), 2), _hex6(v[2])]
    if bd: o["borders"] = bd
    ims = [{"src": s, **({"width": _clamp(w, 1, 5000)} if w and int(w) > 0 else {})} for s, w in (c.get("images") or []) if _IMG.fullmatch(s or "")]
    if ims: o["images"] = ims
    return o

def _block_norm(b):
    t = b.get("tag")
    if t == "pgbreak": return {"type": "pgbreak"}
    if t == "table":
        o = {"type": "table", "rows": [[_cell_norm(c) for c in row] for row in b.get("rows") or []]}
        if b.get("place") in ("fixed", "float"): o["place"] = b["place"]
        for k in ("left", "top", "width"):
            if b.get(k): o[k] = _clamp(b[k], -10000, 10000)
        oa = _align(b.get("objalign"))
        if oa: o["objalign"] = oa
        rh = [_clamp(h or 0, 0, 5000) for h in b.get("rowh") or []][:len(o["rows"])]
        if any(rh): o["rowh"] = rh
        return o
    if t == "img":
        o = {"type": "img", "src": b.get("src") or ""}
        if b.get("place") in ("fixed", "float"): o["place"] = b["place"]
        for k, lo, hi in (("left", -10000, 10000), ("top", -10000, 10000), ("z", -1000, 1000), ("width", 1, 5000)):
            if b.get(k): o[k] = _clamp(b[k], lo, hi)
        return o
    o = {"type": t if t in TEXT_TYPES else "p",
         "runs": normalize_runs([_run_norm(r) for r in b.get("runs") or []])}
    al = _align(b.get("align"))
    if al: o["align"] = al
    if o["type"] == "li":
        o["list"] = b.get("list") if b.get("list") in ("bullet", "number") else "bullet"
        lvl = _clamp(b.get("level"), 0, MAX_LIST_LEVEL)   # 숫자 아님=0(ValueError 대신)
        if lvl: o["level"] = lvl
    if b.get("indent") and int(b["indent"]) > 0: o["indent"] = _clamp(b["indent"], 0, 2000)
    return o

def model(html):
    """에디터 HTML → 모델(dict). 그림 data URI 포함(무손실 왕복용)."""
    g = _MetaGrab(); g.feed(html); g.close()
    meta = {k: v for k, v in (g.meta or {}).items() if _page_meta_ok(k, v)}   # 허용 밖 용지 메타는 버림(render/validate 통과 보장)
    html = _strip_meta(html)
    notes, html = _extract_fnotes(html)
    p = _PP(); p.feed(html); p.close(); p.flush()
    blocks = [_block_norm(b) for b in p.paras]
    blocks = [b for b in blocks if not (b["type"] == "img" and not _IMG.fullmatch(b["src"]))   # 외부/상대 그림 블록은 모델 밖
              and not (b["type"] == "table" and not b["rows"])]
    for b in blocks:
        if "runs" in b: b["runs"] = [r for r in b["runs"] if "img" not in r or _IMG.fullmatch(r["img"])]
    return {"version": VERSION, "meta": meta, "notes": {_nid(k): v[:100000] for k, v in notes.items()}, "blocks": blocks}

def cell_runs(c):
    """셀 글 run 평탄화(문단 사이 " ") — 통계·평문용. 구형 runs 셀은 그대로."""
    if not c.get("paras"): return c.get("runs") or []
    out = []
    for i, p in enumerate(c["paras"]):
        if i: out.append({"text": " "})
        out.extend(p.get("runs") or [])
    return out

# ───────────── 검증 ─────────────
def _fail(msg): raise ModelError(msg)

def _int(v, lo, hi, what):
    if isinstance(v, bool) or not isinstance(v, int) or not (lo <= v <= hi): _fail("%s: %d..%d 정수여야 함" % (what, lo, hi))
    return v

def _check_style(attrs, partial=False):
    """서식 속성 검증. partial=True(set_style)면 None/False=제거 허용."""
    if not isinstance(attrs, dict): _fail("attrs는 객체여야 함")
    if attrs.get("sup") is True and attrs.get("sub") is True: _fail("sup와 sub는 함께 켤 수 없음")
    for k, v in attrs.items():
        if k not in STYLE_ATTRS: _fail("알 수 없는 서식: %s" % k)
        if partial and (v is None or v is False): continue
        if v is None: _fail("%s: null은 set_style에서만(제거)" % k)
        if k in BOOL_ATTRS:
            if v is not True: _fail("%s는 true/false" % k)
        elif k in ("color", "highlight"):
            if not isinstance(v, str) or not _HEX.fullmatch(v): _fail("%s는 #RRGGBB" % k)
        elif k == "size": _int(v, 1, 400, "size")
        elif k == "font":
            if not isinstance(v, str) or not _FONT.fullmatch(v): _fail("font 형식 오류")
        elif k == "link":
            if not isinstance(v, str) or len(v) > 2000 or not _safe_link_url(v): _fail("link는 http(s)/mailto/tel만")

def _check_runs(runs, cell=False):
    if not isinstance(runs, list) or len(runs) > MAX_RUNS: _fail("runs는 배열(최대 %d)" % MAX_RUNS)
    size = 0
    for r in runs:
        if not isinstance(r, dict): _fail("run은 객체")
        if "text" in r:
            if not isinstance(r["text"], str) or not (1 <= len(r["text"]) <= MAX_TEXT): _fail("text 형식/길이 오류(빈 run 금지)")
            if _CTRL.search(r["text"]): _fail("text에 제어문자 금지(줄바꿈은 split_paragraph)")
            _check_style(_attrs(r)); size += len(r["text"].encode("utf-8"))
        elif "img" in r and not cell:
            if set(r) - {"img", "width", "shape"}: _fail("그림 run 키 오류")
            if not isinstance(r["img"], str) or not _IMG.fullmatch(r["img"]): _fail("그림은 data:image(png/jpeg/gif/webp/bmp) base64만")
            if "width" in r: _int(r["width"], 1, 5000, "width")
            if "shape" in r and (not isinstance(r["shape"], dict) or len(r["shape"]) > 10 or
                                 any(not isinstance(k, str) or not re.fullmatch(r"[a-z]{1,10}", k) or not isinstance(v, str) or len(v) > 40 for k, v in r["shape"].items())):
                _fail("shape 형식 오류")
            size += len(r["img"])
        elif "endnote" in r and not cell:
            if set(r) != {"endnote"} or not isinstance(r["endnote"], str) or not _ID.fullmatch(r["endnote"]): _fail("endnote id 오류")
        else: _fail("run 종류 오류(text/img/endnote)")
    return size

def _check_block(b):
    if not isinstance(b, dict): _fail("block은 객체")
    t = b.get("type")
    if t in TEXT_TYPES:
        if set(b) - {"type", "runs", "align", "list", "level", "indent"}: _fail("글 블록 키 오류")
        if "runs" not in b: _fail("글 블록에 runs 필요")
        if "align" in b and b["align"] not in ALIGNS: _fail("align 값 오류")
        if t == "li":
            if b.get("list") not in ("bullet", "number"): _fail("li는 list=bullet|number 필요")
        elif "list" in b or "level" in b: _fail("list/level은 li만")
        if "level" in b: _int(b["level"], 0, MAX_LIST_LEVEL, "level")
        if "indent" in b: _int(b["indent"], 0, 2000, "indent")
        return _check_runs(b.get("runs", []))
    if t == "pgbreak":
        if set(b) != {"type"}: _fail("pgbreak 키 오류")
        return 0
    if t == "img":
        if set(b) - {"type", "src", "place", "left", "top", "z", "width"}: _fail("img 블록 키 오류")
        if not isinstance(b.get("src"), str) or not _IMG.fullmatch(b["src"]): _fail("img src는 data:image base64만")
        if b.get("place", "char") not in ("char", "fixed", "float"): _fail("place 값 오류")
        for k, lo, hi in (("left", -10000, 10000), ("top", -10000, 10000), ("z", -1000, 1000), ("width", 1, 5000)):
            if k in b: _int(b[k], lo, hi, k)
        return len(b["src"])
    if t == "table":
        if set(b) - {"type", "rows", "place", "left", "top", "width", "objalign", "rowh"}: _fail("table 키 오류")
        rows = b.get("rows")
        if not isinstance(rows, list) or not rows or len(rows) > MAX_TROWS: _fail("rows 형식/개수 오류")
        if b.get("place", "char") not in ("char", "fixed", "float"): _fail("place 값 오류")
        for k in ("left", "top", "width"):
            if k in b: _int(b[k], -10000, 10000, k)
        if "objalign" in b and b["objalign"] not in ALIGNS: _fail("objalign 값 오류")
        if "rowh" in b and (not isinstance(b["rowh"], list) or len(b["rowh"]) > len(rows)): _fail("rowh 형식 오류")
        for h in b.get("rowh", []): _int(h, 0, 5000, "rowh")
        size = 0; ncell = 0
        for row in rows:
            if not isinstance(row, list) or len(row) > MAX_TCOLS: _fail("행 형식/열 수 오류")
            for c in row:
                if not isinstance(c, dict) or set(c) - {"colspan", "rowspan", "runs", "paras", "bg", "align", "width", "borders", "images"}: _fail("셀 키 오류")
                cs = _int(c.get("colspan", 1), 1, MAX_TCOLS, "colspan"); rs = _int(c.get("rowspan", 1), 1, MAX_TROWS, "rowspan")
                ncell += cs * min(rs, len(rows))
                if ncell > MAX_TCELLS: _fail("표가 너무 큼")
                if "bg" in c and (not isinstance(c["bg"], str) or not _HEX.fullmatch(c["bg"])): _fail("셀 bg는 #RRGGBB")
                if "align" in c and c["align"] not in ALIGNS: _fail("셀 align 값 오류")
                if "width" in c: _int(c["width"], 0, 10000, "셀 width")
                if "borders" in c and not isinstance(c["borders"], dict): _fail("셀 테두리는 객체")
                for side, v in (c.get("borders") or {}).items():
                    if side not in ("top", "right", "bottom", "left") or not isinstance(v, list) or len(v) != 3 or v[0] not in BSTYLES \
                       or isinstance(v[1], bool) or not isinstance(v[1], (int, float)) or not (0 <= v[1] <= 10) \
                       or not (v[2] is None or (isinstance(v[2], str) and _HEX.fullmatch(v[2]))):
                        _fail("셀 테두리 형식 오류")
                for im in c.get("images") or []:
                    if not isinstance(im, dict) or set(im) - {"src", "width"} or not isinstance(im.get("src"), str) or not _IMG.fullmatch(im["src"]): _fail("셀 그림 형식 오류")
                    if "width" in im: _int(im["width"], 1, 5000, "width")
                    size += len(im["src"])
                if "paras" in c:   # 셀 여러 문단: [{"runs", "align"?}] (runs와 동시 금지)
                    ps = c["paras"]
                    if "runs" in c or not isinstance(ps, list) or not (1 <= len(ps) <= MAX_CPARAS): _fail("셀 paras 형식/개수 오류(runs와 동시 금지)")
                    for p in ps:
                        if not isinstance(p, dict) or "runs" not in p or set(p) - {"runs", "align"}: _fail("셀 문단 키 오류")
                        if "align" in p and p["align"] not in ALIGNS: _fail("셀 문단 align 값 오류")
                        size += _check_runs(p["runs"], cell=True)
                else: size += _check_runs(c.get("runs", []), cell=True)
        return size
    _fail("알 수 없는 블록 type(%s)" % (t if isinstance(t, str) and len(t) < 20 else type(t).__name__))

def _page_meta_ok(k, v):   # 용지 메타(data-paper/orient/margins) 엄격 검증 — 이름 화이트리스트·여백 0~100mm
    if k == "paper": return v in PAPERS_MM
    if k == "orient": return v in ("portrait", "landscape")
    if k == "margins": return _parse_margins(v) is not None
    return True

def validate(m):
    """모델 엄격 검증(형식·범위·크기). 통과 시 m 그대로 반환, 실패 시 ModelError."""
    if not isinstance(m, dict) or not isinstance(m.get("blocks"), list): _fail("모델은 {blocks:[...]} 객체")
    if set(m) - {"version", "meta", "notes", "blocks"}: _fail("모델 최상위 키 오류")
    v = m.get("version", VERSION)
    if isinstance(v, bool) or not isinstance(v, int) or v != VERSION: _fail("지원하지 않는 모델 version")
    if len(m["blocks"]) > MAX_BLOCKS: _fail("블록 과다")
    meta = m.get("meta", {}); notes = m.get("notes", {})
    if not isinstance(meta, dict) or not isinstance(notes, dict): _fail("meta/notes는 객체")
    total = 0
    for k, v in meta.items():
        if not isinstance(k, str) or not _MKEY.fullmatch(k) or not isinstance(v, str) or len(v) > 20 * 1024 * 1024 or _SURR.search(v): _fail("meta 항목 오류")
        if not _page_meta_ok(k, v): _fail("용지 메타 오류(%s)" % k)
        total += len(v)
    for k, v in notes.items():
        if not isinstance(k, str) or not _ID.fullmatch(k) or not isinstance(v, str) or len(v) > 100000 or _SURR.search(v): _fail("notes 항목 오류")
        total += len(v)
    for b in m["blocks"]:
        total += _check_block(b)
        if total > MAX_TOTAL: _fail("문서가 너무 큼(%dMB 상한)" % (MAX_TOTAL >> 20))
    _TL.size = total
    return m

# ───────────── 모델 → HTML ─────────────
def _e(s): return htmlmod.escape(s, quote=True)

_SP2 = re.compile(r"(?<= ) ")
def _text_html(text, prev_space, last):
    """공백 보존: 문단 처음/끝·연속 공백은 &nbsp;(브라우저 공백 접힘 방지). 정규식 일괄 처리(대용량 선형)."""
    if not text: return "", prev_space
    h = _SP2.sub("\x01", text)                       # 앞 글자가 공백인 공백 → 표식
    if prev_space and h[0] == " ": h = "\x01" + h[1:]
    if last and h[-1] == " ": h = h[:-1] + "\x01"
    return _e(h).replace("\x01", "&nbsp;"), text[-1] == " "

def _runs_html(runs, noteno):
    out = []; prev_space = True   # 문단 시작=선행 공백도 보존
    last_text = max((i for i, r in enumerate(runs) if "text" in r), default=-1)
    i = 0
    while i < len(runs):
        r = runs[i]
        if "endnote" in r:
            nid = r["endnote"]; n = noteno.setdefault(nid, len(noteno) + 1)
            out.append('<sup class="fn" contenteditable="false" data-fn="%s">%d)</sup>' % (_e(nid), n)); prev_space = False; i += 1; continue
        if "img" in r:
            st = ("width:%dpx;" % r["width"]) if r.get("width") else ""
            sh = "".join(' data-shape-%s="%s"' % (k, _e(v)) for k, v in (r.get("shape") or {}).items())
            out.append('<img src="%s"%s%s>' % (r["img"], (' style="%s"' % st) if st else "", sh)); prev_space = False; i += 1; continue
        link = r.get("link"); grp = []
        while i < len(runs) and "text" in runs[i] and runs[i].get("link") == link:
            grp.append((i, runs[i])); i += 1
        parts = []
        for j, g in grp:
            h, prev_space = _text_html(g["text"], prev_space, j == last_text)
            for tag, k in (("sup", "sup"), ("sub", "sub"), ("s", "strike"), ("u", "underline"), ("i", "italic"), ("b", "bold")):
                if g.get(k): h = "<%s>%s</%s>" % (tag, h, tag)
            st = ""
            if g.get("color"): st += "color:%s;" % g["color"]
            if g.get("size"): st += "font-size:%dpt;" % g["size"]
            if g.get("highlight"): st += "background-color:%s;" % g["highlight"]
            if g.get("font"): st += "font-family:%s;" % g["font"]
            if st: h = '<span style="%s">%s</span>' % (_e(st), h)
            parts.append(h)
        h = "".join(parts)
        if link: h = '<a href="%s" rel="noopener noreferrer">%s</a>' % (_e(link), h)
        out.append(h)
    return "".join(out)

def _block_style(b):
    st = ""
    if b.get("align"): st += "text-align:%s;" % b["align"]
    if b.get("indent"): st += "margin-left:%dpx;" % b["indent"]
    return (' style="%s"' % st) if st else ""

def _table_html(b, noteno):
    st = "".join("%s:%dpx;" % (k, b[k]) for k in ("left", "top", "width") if k in b)
    o = ['<table%s%s>' % ((' data-place="%s"' % b["place"]) if b.get("place") else "", (' style="%s"' % st) if st else "")]
    rowh = b.get("rowh") or []
    for ri, row in enumerate(b["rows"]):
        o.append('<tr style="height:%dpx">' % rowh[ri] if ri < len(rowh) and rowh[ri] else "<tr>")
        for c in row:
            at = ""
            if c.get("colspan", 1) > 1: at += ' colspan="%d"' % c["colspan"]
            if c.get("rowspan", 1) > 1: at += ' rowspan="%d"' % c["rowspan"]
            cs = ""
            if c.get("bg"): cs += "background-color:%s;" % c["bg"]
            if c.get("align"): cs += "text-align:%s;" % c["align"]
            if c.get("width"): cs += "width:%dpx;" % c["width"]
            for side, (sty, px, col) in (c.get("borders") or {}).items():
                cs += "border-%s:%spx %s%s;" % (side, ("%.2f" % px).rstrip("0").rstrip(".") or "0", sty, (" " + col) if col else "")
            if cs: at += ' style="%s"' % cs
            if c.get("paras"):
                inner = "".join("<p%s>%s</p>" % ((' style="text-align:%s;"' % p["align"]) if p.get("align") else "", _runs_html(p["runs"], noteno) or "<br>") for p in c["paras"])
            else: inner = _runs_html(c.get("runs") or [], noteno)
            inner += "".join('<img src="%s"%s>' % (im["src"], (' style="width:%dpx"' % im["width"]) if im.get("width") else "") for im in c.get("images") or [])
            o.append("<td%s>%s</td>" % (at, inner or "<br>"))
        o.append("</tr>")
    o.append("</table>")
    h = "".join(o)
    if b.get("objalign"): h = '<div class="objline" style="text-align:%s">%s</div>' % (b["objalign"], h)
    return h

def render(m):
    """모델 → 에디터 HTML(웹 편집기에 그대로 넣을 수 있는 형식). 검증 실패 시 ModelError."""
    validate(m)
    out = []; noteno = {}; lst = ListHTML(out)
    for b in m["blocks"]:
        t = b["type"]
        if t == "li":
            lst.item("ul" if b["list"] == "bullet" else "ol", b.get("level", 0), _runs_html(b.get("runs") or [], noteno), _block_style(b)); continue
        lst.close()
        if t in TEXT_TYPES:
            out.append("<%s%s>%s</%s>" % (t, _block_style(b), _runs_html(b.get("runs") or [], noteno) or "<br>", t))
        elif t == "pgbreak":
            out.append('<div class="pgbreak" contenteditable="false"><br></div>')
        elif t == "img":
            st = "".join("%s:%dpx;" % (k if k != "z" else "z-index", b[k]) for k in ("left", "top", "z", "width") if k in b)
            out.append('<img src="%s"%s%s>' % (b["src"], (' data-place="%s"' % b["place"]) if b.get("place") else "", (' style="%s"' % st) if st else ""))
        elif t == "table":
            out.append(_table_html(b, noteno))
    lst.close()
    notes = m.get("notes") or {}
    if notes:
        order = list(noteno) + [k for k in notes if k not in noteno]
        ns = "".join('<p class="fn-e" data-fn="%s"><span class="fn-no" contenteditable="false">%d)</span> %s</p>'
                     % (_e(k), noteno.get(k, i + 1), _e(notes[k])) for i, k in enumerate(order) if k in notes)
        out.append('<div class="fnotes"><p class="fn-t" contenteditable="false">주석</p>%s</div>' % ns)
    meta = m.get("meta") or {}
    if meta:
        out.append('<div id="wpmeta" hidden=""%s></div>' % "".join(' data-%s="%s"' % (k, _e(v)) for k, v in meta.items()))
    return "".join(out) or "<p><br></p>"

# ───────────── 편집 연산 ─────────────
def block_len(b):
    return sum(len(r["text"]) if "text" in r else 1 for r in b.get("runs") or [])

def block_text(b, obj="￼"):
    return "".join(r["text"] if "text" in r else obj for r in b.get("runs") or [])

def _split_at(runs, off):
    """runs를 off 위치에서 나눔 → (앞, 뒤). 개체 run=1칸."""
    pos = 0
    for i, r in enumerate(runs):
        n = len(r["text"]) if "text" in r else 1
        if off <= pos: return runs[:i], runs[i:]
        if off < pos + n:   # text run 내부(개체는 n=1이라 여기 안 옴)
            return runs[:i] + [dict(r, text=r["text"][:off - pos])], [dict(r, text=r["text"][off - pos:])] + runs[i + 1:]
        pos += n
    return runs[:], []

def _pos(m, p, what="pos", allow_end_block=False):
    if not isinstance(p, (list, tuple)) or len(p) != 2: _fail("%s는 [블록, offset]" % what)
    bi, off = p
    nb = len(m["blocks"])
    _int(bi, 0, nb if allow_end_block else nb - 1, what + " 블록")
    if bi == nb: return bi, _int(off, 0, 0, what + " offset")
    b = m["blocks"][bi]
    if b["type"] in TEXT_TYPES: _int(off, 0, block_len(b), what + " offset")
    else: _int(off, 0, 0, what + " offset(잠금 블록은 0만)")
    return bi, off

def _text_block(m, bi, what="블록"):
    b = m["blocks"][bi]
    if b["type"] not in TEXT_TYPES: _fail("%s %d은 잠금 블록(%s) — 글 편집 불가" % (what, bi, b["type"]))
    return b

def _set_attrs(st, attrs):   # 서식 적용(None/False=제거) — sup/sub는 서로 끔
    for k, v in attrs.items():
        if v is None or v is False: st.pop(k, None)
        else:
            st[k] = v
            if k in _EXCL: st.pop(_EXCL[k], None)

def _grow(nbytes, b=None):   # apply 중 누적 크기·run 수 상한(마지막 validate 전에 폭주 차단)
    _TL.size = getattr(_TL, "size", 0) + nbytes
    if _TL.size > MAX_TOTAL: _fail("문서가 너무 큼(%dMB 상한)" % (MAX_TOTAL >> 20))
    if b is not None and len(b.get("runs") or []) > MAX_RUNS: _fail("run 과다")

def _cmp(a, b): return (a[0], a[1]) < (b[0], b[1])

# ── 표 칸 위치 [표 블록, 줄, 칸, 칸 문단, offset] ──
def _is_cpos(p): return isinstance(p, (list, tuple)) and len(p) == 5

def _cell_paras(c):   # 칸 → 문단 목록(paras 형식 = 그 목록 그대로, runs 형식 = 문단 1개 감싸개). 복사 없음 — apply가 깊은 사본에서 동작
    _charge(1)
    if c.get("paras"): return c["paras"]
    return [{"runs": c.get("runs") or []}]

def _cell_store(c, paras, touched=None):
    """문단 목록 → 칸(1개 = runs, 2개 이상 = paras). touched = 바뀐 문단 번호(None = 전부) — 그 문단만 정규화(작업량 ∝ 바뀐 양).
       1개로 줄면 그 문단 정렬이 칸 정렬이 됨(정렬 잃지 않음)."""
    if len(paras) > MAX_CPARAS: _fail("칸 문단 과다(최대 %d)" % MAX_CPARAS)
    for i in (range(len(paras)) if touched is None else touched):
        if 0 <= i < len(paras):
            q = paras[i]; q["runs"] = normalize_runs(q.get("runs") or []); _grow(0, q)
            if q.get("align") and q["align"] == c.get("align"): del q["align"]
    if len(paras) == 1:
        q = paras[0]; c.pop("paras", None); c["runs"] = q["runs"]
        if q.get("align"): c["align"] = q["align"]
    else:
        c.pop("runs", None)
        if c.get("paras") is not paras: c["paras"] = paras

def _cpos(m, p, what):
    if not _is_cpos(p): _fail("%s는 [블록, offset] 또는 [블록, 줄, 칸, 문단, offset]" % what)
    bi = _int(p[0], 0, len(m["blocks"]) - 1, what + " 블록")
    return (bi, p[1], p[2]), _cell_at(m, bi, p[1], p[2], what), p[3], p[4]

def _cell_ctx(m, start, end=None, swhat="pos"):
    """칸 위치 검증 → (칸, 문단 목록, (문단, offset)[, (문단, offset)]). end가 있으면 같은 칸이어야 함."""
    key, cell, pi, off = _cpos(m, start, swhat)
    paras = _cell_paras(cell)
    _int(pi, 0, len(paras) - 1, swhat + " 문단"); _int(off, 0, block_len(paras[pi]), swhat + " offset")
    if end is None: return cell, paras, (pi, off)
    if not _is_cpos(end): _fail("start와 end는 같은 형식(둘 다 칸 위치)이어야 함")
    key2, _, pj, offj = _cpos(m, end, "end")
    if key2 != key: _fail("start와 end는 같은 칸이어야 함")
    _int(pj, 0, len(paras) - 1, "end 문단"); _int(offj, 0, block_len(paras[pj]), "end offset")
    return cell, paras, (pi, off), (pj, offj)

def _no_cpos(p, what):
    if _is_cpos(p): _fail("표 칸 안에는 %s 넣을 수 없음" % what)

def _ins(b, off, text, attrs):
    if not isinstance(text, str) or not (1 <= len(text) <= MAX_INSERT): _fail("text는 1..%d자" % MAX_INSERT)
    if _CTRL.search(text): _fail("text에 제어문자 금지(줄바꿈은 split_paragraph)")
    head, tail = _split_at(b["runs"], off)
    base = next((r for r in reversed(head) if "text" in r), None) or next((r for r in tail if "text" in r), None) or {}
    st = _attrs(base) if base else {}
    if attrs is not None:
        _check_style(attrs, partial=True); _set_attrs(st, attrs)
    b["runs"] = normalize_runs(head + [dict(st, text=text)] + tail)
    _grow(len(text.encode("utf-8")), b)

def op_insert_text(m, pos, text, attrs=None):
    if _is_cpos(pos):
        cell, paras, (pi, off) = _cell_ctx(m, pos)
        _ins(paras[pi], off, text, attrs); _cell_store(cell, paras, (pi,)); return
    bi, off = _pos(m, pos); b = _text_block(m, bi)
    _ins(b, off, text, attrs)

def op_delete_range(m, start, end):
    if _is_cpos(start) or _is_cpos(end):
        if not _is_cpos(start): _fail("start와 end는 같은 형식(둘 다 칸 위치)이어야 함")
        cell, paras, s, e = _cell_ctx(m, start, end, "start")
        if not _cmp(s, e): _fail("start < end 이어야 함")
        head, _ = _split_at(paras[s[0]]["runs"], s[1]); _, tail = _split_at(paras[e[0]]["runs"], e[1])
        paras[s[0]]["runs"] = head + tail; _charge(e[0] - s[0]); del paras[s[0] + 1:e[0] + 1]
        _cell_store(cell, paras, (s[0],)); return
    s = _pos(m, start, "start"); e = _pos(m, end, "end")
    if not _cmp(s, e): _fail("start < end 이어야 함")
    sb = _text_block(m, s[0], "start"); eb = _text_block(m, e[0], "end")
    head, _ = _split_at(sb["runs"], s[1]); _, tail = _split_at(eb["runs"], e[1])
    sb["runs"] = normalize_runs(head + tail)
    del m["blocks"][s[0] + 1:e[0] + 1]

def _style_range(blocks, s, e, attrs):
    for bi in range(s[0], e[0] + 1):
        b = blocks[bi]; _charge(1)
        if b.get("type", "p") not in TEXT_TYPES: continue
        a = s[1] if bi == s[0] else 0; z = e[1] if bi == e[0] else block_len(b)
        head, rest = _split_at(b["runs"], a); mid, tail = _split_at(rest, z - a)
        new = []
        for r in mid:
            if "text" in r: r = dict(r); _set_attrs(r, attrs)
            new.append(r)
        b["runs"] = normalize_runs(head + new + tail); _grow(0, b)

def op_set_style(m, start, end, attrs):
    if _is_cpos(start) or _is_cpos(end):
        if not _is_cpos(start): _fail("start와 end는 같은 형식(둘 다 칸 위치)이어야 함")
        cell, paras, s, e = _cell_ctx(m, start, end, "start")
        if not _cmp(s, e): _fail("start < end 이어야 함")
        _check_style(attrs, partial=True)
        _style_range(paras, s, e, attrs); _cell_store(cell, paras, range(s[0], e[0] + 1)); return
    s = _pos(m, start, "start"); e = _pos(m, end, "end")
    if not _cmp(s, e): _fail("start < end 이어야 함")
    _check_style(attrs, partial=True)
    _style_range(m["blocks"], s, e, attrs)

def op_set_block_type(m, index, type, list=None, **kw):
    bi = _int(index, 0, len(m["blocks"]) - 1, "index"); b = _text_block(m, bi)
    if type not in TEXT_TYPES: _fail("type은 %s 중 하나" % "/".join(TEXT_TYPES))
    if set(kw) - {"align", "indent", "level"}: _fail("set_block_type 인자 오류")
    if "level" in kw and type != "li": _fail("level은 li만")
    if list is not None and list not in ("bullet", "number"): _fail("list는 bullet|number")
    nb = {"type": type, "runs": b["runs"]}
    if type == "li":
        lv = list or b.get("list") or "bullet"
        if lv not in ("bullet", "number"): _fail("list는 bullet|number")
        nb["list"] = lv
        lvl = kw.pop("level", b.get("level", 0) if b["type"] == "li" else 0)
        if _int(lvl, 0, MAX_LIST_LEVEL, "level"): nb["level"] = lvl
    for k in ("align", "indent"):
        v = kw.get(k, b.get(k))
        if v is None: continue
        if k == "align" and v not in ALIGNS: _fail("align 값 오류")
        if k == "indent": _int(v, 0, 2000, "indent")
        nb[k] = v
    m["blocks"][bi] = nb

def op_split_paragraph(m, pos):
    if _is_cpos(pos):
        cell, paras, (pi, off) = _cell_ctx(m, pos)
        if len(paras) >= MAX_CPARAS: _fail("칸 문단 과다(최대 %d)" % MAX_CPARAS)
        head, tail = _split_at(paras[pi]["runs"], off)
        paras[pi]["runs"] = head; paras.insert(pi + 1, dict(paras[pi], runs=tail))
        _cell_store(cell, paras, (pi, pi + 1)); return
    bi, off = _pos(m, pos); b = _text_block(m, bi)
    head, tail = _split_at(b["runs"], off)
    nb = {k: v for k, v in b.items() if k != "runs"}
    if b["type"] in ("h1", "h2", "h3") and not tail: nb = {"type": "p", **({"align": b["align"]} if b.get("align") else {})}
    nb["runs"] = normalize_runs(tail)
    b["runs"] = normalize_runs(head)
    m["blocks"].insert(bi + 1, nb)

def op_merge_paragraph(m, index):
    bi = _int(index, 0, len(m["blocks"]) - 2, "index")
    a = _text_block(m, bi); b = _text_block(m, bi + 1)
    a["runs"] = normalize_runs(a["runs"] + b["runs"])
    del m["blocks"][bi + 1]

def _insert_obj(m, pos, blk):   # 잠금 블록을 pos에 넣음(글 블록 중간이면 문단을 나눔, 처음이면 앞, 끝이면 뒤)
    bi, off = _pos(m, pos, allow_end_block=True)
    if bi < len(m["blocks"]) and m["blocks"][bi]["type"] in TEXT_TYPES and 0 < off:
        if off < block_len(m["blocks"][bi]): op_split_paragraph(m, [bi, off])
        bi += 1
    m["blocks"].insert(bi, blk)

def op_insert_pgbreak(m, pos):
    _no_cpos(pos, "쪽 나눔을")
    _insert_obj(m, pos, {"type": "pgbreak"})

def op_delete_block(m, index):   # 글·표·그림·쪽 나눔 블록 공통(그림 지우기 = 이것)
    bi = _int(index, 0, len(m["blocks"]) - 1, "index")
    del m["blocks"][bi]

def op_insert_block(m, index, block):
    bi = _int(index, 0, len(m["blocks"]), "index")
    _grow(_check_block(block))
    m["blocks"].insert(bi, copy.deepcopy(block))

# ───────────── 표 연산(D11) ─────────────
#   격자 = HTML 표 배치 규칙(칸은 위 줄 rowspan이 차지한 자리를 건너뜀, rowspan은 표 끝에서 자름).
#   칸 목록 원소 = [줄, 칸, rowspan, colspan, cell]. 구조 연산은 짧은 줄 끝 빈 자리를 빈 칸으로 채운 뒤 동작.
MAX_TNEW_ROWS = 200; MAX_TNEW_COLS = 64; MAX_TINS = 100

def _ecell(tmpl=None):   # 빈 칸(tmpl이 있으면 테두리·정렬만 이어받음 — 글·배경·그림·너비는 아님)
    c = {"colspan": 1, "rowspan": 1, "runs": []}
    if tmpl:
        if tmpl.get("align"): c["align"] = tmpl["align"]
        if tmpl.get("borders"): c["borders"] = copy.deepcopy(tmpl["borders"])
    return c

def _table(m, index):
    bi = _int(index, 0, len(m["blocks"]) - 1, "index")
    b = m["blocks"][bi]
    if b["type"] != "table": _fail("블록 %d은 표가 아님(%s)" % (bi, b["type"]))
    return b

def _tgrid(b):
    """표 → (칸 목록, 차지한 자리 set, 줄 수, 격자 칸 수). 비용 = 칸 면적 합(validate 상한 MAX_TCELLS). 겹침 = 거절."""
    rows = b["rows"]; nr = len(rows); occ = set(); an = []; nc = 0
    _charge(nr)
    for r, row in enumerate(rows):
        c = 0
        for cell in row:
            while (r, c) in occ: c += 1
            rs = min(cell.get("rowspan", 1), nr - r); cs = cell.get("colspan", 1)
            _charge(rs * cs)
            for i in range(r, r + rs):
                for j in range(c, c + cs):
                    if (i, j) in occ: _fail("표 칸이 겹침(병합 구조 오류) — 편집 불가")
                    occ.add((i, j))
            an.append([r, c, rs, cs, cell]); c += cs
            if c > nc: nc = c
    return an, occ, nr, nc

def _cell_at(m, bi, r, c, what="칸"):
    b = m["blocks"][bi]
    if b["type"] != "table": _fail("%s: 블록 %d은 표가 아님" % (what, bi))
    _int(r, 0, MAX_TROWS, what + " 줄"); _int(c, 0, MAX_TCOLS, what + " 칸")
    gc = getattr(_TL, "gcache", None); rows = b["rows"]
    ent = gc.get(id(rows)) if gc is not None else None
    if ent is None or ent[0] is not rows:   # apply 안 캐시: 구조 연산은 rows 목록을 새로 만들어(_tbuild) 저절로 무효
        ent = (rows, {(a[0], a[1]): a[4] for a in _tgrid(b)[0]})
        if gc is not None: gc[id(rows)] = ent   # rows 참조를 쥐어 id 재사용 없음
    cell = ent[1].get((r, c))
    if cell is not None: return cell
    _fail("%s: (%d,%d)에 칸 없음(병합 칸은 왼쪽 위 좌표)" % (what, r, c))

def _tfull(b):
    an, occ, nr, nc = _tgrid(b)
    if nr * nc > MAX_TCELLS: _fail("표가 너무 큼")
    _charge(nr * nc)
    for r in range(nr):
        for c in range(nc):
            if (r, c) not in occ: an.append([r, c, 1, 1, _ecell()])
    return an, nr, nc

def _tbuild(b, an, nr):
    if nr > MAX_TROWS: _fail("표 줄 과다(최대 %d)" % MAX_TROWS)
    starts = set(a[0] for a in an)
    cnt = [0] * (nr + 1)   # cnt[i] = i 앞의 빈 줄(시작하는 칸 없음 = 위 병합이 다 덮은 줄) 수 — 빈 줄은 빼고 rowspan 줄임(DOCX·HWPX 빈 행 방지)
    for i in range(nr): cnt[i + 1] = cnt[i] + (i not in starts)
    nn = nr - cnt[nr]
    rows = [[] for _ in range(nn)]
    for r, c, rs, cs, cell in sorted(an, key=lambda a: (a[0], a[1])):
        cell["rowspan"] = rs - (cnt[r + rs] - cnt[r]); cell["colspan"] = cs; rows[r - cnt[r]].append(cell)
    b["rows"] = rows
    if "rowh" in b:
        rh = b["rowh"][:nr]
        b["rowh"] = [h for i, h in enumerate(rh) if i in starts]
        if not any(b["rowh"]): del b["rowh"]

def _colw(an, nc):   # 격자 칸 너비(px, 실수) — 칸 width가 하나도 없으면 None
    if not any(a[4].get("width") for a in an): return None
    w = [0.0] * nc
    for r, c, rs, cs, cell in an:
        if cs == 1 and cell.get("width") and not w[c]: w[c] = float(cell["width"])
    for r, c, rs, cs, cell in an:
        if cs > 1 and cell.get("width"):
            for j in range(c, c + cs):
                if not w[j]: w[j] = cell["width"] / cs
    known = [x for x in w if x]
    avg = sum(known) / len(known)
    return [x or avg for x in w]

def _setw(an, w, total):   # 표 너비(total) 유지하도록 비례 조정 → 칸 width = 걸친 격자 칸 너비 합
    s = sum(w) or 1.0; w = [x * total / s for x in w]
    for r, c, rs, cs, cell in an:
        cell["width"] = max(1, min(10000, int(round(sum(w[c:c + cs])))))

def _tmpl_line(an, idx, axis):   # 기준 줄(axis=0)/칸(axis=1) idx에서 각 자리를 차지한 칸
    t = {}
    for a in an:
        if a[axis] <= idx < a[axis] + a[2 + axis]:
            for j in range(a[1 - axis], a[1 - axis] + a[3 - axis]): t[j] = a[4]
    return t

def op_insert_table(m, pos, rows, cols):
    _no_cpos(pos, "표를")
    nr = _int(rows, 1, MAX_TNEW_ROWS, "rows"); nc = _int(cols, 1, MAX_TNEW_COLS, "cols")
    _charge(nr * nc)
    _insert_obj(m, pos, {"type": "table", "rows": [[_ecell() for _ in range(nc)] for _ in range(nr)]})

def op_table_insert_rows(m, index, at, n=1):
    b = _table(m, index); n = _int(n, 1, MAX_TINS, "n")
    an, nr, nc = _tfull(b); at = _int(at, 0, nr, "at")
    if (nr + n) * nc > MAX_TCELLS: _fail("표가 너무 큼")
    tr = at - 1 if at > 0 else 0
    tmpl = _tmpl_line(an, tr, 0); cov = set()
    for a in an:
        r, c, rs, cs, _ = a
        if r < at < r + rs: a[2] += n; cov.update(range(c, c + cs))   # 병합 칸 안쪽에 넣음 = rowspan 늘림
        elif r >= at: a[0] += n
    for i in range(at, at + n):
        for j in range(nc):
            if j not in cov: an.append([i, j, 1, 1, _ecell(tmpl.get(j))])
    if b.get("rowh"):
        rh = b["rowh"] + [0] * (nr - len(b["rowh"])); b["rowh"] = rh[:at] + [rh[tr]] * n + rh[at:]
    _tbuild(b, an, nr + n)

def op_table_delete_rows(m, index, at, n=1):
    b = _table(m, index)
    an, nr, nc = _tfull(b); at = _int(at, 0, nr - 1, "at"); n = _int(n, 1, nr - at, "n")
    if n >= nr: _fail("마지막 줄은 지울 수 없음(표 지우기는 delete_block)")
    keep = []
    for a in an:
        r, c, rs, cs, _ = a
        ov = max(0, min(r + rs, at + n) - max(r, at))
        if ov == rs: continue                                      # 지운 줄 안에만 있던 칸
        a[2] = rs - ov                                             # 지운 줄에 걸친 병합 = rowspan 줄임
        if r >= at + n: a[0] = r - n
        elif r >= at: a[0] = at                                    # 첫 줄이 지워진 병합 칸 = 남은 첫 줄로(글 유지)
        keep.append(a)
    if b.get("rowh"): b["rowh"] = b["rowh"][:at] + b["rowh"][at + n:]
    _tbuild(b, keep, nr - n)

def op_table_insert_cols(m, index, at, n=1):
    b = _table(m, index); n = _int(n, 1, MAX_TINS, "n")
    an, nr, nc = _tfull(b); at = _int(at, 0, nc, "at")
    if nc + n > MAX_TCOLS: _fail("표 칸 과다(최대 %d)" % MAX_TCOLS)
    if nr * (nc + n) > MAX_TCELLS: _fail("표가 너무 큼")
    w = _colw(an, nc)
    tc = at - 1 if at > 0 else 0
    tmpl = _tmpl_line(an, tc, 1); cov = set()
    for a in an:
        r, c, rs, cs, _ = a
        if c < at < c + cs: a[3] += n; cov.update(range(r, r + rs))   # 병합 칸 안쪽에 넣음 = colspan 늘림
        elif c >= at: a[1] += n
    for i in range(nr):
        if i in cov: continue
        for j in range(at, at + n): an.append([i, j, 1, 1, _ecell(tmpl.get(i))])
    if w:   # 새 칸 너비 = 이웃 평균, 표 너비 유지
        nb = [w[k] for k in (at - 1, at) if 0 <= k < nc]
        _setw(an, w[:at] + [sum(nb) / len(nb)] * n + w[at:], sum(w))
    _tbuild(b, an, nr)

def op_table_delete_cols(m, index, at, n=1):
    b = _table(m, index)
    an, nr, nc = _tfull(b); at = _int(at, 0, nc - 1, "at"); n = _int(n, 1, nc - at, "n")
    if n >= nc: _fail("마지막 칸은 지울 수 없음(표 지우기는 delete_block)")
    w = _colw(an, nc); keep = []
    for a in an:
        r, c, rs, cs, _ = a
        ov = max(0, min(c + cs, at + n) - max(c, at))
        if ov == cs: continue
        a[3] = cs - ov
        if c >= at + n: a[1] = c - n
        elif c >= at: a[1] = at
        keep.append(a)
    if w: _setw(keep, w[:at] + w[at + n:], sum(w))
    _tbuild(b, keep, nr)

def op_table_merge(m, index, r0, c0, r1, c1):
    b = _table(m, index); an, nr, nc = _tfull(b)
    r0 = _int(r0, 0, nr - 1, "r0"); c0 = _int(c0, 0, nc - 1, "c0")
    r1 = _int(r1, r0, nr - 1, "r1"); c1 = _int(c1, c0, nc - 1, "c1")
    inside = []; rest = []
    for a in an:
        r, c, rs, cs, _ = a
        if r > r1 or r + rs - 1 < r0 or c > c1 or c + cs - 1 < c0: rest.append(a); continue
        if r < r0 or r + rs - 1 > r1 or c < c0 or c + cs - 1 > c1: _fail("병합이 고른 영역 밖으로 걸침 — 직사각형으로 고르세요")
        inside.append(a)
    if len(inside) < 2: _fail("합칠 칸이 둘 이상이어야 함")
    inside.sort(key=lambda a: (a[0], a[1]))
    w = _colw(an, nc)
    first = inside[0][4]; paras = []; imgs = []
    for a in inside:   # 글 = 칸 순서(줄 우선)대로 문단 이어 붙임(빈 칸은 건너뜀)
        ps = _cell_paras(a[4]); _charge(len(ps))
        if any(p["runs"] for p in ps): paras.extend(ps)
        imgs.extend(a[4].get("images") or [])
        if len(paras) > MAX_CPARAS: _fail("합친 칸 문단 과다(최대 %d)" % MAX_CPARAS)
    _cell_store(first, paras or [{"runs": []}])
    if imgs: first["images"] = imgs
    rest.append([r0, c0, r1 - r0 + 1, c1 - c0 + 1, first])
    if w: _setw(rest, w, sum(w))
    _tbuild(b, rest, nr)

def op_table_split(m, index, r, c):
    b = _table(m, index); an, nr, nc = _tfull(b)
    r = _int(r, 0, nr - 1, "r"); c = _int(c, 0, nc - 1, "c")
    a = next((a for a in an if a[0] == r and a[1] == c), None)
    if a is None: _fail("(%d,%d)에 칸 없음(병합 칸은 왼쪽 위 좌표)" % (r, c))
    if a[2] == 1 and a[3] == 1: _fail("합친 칸이 아님")
    w = _colw(an, nc); cell = a[4]
    for i in range(r, r + a[2]):
        for j in range(c, c + a[3]):
            if (i, j) == (r, c): continue
            e = _ecell(cell)
            if cell.get("bg"): e["bg"] = cell["bg"]
            an.append([i, j, 1, 1, e])
    a[2] = a[3] = 1
    if w: _setw(an, w, sum(w))
    _tbuild(b, an, nr)

def op_table_cell(m, index, r, c, runs=None, paras=None, bg=None, align=None):
    _table(m, index); cell = _cell_at(m, index, r, c)
    if runs is None and paras is None and bg is None and align is None: _fail("runs·paras·bg·align 중 하나 이상 필요")
    if runs is not None and paras is not None: _fail("runs와 paras는 함께 줄 수 없음")
    if runs is not None:
        _grow(_check_runs(runs, cell=True)); _charge(len(runs))
        _cell_store(cell, [{"runs": copy.deepcopy(runs)}])
    if paras is not None:
        if not isinstance(paras, list) or not (1 <= len(paras) <= MAX_CPARAS): _fail("paras는 1..%d개 배열" % MAX_CPARAS)
        for p in paras:
            if not isinstance(p, dict) or "runs" not in p or set(p) - {"runs", "align"}: _fail("칸 문단 키 오류")
            if "align" in p and p["align"] not in ALIGNS: _fail("칸 문단 align 값 오류")
            _grow(_check_runs(p["runs"], cell=True)); _charge(len(p["runs"]) + 1)
        _cell_store(cell, copy.deepcopy(paras))
    if bg is not None:
        if not isinstance(bg, str) or (bg and not _HEX.fullmatch(bg.upper())): _fail("bg는 #RRGGBB 또는 \"\"")
        if bg: cell["bg"] = bg.upper()
        else: cell.pop("bg", None)
    if align is not None:
        if align != "" and align not in ALIGNS: _fail("align은 %s 또는 \"\"" % "/".join(ALIGNS))
        if align: cell["align"] = align
        else: cell.pop("align", None)
        for p in cell.get("paras") or []:
            if p.get("align") == cell.get("align"): p.pop("align", None)

# ───────────── 그림·용지 연산 ─────────────
def _src_ok(src):
    if not isinstance(src, str) or len(src) > MAX_TOTAL or not _IMG.fullmatch(src): _fail("src는 data:image/(png|jpeg|gif|webp|bmp);base64 만")
    return src

def op_insert_image(m, pos, src, width=None):
    _no_cpos(pos, "그림을")
    blk = {"type": "img", "src": _src_ok(src)}   # place 생략 = char(글자처럼)
    if width is not None: blk["width"] = _int(width, 1, 5000, "width")
    _grow(len(src))
    _insert_obj(m, pos, blk)

def op_set_image(m, width, index=None, pos=None):
    w = _int(width, 1, 5000, "width")
    if (index is None) == (pos is None): _fail("index 또는 pos 중 하나만")
    if index is not None:
        bi = _int(index, 0, len(m["blocks"]) - 1, "index"); b = m["blocks"][bi]
        if b["type"] != "img": _fail("블록 %d은 그림이 아님(%s)" % (bi, b["type"]))
        b["width"] = w; return
    _no_cpos(pos, "글자처럼 그림을")
    bi, off = _pos(m, pos); b = m["blocks"][bi]
    if b["type"] == "img": b["width"] = w; return
    if b["type"] not in TEXT_TYPES: _fail("블록 %d은 그림이 아님(%s)" % (bi, b["type"]))
    head, tail = _split_at(b["runs"], off)
    if not tail or "img" not in tail[0]: _fail("pos 자리에 그림이 없음")
    tail[0]["width"] = w   # 개체 run은 _split_at이 복사하지 않음 = 원 run

def op_set_page(m, paper=None, orient=None, margins=None):
    if paper is None and orient is None and margins is None: _fail("paper·orient·margins 중 하나 이상 필요")
    meta = m.setdefault("meta", {})
    for k, v in (("paper", paper), ("orient", orient), ("margins", margins)):
        if v is None: continue
        if not isinstance(v, str) or len(v) > 100: _fail("%s는 문자열" % k)
        if v == "": meta.pop(k, None); continue
        if not _page_meta_ok(k, v): _fail("%s 값 오류(%s)" % (k, {"paper": "/".join(PAPERS_MM), "orient": "portrait|landscape",
                                                                "margins": "\"위,오른,아래,왼\" mm 0..100"}[k]))
        meta[k] = v

OPS = {"insert_text": op_insert_text, "delete_range": op_delete_range, "set_style": op_set_style,
       "set_block_type": op_set_block_type, "split_paragraph": op_split_paragraph, "merge_paragraph": op_merge_paragraph,
       "insert_pgbreak": op_insert_pgbreak, "delete_block": op_delete_block, "insert_block": op_insert_block,
       "insert_table": op_insert_table, "table_insert_rows": op_table_insert_rows, "table_delete_rows": op_table_delete_rows,
       "table_insert_cols": op_table_insert_cols, "table_delete_cols": op_table_delete_cols, "table_merge": op_table_merge,
       "table_split": op_table_split, "table_cell": op_table_cell,
       "insert_image": op_insert_image, "set_image": op_set_image, "set_page": op_set_page}

_SIGS = {k: inspect.signature(f) for k, f in OPS.items()}

def apply(m, ops):
    """연산 목록 적용 → 새 모델(입력은 바꾸지 않음, 순수 함수). 하나라도 실패하면 ModelError(어느 op인지 표시)."""
    validate(m)
    if not isinstance(ops, list) or len(ops) > MAX_OPS: _fail("ops는 배열(최대 %d)" % MAX_OPS)
    m = copy.deepcopy(m)
    _TL.work = 0; _TL.gcache = {}
    try:
        for i, op in enumerate(ops):
            if not isinstance(op, dict) or not isinstance(op.get("op"), str) or op["op"] not in OPS: _fail("op[%d]: 알 수 없는 연산" % i)
            args = {k: v for k, v in op.items() if k != "op"}
            try: _SIGS[op["op"]].bind(m, **args)   # 인자 묶기 오류만 사용자 오류(연산 안 TypeError = 내부 버그 그대로)
            except TypeError as e: _fail("op[%d] %s: 인자 오류(%s)" % (i, op["op"], str(e)[:120]))
            try: OPS[op["op"]](m, **args)
            except ModelError as e: _fail("op[%d] %s: %s" % (i, op["op"], e))
            if len(m["blocks"]) > MAX_BLOCKS: _fail("블록 과다")
    finally:
        _TL.work = None; _TL.gcache = None
    used = {r["endnote"] for b in m["blocks"] for r in b.get("runs") or [] if "endnote" in r}
    m["notes"] = {k: v for k, v in (m.get("notes") or {}).items() if k in used}   # 표식이 지워진 미주 정리
    validate(m)
    return m

def loads(s):
    """JSON 문자열/바이트 → 모델(검증 포함)."""
    if len(s) > 2 * MAX_TOTAL: _fail("JSON이 너무 큼")
    try:
        if isinstance(s, (bytes, bytearray)): s = s.decode("utf-8")
        m = json.loads(s)
    except (ValueError, RecursionError) as e: _fail("JSON 오류: %s" % str(e)[:120])
    return validate(m)
