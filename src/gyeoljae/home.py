"""홈 화면 데이터, 개인 보관함(폴더), 자료실(전사·부서 파일 공유, 판 관리). App mixin.

자료실 권한: 전사(org) = 모두 읽기, 부서장급·관리자 올리기/관리자 삭제.
부서(dept) = 그 부서원만 읽기·올리기, 삭제는 처음 올린 사람 또는 그 부서 부서장급.
같은 이름(대소문자 무시)으로 올리면 덮어쓰지 않고 새 판(version)이 된다. 삭제는 숨김(기록·이전 판 보존).
"""
from __future__ import annotations

import datetime as _dt
import os
import unicodedata

from .core_base import Forbidden, Invalid, NotFound, _atomic_write, now_iso, safe_filename, sha256

LIB_MAX_BYTES = 20 * 1024 * 1024
NEW_DAYS = 3
HOME_SCHEMA = """
create table if not exists folders(id integer primary key, owner text not null, name text not null, created text,
  unique(owner, name));
create table if not exists folder_docs(folder_id int not null, doc_id int not null, added text,
  primary key(folder_id, doc_id));
create table if not exists lib_files(id integer primary key autoincrement, scope text not null, dept text,
  name text not null, name_key text not null, created_by text, created text, updated text,
  cur_version int not null default 0, deleted int not null default 0);
create unique index if not exists lib_live on lib_files(scope, ifnull(dept,''), name_key) where deleted=0;
create table if not exists lib_versions(file_id int not null, version int not null, stored text not null,
  size int not null, sha256 text not null, uploader text not null, created text, note text,
  primary key(file_id, version));
"""


def _days_since(iso):
    try:
        d = _dt.date.fromisoformat((iso or "")[:10])
    except ValueError:
        return 0
    return (_dt.date.today() - d).days


class HomeMixin:
    # ------------------------------------------------------------ home
    def home(self, actor, n=5):
        """홈 카드 데이터(CLI `home --json` 과 웹 홈이 같은 함수)."""
        me = self.get_user(actor)
        notices = self.board_list(actor, "notice", 50)
        for p in notices:
            p["new"] = _days_since(p["created"]) < NEW_DAYS
        inbox = sorted(self.list_box(actor, "inbox"), key=lambda r: (r["submitted"] or "", r["id"]))
        for r in inbox:
            r["waiting_days"] = _days_since(r["submitted"])
        mine = [r for r in self.list_box(actor, "progress") if r["drafter"] == actor]
        done = sorted(self.list_box(actor, "done"), key=lambda r: (r["completed"] or "", r["id"]), reverse=True)
        rejected = [r for r in self.list_box(actor, "rejected") if r["drafter"] == actor]
        cc = self.list_box(actor, "cc")
        drafts = self.list_box(actor, "drafts")
        return {
            "user": {k: me.get(k) for k in ("username", "name", "dept", "position", "role", "is_head")},
            "notices": {"count": len(notices), "new": sum(1 for p in notices if p["new"]), "items": notices[:n]},
            "inbox": {"count": len(inbox), "items": inbox[:n]},
            "progress": {"count": len(mine), "items": mine[:n]},
            "done": {"count": len(done), "items": done[:n]},
            "rejected": {"count": len(rejected), "items": rejected[:n]},
            "unread_messages": self.unread_count(actor), "unread_mail": self.unread_mail_count(actor),
            "cc": len(cc), "drafts": len(drafts),
        }

    def badges(self, actor):
        return {"inbox": len(self.list_box(actor, "inbox")), "messages": self.unread_count(actor),
                "mail": self.unread_mail_count(actor)}

    # ------------------------------------------------------------ personal folders (내 보관함)
    def _folder(self, c, actor, fid):
        r = c.execute("select * from folders where id=? and owner=?", (int(fid), actor)).fetchone()
        if not r:
            raise NotFound("폴더 없음")
        return r

    @staticmethod
    def _folder_name(name):
        name = unicodedata.normalize("NFC", (name or "").strip())
        if not name or len(name) > 50 or any(ord(ch) < 32 for ch in name):
            raise Invalid("폴더 이름(50자 이하) 필요")
        return name

    def folder_list(self, actor):
        with self._ro() as c:
            self._user(c, actor)
            return [dict(r) for r in c.execute(
                "select f.id, f.name, (select count(*) from folder_docs x where x.folder_id=f.id) as count "
                "from folders f where owner=? order by name", (actor,))]

    def folder_create(self, actor, name):
        name = self._folder_name(name)
        with self._tx() as c:
            self._user(c, actor)
            if c.execute("select count(*) from folders where owner=?", (actor,)).fetchone()[0] >= 100:
                raise Invalid("폴더는 최대 100개")
            if c.execute("select 1 from folders where owner=? and name=?", (actor, name)).fetchone():
                raise Invalid("같은 이름의 폴더가 있습니다")
            cur = c.execute("insert into folders(owner,name,created) values(?,?,?)", (actor, name, now_iso()))
            return {"id": cur.lastrowid, "name": name}

    def folder_rename(self, actor, fid, name):
        name = self._folder_name(name)
        with self._tx() as c:
            f = self._folder(c, actor, fid)
            if c.execute("select 1 from folders where owner=? and name=? and id!=?", (actor, name, f["id"])).fetchone():
                raise Invalid("같은 이름의 폴더가 있습니다")
            c.execute("update folders set name=? where id=?", (name, f["id"]))
        return {"id": f["id"], "name": name}

    def folder_delete(self, actor, fid):
        """폴더만 지운다(문서 자체는 그대로)."""
        with self._tx() as c:
            f = self._folder(c, actor, fid)
            c.execute("delete from folder_docs where folder_id=?", (f["id"],))
            c.execute("delete from folders where id=?", (f["id"],))

    def folder_add(self, actor, fid, doc_id):
        with self._tx() as c:
            f = self._folder(c, actor, fid)
            self._check_view(c, actor, int(doc_id))  # 볼 수 있는 문서만 담기
            c.execute("insert or ignore into folder_docs(folder_id,doc_id,added) values(?,?,?)",
                      (f["id"], int(doc_id), now_iso()))

    def folder_remove(self, actor, fid, doc_id):
        with self._tx() as c:
            f = self._folder(c, actor, fid)
            c.execute("delete from folder_docs where folder_id=? and doc_id=?", (f["id"], int(doc_id)))

    def folder_docs(self, actor, fid):
        with self._ro() as c:
            f = self._folder(c, actor, fid)
            args = self._vis_args(c, actor)
            args["fid"] = f["id"]
            rows = self._rows(c, f"d.id in (select doc_id from folder_docs where folder_id=:fid) and {self._VIS}", args)
            return {"id": f["id"], "name": f["name"], "docs": rows}

    # ------------------------------------------------------------ library (자료실)
    def _lib_dir(self):
        d = self.dir / "library"
        d.mkdir(exist_ok=True)
        return d

    def _lib_perm(self, c, actor, scope, dept):
        """(읽기, 쓰기) 권한."""
        u = self._user(c, actor)
        if scope == "org":
            return True, (u["role"] == "admin" or self._is_head(c, u))
        if scope == "dept":
            same = bool(dept) and u["dept"] == dept
            return same, same
        raise Invalid("자료실 범위는 org(전사) 또는 dept(부서)")

    def _lib_file(self, c, actor, fid, need_write=False):
        r = c.execute("select * from lib_files where id=? and deleted=0", (int(fid),)).fetchone()
        if not r:
            raise NotFound("자료가 없거나 권한이 없습니다")
        rd, wr = self._lib_perm(c, actor, r["scope"], r["dept"])
        if not rd:
            raise NotFound("자료가 없거나 권한이 없습니다")
        if need_write and not wr:
            raise Forbidden("이 자료실에 쓸 권한이 없습니다")
        return r

    def files_list(self, actor, scope="dept", dept=None, q=None):
        with self._ro() as c:
            u = self._user(c, actor)
            dept = (dept or u["dept"]).upper() if scope == "dept" else None
            rd, wr = self._lib_perm(c, actor, scope, dept)
            if not rd:
                raise NotFound("자료실이 없거나 권한이 없습니다")
            conds, args = ["f.deleted=0", "f.scope=?", "ifnull(f.dept,'')=?"], [scope, dept or ""]
            if q:
                conds.append("f.name like ?")
                args.append(f"%{q[:50]}%")
            rows = [dict(r) for r in c.execute(
                "select f.id,f.name,f.cur_version,f.updated,f.created_by,v.size,v.uploader,un.name as uploader_name "
                "from lib_files f join lib_versions v on v.file_id=f.id and v.version=f.cur_version "
                f"left join users un on un.username=v.uploader where {' and '.join(conds)} order by f.name", args)]
            return {"scope": scope, "dept": dept, "can_write": wr, "files": rows}

    def files_upload(self, actor, filename, data: bytes, scope="dept", note=""):
        name = safe_filename(filename)
        if len(data) > LIB_MAX_BYTES:
            raise Invalid(f"자료실 파일은 최대 {LIB_MAX_BYTES // 1048576}MB")
        if not data:
            raise Invalid("빈 파일")
        note = (note or "").strip()[:200]
        key = name.lower()
        with self._tx() as c:
            u = self._user(c, actor)
            dept = u["dept"] if scope == "dept" else None
            rd, wr = self._lib_perm(c, actor, scope, dept)
            if not wr:
                raise Forbidden("이 자료실에 올릴 권한이 없습니다")
            ex = c.execute("select * from lib_files where scope=? and ifnull(dept,'')=? and name_key=? and deleted=0",
                           (scope, dept or "", key)).fetchone()
            ts = now_iso()
            if ex:
                fid, ver = ex["id"], ex["cur_version"] + 1
                c.execute("update lib_files set cur_version=?, updated=? where id=?", (ver, ts, fid))
            else:
                cur = c.execute("insert into lib_files(scope,dept,name,name_key,created_by,created,updated,cur_version) "
                                "values(?,?,?,?,?,?,?,1)", (scope, dept, name, key, actor, ts, ts))
                fid, ver = cur.lastrowid, 1
            ext = os.path.splitext(name)[1]
            stored = f"{fid:06d}/{ver:03d}{ext}"
            c.execute("insert into lib_versions(file_id,version,stored,size,sha256,uploader,created,note) "
                      "values(?,?,?,?,?,?,?,?)", (fid, ver, stored, len(data), sha256(data), actor, ts, note))
            target = self._lib_dir() / f"{fid:06d}"
            target.mkdir(exist_ok=True)
            c.after.append((_atomic_write, target / f"{ver:03d}{ext}", data))
            self._audit(c, actor, "lib_upload", None, {"file": fid, "version": ver, "name": name, "scope": scope,
                                                       "dept": dept, "sha256": sha256(data)})
        return {"id": fid, "name": name, "version": ver}

    def files_new_version(self, actor, fid, data: bytes, note=""):
        """기존 자료에 새 판(이름·위치는 그대로, 확장자도 원래 것)."""
        with self._ro() as c:
            f = self._lib_file(c, actor, fid, need_write=True)
        if f["scope"] == "dept":
            with self._ro() as c:
                if self._user(c, actor)["dept"] != f["dept"]:
                    raise Forbidden("이 자료실에 쓸 권한이 없습니다")
        return self.files_upload(actor, f["name"], data, f["scope"], note)

    def files_versions(self, actor, fid):
        with self._ro() as c:
            f = self._lib_file(c, actor, fid)
            vs = [dict(r) for r in c.execute(
                "select v.version,v.size,v.sha256,v.uploader,un.name as uploader_name,v.created,v.note from lib_versions v "
                "left join users un on un.username=v.uploader where file_id=? order by version desc", (f["id"],))]
            _, wr = self._lib_perm(c, actor, f["scope"], f["dept"])
            return {"id": f["id"], "name": f["name"], "scope": f["scope"], "dept": f["dept"], "can_write": wr,
                    "can_delete": self._lib_can_delete(c, actor, f), "versions": vs}

    def files_get(self, actor, fid, version=None):
        with self._ro() as c:
            f = self._lib_file(c, actor, fid)
            v = c.execute("select * from lib_versions where file_id=? and version=?",
                          (f["id"], int(version) if version else f["cur_version"])).fetchone()
            if not v:
                raise NotFound("판 없음")
        base = self._lib_dir().resolve()
        p = (base / v["stored"]).resolve()
        if base not in p.parents:
            raise NotFound("자료 없음")
        name = f["name"] if not version or int(version) == f["cur_version"] else \
            f"{os.path.splitext(f['name'])[0]} (판{int(version)}){os.path.splitext(f['name'])[1]}"
        return name, p

    def _lib_can_delete(self, c, actor, f):
        u = self._user(c, actor)
        if f["scope"] == "org":
            return u["role"] == "admin"
        return u["dept"] == f["dept"] and (f["created_by"] == actor or self._is_head(c, u))

    def files_delete(self, actor, fid):
        """숨김 삭제(판·기록 보존)."""
        with self._tx() as c:
            f = self._lib_file(c, actor, fid)
            if not self._lib_can_delete(c, actor, f):
                raise Forbidden("삭제 권한이 없습니다(올린 사람·부서장급/전사는 관리자)")
            c.execute("update lib_files set deleted=1, updated=? where id=?", (now_iso(), f["id"]))
            self._audit(c, actor, "lib_delete", None, {"file": f["id"], "name": f["name"]})
