/* gyeoljae 기안 화면 ↔ hanword 편집기(iframe /editor/embed.html) 연결. 같은 출처 postMessage 만(vendor/hanword/docs/embed.md).
   스크립트가 돌면 입력칸(.edfb)을 숨기고 편집기를 띄우며, 제출 때 편집기 HTML·용지를 doc_html·doc_page 로 보낸다.
   host→editor {type:'load',html,readOnly,page,id} / {type:'get'};
   editor→host {type:'ready'} / {type:'dirty'} / {type:'html',html,page,id} / {type:'error',reason}.
   보내기 전 html 속 사진은 img-shrink.js(window.gyeolShrink)로 줄인다.
   규칙: html 응답은 마지막 load 의 id 와 같을 때만 받음, 읽기 전용 세션·error 뒤에는 doc_html 을 보내지 않음(입력칸으로).

   자동 저장: 고친 뒤 5초 쉬면(계속 고치면 30초마다) 같은 주소로 go=auto POST — 서버는 임시저장과 같은 문서·권한에 저장
   (첫 저장이 새 기안 문서를 만들면 주소를 /doc/N/edit 로 바꿈). 첨부 파일·첨부 삭제는 손으로 저장·상신 때만.
   이 화면 안의 다른 메뉴(링크·다른 form)로 갈 때는 먼저 저장하고 이동. 서버 저장 직전마다 이 브라우저(localStorage)에
   사본을 두고, 서버 저장이 되면 지운다 — 서버 저장이 안 된 사본이 있으면 다음에 열 때 「되살리기 / 버리기」를 묻는다
   (고르기 전에는 지우지 않음). 사본 열쇠 = 사용자 + 문서(새 기안은 양식). 상신·임시저장하면 지우고, 로그아웃은 서버가
   Clear-Site-Data 로 지운다. 나가기 경고는 저장되지 않은 고침이 있을 때만. */
(function () {
  'use strict';
  var host = document.getElementById('edhost'), fr = document.getElementById('edframe'),
      init = document.getElementById('docinit'), err = document.getElementById('ederr'),
      form = host && host.closest('form');
  if (!host || !fr || !init || !form) return;
  var ready = false, broken = false, readOnly = false, sending = false, leaving = false, submitting = false,
      lastBtn = null, loadId = null, page = 'gov', busy = false;
  try { page = JSON.parse(host.getAttribute('data-page') || '"gov"'); } catch (x) { page = 'gov'; }
  var fb = form.querySelectorAll('.edfb');

  function send(m) { if (fr.contentWindow) fr.contentWindow.postMessage(m, location.origin); }
  function rid() {
    var a = new Uint32Array(4); crypto.getRandomValues(a);
    return Array.prototype.map.call(a, function (n) { return n.toString(16); }).join('');
  }
  // 서버 상한(sanitize.clean_html)과 같은 기준을 보내기 전에 미리: 사진(data:image) 뺀 글 2MB·전체 8MB(UTF-8 바이트).
  // 대략 판정이며 최종은 서버. 넘으면 안내 문구, 아니면 ''.
  var MB = 1024 * 1024;
  function bytes(s) { try { return new Blob([s]).size; } catch (x) { return s.length * 3; } }
  function tooBig(h) {
    var all = bytes(h), text = bytes(h.replace(/data:image\/[a-z]+;base64,[A-Za-z0-9+\/=]+/gi, ''));
    if (text > 2 * MB) return '본문 글이 너무 큽니다(사진 말고 최대 2MB). 내용을 여러 문서로 나누어 주세요. 편집기 내용은 그대로 있습니다.';
    if (all > 8 * MB) return '본문이 너무 큽니다(사진 포함 최대 8MB). 사진 수나 크기를 줄여 주세요. 편집기 내용은 그대로 있습니다.';
    return '';
  }
  function fallback(on) {  // on=true: 입력칸 사용(편집기 없음)
    Array.prototype.forEach.call(fb, function (x) {
      x.hidden = !on;
      Array.prototype.forEach.call(x.querySelectorAll('input,textarea,select'), function (i) { i.disabled = !on; });
    });
    fr.hidden = on;
  }
  function fail(msg) {  // 편집기 실패: 보이는 알림 + 입력칸으로(doc_html 은 보내지 않음)
    broken = true; ready = false; flushHtml(null);
    if (err) { err.textContent = msg; err.hidden = false; }
    fallback(true);
    status('', false);
  }

  // 편집기 HTML 받기: 여러 요청(자동 저장·제출)이 겹치면 한 번 'get' 으로 함께 받음. 5초 안에 답이 없으면 null
  var waiters = [], waitT = 0;
  function flushHtml(h, pg) {
    var w = waiters; waiters = []; clearTimeout(waitT);
    w.forEach(function (cb) { cb(h, pg); });
  }
  function getHtml(cb) {
    if (!ready || broken || readOnly) { cb(null); return; }
    waiters.push(cb);
    if (waiters.length > 1) return;
    send({type: 'get'});
    waitT = setTimeout(function () { flushHtml(null); }, 5000);
  }

  // ---------------------------------------------------------------- 이 브라우저 사본(localStorage, 실패해도 동작)
  var USER = host.getAttribute('data-user') || '', PFX = 'gyeoljae.draft.', akey = host.getAttribute('data-akey') || 'new';
  function lkey(k) { return PFX + encodeURIComponent(USER) + '.' + (k || akey); }
  function lget(k) { try { return JSON.parse(localStorage.getItem(k) || 'null'); } catch (x) { return null; } }
  function ldel(k) { try { localStorage.removeItem(k); } catch (x) { /* 없음 */ } }
  function lput(k, v) {
    try { localStorage.setItem(k, JSON.stringify(v)); return true; } catch (x) { /* 용량 → 사진 빼고 다시 */ }
    try {
      var v2 = JSON.parse(JSON.stringify(v));
      v2.html = String(v2.html || '').replace(/data:image\/[a-z]+;base64,[A-Za-z0-9+\/=]+/gi, '');
      v2.nophoto = true;
      localStorage.setItem(k, JSON.stringify(v2)); return true;
    } catch (x) { return false; }
  }
  (function purge() {  // 다른 사용자 사본·하루 지난 사본은 지움(같은 기기 다음 사람에게 오래 남지 않게)
    try {
      var old = Date.now() - 864e5, mine = PFX + encodeURIComponent(USER) + '.', drop = [];
      for (var i = 0; i < localStorage.length; i++) {
        var k = localStorage.key(i);
        if (k && k.indexOf(PFX) === 0) {
          var v = lget(k);
          if (k.indexOf(mine) !== 0 || !v || !(v.at > old)) drop.push(k);
        }
      }
      drop.forEach(ldel);
    } catch (x) { /* 저장소 없음 */ }
  })();
  var SKIP = {csrf: 1, doc_html: 1, doc_page: 1, go: 1, files: 1, remove: 1};
  function fields() {  // 손으로 쓰는 칸만(숨은 값·파일 빼고)
    var out = [];
    Array.prototype.forEach.call(form.elements, function (e) {
      if (!e.name || SKIP[e.name] || e.disabled || /^(hidden|file|submit|button|reset)$/.test(e.type)) return;
      if (e.type === 'checkbox' || e.type === 'radio') out.push([e.name, e.value, e.checked]);
      else if (e.tagName !== 'BUTTON') out.push([e.name, e.value]);
    });
    return out;
  }
  function setFields(list) {
    (list || []).forEach(function (f) {
      Array.prototype.forEach.call(form.elements, function (e) {
        if (e.name !== f[0] || e.disabled || /^(hidden|file|submit|button)$/.test(e.type)) return;
        if (f.length === 3) { if (e.value === f[1]) e.checked = !!f[2]; } else if (e.tagName !== 'BUTTON') e.value = f[1];
      });
    });
  }

  // ---------------------------------------------------------------- 상태 줄
  var bar = form.querySelector('.savebar'), st = document.createElement('span'), stT = document.createElement('span');
  var retry = document.createElement('button');
  st.className = 'edstat'; st.setAttribute('role', 'status'); st.setAttribute('aria-live', 'polite');
  retry.type = 'button'; retry.className = 'sec'; retry.textContent = '다시 시도'; retry.hidden = true;
  st.appendChild(stT); st.appendChild(document.createTextNode(' ')); st.appendChild(retry);
  if (bar) bar.insertBefore(st, bar.firstChild); else st.hidden = true;
  retry.addEventListener('click', function () { save(); });
  function hhmm(d) { return ('0' + d.getHours()).slice(-2) + ':' + ('0' + d.getMinutes()).slice(-2); }
  function status(text, bad) {
    stT.textContent = text; st.classList.toggle('bad', !!bad); retry.hidden = !bad;
  }

  // ---------------------------------------------------------------- 자동 저장
  var gen = 0, savedGen = 0, inflight = null, failed = false, timer = 0, first = 0, filesPending = false;
  function changed() {
    if (!ready || broken || readOnly || sending || submitting || leaving) return;
    gen++;
    var now = Date.now();
    if (!first) first = now;
    clearTimeout(timer);
    timer = setTimeout(save, now - first > 30000 ? 0 : 5000);
  }
  function save() {  // → Promise<bool> (서버에 지금까지의 고침이 다 저장됐는가)
    clearTimeout(timer); first = 0;
    // 손으로 저장·상신이 시작됐으면 자동 저장 안 함(같은 기안이 둘 생기거나 상신 뒤 늦은 자동 저장이 남지 않게)
    if (!ready || broken || readOnly || submitting || sending || leaving) return Promise.resolve(false);
    if (inflight) return inflight.then(function () { return gen > savedGen ? save() : !failed; });
    if (gen === savedGen) return Promise.resolve(!failed);
    var g = gen;
    status('저장 중…', false);
    inflight = new Promise(function (done) {
      getHtml(function (raw, pg) {
        if (raw === null) { failed = true; status('저장 실패 — 편집기가 응답하지 않습니다', true); done(false); return; }
        // 사진은 상신 때처럼 먼저 줄인 뒤 크기 판정(줄이면 8MB 안인 문서가 자동 저장에서만 실패하지 않게)
        shrunk(raw, function (html) { send2(html, pg); });
      });
      function send2(html, pg) {
        if (submitting || sending) { done(false); return; }
        var copy = {u: USER, at: Date.now(), html: html, page: pg || null, f: fields()};
        var big = tooBig(html);
        if (big) {  // 8MB·2MB: 서버로 보내지 않음(기존 안내), 이 브라우저 사본은 둠
          lput(lkey(), copy); failed = true; status('저장 실패 — ' + big, true); done(false); return;
        }
        var kept = lput(lkey(), copy);
        var fd = new FormData(form);
        ['files', 'remove', 'go', 'doc_html', 'doc_page'].forEach(function (n) { fd.delete(n); });
        fd.set('go', 'auto'); fd.set('doc_html', html);
        if (pg && typeof pg === 'object') fd.set('doc_page', JSON.stringify(pg));
        fetch(form.getAttribute('action'), {method: 'POST', body: fd, credentials: 'same-origin', redirect: 'error'})
          .then(function (r) { return r.json().then(function (j) { return [r.ok, j]; }); })
          .then(function (rj) {
            var j = rj[1] || {};
            if (!rj[0] || !j.ok) throw new Error(j.error || '서버 오류');
            if (j.edit && form.getAttribute('action') !== j.edit) {  // 새 기안이 문서가 됨 → 이후 저장·상신은 그 문서로
              form.setAttribute('action', j.edit);
              try { history.replaceState(history.state, '', j.edit); } catch (x) { /* 주소만 못 바꿈 */ }
              var nk = String(j.id);
              ldel(lkey()); akey = nk; host.setAttribute('data-akey', nk);
              if (gen > g) lput(lkey(), copy);
            }
            savedGen = Math.max(savedGen, g); failed = false;
            if (gen === savedGen) ldel(lkey());
            status('자동 저장됨 ' + hhmm(new Date()), false);
            done(true);
          })
          .catch(function (e) {
            failed = true;
            var why = e && e.message && !/fetch|network|redirect|JSON|Unexpected/i.test(e.message) ? ' (' + e.message + ')' : '';
            status('저장 실패' + why + ' — ' + (kept ? '이 브라우저에 보관했습니다. ' : '') + '다시 시도', true);
            done(false);
          });
      }
    }).then(function (ok) { inflight = null; return ok; });
    return inflight;
  }
  function shrunk(html, cb) {  // img-shrink.js 로 사진 줄이기(없거나 30초 넘게 멈추면 그대로)
    if (!window.gyeolShrink || html.indexOf('data:image/') < 0) { cb(html); return; }
    var once = function (h) { if (cb) { var c = cb; cb = null; c(h); } };
    setTimeout(function () { once(html); }, 30000);
    try { window.gyeolShrink.html(html, once); } catch (x) { once(html); }
  }
  function unsaved() { return gen > savedGen || !!inflight; }

  // 저장 안 된 이 브라우저 사본 되살리기(서버 저장이 안 된 채 떠났던 것). 고르기 전에는 지우지 않음
  var prevKey = null;
  function offerRestore() {
    var k = lkey(), v = lget(k);
    if (!v || v.u !== USER) return;
    prevKey = k + '.prev';
    var pv = lget(prevKey);
    if (!pv || pv.at < v.at) lput(prevKey, v);  // 지금 화면의 저장이 덮어쓰지 않게 옆으로
    ldel(k);
    v = lget(prevKey) || v;
    if (v.html === init.value && JSON.stringify(v.f || []) === JSON.stringify(fields())) {  // 서버 내용·칸 모두 같음 = 잃을 것 없음
      ldel(prevKey); return;
    }
    var box = document.createElement('div'), p = document.createElement('span');
    box.className = 'banner warn edrestore'; box.setAttribute('role', 'alert');
    p.textContent = '저장 안 된 작성분이 있어요 (' + hhmm(new Date(v.at)) + (v.nophoto ? ', 사진 빠짐' : '') + ') — ';
    var yes = document.createElement('button'), no = document.createElement('button');
    yes.type = no.type = 'button'; yes.textContent = '되살리기'; no.textContent = '버리기'; no.className = 'sec';
    box.appendChild(p); box.appendChild(yes); box.appendChild(document.createTextNode(' ')); box.appendChild(no);
    host.insertBefore(box, host.firstChild);
    yes.addEventListener('click', function () {
      setFields(v.f);
      if (v.page) page = v.page;
      loadId = rid();
      send({type: 'load', html: v.html, readOnly: false, page: page, id: loadId});
      ldel(prevKey); box.remove(); changed(); save();
    });
    no.addEventListener('click', function () { ldel(prevKey); box.remove(); });
  }

  window.addEventListener('message', function (e) {
    if (e.origin !== location.origin || e.source !== fr.contentWindow) return;
    var d = e.data || {};
    if (d.type === 'ready') {
      if (broken) return;
      var firstReady = !ready;
      ready = true; loadId = rid();
      send({type: 'load', html: init.value, readOnly: readOnly, page: page, id: loadId});
      if (firstReady) offerRestore();
    } else if (d.type === 'dirty') {
      changed();
    } else if (d.type === 'error') {
      fail('문서 편집기가 문서를 열지 못했습니다(' + String(d.reason || '오류') +
           '). 아래 입력칸으로 작성하세요 — 편집기 내용은 저장되지 않습니다.');
    } else if (d.type === 'html' && waiters.length) {
      if (d.id !== loadId || readOnly || broken) return;  // 다른 문서·읽기 전용 응답은 버림
      flushHtml(String(d.html || ''), d.page);
    }
  });

  form.addEventListener('input', function (e) { if (e.target && e.target.type !== 'file') changed(); });
  form.addEventListener('change', function (e) {
    if (e.target && e.target.type === 'file') {
      filesPending = !!(e.target.files && e.target.files.length);
      if (filesPending) status('첨부 파일은 임시저장·상신 때 올라갑니다', false);
    } else changed();
  });
  form.addEventListener('click', function (e) {
    var b = e.target.closest && e.target.closest('button');
    if (b) lastBtn = b;
  });
  form.addEventListener('submit', function (e) {
    if (sending) return;
    if (!ready || broken || readOnly) { fallback(true); leaving = true; return; }  // 편집기 없음 → 입력칸으로 그대로 제출
    e.preventDefault();
    if (busy || submitting) return;  // 사진 줄이는 중·편집기 응답 기다리는 중(두 번 누름)
    var go = e.submitter || lastBtn;
    clearTimeout(timer);
    busy = true; submitting = true;
    function stop() { busy = false; submitting = false; }  // 보내지 않고 멈춤(다시 누를 수 있게)
    // 자동 저장 중이면 끝난 뒤(첫 저장이 문서를 만들었으면 그 문서 주소로) 보냄 — 같은 기안이 둘 생기지 않게
    (inflight || Promise.resolve()).then(function () {
      getHtml(function (html, pg) {
        if (html === null) { stop(); alert('편집기가 응답하지 않습니다. 다시 시도하세요.'); return; }
        function hid(tag, name, val) {
          var t = document.createElement(tag);
          if (tag === 'input') t.type = 'hidden'; else t.hidden = true;
          t.name = name; t.value = val; form.appendChild(t);
        }
        function post(h) {
          var big = tooBig(h);
          if (big) { stop(); alert(big); return; }  // 보내지 않음 — 편집기 내용 그대로(서버 거절로 내용을 잃지 않게)
          hid('textarea', 'doc_html', h);
          if (pg && typeof pg === 'object') hid('input', 'doc_page', JSON.stringify(pg));
          if (go && go.name) hid('input', go.name, go.value);
          sending = true; leaving = true;
          ldel(lkey());  // 손으로 저장·상신: 이 브라우저 사본은 필요 없음(거절되면 서버가 내용을 다시 보여 줌)
          form.submit();
        }
        // 사진 줄이기(img-shrink.js: 긴 변 1600px·JPEG 0.85, 작아질 때만). 없거나 30초 넘게 멈추면 그대로 보냄(서버가 8MB 상한)
        shrunk(html, post);
      });
    });
  });

  // 이 화면 안에서 다른 메뉴로: 먼저 저장하고 이동(저장 실패면 물어봄)
  function away(go) {
    save().then(function (ok) {
      if (ok || confirm('작성 중인 내용을 서버에 저장하지 못했습니다. 이 브라우저에 보관한 사본은 다음에 이 문서를 열 때 ' +
                        '되살릴 수 있습니다(로그아웃하면 지워짐). 그래도 나갈까요?')) { leaving = true; go(); }
    });
  }
  document.addEventListener('click', function (e) {
    var a = e.target.closest && e.target.closest('a[href]');
    if (!a || e.defaultPrevented || e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
    if (a.target && a.target !== '_self' || a.hasAttribute('download')) return;
    var u; try { u = new URL(a.href, location.href); } catch (x) { return; }
    if (u.origin !== location.origin || (u.pathname === location.pathname && u.search === location.search && u.hash)) return;
    if (!unsaved()) return;
    e.preventDefault();
    away(function () { location.href = u.href; });
  }, true);
  document.addEventListener('submit', function (e) {  // 머리줄 검색·로그아웃 등 다른 form
    var f = e.target;
    if (f === form || e.defaultPrevented || !unsaved()) return;
    e.preventDefault();
    away(function () { f.submit(); });
  }, true);
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'hidden' && unsaved() && !submitting && !leaving) save();
  });
  window.addEventListener('pagehide', function () { if (unsaved() && !submitting && !leaving) save(); });
  window.addEventListener('beforeunload', function (e) {
    if (leaving || sending || submitting) return;
    if (unsaved() || filesPending) { save(); e.preventDefault(); e.returnValue = ''; }  // 저장 못 한 고침이 있을 때만
  });

  fallback(false);
  host.hidden = false;
  fr.src = '/editor/embed.html';
})();
