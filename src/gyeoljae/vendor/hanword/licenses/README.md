# Third-party components

hanword's own code is MIT-licensed (see `../LICENSE`). The files below are redistributed
unchanged under their own licences.

| File(s) | Component | Licence | Text |
|---|---|---|---|
| `html2canvas.min.js` | html2canvas 1.4.1 — Niklas von Hertzen | MIT | `html2canvas-LICENSE.txt` |
| `jspdf.min.js` | jsPDF 2.5.1 — James Hall, yWorks GmbH and contributors (the minified bundle also contains its own MIT/zlib-licensed helpers, whose notices are kept in the file header) | MIT | `jsPDF-LICENSE.txt` |
| `hwpx_skeleton.hwpx` | empty HWPX document skeleton (`hwpx/data/Skeleton.hwpx`) from **python-hwpx** | Apache-2.0 | `Apache-2.0.txt`, `../NOTICE-hwpx_skeleton.txt` |
| `font/Pretendard-*`, `font/NotoSansKR*`, `font/NotoSerifKR-*`, `font/D2Coding-*`, `font/NanumGothic-{Regular,Bold}.ttf`, `font/NanumMyeongjo-{Regular,Bold}.ttf` | fonts — see the table below | SIL OFL 1.1 | `fonts/` |

### Fonts

Shipped in every build (public release, embed/vendor export via `scripts/export_embed.py`). All SIL OFL 1.1;
the licence text of each family is in `fonts/`, the copyright line is also inside each font file.
Checked 2026-11-14 against upstream (glyph order, cmap and outlines with fontTools).

| File | Font | Copyright / Reserved Font Name | State vs upstream | Licence | sha256 |
|---|---|---|---|---|---|
| `font/Pretendard-400.woff2`, `-700` | Pretendard 1.3.9 Regular/Bold | © 2021 Kil Hyung-jin; RFN "Pretendard" | byte-identical to the v1.3.9 release `web/static/woff2/Pretendard-{Regular,Bold}.woff2` (renamed only) | `fonts/Pretendard-OFL.txt` | `fad853f7…1d63`, `4609c335…d362` |
| `font/NotoSansKR.ttf` | Noto Sans KR 2.004 | © 2014-2021 Adobe; RFN "Source" (not used) | full font | `fonts/NotoSansCJK-OFL.txt` | `9db318b6…5f6d` |
| `font/NotoSansKR-400.woff2`, `-700` | Noto Sans KR 2.004 (web subset) | same | subset (OFL Modified Version; the RFN "Source" is not used, so allowed) | `fonts/NotoSansCJK-OFL.txt` | `3aa0d1d6…a6d9`, `dab3d492…6020` |
| `font/NotoSerifKR-400.woff2`, `-700` | Noto Serif KR 2.003 (web subset) | © 2017-2024 Adobe | subset (OFL Modified Version, no RFN involved) | `fonts/NotoSerifCJK-OFL.txt` | `c9a1e3ac…de45`, `504d6af6…0941` |
| `font/NanumGothic-Regular.ttf`, `-Bold.ttf` | NanumGothic 3.020 | © 2010 NHN (NAVER) Corp.; RFN Nanum, NanumGothic, … | byte-identical to google/fonts@1c96438 `ofl/nanumgothic/NanumGothic-{Regular,Bold}.ttf` (as in public v0.1.0) | `fonts/Nanum-OFL.txt` | `76f45ef4…2d31`, `f96298f9…c674` |
| `font/NanumMyeongjo-Regular.ttf`, `-Bold.ttf` | NanumMyeongjo 2.032 | © 2010 NHN (NAVER) Corp.; RFN Nanum, NanumMyeongjo, … | byte-identical to google/fonts@1c96438 `ofl/nanummyeongjo/NanumMyeongjo-{Regular,Bold}.ttf` | `fonts/NanumMyeongjo-OFL.txt` | `7ed9e865…3205`, `bc9ed8e6…e3f3` |
| `font/D2Coding-400.woff2`, `-700` | D2Coding 1.3.2 | © 2015 NAVER Corp.; RFN "D2Coding" | lossless WOFF2 of `D2Coding-Ver1.3.2-20180524.ttf` / `D2CodingBold-…` (same glyphs, cmap, outlines; no subsetting) | `fonts/D2Coding-OFL.txt` | `5a2caca4…4137`, `7ad949ea…51ee` |

Upstream licence sources: google/fonts `ofl/nanumgothic/OFL.txt` and `ofl/nanummyeongjo/OFL.txt`, orioncactus/pretendard v1.3.9 `LICENSE`, notofonts/noto-cjk `Sans/LICENSE` and
`Serif/LICENSE`, naver/d2codingfont `OFL.txt`; generic text `OFL-1.1.txt`.

**Present in the development tree but never shipped** (excluded by `.publicignore`, so not in public
builds or the embed export; the editor falls back to the fonts above):

| File | Why not shipped |
|---|---|
| `font/NanumGothic-400/700.woff2`, `font/NanumMyeongjo-400/700.woff2` | renamed web subsets used by the live site that keep the Reserved Font Names "Nanum…" (a Modified Version may not use them); builds ship the unmodified TTFs above instead and `editor.css` falls back to them |
| `font/Hamchorom-*` (함초롬바탕·돋움 LVT) | © Hancom; the font carries no licence grant and Hancom's terms do not clearly allow redistribution |
| `font/UnBatang-*` (은바탕) | GPL-2 font converted to WOFF2; redistribution would bring GPL source obligations |

Users can install these themselves (see the manual, "Fonts").

## Not bundled (optional, installed by the user)

| Component | Used for | Licence | Notes |
|---|---|---|---|
| pyhwp (`hwp5html`, `hwp5txt`) | importing legacy `.hwp` (HWP 5) files | **AGPL-3.0** | Run as a separate program; never vendored. Without it `.hwp` import reports "not supported". |
| python-hwpx + lxml | alternative HWPX engine (`WP_HWPX_ENGINE=pyhwpx`) | Apache-2.0 / BSD | The default engine is the stdlib `hwpx_std.py`. |
| cryptography | password-protected edit history | Apache-2.0 / BSD | |
| poppler-utils | PDF import | GPL-2.0 | separate program |
| ImageMagick | server-side raster PDF assembly | ImageMagick licence | separate program |
| Node.js + Playwright | vector PDF export (`htmlpdf.js`) | MIT / Apache-2.0 | separate program |
| 함초롬바탕·함초롬돋움 (Hancom), 은바탕 (UnBatang, GPL) | optional fonts | vendor terms / GPL | download from the official source; see the manual, "Fonts". |

Binary releases additionally contain the CPython runtime (PSF licence), the PyInstaller
bootloader (GPL-2.0 with bootloader exception) and, on Windows, WinSW (MIT); see
`packaging/THIRD-PARTY-NOTICES.txt`.
