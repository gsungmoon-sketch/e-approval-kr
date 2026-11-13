// 한워드 — 전체 화면 = 읽기전용 인쇄 미리보기.
//   #page(편집 호스트)는 건드리지 않고 cleanedHTML()을 읽기전용 클론 #pvpage에 렌더.
//   편집기와 동일한 경계 기하(pageH = contentW × WP.paperRatio())로 쪽을 나누고,
//   쪽 사이에 실제 여백 띠(꼬리말/쪽번호 + 회색 간격 + 머릿말)를 삽입해 종이가 쌓인 모양으로 표시.
//   복귀: Enter · 더블클릭 · Esc (모두 exitPreview로 수렴, 멱등).
(function () {
  "use strict";
  var WP = window.WP, page = WP.page;
  var GAP = 20;                 // 종이 사이 회색 간격(px)
  var LH = 13;                  // 라벨 대략 줄높이(9px×1.4)
  var curPad = { padT: 0, padB: 0 };

  // #wpmeta 머릿말/꼬리말/쪽번호(원본 #page 기준). WP.readMeta 있으면 재사용.
  function readMeta() {
    if (WP.readMeta) return WP.readMeta();
    var el = page.querySelector("#wpmeta");
    return {
      header: el ? el.getAttribute("data-header") || "" : "",
      footer: el ? el.getAttribute("data-footer") || "" : "",
      pagenum: el ? el.getAttribute("data-pagenum") || "" : ""
    };
  }

  function ensureOverlay() {
    if (document.getElementById("preview")) return;
    var ov = document.createElement("div"); ov.id = "preview"; ov.className = "hidden"; ov.setAttribute("tabindex", "-1");
    var pv = document.createElement("div"); pv.id = "pvpage";
    ov.appendChild(pv); document.body.appendChild(ov);
    ov.addEventListener("dblclick", function () { exitPreview(); });   // 더블클릭 복귀
  }

  // #page의 실측 스타일(용지 폭·여백·글꼴)을 클론에 인라인 복사 → 용지/방향/여백 자동 반영
  function copyPageStyle(host) {
    var cs = getComputedStyle(page);
    host.style.width = cs.width;
    host.style.paddingTop = cs.paddingTop; host.style.paddingRight = cs.paddingRight;
    host.style.paddingBottom = cs.paddingBottom; host.style.paddingLeft = cs.paddingLeft;
    host.style.fontSize = cs.fontSize; host.style.lineHeight = cs.lineHeight;
    host.style.fontFamily = cs.fontFamily; host.style.color = cs.color;
    host.style.boxSizing = cs.boxSizing;
    host.style.setProperty("--ml", cs.paddingLeft);   // 회색 간격 좌우 블리드용
    host.style.setProperty("--mr", cs.paddingRight);
  }

  function makeLabel(text, align) {
    var d = document.createElement("div");
    d.className = "pvhf " + (align === "right" ? "right" : align === "left" ? "left" : "center");
    d.textContent = text;
    return d;
  }
  // #21 — 물리 쪽번호 → 표시번호(시작번호 오프셋)·표기(1/①/i/가)·홀짝 머리말. wp-io의 공유 유틸 사용.
  function dispNum(meta, num) { return (meta.pagenumStart || 1) + num - 1; }
  function pageHeader(meta, num) {
    var t = WP.headerFor ? WP.headerFor(meta, dispNum(meta, num)) : meta.header;
    return t || "";
  }
  // 꼬리말 = 꼬리말 텍스트 + 쪽번호. pagenum='' 이면 번호 생략(htmlpdf 규칙 일치). 정렬 = pagenum.
  function footerLabel(meta, num) {
    var align = meta.pagenum || "center";
    var txt = meta.footer || "";
    if (meta.pagenum) {
      var n = WP.fmtPageNum ? WP.fmtPageNum(dispNum(meta, num), meta.pagenumFormat) : String(num);
      txt = (txt ? txt + " " : "") + n;
    }
    if (!txt) return null;
    return makeLabel(txt, align);
  }
  // 여백 띠: [흰 fill] + [끝쪽 꼬리말/쪽번호 padB] + [회색 간격 GAP] + [다음쪽 머릿말 padT]
  function makeBand(fillH, endPageNum, meta) {
    var padT = curPad.padT, padB = curPad.padB;
    var band = document.createElement("div");
    band.className = "pvband"; band.setAttribute("contenteditable", "false");
    band.style.height = (fillH + padB + GAP + padT) + "px";
    var gap = document.createElement("div"); gap.className = "gap";
    gap.style.top = (fillH + padB) + "px"; gap.style.height = GAP + "px";
    band.appendChild(gap);
    var f = footerLabel(meta, endPageNum);
    if (f) { f.style.top = (fillH + Math.max(2, (padB - LH) / 2)) + "px"; band.appendChild(f); }
    var hTxt = pageHeader(meta, endPageNum + 1);   // 띠 아래는 다음 쪽 → 그 쪽 홀짝 머리말
    if (hTxt) {
      var hd = makeLabel(hTxt, "center");
      hd.style.top = (fillH + padB + GAP + Math.max(2, (padT - LH) / 2)) + "px"; band.appendChild(hd);
    }
    return band;
  }

  // 그리디 배치: used+h가 한 쪽을 넘으면 앞에 띠 삽입. 초대형 블록(>pageH)은 그대로(편집기 정책).
  function paginatePreview(host, meta) {
    var cs = getComputedStyle(host);
    var padL = parseFloat(cs.paddingLeft) || 0, padR = parseFloat(cs.paddingRight) || 0,
        padT = parseFloat(cs.paddingTop) || 0, padB = parseFloat(cs.paddingBottom) || 0;
    curPad = { padT: padT, padB: padB };
    var contentW = host.clientWidth - padL - padR;
    var pageH = contentW * (WP.paperRatio ? WP.paperRatio() : 257 / 170);
    if (pageH < 60) return;
    var blocks = Array.prototype.filter.call(host.children, function (b) {
      return b.nodeType === 1 && b.id !== "wpmeta" && !b.classList.contains("pvband") && !b.classList.contains("pvhf");
    });
    // 강제 분할(주인 지시): 편집기가 결정한 쪽 시작(WP.pageStartIdx)을 그대로 따라 시작을 정규화 — 재계산 없이 그 지점에서만 분할.
    // (각 경로가 독립 재계산하면 margin-collapse·타일링 drift로 어긋났음. 이제 편집기가 단일 권위.)
    var starts = window.WP.pageStartIdx || [];
    var startSet = {};
    for (var si = 0; si < starts.length; si++) startSet[starts[si]] = 1;
    var tops = blocks.map(function (b) { return b.offsetTop - padT; });   // 자연 top(마진 포함=정확한 채움 계산)
    var pageNum = 1, pageTop = 0, i;
    for (i = 0; i < blocks.length; i++) {
      if (startSet[i]) {   // 편집기가 정한 쪽 시작 → 남은 쪽 채워 다음 쪽 top으로 밀기
        var used = tops[i] - pageTop;
        host.insertBefore(makeBand(Math.max(0, pageH - used), pageNum, meta), blocks[i]);
        pageNum++; pageTop = tops[i];
      }
    }
    // 마지막 쪽을 온전한 한 장으로 채움(내용이 짧아도 종이 전체가 보이도록). offsetHeight는 띠 삽입에 불변.
    if (blocks.length) {
      var lb = blocks[blocks.length - 1];
      var lastFill = pageH - ((tops[blocks.length - 1] + lb.offsetHeight) - pageTop);
      if (lastFill > 1) {
        var filler = document.createElement("div");
        filler.className = "pvfill"; filler.setAttribute("contenteditable", "false");
        filler.style.cssText = "height:" + Math.round(lastFill) + "px;margin:0;padding:0;border:0;";
        host.appendChild(filler);
      }
    }
    var h1Txt = pageHeader(meta, 1);   // 1쪽 머릿말(상단 여백) — 홀짝이면 1쪽 표시번호 기준
    if (h1Txt) {
      var h1 = makeLabel(h1Txt, "center");
      h1.style.top = Math.max(2, (padT - LH) / 2) + "px"; host.appendChild(h1);
    }
    var lf = footerLabel(meta, pageNum);   // 마지막 쪽 꼬리말/쪽번호(하단 여백)
    if (lf) { lf.style.bottom = Math.max(2, (padB - LH) / 2) + "px"; host.appendChild(lf); }
  }

  // #26 쪽 테두리 — 쪽마다 본문 영역 사각형. 쪽 시작 y = 1쪽:padT, 이후:각 띠의 바닥(다음 쪽 본문 시작)
  function pvBorders(host, meta) {
    if (!meta.pageBorder) return;   // readMeta가 선종류 화이트리스트 보증
    var cs = getComputedStyle(host);
    var padL = parseFloat(cs.paddingLeft) || 0, padR = parseFloat(cs.paddingRight) || 0,
        padT = parseFloat(cs.paddingTop) || 0, padB = parseFloat(cs.paddingBottom) || 0;
    var contentW = host.clientWidth - padL - padR;
    var pageH = contentW * (WP.paperRatio ? WP.paperRatio() : 257 / 170);
    var tops = [padT];
    Array.prototype.forEach.call(host.querySelectorAll(".pvband"), function (b) { tops.push(b.offsetTop + b.offsetHeight); });
    tops.forEach(function (t, i) {
      var h = (i === tops.length - 1) ? Math.min(pageH, host.clientHeight - padB - t) : pageH;   // 마지막 쪽=종이 끝까지만
      if (h < 8) return;
      var d = document.createElement("div"); d.className = "pvborder";
      d.style.cssText = "left:" + Math.round(padL) + "px;top:" + Math.round(t) + "px;width:" + Math.round(contentW) +
                        "px;height:" + Math.round(h) + "px;border:" + meta.pageBorderW + "px " + meta.pageBorder + " #445;";
      host.appendChild(d);
    });
  }

  function enterPreview() {
    if (WP.previewActive) return;
    if (WP.saveRange) WP.saveRange();
    ensureOverlay();
    var ov = document.getElementById("preview"), host = document.getElementById("pvpage");
    try {
      document.body.classList.add("zen");
      page.setAttribute("contenteditable", "false");   // 본문 편집 잠금
      host.innerHTML = WP.cleanedHTML ? WP.cleanedHTML() : page.innerHTML;
      copyPageStyle(host);
      ov.classList.remove("hidden");                    // 측정 위해 먼저 표시
      var meta = readMeta();
      paginatePreview(host, meta);
      pvBorders(host, meta);                            // #26 쪽 테두리(띠 배치 후 실측 기반)
      WP.previewActive = true; ov.focus();
      if (WP.toast) WP.toast("인쇄 미리보기 — Enter·Esc·더블클릭으로 닫기");
    } catch (e) { exitPreview(); }
  }
  function exitPreview() {   // 멱등
    var ov = document.getElementById("preview"), host = document.getElementById("pvpage");
    if (ov) ov.classList.add("hidden");
    if (host) host.innerHTML = "";
    WP.previewActive = false;
    document.body.classList.remove("zen");
    page.setAttribute("contenteditable", "true");
    if (WP.restoreRange) WP.restoreRange();
  }

  WP.enterPreview = enterPreview; WP.exitPreview = exitPreview; WP.previewActive = false;
  WP.register({ fullscreen: function () { WP.previewActive ? exitPreview() : enterPreview(); } });
}());
