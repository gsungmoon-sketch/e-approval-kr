// 한워드 — 표 셀 편집(병합/분할/행열 삽입삭제/배경/정렬) + 표 도구막대.
(function () {
  "use strict";
  var WP = window.WP, $ = WP.$, page = WP.page, markDirty = WP.markDirty, toastMsg = WP.toast,
      saveRange = WP.saveRange, restoreRange = WP.restoreRange, openModal = WP.openModal, closeModal = WP.closeModal;

  function getCell() {
    var s = window.getSelection(); if (!s.rangeCount) return null;
    var n = s.anchorNode; if (n && n.nodeType === 3) n = n.parentNode;
    return n && n.closest ? n.closest("td,th") : null;
  }
  function buildGrid(table) {
    var grid = [], rows = table.rows;
    for (var r = 0; r < rows.length; r++) {
      grid[r] = grid[r] || []; var c = 0;
      for (var i = 0; i < rows[r].cells.length; i++) {
        var td = rows[r].cells[i]; while (grid[r][c]) c++;
        var cs = td.colSpan || 1, rs = td.rowSpan || 1;
        for (var dr = 0; dr < rs; dr++) for (var dc = 0; dc < cs; dc++) { grid[r + dr] = grid[r + dr] || []; grid[r + dr][c + dc] = { td: td, master: (dr === 0 && dc === 0) }; }
        c += cs;
      }
    }
    return grid;
  }
  function cellPos(grid, td) {
    for (var r = 0; r < grid.length; r++) for (var c = 0; grid[r] && c < grid[r].length; c++)
      if (grid[r][c] && grid[r][c].td === td && grid[r][c].master) return { r: r, c: c };
    return null;
  }
  function gridCols(grid) { var m = 0; grid.forEach(function (row) { m = Math.max(m, row.length); }); return m; }
  function selTable() {
    var cell = getCell(); if (cell) return cell.closest("table");
    var s = window.getSelection(); if (!s.rangeCount) return null;
    var n = s.getRangeAt(0).commonAncestorContainer; if (n.nodeType === 3) n = n.parentNode;
    return n && n.closest ? n.closest("table") : null;
  }
  function selCells() {
    if (cb) { var cl = cbList(); if (cl.length) return cl; }   // 셀 블록 모드면 블록 셀(.cb-sel) 사용
    var s = window.getSelection(), table = selTable(); if (!table) return [];
    if (s.rangeCount && !s.isCollapsed) {
      var got = Array.prototype.filter.call(table.querySelectorAll("td,th"), function (td) { return s.containsNode(td, true); });
      if (got.length) return got;
    }
    var cell = getCell(); return cell ? [cell] : [];
  }
  function tEmit() { markDirty(); updateTableTools(); }
  function insertRow(below) {
    var c = getCell(); if (!c) return; var table = c.closest("table"), ncols = gridCols(buildGrid(table));
    var tr = document.createElement("tr");
    for (var i = 0; i < ncols; i++) { var td = document.createElement("td"); td.innerHTML = "<br>"; tr.appendChild(td); }
    var ref = c.parentNode; ref.parentNode.insertBefore(tr, below ? ref.nextSibling : ref); tEmit();
  }
  function insertCol(right) {
    var c = getCell(); if (!c) return; var table = c.closest("table"), grid = buildGrid(table), pos = cellPos(grid, c); if (!pos) return;
    var col = right ? pos.c + (c.colSpan || 1) : pos.c;
    for (var r = 0; r < table.rows.length; r++) {
      var g = grid[r] || [], at = g[col], td = document.createElement("td"); td.innerHTML = "<br>";
      if (at && at.master) at.td.parentNode.insertBefore(td, at.td);
      else if (at && !at.master) { var left = g[col - 1]; if (left && left.td === at.td) at.td.colSpan = (at.td.colSpan || 1) + 1; }
      else table.rows[r].appendChild(td);
    }
    tEmit();
  }
  function delCol() {
    var c = getCell(); if (!c) return; var table = c.closest("table"), grid = buildGrid(table), pos = cellPos(grid, c); if (!pos) return;
    var col = pos.c, removed = [];
    for (var r = 0; r < table.rows.length; r++) {
      var g = grid[r] || [], at = g[col]; if (!at) continue; var td = at.td;
      if ((td.colSpan || 1) > 1) {                 // colspan 셀: 한 번만 줄임(rowspan 연속행서 master 통째삭제 방지)
        if (removed.indexOf(td) < 0) { td.colSpan = td.colSpan - 1; removed.push(td); }
      } else if (removed.indexOf(td) < 0) { removed.push(td); if (td.parentNode) td.parentNode.removeChild(td); }
    }
    tEmit();
  }
  function mergeCells() {
    var cells = selCells(); if (cells.length < 2) { toastMsg("두 개 이상 셀을 선택하세요"); return; }
    var table = cells[0].closest("table"), grid = buildGrid(table), rs = [], cs = [];
    cells.forEach(function (td) { var p = cellPos(grid, td); if (p) { rs.push(p.r, p.r + (td.rowSpan || 1) - 1); cs.push(p.c, p.c + (td.colSpan || 1) - 1); } });
    if (!rs.length) return;
    var minr = Math.min.apply(null, rs), maxr = Math.max.apply(null, rs), minc = Math.min.apply(null, cs), maxc = Math.max.apply(null, cs);
    var mcell = grid[minr] && grid[minr][minc];                  // 비직사각형 선택 시 코너 칸 빌 수 있음
    var master = mcell ? mcell.td : cells[0], box = [];          // 없으면 첫 선택셀로 폴백(null 참조 크래시 방지)
    for (var r = minr; r <= maxr; r++) for (var c = minc; c <= maxc; c++) if (grid[r] && grid[r][c] && box.indexOf(grid[r][c].td) < 0) box.push(grid[r][c].td);
    box.forEach(function (td) { if (td !== master) { var h = td.innerHTML.replace(/<br\s*\/?>/gi, "").trim(); if (h) master.innerHTML += " " + td.innerHTML; if (td.parentNode) td.parentNode.removeChild(td); } });
    master.colSpan = maxc - minc + 1; master.rowSpan = maxr - minr + 1;
    var rg = document.createRange(); rg.selectNodeContents(master); rg.collapse(false); var s = window.getSelection(); s.removeAllRanges(); s.addRange(rg); tEmit();
  }
  // ── 셀 나누기(HWP식): 대상 셀을 grid-line 삽입으로 nr×nc 병합 크기까지 키운 뒤 균등 분할 → 일반 셀·병합 셀 모두 N×M 분할 ──
  // 대상 셀이 차지하는 그리드 열을 add만큼 넓힘: 대상과, 각 행에서 같은 grid열을 덮는 셀을 add씩 증가(rowspan 셀은 dedup)
  function growCellColumns(table, td, add) {
    if (add <= 0) return;
    var grid = buildGrid(table), pos = cellPos(grid, td); if (!pos) return; var done = [];
    for (var r = 0; r < grid.length; r++) {
      var at = grid[r] && grid[r][pos.c]; if (!at) continue; var cell = at.td;
      if (done.indexOf(cell) >= 0) continue; done.push(cell);
      cell.colSpan = (cell.colSpan || 1) + add;
    }
  }
  function growCellRows(table, td, add) {
    if (add <= 0) return;
    var grid = buildGrid(table), pos = cellPos(grid, td); if (!pos) return;
    var cs = td.colSpan || 1, rs0 = td.rowSpan || 1, lastRowIdx = pos.r + rs0 - 1, done = [td];
    td.rowSpan = rs0 + add;   // 대상 자체를 R행까지 확장(unmergeInto가 R/nr로 균등 분할)
    for (var c = 0; c < gridCols(grid); c++) {
      if (c >= pos.c && c < pos.c + cs) continue;   // 대상 열 영역은 unmergeInto가 채움 — 여기선 건너뜀
      var at = grid[lastRowIdx] && grid[lastRowIdx][c]; if (!at) continue; var cell = at.td;   // 대상 밴드 '마지막 행'을 덮는 셀만 확장(반높이 이웃 오확장 방지)
      if (done.indexOf(cell) >= 0) continue; done.push(cell);
      cell.rowSpan = (cell.rowSpan || 1) + add;
    }
    var refRow = table.rows[lastRowIdx];   // 대상 마지막 행 뒤에 새 <tr> add개 삽입(위 셀들이 rowspan으로 덮음)
    for (var k = 0; k < add; k++) { var tr = document.createElement("tr"); refRow.parentNode.insertBefore(tr, refRow.nextSibling); }
  }
  // 대상(현재 C그리드열×R그리드행 병합)을 nr×nc 서브셀로 균등 분할. 서브셀 span = C/nc × R/nr (구성상 정수)
  function unmergeInto(table, td, nr, nc) {
    var pos = cellPos(buildGrid(table), td); if (!pos) return;
    var C = td.colSpan || 1, R = td.rowSpan || 1, subC = Math.max(1, Math.round(C / nc)), subR = Math.max(1, Math.round(R / nr));
    td.colSpan = subC; td.rowSpan = subR;
    var last = td;
    for (var cc = 1; cc < nc; cc++) { var cell = document.createElement("td"); cell.innerHTML = "<br>"; cell.colSpan = subC; cell.rowSpan = subR; td.parentNode.insertBefore(cell, last.nextSibling); last = cell; }
    for (var rr = 1; rr < nr; rr++) {
      var gr = pos.r + rr * subR, row = table.rows[gr]; if (!row) continue;
      var g2 = buildGrid(table)[gr] || [], before = null;
      for (var k = pos.c; k < g2.length; k++) { if (g2[k] && g2[k].master && g2[k].td.parentNode === row) { before = g2[k].td; break; } }
      for (var i = 0; i < nc; i++) { var td2 = document.createElement("td"); td2.innerHTML = "<br>"; td2.colSpan = subC; td2.rowSpan = subR; row.insertBefore(td2, before); }
    }
  }
  function splitInto(td, nr, nc) {
    nr = Math.max(1, nr | 0); nc = Math.max(1, nc | 0); if (nr === 1 && nc === 1) return;
    var table = td.closest("table"); if (!table) return;
    var rc = td.getBoundingClientRect(), origW = rc.width, origH = rc.height;
    var cs0 = td.colSpan || 1, rs0 = td.rowSpan || 1;
    var C = cs0; while (C < nc || C % nc !== 0) C++;            // nc의 배수이면서 ≥cs0 → 균등 분할 보장
    var R = rs0; while (R < nr || R % nr !== 0) R++;
    growCellColumns(table, td, C - cs0);
    growCellRows(table, td, R - rs0);
    unmergeInto(table, td, nr, nc);
    // 크기 힌트(자동 레이아웃): 대상 행 서브셀 폭·새 행 높이 균등 분배(정밀 아님, 시각 힌트)
    if (origW > 0) { var row0 = td.parentNode, cells = row0.cells, base = td.cellIndex; for (var i = 0; i < nc; i++) if (cells[base + i]) setCellW(cells[base + i], origW / nc); }
    var caretR = document.createRange(); caretR.selectNodeContents(td); caretR.collapse(true);
    var s = window.getSelection(); s.removeAllRanges(); s.addRange(caretR);
    tEmit();
  }
  var splitTargets = null;
  function split() {   // S / 툴바 / 우클릭 — 항상 '줄 수/칸 수' 대화상자(일반·병합 셀 공통, HWP 동일)
    var c = getCell(); if (!c) { toastMsg("표 안에 커서를 두세요"); return; }
    // 셀 블록(F5)이면 블록 전체가 대상 — 모달 대기 중 DOM/캐럿 변화 대비 배열로 스냅샷(2026-07-24: 블록 나누기 버그 수정, 종전엔 캐럿 셀 1개만 분할)
    var blk = cb ? selCells() : null;
    splitTargets = (blk && blk.length) ? blk.slice() : [c];
    saveRange(); openModal("m-split");
  }
  function splitApply() {
    var nr = Math.max(1, +$("sp-r").value || 1), nc = Math.max(1, +$("sp-c").value || 1);
    restoreRange();
    var tds = (splitTargets && splitTargets.length) ? splitTargets : [getCell()];
    splitTargets = null; closeModal($("m-split"));
    if (cb) cbClear();
    // 블록 전체 각 셀을 nr×nc로 분할. splitInto가 grid-line 삽입으로 이웃(블록 내) 셀 span을 자기 분할량만큼 부풀렸다가
    // 각 셀 분할이 그만큼 되받아 나눠 동일 nr×nc 적용 시 자기정합적. isConnected로 선행 분할에 흡수된 셀 방어.
    tds.forEach(function (td) { if (td && td.isConnected) splitInto(td, nr, nc); });
  }
  // ── 표 선 그리기(엑셀식 셀 테두리, 2026-07-06) ──
  // 각 셀 4변을 개별 인라인 style로 관리(HTML 저장에 자동 왕복). border-collapse에서
  // 'hidden'은 이웃 실선을 이기고 'dashed'는 'solid'에 지므로, 변경 시 공유변 양쪽을 함께 칠함.
  var _bStyle = "solid", _bWidth = 1;                // 현재 선택된 선 종류/굵기(대화상자)
  var BSTYLE_NAMES = { solid: "실선", dotted: "점선", dashed: "파선", double: "이중선", hidden: "숨김" };
  function _edgeProp(side) { return "border" + side.charAt(0).toUpperCase() + side.slice(1); }
  function setEdge(td, side, style, w) {
    var p = _edgeProp(side), cls = "nb-" + side.charAt(0);     // nb-t/r/b/l = '없음' 변 편집 안내선 마킹
    if (style === "hidden") { td.style[p] = "1px hidden"; td.classList.add(cls); return; }   // 없음: 출력 투명 + 편집기 붉은 점선
    td.classList.remove(cls);
    if (style === "def") td.style[p] = "";                     // 인라인 제거 → CSS 기본(1px solid #555)
    else {
      var wd = (style === "double") ? 3 : (w || 1);            // 이중선은 3px 미만 렌더 불가 → 3px 강제
      td.style[p] = wd + "px " + style + " #555";              // solid/dotted/dashed/double
    }
  }
  function syncHiddenGuides() {   // 불러오기/HWPX 임포트 표: hidden 변에 안내선 클래스(nb-*) 동기화(2026-07-18)
    //   임포트는 인라인 border만 복원해 편집 안내선(붉은 점선)이 안 보였음 — 로드 시 스타일→클래스 재도출.
    Array.prototype.forEach.call(page.querySelectorAll("td,th"), function (td) {
      ["top", "right", "bottom", "left"].forEach(function (side) {
        var v = td.style[_edgeProp(side)] || "", cls = "nb-" + side.charAt(0);
        if (/\bhidden\b/.test(v)) td.classList.add(cls); else td.classList.remove(cls);
      });
    });
  }
  function cellAt(grid, r, c) { return (grid[r] && grid[r][c]) ? grid[r][c].td : null; }
  function paintEdge(grid, r, c, side, style, w) {   // 변 + 공유하는 이웃 셀의 반대변을 함께 세팅
    var td = cellAt(grid, r, c); if (!td) return;
    setEdge(td, side, style, w);
    var nr = r, nc = c, opp;
    if (side === "top") { nr = r - 1; opp = "bottom"; }
    else if (side === "bottom") { nr = r + 1; opp = "top"; }
    else if (side === "left") { nc = c - 1; opp = "right"; }
    else { nc = c + 1; opp = "left"; }
    var nb = cellAt(grid, nr, nc);
    if (nb && nb !== td) setEdge(nb, opp, style, w);
  }
  function applyBorderPreset(place) {
    var cs = selCells(); if (!cs.length) { toastMsg("셀을 먼저 선택하세요(F5 또는 드래그)"); return; }
    var t = cs[0].closest("table"), grid = buildGrid(t), rs = [], ncs = [];
    cs.forEach(function (td) { var p = cellPos(grid, td); if (p) { rs.push(p.r); ncs.push(p.c); } });
    if (!rs.length) return;
    var r0 = Math.min.apply(null, rs), r1 = Math.max.apply(null, rs),
        c0 = Math.min.apply(null, ncs), c1 = Math.max.apply(null, ncs);
    var st = (place === "none") ? "hidden" : _bStyle, w = _bWidth;
    for (var r = r0; r <= r1; r++) for (var c = c0; c <= c1; c++) {
      var top = (r === r0), bot = (r === r1), lft = (c === c0), rgt = (c === c1);
      if (place === "top") { if (top) paintEdge(grid, r, c, "top", st, w); }
      else if (place === "bottom") { if (bot) paintEdge(grid, r, c, "bottom", st, w); }
      else if (place === "left") { if (lft) paintEdge(grid, r, c, "left", st, w); }
      else if (place === "right") { if (rgt) paintEdge(grid, r, c, "right", st, w); }
      else if (place === "outer") {
        if (top) paintEdge(grid, r, c, "top", st, w); if (bot) paintEdge(grid, r, c, "bottom", st, w);
        if (lft) paintEdge(grid, r, c, "left", st, w); if (rgt) paintEdge(grid, r, c, "right", st, w);
      } else if (place === "inner") {
        if (!bot) paintEdge(grid, r, c, "bottom", st, w); if (!rgt) paintEdge(grid, r, c, "right", st, w);
      } else {   // all / none = 4변 모두
        paintEdge(grid, r, c, "top", st, w); paintEdge(grid, r, c, "bottom", st, w);
        paintEdge(grid, r, c, "left", st, w); paintEdge(grid, r, c, "right", st, w);
      }
    }
    tEmit();
    var stLabel = (st === "hidden") ? "숨김" : BSTYLE_NAMES[st] + " " + ((st === "double") ? 3 : w) + "px";
    toastMsg("테두리 적용: " + ({ outer: "바깥", inner: "안쪽", all: "전체", none: "없음(숨김)", top: "위", bottom: "아래", left: "왼", right: "오른" }[place] || place) + " · " + stLabel);
  }
  function openBorderDlg() {
    if (!selCells().length) { toastMsg("표 셀을 먼저 선택하세요(F5)"); return; }
    _syncBStyleUI(); openModal("m-cellborder");
  }
  function _syncBStyleUI() {
    var g = document.getElementById("m-cellborder"); if (!g) return;
    g.querySelectorAll(".lsel-list").forEach(function (l) { l.classList.add("hidden"); });   // 재오픈 시 잔존 목록 닫기
    g.querySelectorAll(".bplace").forEach(function (b) { b.classList.remove("on"); });        // 위치 하이라이트는 리셋(선 스타일은 유지) — 주인 결정
    g.querySelectorAll(".bstyle").forEach(function (b) { b.classList.toggle("on", b.dataset.style === _bStyle); });
    g.querySelectorAll(".bwidth").forEach(function (b) { b.classList.toggle("on", +b.dataset.w === _bWidth); });
    var sb = g.querySelector("#ls-style-btn");
    if (sb) { sb.querySelector(".lprev").setAttribute("data-prev", _bStyle); sb.querySelector(".lsel-txt").textContent = BSTYLE_NAMES[_bStyle]; }
    var lock = (_bStyle === "double" || _bStyle === "hidden");   // 이중선=3px 고정, 숨김=굵기 무관
    var ws = g.querySelector("#ls-width"); if (ws) ws.classList.toggle("disabled", lock);
    var wb = g.querySelector("#ls-width-btn");
    if (wb) {
      wb.querySelector(".lprev").setAttribute("data-prev", _bStyle === "double" ? "w3" : (_bStyle === "hidden" ? "hidden" : "w" + _bWidth));
      wb.querySelector(".lsel-txt").textContent = _bStyle === "double" ? "3px (고정)" : (_bStyle === "hidden" ? "—" : _bWidth + "px");
    }
  }
  var PLACE_BTN = { char: "placeChar", fixed: "placeFixed", float: "placeFloat" };
  function updateTableTools() {
    var t = selTable(); $("tabletools").classList.toggle("hidden", !t);
    if (!t) return;
    var want = PLACE_BTN[tmode(t)];   // 현재 배치 모드 버튼 하이라이트(항상 가시)
    $("tabletools").querySelectorAll("[data-tt^=place]").forEach(function (b) { b.classList.toggle("on", b.getAttribute("data-tt") === want); });
    // 작동가능=진하게, 불가능=흐리게: 표 좌/중/우 오프셋=글자처럼일 때만 / 병합=셀 블록 2개+
    var isChar = tmode(t) === "char", cb = page.querySelectorAll("td.cb-sel, th.cb-sel").length;
    function dim(tt, on) { var b = $("tabletools").querySelector('[data-tt=' + tt + ']'); if (b) b.classList.toggle("dim", !on); }
    ["offXm", "offXc", "offXp"].forEach(function (tt) { dim(tt, isChar); });
    dim("merge", cb >= 2 || selCells().length >= 2);   // 셀 블록(F5) 또는 드래그 선택 2개+ (mergeCells는 둘 다 동작)
  }
  // ── 표 전체 단위(HWP식: 표를 지정해 삭제) ──
  function delTable() {
    var t = selTable(); if (!t) { toastMsg("표 안에 커서를 두세요"); return; }
    var focusEl = t.nextElementSibling || t.previousElementSibling;
    t.parentNode.removeChild(t);
    if (!page.querySelector("p,h1,h2,h3,table,ul,ol,li")) WP.resetPageKeepMeta();   // #wpmeta 보존 리셋
    focusEl = focusEl || page.querySelector("p") || page.firstElementChild;
    if (focusEl) { var r = document.createRange(); r.selectNodeContents(focusEl); r.collapse(true); var s = window.getSelection(); s.removeAllRanges(); s.addRange(r); }
    page.focus(); tEmit(); toastMsg("표 삭제됨");
  }
  var TT = { rowAbove: function () { insertRow(false); }, rowBelow: function () { insertRow(true); },
    colLeft: function () { insertCol(false); }, colRight: function () { insertCol(true); },
    delRow: function () {                              // rowspan 보존(#9, HEAD fd4b780 복원): 삭제 행에서 시작하는 병합 셀은 내용째 다음 행으로 이관, 관통 셀은 rowspan만 감소
      var c = getCell(); if (!c) return; var tr = c.parentNode, t = tr.closest("table");
      if (t.rows.length <= 1) return;
      var grid = buildGrid(t), ri = tr.rowIndex, g = grid[ri] || [], handled = [];
      for (var col = 0; col < g.length; col++) {
        var cell = g[col]; if (!cell || handled.indexOf(cell.td) >= 0) continue;
        handled.push(cell.td);
        var td = cell.td, rs = td.rowSpan || 1; if (rs <= 1) continue;
        td.rowSpan = rs - 1;
        if (td.parentNode === tr) {                    // 이 행에서 시작 → 다음 행의 논리 열 위치로 이동(내용 유지)
          var pos = cellPos(grid, td), nextRow = t.rows[ri + 1], ng = grid[ri + 1] || [], before = null;
          for (var cc = pos.c + (td.colSpan || 1); cc < ng.length; cc++)
            if (ng[cc] && ng[cc].master && ng[cc].td.parentNode === nextRow) { before = ng[cc].td; break; }
          nextRow.insertBefore(td, before);
        }                                              // 위 행에서 시작해 관통 → rowspan만 줄임(위에서 처리됨)
      }
      tr.parentNode.removeChild(tr); tEmit();
    },
    delCol: delCol, merge: mergeCells, split: split,
    joinTable: joinTable, splitTable: splitTable,
    border: openBorderDlg,
    delTable: delTable,
    offXm: function () { alignCharTable("left"); }, offXc: function () { alignCharTable("center"); }, offXp: function () { alignCharTable("right"); },
    copyTable: copyTable, cutTable: cutTable,
    placeChar: function () { setModeUser("char"); }, placeFixed: function () { setModeUser("fixed"); }, placeFloat: function () { setModeUser("float"); },
    place: function () { setPlace(); } };
  $("tabletools").addEventListener("pointerdown", function (e) { if (e.target.closest("[data-tt]")) e.preventDefault(); });
  $("tabletools").addEventListener("pointerup", function (e) { var b = e.target.closest("[data-tt]"); if (!b) return; var f = TT[b.getAttribute("data-tt")]; if (f) f(); });
  $("cellbg").addEventListener("input", function () { var col = this.value; selCells().forEach(function (td) { td.style.background = col; }); tEmit(); });
  // 셀 배경색 견본(테두리 대화상자 내) — 글자색/음영색과 동일 22색 + 없음. 견본 클릭=선택 셀에 즉시 적용
  (function buildCellBgPalette() {
    var pal = $("cbg-pal"); if (!pal) return;
    var CBG_COLORS = ["#000000", "#404040", "#808080", "#a6a6a6", "#d9d9d9", "#ffffff",
      "#c00000", "#e36c09", "#ffc000", "#00b050", "#00b0b0", "#0070c0", "#7030a0", "#e84c9a",
      "#ff0000", "#ff9900", "#ffd966", "#92d050", "#00b0f0", "#8000ff", "#ff00ff", "#7f6000"];
    function applyBg(col) {
      var cs = selCells(); if (!cs.length) { toastMsg("셀을 먼저 선택하세요(F5)"); return; }
      cs.forEach(function (td) { td.style.background = (col === null ? "" : col); }); tEmit();
      var lc = String(col).toLowerCase();
      pal.querySelectorAll(".sw").forEach(function (s) { s.classList.toggle("sel", col !== null && (s.getAttribute("data-color") || "").toLowerCase() === lc); });
    }
    CBG_COLORS.forEach(function (c) {
      var s = document.createElement("button"); s.type = "button"; s.className = "sw"; s.setAttribute("data-color", c); s.style.background = c; s.title = c;
      s.addEventListener("pointerup", function (e) { e.preventDefault(); applyBg(c); });
      pal.appendChild(s);
    });
    var none = $("cbg-none"); if (none) none.addEventListener("pointerup", function (e) { e.preventDefault(); applyBg(null); });
  })();
  var ttRaf = null;   // #23: rAF 스로틀(프레임당 1회)
  document.addEventListener("selectionchange", function () {
    if (ttRaf) return;
    ttRaf = requestAnimationFrame(function () { ttRaf = null; updateTableTools(); });
  });

  // ── 셀 크기 조절(HWP: 셀 블록 상태서 Ctrl/Alt+방향) — setColWidth/setRowHeight 직접 호출 ──
  var COLSTEP = 12, ROWSTEP = 8;
  var COLMIN = 24, ROWMIN = 18;
  // 열 너비: 같은 열(cellIndex) 모든 행 셀에 함께 적용해야 양방향 동작(table-layout:auto는 열폭=그 열 셀 max)
  function setCellW(cell, w) { var s = w + "px"; cell.style.width = s; cell.style.minWidth = s; }  // min-width:40 하한 무력화
  function freezeColW(table) {   // (HEAD fd4b780 복원 2026-07-06)
    // minWidth(하한) 없는 셀을 현재 렌더 폭으로 못박음(width만 있고 minWidth 없으면 width:100% 표에서 이웃이 하한 없이 줄어듦).
    // 측정을 전부 먼저 하고 일괄 기록 — 한 개씩 박으면 아직 안 박힌 셀이 재분배로 줄어든 값이 박힘(읽기/쓰기 분리).
    var pend = [];
    Array.prototype.forEach.call(table.rows, function (r) {
      Array.prototype.forEach.call(r.cells, function (c) { if (!c.style.minWidth) pend.push({ c: c, w: Math.round(c.getBoundingClientRect().width) }); });
    });
    pend.forEach(function (p) { setCellW(p.c, p.w); });
  }
  // 절대 크기 지정(마우스 드래그·Ctrl방향 공통 경로 → 저장 형식 일치)
  function setColWidth(td, w) {
    var table = td.closest("table"); if (!table) return; var ww = Math.max(COLMIN, Math.round(w));
    var grid = buildGrid(table), pos = cellPos(grid, td); if (!pos) return;   // DOM cellIndex는 병합 있으면 논리열과 어긋남 → 그리드 논리열로 매핑
    freezeColW(table);   // 이웃 열 재분배 차단(먼저 참값으로 못박기)
    var done = [];
    for (var r = 0; r < grid.length; r++) {
      var cell = grid[r] && grid[r][pos.c];
      if (cell && cell.master && (cell.td.colSpan || 1) === 1 && done.indexOf(cell.td) < 0) { done.push(cell.td); setCellW(cell.td, ww); }   // 해당 논리열의 단일열 셀만(스팬 셀은 폭 모호 → 제외)
    }
  }
  function setRowHeight(tr, h) { tr.style.height = Math.max(ROWMIN, Math.round(h)) + "px"; }
  // ── HWP 셀 블록 모드: F5=셀 블록(셀→줄→표 순환)·F8=줄·F7=칸. 블록 상태서 단일키 동작 ──
  var cb = false;                                    // 셀 블록 모드 on/off
  var cbAnchor = null, cbFocus = null;               // F5 블록 확장 앵커/포커스 셀 위치(2026-07-06)
  function cbList() { return Array.prototype.slice.call(page.querySelectorAll("td.cb-sel,th.cb-sel")); }
  function cbClear() { cb = false; cbAnchor = cbFocus = null; page.querySelectorAll(".cb-sel").forEach(function (x) { x.classList.remove("cb-sel"); }); }
  WP.cbClear = cbClear;   // 임베드 load 가 이전 문서 셀 블록 해제
  function cbMark(cells) {
    page.querySelectorAll(".cb-sel").forEach(function (x) { x.classList.remove("cb-sel"); });
    cells.forEach(function (td) { td.classList.add("cb-sel"); });
    cb = cells.length > 0;
    if (cells.length) {   // 캐럿은 첫 셀 안에 두어 getCell()이 동작하게(블록 셀은 .cb-sel 클래스로 추적)
      try { var r = document.createRange(); r.selectNodeContents(cells[0]); r.collapse(true);
            var s = window.getSelection(); s.removeAllRanges(); s.addRange(r); } catch (e) {}
    }
  }
  function rectCells(table, r0, c0, r1, c1) {         // 논리격자 직사각형 → td 목록(문서순서, 중복제거)
    var grid = buildGrid(table), out = [], seen = [];
    for (var r = Math.min(r0, r1); r <= Math.max(r0, r1); r++)
      for (var c = Math.min(c0, c1); c <= Math.max(c0, c1); c++) {
        var g = grid[r] && grid[r][c]; if (g && seen.indexOf(g.td) < 0) { seen.push(g.td); out.push(g.td); }
      }
    out.sort(function (a, b) { return (a.compareDocumentPosition(b) & 2) ? 1 : -1; });
    return out;
  }
  function cellBlock() {                              // F5: 현재 셀 블록(이후 방향키로 확장). 연타=현재셀 재설정만
    var c = getCell(); if (!c) return false;
    var t = c.closest("table"), grid = buildGrid(t), pos = cellPos(grid, c); if (!pos) return false;
    cbMark([c]); cbAnchor = { r: pos.r, c: pos.c }; cbFocus = { r: pos.r, c: pos.c };  // 줄/표 순환 제거(2026-07-06 사용자요청, 확장은 방향키 담당)
    return true;
  }
  function selectRow() { var c = getCell(); if (!c) return; var t = c.closest("table"), grid = buildGrid(t), pos = cellPos(grid, c); if (pos) cbMark(rectCells(t, pos.r, 0, pos.r, gridCols(grid) - 1)); }
  function selectColumn() { var c = getCell(); if (!c) return; var t = c.closest("table"), grid = buildGrid(t), pos = cellPos(grid, c); if (pos) cbMark(rectCells(t, 0, pos.c, t.rows.length - 1, pos.c)); }
  function equalHeight() {                            // H: 선택 행 높이를 최대값으로 통일
    var cs = cbList(); if (!cs.length) return; var rows = [];
    cs.forEach(function (td) { if (rows.indexOf(td.parentNode) < 0) rows.push(td.parentNode); });
    var mx = 0; rows.forEach(function (tr) { mx = Math.max(mx, Math.round(tr.getBoundingClientRect().height)); });
    rows.forEach(function (tr) { tr.style.height = mx + "px"; }); tEmit();
  }
  function equalWidth() {                             // W: 선택 열 너비를 최대값으로 통일
    var cs = cbList(); if (!cs.length) return; var t = cs[0].closest("table"), idxs = [], mx = 0;
    cs.forEach(function (td) { if (idxs.indexOf(td.cellIndex) < 0) idxs.push(td.cellIndex); mx = Math.max(mx, Math.round(td.getBoundingClientRect().width)); });
    idxs.forEach(function (i) { Array.prototype.forEach.call(t.rows, function (rr) { if (rr.cells[i]) setCellW(rr.cells[i], mx); }); }); tEmit();
  }
  function autoFill() {                              // A: 첫 셀 내용을 블록 전체에 채우기
    var cs = cbList(); if (cs.length < 2) return; var src = cs[0].innerHTML;
    for (var i = 1; i < cs.length; i++) cs[i].innerHTML = src; tEmit();
  }
  page.addEventListener("pointerdown", function (e) { if (cb && e.button !== 2) cbClear(); });   // 클릭하면 블록 해제(우클릭=컨텍스트 메뉴는 블록 유지 → 블록 대상 병합 등 가능)

  document.addEventListener("keydown", function (e) {
    if (e.target && e.target.closest && e.target.closest(".modal")) return;
    var inTable = !!getCell();
    if (inTable && e.key === "F5") { e.preventDefault(); cellBlock(); toastMsg("셀 블록 — 방향키로 확장 · M합치기 S나누기 L테두리 C배경 · H높이맞춤 W너비맞춤 A자동채움 · Alt+Ins/Del 줄추가/삭제 · Ctrl+방향 셀크기 · Esc 해제"); return; }
    if (inTable && e.key === "F8") { e.preventDefault(); selectRow(); toastMsg("줄(행) 블록 · 맞춤법(F8)은 표 밖에서"); return; }
    if (inTable && e.key === "F7") { e.preventDefault(); selectColumn(); toastMsg("칸(열) 블록 · 편집 용지(F7)는 표 밖에서"); return; }
    if (!cb) return;                                  // 이하 셀 블록 상태 전용
    var c = getCell(); if (!c) { cbClear(); return; }
    var k = e.key, K = (k.length === 1) ? k.toLowerCase() : k;
    if (k === "Escape") { e.preventDefault(); cbClear(); toastMsg("셀 블록 해제"); return; }
    if (k === "Enter") { e.preventDefault(); cbClear(); var r = document.createRange(); r.selectNodeContents(c); r.collapse(false); var s = window.getSelection(); s.removeAllRanges(); s.addRange(r); page.focus(); return; }
    if ((e.ctrlKey || e.altKey) && k.indexOf("Arrow") === 0) {   // Ctrl/Alt+방향 = 셀 크기 조절(블록 전체, 2026-07-12 주인 버그리포트)
      e.preventDefault();
      var rs = cbList(); if (!rs.length) rs = [c];
      if (k === "ArrowLeft" || k === "ArrowRight") {
        var dw = (k === "ArrowLeft") ? -COLSTEP : COLSTEP, colIdx = [], colTd = [];
        rs.forEach(function (td) { if (colIdx.indexOf(td.cellIndex) < 0) { colIdx.push(td.cellIndex); colTd.push(td); } });
        var pw = colTd.map(function (td) { return { td: td, w: Math.round(td.getBoundingClientRect().width) + dw }; });  // 먼저 전부 측정(선반영 셀이 이웃 측정 오염 방지)
        pw.forEach(function (p) { setColWidth(p.td, p.w); });
      } else {
        var dh = (k === "ArrowUp") ? -ROWSTEP : ROWSTEP, trs = [];
        rs.forEach(function (td) { if (trs.indexOf(td.parentNode) < 0) trs.push(td.parentNode); });
        var ph = trs.map(function (tr) { return { tr: tr, h: Math.round(tr.getBoundingClientRect().height) + dh }; });
        ph.forEach(function (p) { setRowHeight(p.tr, p.h); });
      }
      tEmit(); return;
    }
    if (e.altKey && k === "Insert") { e.preventDefault(); insertRow(true); selectRow(); return; }   // 줄 추가
    if (e.altKey && k === "Delete") { e.preventDefault(); TT.delRow(); cbClear(); return; }          // 줄 지우기
    if (k.indexOf("Arrow") === 0) {   // 셀 블록 확장(HWP식, 2026-07-06): 앵커 고정, 포커스 이동→사각형
      e.preventDefault();
      var t2 = c.closest("table"), grid2 = buildGrid(t2), rows2 = grid2.length, cols2 = gridCols(grid2);
      if (!cbFocus || !cbAnchor) { var p2 = cellPos(grid2, c) || { r: 0, c: 0 }; cbAnchor = { r: p2.r, c: p2.c }; cbFocus = { r: p2.r, c: p2.c }; }
      if (k === "ArrowUp") cbFocus.r = Math.max(0, cbFocus.r - 1);
      else if (k === "ArrowDown") cbFocus.r = Math.min(rows2 - 1, cbFocus.r + 1);
      else if (k === "ArrowLeft") cbFocus.c = Math.max(0, cbFocus.c - 1);
      else if (k === "ArrowRight") cbFocus.c = Math.min(cols2 - 1, cbFocus.c + 1);
      cbMark(rectCells(t2, cbAnchor.r, cbAnchor.c, cbFocus.r, cbFocus.c));
      return;
    }
    if (!e.ctrlKey && !e.altKey && k.length === 1) {       // 단일키 동작
      if (K === "m") { e.preventDefault(); mergeCells(); cbClear(); return; }
      if (K === "s") { e.preventDefault(); split(); return; }   // 나누기 대화상자(적용 시 cbClear)
      if (K === "l") { e.preventDefault(); openBorderDlg(); return; }
      if (K === "c") { e.preventDefault(); $("cellbg").click(); return; }   // 셀 배경 색 선택 열기
      if (K === "h") { e.preventDefault(); equalHeight(); return; }
      if (K === "w") { e.preventDefault(); equalWidth(); return; }
      if (K === "a") { e.preventDefault(); autoFill(); return; }
      e.preventDefault(); return;                          // 그 외 글자=블록 중 입력 차단(오타 방지)
    }
  });

  function tableApply() {
    restoreRange();
    var r = Math.max(1, +$("tb-r").value || 1), c = Math.max(1, +$("tb-c").value || 1), html = '<div class="objline"><table>';
    for (var i = 0; i < r; i++) { html += "<tr>"; for (var j = 0; j < c; j++) html += "<td><br></td>"; html += "</tr>"; }
    html += "</table></div><p><br></p>";
    document.execCommand("insertHTML", false, html);
    if (WP.normalizeEdges) WP.normalizeEdges();   // 첫머리 삽입 시 표 앞 문단 보장(표 앞 커서 확보)
    closeModal($("m-table")); markDirty();
  }

  // ── 표 안 Tab/Shift+Tab = 다음/이전 셀 (HWP식). 마지막 셀 Tab = 행 추가 ──
  function cellNav(shift) {
    var c = getCell(); if (!c) return false;
    var row = c.parentNode, cells = Array.prototype.slice.call(row.cells), idx = cells.indexOf(c), table = c.closest("table"), target = null;
    if (!shift) {
      if (idx < cells.length - 1) target = cells[idx + 1];
      else { var nr = row.nextElementSibling; if (nr) target = nr.cells[0]; else { insertRow(true); target = table.rows[table.rows.length - 1].cells[0]; } }
    } else {
      if (idx > 0) target = cells[idx - 1];
      else { var pr = row.previousElementSibling; if (pr) target = pr.cells[pr.cells.length - 1]; }
    }
    if (target) { var r = document.createRange(); r.selectNodeContents(target); r.collapse(false); var s = window.getSelection(); s.removeAllRanges(); s.addRange(r); page.focus(); }
    return true;
  }

  // ── 셀 경계 마우스 드래그 = 열 너비/행 높이 조절 (HWP식, 가장 직관적) ──
  var EDGE = 6, drag = null;
  function edgeAt(e) {
    var el = document.elementFromPoint(e.clientX, e.clientY); if (!el || !el.closest) return null;
    var td = el.closest("td,th"); if (!td || !page.contains(td)) return null;
    var r = td.getBoundingClientRect();
    if (Math.abs(e.clientX - r.right) <= EDGE) return { type: "col", td: td };
    if (Math.abs(e.clientY - r.bottom) <= EDGE) return { type: "row", tr: td.parentNode };
    return null;
  }
  page.addEventListener("pointermove", function (e) {
    if (drag) {   // 드래그도 setColWidth/setRowHeight 공통 경로(열 전체 width+minWidth) — Ctrl+방향과 저장 형식 일치
      if (drag.type === "col") setColWidth(drag.td, drag.w0 + (e.clientX - drag.x0));
      else setRowHeight(drag.tr, drag.h0 + (e.clientY - drag.y0));
      e.preventDefault(); return;
    }
    // 표 밖이면 비싼 히트테스트(elementFromPoint+rect) 생략 — e.target만으로 빠르게 컷
    var overTd = e.target && e.target.closest ? e.target.closest("td,th") : null;
    if (!overTd) { if (page.style.cursor) page.style.cursor = ""; return; }
    // 커서 힌트 히트테스트(elementFromPoint+gBCR)는 rAF 스로틀 — 게임패드 커서가 연속 이동해도 프레임당 1회만
    hoverEv = e; if (hoverRaf) return;
    hoverRaf = requestAnimationFrame(function () {
      hoverRaf = null; if (drag || !hoverEv) return;
      var hit = edgeAt(hoverEv), cur = hit ? (hit.type === "col" ? "col-resize" : "row-resize") : "";
      if (page.style.cursor !== cur) page.style.cursor = cur;   // 변경 시에만 대입(리플로우 절감)
    });
  });
  var hoverRaf = null, hoverEv = null;
  page.addEventListener("pointerdown", function (e) {            // capture=true: 셀 경계 리사이즈만 가로챔(셀 편집·캐럿은 그대로)
    var hit = edgeAt(e); if (!hit) return;
    e.preventDefault(); e.stopPropagation();
    try { page.setPointerCapture(e.pointerId); } catch (x) {}   // 포인터가 page 밖으로 나가도 드래그 유지
    drag = hit.type === "col" ? { type: "col", td: hit.td, x0: e.clientX, w0: hit.td.getBoundingClientRect().width, pid: e.pointerId }
                              : { type: "row", tr: hit.tr, y0: e.clientY, h0: hit.tr.getBoundingClientRect().height, pid: e.pointerId };
  }, true);
  window.addEventListener("pointerup", function () { if (drag) { try { page.releasePointerCapture(drag.pid); } catch (x) {} drag = null; page.style.cursor = ""; tEmit(); } });

  // ── 표 배치(개체) — HWP 3모드: 글자처럼/고정/어울림(2026-07-13: data-place 단일화 + 명시 3버튼) ──
  var PLACES = ["char", "fixed", "float"];
  var PLABEL = { char: "글자처럼 · 정렬로 좌우/가운데", fixed: "고정 · 손잡이로 끌어 이동", float: "문단(어울림) · 끌면 좌/우 자동전환, 글자가 표를 피해감" };
  var freeTable = null, tblhandle = null, hdrag = null;
  function objline(t) { var p = t.parentNode; return (p && p.classList && p.classList.contains("objline")) ? p : null; }
  function wrapObjline(t) { var o = objline(t); if (o) return o; o = document.createElement("div"); o.className = "objline"; t.parentNode.insertBefore(o, t); o.appendChild(t); return o; }
  function unwrapObjline(t) { var o = objline(t); if (!o) return; o.parentNode.insertBefore(t, o); if (!o.childNodes.length) o.parentNode.removeChild(o); }
  function tmode(t) { return t.getAttribute("data-place") || "char"; }
  function setMode(t, m) {
    var cur = getCell();                               // wrap/unwrap DOM 이동이 캐럿을 지움 → 전환 후 복원
    t.style.left = ""; t.style.top = ""; t.style.cssFloat = ""; t.style.marginLeft = ""; t.style.marginRight = ""; t.style.marginTop = "";   // 모드 전환 시 float용 margin 오프셋도 초기화
    if (m === "char") { wrapObjline(t); t.setAttribute("data-place", "char"); }   // 3모드 data-place 단일화(char도 명시 — 저장/복붙 왕복 안정)
    else if (m === "fixed") { var ol = t.offsetLeft, ot = t.offsetTop; unwrapObjline(t); t.setAttribute("data-place", "fixed"); t.style.left = ol + "px"; t.style.top = ot + "px"; }
    else { unwrapObjline(t); t.setAttribute("data-place", "float"); }
    var back = (cur && t.contains(cur)) ? cur : t.querySelector("td,th");   // 캐럿 유실 시 표 도구막대가 사라져 연속 전환 불가(2026-07-06 버그)
    if (back) { var r = document.createRange(); r.selectNodeContents(back); r.collapse(true); var s = window.getSelection(); s.removeAllRanges(); s.addRange(r); }
  }
  function setPlace() {   // (하위호환·e2e) 순환 토글 — UI는 명시 3버튼(placeChar/Fixed/Float)으로 대체됨
    var t = selTable(); if (!t) { toastMsg("표 안에 커서를 두세요"); return; }
    var nx = PLACES[(PLACES.indexOf(tmode(t)) + 1) % PLACES.length];
    setMode(t, nx); tEmit(); updateFree(); toastMsg("표 배치: " + PLABEL[nx]);
  }
  function setModeUser(m) {   // 명시 배치 선택(글자처럼/고정/어울림) — 현재 모드 버튼 하이라이트로 항상 가시
    var t = selTable(); if (!t) { toastMsg("표 안에 커서를 두세요"); return; }
    setMode(t, m); tEmit(); updateFree(); toastMsg("표 배치: " + PLABEL[m]);
  }
  // ── 글자처럼 정렬(왼/가운데/오른) — objline.textAlign. 툴바 ↤▣↦ + 키보드 Ctrl+Shift+L/C/R(표 전체선택 시) ──
  function alignCharTable(al, t) {
    t = t || selTable(); if (!t) { toastMsg("표 안에 커서를 두세요"); return false; }
    if (tmode(t) !== "char") setMode(t, "char");        // 글자처럼(inline) 모드 보장
    wrapObjline(t).style.textAlign = al; tEmit(); updateFree();
    toastMsg("표 정렬: " + ({ left: "왼쪽", center: "가운데", right: "오른쪽" }[al] || al));
    return true;
  }
  // 표 전체선택(개체) 상태서 정렬키가 오면 셀 아닌 표(objline) 정렬로 라우팅. char 모드일 때만(HWP 동일).
  function alignTableIfSelected(al) {
    var t = selectedTableNode(); if (!t || tmode(t) !== "char") return false;
    alignCharTable(al, t); return true;
  }
  // ── 표 개체 복사/오리기(2026-07-13 P0): 내부 클립보드 WP._objClip(권한 불필요) + 시스템 클립보드 best-effort ──
  function serializeTable(t) {   // char=objline 래퍼까지, fixed/float=table(data-place+style 포함)
    if (tmode(t) === "char") return wrapObjline(t).outerHTML;
    return t.outerHTML;
  }
  function setObjClip(t) {
    var html = serializeTable(t);
    window.WP._objClip = { kind: "table", html: html };
    try {   // 시스템 클립보드도 채워 두면 물리 Ctrl+V·타앱 붙이기 가능(실패 무해)
      if (navigator.clipboard && navigator.clipboard.write && window.ClipboardItem)
        navigator.clipboard.write([new window.ClipboardItem({ "text/html": new Blob([html], { type: "text/html" }), "text/plain": new Blob([t.innerText || ""], { type: "text/plain" }) })]).catch(function () {});
    } catch (e) {}
    return html;
  }
  function dropTable(t) {   // 표 제거 + 형제/문단으로 캐럿(delTable 로직 공유)
    var focusEl = t.nextElementSibling || t.previousElementSibling;
    t.parentNode.removeChild(t);
    if (!page.querySelector("p,h1,h2,h3,table,ul,ol,li")) WP.resetPageKeepMeta();
    focusEl = focusEl || page.querySelector("p") || page.firstElementChild;
    if (focusEl) { var r = document.createRange(); r.selectNodeContents(focusEl); r.collapse(true); var s = window.getSelection(); s.removeAllRanges(); s.addRange(r); }
    page.focus(); updateFree(); tEmit();
  }
  // ── 셀블록 복사(A: 떼어 새 표 / C: 다른 표 셀 채움) — 2026-07-17 ──
  function serializeCellBlock() {   // 선택된 .cb-sel 사각형 → 독립 <table> HTML(내용+셀 style 보존, 스팬 블록경계 클램프)
    var cells = cbList(); if (!cells.length) return null;
    var t = cells[0].closest("table"), grid = buildGrid(t);
    var minR = Infinity, minC = Infinity, maxR = -1, maxC = -1;
    cells.forEach(function (td) { var p = cellPos(grid, td); if (!p) return; var cs = td.colSpan || 1, rs = td.rowSpan || 1; minR = Math.min(minR, p.r); minC = Math.min(minC, p.c); maxR = Math.max(maxR, p.r + rs - 1); maxC = Math.max(maxC, p.c + cs - 1); });
    if (maxR < 0) return null;
    var covered = {}, html = '<table class="wp-block">';
    for (var r = minR; r <= maxR; r++) {
      html += "<tr>";
      for (var c = minC; c <= maxC; c++) {
        if (covered[r + "," + c]) continue;
        var cell = grid[r] && grid[r][c];
        if (!cell) { html += "<td><br></td>"; continue; }
        var p2 = cellPos(grid, cell.td);
        if (!p2 || p2.r < minR || p2.c < minC) { html += "<td><br></td>"; continue; }   // 블록 밖에서 걸친 병합 조각 → 빈칸
        var cs2 = Math.min(cell.td.colSpan || 1, maxC - p2.c + 1), rs2 = Math.min(cell.td.rowSpan || 1, maxR - p2.r + 1);
        for (var dr = 0; dr < rs2; dr++) for (var dc = 0; dc < cs2; dc++) covered[(r + dr) + "," + (c + dc)] = true;
        var tag = cell.td.tagName.toLowerCase(), at = "";
        if (cs2 > 1) at += ' colspan="' + cs2 + '"';
        if (rs2 > 1) at += ' rowspan="' + rs2 + '"';
        var st = cell.td.getAttribute("style"); if (st) at += ' style="' + st.replace(/"/g, "&quot;") + '"';
        html += "<" + tag + at + ">" + cell.td.innerHTML + "</" + tag + ">";
      }
      html += "</tr>";
    }
    return html + "</table>";
  }
  function blockText() { return cbList().map(function (td) { return td.innerText || ""; }).join("\t"); }
  function copyCellBlock() {
    var html = serializeCellBlock(); if (!html) { toastMsg("셀을 먼저 선택하세요(F5)"); return false; }
    window.WP._objClip = { kind: "cells", html: html };   // 본문 붙이기=새 표(A) 경로 재사용
    window.WP._objClipPreferred = true;
    try {
      if (navigator.clipboard && navigator.clipboard.write && window.ClipboardItem)
        navigator.clipboard.write([new window.ClipboardItem({ "text/html": new Blob([html], { type: "text/html" }), "text/plain": new Blob([blockText()], { type: "text/plain" }) })]).catch(function () {});
    } catch (e) {}
    return true;
  }
  function fillCellsFrom(targetCell, tableHTML) {   // C: 복사 셀블록을 대상 표에 좌상단 기준 채움(범위 초과 무시)
    var tmp = document.createElement("div"); tmp.innerHTML = tableHTML; var src = tmp.querySelector("table"); if (!src) return false;
    var sg = buildGrid(src), SR = sg.length, SC = gridCols(sg);
    var t = targetCell.closest("table"), tg = buildGrid(t), p = cellPos(tg, targetCell); if (!p) return false;
    for (var r = 0; r < SR; r++) for (var c = 0; c < SC; c++) {
      var scell = sg[r] && sg[r][c]; if (!scell || !scell.master) continue;
      var tcell = tg[p.r + r] && tg[p.r + r][p.c + c]; if (!tcell || !tcell.master) continue;
      tcell.td.innerHTML = scell.td.innerHTML;
      var st = scell.td.getAttribute("style"); if (st) tcell.td.setAttribute("style", st); else tcell.td.removeAttribute("style");
    }
    markDirty(); if (WP.histRecord) WP.histRecord(); tEmit(); return true;
  }
  // wp-core 붙이기 라우팅용: 캐럿이 표 셀 안이면 셀블록을 그 셀 기준으로 채움(C), 아니면 false(→본문에 새 표=A)
  WP.fillCellBlockAtCaret = function (html) { var cell = getCell(); if (!cell) return false; return fillCellsFrom(cell, html); };
  WP.caretInCell = function () { return !!getCell(); };
  // ── 표 나누기(표-)/붙이기(표+) — 2026-07-17 ──
  function regionTableHTML(t, grid, r0, r1) {   // grid 행 r0..r1 → 독립 <table>(위에서 걸친 병합=빈칸, 스팬 하단 클램프, colgroup/표 style 보존)
    var cols = gridCols(grid), covered = {}, st0 = t.getAttribute("style");
    var html = "<table" + (st0 ? ' style="' + st0.replace(/"/g, "&quot;") + '"' : "") + ">";
    var cg = t.querySelector("colgroup"); if (cg) html += cg.outerHTML;
    for (var r = r0; r <= r1; r++) {
      var trr = t.rows[r], drow = trr && trr.getAttribute("data-row");   // 임베드 양식 속성(data-row/field/sum) 보존
      html += "<tr" + (drow ? ' data-row="' + WP.esc(drow) + '"' : "") + ">";
      for (var c = 0; c < cols; c++) {
        if (covered[r + "," + c]) continue;
        var cell = grid[r] && grid[r][c];
        if (!cell) { html += "<td><br></td>"; continue; }
        var p = cellPos(grid, cell.td);
        if (!p || p.r < r0) { html += "<td><br></td>"; continue; }
        var cs = cell.td.colSpan || 1, rs = Math.min(cell.td.rowSpan || 1, r1 - p.r + 1);
        for (var dr = 0; dr < rs; dr++) for (var dc = 0; dc < cs; dc++) covered[(r + dr) + "," + (c + dc)] = true;
        var tag = cell.td.tagName.toLowerCase(), at = "";
        if (cs > 1) at += ' colspan="' + cs + '"';
        if (rs > 1) at += ' rowspan="' + rs + '"';
        var stc = cell.td.getAttribute("style"); if (stc) at += ' style="' + stc.replace(/"/g, "&quot;") + '"';
        ["data-field", "data-sum"].forEach(function (k) { var v = cell.td.getAttribute(k); if (v !== null) at += " " + k + '="' + WP.esc(v) + '"'; });
        html += "<" + tag + at + ">" + cell.td.innerHTML + "</" + tag + ">";
      }
      html += "</tr>";
    }
    return html + "</table>";
  }
  function isEmptyPara(el) { return el && el.nodeType === 1 && /^(P|DIV)$/.test(el.tagName) && !el.classList.contains("objline") && !el.querySelector("table,img,.wp-obj") && !(el.textContent || "").trim(); }
  function splitTable() {   // 표- : 캐럿 행 기준 위/아래 두 표로 분리
    var c = getCell(); if (!c) { toastMsg("표 안에 커서를 두세요"); return; }
    var t = c.closest("table"); if (tmode(t) !== "char") { toastMsg("글자처럼 배치 표에서만 나눌 수 있습니다"); return; }
    var tr = c.closest("tr"), rows = Array.prototype.slice.call(t.rows), ri = rows.indexOf(tr);
    if (ri <= 0) { toastMsg("첫 행에서는 나눌 수 없습니다"); return; }
    var grid = buildGrid(t);
    var t2html = regionTableHTML(t, grid, ri, rows.length - 1);
    var seen = [];   // 경계 가로지르는 rowspan을 위 표에서 ri-1까지로 클램프
    for (var r = 0; r < ri; r++) for (var cc = 0; grid[r] && cc < grid[r].length; cc++) {
      var g = grid[r][cc]; if (!g || !g.master || seen.indexOf(g.td) >= 0) continue; seen.push(g.td);
      var p = cellPos(grid, g.td); if (p && (p.r + (g.td.rowSpan || 1) - 1) >= ri) g.td.rowSpan = ri - p.r;
    }
    for (var k = rows.length - 1; k >= ri; k--) rows[k].parentNode.removeChild(rows[k]);
    var tBlock = t.closest(".objline") || t;
    var tmp = document.createElement("div"); tmp.innerHTML = t2html; var t2 = tmp.firstChild;
    var oline = document.createElement("div"); oline.className = "objline"; t2.setAttribute("data-place", "char"); oline.appendChild(t2);
    var para = document.createElement("div"); para.className = "wp-p"; para.innerHTML = "<br>";
    tBlock.parentNode.insertBefore(para, tBlock.nextSibling);
    tBlock.parentNode.insertBefore(oline, para.nextSibling);
    var fc = t2.querySelector("td,th"); if (fc) { var rg = document.createRange(); rg.selectNodeContents(fc); rg.collapse(true); var s = window.getSelection(); s.removeAllRanges(); s.addRange(rg); }
    page.focus(); markDirty(); if (WP.histRecord) WP.histRecord(); tEmit(); toastMsg("표 나눔");
  }
  function joinTable() {   // 표+ : 바로 아래 표와 합침(사이 빈문단 무시, 열 수 동일 시)
    var c = getCell(); if (!c) { toastMsg("표 안에 커서를 두세요"); return; }
    var t = c.closest("table"); if (tmode(t) !== "char") { toastMsg("글자처럼 배치 표에서만 붙일 수 있습니다"); return; }
    var tBlock = t.closest(".objline") || t, nb = tBlock.nextElementSibling;
    while (nb && isEmptyPara(nb)) nb = nb.nextElementSibling;
    var t2 = null;
    if (nb) { if (nb.tagName === "TABLE") t2 = nb; else if (nb.classList.contains("objline")) t2 = nb.querySelector("table"); }
    if (!t2) { toastMsg("아래에 붙일 표가 없습니다"); return; }
    if (gridCols(buildGrid(t)) !== gridCols(buildGrid(t2))) { toastMsg("열 수가 달라 붙일 수 없습니다"); return; }
    var tb = t.rows.length ? t.rows[t.rows.length - 1].parentNode : t;
    var firstNew = t2.rows[0] ? t2.rows[0].cells[0] : null;
    Array.prototype.slice.call(t2.rows).forEach(function (r) { tb.appendChild(r); });
    var t2Block = t2.closest(".objline") || t2, n = tBlock.nextSibling;
    while (n && n !== t2Block) { var nx = n.nextSibling; if ((n.nodeType === 1 && isEmptyPara(n)) || (n.nodeType === 3 && !(n.textContent || "").trim())) n.parentNode.removeChild(n); n = nx; }
    t2Block.parentNode.removeChild(t2Block);
    if (firstNew) { var rg = document.createRange(); rg.selectNodeContents(firstNew); rg.collapse(true); var s = window.getSelection(); s.removeAllRanges(); s.addRange(rg); }
    page.focus(); markDirty(); if (WP.histRecord) WP.histRecord(); tEmit(); toastMsg("표 붙임");
  }
  function copyTable() {
    if (cb && cbList().length) { if (copyCellBlock()) toastMsg("셀블록 복사됨 · 표 셀에 붙이면 채우고, 본문에 붙이면 새 표"); return; }
    var t = selTable() || selectedTableNode(); if (!t) { toastMsg("표 안에 커서를 두세요"); return; }
    setObjClip(t); toastMsg("표 복사됨 · 붙이기(Ctrl+V)로 삽입");
  }
  function cutTable() {
    var t = selTable() || selectedTableNode(); if (!t) { toastMsg("표 안에 커서를 두세요"); return; }
    setObjClip(t); dropTable(t); toastMsg("표 오려둠 · 붙이기(Ctrl+V)로 삽입");
  }
  // 물리 Ctrl+C/X: 표 전체선택 상태면 내부 개체 클립보드에도 적재(우클릭 붙이기 폴백 보장)
  document.addEventListener("copy", function (e) {
    if (cb && cbList().length) {   // 셀블록 활성 → 블록 복사(A/C)
      var bhtml = serializeCellBlock(); if (bhtml) {
        window.WP._objClip = { kind: "cells", html: bhtml }; window.WP._objClipPreferred = true;
        try { e.clipboardData.setData("text/html", bhtml); e.clipboardData.setData("text/plain", blockText()); e.preventDefault(); } catch (x) {}
        toastMsg("셀블록 복사됨 · 표 셀에 붙이면 채우고, 본문에 붙이면 새 표"); return;
      }
    }
    var t = selectedTableNode(); if (!t) return;
    var html = serializeTable(t); window.WP._objClip = { kind: "table", html: html };
    try { e.clipboardData.setData("text/html", html); e.clipboardData.setData("text/plain", t.innerText || ""); e.preventDefault(); } catch (x) {}
  });
  document.addEventListener("cut", function (e) {
    var t = selectedTableNode(); if (!t) return;
    var html = serializeTable(t); window.WP._objClip = { kind: "table", html: html };
    try { e.clipboardData.setData("text/html", html); e.clipboardData.setData("text/plain", t.innerText || ""); e.preventDefault(); } catch (x) {}
    dropTable(t); toastMsg("표 오려둠");
  });
  function ensureHandle() {
    if (tblhandle) return;
    tblhandle = document.createElement("div"); tblhandle.id = "tblhandle"; tblhandle.className = "hidden"; tblhandle.textContent = "✥";
    tblhandle.title = "표 이동(끌기)"; document.body.appendChild(tblhandle);
    tblhandle.addEventListener("pointerdown", function (e) {
      if (!freeTable) return; e.preventDefault(); e.stopPropagation();
      // 핸들은 fixed/float에서만 뜸(char은 자동전환 안 함 — 배치는 명시 선택, 2026-07-13)
      var pg = document.getElementById("page"), pr = pg.getBoundingClientRect(), tr = freeTable.getBoundingClientRect();
      var padL = parseFloat(getComputedStyle(pg).paddingLeft) || 0;
      hdrag = { x: e.clientX, y: e.clientY, mode: tmode(freeTable),
        l: parseFloat(freeTable.style.left) || 0, t: parseFloat(freeTable.style.top) || 0,
        mt: parseFloat(freeTable.style.marginTop) || 0,
        dl: tr.left - pr.left - padL };   // 표의 페이지 콘텐츠박스 기준 시작 left(어울림 좌/우 자동전환용)
      try { tblhandle.setPointerCapture(e.pointerId); } catch (x) {}
    });
    tblhandle.addEventListener("pointermove", function (e) {
      if (!hdrag || !freeTable) return; e.preventDefault();
      var dx = e.clientX - hdrag.x, dy = e.clientY - hdrag.y;
      if (hdrag.mode === "float") {   // 어울림: 개체 위치에 따라 float 방향 자동전환 → 글이 항상 넓은 쪽으로 흐름(빈공간 최소화)
        var pg = document.getElementById("page"), pcs = getComputedStyle(pg);
        var contentW = pg.clientWidth - (parseFloat(pcs.paddingLeft) || 0) - (parseFloat(pcs.paddingRight) || 0);
        var tw = freeTable.offsetWidth;
        var dl = Math.max(0, Math.min(Math.max(0, contentW - tw), hdrag.dl + dx));   // 콘텐츠박스 내 목표 left
        freeTable.style.marginTop = Math.max(0, hdrag.mt + dy) + "px";
        if (dl + tw / 2 <= contentW / 2) {   // 왼쪽 절반 → float:left, 글은 오른쪽
          freeTable.style.cssFloat = "left"; freeTable.style.marginLeft = dl + "px"; freeTable.style.marginRight = "14px";
        } else {                             // 오른쪽 절반 → float:right, 글은 왼쪽
          freeTable.style.cssFloat = "right"; freeTable.style.marginRight = Math.max(0, contentW - (dl + tw)) + "px"; freeTable.style.marginLeft = "14px";
        }
      } else {
        freeTable.style.left = Math.max(0, hdrag.l + dx) + "px";
        freeTable.style.top = Math.max(0, hdrag.t + dy) + "px";
      }
      placeHandle();
    });
    tblhandle.addEventListener("pointerup", function (e) { if (hdrag) { hdrag = null; markDirty(); } try { tblhandle.releasePointerCapture(e.pointerId); } catch (x) {} });
  }
  function placeHandle() {
    if (!freeTable) { if (tblhandle) tblhandle.classList.add("hidden"); if (WP.hideZButtons) WP.hideZButtons(); return; }
    var r = freeTable.getBoundingClientRect();
    tblhandle.style.left = r.left + "px"; tblhandle.style.top = Math.max(0, r.top - 16) + "px"; tblhandle.classList.remove("hidden");
    if (WP.showZButtons) WP.showZButtons([freeTable], r.left + 20, Math.max(0, r.top - 16));   // 이동핸들 옆 앞/뒤(표는 fixed/float에서만 핸들 뜸=글자처럼 자동숨김)
  }
  // 이동핸들은 fixed/float 모드에서만 표시(char은 정렬로 이동 — 2026-07-13 핸들규율)
  function updateFree() {
    ensureHandle(); var t = selTable(), m = t && tmode(t);
    freeTable = (m === "fixed" || m === "float") ? t : null; placeHandle();
  }
  function selectedTableNode() {   // 표 노드 하나만 range로 선택된 상태 판별(정렬·복사 라우팅용 — 삭제는 하지 않음)
    var s = window.getSelection(); if (!s.rangeCount) return null; var r = s.getRangeAt(0);
    if (r.collapsed || r.startContainer !== r.endContainer || (r.endOffset - r.startOffset) !== 1) return null;
    var node = r.startContainer.childNodes && r.startContainer.childNodes[r.startOffset];
    return (node && node.tagName === "TABLE") ? node : null;
  }
  // (2026-11-13) 구 "표 전체 range 선택 후 Delete=표 삭제" keydown 핸들러 제거 — 07-17 ▦선택 제거·07-19 삭제가드
  //   정책(range 삭제=텍스트만, 개체 삭제=개체선택+✕/Delete 또는 TT.delTable)과 충돌해 가드를 우회하던 근본원인.
  var ufRaf = null;   // #23: rAF 스로틀(프레임당 1회)
  document.addEventListener("selectionchange", function () {
    if (ufRaf) return;
    ufRaf = requestAnimationFrame(function () { ufRaf = null; updateFree(); });
  });
  var pwEl = document.getElementById("pagewrap"), phRaf = null;   // 스크롤 시 표 손잡이 재배치 rAF 스로틀
  if (pwEl) pwEl.addEventListener("scroll", function () { if (phRaf || !freeTable) return; phRaf = requestAnimationFrame(function () { phRaf = null; placeHandle(); }); });

  // 셀 테두리 대화상자(선 종류/굵기 커스텀 드롭다운 + 위치 즉시적용)
  (function () {
    var g = document.getElementById("m-cellborder"); if (!g) return;
    function closeLists() { g.querySelectorAll(".lsel-list").forEach(function (l) { l.classList.add("hidden"); }); }
    g.addEventListener("pointerup", function (e) {
      var tb = e.target.closest(".lsel-btn");
      if (tb) {                                      // 드롭다운 토글(다른 목록은 닫음)
        var list = tb.parentNode.querySelector(".lsel-list"), wasHidden = list.classList.contains("hidden");
        closeLists(); if (wasHidden) list.classList.remove("hidden");
        return;
      }
      var s = e.target.closest(".bstyle");
      if (s) { _bStyle = s.dataset.style; _syncBStyleUI(); closeLists(); return; }
      var w = e.target.closest(".bwidth");
      if (w) { _bWidth = +w.dataset.w; _syncBStyleUI(); closeLists(); return; }
      closeLists();                                  // 목록 밖 누르면 닫힘
      var p = e.target.closest(".bplace");
      if (p) {
        e.stopPropagation();
        g.querySelectorAll(".bplace").forEach(function (b) { b.classList.toggle("on", b === p); });
        applyBorderPreset(p.dataset.place);
      }
    });
  })();

  if (!WP._objClip) WP._objClip = null;                // 내부 개체 클립보드(표 등) — wp-core paste가 소비
  WP.TT = TT;
  WP.syncHiddenGuides = syncHiddenGuides;   // 로드/임포트 후 hidden 변 안내선 동기화(histReset이 호출. TT에 안 넣음=data-tt 교차검사 오탐 방지)
  syncHiddenGuides();                        // 초기 문서에도 1회
  WP.cellNav = cellNav;
  WP.selectedTable = selectedTableNode;                // 표 전체선택 판별(wp-core copy/cut·정렬 라우팅용)
  WP.alignTableIfSelected = alignTableIfSelected;      // Ctrl+Shift+L/C/R → 표 objline 정렬 라우팅(wp-format)
  // 표 명령(TT)은 표도구막대가 TT 객체로 직접 호출 — ACT 등록 불요(2026-07-16 셀메뉴 삭제로 ACT판 유일소비자 소멸).
  WP.register(TT);   // 표 명령을 ACT에 등록 → 툴바·우클릭 메뉴·단축키가 동일 명령/대상 공유(일관성, 웹포팅 복원 2026-07-17 — embed 분기가 미포함)
  WP.register({ table: function () { saveRange(); openModal("m-table"); }, tableApply: tableApply, splitApply: splitApply, borderDlg: openBorderDlg });
})();
