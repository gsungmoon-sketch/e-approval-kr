import re
import threading
import unittest
import urllib.parse

from helpers import rmtree, world
from test_web import Client, multipart

from gyeoljae.core import Forbidden, Invalid, NotFound
from gyeoljae.web import make_server


class HomeCoreTest(unittest.TestCase):
    def setUp(self):
        self.app, self.d = world()

    def tearDown(self):
        rmtree(self.d)

    def test_home_cards(self):
        a = self.app
        a.board_post("admin", "notice", "일반 공지", "x")
        p = a.board_post("admin", "notice", "고정 공지", "x")
        a.board_pin("admin", p["id"])
        with self.assertRaises(Forbidden):
            a.board_pin("devlead", p["id"])
        m1 = a.create_draft("dev1", "첫째", "b", ["devlead"], submit=True)
        a.create_draft("dev1", "둘째", "b", ["devlead"], submit=True)
        r = a.create_draft("dev1", "반려될", "b", ["devlead"], submit=True)
        a.reject("devlead", r["id"], "다시 올려 주세요")
        h = a.home("devlead")
        self.assertEqual(h["notices"]["items"][0]["title"], "고정 공지")
        self.assertEqual([x["id"] for x in h["inbox"]["items"]], [m1["id"], m1["id"] + 1])  # 오래된 순
        d = a.home("dev1")
        self.assertEqual(d["progress"]["count"], 2)
        self.assertEqual(d["progress"]["items"][0]["cur_name"], "개발장")
        self.assertEqual(d["rejected"]["count"], 1)
        self.assertEqual(a.home("sal1")["inbox"]["count"], 0)

    def test_inactive_login_message(self):
        a = self.app
        a.emp_set_status("admin", "hr", "휴직")
        with self.assertRaisesRegex(Forbidden, "사용 중지"):
            a.login("hr", "pass12345", "3.3.3.3")
        with self.assertRaisesRegex(Forbidden, "틀립니다"):
            a.login("hr", "wrongwrong", "3.3.3.3")

    def test_library_permissions(self):
        a = self.app
        r = a.files_upload("dev1", "..\\..\\양식.hwpx", b"PK1")
        self.assertEqual(r["name"], "양식.hwpx")
        r2 = a.files_upload("devlead", "양식.HWPX", b"PK2", note="수정")
        self.assertEqual((r2["id"], r2["version"]), (r["id"], 2))  # 덮어쓰기 아님 → 새 판
        name, path = a.files_get("dev1", r["id"], 1)
        self.assertEqual(path.read_bytes(), b"PK1")
        self.assertEqual(a.files_get("dev1", r["id"])[1].read_bytes(), b"PK2")
        for u in ("sal1", "sallead", "admin"):
            with self.assertRaises(NotFound):
                a.files_get(u, r["id"])
            with self.assertRaises(NotFound):
                a.files_versions(u, r["id"])
        with self.assertRaises(NotFound):
            a.files_list("sal1", "dept", "DEV")
        self.assertEqual(a.files_list("sal1")["files"], [])
        with self.assertRaises(Forbidden):
            a.files_upload("dev1", "x.pdf", b"1", "org")
        o = a.files_upload("ceo", "규정.pdf", b"%PDF", "org")
        self.assertEqual(len(a.files_list("sal1", "org")["files"]), 1)
        with self.assertRaises(Forbidden):
            a.files_delete("sallead", o["id"])
        with self.assertRaises(Forbidden):
            a.files_new_version("sal1", o["id"], b"x")  # 전사 자료실은 부서장급만 새 판
        with self.assertRaises(Invalid):
            a.files_upload("dev1", "evil.exe", b"MZ")
        with self.assertRaises(Invalid):
            a.files_upload("dev1", "big.pdf", b"0" * (20 * 1024 * 1024 + 1))
        a.files_delete("dev1", r["id"])  # 처음 올린 사람
        with self.assertRaises(NotFound):
            a.files_get("devlead", r["id"])
        self.assertTrue(a.verify_audit()["ok"])

    def test_folders(self):
        a = self.app
        f = a.folder_create("dev1", "보관")
        m = a.create_draft("dev1", "담을", "b", ["devlead"], submit=True)
        s = a.create_draft("sal1", "남의", "b", ["sallead"], submit=True)
        a.folder_add("dev1", f["id"], m["id"])
        with self.assertRaises(NotFound):
            a.folder_add("dev1", f["id"], s["id"])  # 못 보는 문서는 못 담음
        with self.assertRaises(NotFound):
            a.folder_docs("sal1", f["id"])  # 남의 폴더
        self.assertEqual([d["id"] for d in a.folder_docs("dev1", f["id"])["docs"]], [m["id"]])
        a.folder_delete("dev1", f["id"])
        self.assertEqual(a.show("dev1", m["id"])["title"], "담을")

    def test_dept_box(self):
        a = self.app
        m = a.create_draft("dev1", "부서공개", "b", ["devlead"], visibility="dept", submit=True)
        p = a.create_draft("dev1", "비공개", "b", ["devlead"], submit=True)
        a.approve("devlead", m["id"])
        a.approve("devlead", p["id"])
        a.add_user("admin", "dev2", "개발2", "DEV", "user", "pass12345")
        self.assertEqual([r["id"] for r in a.list_box("dev2", "dept")], [m["id"]])
        self.assertEqual({r["id"] for r in a.list_box("devlead", "dept")}, {m["id"], p["id"]})
        self.assertEqual(a.list_box("sal1", "dept"), [])


class HomeWebTest(unittest.TestCase):
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

    def test_home_and_nav(self):
        c = Client(self.base)
        st, _, h = c.login("devlead")
        self.assertEqual(h["Location"], "/")
        self.app.create_draft("dev1", "<script>x</script>결재요청", "b", ["devlead"], submit=True)
        st, body, _ = c.req("/")
        self.assertEqual(st, 200)
        for t in ("공지사항", "결재대기", "결재진행", "결재완료", "반려", "기안하기", "nav class=bottom", "nav class=side"):
            self.assertIn(t, body)
        self.assertIn("class=badge", body)
        self.assertNotIn("<script>x", body)
        self.assertNotIn("관리</a>", body)  # 관리자 아님
        self.assertEqual(c.req("/menu")[0], 200)
        self.assertIn("type=expense", body)
        self.assertIn("새 기안 · 지출", c.req("/new?type=expense")[1])

    def test_next_and_expired(self):
        c = Client(self.base)
        st, _, h = c.req("/files?scope=org")
        self.assertIn("next=", h["Location"])
        st, _, h = c.req("/login?next=" + urllib.parse.quote("/files?scope=org"),
                         {"username": "dev1", "password": "pass12345", "next": "/files?scope=org"})
        self.assertEqual(h["Location"], "/files?scope=org")
        st, _, h = Client(self.base).req("/login", {"username": "dev1", "password": "pass12345",
                                                    "next": "//evil.example/x"})
        self.assertEqual(h["Location"], "/")
        e = Client(self.base)
        e.jar.set_cookie(__import__("http.cookiejar").cookiejar.Cookie(
            0, "gyeol_sid", "stale", None, False, "127.0.0.1", False, False, "/", True, False, None, False, None, None, {}))
        st, _, h = e.req("/docs")
        self.assertIn("expired=1", h["Location"])

    def test_files_web(self):
        c = Client(self.base)
        c.login("dev1")
        tok = c.csrf("/files")
        data, ct = multipart({"csrf": tok, "scope": "dept", "note": "<b>메모</b>"},
                             [("file", ("../../<img src=x>.pdf", b"%PDF-1"))])
        st, _, h = c.req("/files", data, ctype=ct)
        self.assertEqual(st, 303, _)
        fid = h["Location"].rsplit("/", 1)[1]
        st, body, _ = c.req(f"/files/{fid}")
        self.assertNotIn("<img src=x", body)
        self.assertNotIn("<b>메모", body)
        st, raw, h = c.req(f"/files/{fid}/get")
        self.assertEqual(h["Content-Type"], "application/octet-stream")
        self.assertIn("attachment", h["Content-Disposition"])
        o = Client(self.base)
        o.login("sal1")
        self.assertEqual(o.req(f"/files/{fid}")[0], 404)
        self.assertEqual(o.req(f"/files/{fid}/get")[0], 404)
        data, ct = multipart({"csrf": o.csrf("/files"), "note": ""}, [("file", ("x.pdf", b"1"))])
        self.assertEqual(o.req(f"/files/{fid}/version", data, ctype=ct)[0], 404)
        self.assertNotIn("올리기</button>", o.req("/files?scope=org")[1])  # 전사 자료실 읽기 전용
        self.assertEqual(c.req("/files", {"scope": "dept"})[0], 403)  # CSRF 없음

    def test_folder_web(self):
        c = Client(self.base)
        c.login("hr")
        tok = c.csrf("/docs")
        c.req("/docs", {"csrf": tok, "name": "<i>폴더</i>"})
        body = c.req("/docs")[1]
        self.assertIn("&lt;i&gt;폴더", body)
        fid = re.search(r"/docs/folder/(\d+)", body).group(1)
        o = Client(self.base)
        o.login("sal1")
        self.assertEqual(o.req(f"/docs/folder/{fid}")[0], 404)


if __name__ == "__main__":
    unittest.main()
