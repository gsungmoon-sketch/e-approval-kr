#!/usr/bin/env python3
# hanword_core.py — 한워드 문서 코어(브라우저 없이 재사용하는 문서 연산 계층).
#   server.py(웹 라우트)·hanword CLI·(예정) maru-ui 네이티브 프런트가 같은 함수를 부른다.
#   내부 표현 = 에디터 HTML(정본 저장형식). 구조 질의·검증은 문서모델(model()) — hwpxconv.P 파서 결과를 JSON화.
#   형식: in = html/hwpx/hwp/docx/txt/pdf, out = html/hwpx/docx/txt/pdf
import os, re, io, sys, json, base64, shutil, subprocess, tempfile, zipfile, difflib, html as htmlmod

APP = os.path.dirname(os.path.abspath(__file__))
if APP not in sys.path: sys.path.insert(0, APP)

import hwpxconv                      # HWPX/HWP 변환 + 문서모델 파서(P/_PP)
import docxconv                      # DOCX 변환
import docconv                       # 레거시 Word(.doc, OLE) 텍스트 가져오기(stdlib)
import hanword_model                 # 문서모델·렌더·편집 연산(stdlib)
from hwpxconv import HwpxInputError

InputError = HwpxInputError          # 사용자 입력 문제(손상/악성/미지원) — 서버는 폴백 없이 거부

FORMATS_IN = ("html", "hwpx", "hwp", "docx", "doc", "txt", "pdf")
FORMATS_OUT = ("html", "hwpx", "docx", "txt", "pdf")
EXT = {"htm": "html", "html": "html", "hwpx": "hwpx", "hwp": "hwp", "docx": "docx", "doc": "doc", "txt": "txt", "pdf": "pdf"}

# ───────────── 형식 판별 ─────────────
def stdio_safe():
    """Windows 콘솔(cp1252 등) 비 UTF-8 stdout/stderr: 못 쓰는 글자(→·한글)는 '?' 로 — UnicodeEncodeError 로 죽지 않게."""
    for s in (sys.stdout, sys.stderr):
        enc = (getattr(s, "encoding", None) or "").lower().replace("-", "").replace("_", "")
        if enc not in ("utf8", "utf8sig") and hasattr(s, "reconfigure"):
            try: s.reconfigure(errors="replace")
            except Exception: pass

def detect(data, name=""):
    """바이트(+파일명) → 형식. 내용 매직 우선, 확장자는 보조(txt/html 구분용)."""
    if data[:4] == b"PK\x03\x04":
        try: names = set(zipfile.ZipFile(io.BytesIO(data)).namelist())
        except Exception: return None
        if "word/document.xml" in names: return "docx"
        if any(n.startswith("Contents/") for n in names) or "mimetype" in names: return "hwpx"
        return None
    if data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return docconv.cfb_kind(data)                      # OLE/CFB: 루트에 WordDocument 스트림=.doc, 그 외=HWP5
    if data[:17] == b"HWP Document File": return "hwp"     # HWP 3.x(미지원이지만 형식은 식별)
    if data[:5] == b"%PDF-": return "pdf"
    if data[:5] == b"{\\rtf": return "rtf"                # RTF(흔히 .doc 확장자) — 미지원이지만 식별해 명확히 안내
    ext = (name or "").rsplit(".", 1)[-1].lower() if "." in (name or "") else ""
    if ext in ("html", "htm", "txt"): return EXT[ext]
    head = data[:2048].lstrip().lower()
    if head.startswith((b"<!doctype html", b"<html", b"<p", b"<div", b"<h1", b"<table")): return "html"
    return "txt"

# ───────────── TXT/PDF (server.py에서 이관) ─────────────
PDF_DPI = int(os.environ.get("WP_PDF_DPI", "120"))
PDF_MAXPAGES = 60

def read_txt_bytes(data):
    """바이트 → 문자열. UTF-8(BOM 포함) → CP949(한글 윈도우 메모장) → 대체 순."""
    for enc in ("utf-8-sig", "cp949"):
        try: return data.decode(enc)
        except UnicodeDecodeError: continue
    return data.decode("utf-8", "replace")

def text_to_html(pages):
    """평문(쪽 리스트) → 에디터 HTML. 빈 줄=빈 문단, 쪽 사이=쪽 나누기(.pgbreak)."""
    out = []
    for i, txt in enumerate(pages):
        if i: out.append('<p class="pgbreak"><br></p>')
        for line in txt.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            line = line.rstrip("\t ")
            out.append("<p>%s</p>" % (htmlmod.escape(line).replace("\t", "&nbsp;&nbsp;&nbsp;&nbsp;") if line else "<br>"))
    return "".join(out) or "<p><br></p>"

def _log(tag, msg):
    try: sys.stderr.write("[%s] %s\n" % (tag, str(msg)[:300])); sys.stderr.flush()
    except Exception: pass

def pdf_text(fp):
    """PDF → 쪽별 텍스트. (ok, pages, err). 사용자 노출 err는 안전한 안내문만."""
    if not shutil.which("pdftotext"):
        return False, [], "이 서버에서 PDF 글자 추출을 지원하지 않습니다."
    try:
        r = subprocess.run(["pdftotext", "-layout", "-enc", "UTF-8", "-l", str(PDF_MAXPAGES), fp, "-"],
                           capture_output=True, timeout=120)
    except Exception as e:
        _log("pdf_text", e); return False, [], "PDF를 읽지 못했습니다."
    if r.returncode != 0:
        _log("pdf_text", "rc=%d %s" % (r.returncode, r.stderr.decode("utf-8", "replace")))
        return False, [], "PDF를 읽지 못했습니다(손상되었거나 암호가 걸린 문서)."
    pages = r.stdout.decode("utf-8", "replace").split("\f")
    if pages and not pages[-1].strip(): pages.pop()
    if not "".join(pages).strip():
        return False, [], "글자가 없는 PDF(그림으로만 된 문서)입니다 — \"그대로\"로 여세요."
    return True, pages, ""

def pdf_render_pages(fp, dpi=PDF_DPI, maxpages=PDF_MAXPAGES):
    """PDF 각 쪽 → JPEG data URI 리스트. (ok, uris, err)."""
    if not shutil.which("pdftoppm"):
        return False, [], "이 서버에서 PDF 쪽 그림 렌더를 지원하지 않습니다."
    tmpd = tempfile.mkdtemp(prefix="wp-pdfimg-")
    try:
        r = subprocess.run(["pdftoppm", "-jpeg", "-jpegopt", "quality=82", "-r", str(dpi),
                            "-f", "1", "-l", str(maxpages), fp, os.path.join(tmpd, "p")],
                           capture_output=True, timeout=180)
        files = sorted(f for f in os.listdir(tmpd) if f.endswith(".jpg"))
        if not files:
            _log("pdf_render", r.stderr.decode("utf-8", "replace"))
            return False, [], "PDF를 그림으로 여는 데 실패했습니다."
        uris = []
        for fn in files:
            with open(os.path.join(tmpd, fn), "rb") as f:
                uris.append("data:image/jpeg;base64," + base64.b64encode(f.read()).decode())
        return True, uris, ""
    except Exception as e:
        _log("pdf_render", e); return False, [], "PDF를 그림으로 여는 데 실패했습니다."
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)

def pdf_pages_html(uris):
    out = []
    for i, u in enumerate(uris):
        if i: out.append('<p class="pgbreak"><br></p>')
        out.append('<p><img src="%s" style="width:100%%" alt="%d쪽"></p>' % (u, i + 1))
    return "".join(out) or "<p><br></p>"

def pdf_to_html(data, mode="text"):
    tf = tempfile.NamedTemporaryFile(prefix="wp-pdfin-", suffix=".pdf", delete=False)
    try:
        tf.write(data); tf.close()
        if mode == "image":
            ok, uris, err = pdf_render_pages(tf.name)
            if ok: return pdf_pages_html(uris)
        else:
            ok, pages, err = pdf_text(tf.name)
            if ok: return text_to_html(pages)
        raise InputError(err)
    finally:
        try: os.remove(tf.name)
        except OSError: pass

# ───────────── PDF 출력(Chromium/Playwright, htmlpdf.js) ─────────────
def html_to_pdf(html, node_path=None, htmlpdf=None, timeout=90):
    htmlpdf = htmlpdf or os.environ.get("WP_HTMLPDF", os.path.join(APP, "htmlpdf.js"))
    node_path = node_path if node_path is not None else os.environ.get("WP_NODE_PATH", "")
    env = dict(os.environ)
    if node_path: env["NODE_PATH"] = node_path
    data = html.encode("utf-8") if isinstance(html, str) else html
    p = subprocess.run(["node", htmlpdf], input=data, capture_output=True, timeout=timeout, env=env)
    if p.returncode == 0 and p.stdout[:4] == b"%PDF": return p.stdout
    raise RuntimeError(p.stderr.decode("utf-8", "replace")[-400:] or "PDF 변환 실패")

# ───────────── 가져오기 / 내보내기 ─────────────
def import_bytes(data, fmt=None, name="", pdf_mode="text"):
    """파일 바이트 → 에디터 HTML. 실패: InputError(사용자 입력 문제) 또는 기타 예외."""
    fmt = fmt or detect(data, name)
    if fmt in ("hwpx", "hwp"): return hwpxconv.hwpx_bytes_to_html(data)
    if fmt == "docx": return docxconv.docx_to_html(data)
    if fmt == "doc": return docconv.doc_to_html(data)       # 텍스트만(서식·표 구조·그림 제외)
    if fmt == "rtf": raise InputError("RTF 문서는 지원하지 않습니다 — Word에서 .docx로 저장 후 여세요")
    if fmt == "txt": return text_to_html(read_txt_bytes(data).split("\f"))
    if fmt == "html": return read_txt_bytes(data)
    if fmt == "pdf": return pdf_to_html(data, pdf_mode)
    raise InputError("지원하지 않는 형식")

def html_to_text(html):
    """에디터 HTML → 평문(문단=줄, 표=행마다 탭 구분, 쪽 나누기=\\f)."""
    _, notes, blocks = docxconv.parse_model(html)
    out = []
    for b in blocks:
        t = b.get("tag")
        if t == "pgbreak": out.append("\f"); continue
        if t == "table":
            for row in b.get("rows") or []:
                out.append("\t".join(_runs_text(hanword_model.cell_runs(c)).strip() for c in row))
            continue
        if t == "img": out.append("[그림]"); continue
        pre = "• " if b.get("list") == "bullet" else ("- " if b.get("list") else "")
        out.append(pre + _runs_text(b.get("runs")))
    s = "\n".join(out).replace("\n\f\n", "\f")
    if notes: s += "\n\n주석\n" + "\n".join("%s) %s" % (k, v) for k, v in notes.items())
    return s + "\n"

def export_html(html, fmt, page=None, **kw):
    """에디터 HTML → 형식 바이트. page=용지 dict(mm, docx·hwpx 반영)."""
    if fmt == "hwpx": return hwpxconv.html_to_hwpx(html, page)
    if fmt == "docx": return docxconv.html_to_docx(html, page)
    if fmt == "txt": return html_to_text(html).encode("utf-8")
    if fmt == "html": return html.encode("utf-8")
    if fmt == "pdf": return html_to_pdf(html, **kw)
    raise ValueError("지원하지 않는 출력 형식: %s" % fmt)

# ───────────── 문서모델 · 통계 · 검증 · 왕복 ─────────────
def _runs_text(runs):
    return "".join(r.get("text", "") for r in (runs or [])).replace("\xa0", " ")

def model(html):
    """에디터 HTML → 문서모델(hanword_model 형식, JSON 직렬화 가능). render(model)이 역함수."""
    return hanword_model.model(html)

render = hanword_model.render
apply_ops = hanword_model.apply
ModelError = hanword_model.ModelError

def stats(html, m=None):
    m = m or model(html)
    s = {"paragraphs": 0, "headings": 0, "list_items": 0, "tables": 0, "cells": 0, "images": 0,
         "links": 0, "endnotes": len(m["notes"]), "pgbreaks": 0, "chars": 0, "empty_paragraphs": 0, "filled_cells": 0}
    def runstat(runs):
        s["images"] += sum(1 for r in runs if "img" in r)
        prev = None
        for r in runs:
            l = r.get("link")
            if l and l != prev: s["links"] += 1
            prev = l
    for b in m["blocks"]:
        t = b["type"]
        if t == "table":
            s["tables"] += 1
            for row in b["rows"]:
                for c in row:
                    tx = _runs_text(hanword_model.cell_runs(c))
                    s["cells"] += 1; s["filled_cells"] += bool(tx.strip()); s["chars"] += len(tx.strip())
                    s["images"] += len(c.get("images") or [])
        elif t == "img": s["images"] += 1
        elif t == "pgbreak": s["pgbreaks"] += 1
        else:
            runs = b.get("runs") or []
            tx = _runs_text(runs)
            nonempty = bool(tx.strip()) or any("img" in r or "endnote" in r for r in runs)
            if t in ("h1", "h2", "h3"): s["headings"] += 1
            elif t == "li": s["list_items"] += 1
            elif nonempty: s["paragraphs"] += 1
            else: s["empty_paragraphs"] += 1
            s["chars"] += len(re.sub(r"\s", "", tx))
            runstat(runs)
    meta = m["meta"]
    s["has_header"] = bool(meta.get("header") or meta.get("header-odd"))
    s["has_footer"] = bool(meta.get("footer") or meta.get("pagenum"))
    return s

def plain_text(html, m=None):
    m = m or model(html); lines = []
    for b in m["blocks"]:
        if b["type"] == "table":
            for row in b["rows"]: lines.extend(_runs_text(hanword_model.cell_runs(c)).strip() for c in row)
        elif "runs" in b: lines.append(_runs_text(b["runs"]))
    return "\n".join(re.sub(r"\s+", " ", l).strip() for l in lines if l.strip())

def check_html(html):
    """에디터 HTML 정합 검사 → issues[{level,msg}]. 내보내기에서 손실될 요소·위험 요소 경고."""
    iss = []
    def add(level, msg): iss.append({"level": level, "msg": msg})
    if not re.sub(r"<[^<>]*>|\s|&nbsp;", "", html) and "<img" not in html and "<table" not in html:   # [^<>]=선형
        add("warn", "빈 문서")
    if re.search(r"<script\b|\son[a-z]+\s*=|javascript:", html, re.I): add("error", "스크립트/이벤트 핸들러/javascript: 포함(열 때 제거됨)")
    n_ext = len(re.findall(r'<img\b[^<>]*?\bsrc="(?!data:)', html))
    if n_ext: add("warn", "외부/상대경로 그림 %d개 — HWPX/DOCX 내보내기에서 빠짐" % n_ext)
    depth = 0; nested = False
    for mt in re.finditer(r"<(/?)table\b", html, re.I):   # 선형: 여닫는 표 태그 깊이 추적
        depth = max(0, depth - 1) if mt.group(1) else depth + 1
        if depth > 1: nested = True; break
    if nested: add("warn", "중첩 표 — 내보내기 시 안쪽 표는 글자로 평탄화")
    if "<svg" in html.lower(): add("info", "도형(SVG) — 웹 내려받기는 그림으로 변환, CLI 직접 변환은 빠짐")
    for m in re.finditer(r'href="([^"]*)"', html):
        if m.group(1) and not hwpxconv._safe_link_url(m.group(1)) and not m.group(1).startswith("#"):
            add("warn", "비허용 링크 스킴: %s" % m.group(1)[:60]); break
    for m in re.finditer(r'src="data:image/([a-z0-9.+-]+);base64,', html, re.I):
        if m.group(1).lower() not in ("png", "jpeg", "jpg", "gif", "bmp"):
            add("info", "그림 형식 %s — HWPX/DOCX에서 빠질 수 있음(png/jpeg 권장)" % m.group(1)); break
    try: model(html)
    except Exception as e: add("error", "문서모델 파싱 실패: %s" % e)
    return iss

def check(data, fmt=None, name=""):
    """파일 → {ok, format, issues, stats}. 가져오기 실패=error."""
    fmt = fmt or detect(data, name)
    res = {"format": fmt, "bytes": len(data), "issues": [], "stats": None}
    if fmt is None:
        res["issues"].append({"level": "error", "msg": "형식 판별 불가"}); res["ok"] = False; return res
    if fmt == "docx":
        try:
            zf = docxconv.guard_docx(data)
            rels = docxconv._DocxReader(zf).rels; names = set(zf.namelist())
            for rid, (typ, tgt, mode) in rels.items():
                if mode == "External": continue
                p = tgt.lstrip("/") if tgt.startswith("/") else "word/" + tgt
                if p not in names: res["issues"].append({"level": "warn", "msg": "관계 대상 없음: %s → %s" % (rid, tgt)})
        except Exception as e:
            res["issues"].append({"level": "error", "msg": "%s: %s" % (type(e).__name__, e)})
    try:
        html = import_bytes(data, fmt, name)
        res["issues"].extend(check_html(html)); res["stats"] = stats(html)
    except Exception as e:
        res["issues"].append({"level": "error", "msg": "가져오기 실패 — %s: %s" % (type(e).__name__, e)})
    res["ok"] = not any(i["level"] == "error" for i in res["issues"])
    return res

_CMP = ("paragraphs", "headings", "list_items", "tables", "filled_cells", "images", "links", "endnotes", "pgbreaks", "has_header", "has_footer")

def roundtrip(data, fmt=None, via=None, name=""):
    """가져오기→via 형식 내보내기→다시 가져오기. 통계·본문 유사도 비교 → {ok, via, before, after, diff, text_similarity}."""
    fmt = fmt or detect(data, name)
    via = via or ("docx" if fmt == "docx" else "hwpx")
    h1 = import_bytes(data, fmt, name)
    out = export_html(h1, via)
    h2 = import_bytes(out, via)
    m1, m2 = model(h1), model(h2)
    s1, s2 = stats(h1, m1), stats(h2, m2)
    t1, t2 = plain_text(h1, m1), plain_text(h2, m2)
    sim = difflib.SequenceMatcher(None, t1, t2, autojunk=False).ratio() if (t1 or t2) else 1.0
    diff = {k: [s1[k], s2[k]] for k in _CMP if s1.get(k) != s2.get(k)}
    dl = [l for l in difflib.unified_diff(t1.splitlines(), t2.splitlines(), lineterm="", n=0) if l[:1] in "+-" and l[:3] not in ("+++", "---")]
    return {"ok": sim >= 0.98 and not diff, "format": fmt, "via": via, "out_bytes": len(out),
            "text_similarity": round(sim, 4), "diff": diff, "text_diff": dl[:20], "before": s1, "after": s2}

def info(data, fmt=None, name=""):
    fmt = fmt or detect(data, name)
    r = {"format": fmt, "bytes": len(data)}
    if fmt == "docx":
        try: r["page_mm"] = docxconv.page_info(data)
        except Exception: pass
    html = import_bytes(data, fmt, name)
    m = model(html); r["stats"] = stats(html, m)
    r["meta"] = {k: (v if len(v) <= 200 else v[:200] + "…") for k, v in m["meta"].items()}
    heads = [_runs_text(b["runs"]) for b in m["blocks"] if b["type"] in ("h1", "h2", "h3")]
    r["outline"] = heads[:50]
    first = next((_runs_text(b["runs"]) for b in m["blocks"] if _runs_text(b.get("runs")).strip()), "")
    r["title_guess"] = (heads[0] if heads else first)[:80]
    return r

# ───────────── 원본 되씀(같은 형식 저장 — hanword_keep) ─────────────
import hanword_keep

def export_keep(orig_bytes, html_or_model, fmt, orig_sha256=None, page=None, keep=True):
    """원본 꾸러미를 바탕으로 저장 → (bytes, report). 바뀌지 않은 덩이 = 원본 XML 조각 그대로(K2, level "blocks"),
       불가하면 전체 다시 만들기 + 허용 목록 파트 복사(level "parts"), 그것도 안 되면 다시 만들기(level "none").
       html_or_model = 에디터 HTML 문자열 또는 문서모델 dict. keep=False(--no-keep) = 늘 다시 만들기. 기존 export_html은 그대로."""
    return hanword_keep.export_keep(orig_bytes, html_or_model, fmt, orig_sha256=orig_sha256, page=page, keep=keep,
                                    export=lambda h, f, p: export_html(h, f, page=p))

def keep_index(orig_bytes, fmt=None, name=""):
    """열 때 곁 기록용 덩이 색인 {sha256, size, format, blocks, groups[{part,start,end,blocks}]} — 조각 내용 없음."""
    fmt = fmt or detect(orig_bytes, name)
    if fmt not in ("hwpx", "docx"): raise InputError("덩이 색인은 hwpx/docx만")
    try: return hanword_keep.keep_index(orig_bytes, fmt)
    except InputError: raise
    except hanword_keep.KeepFail as e: raise InputError("덩이 색인 불가: %s" % e)
    except Exception as e: raise InputError("덩이 색인 불가(%s)" % type(e).__name__)   # MemoryError 등도 입력 오류로
