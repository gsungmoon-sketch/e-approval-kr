// 한워드 — 찾기·바꾸기(정규식/대소문자/낱말단위) + 맞춤법(규칙) + 글자수.
(function () {
  "use strict";
  var WP = window.WP, $ = WP.$, page = WP.page, markDirty = WP.markDirty, toastMsg = WP.toast,
      openModal = WP.openModal, closeModal = WP.closeModal, esc = WP.esc;
  var _matches = [], _mi = -1, _findKey = "";

  function buildRegex(global) {
    var q = $("f-q").value; if (!q) { $("f-count").classList.remove("bad"); return null; }
    var flags = (global ? "g" : "") + ($("f-case").checked ? "" : "i");
    var pat = $("f-regex").checked ? q : q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    // 낱말 단위는 \b 대신 collectMatches의 유니코드 경계 필터로 처리(\b는 한글에서 오동작)
    try { var re = new RegExp(pat, flags); $("f-count").classList.remove("bad"); return re; }
    catch (e) { toastMsg("정규식 오류: " + e.message); var c = $("f-count"); if (c) { c.textContent = "정규식 오류"; c.classList.add("bad"); } return null; }
  }
  // 정규식 바꾸기(2026-11-15): 일치 부분만 떼어 다시 맞추면 $1·뒤보기(?<=)·^ 등 문맥이 깨짐 →
  //   원래 글자 마디 전체에 같은 식(y=그 자리 고정)을 적용해 바꿀 문자열을 얻는다.
  //   (String.replace+sticky는 WebKit이 lastIndex를 무시 → exec로 그 자리 일치를 얻고 $ 치환 규칙은 직접 전개)
  function expandRep(tpl, m, s) {
    return tpl.replace(/\$(\$|&|`|'|<([^>]*)>|\d{1,2})/g, function (all, t, name) {
      if (t === "$") return "$";
      if (t === "&") return m[0];
      if (t === "`") return s.slice(0, m.index);
      if (t === "'") return s.slice(m.index + m[0].length);
      if (name !== undefined) return m.groups ? (m.groups[name] == null ? "" : m.groups[name]) : all;
      var n = parseInt(t, 10);
      if (n >= 1 && n < m.length) return m[n] == null ? "" : m[n];
      if (t.length === 2) { var n1 = parseInt(t.charAt(0), 10); if (n1 >= 1 && n1 < m.length) return (m[n1] == null ? "" : m[n1]) + t.charAt(1); }
      return all;
    });
  }
  function releaseFormula(node) {   // 계산식 칸 글자를 바꿨으면 계산식 해제(직접 입력과 동일 규칙 — 다시 계산이 되돌리지 않게)
    var c = node.parentNode && node.parentNode.closest && node.parentNode.closest("td[data-formula],th[data-formula]");
    if (c) c.removeAttribute("data-formula");
  }
  function replFor(s, mm, re) {
    var rep = $("f-r").value;
    if (!re) return rep;
    var g = new RegExp(re.source, re.flags.replace("g", "") + "g"); g.lastIndex = mm.start;
    var m = g.exec(s);
    return (m && m.index === mm.start) ? expandRep(rep, m, s) : rep;
  }
  var WORDCH = /[0-9A-Za-z가-힣ㄱ-ㅎㅏ-ㅣ_]/;   // 낱말 문자(한글 음절·자모 포함)
  function wholeWord(s, start, end) {
    var a = s.charAt(start - 1), b = s.charAt(end);
    return !(a && WORDCH.test(a)) && !(b && WORDCH.test(b));
  }
  // 편집 불가 파생 글자 판정 — 가장 가까운 contenteditable 조상 기준(#page 자체가 false인 읽기 전용·미리보기는 본문으로 취급)
  function derived(el) {
    if (!el || !el.closest) return true;
    if (el.closest("#wpmeta,[data-pgfill]")) return true;
    var ce = el.closest("[contenteditable]");
    return !!(ce && ce !== page && ce.getAttribute("contenteditable") === "false");
  }
  function textNodes() {
    var walk = document.createTreeWalker(page, NodeFilter.SHOW_TEXT, null), out = [], n;
    while ((n = walk.nextNode())) {   // 문서 메타·편집 불가 파생 글자(미주 번호·주석 제목·개체 내부)는 찾기·바꾸기 대상 아님
      var pe = n.parentNode; if (!pe || derived(pe)) continue;
      out.push(n);
    }
    return out;
  }
  function collectMatches() {
    _matches = []; var re = buildRegex(true); if (!re) { updateFindCount(); return; }
    var word = $("f-word").checked;
    textNodes().forEach(function (n) {
      var s = n.nodeValue, m; re.lastIndex = 0;
      while ((m = re.exec(s))) {
        if (m[0] === "") { re.lastIndex++; continue; }
        if (word && !wholeWord(s, m.index, m.index + m[0].length)) continue;
        _matches.push({ node: n, start: m.index, end: m.index + m[0].length });
      }
    });
    updateFindCount();
  }
  // #23: 캐시키의 전체 innerHTML 직렬화 → MutationObserver 편집세대 카운터로 대체.
  //   모든 DOM 변이(childList+characterData, undo 등 markDirty 우회 경로 포함)가 세대를 올려
  //   stale 노드 참조를 차단(a→b 등 길이 동일 편집 포함 — 종전 innerHTML 방식과 동일 보장, 비용 O(1)).
  var _gen = 0, _mo = new MutationObserver(function () { _gen++; });
  _mo.observe(page, { childList: true, characterData: true, subtree: true });
  function ensureMatches() {
    if (_mo.takeRecords().length) _gen++;   // 같은 태스크 내 변이도 동기 반영(관찰 콜백은 마이크로태스크라 지연될 수 있음)
    var key = $("f-q").value + "|" + $("f-case").checked + $("f-word").checked + $("f-regex").checked + "|" + _gen;
    if (key !== _findKey) { _findKey = key; _mi = -1; collectMatches(); }
  }
  function updateFindCount() { var c = $("f-count"); if (c && c.classList.contains("bad")) return; if (c) c.textContent = _matches.length ? ((_mi + 1) + " / " + _matches.length) : ($("f-q").value ? "일치 없음" : ""); }
  function flashMatch(r) {   // 모달이 포커스를 가져 selection이 흐릿할 때도 찾은 위치가 보이게 잠깐 강조
    try {
      var rect = r.getBoundingClientRect(); if (!rect || (!rect.width && !rect.height)) return;
      var f = document.createElement("div"); f.className = "findflash";
      f.style.left = (rect.left - 3) + "px"; f.style.top = (rect.top - 3) + "px";
      f.style.width = (rect.width + 6) + "px"; f.style.height = (rect.height + 6) + "px";
      document.body.appendChild(f);
      setTimeout(function () { if (f.parentNode) f.parentNode.removeChild(f); }, 700);
    } catch (e) {}
  }
  function selectMatch(i) {
    if (!_matches.length) { updateFindCount(); return; }
    _mi = (i % _matches.length + _matches.length) % _matches.length;
    var mm = _matches[_mi];
    try {
      var r = document.createRange(); r.setStart(mm.node, mm.start); r.setEnd(mm.node, mm.end);
      var sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(r);
      var el = mm.node.parentNode; if (el && el.scrollIntoView) el.scrollIntoView({ block: "center" });
      flashMatch(r);
    } catch (e) {}
    updateFindCount();
  }
  function findStep(d) {
    if (!$("f-q").value) { openModal("m-find"); $("f-q").focus(); return; }   // 검색어 없이 다시 찾기 → 찾기 대화상자(P1-6, 무반응 금지)
    ensureMatches();
    if (_matches.length) selectMatch(_mi + d);
    else { updateFindCount(); if ($("m-find").classList.contains("hidden")) toastMsg("일치 없음: " + $("f-q").value); }
  }
  function findNext() { findStep(1); }
  function findPrev() { findStep(-1); }
  function replaceOne() {
    ensureMatches();
    if (_mi < 0 || _mi >= _matches.length) { findNext(); return; }
    var prev = _mi, mm = _matches[_mi], s = mm.node.nodeValue;
    var rep = replFor(s, mm, $("f-regex").checked ? buildRegex(false) : null);
    mm.node.nodeValue = s.slice(0, mm.start) + rep + s.slice(mm.end);
    releaseFormula(mm.node);
    if (WP.recalcFormulas) WP.recalcFormulas();   // 표 숫자를 바꿨으면 계산식 칸도 맞춤
    markDirty(); _findKey = ""; ensureMatches(); if (_matches.length) selectMatch(prev);   // 바꾼 자리=다음 일치로 진행(마지막이면 wrap)
  }
  function replaceAll() {   // _matches 기반 치환 — 낱말 단위 필터도 일관 적용, 뒤에서부터(오프셋 보존)
    ensureMatches();
    if (!_matches.length) { toastMsg("일치 없음"); updateFindCount(); return; }
    var one = $("f-regex").checked ? buildRegex(false) : null, n = 0;
    var reps = _matches.map(function (mm) { return replFor(mm.node.nodeValue, mm, one); });   // 바꿀 문자열은 모두 원문 기준으로 먼저(문맥 고정)
    for (var i = _matches.length - 1; i >= 0; i--) {   // 같은 마디 안 뒤쪽부터 → 앞 일치의 위치 보존
      var mm = _matches[i], s = mm.node.nodeValue;
      mm.node.nodeValue = s.slice(0, mm.start) + reps[i] + s.slice(mm.end);
      releaseFormula(mm.node);
      n++;
    }
    if (WP.recalcFormulas) WP.recalcFormulas();
    toastMsg(n + "곳 바꿈"); WP.status && WP.status(n + "곳 바꿈"); markDirty(); _findKey = ""; collectMatches();
  }

  // 문서 통계(2026-11-15): 문단 단위(가장 안쪽 p·제목·목록 항목, 문단 없는 표 칸)로 셈. 편집 불가 파생 글자(미주 번호 등)·
  //   문서 메타·쪽 채움·제로폭 자리표시는 제외. 200자 원고지 = 문단마다 첫 칸 비우고 한 줄 20칸, 10줄=1매(문단 끝 남은 칸은 버림).
  var UNIT = "p,h1,h2,h3,h4,h5,h6,li,td,th,pre,blockquote,dt,dd";
  function unitText(el) {
    var w = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, null), n, t = "";
    while ((n = w.nextNode())) {
      if (derived(n.parentNode)) continue;
      t += n.nodeValue;
    }
    return t.replace(/\uFEFF/g, "").replace(/[\r\n]+/g, " ");
  }
  function docStats() {
    var units = Array.prototype.filter.call(page.querySelectorAll(UNIT), function (el) {
      if (derived(el)) return false;
      return !el.querySelector(UNIT);   // 가장 안쪽만(문단 품은 칸·목록은 제외 → 이중 계산 없음)
    });
    var st = { ch: 0, chNoSp: 0, words: 0, paras: 0, lines: 0 };
    units.forEach(function (el) {
      var t = unitText(el); if (!t.replace(/\s/g, "")) return;   // 빈 문단은 세지 않음
      st.paras++; st.ch += t.length; st.chNoSp += t.replace(/\s/g, "").length;
      st.words += (t.trim().match(/\S+/g) || []).length;
      st.lines += Math.ceil((t.trim().length + 1) / 20);
    });
    st.sheets = Math.ceil(st.lines / 10);
    st.pages = WP.pageCount ? WP.pageCount() : 1;
    st.tables = page.querySelectorAll("table").length; st.images = page.querySelectorAll("img").length;
    return st;
  }
  WP.docStats = docStats;
  function wordCount() {
    var st = docStats(), f = function (n) { return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ","); };
    $("st-ch").textContent = f(st.ch) + "자";
    $("st-chnos").textContent = f(st.chNoSp) + "자";
    $("st-words").textContent = f(st.words) + "개";
    $("st-paras").textContent = f(st.paras) + "개";
    $("st-pages").textContent = f(st.pages) + "쪽";
    $("st-sheets").textContent = f(st.sheets) + "매";
    $("st-obj").textContent = st.tables + "표 / " + st.images + "그림";
    openModal("m-stats");
    WP.status && WP.status("문서 통계: " + f(st.ch) + "자 · " + f(st.pages) + "쪽");
  }

  // 자주 틀리는 맞춤법(고신뢰 규칙)
  var SPELL_RULES = [
    ["되요","돼요"],["됬","됐"],["됫","됐"],["할께","할게"],["갈께","갈게"],["줄께","줄게"],["올께","올게"],
    ["그럴려고","그러려고"],["할려고","하려고"],["일일히","일일이"],["곰곰히","곰곰이"],["깨끗히","깨끗이"],
    ["꼼꼼이","꼼꼼히"],["몇일","며칠"],["금새","금세"],["오랫만","오랜만"],["역활","역할"],["설겆이","설거지"],
    ["어떻해","어떡해"],["왠만","웬만"],["왠일","웬일"],["폭팔","폭발"],["희안","희한"],["구지","굳이"]
  ];
  function spellScan() {
    var found = [], txt = page.innerText;
    SPELL_RULES.forEach(function (r) { var c = txt.split(r[0]).length - 1; if (c > 0) found.push({ wrong: r[0], right: r[1], count: c }); });
    if ((txt.match(/ {2,}/g) || []).length) found.push({ wrong: "  ", right: " ", count: (txt.match(/ {2,}/g) || []).length, desc: "이중 공백" });
    return found;
  }
  function spell() {
    var found = spellScan(), dl = $("spell-list");
    if (!found.length) { dl.innerHTML = '<div class="di"><span class="meta">의심되는 맞춤법이 없습니다 👍</span></div>'; openModal("m-spell"); return; }
    dl.innerHTML = "";
    found.forEach(function (f) {
      var row = document.createElement("div"); row.className = "di";
      row.innerHTML = '<span class="nm">' + esc(f.desc || f.wrong) + ' → <b>' + esc(f.right.trim() || "(공백)") + '</b> <span class="meta">' + f.count + '곳</span></span>';
      dl.appendChild(row);
    });
    openModal("m-spell");
  }
  function spellFixAll() {
    var found = spellScan(), n = 0, nodes = textNodes();
    found.forEach(function (f) { nodes.forEach(function (t) { if (t.nodeValue.indexOf(f.wrong) >= 0) { var c = t.nodeValue.split(f.wrong).length - 1; t.nodeValue = t.nodeValue.split(f.wrong).join(f.right); n += c; } }); });
    closeModal($("m-spell")); markDirty(); toastMsg(n + "곳 고침");
  }

  WP.register({
    find: function () { openModal("m-find"); }, replace: function () { openModal("m-find"); },
    findNext: findNext, findPrev: findPrev, replaceOne: replaceOne, replaceAll: replaceAll,
    spell: spell, spellFixAll: spellFixAll, wordcount: wordCount
  });
})();
