import unittest

from helpers import rmtree, world

from gyeoljae import policy as pol
from gyeoljae.core import Forbidden, Invalid

EXPENSE = {"types": {
    "general": {"line": "부서장", "allow_extra": True},
    "expense": {"line": "팀장?, 부서장, 대표", "jeon": "부서장:100만, 임원:500만", "allow_extra": False, "cc": "hr"},
}, "delegation": {"allowed": True, "same_dept_only": True, "max_days": 14}}


class PolicyPureTest(unittest.TestCase):
    P = {c: {"name": n, "rank": r, "scope": s} for c, n, r, s, _ in pol.DEFAULT_POSITIONS}

    def test_amount(self):
        self.assertEqual(pol.parse_amount("150만"), 1500000)
        self.assertEqual(pol.parse_amount("1,500,000원"), 1500000)
        self.assertEqual(pol.parse_amount("1.5억"), 150000000)
        self.assertIsNone(pol.parse_amount(""))
        with self.assertRaises(pol.PolicyError):
            pol.parse_amount("abc")

    def test_normalize(self):
        n = pol.normalize(EXPENSE, self.P)
        self.assertEqual(n["types"]["expense"]["line"][0], {"pos": "lead", "optional": True})
        self.assertEqual(n["types"]["expense"]["jeon"][0], {"pos": "head", "max_amount": 1000000})
        for bad in ({"types": {"expense": {"line": "회장"}}}, {"types": {"x": {}}},
                    {"types": {"general": {"line": [], "allow_extra": False}}},
                    {"delegation": {"max_days": 999}}):
            with self.assertRaises(pol.PolicyError):
                pol.normalize(bad, self.P)


class PolicyTest(unittest.TestCase):
    def setUp(self):
        self.app, self.d = world()
        a = self.app
        a.emp_add("admin", "개발팀장", "DEV", "팀장", username="devtl")
        a.emp_add("admin", "사장", "MGT", "대표", username="boss")
        a.emp_add("admin", "전무", "MGT", "임원", username="cxo")
        a.policy_set("admin", "DEV", EXPENSE)

    def tearDown(self):
        rmtree(self.d)

    def test_threshold_jeongyeol(self):
        a = self.app
        m = a.create_draft("dev1", "소액", "b", doc_type="지출", amount="50만", submit=True)
        self.assertEqual([s["user"] for s in m["line"]], ["devtl", "devlead", "boss"])
        self.assertEqual([s["final"] for s in m["line"]], [False, True, False])
        self.assertIn("hr", m["cc"])
        a.approve("devtl", m["id"])
        m = a.approve("devlead", m["id"])
        self.assertEqual(m["status"], "done")
        self.assertEqual(m["line"][2]["status"], "skipped")
        m2 = a.create_draft("dev1", "고액", "b", doc_type="지출", amount="150만", submit=True)
        self.assertEqual([s["final"] for s in m2["line"]], [False, False, False])
        a.approve("devtl", m2["id"])
        with self.assertRaises(Forbidden):
            a.approve("devlead", m2["id"], jeongyeol=True)  # 150만 > 부서장 한도

    def test_preview(self):
        a = self.app
        p = a.preview_line("admin", "지출", "150만", dept="DEV")
        self.assertEqual([x["user"] for x in p["line"]], ["devtl", "devlead", "boss"])
        self.assertFalse(any(x["final"] for x in p["line"]))
        p = a.preview_line("devtl", "지출", "50만")  # 팀장이 기안 → 팀장 단계 생략
        self.assertEqual([x["user"] for x in p["line"]], ["devlead", "boss"])
        self.assertTrue(p["line"][0]["final"])

    def test_extras_and_server_side(self):
        a = self.app
        with self.assertRaises(Invalid):
            a.create_draft("dev1", "추가불가", "b", ["sal1"], doc_type="지출", amount=1000)
        with self.assertRaises(Invalid):
            a.create_draft("dev1", "전결지정", "b", ["sal1!"], doc_type="일반")
        m = a.create_draft("dev1", "추가", "b", ["sal1"], doc_type="일반", submit=True)
        self.assertEqual([s["user"] for s in m["line"]], ["sal1", "devlead"])
        with self.assertRaises(Invalid):
            a.create_draft("dev1", "금액없음", "b", doc_type="지출")

    def test_versioning_inflight(self):
        a = self.app
        m = a.create_draft("dev1", "버전", "b", doc_type="지출", amount="50만", submit=True)
        self.assertEqual(m["policy"]["version"], 1)
        p2 = {"types": {"expense": {"line": "부서장, 대표", "jeon": "", "allow_extra": False}}}
        a.policy_set("admin", "DEV", p2)
        a.approve("devtl", m["id"])
        m = a.approve("devlead", m["id"])
        self.assertEqual(m["status"], "done")  # v1 의 부서장 전결 유지
        n = a.create_draft("dev1", "신규", "b", doc_type="지출", amount="50만", submit=True)
        self.assertEqual(n["policy"]["version"], 2)
        self.assertEqual([s["final"] for s in n["line"]], [False, False])
        h = a.policy_history("admin", "DEV")
        self.assertEqual(h[0]["version"], 2)
        self.assertIn("+", h[0]["diff"])
        self.assertTrue(any("[결재규정]" in x["body"] for x in a.msg_list("devlead")))
        ent = [e for e in a.audit_list("admin") if e["action"] == "policy_set"]
        self.assertEqual(len(ent), 2)
        self.assertTrue(ent[-1]["detail"]["diff"])
        self.assertTrue(a.verify_audit()["ok"])

    def test_authz(self):
        a = self.app
        for u in ("devlead", "dev1"):
            with self.assertRaises(Forbidden):
                a.policy_get(u, "DEV")
            with self.assertRaises(Forbidden):
                a.policy_set(u, "DEV", EXPENSE)
            with self.assertRaises(Forbidden):
                a.policy_history(u, "DEV")
        p = a.preview_line("sal1", "지출", "50만", dept="DEV")  # 비관리자는 dept 무시, 본인 기준
        self.assertEqual(p["dept"], "SAL")

    def test_delegation_rules(self):
        a = self.app
        with self.assertRaises(Forbidden):
            a.delegate_set("devlead", "sal1")  # same_dept_only
        with self.assertRaises(Forbidden):
            a.delegate_set("devlead", "devtl", "2026-01-01", "2026-02-01")  # > 14일
        a.delegate_set("devlead", "devtl")

    def test_two_person_policy(self):
        a = self.app
        a.add_user("admin", "admin2", "관리자2", "MGT", "admin", "adminpass2")
        a.setting_set("admin", "two_person", True)
        r = a.policy_set("admin", "DEV", {"types": {}})
        self.assertIn("pending", r)
        self.assertEqual(a.policy_get("admin", "DEV")["version"], 1)
        a.change_decide("admin2", r["pending"], True)
        g = a.policy_get("admin", "DEV")
        self.assertEqual((g["version"], g["approved_by"]), (2, "admin2"))

    # ---- 감사 2차 회귀
    def test_extra_cannot_jeongyeol(self):
        a = self.app
        a.policy_update_type("admin", "DEV", "지출", allow_extra=True)
        m = a.create_draft("dev1", "외부전결", "b", ["sallead"], doc_type="지출", amount="50만", submit=True)
        with self.assertRaises(Forbidden):
            a.approve("sallead", m["id"], jeongyeol=True)

    def test_rank_change_needs_two_person_and_snapshot(self):
        a = self.app
        a.add_user("admin", "admin2", "관리자2", "MGT", "admin", "adminpass2")
        a.setting_set("admin", "two_person", True)
        self.assertIn("pending", a.pos_add("admin", "vip", "VIP", 99))
        self.assertIn("pending", a.pos_update("admin", "lead", rank=35))
        self.assertIn("pending", a.emp_move("admin", "dev1", position="팀장"))
        with self.assertRaises(Forbidden):
            a.emp_reset_password("admin", "admin2")
        with self.assertRaises(Invalid):
            a.emp_set_status("admin", "admin2", "퇴사")

    def test_top_rank_drafter_needs_head(self):
        a = self.app
        a.pos_add("admin", "vip", "VIP", 99, scope="dept")
        a.emp_move("admin", "dev1", position="VIP")
        with self.assertRaises(Invalid):
            a.create_draft("dev1", "5억", "b", ["sal1"], doc_type="일반", submit=True)
        m = a.create_draft("dev1", "5억", "b", ["devlead"], doc_type="일반", submit=True)
        self.assertEqual([s["user"] for s in m["line"]], ["devlead"])

    def test_pos_deactivate_blocked_by_policy_and_cleared_policy(self):
        a = self.app
        with self.assertRaises(Invalid):
            a.pos_update("admin", "exec", active=False)  # DEV 지출 규정 jeon 에 임원
        a.policy_set("admin", "DEV", None)
        a.delegate_set("devlead", "sal1")  # 규정 해제 후 대결 500 없음

    def test_named_and_position_overlap(self):
        a = self.app
        a.policy_update_type("admin", "DEV", "일반", line="@devlead, 부서장")
        m = a.create_draft("dev1", "중복", "b", doc_type="일반", submit=True)
        self.assertEqual([s["user"] for s in m["line"]], ["devlead"])

    def test_bad_inputs_are_invalid(self):
        a = self.app
        with self.assertRaises(Invalid):
            a.preview_line("dev1", "zzz")
        with self.assertRaises(Invalid):
            a.preview_line("dev1", "지출", "abc")
        with self.assertRaises(Invalid):
            a.policy_update_delegation("admin", "DEV", max_days="x")

class SeedDemoTest(unittest.TestCase):
    def test_seed_demo_ceo_in_policy(self):
        import tempfile
        from gyeoljae.core import App
        d = tempfile.mkdtemp()
        try:
            a = App(d)
            a.seed_demo()
            self.assertEqual(a.emp_get("admin", "ceo")["position"], "ceo")
            a.policy_set("admin", "DEV", {"types": {"expense": {"line": "부서장, 대표", "jeon": "부서장:100만"}}})
            p = a.preview_line("admin", "지출", "150만", dept="DEV")
            self.assertEqual([x["user"] for x in p["line"]], ["devlead", "ceo"])
        finally:
            rmtree(d)


if __name__ == "__main__":
    unittest.main()
