// 한워드 — 임베드 모드(?embed=1, 2026-11-13). 같은 출처 부모 페이지(전자결재 등)가 iframe으로 띄우는 단일 문서 편집기.
//   프로토콜(docs/embed.md):
//     부모→편집기 {type:'load', html, readOnly, page}   page = "gov" | {preset, paper, orient, mt, mr, mb, ml}
//     부모→편집기 {type:'get'}                         → 편집기→부모 {type:'html', html, page}
//     편집기→부모 {type:'dirty'}  load/get 이후 첫 변경 시 1회
//     편집기→부모 {type:'ready'}  시작 시 1회
//   보안: 수신은 event.origin === location.origin && event.source === window.parent 일 때만, 송신 targetOrigin = location.origin('*' 금지).
//   불러온 HTML은 허용목록 정제(sanitizeEmbed) — 편집기가 만드는 요소·속성만, data-* 는 data-field·data-row·data-sum 만.
(function () {
  "use strict";
  var WP = window.WP;
  if (!WP || !WP.embed) return;
  var page = WP.page, ORIGIN = location.origin;
  var MAX_HTML = 32 * 1024 * 1024;   // 32MB(그림 data URI 포함) 초과 load는 거부
  function has(o, k) { return Object.prototype.hasOwnProperty.call(o, k); }   // 허용목록 조회 = 자기 속성만(constructor·__proto__ 우회 차단)

  // ───────────── 허용목록 정제 ─────────────
  var TAGS = {};
  ("p div span br hr b strong i em u s strike del sub sup h1 h2 h3 h4 h5 h6 ul ol li " +
   "table thead tbody tfoot tr td th caption colgroup col a img blockquote pre code font").split(" ").forEach(function (t) { TAGS[t] = 1; });
  // 내용째 버림(나머지 미허용 요소는 껍데기만 벗기고 자식은 정제해 유지)
  var DROP = {};
  ("script style template iframe frame frameset object embed applet noscript noembed noframes svg math head title meta link base " +
   "form input button select option optgroup textarea datalist output video audio source track canvas map area xmp plaintext listing " +
   "dialog slot portal marquee").split(" ").forEach(function (t) { DROP[t] = 1; });
  var COMMON = { style: 1, "class": 1, title: 1, align: 1 };
  var PER_TAG = {
    td: { colspan: 1, rowspan: 1, valign: 1, width: 1, height: 1 }, th: { colspan: 1, rowspan: 1, valign: 1, width: 1, height: 1 },
    tr: { valign: 1, height: 1 }, table: { border: 1, cellpadding: 1, cellspacing: 1, width: 1 },
    col: { span: 1, width: 1 }, colgroup: { span: 1, width: 1 },
    img: { src: 1, alt: 1, width: 1, height: 1 }, a: { href: 1 },
    font: { face: 1, size: 1, color: 1 }, ol: { start: 1, type: 1 }, li: { value: 1 }
  };
  var DATA_OK = { "data-field": 1, "data-row": 1, "data-sum": 1 };              // 계약: 이 3개만
  var DATA_TAGS = { td: 1, th: 1, tr: 1, table: 1, span: 1 };                    // 표 칸·줄·표·글자 묶음에만
  var DATA_VAL = /^[A-Za-z0-9_.\-\u3131-\u318E\uAC00-\uD7A3]{1,64}(,[A-Za-z0-9_.\-\u3131-\u318E\uAC00-\uD7A3]{1,64}){0,15}$/;   // 이름 또는 쉼표 이름 목록만(괄호·공백·따옴표·콜론 없음 — 식 아님, 부모도 eval 금지). hanword_embed.DATA_VAL 과 동일
  var CLASS_OK = { pgbreak: 1, objline: 1, "wp-hr": 1, caption: 1 };
  var NUM = /^\d{1,4}(\.\d{1,2})?(%|px)?$/;
  var CSS_PROP = /^(color|background-color|font|font-(family|size|weight|style|variant)|text-(align|indent|decoration|decoration-line|decoration-style|decoration-color|transform)|line-height|letter-spacing|word-spacing|vertical-align|white-space|word-break|margin(-(top|right|bottom|left))?|padding(-(top|right|bottom|left))?|border(-(top|right|bottom|left))?(-(width|style|color))?|border-collapse|border-spacing|width|height|min-width|max-width|min-height|max-height|table-layout|list-style-type|page-break-(before|after|inside)|break-(before|after|inside))$/;
  var CSS_BAD = /url\s*\(|expression|javascript:|vbscript:|@|\\|<|>|\/\*|\*\/|image-set|var\s*\(|attr\s*\(/i;

  function cleanStyle(v) {
    var out = [];
    String(v).split(";").forEach(function (decl) {
      var i = decl.indexOf(":"); if (i < 1) return;
      var k = decl.slice(0, i).trim().toLowerCase(), val = decl.slice(i + 1).trim();
      if (!CSS_PROP.test(k) || !val || val.length > 200 || CSS_BAD.test(val)) return;
      if (/[\u0000-\u001f\u007f]/.test(val)) return;
      out.push(k + ":" + val);
    });
    return out.join(";");
  }
  function cleanUrl(tag, v) {
    var u = String(v).replace(/[\u0000- \u007f-\u009f]+/g, "");
    if (tag === "img") return /^data:image\/(png|jpe?g|gif|webp|bmp);base64,[A-Za-z0-9+\/=]+$/i.test(u) ? u : null;   // 그림=내장 data URI만(외부·같은 출처 URL 요청 금지)
    return /^(https?:\/\/|mailto:|#)/i.test(u) ? u : null;                                                           // 링크=http(s)·mailto·문서 안 앵커
  }
  function cleanAttr(tag, name, v) {
    if (name === "style") { var cs = cleanStyle(v); return cs || null; }
    if (name === "class") {
      var cl = String(v).split(/\s+/).filter(function (c) { return has(CLASS_OK, c); });
      return cl.length ? cl.join(" ") : null;
    }
    if (name === "href" || name === "src") return cleanUrl(tag, v);
    if (name === "colspan" || name === "rowspan" || name === "span" || name === "start" || name === "value" || name === "border" ||
        name === "cellpadding" || name === "cellspacing" || name === "size")
      return /^\d{1,4}$/.test(v) ? v : null;
    if (name === "width" || name === "height") return NUM.test(v) ? v : null;
    if (name === "align" || name === "valign") return /^(left|right|center|justify|top|middle|bottom|baseline)$/i.test(v) ? v : null;
    if (name === "type") return /^[1aAiI]$/.test(v) ? v : null;
    if (name === "color") return /^(#[0-9a-f]{3,8}|[a-z]{3,20})$/i.test(v) ? v : null;
    if (name === "face") return /^[^<>"'`;\\]{1,80}$/.test(v) ? v : null;
    if (name === "title" || name === "alt") return String(v).slice(0, 300);
    return null;
  }
  // 편집기 자체 문서메타(#wpmeta)의 용지 설정만 엄격 검증해 회수(요소 자체는 버리고 setPaper로 재생성)
  var PAPERS = { A3: 1, A4: 1, A5: 1, A6: 1, B4: 1, B5: 1, B6: 1, Letter: 1, Legal: 1, Tabloid: 1 };
  function metaPage(el) {
    var c = {}, p = el.getAttribute("data-paper") || "", o = el.getAttribute("data-orient") || "", mg = el.getAttribute("data-margins") || "";
    if (Object.prototype.hasOwnProperty.call(PAPERS, p)) c.paper = p;
    if (o === "landscape") c.orient = o;
    if (/^\d{1,3}(\.\d)?(,\d{1,3}(\.\d)?){3}$/.test(mg)) {
      var a = mg.split(",").map(Number);
      if (a.every(function (x) { return x >= 0 && x <= 100; })) { c.mt = a[0]; c.mr = a[1]; c.mb = a[2]; c.ml = a[3]; }
    }
    return c;
  }
  // html 문자열 → {frag(현재 문서 노드), page(회수한 용지 설정|null)}. 파싱은 비활성 문서(DOMParser — 스크립트·그림 로드 없음),
  //   출력은 허용 노드만 새로 만들어 복사(직렬화→재파싱 없음 = mXSS 여지 없음).
  function sanitizeEmbed(html) {
    var src = new DOMParser().parseFromString("<!DOCTYPE html><html><body>" + String(html || "") + "</body></html>", "text/html");
    var frag = document.createDocumentFragment(), meta = null, depth = 0;
    function copy(from, to) {
      if (++depth > 200) { depth--; return; }   // 과도한 중첩(스택 고갈) 방지
      for (var n = from.firstChild; n; n = n.nextSibling) {
        if (n.nodeType === 3) { to.appendChild(document.createTextNode(n.nodeValue)); continue; }
        if (n.nodeType !== 1) continue;                      // 주석·처리명령 등 버림
        var tag = n.localName;
        if (n.namespaceURI !== "http://www.w3.org/1999/xhtml") continue;
        if (n.id === "wpmeta") { if (!meta) meta = metaPage(n); continue; }
        if (has(DROP, tag)) continue;
        if (!has(TAGS, tag)) { copy(n, to); continue; }           // 미허용 요소: 껍데기만 벗김
        var el = document.createElement(tag), pt = has(PER_TAG, tag) ? PER_TAG[tag] : {};
        for (var i = 0; i < n.attributes.length; i++) {
          var a = n.attributes[i], name = a.name.toLowerCase(), v;
          if (has(DATA_OK, name)) { if (has(DATA_TAGS, tag) && DATA_VAL.test(a.value)) el.setAttribute(name, a.value); continue; }
          if (!has(COMMON, name) && !has(pt, name)) continue;
          v = cleanAttr(tag, name, a.value);
          if (v !== null && v !== "") el.setAttribute(name, v);
        }
        if (tag === "a" && el.hasAttribute("href")) el.setAttribute("rel", "noopener noreferrer");
        to.appendChild(el);
        if (tag !== "br" && tag !== "hr" && tag !== "img" && tag !== "col") copy(n, el);
      }
      depth--;
    }
    copy(src.body, frag);
    // 표 구조 복원: 표는 편집기 규약대로 objline 래퍼 + data-place="char"(편집기 생성 속성 — 입력에서 온 값 아님)
    Array.prototype.slice.call(frag.querySelectorAll("table")).forEach(function (t) {
      if (t.parentNode && t.parentNode.closest && t.parentNode.closest("td,th")) return;   // 칸 안 표는 그대로
      t.setAttribute("data-place", "char");
      if (t.parentNode === frag) { var o = document.createElement("div"); o.className = "objline"; frag.insertBefore(o, t); o.appendChild(t); }
    });
    return { frag: frag, page: meta };
  }
  function sanitizeToString(html) { var d = document.createElement("div"); d.appendChild(sanitizeEmbed(html).frag); return d.innerHTML; }

  // ───────────── 읽기 전용 ─────────────
  var readOnly = false;
  var RO_ACT = { copy: 1, selectall: 1, find: 1, findNext: 1, findPrev: 1, fullview: 1, fullscreen: 1, outline: 1, pageguide: 1, wordcount: 1, browserPrint: 1 };
  var EMBED_NO = { header: 1, pagenum: 1, headFootApply: 1 };   // 머리말/꼬리말·쪽번호 = 호스트 몫(임베드에선 항상 금지)
  WP.actBlocked = function (act) {
    if (has(EMBED_NO, act)) return "머리말·꼬리말·쪽 번호는 결재 서버가 넣습니다";
    return readOnly && !has(RO_ACT, act) ? "읽기 전용 문서" : false;
  };
  function stripHeadFoot() {   // #wpmeta 머리말/꼬리말/쪽번호 속성 제거(어떤 경로로든 생겼다면) — 용지 속성만 남김
    var ms = page.querySelectorAll("#wpmeta"); if (!ms.length) return;
    for (var i = 1; i < ms.length; i++) ms[i].parentNode.removeChild(ms[i]);   // 중복 메타는 첫 것만 남김
    var m = ms[0];
    Array.prototype.slice.call(m.attributes).forEach(function (a) { if (/^data-(header|footer|pagenum|printhf)/.test(a.name)) m.removeAttribute(a.name); });
    while (m.firstChild) m.removeChild(m.firstChild);
  }
  function setReadOnly(on) {
    readOnly = !!on;
    page.setAttribute("contenteditable", readOnly ? "false" : "true");
    document.documentElement.classList.toggle("wp-ro", readOnly);
  }
  function inModal(t) { return t && t.closest && t.closest(".modal"); }
  function roKeyOk(e) {
    if (/^(Shift|Control|Alt|Meta|CapsLock|Escape|F10)$/.test(e.key)) return true;
    if (/^(Arrow|Page|Home|End)/.test(e.key)) return !e.ctrlKey && !e.altKey && !e.metaKey;   // Ctrl/Alt+화살표 = 표 칸 크기 조절 → 차단
    if ((e.ctrlKey || e.metaKey) && /^[acf]$/i.test(e.key) && !e.shiftKey) return true;   // 복사·모두 선택·찾기
    return false;
  }
  window.addEventListener("keydown", function (e) {
    if (!readOnly || inModal(e.target) || roKeyOk(e)) return;
    e.preventDefault(); e.stopImmediatePropagation();
  }, true);
  window.addEventListener("drop", function (e) { if (!inModal(e.target)) { e.preventDefault(); e.stopImmediatePropagation(); } }, true);   // 끌어 놓기 = 정제 우회 → 임베드에선 항상 차단(붙이기 사용)
  ["click", "auxclick"].forEach(function (ev) {   // 문서 안 링크는 iframe 을 이동시키지 않음(피싱 방지). Ctrl/⌘+클릭만 새 탭(noopener)
    window.addEventListener(ev, function (e) {
      var a = e.target && e.target.closest ? e.target.closest("a[href]") : null;
      if (!a || !page.contains(a)) return;
      e.preventDefault(); e.stopImmediatePropagation();
      var h = a.getAttribute("href") || "";
      if ((e.ctrlKey || e.metaKey) && /^(https?:|mailto:)/i.test(h)) window.open(h, "_blank", "noopener,noreferrer");
    }, true);
  });
  ["paste", "cut", "dragstart", "beforeinput"].forEach(function (ev) {
    window.addEventListener(ev, function (e) { if (readOnly && !inModal(e.target)) { e.preventDefault(); e.stopImmediatePropagation(); } }, true);
  });
  ["pointerdown", "mousedown", "dblclick", "contextmenu", "touchstart"].forEach(function (ev) {   // 개체 선택·표 칸 크기 손잡이 등 편집 처리기 차단(기본 글자 선택은 유지)
    window.addEventListener(ev, function (e) { if (readOnly && page.contains(e.target)) e.stopImmediatePropagation(); }, true);
  });

  // ───────────── 메시지 ─────────────
  var parentWin = window.parent !== window ? window.parent : null, armed = false, loading = false;
  function post(msg) { if (parentWin) parentWin.postMessage(msg, ORIGIN); }
  WP.onDirty = function () {
    if (!armed || loading) return;
    armed = false; post({ type: "dirty" });
  };
  function closeUi() {
    document.querySelectorAll(".modal:not(.hidden)").forEach(function (m) { WP.closeModal(m); });
    if (WP.previewActive && WP.exitPreview) try { WP.exitPreview(); } catch (e) {}
    document.body.classList.remove("zen");
  }
  var docId = null;   // 부모가 준 문서 식별자(선택) — html 응답에 그대로 돌려줌(다른 문서 덮어쓰기 방지용 대조)
  function fail(reason) {   // 실패 = 이전 문서를 남기지 않음(빈 읽기 전용) + 부모에 알림
    page.innerHTML = "<p><br></p>"; setReadOnly(true); try { WP.setPaper({}); } catch (e) {} if (WP.histReset) WP.histReset(); WP.clean();
    docId = null; armed = false;
    WP.status("문서를 불러오지 못했습니다"); post({ type: "error", reason: reason });
  }
  function load(d) {
    var html = typeof d.html === "string" ? d.html : "";
    setReadOnly(d.readOnly === true);   // 읽기 전용은 가장 먼저(중간 실패에도 편집 가능 상태로 남지 않게)
    docId = (typeof d.id === "string" || typeof d.id === "number") ? d.id : null;
    if (html.length > MAX_HTML) { fail("too-large"); return; }
    var ok = false;
    loading = true;
    try {
      closeUi();
      if (WP.objDeselect) try { WP.objDeselect(); } catch (e) {}
      if (WP.cbClear) try { WP.cbClear(); } catch (e) {}
      var r = sanitizeEmbed(html);
      page.innerHTML = "";
      page.appendChild(r.frag);
      if (!page.firstChild) page.innerHTML = "<p><br></p>";
      var pg = (d.page && (typeof d.page === "string" || typeof d.page === "object")) ? d.page : (r.page || {});
      WP.setPaper(pg); stripHeadFoot();                  // 지정 없으면 기본(A4·20mm)으로 초기화 — 이전 문서 설정 잔류 방지
      if (WP.normalizeEdges) WP.normalizeEdges();
      if (WP.pageGuides) WP.pageGuides();
      if (WP.histReset) WP.histReset();
      WP.clean();
      WP.status(readOnly ? "읽기 전용" : "편집 중");
      ok = true;
    } catch (e) { ok = false; }
    finally { loading = false; }
    if (!ok) { fail("load-failed"); return; }
    armed = true;
  }
  window.addEventListener("message", function (e) {
    if (!parentWin || e.source !== parentWin || e.origin !== ORIGIN) return;   // 같은 출처 부모만
    var d = e.data;
    if (!d || typeof d !== "object" || typeof d.type !== "string") return;
    if (d.type === "load") load(d);
    else if (d.type === "get") {
      stripHeadFoot();
      var html = WP.cleanedHTML ? WP.cleanedHTML() : page.innerHTML;
      armed = true;
      post({ type: "html", html: html, page: WP.pageCfg ? WP.pageCfg() : null, id: docId });
    }
  });

  // ───────────── 그림 삽입(서버 그림 폴더 대신 이 PC에서 고름 → 내장 data URI) ─────────────
  var imgIn = document.createElement("input");
  imgIn.type = "file"; imgIn.accept = "image/png,image/jpeg,image/gif,image/webp"; imgIn.style.display = "none";
  document.body.appendChild(imgIn);
  imgIn.addEventListener("change", function () {
    var f = imgIn.files && imgIn.files[0]; imgIn.value = ""; if (!f) return;
    var rd = new FileReader();
    rd.onload = function () {
      var u = String(rd.result);
      if (!/^data:image\/(png|jpe?g|gif|webp);base64,/i.test(u)) { WP.toast("그림 파일만 넣을 수 있습니다"); return; }
      WP.restoreRange();
      if (WP.insertImageObj) WP.insertImageObj(u);
      else { var im = document.createElement("img"); im.src = u; im.style.maxWidth = "100%"; WP.insertBlocks([im]); }
      WP.markDirty(); WP.status("그림 삽입됨");
    };
    rd.readAsDataURL(f);
  });
  WP.register({ image: function () { WP.saveRange(); imgIn.click(); } });

  WP.sanitizeEmbed = sanitizeToString;   // 시험용 노출
  WP.sanitizeEmbedFrag = function (html) { return sanitizeEmbed(html).frag; };   // 붙이기(wp-core pasteExternal)도 같은 허용목록
  WP.embedReadOnly = function () { return readOnly; };
  var di = document.getElementById("docname"); if (di) di.value = "문서";
  WP.status("편집기 준비됨");
  armed = true;
  function ready() { post({ type: "ready" }); }
  if (document.readyState === "complete") ready(); else window.addEventListener("load", ready);   // 모든 모듈·초기 배치 뒤 1회
})();
