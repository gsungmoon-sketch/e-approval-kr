// 한워드 — 눈금자(들여쓰기 드래그) + Ctrl+휠 확대/축소 (HWP식).
//   상단 룰러의 ▼ 마커를 끌면 현재 문단 왼쪽 들여쓰기 조절. Ctrl+휠=문서 확대/축소.
(function () {
  "use strict";
  var WP = window.WP, page = WP.page;
  var pw = document.getElementById("pagewrap"), ruler = document.getElementById("ruler");
  if (!pw || !ruler) return;
  var marker = document.getElementById("indentMarker");

  // ── Ctrl+휠 확대/축소 ──
  var zoom = 1;
  pw.addEventListener("wheel", function (e) {
    if (!e.ctrlKey) return; e.preventDefault();
    zoom = Math.max(0.5, Math.min(2.5, zoom + (e.deltaY < 0 ? 0.1 : -0.1)));
    page.style.zoom = zoom.toFixed(2);
    WP.toast("확대 " + Math.round(zoom * 100) + "%");
    layout();
  }, { passive: false });

  // ── 눈금자: 페이지 본문 영역에 맞춰 눈금 + 들여쓰기 마커 ──
  function contentBox() {                       // 페이지 본문(패딩 안쪽) 좌표
    var r = page.getBoundingClientRect(), cs = getComputedStyle(page), rr = ruler.getBoundingClientRect();
    var left = r.left - rr.left + parseFloat(cs.paddingLeft) * zoom;
    var right = r.right - rr.left - parseFloat(cs.paddingRight) * zoom;
    return { left: left, width: Math.max(40, right - left) };
  }
  function layout() {
    var box = contentBox();
    // 눈금(약 1cm≈37.8px*zoom 간격)
    var step = 37.8 * zoom, ticks = "";
    for (var x = 0, i = 0; x <= box.width; x += step, i++)
      ticks += '<span class="tick" style="left:' + (box.left + x) + 'px">' + (i % 2 ? "" : i) + '</span>';   // 라벨=실제 cm(2cm 간격 표기 — 감사2차 P1-7 절반 스케일 교정)
    ruler.querySelector(".ticks").innerHTML = ticks;
    placeMarker();
  }
  function placeMarker() {
    var box = contentBox(), ind = (WP.curIndent ? WP.curIndent() : 0) * zoom;
    marker.style.left = (box.left + ind - 5) + "px";
  }
  // 마커 드래그
  var drag = null;
  marker.addEventListener("pointerdown", function (e) { e.preventDefault(); e.stopPropagation(); drag = { box: contentBox() }; marker.setPointerCapture(e.pointerId); });
  marker.addEventListener("pointermove", function (e) {
    if (!drag) return; e.preventDefault();
    var rr = ruler.getBoundingClientRect(), x = e.clientX - rr.left - drag.box.left;
    x = Math.max(0, Math.min(drag.box.width - 20, x));
    marker.style.left = (drag.box.left + x - 5) + "px";
    if (WP.setIndent) WP.setIndent(x / zoom);
  });
  marker.addEventListener("pointerup", function (e) { drag = null; try { marker.releasePointerCapture(e.pointerId); } catch (x) {} placeMarker(); });

  // #23: rAF 스로틀 — 연속 캐럿 이동(드래그 선택·키 반복) 시 프레임당 1회만 gBCR 측정
  var selRaf = null;
  document.addEventListener("selectionchange", function () {
    if (drag || selRaf) return;
    selRaf = requestAnimationFrame(function () { selRaf = null; if (!drag) placeMarker(); });
  });

  // ── 가로 스크롤 시 눈금자 동기화(스크롤마다 page 위치가 바뀌므로 ticks 재배치, rAF 스로틀) ──
  var raf = null;
  function onScroll() { if (raf) return; raf = requestAnimationFrame(function () { raf = null; layout(); }); }
  pw.addEventListener("scroll", onScroll);

  // ── 입력 중 캐럿이 가로 가시범위를 벗어나면 #pagewrap을 가로 스크롤해 따라가기(좁은 화면) ──
  function followCaretX() {
    if (drag) return;
    var s = window.getSelection(); if (!s.rangeCount || !page.contains(s.anchorNode)) return;
    var rc = s.getRangeAt(0).cloneRange(), rect = rc.getClientRects()[0] || rc.getBoundingClientRect();
    if (!rect || (!rect.width && !rect.height && !rect.left)) return;
    var box = pw.getBoundingClientRect(), pad = 28;
    if (rect.left < box.left + pad) pw.scrollLeft -= (box.left + pad - rect.left);
    else if (rect.right > box.right - pad) pw.scrollLeft += (rect.right - (box.right - pad));
  }
  page.addEventListener("input", followCaretX);
  page.addEventListener("keyup", function (e) { if (e.key && e.key.indexOf("Arrow") === 0) followCaretX(); });

  window.addEventListener("resize", layout);
  layout();
  WP.rulerLayout = layout;
})();
