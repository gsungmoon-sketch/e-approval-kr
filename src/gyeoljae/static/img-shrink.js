/* gyeoljae: 편집기 HTML 속 사진(data:image) 줄이기 — 저장·상신 전에 기안 화면(editor-host.js)이 부른다.
   긴 변 1600px 넘으면 줄이고, JPEG(품질 0.85)로 다시 담아 작아질 때만 바꾼다. 투명 PNG 는 PNG 로(줄일 때만),
   GIF(움직이는 그림)는 그대로. 실패하면 원본 그대로(서버 상한이 최종 판정: 사진 포함 8MB).
   CSP(img-src 'self')에 data: 를 열지 않도록 <img> 대신 Blob → createImageBitmap 으로 디코드한다.
   window.gyeolShrink.html(html, done(newHtml, stats)) / .decide(origLen, outLen, scaled) 순수 판정(시험용). */
(function () {
  'use strict';
  var MAX_EDGE = 1600, JPEG_Q = 0.85;
  var SRC_RE = /(<img\b[^>]*?\bsrc\s*=\s*)(["']?)(data:image\/(png|jpe?g|webp|bmp|gif);base64,([A-Za-z0-9+\/=]+))\2/gi;

  // 바꿀지: 크기 상한을 넘어 줄였으면 늘 바꾸고, 아니면 더 작아질 때만.
  function decide(origLen, outLen, scaled) { return !!outLen && (scaled || outLen < origLen); }

  function toBlob(b64, type) {
    var bin = atob(b64), n = bin.length, a = new Uint8Array(n);
    for (var i = 0; i < n; i++) a[i] = bin.charCodeAt(i);
    return new Blob([a], {type: type});
  }
  function decode(blob) {
    try { return createImageBitmap(blob, {imageOrientation: 'from-image'}); }
    catch (x) { return createImageBitmap(blob); }
  }
  function hasAlpha(g, w, h) {
    var d = g.getImageData(0, 0, w, h).data;
    for (var i = 3; i < d.length; i += 4) if (d[i] < 255) return true;
    return false;
  }

  function one(src, kind, b64, done0) {
    var fired = false;
    function done(v) { if (!fired) { fired = true; done0(v); } }  // 성공·실패 어느 쪽이든 한 번만
    if (kind === 'gif' || typeof createImageBitmap !== 'function') return done(src);
    var p;
    try { p = decode(toBlob(b64, 'image/' + (kind === 'jpg' ? 'jpeg' : kind))); } catch (x) { return done(src); }
    Promise.resolve(p).then(function (bmp) {
      var w = bmp.width, h = bmp.height;
      if (!w || !h) return done(src);
      var k = Math.min(1, MAX_EDGE / Math.max(w, h)), scaled = k < 1;
      var c = document.createElement('canvas');
      c.width = Math.max(1, Math.round(w * k)); c.height = Math.max(1, Math.round(h * k));
      var g = c.getContext('2d');
      g.drawImage(bmp, 0, 0, c.width, c.height);
      if (bmp.close) bmp.close();
      var out;
      if (kind !== 'jpeg' && kind !== 'jpg' && hasAlpha(g, c.width, c.height)) {
        if (!scaled) return done(src);            // 투명 그림: 줄일 필요 없으면 그대로
        out = c.toDataURL('image/png');
      } else {
        var flat = document.createElement('canvas');  // JPEG 는 투명 없음 → 흰 바탕
        flat.width = c.width; flat.height = c.height;
        var f = flat.getContext('2d');
        f.fillStyle = '#fff'; f.fillRect(0, 0, flat.width, flat.height); f.drawImage(c, 0, 0);
        out = flat.toDataURL('image/jpeg', JPEG_Q);
      }
      done(decide(src.length, out && out.indexOf('data:image/') === 0 ? out.length : 0, scaled) ? out : src);
    }).then(null, function () { done(src); });  // 디코드·캔버스(getImageData·toDataURL 메모리 등) 실패 → 원본
  }

  function shrinkHtml(html, done) {
    var found = {}, list = [], m;
    SRC_RE.lastIndex = 0;
    while ((m = SRC_RE.exec(html))) {
      if (!found[m[3]]) { found[m[3]] = true; list.push([m[3], m[4].toLowerCase(), m[5]]); }
    }
    var stats = {images: list.length, changed: 0, before: html.length, after: html.length};
    if (!list.length) return done(html, stats);
    var map = {}, left = list.length;
    list.forEach(function (it) {
      one(it[0], it[1], it[2], function (out) {
        map[it[0]] = out;
        if (out !== it[0]) stats.changed++;
        if (--left) return;
        var res = html.replace(SRC_RE, function (all, pre, q, src) {
          return map[src] && map[src] !== src ? pre + q + map[src] + q : all;
        });
        stats.after = res.length;
        done(res, stats);
      });
    });
  }

  window.gyeolShrink = {html: shrinkHtml, decide: decide, MAX_EDGE: MAX_EDGE, JPEG_Q: JPEG_Q};
})();
