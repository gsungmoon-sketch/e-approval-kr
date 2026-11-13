#!/usr/bin/env python3
# hwpxconv.py — HTML↔HWPX 변환(기본 엔진 = 표준 라이브러리 hwpx_std, python-hwpx 는 선택적 폴백).
#   export: stdin=에디터 HTML → stdout=.hwpx 바이트
#   import: stdin=.hwpx 바이트 → stdout=HTML
# RG DS 한워드(wp)가 ssh로 호출. 한글이 여는 정식 HWPX를 검증된 라이브러리로 생성.
import sys, io, re, os, html as htmlmod, subprocess, tempfile, base64, shutil, zipfile, zlib
from html.parser import HTMLParser
try:
    from hwpx import HwpxDocument          # python-hwpx(lxml) — HWPX 입출력에만 필요. 없으면 문서모델(P)·DOCX는 계속 동작
except ImportError:                        # pragma: no cover
    HwpxDocument = None

_HPNS="{http://www.hancom.co.kr/hwpml/2011/paragraph}"   # #20 하이퍼링크 필드컨트롤(hp:ctrl fieldBegin/End)용
def _safe_link_url(u):   # 왕복 공통 스킴 화이트리스트(에디터 normLinkUrl과 동조·절대 스킴만 — javascript: 등 차단)
    u=(u or "").strip()
    if not u: return None
    low=re.sub(r"[\s\x00-\x1f]+","",u).lower()
    return u if re.match(r"^(https?:|mailto:|tel:)", low) else None

def _hancom_unesc(s):   # 한컴 필드 Command 값: '\\x'=문자 x 그대로, 이스케이프 안 된 첫 ';'에서 끝(뒤 ';1;0;0;' 등 옵션 꼬리 제거). 선형
    out=[]; i=0; n=len(s)
    while i<n:
        c=s[i]
        if c=="\\" and i+1<n: out.append(s[i+1]); i+=2; continue
        if c==";": break
        out.append(c); i+=1
    return "".join(out)

def _field_link_url(fb):
    """hp:fieldBegin(type=HYPERLINK) → 안전 URL 또는 None.
       한컴형: <hp:parameters><hp:stringParam name="Path">URL</…><hp:stringParam name="Command">https\\://…;1;0;0;</…> — http/https/mailto만.
       python-hwpx·구 한워드형: name 속성=URL(왕복 호환, _safe_link_url 규칙)."""
    path=cmd=None; k=0
    for prm in fb:
        if not isinstance(prm.tag,str) or not prm.tag.endswith("}parameters"): continue
        for sp in prm:
            k+=1
            if k>64: break   # 매개변수 상한
            if isinstance(sp.tag,str) and sp.tag.endswith("}stringParam"):
                nm=sp.get("name"); v=(sp.text or "")[:4096]
                if nm=="Path" and path is None: path=v
                elif nm=="Command" and cmd is None: cmd=v
    for cand in (path, _hancom_unesc(path) if path else None, _hancom_unesc(cmd) if cmd else None):
        u=_safe_link_url(cand)
        if u and len(u)<=2000 and re.match(r"^(https?:|mailto:)", re.sub(r"[\s\x00-\x1f]+","",u).lower()): return u
    u=_safe_link_url(fb.get("name"))
    return u if u and len(u)<=2000 else None

# ── 하드닝(#18): 악성 hwpx(zip폭탄·XXE) 사전 차단 ──
class HwpxInputError(ValueError):
    """손상/악성 hwpx 입력(서버가 원격 폴백 없이 즉시 거부·마스킹)."""
    pass

_HWPX_MAXBYTES = int(os.environ.get("WP_HWPX_MAXBYTES", str(200 * 1024 * 1024)))  # 전개(비압축) 총량 상한
_HWPX_MAXFILES = int(os.environ.get("WP_HWPX_MAXFILES", "2000"))                  # 엔트리 수 상한
_HWPX_RATIO    = int(os.environ.get("WP_HWPX_RATIO", "200"))                      # 엔트리별 압축비 상한(폭탄)

ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)
def zip_put(z, name, data, compress=None):
    """결정적 zip 항목 쓰기 — 어느 OS에서든 같은 바이트(고정 시각·create_system=3(Unix)·0644)."""
    zi = zipfile.ZipInfo(name, ZIP_EPOCH)
    zi.create_system = 3
    zi.external_attr = 0o100644 << 16
    zi.compress_type = zipfile.ZIP_DEFLATED if compress is None else compress
    z.writestr(zi, data)

def check_zip_actual(zf, maxbytes, ratio, err):
    """zip 선언 크기를 믿지 않고 실제로 풀어 세며 상한 검사. 압축 방식은 STORED/DEFLATED만(bzip2/lzma 거부)."""
    total = 0
    for i in zf.infolist():
        if i.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise err("지원하지 않는 압축 방식")
        if i.flag_bits & 0x61: raise err("암호화/패치 zip 미지원")
        n = 0
        try:
            with zf.open(i) as f:
                while True:
                    chunk = f.read(1 << 20)
                    if not chunk: break
                    n += len(chunk); total += len(chunk)
                    if total > maxbytes: raise err("전개 크기 초과")
                    if n > (1 << 20) and n > max(1, i.compress_size) * ratio: raise err("압축비 과다(zip폭탄 의심)")
        except err: raise
        except zipfile.BadZipFile: raise err("zip 손상")
        except (zlib.error, EOFError, OSError, NotImplementedError, RuntimeError, ValueError): raise err("zip 손상")

def guard_hwpx_zip(data):
    """import 전 사전검사: zip폭탄(총량·개수·압축비)·XXE(DTD/ENTITY) 차단. 위반 시 HwpxInputError."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise HwpxInputError("hwpx(zip) 형식이 아님")
    infos = zf.infolist()
    if len(infos) > _HWPX_MAXFILES:
        raise HwpxInputError("엔트리 과다(%d)" % len(infos))
    check_zip_actual(zf, _HWPX_MAXBYTES, _HWPX_RATIO, HwpxInputError)
    for i in infos:      # XXE: XML/패키지 파트 전체에서 DTD/엔티티 선언 금지(주석 패딩·UTF-16 우회 차단, 2026-11-13)
        low = i.filename.lower()
        if low.endswith((".xml", ".hpf", ".rdf", ".rels")):
            try:
                raw = zf.read(i.filename)
            except Exception:
                continue
            if raw[:2] in (b"\xff\xfe", b"\xfe\xff") or b"\x00" in raw[:200]:
                try: raw = raw.decode("utf-16").encode("utf-8")
                except Exception: raise HwpxInputError("XML 인코딩 오류")
            raw = raw.lower()
            if b"<!doctype" in raw or b"<!entity" in raw:
                raise HwpxInputError("DTD/엔티티 선언 금지(XXE 차단)")

BLOCK = {"p","h1","h2","h3","li","div"}
HEAD = {"h1":(20,1),"h2":(16,2),"h3":(13,3)}
MAX_LIST_LEVEL = 8   # 목록 수준 0..8(DOCX ilvl 0..8 · HWPX heading level 0..8과 동일)

class ListHTML:
    """수준 있는 목록 항목 → 표준 중첩 HTML(<ul><li>a<ul><li>b</li></ul></li></ul>). 모델 render·DOCX·HWPX 읽기 공용.
       item(tag ul|ol, level, inner, attrs, key): key가 바뀌면(DOCX numId 등) 같은 수준에서도 새 목록.
       부모 항목 없이 수준이 뛰면 형제 형식(<ul><ul>)으로 연다(빈 항목을 만들지 않음 — 다시 읽어도 수준 동일)."""
    def __init__(self, out): self.out = out; self.st = []   # [tag, li_open, key]
    def _pop(self):
        t, lo, _ = self.st.pop()
        self.out.append(("</li>" if lo else "") + "</%s>" % t)
    def item(self, tag, level, inner, attrs="", key=None):
        lv = max(0, min(MAX_LIST_LEVEL, int(level or 0)))
        while len(self.st) > lv + 1: self._pop()
        if len(self.st) == lv + 1 and (self.st[-1][0] != tag or self.st[-1][2] != key): self._pop()
        if len(self.st) == lv + 1 and self.st[-1][1]: self.out.append("</li>"); self.st[-1][1] = False
        while len(self.st) < lv + 1:
            self.out.append("<%s>" % tag); self.st.append([tag, False, key])
        self.out.append("<li%s>%s" % (attrs, inner or "<br>")); self.st[-1][1] = True
    def close(self):
        while self.st: self._pop()
    def __bool__(self): return bool(self.st)

def rgb2hex(c):
    c=(c or "").strip()
    m=re.match(r"rgb\((\d+)[, ]+(\d+)[, ]+(\d+)\)",c)
    if m: return "#%02X%02X%02X"%(int(m[1]),int(m[2]),int(m[3]))
    if c.startswith("#"): return c.upper()
    return None

def _bghex(style):   # 셀 배경(background/background-color) → hex
    m=re.search(r"background(?:-color)?\s*:\s*([^;]+)", style or "", re.I)
    return rgb2hex(m.group(1).strip()) if m else None

def _pxprop(style, prop):   # style에서 'prop: Npx' 정수 px
    m=re.search(r"(?:^|;)\s*%s\s*:\s*(-?[\d.]+)px"%re.escape(prop), style or "", re.I)
    try: return max(-10**6, min(10**6, int(float(m.group(1))))) if m else 0
    except (ValueError, OverflowError): return 0

# ── 셀 테두리(선그리기) ↔ HWPX borderFill 매핑 (백로그 #7) ──
_BTYPE={"solid":"SOLID","dotted":"DOT","dashed":"DASH","double":"DOUBLE_SLIM","hidden":"NONE","none":"NONE"}
_BTYPE_REV={"SOLID":"solid","DASH":"dashed","DOT":"dotted","DOUBLE_SLIM":"double","DOUBLE":"double","NONE":"hidden"}
def _hex6(c):   # rgb2hex 결과를 6자리로(#RGB → #RRGGBB, HWPX color 속성용)
    h=rgb2hex(c)
    if h and re.match(r"^#[0-9A-F]{3}$",h): h="#"+"".join(x*2 for x in h[1:])
    return h if (h and re.match(r"^#[0-9A-F]{6}$",h)) else None
def _mmw(px):   # 에디터 굵기 1/2/3px → HWPX 선굵기(mm 눈금)
    return "0.12 mm" if px<1.5 else ("0.25 mm" if px<2.5 else "0.4 mm")
def _pxw(mm_s):   # 역방향: mm 문자열 → 1/2/3px
    try: mm=float(re.search(r"([\d.]+)",mm_s or "").group(1))
    except Exception: mm=0.12
    return 1 if mm<0.18 else (2 if mm<0.33 else 3)
def _parse_borders(style):   # 인라인 border/border-top…left → {변:(css스타일,px,색)}. 에디터 setEdge 산출형("2px dashed rgb(85,85,85)"·"1px hidden") 대상
    out={}
    for part in (style or "").split(";"):
        if ":" not in part: continue
        k,v=part.split(":",1); k=k.strip().lower(); v=v.strip()
        m=re.match(r"^border(?:-(top|right|bottom|left))?$",k)
        if not m: continue
        ms=re.search(r"\b(solid|dotted|dashed|double|hidden|none)\b",v)
        if not ms: continue
        mw=re.search(r"([\d.]+)px",v)
        mc=re.search(r"(rgb\([^)]*\)|#[0-9a-fA-F]{3,6})",v)
        val=(ms.group(1), float(mw.group(1)) if mw else 1.0, _hex6(mc.group(1)) if mc else None)
        for sd in ([m.group(1)] if m.group(1) else ["top","right","bottom","left"]): out[sd]=val
    return out
def _bf_edge_css(ent):   # import: borderFill 변 정보 → 셀 인라인 CSS(기본 SOLID 1px는 생략=노이즈 방지)
    css=""
    for side in ("top","bottom","left","right"):
        v=ent.get(side)
        if not v: continue
        st=_BTYPE_REV.get(v[0])
        if st is None: continue
        px=_pxw(v[1])
        if st=="solid" and px<=1: continue
        if st=="hidden": css+="border-%s:1px hidden;"%side
        else: css+="border-%s:%dpx %s %s;"%(side,px,st,(_hex6(v[2]) or "#555555"))
    return css

def parse_style(s):
    d={}
    for part in (s or "").split(";"):
        if ":" not in part: continue
        k,v=part.split(":",1); k=k.strip().lower(); v=v.strip()
        if k=="color": d["color"]=rgb2hex(v)
        elif k=="font-size":
            m=re.match(r"([\d.]+)pt",v) or re.match(r"([\d.]+)px",v)
            if m: d["size"]=round(float(m[1])*(1 if "pt" in v else 0.75))
        elif k=="background-color":
            h=rgb2hex(v);
            if h and h!="#FFFFFF": d["highlight"]=h
        elif k=="text-align": d["align"]=v.upper()
        elif k=="font-family": d["font"]=v
        elif k=="font-weight" and (v in("bold","bolder") or (v.isdigit() and int(v)>=600)): d["bold"]=True
        elif k=="font-style" and v=="italic": d["italic"]=True
        elif k=="text-decoration" or k=="text-decoration-line":
            if "underline" in v: d["underline"]=True
            if "line-through" in v: d["strike"]=True
        elif k=="vertical-align":   # 위/아래 첨자(D12)
            if v.startswith("super"): d["sup"]=True
            elif v.startswith("sub"): d["sub"]=True
    return d

MAX_CPARAS=10000   # 셀 하나의 문단 수 상한(변환기·모델 공용) — 넘으면 마지막 문단에 " "로 이어 붙임
MAX_CALIGN=64      # 셀 안 블록 정렬 스택 깊이 상한(닫히지 않은 블록 시작 누적 차단)
_M0={"bold":False,"italic":False,"underline":False,"strike":False,"sup":False,"sub":False,"color":None,"size":None,"highlight":None,"font":None,"link":None}
class _MStack(list):   # 인라인 서식 스택 + 단계별 병합 결과 캐시 → merged()=O(1)(텍스트 노드×스택 깊이 2차시간 차단)
    def __init__(self): super().__init__(); self._m=[_M0]
    def append(self, d):
        m=dict(self._m[-1])
        for k,v in d.items():
            if k in("bold","italic","underline","strike"): m[k]=m[k] or v
            elif k in("sup","sub"):   # 위/아래 첨자 = 서로 배타(안쪽이 이김)
                if v: m[k]=True; m["sub" if k=="sup" else "sup"]=False
            elif v is not None: m[k]=v
        self._m.append(m); super().append(d)
    def pop(self, *a):
        self._m.pop(); return super().pop()
    def top(self): return self._m[-1]

class P(HTMLParser):
    SKIP={"head","title","style","script","noscript"}   # 비콘텐츠 컨테이너(끝태그 있음)=깊이추적 스킵
    SKIP_VOID={"meta","link","base"}                     # 비콘텐츠 void(끝태그 없음)=깊이추적 금지(안 그러면 skip 영구상승→본문 소실)
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.paras=[]; self.cur=None; self.stack=_MStack(); self.block=None; self.lk=[]   # lk=목록종류 스택
        self.table=None; self.trow=None; self.curcell=None; self._objalign=None; self._tnest=0   # 표 캡처 상태(+개체 배치, 중첩깊이)
        self.skip=0    # head/title/style/script 등 비콘텐츠 내부 깊이(>0이면 텍스트·구조 무시)
        self._fnskip=0 # #25 미주 마커 sup.fn 내부 깊이(표시번호 "1)" 텍스트는 export에서 버림 — 한글이 자동 번호)
    def _inline_push(self, tag, ad):
        if tag in("b","strong"): self.stack.append({"bold":True})
        elif tag in("i","em"): self.stack.append({"italic":True})
        elif tag=="u": self.stack.append({"underline":True})
        elif tag in("s","strike","del"): self.stack.append({"strike":True})
        elif tag=="sup": self.stack.append({"sup":True})
        elif tag=="sub": self.stack.append({"sub":True})
        elif tag in("span","font"):
            d=parse_style(ad.get("style",""))
            if ad.get("color"): d["color"]=rgb2hex(ad["color"])
            self.stack.append({k:v for k,v in d.items() if k in("bold","italic","underline","strike","sup","sub","color","size","highlight","font")})
        elif tag=="a":   # #20 하이퍼링크: href를 run에 실어 export에서 필드컨트롤로(상대/# 등 비절대는 텍스트만)
            h=_safe_link_url(ad.get("href"))
            self.stack.append({"link":h} if h else {})
        else: self.stack.append({})
    def _newpara(self, tag, attrs):
        self.flush(); ad=dict(attrs); st=parse_style(ad.get("style",""))
        lk=self.lk[-1] if (tag=="li" and self.lk) else None
        mi=re.search(r"margin-left:\s*(\d+)", ad.get("style","")); indent=int(mi.group(1)) if mi else 0
        self.block={"tag":tag,"align":st.get("align"),"list":lk,"indent":indent,"runs":[]}
        if lk: self.block["level"]=min(MAX_LIST_LEVEL,len(self.lk)-1)   # 중첩 깊이(중첩형·형제형 <ul><ul> 모두 같은 깊이)
        self.cur=self.block["runs"]
    def flush(self):
        if self.block is not None: self.paras.append(self.block)
        self.block=None; self.cur=None
    # ── 셀 문단(2026-11-13): curcell["paras"]=[{"runs","align"}], 문단 2개 이상일 때만 유지(1개면 기존 단일 runs 형식) ──
    def _cpara(self):   # 현재 셀 문단(없으면 시작, 정렬=감싼 블록의 text-align)
        if self._cp is None:
            self._cp={"runs":[],"align":self._calign[-1] if self._calign else None}; self.cur=self._cp["runs"]
        return self._cp
    def _cclose(self):  # 블록 끝/셀 끝: 글자 없는 문단은 버림(br로 끝난 빈 줄만 앞서 append됨)
        cp=self._cp; self._cp=None; self.cur=None
        if cp is not None and any(r.get("text","").strip(" \t\r\n\f") for r in cp["runs"]): self._cappend(cp)
    def _cappend(self, cp):   # 셀 문단 추가 — 상한 도달 시 마지막 문단에 " "+내용 이어 붙임(글자 보존, 출력 w:p/hp:p 수 유계)
        ps=self.curcell["paras"]
        if len(ps)<MAX_CPARAS: ps.append(cp)
        else: ps[-1]["runs"].append({"text":" "}); ps[-1]["runs"].extend(cp["runs"])
    def _cfinish(self):  # td 닫힘: 평탄 runs(문단 사이 " ", 구형 소비자용) + 문단 1개 이하면 paras 제거
        self._cclose(); ps=self.curcell["paras"]; flat=[]
        for i,p in enumerate(ps):
            if i: flat.append({"text":" "})
            flat.extend(p["runs"])
        self.curcell["runs"]=flat
        if len(ps)<2: del self.curcell["paras"]
    def merged(self):
        return dict(self.stack.top())
    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP_VOID: return              # void 비콘텐츠(meta/link/base)=깊이추적 없이 무시
        if tag in self.SKIP: self.skip+=1; return     # 비콘텐츠 컨테이너 진입
        if self.skip: return                          # 비콘텐츠 내부 무시
        ad=dict(attrs)
        # ── #25 미주 마커: <sup class="fn" data-fn=id>1)</sup> → 앵커 run(위치 보존). 표 안은 번호 텍스트로 평탄화 ──
        if tag=="sup" and "fn" in (ad.get("class") or "").split():
            if self.table is None:
                if self.block is None: self._newpara("p",[])
                self.block["runs"].append({"endnote": ad.get("data-fn") or "", "text":""})
                self._fnskip+=1
            elif self.curcell is not None: self.stack.append({})   # 표 안 미주 마커 = 끝태그 sup의 pop 짝
            return
        # ── 개체 배치: objline 래퍼(글자처럼 줄)는 문단 만들지 않고 정렬만 캡처 ──
        if tag=="div" and "objline" in (ad.get("class") or ""):
            self.flush(); self._objalign=parse_style(ad.get("style","")).get("align"); return
        # ── 표 ──
        if tag=="table":
            if self.table is not None:        # 중첩 표=무시(바깥 표 상태 보존, 셀 내용은 텍스트로 평탄화)
                self._tnest+=1; return
            self.flush()
            self.table={"tag":"table","rows":[],"rowh":[],
                        "place": ad.get("data-place") or "char",
                        "left": _pxprop(ad.get("style",""),"left"),
                        "top": _pxprop(ad.get("style",""),"top"),
                        "width": _pxprop(ad.get("style",""),"width"),
                        "objalign": self._objalign}
            self.trow=None; self.curcell=None; self.stack=_MStack(); self._curtrh=0; return
        if self.table is not None:
            if self._tnest>0:                 # 중첩 표 내부=구조 무시(텍스트는 handle_data가 현재 셀로)
                if tag=="img" and self.curcell is not None:
                    self.curcell.setdefault("images",[]).append((ad.get("src",""), _pxprop(ad.get("style",""),"width")))
                return
            if tag=="tr": self.trow=[]; self._curtrh=_pxprop(ad.get("style",""),"height"); return
            if tag in("td","th"):
                st=parse_style(ad.get("style",""))
                self.curcell={"colspan":int(ad.get("colspan") or 1),"rowspan":int(ad.get("rowspan") or 1),
                              "bg":_bghex(ad.get("style","")),"align":st.get("align"),"runs":[],
                              "width":_pxprop(ad.get("style",""),"width"),
                              "borders":_parse_borders(ad.get("style","")),"paras":[]}
                self._cp=None; self._calign=[]; self._calover=0; self.cur=None; self.stack=_MStack(); return
            if self.curcell is not None:                 # 셀 안: <p>/<div> 등 블록·<br> = 셀 문단 경계(2026-11-13 셀 여러 문단)
                if tag=="br": self._cpara(); self._cappend(self._cp); self._cp=None   # br로 끝난 줄=빈 줄이어도 유지
                elif tag in BLOCK:
                    self._cclose(); al=parse_style(ad.get("style","")).get("align")
                    if len(self._calign)<MAX_CALIGN: self._calign.append(al or (self._calign[-1] if self._calign else None))
                    else: self._calover+=1
                elif tag=="img": self.curcell.setdefault("images",[]).append((ad.get("src",""), _pxprop(ad.get("style",""),"width")))   # 표 안 그림(src,width)
                elif tag not in BLOCK: self._inline_push(tag, ad)
            return
        # ── 표 밖 ──
        if tag=="img":
            st=ad.get("style","")
            place=ad.get("data-place") or "char"
            if self.block is not None and place=="char":   # 문단 안 글자처럼 그림/도형(2026-07-18): 인라인 run으로 위치 보존(미주 마커 패턴)
                run={"img": ad.get("src",""), "width": _pxprop(st,"width") or None, "text":""}
                if ad.get("data-shape-kind"):              # 도형 메타(클라 주석) → 네이티브 OWPML 도형 후보(PNG=폴백)
                    run["shape"]={"kind":ad.get("data-shape-kind"),"fill":ad.get("data-shape-fill") or "none",
                                  "stroke":ad.get("data-shape-stroke") or "#2f6fd6","sw":ad.get("data-shape-sw") or "2",
                                  "w":_pxprop(st,"width") or 120,"h":_pxprop(st,"height") or 72}
                self.block["runs"].append(run)
                return
            self.flush()
            zm=re.search(r"z-index:\s*(-?\d+)", st)
            self.paras.append({"tag":"img","src":ad.get("src",""),"align":None,"list":None,"runs":[],
                               "place": place,
                               "left": _pxprop(st,"left"), "top": _pxprop(st,"top"),
                               "z": int(zm.group(1)) if zm else 0,
                               "width": _pxprop(st,"width") or None}); return
        if tag=="ul": self.lk.append("bullet"); return
        if tag=="ol": self.lk.append("number"); return
        if tag in BLOCK: self._newpara(tag, attrs); return
        if tag=="br":
            if self.block is None: self._newpara("p",[])
            else: self.flush(); self._newpara("p",[])
            return
        self._inline_push(tag, ad)
    def handle_endtag(self, tag):
        if tag in self.SKIP: self.skip=max(0,self.skip-1); return   # 비콘텐츠 이탈
        if self.skip: return
        if tag=="sup" and self._fnskip: self._fnskip-=1; return     # #25 미주 마커 닫힘(스택 푸시 없었음)
        if self.table is not None:
            if self._tnest>0 and tag in("td","th","tr"): return    # 중첩 표 구조 무시(table 닫힘만 아래서 처리)
            if tag in("td","th"):
                if self.curcell is not None:
                    self._cfinish()
                    if self.trow is not None: self.trow.append(self.curcell)
                self.curcell=None; self.cur=None; self.stack=_MStack()
            elif tag=="tr":
                if self.trow is not None:
                    self.table["rows"].append(self.trow); self.table["rowh"].append(getattr(self,"_curtrh",0)); self.trow=None
            elif tag=="table":
                if self._tnest>0: self._tnest-=1; return     # 중첩 표 닫힘=바깥 표 유지
                self.paras.append(self.table); self.table=None; self.curcell=None; self.trow=None; self._objalign=None
            elif self.curcell is not None and tag in("b","strong","i","em","u","s","strike","del","span","font","a","sup","sub"):
                if self.stack: self.stack.pop()
            elif self.curcell is not None and tag in BLOCK and self._tnest==0:
                self._cclose()
                if self._calover: self._calover-=1
                elif self._calign: self._calign.pop()
            return
        if tag in("ul","ol"):
            if self.lk: self.lk.pop()
        elif tag in BLOCK: self.flush()
        elif tag in("b","strong","i","em","u","s","strike","del","span","font","a","sup","sub"):
            if self.stack: self.stack.pop()
    def handle_data(self, data):
        if self.skip: return                          # head/style/script 내부 텍스트 버림
        if self._fnskip: return                       # #25 미주 마커 표시번호("1)")는 export에서 버림
        if not data: return
        if self.curcell is not None:
            if self._cp is None and not data.strip(" \t\r\n\f"): return   # 셀 문단 사이 서식용 공백
            self._cpara()["runs"].append(dict(self.merged(), text=data)); return
        if self.table is not None: return                # 표 안·셀 밖(공백) 무시
        if self.block is None:
            if not data.strip(): return
            self._newpara("p",[])
        st=self.merged(); st["text"]=data; self.block["runs"].append(st)

class _PP(P):   # (2026-11-13) 쪽 나누기 보존 — HWPX pageBreak·DOCX w:br page 공용 + <br> 정규화
    """hwpxconv.P + ①쪽 나누기(.pgbreak: 에디터 div / TXT·PDF 열기 p) 인식 ②문단 안 <br> 정규화:
       빈 문단의 자리표시 <br>(<p><br></p>)은 무시(예전엔 빈 줄 하나가 문단 2개로), 끝의 <br>도 무시,
       글자 사이 <br>만 같은 서식의 새 문단으로 나눔."""
    def __init__(self):
        super().__init__(); self._pg = 0; self._pgtag = None; self._brpend = False; self._brlead = 0; self._lstrip = False
    def flush(self):
        extra = self._brlead - 1 if (self.block is not None and self._brlead > 1 and not self._has_content()) else 0
        self._brpend = False; self._brlead = 0; self._lstrip = False
        b = self.block
        super().flush()
        for _ in range(extra):                   # <p><br><br></p> = 빈 줄 2개(마지막 <br>만 자리표시)
            self.paras.append(dict({k: b[k] for k in ("level",) if k in b}, tag=b["tag"], align=b.get("align"), list=b.get("list"), indent=b.get("indent", 0), runs=[]))
    def _has_content(self):
        return any(r.get("text") or r.get("img") or r.get("endnote") is not None for r in self.block["runs"])
    def _split(self):
        b = self.block; self._brlead = 0; self.flush()
        self.block = dict({k: b[k] for k in ("level",) if k in b}, tag=b["tag"], align=b.get("align"), list=b.get("list"), indent=b.get("indent", 0), runs=[])
        self.cur = self.block["runs"]; self._lstrip = True
    def _maybe_split(self):
        if self.block is None or self.table is not None: return
        n, pend = self._brlead, self._brpend
        for _ in range(n): self._split()          # 내용 앞 <br> = 빈 줄(뒤에 내용이 올 때만)
        if pend: self._split()
    def handle_starttag(self, tag, attrs):
        if self._pg:
            if tag in ("br", "span", "b", "i", "u", "font"): return   # 나누기 내부 잔여 인라인 무시
            self._pg = 0                                             # 닫히지 않은 나누기 → 블록 시작에서 해제(문서 삼킴 방지)
        if tag in ("div", "p") and "pgbreak" in (dict(attrs).get("class") or "").split() and self.table is None and not self.skip:
            self.flush(); self.paras.append({"tag": "pgbreak", "runs": []}); self._pg = 1; self._pgtag = tag; return
        if tag == "br" and self.table is None and not self.skip and not self._fnskip and self.block is not None:
            if self._brpend: self._split(); self._brpend = True     # 연속 <br> = 빈 줄
            elif self._has_content(): self._brpend = True
            else: self._brlead += 1                                  # 내용 앞 <br>: 뒤에 내용 오면 빈 줄, 아니면 자리표시
            return
        if self.block is not None and not self.skip:
            ad = dict(attrs)
            if (tag == "img" and (ad.get("data-place") or "char") == "char") or (tag == "sup" and "fn" in (ad.get("class") or "").split()):
                self._maybe_split()
        super().handle_starttag(tag, attrs)
    def handle_endtag(self, tag):
        if self._pg:
            if tag == self._pgtag: self._pg -= 1
            return
        super().handle_endtag(tag)
    def handle_data(self, data):
        if self._pg: return
        if self.block is not None and self.table is None and not self.skip and not self._fnskip:
            if self._brpend or self._brlead:
                if not data.strip(): return                          # <br> 뒤 공백만=버림
                self._maybe_split(); self._brpend = False
            if self._lstrip:                                         # 줄 나눔 직후 선행 공백은 브라우저가 접음
                data = data.lstrip(" \t\r\n")
                if not data: return
                self._lstrip = False
        super().handle_data(data)

def _imgsize(data, fmt):    # stdlib로 png/jpeg 픽셀크기(PIL 없음)
    try:
        if fmt=="png" and data[:8]==b"\x89PNG\r\n\x1a\n":
            return int.from_bytes(data[16:20],"big"), int.from_bytes(data[20:24],"big")
        if fmt in("jpeg","jpg"):
            i=2
            while i<len(data)-9:
                if data[i]!=0xFF: i+=1; continue
                m=data[i+1]
                if m in(0xC0,0xC1,0xC2,0xC3,0xC5,0xC6,0xC7,0xC9,0xCA,0xCB):
                    return int.from_bytes(data[i+7:i+9],"big"), int.from_bytes(data[i+5:i+7],"big")
                i+=2+int.from_bytes(data[i+2:i+4],"big")
    except Exception: pass
    return None

def _set_pic_pos(pic, model):
    # 그림 개체 배치 → hp:pic(treatAsChar/textWrap/offset). 기본=글자처럼(treatAsChar=1)
    el=getattr(pic,"element",None)
    if el is None: el=getattr(pic,"_element",None)
    if el is None or not isinstance(getattr(el,"tag",None),str): return
    tag=el.tag; ns=tag[tag.find('{')+1:tag.find('}')] if tag.startswith('{') else ''
    q=lambda n:('{%s}%s'%(ns,n)) if ns else n
    place=(model.get("place") or "char").lower(); HU=75
    pos=el.find(q('pos'))
    if pos is None:   # hp:pic엔 기본적으로 hp:pos가 없음 → 생성(맨 앞 삽입)
        pos=el.makeelement(q('pos'), {"affectLSpacing":"0","flowWithText":"1","allowOverlap":"0",
            "holdAnchorAndSO":"0","vertRelTo":"PARA","horzRelTo":"COLUMN","vertAlign":"TOP",
            "horzAlign":"LEFT","vertOffset":"0","horzOffset":"0"})
        el.insert(0, pos)
    pos.set("treatAsChar","1" if place=="char" else "0")
    if place=="fixed":
        z=int(model.get("z") or 0)   # 통합 z스택(2026-07-19): 음수=글 뒤(BEHIND_TEXT), 그 외=글 앞
        el.set("textWrap","BEHIND_TEXT" if z<0 else "IN_FRONT_OF_TEXT"); pos.set("allowOverlap","1")
        try: el.set("zOrder", str(max(0, z+100)))   # 개체간 상대순서 힌트(한글이 존중)
        except Exception: pass
        pos.set("vertRelTo","PAPER"); pos.set("horzRelTo","PAPER")
        off=el.find(q('offset'))
        if off is not None:
            off.set("x", str(int(round((model.get("left") or 0)*HU))))
            off.set("y", str(int(round((model.get("top") or 0)*HU))))
    elif place=="float":
        el.set("textWrap","SQUARE")

def _add_image(doc, model):
    src=model.get("src") if isinstance(model, dict) else model
    m=re.match(r"data:image/([\w]+);base64,(.*)", src or "", re.S)
    if not m: return
    fmt=m.group(1).lower()
    try: data=base64.b64decode(m.group(2))
    except Exception: return
    f="jpg" if fmt in("jpeg","jpg") else fmt
    kw={"align":"CENTER"}; sz=_imgsize(data, fmt)
    if sz and sz[0]:
        w,h=sz; mmw=min(150.0, w*25.4/96.0)
        if isinstance(model, dict) and model.get("width"): mmw=min(400.0, model["width"]*25.4/96.0)
        kw["width_mm"]=round(mmw,1); kw["height_mm"]=round(mmw*h/w,1)
    pic=None
    try: pic=doc.add_picture(data, f, **kw)
    except Exception:
        try: pic=doc.add_picture(data, f)
        except Exception: pic=None
    if pic is not None and isinstance(model, dict):
        try: _set_pic_pos(pic, model)
        except Exception: pass

def _celltext(runs): return "".join(r.get("text","") for r in runs).strip()
def cell_paras(cell):   # 셀 → [(runs, align|None)] — paras(문단 2개 이상, 파서·모델 공용) 또는 구형 단일 runs
    ps=cell.get("paras")
    if ps: return [(p.get("runs") or [], p.get("align")) for p in ps]
    return [(cell.get("runs") or [], None)]

HH_NS="{http://www.hancom.co.kr/hwpml/2011/head}"
_GENERIC_FONTS={"serif","sans-serif","monospace","cursive","fantasy","system-ui","-apple-system","inherit","initial"}
_FALLBACK_FONTS={"notokr","noto kr","noto sans kr","nanumgothic","noto sans","malgun gothic"}  # 우리 폴백 마커(보존 대상 아님)
_DEFAULT_FACES={"함초롬돋움","함초롬바탕"}   # python-hwpx 기본 fontface(이 둘은 import시 생략=기본 유지)
def _fontname(css):
    """CSS font-family 값에서 보존할 실제 폰트명 추출(폴백 NotoKR·generic 제외). 없으면 None."""
    if not css: return None
    for fam in str(css).split(","):
        fam=fam.strip().strip('"').strip("'").strip()
        if not fam: continue
        if fam.lower() in _GENERIC_FONTS or fam.lower() in _FALLBACK_FONTS: continue
        return fam[:60]
    return None
def _register_font(doc, name):
    """헤더 fontfaces에 폰트 등록(이미 있으면 그 id). add_run(font=name) 참조용 fontRef id 반환."""
    try:
        from lxml import etree
        hdr=doc.headers[0]; root=getattr(hdr,"element",hdr)
        faces=root.find(".//%sfontfaces"%HH_NS)
        if faces is None: return None
        fcs=faces.findall("%sfontface"%HH_NS)
        for fc in fcs:
            for f in fc.findall("%sfont"%HH_NS):
                if f.get("face")==name: return int(f.get("id"))
        newid=0
        for fc in fcs:
            for f in fc.findall("%sfont"%HH_NS): newid=max(newid,int(f.get("id"))+1)
        for fc in fcs:
            fnt=etree.SubElement(fc,"%sfont"%HH_NS)
            fnt.set("id",str(newid)); fnt.set("face",name); fnt.set("type","TTF"); fnt.set("isEmbedded","0")
            fc.set("fontCnt",str(len(fc.findall("%sfont"%HH_NS))))
        return newid
    except Exception: return None
def _font_map(doc):
    """import: fontRef id(HANGUL)→face 이름. doc에 1회 캐시."""
    m=getattr(doc,"_hwfontmap",None)
    if m is not None: return m
    m={}
    try:
        hdr=doc.headers[0]; root=getattr(hdr,"element",hdr)
        for fc in root.findall(".//%sfontface"%HH_NS):
            if fc.get("lang")=="HANGUL":
                for f in fc.findall("%sfont"%HH_NS): m[f.get("id")]=f.get("face")
                break
    except Exception: pass
    try: doc._hwfontmap=m
    except Exception: pass
    return m

def _runkw(r, hsize=None):   # add_run kwargs(본문·셀 공유)
    kw={}
    if r.get("bold") or hsize: kw["bold"]=True
    if r.get("italic"): kw["italic"]=True
    if r.get("underline"): kw["underline"]=True
    if r.get("strike"): kw["strike"]=True
    if r.get("color"): kw["color"]=r["color"]
    if r.get("highlight"): kw["highlight"]=r["highlight"]
    kw["size"]=r.get("size") or (hsize[0] if hsize else 10)
    return kw
_lkseq=[0]   # #20 하이퍼링크 필드 id 시퀀스(문서 내 유일성만 요구)
def _sub(parent, tag, attrs):   # lxml/stdlib ET 양쪽 호환 서브요소(라이브러리가 lxml element를 쓸 수 있음)
    el=parent.makeelement(tag, attrs); parent.append(el); return el
def _link_field(para, fid, url=None):   # hp:ctrl fieldBegin(url 있으면)/fieldEnd run을 문단 끝에 직접 append — add_run 순서와 동일 스트림이라 인라인 위치 보존
    run=_sub(para.element, _HPNS+"run", {"charPrIDRef":"0"})
    ctrl=_sub(run, _HPNS+"ctrl", {})
    if url is not None:
        _sub(ctrl, _HPNS+"fieldBegin", {"id":fid,"type":"HYPERLINK","name":url,"editable":"false","dirty":"false"})
    else:
        _sub(ctrl, _HPNS+"fieldEnd", {"beginIDRef":fid})
    try: para.section.mark_dirty()
    except Exception: pass
def _add_native_shape(para, sh):
    """도형 → 네이티브 OWPML 개체(2026-07-18): python-hwpx add_rectangle/add_ellipse/add_line 사용
    → 한글에서 진짜 도형(타원·사각형·선)으로 열림. 단위=HWPUNIT(px×75). 성공=True(실패시 호출부가 PNG 폴백)."""
    kind=sh.get("kind")
    try:
        W=max(75,int(round(float(sh.get("w") or 120)*75))); H=max(75,int(round(float(sh.get("h") or 72)*75)))
        stroke=_safecolor(sh.get("stroke")) or "#000000"
        lw=str(max(38,int(round(float(sh.get("sw") or 2)*75))))
        fill=_safecolor(sh.get("fill"))
        if not fill or (sh.get("fill") or "").strip().lower()=="none": fill=None
    except Exception: return False
    if kind=="circle":
        para.add_ellipse(W, H, line_color=stroke, line_width=lw, fill_color=fill, treat_as_char=True); return True
    if kind=="rect":
        para.add_rectangle(W, H, line_color=stroke, line_width=lw, fill_color=fill, treat_as_char=True); return True
    if kind in("line","lineimp"):
        para.add_line(0, H, W, 0, line_color=stroke, line_width=lw, treat_as_char=True); return True
    return False

def _addruns(para, runs, hsize=None, doc=None, notes=None):
    link=None; fid=None   # #20: 연속 동일 link run 묶음을 fieldBegin/End로 감쌈(내부 서식 run은 그대로)
    def _linkto(lk):
        nonlocal link, fid
        if lk==link: return
        try:
            if link is not None: _link_field(para, fid)
            if lk is not None:
                _lkseq[0]+=1; fid="18590%04d"%_lkseq[0]; _link_field(para, fid, lk)
        except Exception: lk=None
        link=lk
    for r in runs:
        if r.get("endnote") is not None and doc is not None:   # #25 미주 마커: run 사이에 앵커 삽입 → 인라인 위치 보존
            try: doc.add_endnote((notes or {}).get(r["endnote"], ""), paragraph=para)
            except Exception: pass
            continue
        if r.get("img") and doc is not None:   # 인라인 글자처럼 그림/도형(2026-07-18): run 순서대로 add_picture → 위치 보존
            ok=False
            if r.get("shape"):                 # 도형=네이티브 OWPML(hp:rect/ellipse/line, 한글 호환) 우선, 실패시 PNG 폴백
                try: ok=_add_native_shape(para, r["shape"])
                except Exception: ok=False
            if not ok:
                try: _add_cell_image(doc, para, r["img"], r.get("width"), capmm=150.0)
                except Exception: pass
            continue
        if r.get("text","")=="": continue
        _linkto(r.get("link") or None)
        kw=_runkw(r, hsize)
        fn=_fontname(r.get("font"))
        if fn and doc is not None:           # 폰트 보존: 헤더에 등록 후 run에 참조
            _register_font(doc, fn); kw["font"]=fn
        try: para.add_run(r["text"], **kw)
        except Exception: para.add_run(r.get("text",""))
    _linkto(None)   # 문단 끝 열린 링크 닫기

def _ensure_align_parapr(doc, align, cache):   # 정렬 paraPr id 민팅(임시문단 경유)+캐시
    if align in cache: return cache[align]
    pid=None
    try:
        tmp=doc.add_paragraph(""); idx=len(doc.paragraphs)-1
        doc.set_paragraph_format(paragraph_index=idx, alignment=align)
        pid=tmp.para_pr_id_ref
        try: doc.remove_paragraph(tmp)
        except Exception: pass
    except Exception: pid=None
    cache[align]=pid; return pid

def _set_table_pos(t, model):
    # 개체 배치 직렬화 → OWPML hp:tbl/hp:pos (글자처럼=treatAsChar, 고정=PAPER+offset, 플로팅=SQUARE wrap)
    el=getattr(t,"element",None)
    if el is None: el=getattr(t,"_element",None)
    if el is None: el=getattr(t,"_tbl",None)
    if el is None or not isinstance(getattr(el,"tag",None),str): return
    tag=el.tag; ns=tag[tag.find('{')+1:tag.find('}')] if tag.startswith('{') else ''
    q=lambda n: ('{%s}%s'%(ns,n)) if ns else n
    pos=el.find(q('pos'))
    if pos is None: return
    place=(model.get("place") or "char").lower(); HU=75    # 96px=7200HWPUNIT → 75HU/px
    if place=="fixed":
        el.set("textWrap","IN_FRONT_OF_TEXT")
        pos.set("treatAsChar","0"); pos.set("horzRelTo","PAPER"); pos.set("vertRelTo","PAPER")
        pos.set("horzAlign","LEFT"); pos.set("vertAlign","TOP"); pos.set("allowOverlap","1")
        pos.set("horzOffset", str(int(round((model.get("left") or 0)*HU))))
        pos.set("vertOffset", str(int(round((model.get("top") or 0)*HU))))
    elif place=="float":
        el.set("textWrap","SQUARE")
        pos.set("treatAsChar","0"); pos.set("horzRelTo","COLUMN"); pos.set("vertRelTo","PARA")
        pos.set("horzAlign","RIGHT" if (model.get("objalign") or "")=="RIGHT" else "LEFT")
        pos.set("vertAlign","TOP"); pos.set("horzOffset","0"); pos.set("vertOffset","0")
    else:                                                   # char(글자처럼)=기본 treatAsChar=1
        pos.set("treatAsChar","1")
        al=(model.get("objalign") or "")
        if al in("CENTER","RIGHT"): pos.set("horzAlign", al)

def _add_cell_image(doc, para, src, wpx=None, capmm=60.0):     # 문단에 글자처럼 그림 삽입(셀 그림·본문 인라인 공용)
    #   para.add_picture는 raw bytes가 아니라 binary id_ref를 받음 → doc.add_image로 먼저 등록
    #   capmm: 셀 안=60mm(셀에 맞춰 축소) / 본문 인라인=150mm(2026-07-18 인라인 run 재사용)
    m=re.match(r"data:image/([\w]+);base64,(.*)", src or "", re.S)
    if not m: return
    fmt=m.group(1).lower()
    try: data=base64.b64decode(m.group(2))
    except Exception: return
    f="jpg" if fmt in("jpeg","jpg") else fmt
    try: ref=doc.add_image(data, f)
    except Exception: return
    W=H=2400; sz=_imgsize(data, fmt)
    if sz and sz[0]:
        w,h=sz
        mmw = (wpx*25.4/96.0) if wpx else (w*25.4/96.0)
        mmw = min(capmm, mmw)
        W=max(300,int(mmw*7200/25.4)); H=max(300,int(W*h/w))
    try: para.add_picture(ref, width=W, height=H, treat_as_char=True)
    except Exception:
        try: para.add_picture(ref)
        except Exception: pass

def _edge_borderfill(doc, base_id, borders, bg):
    # 셀의 변별 선종류/굵기/색(+배경)을 담은 borderFill을 헤더에 민팅해 id 반환. 기본 borderFill(보통 3)을 복제 후 지정 변만 덮어씀.
    # 동일 조합은 doc 단위 캐시로 재사용(파일 비대 방지). 배경까지 여기서 넣으므로 set_cell_shading과 병용 금지.
    cache=getattr(doc,"_wp_bfcache",None)
    if cache is None:
        cache={}
        try: setattr(doc,"_wp_bfcache",cache)
        except Exception: pass
    key=(str(base_id), tuple(sorted((k,(v[0],round(v[1]),v[2])) for k,v in borders.items())), bg)
    if key in cache: return cache[key]
    HH="{http://www.hancom.co.kr/hwpml/2011/head}"; HC="{http://www.hancom.co.kr/hwpml/2011/core}"
    hdr=doc.headers[0]
    bfs=hdr.element.find(".//"+HH+"borderFills")
    if bfs is None: return None
    fills=bfs.findall(HH+"borderFill")
    base=next((b for b in fills if b.get("id")==str(base_id)), None)
    if base is None and fills: base=fills[-1]
    if base is None: return None
    import copy as _copy
    nb=_copy.deepcopy(base)
    nid=str(max([int(b.get("id") or 0) for b in fills]+[0])+1)
    nb.set("id",nid)
    for side,(cssst,wpx,col) in borders.items():
        e=nb.find(HH+side+"Border")
        if e is None: continue
        t=_BTYPE.get(cssst,"SOLID")
        e.set("type",t)
        if t!="NONE": e.set("width",_mmw(wpx))
        if col: e.set("color",col)
    if bg:
        for fb in nb.findall(HC+"fillBrush"): nb.remove(fb)
        fb=nb.makeelement(HC+"fillBrush",{})
        fb.append(nb.makeelement(HC+"winBrush",{"faceColor":bg,"hatchColor":"#FF000000","alpha":"0"}))
        nb.append(fb)
    bfs.append(nb)
    bfs.set("itemCnt",str(len(bfs.findall(HH+"borderFill"))))
    try: hdr.mark_dirty()
    except Exception: pass
    cache[key]=nid
    return nid

def _add_table(doc, tmodel, acache):
    rows=tmodel.get("rows") or []
    if not rows: return
    occ={}; positioned=[]; maxc=0; R=len(rows)   # colspan/rowspan 반영 논리격자 위치
    for r,row in enumerate(rows):
        c=0
        for cell in row:
            while (r,c) in occ: c+=1
            if c>=256: break                                  # 열 상한(병합 증폭 DoS 차단, 2026-11-13)
            cs=max(1,min(64,256-c,int(cell.get("colspan",1)))); rs=max(1,min(R-r,int(cell.get("rowspan",1))))
            cell["colspan"]=cs; cell["rowspan"]=rs           # 아래 병합 단계도 같은 값 사용
            if len(occ)+cs*rs>10000: raise HwpxInputError("표가 너무 큽니다(셀 수 상한)")
            for dr in range(rs):
                for dc in range(cs): occ[(r+dr,c+dc)]=True
            positioned.append((r,c,cell)); maxc=max(maxc,c+cs); c+=cs
    C=maxc
    if R<1 or C<1: return
    try: t=doc.add_table(rows=R, cols=C)
    except Exception: return
    try: _set_table_pos(t, tmodel)
    except Exception: pass
    for (r,c,cell) in positioned:    # 병합 먼저
        if int(cell.get("colspan",1))>1 or int(cell.get("rowspan",1))>1:
            try: t.merge_cells(r,c,r+int(cell["rowspan"])-1,c+int(cell["colspan"])-1)
            except Exception: pass
    try:   # 성능(2026-11-13): python-hwpx t.cell()은 호출마다 전체 격자 재구성(O(n²) — 800셀 11s). 병합 후 구조 고정 → 1회 캐시
        _grid=t._build_cell_grid(); t._build_cell_grid=lambda: _grid
    except Exception: pass
    for (r,c,cell) in positioned:    # 셀 내용(run 단위 서식) + 음영(배경) + 정렬
        runs=[rr for rr in cell["runs"] if rr.get("text","")!=""]
        try:
            cobj=t.cell(r,c); cps=cobj.paragraphs
            para=cps[0] if cps else cobj.add_paragraph("")
            try: para.clear_text()
            except Exception: pass
            cpl=cell_paras(cell)
            if len(cpl)>1:    # 셀 여러 문단(2026-11-13): 문단마다 add_paragraph + 문단 정렬
                for pi,(pr_,pal) in enumerate(cpl):
                    if pi: para=cobj.add_paragraph("")
                    pr_=[rr for rr in pr_ if rr.get("text","")!=""]
                    if pr_: _addruns(para, pr_, None, doc)
                    pa=(pal or cell.get("align") or "").upper()
                    if pa in("CENTER","RIGHT","JUSTIFY","LEFT"):
                        pid=_ensure_align_parapr(doc, pa, acache)
                        if pid is not None: para.para_pr_id_ref=pid
                runs=[]
            if runs: _addruns(para, runs, None, doc)
            for iitem in cell.get("images",[]):     # 표 안 그림(src,width)
                isrc, iwpx = iitem if isinstance(iitem, tuple) else (iitem, None)
                try: _add_cell_image(doc, para, isrc, iwpx)
                except Exception: pass
        except Exception:
            try: t.set_cell_text(r,c,_celltext(cell["runs"]),logical=True)
            except Exception: pass
        bd=cell.get("borders") or {}
        if bd:      # 변별 테두리(+배경 동반 시 함께) → 전용 borderFill. set_cell_shading은 faceColor만 대조해 재사용하므로 병용 시 변 정보 소실
            try:
                cobj=t.cell(r,c)
                base=cobj.element.get("borderFillIDRef") or "3"
                nid=_edge_borderfill(doc, base, bd, cell.get("bg"))
                if nid: cobj.element.set("borderFillIDRef", nid)
            except Exception: pass
        elif cell.get("bg"):
            try: t.set_cell_shading(r,c,cell["bg"])
            except Exception: pass
        al=(cell.get("align") or "").upper()
        if al in("CENTER","RIGHT","JUSTIFY") and not cell.get("paras"):   # 여러 문단은 위에서 문단별 지정
            pid=_ensure_align_parapr(doc, al, acache)
            if pid is not None:
                try:
                    cps=t.cell(r,c).paragraphs
                    if cps: cps[0].para_pr_id_ref=pid
                except Exception: pass
    # ── 열 너비/행 높이 보존(HTML px → HWPUNIT, 1px=75). 명시 너비가 있을 때만 적용(없으면 기본 균등 유지) ──
    try:
        HU=75; DEF=96; rowh=tmodel.get("rowh") or []
        colw=[0]*C
        for (r,c,cell) in positioned:                           # 1차: 단일열 셀=권위(명시 너비 우선)
            w=int(cell.get("width") or 0)
            if w>0 and max(1,int(cell.get("colspan",1)))==1 and colw[c]==0: colw[c]=w
        for (r,c,cell) in positioned:                           # 2차: 스팬셀=아직 빈 열만 균등 분배
            w=int(cell.get("width") or 0); cs=max(1,int(cell.get("colspan",1)))
            if w>0 and cs>1:
                per=max(1,w//cs)
                for dc in range(cs):
                    if c+dc<C and colw[c+dc]==0: colw[c+dc]=per
        if any(colw):
            colw=[w if w>0 else DEF for w in colw]
            for (r,c,cell) in positioned:
                cs=max(1,int(cell.get("colspan",1))); rs=max(1,int(cell.get("rowspan",1)))
                wpx=sum(colw[c:c+cs])
                hpx=sum((rowh[r+dr] if r+dr<len(rowh) and rowh[r+dr]>0 else 0) for dr in range(rs))
                kw={"width":wpx*HU}
                if hpx>0: kw["height"]=hpx*HU
                try: t.cell(r,c).set_size(**kw)
                except Exception: pass
            try:                                                # 표 총 너비/높이(hp:sz는 자동합산 안 됨)
                sz=t.element.find('.//{*}sz')
                if sz is not None:
                    sz.set("width", str(sum(colw)*HU))
                    th=sum(h for h in rowh if h>0)
                    if th>0: sz.set("height", str(th*HU))
            except Exception: pass
    except Exception: pass

def build(html, notes=None):
    p=_PP(); p.feed(html); p.flush()
    paras=p.paras
    doc=HwpxDocument.new()
    base_used=False; acache={}; pgpend=False
    for pp in paras:
        if pp.get("tag")=="pgbreak":            # 쪽 나누기 → 다음 블록 첫 문단 pageBreak="1"(연속이면 빈 쪽 문단 추가)
            if pgpend:
                try: doc.add_paragraph("", inherit_style=False).element.set("pageBreak","1")
                except Exception: pass
            pgpend=True; continue
        n0=len(doc.paragraphs); reused=False
        if pp.get("tag")=="table":
            _add_table(doc, pp, acache)
        elif pp.get("tag")=="img":
            _add_image(doc, pp)
        else:
            if not base_used and len(doc.paragraphs)==1 and not (doc.paragraphs[0].text or "").strip():
                para=doc.paragraphs[0]; base_used=True; reused=True   # 기본 빈 첫 문단 재사용(맨 앞 빈 줄 방지)
                try: para.clear_text()
                except Exception: pass
            else:
                para=doc.add_paragraph("", inherit_style=False)  # 직전 제목 paraPr(outline) 상속 방지
            runs=[r for r in pp["runs"] if r.get("text","")!="" or r.get("endnote") is not None or r.get("img")]
            hsize=HEAD.get(pp["tag"])
            _addruns(para, runs, hsize, doc, notes)
            if not runs: para.add_run("")
            idx=len(doc.paragraphs)-1
            fmt={}
            al=pp.get("align")
            if al in("LEFT","CENTER","RIGHT","JUSTIFY"): fmt["alignment"]=al
            if hsize: fmt["outline_level"]=hsize[1]
            if pp.get("indent"): fmt["indent_left_mm"]=round(pp["indent"]*25.4/96, 1)   # 들여쓰기(px→mm, 한글 출력용)
            if pp.get("list"):    # 글머리표/번호목록 → 한글 목록 속성(paraPr heading BULLET/NUMBER)
                try: doc.set_list_format(paragraph_index=idx, kind=pp["list"])
                except Exception: pass
            elif fmt:
                try: doc.set_paragraph_format(paragraph_index=idx, **fmt)
                except Exception: pass
        if pgpend and not reused and len(doc.paragraphs)>n0:
            try: doc.paragraphs[n0].element.set("pageBreak","1")
            except Exception: pass
            pgpend=False
        elif pgpend and reused:
            pgpend=False                          # 문서 맨 앞 나누기=무의미
    if pgpend:                                    # 문서 끝 나누기 → 빈 쪽 문단
        try: doc.add_paragraph("", inherit_style=False).element.set("pageBreak","1")
        except Exception: pass
    return doc

def _tag_end(s, i):
    """s[i]=='<' 인 태그의 닫는 '>' 위치(따옴표 인지, 선형). 없으면 -1."""
    q = None; n = len(s); j = i + 1
    while j < n:
        c = s[j]
        if q:
            k = s.find(q, j)
            if k < 0: return -1
            j = k + 1; q = None; continue
        if c == '"' or c == "'": q = c
        elif c == ">": return j
        j += 1
    return -1

def _find_block(html, opener, closer="</div>"):
    """opener('<div id="x"' 형태, 공백 유연)로 시작하고 닫힌 첫 블록 (시작, 여는태그끝, 블록끝, 닫는태그시작) — 선형.
    닫는 태그가 없으면 None(본문 유실 방지)."""
    tag, attr = opener.split(None, 1)
    for mo in re.finditer(re.escape(tag) + r"\s+" + re.escape(attr), html):
        i = mo.start()
        e = _tag_end(html, i)
        if e < 0: return None
        k = html.find(closer, e + 1)
        if k < 0: return None
        return i, e, k + len(closer), k
    return None

def _strip_tags(s):   # '<[^<>]*>' — 다음 '<'에서 멈춰 선형
    return re.sub(r"<[^<>]*>", "", s)

def _extract_fnotes(html):   # #25 미주: 문서 끝 <div class="fnotes"> 주석 섹션 → {id: 내용텍스트} + 본문에서 제거(선형 스캔, 2026-11-13)
    b=_find_block(html, '<div class="fnotes"')
    if not b: return {}, html
    i, e, end, k = b
    inner=html[e+1:k]; notes={}
    pos=0
    while True:
        p=inner.find("<p", pos)
        if p<0: break
        if p+2<len(inner) and inner[p+2] not in " >\t\n\r/": pos=p+2; continue
        te=_tag_end(inner, p)
        if te<0: break
        ce=te
        while True:                                               # '</p' 다음이 공백/'>'인 것만(</pre 등 제외)
            ce=inner.find("</p", ce+1)
            if ce<0 or ce+3>=len(inner) or inner[ce+3] in " >\t\n\r": break
        body=inner[te+1:(ce if ce>=0 else len(inner))]
        attrs=inner[p+2:te]
        pos=(ce+3) if ce>=0 else len(inner)
        cls=re.search(r'\bclass="([^"]*)"', attrs); fid=re.search(r'\bdata-fn="([^"]+)"', attrs)
        if not (cls and fid and "fn-e" in cls.group(1).split()): continue
        parts=[]; q=0                                               # 번호 프리픽스(fn-no span) 전부 제거(한글이 자동 번호)
        while True:
            s0=body.find('<span class="fn-no"', q)
            s1=body.find("</span>", s0) if s0>=0 else -1
            if s1<0: break
            parts.append(body[q:s0]); q=s1+7
        body="".join(parts)+body[q:]
        notes[htmlmod.unescape(fid.group(1))]=htmlmod.unescape(_strip_tags(body)).replace("\xa0"," ").strip()
    return notes, html[:i]+html[end:]

def _extract_meta(html):   # 숨김 <div id="wpmeta" data-header/footer/pagenum> 추출+제거(선형 스캔)
    b=_find_block(html, '<div id="wpmeta"')
    if not b: return {}, html
    i, e, end, k = b
    attrs=html[i:e]
    def ga(k):
        mm=re.search(r'data-%s="([^"]*)"'%k, attrs)
        return htmlmod.unescape(mm.group(1)) if mm else ""
    meta={"header":ga("header"),"footer":ga("footer"),"pagenum":ga("pagenum"),
          "pagenum_format":ga("pagenum-format"),"pagenum_start":ga("pagenum-start"),
          "header_odd":ga("header-odd"),"header_even":ga("header-even"),
          "paper":ga("paper"),"orient":ga("orient"),"margins":ga("margins")}
    return meta, html[:i]+html[end:]

# ── 용지 메타(wpmeta data-paper/orient/margins) — 문서와 함께 왕복(2026-11-13). 에디터 PAPERS(wp-format.js)와 동기 ──
PAPERS_MM={"A3":(297,420),"A4":(210,297),"A5":(148,210),"A6":(105,148),"B4":(257,364),"B5":(182,257),"B6":(128,182),
           "Letter":(215.9,279.4),"Legal":(215.9,355.6),"Tabloid":(279.4,431.8)}
_MARGINS_RE=re.compile(r"\d{1,3}(?:\.\d)?(?:,\d{1,3}(?:\.\d)?){3}")

def _parse_margins(v):   # "위,오른쪽,아래,왼쪽"(mm, CSS 순서·소수 1자리) → (mt,mr,mb,ml) 또는 None. 각 0~100
    if not isinstance(v,str) or not _MARGINS_RE.fullmatch(v): return None
    m=tuple(float(x) for x in v.split(","))
    return m if all(0<=x<=100 for x in m) else None

def _meta_page(meta):
    """wpmeta 용지 속성 → page dict(hwpx_std/docx page_setup 입력, mm) 또는 None(속성 없음). 허용 밖 값은 무시(기본값)."""
    meta=meta or {}
    paper=meta.get("paper") or ""; orient=meta.get("orient") or ""; mg=_parse_margins(meta.get("margins"))
    if paper not in PAPERS_MM and orient not in ("portrait","landscape") and mg is None: return None
    pg={"paper":paper if paper in PAPERS_MM else "A4","orient":"landscape" if orient=="landscape" else "portrait"}
    if mg: pg.update(mt=mg[0],mr=mg[1],mb=mg[2],ml=mg[3])
    return pg

def _page_meta_attrs(w, h, mt, mr, mb, ml):
    """가져온 용지 실치수(mm) → wpmeta 속성 dict. 이름 있는 용지(±1.5mm, 방향 무관)만 data-paper, A4 세로·여백 20 기본값은 생략."""
    try:
        w=float(w); h=float(h); mg=[round(max(0.0,min(100.0,float(x))),1) for x in (mt,mr,mb,ml)]
    except (TypeError,ValueError): return {}
    a={}
    if w>0 and h>0:
        lo,hi=min(w,h),max(w,h)
        name=next((k for k,(pw,ph) in PAPERS_MM.items() if abs(pw-lo)<=1.5 and abs(ph-hi)<=1.5), None)
        if name:
            if name!="A4": a["paper"]=name
            if w>h: a["orient"]="landscape"
    if any(abs(x-20)>0.05 for x in mg): a["margins"]=",".join("%g"%x for x in mg)
    return a

def _apply_meta(doc, meta):
    # #21 홀짝 머리말 — odd/even 지정 시 공용 header 대신 페이지타입별 설정
    odd, even = meta.get("header_odd"), meta.get("header_even")
    if odd or even:
        if odd:
            try: doc.set_header_text(odd, page_type="ODD")
            except Exception: pass
        if even:
            try: doc.set_header_text(even, page_type="EVEN")
            except Exception: pass
    elif meta.get("header"):
        try: doc.set_header_text(meta["header"])
        except Exception: pass
    pn=meta.get("pagenum")
    POS={"center":"BOTTOM_CENTER","right":"BOTTOM_RIGHT","left":"BOTTOM_LEFT"}
    FMT={"1":"DIGIT","circ":"CIRCLED_DIGIT","roman":"ROMAN_SMALL","hangul":"HANGUL_SYLLABLE"}   # hp:pageNum formatType
    if pn in POS:
        try: doc.set_page_number(target="footer", position=POS[pn],
                                 format_type=FMT.get(meta.get("pagenum_format") or "1","DIGIT"))
        except Exception: pass
    elif meta.get("footer"):
        try: doc.set_footer_text(meta["footer"])
        except Exception: pass
    st=meta.get("pagenum_start") or ""
    if st.isdigit() and int(st)>=2:   # 시작번호 → 문서 beginNum(hh:beginNum page)
        try: doc.headers[0].set_begin_numbering(page=int(st))
        except Exception: pass

HWPX_ENGINE=os.environ.get("WP_HWPX_ENGINE","std")   # std(표준 라이브러리, 기본) | pyhwpx(python-hwpx 강제)

def html_to_hwpx(html, page=None):   # 호출형(서버 in-process): HTML→.hwpx 바이트. 기본=stdlib(hwpx_std), python-hwpx는 선택적 폴백
    if HWPX_ENGINE!="pyhwpx" or HwpxDocument is None:
        import hwpx_std
        try:
            meta, body=_extract_meta(html)
            notes, body=_extract_fnotes(body)
            p=_PP(); p.feed(body); p.close(); p.flush()
            return hwpx_std.write(p.paras, meta, notes, page or _meta_page(meta) or {"paper":"A4"})   # 명시 용지(클라 paperInfo) 우선 → 문서 메타 → 편집기 기본(A4·20mm)
        except hwpx_std.HwpxStdError as e:
            raise HwpxInputError(str(e))
        except HwpxInputError: raise
        except (ValueError, OverflowError, RecursionError) as e:   # 입력 HTML 문제(비정상 숫자 등) — 폴백 없이 거부
            raise HwpxInputError("변환할 수 없는 문서(%s)"%type(e).__name__)
        except Exception as e:
            if HwpxDocument is None: raise HwpxInputError("hwpx 생성 실패(%s)"%type(e).__name__)
            sys.stderr.write("[hwpx_std] write 실패(%s) → python-hwpx 폴백\n"%type(e).__name__)
    try:
        return _html_to_hwpx_pyhwpx(html)
    except HwpxInputError: raise
    except Exception as e:
        raise HwpxInputError("hwpx 생성 실패(%s)"%type(e).__name__)   # 원격 폴백까지 번지지 않게

def _html_to_hwpx_pyhwpx(html):
    meta, html=_extract_meta(html)
    notes, html=_extract_fnotes(html)   # #25 미주: 섹션은 본문 출력에서 제외, 내용은 hp:endNote로
    doc=build(html, notes); _apply_meta(doc, meta)
    buf=io.BytesIO()
    try: doc.save_to_stream(buf, validate=False)
    except TypeError: doc.save_to_stream(buf)
    return buf.getvalue()

def export():
    sys.stdout.buffer.write(html_to_hwpx(sys.stdin.buffer.read().decode("utf-8","replace")))

def esc(s): return htmlmod.escape(s or "")
def _safecolor(c):   # import한 .hwpx의 색상값을 style에 넣기 전 검증(속성 인젝션/XSS 차단)
    c=(c or "").strip()
    if re.match(r"^#[0-9A-Fa-f]{3,8}$", c) or re.match(r"^rgb\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*\)$", c) or re.match(r"^[A-Za-z]{1,20}$", c):
        return c
    return None

def _runs_to_html(doc, runs, heading=False, fncol=None):   # run 목록→inner HTML(색/크기/음영/굵게/기울임/밑줄/취소선/하이퍼링크). 본문·셀 공유
    inner=[]; lkopen=False
    for run in runs:
        # #20 하이퍼링크 필드: fieldBegin(HYPERLINK)→<a> 열기(스킴 검증), fieldEnd→닫기. run 경계 걸침 허용(문자열 연결)
        try:
            el=getattr(run,"element",None)
            for fe in (el.iter() if el is not None else ()):
                if not isinstance(fe.tag,str): continue
                if fe.tag.endswith("}fieldBegin") and fe.get("type")=="HYPERLINK":
                    u=_field_link_url(fe)
                    if u and not lkopen:
                        inner.append('<a href="%s" rel="noopener noreferrer" title="%s">'%(esc(u),esc(u))); lkopen=True
                elif fe.tag.endswith("}fieldEnd") and lkopen:
                    inner.append("</a>"); lkopen=False
        except Exception: pass
        if fncol is not None:   # #25 미주 앵커(hp:endNote) → 마커 <sup.fn> + 내용 수집(셀 경유 호출은 fncol=None=무시)
            try:
                el=getattr(run,"element",None)
                for en in (el.iter() if el is not None else ()):
                    if isinstance(en.tag,str) and en.tag.endswith('}endNote'):
                        fncol["n"]+=1
                        txt="".join(t2.text or "" for t2 in en.iter() if isinstance(t2.tag,str) and t2.tag.endswith('}t'))
                        fncol["notes"].append(txt)
                        inner.append('<sup class="fn" contenteditable="false" data-fn="%d">%d)</sup>'%(fncol["n"],fncol["n"]))
            except Exception: pass
        t=esc(run.text or "")
        if not t: continue
        style=""
        try:
            rs=doc.char_property(run.char_pr_id_ref)
            if rs is not None:
                attrs=getattr(rs,"attributes",None) or {}
                col=_safecolor(attrs.get("textColor"))
                if col and col.upper() not in("#000000","NONE"): style+="color:%s;"%col
                h=attrs.get("height")
                if h and h!="1000" and not heading:
                    try: style+="font-size:%dpt;"%round(int(h)/100)
                    except Exception: pass
                sh=_safecolor(attrs.get("shadeColor"))
                if sh and sh.upper() not in("NONE","#FFFFFF"): style+="background-color:%s;"%sh
                ca=getattr(rs,"child_attributes",None) or {}
                if (ca.get("strikeout") or {}).get("shape","NONE")!="NONE": t="<s>%s</s>"%t
                if "supscript" in ca: t="<sup>%s</sup>"%t          # 위/아래 첨자(D12)
                elif "subscript" in ca: t="<sub>%s</sub>"%t
                fid=(ca.get("fontRef") or {}).get("hangul")      # 폰트 보존: fontRef→face명(+NotoKR 폴백)
                fname=_font_map(doc).get(fid) if fid is not None else None
                if fname and fname not in _DEFAULT_FACES:   # 작은따옴표=style 속성(큰따옴표) 안에서 안전
                    style+="font-family:'%s','NotoKR',sans-serif;"%fname.replace("'","")
            if run.bold and not heading: t="<b>%s</b>"%t
            if run.italic: t="<i>%s</i>"%t
            if run.underline: t="<u>%s</u>"%t
        except Exception: pass
        if style: t='<span style="%s">%s</span>'%(style,t)
        inner.append(t)
    if lkopen: inner.append("</a>")   # 손상 파일 방어: 문단 끝 미닫힘 링크 봉합
    return "".join(inner)

def _read_table_pos(t):     # OWPML hp:tbl/hp:pos → 개체 배치(char/fixed/float + offset/align)
    res={"place":"char","left":0,"top":0,"align":None}
    try:
        el=t.element; tag=el.tag
        ns=tag[tag.find('{')+1:tag.find('}')] if isinstance(tag,str) and tag.startswith('{') else ''
        q=lambda n:('{%s}%s'%(ns,n)) if ns else n
        pos=el.find(q('pos')); tw=el.get("textWrap")
        if pos is not None:
            if pos.get("treatAsChar")=="0":
                if tw=="IN_FRONT_OF_TEXT" or pos.get("horzRelTo")=="PAPER":
                    res["place"]="fixed"
                    res["left"]=int(round(int(pos.get("horzOffset") or 0)/75.0))
                    res["top"]=int(round(int(pos.get("vertOffset") or 0)/75.0))
                else:
                    res["place"]="float"; res["align"]=pos.get("horzAlign")
            else:
                res["align"]=pos.get("horzAlign")
    except Exception: pass
    return res

def _img_place(px):     # hp:pic XML → 그림 배치(char/fixed/float + offset + width)
    res={"place":"char","left":0,"top":0,"width":0}
    if not px: return res
    twm=re.search(r'textWrap="(\w+)"', px); tw=twm.group(1) if twm else ""
    posm=re.search(r'<hp:pos\b[^>]*>', px); pos=posm.group(0) if posm else ""
    tacm=re.search(r'treatAsChar="(\d)"', pos); tac=tacm.group(1) if tacm else None
    if tac=="0":
        if tw in("IN_FRONT_OF_TEXT","BEHIND_TEXT"):
            res["place"]="fixed"
            if tw=="BEHIND_TEXT": res["z"]=-1   # 글 뒤(배경) — 통합 z스택 역매핑
            off=re.search(r'<hp:offset\s+x="(-?\d+)"\s+y="(-?\d+)"', px)
            if off: res["left"]=int(round(int(off.group(1))/75.0)); res["top"]=int(round(int(off.group(2))/75.0))
        else: res["place"]="float"
    cs=re.search(r'<hp:curSz\s+width="(\d+)"', px)   # 그림 크기(curSz HWPUNIT)→px
    if cs:
        w=int(round(int(cs.group(1))/75.0))
        if w>=8: res["width"]=w
    return res    # tac None/"1" → 글자처럼(기본)

def _ns_of(el):
    t=getattr(el,"tag",""); return t[t.find('{')+1:t.find('}')] if isinstance(t,str) and t.startswith('{') else ''
_SHAPE_TAGS=("rect","ellipse","line")   # 한글 네이티브 도형(hp:) — 우리 도형으로 역매핑(2026-07-18)
def _direct_shapes(el):   # el 직속 도형 요소(중첩 hp:tbl 안 제외) — _direct_pics와 동일 패턴
    if el is None: return []
    ns=_ns_of(el); tbltag=('{%s}tbl'%ns) if ns else 'tbl'
    res=[]
    for t in _SHAPE_TAGS:
        tg=('{%s}%s'%(ns,t)) if ns else t
        for sh in el.iter(tg):
            anc=sh.getparent(); intbl=False
            while anc is not None and anc is not el:
                if anc.tag==tbltag: intbl=True; break
                anc=anc.getparent()
            if not intbl: res.append(sh)
    return res

def _shape_el_to_html(sh):   # hp:rect/ellipse/line → 편집가능 .wp-obj SVG 도형(색·선굵기 역매핑, HWPUNIT/75=px)
    ns=_ns_of(sh); q=(lambda t: ('{%s}%s'%(ns,t)) if ns else t)
    tag=sh.tag.split('}')[-1]
    sz=sh.find(q("sz"))
    try: W=max(8,int(int(sz.get("width"))/75)); H=max(8,int(int(sz.get("height"))/75))
    except Exception: W,H=120,72
    ln=sh.find(q("lineShape"))
    stroke=_safecolor(ln.get("color") if ln is not None else None) or "#2f6fd6"
    try: sw=max(1,int(round(int(ln.get("width","150"))/75)))
    except Exception: sw=2
    fill="none"
    fb=sh.find(q("fillBrush"))
    if fb is not None:
        for c in fb.iter():
            if c.tag.endswith("winBrush"): fill=_safecolor(c.get("faceColor")) or "none"; break
    if tag=="ellipse":
        kind="circle"; inner='<svg viewBox="0 0 100 100" preserveAspectRatio="none"><ellipse cx="50" cy="50" rx="48" ry="48" fill="%s" stroke="%s" stroke-width="%d"/></svg>'%(fill,stroke,sw)
    elif tag=="rect":
        kind="rect"; inner='<svg viewBox="0 0 100 60" preserveAspectRatio="none"><rect x="2" y="2" width="96" height="56" fill="%s" stroke="%s" stroke-width="%d"/></svg>'%(fill,stroke,sw)
    else:
        kind="lineimp"; inner='<svg viewBox="0 0 100 60" preserveAspectRatio="none"><line x1="2" y1="58" x2="98" y2="2" stroke="%s" stroke-width="%d" vector-effect="non-scaling-stroke" stroke-linecap="round"/></svg>'%(stroke,sw)
    return '<span class="wp-obj" data-obj="shape" data-shape="%s" contenteditable="false" style="width:%dpx;height:%dpx">%s</span>'%(kind,W,H,inner)

def _direct_pics(el):     # el 직속 hp:pic(중첩 hp:tbl 안의 그림은 제외) — 본문/셀 그림 분리
    if el is None: return []
    ns=_ns_of(el); pictag=('{%s}pic'%ns) if ns else 'pic'; tbltag=('{%s}tbl'%ns) if ns else 'tbl'
    res=[]
    for pic in el.iter(pictag):
        anc=pic.getparent(); intbl=False
        while anc is not None and anc is not el:
            if anc.tag==tbltag: intbl=True; break
            anc=anc.getparent()
        if not intbl: res.append(pic)
    return res
def _img_place_el(pic):
    try:
        from lxml import etree; return _img_place(etree.tostring(pic, encoding="unicode"))
    except Exception: return {"place":"char","left":0,"top":0}

_PPR_CACHE = {}
def _ppr(doc, ref):
    """paragraph_property 메모이즈(2026-07-18): 라이브러리가 호출마다 헤더 XML 전체를
    재직렬화·재파싱(호출당 ~0.2s)해 13KB 문서 열기 11~18s. 벌크 doc.paragraph_properties()
    1회(0.2s)로 전체 dict 선채움 → 이후 전부 캐시 적중(폴백=ref별 실호출)."""
    key = (id(doc), ref)
    v = _PPR_CACHE.get(key)
    if v is None:
        bk = (id(doc), "__bulk__")
        if bk not in _PPR_CACHE:
            _PPR_CACHE[bk] = 1
            try:
                allp = getattr(doc, "paragraph_properties", None)   # ⚠️dict 속성(호출 아님) — 구현따라 메서드일 수도
                if callable(allp): allp = allp()
                if isinstance(allp, dict):
                    for k2, p in allp.items():
                        _PPR_CACHE[(id(doc), k2)] = p
                        try: _PPR_CACHE[(id(doc), int(k2))] = p   # ref가 int/str 어느 쪽이든 적중
                        except (TypeError, ValueError): pass
            except Exception: pass
        v = _PPR_CACHE.get(key)
        if v is None:
            v = doc.paragraph_property(ref)
            _PPR_CACHE[key] = v
    return v

def _table_to_html(t, doc=None, bfmap=None, imgs=None, pcur=None):   # OWPML 표 → <table>(병합=address가 master일 때만 셀 발행)
    try: R, C = t.row_count, t.column_count
    except Exception: return ""
    bfmap=bfmap or {}
    P=_read_table_pos(t)
    topen="<table"
    if P["place"]=="fixed": topen+=' data-place="fixed" style="left:%dpx;top:%dpx"'%(max(0,P["left"]),max(0,P["top"]))
    elif P["place"]=="float": topen+=' data-place="float"'
    out=[topen+">"]
    for r in range(R):
        rh=0
        try:                                        # 행 높이: 행 첫 셀 cellSz height(HWPUNIT)→px. 기본 3600=auto로 생략
            cz=t.cell(r,0).element.find('.//{*}cellSz')
            if cz is not None:
                hu=int(cz.get("height") or 0)
                if hu>0 and hu!=3600: rh=round(hu/75)
        except Exception: pass
        out.append('<tr style="height:%dpx">'%rh if rh>0 else "<tr>")
        for c in range(C):
            try: cell=t.cell(r,c)
            except Exception: continue
            if getattr(cell,"address",None) != (r, c): continue   # 덮인 셀 skip
            sp=getattr(cell,"span",(1,1)) or (1,1); rs,cs=sp[0],sp[1]
            attrs=(' colspan="%d"'%cs if cs>1 else "")+(' rowspan="%d"'%rs if rs>1 else "")
            style=""
            try:                                    # 열 너비: 셀 width(HWPUNIT)→px. 기본 7200=auto로 생략
                hu=int(getattr(cell,"width",0) or 0)
                if hu>0 and hu!=7200: style+="width:%dpx;"%round(hu/75)
            except Exception: pass
            try:                                        # 배경·테두리: borderFillIDRef→header faceColor+변별 선
                ent=bfmap.get(cell.element.get("borderFillIDRef")) or {}
                if not isinstance(ent,dict): ent={"bg":ent}    # 구형 호출 호환(문자열 bfmap)
                fc=_safecolor(ent.get("bg"))
                if fc and fc.upper() not in("NONE","#FFFFFF"): style+="background:%s;"%fc
                style+=_bf_edge_css(ent)
            except Exception: pass
            try:                                        # 정렬: 셀 문단 paraPr
                if doc is not None and cell.paragraphs:
                    hor=_ppr(doc, cell.paragraphs[0].para_pr_id_ref).align.horizontal
                    if hor in("CENTER","RIGHT"): style+="text-align:%s;"%hor.lower()
            except Exception: pass
            sa=(' style="%s"'%style) if style else ""
            inner=""                                    # 셀 내용=run 단위 서식
            try:
                if doc is not None:
                    cpl=list(cell.paragraphs or [])
                    if len(cpl)>1:    # 셀 여러 문단 → <p> 각각(정렬 다른 문단만 명시)
                        def _pa(p):
                            try: return _ppr(doc, p.para_pr_id_ref).align.horizontal
                            except Exception: return None
                        als=[_pa(p) for p in cpl]; ca=als[0] if als[0] in("CENTER","RIGHT") else None
                        inner="".join("<p%s>%s</p>"%((' style="text-align:%s;"'%(a.lower() if a in("CENTER","RIGHT") else "justify" if a=="JUSTIFY" else "left"))
                                                     if (a if a in("CENTER","RIGHT") else None)!=ca else "", _runs_to_html(doc, p.runs) or "<br>") for p,a in zip(cpl,als))
                    else: inner="".join(_runs_to_html(doc, p.runs) for p in cpl)
            except Exception: inner=""
            if not inner: inner=esc(cell.text or "").strip()
            if imgs is not None and pcur is not None:    # 표 안 그림(셀 직속 hp:pic)
                try:
                    for _pic in _direct_pics(getattr(cell,"element",None)):
                        if pcur[0]>=len(imgs): break
                        d,fmt=imgs[pcur[0]]; pcur[0]+=1
                        Pi=_img_place_el(_pic); ws=("width:%dpx;"%Pi["width"]) if Pi.get("width") else ""
                        inner+='<img src="data:image/%s;base64,%s" style="%smax-width:100%%">'%(fmt, base64.b64encode(d).decode(), ws)  # max-width:100%=셀 폭에 맞춤
                except Exception: pass
            out.append("<td%s%s>%s</td>"%(attrs, sa, inner or "<br>"))
        out.append("</tr>")
    out.append("</table>")
    res="".join(out)
    if P["place"]=="char":     # 글자처럼=objline 래퍼(정렬 보존)
        al=(P["align"] or "").lower()
        st=' style="text-align:%s"'%al if al in("center","right") else ""
        res='<div class="objline"%s>%s</div>'%(st,res)
    return res

# ── HWPX(개방 XML) 읽기 ──
def import_hwpx(data):
    _PPR_CACHE.clear()
    guard_hwpx_zip(data)                      # 하드닝(#18): zip폭탄·XXE 사전 차단(HwpxDocument.open 전)
    fd,tmp=tempfile.mkstemp(suffix=".hwpx")   # 고유 임시(스레드 안전: 서버 동시요청)
    os.close(fd)
    with open(tmp,"wb") as fp: fp.write(data)
    try:
        try: doc=HwpxDocument.open(tmp)
        except AttributeError: doc=HwpxDocument.from_path(tmp)
    except Exception:                          # open 실패 시에도 임시파일 정리(누수 방지)
        try: os.remove(tmp)
        except Exception: pass
        raise
    # 그림: BinData 바이트(문서 순서) + borderFill 맵. 본문/셀 그림은 문단·셀 element의 직속 hp:pic로 분리 소비
    imgs=[]; bfmap={}
    try:
        with zipfile.ZipFile(tmp) as z:        # with=핸들 확실히 닫음
            for im in (doc.list_images() or []):
                try:
                    fmt=(im.get("Format") or "png").lower()
                    if not re.match(r"^(png|jpeg|gif|bmp|webp|avif|tiff?)$",fmt): fmt="png"   # data: URI 인젝션 차단(악성 Format 필드, HEAD 복원)
                    imgs.append((z.read("BinData/"+im.get("BinData")), fmt))
                except Exception: pass
            hdr=z.read("Contents/header.xml").decode("utf-8","replace")   # borderFill id→{배경, 변별 선}(셀 배경·테두리)
            for m in re.finditer(r'<hh:borderFill id="(\d+)".*?</hh:borderFill>', hdr, re.S):
                blk=m.group(0); ent={}
                fc=re.search(r'faceColor="([^"]+)"', blk)
                if fc: ent["bg"]=fc.group(1)
                for side in ("left","right","top","bottom"):
                    eb=re.search(r'<hh:%sBorder\b([^/>]*)/?>'%side, blk)
                    if eb:
                        at=eb.group(1)
                        ty=re.search(r'type="([^"]+)"',at); wd=re.search(r'width="([^"]+)"',at); co=re.search(r'color="([^"]+)"',at)
                        ent[side]=((ty.group(1) if ty else "SOLID"), (wd.group(1) if wd else "0.1 mm"), (co.group(1) if co else "#000000"))
                bfmap[m.group(1)]=ent
    except Exception: pass
    pcur=[0]                                    # 그림 커서(본문+셀 공유, 문서 순서대로 소비)
    out=[]; cur_list=None; fncol={"n":0,"notes":[]}   # #25 미주 수집(문서 순 번호)
    for i,para in enumerate(doc.paragraphs):
        try:   # 쪽 나누기(hp:p pageBreak="1") → 에디터 .pgbreak(첫 문단 제외)
            if out and getattr(para,"element",None) is not None and para.element.get("pageBreak")=="1":
                if cur_list: out.append("</%s>"%cur_list); cur_list=None
                out.append('<div class="pgbreak" contenteditable="false"><br></div>')
        except Exception: pass
        bpics=_direct_pics(getattr(para,"element",None))    # 이 문단 직속 그림(중첩 표 셀 그림 제외)
        bshapes=_direct_shapes(getattr(para,"element",None))  # 네이티브 도형(hp:rect/ellipse/line)
        if (bpics and pcur[0]<len(imgs)) or bshapes:         # 본문 그림/도형 문단
            if cur_list: out.append("</%s>"%cur_list); cur_list=None
            if any((getattr(r,"text","") or "").strip() for r in para.runs):
                # 혼합 문단(2026-07-18): 글자처럼 그림/도형이 텍스트 사이에 → run 순서 인터리브로 인라인 위치 복원
                #   (종전엔 그림 문단으로 취급해 텍스트가 통째 유실됨)
                ns=_ns_of(para.element); pictag=('{%s}pic'%ns) if ns else 'pic'
                shtags=set(('{%s}%s'%(ns,t)) if ns else t for t in _SHAPE_TAGS)
                emap={}
                for r in para.runs:
                    rel=getattr(r,"element",None)
                    if rel is not None: emap[id(rel)]=r
                parts=[]
                for child in para.element:
                    pics=[pc for pc in child.iter(pictag)]
                    shs=[s for s in child.iter() if s.tag in shtags]
                    if pics:
                        for pic in pics:
                            if pcur[0]>=len(imgs): break
                            d,fmt=imgs[pcur[0]]; pcur[0]+=1
                            P=_img_place_el(pic); ws=("width:%dpx;"%P["width"]) if P.get("width") else ""
                            parts.append('<img src="data:image/%s;base64,%s" style="%smax-width:100%%">'%(fmt, base64.b64encode(d).decode(), ws))
                    elif shs:
                        for s in shs: parts.append(_shape_el_to_html(s))
                    else:
                        r=emap.get(id(child))
                        if r is not None: parts.append(_runs_to_html(doc,[r],False,fncol))
                out.append("<p>"+("".join(parts) or "<br>")+"</p>")
                continue
            if bshapes and not (bpics and pcur[0]<len(imgs)):   # 도형 전용 문단
                out.append("<p>"+"".join(_shape_el_to_html(s) for s in bshapes)+"</p>")
                continue
            for pic in bpics:
                if pcur[0]>=len(imgs): break
                d,fmt=imgs[pcur[0]]; pcur[0]+=1; b64=base64.b64encode(d).decode()
                P=_img_place_el(pic); ws=("width:%dpx;"%P["width"]) if P.get("width") else ""
                if P["place"]=="fixed":
                    zs=("z-index:%d;"%P["z"]) if P.get("z") else ""
                    out.append('<img src="data:image/%s;base64,%s" data-place="fixed" style="left:%dpx;top:%dpx;%s%smax-width:100%%">'%(fmt,b64,max(0,P["left"]),max(0,P["top"]),zs,ws))
                elif P["place"]=="float":
                    out.append('<img src="data:image/%s;base64,%s" data-place="float" style="%smax-width:100%%">'%(fmt,b64,ws))
                else:
                    out.append('<p><img src="data:image/%s;base64,%s" style="%smax-width:100%%"></p>'%(fmt,b64,ws))
            continue
        tbls=getattr(para,"tables",None) or []     # 표 복원(셀 안 그림은 _table_to_html이 pcur로 소비)
        if tbls:
            if cur_list: out.append("</%s>"%cur_list); cur_list=None
            for tb in tbls: out.append(_table_to_html(tb, doc, bfmap, imgs, pcur))
            continue
        # 문단 속성: heading.type = OUTLINE(→hN) / BULLET(→ul) / NUMBER(→ol) + 정렬
        tag="p"; pstyle=""; heading=False; listkind=None
        try:
            pr=_ppr(doc, para.para_pr_id_ref)
            hd=getattr(pr,"heading",None); ht=(getattr(hd,"type",None) if hd is not None else None)
            if ht=="OUTLINE":
                lv=int(getattr(hd,"level",0) or 0); tag="h%d"%(min(lv,2)+1); heading=True
            elif ht=="BULLET": listkind="ul"
            elif ht=="NUMBER": listkind="ol"
            al=getattr(pr,"align",None); hor=(getattr(al,"horizontal",None) if al else None)
            if hor in("CENTER","RIGHT"): pstyle=' style="text-align:%s"'%hor.lower()
        except Exception: pass
        inner_html=_runs_to_html(doc, para.runs, heading, fncol) or "<br>"
        if listkind:                                  # 글머리표/번호목록 그룹핑
            if cur_list!=listkind:
                if cur_list: out.append("</%s>"%cur_list)
                out.append("<%s>"%listkind); cur_list=listkind
            out.append("<li>%s</li>"%inner_html)
        else:
            if cur_list: out.append("</%s>"%cur_list); cur_list=None
            out.append("<%s%s>%s</%s>"%(tag,pstyle,inner_html,tag))
    if cur_list: out.append("</%s>"%cur_list)
    if fncol["notes"]:   # #25 미주 → 문서 끝 주석 섹션 복원(마커 data-fn=문서 순 번호와 일치)
        ns="".join('<p class="fn-e" data-fn="%d"><span class="fn-no" contenteditable="false">%d)</span> %s</p>'%(i+1,i+1,esc(t)) for i,t in enumerate(fncol["notes"]))
        out.append('<div class="fnotes"><p class="fn-t" contenteditable="false">주석</p>'+ns+'</div>')
    try: os.remove(tmp)
    except Exception: pass
    html="".join(out)
    return re.sub(r'^<p><br></p>(?=<(?:h[1-3]|table|ul|ol|img|div class="objline"))', '', html)  # 표/제목/개체로 시작 시 맨앞 빈줄 제거

# ── 레거시 HWP5(바이너리) 읽기 — pyhwp hwp5html → CSS 인라인화 ──
HWP_PROPS=("color","font-size","font-weight","font-style","text-decoration","text-align","background-color")
def _parse_css(style_text):
    # 규칙 = (클래스집합, [(prop,decl)...]). 원소의 클래스집합이 규칙 집합을 포함하면 적용(부분매칭).
    rules=[]
    for sel,body in re.findall(r"([^{}]+)\{([^{}]*)\}",style_text,re.S):
        decls=[(d.split(":",1)[0].strip().lower(), d.strip()) for d in body.split(";") if ":" in d]
        for s in sel.split(","):
            s=s.strip()
            if " " in s or ">" in s: continue          # 자손/레이아웃 셀렉터 무시
            cls=set(re.findall(r"\.([\w-]+)",s))
            if cls and decls: rules.append((cls, decls))
    return rules

class Hwp5HtmlClean(HTMLParser):
    # 문단 단위 버퍼링: <p> 안 내용을 모았다가 </p>에서 wrapper(p/hN/li) 결정.
    #   글머리표=class Bullet-N → <ul><li>(레벨중첩) / 번호목록=<span class="autonumbering">(번호 텍스트 제거)→ <ol><li>
    DROP={"style","head","title","meta","link"}; INLINE={"span","b","i","u"}
    def __init__(self,css):
        super().__init__(convert_charrefs=True); self.css=css
        self.out=[]; self.skip=0; self.skip_span=0
        self.buf=None; self.pm=None; self.lists=[]    # buf=현재문단토큰, pm=문단메타, lists=[(ul/ol,level)]
    def _heading(self,c):
        m=re.search(r"(?:Outline|개요)[ _-]*([1-9])",c); return min(int(m.group(1)),3) if m else None
    def _bullet(self,c):   # Bullet-N은 불릿 기호 ID(깊이 아님)→평탄 단일레벨 ul
        return 1 if re.search(r"Bullet[ _-]*[1-9]",c) else None
    def _inh(self): return bool(self.pm and (self.pm.get("tag") or "").startswith("h"))
    def _style(self,c):
        ec=set(c.split()); seen={}
        for cls,decls in self.css:
            if cls <= ec:
                for k,d in decls:
                    if k in HWP_PROPS: seen[k]=d
        if seen.get("color","").replace(" ","").lower().endswith("#000000"): seen.pop("color",None)
        return ";".join(v for v in seen.values() if not re.search(r'["<>]', v))   # 속성 인젝션 차단
    def _emit(self,s): (self.buf if self.buf is not None else self.out).append(s)
    def _close_lists(self,level=0):
        while self.lists and self.lists[-1][1]>level:
            t,_=self.lists.pop(); self.out.append("</%s>"%t)
    def _ensure_list(self,tag,level):
        while self.lists and (self.lists[-1][1]>level or (self.lists[-1][1]==level and self.lists[-1][0]!=tag)):
            t,_=self.lists.pop(); self.out.append("</%s>"%t)
        while not self.lists or self.lists[-1][1]<level:
            nl=(self.lists[-1][1]+1) if self.lists else 1
            self.out.append("<%s>"%tag); self.lists.append((tag,nl))
    def handle_starttag(self,tag,attrs):
        if tag in self.DROP: self.skip+=1; return
        if self.skip: return
        if tag in("html","body","div"): return
        if tag=="img": self._emit("[그림]"); return
        if tag=="p":
            cls=dict(attrs).get("class",""); hv=self._heading(cls)
            self.pm={"cls":cls,"tag":("h%d"%hv if hv else None),"level":self._bullet(cls),"ordered":False}
            self.buf=[]; return
        if tag in("table","tr","td","caption"):
            self._close_lists(0)
            st=self._style(dict(attrs).get("class","")) if tag=="td" else ""
            self.out.append('<%s style="%s">'%(tag,st) if st else "<%s>"%tag); return
        if tag in self.INLINE:
            if tag=="span":
                cls=dict(attrs).get("class","")    # autonumbering(표/그림/쪽 번호)은 캡션→일반 텍스트로 보존
                st=self._style(cls)
                if st and self._inh():
                    st=";".join(d for d in st.split(";") if d.split(":")[0].strip().lower() not in("font-size","font-weight"))
                self._emit('<span style="%s">'%st if st else "<span>")
            else: self._emit("<%s>"%tag)
            return
    def handle_endtag(self,tag):
        if tag in self.DROP: self.skip=max(0,self.skip-1); return
        if self.skip or tag in("html","body","div","img"): return
        if tag=="p":
            inner="".join(self.buf or []); pm=self.pm or {}; self.buf=None; self.pm=None
            if pm.get("tag"):                               # 개요→hN
                self._close_lists(0); self.out.append("<%s>%s</%s>"%(pm["tag"],inner or "<br>",pm["tag"]))
            elif pm.get("level"):                           # 글머리표→ul/li
                self._ensure_list("ul",pm["level"]); self.out.append("<li>%s</li>"%(inner or "<br>"))
            else:
                self._close_lists(0); st=self._style(pm.get("cls",""))
                self.out.append('<p style="%s">%s</p>'%(st,inner or "<br>") if st else "<p>%s</p>"%(inner or "<br>"))
            return
        if tag in("table","tr","td","caption"): self.out.append("</%s>"%tag); return
        if tag in self.INLINE:
            self._emit("</%s>"%tag)
    def handle_data(self,data):
        if self.skip or self.buf is None: return
        d=data.replace("\r","")
        if d: self._emit(htmlmod.escape(d))
    def finish(self):
        self._close_lists(0); return "".join(self.out)

def _clean_hwp5html(html, css_text=""):
    if not css_text:
        ms=re.search(r"<style[^>]*>(.*?)</style>",html,re.S); css_text=ms.group(1) if ms else ""
    css=_parse_css(css_text)
    mb=re.search(r"<body[^>]*>(.*?)</body>",html,re.S)
    body=mb.group(1) if mb else html
    p=Hwp5HtmlClean(css); p.feed(body)
    return p.finish() or "<p><br></p>"

def _hwp5_bin(name):   # pyhwp CLI 위치: WP_HWP5_BIN 디렉터리 > ~/.venvs/hwp5/bin > ~/.local/bin > PATH
    for d in (os.environ.get("WP_HWP5_BIN"), os.path.expanduser("~/.venvs/hwp5/bin"), os.path.expanduser("~/.local/bin")):
        if d and os.path.isfile(os.path.join(d, name)): return os.path.join(d, name)
    return shutil.which(name)

def import_hwp5(data):
    """HWP5(.hwp, OLE) → HTML. pyhwp(hwp5html) 사용, 실패 시 hwp5txt 텍스트 폴백. 둘 다 실패면 HwpxInputError
    (예전엔 '[.hwp 변환 실패: …]' 문단을 성공으로 돌려 내부 경로가 노출되고 사용자는 실패를 몰랐음)."""
    td=tempfile.mkdtemp(); hwp=os.path.join(td,"in.hwp"); od=os.path.join(td,"html")
    try:
        with open(hwp,"wb") as fp: fp.write(data)
        h5=_hwp5_bin("hwp5html")
        try:   # 디렉토리 모드: index.xhtml(본문) + styles.css(charshape 룰)
            if not h5: raise FileNotFoundError("hwp5html 없음")
            subprocess.run([h5,"--output",od,hwp],check=True,capture_output=True,timeout=60)
            with open(os.path.join(od,"index.xhtml"),encoding="utf-8",errors="replace") as fp: body=fp.read()
            cssf=os.path.join(od,"styles.css")
            css_text=""
            if os.path.exists(cssf):
                with open(cssf,encoding="utf-8",errors="replace") as fp: css_text=fp.read()
            return _clean_hwp5html(body, css_text)
        except Exception as e1:
            t5=_hwp5_bin("hwp5txt")
            if t5:
                try:    # 폴백: 텍스트만(문단 단위)
                    r=subprocess.run([t5,hwp],capture_output=True,timeout=60)
                    txt=r.stdout.decode("utf-8","replace")
                    if r.returncode==0 and txt.strip():
                        return "".join("<p>%s</p>"%esc(ln) if ln.strip() else "<p><br></p>" for ln in txt.splitlines()) or "<p><br></p>"
                except Exception: pass
            raise HwpxInputError("hwp 변환 실패(%s)"%type(e1).__name__)
    finally:
        shutil.rmtree(td, ignore_errors=True)   # 임시디렉터리 정리(누수 방지)

def _hwpx_meta_html(data):
    """HWPX 머리말/꼬리말/쪽번호/시작번호 → 에디터 숨김 #wpmeta(왕복 보존, 2026-11-13). 실패 시 빈 문자열.
       정규식 대신 DTD 거부 파서(ET)로 — 닫힘 없는 태그 반복 시 O(n²) 역추적 회피."""
    import xml.etree.ElementTree as ET
    def parse(raw):   # DTD/엔티티 거부(pyexpat 선검사) 후 ET
        if len(raw) > 32*1024*1024: raise ValueError("too big")
        import pyexpat
        pp=pyexpat.ParserCreate()
        def no(*a): raise ValueError("dtd")
        pp.StartDoctypeDeclHandler=no; pp.EntityDeclHandler=no
        pp.Parse(raw, True)
        return ET.fromstring(raw)
    try:
        z=zipfile.ZipFile(io.BytesIO(data)); names=z.namelist()
        secs=sorted(n for n in names if re.match(r"^Contents/section\d+\.xml$", n))
        if not secs: return ""
        sec=parse(z.read(secs[0]))
        hdr=parse(z.read("Contents/header.xml")) if "Contents/header.xml" in names else None
    except Exception:
        return ""
    def ln(e): return e.tag.rsplit("}",1)[-1] if isinstance(e.tag,str) else ""
    def txt(e): return "".join((t.text or "") for t in e.iter() if ln(t)=="t").strip()
    attrs={}; footer=None; pn=None; ppr=None
    for e in sec.iter():
        n=ln(e)
        if n=="pagePr" and ppr is None: ppr=e
        elif n=="header":
            t=txt(e)
            if t: attrs.setdefault({"ODD":"header-odd","EVEN":"header-even"}.get(e.get("applyPageType"),"header"), t)
        elif n=="footer" and footer is None:
            footer=txt(e)
        elif n=="pageNum" and pn is None:
            pn=e
    if ("header-odd" in attrs or "header-even" in attrs) and "header" in attrs:
        attrs.pop("header")
    if pn is not None and (pn.get("pos") or "NONE")!="NONE":
        attrs["pagenum"]={"RIGHT":"right","LEFT":"left"}.get((pn.get("pos") or "").split("_")[-1],"center")
        f={"CIRCLED_DIGIT":"circ","ROMAN_SMALL":"roman","ROMAN_CAPITAL":"roman","HANGUL_SYLLABLE":"hangul"}.get(pn.get("formatType") or "")
        if f: attrs["pagenum-format"]=f
    elif footer:
        attrs["footer"]=footer
    if hdr is not None:
        for e in hdr.iter():
            if ln(e)=="beginNum":
                v=e.get("page") or ""
                if re.fullmatch(r"[0-9]{1,5}",v) and int(v)>=2: attrs["pagenum-start"]=v
                break
    if ppr is not None:   # 용지(secPr/pagePr+margin, HWPUNIT=1/7200in) → data-paper/orient/margins
        try:
            u=lambda v: int(v)*25.4/7200
            w,h=u(ppr.get("width")),u(ppr.get("height"))
            if ppr.get("landscape")=="NARROWLY" and w<h: w,h=h,w   # 한컴: 가로 용지를 세로 치수+NARROWLY로 적기도 함
            mg=next((c for c in ppr if ln(c)=="margin"), None)
            m=[u(mg.get(k) or 0) for k in ("top","right","bottom","left")] if mg is not None else [20]*4
            attrs.update(_page_meta_attrs(w,h,*m))
        except (TypeError,ValueError): pass
    if not attrs: return ""
    return '<div id="wpmeta" hidden=""%s></div>'%"".join(' data-%s="%s"'%(k,esc(v[:2000])) for k,v in attrs.items())

def hwpx_bytes_to_html(data):   # 호출형(서버 in-process): .hwpx/.hwp(/.docx) 바이트→HTML
    if data[:4]==b"PK\x03\x04":                         # ZIP=HWPX(또는 DOCX — 내용으로 판별)
        try:
            names=set(zipfile.ZipFile(io.BytesIO(data)).namelist())
        except Exception:
            raise HwpxInputError("zip 손상")
        if "word/document.xml" in names:
            import docxconv
            return docxconv.docx_to_html(data)
        if HWPX_ENGINE!="pyhwpx" or HwpxDocument is None:
            import hwpx_std
            guard_hwpx_zip(data)
            try:
                return hwpx_std.read(data)+_hwpx_meta_html(data)
            except hwpx_std.HwpxStdError as e:
                raise HwpxInputError(str(e))
            except Exception as e:
                if HwpxDocument is None: raise HwpxInputError("hwpx 해석 실패(%s)"%type(e).__name__)
                sys.stderr.write("[hwpx_std] read 실패(%s) → python-hwpx 폴백\n"%type(e).__name__)
        try:
            return import_hwpx(data)+_hwpx_meta_html(data)
        except HwpxInputError: raise
        except Exception as e:
            raise HwpxInputError("hwpx 해석 실패(%s)"%type(e).__name__)   # 원격 폴백까지 번지지 않게
    if data[:8]==b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":    # OLE/CFB=HWP5 바이너리(루트에 WordDocument면 레거시 Word .doc)
        import docconv
        if docconv.cfb_kind(data)=="doc": return docconv.doc_to_html(data)
        return import_hwp5(data)
    if data[:17]==b"HWP Document File":                  # HWP 3.x(구형) — 미지원 명시
        raise HwpxInputError("HWP 3.x 구형 문서는 지원하지 않습니다(한글에서 hwpx로 저장 후 여세요)")
    raise HwpxInputError("지원하지 않는 형식(hwpx/hwp/docx 아님)")   # 예전: 아무 바이트나 hwp5 폴백→실패문단을 '성공'으로 반환

def import_():
    sys.stdout.write(hwpx_bytes_to_html(sys.stdin.buffer.read()))

if __name__=="__main__":
    mode=sys.argv[1] if len(sys.argv)>1 else "export"
    (export if mode=="export" else import_)()
