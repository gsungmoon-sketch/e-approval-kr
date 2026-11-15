// 한워드 — 도구 모음 모양: 한글97식(기본) / 기존. 보기 메뉴·localStorage("hw.toolbar")로 전환.
//   한글97식 = 표준 막대(새 문서·불러오기·저장 | 인쇄·미리 보기 | 오려 두기·복사·붙이기·모양 복사 | 되돌리기·다시 실행 | 찾기 | 표·그림·그리기·쪽 나누기)
//            + 서식 막대(스타일·글꼴·크기 | 진하게·기울임·밑줄·글자 색 | 정렬 4 | 줄 간격 | 문단 번호·글머리표 | 내어쓰기·들여쓰기 | 글자·문단 모양)
//            + 메뉴 파일·편집·보기·입력·모양·도구·표·도움말.
//   기존 버튼 노드를 그대로 옮겨 쓴다(리스너·id·.on 동기화 유지) — 동작은 전부 기존 ACT(WP.dispatch). 아이콘은 자체 SVG.
//   메뉴 항목은 모드(공개·임베드)가 이미 걸러 둔 기존 메뉴(WP.baseMenus)에서 골라 쓰므로 공개·임베드 감춤 규칙이 그대로 따라온다.
(function () {
  "use strict";
  var WP = window.WP, $ = WP.$, EMBED = WP.embed;
  var KEY = "hw.toolbar";
  var tb = $("toolbar");
  if (!tb || !WP.setMenus) return;

  // ── 아이콘(자체 제작 SVG, 16×16 선 그림) ──
  var P = {
    "new": '<path d="M3.5 1.5h6l3 3v10h-9z"/><path d="M9.5 1.5v3h3"/>',
    open: '<path d="M1.5 13.5v-10h4l1.2 1.5h5.8v2"/><path d="M1.5 13.5l2.3-6h11l-2.3 6z"/>',
    save: '<rect x="2" y="2" width="12" height="12" rx="1"/><path d="M5 2v4h6V2"/><rect x="4.5" y="9" width="7" height="5"/>',
    print: '<path d="M4.5 6V2h7v4"/><rect x="1.5" y="6" width="13" height="5.5" rx="1"/><path d="M4.5 9.5h7v5h-7z"/>',
    preview: '<path d="M8 14.5H2.5v-13h6l2.5 2.5V7"/><circle cx="10.5" cy="10.5" r="2.6"/><path d="M12.4 12.4l2.3 2.3"/>',
    cut: '<circle cx="4.5" cy="12" r="2.2"/><circle cx="11.5" cy="12" r="2.2"/><path d="M6 10.3L12 1.5M10 10.3L4 1.5"/>',
    copy: '<rect x="5.5" y="5" width="8.5" height="9.5" rx="1"/><path d="M3 11.5v-9a1 1 0 0 1 1-1h7"/>',
    paste: '<rect x="2.5" y="2.5" width="11" height="12" rx="1"/><rect x="5.5" y="1" width="5" height="3" rx=".8"/><path d="M5 8h6M5 11h6"/>',
    brush: '<path d="M2.5 2h9v4h-9z"/><path d="M11.5 4h2v3.5H7.5V10"/><rect x="6.5" y="10" width="2" height="4.5" rx=".6"/>',
    undo: '<path d="M5.5 2.5L2.5 5.5l3 3"/><path d="M2.5 5.5h7a4 4 0 0 1 0 8H6"/>',
    redo: '<path d="M10.5 2.5l3 3-3 3"/><path d="M13.5 5.5h-7a4 4 0 0 0 0 8H10"/>',
    find: '<circle cx="6.5" cy="6.5" r="4.5"/><path d="M10 10l4.5 4.5"/>',
    table: '<rect x="1.5" y="2.5" width="13" height="11"/><path d="M1.5 6.2h13M1.5 9.8h13M5.8 2.5v11M10.2 2.5v11"/>',
    image: '<rect x="1.5" y="2.5" width="13" height="11" rx="1"/><circle cx="5.5" cy="6" r="1.3"/><path d="M1.5 12l4-4 3 3 2-2 4 4"/>',
    shape: '<rect x="1.5" y="6.5" width="7" height="7.5"/><circle cx="10.5" cy="5.5" r="4"/>',
    pgbreak: '<path d="M3 1.5v4.5h10V1.5"/><path d="M3 14.5V10h10v4.5"/><path d="M1 8h2M5 8h2M9 8h2M13 8h2"/>',
    alignL: '<path d="M2 3h12M2 6.3h8M2 9.6h12M2 12.9h8"/>',
    alignC: '<path d="M2 3h12M4 6.3h8M2 9.6h12M4 12.9h8"/>',
    alignR: '<path d="M2 3h12M6 6.3h8M2 9.6h12M6 12.9h8"/>',
    alignJ: '<path d="M2 3h12M2 6.3h12M2 9.6h12M2 12.9h12"/>',
    bullets: '<circle cx="3" cy="4" r="1.1" fill="currentColor"/><circle cx="3" cy="8" r="1.1" fill="currentColor"/><circle cx="3" cy="12" r="1.1" fill="currentColor"/><path d="M6 4h8.5M6 8h8.5M6 12h8.5"/>',
    numbers: '<path d="M2 2.6l1.3-.9v4.6"/><path d="M1.7 9.2a1.2 1.2 0 0 1 2.3.4c0 .8-2.3 2-2.3 3.2h2.4"/><path d="M6.5 4h8M6.5 11.5h8"/>',
    outdent: '<path d="M7 3h7M7 6.5h7M7 10h7M2 13.5h12"/><path d="M5 4.5L2 6.5l3 2"/>',
    indent: '<path d="M7 3h7M7 6.5h7M7 10h7M2 13.5h12"/><path d="M2 4.5l3 2-3 2"/>',
    para: '<path d="M10 2v12.5M12.5 2v12.5M13.5 2H7.5a3 3 0 0 0 0 6H10"/>',
    chr: '<path d="M2 14L6 2.5h1L11 14M3.3 10.3h6.4"/><path d="M11.5 14V7.5M11.5 10.5h3"/>'
  };
  function svg(k) { return '<svg class="tb-ic" viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round">' + P[k] + '</svg>'; }

  // ── 메뉴 ──
  function flat(menus) { var o = {}; menus.forEach(function (m) { (m.items || []).forEach(function (it) { if (it !== "-") o[it[2]] = it; }); }); return o; }
  var BASE = WP.baseMenus(), HAVE = flat(BASE);
  function layoutItems(cur) {
    return ["-", ["도구 모음: 한글97식", cur === "97" ? "●" : "", "tbLayout97"], ["도구 모음: 기존", cur === "classic" ? "●" : "", "tbLayoutClassic"]];
  }
  function menusClassic() {   // 기존 메뉴 그대로 + 보기 끝에 도구 모음 전환 2항목
    return BASE.map(function (m) { return { name: m.name, items: m.name === "보기" ? m.items.concat(layoutItems("classic")) : m.items.slice() }; });
  }
  function menus97() {
    function pick(act, label) { var it = HAVE[act]; return it ? [label || it[0], it[1], act] : null; }   // 기존 메뉴에 있는(=이 모드에서 허용된) 항목만
    function menu(name, items) {
      var out = [];
      items.forEach(function (it) {
        if (!it) return;
        if (it === "-") { if (out.length && out[out.length - 1] !== "-") out.push("-"); return; }
        out.push(it);
      });
      while (out.length && out[out.length - 1] === "-") out.pop();
      return out.length ? { name: name, items: out } : null;
    }
    var fileM = BASE.filter(function (m) { return m.name === "파일"; })[0];
    var T = function (label, hk, act) { return [label, hk, act]; };   // 이 모듈·표 모듈이 언제나 등록하는 항목
    return [
      fileM ? { name: "파일", items: fileM.items.slice() } : null,
      menu("편집", [pick("undo"), pick("redo"), "-", pick("cut"), pick("copy"), pick("paste"), T("모양 복사", "Alt+C", "formatCopy"), "-",
        pick("selectall"), pick("find"), pick("replace")]),
      menu("보기", [pick("fullscreen", "미리 보기 (전체 화면)"), pick("fullview"), pick("outline"), pick("pageguide")].concat(layoutItems("97"))),
      menu("입력", [pick("image"), pick("media"), T("그리기 (사각형·원·선)", "", "shapeBox"), "-",
        pick("symbol"), pick("link"), pick("hr"), pick("pagebreak"), "-",
        pick("caption"), pick("captionRenum"), "-",
        pick("fnInsert") && T("미주 삽입", "Ctrl+N,E", "fnInsert"), pick("fnRenum")]),
      menu("모양", [pick("charDlg"), pick("paraDlg"), pick("paraDefault"), pick("style"), "-",
        pick("bold"), pick("italic"), pick("underline"), pick("strike"), "-", pick("bigger"), pick("smaller"), "-",
        pick("justifyFull"), pick("justifyLeft"), pick("justifyCenter"), pick("justifyRight"), "-",
        pick("listDlg", "문단 번호/글머리표 모양…"), "-",
        pick("pageDlg"), pick("header"), pick("pagenum"), pick("objToBg")]),
      menu("도구", [pick("spell"), pick("wordcount"), pick("tocGen", "차례 만들기 (현재 쪽 기준)"), "-", pick("histpw"), "-", pick("board")]),
      menu("표", [pick("table", "표 만들기…"), "-",
        T("위에 줄 추가", "", "tblRowAbove"), T("아래에 줄 추가", "", "tblRowBelow"), T("왼쪽에 칸 추가", "", "tblColLeft"), T("오른쪽에 칸 추가", "", "tblColRight"),
        T("줄 지우기", "", "tblDelRow"), T("칸 지우기", "", "tblDelCol"), "-",
        T("셀 합치기", "", "tblMerge"), T("셀 나누기…", "", "tblSplit"), T("셀 테두리…", "", "tblBorder"), pick("formula"), "-",
        T("표 붙이기 (아래 표와)", "", "tblJoin"), T("표 나누기", "", "tblSplitTable"), "-", T("표 지우기", "", "tblDelete")]),
      menu("도움말", [T("단축키 목록", "F1", "help"), EMBED ? null : T("사용 안내서", "", "guide")])
    ].filter(Boolean);
  }

  // ── 표 메뉴 동작 = 표 도구 막대(WP.TT)와 같은 함수 ──
  function inTable() {
    if (WP.selectedTable && WP.selectedTable()) return true;
    var s = window.getSelection(), n = s && s.rangeCount ? s.anchorNode : null;
    if (n && n.nodeType === 3) n = n.parentNode;
    return !!(n && n.closest && WP.page.contains(n) && n.closest("td,th"));
  }
  function tt(k) { return function () { if (!inTable()) { WP.toast("표 안에 커서를 두세요"); return; } var f = WP.TT && WP.TT[k]; if (f) f(); }; }

  // ── 도움말(단축키 목록) — 현재 메뉴의 단축키 + 메뉴에 없는 고전 키. 전부 textContent(주입 없음) ──
  var EXTRA_KEYS = [["메뉴 막대로 이동", "F10"], ["도움말", "F1"], ["글자 색 검정·빨강·파랑", "Ctrl+M,K / R / B"], ["미주 삽입", "Ctrl+N,E"],
    ["줄 간격 넓게 / 좁게", "Alt+Shift+Z / Alt+Shift+A"], ["글자 크게 / 작게", "Ctrl+] / Ctrl+["], ["다시 찾기", "Ctrl+L"], ["블록 넓히기", "F3"]];
  function openHelp() {
    var body = $("help-body"); if (!body) return;
    body.textContent = "";
    var tbl = document.createElement("table"); tbl.className = "help-keys";
    function row(a, b, head) {
      var tr = document.createElement("tr"), td1 = document.createElement(head ? "th" : "td"), td2 = document.createElement(head ? "th" : "td");
      td1.textContent = a; td2.textContent = b; tr.appendChild(td1); tr.appendChild(td2); tbl.appendChild(tr);
      if (head) td2.textContent = "";
    }
    WP.menus().forEach(function (m) {
      var rows = (m.items || []).filter(function (it) { return it !== "-" && it[1] && it[1] !== "●"; });
      if (!rows.length) return;
      row(m.name, "", true);
      rows.forEach(function (it) { row(it[0], it[1]); });
    });
    row("그 밖의 키", "", true);
    EXTRA_KEYS.forEach(function (k) { row(k[0], k[1]); });
    body.appendChild(tbl);
    WP.openModal("m-help");
  }

  // ── 도구 모음 구성 ──
  function mk(tag, cls, attrs) { var e = document.createElement(tag); if (cls) e.className = cls; for (var k in attrs || {}) e.setAttribute(k, attrs[k]); return e; }
  var ORIG = Array.prototype.slice.call(tb.childNodes);   // 기존 배치(되돌리기용, 공백 텍스트 노드 포함)
  function orig(act) { return tb.querySelector('[data-act="' + act + '"]'); }
  var O = {};
  ["formatCopy", "shapeBox", "bold", "italic", "underline", "justifyLeft", "justifyCenter", "justifyRight", "justifyFull",
   "insertUnorderedList", "insertOrderedList", "indentLess", "indentMore", "charDlg", "paraDlg"].forEach(function (a) { O[a] = orig(a); });
  var SEL = { style: $("styleSel"), font: $("fontSel"), size: $("sizeSel") };
  var SAVED = [];   // 기존 노드의 title/내용(기존 모양 복원)
  ORIG.forEach(function (n) { if (n.nodeType === 1) SAVED.push([n, n.getAttribute("title"), n.tagName === "BUTTON" ? n.innerHTML : null]); });

  // 한글97식에서 기존 노드에 입힐 아이콘·풍선 도움말
  var DECO = {
    formatCopy: ["brush", "모양 복사 (Alt+C)"], shapeBox: ["shape", "그리기 — 사각형·원·선"],
    bold: [null, "진하게 (Ctrl+B)"], italic: [null, "기울임 (Ctrl+I)"], underline: [null, "밑줄 (Ctrl+U)"],
    justifyFull: ["alignJ", "양쪽 정렬 (Ctrl+Shift+M)"], justifyLeft: ["alignL", "왼쪽 정렬 (Ctrl+Shift+L)"],
    justifyCenter: ["alignC", "가운데 정렬 (Ctrl+Shift+C)"], justifyRight: ["alignR", "오른쪽 정렬 (Ctrl+Shift+R)"],
    insertOrderedList: ["numbers", "문단 번호"], insertUnorderedList: ["bullets", "글머리표"],
    indentLess: ["outdent", "내어쓰기 (Shift+Tab)"], indentMore: ["indent", "들여쓰기 (Tab)"],
    charDlg: ["chr", "글자 모양 (Alt+L)"], paraDlg: ["para", "문단 모양 (Alt+T)"]
  };
  var saveAct = HAVE.save ? "save" : (HAVE.dlHtml ? "dlHtml" : null);
  var STD = [   // [act, 아이콘, 풍선 도움말] — act 가 이 모드 메뉴에 없으면(GATED) 버튼을 만들지 않음
    [["new", "new", "새 문서 (Alt+N)"], ["open", "open", window.WP_PUBLIC ? "내 PC에서 열기 (Alt+O)" : "불러오기 (Alt+O)"],
     saveAct ? [saveAct, "save", saveAct === "dlHtml" ? "HTML로 내려받기 (Alt+S)" : "저장하기 (Alt+S)"] : null],
    [["browserPrint", "print", "인쇄 (브라우저 인쇄)"], ["fullscreen", "preview", "미리 보기 (Esc 닫기)"]],
    [["cut", "cut", "오려 두기 (Ctrl+X)"], ["copy", "copy", "복사하기 (Ctrl+C)"], ["paste", "paste", "붙이기 (Ctrl+V)"], "formatCopy"],
    [["undo", "undo", "되돌리기 (Ctrl+Z)"], ["redo", "redo", "다시 실행 (Ctrl+Shift+Z)"]],
    [["find", "find", "찾기 (Ctrl+F)"]],
    [["table", "table", "표 만들기 (Ctrl+N,T)"], ["image", "image", "그림 넣기 (Ctrl+N,I)"], "shapeBox", ["pagebreak", "pgbreak", "쪽 나누기 (Ctrl+Enter)"]]
  ];
  var GATED = { "new": 1, open: 1, save: 1, dlHtml: 1, browserPrint: 1, fullscreen: 1 };

  // 한글97식 전용 새 칸(한 번 만들어 재사용)
  var rowStd = mk("div", "tb-row tb-std", { id: "tbStd", role: "toolbar", "aria-label": "표준 도구 모음" });
  var rowFmt = mk("div", "tb-row tb-fmt", { id: "tbFmt", role: "toolbar", "aria-label": "서식 도구 모음" });
  var moreStd = mk("button", "t-btn tb-more hidden", { type: "button", title: "나머지 도구", "aria-label": "나머지 도구" });
  var moreFmt = mk("button", "t-btn tb-more hidden", { type: "button", title: "나머지 도구", "aria-label": "나머지 도구" });
  moreStd.textContent = moreFmt.textContent = "»";
  var popStd = mk("div", "tb-pop hidden", { id: "tbPopStd" }), popFmt = mk("div", "tb-pop hidden", { id: "tbPopFmt" });
  moreStd._pop = popStd; moreFmt._pop = popFmt;

  var sizeBox = mk("input", "t-sel tb-sizebox", { id: "sizeBox", type: "text", inputmode: "decimal", maxlength: "5", autocomplete: "off", spellcheck: "false",
    title: "글자 크기(pt) — 숫자 입력 후 Enter (Alt+Shift+E 크게 / Alt+Shift+R 작게)", "aria-label": "글자 크기" });
  var sizeWrap = mk("span", "tb-size");
  var lineSel = mk("select", "t-sel sm tb-line", { id: "lineSel", title: "줄 간격 % (Alt+Shift+Z 넓게 / Alt+Shift+A 좁게)", "aria-label": "줄 간격" });
  [100, 130, 150, 160, 180, 200, 250, 300].forEach(function (v) { var o = document.createElement("option"); o.value = String(v); o.textContent = v + "%"; lineSel.appendChild(o); });
  var lineCustom = document.createElement("option"); lineCustom.value = ""; lineCustom.hidden = true; lineSel.appendChild(lineCustom);
  var pen = "#c00000";
  var colorBtn = mk("button", "t-btn tb-color", { type: "button", "data-act": "fontColor", title: "글자 색 (Ctrl+M,R 빨강 · K 검정 · B 파랑)" });
  colorBtn.innerHTML = '<span class="tb-ca">가</span><span class="tb-cbar"></span>';
  var colorDrop = mk("button", "t-btn tb-drop", { type: "button", id: "tbColorDrop", title: "글자 색 고르기", "aria-label": "글자 색 고르기" });
  colorDrop.textContent = "▾";
  var colorPop = mk("div", "tb-pop tb-cpop hidden", { id: "tbColorPop" });
  (WP.PALETTE || ["#000000", "#c00000", "#0070c0"]).forEach(function (c) {
    var s = mk("button", "sw", { type: "button", "data-color": c, title: c }); s.style.background = c; colorPop.appendChild(s);
  });
  function setPen(c) { pen = c; colorBtn.querySelector(".tb-cbar").style.background = c; try { localStorage.setItem("hw.pen", c); } catch (e) {} }
  try { var lp = localStorage.getItem("hw.pen"); if (lp && /^#[0-9a-f]{6}$/i.test(lp)) pen = lp; } catch (e) {}
  setPen(pen);

  var stdBuilt = false;
  function buildStd() {
    if (stdBuilt) return; stdBuilt = true;
    STD.forEach(function (grp, gi) {
      var nodes = [];
      grp.forEach(function (d) {
        if (!d) return;
        if (typeof d === "string") { if (O[d]) nodes.push(O[d]); return; }
        if (GATED[d[0]] && !HAVE[d[0]]) return;   // 이 모드에서 메뉴에 없는 파일 동작 = 버튼도 없음(임베드: 새 문서·불러오기·저장)
        var b = mk("button", "t-btn", { type: "button", "data-act": d[0], title: d[2], "aria-label": d[2] });
        b.innerHTML = svg(d[1]); nodes.push(b);
      });
      if (!nodes.length) return;
      rowStd._groups = rowStd._groups || []; rowStd._groups.push(nodes);
    });
  }
  function fill(row, groups, more) {
    while (row.firstChild) row.removeChild(row.firstChild);
    groups.forEach(function (g, i) { if (i) row.appendChild(mk("span", "t-sep")); g.forEach(function (n) { row.appendChild(n); }); });
    row.appendChild(more);
  }
  function fmtGroups() {
    sizeWrap.appendChild(sizeBox); sizeWrap.appendChild(SEL.size);
    var cw = mk("span", "tb-cwrap"); cw.appendChild(colorBtn); cw.appendChild(colorDrop);
    return [[SEL.style, SEL.font, sizeWrap], [O.bold, O.italic, O.underline, cw],
      [O.justifyFull, O.justifyLeft, O.justifyCenter, O.justifyRight], [lineSel],
      [O.insertOrderedList, O.insertUnorderedList], [O.indentLess, O.indentMore], [O.charDlg, O.paraDlg]]
      .map(function (g) { return g.filter(Boolean); }).filter(function (g) { return g.length; });
  }

  // ── 좁은 화면: 줄에 안 들어가는 뒤쪽 도구를 » 팝업으로(노드째 이동 → 리스너·.on 유지) ──
  function fit(row, more) {
    var pop = more._pop;
    while (pop.firstChild) row.insertBefore(pop.firstChild, more);
    more.classList.add("hidden");
    if (!row.clientWidth || row.scrollWidth <= row.clientWidth + 1) return;
    more.classList.remove("hidden");
    var guard = 200;
    while (row.scrollWidth > row.clientWidth + 1 && more.previousSibling && guard--) pop.insertBefore(more.previousSibling, pop.firstChild);
    var l = more.previousSibling;
    while (l && l.classList && l.classList.contains("t-sep")) { pop.insertBefore(l, pop.firstChild); l = more.previousSibling; }
  }
  var fitPending = false;
  function fitAll() {
    if (layout !== "97") return;
    var ae = document.activeElement;
    if (ae && ae !== document.body && tb.contains(ae)) { fitPending = true; return; }   // 폰: 입력칸 포커스→화상 키보드 resize 로 칸이 팝업 밖으로 옮겨져 포커스 잃는 것 방지 — 포커스 떠난 뒤 맞춤
    fitPending = false; closePops(); fit(rowStd, moreStd); fit(rowFmt, moreFmt);
  }
  tb.addEventListener("focusout", function () { if (fitPending) setTimeout(function () { if (fitPending) fitAll(); }, 0); });
  var fitQ = false;
  function fitSoon() { if (!fitQ) { fitQ = true; requestAnimationFrame(function () { fitQ = false; fitAll(); }); } }
  window.addEventListener("resize", fitSoon);
  if (window.ResizeObserver) {   // 숨김(미리 보기·zen)에서 돌아올 때·폭 변화 — 줄 폭만 보고 다시 맞춤(내용 이동은 줄 폭을 안 바꿔 루프 없음)
    var _rw = [0, 0];
    new ResizeObserver(function (ents) {
      var ch = false;
      ents.forEach(function (en) { var i = en.target === rowStd ? 0 : 1, w = Math.round(en.contentRect.width); if (w !== _rw[i]) { _rw[i] = w; ch = true; } });
      if (ch) fitSoon();
    }).observe(rowStd);
  }
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(fitSoon).catch(function () {});

  function closePops() { [popStd, popFmt, colorPop].forEach(function (p) { p.classList.add("hidden"); }); }
  function openPop(pop, anchor) {
    var was = !pop.classList.contains("hidden"); closePops(); if (was) return;
    pop.classList.remove("hidden");
    var tr = tb.getBoundingClientRect(), ar = anchor.getBoundingClientRect(), w = pop.offsetWidth;
    var left = Math.max(4, Math.min(ar.left - tr.left, tr.width - w - 4));
    pop.style.left = left + "px"; pop.style.top = (ar.bottom - tr.top + 2) + "px";
  }
  [moreStd, moreFmt, colorDrop].forEach(function (b) {
    b.addEventListener("pointerdown", function (e) { e.preventDefault(); WP.saveRange(); });   // 본문 선택 유지
    b.addEventListener("pointerup", function (e) { e.stopPropagation(); openPop(b === colorDrop ? colorPop : b._pop, b); });
  });
  colorPop.addEventListener("pointerdown", function (e) { e.preventDefault(); });
  colorPop.addEventListener("pointerup", function (e) {
    var s = e.target.closest(".sw"); if (!s) return;
    e.stopPropagation(); setPen(s.getAttribute("data-color")); closePops(); WP.restoreRange(); WP.dispatch("fontColor");
  });
  [popStd, popFmt].forEach(function (p) {   // 팝업 안 버튼 실행 뒤 닫기(실행은 #toolbar 위임이 처리)
    p.addEventListener("pointerup", function (e) { if (e.target.closest("[data-act]")) setTimeout(closePops, 0); });
  });
  document.addEventListener("pointerdown", function (e) {   // 캡처: 메뉴 막대(전파 중단)를 눌러도 팝업이 닫히게
    if (e.target.closest && e.target.closest(".tb-pop,.tb-more,#tbColorDrop")) return;
    closePops();
  }, true);
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") closePops(); }, true);

  // ── 글자 크기 입력칸·줄 간격 칸 ──
  sizeBox.addEventListener("pointerdown", WP.saveRange);
  sizeBox.addEventListener("focus", function () { WP.saveRange(); try { sizeBox.select(); } catch (e) {} });
  function commitSize() {
    var v = parseFloat(String(sizeBox.value).replace(",", "."));
    if (!isFinite(v) || v < 1 || v > 300) { WP.toast("글자 크기는 1~300pt"); syncNow(); return; }
    v = Math.round(v * 2) / 2;
    WP.restoreRange(); WP.setFontPt(v); sizeBox.value = String(v);
  }
  sizeBox.addEventListener("keydown", function (e) {
    if (!/^(F\d+|Escape)$/.test(e.key)) e.stopPropagation();   // 문서 단축키로 새지 않게(F10 메뉴·F1 도움말·Esc 는 통과)
    if (e.key === "Enter") { e.preventDefault(); commitSize(); }
    else if (e.key === "Escape") { e.preventDefault(); syncNow(); WP.restoreRange(); }
  });
  sizeBox.addEventListener("change", function () { if (document.activeElement === sizeBox) commitSize(); });
  SEL.size.addEventListener("change", function () { sizeBox.value = SEL.size.value; });
  lineSel.addEventListener("pointerdown", WP.saveRange);
  lineSel.addEventListener("focus", WP.saveRange);
  lineSel.addEventListener("change", function () {
    var v = parseInt(lineSel.value, 10); if (!v) return;
    WP.restoreRange(); WP.setLinePct(v); WP.page.focus();
  });
  function syncNow() { if (WP.syncToolbar) WP.syncToolbar(); }
  WP.onSync(function (blk, cs, pt) {   // 커서 위치 서식 → 크기 입력칸·줄 간격 칸
    if (document.activeElement !== sizeBox) {
      var px = parseFloat(cs.fontSize) || 14.67, p2 = Math.round(px / 1.3333 * 2) / 2;
      sizeBox.value = String(p2);
    }
    var b = blk || WP.page, bcs = getComputedStyle(b), lh = parseFloat(bcs.lineHeight) / (parseFloat(bcs.fontSize) || 1);
    var pct = isFinite(lh) ? Math.round(lh * 100) : 160, hit = false;
    for (var i = 0; i < lineSel.options.length - 1; i++) if (parseInt(lineSel.options[i].value, 10) === pct) { lineSel.selectedIndex = i; hit = true; break; }
    if (!hit) { lineCustom.textContent = pct + "%"; lineCustom.value = ""; lineSel.selectedIndex = lineSel.options.length - 1; }
  });

  // ── 모양 전환 ──
  var layout = null;
  function apply(v) {
    v = v === "classic" ? "classic" : "97";
    closePops();
    if (v === "97") {
      buildStd();
      ORIG.forEach(function (n) { if (n.parentNode === tb) tb.removeChild(n); });
      Object.keys(DECO).forEach(function (a) {
        var n = O[a]; if (!n) return; var d = DECO[a];
        if (d[0]) n.innerHTML = svg(d[0]);
        n.setAttribute("title", d[1]); n.setAttribute("aria-label", d[1]);
      });
      SEL.style.setAttribute("title", "스타일 (F6)"); SEL.font.setAttribute("title", "글꼴 (자세히: 글자 모양 Alt+L)");
      SEL.size.setAttribute("title", "글자 크기 목록");
      layout = "97";
      [popStd, popFmt].forEach(function (p) { while (p.firstChild) p.removeChild(p.firstChild); });   // 지난번 넘침 잔여(옛 구분선) 비움 — 실제 도구는 fill 이 다시 넣음
      fill(rowStd, rowStd._groups || [], moreStd); fill(rowFmt, fmtGroups(), moreFmt);
      tb.appendChild(rowStd); tb.appendChild(rowFmt); tb.appendChild(popStd); tb.appendChild(popFmt); tb.appendChild(colorPop);
      tb.classList.add("tb97");
      WP.setMenus(menus97());
      fitAll(); syncNow();
    } else {
      layout = "classic";
      [rowStd, rowFmt, popStd, popFmt, colorPop].forEach(function (n) { if (n.parentNode === tb) tb.removeChild(n); });
      SAVED.forEach(function (s) {
        var n = s[0];
        if (s[1] == null) n.removeAttribute("title"); else n.setAttribute("title", s[1]);
        if (s[2] != null) { n.innerHTML = s[2]; n.removeAttribute("aria-label"); }
      });
      ORIG.forEach(function (n) { tb.appendChild(n); });   // 기존 순서 그대로(크기 셀렉트도 제자리)
      tb.classList.remove("tb97");
      WP.setMenus(menusClassic());
    }
  }
  function setLayout(v, quiet) {
    apply(v);
    try { localStorage.setItem(KEY, layout); } catch (e) {}
    if (!quiet) WP.toast(layout === "97" ? "도구 모음: 한글97식" : "도구 모음: 기존");
  }

  WP.register({
    fontColor: function () { if (WP.setColor) WP.setColor(pen); },
    help: openHelp,
    guide: function () { try { window.open("guide.html", "_blank", "noopener,noreferrer"); } catch (e) {} },
    tbLayout97: function () { setLayout("97"); WP.page.focus(); }, tbLayoutClassic: function () { setLayout("classic"); WP.page.focus(); },   // 키보드로 고른 뒤 포커스가 사라진 노드에 남지 않게
    tblRowAbove: tt("rowAbove"), tblRowBelow: tt("rowBelow"), tblColLeft: tt("colLeft"), tblColRight: tt("colRight"),
    tblDelRow: tt("delRow"), tblDelCol: tt("delCol"), tblMerge: tt("merge"), tblSplit: tt("split"), tblBorder: tt("border"),
    tblJoin: tt("joinTable"), tblSplitTable: tt("splitTable"), tblDelete: tt("delTable")
  });
  WP.toolbarLayout = function () { return layout; };
  WP.setToolbarLayout = function (v) { setLayout(v, true); };

  var saved = null;
  try { saved = localStorage.getItem(KEY); } catch (e) {}
  apply(saved === "classic" ? "classic" : "97");
})();
