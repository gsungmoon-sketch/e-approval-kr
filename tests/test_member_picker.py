"""사업 문서함 참여자 고르기: 체크박스 목록(활성만, 이스케이프) + 검색 스크립트 + 쉼표 일괄 입력 유지, 서버 검증 그대로.
+ 같은 이름 여러 값(체크박스) 폼 읽기(urlencoded·multipart)."""
import re
import threading
import unittest
from pathlib import Path

from helpers import rmtree, world

from gyeoljae.web import make_server, parse_multipart

STATIC = Path(__file__).resolve().parents[1] / "src" / "gyeoljae" / "static"


class MemberPickerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app, cls.d = world()
        cls.app.add_user("admin", "old1", "퇴사자", "DEV", "user", "pass12345", "사원")
        cls.app.add_user("admin", "esc1", "<b>꺾쇠</b>'따옴", "SAL", "user", "pass12345", "사원")
        cls.app.set_active("admin", "old1", False)
        cls.srv = make_server(cls.app, "127.0.0.1", 0)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        rmtree(cls.d)

    def client(self, u="devlead"):
        from test_web import Client
        c = Client(self.base)
        c.login(u)
        return c

    def post(self, c, path, pairs):
        """같은 이름 여러 값(체크박스) — urlencoded 목록."""
        import urllib.parse
        data = urllib.parse.urlencode([("csrf", c.csrf(path))] + pairs).encode()
        return c.req(path, data, ctype="application/x-www-form-urlencoded")

    def test_picker_page(self):
        c = self.client()
        st, body, h = c.req("/docs/project")
        self.assertEqual(st, 200)
        self.assertIn("script-src 'self'", h["Content-Security-Policy"])
        self.assertNotIn("unsafe-inline", h["Content-Security-Policy"].split("script-src")[1].split(";")[0])
        self.assertIn("/static/member-picker.js?v=", body)
        self.assertNotRegex(body, r"<script>(?!</script>)")  # 인라인 스크립트 없음
        users = re.findall(r"name=pick_user value='([^']+)'", body)
        self.assertIn("dev1", users)
        self.assertIn("esc1", users)
        self.assertNotIn("old1", users)  # 비활성은 목록에 없음
        self.assertIn("&lt;b&gt;꺾쇠&lt;/b&gt;&#x27;따옴", body)
        self.assertNotIn("<b>꺾쇠", body)
        self.assertEqual(set(re.findall(r"name=pick_dept value='([^']+)'", body)), {"ADM", "MGT", "DEV", "SAL"})
        self.assertIn("name=depts", body)  # 쉼표 일괄 입력 칸 유지
        self.assertIn("name=users", body)
        st, js, h = c.req("/static/member-picker.js")
        self.assertEqual((st, js), (200, (STATIC / "member-picker.js").read_bytes().decode("utf-8")))
        # 일반 사용자 목록 화면에는 고르기 폼 없음(만들 권한 없음)
        st, body, h = self.client("dev1").req("/docs/project")
        self.assertNotIn("pick_user", body)
        self.assertNotIn("script-src", h["Content-Security-Policy"])

    def test_create_with_picks_and_bulk(self):
        c = self.client()
        st, _, h = self.post(c, "/docs/project", [("name", "고르기"), ("code", ""), ("members", "1"),
                                                  ("pick_dept", "DEV"), ("pick_dept", "SAL"),
                                                  ("pick_user", "hr"), ("pick_user", "ceo"),
                                                  ("depts", "mgt"), ("users", "sal1, hr")])
        self.assertEqual(st, 303)
        pid = int(re.search(r"/docs/project/(\d+)", h["Location"]).group(1))
        p = self.app.project_get("devlead", pid)
        self.assertEqual(sorted(p["depts"]), ["DEV", "MGT", "SAL"])
        self.assertEqual(sorted(p["users"]), ["ceo", "hr", "sal1"])
        # 설정 화면: 현재 참여자가 체크된 채 맨 앞에
        st, body, _ = c.req(f"/docs/project/{pid}")
        self.assertRegex(body, r"name=pick_user value='ceo' checked")
        self.assertRegex(body, r"name=pick_dept value='MGT' checked")
        self.assertNotRegex(body, r"name=pick_user value='dev1' checked")
        # 체크 해제 = 빠짐(일괄 칸은 비어 있음)
        st, _, _ = self.post(c, f"/docs/project/{pid}", [("name", "고르기"), ("code", ""), ("status", "open"),
                                                         ("members", "1"), ("pick_dept", "DEV"), ("pick_user", "hr"),
                                                         ("depts", ""), ("users", "")])
        self.assertEqual(st, 303)
        p = self.app.project_get("devlead", pid)
        self.assertEqual((p["depts"], p["users"]), (["DEV"], ["hr"]))
        audit = [e for e in self.app.audit_list("admin", limit=1000) if e["action"] == "project_update"][-1]
        self.assertEqual(audit["detail"]["changes"]["users"], [["ceo", "hr", "sal1"], ["hr"]])

    def test_bulk_only_old_style_and_server_validation(self):
        c = self.client()
        st, _, h = self.post(c, "/docs/project", [("name", "일괄"), ("depts", "DEV,SAL"), ("users", "dev1 sal1")])
        self.assertEqual(st, 303)
        pid = int(re.search(r"/docs/project/(\d+)", h["Location"]).group(1))
        p = self.app.project_get("devlead", pid)
        self.assertEqual((p["depts"], p["users"]), (["DEV", "SAL"], ["dev1", "sal1"]))
        st, body, _ = self.post(c, "/docs/project", [("name", "잘못"), ("pick_user", "nobody<x>")])
        self.assertEqual(st, 400)
        self.assertIn("없는 사용자: nobody&lt;x&gt;", body)
        st, body, _ = self.post(c, "/docs/project", [("name", "잘못2"), ("pick_dept", "ZZZ")])
        self.assertEqual(st, 400)
        self.assertIn("없는 부서 코드: ZZZ", body)
        self.assertNotIn("잘못", [x["name"] for x in self.app.project_list("devlead")])

    def test_inactive_member_kept(self):
        p = self.app.project_create("devlead", "퇴사자 포함", users=["old1", "dev1"])
        c = self.client()
        st, body, _ = c.req(f"/docs/project/{p['id']}")
        self.assertRegex(body, r"name=pick_user value='old1' checked> 퇴사자 .*?\(비활성\)")

    def test_multi_value_forms(self):
        # urlencoded: 문서 태그에 사업 두 개(예전엔 첫 값만 받던 것)
        a = self.app.project_create("devlead", "다중A", depts=["DEV"])
        b = self.app.project_create("devlead", "다중B", depts=["DEV"])
        did = self.app.create_draft("dev1", "다중", "본문", ["devlead"], form="general", fields={"purpose": "p"})["id"]
        c = self.client("dev1")
        st, _, _ = self.post(c, f"/doc/{did}/tags", [("what", "class"), ("dclass", "none"),
                                                     ("project", str(a["id"])), ("project", str(b["id"]))])
        self.assertEqual(st, 303)
        self.assertEqual(sorted(p["id"] for p in self.app.doc_tags("dev1", did)["projects"]), sorted([a["id"], b["id"]]))
        # multipart: 첫 값은 form, 모두는 .multi
        body = (b"--x\r\nContent-Disposition: form-data; name=\"k\"\r\n\r\n1\r\n"
                b"--x\r\nContent-Disposition: form-data; name=\"k\"\r\n\r\n2\r\n--x--\r\n")
        form, files = parse_multipart(body, "multipart/form-data; boundary=x")
        self.assertEqual((form["k"], form.multi["k"], files), ("1", ["1", "2"], []))


if __name__ == "__main__":
    unittest.main()
