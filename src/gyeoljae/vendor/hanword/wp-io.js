// 한워드 — 입출력(HWPX 저장/열기, 그림 삽입, 머리말/꼬리말/쪽번호, PDF).
(function () {
  "use strict";
  var WP = window.WP, $ = WP.$, page = WP.page, docname = WP.docname, api = WP.api,
      status = WP.status, toastMsg = WP.toast, markDirty = WP.markDirty, clean = WP.clean,
      openModal = WP.openModal, closeModal = WP.closeModal, esc = WP.esc,
      saveRange = WP.saveRange, restoreRange = WP.restoreRange;
  function fail(msg) { return function (e) { var d = (e && e.message) ? (" — " + e.message) : ""; status(msg + (e && e.message ? (": " + e.message) : "")); toastMsg(msg + d); }; }   // api 실패 시 "…중…" 상태 복구 + 서버 메시지(마스킹됨) 표면화
  function failList(dl, msg) { return function () { dl.innerHTML = '<div class="di"><span class="meta">' + msg + '</span></div>'; }; }

  function pdf() {
    var name = (docname.value || "제목 없음").trim() || "제목 없음";
    if (WP.rasterPages) {   // #28 로컬 래스터 경로(기기: 클라 캡처→서버 magick 조립→documents/print/). 공개모드는 dlPdf라 여기 안 옴
      status("PDF 변환 중…"); toastMsg("PDF 변환 중…");
      WP.rasterPages({ onProgress: WP.printProgress && WP.printProgress("PDF") }).then(function (pages) {
        status("PDF 조립 중…");
        return api("/api/export/pdf", { name: name, pages: pages });
      }).then(function (r) {
        if (r && r.ok) { status("PDF 저장됨"); toastMsg("저장됨: 문서/print/" + r.file); }
        else { status(""); toastMsg("PDF 실패: " + ((r && r.err) || "")); }
      }).catch(fail("PDF 변환 실패"));
      return;
    }
    status("PDF 변환 중…"); toastMsg("PDF 변환 중…");
    api("/api/export/pdf", { name: name, html: (WP.cleanedHTML ? WP.cleanedHTML() : page.innerHTML) }).then(function (r) {
      if (r && r.ok) { status("PDF 저장됨"); toastMsg("PDF 저장: " + r.file + "  ('pdf " + r.file + "' 로 열기)"); }
      else { status(""); toastMsg("PDF 실패: " + ((r && r.err) || "변환 서버 연결 오류")); }
    }).catch(fail("PDF 변환 실패"));
  }
  // 도형 → PNG <img> 치환한 export용 HTML(2026-07-18): HWPX엔 도형 직렬화가 없어 통째 유실되던 것을
  //   wp-print svgToPng 기법(캔버스 래스터, WebKit 오염 우회)으로 그림화해 기존 그림 파이프라인
  //   (data URI→add_picture, data-place/left/top 보존)에 태움. 재임포트=그림으로 복귀(시각 보존, 도형편집성은 상실).
  function rasterizeShapes() {
    var live = Array.prototype.slice.call(page.querySelectorAll('.wp-obj[data-obj="shape"]'));
    var div = document.createElement("div"); div.innerHTML = (WP.cleanedHTML ? WP.cleanedHTML() : page.innerHTML);
    // 쪽 배경그림 부속물은 HWPX 미반출(2026-07-18 사용자 지시 — 한글 비호환): 라벨·배경원본 개체 제거
    Array.prototype.forEach.call(div.querySelectorAll("p.pagebg-cap,[data-pagebg-src]"), function (el) { if (el.parentNode) el.parentNode.removeChild(el); });
    var clones = Array.prototype.slice.call(div.querySelectorAll('.wp-obj[data-obj="shape"]'));
    if (!live.length || live.length !== clones.length) return Promise.resolve(div.innerHTML);   // 불일치=치환 생략(안전)
    return Promise.all(live.map(function (el, i) {
      return new Promise(function (resolve) {
        var svg = el.querySelector("svg"); if (!svg) { resolve(); return; }
        var r = el.getBoundingClientRect();
        var w = Math.max(1, Math.round(r.width)), h = Math.max(1, Math.round(r.height));
        try {
          var sc = svg.cloneNode(true);
          sc.setAttribute("width", w); sc.setAttribute("height", h);
          if (!sc.getAttribute("xmlns")) sc.setAttribute("xmlns", "http://www.w3.org/2000/svg");
          var url = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(new XMLSerializer().serializeToString(sc));
          var im = new Image();
          im.onload = function () {
            try {
              var c = document.createElement("canvas"); c.width = w * 2; c.height = h * 2;   // 2배=출력 선명
              c.getContext("2d").drawImage(im, 0, 0, c.width, c.height);
              var img = document.createElement("img");
              img.src = c.toDataURL("image/png");
              var t = clones[i], st = t.style;
              img.setAttribute("data-place", t.getAttribute("data-place") || "char");
              // 도형 메타 주석(2026-07-18): 서버가 네이티브 OWPML 도형(hp:rect/ellipse/line — 한글 호환)으로
              //   내보내는 데 사용. PNG는 네이티브 실패 시 폴백으로 그대로 살아있음.
              img.setAttribute("data-shape-kind", t.getAttribute("data-shape") || "rect");
              var prim = t.querySelector("rect,ellipse,circle,line,polygon,path");
              if (prim) {
                img.setAttribute("data-shape-fill", prim.getAttribute("fill") || "none");
                img.setAttribute("data-shape-stroke", prim.getAttribute("stroke") || "#2f6fd6");
                img.setAttribute("data-shape-sw", prim.getAttribute("stroke-width") || "2");
              }
              img.setAttribute("style", "width:" + w + "px;height:" + h + "px;" +
                (st.left ? "left:" + st.left + ";" : "") + (st.top ? "top:" + st.top + ";" : "") +
                (st.zIndex ? "z-index:" + st.zIndex + ";" : ""));   // 통합 z스택(글앞/글뒤) HWPX 전달
              t.parentNode.replaceChild(img, t);
            } catch (e) {}
            resolve();
          };
          im.onerror = function () { resolve(); };
          im.src = url;
        } catch (e) { resolve(); }
      });
    })).then(function () { return div.innerHTML; });
  }
  function exportHwpx() {
    var name = (docname.value || "제목 없음").trim() || "제목 없음";
    status("HWPX 변환 중…"); toastMsg("HWPX 변환 중…");
    rasterizeShapes().then(function (html) {
      return api("/api/export/hwpx", { name: name, html: html });
    }).then(function (r) {
      if (r && r.ok) { status("HWPX 저장됨"); toastMsg("한글 문서로 저장: " + r.file); }
      else { status(""); toastMsg("HWPX 실패: " + ((r && r.err) || "")); }
    }).catch(fail("HWPX 변환 실패"));
  }
  function openHwpx() {
    $("m-open-title").textContent = "한글 문서 열기";
    openModal("m-open");
    var dl = $("doclist"); dl.innerHTML = '<div class="di"><span class="meta">목록 불러오는 중…</span></div>';
    api("/api/list/hwpx").then(function (items) {
      dl.innerHTML = "";
      if (!items || !items.length) { dl.innerHTML = '<div class="di"><span class="meta">HWPX/HWP 파일이 없습니다 (문서 폴더에 넣어주세요)</span></div>'; return; }
      items.forEach(function (it) {
        var row = document.createElement("div"); row.className = "di";
        row.innerHTML = '<span class="nm">' + esc(it.name) + '</span><span class="meta">한글</span>';
        row.querySelector(".nm").addEventListener("pointerup", function () { importHwpx(it.name); });
        dl.appendChild(row);
      });
    }).catch(failList(dl, "목록 불러오기 실패(연결?)"));
  }
  function importHwpx(fn) {
    status("HWPX 여는 중…"); toastMsg("한글 문서 여는 중…");
    api("/api/import/hwpx", { name: fn }).then(function (r) {
      if (r && r.ok) { docname.value = r.name; page.innerHTML = r.html && r.html.trim() ? r.html : "<p><br></p>"; if (WP.normalizeEdges) WP.normalizeEdges(); clean(); closeModal($("m-open")); status("불러옴: " + r.name); toastMsg(/\.doc$/i.test(fn) ? "Word 97-2003(.doc) 문서는 텍스트만 가져왔습니다 — 서식·표·그림은 .docx로 저장해 여세요" : "한글 문서 열기 완료"); page.focus(); if (WP.histReset) WP.histReset(); }
      else { status(""); toastMsg("열기 실패: " + ((r && r.err) || "")); }
    }).catch(fail("열기 실패"));
  }
  function image(onSrc) {   // onSrc(src,name)=교체모드(파일핸들 💾, 2026-07-19) / 미지정=삽입
    saveRange(); $("m-open-title").textContent = onSrc ? "그림 파일 바꾸기" : "그림 넣기"; openModal("m-open");
    var dl = $("doclist"); dl.innerHTML = '<div class="di"><span class="meta">이미지 불러오는 중…</span></div>';
    api("/api/images").then(function (items) {
      dl.innerHTML = "";
      if (!items || !items.length) { dl.innerHTML = '<div class="di"><span class="meta">이미지가 없습니다 (문서/images 폴더에 넣어주세요)</span></div>'; return; }
      items.forEach(function (it) {
        var row = document.createElement("div"); row.className = "di";
        row.innerHTML = '<span class="nm">🖼 ' + esc(it.name) + '</span><span class="meta">그림</span>';
        row.querySelector(".nm").addEventListener("pointerup", function () { insertImage(it.name, onSrc); });
        dl.appendChild(row);
      });
    }).catch(failList(dl, "이미지 목록 실패(연결?)"));
  }
  function insertImage(name, onSrc) {
    status("이미지 확인 중…");
    api("/api/image?name=" + encodeURIComponent(name)).then(function (r) {
      if (r && r.ok && r.src) {
        closeModal($("m-open")); restoreRange();
        if (onSrc) { onSrc(r.src, name); status(""); return; }
        if (WP.insertImageObj) WP.insertImageObj(r.src);   // .wp-obj 블록 개체(회전/배치/묶기/맞춤 통일)
        else document.execCommand("insertHTML", false, '<img src="' + r.src + '" style="max-width:100%">');
        markDirty(); status("이미지 삽입됨");
      } else { status(""); toastMsg("이미지 불러오기 실패"); }
    }).catch(fail("이미지 삽입 실패"));
  }
  // 미디어(영상·소리) 피커 — 문서/media 폴더 목록 → data URI 삽입(재생 개체)
  function mediaPick(onSrc) {   // onSrc(src,kind,name) 지정=선택만 콜백(교체모드, 파일핸들 파일바꾸기) / 미지정=삽입
    // 웹판(P10, 2026-07-19): 서버 무저장 철학 — 로컬 파일 픽커 → IndexedDB(wpMedia, 키=파일명)에 Blob 저장, 마크업 src=파일명 단독(재생은 해석기)
    var inp = document.createElement("input"); inp.type = "file";
    inp.accept = "video/*,audio/*,.mp4,.webm,.mkv,.mov,.avi,.mp3,.wav,.m4a,.oga,.ogg,.aac,.flac";
    inp.addEventListener("change", function () {
      var f = inp.files && inp.files[0]; if (!f) { restoreRange(); return; }
      var kind = /^audio\//.test(f.type) || /\.(mp3|wav|m4a|oga|ogg|aac|flac)$/i.test(f.name) ? "audio" : "video";
      status("미디어 저장 중…");
      WP.mediaStore(f.name, f, function (okp) {
        if (!okp) { status(""); toastMsg("미디어 저장 실패(IndexedDB 사용 불가?)"); return; }
        var url = URL.createObjectURL(f); WP._mediaNames[url] = f.name;   // 저장 정규화(cleanedClone)가 blob→파일명 환원
        restoreRange();
        if (onSrc) { onSrc(url, kind, f.name); status(""); return; }
        if (WP.insertMediaObj) WP.insertMediaObj(url, kind);
        markDirty(); status("미디어 삽입됨: " + f.name);
        toastMsg("미디어는 이 브라우저에 저장됩니다 — 문서 공유 시 상대에겐 영상 미포함");   // 한계 안내(P10)
      });
    });
    saveRange(); inp.click();
  }
  WP.mediaPick = mediaPick;
  WP.imagePick = image;   // 콜백=교체모드(개체 파일핸들)

  // 머리말/꼬리말/쪽번호 — 숨김 #wpmeta div
  function wpmetaEl() {
    var el = page.querySelector("#wpmeta");
    if (!el) { el = document.createElement("div"); el.id = "wpmeta"; el.setAttribute("hidden", ""); page.appendChild(el); }
    return el;
  }
  function readMeta() {
    var el = page.querySelector("#wpmeta");
    function ga(k) { return el ? el.getAttribute("data-" + k) || "" : ""; }
    var start = parseInt(ga("pagenum-start"), 10);
    return {
      header: ga("header"), footer: ga("footer"), pagenum: ga("pagenum"),
      // #21 확장 — 부재 시 기본값 폴백(구형 문서 하위호환)
      pagenumFormat: ga("pagenum-format") || "1",
      pagenumStart: (start >= 1 ? start : 1),
      headerOdd: ga("header-odd"), headerEven: ga("header-even"),
      // #26 쪽 테두리 — 선종류 화이트리스트(공개모드 문서의 style 주입 차단)·굵기 1~3px
      pageBorder: /^(solid|dotted|dashed|double)$/.test(ga("pageborder")) ? ga("pageborder") : "",
      pageBorderW: Math.min(3, Math.max(1, parseInt(ga("pageborder-w"), 10) || 1)),
      // 쪽 배경(문서=미디어) — 색·그림(data URI/상대경로만 허용, 외부 스킴 차단)·채움방식
      pageBgColor: /^#[0-9a-fA-F]{3,8}$/.test(ga("pagebg-color")) ? ga("pagebg-color") : "",
      pageBgImage: (function () { var b = ga("pagebg-image"); return (/^data:image\//.test(b) || (b && !/:/.test(b))) ? b : ""; })(),
      pageBgSize: /^(cover|contain|repeat)$/.test(ga("pagebg-size")) ? ga("pagebg-size") : "cover"
    };
  }
  // 쪽번호 표기(1/①/i/가). wp-preview가 재사용(WP.fmtPageNum). htmlpdf.js에 동일 로직 사본 — 동기 유지.
  function fmtPageNum(n, f) {
    if (!(n >= 1) || !f || f === "1") return String(n);
    if (f === "circ") {   // ①~⑳ ㉑~㉟ ㊱~㊿, 범위 밖은 아라비아 폴백
      if (n <= 20) return String.fromCharCode(0x2460 + n - 1);
      if (n <= 35) return String.fromCharCode(0x3251 + n - 21);
      if (n <= 50) return String.fromCharCode(0x32B1 + n - 36);
      return String(n);
    }
    if (f === "roman") {
      var v = [1000, 900, 500, 400, 100, 90, 50, 40, 10, 9, 5, 4, 1],
          s = ["m", "cm", "d", "cd", "c", "xc", "l", "xl", "x", "ix", "v", "iv", "i"], out = "";
      for (var i = 0; i < v.length; i++) while (n >= v[i]) { out += s[i]; n -= v[i]; }
      return out;
    }
    if (f === "hangul") {   // 가~하(14자) 순환, 한 바퀴마다 글자 반복(가→…→하→가가→…)
      var H = "가나다라마바사아자차카타파하", ch = H[(n - 1) % 14];
      return new Array(Math.floor((n - 1) / 14) + 2).join(ch);
    }
    return String(n);
  }
  // 홀짝 머리말 선택 — odd/even 지정 문서는 표시번호 홀짝으로 분기, 아니면 공용 header
  function headerFor(m, dispNum) {
    if (m.headerOdd || m.headerEven) return (dispNum % 2 === 1) ? m.headerOdd : m.headerEven;
    return m.header;
  }
  function toggleOddEven() {
    var on = $("hf-oddeven").checked;
    document.querySelectorAll("#m-headfoot .hf-oe").forEach(function (r) { r.classList.toggle("hidden", !on); });
  }
  function openHeadFoot() {
    var m = readMeta();
    $("hf-header").value = m.header; $("hf-footer").value = m.footer; $("hf-pagenum").value = m.pagenum;
    $("hf-pnformat").value = m.pagenumFormat; $("hf-pnstart").value = m.pagenumStart;
    $("hf-oddeven").checked = !!(m.headerOdd || m.headerEven);
    $("hf-header-odd").value = m.headerOdd; $("hf-header-even").value = m.headerEven;
    toggleOddEven();
    openModal("m-headfoot");
  }
  function headFootApply() {
    var el = wpmetaEl();
    el.setAttribute("data-header", $("hf-header").value);
    el.setAttribute("data-footer", $("hf-footer").value);
    el.setAttribute("data-pagenum", $("hf-pagenum").value);
    // 확장 속성은 기본값이면 제거(문서 청결·구형 렌더러 호환)
    var fmt = $("hf-pnformat").value, start = parseInt($("hf-pnstart").value, 10);
    if (fmt && fmt !== "1") el.setAttribute("data-pagenum-format", fmt); else el.removeAttribute("data-pagenum-format");
    if (start >= 2) el.setAttribute("data-pagenum-start", String(start)); else el.removeAttribute("data-pagenum-start");
    if ($("hf-oddeven").checked) {
      el.setAttribute("data-header-odd", $("hf-header-odd").value);
      el.setAttribute("data-header-even", $("hf-header-even").value);
    } else { el.removeAttribute("data-header-odd"); el.removeAttribute("data-header-even"); }
    closeModal($("m-headfoot")); markDirty(); toastMsg("머리말/꼬리말/쪽번호 설정됨 (PDF·HWPX 출력에 적용)");
  }
  function bindOddEven() { var cb = $("hf-oddeven"); if (cb) cb.addEventListener("change", toggleOddEven); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bindOddEven); else bindOddEven();

  // ── 문서 → 평문 변환(TXT 저장용, 2026-07-22 기기 embed 이식) ──────────────────
  //   원칙: 터미널(고정폭)에서 그대로 읽히게. 표=유니코드 박스문자, 제목=밑줄,
  //   목록=글머리 기호, 쪽나누기=폼피드(\f, TXT 다시 열 때 쪽으로 복원).
  //   ⚠️폭 계산: 한글·한자·전각=2칸, 결합문자·ZWJ=0칸. 서로게이트(이모지) 때문에 코드포인트 단위 순회.
  //     박스문자(U+2500~)는 glibc wcwidth=1 → 1칸.
  var W2 = /[\u1100-\u115F\u2E80-\uA4CF\uA960-\uA97F\uAC00-\uD7A3\uF900-\uFAFF\uFE10-\uFE6F\uFF00-\uFF60\uFFE0-\uFFE6]/;
  var W2S = /[\u{1F300}-\u{1FAFF}\u{20000}-\u{3FFFD}]/u;
  var W0 = /[\u0300-\u036F\u200B-\u200D\uFE0F\u1160-\u11FF\uD7B0-\uD7FF]/;
  function cw(ch) { return W0.test(ch) ? 0 : (W2.test(ch) || W2S.test(ch)) ? 2 : 1; }
  function chars(s) { var a = [], i, c, ch; for (i = 0; i < s.length; i++) { c = s.codePointAt(i); ch = String.fromCodePoint(c); if (ch.length > 1) i++; a.push(ch); } return a; }
  function vlen(s) { var n = 0, a = chars(s), i; for (i = 0; i < a.length; i++) n += cw(a[i]); return n; }
  function padTo(s, w, align) {
    var gap = Math.max(0, w - vlen(s));
    if (align === "right") return new Array(gap + 1).join(" ") + s;
    if (align === "center") { var l = Math.floor(gap / 2); return new Array(l + 1).join(" ") + s + new Array(gap - l + 1).join(" "); }
    return s + new Array(gap + 1).join(" ");
  }
  function cutTo(s, w) {   // 표시폭 기준 자르기(한글 2칸이라 substr 못 씀)
    var out = "", n = 0, a = chars(s);
    for (var i = 0; i < a.length; i++) { var c = cw(a[i]); if (n + c > w) break; out += a[i]; n += c; }
    return out;
  }
  function wrapLines(text, w) {   // 표시폭 기준 줄바꿈(공백 우선, 없으면 강제절단=한글 대응)
    var out = [];
    text.split("\n").forEach(function (para) {
      var line = "";
      para.split(/(\s+)/).forEach(function (tok) {
        if (!tok) return;
        if (vlen(line + tok) <= w) { line += tok; return; }
        if (line.trim()) { out.push(line.replace(/\s+$/, "")); line = ""; }
        if (/^\s+$/.test(tok)) return;
        while (vlen(tok) > w) { var head = cutTo(tok, w); out.push(head); tok = tok.slice(head.length); }
        line = tok;
      });
      out.push(line.replace(/\s+$/, ""));
    });
    return out.length ? out : [""];
  }
  function inlineText(node) {   // 인라인 노드 → 텍스트(<br>=줄바꿈, 그림/영상/도형=자리표시)
    if (node.nodeType === 3) return node.nodeValue.replace(/ /g, " ");
    if (node.nodeType !== 1) return "";
    var tag = node.tagName;
    if (tag === "BR") return "\n";
    if (tag === "IMG") return "[그림]";
    if (tag === "VIDEO") return "[영상]";
    if (tag === "AUDIO") return "[소리]";
    if (node.hasAttribute && node.hasAttribute("hidden")) return "";
    if (node.classList && node.classList.contains("wp-obj") && node.getAttribute("data-obj") === "shape") return "[도형]";
    var s = "";
    for (var i = 0; i < node.childNodes.length; i++) s += inlineText(node.childNodes[i]);
    return s;
  }
  function cellText(td) { return inlineText(td).replace(/[ \t]+\n/g, "\n").replace(/\s+$/, "").replace(/^\s+/, ""); }
  function cellAlign(td) {
    var a = (td.style && td.style.textAlign) || "";
    return a === "center" || a === "right" ? a : "left";
  }
  // ── 표 → 박스문자 표(병합=논리격자 점유맵으로 경계선 정확) ──
  var JUNC = [" ", "─", "─", "─", "│", "┌", "┐", "┬", "│", "└", "┘", "┴", "│", "├", "┤", "┼"];
  var TBL_MAXW = 28;      // 열 하나의 최대 표시폭
  var TBL_TOTAL = 78;     // 표 전체 최대 폭
  function tableGrid(tbl) {
    var rows = Array.prototype.slice.call(tbl.rows), grid = [], nc = 0;
    rows.forEach(function (tr, r) {
      grid[r] = grid[r] || [];
      var c = 0;
      Array.prototype.forEach.call(tr.cells, function (td) {
        while (grid[r][c]) c++;                                   // 위 행 rowspan이 차지한 칸 건너뜀
        var cs = Math.max(1, td.colSpan || 1), rs = Math.max(1, td.rowSpan || 1);
        var cell = { td: td, r0: r, c0: c, cs: cs, rs: rs, align: cellAlign(td), text: cellText(td) };
        for (var i = 0; i < rs; i++) { grid[r + i] = grid[r + i] || []; for (var j = 0; j < cs; j++) grid[r + i][c + j] = cell; }
        c += cs; if (c > nc) nc = c;
      });
    });
    for (var r = 0; r < grid.length; r++) for (var c2 = 0; c2 < nc; c2++) if (!grid[r][c2]) grid[r][c2] = null;
    return { grid: grid, nr: grid.length, nc: nc };
  }
  function tableToText(tbl) {
    var g = tableGrid(tbl), grid = g.grid, nr = g.nr, nc = g.nc;
    if (!nr || !nc) return [];
    var colw = [], r, c, i;
    for (c = 0; c < nc; c++) colw[c] = 1;
    function natural(cell) { return cell.text.split("\n").reduce(function (m, ln) { return Math.max(m, vlen(ln)); }, 0); }
    for (r = 0; r < nr; r++) for (c = 0; c < nc; c++) {          // 1열 셀로 기본 폭
      var cell = grid[r][c];
      if (cell && cell.r0 === r && cell.c0 === c && cell.cs === 1) colw[c] = Math.max(colw[c], Math.min(TBL_MAXW, natural(cell)));
    }
    for (r = 0; r < nr; r++) for (c = 0; c < nc; c++) {          // 병합 셀이 더 넓으면 마지막 열에 부족분 배분
      var cl = grid[r][c];
      if (!cl || cl.r0 !== r || cl.c0 !== c || cl.cs === 1) continue;
      var have = 0; for (i = 0; i < cl.cs; i++) have += colw[c + i] + 3;
      var want = Math.min(TBL_MAXW * cl.cs, natural(cl)) + 3;
      if (want > have) colw[c + cl.cs - 1] += want - have;
    }
    var total = function () { var t = nc + 1; for (var k = 0; k < nc; k++) t += colw[k] + 2; return t; };
    while (total() > TBL_TOTAL) {   // 터미널 폭 초과 시 가장 넓은 열부터 1칸씩(하한 3)
      var wide = -1, wmax = 3;
      for (c = 0; c < nc; c++) if (colw[c] > wmax) { wmax = colw[c]; wide = c; }
      if (wide < 0) break;
      colw[wide]--;
    }
    function span(c0, cs) { var w = 0; for (var k = 0; k < cs; k++) w += colw[c0 + k] + 3; return w - 3; }
    for (r = 0; r < nr; r++) for (c = 0; c < nc; c++) {
      var cc = grid[r][c];
      if (cc && cc.r0 === r && cc.c0 === c) cc.lines = wrapLines(cc.text, span(c, cc.cs));
    }
    var vAt = function (r, b) { return b === 0 || b === nc || grid[r][b - 1] !== grid[r][b]; };
    var hAt = function (r, c) { return r === 0 || r === nr || grid[r - 1][c] !== grid[r][c]; };
    function border(r) {
      var s = "";
      for (var b = 0; b <= nc; b++) {
        var up = r > 0 && vAt(r - 1, b), dn = r < nr && vAt(r, b),
            lf = b > 0 && hAt(r, b - 1), rt = b < nc && hAt(r, b);
        s += JUNC[(up ? 8 : 0) + (dn ? 4 : 0) + (lf ? 2 : 0) + (rt ? 1 : 0)];
        if (b < nc) s += new Array(colw[b] + 3).join(hAt(r, b) ? "─" : " ");
      }
      return s;
    }
    var out = [border(0)];
    for (r = 0; r < nr; r++) {
      var h = 1;
      for (c = 0; c < nc; c++) { var s0 = grid[r][c]; if (s0 && s0.r0 === r && s0.c0 === c) h = Math.max(h, s0.lines.length); }
      for (i = 0; i < h; i++) {
        var line = vAt(r, 0) ? "│" : " ";
        c = 0;
        while (c < nc) {
          var cl2 = grid[r][c], cs = cl2 ? cl2.cs : 1;
          var txt = (cl2 && cl2.r0 === r && cl2.lines[i]) || "";       // rowspan 이어지는 행=빈칸
          line += " " + padTo(txt, span(c, cs), cl2 ? cl2.align : "left") + " ";
          c += cs;
          line += (c >= nc || vAt(r, c)) ? "│" : " ";
        }
        out.push(line);
      }
      out.push(border(r + 1));
    }
    return out;
  }
  function blockLines(el, indent, out) {
    var pad = new Array(indent + 1).join(" ");
    Array.prototype.forEach.call(el.childNodes, function (n) {
      if (n.nodeType === 3) { var t = n.nodeValue.replace(/ /g, " ").trim(); if (t) out.push(pad + t); return; }
      if (n.nodeType !== 1) return;
      if (n.id === "wpmeta" || n.id === "pageguides" || n.hasAttribute("hidden")) return;
      var tag = n.tagName;
      if (tag === "TABLE") { out.push(""); tableToText(n).forEach(function (l) { out.push(pad + l); }); out.push(""); return; }
      if (tag === "UL" || tag === "OL") {
        var num = 1, marks = ["•", "◦", "▪"];
        Array.prototype.forEach.call(n.children, function (li) {
          if (li.tagName !== "LI") return;
          var head = tag === "OL" ? (num++) + ". " : marks[Math.min(2, indent / 2 | 0)] + " ";
          var sub = document.createElement("div"), kids = [];
          Array.prototype.forEach.call(li.childNodes, function (k) {
            if (k.nodeType === 1 && (k.tagName === "UL" || k.tagName === "OL")) kids.push(k); else sub.appendChild(k.cloneNode(true));
          });
          var body = inlineText(sub).replace(/\s+/g, " ").trim();
          out.push(pad + head + body);
          kids.forEach(function (k) { blockLines({ childNodes: [k] }, indent + head.length, out); });
        });
        return;
      }
      if (tag === "HR") { out.push(pad + new Array(41).join("─")); return; }
      if (n.classList && n.classList.contains("pgbreak")) { out.push("\f"); return; }
      if (tag === "DIV" || tag === "SECTION" || tag === "ARTICLE" || (n.classList && n.classList.contains("objline"))) { blockLines(n, indent, out); return; }
      var text = inlineText(n).replace(/[ \t]+/g, " ").replace(/ ?\n ?/g, "\n").trim();
      if (/^H[1-6]$/.test(tag)) {
        out.push("");
        text.split("\n").forEach(function (l) { out.push(pad + l); });
        if (tag === "H1" || tag === "H2") out.push(pad + new Array(Math.max(2, vlen(text) + 1)).join(tag === "H1" ? "═" : "─"));
        out.push("");
        return;
      }
      if (tag === "BLOCKQUOTE") { text.split("\n").forEach(function (l) { out.push(pad + "> " + l); }); return; }
      if (tag === "PRE") { inlineText(n).split("\n").forEach(function (l) { out.push(pad + l); }); return; }
      text.split("\n").forEach(function (l) { out.push(pad + l); });
    });
    return out;
  }
  function docText() {
    var lines = blockLines(page, 0, []);
    while (lines.length && !lines[0].trim()) lines.shift();
    while (lines.length && !lines[lines.length - 1].trim()) lines.pop();
    return lines.join("\n").replace(/\n{3,}/g, "\n\n").replace(/\n*\f\n*/g, "\n\f\n") + "\n";
  }
  WP.docText = docText;   // TXT 내려받기(wp-public dlTxt)·회귀 테스트가 사용

  WP.readMeta = readMeta;   // 미리보기(wp-preview.js)가 머릿말/꼬리말/쪽번호 재사용
  WP.fmtPageNum = fmtPageNum; WP.headerFor = headerFor;   // #21 쪽번호 표기·홀짝 머리말(미리보기 공유)
  WP.wpmetaEl = wpmetaEl;   // #26 쪽 테두리 — 편집용지 대화상자(wp-format)가 wpmeta에 기록
  WP.importHwpx = importHwpx;   // #31 파일 자동열기(?open=)가 hwpx/hwp 열기에 재사용
  WP.register({ pdf: pdf, exportHwpx: exportHwpx, openHwpx: openHwpx, image: image, header: openHeadFoot, pagenum: openHeadFoot, headFootApply: headFootApply });
})();
