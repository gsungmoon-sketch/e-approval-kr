"""OnlyOffice Document Server(Docs API) 연동: 첨부·자료실의 docx/xlsx/pptx 를 브라우저에서 보기·편집. App mixin.

선택 기능(기본 꺼짐). 설정은 데이터 폴더 onlyoffice.json(0600, smtp.json 과 같은 방식). 표준 라이브러리만:
JWT HS256 은 hmac/hashlib 로 직접(alg 는 HS256 만 — none·RS256 등 거부).

흐름(docs/ONLYOFFICE.md):
  1. 사용자가 보기/편집 → 우리가 편집기 설정(config)을 만들고 JWT 서명. document.key = 종류+id+판+sha 앞자리.
  2. 문서 서버가 document.url(짧게 사는 서명 토큰 URL, 세션 쿠키 없음, 한 판 읽기 전용)로 파일을 받음.
  3. 편집 후 문서 서버가 callbackUrl(우리 서명 토큰 — key 묶임)로 POST, 본문/헤더 JWT 를 검증.
     상태 2/6 이면 결과 URL(설정한 문서 서버 출처만, 크기 상한·시간 제한, 리다이렉트 거부)에서 받아 새 판으로 저장.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

from .core_base import Forbidden, Invalid, NotFound, _atomic_write, _try_chmod

OO_EXT = {".docx": "word", ".xlsx": "cell", ".pptx": "slide"}
DL_TTL = 15 * 60            # 문서 서버 내려받기 토큰
CB_TTL = 7 * 24 * 3600      # 콜백 토큰(편집 세션이 길 수 있음 — 본 인증은 문서 서버 JWT)
CFG_TTL = 8 * 3600          # 편집기 설정 JWT
FETCH_TIMEOUT = 30
OO_DEFAULT = {"enabled": False, "docs_url": "", "docs_internal_url": "", "app_internal_url": "",
              "jwt_secret": "", "jwt_header": "Authorization", "link_key": ""}
KEY_RE = re.compile(r"gj([ev])([dl])(\d{1,9})x(\d{1,4})x([0-9a-f]{16})x([0-9a-f]{16})")
STREAM_DEADLINE = 120   # 결과 받기 전체 시간 상한(느린 전송)
MASK = "********"


class JWTError(ValueError):
    pass


# ------------------------------------------------------------------ JWT HS256
def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _b64d(s: str) -> bytes:
    if not re.fullmatch(r"[A-Za-z0-9_-]*", s or ""):
        raise JWTError("bad base64url")
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def jwt_sign(payload: dict, secret: str, ttl: int | None = None, now: float | None = None) -> str:
    if not secret:
        raise JWTError("no secret")
    p = dict(payload)
    if ttl:
        t = int(now if now is not None else time.time())
        p.setdefault("iat", t)
        p["exp"] = t + int(ttl)
    h = _b64e(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    b = _b64e(json.dumps(p, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    sig = hmac.new(secret.encode("utf-8"), f"{h}.{b}".encode("ascii"), hashlib.sha256).digest()
    return f"{h}.{b}.{_b64e(sig)}"


def jwt_verify(token: str, secret: str, now: float | None = None, leeway: int = 60, require_exp=False) -> dict:
    """HS256 만. 서명 → 만료 순으로 검사. 실패는 JWTError."""
    if not secret:
        raise JWTError("no secret")
    if not isinstance(token, str) or len(token) > 65536:
        raise JWTError("bad token")
    try:
        return _jwt_verify(token, secret, now, leeway, require_exp)
    except JWTError:
        raise
    except (ValueError, TypeError, binascii.Error, UnicodeError):  # 깨진 토큰은 모두 JWTError
        raise JWTError("bad token")


def _jwt_verify(token, secret, now, leeway, require_exp):
    parts = token.split(".")
    if any(not re.fullmatch(r"[A-Za-z0-9_-]*", x) for x in parts):
        raise JWTError("bad token")
    if len(parts) != 3 or not parts[2]:
        raise JWTError("bad token")
    try:
        header = json.loads(_b64d(parts[0]))
    except (ValueError, UnicodeDecodeError):
        raise JWTError("bad header")
    if not isinstance(header, dict) or header.get("alg") != "HS256" or "crit" in header:
        raise JWTError("alg")  # none·HS512·RS256(공개키를 비밀로 쓰는 혼동) 모두 거부
    want = hmac.new(secret.encode("utf-8"), f"{parts[0]}.{parts[1]}".encode("ascii"), hashlib.sha256).digest()
    if not hmac.compare_digest(want, _b64d(parts[2])):
        raise JWTError("signature")
    try:
        p = json.loads(_b64d(parts[1]))
    except (ValueError, UnicodeDecodeError):
        raise JWTError("bad payload")
    if not isinstance(p, dict):
        raise JWTError("bad payload")
    t = now if now is not None else time.time()
    exp = p.get("exp")
    if exp is None:
        if require_exp:
            raise JWTError("no exp")
    elif not isinstance(exp, (int, float)) or isinstance(exp, bool) or t > exp + leeway:
        raise JWTError("expired")
    nbf = p.get("nbf")
    if nbf is not None and (not isinstance(nbf, (int, float)) or t + leeway < nbf):
        raise JWTError("not yet")
    return p


# ------------------------------------------------------------------ URL helpers
def _origin(url: str):
    """(scheme, host, port) — 사용자정보·이상한 스킴은 None."""
    try:
        u = urllib.parse.urlsplit(url or "")
        port = u.port
    except ValueError:
        return None
    if u.scheme not in ("http", "https") or not u.hostname or u.username or u.password or "@" in u.netloc:
        return None
    if not re.fullmatch(r"(?:[A-Za-z0-9.-]+|\[[0-9A-Fa-f:.]+\])(?::\d{1,5})?", u.netloc):  # CSP·헤더에 들어감
        return None
    return u.scheme, u.hostname.lower(), port or (443 if u.scheme == "https" else 80)


def _norm_base(url: str, what: str) -> str:
    url = (url or "").strip().rstrip("/")
    if not url:
        return ""
    if len(url) > 300 or _origin(url) is None or any(ch in url for ch in "\"'<> \\\t\r\n"):
        raise Invalid(f"{what}: http(s)://호스트[:포트][/경로] 형식이어야 합니다")
    u = urllib.parse.urlsplit(url)
    if u.query or u.fragment:
        raise Invalid(f"{what}: 쿼리·조각(#) 없이")
    return url


def origin_str(url: str) -> str:
    o = _origin(url)
    if not o:
        return ""
    default = 443 if o[0] == "https" else 80
    host = f"[{o[1]}]" if ":" in o[1] else o[1]
    return f"{o[0]}://{host}" + ("" if o[2] == default else f":{o[2]}")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "redirect refused", headers, fp)


def fetch_limited(url: str, allowed_origins, max_bytes: int, timeout=FETCH_TIMEOUT) -> bytes:
    """allowed_origins 출처만, 리다이렉트·프록시 없이, max_bytes 넘으면 거부."""
    o = _origin(url)
    if o is None or o not in {x for x in allowed_origins if x}:
        raise Forbidden("허용되지 않은 결과 주소")
    opener = urllib.request.build_opener(_NoRedirect, urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, headers={"User-Agent": "gyeoljae-oo"})
    with opener.open(req, timeout=timeout) as r:
        n = r.headers.get("Content-Length")
        if n and n.isdigit() and int(n) > max_bytes:
            raise Invalid("결과 파일이 너무 큽니다")
        buf, end = bytearray(), time.monotonic() + STREAM_DEADLINE
        while len(buf) <= max_bytes:
            chunk = r.read(min(65536, max_bytes + 1 - len(buf)))
            if not chunk:
                break
            buf += chunk
            if time.monotonic() > end:
                raise Invalid("결과 받기 시간 초과")
        data = bytes(buf)
    if len(data) > max_bytes:
        raise Invalid("결과 파일이 너무 큽니다")
    return data


def make_key(link_key, mode, kind, ident, n, sha, epoch=""):
    """문서 키 = 모드(e 편집/v 보기 — 서로 다른 세션) + 종류 + id + 번호/판 + sha 앞 16 + 우리 HMAC 16.
    HMAC(link_key) 이라 문서 서버를 같이 쓰는 다른 앱이 키를 짐작해 세션에 끼어들 수 없다.
    epoch(첨부 = 문서 rev) 가 바뀌면(상신·수정) 키도 바뀌어 옛 편집 세션은 버려진다."""
    mac = hmac.new(link_key.encode(), f"{mode}|{kind}|{int(ident)}|{int(n)}|{sha}|{epoch}".encode(),
                   hashlib.sha256).hexdigest()[:16]
    return f"gj{mode}{kind}{int(ident)}x{int(n)}x{sha[:16]}x{mac}"


def parse_key(key):
    """(모드, 종류, id, 번호/판, sha 앞 16)."""
    m = KEY_RE.fullmatch(key or "") if isinstance(key, str) else None
    if not m:
        raise NotFound("모르는 문서 키")
    return m.group(1), m.group(2), int(m.group(3)), int(m.group(4)), m.group(5)


# ------------------------------------------------------------------ mixin
class OnlyOfficeMixin:
    def _oo_path(self):
        return self.dir / "onlyoffice.json"

    def _oo_conf(self):
        try:
            d = json.loads(self._oo_path().read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            d = {}
        return dict(OO_DEFAULT, **{k: v for k, v in d.items() if k in OO_DEFAULT})

    def oo_enabled(self):
        d = self._oo_conf()
        return bool(d["enabled"] and d["docs_url"] and d["jwt_secret"] and d["app_internal_url"] and d["link_key"])

    def oo_public(self):
        """웹 화면용(비밀 없음): 켜짐 여부와 문서 서버 출처."""
        d = self._oo_conf()
        on = self.oo_enabled()
        return {"enabled": on, "docs_url": d["docs_url"] if on else "", "origin": origin_str(d["docs_url"]) if on else ""}

    def oo_get(self, actor):
        self._require_admin(actor)
        d = self._oo_conf()
        d["jwt_secret"] = MASK if d["jwt_secret"] else ""
        d.pop("link_key")
        return d

    def oo_set(self, actor, **kw):
        self._require_admin(actor)
        d = self._oo_conf()
        for k, v in kw.items():
            if k not in OO_DEFAULT or k == "link_key" or v is None:
                continue
            if k == "jwt_secret" and v in (MASK, ""):
                continue  # 빈 값/가림표 = 기존 유지
            d[k] = v
        d["enabled"] = d["enabled"] if isinstance(d["enabled"], bool) else str(d["enabled"]).lower() in (
            "1", "on", "true", "yes")
        d["docs_url"] = _norm_base(d["docs_url"], "문서 서버 주소")
        d["docs_internal_url"] = _norm_base(d["docs_internal_url"], "문서 서버 내부 주소")
        d["app_internal_url"] = _norm_base(d["app_internal_url"], "결재 서버 내부 주소")
        d["jwt_header"] = str(d["jwt_header"] or "Authorization").strip()
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,40}", d["jwt_header"]):
            raise Invalid("JWT 헤더 이름 형식 오류")
        s = str(d["jwt_secret"] or "")
        if s and (len(s) < 32 or len(s) > 256 or any(ord(ch) < 33 or ord(ch) > 126 for ch in s)):
            raise Invalid("JWT 비밀은 32~256자 인쇄 가능 ASCII")
        if d["enabled"] and not (d["docs_url"] and d["app_internal_url"] and s):
            raise Invalid("켜려면 문서 서버 주소·결재 서버 내부 주소·JWT 비밀이 필요합니다")
        if not d["link_key"]:
            d["link_key"] = secrets.token_urlsafe(32)  # 우리 내려받기·콜백 토큰 서명용(문서 서버와 공유 안 함)
        _atomic_write(self._oo_path(), json.dumps(d, ensure_ascii=False).encode("utf-8"))
        _try_chmod(self._oo_path(), 0o600)
        with self._tx() as c:
            self._audit(c, actor, "oo_set", None, {k: v for k, v in d.items() if k not in ("jwt_secret", "link_key")})
        return self.oo_get(actor)

    # ---------------------------------------------------------- access
    def oo_access(self, actor, kind, ident, n=None):
        """보기·편집 권한과 대상 파일. kind d=문서 첨부(ident=문서, n=첨부 번호) / l=자료실(ident=파일, n=판, None=최신).
        편집 = 이미 새 판을 올릴 수 있는 사람만(첨부: 기안자·기안중/회수/반려, 자료실: 쓰기 권한), 완료·진행 문서는 보기만."""
        if kind == "d":
            name, path = self.get_attachment(actor, ident, n)  # 열람 권한(없으면 NotFound)
            with self._ro() as c:
                r = c.execute("select drafter,status from docs where id=?", (int(ident),)).fetchone()
            meta, _ = self._read_doc(ident)
            a = next(x for x in meta["attachments"] if x["idx"] == int(n))
            can_edit = bool(r) and r["drafter"] == actor and r["status"] in ("draft", "withdrawn", "rejected")
            out = {"name": name, "path": path, "sha256": a["sha256"], "n": int(n), "ver": a.get("ver", 1),
                   "epoch": str(meta.get("rev", 0))}
        elif kind == "l":
            with self._ro() as c:
                f = self._lib_file(c, actor, ident)
                _, wr = self._lib_perm(c, actor, f["scope"], f["dept"])
                if f["scope"] == "dept" and self._user(c, actor)["dept"] != f["dept"]:
                    wr = False
            ver = int(n) if n else f["cur_version"]
            name, path = self.files_get(actor, ident, ver)
            with self._ro() as c:
                v = c.execute("select sha256 from lib_versions where file_id=? and version=?", (f["id"], ver)).fetchone()
            can_edit = wr and ver == f["cur_version"]  # 옛 판은 보기만
            out = {"name": name, "path": path, "sha256": v["sha256"], "n": ver, "ver": ver, "epoch": ""}
        else:
            raise NotFound("없음")
        ext = os.path.splitext(out["name"])[1].lower()
        if ext not in OO_EXT:
            raise Invalid("OnlyOffice 로 열 수 없는 형식(docx/xlsx/pptx)")
        out.update(kind=kind, id=int(ident), ext=ext, can_view=True, can_edit=can_edit)
        return out

    def oo_can(self, actor, kind, ident, n=None):
        """버튼 표시용 (보기, 편집) — 예외 없이."""
        if not self.oo_enabled():
            return False, False
        try:
            a = self.oo_access(actor, kind, ident, n)
        except Exception:  # noqa: BLE001 — 표시 판단일 뿐
            return False, False
        return True, a["can_edit"]

    # ---------------------------------------------------------- editor config
    def oo_editor_config(self, actor, kind, ident, n=None, mode="view", lang="ko"):
        if not self.oo_enabled():
            raise NotFound("OnlyOffice 가 꺼져 있습니다")
        d = self._oo_conf()
        a = self.oo_access(actor, kind, ident, n)
        edit = mode == "edit"
        if edit and not a["can_edit"]:
            raise Forbidden("이 파일은 편집할 수 없습니다(보기만)")
        key = make_key(d["link_key"], "e" if edit else "v", kind, ident, a["n"], a["sha256"], a["epoch"])
        base = d["app_internal_url"]
        dl = jwt_sign({"t": "dl", "k": kind, "id": int(ident), "n": a["n"], "sha": a["sha256"], "u": actor},
                      d["link_key"], ttl=DL_TTL)
        u = self.get_user(actor)
        cfg = {"documentType": OO_EXT[a["ext"]],
               "document": {"fileType": a["ext"][1:], "key": key, "title": a["name"], "url": f"{base}/oo/dl/{dl}",
                            "permissions": {"edit": edit, "download": True, "print": True, "comment": edit,
                                            "review": edit}},
               "editorConfig": {"mode": "edit" if edit else "view", "lang": lang,
                                "user": {"id": actor, "name": u["name"]},
                                "customization": {"forcesave": True, "autosave": True}}}
        if edit:
            cb = jwt_sign({"t": "cb", "key": key, "u": actor}, d["link_key"], ttl=CB_TTL)
            cfg["editorConfig"]["callbackUrl"] = f"{base}/oo/cb/{cb}"
        cfg["token"] = jwt_sign(cfg, d["jwt_secret"], ttl=CFG_TTL)
        return {"config": cfg, "api_js": d["docs_url"] + "/web-apps/apps/api/documents/api.js",
                "origin": origin_str(d["docs_url"]), "name": a["name"], "can_edit": a["can_edit"], "mode": cfg[
                    "editorConfig"]["mode"]}

    # ---------------------------------------------------------- docs server → us
    def oo_download(self, token):
        """문서 서버의 파일 받기(세션 없음). 토큰이 정한 한 판만, 지금도 그 사용자가 볼 수 있을 때만."""
        if not self.oo_enabled():
            raise NotFound("없음")
        d = self._oo_conf()
        try:
            p = jwt_verify(token, d["link_key"], require_exp=True, leeway=0)
        except JWTError:
            raise NotFound("없음")
        if p.get("t") != "dl" or p.get("k") not in ("d", "l"):
            raise NotFound("없음")
        kind, ident, n, sha = p["k"], int(p["id"]), int(p["n"]), str(p.get("sha", ""))
        a = self.oo_access(p.get("u", ""), kind, ident, n)
        if kind == "l":
            if a["sha256"] != sha:
                raise NotFound("없음")
            return a["name"], a["path"].read_bytes()
        # 첨부: 지금 판이거나 이전 판(prev) 중 sha 가 같은 것
        meta, _ = self._read_doc(ident)
        att = next(x for x in meta["attachments"] if x["idx"] == n)
        files = (self._doc_dir(ident) / "files").resolve()
        for cand in [att] + list(att.get("prev", [])):
            if cand.get("sha256") == sha:
                pth = (files / cand["stored"]).resolve()
                if pth.parent != files:
                    break
                data = pth.read_bytes()
                if hashlib.sha256(data).hexdigest() != sha:
                    break
                return a["name"], data
        raise NotFound("없음")

    def _oo_verify_callback(self, d, body: dict, header_value: str):
        """문서 서버 JWT(본문 token 우선, 없으면 헤더 Bearer). 검증된 내용만 돌려줌(서명 밖 본문 필드는 버림)."""
        tok = body.get("token") if isinstance(body.get("token"), str) else None
        if not tok and header_value:
            tok = header_value[7:].strip() if header_value[:7].lower() == "bearer " else header_value.strip()
        if not tok:
            raise Forbidden("JWT 없음")
        try:
            p = jwt_verify(tok, d["jwt_secret"], require_exp=True)
        except JWTError as e:
            raise Forbidden(f"JWT 검증 실패({e})")
        if "key" not in p and isinstance(p.get("payload"), dict):  # 헤더 토큰은 {"payload": {...}} 로 감쌈
            p = p["payload"]
        return p

    def oo_check_cb(self, cb_token):
        """콜백 주소 토큰(우리 link_key 서명) — 본문을 읽기 전에 먼저."""
        if not self.oo_enabled():
            raise NotFound("없음")
        try:
            cb = jwt_verify(cb_token, self._oo_conf()["link_key"], require_exp=True, leeway=0)
        except JWTError:
            raise Forbidden("콜백 주소 토큰 오류")
        if cb.get("t") != "cb" or not isinstance(cb.get("key"), str):
            raise Forbidden("콜백 주소 토큰 오류")
        return cb

    def oo_callback(self, cb_token, body: dict, header_value=""):
        """문서 서버 콜백. 반환 {"error": 0|1}. 저장은 새 판(올리기와 같음, 감사 기록 = 편집 사용자)."""
        cb = self.oo_check_cb(cb_token)
        d = self._oo_conf()
        p = self._oo_verify_callback(d, body if isinstance(body, dict) else {}, header_value)
        key = p.get("key")
        if not isinstance(key, str) or not hmac.compare_digest(key.encode(), cb["key"].encode()):
            raise Forbidden("문서 키 불일치")
        mode, kind, ident, n, ksha = parse_key(key)
        if mode != "e":
            raise Forbidden("보기 세션은 저장하지 않음")
        try:
            status = int(p.get("status"))
        except (TypeError, ValueError):
            raise Invalid("상태 없음")
        if status not in (2, 6):
            return {"error": 0, "saved": False, "status": status}  # 1 편집중 / 3·7 저장 오류 / 4 변경 없음
        url = p.get("url")
        if not isinstance(url, str):
            raise Invalid("결과 주소 없음")
        users = [u for u in (p.get("users") or []) if isinstance(u, str)]
        actor = None
        # 편집기를 연 사람(우리 서명 cb 토큰)이 지금도 편집 가능하면 그 사람, 아니면 문서 서버가 알린 편집자 중 가능한 사람
        for u in ([cb["u"]] if isinstance(cb.get("u"), str) else []) + users:  # 이 파일을 지금 편집할 수 있는 첫 사용자(권한 없는 사람 이름으로는 저장 안 함)
            try:
                if self.oo_access(u, kind, ident, n if kind == "d" else None)["can_edit"]:
                    actor = u
                    break
            except Exception:  # noqa: BLE001
                continue
        if not actor:
            self._oo_audit_refused(key, users, "편집 권한 없음/잠김")
            return {"error": 1, "saved": False, "status": status}
        a = self.oo_access(actor, kind, ident, n if kind == "d" else None)
        # 옛 세션이 그 사이 다른 판을 덮지 않게: 지금 파일이 이 세션의 시작 판이거나 이 세션이 마지막으로 저장한 판일 때만
        if a["sha256"][:16] != ksha and a["sha256"] != self._oo_chain_get(key):
            self._oo_audit_refused(key, users, "그 사이 다른 판이 생김(옛 편집 세션)")
            return {"error": 1, "saved": False, "status": status}
        ft = p.get("filetype")
        if ft is None or ("." + str(ft).lower()) != a["ext"]:
            raise Invalid("결과 형식이 원본과 다릅니다")
        from .core_base import MAX_FILE_BYTES
        from .home import LIB_MAX_BYTES
        cap = MAX_FILE_BYTES if kind == "d" else LIB_MAX_BYTES
        allowed = {_origin(d["docs_url"]), _origin(d["docs_internal_url"])}
        if d["docs_internal_url"] and _origin(url) == _origin(d["docs_url"]):  # 서버끼리는 내부 주소로
            src, dst = urllib.parse.urlsplit(url), urllib.parse.urlsplit(d["docs_internal_url"])
            url = urllib.parse.urlunsplit((dst.scheme, dst.netloc, src.path, src.query, ""))
        data = self._oo_fetch(url, allowed, cap)
        if not data.startswith(b"PK\x03\x04"):  # docx/xlsx/pptx = zip
            raise Invalid("결과가 Office 문서(zip)가 아닙니다")
        note = "OnlyOffice 편집" + (f" (판{n}에서)" if kind == "l" else "") + (" (중간 저장)" if status == 6 else "")
        if kind == "l":
            r = self.files_new_version(actor, ident, data, note, if_changed=True)
        else:
            r = self.attachment_replace(actor, ident, n, data, note)
        with self._tx() as c:
            c.execute("create table if not exists oo_chain(key text primary key, sha text not null, at text)")
            c.execute("insert or replace into oo_chain(key,sha,at) values(?,?,datetime('now'))",
                      (key, hashlib.sha256(data).hexdigest()))
            self._audit(c, actor, "oo_save", ident if kind == "d" else None,
                        {"key": key, "status": status, "claimed_users": users[:10], "result": r})
        return {"error": 0, "saved": not r.get("unchanged"), "status": status}

    def _oo_chain_get(self, key):
        with self._ro() as c:
            if not c.execute("select 1 from sqlite_master where type='table' and name='oo_chain'").fetchone():
                return None
            r = c.execute("select sha from oo_chain where key=?", (key,)).fetchone()
        return r["sha"] if r else None

    def _oo_fetch(self, url, allowed, cap):  # 시험에서 바꿔 끼움
        return fetch_limited(url, allowed, cap)

    def _oo_audit_refused(self, key, users, why):
        with self._tx() as c:
            self._audit(c, "system", "oo_save_refused", None, {"key": key, "users": users[:10], "why": why})
