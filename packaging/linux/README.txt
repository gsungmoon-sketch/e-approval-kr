e-approval-kr for Linux (x86_64, glibc 2.35 or newer; systemd)
================================================================

No Python needed - the program is self-contained.

Install / upgrade (as root, from this directory):
    sudo ./install.sh
Then open http://127.0.0.1:8130/setup on this machine to create the first administrator,
or on a headless server:  sudo e-approval-kr-admin setup

Options:  --lan (listen on the LAN + ufw rule for local private subnets; plain HTTP - see manual)
          --port N   --no-start
Uninstall:  sudo /opt/e-approval-kr/uninstall.sh           (keeps data in /var/lib/e-approval-kr)
            sudo /opt/e-approval-kr/uninstall.sh --purge   (also deletes all data)

Manual: e-approval-kr/manual/index.html (installed to /opt/e-approval-kr/manual/)

---------------------------------------------------------------
설치(이 폴더에서, root):  sudo ./install.sh
설치 후 이 PC 브라우저에서 http://127.0.0.1:8130/setup 으로 첫 관리자를 만드세요.
화면이 없는 서버라면:  sudo e-approval-kr-admin setup
사내 LAN 공개:  sudo ./install.sh --lan  (HTTP 평문 - 설명서의 HTTPS 안내를 꼭 보세요)
제거:  sudo /opt/e-approval-kr/uninstall.sh  (데이터 보존, --purge 를 주면 데이터까지 삭제)
