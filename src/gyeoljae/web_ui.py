"""공통 화면 부품(docs/ui-layout.md): 상태 칩, 결재란(도장 표), 문서 머리표, 목록·쪽 나눔, 문서 보기, 기안 작성, 결재 확인.
모든 동작은 core(App) 호출. 자바스크립트 없이 동작."""
from __future__ import annotations

import datetime as _dt

from . import core
from . import policy as pol
from .web import esc

RETENTION = {"general": "5년", "expense": "5년", "leave": "3년"}
CHIP = {"pending": ("p", "● 진행"), "done": ("d", "✔ 완료"), "rejected": ("r", "✖ 반려"), "draft": ("w", "○ 기안중"),
        "withdrawn": ("w", "↩ 회수")}
STAMP = {"approved": "승인", "rejected": "반려", "pending": "결재중", "waiting": "대기", "skipped": "생략"}
PER_PAGE = 20


def chip(status, late=False):
    if late:
        return "<span class='chip late'>⏱ 지연</span>"
    cls, txt = CHIP.get(status, ("w", status))
    return f"<span class='chip {cls}'>{txt}</span>"


def _days(iso):
    try:
        return (_dt.date.today() - _dt.date.fromisoformat((iso or "")[:10])).days
    except ValueError:
        return 0


def pager(total, page, base):
    pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
    if pages == 1:
        return f"<p class=mut>총 {total}건</p>"
    sep = "&" if "?" in base else "?"
    lo, hi = max(1, page - 2), min(pages, page + 2)
    links = "".join(f"<a href='{base}{sep}page={i}'{' aria-current=page class=on' if i == page else ''}>{i}</a>"
                    for i in range(lo, hi + 1))
    prev = f"<a href='{base}{sep}page={page - 1}'>« 이전</a>" if page > 1 else ""
    nxt = f"<a href='{base}{sep}page={page + 1}'>다음 »</a>" if page < pages else ""
    return f"<nav class=pager aria-label='쪽'>{prev}{links}{nxt}</nav><p class=mut>총 {total}건</p>"


def list_table(rows, page=1, base="", caption="문서 목록", inbox=False):
    if not rows:
        return ("<div class='card empty'><p>문서가 없습니다.</p><p><a href='/new'>✍ 기안하기</a> · "
                "<a href='/'>홈으로</a></p></div>")
    total = len(rows)
    page = max(1, min(page, (total + PER_PAGE - 1) // PER_PAGE))
    part = rows[(page - 1) * PER_PAGE: page * PER_PAGE]
    tr = "".join(
        f"<tr><td data-l='문서번호'>{esc(r.get('number') or '#' + str(r['id']))}</td>"
        f"<td class=t data-l='제목'><a href='/doc/{r['id']}' title='{esc(r['title'])}'>{esc(r['title'])}</a></td>"
        f"<td data-l='기안자'>{esc(r.get('drafter_name') or r['drafter'])}</td>"
        f"<td data-l='기안일'>{esc((r.get('submitted') or r['created'])[:10])}</td>"
        f"<td data-l='상태'>{chip(r['status'], inbox and _days(r.get('submitted')) > 3)}</td>"
        f"<td data-l='현재 결재'>{esc(r.get('cur_name') or '-')}</td></tr>" for r in part)
    return (f"<table class=list><caption class=sr-only>{esc(caption)}</caption><thead><tr><th scope=col>문서번호</th>"
            f"<th scope=col>제목</th><th scope=col>기안자</th><th scope=col>기안일</th><th scope=col>상태</th>"
            f"<th scope=col>현재 결재</th></tr></thead><tbody>{tr}</tbody></table>{pager(total, page, base)}")


def stamp_table(m, me):
    cols = [{"pos": "기안", "name": m["drafter_name"], "stamp": "기안", "at": m.get("submitted") or m["created"],
             "cls": "ok", "cur": False}]
    for i, s in enumerate(m["line"]):
        st = s["status"]
        stamp = STAMP.get(st, st)
        if st == "approved" and s.get("proxy_for"):
            stamp = "대결"
        elif st == "approved" and s["final"] and any(x["status"] == "skipped" for x in m["line"][i + 1:]):
            stamp = "전결"
        cols.append({"pos": s.get("title") or ("전결권" if s["final"] else "결재"), "name": s["name"], "stamp": stamp,
                     "at": s.get("at"), "cls": {"approved": "ok", "rejected": "no"}.get(st, "wait"),
                     "cur": m["status"] == "pending" and m.get("cur_step") == i})
    head = "".join(f"<th scope=col>{esc(c['pos'])}</th>" for c in cols)
    names = "".join(f"<td class='{'cur' if c['cur'] else ''}'>{esc(c['name'])}</td>" for c in cols)
    stamps = "".join(f"<td class='{'cur' if c['cur'] else ''}'><span class='seal {c['cls']}'>{esc(c['stamp'])}</span></td>"
                     for c in cols)
    dates = "".join(f"<td class=mut>{esc((c['at'] or '-')[5:10] if c['at'] else '-')}</td>" for c in cols)
    return (f"<table class=stamp><caption>결재란</caption><tr>{head}</tr><tr>{names}</tr><tr>{stamps}</tr>"
            f"<tr>{dates}</tr></table>")


def doc_head(m):
    t = m.get("doc_type") or "general"
    fm = m.get("form") or {}
    rows = [("문서번호", m.get("number") or "(완료 시 부여)"), ("보존기간", fm.get("retention") or RETENTION.get(t, "5년")),
            ("양식", fm.get("name") or pol.DOC_TYPES.get(t, t)), ("문서 종류", pol.DOC_TYPES.get(t, t)), ("기안부서", m["dept"]), ("기안자", m["drafter_name"]),
            ("기안일", (m.get("submitted") or m["created"])[:10]), ("완료일", (m.get("completed") or "-")[:10])]
    if m.get("amount") is not None:
        rows.append(("금액", pol.fmt_amount(m["amount"])))
    rows.append(("결재규정", f"{m['policy']['dept']} v{m['policy']['version']}" if m.get("policy") else "없음(자유)"))
    return "<table class=head>" + "".join(f"<tr><th scope=row>{a}</th><td>{esc(b)}</td></tr>" for a, b in rows) + "</table>"


def doc_view(h, app, user, m, err="", comment=""):
    did = m["id"]
    cc = ", ".join(f"{esc(x)}{' ✓열람' if x in m['reads'] else ''}" for x in m["cc"]) or "-"
    atts = "".join(f"<li>📎 <a href='/doc/{did}/file/{a['idx']}'>{esc(a['name'])}</a> "
                   f"<span class=mut>{a['size']:,} bytes</span></li>" for a in m["attachments"])
    hist = "".join(f"<li><span class=mut>{esc(x['at'][5:16])}</span> {esc(x['actor'])} {esc(x['action'])}"
                   f"{' “' + esc(x.get('comment')) + '”' if x.get('comment') else ''}</li>" for x in m["history"])
    c = h.csrf()
    bar = (f"<a href='/box/inbox' class=btn>← 목록</a> {chip(m['status'])} "
           f"<a href='/doc/{did}/print' class='btn noprint' target=_blank rel=noopener>🖨 인쇄</a>")
    from . import hanword
    if hanword.available():
        bar += (f" <a href='/doc/{did}/export.hwpx' class='btn noprint' download>HWPX 내려받기</a>"
                f" <a href='/doc/{did}/export.docx' class='btn noprint' download>DOCX 내려받기</a>")
    bar += f" <a href='/doc/{did}/redraft' class='btn noprint'>이 문서로 기안</a>"
    act = ""
    if m["can"]["approve"]:
        proxy = f" <span class=mut>(대결: {esc(m['cur_user'])} 대신)</span>" if m["can"]["proxy"] else ""
        act = (f"<form method=post action='/doc/{did}/confirm' class='card act'>{c}"
               f"<input type=hidden name=rev value='{int(m.get('rev', 0))}'>"
               f"<label for=cmt>의견 / 반려 사유{proxy}</label>"
               f"{'<p class=err role=alert id=cmt-err>' + esc(err) + '</p>' if err else ''}"
               f"<input type=text id=cmt name=comment maxlength=1000 value='{esc(comment)}'"
               f"{' aria-describedby=cmt-err' if err else ''}>"
               f"<p class=row><button name=act value=approve>결재</button><button name=act value=jeon class=sec>전결</button>"
               f"<button name=act value=reject class=warn>반려</button></p></form>")
    if m["can"]["withdraw"]:
        act += (f"<form method=post action='/doc/{did}/withdraw' class=inline>{c}<button class=sec>회수</button></form> ")
    if m["can"]["edit"]:
        act += (f"<a href='/doc/{did}/edit' class=btn>수정</a> <form method=post action='/doc/{did}/submit' class=inline>"
                f"{c}<button>상신</button></form>")
    done = ""
    q = h.qs.get("done")
    if q in ("approve", "reject"):
        left = len(app.list_box(user, "inbox"))
        nxt = f" · <a href='/box/inbox'>다음 문서({left}건 남음)</a>" if left else ""
        done = f"<p class='banner ok' role=status>{'결재했습니다' if q == 'approve' else '반려했습니다'}{nxt}</p>"
    from .paper import render_paper
    from .web_filing import tags_panel
    fv = ""
    if m.get("form"):
        _fd = app.form_for_doc(user, m["id"])
        fv = fields_view(_fd, m.get("fields"))
        fv = f"<div class='card formview'>{fv}</div>" if fv else ""
        miss = [f["label"] for f in _fd["fields"] if f["type"] == "file" and f["required"]
                and len(m["attachments"]) < f.get("min_files", 1)]
        if miss:  # 막지 않고 알림만(결재자가 확인)
            fv = f"<p class='banner warn' role=note>⚠ 증빙 첨부 없음: {esc(', '.join(miss))} — 결재자가 확인하세요</p>" + fv
    if m.get("doc_html"):  # 편집기 문서: 서식 칸까지 HTML 그대로(출력 때 다시 정화)
        from .sanitize import clean_html
        h._csp = "doc"
        fv, bd = "", f"<div class='card dochtml'>{clean_html(m['doc_html'])}</div><div class=card>"
    else:
        bd = f"<div class=card><pre class=body>{esc(m['body'])}</pre>"
    main = (f"{done}<div class=bar>{bar}</div><h2 class=title>{esc(m['title'])}</h2>"
            f"<div class=docgrid><div>{doc_head(m)}</div><div class=stampwrap>{stamp_table(m, user)}</div></div>"
            f"{fv}<p class=mut>참조: {cc}{source_line(m)}</p>{bd}"
            f"{'<ul class=atts>' + atts + '</ul>' if atts else ''}</div>{act}"
            f"<div class=card><h3>의견·이력</h3><ul class=items>{hist}</ul></div>"
            f"<details class='card paperprev noprint'><summary>출력 미리보기 (A4)</summary>"
            f"<div class='paperwrap paper'>{render_paper(app, user, m)}</div></details>")
    aside = (f"<section class=card><h3>결재선</h3><ol class=lineol>" + "".join(
        f"<li>{esc(s['name'])} <span class=mut>{esc(STAMP.get(s['status'], s['status']))}"
        f"{' · 전결권' if s['final'] else ''}</span></li>" for s in m["line"]) + "</ol></section>"
        + tags_panel(h, app, user, did))
    h._aside = aside
    return h.ok(m["title"], main)


def source_line(m):
    """'이 문서로 기안'한 문서: 원문 번호(볼 수 있으면 링크)."""
    s = m.get("source")
    if not s:
        return ""
    num = esc(s["number"])
    return f" · 원문: <a href='/doc/{int(s['id'])}'>{num}</a>" if s["can_view"] else f" · 원문: {num}"


def confirm_page(h, m, act, comment, jeon):
    did = m["id"]
    what = {"approve": "결재", "jeon": "전결", "reject": "반려"}[act]
    warn = ("반려하면 기안자에게 돌아갑니다." if act == "reject" else
            "결재하면 취소할 수 없습니다." + (" 전결하면 이후 결재 단계는 생략되고 문서가 완료됩니다." if act == "jeon" else ""))
    target = "reject" if act == "reject" else "approve"
    body = (f"<h2>{what} 확인</h2><div class=card><table class=head><tr><th scope=row>문서</th><td>"
            f"{esc(m.get('number') or '#' + str(did))} {esc(m['title'])}</td></tr><tr><th scope=row>기안자</th><td>"
            f"{esc(m['drafter_name'])} · {esc(m['dept'])}</td></tr><tr><th scope=row>의견</th><td>{esc(comment) or '-'}"
            f"</td></tr></table><p class='banner warn' role=alert>{warn}</p>"
            f"<form method=get action='/doc/{did}' class=inline><button class=sec autofocus>취소</button></form> "
            f"<form method=post action='/doc/{did}/{target}' class=inline>{h.csrf()}"
            f"<input type=hidden name=rev value='{int(m.get('rev', 0))}'><input type=hidden name=comment value='{esc(comment)}'>"
            f"<input type=hidden name=confirm value=1>{'<input type=hidden name=jeon value=1>' if jeon else ''}"
            f"<button class='{'warn' if act == 'reject' else ''}'>{what} 확정</button></form></div>")
    return h.ok(f"{what} 확인", body)


def _amt(a):
    try:
        return pol.fmt_amount(pol.parse_amount(a))
    except core.GyeolError:
        return str(a)


def draft_form(h, app, user, action, m=None, err=""):
    from .web_filing import draft_section
    m = m or {}
    fd = None
    if m.get("form"):
        fd = app.form_for_doc(user, m["id"]) if m.get("id") else app.form_get(user, m["form"]["code"])
        m.setdefault("doc_type", fd["doc_type"])
    t = m.get("doc_type") or "general"
    line = ",".join(m.get("line_input") or [s["user"] + ("!" if s["final"] else "") for s in m.get("line", [])])
    cc_in = m.get("cc_input", m.get("cc", []))
    amt = m.get("amount")
    types = "".join(f"<label class=pick><input type=radio name=doc_type value='{k}' {'checked' if t == k else ''}> {v}</label>"
                    for k, v in pol.DOC_TYPES.items())
    atts = "".join(f"<li>📎 {esc(a['name'])} <span class=mut>({_size(a.get('size'))})</span> "
                   f"<label class='mut inline'><input type=checkbox name=remove value='{a['idx']}'> 삭제</label></li>"
                   for a in m.get("attachments", []))
    atts = f"<p class=mut>붙어 있는 파일</p><ul class=atts>{atts}</ul>" if atts else ""
    saved = f"마지막 저장 {esc((m.get('updated') or m.get('created') or '')[11:16])}" if m.get("id") else "저장 전"
    pre_f = mid_f = post_f = ""
    if fd:
        from .paper import input_order, layout_for
        before, mid, after = input_order(fd, layout_for(app, fd))
        pre_f, mid_f, post_f = (fields_html(fd, m.get("fields"), ks) for ks in (before, mid, after))
        sec1 = (f"<fieldset class=card><legend>① 양식</legend><b>{esc(fd['name'])}</b> <span class=mut>{esc(fd['category'])} · "
                f"{esc(pol.DOC_TYPES[fd['doc_type']])} · 보존 {esc(fd['retention'])} · v{fd['version']}</span>"
                f"<input type=hidden name=form value='{esc(fd['code'])}'>")
        if not m.get("id"):
            sec1 += " <a href='/new' class=btn>양식 바꾸기</a>"
        # 양식 문서는 금액을 꺼내지 않음(양식 칸에 기안자가 적고 결재자가 확인) — 금액 한도 전결 없음
        sec1 += "</fieldset>"
    else:
        sec1 = (f"<fieldset class=card><legend>① 양식</legend>{types}"
                f"<label for=amt>금액 <span class=mut>(지출 필수, 예: 150만)</span></label>" + f"<input type=text id=amt name=amount inputmode=numeric value='{esc('' if amt is None else amt)}'>" + "</fieldset>")
    form = (f"{'<p class=err role=alert>' + esc(err) + '</p>' if err else ''}"
            f"<form method=post action='{action}' enctype='multipart/form-data' class=draft>{h.csrf()}"
            f"{sec1}"
            f"<fieldset class=card><legend>② 결재선</legend><p class=mut>부서 결재규정이 있으면 결재선은 자동으로 정해지고 "
            f"(오른쪽 미리보기), 여기에는 <b>추가 결재자</b>만 적습니다. 규정이 없으면 결재자 아이디를 순서대로(전결권자는 끝에 !).</p>"
            f"<label for=ln>결재자</label><input type=text id=ln name=line list=users value='{esc(line)}' autocomplete=off>"
            f"<label for=cc>참조</label><input type=text id=cc name=cc list=users value='{esc(','.join(cc_in))}' autocomplete=off>"
            f"</fieldset><fieldset class=card><legend>③ {'서식·' if fd else ''}본문</legend><div class=edfb>{pre_f}</div>"
            f"<label for=ti>제목</label>"
            f"<input type=text id=ti name=title maxlength={core.MAX_TITLE} required value='{esc(m.get('title'))}'"
            f"{' placeholder=' + chr(39) + esc(fd['title_hint']) + chr(39) if fd and fd.get('title_hint') else ''}>"
            f"<div class=edfb>{mid_f if fd else ''}"
            f"<label for=bd>본문</label><textarea id=bd name=body>"
            f"{esc(m.get('body') if m.get('body') is not None or not fd else fd.get('body_hint', ''))}</textarea>{post_f}</div>"
            f"{editor_host(h, fd, m)}</fieldset>"
            # 첨부 칸은 편집기와 같은 <form> 안 — 편집기 경로(doc_html)로 저장·상신해도 파일이 함께 올라감
            f"<fieldset class=card><legend>④ 붙임 (첨부 파일)</legend>"
            f"<p class=mut>올린 파일은 문서의 <b>붙임</b> 줄에 \"1. 파일명 1부.\"처럼 찍힙니다.</p>"
            f"<input type=file name=files multiple aria-label='첨부 파일'>"
            f"<p class=mut>개당 {core.MAX_FILE_BYTES // 1048576}MB · 합계 {core.MAX_TOTAL_BYTES // 1048576}MB · "
            f"최대 {core.MAX_FILES}개, 문서·이미지·압축(hwpx/hwp/docx/xlsx/pptx/pdf/txt/csv/png/jpg/gif/zip 등)</p>{atts}"
            f"<label><input type=checkbox name=dept_open value=1 {'checked' if m.get('visibility') == 'dept' else ''}>"
            f" 완료 후 부서원 열람 허용</label></fieldset>"
            f"{draft_section(h, app, user, m)}"
            f"<div class=savebar><button name=go value=save class=sec>임시저장</button> <span class=mut>{saved} · "
            f"저장하지 않은 내용은 다른 메뉴로 가면 사라집니다</span><span class=sp></span>"
            f"<span class=mut>상신하면 내용을 고칠 수 없고 회수만 가능합니다</span> <button name=go value=submit>상신</button></div>"
            f"</form>{h.user_datalist()}")
    try:
        pv = app.preview_line(user, t, amt)
        if pv["free"]:
            lp = f"<p class=mut>{esc(pv['note'])}</p>"
        else:
            lp = ("<ol class=lineol><li>" + esc(app.get_user(user)["name"]) + " <span class=mut>기안</span></li>" + "".join(
                f"<li>{esc(x['name'])} <span class=mut>{esc(x['position'] or '')}{' · 전결' if x['final'] else ''}</span></li>"
                for x in pv["line"]) + "</ol>" + (f"<p class=mut>필수 참조: {esc(', '.join(pv['cc']))}</p>" if pv["cc"] else "")
                + f"<p class=mut>추가 결재자 {'가능' if pv['allow_extra'] else '불가'} · 규정 v{pv['policy_version']}</p>")
    except core.GyeolError as e:
        lp = f"<p class=err>{esc(e)}</p>"
    if fd and fd["cc_depts"]:
        lp += f"<p class=mut>양식 필수 참조: {esc(', '.join(fd['cc_depts']))} 부서장</p>"
    h._aside = (f"<section class=card><h3>결재선 미리보기</h3><p class=mut>{esc(pol.DOC_TYPES[t])}"
                f"{' · ' + esc(_amt(amt)) if amt not in (None, '') else ''}</p>{lp}"
                f"<p class=mut>양식·금액을 바꾸면 임시저장 후 다시 계산됩니다.</p></section>")
    return form


# ------------------------------------------------------------------ 양식(서식)
REDISPLAY_MAX = 16 * 1024 * 1024  # 크기 초과로 거절된 편집기 내용을 다시 보여 줄 상한(글자 수)


def editor_host(h, fd, m):
    """문서 편집기(iframe) 자리. 스크립트가 돌면 editor-host.js 가 위 입력칸(.edfb)을 숨기고 편집기를 띄운다.
    스크립트가 없으면(휴대폰·시험) 입력칸이 그대로 동작. docinit 은 name 이 없어 그냥은 제출되지 않음."""
    import json
    from . import hanword
    from .doctemplate import initial_html
    if not hanword.available():
        return ""
    h._csp = "host"
    try:
        init = hanword.canonical_html(m["doc_html"]) if m.get("doc_html") is not None else \
            initial_html(fd, m.get("fields"), m.get("body") if m.get("id") else None)
    except ValueError:
        init = ""
        # 크기로 거절된 제출: 내용을 잃지 않게 같은 정화로 다시 보여 줌(저장은 안 됨, 화면 상한 REDISPLAY_MAX 안에서만)
        raw = m.get("doc_html")
        if raw is not None and len(str(raw)) <= REDISPLAY_MAX:
            try:
                init = hanword.canonical_html(raw, limit=False)
            except ValueError:
                init = ""
    try:  # 저장된 용지(편집기가 돌려준 값) → 없으면 공문서 A4(위30·왼20·오15·아래15)
        page = hanword.parse_page(m.get("doc_page")) or hanword.GOV
    except ValueError:
        page = hanword.GOV
    return (f"<div id=edhost class=edhost hidden data-page='{esc(json.dumps(page))}'>"
            f"<p id=ederr class='banner warn' role=alert hidden></p>"
            f"<iframe id=edframe title='문서 편집기'></iframe></div>"
            f"<textarea id=docinit hidden aria-hidden=true>{esc(init)}</textarea>"
            f"<script src='/static/img-shrink.js?v=2' defer></script><script src='/static/editor-host.js?v=5' defer></script>")


def form_chooser(h, app, user):
    """① 양식 선택: 즐겨찾기·최근·분류별."""
    from . import formdef as fdm
    forms = app.forms_list(user)
    by = {f["code"]: f for f in forms}
    c = h.csrf()

    def card(f):
        star = (f"<form method=post action='/forms/fav' class=inline>{c}<input type=hidden name=code value='{esc(f['code'])}'>"
                f"<input type=hidden name=on value='{0 if f['fav'] else 1}'><button class=sec aria-label='즐겨찾기 "
                f"{'해제' if f['fav'] else '추가'}' title='즐겨찾기'>{'★' if f['fav'] else '☆'}</button></form>")
        return (f"<li class=pick><a href='/new?form={esc(f['code'])}'><b>{esc(f['name'])}</b></a> "
                f"<span class=mut>{esc(pol.DOC_TYPES.get(f['doc_type'], ''))} · 보존 {esc(f['retention'])}</span> {star}</li>")
    out = ""
    fav = [f for f in forms if f["fav"]]
    if fav:
        out += "<section class=card><h3>★ 즐겨찾기</h3><ul class=forms>" + "".join(card(f) for f in fav) + "</ul></section>"
    rec = [by[x] for x in app.forms_recent(user) if x in by]
    if rec:
        out += "<section class=card><h3>최근 사용</h3><ul class=forms>" + "".join(card(f) for f in rec) + "</ul></section>"
    for cat in fdm.CATEGORIES:
        fs = [f for f in forms if f["category"] == cat]
        if fs:
            out += f"<section class=card><h3>{cat}</h3><ul class=forms>" + "".join(card(f) for f in fs) + "</ul></section>"
    if not forms:
        out = "<p class='card empty'>쓸 수 있는 양식이 없습니다. 관리자에게 문의하세요.</p>"
    return h.ok("기안 · 양식 선택", f"<h2>새 기안 · ① 양식 선택</h2>{out}")


def _size(n):
    if not isinstance(n, int):
        return "-"
    return f"{n} B" if n < 1024 else (f"{n / 1024:.1f} KB" if n < 1048576 else f"{n / 1048576:.1f} MB")


def _calc(x):
    return f"{x:,}" if isinstance(x, (int, float)) else ""


def _v(x):
    return "" if x is None else esc(x)


def fields_html(fd, vals, keys=None):
    """양식 필드 입력 칸(이름 f.키…). 값은 정규화된 값 또는 직전 입력값. keys = 이 순서로 이 필드만(종이 순서)."""
    vals = vals or {}
    out = []
    by = {f["key"]: f for f in fd["fields"]}
    for f in (fd["fields"] if keys is None else [by[k] for k in keys if k in by]):
        k, t, v = f["key"], f["type"], vals.get(f["key"])
        req = " <span class=req aria-label='필수'>*</span>" if f["required"] else ""
        hp = f" <span class=mut>{esc(f['help'])}</span>" if f.get("help") else ""
        lab = f"<label for='f-{k}'>{esc(f['label'])}{req}{hp}</label>"
        if t == "text":
            out.append(f"{lab}<input type=text id='f-{k}' name='f.{k}' maxlength={f['max']} value='{_v(v)}'>")
        elif t == "textarea":
            out.append(f"{lab}<textarea class=short id='f-{k}' name='f.{k}'>{_v(v)}</textarea>")
        elif t in ("number", "money"):
            out.append(f"{lab}<input type=text inputmode=numeric id='f-{k}' name='f.{k}' value='{_v(v)}'"
                       + (" placeholder='예: 150만'" if t == "money" else "") + ">")
        elif t == "date":
            out.append(f"{lab}<input type=date id='f-{k}' name='f.{k}' value='{_v(v)}'>")
        elif t == "select":
            opts = "".join(f"<option{' selected' if o == v else ''}>{esc(o)}</option>" for o in f["options"])
            out.append(f"{lab}<select id='f-{k}' name='f.{k}'><option value=''>— 선택 —</option>{opts}</select>")
        elif t == "daterange":
            v = v if isinstance(v, dict) else {}
            days = f" <span class=mut>({v['days']:g}일)</span>" if v.get("days") is not None else ""
            out.append(f"{lab}<div class=row><input type=date id='f-{k}' name='f.{k}.from' value='{_v(v.get('from'))}' "
                       f"aria-label='{esc(f['label'])} 시작'><input type=date name='f.{k}.to' value='{_v(v.get('to'))}' "
                       f"aria-label='{esc(f['label'])} 끝'></div>{days}")
        elif t == "file":
            out.append(f"<p class=mut>📎 {esc(f['label'])}{req}: 아래 ④ 붙임에 {f['min_files']}개 이상 올려 주세요</p>")
        elif t == "table":
            rows = v.get("rows") if isinstance(v, dict) else (v or [])
            n = max(f["rows"], len(rows) + 2)
            head = "".join(f"<th scope=col>{esc(c['label'])}</th>" for c in f["columns"])
            body = ""
            for i in range(min(n, 50)):
                r = rows[i] if i < len(rows) else {}
                body += "<tr>" + "".join(
                    f"<td><input type=text name='f.{k}.{i}.{c['key']}' value='{_v(r.get(c['key']))}' "
                    f"aria-label='{i + 1}행 {esc(c['label'])}'{' inputmode=numeric' if c['type'] != 'text' else ''}></td>"
                    for c in f["columns"]) + "</tr>"
            out.append(f"<p><b>{esc(f['label'])}</b>{req} <span class=mut>(빈 행은 무시, 금액도 직접 적으세요)"
                       f"</span></p><div class=tablewrap><table class='form items'><tr>{head}</tr>{body}</table></div>")
    return "".join(out)


def fields_view(fd, vals):
    """문서 보기: 양식 필드를 서식 표로."""
    from . import formdef as fdm
    vals = vals or {}
    rows, tables = "", ""
    for f in fd["fields"]:
        v = vals.get(f["key"])
        if f["type"] == "file":
            continue
        if f["type"] == "table":
            if not v:
                rows += f"<tr><th scope=row>{esc(f['label'])}</th><td>-</td></tr>"
                continue
            cols = f["columns"]
            fmt = lambda c, x: "" if x is None else (f"{x:,}" if c["type"] != "text" and isinstance(x, (int, float))
                                                     else esc(x))  # noqa: E731
            tb = "".join("<tr>" + "".join(f"<td{' class=amt' if c['type'] != 'text' else ''}>{fmt(c, r.get(c['key']))}</td>"
                                          for c in cols) + "</tr>" for r in v["rows"])
            sm = ""
            if v.get("sum"):
                sm = "<tr class=sum>" + "".join(
                    f"<th class=amt>{_calc(v['sum'][c['key']]) or '미상'}</th>" if c["key"] in v["sum"] else
                    f"<th>{'합계' if j == 0 else ''}</th>" for j, c in enumerate(cols)) + "</tr>"
            tables += (f"<table class='form items'><caption>{esc(f['label'])}</caption><tr>"
                       + "".join(f"<th scope=col>{esc(c['label'])}</th>" for c in cols) + f"</tr>{tb}{sm}</table>")
        else:
            val = esc(fdm.display(f, v))
            if f["type"] == "textarea":
                val = f"<pre class=body>{val}</pre>"
            rows += f"<tr><th scope=row>{esc(f['label'])}</th><td>{val}</td></tr>"
    return (f"<table class=form>{rows}</table>" if rows else "") + tables


def print_view(h, app, user, m):
    """인쇄용 보기: 메뉴 없는 A4 한 장(브라우저 인쇄 Ctrl+P / ⌘P). 스크립트 없음. 화면=회색 바탕 가운데 종이."""
    from .paper import render_paper
    sheet = render_paper(app, user, m)
    if m.get("doc_html"):
        h._csp = "doc"
    body = (f"<p class='noprint pbar'><span>인쇄: Ctrl+P (Mac ⌘P) · 용지 A4</span>"
            f"<a href='/doc/{int(m['id'])}'>← 문서로</a></p><div class=paper>{sheet}</div>")
    doc = (f"<!doctype html><html lang=ko><head><meta charset=utf-8><meta name=viewport "
           f"content='width=device-width,initial-scale=1'><title>{esc(m['title'])}</title>"
           f"<link rel=stylesheet href='/paper.css?v=3'></head><body class=paperpage><main>{body}</main></body></html>")
    return core_resp(doc)


def core_resp(doc):
    from .web import Resp
    return Resp(200, doc.encode("utf-8"), {"Content-Type": "text/html; charset=utf-8"})
