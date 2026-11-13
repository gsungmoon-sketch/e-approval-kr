"""양식(서식) 정의·검증 — 순수 함수(DB 없음).

양식 정의(JSON):
  {"name", "category"(일반/보고/재정/인사/대외), "doc_type"(general/expense/leave), "retention"(보존기간),
   "prefix"(문서번호 접두어, 선택), "depts"([] = 전 부서), "cc_depts"(필수 참조 부서 → 부서장),
   "amount"(금액 필드: "필드키" 또는 "표키.열키" 합계), "title_hint", "body_hint",
   "template_html"(편집기 서식 HTML, 선택·512KB, data-field/data-row/data-sum 칸),
   "layout"(출력 서식: "corporate" 결재란 위 / "gov" 공문서 기안문, 비우면 설정 paper_default),
   "fields": [{"key","label","type","required","options","max","columns","sum","workdays","min_files","help"}]}
필드 종류: text textarea number money date daterange select table file
표(table) 열: {"key","label","type"(text/number/money),"calc":"a*b"} · "sum": 합계낼 열 목록.
  calc·sum 은 서식 표시용 표지일 뿐 — 금액·합계 칸도 기안자가 적고 서버는 계산·검증하지 않음(양식 문서 금액=None).
값은 서버가 다시 계산한다(합계·곱·일수) — 클라이언트 값 불신.
"""
from __future__ import annotations

import copy
import datetime as _dt
import json
import re
import unicodedata

from . import policy as pol
from .core_base import Invalid

FIELD_TYPES = {"text": "한 줄 글", "textarea": "여러 줄 글", "number": "숫자", "money": "금액", "date": "날짜",
               "daterange": "기간", "select": "선택", "table": "표(행 반복)", "file": "첨부 필수"}
COL_TYPES = ("text", "number", "money")
CATEGORIES = ("일반", "보고", "재정", "인사", "대외")
LAYOUTS = ("", "corporate", "gov")
RETENTIONS = ("1년", "3년", "5년", "10년", "준영구", "영구")
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,23}$")
CODE_RE = re.compile(r"^[a-z][a-z0-9_-]{1,23}$")
PREFIX_RE = re.compile(r"^[A-Za-z0-9가-힣]{0,8}$")
MAX_FIELDS, MAX_ROWS, MAX_COLS, MAX_OPTS = 30, 50, 8, 30
MAX_NUM = 10 ** 13


def _clean(s, n):
    s = unicodedata.normalize("NFC", str(s if s is not None else ""))
    s = "".join(ch for ch in s if ch in "\n\t" or unicodedata.category(ch)[0] != "C").strip()
    return s[:n]


def _one(s, n=200):
    return _clean(s, n).replace("\n", " ").replace("\t", " ")


# ------------------------------------------------------------------ 정의 검증
def check_def(d):
    """관리자가 넣은 양식 정의를 검증·정규화해 돌려준다. 오류 → Invalid."""
    try:
        return _check_def(d)
    except (TypeError, ValueError, OverflowError, AttributeError, KeyError, StopIteration) as e:
        if isinstance(e, Invalid):
            raise
        raise Invalid(f"양식 정의 형식 오류: {type(e).__name__}")


def _check_def(d):
    if isinstance(d, str):
        try:
            d = json.loads(d)
        except ValueError as e:
            raise Invalid(f"양식 JSON 오류: {e}")
    if not isinstance(d, dict):
        raise Invalid("양식 정의는 객체여야 합니다")
    out = {"name": _one(d.get("name"), 40)}
    if not out["name"]:
        raise Invalid("양식 이름이 필요합니다")
    out["category"] = d.get("category") or "일반"
    if out["category"] not in CATEGORIES:
        raise Invalid(f"분류는 {'/'.join(CATEGORIES)}")
    try:
        out["doc_type"] = pol.type_code(d.get("doc_type") or "general")
    except pol.PolicyError as e:
        raise Invalid(str(e))
    out["retention"] = d.get("retention") or "5년"
    if out["retention"] not in RETENTIONS:
        raise Invalid(f"보존기간은 {'/'.join(RETENTIONS)}")
    out["prefix"] = _one(d.get("prefix"), 8)
    if not PREFIX_RE.match(out["prefix"]):
        raise Invalid("문서번호 접두어는 영문·숫자·한글 8자 이내")
    for k in ("depts", "cc_depts"):
        v = d.get(k) or []
        if not isinstance(v, (str, list)):
            raise Invalid(f"{k}: 부서 코드 목록")
        if isinstance(v, str):
            v = [x for x in re.split(r"[,\s]+", v) if x]
        out[k] = sorted({str(x).strip().upper() for x in v if str(x).strip()})[:30]
    out["layout"] = str(d.get("layout") or "").strip().lower()
    if out["layout"] not in LAYOUTS:
        raise Invalid("출력 서식(layout)은 corporate 또는 gov (비우면 기본 설정)")
    out["title_hint"] = _one(d.get("title_hint"), 100)
    out["body_hint"] = _clean(d.get("body_hint"), 2000)
    fields = d.get("fields") or []
    if not isinstance(fields, list) or len(fields) > MAX_FIELDS:
        raise Invalid(f"필드는 목록(최대 {MAX_FIELDS}개)")
    seen, nf = set(), []
    for f in fields:
        if not isinstance(f, dict):
            raise Invalid("필드 정의 오류")
        key, typ = str(f.get("key") or ""), f.get("type") or "text"
        if not KEY_RE.match(key) or key in seen:
            raise Invalid(f"필드 키 오류/중복: {key!r} (영소문자로 시작, 24자)")
        if typ not in FIELD_TYPES:
            raise Invalid(f"필드 종류 오류: {typ}")
        seen.add(key)
        g = {"key": key, "label": _one(f.get("label") or key, 40), "type": typ, "required": bool(f.get("required"))}
        if f.get("help"):
            g["help"] = _one(f["help"], 120)
        if typ in ("text", "textarea"):
            g["max"] = max(1, min(int(f.get("max") or (200 if typ == "text" else 2000)), 4000))
        if typ == "select":
            opts = [_one(o, 40) for o in (f.get("options") or []) if _one(o, 40)]
            if not opts or len(opts) > MAX_OPTS or len(set(opts)) != len(opts):
                raise Invalid(f"{g['label']}: 선택지 1~{MAX_OPTS}개(중복 없이)")
            g["options"] = opts
        if typ == "daterange":
            g["workdays"] = bool(f.get("workdays"))
            if f.get("half_field"):
                g["half_field"] = str(f["half_field"])
        if typ == "file":
            g["min_files"] = max(1, min(int(f.get("min_files") or 1), 20))
        if typ == "table":
            cols, ck = [], set()
            for col in f.get("columns") or []:
                k2, t2 = str(col.get("key") or ""), col.get("type") or "text"
                if not KEY_RE.match(k2) or k2 in ck or t2 not in COL_TYPES:
                    raise Invalid(f"{g['label']}: 열 정의 오류 {k2!r}")
                ck.add(k2)
                cc = {"key": k2, "label": _one(col.get("label") or k2, 20), "type": t2}
                if col.get("calc"):
                    m = re.fullmatch(r"([a-z][a-z0-9_]*)\*([a-z][a-z0-9_]*)", str(col["calc"]).replace(" ", ""))
                    if not m or m.group(1) not in ck or m.group(2) not in ck or t2 == "text":
                        raise Invalid(f"{g['label']}: 계산식은 앞선 숫자 열 'a*b' 형식만")
                    cc["calc"] = f"{m.group(1)}*{m.group(2)}"
                cols.append(cc)
            if not cols or len(cols) > MAX_COLS:
                raise Invalid(f"{g['label']}: 열 1~{MAX_COLS}개")
            g["columns"] = cols
            g["sum"] = [s for s in (f.get("sum") or []) if s in ck and
                        next(x for x in cols if x["key"] == s)["type"] != "text"]
            g["rows"] = max(1, min(int(f.get("rows") or 5), MAX_ROWS))
        nf.append(g)
    for g in nf:  # half_field 는 같은 양식의 select 필드를 가리켜야 함
        if g.get("half_field") and not any(x["key"] == g["half_field"] and x["type"] == "select" for x in nf):
            raise Invalid(f"{g['label']}: half_field 는 선택 필드 키")
    out["fields"] = nf
    amt = str(d.get("amount") or "")
    if amt:
        a, _, b = amt.partition(".")
        f = next((x for x in nf if x["key"] == a), None)
        ok = f and ((f["type"] == "money" and not b) or (f["type"] == "table" and b in f.get("sum", [])))
        if not ok:
            raise Invalid("금액 필드는 금액(money) 필드 키 또는 '표키.합계열' 이어야 합니다")
    out["amount"] = amt
    if d.get("template_html"):  # 편집기 서식(선택) — 없으면 doctemplate.default_template
        from .doctemplate import check_template
        try:
            out["template_html"] = check_template(d["template_html"])
        except ValueError as e:
            raise Invalid(str(e))
    if out["doc_type"] == "expense" and not amt:
        raise Invalid("지출 양식은 금액 필드(amount)를 지정해야 합니다")
    return out


# ------------------------------------------------------------------ 값 검증
def _num(v, money, label):
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool) and not money:  # 저장된 값 재검증
        if v != v or abs(v) >= MAX_NUM:
            raise Invalid(f"{label}: 숫자 범위 오류")
        if v < 0:
            raise Invalid(f"{label}: 음수는 쓸 수 없습니다")
        return int(v) if v == int(v) else round(v, 2)
    if money:
        try:
            return pol.parse_amount(v)
        except pol.PolicyError:
            raise Invalid(f"{label}: 금액 형식 오류")
    t = str(v).replace(",", "").strip()
    if not re.fullmatch(r"-?\d{1,13}(\.\d{1,2})?", t):
        raise Invalid(f"{label}: 숫자 형식 오류")
    x = float(t)
    if abs(x) >= MAX_NUM:
        raise Invalid(f"{label}: 숫자 범위 오류")
    if x < 0:
        raise Invalid(f"{label}: 음수는 쓸 수 없습니다")
    return int(x) if x == int(x) else x


def _date(v, label):
    v = str(v or "").strip()
    if not v:
        return None
    try:
        return _dt.date.fromisoformat(v).isoformat()
    except ValueError:
        raise Invalid(f"{label}: 날짜는 YYYY-MM-DD")


def _days(a, b, workdays):
    d0, d1 = _dt.date.fromisoformat(a), _dt.date.fromisoformat(b)
    n = (d1 - d0).days + 1
    if n > 366:
        raise Invalid("기간은 1년 이내")
    if workdays:
        n = sum(1 for i in range(n) if (d0 + _dt.timedelta(i)).weekday() < 5)
    return n


def _soft(fn, v, *a):
    """형식이 틀려도 막지 않음(결재자가 확인) — 읽을 수 없으면 입력 그대로(한 줄 글) 보관."""
    try:
        return fn(v, *a)
    except Invalid:
        return _one(v, 100) or None


def _isnum(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def normalize(fd, values, n_files=0, strict=False):
    """값 정리·계산. 반환 (정리된 값 dict, 금액 또는 None).
    금액·기간·필수 항목은 막지 않는다(사용자 결정: 결재자가 확인). 읽을 수 있는 숫자·날짜만 계산에 쓰고,
    읽을 수 없는 값은 입력 그대로 보관. 금액은 항상 None(꺼내 확인하지 않음) → 양식 문서는 금액 한도 전결 없음."""
    values = values if isinstance(values, dict) else {}
    out = {}
    for f in fd["fields"]:
        k, t, lab, v = f["key"], f["type"], f["label"], values.get(f["key"])
        if t in ("text", "textarea"):
            x = _clean(v, f["max"]) if t == "textarea" else _one(v, f["max"])
            out[k] = x or None
        elif t in ("number", "money"):
            out[k] = _soft(_num, v, t == "money", lab)
        elif t == "date":
            out[k] = _soft(_date, v, lab)
        elif t == "select":
            out[k] = _one(v, 40) or None  # 선택지 밖 값도 보관(결재자 확인)
        elif t == "daterange":
            if isinstance(v, str):
                a, _, b = v.partition("~")
                v = {"from": a, "to": b}
            v = v if isinstance(v, dict) else {}
            a, b = _soft(_date, v.get("from"), lab), _soft(_date, v.get("to"), lab)
            if a and not b:
                b = a
            if a or b:
                n = None
                try:
                    if a and b and b >= a:
                        n = _days(a, b, f.get("workdays"))
                        hf = f.get("half_field")
                        if hf and a == b and "반차" in str(values.get(hf) or ""):
                            n = 0.5
                except (Invalid, ValueError):
                    n = None  # 날짜를 못 읽거나 너무 김 → 일수 없음
                out[k] = {"from": a or "", "to": b or "", "days": n}
            else:
                out[k] = None
        elif t == "file":
            out[k] = None  # 첨부 없으면 문서에 경고만(막지 않음)
            continue
        elif t == "table":
            rows = v.get("rows") if isinstance(v, dict) else v
            rows = rows if isinstance(rows, list) else []
            if len(rows) > MAX_ROWS:
                raise Invalid(f"{lab}: 최대 {MAX_ROWS}행")
            nr = []
            for i, r in enumerate(rows, 1):
                r = r if isinstance(r, dict) else {}
                row = {}
                for col in f["columns"]:
                    cv = r.get(col["key"])  # 계산 열(금액)도 기안자가 적은 값 그대로
                    row[col["key"]] = (_one(cv, 100) or None) if col["type"] == "text" else \
                        _soft(_num, cv, col["type"] == "money", f"{lab} {i}행 {col['label']}")
                if all(row[c["key"]] in (None, "") for c in f["columns"]):
                    continue  # 빈 행 버림
                nr.append(row)
            sums = {}  # 합계도 기안자가 적은 그대로(서버 계산·검증 안 함)
            sv = v.get("sum") if isinstance(v, dict) else None
            for s_ in f.get("sum", []):
                x = sv.get(s_) if isinstance(sv, dict) else None
                if x not in (None, ""):
                    sc = next(c for c in f["columns"] if c["key"] == s_)
                    x = _soft(_num, x, sc["type"] == "money", f"{lab} 합계")
                    if x not in (None, ""):
                        sums[s_] = x
            out[k] = {"rows": nr, "sum": sums} if (nr or sums) else None
        # 필수 항목 비어 있어도 막지 않음(결재자 확인)
    amount = None  # 금액을 꺼내 확인하지 않음(사용자 결정: 결재자가 확인) → 금액 한도 전결 없음
    return out, amount


def from_flat(fd, get):
    """웹 폼(f.키, f.키.from/to, f.키.행.열) → 값 dict. get(name) → str|None."""
    vals = {}
    for f in fd["fields"]:
        k = f["key"]
        if f["type"] == "daterange":
            vals[k] = {"from": get(f"f.{k}.from"), "to": get(f"f.{k}.to")}
        elif f["type"] == "table":
            rows = []
            for i in range(MAX_ROWS):  # 중간에 빈 행이 있어도 끝까지 읽음
                r = {c["key"]: get(f"f.{k}.{i}.{c['key']}") for c in f["columns"]}
                if all(x in (None, "") for x in r.values()):
                    continue
                rows.append(r)
            vals[k] = rows
        elif f["type"] != "file":
            vals[k] = get(f"f.{k}")
    return vals


def display(f, v):
    """필드 값 → 보기용 문자열(표 제외)."""
    if v in (None, ""):
        return "-"
    if f["type"] == "money":
        return f"{v:,}원" if _isnum(v) else str(v)
    if f["type"] == "number":
        return f"{v:,}" if _isnum(v) else str(v)
    if f["type"] == "daterange":
        d = v.get("days")
        return f"{v.get('from')} ~ {v.get('to')}" + (f" ({d:g}일)" if _isnum(d) else "")
    return str(v)


# ------------------------------------------------------------------ 기본 양식
def _t(key, label, req=False, **kw):
    return dict(key=key, label=label, type="text", required=req, **kw)


_ITEMS = {"key": "items", "label": "지출 내역", "type": "table", "required": True, "rows": 5,
          "columns": [{"key": "item", "label": "항목", "type": "text"}, {"key": "qty", "label": "수량", "type": "number"},
                      {"key": "price", "label": "단가", "type": "money"},
                      {"key": "amount", "label": "금액", "type": "money", "calc": "qty*price"},
                      {"key": "note", "label": "비고", "type": "text"}], "sum": ["amount"]}
_PROOF = {"key": "proof", "label": "증빙(영수증·세금계산서)", "type": "file", "required": True, "min_files": 1}
_PAY = {"key": "pay", "label": "결제 방법", "type": "select", "required": True,
        "options": ["법인카드", "계좌이체", "현금", "개인카드(환급)"]}

BUILTIN = [
    ("general", {"name": "일반기안(품의)", "category": "일반", "doc_type": "general", "retention": "5년",
                 "title_hint": "○○ 시행 품의", "body_hint": "1. 목적\n2. 내용\n3. 기대효과\n4. 행정사항",
                 "fields": [_t("purpose", "목적", True), {"key": "due", "label": "시행일", "type": "date"}]}),
    ("report", {"name": "업무보고", "category": "보고", "doc_type": "general", "retention": "3년",
                "title_hint": "○○ 주간 업무보고",
                "fields": [{"key": "kind", "label": "보고 종류", "type": "select", "required": True,
                            "options": ["일일보고", "주간보고", "월간보고", "결과보고"]},
                           {"key": "period", "label": "보고 기간", "type": "daterange", "required": True},
                           {"key": "done", "label": "실적", "type": "textarea", "required": True},
                           {"key": "plan", "label": "계획", "type": "textarea"},
                           {"key": "issue", "label": "이슈·건의", "type": "textarea"}]}),
    ("expense", {"name": "지출결의서", "category": "재정", "doc_type": "expense", "retention": "5년", "prefix": "지출",
                 "cc_depts": ["MGT"], "amount": "items.amount", "title_hint": "○○ 지출 결의",
                 "fields": [_t("purpose", "지출 목적", True), _ITEMS, _PAY,
                            {"key": "pay_date", "label": "지급(예정)일", "type": "date"},
                            _t("payee", "지급처"), _PROOF]}),
    ("purchase", {"name": "구매요청서", "category": "재정", "doc_type": "expense", "retention": "5년", "prefix": "구매",
                  "cc_depts": ["MGT"], "amount": "items.amount", "title_hint": "○○ 구매 요청",
                  "fields": [_t("purpose", "구매 사유", True), _ITEMS, _t("vendor", "구매처(업체)"),
                             {"key": "need_by", "label": "필요일", "type": "date", "required": True},
                             _t("place", "납품 장소"), {"key": "quote", "label": "견적서", "type": "file"}]}),
    ("trip", {"name": "출장신청서", "category": "인사", "doc_type": "general", "retention": "3년",
              "title_hint": "○○ 출장 신청",
              "fields": [_t("dest", "출장지", True), _t("purpose", "출장 목적", True),
                         {"key": "period", "label": "출장 기간", "type": "daterange", "required": True},
                         _t("with", "동행자"), {"key": "transport", "label": "교통편", "type": "select",
                                              "options": ["대중교통", "자가용", "법인차량", "항공"]},
                         {"key": "budget", "label": "예상 경비", "type": "money"}]}),
    ("trip_settle", {"name": "출장비정산서", "category": "재정", "doc_type": "expense", "retention": "5년",
                     "prefix": "정산", "cc_depts": ["MGT"], "amount": "items.amount", "title_hint": "○○ 출장비 정산",
                     "fields": [_t("ref", "출장신청 문서번호"),
                                {"key": "period", "label": "출장 기간", "type": "daterange", "required": True},
                                dict(_ITEMS, label="경비 내역"), _PAY, _PROOF]}),
    ("claim", {"name": "경비정산서", "category": "재정", "doc_type": "expense", "retention": "5년", "prefix": "정산",
               "cc_depts": ["MGT"], "amount": "items.amount", "title_hint": "○월 경비 정산",
               "fields": [_t("purpose", "사용 목적", True), dict(_ITEMS, label="사용 내역"), _PAY,
                          _t("account", "환급 계좌"), _PROOF]}),
    ("leave", {"name": "휴가신청서", "category": "인사", "doc_type": "leave", "retention": "3년",
               "title_hint": "휴가 신청",
               "fields": [{"key": "kind", "label": "휴가 종류", "type": "select", "required": True,
                           "options": ["연차", "오전반차", "오후반차", "병가", "경조", "공가", "기타"]},
                          {"key": "period", "label": "기간", "type": "daterange", "required": True, "workdays": True,
                           "half_field": "kind", "help": "주말 제외 일수 자동 계산, 반차는 하루만 0.5일"},
                          _t("reason", "사유"), _t("backup", "대체 근무자", True), _t("contact", "비상 연락처")]}),
    ("coop", {"name": "협조전", "category": "일반", "doc_type": "general", "retention": "3년",
              "title_hint": "○○ 협조 요청",
              "fields": [_t("to_dept", "수신 부서", True), _t("subject", "협조 사항", True),
                         {"key": "due", "label": "회신 기한", "type": "date"}]}),
    ("official", {"name": "공문(대외 발신)", "category": "대외", "doc_type": "general", "retention": "10년",
                  "prefix": "공문", "layout": "gov", "title_hint": "○○ 관련 협조 요청",
                  "body_hint": "1. 귀 기관의 무궁한 발전을 기원합니다.\n2. \n\n붙임  1부.  끝.",
                  "fields": [_t("recv", "수신", True), _t("via", "참조(경유)"), _t("sender", "발신명의", True,
                                                                                   help="예: 주식회사 ○○ 대표이사"),
                             _t("contact", "담당자·연락처")]}),
]


def builtin_defs():
    return [(code, check_def(copy.deepcopy(d))) for code, d in BUILTIN]
