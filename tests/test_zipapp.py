"""단일 파일 .pyz(scripts/build_zipapp.py)가 zip 안에서 편집기·정적 파일·HWPX 변환까지 되는지(실제로 빌드해 띄움).
+ .pyz 용 반입본 캐시(hanword._extract_vendor)가 VENDOR.md 해시로 검증·복구되는지."""
import hashlib
import io
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath

from helpers import rmtree  # noqa: F401  (sys.path 설정)

from gyeoljae import hanword, pkgfiles

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class PkgFilesTest(unittest.TestCase):
    def test_safe_names(self):
        for ok in ("embed.html", "font/D2Coding-400.woff2", "a/b/c.js"):
            self.assertTrue(pkgfiles.safe_name(ok), ok)
        for bad in ("../web.py", "font/../../web.py", "..\\web.py", "font\\x.woff2", "C:/x.js", "c:x.js", "/etc/x.js",
                    ".x.js", "a//b.js", "a/./b.js", "", "LICENSE", "x.JS", None):
            self.assertFalse(pkgfiles.safe_name(bad), bad)

    def test_find_on_disk(self):
        self.assertIsNotNone(pkgfiles.find("static", "editor-host.js"))
        self.assertIsNotNone(pkgfiles.find("vendor/hanword", "font/D2Coding-400.woff2"))
        self.assertIsNone(pkgfiles.find("vendor/hanword", "nope.js"))
        self.assertIsNone(pkgfiles.find("vendor/hanword", "../../web.py"))
        self.assertIsNone(pkgfiles.find("static", "../web.py"))

    def test_manifest_keys_posix(self):
        man = hanword.vendor_manifest((hanword.VENDOR / "VENDOR.md").read_text(encoding="utf-8"))
        self.assertIn("font/D2Coding-400.woff2", man)
        # Windows 경로로 만든 키도 같은 '/' 키
        self.assertEqual(hanword.vendor_key(PureWindowsPath(r"C:\x\hanword\font\D2Coding-400.woff2"),
                                            PureWindowsPath(r"C:\x\hanword")), "font/D2Coding-400.woff2")
        for k in man:
            self.assertEqual(PurePosixPath(k).as_posix(), k)
        for bad in ("../x", "a\\b", "C:x", "/abs"):
            with self.assertRaises(ValueError):
                hanword.vendor_manifest(f"## Files (sha256)\n\n{'0' * 64}  {bad}\n")

    def test_extract_cache_verified_and_repaired(self):
        d = tempfile.mkdtemp(prefix="gyeol-cache-")
        old = hanword._cache_base
        try:
            hanword.set_cache_dir(d)
            dest = hanword._extract_vendor()
            self.assertTrue((dest / "hwpx_skeleton.hwpx").is_file())
            self.assertFalse((dest / "font").exists())  # 글꼴은 풀지 않음
            want = (hanword.VENDOR / "hwpx_std.py").read_bytes()
            self.assertEqual((dest / "hwpx_std.py").read_bytes(), want)
            (dest / "hwpx_std.py").write_bytes(b"tampered")
            self.assertEqual(hanword._extract_vendor(), dest)
            self.assertEqual(hashlib.sha256((dest / "hwpx_std.py").read_bytes()).hexdigest(),
                             hashlib.sha256(want).hexdigest())
            self.assertEqual([p.name for p in Path(d).iterdir() if p.name.startswith(".tmp-")], [])
        finally:
            hanword._cache_base = old
            rmtree(d)


class ZipappSmokeTest(unittest.TestCase):
    """빌드 → seed-demo → 편집기 문서 기안(CLI) → serve → 로그인 → /editor·글꼴·정적 JS·경로이동 404·HWPX."""

    def test_pyz_serves_editor_and_exports(self):
        t0 = time.time()
        tmp = Path(tempfile.mkdtemp(prefix="gyeol-pyz-"))
        srv = None
        try:
            subprocess.run([sys.executable, str(ROOT / "scripts" / "build_zipapp.py"), "--out", str(tmp / "dist")],
                           check=True, capture_output=True, timeout=120)
            pyz = next((tmp / "dist").glob("*.pyz"))
            env = dict(os.environ, GYEOLJAE_DATA=str(tmp / "data"), PYTHONIOENCODING="utf-8")
            env.pop("PYTHONPATH", None)
            run = [sys.executable, "-s", "-I", str(pyz)]
            subprocess.run(run + ["seed-demo"], check=True, capture_output=True, env=env, timeout=60)
            html = tmp / "d.html"
            html.write_text("<p>zip <b>안녕</b></p><table><tr><td>칸</td></tr></table>", encoding="utf-8")
            r = subprocess.run(run + ["-u", "dev1", "--json", "draft", "--form", "general", "--title", "pyz",
                                      "--line", "devlead", "--doc-html", str(html)],
                               capture_output=True, env=env, timeout=60)
            self.assertEqual(r.returncode, 0, r.stdout.decode("utf-8", "replace") + r.stderr.decode("utf-8", "replace"))
            import json
            did = json.loads(r.stdout.decode("utf-8"))["id"]
            caches = list((tmp / "data" / "cache").glob("hanword-*"))
            self.assertEqual(len(caches), 1)
            self.assertTrue((caches[0] / "hwpx_skeleton.hwpx").is_file())
            port = free_port()
            srv = subprocess.Popen(run + ["serve", "--port", str(port)], env=env,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            base = f"http://127.0.0.1:{port}"
            for _ in range(100):
                try:
                    urllib.request.urlopen(base + "/healthz", timeout=1).read()
                    break
                except OSError:
                    time.sleep(0.1)
            from test_web import Client
            c = Client(base)
            st, _, h = c.login("dev1", "demo1234!")
            self.assertEqual(st, 303)

            def get(path):
                try:
                    resp = c.op.open(urllib.request.Request(base + path))
                    return resp.status, resp.read(), resp.headers
                except urllib.error.HTTPError as e:
                    return e.code, e.read(), e.headers
            v = ROOT / "src" / "gyeoljae"
            st, data, h = get("/editor/embed.html")
            self.assertEqual((st, data), (200, (v / "vendor" / "hanword" / "embed.html").read_bytes()))
            self.assertIn("script-src 'self'", h["Content-Security-Policy"])
            st, data, h = get("/editor/font/D2Coding-400.woff2")
            self.assertEqual((st, data), (200, (v / "vendor" / "hanword" / "font" / "D2Coding-400.woff2").read_bytes()))
            self.assertEqual(h["Content-Type"], "font/woff2")
            st, data, _ = get("/static/editor-host.js")
            self.assertEqual((st, data), (200, (v / "static" / "editor-host.js").read_bytes()))
            for bad in ("/editor/../web.py", "/editor/font/../../../web.py", "/editor/%2e%2e/web.py",
                        "/editor/..%5cweb.py", "/editor/hanword_embed.py", "/editor/VENDOR.md", "/editor/nope.js"):
                self.assertEqual(get(bad)[0], 404, bad)
            st, data, h = get(f"/doc/{did}/export.hwpx")
            self.assertEqual((st, h["Content-Type"]), (200, "application/hwp+zip"))
            self.assertEqual(zipfile.ZipFile(io.BytesIO(data)).read("mimetype"), b"application/hwp+zip")
            st, data, _ = get(f"/doc/{did}/export.docx")
            self.assertEqual(st, 200)
            self.assertIn("안녕", zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml").decode("utf-8"))
        finally:
            if srv:
                srv.terminate()
                try:
                    srv.wait(10)
                except subprocess.TimeoutExpired:
                    srv.kill()
            rmtree(tmp)
        self.assertLess(time.time() - t0, 60)


if __name__ == "__main__":
    unittest.main()
