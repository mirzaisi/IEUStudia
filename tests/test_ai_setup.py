"""Connecting Claude or ChatGPT: Claude Code from Anthropic's downloads (with its progress, checksum, Cancel and time
limit), the Codex CLI from the npm registry (checked, unpacked safely), Git for Windows Bash, the plan verdicts, the
test message and the status the set-up shows. No network (a local server stands in), no real installs."""

import base64
import errno
import hashlib
import http.server
import io
import json
import os
import sys
import tarfile
import threading
import time
from pathlib import Path

import pytest
import requests

from studia import ai, ai_setup, httpdl


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    """Installs, tests and copies that did not start are remembered per process; every test starts without any."""
    ai_setup._jobs.clear()
    ai_setup._tests.clear()
    monkeypatch.setattr(ai, "_failed", {})
    yield
    ai_setup._jobs.clear()
    ai_setup._tests.clear()


def _cached(value):
    """A stand-in for an lru_cache'd function (ai_status clears the cache first)."""
    def f():
        return value
    f.cache_clear = lambda: None
    return f


def _tgz(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


V = "package/vendor/x86_64-pc-windows-msvc/"
PACKAGE = _tgz({V + "bin/codex.exe": b"MZ codex", V + "codex-resources/codex-command-runner.exe": b"MZ runner",
                V + "codex-path/rg.exe": b"MZ rg", "package/package.json": b"{}", "package/vendor/../../evil.txt": b"x",
                "package/vendor/aarch64-pc-windows-msvc/bin/codex.exe": b"MZ arm"})


def _sri(data: bytes) -> str:
    return "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()


class _Resp:
    def __init__(self, data=None, body=b""):
        self._data, self._body = data, body
        self.headers = {"Content-Length": str(len(body))}

    def raise_for_status(self):
        pass

    def json(self):
        return self._data

    def iter_content(self, n):
        for i in range(0, len(self._body), n):
            yield self._body[i:i + n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Registry:
    """The npm registry for @openai/codex: latest -> 0.200.0, its win32-x64 build, the tarball."""

    def __init__(self, body=PACKAGE, integrity=None):
        self.body, self.integrity, self.urls = body, integrity if integrity is not None else _sri(PACKAGE), []

    def get(self, url, timeout=0, stream=False):
        self.urls.append(url)
        if url.endswith("/@openai/codex/latest"):
            return _Resp({"version": "0.200.0"})
        if url.endswith("/@openai/codex/0.200.0-win32-x64"):
            return _Resp({"dist": {"tarball": "https://registry.npmjs.org/codex.tgz", "integrity": self.integrity}})
        if url.endswith("codex.tgz"):
            return _Resp(body=self.body)
        raise AssertionError(url)


def _wait(which, timeout=10):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        j = ai_setup.job(which)
        if j.get("state") in ("done", "error", "cancelled"):
            return j
        time.sleep(0.02)
    raise AssertionError("install did not finish")


def _fake_download(body):
    """httpdl.download for the npm tarball: the bytes written to the file, its progress reported."""
    def download(url, dest, session=None, progress=None, cancel=None, parts=6):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)
        if progress:
            progress(len(body), len(body))
        return dest
    return download


@pytest.mark.skipif(sys.platform != "win32", reason="IEUStudia installs the Windows build of Codex")
def test_codex_is_installed_from_the_npm_registry_without_node(monkeypatch):
    reg = _Registry()
    monkeypatch.setattr(ai_setup, "_session", lambda: reg)
    monkeypatch.setattr(httpdl, "download", _fake_download(reg.body))
    monkeypatch.setattr(ai_setup.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(ai, "_codex_versions", {})
    monkeypatch.setattr(ai, "_probe", lambda cmd, timeout=20, env=None: (0, "codex-cli 0.200.0", ""))
    monkeypatch.setattr(ai.shutil, "which", lambda name: None)                 # no npm, no codex on PATH
    monkeypatch.setenv("APPDATA", str(ai_setup.tools_dir() / "nowhere"))
    events = []
    ai_setup.start_install("codex", events.append)
    j = _wait("codex")
    assert j["state"] == "done", j
    home = ai_setup.codex_home()
    assert (home / "bin" / "codex.exe").read_bytes() == b"MZ codex"
    assert (home / "codex-resources" / "codex-command-runner.exe").is_file() and (home / "codex-path" / "rg.exe").is_file()
    assert not list(home.rglob("evil.txt")) and not (home.parent / "evil.txt").exists()
    assert ai_setup.codex_exe() and ai.codex_cli_info()["path"] == ai_setup.codex_exe()
    assert any("Downloading Codex 0.200.0" in (e.get("line") or "") for e in events) and events[-1]["state"] == "done"
    assert reg.urls[:2] == ["https://registry.npmjs.org/@openai/codex/latest",
                            "https://registry.npmjs.org/@openai/codex/0.200.0-win32-x64"]
    assert not list((ai_setup.tools_dir() / "codex-setup").glob("*.tgz"))          # the package is not kept


@pytest.mark.skipif(sys.platform != "win32", reason="IEUStudia installs the Windows build of Codex")
def test_a_damaged_codex_download_is_refused(monkeypatch):
    reg = _Registry(integrity=_sri(b"something else"))
    monkeypatch.setattr(ai_setup, "_session", lambda: reg)
    monkeypatch.setattr(httpdl, "download", _fake_download(reg.body))
    ai_setup.start_install("codex", lambda e: None)
    j = _wait("codex")
    assert j["state"] == "error" and "damaged" in j["error"] and not ai_setup.codex_exe()
    assert not list((ai_setup.tools_dir() / "codex-setup").glob("*.tgz"))          # not continued next time
    with pytest.raises(RuntimeError, match="checksum"):
        ai_setup._check_integrity(b"x", "", "")


# ----------------------------------------------------------------------------- Claude Code from Anthropic's downloads
EXE = os.urandom(3 * 1024 * 1024 + 17)


class _Downloads(http.server.BaseHTTPRequestHandler):
    """downloads.claude.ai: latest -> 9.9.9, its manifest, the build (answering Range requests, as Anthropic's does)."""
    body, checksum, version = EXE, hashlib.sha256(EXE).hexdigest(), "9.9.9"
    seen: list = []

    def do_GET(self):  # noqa: N802 - http.server's naming
        self.seen.append((self.path, self.headers.get("Range")))
        if self.path == "/latest":
            return self._send(200, (self.version + "\n").encode())
        if self.path == "/9.9.9/manifest.json":
            return self._send(200, json.dumps({"version": "9.9.9", "platforms": {
                "win32-x64": {"checksum": self.checksum, "size": len(self.body)}}}).encode())
        if self.path == "/9.9.9/win32-x64/claude.exe":
            rng = self.headers.get("Range")
            if rng:
                a, b = rng.split("=")[1].split("-")
                a, b = int(a), int(b or len(self.body) - 1)
                return self._send(206, self.body[a:b + 1], {"Content-Range": f"bytes {a}-{b}/{len(self.body)}"})
            return self._send(200, self.body)
        self._send(404, b"no")

    def _send(self, code, body, headers=None):
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


SETUP_STAND_IN = r"""
import json, sys, time, subprocess
open(sys.argv[1], "w").write(json.dumps({"args": sys.argv[2:], "size": len(open(sys.argv[2], "rb").read())}))
print("Checking installation status...", flush=True)
if "SLOW" in sys.argv[1]:          # a step that prints nothing for a long time, with a child holding the output open
    subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    time.sleep(60)
print("Claude Code successfully installed!", flush=True)
"""


@pytest.fixture
def downloads(monkeypatch, tmp_path):
    _Downloads.body, _Downloads.checksum, _Downloads.version, _Downloads.seen = \
        EXE, hashlib.sha256(EXE).hexdigest(), "9.9.9", []
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Downloads)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(ai_setup, "CLAUDE_DOWNLOADS", f"http://127.0.0.1:{srv.server_address[1]}")
    monkeypatch.setattr(ai_setup, "claude_platform", lambda: "win32-x64")
    monkeypatch.setattr(ai_setup, "_session", requests.Session)
    stand_in = tmp_path / "setup_stand_in.py"
    stand_in.write_text(SETUP_STAND_IN, encoding="utf-8")
    record = tmp_path / "setup-args.json"
    monkeypatch.setattr(ai_setup, "_claude_setup_command",
                        lambda exe: [sys.executable, str(stand_in), str(record), exe, "install", "latest"])
    monkeypatch.setattr(ai, "claude_cli_info", _cached({"available": True, "path": "claude.exe", "version": "9.9.9"}))
    monkeypatch.setattr(ai_setup, "claude_versions_dir", lambda: tmp_path / "share" / "claude" / "versions")
    yield record
    srv.shutdown()


def test_claude_codes_own_set_up_finds_the_checked_build_and_does_not_download_it_again(downloads, tmp_path):
    """`claude install latest` downloads the whole build a second time (about 125 MB, nothing of it shown, from the
    start on every try) unless that version is already in its versions folder: IEUStudia puts its checked download
    there first, so the set-up step only checks it and sets it up."""
    events = []
    ai_setup.start_install("claude", events.append)
    assert _wait("claude")["state"] == "done"
    handed = tmp_path / "share" / "claude" / "versions" / "9.9.9"
    assert handed.read_bytes() == EXE                              # kept after IEUStudia's own copy was deleted
    assert not list(handed.parent.glob("*.ieustudia-*"))
    lines = [e.get("line") or "" for e in events]
    assert any(x.startswith("Setting up Claude Code… 0:") for x in lines), lines


def test_a_build_that_cant_be_handed_over_is_said_to_download_again(downloads, tmp_path):
    (tmp_path / "share").mkdir()
    (tmp_path / "share" / "claude").write_bytes(b"a file where Claude Code's folder would be")
    events = []
    ai_setup.start_install("claude", events.append)
    assert _wait("claude")["state"] == "done"
    lines = [e.get("line") or "" for e in events]
    assert any(x.startswith("Setting up Claude Code: it downloads itself once more") for x in lines), lines


class _Stalls(http.server.BaseHTTPRequestHandler):
    """A download whose connection stops sending after 512 KB (the Wi-Fi dropped): nothing more arrives."""
    body = os.urandom(16 * 1024 * 1024)

    def do_GET(self):  # noqa: N802 - http.server's naming
        a, b = self.headers.get("Range", "bytes=0-").split("=")[1].split("-")
        a, b = int(a), int(b or len(self.body) - 1)
        self.send_response(206)
        self.send_header("Content-Range", f"bytes {a}-{b}/{len(self.body)}")
        self.send_header("Content-Length", str(b - a + 1))
        self.end_headers()
        if b - a < 10:
            self.wfile.write(self.body[a:b + 1])
            return
        self.wfile.write(self.body[a:a + 512 * 1024])
        self.wfile.flush()
        time.sleep(8)

    def log_message(self, *args):
        pass


def test_cancel_stops_a_download_that_waits_for_a_stalled_connection_at_once(tmp_path):
    """Cancel used to change nothing for up to a minute when the connection had stopped sending (a read waits for
    its timeout, and the page kept saying it waits for the connection). Now the install says it is stopping and
    stops within a couple of seconds; the read still waiting ends by itself."""
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Stalls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    cancel, events, ended = threading.Event(), [], {}

    def run():
        try:
            ai_setup._download(f"http://127.0.0.1:{srv.server_address[1]}/claude.exe", tmp_path / "claude.exe",
                               requests.Session(), "Claude Code", "9.9.9", "claude", events.append, cancel,
                               len(_Stalls.body))
        except BaseException as exc:  # noqa: BLE001 - what the test looks at
            ended["exc"], ended["at"] = exc, time.monotonic()

    th = threading.Thread(target=run, daemon=True)
    th.start()
    time.sleep(1.5)
    pressed = time.monotonic()
    cancel.set()
    th.join(10)
    srv.shutdown()
    assert isinstance(ended.get("exc"), ai_setup.Cancelled) and ended["at"] - pressed < 4
    assert ai_setup._draining.get("claude") is not None           # the read still waiting, left to end by itself
    ai_setup._draining.pop("claude", None)


def test_the_next_download_waits_for_the_stopped_one_and_says_so(tmp_path, monkeypatch):
    old = threading.Thread(target=time.sleep, args=(1.2,), daemon=True)
    old.start()
    ai_setup._draining["codex"] = old
    started = []
    monkeypatch.setattr(httpdl, "download", lambda url, dest, **kw: started.append(old.is_alive()) or Path(dest))
    events = []
    dest = tmp_path / "codex.tgz"
    dest.write_bytes(b"x")
    ai_setup._download("http://x/codex.tgz", dest, None, "Codex", "1.0", "codex", events.append, threading.Event())
    assert started == [False]                                      # only once the stopped one let go of its files
    assert "Waiting for the stopped download to let go of its files…" in [e.get("line") for e in events]


def test_cancel_says_it_is_stopping_at_once(monkeypatch):
    seen = []
    monkeypatch.setitem(ai_setup._jobs, "codex", {"state": "running", "line": "Downloading Codex 1.0… 3 MB"})
    monkeypatch.setitem(ai_setup._cancels, "codex", threading.Event())
    assert ai_setup.cancel_install("codex", seen.append)
    assert seen[-1]["line"] == "Stopping…" and seen[-1]["stopping"] is True and seen[-1]["state"] == "running"


def test_looking_up_the_latest_version_stops_at_once_on_cancel():
    cancel = threading.Event()
    threading.Timer(0.3, cancel.set).start()
    t0 = time.monotonic()
    with pytest.raises(ai_setup.Cancelled):
        ai_setup._until_cancel(lambda: time.sleep(20), cancel, "lookup")
    assert time.monotonic() - t0 < 4
    assert ai_setup._until_cancel(lambda: 5, threading.Event(), "lookup") == 5
    with pytest.raises(KeyError):
        ai_setup._until_cancel(lambda: {}["x"], threading.Event(), "lookup")


def test_a_build_claude_code_already_has_is_left_to_it(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_setup, "claude_versions_dir", lambda: tmp_path / "versions")
    (tmp_path / "versions").mkdir()
    (tmp_path / "versions" / "9.9.9").write_bytes(b"its own")
    exe = tmp_path / "claude-9.9.9.exe"
    exe.write_bytes(b"ours")
    assert ai_setup._hand_over(exe, "9.9.9") and (tmp_path / "versions" / "9.9.9").read_bytes() == b"its own"
    (tmp_path / "versions" / "9.9.9").write_bytes(b"")               # the empty file its lock leaves: replaced
    assert ai_setup._hand_over(exe, "9.9.9") and (tmp_path / "versions" / "9.9.9").read_bytes() == b"ours"
    assert not ai_setup._hand_over(exe, "../9.9.9")


def test_claude_code_is_downloaded_with_its_progress_checked_and_set_up(downloads):
    """Before, PowerShell ran Anthropic's script under a 15-minute limit for the whole thing, with one line that never
    changed: a slow connection never finished. Now IEUStudia downloads it itself (resumable, its size and speed shown),
    checks it against the manifest's SHA-256 and lets the build set itself up, as install.ps1 does."""
    events = []
    ai_setup.start_install("claude", events.append)
    j = _wait("claude")
    assert j["state"] == "done", j
    ran = json.loads(downloads.read_text(encoding="utf-8"))
    assert ran["args"][1:] == ["install", "latest"] and ran["size"] == len(EXE)
    lines = [e.get("line") or "" for e in events]
    assert "Looking up the latest Claude Code…" in lines
    assert any(x.startswith("Downloading Claude Code 9.9.9… ") and " of 3 MB" in x for x in lines), lines
    assert "Checking the download…" in lines and any(x.startswith("Setting up Claude Code… ") for x in lines)
    assert not list((ai_setup.tools_dir() / "claude-setup").glob("claude-*"))          # nothing left behind
    assert [p for p, _ in _Downloads.seen][:2] == ["/latest", "/9.9.9/manifest.json"]


def test_a_damaged_claude_download_is_refused_and_not_set_up(downloads):
    _Downloads.checksum = "0" * 64
    ai_setup.start_install("claude", lambda e: None)
    j = _wait("claude")
    assert j["state"] == "error" and "damaged" in j["error"] and not downloads.exists()
    assert not list((ai_setup.tools_dir() / "claude-setup").glob("claude-*"))


def test_a_cut_off_claude_download_goes_on_where_it_stopped(downloads):
    folder = ai_setup.tools_dir() / "claude-setup"
    folder.mkdir(parents=True)
    (folder / "claude-8.0.0-win32-x64.exe").write_bytes(b"an older version's leftover")
    have = 2 * 1024 * 1024
    part = folder / "claude-9.9.9-win32-x64.exe"
    part.with_name(part.name + ".parts.json").write_text(json.dumps({"total": len(EXE), "version": ""}), encoding="utf-8")
    part.with_name(part.name + ".part0").write_bytes(EXE[:have])          # the connection dropped here last time
    ai_setup.start_install("claude", lambda e: None)
    assert _wait("claude")["state"] == "done"
    assert ("/9.9.9/win32-x64/claude.exe", f"bytes={have}-{len(EXE) - 1}") in _Downloads.seen
    assert not (folder / "claude-8.0.0-win32-x64.exe").exists()


def test_a_claude_version_answer_that_is_no_version_is_said_plainly(downloads):
    _Downloads.version = "<html>blocked</html>"
    ai_setup.start_install("claude", lambda e: None)
    j = _wait("claude")
    assert j["state"] == "error" and "did not answer with a Claude Code version" in j["error"]


def test_cancel_stops_the_set_up_with_the_programs_it_started(downloads, tmp_path, monkeypatch):
    """The set-up step prints nothing for a long time and its child holds the output open: Cancel still ends it at
    once (the whole tree), and the page hears "cancelled", not an error."""
    slow = tmp_path / "SLOW-args.json"
    stand_in = tmp_path / "setup_stand_in.py"
    monkeypatch.setattr(ai_setup, "_claude_setup_command",
                        lambda exe: [sys.executable, str(stand_in), str(slow), exe, "install", "latest"])
    events = []
    ai_setup.start_install("claude", events.append)
    t0 = time.monotonic()
    while not slow.exists() and time.monotonic() - t0 < 20:
        time.sleep(0.05)
    time.sleep(1.3)
    assert ai_setup.job("claude")["line"].startswith("Setting up Claude Code… 0:0")       # the time goes by
    assert ai_setup.cancel_install("claude")
    j = _wait("claude", timeout=20)
    assert j["state"] == "cancelled" and j["error"] == "" and time.monotonic() - t0 < 20
    assert not ai_setup.cancel_install("claude")                               # nothing left to stop
    assert list((ai_setup.tools_dir() / "claude-setup").glob("claude-9.9.9-*"))     # kept: the next try sets it up


def test_the_time_limit_ends_a_silent_installer_and_says_it_took_too_long(tmp_path):
    stand_in = tmp_path / "setup_stand_in.py"
    stand_in.write_text(SETUP_STAND_IN, encoding="utf-8")
    exe = tmp_path / "fake.exe"
    exe.write_bytes(b"x")
    t0 = time.monotonic()
    with pytest.raises(ai_setup.TookTooLong) as err:
        ai_setup._run_logged([sys.executable, str(stand_in), str(tmp_path / "SLOW.json"), str(exe)], "claude",
                             lambda e: None, timeout_s=1.5)
    assert time.monotonic() - t0 < 15                       # the child holding the output was ended too
    assert "took too long" in ai_setup._install_error("claude", err.value)


def test_install_failures_are_said_with_what_to_do(tmp_path, monkeypatch):
    e = ai_setup._install_error
    virus = OSError(None, "Operation did not complete successfully because the file contains a virus", None, 225)
    assert e("claude", virus).startswith("Windows Security (or your antivirus) blocked Claude Code.")
    assert e("codex", OSError(errno.ENOSPC, "No space left on device")).startswith("Your disk is full")
    lost = e("claude", requests.ConnectionError("Connection aborted"))
    assert lost.startswith("Could not download Claude Code: Can't reach the server.")
    assert lost.endswith("(Downloading again continues where it stopped.)") and lost.count("internet connection") == 1
    assert e("claude", RuntimeError("Could not install - another process is currently installing Claude.")) == \
        "Installing Claude Code did not work: Could not install - another process is currently installing Claude."
    import shutil
    import types

    monkeypatch.setattr(shutil, "disk_usage", lambda p: types.SimpleNamespace(free=200 * 1024 * 1024))
    with pytest.raises(RuntimeError, match=r"There isn't enough free space on .* for Claude Code: it needs about 1\.0 GB"):
        ai_setup._need_space(tmp_path / "x", 1024 ** 3, "claude")


def test_the_progress_line_shows_size_speed_and_time_left():
    speed = ai_setup._Speed()
    mb = ai_setup.MB
    assert ai_setup._progress_line("Claude Code", "2.1.318", 0, 327 * mb, speed) == \
        "Downloading Claude Code 2.1.318… 0 of 327 MB"
    for i in range(5):
        speed.add(i * 2 * mb, now=100.0 + i)
    line = ai_setup._progress_line("Claude Code", "2.1.318", 8 * mb, 327 * mb, speed)
    assert line == "Downloading Claude Code 2.1.318… 8 of 327 MB · 2.0 MB/s · about 3 min left"
    assert ai_setup._progress_line("Claude Code", "2.1.318", 326 * mb, 327 * mb, speed).endswith("less than a minute left")
    slow = ai_setup._Speed()
    for i in range(5):
        slow.add(100 * mb + i * 30 * 1024, now=200.0 + i)
    assert ai_setup._progress_line("Claude Code", "2.1.318", 100 * mb, 327 * mb, slow).endswith(
        "· 30 KB/s · about 130 min left · the connection is very slow")
    stuck = ai_setup._Speed()
    for i in range(8):                                    # the connection dropped: nothing arrives
        stuck.add(150 * mb, now=300.0 + i)
    assert ai_setup._progress_line("Claude Code", "2.1.318", 150 * mb, 327 * mb, stuck) == \
        "Downloading Claude Code 2.1.318… 150 of 327 MB · nothing is arriving: waiting for the connection, then it goes on by itself"


def test_unpacking_replaces_an_older_copy(tmp_path):
    dest = tmp_path / "codex"
    (dest / "bin").mkdir(parents=True)
    (dest / "bin" / "codex.exe").write_bytes(b"old")
    (dest / "stale.txt").write_text("x")
    ai_setup.unpack_codex(PACKAGE, "x86_64-pc-windows-msvc", dest)
    assert (dest / "bin" / "codex.exe").read_bytes() == b"MZ codex" and not (dest / "stale.txt").exists()
    with pytest.raises(RuntimeError, match="no codex.exe"):
        ai_setup.unpack_codex(_tgz({"package/README.md": b"hi"}), "x86_64-pc-windows-msvc", tmp_path / "other")


def test_git_bash_is_found_and_passed_to_claude_code(monkeypatch, tmp_path):
    for k in ("CLAUDE_CODE_GIT_BASH_PATH", "ProgramW6432", "ProgramFiles(x86)"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "pf"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "lad"))
    monkeypatch.setattr(ai_setup.shutil, "which", lambda name: None)
    assert ai_setup.git_bash() == "" and "CLAUDE_CODE_GIT_BASH_PATH" not in ai_setup.claude_env()
    bash = tmp_path / "pf" / "Git" / "bin" / "bash.exe"
    bash.parent.mkdir(parents=True)
    bash.write_bytes(b"MZ")
    assert ai_setup.git_bash() == str(bash) and ai_setup.claude_env()["CLAUDE_CODE_GIT_BASH_PATH"] == str(bash)
    monkeypatch.setenv("CLAUDE_CODE_GIT_BASH_PATH", str(bash))       # the user's own setting is kept
    assert ai_setup.claude_env()["CLAUDE_CODE_GIT_BASH_PATH"] == str(bash)


def test_an_api_key_set_on_the_pc_never_reaches_claude_code(monkeypatch):
    """A student's ANTHROPIC_API_KEY (set for coursework) made Claude Code bill every note per use, or fail as "not
    signed in" while the set-up showed "Signed in". IEUStudia now leaves it out (and the other ways of billing or
    routing elsewhere), but keeps a subscription token from claude setup-token."""
    for k, v in (("ANTHROPIC_API_KEY", "sk-ant-api03-x"), ("ANTHROPIC_AUTH_TOKEN", "t"), ("ANTHROPIC_BASE_URL", "http://gw"),
                 ("CLAUDE_CODE_USE_BEDROCK", "1"), ("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-x")):
        monkeypatch.setenv(k, v)
    env = ai_setup.claude_env()
    assert not set(ai_setup.NOT_PASSED_ON) & set(env) and env["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat01-x"
    assert ai_setup.left_out() == ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_BEDROCK"]
    # and a real run of Claude Code (a stand-in that prints what it got) has no key either
    probe = ("import json, os, sys; sys.stdin.read(); print(json.dumps({'type': 'result', 'subtype': 'success', "
             "'usage': {}, 'result': os.environ.get('ANTHROPIC_API_KEY', '-')}))")
    monkeypatch.setattr(ai, "_require_claude", lambda: sys.executable)
    monkeypatch.setattr(ai, "_command", lambda model, effort, sp, partial=False: [sys.executable, "-c", probe])
    assert ai._claude_one_shot("S", [{"type": "text", "text": "x"}])["text"] == "-"


def test_how_claude_code_is_signed_in_is_read_and_said(monkeypatch):
    monkeypatch.setattr(ai, "claude_cli_info", _cached({"available": True, "path": "claude.exe", "version": "2.1.318"}))
    status = {}
    monkeypatch.setattr(ai, "_run", lambda cmd, timeout=20, env=None: (0, json.dumps(status)))
    status.update(loggedIn=True, authMethod="api_key", apiKeySource="ANTHROPIC_API_KEY")   # in Claude Code's settings
    a = ai.claude_auth()
    assert a["plan"] == "api" and a["key_source"] == "ANTHROPIC_API_KEY"
    ps = ai_setup.plan_status("claude", a["plan"], a["logged_in"], source=a["key_source"])
    assert ps["ok"] is None and ps["label"] == "API key" and "billed per use" in ps["note"] and "settings.json" in ps["note"]
    status.update(apiKeySource="/login managed key")                     # signed in with an Anthropic Console account
    a = ai.claude_auth()
    ps = ai_setup.plan_status("claude", a["plan"], a["logged_in"], source=a["key_source"])
    assert ps["ok"] and ps["label"] == "Anthropic Console (API)" and "billed per use" in ps["note"]
    status.clear()
    status.update(loggedIn=True, authMethod="claude.ai", subscriptionType="max", email="a@b.c")
    a = ai.claude_auth()
    assert a["plan"] == "max" and a["email"] == "a@b.c"
    assert ai_setup.plan_status("claude", "max", True, ignored=["ANTHROPIC_API_KEY"])["note"] == \
        "IEUStudia leaves out the ANTHROPIC_API_KEY set on this PC, so Claude Code uses your subscription."


@pytest.mark.parametrize("said, starts", [
    ("Invalid API key · Fix external API key", "Claude Code is set to use an API key that Anthropic refused."),
    ("Credit balance is too low", "The Anthropic API account Claude Code is signed in with has no credit left."),
    ("API Error: Connection error.", "Claude could not be reached: check your internet connection"),
    ("API Error: unable to get local issuer certificate", "Claude Code could not make a secure connection to Anthropic"),
    ("[WinError 225] Operation did not complete successfully because the file contains a virus or potentially unwanted "
     "software", "Windows Security (or your antivirus) blocked Claude Code."),
    ("Failed to refresh OAuth token: another Claude Code process is refreshing it or exited mid-refresh.",
     "Claude Code was renewing your sign-in in another window."),
    ("Invalid API key · Please run /login", "Claude Code is not signed in."),
    ("Failed to authenticate: OAuth token revoked.", "Claude Code is not signed in."),
])
def test_claude_codes_failures_are_explained_once(said, starts, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    got = ai.friendly_error(said)
    assert got.startswith(starts) and ai.friendly_error(got) == got           # explaining it again changes nothing


def test_an_openai_key_set_on_the_pc_never_reaches_the_codex_cli(monkeypatch):
    for k in ("CODEX_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL"):
        monkeypatch.setenv(k, "x")
    assert not set(ai.CODEX_NOT_PASSED_ON) & set(ai._codex_env()) and "PATH" in {k.upper() for k in ai._codex_env()}
    monkeypatch.setattr(ai, "codex_cli_info", _cached({"available": True, "path": "codex.exe", "version": "0.200.0"}))
    cmd, env = ai._login_command("codex")
    assert cmd == ["codex.exe", "login"] and "OPENAI_API_KEY" not in env


def test_a_test_message_that_runs_out_of_time_says_so(monkeypatch):
    """It ended as "Claude: Claude did not return a result." or "ChatGPT did not return an answer."."""
    sleeper = [sys.executable, "-c", "import time; time.sleep(30)"]
    monkeypatch.setattr(ai, "_require_claude", lambda: sys.executable)
    monkeypatch.setattr(ai, "_command", lambda model, effort, sp, partial=False: sleeper)
    with pytest.raises(RuntimeError) as err:
        ai._claude_one_shot("S", [{"type": "text", "text": "x"}], timeout_s=1)
    assert str(err.value) == "Claude took too long to answer. Try again; if it keeps happening, pick a lower reasoning " \
        "level." and ai.friendly_error(str(err.value)) == str(err.value)
    monkeypatch.setattr(ai, "_codex_command", lambda work, model, effort, images=(): sleeper)
    monkeypatch.setattr(ai, "_codex_model", lambda: "")
    with pytest.raises(RuntimeError, match=r"^ChatGPT took too long to answer through the Codex CLI\."):
        ai._codex_once("S", [{"type": "text", "text": "x"}], "sonnet", "", None, None, 1)
    assert ai.friendly_error("Claude did not return a result.") == "Claude did not return a result."


def test_a_refused_subscription_token_set_on_the_pc_is_named(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-x")
    got = ai.friendly_error("Failed to authenticate: OAuth token revoked. Please log in again.")
    assert got.startswith("Claude Code uses the CLAUDE_CODE_OAUTH_TOKEN set on this PC") and ai.friendly_error(got) == got


def test_plan_verdicts():
    p = ai_setup.plan_status
    assert p("claude", "max", True) == {"ok": True, "label": "Claude Max", "note": ""}
    assert p("claude", "free", True)["ok"] is False and "Pro or Max" in p("claude", "free", True)["note"]
    assert p("claude", "api", True)["ok"] and "billed per use" in p("claude", "api", True)["note"]
    assert p("claude", "", True)["ok"] is None and p("claude", "pro", False)["ok"] is None
    assert p("codex", "plus", True)["label"] == "ChatGPT Plus"
    assert p("codex", "free", True)["ok"] is None and "test message" in p("codex", "go", True)["note"]   # OpenAI decides
    assert p("codex", "chatgpt", True) == {"ok": None, "label": "ChatGPT",
                                           "note": "Codex does not show your plan; the test message below shows whether it works."}


def test_the_test_message(monkeypatch):
    monkeypatch.setattr(ai, "_claude_one_shot", lambda *a, **k: {"text": "OK"})
    r = ai_setup.test("claude")
    assert r["ok"] and r["error"] == "" and ai_setup.last_test("claude")["ok"]

    def boom(*a, **k):
        raise RuntimeError("Your organization does not have access to Claude Code.")

    monkeypatch.setattr(ai, "_claude_one_shot", boom)
    r = ai_setup.test("claude")
    assert not r["ok"] and r["error"] == "Your Claude plan doesn't include Claude Code. You need Claude Pro or Max."
    ai_setup.forget_test("claude")
    assert ai_setup.last_test("claude") == {}
    monkeypatch.setattr(ai, "_codex_one_shot", lambda *a, **k: {"text": ""})
    assert ai_setup.test("codex") == {**ai_setup.last_test("codex"), "ok": False, "error": "No answer came back."}
    monkeypatch.setattr(ai, "_chatgpt_one_shot", lambda *a, **k: {"text": "OK"})
    assert ai_setup.test("chatgpt")["ok"] and not ai_setup.last_test("codex")["ok"]
    from studia import chatgpt

    def refused(*a, **k):
        raise chatgpt.NotEligible("Your plan can't be used by apps.")      # the page knows it by the code, in any language

    monkeypatch.setattr(ai, "_chatgpt_one_shot", refused)
    r = ai_setup.test("chatgpt")
    assert not r["ok"] and r["code"] == "plan_not_eligible" and r["error"] == "Your plan can't be used by apps."
    # the program's status says signed in, the service refuses the sign-in: the page offers to sign in again by this
    # code (the sign-in step had no button, and the test step pointed at one)

    def ended(*a, **k):
        raise RuntimeError("Failed to authenticate. API Error: 401 OAuth token has expired. Please run /login")

    monkeypatch.setattr(ai, "_claude_one_shot", ended)
    r = ai_setup.test("claude")
    assert r["code"] == "signed_out" and r["error"] == "Claude Code is not signed in. Settings → AI → Sign in with Claude."

    def used(*a, **k):
        raise RuntimeError(ai._codex_error("Your access token could not be refreshed because your refresh token was "
                                           "already used."))

    monkeypatch.setattr(ai, "_codex_one_shot", used)
    assert ai_setup.test("codex")["code"] == "signed_out"


def test_status_for_one_ai_only(monkeypatch):
    monkeypatch.setattr(ai, "claude_cli_info", _cached({"available": True, "path": "claude.exe", "version": "2.1.0"}))
    monkeypatch.setattr(ai, "claude_auth", lambda: {"logged_in": True, "plan": "pro", "email": "a@b.c"})
    s = ai.ai_status("claude")
    assert "chatgpt" not in s and "codex" not in s and "local" not in s
    c = s["claude"]
    assert c["plan_status"]["label"] == "Claude Pro" and c["job"] == {} and c["test"] == {}


def test_the_full_status_asks_every_ai_at_once(monkeypatch):
    from studia import chatgpt

    def slow(value):
        def f():
            time.sleep(0.4)                     # a program started and asked (claude auth status, codex login status)
            return value
        return f

    monkeypatch.setattr(ai, "claude_cli_info", _cached({"available": True, "path": "claude.exe", "version": "2.1.0"}))
    monkeypatch.setattr(ai, "codex_cli_info", _cached({"available": True, "path": "codex.exe", "version": "0.1"}))
    monkeypatch.setattr(ai, "claude_auth", slow({"logged_in": True, "plan": "max", "email": "a@b.c"}))
    monkeypatch.setattr(ai, "codex_auth", slow({"logged_in": False, "plan": "", "email": ""}))
    monkeypatch.setattr(chatgpt, "status", slow({"signed_in": False, "email": "", "plan_allowed": False,
                                                 "registered": False, "sign_in": {}}))
    t0 = time.monotonic()
    s = ai.ai_status()
    assert time.monotonic() - t0 < 1.0                                  # not 1.2 s one after another
    assert s["claude"]["logged_in"] and not s["codex"]["logged_in"] and not s["chatgpt"]["signed_in"]
    assert "local" in s and s["chosen"] is not None


def test_the_codex_version_is_asked_again_only_when_its_file_changes(tmp_path, monkeypatch):
    exe = tmp_path / "codex.exe"
    exe.write_bytes(b"MZ 1")
    runs = []
    monkeypatch.setattr(ai, "_codex_versions", {})
    monkeypatch.setattr(ai, "_codex_candidates", lambda: [str(exe)])
    monkeypatch.setattr(ai, "_probe", lambda cmd, timeout=20, env=None: runs.append(cmd) or (0, "codex-cli 0.200.0", ""))
    for _ in range(3):
        ai.codex_cli_info.cache_clear()                                 # (as ai_status does each time)
        assert ai.codex_cli_info()["version"] == "0.200.0"
    assert len(runs) == 1
    exe.write_bytes(b"MZ 2 updated")                                    # it updated itself
    monkeypatch.setattr(ai, "_probe", lambda cmd, timeout=20, env=None: runs.append(cmd) or (0, "codex-cli 0.201.0", ""))
    ai.codex_cli_info.cache_clear()
    assert ai.codex_cli_info()["version"] == "0.201.0" and len(runs) == 2
    ai.codex_cli_info.cache_clear()


# ----------------------------------------------------------------------------- which copy of the CLI is used
def _exe(path, data=b"MZ"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path)


def _answers(table):
    """_probe for stand-in programs: {(path, first argument): output} (a missing one did not start)."""
    seen = []

    def probe(cmd, timeout=20, env=None):
        seen.append(cmd)
        key = (cmd[0], cmd[1])
        if key not in table:
            return -1, "", "the file is damaged or not made for this PC"
        return 0, table[key], ""
    probe.seen = seen
    return probe


def test_npm_shims_lead_to_the_program_itself_wherever_npm_put_it(tmp_path, monkeypatch):
    """npm's codex.cmd starts Node.js, which starts codex.exe: the exe is used (no Node.js needed, Cancel ends it),
    also for npm with another folder (a shim on PATH), Arm PCs and the older layout of the package."""
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    other = tmp_path / "D-npm"
    _exe(other / "codex.cmd", b"@echo off")
    monkeypatch.setattr(ai.shutil, "which", lambda name: str(other / "codex.cmd") if name == "codex" else None)
    pkg = other / "node_modules" / "@openai" / "codex"
    arm = _exe(pkg / "node_modules" / "@openai" / "codex-win32-arm64" / "vendor" / "aarch64-pc-windows-msvc" / "bin" / "codex.exe")
    older = _exe(tmp_path / "roaming" / "npm" / "node_modules" / "@openai" / "codex" / "vendor" / "x86_64-pc-windows-msvc"
                 / "codex" / "codex.exe")
    ours = _exe(ai_setup.codex_home() / "bin" / "codex.exe")
    found = ai._codex_candidates()
    assert found == [older, arm, ours] and not any(p.lower().endswith(".cmd") for p in found)
    claude = _exe(other / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe")
    winget = _exe(tmp_path / "local" / "Microsoft" / "WinGet" / "Links" / "claude.exe")
    monkeypatch.setattr(ai.shutil, "which", lambda name: str(other / "claude.cmd") if name == "claude" else None)
    assert ai._candidates() == [claude, winget]


def test_an_older_codex_on_the_pc_is_passed_over_for_ieustudias_own(tmp_path, monkeypatch):
    """An older Codex CLI (installed by the student long ago) came first and failed every request with "unexpected
    argument", shown as "ChatGPT did not return an answer"; installing again didn't help, the old one still came first."""
    old, ours = _exe(tmp_path / "npm" / "codex.exe"), _exe(tmp_path / "ours" / "codex.exe")
    monkeypatch.setattr(ai, "_codex_versions", {})
    monkeypatch.setattr(ai, "_codex_candidates", lambda: [old, ours])
    probe = _answers({(old, "--version"): "codex-cli 0.40.0", (old, "exec"): "Usage: codex exec [OPTIONS]\n  --json\n",
                      (ours, "--version"): "codex-cli 0.200.0"})
    monkeypatch.setattr(ai, "_probe", probe)
    ai.codex_cli_info.cache_clear()
    assert ai.codex_cli_info() == {"available": True, "path": ours, "version": "0.200.0"}
    assert [old, "exec", "--help"] in probe.seen                        # an older one is asked what it takes
    monkeypatch.setattr(ai, "_codex_candidates", lambda: [old])
    ai.codex_cli_info.cache_clear()
    info = ai.codex_cli_info()
    assert not info["available"] and info["too_old"] == {"path": old, "version": "0.40.0"}
    with pytest.raises(RuntimeError, match=r"The Codex CLI on this PC \(0\.40\.0\) is too old for IEUStudia"):
        ai._codex_command(tmp_path, "sonnet", "")
    broken = _exe(tmp_path / "broken" / "codex.exe")
    monkeypatch.setattr(ai, "_codex_candidates", lambda: [broken])
    ai.codex_cli_info.cache_clear()
    info = ai.codex_cli_info()
    assert not info["available"] and info["problem"] == {"path": broken, "error": "the file is damaged or not made for this PC"}
    ai.codex_cli_info.cache_clear()


def test_a_codex_that_turns_down_a_flag_says_to_update_and_is_passed_over(tmp_path, monkeypatch):
    clap = ("import sys; sys.stdin.read(); sys.stderr.write(\"error: unexpected argument '--ignore-user-config' found\\n\\n"
            "  tip: to pass '--ignore-user-config' as a value, use '-- --ignore-user-config'\\n\\nUsage: codex exec "
            "[OPTIONS] [PROMPT]\\n\\nFor more information, try '--help'.\\n\"); sys.exit(2)")
    monkeypatch.setattr(ai, "_codex_versions", {sys.executable: (ai._codex_stamp(sys.executable), "0.150.0", True)})
    monkeypatch.setattr(ai, "_codex_command", lambda work, model, effort, images=(): [sys.executable, "-c", clap])
    monkeypatch.setattr(ai, "_codex_model", lambda: "")
    with pytest.raises(RuntimeError) as err:
        ai._codex_once("S", [{"type": "text", "text": "x"}], "sonnet", "", None, None, 60)
    assert str(err.value).startswith("The Codex CLI on this PC (0.150.0) is too old for IEUStudia.")
    assert ai._codex_versions[sys.executable][2] is False                # not used again until it changes
    # a copy not older than the one IEUStudia knows: "update it" can't help (it is IEUStudia's own, the newest)
    monkeypatch.setattr(ai, "_codex_versions", {sys.executable: (ai._codex_stamp(sys.executable), "0.170.0", True)})
    with pytest.raises(RuntimeError) as err:
        ai._codex_once("S", [{"type": "text", "text": "x"}], "sonnet", "", None, None, 60)
    assert str(err.value).startswith("This Codex CLI (0.170.0) is newer than this IEUStudia knows")
    assert ai._codex_versions[sys.executable][2] is True                 # kept: nothing else would work better
    assert ai._codex_last_words("2026-11-06 ERROR codex_core: stream disconnected\nWARNING: x") == \
        "2026-11-06 ERROR codex_core: stream disconnected"
    # a crash of the program itself: said plainly, not with its backtrace note
    crash = ai._codex_last_words("thread 'main' panicked at src/main.rs:3\nnote: run with RUST_BACKTRACE=1")
    assert crash == "thread 'main' panicked at src/main.rs:3"
    assert ai._codex_error(crash).startswith("The Codex CLI stopped unexpectedly. Try again;")
    assert ai._codex_error("stream disconnected before completion: error sending request").startswith(
        "ChatGPT could not be reached through the Codex CLI")
    assert ai._codex_error("Your access token could not be refreshed because your refresh token was already used.") == \
        "The Codex CLI is not signed in. Sign in under Settings → AI."
    ai.codex_cli_info.cache_clear()


def test_an_older_claude_code_is_passed_over_and_a_broken_one_said(tmp_path, monkeypatch):
    real = ai.claude_cli_info
    old, new = _exe(tmp_path / "npm" / "claude.exe"), _exe(tmp_path / "local" / "claude.exe")
    monkeypatch.setattr(ai, "_candidates", lambda: [old, new])
    probe = _answers({(old, "--version"): "1.0.30 (Claude Code)", (old, "--help"): "Usage: claude [options]\n  --print\n",
                      (new, "--version"): "2.1.318 (Claude Code)"})
    monkeypatch.setattr(ai, "_probe", probe)
    ai.claude_cli_info.cache_clear()
    assert ai.claude_cli_info() == {"available": True, "path": new, "version": "2.1.318"}
    kept = json.loads((ai_setup.tools_dir().parent / "claude-version.json").read_text(encoding="utf-8"))
    assert kept[old]["ok"] is False and kept[new]["ok"] is True
    ai._claude_too_old(new)                                   # it then turned down a flag after all
    info = ai.claude_cli_info()
    assert not info["available"] and info["too_old"] == {"path": old, "version": "1.0.30"}
    with pytest.raises(RuntimeError, match=r"^The Claude Code on this PC \(1\.0\.30\) is too old for IEUStudia"):
        ai._require_claude()
    monkeypatch.setattr(ai, "claude_cli_info", _cached({"available": True, "path": new, "version": "2.0.9"}))
    said = ai.friendly_error("error: unknown option '--permission-prompts'")
    assert said == "The Claude Code on this PC (2.0.9) is too old for IEUStudia. Settings → AI: Update Claude Code."
    assert ai.friendly_error(said) == said
    # one not older than the version IEUStudia was checked with: updating can't help, and it is not passed over
    monkeypatch.setattr(ai, "claude_cli_info", _cached({"available": True, "path": new, "version": "2.2.4"}))
    passed_over = []
    monkeypatch.setattr(ai, "_claude_too_old", passed_over.append)
    newer = ai._claude_failure("error: unknown option '--permission-prompts'")
    assert str(newer).startswith("This Claude Code (2.2.4) is newer than this IEUStudia knows") and not passed_over
    assert ai.friendly_error(str(newer)) == str(newer)
    monkeypatch.setattr(ai, "_candidates", lambda: [_exe(tmp_path / "x" / "claude.exe")])
    monkeypatch.setattr(ai, "claude_cli_info", real)
    real.cache_clear()
    info = ai.claude_cli_info()
    assert not info["available"] and info["problem"]["error"] == "the file is damaged or not made for this PC"
    real.cache_clear()


def test_a_copy_that_does_not_start_is_not_asked_again_until_it_changes(tmp_path, monkeypatch):
    """One that hangs took 20 s each time, and the set-up asks every 4 s."""
    broken = _exe(tmp_path / "codex.exe")
    monkeypatch.setattr(ai, "_codex_versions", {})
    monkeypatch.setattr(ai, "_codex_candidates", lambda: [broken])
    probe = _answers({})
    monkeypatch.setattr(ai, "_probe", probe)
    for _ in range(3):
        ai.codex_cli_info.cache_clear()
        assert ai.codex_cli_info()["problem"]["path"] == broken
    assert len(probe.seen) == 1
    _exe(tmp_path / "codex.exe", b"MZ installed again")                  # the file changed: asked again
    ai.codex_cli_info.cache_clear()
    ai.codex_cli_info()
    assert len(probe.seen) == 2
    ai.codex_cli_info.cache_clear()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' own error for a file that is no program")
def test_a_damaged_program_file_is_described_not_quoted(tmp_path):
    code, out, why = ai._probe([_exe(tmp_path / "claude.exe", b"MZ cut off"), "--version"])
    assert code == -1 and why == "the file is damaged or not made for this PC"
