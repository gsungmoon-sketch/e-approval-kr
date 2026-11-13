"""사내 메일(편지함) + 선택적 외부 SMTP 발송(알림·외부로 보내기). App mixin.

- 사내 메일: 받은편지함/보낸편지함/임시보관/휴지통, 받는사람·참조·숨은참조(사람·@부서·@전체), 첨부, 답장·전달, 읽음 확인, 검색.
- 쪽지(messages)는 별개 기능으로 그대로 둔다(결재 알림은 쪽지).
- 외부 SMTP(기본 꺼짐): 관리자가 릴레이 설정(데이터 디렉터리 smtp.json, 저장소 밖). 결재 알림을 사원 외부 메일로,
  허용 시 사내 메일을 외부 주소로도 보냄. 수신 서버는 만들지 않는다(오픈 릴레이 아님: 우리 앱이 만든 메일만 나감).
"""
from __future__ import annotations

import json
import os
import re
import smtplib
import ssl
import threading
import time
from email.message import EmailMessage
from email.utils import formataddr, make_msgid, parseaddr

from .core_base import (MAX_FILE_BYTES, MAX_FILES, MAX_TOTAL_BYTES, Forbidden, Invalid, NotFound, _atomic_write,
                        _try_chmod, now_iso, safe_filename, sha256)

MAIL_SCHEMA = """
create table if not exists mails(id integer primary key autoincrement, sender text not null, subject text not null,
  body text not null, created text, sent int not null default 0, sender_folder text not null default 'drafts',
  to_spec text, cc_spec text, bcc_spec text, ext_spec text, reply_to int, forward_of int);
create index if not exists mails_sender on mails(sender, sender_folder);
create table if not exists mail_rcpt(mail_id int not null, username text not null, kind text not null,
  folder text not null default 'inbox', read_at text, primary key(mail_id, username));
create index if not exists mail_rcpt_user on mail_rcpt(username, folder, read_at);
create table if not exists mail_att(mail_id int not null, idx int not null, name text not null, stored text not null,
  size int not null, sha256 text not null, primary key(mail_id, idx));
create table if not exists outbox(id integer primary key, kind text not null, to_addr text not null, subject text not null,
  body text not null, created text, status text not null default 'queued', tries int not null default 0,
  error text, sent_at text, requested_by text);
"""
MAX_PLAIN_RCPT = 100
NOTIFY_PER_HOUR = 10  # 받는 사람당 시간당 결재 알림 메일 상한
FOLDERS = {"inbox": "받은편지함", "sent": "보낸편지함", "drafts": "임시보관", "trash": "휴지통"}
MAX_RCPT = 1000
ADDR_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[A-Za-z0-9-]{1,63}(\.[A-Za-z0-9-]{1,63})+$")
SMTP_DEFAULT = {"enabled": False, "host": "", "port": 587, "security": "starttls", "user": "", "password": "",
                "from_addr": "", "from_name": "전자결재", "notify_approvals": True, "allow_external_mail": False,
                "rate_per_hour": 200, "user_external_per_hour": 20}


def _one_line(s, n=200):
    return re.sub(r"[\r\n\x00-\x1f\x7f]+", " ", str(s or "")).strip()[:n]


def valid_addr(a):
    a = (a or "").strip()
    name, addr = parseaddr(a)
    return addr if addr == a and ADDR_RE.match(addr) and len(addr) <= 254 else None


class MailMixin:
    # ------------------------------------------------------------ recipients
    def _expand(self, c, actor_row, spec):
        """'kim, @DEV, @전체' → 사용자 목록. @전체/@ALL 은 관리자·부서장급만."""
        out = []
        for tok in [t.strip() for t in re.split(r"[,;\s]+", spec or "") if t.strip()]:
            if tok.lower() in ("@all", "@전체", "*"):
                if not (actor_row["role"] == "admin" or self._is_head(c, actor_row)):
                    raise Forbidden("전체 메일은 관리자·부서장급만 보낼 수 있습니다")
                out += [r["username"] for r in c.execute("select username from users where active=1 order by username")]
            elif tok.startswith("@"):
                code = tok[1:].upper()
                if not c.execute("select 1 from depts where code=? and active=1", (code,)).fetchone():
                    raise NotFound(f"부서 없음: {tok}")
                out += [r["username"] for r in c.execute(
                    "select username from users where dept=? and active=1 order by username", (code,))]
            else:
                out.append(self._user(c, tok.lower())["username"])
        return out

    # ------------------------------------------------------------ send / draft
    def mail_send(self, actor, to="", cc="", bcc="", subject="", body="", attachments=(), reply_to=None,
                  forward_of=None, draft_id=None, save_draft=False, ext_to=""):
        subject = _one_line(subject, 200)
        body = (body or "").replace("\r\n", "\n")
        if len(body) > 200_000:
            raise Invalid("본문이 너무 깁니다")
        if not save_draft and not subject:
            raise Invalid("제목을 입력하세요")
        specs = {}
        for k, v in (("to", to), ("cc", cc), ("bcc", bcc), ("ext", ext_to)):
            if len(v or "") > 2000:  # 잘라서 엉뚱한 사람에게 가지 않도록 거부
                raise Invalid("받는 사람 목록이 너무 깁니다(2000자)")
            specs[k] = _one_line(v, 2000)
        with self._tx() as c:
            me = self._user(c, actor)
            if draft_id:
                d = c.execute("select * from mails where id=? and sender=? and sent=0 and sender_folder='drafts'",
                              (int(draft_id), actor)).fetchone()
                if not d:
                    raise NotFound("임시보관 메일 없음")
                mid = d["id"]
                c.execute("update mails set subject=?, body=?, to_spec=?, cc_spec=?, bcc_spec=?, ext_spec=?, created=? "
                          "where id=?", (subject, body, specs["to"], specs["cc"], specs["bcc"], specs["ext"], now_iso(), mid))
            else:
                if reply_to:
                    self._mail_row(c, actor, reply_to)
                cur = c.execute("insert into mails(sender,subject,body,created,to_spec,cc_spec,bcc_spec,ext_spec,reply_to,"
                                "forward_of) values(?,?,?,?,?,?,?,?,?,?)",
                                (actor, subject, body, now_iso(), specs["to"], specs["cc"], specs["bcc"], specs["ext"],
                                 int(reply_to) if reply_to else None, int(forward_of) if forward_of else None))
                mid = cur.lastrowid
                if forward_of:  # 전달: 원본을 볼 수 있을 때만 첨부 복사
                    self._mail_row(c, actor, forward_of)
                    for a in c.execute("select * from mail_att where mail_id=? order by idx", (int(forward_of),)).fetchall():
                        src = self._mail_dir() / a["stored"]
                        if src.exists():
                            self._mail_store(c, mid, a["name"], src.read_bytes())
            for fname, data in attachments or []:
                self._mail_store(c, mid, fname, data)
            if save_draft:
                c.execute("update mails set sent=0, sender_folder='drafts' where id=?", (mid,))
                return {"id": mid, "draft": True}
            kinds = {}
            if not (me["role"] == "admin" or self._is_head(c, me)):  # 부서 여러 개 나열로 전체 메일 우회 방지
                ndept = sum(1 for k in ("to", "cc", "bcc") for t in re.split(r"[,;\s]+", specs[k]) if t.startswith("@"))
                if ndept > 1:
                    raise Forbidden("부서 단위(@부서) 받는 사람은 한 번에 한 부서만 쓸 수 있습니다(관리자·부서장급 제외)")
            for kind in ("to", "cc", "bcc"):
                for u in self._expand(c, me, specs[kind]):
                    if u not in kinds:  # 같은 사람은 가장 앞 종류(to>cc>bcc)로 1번만
                        kinds[u] = kind
            ext = []
            for a in [x for x in re.split(r"[,;\s]+", specs["ext"]) if x]:
                v = valid_addr(a)
                if not v:
                    raise Invalid(f"외부 메일 주소 형식 오류: {a}")
                ext.append(v)
            if not kinds and not ext:
                raise Invalid("받는 사람을 입력하세요")
            if len(kinds) > MAX_PLAIN_RCPT and not (me["role"] == "admin" or self._is_head(c, me)):
                raise Forbidden(f"받는 사람은 {MAX_PLAIN_RCPT}명까지입니다(관리자·부서장급 제외)")
            if len(kinds) + len(ext) > MAX_RCPT:
                raise Invalid(f"받는 사람은 최대 {MAX_RCPT}명")
            for u, kind in kinds.items():
                c.execute("insert into mail_rcpt(mail_id,username,kind) values(?,?,?)", (mid, u, kind))
            c.execute("update mails set sent=1, sender_folder='sent', created=? where id=?", (now_iso(), mid))
            if ext:
                self._enqueue_external_mail(c, me, mid, subject, body, ext)
            self._audit(c, actor, "mail_send", None, {"mail": mid, "internal": len(kinds), "external": len(ext)})
            return {"id": mid, "recipients": len(kinds), "external": len(ext)}

    def _mail_dir(self):
        d = self.dir / "mail"
        d.mkdir(exist_ok=True)
        return d

    def _mail_store(self, c, mid, fname, data):
        name = safe_filename(fname)
        rows = c.execute("select idx, size from mail_att where mail_id=?", (mid,)).fetchall()
        if len(rows) >= MAX_FILES:
            raise Invalid(f"첨부는 최대 {MAX_FILES}개")
        if len(data) > MAX_FILE_BYTES:
            raise Invalid(f"첨부 1개 최대 {MAX_FILE_BYTES // 1048576}MB")
        if sum(r["size"] for r in rows) + len(data) > MAX_TOTAL_BYTES:
            raise Invalid(f"첨부 합계 최대 {MAX_TOTAL_BYTES // 1048576}MB")
        idx = max([r["idx"] for r in rows] + [0]) + 1
        stored = f"{mid:07d}/{idx:02d}{os.path.splitext(name)[1]}"
        (self._mail_dir() / f"{mid:07d}").mkdir(exist_ok=True)
        c.execute("insert into mail_att(mail_id,idx,name,stored,size,sha256) values(?,?,?,?,?,?)",
                  (mid, idx, name, stored, len(data), sha256(data)))
        c.after.append((_atomic_write, self._mail_dir() / stored, data))

    # ------------------------------------------------------------ read / list
    def _mail_row(self, c, actor, mid):
        """읽기 권한: 보낸 사람(삭제 전) 또는 받는 사람 행(영구삭제 전)."""
        m = c.execute("select * from mails where id=?", (int(mid),)).fetchone()
        if not m:
            raise NotFound("메일이 없거나 권한이 없습니다")
        if m["sender"] == actor and m["sender_folder"] != "deleted":
            return m
        r = c.execute("select * from mail_rcpt where mail_id=? and username=? and folder!='deleted'",
                      (m["id"], actor)).fetchone()
        if not r or not m["sent"]:
            raise NotFound("메일이 없거나 권한이 없습니다")
        return m

    def mail_read(self, actor, mid):
        with self._tx() as c:
            self._user(c, actor)
            m = self._mail_row(c, actor, mid)
            mine = c.execute("select * from mail_rcpt where mail_id=? and username=?", (m["id"], actor)).fetchone()
            if mine and not mine["read_at"]:
                c.execute("update mail_rcpt set read_at=? where mail_id=? and username=?", (now_iso(), m["id"], actor))
            names = {r["username"]: r["name"] for r in c.execute("select username,name from users")}
            rc = [dict(r) for r in c.execute("select username,kind,read_at from mail_rcpt where mail_id=? order by kind,"
                                             "username", (m["id"],))]
            is_sender = m["sender"] == actor
            out = {"id": m["id"], "sender": m["sender"], "sender_name": names.get(m["sender"]), "subject": m["subject"],
                   "body": m["body"], "created": m["created"], "sent": bool(m["sent"]), "reply_to": m["reply_to"],
                   "forward_of": m["forward_of"], "to_spec": m["to_spec"], "cc_spec": m["cc_spec"],
                   "to": [dict(x, name=names.get(x["username"])) for x in rc if x["kind"] == "to"],
                   "cc": [dict(x, name=names.get(x["username"])) for x in rc if x["kind"] == "cc"],
                   "attachments": [dict(a) for a in c.execute(
                       "select idx,name,size from mail_att where mail_id=? order by idx", (m["id"],))],
                   "folder": m["sender_folder"] if is_sender else (mine["folder"] if mine else None),
                   "my_kind": mine["kind"] if mine else None, "is_sender": is_sender}
            if is_sender:  # 숨은참조·읽음 확인·외부 주소는 보낸 사람에게만
                out["bcc_spec"], out["ext_spec"] = m["bcc_spec"], m["ext_spec"]
                out["bcc"] = [dict(x, name=names.get(x["username"])) for x in rc if x["kind"] == "bcc"]
            else:
                for x in out["to"] + out["cc"]:
                    x.pop("read_at", None)
            return out

    def mail_list(self, actor, folder="inbox", q=None, unread=False, limit=200):
        if folder not in FOLDERS:
            raise Invalid(f"편지함은 {list(FOLDERS)}")
        with self._ro() as c:
            self._user(c, actor)
            args = [actor]
            if folder == "inbox":
                w = "r.username=? and r.folder='inbox' and m.sent=1"
            elif folder == "trash":
                w = "((r.username=? and r.folder='trash' and m.sent=1) or (m.sender=? and m.sender_folder='trash'))"
                args.append(actor)
            else:
                w = "m.sender=? and m.sender_folder=?"
                args.append(folder)
            if unread:
                w += " and r.read_at is null"
            if q:
                w += " and (m.subject like ? or m.body like ? or su.name like ?)"
                args += [f"%{q[:100]}%"] * 3
            join = "left join mail_rcpt r on r.mail_id=m.id and r.username=?" if folder in ("sent", "drafts") else \
                "left join mail_rcpt r on r.mail_id=m.id"
            if folder in ("sent", "drafts"):
                args.insert(0, actor)
            rows = c.execute(
                "select distinct m.id,m.sender,su.name as sender_name,m.subject,m.created,m.sent,m.to_spec,r.read_at,"
                "(select count(*) from mail_att a where a.mail_id=m.id) as files from mails m "
                f"left join users su on su.username=m.sender {join} where {w} order by m.id desc limit {int(limit)}", args)
            return [dict(r) for r in rows]

    def unread_mail_count(self, actor):
        with self._ro() as c:
            return c.execute("select count(*) from mail_rcpt r join mails m on m.id=r.mail_id where r.username=? and "
                             "r.folder='inbox' and r.read_at is null and m.sent=1", (actor,)).fetchone()[0]

    def mail_attachment(self, actor, mid, idx):
        with self._ro() as c:
            m = self._mail_row(c, actor, mid)
            a = c.execute("select * from mail_att where mail_id=? and idx=?", (m["id"], int(idx))).fetchone()
        if not a:
            raise NotFound("첨부 없음")
        base = self._mail_dir().resolve()
        p = (base / a["stored"]).resolve()
        if base not in p.parents:
            raise NotFound("첨부 없음")
        return a["name"], p

    def mail_move(self, actor, mid, to_folder):
        """휴지통으로/되살리기/영구삭제('deleted', 휴지통에서만). 다른 사람의 편지함에는 영향 없음."""
        if to_folder not in ("trash", "restore", "deleted"):
            raise Invalid("trash|restore|deleted")
        with self._tx() as c:
            m = self._mail_row(c, actor, mid)
            if m["sender"] == actor and (m["sender_folder"] in ("sent", "drafts", "trash")) and \
                    not c.execute("select 1 from mail_rcpt where mail_id=? and username=? and folder!='deleted'",
                                  (m["id"], actor)).fetchone():
                cur = m["sender_folder"]
                if to_folder == "deleted" and cur != "trash" and cur != "drafts":
                    raise Invalid("휴지통에서만 영구삭제할 수 있습니다")
                new = {"trash": "trash", "deleted": "deleted", "restore": "sent" if m["sent"] else "drafts"}[to_folder]
                c.execute("update mails set sender_folder=? where id=?", (new, m["id"]))
            else:
                r = c.execute("select folder from mail_rcpt where mail_id=? and username=?", (m["id"], actor)).fetchone()
                if to_folder == "deleted" and r["folder"] != "trash":
                    raise Invalid("휴지통에서만 영구삭제할 수 있습니다")
                new = {"trash": "trash", "deleted": "deleted", "restore": "inbox"}[to_folder]
                c.execute("update mail_rcpt set folder=? where mail_id=? and username=?", (new, m["id"], actor))
        return {"id": m["id"], "folder": new}

    def mail_compose_from(self, actor, mid, mode):
        """답장(reply)/전체답장(replyall)/전달(forward) 미리 채움."""
        m = self.mail_read(actor, mid)
        q = "\n\n----- 원본 메일 -----\n보낸 사람: {} ({})\n날짜: {}\n제목: {}\n\n{}".format(
            m["sender_name"], m["sender"], m["created"][:16], m["subject"], m["body"])
        if mode == "forward":
            return {"subject": "FW: " + m["subject"], "body": q, "forward_of": m["id"], "to": "", "cc": ""}
        to = m["sender"]
        cc = ""
        if mode == "replyall":
            others = [x["username"] for x in m["to"] + m["cc"] if x["username"] not in (actor, m["sender"])]
            cc = ", ".join(others)
        return {"subject": ("RE: " + m["subject"]) if not m["subject"].startswith("RE:") else m["subject"],
                "body": q, "reply_to": m["id"], "to": to, "cc": cc}

    # ------------------------------------------------------------ SMTP settings (admin)
    def _smtp_path(self):
        return self.dir / "smtp.json"

    def _smtp_conf(self):
        try:
            d = json.loads(self._smtp_path().read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            d = {}
        return dict(SMTP_DEFAULT, **{k: v for k, v in d.items() if k in SMTP_DEFAULT})

    def smtp_get(self, actor):
        self._require_admin(actor)
        d = self._smtp_conf()
        d["password"] = "********" if d["password"] else ""
        with self._ro() as c:
            d["outbox"] = {r["status"]: r["n"] for r in c.execute("select status, count(*) n from outbox group by status")}
        return d

    def smtp_set(self, actor, **kw):
        self._require_admin(actor)
        d = self._smtp_conf()
        for k, v in kw.items():
            if k not in SMTP_DEFAULT or v is None:
                continue
            if k == "password" and v in ("********", ""):
                continue  # 빈 값/가림표 = 기존 유지
            d[k] = v
        d["host"] = _one_line(d["host"], 253)
        if d["host"] and not re.fullmatch(r"[A-Za-z0-9.-]+", d["host"]):
            raise Invalid("SMTP 호스트 형식 오류")
        try:
            d["port"] = int(d["port"])
            d["rate_per_hour"] = max(1, min(10000, int(d["rate_per_hour"])))
            d["user_external_per_hour"] = max(0, min(1000, int(d["user_external_per_hour"])))
        except (TypeError, ValueError):
            raise Invalid("포트·한도는 숫자")
        if not 1 <= d["port"] <= 65535:
            raise Invalid("포트 범위 오류")
        if d["security"] not in ("starttls", "ssl", "none"):
            raise Invalid("보안은 starttls|ssl|none")
        for b in ("enabled", "notify_approvals", "allow_external_mail"):
            d[b] = d[b] if isinstance(d[b], bool) else str(d[b]).lower() in ("1", "on", "true", "yes")
        d["user"] = _one_line(d["user"], 200)
        d["from_name"] = _one_line(d["from_name"], 60)
        if d["from_addr"] and not valid_addr(d["from_addr"]):
            raise Invalid("보내는 주소 형식 오류")
        if d["enabled"] and not (d["host"] and d["from_addr"]):
            raise Invalid("사용하려면 호스트와 보내는 주소가 필요합니다")
        _atomic_write(self._smtp_path(), json.dumps(d, ensure_ascii=False).encode("utf-8"))
        _try_chmod(self._smtp_path(), 0o600)
        with self._tx() as c:
            self._audit(c, actor, "smtp_set", None, {k: v for k, v in d.items() if k != "password"})
        return self.smtp_get(actor)

    # ------------------------------------------------------------ outbox / delivery
    def _enqueue(self, c, kind, to_addr, subject, body, requested_by):
        c.execute("insert into outbox(kind,to_addr,subject,body,created,requested_by) values(?,?,?,?,?,?)",
                  (kind, to_addr, _one_line(subject, 200), body[:100_000], now_iso(), requested_by))

    def _notify_external(self, c, to_user, text, doc_id):
        """결재 알림(쪽지와 함께) → 사원 외부 메일. 설정 꺼져 있으면 아무 것도 안 함."""
        conf = self._smtp_conf()
        if not (conf["enabled"] and conf["notify_approvals"]):
            return
        r = c.execute("select email from users where username=? and active=1", (to_user,)).fetchone()
        if r and r["email"] and valid_addr(r["email"]):
            since = _iso_from_ts(time.time() - 3600)
            n, dup = c.execute("select count(*), sum(subject=?) from outbox where kind='notify' and to_addr=? and created>?",
                               (_one_line(text, 200), r["email"], since)).fetchone()
            if dup or n >= NOTIFY_PER_HOUR:  # 같은 알림 반복·폭주 방지(쪽지는 그대로 감)
                return
            self._enqueue(c, "notify", r["email"], text,
                          f"{text}\n\n전자결재에 로그인해 확인하세요(문서 #{doc_id}).\n이 메일은 발신 전용입니다.", "system")

    def _enqueue_external_mail(self, c, me, mid, subject, body, addrs):
        conf = self._smtp_conf()
        if not (conf["enabled"] and conf["allow_external_mail"]):
            raise Forbidden("외부 메일 보내기가 꺼져 있습니다(관리자 설정)")
        since = time.time() - 3600
        n = c.execute("select count(*) from outbox where requested_by=? and kind='mail' and created>?",
                      (me["username"], _iso_from_ts(since))).fetchone()[0]
        if n + len(addrs) > conf["user_external_per_hour"]:
            raise Invalid(f"외부 메일은 1시간에 {conf['user_external_per_hour']}통까지입니다")
        if c.execute("select count(*) from mail_att where mail_id=?", (mid,)).fetchone()[0]:
            body += "\n\n(첨부 파일은 사내 메일에서 확인하세요)"
        for a in addrs:
            self._enqueue(c, "mail", a, subject, f"{body}\n\n— {me['name']} ({me['dept']}) 님이 전자결재 사내 메일로 보냄",
                          me["username"])
        self._audit(c, me["username"], "mail_external", None, {"mail": mid, "to": addrs})

    def _build_message(self, conf, to_addr, subject, body):
        msg = EmailMessage()  # 헤더 줄바꿈은 EmailMessage 가 거부 + _one_line 로 사전 제거
        msg["From"] = formataddr((_one_line(conf["from_name"], 60), conf["from_addr"]))
        msg["To"] = to_addr
        msg["Subject"] = _one_line(subject, 200)
        msg["Message-ID"] = make_msgid(domain=conf["from_addr"].split("@")[1])
        msg["Auto-Submitted"] = "auto-generated"
        msg.set_content(body)
        return msg

    def _smtp_send(self, conf, to_addr, subject, body):
        if not valid_addr(to_addr):
            raise Invalid("받는 주소 형식 오류")
        msg = self._build_message(conf, to_addr, subject, body)
        ctx = ssl.create_default_context()
        if conf["security"] == "ssl":
            s = smtplib.SMTP_SSL(conf["host"], conf["port"], timeout=20, context=ctx)
        else:
            s = smtplib.SMTP(conf["host"], conf["port"], timeout=20)
        try:
            if conf["security"] == "starttls":
                s.starttls(context=ctx)
            if conf["user"]:
                s.login(conf["user"], conf["password"])
            s.send_message(msg, from_addr=conf["from_addr"], to_addrs=[to_addr])
        finally:
            try:
                s.quit()
            except Exception:
                pass

    def smtp_test(self, actor, to_addr):
        self._require_admin(actor)
        conf = self._smtp_conf()
        if not (conf["host"] and conf["from_addr"]):
            raise Invalid("호스트·보내는 주소를 먼저 설정하세요")
        to = valid_addr(to_addr)
        if not to:
            raise Invalid("받는 주소 형식 오류")
        try:
            self._smtp_send(conf, to, "[전자결재] SMTP 시험 메일", "SMTP 설정 시험 메일입니다.")
            ok, err = True, ""
        except Exception as e:  # noqa
            ok, err = False, f"{type(e).__name__}: {str(e)[:200]}"
        with self._tx() as c:
            self._audit(c, actor, "smtp_test", None, {"to": to, "ok": ok, "error": err})
        return {"ok": ok, "error": err}

    def deliver_outbox(self, limit=50):
        """대기 메일 발송(시간당 한도). 웹 서버 백그라운드 스레드·CLI `mail flush` 가 호출."""
        conf = self._smtp_conf()
        if not conf["enabled"]:
            return {"sent": 0, "failed": 0, "skipped": "disabled"}
        with self._tx() as c:
            sent_hour = c.execute("select count(*) from outbox where status='sent' and sent_at>?",
                                  (_iso_from_ts(time.time() - 3600),)).fetchone()[0]
            room = max(0, min(limit, conf["rate_per_hour"] - sent_hour))
            rows = c.execute("select * from outbox where status='queued' order by id limit ?", (room,)).fetchall()
            for r in rows:
                c.execute("update outbox set status='sending', tries=tries+1 where id=?", (r["id"],))
        sent = failed = 0
        for r in rows:
            try:
                self._smtp_send(conf, r["to_addr"], r["subject"], r["body"])
                st, err = "sent", None
                sent += 1
            except Exception as e:  # noqa
                st = "failed" if r["tries"] + 1 >= 3 else "queued"
                err = f"{type(e).__name__}: {str(e)[:200]}"
                failed += 1
            with self._tx() as c:
                c.execute("update outbox set status=?, error=?, sent_at=? where id=?",
                          (st, err, now_iso() if st == "sent" else None, r["id"]))
                self._audit(c, "system", "smtp_" + ("sent" if st == "sent" else "fail"), None,
                            {"outbox": r["id"], "kind": r["kind"], "to": r["to_addr"], "error": err})
        return {"sent": sent, "failed": failed}

    def start_mail_worker(self, interval=30):
        def loop():
            while True:
                try:
                    self.deliver_outbox()
                except Exception:
                    pass
                time.sleep(interval)
        t = threading.Thread(target=loop, daemon=True, name="gyeol-mail")
        t.start()
        return t


def _iso_from_ts(ts):
    import datetime as _dt
    return _dt.datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")
