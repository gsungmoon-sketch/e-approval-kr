"""OnlyOffice 화면·문서 서버 접점(web). 로직은 onlyoffice.OnlyOfficeMixin(App) — 여기는 호출과 그리기만.

경로:
  /oo/edit/d/<문서>/<첨부>?mode=view|edit  · /oo/edit/l/<자료>?mode=..&v=<판>   (로그인)
  /oo/dl/<토큰>  (GET, 문서 서버 → 우리, 세션 없음 — 서명 토큰이 곧 권한, 한 판 읽기 전용)
  /oo/cb/<토큰>  (POST, 문서 서버 콜백, 세션·CSRF 없음 — 문서 서버 JWT + 우리 콜백 토큰)
  /admin/onlyoffice (관리자 설정)
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.parse

from . import core

MAX_CB_BODY = 256 * 1024


def editor_csp(origin):
    """편집기 화면에만: 문서 서버 출처를 script-src·frame-src 에(기능이 켜져 있을 때만 이 화면이 있음)."""
    return ("default-src 'none'; style-src 'self'; img-src 'self'; form-action 'none'; frame-ancestors 'none'; "
            f"base-uri 'none'; script-src 'self' {origin}; frame-src {origin}; connect-src 'none'")


def _cfg_json(cfg):
    """<script type=application/json> 안에 안전하게(</script>·<!-- 탈출 방지)."""
    return json.dumps(cfg, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace(
        "&", "\\u0026").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def public_route(h, app):
    """세션·CSRF 이전에 처리하는 문서 서버 전용 경로. 해당 없으면 None."""
    from .web import Resp
    p = h.route
    m = re.fullmatch(r"/oo/dl/([A-Za-z0-9_.-]{20,4096})", p)
    if m and h.method == "GET":
        try:
            name, data = app.oo_download(m.group(1))
        except core.GyeolError:
            return Resp(404, b"not found", {"Content-Type": "text/plain"})
        return Resp(200, data, {"Content-Type": "application/octet-stream", "Cache-Control": "no-store",
                                "Content-Disposition": "attachment; filename=\"download\"; filename*=UTF-8''"
                                + urllib.parse.quote(name)})
    m = re.fullmatch(r"/oo/cb/([A-Za-z0-9_.-]{20,4096})", p)
    if m and h.method == "POST":
        try:
            app.oo_check_cb(m.group(1))  # 본문을 읽기 전에 주소 토큰부터
        except core.GyeolError:
            r = _json(Resp, 403, {"error": 1})
            r.headers["Connection"] = "close"
            return r
        try:
            n = int(h.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n < 0 or n > MAX_CB_BODY:
            r = _json(Resp, 413, {"error": 1})
            r.headers["Connection"] = "close"
            return r
        raw = h.rfile.read(n) if n else b""
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError:
            return _json(Resp, 400, {"error": 1})
        hdr = app._oo_conf()["jwt_header"]
        try:
            r = app.oo_callback(m.group(1), body if isinstance(body, dict) else {}, h.headers.get(hdr, ""))
        except core.GyeolError as e:
            sys.stderr.write(f"oo callback refused: {type(e).__name__}: {e}\n")
            return _json(Resp, 403 if isinstance(e, core.Forbidden) else 200, {"error": 1})
        except OSError as e:  # 결과 받기 실패(시간 초과 등) — 문서 서버가 다시 시도
            sys.stderr.write(f"oo callback fetch failed: {e}\n")
            return _json(Resp, 200, {"error": 1})
        return _json(Resp, 200, {"error": r.get("error", 0)})
    if p.startswith("/oo/dl/") or p.startswith("/oo/cb/"):
        return Resp(404, b"not found", {"Content-Type": "text/plain"})
    return None


def _json(Resp, status, obj):
    return Resp(status, json.dumps(obj).encode(), {"Content-Type": "application/json"})


def oo_buttons(app, user, kind, ident, n=None, name="", url_extra=""):
    """첨부·자료실 옆 '보기'/'편집' 링크 HTML(꺼져 있거나 형식이 아니면 빈 문자열)."""
    from .onlyoffice import OO_EXT
    if os.path.splitext(name or "")[1].lower() not in OO_EXT:
        return ""
    view, edit = app.oo_can(user, kind, ident, n)
    if not view:
        return ""
    base = f"/oo/edit/{kind}/{int(ident)}" + (f"/{int(n)}" if kind == "d" else "")
    out = f" <a class='btn sec' href='{base}?mode=view{url_extra}' target=_blank rel=noopener>보기</a>"
    if edit:
        out += f" <a class='btn sec' href='{base}?mode=edit{url_extra}' target=_blank rel=noopener>편집</a>"
    return out


def route(h, app, user, p):
    """로그인한 사용자의 /oo/edit/... 화면. 해당 없으면 None."""
    from .web import Resp, esc
    m = re.fullmatch(r"/oo/edit/(d)/(\d{1,9})/(\d{1,4})|/oo/edit/(l)/(\d{1,9})", p)
    if not m or h.method != "GET":
        return None
    if m.group(1):
        kind, ident, n = "d", int(m.group(2)), int(m.group(3))
    else:
        v = h.qs.get("v", "")
        kind, ident, n = "l", int(m.group(5)), int(v) if v.isdigit() and len(v) <= 6 else None
    mode = "edit" if h.qs.get("mode") == "edit" else "view"
    r = app.oo_editor_config(user, kind, ident, n, mode)
    h._csp = ("oo", r["origin"])
    back = f"/doc/{ident}" if kind == "d" else f"/files/{ident}"
    doc = (f"<!doctype html><html lang=ko><head><meta charset=utf-8>"
           f"<meta name=viewport content='width=device-width,initial-scale=1'><title>{esc(r['name'])} - 결재</title>"
           f"<link rel=stylesheet href='/s.css?v=12'></head><body class=oo>"
           f"<header class=oo-bar><a href='{back}'>← 돌아가기</a> <b>{esc(r['name'])}</b> "
           f"<span class=mut>{'편집' if r['mode'] == 'edit' else '보기'}</span>"
           f"{' <span class=mut>· 창을 닫으면 잠시 뒤 새 판으로 저장됩니다</span>' if r['mode'] == 'edit' else ''}"
           f" <span id=oo-msg role=status class=err></span></header>"
           f"<div class=oo-wrap><div id=oo-editor></div></div>"
           f"<script type=application/json id=oo-cfg>{_cfg_json(r['config'])}</script>"
           f"<script src='{esc(r['api_js'])}'></script><script src='/static/oo-editor.js?v=1'></script></body></html>")
    return Resp(200, doc.encode("utf-8"))


def admin_page(h, app, user, ok):
    from .web import esc, redirect
    f = h.form
    note = ""
    if h.method == "POST":
        try:
            app.oo_set(user, enabled=f.get("enabled") == "1", docs_url=f.get("docs_url", ""),
                       docs_internal_url=f.get("docs_internal_url", ""), app_internal_url=f.get("app_internal_url", ""),
                       jwt_secret=f.get("jwt_secret", ""), jwt_header=f.get("jwt_header", "") or "Authorization")
            return redirect("/admin/onlyoffice")
        except core.Invalid as e:
            note = f"<p class=err>{esc(e)}</p>"
    d = app.oo_get(user)
    c = h.csrf()
    body = (f"{note}<div class=card><p class=mut>OnlyOffice Document Server 를 따로 운영하면 첨부·자료실의 docx·xlsx·pptx 를 "
            f"브라우저에서 보고 편집할 수 있습니다(기본 꺼짐). 편집은 새 판을 올릴 수 있는 사람만, 진행·완료 문서의 첨부는 보기만. "
            f"JWT 비밀은 문서 서버의 JWT_SECRET 과 같아야 하며 데이터 폴더의 onlyoffice.json 에 저장됩니다.</p></div>"
            f"<form method=post action='/admin/onlyoffice' class=card>{c}"
            f"<label>문서 서버 주소(브라우저가 여는 주소)</label><input type=text name=docs_url value='{esc(d['docs_url'])}' "
            f"placeholder='https://docs.example.com'>"
            f"<label>문서 서버 내부 주소 <span class=mut>(결재 서버가 결과를 받을 주소, 같으면 비움)</span></label>"
            f"<input type=text name=docs_internal_url value='{esc(d['docs_internal_url'])}'>"
            f"<label>결재 서버 내부 주소 <span class=mut>(문서 서버가 파일을 받고 콜백할 주소)</span></label>"
            f"<input type=text name=app_internal_url value='{esc(d['app_internal_url'])}' placeholder='http://gyeoljae.internal:8130'>"
            f"<div class=row><div><label>JWT 비밀 <span class=mut>(비우면 유지, 32자 이상)</span></label>"
            f"<input type=password name=jwt_secret value='' autocomplete=new-password></div>"
            f"<div><label>JWT 헤더</label><input type=text name=jwt_header value='{esc(d['jwt_header'])}'></div></div>"
            f"<label><input type=checkbox name=enabled value=1 {'checked' if d['enabled'] else ''}> 사용</label>"
            f"<p><button>저장</button></p></form>")
    return ok("OnlyOffice", body)
