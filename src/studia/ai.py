"""Optional AI features (study notes, chat, study plans, digest), only when the user asks, through one of:

- **ChatGPT** ("chatgpt") — the student's own ChatGPT Plus/Pro subscription, through "Continue with ChatGPT"
  (``chatgpt``: OpenAI's sign-in for apps);
- **Claude** ("claude") — the student's own Claude Pro/Max subscription, through the official Claude Code CLI;
- **ChatGPT through the Codex CLI** ("codex") — the same subscription, through OpenAI's official Codex CLI;
- **Offline** ("local") — a small model on this PC (``local_llm``; basic notes and questions, no subscription);
- **None** ("none") — AI features are hidden.

Signing in happens on the vendors' own pages (for Claude and the Codex CLI in their own tools); IEUStudia never sees
a password. AI answers can contain mistakes: the UI says so wherever they appear.

For Claude, to keep subscription usage low we:
- replace Claude Code's large default system prompt with a short one (--system-prompt-file),
- load no tools or MCP servers (--tools "" --strict-mcp-config --restricted); --bare is NOT used because it
  skips the stored subscription login,
- send the compact transcript as text and only downscaled slide images (optional),
- default to Sonnet with low effort (Settings → AI and each request can choose another: effort.py), and cache notes on disk (never run twice for the same input),
- keep one process per chat, so follow-up questions read the lectures from Claude's prompt cache,
- write nothing to the prompt cache for one-off requests (notes, plans, digest): nothing reads it again, and with a
  subscription Claude Code writes to a one-hour cache, priced at twice the rate of plain input.
"""

from __future__ import annotations

import base64
import collections
import datetime as dt
import functools
import glob
import hashlib
import json
import logging
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from . import aierrors, health
from .cancel import Cancelled
from .i18n import N_, Retold, date_short, day_month, language, retell, t
from .notices import ALERTS_MD

# the kinds of lines Claude Code's stream-json and the Codex CLI's --json print: output with none of them (a CLI that
# prints its answers another way since an update) is that CLI changing, said as that (health), not "no answer"
CLAUDE_KINDS = frozenset({"system", "assistant", "user", "result", "stream_event", "rate_limit_event",
                          "control_response", "control_request", "keep_alive"})
CODEX_KINDS = frozenset({"thread.started", "turn.started", "turn.completed", "turn.failed", "item.started",
                         "item.updated", "item.completed", "error"})


def _kinds(kinds: list[str]) -> str:
    return ", ".join(list(dict.fromkeys(kinds))[:12]) or "none"

log = logging.getLogger("studia")

SYSTEM_PROMPT = (
    "You are a meticulous study assistant for a university engineering student. You receive the automatic "
    "transcript of a lecture (mostly English, some Turkish passages tagged [TR]) and sometimes images of the "
    "slides/board shown in the lecture. Produce accurate, well-organised study material in Markdown, with "
    "formulas in LaTeX: $...$ in a sentence, $$...$$ on lines of their own for a displayed formula. Only use what "
    "the transcript and slides support; when you correct an obvious speech-recognition error or are unsure, say "
    "so briefly. Do not ask questions; deliver the complete result.\n\n" + ALERTS_MD
)
IMAGE_TOKENS = 800          # ~1024x576 image
CHARS_PER_TOKEN = 3.1       # Claude, measured on compact transcripts (timestamps and Turkish make tokens shorter)
CHARS_PER_TOKEN_BY = {"chatgpt": 3.9, "codex": 3.9}    # ChatGPT's tokens are longer: 3.87–3.89 billed on a lecture chat
PROMPT_OVERHEAD = 600       # what Claude Code adds to every request
USAGE_KEYS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _npm_folders() -> list[str]:
    """npm's global folders: the usual one, and the folder of a "claude"/"codex" shim found on PATH (another prefix).
    The shims (claude.cmd, codex.cmd) start Node.js, which starts the real program: IEUStudia runs that program itself,
    so Node.js is not needed and Cancel ends the program, not only its shim."""
    out = [os.path.join(os.environ.get("APPDATA", ""), "npm")]
    for name in ("claude", "codex"):
        shim = shutil.which(name) or ""
        if shim and not shim.lower().endswith(".exe"):
            out.append(os.path.dirname(shim))
    return list(dict.fromkeys(x for x in out if x))


def _candidates() -> list[str]:
    env = os.environ
    c = [os.path.join(npm, "node_modules", "@anthropic-ai", "claude-code", "bin", "claude.exe") for npm in _npm_folders()]
    c += [os.path.join(env.get("USERPROFILE", ""), ".local", "bin", "claude.exe"),       # Anthropic's installer
          os.path.join(env.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Links", "claude.exe"),   # winget
          shutil.which("claude.exe") or ""]
    c += sorted(glob.glob(os.path.join(env.get("USERPROFILE", ""), ".vscode", "extensions",
                                       "anthropic.claude-code-*", "resources", "native-binary", "claude.exe")),
                reverse=True)
    if sys.platform != "win32":                  # Linux: the native installer's ~/.local/bin, npm, PATH
        home = os.path.expanduser("~")
        c += [os.path.join(home, ".local", "bin", "claude"), os.path.join(home, ".claude", "local", "claude"),
              shutil.which("claude") or ""]
    return list(dict.fromkeys(p for p in c if p and os.path.isfile(p)))


_failed: dict[str, tuple[str, float, str]] = {}      # path → (size:modified, when, why): a copy that did not start
FAILED_FOR_S = 300                                   # is not asked again for 5 minutes (one that hangs took 20 s each
                                                     # time, and the set-up asks every 4 s), or until the file changes


def _failed_before(path: str, stamp: str) -> str:
    known = _failed.get(path)
    if known and known[0] == stamp and time.monotonic() - known[1] < FAILED_FOR_S:
        return known[2]
    return ""


def _version_of(text: str) -> tuple[int, ...]:
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
    return tuple(int(x) for x in m.groups()) if m else ()


def _probe(cmd: list[str], timeout: float = 20, env: Optional[dict[str, str]] = None) -> tuple[int, str, str]:
    """Run a CLI to ask it something: (exit code, its output, why it could not run)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, creationflags=_NO_WINDOW,
                           encoding="utf-8", errors="replace", env=env)
        return r.returncode, (r.stdout or "") + (r.stderr or ""), ""
    except subprocess.TimeoutExpired:
        return -1, "", t("it did not answer within {s} seconds", s=int(timeout))
    except OSError as exc:
        from .net import plain_error

        if getattr(exc, "winerror", None) == 225:
            return -1, "", t("Windows Security (or your antivirus) blocked it")
        if getattr(exc, "winerror", None) in (193, 216):         # not a valid program: a cut-off file, another CPU's
            return -1, "", t("the file is damaged or not made for this PC")
        return -1, "", plain_error(exc)


# Claude Code: the version IEUStudia's flags were checked with (newer versions keep them); an older one is asked for
# its --help, and one that lacks a flag (an old npm or VS Code copy that doesn't update itself) is passed over
CLAUDE_KNOWN_GOOD = (2, 1, 287)
CLAUDE_FLAGS = ("--restricted", "--strict-mcp-config", "--no-session-persistence", "--permission-prompts", "--effort",
                "--tools", "--input-format")


@functools.lru_cache(maxsize=1)
def claude_cli_info() -> dict[str, Any]:
    """Where Claude Code is and its version: the first copy that runs and takes the flags IEUStudia passes.
    ``claude --version`` takes ~1 s, so the answer is remembered until the program file changes (Claude Code updates
    itself). Without one that works: "too_old" (a copy that lacks them) or "problem" (a copy that does not start)."""
    from .paths import app_dir

    cache = app_dir() / "claude-version.json"
    try:
        known = json.loads(cache.read_text(encoding="utf-8"))
        known = known if isinstance(known, dict) else {}
    except (OSError, ValueError):
        known = {}
    if "path" in known:                                       # the earlier form: one program
        known = {known["path"]: known}
    old: dict[str, str] = {}
    problem: dict[str, str] = {}
    for path in _candidates():
        try:
            st = os.stat(path)
        except OSError:
            continue
        stamp = f"{st.st_size}:{st.st_mtime_ns}"
        k = known.get(path) or {}
        if k.get("stamp") == stamp and k.get("version") and "ok" in k:
            ver, ok = k["version"], bool(k["ok"])
        elif failed := _failed_before(path, stamp):
            problem = problem or {"path": path, "error": failed}
            continue
        else:
            code, out, why = _probe([path, "--version"], 20, _claude_env())
            ver = (out or "").strip().split(" ")[0] if code == 0 else ""
            if not _version_of(ver):
                log.warning("Claude Code at %s did not start: %s", path, why or f"exit code {code}: {out.strip()[-200:]}")
                why = why or t("it ended with code {code}", code=code)
                _failed[path] = (stamp, time.monotonic(), why)
                problem = problem or {"path": path, "error": why}
                continue
            ok = _version_of(ver) >= CLAUDE_KNOWN_GOOD
            if not ok:
                code, out, _ = _probe([path, "--help"], 30, _claude_env())
                ok = code == 0 and all(f in out for f in CLAUDE_FLAGS)
                if not ok:
                    log.warning("Claude Code %s at %s is too old for IEUStudia; looking for another copy", ver, path)
            known[path] = {"stamp": stamp, "version": ver, "ok": ok}
            try:
                cache.write_text(json.dumps(known), encoding="utf-8")
            except OSError:
                pass
        if ok:
            return {"available": True, "path": path, "version": ver}
        old = old or {"path": path, "version": ver}
    out_: dict[str, Any] = {"available": False, "path": "", "version": ""}
    if old:
        out_["too_old"] = old
    elif problem:
        out_["problem"] = problem
    return out_


def _require_claude() -> str:
    info = claude_cli_info()
    if not info["available"]:
        if info.get("too_old"):
            raise RuntimeError(t(CLAUDE_OLD, version=info["too_old"]["version"]))
        raise RuntimeError(t("Claude Code is not installed on this PC. Settings → AI installs it for you."))
    return info["path"]


def _claude_env() -> dict[str, str]:
    from .ai_setup import claude_env

    return claude_env()


def _command(model: str, effort: str, system_prompt_file: Path, partial: bool = False) -> list[str]:
    cmd = [_require_claude(), "-p", "--restricted", "--strict-mcp-config", "--no-session-persistence",
           "--model", model, "--effort", effort or "low", "--system-prompt-file", str(system_prompt_file), "--tools", "",
           "--permission-prompts", "none", "--input-format", "stream-json", "--output-format", "stream-json",
           "--verbose"]
    if partial:   # stream the answer token by token (chat)
        cmd.append("--include-partial-messages")
    return cmd


def chars_per_token(p: str = "") -> float:
    """Characters per token for the AI ``p`` (default: the chosen one)."""
    return CHARS_PER_TOKEN_BY.get(p or provider(), CHARS_PER_TOKEN)


def text_tokens(text: str, per: float = 0.0) -> int:
    """``per``: characters per token when already known (see :func:`chars_per_token`)."""
    return int(len(text) / (per or chars_per_token()))


def estimate_tokens(text: str, n_images: int) -> dict[str, int]:
    inp = text_tokens(text) + PROMPT_OVERHEAD + n_images * IMAGE_TOKENS
    return {"input": inp, "output": min(8000, 1500 + inp // 6), "images": n_images}


SLIDE_COPIES = 2000           # small slide copies kept (about 60 KB each); beyond that the oldest go


def _prune_copies(folder: Path, keep: int = SLIDE_COPIES) -> None:
    try:
        files = sorted(folder.glob("*.jpg"), key=lambda f: f.stat().st_mtime)
        for f in files[:max(0, len(files) - keep)]:
            f.unlink(missing_ok=True)
    except OSError:
        log.debug("Could not prune %s", folder, exc_info=True)


def _small_jpeg(path: Path, width: int = 1024) -> bytes:
    """A slide re-encoded for the AI at ~1024 px (≈800 tokens instead of ≈1900). The copy is kept in the cache,
    keyed by the slide's path, size and time, so chats and notes don't re-encode the same slides every time."""
    import av

    from .paths import app_dir
    from .slides import save_jpeg

    st = path.stat()
    key = hashlib.sha1(f"{os.path.abspath(path)}|{st.st_size}|{st.st_mtime_ns}|{width}".encode("utf-8")).hexdigest()
    kept = app_dir() / "cache" / "slides" / f"{key[:20]}.jpg"
    try:
        return kept.read_bytes()
    except OSError:
        pass
    with av.open(str(path)) as c:
        frame = next(c.decode(video=0))
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "s.jpg"
        save_jpeg(frame, out, max_width=width)
        data = out.read_bytes()
    try:
        kept.parent.mkdir(parents=True, exist_ok=True)
        part = kept.with_name(f"{kept.stem}.{uuid.uuid4().hex[:8]}.tmp")
        part.write_bytes(data)
        part.replace(kept)
        if sum(1 for _ in os.scandir(kept.parent)) > SLIDE_COPIES + 100:       # now and then, not for every copy
            _prune_copies(kept.parent)
    except OSError:
        log.debug("Could not keep the small copy of %s", path, exc_info=True)
    return data


def image_block(path: Path) -> dict[str, Any]:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                        "data": base64.b64encode(_small_jpeg(Path(path))).decode()}}


def pdf_block(path: Path, title: str = "") -> dict[str, Any]:
    """A whole PDF as a file (Claude reads each page as text and as a picture; chatlimits.Limits.pdf)."""
    return {"type": "document", "title": title or Path(path).name,
            "source": {"type": "base64", "media_type": "application/pdf",
                       "data": base64.b64encode(Path(path).read_bytes()).decode()}}


def turn_blocks(content: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The content as the AI takes it: the app's own marks on blocks (``_turn``: this question's only) left out."""
    return [{k: v for k, v in b.items() if not k.startswith("_")} if any(k.startswith("_") for k in b) else b
            for b in content]


def cache_key(prompt: str, transcript: str, images: Sequence[Path], model: str) -> str:
    h = hashlib.sha256()
    for part in (prompt, transcript, model, *[str(p) for p in images]):
        h.update(part.encode("utf-8", "ignore"))
        h.update(b"\x00")
    return h.hexdigest()[:16]


# ---------------------------------------------------------------------- subscription usage limits
_limits: dict[str, Any] = {}
_limits_lock = threading.Lock()


def note_limits(info: dict[str, Any]) -> None:
    """Remember the subscription usage Claude Code reports (``rate_limit_event``): share of the 5-hour and
    weekly limits used, and when they reset."""
    windows = info.get("unifiedWindows") or {}
    if not windows and info.get("rateLimitType") and "utilization" in info:
        windows = {info["rateLimitType"]: {"utilization": info["utilization"], "resetsAt": info.get("resetsAt")}}
    out: dict[str, Any] = {}
    for key, w in windows.items():
        try:
            out[key] = {"used": float(w["utilization"]), "resets_at": int(w.get("resetsAt") or 0)}
        except (KeyError, TypeError, ValueError):
            continue
    if out:
        out["status"] = str(info.get("status") or "")
        out["at"] = int(time.time())
        with _limits_lock:
            _limits.clear()
            _limits.update(out)


def usage_limits() -> dict[str, Any]:
    with _limits_lock:
        return dict(_limits)


def restore_limits(saved: dict[str, Any]) -> None:
    """Seed the last known usage (from a previous run) until Claude reports a fresh one."""
    with _limits_lock:
        if not _limits and isinstance(saved, dict):
            _limits.update(saved)


def _reset_text(resets_at: int) -> str:
    if not resets_at:
        return ""
    when = dt.datetime.fromtimestamp(resets_at)
    clock = f"{when:%H:%M}"
    if when.date() == dt.date.today():
        return clock
    day = f"{when:%a %d %b}" if language() == "en" else date_short(when.date())     # English as before: "Mon 02 Nov"
    return f"{day}, {clock}"


_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")


def _said_when(said: str) -> str:
    """Claude Code's own reset time ("10:40am (Europe/Istanbul)", "Nov 1, 9pm (Europe/Istanbul)"), as it is in English
    and in the interface's way otherwise ("10:40", "1 Kas 21:00"); left as it is if it reads differently."""
    if language() == "en":
        return said
    m = re.match(r"(?:(?P<mon>[A-Za-z]{3})[a-z]*\.?\s+(?P<day>\d{1,2}),?\s+)?(?:at\s+)?(?P<h>\d{1,2})(?::(?P<min>\d{2}))?\s*"
                 r"(?P<ap>am|pm)\b", said.strip(), flags=re.I)
    if not m or int(m["h"]) > 12 or (m["mon"] and m["mon"].lower() not in _MONTHS):
        return said
    clock = f"{int(m['h']) % 12 + (12 if m['ap'].lower() == 'pm' else 0):02d}:{m['min'] or '00'}"
    if not m["mon"]:
        return clock
    try:
        day = dt.date(dt.date.today().year, _MONTHS.index(m["mon"].lower()) + 1, int(m["day"]))
    except ValueError:
        return said
    return f"{day_month(day)} {clock}"


# friendly_error's own messages: given to it again (the set-up's test does), they are left as they are, in English
# or in the language shown
KEY_REFUSED = N_("Claude Code is set to use an API key that Anthropic refused. Remove the key from Claude Code's settings "
                 "(settings.json in the .claude folder of your user folder), then sign in with Claude under Settings → "
                 "AI.")
NO_CREDIT = N_("The Anthropic API account Claude Code is signed in with has no credit left. To use your Claude Pro or Max "
               "plan instead, sign in again with your Claude account under Settings → AI.")
TOKEN_REFUSED = N_("Claude Code uses the CLAUDE_CODE_OAUTH_TOKEN set on this PC, and Anthropic refused it (it expired or "
                   "was revoked). Make a new one with claude setup-token, or remove the variable and sign in under "
                   "Settings → AI.")
UNREACHABLE = N_("Claude could not be reached: check your internet connection (on campus Wi-Fi, make sure you are signed "
                 "in to it), then try again.")
INSECURE = N_("Claude Code could not make a secure connection to Anthropic: an antivirus that scans HTTPS (or a Wi-Fi "
              "sign-in page) may be in the way. Open any website in your browser, then try again; if it keeps "
              "happening, turn off HTTPS scanning in your antivirus.")
BLOCKED = N_("Windows Security (or your antivirus) blocked Claude Code. Claude Code comes from Anthropic: if your "
             "antivirus put it in quarantine, restore it there, then try again.")
RENEWING = N_("Claude Code was renewing your sign-in in another window. Try again in a minute.")
CLAUDE_OLD = N_("The Claude Code on this PC ({version}) is too old for IEUStudia. Settings → AI: Update Claude Code.")
# a copy not older than the one IEUStudia was checked with turned down a flag: updating it can't help (it is the newest
# already), and it is not passed over; a newer IEUStudia knows what changed
CLAUDE_NEWER = N_("This Claude Code ({version}) is newer than this IEUStudia knows, and it turned down how IEUStudia "
                  "asks it. Look for a new version of IEUStudia in Settings → About.")
START_FAILED = N_("Claude Code could not be started: {error} Install it again under Settings → AI.")
_OWN = (N_("You have reached your Claude usage limit — it resets {when}."),
        N_("You have reached your Claude usage limit — it resets at {time}."),
        N_("You have reached your Claude usage limit."),
        N_("Your Claude plan doesn't include Claude Code. You need Claude Pro or Max."),
        N_("Claude Code is not signed in. Settings → AI → Sign in with Claude."),
        N_("Claude Code is not installed on this PC. Settings → AI installs it for you."),
        KEY_REFUSED, NO_CREDIT, TOKEN_REFUSED, UNREACHABLE, INSECURE, BLOCKED, RENEWING, CLAUDE_OLD, START_FAILED,
        N_("Claude took too long to answer. Try again; if it keeps happening, pick a lower reasoning level."),
        N_("Claude did not return a result."), CLAUDE_NEWER,
        N_("Claude Code stopped unexpectedly. Try again."))
_CLAUDE_NETWORK = ("unable to connect", "connection error", "connection refused", "connection reset", "econnrefused",
                   "econnreset", "enotfound", "eai_again", "getaddrinfo", "fetch failed", "socket hang up",
                   "network is unreachable", "request timed out", "no response from api")
_CERTIFICATE = ("self signed certificate", "self-signed certificate", "unable to get local issuer certificate",
                "unable_to_get_issuer_cert", "certificate verify failed", "unable to verify the first certificate",
                "cert_has_expired")


def _explained(m: str) -> bool:
    if m.startswith(("Claude: ", "You have reached your Claude usage limit", "Claude Code is not signed in.",
                     "Your Claude plan doesn't include", "Claude Code is not installed")):
        return True
    heads = (t(s).split("{", 1)[0].strip() for s in _OWN)      # the text before the first value
    return any(head and m.startswith(head) for head in heads)


def friendly_error(message: str) -> str:
    """Claude Code's error text, made understandable (usage limit, not signed in)."""
    m = (message or "").strip()
    low = m.lower()
    if _explained(m):
        return m                                          # already explained
    if re.search(r"hit your [\w -]{0,20}limit|limit reached|usage limit|rate.?limit", low):
        said = re.search(r"\bresets\s+(.+?)[.\s]*$", m, flags=re.I)   # "You've hit your session limit · resets 10:40am (…)"
        if said:
            return t("You have reached your Claude usage limit — it resets {when}.", when=_said_when(said.group(1)))
        stamp = re.search(r"\|(\d{9,11})\b", m)          # "Claude AI usage limit reached|<epoch>"
        full = [w for w in usage_limits().values() if isinstance(w, dict) and w.get("used", 0) >= 0.99]
        reset = _reset_text(int(stamp.group(1)) if stamp else max((w.get("resets_at", 0) for w in full), default=0))
        if reset:
            return t("You have reached your Claude usage limit — it resets at {time}.", time=reset)
        return t("You have reached your Claude usage limit.")
    if "does not have access to claude code" in low or "free plan" in low:
        return t("Your Claude plan doesn't include Claude Code. You need Claude Pro or Max.")
    if re.search(r"unknown option|unknown argument|unexpected argument", low):     # older than the flags IEUStudia passes
        version = claude_cli_info().get("version") or ""
        if version and _version_of(version) >= CLAUDE_KNOWN_GOOD:
            return t(CLAUDE_NEWER, version=version)
        return t(CLAUDE_OLD, version=version or "?")
    if "contains a virus" in low or "potentially unwanted software" in low:
        return t(BLOCKED)
    if "fix external api key" in low:                     # a key from Claude Code's settings, refused or malformed
        return t(KEY_REFUSED)
    if "credit balance is too low" in low:
        return t(NO_CREDIT)
    if "another claude code process" in low and "refresh" in low:
        return t(RENEWING)
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") and any(k in low for k in ("oauth token", "401", "revoked",
                                                                           "failed to authenticate")):
        return t(TOKEN_REFUSED)
    from .net import cert_date_said, clock_tls_text

    if cert_date_said(m):                                 # a wrong date on this PC, not an antivirus
        return clock_tls_text()
    if any(k in low for k in _CERTIFICATE):
        return t(INSECURE)
    if any(k in low for k in _CLAUDE_NETWORK):
        return t(UNREACHABLE)
    if any(k in low for k in ("/login", "not logged in", "invalid api key", "authentication", "oauth")):
        return t("Claude Code is not signed in. Settings → AI → Sign in with Claude.")
    service = aierrors.from_message("Claude", m)        # "API Error: 500 …", "529 Overloaded": on Anthropic's side
    if service is not None:
        return str(service)
    return f"Claude: {m}"[:400] if m else t("Claude did not return an answer.")


def _claude_failure(message: str) -> RuntimeError:
    """Claude Code's error as the exception raised: a ServiceError when it failed on Anthropic's side (Claude Code
    has already asked again by then), with its own words kept in the log."""
    if re.search(r"unknown option|unknown argument|unexpected argument", (message or "").lower()):
        text = friendly_error(message)                # (with its version, before it is passed over)
        info = claude_cli_info()
        if _version_of(info.get("version") or "") >= CLAUDE_KNOWN_GOOD:
            log.warning("Claude Code %s turned down a flag IEUStudia passes: %s", info.get("version"),
                        (message or "")[:300])
            # not older: kept (an update of it can't help); Claude Code changed how it is asked: a newer IEUStudia
            return health.changed("claude", "answers", f"Claude Code {info.get('version')} turned down a flag "
                                                       f"IEUStudia passes", message=text)
        log.warning("Claude Code is too old for IEUStudia: %s", (message or "")[:300])
        _claude_too_old(info.get("path", ""))
        return RuntimeError(text)
    text = friendly_error(message)
    service = aierrors.from_message("Claude", message)
    if service is not None and text == str(service):
        log.warning("Claude failed on its side: %s", (message or "")[:800])
        return service
    if text == t(UNREACHABLE):                    # the connection: a reading in parts asks again by itself
        return aierrors.Dropped(text)
    return RuntimeError(text)


def _claude_too_old(path: str) -> None:
    """Claude Code turned down a flag IEUStudia passes: that copy is passed over from now on (until it updates)."""
    from .paths import app_dir

    cache = app_dir() / "claude-version.json"
    try:
        known = json.loads(cache.read_text(encoding="utf-8"))
        if isinstance(known, dict) and isinstance(known.get(path), dict):
            known[path]["ok"] = False
            cache.write_text(json.dumps(known), encoding="utf-8")
    except (OSError, ValueError):
        pass
    claude_cli_info.cache_clear()


def usage_of(result: dict[str, Any]) -> dict[str, int]:
    usage = result.get("usage") or {}
    return {k: int(usage.get(k) or 0) for k in USAGE_KEYS}


# ---------------------------------------------------------------------- one-shot (study notes)
TRANSCRIPT_MARK, TASK_MARK = "=== LECTURE TRANSCRIPT ===", "=== TASK ==="


def run_claude(prompt: str, transcript: str, images: Sequence[Path] = (), model: str = "sonnet",
               effort: str = "", cancel: Optional[threading.Event] = None,
               on_text: Optional[Callable[[str], None]] = None, timeout_s: int = 900) -> dict[str, Any]:
    """Study material from a lecture: the lecture (and its slides) first, the task last. Every kind of notes for the same
    lecture then begins with the same text, which ChatGPT reads from its prompt cache for the next kind."""
    content: list[dict[str, Any]] = [{"type": "text", "text": f"{TRANSCRIPT_MARK}\n\n{transcript}"}]
    content += [image_block(Path(img)) for img in images]
    content.append({"type": "text", "text": f"{TASK_MARK}\n\n{prompt}"})
    return one_shot(SYSTEM_PROMPT, content, model, effort, cancel, on_text, timeout_s)


# LaTeX written into a JSON string without doubling its backslashes: "\mathrm" is no JSON at all and "\frac" reads as a
# form feed and "rac". Such a backslash is doubled; a real escape (a quote, "\n" before a word, a \u code) is kept.
# "\t" is LaTeX before two letters or a lone "o" (\to); "\n" before the rest of a command's name (\neq, \nolimits), and
# \ne, \ni only before a space, brace, backslash, digit or "$" (a new line then "i) " or "e.g." stays a new line).
_TEX_N = (r"u|eq|eg|ot|otin|abla|mid|leq|geq|exists|ewline|olimits|onumber|subseteq|supseteq|vdash"   # (not \nparallel,
          # \nsim or \ncong: a new line before "parallel plates" is far likelier than those symbols)
          r"|leftarrow|rightarrow|leftrightarrow|Leftarrow|Rightarrow|Leftrightarrow|earrow|warrow")
_TEX_ESCAPE = re.compile(r"[bf][A-Za-z]|r[a-z]|t[a-z]{2}|to(?![A-Za-z])|u(?![0-9a-fA-F]{4})"
                         rf"|n(?:{_TEX_N})(?![A-Za-z])|n[ei](?=[\s{{}}\\$0-9])")


def _tex_backslashes(body: str) -> str:
    out, i = [], 0
    while (j := body.find("\\", i)) >= 0:
        nxt = body[j + 1:j + 2]
        keep = nxt in '"\\/' or (nxt in "bfnrtu" and not _TEX_ESCAPE.match(body, j + 1))
        out += [body[i:j], body[j:j + 2] if keep else "\\\\" + nxt]
        i = j + 2
    return "".join(out) + body[i:]


def parse_json(text: str) -> Any:
    """The JSON value in an answer (also inside a ```json fence or surrounded by a sentence); None if there is none."""
    body = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", body, re.S)
    if fence:
        body = fence.group(1).strip()
    if "\\" in body:
        body = _tex_backslashes(body)
    try:
        return json.loads(body)
    except ValueError:
        pass
    a, b = body.find("{"), body.rfind("}")
    if 0 <= a < b:
        try:
            return json.loads(body[a:b + 1])
        except ValueError:
            return None
    return None


def ask_json(system_prompt: str, text: str, *, model: str = "sonnet", effort: str = "",
             cancel: Optional[threading.Event] = None, timeout_s: int = 900) -> tuple[Any, dict[str, Any]]:
    """One question whose answer must be JSON: (parsed answer, run info with usage). Asks once more if the first
    answer can't be parsed."""
    res = one_shot(system_prompt, [{"type": "text", "text": text}], model, effort, cancel, None, timeout_s)
    data = parse_json(res["text"])
    if data is None:
        log.info("The answer was not JSON; asking again")
        again = one_shot(system_prompt, [{"type": "text", "text": text + "\n\nReply with the JSON object only."}],
                         model, effort, cancel, None, timeout_s)
        for k, v in again["usage"].items():
            res["usage"][k] = res["usage"].get(k, 0) + v
        data = parse_json(again["text"])
        if data is None:
            raise RuntimeError(t("{ai}'s answer could not be read (it was not valid JSON). Try again.", ai=ai_name()))
    return data, res


def one_shot(system_prompt: str, content: list[dict[str, Any]], model: str = "sonnet", effort: str = "",
             cancel: Optional[threading.Event] = None, on_text: Optional[Callable[[str], None]] = None,
             timeout_s: int = 900, *, think: bool = True, drops: Optional[int] = None,
             on_wait: Optional[Callable[[dict[str, Any]], None]] = None) -> dict[str, Any]:
    """One request (no tools, no history) to the AI the user chose: text, usage, model. ``effort``: the reasoning
    level (effort.resolve; "" = low for Claude, the one ``model`` meant for ChatGPT, thinking off offline).
    ``think=False``: a task that needs no extended thinking (a summary of given facts) - Claude then answers without
    it. ChatGPT only (the CLIs ask again inside): ``drops``, how many times a connection that dropped before any of
    the answer came is asked again (None: chatgpt.one_shot's default), ``on_wait``: told before each such wait."""
    p = provider()
    if p == "chatgpt":
        kw: dict[str, Any] = {"on_wait": on_wait}
        if drops is not None:
            kw["drops"] = drops
        return _chatgpt_one_shot(system_prompt, content, model, effort, cancel, on_text, timeout_s, **kw)
    if p == "codex":
        return _codex_one_shot(system_prompt, content, model, effort, cancel, on_text, timeout_s)
    if p == "local":
        from . import appconfig, local_llm

        text = "\n\n".join(b.get("text", "") for b in content if b.get("type") == "text")
        material, found, task = text.partition(TASK_MARK)
        if found:                      # the lecture first, the task last (run_claude)
            system_prompt, text = f"{system_prompt}\n\n{task.strip()}", material.replace(TRANSCRIPT_MARK, "", 1).strip()
        else:
            for marker in (TRANSCRIPT_MARK, "=== THE WEEK ==="):
                task, found, rest = text.partition(marker)
                if found:              # the task stays whole; only the material is condensed when it is too long
                    system_prompt, text = f"{system_prompt}\n\n{task.strip()}", rest.strip()
                    break
        return local_llm.one_shot(system_prompt, text, appconfig.load().local_model, cancel, on_text,
                                  think=effort == "on")
    if p != "claude":
        raise RuntimeError(t("AI features are turned off (Settings → AI)."))
    return _claude_one_shot(system_prompt, content, model, effort, cancel, on_text, timeout_s, think=think)


def _claude_one_shot(system_prompt: str, content: list[dict[str, Any]], model: str = "sonnet", effort: str = "",
                     cancel: Optional[threading.Event] = None, on_text: Optional[Callable[[str], None]] = None,
                     timeout_s: int = 900, think: bool = True) -> dict[str, Any]:
    """One request to Claude Code in a fresh headless process (no tools, no history): text, usage, model.

    The process ends after this one answer, so nothing would read its prompt cache: it writes none
    (DISABLE_PROMPT_CACHING). With a subscription Claude Code would otherwise write the whole request to a one-hour
    cache, priced at twice the rate of plain input. ``think=False``: no extended thinking (for Haiku, which
    ``--effort`` does not apply to, Claude Code otherwise allows 31,999 thinking tokens)."""
    _require_claude()
    env = {**_claude_env(), "DISABLE_PROMPT_CACHING": "1"}
    if not think:
        env["CLAUDE_CODE_DISABLE_THINKING"] = "1"
    message = {"type": "user", "message": {"role": "user", "content": turn_blocks(content)}}
    with tempfile.TemporaryDirectory(prefix="studia-claude-") as work:
        sp = Path(work) / "system.txt"
        sp.write_text(system_prompt, encoding="utf-8")
        started = time.monotonic()
        try:
            proc = subprocess.Popen(_command(model, effort, sp), cwd=work, stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=_NO_WINDOW, env=env)
        except OSError as exc:
            log.warning("Claude Code did not start: %r", exc)
            raise _start_failure("claude", exc) from None
        assert proc.stdin and proc.stdout
        proc.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
        proc.stdin.close()
        result: dict[str, Any] = {}
        text_parts: list[str] = []
        killer_stop = threading.Event()
        timed_out: list[bool] = []

        def killer() -> None:
            while not killer_stop.wait(0.5):
                if (cancel is not None and cancel.is_set()) or time.monotonic() - started > timeout_s:
                    if not (cancel is not None and cancel.is_set()):
                        timed_out.append(True)
                    proc.kill()
                    return

        threading.Thread(target=killer, daemon=True).start()
        kinds: list[str] = []                      # what it printed (see CLAUDE_KINDS)
        try:
            for raw in proc.stdout:
                try:
                    ev = json.loads(raw.decode("utf-8", "replace"))
                except ValueError:
                    if raw.strip() and len(kinds) < 400:
                        kinds.append("(not JSON)")
                    continue
                if not isinstance(ev, dict):
                    continue
                if len(kinds) < 400:
                    kinds.append(str(ev.get("type")))
                if ev.get("type") == "assistant":
                    for block in (ev.get("message") or {}).get("content") or []:
                        if block.get("type") == "text" and block.get("text"):
                            text_parts.append(block["text"])
                            if on_text:
                                on_text("".join(text_parts))
                elif ev.get("type") == "rate_limit_event":
                    note_limits(ev.get("rate_limit_info") or {})
                elif ev.get("type") == "result":
                    result = ev
        finally:
            killer_stop.set()
            proc.wait(timeout=30)
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        err = (proc.stderr.read() or b"").decode("utf-8", "replace") if proc.stderr else ""
    if timed_out and not result:
        log.warning("Claude Code did not answer within %s s: %s", timeout_s, err[-400:])
        raise RuntimeError(t("Claude took too long to answer. Try again; if it keeps happening, pick a lower "
                             "reasoning level."))
    if not result and proc.returncode == 0 and kinds and not CLAUDE_KINDS & set(kinds):
        # it ended well and printed, but nothing IEUStudia reads: Claude Code's output changed (an update of it)
        raise health.changed("claude", "answers", f"Claude Code {claude_cli_info().get('version')} printed "
                                                  f"{len(kinds)} lines of kinds IEUStudia doesn't know: {_kinds(kinds)}")
    if result and not result.get("is_error") and result.get("subtype") in (None, "success") \
            and "result" not in result and not text_parts:
        raise health.changed("claude", "answers", f"Claude Code {claude_cli_info().get('version')}'s result has no "
                                                  f"text ({', '.join(sorted(result)[:12])})")
    if not result or result.get("is_error") or result.get("subtype") not in (None, "success"):
        msg = result.get("result") or (err.strip().splitlines() or [t("Claude did not return a result.")])[-1]
        raise _claude_failure(msg)
    health.ok("claude", "answers")
    return {
        "text": result.get("result") or "".join(text_parts),
        "usage": usage_of(result),
        "cost_usd": result.get("total_cost_usd"),
        "seconds": round(time.monotonic() - started, 1),
        "model": model,
    }


# ---------------------------------------------------------------------- conversation (chat)
class ClaudeSession:
    """A conversation held by one long-lived headless Claude Code process (stream-json in and out).

    The system prompt (instructions + lecture transcripts) is fixed for the process and every :meth:`ask`
    adds a turn. Claude Code keeps the history, and its prompt cache makes follow-up questions cheap.
    Setting the ``stop`` event passed to :meth:`ask` interrupts the answer but keeps the conversation.
    """

    STOP_GRACE_S = 10.0

    def __init__(self, system_prompt: str, model: str = "sonnet", effort: str = "") -> None:
        self.model = model
        self.key: Any = None                  # owner's fingerprint of what this session was started with
        _require_claude()
        self._work = Path(tempfile.mkdtemp(prefix="studia-chat-"))
        sp = self._work / "system.txt"
        sp.write_text(system_prompt, encoding="utf-8")
        try:
            self._proc = subprocess.Popen(_command(model, effort, sp, partial=True), cwd=self._work,
                                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                          creationflags=_NO_WINDOW, env=_claude_env())
        except OSError as exc:
            shutil.rmtree(self._work, ignore_errors=True)
            log.warning("Claude Code did not start: %r", exc)
            raise _start_failure("claude", exc) from None
        self._events: "queue.Queue[Optional[dict[str, Any]]]" = queue.Queue()
        self._stderr: "collections.deque[str]" = collections.deque(maxlen=40)
        self._turn = threading.Lock()
        self.last_used = time.monotonic()
        threading.Thread(target=self._read_stdout, name="claude-out", daemon=True).start()
        threading.Thread(target=self._read_stderr, name="claude-err", daemon=True).start()

    # ---------------------------------------------------------------- process plumbing
    def _read_stdout(self) -> None:
        assert self._proc.stdout
        for raw in self._proc.stdout:
            try:
                self._events.put(json.loads(raw.decode("utf-8", "replace")))
            except ValueError:
                continue
        self._events.put(None)

    def _read_stderr(self) -> None:
        assert self._proc.stderr
        for raw in self._proc.stderr:
            line = raw.decode("utf-8", "replace").strip()
            if line:
                self._stderr.append(line)

    def _write(self, obj: dict[str, Any]) -> None:
        assert self._proc.stdin
        self._proc.stdin.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
        self._proc.stdin.flush()

    def _died(self) -> RuntimeError:
        try:
            self._proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        time.sleep(0.1)    # let the stderr reader catch the last lines
        tail = next((ln for ln in reversed(self._stderr) if ln), "")
        log.warning("Claude Code exited (code %s): %s", self._proc.returncode, " | ".join(self._stderr)[-600:])
        return _claude_failure(tail) if tail else RuntimeError(t("Claude Code stopped unexpectedly. Try again."))

    def alive(self) -> bool:
        return self._proc.poll() is None

    def busy(self) -> bool:
        return self._turn.locked()

    # ---------------------------------------------------------------- a turn
    def ask(self, content: list[dict[str, Any]], on_text: Optional[Callable[[str], None]] = None,
            stop: Optional[threading.Event] = None, timeout_s: float = 900) -> dict[str, Any]:
        """Send one user message and wait for the whole answer.

        Returns ``{"text", "usage", "stopped"}``; ``stopped`` is True when ``stop`` was set (the partial
        answer is returned). Raises RuntimeError when Claude reports an error or the process dies."""
        with self._turn:
            if not self.alive():
                raise self._died()
            started = time.monotonic()
            try:
                self._write({"type": "user", "message": {"role": "user", "content": turn_blocks(content)}})
            except (OSError, ValueError):       # ValueError: the pipe was closed
                raise self._died() from None
            blocks: list[str] = []
            streamed = False
            interrupted_at: Optional[float] = None
            timed_out = False
            result: Optional[dict[str, Any]] = None
            kinds: list[str] = []                  # what it printed this turn (see CLAUDE_KINDS)
            while True:
                try:
                    ev = self._events.get(timeout=0.2)
                except queue.Empty:
                    ev = {}
                if ev and isinstance(ev, dict) and len(kinds) < 400:
                    kinds.append(str(ev.get("type")))
                    if len(kinds) >= 20 and not CLAUDE_KINDS & set(kinds) and interrupted_at is None:
                        self.kill()               # 20 lines and not one IEUStudia reads: not waited for to the end
                        raise health.changed("claude", "answers", f"a chat's turn printed only kinds IEUStudia "
                                                                  f"doesn't know: {_kinds(kinds)}")
                now = time.monotonic()
                if interrupted_at is None and ((stop is not None and stop.is_set()) or now - started > timeout_s):
                    timed_out = not (stop is not None and stop.is_set())
                    interrupted_at = now
                    try:
                        self._write({"type": "control_request", "request_id": f"ls-{uuid.uuid4().hex[:10]}",
                                     "request": {"subtype": "interrupt"}})
                    except (OSError, ValueError):
                        pass
                if interrupted_at is not None and now - interrupted_at > self.STOP_GRACE_S:
                    self.kill()           # did not stop in time; the owner starts a new session next turn
                    break
                if ev is None:            # the process ended
                    if interrupted_at is not None:
                        break
                    raise self._died()
                kind = ev.get("type")
                if kind == "stream_event":
                    e = ev.get("event") or {}
                    if e.get("type") == "content_block_start" and (e.get("content_block") or {}).get("type") == "text":
                        blocks.append("")
                    elif e.get("type") == "content_block_delta" and (e.get("delta") or {}).get("type") == "text_delta":
                        if not blocks:
                            blocks.append("")
                        blocks[-1] += e["delta"].get("text") or ""
                        streamed = True
                        if on_text:
                            on_text(_join(blocks))
                elif kind == "assistant" and not streamed:     # CLI without partial messages
                    for b in (ev.get("message") or {}).get("content") or []:
                        if b.get("type") == "text" and b.get("text"):
                            blocks.append(b["text"])
                            if on_text:
                                on_text(_join(blocks))
                elif kind == "rate_limit_event":
                    note_limits(ev.get("rate_limit_info") or {})
                elif kind == "result":
                    result = ev
                    break
            self.last_used = time.monotonic()
            if timed_out:
                if kinds and not CLAUDE_KINDS & set(kinds):     # it printed, but nothing IEUStudia reads: changed
                    raise health.changed("claude", "answers", f"a chat's turn printed only kinds IEUStudia doesn't "
                                                              f"know: {_kinds(kinds)}")
                raise RuntimeError(t("Claude took too long to answer. Try again; if it keeps happening, pick a lower "
                                     "reasoning level."))
            text = _join(blocks)
            if interrupted_at is None:
                if result is None or result.get("is_error") or result.get("subtype") not in (None, "success"):
                    raise _claude_failure((result or {}).get("result") or "")
                text = text or str(result.get("result") or "")
                health.ok("claude", "answers")
            return {"text": text, "usage": usage_of(result or {}), "stopped": interrupted_at is not None}

    # ---------------------------------------------------------------- shutdown
    def kill(self) -> None:
        try:
            self._proc.kill()
        except OSError:
            pass

    def terminate(self) -> None:
        """End the process now (app closing)."""
        self.kill()
        try:
            self._proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass
        shutil.rmtree(self._work, ignore_errors=True)

    def close(self) -> None:
        """End the conversation: closing stdin lets Claude Code exit on its own. An answer that is still
        being written (and should have been stopped) gets a moment to finish first."""
        got = self._turn.acquire(timeout=self.STOP_GRACE_S + 5)
        try:
            try:
                if self._proc.stdin and not self._proc.stdin.closed:
                    self._proc.stdin.close()
            except (OSError, ValueError):
                pass
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.kill()
                try:
                    self._proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        finally:
            if got:
                self._turn.release()
        shutil.rmtree(self._work, ignore_errors=True)


def _join(blocks: list[str]) -> str:
    return "\n\n".join(b.strip("\n") for b in blocks if b.strip())



# ======================================================================= which AI, and its sign-in
# "chatgpt" is "Continue with ChatGPT" (chatgpt.py), "codex" ChatGPT through the Codex CLI
PROVIDER_LABELS = {"chatgpt": "ChatGPT", "claude": "Claude", "codex": "ChatGPT", "local": N_("Offline model"),
                   "none": ""}                  # English (the exported notes and chats say it); t() to show it
PLAN_AIS = ("chatgpt", "claude", "codex")      # the user's own plan: big enough for study plans, reads images


def provider() -> str:
    """The AI the user chose in the first-start setup (Settings → AI). Before that choice: Claude if its CLI is here."""
    from . import appconfig

    p = appconfig.load().ai_provider
    if p:
        return p
    return "claude" if claude_cli_info()["available"] else "none"


def ai_label(p: str) -> str:
    """The name of an AI as the page shows it: "ChatGPT", "Claude", "Offline model" (in the language shown), "" for
    none."""
    label = PROVIDER_LABELS.get(p, "")
    return t(label) if p == "local" else label


def chosen_now(seen: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
    """The AI as chosen now, for the first screen, from the settings alone (no program is started): ``seen``, the
    last look at it (api._system_info), when it is about the same AI; else only its name, usable once the next look
    says so. None before an AI is chosen."""
    from . import appconfig

    p = appconfig.load().ai_provider
    if not p:
        return seen
    out = dict(seen or {}) if (seen or {}).get("provider") == p else {
        "available": False, "can_plans": p in PLAN_AIS, "can_images": p in PLAN_AIS}
    out.update(provider=p, label=ai_label(p))
    return out


def ai_info() -> dict[str, Any]:
    """The chosen AI and whether it can be used now (fast: no sign-in check)."""
    from . import appconfig

    p = provider()
    out: dict[str, Any] = {"provider": p, "label": ai_label(p), "available": False, "path": "",
                           "version": "", "can_plans": p in PLAN_AIS, "can_images": p in PLAN_AIS}
    if p == "claude":
        out.update(claude_cli_info())
    elif p == "chatgpt":
        from . import chatgpt

        out.update(available=chatgpt.signed_in(), version="Continue with ChatGPT")
    elif p == "codex":
        out.update(codex_cli_info())
    elif p == "local":
        from .components import llm_ready

        key = appconfig.load().local_model
        out.update(available=llm_ready(key), version=key)
    from .effort import supported

    out["efforts"] = list(supported(p))         # the reasoning levels it takes (the page's choices)
    return out


def claude_info() -> dict[str, Any]:          # the name the rest of the app uses
    return ai_info()


def ai_name() -> str:
    """The chosen AI in messages: "ChatGPT", "Claude" or "The offline model" (in the language shown)."""
    p = provider()
    return t("The offline model") if p == "local" else PROVIDER_LABELS.get(p) or t("The AI")


def model_label(level: str, effort: str = "") -> str:
    """The chosen AI with the app's model level, e.g. in an exported chat: "Claude Sonnet", "ChatGPT, medium
    reasoning" (``effort``: the level it reasoned at, "" = the one ``level`` meant)."""
    from .effort import LABELS, LEGACY

    p = provider()
    if p in ("chatgpt", "codex"):
        return f"ChatGPT, {LABELS.get(effort or LEGACY.get(level, ''), level).lower()} reasoning"
    if p == "claude":
        return f"Claude {level.title()}" + (f", {LABELS[effort].lower()} effort" if effort in LABELS else "")
    return PROVIDER_LABELS.get(p) or "AI"


def _run(cmd: list[str], timeout: float = 20, env: Optional[dict[str, str]] = None) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, creationflags=_NO_WINDOW,
                           encoding="utf-8", errors="replace", env=env)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.SubprocessError):
        return -1, ""


def claude_auth() -> dict[str, Any]:
    """Claude Code's sign-in: {"logged_in", "plan", "email"} (``claude auth status``, about a second)."""
    info = claude_cli_info()
    if not info["available"]:
        return {"logged_in": False, "plan": "", "email": ""}
    code, out = _run([info["path"], "auth", "status", "--json"], env=_claude_env())
    try:
        d = json.loads(out[out.find("{"):out.rfind("}") + 1])
    except ValueError:
        return {"logged_in": False, "plan": "", "email": "", "error": out.strip()[-300:]}
    if not isinstance(d, dict) or "loggedIn" not in d:      # its answer no longer says whether it is signed in
        health.changed("claude", "signin", f"auth status --json of Claude Code {info.get('version')} has no loggedIn "
                                           f"({', '.join(sorted(d)[:12]) if isinstance(d, dict) else type(d).__name__})")
        return {"logged_in": False, "plan": "", "email": "", "error": health.said("claude", "signin")}
    health.ok("claude", "signin")
    # authMethod: "claude.ai" (the subscription), "api_key" (apiKeySource: ANTHROPIC_API_KEY, "/login managed key" for
    # an Anthropic Console sign-in, apiKeyHelper), "api_key_helper", "oauth_token" (CLAUDE_CODE_OAUTH_TOKEN),
    # "third_party", "none"; before 2.1: "apiKey", "console"
    method = str(d.get("authMethod") or "")
    plan = str(d.get("subscriptionType") or "")
    if not plan and method in ("api_key", "api_key_helper", "apiKey", "console"):
        plan = "api"
    return {"logged_in": bool(d.get("loggedIn")), "plan": plan, "email": str(d.get("email") or ""),
            "method": method, "key_source": str(d.get("apiKeySource") or "")}


# ----------------------------------------------------------------------- ChatGPT through the Codex CLI
def _codex_candidates() -> list[str]:
    """Where the Codex CLI can be: the user's own install (npm, PATH) first, then the copy IEUStudia installed. npm's
    shims start Node.js, which starts the program: the program in the npm package is used (x64 and Arm builds, and
    the older layout of the package)."""
    from .ai_setup import codex_exe

    env = os.environ
    c: list[str] = []
    for npm in _npm_folders():
        pkg = os.path.join(npm, "node_modules", "@openai", "codex")
        c += [os.path.join(pkg, "node_modules", "@openai", "codex-win32-x64", "vendor", "x86_64-pc-windows-msvc", "bin",
                           "codex.exe"),
              os.path.join(pkg, "node_modules", "@openai", "codex-win32-arm64", "vendor", "aarch64-pc-windows-msvc",
                           "bin", "codex.exe"),
              os.path.join(pkg, "vendor", "x86_64-pc-windows-msvc", "codex", "codex.exe")]
    c += [os.path.join(env.get("LOCALAPPDATA", ""), "Programs", "codex", "codex.exe"),
          os.path.join(env.get("USERPROFILE", ""), ".local", "bin", "codex.exe"),
          shutil.which("codex.exe") or ""]
    if sys.platform != "win32":
        c.insert(0, os.path.join(os.path.expanduser("~"), ".local", "bin", "codex"))
        c.append(shutil.which("codex") or "")
    c.append(codex_exe())
    return list(dict.fromkeys(x for x in c if x and os.path.isfile(x)))


# the Codex CLI: the version IEUStudia's flags were checked with; an older one is asked for `codex exec --help`, and one
# without a flag the app passes is passed over (it failed every request with "unexpected argument", shown as "ChatGPT
# did not return an answer"), so IEUStudia's own copy is used instead
CODEX_KNOWN_GOOD = (0, 156, 1)
CODEX_FLAGS = ("--json", "--ignore-user-config", "--ephemeral", "--skip-git-repo-check")
_codex_versions: dict[str, tuple[str, str, bool]] = {}   # path → (size:modified, version, takes the flags)


def _codex_stamp(path: str) -> str:
    try:
        st = os.stat(path)
        return f"{st.st_size}:{st.st_mtime_ns}"
    except OSError:
        return ""


@functools.lru_cache(maxsize=1)
def codex_cli_info() -> dict[str, Any]:
    """The Codex CLI to use: the first copy that runs and takes the flags IEUStudia passes. Without one: "too_old"
    (a copy that lacks them) or "problem" (a copy that does not start)."""
    old: dict[str, str] = {}
    problem: dict[str, str] = {}
    for path in _codex_candidates():
        stamp = _codex_stamp(path)
        known = _codex_versions.get(path)
        if stamp and known and known[0] == stamp:
            ver, ok = known[1], known[2]
        elif failed := _failed_before(path, stamp):
            problem = problem or {"path": path, "error": failed}
            continue
        else:
            code, out, why = _probe([path, "--version"], 20)
            ver = out.strip().split()[-1] if code == 0 and out.strip() else ""
            if not ver:
                log.warning("The Codex CLI at %s did not start: %s", path, why or f"exit code {code}: {out.strip()[-200:]}")
                why = why or t("it ended with code {code}", code=code)
                _failed[path] = (stamp, time.monotonic(), why)
                problem = problem or {"path": path, "error": why}
                continue
            ok = _version_of(ver) >= CODEX_KNOWN_GOOD
            if not ok:
                code, out, _ = _probe([path, "exec", "--help"], 20)
                ok = code == 0 and all(f in out for f in CODEX_FLAGS)
                if not ok:
                    log.warning("The Codex CLI %s at %s is too old for IEUStudia; looking for another copy", ver, path)
            if stamp:
                _codex_versions[path] = (stamp, ver, ok)
        if ok:
            return {"available": True, "path": path, "version": ver}
        old = old or {"path": path, "version": ver}
    out_: dict[str, Any] = {"available": False, "path": "", "version": ""}
    if old:
        out_["too_old"] = old
    elif problem:
        out_["problem"] = problem
    return out_


def _codex_too_old(path: str) -> None:
    """The Codex CLI turned down a flag IEUStudia passes: that copy is passed over from now on."""
    stamp = _codex_stamp(path)
    known = _codex_versions.get(path)
    if stamp:
        _codex_versions[path] = (stamp, known[1] if known else "", False)
    codex_cli_info.cache_clear()


CODEX_NOT_PASSED_ON = ("CODEX_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL")


def _codex_env() -> dict[str, str]:
    """The environment for the Codex CLI: without an OpenAI API key or another server set on this PC (for coursework,
    another tool), so it uses the ChatGPT sign-in and plan, as Claude Code uses the Claude subscription."""
    return {k: v for k, v in os.environ.items() if k.upper() not in CODEX_NOT_PASSED_ON}


def codex_auth() -> dict[str, Any]:
    info = codex_cli_info()
    if not info["available"]:
        return {"logged_in": False, "plan": "", "email": ""}
    code, out = _run([info["path"], "login", "status"], env=_codex_env())
    low = out.lower()
    logged = code == 0 and "logged in" in low and "not logged in" not in low
    return {"logged_in": logged, "plan": "chatgpt" if "chatgpt" in low else ("api" if "api key" in low else ""), "email": ""}


CODEX_EFFORT = {"haiku": "low", "sonnet": "medium", "opus": "high"}


def _codex_model() -> str:
    """The ChatGPT model chosen in Settings, if the Codex CLI offers it and has not turned it down ("" = Codex's
    default). The setting is shared with "Continue with ChatGPT", whose list can differ."""
    from . import appconfig

    chosen = appconfig.load().codex_model
    return chosen if chosen in codex_models() else ""


_rejected: set[str] = set()


def _rejected_path() -> Path:
    from .paths import app_dir

    return app_dir() / "cache" / "codex-rejected.json"


def codex_rejected() -> set[str]:
    """The models Codex listed but turned down ("model not supported when using Codex with a ChatGPT account", HTTP
    400: gpt-6.1-sol, Nov 2026): kept, so they are no longer offered or sent."""
    try:
        saved = json.loads(_rejected_path().read_text(encoding="utf-8"))
        return _rejected | {str(x) for x in saved if isinstance(x, str)}
    except (OSError, ValueError, TypeError):
        return set(_rejected)


def _codex_reject(model: str) -> None:
    _rejected.add(model)
    try:
        path = _rejected_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(sorted(codex_rejected() | {model})), encoding="utf-8")
    except OSError:
        log.debug("Could not keep the models Codex turned down", exc_info=True)


def _accepted_path() -> Path:
    from .paths import app_dir

    return app_dir() / "cache" / "codex-accepted.json"


def codex_accepted() -> set[str]:
    """The chosen models Codex has answered with at least once. Until then a chat is sized for Codex's default model
    too (chatlimits), so that the same request still fits when Codex turns the chosen one down and asks its default.
    (Not kept: the smaller size again, which is safe.)"""
    try:
        saved = json.loads(_accepted_path().read_text(encoding="utf-8"))
        return {str(x) for x in saved if isinstance(x, str)}
    except (OSError, ValueError, TypeError):
        return set()


def _codex_accept(model: str) -> None:
    if not model or model in codex_accepted():
        return
    try:
        path = _accepted_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(sorted(codex_accepted() | {model})), encoding="utf-8")
    except OSError:
        log.debug("Could not keep the models Codex answered with", exc_info=True)


def _model_refused(message: str) -> bool:
    low = (message or "").lower()
    return "not supported" in low and "model" in low


def _codex_old_message(version: str) -> str:
    return t("The Codex CLI on this PC ({version}) is too old for IEUStudia. In Settings → AI click Install Codex CLI: "
             "IEUStudia then uses its own, newer copy (yours stays as it is).", version=version or "?")


def _codex_command(work: Path, model: str, effort: str, images: Sequence[Path] = ()) -> list[str]:
    info = codex_cli_info()
    if not info["available"]:
        if info.get("too_old"):
            raise RuntimeError(_codex_old_message(info["too_old"]["version"]))
        raise RuntimeError(t("The Codex CLI is not installed on this PC (Settings → AI)."))
    cmd = [info["path"], "exec", "--json", "--ignore-user-config", "--ephemeral", "--skip-git-repo-check",
           "-s", "read-only", "-C", str(work), "-c", f'model_reasoning_effort="{effort or CODEX_EFFORT.get(model, "medium")}"']
    chosen = _codex_model()
    if chosen:
        cmd += ["-m", chosen]
    for img in images:      # by name in the work folder (the process runs there): with full paths a few hundred
        # pictures already pass Windows' 32,767 characters of command line ("WinError 206"), measured at about 600
        cmd += ["-i", Path(img).name if Path(img).parent == Path(work) else str(img)]
    return cmd + ["-"]


CODEX_RULES = ("You are not working on code: do not run commands, read files or browse. Answer directly from the "
               "material in this message.")


def _codex_text(system_prompt: str, content: list[dict[str, Any]], work: Path) -> tuple[str, list[Path]]:
    texts, images = [], []
    for i, b in enumerate(content):
        if b.get("type") == "text":
            texts.append(b.get("text", ""))
        elif b.get("type") == "image" and (b.get("source") or {}).get("data"):
            img = work / f"slide-{i}.jpg"
            img.write_bytes(base64.b64decode(b["source"]["data"]))
            images.append(img)
    return f"{system_prompt}\n\n{CODEX_RULES}\n\n" + "\n\n".join(texts), images


_CODEX_NETWORK = ("stream disconnected", "error sending request", "dns error", "connection refused", "connection reset",
                  "failed to connect", "timed out", "network is unreachable", "no route to host")
CODEX_UNREACHABLE = N_("ChatGPT could not be reached through the Codex CLI: check your internet connection (on campus "
                       "Wi-Fi, make sure you are signed in to it), then try again.")


def _codex_error(message: str) -> str:
    low = (message or "").lower()
    if "usage limit" in low or "rate limit" in low or "too many requests" in low:
        return t("You have reached your ChatGPT usage limit for Codex. Try again later.")
    if ("not logged in" in low or "login" in low or "unauthorized" in low or "401" in low or "refresh token" in low
            or "sign in again" in low or "log in again" in low):
        return t("The Codex CLI is not signed in. Sign in under Settings → AI.")
    if _model_refused(message):
        return t("Your ChatGPT plan does not offer the chosen model. Settings → AI → ChatGPT model: Default.")
    service = aierrors.from_message("ChatGPT", message)  # "unexpected status 500 …": on OpenAI's side
    if service is not None:
        return str(service)
    from .net import cert_date_said, clock_tls_text

    if cert_date_said(message):                       # a wrong date on this PC ("error sending request" comes with it)
        return clock_tls_text()
    if any(k in low for k in _CODEX_NETWORK):
        return t(CODEX_UNREACHABLE)
    if "contains a virus" in low or "potentially unwanted software" in low:
        return t("Windows Security (or your antivirus) blocked the Codex CLI. It comes from OpenAI: if your antivirus "
                 "put it in quarantine, restore it there, then try again.")
    if "panicked at" in low:                          # the program itself crashed: its own words mean nothing here
        return t("The Codex CLI stopped unexpectedly. Try again; if it keeps happening, restart the PC, or use plain "
                 "ChatGPT (Continue with ChatGPT) in Settings → AI.")
    return f"ChatGPT: {message}"[:400] if message else t("ChatGPT did not return an answer.")


def _codex_last_words(err: str) -> str:
    """What the Codex CLI said went wrong: its error line ("ERROR …" in its log, "error: …" about its arguments), or
    else its last line that says something (not a warning, a tip or its usage)."""
    lines = [ln.strip() for ln in (err or "").splitlines() if ln.strip()]
    for ln in reversed(lines):
        if ("ERROR" in ln and "rmcp" not in ln) or ln.lower().startswith("error"):
            return ln
    return next((ln for ln in reversed(lines) if not ln.lower().startswith(
        ("warning", "tip:", "usage:", "for more information", "note:"))), "")


def _codex_failure(message: str) -> RuntimeError:
    """The Codex CLI's error as the exception raised (a ServiceError when it failed on OpenAI's side: the CLI has
    already asked again by then), with its own words kept in the log."""
    text = _codex_error(message)
    service = aierrors.from_message("ChatGPT", message)
    if service is not None and text == str(service):
        log.warning("ChatGPT (Codex CLI) failed on its side: %s", (message or "")[:800])
        return service
    # the connection ("stream disconnected", after the CLI's own retries): a reading in parts asks again by itself
    err = aierrors.Dropped(text) if text == t(CODEX_UNREACHABLE) else RuntimeError(text)
    err.codex_message = message or ""          # type: ignore[attr-defined] - its own words (a model turned down)
    return err


def _chatgpt_one_shot(system_prompt: str, content: list[dict[str, Any]], model: str, effort: str,
                      cancel: Optional[threading.Event], on_text: Optional[Callable[[str], None]],
                      timeout_s: float, **kw: Any) -> dict[str, Any]:
    """One request on the user's ChatGPT plan through "Continue with ChatGPT" (chatgpt.py)."""
    from . import chatgpt

    return chatgpt.one_shot(system_prompt, content, model, effort, cancel, on_text, timeout_s, **kw)


def _codex_one_shot(system_prompt: str, content: list[dict[str, Any]], model: str, effort: str,
                    cancel: Optional[threading.Event], on_text: Optional[Callable[[str], None]],
                    timeout_s: float) -> dict[str, Any]:
    """One request to ChatGPT through the Codex CLI. A chosen model that Codex turns down is remembered (no longer
    offered: codex_models) and the request is asked again with Codex's default model; the answer then says which
    model was turned down (``model_refused``), so the chat can tell the student once."""
    chosen = _codex_model()
    try:
        res = _codex_once(system_prompt, content, model, effort, cancel, on_text, timeout_s)
        _codex_accept(chosen)                      # it answers with it: chats may use its whole window from now on
        return res
    except RuntimeError as exc:
        refused = getattr(exc, "codex_message", "")
        if not chosen or not _model_refused(refused) or (cancel is not None and cancel.is_set()):
            raise
        log.warning("Codex turned down the model %s (%s); asking again with its default model", chosen, refused[:200])
        _codex_reject(chosen)
    return {**_codex_once(system_prompt, content, model, effort, cancel, on_text, timeout_s), "model_refused": chosen}


def _codex_once(system_prompt: str, content: list[dict[str, Any]], model: str, effort: str,
                cancel: Optional[threading.Event], on_text: Optional[Callable[[str], None]],
                timeout_s: float) -> dict[str, Any]:
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="studia-codex-") as tmp:
        work = Path(tmp)
        prompt, images = _codex_text(system_prompt, content, work)
        cmd = _codex_command(work, model, effort, images)
        try:
            proc = subprocess.Popen(cmd, cwd=work, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, creationflags=_NO_WINDOW, env=_codex_env())
        except OSError as exc:
            log.warning("The Codex CLI did not start: %r", exc)
            raise _start_failure("codex", exc) from None
        assert proc.stdin and proc.stdout
        proc.stdin.write(prompt.encode("utf-8"))
        proc.stdin.close()
        stop = threading.Event()
        timed_out: list[bool] = []

        def killer() -> None:
            while not stop.wait(0.5):
                if (cancel is not None and cancel.is_set()) or time.monotonic() - started > timeout_s:
                    if not (cancel is not None and cancel.is_set()):
                        timed_out.append(True)
                    proc.kill()
                    return

        threading.Thread(target=killer, daemon=True).start()
        texts: list[str] = []
        usage: dict[str, int] = {}
        error = ""
        kinds: list[str] = []                      # what it printed (see CODEX_KINDS)
        try:
            for raw in proc.stdout:
                try:
                    ev = json.loads(raw.decode("utf-8", "replace"))
                except ValueError:
                    if raw.strip() and len(kinds) < 400:
                        kinds.append("(not JSON)")
                    continue
                if not isinstance(ev, dict):
                    continue
                kind = ev.get("type")
                if len(kinds) < 400:
                    kinds.append(str(kind))
                item = ev.get("item") if isinstance(ev.get("item"), dict) else {}
                if kind == "item.completed" and item.get("type") == "agent_message" and item.get("text"):
                    texts.append(item["text"])
                    if on_text:
                        on_text(_join(texts))
                elif kind == "turn.completed":
                    u = ev.get("usage") or {}
                    usage = {"input_tokens": int(u.get("input_tokens") or 0), "output_tokens": int(u.get("output_tokens") or 0),
                             "cache_read_input_tokens": int(u.get("cached_input_tokens") or 0)}
                elif kind in ("error", "turn.failed"):
                    error = str(ev.get("message") or (ev.get("error") or {}).get("message") or error)
        finally:
            stop.set()
            proc.wait(timeout=30)
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        err = (proc.stderr.read() or b"").decode("utf-8", "replace") if proc.stderr else ""
    if not texts and timed_out:
        log.warning("The Codex CLI did not answer within %s s: %s", timeout_s, err[-400:])
        raise RuntimeError(t("ChatGPT took too long to answer through the Codex CLI. Try again; if it keeps happening, "
                             "pick a lower reasoning level."))
    if not texts and proc.returncode == 0 and not error and kinds and (
            not CODEX_KINDS & set(kinds) or "turn.completed" in kinds):
        # it ended well and printed, but no answer IEUStudia can read: the Codex CLI's output changed (an update)
        known = _codex_versions.get(cmd[0])
        raise health.changed("codex", "answers", f"the Codex CLI {known[1] if known else ''} printed {len(kinds)} "
                                                 f"lines without an answer IEUStudia reads: {_kinds(kinds)}")
    if not texts:
        last = error or _codex_last_words(err)
        if not error and re.search(r"unexpected argument|unrecognized (?:option|argument)|unknown (?:option|argument)",
                                   last, re.I):          # a CLI too old for the flags IEUStudia passes
            known = _codex_versions.get(cmd[0])
            version = known[1] if known else ""
            if version and _version_of(version) >= CODEX_KNOWN_GOOD:     # not older: an update of it can't help
                log.warning("The Codex CLI %s turned down a flag IEUStudia passes: %s", version, last)
                raise health.changed("codex", "answers", f"the Codex CLI {version} turned down a flag IEUStudia passes",
                                     message=t("This Codex CLI ({version}) is newer than this IEUStudia knows, and it "
                                               "turned down how IEUStudia asks it. Look for a new version of "
                                               "IEUStudia in Settings → About.", version=version))
            log.warning("The Codex CLI at %s is too old for IEUStudia: %s", cmd[0], last)
            _codex_too_old(cmd[0])
            raise RuntimeError(_codex_old_message(version))
        if not last:
            log.warning("The Codex CLI ended with %s and said nothing: %s", proc.returncode, err[-800:])
        raise _codex_failure(last)
    health.ok("codex", "answers")
    return {"text": _join(texts), "usage": usage, "cost_usd": None, "seconds": round(time.monotonic() - started, 1),
            "model": _codex_model() or "Codex's default model"}


class ChatGPTSession:
    """A chat with ChatGPT ("Continue with ChatGPT", or one ``codex exec`` per turn through the Codex CLI): each
    turn is one request that carries the lecture context and the conversation so far (OpenAI caches the long,
    unchanged beginning, so follow-ups are cheap)."""

    KEEP_TURNS, MAX_TURNS = 8, 12     # the conversation sent along: cut back to 8 turns only after 12, so its beginning
                                      # stays the same for four questions in a row and comes from OpenAI's cache

    def __init__(self, system_prompt: str, model: str = "sonnet", effort: str = "", cli: bool = False) -> None:
        if cli:
            codex_cli_info()
        self.request = _codex_one_shot if cli else _chatgpt_one_shot
        self._extra: dict[str, Any] = {} if cli else {
            "cache_key": "ieustudia-chat-" + hashlib.sha256(system_prompt.encode("utf-8", "ignore")).hexdigest()[:24]}
        self.model = model
        self.key: Any = None
        self.system = system_prompt
        self.effort = effort
        self.history: list[tuple[str, str]] = []
        self.slides: list[dict[str, Any]] = []         # the slide images with their labels, sent with every turn
        self.last_used = time.monotonic()
        self._turn = threading.Lock()

    def alive(self) -> bool:
        return True

    def busy(self) -> bool:
        return self._turn.locked()

    def seed(self, history: list[dict[str, Any]]) -> None:
        """The conversation so far, from the library (a new session for an earlier chat)."""
        question = None
        for m in history:
            if m.get("role") == "user":
                question = m.get("text") or ""
            elif question is not None and (m.get("text") or "").strip():
                self.history.append((question, m["text"]))
                question = None

    def ask(self, content: list[dict[str, Any]], on_text: Optional[Callable[[str], None]] = None,
            stop: Optional[threading.Event] = None, timeout_s: float = 900) -> dict[str, Any]:
        with self._turn:
            # blocks marked ``_turn`` are this question's only (the notes read for it, the pages that matter most)
            extra = [b for b in content if b.get("_turn")]
            content = [b for b in content if not b.get("_turn")]
            last_image = max((i for i, b in enumerate(content) if b.get("type") in ("image", "document")), default=-1)
            if last_image >= 0:                        # each turn is a new request: the slides go with every one
                self.slides = content[:last_image + 1]
            texts = [b.get("text", "") for b in content[last_image + 1:] if b.get("type") == "text"]
            question = texts[-1].strip() if texts else ""
            found = "\n\n".join(x for x in texts[:-1] if x.strip())
            if len(self.history) > self.MAX_TURNS:
                self.history = self.history[-self.KEEP_TURNS:]
            convo = "".join(f"\n\n[Student]\n{q}\n\n[You]\n{a}" for q, a in self.history)
            text = ((f"=== CONVERSATION SO FAR ==={convo}\n\n" if convo else "") + (f"{found}\n\n" if found else "")
                    + f"=== NEW QUESTION ===\n{question}")
            try:
                r = self.request(self.system, [*self.slides, *turn_blocks(extra), {"type": "text", "text": text}],
                                 self.model, self.effort, stop, on_text, timeout_s, **self._extra)
            except Cancelled:
                return {"text": "", "usage": {}, "stopped": True}
            self.history.append((question, r["text"]))
            self.last_used = time.monotonic()
            return {"text": r["text"], "usage": r["usage"], "stopped": False,
                    **({"model_refused": r["model_refused"]} if r.get("model_refused") else {})}

    def kill(self) -> None:
        pass

    def terminate(self) -> None:
        pass

    def close(self) -> None:
        pass


def new_session(system_prompt: str, model: str = "sonnet", effort: str = "") -> Any:
    """A chat with the chosen AI (Claude keeps one process per chat; the others are stateless). ``effort``: as for
    :func:`one_shot`; the ChatGPT and offline sessions take a new one between questions (their ``effort``)."""
    p = provider()
    if p in ("chatgpt", "codex"):
        return ChatGPTSession(system_prompt, model, effort, cli=p == "codex")
    if p == "local":
        from . import appconfig
        from .local_llm import LocalSession

        return LocalSession(system_prompt, appconfig.load().local_model, think=effort == "on")
    if p != "claude":
        raise RuntimeError(t("AI features are turned off (Settings → AI)."))
    return ClaudeSession(system_prompt, model, effort)


# ----------------------------------------------------------------------- set-up: status, sign-in, install
def ai_status(which: str = "") -> dict[str, Any]:
    """Everything the AI set-up shows, for each option: installed, signed in, the plan, a running install and the
    last test. ``which`` ("chatgpt", "claude" or "codex"): check only that one (the set-up polls it while you sign
    in); the others are then left out. The three are asked at once: each runs a program or two (~0.2-0.5 s each)."""
    from concurrent.futures import ThreadPoolExecutor

    from . import ai_setup, appconfig
    from .components import LLM_PRESETS, llm_ready

    cfg = appconfig.load()
    out: dict[str, Any] = {"provider": provider(), "chosen": cfg.ai_provider}

    def chatgpt_status() -> dict[str, Any]:
        from . import chatgpt

        native = chatgpt.status()          # signed_in, email, plan_allowed, registered, sign_in (a running attempt)
        return {**native, "model": cfg.codex_model, "needs": "a ChatGPT plan that apps may use (Plus or Pro)",
                "models": _chatgpt_models() if native["signed_in"] and native["plan_allowed"] else [],
                "test": ai_setup.last_test("chatgpt")}

    def claude_status() -> dict[str, Any]:
        claude_cli_info.cache_clear()
        c = claude_cli_info()
        auth = claude_auth() if c["available"] else {"logged_in": False, "plan": "", "email": ""}
        return {**c, **auth, "needs": "Claude Pro or Max subscription",
                "plan_status": ai_setup.plan_status("claude", auth["plan"], auth["logged_in"],
                                                    source=auth.get("key_source", ""), ignored=ai_setup.left_out()),
                "job": ai_setup.job("claude"), "test": ai_setup.last_test("claude"), "login": login_state("claude")}

    def codex_status() -> dict[str, Any]:
        codex_cli_info.cache_clear()
        x = codex_cli_info()
        auth = codex_auth() if x["available"] else {"logged_in": False, "plan": "", "email": ""}
        return {**x, **auth, "model": cfg.codex_model, "models": codex_models(),
                "needs": "a ChatGPT plan with Codex", "ours": bool(ai_setup.codex_exe()),
                "plan_status": ai_setup.plan_status("codex", auth["plan"], auth["logged_in"]),
                "job": ai_setup.job("codex"), "test": ai_setup.last_test("codex"), "login": login_state("codex")}

    asked = {k: f for k, f in (("chatgpt", chatgpt_status), ("claude", claude_status), ("codex", codex_status))
             if which in ("", k)}
    if len(asked) <= 1:
        out.update({k: f() for k, f in asked.items()})
    else:
        with ThreadPoolExecutor(len(asked), thread_name_prefix="ai-status") as pool:
            running = {k: pool.submit(f) for k, f in asked.items()}
            out.update({k: r.result() for k, r in running.items()})
    if not which:
        out["local"] = {"model": cfg.local_model, "models": [{"id": k, "label": t(v["label"]), "description": t(v["description"]),
                                                             "size_mb": v["size_mb"], "ready": llm_ready(k)}
                                                            for k, v in LLM_PRESETS.items()]}
    return out


def _chatgpt_models() -> list[str]:
    """The models of the account signed in with "Continue with ChatGPT" (cached by chatgpt.models)."""
    from . import chatgpt

    try:
        return [m["slug"] for m in chatgpt.models()][:12]
    except Exception as exc:  # noqa: BLE001 - only the model picker is missing then
        log.info("Could not list the ChatGPT models: %s", exc)
        return []


def codex_models() -> list[str]:
    """The models Codex lists for this account (from its own cache), for an optional override; not those it turned
    down when asked (codex_rejected)."""
    path = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "models_cache.json"
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        refused = codex_rejected()
        return [m["slug"] for m in d.get("models", []) if isinstance(m, dict) and m.get("visibility") == "list"
                and m.get("slug") not in refused][:12]
    except (OSError, ValueError, KeyError, TypeError):
        return []


# ----------------------------------------------------------------------- signing in: Claude Code's and Codex's own flow
# The vendor's sign-in command runs hidden (no console window): it opens the sign-in page in the browser and ends by
# itself once the browser has signed in. IEUStudia reads what it prints: the page's link (so the page can be opened
# again or its link copied), Claude Code's "paste the code" prompt (the code is typed in IEUStudia and passed on), the
# Codex device code, and, when it ends without signing in, why. It used to run in a console window whose end nobody
# noticed: closing that window, or a sign-in failing at once, left the list waiting ten minutes with no button.
LOGIN_LIMIT_S = 15 * 60               # a sign-in left open is ended (Codex's device code also expires after 15 minutes)
_URL = re.compile(r"https://[^\s'\"<>\x1b\x07]+")
_DEVICE_CODE = re.compile(r"\b([A-Z0-9]{4,5}-[A-Z0-9]{4,6})\b")
_TERMINAL = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-9;?]*[A-Za-z]")
_logins: dict[str, dict[str, Any]] = {}       # "claude" | "codex" -> the sign-in under way (or the last one's end)
_logins_lock = threading.Lock()
_end_at_exit = False
LoginEmit = Callable[[dict[str, Any]], None]


def _login_command(which: str, device: bool = False) -> tuple[list[str], Optional[dict[str, str]]]:
    if which == "claude":
        info = claude_cli_info()
        if not info["available"]:
            raise RuntimeError(t("Install Claude Code first."))
        return [info["path"], "auth", "login", "--claudeai"], _claude_env()
    if which == "codex":
        info = codex_cli_info()
        if not info["available"]:
            raise RuntimeError(t("Install the Codex CLI first."))
        return [info["path"], "login", *(["--device-auth"] if device else [])], _codex_env()
    raise ValueError(which)


def _start_failure(which: str, exc: OSError) -> RuntimeError:
    """Claude Code or the Codex CLI would not start at all: an antivirus, or the program gone."""
    from .ai_setup import _install_error
    from .net import plain_error

    if getattr(exc, "winerror", None) == 225:                 # quarantined by Windows Security or another antivirus
        return RuntimeError(_install_error(which, exc))
    error = plain_error(exc)
    return RuntimeError(t(START_FAILED, error=error) if which == "claude" else
                        t("The Codex CLI could not be started: {error} Install it again under Settings → AI.",
                          error=error))


def _allow_foreground() -> None:
    """The sign-in program opens the browser: it may come to the front (the student just clicked)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.WinDLL("user32").AllowSetForegroundWindow(0xFFFFFFFF)       # ASFW_ANY
    except (OSError, AttributeError):
        pass


def _end_process(proc: subprocess.Popen) -> None:
    """End the sign-in program itself, not the programs it started: a browser it opened (a child of codex.exe when no
    browser was running yet) must stay open."""
    try:
        proc.kill()
    except OSError:
        pass


def login_state(which: str) -> dict[str, Any]:
    """The sign-in under way, for the page: {"state": "waiting"|"done"|"error"|"", "page", "code", "device",
    "asks_code", "error", "started"}."""
    with _logins_lock:
        a = _logins.get(which) or {}
        # (why it failed: in the language of now, also when it was said before a language switch)
        return {k: retell(a.get(k, d)) for k, d in (("state", ""), ("page", ""), ("code", ""), ("device", False),
                                                      ("asks_code", False), ("error", ""), ("started", 0.0))}


def start_login(which: str, emit: LoginEmit = lambda e: None, device: bool = False) -> dict[str, Any]:
    """Start Claude Code's or the Codex CLI's own sign-in (``device``: Codex's sign-in with a code typed on OpenAI's
    page, for when the browser can't return to this PC). ``emit`` hears {"type": "ai_cli_login", "which", ...state}
    whenever it changes: the link found, then "done" or "error" (or "" once cancelled)."""
    from .ai_setup import forget_test

    global _end_at_exit
    cmd, env = _login_command(which, device)
    cancel_login(which, quiet=True)          # a new click replaces an attempt left open (two would fight over a port)
    if not _end_at_exit:
        import atexit

        atexit.register(end_logins)
        _end_at_exit = True
    _allow_foreground()
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                creationflags=_NO_WINDOW, env=env)
    except OSError as exc:
        log.warning("The sign-in program of %s did not start: %r", which, exc)
        raise _start_failure(which, exc) from None
    attempt: dict[str, Any] = {"proc": proc, "state": "waiting", "page": "", "code": "", "device": device,
                               "asks_code": False, "error": "", "started": time.time(), "ended": "",
                               "lines": collections.deque(maxlen=80)}
    with _logins_lock:
        _logins[which] = attempt
    log.info("Sign-in to %s started (%s)", which, " ".join(cmd[1:]))
    threading.Thread(target=_watch_login, args=(which, attempt, emit), name=f"login-{which}", daemon=True).start()
    forget_test(which)
    return login_state(which)


def _login_event(which: str, attempt: dict[str, Any], emit: LoginEmit) -> None:
    with _logins_lock:
        if _logins.get(which) is not attempt:        # replaced by a newer attempt: that one speaks now
            return
    try:
        emit({"type": "ai_cli_login", "which": which, **login_state(which)})
    except Exception:  # noqa: BLE001 - the page may be gone
        log.debug("Could not tell the page about the sign-in", exc_info=True)


def _read_login(which: str, attempt: dict[str, Any], lines: list[str], prompt: str, emit: LoginEmit) -> None:
    """What the sign-in printed: in whole ``lines``, the link and a device code; a prompt for a code can come without a
    line end (``prompt``: the start of a line still being written)."""
    changed = False
    with _logins_lock:
        for line in lines:
            if not attempt["page"] and (m := _URL.search(line)):
                attempt["page"] = m.group(0).rstrip(".,;:)")
                changed = True
            if attempt["device"] and attempt["page"] and not attempt["code"] and (m := _DEVICE_CODE.search(line)):
                attempt["code"] = m.group(1)
                changed = True
        if not attempt["asks_code"] and any("paste code" in x.lower() for x in [*lines, prompt]):
            attempt["asks_code"] = True
            changed = True
    if changed:
        _login_event(which, attempt, emit)


def _plain_lines(text: str) -> list[str]:
    return [x for x in (" ".join(ln.split()) for ln in _TERMINAL.sub("", text).replace("\r", "\n").split("\n")) if x]


def _watch_login(which: str, attempt: dict[str, Any], emit: LoginEmit) -> None:
    import codecs

    proc: subprocess.Popen = attempt["proc"]
    started = time.monotonic()

    def limit() -> None:
        if proc.poll() is None:
            attempt["ended"] = attempt["ended"] or "time"
            _end_process(proc)

    timer = threading.Timer(LOGIN_LIMIT_S, limit)
    timer.daemon = True
    timer.start()

    def read() -> None:
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        pending = ""
        assert proc.stdout
        try:
            while chunk := proc.stdout.read1(4096):          # as it comes: a prompt has no line end after it
                done, _, pending = (pending + decoder.decode(chunk)).rpartition("\n")
                lines = _plain_lines(done)
                attempt["lines"].extend(lines)
                _read_login(which, attempt, lines, pending, emit)
            lines = _plain_lines(pending + decoder.decode(b"", final=True))
            attempt["lines"].extend(lines)
            _read_login(which, attempt, lines, "", emit)
        except (OSError, ValueError):
            pass

    reader = threading.Thread(target=read, name=f"login-{which}-out", daemon=True)
    reader.start()
    try:
        code = proc.wait()                                   # it ends by itself, or by Cancel or the time limit
    finally:
        timer.cancel()
    reader.join(timeout=2)        # the last of what it printed (a program it started may keep the output open)
    seconds = time.monotonic() - started
    said = list(attempt["lines"].copy())                     # (copy: the reader may still be adding to it)
    log.info("Sign-in to %s ended with code %s after %.0f s%s:\n%s", which, code, seconds,
             f" ({attempt['ended']})" if attempt["ended"] else "", "\n".join(said[-12:]) or "(it printed nothing)")
    error: str = ""
    if attempt["ended"] == "cancel":
        state = ""
    elif attempt["ended"] == "time":
        state, error = "error", Retold(lambda: t("The sign-in waited 15 minutes and was stopped. Click the button to "
                                                 "start it again."))
    else:
        auth = claude_auth() if which == "claude" else codex_auth()
        if auth.get("logged_in"):
            state = "done"
        else:
            state, error = "error", Retold(lambda: _login_failure(which, code, said, seconds))
            log.warning("Sign-in to %s did not sign in: %s", which, error)
    with _logins_lock:
        attempt.update(state=state, error=error)
    _login_event(which, attempt, emit)


_NETWORK = ("unable to connect", "could not connect", "connection refused", "connection reset", "econnrefused",
            "econnreset", "enotfound", "getaddrinfo", "fetch failed", "network", "error sending request",
            "dns error", "could not resolve", "timed out", "socket hang up", "connection error", "connect error",
            "timeout of", "etimedout", "econnaborted")      # (axios: "Login failed: timeout of 15000ms exceeded")


def _login_failure(which: str, code: int, said: list[str], seconds: float) -> str:
    """Why the sign-in ended without signing in, and what to do."""
    claude = which == "claude"
    text = " ".join(said)
    low = text.lower()
    last = next((ln for ln in reversed(said) if re.search(r"error|failed|couldn't|could not|unable|denied|invalid",
                                                           ln, re.I) and not ln.lower().startswith("warning")), "")
    if last and not last.endswith((".", "!", "?")):
        last += "."
    if "already in use" in low or "address in use" in low or "eaddrinuse" in low:
        return (t("Claude Code could not receive the sign-in from your browser: another program uses its port. Click "
                  "the button to try again.") if claude else
                t("Another program is using the port the Codex CLI signs in through (often a Codex sign-in that is still "
                  "open somewhere). Close other Codex windows and try again, or sign in with a code instead."))
    if "local listener" in low or "oauth callback server" in low:
        return t("Claude Code could not open the local connection your browser signs in through (a security program "
                 "may block it). Try again; if it happens again, update Claude Code in Settings → AI.")
    if "couldn't save your login" in low or "could not save" in low:
        return t("Claude Code could not save your sign-in on this PC. Click the button to try again.")
    if "error loading configuration" in low or "config.toml" in low:
        return t("The Codex CLI could not read its settings file (config.toml in the .codex folder of your user "
                 "folder): {error} Fix or rename that file, then try again.", error=last or text[-200:])
    if "device code" in low and "not enabled" in low:
        return t("Signing in with a code isn't turned on for your ChatGPT account. Use the normal sign-in, or turn on "
                 "device code sign-in for Codex in ChatGPT's security settings.")
    from .net import cert_date_said, clock_tls_text

    if cert_date_said(text):                         # (before "expired": a certificate's, not the code's)
        return clock_tls_text()
    if "timed out after 15 minutes" in low or ("expired" in low and not claude):
        return t("The code expired before the sign-in was finished. Click the button to get a new one.")
    if any(k in low for k in _NETWORK):
        return (t("Claude Code could not reach Anthropic: check your internet connection (on campus Wi-Fi, make sure "
                  "you are signed in to it), then try again.") if claude else
                t("The Codex CLI could not reach OpenAI: check your internet connection (on campus Wi-Fi, make sure you "
                  "are signed in to it), then try again."))
    if last:
        return (t("The Claude Code sign-in stopped: {error}", error=last[:300]) if claude else
                t("The Codex CLI sign-in stopped: {error}", error=last[:300]))
    if code == 0:
        return (t("Claude Code said the sign-in worked, but it still reports you as signed out. Click the button to try "
                  "again.") if claude else
                t("The Codex CLI said the sign-in worked, but it still reports you as signed out. Click the button to "
                  "try again."))
    return (t("The Claude Code sign-in ended before you were signed in (code {code}). Click the button to try again.",
              code=code) if claude else
            t("The Codex CLI sign-in ended before you were signed in (code {code}). Click the button to try again.",
              code=code))


def cancel_login(which: str, quiet: bool = False) -> bool:
    """Stop the sign-in under way (Cancel, or a new attempt replacing it: ``quiet``)."""
    with _logins_lock:
        attempt = _logins.get(which)
        proc = attempt.get("proc") if attempt else None
        if proc is None or proc.poll() is not None:
            return False
        attempt["ended"] = "cancel"
        if quiet:
            _logins.pop(which, None)                 # the new attempt takes its place before its end is heard
    log.info("Sign-in to %s: cancelled%s", which, " (started again)" if quiet else "")
    _end_process(proc)
    return True


def reopen_login(which: str) -> dict[str, Any]:
    """The sign-in page once more (it didn't show up, or its tab was closed), opened by IEUStudia: {"launched"}."""
    from .osutil import open_in_browser

    state = login_state(which)
    if state["state"] != "waiting" or not state["page"]:
        return {"launched": False, **state}
    launched = bool(open_in_browser(state["page"]))
    log.info("Sign-in to %s: the page asked for again (%s)", which, "opened" if launched else "no browser started")
    return {"launched": launched, **state}


def login_code(which: str, code: str) -> None:
    """The code Anthropic's page shows when the browser can't return to Claude Code: passed on to the sign-in."""
    code = "".join((code or "").split())
    if "#" not in code:
        raise RuntimeError(t("That isn't the whole code: copy all of it from the page (it has a # in the middle), "
                             "then paste it here again."))
    with _logins_lock:
        attempt = _logins.get(which) or {}
        proc = attempt.get("proc")
        if proc is None or proc.poll() is not None or attempt.get("state") != "waiting":
            raise RuntimeError(t("The sign-in isn't waiting any more. Click the button to start it again."))
    try:
        assert proc.stdin
        proc.stdin.write((code + "\n").encode("utf-8"))
        proc.stdin.flush()
    except (OSError, ValueError):
        raise RuntimeError(t("The sign-in isn't waiting any more. Click the button to start it again.")) from None
    log.info("Sign-in to %s: a code from the page was passed on", which)


def end_logins() -> None:
    """When IEUStudia closes: no sign-in program is left waiting in the background."""
    for which in list(_logins):
        cancel_login(which, quiet=True)
