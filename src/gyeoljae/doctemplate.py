"""문서 편집기(웹 워드프로세서) 서식 HTML ↔ 양식 값.

- default_template(fd): 양식 필드로 기본 서식 HTML(값 칸 = data-field, 표 행 = data-row, 합계 칸 = data-sum).
- extract_fields(fd, html): 편집기 HTML → 양식 값(normalize 전 원시값). 금액(계산) 열·합계 칸도 기안자가 적은 그대로
  읽음(검색·목록·종이 렌더용). 서버는 계산·검증하지 않음.
- fill(fd, html, vals): 편집기 첫 내용(initial_html)에서만 — 서식에 기존 값을 미리 채움. 저장 문서에는 쓰지 않음
  (doc.html 은 기안자가 쓴 그대로, 정화만).
- html_text(html): 검색용 평문.
입력 HTML 은 항상 sanitize.clean_html 을 먼저 거친다(그래서 여기의 트리는 짝이 맞음).
"""
from __future__ import annotations

import html as _h
import re
from html.parser import HTMLParser

from .sanitize import VOID, clean_html

MAX_TEMPLATE = 512 * 1024
BLOCK = {"p", "div", "li", "tr", "h1", "h2", "h3", "h4", "table", "blockquote", "pre", "ul", "ol", "caption"}
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


class _N:
    __slots__ = ("tag", "attrs", "kids")

    def __init__(self, tag, attrs=None):
        self.tag, self.attrs, self.kids = tag, dict(attrs or {}), []


class _Tree(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _N(None)
        self.st = [self.root]

    def handle_starttag(self, tag, attrs):
        n = _N(tag, [(k, v or "") for k, v in attrs])
        self.st[-1].kids.append(n)
        if tag not in VOID:
            self.st.append(n)

    def handle_startendtag(self, tag, attrs):
        self.st[-1].kids.append(_N(tag, [(k, v or "") for k, v in attrs]))

    def handle_endtag(self, tag):
        for i in range(len(self.st) - 1, 0, -1):
            if self.st[i].tag == tag:
                del self.st[i:]
                break

    def handle_data(self, d):
        self.st[-1].kids.append(d)


def _parse(s):
    p = _Tree()
    p.feed(clean_html(s))
    p.close()
    return p.root


def _ser(n):
    if isinstance(n, str):
        return _h.escape(n, quote=False)
    inner = "".join(_ser(k) for k in n.kids)
    if n.tag is None:
        return inner
    a = "".join(f' {k}="{_h.escape(v, quote=True)}"' for k, v in n.attrs.items())
    return f"<{n.tag}{a}>" if n.tag in VOID else f"<{n.tag}{a}>{inner}</{n.tag}>"


def _text(n, cell_sep=" "):
    if isinstance(n, str):
        return n
    if n.tag == "br":
        return "\n"
    s = "".join(_text(k, cell_sep) for k in n.kids)
    if n.tag in ("td", "th"):
        return s + cell_sep
    return ("\n" + s + "\n") if n.tag in BLOCK else s


def _walk(n, fn, row=None):
    """fn(node, row_node) — data-row 안의 칸은 row_node 와 함께."""
    for k in n.kids:
        if isinstance(k, _N):
            fn(k, row)
            _walk(k, fn, k if "data-row" in k.attrs else row)


def _val(n):
    t = _text(n)
    return "\n".join(ln.strip() for ln in t.strip().splitlines()).strip()


# ------------------------------------------------------------------ 기본 서식
def default_template(fd):
    e = lambda s: _h.escape(str(s), quote=True)  # noqa: E731
    rows, tables = [], []
    for f in fd["fields"]:
        if f["type"] == "file":
            continue
        if f["type"] == "table":
            cols = f["columns"]
            head = "".join(f"<th>{e(c['label'])}</th>" for c in cols)
            body = "".join(f'<tr data-row="{e(f["key"])}">'
                           + "".join(f'<td data-field="{e(c["key"])}"></td>' for c in cols) + "</tr>"
                           for _ in range(f.get("rows") or 3))
            foot = ""
            if f.get("sum"):
                foot = '<tr class="sum">' + "".join(
                    f'<td data-sum="{e(f["key"])}.{e(c["key"])}"></td>' if c["key"] in f["sum"]
                    else f"<th>{'합계' if j == 0 else ''}</th>" for j, c in enumerate(cols)) + "</tr>"
            tables.append(f'<p><b>{e(f["label"])}</b></p><table class="items"><thead><tr>{head}</tr></thead>'
                          f"<tbody>{body}{foot}</tbody></table>")
        else:
            rows.append(f'<tr><th>{e(f["label"])}</th><td data-field="{e(f["key"])}"></td></tr>')
    out = (f'<table class="fields"><tbody>{"".join(rows)}</tbody></table>' if rows else "") + "".join(tables)
    hint = [ln for ln in (fd.get("body_hint") or "").split("\n")] or [""]
    return out + '<div class="docbody">' + "".join(f"<p>{e(x) or '<br>'}</p>" for x in hint) + "</div>"


def template_for(fd):
    return (fd or {}).get("template_html") or (default_template(fd) if fd else '<div class="docbody"><p><br></p></div>')


def check_template(s):
    s = str(s or "")
    if len(s.encode("utf-8")) > MAX_TEMPLATE:
        raise ValueError("서식 HTML 이 너무 큽니다(최대 512KB)")
    return clean_html(s)


# ------------------------------------------------------------------ 추출·채우기
def extract_fields(fd, html):
    """편집기 HTML → 양식 원시값(formdef.normalize 입력). 금액 열·합계 칸(data-sum)도 적힌 그대로."""
    by = {f["key"]: f for f in fd["fields"]}
    vals, rows, sums = {}, {}, {}

    def fn(n, row):
        if "data-sum" in n.attrs:
            a, _, b = n.attrs["data-sum"].partition(".")
            if a in by and by[a]["type"] == "table" and b not in sums.setdefault(a, {}):
                sums[a][b] = " ".join(_val(n).split())
            return
        if "data-row" in n.attrs and n.attrs["data-row"] in by and by[n.attrs["data-row"]]["type"] == "table":
            rows.setdefault(n.attrs["data-row"], []).append(n)
            return
        k = n.attrs.get("data-field")
        if row is None and k in by and by[k]["type"] not in ("table", "file") and k not in vals:
            v = _val(n)
            t = by[k]["type"]
            if t == "daterange":
                ds = DATE_RE.findall(v)
                v = {"from": ds[0] if ds else "", "to": ds[1] if len(ds) > 1 else (ds[0] if ds else "")}
            elif t in ("text", "select", "date", "number", "money"):
                v = " ".join(v.split())
            vals[k] = v
    _walk(_parse(html), fn)
    for k, ns in rows.items():
        cols = {c["key"]: c for c in by[k]["columns"]}
        out = []
        for rn in ns:
            r = {}

            def cell(n, _row, r=r):
                ck = n.attrs.get("data-field")
                if ck in cols and ck not in r:
                    r[ck] = " ".join(_val(n).split())
            _walk(rn, cell)
            if any(r.values()):
                out.append(r)
        vals[k] = out
    for k, sm in sums.items():
        sm = {b: x for b, x in sm.items() if x}
        if sm:
            vals[k] = {"rows": vals.get(k) or [], "sum": sm}
    return vals


def _show(f, v):
    if v in (None, ""):
        return ""
    if f["type"] == "daterange":
        return f"{v.get('from') or ''} ~ {v.get('to') or ''}" if isinstance(v, dict) else str(v)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return f"{v:,}"
    return str(v)


def _set(n, s):
    n.kids = []
    for i, ln in enumerate(str(s).split("\n")):
        if i:
            n.kids.append(_N("br"))
        n.kids.append(ln)


def fill(fd, html, vals):
    """값·합계(기안자가 적은 것)를 data-field/data-sum 칸에 채움 → (정화된) HTML. 편집기 첫 내용 전용."""
    root = _parse(html)
    if not fd:
        return _ser(root)
    by = {f["key"]: f for f in fd["fields"]}
    vals = vals or {}
    rows = {}

    def fn(n, row):
        k = n.attrs.get("data-field")
        if "data-row" in n.attrs and n.attrs["data-row"] in by:
            rows.setdefault(n.attrs["data-row"], []).append(n)
        elif "data-sum" in n.attrs:
            a, _, b = n.attrs["data-sum"].partition(".")
            v = vals.get(a) if a in by else None
            _set(n, _show({"type": "money"}, v.get("sum", {}).get(b) if isinstance(v, dict) else None))
        elif row is None and k in by and by[k]["type"] not in ("table", "file"):
            _set(n, _show(by[k], vals.get(k)))
    _walk(root, fn)
    for k, ns in rows.items():
        f = by[k]
        if f["type"] != "table":
            continue
        v = vals.get(k)
        data = v.get("rows") if isinstance(v, dict) else v  # 정규화 전 값(오류 후 재표시)도 받음
        data = [r for r in (data or []) if isinstance(r, dict)][:50]
        if len(data) > len(ns) and ns:  # 행이 모자라면 마지막 행 모양으로 늘림(CLI·무JS 수정)
            import copy
            par = _parent(root, ns[-1])
            for _ in range(len(data) - len(ns)):
                nn = copy.deepcopy(ns[-1])
                par.kids.insert(par.kids.index(ns[-1]) + 1, nn)
                ns.append(nn)
        cols = {c["key"]: c for c in f["columns"]}
        for i, rn in enumerate(ns):
            r = data[i] if i < len(data) else {}

            def cell(n, _row, r=r):
                ck = n.attrs.get("data-field")
                if ck in cols:
                    _set(n, _show(cols[ck], r.get(ck)))
            _walk(rn, cell)
    return _ser(root)


def _parent(root, target):
    st = [root]
    while st:
        n = st.pop()
        for k in n.kids:
            if k is target:
                return n
            if isinstance(k, _N):
                st.append(k)
    return root


def html_text(html):
    """검색·body.md 용 평문(블록마다 줄바꿈, 칸은 공백)."""
    t = _text(_parse(html))
    return "\n".join(ln.strip() for ln in t.splitlines() if ln.strip())


def initial_html(fd, fields, body):
    """편집기 첫 내용: 서식에 현재 값을 채우고, 본문 칸(class=docbody)에 기존 본문을 넣음."""
    root = _parse(fill(fd, template_for(fd), fields))
    if body:
        def fn(n, _row):
            if "docbody" in (n.attrs.get("class") or "").split():
                n.kids = [_N("p") for _ in body.split("\n")]
                for p, ln in zip(n.kids, body.split("\n")):
                    p.kids = [ln] if ln else [_N("br")]
        _walk(root, fn)
    return _ser(root)
