/* gyeoljae: OnlyOffice 편집기 띄우기(web_oo.editor_page). 설정은 서버가 서명한 JSON(<script type=application/json>)을
   그대로 문서 서버 api.js(DocsAPI)에 넘길 뿐 — 여기서 만들거나 고치지 않는다. eval·인라인 스크립트 없음. */
(function () {
  'use strict';
  var el = document.getElementById('oo-cfg'), msg = document.getElementById('oo-msg');
  if (!el) return;
  if (typeof window.DocsAPI === 'undefined') {
    if (msg) msg.textContent = '문서 서버(OnlyOffice)에 연결할 수 없습니다. 관리자에게 문의하세요.';
    return;
  }
  var cfg = JSON.parse(el.textContent);
  cfg.width = '100%';
  cfg.height = '100%';
  cfg.events = {
    onDocumentReady: function () { document.body.setAttribute('data-oo-ready', '1'); },
    onError: function (e) { if (msg) msg.textContent = '편집기 오류: ' + ((e && e.data && e.data.errorDescription) || ''); }
  };
  window.gyeoljaeOO = new window.DocsAPI.DocEditor('oo-editor', cfg);
}());
