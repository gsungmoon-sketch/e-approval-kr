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
