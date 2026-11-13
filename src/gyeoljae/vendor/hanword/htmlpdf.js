// htmlpdf.js — 에디터 본문 HTML(stdin) → A4 PDF(stdout). Playwright Chromium page.pdf().
//   에디터와 같은 스타일(한글폰트·제목크기·표)로 렌더 → WYSIWYG PDF.
//   ⚠️폰트는 한워드 서버의 /font 를 http로 로드(Chromium은 file:// 차단, 그 폰트엔 CORS 필요).
//     주소 = WP_FONT_URL(서버가 자식 프로세스에 넘김) > http://127.0.0.1:$WP_PORT/font > 기본 포트 8088.
//   사용: WP_PDF_LOCAL=1 WP_HTMLPDF=<경로>/htmlpdf.js (node + playwright 필요, 선택 기능)
const { chromium } = require('playwright');
const fs = require('fs');

(async () => {
  const FBASE = process.env.WP_FONT_URL || ('http://127.0.0.1:' + (process.env.WP_PORT || '8088') + '/font');
  const NANUM_TTF = { 'NanumGothic-400.woff2': 'NanumGothic-Regular.ttf', 'NanumGothic-700.woff2': 'NanumGothic-Bold.ttf',
                      'NanumMyeongjo-400.woff2': 'NanumMyeongjo-Regular.ttf', 'NanumMyeongjo-700.woff2': 'NanumMyeongjo-Bold.ttf' };   // 공개·임베드 빌드 = 원본 TTF(웹 서브셋 미배포)
  const ff  = (fam, file, w) => `@font-face{font-family:'${fam}';${w?'font-weight:'+w+';':''}src:url('${FBASE}/${file}') format('woff2')${NANUM_TTF[file] ? `, url('${FBASE}/${NANUM_TTF[file]}') format('truetype')` : ''};}`;
  // NotoKR=풀 NotoSansKR.ttf(원문자 ①·화살표 →·한자까지 커버 → 모든 스택 끝 만능 폴백)
  const FONTFACE = [
    `@font-face{font-family:'NotoKR';src:url('${FBASE}/NotoSansKR.ttf') format('truetype');}`,
    ff('Noto Sans KR','NotoSansKR-400.woff2',400),   ff('Noto Sans KR','NotoSansKR-700.woff2',700),
    ff('Noto Serif KR','NotoSerifKR-400.woff2',400), ff('Noto Serif KR','NotoSerifKR-700.woff2',700),
    ff('Pretendard','Pretendard-400.woff2',400),     ff('Pretendard','Pretendard-700.woff2',700),
    ff('NanumGothic','NanumGothic-400.woff2',400),   ff('NanumGothic','NanumGothic-700.woff2',700),
    ff('NanumMyeongjo','NanumMyeongjo-400.woff2',400),ff('NanumMyeongjo','NanumMyeongjo-700.woff2',700),
    ff('D2Coding','D2Coding-400.woff2',400),         ff('D2Coding','D2Coding-700.woff2',700),
    // KTUG 배포: 함초롬체LVT(자유배포)·은바탕(GPL) — 한자/옛한글 서브셋 제외(→NotoKR 폴백)
    ff('함초롬바탕','Hamchorom-Batang-400.woff2',400), ff('함초롬바탕','Hamchorom-Batang-700.woff2',700),
    ff('함초롬돋움','Hamchorom-Dotum-400.woff2',400),  ff('함초롬돋움','Hamchorom-Dotum-700.woff2',700),
    ff('UnBatang','UnBatang-400.woff2',400),         ff('UnBatang','UnBatang-700.woff2',700),
    // 독점 폰트명 별칭 → 가장 가까운 오픈 폰트(이름은 HWPX에 원본 보존, 한글에선 정품)
    ff('맑은 고딕','Pretendard-400.woff2',400),       ff('맑은 고딕','Pretendard-700.woff2',700),
    ff('굴림','NotoSansKR-400.woff2'),  ff('돋움','NotoSansKR-400.woff2'),  ff('HY견고딕','NotoSansKR-700.woff2'),
    ff('바탕','NotoSerifKR-400.woff2'), ff('궁서','NotoSerifKR-400.woff2'),
    ff('나눔고딕','NanumGothic-400.woff2'), ff('나눔명조','NanumMyeongjo-400.woff2'),
  ].join('');

  let body = fs.readFileSync(0, 'utf-8');                   // stdin = 본문 HTML
  // 머리말/꼬리말/쪽번호 메타(숨김 div) 추출 + 본문에서 제거
  function unesc(s){ return String(s).replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&quot;/g,'"').replace(/&#39;/g,"'").replace(/&amp;/g,'&'); }
  var meta = { header:'', footer:'', pagenum:'', pnFormat:'1', pnStart:1, headerOdd:'', headerEven:'' };
  const PAPERS = { A3:[297,420], A4:[210,297], A5:[148,210], A6:[105,148], B4:[257,364], B5:[182,257], B6:[128,182], Letter:[215.9,279.4], Legal:[215.9,355.6], Tabloid:[279.4,431.8] };
  body = body.replace(/<div id="wpmeta"([^>]*)>[\s\S]*?<\/div>/, function(_, a){
    var g = function(k){ var m = a.match(new RegExp('data-'+k+'="([^"]*)"')); return m ? unesc(m[1]) : ''; };
    meta.header = g('header'); meta.footer = g('footer'); meta.pagenum = g('pagenum');
    meta.pnFormat = g('pagenum-format') || '1';
    var st = parseInt(g('pagenum-start'), 10); meta.pnStart = st >= 1 ? st : 1;
    meta.headerOdd = g('header-odd'); meta.headerEven = g('header-even');
    // #26 쪽 테두리 — 선종류 화이트리스트(신뢰불가 문서의 style 주입 차단)·굵기 1~3px
    var pb = g('pageborder');
    meta.pageBorder = /^(solid|dotted|dashed|double)$/.test(pb) ? pb : '';
    meta.pageBorderW = Math.min(3, Math.max(1, parseInt(g('pageborder-w'), 10) || 1));
    // 용지·여백(2026-11-13, 공문서 30/20/15/15 등) — 편집기 wpmeta 규칙과 같은 화이트리스트, 허용 밖 = A4·20mm
    var pp = g('paper'), mg = g('margins');
    if (Object.prototype.hasOwnProperty.call(PAPERS, pp)) meta.paper = pp;
    if (g('orient') === 'landscape') meta.landscape = true;
    if (/^\d{1,3}(\.\d)?(,\d{1,3}(\.\d)?){3}$/.test(mg)) {
      var a = mg.split(',').map(Number);
      if (a.every(function (x) { return x >= 0 && x <= 100; })) meta.margins = a;
    }
    return '';
  });
  // 쪽 테두리: position:fixed 요소는 Chromium 인쇄에서 모든 쪽에 반복 → 여백 안 본문 영역 사각형
  if (meta.pageBorder)
    body = '<div style="position:fixed;left:0;top:0;right:0;bottom:0;border:' + meta.pageBorderW + 'px ' + meta.pageBorder + ' #445;"></div>' + body;
  // #21 확장 메타(형식/시작/홀짝)는 Chromium 네이티브 footer로 불가(아라비아 고정·홀짝 분기 불가)
  //   → 머리말/꼬리말 없이 렌더 후 pdf-lib로 쪽번호·머리말 스탬핑. 기본 문서는 기존 네이티브 경로(회귀 0).
  var extended = (meta.pnFormat !== '1') || (meta.pnStart > 1) || !!meta.headerOdd || !!meta.headerEven;
  // 쪽번호 표기(1/①/i/가) — wp-io.js fmtPageNum과 동일 로직 사본(동기 유지)
  function fmtPageNum(n, f){
    if (!(n >= 1) || !f || f === '1') return String(n);
    if (f === 'circ'){
      if (n <= 20) return String.fromCharCode(0x2460 + n - 1);
      if (n <= 35) return String.fromCharCode(0x3251 + n - 21);
      if (n <= 50) return String.fromCharCode(0x32B1 + n - 36);
      return String(n);
    }
    if (f === 'roman'){
      var v=[1000,900,500,400,100,90,50,40,10,9,5,4,1], s=['m','cm','d','cd','c','xc','l','xl','x','ix','v','iv','i'], out='';
      for (var i=0;i<v.length;i++) while (n>=v[i]){ out+=s[i]; n-=v[i]; }
      return out;
    }
    if (f === 'hangul'){ var H='가나다라마바사아자차카타파하', ch=H[(n-1)%14]; return new Array(Math.floor((n-1)/14)+2).join(ch); }
    return String(n);
  }
  // 확장 PDF에 쪽번호·머리말 스탬핑(pdf-lib). 실패 시 throw → 호출부가 네이티브 경로 폴백.
  async function stampPdf(raw, meta){
    const { PDFDocument, rgb } = require('pdf-lib');
    const fontkit = require('@pdf-lib/fontkit');
    const path = require('path');
    const cands = [process.env.WP_FONT, path.join(__dirname, 'font', 'NotoSansKR.ttf'),
                   path.join(process.env.HOME || '', 'wp', 'font', 'NotoSansKR.ttf')].filter(Boolean);
    let fontBytes = null;
    for (const p of cands) { try { fontBytes = fs.readFileSync(p); break; } catch (e) {} }
    if (!fontBytes) throw new Error('스탬핑 폰트 없음: ' + cands.join(', '));
    const pdfDoc = await PDFDocument.load(raw);
    pdfDoc.registerFontkit(fontkit);
    const font = await pdfDoc.embedFont(fontBytes, { subset: true });
    const SIZE = 7.5, GRAY = rgb(0x55/255, 0x55/255, 0x55/255), PADX = 51;   // 좌우 18mm(네이티브 템플릿과 동일)
    const pages = pdfDoc.getPages();
    for (let i = 0; i < pages.length; i++){
      const pg = pages[i], w = pg.getWidth(), h = pg.getHeight();
      const num = meta.pnStart + i;
      const headTxt = (meta.headerOdd || meta.headerEven) ? (num % 2 === 1 ? meta.headerOdd : meta.headerEven) : meta.header;
      if (headTxt){
        const tw = font.widthOfTextAtSize(headTxt, SIZE);
        pg.drawText(headTxt, { x: (w - tw) / 2, y: h - 34, size: SIZE, font, color: GRAY });
      }
      let footTxt = meta.footer || '';
      if (meta.pagenum) footTxt = (footTxt ? footTxt + ' ' : '') + fmtPageNum(num, meta.pnFormat);
      if (footTxt){
        const tw = font.widthOfTextAtSize(footTxt, SIZE);
        const x = meta.pagenum === 'left' ? PADX : meta.pagenum === 'right' ? w - PADX - tw : (w - tw) / 2;
        pg.drawText(footTxt, { x, y: 28, size: SIZE, font, color: GRAY });
      }
    }
    return Buffer.from(await pdfDoc.save());
  }
  const doc = `<!doctype html><html><head><meta charset="utf-8"><style>
${FONTFACE}
*{box-sizing:border-box;}
body{font-family:'NotoKR','Noto Sans KR',sans-serif;font-size:11pt;line-height:1.6;color:#111;margin:0;word-break:break-word;}
h1{font-size:20pt;font-weight:700;margin:.4em 0 .3em;}
h2{font-size:16pt;font-weight:700;margin:.4em 0 .3em;}
h3{font-size:13pt;font-weight:700;margin:.4em 0 .3em;}
p{margin:.25em 0;} ul,ol{margin:.25em 0;padding-left:1.6em;}
ol{list-style-type:decimal;} ol ol{list-style-type:lower-alpha;} ol ol ol{list-style-type:lower-roman;}
ul{list-style-type:disc;} ul ul{list-style-type:circle;} ul ul ul{list-style-type:square;}
.pgbreak{break-after:page;height:0;border:0;}
p.caption{font-size:9pt;color:#445;margin:.15em 0 .6em;}
.toc{border:1px solid #d5dbe2;border-radius:4px;padding:10px 14px;margin:.4em 0;}
.toc .toc-t{font-weight:700;font-size:13pt;margin:.1em 0 .45em;}
.toc .toc-e{display:flex;align-items:baseline;gap:6px;margin:.15em 0;}
.toc .toc-e::after{content:"";flex:1 1 auto;order:1;border-bottom:1px dotted #9aa4b0;margin:0 2px 3px;}
.toc .toc-e .tp{order:2;color:#445;}
.toc .toc-l2{padding-left:1.2em;} .toc .toc-l3{padding-left:2.4em;}
sup.fn{color:#1d4ed8;font-size:.72em;}
.fnotes{margin-top:1.2em;border-top:1px solid #9aa4b0;padding-top:.4em;}
.fnotes .fn-t{font-weight:700;font-size:11pt;color:#334;margin:.1em 0 .3em;}
.fnotes .fn-e{font-size:10pt;color:#223;margin:.15em 0;}
.fnotes .fn-no{color:#1d4ed8;}
table{border-collapse:collapse;margin:.4em 0;} td{border:1px solid #555;padding:4px 8px;}
img{max-width:100%;}
</style></head><body>${body}</body></html>`;
  const PW = (function () {   // 용지 치수·여백(mm) → page.pdf 옵션
    var wh = PAPERS[meta.paper || 'A4'], w = wh[0], h = wh[1];
    if (meta.landscape) { var t = w; w = h; h = t; }
    var m = meta.margins || [20, 20, 20, 20];   // 위,오른쪽,아래,왼쪽
    return { width: w + 'mm', height: h + 'mm', margin: { top: m[0] + 'mm', right: m[1] + 'mm', bottom: m[2] + 'mm', left: m[3] + 'mm' } };
  })();
  const browser = await chromium.launch({ args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ javaScriptEnabled: false });   // 신뢰불가 문서의 스크립트 실행 차단
    // SSRF/렌더지연 차단: 신뢰불가 문서가 참조하는 외부 리소스는 로드하지 않음 — 폰트 서버(FBASE)만 허용
    const fontPrefix = FBASE.replace(/\/+$/, '') + '/';   // 폰트 경로(/font/)만 허용 — 같은 서버의 다른 경로(API 등)도 차단
    await page.route('**/*', (route) => {
      const u = route.request().url();
      if (u.startsWith(fontPrefix) || u.startsWith('about:')) return route.continue();
      return route.abort();
    });
    // networkidle은 외부 리소스 hang에 취약 → load + document.fonts.ready로 대체
    await page.setContent(doc, { waitUntil: 'load', timeout: 30000 });
    try { await page.evaluate(() => document.fonts.ready); } catch (e) {}   // @font-face 로드 완료 대기(폰트 임베드 보장)
    await page.emulateMedia({ media: 'print' });
    if (extended){   // #21 확장 경로: HF 없이 렌더 → 스탬핑. 실패(pdf-lib 부재 등) 시 네이티브 폴백.
      try {
        const raw = await page.pdf({ width: PW.width, height: PW.height, printBackground: true, margin: PW.margin });
        process.stdout.write(await stampPdf(raw, meta));
        return;   // finally에서 browser.close()
      } catch (e) { process.stderr.write('[htmlpdf] 스탬핑 폴백(네이티브 아라비아): ' + e + '\n'); }
    }
    function esc(s){ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
    var hasHF = !!(meta.header || meta.footer || meta.pagenum || meta.headerOdd || meta.headerEven);
    var alignMap = { center:'center', right:'right', left:'left' };
    var pnAlign = alignMap[meta.pagenum] || '';
    var footInner = esc(meta.footer);
    if (pnAlign) footInner = (footInner ? footInner + ' ' : '') + '<span class="pageNumber"></span>';
    var hT = '<div style="font-size:9px;width:100%;padding:0 18mm;text-align:center;color:#555;">' + esc(meta.header) + '</div>';
    var fT = '<div style="font-size:9px;width:100%;padding:0 18mm;text-align:' + (pnAlign || 'center') + ';color:#555;">' + footInner + '</div>';
    const pdf = await page.pdf({
      width: PW.width, height: PW.height, printBackground: true,
      displayHeaderFooter: hasHF, headerTemplate: hT, footerTemplate: fT,
      margin: PW.margin
    });
    process.stdout.write(pdf);
  } finally {
    await browser.close();
  }
})().catch(e => { process.stderr.write(String(e && e.stack || e)); process.exit(1); });
