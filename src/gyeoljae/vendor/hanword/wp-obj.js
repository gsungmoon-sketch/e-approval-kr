// 한워드 — 개체(이미지·도형·그룹) 선택/핸들 리사이즈·이동·삭제·배치 (HWP식, 표와 동일 체계).
//   개체 클릭=선택(파란테두리+모서리 핸들+개체 도구막대). 핸들드래그/방향키=크기, Del=삭제, Esc=해제.
//   배치모델(docs/wp-graphics-plan.md): 개체=#page 직속 블록 1개. data-place=char/float/floatR/fixed,
//     fixed=자유배치(left/top/z를 개체 블록 style에 저장 → block-diff undo가 이동/리사이즈 자동추적).
(function () {
  "use strict";
  var WP = window.WP, page = WP.page, markDirty = WP.markDirty, toastMsg = WP.toast;
  var sel = null, selSet = [], handle = null, handleR = null, handleB = null, mover = null, fileH = null, drag = null, move = null, mdrag = null;
  var lineH1 = null, lineH2 = null, lineDrag = null;   // 선 끝점 핸들 2개 + 드래그 상태
  function hideHandles() { [handle, handleR, handleB, mover, lineH1, lineH2, fileH].forEach(function (h) { if (h) h.classList.add("hidden"); }); if (WP.hideZButtons) WP.hideZButtons(); }
  var addMode = false;   // 터치용 다중선택 모드(＋선택): 켜면 탭=집합 토글(Shift 대용)
  function forEachSel(fn) { selSet.forEach(fn); }   // 선택집합 전체에 연산(sel=대표=마지막 클릭)
  function setAddMode(on) { addMode = on; var b = objtools && objtools.querySelector("[data-obj=addsel]"); if (b) b.classList.toggle("on", on); }
  var objtools = document.getElementById("objtools");
  var PLACES = ["char", "float", "floatR", "fixed"];
  var PLABEL = { char: "글자처럼(기본)", float: "어울림 왼쪽 · 글자가 피해감", floatR: "어울림 오른쪽 · 글자가 피해감", fixed: "자유배치 · 끌어서 이동" };
  var oidN = 0;
  function nextOid() {   // 기존 문서의 data-oid 최댓값을 항상 초과(불러온 문서와 충돌 방지)
    var mx = 0; page.querySelectorAll("[data-oid]").forEach(function (e) { var m = /^o(\d+)$/.exec(e.getAttribute("data-oid") || ""); if (m && +m[1] > mx) mx = +m[1]; });
    oidN = Math.max(oidN, mx) + 1; return "o" + oidN;
  }
  function objAt(t) {   // 선택 대상: .wp-obj 블록 우선, 아니면 맨몸 <img>(레거시 인라인)
    if (!t || !page.contains(t)) return null;
    var o = t.closest ? t.closest(".wp-obj") : null;
    if (o && page.contains(o)) return o;
    if (t.tagName === "IMG") return t;
    if (t.tagName === "HR") return t;   // 가로줄=클릭 선택/삭제 가능(개체처럼)
    return null;
  }
  function isHr(el) { return !!(el && el.tagName === "HR"); }
  function isShape(el) { return !!(el && el.getAttribute && el.getAttribute("data-obj") === "shape"); }

  function place() {
    if (!sel) { hideHandles(); if (objtools) objtools.classList.add("hidden"); return; }
    if (isHr(sel)) { hideHandles(); if (objtools) objtools.classList.add("hidden"); return; }   // 가로줄=선택 테두리(obj-sel)만, 손잡이·도구바 없음(크기·이동·스타일 무의미). Delete로 삭제
    if (sel.getAttribute("data-cellfit") === "1") { hideHandles(); if (objtools) objtools.classList.add("hidden"); return; }   // 표 셀 맞춤 미디어=고정(테두리만, Delete로 삭제) — 셀 폭 따라감
    var r = sel.getBoundingClientRect();
    if (isLine(sel)) {   // 선=상자 핸들 대신 양 끝점 핸들 + 이동 핸들
      handle.classList.add("hidden"); if (handleR) handleR.classList.add("hidden"); if (handleB) handleB.classList.add("hidden");
      var o = pgOrigin(), x1 = +sel.getAttribute("data-x1"), y1 = +sel.getAttribute("data-y1"), x2 = +sel.getAttribute("data-x2"), y2 = +sel.getAttribute("data-y2");
      lineH1.style.left = (o.x + x1 - 8) + "px"; lineH1.style.top = (o.y + y1 - 8) + "px"; lineH1.classList.remove("hidden");
      lineH2.style.left = (o.x + x2 - 8) + "px"; lineH2.style.top = (o.y + y2 - 8) + "px"; lineH2.classList.remove("hidden");
      if (mover) { mover.style.left = (o.x + (x1 + x2) / 2 - 9) + "px"; mover.style.top = (o.y + (y1 + y2) / 2 - 20) + "px"; mover.classList.remove("hidden"); showZButtons(selSet, o.x + (x1 + x2) / 2 + 13, o.y + (y1 + y2) / 2 - 20); }   // 선=항상 fixed → z버튼 표시
      if (objtools) { var tb0 = document.getElementById("toolbar"); objtools.style.top = ((tb0 ? tb0.getBoundingClientRect().bottom : 28) + 1) + "px"; objtools.classList.remove("hidden"); updateObjToolState(); }
      return;
    }
    if (lineH1) lineH1.classList.add("hidden"); if (lineH2) lineH2.classList.add("hidden");
    var isMediaObj = sel.getAttribute("data-obj") === "media";   // 미디어=모서리 핸들만 우측하단(2026-07-19 사용자 지정). 컨트롤바와 겹치지만 핸들 z60>비디오라 드래그 우선, 비율고정이라 가로 드래그로 충분
    if (isMediaObj) {
      handle.style.left = (r.right - 7) + "px"; handle.style.top = (r.bottom - 7) + "px"; handle.classList.remove("hidden");
      if (handleR) handleR.classList.add("hidden"); if (handleB) handleB.classList.add("hidden");
    }
    else {
      handle.style.left = (r.right - 7) + "px"; handle.style.top = (r.bottom - 7) + "px"; handle.classList.remove("hidden");          // 모서리=자유(가로세로)
      if (handleR) { handleR.style.left = (r.right - 6) + "px"; handleR.style.top = (r.top + r.height / 2 - 6) + "px"; handleR.classList.remove("hidden"); }   // 우변=가로만
      if (handleB) { handleB.style.left = (r.left + r.width / 2 - 6) + "px"; handleB.style.top = (r.bottom - 6) + "px"; handleB.classList.remove("hidden"); }   // 하변=세로만
    }
    if (mover) { mover.style.left = (r.left - 7) + "px"; mover.style.top = (r.top - 7) + "px"; mover.classList.remove("hidden"); }
    if (isPositioned(sel)) showZButtons(selSet, r.left - 7 + 20, r.top - 7); else hideZButtons();
    var kindT = sel.getAttribute("data-obj");
    var isFileKind = (kindT === "media" || kindT === "img" || sel.tagName === "IMG");   // 파일핸들 💾=미디어·그림만
    if (fileH) {
      if (isFileKind) { fileH.style.left = (r.left - 7 + (isPositioned(sel) ? 64 : 22)) + "px"; fileH.style.top = (r.top - 7) + "px"; fileH.classList.remove("hidden"); }
      else fileH.classList.add("hidden");
    }   // z버튼=이동핸들 옆(positioned=fixed/float만, 글자처럼=숨김)
    if (objtools) {   // 개체 옆이 아니라 상단(도구모음 바로 아래)에 표시. 좌우 폭은 CSS(left/right)로 꽉 채워 줄바꿈
      var tb = document.getElementById("toolbar");
      objtools.style.top = ((tb ? tb.getBoundingClientRect().bottom : 28) + 1) + "px";
      objtools.classList.remove("hidden"); updateObjToolState();
    }
  }
  function updateObjToolState() {   // 현재 선택에 안 맞는 버튼은 흐리게(dim) — 언제 쓰는지 명확화
    if (!objtools) return;
    var multi = selSet.length >= 2;
    var hasGroup = selSet.some(function (el) { return el.getAttribute("data-obj") === "group"; });
    var t = sel && sel.getAttribute("data-obj");
    var any = selSet.length >= 1;
    function dim(act, on) { var b = objtools.querySelector("[data-obj=" + act + "]"); if (b) b.classList.toggle("dim", !on); }
    ["place", "rotL", "rotR", "flipH", "flipV", "copy", "del", "zorder"].forEach(function (a) { dim(a, any); });   // 선택 있어야 작동
    dim("addsel", true);      // 다중선택 모드 토글=항상 가능(도형 그리기 아이콘 data-shape도 항상 가능)
    dim("group", multi);      // 묶기=2개+
    dim("align", multi);      // 맞춤=2개+
    dim("ungroup", hasGroup); // 풀기=그룹
    dim("crop", t === "img"); // 자르기=이미지
    dim("style", t === "shape"); // 스타일=도형
  }
  function assignOid(el) { if (el.classList.contains("wp-obj") && !el.getAttribute("data-oid")) el.setAttribute("data-oid", nextOid()); }
  function outline(el, on) { if (!el || !el.classList) return; el.classList.toggle("obj-sel", on); if (!on && !el.getAttribute("class")) el.removeAttribute("class"); }
  function select(img) {   // 단일 선택(집합 초기화)
    deselect(); selSet = [img]; sel = img; outline(img, true); assignOid(img); place();
  }
  function toggleSel(img) {   // Shift+클릭: 집합 추가/해제 토글
    var i = selSet.indexOf(img);
    if (i >= 0) { outline(img, false); selSet.splice(i, 1); sel = selSet[selSet.length - 1] || null; }
    else { selSet.push(img); outline(img, true); assignOid(img); sel = img; }
    if (sel) place(); else { hideHandles(); if (objtools) objtools.classList.add("hidden"); }
  }
  function deselect() { forEachSel(function (el) { outline(el, false); }); selSet = []; sel = null; setAddMode(false); hideHandles(); if (objtools) objtools.classList.add("hidden"); var at = document.getElementById("aligntools"); if (at) at.classList.add("hidden"); var ct = document.getElementById("croptools"); if (ct) ct.classList.add("hidden"); var st = document.getElementById("shapetools"); if (st) st.classList.add("hidden"); var zt = document.getElementById("zordertools"); if (zt) zt.classList.add("hidden"); }
  function ensureFixedOf(el) { if (el.getAttribute("data-place") !== "fixed") { var ol = el.offsetLeft, ot = el.offsetTop; el.setAttribute("data-place", "fixed"); el.style.left = ol + "px"; el.style.top = ot + "px"; } }
  function ensureFixed() { if (sel) ensureFixedOf(sel); }
  // ── 변환(회전·반전): data-rot/data-fliph/data-flipv → style.transform. 개체 블록 자신에 저장 → undo 자동추적 ──
  function applyXformOf(el) {
    var rot = parseInt(el.getAttribute("data-rot") || "0", 10);
    var fh = el.getAttribute("data-fliph") === "1" ? -1 : 1;
    var fv = el.getAttribute("data-flipv") === "1" ? -1 : 1;
    var t = "";
    if (rot) t += "rotate(" + rot + "deg) ";
    if (fh < 0 || fv < 0) t += "scale(" + fh + "," + fv + ")";
    el.style.transform = t.trim();   // 빈문자열이면 transform만 해제(width/height 등은 보존)
  }
  function rotate(d) {
    if (!selSet.length) return; var last = 0;
    forEachSel(function (el) {
      var r = (((parseInt(el.getAttribute("data-rot") || "0", 10) + d) % 360) + 360) % 360;
      if (r) el.setAttribute("data-rot", r); else el.removeAttribute("data-rot");
      applyXformOf(el); last = r;
    });
    place(); markDirty(); WP.histRecord && WP.histRecord(); toastMsg("회전 " + (selSet.length > 1 ? selSet.length + "개 · " : "") + last + "°");
  }
  function flip(axis) {
    if (!selSet.length) return; var k = axis === "v" ? "data-flipv" : "data-fliph", off = false;
    forEachSel(function (el) {
      var on = el.getAttribute(k) === "1";
      if (on) { el.removeAttribute(k); off = true; } else el.setAttribute(k, "1");
      applyXformOf(el);
    });
    place(); markDirty(); WP.histRecord && WP.histRecord();
    toastMsg((axis === "v" ? "상하" : "좌우") + " 반전" + (selSet.length > 1 ? " " + selSet.length + "개" : (off ? " 해제" : "")));
  }
  function setW(w) {
    var r = sel.getBoundingClientRect(), nw = Math.max(20, Math.round(w));
    if (sel.getAttribute("data-obj") === "group") {   // 그룹=통째 스케일(크기 + 자식 위치·크기 전부 비율)
      var sg = r.width ? nw / r.width : 1;
      sel.style.width = Math.round(r.width * sg) + "px"; sel.style.height = Math.round(r.height * sg) + "px";
      [].slice.call(sel.children).forEach(function (c) { if (c.nodeType === 1 && c.classList.contains("wp-obj")) scaleObj(c, sg); });
      place(); return;
    }
    if (sel.getAttribute("data-obj") === "img" && sel.getAttribute("data-cw")) {   // 이미지 개체=콘텐츠 스케일(크롭 비율 유지)
      var scale = r.width ? nw / r.width : 1;
      sel.setAttribute("data-cw", Math.round((parseFloat(sel.getAttribute("data-cw")) || 0) * scale));
      sel.setAttribute("data-ch", Math.round((parseFloat(sel.getAttribute("data-ch")) || 0) * scale));
      applyImgCrop(sel); place(); return;
    }
    var ratio = r.width ? r.height / r.width : 1;
    sel.style.width = nw + "px";
    if (isShape(sel) || sel.getAttribute("data-obj") === "media") sel.style.height = Math.max(20, Math.round(nw * ratio)) + "px";   // 도형·미디어=비율 유지 양축 스케일
    else sel.style.height = "auto";                                                     // 레거시 이미지=자연비율
    place();
  }
  // 가로·세로 독립 리사이즈(모서리 자유·우변 가로만·하변 세로만). w/h = 목표 렌더 폭·높이(px)
  function setWH(w, h) {
    var r = sel.getBoundingClientRect(), nw = Math.max(20, Math.round(w)), nh = Math.max(20, Math.round(h)), t = sel.getAttribute("data-obj");
    if (t === "group") {   // 그룹=자식을 sx·sy 각각 스케일
      var sx = r.width ? nw / r.width : 1, sy = r.height ? nh / r.height : 1;
      sel.style.width = nw + "px"; sel.style.height = nh + "px";
      [].slice.call(sel.children).forEach(function (c) { if (c.nodeType === 1 && c.classList.contains("wp-obj")) scaleObjXY(c, sx, sy); });
      place(); return;
    }
    if (t === "img" && sel.getAttribute("data-cw")) {   // 이미지=콘텐츠 cw·ch 독립(크롭 창 유지)
      var cr = (sel.getAttribute("data-crop") || "0 0 0 0").split(" ").map(parseFloat);
      sel.setAttribute("data-cw", Math.round(nw / Math.max(0.05, 1 - (cr[3] || 0) - (cr[1] || 0))));
      sel.setAttribute("data-ch", Math.round(nh / Math.max(0.05, 1 - (cr[0] || 0) - (cr[2] || 0))));
      applyImgCrop(sel); place(); return;
    }
    sel.style.width = nw + "px"; sel.style.height = nh + "px";   // 도형(SVG stretch)·레거시 이미지
    place();
  }
  function delImg() {
    if (!selSet.length) return;
    var arr = selSet.slice(); deselect();
    arr.forEach(function (el) { if (el.parentNode) el.parentNode.removeChild(el); });
    markDirty(); WP.histRecord && WP.histRecord();
  }
  function setPlace(el, nx, ol, ot) {
    if (nx === "fixed") { el.setAttribute("data-place", "fixed"); el.style.left = ol + "px"; el.style.top = ot + "px"; }
    else { el.style.left = ""; el.style.top = ""; if (nx === "char") el.removeAttribute("data-place"); else el.setAttribute("data-place", nx); }
  }
  function togglePlacePop() {   // [배치]=명시 4버튼 팝업(2026-07-19 — 순환은 '고정' 도달을 놓치기 쉬움: 사용자 실관찰)
    var pt = document.getElementById("placetools"); if (!pt || !sel) return;
    if (!pt.classList.contains("hidden")) { pt.classList.add("hidden"); return; }
    hideObjPops("placetools");
    var r = objtools.getBoundingClientRect(); pt.style.left = r.left + "px"; pt.style.top = (r.bottom + 3) + "px"; pt.classList.remove("hidden");
    var cur = sel.getAttribute("data-place") || "char";
    [].slice.call(pt.querySelectorAll("[data-pl]")).forEach(function (b) { b.classList.toggle("on", b.getAttribute("data-pl") === cur); });
  }
  (function () {
    var pt = document.getElementById("placetools");
    if (pt) pt.addEventListener("pointerup", function (e) {
      var b = e.target.closest("[data-pl]"); if (!b || !selSet.length) return;
      var nx = b.getAttribute("data-pl");
      var pos = selSet.map(function (el) { return { el: el, l: el.offsetLeft, t: el.offsetTop }; });
      pos.forEach(function (p) { setPlace(p.el, nx, p.l, p.t); });
      place(); markDirty(); WP.histRecord && WP.histRecord();
      pt.classList.add("hidden");
      toastMsg("배치: " + PLABEL[nx] + (selSet.length > 1 ? " (" + selSet.length + "개)" : ""));
    });
  })();
  function cyclePlace() {
    if (!sel) return;
    var cur = sel.getAttribute("data-place") || "char", nx = PLACES[(PLACES.indexOf(cur) + 1) % PLACES.length];
    var pos = selSet.map(function (el) { return { el: el, l: el.offsetLeft, t: el.offsetTop }; });   // reflow 전 위치 스냅샷(여럿 동시 fixed 전환 시 좌표 밀림 방지)
    pos.forEach(function (p) { setPlace(p.el, nx, p.l, p.t); });
    place(); markDirty(); WP.histRecord && WP.histRecord(); toastMsg("배치: " + PLABEL[nx] + (selSet.length > 1 ? " (" + selSet.length + "개)" : ""));
  }
  // ── 정렬/배분/같은크기(§E): 전부 fixed 승격 후 개체 블록 style 조정 → undo 자동추적 ──
  function alignItems() { forEachSel(function (el) { ensureFixedOf(el); }); return selSet.map(function (el) { return { el: el, l: el.offsetLeft, t: el.offsetTop, w: el.offsetWidth, h: el.offsetHeight }; }); }
  function align(mode) {
    if (selSet.length < 2) { toastMsg("2개 이상 선택하세요"); return; }
    var it = alignItems();
    var minL = Math.min.apply(null, it.map(function (i) { return i.l; })), maxR = Math.max.apply(null, it.map(function (i) { return i.l + i.w; }));
    var minT = Math.min.apply(null, it.map(function (i) { return i.t; })), maxB = Math.max.apply(null, it.map(function (i) { return i.t + i.h; }));
    it.forEach(function (i) {
      var nl = null, nt = null;
      if (mode === "left") nl = minL;
      else if (mode === "right") nl = maxR - i.w;
      else if (mode === "hcenter") nl = Math.round((minL + maxR) / 2 - i.w / 2);
      else if (mode === "top") nt = minT;
      else if (mode === "bottom") nt = maxB - i.h;
      else if (mode === "vcenter") nt = Math.round((minT + maxB) / 2 - i.h / 2);
      if (isLine(i.el)) translateLine(i.el, nl != null ? nl - i.l : 0, nt != null ? nt - i.t : 0);   // 선=끝점 이동(style.left/top은 renderLine이 덮어씀)
      else { if (nl != null) i.el.style.left = nl + "px"; if (nt != null) i.el.style.top = nt + "px"; }
    });
    place(); markDirty(); WP.histRecord && WP.histRecord(); toastMsg("맞춤 적용 (" + it.length + "개)");
  }
  function distribute(axis) {
    if (selSet.length < 3) { toastMsg("3개 이상 선택하세요"); return; }
    var it = alignItems();
    var horiz = axis === "h";
    it.sort(function (a, b) { return horiz ? a.l - b.l : a.t - b.t; });
    var first = it[0], last = it[it.length - 1];
    var totalSize = it.reduce(function (s, i) { return s + (horiz ? i.w : i.h); }, 0);
    var span = horiz ? (last.l + last.w - first.l) : (last.t + last.h - first.t);
    var gap = (span - totalSize) / (it.length - 1);
    var cur = horiz ? first.l : first.t;
    it.forEach(function (i) {
      if (horiz) { if (isLine(i.el)) translateLine(i.el, Math.round(cur) - i.l, 0); else i.el.style.left = Math.round(cur) + "px"; cur += i.w + gap; }
      else { if (isLine(i.el)) translateLine(i.el, 0, Math.round(cur) - i.t); else i.el.style.top = Math.round(cur) + "px"; cur += i.h + gap; }
    });
    place(); markDirty(); WP.histRecord && WP.histRecord(); toastMsg((horiz ? "가로" : "세로") + " 간격 동일");
  }
  function sameSize(dim) {
    if (selSet.length < 2 || !sel) { toastMsg("2개 이상 선택하세요"); return; }
    var w = sel.offsetWidth, h = sel.offsetHeight;
    forEachSel(function (el) { if (el === sel || isLine(el)) return; if (dim === "w") el.style.width = w + "px"; else el.style.height = h + "px"; });   // 선=길이/각도 모델이라 폭·높이 맞춤 제외
    place(); markDirty(); WP.histRecord && WP.histRecord(); toastMsg((dim === "w" ? "폭" : "높이") + " 같게 (대표 기준)");
  }
  function hideObjPops(keep) { ["aligntools", "croptools", "shapetools", "zordertools", "placetools"].forEach(function (id) { if (id !== keep) { var e = document.getElementById(id); if (e) e.classList.add("hidden"); } }); }   // 팝업 상호배타(겹침 방지)
  function toggleAlign() {
    var at = document.getElementById("aligntools"); if (!at || !sel) return;
    if (selSet.length < 2) { toastMsg("맞춤은 2개 이상 선택해야 합니다 ([＋선택]으로 여러 개 선택)"); return; }
    if (!at.classList.contains("hidden")) { at.classList.add("hidden"); return; }
    hideObjPops("aligntools");
    var r = objtools.getBoundingClientRect();
    at.style.left = r.left + "px"; at.style.top = (r.bottom + 3) + "px"; at.classList.remove("hidden");
  }
  // 개체를 가로 sx·세로 sy 배율로 스케일(위치·크기·콘텐츠). 그룹이면 자식까지 재귀.
  function scaleObjXY(el, sx, sy) {
    if (el.style.left) el.style.left = (parseFloat(el.style.left) * sx) + "px";
    if (el.style.top) el.style.top = (parseFloat(el.style.top) * sy) + "px";
    var t = el.getAttribute("data-obj");
    if (t === "img" && el.getAttribute("data-cw")) {
      el.setAttribute("data-cw", Math.round((parseFloat(el.getAttribute("data-cw")) || 0) * sx));
      el.setAttribute("data-ch", Math.round((parseFloat(el.getAttribute("data-ch")) || 0) * sy));
      applyImgCrop(el);
    } else {
      if (el.style.width) el.style.width = Math.round(parseFloat(el.style.width) * sx) + "px";
      if (el.style.height && el.style.height !== "auto") el.style.height = Math.round(parseFloat(el.style.height) * sy) + "px";
      if (t === "group") [].slice.call(el.children).forEach(function (c) { if (c.nodeType === 1 && c.classList.contains("wp-obj")) scaleObjXY(c, sx, sy); });
    }
  }
  function scaleObj(el, s) { scaleObjXY(el, s, s); }   // 균등(그룹 균등 리사이즈용)
  // ── z오더(겹침 순서): 같은 부모 형제끼리 스택 정규화(z=10+순번). 인라인 z-index → 직렬화·undo 자동 ──
  function zOf(el) { return parseFloat(el.style.zIndex) || 5; }   // CSS 기본 fixed=5
  // z-order 참여자: .wp-obj(그림·도형·그룹) + positioned 표(fixed/float) — 겹치는 배치만 스택 의미
  function isZParticipant(c) { return c.nodeType === 1 && (c.classList.contains("wp-obj") || (c.tagName === "TABLE" && /^(fixed|float|floatR)$/.test(c.getAttribute("data-place") || ""))); }
  function zReorderCore(targets, mode) {   // 통합 z스택: 개체들 + 가상 '글' 레이어(2026-07-19)
    // 전역 스택(부모 무관): fixed/float은 #page 기준 absolute. '글'=본문 흐름(z=0 경계) —
    //   글 앞 대역 z>=10, 글 뒤 대역 z<0(쪽배경 위·글 아래). forward/backward는 글 경계를 한 칸으로 넘고,
    //   textfront/textback은 글 바로 앞/뒤로 직행. HWPX 매핑=textWrap IN_FRONT_OF_TEXT/BEHIND_TEXT.
    var sibs = [].slice.call(page.querySelectorAll(".wp-obj,table,img")).filter(isZParticipant)
      .filter(function (el) { return isPositioned(el) || targets.indexOf(el) >= 0; });
    if (!sibs.length) { toastMsg("자유배치 개체가 있어야 순서를 정할 수 있어요"); markDirty(); return; }
    var stacked = sibs.slice().sort(function (a, b) { var d = zOf(a) - zOf(b); return d !== 0 ? d : (sibs.indexOf(a) - sibs.indexOf(b)); });
    var TEXT = { text: true };                     // 가상 '글' 레이어 마커
    var arr = [], ins = false;
    stacked.forEach(function (el) { if (!ins && zOf(el) >= 0) { arr.push(TEXT); ins = true; } arr.push(el); });
    if (!ins) arr.push(TEXT);
    var selHere = stacked.filter(function (el) { return targets.indexOf(el) >= 0; });
    if (!selHere.length) return;
    var rest = arr.filter(function (el) { return el === TEXT || targets.indexOf(el) < 0; });
    var out, ti;
    if (mode === "front") out = rest.concat(selHere);
    else if (mode === "back") out = selHere.concat(rest);
    else if (mode === "textfront") { ti = rest.indexOf(TEXT); out = rest.slice(0, ti + 1).concat(selHere).concat(rest.slice(ti + 1)); }
    else if (mode === "textback") { ti = rest.indexOf(TEXT); out = rest.slice(0, ti).concat(selHere).concat(rest.slice(ti)); }
    else {   // forward/backward: 글 마커도 한 칸으로 취급 → 연타로 글 경계 통과
      var firstIdx = arr.indexOf(selHere[0]);
      var beforeCnt = arr.slice(0, firstIdx).filter(function (el) { return el === TEXT || targets.indexOf(el) < 0; }).length;
      var at = Math.max(0, Math.min(rest.length, beforeCnt + (mode === "forward" ? 1 : -1)));
      out = rest.slice(0, at).concat(selHere).concat(rest.slice(at));
    }
    ti = out.indexOf(TEXT);
    out.slice(0, ti).forEach(function (el, i, a) { el.style.zIndex = i - a.length; });   // 글 뒤: -n..-1
    out.slice(ti + 1).forEach(function (el, i) { el.style.zIndex = 10 + i; });          // 글 앞: 10..
    markDirty(); WP.histRecord && WP.histRecord();
  }
  function zReorder(mode) {   // 개체 순서▾ 도구(현재 선택 개체들)
    if (!selSet.length) { toastMsg("개체를 선택하세요"); return; }
    zReorderCore(selSet, mode); place();
    toastMsg(mode === "front" ? "맨 앞으로" : mode === "back" ? "맨 뒤로" : mode === "forward" ? "앞으로" : mode === "backward" ? "뒤로" : mode === "textfront" ? "글 앞으로" : "글 뒤로(배경)");
  }
  WP.zReorderEl = function (el, mode) { if (el) zReorderCore([el], mode); };   // 표 등 외부에서 단일 요소 재정렬(통합 스택 공유)
  function toggleZorder() {
    var zt = document.getElementById("zordertools"); if (!zt || !sel) return;
    if (!zt.classList.contains("hidden")) { zt.classList.add("hidden"); return; }
    hideObjPops("zordertools");
    var r = objtools.getBoundingClientRect(); zt.style.left = r.left + "px"; zt.style.top = (r.bottom + 3) + "px"; zt.classList.remove("hidden");
  }
  // ── 묶기/해체(§D): 그룹=.wp-obj[data-obj=group] 래퍼(fixed, 합집합 bbox). 자식=그룹원점 상대 absolute ──
  function group() {
    if (selSet.some(isLine)) { toastMsg("선은 묶기에서 제외됩니다(끝점 모델)"); }   // 선=끝점 좌표가 #page 기준이라 그룹 원점과 어긋남 → 제외
    var members = selSet.filter(function (el) { return !isLine(el); });
    if (members.length < 2) { toastMsg("선을 제외하고 2개 이상 선택해 묶으세요"); return; }
    var items = members.map(function (el) { return { el: el, l: el.offsetLeft, t: el.offsetTop, w: el.offsetWidth, h: el.offsetHeight }; });   // 페이지 절대좌표 스냅샷
    var minL = Math.min.apply(null, items.map(function (i) { return i.l; }));
    var minT = Math.min.apply(null, items.map(function (i) { return i.t; }));
    var maxR = Math.max.apply(null, items.map(function (i) { return i.l + i.w; }));
    var maxB = Math.max.apply(null, items.map(function (i) { return i.t + i.h; }));
    var g = document.createElement("div");
    g.className = "wp-obj"; g.setAttribute("data-obj", "group"); g.setAttribute("data-oid", nextOid());
    g.setAttribute("data-place", "fixed"); g.setAttribute("contenteditable", "false");
    g.style.left = minL + "px"; g.style.top = minT + "px"; g.style.width = (maxR - minL) + "px"; g.style.height = (maxB - minT) + "px";
    items[0].el.parentNode.insertBefore(g, items[0].el);   // 첫 항목 자리에 래퍼 삽입
    items.forEach(function (it) {   // 자식=그룹원점 상대좌표로 이동
      it.el.setAttribute("data-place", "fixed");
      it.el.style.left = (it.l - minL) + "px"; it.el.style.top = (it.t - minT) + "px";
      g.appendChild(it.el);
    });
    deselect(); select(g); markDirty(); WP.histRecord && WP.histRecord();
    toastMsg("개체 " + items.length + "개 묶음 (풀기로 해제)");
  }
  function ungroup() {
    var groups = selSet.filter(function (el) { return el.getAttribute("data-obj") === "group"; });
    if (!groups.length) { toastMsg("묶은 개체를 선택해 푸세요"); return; }
    var freed = [];
    groups.forEach(function (g) {
      var gl = g.offsetLeft, gt = g.offsetTop;   // 그룹 페이지원점
      [].slice.call(g.children).forEach(function (k) {
        if (k.nodeType !== 1 || !k.classList.contains("wp-obj")) return;
        k.setAttribute("data-place", "fixed");
        k.style.left = ((parseFloat(k.style.left) || 0) + gl) + "px";   // 페이지좌표=그룹원점+자식상대
        k.style.top = ((parseFloat(k.style.top) || 0) + gt) + "px";
        g.parentNode.insertBefore(k, g); freed.push(k);
      });
      g.parentNode.removeChild(g);
    });
    deselect();
    freed.forEach(function (k, i) { if (i === 0) select(k); else toggleSel(k); });
    markDirty(); WP.histRecord && WP.histRecord();
    toastMsg("개체 풀기 " + freed.length + "개");
  }

  function startResize(e, mode) {
    if (!sel) return; e.preventDefault(); e.stopPropagation();
    var rc = sel.getBoundingClientRect();
    drag = { x0: e.clientX, y0: e.clientY, w0: rc.width, h0: rc.height, mode: mode };
  }
  handle = document.createElement("div"); handle.id = "objhandle"; handle.className = "hidden"; handle.title = "크기(자유·Shift=비율고정)"; document.body.appendChild(handle);
  handle.addEventListener("pointerdown", function (e) { startResize(e, "corner"); });
  handleR = document.createElement("div"); handleR.id = "objhandle-r"; handleR.className = "hidden"; handleR.title = "가로 크기"; document.body.appendChild(handleR);
  handleR.addEventListener("pointerdown", function (e) { startResize(e, "w"); });
  handleB = document.createElement("div"); handleB.id = "objhandle-b"; handleB.className = "hidden"; handleB.title = "세로 크기"; document.body.appendChild(handleB);
  handleB.addEventListener("pointerdown", function (e) { startResize(e, "h"); });
  // 선 끝점 핸들(●) — 각각 끌어 끝점 자유 이동(수평/수직/45° 스냅, Alt=자유각)
  function mkLineH(id) { var h = document.createElement("div"); h.id = id; h.className = "objline-h hidden"; document.body.appendChild(h); return h; }
  lineH1 = mkLineH("objlineh1"); lineH2 = mkLineH("objlineh2");
  function startLineDrag(e, which) { if (!sel || !isLine(sel)) return; e.preventDefault(); e.stopPropagation(); lineDrag = { which: which }; try { (which === 1 ? lineH1 : lineH2).setPointerCapture(e.pointerId); } catch (x) {} }
  lineH1.addEventListener("pointerdown", function (e) { startLineDrag(e, 1); });
  lineH2.addEventListener("pointerdown", function (e) { startLineDrag(e, 2); });
  // 이동핸들 옆 z-order 미니버튼(앞=맨앞, 뒤=맨뒤) — 표·개체 공용. positioned일 때만 표시
  var zTargets = null;
  function mkZBtn(id, txt, mode) {
    var b = document.createElement("div"); b.id = id; b.className = "zbtn hidden"; b.textContent = txt; b.title = (mode === "front" ? "맨 앞으로(그림·표 위로)" : "맨 뒤로(그림·표 아래로)"); document.body.appendChild(b);
    b.addEventListener("pointerdown", function (e) { e.preventDefault(); e.stopPropagation(); });
    b.addEventListener("pointerup", function (e) { e.preventDefault(); e.stopPropagation(); if (zTargets && zTargets.length) { zReorderCore(zTargets, mode); toastMsg(mode === "front" ? "맨 앞으로" : "맨 뒤로"); } });
    return b;
  }
  var zfrontBtn = mkZBtn("zfront", "앞", "front"), zbackBtn = mkZBtn("zback", "뒤", "back");
  function showZButtons(targets, x, y) { zTargets = targets; zfrontBtn.style.left = x + "px"; zfrontBtn.style.top = y + "px"; zbackBtn.style.left = (x + 20) + "px"; zbackBtn.style.top = y + "px"; zfrontBtn.classList.remove("hidden"); zbackBtn.classList.remove("hidden"); }
  function hideZButtons() { zfrontBtn.classList.add("hidden"); zbackBtn.classList.add("hidden"); }
  function isPositioned(el) { var p = el && el.getAttribute("data-place"); return p === "fixed" || p === "float" || p === "floatR"; }
  WP.showZButtons = showZButtons; WP.hideZButtons = hideZButtons;
  // 선택집합 중 fixed 개체들의 이동 시작점 스냅샷(함께 이동)
  function startMove(e) {
    var items = [];
    forEachSel(function (el) {
      if (el.getAttribute("data-place") !== "fixed") return;
      if (isLine(el)) items.push({ el: el, line: true, x1: +el.getAttribute("data-x1"), y1: +el.getAttribute("data-y1"), x2: +el.getAttribute("data-x2"), y2: +el.getAttribute("data-y2") });
      else items.push({ el: el, l: parseFloat(el.style.left) || 0, t: parseFloat(el.style.top) || 0 });
    });
    if (items.length) move = { x: e.clientX, y: e.clientY, items: items };
  }
  function dragMove(e) {   // move.items 전부에 동일 델타(상대위치 유지)
    if (!move) return;
    var pw = document.getElementById("pagewrap");   // 가장자리 자동 스크롤(2026-07-19): 화면 밖(이전/다음 페이지)으로도 끌어 이동 가능
    if (pw) {
      var wr = pw.getBoundingClientRect();
      if (e.clientY < wr.top + 26 && pw.scrollTop > 0) { pw.scrollTop -= 18; move.y += 18; }
      else if (e.clientY > wr.bottom - 26) { pw.scrollTop += 18; move.y -= 18; }
    }
    var dx = e.clientX - move.x, dy = e.clientY - move.y;
    move.items.forEach(function (it) {
      if (it.line) {   // 선=끝점 4개 함께 이동(좌상단 음수 방지 클램프)
        var mdx = Math.max(dx, -Math.min(it.x1, it.x2)), mdy = Math.max(dy, -Math.min(it.y1, it.y2));
        it.el.setAttribute("data-x1", it.x1 + mdx); it.el.setAttribute("data-y1", it.y1 + mdy);
        it.el.setAttribute("data-x2", it.x2 + mdx); it.el.setAttribute("data-y2", it.y2 + mdy); renderLine(it.el);
      } else { it.el.style.left = Math.max(0, it.l + dx) + "px"; it.el.style.top = Math.max(0, it.t + dy) + "px"; }
    });
    place();
  }
  // 이동 핸들 ✥ (좌상단) — 끌면 선택개체 전부 고정 배치로 전환되며 함께 이동
  mover = document.createElement("div"); mover.id = "objmove"; mover.className = "hidden"; mover.textContent = "✥"; mover.title = "개체 이동(끌기)"; document.body.appendChild(mover);
  fileH = document.createElement("div"); fileH.id = "objfile"; fileH.className = "hidden"; fileH.textContent = "💾"; fileH.title = "파일: 이름 확인·변경·교체"; document.body.appendChild(fileH);
  fileH.addEventListener("pointerdown", function (e) { e.preventDefault(); e.stopPropagation(); });
  fileH.addEventListener("pointerup", function (e) { e.preventDefault(); e.stopPropagation(); if (sel) showFileCtx(sel); });
  mover.addEventListener("pointerdown", function (e) { if (!sel) return; e.preventDefault(); e.stopPropagation(); forEachSel(function (el) { ensureFixedOf(el); }); startMove(e); try { mover.setPointerCapture(e.pointerId); } catch (x) {} });
  mover.addEventListener("pointermove", function (e) { if (!move) return; e.preventDefault(); dragMove(e); });
  mover.addEventListener("pointerup", function (e) { if (move) { move = null; markDirty(); WP.histRecord && WP.histRecord(); } try { mover.releasePointerCapture(e.pointerId); } catch (x) {} });
  if (objtools) {
    objtools.addEventListener("pointerdown", function (e) { if (e.target.closest("[data-obj],[data-shape]")) e.preventDefault(); });
    objtools.addEventListener("pointerup", function (e) {
      var si = e.target.closest("[data-shape]");   // 앞쪽 도형 그리기 아이콘 = 삽입(선택 불필요, 항상 가능)
      if (si) { insertShape(si.getAttribute("data-shape")); return; }
      var b = e.target.closest("[data-obj]"); if (!b) return; var a = b.getAttribute("data-obj");
      if (a === "addsel") { setAddMode(!addMode); toastMsg(addMode ? "다중 선택 모드 — 개체를 탭해 추가/해제 후 [묶기]" : "다중 선택 종료"); return; }
      if (a === "del") delImg(); else if (a === "place") { togglePlacePop(); return; }
      else if (a === "rotL") rotate(-15); else if (a === "rotR") rotate(15);
      else if (a === "flipH") flip("h"); else if (a === "flipV") flip("v");
      else if (a === "group") group(); else if (a === "ungroup") ungroup();
      else if (a === "align") { toggleAlign(); return; }
      else if (a === "zorder") { toggleZorder(); return; }
      else if (a === "crop") { toggleCrop(); return; }
      else if (a === "style") { toggleShapeStyle(); return; }
      else if (a === "copy") { putObjClip(); toastMsg("개체 복사됨 · 붙이기로 삽입"); }
    });
  }
  var shapetools = document.getElementById("shapetools");
  var styleTarget = "fill";   // 팔레트 색 적용 대상(채우기/선색)
  var SHAPE_COLORS = [   // 32색 팔레트(회색 스펙트럼 + 강/연/원색 8열)
    "#000000", "#404040", "#808080", "#a6a6a6", "#d9d9d9", "#f2f2f2", "#ffffff", "#7f6000",
    "#c00000", "#e36c09", "#ffc000", "#00b050", "#00b0b0", "#0070c0", "#7030a0", "#e84c9a",
    "#f4a9a9", "#fbd5b5", "#ffe699", "#a9d18e", "#a6e0e0", "#9dc3e6", "#c9a0dc", "#f6b6d6",
    "#ff0000", "#ffa500", "#ffff00", "#00ff00", "#00ffff", "#0000ff", "#8000ff", "#ff00ff"
  ];
  var palette = document.getElementById("sh-palette");
  if (palette) {
    SHAPE_COLORS.forEach(function (c) {
      var s = document.createElement("button"); s.className = "sw"; s.setAttribute("data-color", c); s.style.background = c; s.title = c;
      palette.appendChild(s);
    });
  }
  function setTarget(t) {
    styleTarget = t;
    if (shapetools) shapetools.querySelectorAll(".tgt").forEach(function (b) { b.classList.toggle("on", b.getAttribute("data-target") === t); });
  }
  if (shapetools) {
    shapetools.addEventListener("pointerdown", function (e) { if (e.target.closest("[data-color],[data-fill],[data-target],[data-sw],[data-dash]")) e.preventDefault(); });
    shapetools.addEventListener("pointerup", function (e) {
      var b = e.target.closest("[data-color],[data-fill],[data-target],[data-sw],[data-dash]"); if (!b) return;
      if (b.hasAttribute("data-target")) setTarget(b.getAttribute("data-target"));
      else if (b.hasAttribute("data-color")) { if (styleTarget === "stroke") shapeStroke(b.getAttribute("data-color")); else shapeFill(b.getAttribute("data-color")); }
      else if (b.hasAttribute("data-fill")) shapeFill(b.getAttribute("data-fill"));   // 없음(투명)
      else if (b.hasAttribute("data-sw")) shapeWidth(parseFloat(b.getAttribute("data-sw")));
      else if (b.hasAttribute("data-dash")) shapeDash(b.getAttribute("data-dash"));
    });
  }
  var zordertools = document.getElementById("zordertools");
  if (zordertools) {
    zordertools.addEventListener("pointerdown", function (e) { if (e.target.closest("[data-z]")) e.preventDefault(); });
    zordertools.addEventListener("pointerup", function (e) { var b = e.target.closest("[data-z]"); if (b) zReorder(b.getAttribute("data-z")); });
  }
  var croptools = document.getElementById("croptools");
  if (croptools) {
    croptools.addEventListener("pointerdown", function (e) { if (e.target.closest("[data-crop]")) e.preventDefault(); });
    croptools.addEventListener("pointerup", function (e) {
      var b = e.target.closest("[data-crop]"); if (!b) return; var a = b.getAttribute("data-crop");
      if (a === "reset") cropReset();
      else if (a.length === 2 && a[1] === "-") cropEdge(a[0], -0.05);   // 되돌리기
      else cropEdge(a, 0.05);                                           // 안쪽 자르기 5%
    });
  }
  var aligntools = document.getElementById("aligntools");
  if (aligntools) {
    aligntools.addEventListener("pointerdown", function (e) { if (e.target.closest("[data-al]")) e.preventDefault(); });
    aligntools.addEventListener("pointerup", function (e) {
      var b = e.target.closest("[data-al]"); if (!b) return; var a = b.getAttribute("data-al");
      if (a === "disth") distribute("h"); else if (a === "distv") distribute("v");
      else if (a === "samew") sameSize("w"); else if (a === "sameh") sameSize("h");
      else align(a);
    });
  }
  // 이미지 클릭=선택(자유배치면 이동 시작), 그 외=해제
  page.addEventListener("pointerdown", function (e) {
    var o = objAt(e.target);
    if (o) {
      var isMedia = o.getAttribute && o.getAttribute("data-obj") === "media";
      if (isMedia && selSet.indexOf(o) >= 0) return;   // 이미 선택된 미디어=재생/일시정지 등 컨트롤 클릭을 그대로 통과(재선택·제스처 간섭 안 함). 이동은 ✥·삭제는 Delete
      if (!isMedia) { e.preventDefault(); e.stopPropagation(); }   // 미디어=재생 컨트롤 클릭 허용(가로채지 않음, 이동은 ✥ 핸들)
      if (e.shiftKey || e.ctrlKey || ctrlHeld || addMode) { toggleSel(o); return; }   // Shift/Ctrl+클릭 또는 ＋선택 모드=집합 토글(이동 시작 안 함, 2026-07-19 Ctrl 추가)
      if (selSet.indexOf(o) < 0) select(o); else sel = o;   // 집합 밖=단일선택 / 집합 안=집합 유지(함께 이동)
      if (!isMedia) startMove(e);                        // fixed 개체들 함께 이동 준비(미디어는 컨트롤 우선)
    } else if (e.target !== handle && e.target !== mover && !(objtools && objtools.contains(e.target))) deselect();
  }, true);
  window.addEventListener("pointermove", function (e) {
    if (lineDrag && sel && isLine(sel)) {   // 끝점 자유 이동 + 수평/수직/45° 스냅(Alt=해제)
      var p = toPage(e.clientX, e.clientY), other = lineDrag.which === 1 ? 2 : 1;
      var ox = +sel.getAttribute("data-x" + other), oy = +sel.getAttribute("data-y" + other), nx = p.x, ny = p.y;
      if (!e.altKey) {
        var dx = nx - ox, dy = ny - oy, adx = Math.abs(dx), ady = Math.abs(dy);
        if (ady < 12) ny = oy;                       // 수평
        else if (adx < 12) nx = ox;                  // 수직
        else if (Math.abs(adx - ady) < 14) { var m = (adx + ady) / 2; nx = ox + (dx < 0 ? -m : m); ny = oy + (dy < 0 ? -m : m); }   // 45°
      }
      nx = Math.max(0, Math.round(nx)); ny = Math.max(0, Math.round(ny));
      sel.setAttribute("data-x" + lineDrag.which, nx); sel.setAttribute("data-y" + lineDrag.which, ny);
      renderLine(sel); place(); e.preventDefault(); return;
    }
    if (drag && sel) {
      var w = drag.w0, h = drag.h0;
      if (drag.mode !== "h") w = drag.w0 + (e.clientX - drag.x0);   // 가로: 모서리·우변
      if (drag.mode !== "w") h = drag.h0 + (e.clientY - drag.y0);   // 세로: 모서리·하변
      if (drag.mode === "corner" && e.shiftKey && drag.w0 && drag.h0) { var s = Math.max(w / drag.w0, h / drag.h0); w = drag.w0 * s; h = drag.h0 * s; }   // Shift=비율고정
      setWH(w, h); e.preventDefault(); return;
    }
    if (move) { dragMove(e); e.preventDefault(); }
  });
  window.addEventListener("pointerup", function () { if (drag || move || lineDrag) { drag = null; move = null; lineDrag = null; markDirty(); WP.histRecord && WP.histRecord(); } });   // 드롭 시점 1회만 히스토리 기록(중간 프레임 미기록)
  // ── 범위(고무줄) 개체선택(2026-07-19): Ctrl+드래그(게임패드 D패드↓=Ctrl) 또는 ＋선택 모드 드래그.
  //    점선 상자와 겹치는 개체 일괄 선택 → 기존 다중선택(selSet) 합류(묶기/풀기/✕/이동/맞춤 공용).
  var ctrlHeld = false;   // ⚠️이 기기 WPE=포인터 이벤트에 ctrlKey 미탑재 가능 → 키 이벤트로 직접 추적(D패드↓=Ctrl 홀드 포함)
  window.addEventListener("keydown", function (e) { if (e.key === "Control") ctrlHeld = true; }, true);
  window.addEventListener("keyup", function (e) { if (e.key === "Control") ctrlHeld = false; }, true);
  window.addEventListener("blur", function () { ctrlHeld = false; });
  var marq = null, marqEl = null;
  function _mRect(a, x, y) { return { l: Math.min(a.x, x), t: Math.min(a.y, y), r: Math.max(a.x, x), b: Math.max(a.y, y) }; }
  page.addEventListener("pointerdown", function (e) {
    if (e.button !== 0 || !(e.ctrlKey || ctrlHeld || addMode)) return;
    if (objAt(e.target)) return;                       // 개체 위 시작=기존 탭(추가선택) 경로
    marq = { x: e.clientX, y: e.clientY, on: false };
    e.preventDefault(); e.stopPropagation();           // 텍스트 드래그선택 차단
  }, true);
  window.addEventListener("pointermove", function (e) {
    if (!marq) return;
    var r = _mRect(marq, e.clientX, e.clientY);
    if (!marq.on && (r.r - r.l) + (r.b - r.t) < 6) return;   // 6px 미만=클릭
    marq.on = true;
    if (!marqEl) { marqEl = document.createElement("div"); marqEl.id = "marquee"; document.body.appendChild(marqEl); }
    marqEl.style.left = r.l + "px"; marqEl.style.top = r.t + "px";
    marqEl.style.width = (r.r - r.l) + "px"; marqEl.style.height = (r.b - r.t) + "px";
    marqEl.classList.remove("hidden");
    e.preventDefault();
  }, true);
  window.addEventListener("pointerup", function (e) {
    if (!marq) return;
    var was = marq; marq = null;
    if (marqEl) marqEl.classList.add("hidden");
    if (!was.on) return;
    var rr = _mRect(was, e.clientX, e.clientY);
    var hits = [].slice.call(page.querySelectorAll(".wp-obj,table")).filter(function (el) {
      if (el.tagName === "TABLE" && el.closest(".wp-obj")) return false;   // 개체 안 중첩표 제외
      var b = el.getBoundingClientRect();
      return b.right > rr.l && b.left < rr.r && b.bottom > rr.t && b.top < rr.b;   // 겹침 판정
    });
    hits = hits.filter(function (el) { return !hits.some(function (o) { return o !== el && o.contains(el); }); });   // 최상위만
    if (!hits.length) { toastMsg("범위 안에 개체가 없습니다"); return; }
    deselect();
    hits.forEach(function (el) { toggleSel(el); });
    toastMsg("개체 " + hits.length + "개 선택 — 묶기/맞춤/✕ 가능");
  }, true);

  // ── 삭제 가드(2026-07-19 정책): Backspace/Delete=글자만. 개체(그림·미디어·도형·표)는 캐럿이 건너뛰고,
  //    범위 삭제도 텍스트만 지우고 개체는 제자리 보존. 개체 삭제=개별 선택([✕]/Delete)으로 통일.
  // ⭐개체 삭제 제외(2026-07-24 재설계 — 심플): 개체(그림·미디어·도형·표)는 백스페이스/Delete로
  //   **절대 안 지워지는 "벽"**이다. 삭제가 개체에 닿으면 개체는 그대로 두고 **캐럿만 개체 너머로
  //   건너뛴다**. 개체 삭제는 개별 선택 후 [✕]/Delete 전용(2026-07-19 정책).
  //   근거: beforeinput의 getTargetRanges가 브라우저의 **실제 삭제 대상**을 정확히 준다(WPE·WebKit 지원
  //   실측). 개체가 그 안에 있으면 삭제를 통째로 막고 건너뛰기만 — 텍스트를 개체 사이로 쪼개 지우던
  //   종전 방식은 캐럿이 개체에 고착돼 "더 못 지움" 버그를 냈다. 삭제 대상에 개체가 없으면 손 안 댄다.
  var OSEL = ".wp-obj,.objline,table,img,video,audio";
  function topObjsIn(range) {   // range와 교차하는 최상위 개체(래퍼 안 맨몸 img·미디어 중복 제거)
    return [].slice.call(page.querySelectorAll(OSEL)).filter(function (el) {
      if (el.tagName === "IMG" && el.closest(".wp-obj,.objline")) return false;
      if ((el.tagName === "VIDEO" || el.tagName === "AUDIO") && el.closest(".wp-obj,.objline")) return false;
      try { return range.intersectsNode(el); } catch (x) { return false; }
    }).filter(function (el, i, arr) { return !arr.some(function (o) { return o !== el && o.contains(el); }); });
  }
  page.addEventListener("beforeinput", function (e) {
    if (!/^delete/i.test(e.inputType)) return;
    if (sel) return;                                  // 개체 선택 상태=명시 삭제(delImg)
    var trs = e.getTargetRanges ? e.getTargetRanges() : null;
    if (!trs || !trs.length) return;                  // getTargetRanges 없음=기본 삭제(구형 폴백)
    var tr = trs[0], r = document.createRange();
    try { r.setStart(tr.startContainer, tr.startOffset); r.setEnd(tr.endContainer, tr.endOffset); } catch (x) { return; }
    var objs = topObjsIn(r);
    if (!objs.length) return;                          // 개체 없음=기본 삭제 그대로(텍스트)
    e.preventDefault();
    objs.sort(function (a, b) { return (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING) ? -1 : 1; });
    var sel0 = window.getSelection(), c = document.createRange();
    if (sel0 && !sel0.isCollapsed) {
      // ── 블럭(범위) 선택 삭제: 개체는 보존, **개체 사이 텍스트만** 지운다(2026-07-19 정책) ──
      //   개체를 삭제범위에서 제외하고 조각별 deleteContents(개체 제자리·무분할). 캐럿=마지막 개체 뒤 형제.
      var cur = r.cloneRange(), segs = [];
      objs.forEach(function (o) {
        var seg = cur.cloneRange();
        try { seg.setEndBefore(o); } catch (x) { return; }
        segs.push(seg);
        try { cur.setStartAfter(o); } catch (x) {}
      });
      segs.push(cur);
      segs.reverse().forEach(function (seg) { try { seg.deleteContents(); } catch (x) {} });   // 역순=앞 삭제가 뒤 range 무효화 안 함
      var lastB = objs[objs.length - 1], nxB = lastB.nextSibling;
      if (nxB) c.setStart(nxB, 0); else { try { c.setStartAfter(lastB); } catch (x) {} }
    } else {
      // ── 캐럿 삭제(백스페이스/Delete 연타): 개체는 삭제 안 하고 캐럿만 개체 **바깥 형제**로 건너뛴다 ──
      //   ⚠️setStartBefore/After(개체)는 WPE가 캐럿을 개체 div 안(DIV@1)으로 정규화해 무한 정지 → 개체
      //   자체가 아니라 앞/뒤 형제 노드에 놓는다. 형제 없으면 빈 문단을 앵커로 삽입.
      if (/backward/i.test(e.inputType)) {
        var prev = objs[0].previousSibling;
        if (!prev) { prev = document.createElement("p"); prev.appendChild(document.createElement("br")); objs[0].parentNode.insertBefore(prev, objs[0]); }
        c.setStart(prev, prev.nodeType === 3 ? prev.nodeValue.length : prev.childNodes.length);
      } else {
        var last = objs[objs.length - 1], nx = last.nextSibling;
        if (!nx) { nx = document.createElement("p"); nx.appendChild(document.createElement("br")); last.parentNode.appendChild(nx); }
        c.setStart(nx, 0);
      }
    }
    c.collapse(true);
    var s = window.getSelection(); s.removeAllRanges(); s.addRange(c);
    markDirty(); WP.histRecord && WP.histRecord();
    WP.status && WP.status("개체는 보존 — 개체 삭제는 선택 후 ✕");
  }, true);

  // ── 미디어 우클릭: 파일명 확인·이름변경·다른 파일로 교체(2026-07-19) ──
  var mctx = null;
  function hideMediaCtx() { if (mctx) mctx.classList.add("hidden"); }
  function showFileCtx(el) {   // 파일핸들 💾 진입점: 미디어=이름·교체 / 그림=교체(내장 data URI라 이름 없음)
    var r = el.getBoundingClientRect(), x = Math.round(r.left + 16), y = Math.round(r.top + 16);
    var kind = el.getAttribute && el.getAttribute("data-obj");
    if (kind === "media") { showMediaCtx(el, x, y); return; }
    // 그림(.wp-obj[data-obj=img] 또는 IMG): 문서에 내장(base64)이라 파일명 없음 → 교체만
    var img = el.tagName === "IMG" ? el : el.querySelector("img");
    if (!img) { toastMsg("파일 편집을 지원하지 않는 개체"); return; }
    if (WP.imagePick) WP.imagePick(function (src) {
      img.setAttribute("src", src);
      markDirty(); toastMsg("그림 파일 교체됨");
    }); else toastMsg("그림 피커 로드 실패");
  }
  function showMediaCtx(wrapEl, x, y) {
    var v = wrapEl.querySelector("video,audio"); if (!v) return;
    var curSrc = v.getAttribute("src") || "", curEnc = curSrc.split("/").pop() || "", cur = "";
    if (curSrc.slice(0, 5) === "blob:") cur = (WP._mediaNames && WP._mediaNames[curSrc]) || "";   // IDB 재생 중=맵에서 파일명(P10)
    else try { cur = decodeURIComponent(curEnc); } catch (e) { cur = curEnc; }
    if (!mctx) {
      mctx = document.createElement("div"); mctx.id = "mediactx";
      document.body.appendChild(mctx);
      mctx.addEventListener("pointerdown", function (e) { e.stopPropagation(); });
    }
    mctx.innerHTML = '<div class="mc-path">📁 미디어 파일 이름</div><input id="mc-name" type="text" spellcheck="false">' +
      '<div class="mc-btns"><button id="mc-ren">이름 변경</button><button id="mc-swap">다른 파일로</button><button id="mc-x">닫기</button></div>';
    mctx.querySelector("#mc-name").value = cur;
    mctx.classList.remove("hidden");
    mctx.style.left = Math.max(2, Math.min(x, window.innerWidth - 240)) + "px";
    mctx.style.top = Math.max(2, Math.min(y, window.innerHeight - 110)) + "px";
    mctx.querySelector("#mc-x").addEventListener("pointerup", hideMediaCtx);
    mctx.querySelector("#mc-ren").addEventListener("pointerup", function () {
      var nn = mctx.querySelector("#mc-name").value.trim();
      if (!nn || nn === cur) { hideMediaCtx(); return; }
      var applyRefs = function (newName) {   // 문서 참조 전부 갱신: blob 재생=이름 맵만, 경로형=파일명 부분 치환
        page.querySelectorAll("video,audio").forEach(function (m) {
          var s = m.getAttribute("src") || "";
          if (s.slice(0, 5) === "blob:") { if (WP._mediaNames && WP._mediaNames[s] === cur) WP._mediaNames[s] = newName; }
          else if (s.split("/").pop() === curEnc) { m.setAttribute("src", s.slice(0, s.length - curEnc.length) + encodeURIComponent(newName)); try { m.load(); } catch (e2) {} }
        });
        markDirty(); toastMsg("이름 변경됨: " + newName); hideMediaCtx();
      };
      WP.mediaGet(cur, function (b) {
        if (b) {   // 이 브라우저 IDB 미디어(P10): 키 rename + 참조 갱신 — 서버 무관(공개모드 무영향)
          WP.mediaRename(cur, nn, function (okp, why) { if (!okp) { toastMsg("이름 변경 실패: " + (why || "IndexedDB")); return; } applyRefs(nn); });
          return;
        }
        WP.api("/api/media/rename", { old: cur, "new": nn }).then(function (r) {   // 서버 문서/media 파일용 존치(P4 라우트)
          if (!(r && r.ok)) { toastMsg("이름 변경 실패: " + ((r && r.err) || "")); return; }
          applyRefs(r.name);
        }).catch(function () { toastMsg("이름 변경 실패(연결)"); });
      });
    });
    mctx.querySelector("#mc-swap").addEventListener("pointerup", function () {
      hideMediaCtx();
      if (WP.mediaPick) WP.mediaPick(function (src, kind) {   // 교체모드: 이 개체의 src만 바꿈
        v.setAttribute("src", src); wrapEl.setAttribute("data-media", kind || "video");
        try { v.load(); } catch (e2) {}
        markDirty(); toastMsg("미디어 파일 교체됨");
      });
    });
  }
  page.addEventListener("contextmenu", function (e) {
    var w = e.target && e.target.closest && e.target.closest('.wp-obj[data-obj="media"]');
    if (!w) { hideMediaCtx(); return; }
    e.preventDefault(); e.stopPropagation();   // 일반 컨텍스트메뉴(wp-input) 대신 미디어 메뉴
    showMediaCtx(w, e.clientX, e.clientY);
  }, true);
  document.addEventListener("pointerdown", function (e) { if (mctx && !mctx.classList.contains("hidden") && !mctx.contains(e.target)) hideMediaCtx(); }, true);

  // ── 미디어 몸통 클릭=재생/일시정지 토글(2026-07-19): 비디오 몸통 click의 네이티브 동작은 원래 '없음'
  //    (컨트롤바만 반응) → 유튜브식으로 몸통 클릭 토글을 직접 구현. 하단 컨트롤바(시크 등)=네이티브에 맡김.
  //    드리프트/드래그 오발동 방지=다운·업 8px 거리 가드.
  var _mDown = null;
  page.addEventListener("pointerdown", function (e) {
    if (e.button !== 0) { _mDown = null; return; }   // 좌클릭만(btn2 오는 장치 대비)
    var t = e.target, v = null;
    if (t && (t.tagName === "VIDEO" || t.tagName === "AUDIO")) v = t;
    else if (t && t.closest) { var w = t.closest('.wp-obj[data-obj="media"]'); if (w) v = w.querySelector("video,audio"); }
    _mDown = v ? { x: e.clientX, y: e.clientY, v: v } : null;   // ⚠️눌린 시점의 비디오를 기억 — 선택 직후 핸들/도구상자가 릴리즈 지점을 덮어 pointerup target이 바뀌는 문제 회피(2026-07-19)
  }, true);
  window.addEventListener("pointerup", function (e) {   // page 아님=오버레이(핸들·도구상자) 위에서 떼도 수신
    if (!_mDown) return;
    var d = _mDown; _mDown = null;
    if (e.button !== 0) return;
    if (Math.abs(e.clientX - d.x) + Math.abs(e.clientY - d.y) > 8) return;   // 드래그(이동/크기)면 무시
    var v = d.v, r = v.getBoundingClientRect();
    if (v.tagName === "VIDEO" && d.y > r.bottom - 44) return;   // 컨트롤바 영역=네이티브(시크·볼륨)
    if (v.paused) { var p = v.play(); if (p && p.catch) p.catch(function () {}); } else v.pause();
  }, true);
  document.addEventListener("keydown", function (e) {
    if (!sel) return;
    if (e.key === "Delete" || e.key === "Backspace") { e.preventDefault(); delImg(); }
    else if (e.key === "Escape") { deselect(); }
    else if (isHr(sel)) { return; }   // 가로줄=크기조절 없음(Delete/Esc만)
    else if (e.key === "ArrowRight" || e.key === "ArrowUp") { e.preventDefault(); if (isLine(sel)) lineLen(sel, 8); else setW(sel.getBoundingClientRect().width + 8); markDirty(); }
    else if (e.key === "ArrowLeft" || e.key === "ArrowDown") { e.preventDefault(); if (isLine(sel)) lineLen(sel, -8); else setW(sel.getBoundingClientRect().width - 8); markDirty(); }
  });
  // ── 개체 복사/오려두기(선택 상태) → WP._objClip + 선호플래그. 붙이기는 wp-core paste가 insertObjClip(새 oid는 select 지연할당) ──
  function putObjClip() {
    if (!selSet.length) return;
    var html = "";
    forEachSel(function (el) {
      if (!el.classList || (!el.classList.contains("wp-obj") && el.tagName !== "IMG")) return;   // 개체(.wp-obj)·이미지만 직렬화(도구버튼 등 오염 방지)
      var c = el.cloneNode(true); c.classList.remove("obj-sel");
      if (!c.getAttribute("class")) c.removeAttribute("class");
      c.removeAttribute("data-oid");
      c.querySelectorAll("[data-oid]").forEach(function (x) { x.removeAttribute("data-oid"); });   // 그룹 자식 등 하위 oid도 제거(중복 방지)
      html += c.outerHTML;
    });
    var clip = { html: html, pasteN: 0 };
    clip.onPaste = function (nodes) {   // 붙일 때마다 우하향 16px 캐스케이드(자유배치=같은 좌표 겹침 방지). 흐름배치는 다음 블록으로 가 안 겹침
      var d = (++clip.pasteN) * 16;
      nodes.forEach(function (n) {
        if (n.nodeType !== 1 || !n.getAttribute) return;
        if (isLine(n)) {   // 선=끝점 모델 → data-x/y 오프셋 후 재렌더(style.left/top만 바꾸면 다음 조작에서 원위치로 되돌아감)
          ["data-x1", "data-y1", "data-x2", "data-y2"].forEach(function (k) { n.setAttribute(k, (+n.getAttribute(k) || 0) + d); });
          renderLine(n);
        } else if (n.getAttribute("data-place") === "fixed") {
          n.style.left = ((parseFloat(n.style.left) || 0) + d) + "px";
          n.style.top = ((parseFloat(n.style.top) || 0) + d) + "px";
        }
      });
    };
    WP._objClip = clip; WP._objClipPreferred = true; WP._lastClip = "obj";
  }
  // ── 텍스트 내부 클립(2026-07-18, WP_EMBED 전용): WPE 네이티브 pasteboard가 UTF-8을 Latin-1로 오해독(한글 3바이트가
  //    í+C1제어문자로 쪼개져 tofu) → 개체클립(_objClip)과 같은 방식으로 텍스트도 내부 클립 경유해 우회.
  //    웹 브라우저는 네이티브 클립보드 정상 → 게이트 없이 켜면 외부앱→한워드 붙여넣기 오염(절대 금지)
  var WP_EMBED = (location.protocol === "wp:");   // 임베드 판정
  function txtGrab() {   // 현재 텍스트 선택 캡처: contenteditable=Range 직렬화(서식보존), input/textarea=값 부분
    try {
      var a = document.activeElement;
      if (a && (a.tagName === "INPUT" || a.tagName === "TEXTAREA")) {
        if (typeof a.selectionStart === "number" && a.selectionStart !== a.selectionEnd)
          return { text: a.value.substring(a.selectionStart, a.selectionEnd), html: "" };
        return null;
      }
      var s = window.getSelection();
      if (!s || s.isCollapsed || !s.rangeCount) return null;
      var d = document.createElement("div"); d.appendChild(s.getRangeAt(0).cloneContents());
      return { text: s.toString(), html: d.innerHTML };
    } catch (e) { return null; }
  }
  function txtPut(e) {   // 캡처 성공 시 네이티브 경로 차단 + 내부 클립 적재(setData는 미래 호환 best-effort)
    var t = txtGrab(); if (!t || (!t.text && !t.html)) return false;
    e.preventDefault();
    try { e.clipboardData.setData("text/plain", t.text); if (t.html) e.clipboardData.setData("text/html", t.html); } catch (x) {}
    WP._txtClip = t; WP._lastClip = "txt";
    return true;
  }
  document.addEventListener("copy", function (e) {
    if (sel) { e.preventDefault(); putObjClip(); toastMsg("개체 복사됨 · 붙이기로 삽입"); }
    else { WP._objClipPreferred = false; if (WP_EMBED) txtPut(e); }   // 일반 텍스트 복사 → 개체 선호 해제. 임베드만 내부 텍스트클립(한글 안전)
  });
  document.addEventListener("cut", function (e) {
    if (sel) { e.preventDefault(); putObjClip(); delImg(); toastMsg("개체 오려둠 · 붙이기로 삽입"); }
    else { WP._objClipPreferred = false; if (WP_EMBED && txtPut(e)) { document.execCommand("delete"); if (WP.markDirty) WP.markDirty(); } }
  });
  var pw = document.getElementById("pagewrap"), scrollRaf = null;   // 스크롤 시 핸들 재배치 rAF 스로틀(프레임당 1회·강제 레이아웃 최소화)
  if (pw) pw.addEventListener("scroll", function () { if (scrollRaf || !sel) return; scrollRaf = requestAnimationFrame(function () { scrollRaf = null; if (sel) place(); }); });

  // ── 캡션(#22): 표/그림 아래 설명 문단 p.caption[data-cap=tbl|img] + 자동번호(문서 순서 재계산) ──
  //   HWPX는 캡션 요소 미지원(python-hwpx) → 일반 문단으로 내보내짐(텍스트 무손실).
  function capPrefixFix(c, want) {   // 캡션의 선두 "표 N. " 프리픽스만 교정(내부 서식 보존). 끝 구분공백=NBSP(일반 공백은 문단 끝에서 접혀 타이핑 시 "표 1.실험"이 됨)
    var tw = document.createTreeWalker(c, NodeFilter.SHOW_TEXT), first = tw.nextNode();
    if (!first) { c.textContent = want; return; }
    var m = first.nodeValue.match(/^(표|그림)[\s\u00A0]*\d*[\s\u00A0]*[.:]?[\s\u00A0]*/);
    first.nodeValue = want + (m ? first.nodeValue.slice(m[0].length) : first.nodeValue);
  }
  function capRenumber() {
    // 구분공백=NBSP: 일반 공백은 문단 끝에서 브라우저가 접어 캡션 직후 타이핑 시 "표 1.실험"이 됨
    var cnt = { tbl: 0, img: 0 }, LABEL = { tbl: "표 ", img: "그림 " };
    page.querySelectorAll("p.caption").forEach(function (c) {
      var k = c.getAttribute("data-cap") === "img" ? "img" : "tbl";
      capPrefixFix(c, LABEL[k] + (++cnt[k]) + ".\u00A0");
    });
  }
  function capAnchor() {   // 캡션 대상 결정: 그림 선택 > 캐럿 표 안 > 캐럿 기존 캡션 위
    if (sel) { var b = sel; while (b && b.parentNode !== page) b = b.parentNode; return b ? { el: b, kind: "img" } : null; }
    var s = window.getSelection(), n = s && s.anchorNode; if (!n) return null;
    n = n.nodeType === 1 ? n : n.parentNode;
    if (!n || !n.closest || !page.contains(n)) return null;
    var c = n.closest("p.caption");
    if (c) return { el: c, kind: c.getAttribute("data-cap") || "tbl", isCaption: true };
    var t = n.closest("table");
    if (t) { var top = t; while (top.parentNode !== page) top = top.parentNode; return { el: top, kind: "tbl" }; }
    return null;
  }
  function caption() {
    var a = capAnchor();
    if (!a) { toastMsg("표 안에 커서를 두거나 그림을 선택한 뒤 실행하세요"); return; }
    var el;
    if (a.isCaption) el = a.el;
    else {
      var nx = a.el.nextElementSibling;
      if (nx && nx.classList && nx.classList.contains("caption") && nx.getAttribute("data-cap") === a.kind) el = nx;   // 이미 있으면 포커스만(중복 방지)
      else {
        el = document.createElement("p"); el.className = "caption"; el.setAttribute("data-cap", a.kind);
        a.el.parentNode.insertBefore(el, a.el.nextSibling);
      }
    }
    deselect();   // 그림 선택 상태면 해제(캡션 타이핑과 Del 핸들러 충돌 방지)
    capRenumber();
    var r = document.createRange(); r.selectNodeContents(el); r.collapse(false);
    var s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
    page.focus(); markDirty();
    toastMsg((a.kind === "img" ? "그림" : "표") + " 캡션 — 설명을 입력하세요");
  }
  // ── 미주(#25 A안): 마커 sup.fn[data-fn] + 문서 끝 div.fnotes 주석 섹션 ──
  //   번호는 문서 순 재계산(캡션 패턴), 항목도 마커 순으로 재정렬. 마커↔항목 클릭 왕복(#24 목차 패턴).
  //   HWPX는 hwpxconv가 hp:endNote로 정확 매핑(fnotes 섹션은 내보내기에서 제외), PDF는 일반 블록 흐름.
  function fnMarks() { return Array.prototype.filter.call(page.querySelectorAll("sup.fn"), function (s) { return !s.closest(".fnotes"); }); }
  function fnSection(make) {
    var f = page.querySelector("div.fnotes");
    if (!f && make) {
      f = document.createElement("div"); f.className = "fnotes";
      var t = document.createElement("p"); t.className = "fn-t"; t.setAttribute("contenteditable", "false"); t.textContent = "주석"; f.appendChild(t);
      page.insertBefore(f, page.querySelector("#wpmeta"));   // wpmeta(숨김 메타)보다 앞 = 실질 문서 끝
    }
    return f;
  }
  function fnRenum() {
    var f = fnSection(false), n = 0, itemsById = {}, last = f && f.querySelector("p.fn-t");
    if (f) Array.prototype.forEach.call(f.querySelectorAll("p.fn-e"), function (e) { itemsById[e.getAttribute("data-fn")] = e; });
    fnMarks().forEach(function (mk) {
      n++; mk.textContent = n + ")";
      var e = itemsById[mk.getAttribute("data-fn")];
      if (e) {
        var no = e.querySelector(".fn-no"); if (no) no.textContent = n + ")";
        if (last && last.nextSibling !== e) f.insertBefore(e, last.nextSibling);   // 마커 순서대로 재배치
        last = e; delete itemsById[mk.getAttribute("data-fn")];
      }
    });
    Object.keys(itemsById).forEach(function (id) {   // 고아 항목(마커 삭제됨): 내용 보존, ×) 표기로 끝에
      var e = itemsById[id], no = e.querySelector(".fn-no"); if (no) no.textContent = "×)"; f.appendChild(e);
    });
    return n;
  }
  function fnInsert() {
    var s = window.getSelection();
    if (!s || !s.rangeCount || !page.contains(s.anchorNode)) { toastMsg("본문에 커서를 두고 실행하세요"); return; }
    var an = s.anchorNode, ael = an.nodeType === 1 ? an : an.parentNode;
    if (ael && ael.closest && ael.closest(".fnotes")) { toastMsg("주석 안에는 미주를 넣을 수 없습니다"); return; }
    var max = 0;
    Array.prototype.forEach.call(page.querySelectorAll("sup.fn, p.fn-e"), function (e) { var v = parseInt(e.getAttribute("data-fn"), 10); if (v > max) max = v; });
    var id = max + 1;
    document.execCommand("insertHTML", false, '<sup class="fn" contenteditable="false" data-fn="' + id + '">?)</sup>');
    var f = fnSection(true);
    var e = document.createElement("p"); e.className = "fn-e"; e.setAttribute("data-fn", id);
    var no = document.createElement("span"); no.className = "fn-no"; no.setAttribute("contenteditable", "false"); no.title = "클릭=본문 마커로"; no.textContent = "?)";
    e.appendChild(no); e.appendChild(document.createTextNode(" "));
    f.appendChild(e); fnRenum();
    var r = document.createRange(); r.selectNodeContents(e); r.collapse(false);
    s.removeAllRanges(); s.addRange(r);
    e.scrollIntoView({ block: "center" });
    page.focus(); markDirty();
    toastMsg("미주 — 내용을 입력하세요 (번호 클릭=본문 복귀)");
  }
  page.addEventListener("click", function (e) {   // 마커 클릭=항목으로, 항목 번호 클릭=마커로(#24 패턴)
    var mk = e.target.closest && e.target.closest("sup.fn");
    if (mk && page.contains(mk) && !mk.closest(".fnotes")) {
      var it = page.querySelector('.fnotes p.fn-e[data-fn="' + mk.getAttribute("data-fn") + '"]');
      if (it) {
        var r = document.createRange(); r.selectNodeContents(it); r.collapse(false);
        var s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
        it.scrollIntoView({ block: "center" }); WP.status && WP.status("미주 " + mk.textContent + " 항목");
      }
      return;
    }
    var no = e.target.closest && e.target.closest(".fn-no");
    if (no && page.contains(no)) {
      var pe = no.closest("p.fn-e"), id = pe && pe.getAttribute("data-fn"), back = null;
      fnMarks().forEach(function (m2) { if (m2.getAttribute("data-fn") === id) back = m2; });
      if (back) {
        back.scrollIntoView({ block: "center" });
        var r2 = document.createRange(); r2.setStartAfter(back); r2.collapse(true);
        var s2 = window.getSelection(); s2.removeAllRanges(); s2.addRange(r2);
        WP.status && WP.status("본문 복귀: 미주 " + back.textContent);
      }
    }
  });

  // ── 도형 삽입(배치모델): 개체=#page 직속 .wp-obj 블록 1개, 내용=인라인 SVG(벡터·경량) ──
  //   preserveAspectRatio="none" → 래퍼 width/height에 SVG가 늘어남(핸들 리사이즈=래퍼 크기만 변경).
  var SHAPE_SVG = {
    rect: '<svg viewBox="0 0 100 60" preserveAspectRatio="none"><rect x="2" y="2" width="96" height="56" fill="#cfe0f5" stroke="#2f6fd6" stroke-width="2"/></svg>',
    circle: '<svg viewBox="0 0 100 100" preserveAspectRatio="none"><ellipse cx="50" cy="50" rx="48" ry="48" fill="#cfe0f5" stroke="#2f6fd6" stroke-width="2"/></svg>',
    line: '<svg viewBox="0 0 100 60" preserveAspectRatio="none"><line x1="2" y1="58" x2="98" y2="2" stroke="#2f6fd6" stroke-width="3" vector-effect="non-scaling-stroke"/></svg>'
  };
  var SHAPE_SIZE = { rect: [120, 72], circle: [96, 96], line: [160, 20] };
  var SHAPE_NAME = { rect: "사각형", circle: "원", line: "선" };
  // ── 선(2끝점 자유드래그 모델): 끝점=#page 상대 px(data-x1/y1/x2/y2). 상자=두 점 bbox(fixed). 수평·수직·사선 자유 ──
  function isLine(el) { return !!(el && el.getAttribute && el.getAttribute("data-shape") === "line"); }
  function pgOrigin() { var pr = page.getBoundingClientRect(); return { x: pr.left + page.clientLeft, y: pr.top + page.clientTop }; }   // absolute 자식 좌표 원점(패딩 에지)
  function toPage(cx, cy) { var o = pgOrigin(); return { x: cx - o.x, y: cy - o.y }; }
  function renderLine(el) {
    var x1 = +el.getAttribute("data-x1"), y1 = +el.getAttribute("data-y1"), x2 = +el.getAttribute("data-x2"), y2 = +el.getAttribute("data-y2");
    var pad = 8, minX = Math.min(x1, x2) - pad, minY = Math.min(y1, y2) - pad, maxX = Math.max(x1, x2) + pad, maxY = Math.max(y1, y2) + pad;
    var w = Math.max(1, maxX - minX), h = Math.max(1, maxY - minY);
    el.setAttribute("data-place", "fixed"); el.style.left = minX + "px"; el.style.top = minY + "px"; el.style.width = w + "px"; el.style.height = h + "px";
    var p = el.querySelector("line"), stroke = p ? (p.getAttribute("stroke") || "#2f6fd6") : "#2f6fd6", sw = p ? (p.getAttribute("stroke-width") || "3") : "3", dash = p ? (p.getAttribute("stroke-dasharray") || "") : "";
    el.innerHTML = '<svg width="100%" height="100%" viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none"><line x1="' + (x1 - minX) + '" y1="' + (y1 - minY) + '" x2="' + (x2 - minX) + '" y2="' + (y2 - minY) + '" stroke="' + stroke + '" stroke-width="' + sw + '"' + (dash ? ' stroke-dasharray="' + dash + '"' : '') + ' vector-effect="non-scaling-stroke" stroke-linecap="round"/></svg>';
  }
  function translateLine(el, dx, dy) {   // 선을 끝점 4개째 평행이동(맞춤/배분에서 style.left/top 대신 사용)
    ["1", "2"].forEach(function (k) { el.setAttribute("data-x" + k, Math.round((+el.getAttribute("data-x" + k) || 0) + dx)); el.setAttribute("data-y" + k, Math.round((+el.getAttribute("data-y" + k) || 0) + dy)); });
    renderLine(el);
  }
  function lineLen(el, d) {   // 선 길이 조절(끝점2를 방향 유지한 채 ±d px)
    var x1 = +el.getAttribute("data-x1"), y1 = +el.getAttribute("data-y1"), x2 = +el.getAttribute("data-x2"), y2 = +el.getAttribute("data-y2");
    var dx = x2 - x1, dy = y2 - y1, len = Math.hypot(dx, dy) || 1, nl = Math.max(10, len + d);
    el.setAttribute("data-x2", Math.round(x1 + dx / len * nl)); el.setAttribute("data-y2", Math.round(y1 + dy / len * nl));
    renderLine(el); place();
  }
  function insertLine() {
    var d = document.createElement("div");
    d.className = "wp-obj"; d.setAttribute("data-obj", "shape"); d.setAttribute("data-shape", "line");
    d.setAttribute("data-oid", nextOid()); d.setAttribute("contenteditable", "false"); d.setAttribute("data-place", "fixed");
    var pw = document.getElementById("pagewrap"), y = Math.round((pw ? pw.scrollTop : 0) + 70);   // 스크롤 컨테이너=pagewrap(page.scrollTop은 항상 0) → 현재 보이는 위치에 삽입
    d.setAttribute("data-x1", "40"); d.setAttribute("data-y1", "" + y); d.setAttribute("data-x2", "230"); d.setAttribute("data-y2", "" + y);
    if (WP.insertBlocks) WP.insertBlocks([d]); else page.appendChild(d);
    renderLine(d);
    if (WP.normalizeEdges) WP.normalizeEdges();
    select(d);
    toastMsg("선 삽입 — 양 끝점(●)을 끌어 수평·수직·사선 자유 변환. Alt=각도 스냅 해제");
  }
  function insertShape(kind) {
    if (kind === "line") { insertLine(); return; }
    if (!SHAPE_SVG[kind]) return;
    var sz = SHAPE_SIZE[kind], d = document.createElement("span");
    d.className = "wp-obj"; d.setAttribute("data-obj", "shape"); d.setAttribute("data-shape", kind);
    d.setAttribute("data-oid", nextOid()); d.setAttribute("contenteditable", "false");
    d.style.width = sz[0] + "px"; d.style.height = sz[1] + "px";
    d.innerHTML = SHAPE_SVG[kind];
    insertObjInline(d);
    select(d);
    toastMsg(SHAPE_NAME[kind] + " 삽입 — 핸들 크기 · [배치] 자유배치 · [스타일] 색·선");
  }
  // ── 도형 스타일(채우기색·선색·선두께·선종류): SVG 기본요소 속성 → 블록 outerHTML 직렬화 → undo 자동 ──
  function shapePrimary(d) { return d.querySelector("rect,ellipse,circle,line,polygon,path"); }
  function styleShapes(fn) {
    var n = 0;
    forEachSel(function (el) { if (el.getAttribute("data-obj") === "shape") { var p = shapePrimary(el); if (p) { fn(p, el); n++; } } });
    if (!n) { toastMsg("도형을 선택하세요"); return false; }
    place(); markDirty(); WP.histRecord && WP.histRecord(); return true;
  }
  function shapeFill(color) { if (styleShapes(function (p) { if (p.tagName.toLowerCase() !== "line") p.setAttribute("fill", color); })) toastMsg("채우기: " + (color === "none" ? "없음(투명)" : color)); }
  function shapeStroke(color) { if (styleShapes(function (p) { p.setAttribute("stroke", color); })) toastMsg("선 색: " + color); }
  function shapeWidth(delta) { if (styleShapes(function (p) { p.setAttribute("stroke-width", Math.max(0.5, Math.round(((parseFloat(p.getAttribute("stroke-width")) || 2) + delta) * 2) / 2)); })) toastMsg("선 두께 " + (delta > 0 ? "굵게" : "얇게")); }
  function shapeDash(kind) {
    var v = kind === "dash" ? "6 4" : kind === "dot" ? "1.5 3" : "";
    if (styleShapes(function (p) { if (v) p.setAttribute("stroke-dasharray", v); else p.removeAttribute("stroke-dasharray"); })) toastMsg("선 종류: " + (kind === "solid" ? "실선" : kind === "dash" ? "파선" : "점선"));
  }
  function toggleShapeStyle() {
    var st = document.getElementById("shapetools"); if (!st || !sel) return;
    if (sel.getAttribute("data-obj") !== "shape") { toastMsg("도형에서만 스타일 편집"); return; }
    if (!st.classList.contains("hidden")) { st.classList.add("hidden"); return; }
    hideObjPops("shapetools");
    var r = objtools.getBoundingClientRect(); st.style.left = r.left + "px"; st.style.top = Math.min((r.bottom + 3), (window.innerHeight || 480) - 90) + "px"; st.classList.remove("hidden");
  }
  // 이미지도 도형과 동일 .wp-obj 블록 개체(회전/반전/배치/묶기/맞춤/복사/자르기 통일).
  //   자르기 모델(content+window): 래퍼=보이는 창(overflow hidden), 내부 img=콘텐츠(절대배치·data-cw/ch 픽셀).
  //   data-crop="cT cR cB cL"(0~0.9 분수) → img 오프셋+래퍼 축소. 전부 블록 속성 → block-diff undo 자동.
  function applyImgCrop(d) {
    var cw = parseFloat(d.getAttribute("data-cw")) || 0, ch = parseFloat(d.getAttribute("data-ch")) || 0;
    if (!cw || !ch) return;
    var cr = (d.getAttribute("data-crop") || "0 0 0 0").split(" ").map(parseFloat);
    var cT = cr[0] || 0, cR = cr[1] || 0, cB = cr[2] || 0, cL = cr[3] || 0;
    var img = d.querySelector("img"); if (!img) return;
    img.style.width = cw + "px"; img.style.height = ch + "px";
    img.style.left = (-cL * cw) + "px"; img.style.top = (-cT * ch) + "px";
    d.style.width = Math.max(8, Math.round(cw * (1 - cL - cR))) + "px";
    d.style.height = Math.max(8, Math.round(ch * (1 - cT - cB))) + "px";
  }
  function insertObjInline(el) {   // 개체를 커서 위치(문단 텍스트 안)에 인라인 삽입 → 진짜 글자처럼(앞뒤 글자 같은 줄). offsetParent=#page라 이후 어울림/고정도 #page 기준.
    var s = window.getSelection && getSelection();
    var r = (s && s.rangeCount) ? s.getRangeAt(0) : null;
    if (!r || !page.contains(r.startContainer)) { r = document.createRange(); r.selectNodeContents(page.lastElementChild || page); r.collapse(false); }
    r.collapse(false); r.insertNode(el);
    try { r.setStartAfter(el); r.collapse(true); s.removeAllRanges(); s.addRange(r); } catch (e) {}
    if (WP.normalizeEdges) WP.normalizeEdges();
    markDirty();   // 명시 histRecord 안 함 → 관찰자 debounce가 삽입+img.onload 크롭변경을 한 undo 체크포인트로 묶음(undo 1회에 제거)
  }
  function insertImageObj(src) {
    if (!src) return null;
    var d = document.createElement("span");
    d.className = "wp-obj"; d.setAttribute("data-obj", "img"); d.setAttribute("data-oid", nextOid());
    d.setAttribute("contenteditable", "false"); d.style.width = "300px"; d.style.height = "200px";   // ⚠️position/overflow는 CSS(.wp-obj)가 담당(2026-07-19) — 인라인 relative가 고정(absolute) CSS를 이겨 배치 전체가 깨지던 원흉
    var img = document.createElement("img"); img.alt = ""; img.style.position = "absolute"; img.style.left = "0"; img.style.top = "0"; img.style.display = "block";
    img.onload = function () {
      var pw = (page.clientWidth || 600) - 40, cw = Math.min(img.naturalWidth || 300, pw);
      var ch = cw * ((img.naturalHeight || 200) / (img.naturalWidth || 300));
      d.setAttribute("data-cw", Math.round(cw)); d.setAttribute("data-ch", Math.round(ch)); d.setAttribute("data-crop", "0 0 0 0");
      applyImgCrop(d); if (sel === d) place();
    };
    img.src = src; d.appendChild(img);
    insertObjInline(d);
    select(d);
    toastMsg("그림 삽입 — 핸들 크기 · [배치] 자유배치 · [맞춤] 정렬 · [✂] 자르기");
    return d;
  }
  // ── 문서 내 미디어(영상·소리) 개체 — <video>/<audio>를 .wp-obj로 감싸 개체 시스템 통일. 컨트롤 클릭 허용(재생) ──
  function insertMediaObj(src, kind) {
    if (!src) return null;
    kind = (kind === "audio") ? "audio" : "video";
    var cell = null, s = window.getSelection();   // 캐럿이 표 셀 안이면 셀에 딱 맞게 고정 삽입
    if (s && s.rangeCount) { var an = s.anchorNode; an = (an && an.nodeType === 1) ? an : (an && an.parentNode); if (an && an.closest) { cell = an.closest("td,th"); if (cell && !page.contains(cell)) cell = null; } }
    var d = document.createElement("div");
    d.className = "wp-obj"; d.setAttribute("data-obj", "media"); d.setAttribute("data-media", kind);
    d.setAttribute("data-oid", nextOid()); d.setAttribute("contenteditable", "false");
    var m = document.createElement(kind); m.setAttribute("controls", ""); m.setAttribute("preload", "none");   // 재생 전엔 버퍼 안 함(메모리 최적화 — 문서 내 여러 미디어 대비)
    if (kind === "video") m.setAttribute("playsinline", "");
    m.style.width = "100%"; m.style.height = "100%"; m.style.display = "block"; m.src = src;
    d.appendChild(m);
    if (cell) {   // 표 셀: 폭 100%·비율 높이로 셀에 딱 맞게 고정(크기·이동 손잡이 없음)
      d.setAttribute("data-cellfit", "1"); d.style.width = "100%";
      if (kind === "audio") { d.style.height = "54px"; m.style.height = "100%"; } else { d.style.height = "auto"; m.style.height = "auto"; }
      cell.appendChild(d); if (WP.normalizeEdges) WP.normalizeEdges(); select(d); markDirty(); if (WP.histRecord) WP.histRecord();
      toastMsg((kind === "audio" ? "소리" : "영상") + " — 표 셀에 맞춰 고정 · 재생버튼으로 재생");
      return d;
    }
    if (kind === "video") { d.style.width = "360px"; d.style.height = "220px"; } else { d.style.width = "320px"; d.style.height = "54px"; }
    if (WP.insertBlocks) WP.insertBlocks([d]); else page.appendChild(d);
    if (WP.normalizeEdges) WP.normalizeEdges();
    select(d);
    toastMsg((kind === "audio" ? "소리" : "영상") + " 삽입 — 핸들로 크기 · [배치] 자유배치 · 재생버튼으로 재생");
    return d;
  }
  function cropEdge(edge, delta) {
    if (!sel || sel.getAttribute("data-obj") !== "img" || !sel.getAttribute("data-cw")) { toastMsg("이미지를 선택하세요"); return; }
    var cr = (sel.getAttribute("data-crop") || "0 0 0 0").split(" ").map(parseFloat);
    var idx = { t: 0, r: 1, b: 2, l: 3 }[edge], opp = { t: 2, r: 3, b: 0, l: 1 }[edge];
    var v = Math.max(0, Math.min(0.9 - (cr[opp] || 0), (cr[idx] || 0) + delta));
    cr[idx] = Math.round(v * 100) / 100;
    sel.setAttribute("data-crop", cr.join(" "));
    applyImgCrop(sel); place(); markDirty(); WP.histRecord && WP.histRecord();
  }
  function cropReset() {
    if (!sel || sel.getAttribute("data-obj") !== "img") { toastMsg("이미지를 선택하세요"); return; }
    sel.setAttribute("data-crop", "0 0 0 0"); applyImgCrop(sel); place(); markDirty(); WP.histRecord && WP.histRecord(); toastMsg("자르기 초기화");
  }
  function toggleCrop() {
    var ct = document.getElementById("croptools"); if (!ct || !sel) return;
    if (sel.getAttribute("data-obj") !== "img") { toastMsg("이미지에서만 자르기 가능"); return; }
    if (!ct.classList.contains("hidden")) { ct.classList.add("hidden"); return; }
    hideObjPops("croptools");
    var r = objtools.getBoundingClientRect(); ct.style.left = r.left + "px"; ct.style.top = (r.bottom + 3) + "px"; ct.classList.remove("hidden");
  }

  function objToPageBg() {   // 선택 그림 개체를 쪽 배경으로(문서=미디어). src를 wpmeta에 저장 → applyPageBg
    if (!window.WP.PAGEBG_IMG) { toastMsg("쪽 배경 그림은 준비 중입니다 (계획중)"); return; }   // 실행중지 — 배경색만 사용. 켜기=WP.PAGEBG_IMG=true
    var el = sel; if (!el) { toastMsg("그림 개체를 선택하세요"); return; }
    var img = el.tagName === "IMG" ? el : (el.getAttribute && el.getAttribute("data-obj") === "img" ? el.querySelector("img") : null);
    var src = img && img.getAttribute("src");
    if (!src) { toastMsg("그림 개체를 선택하세요"); return; }
    page.querySelectorAll("[data-pagebg-src]").forEach(function (o) { o.removeAttribute("data-pagebg-src"); });   // 이전 표식/라벨 정리(배경은 하나)
    page.querySelectorAll("p.pagebg-cap").forEach(function (p) { p.parentNode.removeChild(p); });
    if (WP.wpmetaEl) WP.wpmetaEl().setAttribute("data-pagebg-image", src);
    el.setAttribute("data-pagebg-src", "1");   // 이 그림이 쪽 배경원본임을 표식(저장·식별)
    var cap = document.createElement("p"); cap.className = "pagebg-cap"; cap.setAttribute("contenteditable", "false"); cap.textContent = "(페이지배경)";
    if (el.parentNode) el.parentNode.insertBefore(cap, el.nextSibling);
    if (WP.applyPageBg) WP.applyPageBg();
    markDirty(); toastMsg("선택 그림을 쪽 배경으로 설정 · '(페이지배경)' 표시 — 편집 용지에서 채움방식 조절");
  }
  WP.register({
    media: function () { if (WP.mediaPick) WP.mediaPick(); else toastMsg("미디어 피커 로드 실패"); },   // 문서/media 폴더 피커 → 재생 개체 삽입
    objToBg: objToPageBg,
    caption: caption,
    captionRenum: function () { capRenumber(); markDirty(); toastMsg("캡션 번호 갱신됨"); },
    fnInsert: fnInsert,
    fnRenum: function () { var n = fnRenum(); markDirty(); toastMsg("미주 번호 갱신됨 — " + n + "개"); },
    shapeRect: function () { insertShape("rect"); },
    shapeCircle: function () { insertShape("circle"); },
    shapeLine: function () { insertShape("line"); },
    shapeBox: function () { toggleShapeBox(); }
  });
  // ── 도형▾ 토글: 개체 미선택이어도 도형/개체 도구상자(#objtools)를 열어 도형 그리기 아이콘 노출 ──
  function toggleShapeBox() {
    if (!objtools) return;
    if (objtools.classList.contains("hidden")) {
      var tb = document.getElementById("toolbar");
      objtools.style.top = ((tb ? tb.getBoundingClientRect().bottom : 28) + 1) + "px";
      objtools.classList.remove("hidden"); updateObjToolState();
    } else { objtools.classList.add("hidden"); hideObjPops(); }
  }

  WP.fnRenumNow = fnRenum;   // 재생성용(토스트·markDirty 없이) — regenPageData가 호출
  WP.normalizeObjStyles = function () {   // 구버전 그림 래퍼의 인라인 position/overflow 잔재 제거(로드 훅, 2026-07-19 — 인라인 relative가 고정 CSS를 이김)
    Array.prototype.forEach.call(page.querySelectorAll(".wp-obj"), function (el) {
      if (el.style.position) el.style.position = "";
      if (el.style.overflow) el.style.overflow = "";
    });
  };
  WP.selectObj = select; WP.objDeselect = deselect; WP.objPlace = cyclePlace; WP.objDelete = delImg; WP.insertShape = insertShape; WP.insertImageObj = insertImageObj; WP.insertMediaObj = insertMediaObj;
  WP.objRotate = rotate; WP.objFlip = flip; WP.objToggleSel = toggleSel; WP.objGroup = group; WP.objUngroup = ungroup;
  WP.objAlign = align; WP.objDistribute = distribute; WP.objSameSize = sameSize;
  WP.objCrop = cropEdge; WP.objCropReset = cropReset;
  WP.shapeFill = shapeFill; WP.shapeStroke = shapeStroke; WP.shapeWidth = shapeWidth; WP.shapeDash = shapeDash;
})();
