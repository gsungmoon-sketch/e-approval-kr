// 한워드 — 전체보기(쪽 맞춤): 한 화면에 종이 한 쪽을 통째로 축소 표시, 폭이 남으면 2쪽 펼침(2026-07-18).
//   원리: 편집기 페이지네이션(pgfill 스페이서)으로 쪽 시작이 n×pageH에 정규화된 #page를 그대로 클론,
//   슬롯(overflow:hidden 클립창) 안에서 scale+translateY(-n×pageH)로 해당 쪽만 창에 노출.
//   해상도 실측(오버레이 clientWidth/Height)으로 배율 계산 → 2×종이폭+간격이 들어가면 2쪽 배치.
//   조작: ←→/↑↓/휠/화면 좌·우 탭=쪽 이동, Esc·Enter·더블클릭=복귀. 읽기전용(편집 잠금).
(function () {
  "use strict";
  var WP = window.WP, page = WP.page;
  var GAP = 14;                       // 2쪽 사이 간격(px, 화면 기준)
  var st = null;                      // 활성 상태 {slots, slotEls, cur, total, pageH, padT, scale}

  function ratio() { return (WP.paperRatio ? WP.paperRatio() : 257 / 170); }

  function ensureOverlay() {
    var ov = document.getElementById("fullview");
    if (ov) return ov;
    ov = document.createElement("div"); ov.id = "fullview"; ov.className = "hidden"; ov.setAttribute("tabindex", "-1");
    ov.innerHTML = '<div id="fvpages"></div><div id="fvbadge"></div>';
    document.body.appendChild(ov);
    ov.addEventListener("dblclick", exitFullview);
    ov.addEventListener("wheel", function (e) { e.preventDefault(); go(e.deltaY > 0 ? 1 : -1); }, { passive: false });
    ov.addEventListener("pointerup", function (e) {   // 좌측 탭=이전, 우측 탭=다음(전자책식)
      if (e.target.closest && e.target.closest("#fvbadge")) { exitFullview(); return; }
      go(e.clientX < ov.clientWidth / 2 ? -1 : 1);
    });
    return ov;
  }

  function buildSlot(scale, paperW, paperH, padT, padB, html, cs) {
    var clip = document.createElement("div"); clip.className = "fvclip";
    clip.style.width = Math.round(paperW * scale) + "px";
    clip.style.height = Math.round(paperH * scale) + "px";
    var host = document.createElement("div"); host.className = "fvhost";
    host.style.width = paperW + "px";
    host.style.paddingTop = cs.paddingTop; host.style.paddingRight = cs.paddingRight;
    host.style.paddingBottom = cs.paddingBottom; host.style.paddingLeft = cs.paddingLeft;
    host.style.fontSize = cs.fontSize; host.style.lineHeight = cs.lineHeight;
    host.style.fontFamily = cs.fontFamily; host.style.color = cs.color; host.style.boxSizing = cs.boxSizing;
    host.style.transform = "scale(" + scale + ")";
    host.innerHTML = html;
    clip.appendChild(host);
    // 여백 덮개: translate로 이전/다음 쪽 내용이 상하 여백 띠에 비치는 것 차단
    var top = document.createElement("div"); top.className = "fvmask"; top.style.cssText = "top:0;height:" + Math.round(padT * scale) + "px";
    var bot = document.createElement("div"); bot.className = "fvmask"; bot.style.cssText = "bottom:0;height:" + Math.round(padB * scale) + "px";
    clip.appendChild(top); clip.appendChild(bot);
    return clip;
  }

  function layout() {
    var ov = ensureOverlay(), wrap = document.getElementById("fvpages");
    var cs = getComputedStyle(page);
    var padL = parseFloat(cs.paddingLeft) || 0, padR = parseFloat(cs.paddingRight) || 0,
        padT = parseFloat(cs.paddingTop) || 0, padB = parseFloat(cs.paddingBottom) || 0;
    var paperW = page.clientWidth;                       // 종이 폭(여백 포함)
    var contentW = paperW - padL - padR, pageH = contentW * ratio();
    if (pageH < 60) return false;
    var contentH = page.scrollHeight - padT - padB;
    var total = Math.max(1, Math.ceil((contentH - 6) / pageH));
    var paperH = padT + pageH + padB;
    // 해상도 실측 → 배율·슬롯수(요청: 폭이 넓으면 2쪽 펼침 검토)
    var availW = ov.clientWidth - 16, availH = ov.clientHeight - 30;   // 배지 여유
    var s1 = Math.min(availH / paperH, availW / paperW);               // 1쪽 최대 배율
    var s2 = Math.min(availH / paperH, (availW - GAP) / (paperW * 2)); // 2쪽 배율
    var slots = (total > 1 && s2 >= s1 * 0.85) ? 2 : 1;                // 쪽 크기 15% 이내 손해면 2쪽 펼침
    var scale = (slots === 2) ? s2 : s1;
    var html = page.innerHTML;                            // ⚠️pgfill 스페이서 포함(쪽 정렬의 핵심) — cleanedHTML 금지
    wrap.innerHTML = "";
    var slotEls = [];
    for (var i = 0; i < slots; i++) { var c = buildSlot(scale, paperW, paperH, padT, padB, html, cs); wrap.appendChild(c); slotEls.push(c); }
    st = { slots: slots, slotEls: slotEls, cur: st ? Math.min(st.cur, total - 1) : 0, total: total, pageH: pageH, padT: padT, scale: scale };
    show();
    return true;
  }

  function show() {
    if (!st) return;
    for (var i = 0; i < st.slots; i++) {
      var n = st.cur + i, host = st.slotEls[i].querySelector(".fvhost");
      st.slotEls[i].style.visibility = (n < st.total) ? "visible" : "hidden";   // 마지막 홀수쪽=오른쪽 빈칸
      host.style.transform = "scale(" + st.scale + ") translateY(" + (-Math.round(n * st.pageH)) + "px)";
    }
    var b = document.getElementById("fvbadge");
    var label = (st.slots === 2 && st.cur + 1 < st.total) ? (st.cur + 1) + "-" + (st.cur + 2) : String(st.cur + 1);
    b.textContent = label + " / " + st.total + "쪽 · 탭=넘김 · Esc=닫기";
  }

  function go(d) {
    if (!st) return;
    var step = st.slots, next = st.cur + d * step;
    if (next < 0) next = 0;
    if (next >= st.total) next = st.cur;                 // 끝 넘어가면 유지
    if (next !== st.cur) { st.cur = next; show(); }
  }

  function enterFullview() {
    if (WP.fullviewActive) return;
    if (WP.previewActive && WP.exitPreview) WP.exitPreview();
    if (!document.body.classList.contains("pageguides")) { document.body.classList.add("pageguides"); }
    if (WP.pageGuides) WP.pageGuides();                  // 스페이서 최신화(쪽 정렬 보장)
    if (WP.saveRange) WP.saveRange();
    var ov = ensureOverlay();
    document.body.classList.add("zen");
    page.setAttribute("contenteditable", "false");
    ov.classList.remove("hidden");                       // 측정 위해 먼저 표시
    if (!layout()) { exitFullview(); WP.toast && WP.toast("전체보기 불가(쪽 계산 실패)"); return; }
    WP.fullviewActive = true; ov.focus();
    window.addEventListener("resize", layout);
    WP.toast && WP.toast("전체보기 — 좌우 탭/휠=쪽 넘김, Esc=닫기");
  }
  function exitFullview() {                              // 멱등
    var ov = document.getElementById("fullview");
    if (ov) { ov.classList.add("hidden"); var w = document.getElementById("fvpages"); if (w) w.innerHTML = ""; }
    window.removeEventListener("resize", layout);
    st = null; WP.fullviewActive = false;
    document.body.classList.remove("zen");
    page.setAttribute("contenteditable", "true");
    if (WP.restoreRange) WP.restoreRange();
  }

  document.addEventListener("keydown", function (e) {   // 전체보기 중 키 조작(캡처=편집기 핫키보다 우선)
    if (!WP.fullviewActive) return;
    if (e.key === "Escape" || e.key === "Enter") { e.preventDefault(); e.stopPropagation(); exitFullview(); return; }
    if (e.key === "ArrowRight" || e.key === "ArrowDown" || e.key === "PageDown") { e.preventDefault(); e.stopPropagation(); go(1); return; }
    if (e.key === "ArrowLeft" || e.key === "ArrowUp" || e.key === "PageUp") { e.preventDefault(); e.stopPropagation(); go(-1); return; }
    e.preventDefault(); e.stopPropagation();             // 그 외 키=편집 차단(읽기전용)
  }, true);

  WP.enterFullview = enterFullview; WP.exitFullview = exitFullview; WP.fullviewActive = false;
  WP.register({ fullview: function () { WP.fullviewActive ? exitFullview() : enterFullview(); } });
}());
