// 한워드 — 로컬 래스터 PDF·인쇄(프린터) (#28, docs/print-spec.md)
//   기기(cog)엔 Chromium/Playwright가 없어 클라에서 html2canvas(벤더링, /html2canvas.min.js)로
//   쪽별 캔버스를 캡처(scale≈2.6, 200dpi급) → PNG dataURL 배열을 서버에 보내 magick으로 PDF 조립.
//   쪽 분해: 종이 크기 고정 div 안에 cleanedHTML 클론을 -k·pageH 만큼 올려 overflow 클립 =
//   편집기(wp-page.js paginate)와 동일 그리디 채움 후 쪽별 이산 캡처(초대형 캔버스 상한 회피).
//   html2canvas onclone에서 클론 문서의 #page id를 우리 클론으로 바꿔치기 → editor.css의
//   #page 스코프 규칙(표/목록/그림 배치/링크 색)이 수정 없이 전부 적용된다.
//   WP_PUBLIC(원격 서비스)은 기존 Playwright 다운로드(wp-public.js dlPdf) 유지 — 이 모듈 미등록.
(function () {
  "use strict";
  // 공개모드: rasterPages(PDF 래스터)는 살리고, 네트워크 인쇄(printer)만 아래에서 제외(방식 A)
  var WP = window.WP, page = WP.page, api = WP.api, status = WP.status,
      toastMsg = WP.toast, docname = WP.docname;
  var LH = 13;                    // 미리보기와 동일한 라벨 줄높이(9px×1.4)
  var MAX_PAGES = 300;            // 폭주 가드(스페이서 오류 등으로 쪽수 발산 시 중단)

  // ── html2canvas 지연 로드(같은 출처 정적 파일 → CSP script-src 'self' 통과) ──
  var libLoading = null;
  function ensureLib() {
    if (window.html2canvas) return Promise.resolve();
    if (libLoading) return libLoading;
    libLoading = new Promise(function (res, rej) {
      var s = document.createElement("script");
      s.src = "html2canvas.min.js";   // 상대경로(하위 경로 배포 /editor/ 대응)
      s.onload = function () { res(); };
      s.onerror = function () { libLoading = null; rej(new Error("html2canvas 로드 실패")); };
      document.head.appendChild(s);
    });
    return libLoading;
  }

  // ── 편집기 paginate와 동일한 그리디 채움(클론 전용, 항상 실행) ──
  function fillSpacers(inner, pageH, padT) {
    Array.prototype.forEach.call(inner.querySelectorAll("[data-pgfill]"), function (s) { if (s.parentNode) s.parentNode.removeChild(s); });
    Array.prototype.forEach.call(inner.querySelectorAll(".pgbreak"), function (b) { b.style.height = ""; });
    var blocks = Array.prototype.filter.call(inner.children, function (b) {
      return b.id !== "wpmeta" && b.nodeType === 1 && !b.hasAttribute("data-pgfill");
    });
    var meas = blocks.map(function (b) { return { b: b, top: b.offsetTop - padT, h: b.offsetHeight }; });
    var shift = 0;
    for (var i = 0; i < meas.length; i++) {
      var m = meas[i], b = m.b, top = m.top + shift, h = m.h;
      if (b.classList && b.classList.contains("pgbreak")) {
        var rem = top % pageH, fill = (rem < 1) ? 0 : (pageH - rem);
        if (fill > 1) { var fr = Math.round(fill); b.style.height = fr + "px"; shift += fr; }
        b.style.border = "0";   // 파선 가이드는 편집 화면 전용 — 인쇄물에서 숨김(htmlpdf.js와 동일 규칙)
        continue;
      }
      if (h > 1 && h <= pageH + 0.5) {
        var boundary = Math.ceil((top + 1) / pageH) * pageH;
        if (top < boundary && (top + h) > boundary + 1) {
          var sp = Math.round(boundary - top), s = document.createElement("div");
          s.setAttribute("data-pgfill", ""); s.style.cssText = "height:" + sp + "px;margin:0;padding:0;border:0;";
          b.parentNode.insertBefore(s, b); shift += sp;
        }
      }
    }
  }

  // ── 머리말/꼬리말/쪽번호 라벨(미리보기 .pvhf와 동일 기하, 스코프 밖이라 인라인 스타일) ──
  function label(text, align, css) {
    var d = document.createElement("div");
    d.style.cssText = "position:absolute;left:0;right:0;padding:0 6mm;font-size:9px;line-height:1.4;color:#555;pointer-events:none;text-align:" + (align || "center") + ";" + css;
    d.textContent = text;
    return d;
  }
  function dispNum(meta, num) { return (meta.pagenumStart || 1) + num - 1; }
  function footerText(meta, num) {
    var txt = meta.footer || "";
    if (meta.pagenum) {
      var n = WP.fmtPageNum ? WP.fmtPageNum(dispNum(meta, num), meta.pagenumFormat) : String(num);
      txt = (txt ? txt + " " : "") + n;
    }
    return txt;
  }

  // ── 쪽별 래스터 캡처 → PNG dataURL 배열 ──
  //   opt.onProgress(k, total) 진행 피드백. 반환 Promise<[dataURL…]>.
  function rasterPages(opt) {
    opt = opt || {};
    if (WP.previewActive && WP.exitPreview) WP.exitPreview();   // 미리보기 중이면 접고 시작(캡처 host 독점)
    return ensureLib().then(function () {
      var cs = getComputedStyle(page);
      var padL = parseFloat(cs.paddingLeft) || 0, padR = parseFloat(cs.paddingRight) || 0,
          padT = parseFloat(cs.paddingTop) || 0, padB = parseFloat(cs.paddingBottom) || 0;
      var contentW = page.clientWidth - padL - padR;
      var pageH = contentW * (WP.paperRatio ? WP.paperRatio() : 257 / 170);
      if (pageH < 60) return Promise.reject(new Error("용지 계산 실패"));
      // 종이 캔버스 = 실제 용지비(A4=210×297 등, WP.paperInfo mm) — 편집 CSS 패딩과 무관하게
      // 본문영역(contentW×pageH)을 설정 여백 위치에 정합 배치(px/mm 환산 = contentW / 본문폭mm)
      var pi = WP.paperInfo ? WP.paperInfo() : { w: 210, h: 297, ml: 20, mr: 20, mt: 20, mb: 20 };
      var pxmm = contentW / Math.max(1, pi.w - pi.ml - pi.mr);
      var paperW = Math.round(pi.w * pxmm), paperH = Math.round(pi.h * pxmm);
      var mL = pi.ml * pxmm, mT = pi.mt * pxmm, mB = pi.mb * pxmm;
      var scale = Math.min(3, Math.max(1.5, 2100 / paperW));    // A4폭 약 2100px=200dpi급

      // 종이 div(뷰포트 뒤 z-index — 음수 좌표는 html2canvas가 빈 캔버스를 그리므로 금지)
      var paper = document.createElement("div");
      paper.style.cssText = "position:fixed;left:0;top:0;width:" + paperW + "px;height:" + paperH +
                            "px;overflow:hidden;background:#fff;z-index:-2147483000;";
      var inner = document.createElement("div");
      inner.setAttribute("data-printinner", "");
      // 중복 id지만 의도적: CSS #page 스코프 규칙(표/목록/서식)이 측정·캡처 양쪽에 동일 적용돼야
      // 쪽 경계 계산이 실제 렌더와 일치한다. getElementById('page')는 문서순 첫 요소(진짜 편집기)를 유지.
      inner.id = "page";
      inner.style.cssText = "position:absolute;left:0;top:0;box-sizing:" + cs.boxSizing + ";width:" + cs.width +
                            ";padding:" + cs.padding + ";margin:0;min-height:0;box-shadow:none;background:#fff;" +
                            "font-size:" + cs.fontSize + ";line-height:" + cs.lineHeight + ";font-family:" + cs.fontFamily +
                            ";color:" + cs.color + ";position:absolute;";
      inner.innerHTML = WP.cleanedHTML ? WP.cleanedHTML() : page.innerHTML;
      paper.appendChild(inner); document.body.appendChild(paper);

      var meta = WP.readMeta ? WP.readMeta() : {};
      var cleanup = function () { if (paper.parentNode) paper.parentNode.removeChild(paper); };
      try { fillSpacers(inner, pageH, padT); } catch (e) { cleanup(); return Promise.reject(e); }
      var contentH = inner.scrollHeight - padT - padB;
      var total = Math.max(1, Math.ceil((contentH - 6) / pageH));
      if (total > MAX_PAGES) { cleanup(); return Promise.reject(new Error("쪽수가 너무 많습니다(" + total + "쪽)")); }

      var out = [];
      function mask(css) {   // 여백 흰 마스크: 연속 흐름(이웃 쪽 꼬리·초대형 블록 삐침)이 여백에 비치지 않게
        var d = document.createElement("div"); d.setAttribute("data-printhf", "");
        d.style.cssText = "position:absolute;background:#fff;z-index:3;pointer-events:none;" + css;
        paper.appendChild(d);
      }
      function decor(k) {   // 쪽별 장식: 여백 마스크 + 라벨 + 쪽 테두리(캡처마다 재구성)
        Array.prototype.forEach.call(paper.querySelectorAll("[data-printhf]"), function (n) { n.remove(); });
        mask("left:0;top:0;width:100%;height:" + Math.round(mT) + "px;");
        mask("left:0;bottom:0;width:100%;height:" + Math.round(paperH - mT - pageH) + "px;");
        mask("left:0;top:0;width:" + Math.round(mL) + "px;height:100%;");
        mask("right:0;top:0;width:" + Math.round(paperW - mL - contentW) + "px;height:100%;");
        var num = k + 1;
        var hTxt = WP.headerFor ? (WP.headerFor(meta, dispNum(meta, num)) || "") : (meta.header || "");
        if (hTxt) { var h = label(hTxt, "center", "top:" + Math.max(2, (mT - LH) / 2) + "px;z-index:5;"); h.setAttribute("data-printhf", ""); paper.appendChild(h); }
        var fTxt = footerText(meta, num);
        if (fTxt) { var f = label(fTxt, meta.pagenum || "center", "bottom:" + Math.max(2, (mB - LH) / 2) + "px;z-index:5;"); f.setAttribute("data-printhf", ""); paper.appendChild(f); }
        if (meta.pageBorder) {
          var bd = document.createElement("div"); bd.setAttribute("data-printhf", "");
          bd.style.cssText = "position:absolute;z-index:5;box-sizing:border-box;pointer-events:none;left:" + Math.round(mL) +
                             "px;top:" + Math.round(mT) + "px;width:" + Math.round(contentW) + "px;height:" + Math.round(pageH) +
                             "px;border:" + meta.pageBorderW + "px " + meta.pageBorder + " #445;";
          paper.appendChild(bd);
        }
      }
      function snap(k) {
        inner.style.left = Math.round(mL - padL) + "px";
        inner.style.top = Math.round(mT - padT - k * pageH) + "px";
        decor(k);
        if (opt.onProgress) opt.onProgress(k + 1, total);
        return window.html2canvas(paper, { scale: scale, backgroundColor: "#ffffff", logging: false, useCORS: true, imageTimeout: 15000 })
          .then(function (canvas) {
            try { out.push(canvas.toDataURL("image/png")); }
            catch (e) { throw new Error("외부(cross-origin) 이미지가 있어 PDF·인쇄로 만들 수 없습니다 — 해당 이미지를 지우거나 문서에 직접 붙여 넣으세요"); }
          });
      }
      // Safari/WebKit는 inline SVG를 html2canvas가 그리면 캔버스가 오염(tainted)→toDataURL이 cross-origin 거부.
      // → 캡처 전 각 SVG를 같은출처 PNG(data URL)로 미리 래스터화해 교체(오염 원천 제거). 실패 시 원본 유지.
      function svgToPng(root) {
        var svgs = Array.prototype.slice.call(root.querySelectorAll("svg"));
        return Promise.all(svgs.map(function (svg) {
          return new Promise(function (resolve) {
            var r = svg.getBoundingClientRect();
            var w = Math.max(1, Math.round(r.width || svg.clientWidth || 1)), h = Math.max(1, Math.round(r.height || svg.clientHeight || 1));
            try {
              var clone = svg.cloneNode(true);
              clone.setAttribute("width", w); clone.setAttribute("height", h);
              if (!clone.getAttribute("xmlns")) clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
              var url = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(new XMLSerializer().serializeToString(clone));
              var im = new Image();
              im.onload = function () {
                try {
                  var c = document.createElement("canvas"); c.width = Math.max(1, w * scale); c.height = Math.max(1, h * scale);
                  c.getContext("2d").drawImage(im, 0, 0, c.width, c.height);
                  var rep = document.createElement("img");
                  rep.setAttribute("style", svg.getAttribute("style") || "");
                  rep.style.width = w + "px"; rep.style.height = h + "px";
                  rep.src = c.toDataURL("image/png");
                  if (svg.parentNode) svg.parentNode.replaceChild(rep, svg);
                } catch (e) {}
                resolve();
              };
              im.onerror = function () { resolve(); };
              im.src = url;
            } catch (e) { resolve(); }
          });
        }));
      }
      var chain = svgToPng(inner);   // SVG→PNG 선변환(오염 방지) 후 쪽 캡처
      for (var k = 0; k < total; k++) (function (kk) { chain = chain.then(function () { return snap(kk); }); }(k));
      return chain.then(function () { cleanup(); return out; })
                  .catch(function (e) { cleanup(); throw e; });
    });
  }

  function curName() { return (docname.value || "제목 없음").trim() || "제목 없음"; }
  function progress(verb) {
    return function (k, total) { status(verb + " 이미지 생성 중… " + k + "/" + total + "쪽"); };
  }

  // ── 인쇄(프린터): 래스터 → 서버 PDF 조립·print/ 저장 → prn 실행 ──
  function printer() {
    status("인쇄 준비 중…"); toastMsg("인쇄 준비 중…");
    rasterPages({ onProgress: progress("인쇄") }).then(function (pages) {
      status("인쇄 전송 중…");
      return api("/api/print", { name: curName(), pages: pages });
    }).then(function (r) {
      if (r && r.ok) { status("인쇄 시작됨"); toastMsg("인쇄 시작: " + (r.msg || r.file || "")); }
      else { status(""); toastMsg("인쇄 실패: " + ((r && r.err) || "")); }
    }).catch(function (e) { status(""); toastMsg("인쇄 실패 — " + (e && e.message ? e.message : "오류")); });
  }
  function printCancel() {
    status("인쇄 취소 중…");
    api("/api/print-cancel", {}).then(function (r) {
      if (r && r.ok) { status("인쇄 취소됨"); toastMsg("인쇄 취소: " + (r.msg || "완료")); }
      else { status(""); toastMsg("인쇄 취소 실패: " + ((r && r.err) || "")); }
    }).catch(function (e) { status(""); toastMsg("인쇄 취소 실패 — " + (e && e.message ? e.message : "오류")); });
  }

  WP.rasterPages = rasterPages;   // wp-io.js pdf()가 재사용(PDF 저장도 동일 래스터 경로)
  WP.printProgress = progress;
  if (!window.WP_PUBLIC) WP.register({ printer: printer, printCancel: printCancel });   // 인쇄 액션은 로컬 전용
}());
