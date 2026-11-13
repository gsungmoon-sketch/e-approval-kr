"""사내 메일 화면(편지함·쓰기·읽기). 모든 동작은 core(App) 함수 호출."""
from __future__ import annotations

import re
import urllib.parse

from . import core
from .mail import FOLDERS
from .web import Resp, esc, redirect


def _tabs(cur, unread):
    return "<nav class=tabs aria-label='편지함'>" + "".join(
        f"<a href='/mail?f={k}' class='{'on' if k == cur else ''}'>{v}"
        f"{' <span class=badge>' + str(unread) + '</span>' if k == 'inbox' and unread else ''}</a>"
        for k, v in FOLDERS.items()) + "<a href='/mail/new' class=on>✉ 메일 쓰기</a></nav>"


def compose(h, app, user, pre=None, err=""):
    pre = pre or {}
    conf_ext = app._smtp_conf()
    ext = ""
    if conf_ext["enabled"] and conf_ext["allow_external_mail"]:
        ext = (f"<label>외부 메일 주소 <span class=mut>(쉼표 구분, 회사 SMTP로 발송 · 1시간 "
               f"{conf_ext['user_external_per_hour']}통 제한)</span></label>"
               f"<input type=text name=ext value='{esc(pre.get('ext', ''))}'>")
    hidden = "".join(f"<input type=hidden name={k} value='{esc(pre[k])}'>" for k in ("reply_to", "forward_of", "draft_id")
                     if pre.get(k))
    body = (f"{'<p class=err>' + esc(err) + '</p>' if err else ''}"
            f"<form method=post action='/mail/new' enctype='multipart/form-data' class=card>{h.csrf()}{hidden}"
            f"<label>받는 사람 <span class=mut>(아이디, @부서코드, @전체 — 쉼표 구분)</span></label>"
            f"<input type=text name=to list=users value='{esc(pre.get('to', ''))}'>"
            f"<label>참조</label><input type=text name=cc list=users value='{esc(pre.get('cc', ''))}'>"
            f"<label>숨은참조 <span class=mut>(다른 받는 사람에게 보이지 않음)</span></label>"
            f"<input type=text name=bcc list=users value='{esc(pre.get('bcc', ''))}'>{ext}"
            f"<label>제목</label><input type=text name=subject maxlength=200 value='{esc(pre.get('subject', ''))}'>"
            f"<label>본문</label><textarea name=body>{esc(pre.get('body', ''))}</textarea>"
            f"<label>첨부 <span class=mut>(개당 10MB·합계 30MB, 문서·이미지·압축 형식)</span></label>"
            f"<input type=file name=files multiple>"
            + ("<p class=mut>전달: 원본 첨부가 함께 갑니다</p>" if pre.get("forward_of") else "")
            + f"<p class=row><button name=go value=draft class=sec>임시보관</button><button name=go value=send>보내기</button>"
            f"</p></form>{h.user_datalist()}")
    return h.ok("메일 쓰기", f"<h2>메일 쓰기</h2>{_tabs('', app.unread_mail_count(user))}{body}")


def route(h, app, user, p):
    f = h.form
    if p == "/mail":
        folder = h.qs.get("f") if h.qs.get("f") in FOLDERS else "inbox"
        q = h.qs.get("q") or None
        rows = app.mail_list(user, folder, q)
        who = (lambda r: r["sender_name"] or r["sender"]) if folder in ("inbox", "trash") else \
            (lambda r: "→ " + (r["to_spec"] or "(받는 사람 없음)"))
        tr = "".join(f"<tr class='{'unread' if folder == 'inbox' and not r['read_at'] else ''}'>"
                     f"<td>{'●' if folder == 'inbox' and not r['read_at'] else ''}</td><td>{esc(who(r))}</td>"
                     f"<td><a href='/mail/{'new?draft=' if folder == 'drafts' else ''}{r['id']}'>{esc(r['subject'] or '(제목 없음)')}"
                     f"</a>{' 📎' if r['files'] else ''}</td><td class=hide>{esc(r['created'][5:16])}</td></tr>" for r in rows)
        body = (f"<h2>메일 · {FOLDERS[folder]}</h2>{_tabs(folder, app.unread_mail_count(user))}"
                f"<form method=get action='/mail' class='card row' role=search><input type=hidden name=f value='{folder}'>"
                f"<input type=text name=q placeholder='제목·본문·보낸 사람' value='{esc(q or '')}'><button class=sec>찾기</button>"
                f"</form><div class=card><table>{tr or '<tr><td class=mut>메일이 없습니다</td></tr>'}</table></div>")
        return h.ok("메일", body)
    if p == "/mail/new":
        if h.method == "POST":
            pre = {k: f.get(k, "") for k in ("to", "cc", "bcc", "subject", "body", "ext", "reply_to", "forward_of",
                                              "draft_id")}
            num = lambda v: int(v) if v and str(v).isdigit() else None  # noqa: E731
            try:
                r = app.mail_send(user, pre["to"], pre["cc"], pre["bcc"], pre["subject"], pre["body"], h.files,
                                  reply_to=num(pre["reply_to"]), forward_of=num(pre["forward_of"]),
                                  draft_id=num(pre["draft_id"]), save_draft=f.get("go") == "draft", ext_to=pre["ext"])
            except core.Invalid as e:  # 입력값 유지한 채 다시
                return compose(h, app, user, pre, str(e))
            return redirect("/mail?f=drafts" if r.get("draft") else "/mail?f=sent")
        q = h.qs
        pre = {}
        for mode in ("reply", "replyall", "forward"):
            if q.get(mode, "").isdigit():
                pre = app.mail_compose_from(user, int(q[mode]), mode)
        if q.get("draft", "").isdigit():
            m = app.mail_read(user, int(q["draft"]))
            if not m["is_sender"] or m["sent"]:
                raise core.NotFound("임시보관 메일 없음")
            pre = {"to": m["to_spec"] or "", "cc": m["cc_spec"] or "", "bcc": m.get("bcc_spec") or "",
                   "subject": m["subject"], "body": m["body"], "draft_id": m["id"], "ext": m.get("ext_spec") or ""}
        if q.get("to"):
            pre.setdefault("to", q["to"][:200])
        return compose(h, app, user, pre)
    m = re.fullmatch(r"/mail/(\d{1,9})(?:/(file|trash|restore|purge)(?:/(\d{1,3}))?)?", p)
    if not m:
        raise core.NotFound("페이지 없음")
    mid, act, idx = int(m.group(1)), m.group(2), m.group(3)
    if act == "file" and idx and h.method == "GET":
        name, path = app.mail_attachment(user, mid, int(idx))
        return Resp(200, path.read_bytes(), {"Content-Type": "application/octet-stream", "Content-Disposition":
                                             f"attachment; filename=\"download\"; filename*=UTF-8''{urllib.parse.quote(name)}"})
    if act in ("trash", "restore", "purge") and h.method == "POST":
        app.mail_move(user, mid, {"trash": "trash", "restore": "restore", "purge": "deleted"}[act])
        return redirect("/mail?f=trash" if act != "trash" else "/mail")
    if act:
        raise core.NotFound("없는 동작")
    x = app.mail_read(user, mid)
    if not x["sent"]:
        return redirect(f"/mail/new?draft={mid}")
    names = lambda L: ", ".join(esc(i["name"] or i["username"]) + (" ✓" if i.get("read_at") else "") for i in L) or "-"  # noqa
    atts = "".join(f"<li><a href='/mail/{mid}/file/{a['idx']}'>{esc(a['name'])}</a> <span class=mut>{a['size']:,}B</span></li>"
                   for a in x["attachments"])
    c = h.csrf()
    mv = ("<form method=post action='/mail/{0}/{1}' class=inline>{2}<button class={3}>{4}</button></form>")
    btns = (f"<a class=btn href='/mail/new?reply={mid}'>답장</a> <a class=btn href='/mail/new?replyall={mid}'>전체 답장</a> "
            f"<a class=btn href='/mail/new?forward={mid}'>전달</a> ")
    if x["folder"] == "trash":
        btns += mv.format(mid, "restore", c, "sec", "되살리기") + " " + mv.format(mid, "purge", c, "warn", "영구 삭제")
    else:
        btns += mv.format(mid, "trash", c, "sec", "휴지통")
    extra = ""
    if x["is_sender"]:
        extra = (f"<p class=mut>숨은참조: {names(x['bcc'])}{' · 외부: ' + esc(x['ext_spec']) if x.get('ext_spec') else ''}"
                 f"</p><p class=mut>✓ = 읽음</p>")
    body = (f"<h2>{esc(x['subject'])}</h2>{_tabs('', app.unread_mail_count(user))}<div class=card>"
            f"<p class=mut>보낸 사람 {esc(x['sender_name'])} ({esc(x['sender'])}) · {esc(x['created'][:16])}</p>"
            f"<p class=mut>받는 사람: {names(x['to'])}</p><p class=mut>참조: {names(x['cc'])}</p>{extra}</div>"
            f"<div class=card><pre class=body>{esc(x['body'])}</pre>"
            f"{'<h3>첨부</h3><ul>' + atts + '</ul>' if atts else ''}</div><p class=row>{btns}</p>")
    return h.ok(x["subject"], body)
