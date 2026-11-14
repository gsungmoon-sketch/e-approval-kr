"""gyeol CLI: 문서 분류·보관(부서 분류·사업 문서함·내 폴더 태그). 웹(web_filing.py)과 같은 core(filing.py) 함수만 부른다
— 권한 검사·감사로그(dclass_add/rename/delete, reclass, project_create/update)도 그쪽에서 그대로."""
from __future__ import annotations

from .cli_admin import NOT_MINE


def _split(s):
    return None if s is None else [x.strip() for x in s.replace(" ", ",").split(",") if x.strip()]


def add_filing_parsers(sp):
    s = sp.add_parser("dclass", help="부서 분류(문서마다 0~1개)")
    ss = s.add_subparsers(dest="sub", required=True)
    x = ss.add_parser("list", help="부서 분류 나무(문서 수 포함). 다른 부서는 관리자만")
    x.add_argument("--dept", help="부서 코드(기본: 내 부서)")
    x = ss.add_parser("add", help="분류 추가(그 부서 부서장급·관리자)"); x.add_argument("name")
    x.add_argument("--parent", type=int, help="상위 분류 id"); x.add_argument("--dept", help="부서 코드(기본: 내 부서)")
    x = ss.add_parser("rename", help="분류 이름 바꾸기"); x.add_argument("id", type=int); x.add_argument("name")
    x = ss.add_parser("delete", help="분류 삭제(그 분류 문서는 미분류가 됨)"); x.add_argument("id", type=int)
    x = ss.add_parser("get", help="문서의 분류"); x.add_argument("doc", type=int)
    x = ss.add_parser("set", help="문서의 분류 바꾸기(기안 중엔 기안자, 그 뒤엔 부서장급·문서관리 담당·관리자)")
    x.add_argument("doc", type=int); x.add_argument("dclass", metavar="CLASS", help="분류 id 또는 none(미분류)")

    s = sp.add_parser("project", help="사업 문서함(부서를 가로지르는 묶음, 문서마다 0~N개)")
    ss = s.add_subparsers(dest="sub", required=True)
    x = ss.add_parser("list", help="참여 중인 사업"); x.add_argument("--all", action="store_true", help="전체(관리자)")
    x.add_argument("--status", choices=["open", "closed"])
    x = ss.add_parser("add", help="사업 만들기(관리자·부서장급)"); x.add_argument("name")
    x.add_argument("--code", help="사업 코드(영문·숫자·_.- 20자)")
    x.add_argument("--depts", help="참여 부서 코드 a,b"); x.add_argument("--users", help="참여자 아이디 a,b")
    x = ss.add_parser("show", help="사업과 그 문서(내가 원래 볼 수 있는 문서만)"); x.add_argument("id", type=int)
    x = ss.add_parser("edit", help="이름·코드 바꾸기(만든 사람·관리자)"); x.add_argument("id", type=int)
    x.add_argument("--name"); x.add_argument("--code", help="빈 값('')이면 코드 지움")
    x = ss.add_parser("members", help="참여자 바꾸기(준 쪽만 전체 교체, 빈 값('')=비움)")
    x.add_argument("id", type=int); x.add_argument("--depts", help="참여 부서 코드 a,b")
    x.add_argument("--users", help="참여자 아이디 a,b")
    x = ss.add_parser("status", help="진행(open)/종료(closed)"); x.add_argument("id", type=int)
    x.add_argument("status", choices=["open", "closed"])
    x = ss.add_parser("tag", help="문서를 사업에 넣기(문서 분류 권한 + 참여 중인 진행 사업)")
    x.add_argument("doc", type=int); x.add_argument("id", type=int, help="사업 id")
    x = ss.add_parser("untag", help="문서를 사업에서 빼기"); x.add_argument("doc", type=int)
    x.add_argument("id", type=int, help="사업 id")


def _fmt_project(p):
    return (f"#{p['id']} {p['name']}{' [' + p['code'] + ']' if p.get('code') else ''} ({p['status_ko']}) "
            f"부서 {','.join(p['depts']) or '-'} · 참여자 {','.join(p['users']) or '-'} · 만든 사람 {p['created_by']}")


def _dclass_id(v):
    v = str(v).strip().lower()
    if v in ("none", "-", "0", ""):
        return None
    if not v.isdigit():
        from . import core
        raise core.Invalid("분류는 id(dclass list) 또는 none")
    return int(v)


def project_tag(app, a, doc, pid, on=True):
    """사업 태그 하나 넣기/빼기 = 문서 화면 '분류 저장'과 같은 doc_reclass(사업 목록 전체 교체)."""
    cur = [p["id"] for p in app.doc_tags(a, doc)["projects"]]
    new = [x for x in cur if x != pid] + ([pid] if on else [])
    ch = app.doc_reclass(a, doc, projects=new)
    return {"doc": doc, "projects": [p["id"] for p in app.doc_tags(a, doc)["projects"]], "changed": ch}


def folder_tag(app, a, doc, fid, on=True):
    """내 폴더 태그 하나 넣기/빼기 = 문서 화면 '폴더 저장'과 같은 doc_set_folders(전체 교체)."""
    cur = [f["id"] for f in app.doc_tags(a, doc)["folders"]]
    new = [x for x in cur if x != fid] + ([fid] if on else [])
    app.doc_set_folders(a, doc, new)
    return {"doc": doc, "folders": [f["id"] for f in app.doc_tags(a, doc)["folders"]]}


def run_filing(args, app, a, out):
    c, s = args.cmd, getattr(args, "sub", None)
    if c == "dclass":
        if s == "list":
            rows = app.dclass_list(a, args.dept, counts=True)
            return out(args, rows, "\n".join(f"{r['id']:>5} {'  ' * r['depth']}{r['name']} ({r['count']})"
                                             for r in rows) or "(없음)")
        if s == "add":
            r = app.dclass_add(a, args.name, args.parent, args.dept)
            return out(args, r, f"분류 추가 #{r['id']} {r['dept']} {r['name']}")
        if s == "rename":
            return out(args, app.dclass_rename(a, args.id, args.name), "변경됨")
        if s == "delete":
            app.dclass_delete(a, args.id)
            return out(args, {"ok": True}, "분류 삭제(그 분류 문서는 미분류)")
        if s == "get":
            t = app.doc_tags(a, args.doc)
            r = {"doc": args.doc, "dclass": t["dclass"], "can_reclass": t["can_reclass"]}
            return out(args, r, f"#{args.doc} 분류: " + (f"{t['dclass']['name']} (id {t['dclass']['id']})"
                                                         if t["dclass"] else "(미분류)"))
        ch = app.doc_reclass(a, args.doc, dclass=_dclass_id(args.dclass))
        t = app.doc_tags(a, args.doc)
        return out(args, {"doc": args.doc, "dclass": t["dclass"], "changed": ch},
                   f"#{args.doc} 분류: {t['dclass']['name'] if t['dclass'] else '(미분류)'}"
                   + ("" if ch else " (변경 없음)"))
    if c == "project":
        if s == "list":
            rows = app.project_list(a, args.status, all_=args.all)
            return out(args, rows, "\n".join(_fmt_project(p) for p in rows) or "(없음)")
        if s == "add":
            p = app.project_create(a, args.name, args.code, _split(args.depts) or (), _split(args.users) or ())
            return out(args, p, "만듦 " + _fmt_project(p))
        if s == "show":
            r = app.project_docs(a, args.id)
            from .cli import fmt_rows
            return out(args, r, _fmt_project(r["project"]) + "\n" + fmt_rows(r["docs"]))
        if s in ("edit", "members", "status"):
            from .filing import KEEP
            kw = {}
            if s == "edit":
                kw = {"name": args.name, "code": KEEP if args.code is None else args.code}
            elif s == "members":
                kw = {"depts": _split(args.depts), "users": _split(args.users)}
            else:
                kw = {"status": args.status}
            p = app.project_update(a, args.id, **kw)
            return out(args, p, "저장 " + _fmt_project(p))
        r = project_tag(app, a, args.doc, args.id, on=s == "tag")
        return out(args, r, f"#{args.doc} 사업: {','.join(map(str, r['projects'])) or '(없음)'}")
    return NOT_MINE
