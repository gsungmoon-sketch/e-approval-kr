// 한워드 — 익명 게시판(버그리포트, 2026-07-25). 로그인 없이 누구나 작성·열람.
//   ⭐오버레이 전용: #page(편집 문서)·캐럿·스크롤을 절대 건드리지 않음 → 닫으면 쓰던 문서 그대로 복귀.
//   태그(버그·문의·의견·기타)·별명(비우면 익명)·비밀번호(본인 글·댓글 수정/삭제)·비공개·검색·댓글.
//   서버: GET /api/board/list?tag=&q=·/api/board/get?id= / POST /api/board/{post,edit,delete,comment,comment-delete}.
(function () {
  "use strict";
  var WP = window.WP; if (!WP) return;
  var esc = WP.esc, toast = WP.toast || function () {};
  var TITLE = "버그리포트 게시판";
  var TAGS = ["버그", "문의", "의견", "기타"];
  var RULES = [
    "욕설·비방·광고, 게시판 목적에 맞지 않는 글은 임의 삭제될 수 있습니다.",
    "문의·신고에 개별적으로 답변하지 않을 수 있습니다.",
    "서버가 중단되거나 자료가 손실되어도 책임지지 않습니다. 작성한 문서는 수시로 내려받아(다운로드) 직접 보관하세요."
  ];
  var MINE_KEY = "wp_board_mine";
  var ov = null, curTag = "전체", curQ = "", mineOn = false;

  function z(n) { return (n < 10 ? "0" : "") + n; }
  function fmtTime(ts) { var d = new Date(ts); return d.getFullYear() + "-" + z(d.getMonth() + 1) + "-" + z(d.getDate()) + " " + z(d.getHours()) + ":" + z(d.getMinutes()); }
  function nl2br(s) { return esc(s).replace(/\n/g, "<br>"); }
  function getMine() { try { return JSON.parse(localStorage.getItem(MINE_KEY) || "[]"); } catch (e) { return []; } }
  function addMine(id) { try { var a = getMine(); if (a.indexOf(id) < 0) { a.unshift(id); localStorage.setItem(MINE_KEY, JSON.stringify(a.slice(0, 500))); } } catch (e) {} }
  function delMine(id) { try { localStorage.setItem(MINE_KEY, JSON.stringify(getMine().filter(function (x) { return x !== id; }))); } catch (e) {} }
  function api(path, data) {
    return fetch("/api/board/" + path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) }).then(function (r) { return r.json(); });
  }

  // ── 고정 공지(pinned): 사용안내·개인정보보호·한워드 소개 + 운영규칙 3종 ──
  function pinnedHtml() {
    return '<div class="bd-pin">' +
      '<div class="bd-pin-h">📌 게시판 공지 (고정)</div>' +
      '<div class="bd-pin-s"><b>사용 안내</b> — 로그인 없이 누구나 익명으로 글을 씁니다. <b>별명</b>은 원하면 지어도 되고 비우면 “익명”으로 표시됩니다. 글·댓글마다 <b>비밀번호</b>를 정하면 나중에 본인 글·댓글을 <b>수정·삭제</b>할 수 있습니다. 글에 <b>댓글</b>을 달 수 있고, <b>태그</b>(버그·문의·의견·기타)로 분류·검색됩니다.</div>' +
      '<div class="bd-pin-s bd-pin-warn"><b>개인정보 보호</b> — 익명 게시판입니다. <b>실명·전화번호·이메일·주소 등 개인정보를 글이나 댓글에 쓰지 마세요.</b> 작성한 내용은 <b>누구나 볼 수 있게 공개</b>됩니다(비공개 글도 관리 목적상 안전을 보장하지 않습니다).</div>' +
      '<div class="bd-pin-s"><b>한워드(hanword)란?</b> — 설치 없이 브라우저에서 바로 쓰는 <b>웹 워드프로세서</b>입니다. 표·그림·도형·미디어 편집, HTML·PDF·HWPX 내보내기를 지원하며 문서는 내 브라우저 안에서 다뤄집니다. 이 게시판은 한워드의 버그 신고·건의 창구입니다.</div>' +
      '<div class="bd-rules"><b>운영 규칙</b><ul>' + RULES.map(function (r) { return "<li>" + esc(r) + "</li>"; }).join("") + "</ul></div>" +
      "</div>";
  }
  function rulesMini() {   // 글쓰기/댓글 폼용 축약 안내
    return '<div class="bd-rules bd-rules-mini"><ul>' + RULES.map(function (r) { return "<li>" + esc(r) + "</li>"; }).join("") +
      '<li><b>개인정보(실명·연락처 등)를 쓰지 마세요 — 작성 내용은 공개됩니다.</b></li></ul></div>';
  }

  function build() {
    ov = document.createElement("div"); ov.id = "board-ov"; ov.className = "hidden";
    ov.innerHTML =
      '<div class="bd-box">' +
      '<div class="bd-h"><span class="bd-title">' + esc(TITLE) + '</span><button class="bd-x" title="닫기(Esc)" aria-label="닫기">✕</button></div>' +
      '<div class="bd-body"></div>' +
      "</div>";
    document.body.appendChild(ov);
    ov.querySelector(".bd-x").addEventListener("click", close);
    ov.addEventListener("pointerdown", function (e) { if (e.target === ov) close(); });
  }
  function bodyEl() { return ov.querySelector(".bd-body"); }
  function openBoard() {   // ⭐오버레이만 — #page 내용·스크롤 미변경. 캐럿은 저장해 두었다가 닫을 때 복원.
    if (!ov) build();
    if (WP.saveRange) { try { WP.saveRange(); } catch (e) {} }
    ov.classList.remove("hidden"); showList();
  }
  function close() {
    if (ov) ov.classList.add("hidden");
    if (WP.restoreRange) { try { WP.restoreRange(); } catch (e) {} }   // 쓰던 위치(캐럿)로 복귀
    if (WP.page) { try { WP.page.focus(); } catch (e) {} }
  }
  function bindBack(b) { var bk = b.querySelector(".bd-back"); if (bk) bk.addEventListener("click", showList); }
  function tagBadge(t) { return '<span class="bd-tag bd-tag-' + (TAGS.indexOf(t) + 1) + '">' + esc(t || "기타") + "</span>"; }
  function itemHtml(p) {
    return '<div class="bd-item" data-id="' + esc(p.id) + '">' + tagBadge(p.tag) +
      '<span class="bd-t">' + (p.private ? "🔒 " : "") + esc(p.title) + "</span>" +
      '<span class="bd-meta">' + esc(p.nick || "익명") + " · " + fmtTime(p.ts) + (p.ncomments ? " · 💬" + p.ncomments : "") + "</span></div>";
  }
  function renderItems(list, posts) {
    if (!posts.length) { list.innerHTML = '<div class="bd-empty">' + (mineOn ? "내가 쓴 글이 없습니다." : "아직 글이 없습니다. 첫 글을 남겨보세요.") + "</div>"; return; }
    list.innerHTML = posts.map(itemHtml).join("");
    Array.prototype.forEach.call(list.querySelectorAll(".bd-item"), function (it) {
      it.addEventListener("click", function () { showPost(it.getAttribute("data-id")); });
    });
  }

  function showList() {
    var b = bodyEl();
    var chips = ["전체"].concat(TAGS).map(function (t) {
      return '<button class="bd-chip' + (t === curTag ? " on" : "") + '" data-tag="' + esc(t) + '">' + esc(t) + "</button>";
    }).join("");
    b.innerHTML = pinnedHtml() +
      '<div class="bd-toolbar">' +
      '<div class="bd-chips">' + chips + "</div>" +
      '<div class="bd-search"><input class="bd-q" placeholder="제목·별명 검색" value="' + esc(curQ) + '">' +
      '<button class="bd-mine' + (mineOn ? " on" : "") + '" title="내가 쓴 글(이 브라우저)">내 글</button>' +
      '<button class="bd-write">＋ 새 글 쓰기</button></div></div>' +
      '<div class="bd-list bd-loading">불러오는 중…</div>';
    Array.prototype.forEach.call(b.querySelectorAll(".bd-chip"), function (c) {
      c.addEventListener("click", function () { curTag = c.getAttribute("data-tag"); mineOn = false; showList(); });
    });
    b.querySelector(".bd-mine").addEventListener("click", function () { mineOn = !mineOn; showList(); });
    b.querySelector(".bd-write").addEventListener("click", function () { showForm(null); });
    var qin = b.querySelector(".bd-q");
    var deb; qin.addEventListener("input", function () { clearTimeout(deb); deb = setTimeout(function () { curQ = qin.value; mineOn = false; loadList(); }, 250); });
    loadList();
  }
  function loadList() {
    var b = bodyEl(); var list = b.querySelector(".bd-list"); if (!list) return;
    list.classList.add("bd-loading"); list.textContent = "불러오는 중…";
    if (mineOn) {
      var ids = getMine();
      if (!ids.length) { list.classList.remove("bd-loading"); renderItems(list, []); return; }
      Promise.all(ids.map(function (id) { return fetch("/api/board/get?id=" + encodeURIComponent(id)).then(function (r) { return r.json(); }).then(function (r) { return r && r.ok ? r.post : null; }).catch(function () { return null; }); }))
        .then(function (arr) {
          list.classList.remove("bd-loading");
          var posts = arr.filter(Boolean).map(function (p) { return { id: p.id, title: p.title, tag: p.tag, nick: p.nick, ts: p.ts, private: p.private, ncomments: (p.comments || []).length }; });
          renderItems(list, posts);
        });
      return;
    }
    var url = "/api/board/list?tag=" + encodeURIComponent(curTag) + "&q=" + encodeURIComponent(curQ);
    fetch(url).then(function (r) { return r.json(); }).then(function (r) {
      list.classList.remove("bd-loading");
      if (!r || !r.ok) { list.textContent = "목록을 불러오지 못했습니다."; return; }
      renderItems(list, r.posts);
    }).catch(function () { list.classList.remove("bd-loading"); list.textContent = "목록을 불러오지 못했습니다(연결?)."; });
  }

  function showPost(id) {
    var b = bodyEl();
    b.innerHTML = '<button class="bd-back">← 목록</button><div class="bd-loading">불러오는 중…</div>';
    bindBack(b);
    fetch("/api/board/get?id=" + encodeURIComponent(id)).then(function (r) { return r.json(); }).then(function (r) {
      if (!r || !r.ok) { b.innerHTML = '<button class="bd-back">← 목록</button><div class="bd-empty">글을 찾을 수 없습니다.</div>'; bindBack(b); return; }
      renderPost(b, r.post);
    }).catch(function () { b.innerHTML = '<button class="bd-back">← 목록</button><div class="bd-empty">불러오지 못했습니다.</div>'; bindBack(b); });
  }
  function renderPost(b, p) {
    var cmts = (p.comments || []).map(function (c) {
      return '<div class="bd-cmt" data-cid="' + esc(c.id) + '"><div class="bd-cmt-m">' + esc(c.nick || "익명") + " · " + fmtTime(c.ts) +
        ' <button class="bd-cmt-del" title="댓글 삭제">삭제</button></div><div class="bd-cmt-b">' + nl2br(c.body) + "</div>" +
        '<div class="bd-cmt-delbar hidden"><input type="password" class="bd-cmt-pw" placeholder="댓글 비밀번호"><button class="bd-cmt-delok">삭제 확인</button></div></div>';
    }).join("");
    b.innerHTML =
      '<button class="bd-back">← 목록</button>' +
      '<div class="bd-view">' +
      '<div class="bd-vtag">' + tagBadge(p.tag) + (p.private ? '<span class="bd-priv">🔒 비공개</span>' : "") + "</div>" +
      '<h3 class="bd-vt">' + esc(p.title) + "</h3>" +
      '<div class="bd-vts">' + esc(p.nick || "익명") + " · " + fmtTime(p.ts) + "</div>" +
      '<div class="bd-vb">' + nl2br(p.body) + "</div>" +
      '<div class="bd-vact"><button class="bd-edit">수정</button><button class="bd-del">삭제</button></div>' +
      '<div class="bd-delbar hidden"><input type="password" class="bd-delpw" placeholder="글 비밀번호"><button class="bd-delok">삭제 확인</button></div>' +
      '<div class="bd-cmts"><div class="bd-cmts-h">댓글 ' + (p.comments || []).length + "</div>" + cmts +
      '<div class="bd-cform"><input class="bd-cnick" maxlength="24" placeholder="별명(선택)"><textarea class="bd-cbody" maxlength="1000" rows="2" placeholder="댓글 (개인정보 금지)"></textarea>' +
      '<input type="password" class="bd-cpw" placeholder="비밀번호"><button class="bd-csubmit">댓글 등록</button></div>' +
      "</div></div>";
    bindBack(b);
    b.querySelector(".bd-edit").addEventListener("click", function () { showForm(p); });
    // 글 삭제(인라인 비번)
    var delbar = b.querySelector(".bd-delbar");
    b.querySelector(".bd-del").addEventListener("click", function () { delbar.classList.toggle("hidden"); });
    b.querySelector(".bd-delok").addEventListener("click", function () {
      var pw = b.querySelector(".bd-delpw").value;
      api("delete", { id: p.id, password: pw }).then(function (r) {
        if (r && r.ok) { delMine(p.id); toast("삭제되었습니다."); showList(); }
        else toast((r && r.err) || "삭제 실패");
      }).catch(function () { toast("삭제 실패(연결?)"); });
    });
    // 댓글 등록
    b.querySelector(".bd-csubmit").addEventListener("click", function () {
      var body = b.querySelector(".bd-cbody").value.trim(), pw = b.querySelector(".bd-cpw").value, nick = b.querySelector(".bd-cnick").value;
      if (!body) { toast("댓글 내용을 입력하세요."); return; }
      if (!pw) { toast("비밀번호를 입력하세요(삭제에 필요)."); return; }
      api("comment", { postId: p.id, nick: nick, body: body, password: pw }).then(function (r) {
        if (r && r.ok) { toast("댓글이 등록되었습니다."); showPost(p.id); }
        else toast((r && r.err) || "댓글 등록 실패");
      }).catch(function () { toast("댓글 등록 실패(연결?)"); });
    });
    // 댓글 삭제(인라인 비번)
    Array.prototype.forEach.call(b.querySelectorAll(".bd-cmt"), function (cm) {
      var cid = cm.getAttribute("data-cid"), bar = cm.querySelector(".bd-cmt-delbar");
      cm.querySelector(".bd-cmt-del").addEventListener("click", function () { bar.classList.toggle("hidden"); });
      cm.querySelector(".bd-cmt-delok").addEventListener("click", function () {
        var pw = cm.querySelector(".bd-cmt-pw").value;
        api("comment-delete", { postId: p.id, commentId: cid, password: pw }).then(function (r) {
          if (r && r.ok) { toast("댓글이 삭제되었습니다."); showPost(p.id); }
          else toast((r && r.err) || "댓글 삭제 실패");
        }).catch(function () { toast("댓글 삭제 실패(연결?)"); });
      });
    });
  }

  function showForm(edit) {   // edit=null 신규 / edit=post 수정
    var b = bodyEl();
    var tagOpts = TAGS.map(function (t) { return '<option value="' + esc(t) + '"' + (edit && edit.tag === t ? " selected" : "") + ">" + esc(t) + "</option>"; }).join("");
    b.innerHTML = '<button class="bd-back">← 목록</button>' +
      '<h3 class="bd-fh">' + (edit ? "글 수정" : "새 글 쓰기") + "</h3>" + rulesMini() +
      '<div class="bd-form">' +
      '<input class="bd-title-in" maxlength="200" placeholder="제목" value="' + (edit ? esc(edit.title) : "") + '" autocomplete="off">' +
      '<div class="bd-frow"><select class="bd-tag-in">' + tagOpts + "</select>" +
      '<input class="bd-nick-in" maxlength="24" placeholder="별명(선택, 비우면 익명)" value="' + (edit ? esc(edit.nick === "익명" ? "" : edit.nick) : "") + '" autocomplete="off"></div>' +
      '<textarea class="bd-content" maxlength="4000" rows="9" placeholder="내용 (버그 신고·건의 등 / 개인정보 금지)">' + (edit ? esc(edit.body) : "") + "</textarea>" +
      '<div class="bd-frow"><input type="password" class="bd-pw-in" maxlength="128" placeholder="비밀번호 (수정·삭제에 필요)" autocomplete="new-password">' +
      '<label class="bd-privlbl"><input type="checkbox" class="bd-priv-in"' + (edit && edit.private ? " checked" : "") + "> 비공개(나·관리자만)</label></div>" +
      '<div class="bd-actions"><button class="bd-submit">' + (edit ? "수정 저장" : "등록") + "</button></div></div>";
    bindBack(b);
    b.querySelector(".bd-submit").addEventListener("click", function () {
      var title = b.querySelector(".bd-title-in").value.trim(), content = b.querySelector(".bd-content").value.trim(),
          pw = b.querySelector(".bd-pw-in").value, nick = b.querySelector(".bd-nick-in").value,
          tag = b.querySelector(".bd-tag-in").value, priv = b.querySelector(".bd-priv-in").checked;
      if (!title || !content) { toast("제목과 내용을 모두 입력하세요."); return; }
      if (!pw) { toast(edit ? "비밀번호를 입력하세요." : "비밀번호를 입력하세요(수정·삭제에 필요)."); return; }
      var btn = b.querySelector(".bd-submit"); btn.disabled = true; btn.textContent = "저장 중…";
      var done = function (r) {
        if (r && r.ok) { if (!edit) addMine(r.id); toast(edit ? "수정되었습니다." : "등록되었습니다."); if (edit) showPost(edit.id); else showList(); }
        else { toast((r && r.err) || "저장에 실패했습니다."); btn.disabled = false; btn.textContent = edit ? "수정 저장" : "등록"; }
      };
      if (edit) api("edit", { id: edit.id, password: pw, title: title, body: content, tag: tag, private: priv }).then(done).catch(function () { toast("저장 실패(연결?)"); btn.disabled = false; btn.textContent = "수정 저장"; });
      else api("post", { title: title, body: content, tag: tag, nick: nick, password: pw, private: priv }).then(done).catch(function () { toast("저장 실패(연결?)"); btn.disabled = false; btn.textContent = "등록"; });
    });
  }

  document.addEventListener("keydown", function (e) {
    if (ov && !ov.classList.contains("hidden") && e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); }
  }, true);
  WP.register({ board: openBoard });
})();
