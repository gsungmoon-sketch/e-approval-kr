"""사원·부서·직위·설정·2인 승인(변경요청) 관리. App 의 mixin — core 의 트랜잭션/감사 기능을 사용한다.

원칙: 관리자 전용, 모든 변경은 해시체인 감사로그 + emp_history, 인사이동/직위변경은 관련 부서장에게 쪽지.
퇴사는 로그인만 막고 문서·감사기록은 절대 삭제하지 않는다.
"""
from __future__ import annotations

import csv
import io
import json
import re
import secrets

from . import policy as pol
from .core_base import (DEPT_RE, USER_RE, Forbidden, Invalid, NotFound, _valid_date, hash_password, now_iso,
                        today)

EMP_STATUS = {"active": "재직", "leave": "휴직", "retired": "퇴사"}
KO2STATUS = {v: k for k, v in EMP_STATUS.items()}
SETTINGS = {"two_person": ("2인 승인(정책·권한 변경에 두 번째 관리자 승인 필요)", False),
            "doc_number": ("문서번호 형식 — {dept} 부서, {year}/{yy} 연도, {prefix} 양식 접두어(없으면 부서), {seq} 일련번호",
                           "{dept}-{year}-{seq}"),
            "allow_freeform": ("양식 없는 자유 기안 허용(끄면 양식이 있을 때 반드시 양식으로 기안 — 지출을 일반으로 올려 전결 한도를 피하는 것 방지)",
                               False),
            "emp_digits": ("자동 사번 일련번호 자릿수(입사일 YYMMDD 뒤, 2 또는 3) — 새 번호부터 적용", 2),
            "show_contacts": ("조직도에 이메일·전화 공개", False)}
POS_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{1,19}$")
EMAIL_RE = re.compile(r"^[^@\s<>\"']{1,64}@[^@\s<>\"']{1,190}$")
PHONE_RE = re.compile(r"^[0-9+()\- ]{3,20}$")
CSV_COLS = {"사번": "emp_no", "이름": "name", "부서": "dept", "직위": "position", "아이디": "username",
            "이메일": "email", "전화": "phone", "입사일": "hired", "상태": "status", "직책": "title"}
_PW_ALPHA = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"

MIGRATIONS = [
    ("users", "emp_no", "text"), ("users", "position", "text"), ("users", "email", "text"),
    ("users", "phone", "text"), ("users", "hired", "text"), ("users", "status", "text not null default 'active'"),
    ("users", "must_change", "int not null default 0"), ("depts", "active", "int not null default 1"),
    ("docs", "doc_type", "text not null default 'general'"), ("docs", "amount", "int"),
    ("posts", "pinned", "int not null default 0"),
]
ORG_SCHEMA = """
create unique index if not exists users_empno on users(emp_no);
create table if not exists positions(code text primary key, name text not null unique, rank int not null,
  scope text not null default 'dept', dept_head int not null default 0, active int not null default 1);
create table if not exists emp_history(id integer primary key, username text not null, at text, actor text,
  field text, before text, after text);
create index if not exists emp_hist_user on emp_history(username);
create table if not exists policies(id integer primary key, dept text not null, version int not null, json text not null,
  created text, created_by text, approved_by text, unique(dept, version));
create table if not exists changes(id integer primary key, kind text not null, target text, payload text not null,
  summary text, requested_by text not null, created text, status text not null default 'pending',
  decided_by text, decided_at text);
"""


_FORMULA = ("=", "+", "-", "@", "\t", "\r")


def _unguard(v):
    """내보내기 때 붙인 수식 방지 따옴표 제거(왕복 가능)."""
    return v[1:] if len(v) > 1 and v[0] == "'" and v[1] in _FORMULA else v


def temp_password():
    while True:
        p = "".join(secrets.choice(_PW_ALPHA) for _ in range(10))
        if re.search(r"\d", p) and re.search(r"[a-z]", p) and re.search(r"[A-Z]", p):
            return p


class OrgMixin:
    # ------------------------------------------------------------ schema / migration
    def _migrate(self, c):
        for table, col, decl in MIGRATIONS:
            cols = {r["name"] for r in c.execute(f"pragma table_info({table})")}
            if col not in cols:
                c.execute(f"alter table {table} add column {col} {decl}")
        c.executescript(ORG_SCHEMA)
        if not c.execute("select count(*) from positions").fetchone()[0]:
            for code, name, rank, scope, head in pol.DEFAULT_POSITIONS:
                c.execute("insert into positions(code,name,rank,scope,dept_head) values(?,?,?,?,?)",
                          (code, name, rank, scope, head))
        # v0.1 데이터: role=head → 직위 부서장, 나머지 팀원 / active=0 → 퇴사
        c.execute("update users set position='head', role='user' where role='head' and position is null")
        c.execute("update users set position='member' where position is null and role='user'")
        c.execute("update users set status='retired' where active=0 and status='active'")
        for r in c.execute("select username, hired, created from users where emp_no is null order by created").fetchall():
            c.execute("update users set emp_no=? where username=?",
                      (self._next_emp_no(c, r["hired"] or (r["created"] or today())[:10]), r["username"]))

    # ------------------------------------------------------------ helpers
    def _positions(self, c, include_inactive=False):
        q = "select * from positions" + ("" if include_inactive else " where active=1") + " order by rank"
        return {r["code"]: dict(r) for r in c.execute(q)}

    def positions(self, include_inactive=False):
        with self._ro() as c:
            return self._positions(c, include_inactive)

    def _is_head(self, c, u):
        if u["role"] == "head":
            return True
        if not u["position"]:
            return False
        r = c.execute("select dept_head from positions where code=?", (u["position"],)).fetchone()
        return bool(r and r["dept_head"])

    def _dept_heads(self, c, dept):
        return [r["username"] for r in c.execute(
            "select u.username from users u left join positions p on p.code=u.position "
            "where u.dept=? and u.active=1 and (u.role='head' or p.dept_head=1)", (dept,))]

    def _emp(self, c, ident):
        """아이디 또는 사번으로 사원 조회(퇴사자 포함)."""
        ident = str(ident or "").strip()
        r = c.execute("select * from users where username=?", (ident.lower(),)).fetchone() or \
            c.execute("select * from users where emp_no=?", (ident,)).fetchone()
        if not r:
            raise NotFound(f"사원 없음: {ident}")
        return r

    def _hist_emp(self, c, actor, username, field, before, after):
        c.execute("insert into emp_history(username,at,actor,field,before,after) values(?,?,?,?,?,?)",
                  (username, now_iso(), actor, field, None if before is None else str(before),
                   None if after is None else str(after)))

    def _setting(self, c, key):
        r = c.execute("select v from kv where k=?", ("setting:" + key,)).fetchone()
        return json.loads(r["v"]) if r else SETTINGS[key][1]

    def _admins(self, c):
        return [r["username"] for r in c.execute("select username from users where role='admin' and active=1")]

    def _defer(self, c, actor, kind, target, payload, summary):
        """2인 승인 대상이면 변경요청으로 보류하고 id 반환, 아니면 None."""
        if not self._setting(c, "two_person"):
            return None
        cur = c.execute("insert into changes(kind,target,payload,summary,requested_by,created) values(?,?,?,?,?,?)",
                        (kind, target, json.dumps(payload, ensure_ascii=False), summary, actor, now_iso()))
        cid = cur.lastrowid
        for a in self._admins(c):
            if a != actor:
                self._notify(c, a, f"[승인요청] #{cid} {summary} (요청 {actor})")
        self._audit(c, actor, "change_request", None, {"id": cid, "kind": kind, "target": target, "summary": summary})
        return cid

    # ------------------------------------------------------------ settings
    def settings(self, actor):
        self._require_admin(actor)
        with self._ro() as c:
            return {k: {"value": self._setting(c, k), "label": v[0]} for k, v in SETTINGS.items()}

    def setting_set(self, actor, key, value):
        self._require_admin(actor)
        if key not in SETTINGS:
            raise Invalid(f"설정 없음: {key}")
        if key == "emp_digits":
            try:
                value = int(value)
            except (TypeError, ValueError):
                value = 0
            if value not in (2, 3):
                raise Invalid("사번 일련번호 자릿수는 2 또는 3")
        elif key == "doc_number":
            from .forms import check_number_format
            value = check_number_format(value)
        else:
            value = bool(value) if not isinstance(value, str) else value.lower() in ("1", "on", "true", "yes", "y")
        with self._tx() as c:
            cur = self._setting(c, key)
            if cur == value:
                return {"key": key, "value": value}
            if key == "two_person" and value and len(self._admins(c)) < 2:
                raise Invalid("2인 승인은 활성 관리자가 2명 이상일 때만 켤 수 있습니다")
            if key == "two_person" and not value:  # 끄는 것도 두 번째 관리자 승인 필요
                cid = self._defer(c, actor, "setting", key, {"key": key, "value": value}, "2인 승인 끄기")
                if cid:
                    return {"pending": cid}
            self._apply_setting(c, actor, key, value)
        return {"key": key, "value": value}

    def _apply_setting(self, c, actor, key, value, approved_by=None):
        before = self._setting(c, key)
        c.execute("insert or replace into kv(k,v) values(?,?)", ("setting:" + key, json.dumps(value)))
        self._audit(c, actor, "setting", None, {"key": key, "before": before, "after": value, "approved_by": approved_by})

    # ------------------------------------------------------------ change requests (2인 승인)
    def change_list(self, actor, status="pending"):
        self._require_admin(actor)
        with self._ro() as c:
            q = "select * from changes" + (" where status=?" if status else "") + " order by id desc limit 200"
            return [dict(r) for r in c.execute(q, (status,) if status else ())]

    def change_decide(self, actor, cid, approve: bool):
        self._require_admin(actor)
        with self._tx() as c:
            r = c.execute("select * from changes where id=?", (int(cid),)).fetchone()
            if not r or r["status"] != "pending":
                raise NotFound("대기 중인 변경요청 없음")
            if r["requested_by"] == actor:
                raise Forbidden("요청자 본인은 승인/거절할 수 없습니다(다른 관리자 필요)")
            p = json.loads(r["payload"])
            if approve:
                req = r["requested_by"]
                rq = c.execute("select role, active from users where username=?", (req,)).fetchone()
                if not rq or rq["role"] != "admin" or not rq["active"]:
                    raise Invalid("요청자가 더 이상 활성 관리자가 아닙니다 — 거절하세요")
                if r["kind"] == "setting":
                    if p["key"] == "two_person" and p["value"] and len(self._admins(c)) < 2:
                        raise Invalid("활성 관리자가 2명 미만")
                    self._apply_setting(c, req, p["key"], p["value"], approved_by=actor)
                elif r["kind"] == "policy":
                    pp = p["policy"]
                    if pp is not None:
                        pp = pol.normalize(pp, self._positions(c))  # 그 사이 직위가 폐지됐으면 실패
                    self._apply_policy(c, req, p["dept"], pp, approved_by=actor)
                elif r["kind"] == "emp_move":
                    u = self._emp(c, p["username"])
                    if u["status"] == "retired":
                        raise Invalid("대상이 퇴사했습니다 — 거절하세요")
                    f = self._check_emp_fields(c, {"dept": p.get("dept"), "position": p.get("position")})
                    self._apply_move(c, req, p["username"], f["dept"], f["position"], approved_by=actor)
                elif r["kind"] == "emp_role":
                    if self._emp(c, p["username"])["status"] != "active":
                        raise Invalid("대상이 재직 중이 아닙니다 — 거절하세요")
                    self._apply_role(c, req, p["username"], p["role"], approved_by=actor)
                elif r["kind"] == "emp_status":
                    self._apply_status(c, req, self._emp(c, p["username"]), p["status"], approved_by=actor)
                elif r["kind"] == "pos_update":
                    self._validate_pos_fields(c, p["code"], p["fields"])
                    self._apply_pos_update(c, req, p["code"], p["fields"], approved_by=actor)
                elif r["kind"] == "pos_add":
                    if c.execute("select 1 from positions where code=? or name=?", (p["code"], p["name"])).fetchone():
                        raise Invalid("이미 있는 직위 — 거절하세요")
                    self._insert_pos(c, req, p, approved_by=actor)
                else:
                    raise Invalid("알 수 없는 변경 종류")
            c.execute("update changes set status=?, decided_by=?, decided_at=? where id=?",
                      ("approved" if approve else "rejected", actor, now_iso(), r["id"]))
            self._notify(c, r["requested_by"], f"[{'승인' if approve else '거절'}] 변경요청 #{r['id']} {r['summary']}")
            self._audit(c, actor, "change_" + ("approve" if approve else "reject"), None, {"id": r["id"]})
        return {"id": r["id"], "status": "approved" if approve else "rejected"}

    # ------------------------------------------------------------ departments
    def _add_dept(self, c, code, name):
        if not DEPT_RE.match(code or ""):
            raise Invalid("부서코드는 영문 대문자로 시작하는 2~10자(A-Z0-9)")
        name = (name or "").strip()
        if not name or len(name) > 50:
            raise Invalid("부서명 필요(50자 이하)")
        if c.execute("select 1 from depts where code=?", (code,)).fetchone():
            raise Invalid(f"이미 있는 부서: {code}")
        c.execute("insert into depts(code,name,created) values(?,?,?)", (code, name, now_iso()))

    def add_dept(self, actor, code, name):
        self._require_admin(actor)
        code = (code or "").strip().upper()
        with self._tx() as c:
            self._add_dept(c, code, name)
            self._audit(c, actor, "dept_add", None, {"code": code, "name": name})
        return {"code": code, "name": name}

    def dept_rename(self, actor, code, name):
        self._require_admin(actor)
        name = (name or "").strip()
        if not name or len(name) > 50:
            raise Invalid("부서명 필요(50자 이하)")
        with self._tx() as c:
            r = c.execute("select * from depts where code=?", (code,)).fetchone()
            if not r:
                raise NotFound("부서 없음")
            c.execute("update depts set name=? where code=?", (name, code))
            self._audit(c, actor, "dept_rename", None, {"code": code, "before": r["name"], "after": name})
        return {"code": code, "name": name}

    def dept_set_active(self, actor, code, active: bool):
        self._require_admin(actor)
        with self._tx() as c:
            if not c.execute("select 1 from depts where code=?", (code,)).fetchone():
                raise NotFound("부서 없음")
            if not active and c.execute("select count(*) from users where dept=? and status!='retired'",
                                        (code,)).fetchone()[0]:
                raise Invalid("재직/휴직 사원이 있는 부서는 폐지할 수 없습니다(먼저 부서 이동)")
            c.execute("update depts set active=? where code=?", (1 if active else 0, code))
            self._audit(c, actor, "dept_active", None, {"code": code, "active": bool(active)})

    def list_depts(self, include_inactive=False):
        with self._ro() as c:
            q = ("select d.code,d.name,d.active,(select count(*) from users u where u.dept=d.code and u.status!='retired') "
                 "as members from depts d" + ("" if include_inactive else " where d.active=1") + " order by d.code")
            return [dict(r) for r in c.execute(q)]

    # ------------------------------------------------------------ positions (직위)
    def pos_add(self, actor, code, name, rank, scope="dept", dept_head=False):
        """직위 추가. 서열은 결재규정 의미를 바꾸므로 2인 승인 대상."""
        self._require_admin(actor)
        code = (code or "").strip().lower()
        if not POS_CODE_RE.match(code):
            raise Invalid("직위 코드는 영문 소문자 2~20자(a-z0-9_)")
        name = (name or "").strip()
        if not name or len(name) > 20:
            raise Invalid("직위명 필요(20자 이하)")
        if scope not in ("dept", "org"):
            raise Invalid("범위는 dept(부서 내) 또는 org(전사)")
        try:
            rank = int(rank)
        except (TypeError, ValueError):
            raise Invalid("서열은 숫자")
        p = {"code": code, "name": name, "rank": rank, "scope": scope, "dept_head": 1 if dept_head else 0}
        with self._tx() as c:
            if c.execute("select 1 from positions where code=? or name=?", (code, name)).fetchone():
                raise Invalid("이미 있는 직위 코드/이름")
            cid = self._defer(c, actor, "pos_add", code, p, f"직위 추가 {name}(서열 {rank}, {scope}"
                                                            f"{', 부서장 열람' if dept_head else ''})")
            if cid:
                return {"pending": cid}
            self._insert_pos(c, actor, p)
        return self.positions(True)[code]

    def _insert_pos(self, c, actor, p, approved_by=None):
        c.execute("insert into positions(code,name,rank,scope,dept_head) values(?,?,?,?,?)",
                  (p["code"], p["name"], p["rank"], p["scope"], p["dept_head"]))
        self._audit(c, actor, "pos_add", None, dict(p, approved_by=approved_by))

    def _policy_refs(self, c, code):
        """현재(최신) 결재규정에서 이 직위를 쓰는 부서들."""
        out = []
        for r in c.execute("select dept, json from policies p where version=(select max(version) from policies q "
                           "where q.dept=p.dept)"):
            pj = json.loads(r["json"]) or {}
            for tp in (pj.get("types") or {}).values():
                if any(st.get("pos") == code for st in tp["line"]) or any(j["pos"] == code for j in tp["jeon"]):
                    out.append(r["dept"])
                    break
            if (pj.get("delegation") or {}).get("min_pos") == code and r["dept"] not in out:
                out.append(r["dept"])
        return out

    def _validate_pos_fields(self, c, code, fields):
        r = c.execute("select * from positions where code=?", (code,)).fetchone()
        if not r:
            raise NotFound("직위 없음")
        if fields.get("active") == 0:
            if c.execute("select count(*) from users where position=? and status!='retired'", (code,)).fetchone()[0]:
                raise Invalid("이 직위의 재직 사원이 있어 폐지할 수 없습니다")
            refs = self._policy_refs(c, code)
            if refs:
                raise Invalid(f"결재규정에서 쓰는 직위라 폐지할 수 없습니다: {', '.join(refs)}")
        if "name" in fields and c.execute("select 1 from positions where name=? and code!=?",
                                          (fields["name"], code)).fetchone():
            raise Invalid("같은 이름의 직위가 있습니다")
        return r

    def pos_update(self, actor, code, name=None, rank=None, scope=None, dept_head=None, active=None):
        self._require_admin(actor)
        fields = {}
        if name is not None:
            name = name.strip()
            if not name or len(name) > 20:
                raise Invalid("직위명 필요(20자 이하)")
            fields["name"] = name
        if rank is not None:
            try:
                fields["rank"] = int(rank)
            except (TypeError, ValueError):
                raise Invalid("서열은 숫자")
        if scope is not None:
            if scope not in ("dept", "org"):
                raise Invalid("범위는 dept 또는 org")
            fields["scope"] = scope
        if dept_head is not None:
            fields["dept_head"] = 1 if dept_head else 0
        if active is not None:
            fields["active"] = 1 if active else 0
        with self._tx() as c:
            r = c.execute("select * from positions where code=?", (code,)).fetchone()
            if not r:
                raise NotFound("직위 없음")
            fields = {k: v for k, v in fields.items() if r[k] != v}
            if not fields:
                return dict(r)
            self._validate_pos_fields(c, code, fields)
            if set(fields) - {"name"}:  # 서열·범위·열람권·폐지는 결재 의미를 바꾸므로 2인 승인 대상
                cid = self._defer(c, actor, "pos_update", code, {"code": code, "fields": fields},
                                  f"직위 {r['name']} 변경 {fields}")
                if cid:
                    return {"pending": cid}
            self._apply_pos_update(c, actor, code, fields)
        return self.positions(True)[code]

    def _apply_pos_update(self, c, actor, code, fields, approved_by=None):
        r = c.execute("select * from positions where code=?", (code,)).fetchone()
        before = {k: r[k] for k in fields}
        for k, v in fields.items():
            if k not in ("name", "rank", "scope", "dept_head", "active"):
                raise Invalid("잘못된 필드")
            c.execute(f"update positions set {k}=? where code=?", (v, code))
        self._audit(c, actor, "pos_update", None, {"code": code, "before": before, "after": fields,
                                                   "approved_by": approved_by})
        if "dept_head" in fields or "scope" in fields:
            depts = {u["dept"] for u in c.execute("select dept from users where position=? and active=1", (code,))}
            for d in depts:
                for h in self._dept_heads(c, d):
                    self._notify(c, h, f"[권한변경] 직위 {r['name']} 설정 변경: {fields}")

    # ------------------------------------------------------------ employees (사원)
    def _check_emp_fields(self, c, f):
        if "name" in f:
            f["name"] = (f["name"] or "").strip()
            if not f["name"] or len(f["name"]) > 50:
                raise Invalid("이름 필요(50자 이하)")
        if f.get("email"):
            f["email"] = f["email"].strip()
            if not EMAIL_RE.match(f["email"]):
                raise Invalid(f"이메일 형식 오류: {f['email']}")
        if f.get("phone"):
            f["phone"] = f["phone"].strip()
            if not PHONE_RE.match(f["phone"]):
                raise Invalid(f"전화 형식 오류: {f['phone']}")
        if f.get("hired"):
            if not _valid_date(f["hired"]):
                raise Invalid(f"입사일은 YYYY-MM-DD: {f['hired']}")
        if f.get("title") is not None:
            f["title"] = (f["title"] or "").strip()[:30]
        if "dept" in f and f["dept"] is not None:
            r = c.execute("select active from depts where code=?", (f["dept"],)).fetchone()
            if not r or not r["active"]:
                raise NotFound(f"부서 없음/폐지: {f['dept']}")
        if "position" in f and f["position"] is not None:
            try:
                f["position"] = pol.pos_code(f["position"], self._positions(c))
            except pol.PolicyError as e:
                raise Invalid(str(e))
        return f

    def _next_emp_no(self, c, hired=None):
        """자동 사번 = 입사일 YYMMDD + 입사일별 일련번호(2/3자리, 설정). 호출자의 BEGIN IMMEDIATE 안에서 원자적 발급.
        수동 사번과 겹치는 번호는 건너뛴다."""
        hired = hired or today()
        if not _valid_date(hired):
            raise Invalid(f"입사일은 YYYY-MM-DD: {hired}")
        ymd = hired.replace("-", "")[2:]  # YYMMDD
        digits = int(self._setting(c, "emp_digits") or 2)
        while True:
            n = self._next_serial(c, "#EMP" + ymd, 0)
            if n >= 10 ** digits:
                raise Invalid(f"{hired} 입사자 자동 사번 일련번호가 {digits}자리를 넘었습니다 — 설정에서 3자리로 바꾸거나 수동 입력")
            e = f"{ymd}{n:0{digits}d}"
            if not c.execute("select 1 from users where emp_no=? or lower(username)=?", (e, e)).fetchone():
                return e

    def _pos_is_head(self, c, code):
        if not code:
            return False
        r = c.execute("select dept_head from positions where code=?", (code,)).fetchone()
        return bool(r and r["dept_head"])

    def _emp_create(self, c, actor, name, dept, position, emp_no=None, username=None, email=None, phone=None,
                    hired=None, title="", admin=False, password=None, status="active"):
        f = self._check_emp_fields(c, {"name": name, "dept": dept, "position": position, "email": email,
                                       "phone": phone, "hired": hired, "title": title})
        f["hired"] = f.get("hired") or today()  # 입사일 기본 = 오늘(자동 사번의 기준)
        emp_no = (str(emp_no).strip() if emp_no else "") or self._next_emp_no(c, f["hired"])
        if not re.match(r"^[A-Za-z0-9-]{1,20}$", emp_no):
            raise Invalid(f"사번 형식 오류(영숫자·- 20자): {emp_no}")
        username = (username or emp_no).strip().lower()
        if not USER_RE.match(username):
            raise Invalid(f"아이디 형식 오류(영소문자/숫자로 시작, 2~32자): {username}")
        if username in ("import", "export"):
            raise Invalid(f"쓸 수 없는 아이디: {username}")
        if c.execute("select 1 from users where emp_no=? or lower(username)=lower(?)", (emp_no, emp_no)).fetchone():
            raise Invalid(f"이미 있는 사번(또는 같은 아이디): {emp_no}")
        if c.execute("select 1 from users where username=? or lower(emp_no)=?", (username, username)).fetchone():
            raise Invalid(f"이미 있는 아이디(또는 같은 사번): {username}")
        if status not in EMP_STATUS:
            raise Invalid("상태는 재직/휴직/퇴사")
        temp = password or temp_password()
        pos = f["position"]
        deferred = []
        Pn = self._positions(c)
        lowest = min(v["rank"] for v in Pn.values()) if Pn else 0
        if pos and self._setting(c, "two_person") and (self._pos_is_head(c, pos) or Pn[pos]["rank"] > lowest):
            deferred.append(("emp_move", {"username": username, "position": pos}, f"{name} 직위 {pos} 부여"))
            pos = None
        role = "user"
        if admin:
            if self._setting(c, "two_person"):
                deferred.append(("emp_role", {"username": username, "role": "admin"}, f"{name} 관리자 권한 부여"))
            else:
                role = "admin"
        c.execute("insert into users(username,name,dept,role,title,pwhash,created,emp_no,position,email,phone,hired,"
                  "status,active,must_change) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (username, f["name"], f["dept"], role, f.get("title") or "", hash_password(temp), now_iso(), emp_no,
                   pos, f.get("email"), f.get("phone"), f.get("hired"), status, 1 if status == "active" else 0,
                   0 if password else 1))
        self._hist_emp(c, actor, username, "create", None, f"{f['dept']}/{pos}/{role}")
        self._audit(c, actor, "emp_add", None, {"user": username, "emp_no": emp_no, "dept": f["dept"],
                                                "position": pos, "role": role})
        for h in self._dept_heads(c, f["dept"]):
            if h != username:
                self._notify(c, h, f"[인사] {f['name']}({emp_no}) {f['dept']} 배치")
        pend = [self._defer(c, actor, k, username, p, s) for k, p, s in deferred]
        return {"username": username, "emp_no": emp_no, "temp_password": None if password else temp,
                "pending": [p for p in pend if p]}

    def emp_add(self, actor, name, dept, position, emp_no=None, username=None, email=None, phone=None, hired=None,
                title="", admin=False):
        """사원 등록. 임시 비밀번호는 반환값에서 한 번만 보여 준다(첫 로그인 시 변경 강제)."""
        self._require_admin(actor)
        with self._tx() as c:
            return self._emp_create(c, actor, name, (dept or "").strip().upper(), position, emp_no, username, email,
                                    phone, hired, title, admin)

    def emp_edit(self, actor, ident, **fields):
        """이름·이메일·전화·입사일·직책 수정(부서/직위는 emp_move)."""
        self._require_admin(actor)
        allowed = {"name", "email", "phone", "hired", "title"}
        fields = {k: v for k, v in fields.items() if k in allowed and v is not None}
        with self._tx() as c:
            u = self._emp(c, ident)
            f = self._check_emp_fields(c, dict(fields))
            changed = {}
            for k, v in f.items():
                v = v or None if k != "name" else v
                if (u[k] or None) != v:
                    changed[k] = (u[k], v)
                    c.execute(f"update users set {k}=? where username=?", (v, u["username"]))
                    self._hist_emp(c, actor, u["username"], k, u[k], v)
            if changed:
                self._audit(c, actor, "emp_edit", None, {"user": u["username"],
                                                         "changes": {k: list(v) for k, v in changed.items()}})
            return {"username": u["username"], "changed": list(changed)}

    def emp_move(self, actor, ident, dept=None, position=None):
        """인사이동(부서)·직위 변경. 부서장 열람권이 생기는 변경은 2인 승인 대상."""
        self._require_admin(actor)
        with self._tx() as c:
            u = self._emp(c, ident)
            f = self._check_emp_fields(c, {"dept": (dept or "").strip().upper() or None, "position": position})
            nd = f["dept"] if dept else None
            np = f["position"] if position else None
            if (nd is None or nd == u["dept"]) and (np is None or np == u["position"]):
                raise Invalid("변경 사항이 없습니다")
            grants = bool(np) or (self._pos_is_head(c, u["position"]) and nd)  # 직위 변경·부서장 이동
            if grants:
                cid = self._defer(c, actor, "emp_move", u["username"], {"username": u["username"], "dept": nd,
                                                                        "position": np},
                                  f"{u['name']} 이동/직위 → {nd or u['dept']}/{np or u['position']}")
                if cid:
                    return {"pending": cid}
            self._apply_move(c, actor, u["username"], nd, np)
            return {"username": u["username"], "dept": nd or u["dept"], "position": np or u["position"]}

    def _apply_move(self, c, actor, username, dept, position, approved_by=None):
        u = self._emp(c, username)
        old_dept, old_pos = u["dept"], u["position"]
        if dept and dept != old_dept:
            c.execute("update users set dept=? where username=?", (dept, username))
            self._hist_emp(c, actor, username, "dept", old_dept, dept)
        if position and position != old_pos:
            c.execute("update users set position=? where username=?", (position, username))
            self._hist_emp(c, actor, username, "position", old_pos, position)
        new_dept, new_pos = dept or old_dept, position or old_pos
        self._audit(c, actor, "emp_move", None, {"user": username, "before": [old_dept, old_pos],
                                                 "after": [new_dept, new_pos], "approved_by": approved_by})
        P = self._positions(c, True)
        pn = lambda p: P[p]["name"] if p in P else (p or "-")  # noqa: E731
        msg = f"[인사] {u['name']}: {old_dept}/{pn(old_pos)} → {new_dept}/{pn(new_pos)}"
        for d in {old_dept, new_dept}:
            for h in self._dept_heads(c, d):
                if h != username:
                    self._notify(c, h, msg)
        self._notify(c, username, msg)
        c.execute("delete from sessions where username=?", (username,))

    def emp_set_role(self, actor, ident, role):
        self._require_admin(actor)
        if role not in ("user", "admin"):
            raise Invalid("역할은 user 또는 admin")
        with self._tx() as c:
            u = self._emp(c, ident)
            if u["role"] == role:
                raise Invalid("변경 사항이 없습니다")
            if u["username"] == actor:
                raise Invalid("자기 역할은 바꿀 수 없습니다")
            if role == "admin":
                if u["status"] != "active":
                    raise Invalid("재직 중인 사원만 관리자로 지정할 수 있습니다")
                cid = self._defer(c, actor, "emp_role", u["username"], {"username": u["username"], "role": role},
                                  f"{u['name']} 관리자 권한 부여")
                if cid:
                    return {"pending": cid}
            elif self._setting(c, "two_person") and len(self._admins(c)) <= 2:
                raise Invalid("2인 승인 중에는 활성 관리자를 2명 미만으로 줄일 수 없습니다(먼저 2인 승인 끄기)")
            self._apply_role(c, actor, u["username"], role)
            return {"username": u["username"], "role": role}

    def _apply_role(self, c, actor, username, role, approved_by=None):
        u = self._emp(c, username)
        c.execute("update users set role=? where username=?", (role, username))
        c.execute("delete from sessions where username=?", (username,))
        self._hist_emp(c, actor, username, "role", u["role"], role)
        self._audit(c, actor, "emp_role", None, {"user": username, "before": u["role"], "after": role,
                                                 "approved_by": approved_by})
        for h in self._dept_heads(c, u["dept"]) + [username]:
            self._notify(c, h, f"[권한변경] {u['name']} 역할 {u['role']} → {role}")

    def emp_set_status(self, actor, ident, status):
        """재직/휴직/퇴사. 휴직·퇴사 = 로그인 차단·결재선 제외, 문서·기록은 보존."""
        self._require_admin(actor)
        status = KO2STATUS.get(status, status)
        if status not in EMP_STATUS:
            raise Invalid("상태는 재직/휴직/퇴사")
        with self._tx() as c:
            u = self._emp(c, ident)
            if u["status"] == status:
                raise Invalid("변경 사항이 없습니다")
            if u["role"] == "admin" and status == "active":  # 휴면 관리자 복귀 = 관리자 권한 부여
                cid = self._defer(c, actor, "emp_status", u["username"], {"username": u["username"], "status": status},
                                  f"관리자 {u['name']} 재직 복귀")
                if cid:
                    return {"pending": cid}
            self._apply_status(c, actor, u, status)
        return {"username": u["username"], "status": status}

    def _apply_status(self, c, actor, u, status, approved_by=None):
        if u["username"] == actor and status != "active":
            raise Invalid("자기 자신을 휴직/퇴사 처리할 수 없습니다")
        if u["role"] == "admin" and status != "active" and u["status"] == "active":
            n = len(self._admins(c))
            if n <= 1:
                raise Invalid("마지막 관리자는 휴직/퇴사 처리할 수 없습니다")
            if self._setting(c, "two_person") and n <= 2:
                raise Invalid("2인 승인 중에는 활성 관리자를 2명 미만으로 줄일 수 없습니다(먼저 2인 승인 끄기)")
        c.execute("update users set status=?, active=? where username=?",
                  (status, 1 if status == "active" else 0, u["username"]))
        if status != "active":
            c.execute("delete from sessions where username=?", (u["username"],))
            c.execute("update delegations set revoked=1 where (from_user=? or to_user=?) and revoked=0",
                      (u["username"], u["username"]))
            for d in c.execute("select id,title,drafter from docs where status='pending' and cur_user=?",
                               (u["username"],)).fetchall():
                self._notify(c, d["drafter"], f"[결재지연] #{d['id']} {d['title']}: 결재자 {u['name']} "
                                              f"{EMP_STATUS[status]} — 회수 후 재상신하거나 관리자에게 문의", d["id"])
        self._hist_emp(c, actor, u["username"], "status", u["status"], status)
        self._audit(c, actor, "emp_status", None, {"user": u["username"], "before": u["status"], "after": status,
                                                   "approved_by": approved_by})
        for h in self._dept_heads(c, u["dept"]):
            if h != u["username"]:
                self._notify(c, h, f"[인사] {u['name']} {EMP_STATUS[status]}")

    def emp_reset_password(self, actor, ident):
        self._require_admin(actor)
        temp = temp_password()
        with self._tx() as c:
            u = self._emp(c, ident)
            if u["role"] == "admin" and u["username"] != actor and self._setting(c, "two_person"):
                raise Forbidden("2인 승인 중에는 다른 관리자의 비밀번호를 초기화할 수 없습니다(본인이 변경)")
            c.execute("update users set pwhash=?, must_change=1 where username=?", (hash_password(temp), u["username"]))
            c.execute("delete from sessions where username=?", (u["username"],))
            self._audit(c, actor, "emp_resetpw", None, {"user": u["username"]})
        return {"username": u["username"], "temp_password": temp}

    def emp_list(self, actor, dept=None, q=None, status=None):
        self._require_admin(actor)
        with self._ro() as c:
            conds, args = [], []
            if dept:
                conds.append("u.dept=?"); args.append(dept.upper())
            if status:
                conds.append("u.status=?"); args.append(KO2STATUS.get(status, status))
            if q:
                conds.append("(u.name like ? or u.emp_no like ? or u.username like ?)")
                args += [f"%{q}%"] * 3
            w = (" where " + " and ".join(conds)) if conds else ""
            rows = c.execute("select u.username,u.emp_no,u.name,u.dept,d.name as dept_name,u.position,p.name as "
                             "position_name,u.title,u.email,u.phone,u.hired,u.status,u.role from users u left join depts d "
                             f"on d.code=u.dept left join positions p on p.code=u.position{w} "
                             "order by u.dept, p.rank desc, u.name", args)
            return [dict(r, status_ko=EMP_STATUS.get(r["status"], r["status"])) for r in rows]

    def emp_get(self, actor, ident):
        self._require_admin(actor)
        with self._ro() as c:
            u = dict(self._emp(c, ident))
            u.pop("pwhash", None)
            u["history"] = [dict(r) for r in c.execute(
                "select at,actor,field,before,after from emp_history where username=? order by id", (u["username"],))]
            u["status_ko"] = EMP_STATUS.get(u["status"], u["status"])
            return u

    def org_directory(self, actor, q=None, dept=None):
        """조직도: 로그인 사용자 누구나. 연락처는 show_contacts 설정 시에만."""
        with self._ro() as c:
            self._user(c, actor)
            show = self._setting(c, "show_contacts")
            conds, args = ["u.status='active'"], []
            if dept:
                conds.append("u.dept=?"); args.append(dept.upper())
            if q:
                conds.append("(u.name like ? or d.name like ? or p.name like ?)")
                args += [f"%{q[:50]}%"] * 3
            rows = c.execute("select u.username,u.name,u.dept,d.name as dept_name,p.name as position_name,u.title,"
                             "u.email,u.phone from users u left join depts d on d.code=u.dept left join positions p "
                             f"on p.code=u.position where {' and '.join(conds)} order by d.code, p.rank desc, u.name", args)
            out = []
            for r in rows:
                x = dict(r)
                if not show:
                    x.pop("email"); x.pop("phone")
                out.append(x)
            return out

    # ------------------------------------------------------------ CSV import/export
    @staticmethod
    def _decode_csv(data: bytes) -> str:
        for enc in ("utf-8-sig", "cp949"):
            try:
                return data.decode(enc)
            except UnicodeDecodeError:
                continue
        raise Invalid("CSV 인코딩을 알 수 없습니다(UTF-8 또는 CP949)")

    def emp_import(self, actor, data: bytes, apply=False):
        """CSV 일괄 등록/수정. apply=False 면 미리보기만. 오류가 하나라도 있으면 반영하지 않는다(전부 아니면 전무)."""
        self._require_admin(actor)
        if len(data) > 5 * 1024 * 1024:
            raise Invalid("CSV 최대 5MB")
        text = self._decode_csv(data)
        rd = csv.reader(io.StringIO(text))
        try:
            header = next(rd)
        except StopIteration:
            raise Invalid("빈 CSV")
        cols = []
        for h in header:
            h = h.strip()
            cols.append(CSV_COLS.get(h, h if h in CSV_COLS.values() else None))
        if "name" not in cols or "dept" not in cols:
            raise Invalid("CSV 헤더에 최소 '이름','부서' 필요 (사번,이름,부서,직위,아이디,이메일,전화,입사일,상태,직책)")
        rows = []
        for ln, rec in enumerate(rd, 2):
            if not any(x.strip() for x in rec):
                continue
            rows.append((ln, {k: _unguard(rec[i].strip()) for i, k in enumerate(cols) if k and i < len(rec)}))
            if len(rows) > 5000:
                raise Invalid("한 번에 최대 5000행")
        result = {"rows": [], "errors": 0, "created": [], "applied": False}
        c = self._connect()
        try:
            c.execute("begin immediate")
            seen = set()
            for ln, r in rows:
                item = {"line": ln, "emp_no": r.get("emp_no") or "(자동)", "name": r.get("name"), "action": "", "error": ""}
                c.execute("savepoint row")
                try:
                    key = r.get("emp_no") or r.get("username")
                    if key and key in seen:
                        raise Invalid("CSV 안에서 사번/아이디 중복")
                    if key:
                        seen.add(key)
                    st = KO2STATUS.get(r.get("status") or "재직", r.get("status") or "active")
                    ex = None
                    if r.get("emp_no"):
                        ex = c.execute("select * from users where emp_no=?", (r["emp_no"],)).fetchone()
                    if ex:
                        item["action"] = "수정"
                        f = self._check_emp_fields(c, {k: r[k] for k in ("name", "email", "phone", "hired", "title")
                                                       if r.get(k)})
                        for k, v in f.items():
                            if (ex[k] or None) != (v or None):
                                c.execute(f"update users set {k}=? where username=?", (v, ex["username"]))
                                self._hist_emp(c, actor, ex["username"], k, ex[k], v)
                        nd = r["dept"].upper() if r.get("dept") else None
                        npos = r.get("position") or None
                        if (nd and nd != ex["dept"]) or npos:
                            f2 = self._check_emp_fields(c, {"dept": nd or ex["dept"], "position": npos})
                            if (f2["dept"] != ex["dept"]) or (f2["position"] and f2["position"] != ex["position"]):
                                if self._setting(c, "two_person") and (
                                        (f2["position"] and f2["position"] != ex["position"])
                                        or self._pos_is_head(c, ex["position"])):
                                    raise Invalid("2인 승인 대상 변경(직위 변경·부서장 이동)은 개별 처리하세요")
                                self._apply_move(c, actor, ex["username"], f2["dept"], f2["position"])
                        if st != ex["status"]:
                            if st not in EMP_STATUS:
                                raise Invalid("상태는 재직/휴직/퇴사")
                            if ex["role"] == "admin" and st == "active" and self._setting(c, "two_person"):
                                raise Invalid("관리자 복귀는 2인 승인 대상 — 개별 처리하세요")
                            self._apply_status(c, actor, self._emp(c, ex["username"]), st)
                    else:
                        item["action"] = "등록"
                        res = self._emp_create(c, actor, r.get("name"), (r.get("dept") or "").upper(),
                                               r.get("position") or None, r.get("emp_no"), r.get("username"),
                                               r.get("email"), r.get("phone"), r.get("hired"), r.get("title") or "",
                                               status=st)
                        item["emp_no"] = res["emp_no"]
                        result["created"].append({"username": res["username"], "emp_no": res["emp_no"],
                                                  "name": r.get("name"), "temp_password": res["temp_password"]})
                    c.execute("release row")
                except (Invalid, NotFound, Forbidden) as e:
                    c.execute("rollback to row")
                    c.execute("release row")
                    item["error"] = str(e)
                    result["errors"] += 1
                result["rows"].append(item)
            if apply and not result["errors"]:
                self._audit(c, actor, "emp_import", None, {"rows": len(rows), "created": len(result["created"])})
                self._flush_audit_c(c)
                c.execute("commit")
                result["applied"] = True
            else:
                c.execute("rollback")
                c.after.clear()
                c.audited = False
        finally:
            c.close()
        if not result["applied"]:
            for x in result["created"]:
                x["temp_password"] = None  # 미리보기: 비밀번호 비공개(반영되지 않음)
        return result

    def emp_export(self, actor, encoding="utf-8-sig"):
        rows = self.emp_list(actor)
        if encoding not in ("utf-8-sig", "cp949"):
            raise Invalid("encoding 은 utf-8-sig 또는 cp949")
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\r\n")
        w.writerow(["사번", "이름", "부서", "직위", "아이디", "이메일", "전화", "입사일", "상태", "직책"])
        for r in rows:
            vals = [r["emp_no"], r["name"], r["dept"], r["position_name"] or "", r["username"], r["email"] or "",
                    r["phone"] or "", r["hired"] or "", r["status_ko"], r["title"] or ""]
            w.writerow([("'" + v) if isinstance(v, str) and v[:1] in _FORMULA else v for v in vals])
        try:
            data = buf.getvalue().encode(encoding)
        except UnicodeEncodeError:
            raise Invalid("CP949로 표현할 수 없는 글자가 있습니다 — UTF-8로 내보내세요")
        self._audit_simple(actor, "emp_export", {"rows": len(rows)})
        return data

    def _audit_simple(self, actor, action, detail):
        with self._tx() as c:
            self._audit(c, actor, action, None, detail)
