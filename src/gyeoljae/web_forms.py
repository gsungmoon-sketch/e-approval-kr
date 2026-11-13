"""관리자 양식(서식) 관리 화면: 목록·수정(새 버전)·복사·사용중지."""
from __future__ import annotations

import json
import re

from . import core
from . import formdef as fdm
from . import policy as pol
from .web import Resp, esc, redirect


SAVED = "<p class='banner ok' role=status>저장했습니다(새 버전). 이미 기안된 문서는 기안 당시 버전을 유지합니다.</p>"
ON, OFF = "<span class='chip d'>✔ 사용</span>", "<span class='chip w'>○ 중지</span>"


def _sel(name, opts, cur, label):
    o = "".join(f"<option value='{esc(k)}'{' selected' if k == cur else ''}>{esc(v)}</option>" for k, v in opts)
    return f"<label for='fm-{name}'>{label}</label><select id='fm-{name}' name={name}>{o}</select>"


def _txt(name, val, label, hint=""):
    return (f"<label for='fm-{name}'>{label}{' <span class=mut>' + hint + '</span>' if hint else ''}</label>"
            f"<input type=text id='fm-{name}' name={name} value='{esc(val)}'>")


def _edit_page(h, app, user, code, d, ok, err="", new=False, status=200):
    vers = "" if new else "".join(f"<li>v{r['version']} <span class=mut>{esc(r['created'][:16])} {esc(r['actor'])}</span></li>"
                                  for r in app.form_versions(user, code))
    body = (f"{'<p class=err role=alert>' + esc(err) + '</p>' if err else ''}"
            f"{SAVED if h.qs.get('saved') else ''}"
            f"<form method=post action='/admin/forms/{esc(code) if not new else 'new'}' class=card>{h.csrf()}"
            + (_txt("code", code, "양식 코드", "영소문자·숫자·_- (만든 뒤 못 바꿈)") if new else "")
            + _txt("name", d.get("name", ""), "양식 이름")
            + "<div class=row><div>" + _sel("category", [(c, c) for c in fdm.CATEGORIES], d.get("category"), "분류")
            + "</div><div>" + _sel("doc_type", list(pol.DOC_TYPES.items()), d.get("doc_type"), "결재 종류(결재규정 연결)")
            + "</div><div>" + _sel("retention", [(r, r) for r in fdm.RETENTIONS], d.get("retention", "5년"), "보존기간")
            + "</div></div>"
            + _txt("prefix", d.get("prefix", ""), "문서번호 접두어", "설정의 문서번호 형식에 {prefix} 가 있을 때 사용")
            + _txt("depts", ",".join(d.get("depts", [])), "사용 부서", "부서 코드, 쉼표 구분 · 비우면 전 부서")
            + _txt("cc_depts", ",".join(d.get("cc_depts", [])), "필수 참조 부서", "그 부서 부서장이 자동 참조 (예: 재정 → MGT)")
            + _txt("amount", d.get("amount", ""), "금액 필드", "결재규정 전결 한도에 쓰임: 금액 필드 키 또는 표키.합계열 (예: items.amount)")
            + _txt("title_hint", d.get("title_hint", ""), "제목 안내")
            + f"<label for=fm-bh>본문 기본 내용</label><textarea id=fm-bh class=short name=body_hint>{esc(d.get('body_hint', ''))}</textarea>"
            + f"<label for=fm-f>항목(JSON) <span class=mut>종류: {esc(' '.join(fdm.FIELD_TYPES))} · 표 열 계산식 'qty*price' · "
              f"합계 sum</span></label><textarea id=fm-f name=fields style='min-height:320px;font-family:monospace'>"
              f"{esc(d['fields'] if isinstance(d.get('fields'), str) else json.dumps(d.get('fields', []), ensure_ascii=False, indent=1))}"
              f"</textarea><p class=row><button>{'만들기' if new else '새 버전으로 저장'}</button></p></form>"
            + (f"<div class=card><h3>버전</h3><ul>{vers}</ul></div>" if vers else ""))
    r = ok(f"양식 {'새로 만들기' if new else '· ' + d.get('name', code)}", body)
    if status != 200:
        r.status = status
    return r


def _from_post(f):
    d = {k: f.get(k, "") for k in ("name", "category", "doc_type", "retention", "prefix", "depts", "cc_depts", "amount",
                                    "title_hint", "body_hint")}
    try:
        d["fields"] = json.loads(f.get("fields") or "[]")
    except ValueError as e:
        raise core.Invalid(f"항목 JSON 오류: {e}")
    return d


def forms_admin(h, app, user, p, ok):
    f, post = h.form, h.method == "POST"
    if p == "/admin/forms":
        rows = app.forms_list(user, all_=True)
        c = h.csrf()
        tr = "".join(
            f"<tr><td><a href='/admin/forms/{esc(r['code'])}'>{esc(r['name'])}</a><br><span class=mut>{esc(r['code'])}</span></td>"
            f"<td>{esc(r['category'])}</td><td>{esc(pol.DOC_TYPES[r['doc_type']])}</td><td>{esc(r['retention'])}</td>"
            f"<td>v{r['version']}</td><td>{esc(','.join(r['depts']) or '전체')}</td>"
            f"<td>{ON if r['enabled'] else OFF}</td>"
            f"<td><form method=post action='/admin/forms/{esc(r['code'])}/{'disable' if r['enabled'] else 'enable'}' "
            f"class=inline>{c}<button class=sec>{'사용 중지' if r['enabled'] else '다시 사용'}</button></form> "
            f"<a class=btn href='/admin/forms/new?copy={esc(r['code'])}'>복사</a></td></tr>" for r in rows)
        return ok("양식", f"<p><a class=btn href='/admin/forms/new?copy=general'>+ 새 양식(일반기안 복사)</a></p>"
                        f"<div class=card><table class=list><tr><th>양식</th><th>분류</th><th>결재 종류</th><th>보존</th>"
                        f"<th>버전</th><th>사용 부서</th><th>상태</th><th></th></tr>{tr}</table></div>"
                        f"<p class=mut>수정하면 새 버전이 생기고, 이미 기안된 문서는 기안 당시 버전을 그대로 씁니다.</p>")
    if p == "/admin/forms/new":
        if post:
            code = f.get("code", "")
            try:
                d = _from_post(f)
                app.form_save(user, code, d, new=True)
            except core.Invalid as e:
                return _edit_page(h, app, user, code, _from_post_safe(f), ok, str(e), new=True, status=400)
            return redirect(f"/admin/forms/{code.strip().lower()}?saved=1")
        src = app.form_get(user, h.qs.get("copy") or "general")
        d = {k: v for k, v in src.items() if k not in ("code", "version", "enabled", "builtin")}
        d["name"] = (d["name"] + " (사본)")[:40]
        return _edit_page(h, app, user, (src["code"] + "_2")[:24], d, ok, new=True)
    m = re.fullmatch(r"/admin/forms/([a-z][a-z0-9_-]{1,23})(?:/(enable|disable))?", p)
    if not m:
        raise core.NotFound("페이지 없음")
    code, act = m.group(1), m.group(2)
    if act and post:
        app.form_enable(user, code, act == "enable")
        return redirect("/admin/forms")
    if act:
        raise core.NotFound("없는 동작")
    if post:
        try:
            app.form_save(user, code, _from_post(f))
        except core.Invalid as e:
            return _edit_page(h, app, user, code, _from_post_safe(f), ok, str(e), status=400)
        return redirect(f"/admin/forms/{code}?saved=1")
    return _edit_page(h, app, user, code, app.form_get(user, code), ok)


def _from_post_safe(f):
    d = {k: f.get(k, "") for k in ("name", "category", "doc_type", "retention", "prefix", "amount", "title_hint",
                                    "body_hint")}
    d["depts"] = [x for x in re.split(r"[,\s]+", f.get("depts", "")) if x]
    d["cc_depts"] = [x for x in re.split(r"[,\s]+", f.get("cc_depts", "")) if x]
    d["fields"] = f.get("fields", "")
    return d
