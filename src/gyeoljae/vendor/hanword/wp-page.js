// 한워드 — 인쇄 쪽 경계 + WYSIWYG 페이지네이션. 보기 메뉴 "쪽 경계" 토글(기본 ON).
//   ① 수동 쪽나누기(.pgbreak): 남은 페이지를 빈 공간으로 채워 다음 내용이 새 쪽 맨 위에서 시작
//   ② 경계에 걸친 문단/표: 통째로 다음 쪽으로 밀어 잘리지 않게(앞에 빈 스페이서 삽입)
//   ③ 쪽 경계마다 점선 + "N쪽" 라벨
//   화면맞춤 편집이라 A4 본문 종횡비(257/170mm)로 쪽 높이 추정. 스페이서는 저장/출력서 제거(cleanedHTML).
(function () {
  "use strict";
  var WP = window.WP, page = WP.page;
  var pw = document.getElementById("pagewrap");
  if (!pw) return;
  var layer = document.createElement("div"); layer.id = "pageguides"; pw.appendChild(layer);
  var timer = null, obs = null, working = false;
  function ratio() { return (WP.paperRatio ? WP.paperRatio() : 257 / 170); }   // 현재 용지 본문 종횡비(용지 바꾸면 쪽 높이도 바뀜)

  function clearSpacers() {
    Array.prototype.forEach.call(page.querySelectorAll("[data-pgfill]"), function (s) { if (s.parentNode) s.parentNode.removeChild(s); });
    Array.prototype.forEach.call(page.querySelectorAll(".pgbreak"), function (b) { b.style.height = ""; if (b.getAttribute("contenteditable") !== "false") b.setAttribute("contenteditable", "false"); });   // 자가치유(2026-07-19): 구문서/임포트 나눔도 비편집화 — 채움영역 타이핑 방지
  }
  function spacer(h) {
    var s = document.createElement("div");
    s.setAttribute("data-pgfill", ""); s.setAttribute("contenteditable", "false");
    s.style.cssText = "height:" + Math.round(h) + "px;margin:0;padding:0;border:0;";
    return s;
  }
  function paginate() {
    if (working) return;
    working = true; if (obs) obs.disconnect();
    try {
      clearSpacers();
      if (WP.syncPaperMeta) WP.syncPaperMeta();   // 문서 용지 메타(#wpmeta data-paper/orient/margins) → 편집 용지(로드·되돌리기 반영)
      if (!document.body.classList.contains("pageguides")) { layer.innerHTML = ""; return; }
      var cs = getComputedStyle(page);
      var padL = parseFloat(cs.paddingLeft) || 0, padR = parseFloat(cs.paddingRight) || 0,
          padT = parseFloat(cs.paddingTop) || 0, padB = parseFloat(cs.paddingBottom) || 0;
      var contentW = page.clientWidth - padL - padR, pageH = contentW * ratio();
      if (pageH < 60) { layer.innerHTML = ""; return; }
      // 블록 순회 — 읽기/쓰기 분리(루프 안에서 읽기·삽입을 번갈면 매번 강제 리플로우=타이핑 끊김)
      var blocks = Array.prototype.filter.call(page.children, function (b) {
        return b.id !== "wpmeta" && b.nodeType === 1 && !b.hasAttribute("data-pgfill");
      });
      // 1) 측정만(읽기 전용, 리플로우 1회)
      var meas = blocks.map(function (b) { return { b: b, top: b.offsetTop - padT, h: b.offsetHeight }; });
      // 2) 삽입/채움만(쓰기 전용) — 위쪽 삽입으로 밀린 양은 shift로 가산해 재측정 없이 계산
      //    (마진 겹침 변화로 수 px 오차 가능하나 가이드용으론 무시 가능)
      var shift = 0, starts = [], curPage = 0;   // starts=모든 쪽의 첫 real-block 인덱스(단일 권위 → 미리보기가 재계산 없이 소비=매 페이지 강제 분할·시작 정규화)
      for (var i = 0; i < meas.length; i++) {
        var m = meas[i], b = m.b, top = m.top + shift, h = m.h;
        if (b.classList && b.classList.contains("pgbreak")) {       // 수동 나누기=남은 쪽 채움
          var rem = top % pageH, fill = (rem < 1) ? 0 : (pageH - rem);
          if (fill > 1) { var fr = Math.round(fill); b.style.height = fr + "px"; shift += fr; top = m.top + shift; }   // 측정시 내용높이 0 → 증가분=fr
          // 다음 블록의 pageOf 검사가 새 쪽 시작을 잡음(아래)
        } else if (h > 1 && h <= pageH + 0.5) {                     // 한 쪽에 들어가는 블록만 경계 걸침 시 통째 밀기
          var boundary = Math.ceil((top + 1) / pageH) * pageH;
          if (top < boundary && (top + h) > boundary + 1) {         // 경계에 걸침→다음 쪽으로
            var sp = Math.round(boundary - top);
            b.parentNode.insertBefore(spacer(sp), b); shift += sp;
            top = m.top + shift;
          }
        }
        var pageOf = Math.floor((top + 0.5) / pageH);               // 이 블록이 속한 쪽(0-based)
        if (pageOf > curPage) { starts.push(i); curPage = pageOf; } // 새 쪽의 첫 블록 → 기록(걸침이든 경계 사이든 모든 쪽 시작 포함)
      }
      window.WP.pageStartIdx = starts;   // 편집기 결정 쪽 시작(real-block 인덱스). 미리보기가 그대로 따름
      // 가이드선/라벨(그리드 정렬됨)
      var contentH = page.scrollHeight - padT - padB, html = "";
      for (var k = 1; (k * pageH) < contentH - 6; k++) {
        var t = Math.round(k * pageH + padT);
        html += '<div class="pgline" style="top:' + t + 'px"></div><div class="pglabel" style="top:' + t + 'px">' + (k + 1) + '쪽</div>';
      }
      if (WP.applyPageBg) WP.applyPageBg();   // 쪽 배경(색·그림) 반영 — 로드·메타변경 시 재적용
      // #26 쪽 테두리 — 쪽마다 본문 영역 사각형(readMeta가 선종류 화이트리스트 보증)
      var meta = WP.readMeta ? WP.readMeta() : {};
      if (meta.pageBorder) {
        var pages = Math.max(1, Math.ceil((contentH - 6) / pageH));
        for (var j = 0; j < pages; j++)
          html += '<div class="pgborder" style="left:' + Math.round(padL) + 'px;top:' + Math.round(padT + j * pageH) +
                  'px;width:' + Math.round(contentW) + 'px;height:' + Math.round(pageH) +
                  'px;border:' + meta.pageBorderW + 'px ' + meta.pageBorder + ' #445;"></div>';
      }
      layer.innerHTML = html;
      var pr = page.getBoundingClientRect(), wr = pw.getBoundingClientRect();
      layer.style.left = (pr.left - wr.left + pw.scrollLeft) + "px";
      layer.style.top = (pr.top - wr.top + pw.scrollTop) + "px";
      layer.style.width = pr.width + "px"; layer.style.height = pr.height + "px";
    } finally {
      if (obs) { obs.takeRecords(); obs.observe(page, { childList: true, subtree: true, characterData: true }); }
      working = false;
    }
  }
  function schedule() { if (working) return; clearTimeout(timer); timer = setTimeout(paginate, 250); }

  try { obs = new MutationObserver(schedule); obs.observe(page, { childList: true, subtree: true, characterData: true }); } catch (e) {}
  window.addEventListener("resize", schedule);

  // ── 목차 1회 생성(2026-07-20): 쪽 메뉴 → 확인 → 1페이지 맨 앞에 편집가능한 일반 <p>로 삽입.
  //   자동갱신 없음(생성 시점 스냅샷). 순서=①뼈대 삽입(자리표시 쪽번호로 줄높이 안정) ②배치 측정
  //   (tocPageMap — 목차 자신의 높이가 이미 문서에 있으니 페이지 밀림·2페이지 확장도 반영) ③실제 쪽번호
  //   채움 + 마커 제거. 순환의존을 1회 정착으로 해소. 이후엔 순수 텍스트라 일반 문단과 동일하게 편집.
  function tocPageMap() {   // 최상위 블록 → 추정 쪽번호(미리보기와 동일한 그리디 배치, 스페이서 무관)
    var cs = getComputedStyle(page);
    var padL = parseFloat(cs.paddingLeft) || 0, padR = parseFloat(cs.paddingRight) || 0;
    var contentW = page.clientWidth - padL - padR, pageH = contentW * ratio();
    var map = new Map(), num = 1, used = 0;
    if (pageH < 60) return map;
    Array.prototype.forEach.call(page.children, function (b) {
      if (b.nodeType !== 1 || b.id === "wpmeta" || b.hasAttribute("data-pgfill")) return;
      if (b.classList.contains("pgbreak")) { num++; used = 0; return; }
      var h = b.offsetHeight;
      if (used > 0 && used + h > pageH + 1) { num++; used = 0; }
      map.set(b, num); used += h;
    });
    return map;
  }
  function collectHeads() {   // 본문의 H1~H3(기존 레거시 목차 div.toc 안 제목은 제외)
    return Array.prototype.slice.call(page.querySelectorAll("h1,h2,h3")).filter(function (h) {
      return page.contains(h) && !(h.closest && h.closest("div.toc"));
    });
  }
  function tocGenerate() {
    var heads = collectHeads();
    if (!heads.length) { WP.toast("제목(H1~H3)이 없습니다 — 문단을 '제목' 스타일로 지정하세요"); return; }
    var meta = WP.readMeta ? WP.readMeta() : {};
    var titleP = document.createElement("p");
    titleP.setAttribute("data-tocgen", "t"); titleP.style.cssText = "text-align:center;font-weight:bold;font-size:1.25em;margin:0 0 10px";
    titleP.textContent = "목  차";
    var entries = heads.map(function (h) {
      var lv = +h.tagName.charAt(1);
      var p = document.createElement("p");
      p.setAttribute("data-tocgen", "e"); p.style.cssText = "margin:2px 0;padding-left:" + ((lv - 1) * 22) + "px";
      p.textContent = ((h.textContent || "(제목)").trim()) + "  ······  0";   // 자리표시 쪽번호(줄높이 안정)
      return { p: p, h: h };
    });
    var meta0 = page.querySelector("#wpmeta");
    var anchor = meta0 ? meta0.nextSibling : page.firstChild;   // wpmeta 뒤(본문 맨 앞)
    page.insertBefore(titleP, anchor);
    entries.forEach(function (e) { page.insertBefore(e.p, anchor); });
    var pmap = tocPageMap();                                    // 삽입 후 리플로우된 높이로 측정
    entries.forEach(function (e) {
      var top = e.h; while (top.parentNode && top.parentNode !== page) top = top.parentNode;
      var pg = pmap.get(top) || 1;
      var disp = WP.fmtPageNum ? WP.fmtPageNum((meta.pagenumStart || 1) + pg - 1, meta.pagenumFormat) : String(pg);
      e.p.textContent = ((e.h.textContent || "(제목)").trim()) + "  ······  " + disp;
      e.p.removeAttribute("data-tocgen");                       // 마커 제거 → 순수 텍스트(일반 편집)
    });
    titleP.removeAttribute("data-tocgen");
    WP.markDirty && WP.markDirty();
    WP.histRecord && WP.histRecord();
    paginate();
    WP.toast("목차 생성됨 — 제목 " + entries.length + "개 · 이제 일반 텍스트로 자유 편집(자동 갱신 없음)");
  }
  page.addEventListener("click", function (e) {   // 채움영역(비편집 pgbreak/pgfill) 클릭=다음 쪽 첫 문단으로 캐럿(2026-07-19 웹 보충: WebKit이 비편집 div '안'에 캐럿을 두어 타이핑이 무반응 유실)
    var b = e.target.closest && e.target.closest(".pgbreak,[data-pgfill]");
    if (!b || !page.contains(b)) return;
    var n = b.nextElementSibling;
    while (n && n.nodeType === 1 && (n.hasAttribute("data-pgfill") || (n.classList && n.classList.contains("pgbreak")))) n = n.nextElementSibling;
    if (!n) { n = document.createElement("p"); n.innerHTML = "<br>"; page.appendChild(n); }
    page.focus();
    var r = document.createRange(); r.selectNodeContents(n); r.collapse(true);
    var s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
  });
  page.addEventListener("click", function (e) {   // 목차 항목 클릭=해당 제목으로 이동(비편집 블록이라 캐럿 영향 없음)
    var it = e.target.closest && e.target.closest(".toc-e[data-hi]");
    if (!it || !page.contains(it)) return;
    var hs = page.querySelectorAll("h1,h2,h3"), h = hs[+it.getAttribute("data-hi")];
    if (h) { h.scrollIntoView({ block: "center" }); WP.status && WP.status("목차 → " + h.textContent); }
  });

  WP.register({
    pageguide: function () {
      document.body.classList.toggle("pageguides"); paginate();
      WP.toast(document.body.classList.contains("pageguides") ? "쪽 경계·페이지 맞춤 켬" : "쪽 경계 끔(연속)");
    },
    tocGen: function () {   // 쪽 메뉴 항목 → 확인 모달(생성 전 1회). 제목 없으면 즉시 안내.
      var heads = collectHeads();
      if (!heads.length) { WP.toast("제목(H1~H3)이 없습니다 — 문단을 '제목' 스타일로 지정하세요"); return; }
      var info = document.getElementById("toc-info");
      if (info) info.textContent = "1페이지 맨 앞에 목차를 생성합니다. 제목 " + heads.length + "개 · 생성 후엔 일반 텍스트라 자유롭게 수정할 수 있고, 내용이 바뀌어도 자동 갱신되지 않습니다(다시 필요하면 지우고 재생성).";
      if (WP.openModal) WP.openModal("m-toc"); else tocGenerate();
    },
    tocGenDo: function () {   // 모달 [생성] 버튼 → 닫고 실제 생성
      var m = document.getElementById("m-toc"); if (m) m.classList.add("hidden");
      tocGenerate();
    },
  });   // 자동 목차(연속 갱신)는 폐지(2026-07-19). tocGen=쪽 메뉴 1회 생성(편집가능 텍스트)로 대체(2026-07-20).
  WP.pageGuides = paginate;
  document.body.classList.add("pageguides");
  setTimeout(paginate, 350);
}());
