// 한워드 — 코어(상태·헬퍼·메뉴·단축키·저장/불러오기). window.WP 네임스페이스 생성.
//   모듈(wp-format/table/find/io)은 WP.* 헬퍼를 alias하고 WP.register({})로 ACT에 동작 등록.
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var page = $("page"), docname = $("docname"), statusEl = $("status");
  var menubar = $("menubar"), dropdown = $("dropdown"), toast = $("toast");
  // Enter가 <div> 대신 <p>를 만들게(WebKit 기본=div → 제목/목록/문단 블록작업이 엉뚱한 블록에 적용되는 버그 방지)
  try { document.execCommand("defaultParagraphSeparator", false, "p"); } catch (e) {}
  // 임베드 모드(?embed=1, 2026-11-13): 결재 등 같은 출처 부모 페이지가 iframe으로 띄우는 단일 문서 편집기.
  //   파일 목록·저장·삭제·이름변경 등 서버 저장 UI 전부 숨김, /api 호출 금지 — 문서 입출력은 wp-embed.js postMessage.
  var EMBED = window.WP_IFRAME = /^\?(?:.*&)?embed=1(?:&|$)/.test(location.search) || document.documentElement.hasAttribute("data-wp-embed");   // embed.html 입구 = 표식 속성
  if (EMBED) document.documentElement.classList.add("wp-embed");
  var dirty = false, saveTimer = null, savedRange = null, saving = false, pendingSave = false;
  // 선택영역 저장/복원(대화상자가 포커스 가져가도 유지) — 여러 모듈 공유
  function saveRange() { var s = window.getSelection(); if (s.rangeCount && page.contains(s.anchorNode)) savedRange = s.getRangeAt(0).cloneRange(); }
  function restoreRange() {   // savedRange가 detached(undo/불러오기로 노드 교체)면 안전 폴백
    if (savedRange && page.contains(savedRange.startContainer)) {
      try { var s = window.getSelection(); s.removeAllRanges(); s.addRange(savedRange); page.focus(); return; } catch (e) {}
    }
    page.focus();
  }

  // ── 다단계 되돌리기/다시실행 (자체 히스토리 — innerHTML 직접조작도 깊게 추적, 네이티브 execCommand undo 한계 극복) ──
  //   블록 diff 방식(#15 P1): hist[0]=베이스 상태 {blocks,caret}, hist[i>0]=전이 {start,oldB,newB,caretB,caretA}(상태 i-1→i).
  //   블록 = cleanedHTML의 최상위 노드 직렬화 조각 — join("")이 cleanedHTML()과 byte 동일. 메모리 O(변경분), 전체 스냅샷은 베이스 1개.
  var hist = [], hi = -1, histTimer = null, restoring = false, HISTMAX = 80;   // 핸드헬드 RAM: 상태 상한(베이스+전이 79)
  var histBlocks = null, histCaret = null;        // 현재 상태(hi)의 블록배열 캐시·캐럿
  function caretOffsets() {                       // 선택을 page 텍스트 기준 [start,end] 문자오프셋으로
    var s = window.getSelection(); if (!s.rangeCount || !page.contains(s.anchorNode)) return null;
    var r = s.getRangeAt(0);
    function off(node, o) { var rr = document.createRange(); rr.selectNodeContents(page); try { rr.setEnd(node, o); } catch (e) { return null; } return rr.toString().length; }
    var a = off(r.startContainer, r.startOffset), b = off(r.endContainer, r.endOffset);
    return (a === null || b === null) ? null : [a, b];
  }
  function nodeAtOffset(target) {                  // 문자오프셋 → {node,offset}
    var w = document.createTreeWalker(page, NodeFilter.SHOW_TEXT, null), n, acc = 0;
    while ((n = w.nextNode())) { var len = n.nodeValue.length; if (acc + len >= target) return { node: n, offset: target - acc }; acc += len; }
    return { node: page, offset: page.childNodes.length };
  }
  function setCaret(o) {
    if (!o) { page.focus(); return; }
    var a = nodeAtOffset(o[0]), b = nodeAtOffset(o[1]), r = document.createRange();
    try { r.setStart(a.node, a.offset); r.setEnd(b.node, b.offset); } catch (e) { r.selectNodeContents(page); r.collapse(false); }
    var s = window.getSelection(); s.removeAllRanges(); s.addRange(r); page.focus();
  }
  function recordNow() {                           // 현재 상태를 히스토리에 diff로 적재(변화 없으면 caret만 갱신)
    if (restoring) return;
    var cur = cleanedBlocks();                      // 페이지네이션 스페이서 제외(undo/redo는 깨끗한 본문 기준)
    var caret = caretOffsets();
    if (hi < 0) {                                   // 베이스 없음(방어 — 보통 histReset이 만듦)
      hist = [{ blocks: cur.slice(), caret: caret }]; hi = 0; histBlocks = cur; histCaret = caret; return;
    }
    var prev = histBlocks, pLen = prev.length, cLen = cur.length, min = Math.min(pLen, cLen), p = 0, s = 0;
    while (p < min && prev[p] === cur[p]) p++;                                   // 공통 prefix
    while (s < min - p && prev[pLen - 1 - s] === cur[cLen - 1 - s]) s++;         // 공통 suffix
    if (p + s >= pLen && p + s >= cLen) {           // 변화 없음 — 현재 상태의 caret만 갱신(기존 skip 경로와 동일 의미)
      histCaret = caret || histCaret;
      if (hi > 0) hist[hi].caretA = histCaret; else hist[0].caret = histCaret;
      return;
    }
    if (hi < hist.length - 1) {                     // ⭐Emacs식 무손실 언두(2026-07-19 사용자 설계): redo 가지를 버리지 않고
      //   꼭대기→현재까지의 '역전이'를 항목으로 이어붙임 → 버려질 뻔한 상태들(18·19·20)이 되돌리기 연타로 전부 도달 가능.
      //   예) 20에서 17로 되돌린 뒤 새 편집 → 이후 되돌리기 순회: 19-1 18-1 17 18 19 20 19 18 17 16 15
      for (var k = hist.length - 1; k > hi; k--) {
        var t = hist[k];
        hist.push({ start: t.start, oldB: t.newB.slice(), newB: t.oldB.slice(), caretB: t.caretA, caretA: t.caretB });
      }
      hi = hist.length - 1;
      toastMsg("이전 흐름 보존됨 — 되돌리기로 전부 복원 가능");
    }
    hist.push({ start: p, oldB: prev.slice(p, pLen - s), newB: cur.slice(p, cLen - s), caretB: histCaret, caretA: caret });
    while (hist.length > HISTMAX) {                 // 상한 초과 — 가장 오래된 전이를 베이스에 접어넣음(가지보존 append로 여러 개 초과 가능=while)
      var b = hist[0], t = hist[1];
      Array.prototype.splice.apply(b.blocks, [t.start, t.oldB.length].concat(t.newB));
      b.caret = t.caretA; hist.splice(1, 1);
    }
    hi = hist.length - 1; histBlocks = cur; histCaret = caret;
  }
  function scheduleRecord() { if (restoring) return; clearTimeout(histTimer); histTimer = setTimeout(recordNow, 350); }   // 타이핑은 묶음
  function flushRecord() { clearTimeout(histTimer); recordNow(); }
  function histReset() {                            // 새 문서/불러오기 시 히스토리 리셋(현 상태 = 베이스)
    clearTimeout(histTimer);
    histBlocks = cleanedBlocks(); histCaret = caretOffsets();
    hist = [{ blocks: histBlocks.slice(), caret: histCaret }]; hi = 0;
    updatePh();   // 새 문서/불러오기 등 프로그램적 본문 교체 후 플레이스홀더 갱신(input 이벤트 없음)
    if (window.WP && window.WP.syncHiddenGuides) window.WP.syncHiddenGuides();   // hidden 변 안내선 재도출(HWPX 임포트=인라인만 옴)
    if (window.WP && window.WP.resolveMediaSrcs) window.WP.resolveMediaSrcs();   // 파일명 단독 미디어 참조 → 재생 URL
    if (window.WP && window.WP.normalizeObjStyles) window.WP.normalizeObjStyles();   // 그림 래퍼 인라인 position 잔재 제거(배치 CSS 우선권 확보)
  }
  function histApply(blocks, e, back) {             // 전이 e를 블록배열에 적용(back=true면 역방향)
    var del = back ? e.newB : e.oldB, ins = back ? e.oldB : e.newB;
    Array.prototype.splice.apply(blocks, [e.start, del.length].concat(ins));
  }
  function histCheck(e, back) {                     // 적용 전 무결성 — 캐시가 전이의 기대 상태와 일치하는가
    var expect = back ? e.newB : e.oldB, i;
    if (e.start < 0 || e.start + expect.length > histBlocks.length) return false;
    for (i = 0; i < expect.length; i++) if (histBlocks[e.start + i] !== expect[i]) return false;
    return true;
  }
  function histRebuild(target) {                    // 폴백: 베이스(전체 스냅샷)에서 전이 재적용으로 상태 target 재구성
    var blocks = hist[0].blocks.slice(), i;
    for (i = 1; i <= target; i++) histApply(blocks, hist[i], false);
    return blocks;
  }
  var _insTmp = document.createElement("div");
  function regenPageData() {   // 파생(각주번호) 재생성 — undo diff엔 정규화돼 안 잡히므로 렌더 후 채워 최신 유지. 목차=폐지·일반 텍스트(감독 지시 07-17, 재생성 안 함)
    try { if (window.WP.fnRenumNow && page.querySelector("div.fnotes, sup.fn")) window.WP.fnRenumNow(); } catch (e) {}
  }
  function histRender(e, back) {                    // 상태 캐시를 화면에 반영 — 변경 블록 범위만 부분 DOM 교체(P2). e 없거나 DOM 불일치면 전체 교체 폴백
    restoring = true;
    var done = false;
    if (e) {
      var del = back ? e.newB : e.oldB, ins = back ? e.oldB : e.newB;
      var live = [], n, next;                        // 라이브 최상위 노드(스페이서 제외) = cleanedBlocks 인덱스와 1:1
      for (n = page.firstChild; n; n = n.nextSibling) if (!(n.nodeType === 1 && n.hasAttribute("data-pgfill"))) live.push(n);
      if (live.length === histBlocks.length - ins.length + del.length) {   // DOM이 전이 직전 상태일 때만(아니면 폴백)
        var anchor = (e.start + del.length < live.length) ? live[e.start + del.length] : null;
        var cur = (e.start < live.length) ? live[e.start] : null;
        while (cur && cur !== anchor) { next = cur.nextSibling; page.removeChild(cur); cur = next; }   // 대상 블록+사이 스페이서 제거
        _insTmp.innerHTML = ins.join("");
        while (_insTmp.firstChild) page.insertBefore(_insTmp.firstChild, anchor);
        done = true;                                 // childList 변이 → wp-page MutationObserver가 paginate 재예약(전체교체와 동일 경로)
      }
    }
    if (!done) page.innerHTML = histBlocks.join("");
    setCaret(histCaret);
    regenPageData();   // 목차·각주번호는 undo에서 정규화(빈껍데기/공백) → 렌더 직후 실제 내용으로 재생성
    restoring = false; updatePh();   // undo/redo로 비워지거나 채워질 때 플레이스홀더 동기
    dirty = true; status("편집 중 · 저장 안 됨");   // 자동저장 없음(2026-07-16): 저장=Ctrl+S 명시
  }
  function histUndo() {
    flushRecord(); if (hi <= 0) { toastMsg("되돌릴 작업이 없습니다"); return; }
    if (window.WP.objDeselect) window.WP.objDeselect();   // 재렌더로 노드가 교체되면 sel이 detach → 언두 전 선택 해제(스테일 핸들 방지)
    var e = hist[hi], sane = histCheck(e, true);
    if (sane) histApply(histBlocks, e, true); else histBlocks = histRebuild(hi - 1);
    hi--; histCaret = e.caretB; histRender(sane ? e : null, true); status("되돌림 (" + hi + ")");
  }
  function histRedo() {
    flushRecord(); if (hi >= hist.length - 1) { toastMsg("다시 실행할 작업이 없습니다"); return; }
    if (window.WP.objDeselect) window.WP.objDeselect();
    hi++; var e = hist[hi], sane = histCheck(e, false);
    if (sane) histApply(histBlocks, e, false); else histBlocks = histRebuild(hi);
    histCaret = e.caretA; histRender(sane ? e : null, false); status("다시 실행 (" + hi + ")");
  }

  // ── 편집 역사 파일 보존(암호화, 2026-07-19 사용자 설계): 저장 시 .hist/<문서명>.hist(AES)로 잠가 두고
  //    열 때 암호 입력→복호+무결성 검증(저장시점 최종상태==현재 문서) 통과 시만 채택. 암호=세션 메모리만.
  var _histPw = null, _hpEl = null;
  function histPwModal(title, hint, cb) {
    if (!_hpEl) { _hpEl = document.createElement("div"); _hpEl.id = "histpw"; document.body.appendChild(_hpEl); }
    _hpEl.innerHTML = '<div class="hp-t"></div><div class="hp-h"></div><input type="password" id="hp-in" autocomplete="off">' +
      '<div class="hp-b"><button id="hp-ok">확인</button><button id="hp-x">건너뜀</button></div>';
    _hpEl.querySelector(".hp-t").textContent = title;
    _hpEl.querySelector(".hp-h").textContent = hint;
    _hpEl.classList.remove("hidden");
    var inp = _hpEl.querySelector("#hp-in");
    function close(v) { _hpEl.classList.add("hidden"); cb(v); }
    _hpEl.querySelector("#hp-ok").addEventListener("pointerup", function () { close(inp.value || null); });
    _hpEl.querySelector("#hp-x").addEventListener("pointerup", function () { close(null); });
    inp.addEventListener("keydown", function (e) { e.stopPropagation(); if (e.key === "Enter") close(inp.value || null); else if (e.key === "Escape") close(null); });
    setTimeout(function () { try { inp.focus(); } catch (e) {} }, 60);
  }
  function persistHist(name) {
    if (!name || window.WP_PUBLIC) return;
    var doSave = function (pw) {
      api("/api/hist/save", { name: name, pw: pw, data: { hist: hist, hi: hi } }).then(function (r) {
        if (r && r.ok) status("저장됨 · 역사 잠금보존 🔒");
        else if (r && r.err) toastMsg("역사 보존 생략: " + r.err);
      }).catch(function () {});
    };
    if (_histPw) { doSave(_histPw); return; }
    histPwModal("편집 역사 보존 — 암호 설정", "이 암호로 역사 파일이 잠깁니다(분실 시 역사만 복구 불가·문서는 무관). 건너뜀=역사 저장 안 함", function (pw) {
      if (pw) { _histPw = pw; doSave(pw); }
    });
  }
  function adoptHist(d) {
    if (!d || !d.hist || !d.hist.length || typeof d.hi !== "number") return false;
    try {
      var blocks = d.hist[0].blocks.slice(), i, e;
      for (i = 1; i <= d.hi; i++) { e = d.hist[i]; Array.prototype.splice.apply(blocks, [e.start, e.oldB.length].concat(e.newB)); }
      if (blocks.join("") !== cleanedBlocks().join("")) return false;   // 무결성: 다른 곳에서 수정된 문서엔 역사 미적용
      hist = d.hist; hi = d.hi; histBlocks = blocks;
      histCaret = (hi > 0 ? d.hist[hi].caretA : d.hist[0].caret) || null;
      return true;
    } catch (x) { return false; }
  }
  var WP_setHistPw = function () {   // 도구>역사 암호 설정/변경 — 세션 암호 재설정(다음 저장부터 새 암호로 잠김)
    histPwModal("편집 역사 암호 설정/변경", window.WP_PUBLIC
      ? "다음 저장·내려받기부터 이 암호로 되돌리기 역사가 암호화되어 이 브라우저 보관소와 내려받는 HTML에 동봉됩니다(분실 시 역사만 복구 불가·문서는 무관). 건너뜀=변경 안 함"
      : "다음 저장부터 이 암호로 역사가 잠깁니다(기존 역사 파일은 옛 암호 유지). 건너뜀=변경 안 함", function (pw) {
      if (pw) { _histPw = pw; toastMsg("역사 암호 설정됨 — 저장 시 적용"); }
    });
  };
  function restoreHistFlow(name) {
    api("/api/hist/has?name=" + encodeURIComponent(name)).then(function (r) {
      if (!(r && r.has)) return;
      var tryLoad = function (pw, askOnFail) {
        api("/api/hist/load", { name: name, pw: pw }).then(function (l) {
          if (l && l.ok) {
            if (adoptHist(l.data)) toastMsg("편집 역사 복원됨 — 되돌리기 " + hi + "단계 🔓");
            else toastMsg("역사가 현재 문서와 달라 폐기됨(다른 곳에서 수정됨)");
          } else if (askOnFail) { toastMsg((l && l.err) || "암호 불일치"); ask(); }
          else toastMsg((l && l.err) || "암호 불일치");
        }).catch(function () {});
      };
      function ask() {
        histPwModal("🔒 편집 역사 복원 — 암호", "이 문서의 되돌리기 역사가 잠겨 있습니다. 건너뜀=역사 없이 열기", function (pw) {
          if (!pw) return;
          if (!_histPw) _histPw = pw;
          tryLoad(pw, false);
        });
      }
      if (_histPw) tryLoad(_histPw, true); else ask();
    }).catch(function () {});
  }

  // ── HWP 메뉴 구성 ──
  var MENUS = [
    { name:"파일", items: EMBED ? [
      ["바로 인쇄(브라우저)","","browserPrint"] ] : window.WP_PUBLIC ? [
      ["새 문서","Alt+N","new"], ["내 PC에서 열기 (업로드)","Alt+O","open"], "-",
      ["HTML로 내려받기","Alt+S","dlHtml"],
      ["한글 문서로 내려받기 (HWPX)","","dlHwpx"],
      ["워드 문서로 내려받기 (DOCX)","","dlDocx"],
      ["텍스트로 내려받기 (TXT)","","dlTxt"],
      ["PDF로 내려받기","Alt+P","dlPdf"] ] : [
      ["새 문서","Alt+N","new"], ["불러오기","Alt+O","open"],
      ["저장하기","Alt+S","save"], ["다른 이름으로 저장하기","Alt+V","saveas"], "-",
      ["한글 문서로 저장 (HWPX)","","exportHwpx"], ["한글 문서 열기 (HWPX/HWP)","","openHwpx"], "-",
      ["PDF 저장","Alt+P","pdf"], "-",
      ["인쇄 (프린터)","","printer"], ["인쇄 취소","","printCancel"], ["바로 인쇄(브라우저)","","browserPrint"], "-", ["끝","Alt+X","quit"] ]},   // #29 끝: 인앱 저장확인 후 종료(2026-07-18 — Home 안내 제거)
    { name:"편집", items:[
      ["되돌리기","Ctrl+Z","undo"], ["다시 실행","Ctrl+Shift+Z","redo"], "-",
      ["오려 두기","Ctrl+X","cut"], ["복사하기","Ctrl+C","copy"], ["붙이기","Ctrl+V","paste"], "-",
      ["모두 선택","Ctrl+A","selectall"], ["찾기","Ctrl+F","find"], ["찾아 바꾸기","Ctrl+H","replace"] ]},
    { name:"보기", items:[ ["전체보기 (쪽 맞춤)","","fullview"], ["전체 화면 전환","","fullscreen"], ["쪽 윤곽","","outline"], ["쪽 경계","","pageguide"] ]},
    { name:"입력", items:[
      ["표","Ctrl+N,T","table"], ["그림","Ctrl+N,I","image"], ["미디어 삽입(영상·소리)","","media"], ["특수 문자…","Ctrl+F10","symbol"], "-",
      ["캡션 달기","","caption"], ["캡션 번호 갱신","","captionRenum"],
      ["하이퍼링크…","Ctrl+K","link"], ["가로줄","","hr"], ["쪽 나누기","Ctrl+Enter","pagebreak"], ["목록 속성…","","listDlg"] ]},   // 쪽 나누기 입력메뉴 추가(감독 지시 07-17, 쪽 메뉴에도 유지=양쪽 접근)
    { name:"서식", items:[
      ["글자 모양…","Alt+L","charDlg"], ["문단 모양…","Alt+T","paraDlg"], ["문단 기본값","Alt+Shift+C","paraDefault"], ["스타일","F6","style"], "-",
      ["진하게","Ctrl+B","bold"], ["기울임","Ctrl+I","italic"], ["밑줄","Ctrl+U","underline"], ["취소선","","strike"], "-",
      ["글자 크게","Alt+Shift+E","bigger"], ["글자 작게","Alt+Shift+R","smaller"], "-",
      ["왼쪽 정렬","Ctrl+Shift+L","justifyLeft"], ["가운데 정렬","Ctrl+Shift+C","justifyCenter"],
      ["오른쪽 정렬","Ctrl+Shift+R","justifyRight"], ["양쪽 정렬","Ctrl+Shift+M","justifyFull"] ]},
    { name:"쪽", items:[
      ["편집 용지…","F7","pageDlg"], ["쪽 나누기","Ctrl+Enter","pagebreak"], "-",
      ["머리말/꼬리말","Ctrl+N,H","header"], ["쪽 번호 매기기","Ctrl+N,P","pagenum"], "-",
      ["선택 그림을 쪽 배경으로 (계획중)","","objToBg"], "-",
      ["목차 생성(현재 쪽 기준)","","tocGen"], "-",
      ["미주 삽입","","fnInsert"], ["미주 번호 갱신","","fnRenum"] ]},   // 목차 생성=1회 스냅샷(편집가능 텍스트, 2026-07-20). 옛 자동갱신 목차는 폐지
    { name:"도구", items:[ ["맞춤법 검사","F8","spell"], ["글자 수","","wordcount"], "-", ["편집 역사 암호 설정/변경","","histpw"], "-", ["버그 신고 / 게시판","","board"] ]},
  ];
  if (EMBED) {   // 임베드: 서버 저장소·외부 게시판·역사 암호(서버 .hist)·미디어(IDB/서버 폴더) 메뉴 제거
    var EMBED_DROP = { media:1, histpw:1, board:1, objToBg:1, header:1, pagenum:1 };   // 머리말/꼬리말·쪽번호 = 호스트(결재 서버)가 직접 인쇄
    MENUS.forEach(function (m) { if (m.items) m.items = m.items.filter(function (it) { return it === "-" || !EMBED_DROP[it[2]]; }); });
  }
  if (window.WP_PUBLIC || EMBED) {   // 공개 웹: 미디어 삽입(영상·소리)=P10 웹 미디어(IndexedDB) 지원으로 노출(histpw도 P13로 노출). 꼬리 구분선만 정리(2026-07-20)
    MENUS.forEach(function (m) { if (m.items) {
      m.items = m.items.filter(function (it, k, a) { return it !== "-" || (k > 0 && a[k - 1] !== "-"); });   // 연속 구분선 정리
      while (m.items.length && m.items[m.items.length - 1] === "-") m.items.pop(); } });   // 필터로 남은 꼬리 구분선 제거
  }
  // ── 단축키: 전부 아래한글(HWP) 표준에 맞춤(비표준 우리 키 폐기) ──
  var HOT = {
    // 파일
    "alt+n":"new","alt+o":"open","alt+s":"save","ctrl+s":"save","alt+v":"saveas","alt+p":"pdf","ctrl+p":"pdf","alt+x":"quit",
    // 편집
    "ctrl+z":"undo","ctrl+shift+z":"redo","ctrl+a":"selectall",
    "ctrl+f":"find","f2":"find","ctrl+h":"replace","ctrl+f2":"replace","ctrl+l":"findNext",
    // 글자 모양
    "ctrl+b":"bold","alt+shift+b":"bold","ctrl+i":"italic","alt+shift+i":"italic","ctrl+u":"underline","alt+shift+u":"underline",
    "alt+shift+p":"superscript","alt+shift+s":"subscript",
    "alt+shift+e":"bigger","ctrl+]":"bigger","alt+shift+r":"smaller","ctrl+[":"smaller",
    "alt+shift+w":"letterWider","alt+shift+n":"letterNarrower","alt+l":"charDlg",
    // 문단 모양
    "ctrl+shift+l":"justifyLeft","ctrl+shift+c":"justifyCenter","ctrl+shift+r":"justifyRight","ctrl+shift+m":"justifyFull","ctrl+shift+t":"justifyFull",
    "alt+shift+z":"lineWider","ctrl+shift+w":"lineWider","alt+shift+a":"lineNarrower","ctrl+shift+q":"lineNarrower",   // HWP: Z=넓게, A=좁게
    "ctrl+shift+i":"firstIndent","ctrl+shift+o":"firstOutdent",
    "ctrl+shift+g":"marginLInc","ctrl+shift+e":"marginLDec","ctrl+shift+d":"marginRInc","ctrl+shift+f":"marginRDec",   // 좌/우 여백
    "alt+t":"paraDlg","alt+c":"formatCopy","alt+shift+c":"paraDefault",   // 문단 서식 기본값 복원
    // 스타일
    "ctrl+0":"styleP","ctrl+1":"styleH1","ctrl+2":"styleH2","ctrl+3":"styleH3","f6":"style",
    // 쪽
    "ctrl+Enter":"pagebreak","ctrl+j":"pagebreak","f7":"pageDlg",
    // 입력·도구
    "ctrl+f10":"symbol","f8":"spell","ctrl+k":"link",
    // 이동·선택
    "alt+Home":"paraHome","alt+End":"paraEnd","f3":"blockCycle",
  };
  if (EMBED) {   // 임베드: 파일 단축키(새 문서·열기·저장·PDF·끝) 비활성 — 문서 입출력은 부모 페이지 몫
    ["alt+n","alt+o","alt+s","ctrl+s","alt+v","alt+p","ctrl+p","alt+x"].forEach(function (k) { delete HOT[k]; });
  } else if (window.WP_PUBLIC) {   // 공개모드: 저장/인쇄 핫키를 다운로드로 전환(wp-public.js가 dl* 등록)
    HOT["alt+s"] = "dlHtml"; HOT["ctrl+s"] = "dlHtml"; HOT["alt+p"] = "dlPdf"; HOT["ctrl+p"] = "dlPdf";
  }

  // ── 메뉴 막대 (키보드/게임패드: F10→포커스, ←→ 이동, Enter/↓ 열기 — HEAD fd4b780 복원) ──
  menubar.setAttribute("role", "menubar");
  MENUS.forEach(function (m, i) {
    var el = document.createElement("div"); el.className = "mb";
    el.innerHTML = '<span class="acc">' + m.name.charAt(0) + '</span>' + m.name.slice(1);
    el.tabIndex = 0; el.setAttribute("role", "menuitem");
    el.addEventListener("pointerdown", function (e) { e.preventDefault(); e.stopPropagation(); saveRange(); toggleMenu(i, el); });
    el.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " " || e.key === "ArrowDown") {
        e.preventDefault(); saveRange(); if (openIdx !== i) toggleMenu(i, el);
        var first = dropdown.querySelector(".dd-item"); if (first) first.focus();
      } else if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
        e.preventDefault(); var n = MENUS.length, j = (i + (e.key === "ArrowRight" ? 1 : n - 1)) % n;
        MENUS[j]._el.focus();
        if (openIdx >= 0 && openIdx !== j) toggleMenu(j, MENUS[j]._el);
      } else if (e.key === "Escape" && openIdx >= 0) { e.stopPropagation(); closeDropdown(); }
    });
    menubar.appendChild(el); m._el = el;
  });
  var openIdx = -1;
  function toggleMenu(i, el) {
    if (openIdx === i) { closeDropdown(); return; }
    openIdx = i;
    MENUS.forEach(function (m) { m._el.classList.remove("open"); });
    el.classList.add("open"); dropdown.innerHTML = "";
    dropdown.setAttribute("role", "menu");
    MENUS[i].items.forEach(function (it) {
      if (it === "-") { var s = document.createElement("div"); s.className = "dd-sep"; dropdown.appendChild(s); return; }
      var d = document.createElement("div"); d.className = "dd-item"; d.setAttribute("data-act", it[2]);
      d.tabIndex = -1; d.setAttribute("role", "menuitem");
      var label = it[0];   // 되돌리기/다시실행 옆에 가능 횟수 표시(메뉴 열 때마다 갱신)
      if (it[2] === "undo") label += " (" + Math.max(0, hi) + ")";
      else if (it[2] === "redo") label += " (" + Math.max(0, hist.length - 1 - hi) + ")";
      if ((it[2] === "undo" && hi <= 0) || (it[2] === "redo" && hi >= hist.length - 1)) d.classList.add("dd-dim");   // 0이면 흐리게
      d.innerHTML = '<span>' + label + '</span><span class="hk">' + (it[1] || "") + '</span>';
      dropdown.appendChild(d);
    });
    var r = el.getBoundingClientRect();
    dropdown.style.left = Math.min(r.left, window.innerWidth - 220) + "px";
    dropdown.style.top = r.bottom + "px"; dropdown.classList.remove("hidden");
    // 세로 오버플로: 좁은 화면(480px 등)서 화면 밖으로 내려가면 위로 클램프
    var dh = dropdown.offsetHeight, want = r.bottom;
    if (want + dh > window.innerHeight - 4) want = Math.max(4, window.innerHeight - dh - 4);
    dropdown.style.top = want + "px";
  }
  function closeDropdown() { openIdx = -1; dropdown.classList.add("hidden"); MENUS.forEach(function (m) { m._el.classList.remove("open"); }); }
  // 드롭다운 항목 선택은 pointerup 위임 — 누른 곳/뗀 곳이 달라도(게임패드 커서 드리프트) 뗀 순간 항목으로 판정
  dropdown.addEventListener("pointerdown", function (e) { e.stopPropagation(); e.preventDefault(); });
  dropdown.addEventListener("pointerup", function (e) {
    var d = e.target.closest(".dd-item"); if (!d) return;
    e.stopPropagation(); var act = d.getAttribute("data-act"); closeDropdown(); restoreRange(); dispatch(act);
  });
  dropdown.addEventListener("keydown", function (e) {   // 풀다운 키보드 내비게이션(HEAD 복원)
    var items = Array.prototype.slice.call(dropdown.querySelectorAll(".dd-item"));
    var idx = items.indexOf(document.activeElement);
    if (e.key === "ArrowDown") { e.preventDefault(); (items[Math.min(items.length - 1, idx + 1)] || items[0]).focus(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); (items[Math.max(0, idx - 1)] || items[0]).focus(); }
    else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault(); var d = document.activeElement;
      if (d && d.classList.contains("dd-item")) { var act = d.getAttribute("data-act"); closeDropdown(); restoreRange(); dispatch(act); }
    }
    else if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
      e.preventDefault(); var n = MENUS.length, j = (openIdx + (e.key === "ArrowRight" ? 1 : n - 1)) % n;
      toggleMenu(j, MENUS[j]._el); var first = dropdown.querySelector(".dd-item"); if (first) first.focus();
    }
    else if (e.key === "Escape") {
      e.preventDefault(); e.stopPropagation();
      var mb = MENUS[openIdx] && MENUS[openIdx]._el; closeDropdown(); if (mb) mb.focus();
    }
  });
  document.addEventListener("pointerdown", function () { if (openIdx >= 0) closeDropdown(); });

  // ── dispatch / 코어 동작 ──
  function exec(cmd, val) { document.execCommand(cmd, false, val || null); page.focus(); markDirty(); }
  function dispatch(act) {
    var blk = window.WP.actBlocked && window.WP.actBlocked(act);   // 임베드: 금지 동작(사유 문자열) / 읽기 전용
    if (blk) { toastMsg(typeof blk === "string" ? blk : "읽기 전용 문서"); return; }   // 임베드 읽기 전용
    var f = ACT[act]; if (!f) { toastMsg("준비중: " + act); return; }
    f();
    if (WP.syncToolbar) requestAnimationFrame(WP.syncToolbar);   // 정렬·목록 등 selectionchange 없는 액션도 .on 즉시 반영(P1-5)
  }
  // ── 붙여넣기/개체 클립보드(2026-07-13 P0): execCommand("paste")는 cog/WebKit no-op → 자체 경로 ──
  //   WP._objClip = 내부 개체 클립보드(표 등, 권한 불필요) — wp-table가 채움. 없으면 시스템 클립보드 폴백.
  var WP_EMBED = (location.protocol === "wp:");   // 임베드(WPE) 판정 — 내부 텍스트클립 우회는 임베드 전용(웹 네이티브 클립보드 보호)
  var CLIP_KEEP = { style:1, href:1, src:1, alt:1, title:1, colspan:1, rowspan:1, "data-place":1, align:1, valign:1, width:1, height:1,
                    "data-obj":1, "data-shape":1, "data-x1":1, "data-y1":1, "data-x2":1, "data-y2":1 };   // 도형(.wp-obj) 개체성 보존(data-oid는 중복방지 위해 제외=select시 재할당)
  function sanitizeHTML(html) {   // 외부 HTML 붙임 정제: 위험 요소·속성 제거, 서식/표 구조만 유지
    // 비활성 문서에서 파싱(2026-11-13): 라이브 document의 div.innerHTML은 <img onerror>를 정제 전에 실행 → 클립보드 HTML XSS
    var tmp = document.implementation.createHTMLDocument("").createElement("div"); tmp.innerHTML = html || "";
    Array.prototype.forEach.call(tmp.querySelectorAll("script,style,link,meta,title,noscript,iframe,object,embed,form,input,button,foreignObject,animate,set,animateTransform,animateMotion,use,template"), function (el) { if (el.parentNode) el.parentNode.removeChild(el); });
    Array.prototype.forEach.call(tmp.querySelectorAll("*"), function (el) {
      var inSvg = el.namespaceURI === "http://www.w3.org/2000/svg";   // SVG 도형은 표현속성(fill/stroke/d/viewBox…) 필수 → 이벤트핸들러만 제거(벗기면 검정 도형)
      Array.prototype.slice.call(el.attributes).forEach(function (at) {
        var n = at.name.toLowerCase();
        if (inSvg) { if (n.slice(0, 2) === "on" || /^\s*javascript:/i.test(at.value)) el.removeAttribute(at.name); return; }
        if (n === "class") {
          if (el.classList.contains("objline")) el.setAttribute("class", "objline");
          else if (el.classList.contains("wp-obj")) el.setAttribute("class", "wp-obj");   // 도형 래퍼 보존(검정/비개체화 방지)
          else el.removeAttribute(at.name);
          return;
        }
        if (!CLIP_KEEP[n]) el.removeAttribute(at.name);
      });
      if (el.tagName === "A") { var h = el.getAttribute("href") || ""; if (/^\s*javascript:/i.test(h)) el.removeAttribute("href"); }
    });
    return tmp;
  }
  function topBlock() {   // 캐럿이 속한 #page 직속 최상위 블록(표는 objline/table 자체)
    var s = window.getSelection(); if (!s.rangeCount) return page.lastChild;
    var r = s.getRangeAt(0), n = r.commonAncestorContainer;
    if (n === page) return page.childNodes[Math.min(r.startOffset, page.childNodes.length - 1)] || page.lastChild;
    if (n.nodeType === 3) n = n.parentNode;
    while (n && n.parentNode && n.parentNode !== page) n = n.parentNode;
    return (n && n.parentNode === page) ? n : page.lastChild;
  }
  function placeCaretIn(node) {   // 삽입한 개체(표=첫 셀, 그 외=내용 끝)로 캐럿 이동
    var cell = node && node.querySelector ? node.querySelector("td,th") : null, target = cell || node;
    try { var r = document.createRange(); r.selectNodeContents(target); r.collapse(!!cell); var s = window.getSelection(); s.removeAllRanges(); s.addRange(r); } catch (e) {}
    page.focus();
  }
  function insertBlocksAsSiblings(nodes) {   // 표 등 블록을 최상위 형제로 삽입(셀 중첩 방지)
    var tb = topBlock(), ref = tb ? tb.nextSibling : null, last = null;
    nodes.forEach(function (n) {
      if (n.nodeType === 1 && /^(BUTTON|INPUT|SELECT|TEXTAREA|SCRIPT|STYLE|LINK|META)$/.test(n.tagName)) return;   // UI 요소가 문서에 삽입되는 것 차단(복사 중 포커스된 도구버튼 등)
      if (n.nodeType === 1 && n.tagName === "TABLE" && !n.hasAttribute("data-place")) {   // char면 objline 재래핑
        var o = document.createElement("div"); o.className = "objline"; n.setAttribute("data-place", "char"); o.appendChild(n); n = o;
      }
      page.insertBefore(n, ref); last = n;
    });
    if (last) placeCaretIn(last);
    markDirty(); recordNow();
  }
  function insertObjClip() {   // 내부 개체 클립보드 → 현재 최상위 블록 뒤 형제 삽입
    if (!window.WP._objClip || !window.WP._objClip.html) return false;
    window.WP._objClipPreferred = false;   // 소비 시점에 선호플래그 해제(메뉴 붙이기 경로 포함) → 다음 Ctrl+V가 스테일 개체를 가로채지 않음
    if (window.WP._objClip.kind === "cells" && window.WP.fillCellBlockAtCaret && window.WP.fillCellBlockAtCaret(window.WP._objClip.html)) return true;   // C: 캐럿이 표 셀 안이면 채움. 아니면 아래로 떨어져 새 표 삽입(A)
    var tmp = document.createElement("div"); tmp.innerHTML = window.WP._objClip.html;
    var nodes = Array.prototype.slice.call(tmp.childNodes);
    if (typeof window.WP._objClip.onPaste === "function") { try { window.WP._objClip.onPaste(nodes); } catch (e) {} }   // 붙이기 오프셋 등(삽입 전 = 히스토리에 반영)
    insertBlocksAsSiblings(nodes);
    return true;
  }
  function insertText(text) { document.execCommand("insertText", false, text); markDirty(); }
  function pasteExternal(html, text) {   // 시스템 클립보드 → 표 포함이면 형제 삽입, 아니면 캐럿 삽입
    if (html) {
      var frag = (EMBED && window.WP.sanitizeEmbedFrag) ? window.WP.sanitizeEmbedFrag(html) : sanitizeHTML(html);   // 임베드=load와 같은 허용목록
      if (frag.querySelector("table")) { insertBlocksAsSiblings(Array.prototype.slice.call(frag.childNodes)); return true; }
      var s = window.getSelection();
      if (s.rangeCount) {
        var r = s.getRangeAt(0); r.deleteContents();
        var docf = document.createDocumentFragment(), n; while ((n = frag.firstChild)) docf.appendChild(n);
        var lastN = docf.lastChild; r.insertNode(docf);
        if (lastN) { r.setStartAfter(lastN); r.collapse(true); s.removeAllRanges(); s.addRange(r); }
        markDirty(); recordNow(); return true;
      }
    }
    if (text) { insertText(text); return true; }
    return false;
  }
  function pasteFromSystem() {   // 메뉴/우클릭 붙이기 비동기 폴백(권한 필요할 수 있음)
    if (navigator.clipboard && navigator.clipboard.read) {
      navigator.clipboard.read().then(function (items) {
        var it = items && items[0]; if (!it) throw 0;
        var hasHtml = it.types && it.types.indexOf("text/html") >= 0;
        if (hasHtml) return it.getType("text/html").then(function (b) { return b.text(); }).then(function (h) { if (!pasteExternal(h, "")) toastMsg("붙일 내용이 없습니다"); });
        return navigator.clipboard.readText().then(function (t) { if (!pasteExternal("", t)) toastMsg("붙일 내용이 없습니다"); });
      }).catch(function () {
        if (navigator.clipboard && navigator.clipboard.readText)
          navigator.clipboard.readText().then(function (t) { if (!pasteExternal("", t)) toastMsg("붙일 내용이 없습니다"); }).catch(function () { toastMsg("붙이기 권한이 없습니다 · Ctrl+V를 쓰세요"); });
        else toastMsg("붙일 내용이 없습니다");
      });
    } else if (navigator.clipboard && navigator.clipboard.readText) {
      navigator.clipboard.readText().then(function (t) { if (!pasteExternal("", t)) toastMsg("붙일 내용이 없습니다"); }).catch(function () { toastMsg("붙이기 권한이 없습니다 · Ctrl+V를 쓰세요"); });
    } else toastMsg("붙일 내용이 없습니다 · Ctrl+V를 쓰세요");
  }
  // 물리 Ctrl+V(네이티브 paste 이벤트) — 셀 중첩 방지 re-home. 시스템 클립보드 우선, 비면 내부 개체.
  page.addEventListener("paste", function (e) {
    if (window.WP._objClip && window.WP._objClip.kind === "cells" && window.WP._lastClip !== "txt" && window.WP.caretInCell && window.WP.caretInCell()) {   // 셀블록 클립 + 캐럿이 표 셀 안 → 셀 채움(C). 단 텍스트를 나중에 복사했으면 텍스트 우선
      e.preventDefault(); window.WP._objClipPreferred = false; insertObjClip(); return;
    }
    if (window.WP._objClip && window.WP._objClipPreferred) {   // 개체 복사/오려두기 직후 = 시스템 클립보드 텍스트보다 개체 우선
      e.preventDefault(); window.WP._objClipPreferred = false; insertObjClip(); return;
    }
    var cd = e.clipboardData || window.clipboardData; if (!cd) return;
    var html = cd.getData("text/html"), text = cd.getData("text/plain");
    if (html || text) { e.preventDefault(); pasteExternal(html, text); }
    else if (WP_EMBED && window.WP._txtClip && window.WP._lastClip === "txt") {   // WPE 네이티브 pasteboard Latin-1 오해독(한글 tofu) 우회 — 내부 텍스트클립(wp-obj txtPut) 삽입. 웹은 네이티브 정상=게이트
      e.preventDefault(); pasteExternal(window.WP._txtClip.html, window.WP._txtClip.text);
    }
    else if (window.WP._objClip) { e.preventDefault(); insertObjClip(); }
  });
  // ── 내부 클립 붙여넣기 공용부(2026-07-18, WP_EMBED 전용): copy를 preventDefault하면 WPE pasteboard가 비어
  //    Ctrl+V에 paste 이벤트 자체가 안 옴 → keydown에서 직접 처리(위 paste 핸들러는 이벤트 오는 경로용 존치).
  //    웹은 네이티브 클립보드 정상 → 게이트 없이 켜면 외부앱→한워드 붙여넣기가 스테일 내부클립으로 오염(절대 금지)
  function pasteInternal() {   // 개체/텍스트 내부 클립 우선순위 삽입 — keydown·메뉴 공용. 삽입했으면 true
    if (window.WP._objClip && window.WP._objClip.kind === "cells" && window.WP._lastClip !== "txt" && window.WP.caretInCell && window.WP.caretInCell()) { window.WP._objClipPreferred = false; return insertObjClip(); }
    if (window.WP._objClip && window.WP._objClipPreferred) { window.WP._objClipPreferred = false; return insertObjClip(); }
    if (window.WP._txtClip && window.WP._lastClip === "txt") return pasteExternal(window.WP._txtClip.html, window.WP._txtClip.text);
    if (window.WP._objClip) return insertObjClip();
    return false;
  }
  function pasteIntoInput(a) {   // INPUT/TEXTAREA(문서명·찾기 등)에 내부 텍스트클립 삽입
    var t = window.WP._txtClip; if (!t || !t.text || window.WP._lastClip !== "txt") return false;
    var st = (typeof a.selectionStart === "number") ? a.selectionStart : a.value.length, en = (typeof a.selectionEnd === "number") ? a.selectionEnd : st;
    a.value = a.value.slice(0, st) + t.text + a.value.slice(en);
    a.selectionStart = a.selectionEnd = st + t.text.length;
    try { a.dispatchEvent(new Event("input", { bubbles: true })); } catch (x) {}
    return true;
  }
  if (WP_EMBED) document.addEventListener("keydown", function (e) {   // 물리 Ctrl+V(게임패드 A 포함) 최우선 가로채기
    if (!(e.ctrlKey && !e.altKey && !e.shiftKey && (e.key === "v" || e.key === "V"))) return;
    var a = document.activeElement;
    if (a && (a.tagName === "INPUT" || a.tagName === "TEXTAREA")) {
      if (pasteIntoInput(a)) { e.preventDefault(); e.stopPropagation(); }
      return;
    }
    if (pasteInternal()) { e.preventDefault(); e.stopPropagation(); }
  }, true);
  if (WP_EMBED) document.addEventListener("paste", function (e) {   // paste 이벤트가 오는 경로(메뉴 등)용 입력칸 처리
    var a = e.target;
    if (!a || (a.tagName !== "INPUT" && a.tagName !== "TEXTAREA")) return;
    if (pasteIntoInput(a)) e.preventDefault();
  }, true);

  // 종료(2026-07-18 재설계): 외부 swaynag 브리지(wp-quit-req→host quitnag→quitpoll) 폐기 —
  //   워처 없는 실행경로(debug 직기동 등)선 확인창이 영영 안 떠 "종료 확인 중…" 고착.
  //   인앱 모달(#m-quit)로 자체완결: 클린=즉시 종료, 더티=[저장 후 끝][저장 안 함][취소].
  //   창닫기(xdg close)도 임베더 부활루프가 WP.askQuit() 호출로 같은 흐름을 탐.
  function quitClose() {
    status("종료 중…");
    api("/api/quitclose").catch(function () { try { window.close(); } catch (e) {} });   // 임베더=SIGTERM. 실패시 웹판 폴백
  }
  function askQuit() {
    if (!dirty) { quitClose(); return; }
    openModal("m-quit");
  }
  (function () {   // 모달 버튼 배선(pointerup — 게임패드 드리프트 면역 UI 규약)
    var up = function (id, fn) { var el = $(id); if (el) el.addEventListener("pointerup", fn); };
    up("quit-save", function () {
      closeModal($("m-quit")); status("저장 중…");   // ⚠️closeModal은 요소 인자(openModal만 id 문자열)
      save(true).then(function () { if (!dirty) quitClose(); else { status(""); toastMsg("저장 실패 — 종료 취소"); } });
    });
    up("quit-discard", function () { closeModal($("m-quit")); quitClose(); });
    up("quit-cancel", function () { closeModal($("m-quit")); status(""); });
  })();

  var ACT = {
    "new": function () { confirmUnsaved(function () { docname.value = "제목 없음"; page.innerHTML = "<p><br></p>"; dirty = false; status("새 문서"); page.focus(); histReset(); }); },
    open: function () { $("m-open-title").textContent = "불러오기"; openModal("m-open"); api("/api/list").then(renderList).catch(apiFail("목록 불러오기")); },
    save: function () { save(true); }, saveas: function () { docname.focus(); docname.select(); toastMsg("새 이름 입력 후 Alt+S"); },
    quit: function () {   // #29 끝: 인앱 확인(2026-07-18 재설계 — 클린=즉시, 더티=#m-quit 모달)
      if (window.WP_PUBLIC) { if (dirty) save(true); toastMsg("저장 후 닫으세요"); return; }
      askQuit();
    },
    undo: function () { histUndo(); }, redo: function () { histRedo(); },
    cut: function () { if (window.WP.selectedTable && window.WP.selectedTable() && window.WP.TT && window.WP.TT.cutTable) { window.WP.TT.cutTable(); return; } exec("cut"); },
    copy: function () { if (window.WP.selectedTable && window.WP.selectedTable() && window.WP.TT && window.WP.TT.copyTable) { window.WP.TT.copyTable(); return; } document.execCommand("copy"); page.focus(); },   // 표 전체선택=표 복사, 그 외 문서 무변경(P2-4)
    paste: function () { if (insertObjClip()) return; pasteFromSystem(); },   // 내부 개체 우선, 없으면 시스템 클립보드(2026-07-13 P0: execCommand paste는 cog no-op)
    histpw: function () { WP_setHistPw(); },   // 도구>편집 역사 암호 설정/변경
    selectall: function () { document.execCommand("selectAll"); page.focus(); },   // 〃

    fullscreen: function () { document.body.classList.toggle("zen"); toastMsg("보기 전환"); },
    outline: function () { page.classList.toggle("outline"); },
    hr: function () {   // 가로줄=최상위 블록 <hr class="wp-hr">(클릭 선택·Delete 삭제 가능한 개체) + 가장자리 캐럿 확보
      var hr = document.createElement("hr"); hr.className = "wp-hr";
      insertBlocksAsSiblings([hr]); normalizeEdges();
    },
  };

  // ── 대화상자 공통 ──
  // ⚠️열자마자 닫힘 버그(2026-07-06): 메뉴 항목 pointerup→openModal로 대화상자가 손가락 위치에
  //   나타나면, 같은 제스처의 잔여/후속 pointerup이 새 backdrop 위에서 발생해 즉시 닫혔음(재선택도
  //   반복→영영 안 뜸). openModal 시각을 기록하고 backdrop 닫기가 그 직후(GUARD_MS) pointerup은
  //   무시. backdrop 닫기가 pointerup인 전 대화상자 일괄 적용.
  var _modalOpenAt = 0, MODAL_GUARD_MS = 350;
  document.querySelectorAll(".modal .dlg").forEach(function (d) {   // 모달 접근성(HEAD 복원)
    d.tabIndex = -1; d.setAttribute("role", "dialog"); d.setAttribute("aria-modal", "true");
  });
  function openModal(id) {
    var m = $(id); m.classList.remove("hidden"); _modalOpenAt = Date.now();
    var dlg = m.querySelector(".dlg");   // 대화상자 컨테이너에 포커스(입력칸 자동포커스는 모바일 OSK를 띄워 회피)
    if (dlg) setTimeout(function () { dlg.focus(); }, 0);
  }
  function closeModal(el) { el.classList.add("hidden"); if (el.contains(document.activeElement)) page.focus(); }
  document.querySelectorAll(".modal").forEach(function (m) {
    m.addEventListener("pointerup", function (e) {
      if (e.target === m || e.target.hasAttribute("data-close")) {
        if (Date.now() - _modalOpenAt < MODAL_GUARD_MS) return;   // 열림 직후 잔여 pointerup=무시
        closeModal(m); return;
      }
      var b = e.target.closest("[data-act]"); if (b) { e.stopPropagation(); dispatch(b.getAttribute("data-act")); }
    });
  });

  // ── 저장/불러오기 ──
  function api(path, body) {
    if (EMBED) return Promise.reject(new Error("임베드 모드: 서버 API 사용 안 함"));   // 파일 목록/삭제/저장 등 엔드포인트 호출 원천 차단
    var o = { method: body ? "POST" : "GET" };
    if (body) { o.headers = { "Content-Type": "application/json" }; o.body = JSON.stringify(body); }
    return fetch(path, o).then(function (r) {
      return r.text().then(function (t) {
        var j = null; if (t) { try { j = JSON.parse(t); } catch (e) { j = null; } }
        if (!r.ok) {   // HTTP 오류(500/502/503…)를 삼키지 않고 예외로 — 호출부 .catch가 서버 메시지(마스킹됨)를 표시
          var m = (j && j.err) ? j.err : ("서버 오류 (" + r.status + ")");
          var err = new Error(m); err.status = r.status; throw err;
        }
        return j == null ? {} : j;
      });
    });
  }
  // 공통 실패 핸들러 — 무반응 UI 금지 원칙: 모든 api 실패에 토스트+상태줄 시각 피드백(상세는 콘솔)
  function apiFail(label) {
    return function (e) {
      var m = (e && e.message) ? e.message : "연결 오류";
      status(label + " 실패: " + m); toastMsg(label + " 실패 — " + m);
      try { console.error("[api]", label, e); } catch (x) {}
    };
  }
  function status(t) { statusEl.textContent = t || ""; }
  function toastMsg(t) { toast.textContent = t; toast.classList.remove("hidden"); clearTimeout(toast._t); toast._t = setTimeout(function () { toast.classList.add("hidden"); }, 1800); }
  function markDirty() { if (window.WP && window.WP.onDirty) window.WP.onDirty(); dirty = true; status("편집 중 · 저장 안 됨"); updatePh(); scheduleRecord(); }   // 자동저장 제거(2026-07-16): 저장=Ctrl+S 명시. updatePh: 프로그램적 편집(표 삭제 등)도 플레이스홀더 동기
  // 마크업 정규화(#9, HEAD fd4b780 적응 복원): 무속성 span 언랩 + 인접 동일서식 순수 span 병합 + 텍스트 노드 합치기
  //   → 글자별 span 없이 run(연속 동일서식) 모델을 정규형으로 유지. 저장/출력/undo 경계서 1회.
  //   적응: class 등 속성 가진 span(미주 .fn-no 등 클로버 후 신작)은 언랩·병합 대상에서 제외(원본은 style 유무만 봤음)
  function normalizeRuns(root) {
    var changed = true, guard = 0;
    function plain(sp) { return sp.attributes.length === 1 && sp.getAttribute("style"); }   // style만 가진 순수 서식 run
    while (changed && guard++ < 60) {
      changed = false;
      var spans = root.querySelectorAll("span");
      for (var i = 0; i < spans.length; i++) {
        var sp = spans[i]; if (!sp.parentNode) continue;
        if (sp.querySelector("img,br,table,svg") || sp.classList.contains("wp-obj")) continue;   // 인라인 개체(도형 wp-obj 포함) 든 span은 손대지 않음(2026-07-18 svg·wp-obj 추가)
        if (sp.getAttribute("style") === "") sp.removeAttribute("style");   // 비워진 style="" 잔재는 무속성 취급
        if (!sp.attributes.length) {   // 속성 전무 span은 언랩(내용만 남김)
          while (sp.firstChild) sp.parentNode.insertBefore(sp.firstChild, sp);
          sp.parentNode.removeChild(sp); changed = true; continue;
        }
        if (!plain(sp)) continue;
        var nx = sp.nextSibling;   // 바로 다음 형제도 같은 스타일 순수 span이면 병합(사이에 텍스트 있으면 nextSibling이 span이 아니라 병합 안 됨)
        if (nx && nx.nodeType === 1 && nx.tagName === "SPAN" && plain(nx) && nx.getAttribute("style") === sp.getAttribute("style") && !nx.querySelector("img,br,table")) {
          while (nx.firstChild) sp.appendChild(nx.firstChild);
          nx.parentNode.removeChild(nx); changed = true;
        }
      }
    }
    root.normalize();
  }
  // ── 웹 미디어 저장소(P10, 2026-07-19): IndexedDB wpMedia(키=파일명, 값=Blob) — 서버 무저장 철학 유지, 초안 저장소와 별개 ──
  //   마크업 src=파일명 단독(이식성) / 재생은 해석기: ①IDB Blob→objectURL ②상대 media/파일명 ③상대 파일명(②③=HEAD 존재확인 — 정적 폴더로 내려받아 열 때 재생)
  var MDB = null, _mediaNames = {};   // _mediaNames: blobURL→파일명(저장 정규화·이름변경 참조갱신용)
  function mdb(cb) {
    if (MDB) return cb(MDB);
    try {
      var rq = indexedDB.open("wpMediaDB", 1);
      rq.onupgradeneeded = function () { rq.result.createObjectStore("wpMedia"); };
      rq.onsuccess = function () { MDB = rq.result; cb(MDB); };
      rq.onerror = function () { cb(null); };
    } catch (e) { cb(null); }
  }
  function mediaStore(name, blob, cb) {
    mdb(function (db) {
      if (!db) return cb(false);
      var tx = db.transaction("wpMedia", "readwrite");
      tx.objectStore("wpMedia").put(blob, name);
      tx.oncomplete = function () { cb(true); }; tx.onerror = function () { cb(false); };
    });
  }
  function mediaGet(name, cb) {
    mdb(function (db) {
      if (!db) return cb(null);
      var rq = db.transaction("wpMedia").objectStore("wpMedia").get(name);
      rq.onsuccess = function () { cb(rq.result || null); }; rq.onerror = function () { cb(null); };
    });
  }
  function mediaRename(oldName, newName, cb) {
    mediaGet(oldName, function (b) {
      if (!b) return cb(false, "원본 없음");
      mediaGet(newName, function (dup) {
        if (dup) return cb(false, "이미 있는 이름");
        mediaStore(newName, b, function (okp) {
          if (!okp) return cb(false, "저장 실패");
          mdb(function (db) {
            var tx = db.transaction("wpMedia", "readwrite"); tx.objectStore("wpMedia")["delete"](oldName);
            tx.oncomplete = function () { cb(true); }; tx.onerror = function () { cb(true); };   // 옛 키 삭제 실패해도 rename 자체는 성공
          });
        });
      });
    });
  }
  function mediaResolve(name, cb) {   // 파일명→재생 URL(비동기). 실패 시 cb(null)
    mediaGet(name, function (b) {
      if (b) { var u = URL.createObjectURL(b); _mediaNames[u] = name; return cb(u); }
      var rel = "media/" + encodeURIComponent(name);
      fetch(rel, { method: "HEAD" }).then(function (r) {
        if (r.ok) return cb(rel);
        return fetch(encodeURIComponent(name), { method: "HEAD" }).then(function (r2) { cb(r2.ok ? encodeURIComponent(name) : null); });
      }).catch(function () { cb(null); });
    });
  }
  // 저장/출력용 HTML — 페이지네이션 스페이서([data-pgfill])·수동나누기 채움높이를 제거한 깨끗한 본문
  function cleanedClone() {
    var c = page.cloneNode(true);
    c.querySelectorAll("button,input,select,textarea,script,style").forEach(function (el) { if (el.id !== "wpmeta") el.parentNode && el.parentNode.removeChild(el); });   // UI 요소는 저장/출력에서 제외
    c.querySelectorAll("[data-pgfill]").forEach(function (s) { s.parentNode.removeChild(s); });
    c.querySelectorAll(".pgbreak").forEach(function (b) { b.style.height = ""; if (!b.getAttribute("style")) b.removeAttribute("style"); });
    // 선택 표시(셀 블록/개체 선택/리사이즈)는 화면 전용 — 저장·undo 본문에 남기지 않음
    c.querySelectorAll(".cb-sel,.obj-sel,.resizing").forEach(function (x) {
      x.classList.remove("cb-sel", "obj-sel", "resizing");
      if (!x.getAttribute("class")) x.removeAttribute("class");
    });
    // 해제 시 라이브 DOM에 남는 class="" 잔재 전역 정화 — 방치 시 undo diff 스퓨리어스 엔트리(첫 Ctrl+Z 무효)+저장 오염(감사2차 P1-8)
    c.querySelectorAll('[class=""]').forEach(function (x) { x.removeAttribute("class"); });
    // 대기 서식 span의 제로폭(U+FEFF) 자리표시 제거 → 저장/출력/undo 본문엔 안 남김. 입력 안 돼 빈 span은 통째 제거 (#9 복원)
    var tw = document.createTreeWalker(c, NodeFilter.SHOW_TEXT), tn, zap = [];
    while ((tn = tw.nextNode())) if (tn.nodeValue.indexOf("\uFEFF") >= 0) zap.push(tn);
    zap.forEach(function (t) { t.nodeValue = t.nodeValue.replace(/\uFEFF/g, ""); });
    c.querySelectorAll("span").forEach(function (sp) { if (!sp.textContent && !sp.querySelector("img,br,svg") && !sp.classList.contains("wp-obj") && !sp.hasAttribute("data-field")) sp.parentNode.removeChild(sp); });   // data-field=결재 양식 빈 칸(임베드) 보존   // ⚠️wp-obj(인라인 도형=텍스트 없는 span) 예외 — 종전엔 저장/export에서 도형이 통째 유실(2026-07-18)
    c.querySelectorAll("video,audio").forEach(function (m) {   // 저장=파일명 단독(html과 동일 경로 모델, 2026-07-19 P10) — blob:(IDB 재생)·상대 media/·서버 /media/ 전부 파일명으로 정규화(이식성)
      var s = m.getAttribute("src") || "", nm = null, mk;
      if (s.slice(0, 5) === "blob:") nm = _mediaNames[s] || null;
      else if ((mk = s.match(/^(?:https?:\/\/[^/]+)?\/?media\/(.+)$/))) { try { nm = decodeURIComponent(mk[1]); } catch (e) { nm = mk[1]; } }
      if (nm) m.setAttribute("src", nm);
    });
    normalizeRuns(c);   // 인접 동일서식 run 병합·불필요 span 언랩(정규형 마크업)
    return c;
  }
  function cleanedHTML() { return cleanedClone().innerHTML; }
  // cleanedHTML을 최상위 노드 단위 직렬화 조각으로 — join("")이 cleanedHTML()과 byte 동일(undo diff의 블록 단위).
  //   떠도는 텍스트/주석 노드도 브라우저 직렬화 그대로 제 조각이 됨(DOM 정규화 불필요 → 캐럿 무영향)
  var _serTmp = document.createElement("div");
  function cleanedBlocks() {
    var c = cleanedClone(), out = [], n;
    // 파생(페이지)데이타는 undo 기준에서 정규화 → 변화가 undo diff에 안 잡힘(저장용 cleanedClone은 원본 유지).
    c.querySelectorAll("sup.fn, .fn-no").forEach(function (x) { x.textContent = ""; });                          // 각주 번호만 파생(주석 본문은 콘텐츠라 유지). div.toc=일반 텍스트로 저장/undo 포함(목차 폐지, 감독 지시 07-17)
    for (n = c.firstChild; n; n = n.nextSibling) {
      if (n.nodeType === 1) out.push(n.outerHTML);
      else { _serTmp.textContent = ""; _serTmp.appendChild(n.cloneNode(true)); out.push(_serTmp.innerHTML); }
    }
    return out;
  }
  function save(announce) {
    if (EMBED) return Promise.resolve();   // 임베드: 저장=부모가 {type:'get'}으로 가져감(서버 저장 없음)
    if (window.WP_PUBLIC) {   // 공개모드: 서버 저장 대신 브라우저 초안(IndexedDB) 보관. dirty는 다운로드 전까지 유지(미저장 경고용)
      if (window.WP && window.WP.draftPut) window.WP.draftPut();
      return Promise.resolve();
    }
    if (saving) { pendingSave = true; return Promise.resolve(); }   // 중복저장 방지(진행중이면 끝나고 1회 더)
    saving = true;
    var name = (docname.value || "제목 없음").trim() || "제목 없음";
    return api("/api/save", { name: name, html: cleanedHTML() }).then(function (r) {
      if (r && r.ok) { dirty = false; if (docname.value.trim() === name) docname.value = r.name; var d = new Date(); status("저장됨 " + ("0"+d.getHours()).slice(-2) + ":" + ("0"+d.getMinutes()).slice(-2)); if (announce) toastMsg("저장 완료"); persistHist(r.name || name); }   // 진행중 저장 응답이 사용자가 방금 바꾼 문서명을 롤백하지 않게(같을 때만 서버 정규화명 반영, 2026-07-06)
      else status("저장 실패");
    }).catch(function (e) { var m = (e && e.message) ? e.message : "서버?"; status("저장 오류: " + m); toastMsg("저장 실패 — " + m); }).then(function () { saving = false; if (pendingSave) { pendingSave = false; save(false); } });
  }
  // 자동저장(2026-07-16 재설계): 파일명 옆 "자동" 체크박스 옵트인. 켜면 30초마다 dirty면 저장.
  //   1.5초 디바운스 자동저장은 폐기(끄면 Ctrl+S 수동). localStorage로 선호 기억.
  //   끈 상태(또는 30초 사이 편집분)에서 종료/불러오기/새문서는 confirmUnsaved로 안내·확인.
  var _autosaveTimer = null;
  function autosaveApply(on) {
    clearInterval(_autosaveTimer); _autosaveTimer = null;
    if (on) _autosaveTimer = setInterval(function () { if (dirty && !saving) save(false); }, 30000);
    try { localStorage.setItem("wp-autosave", on ? "1" : "0"); } catch (e) {}
  }
  (function initAutosave() {
    var cb = $("autosave"); if (!cb || EMBED) return;   // 임베드: 자동저장 없음(선호값도 건드리지 않음)
    var on = false; try { on = !EMBED && localStorage.getItem("wp-autosave") === "1"; } catch (e) {}
    cb.checked = on; autosaveApply(on);
    cb.addEventListener("change", function () { autosaveApply(cb.checked); if (cb.checked && dirty) save(true); page.focus(); });
  })();
  function renderList(items) {
    var dl = $("doclist"); dl.innerHTML = "";
    if (!items || !items.length) { dl.innerHTML = '<div class="di"><span class="meta">저장된 문서가 없습니다</span></div>'; return; }
    items.forEach(function (it) {
      var row = document.createElement("div"); row.className = "di";
      var d = new Date(it.mtime * 1000), when = (d.getMonth()+1) + "/" + d.getDate() + " " + ("0"+d.getHours()).slice(-2) + ":" + ("0"+d.getMinutes()).slice(-2);
      row.innerHTML = '<span class="nm">' + esc(it.name) + '</span><span class="meta">' + when + '</span><button class="del">삭제</button>';
      row.querySelector(".nm").addEventListener("pointerup", function () { openDoc(it.name); });
      row.querySelector(".del").addEventListener("pointerup", function (e) { e.stopPropagation(); api("/api/delete", { name: it.name }).then(function () { return api("/api/list"); }).then(renderList).catch(apiFail("삭제")); });
      dl.appendChild(row);
    });
  }
  // 미저장 변경 확인(2026-07-16): 자동저장 없앤 대신, 파괴적 동작(불러오기/새문서) 전 안내·확인.
  //   dirty 아니면 즉시 진행. [저장 후 진행][저장 안 함][취소] — save는 성공(!dirty)해야 진행.
  function confirmUnsaved(onProceed) {
    if (!dirty) { onProceed(); return; }
    var ex = $("m-unsaved"); if (ex) ex.parentNode.removeChild(ex);
    var ov = document.createElement("div"); ov.id = "m-unsaved";
    ov.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:99999;display:flex;align-items:center;justify-content:center";
    var box = document.createElement("div");
    box.style.cssText = "background:#fff;color:#111;border-radius:8px;padding:16px 18px;max-width:88%;box-shadow:0 6px 24px rgba(0,0,0,.45);font-size:13px";
    box.innerHTML = '<div style="margin-bottom:12px;line-height:1.6">저장하지 않은 변경이 있습니다.<br>저장 없이 진행하면 변경 내용이 사라집니다.</div>';
    var row = document.createElement("div"); row.style.cssText = "display:flex;gap:8px;justify-content:flex-end;flex-wrap:wrap";
    function mk(t, d) { var b = document.createElement("button"); b.textContent = t; b.style.cssText = "padding:7px 13px;border-radius:6px;font-size:13px;border:1px solid " + (d ? "#e0503a;background:#e0503a;color:#fff" : "#bbb;background:#f2f2f2;color:#111"); return b; }
    var bSave = mk("저장 후 진행"), bDisc = mk("저장 안 함", 1), bCancel = mk("취소");
    function close() { if (ov.parentNode) ov.parentNode.removeChild(ov); }
    bCancel.addEventListener("pointerup", function () { close(); page.focus(); });
    bDisc.addEventListener("pointerup", function () { close(); onProceed(); });
    bSave.addEventListener("pointerup", function () { save(true).then(function () { if (!dirty) { close(); onProceed(); } else toastMsg("저장 실패 — 진행 취소"); }); });
    row.appendChild(bSave); row.appendChild(bDisc); row.appendChild(bCancel);
    box.appendChild(row); ov.appendChild(box); document.body.appendChild(ov);
  }
  function openDoc(name) { confirmUnsaved(function () { doOpenDoc(name); }); }
  function doOpenDoc(name) {
    api("/api/load?name=" + encodeURIComponent(name)).then(function (r) {
      docname.value = r.name; page.innerHTML = r.html && r.html.trim() ? r.html : "<p><br></p>";
      page.querySelectorAll(".cb-sel,.obj-sel,.resizing").forEach(function (x) { x.classList.remove("cb-sel", "obj-sel", "resizing"); });   // 과거 저장분 청소
      normalizeEdges();   // 표로 시작/끝나는 과거 문서 교정(가장자리 캐럿 확보)
      dirty = false; closeModal($("m-open")); status("불러옴: " + r.name); page.focus(); histReset(); restoreHistFlow(r.name);
    }).catch(apiFail("불러오기"));
  }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;" }[c]; }); }

  // #31 파일 자동열기: /?open=<문서명|documents 상대경로> — 기기 연결프로그램 라우터(fm-open) 연동.
  //   전 모듈 등록 후(load) 불러오기와 동일 경로로 오픈. 미존재=토스트+빈 문서(보수). 공개판=쿼리 무시(로컬 전용).
  window.addEventListener("load", function () {
    if (window.WP_PUBLIC || EMBED) return;
    var q = new URLSearchParams(location.search).get("open"); if (!q) return;
    var name = q.split("/").pop();   // documents/ 접두 등 경로는 파일명만(서버 safe_name이 어차피 "/" 제거)
    if (/\.(hwpx|hwp)$/i.test(name)) {
      api("/api/list/hwpx").then(function (items) {
        if ((items || []).some(function (it) { return it.name === name; })) WP.importHwpx(name);
        else toastMsg("문서 없음: " + name);
      }).catch(apiFail("자동 열기"));
      return;
    }
    name = name.replace(/\.html$/i, "");
    api("/api/list").then(function (items) {
      if ((items || []).some(function (it) { return it.name === name; })) openDoc(name);
      else toastMsg("문서 없음: " + name);
    }).catch(apiFail("자동 열기"));
  });

  // ── 플레이스홀더 + 입력 ──
  // 본문 리셋 시 숨김 문서메타(#wpmeta 머리말/쪽번호)는 보존 — 빈 문서에서 설정 직후 무음 유실 방지(#21에서 발견)
  function resetPageKeepMeta() {
    var metaEl = page.querySelector("#wpmeta");
    page.innerHTML = "<p><br></p>";
    if (metaEl) page.appendChild(metaEl);
  }
  // 표/개체가 문서 첫·끝 블록이면 그 앞·뒤에 캐럿 놓을 위치가 없음(contenteditable 한계: leading 표 앞
  //   커서 불가 → 표를 문자처럼 아래로 못 밈). 가장자리를 항상 빈 문단으로 보장해 해소(표삽입·로드 후 호출).
  function _needsEdgePara(el) {
    return el && (el.tagName === "TABLE" || el.tagName === "HR" || (el.classList && (el.classList.contains("objline") || el.classList.contains("wp-obj"))));
  }
  function _newPara() { var p = document.createElement("p"); p.innerHTML = "<br>"; return p; }
  function normalizeEdges() {
    page.querySelectorAll("button,input,select,textarea,script,style").forEach(function (el) { if (el.id !== "wpmeta") el.parentNode && el.parentNode.removeChild(el); });   // 문서에 섞인 UI 요소 제거(복사 중 유입된 도구버튼 등)
    if (!page.firstElementChild || _needsEdgePara(page.firstElementChild))
      page.insertBefore(_newPara(), page.firstChild);
    var meta = page.querySelector("#wpmeta");
    var last = meta ? meta.previousElementSibling : page.lastElementChild;
    if (_needsEdgePara(last)) page.insertBefore(_newPara(), meta || null);
  }
  // ── 플레이스홀더(#9, HEAD fd4b780 택일 복원): CSS 오버레이(.ph+::before) — 실제 텍스트가 아니라 글자수/저장/찾기에 안 섞임 ──
  function updatePh() {
    page.classList.toggle("ph", !page.textContent.trim() && !page.querySelector("img,table,.wp-obj"));
  }
  page.addEventListener("input", markDirty);   // markDirty가 updatePh 포함(사용자 입력·프로그램 편집 단일 경로)

  // ── 단축키(HWP) + Ctrl+N 코드 ──
  var chord = null, chordTimer = null;   // chord = 접두 프리픽스("n"=Ctrl+N 입력, "m"=Ctrl+M 글자색)
  function comboStr(e) {
    var p = []; if (e.ctrlKey) p.push("ctrl"); if (e.altKey) p.push("alt"); if (e.shiftKey) p.push("shift");
    var k = e.key; if (/^F\d+$/.test(k)) k = k.toLowerCase(); else if (k.length === 1) k = k.toLowerCase();
    p.push(k); return p.join("+");
  }
  document.addEventListener("keydown", function (e) {
    if (e.isComposing || e.keyCode === 229) return;   // 한글 IME 조합 중엔 단축키 무시
    if (e.key === "Escape") {   // 우선순위: 열린 모달(마지막) > 드롭다운 > 전체화면 — 한 번에 하나씩
      var open = document.querySelectorAll(".modal:not(.hidden)");
      if (open.length) { closeModal(open[open.length - 1]); return; }
      if (openIdx >= 0) { closeDropdown(); return; }
      if (document.body.classList.contains("zen")) {   // 미리보기면 exitPreview 경유(편집 잠금 해제·오버레이 제거)
        if (window.WP.previewActive && window.WP.exitPreview) window.WP.exitPreview(); else document.body.classList.remove("zen");
        return;
      }
      return;
    }
    if (window.WP.previewActive) {   // 인쇄 미리보기 중: Enter 복귀, 그 외 단축키 차단(모달 오픈 방지)
      if (e.key === "Enter" && !document.querySelector(".modal:not(.hidden)")) { e.preventDefault(); window.WP.exitPreview(); }
      return;
    }
    if (e.key === "F10" && !e.ctrlKey && !e.altKey && !e.shiftKey) { e.preventDefault(); MENUS[0]._el.focus(); return; }   // 키보드/게임패드로 메뉴 진입(Ctrl+F10=특수문자와 구분)
    var modalEl = e.target && e.target.closest && e.target.closest(".modal");
    if (modalEl) {
      if (e.key === "Tab") {                       // 포커스 트랩: 대화상자 안에서 순환
        var f = Array.prototype.filter.call(
          modalEl.querySelectorAll("button,input,select,textarea"),
          function (x) { return !x.disabled && x.offsetParent !== null; });
        if (f.length) {
          var fi = f.indexOf(document.activeElement);
          var ni = e.shiftKey ? (fi <= 0 ? f.length - 1 : fi - 1) : (fi >= f.length - 1 ? 0 : fi + 1);
          e.preventDefault(); f[ni].focus();
        }
        return;
      }
      if (e.key === "Enter" && e.target.tagName === "INPUT" && e.target.type !== "checkbox") {
        var okb = modalEl.querySelector(".dlg-f .ok[data-act]");
        var act = (modalEl.id === "m-find") ? (e.shiftKey ? "findPrev" : "findNext")   // 찾기: Enter=다음, Shift+Enter=이전
                                            : (okb ? okb.getAttribute("data-act") : null);
        if (act) { e.preventDefault(); dispatch(act); }
        return;
      }
      return;
    }
    if (e.target && e.target !== page && /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName || "")) return;   // 문서명 등 입력칸에선 서식 핫키 비활성(오적용 방지)
    if (chord) {
      e.preventDefault(); clearTimeout(chordTimer); var pre = chord; chord = null;
      var k = e.key.toLowerCase();
      if (pre === "n") { if (k === "t") dispatch("table"); else if (k === "i") dispatch("image"); else if (!EMBED && k === "h") dispatch("header"); else if (!EMBED && k === "p") dispatch("pagenum"); }
      else if (pre === "m") { if (k === "k") dispatch("colorBlack"); else if (k === "r") dispatch("colorRed"); else if (k === "b") dispatch("colorBlue"); }
      return;
    }
    if (e.key === "Tab" && page.contains(window.getSelection().anchorNode)) {   // 탭=표안 셀이동 / 목록 레벨 / 문단 들여쓰기
      e.preventDefault();
      if (window.WP && window.WP.cellNav && window.WP.cellNav(e.shiftKey)) { markDirty(); return; }   // 표 안=셀 이동(HWP식)
      var n = window.getSelection().anchorNode; if (n && n.nodeType === 3) n = n.parentNode;
      if (n && n.closest && n.closest("li")) document.execCommand(e.shiftKey ? "outdent" : "indent");
      else dispatch(e.shiftKey ? "indentLess" : "indentMore");
      markDirty(); return;
    }
    var combo = comboStr(e);
    if (combo === "ctrl+n") { e.preventDefault(); chord = "n"; toastMsg(EMBED ? "표 T · 그림 I" : "표 T · 그림 I · 머리말 H · 쪽번호 P"); chordTimer = setTimeout(function () { chord = null; }, 2000); return; }
    if (combo === "ctrl+m") { e.preventDefault(); chord = "m"; toastMsg("글자색 — 검정 K · 빨강 R · 파랑 B"); chordTimer = setTimeout(function () { chord = null; }, 2000); return; }
    if (combo === "f5" || combo === "f7" || combo === "f8") {   // 표 안: 표 모듈이 F5(셀블록)·F7(칸)·F8(줄) 처리 → 편집용지(F7)/맞춤법(F8) 양보
      var an = window.getSelection().anchorNode, en = an && an.nodeType === 3 ? an.parentNode : an;
      if (en && en.closest && en.closest("td,th")) return;
    }
    var act = HOT[combo]; if (act) { e.preventDefault(); dispatch(act); }
  });

  // ── 공유 네임스페이스(모듈·디버그 브리지) ──
  window.WP = {
    page: page, docname: docname, embed: EMBED,
    $: $, exec: exec, dispatch: dispatch, ACT: ACT,
    register: function (o) { for (var k in o) ACT[k] = o[k]; },
    HOT: HOT,   // 단축키 테이블(디버그/전수 점검용)
    openModal: openModal, closeModal: closeModal,
    toast: toastMsg, status: status, markDirty: markDirty, clean: function () { dirty = false; },
    PAGEBG_IMG: true,   // 쪽 배경그림 활성(2026-07-18 사용자 지시 — HWPX로는 미반출, 편집기/HTML 전용)
    mediaStore: mediaStore, mediaGet: mediaGet, mediaRename: mediaRename, mediaResolve: mediaResolve, _mediaNames: _mediaNames,   // 웹 미디어 IDB(P10)
    resolveMediaSrcs: function () {   // 로드 훅: 파일명 단독 참조 → 재생 URL(마크업 이식성 유지, 2026-07-19. 기기=임베더 mediaURL / 웹=IDB→media/→동일폴더 해석)
      Array.prototype.forEach.call(page.querySelectorAll("video,audio"), function (m) {
        var s = m.getAttribute("src") || "";
        if (!s || s.indexOf(":") >= 0 || s.indexOf("/") >= 0) return;   // 파일명 단독만
        mediaResolve(s, function (u) { if (u && u !== s) { m.setAttribute("src", u); try { m.load(); } catch (e) {} } });
      });
    },
    api: api, apiFail: apiFail, esc: esc, openDoc: openDoc, renderList: renderList,
    saveRange: saveRange, restoreRange: restoreRange,
    histReset: histReset, histRecord: recordNow,   // 불러오기/대량삽입 후 모듈이 호출
    // P13 공개모드 역사보존(wp-public A=IDB·C=동봉)용 브리지 — 암호는 세션 메모리(_histPw)만
    histPwGet: function () { return _histPw; }, histPwSet: function (pw) { if (pw) _histPw = pw; },
    histPwModal: histPwModal, histShot: function () { return { hist: hist, hi: hi }; }, adoptHistData: adoptHist,
    cleanedHTML: cleanedHTML,                       // 저장/출력용(페이지네이션 스페이서 제거)
    resetPageKeepMeta: resetPageKeepMeta,           // 본문 리셋(#wpmeta 보존) — wp-table 빈문서 가드 재사용
    normalizeEdges: normalizeEdges,                 // 표/개체가 첫·끝 블록이면 가장자리 빈문단 보장(캐럿 확보)
    askQuit: askQuit, isDirty: function () { return dirty; },   // 임베더 창닫기(xdg close) 부활루프가 호출
    insertBlocks: insertBlocksAsSiblings,           // 개체(도형 등)를 #page 직속 형제 블록으로 삽입(wp-obj가 사용)
    TT: {},   // 표 모듈이 채움
    save: save, snap: function () {   // histBytes = 히스토리 실사용량(UTF-16 근사, 디버그/벤치용)
      var hb = 0, i, e, k;
      for (i = 0; i < hist.length; i++) {
        e = hist[i];
        if (e.blocks) { for (k = 0; k < e.blocks.length; k++) hb += e.blocks[k].length; }
        else { for (k = 0; k < e.oldB.length; k++) hb += e.oldB[k].length; for (k = 0; k < e.newB.length; k++) hb += e.newB[k].length; }
      }
      return { docname: docname.value, dirty: dirty, htmlLen: page.innerHTML.length, hist: hist.length, hi: hi, histBytes: hb * 2 };
    }
  };
  histReset();   // 초기 상태를 히스토리 시작점으로
  status("준비됨 · 메뉴를 누르거나 단축키 사용 (글자 모양 Alt+L)");
})();
