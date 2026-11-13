"""부서별 결재 규정(정책) 관리 + 문서 결재선 자동 구성. App 의 mixin.

- 정책은 부서마다 버전 관리(policies 표). 새 상신만 최신 버전을 쓰고, 진행 중 문서는 상신 당시 버전(meta.policy)을 유지.
- 정책 변경은 감사로그(전/후 diff) + 해당 부서장 쪽지. 2인 승인 설정 시 두 번째 관리자 승인 후 적용.
- 결재선은 서버가 정책으로 만든다(클라이언트가 보낸 결재선은 '추가 결재자'로만 취급).
"""
from __future__ import annotations

import datetime as _dt
import json

from . import policy as pol
from .core_base import Forbidden, Invalid, NotFound, now_iso, sha256


class RulesMixin:
    # ------------------------------------------------------------ policy storage
    def _policy_row(self, c, dept, version=None):
        if version is None:
            return c.execute("select * from policies where dept=? order by version desc limit 1", (dept,)).fetchone()
        return c.execute("select * from policies where dept=? and version=?", (dept, int(version))).fetchone()

    def policy_get(self, actor, dept):
        self._require_admin(actor)
        dept = (dept or "").upper()
        with self._ro() as c:
            if not c.execute("select 1 from depts where code=?", (dept,)).fetchone():
                raise NotFound("부서 없음")
            r = self._policy_row(c, dept)
            P = self._positions(c, True)
            if not r:
                return {"dept": dept, "version": 0, "policy": None, "text": self._policy_text(None, P)}
            p = json.loads(r["json"])
            return {"dept": dept, "version": r["version"], "policy": p, "created": r["created"],
                    "created_by": r["created_by"], "approved_by": r["approved_by"], "text": self._policy_text(p, P)}

    @staticmethod
    def _policy_text(p, P):
        """폼 표시용 텍스트."""
        out = {}
        for t in pol.DOC_TYPES:
            tp = (p or {}).get("types", {}).get(t)
            out[t] = {"line": pol.line_text(tp["line"], P) if tp else "", "jeon": pol.jeon_text(tp["jeon"], P) if tp else "",
                      "allow_extra": tp["allow_extra"] if tp else True, "cc": ", ".join(tp["cc"]) if tp else "",
                      "enabled": bool(tp)}
        dg = (p or {}).get("delegation") or {}
        out["delegation"] = {"allowed": dg.get("allowed", True), "same_dept_only": dg.get("same_dept_only", False),
                             "min_pos": dg.get("min_pos") or "", "max_days": dg.get("max_days") or ""}
        return out

    def policy_set(self, actor, dept, policy):
        """정책 전체 교체(새 버전). policy=None 이면 정책 해제(자유 결재선). 2인 승인 시 {'pending': id}."""
        self._require_admin(actor)
        dept = (dept or "").upper()
        with self._tx() as c:
            if not c.execute("select 1 from depts where code=? and active=1", (dept,)).fetchone():
                raise NotFound("부서 없음/폐지")
            P = self._positions(c)
            if policy is not None:
                try:
                    policy = pol.normalize(policy, P)
                except pol.PolicyError as e:
                    raise Invalid(str(e))
                for tp in policy["types"].values():
                    for u in tp["cc"] + [st["user"] for st in tp["line"] if "user" in st]:
                        self._user(c, u)
            cur = self._policy_row(c, dept)
            if (json.loads(cur["json"]) if cur else None) == policy:
                raise Invalid("변경 사항이 없습니다")
            cid = self._defer(c, actor, "policy", dept, {"dept": dept, "policy": policy},
                              f"{dept} 결재규정 변경 (현재 v{cur['version'] if cur else 0})")
            if cid:
                return {"pending": cid}
            v = self._apply_policy(c, actor, dept, policy)
        return {"dept": dept, "version": v}

    def _apply_policy(self, c, actor, dept, policy, approved_by=None):
        cur = self._policy_row(c, dept)
        old = json.loads(cur["json"]) if cur else None
        v = (cur["version"] if cur else 0) + 1
        js = json.dumps(policy, ensure_ascii=False, sort_keys=True)
        c.execute("insert into policies(dept,version,json,created,created_by,approved_by) values(?,?,?,?,?,?)",
                  (dept, v, js, now_iso(), actor, approved_by))
        d = pol.diff(old, policy)
        self._audit(c, actor, "policy_set", None, {"dept": dept, "version": v, "before_sha": sha256(
            json.dumps(old, ensure_ascii=False, sort_keys=True).encode()) if old else None,
            "after_sha": sha256(js.encode()), "diff": d[:4000], "approved_by": approved_by})
        for h in self._dept_heads(c, dept):
            self._notify(c, h, f"[결재규정] {dept} 결재규정이 v{v}로 변경되었습니다(변경 {actor}"
                               f"{', 승인 ' + approved_by if approved_by else ''}). 새 상신부터 적용.")
        return v

    def policy_update_type(self, actor, dept, doc_type, line=None, jeon=None, allow_extra=None, cc=None,
                           enabled=True):
        """한 문서종류만 수정(나머지는 현재 버전 유지) — CLI/메뉴용 편의."""
        cur = self.policy_get(actor, dept)["policy"] or {"types": {}, "delegation": {}}
        t = pol.type_code(doc_type)
        types = dict(cur.get("types", {}))
        if not enabled:
            types.pop(t, None)
        else:
            tp = dict(types.get(t) or {"line": [], "jeon": [], "allow_extra": True, "cc": []})
            if line is not None:
                tp["line"] = line
            if jeon is not None:
                tp["jeon"] = jeon
            if allow_extra is not None:
                tp["allow_extra"] = allow_extra
            if cc is not None:
                tp["cc"] = cc
            types[t] = tp
        return self.policy_set(actor, dept, {"types": types, "delegation": cur.get("delegation") or {}})

    def policy_update_delegation(self, actor, dept, **kw):
        cur = self.policy_get(actor, dept)["policy"] or {"types": {}, "delegation": {}}
        dg = dict(cur.get("delegation") or {})
        dg.update({k: v for k, v in kw.items() if v is not None})
        return self.policy_set(actor, dept, {"types": cur.get("types", {}), "delegation": dg})

    def policy_history(self, actor, dept):
        self._require_admin(actor)
        dept = (dept or "").upper()
        with self._ro() as c:
            rows = c.execute("select * from policies where dept=? order by version", (dept,)).fetchall()
        out, prev = [], None
        for r in rows:
            p = json.loads(r["json"])
            out.append({"version": r["version"], "created": r["created"], "created_by": r["created_by"],
                        "approved_by": r["approved_by"], "diff": pol.diff(prev, p)})
            prev = p
        return list(reversed(out))

    def policy_overview(self, actor):
        self._require_admin(actor)
        with self._ro() as c:
            return [dict(r) for r in c.execute(
                "select d.code, d.name, d.active, (select max(version) from policies p where p.dept=d.code) as version "
                "from depts d order by d.code")]

    # ------------------------------------------------------------ line resolution
    def _active_users(self, c):
        return [dict(r) for r in c.execute("select username,name,dept,position,title,role from users where active=1")]

    def _resolve_line(self, c, drafter, doc_type, amount, line_input, cc_input, policy_ref=None):
        """반환 (steps, ccs, policy_ref). 정책 없는 부서는 기존 자유 결재선 규칙."""
        dept = drafter["dept"]
        r = (self._policy_row(c, policy_ref["dept"], policy_ref["version"]) if policy_ref
             else self._policy_row(c, dept))
        p = json.loads(r["json"]) if r else None
        tp = None
        if p:
            tp = p["types"].get(doc_type) or p["types"].get("general")
        if not tp:
            steps, ccs = self._validate_line(c, drafter["username"], line_input, cc_input)
            return steps, ccs, None
        P = self._positions(c)
        extras = []
        for x in line_input or []:
            x = x.strip()
            if not x:
                continue
            if x.endswith("!"):
                raise Invalid("결재규정 적용 부서는 전결을 직접 지정할 수 없습니다(규정이 정함)")
            extras.append(x.lower())
        if extras and not tp["allow_extra"]:
            raise Invalid("이 문서 종류는 결재자를 추가할 수 없습니다(부서 결재규정)")
        try:
            built = pol.build_line(tp, dict(drafter), self._active_users(c), amount, P)
        except pol.PolicyError as e:
            raise Invalid(str(e))
        if not built:
            # 규정상 결재자가 남지 않음(최상위 직위 기안 등) → 추가 결재자만으로는 부족: 자유 결재선 규칙으로 검증
            steps, ccs = self._validate_line(c, drafter["username"], extras, cc_input)
            for st in steps:
                st["from_policy"] = False
            return steps, ccs, {"dept": r["dept"], "version": r["version"], "doc_type": doc_type, "fallback": True}
        names = [b["user"] for b in built]
        seq = []
        for x in extras:
            if x == drafter["username"]:
                raise Invalid("기안자는 결재선에 들어갈 수 없습니다")
            if x in names or x in [s for s, _ in seq]:
                raise Invalid(f"결재선 중복: {x}")
            self._user(c, x)
            seq.append((x, False, False))
        seq += [(b["user"], b["final"], True) for b in built]
        if not seq:
            raise Invalid("규정상 결재자가 없습니다(관리자에게 문의)")
        if len(seq) > 10:
            raise Invalid("결재선은 최대 10명")
        steps = []
        for uname, final, from_policy in seq:
            u = self._user(c, uname)
            steps.append({"user": uname, "name": u["name"], "title": u["title"], "position": u["position"],
                          "final": final, "from_policy": from_policy, "status": "waiting", "acted_by": None, "proxy_for": None, "at": None,
                          "comment": ""})
        ccs = []
        for x in list(cc_input or []) + tp["cc"]:
            x = (x or "").strip().lower()
            if not x or x == drafter["username"] or x in seq_names(steps) or x in ccs:
                continue
            if not c.execute("select 1 from users where username=? and active=1", (x,)).fetchone():
                if x in tp["cc"]:
                    continue  # 필수 참조자가 퇴사/휴직 → 생략
                raise NotFound(f"사용자 없음: {x}")
            ccs.append(x)
        snap = {k: {"rank": v["rank"], "scope": v["scope"], "name": v["name"]} for k, v in P.items()}
        return steps, ccs, {"dept": r["dept"], "version": r["version"], "doc_type": doc_type, "positions": snap}

    def preview_line(self, actor, doc_type="general", amount=None, dept=None, drafter=None):
        """누가 결재하게 되는지 미리보기. 관리자는 임의 부서(가상 팀원 기안), 일반 사용자는 본인 기안 기준."""
        t = pol.type_code(doc_type)
        amount = pol.parse_amount(amount)
        with self._ro() as c:
            me = self._user(c, actor)
            if me["role"] == "admin" and (dept or drafter):
                if drafter:
                    d = dict(self._user(c, drafter))
                else:
                    dept = dept.upper()
                    if not c.execute("select 1 from depts where code=?", (dept,)).fetchone():
                        raise NotFound("부서 없음")
                    low = min(self._positions(c).values(), key=lambda x: x["rank"])["code"]
                    d = {"username": "(가상기안자)", "dept": dept, "position": low, "name": "가상", "title": ""}
            else:
                d = dict(me)
            r = self._policy_row(c, d["dept"])
            p = json.loads(r["json"]) if r else None
            tp = (p["types"].get(t) or p["types"].get("general")) if p else None
            if not tp:
                return {"dept": d["dept"], "policy_version": 0, "free": True, "line": [], "cc": [],
                        "note": "결재규정 없음: 기안자가 결재선을 직접 지정(부서장/관리자 1명 이상)"}
            P = self._positions(c)
            try:
                built = pol.build_line(tp, d, self._active_users(c), amount, P)
            except pol.PolicyError as e:
                raise Invalid(str(e))
            users = {u["username"]: u for u in self._active_users(c)}
            line = [{"user": b["user"], "name": users[b["user"]]["name"],
                     "position": P.get(b["position"], {}).get("name"), "final": b["final"]} for b in built]
            return {"dept": d["dept"], "policy_version": r["version"], "free": False, "doc_type": t, "amount": amount,
                    "line": line, "cc": tp["cc"], "allow_extra": tp["allow_extra"]}

    def _jeon_allowed(self, c, meta, holder, step):
        """수동 전결 권한. 규정 문서: 규정이 만든 단계만, 상신 당시 직위·서열 스냅숏 기준(진행 문서 불변)."""
        ref = meta.get("policy")
        if not ref or ref.get("fallback"):
            return holder["role"] == "admin" or self._is_head(c, holder)
        if not step.get("from_policy"):
            return False
        r = self._policy_row(c, ref["dept"], ref["version"])
        p = json.loads(r["json"]) if r else None
        if not p:
            return False
        tp = p["types"].get(meta.get("doc_type") or "general") or p["types"].get("general")
        P = ref.get("positions") or self._positions(c, True)
        return pol.jeon_ok(tp, step.get("position"), meta.get("amount"), P)

    def _check_delegation_rules(self, c, from_u, to_u, start, end):
        r = self._policy_row(c, from_u["dept"])
        dg = ((json.loads(r["json"]) or {}).get("delegation") if r else None) or {}
        if dg.get("allowed") is False:
            raise Forbidden("이 부서 결재규정상 대결 위임이 허용되지 않습니다")
        if dg.get("same_dept_only") and to_u["dept"] != from_u["dept"]:
            raise Forbidden("같은 부서 사람에게만 위임할 수 있습니다(부서 결재규정)")
        if dg.get("min_pos"):
            P = self._positions(c, True)
            need = P.get(dg["min_pos"], {}).get("rank", 0)
            if P.get(to_u["position"] or "", {}).get("rank", 0) < need:
                raise Forbidden(f"대결자는 {P.get(dg['min_pos'], {}).get('name', dg['min_pos'])} 이상이어야 합니다")
        if dg.get("max_days"):
            days = (_dt.date.fromisoformat(end) - _dt.date.fromisoformat(start)).days + 1
            if days > dg["max_days"]:
                raise Forbidden(f"대결 기간은 최대 {dg['max_days']}일입니다(부서 결재규정)")


def seq_names(steps):
    return [s["user"] for s in steps]
