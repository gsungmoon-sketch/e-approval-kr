"""문서 분류·보관 화면: 부서 문서함(분류 거르기)·부서 분류 관리·사업 문서함·문서 태그 편집·"이 문서로 기안".
모든 동작은 core(App) 호출. 태그는 열람 권한을 넓히지 않음(목록은 core 가 열람 규칙으로 거름)."""
from __future__ import annotations

import re
import urllib.parse

from . import core
from .web import esc, redirect


def _list(h, name):
    v = h.form.get(name)
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _indent(r):
    return "　" * r.get("depth", 0) + ("└ " if r.get("depth") else "")


# ------------------------------------------------------------------ 문서 태그(칩 + 편집)
def tag_chips(t):
    out = []
    if t["dclass"]:
        out.append(f"<span class=tag title='부서 분류'>🗂 {esc(t['dclass']['name'])}</span>")
    for p in t["projects"]:
        nm = esc(p["name"]) + (" (종료)" if p["status"] == "closed" else "")
        out.append(f"<a class=tag href='/docs/project/{p['id']}' title='사업 문서함'>📁 {nm}</a>" if p["can_open"]
                   else f"<span class=tag title='사업'>📁 {nm}</span>")
    for f in t["folders"]:
        out.append(f"<a class='tag mine' href='/docs/folder/{f['id']}' title='내 폴더'>★ {esc(f['name'])}</a>")
    return "".join(out) or "<span class=mut>분류·폴더 없음</span>"


def tags_panel(h, app, user, did):
    """문서 화면 오른쪽: 분류·사업·내 폴더 칩 + 고치기(권한 있을 때 분류·사업, 내 폴더는 늘)."""
    t = app.doc_tags(user, did, options=True)
    c = h.csrf()
    edit = ""
    if t["can_reclass"]:
        cur = t["dclass"]["id"] if t["dclass"] else None
        opts = "<option value=none>(미분류)</option>" + "".join(
            f"<option value='{r['id']}'{' selected' if r['id'] == cur else ''}>{esc(_indent(r) + r['name'])}</option>"
            for r in t["class_options"])
        have = {p["id"] for p in t["projects"]}
        avail = {p["id"]: p for p in t["project_options"]}
        for p in t["projects"]:  # 이미 붙은 사업(종료·비참여)도 뺄 수 있게
            avail.setdefault(p["id"], p)
        pj = "".join(f"<label class=inline><input type=checkbox name=project value='{p['id']}'"
                     f"{' checked' if p['id'] in have else ''}> {esc(p['name'])}</label><br>" for p in avail.values())
        edit += (f"<form method=post action='/doc/{did}/tags' class=tagedit>{c}<input type=hidden name=what value=class>"
                 f"<label for=tg-c>부서 분류</label><select id=tg-c name=dclass>{opts}</select>"
                 f"{'<p class=lbl>사업</p>' + pj if pj else ''}<p><button class=sec>분류 저장</button></p></form>")
    if t["folder_options"]:
        have = {f["id"] for f in t["folders"]}
        fl = "".join(f"<label class=inline><input type=checkbox name=folder value='{f['id']}'"
                     f"{' checked' if f['id'] in have else ''}> {esc(f['name'])}</label><br>" for f in t["folder_options"])
        edit += (f"<form method=post action='/doc/{did}/tags' class=tagedit>{c}<input type=hidden name=what value=folders>"
                 f"<p class=lbl>내 폴더</p>{fl}<p><button class=sec>폴더 저장</button></p></form>")
    else:
        edit += "<p class=mut><a href='/docs'>내 폴더</a>를 만들면 이 문서를 담을 수 있습니다.</p>"
    return (f"<section class=card><h3>분류·보관</h3><p class=tags>{tag_chips(t)}</p>"
            f"<details><summary>고치기</summary>{edit}</details></section>")


def doc_tags_post(h, app, user, did):
    f = h.form
    if f.get("what") == "folders":
        app.doc_set_folders(user, did, _list(h, "folder"))
    else:
        dc = f.get("dclass")
        app.doc_reclass(user, did, dclass=None if dc in (None, "", "none") else dc, projects=_list(h, "project"))
    return redirect(f"/doc/{did}")


# ------------------------------------------------------------------ 기안 화면 ⑤ 분류·보관
def draft_section(h, app, user, m):
    """기안·수정 화면의 분류·사업(+새 기안이면 보관 폴더) 고르기. tags=1 이 있어야 서버가 값을 바꿈."""
    new = not m.get("id")
    try:
        o = app.draft_options(user, (m.get("form") or {}).get("code"), m.get("doc_type") or "general")
    except core.GyeolError:
        return ""
    if m.get("id"):
        t = app.doc_tags(user, m["id"])
        cur = t["dclass"]["id"] if t["dclass"] else None
        projs = {p["id"] for p in t["projects"]}
    else:
        cur = m.get("dclass", o["default_class"])
        projs = set(m.get("projects") or [])
    opts = "<option value=none>(미분류)</option>" + "".join(
        f"<option value='{r['id']}'{' selected' if r['id'] == cur else ''}>{esc(_indent(r) + r['name'])}</option>"
        for r in o["classes"])
    pj = "".join(f"<label class=pick><input type=checkbox name=project value='{p['id']}'"
                 f"{' checked' if p['id'] in projs else ''}> {esc(p['name'])}"
                 f"{' <span class=mut>' + esc(p['code']) + '</span>' if p.get('code') else ''}</label>"
                 for p in o["projects"])
    fl = ""
    if new:
        fsel = set(m.get("folders") or [])
        fl = "".join(f"<label class=pick><input type=checkbox name=folder value='{x['id']}'"
                     f"{' checked' if x['id'] in fsel else ''}> {esc(x['name'])}</label>" for x in o["folders"])
        fl = (f"<p class=lbl>보관 폴더 <span class=mut>(나만 보임)</span></p>{fl}" if fl else
              "<p class=mut>보관 폴더: <a href='/docs'>내 폴더</a>를 만들면 고를 수 있습니다.</p>")
    src = ""
    if m.get("source_id"):
        src = (f"<input type=hidden name=source_id value='{int(m['source_id'])}'>"
               f"<p class=mut>원문: <a href='/doc/{int(m['source_id'])}'>{esc(m.get('source_number') or '#' + str(m['source_id']))}</a>"
               f" — 새 문서는 상신할 때 새 번호를 받습니다.</p>")
        if m.get("source_attachments"):
            src += (f"<label><input type=checkbox name=copy_atts value=1{' checked' if m.get('copy_atts') else ''}>"
                    f" 원문 첨부도 가져오기 ({int(m['source_attachments'])}개)</label>")
    return (f"<fieldset class=card><legend>⑤ 분류·보관</legend><input type=hidden name=tags value=1>{src}"
            f"<label for=dc>부서 분류</label><select id=dc name=dclass>{opts}</select>"
            f"{'<p class=lbl>사업 문서함 <span class=mut>(참여 중인 진행 사업)</span></p>' + pj if pj else ''}{fl}"
            f"<p class=mut>분류·사업·폴더는 표시일 뿐, 볼 수 있는 사람을 늘리지 않습니다.</p></fieldset>")


def draft_kwargs(h, new=True):
    """기안 POST → create_draft/edit_draft 의 분류·보관 인자."""
    f = h.form
    if not f.get("tags"):
        return {}
    dc = f.get("dclass")
    kw = {"dclass": "none" if dc in (None, "", "none") else dc, "projects": _list(h, "project")}
    if new:
        kw["folders"] = _list(h, "folder")
        if f.get("source_id", "").isdigit():
            kw["source_id"] = int(f["source_id"])
            kw["copy_attachments"] = bool(f.get("copy_atts"))
    return kw


def redraft_page(h, app, user, did):
    """이 문서로 기안: 원문 값으로 채운 새 기안 화면(아직 저장 안 함)."""
    from .web_ui import draft_form
    m = app.redraft_prefill(user, did)
    title = f"<h2>새 기안 · 이 문서로 기안 <span class=mut>(원문 {esc(m.get('source_number') or '#' + str(did))})</span></h2>"
    return h.ok("기안", title + draft_form(h, app, user, "/new", m))


# ------------------------------------------------------------------ 부서 문서함(분류 거르기)
def dept_page(h, app, user):
    me = h.me
    sel = h.qs.get("c") or ""
    rows = app.dept_docs(user, sel or None)
    classes = app.dclass_list(user, counts=True)
    tabs = (f"<a href='/docs/dept' class='{'on' if not sel else ''}'>전체</a>" + "".join(
        f"<a href='/docs/dept?c={r['id']}' class='{'on' if sel == str(r['id']) else ''}'>{esc(_indent(r) + r['name'])}</a>"
        for r in classes) + f"<a href='/docs/dept?c=none' class='{'on' if sel == 'none' else ''}'>미분류</a>")
    mng = (" · <a href='/docs/classes'>분류 관리</a>" if me["role"] == "admin" or me.get("is_head") else "")
    return h.ok("부서 문서함", f"<h2>문서함 › 부서 문서함</h2><p class=mut>우리 부서의 완료 문서 중 부서 공개 문서"
                              f"(부서장급은 전체){mng}</p><nav class=tabs aria-label='부서 분류'>{tabs}</nav>"
                              f"{h.table(rows, '/docs/dept' + ('?c=' + esc(sel) if sel else ''))}")


def classes_page(h, app, user):
    me = h.me
    dept = (h.qs.get("dept") or h.form.get("dept") or me["dept"]).upper()
    if h.method == "POST":
        f, op = h.form, h.form.get("op")
        if op == "add":
            app.dclass_add(user, f.get("name"), f.get("parent") or None, dept)
        elif op == "rename":
            app.dclass_rename(user, int(f.get("id", "0") or 0), f.get("name"))
        elif op == "delete":
            app.dclass_delete(user, int(f.get("id", "0") or 0))
        return redirect("/docs/classes" + ("?dept=" + urllib.parse.quote(dept, safe="") if dept != me["dept"] else ""))
    if not (me["role"] == "admin" or me.get("is_head")):
        raise core.Forbidden("부서 분류는 부서장급 또는 관리자가 관리합니다")
    rows = app.dclass_list(user, dept, counts=True)
    c = h.csrf()
    hid = f"<input type=hidden name=dept value='{esc(dept)}'>"
    tr = "".join(
        f"<tr><td>{esc(_indent(r))}<form method=post action='/docs/classes' class=inline>{c}{hid}"
        f"<input type=hidden name=op value=rename><input type=hidden name=id value='{r['id']}'>"
        f"<input type=text name=name value='{esc(r['name'])}' aria-label='분류 이름' maxlength=30 style='width:12em'> "
        f"<button class=sec>이름 바꾸기</button></form></td><td>{r['count']}</td><td>"
        f"<form method=post action='/docs/classes' class=inline>{c}{hid}<input type=hidden name=op value=delete>"
        f"<input type=hidden name=id value='{r['id']}'><button class=warn>삭제</button></form></td></tr>" for r in rows)
    popts = "<option value=''>(최상위)</option>" + "".join(
        f"<option value='{r['id']}'>{esc(_indent(r) + r['name'])}</option>" for r in rows)
    pick = ""
    if me["role"] == "admin":
        pick = (f"<form method=get action='/docs/classes' class='card row'><input type=text name=dept value='{esc(dept)}' "
                f"aria-label='부서 코드'><button class=sec>부서 바꾸기</button></form>")
    body = (f"<h2>문서함 › 부서 분류 관리 <span class=mut>{esc(dept)}</span></h2>{pick}"
            f"<div class=card><table><tr><th>분류</th><th>문서</th><th></th></tr>"
            f"{tr or '<tr><td class=mut>분류가 없습니다</td></tr>'}</table>"
            f"<p class=mut>삭제하면 그 분류의 문서는 미분류가 됩니다(문서는 그대로). 하위 분류가 있으면 먼저 지우세요.</p></div>"
            f"<form method=post action='/docs/classes' class=card>{c}{hid}<input type=hidden name=op value=add>"
            f"<h3>분류 추가</h3><div class=row><input type=text name=name placeholder='분류 이름' maxlength=30 required "
            f"aria-label='새 분류 이름'><select name=parent aria-label='상위 분류'>{popts}</select>"
            f"<button>추가</button></div></form>")
    return h.ok("부서 분류 관리", body)


# ------------------------------------------------------------------ 사업 문서함
def _split(s):
    return [x.strip() for x in re.split(r"[,\s]+", s or "") if x.strip()]


def _project_form(h, action, p=None, label="만들기"):
    p = p or {}
    st = ""
    if p.get("id"):
        st = ("<label for=pj-s>상태</label><select id=pj-s name=status>" + "".join(
            f"<option value='{k}'{' selected' if p.get('status') == k else ''}>{v}</option>"
            for k, v in (("open", "진행"), ("closed", "종료"))) + "</select>")
    return (f"<form method=post action='{action}' class=card>{h.csrf()}<h3>{'사업 설정' if p.get('id') else '새 사업 문서함'}</h3>"
            f"<div class=row><div><label for=pj-n>이름</label><input type=text id=pj-n name=name maxlength=60 required "
            f"value='{esc(p.get('name'))}'></div><div><label for=pj-c>코드 <span class=mut>(선택)</span></label>"
            f"<input type=text id=pj-c name=code maxlength=20 value='{esc(p.get('code'))}'></div></div>"
            f"<label for=pj-d>참여 부서 <span class=mut>(부서 코드, 쉼표 구분)</span></label><input type=text id=pj-d "
            f"name=depts value='{esc(','.join(p.get('depts') or []))}'>"
            f"<label for=pj-u>참여자 <span class=mut>(아이디, 쉼표 구분)</span></label><input type=text id=pj-u name=users "
            f"list=users value='{esc(','.join(p.get('users') or []))}'>{st}<p><button>{label}</button></p></form>"
            f"{h.user_datalist()}")


def projects_page(h, app, user, p):
    me = h.me
    f = h.form
    if p == "/docs/project":
        if h.method == "POST":
            r = app.project_create(user, f.get("name"), f.get("code"), _split(f.get("depts")), _split(f.get("users")))
            return redirect(f"/docs/project/{r['id']}")
        rows = app.project_list(user, all_=me["role"] == "admin")
        tr = "".join(f"<tr><td><a href='/docs/project/{x['id']}'>{esc(x['name'])}</a> <span class=mut>{esc(x['code'] or '')}"
                     f"</span></td><td>{esc(x['status_ko'])}</td><td>{esc(', '.join(x['depts']) or '-')}</td>"
                     f"<td>{len(x['users'])}명</td></tr>" for x in rows)
        new = _project_form(h, "/docs/project") if me["role"] == "admin" or me.get("is_head") else ""
        return h.ok("사업 문서함", f"<h2>문서함 › 사업 문서함</h2><div class=card><table><tr><th>사업</th><th>상태</th>"
                                  f"<th>참여 부서</th><th>참여자</th></tr>{tr or '<tr><td class=mut>참여 중인 사업이 없습니다</td></tr>'}"
                                  f"</table><p class=mut>사업 문서함에는 내가 원래 볼 수 있는 문서만 보입니다.</p></div>{new}")
    m = re.fullmatch(r"/docs/project/(\d{1,9})", p)
    if not m:
        raise core.NotFound("페이지 없음")
    pid = int(m.group(1))
    if h.method == "POST":
        app.project_update(user, pid, name=f.get("name"), code=f.get("code", ""), depts=_split(f.get("depts")),
                           users=_split(f.get("users")), status=f.get("status") or None)
        return redirect(f"/docs/project/{pid}")
    r = app.project_docs(user, pid)
    pj = r["project"]
    info = (f"<p class=mut>{esc(pj['status_ko'])}{' · 코드 ' + esc(pj['code']) if pj['code'] else ''} · 참여 부서 "
            f"{esc(', '.join(pj['depts']) or '-')} · 참여자 {esc(', '.join(pj['users']) or '-')} · 만든 사람 "
            f"{esc(pj['created_by'])}</p>")
    mng = _project_form(h, f"/docs/project/{pid}", pj, "저장") if pj["can_manage"] else ""
    return h.ok(pj["name"], f"<h2>사업 문서함 › {esc(pj['name'])}</h2>{info}"
                            f"{h.table(r['docs'], f'/docs/project/{pid}')}{mng}")


def route(h, app, user, p):
    if p == "/docs/dept":
        return dept_page(h, app, user)
    if p == "/docs/classes":
        return classes_page(h, app, user)
    if p == "/docs/project" or p.startswith("/docs/project/"):
        return projects_page(h, app, user, p)
    return None

