"""PyInstaller entry point for the e-approval-kr server binary.

Behaves exactly like `gyeol` / `python -m gyeoljae`: all arguments are passed through
(services and installers always pass an explicit sub-command such as `serve`).
Run with no arguments it prints a short hint instead of an argparse error, and on
Windows waits for Enter so a double-clicked console window does not vanish at once.
"""
import os
import sys

from gyeoljae.cli import main

HINT = """e-approval-kr (전자결재 서버)

  e-approval-kr serve                 서버 실행 / run the server (default http://127.0.0.1:8130/)
  e-approval-kr setup                 첫 관리자 만들기 / create the first administrator
  e-approval-kr --help                전체 명령 / all commands

데이터 위치 / data directory: --data <dir> 또는 환경변수 GYEOLJAE_DATA
설치형(서비스)으로 설치했다면 브라우저에서 http://127.0.0.1:8130/ 을 여세요.
"""


def run() -> int:
    if len(sys.argv) == 1:
        print(HINT)
        if os.name == "nt" and sys.stdin is not None and sys.stdin.isatty():
            try:
                input("Enter 키를 누르면 닫힙니다 / press Enter to close...")
            except EOFError:
                pass
        return 0
    return main()


if __name__ == "__main__":
    sys.exit(run())
