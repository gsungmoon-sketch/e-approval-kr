"""기안 자동 저장(go=auto): 임시저장과 같은 문서·권한, 첨부는 빼고, 감사는 자동 저장 묶음마다 1건, '새 기안'의 이어 쓰기 안내."""
import json
import re
import threading
import unittest

from helpers import rmtree, world


def audits(app, did):
    with app._ro() as c:
        return [json.loads(r["line"]) for r in c.execute("select line from audit_log order by seq")
                if json.loads(r["line"]).get("doc") == did]


class AutosaveTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from gyeoljae.web import make_server
        cls.app, cls.d = world()
        cls.srv = make_server(cls.app, "127.0.0.1", 0)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        rmtree(cls.d)

    def client(self, user):
        from test_web import Client
        c = Client(self.base)
        c.login(user)
        return c

    def post(self, c, path, fields, files=()):
        from test_web import multipart
        data, ct = multipart(fields, list(files))
        st, body, h = c.req(path, data, ctype=ct)
        return st, body, h

    def test_auto_create_edit_audit_and_privacy(self):
        c = self.client("dev1")
        tok = c.csrf("/new?form=general")
        base = {"csrf": tok, "form": "general", "title": "", "body": "", "line": "devlead", "cc": "", "go": "auto",
                "doc_html": "<p>첫 글</p>"}
        st, body, h = self.post(c, "/new", base, [("files", ("a.txt", b"x"))])
        self.assertEqual(st, 200, body)
        self.assertIn("application/json", h["Content-Type"])
        j = json.loads(body)
        did = j["id"]
        self.assertEqual(j["edit"], f"/doc/{did}/edit")
        m = self.app.show("dev1", did)
        self.assertEqual(m["status"], "draft")
        self.assertIn("자동 저장", m["title"])  # 제목 전: 자리 이름
        self.assertEqual(m["attachments"], [], "자동 저장은 첨부를 올리지 않음")
        self.assertIn("첫 글", m["doc_html"])
        # 이어지는 자동 저장 여러 번 → 감사 'autosave' 1건(첫 묶음), 이력도 1건
        for i in range(4):
            st, body, _ = self.post(c, j["edit"], dict(base, title="자동 제목", doc_html=f"<p>고침 {i}</p>"),
                                    [("files", ("b.txt", b"y"))])
            self.assertEqual(st, 200, body)
        m = self.app.show("dev1", did)
        self.assertEqual(m["title"], "자동 제목")
        self.assertIn("고침 3", m["doc_html"])
        self.assertEqual(m["attachments"], [])
        acts = [a["action"] for a in audits(self.app, did)]
        self.assertEqual(acts, ["draft"], acts)  # 만든 자동 저장 = 기안 1건으로 묶음
        self.assertTrue(audits(self.app, did)[0]["detail"].get("auto"))
        # '새 기안'에서 이어 쓰기 안내
        st, body, _ = c.req("/new")
        self.assertIn("작성 중이던 문서를 이어서 쓰기", body)
        self.assertIn(f"/doc/{did}/edit", body)
        st, body, _ = c.req("/new?form=general")
        self.assertIn(f"/doc/{did}/edit", body)
        # 다른 사용자: 문서·안내 모두 안 보임, 자동 저장으로 고칠 수 없음
        o = self.client("devlead")
        st, body, _ = o.req("/new")
        self.assertNotIn(f"/doc/{did}/edit", body)
        st, _, _ = o.req(f"/doc/{did}")
        self.assertEqual(st, 404)
        st, body, _ = self.post(o, f"/doc/{did}/edit", dict(base, csrf=o.csrf("/new?form=general"), title="남의 것"))
        self.assertEqual(st, 400)
        self.assertFalse(json.loads(body)["ok"])
        self.assertEqual(self.app.show("dev1", did)["title"], "자동 제목")
        # CSRF 없으면 거절
        st, _, _ = self.post(c, j["edit"], dict(base, csrf="x"))
        self.assertEqual(st, 403)
        # 손으로 임시저장 → 'edit' 기록, 안내 대상에서 빠짐. 다시 자동 저장하면 새 묶음 1건
        st, _, _ = self.post(c, j["edit"], dict(base, title="손 저장", go="save"))
        self.assertEqual(st, 303)
        st, body, _ = c.req("/new")
        self.assertNotIn(f"/doc/{did}/edit", body)
        for i in range(2):
            self.post(c, j["edit"], dict(base, title="손 저장", doc_html=f"<p>다시 {i}</p>"))
        acts = [a["action"] for a in audits(self.app, did)]
        self.assertEqual(acts, ["draft", "edit", "autosave"], acts)
        # 상신 → 이어 쓰기 안내 사라짐, 상신된 문서는 자동 저장 거절
        st, _, _ = self.post(c, j["edit"], dict(base, title="상신", go="submit"))
        self.assertEqual(st, 303)
        self.assertEqual(self.app.show("dev1", did)["status"], "pending")
        st, body, _ = self.post(c, j["edit"], dict(base, title="상신 뒤"))
        self.assertEqual(st, 400)
        self.assertEqual(self.app.show("dev1", did)["title"], "상신")
        st, body, _ = c.req("/new")
        self.assertNotIn(f"/doc/{did}/edit", body)

    def test_auto_too_big_and_logout(self):
        c = self.client("dev1")
        tok = c.csrf("/new?form=general")
        big = "<p>" + "가" * (1024 * 1024) + "</p>"  # 글 3MB(UTF-8) > 2MB
        st, body, _ = self.post(c, "/new", {"csrf": tok, "form": "general", "title": "큰 글", "line": "devlead",
                                            "go": "auto", "doc_html": big})
        self.assertEqual(st, 400)
        self.assertFalse(json.loads(body)["ok"])
        st, _, h = c.req("/logout", {"csrf": tok})
        self.assertEqual(st, 303)
        self.assertEqual(h["Clear-Site-Data"], '"storage"')

    def test_placeholder_title_and_source_copy(self):
        from gyeoljae import core
        c = self.client("dev1")
        tok = c.csrf("/new?form=general")
        base = {"csrf": tok, "form": "general", "title": "", "line": "devlead", "go": "auto", "doc_html": "<p>x</p>"}
        did = json.loads(self.post(c, "/new", base)[1])["id"]
        self.assertEqual(self.app.show("dev1", did)["title"], core.AUTO_TITLE)
        st, body, _ = c.req(f"/doc/{did}/edit")  # 자리 이름은 제목 칸에 넣지 않음(required 로 막힘)
        self.assertNotIn(f"value='{core.AUTO_TITLE}'", body)
        with self.assertRaises(core.Invalid):
            self.app.submit("dev1", did)
        with self.assertRaises(core.Invalid):
            self.app.edit_draft("dev1", did, core.AUTO_TITLE, "")
        with self.assertRaises(core.Invalid):
            self.app.create_draft("dev1", core.AUTO_TITLE, "", ["devlead"])
        with self.assertRaises(core.Invalid):
            self.app.create_draft("dev1", "x", "", ["devlead"], submit=True, auto=True)
        # 원문 첨부 복사가 걸린 '이 문서로 기안'은 자동 저장으로 미리 만들지 않음
        n = len(self.app.list_box("dev1", "drafts"))
        st, body, _ = self.post(c, "/new", dict(base, title="원문", source_id=str(did), copy_atts="1"))
        self.assertEqual(st, 400)
        self.assertIn("임시저장·상신 때", json.loads(body)["error"])
        self.assertEqual(len(self.app.list_box("dev1", "drafts")), n)

    def test_page_markers(self):
        c = self.client("dev1")
        st, body, _ = c.req("/new?form=general")
        self.assertRegex(body, r"data-user='dev1' data-akey='new:general'")
        self.assertIn("/static/editor-view.js?v=", body)
        self.assertIn("name=folders_new value=1", body)
        did = self.app.create_draft("dev1", "수정 화면", "", ["devlead"])["id"]
        st, body, _ = c.req(f"/doc/{did}/edit")
        self.assertIn(f"data-akey='{did}'", body)
        self.assertNotIn("folders_new", body)
        st, js, h = c.req("/static/editor-view.js")
        self.assertEqual(st, 200)
        self.assertIn("requestFullscreen", js)

    def test_new_folders_follow_autosaved_doc(self):
        """새 기안 화면에서 자동 저장으로 문서가 먼저 생긴 뒤 고른 보관 폴더도 상신 때 반영."""
        r = self.app.folder_create("dev1", "자동폴더")
        fid = r["id"] if isinstance(r, dict) else int(r)
        c = self.client("dev1")
        tok = c.csrf("/new?form=general")
        base = {"csrf": tok, "form": "general", "title": "폴더", "line": "devlead", "go": "auto", "tags": "1",
                "dclass": "none", "folders_new": "1", "doc_html": "<p>x</p>"}
        did = json.loads(self.post(c, "/new", base)[1])["id"]
        st, _, _ = self.post(c, f"/doc/{did}/edit", dict(base, folder=str(fid), go="save"))
        self.assertEqual(st, 303)
        self.assertIn(fid, [f["id"] for f in self.app.doc_tags("dev1", did)["folders"]])


if __name__ == "__main__":
    unittest.main()
