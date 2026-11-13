import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from helpers import rmtree
from test_web import Client

from gyeoljae.core import App, Forbidden, Invalid
from gyeoljae.web import make_server
from gyeoljae import web_setup


class SetupWizardTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="gyeol-setup-")
        self.app = App(self.d)
        self.srv = make_server(self.app, "127.0.0.1", 0)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        rmtree(self.d)

    def test_wizard(self):
        c = Client(self.base)
        st, body, _ = c.req("/healthz")
        self.assertEqual((st, json.loads(body)["ok"]), (200, True))
        st, _, h = c.req("/")
        self.assertEqual((st, h.get("Location")), (303, "/setup"))
        self.assertEqual(c.req("/login")[0], 303)
        st, body, _ = c.req("/setup")
        self.assertIn("처음 설정", body)
        form = {"admin_id": "boss", "admin_name": "대표", "pw1": "longpass99", "pw2": "longpass99", "dept_code": "ADM",
                "dept_name": "관리", "bind": "lan", "port": "8131", "backup_dir": str(Path(self.d) / "bk")}
        self.assertEqual(c.req("/setup", dict(form, token="x"))[0], 403)  # 토큰 없으면 거부
        st, body, _ = c.req("/setup", dict(form, token=web_setup.TOKEN, pw2="different1"))
        self.assertEqual(st, 400)
        self.assertIn("value='boss'", body)
        st, body, _ = c.req("/setup", dict(form, token=web_setup.TOKEN))
        self.assertEqual(st, 200)
        self.assertIn("다시 시작", body)
        self.assertEqual(self.app.server_conf()["host"], "0.0.0.0")
        self.assertEqual(self.app.server_conf()["port"], 8131)
        self.assertEqual(c.req("/setup")[0], 303)  # 끝난 뒤엔 로그인으로
        self.assertEqual(len(list((Path(self.d) / "bk").glob("e-approval-kr-*.zip"))), 1)  # 첫 백업 즉시
        st, _, h = c.req("/login", {"username": "boss", "password": "longpass99"})
        self.assertEqual(st, 303)
        with self.assertRaises(Forbidden):  # 마법사 경로로 재설정 불가
            self.app.server_conf_set(None, host="127.0.0.1")

    def test_remote_refused(self):
        class H:
            headers = {}
            client_address = ("203.0.113.5", 5)
            qs, form = {}, {}
        self.assertFalse(web_setup._local(H))
        self.assertFalse(web_setup._key_ok(H))
        H.qs = {"key": "wrong"}
        self.assertFalse(web_setup._key_ok(H))
        H.qs = {"key": web_setup.REMOTE_TOKEN}
        self.assertTrue(web_setup._key_ok(H))
        H.client_address = ("127.0.0.1", 5)
        self.assertTrue(web_setup._local(H))

    def test_misc_cli_and_print(self):
        import io
        import subprocess
        import sys
        a = self.app
        a.bootstrap("ADM", "관리", "admin", "관리자", "adminpass1")
        a.setting_set("admin", "allow_freeform", True)
        env = {"GYEOLJAE_DATA": self.d, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
               "PATH": "/usr/bin:/bin", "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", "")}
        r = subprocess.run([sys.executable, "-m", "gyeoljae", "-u", "admin", "setting", "list"], env=env,
                           capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertIn("doc_number: {dept}-{year}-{seq}", r.stdout)
        r = subprocess.run([sys.executable, "-m", "gyeoljae", "-u", "admin", "change", "approve", "1"], env=env,
                           capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL, timeout=60)
        self.assertIn("--password-file", r.stdout + r.stderr)  # 조용히 stdin 읽지 않음
        a.add_dept("admin", "DEV", "개발")
        a.emp_add("admin", "팀장", "DEV", "부서장", username="lead")
        tmp = a.emp_add("admin", "직원", "DEV", "팀원", username="kim")
        d = a.create_draft("kim", "<인쇄>", "본문", ["lead"], submit=True)
        pw = next(v for k, v in tmp.items() if "pass" in k)
        a.set_password("kim", "kim", "kimpass123", pw)
        c = Client(self.base)
        c.login("kim", "kimpass123")
        c.req("/me")
        st, body, _ = c.req(f"/doc/{d['id']}/print")
        self.assertEqual(st, 200)
        self.assertIn("Ctrl+P", body)
        self.assertNotIn("<인쇄>", body)
        self.assertNotIn("nav class=side", body)

    def test_conf_and_auto_backup(self):
        a = self.app
        a.bootstrap("ADM", "관리", "admin", "관리자", "adminpass1")
        for bad in ({"host": "evil host"}, {"port": "80"}, {"backup_dir": "relative/dir"}):
            with self.assertRaises(Invalid):
                a.server_conf_set("admin", **bad)
        bk = Path(self.d) / "bk"
        a.server_conf_set("admin", backup_dir=str(bk), backup_keep=2)
        (bk / "keep-me.zip").write_bytes(b"x")  # 남의 파일은 안 지움
        t0 = time.mktime((2026, 11, 1, 3, 0, 0, 0, 0, -1))
        made = [a.auto_backup(time.localtime(t0 + 86400 * i)) for i in range(4)]
        self.assertTrue(all(made))
        self.assertIsNone(a.auto_backup(time.localtime(t0 + 86400 * 3 + 60)))  # 같은 날 두 번 안 함
        names = sorted(p.name for p in bk.iterdir())
        self.assertEqual(len(names), 3)
        self.assertIn("keep-me.zip", names)
        self.assertEqual(json.loads((Path(self.d) / "server.json").read_text(encoding="utf-8"))["backup_keep"], 2)


if __name__ == "__main__":
    unittest.main()
