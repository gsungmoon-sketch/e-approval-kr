/* gyeoljae 기안 화면 ↔ hanword 편집기(iframe /editor/embed.html) 연결. 같은 출처 postMessage 만(vendor/hanword/docs/embed.md).
   스크립트가 돌면 입력칸(.edfb)을 숨기고 편집기를 띄우며, 제출 때 편집기 HTML·용지를 doc_html·doc_page 로 보낸다.
   host→editor {type:'load',html,readOnly,page,id} / {type:'get'};
   editor→host {type:'ready'} / {type:'dirty'} / {type:'html',html,page,id} / {type:'error',reason}.
   보내기 전 html 속 사진은 img-shrink.js(window.gyeolShrink)로 줄인다.
   규칙: html 응답은 마지막 load 의 id 와 같을 때만 받음, 읽기 전용 세션·error 뒤에는 doc_html 을 보내지 않음(입력칸으로). */
(function () {
  'use strict';
  var host = document.getElementById('edhost'), fr = document.getElementById('edframe'),
      init = document.getElementById('docinit'), err = document.getElementById('ederr'),
      form = host && host.closest('form');
  if (!host || !fr || !init || !form) return;
  var ready = false, broken = false, readOnly = false, pending = null, dirty = false, sending = false,
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
    broken = true; ready = false; pending = null;
    if (err) { err.textContent = msg; err.hidden = false; }
    fallback(true);
  }

  window.addEventListener('message', function (e) {
    if (e.origin !== location.origin || e.source !== fr.contentWindow) return;
    var d = e.data || {};
    if (d.type === 'ready') {
      if (broken) return;
      ready = true; loadId = rid();
      send({type: 'load', html: init.value, readOnly: readOnly, page: page, id: loadId});
    } else if (d.type === 'dirty') {
      dirty = true;
    } else if (d.type === 'error') {
      fail('문서 편집기가 문서를 열지 못했습니다(' + String(d.reason || '오류') +
           '). 아래 입력칸으로 작성하세요 — 편집기 내용은 저장되지 않습니다.');
    } else if (d.type === 'html' && pending) {
      if (d.id !== loadId || readOnly || broken) return;  // 다른 문서·읽기 전용 응답은 버림
      var cb = pending; pending = null; cb(String(d.html || ''), d.page);
    }
  });

  form.addEventListener('click', function (e) {
    var b = e.target.closest && e.target.closest('button');
    if (b) lastBtn = b;
  });
  form.addEventListener('submit', function (e) {
    if (sending) return;
    if (!ready || broken || readOnly) { fallback(true); return; }  // 편집기 없음 → 입력칸으로 그대로 제출
    e.preventDefault();
    if (busy) return;  // 사진 줄이는 중(두 번 누름)
    var go = e.submitter || lastBtn;
    pending = function (html, pg) {
      function hid(tag, name, val) {
        var t = document.createElement(tag);
        if (tag === 'input') t.type = 'hidden'; else t.hidden = true;
        t.name = name; t.value = val; form.appendChild(t);
      }
      function post(h) {
        busy = false;
        var big = tooBig(h);
        if (big) { alert(big); return; }  // 보내지 않음 — 편집기 내용 그대로(서버 거절로 내용을 잃지 않게)
        hid('textarea', 'doc_html', h);
        if (pg && typeof pg === 'object') hid('input', 'doc_page', JSON.stringify(pg));
        if (go && go.name) hid('input', go.name, go.value);
        sending = true;
        form.submit();
      }
      // 사진 줄이기(img-shrink.js: 긴 변 1600px·JPEG 0.85, 작아질 때만). 없거나 실패하면 그대로 보냄(서버가 8MB 상한)
      if (window.gyeolShrink && html.indexOf('data:image/') >= 0) {
        busy = true;
        var once = function (h) { if (busy) post(h); };
        setTimeout(function () { once(html); }, 30000);  // 줄이기가 멈추면 원본 그대로(서버가 판정)
        try { window.gyeolShrink.html(html, once); } catch (x) { once(html); }
      } else {
        post(html);
      }
    };
    send({type: 'get'});
    setTimeout(function () { if (pending) { pending = null; alert('편집기가 응답하지 않습니다. 다시 시도하세요.'); } }, 5000);
  });
  window.addEventListener('beforeunload', function (e) {
    if (dirty && !sending) { e.preventDefault(); e.returnValue = ''; }
  });

  fallback(false);
  host.hidden = false;
  fr.src = '/editor/embed.html';
})();
