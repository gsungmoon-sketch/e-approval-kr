"""gyeoljae 공통 상수·예외·도우미(순환 import 방지용 하위 모듈)."""
from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import sys
import time
import unicodedata
from pathlib import Path

ROLES = {"user": "일반", "head": "부서장", "admin": "관리자"}
STATUS_KO = {"draft": "기안중", "pending": "진행", "done": "완료", "rejected": "반려", "withdrawn": "회수"}
STEP_KO = {"waiting": "대기", "pending": "결재중", "approved": "승인", "rejected": "반려", "skipped": "전결생략"}
BOXES = ["inbox", "progress", "done", "rejected", "cc", "drafts", "dept"]
BOX_KO = {"inbox": "결재대기", "drafts": "기안함", "progress": "진행", "done": "완료", "rejected": "반려", "cc": "참조",
          "dept": "부서 문서함"}
BOARDS = {"notice": "공지사항", "free": "자유게시판"}

MAX_TITLE = 200
REJECT_MIN = 5  # 반려 사유 최소 글자 수
MAX_BODY = 200_000
MAX_LINE = 10
MAX_FILES = 10
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 30 * 1024 * 1024
ALLOWED_EXT = {".hwpx", ".hwp", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".pdf", ".txt", ".md",
               ".csv", ".png", ".jpg", ".jpeg", ".gif", ".zip"}
WIN_RESERVED = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} | {f"COM{i}" for i in range(1, 10)} \
    | {f"LPT{i}" for i in range(1, 10)} | {"COM¹", "COM²", "COM³", "LPT¹", "LPT²", "LPT³"}
USER_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,31}$")
DEPT_RE = re.compile(r"^[A-Z][A-Z0-9]{1,9}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SESSION_TTL = 8 * 3600
LOGIN_MAX_FAIL = 5        # 계정당
LOGIN_MAX_FAIL_IP = 20    # IP당(사무실 NAT 공유 고려)
LOGIN_WINDOW = 600


class GyeolError(Exception):
    code = "error"


class NotFound(GyeolError):
    code = "not_found"


class Forbidden(GyeolError):
    code = "forbidden"


class Invalid(GyeolError):
    code = "invalid"


class RateLimited(GyeolError):
    code = "rate_limited"


def now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def today() -> str:
    return _dt.date.today().isoformat()


def canon(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def default_data_dir() -> Path:
    env = os.environ.get("GYEOLJAE_DATA")
    if env:
        return Path(env)
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "gyeoljae"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "gyeoljae"
    xdg = os.environ.get("XDG_DATA_HOME")
    return (Path(xdg) if xdg else Path.home() / ".local" / "share") / "gyeoljae"


# ---------------------------------------------------------------- passwords
def hash_password(pw: str) -> str:
    if not isinstance(pw, str) or len(pw) < 8:
        raise Invalid("비밀번호는 8자 이상이어야 합니다")
    salt = secrets.token_bytes(16)
    n, r, p = 2 ** 14, 8, 1
    h = hashlib.scrypt(pw.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=32)
    return f"scrypt${n}${r}${p}${salt.hex()}${h.hex()}"


_DUMMY_HASH = None


def verify_password(pw: str, stored: str | None) -> bool:
    global _DUMMY_HASH
    if not stored:
        if _DUMMY_HASH is None:
            _DUMMY_HASH = hash_password("dummy-password-x")
        stored, ok_possible = _DUMMY_HASH, False
    else:
        ok_possible = True
    try:
        _, n, r, p, salt, h = stored.split("$")
        calc = hashlib.scrypt((pw or "").encode("utf-8"), salt=bytes.fromhex(salt), n=int(n), r=int(r),
                              p=int(p), dklen=len(h) // 2)
    except Exception:
        return False
    return hmac.compare_digest(calc.hex(), h) and ok_possible


# ---------------------------------------------------------------- filenames
def safe_filename(name: str) -> str:
    """첨부 파일명 정규화. 경로 성분 제거, Windows/Linux/macOS 모두에서 안전한 이름만 허용."""
    name = unicodedata.normalize("NFC", str(name or ""))
    name = name.replace("\\", "/").split("/")[-1]
    name = re.sub(r"[\x00-\x1f\x7f]", "", name)
    name = re.sub(r'[<>:"|?*]', "_", name)
    name = re.sub(r"[^\w .()\[\]+,=&#@!~'-]", "_", name)
    name = name.strip(" .")
    if not name or set(name) <= {"_", "."}:
        raise Invalid("파일명이 올바르지 않습니다")
    stem, ext = os.path.splitext(name)
    ext = ext.lower()
    if ext not in ALLOWED_EXT:
        raise Invalid(f"허용되지 않는 첨부 형식: {ext or '(없음)'}")
    stem = stem.strip(" .")[:80].strip(" .")
    if not stem:
        raise Invalid("파일명이 올바르지 않습니다")
    if stem.split(".")[0].upper().strip() in WIN_RESERVED:
        stem = "_" + stem
    return stem + ext


def _valid_date(s) -> bool:
    try:
        return bool(DATE_RE.match(s or "")) and _dt.date.fromisoformat(s).isoformat() == s
    except ValueError:
        return False


def _try_chmod(path: Path, mode: int):
    try:
        os.chmod(path, mode)
    except Exception:
        pass  # best effort (Windows/권한 없는 FS)


def _rmtree(path: Path):
    def fix(func, p, _exc):
        _try_chmod(p, 0o700)
        func(p)
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=fix)
    else:
        shutil.rmtree(path, onerror=fix)


def _unlink(path: Path):
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _atomic_write(path: Path, data: bytes):
    tmp = path.with_name(path.name + f".tmp{secrets.token_hex(4)}")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
