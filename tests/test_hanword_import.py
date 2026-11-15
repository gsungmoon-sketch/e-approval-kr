"""hanword 변환기 import 가 반입 폴더·캐시에 심은 파일(json.py, hwpx.py, 가짜 .pyc)에 끌려가지 않는지."""
import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

from helpers import rmtree  # noqa: F401  (sys.path 설정)

from gyeoljae import hanword


class HanwordImportTest(unittest.TestCase):
    def test_vendor_dir_appended_not_prepended(self):
        mod = hanword.embed()
        d = str(hanword.vendor_dir())
        self.assertIn(d, sys.path)
        self.assertNotEqual(sys.path[0], d)
        stdlib_json = sys.modules["json"].__file__ or ""
        self.assertFalse(stdlib_json.startswith(d))
        self.assertIsInstance(mod.__spec__.loader, hanword._VendorFinder)
        self.assertIsInstance(__import__("importlib").import_module("hwpxconv").__spec__.loader, hanword._VendorFinder)

    def test_vendor_dir_never_used_for_path_imports(self):
        """검사 뒤에 반입 폴더에 심은 모듈(선택적 'from hwpx import'·'from lxml import' 같은 try-import 대상)도
        경로 기반 import 로는 절대 적재되지 않는다(TOCTOU·Windows 공통)."""
        import importlib
        hanword.embed()
        d = hanword.vendor_dir()
        self.assertIsInstance(sys.path_importer_cache.get(str(d)), hanword._NoPathEntry)
        name = "gyeoljae_planted_probe_" + os.urandom(4).hex()
        if not os.access(d, os.W_OK):
            self.skipTest("반입 폴더 쓰기 불가")
        fp = d / (name + ".py")
        try:
            fp.write_text("raise SystemExit('planted')\n", encoding="utf-8")
            importlib.invalidate_caches()  # 캐시 비워도 _NoPathEntry 는 남는다
            with self.assertRaises(ModuleNotFoundError):
                importlib.import_module(name)
            self.assertIsInstance(sys.path_importer_cache.get(str(d)), hanword._NoPathEntry)
        finally:
            fp.unlink()
            sys.modules.pop(name, None)

    def test_finder_refuses_tampered_source_and_ignores_pyc(self):
        d = Path(tempfile.mkdtemp(prefix="gyeol-vf-"))
        try:
            good = b"X = 1\n"
            (d / "vmod_ok.py").write_bytes(good)
            (d / "vmod_bad.py").write_bytes(b"X = 2\n")
            f = hanword._VendorFinder(d, {"vmod_ok": hashlib.sha256(good).hexdigest(),
                                          "vmod_bad": hashlib.sha256(good).hexdigest()})
            import importlib.util
            spec = f.find_spec("vmod_ok")
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            self.assertEqual(m.X, 1)
            spec = f.find_spec("vmod_bad")
            with self.assertRaises(ImportError):
                spec.loader.exec_module(importlib.util.module_from_spec(spec))
            self.assertIsNone(f.find_spec("json"))  # 목록 밖 이름은 맡지 않음
        finally:
            rmtree(str(d))

    def test_cache_dir_rejects_planted_files(self):
        base = Path(tempfile.mkdtemp(prefix="gyeol-cc-"))
        try:
            d = base / "hanword-x"
            d.mkdir(mode=0o700)
            (d / "a.py").write_text("", encoding="utf-8")
            man = {"a.py": "0" * 64}
            hanword._check_cache_dir(d, man)  # 정상
            (d / "hwpx.py").write_text("raise SystemExit", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                hanword._check_cache_dir(d, man)
            os.remove(d / "hwpx.py")
            if hasattr(os, "getuid"):
                os.chmod(d, 0o777)
                with self.assertRaises(RuntimeError):
                    hanword._check_cache_dir(d, man)
                os.chmod(d, 0o700)
        finally:
            rmtree(str(base))

    def test_skeleton_served_from_verified_memory(self):
        """데이터 파일(hwpx_skeleton.hwpx)은 적재 때 VENDOR.md 해시와 대조한 바이트로 메모리에 고정 — 변환 때 디스크를 다시 읽지 않음."""
        import zipfile
        hanword.embed()
        import hwpx_std
        self.assertIsInstance(hwpx_std.SKELETON, hanword._VerifiedBlob)
        want = (hanword.VENDOR / "hwpx_skeleton.hwpx").read_bytes()
        man = hanword._trusted_manifest()
        self.assertEqual(hashlib.sha256(want).hexdigest(), man["hwpx_skeleton.hwpx"])
        with zipfile.ZipFile(hwpx_std.SKELETON) as z:
            self.assertIn("Contents/header.xml", z.namelist())
        self.assertTrue(hanword.export("<p>가</p>", "hwpx").startswith(b"PK"))

    def test_pin_data_files_checks_hash_and_ignores_later_tamper(self):
        import types
        d = Path(tempfile.mkdtemp(prefix="gyeol-df-"))
        name = "gyeol_fake_data_mod_" + os.urandom(4).hex()
        old = hanword._DATA_FILES
        try:
            good = b"good-bytes"
            fp = d / "x.bin"
            fp.write_bytes(b"evil")
            mod = types.ModuleType(name)
            mod.PATH = str(fp)
            sys.modules[name] = mod
            hanword._DATA_FILES = {(name, "PATH"): "x.bin"}
            man = {"x.bin": hashlib.sha256(good).hexdigest()}
            with self.assertRaises(RuntimeError):
                hanword._pin_data_files(d, man)
            self.assertEqual(mod.PATH, str(fp))  # 실패하면 바꾸지 않음
            fp.write_bytes(good)
            hanword._pin_data_files(d, man)
            fp.write_bytes(b"evil")  # 검사 뒤 바꿔치기 → 소용없음
            self.assertEqual(mod.PATH.read(), good)
            mod.PATH = str(d / "other.bin")  # 예상 밖 경로는 거절(반입본 갱신 감지)
            with self.assertRaises(RuntimeError):
                hanword._pin_data_files(d, man)
        finally:
            hanword._DATA_FILES = old
            sys.modules.pop(name, None)
            rmtree(str(d))

    def test_verified_blob_threads(self):
        import io
        import threading
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for i in range(20):
                z.writestr(f"f{i}", os.urandom(4096))
        blob = hanword._VerifiedBlob(buf.getvalue(), "t.zip")
        ref = {}
        with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as z:
            ref = {n: z.read(n) for n in z.namelist()}
        errs, start = [], threading.Barrier(8)

        def work():
            try:
                start.wait()
                for _ in range(30):
                    with zipfile.ZipFile(blob) as z:
                        if {n: z.read(n) for n in z.namelist()} != ref:
                            errs.append("mismatch")
            except Exception as e:  # pragma: no cover
                errs.append(repr(e))
        ts = [threading.Thread(target=work) for _ in range(8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(errs, [])

    def test_concurrent_first_embed_single_finder(self):
        """동시 첫 호출: 초기화 한 번, meta path 의 _VendorFinder 는 늘 하나."""
        import threading
        hanword.embed()
        saved = hanword._embed_mod
        try:
            hanword._embed_mod = None
            sys.meta_path.append(hanword._VendorFinder(Path("/nonexistent-old"), {}))  # 옛 폴더용 finder 가 남아 있어도
            start, got = threading.Barrier(8), []

            def work():
                start.wait()
                got.append(hanword.embed())
            ts = [threading.Thread(target=work) for _ in range(8)]
            [t.start() for t in ts]
            [t.join() for t in ts]
            self.assertEqual(len(got), 8)
            self.assertTrue(all(m is got[0] for m in got))
            fs = [f for f in sys.meta_path if isinstance(f, hanword._VendorFinder)]
            self.assertEqual(len(fs), 1)
            self.assertEqual(fs[0].dir, hanword.vendor_dir())
        finally:
            if hanword._embed_mod is None:
                hanword._embed_mod = saved

    def test_set_cache_dir_refuses_change_after_load(self):
        """.pyz 에서 변환기를 적재한 뒤 다른 캐시 폴더로 바꾸면 거절(finder·모듈은 첫 폴더에 묶여 있음)."""
        a = Path(tempfile.mkdtemp(prefix="gyeol-cd-"))
        b = Path(tempfile.mkdtemp(prefix="gyeol-cd-"))
        old_base, old_loaded = hanword._cache_base, hanword._loaded_base
        try:
            hanword._loaded_base = a
            hanword.set_cache_dir(a)            # 같은 폴더는 됨
            hanword.set_cache_dir(str(a) + os.sep)
            with self.assertRaises(RuntimeError):
                hanword.set_cache_dir(b)
            self.assertEqual(hanword._cache_base, Path(str(a) + os.sep))
            hanword._loaded_base = None         # 적재 전(또는 폴더 설치)에는 자유롭게
            hanword.set_cache_dir(b)
            self.assertEqual(hanword._cache_base, b)
        finally:
            hanword._cache_base, hanword._loaded_base = old_base, old_loaded
            rmtree(str(a))
            rmtree(str(b))

    def test_export_only_hwpx_docx(self):
        """html_to_pdf(검증 없이 캐시 경로를 읽음)는 export 로 닿지 않는다 — _check_cache_dir 문서의 전제."""
        for fmt in ("pdf", "html", ""):
            with self.assertRaises(ValueError):
                hanword.export("<p>가</p>", fmt)

    def test_pyz_load_pins_cache_base(self):
        """.pyz 경로(on_disk=False)로 실제 적재하면 _loaded_base 가 그 캐시로 고정되고 다른 폴더 지정은 거절."""
        from unittest import mock
        from gyeoljae import pkgfiles
        hanword.embed()
        man = hanword._trusted_manifest()
        names = [k[:-3] for k in man if "/" not in k and k.endswith(".py")]
        base = Path(tempfile.mkdtemp(prefix="gyeol-pz-"))
        other = Path(tempfile.mkdtemp(prefix="gyeol-pz-"))
        saved = {n: sys.modules[n] for n in names if n in sys.modules}
        st = (hanword._embed_mod, hanword._cache_base, hanword._loaded_base)
        old_meta, old_path = list(sys.meta_path), list(sys.path)
        try:
            for n in names:
                sys.modules.pop(n, None)
            hanword._embed_mod, hanword._cache_base, hanword._loaded_base = None, base, None
            with mock.patch.object(pkgfiles, "on_disk", return_value=False):
                m = hanword.embed()
                self.assertEqual(Path(m.__spec__.origin).parent.parent, base)
                self.assertEqual(hanword._loaded_base, base)
                with self.assertRaises(RuntimeError):
                    hanword.set_cache_dir(other)
                self.assertTrue(hanword.export("<p>가</p>", "hwpx").startswith(b"PK"))
        finally:
            for n in names:
                sys.modules.pop(n, None)
            sys.modules.update(saved)
            sys.meta_path[:] = old_meta
            sys.path[:] = old_path
            hanword._embed_mod, hanword._cache_base, hanword._loaded_base = st
            rmtree(str(base))
            rmtree(str(other))

    @unittest.skipUnless(hasattr(os, "getuid"), "POSIX 권한")
    def test_extracted_cache_is_private(self):
        """umask 002 에서도 새로 푼 캐시는 나만 쓸 수 있게(폴더 0700, 파일 0600)."""
        d = Path(tempfile.mkdtemp(prefix="gyeol-um-"))
        old_base, old_mask = hanword._cache_base, os.umask(0o002)
        try:
            hanword._cache_base = d / "cache"
            dest = hanword._extract_vendor()
            for p in (d / "cache", dest):
                self.assertEqual(p.stat().st_mode & 0o077, 0, p)
            for p in dest.rglob("*"):
                if p.is_file():
                    self.assertEqual(p.stat().st_mode & 0o077, 0, p)
        finally:
            os.umask(old_mask)
            hanword._cache_base = old_base
            rmtree(str(d))


if __name__ == "__main__":
    unittest.main()
