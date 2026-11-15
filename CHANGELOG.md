# Changelog

## 0.2.6
- Bundled hanword editor updated to 0.1.11 (re-vendored with `scripts/vendor_hanword.py`; hash-checked as before). New in the editor since 0.1.9: a 한글97-style two-row toolbar (standard and formatting rows with style, font, font-size box, bold/italic/underline, text colour, alignment, line spacing; overflow under »; the previous layout stays available under 보기 › 도구 모음), table formulas with a real recalculation budget, a document statistics dialog, find/replace regex fixes, Backspace/Delete inside table cells, linear-time import of nested elements, DOCX XML size/element caps, and a server sanitizer matching the browser parser. In the approval editor the toolbar has no New/Open/Save buttons (saving and submitting stay on the gyeoljae page); the toolbar script is served from the program itself under the existing editor CSP (`script-src 'self'`).
- Table formulas: the body sanitizer keeps `data-formula` on `td`/`th` with the same fixed grammar as the editor (`SUM|AVG(ABOVE|BELOW|LEFT|RIGHT|A1|A1:B3)`). The editor computes the value and stores it as the cell text; the server never evaluates formulas, so saved, submitted and completed documents and HWPX/DOCX downloads carry the computed value. Circular references are only marked `#순환` on screen; the cell is left empty and the mark is not saved. The editor stops recalculating at its budget (300 formula cells per table, bounded evaluations and cell reads per recalculation) and keeps the previous values; as defence in depth the server keeps the values but drops the formulas when one table has more than 300 formula cells, the sum over tables of formula cells × cells exceeds 2,000,000, or the whole document has more than 3,000 formula cells or formula cells × cells over 20,000,000 (formulas spread over several tables are kept).
- Body sanitizer: `<frame>` is treated as a void element, `<plaintext>` drops everything after it, `/>` on dropped elements such as `<title/>` is ignored, and only the first of duplicate attributes counts, as in browsers and hanword.
- Footnotes: the editor's footnote/endnote markup and its footnote export option are editor-only and are not kept in approval documents; note text stays as ordinary paragraphs (unchanged behaviour).
- Optional OnlyOffice Document Server integration (off by default): view and edit docx/xlsx/pptx attachments and library files in the browser. Signed (HS256 JWT) editor config, short-lived signed download links, verified callbacks; saves become a new version under the editing user; approved/in-progress documents are view-only. Admin page and `gyeol onlyoffice show|set`. See docs/ONLYOFFICE.md.
- Manual: editor toolbar and table formulas (user guide), bundled editor version (security).
- Browser E2E: table formula case (formula, recalculation on edit, submit, approve, completed document; read-only editor find and statistics) and toolbar case (한글97 toolbar present, font/size boxes, bold, no New/Open/Save, no CSP violations).

## 0.2.5
- Bundled editor data files (HWPX template) are hash-checked when loaded; new cache folders are private (0700/0600) regardless of umask.
- Editor first-use setup is done once under a lock; a .pyz process keeps one data folder once the editor is loaded.
- Personal folder changes are recorded in the audit log by document and folder number only (folder names stay private).
- User manual updated for 0.2.3–0.2.4 features (filing CLI, project member picker, photo downscale and 8 MB limit, bundled editor checks).

## 0.2.4
- Bundled hanword editor updated to 0.1.9 (re-vendored with `scripts/vendor_hanword.py`).
- Vendored editor code is loaded only after a SHA-256 check against `VENDOR.md`; tampered or unknown files and unsafe cache folders are refused; the vendor folder never serves path-based imports.
- CLI tag/untag of documents (folders, projects) is now atomic (no lost update under concurrent changes).
- Opt-in browser end-to-end test (editor, photo, submit, approve; Playwright).

## 0.2.3
- The single-file `.pyz` now serves the document editor, its fonts and the static scripts itself.
- Filing CLI: `gyeol folder tag|untag`, `gyeol dclass list|add|rename|delete|get|set`, `gyeol project list|add|show|edit|members|status|tag|untag` (see the CLI chapter of the manual).
- Project member picker: checkbox list with a search box for participating departments and people (works without scripts).
- Photos in the body are shrunk automatically (longest side 1600 px, JPEG 0.85, only when smaller). Body limits: 2 MB of text, 8 MB including photos; when exceeded the user gets a message and the editor keeps its content.
