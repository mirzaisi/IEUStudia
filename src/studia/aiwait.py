"""What an AI run is doing while the student waits, so a wait never looks stuck: the question going up (with the MB
sent, when the request can tell), the AI thinking (its reasoning summaries as short "Thinking: …" lines), the answer
being written (tokens so far), a retry said in words, the time so far and a fair estimate of the time left. The
estimate comes from speeds measured on this PC (how long this AI and model took to start writing, how fast it wrote,
how long such answers are, how fast this connection uploads) and from the run's own pace; with nothing measured yet
there is none, and a run slower than usual says so instead of counting below zero.

One :class:`Wait` per run the student started (a chat answer, study notes, a plan, the digest), registered under the
run's cancel event with :func:`tracking`. Every request made with that event reports to it (:func:`of`: ai.py for
Claude Code and the Codex CLI, chatgpt.py, local_llm.py), from any thread: a study pack writes two chapters at once.
A request made with another event (the reading of a chat's pages in parts: its own progress) reports nowhere. The
page draws the snapshot (web/waits.js).
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import threading
import time
import weakref
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

log = logging.getLogger("studia")

EMIT_EVERY_S = 0.5            # the page hears of a wait at most this often (at once when its step changes)
BIG = 2 * 1024 * 1024         # a request this heavy takes a while to go up: the page says how much
LINES = 3                     # thinking lines kept for the page
LINE_CHARS = 110
MIN_WRITE_S = 3.0             # the pace of the writing counts once it has gone on this long
EMA = 0.35                    # how much one new measurement moves the speeds kept
SLOWER = 3.0                  # ... a measurement counts as at most this many times slower than the speed kept
SPEEDS_FILE = "ai-speeds.json"
_now = time.time            # (tests set the clock)


def _line(text: str) -> str:
    """A thinking summary as one short line: its last bold title ("**Comparing the circuits**", as ChatGPT writes
    them), else its last whole sentence; at most LINE_CHARS characters."""
    text = (text or "").strip()
    if not text:
        return ""
    titles = re.findall(r"\*\*([^*\n]{3,140})\*\*", text)
    if titles:
        out = titles[-1].strip()
    else:                      # prose (Claude's summaries): its last whole sentences, as many as fit on the line
        plain = re.sub(r"[*_`#>]+", "", text)
        whole = [s.strip() for s in re.split(r"(?<=[.!?])\s+", plain) if s.strip()]
        done = [s for s in whole if s[-1] in ".!?"] or whole
        out = done[-1] if done else plain
        for s in reversed(done[:-1]):
            if len(s) + 1 + len(out) > LINE_CHARS:
                break
            out = f"{s} {out}"
    out = " ".join(out.split())
    return out if len(out) <= LINE_CHARS else out[:LINE_CHARS - 1].rstrip(" ,;:") + "…"


# ---------------------------------------------------------------------------------------------- measured speeds
class Speeds:
    """How long this PC's AI requests take, kept in the app's folder: per AI, model, reasoning level and kind of
    request, the seconds before the first words of the answer ("first", thinking included), the pace of the writing
    (characters a second) and how long such answers are (characters); and how fast this connection uploads."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._data: Optional[dict[str, Any]] = None

    def _file(self) -> Path:
        if self._path is None:
            from .paths import app_dir

            self._path = app_dir() / SPEEDS_FILE
        return self._path

    def _load(self) -> dict[str, Any]:
        if self._data is None:
            try:
                data = json.loads(self._file().read_text(encoding="utf-8"))
                self._data = data if isinstance(data, dict) and isinstance(data.get("rows"), dict) else {}
            except (OSError, ValueError):
                self._data = {}
            self._data.setdefault("rows", {})
        return self._data

    def _save(self) -> None:
        try:
            path = self._file()
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_bytes(json.dumps(self._data, separators=(",", ":")).encode("utf-8"))
            tmp.replace(path)
        except OSError:
            log.debug("The AI speeds could not be kept", exc_info=True)

    @staticmethod
    def key(provider: str, model: str, level: str, kind: str) -> str:
        return "|".join((provider or "?", model or "?", level or "?", kind or "?"))

    def row(self, provider: str, model: str, level: str, kind: str) -> dict[str, float]:
        """What is known for this kind of request: the closest measurements (this model and level for this kind, else
        for any kind (its pace), else this AI for this kind (the answer's length))."""
        with self._lock:
            rows = self._load()["rows"]
            exact = rows.get(self.key(provider, model, level, kind)) or {}
            same_model = [r for k, r in rows.items() if k.startswith(f"{provider or '?'}|{model or '?'}|{level or '?'}|")]
            same_kind = [r for k, r in rows.items() if k.startswith(f"{provider or '?'}|") and k.endswith(f"|{kind or '?'}")]
        out = dict(exact)
        for field, pool in (("first", same_model), ("rate", same_model), ("chars", same_kind)):
            if field not in out:
                vals = [r[field] for r in pool if isinstance(r.get(field), (int, float)) and r[field] > 0]
                if vals:
                    out[field] = sum(vals) / len(vals)
        return out

    def note(self, provider: str, model: str, level: str, kind: str, **measured: float) -> None:
        if not measured:
            return
        with self._lock:
            data = self._load()
            row = data["rows"].setdefault(self.key(provider, model, level, kind), {})
            for k, v in measured.items():
                if v and v > 0:
                    old = row.get(k)
                    if old and int(row.get("n") or 0) >= 2:    # one request across a sleep (or a clock set forward)
                        v = min(v, old * SLOWER) if k == "first" else max(v, old / SLOWER) if k == "rate" else v
                    row[k] = round(v if not old else old + EMA * (v - old), 3)
            row["n"] = int(row.get("n") or 0) + 1
            if len(data["rows"]) > 400:           # the oldest kinds go (a key that is no longer used)
                for k in list(data["rows"])[:len(data["rows"]) - 400]:
                    data["rows"].pop(k, None)
            self._save()

    def upload(self, bps: float = 0.0) -> float:
        """This connection's measured upload speed (bytes a second); ``bps``: a new measurement to keep."""
        with self._lock:
            data = self._load()
            if bps > 0:
                old = float(data.get("upload") or 0)
                data["upload"] = round(bps if not old else old + EMA * (bps - old))
                self._save()
            return float(data.get("upload") or 0)


SPEEDS = Speeds()


# ---------------------------------------------------------------------------------------------- one run's wait
class _Req:
    """One request in flight (one thread's)."""

    def __init__(self, size: int, pictures: int, provider: str, model: str, level: str, cap: int,
                 kind: str = "") -> None:
        self.begun = _now()
        self.kind = kind               # its own kind of request (the offline model condensing a long text first)
        self.size, self.pictures, self.cap = size, pictures, cap
        self.provider, self.model, self.level = provider, model, level
        self.up = 0                    # bytes sent (when the request can tell)
        self.up_at = 0.0               # the question is up: the AI has it
        self.think_at = 0.0
        self.first_at = 0.0            # the first words of the answer
        self.chars = 0
        self.tokens = 0                # output tokens the AI reported (the offline model: as it writes)
        self.thought = ""              # the thinking summary being written
        self.line_open = False         # the page's last thinking line is this summary's (it grows in place)
        self.retry: Optional[dict[str, Any]] = None

    @property
    def phase(self) -> str:
        if self.retry and self.retry.get("until", 0) > _now() - 1:
            return "retry"
        if self.first_at:
            return "write"
        if self.think_at:
            return "think"
        if self.up_at:
            return "wait"
        return "send"


class Wait:
    """One AI run's wait, as the page shows it. ``emit(snapshot)``: tells the page (at once when the step changes,
    else at most every EMIT_EVERY_S). ``where``: what the run makes ("chat", "notes", "digest", "pack", "sheet",
    "prep", "videos", "quiz"…), for the page's words; ``kind``: the kind of request, for the speeds kept."""

    def __init__(self, emit: Optional[Callable[[dict[str, Any]], None]] = None, *, ai: str = "", provider: str = "",
                 model: str = "", level: str = "", kind: str = "", where: str = "",
                 speeds: Optional[Speeds] = None) -> None:
        self._emit = emit
        self.ai, self.provider, self.model, self.level = ai, provider, model, level
        self.kind, self.where = kind or where, where
        self._speeds = speeds or SPEEDS
        self._lock = threading.RLock()
        self._reqs: dict[int, _Req] = {}
        self.since = _now()
        self._phase = "prepare"
        self._phase_at = self.since
        self._lines: list[str] = []
        self._stage: Optional[dict[str, Any]] = None
        self._units: Optional[tuple[int, int, int]] = None
        self._ended: list[float] = []      # how long this run's finished requests took (its own pace, for units)
        self._said = 0.0
        self._read: Optional[int] = None
        self._hint: tuple[int, int, str] = (0, 0, "")   # what the next request carries in all (expect)

    # ---------------------------------------------------------------- the owner (the run)
    def phase(self, name: str) -> None:
        """A step of the run before (or between) its requests: "prepare" (its material being got ready)."""
        with self._lock:
            self._phase, self._phase_at = name, _now()
        self._tell(True)

    def stage(self, label: str = "", n: int = 0, of: int = 0, kind: str = "") -> None:
        """The run's own step ("Writing chapter 2 of 5"), and the kind of request it makes there (its speeds)."""
        with self._lock:
            self._stage = {"label": label, "n": n, "of": of} if label or of else None
            if kind:
                if kind != self.kind:
                    self._ended.clear()     # another kind of request: its own pace
                self.kind = kind
        self._tell(True)

    def set_kind(self, kind: str) -> None:
        """The kind of request the run makes now (its step's), for its speeds; nothing is said."""
        with self._lock:
            if kind and kind != self.kind:
                self.kind = kind
                self._ended.clear()
                self._units = None

    def units(self, done: int, total: int, parallel: int = 1) -> None:
        """Requests of the same kind this step makes in all (chapters): the time left counts the ones still to come."""
        with self._lock:
            self._units = (max(0, done), max(0, total), max(1, parallel))
        self._tell(True)

    def expect(self, size: int = 0, pictures: int = 0, kind: str = "") -> None:
        """What the next request weighs in all, as the run knows it (Claude Code sends the whole conversation with
        every question, its first pictures too; the request itself sees only the new message); ``kind``: its own kind
        of request (its speeds apart from the run's: the offline model condensing a long text first)."""
        with self._lock:
            self._hint = (int(size or 0), int(pictures or 0), kind)

    def read(self, pages: int) -> None:
        """"Read everything" went through these pages before the question (said with the wait)."""
        with self._lock:
            self._read = pages

    # ---------------------------------------------------------------- the request (ai.py, chatgpt.py, local_llm.py)
    def _req(self) -> _Req:
        r = self._reqs.get(threading.get_ident())
        if r is None:
            r = self._reqs[threading.get_ident()] = _Req(0, 0, self.provider, self.model, self.level, 0)
        return r

    def begin(self, size: int = 0, pictures: int = 0, *, provider: str = "", model: str = "", level: str = "",
              cap: int = 0, kind: str = "") -> None:
        """A request starts going up: ``size`` bytes with ``pictures`` pictures (and PDFs); ``cap``: the most tokens
        its answer may have (the offline model)."""
        with self._lock:
            hint, self._hint = self._hint, (0, 0, "")
            self._reqs[threading.get_ident()] = _Req(max(int(size or 0), hint[0]), max(int(pictures or 0), hint[1]),
                                                     provider or self.provider, model or self.model,
                                                     level or self.level, int(cap or 0), kind or hint[2])
            if provider:
                self.provider = provider
            if model:
                self.model = model
            if level:
                self.level = level
            self._phase = "ask"
        self._tell(True)

    def upload(self, sent: int, total: int) -> None:
        with self._lock:
            r = self._req()
            r.up, r.size = int(sent), int(total or r.size)
            done = r.size and r.up >= r.size
        if done:
            self.sent()
        else:
            self._tell(False)

    def sent(self) -> None:
        """The AI has the question (the upload ended, or its first event came)."""
        with self._lock:
            r = self._req()
            if r.up_at:
                return
            r.up_at, r.retry = _now(), None          # (asked again: it answers)
            if r.size >= BIG and r.up >= r.size and r.up_at - r.begun > 0.5:     # measured: this connection's speed
                self._speeds.upload(r.size / (r.up_at - r.begun))
        self._tell(True)

    def thinking(self, delta: str = "", whole: str = "", new: bool = False) -> None:
        """The AI thinks: ``delta`` adds to its summary being written (``new``: a new part of it begins), ``whole``
        is one summary whole (Codex's reasoning item, Claude's thinking block)."""
        with self._lock:
            r = self._req()
            first = not r.think_at
            r.up_at = r.up_at or _now()
            r.think_at = r.think_at or _now()
            r.retry = None
            if new or whole:
                r.thought, r.line_open = "", False
            r.thought += whole or delta or ""
            line = _line(r.thought)
            fresh = bool(line) and (not self._lines or self._lines[-1] != line)
            if fresh:                  # the summary being written changes its own line; a new one adds a line
                if r.line_open and self._lines:
                    self._lines[-1] = line
                else:
                    self._lines = (self._lines + [line])[-LINES:]
                r.line_open = not whole
        self._tell(fresh or first)

    def text(self, so_far: str) -> None:
        with self._lock:
            r = self._req()
            first = not r.first_at and bool(so_far)
            if first:
                r.up_at = r.up_at or _now()
                r.first_at = _now()
            r.chars = len(so_far or "")
            r.retry = None
        self._tell(first)

    def tokens(self, n: int) -> None:
        """Output tokens so far, as the AI counts them."""
        with self._lock:
            self._req().tokens = int(n or 0)
        self._tell(False)

    def retry(self, why: str, wait: float = 0.0, attempt: int = 0, of: int = 0, status: int = 0) -> None:
        """The request is asked again (by the app or by the AI's own tool): ``why`` "dropped" (the connection),
        "service" (the AI's side failed), "overloaded", "rate_limit"; ``attempt`` of ``of``: the try about to start."""
        now = _now()
        with self._lock:
            r = self._req()
            r.retry = {"why": why or "service", "wait": round(float(wait or 0), 1), "until": round(now + float(wait or 0), 1),
                       "n": int(attempt or 0), "of": int(of or 0), "status": int(status or 0)}
            r.up_at = r.think_at = r.first_at = 0.0
            r.up, r.chars, r.thought = 0, 0, ""
            r.begun = now + float(wait or 0)
        self._tell(True)

    def end(self, ok: bool = True) -> None:
        """The request is over: its times are kept as this PC's speeds when it went well."""
        with self._lock:
            r = self._reqs.pop(threading.get_ident(), None)
            if r is None:
                return
            if not self._reqs:
                self._phase, self._phase_at = "between", _now()
            now = _now()
            if ok:
                self._ended.append(now - r.begun)
            measured: dict[str, float] = {}
            if ok and r.first_at and r.up_at:
                measured["first"] = max(0.1, r.first_at - r.up_at)
                if now - r.first_at >= 1.0 and r.chars >= 200:
                    measured["rate"] = r.chars / (now - r.first_at)
                if r.chars:
                    measured["chars"] = float(r.chars)
            key = (r.provider or self.provider, r.model or self.model, r.level or self.level, r.kind or self.kind)
        if measured:
            self._speeds.note(*key, **measured)
        self._tell(True)

    # ---------------------------------------------------------------- what the page gets
    def _eta(self, r: _Req, now: float) -> tuple[Optional[float], bool]:
        """(seconds left, about; slower than usual) for one request."""
        row = self._speeds.row(r.provider or self.provider, r.model or self.model, r.level or self.level,
                               r.kind or self.kind)
        first, rate, chars = row.get("first"), row.get("rate"), row.get("chars")
        writing = (chars / rate) if chars and rate else None
        ph = r.phase
        if ph == "retry":
            return None, False
        if ph == "send":
            if r.size and r.up and now - r.begun >= 2:
                up_left: Optional[float] = (r.size - r.up) / max(1.0, r.up / (now - r.begun))
            elif r.size < BIG:
                up_left = 0.0
            else:
                bps = self._speeds.upload()
                up_left = max(0.0, r.size / bps - (now - r.begun)) if bps else None
            if up_left is None or first is None or writing is None:
                return None, False
            return up_left + first + writing, False
        if ph in ("wait", "think"):
            if first is None or writing is None:
                return None, False
            gone = now - (r.up_at or r.begun)
            if gone > first * 1.5 + 5:
                return None, True
            return max(0.0, first - gone) + writing, False
        # writing: at its own pace once it has gone on a little
        pace = r.chars / (now - r.first_at) if now - r.first_at >= MIN_WRITE_S and r.chars >= 200 else rate
        if not chars or not pace:
            return None, False
        if r.chars >= chars * 1.15:
            return None, True                  # longer than such answers usually are: no guess
        return max(0.0, (chars - r.chars) / pace), False

    def snapshot(self) -> dict[str, Any]:
        now = _now()
        with self._lock:
            reqs = list(self._reqs.values())
            order = {"retry": 0, "write": 1, "think": 2, "wait": 3, "send": 4}
            lead = min(reqs, key=lambda r: (order[r.phase], -r.begun)) if reqs else None
            phase = lead.phase if lead else self._phase
            out: dict[str, Any] = {"ai": self.ai, "where": self.where, "phase": phase, "since": round(self.since, 1),
                                   "at": round(now, 1), "busy": len(reqs)}
            if self._stage:
                out["stage"] = dict(self._stage)
            if self._read is not None:
                out["read"] = self._read
            if self._lines and phase in ("think", "write", "wait", "retry"):
                out["lines"] = list(self._lines)
            if lead is not None:
                out["step_at"] = round(lead.retry["until"] if phase == "retry" and lead.retry else
                                       lead.first_at if phase == "write" else lead.think_at if phase == "think" else
                                       lead.up_at if phase == "wait" else lead.begun, 1)
                if lead.size:
                    out["size"] = lead.size
                if lead.pictures:
                    out["pictures"] = lead.pictures
                if lead.up and phase == "send":
                    out["up"] = [lead.up, lead.size]
                if lead.retry and phase == "retry":
                    out["retry"] = dict(lead.retry)
                from .ai import chars_per_token

                per = chars_per_token(lead.provider or self.provider) if (lead.provider or self.provider) else 3.5
                written = sum(r.tokens or int(r.chars / per) for r in reqs)
                if written:
                    out["tokens"] = written
                if lead.cap and lead.tokens and phase == "write" and now - lead.first_at >= MIN_WRITE_S:
                    # the offline model stops at its cap: at the pace it writes, the most it can still take
                    out["most"] = round(max(0, lead.cap - lead.tokens) / max(0.1, lead.tokens / (now - lead.first_at)))
            else:
                out["step_at"] = round(self._phase_at, 1)
            # the time left: this step's requests still to come at this run's own pace, else the requests in flight
            left: Optional[float] = None
            slow = False
            if self._units and self._ended and reqs:
                done, total, par = self._units
                avg = sum(self._ended) / len(self._ended)
                own = [self._eta(r, now) for r in reqs]
                now_left = max((x for x, _ in own if x is not None), default=avg * 0.5)
                waiting = max(0, total - done - len(reqs))
                left = now_left + avg * waiting / par
            elif reqs:
                etas = [self._eta(r, now) for r in reqs]
                if all(x is not None for x, _ in etas):
                    left = max(x for x, _ in etas)            # (parallel requests: the last one to end)
                slow = any(s for _, s in etas)
            if left is not None:
                out["eta"] = round(left)
            if slow:
                out["slow"] = True
            if self._units:
                out["units"] = list(self._units[:2])
        return out

    def _tell(self, now_: bool) -> None:
        if self._emit is None:
            return
        t0 = time.monotonic()
        with self._lock:
            if not now_ and t0 - self._said < EMIT_EVERY_S:
                return
            self._said = t0
        try:
            self._emit(self.snapshot())
        except Exception:  # noqa: BLE001 - the page's copy only: the run goes on
            log.debug("telling the page of a wait failed", exc_info=True)


class _Null:
    """No wait to tell (a request made outside a run the student waits for): every call does nothing."""

    def __getattr__(self, name: str) -> Callable[..., None]:
        return lambda *a, **k: None

    def __bool__(self) -> bool:
        return False


NULL = _Null()
_waits: "weakref.WeakKeyDictionary[threading.Event, Wait]" = weakref.WeakKeyDictionary()
_reg = threading.Lock()


def of(cancel: Optional[threading.Event]) -> Any:
    """The wait the requests made with ``cancel`` report to (NULL: none)."""
    if cancel is None:
        return NULL
    with _reg:
        return _waits.get(cancel) or NULL


def running() -> list[str]:
    """What the AI runs going on now make ("chat", "notes", "pack"…; "" for one that doesn't say): closing the window
    stops them, so the app asks first (app.py)."""
    with _reg:
        return [str(getattr(w, "where", "") or "") for w in list(_waits.values())]


_awake: Any = None            # one "keep the PC awake" request (osutil.KeepAwake) for all the runs going on
_awake_n = 0
_awake_lock = threading.Lock()


def _stay_awake(on: bool) -> None:
    """While at least one run goes on, Windows' idle timer doesn't put the PC to sleep: a study pack takes half an
    hour, and a laptop left alone meanwhile would wake up to a run that "took too long"."""
    global _awake, _awake_n
    with _awake_lock:
        _awake_n = max(0, _awake_n + (1 if on else -1))
        try:
            if _awake is None:
                from .osutil import KeepAwake

                _awake = KeepAwake("IEUStudia is working with the AI")
            (_awake.on if _awake_n else _awake.off)()
        except Exception:  # noqa: BLE001 - the run goes on without it
            log.debug("keeping the PC awake failed", exc_info=True)


@contextlib.contextmanager
def tracking(cancel: threading.Event, wait: Wait) -> Iterator[Wait]:
    """Requests made with ``cancel`` report to ``wait`` while this lasts (and the PC stays awake)."""
    with _reg:
        _waits[cancel] = wait
    _stay_awake(True)
    try:
        yield wait
    finally:
        _stay_awake(False)
        with _reg:
            if _waits.get(cancel) is wait:
                del _waits[cancel]
