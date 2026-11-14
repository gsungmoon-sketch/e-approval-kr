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


if __name__ == "__main__":
    unittest.main()
