import http.cookiejar
import re
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request

from helpers import PW, rmtree, world

from gyeoljae.web import make_server


class Client:
    def __init__(self, base):
        self.base = base
        self.jar = http.cookiejar.CookieJar()

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar), NoRedirect)

    def req(self, path, data=None, headers=None, ctype=None):
        h = dict(headers or {})
        if isinstance(data, dict):
            data = urllib.parse.urlencode(data).encode()
        if ctype:
            h["Content-Type"] = ctype
        r = urllib.request.Request(self.base + path, data=data, headers=h)
        try:
            resp = self.op.open(r)
            return resp.status, resp.read().decode("utf-8", "replace"), resp.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace"), e.headers

    def login(self, u, pw=PW):
        return self.req("/login", {"username": u, "password": pw})

    def csrf(self, path="/box/inbox"):
        _, body, _ = self.req(path)
        return re.search(r"name=csrf value='([^']+)'", body).group(1)


def multipart(fields, files):
    b = "----gyeoltestboundary"
    out = b""
    for k, v in fields.items():
        out += f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    for k, (fn, data) in files:
        out += (f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{fn}\"\r\n"
                f"Content-Type: application/octet-stream\r\n\r\n").encode() + data + b"\r\n"
    out += f"--{b}--\r\n".encode()
    return out, f"multipart/form-data; boundary={b}"


class WebTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app, cls.d = world()
        cls.srv = make_server(cls.app, "127.0.0.1", 0)
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        rmtree(cls.d)

    def test_login_and_cookie(self):
        c = Client(self.base)
        st, _, h = c.req("/box/inbox")
        self.assertEqual(st, 303)
        st, _, h = c.login("dev1")
        self.assertEqual(st, 303)
        sc = h["Set-Cookie"]
        self.assertIn("HttpOnly", sc)
        self.assertIn("SameSite=Strict", sc)
        st, body, h = c.req("/box/inbox")
        self.assertEqual(st, 200)
        self.assertIn("default-src 'none'", h["Content-Security-Policy"])

    def test_csrf(self):
        c = Client(self.base)
        c.login("dev1")
        st, _, _ = c.req("/msg/send", {"to": "hr", "body": "x"})
        self.assertEqual(st, 403)
        tok = c.csrf()
        st, _, _ = c.req("/msg/send", {"to": "hr", "body": "x", "csrf": tok}, {"Origin": "http://evil.example"})
        self.assertEqual(st, 403)
        st, _, _ = c.req("/msg/send", {"to": "hr", "body": "x", "csrf": tok})
        self.assertEqual(st, 303)

    def test_draft_xss_attach_and_leak(self):
        c = Client(self.base)
        c.login("sal1")
        tok = c.csrf()
        data, ct = multipart({"csrf": tok, "title": "<script>alert(1)</script>견적", "line": "sallead",
                              "cc": "", "body": "<img src=x onerror=alert(1)>", "go": "submit"},
                             [("files", ("../../../etc/passwd.pdf", b"%PDF-1")), ("files", ("견적서.hwpx", b"PK"))])
        st, _, h = c.req("/new", data, ctype=ct)
        self.assertEqual(st, 303, _)
        did = int(h["Location"].split("?")[0].rsplit("/", 1)[1])
        st, body, _ = c.req(f"/doc/{did}")
        self.assertNotIn("<script>alert", body)
        self.assertNotIn("<img src=x", body)
        self.assertIn("&lt;script&gt;", body)
        self.assertIn("passwd.pdf", body)
        st, data, h = c.req(f"/doc/{did}/file/2")
        self.assertEqual(st, 200)
        self.assertIn("attachment", h["Content-Disposition"])
        self.assertEqual(h["Content-Type"], "application/octet-stream")
        # 다른 부서 사용자는 문서/첨부/검색 모두 불가
        o = Client(self.base)
        o.login("dev1")
        self.assertEqual(o.req(f"/doc/{did}")[0], 404)
        self.assertEqual(o.req(f"/doc/{did}/file/1")[0], 404)
        self.assertNotIn(f"/doc/{did}'", o.req("/search?q=" + urllib.parse.quote("견적"))[1])
        # 결재자 승인 via web
        a = Client(self.base)
        a.login("sallead")
        t2 = a.csrf(f"/doc/{did}")
        rev = re.search(r"name=rev value='(\d+)'", a.req(f"/doc/{did}")[1]).group(1)
        self.assertEqual(o.req(f"/doc/{did}/approve", {"csrf": o.csrf(), "comment": "", "rev": rev, "confirm": "1"})[0], 404)
        self.assertEqual(a.req(f"/doc/{did}/approve", {"csrf": t2, "comment": "x", "rev": "99", "confirm": "1"})[0], 400)
        self.assertEqual(a.req(f"/doc/{did}/approve", {"csrf": t2, "comment": "x", "confirm": "1"})[0], 400)
        self.assertEqual(a.req(f"/doc/{did}/approve", {"csrf": t2, "comment": "x", "rev": rev})[0], 400)  # 확인 단계 없이
        st, body, _ = a.req(f"/doc/{did}/confirm", {"csrf": t2, "act": "reject", "comment": "짧음", "rev": rev})
        self.assertEqual(st, 400)
        self.assertIn("5자 이상", body)
        self.assertIn("value='짧음'", body)  # 입력값 유지
        st, body, _ = a.req(f"/doc/{did}/confirm", {"csrf": t2, "act": "approve", "comment": "좋음", "rev": rev})
        self.assertIn("결재 확인", body)
        self.assertIn("autofocus>취소", body)
        self.assertEqual(o.req(f"/doc/{did}/confirm", {"csrf": o.csrf(), "act": "approve", "rev": rev})[0], 404)
        st, _, _ = a.req(f"/doc/{did}/approve", {"csrf": t2, "comment": "좋음", "rev": rev, "confirm": "1"})
        self.assertEqual(st, 303)
        self.assertIn("SAL-", a.req(f"/doc/{did}")[1])

    def test_admin_page_forbidden(self):
        c = Client(self.base)
        c.login("ceo")
        self.assertEqual(c.req("/admin")[0], 403)
        a = Client(self.base)
        a.login("admin", "adminpass1")
        st, body, _ = a.req("/admin/audit?verify=1")
        self.assertEqual(st, 200)
        self.assertIn("감사로그 정상", body)

    def test_admin_emp_policy_pages(self):
        a = Client(self.base)
        a.login("admin", "adminpass1")
        tok = a.csrf("/admin/emp")
        st, body, _ = a.req("/admin/emp", {"csrf": tok, "name": "<b>웹신입</b>", "dept": "DEV", "position": "member",
                                           "emp_no": "W1", "username": "", "email": "", "phone": "", "hired": "",
                                           "title": ""})
        self.assertEqual(st, 200)
        self.assertNotIn("<b>웹신입", body)
        temp = re.search(r"임시 비밀번호: <b class=ok>([^<]+)</b>", body).group(1)
        n = Client(self.base)
        st, _, h = n.login("w1", temp)
        self.assertEqual(st, 303)
        st, _, h = n.req("/box/inbox")
        self.assertEqual((st, h["Location"]), (303, "/me"))  # 첫 로그인 → 비밀번호 변경 강제
        t2 = n.csrf("/me")
        st, _, _ = n.req("/me", {"csrf": t2, "old": temp, "new1": "brandnew99", "new2": "brandnew99"})
        self.assertEqual(st, 303)
        n2 = Client(self.base)
        n2.login("w1", "brandnew99")
        self.assertEqual(n2.req("/box/inbox")[0], 200)
        for path in ("/admin/emp", "/admin/policy/DEV", "/admin/emp/export", "/admin/changes"):
            self.assertEqual(n2.req(path)[0], 403, path)
        self.assertEqual(n2.req("/admin/emp", {"csrf": n2.csrf(), "name": "x", "dept": "DEV", "position": "member"})[0], 403)
        # 결재규정 폼 저장 → 미리보기
        st, _, _ = a.req("/admin/policy/DEV", {"csrf": tok, "expense_on": "1", "expense_line": "부서장",
                                                "expense_jeon": "부서장:100만", "expense_cc": "", "dg_allowed": "1"})
        self.assertEqual(st, 303)
        st, body, _ = a.req("/admin/preview?dept=DEV&type=expense&amount=" + urllib.parse.quote("50만"))
        self.assertIn("개발장", body)
        self.assertIn("v1", a.req("/admin/policy/DEV/history")[1])
        # CSV 업로드 미리보기
        data, ct = multipart({"csrf": tok, "go": "preview"},
                             [("csv", ("e.csv", "사번,이름,부서,직위\nC9,씨에스브이,SAL,팀원\n".encode("cp949")))])
        st, body, _ = a.req("/admin/emp/import", data, ctype=ct)
        self.assertIn("미리보기", body)
        self.assertIn("씨에스브이", body)
        st, body, h = a.req("/admin/emp/export?enc=cp949")
        self.assertIn("text/csv", h["Content-Type"])
        # 조직도: 일반 사용자 접근 가능, 연락처 비공개
        self.assertEqual(n2.req("/org")[0], 200)

    def test_rate_limit_web(self):
        c = Client(self.base)
        for _ in range(5):
            c.login("hr", "badbadbad")
        st, body, _ = c.login("hr")
        self.assertEqual(st, 429)


if __name__ == "__main__":
    unittest.main()
