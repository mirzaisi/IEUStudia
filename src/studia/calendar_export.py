"""The calendar as a file (.ics) for Google Calendar, Outlook or a phone: the announced quizzes and exams, IEULearn's
assignment deadlines and the student's own entries. The reminders (agent.reminders) read the same events.

Lecturers announce assessments by week ("Quiz 2: week 5") and now and then by day ("Quiz 2 on Friday, 6 November").
A quiz announced for a week is held in one of the course's classes that week, so it becomes an all-day event over
those class days; a midterm or final follows the exam schedule and spans its week. One announced for a day is on
that day (at its time, when one was given). Exam weeks from the syllabus are added when no announcement covers them.
An assignment is due at the time IEULearn gives (only the day, when IEULearn gave no time). Each event carries the
reminders chosen on the Calendar page. The event ids are stable, so importing a newer file again updates the events
instead of duplicating them (in apps that support it, such as Outlook).
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Iterable, Optional

from . import views
from .config import Settings

ISTANBUL = dt.timezone(dt.timedelta(hours=3))      # IEULearn's times (Turkey keeps UTC+3 all year)


def _course(lib: Any, settings: Settings, course: Any, cv: Optional[dict[str, Any]] = None,
            today: Optional[dt.date] = None) -> dict[str, Any]:
    """The part of views.course_detail that assessment_events reads (the same weeks, dates and class days), without
    the heads-ups from the lectures: those read every transcript, and the plan comes from the announcements alone.
    ``cv``: the course's views.course_view when the caller has it (the calendar: its class days count then too)."""
    from .alerts import assessment_plan
    from .materials import unit_week, week_starts

    info = lib.course_info(course.id)
    ov, syl = info.get("overview") or {}, info.get("syllabus") or {}
    units = lib.units(course.id)
    first = week_starts(units)
    if first is None and course.term:      # no dates in this plan: the term's calendar from the other courses
        start = lib.term_start(course.term)
        if start:
            first = dt.date.fromisoformat(start)
            first -= dt.timedelta(days=first.weekday())
    plan = views.dated_plan(assessment_plan(lib.announcements(course.id)), first)

    def week(n: int) -> tuple[str, str]:
        start = first + dt.timedelta(days=7 * (n - 1)) if first else None
        return (start.isoformat(), (start + dt.timedelta(days=6)).isoformat()) if start else ("", "")

    tr_topics = {w["no"]: w["topic"] for w in ov.get("weeks", [])}
    en_weeks = {w["no"]: w for w in syl.get("weeks", [])}
    dates = {n: week(n) for n in [*tr_topics, *en_weeks, *(e["week"] for e in plan if e.get("week") and first)]}
    for u in units:                        # a dated unit gives its week's dates
        n = unit_week(u, first)
        if n is not None:
            dates.setdefault(n, week(n))
            if u.get("start_date"):
                dates[n] = (u["start_date"], u.get("end_date") or dates[n][1])
    times: dict[int, str] = {}
    if first and (cv is not None or any(e["kind"] == "quiz" for e in plan)):
        if cv is None:
            cv = views.course_view(course, lib.recordings(course.id), settings, {}, {}, set())
        times = views.class_times(cv["days"], today or dt.date.today())
    weeks = []
    for n in sorted(dates):
        topic_en, topic_tr = (en_weeks.get(n) or {}).get("subject", ""), tr_topics.get(n, "")
        exam = next((x for x in (topic_en, topic_tr) if views.exam_topic(x)), "")
        start, end = dates[n]
        weeks.append({"no": n, "start": start, "end": end, "topic_en": topic_en, "topic_tr": topic_tr,
                      "exam": bool(exam), "exam_label": exam, "classes": views.week_classes(start, end, times)})
    return {"code": course.code, "outcomes_en": syl.get("outcomes", []), "weeks": weeks, "alerts": {"plan": plan},
            "first": first, "times": times}


def assessment_events(lib: Any, settings: Settings, today: dt.date | None = None) -> list[dict[str, Any]]:
    """The assessments still ahead (this week included), soonest first."""
    since = (today or dt.date.today()).isoformat()
    out: list[dict[str, Any]] = []
    for c in lib.courses():
        d = _course(lib, settings, c)
        weeks = {w["no"]: w for w in d["weeks"]}
        covered: set[int] = set()
        for e in (d.get("alerts") or {}).get("plan") or []:
            w = weeks.get(e.get("week") or 0)
            src = e["sources"][0]["title"] if e.get("sources") else ""
            los = [f"LO{n}: {d['outcomes_en'][n - 1]}" if n <= len(d["outcomes_en"]) else f"LO{n}" for n in e["los"]]
            base = {"uid": f"{c.id}-{e['kind']}-{e['no']}", "course": d["code"], "name": e["name"], "course_id": c.id,
                    "kind": e["kind"], "los": list(e["los"])}
            if e.get("date"):                                   # an announcement gave the day
                if w:
                    covered.add(w["no"])
                at = f" at {e['time']}" if e.get("time") else ""
                out.append({**base, "classes": [], "ends": [], "start": e["date"], "end": e["date"], "date": e["date"],
                            "time": e.get("time") or "", "week": w["no"] if w else e.get("week"),
                            "notes": [f"On {_long(e['date'])}{at}, as announced.",
                                      *([f"Covers {', '.join(los)}"] if los else []),
                                      *([f"As announced in “{src}” on IEULearn."] if src else [])]})
                continue
            if not w or not w["start"]:
                continue
            covered.add(w["no"])
            # a quiz is held in one of the week's classes; midterms and finals follow the exam schedule
            held = (w.get("classes") or []) if e["kind"] == "quiz" else []
            classes, ends = [x["date"] for x in held], [x["end"] for x in held]
            when = (f"Probably in class on {_days(classes)}; the exact day was not announced: check IEULearn."
                    if classes else "The exact day was not announced: check IEULearn." if e["kind"] == "quiz"
                    else "The exact day and time come from the exam schedule: check IEULearn.")
            out.append({**base, "classes": classes, "ends": ends,
                        "start": classes[0] if classes else w["start"],
                        "end": classes[-1] if classes else w["end"] or w["start"], "week": w["no"],
                        "notes": [f"Week {w['no']}" + (f" ({w['topic_en'] or w['topic_tr']})" if w["topic_en"] or w["topic_tr"] else ""),
                                  *([f"Covers {', '.join(los)}"] if los else []),
                                  *([f"As announced in “{src}” on IEULearn."] if src else []), when]})
        for w in d["weeks"]:
            if w["exam"] and w["start"] and w["no"] not in covered:
                out.append({"uid": f"{c.id}-syllabus-week{w['no']}", "course": d["code"], "name": w["exam_label"] or "Exam",
                            "course_id": c.id, "kind": "exam", "classes": [], "ends": [], "los": [],
                            "start": w["start"], "end": w["end"] or w["start"], "week": w["no"],
                            "notes": [f"Week {w['no']} in the syllabus: {w['exam_label']}.",
                                      "The exact day and time come from the exam schedule: check IEULearn."]})
    out = [e for e in out if e["end"] >= since]
    out.sort(key=lambda e: (e["start"], e["course"]))
    return out


def deadline_events(lib: Any, today: dt.date | None = None) -> list[dict[str, Any]]:
    """IEULearn's assignments still to send (due today or later) and the student's own entries from today on."""
    from .agenda import exam_like

    since = (today or dt.date.today()).isoformat()
    codes = {c.id: c.code for c in lib.courses(True)}
    active = {c.id for c in lib.courses()}       # a course that left the dashboard (dropped): no reminders, as the
    out: list[dict[str, Any]] = []               # Calendar page they would open doesn't show it either
    for a in lib.assignments():
        day, time = a["due"][:10], a["due"][11:16]
        if not day or day < since or a["done"] or a["course_id"] not in active:
            continue
        out.append({"uid": f"{a['course_id']}-asg-{a['asg_id']}", "kind": "assignment", "course": codes[a["course_id"]],
                    "course_id": a["course_id"], "name": a["title"], "start": day, "end": day, "date": day, "time": time,
                    "url": a["url"], "classes": [], "ends": [], "los": [], "week": None,
                    "notes": [f"Due {_long(day)}" + (f" at {time}" if time else " (IEULearn didn't give the time)")
                              + " on IEULearn.", *([a["url"]] if a["url"] else [])]})
    for e in lib.calendar_entries():
        if e["date"] < since:
            continue
        out.append({"uid": f"own-{e['id']}", "kind": "own", "course": codes.get(e["course_id"] or "", ""),
                    "course_id": e["course_id"] or "", "name": e["title"], "start": e["date"], "end": e["date"],
                    "date": e["date"], "time": e["time"] or "", "exam_like": exam_like(e["title"]), "classes": [],
                    "ends": [], "los": [], "week": None, "notes": [x for x in (e["note"], "Your own entry.") if x]})
    out.sort(key=lambda e: (e["start"], e["time"], e["course"]))
    return out


def all_events(lib: Any, settings: Settings, today: dt.date | None = None) -> list[dict[str, Any]]:
    """What the .ics file and the reminders hold: the assessments, the deadlines and the own entries."""
    out = assessment_events(lib, settings, today) + deadline_events(lib, today)
    out.sort(key=lambda e: (e["start"], e.get("time") or "", e["course"]))
    return out


def _long(day: str) -> str:
    """"2026-11-06" → "Fri 6 Nov 2026" (the file's English)."""
    from .outputs.common import MONTHS, WEEKDAYS_SHORT

    d = dt.date.fromisoformat(day)
    return f"{WEEKDAYS_SHORT[d.weekday()]} {d.day} {MONTHS[d.month - 1][:3]} {d.year}"


def _days(dates: list[str]) -> str:
    """["2026-10-13", "2026-10-15"] → "Tue 13 Oct or Thu 15 Oct"."""
    from .outputs.common import MONTHS, WEEKDAYS_SHORT

    ds = [dt.date.fromisoformat(x) for x in dates]
    return " or ".join(f"{WEEKDAYS_SHORT[d.weekday()]} {d.day} {MONTHS[d.month - 1][:3]}" for d in ds)


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> list[str]:
    """RFC 5545: lines longer than 75 octets continue on the next line after a space."""
    out, cur = [], ""
    for ch in line:
        if len((cur + ch).encode("utf-8")) > 75:
            out.append(cur)
            cur = " " + ch
        else:
            cur += ch
    return out + [cur]


def _alarms(e: dict[str, Any], prefs: dict[str, int]) -> list[tuple[str, str]]:
    """The reminders of an event, as the Calendar page set them: [(trigger, text)]."""
    what = f"{e['course']} {e['name']}".strip()
    if e.get("kind") in ("assignment", "own") and not e.get("exam_like"):
        out = []
        if prefs.get("due_days"):
            n = prefs["due_days"]
            out.append((f"-P{n}D", f"{what}: due in {'a day' if n == 1 else f'{n} days'}"
                        if e["kind"] == "assignment" else f"{what}: in {'a day' if n == 1 else f'{n} days'}"))
        if prefs.get("due_hours") and e.get("time"):
            n = prefs["due_hours"]
            out.append((f"-PT{n}H", f"{what}: due in {n} hour{'s' if n > 1 else ''}"
                        if e["kind"] == "assignment" else f"{what}: in {n} hour{'s' if n > 1 else ''}"))
        return out
    n = prefs.get("exams", 0)
    return [(f"-P{n}D", f"{what} is in {'a day' if n == 1 else f'{n} days'}")] if n else []


def ics(events: Iterable[dict[str, Any]], now: dt.datetime | None = None,
        prefs: Optional[dict[str, int]] = None) -> str:
    from .agenda import REMIND_DEFAULTS

    prefs = {**REMIND_DEFAULTS, **(prefs or {})}
    stamp = (now or dt.datetime.now(dt.timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//IEUStudia//Calendar//EN", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", "X-WR-CALNAME:IEUStudia quizzes, exams and deadlines"]
    for e in events:
        if e.get("time"):          # at its time (an instant: a deadline, or an exam whose length isn't known)
            at = dt.datetime.combine(dt.date.fromisoformat(e["start"]), dt.time.fromisoformat(e["time"]), ISTANBUL)
            when = [f"DTSTART:{at.astimezone(dt.timezone.utc):%Y%m%dT%H%M%SZ}"]
        else:
            start = dt.date.fromisoformat(e["start"])
            end = dt.date.fromisoformat(e["end"]) + dt.timedelta(days=1)      # DTEND of an all-day event is exclusive
            when = [f"DTSTART;VALUE=DATE:{start:%Y%m%d}", f"DTEND;VALUE=DATE:{end:%Y%m%d}"]
        title = " · ".join(x for x in (e["course"], e["name"]) if x)
        if e.get("kind") == "assignment":
            title += " (due)"
        lines += ["BEGIN:VEVENT", f"UID:{e['uid']}@ieustudia", f"DTSTAMP:{stamp}", *when,
                  f"SUMMARY:{_esc(title)}", f"DESCRIPTION:{_esc(chr(10).join(e['notes']))}",
                  *([f"URL:{e['url']}"] if e.get("url") else []), "TRANSP:TRANSPARENT",
                  # (an assessment's day may be only its week's)
                  "STATUS:CONFIRMED" if e.get("kind") in ("assignment", "own") else "STATUS:TENTATIVE"]
        for trigger, text in _alarms(e, prefs):
            lines += ["BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{_esc(text)}", f"TRIGGER:{trigger}", "END:VALARM"]
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(part for ln in lines for part in _fold(ln)) + "\r\n"
