"""관리자 웹 화면: 사원·부서·직위·결재규정·변경요청·설정·감사. 모든 동작은 core(App) 함수 호출."""
from __future__ import annotations

import datetime as _dt
import re
import urllib.parse

from . import core
from . import policy as pol
from .org import EMP_STATUS
from .web import Resp, esc, redirect

NAV = [("/admin/emp", "사원 관리"), ("/admin/dept", "부서"), ("/admin/pos", "직위"), ("/admin/policy", "결재규정"), ("/admin/forms", "양식"),
       ("/admin/changes", "변경요청·설정"), ("/admin/smtp", "외부 메일"), ("/admin/audit", "감사로그")]


def admin_route(h, app, user, p):
    nav = "<p class=row>" + "".join(f"<a href='{u}'>{n}</a>" for u, n in NAV) + "</p>"

    def ok(title, body):
        return h.ok(title, f"<h2>관리 · {esc(title)}</h2>{nav}{body}")

    def pend(r, back):
        if isinstance(r, dict) and r.get("pending"):
            return ok("2인 승인 대기", f"<div class=card><p>변경요청 <b>#{r['pending']}</b> 이 만들어졌습니다. "
                                      f"다른 관리자가 승인해야 적용됩니다.</p><a href='{back}'>돌아가기</a></div>")
        return redirect(back)

    f = h.form
    post = h.method == "POST"
    P = app.positions()
    pos_opts = lambda sel=None: "".join(  # noqa: E731
        f"<option value='{esc(k)}' {'selected' if k == sel else ''}>{esc(v['name'])}</option>" for k, v in P.items())
    depts = app.list_depts()
    dept_opts = lambda sel=None: "".join(  # noqa: E731
        f"<option value='{esc(d['code'])}' {'selected' if d['code'] == sel else ''}>{esc(d['name'])} ({esc(d['code'])})"
        f"</option>" for d in depts)

    if p == "/admin":
        return redirect("/admin/emp")
    if p.startswith("/admin/forms"):
        from .web_forms import forms_admin
        return forms_admin(h, app, user, p, ok)

    # ---------------------------------------------------------------- 사원
    if p == "/admin/emp":
        if post:
            r = app.emp_add(user, f.get("name"), f.get("dept"), f.get("position"), f.get("emp_no") or None,
                            f.get("username") or None, f.get("email") or None, f.get("phone") or None,
                            f.get("hired") or None, f.get("title", ""), bool(f.get("admin")))
            extra = f"<p>2인 승인 대기 변경요청: {r['pending']}</p>" if r["pending"] else ""
            return ok("사원 등록 완료", f"<div class=card><p>사번 <b>{esc(r['emp_no'])}</b> · 아이디 <b>{esc(r['username'])}</b></p>"
                                       f"<p>임시 비밀번호: <b class=ok>{esc(r['temp_password'])}</b></p><p class=err>"
                                       f"이 화면에서 한 번만 보입니다. 본인에게 전달하세요(첫 로그인 시 변경).</p>{extra}"
                                       f"<a href='/admin/emp'>사원 목록</a></div>")
        q = h.qs
        rows = app.emp_list(user, q.get("dept") or None, q.get("q") or None, q.get("status") or None)
        tr = "".join(f"<tr><td>{esc(r['emp_no'])}</td><td><a href='/admin/emp/{urllib.parse.quote(r['username'])}'>"
                     f"{esc(r['name'])}</a> <span class=mut>{esc(r['username'])}</span></td><td>{esc(r['dept_name'])}</td>"
                     f"<td>{esc(r['position_name'] or '-')}</td><td>{esc(r['status_ko'])}"
                     f"{' · 관리자' if r['role'] == 'admin' else ''}</td></tr>" for r in rows)
        sts = "".join(f"<option value='{k}' {'selected' if q.get('status') == k else ''}>{v}</option>"
                      for k, v in EMP_STATUS.items())
        body = (f"<form method=get action='/admin/emp' class=card><div class=row><input type=text name=q placeholder='이름·사번' "
                f"value='{esc(q.get('q'))}'><select name=dept><option value=''>전체 부서</option>{dept_opts(q.get('dept'))}"
                f"</select><select name=status><option value=''>전체 상태</option>{sts}</select><button>찾기</button></div></form>"
                f"<div class=card><table><tr><th>사번</th><th>이름</th><th>부서</th><th>직위</th><th>상태</th></tr>{tr}</table>"
                f"<p><a href='/admin/emp/import'>CSV 일괄 등록</a> · <a href='/admin/emp/export'>CSV 내보내기(UTF-8)</a> · "
                f"<a href='/admin/emp/export?enc=cp949'>CSV(엑셀 CP949)</a></p></div>"
                f"<form method=post action='/admin/emp' class=card>{h.csrf()}<h3>사원 등록</h3><div class=row>"
                f"<div><label>이름*</label><input type=text name=name required></div>"
                f"<div><label>부서*</label><select name=dept>{dept_opts()}</select></div>"
                f"<div><label>직위*</label><select name=position>{pos_opts('member')}</select></div></div><div class=row>"
                f"<div><label>사번 <span class=mut>(비우면 입사일+일련번호 자동)</span></label><input type=text name=emp_no></div>"
                f"<div><label>로그인 아이디 <span class=mut>(비우면 사번)</span></label><input type=text name=username></div>"
                f"<div><label>입사일 <span class=mut>(비우면 오늘)</span></label><input type=date name=hired "
                f"value='{_dt.date.today().isoformat()}'></div></div><div class=row>"
                f"<div><label>이메일</label><input type=text name=email></div><div><label>전화</label>"
                f"<input type=text name=phone></div><div><label>직책</label><input type=text name=title></div></div>"
                f"<label><input type=checkbox name=admin value=1> 관리자 권한</label><p><button>등록</button></p></form>")
        return ok("사원 관리", body)

    if p == "/admin/emp/export":
        enc = "cp949" if h.qs.get("enc") == "cp949" else "utf-8-sig"
        data = app.emp_export(user, enc)
        fn = f"employees-{_dt.date.today().isoformat()}.csv"
        return Resp(200, data, {"Content-Type": f"text/csv; charset={'cp949' if enc == 'cp949' else 'utf-8'}",
                                "Content-Disposition": f"attachment; filename=\"{fn}\""})

    if p == "/admin/emp/import":
        res = ""
        if post:
            if not h.files:
                raise core.Invalid("CSV 파일을 선택하세요")
            r = app.emp_import(user, h.files[0][1], apply=f.get("go") == "apply")
            tr = "".join(f"<tr><td>{x['line']}</td><td>{esc(x['action'])}</td><td>{esc(x['emp_no'])}</td>"
                         f"<td>{esc(x['name'])}</td><td class=err>{esc(x['error'])}</td></tr>" for x in r["rows"])
            pw = "".join(f"<tr><td>{esc(x['emp_no'])}</td><td>{esc(x['username'])}</td><td>{esc(x['name'])}</td>"
                         f"<td><b>{esc(x['temp_password'])}</b></td></tr>" for x in r["created"] if x["temp_password"])
            state = ("<p class=ok>반영 완료</p>" if r["applied"] else
                     (f"<p class=err>오류 {r['errors']}건 — 반영하지 않았습니다. 고친 뒤 다시 올리세요.</p>" if r["errors"]
                      else "<p>미리보기입니다. 같은 파일로 [반영]을 누르세요.</p>"))
            res = (f"<div class=card>{state}<table><tr><th>줄</th><th>동작</th><th>사번</th><th>이름</th><th>오류</th></tr>{tr}</table>"
                   + (f"<h3>임시 비밀번호 (한 번만 표시)</h3><table>{pw}</table>" if pw else "") + "</div>")
        body = (f"{res}<form method=post action='/admin/emp/import' enctype='multipart/form-data' class=card>{h.csrf()}"
                f"<p class=mut>열: 사번,이름,부서,직위,아이디,이메일,전화,입사일,상태,직책 (이름·부서 필수, UTF-8 또는 엑셀 CP949). "
                f"사번이 비면 그 줄의 입사일(없으면 오늘)+일련번호로 파일 순서대로 자동 발급. "
                f"사번이 이미 있으면 수정, 없으면 등록. 오류가 한 줄이라도 있으면 전체를 반영하지 않습니다.</p>"
                f"<input type=file name=csv accept='.csv,text/csv' required><p class=row>"
                f"<button name=go value=preview class=sec>미리보기</button><button name=go value=apply>반영</button></p></form>")
        return ok("CSV 일괄 등록", body)

    m = re.fullmatch(r"/admin/emp/([a-z0-9][a-z0-9_.-]{1,31})(?:/(\w+))?", p)
    if m:
        who, act = m.group(1), m.group(2)
        back = f"/admin/emp/{who}"
        if post and act == "edit":
            app.emp_edit(user, who, name=f.get("name"), email=f.get("email", ""), phone=f.get("phone", ""),
                         hired=f.get("hired", ""), title=f.get("title", ""))
            return redirect(back)
        if post and act == "move":
            e = app.emp_get(user, who)
            nd = f.get("dept") if f.get("dept") != e["dept"] else None
            np = f.get("position") if f.get("position") != e["position"] else None
            return pend(app.emp_move(user, who, nd, np), back)
        if post and act == "status":
            app.emp_set_status(user, who, f.get("status"))
            return redirect(back)
        if post and act == "role":
            return pend(app.emp_set_role(user, who, f.get("role")), back)
        if post and act == "resetpw":
            r = app.emp_reset_password(user, who)
            return ok("임시 비밀번호", f"<div class=card><p>{esc(r['username'])} 임시 비밀번호: <b class=ok>"
                                      f"{esc(r['temp_password'])}</b></p><p class=err>한 번만 보입니다.</p>"
                                      f"<a href='{back}'>돌아가기</a></div>")
        if act:
            raise core.NotFound("없는 동작")
        e = app.emp_get(user, who)
        sts = "".join(f"<option value='{k}' {'selected' if e['status'] == k else ''}>{v}</option>" for k, v in EMP_STATUS.items())
        hist = "".join(f"<tr><td class=mut>{esc(x['at'][:16])}</td><td>{esc(x['actor'])}</td><td>{esc(x['field'])}</td>"
                       f"<td>{esc(x['before'])} → {esc(x['after'])}</td></tr>" for x in reversed(e["history"]))
        c = h.csrf()
        body = (f"<div class=card><h3>{esc(e['name'])} <span class=mut>{esc(e['emp_no'])} · {esc(e['username'])} · "
                f"{esc(e['status_ko'])}{' · 관리자' if e['role'] == 'admin' else ''}</span></h3></div>"
                f"<form method=post action='{back}/edit' class=card>{c}<h3>인적 사항</h3><div class=row>"
                f"<div><label>이름</label><input type=text name=name value='{esc(e['name'])}'></div>"
                f"<div><label>직책</label><input type=text name=title value='{esc(e['title'])}'></div>"
                f"<div><label>입사일</label><input type=date name=hired value='{esc(e['hired'])}'></div></div><div class=row>"
                f"<div><label>이메일</label><input type=text name=email value='{esc(e['email'])}'></div>"
                f"<div><label>전화</label><input type=text name=phone value='{esc(e['phone'])}'></div></div>"
                f"<p><button>저장</button></p></form>"
                f"<form method=post action='{back}/move' class=card>{c}<h3>부서 이동 · 직위 변경</h3><div class=row>"
                f"<select name=dept>{dept_opts(e['dept'])}</select><select name=position>{pos_opts(e['position'])}</select>"
                f"<button>변경</button></div><p class=mut>변경 시 이전·새 부서장과 본인에게 쪽지가 갑니다.</p></form>"
                f"<div class=card><h3>상태 · 권한</h3><form method=post action='{back}/status' class=inline>{c}"
                f"<select name=status>{sts}</select> <button class=sec>상태 변경</button></form> "
                f"<form method=post action='{back}/role' class=inline>{c}<input type=hidden name=role "
                f"value='{'user' if e['role'] == 'admin' else 'admin'}'><button class=sec>"
                f"{'관리자 해제' if e['role'] == 'admin' else '관리자 지정'}</button></form> "
                f"<form method=post action='{back}/resetpw' class=inline>{c}<button class=warn>임시 비밀번호 재발급</button></form>"
                f"<p class=mut>퇴사·휴직 = 로그인 차단·결재선 제외. 문서·기록은 지우지 않습니다.</p></div>"
                f"<div class=card><h3>변경 이력</h3><table>{hist}</table></div>")
        return ok("사원 상세", body)

    # ---------------------------------------------------------------- 부서
    if p == "/admin/dept":
        if post:
            op = f.get("op")
            if op == "add":
                app.add_dept(user, f.get("code", ""), f.get("name", ""))
            elif op == "rename":
                app.dept_rename(user, f.get("code", ""), f.get("name", ""))
            elif op in ("disable", "enable"):
                app.dept_set_active(user, f.get("code", ""), op == "enable")
            return redirect("/admin/dept")
        c = h.csrf()
        tr = "".join(
            f"<tr><td>{esc(d['code'])}</td><td><form method=post action='/admin/dept' class=inline>{c}"
            f"<input type=hidden name=op value=rename><input type=hidden name=code value='{esc(d['code'])}'>"
            f"<input type=text name=name value='{esc(d['name'])}'> <button class=sec>이름 변경</button></form></td>"
            f"<td>{d['members']}명</td><td><form method=post action='/admin/dept' class=inline>{c}"
            f"<input type=hidden name=op value={'disable' if d['active'] else 'enable'}><input type=hidden name=code "
            f"value='{esc(d['code'])}'><button class=sec>{'폐지' if d['active'] else '복구'}</button></form></td></tr>"
            for d in app.list_depts(True))
        body = (f"<div class=card><table><tr><th>코드</th><th>이름</th><th>인원</th><th></th></tr>{tr}</table>"
                f"<p class=mut>부서코드는 문서번호에 쓰이므로 바꿀 수 없습니다(이름만 변경).</p></div>"
                f"<form method=post action='/admin/dept' class=card>{c}<input type=hidden name=op value=add><h3>부서 추가</h3>"
                f"<div class=row><input type=text name=code placeholder='코드(예: DEV)' required>"
                f"<input type=text name=name placeholder='부서명' required><button>추가</button></div></form>")
        return ok("부서", body)

    # ---------------------------------------------------------------- 직위
    if p == "/admin/pos":
        if post:
            if f.get("op") == "add":
                r = app.pos_add(user, f.get("code", ""), f.get("name", ""), int(f.get("rank") or 0),
                                f.get("scope", "dept"), bool(f.get("dept_head")))
            else:
                r = app.pos_update(user, f.get("code", ""), f.get("name") or None, int(f["rank"]) if f.get("rank") else None,
                                   f.get("scope") or None, f.get("dept_head") == "1", f.get("active") == "1")
            return pend(r, "/admin/pos")
        c = h.csrf()
        tr = "".join(
            f"<tr><td>{esc(k)}</td><td colspan=5><form method=post action='/admin/pos' class=row>{c}"
            f"<input type=hidden name=op value=edit><input type=hidden name=code value='{esc(k)}'>"
            f"<input type=text name=name value='{esc(v['name'])}'><input type=text name=rank value='{v['rank']}'>"
            f"<select name=scope><option value=dept {'selected' if v['scope'] == 'dept' else ''}>부서 내</option>"
            f"<option value=org {'selected' if v['scope'] == 'org' else ''}>전사</option></select>"
            f"<select name=dept_head><option value=0>열람권 없음</option><option value=1 {'selected' if v['dept_head'] else ''}>"
            f"부서 문서 열람(부서장)</option></select><select name=active><option value=1>사용</option>"
            f"<option value=0 {'selected' if not v['active'] else ''}>폐지</option></select><button class=sec>저장</button>"
            f"</form></td></tr>" for k, v in app.positions(True).items())
        body = (f"<div class=card><p class=mut>서열 숫자가 클수록 높은 직위. '부서 내' 직위는 결재선에서 기안 부서 안에서, "
                f"'전사' 직위는 회사 전체에서 찾습니다. 부서장 열람권 부여는 2인 승인 대상입니다.</p>"
                f"<table><tr><th>코드</th><th colspan=5>이름 · 서열 · 범위 · 열람권 · 상태</th></tr>{tr}</table></div>"
                f"<form method=post action='/admin/pos' class=card>{c}<input type=hidden name=op value=add><h3>직위 추가</h3>"
                f"<div class=row><input type=text name=code placeholder='코드(영문, 예: senior)' required>"
                f"<input type=text name=name placeholder='이름(예: 책임)' required><input type=text name=rank placeholder='서열' required>"
                f"<select name=scope><option value=dept>부서 내</option><option value=org>전사</option></select></div>"
                f"<label><input type=checkbox name=dept_head value=1> 부서 문서 열람권(부서장급)</label>"
                f"<p><button>추가</button></p></form>")
        return ok("직위", body)

    # ---------------------------------------------------------------- 결재규정
    if p == "/admin/policy":
        tr = "".join(f"<tr><td>{esc(r['code'])}</td><td><a href='/admin/policy/{esc(r['code'])}'>{esc(r['name'])}</a></td>"
                     f"<td>{'v' + str(r['version']) if r['version'] else '<span class=mut>규정 없음(자유 결재선)</span>'}</td>"
                     f"</tr>" for r in app.policy_overview(user))
        return ok("결재규정", f"<div class=card><table><tr><th>코드</th><th>부서</th><th>버전</th></tr>{tr}</table></div>"
                             f"<p><a href='/admin/preview'>결재선 미리보기</a></p>")

    if p == "/admin/preview":
        q = h.qs
        res = ""
        types = "".join(f"<option value='{k}' {'selected' if q.get('type') == k else ''}>{v}</option>"
                        for k, v in pol.DOC_TYPES.items())
        if q.get("dept") or q.get("drafter"):
            pv = app.preview_line(user, q.get("type") or "general", q.get("amount") or None, q.get("dept") or None,
                                  q.get("drafter") or None)
            res = "<div class=card>" + (esc(pv["note"]) if pv["free"] else (
                f"<p class=mut>{esc(pv['dept'])} v{pv['policy_version']} · {esc(pol.DOC_TYPES[pv['doc_type']])} "
                f"{esc(pol.fmt_amount(pv['amount']))}</p><div class=line>" + "".join(
                    f"<div class=step>{esc(x['name'])} <span class=mut>{esc(x['position'] or '')}</span>"
                    f"{' <b>전결</b>' if x['final'] else ''}</div>" for x in pv["line"]) + "</div>"
                + (f"<p>필수 참조: {esc(', '.join(pv['cc']))}</p>" if pv["cc"] else ""))) + "</div>"
        body = (f"<form method=get action='/admin/preview' class=card><div class=row><select name=dept>{dept_opts(q.get('dept'))}"
                f"</select><select name=type>{types}</select><input type=text name=amount placeholder='금액 예: 150만' "
                f"value='{esc(q.get('amount'))}'><input type=text name=drafter placeholder='기안자 아이디(선택)' "
                f"value='{esc(q.get('drafter'))}'><button>보기</button></div><p class=mut>기안자를 비우면 그 부서의 "
                f"가장 낮은 직위 사원이 기안한다고 가정합니다.</p></form>{res}")
        return ok("결재선 미리보기", body)

    m = re.fullmatch(r"/admin/policy/([A-Z][A-Z0-9]{1,9})(/history)?", p)
    if m:
        dept, hist = m.group(1), m.group(2)
        if hist:
            hs = app.policy_history(user, dept)
            body = "".join(f"<div class=card><b>v{x['version']}</b> <span class=mut>{esc(x['created'][:16])} "
                           f"{esc(x['created_by'])}{' · 승인 ' + esc(x['approved_by']) if x['approved_by'] else ''}</span>"
                           f"<pre class=body>{esc(x['diff'])}</pre></div>" for x in hs) or "<p class=mut>(이력 없음)</p>"
            return ok(f"{dept} 규정 이력", body)
        if post:
            if f.get("op") == "clear":
                return pend(app.policy_set(user, dept, None), f"/admin/policy/{dept}")
            types = {}
            for t in pol.DOC_TYPES:
                if f.get(f"{t}_on"):
                    types[t] = {"line": f.get(f"{t}_line", ""), "jeon": f.get(f"{t}_jeon", ""),
                                "allow_extra": bool(f.get(f"{t}_extra")), "cc": f.get(f"{t}_cc", "")}
            dg = {"allowed": bool(f.get("dg_allowed")), "same_dept_only": bool(f.get("dg_same")),
                  "min_pos": f.get("dg_min") or None, "max_days": f.get("dg_days") or None}
            return pend(app.policy_set(user, dept, {"types": types, "delegation": dg}), f"/admin/policy/{dept}")
        g = app.policy_get(user, dept)
        T = g["text"]
        c = h.csrf()
        rows = ""
        for t, ko in pol.DOC_TYPES.items():
            x = T[t]
            rows += (f"<div class=card><label><input type=checkbox name={t}_on value=1 {'checked' if x['enabled'] else ''}> "
                     f"<b>{ko}</b> 문서 규정 사용</label><label>결재선 <span class=mut>(순서대로, 예: 팀장?, 부서장, 대표 — "
                     f"?=해당자 없으면 생략, @아이디=특정인)</span></label><input type=text name={t}_line value='{esc(x['line'])}'>"
                     f"<label>전결 <span class=mut>(예: 부서장:100만, 임원:500만 — 금액 없으면 무제한)</span></label>"
                     f"<input type=text name={t}_jeon value='{esc(x['jeon'])}'><label>필수 참조 <span class=mut>(아이디, 쉼표)"
                     f"</span></label><input type=text name={t}_cc value='{esc(x['cc'])}'><label><input type=checkbox "
                     f"name={t}_extra value=1 {'checked' if x['allow_extra'] else ''}> 기안자가 결재자 추가 가능</label></div>")
        d = T["delegation"]
        minp = "<option value=''>제한 없음</option>" + "".join(
            f"<option value='{esc(k)}' {'selected' if d['min_pos'] == k else ''}>{esc(v['name'])} 이상</option>"
            for k, v in P.items())
        status = (f"현재 v{g['version']} ({esc(g['created'][:16])} {esc(g['created_by'])})" if g["policy"]
                  else "현재 규정 없음 — 기안자가 결재선을 직접 지정합니다")
        body = (f"<div class=card><p>{status} · <a href='/admin/policy/{dept}/history'>변경 이력(diff)</a> · "
                f"<a href='/admin/preview?dept={dept}&type=expense&amount=150%EB%A7%8C'>미리보기</a></p>"
                f"<p class=mut>저장하면 새 버전이 되며 <b>새 상신부터</b> 적용됩니다. 진행 중 문서는 상신 당시 버전을 유지합니다. "
                f"변경은 감사로그에 기록되고 부서장에게 쪽지가 갑니다.</p></div>"
                f"<form method=post action='/admin/policy/{dept}'>{c}{rows}<div class=card><h3>대결(위임) 규칙</h3>"
                f"<label><input type=checkbox name=dg_allowed value=1 {'checked' if d['allowed'] else ''}> 대결 위임 허용</label>"
                f"<label><input type=checkbox name=dg_same value=1 {'checked' if d['same_dept_only'] else ''}> 같은 부서에게만</label>"
                f"<div class=row><div><label>대결자 최소 직위</label><select name=dg_min>{minp}</select></div>"
                f"<div><label>최대 기간(일)</label><input type=text name=dg_days value='{esc(d['max_days'])}'></div></div></div>"
                f"<p><button>새 버전으로 저장</button></p></form>"
                f"<form method=post action='/admin/policy/{dept}' class=card>{c}<input type=hidden name=op value=clear>"
                f"<button class=warn>규정 해제(자유 결재선)</button></form>")
        return ok(f"{dept} 결재규정", body)

    # ---------------------------------------------------------------- 변경요청·설정
    if p == "/admin/changes":
        if post:
            if f.get("op") == "setting":
                return pend(app.setting_set(user, f.get("key", ""), f.get("value", "")), "/admin/changes")
            app.change_decide(user, int(f.get("id", "0")), f.get("op") == "approve")
            return redirect("/admin/changes")
        c = h.csrf()
        rows = app.change_list(user, None)
        tr = "".join(
            f"<tr><td>#{r['id']}</td><td>{esc(r['created'][:16])}</td><td>{esc(r['requested_by'])}</td><td>{esc(r['summary'])}</td>"
            f"<td>{esc(r['status'])}{' ' + esc(r['decided_by']) if r['decided_by'] else ''}</td><td>"
            + (f"<form method=post action='/admin/changes' class=inline>{c}<input type=hidden name=id value='{r['id']}'>"
               f"<button name=op value=approve>승인</button> <button name=op value=reject class=sec>거절</button></form>"
               if r["status"] == "pending" and r["requested_by"] != user else "")
            + "</td></tr>" for r in rows)
        def srow(k, v):
            head = (f"<form method=post action='/admin/changes' class=inline>{c}<input type=hidden name=op value=setting>"
                    f"<input type=hidden name=key value='{esc(k)}'>")
            if k == "doc_number":
                ctl = (f"{head}<input type=text name=value value='{esc(v['value'])}' aria-label='문서번호 형식' "
                       f"style='width:14em'> <button class=sec>저장</button></form>")
                return f"<tr><td>{esc(v['label'])}</td><td><code>{esc(v['value'])}</code></td><td>{ctl}</td></tr>"
            if k == "emp_digits":
                opts = "".join(f"<option value={d} {'selected' if v['value'] == d else ''}>{d}자리</option>" for d in (2, 3))
                ctl = f"{head}<select name=value>{opts}</select> <button class=sec>저장</button></form>"
                return f"<tr><td>{esc(v['label'])}</td><td>{v['value']}자리</td><td>{ctl}</td></tr>"
            ctl = (f"{head}<input type=hidden name=value value='{0 if v['value'] else 1}'><button class=sec>"
                   f"{'끄기' if v['value'] else '켜기'}</button></form>")
            return f"<tr><td>{esc(v['label'])}</td><td>{'켜짐' if v['value'] else '꺼짐'}</td><td>{ctl}</td></tr>"
        st = "".join(srow(k, v) for k, v in app.settings(user).items())
        body = (f"<div class=card><h3>설정</h3><table>{st}</table><p class=mut>2인 승인을 켜면 결재규정·관리자 지정·"
                f"부서장급 직위 부여가 다른 관리자 승인 후 적용됩니다(끄는 것도 승인 필요).</p></div>"
                f"<div class=card><h3>변경요청</h3><table>{tr or '<tr><td class=mut>(없음)</td></tr>'}</table></div>")
        return ok("변경요청·설정", body)

    # ---------------------------------------------------------------- 외부 메일(SMTP)
    if p == "/admin/smtp":
        note = ""
        if post:
            if f.get("op") == "test":
                r = app.smtp_test(user, f.get("to", ""))
                note = f"<p class={'ok' if r['ok'] else 'err'}>{'시험 메일 보냄' if r['ok'] else '실패: ' + esc(r['error'])}</p>"
            elif f.get("op") == "flush":
                r = app.deliver_outbox(limit=10)
                note = f"<p class=ok>발송 {r.get('sent', 0)} · 실패 {r.get('failed', 0)}</p>"
            else:
                app.smtp_set(user, host=f.get("host", ""), port=f.get("port") or 587, security=f.get("security", "starttls"),
                             user=f.get("user", ""), password=f.get("password", ""), from_addr=f.get("from_addr", ""),
                             from_name=f.get("from_name", ""), enabled=bool(f.get("enabled")),
                             notify_approvals=bool(f.get("notify_approvals")),
                             allow_external_mail=bool(f.get("allow_external_mail")),
                             rate_per_hour=f.get("rate_per_hour") or 200,
                             user_external_per_hour=f.get("user_external_per_hour") or 20)
                return redirect("/admin/smtp")
        d = app.smtp_get(user)
        c = h.csrf()
        chk = lambda k: "checked" if d[k] else ""  # noqa: E731
        sec = "".join(f"<option value={k} {'selected' if d['security'] == k else ''}>{v}</option>"
                      for k, v in (("starttls", "STARTTLS(587)"), ("ssl", "SSL/TLS(465)"), ("none", "없음(권장 안 함)")))
        ob = ", ".join(f"{esc(k)} {v}" for k, v in d["outbox"].items()) or "없음"
        body = (f"{note}<div class=card><p class=mut>회사 메일(Google Workspace·Naver Works·Microsoft 365 등)의 SMTP 릴레이로 "
                f"<b>결재 알림</b>을 사원의 외부 이메일로 보내고, 허용하면 사내 메일을 외부 주소로도 보냅니다. 받는 메일 서버는 아닙니다. "
                f"비밀번호는 데이터 폴더의 smtp.json 에 저장됩니다(저장소·백업 접근 권한 주의).</p><p>대기열: {ob}</p></div>"
                f"<form method=post action='/admin/smtp' class=card>{c}<div class=row>"
                f"<div><label>SMTP 호스트</label><input type=text name=host value='{esc(d['host'])}' placeholder='smtp.gmail.com'></div>"
                f"<div><label>포트</label><input type=text name=port value='{esc(d['port'])}'></div>"
                f"<div><label>보안</label><select name=security>{sec}</select></div></div><div class=row>"
                f"<div><label>계정</label><input type=text name=user value='{esc(d['user'])}' autocomplete=off></div>"
                f"<div><label>비밀번호 <span class=mut>(비우면 유지)</span></label><input type=password name=password "
                f"value='' autocomplete=new-password></div></div><div class=row>"
                f"<div><label>보내는 주소</label><input type=text name=from_addr value='{esc(d['from_addr'])}'></div>"
                f"<div><label>보내는 이름</label><input type=text name=from_name value='{esc(d['from_name'])}'></div></div>"
                f"<div class=row><div><label>전체 시간당 한도</label><input type=text name=rate_per_hour value='{d['rate_per_hour']}'></div>"
                f"<div><label>1인 외부 메일 시간당</label><input type=text name=user_external_per_hour "
                f"value='{d['user_external_per_hour']}'></div></div>"
                f"<label><input type=checkbox name=enabled value=1 {chk('enabled')}> 외부 메일 사용</label>"
                f"<label><input type=checkbox name=notify_approvals value=1 {chk('notify_approvals')}> 결재 알림(도착·완료·반려)을 "
                f"사원 이메일로</label><label><input type=checkbox name=allow_external_mail value=1 {chk('allow_external_mail')}>"
                f" 사내 메일에서 외부 주소로 보내기 허용</label><p><button>저장</button></p></form>"
                f"<form method=post action='/admin/smtp' class='card row'>{c}<input type=hidden name=op value=test>"
                f"<input type=text name=to placeholder='시험 받을 주소'><button class=sec>시험 발송</button></form>"
                f"<form method=post action='/admin/smtp' class=card>{c}<input type=hidden name=op value=flush>"
                f"<button class=sec>대기열 지금 보내기</button></form>")
        return ok("외부 메일(SMTP)", body)

    # ---------------------------------------------------------------- 감사
    if p == "/admin/audit":
        note = ""
        if h.qs.get("verify"):
            v = app.verify_audit(user)
            note = (f"<div class=card><b class={'ok' if v['ok'] else 'err'}>{'감사로그 정상' if v['ok'] else '변조 감지!'}"
                    f"</b> 항목 {v['entries']}<ul>" + "".join(f"<li>{esc(e)}</li>" for e in v["errors"]) + "</ul></div>")
        hd = app.audit_head(user)
        logs = "".join(f"<tr><td>{e['seq']}</td><td class=hide>{esc(e['ts'][5:19])}</td><td>{esc(e['actor'])}</td>"
                       f"<td>{esc(e['action'])}</td><td>{esc(e.get('doc') or '')}</td></tr>"
                       for e in reversed(app.audit_list(user, limit=100)))
        body = (f"{note}<div class=card><p><a href='/admin/audit?verify=1'>무결성 검증</a></p><p class=mut>체인 머리값 "
                f"(외부 보관용): seq {hd['seq']} · {esc(hd['hash'])}</p></div>"
                f"<div class=card><h3>최근 100건</h3><table>{logs}</table></div>")
        return ok("감사로그", body)

    raise core.NotFound("페이지 없음")
