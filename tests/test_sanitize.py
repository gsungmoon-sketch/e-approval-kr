import unittest

from gyeoljae.sanitize import MAX_DEPTH, clean_html, clean_style

PNG = "data:image/png;base64,iVBORw0KGgo="


class SanitizeTest(unittest.TestCase):
    def ok_safe(self, out):
        low = out.lower()
        for bad in ("<script", "onerror", "onload", "javascript:", "<svg", "<iframe", "<style", "expression",
                    "<math", "<object", "<embed", "<form", "<input", "url(", "<!--"):
            self.assertNotIn(bad, low, out)

    def test_keeps_allowed(self):
        s = ("<p class='x'><b>굵게</b> <em>e</em><br><span style='color:#c00;font-weight:700'>빨강</span></p>"
             "<table><tr><td colspan=2 data-field='amount'>1</td></tr></table><hr><ul><li>a</li></ul>")
        out = clean_html(s)
        self.assertIn('<p class="x"><b>굵게</b> <em>e</em><br>', out)
        self.assertIn('<span style="color:#c00;font-weight:700">빨강</span>', out)
        self.assertIn('<td colspan="2" data-field="amount">1</td>', out)
        self.assertIn("<hr><ul><li>a</li></ul>", out)

    def test_xss_vectors(self):
        vecs = [
            "<img src=x onerror=alert(1)>",
            "<IMG SRC=\"javascript:alert('x')\">",
            "<a href=\"javascript:alert(1)\">link</a>",
            "<a href=\"jav&#x09;ascript:alert(1)\">t</a>",
            "<svg onload=alert(1)><circle/></svg>",
            "<svg><script>alert(1)</script></svg>",
            "<SCRIPT>alert(1)</SCRIPT>",
            "<scr<script>ipt>alert(1)</script>",
            "<script>alert(1)",
            "<style>body{background:url(javascript:alert(1))}</style>",
            "<div style=\"width:expression(alert(1))\">x</div>",
            "<div style=\"background-color:url(javascript:alert(1))\">x</div>",
            "<div style=\"color:red;behavior:url(x.htc)\">x</div>",
            "<p onclick=alert(1) ONMOUSEOVER=alert(2)>x</p>",
            "<iframe src=//evil></iframe>",
            "<object data=x></object><embed src=x>",
            "<form action=//evil><input name=a><button>b</button></form>",
            "<math><mi xlink:href=javascript:alert(1)>x</mi></math>",
            "<!--<script>alert(1)</script>-->",
            "<img src=\"data:text/html;base64,PHNjcmlwdD4=\">",
            "<img src=\"/doc/1/file/1?x=javascript:\">",
            "&lt;script&gt;alert(1)&lt;/script&gt;",
            "<p title=\"&quot;><script>alert(1)</script>\">x</p>",
            "<div style=\"color:&#x72;ed;background:u&#114;l(x)\">x</div>",
            "<img/src=x/onerror=alert(1)>",
            "<b <script>alert(1)//",
            "<p>unclosed <b>bold <i>it",
            "<noscript><p title=\"</noscript><img src=x onerror=alert(1)>\">",
            "<textarea><script>alert(1)</script></textarea>",
        ]
        for v in vecs:
            with self.subTest(v=v):
                out = clean_html(v)
                self.ok_safe(out)
                self.assertNotIn("href", out)

    def test_entities_stay_text(self):
        out = clean_html("&lt;script&gt;alert(1)&lt;/script&gt; &amp; <b>x</b>")
        self.assertEqual(out, "&lt;script&gt;alert(1)&lt;/script&gt; &amp; <b>x</b>")

    def test_drop_content(self):
        self.assertEqual(clean_html("a<script>evil()</script>b<style>p{}</style>c"), "abc")
        self.assertEqual(clean_html("a<svg><text>t</text></svg>b"), "ab")
        self.assertEqual(clean_html("<a href='javascript:x'>link</a>"), "<a>link</a>")  # href 만 버림(hanword 와 같음)
        self.assertEqual(clean_html("<a href='https://x.kr/' onclick=y>l</a>"),
                         '<a href="https://x.kr/" rel="noopener noreferrer">l</a>')
        self.assertEqual(clean_html("<blink>a</blink>"), "a")  # 모르는 태그는 글자만
        self.assertEqual(clean_html("x<!-- c -->y"), "xy")

    def test_unclosed_and_case(self):
        self.assertEqual(clean_html("<P>a<B>b"), "<p>a<b>b</b></p>")
        self.assertEqual(clean_html("<p>a</b></p></div>"), "<p>a</p>")
        self.assertEqual(clean_html("<p><b>a</p>c"), "<p><b>a</b></p>c")

    def test_img_src(self):
        self.assertEqual(clean_html(f"<img src='{PNG}' alt='그림'>"), f'<img src="{PNG}" alt="그림">')
        for bad in ("http://evil/x.png", "//evil/x.png", "data:image/svg+xml;base64,PHN2Zz4=", "/doc/1/file/../x",
                    "/doc/12/file/3",  # 같은 출처 URL 도 금지(hanword 규칙: data: 만)
                    "javascript:alert(1)", ""):
            self.assertEqual(clean_html(f"<img src='{bad}'>"), "", bad)

    def test_style_filter(self):
        self.assertEqual(clean_style("text-align:center; border-top:1px solid #000; position:fixed"),
                         "text-align:center;border-top:1px solid #000")
        self.assertEqual(clean_style("color:red;background-image:url(x)"), "")
        self.assertEqual(clean_style("width:\\65xpression(1)"), "")
        self.assertEqual(clean_style("color:red/**/"), "")
        self.assertEqual(clean_html("<p style='position:absolute'>x</p>"), "<p>x</p>")

    def test_attrs(self):
        out = clean_html("<td colspan=99999 rowspan=2 class='a\"b' data-row='1' id=x name=y>t</td>")
        self.assertEqual(out, '<td rowspan="2" data-row="1">t</td>')

    def test_formula_rules(self):  # hanword 0.1.10 표 계산식: td/th·고정 문법만, 첫 속성만, 편집기 계산 비용 상한
        from gyeoljae.sanitize import FORMULA_MAX, FORMULA_WORK
        self.assertIn('<td data-formula="SUM(A1:B3)">', clean_html('<table><tr><td data-formula="SUM(A1:B3)">1</td></tr></table>'))
        self.assertNotIn("data-formula", clean_html('<table><tr><td data-formula="bad" data-formula="SUM(LEFT)">1</td></tr></table>'))
        self.assertNotIn("data-formula", clean_html('<p data-formula="SUM(LEFT)">1</p>'))
        row = '<tr><td data-formula="SUM(ABOVE)">1</td></tr>'
        self.assertEqual(clean_html("<table>" + row * FORMULA_MAX + "</table>").count("data-formula"), FORMULA_MAX)
        out = clean_html("<table>" + row * (FORMULA_MAX + 1) + "</table>")
        self.assertNotIn("data-formula", out)
        self.assertEqual(out.count("<td>1</td>"), FORMULA_MAX + 1)  # 값은 그대로
        big = "<table>" + row * 10 + "<tr><td>2</td></tr>" * (FORMULA_WORK // 10) + "</table>"
        self.assertNotIn("data-formula", clean_html(big, limit=False))
        # 상한은 표마다(hanword 0.1.11 예산과 같게): 여러 표에 나뉜 계산식은 그대로, 큰 표 옆의 작은 표는 곱에 안 섞임
        two = ("<table>" + row * 200 + "</table>") * 2
        self.assertEqual(clean_html(two).count("data-formula"), 400)
        side = "<table>" + "<tr><td>2</td></tr>" * 20000 + "</table><table>" + row * 200 + "</table>"
        self.assertEqual(clean_html(side, limit=False).count("data-formula"), 200)
        nest = "<table><tr><td><table>" + row * 200 + "</table></td></tr>" + row * 200 + "</table>"
        self.assertEqual(clean_html(nest).count("data-formula"), 400)  # 중첩 표 칸은 안쪽 표에
        self.assertNotIn("data-formula", clean_html(nest.replace("</table></td>", row * 101 + "</table></td>")))
        # 문서 전체 뒷받침: 서버는 표 안 <table> 을 중첩으로 보지만 브라우저는 앞 표를 닫음(감사 사례) → 전체 칸 수로 막음
        from gyeoljae.sanitize import FORMULA_TOTAL
        f = '<td data-formula="SUM(ABOVE)">1</td>'
        trick = "<table><tr><td>" + ("<table><table></table>" + f * 299 + '<td data-formula="SUM(ABOVE)">1') * 20
        self.assertNotIn("data-formula", clean_html(trick, limit=False))
        many = ("<table>" + row * 300 + "</table>") * (FORMULA_TOTAL // 300)
        self.assertEqual(clean_html(many, limit=False).count("data-formula"), FORMULA_TOTAL)
        self.assertNotIn("data-formula", clean_html(many + "<table>" + row + "</table>", limit=False))

    def test_browser_parity_edges(self):
        self.assertNotIn("hidden", clean_html("<title/>hidden<p>vis</p></title><p>after</p>"))
        self.assertIn("after", clean_html("<title/>hidden<p>vis</p></title><p>after</p>"))
        self.assertNotIn("X", clean_html("<dialog><plaintext></dialog><p>X</p>"))
        self.assertIn("본문", clean_html('<meta charset="utf-8"><frame><p>본문</p>'))

    def test_limits(self):
        with self.assertRaises(ValueError):
            clean_html("a" * (2 * 1024 * 1024 + 1))
        out = clean_html("<div>" * 200 + "x" + "</div>" * 200)
        self.assertEqual(out.count("<div>"), MAX_DEPTH)
        self.assertEqual(out.count("</div>"), MAX_DEPTH)
        self.assertIn("x", out)
        self.assertEqual(clean_html(None), "")


if __name__ == "__main__":
    unittest.main()
