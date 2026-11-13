# 기여 안내

버그·제안은 https://github.com/gsungmoon-sketch/e-approval-kr/issues 에, 변경은 Pull Request 로 보내 주세요(CI 가 Windows/macOS/Linux × Python 3.10–3.13 에서 테스트).

- **표준 라이브러리만** 사용합니다(외부 패키지 추가 금지). Windows/macOS/Linux 모두에서 동작해야 합니다:
  fcntl·pwd·grp 금지, 파일 I/O 는 `encoding="utf-8"`, 경로는 `pathlib`, 잠금·번호는 SQLite 트랜잭션.
- **CLI 먼저**: 기능은 `src/gyeoljae/core.py`(App)와 mixin(`org.py`, `rules.py`, `ops.py`)에 넣고, CLI(`--json`)와
  웹은 같은 함수를 부르기만 합니다. 웹에 로직을 넣지 마세요.
- 조회 기능은 반드시 열람권 SQL(`App._VIS`)을 거칩니다. 상태를 바꾸는 동작은 `_audit()`로 기록합니다.
- 웹 출력은 전부 `esc()`, POST 는 CSRF 토큰. 자바스크립트는 쓰지 않습니다.
- 변경 후: `cd tests && python -m unittest test_core test_web test_org test_policy test_home test_mail && python e2e_scenario.py`
- 커밋 메시지는 한국어/영어 모두 좋습니다. 테스트를 함께 추가해 주세요.
