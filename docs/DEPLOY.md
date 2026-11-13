# 배포 안내

결재(gyeoljae)는 Python 3.10 이상만 있으면 됩니다(외부 패키지 없음). 아래에서 상황에 맞는 방법을 고르세요.

## 공통: 설치와 최초 설정
```sh
# 소스 받기(또는 zip 다운로드) 후
git clone https://github.com/gsungmoon-sketch/e-approval-kr.git && cd e-approval-kr
python -m pip install .          # gyeol 명령 설치. 설치하지 않으려면 run.sh / run.bat 사용
gyeol --data <데이터경로> setup   # 첫 관리자 만들기(대화형). 데모 계정은 만들지 않습니다
```
데이터 경로를 정하지 않으면 OS 기본 위치를 씁니다(README 참고). 서비스로 돌릴 때는 `GYEOLJAE_DATA` 환경변수로 고정하세요.

로그인 후 관리 → **부서 → 직위 → 사원 등록(또는 CSV 일괄)** → **결재규정** 순서로 설정합니다.
사번은 비워 두면 입사일 YYMMDD + 일련번호(예: 2026-11-13 입사 → 26111301)로 자동 발급되고, 로그인 아이디 기본값도 사번입니다.
사원에게는 등록 시 한 번 보이는 임시 비밀번호를 전달하고, 첫 로그인 때 본인이 바꿉니다.

## A. 사내 LAN 서버 1대 (가장 간단)
사무실 PC 한 대(Windows 또는 Linux)를 서버로 씁니다.
```sh
gyeol serve --host 0.0.0.0 --port 8130
```
- 다른 PC 브라우저에서 `http://서버IP:8130` 으로 접속합니다. (서버 IP: Windows `ipconfig`, Linux `ip addr`)
- **방화벽은 사내망만 허용**하세요.
  - Windows(관리자 PowerShell):
    `New-NetFirewallRule -DisplayName gyeoljae -Direction Inbound -Protocol TCP -LocalPort 8130 -RemoteAddress LocalSubnet -Action Allow`
  - Linux(ufw): `sudo ufw allow from 192.168.0.0/24 to any port 8130 proto tcp` (사내 대역에 맞게)
- HTTP 는 사내망 안에서도 도청될 수 있습니다. 가능하면 B의 HTTPS 프록시나 `serve --https cert.pem key.pem`을 쓰세요.
- 공유기 포트포워딩으로 인터넷에 직접 여는 것은 **권장하지 않습니다**.

## B. 외부(재택·모바일) 접속
### B-1. Caddy 리버스 프록시 (자동 HTTPS, 도메인 필요)
1. gyeoljae 는 그대로 `127.0.0.1:8130` 에서 실행하고 환경변수 `GYEOLJAE_TRUST_PROXY=1`, `GYEOLJAE_COOKIE_SECURE=1` 을 줍니다.
2. `deploy/caddy/Caddyfile` 의 도메인을 바꿔 Caddy 를 실행합니다. 인증서는 Caddy 가 자동 발급합니다(80/443 포트 필요).

### B-2. Cloudflare Tunnel (포트포워딩 없이)
1. `cloudflared tunnel create gyeol` → 터널 설정의 ingress 에 `service: http://127.0.0.1:8130` 지정.
2. gyeoljae 환경변수 `GYEOLJAE_TRUST_PROXY=1`, `GYEOLJAE_COOKIE_SECURE=1`. (실제 접속자 IP는 `Cf-Connecting-Ip` 로 판단)
3. 필요하면 Cloudflare Access 로 회사 이메일만 통과하도록 앞단 인증을 추가하세요.

> `GYEOLJAE_TRUST_PROXY=1` 은 **프록시 뒤에서만** 켜세요. 직접 노출된 서버에서 켜면 IP 위조로 로그인 제한을 우회할 수 있습니다.

## C. 서비스로 자동 실행
### Linux (systemd)
`deploy/systemd/gyeoljae.service` 를 경로·사용자에 맞게 고쳐 `/etc/systemd/system/` 에 복사:
```sh
sudo useradd -r -s /usr/sbin/nologin gyeoljae && sudo install -d -o gyeoljae -m 700 /var/lib/gyeoljae
sudo -u gyeoljae env GYEOLJAE_DATA=/var/lib/gyeoljae PYTHONPATH=/opt/gyeoljae/src python3 -m gyeoljae setup
sudo systemctl daemon-reload && sudo systemctl enable --now gyeoljae
```
### Windows
- **작업 스케줄러**(가장 간단): 관리자 명령 프롬프트에서
  `schtasks /Create /TN gyeoljae /SC ONSTART /RU SYSTEM /TR "C:\gyeoljae\run.bat --data C:\gyeoljae-data serve --host 0.0.0.0"`
- **NSSM**(서비스 관리 도구): `nssm install gyeoljae C:\Python312\python.exe -m gyeoljae --data C:\gyeoljae-data serve`
  → `nssm set gyeoljae AppEnvironmentExtra PYTHONPATH=C:\gyeoljae\src` → `nssm start gyeoljae`
- 데이터 폴더(`C:\gyeoljae-data`)는 관리자·서비스 계정만 접근하도록 권한을 제한하세요.
### Docker (선택)
`Dockerfile` 참고. `docker run -d -p 127.0.0.1:8130:8130 -v gyeoljae-data:/data gyeoljae`

## D. 백업·복원
```sh
gyeol --data <데이터경로> backup 2026-11-13.zip      # 실행 중에도 일관된 백업(잠깐 쓰기 대기)
gyeol --data <데이터경로> export-audit-head          # 감사체인 머리값 → 다른 곳(메일·다른 PC)에 보관
gyeol restore 2026-11-13.zip --to <빈 디렉터리>      # 복원 후 자동으로 감사로그 검증
gyeol --data <복원경로> check-audit-head <seq> <hash> # 보관해 둔 머리값이 그대로인지 확인
```
- 매일 1회 백업 + 주 1회 다른 매체(외장 디스크·NAS)로 복사를 권장합니다.
  - Linux cron: `0 2 * * * gyeoljae env GYEOLJAE_DATA=/var/lib/gyeoljae python3 -m gyeoljae backup /backup/gyeol-$(date +\%F).zip`
  - Windows: 작업 스케줄러로 `run.bat --data C:\gyeoljae-data backup D:\backup\gyeol.zip` (날짜별 이름은 배치 파일에서 `%date%` 사용)
- 백업 zip 에는 문서 원문·첨부·사원 정보가 들어 있으니 암호화된 저장소에 두세요.

## E. 규모 안내
- SQLite + 스레드 웹서버 구조입니다. **수십~수백 명, 하루 수천 건 결재**까지는 PC 한 대로 충분합니다.
- 쓰기는 한 번에 하나씩(전역 잠금) 처리됩니다. 동시 상신이 매우 많거나 수천 명 규모, 여러 서버 이중화가 필요하면
  다른 구조(PostgreSQL 등)가 필요합니다.
- 첨부 개당 10MB·합계 30MB, 요청 32MB, 동시 처리 32개가 기본 한도입니다.
- 데이터 디렉터리는 로컬 디스크에 두세요(네트워크 드라이브의 SQLite 잠금은 신뢰할 수 없음).

## F. 메일
- **사내 메일·쪽지**는 gyeoljae 안에서만 오갑니다(인터넷 메일 서버 아님).
- **외부 메일 알림/발송(선택, 기본 꺼짐)**: 관리 → 외부 메일에서 회사 메일의 SMTP 릴레이를 설정합니다.
  - 예) Google Workspace `smtp.gmail.com:587 STARTTLS`(앱 비밀번호), Naver Works·Microsoft 365 는 각 서비스의 SMTP 안내 참고.
  - 결재 도착·완료·반려 알림이 사원 이메일(사원 정보의 이메일)로 갑니다. "사내 메일 → 외부 주소 보내기"는 관리자가 따로 허용해야 하며
    1인 시간당 한도·전체 시간당 한도가 있습니다. 보낸 기록은 감사로그에 남습니다.
  - SMTP 비밀번호는 데이터 폴더 `smtp.json` 에 저장됩니다(저장소에는 없음). 데이터 폴더·백업 파일 접근을 제한하세요.
  - 설정 후 [시험 발송]으로 확인하세요. 대기열은 웹 서버가 30초마다 보내며, CLI `gyeol -u admin mail flush` 로도 보낼 수 있습니다.
- **회사 전체 이메일(받는 메일 포함)**이 필요하면 gyeoljae 가 아니라 호스팅 메일(Google Workspace·Naver Works·Microsoft 365)이나
  별도의 메일 서버(Stalwart, Maddy 등)를 쓰세요. 인터넷 메일을 받으려면 25번 포트, MX·SPF·DKIM·DMARC, 스팸 대응이 필요하며
  gyeoljae 는 이를 하지 않습니다(SMTP 로 보내기만 함).
