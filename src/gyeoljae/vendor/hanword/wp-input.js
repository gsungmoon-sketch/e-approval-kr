// 한워드 — 마우스/터치 액션(아래한글식): 드래그 선택·더블탭 단어·트리플 문단·우클릭/롱프레스 메뉴·Shift+클릭 확장.
(function () {
  "use strict";
  var WP = window.WP, page = WP.page, $ = WP.$, dispatch = WP.dispatch;
  var ctx = $("ctxmenu");
  function caretAt(x, y) { return document.caretRangeFromPoint ? document.caretRangeFromPoint(x, y) : null; }

  // ── 드래그 선택 ──
  var down = null, anchor = null, moved = false;
  page.addEventListener("pointerdown", function (e) {
    if (e.button === 2) return;                       // 우클릭=메뉴
    down = { x: e.clientX, y: e.clientY }; moved = false; anchor = caretAt(e.clientX, e.clientY);
  });
  page.addEventListener("pointermove", function (e) {
    if (!down || !anchor) return;
    if (!moved && Math.abs(e.clientX - down.x) + Math.abs(e.clientY - down.y) < 6) return;
    moved = true;
    var f = caretAt(e.clientX, e.clientY); if (!f) return;
    var sel = document.createRange();
    if (anchor.compareBoundaryPoints(Range.START_TO_START, f) <= 0) { sel.setStart(anchor.startContainer, anchor.startOffset); sel.setEnd(f.startContainer, f.startOffset); }
    else { sel.setStart(f.startContainer, f.startOffset); sel.setEnd(anchor.startContainer, anchor.startOffset); }
    var s = window.getSelection(); s.removeAllRanges(); s.addRange(sel); e.preventDefault();
  });
  page.addEventListener("pointerup", function () { down = null; anchor = null; });

  // ── 더블탭=단어 / 트리플탭=문단 ──
  var taps = 0, tapTimer = null, tapPt = null;
  page.addEventListener("pointerdown", function (e) {
    if (e.button === 2) return;
    if (tapPt && Math.abs(e.clientX - tapPt.x) + Math.abs(e.clientY - tapPt.y) > 12) taps = 0;
    taps++; tapPt = { x: e.clientX, y: e.clientY };
    clearTimeout(tapTimer); tapTimer = setTimeout(function () { taps = 0; }, 400);
    if (taps === 2) selectWord(e.clientX, e.clientY);
    else if (taps >= 3) { selectPara(e.clientX, e.clientY); taps = 0; }
  });
  function selectWord(x, y) {
    var r = caretAt(x, y); if (!r) return;
    var s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
    if (s.modify) { s.modify("move", "backward", "word"); s.modify("extend", "forward", "word"); }
  }
  function selectPara(x, y) {
    var r = caretAt(x, y); if (!r) return; var n = r.startContainer; if (n.nodeType === 3) n = n.parentNode;
    var b = n && n.closest ? n.closest("p,h1,h2,h3,li,td,div") : null; if (!b) return;
    var rg = document.createRange(); rg.selectNodeContents(b); var s = window.getSelection(); s.removeAllRanges(); s.addRange(rg);
  }

  // ── Shift+클릭: 선택 확장 ──
  page.addEventListener("click", function (e) {
    if (!e.shiftKey) return;
    var f = caretAt(e.clientX, e.clientY), s = window.getSelection();
    if (f && s.rangeCount) { try { s.extend(f.startContainer, f.startOffset); e.preventDefault(); } catch (x) {} }
  });

  // ── 컨텍스트 메뉴(우클릭 / 롱프레스) ──
  function showCtx(x, y) {
    ctx.classList.remove("hidden");
    var w = ctx.offsetWidth || 150, h = ctx.offsetHeight || 220;
    ctx.style.left = Math.max(2, Math.min(x, window.innerWidth - w - 4)) + "px";
    ctx.style.top = Math.max(2, Math.min(y, window.innerHeight - h - 4)) + "px";
  }
  var ctxArmed = false;
  function hideCtx() { ctx.classList.add("hidden"); ctxArmed = false; }
  var ctxTbl = $("ctx-tbl");
  function showCtxAt(x, y, target) {
    // 표 안이면 표 명령 섹션 노출. 셀 블록이 없으면 우클릭 위치 셀에 캐럿(명령 대상 지정, HWP식)
    var td = target && target.closest ? target.closest("td,th") : null;
    var blockActive = WP.cellBlockActive && WP.cellBlockActive();
    if (td && !blockActive) { var r = caretAt(x, y); if (r) { var s = window.getSelection(); s.removeAllRanges(); s.addRange(r); } }
    if (ctxTbl) ctxTbl.classList.toggle("hidden", !td);
    showCtx(x, y);
  }
  page.addEventListener("contextmenu", function (e) { e.preventDefault(); showCtxAt(e.clientX, e.clientY, e.target); });
  // 폴백: 게임패드→마우스 매퍼 등 우측 버튼이 contextmenu 이벤트를 안 만드는 기기 — 우측 버튼 pointerup(button 2)으로도 메뉴 열기.
  //   #ctxmenu는 #page 밖 요소라 메뉴 위 pointerup은 여기로 안 옴 → 항목 선택(ctx 자체 핸들러)과 충돌 없음. contextmenu가 정상인 기기는 같은 위치 재오픈(무해).
  page.addEventListener("pointerup", function (e) { if (e.button === 2) { e.preventDefault(); showCtxAt(e.clientX, e.clientY, e.target); } });
  ctx.addEventListener("pointerdown", function (e) { e.preventDefault(); ctxArmed = true; });   // 메뉴 위에서 누름 시작 표시(선택영역 유지)
  ctx.addEventListener("pointerup", function (e) {
    if (!ctxArmed) return;   // 메뉴를 연 우클릭 자체의 pointerup(메뉴 위에서 시작 안 함)은 무시 → 즉시 실행/닫힘 방지
    ctxArmed = false;
    var b = e.target.closest("[data-act]"); if (b) { hideCtx(); dispatch(b.getAttribute("data-act")); }
  });
  document.addEventListener("pointerdown", function (e) { if (!ctx.contains(e.target)) hideCtx(); }, true);
  document.addEventListener("scroll", hideCtx, true);
  var lp = null;
  page.addEventListener("touchstart", function (e) { var t = e.touches[0]; lp = setTimeout(function () { showCtxAt(t.clientX, t.clientY, document.elementFromPoint(t.clientX, t.clientY)); }, 500); }, { passive: true });
  page.addEventListener("touchmove", function () { clearTimeout(lp); });
  page.addEventListener("touchend", function () { clearTimeout(lp); });
})();
