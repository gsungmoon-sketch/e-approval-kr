# Changelog

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
