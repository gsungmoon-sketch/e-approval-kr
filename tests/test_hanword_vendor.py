"""반입한 hanword 편집기(vendor/hanword): 반입본 무결성, /editor/ 제공·헤더, 정본 정화, HWPX/DOCX 내려받기."""
import hashlib
import io
import json
import re
import threading
import unittest
import urllib.error
import urllib.request
import zipfile

from helpers import rmtree, world

from gyeoljae import hanword
from gyeoljae.core import Invalid
from gyeoljae.sanitize import clean_html

PNG = "data:image/png;base64,iVBORw0KGgo="
RICH = ('<div id="wpmeta" hidden data-margins="25,15,15,20"><div class="wp-header">머리말 비밀</div>'
        '<div class="wp-footer">꼬리말 비밀</div></div>'
        '<h2 style="text-align:center;font-family:\'함초롬바탕\';font-size:20pt">지출 결의서</h2>'
        '<div class="objline" data-place="char"><table border="1" cellpadding="3" width="100%" style="border-collapse:collapse">'
        '<colgroup><col width="30%"><col span="2"></colgroup><tbody>'
        '<tr data-row="items"><td data-field="item" valign="top" width="120px">모니터</td>'
        '<td data-field="품목.수량,금액" align="right" colspan="2" style="padding:2px 4px;border:1px solid #000">2</td></tr>'
        '<tr><th data-sum="items.amount" rowspan="1">1만</th><td><span data-field="비고"></span></td></tr>'
        '</tbody></table></div><p class="pgbreak"></p><hr class="wp-hr">'
        '<p style="text-indent:10pt;line-height:160%;page-break-before:always"><font face="돋움" size="3" color="#c00">글</font>'
        '<a href="https://example.com/a?b=1&amp;c=2">링크</a> <img src="' + PNG + '" alt="그림" width="50%"></p>'
        '<ol start="2" type="a"><li value="3">항목</li></ol><p><s>취</s><del>소</del><strike>선</strike></p>')


class VendorTest(unittest.TestCase):
    def test_vendor_key_is_posix(self):  # Windows 경로도 VENDOR.md·URL 키는 '/'
        from pathlib import PurePosixPath, PureWindowsPath
        base = PureWindowsPath(r"C:\app\gyeoljae\vendor\hanword")
        self.assertEqual(hanword.vendor_key(base / "licenses" / "fonts" / "Pretendard-OFL.txt", base),
                         "licenses/fonts/Pretendard-OFL.txt")
        self.assertEqual(hanword.vendor_key(PureWindowsPath(r"C:\app\gyeoljae\vendor\hanword\font\NotoSansKR.ttf"), base),
                         "font/NotoSansKR.ttf")
        pb = PurePosixPath("/opt/x/vendor/hanword")
        self.assertEqual(hanword.vendor_key(pb / "font" / "D2Coding-400.woff2", pb), "font/D2Coding-400.woff2")

    def test_vendor_md_hashes(self):
        md = (hanword.VENDOR / "VENDOR.md").read_text(encoding="utf-8")
        self.assertRegex(md, r"commit `[0-9a-f]{40}`")
        rows = re.findall(r"^([0-9a-f]{64})  (\S+)$", md, re.M)
        self.assertGreater(len(rows), 40)
        listed = {f for _, f in rows}
        for h, f in rows:
            with self.subTest(f=f):
                self.assertEqual(hashlib.sha256((hanword.VENDOR / f).read_bytes()).hexdigest(), h)
        actual = {hanword.vendor_key(p) for p in hanword.VENDOR.rglob("*")
                  if p.is_file() and "__pycache__" not in p.parts and p.name != "VENDOR.md"}
        self.assertEqual(actual, listed)  # 목록 밖 파일 없음
        for bad in ("server.py", "debug.js", "guide.html", "CLAUDE.md", "wp-public.js"):
            self.assertNotIn(bad, listed)
        self.assertNotIn("index.html", listed)  # 진입점은 업스트림 임베드 빌드 embed.html
        self.assertIn("byte-identical subset of that export", md)  # 0.1.4~ 로컬 패치 없음
        for f in listed:  # 업스트림이 뺀 폰트(나눔 서브셋·함초롬·은바탕)는 다시 넣지 않음. 나눔 원본 TTF 만 허용
            self.assertNotRegex(f, r"(?i)hamchorom|unbatang|nanum.*\.woff2")
        idx = (hanword.VENDOR / "embed.html").read_text(encoding="utf-8")
        self.assertNotRegex(idx, r"<script>|<script(?![^>]*src=)|canonical|og:|ld\+json|debug\.js|site-verification")
        self.assertIn("data-wp-embed", idx)
        self.assertIn("noindex", idx)

    def test_pipeline_keeps_hanword_markup(self):
        san = hanword.embed().sanitize_html(RICH)
        self.assertEqual(clean_html(san), san)  # 두 번째 통과가 hanword 출력을 바꾸지 않음
        out = hanword.canonical_html(RICH)
        self.assertEqual(hanword.canonical_html(out), out)  # 멱등
        for want in ('data-row="items"', 'data-field="item"', 'data-field="품목.수량,금액"', 'data-sum="items.amount"',
                     '<span data-field="비고"></span>', 'class="objline"', 'class="pgbreak"', 'class="wp-hr"',
                     'colspan="2"', 'valign="top"', 'width="120px"', '<col span="2">', 'border-collapse:collapse',
                     'face="돋움"', 'rel="noopener noreferrer"', 'src="' + PNG, "text-indent:10pt",
                     "page-break-before:always", '<ol start="2" type="a"><li value="3">', "<strike>선</strike>"):
            self.assertIn(want, out)
        # 기안문에는 머리말·꼬리말 없음: 편집기 메타(#wpmeta)와 그 안의 머리말/꼬리말은 저장되지 않음
        self.assertNotIn("머리말", out)
        self.assertNotIn("꼬리말", out)
        self.assertNotIn("wpmeta", out)

    def test_pipeline_strips_active_content(self):
        for v in ('<p>a<script>alert(1)</script>b</p>', '<img src=x onerror=alert(1)>',
                  '<p onclick="x()">t</p>', '<a href="javascript:alert(1)">x</a>', '<title/><img src=x onerror=1></title>',
                  '<img src="/doc/1/file/1">', '<td data-field="a(b)">x</td>', '<p data-field="a">x</p>',
                  '<style>p{}</style><svg onload=1>', '<p style="background:url(//e/x)">x</p>'):
            with self.subTest(v=v):
                out = hanword.canonical_html(v)
                for bad in ("<script", "onerror", "onclick", "javascript", "/doc/1/file", "data-field", "<svg",
                            "url(", "<style"):
                    self.assertNotIn(bad, out)

    def test_page_validation(self):
        self.assertEqual(hanword.parse_page("gov"), {"paper": "A4", "orient": "portrait", "mt": 30.0, "mr": 15.0,
                                                     "mb": 15.0, "ml": 20.0})
        pg = hanword.parse_page(json.dumps({"paper": "B5", "orient": "landscape", "mt": 10, "mr": 999, "ml": "x"}))
        self.assertEqual(pg, {"paper": "B5", "orient": "landscape", "mt": 10.0, "mr": 20.0, "mb": 20.0, "ml": 20.0})
        self.assertIsNone(hanword.parse_page(None))
        for bad in ("{bad", "[1,2]", "5", "evil", '"x"', json.dumps({"x" * 3: 1, **{str(i): i for i in range(9)}}), "x" * 2000):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                hanword.parse_page(bad)

    def test_converters(self):
        h = hanword.export(RICH, "hwpx")
        z = zipfile.ZipFile(io.BytesIO(h))
        self.assertEqual(z.infolist()[0].filename, "mimetype")
        self.assertEqual(z.read("mimetype"), b"application/hwp+zip")
        d = zipfile.ZipFile(io.BytesIO(hanword.export(RICH, "docx", {"paper": "A4"})))
        self.assertIn("모니터", d.read("word/document.xml").decode("utf-8"))


class VendorWebTest(unittest.TestCase):
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

    def raw(self, c, path):
        try:
            r = c.op.open(urllib.request.Request(self.base + path))
            return r.status, r.read(), r.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read(), e.headers

    def test_editor_serving(self):
        from test_web import Client
        c = Client(self.base)
        st, _, h = c.req("/editor/embed.html")
        self.assertEqual(st, 303)
        self.assertTrue(h["Location"].startswith("/login"))
        self.assertEqual(c.req("/editor/font/NotoSansKR-400.woff2")[0], 303)
        c.login("dev1")
        st, body, h = c.req("/editor/embed.html")
        self.assertEqual(st, 200)
        self.assertIn("wp-embed.js", body)
        csp = h["Content-Security-Policy"]
        for want in ("script-src 'self'", "style-src 'self' 'unsafe-inline'", "img-src 'self' data: blob:",
                     "font-src 'self'", "connect-src 'none'", "frame-ancestors 'self'", "default-src 'none'"):
            self.assertIn(want, csp)
        self.assertNotIn("unsafe-eval", csp)
        self.assertNotIn("script-src 'self' 'unsafe-inline'", csp)
        self.assertEqual(h["X-Frame-Options"], "SAMEORIGIN")
        st, _, h = c.req("/editor?x=2")
        self.assertEqual((st, h["Location"]), (303, "/editor/embed.html?x=2"))
        self.assertEqual(c.req("/editor/")[2]["Location"], "/editor/embed.html")
        self.assertEqual(self.raw(c, "/editor/index.html")[0], 404)  # 공개 사이트용 index.html 은 반입 안 함
        st, data, h = self.raw(c, "/editor/font/NotoSansKR-400.woff2")
        self.assertEqual((st, h["Content-Type"]), (200, "font/woff2"))
        self.assertEqual(data, (hanword.VENDOR / "font" / "NotoSansKR-400.woff2").read_bytes())
        self.assertEqual(self.raw(c, "/editor/font/NotoSansKR.ttf")[2]["Content-Type"], "font/ttf")
        st, _, h = c.req("/editor/html2canvas.min.js")
        self.assertEqual((st, h["Content-Type"]), (200, "text/javascript; charset=utf-8"))
        self.assertEqual(h["X-Frame-Options"], "DENY")  # 틀 허용은 편집기 HTML 만
        for bad in ("/editor/../web.py", "/editor/%2e%2e/web.py", "/editor/..%2fweb.py", "/editor/font/..%2f..%2fweb.py",
                    "/editor/hanword_embed.py", "/editor/VENDOR.md", "/editor/LICENSE", "/editor/.x.js",
                    "/editor/hwpx_skeleton.hwpx", "/editor/docs/embed.md", "/editor/a..b.js", "/editor//etc/passwd.js"):
            with self.subTest(bad=bad):
                self.assertEqual(self.raw(c, bad)[0], 404)
        st, body, h = c.req("/box/inbox")  # 나머지 화면은 그대로
        self.assertEqual(h["X-Frame-Options"], "DENY")
        self.assertIn("frame-ancestors 'none'", h["Content-Security-Policy"])

    def test_draft_page_and_export(self):
        from test_web import Client, multipart
        c = Client(self.base)
        c.login("dev1")
        st, body, h = c.req("/new?form=general")
        self.assertIn("id=edframe", body)
        self.assertIn("data-page='&quot;gov&quot;'", body)
        self.assertIn("frame-src 'self'", h["Content-Security-Policy"])
        tok = c.csrf("/new?form=general")
        base = {"csrf": tok, "form": "general", "title": "보고/1", "body": "", "line": "devlead", "cc": "", "go": "save"}
        html = ('<table><tr><th>목적</th><td data-field="purpose">시험 목적</td></tr></table>'
                '<p>본문<img src=x onerror=alert(1)><script>x</script></p>')
        page = json.dumps({"paper": "A4", "orient": "portrait", "mt": 30, "mr": 15, "mb": 15, "ml": 20})
        data, ct = multipart(dict(base, doc_html=html, doc_page=page), [])
        st, _, h = c.req("/new", data, ctype=ct)
        self.assertEqual(st, 303)
        did = int(re.search(r"/doc/(\d+)", h["Location"]).group(1))
        m = self.app.show("dev1", did)
        self.assertEqual(m["doc_page"]["mt"], 30.0)
        self.assertEqual(m["fields"]["purpose"], "시험 목적")
        self.assertNotIn("onerror", m["doc_html"])
        st, body, _ = c.req(f"/doc/{did}/edit")
        self.assertIn("&quot;mt&quot;: 30.0", body)  # 저장된 용지를 다음 load 에
        st, body, _ = c.req(f"/doc/{did}")
        self.assertIn(f"/doc/{did}/export.hwpx", body)
        self.assertIn(f"/doc/{did}/export.docx", body)
        st, data, h = self.raw(c, f"/doc/{did}/export.hwpx")
        self.assertEqual((st, h["Content-Type"]), (200, "application/hwp+zip"))
        self.assertIn("filename*=UTF-8''%EB%B3%B4%EA%B3%A0_1.hwpx", h["Content-Disposition"])
        z = zipfile.ZipFile(io.BytesIO(data))
        self.assertEqual((z.infolist()[0].filename, z.read("mimetype")), ("mimetype", b"application/hwp+zip"))
        st, data, h = self.raw(c, f"/doc/{did}/export.docx")
        self.assertEqual(st, 200)
        self.assertIn("시험 목적", zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml").decode("utf-8"))
        # 볼 수 없는 사람·없는 문서 = 404
        o = Client(self.base)
        o.login("sal1")
        self.assertEqual(self.raw(o, f"/doc/{did}/export.hwpx")[0], 404)
        self.assertEqual(self.raw(o, f"/doc/{did}/export.docx")[0], 404)
        self.assertEqual(self.raw(c, "/doc/99999/export.hwpx")[0], 404)
        self.assertEqual(self.raw(c, f"/doc/{did}/export.pdf")[0], 404)
        # 편집기 문서가 아닌 문서도 출력 화면 HTML 로 변환
        m2 = self.app.create_draft("dev1", "일반", "평문 본문", ["devlead"], form="general", fields={"purpose": "p2"})
        st, data, _ = self.raw(c, f"/doc/{m2['id']}/export.docx")
        self.assertEqual(st, 200)
        self.assertIn("평문 본문", zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml").decode("utf-8"))
        # 잘못된 용지 → 400(입력 유지)
        data, ct = multipart(dict(base, doc_html=html, doc_page="{bad"), [])
        self.assertEqual(c.req("/new", data, ctype=ct)[0], 400)
        with self.assertRaises(Invalid):
            self.app.create_draft("dev1", "x", "", form="general", doc_html=html, doc_page="[1]")


if __name__ == "__main__":
    unittest.main()
