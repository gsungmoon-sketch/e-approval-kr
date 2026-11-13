import json
import multiprocessing as mp
import unittest

from helpers import PW, rmtree, world

from gyeoljae import core
from gyeoljae.core import App, Forbidden, Invalid, NotFound, safe_filename


class SanitizerTest(unittest.TestCase):
    """OS 무관 파일명 규칙(Windows 예약어·금지문자 포함) — Linux에서 Windows 규칙을 단위 검증."""

    def test_traversal(self):
        for bad in ("../../etc/passwd.txt", "..\\..\\win.ini.txt", "/abs/x.pdf", "C:\\x\\y.pdf"):
            n = safe_filename(bad)
            self.assertNotIn("/", n)
            self.assertNotIn("\\", n)
            self.assertFalse(n.startswith("."))

    def test_windows_rules(self):
        self.assertEqual(safe_filename("CON.txt"), "_CON.txt")
        self.assertEqual(safe_filename("nul.pdf"), "_nul.pdf")
        self.assertEqual(safe_filename("com1.backup.pdf"), "_com1.backup.pdf")
        self.assertEqual(safe_filename('a<b>c:d"e|f?g*.pdf'), "a_b_c_d_e_f_g_.pdf")
        self.assertEqual(safe_filename("보고서 .  .pdf"), "보고서.pdf")
        self.assertEqual(safe_filename("기안서.HWPX"), "기안서.hwpx")
        self.assertEqual(safe_filename("x\x00y.pdf"), "xy.pdf")

    def test_reject(self):
        for bad in ("", "...", "evil.exe", "noext", ".pdf", "a.pdf.exe", "../"):
            with self.assertRaises(Invalid, msg=bad):
                safe_filename(bad)

    def test_case_collision(self):
        app, d = world()
        try:
            m = app.create_draft("dev1", "t", "b", ["devlead"], attachments=[("A.pdf", b"1"), ("a.PDF", b"2")])
            names = [a["name"] for a in m["attachments"]]
            self.assertEqual(names, ["A.pdf", "a(2).pdf"])
            self.assertEqual([a["stored"] for a in m["attachments"]], ["01.pdf", "02.pdf"])
        finally:
            rmtree(d)


def _worker_serial(args):
    d, n = args
    app = App(d)
    return [app.reserve_serial("DEV", 2026) for _ in range(n)]


def _worker_flow(args):
    d, k = args
    app = App(d)
    nums = []
    for i in range(k):
        m = app.create_draft("dev1", f"동시 {i}", "x", ["devlead"], submit=True)
        nums.append(app.approve("devlead", m["id"])["number"])
    return nums


class CoreTest(unittest.TestCase):
    def setUp(self):
        self.app, self.d = world()

    def tearDown(self):
        rmtree(self.d)

    def test_password(self):
        h = core.hash_password("abcdefgh")
        self.assertTrue(core.verify_password("abcdefgh", h))
        self.assertFalse(core.verify_password("abcdefgX", h))
        self.assertFalse(core.verify_password("x", None))
        with self.assertRaises(Invalid):
            core.hash_password("short")

    def test_flow_and_number(self):
        a = self.app
        m = a.create_draft("dev1", "장비 구매", "노트북 2대", ["devlead", "ceo"], cc=["hr"], submit=True)
        self.assertEqual(m["status"], "pending")
        self.assertEqual([r["id"] for r in a.list_box("devlead", "inbox")], [m["id"]])
        self.assertEqual(a.list_box("ceo", "inbox"), [])
        a.approve("devlead", m["id"], "ok")
        m = a.approve("ceo", m["id"])
        self.assertEqual(m["status"], "done")
        self.assertRegex(m["number"], r"^DEV-\d{4}-0001$")
        with self.assertRaises(Forbidden):
            a.edit_draft("dev1", m["id"], title="변조")
        self.assertTrue(a.verify_audit("admin")["ok"])
        self.assertEqual(len(a.list_box("hr", "cc")), 1)

    def test_withdraw_rules(self):
        a = self.app
        m = a.create_draft("dev1", "회수", "b", ["devlead", "ceo"], submit=True)
        with self.assertRaises(NotFound):
            a.withdraw("devlead", m["id"])
        a.withdraw("dev1", m["id"])
        a.submit("dev1", m["id"])
        a.approve("devlead", m["id"])
        with self.assertRaises(Forbidden):
            a.withdraw("dev1", m["id"])

    def test_wrong_turn(self):
        a = self.app
        m = a.create_draft("dev1", "차례", "b", ["devlead", "ceo"], submit=True)
        with self.assertRaises(Forbidden):
            a.approve("ceo", m["id"])  # ceo는 결재선에 있어 보이지만 차례 아님
        with self.assertRaises(NotFound):
            a.approve("sal1", m["id"])  # 무관한 사람은 존재도 모름

    def test_jeongyeol(self):
        a = self.app
        m = a.create_draft("dev1", "전결", "b", ["devlead!", "ceo"], submit=True)
        m = a.approve("devlead", m["id"])
        self.assertEqual(m["status"], "done")
        self.assertEqual(m["line"][1]["status"], "skipped")
        m2 = a.create_draft("hr", "일반전결불가", "b", ["dev1", "ceo"], submit=True)
        with self.assertRaises(Forbidden):
            a.approve("dev1", m2["id"], jeongyeol=True)

    def test_delegate(self):
        a = self.app
        m = a.create_draft("dev1", "대결", "b", ["devlead", "ceo"], submit=True)
        a.delegate_set("devlead", "dev1")  # 기안자 본인에게 위임해도 대결 가능 여부: 허용하되 기록
        a.delegate_set("ceo", "hr")
        a.approve("devlead", m["id"])
        self.assertEqual([r["id"] for r in a.list_box("hr", "inbox")], [m["id"]])
        m = a.approve("hr", m["id"])
        self.assertEqual(m["status"], "done")
        self.assertEqual(m["line"][1]["proxy_for"], "ceo")
        self.assertEqual(m["line"][1]["acted_by"], "hr")
        with self.assertRaises(Forbidden):
            a.delegate_set("dev1", "sal1", from_user="ceo")

    def test_numbering_concurrency(self):
        with mp.get_context("spawn").Pool(8) as p:
            res = p.map(_worker_serial, [(self.d, 25)] * 8)
        allnums = sorted(x for r in res for x in r)
        self.assertEqual(allnums, list(range(1, 201)))

    def test_flow_concurrency(self):
        with mp.get_context("spawn").Pool(6) as p:
            res = p.map(_worker_flow, [(self.d, 5)] * 6)
        nums = [x for r in res for x in r]
        self.assertEqual(len(nums), 30)
        self.assertEqual(len(set(nums)), 30)
        v = self.app.verify_audit("admin")
        self.assertTrue(v["ok"], v["errors"])

    def test_search_permissions(self):
        a = self.app
        s = a.create_draft("sal1", "영업비밀 단가표", "고객사 특별할인 단가", ["sallead"], submit=True)
        a.create_draft("dev1", "개발 서버 구매", "서버 단가 비교", ["devlead"], submit=True)
        for u in ("dev1", "devlead", "hr", "ceo", "admin"):
            ids = [r["id"] for r in a.search(u, q="단가")]
            self.assertNotIn(s["id"], ids, u)
            with self.assertRaises(NotFound):
                a.show(u, s["id"])
        self.assertIn(s["id"], [r["id"] for r in a.search("sallead", q="단가표")])  # 부서장
        self.assertIn(s["id"], [r["id"] for r in a.search("sal1", q="단가")])  # 2글자(LIKE 경로)
        self.assertEqual(len(a.search("dev1", q="서버 단가")), 1)  # trigram 경로
        # 기안중(미상신) 문서는 결재선도 못 봄
        d = a.create_draft("dev1", "초안", "b", ["devlead"])
        with self.assertRaises(NotFound):
            a.show("devlead", d["id"])

    def test_dept_open(self):
        a = self.app
        m = a.create_draft("sal1", "부서공개", "b", ["sallead"], visibility="dept", submit=True)
        self.assertEqual(a.search("dev1", q="부서공개"), [])
        a.approve("sallead", m["id"])
        self.assertEqual(len(a.search("sal1", title="부서공개")), 1)
        self.assertEqual(a.search("dev1", title="부서공개"), [])

    def test_audit_tamper(self):
        a = self.app
        m = a.create_draft("dev1", "감사", "원본", ["devlead"], submit=True)
        a.approve("devlead", m["id"])
        self.assertTrue(a.verify_audit()["ok"])
        lines = a.audit_path.read_text(encoding="utf-8").splitlines(True)
        e = json.loads(lines[-3])
        e["actor"] = "hacker"
        tampered = lines[:-3] + [json.dumps(e, ensure_ascii=False, sort_keys=True) + "\n"] + lines[-2:]
        a.audit_path.write_text("".join(tampered), encoding="utf-8")
        self.assertFalse(a.verify_audit()["ok"])
        a.audit_path.write_text("".join(lines), encoding="utf-8")
        self.assertTrue(a.verify_audit()["ok"])
        a.audit_path.write_text("".join(lines[:-1]), encoding="utf-8")  # 꼬리 잘라내기
        self.assertFalse(a.verify_audit()["ok"])
        a.audit_path.write_text("".join(lines), encoding="utf-8")
        body = a._doc_dir(m["id"]) / "body.md"
        import os
        os.chmod(body, 0o644)
        body.write_text("변조된 본문", encoding="utf-8")
        v = a.verify_audit()
        self.assertFalse(v["ok"])
        self.assertTrue(any("변조" in x for x in v["errors"]))

    def test_messages_board(self):
        a = self.app
        m = a.create_draft("dev1", "알림", "b", ["devlead"], submit=True)
        self.assertTrue(any(x["doc_id"] == m["id"] for x in a.msg_list("devlead", unread=True)))
        r = a.msg_send("dev1", "hr", "안녕")
        with self.assertRaises(NotFound):
            a.msg_read("sal1", r["id"])
        self.assertEqual(a.msg_read("hr", r["id"])["body"], "안녕")
        with self.assertRaises(Forbidden):
            a.board_post("dev1", "notice", "공지", "x")
        a.board_post("ceo", "notice", "공지", "x")
        a.board_post("dev1", "free", "자유", "x")
        self.assertEqual(len(a.board_list("sal1", "notice")), 1)

    def test_admin_only(self):
        with self.assertRaises(Forbidden):
            self.app.add_user("devlead", "x1", "x", "DEV", "admin", PW)
        with self.assertRaises(Forbidden):
            self.app.verify_audit("ceo")
        with self.assertRaises(Forbidden):
            self.app.audit_list("ceo")

    def test_like_fallback(self):
        self.app.fts = False
        self.app.create_draft("dev1", "폴백검색", "본문내용", ["devlead"], submit=True)
        self.assertEqual(len(self.app.search("dev1", q="본문내")), 1)

    def test_attachment_limits(self):
        with self.assertRaises(Invalid):
            self.app.create_draft("dev1", "t", "b", ["devlead"],
                                  attachments=[("big.pdf", b"0" * (core.MAX_FILE_BYTES + 1))])
        with self.assertRaises(Invalid):
            self.app.create_draft("dev1", "t", "b", ["devlead"], attachments=[("x.exe", b"1")])
        m = self.app.create_draft("dev1", "t2", "b", ["devlead"])  # 롤백 뒤 같은 id 재사용 OK
        self.assertTrue(m["id"] >= 1)

    def test_rate_limit(self):
        for _ in range(5):
            with self.assertRaises(Forbidden):
                self.app.login("dev1", "wrong", "1.2.3.4")
        with self.assertRaises(core.RateLimited):
            self.app.login("dev1", PW, "5.6.7.8")

    # ---- 감사(audit) 지적 회귀 시험
    def test_no_self_approval_via_delegation(self):
        a = self.app
        a.delegate_set("devlead", "dev1")
        m = a.create_draft("dev1", "셀프", "b", ["devlead"], submit=True)
        with self.assertRaises(Forbidden):
            a.approve("dev1", m["id"])

    def test_no_double_approval(self):
        a = self.app
        a.delegate_set("devlead", "ceo")
        m = a.create_draft("dev1", "중복", "b", ["devlead", "ceo"], submit=True)
        with self.assertRaises(Forbidden):
            a.approve("ceo", m["id"])  # ceo는 뒤 단계 결재자 → 앞 단계 대결 불가

    def test_admin_cannot_grab_delegation(self):
        with self.assertRaises(Forbidden):
            self.app.delegate_set("admin", "admin", from_user="ceo")

    def test_line_rules(self):
        with self.assertRaises(Invalid):
            self.app.create_draft("dev1", "t", "b", ["hr!"])
        with self.assertRaises(Invalid):
            self.app.create_draft("dev1", "t", "b", ["hr"])  # 부서장/관리자 없음

    def test_stale_rev(self):
        a = self.app
        m = a.create_draft("dev1", "버전", "1대", ["devlead"], submit=True)
        rev = a.show("devlead", m["id"])["rev"]
        a.withdraw("dev1", m["id"])
        a.edit_draft("dev1", m["id"], body="100대")
        with self.assertRaises(NotFound):
            a.show("devlead", m["id"])  # 재상신 전 수정본 비공개
        a.submit("dev1", m["id"])
        with self.assertRaises(Invalid):
            a.approve("devlead", m["id"], rev=rev)
        a.approve("devlead", m["id"], rev=a.show("devlead", m["id"])["rev"])

    def test_rollback_keeps_files_and_audit(self):
        a = self.app
        m = a.create_draft("dev1", "롤백", "b", ["devlead"], attachments=[("a.pdf", b"1")])
        with self.assertRaises(Invalid):
            a.edit_draft("dev1", m["id"], body="changed", add_attachments=[("x.exe", b"2")], remove_attachments=[1])
        name, path = a.get_attachment("dev1", m["id"], 1)
        self.assertTrue(path.exists())
        self.assertEqual(a.show("dev1", m["id"])["body"], "b")
        self.assertTrue(a.verify_audit()["ok"])

    def test_invalid_date(self):
        with self.assertRaises(Invalid):
            self.app.search("dev1", date_from="2026-13-45")

    def test_parallel_login_limit(self):
        import concurrent.futures as cf

        def try_login(_):
            try:
                self.app.login("hr", "wrongwrong", "9.9.9.9")
                return "ok"
            except core.RateLimited:
                return "limited"
            except Forbidden:
                return "checked"
        with cf.ThreadPoolExecutor(20) as ex:
            res = list(ex.map(try_login, range(20)))
        self.assertLessEqual(res.count("checked"), core.LOGIN_MAX_FAIL)


if __name__ == "__main__":
    unittest.main()
