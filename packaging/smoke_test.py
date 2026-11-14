#!/usr/bin/env python3
"""Install -> run -> log in -> uninstall smoke test for a built e-approval-kr package (used by CI).

    python packaging/smoke_test.py windows dist/e-approval-kr-setup-0.1.0-win-x64.exe
    python packaging/smoke_test.py macos   dist/e-approval-kr-0.1.0-macos-arm64.pkg
    python packaging/smoke_test.py linux   dist/e-approval-kr-0.1.0-linux-x64.tar.gz
    python packaging/smoke_test.py http    --port 18140      # only the HTTP part, against a running
                                                             # server whose admin exists (local check)

It REALLY installs a system service (needs admin/sudo) - meant for throw-away CI runners.
Steps: install silently -> GET /healthz == 200 -> service runs as the unprivileged account ->
`setup --admin-id ... --password-file -` (non-interactive, as the service user) -> POST /login
gives 303 + session cookie -> GET / with the cookie is 200 -> editor page, a font and the host
script are served, traversal is 404 -> uninstall silently -> service and
program gone, data directory kept.
"""
import argparse
import http.client
import json
import os
import secrets
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.parse
from pathlib import Path

HOST = "127.0.0.1"


def log(msg):
    print(f"[smoke] {msg}", flush=True)


def sh(cmd, check=True, **kw):
    log("$ " + " ".join(str(c) for c in cmd))
    r = subprocess.run([str(c) for c in cmd], text=True, encoding="utf-8", errors="replace",
                       capture_output=True, **kw)
    if r.stdout.strip():
        print(r.stdout.rstrip(), flush=True)
    if r.stderr.strip():
        print(r.stderr.rstrip(), file=sys.stderr, flush=True)
    if check and r.returncode != 0:
        raise SystemExit(f"command failed (rc={r.returncode})")
    return r


def request(port, method, path, body=None, headers=None, timeout=5):
    c = http.client.HTTPConnection(HOST, port, timeout=timeout)
    try:
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        return r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()
    finally:
        c.close()


def wait_health(port, timeout=90):
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        try:
            st, _, body = request(port, "GET", "/healthz")
            if st == 200:
                info = json.loads(body.decode("utf-8"))
                assert info.get("ok") is True, info
                log(f"/healthz 200 {info} after {time.time() - t0:.1f}s")
                return info
            last = st
        except (OSError, ValueError) as e:
            last = e
        time.sleep(1)
    raise SystemExit(f"/healthz did not answer 200 within {timeout}s (last: {last})")


def wait_down(port, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            request(port, "GET", "/healthz", timeout=2)
        except OSError:
            log("server is down")
            return
        time.sleep(1)
    raise SystemExit("server still answers after uninstall")


def check_login(port, user, password):
    st, h, _ = request(port, "POST", "/login",
                       body=urllib.parse.urlencode({"username": user, "password": "wrong-" + password}),
                       headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert st != 303 and "set-cookie" not in h, f"wrong password accepted: {st}"
    st, h, _ = request(port, "POST", "/login",
                       body=urllib.parse.urlencode({"username": user, "password": password}),
                       headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert st == 303, f"POST /login -> {st}, expected 303"
    cookie = h.get("set-cookie", "")
    assert cookie and "HttpOnly" in cookie, f"no session cookie: {cookie!r}"
    st, _, body = request(port, "GET", "/", headers={"Cookie": cookie.split(";", 1)[0]})
    assert st == 200, f"GET / with session -> {st}"
    log(f"login OK (303 + cookie, home 200, {len(body)} bytes)")
    check_editor(port, cookie.split(";", 1)[0])


def check_editor(port, cookie):
    """Packaged data files are served (also from inside a .pyz): editor page, one font, host script;
    a path-traversal attempt is 404."""
    for path, ctype, min_size in (("/editor/embed.html", "text/html", 1000),
                                  ("/editor/font/D2Coding-400.woff2", "font/woff2", 100000),
                                  ("/static/editor-host.js", "text/javascript", 500)):
        st, h, body = request(port, "GET", path, headers={"Cookie": cookie}, timeout=30)
        assert st == 200 and h.get("content-type", "").startswith(ctype) and len(body) >= min_size, \
            f"GET {path} -> {st} {h.get('content-type')} {len(body)} bytes"
    for path in ("/editor/../web.py", "/editor/hanword_embed.py"):
        st, _, _ = request(port, "GET", path, headers={"Cookie": cookie})
        assert st == 404, f"GET {path} -> {st}, expected 404"
    log("editor files OK (embed.html, font, editor-host.js; traversal 404)")


# ---------------------------------------------------------------- per-OS install/uninstall

class Windows:
    name = "e-approval-kr"

    def __init__(self, artifact):
        self.artifact = Path(artifact).resolve()
        self.app = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / self.name
        self.data = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / self.name

    def install(self):
        sh([self.artifact, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-",
            f"/LOG={Path(tempfile.gettempdir()) / 'eak-install.log'}"])

    def check_service(self):
        q = sh(["sc.exe", "query", self.name]).stdout
        assert "RUNNING" in q, "service not running"
        qc = sh(["sc.exe", "qc", self.name]).stdout
        assert "LocalService" in qc, "service does not run as NT AUTHORITY\\LocalService"

    def setup_admin(self, user, pw):
        sh([self.app / "e-approval-kr.exe", "--data", self.data, "setup", "--admin-id", user,
            "--admin-name", "Smoke Admin", "--password-file", "-"], input=pw + "\n")

    def uninstall(self):
        sh([self.app / "unins000.exe", "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"])
        # unins000.exe hands over to a temp copy and returns at once: wait for the real work
        t0 = time.time()
        while time.time() - t0 < 180:
            if sh(["sc.exe", "query", self.name], check=False).returncode != 0 \
                    and not (self.app / "e-approval-kr.exe").exists():
                break
            time.sleep(2)
        else:
            raise SystemExit("uninstall did not finish")

    def check_gone(self):
        assert sh(["sc.exe", "query", self.name], check=False).returncode != 0, "service still registered"
        assert not (self.app / "e-approval-kr.exe").exists(), "program files left"
        assert self.data.is_dir(), "data directory was deleted (should be kept)"


class MacOS:
    label = "com.e-approval-kr.server"
    lib = Path("/usr/local/lib/e-approval-kr")
    data = Path("/Library/Application Support/e-approval-kr")

    def __init__(self, artifact):
        self.artifact = Path(artifact).resolve()

    def install(self):
        sh(["sudo", "installer", "-pkg", self.artifact, "-target", "/"])

    def check_service(self):
        out = sh(["sudo", "launchctl", "print", f"system/{self.label}"]).stdout
        assert "state = running" in out, "daemon not running"
        pid = next(ln.split("=")[1].strip() for ln in out.splitlines() if ln.strip().startswith("pid ="))
        uid = sh(["ps", "-o", "uid=", "-p", pid]).stdout.strip()
        assert uid == sh(["id", "-u", "_eapprovalkr"]).stdout.strip(), f"daemon runs as uid {uid}"
        mode = sh(["sudo", "stat", "-f", "%Sp %Su", str(self.data)]).stdout.strip()
        assert mode.startswith("drwx------") and mode.endswith("_eapprovalkr"), f"data dir perms: {mode}"

    def setup_admin(self, user, pw):
        sh(["sudo", "/usr/local/bin/e-approval-kr-admin", "setup", "--admin-id", user,
            "--admin-name", "Smoke Admin", "--password-file", "-"], input=pw + "\n")

    def uninstall(self):
        sh(["sudo", self.lib / "uninstall.sh"])

    def check_gone(self):
        assert sh(["sudo", "launchctl", "print", f"system/{self.label}"], check=False).returncode != 0, \
            "daemon still loaded"
        assert not self.lib.exists() and not Path(f"/Library/LaunchDaemons/{self.label}.plist").exists()
        assert sh(["sudo", "test", "-d", str(self.data)], check=False).returncode == 0, "data deleted"


class Linux:
    unit = "e-approval-kr"
    prefix = Path("/opt/e-approval-kr")
    data = "/var/lib/e-approval-kr"

    def __init__(self, artifact):
        self.artifact = Path(artifact).resolve()

    def install(self):
        tmp = Path(tempfile.mkdtemp(prefix="eak-"))
        with tarfile.open(self.artifact) as tf:
            if hasattr(tarfile, "tar_filter"):
                tf.extractall(tmp, filter="tar")
            else:
                tf.extractall(tmp)
        top = next(tmp.iterdir())
        sh(["sudo", top / "install.sh"])

    def check_service(self):
        sh(["systemctl", "is-active", self.unit])
        pid = sh(["systemctl", "show", "-p", "MainPID", "--value", self.unit]).stdout.strip()
        uid = sh(["ps", "-o", "uid=", "-p", pid]).stdout.strip()
        assert uid == sh(["id", "-u", "e-approval-kr"]).stdout.strip(), f"service runs as uid {uid}"
        mode = sh(["sudo", "stat", "-c", "%a %U", self.data]).stdout.strip()
        assert mode == "700 e-approval-kr", f"data dir perms: {mode}"

    def setup_admin(self, user, pw):
        sh(["sudo", "/usr/local/bin/e-approval-kr-admin", "setup", "--admin-id", user,
            "--admin-name", "Smoke Admin", "--password-file", "-"], input=pw + "\n")

    def uninstall(self):
        sh(["sudo", self.prefix / "uninstall.sh"])

    def check_gone(self):
        assert sh(["systemctl", "cat", self.unit], check=False).returncode != 0, "unit still present"
        assert not self.prefix.exists(), "program dir left"
        assert sh(["sudo", "test", "-d", self.data], check=False).returncode == 0, "data deleted"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("os", choices=["windows", "macos", "linux", "http"])
    ap.add_argument("artifact", nargs="?")
    ap.add_argument("--port", type=int, default=8130)
    ap.add_argument("--user", default="admin")
    ap.add_argument("--password", help="http mode: password of an existing admin")
    a = ap.parse_args()
    if a.os == "http":
        wait_health(a.port, timeout=10)
        check_login(a.port, a.user, a.password or sys.exit("--password required in http mode"))
        log("PASS (http only)")
        return 0
    if not a.artifact:
        ap.error("artifact required")
    pkg = {"windows": Windows, "macos": MacOS, "linux": Linux}[a.os](a.artifact)
    pw = "Smoke-" + secrets.token_urlsafe(12)
    pkg.install()
    info = wait_health(a.port)
    pkg.check_service()
    pkg.setup_admin(a.user, pw)
    check_login(a.port, a.user, pw)
    pkg.uninstall()
    wait_down(a.port)
    pkg.check_gone()
    log(f"PASS {a.os} {Path(a.artifact).name} (server version {info.get('version')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
