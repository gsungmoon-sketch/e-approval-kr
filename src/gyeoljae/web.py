"""gyeoljae 웹 UI: 표준 라이브러리 http.server, 서버 렌더링 HTML. 모든 기능은 core.App 호출.

보안: 세션 쿠키 HttpOnly+SameSite=Strict(+Secure 옵션), 모든 POST에 CSRF 토큰 + Origin 검사,
로그인 실패 제한(core), CSP(script 없음), 모든 출력 html.escape.
"""
from __future__ import annotations

import hmac
import json
import html
import os
import re
import sys
import threading
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import core
from . import policy as pol
from .core import App, BOX_KO, BOXES, BOARDS, GyeolError

MAX_REQ = 32 * 1024 * 1024
MAX_REQ_ANON = 16 * 1024          # 로그인 전 요청 본문 상한
MAX_CONCURRENT = 32               # 동시 처리 요청 상한(초과 시 503)
_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT)
COOKIE = "gyeol_sid"
SECURE_COOKIE = os.environ.get("GYEOLJAE_COOKIE_SECURE") == "1"
TRUST_PROXY = os.environ.get("GYEOLJAE_TRUST_PROXY") == "1"

CSS = """
:root{--bg:#F5F6F8;--fg:#1A1F2B;--mut:#4A5263;--card:#fff;--line:#C9CED8;--acc:#1F4E9A;--primary:#1F4E9A;--acc-fg:#fff;--focus:#FFB000;--warn:#B3261E;--ok:#1B6E35;--badge:#C62828;--side:#EEF1F6;--chip-p:#DCE8FB;--chip-d:#D7F0DD;--chip-r:#FADBD8;--chip-w:#ECEEF2;--chip-l:#FFE8B3}
*{box-sizing:border-box}html,body{margin:0}body{font:15px/1.6 system-ui,"Malgun Gothic","Apple SD Gothic Neo",sans-serif;background:var(--bg);color:var(--fg)}
a{color:var(--acc)}:focus-visible{outline:3px solid var(--focus);outline-offset:2px}
.skip{position:absolute;left:-999px;top:0;background:var(--focus);color:#1A1F2B;padding:8px 12px;z-index:20}.skip:focus{left:8px}
.sr-only{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
header.top{display:flex;gap:12px;align-items:center;height:56px;padding:0 16px;background:var(--primary);color:#fff;position:sticky;top:0;z-index:5}
header.top a{color:#fff}.brand{font-weight:700;font-size:19px;text-decoration:none}header.top .qs{flex:1;max-width:420px}header.top .me{margin-left:auto;white-space:nowrap;display:flex;gap:10px;align-items:center}
header.top input{width:100%;border:0}header.top button.sec{padding:4px 10px}
.layout{display:grid;grid-template-columns:220px minmax(0,1fr);min-height:calc(100vh - 56px)}.layout.has-aside{grid-template-columns:220px minmax(0,1fr) 280px}
nav.side{background:var(--side);border-right:1px solid var(--line);padding:10px 0}nav.side ul{list-style:none;margin:0;padding:0}
nav.side a{display:block;padding:9px 18px;color:var(--fg);text-decoration:none}nav.side a.top.on{font-weight:700;border-left:4px solid var(--primary);padding-left:14px}
nav.side ul ul a{padding:6px 18px 6px 30px;font-size:14px}nav.side a[aria-current=page]{background:var(--primary);color:#fff;font-weight:600}
aside.right{border-left:1px solid var(--line);padding:12px;background:var(--card)}
nav.bottom{display:none}
.badge{display:inline-block;min-width:20px;padding:0 6px;border-radius:10px;background:var(--badge);color:#fff;font-size:12px;line-height:20px;text-align:center;font-weight:700}
main{min-width:0;max-width:1100px;width:100%;padding:8px 20px 40px}main.solo{margin:0 auto}main:focus{outline:0}
nav.tabs{display:flex;flex-wrap:wrap;gap:6px;margin:6px 0 10px}nav.tabs a{padding:4px 12px;border:1px solid var(--line);border-radius:14px;color:var(--fg);text-decoration:none;background:var(--card)}
nav.tabs a.on{background:var(--primary);color:#fff;border-color:var(--primary)}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 14px;margin:10px 0}.card.empty{text-align:center;color:var(--mut);padding:28px}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 12px}.dash h3{margin:0 0 6px;font-size:16px}.dash h3 a{color:var(--fg);text-decoration:none}
.num{float:right;font-size:20px;color:var(--acc)}ul.items{list-style:none;margin:0;padding:0}ul.items li{padding:4px 0;border-bottom:1px solid var(--line);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
ul.menu{columns:2;margin:0}.more{display:inline-block;margin-top:6px;font-size:13px}.new{color:var(--badge);font-weight:700;font-size:12px}
.quick{display:flex;flex-wrap:wrap;gap:8px;align-items:center}.quick .lbl{font-weight:700}.quick form{margin-left:auto;display:flex;gap:6px}
.btn{display:inline-block;padding:6px 14px;border-radius:6px;border:1px solid var(--acc);color:var(--acc);text-decoration:none;background:var(--card)}.hint{border-style:dashed}
.chip{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px;font-weight:700;color:#1A1F2B;background:var(--chip-w);white-space:nowrap}
.chip.p{background:var(--chip-p)}.chip.d{background:var(--chip-d)}.chip.r{background:var(--chip-r)}.chip.w{background:var(--chip-w)}.chip.late{background:var(--chip-l)}
.banner{border-radius:8px;padding:10px 14px;margin:10px 0;font-weight:600;color:#1A1F2B}.banner.ok{background:var(--chip-d)}.banner.warn{background:var(--chip-r)}
table{width:100%;border-collapse:collapse}td,th{padding:7px 6px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}th{color:var(--mut);font-weight:600;font-size:13px}
table.stamp{width:auto;border:1px solid #1A1F2B;margin-left:auto}table.stamp caption{font-size:12px;color:var(--mut)}table.stamp th,table.stamp td{border:1px solid #1A1F2B;text-align:center;min-width:64px;font-size:12px;padding:3px}
table.stamp td.sp{height:58px;vertical-align:middle}.seal{display:inline-block;border:2px solid var(--warn);color:var(--warn);border-radius:50%;width:46px;height:46px;line-height:42px;font-weight:700;font-size:13px}
.seal.rej{border-style:double}.docgrid{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:12px;align-items:start}
table.form th{width:130px;background:#F0F2F6;color:var(--fg);border:1px solid var(--line)}table.form td{border:1px solid var(--line)}table.form td.amt{text-align:right}
.savebar{position:sticky;bottom:0;display:flex;gap:8px;justify-content:flex-end;padding:10px;background:var(--card);border-top:1px solid var(--line);z-index:4}
.pick{display:inline-flex;gap:6px;align-items:center;border:1px solid var(--line);border-radius:8px;padding:8px 12px;margin:4px;background:var(--card);font-weight:500}
.pick:has(input:checked){border:2px solid var(--primary);background:var(--chip-p)}
.pager{display:flex;gap:4px;flex-wrap:wrap;margin:10px 0}.pager a,.pager span{padding:4px 10px;border:1px solid var(--line);border-radius:6px;text-decoration:none}.pager [aria-current]{background:var(--primary);color:#fff}
.req{color:var(--warn);font-weight:700}ul.forms{list-style:none;padding:0;margin:0;display:flex;flex-wrap:wrap}ul.forms .pick a{color:var(--fg)}
.tablewrap{overflow-x:auto}table.form.items{margin:8px 0}table.form.items caption{text-align:left;font-weight:700;padding:4px 0}table.form.items td input{min-width:70px}tr.sum th{background:#F0F2F6;color:var(--fg)}
body.print main{max-width:190mm}h1.ptitle{text-align:center;letter-spacing:.3em;font-size:24px}
.mut{color:var(--mut);font-size:13px}.err{color:var(--warn);font-weight:600}.ok{color:var(--ok)}
pre.body{white-space:pre-wrap;word-break:break-word;font:inherit;margin:0}
input[type=text],input[type=search],input[type=password],input[type=date],input[type=number],input[type=file],textarea,select{width:100%;padding:7px;border:1px solid var(--line);border-radius:6px;background:#fff;color:var(--fg);font:inherit}
.quick input[type=text]{width:auto;flex:1}
textarea{min-height:180px}label{display:block;margin:8px 0 3px;font-weight:600}label.pick{display:inline-flex}
button{padding:7px 14px;border-radius:6px;border:1px solid var(--acc);background:var(--acc);color:#fff;font:inherit;cursor:pointer}
button.sec{background:var(--card);color:var(--fg);border-color:var(--line)}button.warn{background:var(--warn);border-color:var(--warn);color:#fff}
form.inline{display:inline}.row{display:flex;flex-wrap:wrap;gap:8px}.row>*{flex:1 1 160px}
.line{display:flex;flex-wrap:wrap;gap:6px}.step{border:1px solid var(--line);border-radius:6px;padding:4px 8px;font-size:13px}
.step.approved{border-color:var(--ok)}.step.rejected{border-color:var(--warn)}.step.pending{border-color:var(--acc);border-width:2px}
.login{max-width:360px;margin:40px auto}textarea.short{min-height:80px}
@media (max-width:767px){.layout,.layout.has-aside{display:block}nav.side{display:none}aside.right{border:0;border-top:1px solid var(--line)}
 .grid{grid-template-columns:1fr}header.top .qs{display:none}main{padding:8px 16px 80px}.quick form{margin-left:0;width:100%}td.hide,th.hide{display:none}
 table.list thead{display:none}table.list tr{display:block;border:1px solid var(--line);border-radius:8px;margin:8px 0;background:var(--card);padding:6px}
 table.list td{display:block;border:0;padding:2px 6px}table.list td[data-l]::before{content:attr(data-l) " · ";color:var(--mut);font-size:12px}
 .docgrid{grid-template-columns:1fr}table.stamp{margin:0}ul.menu{columns:1}
 nav.bottom{display:flex;position:fixed;left:0;right:0;bottom:0;background:var(--card);border-top:1px solid var(--line);z-index:5}
 nav.bottom a{flex:1;text-align:center;padding:10px 2px;color:var(--fg);text-decoration:none;font-size:13px;min-height:48px}nav.bottom a.on{color:var(--acc);font-weight:700}}
@media print{@page{size:A4;margin:15mm}header.top,nav.side,nav.bottom,aside.right,.savebar,.noprint,form button{display:none!important}
 body{background:#fff;font-size:11pt}.layout{display:block}main{max-width:none;padding:0}.card{border:0;padding:0}a{color:#000;text-decoration:none}}
"""


def esc(s):
    return html.escape("" if s is None else str(s), quote=True)


class Resp(Exception):
    def __init__(self, status=200, body=b"", headers=None):
        self.status, self.body, self.headers = status, body, headers or {}


def redirect(path):
    if not path.startswith("/") or path.startswith("//"):
        path = "/"
    return Resp(303, b"", {"Location": path})


def make_handler(app: App):
    class H(BaseHTTPRequestHandler):
        server_version = "gyeoljae"
        sys_version = ""
        timeout = 30  # slowloris 방어

        def log_message(self, fmt, *a):
            sys.stderr.write("%s %s\n" % (self.client_ip(), fmt % a))

        def client_ip(self):
            if TRUST_PROXY:
                cf = self.headers.get("Cf-Connecting-Ip", "").strip()
                if cf:
                    return cf[:64]
                xf = self.headers.get("X-Forwarded-For", "")
                if xf:
                    return xf.split(",")[-1].strip()[:64]
            return self.client_address[0]

        # ------------------------------------------------ plumbing
        def setup(self):
            super().setup()
            hs = getattr(self.connection, "do_handshake", None)
            if hs:  # TLS: 연결별 스레드에서 시간 제한 걸고 핸드셰이크(accept 루프를 막지 않음)
                self.connection.settimeout(self.timeout)
                hs()

        def do_GET(self):
            self.guarded("GET")

        def do_POST(self):
            self.guarded("POST")

        def guarded(self, method):
            if not _SLOTS.acquire(timeout=5):
                self.send(Resp(503, b"busy", {"Content-Type": "text/plain", "Retry-After": "5"}))
                return
            try:
                self.dispatch(method)
            finally:
                _SLOTS.release()

        def send(self, r: Resp):
            self.send_response(r.status)
            h = {"Content-Security-Policy": "default-src 'none'; style-src 'self'; img-src 'self'; form-action 'self'; "
                                            "frame-ancestors 'none'; base-uri 'none'",
                 "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "same-origin",
                 "Cache-Control": "no-store"}
            if SECURE_COOKIE:
                h["Strict-Transport-Security"] = "max-age=31536000"
            h.update(r.headers)
            h.setdefault("Content-Type", "text/html; charset=utf-8")
            for k, v in h.items():
                if isinstance(v, list):
                    for x in v:
                        self.send_header(k, x)
                else:
                    self.send_header(k, v)
            self.send_header("Content-Length", str(len(r.body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(r.body)

        def dispatch(self, method):
            try:
                self.method = method
                u = urllib.parse.urlsplit(self.path)
                self.route = u.path
                self.qs = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
                self.form, self.files = {}, []
                self._aside = None
                self.sess = app.session(self.cookie())
                self.me = None
                if self.sess:
                    try:
                        self.me = app.get_user(self.sess["username"])
                    except GyeolError:
                        self.sess = None
                if method == "POST":
                    self.read_body()
                    self.check_csrf()
                self.send(self.handle_route())
            except Resp as r:
                self.send(r)
            except core.NotFound as e:
                self.send(Resp(404, self.page("없음", f"<p class=err>{esc(e)}</p>")))
            except core.RateLimited as e:
                self.send(Resp(429, self.page("제한", f"<p class=err>{esc(e)}</p>")))
            except (GyeolError, ValueError) as e:
                self.send(Resp(400 if isinstance(e, (core.Invalid, ValueError)) else 403,
                               self.page("오류", f"<p class=err>{esc(e)}</p><p><a href='/'>처음으로</a></p>")))
            except Exception as e:  # noqa
                import traceback
                traceback.print_exc()
                self.send(Resp(500, self.page("오류", "<p class=err>서버 오류</p>")))

        def cookie(self):
            raw = self.headers.get("Cookie", "")
            for part in raw.split(";"):
                k, _, v = part.strip().partition("=")
                if k == COOKIE:
                    return v
            return None

        def read_body(self):
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                raise Resp(400, b"bad length")
            if n < 0 or n > (MAX_REQ if self.sess else MAX_REQ_ANON):
                raise Resp(413, self.page("너무 큼", "<p class=err>요청이 너무 큽니다(32MB)</p>"),
                           {"Connection": "close"})
            data = self.rfile.read(n) if n else b""
            ct = self.headers.get("Content-Type", "")
            if ct.startswith("multipart/form-data"):
                self.form, self.files = parse_multipart(data, ct)
            else:
                self.form = {k: v[0] for k, v in urllib.parse.parse_qs(data.decode("utf-8", "replace"),
                                                                       keep_blank_values=True).items()}

        def check_csrf(self):
            origin = self.headers.get("Origin")
            if origin and origin != "null":
                host = self.headers.get("X-Forwarded-Host") if TRUST_PROXY and self.headers.get("X-Forwarded-Host") \
                    else self.headers.get("Host", "")
                if urllib.parse.urlsplit(origin).netloc != host:
                    raise Resp(403, self.page("차단", "<p class=err>출처가 다른 요청입니다</p>"))
            if self.route in ("/login", "/setup") or (self.route == "/logout" and not self.sess):
                return  # 로그인 전: Origin 검사로 로그인 CSRF 차단(세션 없음)
            if not self.sess or not hmac.compare_digest(self.form.get("csrf", ""), self.sess["csrf"]):
                raise Resp(403, self.page("차단", "<p class=err>CSRF 토큰 오류. 새로고침 후 다시 시도하세요</p>"))

        def need_login(self):
            if not self.me:
                q = {}
                if self.method == "GET" and self.route not in ("/", "/login"):
                    q["next"] = self.path[:500]
                if self.cookie():
                    q["expired"] = "1"  # 쿠키는 있는데 세션이 없음 = 만료/로그아웃
                raise redirect("/login" + ("?" + urllib.parse.urlencode(q) if q else ""))
            return self.me["username"]

        def csrf(self):
            return f"<input type=hidden name=csrf value='{esc(self.sess['csrf'])}'>" if self.sess else ""

        def page(self, title, body, box=None):
            me = getattr(self, "me", None)
            if not me:
                doc_body = f"<main class=solo>{body}</main>"
            else:
                from .web_home import shell
                doc_body = shell(self, app, me, body, box)
            doc = (f"<!doctype html><html lang=ko><head><meta charset=utf-8>"
                   f"<meta name=viewport content='width=device-width,initial-scale=1'>"
                   f"<title>{esc(title)} - 결재</title><link rel=stylesheet href='/s.css?v=6'></head>"
                   f"<body>{doc_body}</body></html>")
            return doc.encode("utf-8")

        def ok(self, title, body, box=None, headers=None):
            return Resp(200, self.page(title, body, box), headers)

        # ------------------------------------------------ routes
        def handle_route(self):
            p, m = self.route, self.method
            if p == "/s.css":
                return Resp(200, CSS.encode(), {"Content-Type": "text/css; charset=utf-8",
                                                "Cache-Control": "public, max-age=86400"})
            if p == "/healthz":
                from . import __version__
                return Resp(200, json.dumps({"ok": True, "version": __version__}).encode(),
                            {"Content-Type": "application/json", "Cache-Control": "no-store"})
            if p == "/setup" or not app.has_users():
                from .web_setup import setup_route
                return setup_route(self, app)
            if p == "/login":
                return self.r_login()
            if p == "/logout" and m == "POST":
                if self.sess:
                    app.logout(self.cookie())
                r = redirect("/login")
                r.headers["Set-Cookie"] = f"{COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict"
                return r
            user = self.need_login()
            if self.sess.get("must_change") and p != "/me":
                return redirect("/me")  # 임시 비밀번호 → 변경 전에는 아무 것도 못 함
            from .web_home import route as home_route
            hr = home_route(self, app, user, p)
            if hr is not None:
                return hr
            mm = re.fullmatch(r"/box/(\w+)", p)
            if mm and mm.group(1) in BOXES:
                return self.r_box(user, mm.group(1))
            if p == "/new":
                return self.r_new(user)
            if p == "/forms/fav" and self.method == "POST":
                app.form_fav(user, self.form.get("code", ""), self.form.get("on") == "1")
                return redirect("/new")
            mm = re.fullmatch(r"/doc/(\d{1,9})(?:/(\w+)(?:/(\d{1,3}))?)?", p)
            if mm:
                return self.r_doc(user, int(mm.group(1)), mm.group(2), mm.group(3))
            if p == "/search":
                return self.r_search(user)
            mm = re.fullmatch(r"/msg(?:/(\d{1,9}|send))?", p)
            if mm:
                return self.r_msg(user, mm.group(1))
            mm = re.fullmatch(r"/board/(\w+)(?:/(\d{1,9}|new|del|pin))?", p)
            if mm and mm.group(1) in BOARDS:
                return self.r_board(user, mm.group(1), mm.group(2))
            if p == "/delegate":
                return self.r_delegate(user)
            if p == "/org":
                return self.r_org(user)
            if p == "/preview":
                return self.r_preview(user)
            if p == "/me":
                return self.r_me(user)
            if p == "/admin" or p.startswith("/admin/"):
                if self.me["role"] != "admin":
                    raise core.Forbidden("관리자 전용")
                from .web_admin import admin_route
                return admin_route(self, app, user, p)
            raise core.NotFound("페이지 없음")

        def r_login(self):
            err = ""
            if self.method == "POST":
                try:
                    tok, _ = app.login(self.form.get("username", ""), self.form.get("password", ""), self.client_ip())
                    sec = "; Secure" if SECURE_COOKIE else ""
                    nxt = self.qs.get("next") or self.form.get("next") or "/"
                    if not re.fullmatch(r"/[A-Za-z0-9/_\-?=&%.~+]*", nxt) or nxt.startswith("//") or "\\" in nxt:
                        nxt = "/"
                    r = redirect(nxt)
                    r.headers["Set-Cookie"] = (f"{COOKIE}={tok}; Path=/; Max-Age={core.SESSION_TTL}; HttpOnly; "
                                               f"SameSite=Strict{sec}")
                    return r
                except core.RateLimited as e:
                    err = str(e)
                except GyeolError as e:
                    err = str(e)
            if not err and self.qs.get("expired"):
                err = "세션이 만료되었습니다. 다시 로그인하세요."
            nxt = self.qs.get("next") or self.form.get("next") or ""
            body = (f"<div class='card login'><h2>전자결재 로그인</h2>"
                    f"{'<p class=err>' + esc(err) + '</p>' if err else ''}"
                    f"<form method=post action='/login'><input type=hidden name=next value='{esc(nxt)}'>"
                    f"<label>아이디 <span class=mut>(사번)</span></label><input type=text name=username "
                    f"autocomplete=username autofocus><label>비밀번호</label><input type=password name=password "
                    f"autocomplete=current-password><p><button>로그인</button></p></form></div>")
            return Resp(429 if "너무 많" in err else 200, self.page("로그인", body))

        def page_no(self):
            p = self.qs.get("page", "1")
            return int(p) if p.isdigit() and 0 < int(p) < 100000 else 1

        def table(self, rows, base="", inbox=False):
            from .web_ui import list_table
            return list_table(rows, self.page_no(), base or self.path.split("?")[0], inbox=inbox)

        def r_box(self, user, box):
            rows = app.list_box(user, box)
            if box == "inbox":
                rows.sort(key=lambda r: (r["submitted"] or "", r["id"]))  # 오래된 순
            return self.ok(BOX_KO[box], f"<h2>결재 › {BOX_KO[box]}</h2>{self.table(rows, f'/box/{box}', box == 'inbox')}",
                           box)

        def user_datalist(self):
            opts = "".join(f"<option value='{esc(u['username'])}'>{esc(u['name'])} {esc(u['dept'])} {esc(u['title'] or '')}"
                           f"</option>" for u in app.list_users() if u["active"])
            return f"<datalist id=users>{opts}</datalist>"

        def doc_form(self, action, m=None, err=""):
            from .web_ui import draft_form
            return draft_form(self, app, self.me["username"], action, m, err)

        def _form_doc(self, m=None):
            """POST 실패 시 입력값을 유지해 다시 보여 주기 위한 값."""
            f = self.form
            d = dict(m or {})
            d.update({"title": f.get("title", ""), "body": f.get("body", ""), "doc_type": f.get("doc_type") or "general",
                      "amount": f.get("amount") or None, "line_input": self._split(f.get("line")),
                      "cc_input": self._split(f.get("cc")), "visibility": "dept" if f.get("dept_open") else "private"})
            fd = self._fd(d)
            if fd:
                d["fields"] = self._fields(fd)
                d["doc_type"] = fd["doc_type"]
            return d

        def _fd(self, m):
            """편집 중인 문서(기안 당시 버전) 또는 새 기안의 양식 정의."""
            m = m or {}
            if m.get("id"):
                return app.form_for_doc(self.me["username"], m["id"])
            code = self.form.get("form")
            if not code:
                return None
            fd = app.form_get(self.me["username"], code)
            m["form"] = {"code": code, "version": fd["version"]}
            return fd

        def _fields(self, fd):
            from .formdef import from_flat
            return from_flat(fd, self.form.get)

        def _split(self, s):
            return [x.strip() for x in (s or "").split(",") if x.strip()]

        def r_new(self, user):
            if self.method == "POST":
                f = self.form
                try:
                    fd = app.form_get(user, f["form"]) if f.get("form") else None
                    m = app.create_draft(user, f.get("title"), f.get("body", ""), self._split(f.get("line")),
                                         self._split(f.get("cc")), "dept" if f.get("dept_open") else "private",
                                         self.files, submit=f.get("go") == "submit",
                                         doc_type=f.get("doc_type") or "general", amount=f.get("amount") or None,
                                         form=fd and fd["code"], fields=fd and self._fields(fd))
                except (core.Invalid, core.NotFound) as e:  # 입력값 유지
                    d = self._form_doc()
                    return Resp(400, self.page("기안", f"<h2>새 기안</h2>{self.doc_form('/new', d, str(e))}"))
                if f.get("go") == "save":
                    return redirect(f"/doc/{m['id']}/edit?saved=1")
                return redirect(f"/doc/{m['id']}?submitted=1")
            if self.qs.get("form"):
                fd = app.form_get(user, self.qs["form"])
                return self.ok("기안", f"<h2>새 기안 · {esc(fd['name'])}</h2>"
                               + self.doc_form("/new", {"form": {"code": fd["code"], "version": fd["version"]}}))
            if "type" not in self.qs or not app.freeform_allowed():
                from .web_ui import form_chooser
                return form_chooser(self, app, user)
            t = self.qs.get("type") if self.qs.get("type") in pol.DOC_TYPES else "general"
            return self.ok("기안", f"<h2>새 기안 · {pol.DOC_TYPES[t]}</h2>" + self.doc_form("/new", {"doc_type": t}))

        def r_doc(self, user, did, act, sub):
            if act == "file" and sub and self.method == "GET":
                name, path = app.get_attachment(user, did, int(sub))
                data = path.read_bytes()
                q = urllib.parse.quote(name)
                return Resp(200, data, {"Content-Type": "application/octet-stream",
                                        "Content-Disposition": f"attachment; filename=\"download\"; filename*=UTF-8''{q}"})
            if act == "edit":
                if self.method == "POST":
                    f = self.form
                    try:
                        m0 = app.show(user, did, mark_read=False)
                        fd = self._fd(m0) if m0.get("form") else None
                        app.edit_draft(user, did, f.get("title"), f.get("body", ""), self._split(f.get("line")),
                                       self._split(f.get("cc")), "dept" if f.get("dept_open") else "private",
                                       self.files, [int(x) for x in self.form_list("remove") if x.isdigit()],
                                       doc_type=f.get("doc_type") or "general", amount=f.get("amount") or "",
                                       fields=self._fields(fd) if fd else None)
                        if f.get("go") == "submit":
                            app.submit(user, did)
                    except core.Invalid as e:
                        m = app.show(user, did, mark_read=False)
                        return Resp(400, self.page("수정", f"<h2>기안 수정</h2>"
                                                         f"{self.doc_form(f'/doc/{did}/edit', self._form_doc(m), str(e))}"))
                    if f.get("go") == "submit":
                        return redirect(f"/doc/{did}?submitted=1")
                    return redirect(f"/doc/{did}/edit?saved=1")
                m = app.show(user, did, mark_read=False)
                if not m["can"]["edit"]:
                    raise core.Forbidden("수정할 수 없는 문서입니다")
                note = "<p class='banner ok' role=status>임시저장했습니다</p>" if self.qs.get("saved") else ""
                return self.ok("수정", f"{note}<h2>기안 수정 #{did}</h2>" + self.doc_form(f"/doc/{did}/edit", m))
            if act == "print" and self.method == "GET":
                from .web_ui import print_view
                return print_view(self, app, user, app.show(user, did, mark_read=False))
            if act == "confirm" and self.method == "POST":
                from .web_ui import confirm_page, doc_view
                m = app.show(user, did, mark_read=False)
                kind = self.form.get("act")
                c = self.form.get("comment", "").strip()
                if kind not in ("approve", "jeon", "reject") or not m["can"]["approve"]:
                    raise core.Forbidden("지금 결재할 수 있는 문서가 아닙니다(이미 처리되었을 수 있음)")
                if kind == "reject" and len(c) < core.REJECT_MIN:
                    return Resp(400, doc_view(self, app, user, m, f"반려 사유를 {core.REJECT_MIN}자 이상 적어 주세요", c).body)
                return confirm_page(self, m, kind, c, kind == "jeon")
            if act and self.method == "POST":
                c = self.form.get("comment", "")
                rev = self.form.get("rev")
                rev = int(rev) if rev and rev.isdigit() else -1  # 웹은 rev 필수(없으면 core가 권한 확인 뒤 거부)
                if act in ("approve", "reject"):
                    app.show(user, did, mark_read=False)  # 권한 먼저(없으면 404)
                if act in ("approve", "reject") and self.form.get("confirm") != "1":
                    raise core.Invalid("확인 단계를 거쳐야 합니다. 문서를 다시 여세요")
                if act == "approve":
                    app.approve(user, did, c, jeongyeol=bool(self.form.get("jeon")), rev=rev)
                    return redirect(f"/doc/{did}?done=approve")
                elif act == "reject":
                    app.reject(user, did, c, rev=rev)
                    return redirect(f"/doc/{did}?done=reject")
                elif act == "withdraw":
                    app.withdraw(user, did)
                elif act == "submit":
                    app.submit(user, did)
                else:
                    raise core.NotFound("없는 동작")
                return redirect(f"/doc/{did}")
            if act:
                raise core.NotFound("없는 동작")
            m = app.show(user, did)
            from .web_ui import doc_view
            return doc_view(self, app, user, m)

        def form_list(self, name):
            v = self.form.get(name)
            if v is None:
                return []
            return v if isinstance(v, list) else [v]

        def r_search(self, user):
            q = self.qs
            rows, shown = [], any(q.get(k) for k in ("q", "title", "drafter", "dept", "status", "from", "to", "number"))
            if shown:
                rows = app.search(user, q=q.get("q"), title=q.get("title"), drafter=q.get("drafter"), dept=q.get("dept"),
                                  status=q.get("status") or None, date_from=q.get("from") or None,
                                  date_to=q.get("to") or None, number=q.get("number"))
            sts = "".join(f"<option value='{k}' {'selected' if q.get('status') == k else ''}>{v}</option>"
                          for k, v in core.STATUS_KO.items())
            body = (f"<h2>검색</h2><form method=get action='/search' class=card><div class=row>"
                    f"<div><label>본문·제목</label><input type=text name=q value='{esc(q.get('q'))}'></div>"
                    f"<div><label>제목</label><input type=text name=title value='{esc(q.get('title'))}'></div>"
                    f"<div><label>기안자</label><input type=text name=drafter value='{esc(q.get('drafter'))}'></div></div>"
                    f"<div class=row><div><label>부서</label><input type=text name=dept value='{esc(q.get('dept'))}'></div>"
                    f"<div><label>상태</label><select name=status><option value=''>전체</option>{sts}</select></div>"
                    f"<div><label>문서번호</label><input type=text name=number value='{esc(q.get('number'))}'></div></div>"
                    f"<div class=row><div><label>작성일 부터</label><input type=date name=from value='{esc(q.get('from'))}'></div>"
                    f"<div><label>까지</label><input type=date name=to value='{esc(q.get('to'))}'></div></div>"
                    f"<p><button>검색</button></p></form>"
                    + (f"<div class=card>{self.table(rows)}</div>" if shown else ""))
            return self.ok("검색", body)

        def r_msg(self, user, sub):
            if sub == "send" and self.method == "POST":
                app.msg_send(user, self.form.get("to", "").strip(), self.form.get("body", ""))
                return redirect("/msg?sent=1")
            if sub and sub.isdigit():
                r = app.msg_read(user, int(sub))
                link = f"<p><a href='/doc/{r['doc_id']}'>문서 #{r['doc_id']} 열기</a></p>" if r.get("doc_id") else ""
                if r["sender"] not in ("system", user):
                    link += (f"<form method=post action='/msg/send' class=card>{self.csrf()}<input type=hidden name=to "
                             f"value='{esc(r['sender'])}'><label>바로 답장</label><textarea name=body required "
                             f"class=short maxlength=5000></textarea><p><button>보내기</button></p></form>")
                return self.ok("쪽지", f"<div class=card><div class=mut>{esc(r['sender'])} → {esc(r['recipient'])} "
                                       f"{esc(r['created'][:16])}</div><pre class=body>{esc(r['body'])}</pre>{link}</div>"
                                       f"<a href='/msg'>목록</a>")
            sent = self.qs.get("box") == "sent"
            rows = app.msg_list(user, sent=sent)
            tr = "".join(f"<tr><td>{'●' if not r['read_at'] and not sent else ''}</td><td>{esc(r['recipient'] if sent else r['sender'])}"
                         f"</td><td><a href='/msg/{r['id']}'>{esc(r['body'][:80])}</a></td>"
                         f"<td class=hide>{esc(r['created'][5:16])}</td></tr>" for r in rows)
            body = (f"<h2>쪽지</h2><p><a href='/msg'>받은쪽지</a> · <a href='/msg?box=sent'>보낸쪽지</a></p>"
                    f"{'<p class=ok>보냈습니다</p>' if self.qs.get('sent') else ''}"
                    f"<div class=card><table>{tr or '<tr><td class=mut>(없음)</td></tr>'}</table></div>"
                    f"<form method=post action='/msg/send' class=card>{self.csrf()}<label>받는 사람</label>"
                    f"<input type=text name=to list=users required><label>내용</label><textarea name=body required "
                    f"class=short></textarea><p><button>보내기</button></p></form>{self.user_datalist()}")
            return self.ok("쪽지", body)

        def r_board(self, user, board, sub):
            tabs = " · ".join(f"<a href='/board/{b}'>{n}</a>" for b, n in BOARDS.items())
            if sub == "new" and self.method == "POST":
                r = app.board_post(user, board, self.form.get("title"), self.form.get("body", ""))
                return redirect(f"/board/{board}/{r['id']}")
            if sub == "pin" and self.method == "POST":
                app.board_pin(user, int(self.form.get("id", "0") or 0), self.form.get("on") == "1")
                return redirect(f"/board/{board}")
            if sub == "del" and self.method == "POST":
                app.board_delete(user, int(self.form.get("id", "0") or 0))
                return redirect(f"/board/{board}")
            if sub and sub.isdigit():
                r = app.board_read(user, int(sub))
                if r["board"] != board:
                    raise core.NotFound("게시글 없음")
                dl = ""
                if r["author"] == user or self.me["role"] == "admin":
                    dl = (f"<form method=post action='/board/{board}/del' class=inline>{self.csrf()}"
                          f"<input type=hidden name=id value='{r['id']}'><button class=sec>삭제</button></form>")
                if self.me["role"] == "admin" and board == "notice":
                    dl += (f" <form method=post action='/board/{board}/pin' class=inline>{self.csrf()}"
                           f"<input type=hidden name=id value='{r['id']}'><input type=hidden name=on "
                           f"value='{0 if r.get('pinned') else 1}'><button class=sec>"
                           f"{'고정 해제' if r.get('pinned') else '📌 고정'}</button></form>")
                return self.ok(r["title"], f"<p>{tabs}</p><div class=card><h2>{esc(r['title'])}</h2><div class=mut>"
                                           f"{esc(r['author_name'])} {esc(r['created'][:16])}</div><pre class=body>"
                                           f"{esc(r['body'])}</pre></div>{dl}")
            rows = app.board_list(user, board)
            tr = "".join(f"<tr><td>{'📌' if r.get('pinned') else r['id']}</td><td><a href='/board/{board}/{r['id']}'>{esc(r['title'])}</a></td>"
                         f"<td>{esc(r['author_name'])}</td><td class=hide>{esc(r['created'][:10])}</td></tr>" for r in rows)
            can = board != "notice" or self.me["role"] == "admin" or self.me["is_head"]
            form = (f"<form method=post action='/board/{board}/new' class=card>{self.csrf()}<label>제목</label>"
                    f"<input type=text name=title required><label>내용</label><textarea name=body></textarea>"
                    f"<p><button>올리기</button></p></form>") if can else ""
            return self.ok(BOARDS[board], f"<p>{tabs}</p><h2>{BOARDS[board]}</h2><div class=card><table>"
                                          f"{tr or '<tr><td class=mut>(없음)</td></tr>'}</table></div>{form}")

        def r_delegate(self, user):
            if self.method == "POST":
                if self.form.get("revoke"):
                    app.delegate_revoke(user, int(self.form["revoke"]))
                else:
                    app.delegate_set(user, self.form.get("to", "").strip(), self.form.get("start") or None,
                                     self.form.get("end") or None)
                return redirect("/delegate")
            rows = app.delegate_list(user)
            tr = "".join(
                f"<tr><td>{esc(r['from_user'])} → {esc(r['to_user'])}</td><td>{esc(r['start'])}~{esc(r['end'])}</td>"
                f"<td>{'활성' if r['active'] else ('취소' if r['revoked'] else '-')}</td><td>"
                + (f"<form method=post action='/delegate' class=inline>{self.csrf()}<input type=hidden name=revoke "
                   f"value='{r['id']}'><button class=sec>취소</button></form>" if r['from_user'] == user and not r['revoked'] else "")
                + "</td></tr>" for r in rows)
            body = (f"<h2>대결(결재 위임)</h2><div class=card><table>{tr or '<tr><td class=mut>(없음)</td></tr>'}</table></div>"
                    f"<form method=post action='/delegate' class=card>{self.csrf()}<label>대결자(받는 사람)</label>"
                    f"<input type=text name=to list=users required><div class=row><div><label>시작</label>"
                    f"<input type=date name=start></div><div><label>종료</label><input type=date name=end></div></div>"
                    f"<p><button>위임</button></p></form>{self.user_datalist()}"
                    f"<p><a href='/me'>내 비밀번호 변경</a></p>")
            return self.ok("대결", body)

        def r_org(self, user):
            q = self.qs.get("q", "")
            rows = app.org_directory(user, q=q or None, dept=self.qs.get("dept") or None)
            tr = "".join(f"<tr><td>{esc(r['dept_name'] or r['dept'])}</td><td>{esc(r['position_name'] or '-')}</td>"
                         f"<td>{esc(r['name'])} <span class=mut>{esc(r['username'])}</span></td>"
                         f"<td class=hide>{esc(r['title'] or '')}{' · ' + esc(r['email']) if r.get('email') else ''}"
                         f"{' · ' + esc(r['phone']) if r.get('phone') else ''}</td></tr>" for r in rows)
            return self.ok("조직도", f"<h2>조직도</h2><form method=get action='/org' class=card><div class=row>"
                                     f"<input type=text name=q placeholder='이름·부서·직위' value='{esc(q)}'>"
                                     f"<button>찾기</button></div></form><div class=card><table><tr><th>부서</th><th>직위</th>"
                                     f"<th>이름</th><th class=hide>직책/연락처</th></tr>{tr}</table></div>")

        def r_preview(self, user):
            t, a = self.qs.get("type") or "general", self.qs.get("amount") or None
            types = "".join(f"<option value='{k}' {'selected' if t == k else ''}>{v}</option>" for k, v in pol.DOC_TYPES.items())
            res = ""
            if self.qs.get("type"):
                p = app.preview_line(user, t, a)
                res = "<div class=card>" + (esc(p["note"]) if p["free"] else (
                    f"<p class=mut>{esc(p['dept'])} 결재규정 v{p['policy_version']}</p><div class=line>" + "".join(
                        f"<div class=step>{esc(x['name'])} <span class=mut>{esc(x['position'] or '')}</span>"
                        f"{' <b>전결</b>' if x['final'] else ''}</div>" for x in p["line"]) + "</div>"
                    + (f"<p>필수 참조: {esc(', '.join(p['cc']))}</p>" if p["cc"] else "")
                    + f"<p class=mut>추가 결재자 {'허용' if p['allow_extra'] else '금지'}</p>")) + "</div>"
            return self.ok("결재선 미리보기", f"<h2>내 결재선 미리보기</h2><form method=get action='/preview' class=card>"
                                         f"<div class=row><select name=type>{types}</select><input type=text name=amount "
                                         f"placeholder='금액(지출)' value='{esc(a or '')}'><button>보기</button></div></form>{res}")

        def r_me(self, user):
            msg = ""
            if self.method == "POST":
                if self.form.get("new1") != self.form.get("new2"):
                    raise core.Invalid("새 비밀번호 확인 불일치")
                app.set_password(user, user, self.form.get("new1", ""), self.form.get("old", ""))
                r = redirect("/login")
                r.headers["Set-Cookie"] = f"{COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict"
                return r
            if self.sess.get("must_change"):
                msg = "<p class=err>임시 비밀번호입니다. 새 비밀번호로 바꿔야 계속 사용할 수 있습니다.</p>"
            body = (f"<h2>비밀번호 변경</h2><form method=post action='/me' class=card>{self.csrf()}{msg}"
                    f"<label>현재</label><input type=password name=old><label>새 비밀번호(8자+)</label>"
                    f"<input type=password name=new1><label>확인</label><input type=password name=new2>"
                    f"<p><button>변경</button></p></form>")
            return self.ok("내 정보", body)

    return H


def parse_multipart(data: bytes, ctype: str):
    m = re.search(r'boundary="?([^";]+)"?', ctype)
    if not m:
        raise core.Invalid("multipart boundary 없음")
    delim = b"\r\n--" + m.group(1).encode("latin-1")
    form, files = {}, []
    parts = (b"\r\n" + data).split(delim)
    for part in parts[1:]:
        if part.startswith(b"--"):
            break
        if part.startswith(b"\r\n"):
            part = part[2:]
        head, _, body = part.partition(b"\r\n\r\n")
        hdr = head.decode("utf-8", "replace")
        cd = next((ln for ln in hdr.split("\r\n") if ln.lower().startswith("content-disposition")), "")
        nm = re.search(r'\bname="([^"]*)"', cd)
        fn = re.search(r'\bfilename="([^"]*)"', cd)
        if not nm:
            continue
        name = nm.group(1)
        if fn is not None:
            if fn.group(1) and body:
                files.append((fn.group(1), body))
            continue
        val = body.decode("utf-8", "replace")
        if name == "remove":
            form.setdefault(name, []).append(val)
        else:
            form.setdefault(name, val)
    return form, files


def make_server(app: App, host="127.0.0.1", port=8130):
    class Srv(ThreadingHTTPServer):
        request_queue_size = 64
    srv = Srv((host, port), make_handler(app))
    srv.daemon_threads = True
    return srv


def serve(app: App, host="127.0.0.1", port=8130, https=None):
    global SECURE_COOKIE
    srv = make_server(app, host, port)
    app.start_mail_worker()  # 외부 SMTP 대기열(설정 꺼져 있으면 아무 것도 안 함)
    app.start_backup_worker()  # server.json 의 backup_dir 이 있으면 하루 1번 자동 백업
    scheme = "http"
    if https:
        import ssl
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(https[0], https[1])
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True, do_handshake_on_connect=False)
        SECURE_COOKIE = True
        scheme = "https"
    if host not in ("127.0.0.1", "localhost", "::1") and not https and not TRUST_PROXY:
        print(f"경고: {host} 로 HTTPS 없이 공개합니다. 사내 LAN 전용으로만 쓰고, 외부 공개는 HTTPS 리버스 프록시"
              f"(GYEOLJAE_TRUST_PROXY=1, GYEOLJAE_COOKIE_SECURE=1) 또는 --https 를 쓰세요.", file=sys.stderr, flush=True)
    print(f"gyeoljae web: {scheme}://{host}:{port}/  data={app.dir}  fts={'trigram' if app.fts else 'LIKE 폴백'}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
