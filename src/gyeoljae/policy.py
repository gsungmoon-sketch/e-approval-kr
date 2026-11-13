"""부서별 결재 규정(정책): 순수 함수만. 검증·정규화·결재선 생성·전결 판정·diff.

정책 JSON 형태(정규화 후):
{
  "types": {
    "general": {"line": [{"pos": "head"}], "jeon": [], "allow_extra": true, "cc": []},
    "expense": {"line": [{"pos": "lead", "optional": true}, {"pos": "head"}, {"pos": "ceo"}],
                "jeon": [{"pos": "head", "max_amount": 1000000}], "allow_extra": false, "cc": ["hr"]},
    "leave": {...}
  },
  "delegation": {"allowed": true, "same_dept_only": false, "min_pos": null, "max_days": null}
}
결재선 단계: {"pos": 직급코드} 또는 {"user": 아이디}, "optional": 해당자 없으면 생략.
직위 scope='dept'(팀원/팀장/부서장)는 기안 부서 안에서, 'org'(임원/대표)는 전사에서 찾는다.
"""
from __future__ import annotations

import difflib
import json
import re

# 기본 직위(관리자가 positions 표에서 편집 가능). P = {code: {"name","rank","scope"('dept'|'org'),"dept_head"}}
DEFAULT_POSITIONS = [
    ("member", "팀원", 10, "dept", 0), ("lead", "팀장", 20, "dept", 0), ("head", "부서장", 30, "dept", 1),
    ("exec", "임원", 40, "org", 0), ("ceo", "대표", 50, "org", 0)]
DOC_TYPES = {"general": "일반", "expense": "지출", "leave": "휴가"}
KO2TYPE = {v: k for k, v in DOC_TYPES.items()}
MAX_STEPS = 10
MAX_AMOUNT = 10 ** 13
USER_RE = re.compile(r"^[a-z][a-z0-9_.-]{1,31}$")


from .core_base import Invalid


class PolicyError(Invalid):
    """규정 오류(=입력 오류, 웹 400)."""


def pos_code(x, P) -> str:
    x = str(x or "").strip()
    if x.lower() in P:
        return x.lower()
    for code, v in P.items():
        if v["name"] == x:
            return code
    raise PolicyError(f"알 수 없는 직위: {x} ({'/'.join(v['name'] for v in P.values())})")


def type_code(x) -> str:
    x = str(x or "general").strip()
    c = KO2TYPE.get(x, x.lower())
    if c not in DOC_TYPES:
        raise PolicyError(f"알 수 없는 문서 종류: {x} (일반/지출/휴가)")
    return c


def parse_amount(s):
    """'150만', '1,500,000', '1.5억', '3천만' → 정수 원. 빈 값 → None."""
    if s is None:
        return None
    if isinstance(s, int) and not isinstance(s, bool):
        v = s
    else:
        t = str(s).strip().replace(",", "").replace(" ", "").replace("원", "")
        if not t:
            return None
        m = re.fullmatch(r"(?:(\d+(?:\.\d+)?)억)?(?:(\d+(?:\.\d+)?)천만)?(?:(\d+(?:\.\d+)?)만)?(\d+)?", t)
        if not m or not any(m.groups()):
            raise PolicyError(f"금액 형식 오류: {s}")
        eok, cheonman, man, won = m.groups()
        v = round(float(eok or 0) * 1e8 + float(cheonman or 0) * 1e7 + float(man or 0) * 1e4 + int(won or 0))
    if v < 0 or v > MAX_AMOUNT:
        raise PolicyError("금액 범위 오류")
    return int(v)


def fmt_amount(v):
    if v is None:
        return "-"
    if v and v % 10000 == 0:
        return f"{v // 10000:,}만원"
    return f"{v:,}원"


def _split(s):
    return [x.strip() for x in re.split(r"[,→>\n]+", s or "") if x.strip()]


def parse_line_spec(s):
    """'팀장?, 부서장, 대표, @hr' → 단계 목록. '?'=해당자 없으면 생략, '@아이디'=특정인."""
    if isinstance(s, list):
        items = s
    else:
        items = _split(s)
    out = []
    for it in items:
        if isinstance(it, dict):
            st = dict(it)
        else:
            t = str(it).strip()
            opt = t.endswith("?")
            t = t.rstrip("?").strip()
            st = {"user": t[1:].strip().lower()} if t.startswith("@") else {"pos": t}
            st["optional"] = opt
        out.append(st)
    return out


def parse_jeon_spec(s):
    """'부서장:100만, 임원:500만, 대표' → [{'pos':'head','max_amount':1000000}, ...]."""
    if isinstance(s, list):
        items = s
    else:
        items = _split(s)
    out = []
    for it in items:
        if isinstance(it, dict):
            out.append(dict(it))
            continue
        p, _, a = str(it).partition(":")
        out.append({"pos": p.strip(), "max_amount": a.strip() or None})
    return out


def _bool(v, default):
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "on", "y", "예")


def normalize(pol, P) -> dict:
    if not isinstance(pol, dict):
        raise PolicyError("정책은 객체여야 합니다")
    types_in = pol.get("types") or {}
    if not isinstance(types_in, dict):
        raise PolicyError("types 는 객체")
    types = {}
    for k, tp in types_in.items():
        t = type_code(k)
        if not isinstance(tp, dict):
            raise PolicyError(f"{k}: 객체여야 합니다")
        line = []
        for st in parse_line_spec(tp.get("line") or []):
            if "user" in st and st["user"] is not None:
                u = str(st["user"]).strip().lower()
                if not USER_RE.match(u):
                    raise PolicyError(f"{DOC_TYPES[t]}: 잘못된 아이디 {u}")
                line.append({"user": u, "optional": _bool(st.get("optional"), False)})
            else:
                line.append({"pos": pos_code(st.get("pos"), P), "optional": _bool(st.get("optional"), False)})
        if len(line) > MAX_STEPS:
            raise PolicyError(f"{DOC_TYPES[t]}: 결재선 최대 {MAX_STEPS}단계")
        jeon = []
        for r in parse_jeon_spec(tp.get("jeon") or []):
            ma = parse_amount(r.get("max_amount"))
            jeon.append({"pos": pos_code(r.get("pos"), P), "max_amount": ma})
        cc = []
        for x in (tp.get("cc") if isinstance(tp.get("cc"), list) else _split(tp.get("cc") or "")):
            x = str(x).strip().lower().lstrip("@")
            if not USER_RE.match(x):
                raise PolicyError(f"{DOC_TYPES[t]}: 잘못된 참조 아이디 {x}")
            if x not in cc:
                cc.append(x)
        allow_extra = _bool(tp.get("allow_extra"), True)
        if not line and not allow_extra:
            raise PolicyError(f"{DOC_TYPES[t]}: 결재선이 비었는데 추가 결재자도 금지되어 있습니다")
        types[t] = {"line": line, "jeon": sorted(jeon, key=lambda r: P[r["pos"]]["rank"]),
                    "allow_extra": allow_extra, "cc": cc}
    dg = pol.get("delegation") or {}
    md = dg.get("max_days")
    try:
        md = int(md) if md not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        raise PolicyError("대결 최대 기간은 숫자(일)")
    if md is not None and not (1 <= md <= 366):
        raise PolicyError("대결 최대 기간은 1~366일")
    mp = dg.get("min_pos")
    delegation = {"allowed": _bool(dg.get("allowed"), True), "same_dept_only": _bool(dg.get("same_dept_only"), False),
                  "min_pos": pos_code(mp, P) if mp not in (None, "") else None, "max_days": md}
    return {"types": types, "delegation": delegation}


def canonical_json(pol) -> str:
    return json.dumps(pol, ensure_ascii=False, sort_keys=True, indent=1)


def diff(old, new) -> str:
    a = canonical_json(old).splitlines() if old else []
    b = canonical_json(new).splitlines() if new else []
    return "\n".join(difflib.unified_diff(a, b, "이전", "변경", lineterm="", n=1))


def _pname(P, code):
    return P[code]["name"] if code in P else f"({code})"


def line_text(line, P):
    out = []
    for st in line:
        t = "@" + st["user"] if "user" in st else _pname(P, st["pos"])
        out.append(t + ("?" if st.get("optional") else ""))
    return ", ".join(out)


def jeon_text(jeon, P):
    return ", ".join(_pname(P, r["pos"]) + (f":{fmt_amount(r['max_amount'])}" if r["max_amount"] is not None else "")
                     for r in jeon)


def jeon_ok(tp, position, amount, P) -> bool:
    """position 보유자가 이 종류·금액에서 전결할 수 있는가(상위 직위는 하위 직위 권한 포함)."""
    if not tp or not position or position not in P:
        return False
    for r in tp.get("jeon", []):
        if r["pos"] in P and P[position]["rank"] >= P[r["pos"]]["rank"]:
            if r["max_amount"] is None or (amount is not None and amount <= r["max_amount"]):
                return True
    return False


def build_line(tp, drafter, users, amount, P):
    """정책 템플릿으로 결재선 생성. drafter={'username','position','dept'}, users=[활성 사용자 dict].
    반환: [{'user','final'}], 결재자 못 찾으면 PolicyError."""
    steps, seen = [], {drafter.get("username")}
    dr_rank = P.get(drafter.get("position") or "", {}).get("rank", 0)
    for st in tp["line"]:
        if "user" in st:
            cand = [u for u in users if u["username"] == st["user"]]
            label = "@" + st["user"]
        else:
            pos = st["pos"]
            if pos not in P:
                if st.get("optional"):
                    continue
                raise PolicyError(f"정책의 직위 '{pos}'가 폐지되었습니다(관리자에게 문의)")
            if dr_rank >= P[pos]["rank"]:
                continue  # 기안자가 이미 그 직위 이상 → 해당 단계 생략
            cand = [u for u in users if u.get("position") == pos
                    and (P[pos]["scope"] != "dept" or u["dept"] == drafter["dept"])]
            label = P[pos]["name"]
        fresh = sorted((u for u in cand if u["username"] not in seen), key=lambda u: u["username"])
        if not fresh and cand:
            continue  # 해당자가 이미 앞 단계에 있음(@지정 + 직위 중복 등)
        cand = fresh
        if not cand:
            if st.get("optional"):
                continue
            raise PolicyError(f"결재선 '{label}' 해당자가 없습니다(관리자에게 문의)")
        seen.add(cand[0]["username"])
        steps.append({"user": cand[0]["username"], "position": cand[0].get("position"), "final": False})
    for i, s in enumerate(steps):
        if jeon_ok(tp, s["position"], amount, P):
            if i < len(steps) - 1:
                s["final"] = True
            break
    return steps
