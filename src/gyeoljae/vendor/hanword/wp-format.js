// 한워드 — 서식(글자모양/문단모양/편집용지 + 서식 도구상자 + 정렬/목록).
(function () {
  "use strict";
  var WP = window.WP, $ = WP.$, page = WP.page, exec = WP.exec, markDirty = WP.markDirty,
      openModal = WP.openModal, closeModal = WP.closeModal, toastMsg = WP.toast,
      saveRange = WP.saveRange, restoreRange = WP.restoreRange;
  var PAGECFG = { paper:"A4", orient:"portrait" };

  function curBlock() {
    var s = window.getSelection(); if (!s.rangeCount) return null;
    var n = s.anchorNode; if (n && n.nodeType === 3) n = n.parentNode;
    while (n && n !== page && !/^(P|H1|H2|H3|LI|DIV|TD|TH)$/.test(n.tagName)) n = n.parentNode;
    return (n && n !== page) ? n : null;
  }
  function selBlocks() {
    var s = window.getSelection(); if (!s.rangeCount) { var b = curBlock(); return b ? [b] : []; }
    var blocks = [], all = page.querySelectorAll("p,h1,h2,h3,li,div,td,th");
    for (var i = 0; i < all.length; i++) {
      var el = all[i];
      if (el.matches && el.matches(".pgbreak,.objline,[data-pgfill],#wpmeta")) continue;   // 구조용 div엔 문단 서식 미적용 (HEAD fd4b780 복원)
      if (s.containsNode(el, true)) blocks.push(el);
    }
    if (!blocks.length) { var c = curBlock(); if (c) blocks.push(c); }
    return blocks;
  }
  function curRun() {   // 커서/선택의 실제 서식 요소(span 등) — 블록보다 정확. 크기/글꼴 등 읽기 기준 (HEAD fd4b780 복원)
    var s = window.getSelection(); if (!s.rangeCount) return curBlock() || page;
    // 앵커(드래그 시작점)가 아니라 '범위 시작'을 기준 — 역방향 선택·혼합 크기에서도 보이는 앞쪽 run을 안정적으로 읽음
    var r = s.getRangeAt(0), n = r.startContainer, off = r.startOffset;
    if (n && n.nodeType === 1) n = n.childNodes[off] || n.childNodes[off - 1] || n;
    if (n && n.nodeType === 3) n = n.parentNode;
    return (n && n.nodeType === 1 && n !== page && page.contains(n)) ? n : (curBlock() || page);
  }
  // ── run 기반 서식(#9, HEAD fd4b780 복원): 대기 span·shadowing 제거·빈 span 정리 ──
  function unwrapEl(el) { var pa = el.parentNode; if (!pa) return; while (el.firstChild) pa.insertBefore(el.firstChild, el); pa.removeChild(el); }
  // 서식 조작으로 비워진 span 즉시 제거(라이브 오염 방지). 대기 span은 제로폭(﻿) 텍스트가 있어 보존 — 완전 빈 것만
  function cleanEmptySpans(scope) {
    if (!scope || !scope.querySelectorAll) return;
    Array.prototype.slice.call(scope.querySelectorAll("span")).forEach(function (sp) {
      if (!sp.textContent && !sp.querySelector("img,br")) unwrapEl(sp);
    });
  }
  // 하위 요소에서 같은 스타일 속성 제거 → 안쪽 값이 바깥 wrapper 값을 가리는(shadowing) 것 방지 + 스타일 빈 무속성 span 언랩(중첩 누적 방지)
  function stripDesc(root, styleObj) {
    if (!root || !root.querySelectorAll) return;
    Array.prototype.slice.call(root.querySelectorAll("*")).forEach(function (el) {
      for (var k in styleObj) el.style[k] = "";
      if (el.tagName === "SPAN") {
        if (!el.getAttribute("style")) el.removeAttribute("style");   // 비워진 style="" 잔재 제거(중첩 판정 방해)
        if (!el.attributes.length) unwrapEl(el);   // class 가진 신작 span(.fn-no 등)은 보존
      }
    });
  }
  function reselect(el) { try { var nr = document.createRange(); nr.selectNodeContents(el); var s = window.getSelection(); s.removeAllRanges(); s.addRange(nr); } catch (x) {} }
  // 선택 없이 캐럿만: 이후 입력 글자에만 적용(아래한글식). 제로폭(﻿) 자리표시는 입력 시 그 안으로 흡수되고 저장·글자수에선 제거
  function caretPendingSpan(styleObj) {
    var s = window.getSelection(); if (!s.rangeCount) return;
    var r = s.getRangeAt(0), span = document.createElement("span"), p;
    for (p in styleObj) span.style[p] = styleObj[p];
    span.appendChild(document.createTextNode("\uFEFF"));
    r.insertNode(span);
    var r2 = document.createRange(); r2.selectNodeContents(span); r2.collapse(false);
    s.removeAllRanges(); s.addRange(r2);
  }
  function wrapStyle(styleObj) {
    var s = window.getSelection(); if (!s.rangeCount) return;
    var range = s.getRangeAt(0), k;
    if (range.collapsed) {
      var b = curRun();
      if (b && b !== page && b.tagName === "SPAN") { for (k in styleObj) b.style[k] = styleObj[k]; }   // 인라인 run/대기 span 안: 그 run만 갱신(중첩·문단 전체 리사이즈 방지)
      else caretPendingSpan(styleObj);   // 맨몸 텍스트/블록: 문단 전체 대신 다음 입력 글자용 대기 span
      return;
    }
    // 선택이 기존 span 전체와 일치하면(직전 wrapStyle이 남긴 선택 등) 중첩 생성 없이 그 span에 갱신 — 연속 적용·마크업 청결
    var n = range.commonAncestorContainer; if (n.nodeType === 3) n = n.parentNode;
    if (n && n.tagName === "SPAN" && page.contains(n) && range.toString() === n.textContent) {
      for (k in styleObj) n.style[k] = styleObj[k];
      stripDesc(n, styleObj); reselect(n); return;   // 안쪽 shadowing 제거 후 선택 복원
    }
    var span = document.createElement("span");
    for (var p in styleObj) span.style[p] = styleObj[p];
    try { range.surroundContents(span); }
    catch (e) { var frag = range.extractContents(); span.appendChild(frag); range.insertNode(span); }
    stripDesc(span, styleObj);   // 혼합 선택: 안쪽 기존 span의 같은 속성 제거 → 바깥 값이 전체에 균일 적용
    cleanEmptySpans(span.closest ? (span.closest("p,h1,h2,h3,li,div,td") || span.parentNode) : span.parentNode);   // extractContents로 비워진 원본 span 즉시 제거
    reselect(span);   // 선택 유지(연속 서식·시각 피드백)
  }

  // 글자 크기 단계
  var SIZES = [8,9,10,11,12,14,16,18,20,24,28,36,48];
  function curPt() { var b = curRun(), px = parseFloat(getComputedStyle(b).fontSize) || 14.67; return Math.round(px / 1.3333); }   // run 기준(연속 크게/작게, HEAD 복원)
  function setPt(pt) { wrapStyle({ fontSize: pt + "pt" }); $("sizeSel").value = String(pt); markDirty(); }
  function stepFont(d) {
    var cur = curPt(), idx = 0, best = 1e9;
    SIZES.forEach(function (s, i) { if (Math.abs(s - cur) < best) { best = Math.abs(s - cur); idx = i; } });
    idx = Math.max(0, Math.min(SIZES.length - 1, idx + d)); setPt(SIZES[idx]);
  }

  // 서식 도구 상자
  $("toolbar").addEventListener("pointerdown", function (e) { if (e.target.closest("[data-act]")) e.preventDefault(); });   // 본문 선택영역 유지
  $("toolbar").addEventListener("pointerup", function (e) { var b = e.target.closest("[data-act]"); if (b) WP.dispatch(b.getAttribute("data-act")); });

  // ── 좁은 폭 오버플로 힌트(P2-7): 스크롤 여지가 있는 쪽 가장자리 페이드(.more-l/.more-r) ──
  function scrollHint(el) {
    function setCls(c, v) { if (el.classList.contains(c) !== v) el.classList.toggle(c, v); }   // 무변경 시 미기록 → 옵저버 루프 방지
    function upd() {
      setCls("more-l", el.scrollLeft > 4);
      setCls("more-r", el.scrollLeft + el.clientWidth < el.scrollWidth - 4);
    }
    el.addEventListener("scroll", upd, { passive: true });
    window.addEventListener("resize", upd);
    new MutationObserver(upd).observe(el, { attributes: true, attributeFilter: ["class", "style"] });   // 표도구 show/hide 대응
    upd();
  }
  scrollHint($("toolbar")); scrollHint($("tabletools"));
  // 네이티브 select·색상 picker는 포커스를 가져가 선택이 무너짐(특히 모바일) → 열기 전 저장, 적용 전 복원 (HEAD fd4b780 복원)
  ["styleSel", "fontSel", "sizeSel"].forEach(function (id) {
    $(id).addEventListener("pointerdown", saveRange);
    $(id).addEventListener("focus", saveRange);
  });
  $("styleSel").addEventListener("change", function () { restoreRange(); exec("formatBlock", this.value === "P" ? "P" : this.value); });
  $("fontSel").addEventListener("change", function () { if (!this.value) return; restoreRange(); wrapStyle({ fontFamily: this.value }); markDirty(); });
  $("sizeSel").addEventListener("change", function () { restoreRange(); setPt(parseInt(this.value, 10)); });
  // 글자색·형광펜 툴바 버튼은 제거됨(글자모양 대화상자 색상샘플로 일원화, 2026-07-17)

  // ── 툴바 상태 동기화: 커서 위치의 실제 서식을 버튼/셀렉트에 반영 (selectionchange, rAF 스로틀 — HEAD fd4b780 복원) ──
  var STATE_BTNS = { bold:"bold", italic:"italic", underline:"underline", strike:"strikeThrough",
    superscript:"superscript", subscript:"subscript",
    justifyLeft:"justifyLeft", justifyCenter:"justifyCenter", justifyRight:"justifyRight", justifyFull:"justifyFull",
    insertUnorderedList:"insertUnorderedList", insertOrderedList:"insertOrderedList" };
  var btnMap = {};
  document.querySelectorAll("#toolbar [data-act]").forEach(function (b) {
    var a = b.getAttribute("data-act"); if (STATE_BTNS[a]) btnMap[a] = b;
  });
  function familyKey(s) { return String(s).split(",")[0].replace(/["']/g, "").trim().toLowerCase(); }
  function syncToolbar() {
    syncQueued = false;
    var s = window.getSelection();
    if (!s.rangeCount || !page.contains(s.anchorNode)) return;   // 편집영역 밖이면 마지막 상태 유지
    for (var act in btnMap) {
      var on = false; try { on = document.queryCommandState(STATE_BTNS[act]); } catch (e) {}
      btnMap[act].classList.toggle("on", on);
    }
    var blk = curBlock(), cs = getComputedStyle(curRun());   // 글꼴/크기는 run 기준(문단 아닌 실제 커서 서식)
    var tag = blk ? blk.tagName : "P"; $("styleSel").value = /^(H1|H2|H3)$/.test(tag) ? tag : "P";
    var fam = familyKey(cs.fontFamily), fsel = $("fontSel"), fi = -1;   // WT 목록엔 "(기본)" 없음 → 미일치=빈 표시(HEAD와 다른 점)
    for (var i = 0; i < fsel.options.length; i++) if (familyKey(fsel.options[i].value) === fam) { fi = i; break; }
    fsel.selectedIndex = fi;
    var pt = Math.round((parseFloat(cs.fontSize) || 14.67) / 1.3333), ssel = $("sizeSel"), si = -1;
    for (var j = 0; j < ssel.options.length; j++) if (parseInt(ssel.options[j].value, 10) === pt) { si = j; break; }
    ssel.selectedIndex = si;   // 목록에 없는 크기는 빈 표시
  }
  var syncQueued = false;
  document.addEventListener("selectionchange", function () {
    if (!syncQueued) { syncQueued = true; requestAnimationFrame(syncToolbar); }
  });
  WP.syncToolbar = syncToolbar;   // 액션 직후 .on 즉시 반영용(P1-5) — selectionchange 미발생 액션(정렬·목록) 보완

  // 대화상자 단위: 표시·입력은 pt(HWP 관례), 저장 스타일은 px 유지(눈금자·변환기 호환) — 경계에서만 환산(P2-9)
  var PXPT = 96 / 72;
  // 글자 모양
  function openChar() {
    saveRange();
    var b = curBlock() || page, cs = getComputedStyle(b);
    $("c-size").value = Math.round((parseFloat(cs.fontSize) || 14.67) / 1.3333);
    document.querySelectorAll("#m-char .att").forEach(function (x) {
      var cmd = { bold:"bold", italic:"italic", underline:"underline", strike:"strikeThrough", superscript:"superscript", subscript:"subscript" }[x.dataset.att];
      x.classList.toggle("on", document.queryCommandState(cmd));
    });
    $("c-spacing").value = Math.round((parseFloat(cs.letterSpacing) || 0) / PXPT * 10) / 10;
    try { if (WP.syncPalette) WP.syncPalette("c-color", document.queryCommandValue("foreColor")); } catch (e) {}   // 현재 글자색을 팔레트에 반영
    shadeNone(); openModal("m-char");   // 음영색 기본=없음(표시칸·선택 초기화)
  }
  document.querySelectorAll("#m-char .att").forEach(function (x) { x.addEventListener("pointerup", function () { x.classList.toggle("on"); }); });
  // ── 색상 팔레트(코드 대신 샘플): .cpalette[data-for=<hidden input>][data-cur=<표시칸>] ──
  var PALETTE_COLORS = ["#000000", "#404040", "#808080", "#a6a6a6", "#d9d9d9", "#ffffff",
    "#c00000", "#e36c09", "#ffc000", "#00b050", "#00b0b0", "#0070c0", "#7030a0", "#e84c9a",
    "#ff0000", "#ff9900", "#ffd966", "#92d050", "#00b0f0", "#8000ff", "#ff00ff", "#7f6000"];
  function rgbToHex(c) {
    if (!c) return null; if (c.charAt(0) === "#") return c;
    var m = /rgba?\((\d+),\s*(\d+),\s*(\d+)/.exec(c); if (!m) return null;
    function h(n) { return ("0" + parseInt(n, 10).toString(16)).slice(-2); }
    return "#" + h(m[1]) + h(m[2]) + h(m[3]);
  }
  function setPaletteVal(pal, color) {
    var inp = document.getElementById(pal.getAttribute("data-for")); if (inp) inp.value = color;
    var cur = document.getElementById(pal.getAttribute("data-cur")); if (cur) cur.style.background = color;
    var lc = String(color).toLowerCase();
    pal.querySelectorAll(".sw").forEach(function (s) { s.classList.toggle("sel", (s.getAttribute("data-color") || "").toLowerCase() === lc); });
  }
  document.querySelectorAll(".cpalette").forEach(function (pal) {
    var inp = document.getElementById(pal.getAttribute("data-for"));
    PALETTE_COLORS.forEach(function (c) {
      var s = document.createElement("button"); s.type = "button"; s.className = "sw"; s.setAttribute("data-color", c); s.style.background = c; s.title = c;
      s.addEventListener("pointerup", function (e) { e.preventDefault(); setPaletteVal(pal, c); if (inp) inp.dataset.none = ""; });   // 색 선택=음영 적용(없음 해제)
      pal.appendChild(s);
    });
    if (inp) setPaletteVal(pal, inp.value || "#000000");
  });
  WP.syncPalette = function (id, color) { var pal = document.querySelector('.cpalette[data-for="' + id + '"]'); if (pal) setPaletteVal(pal, rgbToHex(color) || color || "#000000"); };
  function shadeNone() {   // 음영 없음: 값 초기화 + 팔레트 선택 해제 + 표시칸 비움
    $("c-bg").value = "#ffffff"; $("c-bg").dataset.none = "1";
    var pal = document.querySelector('.cpalette[data-for="c-bg"]'); if (pal) pal.querySelectorAll(".sw").forEach(function (s) { s.classList.remove("sel"); });
    var cur = document.getElementById("c-bg-cur"); if (cur) cur.style.background = "transparent";
  }
  $("c-bg-none").addEventListener("pointerup", shadeNone);
  function charApply() {
    restoreRange();
    var fam = $("c-font").value, pt = parseInt($("c-size").value, 10), col = $("c-color").value;
    document.execCommand("styleWithCSS", false, true);
    try {                                                  // 예외 나도 styleWithCSS는 false로 복원(이후 색 적용 고착 방지)
      document.execCommand("foreColor", false, col);
      document.querySelectorAll("#m-char .att").forEach(function (x) {
        var cmd = { bold:"bold", italic:"italic", underline:"underline", strike:"strikeThrough", superscript:"superscript", subscript:"subscript" }[x.dataset.att];
        if (x.classList.contains("on") !== document.queryCommandState(cmd)) document.execCommand(cmd, false, null);
      });
      if (!$("c-bg").dataset.none) document.execCommand("hiliteColor", false, $("c-bg").value);
    } finally { document.execCommand("styleWithCSS", false, false); }
    var sp = parseFloat($("c-spacing").value) || 0;
    var st = { fontFamily: fam, fontSize: pt + "pt" }; st.letterSpacing = sp ? (Math.round(sp * PXPT * 10) / 10 + "px") : "normal";   // 0 입력=자간 초기화(normal), 기존 자간이 남지 않게
    wrapStyle(st);
    closeModal($("m-char")); markDirty();
  }

  // 문단 모양
  function openPara() {
    saveRange(); var b = curBlock() || page, cs = getComputedStyle(b);
    var al = cs.textAlign; al = (al === "start" || al === "") ? "left" : al;
    document.querySelectorAll("#p-align button").forEach(function (x) { x.classList.toggle("on", x.dataset.al === al); });
    $("p-line").value = Math.round((parseFloat(cs.lineHeight) / (parseFloat(cs.fontSize) || 1)) * 100) || 160;
    $("p-ml").value = Math.round((parseFloat(cs.marginLeft) || 0) / PXPT); $("p-mr").value = Math.round((parseFloat(cs.marginRight) || 0) / PXPT);
    var ti = parseFloat(cs.textIndent) || 0;   // 첫 줄·문단 위/아래도 현재 블록에서 읽어 채움(재오픈 잔존값 방지)
    $("p-indkind").value = ti > 0 ? "1" : ti < 0 ? "-1" : "0";
    $("p-ind").value = Math.round(Math.abs(ti) / PXPT);
    $("p-st").value = Math.round((parseFloat(cs.marginTop) || 0) / PXPT); $("p-sb").value = Math.round((parseFloat(cs.marginBottom) || 0) / PXPT);
    openModal("m-para");
  }
  document.querySelectorAll("#p-align button").forEach(function (x) {
    x.addEventListener("pointerup", function () { document.querySelectorAll("#p-align button").forEach(function (y) { y.classList.remove("on"); }); x.classList.add("on"); });
  });
  function paraApply() {
    restoreRange();
    var al = (document.querySelector("#p-align button.on") || {}).dataset; al = al ? al.al : "left";
    var line = (parseInt($("p-line").value, 10) || 160) / 100, ml = parseInt($("p-ml").value, 10) || 0,
        mr = parseInt($("p-mr").value, 10) || 0, ind = parseInt($("p-ind").value, 10) || 0,
        kind = parseInt($("p-indkind").value, 10), st = parseInt($("p-st").value, 10) || 0, sb = parseInt($("p-sb").value, 10) || 0;
    selBlocks().forEach(function (b) {
      b.style.textAlign = al; b.style.lineHeight = line; b.style.marginLeft = Math.round(ml * PXPT) + "px"; b.style.marginRight = Math.round(mr * PXPT) + "px";
      b.style.textIndent = (kind === 0 ? 0 : Math.round(kind * ind * PXPT)) + "px"; b.style.marginTop = Math.round(st * PXPT) + "px"; b.style.marginBottom = Math.round(sb * PXPT) + "px";
    });
    closeModal($("m-para")); markDirty();
  }
  // 문단 서식 기본값 복원(Shift+Alt+C, 2026-07-13): 문단모양 7속성만 제거 → CSS 기본으로 복귀.
  //   글자서식·자간(letterSpacing, 글자레벨)·블록태그(제목 등, '스타일' 별개기능)는 불변 — '문단 기능 디폴트' 취지.
  function paraDefault() {
    var bs = selBlocks(); if (!bs.length) { toastMsg("문단에 커서를 두세요"); return; }
    bs.forEach(function (b) {
      b.style.textAlign = ""; b.style.lineHeight = ""; b.style.marginLeft = ""; b.style.marginRight = "";
      b.style.textIndent = ""; b.style.marginTop = ""; b.style.marginBottom = "";
      if (b.getAttribute("style") === "") b.removeAttribute("style");   // 빈 style 속성 잔재 정리
    });
    markDirty(); toastMsg("문단 기본값 복원");
  }

  // 편집 용지
  function openPage() {
    document.querySelectorAll("#pg-orient button").forEach(function (x) { x.classList.toggle("on", x.dataset.or === PAGECFG.orient); });
    $("pg-paper").value = PAGECFG.paper;
    $("pg-mt").value = num20(PAGECFG.mt); $("pg-mb").value = num20(PAGECFG.mb); $("pg-ml").value = num20(PAGECFG.ml); $("pg-mr").value = num20(PAGECFG.mr);
    syncPresetSel();
    var m = WP.readMeta ? WP.readMeta() : {};   // #26 쪽 테두리(wpmeta 저장 — 문서와 함께 왕복)
    $("pg-border").value = m.pageBorder || ""; $("pg-border-w").value = String(m.pageBorderW || 1);
    if (m.pageBgColor) { $("pg-bg").dataset.none = ""; if (WP.syncPalette) WP.syncPalette("pg-bg", m.pageBgColor); }   // 쪽 배경색(샘플)
    else { $("pg-bg").value = ""; $("pg-bg").dataset.none = "1"; $("pg-bg-cur").style.background = ""; var pp = document.querySelector('.cpalette[data-for="pg-bg"]'); if (pp) pp.querySelectorAll(".sw.sel").forEach(function (s) { s.classList.remove("sel"); }); }
    $("pg-bg-size").value = m.pageBgSize || "cover";
    $("pg-bg-clear").dataset.on = ""; $("pg-bg-img-st").textContent = m.pageBgImage ? "있음" : "없음";
    openModal("m-page");
  }
  document.querySelectorAll("#pg-orient button").forEach(function (x) {
    x.addEventListener("pointerup", function () { document.querySelectorAll("#pg-orient button").forEach(function (y) { y.classList.remove("on"); }); x.classList.add("on"); });
  });
  $("pg-bg-clear").addEventListener("pointerup", function () { this.dataset.on = "1"; $("pg-bg-img-st").textContent = "없음(적용 시)"; });
  $("pg-bg-none").addEventListener("pointerup", function () { $("pg-bg").value = ""; $("pg-bg").dataset.none = "1"; $("pg-bg-cur").style.background = ""; var pp = document.querySelector('.cpalette[data-for="pg-bg"]'); if (pp) pp.querySelectorAll(".sw.sel").forEach(function (s) { s.classList.remove("sel"); }); });
  // ── 여백 미리 설정(2026-11-13): 공문서(기안문) A4 = 위30·왼20·오15·아래15mm. 대화상자 선택 + 임베드 load 옵션(WP.setPaper) ──
  var MARGIN_PRESETS = { "default": { mt: 20, mr: 20, mb: 20, ml: 20 }, gov: { mt: 30, ml: 20, mr: 15, mb: 15 } };
  function syncPresetSel() {   // 여백 칸 값이 미리 설정과 같으면 그 이름 표시, 아니면 "직접 입력"
    var sel = $("pg-preset"); if (!sel) return;
    var v = { mt: +$("pg-mt").value, mr: +$("pg-mr").value, mb: +$("pg-mb").value, ml: +$("pg-ml").value }, hit = "";
    Object.keys(MARGIN_PRESETS).forEach(function (k) { var p = MARGIN_PRESETS[k]; if (p.mt === v.mt && p.mr === v.mr && p.mb === v.mb && p.ml === v.ml) hit = k; });
    sel.value = hit;
  }
  if ($("pg-preset")) {
    $("pg-preset").addEventListener("change", function () {
      var p = MARGIN_PRESETS[this.value]; if (!p) return;
      $("pg-mt").value = p.mt; $("pg-mb").value = p.mb; $("pg-ml").value = p.ml; $("pg-mr").value = p.mr;
      if (this.value === "gov") {   // 공문서 = A4 세로
        $("pg-paper").value = "A4";
        document.querySelectorAll("#pg-orient button").forEach(function (x) { x.classList.toggle("on", x.dataset.or === "portrait"); });
      }
    });
    ["pg-mt", "pg-mb", "pg-ml", "pg-mr"].forEach(function (id) { $(id).addEventListener("input", syncPresetSel); });
  }
  WP.MARGIN_PRESETS = MARGIN_PRESETS;
  // ── 용지 치수(HEAD fd4b780 복원 2026-07-06): 종이를 바꾸면 페이지 폭/높이·쪽 경계도 바뀜 ──
  var PAPERS = { A3: [297, 420], A4: [210, 297], A5: [148, 210], A6: [105, 148], B4: [257, 364], B5: [182, 257], B6: [128, 182], Letter: [215.9, 279.4], Legal: [215.9, 355.6], Tabloid: [279.4, 431.8] };
  function paperWH() {
    var d = PAPERS[PAGECFG.paper] || PAPERS.A4, land = PAGECFG.orient === "landscape";
    return land ? { w: d[1], h: d[0] } : { w: d[0], h: d[1] };
  }
  function applyPaper() {   // 선택 용지·방향을 페이지 요소 폭/높이에 반영(종이를 바꾸면 레이아웃도 바뀜)
    var wh = paperWH();
    page.style.width = wh.w + "mm"; page.style.minHeight = wh.h + "mm";
  }
  WP.paperRatio = function () {   // 본문 종횡비(content H/W, mm) — wp-page 페이지네이션이 용지별로 쪽 높이 산출
    var wh = paperWH(), ml = num20(PAGECFG.ml), mr = num20(PAGECFG.mr), mt = num20(PAGECFG.mt), mb = num20(PAGECFG.mb);
    var cw = wh.w - ml - mr, ch = wh.h - mt - mb;
    return (cw > 0 && ch > 0) ? ch / cw : 257 / 170;
  };
  // 용지 메타(2026-11-13): #wpmeta data-paper(이름)·data-orient·data-margins("위,오른쪽,아래,왼쪽" mm) — 문서와 함께 저장·왕복.
  //   기본값(A4 세로·여백 20)은 속성 생략. 서버(hwpxconv._meta_page/PAPERS_MM)와 같은 규칙·화이트리스트.
  function num20(v) { return (typeof v === "number" && isFinite(v)) ? v : 20; }
  function mmStr(x) { return String(Math.round(Math.max(0, Math.min(100, +x || 0)) * 10) / 10); }
  function metaPaper() {   // wpmeta → 검증된 용지 설정(허용 밖 값=기본)
    var el = page.querySelector("#wpmeta");
    function ga(k) { return el ? el.getAttribute("data-" + k) || "" : ""; }
    var p = ga("paper"), o = ga("orient"), mg = ga("margins");
    var c = { paper: Object.prototype.hasOwnProperty.call(PAPERS, p) ? p : "A4", orient: o === "landscape" ? "landscape" : "portrait", mt: 20, mr: 20, mb: 20, ml: 20 };
    if (/^\d{1,3}(\.\d)?(,\d{1,3}(\.\d)?){3}$/.test(mg)) {
      var a = mg.split(",").map(Number);
      if (a.every(function (x) { return x >= 0 && x <= 100; })) { c.mt = a[0]; c.mr = a[1]; c.mb = a[2]; c.ml = a[3]; }
    }
    return c;
  }
  function writePaperMeta(mEl) {   // 현재 편집 용지 → wpmeta(기본값이면 속성 제거)
    if (PAGECFG.paper !== "A4" && PAPERS[PAGECFG.paper]) mEl.setAttribute("data-paper", PAGECFG.paper); else mEl.removeAttribute("data-paper");
    if (PAGECFG.orient === "landscape") mEl.setAttribute("data-orient", "landscape"); else mEl.removeAttribute("data-orient");
    var mg = [PAGECFG.mt, PAGECFG.mr, PAGECFG.mb, PAGECFG.ml].map(mmStr);
    if (mg.join(",") !== "20,20,20,20") mEl.setAttribute("data-margins", mg.join(",")); else mEl.removeAttribute("data-margins");
  }
  WP.syncPaperMeta = function () {   // 문서 로드·되돌리기 등으로 wpmeta 용지가 바뀌면 편집 용지에 반영(같으면 무동작). wp-page paginate가 호출
    var c = metaPaper();
    if (c.paper === PAGECFG.paper && c.orient === PAGECFG.orient && c.mt === num20(PAGECFG.mt) && c.mr === num20(PAGECFG.mr) &&
        c.mb === num20(PAGECFG.mb) && c.ml === num20(PAGECFG.ml)) return false;
    PAGECFG.paper = c.paper; PAGECFG.orient = c.orient; PAGECFG.mt = c.mt; PAGECFG.mr = c.mr; PAGECFG.mb = c.mb; PAGECFG.ml = c.ml;
    $("pg-mt").value = c.mt; $("pg-mb").value = c.mb; $("pg-ml").value = c.ml; $("pg-mr").value = c.mr;
    if (c.paper === "A4" && c.orient === "portrait" && c.mt === 20 && c.mr === 20 && c.mb === 20 && c.ml === 20) {
      page.style.padding = ""; page.style.width = ""; page.style.minHeight = "";   // 기본 용지 = CSS 기본(모바일 반응형 유지)
    } else {
      page.style.padding = c.mt + "mm " + c.mr + "mm " + c.mb + "mm " + c.ml + "mm"; applyPaper();
    }
    return true;
  };
  WP.setPaper = function (cfg) {   // 프로그램 용지 설정(임베드 load 옵션). cfg = "gov" | {preset, paper, orient, mt, mr, mb, ml}. 허용 밖 값=무시(기본 유지)
    if (typeof cfg === "string") cfg = { preset: cfg };
    if (!cfg || typeof cfg !== "object") return false;
    var c = { paper: "A4", orient: "portrait", mt: 20, mr: 20, mb: 20, ml: 20 }, p = MARGIN_PRESETS[cfg.preset];
    if (p && Object.prototype.hasOwnProperty.call(MARGIN_PRESETS, cfg.preset)) { c.mt = p.mt; c.mr = p.mr; c.mb = p.mb; c.ml = p.ml; }
    if (typeof cfg.paper === "string" && Object.prototype.hasOwnProperty.call(PAPERS, cfg.paper)) c.paper = cfg.paper;
    if (cfg.orient === "landscape" || cfg.orient === "portrait") c.orient = cfg.orient;
    ["mt", "mr", "mb", "ml"].forEach(function (k) { var v = cfg[k]; if (typeof v === "number" && isFinite(v) && v >= 0 && v <= 100) c[k] = Math.round(v * 10) / 10; });
    var mEl = WP.wpmetaEl ? WP.wpmetaEl() : null; if (!mEl) return false;
    if (c.paper !== "A4") mEl.setAttribute("data-paper", c.paper); else mEl.removeAttribute("data-paper");
    if (c.orient === "landscape") mEl.setAttribute("data-orient", "landscape"); else mEl.removeAttribute("data-orient");
    var mg = [c.mt, c.mr, c.mb, c.ml].map(mmStr).join(",");
    if (mg !== "20,20,20,20") mEl.setAttribute("data-margins", mg); else mEl.removeAttribute("data-margins");
    WP.syncPaperMeta();
    if (WP.pageGuides) WP.pageGuides();
    return true;
  };
  WP.pageCfg = function () {   // 현재 용지 설정(이름·방향·여백 mm) — 임베드 {type:'html'} 응답에 동봉
    return { paper: PAGECFG.paper, orient: PAGECFG.orient, mt: num20(PAGECFG.mt), mr: num20(PAGECFG.mr), mb: num20(PAGECFG.mb), ml: num20(PAGECFG.ml) };
  };
  WP.paperInfo = function () {    // 용지 실치수·여백(mm) — 래스터 인쇄(wp-print)가 실제 용지비 캔버스 구성에 사용(#28)
    var wh = paperWH();
    return { w: wh.w, h: wh.h, ml: num20(PAGECFG.ml), mr: num20(PAGECFG.mr), mt: num20(PAGECFG.mt), mb: num20(PAGECFG.mb) };
  };
  function applyPageBg() {   // 쪽 배경(색·그림)을 #page에 반영 — 모든 페이지에 동일하게(페이지 높이만큼 반복). 메타 왕복
    var m = WP.readMeta ? WP.readMeta() : {};
    page.style.backgroundColor = m.pageBgColor || "";   // 빈값=CSS 기본(흰색)
    if (m.pageBgImage && window.WP.PAGEBG_IMG) {   // 쪽 배경그림 렌더(2026-07-18 활성). 플래그 WP.PAGEBG_IMG로 게이트(끄면 배경색만)
      var cs = getComputedStyle(page), padL = parseFloat(cs.paddingLeft) || 0, padR = parseFloat(cs.paddingRight) || 0, padT = parseFloat(cs.paddingTop) || 0;
      var contentW = page.clientWidth - padL - padR, pageH = Math.max(60, Math.round(contentW * (WP.paperRatio ? WP.paperRatio() : 257 / 170)));   // 한 페이지 콘텐츠 높이(px)
      page.style.backgroundImage = 'url("' + m.pageBgImage.replace(/"/g, "%22") + '")';
      page.style.backgroundAttachment = "local"; page.style.backgroundOrigin = "content-box"; page.style.backgroundClip = "border-box";
      if (m.pageBgSize === "repeat") { page.style.backgroundRepeat = "repeat"; page.style.backgroundSize = "auto"; page.style.backgroundPosition = "left top"; }   // 바둑판(원본 타일)
      else if (m.pageBgSize === "contain") { page.style.backgroundRepeat = "repeat-y"; page.style.backgroundSize = "auto " + pageH + "px"; page.style.backgroundPosition = "center top"; }   // 페이지 맞춤(비율유지)
      else { page.style.backgroundRepeat = "repeat-y"; page.style.backgroundSize = "100% " + pageH + "px"; page.style.backgroundPosition = "left top"; }   // 꽉 채움: 페이지마다 한 장씩
    } else { page.style.backgroundImage = ""; page.style.backgroundOrigin = ""; page.style.backgroundClip = ""; }
  }
  WP.applyPageBg = applyPageBg;
  function pageApply() {
    PAGECFG.paper = $("pg-paper").value;
    PAGECFG.orient = (document.querySelector("#pg-orient button.on") || {}).dataset ? document.querySelector("#pg-orient button.on").dataset.or : "portrait";
    var mIn = function (id) { var t = String($(id).value).trim(), v = +t; return (t === "" || !isFinite(v)) ? 20 : Math.max(0, Math.min(100, v)); };   // 빈칸·비숫자=기본 20mm, 0은 0 유지
    PAGECFG.mt = mIn("pg-mt"); PAGECFG.mb = mIn("pg-mb"); PAGECFG.ml = mIn("pg-ml"); PAGECFG.mr = mIn("pg-mr");
    page.style.padding = PAGECFG.mt + "mm " + PAGECFG.mr + "mm " + PAGECFG.mb + "mm " + PAGECFG.ml + "mm";
    if (WP.wpmetaEl) {   // #26 쪽 테두리 → wpmeta(문서 저장·PDF·미리보기 공유). 없음이면 속성 제거
      var mEl = WP.wpmetaEl(), pb = $("pg-border").value;
      writePaperMeta(mEl);   // 용지·방향·여백도 문서에 저장(다시 열면 복원)
      if (pb) { mEl.setAttribute("data-pageborder", pb); mEl.setAttribute("data-pageborder-w", $("pg-border-w").value); }
      else { mEl.removeAttribute("data-pageborder"); mEl.removeAttribute("data-pageborder-w"); }
      if ($("pg-bg").value && !$("pg-bg").dataset.none) mEl.setAttribute("data-pagebg-color", $("pg-bg").value); else mEl.removeAttribute("data-pagebg-color");   // 쪽 배경색(색상샘플)
      mEl.setAttribute("data-pagebg-size", $("pg-bg-size").value);
      if ($("pg-bg-clear").dataset.on) { mEl.removeAttribute("data-pagebg-image"); $("pg-bg-clear").dataset.on = ""; page.querySelectorAll("p.pagebg-cap").forEach(function (p) { p.parentNode.removeChild(p); }); page.querySelectorAll("[data-pagebg-src]").forEach(function (o) { o.removeAttribute("data-pagebg-src"); }); }   // 배경 그림 지우기(+라벨·표식 제거)
    }
    if (WP.applyPageBg) WP.applyPageBg();
    applyPaper();   // 용지 크기·방향을 페이지 폭/높이에 반영
    if (WP.pageGuides) WP.pageGuides();   // 새 용지 비율로 쪽 경계 재계산
    closeModal($("m-page")); toastMsg("편집 용지: " + PAGECFG.paper + " " + (PAGECFG.orient === "portrait" ? "세로" : "가로")); markDirty();
  }

  // #26-3 브라우저 직접 인쇄 — @page(용지·여백)를 현재 설정으로 동적 주입 + 쪽 테두리 fixed 동행.
  //   UI 숨김·스페이서 제거는 editor.css @media print. 머리말/쪽번호는 브라우저 인쇄가 못 그림 → PDF 저장 안내.
  function applyPrintRule() {
    var st = document.getElementById("printpage");
    if (!st) { st = document.createElement("style"); st.id = "printpage"; document.head.appendChild(st); }
    var wh = paperWH(), mt = num20(PAGECFG.mt), mr = num20(PAGECFG.mr), mb = num20(PAGECFG.mb), ml = num20(PAGECFG.ml);
    st.textContent = "@page{size:" + wh.w + "mm " + wh.h + "mm;margin:" + mt + "mm " + mr + "mm " + mb + "mm " + ml + "mm;}";
    var m = WP.readMeta ? WP.readMeta() : {}, pbEl = document.getElementById("printborder");
    if (m.pageBorder) {
      if (!pbEl) { pbEl = document.createElement("div"); pbEl.id = "printborder"; document.body.appendChild(pbEl); }
      pbEl.style.border = m.pageBorderW + "px " + m.pageBorder + " #445";
    } else if (pbEl) pbEl.parentNode.removeChild(pbEl);
  }
  window.addEventListener("beforeprint", applyPrintRule);   // 브라우저 자체 Ctrl+P 경로도 동일 준비
  function browserPrint() {
    applyPrintRule();
    toastMsg("브라우저 인쇄 — 머리말·쪽번호는 '인쇄 / PDF' 사용");
    window.print();
  }

  // 들여쓰기/내어쓰기(문단 왼쪽 여백 단계, 30px씩)
  function indentBlocks(delta) {
    selBlocks().forEach(function (b) { var cur = parseInt(b.style.marginLeft) || 0; b.style.marginLeft = Math.max(0, cur + delta) + "px"; });
    markDirty();
  }

  // 특수 문자 팔레트(#26: 분류 탭 + 최근 사용. 기존 90자 유지 + 카테고리별 보강)
  var SYMCATS = [
    ["일반", "… · • ※ ★ ☆ ○ ● ◎ ◇ ◆ □ ■ △ ▲ ▽ ▼ ♠ ♥ ♦ ♣ ♪ ♬ ☀ ☁ ☂ ☎ ✉ ✓ ✔ ✗ © ® ™ § ¶ † ‡ ☞ ☜"],
    ["화살표", "→ ← ↑ ↓ ↔ ↕ ↗ ↘ ↖ ↙ ⇒ ⇐ ⇑ ⇓ ⇔ ⇕ ▶ ◀ ↩ ↪ ↺ ↻ ⇄ ⇅"],
    ["괄호", "« » 〈 〉 《 》 「 」 『 』 【 】 〔 〕 ‘ ’ “ ” ˝ ‥ ― ∼"],
    ["원문자", "① ② ③ ④ ⑤ ⑥ ⑦ ⑧ ⑨ ⑩ ⑪ ⑫ ⑬ ⑭ ⑮ ⑴ ⑵ ⑶ ⑷ ⑸ ㉠ ㉡ ㉢ ㉣ ㉤ ㉥ ㉦ ㉧ ㉨ ㉩ ㉪ ㉫ ㉬ ㉭ ⓐ ⓑ ⓒ ⓓ ⓔ ㈀ ㈁ ㈂ ㈃ ㈄"],
    ["수학", "± × ÷ ≠ ≤ ≥ ∞ ∑ √ ∂ ∫ ≒ ≈ ≡ ∴ ∵ ∝ ∈ ∋ ⊂ ⊃ ∪ ∩ ∧ ∨ ¬ ∀ ∃ ⊥ ∠ ⊕ ⊗ ° ′ ″ ‰"],
    ["단위·통화", "℃ ℉ ㎜ ㎝ ㎞ ㎟ ㎠ ㎡ ㎢ ㎣ ㎤ ㎥ ㎎ ㎏ ㎕ ㎖ ㎗ ℓ ㏊ ㎈ ㎉ № ₩ $ € £ ¥ ¢ ￦"],
    ["그리스·로마", "α β γ δ ε ζ η θ λ μ ν ξ π ρ σ τ φ χ ψ ω Γ Δ Θ Λ Ξ Π Σ Φ Ψ Ω Ⅰ Ⅱ Ⅲ Ⅳ Ⅴ Ⅵ Ⅶ Ⅷ Ⅸ Ⅹ ⅰ ⅱ ⅲ ⅳ ⅴ"],
    ["한글 자모", "ㄱ ㄲ ㄳ ㄴ ㄵ ㄶ ㄷ ㄸ ㄹ ㅁ ㅂ ㅃ ㅄ ㅅ ㅆ ㅇ ㅈ ㅉ ㅊ ㅋ ㅌ ㅍ ㅎ ㅏ ㅐ ㅑ ㅒ ㅓ ㅔ ㅕ ㅖ ㅗ ㅘ ㅙ ㅚ ㅛ ㅜ ㅝ ㅞ ㅟ ㅠ ㅡ ㅢ ㅣ"]
  ].map(function (c) { return { name: c[0], chars: c[1].split(/\s+/).filter(Boolean) }; });
  var SYM_RECENT_KEY = "wp.symRecent", SYM_RECENT_MAX = 24;
  function symRecent() { try { return JSON.parse(localStorage.getItem(SYM_RECENT_KEY)) || []; } catch (e) { return []; } }
  function symbolInsert(ch) {
    closeModal($("m-symbol")); restoreRange(); document.execCommand("insertText", false, ch); markDirty();
    try { var r = symRecent().filter(function (c) { return c !== ch; }); r.unshift(ch); localStorage.setItem(SYM_RECENT_KEY, JSON.stringify(r.slice(0, SYM_RECENT_MAX))); } catch (e) {}
  }
  function symBuild(cat) {   // cat: -1=최근, 0..n=SYMCATS
    var g = $("symgrid"); g.innerHTML = "";
    (cat < 0 ? symRecent() : SYMCATS[cat].chars).forEach(function (ch) {
      var b = document.createElement("button"); b.className = "symbtn"; b.textContent = ch;
      b.addEventListener("pointerup", function () { symbolInsert(ch); }); g.appendChild(b);
    });
    document.querySelectorAll("#symtabs .symtab").forEach(function (t) { t.classList.toggle("on", +t.dataset.cat === cat); });
  }
  function openSymbol() {
    saveRange(); var tabs = $("symtabs");
    if (!tabs.dataset.built) {
      [{ name: "최근", cat: -1 }].concat(SYMCATS.map(function (c, i) { return { name: c.name, cat: i }; })).forEach(function (t) {
        var b = document.createElement("button"); b.className = "symtab"; b.dataset.cat = t.cat; b.textContent = t.name;
        b.addEventListener("pointerup", function () { symBuild(t.cat); }); tabs.appendChild(b);
      });
      tabs.dataset.built = "1";
    }
    symBuild(symRecent().length ? -1 : 0);   // 최근 사용이 있으면 최근 탭, 없으면 일반
    openModal("m-symbol");
  }
  // #26-4 목록 속성 — 캐럿이 속한 ol/ul의 스타일(번호 표기·글머리표 모양)·시작번호
  var LS_OL = [["decimal", "1, 2, 3"], ["lower-alpha", "a, b, c"], ["upper-alpha", "A, B, C"], ["lower-roman", "i, ii, iii"], ["upper-roman", "Ⅰ, Ⅱ, Ⅲ"]],
      LS_UL = [["disc", "● 검정 원"], ["circle", "○ 흰 원"], ["square", "■ 네모"]];
  if (window.CSS && CSS.supports && CSS.supports("list-style-type", "hangul")) LS_OL.push(["hangul", "가, 나, 다"]);   // WebKit 미지원 대비 기능탐지
  function caretList() {
    var s = window.getSelection(), n = s && s.anchorNode;
    if (!n) return null;
    n = n.nodeType === 1 ? n : n.parentNode;
    if (!n || !page.contains(n)) return null;
    var l = n.closest && n.closest("ol,ul");
    return (l && page.contains(l)) ? l : null;
  }
  function openListDlg() {
    saveRange();
    var l = caretList();
    if (!l) { toastMsg("커서를 목록(글머리표·번호) 안에 두세요"); return; }
    var isOl = l.tagName === "OL", sel = $("ls-type");
    sel.innerHTML = "";
    (isOl ? LS_OL : LS_UL).forEach(function (o) { var op = document.createElement("option"); op.value = o[0]; op.textContent = o[1]; sel.appendChild(op); });
    sel.value = l.style.listStyleType || (isOl ? "decimal" : "disc");
    if (sel.selectedIndex < 0) sel.selectedIndex = 0;   // 중첩 CSS 기본값 등 목록 밖 값
    $("ls-startrow").style.display = isOl ? "" : "none";
    $("ls-start").value = l.getAttribute("start") || "1";
    openModal("m-list");
  }
  function listApply() {
    closeModal($("m-list")); restoreRange();
    var l = caretList(); if (!l) return;
    l.style.listStyleType = $("ls-type").value;
    if (l.tagName === "OL") { var st = parseInt($("ls-start").value, 10); if (st > 1) l.setAttribute("start", st); else l.removeAttribute("start"); }
    toastMsg("목록 속성 적용"); markDirty();
  }

  // 스타일(F6): 대화상자에서 문단 스타일 적용
  function openStyleDlg() { saveRange(); openModal("m-style"); }
  document.querySelectorAll("#m-style [data-block]").forEach(function (b) {
    b.addEventListener("pointerup", function () { restoreRange(); exec("formatBlock", b.dataset.block === "P" ? "P" : b.dataset.block); closeModal($("m-style")); });
  });

  // ── 모양 복사(Alt+C): 현재 서식 캡처 → 다음 드래그 선택에 적용 (HWP식) ──
  var paintStyle = null;
  function captureStyle() {
    var s = window.getSelection(); if (!s.rangeCount) return null;
    var n = s.anchorNode; if (n && n.nodeType === 3) n = n.parentNode;
    if (!n || !page.contains(n)) return null;
    var cs = getComputedStyle(n), td = cs.textDecorationLine || cs.textDecoration || "";
    return { fontWeight: cs.fontWeight, fontStyle: cs.fontStyle, color: cs.color, fontSize: cs.fontSize,
      fontFamily: cs.fontFamily, letterSpacing: cs.letterSpacing,
      textDecoration: /underline|line-through/.test(td) ? td.split(" ")[0] : "none" };
  }
  function formatCopy() { paintStyle = captureStyle(); if (paintStyle) toastMsg("모양 복사 — 적용할 곳을 드래그"); else toastMsg("복사할 글자에 커서를 두세요"); }
  function applyPaint() {
    if (!paintStyle) return;
    var s = window.getSelection(); if (!s.rangeCount || s.isCollapsed || !page.contains(s.anchorNode)) return;
    wrapStyle(paintStyle); paintStyle = null; markDirty(); toastMsg("모양 적용됨");
  }
  document.addEventListener("pointerup", function () { if (paintStyle) setTimeout(applyPaint, 0); });

  // ── 줄 간격 ±0.2 (Alt+Shift+A 넓게 / Alt+Shift+Z 좁게) ──
  function lineStep(d) {
    selBlocks().forEach(function (b) {
      var cs = getComputedStyle(b), lh = parseFloat(cs.lineHeight) / parseFloat(cs.fontSize);
      if (!isFinite(lh)) lh = 1.6;
      b.style.lineHeight = Math.max(1, Math.min(3, lh + d)).toFixed(2);
    });
    markDirty();
  }

  function setIndentPx(px) { selBlocks().forEach(function (b) { b.style.marginLeft = Math.max(0, Math.round(px)) + "px"; }); markDirty(); }
  function curIndentPx() { var b = curBlock(); return b ? (parseFloat(getComputedStyle(b).marginLeft) || 0) : 0; }
  WP.setIndent = setIndentPx; WP.curIndent = curIndentPx;

  // ── 추가 HWP 단축키 동작 ──
  function letterStep(d) {                                   // 자간 ±0.5px (Alt+Shift+W/N)
    // curRun(선택 span)에서 읽어야 누적됨 — curBlock(문단)은 늘 normal(0)이라 매번 ±0.5 절대세팅=첫 압만 보임(2026-07-13 fix)
    var b = curRun() || page, cur = parseFloat(getComputedStyle(b).letterSpacing) || 0;
    wrapStyle({ letterSpacing: (Math.round((cur + d) * 10) / 10) + "px" }); markDirty();
  }
  function firstLineStep(d) {                                // 첫 줄 들여/내어쓰기 text-indent ±10px (Ctrl+Shift+I/O)
    selBlocks().forEach(function (b) { var cur = parseInt(b.style.textIndent) || 0; b.style.textIndent = (cur + d) + "px"; }); markDirty();
  }
  function marginRStep(d) {                                  // 오른쪽 여백 ±10px (Ctrl+Shift+D/F)
    selBlocks().forEach(function (b) { var cur = parseInt(b.style.marginRight) || 0; b.style.marginRight = Math.max(0, cur + d) + "px"; }); markDirty();
  }
  function caretMove(dir) {                                  // 문단 처음/끝 이동 (Alt+Home/End)
    var s = window.getSelection(); if (s && s.modify) { try { s.modify("move", dir, "paragraphboundary"); page.focus(); } catch (e) {} }
  }
  var blockState = 0;                                        // F3 블록 설정: 글자→낱말→문단→전체 순환
  function blockCycle() {
    var s = window.getSelection(); if (!s.rangeCount || !page.contains(s.anchorNode)) return;
    blockState = (blockState % 4) + 1;
    try {
      if (blockState === 1) { s.collapseToStart(); s.modify("extend", "forward", "character"); }
      else if (blockState === 2) { s.modify("move", "backward", "word"); s.modify("extend", "forward", "word"); }
      else if (blockState === 3) { s.modify("move", "backward", "paragraphboundary"); s.modify("extend", "forward", "paragraphboundary"); }
      else { var r = document.createRange(); r.selectNodeContents(page); s.removeAllRanges(); s.addRange(r); blockState = 0; }
    } catch (e) {}
    page.focus();
  }
  page.addEventListener("pointerdown", function () { blockState = 0; });   // 클릭하면 블록 상태 리셋
  function setColor(c) { document.execCommand("styleWithCSS", false, true); try { exec("foreColor", c); } finally { document.execCommand("styleWithCSS", false, false); } }

  // ── 하이퍼링크(#20) ──
  var _lkEdit = null;   // 편집 대상 <a>(신규 삽입이면 null)
  function curLink() {
    var s = window.getSelection(); if (!s.rangeCount) return null;
    var n = s.anchorNode; if (n && n.nodeType === 3) n = n.parentNode;
    return (n && n.closest && page.contains(n)) ? n.closest("a") : null;
  }
  function normLinkUrl(raw) {   // 스킴 화이트리스트: http/https/mailto/앵커(#)/상대만. 위험 스킴 거부(null)
    var u = String(raw || "").trim(); if (!u) return null;
    var low = u.replace(/[\s\x00-\x1f]+/g, "").toLowerCase();
    if (/^(javascript|vbscript|data|file):/.test(low)) return null;
    if (/^(https?:|mailto:|tel:|#|\/|\.\/|\.\.\/)/.test(low)) return u;
    if (/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(u)) return "mailto:" + u;            // 이메일 → mailto 보정
    if (/^[\w.-]+\.[a-z]{2,}([\/?#].*)?$/i.test(u)) return "https://" + u;     // 스킴 없는 도메인 → https 보정
    return null;
  }
  function decorate(a, url) { a.setAttribute("href", url); a.setAttribute("rel", "noopener noreferrer"); a.setAttribute("title", url); }
  function openLink() {
    saveRange();
    var a = curLink(); _lkEdit = a;
    var s = window.getSelection();
    var selText = (s && s.rangeCount) ? String(s.getRangeAt(0).toString()) : "";
    $("lk-url").value = a ? (a.getAttribute("href") || "") : "";
    $("lk-text").value = a ? a.textContent : selText;
    $("m-link-rm").style.display = a ? "" : "none";
    $("m-link-h").firstChild.nodeValue = a ? "하이퍼링크 편집" : "하이퍼링크";
    openModal("m-link");
  }
  function linkApply() {
    var url = normLinkUrl($("lk-url").value), text = $("lk-text").value;
    if (!url) { toastMsg("URL이 올바르지 않습니다 (http/https/mailto만)"); return; }
    closeModal($("m-link"));
    if (_lkEdit) {   // 기존 링크 편집: href·표시문구 갱신
      decorate(_lkEdit, url);
      if (text && text !== _lkEdit.textContent) _lkEdit.textContent = text;
      _lkEdit = null; page.focus(); markDirty(); toastMsg("링크 수정됨"); return;
    }
    restoreRange();
    var s = window.getSelection(), a = document.createElement("a"); decorate(a, url);
    if (s.rangeCount && !s.getRangeAt(0).collapsed && !text) {   // 선택영역을 그대로 래핑(표시문구 미지정 시 선택 텍스트 유지)
      var range = s.getRangeAt(0);
      try { range.surroundContents(a); }
      catch (e) { var frag = range.extractContents(); a.appendChild(frag); range.insertNode(a); }
    } else {
      a.textContent = text || url;
      if (s.rangeCount) { var r2 = s.getRangeAt(0); r2.deleteContents(); r2.insertNode(a); }
      else page.appendChild(a);
    }
    try { var nr = document.createRange(); nr.setStartAfter(a); nr.collapse(true); s.removeAllRanges(); s.addRange(nr); } catch (x) {}   // 캐럿을 링크 뒤로(연속 타이핑은 링크 밖)
    page.focus(); markDirty(); toastMsg("링크 적용: " + url);
  }
  function linkRemove() {
    closeModal($("m-link"));
    var a = _lkEdit || curLink(); _lkEdit = null;
    if (!a) { toastMsg("제거할 링크가 없습니다"); return; }
    var parent = a.parentNode;
    while (a.firstChild) parent.insertBefore(a.firstChild, a);
    parent.removeChild(a); page.focus(); markDirty(); toastMsg("링크 제거됨");
  }
  // Ctrl/⌘+클릭으로 링크 열기(편집 중 일반 클릭은 캐럿만 — 예측가능성)
  page.addEventListener("click", function (e) {
    var a = e.target && e.target.closest ? e.target.closest("a") : null;
    if (a && (e.ctrlKey || e.metaKey)) { var href = a.getAttribute("href"); if (href) { e.preventDefault(); window.open(href, "_blank", "noopener,noreferrer"); } }
  });

  WP.register({
    charDlg: openChar, paraDlg: openPara, pageDlg: openPage, style: openStyleDlg, symbol: openSymbol,
    superscript: function () { exec("superscript"); }, subscript: function () { exec("subscript"); },
    formatCopy: formatCopy, lineWider: function () { lineStep(0.2); }, lineNarrower: function () { lineStep(-0.2); },
    bold: function () { exec("bold"); }, italic: function () { exec("italic"); }, underline: function () { exec("underline"); }, strike: function () { exec("strikeThrough"); },
    bigger: function () { stepFont(1); }, smaller: function () { stepFont(-1); },
    justifyLeft: function () { if (WP.alignTableIfSelected && WP.alignTableIfSelected("left")) return; exec("justifyLeft"); },   // 표 전체선택=표 정렬, 셀 안=셀 정렬(HWP)
    justifyCenter: function () { if (WP.alignTableIfSelected && WP.alignTableIfSelected("center")) return; exec("justifyCenter"); },
    justifyRight: function () { if (WP.alignTableIfSelected && WP.alignTableIfSelected("right")) return; exec("justifyRight"); }, justifyFull: function () { exec("justifyFull"); },
    insertUnorderedList: function () { exec("insertUnorderedList"); }, insertOrderedList: function () { exec("insertOrderedList"); },
    indentMore: function () { indentBlocks(30); }, indentLess: function () { indentBlocks(-30); },
    styleP: function () { exec("formatBlock", "P"); }, styleH1: function () { exec("formatBlock", "H1"); },
    styleH2: function () { exec("formatBlock", "H2"); }, styleH3: function () { exec("formatBlock", "H3"); },
    pagebreak: function () {   // 쪽 나눔 후 새 쪽 시작이 보이게 스크롤+안내(2026-07-19 — 점선 경계만이라 '안 된 줄' 오인)
      document.execCommand("insertHTML", false, '<div class="pgbreak" contenteditable="false"><br></div><p><br></p>');   // 비편집: 채움영역에 글 못 쓰게(2026-07-19)
      markDirty();
      setTimeout(function () {   // 페이지네이션(250ms 디바운스) 후 캐럿 위치로 스크롤
        try {
          var s = window.getSelection(), n = s && s.anchorNode;
          if (n) { n = n.nodeType === 1 ? n : n.parentNode; n.scrollIntoView({ block: "center" }); }
        } catch (e) {}
        toastMsg("쪽 나눔 — 다음 쪽 시작(점선 경계 아래). 종이 모양은 미리보기·전체보기에서");
      }, 400);
    },
    letterWider: function () { letterStep(0.5); }, letterNarrower: function () { letterStep(-0.5); },
    firstIndent: function () { firstLineStep(10); }, firstOutdent: function () { firstLineStep(-10); },
    marginLInc: function () { indentBlocks(10); }, marginLDec: function () { indentBlocks(-10); },
    marginRInc: function () { marginRStep(10); }, marginRDec: function () { marginRStep(-10); },
    paraHome: function () { caretMove("backward"); }, paraEnd: function () { caretMove("forward"); },
    blockCycle: blockCycle,
    colorBlack: function () { setColor("#000000"); }, colorRed: function () { setColor("#cc0000"); }, colorBlue: function () { setColor("#0000cc"); },
    charApply: charApply, paraApply: paraApply, paraDefault: paraDefault, pageApply: pageApply, browserPrint: browserPrint,
    listDlg: openListDlg, listApply: listApply,
    link: openLink, linkApply: linkApply, linkRemove: linkRemove
  });
})();
