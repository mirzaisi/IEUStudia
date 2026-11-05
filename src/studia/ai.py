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

from . import aierrors
from .cancel import Cancelled
from .i18n import N_, date_short, day_month, language, t
from .notices import ALERTS_MD

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


def _candidates() -> list[str]:
    env = os.environ
    c = [
        os.path.join(env.get("APPDATA", ""), "npm", "node_modules", "@anthropic-ai", "claude-code", "bin", "claude.exe"),
        os.path.join(env.get("USERPROFILE", ""), ".local", "bin", "claude.exe"),
        shutil.which("claude.exe") or "",
    ]
    c += sorted(glob.glob(os.path.join(env.get("USERPROFILE", ""), ".vscode", "extensions",
                                       "anthropic.claude-code-*", "resources", "native-binary", "claude.exe")),
                reverse=True)
    if sys.platform != "win32":                  # Linux: the native installer's ~/.local/bin, npm, PATH
        home = os.path.expanduser("~")
        c += [os.path.join(home, ".local", "bin", "claude"), os.path.join(home, ".claude", "local", "claude"),
              shutil.which("claude") or ""]
    return [p for p in c if p and os.path.isfile(p)]


@functools.lru_cache(maxsize=1)
def claude_cli_info() -> dict[str, Any]:
    """Where Claude Code is and its version. ``claude --version`` takes ~1 s, so the answer is remembered
    until the program file changes (Claude Code updates itself)."""
    from .paths import app_dir

    cache = app_dir() / "claude-version.json"
    try:
        known = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        known = {}
    for path in _candidates():
        try:
            st = os.stat(path)
        except OSError:
            continue
        stamp = f"{st.st_size}:{st.st_mtime_ns}"
        if known.get("path") == path and known.get("stamp") == stamp and known.get("version"):
            return {"available": True, "path": path, "version": known["version"]}
        try:
            out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=20,
                                 creationflags=_NO_WINDOW, env=_claude_env())
            ver = (out.stdout or "").strip().split(" ")[0]
            if out.returncode == 0 and ver:
                try:
                    cache.write_text(json.dumps({"path": path, "stamp": stamp, "version": ver}), encoding="utf-8")
                except OSError:
                    pass
                return {"available": True, "path": path, "version": ver}
        except (OSError, subprocess.TimeoutExpired):
            continue
    return {"available": False, "path": "", "version": ""}


def _require_claude() -> str:
    info = claude_cli_info()
    if not info["available"]:
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
_OWN = (N_("You have reached your Claude usage limit — it resets {when}."),
        N_("You have reached your Claude usage limit — it resets at {time}."),
        N_("You have reached your Claude usage limit."),
        N_("Your Claude plan doesn't include Claude Code. You need Claude Pro or Max."),
        N_("Claude Code is not signed in. Settings → AI → Sign in with Claude."),
        N_("Claude Code is not installed on this PC. Settings → AI installs it for you."))


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
    if any(k in low for k in ("/login", "not logged in", "invalid api key", "authentication", "oauth")):
        return t("Claude Code is not signed in. Settings → AI → Sign in with Claude.")
    service = aierrors.from_message("Claude", m)        # "API Error: 500 …", "529 Overloaded": on Anthropic's side
    if service is not None:
        return str(service)
    return f"Claude: {m}"[:400] if m else t("Claude did not return an answer.")


def _claude_failure(message: str) -> RuntimeError:
    """Claude Code's error as the exception raised: a ServiceError when it failed on Anthropic's side (Claude Code
    has already asked again by then), with its own words kept in the log."""
    text = friendly_error(message)
    service = aierrors.from_message("Claude", message)
    if service is not None and text == str(service):
        log.warning("Claude failed on its side: %s", (message or "")[:800])
        return service
    return RuntimeError(text)


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
             timeout_s: int = 900, *, think: bool = True) -> dict[str, Any]:
    """One request (no tools, no history) to the AI the user chose: text, usage, model. ``effort``: the reasoning
    level (effort.resolve; "" = low for Claude, the one ``model`` meant for ChatGPT, thinking off offline).
    ``think=False``: a task that needs no extended thinking (a summary of given facts) - Claude then answers without
    it."""
    p = provider()
    if p == "chatgpt":
        return _chatgpt_one_shot(system_prompt, content, model, effort, cancel, on_text, timeout_s)
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
    message = {"type": "user", "message": {"role": "user", "content": content}}
    with tempfile.TemporaryDirectory(prefix="studia-claude-") as work:
        sp = Path(work) / "system.txt"
        sp.write_text(system_prompt, encoding="utf-8")
        started = time.monotonic()
        proc = subprocess.Popen(_command(model, effort, sp), cwd=work, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, creationflags=_NO_WINDOW, env=env)
        assert proc.stdin and proc.stdout
        proc.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
        proc.stdin.close()
        result: dict[str, Any] = {}
        text_parts: list[str] = []
        killer_stop = threading.Event()

        def killer() -> None:
            while not killer_stop.wait(0.5):
                if (cancel is not None and cancel.is_set()) or time.monotonic() - started > timeout_s:
                    proc.kill()
                    return

        threading.Thread(target=killer, daemon=True).start()
        try:
            for raw in proc.stdout:
                try:
                    ev = json.loads(raw.decode("utf-8", "replace"))
                except ValueError:
                    continue
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
    if not result or result.get("is_error") or result.get("subtype") not in (None, "success"):
        msg = result.get("result") or (err.strip().splitlines() or [t("Claude did not return a result.")])[-1]
        raise _claude_failure(msg)
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
        except OSError:
            shutil.rmtree(self._work, ignore_errors=True)
            raise
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
                self._write({"type": "user", "message": {"role": "user", "content": content}})
            except (OSError, ValueError):       # ValueError: the pipe was closed
                raise self._died() from None
            blocks: list[str] = []
            streamed = False
            interrupted_at: Optional[float] = None
            timed_out = False
            result: Optional[dict[str, Any]] = None
            while True:
                try:
                    ev = self._events.get(timeout=0.2)
                except queue.Empty:
                    ev = {}
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
                raise RuntimeError(t("Claude took too long to answer."))
            text = _join(blocks)
            if interrupted_at is None:
                if result is None or result.get("is_error") or result.get("subtype") not in (None, "success"):
                    raise _claude_failure((result or {}).get("result") or "")
                text = text or str(result.get("result") or "")
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


def ai_info() -> dict[str, Any]:
    """The chosen AI and whether it can be used now (fast: no sign-in check)."""
    from . import appconfig

    p = provider()
    label = PROVIDER_LABELS.get(p, "")
    out: dict[str, Any] = {"provider": p, "label": t(label) if p == "local" else label, "available": False, "path": "",
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
    plan = str(d.get("subscriptionType") or ("api" if d.get("authMethod") in ("apiKey", "console") else ""))
    return {"logged_in": bool(d.get("loggedIn")), "plan": plan, "email": str(d.get("email") or "")}


# ----------------------------------------------------------------------- ChatGPT through the Codex CLI
def _codex_candidates() -> list[str]:
    """Where the Codex CLI can be: the user's own install (npm, PATH) first, then the copy IEUStudia installed."""
    from .ai_setup import codex_exe

    env = os.environ
    npm = os.path.join(env.get("APPDATA", ""), "npm")
    c = [os.path.join(npm, "node_modules", "@openai", "codex", "node_modules", "@openai", "codex-win32-x64", "vendor",
                      "x86_64-pc-windows-msvc", "bin", "codex.exe"),
         os.path.join(env.get("LOCALAPPDATA", ""), "Programs", "codex", "codex.exe"),
         os.path.join(env.get("USERPROFILE", ""), ".local", "bin", "codex.exe"),
         shutil.which("codex.exe") or "", shutil.which("codex") or "", codex_exe()]
    if sys.platform != "win32":
        c.insert(0, os.path.join(os.path.expanduser("~"), ".local", "bin", "codex"))
    return [x for x in c if x and os.path.isfile(x)]


_codex_versions: dict[str, tuple[str, str]] = {}      # path → (size:modified, version): the same file, the same answer


@functools.lru_cache(maxsize=1)
def codex_cli_info() -> dict[str, Any]:
    for path in _codex_candidates():
        try:
            st = os.stat(path)
            stamp = f"{st.st_size}:{st.st_mtime_ns}"
        except OSError:
            stamp = ""
        known = _codex_versions.get(path)
        if stamp and known and known[0] == stamp:
            return {"available": True, "path": path, "version": known[1]}
        code, out = _run([path, "--version"], 20)
        ver = out.strip().split()[-1] if code == 0 and out.strip() else ""
        if ver:
            if stamp:
                _codex_versions[path] = (stamp, ver)
            return {"available": True, "path": path, "version": ver}
    return {"available": False, "path": "", "version": ""}


def codex_auth() -> dict[str, Any]:
    info = codex_cli_info()
    if not info["available"]:
        return {"logged_in": False, "plan": "", "email": ""}
    code, out = _run([info["path"], "login", "status"])
    low = out.lower()
    logged = code == 0 and "logged in" in low and "not logged in" not in low
    return {"logged_in": logged, "plan": "chatgpt" if "chatgpt" in low else ("api" if "api key" in low else ""), "email": ""}


CODEX_EFFORT = {"haiku": "low", "sonnet": "medium", "opus": "high"}


def _codex_model() -> str:
    """The ChatGPT model chosen in Settings, if the Codex CLI offers it ("" = Codex's default). The setting is shared
    with "Continue with ChatGPT", whose list can differ."""
    from . import appconfig

    chosen = appconfig.load().codex_model
    return chosen if chosen in codex_models() else ""


def _codex_command(work: Path, model: str, effort: str, images: Sequence[Path] = ()) -> list[str]:
    info = codex_cli_info()
    if not info["available"]:
        raise RuntimeError(t("The Codex CLI is not installed on this PC (Settings → AI)."))
    cmd = [info["path"], "exec", "--json", "--ignore-user-config", "--ephemeral", "--skip-git-repo-check",
           "-s", "read-only", "-C", str(work), "-c", f'model_reasoning_effort="{effort or CODEX_EFFORT.get(model, "medium")}"']
    chosen = _codex_model()
    if chosen:
        cmd += ["-m", chosen]
    for img in images:
        cmd += ["-i", str(img)]
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


def _codex_error(message: str) -> str:
    low = (message or "").lower()
    if "usage limit" in low or "rate limit" in low or "too many requests" in low:
        return t("You have reached your ChatGPT usage limit for Codex. Try again later.")
    if "not logged in" in low or "login" in low or "unauthorized" in low or "401" in low:
        return t("The Codex CLI is not signed in. Sign in under Settings → AI.")
    if "not supported" in low and "model" in low:
        return t("Your ChatGPT plan does not offer the chosen model. Settings → AI → ChatGPT model: Default.")
    service = aierrors.from_message("ChatGPT", message)  # "unexpected status 500 …": on OpenAI's side
    if service is not None:
        return str(service)
    return f"ChatGPT: {message}"[:400] if message else t("ChatGPT did not return an answer.")


def _codex_failure(message: str) -> RuntimeError:
    """The Codex CLI's error as the exception raised (a ServiceError when it failed on OpenAI's side: the CLI has
    already asked again by then), with its own words kept in the log."""
    text = _codex_error(message)
    service = aierrors.from_message("ChatGPT", message)
    if service is not None and text == str(service):
        log.warning("ChatGPT (Codex CLI) failed on its side: %s", (message or "")[:800])
        return service
    return RuntimeError(text)


def _chatgpt_one_shot(system_prompt: str, content: list[dict[str, Any]], model: str, effort: str,
                      cancel: Optional[threading.Event], on_text: Optional[Callable[[str], None]],
                      timeout_s: float, **kw: Any) -> dict[str, Any]:
    """One request on the user's ChatGPT plan through "Continue with ChatGPT" (chatgpt.py)."""
    from . import chatgpt

    return chatgpt.one_shot(system_prompt, content, model, effort, cancel, on_text, timeout_s, **kw)


def _codex_one_shot(system_prompt: str, content: list[dict[str, Any]], model: str, effort: str,
                    cancel: Optional[threading.Event], on_text: Optional[Callable[[str], None]],
                    timeout_s: float) -> dict[str, Any]:
    """One request to ChatGPT through the Codex CLI."""
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="studia-codex-") as tmp:
        work = Path(tmp)
        prompt, images = _codex_text(system_prompt, content, work)
        proc = subprocess.Popen(_codex_command(work, model, effort, images), cwd=work, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=_NO_WINDOW)
        assert proc.stdin and proc.stdout
        proc.stdin.write(prompt.encode("utf-8"))
        proc.stdin.close()
        stop = threading.Event()

        def killer() -> None:
            while not stop.wait(0.5):
                if (cancel is not None and cancel.is_set()) or time.monotonic() - started > timeout_s:
                    proc.kill()
                    return

        threading.Thread(target=killer, daemon=True).start()
        texts: list[str] = []
        usage: dict[str, int] = {}
        error = ""
        try:
            for raw in proc.stdout:
                try:
                    ev = json.loads(raw.decode("utf-8", "replace"))
                except ValueError:
                    continue
                kind = ev.get("type")
                item = ev.get("item") or {}
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
    if not texts:
        last = error or next((ln for ln in reversed(err.splitlines()) if "ERROR" in ln and "rmcp" not in ln), "")
        raise _codex_failure(last)
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
            last_image = max((i for i, b in enumerate(content) if b.get("type") == "image"), default=-1)
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
                r = self.request(self.system, [*self.slides, {"type": "text", "text": text}], self.model, self.effort,
                                 stop, on_text, timeout_s, **self._extra)
            except Cancelled:
                return {"text": "", "usage": {}, "stopped": True}
            self.history.append((question, r["text"]))
            self.last_used = time.monotonic()
            return {"text": r["text"], "usage": r["usage"], "stopped": False}

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
                "plan_status": ai_setup.plan_status("claude", auth["plan"], auth["logged_in"]),
                "job": ai_setup.job("claude"), "test": ai_setup.last_test("claude")}

    def codex_status() -> dict[str, Any]:
        codex_cli_info.cache_clear()
        x = codex_cli_info()
        auth = codex_auth() if x["available"] else {"logged_in": False, "plan": "", "email": ""}
        return {**x, **auth, "model": cfg.codex_model, "models": codex_models(),
                "needs": "a ChatGPT plan with Codex", "ours": bool(ai_setup.codex_exe()),
                "plan_status": ai_setup.plan_status("codex", auth["plan"], auth["logged_in"]),
                "job": ai_setup.job("codex"), "test": ai_setup.last_test("codex")}

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
    """The models Codex lists for this account (from its own cache), for an optional override."""
    path = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "models_cache.json"
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return [m["slug"] for m in d.get("models", []) if isinstance(m, dict) and m.get("visibility") == "list"][:12]
    except (OSError, ValueError, KeyError, TypeError):
        return []


_NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)


def _console(cmd: list[str], env: Optional[dict[str, str]] = None) -> None:
    """A console window of its own for ``cmd`` (on Linux, a terminal window)."""
    if sys.platform == "win32":
        subprocess.Popen(cmd, creationflags=_NEW_CONSOLE, env=env)
        return
    import shlex

    from .osutil import run_in_terminal

    if not run_in_terminal(cmd, env):
        command = shlex.join(cmd)
        if env and env.get("CLAUDE_CONFIG_DIR"):          # IEUStudia's own Claude Code profile, not your personal one
            command = f"CLAUDE_CONFIG_DIR={shlex.quote(env['CLAUDE_CONFIG_DIR'])} {command}"
        raise RuntimeError(t("No terminal app was found. Run this in a terminal: {command}", command=command))


def start_login(which: str) -> None:
    """Open the vendor's own sign-in: a small console window from the vendor's tool, which opens the sign-in page in
    the browser and finishes by itself (IEUStudia never sees the password)."""
    from .ai_setup import forget_test

    if which == "claude":
        info = claude_cli_info()
        if not info["available"]:
            raise RuntimeError(t("Install Claude Code first."))
        _console([info["path"], "auth", "login", "--claudeai"], _claude_env())
    elif which == "codex":
        info = codex_cli_info()
        if not info["available"]:
            raise RuntimeError(t("Install the Codex CLI first."))
        _console([info["path"], "login"])
    else:
        raise ValueError(which)
    forget_test(which)
