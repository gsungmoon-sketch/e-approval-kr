"""문서 분류·보관(태그 방식 — 문서를 옮기지 않음) + "이 문서로 기안". App mixin.

- 부서 분류: 부서별 분류 나무(기본 회계·인사·비품·일반). 부서장급·관리자가 관리. 문서마다 0~1개(docs.dclass).
- 사업(프로젝트) 문서함: 부서를 가로지르는 묶음(참여 부서·사람, 진행/종료). 문서마다 0~N개(doc_projects).
- 내 폴더(개인 보관함): home.py 의 folders — 여기서는 기안 때 고르기·문서 화면에서 넣고 빼기만 더한다.
- 태그는 열람 권한을 절대 넓히지 않는다: 모든 목록은 기존 _VIS(_check_view 와 같은 규칙)로 다시 거른다.
- 분류·사업 변경: 기안자(기안중·회수·반려 상태), 그 부서 부서장급·문서관리 담당(users.doc_manager), 관리자.
  모든 변경은 감사로그에 이전 → 이후로 남긴다.
"""
from __future__ import annotations

import re
import unicodedata

from .core_base import Forbidden, Invalid, NotFound, now_iso

FILING_SCHEMA = """
create table if not exists dept_classes(id integer primary key, dept text not null, name text not null, parent int,
  sort int not null default 100, created text, unique(dept, name));
create table if not exists projects(id integer primary key, name text not null, code text,
  status text not null default 'open', created_by text not null, created text, updated text);
create unique index if not exists projects_code on projects(code) where code is not null;
create table if not exists project_members(project_id int not null, kind text not null, ref text not null,
  primary key(project_id, kind, ref));
create table if not exists doc_projects(doc_id int not null, project_id int not null, added text,
  primary key(doc_id, project_id));
create index if not exists doc_projects_p on doc_projects(project_id);
"""
FILING_MIGRATIONS = [("docs", "dclass", "int"), ("docs", "source_doc_id", "int"),
                     ("users", "doc_manager", "int not null default 0"), ("forms", "dclass", "text")]
DEFAULT_CLASSES = ("회계", "인사", "비품", "일반")
# 기본 양식 → 기본 분류(관리자가 양식마다 바꿀 수 있음: forms.dclass)
BUILTIN_CLASS = {"expense": "회계", "claim": "회계", "trip_settle": "회계", "leave": "인사", "trip": "인사",
                 "purchase": "비품"}
TYPE_CLASS = {"expense": "회계", "leave": "인사"}
PROJECT_STATUS = {"open": "진행", "closed": "종료"}
PCODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,19}$")
EDITABLE = ("draft", "withdrawn", "rejected")
KEEP = object()


def _name(s, what, n=30):
    s = unicodedata.normalize("NFC", str(s or "")).strip()
    s = " ".join(s.split())
    if not s or len(s) > n or any(unicodedata.category(ch)[0] == "C" for ch in s):
        raise Invalid(f"{what} 이름({n}자 이하) 필요")
    return s


def _ids(xs):
    out = []
    for x in xs or []:
        x = str(x).strip()
        if x.isdigit() and int(x) not in out:
            out.append(int(x))
    return out


class FilingMixin:
    # ------------------------------------------------------------ schema / migration
    def _filing_init(self, c):
        """기존 DB 제자리 이관: 없는 열만 추가(기존 행 = NULL/0 → 미분류·태그 없음) + 부서별 기본 분류 한 번 심기."""
        c.executescript(FILING_SCHEMA)
        for table, col, decl in FILING_MIGRATIONS:
            cols = {r["name"] for r in c.execute(f"pragma table_info({table})")}
            if col not in cols:
                c.execute(f"alter table {table} add column {col} {decl}")
        for r in c.execute("select code from depts").fetchall():
            self._seed_classes(c, r["code"])

    def _seed_classes(self, c, dept):
        key = "dclass_seeded:" + dept
        if c.execute("select 1 from kv where k=?", (key,)).fetchone():
            return  # 한 번만(부서장이 지운 기본 분류를 되살리지 않음)
        for i, n in enumerate(DEFAULT_CLASSES):
            c.execute("insert or ignore into dept_classes(dept,name,sort,created) values(?,?,?,?)",
                      (dept, n, (i + 1) * 10, now_iso()))
        c.execute("insert into kv(k,v) values(?,'1')", (key,))

    # ------------------------------------------------------------ 권한 도우미
    def _is_manager_of(self, c, u, dept):
        """그 부서 분류를 관리(부서장급·관리자)."""
        return u["role"] == "admin" or (u["dept"] == dept and self._is_head(c, u))

    def _can_reclass(self, c, u, d):
        """d = docs 행. 열람 가능 여부는 호출 쪽에서 먼저 확인."""
        if u["role"] == "admin":
            return True
        if d["drafter"] == u["username"] and d["status"] in EDITABLE:
            return True
        return u["dept"] == d["dept"] and (self._is_head(c, u) or bool(u["doc_manager"]))

    def _participates(self, c, u, pid):
        return bool(c.execute(
            "select 1 from project_members where project_id=? and ((kind='user' and ref=?) or (kind='dept' and ref=?)) "
            "union select 1 from projects where id=? and created_by=?",
            (pid, u["username"], u["dept"], pid, u["username"])).fetchone())

    # ------------------------------------------------------------ 부서 분류
    def _class_rows(self, c, dept):
        rows = [dict(r) for r in c.execute("select id,name,parent,sort from dept_classes where dept=? order by sort,name",
                                           (dept,))]
        kids = {}
        for r in rows:
            kids.setdefault(r["parent"], []).append(r)
        out = []

        def walk(p, depth):
            for r in kids.get(p, []):
                r["depth"] = depth
                out.append(r)
                if depth < 5:
                    walk(r["id"], depth + 1)
        walk(None, 0)
        return out

    def dclass_list(self, actor, dept=None, counts=False):
        """부서 분류 나무(들여쓰기 순서, depth). 다른 부서는 관리자만."""
        with self._ro() as c:
            u = self._user(c, actor)
            dept = (dept or u["dept"]).upper()
            if dept != u["dept"] and u["role"] != "admin":
                raise Forbidden("다른 부서 분류는 볼 수 없습니다")
            rows = self._class_rows(c, dept)
            if counts:  # 내가 볼 수 있는 문서만 셈
                args = self._vis_args(c, actor)
                args["dp"] = dept
                n = {r["dclass"]: r["n"] for r in c.execute(
                    f"select d.dclass, count(*) n from docs d where d.dept=:dp and {self._VIS} group by d.dclass", args)}
                for r in rows:
                    r["count"] = n.get(r["id"], 0)
            return rows

    def _class_of(self, c, cid):
        r = c.execute("select * from dept_classes where id=?", (int(cid),)).fetchone()
        if not r:
            raise NotFound("분류 없음")
        return r

    def dclass_add(self, actor, name, parent=None, dept=None):
        name = _name(name, "분류")
        with self._tx() as c:
            u = self._user(c, actor)
            dept = (dept or u["dept"]).upper()
            if not self._is_manager_of(c, u, dept):
                raise Forbidden("부서 분류는 그 부서 부서장 또는 관리자만 관리합니다")
            if not c.execute("select 1 from depts where code=?", (dept,)).fetchone():
                raise NotFound("부서 없음")
            pid = None
            if parent not in (None, "", 0, "0"):
                p = self._class_of(c, parent)
                if p["dept"] != dept:
                    raise Invalid("상위 분류가 다른 부서 것입니다")
                pid = p["id"]
            if c.execute("select count(*) from dept_classes where dept=?", (dept,)).fetchone()[0] >= 200:
                raise Invalid("분류는 부서당 최대 200개")
            if c.execute("select 1 from dept_classes where dept=? and name=?", (dept, name)).fetchone():
                raise Invalid("같은 이름의 분류가 있습니다")
            srt = c.execute("select coalesce(max(sort),0)+10 from dept_classes where dept=?", (dept,)).fetchone()[0]
            cur = c.execute("insert into dept_classes(dept,name,parent,sort,created) values(?,?,?,?,?)",
                            (dept, name, pid, srt, now_iso()))
            self._audit(c, actor, "dclass_add", None, {"dept": dept, "id": cur.lastrowid, "name": name, "parent": pid})
            return {"id": cur.lastrowid, "dept": dept, "name": name, "parent": pid}

    def dclass_rename(self, actor, cid, name):
        name = _name(name, "분류")
        with self._tx() as c:
            u = self._user(c, actor)
            r = self._class_of(c, cid)
            if not self._is_manager_of(c, u, r["dept"]):
                raise Forbidden("부서 분류는 그 부서 부서장 또는 관리자만 관리합니다")
            if c.execute("select 1 from dept_classes where dept=? and name=? and id!=?", (r["dept"], name, r["id"])).fetchone():
                raise Invalid("같은 이름의 분류가 있습니다")
            c.execute("update dept_classes set name=? where id=?", (name, r["id"]))
            self._audit(c, actor, "dclass_rename", None, {"dept": r["dept"], "id": r["id"], "before": r["name"], "after": name})
        return {"id": r["id"], "name": name}

    def dclass_delete(self, actor, cid):
        """분류만 지운다: 그 분류 문서는 미분류가 된다(문서·기록 그대로). 하위 분류가 있으면 거부."""
        with self._tx() as c:
            u = self._user(c, actor)
            r = self._class_of(c, cid)
            if not self._is_manager_of(c, u, r["dept"]):
                raise Forbidden("부서 분류는 그 부서 부서장 또는 관리자만 관리합니다")
            if c.execute("select 1 from dept_classes where parent=?", (r["id"],)).fetchone():
                raise Invalid("하위 분류를 먼저 지우세요")
            docs = [x["id"] for x in c.execute("select id from docs where dclass=?", (r["id"],))]
            c.execute("update docs set dclass=null where dclass=?", (r["id"],))
            c.execute("delete from dept_classes where id=?", (r["id"],))
            self._audit(c, actor, "dclass_delete", None, {"dept": r["dept"], "id": r["id"], "name": r["name"],
                                                           "unclassified_docs": docs})

    # ------------------------------------------------------------ 양식 기본 분류
    def _form_class_name(self, c, code, doc_type=None):
        r = c.execute("select dclass from forms where code=?", (code,)).fetchone() if code else None
        if r and r["dclass"]:
            return r["dclass"]
        if code in BUILTIN_CLASS:
            return BUILTIN_CLASS[code]
        return TYPE_CLASS.get(doc_type or "general", "일반")

    def form_dclass(self, actor, code):
        with self._ro() as c:
            self._user(c, actor)
            d = self._form_def(c, code)
            r = c.execute("select dclass from forms where code=?", (code,)).fetchone()
            return {"code": code, "set": r["dclass"] or "", "effective": self._form_class_name(c, code, d["doc_type"])}

    def form_dclass_set(self, actor, code, name):
        """양식의 기본 부서 분류(이름 — 기안자 부서의 같은 이름 분류로 연결, 없으면 일반/미분류). 비우면 자동."""
        self._require_admin(actor)
        name = _name(name, "분류") if str(name or "").strip() else None
        with self._tx() as c:
            self._form_def(c, code)
            before = c.execute("select dclass from forms where code=?", (code,)).fetchone()["dclass"]
            c.execute("update forms set dclass=? where code=?", (name, code))
            self._audit(c, actor, "form_dclass", None, {"code": code, "before": before, "after": name})
        return {"code": code, "dclass": name}

    def _default_class(self, c, dept, form_code, doc_type):
        for nm in (self._form_class_name(c, form_code, doc_type), "일반"):
            r = c.execute("select id from dept_classes where dept=? and name=?", (dept, nm)).fetchone()
            if r:
                return r["id"]
        return None

    # ------------------------------------------------------------ 사업(프로젝트)
    def _project(self, c, pid):
        r = c.execute("select * from projects where id=?", (int(pid),)).fetchone()
        if not r:
            raise NotFound("사업 없음")
        return r

    def _project_dict(self, c, r, u=None):
        d = dict(r)
        d["status_ko"] = PROJECT_STATUS.get(d["status"], d["status"])
        mem = c.execute("select kind, ref from project_members where project_id=? order by kind, ref", (r["id"],)).fetchall()
        d["depts"] = [x["ref"] for x in mem if x["kind"] == "dept"]
        d["users"] = [x["ref"] for x in mem if x["kind"] == "user"]
        if u is not None:
            d["can_manage"] = u["role"] == "admin" or r["created_by"] == u["username"]
            d["participant"] = self._participates(c, u, r["id"])
        return d

    def _project_access(self, c, u, r):
        if not (u["role"] == "admin" or self._participates(c, u, r["id"])):
            raise NotFound("사업이 없거나 참여하지 않았습니다")

    def _project_members(self, c, depts, users):
        ds, us = [], []
        for x in depts or []:
            x = str(x).strip().upper()
            if not x:
                continue
            if not c.execute("select 1 from depts where code=?", (x,)).fetchone():
                raise Invalid(f"없는 부서 코드: {x}")
            if x not in ds:
                ds.append(x)
        for x in users or []:
            x = str(x).strip().lower()
            if not x:
                continue
            if not c.execute("select 1 from users where username=?", (x,)).fetchone():
                raise Invalid(f"없는 사용자: {x}")
            if x not in us:
                us.append(x)
        if len(ds) + len(us) > 500:
            raise Invalid("참여자가 너무 많습니다")
        return ds, us

    def _project_code(self, c, code, pid=None):
        code = str(code or "").strip()
        if not code:
            return None
        if not PCODE_RE.match(code):
            raise Invalid("사업 코드는 영문·숫자·_.- 20자 이내")
        r = c.execute("select id from projects where code=?", (code,)).fetchone()
        if r and r["id"] != pid:
            raise Invalid(f"이미 있는 사업 코드: {code}")
        return code

    def project_create(self, actor, name, code=None, depts=(), users=()):
        name = _name(name, "사업", 60)
        with self._tx() as c:
            u = self._user(c, actor)
            if not (u["role"] == "admin" or self._is_head(c, u)):
                raise Forbidden("사업 문서함은 관리자 또는 부서장급이 만듭니다")
            code = self._project_code(c, code)
            ds, us = self._project_members(c, depts, users)
            ts = now_iso()
            pid = c.execute("insert into projects(name,code,status,created_by,created,updated) values(?,?,'open',?,?,?)",
                            (name, code, actor, ts, ts)).lastrowid
            for x in ds:
                c.execute("insert into project_members values(?,'dept',?)", (pid, x))
            for x in us:
                c.execute("insert into project_members values(?,'user',?)", (pid, x))
            self._audit(c, actor, "project_create", None, {"id": pid, "name": name, "code": code, "depts": ds, "users": us})
            return self._project_dict(c, self._project(c, pid), u)

    def project_update(self, actor, pid, name=None, code=KEEP, depts=None, users=None, status=None):
        with self._tx() as c:
            u = self._user(c, actor)
            r = self._project(c, pid)
            if not (u["role"] == "admin" or r["created_by"] == actor):
                raise Forbidden("사업 문서함은 만든 사람 또는 관리자만 관리합니다")
            before = self._project_dict(c, r)
            nm = _name(name, "사업", 60) if name is not None else r["name"]
            cd = self._project_code(c, code, r["id"]) if code is not KEEP else r["code"]
            st = r["status"]
            if status is not None:
                if status not in PROJECT_STATUS:
                    raise Invalid("상태는 open(진행) 또는 closed(종료)")
                st = status
            c.execute("update projects set name=?, code=?, status=?, updated=? where id=?", (nm, cd, st, now_iso(), r["id"]))
            if depts is not None or users is not None:
                ds, us = self._project_members(c, before["depts"] if depts is None else depts,
                                               before["users"] if users is None else users)
                c.execute("delete from project_members where project_id=?", (r["id"],))
                for x in ds:
                    c.execute("insert into project_members values(?,'dept',?)", (r["id"], x))
                for x in us:
                    c.execute("insert into project_members values(?,'user',?)", (r["id"], x))
            after = self._project_dict(c, self._project(c, r["id"]), u)
            ch = {k: [before[k], after[k]] for k in ("name", "code", "status", "depts", "users") if before[k] != after[k]}
            if ch:
                self._audit(c, actor, "project_update", None, {"id": r["id"], "changes": ch})
            return after

    def project_list(self, actor, status=None, all_=False):
        """내가 참여(또는 만든) 사업. all_=True 는 관리자 전체."""
        with self._ro() as c:
            u = self._user(c, actor)
            if all_ and u["role"] != "admin":
                raise Forbidden("관리자만")
            out = []
            for r in c.execute("select * from projects order by status desc, name").fetchall():
                if status and r["status"] != status:
                    continue
                if not all_ and not self._participates(c, u, r["id"]):
                    continue
                out.append(self._project_dict(c, r, u))
            return out

    def project_get(self, actor, pid):
        with self._ro() as c:
            u = self._user(c, actor)
            r = self._project(c, pid)
            self._project_access(c, u, r)
            return self._project_dict(c, r, u)

    def project_docs(self, actor, pid):
        """사업 문서함: 태그된 문서 중 원래 볼 수 있는 문서만(태그는 권한을 넓히지 않음)."""
        with self._ro() as c:
            u = self._user(c, actor)
            r = self._project(c, pid)
            self._project_access(c, u, r)
            args = self._vis_args(c, actor)
            args["pid"] = r["id"]
            rows = self._rows(c, f"d.id in (select doc_id from doc_projects where project_id=:pid) and {self._VIS}", args)
            return {"project": self._project_dict(c, r, u), "docs": rows}

    # ------------------------------------------------------------ 부서 문서함(분류 거르기)
    def dept_docs(self, actor, dclass=None):
        """부서 문서함(기존 규칙: 완료 + 부서 공개, 부서장급은 전체) — dclass=분류 id(하위 포함) 또는 'none'(미분류)."""
        with self._ro() as c:
            u = self._user(c, actor)
            args = self._vis_args(c, actor)
            w = "d.status='done' and d.dept=:dept and (d.visibility='dept' or :is_head=1)"
            if dclass == "none":
                w += " and d.dclass is null"
            elif dclass not in (None, ""):
                r = self._class_of(c, dclass)
                if r["dept"] != u["dept"]:
                    raise NotFound("분류 없음")
                ids, todo = [r["id"]], [r["id"]]
                while todo:
                    p = todo.pop()
                    for x in c.execute("select id from dept_classes where parent=?", (p,)):
                        if x["id"] not in ids:
                            ids.append(x["id"])
                            todo.append(x["id"])
                w += f" and d.dclass in ({','.join(str(int(i)) for i in ids)})"
            return self._rows(c, f"{w} and {self._VIS}", args)

    # ------------------------------------------------------------ 문서의 태그
    def _tags(self, c, actor, d):
        u = self._user(c, actor)
        cl = c.execute("select id,name from dept_classes where id=?", (d["dclass"],)).fetchone() if d["dclass"] else None
        projs = [dict(r) for r in c.execute(
            "select p.id, p.name, p.code, p.status from doc_projects x join projects p on p.id=x.project_id "
            "where x.doc_id=? order by p.name", (d["id"],))]
        for p in projs:
            p["can_open"] = u["role"] == "admin" or self._participates(c, u, p["id"])
        folders = [dict(r) for r in c.execute(
            "select f.id, f.name from folder_docs x join folders f on f.id=x.folder_id where x.doc_id=? and f.owner=? "
            "order by f.name", (d["id"], actor))]
        return {"dclass": dict(cl) if cl else None, "projects": projs, "folders": folders,
                "can_reclass": self._can_reclass(c, u, d)}

    def doc_tags(self, actor, doc_id, options=False):
        """문서의 분류·사업·내 폴더(+ options=True 면 고를 수 있는 목록)."""
        with self._ro() as c:
            d = self._check_view(c, actor, int(doc_id))
            t = self._tags(c, actor, d)
            if options:
                u = self._user(c, actor)
                t["class_options"] = self._class_rows(c, d["dept"]) if t["can_reclass"] else []
                t["project_options"] = [self._project_dict(c, r) for r in self._open_projects(c, u)] \
                    if t["can_reclass"] else []
                t["folder_options"] = [dict(r) for r in c.execute("select id, name from folders where owner=? order by name",
                                                                  (actor,))]
            return t

    def _open_projects(self, c, u):
        rows = c.execute("select * from projects where status='open' order by name").fetchall()
        return [r for r in rows if self._participates(c, u, r["id"])]

    def draft_options(self, actor, form=None, doc_type="general"):
        """기안 화면에 고를 거리: 내 부서 분류(+양식 기본값), 참여 중인 진행 사업, 내 폴더."""
        with self._ro() as c:
            u = self._user(c, actor)
            return {"classes": self._class_rows(c, u["dept"]),
                    "default_class": self._default_class(c, u["dept"], form, doc_type),
                    "projects": [self._project_dict(c, r) for r in self._open_projects(c, u)],
                    "folders": [dict(r) for r in c.execute("select id, name from folders where owner=? order by name",
                                                           (actor,))]}

    def _check_class(self, c, dept, cid):
        if cid in (None, "", 0, "0"):
            return None
        r = self._class_of(c, cid)
        if r["dept"] != dept:
            raise Invalid("문서 부서의 분류만 고를 수 있습니다")
        return r["id"]

    def _check_projects(self, c, u, pids, keep=()):
        out = []
        for pid in _ids(pids):
            if pid in keep:
                out.append(pid)
                continue
            r = self._project(c, pid)
            if r["status"] != "open":
                raise Invalid(f"종료된 사업에는 넣을 수 없습니다: {r['name']}")
            if not (u["role"] == "admin" or self._participates(c, u, pid)):
                raise Forbidden(f"참여하지 않은 사업입니다: {r['name']}")
            out.append(pid)
        return out

    def _set_tags_c(self, c, actor, did, dclass=KEEP, projects=None, initial=False):
        """분류·사업 태그 쓰기(권한 확인 뒤). 바뀐 것만 감사로그(이전 → 이후)."""
        d = c.execute("select * from docs where id=?", (did,)).fetchone()
        u = self._user(c, actor)
        ch = {}
        if dclass is not KEEP:
            new = self._check_class(c, d["dept"], dclass)
            if new != d["dclass"]:
                name = (lambda i: (c.execute("select name from dept_classes where id=?", (i,)).fetchone() or
                                   {"name": None})["name"] if i else None)
                ch["dclass"] = [name(d["dclass"]), name(new)]
                c.execute("update docs set dclass=? where id=?", (new, did))
        if projects is not None:
            cur = [r["project_id"] for r in c.execute("select project_id from doc_projects where doc_id=?", (did,))]
            new = self._check_projects(c, u, projects, keep=cur)
            if set(new) != set(cur):
                c.execute("delete from doc_projects where doc_id=?", (did,))
                for pid in new:
                    c.execute("insert into doc_projects(doc_id,project_id,added) values(?,?,?)", (did, pid, now_iso()))
                nm = {r["id"]: r["name"] for r in c.execute("select id, name from projects")}
                ch["projects"] = [sorted(nm.get(i, str(i)) for i in cur), sorted(nm.get(i, str(i)) for i in new)]
        if ch and not initial:
            self._audit(c, actor, "reclass", did, ch)
        return ch

    def doc_reclass(self, actor, doc_id, dclass=KEEP, projects=None):
        """분류(dclass=id 또는 None=미분류)·사업(projects=id 목록, 전체 교체) 변경."""
        with self._tx() as c:
            d = self._check_view(c, actor, int(doc_id))
            if not self._can_reclass(c, self._user(c, actor), d):
                raise Forbidden("분류를 바꿀 권한이 없습니다(기안 중엔 기안자, 그 뒤엔 부서장·문서관리 담당)")
            return self._set_tags_c(c, actor, d["id"], dclass, projects)

    def doc_set_folders(self, actor, doc_id, folder_ids):
        """이 문서가 들어 있을 내 폴더(전체 교체). 볼 수 있는 문서만."""
        with self._tx() as c:
            self._check_view(c, actor, int(doc_id))
            mine = {r["id"] for r in c.execute("select id from folders where owner=?", (actor,))}
            want = set(_ids(folder_ids))
            if want - mine:
                raise NotFound("폴더 없음")
            for fid in mine:
                if fid in want:
                    c.execute("insert or ignore into folder_docs(folder_id,doc_id,added) values(?,?,?)",
                              (fid, int(doc_id), now_iso()))
                else:
                    c.execute("delete from folder_docs where folder_id=? and doc_id=?", (fid, int(doc_id)))

    # ------------------------------------------------------------ 문서관리 담당
    def set_doc_manager(self, actor, ident, on=True):
        self._require_admin(actor)
        with self._tx() as c:
            u = self._emp(c, ident)
            if bool(u["doc_manager"]) == bool(on):
                return {"username": u["username"], "doc_manager": bool(on)}
            c.execute("update users set doc_manager=? where username=?", (1 if on else 0, u["username"]))
            self._hist_emp(c, actor, u["username"], "doc_manager", bool(u["doc_manager"]), bool(on))
            self._audit(c, actor, "doc_manager", None, {"username": u["username"], "before": bool(u["doc_manager"]),
                                                         "after": bool(on)})
        return {"username": u["username"], "doc_manager": bool(on)}

    # ------------------------------------------------------------ 이 문서로 기안
    def redraft_prefill(self, actor, src_id):
        """원문(볼 수 있는 문서)으로 새 기안 화면 값을 만든다(저장하지 않음). 양식은 현재 버전, 같은 이름 항목만,
        결재선은 지금 규정으로 다시 계산해 허용되는 직접 지정만 남김."""
        src_id = int(src_id)
        with self._ro() as c:
            self._check_view(c, actor, src_id)
        meta, body = self._read_doc(src_id)
        with self._ro() as c:
            u = self._user(c, actor)
            out = {"title": meta["title"], "visibility": meta.get("visibility", "private"),
                   "source_id": src_id, "source_number": meta.get("number"),
                   "source_attachments": len(meta.get("attachments") or [])}
            fd = None
            if meta.get("form"):
                code = meta["form"]["code"]
                fd = self._form_def(c, code)
                if not fd["enabled"]:
                    raise Forbidden(f"원문 양식({fd['name']})이 사용 중지되어 이 문서로 기안할 수 없습니다")
                if not self._form_allowed(c, u, fd):
                    raise Forbidden(f"원문 양식({fd['name']})은 우리 부서에서 쓸 수 없어 이 문서로 기안할 수 없습니다")
                keys = {f["key"] for f in fd["fields"]}
                out["form"] = {"code": code, "version": fd["version"]}
                out["fields"] = {k: v for k, v in (meta.get("fields") or {}).items() if k in keys}
                out["doc_type"] = fd["doc_type"]
            else:
                if not (self._setting(c, "allow_freeform") or
                        not c.execute("select 1 from forms where enabled=1 limit 1").fetchone()):
                    raise Invalid("원문이 양식 없는 문서이고 자유 기안이 꺼져 있어 이 문서로 기안할 수 없습니다")
                out["doc_type"] = meta.get("doc_type") or "general"
                out["amount"] = meta.get("amount")
            out["body"] = body
            if meta.get("doc_sha"):
                html = self._read_html(src_id)
                if html:
                    out["doc_html"] = self._clean_doc_html(html)
                    if meta.get("doc_page"):
                        out["doc_page"] = meta["doc_page"]
            self._line_inputs(meta)
            out["line_input"], out["cc_input"] = self._redraft_line(c, u, out["doc_type"], out.get("amount"),
                                                                    meta["line_input"], meta["cc_input"])
            # 분류: 같은 부서면 원문 분류, 아니면 양식 기본값 / 사업: 내가 넣을 수 있는(진행·참여) 것만
            src = c.execute("select dept, dclass from docs where id=?", (src_id,)).fetchone()
            out["dclass"] = src["dclass"] if src["dept"] == u["dept"] and src["dclass"] else \
                self._default_class(c, u["dept"], out.get("form", {}).get("code"), out["doc_type"])
            ok = {r["id"] for r in self._open_projects(c, u)}
            out["projects"] = [r["project_id"] for r in c.execute("select project_id from doc_projects where doc_id=?",
                                                                  (src_id,)) if r["project_id"] in ok]
            out["folders"] = [r["folder_id"] for r in c.execute(
                "select x.folder_id from folder_docs x join folders f on f.id=x.folder_id where x.doc_id=? and f.owner=?",
                (src_id, actor))]
            return out

    def _redraft_line(self, c, u, doc_type, amount, line_input, cc_input):
        """원문의 직접 지정 결재자·참조 중 지금 규정이 허용하는 것만(나·퇴사자 빼고, 안 되면 규정 결과만)."""
        me = u["username"]

        def ok_user(x):
            x = x.rstrip("!").strip().lower()
            return x and x != me and c.execute("select 1 from users where username=? and active=1", (x,)).fetchone()
        line = [x for x in line_input or [] if ok_user(x)]
        cc = [x for x in cc_input or [] if ok_user(x) and x.strip().lower() not in [y.rstrip("!").lower() for y in line]]
        for cand in (line, [x.rstrip("!") for x in line], []):
            try:
                self._resolve_line(c, u, doc_type, amount, cand, cc)
                return cand, cc
            except (Invalid, NotFound):
                continue
        return line, cc  # 규정 없는 부서에서 남은 결재자로도 안 되면 그대로 보여 주고 기안자가 고침
