# 보안 정책

## 취약점 신고
공개 이슈 대신 GitHub **Security Advisories(Private vulnerability reporting)** 로 알려 주세요: https://github.com/gsungmoon-sketch/e-approval-kr/security/advisories/new
재현 방법·영향 범위를 적어 주시면 확인 후 답변드립니다.

## 설계상 보안 경계
- 웹: 세션 쿠키 HttpOnly·SameSite=Strict(HTTPS 시 Secure+HSTS), 모든 POST CSRF 토큰 + Origin 검사, 스크립트 없는 CSP,
  로그인 실패 제한(계정 5회·IP 20회/10분), 요청 크기·동시 처리·소켓 시간 제한.
- 권한 판정은 한 곳(SQL)에서 하며, 권한 없는 문서는 "없음"으로 응답(존재 숨김).
- 첨부는 확장자 허용 목록·크기 제한, 디스크 이름은 번호로만 저장(경로 탈출·Windows 예약어 무관).
- 감사로그 해시 체인 + 완료 문서 내용 해시. **한계**: 데이터 디렉터리 쓰기 권한자는 체인 전체를 다시 만들 수 있으므로
  `gyeol export-audit-head` 값을 주기적으로 다른 곳에 보관하세요.
- CLI 는 데이터 디렉터리 접근권 = 시스템 관리자로 신뢰합니다. 데이터 디렉터리 권한을 서비스 계정만으로 제한하세요.

## 공개 운영 체크리스트
1. HTTPS 리버스 프록시 뒤에서 `GYEOLJAE_TRUST_PROXY=1`, `GYEOLJAE_COOKIE_SECURE=1` (또는 `serve --https`).
2. 데모 데이터(`seed-demo`) 사용 금지.
3. 관리자 2명 이상이면 2인 승인 켜기(`gyeol -u admin setting set two_person on`).
4. 정기 백업 + 감사체인 머리값 외부 보관.
