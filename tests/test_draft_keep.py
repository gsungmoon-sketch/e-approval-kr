"""임시저장 문서 다시 고치기: 손 저장(go=save)·자동 저장(go=auto)·회수·반려 뒤에도 기안 값(제목·본문·양식 칸·종류·금액·
결재선·참조·부서 공개·분류·사업·보관 폴더·원문·첨부)이 그대로 남고, 고치기 화면에 그대로 다시 채워지는지.
임시저장함·홈·문서 화면에서 '이어 쓰기'로 바로 가는지."""
import html
import json
import re
import threading
import unittest

from helpers import rmtree, world


class DraftKeepTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from gyeoljae.web import make_server
        cls.app, cls.d = world()
        a = cls.app
        # SAL: 부서 결재규정(일반 = 부서장 자동, 추가 결재자 허용, 필수 참조 hr)
        a.policy_set("admin", "SAL", {"types": {"general": {"line": "부서장", "allow_extra": True, "cc": "hr"}}})
        cls.fid = a.folder_create("dev1", "보관A")["id"]
        cls.fid2 = a.folder_create("dev1", "보관B")["id"]
        cls.sfid = a.folder_create("sal1", "영업보관")["id"]
        cls.cid = a.dclass_add("devlead", "임시저장시험")["id"]
        cls.pid = a.project_create("devlead", "사업X", depts=["DEV"])["id"]
        cls.srv = make_server(a, "127.0.0.1", 0)
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
        return c.req(path, data, ctype=ct)

    # ---------------------------------------------------------------- 도움
    def form_fields(self, c, did):
        """고치기 화면이 되채운 값: 텍스트 칸 value, 체크된 체크박스·라디오, 고른 option."""
        st, body, _ = c.req(f"/doc/{did}/edit")
        self.assertEqual(st, 200, body)
        out = {}
        for m in re.finditer(r"<input type=text id=\w+ name=(\w+)[^>]*?value='([^']*)'", body):
            out[m.group(1)] = html.unescape(m.group(2))
        out["checked"] = sorted(set(re.findall(r"<input type=(?:checkbox|radio) name=(\w+) value='?([^' >]*)'?\s*checked",
                                               body)))
        out["dclass"] = re.findall(r"<option value='(\d+)' selected>", body)
        return out, body

    def full(self, tok, **kw):
        d = {"csrf": tok, "doc_type": "expense", "amount": "150만", "title": "출장비", "body": "본문 글",
             "line": "devlead!", "cc": "hr,sal1", "dept_open": "1", "tags": "1", "dclass": str(self.cid),
             "project": str(self.pid), "folders_new": "1", "folder": str(self.fid), "doc_html": "<p>편집기 글</p>",
             "doc_page": json.dumps({"size": "A4", "orient": "landscape"})}
        d.update(kw)
        return d

    def check(self, did, c, where):
        a = self.app
        m = a.show("dev1", did, mark_read=False)
        self.assertEqual(m["title"], "출장비", where)
        self.assertIn("편집기 글", m["doc_html"], where)
        self.assertEqual(m["doc_type"], "expense", where)
        self.assertEqual(m["amount"], 1500000, where)
        self.assertEqual(m["line_input"], ["devlead!"], where)
        self.assertEqual([(s["user"], s["final"]) for s in m["line"]], [("devlead", True)], where)
        self.assertEqual(m["cc_input"], ["hr", "sal1"], where)
        self.assertEqual(m["visibility"], "dept", where)
        t = a.doc_tags("dev1", did)
        self.assertEqual((t["dclass"] or {}).get("id"), self.cid, where)
        self.assertEqual([p["id"] for p in t["projects"]], [self.pid], where)
        self.assertEqual([f["id"] for f in t["folders"]], [self.fid], where)
        f, body = self.form_fields(c, did)
        self.assertEqual(f.get("title"), "출장비", where)
        self.assertEqual(f.get("line"), "devlead!", where)
        self.assertEqual(f.get("cc"), "hr,sal1", where)
        self.assertEqual(f.get("amount"), "1500000", where)
        self.assertIn(("doc_type", "expense"), f["checked"], where)
        self.assertIn(("dept_open", "1"), f["checked"], where)
        self.assertIn(("project", str(self.pid)), f["checked"], where)
        self.assertEqual(f["dclass"], [str(self.cid)], where)
        self.assertIn("편집기 글", body, where)
        self.assertIn("landscape", body, where)
        return m

    # ---------------------------------------------------------------- 시험
    def test_manual_then_auto_then_reopen(self):
        c = self.client("dev1")
        tok = c.csrf("/new?type=expense")
        st, _, h = self.post(c, "/new", self.full(tok, go="save"), [("files", ("a.txt", b"att"))])
        self.assertEqual(st, 303)
        did = int(re.search(r"/doc/(\d+)/edit", h["Location"]).group(1))
        self.check(did, c, "손 저장")
        # 고치기 화면에서 그대로 자동 저장(고치기 화면엔 폴더 칸 없음 → 폴더 유지)
        f = self.full(tok, go="auto", body="본문 글")
        for k in ("folders_new", "folder"):
            f.pop(k)
        st, body, _ = self.post(c, f"/doc/{did}/edit", f)
        self.assertEqual(st, 200, body)
        m = self.check(did, c, "손 저장 뒤 자동 저장")
        self.assertEqual([x["name"] for x in m["attachments"]], ["a.txt"], "자동 저장이 첨부를 지우지 않음")
        # 다시 손 저장
        st, _, _ = self.post(c, f"/doc/{did}/edit", dict(f, go="save"))
        self.assertEqual(st, 303)
        m = self.check(did, c, "자동 저장 뒤 손 저장")
        self.assertEqual(len(m["attachments"]), 1)

    def test_auto_first_then_manual(self):
        c = self.client("dev1")
        tok = c.csrf("/new?type=expense")
        st, body, _ = self.post(c, "/new", self.full(tok, go="auto"))
        self.assertEqual(st, 200, body)
        did = json.loads(body)["id"]
        self.check(did, c, "자동 저장으로 생김")
        # 같은 화면(폴더 칸 있음)에서 폴더를 바꿔 다시 자동 저장 → 바꾼 폴더
        st, body, _ = self.post(c, f"/doc/{did}/edit", self.full(tok, go="auto", folder=str(self.fid2)))
        self.assertEqual(st, 200, body)
        self.assertEqual([x["id"] for x in self.app.doc_tags("dev1", did)["folders"]], [self.fid2])
        st, _, _ = self.post(c, f"/doc/{did}/edit", self.full(tok, go="save"), [("files", ("b.txt", b"bb"))])
        self.assertEqual(st, 303)
        m = self.check(did, c, "자동 저장 뒤 손 저장")
        self.assertEqual([x["name"] for x in m["attachments"]], ["b.txt"])

    def test_form_fields_kept(self):
        """양식 문서(입력칸 경로): 양식·양식 칸·종류가 손 저장·자동 저장 뒤에도 남고 고치기 화면에 다시 채워짐."""
        c = self.client("dev1")
        tok = c.csrf("/new?form=trip")
        f = {"csrf": tok, "form": "trip", "title": "출장 기안", "body": "b", "line": "devlead", "cc": "hr",
             "f.dest": "부산 지사", "f.purpose": "점검", "f.period.from": "2026-11-20", "f.period.to": "2026-11-21",
             "f.with": "sal1", "tags": "1", "dclass": "none", "folders_new": "1", "go": "save"}
        st, _, h = self.post(c, "/new", f)
        self.assertEqual(st, 303)
        did = int(re.search(r"/doc/(\d+)/edit", h["Location"]).group(1))
        for go in ("auto", "save"):
            st, body, _ = self.post(c, f"/doc/{did}/edit", dict(f, go=go, folders_new=""))
            self.assertIn(st, (200, 303), body)
            m = self.app.show("dev1", did, mark_read=False)
            self.assertEqual(m["form"]["code"], "trip", go)
            self.assertEqual(m["fields"]["dest"], "부산 지사", go)
            self.assertEqual((m["fields"]["period"]["from"], m["fields"]["period"]["to"]), ("2026-11-20", "2026-11-21"), go)
            self.assertEqual(m["fields"]["with"], "sal1", go)
            self.assertEqual(m["doc_type"], "general", go)
            self.assertEqual(m["cc_input"], ["hr"], go)
            st, body, _ = c.req(f"/doc/{did}/edit")
            self.assertIn("부산 지사", body, go)
            self.assertIn("2026-11-21", body, go)
            self.assertIn("name=form value='trip'", body, go)

    def test_withdraw_and_reject_keep_values(self):
        c = self.client("dev1")
        tok = c.csrf("/new?type=expense")
        st, _, h = self.post(c, "/new", self.full(tok, go="submit"))
        self.assertEqual(st, 303)
        did = int(re.search(r"/doc/(\d+)", h["Location"]).group(1))
        self.app.withdraw("dev1", did)
        self.check(did, c, "회수 뒤")
        st, _, _ = self.post(c, f"/doc/{did}/edit", dict(self.full(tok, go="submit"), folders_new="", folder=""))
        self.assertEqual(st, 303)
        rev = self.app.show("devlead", did)["rev"]
        self.app.reject("devlead", did, "금액을 확인해 주세요", rev=rev)
        m = self.check(did, c, "반려 뒤")
        self.assertEqual(m["status"], "rejected")

    def test_policy_dept_extra_line_kept(self):
        """부서 결재규정 부서: 결재선 칸엔 사용자가 더한 결재자만, 규정 결재자·필수 참조는 칸에 섞이지 않음."""
        c = self.client("sal1")
        tok = c.csrf("/new?type=general")
        fields = {"csrf": tok, "doc_type": "general", "title": "규정 기안", "body": "b", "line": "dev1",
                  "cc": "ceo", "tags": "1", "dclass": "none", "folders_new": "1", "folder": str(self.sfid),
                  "go": "auto", "doc_html": "<p>x</p>"}
        did = json.loads(self.post(c, "/new", fields)[1])["id"]
        for go in ("save", "auto", "save"):
            st, _, _ = self.post(c, f"/doc/{did}/edit", dict(fields, go=go, folders_new="", folder=""))
            self.assertIn(st, (200, 303))
            m = self.app.show("sal1", did, mark_read=False)
            self.assertEqual(m["line_input"], ["dev1"], go)
            self.assertEqual([s["user"] for s in m["line"]], ["dev1", "sallead"], go)  # 추가 결재자 앞, 규정 뒤
            self.assertEqual(m["cc_input"], ["ceo"], go)
            self.assertEqual(sorted(m["cc"]), ["ceo", "hr"], go)
            f, _ = self.form_fields(c, did)
            self.assertEqual(f.get("line"), "dev1", go)
            self.assertEqual(f.get("cc"), "ceo", go)
            self.assertEqual([x["id"] for x in self.app.doc_tags("sal1", did)["folders"]], [self.sfid])

    def test_redraft_autosaved_then_copy_atts(self):
        """'이 문서로 기안' 화면에서 자동 저장으로 문서가 먼저 생긴 뒤 '원문 첨부도 가져오기'를 고르고 손 저장 → 첨부 복사."""
        a = self.app
        src = a.create_draft("dev1", "원문", "본문", ["devlead"], attachments=[("src.txt", b"SRC")])
        c = self.client("dev1")
        tok = c.csrf(f"/doc/{src['id']}/redraft")
        base = {"csrf": tok, "doc_type": "general", "title": "원문 복사", "body": "b", "line": "devlead",
                "tags": "1", "dclass": "none", "folders_new": "1", "source_id": str(src["id"]), "go": "auto",
                "doc_html": "<p>x</p>"}
        did = json.loads(self.post(c, "/new", base)[1])["id"]
        self.assertEqual(a.show("dev1", did, mark_read=False).get("source_doc_id"), src["id"])
        st, body, _ = self.post(c, f"/doc/{did}/edit", dict(base, copy_atts="1"))  # 자동 저장은 복사 안 함
        self.assertEqual(st, 200, body)
        self.assertEqual(a.show("dev1", did, mark_read=False)["attachments"], [])
        st, _, _ = self.post(c, f"/doc/{did}/edit", dict(base, copy_atts="1", go="save"))
        self.assertEqual(st, 303)
        m = a.show("dev1", did, mark_read=False)
        self.assertEqual([x["name"] for x in m["attachments"]], ["src.txt"])
        st, _, _ = self.post(c, f"/doc/{did}/edit", dict(base, copy_atts="1", go="save"))  # 두 번 복사하지 않음
        m = a.show("dev1", did, mark_read=False)
        self.assertEqual([x["name"] for x in m["attachments"]], ["src.txt"])
        self.assertEqual(m.get("source_doc_id"), src["id"])
        st, body, _ = c.req(f"/doc/{did}")
        self.assertIn("원문:", body)
        st, body, _ = c.req(f"/doc/{did}/edit")  # 복사 뒤엔 다시 묻지 않음
        self.assertNotIn("name=copy_atts", body)
        # 자동 저장으로 생긴 뒤 새 기안 화면을 떠났다가 고치기 화면에서 다시 열어도 가져오기 칸이 있음
        did2 = json.loads(self.post(c, "/new", dict(base, title="나중에 복사"))[1])["id"]
        st, body, _ = c.req(f"/doc/{did2}/edit")
        self.assertIn("name=copy_atts", body)
        self.assertIn(f"name=source_id value='{src['id']}'", body)
        st, _, _ = self.post(c, f"/doc/{did2}/edit", {"csrf": tok, "doc_type": "general", "title": "나중에 복사",
                                                       "body": "b", "line": "devlead", "tags": "1", "dclass": "none",
                                                       "source_id": str(src["id"]), "copy_atts": "1", "go": "save"})
        self.assertEqual(st, 303)
        self.assertEqual([x["name"] for x in a.show("dev1", did2, mark_read=False)["attachments"]], ["src.txt"])
        # 위조: 다른 문서를 원문이라 우겨도 복사 안 함
        other = a.create_draft("dev1", "남", "b", ["devlead"], attachments=[("x.txt", b"X")])["id"]
        did3 = a.create_draft("dev1", "원문 없음", "b", ["devlead"])["id"]
        self.post(c, f"/doc/{did3}/edit", {"csrf": tok, "title": "원문 없음", "body": "b", "line": "devlead",
                                           "source_id": str(other), "copy_atts": "1", "go": "save"})
        self.assertEqual(a.show("dev1", did3, mark_read=False)["attachments"], [])

    def test_continue_links(self):
        """임시저장함·홈·문서 화면: 이어 쓰기 단추가 바로 고치기 화면으로."""
        a = self.app
        did = a.create_draft("dev1", "이어 쓸 문서", "b", ["devlead"])["id"]
        done = a.create_draft("dev1", "상신한 문서", "b", ["devlead"], submit=True)["id"]
        c = self.client("dev1")
        st, body, _ = c.req("/box/drafts")
        self.assertRegex(body, rf"<a [^>]*href='/doc/{did}/edit'[^>]*>[^<]*이어 쓰기")
        st, body, _ = c.req("/")
        self.assertRegex(body, rf"<a [^>]*href='/doc/{did}/edit'[^>]*>")
        self.assertIn("이어 쓸 문서", body)
        st, body, _ = c.req(f"/doc/{did}")
        bar = re.search(r"<div class=bar>(.*?)</div>", body, re.S).group(1)
        self.assertRegex(bar, rf"<a href='/doc/{did}/edit' class='btn[^']*'>[^<]*이어 쓰기")
        self.assertIn("임시저장된 문서", body)
        st, body, _ = c.req(f"/doc/{done}")
        self.assertNotIn(f"/doc/{done}/edit", body)
        # 남의 문서는 안 보임
        o = self.client("devlead")
        st, body, _ = o.req("/")
        self.assertNotIn(f"/doc/{did}/edit", body)


if __name__ == "__main__":
    unittest.main()
