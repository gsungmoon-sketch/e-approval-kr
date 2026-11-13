"""양식(서식) 관리 — 버전 관리되는 양식 정의, 부서별 사용 권한, 즐겨찾기, 문서번호 형식."""
from __future__ import annotations

import json
import re

from . import formdef as fdm
from .core_base import Forbidden, Invalid, NotFound, now_iso

FORMS_SCHEMA = """
create table if not exists forms(code text primary key, version int not null, enabled int not null default 1,
  builtin int not null default 0, sort int not null default 100, updated text);
create table if not exists form_versions(code text not null, version int not null, def text not null, created text,
  actor text, primary key(code, version));
create table if not exists form_fav(username text not null, code text not null, primary key(username, code));
"""

NUMBER_DEFAULT = "{dept}-{year}-{seq}"
NUMBER_TOKENS = ("dept", "year", "yy", "prefix", "seq")


def check_number_format(s):
    s = str(s or "").strip()
    if not s or len(s) > 40 or s.count("{seq}") != 1 or ("{year}" not in s and "{yy}" not in s):
        raise Invalid("문서번호 형식은 {seq} 1개와 {year} 또는 {yy} 를 포함해 40자 이내")
    rest = re.sub(r"\{(" + "|".join(NUMBER_TOKENS) + r")\}", "", s)
    if not re.fullmatch(r"[A-Za-z0-9가-힣_./-]*", rest):
        raise Invalid("문서번호 형식: 쓸 수 있는 칸은 {dept} {year} {yy} {prefix} {seq}, 기호는 - _ . / 만")
    return s


def _like(s):
    return s.replace("!", "!!").replace("%", "!%").replace("_", "!_")


class FormsMixin:
    def _forms_init(self, c):
        c.executescript(FORMS_SCHEMA)
        cols = {r["name"] for r in c.execute("pragma table_info(docs)")}
        if "form" not in cols:
            c.execute("alter table docs add column form text")
        if not c.execute("select count(*) from forms").fetchone()[0]:
            ts = now_iso()
            for i, (code, d) in enumerate(fdm.builtin_defs()):
                c.execute("insert into forms(code,version,enabled,builtin,sort,updated) values(?,1,1,1,?,?)",
                          (code, (i + 1) * 10, ts))
                c.execute("insert into form_versions(code,version,def,created,actor) values(?,1,?,?,'system')",
                          (code, json.dumps(d, ensure_ascii=False), ts))

    # ------------------------------------------------------------ 조회
    def _form_def(self, c, code, version=None):
        r = c.execute("select * from forms where code=?", (code,)).fetchone()
        if not r:
            raise NotFound(f"양식 없음: {code}")
        v = c.execute("select def from form_versions where code=? and version=?",
                      (code, version or r["version"])).fetchone()
        if not v:
            raise NotFound(f"양식 버전 없음: {code} v{version}")
        d = json.loads(v["def"])
        d.update(code=code, version=version or r["version"], enabled=bool(r["enabled"]), builtin=bool(r["builtin"]))
        return d

    def _form_allowed(self, c, u, d):
        return u["role"] == "admin" or not d["depts"] or u["dept"] in d["depts"]

    def forms_list(self, actor, all_=False):
        """기안자가 쓸 수 있는 양식(all_=True 이고 관리자면 사용 중지 포함 전부)."""
        with self._ro() as c:
            u = self._user(c, actor)
            if all_ and u["role"] != "admin":
                raise Forbidden("관리자만")
            fav = {r["code"] for r in c.execute("select code from form_fav where username=?", (actor,))}
            out = []
            for r in c.execute("select code from forms order by sort, code"):
                d = self._form_def(c, r["code"])
                if not all_ and (not d["enabled"] or not self._form_allowed(c, u, d)):
                    continue
                d["fav"] = d["code"] in fav
                out.append(d)
            return out

    def forms_recent(self, actor, n=5):
        with self._ro() as c:
            rows = c.execute("select form, max(id) m from docs where drafter=? and form is not null group by form "
                             "order by m desc limit ?", (actor, n)).fetchall()
        return [r["form"] for r in rows]

    def form_get(self, actor, code, version=None):
        """현재 쓸 수 있는 양식(관리자는 모든 양식·버전)."""
        with self._ro() as c:
            u = self._user(c, actor)
            if version and u["role"] != "admin":
                raise NotFound(f"양식 없음: {code}")
            d = self._form_def(c, code, version)
            if u["role"] != "admin" and (not d["enabled"] or not self._form_allowed(c, u, d)):
                raise NotFound(f"양식 없음: {code}")
            return d

    def form_for_doc(self, actor, doc_id):
        """문서를 볼 수 있는 사람에게 그 문서가 쓴 (기안 당시) 양식 정의."""
        m = self.show(actor, doc_id, mark_read=False)
        if not m.get("form"):
            return None
        with self._ro() as c:
            return self._form_def(c, m["form"]["code"], m["form"]["version"])

    def form_versions(self, actor, code):
        self._require_admin(actor)
        with self._ro() as c:
            return [dict(r) for r in c.execute("select version,created,actor from form_versions where code=? "
                                               "order by version desc", (code,))]

    def form_fav(self, actor, code, on=True):
        with self._tx() as c:
            d = self._form_def(c, code)
            if on and (not d["enabled"] or not self._form_allowed(c, self._user(c, actor), d)):
                raise NotFound(f"양식 없음: {code}")
            if on:
                c.execute("insert or ignore into form_fav values(?,?)", (actor, code))
            else:
                c.execute("delete from form_fav where username=? and code=?", (actor, code))

    # ------------------------------------------------------------ 관리
    def form_save(self, actor, code, d, new=False):
        """새 양식(new=True) 또는 새 버전 저장. 기존 문서는 기안 당시 버전을 그대로 유지."""
        self._require_admin(actor)
        code = str(code or "").strip().lower()
        if not fdm.CODE_RE.match(code):
            raise Invalid("양식 코드는 영소문자로 시작, 영소문자·숫자·_- 2~24자")
        d = fdm.check_def(d)
        with self._tx() as c:
            for k in ("depts", "cc_depts"):
                for x in d[k]:
                    if not c.execute("select 1 from depts where code=?", (x,)).fetchone():
                        raise Invalid(f"없는 부서 코드: {x}")
            r = c.execute("select * from forms where code=?", (code,)).fetchone()
            if new and r:
                raise Invalid(f"이미 있는 양식 코드: {code}")
            if not new and not r:
                raise NotFound(f"양식 없음: {code}")
            ts = now_iso()
            if r:
                cur = json.loads(c.execute("select def from form_versions where code=? and version=?",
                                           (code, r["version"])).fetchone()["def"])
                if cur == d:
                    return {"code": code, "version": r["version"], "changed": False}
                ver = r["version"] + 1
                c.execute("update forms set version=?, updated=? where code=?", (ver, ts, code))
            else:
                ver = 1
                srt = c.execute("select coalesce(max(sort),0)+10 from forms").fetchone()[0]
                c.execute("insert into forms(code,version,enabled,builtin,sort,updated) values(?,1,1,0,?,?)",
                          (code, srt, ts))
            c.execute("insert into form_versions(code,version,def,created,actor) values(?,?,?,?,?)",
                      (code, ver, json.dumps(d, ensure_ascii=False), ts, actor))
            self._audit(c, actor, "form_save", None, {"code": code, "version": ver, "name": d["name"]})
        return {"code": code, "version": ver, "changed": True}

    def form_copy(self, actor, src, code, name=None):
        self._require_admin(actor)
        with self._ro() as c:
            d = self._form_def(c, src)
        for k in ("code", "version", "enabled", "builtin"):
            d.pop(k, None)
        d["name"] = name or (d["name"] + " (사본)")[:40]
        return self.form_save(actor, code, d, new=True)

    def form_enable(self, actor, code, on=True):
        self._require_admin(actor)
        with self._tx() as c:
            self._form_def(c, code)
            c.execute("update forms set enabled=?, updated=? where code=?", (1 if on else 0, now_iso(), code))
            self._audit(c, actor, "form_enable" if on else "form_disable", None, {"code": code})

    # ------------------------------------------------------------ 문서에 적용
    def _apply_form(self, c, u, meta, values, strict, n_files):
        """meta['form'] 기준(기안 당시 버전) 값 검증 → meta 의 fields/doc_type/amount 갱신."""
        f = meta["form"]
        d = self._form_def(c, f["code"], f["version"])
        vals, amt = fdm.normalize(d, values, n_files, strict)
        meta["fields"] = vals
        meta["doc_type"] = d["doc_type"]
        if d.get("amount"):
            meta["amount"] = amt
        if strict and d["doc_type"] == "expense" and meta.get("amount") is None:
            raise Invalid("지출 문서는 금액이 필요합니다")
        return d

    def _require_form(self, c, meta_form):
        """양식 없는 기안은 설정(allow_freeform)이 켜져 있거나 쓸 수 있는 양식이 하나도 없을 때만."""
        if meta_form or self._setting(c, "allow_freeform"):
            return
        if c.execute("select 1 from forms where enabled=1 limit 1").fetchone():
            raise Invalid("양식을 골라 기안하세요(자유 기안은 관리자 설정에서 꺼져 있음)")

    def freeform_allowed(self):
        with self._ro() as c:
            return bool(self._setting(c, "allow_freeform")) or \
                not c.execute("select 1 from forms where enabled=1 limit 1").fetchone()

    def _start_form(self, c, u, code):
        d = self._form_def(c, code)
        if not d["enabled"] or not self._form_allowed(c, u, d):
            raise Forbidden(f"이 부서에서 쓸 수 없는 양식입니다: {d['name']}")
        return {"code": code, "version": d["version"], "name": d["name"], "category": d["category"],
                "retention": d["retention"], "prefix": d["prefix"], "cc_depts": d["cc_depts"]}

    def _form_cc(self, c, meta, steps, ccs, drafter):
        """양식의 필수 참조 부서 → 그 부서장들을 참조에 추가(결재선·기안자·중복 제외)."""
        line = {s["user"] for s in steps}
        for dept in (meta.get("form") or {}).get("cc_depts") or []:
            for x in self._dept_heads(c, dept):
                if x != drafter and x not in line and x not in ccs:
                    ccs.append(x)
        return ccs

    def _doc_number(self, c, meta, year):
        fmt = self._setting(c, "doc_number") or NUMBER_DEFAULT
        prefix = (meta.get("form") or {}).get("prefix") or meta["dept"]
        tok = {"dept": meta["dept"], "year": str(year), "yy": str(year)[2:], "prefix": prefix}
        base = fmt
        for k, v in tok.items():
            base = base.replace("{" + k + "}", v)
        key = meta["dept"] if fmt == NUMBER_DEFAULT else "#" + base  # 기본 형식은 기존 일련번호 그대로
        if not c.execute("select 1 from serials where dept=? and year=?", (key, year)).fetchone():
            # 새 카운터: 같은 모양으로 이미 발급된 번호의 최댓값부터(형식 변경 시 충돌 방지)
            pre, _, post = base.partition("{seq}")
            top = 0
            for r in c.execute("select number from docs where number like ? escape '!'",
                               (_like(pre) + "%" + _like(post),)):
                mid = r["number"][len(pre):len(r["number"]) - len(post) if post else None]
                if mid.isdigit():
                    top = max(top, int(mid))
            c.execute("insert into serials(dept,year,last) values(?,?,?)", (key, year, top))
        for _ in range(1000):
            n = self._next_serial(c, key, year)
            num = base.replace("{seq}", f"{n:04d}")
            if not c.execute("select 1 from docs where number=?", (num,)).fetchone():
                return num
        raise Invalid("문서번호를 만들 수 없습니다(형식 확인)")
