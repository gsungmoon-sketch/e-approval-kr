"""OnlyOffice 연동: JWT(HS256 만), 문서 키, 권한 표, 내려받기 토큰, 콜백 상태, 결과 주소 SSRF 방어·크기 상한, 웹 경로."""
import base64
import hashlib
import hmac
import json
import os
import re
import threading
import time
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from helpers import rmtree, world

from gyeoljae import core
from gyeoljae import onlyoffice as oo
from gyeoljae.web import make_server

SECRET = "s" * 16 + "e" * 16 + "cret"
DOCS = "http://docs.test:8701"


def b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def forge(header, payload, key=SECRET.encode(), alg=hashlib.sha256):
    h, p = b64(json.dumps(header).encode()), b64(json.dumps(payload).encode())
    return f"{h}.{p}." + b64(hmac.new(key, f"{h}.{p}".encode(), alg).digest())


class JWTTest(unittest.TestCase):
    def test_roundtrip_and_tamper(self):
        t = oo.jwt_sign({"a": 1, "한": "글"}, SECRET, ttl=60)
        self.assertEqual(oo.jwt_verify(t, SECRET)["한"], "글")
        with self.assertRaises(oo.JWTError):
            oo.jwt_verify(t, SECRET + "x")
        h, p, s = t.split(".")
        with self.assertRaises(oo.JWTError):
            oo.jwt_verify(f"{h}.{b64(json.dumps({'a': 2}).encode())}.{s}", SECRET)
        for bad in ("", "a.b", "a.b.c.d", t + "=", "x" * 70000, None, f"{h}.{p}."):
            with self.assertRaises(oo.JWTError):
                oo.jwt_verify(bad, SECRET)

    def test_alg_none_and_confusion(self):
        p = {"key": "k", "status": 2}
        none = b64(json.dumps({"alg": "none"}).encode()) + "." + b64(json.dumps(p).encode()) + "."
        for tok in (none, none + "x",
                    forge({"alg": "HS512"}, p, alg=hashlib.sha512),
                    forge({"alg": "RS256"}, p),        # 공개키를 HMAC 비밀로 쓰는 혼동
                    forge({"alg": "hs256"}, p),
                    forge({"alg": "HS256", "crit": ["x"]}, p),
                    forge({"typ": "JWT"}, p)):
            with self.assertRaises(oo.JWTError):
                oo.jwt_verify(tok, SECRET)
        self.assertEqual(oo.jwt_verify(forge({"alg": "HS256"}, p), SECRET)["status"], 2)

    def test_expiry(self):
        t = oo.jwt_sign({"a": 1}, SECRET, ttl=10, now=1000)
        self.assertEqual(oo.jwt_verify(t, SECRET, now=1005, leeway=0)["a"], 1)
        with self.assertRaises(oo.JWTError):
            oo.jwt_verify(t, SECRET, now=1011, leeway=0)
        with self.assertRaises(oo.JWTError):
            oo.jwt_verify(oo.jwt_sign({"a": 1}, SECRET), SECRET, require_exp=True)
        with self.assertRaises(oo.JWTError):
            oo.jwt_verify(forge({"alg": "HS256"}, {"exp": "9999999999"}), SECRET)
        with self.assertRaises(oo.JWTError):
            oo.jwt_verify(forge({"alg": "HS256"}, {"nbf": time.time() + 3600}), SECRET)
        with self.assertRaises(oo.JWTError):
            oo.jwt_sign({"a": 1}, "")

    def test_key(self):
        k = oo.make_key("L" * 40, "e", "l", 12, 3, "ab" * 32)
        self.assertEqual(oo.parse_key(k), ("e", "l", 12, 3, "ab" * 8))
        self.assertTrue(re.fullmatch(r"[0-9A-Za-z.=_-]{1,128}", k))
        self.assertNotEqual(k, oo.make_key("L" * 40, "v", "l", 12, 3, "ab" * 32))      # 보기 ≠ 편집 세션
        self.assertNotEqual(k, oo.make_key("M" * 40, "e", "l", 12, 3, "ab" * 32))      # 비밀 없이는 짐작 불가
        self.assertNotEqual(oo.make_key("L" * 40, "e", "d", 1, 1, "ab" * 32, "3"),
                            oo.make_key("L" * 40, "e", "d", 1, 1, "ab" * 32, "4"))      # rev(상신 등) 바뀌면 새 키
        for bad in ("", "gjx1x1x" + "a" * 16, k + "0", "gjel1x1x" + "G" * 16 + "x" + "a" * 16, None, 5):
            with self.assertRaises(core.NotFound):
                oo.parse_key(bad)


class _Srv(BaseHTTPRequestHandler):
    body = b"x"

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/redir":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:1/secret")
            self.end_headers()
            return
        self.send_response(200)
        if self.path != "/nolen":
            self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)


class FetchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), _Srv)
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_origin_only(self):
        ok = {oo._origin(self.base)}
        _Srv.body = b"hello"
        self.assertEqual(oo.fetch_limited(self.base + "/f", ok, 100), b"hello")
        for u in ("http://127.0.0.1:1/f", "file:///etc/passwd", f"http://u:p@{self.base[7:]}/f", "ftp://x/y",
                  self.base.replace("127.0.0.1", "localhost") + "/f", "https://" + self.base[7:] + "/f", "", "http://"):
            with self.assertRaises(core.Forbidden, msg=u):
                oo.fetch_limited(u, ok, 100)
        with self.assertRaises(core.Forbidden):
            oo.fetch_limited(self.base + "/f", {None}, 100)

    def test_redirect_refused(self):
        with self.assertRaises(OSError):
            oo.fetch_limited(self.base + "/redir", {oo._origin(self.base)}, 100)

    def test_size_cap(self):
        _Srv.body = b"y" * 1000
        for path in ("/f", "/nolen"):
            with self.assertRaises(core.Invalid):
                oo.fetch_limited(self.base + path, {oo._origin(self.base)}, 999)
        self.assertEqual(len(oo.fetch_limited(self.base + "/f", {oo._origin(self.base)}, 1000)), 1000)


DOCX = b"PK\x03\x04 fake docx v1"


class AppTest(unittest.TestCase):
    def setUp(self):
        self.app, self.d = world()
        a = self.app
        a.oo_set("admin", enabled=True, docs_url=DOCS, app_internal_url="http://gyeol.test:8130", jwt_secret=SECRET)
        self.doc = a.create_draft("dev1", "보고", "본문", ["devlead"], attachments=[("보고.docx", DOCX),
                                                                                  ("메모.txt", b"t")])["id"]
        self.lib = a.files_upload("dev1", "계획.xlsx", b"PK xlsx v1")["id"]
        self.org = a.files_upload("devlead", "규정.pptx", b"PK pptx v1", scope="org")["id"]
        self.fetched = {}
        a._oo_fetch = lambda url, allowed, cap: self._fake_fetch(url, allowed, cap)

    def tearDown(self):
        rmtree(self.d)

    def _fake_fetch(self, url, allowed, cap):
        self.fetched = {"url": url, "allowed": allowed, "cap": cap}
        return self.result

    def cfg(self, user, kind, ident, n=None, mode="edit"):
        return self.app.oo_editor_config(user, kind, ident, n, mode)["config"]

    def cb(self, cfg, status=2, users=None, extra=None, header=False, cb_token=None):
        key = cfg["document"]["key"]
        ft = os.path.splitext(cfg["document"]["title"])[1][1:]
        p = {"key": key, "status": status, "url": DOCS + "/cache/files/out.docx", "users": users or [], "filetype": ft,
             **(extra or {})}
        tok = cb_token or cfg["editorConfig"]["callbackUrl"].rsplit("/", 1)[1]
        if header:
            return self.app.oo_callback(tok, {"status": 2}, "Bearer " + oo.jwt_sign({"payload": p}, SECRET, ttl=60))
        return self.app.oo_callback(tok, {"token": oo.jwt_sign(p, SECRET, ttl=60)})

    # ---------------------------------------------------------------- settings
    def test_settings(self):
        a = self.app
        g = a.oo_get("admin")
        self.assertEqual(g["jwt_secret"], "********")
        self.assertNotIn("link_key", g)
        if os.name == "posix":
            self.assertEqual(os.stat(a._oo_path()).st_mode & 0o777, 0o600)
        with self.assertRaises(core.Forbidden):
            a.oo_set("dev1", enabled=False)
        for kw in ({"jwt_secret": "short"}, {"docs_url": "http://u:p@h"}, {"docs_url": "javascript:alert(1)"},
                   {"docs_url": "http://h/x?q=1"}, {"app_internal_url": "http://h'x"}, {"jwt_header": "A B"},
                   {"enabled": True, "app_internal_url": ""}):
            with self.assertRaises(core.Invalid, msg=kw):
                a.oo_set("admin", **kw)
        a.oo_set("admin", jwt_secret=oo.MASK)  # 가림표 = 유지
        self.assertEqual(a._oo_conf()["jwt_secret"], SECRET)
        self.assertIn("oo_set", [x["action"] for x in a.audit_list("admin")])
        self.assertNotIn(SECRET, json.dumps(a.audit_list("admin")))
        a.oo_set("admin", enabled=False)
        self.assertFalse(a.oo_enabled())
        self.assertEqual(a.oo_can("dev1", "l", self.lib), (False, False))
        with self.assertRaises(core.NotFound):
            self.cfg("dev1", "l", self.lib)

    # ---------------------------------------------------------------- permissions
    def test_permission_matrix_attachments(self):
        a, d = self.app, self.doc
        self.assertEqual(a.oo_can("dev1", "d", d, 1), (True, True))       # 기안자·기안중
        self.assertEqual(a.oo_can("devlead", "d", d, 1), (False, False))  # 상신 전 = 결재자도 못 봄
        with self.assertRaises(core.Invalid):
            a.oo_access("dev1", "d", d, 2)                                # txt 는 대상 아님
        a.submit("dev1", d)
        self.assertEqual(a.oo_can("dev1", "d", d, 1), (True, False))      # 진행 = 보기만
        self.assertEqual(a.oo_can("devlead", "d", d, 1), (True, False))
        self.assertEqual(a.oo_can("sal1", "d", d, 1), (False, False))
        with self.assertRaises(core.Forbidden):
            self.cfg("dev1", "d", d, 1)
        a.reject("devlead", d, "다시 작성하세요")
        self.assertEqual(a.oo_can("dev1", "d", d, 1), (True, True))       # 반려 = 다시 편집
        a.submit("dev1", d)
        a.approve("devlead", d)
        self.assertEqual(a.show("dev1", d)["status"], "done")
        self.assertEqual(a.oo_can("dev1", "d", d, 1), (True, False))      # 완료 = 보기만
        with self.assertRaises(core.Forbidden):
            a.attachment_replace("dev1", d, 1, b"new")

    def test_permission_matrix_library(self):
        a = self.app
        self.assertEqual(a.oo_can("dev1", "l", self.lib), (True, True))
        self.assertEqual(a.oo_can("devlead", "l", self.lib), (True, True))
        self.assertEqual(a.oo_can("sal1", "l", self.lib), (False, False))     # 다른 부서
        self.assertEqual(a.oo_can("dev1", "l", self.org), (True, False))      # 전사: 일반은 읽기만
        self.assertEqual(a.oo_can("sallead", "l", self.org), (True, True))
        a.files_new_version("dev1", self.lib, b"PK xlsx v2")
        self.assertEqual(a.oo_can("dev1", "l", self.lib, 1), (True, False))   # 옛 판 = 보기만
        self.assertEqual(a.oo_can("dev1", "l", self.lib, 2), (True, True))

    # ---------------------------------------------------------------- config / download
    def test_config_signed(self):
        a = self.app
        c = self.cfg("dev1", "l", self.lib)
        body = oo.jwt_verify(c["token"], SECRET, require_exp=True)
        self.assertEqual(body["document"], c["document"])
        self.assertEqual(body["editorConfig"]["callbackUrl"], c["editorConfig"]["callbackUrl"])
        self.assertEqual(c["document"]["key"], oo.make_key(a._oo_conf()["link_key"], "e", "l", self.lib, 1,
                                                           hashlib.sha256(b"PK xlsx v1").hexdigest()))
        self.assertTrue(c["document"]["url"].startswith("http://gyeol.test:8130/oo/dl/"))
        self.assertEqual(c["editorConfig"]["user"]["id"], "dev1")
        v = self.cfg("dev1", "l", self.org, mode="view")
        self.assertNotIn("callbackUrl", v["editorConfig"])
        self.assertTrue(v["document"]["key"].startswith("gjv"))
        self.assertFalse(v["document"]["permissions"]["edit"])
        with self.assertRaises(core.Forbidden):
            self.cfg("dev1", "l", self.org, mode="edit")
        a.files_new_version("dev1", self.lib, b"PK xlsx v2")
        self.assertNotEqual(self.cfg("dev1", "l", self.lib)["document"]["key"], c["document"]["key"])

    def test_download_token(self):
        a = self.app
        c = self.cfg("dev1", "l", self.lib)
        tok = c["document"]["url"].rsplit("/", 1)[1]
        self.assertEqual(a.oo_download(tok)[1], b"PK xlsx v1")
        a.files_new_version("dev1", self.lib, b"PK xlsx v2")
        self.assertEqual(a.oo_download(tok)[1], b"PK xlsx v1")   # 토큰은 그 판만
        link = a._oo_conf()["link_key"]
        for bad in (tok[:-2] + "AA", oo.jwt_sign({"t": "dl", "k": "l", "id": self.lib, "n": 1, "sha": "0", "u": "dev1"},
                                                  link, ttl=60),
                    oo.jwt_sign({"t": "cb", "k": "l", "id": self.lib, "n": 1, "u": "dev1"}, link, ttl=60),
                    oo.jwt_sign({"t": "dl", "k": "l", "id": self.lib, "n": 1, "u": "dev1"}, link),          # exp 없음
                    oo.jwt_sign({"t": "dl", "k": "l", "id": self.lib, "n": 1, "u": "dev1"}, SECRET, ttl=60),  # 다른 비밀
                    oo.jwt_sign({"t": "dl", "k": "l", "id": self.lib, "n": 1, "u": "dev1",
                                 "sha": hashlib.sha256(b"PK xlsx v1").hexdigest()}, link, ttl=-100)):
            with self.assertRaises(core.NotFound):
                a.oo_download(bad)
        # 열람 권한이 없어진 사용자의 토큰은 무효
        sal = oo.jwt_sign({"t": "dl", "k": "l", "id": self.lib, "n": 1, "u": "sal1",
                           "sha": hashlib.sha256(b"PK xlsx v1").hexdigest()}, link, ttl=60)
        with self.assertRaises(core.NotFound):
            a.oo_download(sal)
        a.oo_set("admin", enabled=False)
        with self.assertRaises(core.NotFound):
            a.oo_download(tok)

    def test_download_attachment_versions(self):
        a = self.app
        c = self.cfg("dev1", "d", self.doc, 1)
        tok = c["document"]["url"].rsplit("/", 1)[1]
        a.attachment_replace("dev1", self.doc, 1, b"PK new")
        self.assertEqual(a.oo_download(tok)[1], DOCX)  # 이전 판(prev)
        c2 = self.cfg("dev1", "d", self.doc, 1)
        self.assertEqual(a.oo_download(c2["document"]["url"].rsplit("/", 1)[1])[1], b"PK new")

    # ---------------------------------------------------------------- callback
    def test_callback_statuses_library(self):
        a = self.app
        c = self.cfg("dev1", "l", self.lib)
        for st in (1, 3, 4, 7):
            self.assertEqual(self.cb(c, st, ["dev1"]), {"error": 0, "saved": False, "status": st})
        self.assertEqual(a.files_versions("dev1", self.lib)["versions"][0]["version"], 1)
        self.result = b"PK\x03\x04 xlsx edited"
        r = self.cb(c, 6, ["devlead"])
        self.assertEqual((r["error"], r["saved"]), (0, True))
        self.assertEqual(self.fetched["cap"], 20 * 1024 * 1024)
        self.assertEqual(self.fetched["allowed"], {oo._origin(DOCS), None})
        vs = a.files_versions("dev1", self.lib)["versions"]
        self.assertEqual((vs[0]["version"], vs[0]["uploader"]), (2, "dev1"))   # 연 사람(우리 서명)이 우선
        self.assertEqual(a.files_get("dev1", self.lib)[1].read_bytes(), b"PK\x03\x04 xlsx edited")
        r = self.cb(c, 2, ["devlead"])        # 같은 내용 = 새 판 없음
        self.assertEqual((r["error"], r["saved"]), (0, False))
        self.assertEqual(len(a.files_versions("dev1", self.lib)["versions"]), 2)
        acts = [(x["actor"], x["action"]) for x in a.audit_list("admin")]
        self.assertIn(("dev1", "oo_save"), acts)
        self.assertIn(("dev1", "lib_upload"), acts)

    def test_callback_header_token_and_unsigned_body_ignored(self):
        a = self.app
        c = self.cfg("dev1", "l", self.lib)
        self.result = b"PK\x03\x04 via header"
        self.assertTrue(self.cb(c, 2, ["dev1"], header=True)["saved"])
        # 본문의 서명 밖 status=2 는 무시: 서명된 것은 4
        tok = c["editorConfig"]["callbackUrl"].rsplit("/", 1)[1]
        p = {"key": c["document"]["key"], "status": 4}
        r = a.oo_callback(tok, {"status": 2, "url": DOCS + "/x", "token": oo.jwt_sign(p, SECRET, ttl=60)})
        self.assertFalse(r["saved"])

    def test_callback_rejects(self):
        a = self.app
        c = self.cfg("dev1", "l", self.lib)
        tok = c["editorConfig"]["callbackUrl"].rsplit("/", 1)[1]
        key = c["document"]["key"]
        p = {"key": key, "status": 2, "url": DOCS + "/x", "users": ["dev1"]}
        with self.assertRaises(core.Forbidden):   # JWT 없음
            a.oo_callback(tok, dict(p))
        with self.assertRaises(core.Forbidden):   # 다른 비밀
            a.oo_callback(tok, {"token": oo.jwt_sign(p, "x" * 40, ttl=60)})
        with self.assertRaises(core.Forbidden):   # alg none
            a.oo_callback(tok, {"token": b64(b'{"alg":"none"}') + "." + b64(json.dumps(p).encode()) + "."})
        with self.assertRaises(core.Forbidden):   # 콜백 토큰 위조
            a.oo_callback(tok[:-3] + "AAA", {"token": oo.jwt_sign(p, SECRET, ttl=60)})
        other = self.cfg("devlead", "l", self.org)
        with self.assertRaises(core.Forbidden):   # 콜백 토큰의 키와 다른 문서 키
            a.oo_callback(other["editorConfig"]["callbackUrl"].rsplit("/", 1)[1],
                          {"token": oo.jwt_sign(p, SECRET, ttl=60)})
        with self.assertRaises(core.Forbidden):   # 문서 서버 비밀로 만든 콜백 토큰(우리 link_key 가 아님)
            a.oo_callback(oo.jwt_sign({"t": "cb", "key": key, "u": "dev1"}, SECRET, ttl=60),
                          {"token": oo.jwt_sign(p, SECRET, ttl=60)})
        self.result = b"PK\x03\x04"
        link = a._oo_conf()["link_key"]
        sal_cb = oo.jwt_sign({"t": "cb", "key": key, "u": "sal1"}, link, ttl=60)
        r = self.cb(c, 2, ["sal1"], cb_token=sal_cb)   # 편집 권한 없는 사용자 이름으로는 저장 안 함
        self.assertEqual((r["error"], r["saved"]), (1, False))
        self.assertIn("oo_save_refused", [x["action"] for x in a.audit_list("admin")])
        with self.assertRaises(core.Invalid):     # 형식이 바뀐 결과
            self.cb(c, 2, ["dev1"], extra={"filetype": "pdf"})
        with self.assertRaises(core.Invalid):     # 형식 없음
            self.cb(c, 2, ["dev1"], extra={"filetype": None})
        self.result = b"<html>not zip"
        with self.assertRaises(core.Invalid):     # zip 이 아닌 결과
            self.cb(c, 2, ["dev1"])
        v = self.cfg("dev1", "l", self.lib, mode="view")   # 보기 세션 키로는 저장 불가
        vcb = oo.jwt_sign({"t": "cb", "key": v["document"]["key"], "u": "dev1"}, link, ttl=60)
        with self.assertRaises(core.Forbidden):
            self.cb(v, 2, ["dev1"], cb_token=vcb)
        with self.assertRaises(core.Forbidden):   # 문서 서버 JWT 에 exp 없음
            a.oo_callback(tok, {"token": oo.jwt_sign(p, SECRET)})
        self.assertEqual(len(a.files_versions("dev1", self.lib)["versions"]), 1)

    def test_callback_stale_session(self):
        a = self.app
        c = self.cfg("dev1", "l", self.lib)
        a.files_new_version("devlead", self.lib, b"PK\x03\x04 someone else")   # 그 사이 다른 판
        self.result = b"PK\x03\x04 old session"
        self.assertEqual(self.cb(c, 2, ["dev1"])["error"], 1)
        self.assertEqual(a.files_get("dev1", self.lib)[1].read_bytes(), b"PK\x03\x04 someone else")
        c2 = self.cfg("dev1", "l", self.lib)                                     # 이어지는 저장(6 → 2)은 허용
        self.result = b"PK\x03\x04 s1"
        self.assertTrue(self.cb(c2, 6, ["dev1"])["saved"])
        self.result = b"PK\x03\x04 s2"
        self.assertTrue(self.cb(c2, 2, ["dev1"])["saved"])
        self.assertEqual(len(a.files_versions("dev1", self.lib)["versions"]), 4)

    def test_jwt_garbage_is_jwterror(self):
        for t in ("a.b.c", "a.b.cc" + "c" * 3, "eyJ.é.x", "a.b." + "A" * 5, "..."):
            with self.assertRaises(oo.JWTError):
                oo.jwt_verify(t, SECRET)
        for u in ("http://a;b/", "http://h,x", "http://[::1]:80", "http://good.host:8701"):
            self.assertEqual(oo._origin(u) is not None, u in ("http://[::1]:80", "http://good.host:8701"), u)

    def test_callback_internal_url_rewrite(self):
        a = self.app
        a.oo_set("admin", docs_internal_url="http://127.0.0.1:8701")
        c = self.cfg("dev1", "l", self.lib)
        self.result = b"PK\x03\x04 rewritten"
        self.cb(c, 2, ["dev1"])
        self.assertEqual(self.fetched["url"], "http://127.0.0.1:8701/cache/files/out.docx")
        self.assertIn(oo._origin("http://127.0.0.1:8701"), self.fetched["allowed"])

    def test_callback_attachment_and_lock(self):
        a, d = self.app, self.doc
        c = self.cfg("dev1", "d", d, 1)
        self.result = b"PK\x03\x04 edited docx"
        r = self.cb(c, 2, ["dev1"])
        self.assertTrue(r["saved"])
        self.assertEqual(self.fetched["cap"], 10 * 1024 * 1024)
        m = a.show("dev1", d)
        att = m["attachments"][0]
        self.assertEqual((att["name"], att["ver"], att["size"]), ("보고.docx", 2, len(self.result)))
        self.assertEqual(a.get_attachment("dev1", d, 1)[1].read_bytes(), self.result)
        self.assertEqual(att["prev"][0]["sha256"], hashlib.sha256(DOCX).hexdigest())
        self.assertIn(("dev1", "attach_edit"), [(x["actor"], x["action"]) for x in a.audit_list("admin")])
        # 편집기를 연 뒤 상신(잠금) → 저장 거부
        a.submit("dev1", d)
        self.result = b"PK\x03\x04 late save"
        self.assertEqual(self.cb(c, 2, ["dev1"])["error"], 1)
        self.assertEqual(a.get_attachment("dev1", d, 1)[1].read_bytes(), b"PK\x03\x04 edited docx")
        self.assertTrue(a.verify_audit("admin")["ok"])

    def test_attachment_replace_limits_and_remove(self):
        a, d = self.app, self.doc
        with self.assertRaises(core.Invalid):
            a.attachment_replace("dev1", d, 1, b"")
        with self.assertRaises(core.Invalid):
            a.attachment_replace("dev1", d, 1, b"x" * (core.MAX_FILE_BYTES + 1))
        with self.assertRaises(core.NotFound):
            a.attachment_replace("dev1", d, 9, b"x")
        with self.assertRaises(core.NotFound):
            a.attachment_replace("devlead", d, 1, b"x")
        self.assertTrue(a.attachment_replace("dev1", d, 1, DOCX)["unchanged"])
        a.attachment_replace("dev1", d, 1, b"PK 2")
        files = a._doc_dir(d) / "files"
        self.assertEqual(sorted(p.name for p in files.iterdir()), ["01.docx", "01v2.docx", "02.txt"])
        a.edit_draft("dev1", d, remove_attachments=[1])   # 지우면 이전 판 파일도 지움
        self.assertEqual(sorted(p.name for p in files.iterdir()), ["02.txt"])


class WebOOTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app, cls.d = world()
        cls.app.oo_set("admin", enabled=True, docs_url=DOCS, app_internal_url="http://gyeol.test:8130",
                       jwt_secret=SECRET)
        cls.fid = cls.app.files_upload("dev1", "계획.docx", b"PK docx")["id"]
        cls.srv = make_server(cls.app, "127.0.0.1", 0)
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        rmtree(cls.d)

    def client(self):
        from test_web import Client
        return Client(self.base)

    def test_editor_page_csp_and_buttons(self):
        c = self.client()
        c.login("dev1")
        st, body, _ = c.req(f"/files/{self.fid}")
        self.assertIn(f"/oo/edit/l/{self.fid}?mode=edit", body)
        st, body, h = c.req(f"/oo/edit/l/{self.fid}?mode=edit")
        self.assertEqual(st, 200)
        csp = h["Content-Security-Policy"]
        self.assertIn(f"script-src 'self' {DOCS}", csp)
        self.assertIn(f"frame-src {DOCS}", csp)
        self.assertNotIn("unsafe-eval", csp)
        self.assertNotIn("unsafe-inline", csp.split("script-src")[1].split(";")[0])
        self.assertIn(DOCS + "/web-apps/apps/api/documents/api.js", body)
        cfg = json.loads(re.search(r"<script type=application/json id=oo-cfg>(.*?)</script>", body, re.S).group(1))
        self.assertEqual(cfg["editorConfig"]["mode"], "edit")
        # 다른 화면은 문서 서버 출처 없음
        _, _, h2 = c.req("/files")
        self.assertNotIn(DOCS, h2["Content-Security-Policy"])
        s = self.client()
        s.login("sal1")
        self.assertEqual(s.req(f"/oo/edit/l/{self.fid}?mode=view")[0], 404)

    def test_public_endpoints_no_session(self):
        c = self.client()
        st, _, _ = c.req("/oo/dl/" + "A" * 40)
        self.assertEqual(st, 404)
        st, body, _ = c.req("/oo/cb/" + "A" * 40, data=b'{"status":2}', ctype="application/json")
        self.assertEqual((st, json.loads(body)), (403, {"error": 1}))
        cfg = self.app.oo_editor_config("dev1", "l", self.fid, None, "edit")["config"]
        st, _, _ = c.req(urllib.parse.urlsplit(cfg["editorConfig"]["callbackUrl"]).path, data=b"x" * (300 * 1024),
                         ctype="application/json")
        self.assertEqual(st, 413)
        dl = urllib.parse.urlsplit(cfg["document"]["url"]).path
        st, body, h = c.req(dl)
        self.assertEqual((st, body), (200, "PK docx"))
        cb = urllib.parse.urlsplit(cfg["editorConfig"]["callbackUrl"]).path
        tok = oo.jwt_sign({"key": cfg["document"]["key"], "status": 4}, SECRET, ttl=60)
        st, body, _ = c.req(cb, data=json.dumps({"token": tok}).encode(), ctype="application/json",
                            headers={"Origin": "http://evil.test"})
        self.assertEqual((st, json.loads(body)), (200, {"error": 0}))
        self.assertEqual(c.req("/oo/edit/l/1")[0], 303)   # 편집기 화면은 로그인 필요

    def test_admin_page(self):
        c = self.client()
        c.login("admin", "adminpass1")
        st, body, _ = c.req("/admin/onlyoffice")
        self.assertEqual(st, 200)
        self.assertNotIn(SECRET, body)
        self.assertIn(DOCS, body)


if __name__ == "__main__":
    unittest.main()
