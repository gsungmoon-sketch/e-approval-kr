import re
import socket
import threading
import unittest

from helpers import rmtree, world
from test_web import Client, multipart

from gyeoljae.core import Forbidden, Invalid, NotFound
from gyeoljae.web import make_server


class FakeSMTP:
    """테스트용 최소 SMTP 서버(평문). 받은 DATA 를 기록."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self.messages = []
        threading.Thread(target=self.loop, daemon=True).start()

    def loop(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self.handle, args=(conn,), daemon=True).start()

    def handle(self, conn):
        f = conn.makefile("rwb")
        w = lambda s: (f.write(s.encode() + b"\r\n"), f.flush())  # noqa: E731
        w("220 fake")
        rcpts = []
        while True:
            line = f.readline()
            if not line:
                break
            cmd = line.decode("utf-8", "replace").strip()
            up = cmd.upper()
            if up.startswith("EHLO") or up.startswith("HELO"):
                w("250-fake\r\n250 8BITMIME")
            elif up.startswith("RCPT"):
                rcpts.append(cmd)
                w("250 ok")
            elif up == "DATA":
                w("354 go")
                data = b""
                while True:
                    ln = f.readline()
                    if ln in (b".\r\n", b""):
                        break
                    data += ln
                self.messages.append({"rcpt": list(rcpts), "data": data.decode("utf-8", "replace")})
                rcpts.clear()
                w("250 queued")
            elif up == "QUIT":
                w("221 bye")
                break
            else:
                w("250 ok")
        conn.close()


class MailCoreTest(unittest.TestCase):
    def setUp(self):
        self.app, self.d = world()

    def tearDown(self):
        rmtree(self.d)

    def test_privacy_and_bcc(self):
        a = self.app
        r = a.mail_send("dev1", "devlead", "@SAL", "hr", "회의", "본문", [("../../회의록.pdf", b"%PDF")])
        self.assertEqual(r["recipients"], 4)
        for u in ("devlead", "sal1", "sallead", "hr", "dev1"):
            m = a.mail_read(u, r["id"])
            self.assertEqual(m["subject"], "회의")
            if u != "dev1":
                self.assertNotIn("bcc", m)
                self.assertNotIn("bcc_spec", m)
                self.assertFalse(any("read_at" in x for x in m["to"] + m["cc"]))
        self.assertEqual([x["username"] for x in a.mail_read("dev1", r["id"])["bcc"]], ["hr"])
        for u in ("ceo", "admin"):
            with self.assertRaises(NotFound):
                a.mail_read(u, r["id"])
            with self.assertRaises(NotFound):
                a.mail_attachment(u, r["id"], 1)
        name, p = a.mail_attachment("hr", r["id"], 1)
        self.assertEqual(name, "회의록.pdf")
        self.assertNotIn("..", str(p))
        with self.assertRaises(Invalid):
            a.mail_send("dev1", "devlead", subject="x", attachments=[("a.exe", b"MZ")])
        with self.assertRaises(Forbidden):
            a.mail_send("dev1", "@전체", subject="전체")
        self.assertGreater(a.mail_send("ceo", "@전체", subject="전체")["recipients"], 5)

    def test_folders_reply_forward(self):
        a = self.app
        r = a.mail_send("dev1", "devlead, sal1", "", "", "원본", "내용", [("a.pdf", b"1")])
        self.assertEqual(a.unread_mail_count("devlead"), 1)
        a.mail_read("devlead", r["id"])
        self.assertEqual(a.unread_mail_count("devlead"), 0)
        pre = a.mail_compose_from("devlead", r["id"], "replyall")
        self.assertEqual((pre["to"], pre["cc"]), ("dev1", "sal1"))
        fw = a.mail_compose_from("sal1", r["id"], "forward")
        f = a.mail_send("sal1", "sallead", subject=fw["subject"], body=fw["body"], forward_of=fw["forward_of"])
        self.assertEqual(len(a.mail_read("sallead", f["id"])["attachments"]), 1)
        with self.assertRaises(NotFound):
            a.mail_send("ceo", "hr", subject="x", forward_of=r["id"])  # 못 보는 메일은 전달 불가
        a.mail_move("devlead", r["id"], "trash")
        self.assertEqual([m["id"] for m in a.mail_list("devlead", "trash")], [r["id"]])
        self.assertEqual(len(a.mail_list("sal1", "inbox")), 1)  # 다른 사람 편지함엔 영향 없음
        a.mail_move("devlead", r["id"], "deleted")
        with self.assertRaises(NotFound):
            a.mail_read("devlead", r["id"])
        self.assertEqual(a.mail_read("dev1", r["id"])["subject"], "원본")
        d = a.mail_send("dev1", "devlead", subject="임시", save_draft=True)
        self.assertEqual([m["id"] for m in a.mail_list("dev1", "drafts")], [d["id"]])
        with self.assertRaises(NotFound):
            a.mail_read("devlead", d["id"])
        a.mail_send("dev1", "devlead", subject="임시→보냄", draft_id=d["id"])
        self.assertEqual(a.mail_list("dev1", "drafts"), [])
        self.assertEqual(len(a.mail_list("devlead", "inbox", q="보냄")), 1)

    def test_audit3_regressions(self):
        a = self.app
        with self.assertRaises(Forbidden):  # 부서 나열로 전체 메일 우회
            a.mail_send("dev1", "@DEV, @SAL", subject="x")
        with self.assertRaises(Forbidden):
            a.mail_send("dev1", "@DEV", cc="@MGT", subject="x")
        a.mail_send("dev1", "@SAL", subject="한 부서는 됨")
        with self.assertRaises(Invalid):  # 긴 목록은 자르지 않고 거부
            a.mail_send("dev1", ",".join(["devlead"] * 400), subject="x")
        d = a.mail_send("dev1", "devlead", subject="임시", save_draft=True)
        a.mail_move("dev1", d["id"], "trash")
        with self.assertRaises(NotFound):  # 휴지통 임시보관은 보낼 수 없음
            a.mail_send("dev1", "devlead", subject="x", draft_id=d["id"])
        a.smtp_set("admin", host="127.0.0.1", port=1, security="none", from_addr="noreply@example.com",
                   enabled=True, notify_approvals=True)
        a.emp_edit("admin", "devlead", email="lead@example.com")
        for i in range(15):
            a.create_draft("dev1", f"폭주{i}", "b", ["devlead"], submit=True)
        with a._ro() as c:
            n = c.execute("select count(*) from outbox where kind='notify'").fetchone()[0]
        self.assertLessEqual(n, 10)

    def test_smtp_relay(self):
        a = self.app
        srv = FakeSMTP()
        with self.assertRaises(Forbidden):
            a.smtp_set("devlead", host="x")
        a.smtp_set("admin", host="127.0.0.1", port=srv.port, security="none", from_addr="noreply@example.com",
                   enabled=True, notify_approvals=True, allow_external_mail=False, password="secret")
        self.assertEqual(a.smtp_get("admin")["password"], "********")
        a.emp_edit("admin", "devlead", email="lead@example.com")
        m = a.create_draft("dev1", "제목\r\nBcc: victim@evil.example", "b", ["devlead"], submit=True)
        r = a.deliver_outbox()
        self.assertEqual(r["sent"], 1)
        data = srv.messages[0]["data"]
        headers = data.split("\r\n\r\n", 1)[0]
        self.assertFalse(re.search(r"(?im)^bcc:", headers))  # 헤더 줄로 주입되지 않음
        self.assertIn("lead@example.com", srv.messages[0]["rcpt"][0])
        self.assertEqual(len(srv.messages[0]["rcpt"]), 1)
        with self.assertRaises(Forbidden):  # 외부 보내기 꺼짐
            a.mail_send("dev1", "", subject="x", ext_to="out@example.com")
        a.smtp_set("admin", allow_external_mail=True, user_external_per_hour=2)
        with self.assertRaises(Invalid):
            a.mail_send("dev1", "", subject="x", ext_to="a@example.com\r\nRCPT TO:<b@evil>")
        a.mail_send("dev1", "", subject="밖으로", ext_to="out@example.com, out2@example.com")
        with self.assertRaises(Invalid):
            a.mail_send("dev1", "", subject="한도", ext_to="out3@example.com")
        a.deliver_outbox()
        self.assertEqual(sorted(re.search(r"<(.+)>", m["rcpt"][0]).group(1) for m in srv.messages[1:]),
                         ["out2@example.com", "out@example.com"])
        self.assertTrue(a.smtp_test("admin", "t@example.com")["ok"])
        self.assertTrue(any(e["action"] == "mail_external" for e in a.audit_list("admin")))
        self.assertTrue(a.verify_audit()["ok"])


class MailWebTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app, cls.d = world()
        cls.srv = make_server(cls.app, "127.0.0.1", 0)
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        rmtree(cls.d)

    def test_mail_web(self):
        c = Client(self.base)
        c.login("dev1")
        tok = c.csrf("/mail/new")
        data, ct = multipart({"csrf": tok, "to": "devlead", "cc": "", "bcc": "hr", "subject": "<script>s</script>안녕",
                              "body": "<img src=x onerror=1>", "go": "send"}, [("files", ("a.pdf", b"%PDF"))])
        st, _, h = c.req("/mail/new", data, ctype=ct)
        self.assertEqual(st, 303)
        mid = self.app.mail_list("devlead")[0]["id"]
        l = Client(self.base)
        l.login("devlead")
        home = l.req("/")[1]
        self.assertIn("메일", home)
        st, body, _ = l.req(f"/mail/{mid}")
        self.assertEqual(st, 200)
        self.assertNotIn("<script>s", body)
        self.assertNotIn("<img src=x", body)
        self.assertNotIn("숨은참조", body)  # 숨은참조 숨김
        self.assertIn("숨은참조: 인사", c.req(f"/mail/{mid}")[1])  # 보낸 사람은 보임
        o = Client(self.base)
        o.login("sal1")
        self.assertEqual(o.req(f"/mail/{mid}")[0], 404)
        self.assertEqual(o.req(f"/mail/{mid}/file/1")[0], 404)
        self.assertEqual(o.req(f"/mail/{mid}/trash", {"csrf": o.csrf("/mail")})[0], 404)
        self.assertEqual(l.req(f"/mail/{mid}/trash", {})[0], 403)  # CSRF
        self.assertEqual(o.req("/admin/smtp")[0], 403)


if __name__ == "__main__":
    unittest.main()
