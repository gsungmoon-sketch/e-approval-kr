"""최초 설정 마법사(웹): 사용자가 한 명도 없을 때, 서버와 같은 컴퓨터(루프백)에서만.
첫 관리자 만들기 + 접속 범위(이 컴퓨터만 / 사내망) + 자동 백업 폴더."""
from __future__ import annotations

import hmac
import os
import secrets
import sys

from . import core
from .web import TRUST_PROXY, Resp, esc, redirect

TOKEN = secrets.token_urlsafe(24)  # 프로세스마다 새로 — 다른 사이트가 대신 POST 하지 못하게
LOOPBACK = ("127.0.0.1", "::1", "::ffff:127.0.0.1")
# 다른 PC(예: Docker 컨테이너 밖)에서 설정할 때만 쓰는 1회용 열쇠 — 서버 로그에만 찍힘(또는 GYEOLJAE_SETUP_TOKEN)
_ENV = os.environ.get("GYEOLJAE_SETUP_TOKEN", "")
REMOTE_TOKEN = _ENV if len(_ENV) >= 16 else secrets.token_urlsafe(18)


def announce(port):
    """사용자가 없을 때 serve 시작 시 호출: 원격 설정용 주소를 로그(stderr)에만 출력."""
    print(f"첫 설정 / first-run setup: 이 컴퓨터에서 http://127.0.0.1:{port}/setup\n"
          f"  다른 PC·컨테이너 밖에서는(1회용 열쇠): http://<서버주소>:{port}/setup?key={REMOTE_TOKEN}",
          file=sys.stderr, flush=True)


def _key_ok(h):
    k = h.qs.get("key") or h.form.get("key") or ""
    return bool(k) and hmac.compare_digest(k, REMOTE_TOKEN)


def _local(h):
    if TRUST_PROXY and (h.headers.get("X-Forwarded-For") or h.headers.get("Cf-Connecting-Ip")):
        return False  # 프록시 뒤 = 원격
    return h.client_address[0] in LOOPBACK


def _form(h, app, v, err=""):
    conf = app.server_conf()
    lan = v.get("bind", "lan" if conf["host"] not in ("127.0.0.1", "localhost", "::1") else "local") == "lan"
    bd = v.get("backup_dir", conf["backup_dir"] or str(app.dir / "backups"))
    return (f"<h2>처음 설정</h2><p class=mut>Welcome — first-run setup. 이 화면은 서버가 설치된 컴퓨터에서만 열립니다.</p>"
            f"{'<p class=err role=alert>' + esc(err) + '</p>' if err else ''}"
            f"<form method=post action='/setup' class=card><input type=hidden name=token value='{TOKEN}'>"
            f"{'<input type=hidden name=key value=' + chr(39) + esc(REMOTE_TOKEN) + chr(39) + '>' if _key_ok(h) else ''}"
            f"<fieldset><legend>① 첫 관리자 (Administrator)</legend>"
            f"<label for=s-id>관리자 아이디</label><input type=text id=s-id name=admin_id required "
            f"value='{esc(v.get('admin_id', 'admin'))}' autocomplete=username>"
            f"<label for=s-nm>이름</label><input type=text id=s-nm name=admin_name required value='{esc(v.get('admin_name', '관리자'))}'>"
            f"<label for=s-p1>비밀번호 (8자 이상)</label><input type=password id=s-p1 name=pw1 required minlength=8 autocomplete=new-password>"
            f"<label for=s-p2>비밀번호 확인</label><input type=password id=s-p2 name=pw2 required minlength=8 autocomplete=new-password>"
            f"<div class=row><div><label for=s-dc>관리 부서 코드</label><input type=text id=s-dc name=dept_code "
            f"value='{esc(v.get('dept_code', 'ADM'))}'></div><div><label for=s-dn>관리 부서 이름</label>"
            f"<input type=text id=s-dn name=dept_name value='{esc(v.get('dept_name', '관리부'))}'></div></div></fieldset>"
            f"<fieldset><legend>② 접속 범위 (Who can connect)</legend>"
            f"<label class=pick><input type=radio name=bind value=local {'' if lan else 'checked'}> 이 컴퓨터만 (127.0.0.1, 권장)</label>"
            f"<label class=pick><input type=radio name=bind value=lan {'checked' if lan else ''}> 사내망의 다른 PC도 (0.0.0.0)</label>"
            f"<p class=mut>⚠ 사내망 공개는 암호화되지 않은 HTTP 입니다. 인터넷에 직접 열지 말고, 가능하면 HTTPS 리버스 프록시를 앞에 두세요"
            f"(설명서 ‘보안 권장’). 바꾼 접속 범위는 서비스를 다시 시작하면 적용됩니다.</p>"
            f"<label for=s-pt>포트</label><input type=text id=s-pt name=port inputmode=numeric value='{esc(v.get('port', conf['port']))}'>"
            f"</fieldset><fieldset><legend>③ 자동 백업 (Daily backup)</legend>"
            f"<label for=s-bd>백업 폴더 <span class=mut>(전체 경로, 비우면 자동 백업 안 함 · 하루 1번, 14개 보관)</span></label>"
            f"<input type=text id=s-bd name=backup_dir value='{esc(bd)}'>"
            f"<p class=mut>다른 디스크나 NAS 폴더를 권장합니다. 백업 파일에는 회사 문서가 들어 있으니 안전한 곳에 두세요.</p></fieldset>"
            f"<p class=row><button>설정 완료</button></p></form>")


def setup_route(h, app):
    if app.has_users():
        return redirect("/login")
    if not (_local(h) or _key_ok(h)):
        return Resp(403, h.page("설정 필요", "<h2>아직 설정되지 않은 서버입니다</h2><p>서버가 설치된 컴퓨터에서 "
                                          "<b>http://127.0.0.1:포트/setup</b> 을 열어 첫 설정을 마치세요.</p>"
                                          "<p class=mut>Not set up yet. Open /setup on the server computer itself.</p>"
                                          "<p class=mut>Docker·원격: 서버 로그에 찍힌 <code>/setup?key=…</code> 주소를 쓰세요.</p>"))
    if h.route != "/setup":
        return redirect("/setup" + (f"?key={h.qs['key']}" if _key_ok(h) else ""))
    if h.method != "POST":
        return h.ok("처음 설정", _form(h, app, {}))
    f = h.form
    if not hmac.compare_digest(f.get("token", ""), TOKEN):
        return Resp(403, h.page("차단", "<p class=err>설정 화면을 새로 고친 뒤 다시 시도하세요</p>"))
    v = {k: f.get(k, "") for k in ("admin_id", "admin_name", "dept_code", "dept_name", "bind", "port", "backup_dir")}
    try:
        if f.get("pw1", "") != f.get("pw2", ""):
            raise core.Invalid("비밀번호 확인이 다릅니다")
        if len(f.get("pw1", "")) < 8:
            raise core.Invalid("비밀번호는 8자 이상")
        conf = app.server_conf_set(None, host="0.0.0.0" if v["bind"] == "lan" else "127.0.0.1", port=v["port"],
                                   backup_dir=v["backup_dir"].strip())
        app.bootstrap((v["dept_code"] or "ADM").strip().upper(), (v["dept_name"] or "관리부").strip(),
                      (v["admin_id"] or "admin").strip().lower(), (v["admin_name"] or "관리자").strip(), f["pw1"])
    except core.GyeolError as e:
        r = Resp(400, h.page("처음 설정", _form(h, app, v, str(e))))
        return r
    try:
        made = app.auto_backup()  # 첫 백업을 바로 만들어 백업 폴더가 실제로 되는지 확인
    except Exception as e:  # noqa: BLE001 — 설정은 끝났으니 경고만
        made = None
        print(f"첫 자동 백업 실패: {e}", file=sys.stderr, flush=True)
    cur = h.server.server_address
    restart = conf["host"] != cur[0] or conf["port"] != cur[1]
    note = ("<p class='banner warn'>접속 범위·포트를 바꿨습니다. 서비스를 다시 시작해야 적용됩니다"
            "(설명서 ‘첫 설정’ 참고).</p>" if restart else "")
    return h.ok("설정 완료", f"<h2>설정 완료</h2>{note}<p>이제 관리자 <b>{esc(v['admin_id'] or 'admin')}</b> 로 로그인해 "
                           f"부서·사원을 등록하세요.</p><p><a class=btn href='/login'>로그인하기</a></p>"
                           f"<p class=mut>Setup complete — log in as the administrator.</p>")
