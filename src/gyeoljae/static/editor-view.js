/* gyeoljae 기안 화면: 문서 편집기 크기.
   기본: 편집기(iframe)가 화면 높이에 맞춰(머리줄·저장 줄 뺀 나머지) 커지고, 기안 화면만 본문 폭 상한을 푼다.
   「크게 보기」: 머리줄·메뉴·오른쪽 칸을 숨기고 편집기만 화면 가득. 제목·결재선·첨부·분류는 「결재 정보」 칸(덮개)으로 옮겨
   언제든 열 수 있다(같은 <form> 안이라 그대로 제출됨). 「전체 화면」= Fullscreen API(누를 때만), Esc 로 돌아감.
   선택은 이 브라우저에 기억(localStorage, 실패해도 동작). 기억이 없으면 작은 화면(폭 ≤1024 또는 높이 ≤700)은 크게 보기로 시작.
   편집기가 실패해 입력칸으로 바뀌면(iframe hidden) 크게 보기를 끄고 단추를 숨긴다. */
(function () {
  'use strict';
  var host = document.getElementById('edhost'), fr = document.getElementById('edframe'),
      form = host && host.closest('form'), bar = form && form.querySelector('.savebar');
  if (!host || !fr || !form || !bar || host.hidden) return;
  var KEY = 'gyeoljae.edbig', body = document.body, root = document.documentElement;
  var sec = host.closest('fieldset') || host.parentNode;
  var info = null, marks = [], big = false;

  function load() { try { return localStorage.getItem(KEY); } catch (x) { return null; } }
  function save(v) { try { localStorage.setItem(KEY, v ? '1' : '0'); } catch (x) { /* 기억 못 해도 동작 */ } }
  function small() {
    try { return window.matchMedia('(max-width:1024px),(max-height:700px)').matches; } catch (x) { return false; }
  }
  function btn(text, cls) {
    var b = document.createElement('button');
    b.type = 'button'; b.className = 'sec ' + cls; b.textContent = text;
    return b;
  }

  // 저장 줄 앞에 단추: 크게 보기 · 결재 정보(크게 보기일 때만) · 전체 화면(지원할 때만) · 안내
  var tools = document.createElement('span'); tools.className = 'edtools';
  var bBig = btn('크게 보기', 'edbig-t'), bInfo = btn('결재 정보', 'edinfo-t'), bFull = null;
  bBig.setAttribute('aria-pressed', 'false');
  bInfo.setAttribute('aria-expanded', 'false'); bInfo.setAttribute('aria-controls', 'edinfo'); bInfo.hidden = true;
  tools.appendChild(bBig); tools.appendChild(bInfo);
  var fsOk = !!(document.fullscreenEnabled && root.requestFullscreen);
  if (fsOk) { bFull = btn('전체 화면', 'edfull-t'); bFull.title = '전체 화면(F11 도 됨) · Esc 로 돌아가기'; tools.appendChild(bFull); }
  var hint = document.createElement('span'); hint.className = 'mut edhint';
  hint.textContent = fsOk ? 'F11·전체 화면 / Esc 로 돌아가기' : 'F11 = 브라우저 전체 화면';
  tools.appendChild(hint);
  bar.insertBefore(tools, bar.firstChild);

  // 결재 정보 덮개: 크게 보기 동안 편집기 칸 밖의 입력(제목·결재선·첨부·분류)을 옮겨 둔다. iframe 은 옮기지 않음(다시 읽힘 방지)
  function movable() {
    var out = [];
    Array.prototype.forEach.call(form.children, function (x) {
      if (x === sec || x === bar || x === info || x.tagName === 'INPUT' || x.tagName === 'TEXTAREA') return;
      out.push(x);
    });
    // 편집기 칸 안의 제목(label·input)도 — 입력칸(.edfb)은 편집기가 있을 때 숨겨져 있으므로 두고
    var ti = sec.querySelector('#ti'), tl = ti && sec.querySelector("label[for='ti']");
    var at = out.length ? 1 : 0;  // ① 양식 다음 자리(제목이 앞쪽에 보이게)
    if (tl) out.splice(at, 0, tl);
    if (ti) out.splice(at + (tl ? 1 : 0), 0, ti);
    return out;
  }
  function openInfo(on) {
    if (!info) return;
    info.hidden = !on;
    bInfo.setAttribute('aria-expanded', on ? 'true' : 'false');
    if (on) { var f = info.querySelector('input:not([type=hidden]),select,textarea,button'); if (f) f.focus(); }
  }
  function enter() {
    if (big) return;
    big = true;
    if (!info) {
      info = document.createElement('div');
      info.id = 'edinfo'; info.className = 'edinfo'; info.hidden = true;
      info.setAttribute('role', 'region'); info.setAttribute('aria-label', '결재 정보');
      var head = document.createElement('div'); head.className = 'edinfo-h';
      var h = document.createElement('b'); h.textContent = '결재 정보';
      var close = btn('닫기', 'edinfo-x');
      close.addEventListener('click', function () { openInfo(false); bInfo.focus(); });
      head.appendChild(h); head.appendChild(close); info.appendChild(head);
      form.insertBefore(info, form.firstChild);
    }
    movable().forEach(function (x) {
      var m = document.createComment('edinfo');
      x.parentNode.insertBefore(m, x);
      marks.push([m, x]);
      info.appendChild(x);
    });
    body.classList.add('edbig');
    bBig.setAttribute('aria-pressed', 'true'); bBig.textContent = '크게 보기 끄기';
    bInfo.hidden = false;
    fit();
  }
  function leave() {
    if (!big) return;
    big = false;
    openInfo(false);
    marks.forEach(function (p) { p[0].parentNode.insertBefore(p[1], p[0]); p[0].parentNode.removeChild(p[0]); });
    marks = [];
    body.classList.remove('edbig');
    bBig.setAttribute('aria-pressed', 'false'); bBig.textContent = '크게 보기';
    bInfo.hidden = true;
    if (document.fullscreenElement && document.exitFullscreen) document.exitFullscreen().catch(function () {});
    fit();
  }

  // 높이: 보이는 창 높이(--vh)·저장 줄(--sb)·머리줄(--hd) 을 재어 CSS 가 편집기 높이를 계산(창 크기·방향·전체 화면 바뀔 때마다)
  function fit() {
    var hd = document.querySelector('header.top');
    root.style.setProperty('--vh', window.innerHeight + 'px');
    root.style.setProperty('--sb', bar.offsetHeight + 'px');
    root.style.setProperty('--hd', (hd && !big ? hd.offsetHeight : 0) + 'px');
  }
  var raf = 0;
  function refit() { if (!raf) raf = requestAnimationFrame(function () { raf = 0; fit(); }); }
  window.addEventListener('resize', refit);
  window.addEventListener('orientationchange', refit);
  if (window.visualViewport) window.visualViewport.addEventListener('resize', refit);
  if (window.ResizeObserver) new ResizeObserver(refit).observe(bar);

  bBig.addEventListener('click', function () { if (big) leave(); else enter(); save(big); });
  bInfo.addEventListener('click', function () { openInfo(info.hidden); });
  if (bFull) {
    bFull.addEventListener('click', function () {
      if (document.fullscreenElement) { document.exitFullscreen().catch(function () {}); return; }
      if (!big) { enter(); save(true); }
      root.requestFullscreen().catch(function () { /* 거절(설정·정책) → 크게 보기만 */ });
    });
    document.addEventListener('fullscreenchange', function () {
      bFull.textContent = document.fullscreenElement ? '전체 화면 끝내기' : '전체 화면';
      refit();
    });
  }
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') return;
    if (info && !info.hidden) { openInfo(false); bInfo.focus(); return; }
    if (document.fullscreenElement && document.exitFullscreen) document.exitFullscreen().catch(function () {});
  });
  // 결재 정보 안의 필수 칸(제목 등)이 비어 상신이 막히면 덮개를 열어 보여 줌
  form.addEventListener('invalid', function () { if (big && info && info.hidden) openInfo(true); }, true);
  // 편집기 실패 → 입력칸으로: 크게 보기 끄고 단추 숨김
  new MutationObserver(function () {
    if (fr.hidden) { leave(); tools.hidden = true; body.classList.remove('edpage'); }
  }).observe(fr, {attributes: true, attributeFilter: ['hidden']});

  body.classList.add('edpage');
  var pref = load();
  if (pref === '1' || (pref === null && small())) enter(); else fit();
  window.gyeolView = {big: function () { return big; }, enter: enter, leave: leave};
})();
