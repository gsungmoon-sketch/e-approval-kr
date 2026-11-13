"""gyeol CLI. `python -m gyeoljae <명령>`. 모든 기능은 core.App 호출.

CLI는 데이터 디렉터리에 접근 가능한 로컬 운영자용 도구다. --user(또는 GYEOLJAE_USER)로 행위자를 지정하며
권한 검사는 그 사용자 기준으로 core가 수행한다(데이터 파일 접근권 = 시스템 관리자 신뢰 경계).
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path

from . import core
from . import policy as pol
from .core import App, BOX_KO, BOXES, BOARDS, GyeolError
from .cli_admin import NOT_MINE, add_admin_parsers, admin_menu, run_admin, setup


def _utf8_console():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def out(args, data, text=None):
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=1, default=str))
    else:
        print(text if text is not None else data)


def _pw(args, prompt="비밀번호: "):
    if getattr(args, "password_stdin", False):
        return sys.stdin.readline().rstrip("\r\n")
    p1 = getpass.getpass(prompt)
    if getpass.getpass("확인: ") != p1:
        raise core.Invalid("비밀번호 확인 불일치")
    return p1


def _actor(args):
    u = args.user or os.environ.get("GYEOLJAE_USER")
    if not u:
        raise core.Invalid("--user 또는 GYEOLJAE_USER 필요")
    return u


def fmt_rows(rows):
    if not rows:
        return "(없음)"
    lines = [f"{'ID':>5} {'상태':<4} {'문서번호':<16} {'기안자':<8} {'작성일':<10} 제목"]
    for r in rows:
        lines.append(f"{r['id']:>5} {r['status_ko']:<4} {r.get('number') or '-':<16} "
                     f"{r.get('drafter_name') or r['drafter']:<8} {r['created'][:10]:<10} {r['title']}")
    return "\n".join(lines)


def fmt_doc(m):
    L = [f"#{m['id']} [{m['status_ko']}] {m['title']}  (rev {m.get('rev', 0)})",
         f"종류: {pol.DOC_TYPES.get(m.get('doc_type') or 'general')}"
         + (f"  금액: {pol.fmt_amount(m.get('amount'))}" if m.get("amount") is not None else "")
         + (f"  결재규정: {m['policy']['dept']} v{m['policy']['version']}" if m.get("policy") else "  결재규정: 없음(자유)"),
         f"문서번호: {m.get('number') or '(완료 시 부여)'}   기안: {m['drafter_name']}({m['drafter']}) {m['dept']}  "
         f"작성 {m['created'][:16]}",
         "결재선: " + " → ".join(
             f"{s['name']}{'(전결)' if s['final'] else ''}[{core.STEP_KO[s['status']]}"
             f"{' 대결:' + s['acted_by'] if s.get('proxy_for') else ''}]" for s in m["line"]),
         "참조: " + (", ".join(f"{x}{'(열람)' if x in m.get('reads', {}) else ''}" for x in m["cc"]) or "-"),
         "-" * 60, m.get("body", ""), "-" * 60]
    for a in m["attachments"]:
        L.append(f"첨부 {a['idx']}: {a['name']} ({a['size']} bytes)")
    for h in m.get("history", []):
        L.append(f"  {h['at'][:16]} {h['actor']} {h['action']} {h.get('comment') or ''}")
    return "\n".join(L)


def build_parser():
    p = argparse.ArgumentParser(prog="gyeol", description="gyeoljae 전자결재 CLI")
    p.add_argument("--data", help="데이터 디렉터리 (기본: GYEOLJAE_DATA 또는 OS별 기본)")
    p.add_argument("-u", "--user", help="행위자 아이디 (또는 GYEOLJAE_USER)")
    p.add_argument("--json", action="store_true", help="JSON 출력")
    sp = p.add_subparsers(dest="cmd", required=True)

    s = sp.add_parser("init", help="최초 관리자 생성")
    s.add_argument("--dept", default="ADM"); s.add_argument("--dept-name", default="관리부")
    s.add_argument("--admin", default="admin"); s.add_argument("--name", default="관리자")
    s.add_argument("--password-stdin", action="store_true")

    s = sp.add_parser("setup", help="최초 설정: 첫 관리자 만들기(옵션을 주면 비대화형)")
    s.add_argument("--admin-id"); s.add_argument("--admin-name"); s.add_argument("--dept-code")
    s.add_argument("--dept-name")
    s.add_argument("--password-file", help="첫 줄이 비밀번호인 파일('-' = 표준입력). 주면 묻지 않음")
    s = sp.add_parser("seed-demo", help="시험용 데모 데이터(빈 데이터에만, 비밀번호 demo1234!)")
    s = sp.add_parser("backup", help="백업 zip 만들기(실행 중에도 일관)"); s.add_argument("out")
    s = sp.add_parser("restore", help="백업 zip → 빈 데이터 디렉터리로 복원"); s.add_argument("zip")
    s.add_argument("--to", required=True, help="복원할 (빈) 데이터 디렉터리")
    sp.add_parser("export-audit-head", help="감사체인 머리값 출력(외부 보관용)")
    s = sp.add_parser("check-audit-head", help="보관한 머리값이 체인에 그대로 있는지 확인")
    s.add_argument("seq", type=int); s.add_argument("hash")
    add_admin_parsers(sp)

    s = sp.add_parser("delegate", help="대결(결재 위임)")
    ss = s.add_subparsers(dest="sub", required=True)
    x = ss.add_parser("set"); x.add_argument("--to", required=True); x.add_argument("--start")
    x.add_argument("--end"); x.add_argument("--from-user")
    ss.add_parser("list")
    x = ss.add_parser("revoke"); x.add_argument("id", type=int)

    s = sp.add_parser("draft", help="기안 작성 (--submit 시 바로 상신)")
    s.add_argument("--title", required=True); s.add_argument("--body"); s.add_argument("--body-file")
    s.add_argument("--line", default="", help="결재선 a,b!,c ('!'=전결). 결재규정 부서는 '추가 결재자'만")
    s.add_argument("--type", default="일반", help="문서 종류 일반/지출/휴가")
    s.add_argument("--amount", help="금액(지출 필수) 예: 150만")
    s.add_argument("--cc", default=""); s.add_argument("--attach", nargs="*", default=[])
    s.add_argument("--dept-open", action="store_true", help="완료 후 부서원 열람 허용")
    s.add_argument("--submit", action="store_true")
    _form_args(s)

    s = sp.add_parser("edit", help="기안 수정(기안/회수/반려 상태)")
    s.add_argument("id", type=int); s.add_argument("--title"); s.add_argument("--body"); s.add_argument("--body-file")
    s.add_argument("--line"); s.add_argument("--cc"); s.add_argument("--attach", nargs="*", default=[])
    s.add_argument("--remove", nargs="*", type=int, default=[])
    s.add_argument("--type"); s.add_argument("--amount")
    _form_args(s, edit=True)

    for name, hlp in (("submit", "상신"), ("withdraw", "회수")):
        s = sp.add_parser(name, help=hlp); s.add_argument("id", type=int)
    s = sp.add_parser("approve", help="결재(승인)"); s.add_argument("id", type=int); s.add_argument("-m", "--comment", default="")
    s.add_argument("--jeon", action="store_true", help="전결")
    s.add_argument("--rev", type=int, help="본 문서 버전(show의 rev). 다르면 거부")
    s = sp.add_parser("reject", help="반려"); s.add_argument("id", type=int); s.add_argument("-m", "--comment", required=True)
    s.add_argument("--rev", type=int)

    s = sp.add_parser("list", help="문서함"); s.add_argument("--box", default="inbox", choices=BOXES)
    s = sp.add_parser("show", help="문서 보기"); s.add_argument("id", type=int)
    s.add_argument("--file", type=int, help="첨부 번호 저장"); s.add_argument("-o", "--output")
    s = sp.add_parser("search", help="검색")
    s.add_argument("-q", "--query"); s.add_argument("--title"); s.add_argument("--drafter"); s.add_argument("--dept")
    s.add_argument("--status", choices=list(core.STATUS_KO)); s.add_argument("--from", dest="date_from")
    s.add_argument("--to", dest="date_to"); s.add_argument("--number"); s.add_argument("--type")
    s = sp.add_parser("number", help="문서번호로 찾기"); s.add_argument("number")

    s = sp.add_parser("board", help="게시판")
    ss = s.add_subparsers(dest="sub", required=True)
    x = ss.add_parser("list"); x.add_argument("--board", default="notice", choices=list(BOARDS))
    x = ss.add_parser("post"); x.add_argument("--board", default="notice", choices=list(BOARDS))
    x.add_argument("--title", required=True); x.add_argument("--body", required=True)
    x = ss.add_parser("read"); x.add_argument("id", type=int)

    s = sp.add_parser("msg", help="쪽지")
    ss = s.add_subparsers(dest="sub", required=True)
    x = ss.add_parser("list"); x.add_argument("--unread", action="store_true"); x.add_argument("--sent", action="store_true")
    x = ss.add_parser("send"); x.add_argument("to"); x.add_argument("text")
    x = ss.add_parser("read"); x.add_argument("id", type=int)

    s = sp.add_parser("audit", help="감사로그(관리자)"); s.add_argument("--doc", type=int); s.add_argument("--limit", type=int, default=50)
    sp.add_parser("verify-audit", help="감사로그 해시체인·완료문서 변조 검증")
    sp.add_parser("menu", help="BBS식 대화형 메뉴")
    s = sp.add_parser("serve", help="웹 서버")
    s.add_argument("--host", help="기본: 데이터 폴더 server.json(최초 설정 마법사에서 고름), 없으면 127.0.0.1(이 PC만)")
    s.add_argument("--port", type=int, help="기본: server.json, 없으면 8130")
    s.add_argument("--https", nargs=2, metavar=("CERT", "KEY"), help="직접 HTTPS(인증서·키 PEM)")
    return p


def _split(s):
    return [x.strip() for x in (s or "").split(",") if x.strip()]


def _body(args):
    if getattr(args, "body_file", None):
        return Path(args.body_file).read_text(encoding="utf-8")
    return args.body


def _files(paths):
    return [(Path(p).name, Path(p).read_bytes()) for p in paths]


def run(args, app: App):
    c = args.cmd
    if c == "init":
        pw = _pw(args)
        app.bootstrap(args.dept, args.dept_name, args.admin, args.name, pw)
        return out(args, {"ok": True}, f"초기화 완료: 관리자 {args.admin}")
    if c == "setup":
        return setup(args, app)
    if c == "seed-demo":
        r = app.seed_demo()
        return out(args, r, f"데모 생성: {' '.join(r['accounts'])} / 비밀번호 {r['password']} (시험 전용)")
    if c == "backup":
        r = app.backup(args.out)
        return out(args, r, f"백업: {r['file']} ({r['size']:,} bytes)")
    if c == "restore":
        r = App.restore(args.zip, args.to)
        out(args, r, f"복원: {r['data']}  감사검증 {'OK' if r['verify']['ok'] else '오류: ' + '; '.join(r['verify']['errors'])}")
        return 0 if r["verify"]["ok"] else 3
    if c == "export-audit-head":
        r = app.audit_head()
        return out(args, r, json.dumps(r, ensure_ascii=False))
    if c == "check-audit-head":
        ok = app.check_audit_head(args.seq, args.hash)
        out(args, {"ok": ok}, "OK: 체인에 그대로 있음" if ok else "불일치: 체인이 재작성되었거나 값이 틀림")
        return 0 if ok else 3
    if c == "serve":
        from .web import serve
        conf = app.server_conf()
        host = args.host or conf["host"]
        port = args.port or conf["port"]
        if not app.has_users():
            from .web_setup import announce
            announce(port)
        return serve(app, host, port, tuple(args.https) if args.https else None)
    a = _actor(args)
    rc = run_admin(args, app, a, out)
    if rc is not NOT_MINE:
        return rc
    if c == "delegate":
        if args.sub == "set":
            r = app.delegate_set(a, args.to, args.start, args.end, args.from_user)
            return out(args, r, f"대결 위임 #{r['id']}: {r['from_user']} → {r['to_user']} {r['start']}~{r['end']}")
        if args.sub == "revoke":
            app.delegate_revoke(a, args.id)
            return out(args, {"ok": True}, "취소됨")
        rows = app.delegate_list(a)
        return out(args, rows, "\n".join(f"#{r['id']} {r['from_user']}→{r['to_user']} {r['start']}~{r['end']}"
                                         f"{' 활성' if r['active'] else ''}{' 취소' if r['revoked'] else ''}"
                                         for r in rows) or "(없음)")
    if c == "draft":
        m = app.create_draft(a, args.title, _body(args) or "", _split(args.line), _split(args.cc),
                             "dept" if args.dept_open else "private", _files(args.attach), submit=args.submit,
                             doc_type=args.type, amount=args.amount, form=args.form, fields=_cli_fields(args))
        return out(args, m, f"기안 #{m['id']} ({m['status_ko']})"
                   + (f" 양식 {m['form']['name']} v{m['form']['version']}" if m.get("form") else "")
                   + (f" 금액 {m['amount']:,}원" if m.get("amount") is not None else ""))
    if c == "edit":
        m = app.edit_draft(a, args.id, args.title, _body(args), _split(args.line) if args.line else None,
                           _split(args.cc) if args.cc is not None else None, None, _files(args.attach), args.remove,
                           doc_type=args.type, amount=args.amount, fields=_merge_fields(app, a, args))
        return out(args, m, f"수정 #{m['id']}")
    if c in ("submit", "withdraw"):
        m = getattr(app, c)(a, args.id)
        return out(args, m, f"#{m['id']} {m['status_ko']}")
    if c == "approve":
        m = app.approve(a, args.id, args.comment, args.jeon, rev=args.rev)
        return out(args, m, f"#{m['id']} {m['status_ko']} {m.get('number') or ''}")
    if c == "reject":
        m = app.reject(a, args.id, args.comment, rev=args.rev)
        return out(args, m, f"#{m['id']} 반려")
    if c == "list":
        rows = app.list_box(a, args.box)
        return out(args, rows, f"[{BOX_KO[args.box]}]\n" + fmt_rows(rows))
    if c == "show":
        if args.file:
            name, path = app.get_attachment(a, args.id, args.file)
            dest = Path(args.output) if args.output else Path.cwd() / name
            if dest.exists():
                raise core.Invalid(f"이미 있는 파일: {dest}")
            dest.write_bytes(path.read_bytes())
            return out(args, {"saved": str(dest)}, f"저장: {dest}")
        m = app.show(a, args.id)
        return out(args, m, fmt_doc(m))
    if c in ("search", "number"):
        if c == "number":
            rows = app.search(a, number=args.number)
        else:
            rows = app.search(a, q=args.query, title=args.title, drafter=args.drafter, dept=args.dept,
                              status=args.status, date_from=args.date_from, date_to=args.date_to, number=args.number,
                              doc_type=args.type)
        return out(args, rows, fmt_rows(rows))
    if c == "board":
        if args.sub == "post":
            return out(args, app.board_post(a, args.board, args.title, args.body), "게시됨")
        if args.sub == "read":
            r = app.board_read(a, args.id)
            return out(args, r, f"[{BOARDS[r['board']]}] {r['title']}\n{r['author_name']} {r['created'][:16]}\n\n{r['body']}")
        rows = app.board_list(a, args.board)
        return out(args, rows, "\n".join(f"{r['id']:>4} {r['created'][:10]} {r['author_name']:<8} {r['title']}"
                                         for r in rows) or "(없음)")
    if c == "msg":
        if args.sub == "send":
            return out(args, app.msg_send(a, args.to, args.text), "보냄")
        if args.sub == "read":
            r = app.msg_read(a, args.id)
            return out(args, r, f"{r['sender']} → {r['recipient']} {r['created'][:16]}\n{r['body']}")
        rows = app.msg_list(a, args.unread, args.sent)
        return out(args, rows, "\n".join(f"{r['id']:>4} {'*' if not r['read_at'] else ' '} {r['created'][:16]} "
                                         f"{r['sender']}: {r['body'][:60]}" for r in rows) or "(없음)")
    if c == "audit":
        rows = app.audit_list(a, args.doc, args.limit)
        return out(args, rows, "\n".join(f"{e['seq']:>5} {e['ts'][:19]} {e['actor']:<10} {e['action']:<14} "
                                         f"{e.get('doc') or ''} {json.dumps(e['detail'], ensure_ascii=False)}"
                                         for e in rows))
    if c == "verify-audit":
        r = app.verify_audit(a)
        out(args, r, ("OK" if r["ok"] else "변조 감지!") + f" entries={r['entries']}\n" + "\n".join(r["errors"]))
        return 0 if r["ok"] else 3
    if c == "menu":
        return menu(app, a)


def menu(app: App, a):
    """옛 BBS 스타일 메뉴."""
    u = app.get_user(a)
    while True:
        cnt = app.box_counts(a)
        print(f"\n=== 결재 BBS === {u['name']}({a}) {u['dept']}  쪽지 {app.unread_count(a)}통")
        print(f" 1.결재대기({cnt['inbox']})  2.기안함({cnt['drafts']})  3.진행  4.완료  5.반려  6.참조({cnt['cc']})")
        print(" 7.검색  8.쪽지  9.공지  O.조직도" + ("  A.관리" if u["role"] == "admin" else "") +
              "  0.종료   (문서 열기: #12)")
        try:
            ch = input("선택> ").strip()
        except EOFError:
            return 0
        try:
            if ch in ("0", "q", "exit"):
                return 0
            if ch.startswith("#"):
                m = app.show(a, int(ch[1:]))
                print(fmt_doc(m))
                if m["can"]["approve"]:
                    act = input("[a]승인 [r]반려 [Enter]넘어감> ").strip().lower()
                    if act == "a":
                        print(app.approve(a, m["id"], input("의견> "))["status_ko"])
                    elif act == "r":
                        app.reject(a, m["id"], input("반려 사유> "))
                        print("반려됨")
            elif ch in "123456" and len(ch) == 1:
                box = ["inbox", "drafts", "progress", "done", "rejected", "cc"][int(ch) - 1]
                print(fmt_rows(app.list_box(a, box)))
            elif ch == "7":
                print(fmt_rows(app.search(a, q=input("검색어> "))))
            elif ch == "8":
                for r in app.msg_list(a, limit=20):
                    print(f"{'*' if not r['read_at'] else ' '} {r['created'][5:16]} {r['sender']}: {r['body']}")
                app.msg_read_all(a)
            elif ch.upper() == "O":
                for r in app.org_directory(a, q=input("이름/부서/직위(엔터=전체)> ").strip() or None):
                    print(f"{r['dept_name']:<8} {r['position_name'] or '-':<4} {r['name']} ({r['username']})")
            elif ch.upper() == "A" and u["role"] == "admin":
                admin_menu(app, a)
            elif ch == "9":
                for r in app.board_list(a, "notice", 20):
                    print(f"{r['id']:>4} {r['created'][:10]} {r['title']}")
        except (GyeolError, ValueError) as e:
            print("오류:", e)


def _form_args(s, edit=False):
    if not edit:
        s.add_argument("--form", help="양식 코드(gyeoljae form list). 예: expense, leave")
    s.add_argument("--field", action="append", default=[], metavar="키=값",
                   help="양식 항목. 기간은 2026-11-16~2026-11-18")
    s.add_argument("--row", action="append", default=[], metavar="표키:열=값;열=값",
                   help="표 행 추가. 예: items:item=모니터;qty=2;price=30만")
    s.add_argument("--fields-json", help="항목 전체를 JSON 파일로")


def _cli_fields(args):
    if not (args.field or args.row or args.fields_json):
        return None
    vals = {}
    if args.fields_json:
        vals = json.loads(Path(args.fields_json).read_text(encoding="utf-8"))
        if not isinstance(vals, dict):
            raise core.Invalid("--fields-json 은 객체")
    for kv in args.field:
        k, sep, v = kv.partition("=")
        if not sep:
            raise core.Invalid(f"--field 형식은 키=값: {kv}")
        vals[k.strip()] = v
    for r in args.row:
        k, sep, rest = r.partition(":")
        if not sep:
            raise core.Invalid(f"--row 형식은 표키:열=값;열=값 : {r}")
        row = {}
        for cell in rest.split(";"):
            ck, _, cv = cell.partition("=")
            if ck.strip():
                row[ck.strip()] = cv
        vals.setdefault(k.strip(), [])
        if isinstance(vals[k.strip()], dict):
            vals[k.strip()] = vals[k.strip()].get("rows", [])
        vals[k.strip()].append(row)
    return vals


def _merge_fields(app, a, args):
    new = _cli_fields(args)
    if new is None:
        return None
    old = dict(app.show(a, args.id, mark_read=False).get("fields") or {})
    old.update(new)
    return old


def main(argv=None):
    _utf8_console()
    args = build_parser().parse_args(argv)
    try:
        app = App(args.data)
        rc = run(args, app)
        return rc if isinstance(rc, int) else 0
    except GyeolError as e:
        if args.json:
            print(json.dumps({"error": e.code, "message": str(e)}, ensure_ascii=False))
        else:
            print(f"오류: {e}", file=sys.stderr)
        return 2
    except FileNotFoundError as e:
        print(f"오류: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
