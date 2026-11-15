# 재사용 기록 (REUSE)

새 기능·시험 도구를 들이기 전에 이미 있는 것을 먼저 찾고, 쓴 것을 여기 적는다.

## 0.2.6 — OnlyOffice 연동 (`onlyoffice.py`, `web_oo.py`, docs/ONLYOFFICE.md)

- **찾은 것**: 자산 DB(코드·컴포넌트·의미 검색)에 OnlyOffice 연동·콜백 코드는 없음. JWT 는 다른 웹 프로젝트(Node)의
  `sign/verify`(HMAC-SHA256, base64url, `timingSafeEqual`, `exp`·`typ` 검사)뿐 — Python 라이브러리는 없음.
  저장소 안: 자료실 판 관리(`files_new_version`), 첨부 저장(`_store_attachments`), 비밀 보관(`smtp.json` 0600·가림표),
  CSP 표(`web.CSP`), 감사(`_audit`), 브라우저 E2E(`test_browser_e2e._launch`).
- **쓴 것**: 그 Node 토큰의 모양(서명→만료→토큰 종류 `t` 검사, 상수 시간 비교)을 표준 라이브러리 `hmac`/`hashlib`
  로 옮겨 JWT HS256 을 직접 구현(alg 는 HS256 만). 저장은 기존 `files_new_version`(같은 내용이면 건너뛰는
  `if_changed` 만 추가)과, 첨부용 `attachment_replace`(`_editable` 권한·잠금 재사용). 설정 파일은 `smtp.json` 과 같은
  방식(`onlyoffice.json`, `_atomic_write`+`_try_chmod 0600`, 비밀 가림). 실서버 시험은 `test_browser_e2e._launch`
  (브라우저 찾기) 재사용. 새 pip 의존 없음.

## 0.2.4 — 브라우저 종단간 시험 (`tests/test_browser_e2e.py`)

- **찾은 것**: 개발 기계에 이미 있던 Python Playwright 패키지와 Playwright 가 받아 둔 Chromium(브라우저 캐시),
  그리고 같은 편집기(hanword) 원 프로젝트의 Node Playwright 시험들.
- **쓴 것**: Python Playwright(`playwright.sync_api`) + 이미 받아 둔 Chromium. 새 시스템 패키지 설치 없음.
  우리 시험은 표준 `unittest` 라서 Node 시험 묶음은 가져오지 않고, 그쪽의 방식(iframe 편집기 `#page` 에 직접 입력,
  그림 삽입 단축키 Ctrl+N,I → 파일 선택)만 따랐다.
- **판 불일치 대비**: 설치된 Playwright 가 기대하는 브라우저 판이 캐시에 없을 수 있어서, 브라우저를 이 순서로 찾는다:
  `GYEOLJAE_TEST_CHROME` → Playwright 기본 → 시스템 Chrome/Chromium → Playwright 캐시의 다른 판. 모두 실패하면 건너뜀.
- **사진 만들기**: PIL 등 추가 의존 없이 브라우저 캔버스로 시험 사진(JPEG)을 만든다.
- 기존 `tests/test_photo_limit.py` 의 headless Chrome 시험(img-shrink.js 단독)은 그대로 두었다. 새 시험은 기안 화면 전체 흐름.

## 실행

```sh
pip install playwright        # 가상환경 권장, 브라우저는 `python -m playwright install chromium` 또는 시스템 Chrome
cd tests && GYEOLJAE_BROWSER_E2E=1 python -m unittest -v test_browser_e2e
```

환경변수가 없으면(CI 기본) 건너뛴다.
