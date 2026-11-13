import json
import re
import threading
import unittest

from helpers import rmtree, world
from test_policy import EXPENSE

from gyeoljae import doctemplate as dt
from gyeoljae import formdef as fdm
from gyeoljae.core import Invalid
from gyeoljae.paper import render_paper


def filled(fd, purpose="장비", rows=(("모니터", "2", "30만원", "1"),), pay="법인카드", tamper_sum="1"):
    """기본 서식에 값 채우기(브라우저 편집기가 하는 일 흉내). 금액 칸·합계 칸도 기안자가 적은 값 그대로."""
    h = dt.default_template(fd)
    h = h.replace('<td data-field="purpose"></td>', f'<td data-field="purpose">{purpose}</td>', 1)
    h = h.replace('<td data-field="pay"></td>', f'<td data-field="pay">{pay}</td>', 1)
    empty = ('<tr data-row="items"><td data-field="item"></td><td data-field="qty"></td><td data-field="price"></td>'
             '<td data-field="amount"></td>')
    for it, q, p, a in rows:
        h = h.replace(empty, f'<tr data-row="items"><td data-field="item">{it}</td><td data-field="qty">{q}</td>'
                             f'<td data-field="price"><b>{p}</b></td><td data-field="amount">{a}</td>', 1)
    return h.replace('<td data-sum="items.amount"></td>', f'<td data-sum="items.amount">{tamper_sum}</td>')


class TemplateTest(unittest.TestCase):
    def setUp(self):
        self.d = dict(fdm.builtin_defs())

    def test_default_template(self):
        for code, fd in self.d.items():
            h = dt.default_template(fd)
            for f in fd["fields"]:
                if f["type"] == "table":
                    self.assertIn(f'data-row="{f["key"]}"', h, code)
                    for s in f.get("sum", []):
                        self.assertIn(f'data-sum="{f["key"]}.{s}"', h)
                elif f["type"] != "file":
                    self.assertIn(f'data-field="{f["key"]}"', h, code)
            self.assertIn('class="docbody"', h)

    def test_extract_round_trip(self):
        fd = self.d["expense"]
        v = dt.extract_fields(fd, filled(fd, rows=(("모니터", "2", "30만원", "999"), ("케이블", "3", "1,000", ""))))
        self.assertEqual(v["purpose"], "장비")
        self.assertEqual(v["items"], {"rows": [
            {"item": "모니터", "qty": "2", "price": "30만원", "amount": "999", "note": ""},
            {"item": "케이블", "qty": "3", "price": "1,000", "amount": "", "note": ""}], "sum": {"amount": "1"}})
        n, amt = fdm.normalize(fd, v)
        self.assertIsNone(amt)  # 금액은 꺼내지 않음(정책에 안 씀)
        self.assertEqual([r["amount"] for r in n["items"]["rows"]], [999, None])  # 계산 없이 적은 그대로
        self.assertEqual(n["items"]["sum"], {"amount": 1})
        out = dt.fill(fd, dt.default_template(fd), n)  # 편집기 첫 내용용 미리 채우기
        self.assertIn('data-field="price">300,000<', out)
        self.assertIn('data-sum="items.amount">1<', out)
        lv = self.d["leave"]
        h = dt.default_template(lv).replace('<td data-field="period"></td>',
                                            '<td data-field="period">2026-11-16 ~ 2026-11-18</td>')
        self.assertEqual(dt.extract_fields(lv, h)["period"], {"from": "2026-11-16", "to": "2026-11-18"})

    def test_template_html_in_def(self):
        d = fdm.check_def({"name": "x", "fields": [{"key": "a", "label": "A"}],
                           "template_html": '<p><span data-field="a" onclick="x">A</span></p><script>1</script>'})
        self.assertEqual(d["template_html"], '<p><span data-field="a">A</span></p>')  # data-* 는 td·th·tr·table·span 만
        self.assertNotIn("template_html", fdm.check_def({"name": "x"}))  # 없으면 키도 없음(기존 버전 불변)
        with self.assertRaises(Invalid):
            fdm.check_def({"name": "x", "template_html": "a" * (513 * 1024)})


class EditorCoreTest(unittest.TestCase):
    def setUp(self):
        self.app, self.d = world()
        a = self.app
        a.emp_add("admin", "개발팀장", "DEV", "팀장", username="devtl")
        a.emp_add("admin", "사장", "MGT", "대표", username="boss")
        a.emp_add("admin", "전무", "MGT", "임원", username="cxo")
        a.policy_set("admin", "DEV", EXPENSE)
        self.fd = self.app.form_get("dev1", "expense")

    def tearDown(self):
        rmtree(self.d)

    def test_doc_html_flow(self):
        a = self.app
        h = filled(self.fd, tamper_sum="1") + '<p>본문<img src=x onerror="alert(1)"><script>alert(2)</script></p>'
        m = a.create_draft("dev1", "장비", "", form="expense", doc_html=h)
        self.assertIsNone(m["amount"])  # 금액 안 꺼냄
        self.assertEqual(m["fields"]["items"]["rows"][0]["amount"], 1)  # 적은 그대로(계산 안 함)
        self.assertEqual(m["fields"]["items"]["sum"], {"amount": 1})
        self.assertNotIn("script", m["doc_html"])
        self.assertNotIn("onerror", m["doc_html"])
        self.assertIn('data-sum="items.amount">1<', m["doc_html"])  # 서버가 다시 써 넣지 않음
        self.assertIn('<b>30만원</b>', m["doc_html"])
        self.assertIn("본문", m["body"])
        self.assertEqual(a.search("dev1", q="모니터")[0]["id"], m["id"])
        # 상신: 양식 문서는 금액 한도 전결 없음 → 소액도 전체 결재선
        m = a.edit_draft("dev1", m["id"], doc_html=filled(self.fd, rows=(("마우스", "1", "1만", "1만"),), tamper_sum="1만"),
                         add_attachments=[("r.pdf", b"%PDF")])
        a.submit("dev1", m["id"])
        m = a.show("dev1", m["id"])
        self.assertIsNone(m["amount"])
        self.assertEqual([s["final"] for s in m["line"]], [False, False, False])
        paper = render_paper(a, "dev1", dict(m, doc_html=m["doc_html"] + "<script>x</script>"))
        self.assertIn("dochtml", paper)
        self.assertNotIn("<script", paper)

    def test_hash_unchanged_without_html(self):
        a = self.app
        m = a.create_draft("dev1", "t", "본문", form="general", fields={"purpose": "p"})
        meta, body = a._read_doc(m["id"])
        self.assertNotIn("doc_sha", meta)
        core = {"id": meta["id"], "title": meta["title"], "body": body, "number": None, "drafter": "dev1",
                "attachments": [], "cc": meta["cc"], "visibility": meta["visibility"], "dept": meta["dept"],
                "doc_type": meta["doc_type"], "amount": meta["amount"], "policy": meta["policy"],
                "line": [[s["user"], s["status"], s.get("acted_by"), s.get("proxy_for"), s.get("at"),
                          s.get("comment"), s.get("final")] for s in meta["line"]],
                "form": [meta["form"]["code"], meta["form"]["version"]], "fields": meta["fields"]}
        from gyeoljae.core_base import canon, sha256
        self.assertEqual(a._content_hash(m["id"], meta, body), sha256(canon(core)))
        m2 = a.create_draft("dev1", "t", "", form="general", doc_html=dt.default_template(
            a.form_get("dev1", "general")).replace('data-field="purpose"></td>', 'data-field="purpose">p</td>'))
        meta2, body2 = a._read_doc(m2["id"])
        h1 = a._content_hash(m2["id"], meta2, body2)
        (a._doc_dir(m2["id"]) / "doc.html").write_text("<p>변조</p>", encoding="utf-8")
        self.assertNotEqual(a._content_hash(m2["id"], meta2, body2), h1)


class EditorWebTest(unittest.TestCase):
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

    def test_editor_pages_and_csp(self):
        from test_web import Client
        c = Client(self.base)
        st, _, h = c.req("/editor/embed.html")
        self.assertEqual(st, 303)
        self.assertTrue(h["Location"].startswith("/login"))
        c.login("dev1")
        st, body, h = c.req("/editor/embed.html")
        self.assertEqual(st, 200)
        self.assertEqual(h["X-Frame-Options"], "SAMEORIGIN")
        self.assertIn("script-src 'self'", h["Content-Security-Policy"])
        self.assertIn("frame-ancestors 'self'", h["Content-Security-Policy"])
        self.assertNotIn("unsafe-eval", h["Content-Security-Policy"])
        st, js, h = c.req("/editor/wp-embed.js")
        self.assertEqual((st, h["Content-Type"], h["X-Content-Type-Options"]),
                         (200, "text/javascript; charset=utf-8", "nosniff"))
        self.assertIn("location.origin", js)
        self.assertNotRegex(js, r"postMessage\([^)]*'\*'")
        self.assertEqual(c.req("/editor/../web.py")[0], 404)
        self.assertEqual(c.req("/editor/x.py")[0], 404)
        st, js, h = c.req("/static/editor-host.js")
        self.assertEqual((st, h["Content-Type"]), (200, "text/javascript; charset=utf-8"))
        st, body, h = c.req("/new?form=expense")  # 기안 화면: 편집기 틀 + 'self' 스크립트, 틀 금지 유지
        self.assertIn("script-src 'self'", h["Content-Security-Policy"])
        self.assertEqual(h["X-Frame-Options"], "DENY")
        self.assertIn("id=edframe", body)
        self.assertIn("f.items.0.qty", body)  # 스크립트 없어도 입력칸 그대로
        self.assertIn("data-row=&quot;items&quot;", body)
        st, body, h = c.req("/box/inbox")
        self.assertNotIn("script-src", h["Content-Security-Policy"])
        self.assertEqual(h["X-Frame-Options"], "DENY")
        self.assertNotIn("<script", body)

    def test_draft_post(self):
        from test_web import Client, multipart
        c = Client(self.base)
        c.login("dev1")
        tok = c.csrf("/new?form=expense")
        fd = self.app.form_get("dev1", "expense")
        base = {"csrf": tok, "form": "expense", "title": "장비", "body": "", "line": "devlead", "cc": "", "go": "save"}
        html = filled(fd, rows=(("모니터", "2", "30만", "1"),)) + "<p>사유<script>x</script></p>"
        data, ct = multipart(dict(base, doc_html=html, **{"f.purpose": "무시됨"}), [])
        st, _, h = c.req("/new", data, ctype=ct)
        self.assertEqual(st, 303)
        did = int(re.search(r"/doc/(\d+)", h["Location"]).group(1))
        m = self.app.show("dev1", did)
        self.assertEqual((m["fields"]["purpose"], m["amount"]), ("장비", None))
        self.assertNotIn("<script", m["doc_html"])
        st, body, h = c.req(f"/doc/{did}")
        self.assertIn("dochtml", body)
        self.assertNotIn("script-src", h["Content-Security-Policy"])
        st, body, _ = c.req(f"/doc/{did}/print")
        self.assertIn("30만", body)
        self.assertNotIn("결재 금액", body)
        st, body, _ = c.req(f"/doc/{did}/edit")
        self.assertIn("id=docinit", body)
        self.assertIn("모니터", body)
        # 스크립트 없음(입력칸): 그대로 동작
        data, ct = multipart(dict(base, **{"f.purpose": "모니터", "f.items.0.item": "a", "f.items.0.qty": "1",
                                           "f.items.0.price": "10"}), [])
        st, _, h = c.req("/new", data, ctype=ct)
        self.assertEqual(st, 303)
        m = self.app.show("dev1", int(re.search(r"/doc/(\d+)", h["Location"]).group(1)))
        self.assertEqual((m["amount"], m.get("doc_html")), (None, None))
        self.assertEqual(json.dumps(m["fields"]["purpose"], ensure_ascii=False), '"모니터"')

    def test_doc_html_with_attachment(self):
        """편집기 경로(doc_html)로 저장해도 같은 폼의 첨부가 함께 올라가고 종이 붙임 줄에 찍힘."""
        from test_web import Client, multipart
        c = Client(self.base)
        c.login("dev1")
        st, body, _ = c.req("/new?form=expense")
        self.assertIn("④ 붙임 (첨부 파일)", body)
        self.assertIn("개당 10MB · 합계 30MB", body)
        form = body[body.index("<form method=post action='/new'"):]
        form = form[:form.index("</form>")]
        self.assertIn("id=edframe", form)  # 편집기와 첨부 칸이 같은 <form> 안
        self.assertIn("type=file name=files", form)
        tok = c.csrf("/new?form=expense")
        fd = self.app.form_get("dev1", "expense")
        data, ct = multipart({"csrf": tok, "form": "expense", "title": "장비", "body": "", "line": "devlead", "cc": "",
                              "go": "save", "doc_html": filled(fd)}, [("files", ("영수증.pdf", b"%PDF-1.4 x"))])
        st, _, h = c.req("/new", data, ctype=ct)
        self.assertEqual(st, 303)
        did = int(re.search(r"/doc/(\d+)", h["Location"]).group(1))
        m = self.app.show("dev1", did)
        self.assertTrue(m.get("doc_html"))
        self.assertEqual([a["name"] for a in m["attachments"]], ["영수증.pdf"])
        st, body, _ = c.req(f"/doc/{did}/print")
        self.assertRegex(body, r"붙임</th><td>[^<]*1\. 영수증\.pdf")
        st, body, _ = c.req(f"/doc/{did}/edit")  # 수정 화면: 이름·크기·삭제 칸
        self.assertIn("영수증.pdf", body)
        self.assertIn("(10 B)", body)
        self.assertIn("name=remove value=", body)


if __name__ == "__main__":
    unittest.main()
