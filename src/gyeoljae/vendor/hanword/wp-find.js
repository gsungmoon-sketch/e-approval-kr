// 한워드 — 찾기·바꾸기(정규식/대소문자/낱말단위) + 맞춤법(규칙) + 글자수.
(function () {
  "use strict";
  var WP = window.WP, $ = WP.$, page = WP.page, markDirty = WP.markDirty, toastMsg = WP.toast,
      openModal = WP.openModal, closeModal = WP.closeModal, esc = WP.esc;
  var _matches = [], _mi = -1, _findKey = "";

  function buildRegex(global) {
    var q = $("f-q").value; if (!q) return null;
    var flags = (global ? "g" : "") + ($("f-case").checked ? "" : "i");
    var pat = $("f-regex").checked ? q : q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    // 낱말 단위는 \b 대신 collectMatches의 유니코드 경계 필터로 처리(\b는 한글에서 오동작)
    try { return new RegExp(pat, flags); } catch (e) { toastMsg("정규식 오류: " + e.message); return null; }
  }
  var WORDCH = /[0-9A-Za-z가-힣ㄱ-ㅎㅏ-ㅣ_]/;   // 낱말 문자(한글 음절·자모 포함)
  function wholeWord(s, start, end) {
    var a = s.charAt(start - 1), b = s.charAt(end);
    return !(a && WORDCH.test(a)) && !(b && WORDCH.test(b));
  }
  function textNodes() {
    var walk = document.createTreeWalker(page, NodeFilter.SHOW_TEXT, null), out = [], n;
    while ((n = walk.nextNode())) { if (!(n.parentNode && n.parentNode.id === "wpmeta")) out.push(n); }
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
  function updateFindCount() { var c = $("f-count"); if (c) c.textContent = _matches.length ? ((_mi + 1) + " / " + _matches.length) : ($("f-q").value ? "일치 없음" : ""); }
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
    var prev = _mi, mm = _matches[_mi], s = mm.node.nodeValue, matched = s.slice(mm.start, mm.end), rep = $("f-r").value;
    if ($("f-regex").checked) { var one = buildRegex(false); if (one) rep = matched.replace(one, $("f-r").value); }
    mm.node.nodeValue = s.slice(0, mm.start) + rep + s.slice(mm.end);
    markDirty(); _findKey = ""; ensureMatches(); if (_matches.length) selectMatch(prev);   // 바꾼 자리=다음 일치로 진행(마지막이면 wrap)
  }
  function replaceAll() {   // _matches 기반 치환 — 낱말 단위 필터도 일관 적용, 뒤에서부터(오프셋 보존)
    ensureMatches();
    if (!_matches.length) { toastMsg("일치 없음"); updateFindCount(); return; }
    var rep = $("f-r").value, one = $("f-regex").checked ? buildRegex(false) : null, n = 0;
    for (var i = _matches.length - 1; i >= 0; i--) {
      var mm = _matches[i], s = mm.node.nodeValue, matched = s.slice(mm.start, mm.end);
      var r = one ? matched.replace(one, rep) : rep;
      mm.node.nodeValue = s.slice(0, mm.start) + r + s.slice(mm.end);
      n++;
    }
    toastMsg(n + "곳 바꿈"); markDirty(); _findKey = ""; collectMatches();
  }

  function wordCount() {
    var txt = page.innerText.replace(/\uFEFF/g, ""), nospace = txt.replace(/\s/g, ""), words = (txt.trim().match(/\S+/g) || []).length;   // 대기 서식 span의 제로폭 자리표시는 글자수서 제외
    $("st-ch").textContent = txt.replace(/\n/g, "").length + "자";
    $("st-chnos").textContent = nospace.length + "자";
    $("st-words").textContent = words + "개";
    $("st-paras").textContent = page.querySelectorAll("p,h1,h2,h3,li").length + "개";
    $("st-obj").textContent = page.querySelectorAll("table").length + "표 / " + page.querySelectorAll("img").length + "그림";
    openModal("m-stats");
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
