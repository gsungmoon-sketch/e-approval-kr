"""운영: 백업/복원, 감사체인 머리값 내보내기, 데모 데이터. App mixin."""
from __future__ import annotations

import json
import os
import re
import socket
import sqlite3
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path, PurePosixPath

from .core_base import Forbidden, Invalid, now_iso


class OpsMixin:
    def backup(self, out_path):
        """실행 중에도 일관된 백업(zip): 쓰기 잠금을 잡은 채 SQLite backup API + 문서·감사로그 복사."""
        out = Path(out_path)
        if out.exists():
            raise Invalid(f"이미 있는 파일: {out}")
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_db = tempfile.mkstemp(suffix=".db", dir=str(out.parent))
        os.close(tmp_fd)
        try:
            with self._tx() as c:  # 쓰기 잠금 유지 → 그동안 DB·파일 변경 없음
                self._audit(c, "-", "backup", None, {"file": out.name})
                head = c.execute("select v from kv where k='audit_head'").fetchone()
                src = sqlite3.connect(str(self.db_path))
                dst = sqlite3.connect(tmp_db)
                try:
                    # 커밋 전이지만 백업에는 backup 감사항목 직전 상태가 들어간다(정상)
                    src.backup(dst)
                finally:
                    dst.close()
                    src.close()
                os.close(os.open(str(out), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))  # 비밀값 포함 → 소유자만
                with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
                    z.write(tmp_db, "gyeoljae.db")
                    if self.audit_path.exists():
                        z.write(self.audit_path, "audit.log")
                    if (self.dir / "smtp.json").exists():
                        z.write(self.dir / "smtp.json", "smtp.json")
                    for base, prefix in ((self.docs_dir, "docs/"), (self.dir / "library", "library/"),
                                         (self.dir / "mail", "mail/")):
                        if not base.exists():
                            continue
                        for p in sorted(base.rglob("*")):
                            if p.is_file() and ".tmp" not in p.name:
                                z.write(p, prefix + p.relative_to(base).as_posix())
                    z.writestr("BACKUP.json", json.dumps({"created": now_iso(), "audit_head": json.loads(head["v"])
                                                          if head else None, "format": 1}, ensure_ascii=False))
        except BaseException:
            try:
                out.unlink()
            except FileNotFoundError:
                pass
            raise
        finally:
            try:
                os.unlink(tmp_db)
            except OSError:
                pass
        return {"file": str(out), "size": out.stat().st_size}

    @staticmethod
    def restore(zip_path, data_dir):
        """백업 zip 을 빈(또는 없는) 데이터 디렉터리로 복원. 기존 데이터 덮어쓰기 금지."""
        d = Path(data_dir)
        if d.exists() and any(d.iterdir()):
            raise Invalid(f"복원 대상 디렉터리가 비어 있지 않습니다: {d}")
        d.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path) as z:
            names = z.namelist()
            infos = z.infolist()
            if len(infos) > 200000 or sum(i.file_size for i in infos) > 20 * 1024 ** 3:
                raise Invalid("백업이 너무 큽니다(파일 20만 개/20GB 초과) — 손상 또는 악성 zip 의심")
            if "gyeoljae.db" not in names:
                raise Invalid("gyeoljae 백업 파일이 아닙니다")
            for n in names:
                pp = PurePosixPath(n)
                if pp.is_absolute() or ".." in pp.parts or "\\" in n or ":" in n:
                    raise Invalid(f"잘못된 경로가 들어 있습니다: {n}")
                if not (n in ("gyeoljae.db", "audit.log", "BACKUP.json", "smtp.json") or n.startswith(("docs/", "library/", "mail/"))):
                    raise Invalid(f"예상하지 못한 파일: {n}")
            for n in names:
                if n.endswith("/"):
                    continue
                target = d.joinpath(*PurePosixPath(n).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(n) as src, open(target, "wb") as dst:
                    dst.write(src.read())
        from .core import App
        app = App(d)
        return {"data": str(d), "verify": app.verify_audit()}

    def audit_head(self, actor=None):
        """감사체인 머리값(오프사이트 보관용). 나중에 이 값이 체인에 그대로 있는지로 재작성 여부 확인."""
        if actor is not None:
            self._require_admin(actor)
        with self._ro() as c:
            r = c.execute("select v from kv where k='audit_head'").fetchone()
        seq, h = json.loads(r["v"]) if r else (0, "0" * 64)
        return {"seq": seq, "hash": h, "at": now_iso(), "host": socket.gethostname()}

    def check_audit_head(self, seq, h):
        """보관해 둔 머리값이 현재 체인에 그대로 있는지(재작성 탐지)."""
        with self._ro() as c:
            r = c.execute("select line from audit_log where seq=?", (int(seq),)).fetchone()
        return bool(r and json.loads(r["line"])["hash"] == h)

    def seed_demo(self, password="demo1234!"):
        """시험용 데모(3부서·6명). 실제 운영에서는 쓰지 말 것."""
        if self.has_users():
            raise Invalid("이미 사용자가 있습니다(데모는 빈 데이터에만)")
        self.bootstrap("ADM", "관리부", "admin", "관리자", password)
        for code, name in (("MGT", "경영지원"), ("DEV", "개발"), ("SAL", "영업")):
            self.add_dept("admin", code, name)
        for u, n, dept, role, t in (("ceo", "김대표", "MGT", "head", "대표"), ("hr", "이인사", "MGT", "user", "대리"),
                                    ("devlead", "박개발", "DEV", "head", "팀장"), ("dev1", "최코딩", "DEV", "user", "사원"),
                                    ("sallead", "정영업", "SAL", "head", "팀장"), ("sal1", "한판매", "SAL", "user", "사원")):
            self.add_user("admin", u, n, dept, role, password, t)
        self.emp_move("admin", "ceo", position="ceo")  # 대표 직위(결재규정의 '대표' 단계 해당자)
        m = self.create_draft("dev1", "개발 서버 구매 품의", "GPU 서버 1대 구매를 요청합니다.\n- 예산: 500만원",
                              ["devlead", "ceo"], cc=["hr"], submit=True, form="general",
                              fields={"purpose": "개발 서버 증설"})
        self.approve("devlead", m["id"], "검토했습니다")
        self.create_draft("sal1", "부산 출장 신청", "11월 20일 고객사 방문", ["sallead"], submit=True, form="trip",
                          fields={"dest": "부산", "purpose": "고객사 방문", "period": "2026-11-20~2026-11-20"})
        self.board_post("admin", "notice", "전자결재 시범 운영 안내", "결재는 이 시스템으로 올려 주세요.")
        return {"accounts": ["admin", "ceo", "hr", "devlead", "dev1", "sallead", "sal1"], "password": password}


# ---------------------------------------------------------------- 서버 설정(server.json)·자동 백업
SERVER_DEFAULT = {"host": "127.0.0.1", "port": 8130, "backup_dir": "", "backup_keep": 14}
BACKUP_PREFIX = "e-approval-kr-"


def _check_server_conf(d):
    import ipaddress
    out = dict(SERVER_DEFAULT)
    host = str(d.get("host", out["host"])).strip()
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if host != "localhost":
            raise Invalid("주소는 127.0.0.1(이 컴퓨터만), 0.0.0.0(사내망) 또는 IP")
    try:
        port = int(d.get("port", out["port"]))
    except (TypeError, ValueError):
        port = 0
    if not 1024 <= port <= 65535:
        raise Invalid("포트는 1024~65535")
    bd = str(d.get("backup_dir") or "").strip()
    if bd and not Path(bd).is_absolute():
        raise Invalid("백업 폴더는 전체 경로로 적어 주세요")
    try:
        keep = int(d.get("backup_keep", 14))
    except (TypeError, ValueError):
        keep = 14
    out.update(host=host, port=port, backup_dir=bd, backup_keep=max(1, min(keep, 365)))
    return out


class ServerConfMixin:
    def server_conf(self):
        p = self.dir / "server.json"
        try:
            return _check_server_conf(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError, Invalid):
            return dict(SERVER_DEFAULT)

    def server_conf_set(self, actor, **kw):
        """actor=None 은 최초 설정 마법사(사용자 없을 때만)."""
        if actor is None:
            if self.has_users():
                raise Forbidden("이미 설정되어 있습니다")
        else:
            self._require_admin(actor)
        cur = self.server_conf()
        cur.update({k: v for k, v in kw.items() if v is not None})
        new = _check_server_conf(cur)
        if new["backup_dir"]:
            try:
                Path(new["backup_dir"]).mkdir(parents=True, exist_ok=True)
                probe = Path(new["backup_dir"]) / ".write-test"
                probe.write_bytes(b"ok")
                probe.unlink()
            except OSError as e:  # 서비스 계정(샌드박스)이 쓸 수 없는 폴더
                raise Invalid(f"백업 폴더에 쓸 수 없습니다(서비스 계정 권한 확인): {e.strerror}")
        from .core_base import _atomic_write
        _atomic_write(self.dir / "server.json", json.dumps(new, ensure_ascii=False, indent=1).encode("utf-8"))
        if actor:
            with self._tx() as c:
                self._audit(c, actor, "server_conf", None, new)
        return new

    def auto_backup(self, now=None):
        """하루 1번 backup_dir 에 백업, backup_keep 개 초과분(이 이름 형식만) 삭제. 만든 경로 또는 None."""
        conf = self.server_conf()
        if not conf["backup_dir"] or not self.has_users():
            return None
        d = Path(conf["backup_dir"])
        now = now or time.localtime()
        day = time.strftime("%Y%m%d", now)
        mine = sorted(p for p in d.glob(BACKUP_PREFIX + "*.zip") if re.fullmatch(
            re.escape(BACKUP_PREFIX) + r"\d{8}-\d{6}\.zip", p.name))
        if any(p.name.startswith(BACKUP_PREFIX + day) for p in mine):
            return None
        out = d / f"{BACKUP_PREFIX}{time.strftime('%Y%m%d-%H%M%S', now)}.zip"
        self.backup(out)
        mine.append(out)
        for old in mine[:-conf["backup_keep"]]:
            try:
                old.unlink()
            except OSError:
                pass
        return out

    def start_backup_worker(self, interval=3600):
        def loop():
            while True:
                try:
                    self.auto_backup()
                except Exception as e:  # 백업 실패는 로그만
                    print(f"자동 백업 실패: {e}", file=sys.stderr, flush=True)
                time.sleep(interval)
        t = threading.Thread(target=loop, daemon=True, name="gyeol-backup")
        t.start()
        return t
