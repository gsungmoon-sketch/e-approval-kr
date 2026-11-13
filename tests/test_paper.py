import threading
import unittest

from helpers import rmtree, world
from test_web import Client

from gyeoljae import formdef as fdm
from gyeoljae.core import Invalid
from gyeoljae.paper import render_paper
from gyeoljae.web import make_server

XSS = "<script>alert(1)</script><img src=x onerror=alert(2)>"
ITEMS = [{"item": "모니터", "qty": 2, "price": "30만"}, {"item": "케이블", "qty": 3, "price": "5,000"}]
SAMPLE = {
    "general": {"purpose": "시범 운영", "due": "2026-12-01"},
    "report": {"kind": "주간보고", "period": {"from": "2026-11-09", "to": "2026-11-13"}, "done": "실적1\n실적2"},
    "expense": {"purpose": "장비", "items": {"rows": [dict(ITEMS[0], amount="60만"), dict(ITEMS[1], amount="15,000")],
                                             "sum": {"amount": "615,000"}}, "pay": "법인카드", "pay_date": "2026-11-30", "payee": "가게"},
    "purchase": {"purpose": "구매", "items": ITEMS, "vendor": "업체", "need_by": "2026-12-10"},
    "trip": {"dest": "부산", "purpose": "고객 방문", "period": {"from": "2026-11-24", "to": "2026-11-27"},
             "transport": "항공", "budget": "30만"},
    "trip_settle": {"period": {"from": "2026-11-24", "to": "2026-11-27"}, "items": ITEMS, "pay": "계좌이체"},
    "claim": {"purpose": "회의비", "items": ITEMS, "pay": "현금"},
    "leave": {"kind": "연차", "period": {"from": "2026-11-24", "to": "2026-11-27"}, "backup": "개발장"},
    "coop": {"to_dept": "영업", "subject": "자료 협조", "due": "2026-11-20"},
    "official": {"recv": "○○기관장", "via": "", "sender": "주식회사 시험 대표이사", "contact": "개발1 02-000-0000"},
}


class PaperTest(unittest.TestCase):
    def setUp(self):
        self.app, self.d = world()

    def tearDown(self):
        rmtree(self.d)

    def _doc(self, code, **kw):
        m = self.app.create_draft("dev1", kw.pop("title", f"{code} 시험"), kw.pop("body", "본문"), ["devlead"],
                                  form=code, fields=kw.pop("fields", SAMPLE[code]),
                                  attachments=[("a.pdf", b"%PDF"), ("b.hwpx", b"PK")], **kw)
        return self.app.show("dev1", m["id"], mark_read=False)

    def test_every_builtin_both_layouts(self):
        defs = dict(fdm.builtin_defs())
        self.assertEqual(defs["official"]["layout"], "gov")
        self.assertTrue(all(d["layout"] == "" for c, d in defs.items() if c != "official"))
        for code, fd in defs.items():
            m = self._doc(code)
            for lay in ("corporate", "gov"):
                with self.subTest(code=code, layout=lay):
                    h = render_paper(self.app, "dev1", m, lay)
                    self.assertIn(f"class='sheet {lay}'", h)
                    self.assertNotIn("결재 금액", h)  # 사용자 결정: 금액 상자·한글 금액 없음
                    self.assertNotIn("원정", h)
                    self.assertIn("a.pdf", h)
                    if lay == "corporate":
                        self.assertIn("class=stamp", h)
                        self.assertIn("1. a.pdf  2. b.hwpx", h)
                    else:
                        self.assertNotIn("class=stamp", h)
                        self.assertIn("기안자", h)
                        self.assertIn("1. a.pdf 1부.  2. b.hwpx 1부.  끝.", h)
            h = render_paper(self.app, "dev1", m)  # 양식 기본
            self.assertIn("sheet gov" if code == "official" else "sheet corporate", h)
        h = render_paper(self.app, "dev1", self._doc("expense"))
        self.assertIn("지 출 결 의 서", h)
        self.assertIn("합 계", h)
        self.assertIn("615,000", h)  # 기안자가 적은 합계 그대로
        self.assertIn("600,000", h)
        self.assertRegex(h, r"<th scope=row>기안자</th><td>\S+ 개발1</td>")  # "직위 이름", 사번 없음
        self.assertIn("2026-11-30", h)  # 둘째 날짜(지급일) 머리표
        h = render_paper(self.app, "dev1", self._doc("leave"))
        self.assertIn("■연차", h)
        self.assertIn("□병가", h)
        self.assertIn("2026-11-24 (화) ~ 2026-11-27 (금)", h)
        self.assertIn("<b>4일</b>", h)
        self.assertIn("주말 제외", h)
        self.assertNotIn("금액", h)
        h = render_paper(self.app, "dev1", self._doc("trip"))
        self.assertIn("예상 경비", h)
        self.assertIn("300,000원", h)
        self.assertNotIn("결재 금액", h)
        self.assertIn("출 장 신 청 서", h)

    def test_freeform_and_default_setting(self):
        a = self.app
        m = a.create_draft("dev1", "자유", "내용", ["devlead"])
        m = a.show("dev1", m["id"], mark_read=False)
        h = render_paper(a, "dev1", m)
        self.assertIn("기 안 문", h)
        self.assertIn("sheet corporate", h)
        a.setting_set("admin", "paper_default", "gov")
        a.setting_set("admin", "company_name", "시험상사")
        h = render_paper(a, "dev1", m)
        self.assertIn("sheet gov", h)
        self.assertIn("내부결재", h)
        self.assertIn("시험상사 전자결재", h)
        h = render_paper(a, "dev1", self._doc("expense"))  # 양식에 layout 없음 → 설정
        self.assertIn("sheet gov", h)
        with self.assertRaises(Invalid):
            a.setting_set("admin", "paper_default", "fancy")
        with self.assertRaises(Invalid):
            fdm.check_def(dict(fdm.BUILTIN[0][1], layout="x"))

    def test_doc_mark(self):
        """문서 표시(예: 대외비): 기업=결재란 위 오른쪽, 공문서=기관명 위 왼쪽. 20자 이내, 관리자 화면·CLI."""
        import os
        import subprocess
        import sys
        from pathlib import Path
        a = self.app
        m = self._doc("general")
        self.assertNotIn("dmark", render_paper(a, "dev1", m, "corporate"))
        a.setting_set("admin", "doc_mark", "  대외비\x07 ")
        self.assertEqual(a.paper_settings()["doc_mark"], "대외비")
        h = render_paper(a, "dev1", m, "corporate")
        self.assertLess(h.index("<p class='dmark r'>대외비</p>"), h.index("class=stamp"))
        h = render_paper(a, "dev1", m, "gov")
        self.assertLess(h.index("<p class=dmark>대외비</p>"), h.index("class=ghead"))
        a.setting_set("admin", "doc_mark", "<b>")
        self.assertIn("&lt;b&gt;", render_paper(a, "dev1", m, "gov"))
        with self.assertRaises(Invalid):
            a.setting_set("admin", "doc_mark", "가" * 21)
        with self.assertRaises(Exception):
            a.setting_set("dev1", "doc_mark", "x")  # 관리자만
        env = dict(os.environ, GYEOLJAE_DATA=self.d, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
        r = subprocess.run([sys.executable, "-m", "gyeoljae", "-u", "admin", "setting", "set", "doc_mark", "사내한정"],
                           env=env, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(a.paper_settings()["doc_mark"], "사내한정")
        srv = make_server(a, "127.0.0.1", 0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            c = Client(f"http://127.0.0.1:{srv.server_address[1]}")
            c.login("admin", "adminpass1")
            st, body, _ = c.req("/admin/changes")
            self.assertIn("aria-label='문서 표시' maxlength=20", body)
            tok = c.csrf("/admin/changes")
            st = c.req("/admin/changes", {"csrf": tok, "op": "setting", "key": "doc_mark", "value": ""})[0]
            self.assertEqual(st, 303)
            self.assertEqual(a.paper_settings()["doc_mark"], "")
        finally:
            srv.shutdown()
            srv.server_close()

    def test_stamp_states(self):
        a = self.app
        m = a.create_draft("dev1", "전결 시험", "x", ["devlead!", "ceo"], submit=True)
        a.approve("devlead", m["id"])
        m = a.show("dev1", m["id"], mark_read=False)
        h = render_paper(a, "dev1", m, "corporate")
        self.assertIn("<span class=seal>개발장</span><span class=jl>전결</span>", h)
        self.assertIn("<span class=skip>생략</span>", h)
        self.assertIn("<span class=seal>개발1</span>", h)
        self.assertIn("결재 완료", h)
        g = render_paper(a, "dev1", m, "gov")
        self.assertIn("결재권자(전결)", g)
        self.assertIn("생략: 대표", g)
        self.assertIn(m["number"], g)
        m2 = a.create_draft("dev1", "진행", "x", ["devlead", "ceo"], submit=True)
        a.approve("devlead", m2["id"])
        h = render_paper(a, "dev1", a.show("dev1", m2["id"], mark_read=False))
        self.assertIn("결재 진행 중 (1/2)", h)
        self.assertIn("결재중", h)
        self.assertNotIn("전결", h)

    def test_official_layout(self):
        h = render_paper(self.app, "dev1", self._doc("official"))
        self.assertNotIn("class=stamp", h)
        self.assertIn("직인", h)
        self.assertIn("주식회사 시험 대표이사", h)
        self.assertIn("○○기관장", h)
        self.assertIn("시행", h)
        self.assertIn("접수", h)
        self.assertIn("개발1 02-000-0000", h)

    def test_escaping(self):
        a = self.app
        m = self._doc("expense", title=XSS, body=XSS,
                      fields=dict(SAMPLE["expense"], purpose=XSS, payee=XSS, items=[dict(ITEMS[0], item=XSS)]))
        a.form_save("admin", "evil", {"name": "<b>악성</b>", "fields": [
            {"key": "k", "label": "<script>x</script>", "type": "select", "options": ["<i>a</i>", "b"]}]}, new=True)
        e = a.create_draft("dev1", "t", "b", ["devlead"], form="evil", fields={"k": "<i>a</i>"})
        e = a.show("dev1", e["id"], mark_read=False)
        for doc in (m, e):
            for lay in ("corporate", "gov"):
                h = render_paper(a, "dev1", doc, lay)
                self.assertNotIn("<script", h)
                self.assertNotIn("<img", h)
                self.assertNotIn("<i>", h)
                self.assertNotIn("<b>악", h)
        self.assertIn("■&lt;i&gt;a&lt;/i&gt;", render_paper(a, "dev1", e))


class PrintRouteTest(unittest.TestCase):
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

    def test_print_route(self):
        m = self.app.create_draft("dev1", "<인쇄>", "본문", ["devlead"], cc=["hr"], submit=True,
                                  form="leave", fields=SAMPLE["leave"])
        for u in ("dev1", "devlead", "hr"):
            c = Client(self.base)
            c.login(u)
            st, body, h = c.req(f"/doc/{m['id']}/print")
            self.assertEqual(st, 200, u)
            self.assertIn("휴 가 신 청 서", body)
            self.assertIn("/paper.css", body)
            self.assertNotIn("<인쇄>", body)
            self.assertNotIn("<script", body)
        c = Client(self.base)
        c.login("sal1")
        st, body, _ = c.req(f"/doc/{m['id']}/print")
        self.assertEqual(st, 404)
        self.assertNotIn("휴 가", body)
        st, css, h = c.req("/paper.css")
        self.assertEqual(st, 200)
        self.assertIn("@page{size:A4;margin:30mm 15mm 15mm 20mm}", css)
        c = Client(self.base)
        c.login("dev1")
        st, body, _ = c.req(f"/doc/{m['id']}")
        self.assertIn("출력 미리보기", body)
        self.assertIn("class='sheet corporate'", body)


if __name__ == "__main__":
    unittest.main()
