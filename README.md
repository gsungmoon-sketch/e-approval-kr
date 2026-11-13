# e-approval-kr — 결재 (package: gyeoljae)

[![CI](https://github.com/gsungmoon-sketch/e-approval-kr/actions/workflows/ci.yml/badge.svg)](https://github.com/gsungmoon-sketch/e-approval-kr/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![stdlib only](https://img.shields.io/badge/dependencies-none-brightgreen)

Korean-style e-approval (전자결재) system — approval lines, 전결/대결, forms, mail, files. Python stdlib, Windows/Linux/macOS.
저장소 이름은 `e-approval-kr`, 파이썬 패키지는 `gyeoljae`, 명령은 `gyeol` 입니다.

옛 BBS 감성의 **CLI 우선 전자결재**. 메뉴·게시판·쪽지에 결재선·대결·전결·문서번호·감사로그를 더했습니다.
Python 3.10+ **표준 라이브러리만** 사용 — Windows / macOS / Linux 어디서나 같은 방식으로 동작합니다.

> **English** — A small, CLI-first electronic approval (전자결재) system for Korean organizations: approval lines,
> delegation, threshold-based final approval, department policies, document numbering, full-text search,
> notice board and messages, with a tamper-evident hash-chained audit log. Pure Python stdlib (sqlite3, http.server),
> runs on Windows/macOS/Linux. The same core functions back the CLI (`--json`) and a server-rendered web UI. MIT licensed.

## 내려받기 (Python 설치 불필요)
[Releases](https://github.com/gsungmoon-sketch/e-approval-kr/releases) 에서 OS에 맞는 파일 하나를 받아 설치하면 서버가 서비스로 등록되고
브라우저에서 첫 설정 화면(`http://127.0.0.1:8130/setup`)이 열립니다.

| OS | 파일 | 설치 |
|---|---|---|
| Windows 10/11 x64 | `e-approval-kr-setup-<버전>-win-x64.exe` | 더블클릭 (서명 전이라 SmartScreen → 추가 정보 → 실행) |
| macOS (Apple silicon / Intel) | `e-approval-kr-<버전>-macos-<arm64·x64>.pkg` | 우클릭 → 열기 (Gatekeeper) |
| Linux x64 | `e-approval-kr-<버전>-linux-x64.tar.gz` | 풀고 `sudo ./install.sh` (systemd) |
| Python 3.10+ 있는 곳 | `e-approval-kr-<버전>.pyz` | `python3 e-approval-kr-<버전>.pyz serve` |

설명서 / Manual: [한국어](docs/manual/ko/index.html) · [English](docs/manual/en/index.html) — 설치 패키지에도 들어 있습니다.
무결성 확인: 릴리스의 `SHA256SUMS`.

## 빠른 시작 (소스에서)
```sh
git clone https://github.com/gsungmoon-sketch/e-approval-kr.git && cd e-approval-kr
python -m pip install .            # 또는 설치 없이: ./run.sh ... (Windows: run.bat ...)
gyeol setup                        # 최초 관리자 만들기(대화형)
# 비대화형(설치 마법사): gyeol setup --admin-id admin --admin-name 관리자 --password-file pw.txt  ('-' = 표준입력)
gyeol serve                        # http://127.0.0.1:8130 — 관리자로 로그인해 부서·사원 등록
```
시험만 해 볼 때: 빈 데이터 디렉터리에서 `gyeol seed-demo` (데모 계정 비밀번호 `demo1234!`, 실사용 금지).

데이터 위치: `GYEOLJAE_DATA` 또는 `--data`, 기본은 Windows `%LOCALAPPDATA%\gyeoljae`, macOS `~/Library/Application Support/gyeoljae`,
Linux `~/.local/share/gyeoljae`.

## CLI 예
```sh
gyeol -u admin emp add --name 홍길동 --dept DEV --position 팀원 --hired 2026-11-13   # 사번 26111301, 임시 비밀번호 1회 표시
gyeol -u admin emp import 사원.csv            # 미리보기 → --apply 로 반영 (UTF-8 / 엑셀 CP949, 사번 비면 줄마다 입사일로 자동)
gyeol -u admin setting set emp_digits 3       # 자동 사번 일련번호 3자리(새 번호부터)
gyeol -u admin policy edit DEV --type 지출 --line "팀장?, 부서장, 대표" --jeon "부서장:100만" --cc hr
gyeol -u admin policy preview --dept DEV --type 지출 --amount 150만
gyeol -u kim form list ; gyeol -u kim form show expense   # 양식(서식) 목록·항목
gyeol -u kim draft --title "노트북 구매" --form expense --field purpose=업무용 --field pay=법인카드 \
      --row "items:item=노트북;qty=1;price=90만" --attach 영수증.pdf --submit   # 금액은 표 합계로 자동 → 전결 한도 적용
gyeol -u admin form copy general hr_memo --name "인사 품의" ; gyeol -u admin form edit hr_memo 정의.json   # 새 버전
gyeol -u admin setting set doc_number "{prefix}-{year}-{seq}"   # 문서번호 형식
gyeol -u lee list --box inbox ; gyeol -u lee approve 12 -m 확인
gyeol -u kim search -q 노트북 --from 2026-01-01 ; gyeol -u kim menu       # BBS 메뉴
gyeol backup 백업.zip ; gyeol -u admin verify-audit ; gyeol export-audit-head
```
모든 명령은 `--json` 출력 지원. CLI 는 데이터 디렉터리에 접근할 수 있는 **로컬 운영자 도구**입니다(`-u`로 행위자 지정).

## 기능
- **사원·조직**: 사번(자동 = 입사일 YYMMDD + 일련번호, 예 2026-11-13 입사 → `26111301`, `26111302`…; 일련번호 2/3자리 설정, 수동 입력 가능)·이름·부서·직위(편집 가능)·입사일·재직/휴직/퇴사, 인사이동 이력, CSV 일괄 등록/내보내기, 조직도.
  첫 로그인 시 비밀번호 변경 강제. 퇴사 = 로그인 차단, 문서·기록 보존.
- **결재**: 문서함(결재대기·기안함·진행·완료·반려·참조), 기안·상신·승인·반려·회수·대결·전결, 참조 열람 표시, 첨부(hwpx/docx/pdf…).
- **부서별 결재규정**: 문서 종류(일반·지출·휴가)별 결재선 템플릿(직위 기준), 금액별 전결 한도, 추가 결재자 허용 여부, 필수 참조,
  대결 규칙. 규정은 버전 관리되며 **진행 중 문서는 상신 당시 버전**을 유지. 결재선은 서버가 만듦.
- **양식(서식)**: 기안은 양식에서 시작 — 기본 10종(일반기안(품의)·업무보고·지출결의서·구매요청서·출장신청서·출장비정산서·
  경비정산서·휴가신청서·협조전·공문). 항목 종류: 글·숫자·금액·날짜·기간(일수 자동, 주말 제외·반차 0.5)·선택·표(행 반복, `수량*단가`
  자동 계산·합계)·첨부 필수. 값은 서버가 검증·계산하며 **금액 항목이 결재규정 전결 한도에 자동 반영**. 양식별 분류·결재 종류·보존기간·
  사용 부서·필수 참조 부서(부서장 자동 참조)·문서번호 접두어. 양식은 버전 관리되고 문서는 **기안 당시 버전**을 유지. 즐겨찾기·최근 사용.
- **문서번호** 기본 `<부서>-<연도>-<4자리>`, 형식 설정 가능(`{dept} {year} {yy} {prefix} {seq}`) — 최종 승인 시 발급(결번 없음).
- **검색**: 제목·본문(SQLite FTS5 trigram, 없으면 LIKE)·기간·기안자·부서·상태·종류·문서번호. 결과는 열람권으로 필터.
- **감사로그**: 모든 상태 변경을 해시 체인으로 기록(DB 정본 + `audit.log` 사본), 완료 문서 내용 해시 → `verify-audit`가 변조 감지.
  체인 머리값을 외부에 보관해 두면 체인 전체 재작성도 감지(`check-audit-head`).
- **2인 승인(선택)**: 결재규정 변경·관리자 지정·부서장급 직위 부여는 두 번째 관리자 승인 후 적용.
- **게시판·쪽지**: 공지/자유게시판, 결재 이벤트 쪽지 알림.

## 열람 권한
기안자 · (상신 후) 결재선·대결자·참조자 · 기안 부서의 부서장급 직위 · "부서원 열람 허용" 완료 문서는 같은 부서원.
관리자는 문서 본문 권한이 없고 사원·규정·감사만 관리합니다(권한 부여는 감사로그·부서장 쪽지로 드러남).

## 배포
[docs/DEPLOY.md](docs/DEPLOY.md) — 사내 LAN 서버 1대, 외부 접속(Caddy 자동 HTTPS / Cloudflare Tunnel), 서비스 등록(systemd·Windows),
백업, 규모 안내.

## 개발
`cd tests && python -m unittest test_core test_web test_org test_policy test_home test_mail && python e2e_scenario.py`
구조: `core.py`(App) ← `org.py` 사원·조직 / `rules.py`+`policy.py` 결재규정 / `ops.py` 백업 — `cli.py`·`cli_admin.py`·`web.py`·`web_admin.py`는 화면일 뿐.
[CONTRIBUTING.md](CONTRIBUTING.md) · [SECURITY.md](SECURITY.md) · 라이선스 [MIT](LICENSE)
