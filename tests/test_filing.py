"""문서 분류·보관(부서 분류·사업 문서함·내 폴더 태그)과 "이 문서로 기안"."""
import json
import re
import sqlite3
import threading
import unittest

from helpers import PW, rmtree, world
from test_web import Client

from gyeoljae.core import App, Forbidden, Invalid, NotFound
from gyeoljae.web import make_server


def audits(app, action):
    with app._ro() as c:
        return [json.loads(r["line"]) for r in c.execute("select line from audit_log order by seq")
                if json.loads(r["line"])["action"] == action]


class FilingCoreTest(unittest.TestCase):
    def setUp(self):
        self.app, self.d = world()

    def tearDown(self):
        rmtree(self.d)

    def cls(self, name, dept="DEV"):
        return next(r["id"] for r in self.app.dclass_list("admin", dept) if r["name"] == name)

    def doc_class(self, did):
        t = self.app.doc_tags("dev1", did)
        return t["dclass"]["name"] if t["dclass"] else None

    @unittest.skipIf(sqlite3.sqlite_version_info < (3, 35), "drop column 필요")
    def test_migration_in_place(self):
        a = self.app
        m = a.create_draft("dev1", "옛 문서", "b", ["devlead"], submit=True)
        a.approve("devlead", m["id"])
        # 옛 DB 흉내: 새 열·표·심기 표시 제거
        c = sqlite3.connect(str(a.db_path))
        for t, col in (("docs", "dclass"), ("docs", "source_doc_id"), ("users", "doc_manager"), ("forms", "dclass")):
            c.execute(f"alter table {t} drop column {col}")
        for t in ("dept_classes", "projects", "project_members", "doc_projects"):
            c.execute(f"drop table {t}")
        c.execute("delete from kv where k like 'dclass_seeded:%'")
        c.commit()
        c.close()
        b = App(self.d)
        self.assertEqual(b.show("dev1", m["id"])["title"], "옛 문서")
        self.assertEqual([r["name"] for r in b.dclass_list("dev1")], ["회계", "인사", "비품", "일반"])
        self.assertIsNone(b.doc_tags("dev1", m["id"])["dclass"])  # 옛 문서 = 미분류
        self.assertTrue(b.verify_audit("admin")["ok"])  # 완료문서 내용 해시 불변
        App(self.d)  # 두 번 열어도 그대로(심기 1회)
        self.assertEqual(len(b.dclass_list("dev1")), 4)

    def test_default_class_by_form(self):
        a = self.app
        a.setting_set("admin", "allow_freeform", False)
        e = a.create_draft("dev1", "지출", "", line=["devlead"], form="expense")
        lv = a.create_draft("dev1", "휴가", "", line=["devlead"], form="leave")
        p = a.create_draft("dev1", "구매", "", line=["devlead"], form="purchase")
        g = a.create_draft("dev1", "일반", "", line=["devlead"], form="general")
        t = a.create_draft("dev1", "출장", "", line=["devlead"], form="trip")
        self.assertEqual([self.doc_class(x["id"]) for x in (e, lv, p, g, t)], ["회계", "인사", "비품", "일반", "인사"])
        a.form_dclass_set("admin", "general", "비품")
        self.assertEqual(self.doc_class(a.create_draft("dev1", "g2", "", line=["devlead"], form="general")["id"]), "비품")
        with self.assertRaises(Forbidden):
            a.form_dclass_set("devlead", "general", "회계")
        # 기안 때 바꾸기 + 미분류
        x = a.create_draft("dev1", "x", "", line=["devlead"], form="expense", dclass=self.cls("일반"))
        self.assertEqual(self.doc_class(x["id"]), "일반")
        y = a.create_draft("dev1", "y", "", line=["devlead"], form="expense", dclass="none")
        self.assertIsNone(self.doc_class(y["id"]))
        with self.assertRaises(Invalid):  # 다른 부서 분류
            a.create_draft("dev1", "z", "", line=["devlead"], form="expense", dclass=self.cls("회계", "SAL"))

    def test_class_management(self):
        a = self.app
        r = a.dclass_add("devlead", "서버", parent=self.cls("비품"))
        with self.assertRaises(Forbidden):
            a.dclass_add("dev1", "몰래")
        with self.assertRaises(Forbidden):
            a.dclass_add("sallead", "남의", dept="DEV")
        with self.assertRaises(Invalid):
            a.dclass_add("devlead", "회계")  # 중복
        a.dclass_add("admin", "관리자분류", dept="DEV")
        rows = a.dclass_list("dev1")
        self.assertEqual(next(x for x in rows if x["name"] == "서버")["depth"], 1)
        m = a.create_draft("dev1", "서버 구입", "b", ["devlead"], dclass=r["id"], submit=True)
        a.approve("devlead", m["id"])
        a.dclass_rename("devlead", r["id"], "서버장비")
        self.assertEqual(self.doc_class(m["id"]), "서버장비")
        with self.assertRaises(Invalid):
            a.dclass_delete("devlead", self.cls("비품"))  # 하위 분류 있음
        # 부서 문서함 거르기(상위 분류는 하위 포함)
        a.create_draft("dev1", "공개", "b", ["devlead"], visibility="dept", submit=True)
        self.assertEqual([x["id"] for x in a.dept_docs("devlead", self.cls("비품"))], [m["id"]])
        self.assertEqual(len(a.dept_docs("devlead")), 1)
        a.dclass_delete("devlead", r["id"])
        self.assertIsNone(self.doc_class(m["id"]))
        self.assertEqual([x["id"] for x in a.dept_docs("devlead", "none")], [m["id"]])
        self.assertTrue(audits(a, "dclass_delete"))

    def test_reclass_permissions_and_audit(self):
        a = self.app
        m = a.create_draft("dev1", "문서", "b", ["devlead"])
        a.doc_reclass("dev1", m["id"], dclass=self.cls("회계"))  # 기안 중 = 기안자
        a.submit("dev1", m["id"])
        with self.assertRaises(Forbidden):
            a.doc_reclass("dev1", m["id"], dclass=self.cls("인사"))
        a.approve("devlead", m["id"])
        a.doc_reclass("devlead", m["id"], dclass=self.cls("인사"))  # 부서장
        with self.assertRaises(Forbidden):
            a.doc_reclass("dev1", m["id"], dclass=None)
        # 문서관리 담당(관리자가 지정) — 볼 수 있는 문서만
        a.add_user("admin", "dev2", "개발2", "DEV", "user", PW)
        a.set_doc_manager("admin", "dev2", True)
        with self.assertRaises(NotFound):  # 비공개 문서는 못 봄 → 못 바꿈
            a.doc_reclass("dev2", m["id"], dclass=None)
        p = a.create_draft("dev1", "공개", "b", ["devlead"], visibility="dept", submit=True)
        a.approve("devlead", p["id"])
        a.doc_reclass("dev2", p["id"], dclass=self.cls("비품"))
        a.set_doc_manager("admin", "dev2", False)
        with self.assertRaises(Forbidden):
            a.doc_reclass("dev2", p["id"], dclass=None)
        with self.assertRaises(Forbidden):
            a.set_doc_manager("devlead", "dev2", True)
        ch = [x["detail"]["dclass"] for x in audits(a, "reclass")]
        self.assertIn(["회계", "인사"], ch)
        self.assertIn(["일반", "비품"], ch)
        self.assertTrue(audits(a, "doc_manager"))

    def test_projects_and_no_widening(self):
        a = self.app
        with self.assertRaises(Forbidden):
            a.project_create("dev1", "몰래")
        pj = a.project_create("devlead", "신제품", "NP-1", depts=["DEV"], users=["sal1"])
        with self.assertRaises(Invalid):
            a.project_create("admin", "중복", "NP-1")
        m = a.create_draft("dev1", "개발 기안", "b", ["devlead"], projects=[pj["id"]], submit=True)
        s = a.create_draft("sal1", "영업 기안", "b", ["sallead"], projects=[pj["id"]], submit=True)
        with self.assertRaises(Forbidden):  # 참여하지 않은 사업
            a.create_draft("hr", "x", "b", ["ceo"], projects=[pj["id"]])
        # 사업 문서함 = 원래 볼 수 있는 문서만
        self.assertEqual([x["id"] for x in a.project_docs("dev1", pj["id"])["docs"]], [m["id"]])
        self.assertEqual([x["id"] for x in a.project_docs("sal1", pj["id"])["docs"]], [s["id"]])
        self.assertEqual({x["id"] for x in a.project_docs("devlead", pj["id"])["docs"]}, {m["id"]})
        with self.assertRaises(NotFound):
            a.project_docs("hr", pj["id"])
        with self.assertRaises(NotFound):  # 태그돼도 못 봄
            a.show("sal1", m["id"])
        with self.assertRaises(Forbidden):  # 관리는 만든 사람·관리자
            a.project_update("dev1", pj["id"], status="closed")
        a.project_update("devlead", pj["id"], status="closed")
        self.assertEqual(a.project_list("dev1", status="open"), [])
        with self.assertRaises(Invalid):
            a.create_draft("dev1", "닫힘", "b", ["devlead"], projects=[pj["id"]])
        a.approve("devlead", m["id"])
        a.doc_reclass("devlead", m["id"], projects=[])  # 종료된 사업 태그 빼기는 가능
        self.assertEqual(a.project_docs("devlead", pj["id"])["docs"], [])
        self.assertEqual(audits(a, "reclass")[-1]["detail"]["projects"], [["신제품"], []])
        self.assertTrue(audits(a, "project_update"))

    def test_folders_at_draft(self):
        a = self.app
        f = a.folder_create("dev1", "내 것")
        g = a.folder_create("devlead", "남의 것")
        m = a.create_draft("dev1", "x", "b", ["devlead"], folders=[f["id"]])
        self.assertEqual([x["name"] for x in a.doc_tags("dev1", m["id"])["folders"]], ["내 것"])
        with self.assertRaises(NotFound):
            a.create_draft("dev1", "y", "b", ["devlead"], folders=[g["id"]])
        a.doc_set_folders("dev1", m["id"], [])
        self.assertEqual(a.doc_tags("dev1", m["id"])["folders"], [])
        with self.assertRaises(NotFound):  # 못 보는 문서
            a.doc_set_folders("sal1", m["id"], [])

    def test_redraft(self):
        a = self.app
        a.setting_set("admin", "allow_freeform", False)
        pj = a.project_create("devlead", "사업A", depts=["DEV"])
        f = a.folder_create("dev1", "모음")
        src = a.create_draft("dev1", "장비 구입", "", form="purchase", line=["devlead"], cc=["hr"],
                             fields={"purpose": "서버", "vendor": "가나상사"}, attachments=[("견적.pdf", b"%PDF-1")],
                             projects=[pj["id"]], folders=[f["id"]], submit=True)
        for u in [s["user"] for s in src["line"]]:
            a.approve(u, src["id"])
        src = a.show("dev1", src["id"])
        self.assertEqual(src["status"], "done")
        # 양식 새 버전(vendor 항목 빠짐) → 같은 이름 항목만
        d = a.form_get("admin", "purchase")
        nd = {k: v for k, v in d.items() if k not in ("code", "version", "enabled", "builtin")}
        nd["fields"] = [x for x in nd["fields"] if x["key"] != "vendor"]
        a.form_save("admin", "purchase", nd)
        p = a.redraft_prefill("dev1", src["id"])
        self.assertEqual(p["form"]["version"], a.form_get("dev1", "purchase")["version"])
        self.assertEqual(p["fields"].get("purpose"), "서버")
        self.assertNotIn("vendor", p["fields"])
        self.assertEqual(p["cc_input"], ["hr"])
        self.assertEqual(p["projects"], [pj["id"]])
        self.assertEqual(p["folders"], [f["id"]])
        n = a.create_draft("dev1", p["title"] + " (재)", "", form="purchase", fields=p["fields"], line=p["line_input"],
                           cc=p["cc_input"], dclass=p["dclass"], projects=p["projects"], source_id=src["id"])
        self.assertEqual(n["attachments"], [])
        self.assertEqual(n["source"]["number"], src["number"])
        self.assertIsNone(n["number"])
        n2 = a.create_draft("dev1", "첨부까지", "", ["devlead"], form="purchase", fields=p["fields"], source_id=src["id"],
                            copy_attachments=True)
        self.assertEqual([x["name"] for x in n2["attachments"]], ["견적.pdf"])
        self.assertEqual(a.get_attachment("dev1", n2["id"], n2["attachments"][0]["idx"])[1].read_bytes(), b"%PDF-1")
        a.submit("dev1", n2["id"])
        self.assertNotEqual(a.show("dev1", n2["id"])["number"], src["number"])
        self.assertEqual(audits(a, "draft_from")[-1]["detail"]["source"], src["id"])
        # 남의 문서(볼 수 있는 결재자)로 기안: 결재선은 지금 규정으로 다시
        q = a.redraft_prefill("devlead", src["id"])
        self.assertNotIn("devlead", [x.rstrip("!") for x in q["line_input"]])
        with self.assertRaises(NotFound):  # 못 보는 문서
            a.redraft_prefill("sal1", src["id"])
        with self.assertRaises(NotFound):
            a.create_draft("sal1", "x", "", ["sallead"], form="purchase", source_id=src["id"])
        # 거부: 양식 사용 중지 / 부서 제한 / 자유 기안 꺼짐
        a.form_enable("admin", "purchase", False)
        with self.assertRaisesRegex(Forbidden, "사용 중지"):
            a.redraft_prefill("dev1", src["id"])
        a.form_enable("admin", "purchase", True)
        d = a.form_get("admin", "purchase")
        nd = {k: v for k, v in d.items() if k not in ("code", "version", "enabled", "builtin")}
        nd["depts"] = ["SAL"]
        a.form_save("admin", "purchase", nd)
        with self.assertRaisesRegex(Forbidden, "쓸 수 없어"):
            a.redraft_prefill("dev1", src["id"])
        a.setting_set("admin", "allow_freeform", True)
        free = a.create_draft("dev1", "자유", "본문", ["devlead"])
        a.setting_set("admin", "allow_freeform", False)
        with self.assertRaisesRegex(Invalid, "자유 기안"):
            a.redraft_prefill("dev1", free["id"])

    def test_redraft_missing_source_attachment_is_invalid(self):
        """원문 첨부 파일이 디스크에 없으면 서버 오류(500) 대신 Invalid."""
        a = self.app
        src = a.create_draft("dev1", "견적", "", ["devlead"], form="purchase", fields={"purpose": "서버"},
                             attachments=[("견적.pdf", b"%PDF-1")])
        (a._doc_dir(src["id"]) / "files" / src["attachments"][0]["stored"]).unlink()
        with self.assertRaisesRegex(Invalid, "원문 첨부"):
            a.create_draft("dev1", "재", "", ["devlead"], form="purchase", fields={"purpose": "서버"},
                           source_id=src["id"], copy_attachments=True)


class FilingWebTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app, cls.d = world()
        cls.srv = make_server(cls.app, port=0)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        rmtree(cls.d)

    def test_redraft_and_tags_web(self):
        a = self.app
        pj = a.project_create("devlead", "웹사업", depts=["DEV"])
        src = a.create_draft("dev1", "원문 문서", "원문 본문", ["devlead"], attachments=[("a.txt", b"hello")],
                             projects=[pj["id"]], submit=True)
        a.approve("devlead", src["id"])
        num = a.show("dev1", src["id"])["number"]
        c = Client(self.base)
        c.login("dev1")
        st, body, _ = c.req(f"/doc/{src['id']}")
        self.assertIn(f"/doc/{src['id']}/redraft", body)
        self.assertIn("웹사업", body)  # 사업 칩
        st, body, _ = c.req(f"/doc/{src['id']}/redraft")
        self.assertEqual(st, 200)
        self.assertIn("원문 첨부도 가져오기", body)
        self.assertIn("value='원문 문서'", body)
        self.assertNotIn("name=copy_atts value=1 checked", body)  # 기본 꺼짐
        tok = re.search(r"name=csrf value='([^']+)'", body).group(1)
        dc = re.search(r"<option value='(\d+)' selected>", body).group(1)
        st, _, h = c.req("/new", {"csrf": tok, "title": "새 문서", "body": "원문 본문", "line": "devlead", "go": "save",
                                  "tags": "1", "dclass": dc, "project": str(pj["id"]), "source_id": str(src["id"]),
                                  "copy_atts": "1"})
        self.assertEqual(st, 303)
        nid = int(re.search(r"/doc/(\d+)", h["Location"]).group(1))
        n = a.show("dev1", nid)
        self.assertEqual([x["name"] for x in n["attachments"]], ["a.txt"])
        st, body, _ = c.req(f"/doc/{nid}")
        self.assertIn(f"원문: <a href='/doc/{src['id']}'>{num}</a>", body)
        self.assertIn("<th scope=row>원문</th>", body)  # 출력 미리보기
        # 태그 고치기(기안 중 = 기안자): 미분류로, 사업 빼기
        st, _, _ = c.req(f"/doc/{nid}/tags", {"csrf": tok, "what": "class", "dclass": "none"})
        self.assertEqual(st, 303)
        t = a.doc_tags("dev1", nid)
        self.assertIsNone(t["dclass"])
        self.assertEqual(t["projects"], [])
        # 완료 문서는 기안자가 못 바꿈
        st, _, _ = c.req(f"/doc/{src['id']}/tags", {"csrf": tok, "what": "class", "dclass": "none"})
        self.assertEqual(st, 403)
        # 내 폴더 넣기
        f = a.folder_create("dev1", "웹폴더")
        c.req(f"/doc/{src['id']}/tags", {"csrf": tok, "what": "folders", "folder": str(f["id"])})
        self.assertEqual([x["id"] for x in a.doc_tags("dev1", src["id"])["folders"]], [f["id"]])
        # 왼쪽 메뉴: 문서함 구역(부서 분류·사업·내 폴더)
        body = c.req("/docs/dept")[1]
        for s in ("부서 문서함", "사업 문서함", "내 폴더", "웹폴더", "· 회계"):
            self.assertIn(s, body)
        self.assertEqual(c.req(f"/docs/project/{pj['id']}")[0], 200)
        o = Client(self.base)
        o.login("sal1")
        self.assertEqual(o.req(f"/docs/project/{pj['id']}")[0], 404)
        self.assertEqual(o.req(f"/doc/{src['id']}/redraft")[0], 404)
        self.assertEqual(o.req("/docs/classes")[0], 403)

    def test_class_and_project_admin_pages(self):
        h = Client(self.base)
        h.login("sallead")
        tok = h.csrf("/docs/classes")
        h.req("/docs/classes", {"csrf": tok, "op": "add", "name": "영업자료"})
        self.assertIn("영업자료", [r["name"] for r in self.app.dclass_list("sallead")])
        st, _, loc = h.req("/docs/project", {"csrf": tok, "name": "영업사업", "code": "S-1", "depts": "SAL", "users": ""})
        self.assertEqual(st, 303)
        ad = Client(self.base)
        ad.login("admin", "adminpass1")
        tok = ad.csrf("/admin/forms/general")
        ad.req("/admin/forms/general/dclass", {"csrf": tok, "dclass": "인사"})
        self.assertEqual(self.app.form_dclass("admin", "general")["effective"], "인사")
        ad.req("/admin/forms/general/dclass", {"csrf": tok, "dclass": ""})
        ad.req("/admin/emp/sal1/docmgr", {"csrf": tok, "on": "1"})
        self.assertEqual(self.app.emp_get("admin", "sal1")["doc_manager"], 1)


if __name__ == "__main__":
    unittest.main()
