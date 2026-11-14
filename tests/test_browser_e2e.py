"""실제 브라우저(Playwright + headless Chromium)로 기안 화면 → hanword 편집기 → 사진 넣기 → 상신 → 다른 사람 결재.

opt-in: GYEOLJAE_BROWSER_E2E=1 일 때만 돈다(CI 는 건너뜀). Playwright(pip) 가 없거나 브라우저를 못 띄우면 건너뜀.
브라우저 찾는 순서: GYEOLJAE_TEST_CHROME → Playwright 기본 → 시스템 Chrome/Chromium → Playwright 캐시의 다른 판.
확인하는 것:
  1) 편집기에 친 글과 넣은 사진(3200×2400)이 상신 뒤 정본(doc_html)에 들어가고, 사진은 긴 변 1600 JPEG 로 줄어 있음
  2) 다른 사람(결재자)이 브라우저에서 결재 → 확인 → 완료, 완료 문서 화면에 글·사진이 보임
  3) 줄여도 8MB 를 넘는 사진 묶음은 보내지 않고 8MB 안내(alert), 편집기 내용·화면은 그대로, 문서가 생기지 않음
"""
import base64
import glob
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

from helpers import rmtree

from gyeoljae.core import App

ROOT = Path(__file__).resolve().parents[1]
PW = "demo1234!"  # seed-demo 비밀번호
ERR_8MB = "사진 포함 최대 8MB"

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _candidates():
    yield os.environ.get("GYEOLJAE_TEST_CHROME") or None
    yield None  # Playwright 가 받아 둔 기본 브라우저
    for n in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        if shutil.which(n):
            yield shutil.which(n)
    cache = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or Path.home() / ".cache" / "ms-playwright")
    for pat in ("chromium-*/chrome-linux*/chrome", "chromium_headless_shell-*/chrome-headless-shell-linux*/"
                "chrome-headless-shell", "chromium-*/chrome-mac*/Chromium.app/Contents/MacOS/Chromium",
                "chromium-*/chrome-win*/chrome.exe"):
        yield from sorted(glob.glob(str(cache / pat)), reverse=True)


def _launch(p):
    seen = set()
    for exe in _candidates():
        if exe in seen or (exe and not os.path.exists(exe)):
            continue
        seen.add(exe)
        try:
            return p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
        except Exception:  # 판 불일치·없음 → 다음 후보
            continue
    return None


def _jpeg_size(data: bytes):
    """JPEG SOFn 에서 (가로, 세로)."""
    i = 2
    while i < len(data) - 9:
        if data[i] != 0xFF:
            i += 1
            continue
        m = data[i + 1]
        if m in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            return int.from_bytes(data[i + 7:i + 9], "big"), int.from_bytes(data[i + 5:i + 7], "big")
        if m in (0xD8, 0x01) or 0xD0 <= m <= 0xD7:
            i += 2
            continue
        i += 2 + int.from_bytes(data[i + 2:i + 4], "big")
    return None


# 브라우저 캔버스로 시험 사진 만들기(PIL 없이). noise=true 면 압축이 잘 안 되는 사진(8MB 시험용).
MKIMG = """([w, h, q, noise, seed]) => {
  const c = document.createElement('canvas'); c.width = w; c.height = h; const g = c.getContext('2d');
  if (noise) {
    const d = g.createImageData(w, h); let x = seed * 2654435761 >>> 0;
    for (let i = 0; i < d.data.length; i++) { x ^= x << 13; x ^= x >>> 17; x ^= x << 5; d.data[i] = x & 255; }
    g.putImageData(d, 0, 0);
  } else {
    const gr = g.createLinearGradient(0, 0, w, h); gr.addColorStop(0, '#2a6'); gr.addColorStop(1, '#fc3');
    g.fillStyle = gr; g.fillRect(0, 0, w, h); g.fillStyle = '#123'; g.font = '200px sans-serif';
    g.fillText('E2E ' + seed, 200, h / 2);
  }
  return c.toDataURL('image/jpeg', q).split(',')[1];
}"""


@unittest.skipUnless(os.environ.get("GYEOLJAE_BROWSER_E2E") == "1", "opt-in: GYEOLJAE_BROWSER_E2E=1")
@unittest.skipUnless(sync_playwright, "Playwright 없음(pip install playwright)")
class BrowserE2ETest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="gyeol-bro-"))
        cls.data = cls.tmp / "data"
        env = dict(os.environ, GYEOLJAE_DATA=str(cls.data), PYTHONIOENCODING="utf-8",
                   PYTHONPATH=str(ROOT / "src"))
        cls.env = env
        subprocess.run([sys.executable, "-m", "gyeoljae", "seed-demo"], check=True, capture_output=True,
                       env=env, timeout=120)
        cls.port = _free_port()
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.srv = subprocess.Popen([sys.executable, "-m", "gyeoljae", "serve", "--port", str(cls.port)], env=env,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(150):
            try:
                urllib.request.urlopen(cls.base + "/healthz", timeout=1).read()
                break
            except OSError:
                time.sleep(0.1)
        cls.pw = sync_playwright().start()
        cls.browser = _launch(cls.pw)

    @classmethod
    def tearDownClass(cls):
        try:
            if cls.browser:
                cls.browser.close()
            cls.pw.stop()
        finally:
            cls.srv.terminate()
            try:
                cls.srv.wait(10)
            except subprocess.TimeoutExpired:
                cls.srv.kill()
            rmtree(str(cls.tmp))

    def setUp(self):
        if not self.browser:
            self.skipTest("headless Chromium 을 띄우지 못함")
        self.ctxs = []

    def tearDown(self):
        for c in self.ctxs:
            c.close()

    # ------------------------------------------------------------ 도움
    def login(self, user):
        ctx = self.browser.new_context(viewport={"width": 1400, "height": 1000})
        self.ctxs.append(ctx)
        pg = ctx.new_page()
        pg.set_default_timeout(30000)
        self.errors = []
        pg.on("pageerror", lambda e: self.errors.append(str(e)))
        pg.goto(self.base + "/login")
        pg.fill("input[name=username]", user)
        pg.fill("input[name=password]", PW)
        pg.click("form[action='/login'] button")
        pg.wait_for_load_state()
        self.assertNotIn("/login", pg.url, f"{user} 로그인 실패")
        return pg

    def make_images(self, pg, specs):
        out = []
        for i, (w, h, q, noise) in enumerate(specs):
            p = self.tmp / f"img-{len(list(self.tmp.glob('img-*')))}-{i}.jpg"
            p.write_bytes(base64.b64decode(pg.evaluate(MKIMG, [w, h, q, noise, i + 1])))
            out.append(p)
        return out

    def open_editor(self, pg, title):
        pg.goto(self.base + "/new?form=general")
        fr = pg.frame_locator("#edframe")
        frame = pg.frame("edframe")
        frame.wait_for_function("() => window.WP && document.getElementById('page')")
        # 'ready' → host 'load'(양식 본문) 까지: 편집기 입력칸이 보이고 입력칸(.edfb)은 숨겨짐
        pg.wait_for_function("() => !document.getElementById('edhost').hidden && "
                             "[...document.querySelectorAll('.edfb')].every(x => x.hidden)")
        pg.wait_for_timeout(500)
        pg.fill("#ti", title)
        pg.fill("#ln", "devlead")
        return fr, frame

    def type_text(self, fr, text):
        page = fr.locator("#page")
        page.click()
        page.press("Control+End")
        page.press("Enter")
        page.type(text)

    def insert_image(self, pg, frame, path):
        n = frame.evaluate("() => document.querySelectorAll('#page img').length")
        with pg.expect_file_chooser() as fc:
            frame.locator("#page").press("Control+n")
            frame.locator("#page").press("i")
        fc.value.set_files(str(path))
        frame.wait_for_function(f"() => document.querySelectorAll('#page img').length > {n}")

    # ------------------------------------------------------------ 시나리오
    def test_1_editor_photo_submit_approve(self):
        pg = self.login("dev1")
        fr, frame = self.open_editor(pg, "브라우저 E2E 품의")
        text = "브라우저로 쓴 본문 E2E 확인"
        self.type_text(fr, text)
        big, = self.make_images(pg, [(3200, 2400, 0.95, False)])
        self.insert_image(pg, frame, big)
        with pg.expect_navigation(url=re.compile(r"/doc/\d+")):
            pg.click("button[name=go][value=submit]")
        did = int(re.search(r"/doc/(\d+)", pg.url).group(1))
        self.assertEqual(self.errors, [])

        app = App(str(self.data))
        m = app.show("dev1", did)
        self.assertEqual(m["status"], "pending", m["status"])
        html = m["doc_html"]
        self.assertIn(text, html)
        imgs = re.findall(r"data:image/(\w+);base64,([A-Za-z0-9+/=]+)", html)
        self.assertEqual(len(imgs), 1, "사진 1장")
        self.assertEqual(imgs[0][0], "jpeg")
        raw = base64.b64decode(imgs[0][1])
        self.assertEqual(_jpeg_size(raw), (1600, 1200), "긴 변 1600 으로 줄어야 함")
        self.assertLess(len(raw), big.stat().st_size)
        # 기안자 화면에도 글이 보임
        self.assertIn(text, pg.content())

        # 다른 사람(결재자 devlead)이 브라우저로 결재
        ap = self.login("devlead")
        ap.goto(f"{self.base}/doc/{did}")
        self.assertIn(text, ap.content())
        ap.fill("#cmt", "E2E 승인")
        ap.click("button[name=act][value=approve]")
        ap.wait_for_load_state()
        ap.click(f"form[action='/doc/{did}/approve'] button")
        ap.wait_for_load_state()
        self.assertEqual(self.errors, [])

        m = app.show("dev1", did)
        self.assertEqual(m["status"], "done", m["status"])
        self.assertTrue(m.get("number"), "완료 문서 번호")
        self.assertEqual(m["doc_html"], html, "완료 뒤 본문 그대로")
        pg.goto(f"{self.base}/doc/{did}")
        body = pg.content()
        self.assertIn(text, body)
        self.assertIn("E2E 승인", body)
        shown = pg.locator(".dochtml img").evaluate_all(
            "els => els.filter(e => e.complete && e.naturalWidth === 1600 && e.offsetParent).length")
        self.assertGreaterEqual(shown, 1, "완료 문서에 사진이 보임(본문 카드·종이 보기)")

    def test_2_over_8mb_is_not_sent(self):
        pg = self.login("dev1")
        app = App(str(self.data))
        before = {b: len(app.list_box("dev1", b)) for b in ("drafts", "progress")}
        fr, frame = self.open_editor(pg, "너무 큰 사진")
        self.type_text(fr, "8MB 시험")
        photos = self.make_images(pg, [(1600, 1200, 0.95, True)] * 6)  # 줄여도 장당 ~1.7MB → 합 8MB 넘음
        for p in photos:
            self.insert_image(pg, frame, p)
        msgs = []

        def on_dialog(d):
            msgs.append(d.message)
            d.dismiss()
        pg.on("dialog", on_dialog)
        url = pg.url
        pg.click("button[name=go][value=submit]")
        for _ in range(600):
            if msgs:
                break
            pg.wait_for_timeout(100)
        self.assertEqual(len(msgs), 1, "8MB 안내가 떠야 함")
        self.assertIn(ERR_8MB, msgs[0])
        self.assertEqual(pg.url, url, "보내지 않음")
        self.assertEqual(frame.evaluate("() => document.querySelectorAll('#page img').length"), 6, "편집기 내용 그대로")
        self.assertIn("8MB 시험", frame.locator("#page").inner_text())
        self.assertEqual({b: len(app.list_box("dev1", b)) for b in before}, before, "문서가 생기지 않음")
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
