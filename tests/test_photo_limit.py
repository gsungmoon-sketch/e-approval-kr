"""본문 크기 상한: 사진 없는 본문 2MB, 사진(data:image) 든 본문 8MB, 넘으면 분명한 한국어 안내.
+ 기안 화면이 사진 줄이기 스크립트(static/img-shrink.js)를 싣는지, 가능하면 headless Chrome 으로 실제 줄이기 확인."""
import http.server
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from helpers import rmtree, world

from gyeoljae import core, sanitize
from gyeoljae.web import make_server

MB = 1024 * 1024
STATIC = Path(__file__).resolve().parents[1] / "src" / "gyeoljae" / "static"
ERR_IMG = "본문이 너무 큽니다(사진 포함 최대 8MB). 사진 수나 크기를 줄여 주세요."


def photo_html(total):
    """data:image 사진 하나로 대략 total 바이트가 되는 본문."""
    head, tail = '<p>사진</p><p><img src="data:image/jpeg;base64,', '"></p>'
    return head + "A" * (total - len(head) - len(tail)) + tail


def text_html(total):
    return "<p>" + "가" * ((total - 7) // 3) + "</p>"


class SizeLimitTest(unittest.TestCase):
    def test_limits(self):
        self.assertEqual(sanitize.size_limit(text_html(1000)), 2 * MB)
        self.assertEqual(sanitize.size_limit(photo_html(1000)), 8 * MB)
        self.assertEqual(sanitize.size_limit("<p>data:image/png;base64,AAAA</p>"), 2 * MB)  # 글자일 뿐 사진 아님
        sanitize.check_size(text_html(2 * MB - 10))
        with self.assertRaises(ValueError) as e:
            sanitize.check_size(text_html(2 * MB + 100))
        self.assertIn("최대 2MB", str(e.exception))
        sanitize.check_size(photo_html(7 * MB))
        with self.assertRaises(ValueError) as e:
            sanitize.check_size(photo_html(8 * MB + 100))
        self.assertEqual(str(e.exception), ERR_IMG)

    def test_canonical_keeps_big_photo(self):
        from gyeoljae import hanword
        out = hanword.canonical_html(photo_html(5 * MB))
        self.assertIn('src="data:image/jpeg;base64,AAAA', out)
        self.assertGreater(len(out), 5 * MB - 100)


    def test_tiny_photo_does_not_lift_text_cap(self):
        """사진 한 장(아주 작아도)이 있다고 글 상한이 8MB 로 늘지 않음: 결과에서 사진 바이트를 뺀 글이 2MB."""
        from gyeoljae import hanword
        h = '<p><img src="data:image/png;base64,AAAA"></p>' + text_html(3 * MB)
        self.assertEqual(sanitize.size_limit(h), 8 * MB)  # 입력 문턱은 통과하지만
        for f in (sanitize.clean_html, hanword.canonical_html):
            with self.assertRaises(ValueError) as e:
                f(h)
            self.assertIn("사진 말고 최대 2MB", str(e.exception))
        # 사진처럼 보이지만 정화에서 버려지는 img(svg)도 같음
        h = '<img src="data:image/svg+xml;base64,AAAA">' + text_html(3 * MB)
        with self.assertRaises(ValueError):
            sanitize.clean_html(h)

    def test_escape_growth_counted(self):
        """입력은 2MB 안이지만 정화 결과(이스케이프로 커짐)가 2MB 넘으면 거절 — 저장본은 다시 정화해도 통과."""
        h = "<p>" + "< " * (MB // 2 + MB // 4) + "</p>"
        self.assertLess(len(h.encode()), 2 * MB)
        with self.assertRaises(ValueError):
            sanitize.clean_html(h)
        ok = sanitize.clean_html(photo_html(6 * MB) + text_html(MB))
        self.assertEqual(sanitize.clean_html(ok), ok)

    def test_redisplay_without_limit(self):
        from gyeoljae import hanword
        h = photo_html(9 * MB)
        with self.assertRaises(ValueError):
            hanword.canonical_html(h)
        out = hanword.canonical_html(h + "<script>x</script>", limit=False)
        self.assertGreater(len(out), 9 * MB - 100)
        self.assertNotIn("<script", out)


class PhotoDraftWebTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app, cls.d = world()
        cls.srv = make_server(cls.app, "127.0.0.1", 0)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        rmtree(cls.d)

    def test_core_limits(self):
        m = self.app.create_draft("dev1", "사진 7MB", "", ["devlead"], form="general", doc_html=photo_html(7 * MB))
        self.assertGreater(len(self.app.show("dev1", m["id"])["doc_html"]), 7 * MB - 100)
        with self.assertRaises(core.Invalid) as e:
            self.app.create_draft("dev1", "사진 9MB", "", ["devlead"], form="general", doc_html=photo_html(9 * MB))
        self.assertEqual(str(e.exception), ERR_IMG)
        with self.assertRaises(core.Invalid) as e:
            self.app.create_draft("dev1", "글 3MB", "", ["devlead"], form="general", doc_html=text_html(3 * MB))
        self.assertIn("최대 2MB", str(e.exception))

    def test_web_post(self):
        from test_web import Client, multipart
        c = Client(self.base)
        c.login("dev1")
        tok = c.csrf("/new?form=general")
        base = {"csrf": tok, "form": "general", "title": "사진 보고", "body": "", "line": "devlead", "cc": "", "go": "save"}
        data, ct = multipart(dict(base, doc_html=photo_html(7 * MB + MB // 2)), [])
        self.assertLess(len(data), 32 * MB)  # 요청 상한(MAX_REQ) 안
        st, _, h = c.req("/new", data, ctype=ct)
        self.assertEqual(st, 303, "7.5MB 사진 본문은 저장")
        data, ct = multipart(dict(base, doc_html=photo_html(8 * MB + 1000)), [])
        st, body, _ = c.req("/new", data, ctype=ct)
        self.assertEqual(st, 400)
        self.assertIn(ERR_IMG, body)
        # 같은 크기 urlencoded(+ / = 가 %XX 로 늘어남)도 요청 상한에 안 걸림
        st, _, _ = c.req("/new", dict(base, doc_html=photo_html(7 * MB).replace("AAAA", "A+/A")))
        self.assertEqual(st, 303)

    def test_rejected_body_is_kept(self):
        """크기로 거절돼도 편집기 내용(docinit)이 다시 채워져 돌아옴 — 사용자가 내용을 잃지 않음."""
        from test_web import Client, multipart
        import html as _html
        c = Client(self.base)
        c.login("dev1")
        tok = c.csrf("/new?form=general")
        base = {"csrf": tok, "form": "general", "title": "큰 문서", "body": "", "line": "devlead", "cc": "", "go": "save"}
        for doc, err in ((photo_html(9 * MB).replace("<p>사진</p>", "<p>남겨야 할 글</p>"), ERR_IMG),
                         ("<p>남겨야 할 글</p>" + text_html(3 * MB), "최대 2MB")):
            data, ct = multipart(dict(base, doc_html=doc), [])
            st, body, _ = c.req("/new", data, ctype=ct)
            self.assertEqual(st, 400)
            self.assertIn(err, body)
            m = re.search(r"<textarea id=docinit[^>]*>(.*?)</textarea>", body, re.S)
            self.assertIsNotNone(m)
            init = _html.unescape(m.group(1))
            self.assertIn("남겨야 할 글", init)
            self.assertGreater(len(init), 3 * MB // 3)

    def test_draft_page_loads_shrink_script(self):
        from test_web import Client
        c = Client(self.base)
        c.login("dev1")
        st, body, _ = c.req("/new?form=general")
        i, j = body.find("/static/img-shrink.js?v="), body.find("/static/editor-host.js?v=")
        self.assertTrue(0 < i < j, "img-shrink.js 가 editor-host.js 보다 먼저")
        st, js, h = c.req("/static/img-shrink.js")
        self.assertEqual(st, 200)
        self.assertEqual(js, (STATIC / "img-shrink.js").read_bytes().decode("utf-8"))
        self.assertIn("javascript", h["Content-Type"])
        self.assertEqual(c.req("/static/../web.py")[0], 404)
        self.assertEqual(c.req("/static/nope.js")[0], 404)


def _chrome():
    for n in (os.environ.get("GYEOLJAE_TEST_CHROME"), "google-chrome", "google-chrome-stable", "chromium",
              "chromium-browser"):
        if n and shutil.which(n):
            return shutil.which(n)
    return None


PAGE = r"""<!doctype html><meta charset=utf-8><script src="img-shrink.js"></script><script src="t.js"></script>"""
TJS = r"""
function mk(w, h, type, q, alpha) {
  var c = document.createElement('canvas'); c.width = w; c.height = h; var g = c.getContext('2d');
  var d = g.createImageData(w, h);
  for (var i = 0; i < d.data.length; i += 4) {
    d.data[i] = (i * 7) % 251; d.data[i + 1] = (i * 13) % 253; d.data[i + 2] = (i >> 4) % 255;
    d.data[i + 3] = alpha ? ((i >> 2) % 2 ? 0 : 255) : 255;
  }
  g.putImageData(d, 0, 0); return c.toDataURL(type, q);
}
var big = mk(3200, 2400, 'image/jpeg', 0.98, false), tp = mk(40, 30, 'image/png', 0, true);
var html = '<p>a<img src="' + big + '" width="300"></p><p><img src=\'' + tp + '\'></p><img src="' + big + '">';
gyeolShrink.html(html, function (res, st) {
  var m = res.match(/src="data:image\/jpeg;base64,([^"]+)"/);
  var bin = atob(m[1]), a = new Uint8Array(bin.length);
  for (var i = 0; i < bin.length; i++) a[i] = bin.charCodeAt(i);
  createImageBitmap(new Blob([a])).then(function (b) {
    fetch('/result', {method: 'POST', body: JSON.stringify({st: st, w: b.width, h: b.height,
      tpSame: res.indexOf(tp) >= 0, width300: res.indexOf('width="300"') >= 0, beforeBig: big.length,
      jpegs: (res.match(/data:image\/jpeg/g) || []).length})});
  });
});
"""


@unittest.skipUnless(_chrome(), "headless Chrome 없음(수동 확인: docs 참조)")
class ShrinkChromeTest(unittest.TestCase):
    """실제 브라우저에서 img-shrink.js: 3200×2400 사진 → 긴 변 1600 JPEG, 작은 투명 PNG 는 그대로, 속성 유지."""

    def test_shrink_in_browser(self):
        d = Path(tempfile.mkdtemp(prefix="gyeol-js-"))
        res = {}

        class H(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *a, **k):
                super().__init__(*a, directory=str(d), **k)

            def log_message(self, *a):
                pass

            def do_POST(self):
                res["r"] = self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8")
                self.send_response(204)
                self.end_headers()
        shutil.copy(STATIC / "img-shrink.js", d / "img-shrink.js")
        (d / "t.html").write_text(PAGE, encoding="utf-8")
        (d / "t.js").write_text(TJS, encoding="utf-8")
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        p = subprocess.Popen([_chrome(), "--headless=new", "--disable-gpu", "--no-sandbox", "--no-first-run",
                              f"--user-data-dir={d / 'profile'}", "--remote-debugging-port=0",
                              f"http://127.0.0.1:{srv.server_address[1]}/t.html"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            t0 = time.time()
            while "r" not in res and time.time() - t0 < 30 and p.poll() is None:
                time.sleep(0.1)
        finally:
            p.terminate()
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()
            srv.shutdown()
            srv.server_close()
            rmtree(d)
        if "r" not in res:
            self.skipTest("headless Chrome 이 결과를 보내지 않음")
        r = json.loads(res["r"])
        self.assertEqual((r["w"], r["h"]), (1600, 1200))
        self.assertEqual(r["st"]["images"], 2)   # 같은 사진 두 번 = 한 번만 처리
        self.assertEqual(r["st"]["changed"], 1)  # 큰 사진만, 작은 투명 PNG 는 그대로
        self.assertTrue(r["tpSame"] and r["width300"])
        self.assertEqual(r["jpegs"], 2)
        self.assertLess(r["st"]["after"], r["st"]["before"] / 3)


if __name__ == "__main__":
    unittest.main()
