"""홈 대시보드·메뉴 틀(왼쪽 메뉴/모바일 아래 탭)·문서함(부서·내 보관함)·자료실·설정 화면.
화면 시나리오: docs/ui-home.md. 모든 동작은 core(App) 함수 호출."""
from __future__ import annotations

import re
import urllib.parse

from . import core
from . import policy as pol
from .core_base import BOX_KO
from .web import Resp, esc, redirect
from .web_ui import chip

BOX_TABS = ["inbox", "progress", "done", "rejected", "cc", "drafts"]


def _section(route):
    for pre, sec in (("/box", "box"), ("/doc/", "box"), ("/new", "box"), ("/docs", "docs"), ("/board", "board"),
                     ("/files", "files"), ("/mail", "mail"), ("/msg", "msg"), ("/org", "org"), ("/settings", "settings"),
                     ("/me", "settings"), ("/delegate", "settings"), ("/preview", "box"), ("/search", "box"),
                     ("/admin", "admin"), ("/menu", "menu")):
        if route.startswith(pre):
            return sec
    return "home"


def nav_items(app, me):
    """메뉴 트리(docs/ui-layout.md §3): (주소, 이름, 구역, 배지, 하위목록)."""
    b = app.badges(me["username"])
    u = me["username"]
    rej = len([r for r in app.list_box(u, "rejected") if r["drafter"] == u])
    cc = len(app.list_box(u, "cc"))
    items = [("/", "홈", "home", 0, []),
             ("/box/inbox", "결재", "box", b["inbox"], [
                 ("/box/inbox", "결재대기", b["inbox"]), ("/box/progress", "결재진행", 0), ("/box/done", "결재완료", 0),
                 ("/box/rejected", "반려", rej), ("/box/cc", "참조/열람", cc), ("/box/drafts", "기안함·임시저장", 0),
                 ("/box/dept", "부서문서함", 0), ("/new", "✍ 기안하기", 0)]),
             ("/msg", "소통", "talk", b["messages"] + b.get("mail", 0), [
                 ("/msg", "쪽지", b["messages"]), ("/mail", "메일", b.get("mail", 0)), ("/board/notice", "게시판", 0),
                 ("/files", "자료실", 0)]),
             ("/docs", "내 보관함", "docs", 0, []), ("/org", "조직도", "org", 0, []),
             ("/settings", "설정", "settings", 0, [("/me", "비밀번호", 0), ("/delegate", "대결자 지정", 0),
                                                 ("/preview", "내 결재선 미리보기", 0)])]
    if me["role"] == "admin":
        items.append(("/admin", "관리", "admin", 0, [("/admin/emp", "사원", 0), ("/admin/dept", "부서", 0),
                                                     ("/admin/pos", "직위·서열", 0), ("/admin/policy", "규정·전결", 0), ("/admin/forms", "양식", 0),
                                                     ("/admin/changes", "변경요청·설정", 0), ("/admin/smtp", "외부 메일", 0),
                                                     ("/admin/audit", "감사로그", 0)]))
    return items, b


def _badge(n):
    return f" <span class=badge aria-label='{n}건'>{n}</span>" if n else ""


def _cur(h, href):
    path = h.path.split("#")[0]
    return path == href or (href != "/" and "?" not in href and h.route == href)


def shell(h, app, me, body, box=None):
    sec = _section(h.route)
    if sec in ("msg", "mail", "board", "files"):
        sec = "talk"
    items, b = nav_items(app, me)
    side = ""
    for u, t, s, n, kids in items:
        on = s == sec
        side += (f"<li><a href='{u}' class='top{' on' if on else ''}'"
                 f"{' aria-current=page' if _cur(h, u) else ''}>{t}{_badge(n) if not (on and kids) else ''}</a>")
        if kids and on:
            side += "<ul>" + "".join(f"<li><a href='{ku}'{' aria-current=page class=on' if _cur(h, ku) else ''}>"
                                     f"{kt}{_badge(kn)}</a></li>" for ku, kt, kn in kids) + "</ul>"
        side += "</li>"
    tabs = [("/", "홈", "home", 0), ("/box/inbox", "결재", "box", b["inbox"]), ("/new", "기안", "new", 0),
            ("/msg", "쪽지", "msgtab", b["messages"]), ("/menu", "더보기", "menu", 0)]
    cur_tab = "new" if h.route.startswith("/new") else ("msgtab" if h.route.startswith("/msg") else
                                                         (sec if sec in ("home", "box", "menu") else ""))
    bottom = "".join(f"<a href='{u}'{' aria-current=page class=on' if s == cur_tab else ''}>{t}{_badge(n)}</a>"
                     for u, t, s, n in tabs)
    top = (f"<a href='#main' class=skip>본문으로 건너뛰기</a><header class=top><a href='/' class=brand>결재</a>"
           f"<form method=get action='/search' class=qs role=search><input type=search name=q placeholder='통합검색' "
           f"aria-label='통합검색'></form><span class=me><a href='/msg'>쪽지{_badge(b['messages'])}</a> "
           f"<a href='/mail'>메일{_badge(b.get('mail', 0))}</a> <span class=who>{esc(me['name'])}</span> "
           f"<form class=inline method=post action='/logout'>{h.csrf()}<button class=sec>로그아웃</button></form></span></header>")
    aside = getattr(h, "_aside", None)
    return (f"{top}<div class='layout{' has-aside' if aside else ''}'><nav class=side aria-label='주 메뉴'><ul>{side}</ul></nav>"
            f"<main id=main tabindex=-1>{body}</main>{'<aside class=right>' + aside + '</aside>' if aside else ''}</div>"
            f"<nav class=bottom aria-label='하단 메뉴'>{bottom}</nav>")


def _doc_items(rows, extra):
    if not rows:
        return "<p class=mut>없음</p>"
    return "<ul class=items>" + "".join(
        f"<li><a href='/doc/{r['id']}'>{esc(r['title'])}</a> <span class=mut>{extra(r)}</span></li>" for r in rows) + "</ul>"


def _card(title, href, card, extra, empty_hint=""):
    more = f"<a href='{href}' class=more>전체 보기({card['count']})</a>" if card["count"] > len(card["items"]) else ""
    body = _doc_items(card["items"], extra) if card["items"] else f"<p class=mut>없음{empty_hint}</p>"
    return (f"<section class='card dash'><h3><a href='{href}'>{title}</a> <span class=num>{card['count']}</span></h3>"
            f"{body}{more}</section>")


def home_page(h, app, user):
    d = app.home(user)
    no = d["notices"]
    nl = "".join(f"<li>{'📌 ' if p['pinned'] else ''}<a href='/board/notice/{p['id']}'>{esc(p['title'])}</a>"
                 f"{' <span class=new>N</span>' if p['new'] else ''} <span class=mut>{esc(p['created'][5:10])}</span></li>"
                 for p in no["items"]) or "<li class=mut>공지가 없습니다</li>"
    types = "".join(f"<a href='/new?type={k}' class=btn>{v}</a>" for k, v in pol.DOC_TYPES.items())
    cards = (
        _card("결재대기", "/box/inbox", d["inbox"],
              lambda r: f"{esc(r['drafter_name'])} · {r['waiting_days']}일째" if r["waiting_days"] else
              f"{esc(r['drafter_name'])} · 오늘", " — 내 차례인 문서가 없습니다")
        + _card("결재진행", "/box/progress", d["progress"], lambda r: f"현재 {esc(r.get('cur_name') or '-')}")
        + _card("결재완료", "/box/done", d["done"], lambda r: esc(r.get("number") or ""))
        + _card("반려", "/box/rejected", d["rejected"], lambda r: "다시 올리기", " — 다시 올릴 문서가 없습니다"))
    empty = ""
    if not any(d[k]["count"] for k in ("inbox", "progress", "done", "rejected")) and not d["drafts"]:
        empty = ("<p class='card hint'>아직 문서가 없습니다. 위의 <b>기안하기</b>에서 양식을 골라 첫 문서를 올려 보세요. "
                 "결재선은 부서 결재규정에 따라 자동으로 정해집니다.</p>")
    body = (f"<section class=card><h3><a href='/board/notice'>공지사항</a> <span class=mut>새 글 {no['new']}</span></h3>"
            f"<ul class=items>{nl}</ul></section>"
            f"<section class='card quick'><span class=lbl>✍ 기안하기</span> {types}"
            f"<form method=get action='/search' class=inline role=search><input type=text name=q placeholder='제목·본문 검색'>"
            f" <button class=sec>찾기</button></form></section>{empty}<div class=grid>{cards}</div>"
            f"<p class=mut><a href='/msg'>쪽지 안 읽음 {d['unread_messages']}</a>"
            f"{' · <a href=/mail>메일 안 읽음 ' + str(d['unread_mail']) + '</a>' if d.get('unread_mail') is not None else ''}"
            f" · <a href='/box/cc'>참조 {d['cc']}</a> · <a href='/box/drafts'>기안함·임시저장 {d['drafts']}</a></p>")
    return h.ok("홈", body)


def menu_page(h, app, me):
    items, _ = nav_items(app, me)
    out = ""
    for u, t, s, n, kids in items:
        links = "".join(f"<li><a href='{a}'>{b}{_badge(k)}</a></li>" for a, b, k in kids)
        out += f"<section class=card><h3><a href='{u}'>{t}</a>{_badge(n)}</h3>{'<ul class=menu>' + links + '</ul>' if links else ''}</section>"
    return h.ok("더보기", f"<h2>전체 메뉴</h2>{out}")


def docs_page(h, app, user, p):
    f = h.form
    m = re.fullmatch(r"/docs/folder/(\d{1,9})(?:/(add|remove|rename|delete))?", p)
    if p == "/docs" and h.method == "POST":
        app.folder_create(user, f.get("name"))
        return redirect("/docs")
    if m:
        fid, act = int(m.group(1)), m.group(2)
        if act and h.method == "POST":
            if act == "add":
                app.folder_add(user, fid, int(f.get("doc", "0") or 0))
                return redirect(f"/doc/{int(f.get('doc', '0') or 0)}")
            if act == "remove":
                app.folder_remove(user, fid, int(f.get("doc", "0") or 0))
            elif act == "rename":
                app.folder_rename(user, fid, f.get("name"))
            elif act == "delete":
                app.folder_delete(user, fid)
                return redirect("/docs")
            return redirect(f"/docs/folder/{fid}")
        if act:
            raise core.NotFound("없는 동작")
        r = app.folder_docs(user, fid)
        c = h.csrf()
        rows = "".join(f"<tr><td><a href='/doc/{d['id']}'>{esc(d['title'])}</a> <span class=mut>{esc(d['status_ko'])} "
                       f"{esc(d.get('number') or '')}</span></td><td><form method=post action='/docs/folder/{fid}/remove' "
                       f"class=inline>{c}<input type=hidden name=doc value='{d['id']}'><button class=sec>빼기</button></form>"
                       f"</td></tr>" for d in r["docs"])
        body = (f"<h2>내 보관함 · {esc(r['name'])}</h2><div class=card><table>{rows or '<tr><td class=mut>비어 있음 — 문서 화면에서 [보관함에 담기]</td></tr>'}"
                f"</table></div><form method=post action='/docs/folder/{fid}/rename' class=card>{c}<div class=row>"
                f"<input type=text name=name value='{esc(r['name'])}' aria-label='폴더 이름'><button class=sec>이름 바꾸기</button>"
                f"</div></form><form method=post action='/docs/folder/{fid}/delete' class=card>{c}"
                f"<button class=warn>폴더 삭제</button> <span class=mut>문서 자체는 지워지지 않습니다</span></form>")
        return h.ok("내 보관함", body)
    if p != "/docs":
        raise core.NotFound("페이지 없음")
    rows = app.list_box(user, "dept")
    folders = app.folder_list(user)
    fl = "".join(f"<li><a href='/docs/folder/{x['id']}'>{esc(x['name'])}</a> <span class=mut>{x['count']}</span></li>"
                 for x in folders) or "<li class=mut>폴더가 없습니다</li>"
    body = (f"<h2>문서함</h2><div class=card><h3>부서 문서함</h3><p class=mut>우리 부서의 완료 문서 중 부서 공개 문서"
            f"(부서장급은 전체).</p>{h.table(rows)}</div><div class=card><h3>내 보관함</h3><ul class=items>{fl}</ul>"
            f"<form method=post action='/docs' class=row>{h.csrf()}<input type=text name=name placeholder='새 폴더 이름' "
            f"aria-label='새 폴더 이름' required><button class=sec>만들기</button></form></div>")
    return h.ok("문서함", body)


def folder_picker(h, app, user, did):
    fs = app.folder_list(user)
    if not fs:
        return "<p class=mut><a href='/docs'>내 보관함</a>에 폴더를 만들면 이 문서를 담을 수 있습니다.</p>"
    opts = "".join(f"<option value='{x['id']}'>{esc(x['name'])}</option>" for x in fs)
    return (f"<form method=post action='/docs/folder/{fs[0]['id']}/add' class='card row'>{h.csrf()}"
            f"<input type=hidden name=doc value='{int(did)}'><select name=folder aria-label='보관함 폴더'>{opts}</select>"
            f"<button class=sec formaction='/docs/folder/pick/add'>보관함에 담기</button></form>")


def files_page(h, app, user, p):
    f = h.form
    if p == "/files":
        scope = "org" if h.qs.get("scope") == "org" else "dept"
        if h.method == "POST":
            if not h.files:
                raise core.Invalid("파일을 선택하세요")
            r = app.files_upload(user, h.files[0][0], h.files[0][1], "org" if f.get("scope") == "org" else "dept",
                                 f.get("note", ""))
            return redirect(f"/files/{r['id']}")
        r = app.files_list(user, scope, q=h.qs.get("q") or None)
        tabs = (f"<nav class=tabs><a href='/files' class='{'on' if scope == 'dept' else ''}'>우리 부서 자료실</a>"
                f"<a href='/files?scope=org' class='{'on' if scope == 'org' else ''}'>전사 자료실</a></nav>")
        rows = "".join(f"<tr><td><a href='/files/{x['id']}'>{esc(x['name'])}</a></td><td>판{x['cur_version']}</td>"
                       f"<td class=hide>{x['size']:,}</td><td class=hide>{esc(x['uploader_name'] or x['uploader'])}</td>"
                       f"<td>{esc(x['updated'][:10])}</td></tr>" for x in r["files"])
        up = ""
        if r["can_write"]:
            up = (f"<form method=post action='/files' enctype='multipart/form-data' class=card>{h.csrf()}"
                  f"<input type=hidden name=scope value='{scope}'><h3>올리기</h3><input type=file name=file required "
                  f"aria-label='올릴 파일'><input type=text name=note placeholder='메모(선택)' maxlength=200>"
                  f"<p class=mut>최대 20MB · 문서/이미지/압축 형식만 · 같은 이름이면 덮어쓰지 않고 새 판</p><button>올리기</button></form>")
        else:
            up = "<p class=mut>이 자료실은 읽기 전용입니다(전사 자료실은 부서장급·관리자가 올립니다).</p>"
        body = (f"<h2>자료실</h2>{tabs}<form method=get action='/files' class='card row'><input type=hidden name=scope "
                f"value='{scope}'><input type=text name=q placeholder='파일 이름' value='{esc(h.qs.get('q'))}'>"
                f"<button class=sec>찾기</button></form><div class=card><table><tr><th>이름</th><th>판</th><th class=hide>크기"
                f"</th><th class=hide>올린 사람</th><th>날짜</th></tr>{rows or '<tr><td class=mut>파일이 없습니다</td></tr>'}"
                f"</table></div>{up}")
        return h.ok("자료실", body)
    m = re.fullmatch(r"/files/(\d{1,9})(?:/(get|version|delete))?", p)
    if not m:
        raise core.NotFound("페이지 없음")
    fid, act = int(m.group(1)), m.group(2)
    if act == "get" and h.method == "GET":
        v = h.qs.get("v")
        name, path = app.files_get(user, fid, int(v) if v and v.isdigit() else None)
        return Resp(200, path.read_bytes(), {"Content-Type": "application/octet-stream", "Content-Disposition":
                                             f"attachment; filename=\"download\"; filename*=UTF-8''{urllib.parse.quote(name)}"})
    if act == "version" and h.method == "POST":
        if not h.files:
            raise core.Invalid("파일을 선택하세요")
        app.files_new_version(user, fid, h.files[0][1], f.get("note", ""))
        return redirect(f"/files/{fid}")
    if act == "delete" and h.method == "POST":
        app.files_delete(user, fid)
        return redirect("/files" + ("?scope=org" if f.get("scope") == "org" else ""))
    if act:
        raise core.NotFound("없는 동작")
    r = app.files_versions(user, fid)
    c = h.csrf()
    vs = "".join(f"<tr><td><a href='/files/{fid}/get?v={v['version']}'>판{v['version']}</a></td>"
                 f"<td>{esc(v['created'][:16])}</td><td>{esc(v['uploader_name'] or v['uploader'])}</td>"
                 f"<td class=hide>{v['size']:,}</td><td>{esc(v['note'] or '')}</td></tr>" for v in r["versions"])
    act_html = ""
    if r["can_write"]:
        act_html += (f"<form method=post action='/files/{fid}/version' enctype='multipart/form-data' class=card>{c}"
                     f"<h3>새 판 올리기</h3><input type=file name=file required aria-label='새 판 파일'>"
                     f"<input type=text name=note placeholder='무엇이 바뀌었나(선택)' maxlength=200><button>올리기</button></form>")
    if r["can_delete"]:
        act_html += (f"<form method=post action='/files/{fid}/delete' class=card>{c}<input type=hidden name=scope "
                     f"value='{esc(r['scope'])}'><button class=warn>삭제(숨김)</button> <span class=mut>이전 판과 기록은 "
                     f"보존됩니다</span></form>")
    body = (f"<h2>{esc(r['name'])}</h2><p><a href='/files/{fid}/get' class=btn>최신판 내려받기</a> "
            f"<span class=mut>{'전사' if r['scope'] == 'org' else esc(r['dept']) + ' 부서'} 자료실</span></p>"
            f"<div class=card><h3>판 이력</h3><table>{vs}</table></div>{act_html}")
    return h.ok(r["name"], body)


def settings_page(h, app, me):
    body = (f"<h2>설정</h2><div class=card><p>{esc(me['name'])} · {esc(me['emp_no'] or '')} · {esc(me['dept'])}</p><ul>"
            f"<li><a href='/me'>비밀번호 변경</a></li><li><a href='/delegate'>대결(결재 위임) 설정</a></li>"
            f"<li><a href='/preview'>내 결재선 미리보기</a></li></ul></div>")
    return h.ok("설정", body)


def route(h, app, user, p):
    """홈 쪽 경로면 Resp, 아니면 None."""
    if p == "/":
        return home_page(h, app, user)
    if p == "/menu":
        return menu_page(h, app, h.me)
    if p == "/settings":
        return settings_page(h, app, h.me)
    if p == "/docs/folder/pick/add" and h.method == "POST":
        fid = h.form.get("folder", "")
        return docs_page(h, app, user, f"/docs/folder/{int(fid) if fid.isdigit() else 0}/add")
    if p == "/docs" or p.startswith("/docs/"):
        return docs_page(h, app, user, p)
    if p == "/files" or p.startswith("/files/"):
        return files_page(h, app, user, p)
    if p == "/mail" or p.startswith("/mail/"):
        from .web_mail import route as mail_route
        return mail_route(h, app, user, p)
    return None
