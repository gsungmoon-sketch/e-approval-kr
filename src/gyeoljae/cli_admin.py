"""관리 CLI: 사원·부서·직위·조직도·결재규정·변경요청·설정 + 최초 설정 + BBS 관리 메뉴."""
from __future__ import annotations

import getpass
import json
import sys
from pathlib import Path

from . import core
from . import policy as pol
from .core_base import today as _today
from .org import EMP_STATUS

NOT_MINE = object()


def add_admin_parsers(sp):
    s = sp.add_parser("emp", help="사원 관리(관리자)")
    ss = s.add_subparsers(dest="sub", required=True)
    x = ss.add_parser("add", help="사원 등록(임시 비밀번호 1회 표시)")
    x.add_argument("--name", required=True); x.add_argument("--dept", required=True)
    x.add_argument("--position", required=True, help="직위(팀원/팀장/부서장/임원/대표 또는 코드)")
    x.add_argument("--empno", help="사번(생략 시 자동: 입사일 YYMMDD+일련번호 2자리, 예 26111301)")
    x.add_argument("--id", help="로그인 아이디(기본=사번)")
    x.add_argument("--email"); x.add_argument("--phone"); x.add_argument("--hired", help="입사일 YYYY-MM-DD(기본 오늘)")
    x.add_argument("--title", default=""); x.add_argument("--admin", action="store_true", help="관리자 권한")
    x = ss.add_parser("edit"); x.add_argument("who"); x.add_argument("--name"); x.add_argument("--email")
    x.add_argument("--phone"); x.add_argument("--hired"); x.add_argument("--title")
    x = ss.add_parser("move", help="부서 이동/직위 변경"); x.add_argument("who"); x.add_argument("--dept")
    x.add_argument("--position")
    x = ss.add_parser("role", help="역할 user|admin"); x.add_argument("who"); x.add_argument("role", choices=["user", "admin"])
    x = ss.add_parser("status", help="재직/휴직/퇴사"); x.add_argument("who"); x.add_argument("status")
    x = ss.add_parser("retire", help="퇴사(로그인 차단, 기록 보존)"); x.add_argument("who")
    x = ss.add_parser("resetpw", help="임시 비밀번호 재발급"); x.add_argument("who")
    x = ss.add_parser("list"); x.add_argument("--dept"); x.add_argument("-q"); x.add_argument("--status")
    x = ss.add_parser("show"); x.add_argument("who")
    x = ss.add_parser("import", help="CSV 일괄(기본 미리보기, --apply 로 반영)"); x.add_argument("csv")
    x.add_argument("--apply", action="store_true")
    x = ss.add_parser("export"); x.add_argument("-o", "--output"); x.add_argument("--cp949", action="store_true")

    s = sp.add_parser("dept", help="부서")
    ss = s.add_subparsers(dest="sub", required=True)
    x = ss.add_parser("add"); x.add_argument("code"); x.add_argument("name")
    x = ss.add_parser("rename"); x.add_argument("code"); x.add_argument("name")
    x = ss.add_parser("disable"); x.add_argument("code")
    x = ss.add_parser("enable"); x.add_argument("code")
    x = ss.add_parser("list"); x.add_argument("--all", action="store_true")

    s = sp.add_parser("position", help="직위")
    ss = s.add_subparsers(dest="sub", required=True)
    x = ss.add_parser("list")
    x = ss.add_parser("add"); x.add_argument("code"); x.add_argument("name"); x.add_argument("rank", type=int)
    x.add_argument("--scope", default="dept", choices=["dept", "org"]); x.add_argument("--dept-head", action="store_true")
    x = ss.add_parser("edit"); x.add_argument("code"); x.add_argument("--name"); x.add_argument("--rank", type=int)
    x.add_argument("--scope", choices=["dept", "org"]); x.add_argument("--dept-head", choices=["yes", "no"])
    x.add_argument("--active", choices=["yes", "no"])

    s = sp.add_parser("org", help="조직도·직원 찾기"); s.add_argument("-q"); s.add_argument("--dept")

    s = sp.add_parser("policy", help="부서별 결재규정(관리자)")
    ss = s.add_subparsers(dest="sub", required=True)
    ss.add_parser("list")
    x = ss.add_parser("show"); x.add_argument("dept")
    x = ss.add_parser("set", help="JSON 파일로 전체 교체"); x.add_argument("dept"); x.add_argument("file")
    x = ss.add_parser("clear", help="규정 해제(자유 결재선)"); x.add_argument("dept")
    x = ss.add_parser("edit", help="문서종류 하나 수정"); x.add_argument("dept"); x.add_argument("--type", required=True)
    x.add_argument("--line", help="예: '팀장?, 부서장, 대표' (?=없으면 생략, @아이디=특정인)")
    x.add_argument("--jeon", help="예: '부서장:100만, 임원:500만'"); x.add_argument("--extra", choices=["yes", "no"])
    x.add_argument("--cc", help="필수 참조 아이디들"); x.add_argument("--off", action="store_true", help="이 종류 규정 삭제")
    x = ss.add_parser("deleg", help="대결 규칙"); x.add_argument("dept"); x.add_argument("--allowed", choices=["yes", "no"])
    x.add_argument("--same-dept", choices=["yes", "no"]); x.add_argument("--min-pos"); x.add_argument("--max-days", type=int)
    x = ss.add_parser("history"); x.add_argument("dept")
    x = ss.add_parser("preview", help="누가 결재하나 미리보기"); x.add_argument("--dept"); x.add_argument("--drafter")
    x.add_argument("--type", default="일반"); x.add_argument("--amount")

    s = sp.add_parser("change", help="2인 승인 변경요청")
    ss = s.add_subparsers(dest="sub", required=True)
    x = ss.add_parser("list"); x.add_argument("--all", action="store_true")
    for nm in ("approve", "reject"):
        x = ss.add_parser(nm, help=("승인" if nm == "approve" else "거절") + " — 본인 비밀번호를 다시 확인(터미널에서 물음)")
        x.add_argument("id", type=int)
        x.add_argument("--password-file", help="비대화형: 첫 줄이 비밀번호인 파일('-' = 표준입력)")

    s = sp.add_parser("setting", help="설정")
    ss = s.add_subparsers(dest="sub", required=True)
    ss.add_parser("list")
    x = ss.add_parser("set"); x.add_argument("key", choices=["two_person", "show_contacts", "emp_digits", "doc_number", "allow_freeform",
                                                            "company_name", "doc_mark", "paper_default"])
    x.add_argument("value", help="on/off, emp_digits 는 2 또는 3, doc_number 는 예: {dept}-{year}-{seq}, "
                                 "company_name 은 회사명, doc_mark 는 문서 표시(예: 대외비, 20자 이내·빈 값=없음), paper_default 는 corporate/gov")

    s = sp.add_parser("form", help="양식(서식) — 목록·보기, 관리자는 추가·복사·수정·사용중지")
    ss = s.add_subparsers(dest="sub", required=True)
    x = ss.add_parser("list"); x.add_argument("--all", action="store_true", help="사용 중지 포함(관리자)")
    x = ss.add_parser("show"); x.add_argument("code"); x.add_argument("--version", type=int)
    x = ss.add_parser("add", help="JSON 파일로 새 양식"); x.add_argument("code"); x.add_argument("file")
    x = ss.add_parser("edit", help="JSON 파일로 새 버전(기존 문서는 옛 버전 유지)"); x.add_argument("code"); x.add_argument("file")
    x = ss.add_parser("copy"); x.add_argument("src"); x.add_argument("code"); x.add_argument("--name")
    x = ss.add_parser("disable"); x.add_argument("code")
    x = ss.add_parser("enable"); x.add_argument("code")
    x = ss.add_parser("versions"); x.add_argument("code")
    x = ss.add_parser("fav", help="즐겨찾기 켜기/끄기"); x.add_argument("code"); x.add_argument("--off", action="store_true")

    s = sp.add_parser("passwd", help="비밀번호 변경(본인)")

    s = sp.add_parser("home", help="홈 화면(공지·결재대기·진행·완료·반려·쪽지)")
    s = sp.add_parser("files", help="자료실(전사·부서 파일 공유, 판 관리)")
    ss = s.add_subparsers(dest="sub", required=True)
    x = ss.add_parser("list"); x.add_argument("--org", action="store_true", help="전사 자료실(기본: 내 부서)")
    x.add_argument("-q")
    x = ss.add_parser("upload"); x.add_argument("path"); x.add_argument("--org", action="store_true")
    x.add_argument("--note", default="")
    x = ss.add_parser("versions"); x.add_argument("id", type=int)
    x = ss.add_parser("get"); x.add_argument("id", type=int); x.add_argument("--version", type=int)
    x.add_argument("-o", "--output")
    x = ss.add_parser("delete"); x.add_argument("id", type=int)
    s = sp.add_parser("folder", help="내 보관함(개인 폴더)")
    ss = s.add_subparsers(dest="sub", required=True)
    ss.add_parser("list")
    x = ss.add_parser("create"); x.add_argument("name")
    x = ss.add_parser("rename"); x.add_argument("id", type=int); x.add_argument("name")
    x = ss.add_parser("delete"); x.add_argument("id", type=int)
    x = ss.add_parser("show"); x.add_argument("id", type=int)
    x = ss.add_parser("add"); x.add_argument("id", type=int); x.add_argument("doc", type=int)
    x = ss.add_parser("remove"); x.add_argument("id", type=int); x.add_argument("doc", type=int)
    s = sp.add_parser("mail", help="사내 메일(편지함)")
    ss = s.add_subparsers(dest="sub", required=True)
    x = ss.add_parser("list"); x.add_argument("--folder", default="inbox", choices=["inbox", "sent", "drafts", "trash"])
    x.add_argument("-q"); x.add_argument("--unread", action="store_true")
    x = ss.add_parser("send", help="받는사람·참조·숨은참조: 아이디, @부서코드, @전체")
    x.add_argument("--to", default=""); x.add_argument("--cc", default=""); x.add_argument("--bcc", default="")
    x.add_argument("--ext", default="", help="외부 주소(관리자가 허용한 경우)")
    x.add_argument("--subject", required=True); x.add_argument("--body", default=""); x.add_argument("--body-file")
    x.add_argument("--attach", nargs="*", default=[]); x.add_argument("--draft", action="store_true", help="임시보관")
    x = ss.add_parser("read"); x.add_argument("id", type=int)
    for nm in ("reply", "replyall", "forward"):
        x = ss.add_parser(nm); x.add_argument("id", type=int); x.add_argument("--body", default="")
        x.add_argument("--to", default=""); x.add_argument("--cc", default="")
    for nm in ("trash", "restore", "purge"):
        x = ss.add_parser(nm); x.add_argument("id", type=int)
    x = ss.add_parser("file"); x.add_argument("id", type=int); x.add_argument("idx", type=int); x.add_argument("-o", "--output")
    ss.add_parser("flush", help="외부 메일 대기열 발송(관리자)")
    s = sp.add_parser("smtp", help="외부 메일 릴레이 설정(관리자)")
    ss = s.add_subparsers(dest="sub", required=True)
    ss.add_parser("show")
    x = ss.add_parser("set")
    for k in ("host", "port", "security", "user", "from-addr", "from-name", "rate-per-hour", "user-external-per-hour"):
        x.add_argument("--" + k)
    for k in ("enabled", "notify-approvals", "allow-external-mail"):
        x.add_argument("--" + k, choices=["on", "off"])
    x.add_argument("--password-stdin", action="store_true")
    x = ss.add_parser("test"); x.add_argument("to")
    s = sp.add_parser("pin", help="공지 고정/해제(관리자)"); s.add_argument("post", type=int)
    s.add_argument("--off", action="store_true")


def _interactive():
    """사람이 터미널 앞에 있는가. Windows 는 NUL 장치도 isatty()=True 라 stdout 까지 본다(getpass 무한 대기 방지)."""
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def _confirm_password(app, a, args=None):
    pf = getattr(args, "password_file", None)
    if pf:
        pw = (sys.stdin.readline() if pf == "-" else (Path(pf).read_text(encoding="utf-8").splitlines() or [""])[0])
        pw = pw.rstrip("\r\n")
    elif _interactive():
        pw = getpass.getpass(f"{a} 비밀번호 확인: ")
    else:
        raise core.Invalid("비밀번호 확인이 필요합니다: 터미널에서 실행하거나 --password-file 을 주세요")
    if not app.check_password(a, pw):
        raise core.Forbidden("비밀번호가 틀립니다")


def _sv(v):
    if isinstance(v, bool):
        return "on" if v else "off"
    return str(v)


def _yn(v):
    return None if v is None else v == "yes"


def _mail_cmd(args, app, a, out):
    s = args.sub
    if s == "list":
        rows = app.mail_list(a, args.folder, args.q, args.unread)
        return out(args, rows, "\n".join(
            f"{r['id']:>5} {'*' if args.folder == 'inbox' and not r['read_at'] else ' '} {r['created'][:16]} "
            f"{(r['sender_name'] or r['sender']) if args.folder in ('inbox', 'trash') else '→ ' + (r['to_spec'] or '')[:20]:<12} "
            f"{r['subject']}{' 📎' if r['files'] else ''}" for r in rows) or "(없음)")
    if s == "send":
        body = Path(args.body_file).read_text(encoding="utf-8") if args.body_file else args.body
        files = [(Path(p).name, Path(p).read_bytes()) for p in args.attach]
        r = app.mail_send(a, args.to, args.cc, args.bcc, args.subject, body, files, save_draft=args.draft,
                          ext_to=args.ext)
        return out(args, r, f"임시보관 #{r['id']}" if r.get("draft") else
                   f"보냄 #{r['id']} (사내 {r['recipients']}명{', 외부 ' + str(r['external']) if r['external'] else ''})")
    if s == "read":
        m = app.mail_read(a, args.id)
        L = [f"#{m['id']} {m['subject']}", f"보낸 사람: {m['sender_name']} ({m['sender']})  {m['created'][:16]}",
             "받는 사람: " + ", ".join(x["name"] or x["username"] for x in m["to"]),
             "참조: " + (", ".join(x["name"] or x["username"] for x in m["cc"]) or "-")]
        if m["is_sender"]:
            L.append("숨은참조: " + (", ".join(x["name"] or x["username"] for x in m["bcc"]) or "-"))
            L.append("읽음: " + ", ".join(f"{x['username']}{'✓' if x.get('read_at') else '·'}"
                                          for x in m["to"] + m["cc"] + m["bcc"]))
        L += ["-" * 50, m["body"], "-" * 50] + [f"첨부 {x['idx']}: {x['name']} ({x['size']:,}B)" for x in m["attachments"]]
        return out(args, m, "\n".join(L))
    if s in ("reply", "replyall", "forward"):
        pre = app.mail_compose_from(a, args.id, s)
        r = app.mail_send(a, args.to or pre["to"], args.cc or pre["cc"], "", pre["subject"],
                          (args.body + pre["body"]) if args.body else pre["body"],
                          reply_to=pre.get("reply_to"), forward_of=pre.get("forward_of"))
        return out(args, r, f"보냄 #{r['id']}")
    if s in ("trash", "restore", "purge"):
        r = app.mail_move(a, args.id, {"trash": "trash", "restore": "restore", "purge": "deleted"}[s])
        return out(args, r, "ok")
    if s == "file":
        name, path = app.mail_attachment(a, args.id, args.idx)
        dest = Path(args.output) if args.output else Path.cwd() / name
        if dest.exists():
            raise core.Invalid(f"이미 있는 파일: {dest}")
        dest.write_bytes(path.read_bytes())
        return out(args, {"saved": str(dest)}, f"저장: {dest}")
    if s == "flush":
        app._require_admin(a)
        r = app.deliver_outbox()
        return out(args, r, str(r))


def fmt_home(h):
    def items(card, fn):
        return "\n".join("   " + fn(x) for x in h[card]["items"]) or "   (없음)"
    u = h["user"]
    return "\n".join([
        f"== {u['name']}({u['username']}) {u['dept']} ==  메일 {h['unread_mail']}통 · 쪽지 {h['unread_messages']}통"
        f" · 참조 {h['cc']} · 기안함 {h['drafts']}",
        f"[공지] {h['notices']['count']}건(새 글 {h['notices']['new']})",
        items("notices", lambda p: f"{'📌' if p['pinned'] else '  '}{'N ' if p['new'] else '  '}{p['title']}"),
        f"[결재대기] {h['inbox']['count']}  (오래된 순)",
        items("inbox", lambda r: f"#{r['id']} {r['title']} — {r['drafter_name']}, {r['waiting_days']}일째"),
        f"[결재진행] {h['progress']['count']}",
        items("progress", lambda r: f"#{r['id']} {r['title']} — 현재 {r['cur_name'] or '-'}"),
        f"[결재완료] {h['done']['count']}",
        items("done", lambda r: f"{r['number']} {r['title']}"),
        f"[반려] {h['rejected']['count']}",
        items("rejected", lambda r: f"#{r['id']} {r['title']}")])


def fmt_emp(rows):
    return "\n".join(f"{r['emp_no'] or '-':<10} {r['username']:<12} {r['name']:<8} {r['dept']:<5} "
                     f"{r['position_name'] or '-':<4} {r['status_ko']}{' [관리자]' if r['role'] == 'admin' else ''}"
                     for r in rows) or "(없음)"


def fmt_policy(g):
    if not g["policy"]:
        return f"{g['dept']}: 결재규정 없음(자유 결재선)"
    L = [f"{g['dept']} 결재규정 v{g['version']} ({g['created'][:16]} {g['created_by']}"
         f"{', 승인 ' + g['approved_by'] if g.get('approved_by') else ''})"]
    for t, ko in pol.DOC_TYPES.items():
        x = g["text"][t]
        if x["enabled"]:
            L.append(f" [{ko}] 결재선: {x['line'] or '(없음)'} | 전결: {x['jeon'] or '-'} | "
                     f"추가결재자: {'허용' if x['allow_extra'] else '금지'} | 필수참조: {x['cc'] or '-'}")
    d = g["text"]["delegation"]
    L.append(f" [대결] {'허용' if d['allowed'] else '금지'}, 같은부서만={d['same_dept_only']}, "
             f"최소직위={d['min_pos'] or '-'}, 최대일수={d['max_days'] or '-'}")
    return "\n".join(L)


def fmt_preview(p):
    if p["free"]:
        return p["note"]
    return (f"{p['dept']} v{p['policy_version']} {pol.DOC_TYPES[p['doc_type']]} {pol.fmt_amount(p['amount'])}: "
            + " → ".join(f"{x['name']}({x['position']}){'[전결]' if x['final'] else ''}" for x in p["line"])
            + (f" | 참조 {', '.join(p['cc'])}" if p["cc"] else ""))


def _pending(out, args, r, done_text):
    if isinstance(r, dict) and r.get("pending"):
        return out(args, r, f"2인 승인 대기: 변경요청 #{r['pending']} (다른 관리자가 `change approve {r['pending']}`)")
    return out(args, r, done_text)


def run_admin(args, app, a, out):
    c = args.cmd
    if c == "passwd":
        old = getpass.getpass("현재 비밀번호: ")
        new = getpass.getpass("새 비밀번호(8자+): ")
        if getpass.getpass("확인: ") != new:
            raise core.Invalid("확인 불일치")
        app.set_password(a, a, new, old)
        return out(args, {"ok": True}, "변경됨")
    if c == "mail":
        return _mail_cmd(args, app, a, out)
    if c == "form":
        return _form_cmd(args, app, a, out)
    if c == "smtp":
        if args.sub == "show":
            r = app.smtp_get(a)
            return out(args, r, "\n".join(f"{k}: {v}" for k, v in r.items()))
        if args.sub == "test":
            r = app.smtp_test(a, args.to)
            out(args, r, "성공" if r["ok"] else "실패: " + r["error"])
            return 0 if r["ok"] else 3
        kw = {k.replace("-", "_"): getattr(args, k.replace("-", "_")) for k in
              ("host", "port", "security", "user", "from-addr", "from-name", "rate-per-hour", "user-external-per-hour",
               "enabled", "notify-approvals", "allow-external-mail")}
        for b in ("enabled", "notify_approvals", "allow_external_mail"):
            if kw[b] is not None:
                kw[b] = kw[b] == "on"
        if args.password_stdin:
            kw["password"] = sys.stdin.readline().rstrip("\r\n")
        r = app.smtp_set(a, **kw)
        return out(args, r, "저장됨")
    if c == "home":
        h = app.home(a)
        return out(args, h, fmt_home(h))
    if c == "pin":
        app.board_pin(a, args.post, not args.off)
        return out(args, {"ok": True}, "해제됨" if args.off else "고정됨")
    if c == "files":
        s = args.sub
        if s == "list":
            r = app.files_list(a, "org" if args.org else "dept", q=args.q)
            return out(args, r, f"[{'전사' if r['scope'] == 'org' else r['dept'] + ' 부서'} 자료실]"
                                f"{' (읽기 전용)' if not r['can_write'] else ''}\n" + ("\n".join(
                                    f"{x['id']:>4} 판{x['cur_version']:<3} {x['size']:>10,}  {x['updated'][:10]} "
                                    f"{x['uploader_name'] or x['uploader']:<6} {x['name']}" for x in r["files"]) or "(없음)"))
        if s == "upload":
            p = Path(args.path)
            r = app.files_upload(a, p.name, p.read_bytes(), "org" if args.org else "dept", args.note)
            return out(args, r, f"올림: #{r['id']} {r['name']} 판{r['version']}")
        if s == "versions":
            r = app.files_versions(a, args.id)
            return out(args, r, f"{r['name']}\n" + "\n".join(
                f"  판{v['version']} {v['created'][:16]} {v['uploader_name'] or v['uploader']} {v['size']:,}B {v['note'] or ''}"
                for v in r["versions"]))
        if s == "get":
            name, path = app.files_get(a, args.id, args.version)
            dest = Path(args.output) if args.output else Path.cwd() / name
            if dest.exists():
                raise core.Invalid(f"이미 있는 파일: {dest}")
            dest.write_bytes(path.read_bytes())
            return out(args, {"saved": str(dest)}, f"저장: {dest}")
        app.files_delete(a, args.id)
        return out(args, {"ok": True}, "삭제됨(이전 판·기록은 보존)")
    if c == "folder":
        s = args.sub
        if s == "list":
            rows = app.folder_list(a)
            return out(args, rows, "\n".join(f"{r['id']:>4} {r['name']} ({r['count']})" for r in rows) or "(없음)")
        if s == "create":
            return out(args, app.folder_create(a, args.name), "만듦")
        if s == "rename":
            return out(args, app.folder_rename(a, args.id, args.name), "변경됨")
        if s == "delete":
            app.folder_delete(a, args.id)
            return out(args, {"ok": True}, "폴더 삭제(문서는 그대로)")
        if s == "show":
            r = app.folder_docs(a, args.id)
            from .cli import fmt_rows
            return out(args, r, f"[{r['name']}]\n" + fmt_rows(r["docs"]))
        getattr(app, "folder_" + s)(a, args.id, args.doc)
        return out(args, {"ok": True}, "ok")
    if c == "emp":
        s = args.sub
        if s == "add":
            r = app.emp_add(a, args.name, args.dept, args.position, args.empno, args.id, args.email, args.phone,
                            args.hired, args.title, args.admin)
            t = f"등록: 사번 {r['emp_no']} 아이디 {r['username']}  임시비밀번호 {r['temp_password']} (지금 한 번만 표시)"
            if r["pending"]:
                t += f"\n2인 승인 대기 변경요청: {r['pending']}"
            return out(args, r, t)
        if s == "edit":
            r = app.emp_edit(a, args.who, name=args.name, email=args.email, phone=args.phone, hired=args.hired,
                             title=args.title)
            return out(args, r, f"수정: {', '.join(r['changed']) or '변경 없음'}")
        if s == "move":
            return _pending(out, args, app.emp_move(a, args.who, args.dept, args.position), "이동/직위 변경 완료")
        if s == "role":
            return _pending(out, args, app.emp_set_role(a, args.who, args.role), "역할 변경 완료")
        if s in ("status", "retire"):
            r = app.emp_set_status(a, args.who, "retired" if s == "retire" else args.status)
            return out(args, r, f"{r['username']}: {EMP_STATUS[r['status']]}")
        if s == "resetpw":
            r = app.emp_reset_password(a, args.who)
            return out(args, r, f"{r['username']} 임시비밀번호 {r['temp_password']} (지금 한 번만 표시, 첫 로그인 시 변경)")
        if s == "list":
            rows = app.emp_list(a, args.dept, args.q, args.status)
            return out(args, rows, fmt_emp(rows))
        if s == "show":
            e = app.emp_get(a, args.who)
            t = "\n".join([f"{k}: {e.get(k)}" for k in ("emp_no", "username", "name", "dept", "position", "title", "email",
                                                       "phone", "hired", "status_ko", "role")]
                          + ["이력:"] + [f"  {h['at'][:16]} {h['actor']} {h['field']}: {h['before']} → {h['after']}"
                                         for h in e["history"]])
            return out(args, e, t)
        if s == "import":
            r = app.emp_import(a, Path(args.csv).read_bytes(), apply=args.apply)
            L = [f"{x['line']:>4} {x['action']:<3} {x['emp_no']:<10} {x['name'] or '':<8} {x['error'] and '오류: ' + x['error']}"
                 for x in r["rows"]]
            L.append(f"오류 {r['errors']}건 — " + ("반영 완료" if r["applied"] else
                                                   ("반영 안 함(오류 수정 후 다시)" if r["errors"] else "미리보기(--apply 로 반영)")))
            for x in r["created"]:
                if x["temp_password"]:
                    L.append(f"  임시비밀번호 {x['username']}: {x['temp_password']}")
            out(args, r, "\n".join(L))
            return 0 if not r["errors"] else 3
        if s == "export":
            data = app.emp_export(a, "cp949" if args.cp949 else "utf-8-sig")
            if args.output:
                p = Path(args.output)
                if p.exists():
                    raise core.Invalid(f"이미 있는 파일: {p}")
                p.write_bytes(data)
                return out(args, {"file": str(p)}, f"저장: {p}")
            sys.stdout.buffer.write(data)
            return 0
    if c == "dept":
        s = args.sub
        if s == "add":
            return out(args, app.add_dept(a, args.code, args.name), f"부서 추가: {args.code.upper()}")
        if s == "rename":
            return out(args, app.dept_rename(a, args.code.upper(), args.name), "변경됨")
        if s in ("disable", "enable"):
            app.dept_set_active(a, args.code.upper(), s == "enable")
            return out(args, {"ok": True}, "ok")
        rows = app.list_depts(include_inactive=args.all)
        return out(args, rows, "\n".join(f"{r['code']:<8} {r['name']:<12} {r['members']}명{'' if r['active'] else ' (폐지)'}"
                                         for r in rows))
    if c == "position":
        s = args.sub
        if s == "add":
            return _pending(out, args, app.pos_add(a, args.code, args.name, args.rank, args.scope, args.dept_head), "추가됨")
        if s == "edit":
            return _pending(out, args, app.pos_update(a, args.code, args.name, args.rank, args.scope, _yn(args.dept_head),
                                                      _yn(args.active)), "변경됨")
        P = app.positions(True)
        return out(args, P, "\n".join(f"{k:<8} {v['name']:<5} 서열{v['rank']:>3} {'부서내' if v['scope'] == 'dept' else '전사'}"
                                      f"{' 부서장열람' if v['dept_head'] else ''}{'' if v['active'] else ' (폐지)'}"
                                      for k, v in P.items()))
    if c == "org":
        rows = app.org_directory(a, args.q, args.dept)
        return out(args, rows, "\n".join(f"{r['dept_name'] or r['dept']:<10} {r['position_name'] or '-':<4} {r['name']}"
                                         f" ({r['username']}){' ' + r['email'] if r.get('email') else ''}"
                                         f"{' ' + r['phone'] if r.get('phone') else ''}" for r in rows) or "(없음)")
    if c == "policy":
        s = args.sub
        if s == "list":
            rows = app.policy_overview(a)
            return out(args, rows, "\n".join(f"{r['code']:<8} {r['name']:<12} "
                                             f"{'v' + str(r['version']) if r['version'] else '규정 없음(자유)'}" for r in rows))
        if s == "show":
            g = app.policy_get(a, args.dept)
            return out(args, g, fmt_policy(g))
        if s == "set":
            data = json.loads(Path(args.file).read_text(encoding="utf-8"))
            return _pending(out, args, app.policy_set(a, args.dept, data), "적용됨(새 상신부터)")
        if s == "clear":
            return _pending(out, args, app.policy_set(a, args.dept, None), "해제됨")
        if s == "edit":
            r = app.policy_update_type(a, args.dept, args.type, args.line, args.jeon, _yn(args.extra), args.cc,
                                       enabled=not args.off)
            return _pending(out, args, r, f"적용됨 v{r.get('version')}")
        if s == "deleg":
            r = app.policy_update_delegation(a, args.dept, allowed=_yn(args.allowed), same_dept_only=_yn(args.same_dept),
                                             min_pos=args.min_pos, max_days=args.max_days)
            return _pending(out, args, r, f"적용됨 v{r.get('version')}")
        if s == "history":
            h = app.policy_history(a, args.dept)
            return out(args, h, "\n\n".join(f"v{x['version']} {x['created'][:16]} {x['created_by']}"
                                            f"{' 승인 ' + x['approved_by'] if x['approved_by'] else ''}\n{x['diff']}"
                                            for x in h) or "(이력 없음)")
        if s == "preview":
            p = app.preview_line(a, args.type, args.amount, args.dept, args.drafter)
            return out(args, p, fmt_preview(p))
    if c == "change":
        if args.sub == "list":
            rows = app.change_list(a, None if args.all else "pending")
            return out(args, rows, "\n".join(f"#{r['id']} {r['status']:<8} {r['created'][:16]} {r['requested_by']}: "
                                             f"{r['summary']}" for r in rows) or "(없음)")
        _confirm_password(app, a, args)  # CLI 의 --user 는 인증이 아니므로 2인 승인 시 본인 비밀번호 확인
        r = app.change_decide(a, args.id, args.sub == "approve")
        return out(args, r, f"#{r['id']} {r['status']}")
    if c == "setting":
        if args.sub == "list":
            st = app.settings(a)
            return out(args, st, "\n".join(f"{k}: {_sv(v['value'])}  ({v['label']})" for k, v in st.items()))
        return _pending(out, args, app.setting_set(a, args.key, args.value), "설정됨")
    return NOT_MINE


def setup(args, app):
    """최초 설정. 데모 계정 없이 첫 관리자만 만든다. --password-file 이 있으면 비대화형(설치 마법사용)."""
    if app.has_users():
        raise core.Invalid("이미 설정되어 있습니다")
    pwf = getattr(args, "password_file", None)
    if pwf:
        raw = sys.stdin.readline() if pwf == "-" else Path(pwf).read_text(encoding="utf-8").splitlines()[0:1]
        p1 = (raw if isinstance(raw, str) else (raw[0] if raw else "")).rstrip("\r\n")
        if len(p1) < 8:
            raise core.Invalid("비밀번호는 8자 이상")
        code = (args.dept_code or "ADM").strip().upper()
        dname = (args.dept_name or "관리부").strip()
        uid = (args.admin_id or "admin").strip().lower()
        name = (args.admin_name or "관리자").strip()
        app.bootstrap(code, dname, uid, name, p1)
        if getattr(args, "json", False):
            print(json.dumps({"ok": True, "admin": uid, "data": str(app.dir)}, ensure_ascii=False))
        else:
            print(f"완료: 관리자 {uid} (데이터 {app.dir})")
        return 0
    print("== 결재 최초 설정 ==  (데이터:", app.dir, ")")
    code = (args.dept_code or input("관리 부서 코드 [ADM]: ")).strip().upper() or "ADM"
    dname = (args.dept_name or input("관리 부서 이름 [관리부]: ")).strip() or "관리부"
    uid = (args.admin_id or input("관리자 아이디 [admin]: ")).strip().lower() or "admin"
    name = (args.admin_name or input("관리자 이름 [관리자]: ")).strip() or "관리자"
    while True:
        p1 = getpass.getpass("비밀번호(8자+): ")
        if getpass.getpass("확인: ") == p1 and len(p1) >= 8:
            break
        print("불일치하거나 8자 미만입니다.")
    app.bootstrap(code, dname, uid, name, p1)
    print(f"완료. 이제 `serve`로 웹을 띄우고 {uid} 로 로그인해 사원을 등록하세요.")
    return 0


def _ask(p, default=None):
    v = input(f"{p}{' [' + str(default) + ']' if default not in (None, '') else ''}: ").strip()
    return v or default


def admin_menu(app, a):
    """BBS 관리 메뉴 — CLI 와 같은 core 함수."""
    while True:
        pend = len(app.change_list(a))
        print("\n--- 관리 ---  1.사원목록 2.사원등록 3.이동/직위 4.재직상태 5.임시비번 6.부서 7.직위"
              f"\n             8.결재규정 보기 9.규정 편집 P.미리보기 H.규정이력 C.변경요청({pend}) S.설정 V.감사검증 0.뒤로")
        ch = input("관리> ").strip().upper()
        try:
            if ch in ("0", "Q", ""):
                return
            if ch == "1":
                print(fmt_emp(app.emp_list(a, _ask("부서코드(엔터=전체)"), _ask("이름/사번 검색"))))
            elif ch == "2":
                r = app.emp_add(a, _ask("이름"), _ask("부서코드"), _ask("직위", "팀원"), _ask("사번(엔터=입사일+일련번호 자동)"),
                                _ask("아이디(엔터=사번)"), _ask("이메일"), _ask("전화"),
                                _ask("입사일 YYYY-MM-DD", _today()))
                print(f"등록: 사번 {r['emp_no']} 아이디 {r['username']} 임시비밀번호 {r['temp_password']} (한 번만 표시)")
            elif ch == "3":
                print(app.emp_move(a, _ask("사번/아이디"), _ask("새 부서(엔터=유지)"), _ask("새 직위(엔터=유지)")))
            elif ch == "4":
                print(app.emp_set_status(a, _ask("사번/아이디"), _ask("재직/휴직/퇴사")))
            elif ch == "5":
                r = app.emp_reset_password(a, _ask("사번/아이디"))
                print(f"임시비밀번호 {r['temp_password']} (한 번만 표시)")
            elif ch == "6":
                for r in app.list_depts(True):
                    print(f"{r['code']:<8} {r['name']} {r['members']}명{'' if r['active'] else ' (폐지)'}")
                op = _ask("a=추가 r=이름변경 d=폐지 (엔터=뒤로)")
                if op == "a":
                    app.add_dept(a, _ask("코드"), _ask("이름"))
                elif op == "r":
                    app.dept_rename(a, _ask("코드").upper(), _ask("새 이름"))
                elif op == "d":
                    app.dept_set_active(a, _ask("코드").upper(), False)
            elif ch == "7":
                for k, v in app.positions(True).items():
                    print(f"{k:<8} {v['name']} 서열{v['rank']} {v['scope']}{' 부서장열람' if v['dept_head'] else ''}")
                if _ask("a=추가 (엔터=뒤로)") == "a":
                    print(app.pos_add(a, _ask("코드(영문)"), _ask("이름"), int(_ask("서열(숫자, 클수록 높음)")),
                                      _ask("범위 dept/org", "dept")))
            elif ch == "8":
                for r in app.policy_overview(a):
                    print(f"{r['code']:<8} {r['name']:<10} {'v' + str(r['version']) if r['version'] else '규정 없음'}")
                d = _ask("자세히 볼 부서(엔터=뒤로)")
                if d:
                    print(fmt_policy(app.policy_get(a, d)))
            elif ch == "9":
                d = _ask("부서코드").upper()
                g = app.policy_get(a, d)
                print(fmt_policy(g))
                t = pol.type_code(_ask("문서종류 일반/지출/휴가", "일반"))
                cur = g["text"][t]
                r = app.policy_update_type(a, d, t, _ask("결재선 (예: 팀장?, 부서장, 대표)", cur["line"]),
                                           _ask("전결 (예: 부서장:100만)", cur["jeon"]) or "",
                                           _ask("추가결재자 허용 y/n", "y" if cur["allow_extra"] else "n") == "y",
                                           _ask("필수참조 아이디", cur["cc"]) or "")
                print(f"2인 승인 대기 #{r['pending']}" if r.get("pending") else f"적용 v{r['version']}")
            elif ch == "P":
                print(fmt_preview(app.preview_line(a, _ask("종류", "지출"), _ask("금액", "150만"), _ask("부서코드"))))
            elif ch == "H":
                for x in app.policy_history(a, _ask("부서코드")):
                    print(f"v{x['version']} {x['created'][:16]} {x['created_by']}\n{x['diff']}\n")
            elif ch == "C":
                for r in app.change_list(a):
                    print(f"#{r['id']} {r['requested_by']}: {r['summary']}")
                cid = _ask("처리할 번호(엔터=뒤로)")
                if cid:
                    _confirm_password(app, a)
                    print(app.change_decide(a, int(cid), _ask("승인 y/거절 n", "n") == "y"))
            elif ch == "S":
                for k, v in app.settings(a).items():
                    print(f"{k}: {_sv(v['value'])} ({v['label']})")
                k = _ask("바꿀 항목(엔터=뒤로)")
                if k:
                    print(app.setting_set(a, k, _ask("값(on/off, 자릿수 2/3)")))
            elif ch == "V":
                v = app.verify_audit(a)
                print("감사로그 정상" if v["ok"] else "변조 감지!\n" + "\n".join(v["errors"]))
        except (core.GyeolError, ValueError) as e:
            print("오류:", e)


def _form_cmd(args, app, a, out):
    from .formdef import FIELD_TYPES
    sub = args.sub
    if sub == "list":
        rows = app.forms_list(a, all_=args.all)
        return out(args, rows, "\n".join(
            f"{'★' if r['fav'] else ' '} {r['code']:<12} {r['name']:<14} [{r['category']}] {pol.DOC_TYPES[r['doc_type']]}"
            f" 보존 {r['retention']} v{r['version']}{'' if r['enabled'] else ' (사용중지)'}"
            f"{' 부서 ' + ','.join(r['depts']) if r['depts'] else ''}" for r in rows) or "(없음)")
    if sub == "show":
        d = app.form_get(a, args.code, args.version)
        lines = [f"{d['name']} ({d['code']} v{d['version']}) [{d['category']}] {pol.DOC_TYPES[d['doc_type']]} "
                 f"보존 {d['retention']}" + (f" 접두어 {d['prefix']}" if d["prefix"] else ""),
                 f"사용 부서: {','.join(d['depts']) or '전체'} · 필수 참조 부서: {','.join(d['cc_depts']) or '-'}"
                 + (f" · 금액 = {d['amount']}" if d["amount"] else "")]
        for f in d["fields"]:
            extra = (" " + "/".join(f["options"])) if f.get("options") else ""
            if f["type"] == "table":
                extra = " 열: " + ", ".join(c["key"] + ("=" + c["calc"] if c.get("calc") else "") for c in f["columns"]) \
                    + (" 합계: " + ",".join(f["sum"]) if f.get("sum") else "")
            lines.append(f"  {f['key']:<10} {f['label']} · {FIELD_TYPES[f['type']]}{' *필수' if f['required'] else ''}{extra}")
        return out(args, d, "\n".join(lines))
    if sub in ("add", "edit"):
        d = json.loads(Path(args.file).read_text(encoding="utf-8"))
        r = app.form_save(a, args.code, d, new=sub == "add")
        return out(args, r, f"{r['code']} v{r['version']}" + ("" if r["changed"] else " (변경 없음)"))
    if sub == "copy":
        r = app.form_copy(a, args.src, args.code, args.name)
        return out(args, r, f"{r['code']} v{r['version']} 만듦")
    if sub in ("disable", "enable"):
        app.form_enable(a, args.code, sub == "enable")
        return out(args, {"ok": True}, "사용" if sub == "enable" else "사용 중지")
    if sub == "versions":
        rows = app.form_versions(a, args.code)
        return out(args, rows, "\n".join(f"v{r['version']} {r['created'][:16]} {r['actor']}" for r in rows))
    if sub == "fav":
        app.form_fav(a, args.code, not args.off)
        return out(args, {"ok": True}, "즐겨찾기 해제" if args.off else "즐겨찾기")
