"""종단간 시나리오: CLI(`python -m gyeoljae --json`)만으로 3부서·6명 결재 흐름을 돌린다.

실행: python tests/e2e_scenario.py   (임시 데이터 디렉터리 사용, 끝나면 삭제)
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).parent))
from helpers import PW, rmtree  # noqa: E402

D = tempfile.mkdtemp(prefix="gyeol-e2e-")
ENV = dict(os.environ, GYEOLJAE_DATA=D, PYTHONPATH=str(ROOT / "src"), PYTHONIOENCODING="utf-8")
FAILS = []


def g(user, *args, stdin=None, ok=True):
    cmd = [sys.executable, "-m", "gyeoljae", "--json"] + (["-u", user] if user else []) + list(args)
    p = subprocess.run(cmd, input=stdin, capture_output=True, text=True, encoding="utf-8", env=ENV, timeout=120,
                       stdin=None if stdin is not None else subprocess.DEVNULL)
    data = json.loads(p.stdout) if p.stdout.strip() else None
    if ok and p.returncode not in (0,):
        raise SystemExit(f"FAIL {args}: rc={p.returncode} {p.stdout} {p.stderr}")
    if not ok and p.returncode == 0 and args[0] != "verify-audit":
        raise SystemExit(f"FAIL (expected error) {args}: {p.stdout}")
    return data, p.returncode


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        FAILS.append(name)


try:
    g(None, "init", "--password-stdin", stdin="adminpass1\n")
    for code, nm in (("MGT", "경영지원"), ("DEV", "개발"), ("SAL", "영업")):
        g("admin", "dept", "add", code, nm)
    for u, n, dept, role in (("ceo", "대표", "MGT", "head"), ("hr", "인사", "MGT", "user"),
                             ("devlead", "개발장", "DEV", "head"), ("dev1", "개발1", "DEV", "user"),
                             ("sallead", "영업장", "SAL", "head"), ("sal1", "영업1", "SAL", "user")):
        g("admin", "emp", "add", "--id", u, "--name", n, "--dept", dept, "--position",
          "부서장" if role == "head" else "팀원")

    # 0) 양식 강제(기본): 양식 없는 기안 거부 → 이 시나리오는 자유 기안 허용으로 진행
    _, rc = g("dev1", "draft", "--title", "x", "--body", "y", "--line", "devlead", ok=False)
    check("양식 없는 기안 거부(기본)", rc != 0)
    g("admin", "setting", "set", "allow_freeform", "on")

    # 1) 승인 흐름: dev1 → devlead → ceo, 참조 hr, 첨부
    att = Path(D) / "견적서.hwpx"
    att.write_bytes(b"PK\x03\x04fake")
    m, _ = g("dev1", "draft", "--title", "서버 구매 품의", "--body", "GPU 서버 1대 구매 요청", "--line", "devlead,ceo",
             "--cc", "hr", "--attach", str(att), "--submit")
    d1 = m["id"]
    inbox, _ = g("devlead", "list", "--box", "inbox")
    check("결재대기함에 도착", [r["id"] for r in inbox] == [d1])
    g("devlead", "approve", str(d1), "-m", "검토 완료")
    m, _ = g("ceo", "approve", str(d1))
    check("최종 승인 → 완료+문서번호", m["status"] == "done" and m["number"].startswith("DEV-"))
    _, rc = g("dev1", "edit", str(d1), "--title", "고침", ok=False)
    check("완료 문서 수정 불가", rc != 0)
    msgs, _ = g("hr", "msg", "list", "--unread")
    check("참조자 쪽지 알림", any("[참조]" in x["body"] for x in msgs))
    g("hr", "show", str(d1))
    m, _ = g("dev1", "show", str(d1))
    check("참조 열람 표시", "hr" in m["reads"])

    # 2) 반려 → 수정 → 재상신
    m, _ = g("sal1", "draft", "--title", "출장비 정산", "--body", "부산 출장 KTX", "--line", "sallead", "--submit")
    d2 = m["id"]
    _, rc = g("sallead", "reject", str(d2), ok=False)
    check("반려 사유 필수", rc != 0)
    g("sallead", "reject", str(d2), "-m", "영수증 누락")
    rej, _ = g("sal1", "list", "--box", "rejected")
    check("반려함", [r["id"] for r in rej] == [d2])
    g("sal1", "edit", str(d2), "--body", "부산 출장 KTX, 영수증 첨부")
    g("sal1", "submit", str(d2))
    m, _ = g("sallead", "approve", str(d2))
    check("재상신 후 완료", m["status"] == "done" and m["number"].startswith("SAL-"))

    # 3) 회수
    m, _ = g("hr", "draft", "--title", "채용 공고안", "--body", "개발자 채용", "--line", "ceo", "--submit")
    d3 = m["id"]
    g("hr", "withdraw", str(d3))
    drafts, _ = g("hr", "list", "--box", "drafts")
    check("회수 → 기안함", d3 in [r["id"] for r in drafts])
    _, rc = g("ceo", "approve", str(d3), ok=False)
    check("회수 문서 결재 불가", rc != 0)

    # 4) 대결 + 전결
    g("ceo", "delegate", "set", "--to", "hr")
    m, _ = g("devlead", "draft", "--title", "교육비 신청", "--body", "AI 교육", "--line", "ceo", "--submit")
    d4 = m["id"]
    m, _ = g("hr", "approve", str(d4), "-m", "대결 처리")
    check("대결 승인", m["status"] == "done" and m["line"][0]["proxy_for"] == "ceo" and m["line"][0]["acted_by"] == "hr")
    m, _ = g("dev1", "draft", "--title", "소모품 구매", "--body", "키보드", "--line", "devlead!,ceo", "--submit")
    m, _ = g("devlead", "approve", str(m["id"]))
    check("전결(!) → 이후 단계 생략", m["status"] == "done" and m["line"][1]["status"] == "skipped")

    # 5) 검색 + 권한 누수
    r, _ = g("sal1", "search", "-q", "GPU")
    check("타부서 문서 검색 누수 없음", r == [])
    r, _ = g("devlead", "search", "-q", "GPU 서버")
    check("결재선 사용자 검색 가능", [x["id"] for x in r] == [d1])
    r, _ = g("dev1", "search", "--title", "출장")
    check("타부서 제목 검색 누수 없음", r == [])
    r, _ = g("sallead", "search", "--status", "done", "--dept", "SAL")
    check("부서장 부서 문서 조회", d2 in [x["id"] for x in r])
    num = g("dev1", "show", str(d1))[0]["number"]
    r, _ = g("dev1", "number", num)
    check("문서번호 조회", [x["id"] for x in r] == [d1])
    r, _ = g("sal1", "number", num)
    check("타인 문서번호 조회 차단", r == [])
    _, rc = g("sal1", "show", str(d1), ok=False)
    check("타인 문서 직접 열람 차단", rc != 0)

    # 6) 게시판·쪽지
    g("ceo", "board", "post", "--title", "전자결재 시범 운영", "--body", "다음 주부터")
    _, rc = g("dev1", "board", "post", "--title", "공지", "--body", "x", ok=False)
    check("일반 사용자 공지 작성 불가", rc != 0)
    g("dev1", "msg", "send", "sal1", "협조 부탁드립니다")
    r, _ = g("sal1", "msg", "list", "--unread")
    check("쪽지 수신", any(x["sender"] == "dev1" for x in r))

    # 7) 감사로그 검증 + 변조 감지
    v, rc = g("admin", "verify-audit")
    check("감사로그 정상", rc == 0 and v["ok"])
    lines = (Path(D) / "audit.log").read_text(encoding="utf-8").splitlines(True)
    tampered = [ln.replace('"action": "reject"', '"action": "approve"') for ln in lines]
    (Path(D) / "audit.log").write_text("".join(tampered), encoding="utf-8")
    v, rc = g("admin", "verify-audit", ok=False)
    check("감사로그 변조 감지", rc == 3 and not v["ok"])
    (Path(D) / "audit.log").write_text("".join(lines), encoding="utf-8")
    _, rc = g("ceo", "verify-audit", ok=False)
    check("비관리자 감사 접근 차단", rc != 0)
finally:
    rmtree(D)

print(f"\n{'ALL PASS' if not FAILS else 'FAILURES: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
