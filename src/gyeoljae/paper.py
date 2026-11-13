"""출력(A4) 문서 — 인쇄된 종이 서식이 기준이고 화면 입력은 그 순서를 따른다.

render_paper(app, actor, m) → A4 한 장(<article class=sheet>)의 HTML. 호출하는 쪽이 class=paper 로 감싼다.
양식 정의의 layout: "corporate"(기업 결재 양식: 오른쪽 위 결재란) / "gov"(행정업무운영 편람 기안문: 두문·본문·결문,
결재 표시는 결문에). 비어 있으면 설정 paper_default. 모든 값은 escape(양식 정의 라벨도 신뢰하지 않음).
스크립트·외부 글꼴 없음(오프라인·CSP).
"""
from __future__ import annotations

import datetime as _dt
import html
import re

LAYOUTS = {"corporate": "기업 결재 양식(결재란 위)", "gov": "공문서 기안문(두문·본문·결문)"}
DATE2_KEYS = ("due", "pay_date", "need_by")  # 머리표 둘째 날짜 칸으로 올라가는 날짜 필드
GOV_KEYS = ("recv", "via", "sender", "contact")  # gov: 두문·결문으로 가는 필드
MAX_STAMP_COLS = 7  # 기안 + 6단계, 넘으면 다음 줄
WEEK = "월화수목금토일"


def esc(s):
    return html.escape("" if s is None else str(s), quote=True)


# ------------------------------------------------------------------ 도움 함수
def _spaced(name):
    base = re.sub(r"\s*\(.*?\)\s*", "", name or "").replace(" ", "") or (name or "")
    return " ".join(base) if len(base) <= 6 else base


def _mmdd(iso):
    return iso[5:10].replace("-", "/") if iso and len(iso) >= 10 else ""


def _wd(iso):
    try:
        return f"{iso} ({WEEK[_dt.date.fromisoformat(iso).weekday()]})"
    except (TypeError, ValueError):
        return str(iso or "")


def _num(x):
    if x is None or x == "":
        return ""
    return f"{x:,}" if isinstance(x, (int, float)) and not isinstance(x, bool) else str(x)


def layout_for(app, fd):
    lay = (fd or {}).get("layout") or ""
    if lay not in LAYOUTS:
        try:
            lay = app.paper_settings()["paper_default"]
        except Exception:  # noqa: BLE001 — 출력은 설정 오류로 막히지 않음
            lay = "corporate"
    return lay if lay in LAYOUTS else "corporate"


def date2_field(fd):
    for f in (fd or {}).get("fields", []):
        if f["type"] == "date" and f["key"] in DATE2_KEYS:
            return f
    return None


def input_order(fd, layout):
    """기안 화면 입력 순서(종이 순서): (제목 앞 필드키, 제목 뒤 필드키, 본문 뒤 필드키)."""
    keys = [f["key"] for f in fd["fields"]]
    if layout == "gov":
        before = [k for k in ("recv", "via") if k in keys]
        after_body = [k for k in ("sender", "contact") if k in keys]
        mid = [k for k in keys if k not in before and k not in after_body]
        return before, mid, after_body
    d2 = date2_field(fd)
    mid = ([d2["key"]] if d2 else []) + [k for k in keys if not d2 or k != d2["key"]]
    return [], mid, []


class _Ctx:
    def __init__(self, app, actor, m):
        self.app, self.m = app, m
        self.fd = app.form_for_doc(actor, m["id"]) if m.get("form") else None
        try:
            ps = app.paper_settings()
        except Exception:  # noqa: BLE001
            ps = {"company_name": "", "doc_mark": "", "paper_default": "corporate"}
        self.company = ps.get("company_name") or ""
        self.mark = ps.get("doc_mark") or ""
        self.layout = layout_for(app, self.fd)
        try:
            self.depts = {d["code"]: d["name"] for d in app.list_depts(True)}
        except Exception:  # noqa: BLE001
            self.depts = {}
        try:
            self.pos = {k: v.get("name") for k, v in app.positions(True).items()}
        except Exception:  # noqa: BLE001
            self.pos = {}
        self._u = {}
        self.vals = m.get("fields") or {}

    def user(self, u):
        if u not in self._u:
            try:
                self._u[u] = self.app.get_user(u)
            except Exception:  # noqa: BLE001 — 지워진 사용자도 출력은 됨
                self._u[u] = {"username": u, "name": u}
        return self._u[u]

    def pos_name(self, code):
        return self.pos.get(code) or ""


# ------------------------------------------------------------------ 결재 표시(공통 자료)
def _marks(cx):
    """[(머리글, 가운데칸 html, 아랫줄 글, 사람 dict)] — 기안자 + 결재선."""
    m = cx.m
    line = m.get("line") or []
    dr = cx.user(m["drafter"])
    sub = m.get("submitted")
    out = [{"role": "기안자", "head": "기안", "pos": cx.pos_name(dr.get("position")) or dr.get("title") or "",
            "name": m.get("drafter_name") or dr.get("name"),
            "mid": f"<span class=seal>{esc(m.get('drafter_name'))}</span>" if sub else
                   f"<span class=nm>{esc(m.get('drafter_name'))}</span>",
            "bot": _mmdd(sub) if sub else "기안중", "at": sub}]
    approved = [i for i, s in enumerate(line) if s.get("status") == "approved"]
    jeon = None
    if approved and any(s.get("status") == "skipped" for s in line[approved[-1] + 1:]):
        jeon = approved[-1]
    for i, s in enumerate(line):
        st = s.get("status")
        head = cx.pos_name(s.get("position")) or s.get("title") or s.get("name") or "결재"
        name = s.get("name") or s.get("user")
        if st == "approved":
            who = cx.user(s["acted_by"])["name"] if s.get("proxy_for") and s.get("acted_by") else name
            lab = "전결" if i == jeon else ("대결" if s.get("proxy_for") else "")
            mid = f"<span class=seal>{esc(who)}</span>" + (f"<span class=jl>{lab}</span>" if lab else "")
            bot = _mmdd(s.get("at"))
        elif st == "rejected":
            mid, bot = f"<span class='seal rej'>{esc(name)}</span>", "반려"
        elif st == "skipped":
            mid, bot = "<span class=skip>생략</span>", "-"
        else:
            mid, bot = f"<span class=nm>{esc(name)}</span>", "결재중" if st == "pending" else "대기"
        out.append({"role": "결재", "head": head, "pos": cx.pos_name(s.get("position")) or s.get("title") or "",
                    "name": name, "mid": mid, "bot": bot, "st": st, "at": s.get("at"), "jeon": i == jeon, "idx": i})
    return out, jeon


def _stamp(cx):
    marks, _ = _marks(cx)
    out = ""
    for k in range(0, len(marks), MAX_STAMP_COLS):
        part = marks[k:k + MAX_STAMP_COLS]
        head = "".join(f"<th scope=col>{esc(x['head'])}</th>" for x in part)
        mid = "".join(f"<td class='mid{' sk' if x.get('st') == 'skipped' else ''}'>{x['mid']}</td>" for x in part)
        bot = "".join(f"<td class=bot>{esc(x['bot'])}</td>" for x in part)
        out += (f"<table class=stamp><caption class=sr>결재란</caption><tr><th rowspan=3 class=vert scope=row>결재</th>"
                f"{head}</tr><tr>{mid}</tr><tr>{bot}</tr></table>")
    return f"<div class=stamps>{out}</div>"


# ------------------------------------------------------------------ 양식 필드 블록
def _items(f, v):
    cols = f["columns"]
    head = "<th scope=col class=no>No</th>" + "".join(f"<th scope=col>{esc(c['label'])}</th>" for c in cols)
    rows = (v or {}).get("rows") or []
    body = ""
    for i, r in enumerate(rows, 1):
        body += f"<tr><td class=no>{i}</td>" + "".join(
            f"<td class=n>{esc(_num(r.get(c['key'])))}</td>" if c["type"] != "text" else f"<td>{esc(r.get(c['key']))}</td>"
            for c in cols) + "</tr>"
    body += "<tr class=fill>" + "<td>&nbsp;</td>" * (len(cols) + 1) + "</tr>"
    foot = ""
    if f.get("sum"):
        sums = (v or {}).get("sum") or {}
        span = 2 if cols[0]["key"] not in f["sum"] else 1
        rest = cols[1:] if span == 2 else cols
        foot = (f"<tr class=total><th colspan={span} scope=row>합 계</th>" + "".join(
            f"<td class=n>{esc(_num(sums.get(c['key'])) or '-')}</td>" if c["key"] in f["sum"] else "<td></td>"
            for c in rest) + "</tr>")
    return (f"<table class='grid items'><caption>{esc(f['label'])}</caption><thead><tr>{head}</tr></thead>"
            f"<tbody>{body}{foot}</tbody></table>")


def _select(f, v):
    return " ".join(f"<span class=opt>{'■' if o == v else '□'}{esc(o)}</span>" for o in f["options"])


def _range(f, v):
    if not v:
        return "-", ""
    days = v.get("days")
    d = f"<b>{esc(format(days, 'g'))}일</b>" if days is not None else ""
    if d and f.get("workdays"):
        d += "<br><small>주말 제외</small>"
    return f"{esc(_wd(v.get('from')))} ~ {esc(_wd(v.get('to')))}", d


def _field_block(cx, exclude=(), compact=False):
    """양식 필드를 종이 순서대로: 짧은 칸은 두 쌍씩, 선택·기간·여러 줄은 한 줄, 표는 따로."""
    fd, vals, m = cx.fd, cx.vals, cx.m
    if not fd:
        return ""
    from . import formdef as fdm
    segs, rows, pend = [], [], []

    def flush_pair():
        if pend:
            cells = "".join(f"<th scope=row>{esc(a)}</th><td>{b}</td>" for a, b in pend)
            if len(pend) == 1:
                cells = f"<th scope=row>{esc(pend[0][0])}</th><td colspan=3>{pend[0][1]}</td>"
            rows.append(f"<tr>{cells}</tr>")
            pend.clear()

    def flush_rows():
        flush_pair()
        if rows:
            segs.append(f"<table class='grid fields{' compact' if compact else ''}'><colgroup><col class=lab><col>"
                        f"<col class=lab><col></colgroup>{''.join(rows)}</table>")
            rows.clear()

    for f in fd["fields"]:
        k, t, v = f["key"], f["type"], vals.get(f["key"])
        if k in exclude or t == "file":
            continue
        if t == "table":
            flush_rows()
            segs.append(_items(f, v))  # 금액 상자·한글 금액은 안 붙임 — 기안자가 적음
            continue
        if t == "select":
            flush_pair()
            rows.append(f"<tr><th scope=row>{esc(f['label'])}</th><td colspan=3 class=opts>{_select(f, v)}</td></tr>")
        elif t == "daterange":
            flush_pair()
            a, d = _range(f, v)
            rows.append(f"<tr><th scope=row>{esc(f['label'])}</th><td colspan=2>{a}</td><td class=days>{d}</td></tr>")
        elif t == "textarea":
            flush_pair()
            rows.append(f"<tr><th scope=row>{esc(f['label'])}</th><td colspan=3 class=pre>{esc(v or '-')}</td></tr>")
        else:
            pend.append((f["label"], esc(fdm.display(f, v))))
            if len(pend) == 2:
                flush_pair()
    flush_rows()
    return "".join(segs)


def _atts_line(m):
    return "  ".join(f"{i}. {a['name']}" for i, a in enumerate(m.get("attachments") or [], 1)) or "-"


def _footer(cx):
    m = cx.m
    line = m.get("line") or []
    st = m.get("status")
    if st == "done":
        s = f"결재 완료 {(m.get('completed') or '')[:10]}"
    elif st == "pending":
        s = f"결재 진행 중 ({sum(1 for x in line if x.get('status') == 'approved')}/{len(line)})"
    else:
        s = {"draft": "기안 중(상신 전)", "rejected": "반려", "withdrawn": "회수"}.get(st, str(st or ""))
    pre = f"{cx.company} 전자결재 · " if cx.company else ""
    return f"<footer class=pfoot><span>{esc(pre + s)}</span><span>1 / 1</span></footer>"


def _cc_names(cx):
    return ", ".join(cx.user(x).get("name") or x for x in (cx.m.get("cc") or [])) or "-"


# ------------------------------------------------------------------ 기업 양식(corporate)
def _corporate(cx):
    m, fd = cx.m, cx.fd
    t = m.get("doc_type") or "general"
    from . import policy as pol
    if fd:
        title = _spaced(fd["name"])
        sub = f"양식: {fd['name']} v{fd.get('version', '')} · {fd.get('category', '')}"
        ret = (m.get("form") or {}).get("retention") or fd.get("retention") or "5년"
    else:
        title, sub = "기 안 문", f"양식: 자유 기안 · {pol.DOC_TYPES.get(t, t)}"
        ret = {"general": "5년", "expense": "5년", "leave": "3년"}.get(t, "5년")
    dr = cx.user(m["drafter"])
    who = m.get("drafter_name") or dr.get("name") or m["drafter"]
    pos = cx.pos_name(dr.get("position")) or dr.get("title") or ""  # 표시 = "직위 이름"(사용자 결정)
    d2 = date2_field(fd)
    if d2:
        lab2, val2 = d2["label"], cx.vals.get(d2["key"]) or "-"
    else:
        lab2, val2 = "완료일", (m.get("completed") or "-")[:10]
    info = (f"<table class='grid info'><colgroup><col class=lab><col><col class=lab><col></colgroup>"
            f"<tr><th scope=row>문서번호</th><td>{esc(m.get('number') or '(완료 시 부여)')}</td>"
            f"<th scope=row>보존기간</th><td>{esc(ret)}</td></tr>"
            f"<tr><th scope=row>기안부서</th><td>{esc(cx.depts.get(m.get('dept'), m.get('dept')))}</td>"
            f"<th scope=row>기안자</th><td>{esc((pos + ' ' + who).strip())}</td></tr>"
            f"<tr><th scope=row>기안일자</th><td>{esc((m.get('submitted') or m.get('created') or '')[:10])}</td>"
            f"<th scope=row>{esc(lab2)}</th><td>{esc(val2)}</td></tr>"
            f"<tr><th scope=row>참조</th><td colspan=3>{esc(_cc_names(cx))}</td></tr>"
            + (f"<tr><th scope=row>원문</th><td colspan=3>{esc(m['source']['number'])}</td></tr>" if m.get("source") else "")
            + f"<tr><th scope=row>제목</th><td colspan=3 class=subj>{esc(m.get('title'))}</td></tr></table>")
    fields = _field_block(cx, exclude=(d2["key"],) if d2 else ())
    main = doc_html_block(m) or f"{fields}<div class=pbody>{esc(m.get('body') or '')}</div>"
    mark = f"<p class='dmark r'>{esc(cx.mark)}</p>" if cx.mark else ""  # 문서 표시: 결재란 위 오른쪽
    return (f"{mark}<header class=phead><div class=ttl><h1>{esc(title)}</h1><p class=sub>{esc(sub)}</p></div>{_stamp(cx)}"
            f"</header>{info}{main}"
            f"<table class='grid atts'><colgroup><col class=lab><col></colgroup><tr><th scope=row>붙임</th>"
            f"<td>{esc(_atts_line(m))}</td></tr></table>{_footer(cx)}")


# ------------------------------------------------------------------ 공문서 기안문(gov)
def _gyeolmun(cx):
    marks, jeon = _marks(cx)
    m = cx.m
    steps = marks[1:]
    live = [x for x in steps if x.get("st") != "skipped"]
    final = next((x for x in steps if x.get("jeon")), None) or (live[-1] if live else None)
    boxes = []
    for x in [marks[0]] + live:
        role = "기안자" if x is marks[0] else ("결재권자" if x is final else "검토자")
        if x is final and x.get("jeon"):
            role = "결재권자(전결)"
        date = _mmdd(m.get("completed")) if x is final and m.get("status") == "done" else x["bot"]
        boxes.append(f"<td><div class=role>{esc(role)}</div><div class=pos>{esc(x['pos'] or '-')}</div>"
                     f"<div class=mid>{x['mid']}</div><div class=bot>{esc(date)}</div></td>")
    sk = [x for x in steps if x.get("st") == "skipped"]
    skn = f"<p class=sml>생략: {esc(', '.join(x['name'] for x in sk))}</p>" if sk else ""
    return f"<table class=gmarks><caption class=sr>결재</caption><tr>{''.join(boxes)}</tr></table>{skn}"


def _gov(cx):
    m, fd, vals = cx.m, cx.fd, cx.vals
    keys = {f["key"] for f in (fd or {}).get("fields", [])}
    org = cx.company or cx.depts.get(m.get("dept"), m.get("dept") or "")
    recv = vals.get("recv") if "recv" in keys else None
    recv = recv or ("내부결재" if "recv" not in keys else "-")
    via = vals.get("via") if "via" in keys else ""
    fields = _field_block(cx, exclude=GOV_KEYS, compact=True)
    atts = m.get("attachments") or []
    if atts:
        end = "붙임  " + "  ".join(f"{i}. {a['name']} 1부." for i, a in enumerate(atts, 1)) + "  끝."
    else:
        end = "끝."
    sender = ""
    if "sender" in keys:
        sender = (f"<div class=sender><span>{esc(vals.get('sender') or '')}</span>"
                  f"<span class=jik>직인</span></div>")
    num = m.get("number") or "(완료 시 부여)"
    dt = (m.get("completed") or "")[:10]
    contact = vals.get("contact") if "contact" in keys else None
    du = cx.user(m["drafter"])
    dr = " ".join(x for x in (cx.pos_name(du.get("position")) or du.get("title") or "",
                              m.get("drafter_name") or du.get("name") or m.get("drafter")) if x)  # "직위 이름"
    cline = (f"담당자·연락처  {contact}" if contact else
             f"담당  {cx.depts.get(m.get('dept'), m.get('dept') or '')} {dr}")
    mark = f"<p class=dmark>{esc(cx.mark)}</p>" if cx.mark else ""  # 문서 표시: 기관명 위 왼쪽
    return (f"{mark}<header class=ghead><h1>{esc(_spaced(org) if len(org) <= 6 else org)}</h1></header>"
            f"<div class=grule></div>"
            f"<table class=gtop><tr><th scope=row>수신</th><td>{esc(recv)}</td></tr>"
            f"<tr><th scope=row>(경유)</th><td>{esc(via or '')}</td></tr>"
            f"<tr><th scope=row>제목</th><td class=subj>{esc(m.get('title'))}</td></tr></table>"
            f"<div class=gbody>{doc_html_block(m) or fields + '<div class=pbody>' + esc(m.get('body') or '') + '</div>'}"
            f"<p class=gend>{esc(end)}</p></div>{sender}<div class=grule></div>"
            f"{_gyeolmun(cx)}"
            f"<table class=gfoot><tr><th scope=row>협조자</th><td colspan=3>{esc(_cc_names(cx))}</td></tr>"
            f"<tr><th scope=row>시행</th><td>{esc(num)}{' (' + esc(dt) + ')' if dt else ''}</td>"
            f"<th scope=row>접수</th><td>(&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;)</td></tr>"
            f"<tr><td colspan=4>{esc(cline)}</td></tr>"
            + (f"<tr><th scope=row>원문</th><td colspan=3>{esc(m['source']['number'])}</td></tr>" if m.get("source") else "")
            + f"</table>{_footer(cx)}")


def doc_html_block(m):
    """편집기 문서 본문(서식 칸 포함) — 출력 때도 다시 정화."""
    if not m.get("doc_html"):
        return ""
    from .sanitize import clean_html
    return f"<div class='pbody dochtml'>{clean_html(m['doc_html'])}</div>"


def render_paper(app, actor, m, layout=None) -> str:
    """문서 meta(app.show 결과) → A4 한 장 HTML. layout 을 주면 양식·설정 대신 그 서식으로."""
    cx = _Ctx(app, actor, m)
    if layout in LAYOUTS:
        cx.layout = layout
    inner = _gov(cx) if cx.layout == "gov" else _corporate(cx)
    return f"<article class='sheet {cx.layout}' aria-label='출력 문서'>{inner}</article>"


# ------------------------------------------------------------------ CSS (.paper 아래로 한정)
_SANS = '"Malgun Gothic","Apple SD Gothic Neo","Noto Sans KR",sans-serif'
_SERIF = '"Noto Serif KR","HY견명조","Batang",serif'
PAPER_CSS = (
    ".paper{color:#000}.paper .sheet{width:794px;min-height:1123px;margin:0 auto;padding:56px 60px 48px;background:#fff;"
    "color:#000;display:flex;flex-direction:column;font:13px/1.5 " + _SANS + ";text-align:left}"
    ".paper .sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}"
    ".paper table{width:100%;border-collapse:collapse;margin:0}"
    ".paper th,.paper td{color:#000;font-size:13px;vertical-align:middle;padding:4px 6px;border-bottom:0}"
    ".paper .phead{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;margin-bottom:14px}"
    ".paper .ttl h1{font:700 30px/1.3 " + _SERIF + ";margin:6px 0 4px;letter-spacing:.08em;color:#000}"
    ".paper .sub{font-size:11px;margin:0}"
    ".paper .stamps{display:flex;flex-direction:column;align-items:flex-end;gap:4px}"
    ".paper table.stamp{width:auto;border:1.5px solid #000}"
    ".paper table.stamp th,.paper table.stamp td{border:1px solid #000;text-align:center;min-width:62px;padding:2px 3px;"
    "font-size:11px;background:#fff}"
    ".paper table.stamp th.vert{writing-mode:vertical-rl;min-width:0;width:22px;letter-spacing:.4em;background:#f2f2f2;"
    "font-weight:700;padding:4px 2px}"
    ".paper td.mid,.paper .gmarks .mid{height:58px;position:relative}"
    ".paper .seal{display:inline-flex;align-items:center;justify-content:center;width:44px;height:44px;border:2px solid #c62828;"
    "border-radius:50%;color:#c62828;font-weight:700;font-size:12px;line-height:1.1;word-break:keep-all;overflow:hidden}"
    ".paper .seal.rej{border-style:double;border-width:4px}"
    ".paper .jl{display:block;font-size:11px;font-weight:700;margin-top:1px}"
    ".paper .nm{font-size:11px}"
    ".paper .dmark{margin:0 0 2px;color:#c62828;font-weight:700;font-size:12px;line-height:1.3}.paper .dmark.r{text-align:right}"
    ".paper td.sk,.paper .gmarks td.sk{background:linear-gradient(to top right,#fff calc(50% - 1px),#000 50%,#fff calc(50% + 1px))}"
    ".paper .skip{font-size:11px;background:#fff;padding:0 2px}"
    ".paper table.grid{border:1.5px solid #000;margin-top:-1.5px}"
    ".paper table.grid th,.paper table.grid td{border:1px solid #000}"
    ".paper table.grid th{background:#f2f2f2;font-weight:700;text-align:left;white-space:nowrap}"
    ".paper col.lab{width:92px}"
    ".paper table.info{margin-top:0}.paper td.subj{font-size:15px;font-weight:700}"
    ".paper table.fields td.opts .opt{margin-right:12px;white-space:nowrap}"
    ".paper td.days{text-align:center;width:90px}.paper td.days small,.paper .sml{font-size:11px}"
    ".paper td.pre,.paper .pbody{white-space:pre-wrap;word-break:break-word}"
    ".paper table.items{margin-top:10px}.paper table.items caption{text-align:left;font-weight:700;padding:2px 0;caption-side:top}"
    ".paper table.items th{text-align:center}.paper td.n{text-align:right;font-variant-numeric:tabular-nums}"
    ".paper td.no,.paper th.no{width:36px;text-align:center}.paper tr.total th{text-align:center;letter-spacing:.3em}"
    ".paper tr.total td{font-weight:700}"
    ".paper .pbody{flex:1 0 auto;border:1.5px solid #000;margin-top:10px;padding:12px 14px;font-size:13.5px;line-height:1.85;"
    "min-height:120px}"
    ".paper table.atts{margin-top:-1.5px}"
    ".paper .pfoot{display:flex;justify-content:space-between;border-top:1px solid #000;margin-top:14px;padding-top:4px;"
    "font-size:11px}"
    ".paper .ghead h1{text-align:center;font:700 28px/1.4 " + _SERIF + ";letter-spacing:.15em;margin:0 0 8px;color:#000}"
    ".paper .grule{border-top:3px solid #000;margin:6px 0}"
    ".paper table.gtop th{width:64px;text-align:left;font-weight:700}.paper table.gtop td,.paper table.gtop th{padding:3px 4px}"
    ".paper .dochtml{white-space:normal}.paper .dochtml table{border:1.5px solid #000;margin:6px 0}"
    ".paper .dochtml th,.paper .dochtml td{border:1px solid #000;padding:3px 6px}.paper .dochtml th{background:#f2f2f2}"
    ".paper .dochtml p{margin:0 0 4px}"
    ".paper .gbody{flex:1 0 auto;display:flex;flex-direction:column;margin-top:6px}"
    ".paper .gbody .pbody{flex:0 0 auto;border:0;padding:6px 0;margin-top:6px;min-height:0}"
    ".paper table.compact th,.paper table.compact td{padding:2px 5px;font-size:12px}"
    ".paper .gend{margin:6px 0}"
    ".paper .sender{display:flex;justify-content:center;align-items:center;gap:14px;margin:18px 0 10px;"
    "font:700 22px/1.4 " + _SERIF + ";letter-spacing:.1em}"
    ".paper .jik{display:inline-flex;align-items:center;justify-content:center;width:58px;height:58px;border:1.5px dashed #000;"
    "font:400 12px/1 " + _SANS + ";letter-spacing:0}"
    ".paper table.gmarks{width:auto}.paper table.gmarks td{border:1px solid #000;text-align:center;min-width:86px;padding:2px 4px}"
    ".paper .gmarks .role{font-size:11px;font-weight:700}.paper .gmarks .pos,.paper .gmarks .bot{font-size:11px}"
    ".paper .gmarks .mid{height:52px;display:flex;align-items:center;justify-content:center;flex-direction:column}"
    ".paper table.gfoot{margin-top:6px}.paper table.gfoot th{width:64px;text-align:left;font-weight:700}"
    ".paper table.gfoot td,.paper table.gfoot th{padding:2px 4px;font-size:12px}"
    ".paperwrap{overflow-x:auto;background:#d9d9d9;padding:16px 8px}.paperwrap .sheet{box-shadow:0 2px 8px rgba(0,0,0,.35)}"
)

# 인쇄 페이지(/paper.css) 전용: 화면은 회색 바탕 가운데 한 장, 인쇄는 @page 여백만.
PAGE_CSS = (
    "*{box-sizing:border-box}html,body{margin:0}body.paperpage{background:#d9d9d9;padding:16px 8px;font-family:" + _SANS + "}"
    ".pbar{max-width:794px;margin:0 auto 10px;display:flex;justify-content:space-between;font-size:14px;color:#000}"
    ".pbar a{color:#000}.paperpage .sheet{box-shadow:0 2px 8px rgba(0,0,0,.35)}"
    "@page{size:A4;margin:30mm 15mm 15mm 20mm}"
    "@media print{body.paperpage{background:#fff;padding:0}.noprint{display:none!important}"
    ".paper .sheet{width:auto;min-height:252mm;padding:0;margin:0;box-shadow:none!important}"
    ".paper .seal,.paper .grule,.paper th{-webkit-print-color-adjust:exact;print-color-adjust:exact}}"
) + PAPER_CSS
