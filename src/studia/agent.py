"""The tray helper (``IEUStudia --agent``): a small background process, without the app window, that checks
IEULearn now and then and shows a Windows notification when something new arrives — a lecture recording of your
section, an announcement (quiz and exam news first) or, if you want, new files. Clicking the notification or the
tray icon opens IEUStudia.

It stays out of the way of the app: while the window is open the app does its own checking and the helper waits.
It never downloads materials or transcribes anything, and it uses only what the app stored (your encrypted
IEULearn login). Memory: tens of MB (no speech engine, no browser engine).
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

from .i18n import date_short, t, tn

log = logging.getLogger("studia")
WINDOWS = sys.platform == "win32"

CMD_OPEN, CMD_SYNC, CMD_PAUSE, CMD_STARTUP, CMD_QUIT = 1, 2, 3, 4, 9
FIRST_CHECK_S = 90            # after signing in to Windows, let the PC settle first
BACK_S = 50                   # after the screen comes on or the PC wakes up: Wi-Fi first
SCREEN_OFF_EVERY = 4          # while the screen is off: a check only every 4 intervals (in case "off" was wrong)
KEEP_SEEN = 3000
REMIND_FROM = dt.time(17)     # a reminder comes the evening before, from 17:00 ...
REMIND_UNTIL = dt.time(14)    # ... or on the day itself until 14:00, if the PC was off that evening


def instance_name() -> str:
    from .osutil import instance_name as ui_name

    return ui_name().replace("SingleInstance", "Helper")


def ui_running() -> bool:
    """Is the app window open (it holds its single-instance lock)?"""
    from .osutil import instance_held, instance_name as ui_name

    return instance_held(ui_name())


def running() -> bool:
    """Is the helper running?"""
    from .osutil import instance_held

    return instance_held(instance_name())


def ensure_running() -> bool:
    """Start the helper if the user wants it and it is not running (called by the app)."""
    from . import appconfig, launch

    cfg = appconfig.load()
    if cfg.tray and cfg.start_with_windows:
        launch.refresh_startup()       # (also when another copy's helper runs: the next sign-in starts this one)
    if not cfg.tray or running():
        return False
    return launch.spawn(launch.app_command("--agent"))


# ----------------------------------------------------------------------------- what to tell the user
def _seen_path() -> Path:
    from .paths import profile_dir

    return profile_dir() / "notified.json"


def _load_seen() -> dict[str, list[str]]:
    try:
        d = json.loads(_seen_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_seen(seen: dict[str, list[str]]) -> None:
    """Add to what was told: the app and the helper may both have told something since this was loaded."""
    now = _load_seen()
    merged = {k: list(dict.fromkeys(now.get(k, []) + seen.get(k, [])))[-KEEP_SEEN:] for k in set(now) | set(seen)}
    path = _seen_path()
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(merged), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def summarize(lib: Any, settings: Any, cfg: Any, new_recordings: list[str], new_announcements: list[str],
              new_materials: list[str], seen: dict[str, list[str]]) -> Optional[tuple[str, str, Any]]:
    """One notification for what a check found (or None): title, text and what clicking it opens."""
    from .alerts import LABELS, matches
    from .sections import available_sections, classify, guess_my_section

    lines: list[tuple[int, str, str]] = []            # (priority, course id, line)
    courses = {c.id: c for c in lib.courses()}
    done = {k: set(v) for k, v in seen.items()}
    if cfg.notify_recordings and new_recordings:
        by_course: dict[str, list[Any]] = {}
        for did in new_recordings:
            r = lib.get_recording(did)
            if r is None or r.duplicate_of or r.hidden or did in done.get("rec", set()):
                continue
            by_course.setdefault(r.course_id, []).append(r)
        for cid, recs in by_course.items():
            c = courses.get(cid)
            if c is None:
                continue
            all_recs = lib.recordings(cid)
            prefs = settings.prefs(cid)
            mine = classify(c, all_recs, prefs.my_section or guess_my_section(c)) if available_sections(all_recs) else {}
            own = [r for r in recs if mine.get(r.delivery_id) is not False or prefs.show_mode == "all"]
            if own:
                lines.append((2, cid, tn(len(own), "{course}: {n} new lecture recording",
                                         "{course}: {n} new lecture recordings", course=c.code)))
    if cfg.notify_announcements and new_announcements:
        fresh = set(new_announcements) - done.get("ann", set())
        for a in lib.announcements(limit=200):
            if a["id"] not in fresh:
                continue
            c = courses.get(a["course_id"])
            cats = matches(f"{a['title']}\n{a['body'] or ''}")
            key = next((k for k in ("final", "midterm", "quiz", "exam", "project", "homework", "schedule") if k in cats), "")
            prefix = f"{t(LABELS[key])} · " if key else ""              # "Quiz · ", "Midterm · " …
            lines.append((0 if key else 1, a["course_id"], f"{c.code if c else t('Course')}: {prefix}{a['title']}"))
    if cfg.notify_materials and new_materials:
        fresh = [m for m in new_materials if m not in done.get("mat", set())]
        per: dict[str, int] = {}
        for rid in fresh:
            m = lib.get_material(rid)
            if m:
                per[m["course_id"]] = per.get(m["course_id"], 0) + 1
        for cid, n in per.items():
            c = courses.get(cid)
            lines.append((3, cid, tn(n, "{course}: {n} new file on IEULearn", "{course}: {n} new files on IEULearn",
                                     course=c.code if c else t("Course"))))
    seen.setdefault("rec", []).extend(new_recordings)
    seen.setdefault("ann", []).extend(new_announcements)
    seen.setdefault("mat", []).extend(new_materials)
    if not lines:
        return None
    lines.sort(key=lambda x: x[0])
    targets = {cid for _, cid, _ in lines}
    title = t("IEUStudia: heads-up") if lines[0][0] == 0 else t("IEUStudia: new on IEULearn")
    text = "\n".join(line for _, _, line in lines[:4])
    if len(lines) > 4:
        text += "\n" + t("+{n} more", n=len(lines) - 4)
    tab = {0: "announcements", 1: "announcements", 2: "lectures", 3: "materials"}[lines[0][0]]   # what it's about
    payload = f"course:{next(iter(targets))}:{tab}" if len(targets) == 1 else "home"
    return title, text, payload


def found_by_window(lib: Any, settings: Any, new_recordings: list[str], new_announcements: list[str],
                    new_materials: list[str]) -> Optional[tuple[str, str, Any]]:
    """What a sync of the open window found, told as the helper tells what its checks find: the window's syncs merge
    the new lectures and announcements first, so the helper, once the window is closed, no longer finds them new. For
    a Windows notification while the window isn't the one in use (minimized during a long batch, say); marked told,
    so it is said once. None: nothing to tell, or background checks are off (their notify switches are off then).
    The caller keeps the app's reminders from writing the told list at the same time."""
    from . import appconfig

    cfg = appconfig.load()
    if not cfg.tray or not (new_recordings or new_announcements or new_materials):
        return None
    seen = _load_seen()
    note = summarize(lib, settings, cfg, new_recordings, new_announcements, new_materials, seen)
    _save_seen(seen)
    return note


def _day(d: dt.date) -> str:
    return date_short(d)          # Tue 13 Oct / 13 Eki Sal


def _los(los: list[int]) -> str:
    """[3, 4, 6] → "LO3–LO4, LO6"."""
    runs: list[list[int]] = []
    for n in sorted(set(los)):
        if runs and n == runs[-1][1] + 1:
            runs[-1][1] = n
        else:
            runs.append([n, n])
    return ", ".join(f"LO{a}" if a == b else f"LO{a}–LO{b}" for a, b in runs)


REMIND_MORNING = dt.time(8)  # a deadline whose time isn't known: its "hours before" reminder comes that morning


def _when(day: dt.date, today: dt.date) -> str:
    return (t("today") if day == today else t("tomorrow ({day})", day=_day(day)) if day == today + dt.timedelta(days=1)
            else t("on {day}", day=_day(day)))


def _told_that_week(told: set[str], uid: str, day: dt.date) -> bool:
    """Was this assessment told about for a day of the same week (its week first, then the day announced for it)?"""
    monday = day - dt.timedelta(days=day.weekday())
    for key in told:
        if key.startswith(uid + "@"):
            try:
                other = dt.date.fromisoformat(key[len(uid) + 1:len(uid) + 11])
            except ValueError:
                continue
            if other - dt.timedelta(days=other.weekday()) == monday:
                return True
    return False


def _assessment_due(e: dict[str, Any], now: dt.datetime, days: int, told: set[str]) -> Optional[dict[str, Any]]:
    """A quiz's or an exam's reminder, if it is due now: ``days`` before it (from 17:00), until it is over (a quiz
    announced for a week: until the last of its class days still to come; an exam week: 14:00 on its first day)."""
    if not days:
        return None
    today, hm = now.date(), f"{now:%H:%M}"
    quiz = e.get("kind") == "quiz"
    classes = list(zip(e.get("classes") or [], e.get("ends") or ["23:59"] * len(e.get("classes") or [])))
    rest: list[dt.date] = []
    if e.get("date"):                                     # an announced day (or your own entry for one)
        first = dt.date.fromisoformat(e["date"])
        until = dt.datetime.combine(first, dt.time.fromisoformat(e["time"]) if e.get("time") else REMIND_UNTIL)
        if e.get("kind") == "own":
            key = f"{e['uid']}@{e['date']}" + (f"T{e['time']}" if e.get("time") else "")
        elif quiz or not e.get("week"):
            key = f"{e['uid']}@{e['date']}"
        else:
            key = f"{e.get('course_id') or e['course']}-exam-week{e['week']}"
        if key in told or (quiz and _told_that_week(told, e["uid"], first)):
            return None
    else:
        if quiz and classes:
            left = [(d, end) for d, end in classes if d > today.isoformat() or (d == today.isoformat() and end > hm)]
            if not left:                                  # all its class hours are over: it's done
                return None
            first = dt.date.fromisoformat(left[0][0])
            until = dt.datetime.combine(first, dt.time.fromisoformat(left[0][1]))
            rest = [dt.date.fromisoformat(d) for d, _ in left[1:]]
        else:
            first = dt.date.fromisoformat(e["start"])
            until = dt.datetime.combine(first, REMIND_UNTIL)
        # a quiz moved to another week is said again; an exam week once, even when an announcement replaces the
        # syllabus's word for it
        key = f"{e['uid']}@{e['start']}" if quiz else f"{e.get('course_id') or e['course']}-exam-week{e['week']}"
        if key in told:
            return None
    if not dt.datetime.combine(first - dt.timedelta(days=days), REMIND_FROM) <= now <= until:
        return None
    return {"keys": [key], "e": e, "first": first, "rest": rest, "n": 0 if e.get("date") else len(classes)}


def _deadline_due(e: dict[str, Any], now: dt.datetime, prefs: dict[str, int], told: set[str]) -> Optional[dict[str, Any]]:
    """An assignment's or your own entry's reminder, if one is due now: ``due_days`` before it and ``due_hours``
    before it (without a time: from 17:00 that many days before, and on the morning of the day), each once; when
    both are due at once, one notification says it."""
    day = dt.date.fromisoformat(e["date"])
    at = dt.datetime.combine(day, dt.time.fromisoformat(e["time"])) if e.get("time") else None
    end = at or dt.datetime.combine(day, dt.time(23, 59))
    if now > end:
        return None
    base = f"{e['uid']}@{e['date']}" + (f"T{e['time']}" if e.get("time") else "")
    slots = []                                            # (key, opens)
    if prefs.get("due_days"):
        n = prefs["due_days"]
        slots.append((base + "#1", at - dt.timedelta(days=n) if at else
                      dt.datetime.combine(day - dt.timedelta(days=n), REMIND_FROM)))
    if prefs.get("due_hours"):
        slots.append((base + "#2", at - dt.timedelta(hours=prefs["due_hours"]) if at else
                      dt.datetime.combine(day, REMIND_MORNING)))
    open_now = [(key, opens) for key, opens in slots if opens <= now]
    if not open_now or open_now[-1][0] in told:           # (the later one told: the earlier one is past too)
        return None
    return {"keys": [key for key, _ in open_now], "e": e, "first": day, "rest": [], "n": 0}


def _line(r: dict[str, Any], today: dt.date) -> str:
    e, first, rest, n = r["e"], r["first"], r["rest"], r["n"]
    kind = e.get("kind")
    what = {"course": e["course"], "name": e["name"], "when": _when(first, today)}
    if kind in ("assignment", "own") and not e.get("exam_like"):
        if kind == "assignment":
            return (t("{course}: {name} is due {when} at {time}.", time=e["time"], **what) if e.get("time")
                    else t("{course}: {name} is due {when}. IEULearn has the time.", **what))
        if not e["course"]:
            return (t("{name}: {when} at {time}.", time=e["time"], **what) if e.get("time")
                    else t("{name}: {when}.", **what))
        return (t("{course}: {name}, {when} at {time}.", time=e["time"], **what) if e.get("time")
                else t("{course}: {name}, {when}.", **what))
    los = " " + t("Covers {los}.", los=_los(e["los"])) if e.get("los") else ""
    if kind == "own":                                     # your entry for a quiz or an exam
        return (t("{name}: {when} at {time}.", time=e["time"], **what) if not e["course"] and e.get("time")
                else t("{name}: {when}.", **what) if not e["course"]
                else t("{course}: {name}, {when} at {time}.", time=e["time"], **what) if e.get("time")
                else t("{course}: {name}, {when}.", **what))
    if e.get("date"):                                     # the day it was announced for
        line = (t("{course}: {name} is {when} at {time}, as announced.", time=e["time"], **what) if e.get("time")
                else t("{course}: {name} is {when}, as announced.", **what))
    elif kind == "quiz" and n:                            # one of its week's class days (rest: another one)
        line = (t("{course}: {name} {when}, as announced.", **what) if n == 1 else
                t("{course}: {name} may be {when} or {day}, as announced.", day=_day(rest[0]), **what) if rest
                else t("{course}: {name} may be {when}, as announced.", **what))
    elif kind == "quiz":
        line = t("{course}: {name} is in week {week}, which starts {when}.", week=e["week"], **what)
    else:
        line = t("{course}: {name} is in week {week}, which starts {when}. The exam schedule on IEULearn has the "
                 "day.", week=e["week"], **what)
    return line + los


def reminders(events: list[dict[str, Any]], now: dt.datetime, seen: dict[str, list[str]],
              prefs: Optional[dict[str, int]] = None) -> Optional[tuple[str, str, str]]:
    """What to remind you of now, each once (``seen["rem"]``), as the Calendar page set it (``prefs``:
    agenda.reminder_prefs; its defaults without): a quiz or an exam ``exams`` days before (from 17:00; a quiz
    announced for a week: before the first of its week's class days still to come, until that class is over; a
    midterm or final announced for a week: before its week, since the exam schedule gives its day), an assignment
    and your own entry ``due_days`` days and ``due_hours`` hours before. One notification (title, text, what
    clicking it opens) or None. ``events``: calendar_export.all_events (or only its assessment_events)."""
    from .agenda import REMIND_DEFAULTS

    prefs = {**REMIND_DEFAULTS, **(prefs or {})}
    told = set(seen.get("rem", []))
    today = now.date()
    due = []
    for e in events:
        if e.get("kind") in ("assignment", "own") and not e.get("exam_like"):
            r = _deadline_due(e, now, prefs, told)
        else:
            r = _assessment_due(e, now, prefs.get("exams", 0), told)
        if r:
            due.append(r)
    if not due:
        return None
    seen.setdefault("rem", []).extend(key for r in due for key in r["keys"])
    lines = [_line(r, today) for r in due]
    first, e = due[0]["first"], due[0]["e"]
    kinds = {r["e"].get("kind") for r in due}
    title = t("IEUStudia: coming up")
    if len(due) == 1 and e.get("kind") == "quiz" and due[0]["n"] == 1:
        title = (t("IEUStudia: {name} today", name=e["name"]) if first == today else
                 t("IEUStudia: {name} tomorrow", name=e["name"]))
    elif kinds == {"assignment"}:
        title = t("IEUStudia: a deadline is coming up") if len(due) == 1 else t("IEUStudia: deadlines coming up")
    elif kinds == {"own"}:
        title = t("IEUStudia: from your calendar")
    payload = "home"
    if kinds & {"assignment", "own"}:                     # the Calendar page has it (and how sure it is)
        payload = f"calendar:{e['uid']}" if len(due) == 1 else "calendar"
    elif len(due) == 1 and e.get("course_id"):           # Get ready knows what was announced; a syllabus week: the week
        payload = (f"week:{e['course_id']}:{e['week']}" if "-syllabus-week" in e["uid"]
                   else f"ready:{e['course_id']}:{e['name']}")
    return title, "\n".join(lines[:4]) + ("\n" + t("+{n} more", n=len(lines) - 4) if len(lines) > 4 else ""), payload


def changed_note() -> Optional[tuple[str, str, str]]:
    """A notification for the services that changed on their side since the helper last told (each change once):
    (title, text, what a click opens), or None."""
    from . import health
    from .i18n import join

    try:
        fresh = health.news("tray")
    except Exception:  # noqa: BLE001 - never a reason for a check to fail
        log.debug("Helper: the services' state can't be read", exc_info=True)
        return None
    if not fresh:
        return None
    return (t("{service} changed something on its side", service=join(fresh)),
            t("Open IEUStudia to see what doesn't work right now. Everything on this PC is safe."), "home")


def next_check(now: float, last: float, interval_s: float, screen_on: bool = True, back_at: float = 0.0,
               manual: bool = False, paused: bool = False) -> Optional[float]:
    """Seconds until the helper checks IEULearn (0: now; None: not while notifications are paused, then the first
    check after them tells what came in meanwhile). Every ``interval_s`` while the screen is on; while it is off
    nobody reads a notification, so only every SCREEN_OFF_EVERY intervals. When the screen comes on or the PC wakes
    up (``back_at``) after a check was due, BACK_S later. ``manual``: "Check IEULearn now". The times are the wall
    clock's (time.time()): Windows' waits don't count the hours the PC slept."""
    if manual:
        return 0.0
    if paused:
        return None
    last = min(last, now)                                      # the clock was set back
    due = last + interval_s * (1 if screen_on else SCREEN_OFF_EVERY)
    if back_at > last:
        due = max(due, back_at + BACK_S)
    return max(0.0, due - now)


# ----------------------------------------------------------------------------- the helper
class Helper:
    def __init__(self, at_login: bool) -> None:
        from . import appconfig

        self.cfg = appconfig.load()
        self.paused = False
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.first_delay = FIRST_CHECK_S if at_login else 20
        self.last = ""
        self.checked_at = 0.0            # when the last check ran (wall clock)
        self.manual = False              # "Check IEULearn now"
        self.screen_on = True
        self.back_at = 0.0               # when the screen last came on or the PC woke up (wall clock)
        self.told_rejected = False       # said once that IEULearn refused the saved password
        self.untold: list[str] = []      # new recordings found by a check that then failed (see _check)
        self.asking = False              # "Turn off background checks" asks first; the question is showing
        from .osutil import Tray

        self.tray = Tray("IEUStudia", _icon(), self._menu, self._command, self._open, on_power=self._power)

    # ---- tray
    def _menu(self) -> list[tuple[int, str, bool, bool]]:
        from . import i18n, launch

        i18n.load_language()             # Settings may have changed it, and no check reads it while the window is open
        return [(CMD_OPEN, t("Open IEUStudia"), False, True),          # built each time the menu opens: the language
                (CMD_SYNC, t("Check IEULearn now"), False, not ui_running()),    # set in Settings applies at once
                (0, "", False, False),
                (CMD_PAUSE, t("Pause notifications"), self.paused, True),
                (CMD_STARTUP, t("Start with Windows") if WINDOWS else t("Start at login"), launch.startup_enabled(),
                 True),
                (0, "", False, False),
                (CMD_QUIT, t("Turn off background checks and notifications…"), False, True)]

    def _command(self, cmd: int) -> None:
        from . import appconfig, launch

        if cmd == CMD_OPEN:
            self._open(None)
        elif cmd == CMD_SYNC:
            self.manual = True
            self.wake.set()
        elif cmd == CMD_PAUSE:
            self.paused = not self.paused
            self.wake.set()              # no checks while paused; afterwards at once if one is due
        elif cmd == CMD_STARTUP:
            on = not launch.startup_enabled()
            launch.set_startup(on)
            appconfig.update({"start_with_windows": on})
        elif cmd == CMD_QUIT:            # off until it's turned on again in Settings, not just until the app opens
            # it sat right under "Start with Windows", and a misclick turned it off for days: ask first (once, however
            # often the menu is opened again while the question shows)
            if getattr(self, "asking", False):
                return
            self.asking = True
            try:
                if not confirm_stop():
                    return
            finally:
                self.asking = False
            log.info("Helper: background checks turned off from the tray menu")
            appconfig.update({"tray": False, "start_with_windows": False})
            launch.set_startup(False)
            self.stop.set()
            self.wake.set()
            self.tray.stop()

    def _open(self, payload: Optional[Any]) -> None:
        from . import launch

        target = payload if isinstance(payload, str) and payload else ""
        launch.spawn(launch.app_command(*(["--open", target] if target else [])))

    def _power(self, event: str) -> None:
        """From the tray window: the screen went "off" or came "on", or the PC woke up ("resume")."""
        if event in ("on", "off"):
            was_on, self.screen_on = self.screen_on, event == "on"
            if not self.screen_on or was_on:
                return
        self.back_at = time.time()
        self.wake.set()                                  # (a wait doesn't count the time the PC slept)

    # ---- work
    def _loop(self) -> None:
        from . import appconfig
        from .osutil import background_work

        self.wake.wait(self.first_delay)
        while not self.stop.is_set():
            self.wake.clear()
            self.cfg = appconfig.load()
            if not self.cfg.tray:                       # turned off in the app: end
                log.info("Helper: background mode was turned off")
                self.tray.stop()
                return
            interval = self.cfg.sync_minutes * 60
            manual, self.manual = self.manual, False
            wait = next_check(time.time(), self.checked_at, interval, self.screen_on, self.back_at, manual,
                              self.paused)
            if wait == 0 and not ui_running():
                self.checked_at = time.time()
                told = False
                with background_work():                 # the programs you use come first
                    try:
                        told = self._check()
                    except Exception:  # noqa: BLE001
                        log.exception("Helper: check failed")
                    if not told:        # one notification at a time: a click opens what the last one was about
                        self._check_update()
                wait = next_check(time.time(), self.checked_at, interval, self.screen_on, self.back_at)
            # at least once an interval: Settings may have turned the helper off (or the window is open: it checks)
            self.wake.wait(interval if wait is None or wait == 0 else min(wait, interval))

    def _check_update(self) -> None:
        """A new version of IEUStudia: said once, and opening the app shows how to update. With the look, at most once
        a day, the anonymous count of copies in use (updates.count_in)."""
        from . import updates

        try:
            if not self.cfg.check_updates or not updates.installed():
                return
            st = updates.check()
            updates.count_in()
            if st["available"] and not self.paused and updates.tell_once(st["version"]):
                self.tray.notify(t("IEUStudia {version} is available", version=st["version"]),
                                 t("Open IEUStudia to update. It takes about a minute."), "update")
        except Exception:  # noqa: BLE001
            log.debug("Helper: update check failed", exc_info=True)

    def _check(self) -> bool:
        """Check IEULearn; True if a notification was shown."""
        from . import __version__, i18n

        i18n.load_language()             # Settings may have changed it since the last check
        from .accounts import activate
        from .config import load_settings
        from .library import Library
        from .paths import db_path
        from .sync import SyncService

        activate()
        settings = load_settings()
        lib = Library(db_path(), app_version=__version__)
        try:
            from . import secrets_store

            seen = _load_seen()
            known = {k: set(v) for k, v in seen.items()}
            note = None
            sync = SyncService(lib, lambda: settings, log)
            if not sync.has_credentials():
                if secrets_store.rejected():
                    self.tray.set_tooltip(t("IEUStudia · IEULearn didn't accept your saved password: open IEUStudia "
                                            "to sign in again"))
                    if not self.told_rejected:
                        self.told_rejected = True
                        self.tray.notify(t("IEULearn didn't accept your saved password"),
                                         t("Did you change it? Open IEUStudia and sign in again; until then it can't "
                                           "check for new lectures."))
                        return True
                else:
                    self.tray.set_tooltip(t("IEUStudia · sign in in the app to get notifications"))
            else:
                self.told_rejected = False
                # new recordings merged by a check that then failed (IEULearn down, a changed password): told about
                # at the next good check, as the library no longer calls them new
                waiting = list(self.untold)

                def found(ids: list[str]) -> None:
                    self.untold = list(dict.fromkeys(waiting + ids))
                try:
                    # probes, and a full listing now and then; the courses from the small list, not the dashboard
                    res = sync.sync(full_every=sync.HELPER_PANOPTO_EVERY, dashboard=False, partial=found)
                    new_ids = list(dict.fromkeys(waiting + res.new_ids))
                    found(new_ids)
                    content = sync.sync_content(units_every=None if self.cfg.notify_materials else sync.HELPER_UNITS_EVERY)
                    note = summarize(lib, settings, self.cfg, new_ids, content["new_announcements"],
                                     content["new_materials"], seen)
                    self.untold = []
                    self.last = time.strftime("%H:%M")
                    self.tray.set_tooltip(t("IEUStudia · checked {time}", time=self.last))
                    log.info("Helper: checked IEULearn (%d new recordings, %d announcements, %d files)",
                             len(new_ids), len(content["new_announcements"]), len(content["new_materials"]))
                except Exception:  # noqa: BLE001 - IEULearn can't be reached: the reminders still come
                    log.warning("Helper: check failed", exc_info=True)
            if note is None and self.cfg.notify_reminders and not self.paused:     # one notification at a time
                from .agenda import reminder_prefs
                from .calendar_export import all_events

                told = _load_seen().get("rem", [])      # the app may have reminded meanwhile (the check takes a while)
                seen["rem"] = list(dict.fromkeys(told + seen.get("rem", [])))
                note = reminders(all_events(lib, settings), dt.datetime.now(), seen, reminder_prefs(lib))
            if note is None and not self.paused:     # a service that changed on its side: said once, while the window
                note = changed_note()                # is closed (notifications may not come until an update)
            if any(set(v) - known.get(k, set()) for k, v in seen.items()):     # (nothing new: nothing to write)
                _save_seen(seen)
            told = bool(note) and not self.paused
            if told:
                self.tray.notify(*note)
            return told
        finally:
            lib.close()

    def run(self) -> None:
        threading.Thread(target=self._loop, name="helper", daemon=True).start()
        self.tray.run()
        self.stop.set()
        self.wake.set()


def confirm_stop() -> bool:
    """Ask before the tray menu turns the background checks off: they stay off, with no icon to turn them on again,
    until the student does so in Settings."""
    from .osutil import message_box

    where = t("Settings → Background and notifications")
    text = (t("IEUStudia will stop checking IEULearn in the background and won't start with Windows. You get no "
              "notifications about new lectures, announcements or coming quizzes until you turn background checks on "
              "again in IEUStudia ({where}).", where=where) if WINDOWS else
            t("IEUStudia will stop checking IEULearn in the background and won't start at login. You get no "
              "notifications about new lectures, announcements or coming quizzes until you turn background checks on "
              "again in IEUStudia ({where}).", where=where))
    # MB_YESNO | MB_ICONWARNING | MB_DEFBUTTON2 ("No" is the default) | MB_SETFOREGROUND | MB_TOPMOST: IDYES is 6
    return message_box(text + "\n\n" + t("Turn them off?"), t("IEUStudia background checks"),
                       0x4 | 0x30 | 0x100 | 0x10000 | 0x40000) == 6


def _icon() -> Path:
    return Path(__file__).with_name("assets") / ("icon.ico" if WINDOWS else "icon.png")


def main(argv: Optional[list[str]] = None) -> None:
    import sys

    from .accounts import activate
    from .log import setup_logging
    from .paths import migrate_legacy_data
    from .osutil import acquire_single_instance, set_app_id

    argv = list(sys.argv[1:] if argv is None else argv)
    if not acquire_single_instance(instance_name()):
        return
    set_app_id()
    migrate_legacy_data()
    activate()
    setup_logging("--debug" in argv, "helper.log")
    from . import appconfig, i18n

    if not appconfig.load().tray:
        log.info("Helper: background mode is off; not starting")
        return
    i18n.load_language()
    log.info("Helper started%s", " at sign-in" if "--at-login" in argv else "")
    Helper("--at-login" in argv).run()
    log.info("Helper stopped")
