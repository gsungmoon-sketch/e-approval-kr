import faulthandler
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gyeoljae.core import App  # noqa: E402

PW = "pass12345"
# 테스트가 멈추면 5분마다 모든 스레드 스택을 출력(CI 에서 어디서 멈췄는지 보이게)
faulthandler.dump_traceback_later(300, repeat=True)


def rmtree(d):
    def onerr(func, path, exc):
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
        func(path)
    shutil.rmtree(d, onerror=onerr)


def world(tmp=None):
    """3개 부서, 6명(+관리자). MGT 경영지원 / DEV 개발 / SAL 영업."""
    d = tmp or tempfile.mkdtemp(prefix="gyeol-")
    app = App(d)
    app.bootstrap("ADM", "관리부", "admin", "관리자", "adminpass1")
    for code, name in (("MGT", "경영지원"), ("DEV", "개발"), ("SAL", "영업")):
        app.add_dept("admin", code, name)
    for u, n, dept, role in (("ceo", "대표", "MGT", "head"), ("hr", "인사", "MGT", "user"),
                             ("devlead", "개발장", "DEV", "head"), ("dev1", "개발1", "DEV", "user"),
                             ("sallead", "영업장", "SAL", "head"), ("sal1", "영업1", "SAL", "user")):
        app.add_user("admin", u, n, dept, role, PW, "팀장" if role == "head" else "사원")
    app.setting_set("admin", "allow_freeform", True)  # 결재 흐름 시험은 양식 없는 기안으로(양식 강제는 test_forms)
    return app, d
