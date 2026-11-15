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
    def login(self, user, size=(1400, 1000)):
        ctx = self.browser.new_context(viewport={"width": size[0], "height": size[1]})
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

    def wait_editor(self, pg):
        pg.wait_for_selector("#edframe", state="attached")
        for _ in range(100):
            frame = pg.frame("edframe")
            if frame:
                break
            pg.wait_for_timeout(100)
        frame.wait_for_function("() => window.WP && document.getElementById('page')")
        # 'ready' → host 'load'(양식 본문) 까지: 편집기 입력칸이 보이고 입력칸(.edfb)은 숨겨짐
        pg.wait_for_function("() => !document.getElementById('edhost').hidden && "
                             "[...document.querySelectorAll('.edfb')].every(x => x.hidden)")
        pg.wait_for_timeout(500)
        return pg.frame_locator("#edframe"), frame

    def open_editor(self, pg, title):
        pg.goto(self.base + "/new?form=general")
        fr, frame = self.wait_editor(pg)
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

    def test_3_formula_submit_approve(self):  # hanword 0.1.10 표 계산식: 편집기가 계산, 서버는 식·값을 그대로 보관
        pg = self.login("dev1")
        fr, frame = self.open_editor(pg, "계산식 E2E")
        self.type_text(fr, "계산식 표")
        frame.evaluate("""() => {
            const p = document.getElementById('page'), d = document.createElement('div');
            d.className = 'objline'; d.setAttribute('data-place', 'char');
            d.innerHTML = '<table border="1"><tbody><tr><td>1,200</td></tr><tr><td id="e2e-b">800</td></tr>' +
                          '<tr><td id="e2e-sum"><br></td></tr></tbody></table>';
            p.appendChild(d); p.appendChild(document.createElement('p'));
        }""")
        fr.locator("#e2e-sum").click()
        frame.evaluate("() => WP.dispatch('formula')")  # 입력 › 표 계산식… (우클릭 '계산식…' 과 같은 동작)
        frame.wait_for_function("() => !document.getElementById('m-formula').classList.contains('hidden')")
        self.assertEqual(frame.evaluate("() => document.getElementById('fm-prev').textContent"), "=SUM(ABOVE)")
        fr.locator("#m-formula button.ok").click()
        frame.wait_for_function("() => document.getElementById('e2e-sum').textContent === '2,000'")
        frame.evaluate("""() => {  // 캐럿을 '800' 끝에(토스트가 칸을 가릴 수 있어 클릭 대신)
            const t = document.getElementById('e2e-b').firstChild, r = document.createRange(), s = getSelection();
            document.getElementById('page').focus(); r.setStart(t, t.length); r.collapse(true);
            s.removeAllRanges(); s.addRange(r);
        }""")
        pg.keyboard.type("0")  # 800 → 8000: 표 안 입력이면 다시 계산
        frame.wait_for_function("() => document.getElementById('e2e-sum').textContent === '9,200'")
        with pg.expect_navigation(url=re.compile(r"/doc/\d+")):
            pg.click("button[name=go][value=submit]")
        did = int(re.search(r"/doc/(\d+)", pg.url).group(1))
        app = App(str(self.data))
        html = app.show("dev1", did)["doc_html"]
        self.assertRegex(html, r'<td data-formula="SUM\(ABOVE\)">9,200</td>')
        ap = self.login("devlead")
        ap.goto(f"{self.base}/doc/{did}")
        ap.click("button[name=act][value=approve]")
        ap.wait_for_load_state()
        ap.click(f"form[action='/doc/{did}/approve'] button")
        ap.wait_for_load_state()
        m = app.show("dev1", did)
        self.assertEqual(m["status"], "done", m["status"])
        self.assertEqual(m["doc_html"], html, "완료 뒤 식·값 그대로")
        pg.goto(f"{self.base}/doc/{did}")
        self.assertIn("9,200", pg.locator(".dochtml").first.inner_text())  # 보관 문서에 계산된 값
        # 같은 보관 본문을 편집기 읽기 전용으로: 다시 계산하지 않고, 찾기·문서 통계는 동작
        # (결재 화면은 스크립트 없는 서버 HTML — 편집기 iframe 을 허용하는 기안 화면 CSP 위에서 시험)
        ap.goto(self.base + "/new?form=general")
        ap.evaluate("""(h) => new Promise((ok) => {
            const f = document.createElement('iframe'); f.id = 'rof'; f.src = '/editor/embed.html';
            window.addEventListener('message', (e) => {
                if (e.source === f.contentWindow && e.data && e.data.type === 'ready') {
                    f.contentWindow.postMessage({type: 'load', html: h, readOnly: true, page: 'gov', id: 'ro'}, location.origin);
                    ok();
                }
            });
            document.body.appendChild(f); setTimeout(ok, 20000);
        })""", m["doc_html"])
        ro = ap.frame_locator("#rof")
        rof = ap.locator("#rof").element_handle().content_frame()
        rof.wait_for_function("() => window.WP && WP.embedReadOnly && WP.embedReadOnly() && "
                              "document.querySelector('#page td[data-formula]')")
        self.assertEqual(rof.evaluate("() => document.querySelector('#page td[data-formula]').textContent"), "9,200")
        st = rof.evaluate("() => WP.docStats()")
        self.assertGreater(st["ch"], 0)
        self.assertGreaterEqual(st["tables"], 1)
        rof.evaluate("() => { WP.dispatch('wordcount'); }")
        rof.wait_for_function("() => !document.getElementById('m-stats').classList.contains('hidden')")
        rof.evaluate("() => { WP.closeModal(document.getElementById('m-stats')); WP.dispatch('find'); }")
        ro.locator("#f-q").fill("9,200")
        rof.evaluate("() => WP.dispatch('findNext')")
        rof.wait_for_function("() => /1/.test(document.getElementById('f-count').textContent)")
        self.assertEqual(rof.evaluate("() => document.querySelector('#page td[data-formula]').textContent"), "9,200")
        self.assertEqual(self.errors, [])


    def test_4_toolbar97_in_embed(self):  # hanword 0.1.11 한글97식 도구 모음: 결재 편집기에서도 뜨고, 새 문서·열기·저장은 없음
        pg = self.login("dev1")
        csp = []
        pg.on("console", lambda m: csp.append(m.text) if "Content Security Policy" in m.text else None)
        fr, frame = self.open_editor(pg, "도구 모음 E2E")
        frame.wait_for_function("() => WP.toolbarLayout && WP.toolbarLayout() === '97'")
        for sel in ("#tbStd", "#tbFmt", "#tbFmt #fontSel", "#tbFmt #sizeBox", "#tbFmt #sizeSel", "#tbFmt [data-act=bold]"):
            self.assertEqual(fr.locator(sel).count(), 1, sel)
        self.assertTrue(fr.locator("#tbFmt #fontSel").is_visible())
        self.assertTrue(fr.locator("#tbFmt #sizeBox").is_visible())
        self.assertGreater(fr.locator("#tbStd button").count(), 0, "표준 도구 줄에 버튼")
        gone = ("new", "open", "save", "dlHtml")
        sel = ", ".join(f'[data-act="{a}"]' for a in gone)
        self.assertEqual(fr.locator(sel).count(), 0, "임베드: 새 문서·불러오기·저장 버튼 없음")
        acts = frame.evaluate("""() => WP.menus().flatMap(m => (m.items || []).filter(i => i !== '-').map(i => i[2]))""")
        self.assertTrue(acts, "메뉴")
        self.assertFalse(set(acts) & set(gone), "임베드: 메뉴에도 새 문서·불러오기·저장 없음")
        self.assertNotIn("guide", acts, "임베드: 사용 안내서(guide.html) 없음")
        # 진하게: 글자를 고르고 서식 줄의 '가' 단추
        self.type_text(fr, "굵게할글자")
        frame.evaluate("""() => {
            const w = document.createTreeWalker(document.getElementById('page'), NodeFilter.SHOW_TEXT);
            let n; while ((n = w.nextNode()) && n.nodeValue.indexOf('굵게할글자') < 0);
            const i = n.nodeValue.indexOf('굵게할글자'), r = document.createRange(), s = getSelection();
            r.setStart(n, i); r.setEnd(n, i + 5); s.removeAllRanges(); s.addRange(r);
        }""")
        fr.locator("#tbFmt [data-act=bold]").click()
        frame.wait_for_function("""() => [...document.querySelectorAll('#page b, #page strong, #page [style*=bold], #page [style*="700"]')]
            .some(e => e.textContent.indexOf('굵게할글자') >= 0)""")
        with pg.expect_navigation(url=re.compile(r"/doc/\d+")):
            pg.click("button[name=go][value=submit]")
        did = int(re.search(r"/doc/(\d+)", pg.url).group(1))
        html = App(str(self.data)).show("dev1", did)["doc_html"]
        self.assertRegex(html, r"<(b|strong)[^>]*>굵게할글자|font-weight: ?(bold|700)[^>]*>[^<]*굵게할글자")
        self.assertEqual(csp, [], "CSP 위반 없음")
        self.assertEqual(self.errors, [])

    def frame_h(self, pg):
        return pg.evaluate("() => document.getElementById('edframe').getBoundingClientRect().height")

    def submit_visible(self, pg, h):
        box = pg.locator("button[name=go][value=submit]").bounding_box()
        return pg.locator("button[name=go][value=submit]").is_visible() and box and box["y"] + box["height"] <= h + 1

    def test_5_editor_fills_screen(self):  # 편집기 크기: 기본 = 화면 높이에 맞춤, 크게 보기 = 화면 가득(작은 화면은 기본값)
        self.sizes = {}
        for w, h in ((1920, 1080), (1366, 768), (1024, 600), (800, 480)):
            with self.subTest(size=f"{w}x{h}"):
                pg = self.login("dev1", (w, h))
                pg.goto(self.base + "/new?form=general")
                self.wait_editor(pg)
                small = w <= 1024 or h <= 700
                big = pg.evaluate("() => document.body.classList.contains('edbig')")
                self.assertEqual(big, small, "작은 화면만 크게 보기로 시작")
                if not small:
                    fh = self.frame_h(pg)
                    self.sizes[(w, h, "default")] = fh / h
                    self.assertGreaterEqual(fh, 0.6 * h, f"기본 편집기 높이 {fh}/{h}")
                    self.assertTrue(self.submit_visible(pg, h), "상신 단추 보임(기본)")
                    pg.click("button.edbig-t")
                    pg.wait_for_function("() => document.body.classList.contains('edbig')")
                pg.wait_for_timeout(200)
                fh = self.frame_h(pg)
                self.sizes[(w, h, "big")] = fh / h
                self.assertGreaterEqual(fh, 0.8 * h, f"크게 보기 편집기 높이 {fh}/{h}")
                self.assertTrue(self.submit_visible(pg, h), "상신 단추 보임(크게 보기)")
                # A4 쪽은 편집기 안에서 가운데(쪽보다 넓을 때)
                cx = pg.frame("edframe").evaluate("() => { const r = document.getElementById('page').getBoundingClientRect();"
                                                  " return [r.left + r.width / 2, innerWidth, r.width]; }")
                if cx[2] < cx[1]:
                    self.assertLess(abs(cx[0] - cx[1] / 2), 40, f"쪽 가운데 {cx}")
                self.assertFalse(pg.locator("header.top").is_visible())
                # 결재 정보 덮개: 제목·결재선이 그 안에서 보이고 고칠 수 있음
                self.assertFalse(pg.locator("#ti").is_visible())
                pg.click("button.edinfo-t")
                pg.fill("#ti", f"크게 보기 {w}x{h}")
                pg.fill("#ln", "devlead")
                pg.keyboard.press("Escape")
                self.assertFalse(pg.locator("#edinfo").is_visible(), "Esc 로 덮개 닫힘")
                # 기억: 다시 열어도 크게 보기(작은 화면이 아니면 기본은 꺼짐이므로 기억 확인이 됨)
                pg.reload()
                self.wait_editor(pg)
                self.assertTrue(pg.evaluate("() => document.body.classList.contains('edbig')"), "크게 보기 기억")
                # 전체 화면 → Esc 로 돌아감
                if pg.evaluate("() => document.fullscreenEnabled"):
                    pg.click("button.edfull-t")
                    pg.wait_for_function("() => !!document.fullscreenElement")
                    pg.keyboard.press("Escape")
                    pg.wait_for_function("() => !document.fullscreenElement")
                # 끄면 원래 화면, 다시 열어도 꺼짐
                pg.click("button.edbig-t")
                self.assertTrue(pg.locator("header.top").is_visible())
                self.assertTrue(pg.locator("#ti").is_visible(), "제목 칸 제자리")
                pg.reload()
                self.wait_editor(pg)
                self.assertFalse(pg.evaluate("() => document.body.classList.contains('edbig')"), "끈 것도 기억")
                self.assertEqual(self.errors, [])
        # 크게 보기에서 제목을 비운 채 상신 → 결재 정보가 열려 제목 칸을 보여 줌, 채우면 상신
        pg = self.login("dev1", (800, 480))
        pg.goto(self.base + "/new?form=general")
        fr, frame = self.wait_editor(pg)
        self.type_text(fr, "작은 화면 상신")
        pg.click("button[name=go][value=submit]")
        pg.wait_for_function("() => !document.getElementById('edinfo').hidden")
        pg.fill("#ti", "작은 화면 상신")
        pg.fill("#ln", "devlead")
        with pg.expect_navigation(url=re.compile(r"/doc/\d+")):
            pg.click("button[name=go][value=submit]")
        did = int(re.search(r"/doc/(\d+)", pg.url).group(1))
        m = App(str(self.data)).show("dev1", did)
        self.assertEqual((m["title"], m["status"]), ("작은 화면 상신", "pending"))
        self.assertIn("작은 화면 상신", m["doc_html"])
        print("\n편집기 높이/화면:", {f"{w}x{h} {k}": round(v, 2) for (w, h, k), v in self.sizes.items()},
              file=sys.stderr)
        self.assertEqual(self.errors, [])

    def page_text(self, frame):
        return frame.locator("#page").inner_text()

    def test_6_autosave_navigate_restore(self):  # 작성 중 다른 메뉴로 갔다 와도 남아 있음 + 서버 저장 실패 시 이 브라우저 사본
        pg = self.login("dev1")
        fr, frame = self.open_editor(pg, "자동 저장 E2E")
        self.type_text(fr, "자동저장 첫 글")
        # 5초 쉬면 자동 저장 → 주소가 /doc/N/edit
        pg.wait_for_function("() => /자동 저장됨/.test(document.querySelector('.edstat').textContent)", timeout=20000)
        self.assertRegex(pg.url, r"/doc/\d+/edit$")
        did = int(re.search(r"/doc/(\d+)/edit", pg.url).group(1))
        # 바로 이어 쓰고(5초 전) 다른 메뉴로 → 먼저 저장하고 이동
        self.type_text(fr, "떠나기 직전 글")
        with pg.expect_navigation():
            pg.click("nav.side a[href='/']")
        app = App(str(self.data))
        self.assertIn("떠나기 직전 글", app.show("dev1", did)["doc_html"])
        # 새 기안 → 이어 쓰기 안내 → 그대로 열림
        pg.goto(self.base + "/new")
        link = pg.locator(f"a[href='/doc/{did}/edit']", has_text="이어서 쓰기")
        self.assertEqual(link.count(), 1)
        link.click()
        fr, frame = self.wait_editor(pg)
        self.assertIn("떠나기 직전 글", self.page_text(frame))
        self.assertEqual(pg.input_value("#ti"), "자동 저장 E2E")
        # 다른 사용자에게는 안내도 문서도 없음
        other = self.login("devlead")
        other.goto(self.base + "/new")
        self.assertEqual(other.locator(f"a[href='/doc/{did}/edit']").count(), 0)
        # 서버 저장 실패(네트워크 끊김 흉내) → '저장 실패' + 이 브라우저 사본, 나갈 때 확인
        pg.route("**/doc/*/edit", lambda r: r.abort() if r.request.method == "POST" else r.continue_())
        self.type_text(fr, "오프라인 글")
        pg.wait_for_function("() => /저장 실패/.test(document.querySelector('.edstat').textContent)", timeout=20000)
        self.assertTrue(pg.locator(".edstat button").is_visible(), "다시 시도 단추")
        self.assertNotIn("오프라인 글", app.show("dev1", did)["doc_html"])
        msgs = []
        pg.once("dialog", lambda d: (msgs.append(d.message), d.accept()))
        with pg.expect_navigation():
            pg.click("nav.side a[href='/']")
        self.assertTrue(msgs and "저장하지 못했습니다" in msgs[0], msgs)
        pg.unroute("**/doc/*/edit")
        # 다시 열면 되살리기 제안 → 되살리면 편집기에 돌아오고 서버에도 저장됨
        pg.goto(f"{self.base}/doc/{did}/edit")
        fr, frame = self.wait_editor(pg)
        self.assertNotIn("오프라인 글", self.page_text(frame))
        pg.wait_for_selector(".edrestore")
        self.assertIn("저장 안 된 작성분이 있어요", pg.locator(".edrestore").inner_text())
        pg.click(".edrestore button:has-text('되살리기')")
        frame.wait_for_function("() => /오프라인 글/.test(document.getElementById('page').innerText)")
        pg.wait_for_function("() => /자동 저장됨/.test(document.querySelector('.edstat').textContent)", timeout=20000)
        self.assertIn("오프라인 글", app.show("dev1", did)["doc_html"])
        # 상신 → 이 브라우저 사본 없음, 이어 쓰기 안내 없음
        with pg.expect_navigation(url=re.compile(r"/doc/\d+")):
            pg.click("button[name=go][value=submit]")
        self.assertEqual(app.show("dev1", did)["status"], "pending")
        left = pg.evaluate("() => Object.keys(localStorage).filter(k => k.startsWith('gyeoljae.draft.'))")
        self.assertEqual(left, [], "상신하면 이 브라우저 사본 지움")
        pg.goto(self.base + "/new")
        self.assertEqual(pg.locator(f"a[href='/doc/{did}/edit']").count(), 0)
        self.assertEqual(self.errors, [])

    def test_7_new_draft_offline_restore_and_logout(self):  # 첫 저장부터 실패한 새 기안: 같은 양식 새 기안에서 되살리기, 로그아웃은 사본 지움
        pg = self.login("dev1")
        pg.route("**/new", lambda r: r.abort() if r.request.method == "POST" else r.continue_())
        fr, frame = self.open_editor(pg, "오프라인 새 기안")
        self.type_text(fr, "처음부터 오프라인")
        pg.wait_for_function("() => /저장 실패/.test(document.querySelector('.edstat').textContent)", timeout=20000)
        pg.once("dialog", lambda d: d.accept())
        with pg.expect_navigation():
            pg.click("nav.side a[href='/']")
        pg.unroute("**/new")
        pg.goto(self.base + "/new?form=general")
        fr, frame = self.wait_editor(pg)
        pg.wait_for_selector(".edrestore")
        pg.click(".edrestore button:has-text('되살리기')")
        frame.wait_for_function("() => /처음부터 오프라인/.test(document.getElementById('page').innerText)")
        self.assertEqual(pg.input_value("#ti"), "오프라인 새 기안")
        pg.wait_for_function("() => /자동 저장됨/.test(document.querySelector('.edstat').textContent)", timeout=20000)
        did = int(re.search(r"/doc/(\d+)/edit", pg.url).group(1))
        self.assertIn("처음부터 오프라인", App(str(self.data)).show("dev1", did)["doc_html"])
        # 버리기를 고르지 않은 사본은 남고, 로그아웃하면 지워짐(Clear-Site-Data)
        pg.evaluate("() => localStorage.setItem('gyeoljae.draft.dev1.999', JSON.stringify({u:'dev1',at:Date.now(),html:'x'}))")
        with pg.expect_navigation():
            pg.click("form[action='/logout'] button")
        self.assertIn("/login", pg.url)
        left = pg.evaluate("() => Object.keys(localStorage).filter(k => k.startsWith('gyeoljae.draft.'))")
        self.assertEqual(left, [], "로그아웃하면 사본 지움")
        self.assertEqual(self.errors, [])

    def test_8_submit_right_after_typing_one_doc(self):  # 자동 저장 대기 중 바로 상신 → 문서 1건, 늦은 자동 저장 없음
        pg = self.login("dev1")
        fr, frame = self.open_editor(pg, "바로 상신 E2E")
        self.type_text(fr, "바로 상신")
        with pg.expect_navigation(url=re.compile(r"/doc/\d+")):
            pg.click("button[name=go][value=submit]")
        pg.wait_for_timeout(7000)
        app = App(str(self.data))
        rows = [r for b in ("drafts", "progress") for r in app.list_box("dev1", b)
                if r["title"] in ("바로 상신 E2E", app.AUTO_TITLE if hasattr(app, "AUTO_TITLE") else "(제목 없음 · 자동 저장)")]
        self.assertEqual([r["status"] for r in rows], ["pending"], rows)
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
