"""OnlyOffice 실서버 종단간 시험(opt-in, 기본 건너뜀 — CI 무관). docs/ONLYOFFICE.md '실서버 시험'.

필요: 실행 중인 OnlyOffice Document Server(JWT 켬) + Python Playwright.
  GYEOLJAE_OO_E2E=1
  GYEOLJAE_OO_DOCS_URL=http://127.0.0.1:8701          브라우저와 이 시험이 여는 문서 서버 주소
  GYEOLJAE_OO_JWT_SECRET_FILE=<파일>                   문서 서버 JWT_SECRET(첫 줄)  (또는 GYEOLJAE_OO_JWT_SECRET)
  GYEOLJAE_OO_APP_HOST=<문서 서버 컨테이너가 닿는 이 PC 주소>  결재 서버를 이 주소에 띄움(기본 127.0.0.1)
  GYEOLJAE_OO_APP_PORT=<포트>                          (기본 빈 포트)
흐름: dev1 이 자료실에 docx 올림 → 브라우저로 편집기 열기 → 글자 입력 → 저장(Ctrl+S, 안 되면 창 닫기)
      → 콜백으로 판 2 가 생기고 그 docx 안에 입력한 글자가 있는지 확인.
"""
import io
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
import zipfile
from pathlib import Path

from helpers import PW, rmtree, world
from test_browser_e2e import _launch, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
ON = os.environ.get("GYEOLJAE_OO_E2E") == "1" and bool(os.environ.get("GYEOLJAE_OO_DOCS_URL"))


def minimal_docx(text):
    """표준 라이브러리만으로 만든 최소 docx."""
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.'
                   'wordprocessingml.document.main+xml"/></Types>')
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
                   'officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr("word/document.xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
                   f'<w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>')
    return b.getvalue()


def docx_text(data):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return z.read("word/document.xml").decode("utf-8")


def _free_port(host):
    with socket.socket() as s:
        s.bind((host, 0))
        return s.getsockname()[1]


@unittest.skipUnless(ON, "opt-in: GYEOLJAE_OO_E2E=1 + GYEOLJAE_OO_DOCS_URL")
@unittest.skipUnless(sync_playwright, "Playwright 없음")
class OnlyOfficeLiveTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.docs = os.environ["GYEOLJAE_OO_DOCS_URL"].rstrip("/")
        sf = os.environ.get("GYEOLJAE_OO_JWT_SECRET_FILE")
        secret = Path(sf).read_text(encoding="utf-8").splitlines()[0].strip() if sf else \
            os.environ.get("GYEOLJAE_OO_JWT_SECRET", "")
        host = os.environ.get("GYEOLJAE_OO_APP_HOST", "127.0.0.1")
        port = int(os.environ.get("GYEOLJAE_OO_APP_PORT") or _free_port(host))
        cls.tmp = Path(tempfile.mkdtemp(prefix="gyeol-oo-"))
        cls.app, _ = world(str(cls.tmp / "data"))
        cls.app.oo_set("admin", enabled=True, docs_url=cls.docs, app_internal_url=f"http://{host}:{port}",
                       jwt_secret=secret)
        cls.fid = cls.app.files_upload("dev1", "live.docx", minimal_docx("gyeoljae original"))["id"]
        cls.base = f"http://{host}:{port}"
        env = dict(os.environ, GYEOLJAE_DATA=str(cls.tmp / "data"), PYTHONPATH=str(ROOT / "src"),
                   PYTHONIOENCODING="utf-8")
        cls.log = open(cls.tmp / "server.log", "wb")
        cls.srv = subprocess.Popen([sys.executable, "-m", "gyeoljae", "serve", "--host", host, "--port", str(port)],
                                   env=env, stdout=cls.log, stderr=subprocess.STDOUT)
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
            cls.srv.wait(10)
            cls.log.close()
            if os.environ.get("GYEOLJAE_OO_KEEP") != "1":
                rmtree(str(cls.tmp))

    def test_edit_docx_creates_version(self):
        if not self.browser:
            self.skipTest("Chromium 없음")
        marker = "OOE2E" + secrets.token_hex(4).upper()
        ctx = self.browser.new_context(viewport={"width": 1400, "height": 1000})
        pg = ctx.new_page()
        pg.set_default_timeout(60000)
        csp = []
        pg.on("console", lambda m: csp.append(m.text) if "Content Security Policy" in m.text else None)
        pg.goto(self.base + "/login")
        pg.fill("input[name=username]", "dev1")
        pg.fill("input[name=password]", PW)
        pg.click("form[action='/login'] button")
        pg.wait_for_load_state()
        pg.goto(f"{self.base}/files/{self.fid}")
        self.assertIn(f"/oo/edit/l/{self.fid}?mode=edit", pg.content())
        pg.goto(f"{self.base}/oo/edit/l/{self.fid}?mode=edit")
        pg.wait_for_selector("body[data-oo-ready='1']", timeout=120000)
        self.assertEqual(csp, [], "CSP 위반")
        frame = pg.frame_locator("iframe[name=frameEditor]")
        frame.locator("#editor_sdk").click(position={"x": 400, "y": 300})
        pg.keyboard.press("Control+End")
        pg.keyboard.type(" " + marker, delay=30)
        time.sleep(2)
        pg.keyboard.press("Control+s")  # forcesave → 상태 6
        got = self._wait_version(60)
        if got is None:  # 중간 저장이 안 되면 창 닫기 → 상태 2
            ctx.close()
            got = self._wait_version(120)
        else:
            ctx.close()
        self.assertIsNotNone(got, "새 판이 생기지 않음(서버 로그: %s)" % (self.tmp / "server.log"))
        self.assertIn(marker, docx_text(got))
        vs = self.app.files_versions("dev1", self.fid)["versions"]
        self.assertEqual(vs[0]["uploader"], "dev1")
        self.assertIn("OnlyOffice", vs[0]["note"])
        self.assertIn("oo_save", [x["action"] for x in self.app.audit_list("admin")])

    def _wait_version(self, sec):
        end = time.time() + sec
        while time.time() < end:
            f = self.app.files_versions("dev1", self.fid)
            if f["versions"][0]["version"] >= 2:
                return self.app.files_get("dev1", self.fid)[1].read_bytes()
            time.sleep(1)
        return None


if __name__ == "__main__":
    unittest.main()
