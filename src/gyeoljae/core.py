"""gyeoljae core: 전자결재 도메인 로직. CLI와 웹이 모두 이 모듈만 호출한다.

- 순수 Python 3 표준 라이브러리. OS 의존 호출(fcntl, chmod 의존, pwd/grp) 없음.
- 동시성: 모든 쓰기 작업은 SQLite `BEGIN IMMEDIATE` 트랜잭션 하나 안에서 실행된다.
  SQLite 쓰기 잠금이 프로세스/OS 무관 전역 잠금 역할을 하므로 문서번호·감사로그 체인이 직렬화된다.
- 문서 = 데이터 디렉터리 docs/<id>/ (meta.json, body.md, files/). SQLite는 색인·권한·쪽지·게시판.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import sqlite3
import sys
import time
import unicodedata
from contextlib import contextmanager
from pathlib import Path

from .core_base import *  # noqa: F401,F403
from .core_base import (_atomic_write, _rmtree, _try_chmod, _unlink, _valid_date)  # noqa: F401
from .org import OrgMixin
from .rules import RulesMixin
from .ops import OpsMixin, ServerConfMixin
from .home import HomeMixin
from .mail import MailMixin
from .forms import FormsMixin
from .filing import FilingMixin, KEEP as _KEEP
from . import policy as pol
from .doctemplate import extract_fields


SCHEMA = """
create table if not exists kv(k text primary key, v text);
create table if not exists audit_log(seq integer primary key, line text not null);
create table if not exists depts(code text primary key, name text not null, created text);
create table if not exists users(username text primary key, name text not null, dept text not null references depts(code),
  role text not null, title text, pwhash text, active int not null default 1, created text);
create table if not exists delegations(id integer primary key, from_user text not null, to_user text not null,
  start text not null, end text not null, created text, revoked int not null default 0);
create table if not exists docs(id integer primary key autoincrement, title text not null, drafter text not null,
  dept text not null, status text not null, created text, submitted text, completed text, number text unique,
  cur_step int, cur_user text, visibility text not null default 'private', updated text);
create index if not exists docs_status on docs(status, cur_user);
create table if not exists doc_party(doc_id int not null, username text not null, kind text not null,
  primary key(doc_id, username, kind));
create index if not exists party_user on doc_party(username);
create table if not exists doc_text(doc_id integer primary key, title text, body text);
create table if not exists serials(dept text not null, year int not null, last int not null, primary key(dept, year));
create table if not exists reads(doc_id int not null, username text not null, at text, primary key(doc_id, username));
create table if not exists messages(id integer primary key, sender text not null, recipient text not null,
  body text not null, doc_id int, created text, read_at text);
create index if not exists msg_rcpt on messages(recipient, read_at);
create table if not exists posts(id integer primary key, board text not null, author text not null,
  title text not null, body text not null, created text);
create table if not exists sessions(tokhash text primary key, username text not null, csrf text not null,
  created real, expires real, ip text);
create table if not exists login_fail(key text not null, at real not null);
create index if not exists lf_key on login_fail(key, at);
"""


class _Conn(sqlite3.Connection):
    """after: 커밋 성공 후 실행할 파일 작업(문서 쓰기·삭제·감사로그 반영)."""
    after: list
    audited: bool


def _retry_io(fn, *a):
    # Windows: 다른 스레드가 파일을 열고 있으면 os.replace/unlink 가 PermissionError → 짧게 재시도
    for i in range(20):
        try:
            return fn(*a)
        except PermissionError:
            if i == 19:
                raise
            time.sleep(0.05 * (i + 1))


class App(OrgMixin, RulesMixin, OpsMixin, HomeMixin, MailMixin, FormsMixin, FilingMixin, ServerConfMixin):
    def __init__(self, data_dir=None):
        self.dir = Path(data_dir) if data_dir else default_data_dir()
        self.dir.mkdir(parents=True, exist_ok=True)
        _try_chmod(self.dir, 0o700)
        self.docs_dir = self.dir / "docs"
        self.docs_dir.mkdir(exist_ok=True)
        self.db_path = self.dir / "gyeoljae.db"
        self.audit_path = self.dir / "audit.log"
        c = self._connect()
        try:
            c.executescript(SCHEMA)
            try:
                c.execute("pragma journal_mode=wal")
            except sqlite3.DatabaseError:
                pass
            self.fts = self._init_fts(c)
            self._migrate(c)
            from .home import HOME_SCHEMA
            from .mail import MAIL_SCHEMA
            c.executescript(HOME_SCHEMA)
            c.executescript(MAIL_SCHEMA)
            self._forms_init(c)
            self._filing_init(c)
            if not c.execute("select count(*) from audit_log").fetchone()[0] and self.audit_path.exists():
                with open(self.audit_path, encoding="utf-8") as f:  # v0.1 파일 전용 로그 이관
                    for ln in f:
                        if ln.strip():
                            c.execute("insert or ignore into audit_log(seq,line) values(?,?)",
                                      (json.loads(ln)["seq"], ln.rstrip("\n")))
        finally:
            c.close()

    # ------------------------------------------------------------ db plumbing
    def _connect(self):
        c = sqlite3.connect(str(self.db_path), timeout=60, isolation_level=None, factory=_Conn)
        c.after, c.audited = [], False
        c.row_factory = sqlite3.Row
        c.execute("pragma busy_timeout=60000")
        c.execute("pragma foreign_keys=on")
        return c

    def _init_fts(self, c) -> bool:
        try:
            c.execute("create virtual table if not exists doc_fts using fts5(title, body, tokenize='trigram')")
            c.execute("select count(*) from doc_fts").fetchone()
            return True
        except sqlite3.OperationalError:
            return False  # FTS5/trigram 미지원 SQLite → LIKE 검색 폴백

    @contextmanager
    def _ro(self):
        c = self._connect()
        try:
            yield c
        finally:
            c.close()

    @contextmanager
    def _tx(self):
        """쓰기 트랜잭션. BEGIN IMMEDIATE = 전역 쓰기 잠금(모든 OS, 다중 프로세스)."""
        c = self._connect()
        try:
            c.execute("begin immediate")
            try:
                yield c
                # 로직이 모두 끝난 뒤, 쓰기 잠금을 쥔 채로 파일 반영 → 커밋 (백업·다른 쓰기와 경합 없음).
                # 로직 중 예외면 여기까지 오지 않으므로 파일은 그대로다.
                for fn in c.after:
                    _retry_io(*fn)
                if c.audited:
                    self._flush_audit_c(c)
                c.execute("commit")
            except BaseException:
                c.execute("rollback")
                raise
        finally:
            c.close()

    # ------------------------------------------------------------ audit (hash chain)
    def _audit(self, c, actor, action, doc_id=None, detail=None):
        row = c.execute("select v from kv where k='audit_head'").fetchone()
        seq, prev = (json.loads(row["v"]) if row else [0, "0" * 64])
        entry = {"seq": seq + 1, "ts": now_iso(), "actor": actor, "action": action, "doc": doc_id,
                 "detail": detail or {}, "prev": prev}
        entry["hash"] = sha256(canon(entry))
        c.execute("insert into audit_log(seq,line) values(?,?)",
                  (entry["seq"], json.dumps(entry, ensure_ascii=False, sort_keys=True)))
        c.execute("insert or replace into kv(k,v) values('audit_head',?)", (json.dumps([entry["seq"], entry["hash"]]),))
        c.audited = True
        return entry

    def _file_last_seq(self):
        if not self.audit_path.exists():
            return 0
        with open(self.audit_path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 65536))
            tail = f.read().splitlines()
        for ln in reversed(tail):
            try:
                return int(json.loads(ln.decode("utf-8"))["seq"])
            except Exception:
                continue
        return 0

    def _flush_audit_c(self, c):
        """DB(정본) → audit.log(사본, append-only). 호출자가 쓰기 잠금(트랜잭션)을 쥐고 있어야 한다."""
        last = self._file_last_seq()
        rows = c.execute("select line from audit_log where seq>? order by seq", (last,)).fetchall()
        if rows:
            with open(self.audit_path, "a", encoding="utf-8", newline="\n") as f:
                f.write("".join(r["line"] + "\n" for r in rows))
                f.flush()
                os.fsync(f.fileno())

    def _flush_audit(self):
        with self._tx() as c:
            self._flush_audit_c(c)

    def audit_list(self, actor, doc_id=None, limit=200):
        self._require_admin(actor)
        with self._ro() as c:
            rows = [json.loads(r["line"]) for r in c.execute("select line from audit_log order by seq")]
        return [e for e in rows if doc_id is None or e.get("doc") == doc_id][-limit:]

    def verify_audit(self, actor=None):
        """감사로그 검증: DB 체인(정본) + audit.log 사본 일치 + DB 헤드 + 완료문서 내용 해시."""
        if actor is not None:
            self._require_admin(actor)
        errors, prev, seq, n = [], "0" * 64, 0, 0
        completes = {}
        with self._ro() as c:
            db_lines = [r["line"] for r in c.execute("select line from audit_log order by seq")]
        for i, ln in enumerate(db_lines, 1):
            try:
                e = json.loads(ln)
                h = e.pop("hash")
            except Exception:
                errors.append(f"DB #{i}: 파싱 실패")
                continue
            n += 1
            if e.get("seq") != seq + 1:
                errors.append(f"DB #{i}: seq 불연속 ({e.get('seq')} != {seq + 1})")
            if e.get("prev") != prev:
                errors.append(f"DB #{i}: prev 해시 불일치 (체인 단절)")
            if sha256(canon(e)) != h:
                errors.append(f"DB #{i}: 항목 해시 불일치 (내용 변조)")
            seq, prev = e.get("seq", seq + 1), h
            if e.get("action") == "complete":
                completes[e.get("doc")] = e.get("detail", {}).get("content_hash")
        file_lines = []
        if self.audit_path.exists():
            with open(self.audit_path, encoding="utf-8") as f:
                file_lines = [ln.rstrip("\n") for ln in f if ln.strip()]
        for i, ln in enumerate(file_lines, 1):
            if i > len(db_lines):
                errors.append(f"audit.log line {i}: DB에 없는 항목(추가 변조)")
                break
            if ln != db_lines[i - 1]:
                errors.append(f"audit.log line {i}: DB 정본과 불일치(변조)")
        if len(file_lines) < len(db_lines):
            errors.append(f"audit.log가 DB보다 {len(db_lines) - len(file_lines)}건 적음(삭제/잘림 또는 미반영)")
        with self._ro() as c:
            row = c.execute("select v from kv where k='audit_head'").fetchone()
            head = json.loads(row["v"]) if row else [0, "0" * 64]
            if head != [seq, prev]:
                errors.append(f"DB 헤드 불일치: db={head[0]}:{head[1][:12]} chain={seq}:{(prev or '')[:12]} (DB 행 삭제/추가 의심)")
            done = c.execute("select id from docs where status='done'").fetchall()
        for r in done:
            did = r["id"]
            if did not in completes:
                errors.append(f"문서 {did}: 완료 감사기록 없음")
                continue
            try:
                meta, body = self._read_doc(did)
                ch = self._content_hash(did, meta, body)
            except Exception as ex:
                errors.append(f"문서 {did}: 읽기 실패 {ex}")
                continue
            if ch != completes[did]:
                errors.append(f"문서 {did}: 완료 후 내용 변조 감지")
        return {"ok": not errors, "entries": n, "head": seq, "errors": errors}

    # ------------------------------------------------------------ users / depts
    def _user(self, c, username):
        r = c.execute("select * from users where username=? and active=1", (username,)).fetchone()
        if not r:
            raise NotFound(f"사용자 없음: {username}")
        return r

    def get_user(self, username):
        with self._ro() as c:
            r = self._user(c, username)
            d = dict(r)
            d.pop("pwhash", None)
            d["is_head"] = self._is_head(c, r)
            return d

    def _require_admin(self, actor):
        with self._ro() as c:
            if self._user(c, actor)["role"] != "admin":
                raise Forbidden("관리자 권한이 필요합니다")

    def has_users(self):
        with self._ro() as c:
            return c.execute("select count(*) from users").fetchone()[0] > 0

    def bootstrap(self, dept_code, dept_name, username, name, password):
        """최초 관리자 생성(사용자가 하나도 없을 때만)."""
        pw = hash_password(password)
        with self._tx() as c:
            if c.execute("select count(*) from users").fetchone()[0]:
                raise Forbidden("이미 초기화되었습니다")
            self._add_dept(c, dept_code, dept_name)
            c.execute("insert into users(username,name,dept,role,title,pwhash,created,emp_no,position,status,active) "
                      "values(?,?,?,?,?,?,?,?,?,?,1)", (username, name, dept_code, "admin", "관리자", pw, now_iso(),
                                                       self._next_emp_no(c, today()), None, "active"))
            self._audit(c, "-", "bootstrap", None, {"admin": username, "dept": dept_code})

    def add_user(self, actor, username, name, dept, role, password, title=""):
        """호환용 간이 등록(시험·시드): role head → 직위 부서장, 비밀번호 직접 지정(변경 강제 없음)."""
        self._require_admin(actor)
        with self._tx() as c:
            self._emp_create(c, actor, name, dept, "head" if role == "head" else "member", None, username,
                             title=title, admin=(role == "admin"), password=password)
        return self.get_user(username)

    def set_password(self, actor, username, new_password, old_password=None):
        with self._ro() as c:
            a = self._user(c, actor)
            u = c.execute("select * from users where username=?", (username,)).fetchone()
        if not u:
            raise NotFound("사용자 없음")
        if actor != username and u["role"] == "admin":
            with self._ro() as c:
                if self._setting(c, "two_person"):
                    raise Forbidden("2인 승인 중에는 다른 관리자의 비밀번호를 바꿀 수 없습니다")
        if actor == username:
            if not verify_password(old_password or "", u["pwhash"]):
                raise Forbidden("현재 비밀번호가 틀립니다")
        elif actor != username and a["role"] != "admin":
            raise Forbidden("권한 없음")
        pw = hash_password(new_password)
        with self._tx() as c:
            if actor == username and verify_password(new_password, u["pwhash"]):
                raise Invalid("이전과 다른 비밀번호를 쓰세요")
            c.execute("update users set pwhash=?, must_change=? where username=?",
                      (pw, 0 if actor == username else 1, username))
            c.execute("delete from sessions where username=?", (username,))
            self._audit(c, actor, "passwd", None, {"user": username})

    def set_active(self, actor, username, active: bool):
        return self.emp_set_status(actor, username, "active" if active else "retired")

    def list_users(self, dept=None):
        with self._ro() as c:
            q = "select username,name,dept,role,title,active,position,emp_no from users"
            args = ()
            if dept:
                q += " where dept=?"
                args = (dept,)
            return [dict(r) for r in c.execute(q + " order by dept, username", args)]

    # ------------------------------------------------------------ auth / sessions (web)
    def _fail_count(self, c, key):
        return c.execute("select count(*) from login_fail where key=? and at>?",
                         (key, time.time() - LOGIN_WINDOW)).fetchone()[0]

    def login(self, username, password, ip="-"):
        """성공 시 (token, csrf) 반환. 계정 5회·IP 20회 실패/10분 제한."""
        username = (username or "").strip().lower()[:40]
        keys = [f"ip:{ip}", f"user:{username}"]
        now = time.time()
        # 원자적: 한도 확인 + '시도' 행 선기록을 한 쓰기 트랜잭션에서 (병렬 요청 우회 차단)
        with self._tx() as c:
            c.execute("delete from login_fail where at<?", (now - LOGIN_WINDOW,))
            c.execute("delete from sessions where expires<?", (now,))
            if (self._fail_count(c, keys[0]) >= LOGIN_MAX_FAIL_IP
                    or self._fail_count(c, keys[1]) >= LOGIN_MAX_FAIL):
                limited = True
            else:
                limited = False
                rowids = [c.execute("insert into login_fail(key,at) values(?,?)", (k, now)).lastrowid for k in keys]
            u = c.execute("select * from users where username=?", (username,)).fetchone()
        if limited:
            raise RateLimited("로그인 시도가 너무 많습니다. 10분 후 다시 시도하세요")
        ok = verify_password(password or "", u["pwhash"] if u else None)
        if ok and not u["active"]:
            # 비밀번호를 아는 본인에게만 계정 상태를 알려 준다(모르는 사람에겐 일반 오류)
            with self._tx() as c:
                c.executemany("delete from login_fail where rowid=?", [(r,) for r in rowids])
                self._audit(c, username, "login_inactive", None, {"ip": ip})
            raise Forbidden("사용 중지된 계정입니다(휴직·퇴사) — 관리자에게 문의하세요")
        with self._tx() as c:
            if not ok:
                self._audit(c, username or "-", "login_fail", None, {"ip": ip})
                tok = None
            else:
                c.executemany("delete from login_fail where rowid=?", [(r,) for r in rowids])
                tok = self._new_session(c, username, ip)
        if tok is None:
            raise Forbidden("아이디 또는 비밀번호가 틀립니다")
        return tok

    def _new_session(self, c, username, ip):
        c.execute("delete from login_fail where key=?", (f"user:{username}",))
        tok, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        c.execute("insert into sessions(tokhash,username,csrf,created,expires,ip) values(?,?,?,?,?,?)",
                  (sha256(tok.encode()), username, csrf, time.time(), time.time() + SESSION_TTL, ip))
        self._audit(c, username, "login", None, {"ip": ip})
        return tok, csrf

    def session(self, token):
        if not token or len(token) > 100:
            return None
        with self._ro() as c:
            r = c.execute("select s.username, s.csrf, s.expires, u.must_change from sessions s join users u on u.username=s.username "
                          "where s.tokhash=? and u.active=1", (sha256(token.encode()),)).fetchone()
        if not r or r["expires"] < time.time():
            return None
        return {"username": r["username"], "csrf": r["csrf"], "must_change": bool(r["must_change"])}

    def check_password(self, username, password):
        with self._ro() as c:
            u = c.execute("select pwhash from users where username=? and active=1", (username,)).fetchone()
        return verify_password(password or "", u["pwhash"] if u else None)

    def logout(self, token):
        with self._tx() as c:
            c.execute("delete from sessions where tokhash=?", (sha256((token or "").encode()),))

    # ------------------------------------------------------------ delegation (대결)
    def delegate_set(self, actor, to_user, start=None, end=None, from_user=None):
        from_user = from_user or actor
        start, end = start or today(), end or today()
        if not (_valid_date(start) and _valid_date(end)) or start > end:
            raise Invalid("기간은 YYYY-MM-DD, 시작<=종료")
        with self._ro() as c:
            a = self._user(c, actor)
            if from_user != actor and a["role"] != "admin":
                raise Forbidden("본인 결재권만 위임할 수 있습니다")
            if from_user != actor and to_user == actor:
                raise Forbidden("관리자는 타인의 결재권을 자신에게 위임할 수 없습니다")
            fu = self._user(c, from_user)
            tu = self._user(c, to_user)
            if to_user == from_user:
                raise Invalid("자기 자신에게 위임할 수 없습니다")
            self._check_delegation_rules(c, fu, tu, start, end)
        with self._tx() as c:
            cur = c.execute("insert into delegations(from_user,to_user,start,end,created) values(?,?,?,?,?)",
                            (from_user, to_user, start, end, now_iso()))
            did = cur.lastrowid
            self._notify(c, to_user, f"[대결] {from_user} 님이 {start}~{end} 결재를 위임했습니다.")
            self._audit(c, actor, "delegate_set", None, {"id": did, "from": from_user, "to": to_user, "start": start, "end": end})
        return {"id": did, "from_user": from_user, "to_user": to_user, "start": start, "end": end}

    def delegate_list(self, actor):
        with self._ro() as c:
            a = self._user(c, actor)
            if a["role"] == "admin":
                rows = c.execute("select * from delegations order by id desc")
            else:
                rows = c.execute("select * from delegations where from_user=? or to_user=? order by id desc", (actor, actor))
            t = today()
            return [dict(r, active=bool(not r["revoked"] and r["start"] <= t <= r["end"])) for r in rows]

    def delegate_revoke(self, actor, did):
        with self._tx() as c:
            r = c.execute("select * from delegations where id=?", (did,)).fetchone()
            a = self._user(c, actor)
            if not r or (r["from_user"] != actor and a["role"] != "admin"):
                raise NotFound("위임 없음")
            c.execute("update delegations set revoked=1 where id=?", (did,))
            self._audit(c, actor, "delegate_revoke", None, {"id": did})

    def _delegators(self, c, user):
        t = today()
        return [r["from_user"] for r in c.execute(
            "select from_user from delegations where to_user=? and revoked=0 and start<=? and end>=?", (user, t, t))]

    # ------------------------------------------------------------ visibility
    _VIS = """(
      d.id in (select doc_id from doc_party where username=:u and kind='drafter')
      or (d.submitted is not null and d.id in (select doc_id from doc_party where username=:u))
      or (d.submitted is not null and :is_head=1 and d.dept=:dept)
      or (d.status='done' and d.visibility='dept' and d.dept=:dept)
      or (d.status='pending' and d.cur_user in (select from_user from delegations
            where to_user=:u and revoked=0 and start<=:t and end>=:t))
    )"""

    def _vis_args(self, c, actor):
        u = self._user(c, actor)
        return {"u": actor, "is_head": 1 if self._is_head(c, u) else 0, "dept": u["dept"], "t": today()}

    def _check_view(self, c, actor, doc_id):
        args = self._vis_args(c, actor)
        args["id"] = doc_id
        r = c.execute(f"select d.* from docs d where d.id=:id and {self._VIS}", args).fetchone()
        if not r:
            raise NotFound("문서가 없거나 열람 권한이 없습니다")  # 존재 여부도 숨김
        return r

    # ------------------------------------------------------------ documents
    def _doc_dir(self, doc_id) -> Path:
        return self.docs_dir / f"{int(doc_id):06d}"

    def _read_doc(self, doc_id):
        d = self._doc_dir(doc_id)
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        body = (d / "body.md").read_text(encoding="utf-8")
        return meta, body

    def _write_doc(self, c, meta, body=None):
        """meta.json/body.md 쓰기는 커밋 뒤로 미룬다(롤백 시 파일 불변)."""
        d = self._doc_dir(meta["id"])
        (d / "files").mkdir(parents=True, exist_ok=True)
        if body is not None:
            c.after.append((_atomic_write, d / "body.md", body.encode("utf-8")))
        c.after.append((_atomic_write, d / "meta.json", json.dumps(meta, ensure_ascii=False, indent=1).encode("utf-8")))

    def _read_html(self, doc_id):
        p = self._doc_dir(doc_id) / "doc.html"
        return p.read_text(encoding="utf-8") if p.exists() else ""

    @staticmethod
    def _clean_doc_html(html):
        """편집기 HTML → 정본(hanword sanitize_html → 우리 clean_html). 이 결과만 저장·표시·변환."""
        from .hanword import canonical_html
        try:
            return canonical_html(html)
        except ValueError as e:
            raise Invalid(str(e))

    @staticmethod
    def _doc_page(meta, page):
        """편집기 용지(page) → 검증해 meta['doc_page'] 에(None 이면 그대로)."""
        if page is None:
            return
        from .hanword import parse_page
        try:
            pg = parse_page(page)
        except ValueError as e:
            raise Invalid(str(e))
        if pg is not None:
            meta["doc_page"] = pg

    def _store_html(self, c, meta, fd, html):
        """편집기 문서: 기안자가 쓴 그대로(정화만) doc.html(커밋 뒤) 저장 — 서버가 값·합계를 다시 써 넣지 않음.
        반환 = 검색용 평문 본문."""
        from .doctemplate import html_text
        body = html_text(html)
        if len(body) > MAX_BODY:
            raise Invalid("본문이 너무 깁니다")
        data = html.encode("utf-8")
        meta["doc_sha"] = sha256(data)
        c.after.append((_atomic_write, self._doc_dir(meta["id"]) / "doc.html", data))
        return body

    def _content_hash(self, doc_id, meta, body):
        d = self._doc_dir(doc_id) / "files"
        atts = []
        for a in meta.get("attachments", []):
            p = d / a["stored"]
            atts.append([a["name"], sha256(p.read_bytes()) if p.exists() else "MISSING", a.get("sha256")])
        core = {"id": meta["id"], "title": meta["title"], "body": body, "number": meta.get("number"),
                "drafter": meta["drafter"], "attachments": atts, "cc": meta.get("cc"),
                "visibility": meta.get("visibility"), "dept": meta.get("dept"),
                "doc_type": meta.get("doc_type"), "amount": meta.get("amount"), "policy": meta.get("policy"),
                "line": [[s["user"], s["status"], s.get("acted_by"), s.get("proxy_for"), s.get("at"),
                          s.get("comment"), s.get("final")] for s in meta["line"]]}
        if meta.get("form"):  # 양식 문서만(기존 문서 해시 불변)
            core["form"] = [meta["form"]["code"], meta["form"]["version"]]
            core["fields"] = meta.get("fields")
        if meta.get("doc_sha"):  # 편집기 문서만(기존 문서 해시 불변)
            p = self._doc_dir(doc_id) / "doc.html"
            core["doc_html"] = sha256(p.read_bytes()) if p.exists() else "MISSING"
        if meta.get("doc_page"):  # 편집기 용지(있을 때만 — 기존 문서 해시 불변)
            core["doc_page"] = meta["doc_page"]
        return sha256(canon(core))

    def _validate_line(self, c, drafter, line, cc):
        if not line:
            raise Invalid("결재선이 비어 있습니다")
        if len(line) > MAX_LINE:
            raise Invalid(f"결재선은 최대 {MAX_LINE}명")
        steps, seen = [], set()
        for item in line:
            item = item.strip()
            final = item.endswith("!")
            user = item.rstrip("!").strip().lower()
            if user == drafter:
                raise Invalid("기안자는 결재선에 들어갈 수 없습니다")
            if user in seen:
                raise Invalid(f"결재선 중복: {user}")
            seen.add(user)
            u = self._user(c, user)
            if final and not (u["role"] == "admin" or self._is_head(c, u)):
                raise Invalid(f"전결(!)은 부서장/관리자에게만 지정할 수 있습니다: {user}")
            steps.append({"user": user, "name": u["name"], "title": u["title"], "final": final,
                          "status": "waiting", "acted_by": None, "proxy_for": None, "at": None, "comment": ""})
        if not any(self._user(c, st["user"])["role"] == "admin" or self._is_head(c, self._user(c, st["user"]))
                   for st in steps):
            raise Invalid("결재선에 부서장 또는 관리자가 한 명 이상 있어야 합니다")
        ccs = []
        for x in cc or []:
            x = x.strip().lower()
            if not x:
                continue
            if x in seen or x == drafter or x in ccs:
                raise Invalid(f"참조자 중복/결재선과 겹침: {x}")
            self._user(c, x)
            ccs.append(x)
        return steps, ccs

    def _check_text(self, title, body):
        title = re.sub(r"[\x00-\x1f\x7f]+", " ", title or "").strip()  # 제목은 한 줄(헤더 주입·표시 깨짐 방지)
        if not title or len(title) > MAX_TITLE:
            raise Invalid(f"제목 필요({MAX_TITLE}자 이하)")
        if body is None:
            body = ""
        if len(body) > MAX_BODY:
            raise Invalid("본문이 너무 깁니다")
        return title, body.replace("\r\n", "\n")

    def _store_attachments(self, doc_id, meta, attachments):
        d = self._doc_dir(doc_id) / "files"
        d.mkdir(parents=True, exist_ok=True)
        cur = meta.setdefault("attachments", [])
        total = sum(a["size"] for a in cur)
        names_lower = {a["name"].lower() for a in cur}
        for fname, data in attachments or []:
            if len(cur) >= MAX_FILES:
                raise Invalid(f"첨부는 최대 {MAX_FILES}개")
            if len(data) > MAX_FILE_BYTES:
                raise Invalid(f"첨부 1개 최대 {MAX_FILE_BYTES // 1048576}MB")
            total += len(data)
            if total > MAX_TOTAL_BYTES:
                raise Invalid(f"첨부 합계 최대 {MAX_TOTAL_BYTES // 1048576}MB")
            name = safe_filename(fname)
            base, ext = os.path.splitext(name)
            k = 2
            while name.lower() in names_lower:  # 대소문자 무시 FS(Windows/macOS) 충돌 회피
                name = f"{base}({k}){ext}"
                k += 1
            names_lower.add(name.lower())
            idx = max([a["idx"] for a in cur] + [meta.get("last_idx", 0), 0]) + 1
            meta["last_idx"] = idx  # 단조 증가: 삭제 예정 파일과 저장명 충돌 방지
            stored = f"{idx:02d}{ext}"  # 디스크 이름은 번호+확장자만 → 경로·예약어 문제 원천 차단
            target = (d / stored).resolve()
            if target.parent != d.resolve():
                raise Invalid("잘못된 경로")
            _atomic_write(target, data)
            cur.append({"idx": idx, "name": name, "stored": stored, "size": len(data), "sha256": sha256(data)})

    def _index(self, c, meta, body):
        did = meta["id"]
        c.execute("update docs set title=?, status=?, submitted=?, completed=?, number=?, cur_step=?, cur_user=?, "
                  "visibility=?, updated=?, doc_type=?, amount=? where id=?",
                  (meta["title"], meta["status"], meta.get("submitted"), meta.get("completed"), meta.get("number"),
                   meta.get("cur_step"), meta.get("cur_user"), meta["visibility"], now_iso(),
                   meta.get("doc_type", "general"), meta.get("amount"), did))
        c.execute("delete from doc_party where doc_id=? and kind in ('line','cc')", (did,))
        for s in meta["line"]:
            c.execute("insert or ignore into doc_party values(?,?,'line')", (did, s["user"]))
            if s.get("acted_by") and s["acted_by"] != s["user"]:
                c.execute("insert or ignore into doc_party values(?,?,'proxy')", (did, s["acted_by"]))
        for x in meta["cc"]:
            c.execute("insert or ignore into doc_party values(?,?,'cc')", (did, x))
        text = body + "\n" + " ".join(a["name"] for a in meta.get("attachments", []))
        c.execute("insert or replace into doc_text(doc_id,title,body) values(?,?,?)", (did, meta["title"], text))
        if self.fts:
            c.execute("delete from doc_fts where rowid=?", (did,))
            c.execute("insert into doc_fts(rowid,title,body) values(?,?,?)", (did, meta["title"], text))

    def _hist(self, meta, actor, action, comment=""):
        meta.setdefault("history", []).append({"at": now_iso(), "actor": actor, "action": action, "comment": comment})

    def _notify(self, c, to, text, doc_id=None):
        c.execute("insert into messages(sender,recipient,body,doc_id,created) values('system',?,?,?,?)",
                  (to, text, doc_id, now_iso()))
        if doc_id is not None:  # 결재 알림 → (설정 시) 외부 메일
            self._notify_external(c, to, text, doc_id)

    def _type_amount(self, doc_type, amount):
        try:
            t = pol.type_code(doc_type or "general")
            a = pol.parse_amount(amount)
        except pol.PolicyError as e:
            raise Invalid(str(e))
        if t == "expense" and a is None:
            raise Invalid("지출 문서는 금액이 필요합니다")
        return t, a

    def create_draft(self, actor, title, body, line=None, cc=(), visibility="private", attachments=(), submit=False,
                     doc_type="general", amount=None, form=None, fields=None, doc_html=None, doc_page=None,
                     dclass=None, projects=None, folders=None, source_id=None, copy_attachments=False):
        """line: 정책 없는 부서=전체 결재선('!'=전결), 정책 부서=추가 결재자(앞에 붙음).
        dclass: 부서 분류 id(None=양식 기본값, 'none'/0=미분류), projects: 사업 id 목록, folders: 내 폴더 id 목록.
        source_id: "이 문서로 기안"의 원문(볼 수 있어야 함) — copy_attachments 면 원문 첨부를 새 파일로 복사."""
        title, body = self._check_text(title, body)
        if visibility not in ("private", "dept"):
            raise Invalid("visibility는 private|dept")
        doc_type, amount = self._type_amount(doc_type, amount)
        line_input = [x.strip() for x in (line or []) if x.strip()]
        cc_input = [x.strip().lower() for x in (cc or []) if x.strip()]
        with self._tx() as c:
            u = self._user(c, actor)
            fmeta = {"dept": u["dept"]}
            self._require_form(c, form)
            fd = None
            if doc_html is not None:  # 편집기 문서: 값은 HTML 칸에서 뽑아 서버가 다시 검증
                doc_html = self._clean_doc_html(doc_html)
                if form:
                    fields = extract_fields(self._form_def(c, form), doc_html)
            if form:
                fmeta["form"] = self._start_form(c, u, form)
                d = fd = self._apply_form(c, u, fmeta, fields or {}, submit, len(attachments or ()))
                doc_type = fmeta["doc_type"]
                amount = fmeta["amount"] if d.get("amount") else None  # 양식 문서 금액 = 양식 항목만
            steps, ccs, pref = self._resolve_line(c, u, doc_type, amount, line_input, cc_input)
            self._form_cc(c, fmeta, steps, ccs, actor)
            src_meta = None
            if source_id is not None:
                self._check_view(c, actor, int(source_id))  # 원문을 볼 수 있는 사람만
                src_meta, _ = self._read_doc(int(source_id))
                if copy_attachments:
                    sd = self._doc_dir(int(source_id)) / "files"
                    try:
                        attachments = list(attachments or ()) + [(a["name"], (sd / a["stored"]).read_bytes())
                                                                 for a in src_meta.get("attachments", [])]
                    except OSError:  # 원문 첨부가 디스크에 없음 → 서버 오류 대신 안내
                        raise Invalid("원문 첨부 파일을 읽을 수 없습니다")
            ts = now_iso()
            cur = c.execute("insert into docs(title,drafter,dept,status,created,visibility,updated) values(?,?,?,?,?,?,?)",
                            (title, actor, u["dept"], "draft", ts, visibility, ts))
            did = cur.lastrowid
            c.execute("insert into doc_party values(?,?,'drafter')", (did, actor))
            meta = {"id": did, "title": title, "drafter": actor, "drafter_name": u["name"], "dept": u["dept"],
                    "created": ts, "submitted": None, "completed": None, "status": "draft", "number": None,
                    "visibility": visibility, "line": steps, "cc": ccs, "attachments": [], "history": [],
                    "cur_step": None, "cur_user": None, "doc_type": doc_type, "amount": amount,
                    "line_input": line_input, "cc_input": cc_input, "policy": pref, "rev": 0}
            if form:
                meta.update(form=fmeta["form"], fields=fmeta["fields"])
                c.execute("update docs set form=? where id=?", (form, did))
            if src_meta is not None:
                meta["source_doc_id"] = int(source_id)
                c.execute("update docs set source_doc_id=? where id=?", (int(source_id), did))
            cid = self._default_class(c, u["dept"], form, doc_type) if dclass is None else \
                self._check_class(c, u["dept"], 0 if dclass == "none" else dclass)
            self._set_tags_c(c, actor, did, cid, projects or [], initial=True)
            mine = {r["id"] for r in c.execute("select id from folders where owner=?", (actor,))}
            for fid in [int(x) for x in folders or [] if str(x).isdigit()]:
                if fid not in mine:
                    raise NotFound("폴더 없음")
                c.execute("insert or ignore into folder_docs(folder_id,doc_id,added) values(?,?,?)", (fid, did, ts))
            dd = self._doc_dir(did)
            if dd.exists():  # 롤백된 이전 시도의 고아 디렉터리(같은 id 재사용) 정리
                _rmtree(dd)
            dd.mkdir(parents=True)
            self._store_attachments(did, meta, attachments)
            self._hist(meta, actor, "draft")
            self._audit(c, actor, "draft", did, {"title": title})
            if src_meta is not None:
                self._audit(c, actor, "draft_from", did, {"source": int(source_id), "source_number": src_meta.get("number"),
                                                          "attachments": bool(copy_attachments)})
            if submit:
                self._do_submit(c, actor, meta)
            if doc_html is not None:
                body = self._store_html(c, meta, fd, doc_html)
                self._doc_page(meta, doc_page)
            self._index(c, meta, body)
            self._write_doc(c, meta, body)
        return self.show(actor, did, mark_read=False)

    def _editable(self, c, actor, doc_id):
        r = c.execute("select * from docs where id=?", (doc_id,)).fetchone()
        if not r or r["drafter"] != actor:
            raise NotFound("문서가 없거나 권한이 없습니다")
        if r["status"] not in ("draft", "withdrawn", "rejected"):
            raise Forbidden(f"{STATUS_KO[r['status']]} 상태 문서는 수정할 수 없습니다")
        return r

    def edit_draft(self, actor, doc_id, title=None, body=None, line=None, cc=None, visibility=None,
                   add_attachments=(), remove_attachments=(), doc_type=None, amount=None, fields=None, doc_html=None,
                   doc_page=None, dclass=_KEEP, projects=None):
        with self._tx() as c:
            self._editable(c, actor, doc_id)
            self._set_tags_c(c, actor, int(doc_id), _KEEP if dclass is _KEEP else (0 if dclass in (None, "none") else dclass),
                             projects)
            meta, old_body = self._read_doc(doc_id)
            t, b = self._check_text(title if title is not None else meta["title"],
                                    body if body is not None else old_body)
            meta["title"] = t
            fd = None
            if meta.get("form"):  # 양식 문서: 종류는 양식이 정함, 금액 필드가 있으면 금액도
                fd = self._form_def(c, meta["form"]["code"], meta["form"]["version"])
                doc_type = amount = None  # 종류·금액은 양식이 정함(직접 입력 무시)
            if doc_html is not None:
                doc_html = self._clean_doc_html(doc_html)
                if fd is not None:
                    fields = extract_fields(fd, doc_html)
            if doc_type is not None or amount is not None:
                meta["doc_type"], meta["amount"] = self._type_amount(
                    doc_type if doc_type is not None else meta.get("doc_type"),
                    amount if amount is not None else meta.get("amount"))
            if line is not None:
                meta["line_input"] = [x.strip() for x in line if x.strip()]
            if cc is not None:
                meta["cc_input"] = [x.strip().lower() for x in cc if x.strip()]
            self._line_inputs(meta)
            u = self._user(c, actor)
            meta["line"], meta["cc"], meta["policy"] = self._resolve_line(
                c, u, meta.get("doc_type", "general"), meta.get("amount"), meta["line_input"], meta["cc_input"])
            if visibility in ("private", "dept"):
                meta["visibility"] = visibility
            rm = {int(x) for x in remove_attachments or []}
            if rm:
                keep = []
                for a in meta["attachments"]:
                    if a["idx"] in rm:
                        c.after.append((_unlink, self._doc_dir(doc_id) / "files" / a["stored"]))
                    else:
                        keep.append(a)
                meta["attachments"] = keep
            self._store_attachments(doc_id, meta, add_attachments)
            if fd is not None:
                self._apply_form(c, u, meta, fields if fields is not None else meta.get("fields") or {}, False,
                                 len(meta["attachments"]))
                meta["line"], meta["cc"], meta["policy"] = self._resolve_line(
                    c, u, meta["doc_type"], meta.get("amount"), meta["line_input"], meta["cc_input"])
                self._form_cc(c, meta, meta["line"], meta["cc"], actor)
            if doc_html is not None or meta.get("doc_sha"):  # 편집기 문서는 값이 바뀌면 HTML 칸도 다시 씀
                b = self._store_html(c, meta, fd, doc_html if doc_html is not None else self._read_html(doc_id))
                if doc_html is not None:
                    self._doc_page(meta, doc_page)
            meta["rev"] = meta.get("rev", 0) + 1
            meta["submitted"] = None  # 재상신 전까지 결재선·부서장에게 숨김
            c.execute("delete from doc_party where doc_id=? and kind='proxy'", (doc_id,))
            self._hist(meta, actor, "edit")
            self._audit(c, actor, "edit", doc_id, {"title": t})
            self._index(c, meta, b)
            self._write_doc(c, meta, b)
        return self.show(actor, doc_id, mark_read=False)

    @staticmethod
    def _line_inputs(meta):
        if "line_input" not in meta:  # v0.1 문서 호환
            meta["line_input"] = [s["user"] + ("!" if s["final"] else "") for s in meta["line"]]
            meta["cc_input"] = list(meta["cc"])

    def _do_submit(self, c, actor, meta):
        """상신: 서버가 최신 정책으로 결재선을 다시 만든다(클라이언트 값 불신). 사용된 정책 버전을 meta 에 기록."""
        self._line_inputs(meta)
        u = self._user(c, actor)
        self._require_form(c, meta.get("form"))  # 자유 기안 우회 차단(상신 시 다시)
        if meta.get("form"):  # 상신 시 필수 항목·첨부까지 엄격 검증(기안 당시 양식 버전)
            self._apply_form(c, u, meta, meta.get("fields") or {}, True, len(meta.get("attachments", [])))
        meta["line"], meta["cc"], meta["policy"] = self._resolve_line(
            c, u, meta.get("doc_type", "general"), meta.get("amount"), meta["line_input"], meta["cc_input"])
        self._form_cc(c, meta, meta["line"], meta["cc"], actor)
        meta["rev"] = meta.get("rev", 0) + 1
        for s in meta["line"]:
            s.update(status="waiting", acted_by=None, proxy_for=None, at=None, comment="")
        meta["line"][0]["status"] = "pending"
        meta.update(status="pending", submitted=now_iso(), cur_step=0, cur_user=meta["line"][0]["user"])
        self._hist(meta, actor, "submit")
        self._audit(c, actor, "submit", meta["id"], {"line": [s["user"] for s in meta["line"]], "cc": meta["cc"],
                                                     "final": [s["user"] for s in meta["line"] if s["final"]],
                                                     "policy": meta.get("policy")})
        self._notify_pending(c, meta)

    def _notify_pending(self, c, meta):
        u = meta["cur_user"]
        self._notify(c, u, f"[결재요청] #{meta['id']} {meta['title']} (기안 {meta['drafter_name']})", meta["id"])
        t = today()
        for r in c.execute("select to_user from delegations where from_user=? and revoked=0 and start<=? and end>=?",
                           (u, t, t)).fetchall():
            self._notify(c, r["to_user"], f"[대결요청] {u} 대신 #{meta['id']} {meta['title']}", meta["id"])

    def submit(self, actor, doc_id):
        with self._tx() as c:
            self._editable(c, actor, doc_id)
            meta, body = self._read_doc(doc_id)
            self._do_submit(c, actor, meta)
            self._index(c, meta, body)
            self._write_doc(c, meta)
        return self.show(actor, doc_id, mark_read=False)

    def _acting_step(self, c, actor, doc_id):
        r = c.execute("select * from docs where id=?", (doc_id,)).fetchone()
        if not r:
            raise NotFound("문서가 없거나 권한이 없습니다")
        if r["status"] != "pending":
            self._check_view(c, actor, doc_id)
            raise Forbidden(f"{STATUS_KO[r['status']]} 상태 문서는 결재할 수 없습니다")
        meta, body = self._read_doc(doc_id)
        if meta.get("status") != "pending" or meta.get("cur_step") is None:
            raise GyeolError("문서 상태 불일치(관리자 확인 필요)")
        step = meta["line"][meta["cur_step"]]
        if actor == meta["drafter"]:
            self._check_view(c, actor, doc_id)
            raise Forbidden("기안자는 자기 문서를 결재(대결 포함)할 수 없습니다")
        others = [st for i, st in enumerate(meta["line"]) if i != meta["cur_step"]]
        if any(st.get("acted_by") == actor for st in others):
            raise Forbidden("이미 이 문서를 결재했습니다(중복 결재 불가)")
        if step["user"] == actor:
            return meta, body, step, None
        if step["user"] in self._delegators(c, actor):
            if any(st["user"] == actor for st in others):
                raise Forbidden("결재선에 있는 사람은 다른 단계를 대결할 수 없습니다")
            return meta, body, step, step["user"]
        self._check_view(c, actor, doc_id)
        raise Forbidden("현재 결재 차례가 아닙니다")

    def _check_rev(self, meta, rev):
        if rev is not None and int(rev) != meta.get("rev", 0):
            raise Invalid("문서가 그 사이 변경되었습니다. 다시 열어 확인하세요")

    def approve(self, actor, doc_id, comment="", jeongyeol=False, rev=None):
        """결재(승인). proxy=대결, 전결=결재선의 '!' 표시 단계 또는 부서장/관리자가 jeongyeol=True."""
        comment = (comment or "")[:1000]
        with self._tx() as c:
            meta, body, step, proxy_for = self._acting_step(c, actor, doc_id)
            self._check_rev(meta, rev)
            if jeongyeol and not step["final"]:
                holder = c.execute("select * from users where username=?", (step["user"],)).fetchone()
                if not holder or not self._jeon_allowed(c, meta, holder, step):
                    raise Forbidden("전결 권한이 없습니다(결재규정/부서장)")
            step.update(status="approved", acted_by=actor, proxy_for=proxy_for, at=now_iso(), comment=comment)
            final = step["final"] or jeongyeol
            label = "대결" if proxy_for else "결재"
            if final and meta["cur_step"] < len(meta["line"]) - 1:
                label += "(전결)"
            self._hist(meta, actor, label, comment)
            self._audit(c, actor, "approve", doc_id, {"step": meta["cur_step"], "proxy_for": proxy_for,
                                                       "final": bool(final)})
            nxt = meta["cur_step"] + 1
            if final or nxt >= len(meta["line"]):
                for s in meta["line"][nxt:]:
                    s["status"] = "skipped"
                year = int(today()[:4])
                meta.update(status="done", completed=now_iso(), cur_step=None, cur_user=None,
                            number=self._doc_number(c, meta, year))
                self._hist(meta, "system", "완료", meta["number"])
                self._write_doc(c, meta)
                ch = self._content_hash(doc_id, meta, body)
                self._audit(c, "system", "complete", doc_id, {"number": meta["number"], "content_hash": ch})
                msg = f"[결재완료] {meta['number']} {meta['title']}"
                for who in {meta["drafter"], *[s["user"] for s in meta["line"]]}:
                    self._notify(c, who, msg, doc_id)
                for x in meta["cc"]:
                    self._notify(c, x, f"[참조] {meta['number']} {meta['title']}", doc_id)
                self._index(c, meta, body)
                c.after.append((self._make_readonly, doc_id))
            else:
                meta["line"][nxt]["status"] = "pending"
                meta.update(cur_step=nxt, cur_user=meta["line"][nxt]["user"])
                self._notify_pending(c, meta)
                self._index(c, meta, body)
                self._write_doc(c, meta)
        return self.show(actor, doc_id, mark_read=False)

    def _next_serial(self, c, dept, year):
        c.execute("insert or ignore into serials(dept,year,last) values(?,?,0)", (dept, year))
        c.execute("update serials set last=last+1 where dept=? and year=?", (dept, year))
        return c.execute("select last from serials where dept=? and year=?", (dept, year)).fetchone()[0]

    def reserve_serial(self, dept, year):
        """동시성 시험용 공개 API: 독립 트랜잭션으로 일련번호 1개 발급."""
        with self._tx() as c:
            return self._next_serial(c, dept, year)

    def _make_readonly(self, doc_id):
        d = self._doc_dir(doc_id)
        for p in list((d / "files").iterdir()) + [d / "meta.json", d / "body.md", d / "doc.html"]:
            _try_chmod(p, 0o444)

    def reject(self, actor, doc_id, comment, rev=None):
        comment = (comment or "").strip()[:1000]
        if len(comment) < REJECT_MIN:
            raise Invalid(f"반려 사유를 {REJECT_MIN}자 이상 입력하세요")
        with self._tx() as c:
            meta, body, step, proxy_for = self._acting_step(c, actor, doc_id)
            self._check_rev(meta, rev)
            step.update(status="rejected", acted_by=actor, proxy_for=proxy_for, at=now_iso(), comment=comment)
            meta.update(status="rejected", cur_step=None, cur_user=None)
            self._hist(meta, actor, "반려", comment)
            self._audit(c, actor, "reject", doc_id, {"proxy_for": proxy_for, "comment": comment})
            self._notify(c, meta["drafter"], f"[반려] #{doc_id} {meta['title']}: {comment}", doc_id)
            self._index(c, meta, body)
            self._write_doc(c, meta)
        return self.show(actor, doc_id, mark_read=False)

    def withdraw(self, actor, doc_id):
        """회수: 기안자가, 아직 아무도 결재하지 않은(첫 결재자 처리 전) 진행 문서만."""
        with self._tx() as c:
            r = c.execute("select * from docs where id=?", (doc_id,)).fetchone()
            if not r or r["drafter"] != actor:
                raise NotFound("문서가 없거나 권한이 없습니다")
            if r["status"] != "pending":
                raise Forbidden("진행 중인 문서만 회수할 수 있습니다")
            meta, body = self._read_doc(doc_id)
            if any(s["status"] == "approved" for s in meta["line"]):
                raise Forbidden("이미 결재가 시작되어 회수할 수 없습니다")
            prev = meta["cur_user"]
            for s in meta["line"]:
                s["status"] = "waiting"
            meta.update(status="withdrawn", cur_step=None, cur_user=None)
            self._hist(meta, actor, "회수")
            self._audit(c, actor, "withdraw", doc_id)
            self._notify(c, prev, f"[회수] #{doc_id} {meta['title']} 기안자가 회수했습니다", doc_id)
            self._index(c, meta, body)
            self._write_doc(c, meta)
        return self.show(actor, doc_id, mark_read=False)

    def show(self, actor, doc_id, mark_read=True):
        doc_id = int(doc_id)
        with self._ro() as c:
            self._check_view(c, actor, doc_id)
        meta, body = self._read_doc(doc_id)
        if mark_read:
            with self._tx() as c:
                if not c.execute("select 1 from reads where doc_id=? and username=?", (doc_id, actor)).fetchone():
                    c.execute("insert into reads(doc_id,username,at) values(?,?,?)", (doc_id, actor, now_iso()))
                    if actor in meta["cc"]:
                        self._audit(c, actor, "cc_read", doc_id)
        with self._ro() as c:
            reads = {r["username"]: r["at"] for r in c.execute("select * from reads where doc_id=?", (doc_id,))}
            delegators = self._delegators(c, actor)
        meta["body"] = body
        if meta.get("doc_sha"):
            meta["doc_html"] = self._read_html(doc_id)
        meta["reads"] = reads
        meta["status_ko"] = STATUS_KO[meta["status"]]
        if meta.get("source_doc_id"):  # "이 문서로 기안"의 원문: 번호(없으면 #id), 링크는 볼 수 있을 때만
            sid = meta["source_doc_id"]
            with self._ro() as c:
                r = c.execute("select number from docs where id=?", (sid,)).fetchone()
                try:
                    self._check_view(c, actor, sid)
                    can = True
                except NotFound:
                    can = False
            meta["source"] = {"id": sid, "number": (r["number"] if r else None) or f"#{sid}", "can_view": can}
        cur = meta.get("cur_user")
        meta["can"] = {
            "edit": actor == meta["drafter"] and meta["status"] in ("draft", "withdrawn", "rejected"),
            "approve": meta["status"] == "pending" and (cur == actor or cur in delegators),
            "proxy": meta["status"] == "pending" and cur != actor and cur in delegators,
            "withdraw": actor == meta["drafter"] and meta["status"] == "pending"
                        and not any(s["status"] == "approved" for s in meta["line"]),
        }
        return meta

    def get_attachment(self, actor, doc_id, idx):
        meta = self.show(actor, doc_id, mark_read=False)
        for a in meta["attachments"]:
            if a["idx"] == int(idx):
                d = (self._doc_dir(doc_id) / "files").resolve()
                p = (d / a["stored"]).resolve()
                if p.parent != d:
                    raise NotFound("첨부 없음")
                return a["name"], p
        raise NotFound("첨부 없음")

    def _rows(self, c, where, args, limit=200):
        q = (f"select d.id,d.title,d.drafter,u.name as drafter_name,d.dept,d.status,d.created,d.submitted,"
             f"d.completed,d.number,d.cur_user,cu.name as cur_name,d.doc_type,d.amount from docs d "
             f"left join users u on u.username=d.drafter left join users cu on cu.username=d.cur_user "
             f"where {where} order by d.id desc limit {int(limit)}")
        out = []
        for r in c.execute(q, args):
            x = dict(r)
            x["status_ko"] = STATUS_KO[x["status"]]
            out.append(x)
        return out

    def list_box(self, actor, box):
        if box not in BOXES:
            raise Invalid(f"box는 {BOXES} 중 하나")
        with self._ro() as c:
            args = self._vis_args(c, actor)
            P = "d.id in (select doc_id from doc_party where username=:u and kind in ({}))"
            w = {
                "inbox": "d.status='pending' and (d.cur_user=:u or d.cur_user in (select from_user from delegations "
                         "where to_user=:u and revoked=0 and start<=:t and end>=:t))",
                "drafts": "d.drafter=:u and d.status in ('draft','withdrawn')",
                "progress": "d.status='pending' and " + P.format("'drafter','line','proxy'"),
                "done": "d.status='done' and " + P.format("'drafter','line','proxy','cc'"),
                "rejected": "d.status='rejected' and " + P.format("'drafter','line','proxy'"),
                "cc": "d.status in ('pending','done') and " + P.format("'cc'"),
                "dept": "d.status='done' and d.dept=:dept and (d.visibility='dept' or :is_head=1)",
            }[box]
            return self._rows(c, f"{w} and {self._VIS}", args)

    def box_counts(self, actor):
        return {b: len(self.list_box(actor, b)) for b in ("inbox", "drafts", "cc")}

    def search(self, actor, q=None, title=None, drafter=None, dept=None, status=None, date_from=None,
               date_to=None, number=None, limit=100, doc_type=None):
        with self._ro() as c:
            args = self._vis_args(c, actor)
            conds = [self._VIS]

            def like(s):
                return "%" + s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            if q and q.strip():
                q = q.strip()[:100]
                if self.fts and len(q) >= 3:
                    conds.append("d.id in (select rowid from doc_fts where doc_fts match :q)")
                    args["q"] = '"' + q.replace('"', '""') + '"'
                else:
                    conds.append("d.id in (select doc_id from doc_text where title like :ql escape '\\' "
                                 "or body like :ql escape '\\')")
                    args["ql"] = like(q)
            if title:
                conds.append("d.title like :tl escape '\\'")
                args["tl"] = like(title.strip()[:100])
            if drafter:
                conds.append("(d.drafter=:dr or u.name=:dr)")
                args["dr"] = drafter.strip()
            if dept:
                conds.append("d.dept=:dp")
                args["dp"] = dept.strip().upper()
            if status:
                if status not in STATUS_KO:
                    raise Invalid(f"status는 {list(STATUS_KO)}")
                conds.append("d.status=:st")
                args["st"] = status
            if date_from:
                if not _valid_date(date_from):
                    raise Invalid("날짜는 YYYY-MM-DD")
                conds.append("substr(d.created,1,10)>=:df")
                args["df"] = date_from
            if date_to:
                if not _valid_date(date_to):
                    raise Invalid("날짜는 YYYY-MM-DD")
                conds.append("substr(d.created,1,10)<=:dt")
                args["dt"] = date_to
            if doc_type:
                try:
                    args["ty"] = pol.type_code(doc_type)
                except pol.PolicyError as e:
                    raise Invalid(str(e))
                conds.append("d.doc_type=:ty")
            if number:
                conds.append("d.number=:nb")
                args["nb"] = number.strip().upper()
            return self._rows(c, " and ".join(conds), args, limit=min(int(limit), 500))

    # ------------------------------------------------------------ messages (쪽지)
    def msg_send(self, actor, to, body):
        body = (body or "").strip()
        if not body or len(body) > 5000:
            raise Invalid("쪽지 내용(5000자 이하) 필요")
        with self._tx() as c:
            self._user(c, actor)
            self._user(c, to)
            cur = c.execute("insert into messages(sender,recipient,body,created) values(?,?,?,?)",
                            (actor, to, body, now_iso()))
            return {"id": cur.lastrowid}

    def msg_list(self, actor, unread=False, sent=False, limit=100):
        with self._ro() as c:
            col = "sender" if sent else "recipient"
            q = f"select * from messages where {col}=?" + (" and read_at is null" if unread else "")
            return [dict(r) for r in c.execute(q + f" order by id desc limit {int(limit)}", (actor,))]

    def msg_read(self, actor, mid):
        with self._tx() as c:
            r = c.execute("select * from messages where id=? and (recipient=? or sender=?)",
                          (int(mid), actor, actor)).fetchone()
            if not r:
                raise NotFound("쪽지 없음")
            if r["recipient"] == actor and not r["read_at"]:
                c.execute("update messages set read_at=? where id=?", (now_iso(), r["id"]))
            return dict(r)

    def msg_read_all(self, actor):
        with self._tx() as c:
            c.execute("update messages set read_at=? where recipient=? and read_at is null", (now_iso(), actor))

    def unread_count(self, actor):
        with self._ro() as c:
            return c.execute("select count(*) from messages where recipient=? and read_at is null", (actor,)).fetchone()[0]

    # ------------------------------------------------------------ board (게시판)
    def board_post(self, actor, board, title, body):
        if board not in BOARDS:
            raise Invalid(f"게시판은 {list(BOARDS)}")
        title, body = self._check_text(title, body)
        with self._tx() as c:
            u = self._user(c, actor)
            if board == "notice" and not (u["role"] == "admin" or self._is_head(c, u)):
                raise Forbidden("공지는 부서장/관리자만 쓸 수 있습니다")
            cur = c.execute("insert into posts(board,author,title,body,created) values(?,?,?,?,?)",
                            (board, actor, title, body, now_iso()))
            self._audit(c, actor, "post", None, {"board": board, "id": cur.lastrowid})
            return {"id": cur.lastrowid}

    def board_list(self, actor, board, limit=100):
        if board not in BOARDS:
            raise Invalid("게시판 없음")
        with self._ro() as c:
            self._user(c, actor)
            return [dict(r) for r in c.execute(
                "select p.id,p.title,p.author,u.name as author_name,p.created,p.pinned from posts p left join users u "
                f"on u.username=p.author where board=? order by p.pinned desc, p.id desc limit {int(limit)}", (board,))]

    def board_pin(self, actor, pid, pinned=True):
        self._require_admin(actor)
        with self._tx() as c:
            r = c.execute("select id from posts where id=?", (int(pid),)).fetchone()
            if not r:
                raise NotFound("게시글 없음")
            c.execute("update posts set pinned=? where id=?", (1 if pinned else 0, r["id"]))
            self._audit(c, actor, "post_pin", None, {"id": r["id"], "pinned": bool(pinned)})

    def board_read(self, actor, pid):
        with self._ro() as c:
            self._user(c, actor)
            r = c.execute("select p.*, u.name as author_name from posts p left join users u on u.username=p.author "
                          "where p.id=?", (int(pid),)).fetchone()
            if not r:
                raise NotFound("게시글 없음")
            return dict(r)

    def board_delete(self, actor, pid):
        with self._tx() as c:
            u = self._user(c, actor)
            r = c.execute("select * from posts where id=?", (int(pid),)).fetchone()
            if not r or (r["author"] != actor and u["role"] != "admin"):
                raise NotFound("게시글 없음")
            c.execute("delete from posts where id=?", (r["id"],))
            self._audit(c, actor, "post_delete", None, {"id": r["id"]})
