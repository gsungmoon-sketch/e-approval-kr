# OnlyOffice 연동 (선택 기능, 기본 꺼짐)

첨부·자료실의 **docx / xlsx / pptx** 를 브라우저에서 보고 편집한다. 사용자 PC 에는 브라우저만 있으면 된다
(번들 hanword 편집기와 같은 방식). 문서 서버(OnlyOffice Document Server, Docs API)는 따로 운영한다.
코드: `src/gyeoljae/onlyoffice.py`(로직, App mixin) · `web_oo.py`(화면·경로) · `static/oo-editor.js`.

## 설정 (관리 → OnlyOffice, 또는 `gyeol onlyoffice set`)

| 항목 | 뜻 |
|---|---|
| 문서 서버 주소 `docs_url` | 브라우저가 여는 문서 서버(예 `https://docs.example.com`). api.js 를 여기서 받는다 |
| 문서 서버 내부 주소 `docs_internal_url` | 결재 서버가 편집 결과를 받을 주소(다르면). 결과 URL 의 출처가 `docs_url` 이면 이 출처로 바꿔 받는다 |
| 결재 서버 내부 주소 `app_internal_url` | 문서 서버가 파일을 받고 콜백할 결재 서버 주소(예 `http://gyeoljae.internal:8130`) |
| JWT 비밀 `jwt_secret` | 문서 서버 `JWT_SECRET` 과 같은 값, 32자 이상. `onlyoffice.json`(데이터 폴더, 0600)에 저장 — `smtp.json` 과 같은 방식, 화면·감사로그에는 가림 |
| JWT 헤더 | 기본 `Authorization` |

`onlyoffice.json` 에는 결재 서버만 아는 `link_key`(내려받기·콜백 토큰 서명용, 자동 생성)도 들어 있다.
문서 서버 쪽은 JWT 를 반드시 켠다(`JWT_ENABLED=true`). 결재 서버를 사설 주소로 두면 문서 서버의
`ALLOW_PRIVATE_IP_ADDRESS=true` 가 필요하다.

## 흐름

1. 첨부(문서 화면)·자료실(파일 화면) 옆 **보기 / 편집** 단추 → `/oo/edit/d/<문서>/<첨부>` 또는 `/oo/edit/l/<자료>`.
2. 편집기 화면은 문서 서버의 `api.js` 를 싣고, 서버가 만든 설정을 `<script type=application/json>` 에 넣는다
   (`oo-editor.js` 는 읽어서 `DocsAPI.DocEditor` 에 넘기기만). 설정 전체를 **HS256 JWT** 로 서명(`config.token`, 8시간).
3. `document.key` = `gj` + 모드(e 편집 / v 보기) + 종류(d/l) + id + `x` + 첨부번호/판 + `x` + sha256 앞 16자 + `x` +
   HMAC(link_key) 16자. 보기와 편집은 **다른 세션**(결재자가 기안자의 저장 안 된 편집을 보지 않음), 내용·문서 rev(상신·수정)가
   바뀌면 키도 바뀌고, 우리 비밀 없이는 키를 짐작할 수 없다(문서 서버를 같이 쓰는 다른 앱이 세션에 끼어들지 못함).
4. `document.url` = `<app_internal_url>/oo/dl/<토큰>` — 우리 `link_key` 로 서명, 15분, 한 파일의 한 판(sha 고정),
   읽기 전용. 세션 쿠키 없음. 받을 때마다 토큰의 사용자가 지금도 볼 수 있는지 다시 확인. 이 주소는 편집기 화면의
   설정에도 보이므로, 그 사용자가 이미 받을 수 있는 파일을 15분 동안 로그인 없이 받을 수 있는 정도의 노출이 있다.
5. 편집 모드에만 `callbackUrl` = `<app_internal_url>/oo/cb/<토큰>`(우리 서명, 문서 키에 묶임, 7일). 보기 모드는 콜백 없음.
6. 콜백: (a) 콜백 주소 토큰 검증(본문을 읽기 전) (b) 문서 서버 JWT 검증(본문 `token` 우선, 없으면 헤더 Bearer; 헤더
   토큰은 `{"payload": …}` 를 풂, `exp` 필수) — **서명된 내용만** 쓴다 (c) 서명된 `key` 가 콜백 토큰의 키와 같고 편집(e) 키여야 함.
   - 상태 1(편집 중)·3(저장 오류)·4(변경 없음)·7(중간 저장 오류) → `{"error":0}`, 아무것도 안 함.
   - 상태 2(완료)·6(중간 저장) → 편집기를 연 사용자(콜백 토큰), 그가 권한을 잃었으면 JWT `users` 중 **지금 편집
     권한이 있는** 첫 사용자 이름으로 결과 URL 에서 받아 새 판을 만든다(`users` 는 감사에 `claimed_users` 로 남김).
     **옛 세션 방지**: 지금 파일이 이 세션이 시작한 판이거나 이 세션이 마지막으로 저장한 판(`oo_chain`)일 때만 저장 —
     그 사이 다른 사람이 새 판을 올렸으면 거부. 결과 URL 은 설정한 문서 서버 출처(스킴·호스트·포트 일치)만, 리다이렉트·프록시 없음,
     읽기 30초·전체 120초, 크기 상한(첨부 10MB·자료실 20MB). 결과 형식(`filetype`, 필수)이 원본 확장자와 다르거나
     zip(`PK\x03\x04`)이 아니면 거부. 같은 내용이면 판을 만들지 않음.
   - 권한이 없거나 문서가 잠기면(상신·완료) `{"error":1}` + 감사 `oo_save_refused`.
7. 저장 = 올리기와 같다. 자료실: `files_new_version`(감사 `lib_upload`, 판 메모 "OnlyOffice 편집 (판N에서)").
   첨부: `attachment_replace` — 이름·번호 그대로, 이전 내용은 `prev` 로 보존, 감사 `attach_edit`. 둘 다 추가로 `oo_save`.

## 신뢰 경계

문서 서버와 그 JWT 비밀을 가진 쪽은 편집 내용을 바꿀 수 있다(그것이 문서 서버의 역할). 결재 전용 문서 서버를 쓰고 비밀을
다른 서비스와 나누지 말 것. 결재 서버 ↔ 문서 서버 내부 주소가 평문 http 면 토큰과 결과 파일이 그 망에 그대로 흐르므로
HTTPS 나 믿을 수 있는 내부망에서만 쓴다. 주소의 호스트는 영숫자·`.`·`-`(또는 `[IPv6]`)만 허용(CSP 에 들어감).

## 권한

| 대상 | 보기 | 편집 |
|---|---|---|
| 문서 첨부 | 문서를 볼 수 있는 사람(`_VIS`) | 기안자, 문서가 기안중·회수·반려일 때만(= 첨부를 지우고 다시 올릴 수 있을 때). 진행·완료 = 보기만 |
| 자료실 | 읽기 권한(전사=모두, 부서=그 부서) | 쓰기 권한(전사=부서장급·관리자, 부서=그 부서원), **최신 판만**. 옛 판은 보기만 |

권한은 편집기를 열 때·파일을 줄 때·저장할 때 **매번** 다시 판단한다(열어 둔 사이 상신하면 저장 거부).

## CSP

편집기 화면에만 `script-src 'self' <문서 서버 출처>; frame-src <문서 서버 출처>`. 다른 화면은 그대로.
기능이 꺼져 있으면 이 화면 자체가 없다. 인라인 스크립트·eval 없음. 접근 로그에는 `/oo/dl|cb/<token>` 으로 가린다.

## 실서버 시험 (opt-in)

`tests/test_onlyoffice_live.py` — 기본 건너뜀. 문서 서버를 이 PC 에만 열고(예 `-p 127.0.0.1:8701:80`),
결재 서버는 문서 서버 컨테이너가 닿되 밖에는 안 열린 주소에 띄운다. 예: 전용 도커 네트워크
(`docker network create --subnet <사설대역> --gateway <게이트웨이IP> …`)에 문서 서버를 붙이고, 결재 서버를
그 게이트웨이 IP 에 띄운 뒤, 방화벽은 그 네트워크에서 그 포트로 오는 것만 허용.

```sh
docker run -d --name gyeoljae-oo-docs --restart no --network <전용 네트워크> -p 127.0.0.1:8701:80 \
  -e JWT_ENABLED=true -e JWT_SECRET=<비밀 파일의 값> -e ALLOW_PRIVATE_IP_ADDRESS=true \
  onlyoffice/documentserver:9.0.4.1
cd tests && GYEOLJAE_OO_E2E=1 GYEOLJAE_OO_DOCS_URL=http://127.0.0.1:8701 GYEOLJAE_OO_JWT_SECRET_FILE=<비밀 파일> \
  GYEOLJAE_OO_APP_HOST=<게이트웨이IP> GYEOLJAE_OO_APP_PORT=<포트> python3 -m unittest -v test_onlyoffice_live
```

시험: dev1 이 자료실에 docx 를 올림 → Playwright 로 편집기를 열고 글자 입력 → Ctrl+S(중간 저장, 상태 6; 안 되면
창 닫기 → 상태 2) → 판 2 의 docx XML 에 그 글자가 있는지, 올린 사람·감사 기록 확인. 문서 서버는 메모리 2~4GB 필요.
