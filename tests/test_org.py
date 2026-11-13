import unittest

from helpers import rmtree, world

from gyeoljae import core
from gyeoljae.core import Forbidden, Invalid, NotFound


def _emp_worker(args):
    d, k = args
    from gyeoljae.core import App
    app = App(d)
    return [app.emp_add("admin", f"동시{i}", "DEV", "팀원", hired="2027-03-02")["emp_no"] for i in range(k)]


class OrgTest(unittest.TestCase):
    def setUp(self):
        self.app, self.d = world()

    def tearDown(self):
        rmtree(self.d)

    def test_emp_add_temp_pw_and_force_change(self):
        a = self.app
        r = a.emp_add("admin", "신입", "DEV", "팀원", hired="2026-11-01", email="new@example.com")
        self.assertEqual(r["emp_no"], "26110101")
        self.assertEqual(r["username"], r["emp_no"])
        tok, _ = a.login(r["username"], r["temp_password"], "1.1.1.1")
        self.assertTrue(a.session(tok)["must_change"])
        with self.assertRaises(Invalid):
            a.set_password(r["username"], r["username"], r["temp_password"], r["temp_password"])
        a.set_password(r["username"], r["username"], "newpass123", r["temp_password"])
        tok, _ = a.login(r["username"], "newpass123", "1.1.1.1")
        self.assertFalse(a.session(tok)["must_change"])

    def test_emp_validation(self):
        a = self.app
        with self.assertRaises(Invalid):
            a.emp_add("admin", "x", "DEV", "회장")
        with self.assertRaises(NotFound):
            a.emp_add("admin", "x", "NOPE", "팀원")
        with self.assertRaises(Invalid):
            a.emp_add("admin", "x", "DEV", "팀원", email="bad")
        a.emp_add("admin", "x", "DEV", "팀원", emp_no="E1")
        with self.assertRaises(Invalid):
            a.emp_add("admin", "y", "DEV", "팀원", emp_no="E1")
        with self.assertRaises(Forbidden):
            a.emp_add("devlead", "z", "DEV", "팀원")

    def test_move_history_and_notify(self):
        a = self.app
        a.emp_move("admin", "dev1", dept="SAL", position="팀장")
        e = a.emp_get("admin", "dev1")
        self.assertEqual((e["dept"], e["position"]), ("SAL", "lead"))
        self.assertEqual({h["field"] for h in e["history"]} >= {"dept", "position"}, True)
        self.assertTrue(any("[인사]" in m["body"] for m in a.msg_list("devlead")))
        self.assertTrue(any("[인사]" in m["body"] for m in a.msg_list("sallead")))
        self.assertTrue(any(x["action"] == "emp_move" for x in a.audit_list("admin")))

    def test_retire_keeps_docs(self):
        a = self.app
        m = a.create_draft("dev1", "퇴사 전 문서", "b", ["devlead"], submit=True)
        a.approve("devlead", m["id"])
        a.emp_set_status("admin", "dev1", "퇴사")
        with self.assertRaises(Forbidden):
            a.login("dev1", "pass12345", "2.2.2.2")
        self.assertEqual(a.show("devlead", m["id"])["status"], "done")
        self.assertTrue(a.verify_audit()["ok"])
        with self.assertRaises(Invalid):
            a.emp_set_status("admin", "admin", "퇴사")

    def test_dept_and_positions(self):
        a = self.app
        a.dept_rename("admin", "DEV", "개발본부")
        self.assertEqual([d["name"] for d in a.list_depts() if d["code"] == "DEV"], ["개발본부"])
        with self.assertRaises(Invalid):
            a.dept_set_active("admin", "DEV", False)
        a.add_dept("admin", "TMP", "임시")
        a.dept_set_active("admin", "TMP", False)
        with self.assertRaises(NotFound):
            a.emp_add("admin", "x", "TMP", "팀원")
        a.pos_add("admin", "senior", "책임", 15)
        a.emp_add("admin", "책임자", "DEV", "책임")
        with self.assertRaises(Invalid):
            a.pos_update("admin", "senior", active=False)

    def test_csv_import_utf8_cp949(self):
        a = self.app
        csv_text = "사번,이름,부서,직위,이메일,입사일\nA100,가나다,DEV,팀원,a@b.c,2026-01-02\nA101,라마바,XXX,팀원,,\n"
        r = a.emp_import("admin", csv_text.encode("cp949"))
        self.assertEqual(r["errors"], 1)
        self.assertFalse(r["applied"])
        with self.assertRaises(NotFound):
            a.emp_get("admin", "A100")
        r = a.emp_import("admin", csv_text.encode("utf-8-sig"), apply=True)
        self.assertFalse(r["applied"])  # 오류 있으면 전무
        good = "사번,이름,부서,직위\nA100,가나다,DEV,팀원\nA101,라마바,SAL,팀장\n"
        r = a.emp_import("admin", good.encode("cp949"), apply=True)
        self.assertTrue(r["applied"])
        self.assertEqual(len(r["created"]), 2)
        self.assertTrue(r["created"][0]["temp_password"])
        self.assertEqual(a.emp_get("admin", "A101")["position"], "lead")
        r = a.emp_import("admin", "사번,이름,부서,직위,상태\nA100,가나다,SAL,팀원,휴직\n".encode(), apply=True)
        self.assertTrue(r["applied"])
        e = a.emp_get("admin", "A100")
        self.assertEqual((e["dept"], e["status"]), ("SAL", "leave"))
        out = a.emp_export("admin", "cp949").decode("cp949")
        self.assertIn("가나다", out)
        self.assertTrue(a.verify_audit()["ok"])

    def test_export_formula_injection(self):
        a = self.app
        a.emp_add("admin", "=cmd()", "DEV", "팀원")
        self.assertIn("'=cmd()", a.emp_export("admin").decode("utf-8-sig"))

    def test_org_directory(self):
        a = self.app
        a.emp_add("admin", "연락처", "DEV", "팀원", email="x@y.z", phone="010-1111-2222")
        rows = a.org_directory("sal1", q="연락처")
        self.assertEqual(len(rows), 1)
        self.assertNotIn("email", rows[0])
        a.setting_set("admin", "show_contacts", True)
        self.assertEqual(a.org_directory("sal1", q="연락처")[0]["email"], "x@y.z")
        with self.assertRaises(Forbidden):
            a.emp_list("sal1")

    def test_two_person(self):
        a = self.app
        with self.assertRaises(Invalid):
            a.setting_set("admin", "two_person", True)  # 관리자 1명
        a.add_user("admin", "admin2", "관리자2", "MGT", "admin", "adminpass2")
        a.setting_set("admin", "two_person", True)
        r = a.emp_move("admin", "dev1", position="부서장")
        self.assertIn("pending", r)
        self.assertEqual(a.emp_get("admin", "dev1")["position"], "member")
        with self.assertRaises(Forbidden):
            a.change_decide("admin", r["pending"], True)
        a.change_decide("admin2", r["pending"], True)
        self.assertEqual(a.emp_get("admin", "dev1")["position"], "head")
        r = a.emp_set_role("admin", "hr", "admin")
        self.assertIn("pending", r)
        a.change_decide("admin2", r["pending"], False)
        self.assertEqual(a.emp_get("admin", "hr")["role"], "user")
        r = a.setting_set("admin", "two_person", False)
        self.assertIn("pending", r)
        self.assertTrue(a.settings("admin")["two_person"]["value"])

    def test_csv_roundtrip_and_id_collision(self):
        a = self.app
        a.emp_add("admin", "국제", "DEV", "팀원", emp_no="R1", phone="+82-10-1234")
        out = a.emp_export("admin")
        r = a.emp_import("admin", out, apply=True)
        self.assertEqual(r["errors"], 0, r["rows"])
        with self.assertRaises(Invalid):
            a.emp_add("admin", "충돌", "DEV", "팀원", emp_no="X9", username="r1")
        with self.assertRaises(Invalid):
            a.emp_add("admin", "예약", "DEV", "팀원", username="import")

    def test_backup_restore(self):
        import os, tempfile
        a = self.app
        a.create_draft("dev1", "백업문서", "b", ["devlead"], attachments=[("a.pdf", b"1")], submit=True)
        z = os.path.join(tempfile.mkdtemp(), "b.zip")
        a.backup(z)
        dst = tempfile.mkdtemp()
        r = core.App.restore(z, dst)
        self.assertTrue(r["verify"]["ok"], r["verify"]["errors"])
        b = core.App(dst)
        self.assertEqual(len(b.search("dev1", q="백업문서")), 1)
        h = a.audit_head("admin")
        self.assertTrue(a.check_audit_head(h["seq"], h["hash"]))
        self.assertFalse(a.check_audit_head(h["seq"], "0" * 64))
        rmtree(dst)

    def test_change_requester_recheck(self):
        a = self.app
        a.add_user("admin", "admin2", "관리자2", "MGT", "admin", "adminpass2")
        a.add_user("admin", "admin3", "관리자3", "MGT", "admin", "adminpass3")
        a.setting_set("admin", "two_person", True)
        r = a.emp_move("admin2", "dev1", position="부서장")
        a.emp_set_role("admin", "admin2", "user")  # 관리자 3 → 2
        with self.assertRaises(Invalid):
            a.change_decide("admin3", r["pending"], True)

    def test_emp_no_format(self):
        a = self.app
        n = [a.emp_add("admin", f"가{i}", "DEV", "팀원", hired="2025-03-04")["emp_no"] for i in range(2)]
        self.assertEqual(n, ["25030401", "25030402"])
        self.assertEqual(a.emp_add("admin", "다른날", "DEV", "팀원", hired="2025-03-05")["emp_no"], "25030501")
        a.emp_add("admin", "수동", "DEV", "팀원", emp_no="25030403", hired="2025-03-04")  # 수동 사번은 건너뜀
        self.assertEqual(a.emp_add("admin", "다음", "DEV", "팀원", hired="2025-03-04")["emp_no"], "25030404")
        with self.assertRaises(Invalid):
            a.emp_add("admin", "중복", "DEV", "팀원", emp_no="25030401")
        with self.assertRaises(Invalid):
            a.emp_add("admin", "형식", "DEV", "팀원", emp_no="26/11")
        with self.assertRaises(Invalid):
            a.setting_set("admin", "emp_digits", 4)
        a.setting_set("admin", "emp_digits", 3)
        self.assertEqual(a.emp_add("admin", "세자리", "DEV", "팀원", hired="2025-03-04")["emp_no"], "250304005")
        self.assertEqual(a.emp_get("admin", "25030401")["name"], "가0")  # 기존 번호 불변
        a.setting_set("admin", "emp_digits", 2)
        for i in range(94):  # 06..99 소진
            a.emp_add("admin", f"채움{i}", "DEV", "팀원", hired="2025-03-04")
        with self.assertRaises(Invalid):
            a.emp_add("admin", "넘침", "DEV", "팀원", hired="2025-03-04")

    def test_emp_no_csv_order(self):
        a = self.app
        csv = "이름,부서,직위,입사일\n갑,DEV,팀원,2026-12-01\n을,DEV,팀원,2026-12-02\n병,DEV,팀원,2026-12-01\n"
        r = a.emp_import("admin", csv.encode(), apply=True)
        self.assertEqual([x["emp_no"] for x in r["created"]], ["26120101", "26120201", "26120102"])

    def test_emp_no_concurrency(self):
        import multiprocessing as mp
        with mp.get_context("spawn").Pool(6) as p:
            res = p.map(_emp_worker, [(self.d, 5)] * 6)
        nums = sorted(x for r in res for x in r)
        self.assertEqual(nums, [f"270302{i:02d}" for i in range(1, 31)])


if __name__ == "__main__":
    unittest.main()
