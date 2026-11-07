"""Connecting ChatGPT or Claude, step by step: what is missing on this PC, installing it (without a console window),
the sign-in, the plan and a short test message.

ChatGPT is reached with "Continue with ChatGPT" (chatgpt.py), which needs nothing installed, or, as its own choice
for people who already use it, through OpenAI's Codex CLI, which this module can install: OpenAI's npm package for
Windows (@openai/codex, the win32 build), fetched from the npm registry and checked against its published SHA-512 -
the same files npm would install, so Node.js is not needed. Claude is reached only through Anthropic's own Claude
Code (not the Claude desktop app), installed the way Anthropic's installer script (https://claude.ai/install.ps1)
does it: the latest build from downloads.claude.ai, checked against the SHA-256 in its manifest, then
``claude install latest`` (into ~\\.local\\bin). IEUStudia downloads the build itself, so it resumes after a dropped
connection, shows its size and speed and can be cancelled, and neither PowerShell nor its execution policy is
needed. Signing in happens in Claude Code's own flow (:func:`studia.ai.start_login`), as for the Codex CLI.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from .cancel import Cancelled
from .i18n import num, t

log = logging.getLogger("studia")

Emit = Callable[[dict[str, Any]], None]
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
NPM_REGISTRY = "https://registry.npmjs.org"
CLAUDE_DOWNLOADS = "https://downloads.claude.ai/claude-code-releases"     # as Anthropic's install.ps1 and install.sh
SETUP_LIMIT_S = 45 * 60      # `claude install` after the download: it may fetch files of its own, so the limit is
                             # generous; the student sees the time go by and can cancel it any moment
MB = 1048576
# The plans that include the tool; anything else is said plainly (Claude Free has no Claude Code).
CLAUDE_PLANS = {"pro": "Claude Pro", "max": "Claude Max", "team": "Claude Team", "enterprise": "Claude Enterprise"}
CODEX_PLANS = {"plus": "ChatGPT Plus", "pro": "ChatGPT Pro", "team": "ChatGPT Business", "business": "ChatGPT Business",
               "edu": "ChatGPT Edu", "enterprise": "ChatGPT Enterprise"}
TEST_SYSTEM = "This is a connection test. Reply with the single word OK and nothing else."

_lock = threading.Lock()
_jobs: dict[str, dict[str, Any]] = {}       # "claude" | "codex" -> {"state", "line", "error", "started"}
_cancels: dict[str, threading.Event] = {}   # the running install's Cancel
_tests: dict[str, dict[str, Any]] = {}      # "chatgpt" | "claude" | "codex" -> the last test's result


# ----------------------------------------------------------------------------- where things are
def tools_dir() -> Path:
    from .paths import app_dir

    return app_dir() / "tools"


def codex_home() -> Path:
    """Where IEUStudia keeps the Codex CLI it installs: ``<app data>\\tools\\codex`` (bin\\codex.exe inside)."""
    return tools_dir() / "codex"


def codex_exe() -> str:
    exe = codex_home() / "bin" / "codex.exe"
    return str(exe) if exe.is_file() else ""


def git_bash() -> str:
    """Git for Windows' bash.exe if it is installed; "" if not. Claude Code prefers it for shell commands and uses
    PowerShell without it - and IEUStudia runs Claude Code without any tools, so it is never required."""
    env = os.environ.get("CLAUDE_CODE_GIT_BASH_PATH", "")
    if env and os.path.isfile(env):
        return env
    roots = [os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramW6432", ""),
             os.environ.get("ProgramFiles(x86)", ""), os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")]
    for root in roots:
        cand = os.path.join(root, "Git", "bin", "bash.exe") if root else ""
        if cand and os.path.isfile(cand):
            return cand
    git = shutil.which("git.exe") or shutil.which("git")
    if git:                                        # ...\Git\cmd\git.exe -> ...\Git\bin\bash.exe
        cand = Path(git).resolve().parent.parent / "bin" / "bash.exe"
        if cand.is_file():
            return str(cand)
    return ""


# What would make Claude Code bill or route requests another way than the student's own Claude subscription: an
# Anthropic API key (or token) set on the PC for coursework, a gateway, a cloud provider. Claude Code prefers an API
# key to the subscription sign-in, so with one set every note and chat was billed per use, or failed with "not signed
# in" while the set-up said "Signed in". CLAUDE_CODE_OAUTH_TOKEN stays: it is a subscription sign-in (claude setup-token).
NOT_PASSED_ON = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_BEDROCK",
                 "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")


def left_out() -> list[str]:
    """The variables of :data:`NOT_PASSED_ON` set on this PC (Settings → AI says they are not used)."""
    return [k for k in NOT_PASSED_ON if os.environ.get(k)]


def claude_env() -> dict[str, str]:
    """The environment for running Claude Code: without an API key or another way of billing set on this PC (see
    :data:`NOT_PASSED_ON`), and telling it where Git's Bash is when Git was installed after IEUStudia started (so the
    PATH this process got does not have it yet). Optional: Claude Code works without Git."""
    env = {k: v for k, v in os.environ.items() if k.upper() not in NOT_PASSED_ON}
    if sys.platform != "win32" and not env.get("CLAUDE_CONFIG_DIR"):
        # Linux: a Claude Code profile of IEUStudia's own (signed in once in the setup), so a developer's personal
        # CLAUDE.md, memory, hooks and plugins never come along with the lecture text
        env["CLAUDE_CONFIG_DIR"] = str(tools_dir() / "claude-config")
    bash = git_bash()
    if bash and not env.get("CLAUDE_CODE_GIT_BASH_PATH"):
        env["CLAUDE_CODE_GIT_BASH_PATH"] = bash
    return env


# ----------------------------------------------------------------------------- jobs (installs) and their progress
def job(which: str) -> dict[str, Any]:
    with _lock:
        return dict(_jobs.get(which) or {})


def last_test(which: str) -> dict[str, Any]:
    with _lock:
        return dict(_tests.get(which) or {})


def _update(which: str, emit: Emit, **change: Any) -> None:
    with _lock:
        j = _jobs.setdefault(which, {})
        j.update(change)
        evt = {"type": "ai_setup", "which": which, **j}
    emit(evt)


_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")


def _clean(line: str) -> str:
    return " ".join(_ANSI.sub("", line).replace("\r", " ").split())


class Plain(RuntimeError):
    """A failure already said in words for the student, with what to do: shown as it is."""


class TookTooLong(RuntimeError):
    """A program the set-up ran was stopped at its time limit."""


def _kill_tree(proc: subprocess.Popen) -> None:
    """End a program and every program it started (an installer's child holds the output open, and would keep the
    set-up waiting after the installer itself was ended)."""
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        from .osutil import kill_tree

        kill_tree(proc.pid)
    else:
        try:
            os.killpg(proc.pid, 9)                   # its own process group (start_new_session)
        except OSError:
            pass
    try:
        proc.kill()
    except OSError:
        pass


def _elapsed(seconds: float) -> str:
    """1:05, 12:30: how long something has been going (minutes and seconds, the same in every language)."""
    s = int(seconds)
    return f"{s // 60}:{s % 60:02d}"


def _run_logged(cmd: list[str], which: str, emit: Emit, timeout_s: float = 900,
                cancel: Optional[threading.Event] = None, label: str = "",
                env: Optional[dict[str, str]] = None) -> list[str]:
    """Run an installer hidden. ``label``: the line shown meanwhile, with the time going by, so a step that prints
    nothing for minutes still visibly works (without one, each output line is shown). Its whole output goes to the
    log. Cancel (``cancel``) or the time limit end it with every program it started; then Cancelled or TookTooLong
    is raised, and RuntimeError with its last lines when it fails."""
    lines: list[str] = []
    log.info("Running %s", " ".join(Path(cmd[0]).name if i == 0 else a for i, a in enumerate(cmd[:4])))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                            creationflags=_NO_WINDOW, start_new_session=sys.platform != "win32", env=env)
    assert proc.stdout
    started, last_emit = time.monotonic(), 0.0
    ended: list[str] = []                            # "cancel" | "time": why it was ended

    def killer() -> None:
        shown = 0.0
        while proc.poll() is None:
            if cancel is not None and cancel.is_set():
                ended.append("cancel")
            elif time.monotonic() - started > timeout_s:
                ended.append("time")
            if ended:
                _kill_tree(proc)
                return
            if label and time.monotonic() - shown >= 1:
                shown = time.monotonic()
                _update(which, emit, line=f"{label} {_elapsed(shown - started)}")
            time.sleep(0.25)

    threading.Thread(target=killer, name=f"install-{which}-limit", daemon=True).start()
    for raw in proc.stdout:
        line = _clean(raw.decode("utf-8", "replace"))
        if not line:
            continue
        lines.append(line)
        if not label and time.monotonic() - last_emit > 0.3:
            last_emit = time.monotonic()
            _update(which, emit, line=line[:160])
    code = proc.wait()
    log.info("%s exited with %s after %.0f s:\n%s", Path(cmd[0]).name, code, time.monotonic() - started,
             "\n".join(lines[-60:]) or "(no output)")
    if "cancel" in ended:
        raise Cancelled()
    if "time" in ended:
        raise TookTooLong(" ".join(lines[-3:]))
    if code != 0:
        tail = " ".join(lines[-3:]) or f"exit code {code}"
        raise RuntimeError(tail[:400])
    return lines


def start_install(which: str, emit: Emit) -> dict[str, Any]:
    """Install "claude" (Claude Code) or "codex" (the Codex CLI) in the background. Progress and the outcome go to
    ``emit`` as {"type": "ai_setup", "which", "state", "line", "error"} events; ``state`` is "running", "done",
    "error" or "cancelled" (:func:`cancel_install`)."""
    if which not in ("claude", "codex"):
        raise ValueError(which)
    with _lock:
        if (_jobs.get(which) or {}).get("state") == "running":
            return dict(_jobs[which])
        cancel = _cancels[which] = threading.Event()
        _jobs[which] = {"state": "running", "line": t("Starting…"), "error": "", "started": time.time()}
    log.info("Installing %s", which)

    def run() -> None:
        from . import ai

        try:
            if which == "claude":
                _install_claude(emit, cancel)
            else:
                _install_codex(emit, cancel)
            ai.claude_cli_info.cache_clear()
            ai.codex_cli_info.cache_clear()
            ai._failed.clear()                     # a copy that didn't start before is asked again: it may be new now
            if not _installed(which):
                ai._failed.clear()                 # (the next look asks again, not only in 5 minutes)
                raise Plain(t("Claude Code was installed, but it doesn't start yet: your antivirus may still be "
                              "checking it (or put it in quarantine). Wait a minute: this list looks again by itself. "
                              "If it stays like this, restore Claude Code in your antivirus, or restart the PC.")
                            if which == "claude" else
                            t("The Codex CLI was installed, but it doesn't start yet: your antivirus may still be "
                              "checking it (or put it in quarantine). Wait a minute: this list looks again by itself. "
                              "If it stays like this, restore the Codex CLI in your antivirus, or restart the PC."))
            log.info("Installed %s", which)
            _update(which, emit, state="done", line=t("Installed"), error="", stopping=False)
        except Exception as exc:  # noqa: BLE001 - shown in the set-up
            if isinstance(exc, Cancelled) or cancel.is_set():
                log.info("Installing %s: cancelled", which)
                _update(which, emit, state="cancelled", line="", error="", stopping=False)
                return
            log.warning("Installing %s failed: %r", which, exc)
            _update(which, emit, state="error", error=_install_error(which, exc), stopping=False)

    threading.Thread(target=run, name=f"install-{which}", daemon=True).start()
    return job(which)


def cancel_install(which: str, emit: Optional[Emit] = None) -> bool:
    """Stop the install under way (its download is kept: the next try continues where it stopped). Its line says
    "Stopping…" at once, until it has stopped (a second or two)."""
    with _lock:
        cancel = _cancels.get(which)
        running = (_jobs.get(which) or {}).get("state") == "running"
    if cancel is None or not running:
        return False
    cancel.set()
    _update(which, emit or (lambda e: None), line=t("Stopping…"), stopping=True)
    return True


def _installed(which: str) -> bool:
    from . import ai

    if which == "claude":
        return ai.claude_cli_info()["available"]
    return ai.codex_cli_info()["available"]


def _install_error(which: str, exc: BaseException) -> str:
    """Why an install failed, in words for anyone, with what to do."""
    import errno

    import requests

    from .net import plain_error

    claude = which == "claude"
    message = str(exc) or exc.__class__.__name__
    low = message.lower()
    winerror = getattr(exc, "winerror", None)
    if isinstance(exc, Plain):
        return message[:500]
    if isinstance(exc, TookTooLong):
        return (t("Setting up Claude Code took too long and was stopped. Click Try again; if it stops again, use Rather "
                  "do it yourself? below.") if claude else
                t("Installing the Codex CLI took too long and was stopped. Click Try again."))
    if winerror == 225 or "virus" in low:              # ERROR_VIRUS_INFECTED: Windows Security or another antivirus
        return (t("Windows Security (or your antivirus) blocked Claude Code. Claude Code comes from Anthropic: if your "
                  "antivirus put it in quarantine, restore it there, then try again.") if claude else
                t("Windows Security (or your antivirus) blocked the Codex CLI. It comes from OpenAI: if your antivirus "
                  "put it in quarantine, restore it there, then try again."))
    if (isinstance(exc, OSError) and exc.errno == errno.ENOSPC) or winerror in (39, 112):
        return (t("Your disk is full, so Claude Code could not be installed. Free up about 1 GB, then try again.")
                if claude else
                t("Your disk is full, so the Codex CLI could not be installed. Free up about 1 GB, then try again."))
    if isinstance(exc, requests.RequestException) or any(k in low for k in (
            "could not resolve", "name or service", "getaddrinfo", "unable to connect", "timed out", "no such host",
            "connection", "did not complete")):
        if isinstance(exc, requests.RequestException):       # the connection, a blocked secure one, a server error
            why = plain_error(exc)
            first = (t("Could not download Claude Code: {error}", error=why) if claude else
                     t("Could not download the Codex CLI: {error}", error=why))
        else:
            first = (t("Could not download Claude Code: check your internet connection and try again.") if claude else
                     t("Could not download the Codex CLI: check your internet connection and try again."))
        return f"{first} {t('(Downloading again continues where it stopped.)')}"
    if isinstance(exc, (PermissionError, FileNotFoundError)):
        return (t("Installing Claude Code did not work: {error}", error=plain_error(exc)) if claude else
                t("Installing the Codex CLI did not work: {error}", error=plain_error(exc)))[:500]
    return (t("Installing Claude Code did not work: {error}", error=message) if claude else
            t("Installing the Codex CLI did not work: {error}", error=message))[:500]


# ----------------------------------------------------------------------------- downloads with size, speed and time left
class _Speed:
    """The download speed over the last few seconds, and the time left at that speed."""

    def __init__(self) -> None:
        self._seen: list[tuple[float, int]] = []

    def add(self, done: int, now: Optional[float] = None) -> None:
        now = time.monotonic() if now is None else now
        self._seen.append((now, done))
        while len(self._seen) > 2 and now - self._seen[0][0] > 8:
            self._seen.pop(0)

    def per_s(self) -> float:
        if len(self._seen) < 2 or self._seen[-1][0] - self._seen[0][0] < 1.5:
            return 0.0
        (t0, b0), (t1, b1) = self._seen[0], self._seen[-1]
        return max(0.0, (b1 - b0) / (t1 - t0))

    def stalled(self) -> bool:
        """Nothing came for the last 6 seconds or more (the download waits for the connection, then tries again)."""
        return (len(self._seen) >= 2 and self._seen[-1][0] - self._seen[0][0] >= 6
                and self._seen[-1][1] == self._seen[0][1])


def _progress_line(name: str, version: str, done: int, total: int, speed: _Speed) -> str:
    """"Downloading Claude Code 2.1.318… 120 of 327 MB · 2.4 MB/s · about 2 min left"."""
    head = (t("Downloading {name} {version}… {got} of {total} MB", name=name, version=version,
              got=num(done / MB, group=False), total=num(total / MB, group=False)) if total else
            t("Downloading {name} {version}… {got} MB", name=name, version=version, got=num(done / MB, group=False)))
    if speed.stalled():
        return f"{head} · {t('nothing is arriving: waiting for the connection, then it goes on by itself')}"
    rate = speed.per_s()
    if not rate:
        return head
    parts = [head, t("{speed} MB/s", speed=num(rate / MB, 1)) if rate >= MB / 2 else
             t("{speed} KB/s", speed=num(rate / 1024, group=False))]
    if total > done:
        left = (total - done) / rate
        parts.append(t("less than a minute left") if left < 60 else t("about {m} min left", m=int(-(-left // 60))))
    if rate < 50 * 1024:
        parts.append(t("the connection is very slow"))
    return " · ".join(parts)


_draining: dict[str, threading.Thread] = {}     # a download still ending after its Cancel (see _download)


def _until_cancel(fn: Callable[[], Any], cancel: threading.Event, name: str) -> Any:
    """``fn()`` in a thread of its own, given up at once when Cancel is pressed. A request waiting for a connection
    that stopped answering only ends at its timeout (up to a minute), and on Windows nothing can cut that wait short:
    so Cancel doesn't wait for it. What the request still brings is dropped."""
    out: dict[str, Any] = {}

    def work() -> None:
        try:
            out["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - handed to the caller
            out["error"] = exc

    th = threading.Thread(target=work, name=name, daemon=True)
    th.start()
    while th.is_alive():
        th.join(0.2)
        if cancel.is_set() and th.is_alive():
            th.join(1.5)                         # most stop within a chunk
            if th.is_alive():
                stopped = Cancelled()
                stopped.still_running = th       # type: ignore[attr-defined] - (see _download)
                raise stopped
    if "error" in out:
        raise out["error"]
    return out.get("value")


def _download(url: str, dest: Path, session: Any, name: str, version: str, which: str, emit: Emit,
              cancel: threading.Event, size: int = 0) -> Path:
    """Download into ``dest`` with httpdl (resumable, read timeouts, retries; no limit on the whole time) and show
    its progress on the set-up's line. Cancel ends it at once: a read waiting for a stalled connection is left to
    end by itself, and the next try waits for that before it touches the same files (said on the line)."""
    from . import httpdl

    old = _draining.pop(which, None)
    while old is not None and old.is_alive():
        _update(which, emit, line=t("Waiting for the stopped download to let go of its files…"))
        old.join(0.5)
        if cancel.is_set():
            _draining[which] = old
            raise Cancelled()
    speed = _Speed()
    last = [0.0]

    def progress(done: int, total: int) -> None:
        if cancel.is_set():                      # stopped: its line is not drawn again
            return
        speed.add(done)
        now = time.monotonic()
        if now - last[0] >= 0.5 or (total and done >= total):
            last[0] = now
            _update(which, emit, line=_progress_line(name, version, done, total or size, speed))

    _update(which, emit, line=t("Downloading {name} {version}…", name=name, version=version))
    try:
        path = _until_cancel(lambda: httpdl.download(url, dest, session=session, progress=progress, cancel=cancel,
                                                     parts=4), cancel, f"install-{which}-download")
    except Cancelled as exc:
        still = getattr(exc, "still_running", None)     # the download, left to end by itself: the next one waits
        if still is not None:
            _draining[which] = still
        raise
    log.info("Downloaded %s %s (%s bytes)", name, version, path.stat().st_size)
    return path


def _need_space(folder: Path, need: int, which: str) -> None:
    """Stop before a download that can't fit (the drive is checked, not left to fail halfway)."""
    import shutil as _shutil

    folder.mkdir(parents=True, exist_ok=True)
    try:
        free = _shutil.disk_usage(folder).free
    except OSError:
        return
    if free < need:
        drive = Path(os.path.abspath(folder)).anchor or "/"
        args = {"need": num(need / 1024 ** 3, 1), "free": num(free / 1024 ** 3, 1), "drive": drive}
        raise Plain(t("There isn't enough free space on {drive} for Claude Code: it needs about {need} GB and "
                      "{free} GB is free. Free up some space, then try again.", **args) if which == "claude" else
                    t("There isn't enough free space on {drive} for the Codex CLI: it needs about {need} GB and "
                      "{free} GB is free. Free up some space, then try again.", **args))


def _sha256(path: Path, cancel: Optional[threading.Event] = None) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(4 * MB):
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            h.update(chunk)
    return h.hexdigest()


# ----------------------------------------------------------------------------- Claude Code from downloads.claude.ai
def claude_platform() -> str:
    """The name Anthropic's downloads use for this PC: win32-x64, win32-arm64, linux-x64(-musl), linux-arm64(-musl)."""
    arm = platform.machine().lower() in ("arm64", "aarch64")
    if sys.platform == "win32":
        return "win32-arm64" if arm else "win32-x64"
    arch = "arm64" if arm else "x64"
    if sys.platform == "darwin":
        return f"darwin-{arch}"
    musl = any(Path("/lib").glob("libc.musl-*.so.1")) or any(Path("/lib").glob("ld-musl-*.so.1"))
    return f"linux-{arch}-musl" if musl else f"linux-{arch}"


def claude_release(session: Any) -> dict[str, Any]:
    """The latest Claude Code for this PC: {"version", "platform", "url", "sha256", "size"}."""
    r = session.get(f"{CLAUDE_DOWNLOADS}/latest", timeout=30)
    r.raise_for_status()
    version = (r.content or b"").decode("utf-8", "replace").strip()     # (r.text guesses: "9.9.9" read as UTF-16)
    if not re.match(r"^\d+\.\d+\.\d+\S*$", version):
        log.warning("downloads.claude.ai answered %r instead of a version", version[:200])
        raise Plain(t("downloads.claude.ai did not answer with a Claude Code version: it may be blocked on this "
                      "network. Try another connection, or install Claude Code yourself (Rather do it yourself? "
                      "below)."))
    r = session.get(f"{CLAUDE_DOWNLOADS}/{version}/manifest.json", timeout=30)
    r.raise_for_status()
    plat = claude_platform()
    try:
        manifest = json.loads((r.content or b"").decode("utf-8", "replace"))
    except ValueError:
        manifest = {}
    entry = ((manifest if isinstance(manifest, dict) else {}).get("platforms") or {}).get(plat) or {}
    checksum = str(entry.get("checksum") or "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", checksum):
        raise Plain(t("Claude Code has no download for this kind of PC ({platform}).", platform=plat))
    name = "claude.exe" if plat.startswith("win32") else "claude"
    return {"version": version, "platform": plat, "url": f"{CLAUDE_DOWNLOADS}/{version}/{plat}/{name}",
            "sha256": checksum, "size": int(entry.get("size") or 0)}


def _claude_setup_command(exe: str) -> list[str]:
    """What install.ps1 runs after its download: Claude Code sets itself up in ~/.local (launcher and updates)."""
    return [exe, "install", "latest"]


def claude_versions_dir() -> Path:
    """Where Claude Code keeps its builds, one file per version named by it: $XDG_DATA_HOME/claude/versions, which is
    ~/.local/share/claude/versions unless that variable is set (on Windows too)."""
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "claude" / "versions"


def _hand_over(exe: Path, version: str) -> bool:
    """Put the checked build where ``claude install`` looks first. It downloads the whole build again otherwise
    (about 125 MB, with nothing IEUStudia can show of it, and from the start on every try): with the build already in
    its versions folder, it only checks it against Anthropic's manifest and sets it up. True when it is there. A
    build Claude Code already has there is left alone (it checks that one itself)."""
    if not re.fullmatch(r"[A-Za-z0-9._+-]+", version) or ".." in version:
        return False
    target = claude_versions_dir() / version
    tmp = target.with_name(f"{version}.ieustudia-{os.getpid()}")
    try:
        if target.is_file() and target.stat().st_size > 0:
            return True
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp.unlink(missing_ok=True)
        try:
            os.link(exe, tmp)                        # the same drive: no copy, no room needed
        except OSError:
            shutil.copyfile(exe, tmp)
        os.replace(tmp, target)
        log.info("Claude Code %s handed over in %s", version, target.parent)
        return True
    except OSError as exc:
        log.info("Claude Code's build could not be put in %s (%s): claude install downloads it itself", target, exc)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def _install_claude(emit: Emit, cancel: threading.Event) -> None:
    session = _session()
    _update("claude", emit, line=t("Looking up the latest Claude Code…"))
    rel = _until_cancel(lambda: claude_release(session), cancel, "install-claude-lookup")
    folder = tools_dir() / "claude-setup"
    exe = folder / f"claude-{rel['version']}-{rel['platform']}{'.exe' if rel['platform'].startswith('win32') else ''}"
    have = sum(p.stat().st_size for p in folder.glob(exe.name + "*") if p.is_file()) if folder.is_dir() else 0
    _need_space(folder, max(0, 2 * (rel["size"] or 400 * MB) - have) + 100 * MB, "claude")   # the download + its copy
    for old in folder.glob("claude-*"):                  # an earlier version's leftovers
        if not old.name.startswith(exe.name):
            old.unlink(missing_ok=True)
    _download(rel["url"], exe, session, "Claude Code", rel["version"], "claude", emit, cancel, rel["size"])
    _update("claude", emit, line=t("Checking the download…"))
    if not exe.is_file():
        raise Plain(t("The downloaded Claude Code disappeared right after the download: your antivirus may have "
                      "removed it. Check its quarantine (Claude Code comes from Anthropic), then try again."))
    if _sha256(exe, cancel) != rel["sha256"]:
        exe.unlink(missing_ok=True)
        raise Plain(t("The download was damaged (checksum mismatch). Try again."))
    if sys.platform != "win32":
        os.chmod(exe, 0o755)
    # the build where Claude Code's own set-up finds it, so that step doesn't download it all over again; when it
    # can't be put there, the line says that this step downloads (the time going by shows it still works)
    handed = _hand_over(exe, rel["version"])
    label = (t("Setting up Claude Code…") if handed else
             t("Setting up Claude Code: it downloads itself once more, which can take several minutes on a slow "
               "connection…"))
    # kept until it is set up: after a Cancel or a failure here, the next try starts from this step, not the download
    _run_logged(_claude_setup_command(str(exe)), "claude", emit, timeout_s=SETUP_LIMIT_S, cancel=cancel,
                label=label, env=claude_env())   # (Linux: IEUStudia's own Claude profile)
    for _ in range(3):                                   # Windows may hold the file for a moment after it ran
        try:
            exe.unlink(missing_ok=True)
            break
        except OSError:
            time.sleep(1)


# ----------------------------------------------------------------------------- the Codex CLI from the npm registry
def _session() -> Any:
    from .net import plain_session

    s = plain_session()
    s.headers["User-Agent"] = "IEUStudia (+https://github.com/mirzaisi/IEUStudia)"
    return s


def codex_package(session: Any = None) -> dict[str, Any]:
    """The latest Windows build of @openai/codex on the npm registry: {"version", "tarball", "integrity", "target"}."""
    session = session or _session()
    arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x64"
    r = session.get(f"{NPM_REGISTRY}/@openai/codex/latest", timeout=30)
    r.raise_for_status()
    version = r.json()["version"]
    r = session.get(f"{NPM_REGISTRY}/@openai/codex/{version}-win32-{arch}", timeout=30)
    r.raise_for_status()
    dist = r.json()["dist"]
    return {"version": version, "tarball": dist["tarball"], "integrity": dist.get("integrity", ""),
            "shasum": dist.get("shasum", ""),
            "target": "aarch64-pc-windows-msvc" if arch == "arm64" else "x86_64-pc-windows-msvc"}


def _check_integrity(data: bytes, integrity: str, shasum: str) -> None:
    """npm's "sha512-<base64>" (or the older sha1 "shasum") must match, or the download is not used."""
    for item in (integrity or "").split():
        algo, _, digest = item.partition("-")
        if algo in ("sha512", "sha384", "sha256"):
            if base64.b64encode(hashlib.new(algo, data).digest()).decode() != digest:
                raise Plain(t("The download was damaged (checksum mismatch). Try again."))
            return
    if shasum and hashlib.sha1(data).hexdigest() != shasum:  # noqa: S324 - npm's published checksum
        raise Plain(t("The download was damaged (checksum mismatch). Try again."))
    if not integrity and not shasum:
        raise Plain(t("The npm registry did not publish a checksum for this package."))


def unpack_codex(data: bytes, target: str, dest: Path) -> None:
    """The package's ``vendor/<target>/`` folder (codex.exe and its helpers) into ``dest``, replacing it."""
    prefix = f"package/vendor/{target}/"
    tmp = dest.with_name(dest.name + ".new")
    shutil.rmtree(tmp, ignore_errors=True)
    found = False
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for m in tar.getmembers():
            if not m.isfile() or not m.name.startswith(prefix):
                continue
            rel = Path(m.name[len(prefix):])
            if rel.is_absolute() or ".." in rel.parts:
                continue
            out = tmp / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            src = tar.extractfile(m)
            if src is None:
                continue
            with src, open(out, "wb") as f:
                shutil.copyfileobj(src, f)
            found = found or rel.as_posix() == "bin/codex.exe"
    if not found:
        shutil.rmtree(tmp, ignore_errors=True)
        raise Plain(t("The package has no codex.exe for this PC."))
    old = dest.with_name(dest.name + ".old")
    shutil.rmtree(old, ignore_errors=True)
    if dest.exists():
        dest.rename(old)
    tmp.rename(dest)
    shutil.rmtree(old, ignore_errors=True)


def _install_codex(emit: Emit, cancel: Optional[threading.Event] = None) -> None:
    if sys.platform != "win32":
        raise Plain(t("On Linux, install the Codex CLI yourself (npm install -g @openai/codex), then check again."))
    cancel = cancel or threading.Event()
    session = _session()
    _update("codex", emit, line=t("Looking up the latest Codex CLI on npm…"))
    pkg = _until_cancel(lambda: codex_package(session), cancel, "install-codex-lookup")
    folder = tools_dir() / "codex-setup"
    tgz = folder / f"codex-{pkg['version']}-{pkg['target']}.tgz"
    _need_space(folder, 600 * MB, "codex")                # the package (about 100 MB) and its files (about 400 MB)
    for old in folder.glob("codex-*"):
        if not old.name.startswith(tgz.name):
            old.unlink(missing_ok=True)
    _download(pkg["tarball"], tgz, session, "Codex", pkg["version"], "codex", emit, cancel)
    data = tgz.read_bytes()
    try:
        _check_integrity(data, pkg["integrity"], pkg["shasum"])
    except RuntimeError:
        tgz.unlink(missing_ok=True)                        # a damaged file is not continued next time
        raise
    if cancel.is_set():
        raise Cancelled()
    _update("codex", emit, line=t("Unpacking…"))
    home = codex_home()
    home.parent.mkdir(parents=True, exist_ok=True)
    unpack_codex(data, pkg["target"], home)
    (home / ".installed").write_text(json.dumps({"version": pkg["version"], "from": pkg["tarball"]}), encoding="utf-8")
    tgz.unlink(missing_ok=True)


# ----------------------------------------------------------------------------- plan and test
def plan_status(which: str, plan: str, logged_in: bool, source: str = "",
                ignored: Sequence[str] = ()) -> dict[str, Any]:
    """Whether the signed-in account's plan includes the tool: {"ok": True/False/None, "label", "note"}. Claude:
    ``source``, where Claude Code's API key comes from, if it uses one; ``ignored``, the variables of NOT_PASSED_ON set
    on this PC (the note says they are not used)."""
    p = (plan or "").lower()
    if not logged_in:
        return {"ok": None, "label": "", "note": ""}
    if which == "claude":
        if p in CLAUDE_PLANS:
            note = (t("IEUStudia leaves out the {names} set on this PC, so Claude Code uses your subscription.",
                      names=", ".join(ignored)) if ignored else "")
            return {"ok": True, "label": CLAUDE_PLANS[p], "note": note}
        if p == "api" and source and source != "/login managed key":       # a key in Claude Code's own settings
            return {"ok": None, "label": t("API key"), "note": t(
                "Claude Code uses an API key ({source}) from its own settings instead of your Claude subscription, so "
                "every request is billed per use. To use your Pro or Max plan, remove the key from Claude Code's "
                "settings (settings.json in the .claude folder of your user folder), then sign in with Claude.",
                source=source)}
        if p == "api":
            return {"ok": True, "label": "Anthropic Console (API)", "note": t("Signed in with an API account: every "
                    "request is billed per use, not covered by a Pro or Max subscription.")}
        if p == "free":
            return {"ok": False, "label": "Claude Free", "note": t("Claude's free plan does not include Claude Code. "
                    "You need Claude Pro or Max.")}
        return {"ok": None, "label": "", "note": t("Could not tell your plan; the test message below will show whether "
                "it works.")}
    if p in CODEX_PLANS:
        return {"ok": True, "label": CODEX_PLANS[p], "note": ""}
    if p == "api":
        return {"ok": True, "label": t("OpenAI API key"), "note": t("Signed in with an API key: every request is billed "
                "per use, not covered by a ChatGPT subscription.")}
    if p in ("free", "go"):              # OpenAI includes Codex in Free and Go too, with smaller limits
        return {"ok": None, "label": f"ChatGPT {p.capitalize()}", "note": t("Codex comes with ChatGPT Free and Go too, "
                "with smaller limits; the test message below shows whether it works.")}
    return {"ok": None, "label": "ChatGPT" if p == "chatgpt" else "", "note": t("Codex does not show your plan; the "
            "test message below shows whether it works.")}


def test(which: str) -> dict[str, Any]:
    """Send a tiny test message through the chosen tool (a few tokens of your plan): {"ok", "seconds", "error",
    "code"}. ``code`` is "plan_not_eligible" when OpenAI doesn't let apps use the ChatGPT plan."""
    from . import ai
    from .chatgpt import NotEligible

    started = time.monotonic()
    content = [{"type": "text", "text": "Reply with OK."}]
    try:
        if which == "claude":
            res = ai._claude_one_shot(TEST_SYSTEM, content, model="haiku", effort="low", timeout_s=180, think=False)
        elif which in ("chatgpt", "codex"):
            ask = ai._chatgpt_one_shot if which == "chatgpt" else ai._codex_one_shot
            res = ask(TEST_SYSTEM, content, "haiku", "low", None, None, 180)
        else:
            raise ValueError(which)
        ok = bool((res.get("text") or "").strip())
        out = {"ok": ok, "error": "" if ok else t("No answer came back."), "code": ""}
    except Exception as exc:  # noqa: BLE001 - shown in the set-up
        msg = str(exc) or exc.__class__.__name__
        error = (ai.friendly_error(msg) if which == "claude" else msg)[:400]
        # "signed_out": the program's own status still says signed in, but the service no longer takes that sign-in
        # (it ended after a password change, a sign-out everywhere, a refused renewal): the page offers to sign in again
        signed_out = which != "chatgpt" and error in (t("Claude Code is not signed in. Settings → AI → Sign in with Claude."),
                                                       t("The Codex CLI is not signed in. Sign in under Settings → AI."))
        out = {"ok": False, "error": error,
               "code": "plan_not_eligible" if isinstance(exc, NotEligible) else "signed_out" if signed_out else ""}
    out.update(seconds=round(time.monotonic() - started, 1), at=time.time())
    with _lock:
        _tests[which] = out
    return dict(out)


def forget_test(which: str) -> None:
    """After signing in again or installing, an old test result says nothing."""
    with _lock:
        _tests.pop(which, None)
