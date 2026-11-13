import unittest

from helpers import rmtree, world
from test_policy import EXPENSE

from gyeoljae import formdef as fdm
from gyeoljae.core import Forbidden, Invalid, NotFound

ITEMS = [{"item": "모니터", "qty": "2", "price": "30만"}, {"item": "케이블", "qty": "3", "price": "10,000"},
         {"item": "", "qty": "", "price": ""}]


class FormDefPureTest(unittest.TestCase):
    def test_builtins_valid(self):
        names = [d["name"] for _, d in fdm.builtin_defs()]
        self.assertEqual(len(names), 10)
        self.assertIn("지출결의서", names)

    def test_sums_days_validation(self):
        d = dict(fdm.builtin_defs())
        v, amt = fdm.normalize(d["expense"], {"purpose": "장비", "items": ITEMS, "pay": "법인카드"}, 1, True)
        self.assertEqual(len(v["items"]["rows"]), 2)  # 빈 행 버림
        self.assertEqual(v["items"]["rows"][0]["amount"], 600000)
        self.assertEqual(amt, 630000)
        tampered = [dict(ITEMS[0], amount="1")]  # 클라이언트 계산값 무시
        self.assertEqual(fdm.normalize(d["expense"], {"items": tampered})[1], 600000)
        with self.assertRaises(Invalid):  # 증빙 첨부 필수
            fdm.normalize(d["expense"], {"purpose": "x", "items": ITEMS, "pay": "법인카드"}, 0, True)
        with self.assertRaises(Invalid):
            fdm.normalize(d["expense"], {"pay": "비트코인"})
        with self.assertRaises(Invalid):
            fdm.normalize(d["expense"], {"items": [{"item": "a", "qty": "x1", "price": "1"}]})
        lv = d["leave"]
        v, _ = fdm.normalize(lv, {"kind": "연차", "period": {"from": "2026-11-13", "to": "2026-11-17"}, "backup": "a"},
                             0, True)
        self.assertEqual(v["period"]["days"], 3)  # 금·월·화 (주말 제외)
        v, _ = fdm.normalize(lv, {"kind": "오전반차", "period": "2026-11-16~2026-11-16", "backup": "a"}, 0, True)
        self.assertEqual(v["period"]["days"], 0.5)
        with self.assertRaises(Invalid):
            fdm.normalize(lv, {"kind": "연차", "period": {"from": "2026-11-17", "to": "2026-11-13"}})
        with self.assertRaises(Invalid):  # 필수(대체 근무자) 누락 → 상신 불가
            fdm.normalize(lv, {"kind": "연차", "period": "2026-11-16~2026-11-16"}, 0, True)
        fdm.normalize(lv, {"kind": "연차"}, 0, False)  # 임시저장은 필수 미검사

    def test_check_def_errors(self):
        for bad in ({"name": ""}, {"name": "x", "category": "기타"}, {"name": "x", "fields": [{"key": "A"}]},
                    {"name": "x", "fields": [{"key": "a"}, {"key": "a"}]},
                    {"name": "x", "doc_type": "expense"},  # 지출인데 금액 필드 없음
                    {"name": "x", "fields": [{"key": "t", "type": "table", "columns": [
                        {"key": "a", "type": "number", "calc": "a*zz"}]}]},
                    {"name": "x", "amount": "nope"}, {"name": "x", "fields": [{"key": "s", "type": "select"}]},
                    "{not json"):
            with self.assertRaises(Invalid, msg=str(bad)):
                fdm.check_def(bad)


class FormAuditFixTest(unittest.TestCase):
    def test_calc_and_numbers(self):
        d = dict(fdm.builtin_defs())["expense"]
        with self.assertRaises(Invalid):  # 수량 비우고 금액 직접 넣기 → 거부
            fdm.normalize(d, {"items": [{"item": "서버", "qty": "", "price": "5000만", "amount": "1"}]})
        v, amt = fdm.normalize(d, {"items": [{"item": "서버", "qty": "1", "price": "5000만", "amount": "1"}]})
        self.assertEqual(amt, 50000000)
        with self.assertRaises(Invalid):
            fdm.normalize(d, {"items": [{"item": "a", "qty": "-1", "price": "1000"}]})
        cd = fdm.check_def({"name": "t", "fields": [{"key": "t", "type": "table", "columns": [
            {"key": "a", "type": "number"}, {"key": "b", "type": "number"}, {"key": "c", "type": "number", "calc": "a*b"}],
            "sum": ["c"]}]})
        v, _ = fdm.normalize(cd, {"t": [{"a": "0.1", "b": "0.1"}]})
        self.assertEqual(v["t"]["rows"][0]["c"], 0.01)
        fdm.normalize(cd, v, 0, True)  # 저장값 재검증 통과
        got = fdm.from_flat(d, {"f.items.49.item": "x", "f.items.49.qty": "1", "f.items.49.price": "1"}.get)
        self.assertEqual(len(got["items"]), 1)  # 빈 행 뒤 행도 읽음
        for bad in ({"name": "x", "fields": [{"key": "t", "type": "table", "columns": [5]}]},
                    {"name": "x", "fields": [{"key": "s", "type": "select", "options": 5}]},
                    {"name": "x", "fields": [{"key": "t", "type": "text", "max": 1e400}]}, {"name": "x", "depts": 5},
                    {"name": "x", "fields": [{"key": "t", "type": "table", "columns": [{"key": "a"}], "sum": [["x"]]}]}):
            with self.assertRaises(Invalid, msg=str(bad)):
                fdm.check_def(bad)


class FormsTest(unittest.TestCase):
    def setUp(self):
        self.app, self.d = world()
        a = self.app
        a.emp_add("admin", "개발팀장", "DEV", "팀장", username="devtl")
        a.emp_add("admin", "사장", "MGT", "대표", username="boss")
        a.emp_add("admin", "전무", "MGT", "임원", username="cxo")
        a.policy_set("admin", "DEV", EXPENSE)

    def tearDown(self):
        rmtree(self.d)

    def _exp(self, items, submit=True, files=(("영수증.pdf", b"%PDF"),)):
        return self.app.create_draft("dev1", "장비 구입", "", form="expense", attachments=list(files), submit=submit,
                                     fields={"purpose": "장비", "items": items, "pay": "법인카드"})

    def test_amount_drives_threshold(self):
        a = self.app
        m = self._exp([{"item": "마우스", "qty": 2, "price": "25만"}])  # 50만 → 부서장 전결
        self.assertEqual((m["doc_type"], m["amount"]), ("expense", 500000))
        self.assertEqual([s["final"] for s in m["line"]], [False, True, False])
        self.assertIn("ceo", m["cc"])  # 재정 양식 → 경영지원 부서장 필수 참조
        self.assertEqual(m["form"]["retention"], "5년")
        m2 = self._exp([{"item": "서버", "qty": 1, "price": "150만"}])
        self.assertEqual([s["final"] for s in m2["line"]], [False, False, False])
        # 양식 문서는 금액을 직접 못 바꿈(필드가 정함)
        d = a.create_draft("dev1", "x", "", form="expense", fields={"items": [{"item": "a", "qty": 1, "price": 10}]})
        d = a.edit_draft("dev1", d["id"], amount="1", doc_type="일반")
        self.assertEqual((d["amount"], d["doc_type"]), (10, "expense"))
        with self.assertRaises(Invalid):  # 증빙 없이 상신 불가
            a.submit("dev1", d["id"])
        a.approve("devtl", m["id"])
        m = a.approve("devlead", m["id"])
        self.assertEqual(m["status"], "done")
        self.assertRegex(m["number"], r"^DEV-\d{4}-\d{4}$")
        self.assertTrue(a.verify_audit()["ok"])

    def test_versioning_and_number_format(self):
        a = self.app
        d = a.create_draft("dev1", "휴가", "", form="leave", submit=False,
                           fields={"kind": "연차", "period": "2026-11-16~2026-11-16", "backup": "dev1"})
        self.assertEqual(d["form"]["version"], 1)
        nd = a.form_get("admin", "leave")
        nd["fields"] = [f for f in nd["fields"] if f["key"] != "backup"] + [
            {"key": "dest", "label": "행선지", "type": "text", "required": True}]
        r = a.form_save("admin", "leave", nd)
        self.assertEqual(r["version"], 2)
        self.assertFalse(a.form_save("admin", "leave", nd)["changed"])
        d = a.edit_draft("dev1", d["id"], fields={"kind": "연차", "period": "2026-11-16~2026-11-16", "backup": "x"})
        self.assertEqual(d["fields"]["backup"], "x")  # 기안 당시 v1 유지
        a.submit("dev1", d["id"])
        n = a.create_draft("dev1", "휴가2", "", form="leave", fields={"kind": "연차"})
        self.assertEqual(n["form"]["version"], 2)
        self.assertNotIn("backup", n["fields"])
        with self.assertRaises(Invalid):
            a.setting_set("admin", "doc_number", "{dept}-{year}")  # {seq} 없음
        with self.assertRaises(Invalid):
            a.setting_set("admin", "doc_number", "{dept}<{seq}>")
        a.setting_set("admin", "doc_number", "{prefix}-{yy}-{seq}")
        m = self._exp([{"item": "a", "qty": 1, "price": "1만"}])
        a.approve("devtl", m["id"])
        m = a.approve("devlead", m["id"])
        self.assertRegex(m["number"], r"^지출-\d\d-0001$")
        g = a.create_draft("dev1", "일반", "", form="general", submit=True, fields={"purpose": "p"})
        g = a.approve("devlead", g["id"])
        self.assertRegex(g["number"], r"^DEV-\d\d-0001$")  # 접두어 없는 양식 → 부서

    def test_freeform_bypass_blocked(self):
        a = self.app
        d = a.create_draft("dev1", "옛 자유기안", "b", doc_type="지출", amount="5000만")  # 설정 켜진 동안 만든 임시저장
        a.setting_set("admin", "allow_freeform", False)
        with self.assertRaises(Invalid):  # 지출을 일반으로 올려 한도 회피
            a.create_draft("dev1", "서버 구매", "5000만원", doc_type="일반", submit=True)
        with self.assertRaises(Invalid):
            a.create_draft("dev1", "x", "b", doc_type="지출", amount="10만")
        with self.assertRaises(Invalid):  # 상신 시에도 다시 검사
            a.submit("dev1", d["id"])
        m = a.create_draft("dev1", "서버", "", form="expense", attachments=[("r.pdf", b"%PDF")], submit=True,
                           fields={"purpose": "p", "items": [{"item": "서버", "qty": 1, "price": "5000만"}], "pay": "법인카드"})
        self.assertFalse(any(s["final"] for s in m["line"][:2]))  # 고액 → 전결 없음
        for f in a.forms_list("admin", all_=True):  # 쓸 수 있는 양식이 하나도 없으면 자유 기안 허용
            a.form_enable("admin", f["code"], False)
        a.create_draft("dev1", "양식 없음", "b")

    def test_audit_fixes(self):
        a = self.app
        with self.assertRaises(Invalid):
            a.setting_set("admin", "doc_number", "{dept}-{seq}")  # 연도 없음
        a.setting_set("admin", "doc_number", "{dept}-{year}-{seq}X")
        m = self._exp([{"item": "a", "qty": 1, "price": "1만"}])
        a.approve("devtl", m["id"])
        self.assertRegex(a.approve("devlead", m["id"])["number"], r"^DEV-\d{4}-0001X$")
        a.setting_set("admin", "doc_number", "{dept}-{year}-{seq}")  # 기본으로 복귀해도 이어서
        a.setting_set("admin", "doc_number", "{prefix}-{year}-{seq}")
        g = a.create_draft("dev1", "g", "", form="general", submit=True, fields={"purpose": "p"})
        g = a.approve("devlead", g["id"])
        self.assertRegex(g["number"], r"^DEV-\d{4}-0001$")
        g2 = a.create_draft("dev1", "g2", "", form="general", submit=True, fields={"purpose": "p"})
        self.assertRegex(a.approve("devlead", g2["id"])["number"], r"^DEV-\d{4}-0002$")
        a.setting_set("admin", "doc_number", "{dept}-{year}-{seq}")
        g3 = a.create_draft("dev1", "g3", "", form="general", submit=True, fields={"purpose": "p"})
        self.assertRegex(a.approve("devlead", g3["id"])["number"], r"^DEV-\d{4}-0003$")  # 형식 바꿔도 충돌 없음
        with self.assertRaises(Invalid):
            a.form_save("admin", "general", dict(a.form_get("admin", "general"), cc_depts=["NOPE"]))
        a.form_copy("admin", "general", "salonly")
        a.form_save("admin", "salonly", dict(a.form_get("admin", "salonly"), depts=["SAL"]))
        with self.assertRaises(NotFound):
            a.form_get("dev1", "salonly", 1)
        with self.assertRaises(NotFound):
            a.form_fav("dev1", "salonly")
        self.assertEqual(a.form_for_doc("dev1", g["id"])["code"], "general")
        self.assertEqual(a.form_for_doc("devlead", g["id"])["version"], 1)

    def test_permissions(self):
        a = self.app
        with self.assertRaises(Forbidden):
            a.form_save("dev1", "x1", {"name": "x"}, new=True)
        with self.assertRaises(Forbidden):
            a.form_enable("devlead", "leave", False)
        a.form_copy("admin", "general", "salonly", "영업 전용")
        nd = a.form_get("admin", "salonly")
        nd["depts"] = ["SAL"]
        a.form_save("admin", "salonly", nd)
        self.assertNotIn("salonly", [f["code"] for f in a.forms_list("dev1")])
        self.assertIn("salonly", [f["code"] for f in a.forms_list("sal1")])
        with self.assertRaises(Forbidden):
            a.create_draft("dev1", "x", "", form="salonly")
        a.form_enable("admin", "leave", False)
        with self.assertRaises(Forbidden):
            a.create_draft("dev1", "x", "", form="leave")
        with self.assertRaises(NotFound):
            a.form_get("dev1", "leave")
        with self.assertRaises(NotFound):
            a.create_draft("dev1", "x", "", form="nope")
        with self.assertRaises(Invalid):
            a.form_save("admin", "salonly", {"name": "x"}, new=True)  # 코드 중복
        with self.assertRaises(Forbidden):
            a.forms_list("dev1", all_=True)
        a.form_fav("dev1", "expense")
        self.assertTrue(next(f for f in a.forms_list("dev1") if f["code"] == "expense")["fav"])
        a.create_draft("dev1", "x", "", form="coop", fields={})
        self.assertEqual(a.forms_recent("dev1"), ["coop"])


if __name__ == "__main__":
    unittest.main()


class FormsWebTest(unittest.TestCase):
    def test_web_flow(self):
        import threading
        from test_web import Client, multipart
        from gyeoljae.web import make_server
        app, d = world()
        srv = make_server(app, "127.0.0.1", 0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            c = Client(f"http://127.0.0.1:{srv.server_address[1]}")
            c.login("dev1")
            st, body, _ = c.req("/new")
            self.assertIn("지출결의서", body)
            self.assertIn("휴가신청서", body)
            st, body, _ = c.req("/new?form=expense")
            self.assertIn("f.items.0.qty", body)
            tok = c.csrf("/new?form=expense")
            fields = {"csrf": tok, "form": "expense", "title": "<b>장비</b>", "body": "", "line": "devlead", "cc": "",
                      "f.purpose": "모니터", "f.items.0.item": "모니터", "f.items.0.qty": "2",
                      "f.items.0.price": "300000", "f.items.0.amount": "1", "f.pay": "법인카드", "go": "submit"}
            data, ct = multipart(fields, [])
            st, body, _ = c.req("/new", data, ctype=ct)
            self.assertEqual(st, 400)  # 증빙 첨부 필수
            self.assertIn("증빙", body)
            self.assertIn("value='모니터'", body)  # 입력 유지
            data, ct = multipart(fields, [("files", ("영수증.pdf", b"%PDF"))])
            st, b2, h = c.req("/new", data, ctype=ct)
            self.assertEqual(st, 303, __import__("re").findall("class=err role=alert>([^<]*)", b2))
            did = app.list_box("dev1", "progress")[0]["id"]
            m = app.show("dev1", did)
            self.assertEqual(m["amount"], 600000)
            st, body, _ = c.req(f"/doc/{did}")
            self.assertIn("600,000", body)
            self.assertIn("보존기간", body)
            self.assertNotIn("<b>장비", body)
            o = Client(f"http://127.0.0.1:{srv.server_address[1]}")
            o.login("dev1")
            self.assertEqual(c.req("/admin/forms")[0], 403)
            ad = Client(f"http://127.0.0.1:{srv.server_address[1]}")
            ad.login("admin", "adminpass1")
            self.assertIn("지출결의서", ad.req("/admin/forms")[1])
            st, body, _ = ad.req("/admin/forms/leave")
            self.assertIn("half_field", body)
            tok = ad.csrf("/admin/forms/leave")
            fd = app.form_get("admin", "leave")
            post = {"csrf": tok, "name": "휴가신청서", "category": "인사", "doc_type": "leave", "retention": "3년",
                    "prefix": "", "depts": "", "cc_depts": "MGT", "amount": "", "title_hint": "", "body_hint": "",
                    "fields": __import__("json").dumps(fd["fields"])}
            self.assertEqual(ad.req("/admin/forms/leave", post)[0], 303)
            self.assertEqual(app.form_get("admin", "leave")["version"], 2)
            bad = dict(post, fields="[{bad")
            st, body, _ = ad.req("/admin/forms/leave", bad)
            self.assertEqual(st, 400)
            self.assertIn("[{bad", body)
            nw = dict(post, code="leave_kr", name="휴가(복사)", csrf=ad.csrf("/admin/forms/new?copy=leave"))
            self.assertEqual(ad.req("/admin/forms/new", nw)[0], 303)
            self.assertEqual(ad.req("/admin/forms/leave_kr/disable", {"csrf": tok})[0], 303)
            self.assertFalse(app.form_get("admin", "leave_kr")["enabled"])
            st = ad.req("/admin/changes", {"csrf": tok, "op": "setting", "key": "doc_number", "value": "{prefix}-{yy}-{seq}"})[0]
            self.assertEqual(st, 303)
        finally:
            srv.shutdown()
            rmtree(d)
