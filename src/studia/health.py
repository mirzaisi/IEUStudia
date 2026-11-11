"""What IEUStudia knows about the services it reads but doesn't control: IEULearn and Panopto (pages and answers made
for their own website), ChatGPT (OpenAI's sign-in and API), the Claude Code and Codex CLIs (their output), YouTube
(through yt-dlp), Hugging Face (model downloads) and GitHub (new versions).

Each of them can change without warning. A service that is down or slow is said where it happens and tried again
later. One that answers in a form IEUStudia doesn't know (a page without the parts the parser needs, JSON without its
fields, an endpoint that answers 404, a CLI whose output no longer parses) *changed*: trying again won't help, a new
IEUStudia will. The code that reads a service says so here (:func:`changed`, and :func:`ok` once it reads fine), keeps
what it had (nothing is deleted because a parse came back empty) and raises the :class:`ServiceChanged` it gets back.

The state of every part of every service is kept in ``services.json`` in the app folder (shared by the app and the
tray helper, and by every profile: the services are the same for all). Settings → Services shows it, Home warns once
per change, and Copy details gives the developer what they need, with nothing personal in it. A change seen by an
older version of IEUStudia counts as unknown after an update (the new version may read it fine: it is checked anew).

A part read for each course (a course's weekly plan, its announcements, its way into Panopto) is noted with the course
it was read for (``where``): one course's page that changed stays a change while the other courses' pages read fine,
and it is over when that course's page reads again (or when it hasn't been seen for SUBJECT_DAYS: the course left the
dashboard). A change keeps its id meanwhile, so Hide hides it and the helper tells about it once.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import platform
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from . import __version__
from .i18n import N_, t

log = logging.getLogger("studia")

# the services, in the order Settings lists them, by the names students know them by
SERVICES = (("ieulearn", "IEULearn"), ("panopto", "Panopto"), ("openai", "ChatGPT"), ("claude", "Claude Code"),
            ("codex", "Codex CLI"), ("youtube", "YouTube"), ("huggingface", "Hugging Face"), ("github", "GitHub"))
NAMES = dict(SERVICES)

# what doesn't work while a part of a service is changed (English: t() it to show it)
WHAT = {
    "ieulearn.signin": N_("IEUStudia can't sign you in to IEULearn right now."),
    "ieulearn.courses": N_("IEUStudia can't read your course list from IEULearn right now."),
    "ieulearn.course_page": N_("IEUStudia can't read the courses' learning outcomes and weekly topics right now."),
    "ieulearn.unit_plan": N_("IEUStudia can't read new course files from IEULearn right now."),
    "ieulearn.announcements": N_("IEUStudia can't read new announcements from IEULearn right now."),
    "ieulearn.assignments": N_("IEUStudia can't read the times assignments are due from IEULearn right now."),
    "panopto.lti": N_("IEUStudia can't open Panopto through IEULearn right now, so new recordings can't be looked for "
                      "or downloaded."),
    "panopto.signin": N_("IEUStudia can't check its Panopto sign-in right now, so new recordings can't be looked for "
                         "or downloaded."),
    "panopto.sessions": N_("IEUStudia can't look for new recordings on Panopto right now."),
    "panopto.delivery": N_("IEUStudia can't download lectures from Panopto right now."),
    "panopto.stream": N_("IEUStudia can't download the sound or the slides of lectures from Panopto right now."),
    "openai.signin": N_("IEUStudia can't sign you in to ChatGPT right now."),
    "openai.models": N_("IEUStudia can't list your ChatGPT models right now."),
    "openai.answers": N_("IEUStudia can't read ChatGPT's answers right now."),
    "claude.answers": N_("IEUStudia can't read Claude's answers right now."),
    "claude.signin": N_("IEUStudia can't tell whether Claude Code is signed in right now."),
    "codex.answers": N_("IEUStudia can't read the Codex CLI's answers right now."),
    "youtube.videos": N_("IEUStudia can't find or read YouTube videos for study plans right now: the part of IEUStudia "
                         "that reads YouTube needs an update."),
    "huggingface.models": N_("IEUStudia can't download speech or offline AI models right now."),
    "github.releases": N_("IEUStudia can't look for its new versions right now."),
}
ISSUES = "https://github.com/mirzaisi/IEUStudia/issues"
RELEASES = "https://github.com/mirzaisi/IEUStudia/releases/latest"
SEEN_EVERY_S = 600          # a state that stays the same is written again (its "seen" time) at most this often
SUBJECTS = 40               # the courses (or folders) a part keeps as changed, at most
SUBJECT_DAYS = 3            # one not seen changed for this long is let go (a course that left the dashboard)
RANK = {"unknown": 0, "ok": 1, "down": 2, "changed": 3}

_lock = threading.RLock()
_cache: dict[str, Any] = {"path": None, "mtime": None, "data": {}}
_heard: dict[str, str] = {}      # each part's last answer in this process (the file's "seen": at most every SEEN_EVERY_S)
on_change: Callable[[], None] = lambda: None     # the app: send the new state to the page


class ServiceChanged(RuntimeError):
    """A service answered, but not in a form IEUStudia knows: it changed (a new IEUStudia is needed), as opposed to
    being down or slow. ``str()`` says it to the student; ``service`` and ``part`` say what changed. The pages tell it
    by ``kind``."""

    kind = "changed"

    def __init__(self, service: str, part: str, message: str = "") -> None:
        super().__init__(message or said(service, part))
        self.service, self.part = service, part

    def __reduce__(self) -> tuple[Any, ...]:
        """copy.copy() and pickling make it again from its service, part and words (a sign-in that failed a moment
        ago is raised again as a copy: sync.SyncService.ensure_login)."""
        return type(self), (self.service, self.part, str(self)), dict(self.__dict__)


# ----------------------------------------------------------------------------- words
def what(service: str, part: str) -> str:
    """What doesn't work while ``part`` of ``service`` is changed, in the language shown."""
    key = f"{service}.{part}"
    return t(WHAT[key]) if key in WHAT else t("Part of what IEUStudia does with {service} doesn't work right now.",
                                               service=NAMES.get(service, service))


def said(service: str, part: str) -> str:
    """The whole message: which service changed, what doesn't work, that everything on this PC is safe, what to do."""
    return " ".join((t("{service} changed something on its side.", service=NAMES.get(service, service)),
                     what(service, part), safe_text(), todo_text(service)))


def safe_text() -> str:
    return t("Everything on this PC is safe: IEUStudia keeps what it had and deletes nothing.")


def todo_text(service: str = "") -> str:
    if service == "github":          # (the update check is what doesn't work: the way to the new version is the page)
        return t("Download the newest IEUStudia from its page on GitHub ({url}) and install it over this one: your "
                 "library stays. If there's none yet, the developer can fix it: Settings → Services → Copy details "
                 "gives them what they need, with nothing personal in it.", url=RELEASES)
    return t("Check for an IEUStudia update in Settings → About. If there's none yet, the developer can fix it: "
             "Settings → Services → Copy details gives them what they need, with nothing personal in it.")


# ----------------------------------------------------------------------------- what is known (services.json)
def _path() -> Path:
    from .paths import app_dir

    return app_dir() / "services.json"


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _age_s(stamp: str) -> float:
    try:
        return (dt.datetime.now() - dt.datetime.fromisoformat(stamp)).total_seconds()
    except (TypeError, ValueError):
        return float("inf")


def _read() -> dict[str, Any]:
    """The state, read again whenever the file changed (the tray helper writes it too)."""
    path = _path()
    try:
        mtime: Optional[float] = path.stat().st_mtime
    except OSError:
        mtime = None
    if _cache["path"] != path or _cache["mtime"] != mtime:
        if _cache["path"] != path:
            _heard.clear()                   # (another app folder: another state)
        data: Any = {}
        if mtime is not None:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
        if not isinstance(data, dict):
            data = {}
        if not isinstance(data.get("parts"), dict):
            data["parts"] = {}
        if not isinstance(data.get("told"), dict):
            data["told"] = {}
        _cache.update(path=path, mtime=mtime, data=data)
    return _cache["data"]


def _write(data: dict[str, Any]) -> None:
    path = _path()
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    for attempt in range(3):          # (Windows: the other process may be reading it this very moment)
        try:
            tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
            _cache.update(path=path, mtime=path.stat().st_mtime, data=data)
            return
        except OSError:
            time.sleep(0.05 * (attempt + 1))
    log.debug("Could not save %s", path, exc_info=True)
    try:
        tmp.unlink(missing_ok=True)
    except OSError:
        pass


_SCRUB = (
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "<email>"),
    (re.compile(r"(?i)bearer\s+\S+|\beyJ[\w-]{8,}\.[\w-]+\.[\w-]*|\b(?:sk|rt|at)-[\w-]{12,}"), "<token>"),
    (re.compile(r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"), "<id>"),
    (re.compile(r"(https?://[^\s?#'\"]+)[?#][^\s'\"]*"), r"\1"),
    (re.compile(r"\d{5,}"), "#"),
)


def scrub(text: str) -> str:
    """Nothing personal in what the developer is sent: no email address, token, id, query string or long number (a
    student number, a course's IEULearn number)."""
    out = " ".join(str(text or "").split())
    for rx, repl in _SCRUB:
        out = rx.sub(repl, out)
    return out[:300]


def _subjects(entry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The courses (or folders) a changed part is changed for, without those not seen so for SUBJECT_DAYS."""
    got = entry.get("where") if isinstance(entry.get("where"), dict) else {}
    return {str(k): v for k, v in got.items()
            if isinstance(v, dict) and _age_s(str(v.get("seen") or "")) < SUBJECT_DAYS * 86400}


def _record(service: str, part: str, state: str, detail: str = "", where: str = "") -> bool:
    """Note what ``part`` of ``service`` did just now (``where``: for which course or folder, when the part is read
    for each); True when that is news (its state changed)."""
    key = f"{service}.{part}"
    now = _now()
    where = str(where or "")
    with _lock:
        data = _read()
        _heard[key] = now
        parts = data["parts"]
        old = parts.get(key) if isinstance(parts.get(key), dict) else {}
        current = old.get("version") == __version__
        if old.get("state") == "changed" and current and state != "changed":
            if state == "down":
                return False              # a change stays said until it works again
            # read fine: over for that course (or, without one, for the part as a whole); a change of another
            # course's page stays (it is the same change: its id and its Hide stay too)
            subs = _subjects(old)
            whole = bool(old.get("whole", not old.get("where")))
            if where:
                subs.pop(where, None)
            else:
                whole = False
            if whole or subs:
                if subs != old.get("where") or whole != old.get("whole"):
                    old.update(where=subs, whole=whole)
                    _write(data)
                return False
        elif old.get("state") == state and current:
            if detail and state != "ok":
                old["detail"] = scrub(detail)
            old["count"] = int(old.get("count") or 0) + 1
            fresh = False
            if state == "changed":        # another course (or the part as a whole) changed too: the same change
                subs = _subjects(old)
                if where:
                    fresh = where not in subs
                    subs[where] = {"seen": now}
                    subs = dict(sorted(subs.items(), key=lambda kv: str(kv[1].get("seen") or ""))[-SUBJECTS:])
                else:
                    fresh = not old.get("whole", not old.get("where"))
                old.update(where=subs, whole=bool(old.get("whole", not old.get("where"))) or not where)
            if fresh or _age_s(str(old.get("seen") or "")) >= SEEN_EVERY_S:
                old["seen"] = now
                _write(data)
            return False
        parts[key] = {"state": state, "since": now, "seen": now, "count": 1, "detail": scrub(detail) if state != "ok" else "",
                      "version": __version__, "id": os.urandom(4).hex()}      # (id: this change, told once)
        if state == "changed":
            parts[key].update(where={where: {"seen": now}} if where else {}, whole=not where)
        _write(data)
    if state == "changed":
        log.warning("%s changed (%s): %s", NAMES.get(service, service), part, scrub(detail))
    elif old.get("state") == "changed":
        log.info("%s (%s) reads as IEUStudia expects again", NAMES.get(service, service), part)
    try:
        on_change()
    except Exception:  # noqa: BLE001 - only the page's copy of it
        log.debug("health listener failed", exc_info=True)
    return True


def ok(service: str, part: str, where: str = "") -> None:
    """``part`` of ``service`` answered as IEUStudia expects (a change said before is over; ``where``: for that course
    or folder only)."""
    _record(service, part, "ok", where=where)


def down(service: str, part: str, detail: str = "") -> None:
    """``part`` of ``service`` didn't answer, or answered with a server error: down or busy, not changed."""
    _record(service, part, "down", detail)


def changed(service: str, part: str, detail: str = "", message: str = "",
            cls: type[ServiceChanged] = ServiceChanged, where: str = "") -> ServiceChanged:
    """``part`` of ``service`` answered in a form IEUStudia doesn't know: noted (``detail``: what exactly, for the
    developer; it is scrubbed of anything personal) and returned, to raise (as ``cls``). ``message``: what the
    student reads, when the place it happens has better words than :func:`said`. ``where``: the course (or folder)
    the part was read for, when it is read for each: then only that one's page changed."""
    _record(service, part, "changed", detail, where)
    return cls(service, part, message)


def _changed_now(p: dict[str, Any]) -> bool:
    """A part this version of IEUStudia saw changed, and still so: as a whole, or for a course seen so lately."""
    return (p.get("state") == "changed" and p.get("version") == __version__
            and (bool(p.get("whole", not p.get("where"))) or bool(_subjects(p))))


def is_changed(service: str, part: str = "") -> bool:
    """Is ``part`` of ``service`` (any part, without one) changed, as this version of IEUStudia saw it?"""
    with _lock:
        parts = _read()["parts"]
    return any(_changed_now(p) for k, p in parts.items()
               if isinstance(p, dict) and (k == f"{service}.{part}" if part else k.startswith(f"{service}.")))


# ----------------------------------------------------------------------------- what the page and the helper show
def snapshot() -> dict[str, Any]:
    """Every service, in Settings' order: {"id", "name", "state" (ok, down, changed, unknown: never asked yet), "since"
    (of a change), "seen" (last answer), "parts": [{"part", "state", "since", "seen", "what"}], "message", and for one
    that changed the words Home shows ("title", "whats", "safe", "todo") and "key" (this change: Hide hides it once)}
    and the changed ones' ids (``changed``)."""
    with _lock:
        parts = dict(_read()["parts"])
        heard = dict(_heard)
    out = []
    for sid, name in SERVICES:
        mine = []
        for key, p in sorted(parts.items()):
            if not key.startswith(sid + ".") or not isinstance(p, dict):
                continue
            state = str(p.get("state") or "unknown")
            if state != "ok" and p.get("version") != __version__:
                state = "unknown"            # seen by an older IEUStudia: looked at anew
            part = key.split(".", 1)[1]
            subs = sorted(_subjects(p)) if state == "changed" else []
            whole = state == "changed" and bool(p.get("whole", not p.get("where")))
            if state == "changed" and not whole and not subs:
                state = "ok"                     # the courses it changed for weren't seen for days: let go
            mine.append({"part": part, "state": state, "since": str(p.get("since") or ""),
                         "seen": max(str(p.get("seen") or ""), heard.get(key, "")),       # (the latest answer)
                         "what": what(sid, part) if state == "changed" else "",
                         "id": str(p.get("id") or p.get("since") or ""),
                         # (the courses its page changed for; "whole": for every course, or not read per course)
                         "where": subs, "whole": whole})
        state = max((p["state"] for p in mine), key=lambda s: RANK.get(s, 0), default="unknown")
        hit = [p for p in mine if p["state"] == state]
        bad = [p for p in mine if p["state"] == "changed"]
        entry: dict[str, Any] = {"id": sid, "name": name, "state": state, "parts": mine,
                                 "since": min((p["since"] for p in hit), default=""),
                                 "seen": max((p["seen"] for p in mine), default=""),
                                 "message": said(sid, bad[0]["part"]) if bad else ""}
        if bad:
            entry.update(title=t("{service} changed something on its side.", service=name),
                         whats=[p["what"] for p in bad], safe=safe_text(), todo=todo_text(sid),
                         key=f"{sid}@" + "+".join(sorted(p["id"] for p in bad)))
        out.append(entry)
    return {"services": out, "changed": [s["id"] for s in out if s["state"] == "changed"], "issues": ISSUES}


def details() -> str:
    """What the developer needs to fix a change, in English and with nothing personal in it: this IEUStudia, this PC's
    system, and what each service did (its parts' states, since when, how often, and what exactly didn't match)."""
    with _lock:
        parts = dict(_read()["parts"])
    lines = [f"IEUStudia {__version__} on {platform.system()} {platform.release()} ({platform.version()})",
             f"Services as of {_now()}:"]
    for sid, name in SERVICES:
        mine = [(k.split(".", 1)[1], p) for k, p in sorted(parts.items()) if k.startswith(sid + ".") and isinstance(p, dict)]
        if not mine:
            lines.append(f"- {name}: not used yet")
            continue
        worst = max((str(p.get("state") or "") for _, p in mine), key=lambda s: RANK.get(s, 0))
        lines.append(f"- {name}: {worst}")
        for part, p in mine:
            line = (f"    {part}: {p.get('state')} since {p.get('since')}, last {p.get('seen')}, {p.get('count')}x"
                    f" (seen by {p.get('version')})")
            if p.get("state") == "changed" and (n := len(_subjects(p))):      # (how many, never which: their numbers)
                line += f", for {n} of the courses read" + (" and as a whole" if p.get("whole") else "")
            if p.get("detail"):
                line += f": {scrub(str(p['detail']))}"
            lines.append(line)
    try:                                       # Claude Code and the Codex CLI: found where, and how their install ended
        from .ai_setup import setup_report

        lines += setup_report()
    except Exception:  # noqa: BLE001 - the services' part is sent anyway
        logging.getLogger("studia").warning("The AI programs couldn't be added to the details", exc_info=True)
    return "\n".join(lines)


def news(who: str = "tray") -> list[str]:
    """The services that changed since ``who`` last told about them (each change is told once): their names. Noted
    as told."""
    with _lock:
        data = _read()
        told = data["told"].get(who) if isinstance(data["told"].get(who), list) else []
        fresh: list[str] = []
        keys: list[str] = []
        for sid, name in SERVICES:
            for key, p in sorted(data["parts"].items()):
                if (key.startswith(sid + ".") and isinstance(p, dict) and _changed_now(p)
                        and p.get("version") == __version__):
                    mark = f"{key}@{p.get('id') or p.get('since')}"
                    keys.append(mark)
                    if mark not in told and name not in fresh:
                        fresh.append(name)
        if fresh or set(told) - set(keys):
            data["told"][who] = keys
            _write(data)
    return fresh
