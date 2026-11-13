# Embedding hanword (iframe editor)

A host app (for example an e-approval system) can use hanword as its document editor. The host serves
a copy of hanword on **its own origin** (for example under `/editor/`) and shows it in an iframe:

```html
<iframe id="ed" src="/editor/embed.html"></iframe>
```

**Vendoring:** make the copy with `python3 scripts/export_embed.py DST` (from a tagged checkout). It
copies the committed files minus `.publicignore` (no internal notes, no fonts that may not be
redistributed) and checks the font list, `embed.html` and that no internal host names or paths are left.
Shipped fonts and their licences: `licenses/README.md`. `editor.css` still declares the unshipped families
(함초롬, 은바탕; Nanum uses the shipped original TTFs instead of the web subsets); those requests 404 and the browser falls back to the shipped fonts.

**Entry: `embed.html`.** It is generated from `index.html` by `python3 scripts/build_embed.py` (tests fail if
it is stale) and is meant to be vendored as is: no SEO/public-host lines (canonical, og:, twitter:, ld+json,
site verification), `robots noindex`, and **no inline `<script>`**, so a host can serve it with
`script-src 'self'` and no nonce. `<html data-wp-embed>` turns embed mode on without a query string.
`/?embed=1` (index.html) still works but carries the public SEO lines; hosts should use `embed.html`.

Asset paths in `index.html` are relative, so a sub-path works as long as the URL ends with `/`
(`/editor/embed.html`). When hanword's own `server.py` serves the page, `/embed.html` or `?embed=1`
switches the page headers to `frame-ancestors 'self'` / `X-Frame-Options: SAMEORIGIN`; every other page
stays `DENY`. A host that serves the static files itself must send equivalent headers.

## What `?embed=1` changes

- One in-memory document. No server storage: the file menu only has browser print; new/open/save/save as/
  HWPX/PDF export, document list, delete, rename, board, history password and media menus and their
  shortcuts are removed; the document name and autosave fields are hidden.
- **No header/footer/page numbers.** The host prints company, document number, approval box and page
  numbers itself. The header/footer and page-number menus and shortcuts (Ctrl+N,H / Ctrl+N,P) are removed,
  the actions are blocked, and any header/footer/page-number data in `#wpmeta` is dropped on `load` and
  removed before every `get` reply.
- The editor makes **no `/api/` requests** (the API helper refuses in embed mode). Images are inserted
  from the user's computer as `data:` URIs.
- Public-mode extras (`WP_PUBLIC`: guide page, leave-page warning, download menu) are not loaded.

## postMessage protocol

Both sides use `targetOrigin = location.origin` (never `'*'`). The editor accepts a message only when
`event.origin === location.origin && event.source === window.parent`; anything else is ignored silently.

| direction | message | meaning |
|---|---|---|
| editor → host | `{type:'ready'}` | sent once, after the editor has started (window `load`) |
| host → editor | `{type:'load', html, readOnly, page, id}` | replace the document. `html` string (max 32 MB); `readOnly` `true` = view only; `page` optional, see below; `id` optional string/number echoed back in `html` |
| host → editor | `{type:'get'}` | ask for the document |
| editor → host | `{type:'html', html, page, id}` | reply to `get`: cleaned editor HTML, the paper setup `{paper, orient, mt, mr, mb, ml}` (mm) and the `id` of the last `load`. Check `id` before saving |
| editor → host | `{type:'error', reason}` | `load` failed (`too-large`, `load-failed`). The editor is then empty and read-only; do not save from it |
| editor → host | `{type:'dirty'}` | sent once on the first change after each `load` or `get` |

`page` is `"gov"`, `"default"` or an object `{preset, paper, orient, mt, mr, mb, ml}`: `preset` first,
then the other keys override it. `paper` ∈ A3 A4 A5 A6 B4 B5 B6 Letter Legal Tabloid, `orient` ∈
portrait/landscape, margins are numbers 0–100 mm; invalid values fall back to A4 portrait, 20 mm.
Without `page`, the paper setup stored in the html (`#wpmeta`, see below) is used, otherwise the default.
Store the `page` you get back next to the html and pass it on the next `load`. Never save the html of a read-only session, and only save an `html` reply whose `id` matches the document you loaded.

`"gov"` = Korean government draft document, A4 portrait, top 30, left 20, right 15, bottom 15 mm. The same
preset is in the editor's page-setup dialog (편집 용지 → 여백 미리 설정).

Links in the document never navigate the editor frame (Ctrl/⌘-click opens http(s)/mailto in a new tab with `noopener`). Drag-and-drop into the editor is blocked (paste goes through the allowlist).

Read-only: the page is not editable, the format toolbar is hidden, only copy/select all/find/view/
print/word-count actions run, and no `dirty` is sent.

Minimal host:

```js
const ed = document.getElementById('ed');
const send = m => ed.contentWindow.postMessage(m, location.origin);
addEventListener('message', e => {
  if (e.origin !== location.origin || e.source !== ed.contentWindow) return;
  if (e.data.type === 'ready') send({type: 'load', html: savedHtml, page: savedPage || 'gov'});
  if (e.data.type === 'dirty') markUnsaved();
  if (e.data.type === 'html')  save(e.data.html, e.data.page);
});
saveButton.onclick = () => send({type: 'get'});
```

## Sanitising

Loaded html (and paste in embed mode) goes through an allowlist in `wp-embed.js`. It is parsed in an
inert document (DOMParser) and only allowed nodes are copied into the editor, so nothing is re-parsed.

- elements: p div span br hr b strong i em u s strike del sub sup h1–h6 ul ol li table thead tbody tfoot
  tr td th caption colgroup col a img blockquote pre code font. script/style/iframe/object/svg/math/form/
  media and similar are removed with their content; other unknown elements are unwrapped.
- attributes: style, class, title, align on all; colspan rowspan valign width height (td/th), border
  cellpadding cellspacing width (table), span width (col), src alt width height (img), href (a), face
  size color (font), start type (ol), value (li). Everything else, including `id` and `on*`, is dropped.
- **data-\***: only `data-field`, `data-row`, `data-sum`, only on td, th, tr, table and span. A value is a
  name or a comma-separated list of up to 16 names; a name is 1–64 characters from `A–Z a–z 0–9 _ . -` and
  Hangul (no spaces, brackets, quotes, `:`). They are names, not expressions: **the host must never
  evaluate them** (no `eval`/`new Function`); look names up in its own table. They survive editing,
  table split and `get` (an empty `<span data-field>` is kept too).
- class tokens: pgbreak, objline, wp-hr, caption. style: an allowlist of text, colour, spacing, border,
  size and page-break properties; values with `url(`, `expression`, `\`, `@`, `<`, comments etc. are
  dropped. `display`, `position`, `float`, `opacity` are not allowed.
- img `src`: only `data:image/(png|jpeg|gif|webp|bmp);base64`. No external or same-origin URLs, so a
  document cannot make the browser request host URLs. a `href`: `http(s):`, `mailto:`, `#` only.
- `#wpmeta` (editor page meta) is dropped; only its paper/orientation/margins are read (validated).
  Headers/footers/page numbers stored there are not carried over.
- Tables get the editor's own wrapper (`div.objline` + `data-place="char"`); floating objects, shapes,
  footnote links and media lose their editor-specific attributes and come back as plain content.

`hanword_embed.sanitize_html(html)` applies the same rules on the server. **Store its output as the
canonical document** and show/convert only that: the output has no raw-text elements, so a browser reading
it again sees exactly the same content (script/style are removed from the first opening to the last
closing tag, `<title/>`-style tricks are removed with their content).

## Converters (Python, no server)

```python
import sys; sys.path.insert(0, "vendor/hanword")
from hanword_embed import html_to_hwpx, html_to_docx, html_to_pdf, sanitize_html, normalize_page, GOV_PAGE

hwpx = html_to_hwpx(html, page=page)     # bytes (.hwpx)
docx = html_to_docx(html, page=page)     # bytes (.docx)
pdf  = html_to_pdf(html, page=page)      # bytes (.pdf), needs node + playwright
```

- `page` is the same value as in the protocol (`"gov"`, dict, or `None` = use the html's `#wpmeta`, then
  A4 20 mm). Pass the `page` object from the editor's `html` reply.
- By default the converters run `sanitize_html` first (`sanitize=False` to convert trusted editor output
  as is, keeping editor-only features such as floating objects); the paper setup is read from `#wpmeta`
  before sanitising.
- HWPX/DOCX use only the Python standard library. Bad input raises `hanword_embed.InputError`
  (a `ValueError`).
- PDF runs `node htmlpdf.js` (Chromium via Playwright; set `WP_NODE_PATH` or pass `node_path=` for the
  `node_modules` folder). Fonts are served from the `font/` folder by a short-lived server on a random
  127.0.0.1 port; calls are serialised. The renderer runs with JavaScript off and blocks all requests
  except those fonts. Raises `RuntimeError` on failure.

## Tests

- `python3 -m unittest tests/test_embed.py` — module without a server, gov preset in HWPX/DOCX/PDF,
  server sanitizer, `?embed=1` headers.
- `NODE_PATH=<playwright node_modules> node tests/e2e-embed.js` (development tree only) — browser: menus, no `/api` calls, origin and
  source checks, XSS on load, data-* edit/save round trip, dirty rule, read-only, preset.
