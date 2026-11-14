#!/usr/bin/env python3
# hanword_keep.py — 원본 XML 되씀(K2 덩이 + 허용 목록 파트 보존). 표준 라이브러리만.
#
# 무엇: 같은 형식(hwpx→hwpx, docx→docx)으로 저장할 때 바뀌지 않은 덩이(문단·표)는 원본 XML 조각을 바이트 그대로 쓰고
#       (그 안의 도형·수식·필드·id 째), 바뀌었거나 새로 생긴 덩이만 기존 쓰기기(hwpx_std·docxconv)로 다시 만든다.
#
# 구조(왜 이렇게):
#  1) 바탕 = 원본 꾸러미. 유지 조각이 부르는 id(hwpx charPr·paraPr·borderFill·style·binData / docx pStyle·numId·rId·
#     bookmark·comment)는 원본 header·styles·numbering·rels에 이미 있으므로, 원본 파트를 그대로 두면 옮겨 적을 필요가 없다.
#     다시 만든 덩이에 필요한 항목만 원본 머리 뒤에 새 id(겹치지 않게 max+1)로 덧붙인다. 반대(새 골격에 유지 조각의 참조를
#     옮겨 심기)는 원본 머리 전체를 해석·이식해야 해 훨씬 크고 위험하다.
#  2) 조각 위치·해시는 저장할 때 원본 바이트를 다시 읽어 계산한다(§19: 조각 내용은 들고 다니지 않음, sha256 확인).
#     읽기기(reader)는 고치지 않는다: 본문 최상위 요소마다 뒤에 「표식 문단」을 끼운 탐침 꾸러미를 같은 읽기기로 가져와
#     표식 사이 블록 = 그 요소가 만든 블록으로 나눈다. 표식을 뺀 결과가 원본 가져오기 모델과 정확히 같을 때만 믿는다
#     (읽기기가 바뀌어도 — 예: sup/sub 속성 추가 — 대응이 자동으로 따라오고, 틀리면 검증에서 걸러짐).
#  3) 덩이 해시 = 정규 모델 블록 JSON(sort_keys)의 sha256. 미주는 번호 대신 본문으로 바꿔 해시(앞 미주 삭제로 번호가
#     밀려도 뒤 덩이가 '바뀜'으로 잡히지 않게). 속성이 나중에 늘어도 해시 정의는 그대로 성립.
#  4) 대응 = 해시 열 맞추기(공통 앞뒤 + 양쪽 유일 해시 닻의 LIS, 구간 재귀 깊이 상한) — O(n log n), n² LCS 없음.
#  5) 다시 만들 덩이 묶음은 [표식, 묶음1, 표식, 묶음2, …, 표식] 모델로 기존 쓰기기를 한 번 돌려 표식 사이 XML을 잘라 쓴다
#     (hwpx는 원본 header를 바탕으로 하는 _KeepWriter — 새 항목 id가 원본과 겹치지 않음).
#  6) 결과를 같은 가져오기 경로로 다시 읽어 정규 해시 열·meta가 요청 모델과 같을 때만 내보낸다. 어디서든 실패 =
#     K1(전체 다시 만들기 + 허용 목록 파트 복사) → 그것도 실패 = 그냥 다시 만들기(level none). 깨진 꾸러미는 내보내지 않는다.
#
# 보안: 원본은 믿을 수 없는 입력 — guard_docx/guard_hwpx_zip(zip폭탄·DTD), 파트 크기·요소 수 상한, DTD/ENTITY 거부 파서,
#       조각 24 MiB 상한, 선형 스캔. 허용 목록 밖 파트(매크로·OLE 패키지·외부 템플릿·미리보기 그림·스크립트)는 버린다.
import io, re, json, hashlib, zipfile, secrets, posixpath, bisect, html as htmlmod
import pyexpat

import hanword_model, hwpxconv, docxconv, hwpx_std
from hwpxconv import HwpxInputError

MAX_FRAG = 24 * 1024 * 1024          # §19·§25 조각 상한
MAX_PART = hwpx_std.MAX_XML          # XML 파트 하나(32 MiB)
MAX_ELEMS = hwpx_std.MAX_ELEMS
MAX_DEPTH_ALIGN = 8
W_NS = docxconv.NS["w"]; R_NS = docxconv.NS["r"]
HP_NS = hwpx_std.NS["hp"]; HS_NS = hwpx_std.NS["hs"]; HH_NS = hwpx_std.NS["hh"]
RT = docxconv.RT
PKG_RT = "http://schemas.openxmlformats.org/package/2006/relationships/"
REL_NS = docxconv.NS["rel"]

class KeepFail(Exception):
    """K2 불가(정상 흐름) — 호출부가 K1/전체 다시 만들기로 폴백."""

def sha256(b): return hashlib.sha256(b).hexdigest()

# ───────────── 정규 덩이 해시 ─────────────
def _canon_block(b, notes):
    if b.get("runs") and any("endnote" in r for r in b["runs"]):
        b = dict(b)
        b["runs"] = [dict(r, endnote=["note", notes.get(r["endnote"], "")]) if "endnote" in r else r for r in b["runs"]]
    return b

def block_hashes(m):
    """모델 → 블록별 정규 해시(sha256 hex). 미주는 번호가 아니라 내용으로."""
    notes = m.get("notes") or {}
    return [sha256(json.dumps(_canon_block(b, notes), sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            for b in m["blocks"]]

# ───────────── XML 스캔(선형, DTD 거부, 상한) ─────────────
_ENC = re.compile(rb"^\s*<\?xml[^>]{0,200}?encoding\s*=\s*[\"']([A-Za-z0-9._-]{1,40})[\"']")
def _utf8_only(raw):
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff") or b"\x00" in raw[:200]: raise KeepFail("XML이 UTF-8이 아님")
    m = _ENC.match(raw[3:] if raw[:3] == b"\xef\xbb\xbf" else raw)
    if m and m.group(1).lower().replace(b"_", b"-") not in (b"utf-8", b"utf8"): raise KeepFail("XML이 UTF-8이 아님")

_MM = re.compile(rb"<(?:[A-Za-z_][\w.-]*:)?mailMerge\b")
def _text_utf8(data, ct):
    """보존 파트가 UTF-16 등 비 UTF-8 XML이면 거부 — '<' 시작 검사·바이트 검색(필드·mailMerge) 우회 차단."""
    if ct.endswith("xml") or data[:2] in (b"\xff\xfe", b"\xfe\xff") or data[:4] in (b"<\x00?\x00", b"\x00<\x00?") or b"\x00" in data[:4]:
        if not ct.startswith("image/"): _utf8_only(data)

def _tag_end(buf, i):
    """buf[i]='<' 인 시작 태그의 '>' 위치(따옴표 안 '>' 무시). 선형."""
    q = None; n = len(buf)
    while i < n:
        c = buf[i]
        if q is not None:
            if c == q: q = None
        elif c in (34, 39): q = c
        elif c == 62: return i
        i += 1
    raise KeepFail("태그 끝 없음")

def _safe_xml(raw):
    """보존 파트 검증: 크기·DTD/ENTITY·요소 수(코어 safe_parse와 같은 상한)."""
    try: hwpx_std.safe_parse(raw)
    except hwpx_std.HwpxStdError as e: raise KeepFail("파트 거부: %s" % e)
    except Exception: raise KeepFail("파트 XML 손상")

MAX_KEEP_KIDS = 100000   # 본문 최상위 요소 상한 — 넘으면 K2 안 함(탐침 가져오기 비용·메모리 차단, 감사 2026-11-13)
MAX_INSTR = 65536

# ───────────── 필드 허용 목록(2026-11-14 감사) ─────────────
#   원본 조각째 되쓰는 덩이·파트에 남아도 되는 필드 종류만 허용. 그 밖(LINK·INCLUDE*·IMPORT·DATABASE·RD·AUTOTEXT*·DDE*·
#   MERGEFIELD·MACROBUTTON·FILLIN·USERNAME 등)·빈/깨진 필드·UNC/file: 대상 = 그 덩이는 되씀 안 함(모델에서 다시 만듦 = 결과 글자만).
#   이름공간은 접두어가 아니라 URI로(Transitional·Strict), instrText·delInstrText 는 begin…separate 전체를 이어 판정(중첩 포함).
W_URIS = {W_NS, "http://purl.oclc.org/ooxml/wordprocessingml/main"}
DOCX_FIELDS = {"PAGE", "NUMPAGES", "SECTIONPAGES", "SECTION", "DATE", "TIME", "CREATEDATE", "SAVEDATE", "PRINTDATE", "EDITTIME",
               "AUTHOR", "TITLE", "SUBJECT", "KEYWORDS", "COMMENTS", "LASTSAVEDBY", "NUMWORDS", "NUMCHARS", "REVNUM", "DOCPROPERTY",
               "REF", "PAGEREF", "NOTEREF", "SEQ", "TOC", "TC", "TOA", "TA", "XE", "INDEX", "STYLEREF", "LISTNUM", "AUTONUM",
               "AUTONUMLGL", "AUTONUMOUT", "SYMBOL", "EQ", "=", "FORMTEXT", "FORMCHECKBOX", "FORMDROPDOWN", "HYPERLINK", "IF",
               "COMPARE", "QUOTE", "ADVANCE", "BIDIOUTLINE"}
HWPX_FIELDS = {"HYPERLINK", "CLICK_HERE", "BOOKMARK", "FORMULA", "DATE", "DOC_DATE", "CROSSREF", "SUMMARY", "MEMO", "PROOFREADING_MARKS"}
_LINK_OK = re.compile(r"(https?:|mailto:)", re.I)
_FIELD_TOK = re.compile(r'\s*("(?:[^"\\]|\\.)*"|\S+)')

def _field_args(s):
    out = []; i = 0
    while len(out) < 64:
        m = _FIELD_TOK.match(s, i)
        if not m: break
        t = m.group(1); i = m.end()
        out.append(t[1:-1] if len(t) >= 2 and t[0] == '"' and t[-1] == '"' else t)
    return out

def _url_ok(u):
    low = re.sub(r"[\s\x00-\x1f]+", "", u or "")
    return bool(_LINK_OK.match(low)) and "\\" not in low and len(u) <= 2000   # http/https/mailto 만, 역슬래시(UNC 우회) 불가

def docx_field_ok(instr):
    """DOCX 필드 명령 → 되씀 허용?(허용 목록 종류 + 위험 대상 없음)."""
    s = (instr or "").strip()
    if not s or len(s) > MAX_INSTR: return False
    if re.search(r"(?i)file:|\\\\", s): return False     # file: 대상·이중 역슬래시(경로·UNC \\\\server) 금지
    a = _field_args(s)
    if not a: return False
    kind = "=" if a[0].startswith("=") else a[0].upper()
    if kind not in DOCX_FIELDS: return False
    if kind == "HYPERLINK":
        tgt = None; k = 1
        while k < len(a):
            if a[k].startswith("\\"):
                k += 2 if a[k][1:2].lower() in ("l", "o", "t") else 1   # 인자 받는 스위치
                continue
            tgt = a[k]; break
        if tgt is not None and not _url_ok(tgt): return False
        if tgt is None and "\\l" not in s.lower(): return False   # 대상도 문서 안 앵커(\\l)도 없음
    return True

def _hancom_cmd_url(v):
    return hwpxconv._hancom_unesc(v) if v else v

class _Fields:
    """스트리밍 XML 필드 추적기(pyexpat namespace_separator=" ", buffer_text). bad = 허용 밖 필드 있음.
       보수 규칙: 명령부(begin…separate)에 중첩 필드·fldSimple·w:t/w:sym·매크로 등이 끼면 바깥·안쪽 모두 bad
       (Word는 중첩 결과를 이어 붙여 명령을 만듦 — QUOTE/SYMBOL 로 종류·대상 조립 차단)."""
    __slots__ = ("kind", "bad", "stk", "cap", "hl", "hlp", "hln", "hld")
    def __init__(self, kind):
        self.kind = kind; self.bad = False; self.stk = []; self.cap = 0; self.hl = None; self.hlp = None; self.hln = 0; self.hld = 0
    def reset(self):   # 덩이 경계: 미완 필드 = 위험(덩이 걸침)
        if self.stk: self.bad = True
        b = self.bad; self.bad = False; self.stk = []; self.cap = 0; self.hl = None; self.hlp = None; self.hln = 0; self.hld = 0
        return b
    def _eval(self, fr):
        if fr["nest"] or fr["over"] or not docx_field_ok("".join(fr["buf"])): self.bad = True
    def _in_instr(self):
        return bool(self.stk) and self.stk[-1]["ph"] == "i"
    @staticmethod
    def _wattr(attrs, local):   # 같은 로컬 이름 속성(Transitional·Strict) 전부 — 둘 이상이면 모호 = 거절
        return [v for k, v in attrs.items() if k.rpartition(" ")[0] in W_URIS and k.rpartition(" ")[2] == local]
    def start(self, name, attrs, depth):
        uri, _, loc = name.rpartition(" ")
        if self.kind == "docx":
            if uri not in W_URIS: return
            if loc == "fldSimple":
                ins = self._wattr(attrs, "instr")
                if len(ins) != 1 or not docx_field_ok(ins[0]) or self._in_instr(): self.bad = True
                if self._in_instr(): self.stk[-1]["nest"] = True
            elif loc == "fldChar":
                ts = self._wattr(attrs, "fldCharType")
                if len(ts) != 1: self.bad = True; return
                t = ts[0]
                if t == "begin":
                    if self._in_instr(): self.stk[-1]["nest"] = True; self.bad = True
                    self.stk.append({"buf": [], "ph": "i", "n": 0, "over": False, "nest": False})
                elif t == "separate":
                    if self._in_instr(): self._eval(self.stk[-1]); self.stk[-1]["ph"] = "r"
                elif t == "end":
                    if self.stk:
                        fr = self.stk.pop()
                        if fr["ph"] == "i": self._eval(fr)
                else: self.bad = True
            elif loc in ("instrText", "delInstrText"):
                self.cap += 1
            elif loc in ("t", "delText", "sym") and self._in_instr():   # 명령부 안 일반 글자 = 명령으로 읽힐 수 있음
                self.bad = True
            elif loc in ("entryMacro", "exitMacro"):   # 양식 필드 매크로
                self.bad = True
        else:
            if loc == "fieldBegin":   # 이름공간 무관(로컬 이름) — 한컴이 접두어·URI 를 느슨히 다뤄도 안전
                t = attrs.get("type") or ""
                if t not in HWPX_FIELDS: self.bad = True
                elif t == "HYPERLINK":
                    self.hl = {"name": attrs.get("name"), "Path": None, "Command": None}; self.hld = depth
                    if self.hl["name"] and not _url_ok(self.hl["name"]): self.bad = True
            elif loc == "stringParam" and self.hl is not None and attrs.get("name") in ("Path", "Command"):
                self.hlp = [attrs.get("name"), []]; self.hln = 0
    def chars(self, data):
        if self.kind == "docx":
            if self.cap and self._in_instr():
                fr = self.stk[-1]; fr["n"] += len(data)
                if fr["n"] > MAX_INSTR: fr["over"] = True
                else: fr["buf"].append(data)
        elif self.hlp is not None:
            self.hln += len(data)
            if self.hln <= 8192: self.hlp[1].append(data)
            else: self.bad = True
    def end(self, name, depth):
        uri, _, loc = name.rpartition(" ")
        if self.kind == "docx":
            if uri in W_URIS and loc in ("instrText", "delInstrText") and self.cap: self.cap -= 1
        else:
            if self.hlp is not None and loc == "stringParam":
                v = "".join(self.hlp[1]); k = self.hlp[0]; self.hlp = None
                v = _hancom_cmd_url(v) if k == "Command" else v
                if v.strip() and not _url_ok(v): self.bad = True
                if self.hl is not None: self.hl[k] = v
            elif self.hl is not None and loc == "fieldBegin" and depth == self.hld:
                h = self.hl; self.hl = None
                if not any((x or "").strip() for x in (h["name"], h["Path"], h["Command"])): self.bad = True   # 대상 없는 하이퍼링크

def fields_bad(raw, kind):
    """파트 전체(머리말·꼬리말·메모·주석·바탕쪽 등) 필드 검사 — 허용 밖이 하나라도 있으면 True."""
    if len(raw) > MAX_PART: return True
    f = _Fields(kind); p = pyexpat.ParserCreate(namespace_separator=" "); p.buffer_text = True; dep = [0]
    def no(*a): raise KeepFail("DTD/엔티티 선언 금지")
    def st(n, a): dep[0] += 1; f.start(n, a, dep[0])
    def en(n): f.end(n, dep[0]); dep[0] -= 1
    p.StartDoctypeDeclHandler = no; p.EntityDeclHandler = no
    p.StartElementHandler = st; p.EndElementHandler = en; p.CharacterDataHandler = f.chars
    try: p.Parse(raw, True)
    except pyexpat.ExpatError: return True
    return f.reset()

class _Kid:
    __slots__ = ("start", "end", "name", "refs", "unk", "num", "bad", "com")
    def __init__(self, start, name):
        self.start = start; self.end = None; self.name = name; self.refs = set(); self.unk = 0; self.num = {}
        self.bad = False; self.com = False

_UNK_HWPX = {"rect", "ellipse", "line", "arc", "polygon", "curve", "connectLine", "container", "equation", "ole", "textart",
             "chart", "video", "compose", "dutmal", "bookmark", "memo", "fieldBegin", "footNote", "indexmark", "hiddenComment"}
_UNK_DOCX = {"pict", "object", "oMath", "oMathPara", "fldSimple", "fldChar", "bookmarkStart", "commentRangeStart",
             "commentReference", "ins", "del", "moveFrom", "moveTo", "sdt", "txbxContent", "AlternateContent", "footnoteReference", "sectPr"}

def _scan(raw, kind):
    """본문 파트 → 최상위 자식 위치·참조. kind='docx'(w:document/w:body/*) | 'hwpx'(hs:sec/*)."""
    if len(raw) > MAX_PART: raise KeepFail("본문 파트가 너무 큼")
    _utf8_only(raw)
    docx = kind == "docx"
    cdepth = 2 if docx else 1
    cname = (W_NS + " body") if docx else (HS_NS + " sec")
    p = pyexpat.ParserCreate(namespace_separator=" "); p.buffer_text = True
    decls = {}; kids = []; outer = set(); stack = []
    st = {"n": 0, "cont": None, "cend": None, "inc": False, "root": None}
    fld = _Fields(kind)
    def no(*a): raise KeepFail("DTD/엔티티 선언 금지")
    def nsdecl(prefix, uri):
        if not stack: decls[prefix or ""] = uri
    def start(name, attrs):
        st["n"] += 1
        if st["n"] > MAX_ELEMS: raise KeepFail("XML 요소가 너무 많음")
        stack.append(name.rsplit(" ", 1)[-1]); d = len(stack)
        if d == 1: st["root"] = name
        cur = None
        if d == cdepth and name == cname and st["cont"] is None:
            st["cont"] = p.CurrentByteIndex; st["inc"] = True
        elif st["inc"] and d == cdepth + 1:
            if len(kids) >= MAX_KEEP_KIDS: raise KeepFail("본문 요소가 너무 많음(K2 상한)")
            if kids and fld.reset(): kids[-1].bad = True
            kids.append(_Kid(p.CurrentByteIndex, name)); cur = kids[-1]
        elif st["inc"] and d > cdepth + 1:
            cur = kids[-1]
        loc = stack[-1]
        if cur is not None: fld.start(name, attrs, d)
        for k, v in attrs.items():
            if docx:
                if k.startswith(R_NS + " "): (cur.refs if cur else outer).add(v)
            elif k == "binaryItemIDRef": (cur.refs if cur else outer).add(v)
        if cur is not None:
            if docx:
                if loc in _UNK_DOCX and (loc != "fldChar" or attrs.get(W_NS + " fldCharType") == "begin"): cur.unk += 1
                if loc == "commentReference": cur.com = True
                if d == cdepth + 4 and stack[cdepth + 1] == "pPr" and stack[cdepth + 2] == "numPr" and loc in ("numId", "ilvl"):
                    cur.num[loc] = attrs.get(W_NS + " val")
            elif loc in _UNK_HWPX and (loc != "fieldBegin" or attrs.get("type") != "HYPERLINK"): cur.unk += 1
            if not docx and loc == "ole": cur.bad = True      # OLE 개체 = BinData 비그림 → 조각째 되씀 안 함(다시 만들고 셈)
    def chars(data):
        if st["inc"] and kids: fld.chars(data)
    def end(name):
        if st["inc"] and kids and len(stack) > cdepth: fld.end(name, len(stack))
        if st["inc"] and len(stack) == cdepth:
            st["cend"] = p.CurrentByteIndex; st["inc"] = False
        stack.pop()
    p.StartDoctypeDeclHandler = no; p.EntityDeclHandler = no
    p.StartNamespaceDeclHandler = nsdecl; p.StartElementHandler = start; p.EndElementHandler = end
    p.CharacterDataHandler = chars
    try: p.Parse(raw, True)
    except pyexpat.ExpatError: raise KeepFail("XML 손상")
    if kids and fld.reset(): kids[-1].bad = True
    if st["cont"] is None or st["cend"] is None: raise KeepFail("본문 컨테이너 없음")
    if not docx and st["root"] != cname: raise KeepFail("hs:sec 아님")
    cstart = _tag_end(raw, st["cont"]) + 1
    if raw[cstart - 2:cstart] == b"/>" or st["cend"] < cstart: raise KeepFail("빈 본문")
    suffix = st["cend"]
    if docx and kids and kids[-1].name == W_NS + " sectPr":   # 마지막 구역 설정 = 늘 원본 그대로(뒤꼬리)
        outer |= kids[-1].refs; suffix = kids[-1].start; kids.pop()
    if not kids: raise KeepFail("본문 요소 없음")
    for a, b in zip(kids, kids[1:]): a.end = b.start
    kids[-1].end = suffix
    return {"raw": raw, "decls": decls, "kids": kids, "prefix_end": kids[0].start, "suffix": suffix, "outer": outer}

def _prefix_of(decls, uri):
    for k, v in decls.items():
        if v == uri and k: return k
    if decls.get("") == uri: return ""
    raise KeepFail("이름공간 접두어 없음")

def _q(prefix, local): return ("%s:%s" % (prefix, local)) if prefix else local

def _marker_xml(kind, decls, tok):
    if kind == "docx":
        w = _prefix_of(decls, W_NS)
        return ("<{p}><{r}><{t}>%s</{t}></{r}></{p}>" % tok).format(p=_q(w, "p"), r=_q(w, "r"), t=_q(w, "t")).encode()
    h = _prefix_of(decls, HP_NS)
    return ('<{p} id="0" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0"><{r} charPrIDRef="0"><{t}>%s</{t}></{r}></{p}>'
            % tok).format(p=_q(h, "p"), r=_q(h, "run"), t=_q(h, "t")).encode()

def _is_marker(b, tok):
    return b.get("type") == "p" and "".join(r.get("text", "") for r in b.get("runs") or []) == tok

# ───────────── zip ─────────────
def _read_zip(data, fmt):
    if fmt == "docx": zf = docxconv.guard_docx(data)
    else:
        hwpxconv.guard_hwpx_zip(data); zf = zipfile.ZipFile(io.BytesIO(data))
    infos = [i for i in zf.infolist() if not i.is_dir()]
    names = [i.filename for i in infos]
    if len(set(names)) != len(names): raise KeepFail("zip 이름 중복")
    for n in names:
        if n.startswith("/") or "\\" in n or ".." in n.split("/"): raise KeepFail("zip 경로 이상")
    files = {i.filename: zf.read(i) for i in infos}
    return files, infos

def _zip(entries, infos_by_name, stored=False):
    """entries=[(name, bytes)] → zip 바이트. 원본 엔트리의 날짜·압축 방식·속성 유지."""
    names = [n for n, _ in entries]
    if len(set(names)) != len(names): raise KeepFail("출력 zip 이름 중복")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in entries:
            src = infos_by_name.get(name)
            zi = zipfile.ZipInfo(name, src.date_time if src else (1980, 1, 1, 0, 0, 0))
            if name == "mimetype" or stored: zi.compress_type = zipfile.ZIP_STORED
            else: zi.compress_type = src.compress_type if src and src.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED) else zipfile.ZIP_DEFLATED
            if src: zi.external_attr = src.external_attr; zi.create_system = src.create_system
            z.writestr(zi, data)
    return buf.getvalue()

_HPF_ITEM = re.compile(r'<(?:[\w.-]+:)?item\b[^<>]*?/>')
def _hpf_items(hs):
    """content.hpf manifest 항목들(접두어 무관) → [{속성}]."""
    return [dict(re.findall(r'\b([\w.:-]+)="([^"]*)"', m.group(0))) for m in _HPF_ITEM.finditer(hs)]

def _attr_values(raw, elem=None, attrs=("id",)):
    """XML(DTD 거부·요소 상한) → 지정 요소(local, None=전부)의 지정 속성(local) 값들. 따옴표 종류·접두어 무관."""
    p = pyexpat.ParserCreate(namespace_separator=" "); out = []; n = [0]
    def no(*a): raise KeepFail("DTD/엔티티 선언 금지")
    def start(name, a):
        n[0] += 1
        if n[0] > MAX_ELEMS: raise KeepFail("XML 요소가 너무 많음")
        if elem is None or name.rsplit(" ", 1)[-1] == elem:
            for k, v in a.items():
                if k.rsplit(" ", 1)[-1] in attrs: out.append(v)
    p.StartDoctypeDeclHandler = no; p.EntityDeclHandler = no; p.StartElementHandler = start
    try: p.Parse(raw, True)
    except pyexpat.ExpatError: raise KeepFail("XML 손상")
    return out

def _r_refs(raw):
    """파트가 r: 이름공간 속성으로 부르는 관계 id 집합(따옴표·접두어 무관)."""
    p = pyexpat.ParserCreate(namespace_separator=" "); out = set()
    def no(*a): raise KeepFail("DTD/엔티티 선언 금지")
    def start(name, a):
        for k, v in a.items():
            if k.startswith(R_NS + " "): out.add(v)
    p.StartDoctypeDeclHandler = no; p.EntityDeclHandler = no; p.StartElementHandler = start
    try: p.Parse(raw, True)
    except pyexpat.ExpatError: raise KeepFail("XML 손상")
    return out

def _max_int(vals):
    m = -1
    for v in vals:
        if re.fullmatch(r"[0-9]{1,10}", v or ""): m = max(m, int(v))
    return m

# ───────────── 모델 ─────────────
def _import_model(data, fmt):
    html = hwpxconv.hwpx_bytes_to_html(data) if fmt == "hwpx" else docxconv.docx_to_html(data)
    return hanword_model.model(html)

def new_model(doc):
    """HTML 문자열 또는 모델 dict → (html, 정규 모델). 쓰기기가 실제로 받는 형태(render 결과) 기준."""
    if isinstance(doc, (bytes, bytearray)): doc = doc.decode("utf-8")
    if isinstance(doc, str):
        return doc, hanword_model.model(doc)
    hanword_model.validate(doc)
    html = hanword_model.render(doc)
    return html, hanword_model.model(html)

# ───────────── 해시 열 맞추기 ─────────────
def _lis(pairs):
    """(i,j) i 오름차순 → j 증가 최장 부분열. O(k log k)."""
    tails = []; tidx = []; prev = [-1] * len(pairs)
    for k, (_, j) in enumerate(pairs):
        x = bisect.bisect_left(tails, j)
        if x == len(tails): tails.append(j); tidx.append(k)
        else: tails[x] = j; tidx[x] = k
        prev[k] = tidx[x - 1] if x else -1
    out = []; k = tidx[-1] if tidx else -1
    while k >= 0: out.append(pairs[k]); k = prev[k]
    return out[::-1]

GREEDY_WIN = 64
def _greedy(a, b, a0, a1, b0, b1, ma):
    """유일 닻이 없는 구간(빈 문단 반복 등): 두 포인터 탐욕 맞춤 — 같으면 짝, 다르면 창(64) 안 가장 가까운 재출현으로 건너뜀.
       O(n log n)(해시별 위치 목록 + bisect)."""
    pa = {}; pb = {}
    for i in range(a0, a1): pa.setdefault(a[i], []).append(i)
    for j in range(b0, b1): pb.setdefault(b[j], []).append(j)
    def nxt(pos, h, x):
        l = pos.get(h)
        if not l: return None
        k = bisect.bisect_left(l, x)
        return l[k] if k < len(l) else None
    i, j = a0, b0
    while i < a1 and j < b1:
        if a[i] == b[j]: ma[i] = j; i += 1; j += 1; continue
        jn = nxt(pb, a[i], j); inn = nxt(pa, b[j], i)
        dj = (jn - j) if jn is not None and jn - j <= GREEDY_WIN else None
        di = (inn - i) if inn is not None and inn - i <= GREEDY_WIN else None
        if dj is None and di is None: i += 1; j += 1
        elif di is None or (dj is not None and dj <= di): j = jn
        else: i = inn

def align(a, b):
    """해시 열 a(원본)·b(새) → ma[i] = 대응 j 또는 -1(단조). 앞뒤 공통 + 유일 닻 LIS, 구간 재귀 깊이 상한."""
    ma = [-1] * len(a)
    stack = [(0, len(a), 0, len(b), 0)]
    while stack:
        a0, a1, b0, b1, d = stack.pop()
        while a0 < a1 and b0 < b1 and a[a0] == b[b0]: ma[a0] = b0; a0 += 1; b0 += 1
        while a0 < a1 and b0 < b1 and a[a1 - 1] == b[b1 - 1]: ma[a1 - 1] = b1 - 1; a1 -= 1; b1 -= 1
        if a0 >= a1 or b0 >= b1: continue
        if d >= MAX_DEPTH_ALIGN: _greedy(a, b, a0, a1, b0, b1, ma); continue
        ca = {}; cb = {}
        for i in range(a0, a1): ca[a[i]] = ca.get(a[i], 0) + 1
        posb = {}
        for j in range(b0, b1):
            cb[b[j]] = cb.get(b[j], 0) + 1; posb[b[j]] = j
        anchors = [(i, posb[a[i]]) for i in range(a0, a1) if ca[a[i]] == 1 and cb.get(a[i]) == 1]
        if not anchors: _greedy(a, b, a0, a1, b0, b1, ma); continue
        pa, pb = a0, b0
        for i, j in _lis(anchors):
            ma[i] = j; stack.append((pa, i, pb, j, d + 1)); pa, pb = i + 1, j + 1
        stack.append((pa, a1, pb, b1, d + 1))
    return ma

# ───────────── 원본 색인(조각 위치·해시) ─────────────
def _body_parts(files, fmt):
    if fmt == "docx": return ["word/document.xml"]
    secs = sorted((n for n in files if re.fullmatch(r"Contents/section\d+\.xml", n)), key=lambda n: int(re.search(r"\d+", n.rsplit("/", 1)[1]).group(0)))
    if not secs: raise KeepFail("section 없음")
    return secs

def _index(orig, fmt):
    """원본 → 색인 {files, infos, parts:{name:scan}, groups:[...], model}. 탐침(표식 문단)으로 요소→블록 대응."""
    files, infos = _read_zip(orig, fmt)
    pnames = _body_parts(files, fmt)
    scans = {n: _scan(files[n], fmt) for n in pnames}
    tok = "zqHWKEEP%sqz" % secrets.token_hex(8)
    probe = dict(files)
    elems = []    # (part, kid, sec_index, first_in_sec)
    for si, n in enumerate(pnames):
        s = scans[n]; raw = s["raw"]; mk = _marker_xml(fmt, s["decls"], tok)
        out = [raw[:s["prefix_end"]]]
        for k, kid in enumerate(s["kids"]):
            out.append(raw[kid.start:kid.end]); out.append(mk); elems.append((n, kid, si, k == 0))
        out.append(raw[s["suffix"]:])
        probe[n] = b"".join(out)
    order = [i.filename for i in infos]
    pm = _import_model(_zip([(n, probe[n]) for n in order], {}, stored=True), fmt)
    real = _import_model(orig, fmt)
    groups = [[]]
    for b in pm["blocks"]:
        if _is_marker(b, tok): groups.append([])
        else: groups[-1].append(b)
    if len(groups) != len(elems) + 1 or groups[-1]: raise KeepFail("탐침 표식 수 불일치")
    groups.pop()
    ph = [block_hashes({"notes": pm["notes"], "blocks": g}) for g in groups]
    rh = block_hashes(real)
    flat = [h for g in ph for h in g]
    if flat != rh:
        # hwpx 읽기: 맨 앞 빈 문단이 제목·표 앞이면 버림(탐침은 표식 때문에 안 버려짐) — 그 한 경우만 맞춤
        if fmt == "hwpx" and flat[1:] == rh and groups and groups[0] and groups[0][0].get("type") == "p" \
                and not "".join(r.get("text", "") for r in groups[0][0].get("runs") or []):
            ph[0] = ph[0][1:]
        else: raise KeepFail("탐침 모델이 원본 모델과 다름")
    return {"files": files, "infos": infos, "pnames": pnames, "scans": scans, "elems": elems, "ehash": ph, "model": real, "hashes": rh}

def keep_index(orig, fmt):
    """열 때 곁 기록용: {sha256, size, format, blocks:[해시], groups:[{part,start,end,blocks:[해시]}]}. 조각 내용은 담지 않음."""
    ix = _index(orig, fmt)
    return {"sha256": sha256(orig), "size": len(orig), "format": fmt, "blocks": ix["hashes"],
            "groups": [{"part": n, "start": k.start, "end": k.end, "blocks": h} for (n, k, _, _), h in zip(ix["elems"], ix["ehash"])]}

# ───────────── 덩이 결정 ─────────────
def _groups(ix):
    """요소 → 덩이(블록 0개 요소는 다음 덩이에 붙임). [{elems:[e], blocks:[h], s0:원본 블록 시작}]"""
    out = []; pend = []; pos = 0
    for e, h in zip(ix["elems"], ix["ehash"]):
        pend.append(e)
        if h:
            out.append({"elems": pend, "hashes": h, "s0": pos}); pos += len(h); pend = []
    if pend:
        if not out: raise KeepFail("블록 없는 문서")
        out[-1]["elems"].extend(pend)
    return out

def _plan(G, ma, nnew, keepable):
    kept = {}
    for gi, g in enumerate(G):
        s, k = g["s0"], len(g["hashes"])
        j0 = ma[s]
        if j0 >= 0 and all(ma[s + t] == j0 + t for t in range(k)):
            kept[gi] = j0
    orig = 0
    for gi in list(kept):
        if not keepable(G[gi]): del kept[gi]; orig += 1
    return kept, orig

def _items(G, kept, nnew):
    start = {j0: gi for gi, j0 in kept.items()}
    items = []; j = 0; unit = []
    while j < nnew:
        gi = start.get(j)
        if gi is not None:
            if unit: items.append(("new", unit)); unit = []
            items.append(("keep", gi)); j += len(G[gi]["hashes"])
        else: unit.append(j); j += 1
    if unit: items.append(("new", unit))
    return items

# ───────────── 공용: 이름공간 선언 보충 ─────────────
_PFX = re.compile(rb"</?([A-Za-z_][\w.-]*):|\s([A-Za-z_][\w.-]*):[A-Za-z_][\w.-]*\s*=")
def _ns_fix(elem, gen_decls, out_decls):
    """다시 만든 최상위 요소가 쓰는 접두어 중 출력 파트 루트에 없는 것을 그 요소에 선언(충돌=실패)."""
    need = {}
    for m in _PFX.finditer(elem):
        pf = (m.group(1) or m.group(2)).decode()
        if pf in ("xml", "xmlns") or pf in need: continue
        uri = gen_decls.get(pf)
        if uri is None: raise KeepFail("생성 XML 접두어 미선언: %s" % pf)
        have = out_decls.get(pf)
        if have == uri: continue
        if have is not None: raise KeepFail("이름공간 접두어 충돌: %s" % pf)
        need[pf] = uri
    if not need: return elem
    i = _tag_end(elem, 0)
    if elem[i - 1:i] == b"/": i -= 1
    add = "".join(' xmlns:%s="%s"' % (k, docxconv._x(v)) for k, v in sorted(need.items())).encode()
    return elem[:i] + add + elem[i:]

def _gen_units(gen_raw, kind, nunits, tok):
    """생성 본문 → 표식 사이 최상위 요소 목록 nunits개 + 생성 루트 선언."""
    s = _scan(gen_raw, kind); raw = s["raw"]; tb = tok.encode()
    units = []; cur = None; nm = 0
    for kid in s["kids"]:
        frag = raw[kid.start:kid.end]
        if tb in frag:
            nm += 1
            if cur is not None: units.append(cur)
            cur = []
            continue
        if cur is None: raise KeepFail("생성 본문 앞에 표식 아닌 요소")
        cur.append(frag)
    if cur: raise KeepFail("생성 본문 끝 표식 뒤 요소")
    if nm != nunits + 1 or len(units) != nunits: raise KeepFail("생성 표식 수 불일치")
    return units, s["decls"]

def _unit_hashes(gen, fmt, tok, nunits):
    """생성 꾸러미를 같은 읽기기로 되읽어 단위별 정규 해시 — 쓰기기가 원래 잃는 것(전체 다시 만들기와 같은 손실)을 기대값에 반영."""
    gm = _import_model(gen, fmt)
    groups = [[]]
    for b in gm["blocks"]:
        if _is_marker(b, tok): groups.append([])
        else: groups[-1].append(b)
    if len(groups) != nunits + 2 or groups[0] or groups[-1]: raise KeepFail("생성 꾸러미 되읽기 표식 불일치")
    return [block_hashes({"notes": gm["notes"], "blocks": g}) for g in groups[1:-1]]

def _gen_model(nm, units, tok):
    mk = {"type": "p", "runs": [{"text": tok}]}
    blocks = [mk]
    for u in units:
        blocks.extend(nm["blocks"][j] for j in u); blocks.append(mk)
    return {"version": hanword_model.VERSION, "meta": nm["meta"], "notes": nm["notes"], "blocks": blocks}

# ═════════════════════════════ HWPX ═════════════════════════════
_HWPX_KEEP = re.compile(r"(mimetype|version\.xml|settings\.xml|META-INF/(container\.xml|manifest\.xml|container\.rdf)|"
                        r"Contents/(content\.hpf|header\.xml|section\d+\.xml|masterpage\d+\.xml)|BinData/[A-Za-z0-9_.-]{1,100})")

_ATTR = re.compile(r'([\w:.-]+)="([^"]*)"')
def _tag_attrs(tag): return dict(_ATTR.findall(tag))

def _set_cnt(h, tag, attr, value, start=0):
    """h 안 첫 <hh:tag ...> 시작 태그(start 이후)의 attr 값을 value로(속성 순서 무관)."""
    m = re.compile(r"<hh:%s\b[^<>]*>" % tag).search(h, start)
    if not m: raise KeepFail("header %s 없음" % tag)
    t = m.group(0)
    t2 = re.sub(r'\b%s="\d*"' % attr, '%s="%d"' % (attr, value), t, count=1) if re.search(r'\b%s="' % attr, t) else t[:-1] + ' %s="%d">' % (attr, value)
    return h[:m.start()] + t2 + h[m.end():]

class _KeepWriter(hwpx_std._Writer):
    """hwpx_std 쓰기기를 원본 header 위에서 돌림: 새 charPr/paraPr/borderFill/글꼴/그림 id = 원본 최댓값 뒤.
       원본 header는 속성 순서가 제각각(한컴·python-hwpx) — id·개수는 파서로 읽고, 고칠 때도 순서 무관 정규식."""
    TOK = 900000
    LANGS = ("HANGUL", "LATIN", "HANJA", "JAPANESE", "OTHER", "SYMBOL", "USER")
    def __init__(self, hdr, bin_ids, sec_max_id):
        super().__init__()
        HH = "{%s}" % HH_NS
        root = hwpx_std.safe_parse(hdr.encode("utf-8"))
        def ids(tag):
            return [int(e.get("id")) for e in root.iter(HH + tag) if re.fullmatch(r"\d{1,9}", e.get("id") or "")]
        for tag in ("charProperties", "paraProperties", "borderFills"):
            if root.find(".//" + HH + tag) is None or "</hh:%s>" % tag not in hdr: raise KeepFail("원본 header 형식(%s)" % tag)
        if 0 not in ids("tabPr"): raise KeepFail("원본 header tabPr 0 없음")
        self.cnt0 = {t: len(list(root.iter(HH + e))) for t, e in (("charProperties", "charPr"), ("paraProperties", "paraPr"), ("borderFills", "borderFill"))}
        self.faces = {}
        for ff in root.iter(HH + "fontface"):
            self.faces[ff.get("lang")] = [(int(f.get("id")), f.get("face") or "") for f in ff.findall(HH + "font") if re.fullmatch(r"\d{1,9}", f.get("id") or "")]
        if any(l not in self.faces for l in self.LANGS): raise KeepFail("원본 header 글꼴 언어")
        skel = self.hdr
        self.hdr = hdr
        mx = lambda tag: (max(ids(tag)) + 1) if ids(tag) else 0
        self.next_cp = mx("charPr"); self.next_pp = mx("paraPr"); self.next_bf = mx("borderFill")
        self.next_font = self.TOK
        self.cps = {}; self.pps = {}; self.bfs = {}; self.fonts = {}
        self.new_cp = []; self.new_pp = []; self.new_bf = []; self.new_font = []
        self.bins = []; self.bin_ids = set(bin_ids); self.ids = sec_max_id
        # 맨 바탕 테두리(골격 borderFill 2 = 선 없음·채움 없음)를 새 id로
        bf2 = re.search(r'<hh:borderFill id="2".*?</hh:borderFill>', skel, re.S).group(0)
        self.plain_bf = self.next_bf; self.next_bf += 1
        self.new_bf.append(re.sub(r'id="2"', 'id="%d"' % self.plain_bf, bf2, count=1))
        face = re.search(r'<hh:fontface lang="HANGUL"[^>]*>.*?<hh:font id="1" face="([^"]*)"', skel, re.S).group(1)
        tok = self.font(face)
        self.cp0 = re.sub(r'borderFillIDRef="\d+"', 'borderFillIDRef="%d"' % self.plain_bf, self.cp0, count=1)
        self.cp0 = re.sub(r'<hh:fontRef [^>]*/>', '<hh:fontRef hangul="%d" latin="%d" hanja="%d" japanese="%d" other="%d" symbol="%d" user="%d"/>' % ((tok,) * 7), self.cp0, count=1)
        self.pp0 = re.sub(r'borderFillIDRef="\d+"', 'borderFillIDRef="%d"' % self.plain_bf, self.pp0)
        # 목록·개요 머리 idRef = 원본 문서가 실제 쓰는 것(번호 이어짐)
        self.href = {}
        for t in ("OUTLINE", "NUMBER", "BULLET"):
            c = {}
            for e in root.iter(HH + "heading"):
                if e.get("type") == t and re.fullmatch(r"\d{1,9}", e.get("idRef") or ""): c[e.get("idRef")] = c.get(e.get("idRef"), 0) + 1
            if c: self.href[t] = int(max(c, key=c.get))
            else:
                x = ids("bullet" if t == "BULLET" else "numbering")
                if x: self.href[t] = min(x)
        self.tbl_bf = self.borderfill({}, None)
        # 기본 글자·문단 모양(원본 0번은 뜻이 다를 수 있음 → 다시 만든 조각의 0 참조를 이것으로)
        self.def_cp = self.next_cp; self.next_cp += 1
        self.new_cp.append(re.sub(r'<hh:charPr id="\d+"', '<hh:charPr id="%d"' % self.def_cp, self.cp0, count=1))
        self.def_pp = self.next_pp; self.next_pp += 1
        self.new_pp.append(re.sub(r'<hh:paraPr id="\d+"', '<hh:paraPr id="%d"' % self.def_pp, self.pp0, count=1))

    def parapr(self, align=None, heading=None, indent_px=0):
        if heading and heading[0] in self.href: heading = (heading[0], self.href[heading[0]], heading[2])
        return super().parapr(align, heading, indent_px)

    def binitem(self, data, fmt):
        self.img_bytes += len(data)
        if self.img_bytes > hwpx_std.MAX_IMG_BYTES or len(self.bins) >= hwpx_std.MAX_IMGS: raise hwpx_std.HwpxStdError("그림이 너무 많거나 큽니다")
        ext = {"jpeg": "jpg"}.get(fmt, fmt); n = len(self.bins) + 1
        bid = "HWK%04X" % n
        while bid in self.bin_ids: n += 1; bid = "HWK%04X" % n
        self.bin_ids.add(bid)
        self.bins.append((bid, "BinData/%s.%s" % (bid, ext), data, "image/" + fmt))
        return bid

    def _finish_header(self, meta):
        h = self.hdr
        fonts = list(self.new_font); self.new_font = []
        repl = {}
        for lang in self.LANGS:
            have = {}
            for i, face in self.faces[lang]: have.setdefault(htmlmod.unescape(face), i)
            nxt = (max(i for i, _ in self.faces[lang]) + 1) if self.faces[lang] else 0; add = []
            for fid, name in fonts:
                ex = have.get(name)
                if ex is None:
                    ex = nxt; nxt += 1; have[name] = ex
                    add.append('<hh:font id="%d" face="%s" type="TTF" isEmbedded="0"><hh:typeInfo familyType="FCAT_GOTHIC" weight="6" proportion="4" contrast="0" '
                               'strokeVariation="1" armStyle="1" letterform="1" midline="1" xHeight="1"/></hh:font>' % (ex, hwpx_std._x(name)))
                repl.setdefault(fid, {})[lang.lower()] = ex
            if add:
                m = re.search(r'<hh:fontface\b[^<>]*\blang="%s"[^<>]*>' % lang, h)
                if not m: raise KeepFail("원본 header 글꼴(%s)" % lang)
                e = h.find("</hh:fontface>", m.end())
                if e < 0: raise KeepFail("원본 header 글꼴 끝")
                h = h[:e] + "".join(add) + h[e:]
                h = _set_cnt(h, "fontface", "fontCnt", len(self.faces[lang]) + len(add), m.start())
        def fix(s):
            mm = re.search(r'<hh:fontRef hangul="(\d+)"', s)
            if mm and int(mm.group(1)) in repl:
                r = repl[int(mm.group(1))]
                s = re.sub(r'<hh:fontRef [^>]*/>', '<hh:fontRef hangul="%d" latin="%d" hanja="%d" japanese="%d" other="%d" symbol="%d" user="%d"/>'
                           % tuple(r[k] for k in ("hangul", "latin", "hanja", "japanese", "other", "symbol", "user")), s, count=1)
            return s
        self.new_cp = [fix(s) for s in self.new_cp]
        # itemCnt 정규식이 속성 순서를 가정하므로 같은 태그를 잠시 표준형으로 바꿔 둠 → 상위가 더한 뒤 실제 개수로
        for tag in self.cnt0: h = _set_cnt(h, tag, "itemCnt", 0)
        self.hdr = h
        bins = self.bins; self.bins = []        # 골격용 binDataList를 새로 만들지 않게(원본에 있으면 아래에서 덧붙임)
        try: super()._finish_header(meta)
        finally: self.bins = bins
        h = self.files["Contents/header.xml"].decode("utf-8")
        for tag, n in (("charProperties", len(self.new_cp)), ("paraProperties", len(self.new_pp)), ("borderFills", len(self.new_bf))):
            h = _set_cnt(h, tag, "itemCnt", self.cnt0[tag] + n)
        if bins and "<hh:binDataList" in h:   # 구형(한글 2010~·python-hwpx) 목록이 원본에 있으면 같이 늘림
            m = re.search(r'<hh:binDataList\b[^<>]*>(.*?)</hh:binDataList>', h, re.S)
            if not m: raise KeepFail("binDataList 형식")
            ids = [int(x) for x in re.findall(r'<hh:binItem\b[^<>]*?\bid="(\d{1,9})"', m.group(1))]
            nx = (max(ids) + 1) if ids else 0
            add = "".join('<hh:binItem id="%d" Type="Embedding" BinData="%s" Format="%s"/>' % (nx + i, p.split("/", 1)[1], p.rsplit(".", 1)[1]) for i, (_, p, _, _) in enumerate(bins))
            e = m.end() - len("</hh:binDataList>")
            h = h[:e] + add + h[e:]
            h = _set_cnt(h, "binDataList", "itemCnt", len(ids) + len(bins))
        self.files["Contents/header.xml"] = h.encode("utf-8")

class _Demote(Exception):
    def __init__(self, gi): self.gi = gi

def _first_groups(G):
    first_g = {}
    for gi, g in enumerate(G):
        for e in g["elems"]:
            if e[3]: first_g.setdefault(e[2], gi)
    return first_g

def _hwpx_place(items, G, nsec, nm):
    """hwpx 단위 → 구역 배정. 쪽 나눔으로 끝나는 새 단위·구역 첫 문단 앞 새 단위 = _Demote(그 유지 덩이)."""
    for k, (t, v) in enumerate(items):   # 쪽 나눔 = 다음 문단의 pageBreak 속성 → 뒤 유지 덩이를 흡수
        if t == "new" and nm["blocks"][v[-1]]["type"] == "pgbreak":
            if k + 1 < len(items): raise _Demote(items[k + 1][1])
            raise KeepFail("문서가 쪽 나눔으로 끝남")
    sec_of = lambda gi: G[gi]["elems"][0][2]
    first_g = _first_groups(G)
    bysec = {s: [] for s in range(nsec)}; prev_g = -1
    for k, (t, v) in enumerate(items):
        if t == "keep": s = sec_of(v); prev_g = v
        else:
            nxt = next((items[x][1] for x in range(k + 1, len(items)) if items[x][0] == "keep"), len(G))
            if prev_g + 1 < nxt: s = sec_of(prev_g + 1)
            elif prev_g >= 0: s = sec_of(prev_g)
            elif nxt < len(G): s = sec_of(nxt)
            else: s = 0
        bysec[s].append((t, v))
    for s in range(nsec):
        lst = bysec[s]
        if not lst: raise KeepFail("구역이 비게 됨")
        for t2, v2 in lst[1:]:
            if t2 == "keep" and v2 == first_g.get(s): raise _Demote(v2)
    return bysec, first_g

def _hwpx_keep(ix, nm, bysec, first_g, G):
    files = ix["files"]; pnames = ix["pnames"]; scans = ix["scans"]
    tok = "zqHWKEEP%sqz" % secrets.token_hex(8)
    units = [v for s in range(len(pnames)) for t, v in bysec[s] if t == "new"]
    gen_units = []; wr = None; hdr_out = files["Contents/header.xml"]; exp = []
    if units:
        hdr = files.get("Contents/header.xml")
        if hdr is None: raise KeepFail("header 없음")
        _safe_xml(hdr)
        hdr_s = hdr.decode("utf-8")
        bin_ids = {a["id"] for a in _hpf_items(files.get("Contents/content.hpf", b"").decode("utf-8", "replace")) if a.get("id")}
        bin_ids |= {n.split("/", 1)[1].rsplit(".", 1)[0] for n in files if n.startswith("BinData/")}   # 목록에 없는 파일 이름과도 안 겹치게
        mxid = 0
        for n in files:
            if n in pnames or re.fullmatch(r"Contents/masterpage\d+\.xml", n):
                mxid = max(mxid, _max_int(_attr_values(files[n], None, ("id", "instid", "instId"))))
        if mxid + 1000 + 10 ** 6 > 2 ** 31 - 1: raise KeepFail("원본 개체 id가 너무 큼")
        wr = _KeepWriter(hdr_s, bin_ids, mxid + 1000)
        html = hanword_model.render(_gen_model(nm, units, tok))
        meta, body = hwpxconv._extract_meta(html)
        notes, body = hwpxconv._extract_fnotes(body)
        pp = hwpxconv._PP(); pp.feed(body); pp.close(); pp.flush()
        try: gen = wr.build(pp.paras, meta, notes, hwpxconv._meta_page(meta) or {"paper": "A4"})
        except hwpx_std.HwpxStdError as e: raise KeepFail("다시 만들기 실패: %s" % e)
        exp = _unit_hashes(gen, "hwpx", tok, len(units))
        gen_units, gdecls = _gen_units(wr.files["Contents/section0.xml"], "hwpx", len(units), tok)
        hdr_out = wr.files["Contents/header.xml"]
        cp0 = b' charPrIDRef="%d"' % wr.def_cp; pp0 = b' paraPrIDRef="%d"' % wr.def_pp
        gen_units = [[e.replace(b' charPrIDRef="0"', cp0).replace(b' paraPrIDRef="0"', pp0) for e in u] for u in gen_units]
    # 구역 조립
    out = {}; uid = {id(u): k for k, u in enumerate(units)}
    regen = 0
    for s, n in enumerate(pnames):
        sc = scans[n]; raw = sc["raw"]; parts = [raw[:sc["prefix_end"]]]
        for idx, (t, v) in enumerate(bysec[s]):
            if t == "keep":
                ks = [raw[e[1].start:e[1].end] for e in G[v]["elems"]]
                if idx == 0 and v != first_g.get(s):   # 원본 구역 첫 문단이 지워짐 → 남은 첫 유지 문단에 secPr run을 옮겨 심음
                    fe = next(e for e in G[first_g[s]]["elems"] if e[3])
                    ks[0] = _with_secrun(ks[0], _secrun(raw[fe[1].start:fe[1].end]))
                parts.extend(ks)
                continue
            elems = [_ns_fix(e, gdecls, sc["decls"]) for e in gen_units[uid[id(v)]]]
            regen += len(v)
            if not elems: raise KeepFail("빈 다시 만들기 단위")
            if idx == 0:   # 구역 첫 문단을 새로 만듦 → 원본 첫 문단의 secPr·머리말 run을 옮겨 심음
                fe = next(e for e in G[first_g[s]]["elems"] if e[3])
                elems[0] = _with_secrun(elems[0], _secrun(raw[fe[1].start:fe[1].end]))
            parts.extend(elems)
        parts.append(raw[sc["suffix"]:])
        out[n] = b"".join(parts)
    return out, hdr_out, (wr.bins if wr else []), regen, exp

def _with_secrun(elem, run):
    i = _tag_end(elem, 0) + 1
    if elem[i - 2:i] == b"/>": raise KeepFail("빈 문단 태그")
    return elem[:i] + run + elem[i:]

def _secrun(frag):
    """원본 구역 첫 문단 → 앞쪽 secPr/ctrl 전용 run들(바이트). 글자가 섞여 있으면 실패."""
    p = pyexpat.ParserCreate()
    runs = []; stack = []
    def no(*a): raise KeepFail("DTD")
    def start(name, attrs):
        stack.append(name.split(":")[-1]); d = len(stack)
        if stack[-1] == "ole": raise KeepFail("secPr 안 OLE 개체 — 옮겨 심기 불가")
        if d == 2: runs.append([p.CurrentByteIndex, None, set(), True, stack[-1] == "run"])
        elif d >= 3 and runs:
            r = runs[-1]
            if d == 3: r[2].add(stack[-1])
            if d == 3 and stack[-1] not in ("secPr", "ctrl"): r[3] = False
            if d == 4 and stack[-2] == "ctrl" and stack[-1] not in ("colPr", "header", "footer", "pageNum", "pageHiding", "newNum", "pageNumCtrl"): r[3] = False
    def end(name):
        if len(stack) == 2 and runs:
            i = p.CurrentByteIndex
            runs[-1][1] = (_tag_end(frag, i) + 1) if frag[i + 1:i + 2] != b"/" else frag.index(b">", i) + 1
        stack.pop()
    p.StartDoctypeDeclHandler = no; p.EntityDeclHandler = no
    p.StartElementHandler = start; p.EndElementHandler = end
    try: p.Parse(frag, True)
    except pyexpat.ExpatError: raise KeepFail("첫 문단 XML")
    if not runs or not runs[0][4] or "secPr" not in runs[0][2] or not runs[0][3]: raise KeepFail("secPr run 옮기기 불가")
    k = 0
    while k + 1 < len(runs) and runs[k + 1][4] and runs[k + 1][3] and runs[k + 1][2]: k += 1
    return frag[runs[0][0]:runs[k][1]]

def _hwpx_package(ix, secs, hdr, bins):
    files = ix["files"]; infos = ix["infos"]
    out = dict(files); out.update(secs); out["Contents/header.xml"] = hdr
    hpf = files.get("Contents/content.hpf")
    if hpf is None: raise KeepFail("content.hpf 없음")
    _safe_xml(hpf)
    hs = hpf.decode("utf-8")
    item_re = _HPF_ITEM
    id_of = {}; mt_of = {}
    for a in _hpf_items(hs):
        if a.get("href") and a.get("id"): id_of[a["href"]] = a["id"]; mt_of[a["href"]] = a.get("media-type") or ""
    refs = set()
    for n in out:
        if n in ix["pnames"] or re.fullmatch(r"Contents/(header|masterpage\d+)\.xml", n): refs |= set(_attr_values(out[n], None, ("binaryItemIDRef",)))
    keep = {}
    dropped = set()
    for n, data in out.items():
        if n.startswith("BinData/"):
            if _HWPX_KEEP.fullmatch(n) and id_of.get(n) in refs and re.fullmatch(r"image/[A-Za-z0-9.+-]{1,40}", mt_of.get(n, "")): keep[n] = data   # 그림만(OLE 등 버림)
            else: dropped.add(n)
        elif _HWPX_KEEP.fullmatch(n): keep[n] = data
        elif n == "Preview/PrvText.txt":
            with zipfile.ZipFile(hwpx_std.SKELETON) as z: keep[n] = z.read(n)   # 옛 본문 노출 막기 — 빈 미리보기 글
        else: dropped.add(n)
    for n, data in keep.items():
        if n.endswith((".xml", ".hpf", ".rdf")) and n not in secs: _safe_xml(data)
        if not n.startswith("BinData/"): _text_utf8(data, "xml")
        if not n.startswith("BinData/") and data.lstrip(b"\xef\xbb\xbf \t\r\n")[:1] == b"<" and fields_bad(data, "hwpx"):
            raise KeepFail("허용 밖 필드: %s" % n)   # 구역·바탕쪽·header 등 XML 모양 파트 전부
    # manifest(content.hpf): 버린 파트 항목 빼고 새 그림 더함 — 바뀐 게 없으면 원본 바이트
    def drop_item(m):
        a = dict(re.findall(r'\b([\w.:-]+)="([^"]*)"', m.group(0)))
        return "" if a.get("href") in dropped else m.group(0)
    hs2 = item_re.sub(drop_item, hs)
    for bid, path, data, mt in bins:
        keep[path] = data
    if bins:
        mpf = re.search(r'</((?:[\w.-]+:)?)manifest>', hs2)
        if not mpf: raise KeepFail("hpf manifest 없음")
        pf = mpf.group(1)
        hs2 = hs2[:mpf.start()] + "".join('<%sitem id="%s" href="%s" media-type="%s" isEmbeded="1"/>' % (pf, b, p, t) for b, p, _, t in bins) + hs2[mpf.start():]
    keep["Contents/content.hpf"] = hpf if hs2 == hs else hs2.encode("utf-8")
    cx = keep.get("META-INF/container.xml")
    if cx is not None:
        cs = cx.decode("utf-8")
        cs2 = re.sub(r'<(?:[\w.-]+:)?rootfile\b[^<>]*?/>', lambda m: "" if (re.search(r'full-path="([^"]*)"', m.group(0)) or [None, None])[1] not in keep else m.group(0), cs)
        if cs2 != cs: keep["META-INF/container.xml"] = cs2.encode("utf-8")
    if keep.get("mimetype") != b"application/hwp+zip": raise KeepFail("mimetype 없음/이상")
    order = ["mimetype"] + [i.filename for i in infos if i.filename in keep and i.filename != "mimetype"] + [b[1] for b in bins]
    data = _zip([(n, keep[n]) for n in order], {i.filename: i for i in infos})
    same = [n for n in order if n in files and keep[n] == files[n]]
    return data, same, sorted(dropped)

# ═════════════════════════════ DOCX ═════════════════════════════
WM = "application/vnd.openxmlformats-officedocument.wordprocessingml."
_CT_BY_REL = {
    "officeDocument": WM + "document.main+xml", "styles": WM + "styles+xml", "numbering": WM + "numbering+xml",
    "settings": WM + "settings+xml", "fontTable": WM + "fontTable+xml", "webSettings": WM + "webSettings+xml",
    "footnotes": WM + "footnotes+xml", "endnotes": WM + "endnotes+xml", "comments": WM + "comments+xml",
    "header": WM + "header+xml", "footer": WM + "footer+xml", "theme": "application/vnd.openxmlformats-officedocument.theme+xml",
    "core-properties": "application/vnd.openxmlformats-package.core-properties+xml",
    "extended-properties": "application/vnd.openxmlformats-officedocument.extended-properties+xml",
    "custom-properties": "application/vnd.openxmlformats-officedocument.custom-properties+xml",
    "stylesWithEffects": "application/vnd.ms-word.stylesWithEffects+xml", "people": WM + "people+xml",
    "commentsExtended": WM + "commentsExtended+xml", "commentsIds": WM + "commentsIds+xml",
    "commentsExtensible": WM + "commentsExtensible+xml", "customXmlProps": "application/vnd.openxmlformats-officedocument.customXmlProperties+xml",
    "customXml": None, "image": None, "font": None}
_EXT_CT = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "bmp": "image/bmp", "tif": "image/tiff",
           "tiff": "image/tiff", "emf": "image/x-emf", "wmf": "image/x-wmf", "svg": "image/svg+xml", "wdp": "image/vnd.ms-photo",
           "odttf": "application/vnd.openxmlformats-officedocument.obfuscatedFont"}
_DOC_STRUCT = {"styles", "stylesWithEffects", "numbering", "settings", "fontTable", "webSettings", "theme", "footnotes", "endnotes",
               "comments", "commentsExtended", "commentsIds", "commentsExtensible", "people", "customXml"}
_DOC_BYID = {"image", "hyperlink", "header", "footer"}
_SEC_OK = {"image", "hyperlink", "customXmlProps", "font", "theme"}
_PKG_OK = {"officeDocument", "core-properties", "extended-properties", "custom-properties"}

def _rtype(t): return (t or "").rstrip("/").rsplit("/", 1)[-1]

def _rels_path(part):
    d, b = posixpath.split(part)
    return posixpath.join(d, "_rels", b + ".rels")

def _resolve(part, target):
    if target.startswith("/"): p = target.lstrip("/")
    else: p = posixpath.normpath(posixpath.join(posixpath.dirname(part), target))
    if p.startswith("../") or p == ".." or p.startswith("/"): return None
    return p

def _parse_rels(raw):
    _safe_xml(raw)
    root = hwpx_std.ET.fromstring(raw)
    out = []
    for r in root:
        out.append((r.get("Id") or "", r.get("Type") or "", r.get("Target") or "", r.get("TargetMode") or ""))
    return out

def _rels_xml(rels):
    o = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships xmlns="%s">' % REL_NS]
    for rid, typ, tgt, mode in rels:
        o.append('<Relationship Id="%s" Type="%s" Target="%s"%s/>' % (docxconv._x(rid), docxconv._x(typ), docxconv._x(tgt), ' TargetMode="External"' if mode == "External" else ""))
    o.append("</Relationships>")
    return "".join(o).encode("utf-8")

def _docx_retain(files):
    """원본 → 보존 파트 닫힘: {part: ct}, 문서 rels 판정 {Id: ok?}, 바뀐 rels 파일 {path: bytes}."""
    keep = {}; rels_out = {}
    pr = files.get("_rels/.rels")
    if pr is None: raise KeepFail("_rels/.rels 없음")
    prl = _parse_rels(pr); newp = []
    for rel in prl:
        t = _rtype(rel[1]); p = _resolve("", rel[2]) if rel[3] != "External" else None
        if t in _PKG_OK and p and p in files:
            if t == "officeDocument" and p != "word/document.xml": raise KeepFail("본문 파트 경로 이상")
            keep[p] = _CT_BY_REL[t]; newp.append(rel)
    if "word/document.xml" not in keep: raise KeepFail("officeDocument 관계 없음")
    if newp != prl: rels_out["_rels/.rels"] = _rels_xml(newp)
    # 문서 rels
    doc_ok = {}; doc_rels = []
    drp = _rels_path("word/document.xml")
    drl = _parse_rels(files[drp]) if drp in files else []
    for rel in drl:
        rid, typ, tgt, mode = rel; t = _rtype(typ)
        if mode == "External":
            ok = t == "hyperlink" and hwpxconv._safe_link_url(tgt.strip())
        else:
            p = _resolve("word/document.xml", tgt)
            ok = bool(p and p in files and (t in _DOC_STRUCT or t in _DOC_BYID))
        doc_ok[rid] = (bool(ok), t)
        doc_rels.append(rel)
    return keep, rels_out, doc_ok, doc_rels, drp

def _docx_closure(files, keep, start, rels_out, settings_fix, content=None):
    """보존 파트(덧붙여 고친 파트 포함)에서 rels를 따라 닫힘(허용 형식만, rels는 허용 관계만 남겨 다시 씀).
       허용 밖 관계를 파트가 부르면(r: 속성, 따옴표 무관) 실패."""
    content = content or {}
    todo = list(start); seen = set()
    while todo:
        part = todo.pop()
        if part in seen: continue
        seen.add(part)
        rp = _rels_path(part)
        if rp not in files: continue
        rl = _parse_rels(files[rp]); new = []
        raw = content.get(part, files.get(part)); refs = None
        for rid, typ, tgt, mode in rl:
            t = _rtype(typ)
            if mode == "External":
                if t == "hyperlink" and hwpxconv._safe_link_url(tgt.strip()): new.append((rid, typ, tgt, mode)); continue
            else:
                p = _resolve(part, tgt)
                if p and p in files and t in _SEC_OK | ({"customXmlProps"} if part.startswith("customXml/") else set()):
                    if p not in keep:
                        keep[p] = _CT_BY_REL.get(t); todo.append(p)
                    new.append((rid, typ, tgt, mode)); continue
            if part == "word/settings.xml" and t == "attachedTemplate":
                settings_fix.append(rid); continue
            if refs is None: refs = _r_refs(raw)
            if rid in refs: raise KeepFail("허용 밖 관계를 쓰는 파트: %s" % part)
        if new != rl: rels_out[rp] = _rels_xml(new)

def _docx_ct(keep, files, orig_ct):
    """[Content_Types].xml 새로 만듦 — 형식은 관계 종류·확장자에서(원본 선언은 그림 형식 확인용으로만)."""
    defaults = {"rels": "application/vnd.openxmlformats-package.relationships+xml", "xml": "application/xml"}
    over = []
    odef = {}; oov = {}; ct_ok = False
    if orig_ct is not None:
        try:
            CT = "{http://schemas.openxmlformats.org/package/2006/content-types}"
            root = hwpx_std.safe_parse(orig_ct); ct_ok = root.tag == CT + "Types"
            for el in root:
                if el.tag == CT + "Default": odef[(el.get("Extension") or "").lower()] = el.get("ContentType") or ""
                elif el.tag == CT + "Override": oov[(el.get("PartName") or "").lstrip("/")] = el.get("ContentType") or ""
                else: ct_ok = False          # 모르는 요소·이름공간 → 원본 바이트 재사용 안 함
        except Exception: ct_ok = False
    for p in sorted(keep):
        ct = keep[p]; ext = p.rsplit(".", 1)[-1].lower() if "." in p.rsplit("/", 1)[-1] else ""
        if ct: over.append((p, ct)); continue
        if ext == "xml": continue
        c = _EXT_CT.get(ext)
        if c is None:
            c0 = oov.get(p) or odef.get(ext) or ""
            if re.fullmatch(r"image/[A-Za-z0-9.+-]{1,60}", c0): over.append((p, c0)); continue
            raise KeepFail("형식 모를 보존 파트: %s" % p)
        defaults.setdefault(ext, c)
    # 원본 선언과 뜻이 같으면(확장자 기본값·파트 재정의 집합 동일) 원본 바이트 그대로 — 안 바뀐 문서는 파일째 같게
    if ct_ok and {e: c for e, c in odef.items()} == {e.lower(): c for e, c in defaults.items()} and oov == dict(over):
        return orig_ct
    o = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">']
    o += ['<Default Extension="%s" ContentType="%s"/>' % (docxconv._x(e), c) for e, c in defaults.items()]
    o += ['<Override PartName="/%s" ContentType="%s"/>' % (docxconv._x(p), c) for p, c in over]
    o.append("</Types>")
    return "".join(o).encode("utf-8")

def _numinfo(raw):
    """numbering.xml → ({numId: {ilvl: fmt}}, max abstractNumId, max numId)."""
    if raw is None: return {}, -1, 0
    root = hwpx_std.safe_parse(raw); W = "{%s}" % W_NS
    absf = {}; mabs = -1; mnum = 0; nums = {}
    for a in root.findall(W + "abstractNum"):
        aid = a.get(W + "abstractNumId") or ""
        if docxconv._isint(aid): mabs = max(mabs, int(aid))
        absf[aid] = {l.get(W + "ilvl"): ((l.find(W + "numFmt").get(W + "val") if l.find(W + "numFmt") is not None else "decimal")) for l in a.findall(W + "lvl")}
    for n in root.findall(W + "num"):
        nid = n.get(W + "numId") or ""
        if docxconv._isint(nid): mnum = max(mnum, int(nid))
        a = n.find(W + "abstractNumId")
        nums[nid] = absf.get(a.get(W + "val") if a is not None else None, {})
    return nums, mabs, mnum

def _kind(nums, nid, ilvl):
    f = (nums.get(nid) or {}).get(ilvl or "0")
    return None if f is None else ("bullet" if f == "bullet" else "number")

def _docx_keep(ix, nm, kept, items, G):
    files = ix["files"]; sc = ix["scans"]["word/document.xml"]; raw = sc["raw"]
    keep, rels_out, doc_ok, doc_rels, drp = _docx_retain(files)
    tok = "zqHWKEEP%sqz" % secrets.token_hex(8)
    units = [v for t, v in items if t == "new"]
    for rid in sc["outer"]:
        if rid in doc_ok and not doc_ok[rid][0]: raise KeepFail("구역 설정이 허용 밖 관계를 씀")
    out_rels = [r for r in doc_rels if doc_ok[r[0]][0]]
    added = {}   # 새 파트
    numxml = files.get("word/numbering.xml"); styxml = files.get("word/styles.xml")
    if numxml is not None: _safe_xml(numxml)
    if styxml is not None: _safe_xml(styxml)
    gen_units = []; regen = 0; exp = []
    if units:
        if items[-1][0] == "new" and nm["blocks"][items[-1][1][-1]]["type"] == "table": raise KeepFail("문서가 표로 끝남")
        gen = docxconv.html_to_docx(hanword_model.render(_gen_model(nm, units, tok)))
        gz = zipfile.ZipFile(io.BytesIO(gen)); gf = {n: gz.read(n) for n in gz.namelist()}
        exp = _unit_hashes(gen, "docx", tok, len(units))
        gen_units, gdecls = _gen_units(gf["word/document.xml"], "docx", len(units), tok)
        grel = {r[0]: r for r in _parse_rels(gf["word/_rels/document.xml.rels"])}
        gnums, _, _ = _numinfo(gf.get("word/numbering.xml"))
        onums, mabs, mnum = _numinfo(numxml)
        ostyle = set(v.encode() for v in _attr_values(styxml, "style", ("styleId",))) if styxml is not None else set()
        need_styles = set(); new_nums = {}; num_add_abs = []; num_add_num = []
        used = set(r[0] for r in out_rels); rn = [0]
        def newrid():
            while True:
                rn[0] += 1; rid = "rIdHwk%d" % rn[0]
                if rid not in used and rid not in doc_ok: used.add(rid); return rid
        dn = [max([0] + [_max_int(_attr_values(files[p], "docPr", ("id",))) for p in files
                          if p.startswith("word/") and p.endswith(".xml") and len(files[p]) <= MAX_PART])]   # 머리말·미주 등 모든 파트의 그림 id 뒤
        media_n = [0]
        def media_name(ext):
            while True:
                media_n[0] += 1; p = "word/media/hwk_image%d.%s" % (media_n[0], ext)
                if p not in files and p not in added: return p
        gnum_xml = (gf.get("word/numbering.xml") or b"").decode("utf-8")
        def neighbor_num(pos, kind, ilvl):
            for d in (-1, 1):
                q = pos + d
                if 0 <= q < len(items) and items[q][0] == "keep":
                    es = G[items[q][1]]["elems"]
                    kid = es[-1][1] if d < 0 else es[0][1]
                    nid = kid.num.get("numId"); il = kid.num.get("ilvl") or "0"
                    if nid and il == (ilvl or "0") and _kind(onums, nid, il) == kind: return nid
            return None
        upos = [k for k, (t, _) in enumerate(items) if t == "new"]
        enxml = files.get("word/endnotes.xml"); gen_en = (gf.get("word/endnotes.xml") or b"").decode("utf-8")
        if enxml is not None: _safe_xml(enxml)
        en_n = [max(1, _max_int(_attr_values(enxml, "endnote", ("id",))) if enxml is not None else 1)]
        note_map = {}; note_add = []
        for ui, u in enumerate(gen_units):
            nu = []
            for e in u:
                if b"footnoteReference" in e: raise KeepFail("다시 만든 덩이에 각주")
                def rnote(m):   # 생성 미주 → 원본 endnotes.xml 뒤에 새 id로
                    g = m.group(1).decode()
                    if g not in note_map:
                        mm_ = re.search(r'<w:endnote w:id="%s">.*?</w:endnote>' % g, gen_en, re.S)
                        if not mm_: raise KeepFail("생성 미주 없음")
                        en_n[0] += 1; note_map[g] = en_n[0]
                        note_add.append(re.sub(r'w:id="%s"' % g, 'w:id="%d"' % en_n[0], mm_.group(0), count=1))
                    return b'<w:endnoteReference w:id="%d"/>' % note_map[g]
                e = re.sub(rb'<w:endnoteReference w:id="(\d{1,9})"/>', rnote, e)
                def rrel(m):
                    old = m.group(2).decode()
                    r = grel.get(old)
                    if r is None: raise KeepFail("생성 관계 없음")
                    t = _rtype(r[1]); rid = newrid()
                    if r[3] == "External":
                        out_rels.append((rid, r[1], r[2], "External"))
                    elif t == "image":
                        src = _resolve("word/document.xml", r[2]); ext = src.rsplit(".", 1)[-1].lower()
                        dst = media_name(ext); added[dst] = gf[src]; keep[dst] = None
                        out_rels.append((rid, r[1], posixpath.relpath(dst, "word"), ""))
                    else: raise KeepFail("생성 관계 종류: %s" % t)
                    return m.group(1) + b'="' + rid.encode() + b'"'
                e = re.sub(rb'\b(r:(?:id|embed|link))="([^"]{1,100})"', rrel, e)
                def rdoc(m):
                    dn[0] += 1; return b'docPr id="%d"' % dn[0]
                e = re.sub(rb'docPr id="\d{1,9}"', rdoc, e)
                for s in re.findall(rb'<w:(?:pStyle|rStyle|tblStyle) w:val="([^"]{1,100})"', e):
                    if s not in ostyle: need_styles.add(s)
                mm = re.search(rb'<w:numId w:val="(\d{1,9})"/>', e)
                if mm:
                    g = mm.group(1).decode(); il = (re.search(rb'<w:ilvl w:val="(\d)"/>', e) or [None, b"0"])[1].decode()
                    kind = _kind(gnums, g, il)
                    if g not in new_nums:
                        nb = neighbor_num(upos[ui], kind, il)
                        if nb is None:
                            ne = re.search(r'<w:num w:numId="%s">.*?</w:num>' % g, gnum_xml, re.S)
                            ma = re.search(r'<w:abstractNumId w:val="(\d+)"/>', ne.group(0)) if ne else None
                            ab = re.search(r'<w:abstractNum w:abstractNumId="%s".*?</w:abstractNum>' % ma.group(1), gnum_xml, re.S) if ma else None
                            if not ab: raise KeepFail("생성 번호 정의 없음")
                            mabs += 1; mnum += 1
                            num_add_abs.append(re.sub(r'w:abstractNumId="\d+"', 'w:abstractNumId="%d"' % mabs, ab.group(0), count=1))
                            num_add_num.append(re.sub(r'<w:abstractNumId w:val="\d+"/>', '<w:abstractNumId w:val="%d"/>' % mabs,
                                                      re.sub(r'w:numId="\d+"', 'w:numId="%d"' % mnum, ne.group(0), count=1), count=1))
                            nb = str(mnum)
                        new_nums[g] = nb
                    e = e[:mm.start()] + b'<w:numId w:val="%s"/>' % new_nums[g].encode() + e[mm.end():]
                nu.append(_ns_fix(e, gdecls, sc["decls"]))
            gen_units[ui] = nu
        if note_add:
            for x in note_add:
                for st_ in re.findall(r'<w:(?:pStyle|rStyle) w:val="([^"]{1,100})"', x):
                    if st_.encode() not in ostyle: need_styles.add(st_.encode())
            if enxml is None:
                body_ = "".join(re.findall(r'<w:endnote w:type="(?:separator|continuationSeparator)".*?</w:endnote>', gen_en, re.S)) + "".join(note_add)
                added["word/endnotes.xml"] = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:endnotes xmlns:w="%s" xmlns:r="%s">%s</w:endnotes>'
                                              % (W_NS, R_NS, body_)).encode("utf-8")
                keep["word/endnotes.xml"] = _CT_BY_REL["endnotes"]
                out_rels.append((newrid(), RT + "endnotes", "endnotes.xml", ""))
            else:
                if _scan_root_prefix(enxml, W_NS) != "w": raise KeepFail("endnotes 접두어")
                i = enxml.rfind(b"</w:endnotes>")
                if i < 0: raise KeepFail("endnotes 끝 없음")
                added["word/endnotes.xml"] = enxml[:i] + "".join(note_add).encode("utf-8") + enxml[i:]
        # styles: 다시 만든 조각이 부르는데 원본에 없는 스타일 정의만 덧붙임
        if need_styles:
            gst = gf["word/styles.xml"].decode("utf-8")
            if styxml is None:
                added["word/styles.xml"] = gf["word/styles.xml"]; keep["word/styles.xml"] = _CT_BY_REL["styles"]
                out_rels.append((newrid(), RT + "styles", "styles.xml", ""))
            else:
                if _scan_root_prefix(styxml, W_NS) != "w": raise KeepFail("styles 접두어")
                adds = []
                for s in sorted(need_styles):
                    m = re.search(r'<w:style [^>]*w:styleId="%s".*?</w:style>' % re.escape(s.decode()), gst, re.S)
                    if not m: raise KeepFail("생성 스타일 없음")
                    x = m.group(0)
                    for tag in ("basedOn", "next", "link"):
                        v = re.search(r'<w:%s w:val="([^"]*)"/>' % tag, x)
                        if v and v.group(1).encode() not in ostyle and v.group(1).encode() not in need_styles: x = x.replace(v.group(0), "")
                    adds.append(x)
                i = styxml.rfind(b"</w:styles>")
                if i < 0: raise KeepFail("styles 끝 없음")
                added["word/styles.xml"] = styxml[:i] + "".join(adds).encode("utf-8") + styxml[i:]
        if num_add_num:
            if numxml is None:
                added["word/numbering.xml"] = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:numbering xmlns:w="%s">%s%s</w:numbering>'
                                               % (W_NS, "".join(num_add_abs), "".join(num_add_num))).encode("utf-8")
                keep["word/numbering.xml"] = _CT_BY_REL["numbering"]
                out_rels.append((newrid(), RT + "numbering", "numbering.xml", ""))
            else:
                if _scan_root_prefix(numxml, W_NS) != "w": raise KeepFail("numbering 접두어")
                s = numxml
                m = re.search(rb"<w:num\b", s)
                ia = m.start() if m else s.rfind(b"</w:numbering>")
                if ia < 0: raise KeepFail("numbering 끝 없음")
                s = s[:ia] + "".join(num_add_abs).encode() + s[ia:]
                ie = s.rfind(b"</w:numbering>")
                added["word/numbering.xml"] = s[:ie] + "".join(num_add_num).encode() + s[ie:]
    # 본문 조립
    parts = [raw[:sc["prefix_end"]]]; ui = 0
    for t, v in items:
        if t == "keep":
            for e in G[v]["elems"]: parts.append(raw[e[1].start:e[1].end])
        else:
            parts.extend(gen_units[ui]); ui += 1; regen += len(v)
    parts.append(raw[sc["suffix"]:])
    doc = b"".join(parts)
    # 문서가 부르는 관계만 남김(구조 관계는 늘)
    refd = set(sc["outer"])
    for t, v in items:
        if t == "keep":
            for e in G[v]["elems"]: refd |= e[1].refs
    has_com = any(e[1].com for t, v in items if t == "keep" for e in G[v]["elems"])
    final_rels = []
    for r in out_rels:
        t = _rtype(r[1])
        if t in _COMMENT_RELS and not has_com: continue   # 메모 본문은 남은 덩이가 메모를 부를 때만(지운 덩이의 메모 노출 막기)
        if r[0].startswith("rIdHwk") or (t in _DOC_STRUCT) or r[0] in refd: final_rels.append(r)
    for r in final_rels:
        if r[3] != "External":
            p = _resolve("word/document.xml", r[2])
            if p not in keep: keep[p] = _CT_BY_REL.get(_rtype(r[1]))
    if "docProps/core.xml" in keep: added["docProps/core.xml"] = _CORE_XML   # 작성자·제목 등 옛 메타 대신 새로(§19: app·custom만 보존)
    # 미주·각주: 남은 본문이 부르는 것만(지운 덩이의 미주 노출 막기)
    for part, ref, el in (("word/endnotes.xml", "endnoteReference", "endnote"), ("word/footnotes.xml", "footnoteReference", "footnote"),
                          ("word/comments.xml", "commentReference", "comment")):
        if part in keep and (part in added or part in files):
            ids = set(_attr_values(doc, ref, ("id",)))
            src = added.get(part, files.get(part))
            pr = _prune_children(src, el, ids)
            if pr != src: added[part] = pr
    settings_fix = []
    _docx_closure(files, keep, [p for p in keep if p != "word/document.xml"], rels_out, settings_fix, added)
    if settings_fix and "word/settings.xml" in keep:
        added["word/settings.xml"] = re.sub(rb'<w:attachedTemplate\b[^<>]*/>', b"", files["word/settings.xml"])
    for sp in [p for p in keep if not p.endswith(".rels")]:   # 편지 병합 원본(ODBC·UNC 연결문) = 열 때 SQL 실행 요청 → 제거. 이름 무관(설정 파트 개명 우회)
        src = added.get(sp, files.get(sp))
        if src is not None and _MM.search(src):
            new = re.sub(rb"<([A-Za-z_][\w.-]*:)?mailMerge\b[^<>]*/>|<([A-Za-z_][\w.-]*:)?mailMerge\b.*?</\2mailMerge\s*>", b"", src, flags=re.S)
            if _MM.search(new): raise KeepFail("settings 편지 병합 제거 불가")
            added[sp] = new
    rels_out[drp] = files[drp] if (drp in files and final_rels == doc_rels) else _rels_xml(final_rels)
    keep[drp] = None
    out = {}
    for p in keep:
        if p in added: out[p] = added[p]
        elif p in files: out[p] = files[p]
        elif p == drp: pass
        else: raise KeepFail("보존 파트 없음: %s" % p)
    out["word/document.xml"] = doc
    out["_rels/.rels"] = files["_rels/.rels"]
    for rp, data in rels_out.items(): out[rp] = data
    for rp in [_rels_path(p) for p in list(out)]:
        if rp in files and rp not in out: out[rp] = files[rp]
    for p, data in out.items():
        if p.endswith((".xml", ".rels")) and p != "word/document.xml" and p not in added: _safe_xml(data)
        _text_utf8(data, keep.get(p) or "")
        ct = keep.get(p) or ""
        if ct.startswith(("application/vnd.openxmlformats", "application/vnd.ms-word")) and ct.endswith("xml") and not re.fullmatch(r"[A-Za-z0-9_./-]{1,200}\.xml", p):
            raise KeepFail("XML 파트 이름 이상: %s" % p)   # 확장자 바꿔 검사 우회 차단
        if not p.endswith(".rels") and data.lstrip(b"\xef\xbb\xbf \t\r\n")[:1] == b"<" and fields_bad(data, "docx"):
            raise KeepFail("허용 밖 필드: %s" % p)   # 이름 무관 XML 모양 파트 전부(본문·머리말·메모·주석·설정)
    ctkeep = {p: c for p, c in keep.items() if p in out and not p.endswith(".rels")}
    out["[Content_Types].xml"] = _docx_ct(ctkeep, files, files.get("[Content_Types].xml"))
    order = [i.filename for i in ix["infos"] if i.filename in out] + sorted(p for p in out if p not in files)
    data = _zip([(n, out[n]) for n in order], {i.filename: i for i in ix["infos"]})
    same = [n for n in order if n in files and out[n] == files[n]]
    dropped = sorted(n for n in files if n not in out)
    return data, same, dropped, regen, exp

_COMMENT_RELS = {"comments", "people", "commentsExtended", "commentsIds", "commentsExtensible"}
_CORE_XML = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
             'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:creator>한워드</dc:creator></cp:coreProperties>').encode("utf-8")
_NOTE_SEP = ("separator", "continuationSeparator", "continuationNotice")

def _prune_children(raw, local, keep_ids):
    """루트 바로 아래 local 요소 중 id가 keep_ids에 없는 것(구분선 종류 제외)을 바이트 그대로 잘라냄. 선형·DTD 거부."""
    p = pyexpat.ParserCreate(namespace_separator=" "); spans = []; depth = [0]; n = [0]
    def no(*a): raise KeepFail("DTD/엔티티 선언 금지")
    def start(name, a):
        n[0] += 1
        if n[0] > MAX_ELEMS: raise KeepFail("XML 요소가 너무 많음")
        depth[0] += 1
        if depth[0] == 2 and name.rsplit(" ", 1)[-1] == local:
            at = {k.rsplit(" ", 1)[-1]: v for k, v in a.items()}
            drop = at.get("type") not in _NOTE_SEP and at.get("id") not in keep_ids
            spans.append([p.CurrentByteIndex, None, drop])
    def end(name):
        if depth[0] == 2 and spans and spans[-1][1] is None and name.rsplit(" ", 1)[-1] == local:
            i = p.CurrentByteIndex
            spans[-1][1] = (raw.index(b">", i) + 1) if raw[i + 1:i + 2] == b"/" else _tag_end(raw, i) + 1
        depth[0] -= 1
    p.StartDoctypeDeclHandler = no; p.EntityDeclHandler = no; p.StartElementHandler = start; p.EndElementHandler = end
    try: p.Parse(raw, True)
    except pyexpat.ExpatError: raise KeepFail("XML 손상")
    if not any(s[2] for s in spans): return raw
    out = []; pos = 0
    for a, b, drop in spans:
        if drop: out.append(raw[pos:a]); pos = b
    out.append(raw[pos:])
    return b"".join(out)

def _scan_root_prefix(raw, uri):
    p = pyexpat.ParserCreate(namespace_separator=" "); d = {}; st = {"done": False}
    def ns(prefix, u):
        if not st["done"]: d[prefix or ""] = u
    def start(name, attrs): st["done"] = True; raise StopIteration
    def no(*a): raise KeepFail("DTD")
    p.StartNamespaceDeclHandler = ns; p.StartElementHandler = start; p.StartDoctypeDeclHandler = no; p.EntityDeclHandler = no
    try: p.Parse(raw, True)
    except StopIteration: pass
    except pyexpat.ExpatError: raise KeepFail("XML 손상")
    return _prefix_of(d, uri)

# ═════════════════════════════ K2 진입점 ═════════════════════════════
def keep_blocks(orig, doc, fmt):
    """K2 시도. 성공 = (bytes, report), 불가 = KeepFail."""
    html, nm = new_model(doc)
    ix = _index(orig, fmt)
    if nm["meta"] != ix["model"]["meta"]: raise KeepFail("문서 설정(meta)이 바뀜 — 구역 설정 되씀 불가")
    G = _groups(ix)
    a = [h for g in G for h in g["hashes"]]
    b = block_hashes(nm)
    ma = align(a, b)
    def keepable(g):
        for e in g["elems"]:
            if e[1].end - e[1].start > MAX_FRAG or e[1].bad: return False
        return True
    if fmt == "docx":
        try: _, _, doc_ok, _, _ = _docx_retain(ix["files"])
        except KeepFail: raise
        def keepable(g, _k=keepable):
            if not _k(g): return False
            return all(doc_ok.get(r, (False,))[0] for e in g["elems"] for r in e[1].refs)
    kept, orig_n = _plan(G, ma, len(b), keepable)
    demoted = 0; bysec = first_g = None
    for _ in range(64):
        items = _items(G, kept, len(b))
        try:
            if fmt == "hwpx": bysec, first_g = _hwpx_place(items, G, len(ix["pnames"]), nm)
            break
        except _Demote as d:
            del kept[d.gi]; demoted += 1
    else: raise KeepFail("강등 반복 상한")
    unk = sum(e[1].unk for gi, g in enumerate(G) if gi not in kept for e in g["elems"])
    if fmt == "hwpx":
        secs, hdr, bins, regen, exp = _hwpx_keep(ix, nm, bysec, first_g, G)
        data, same, dropped = _hwpx_package(ix, secs, hdr, bins)
    else:
        data, same, dropped, regen, exp = _docx_keep(ix, nm, kept, items, G)
    # 검증: 같은 가져오기 경로로 다시 읽은 결과 = 유지 덩이(원본 해시) + 다시 만든 덩이(쓰기기 결과 되읽기 해시), meta 같음
    want = []; ui = 0; lossy = 0
    for t, v in items:
        if t == "keep": want.extend(G[v]["hashes"])
        else:
            want.extend(exp[ui]); lossy += sum(1 for x, y in zip(exp[ui], [b[j] for j in v]) if x != y) + abs(len(exp[ui]) - len(v)); ui += 1
    vm = _import_model(data, fmt)
    if block_hashes(vm) != want or vm["meta"] != nm["meta"]: raise KeepFail("되읽기 검증 불일치")
    kb = sum(len(G[gi]["hashes"]) for gi in kept)
    return data, {"level": "blocks", "kept_blocks": kb, "regenerated": regen, "parts": same, "dropped_parts": dropped,
                  "counts": {"orig": orig_n, "demoted": demoted, "lossy": lossy, "unkept": unk, "groups": len(G), "kept_groups": len(kept),
                             "blocks": len(b)}, "why": ""}

# ═════════════════════════════ K1 (전체 다시 만들기 + 허용 파트) ═════════════════════════════
_K1_DOCX = re.compile(r"(docProps/(app|custom)\.xml|customXml/[A-Za-z0-9_.-]{1,100}|customXml/_rels/[A-Za-z0-9_.-]{1,100}\.rels|word/theme/[A-Za-z0-9_.-]{1,100}\.xml|word/webSettings\.xml)")
_K1_HWPX = ("version.xml", "settings.xml")

def keep_parts(orig, gen, fmt):
    """다시 만든 꾸러미(gen)에 원본의 허용 목록 파트를 옮겨 담음 → (bytes, 옮긴 파트 목록)."""
    files, infos = _read_zip(orig, fmt)
    gz = zipfile.ZipFile(io.BytesIO(gen)); gi = gz.infolist(); gf = {i.filename: gz.read(i) for i in gi}
    moved = []
    if fmt == "hwpx":
        for n in _K1_HWPX:
            if n in files and len(files[n]) <= MAX_FRAG:
                _safe_xml(files[n]); gf[n] = files[n]; moved.append(n)
    else:
        # 원본이 실제로 부르는 파트만(고아 파트 = 지운 내용일 수 있음): 꾸러미 rels + 문서 rels(+ customXml 항목의 itemProps)
        reach = set()
        for src, rp in (("", "_rels/.rels"), ("word/document.xml", "word/_rels/document.xml.rels")):
            if rp in files:
                for r in _parse_rels(files[rp]):
                    if r[3] != "External":
                        q = _resolve(src, r[2])
                        if q: reach.add(q)
        for q in list(reach):
            if q.startswith("customXml/") and _rels_path(q) in files:
                for r in _parse_rels(files[_rels_path(q)]):
                    if r[3] != "External" and _resolve(q, r[2]): reach.add(_resolve(q, r[2]))
        cand = [n for n in files if _K1_DOCX.fullmatch(n) and len(files[n]) <= MAX_FRAG
                and (n in reach or (n.startswith("customXml/_rels/") and n[len("customXml/_rels/"):-len(".rels")] in {x.split("/", 1)[1] for x in reach if x.startswith("customXml/")}))]
        for n in cand: _safe_xml(files[n])
        prl = _parse_rels(gf["_rels/.rels"]); drl = _parse_rels(gf["word/_rels/document.xml.rels"])
        pids = {r[0] for r in prl}; dids = {r[0] for r in drl}
        def nid(ids):
            k = 0
            while True:
                k += 1; rid = "rIdK%d" % k
                if rid not in ids: ids.add(rid); return rid
        cts = {}
        for n in cand:
            if n.startswith("customXml/_rels/"): continue
            if n == "docProps/app.xml":
                gf[n] = files[n]; moved.append(n); continue
            if n == "docProps/custom.xml":
                prl.append((nid(pids), RT + "custom-properties", n, "")); cts[n] = _CT_BY_REL["custom-properties"]
            elif n.startswith("word/theme/"):
                if any(_rtype(r[1]) == "theme" for r in drl): continue
                drl.append((nid(dids), RT + "theme", n[len("word/"):], "")); cts[n] = _CT_BY_REL["theme"]
            elif n == "word/webSettings.xml":
                drl.append((nid(dids), RT + "webSettings", "webSettings.xml", "")); cts[n] = _CT_BY_REL["webSettings"]
            elif n.startswith("customXml/"):
                if re.fullmatch(r"customXml/item\d+\.xml", n):
                    rp = _rels_path(n)
                    if rp in files:
                        rl = _parse_rels(files[rp])
                        if not all(_rtype(r[1]) == "customXmlProps" and r[3] != "External" and _resolve(n, r[2]) in files for r in rl): continue
                        gf[rp] = files[rp]
                    drl.append((nid(dids), RT + "customXml", "../" + n, ""))
                elif re.fullmatch(r"customXml/itemProps\d+\.xml", n): cts[n] = _CT_BY_REL["customXmlProps"]
                else: continue
            else: continue
            gf[n] = files[n]; moved.append(n)
        gf["_rels/.rels"] = _rels_xml(prl); gf["word/_rels/document.xml.rels"] = _rels_xml(drl)
        if cts:
            ct = gf["[Content_Types].xml"]; i = ct.rfind(b"</Types>")
            gf["[Content_Types].xml"] = ct[:i] + "".join('<Override PartName="/%s" ContentType="%s"/>' % (docxconv._x(p), c) for p, c in sorted(cts.items())).encode() + ct[i:]
    order = [i.filename for i in gi] + [n for n in gf if n not in {i.filename for i in gi}]
    return _zip([(n, gf[n]) for n in order], {i.filename: i for i in gi}), moved

# ═════════════════════════════ 공개 진입점 ═════════════════════════════
def export_keep(orig, doc, fmt, orig_sha256=None, page=None, keep=True, export=None):
    """원본(같은 형식) 위에 저장. → (bytes, report{level:"blocks"|"parts"|"none", kept_blocks, regenerated, parts, counts, why}).
       export(html, fmt, page) = 전체 다시 만들기 함수(코어 export_html). 실패 경로는 전부 다시 만들기로 폴백."""
    html, nm0 = new_model(doc)
    full = lambda: export(html, fmt, page)
    rep = {"level": "none", "kept_blocks": 0, "regenerated": len(nm0["blocks"]), "parts": [], "counts": {"orig": 0}, "why": ""}
    def none(why):
        r = dict(rep, why=why); return full(), r
    if not keep: return none("keep 꺼짐")
    if fmt not in ("hwpx", "docx"): return none("되씀 없는 형식")
    if orig is None: return none("원본 없음")
    if orig_sha256 is not None and sha256(orig) != str(orig_sha256).lower(): return none("원본 sha256 불일치")
    if fmt == "hwpx" and hwpxconv.HWPX_ENGINE == "pyhwpx" and hwpxconv.HwpxDocument is not None: return none("python-hwpx 엔진")
    try:
        if (orig[:4] != b"PK\x03\x04") or (("word/document.xml" in zipfile.ZipFile(io.BytesIO(orig)).namelist()) != (fmt == "docx")):
            return none("원본 형식이 다름")
    except Exception: return none("원본 zip 손상")
    if page is None:
        try: return keep_blocks(orig, doc, fmt)
        except (KeepFail, HwpxInputError, hanword_model.ModelError, hwpx_std.HwpxStdError) as e: why = "K2: %s" % e
        except Exception as e:   # MemoryError 포함 — 어떤 실패도 다시 만들기로(KeyboardInterrupt·SystemExit은 통과)
            why = "K2 내부(%s)" % type(e).__name__
    else: why = "용지 지정(page) — 구역 설정 되씀 불가"
    gen = full()
    try:
        data, moved = keep_parts(orig, gen, fmt)
        _import_model(data, fmt)          # 깨진 꾸러미는 내보내지 않음
        return data, dict(rep, level="parts", parts=moved, why=why)
    except Exception as e:
        return gen, dict(rep, why="%s; K1: %s" % (why, (str(e) if isinstance(e, KeepFail) else type(e).__name__)))
