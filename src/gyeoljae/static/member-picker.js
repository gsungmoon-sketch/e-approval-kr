/* gyeoljae: 사업 문서함 참여 부서·참여자 고르기(web_filing._member_picker). 스크립트 없이도 체크박스로 동작하며,
   이 파일은 검색칸을 보여 주고 입력한 글자가 든 항목만 남기며(이름·아이디·부서, 대소문자 무시) 고른 수를 표시한다.
   목록은 서버가 그린 것(이스케이프됨)을 거르기만 하고 HTML 을 만들지 않는다. 검증은 서버가 한다. */
(function () {
  'use strict';
  Array.prototype.forEach.call(document.querySelectorAll('[data-picker]'), function (box) {
    var q = box.querySelector('.pk-q'), n = box.querySelector('.pk-n'),
        items = box.querySelectorAll('.pk-list label.pick');
    function count() {
      if (n) n.textContent = '(' + box.querySelectorAll('.pk-list input:checked').length + '개 선택)';
    }
    function filter() {
      var t = (q.value || '').trim().toLowerCase().split(/\s+/).filter(Boolean);
      Array.prototype.forEach.call(items, function (it) {
        var s = it.getAttribute('data-q') || '', on = it.querySelector('input').checked;
        it.hidden = !on && !t.every(function (w) { return s.indexOf(w) >= 0; });  // 고른 것은 늘 보임
      });
    }
    if (q) {
      q.hidden = false;
      q.addEventListener('input', filter);
      q.addEventListener('keydown', function (e) { if (e.key === 'Enter') e.preventDefault(); });
    }
    box.addEventListener('change', count);
    count();
  });
})();
