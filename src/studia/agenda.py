"""The Calendar page: every dated thing of the term in one list, built from the library (no AI, no network).

- quizzes, midterms and finals as the announcements give them: a day (and time) or a week (a quiz: one of the
  course's classes that week), and the syllabus' exam weeks no announcement covers;
- IEULearn's assignment deadlines, with the time IEULearn gives, and whether each was sent;
- the course's class days: the ones held (recorded on Panopto) and the ones to come by its usual schedule, public
  holidays left out; make-up classes and cancelled classes the announcements tell of;
- public holidays, and the student's own entries (title, course, day, optional time; kept per profile).

Each item says how sure its date is ("sure": exact from IEULearn, a day or a week from an announcement, a syllabus
week, the usual schedule, a recording, your own) and where it comes from ("source"), so the page can open that.
The reminders (agent.reminders) and the .ics file (calendar_export) read the same things."""

from __future__ import annotations

import datetime as dt
import json
import re
import uuid
from collections import Counter
from typing import Any, Iterable, Optional

from .i18n import t
from .sections import guess_my_section, parse_section_tag, section_set

REMIND_KEY ="calendar_reminders"        # library meta (per profile): when the reminders come
REMIND_DEFAULTS = {"exams": 2, "due_days": 1, "due_hours": 3}   # days before a quiz or exam; a deadline: days, hours
REMIND_CHOICES = {"exams": (0, 1, 2, 3, 7), "due_days": (0, 1, 2, 3), "due_hours": (0, 1, 3, 6)}    # 0: off
TITLE_MAX, NOTE_MAX = 120, 500
AHEAD_DAYS = 150                         # class days to come are listed this far ahead at most


# ------------------------------------------------------------------ reminders: when
def reminder_prefs(lib: Any) -> dict[str, int]:
    """When the reminders come, as set on the Calendar page (the defaults until then)."""
    try:
        saved = json.loads(lib.get_meta(REMIND_KEY) or "{}")
    except (TypeError, ValueError):
        saved = {}
    out = dict(REMIND_DEFAULTS)
    for key, choices in REMIND_CHOICES.items():
        value = saved.get(key) if isinstance(saved, dict) else None
        if isinstance(value, int) and value in choices:
            out[key] = value
    return out


def set_reminder_prefs(lib: Any, patch: dict[str, Any]) -> dict[str, int]:
    cur = reminder_prefs(lib)
    for key, choices in REMIND_CHOICES.items():
        if key not in (patch or {}):
            continue
        try:
            value = int(patch[key])
        except (TypeError, ValueError):
            value = -1
        if value not in choices:
            raise ValueError(t("That reminder time isn't one IEUStudia offers."))
        cur[key] = value
    lib.set_meta(REMIND_KEY, json.dumps(cur))
    return cur


# ------------------------------------------------------------------ the student's own entries
_TIME = re.compile(r"([01]\d|2[0-3]):[0-5]\d")


def save_entry(lib: Any, entry: dict[str, Any], today: Optional[dt.date] = None) -> dict[str, Any]:
    """Add an entry (without an "id") or change one, after checking what it holds: a title, a day (ISO, within two
    years), an optional time ("14:30"), an optional course of the library and a note. Raises ValueError with the
    student's words."""
    today = today or dt.date.today()
    title = " ".join(str(entry.get("title") or "").split())[:TITLE_MAX]
    if not title:
        raise ValueError(t("Give the entry a title."))
    try:
        day = dt.date.fromisoformat(str(entry.get("date") or ""))
    except ValueError:
        raise ValueError(t("Pick the day.")) from None
    if abs((day - today).days) > 730:
        raise ValueError(t("Pick a day within two years of today."))
    time = str(entry.get("time") or "").strip()
    if time and not _TIME.fullmatch(time):
        raise ValueError(t("Write the time as 14:30, or leave it empty."))
    course_id = str(entry.get("course_id") or "")
    if course_id and lib.get_course(course_id) is None:
        course_id = ""
    note = str(entry.get("note") or "").strip()[:NOTE_MAX]
    entry_id = str(entry.get("id") or "")
    if entry_id and not any(e["id"] == entry_id for e in lib.calendar_entries()):
        raise ValueError(t("This entry isn't in your calendar any more."))
    entry_id = entry_id or uuid.uuid4().hex[:12]
    lib.save_calendar_entry(entry_id, title, day.isoformat(), time, course_id, note)
    return {"id": entry_id, "title": title, "date": day.isoformat(), "time": time, "course_id": course_id, "note": note}


def exam_like(title: str) -> bool:
    """Does an entry's title name a quiz or an exam ("EEE 215 midterm", "Kısa sınav")? Its reminders then come as a
    quiz's or an exam's do."""
    from .alerts import matches

    return bool(set(matches(title or "", "tr" if re.search(r"[çğışöüÇĞİŞÖÜ]", title or "") else "en"))
                & {"final", "midterm", "quiz", "exam"})


# ------------------------------------------------------------------ make-up and cancelled classes, from announcements
_CW = r"(?:class|classes|lecture|lectures|lab|labs|lesson|lessons|session|sessions)"
# a class that won't be held: the cancelling words with the class they are about ("no labs this week", "the lab
# sessions on October 28 will be canceled", "dersimiz iptal edilmiştir"; not "the quiz is cancelled" or "quiz
# olmayacak, ders normal işlenecek")
_CANCEL = re.compile(rf"\bno {_CW}\b|\b{_CW}\b(?:\s+\S+){{0,6}}?\s+(?:is|are|was|were|will(?:\s+also)?\s+be|"
                     rf"ha(?:s|ve)(?:\s+also)?\s+been)\s+(?:also\s+)?cancell?ed\b|"
                     rf"\bcancell?(?:ed|ing)\s+(?:\w+\s+)?{_CW}\b|"
                     r"\b(?:ders|lab|laboratuvar|uygulama)\w*\s+(?:\S+\s+){0,3}?(?:yok|iptal\w*|yap[ıi]lmayacak\w*|"
                     r"olmayacak\w*)")
# a make-up class: said so ("make-up", "telafi"), or moved ("rescheduled to", "ertelendi"); "will be held on" or
# "yapılacaktır" only with the day it was moved away from (originally/planlanan/yerine: _WAS)
_MAKEUP = re.compile(r"\bmake-?up\b|\btelafi\w*|\breschedul\w*|\bmoved to\b|\bpostponed to\b|\bertelen\w*|\btaşın\w*|"
                     r"\bkaydırıl\w*")
_HELD_ON = re.compile(r"\bwill be held\b|\bwill take place\b|\byap[ıi]lacak\w*")
_CLASS = re.compile(rf"\b{_CW}\b|\blaboratory\b|\bders\w*|\blaboratuvar\w*|\btelafi\w*|\bmake-?up\b|\buygulama\w*")
_CLASS_WORD = re.compile(rf"\b{_CW}\b|\blaboratory\b|\bders\b|\bders(?:imiz|ler|lerimiz|i|e|te|ten)\b|"
                         r"\blaboratuvar\w*|\buygulama\w*")
_LAB = re.compile(r"\blabs?\b|\blaboratory\b|\blaboratuvar\w*|\buygulama\w*")
_EXAMISH = re.compile(r"\bquiz|\bexams?\b|\bmidterm|\bfinal exam|\bara s[ıi]nav|\bvize\b|\bs[ıi]nav\w*")
# the day a class was moved away from: "originally scheduled for X" (said before the day), "X tarihinde yapılması
# planlanan", "X yerine" (after it)
_WAS = re.compile(r"originally|scheduled for|planned for|instead of|\bfrom\b")
_WAS_TR = re.compile(r"^[^.;]{0,40}?(?:yerine|planlanan)")
_REL = (("today", re.compile(r"\btoday\b|\bbugün\w*")), ("tomorrow", re.compile(r"\btomorrow\b|\byarın\w*")),
        ("this_week", re.compile(r"\bthis week\b|\bbu hafta\w*")),
        ("next_week", re.compile(r"\bnext week\b|\bgelecek hafta\w*|\bhaftaya\b")))
_CLOCK = re.compile(r"\b([01]?\d|2[0-3])[:.]([0-5]\d)\b(?:\s*([ap])\.?m\b)?")
_SENT = re.compile(r"(?<=[.!?])\s+|\n+")


def _clock(m: re.Match) -> str:
    hour = int(m.group(1)) % (12 if m.group(3) else 24) + (12 if m.group(3) == "p" else 0)
    return f"{hour:02d}:{m.group(2)}"


def _lower(text: str) -> str:
    from .outputs.highlights import _tr_lower

    return _tr_lower(text or "")


def _dates(text: str, posted: dt.date) -> list[tuple[dt.date, int, int]]:
    """The days in a sentence of an announcement, with where each is: a day without a year is the one after the
    announcement (a day two months before it: the next year's); only days near it count (14 days before, 120 after)."""
    from .coursesite import _date_of, date_matches

    out: list[tuple[dt.date, int, int]] = []
    for m in date_matches(text):
        d = _date_of(m, posted.year, 1)
        if d is None:
            continue
        if not any(m.group(k) for k in ("iy", "ny", "dy", "my")):
            try:
                d = d.replace(year=posted.year)
                if d < posted - dt.timedelta(days=60):
                    d = d.replace(year=posted.year + 1)
            except ValueError:
                continue
        if posted - dt.timedelta(days=14) <= d <= posted + dt.timedelta(days=120) and \
                not any(x[0] == d and x[1] <= m.start() < x[2] for x in out):
            out.append((d, m.start(), m.end()))
    return out


def schedule_changes(anns: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """The make-up and cancelled classes the announcements tell of: [{"kind": "makeup" | "cancelled", "date" (or
    "" with "week_of": a day of that week, for "no labs this week"), "time", "lab", "announcement_id", "title"}].
    Only sentences about a class (not a quiz or an exam: the assessment plan has those), and only days near the
    announcement; "originally scheduled for X, rescheduled to Y" cancels X and adds Y."""
    out: dict[tuple[str, str, str], dict[str, Any]] = {}
    for a in anns:
        try:
            posted = dt.datetime.fromisoformat(a.get("posted_at") or a.get("first_seen") or "").date()
        except ValueError:
            continue
        title, body = a.get("title") or "", a.get("body") or ""
        low_title = _lower(title)
        whole = _lower(f"{title}\n{body}")
        if not (_CANCEL.search(whole) or _MAKEUP.search(whole) or _HELD_ON.search(whole)):
            continue
        clocks = {_clock(m) for m in _CLOCK.finditer(whole) if not re.match(r"\d{1,2}[.:]\d{2}[.:]", whole[m.start():])}
        one_time = next(iter(clocks)) if len(clocks) == 1 else ""
        sent = ""

        def add(kind: str, day: str, time: str, lab: bool, week_of: str = "") -> None:
            key = (kind, day or f"w{week_of}", "lab" if lab else "class")
            if key not in out:                 # the sections it is for ("Section 2: Make-up Lab Lecture"), if it says
                sections = section_set(parse_section_tag(sent) or parse_section_tag(title))
                out[key] = {"kind": kind, "date": day, "week_of": week_of, "time": time, "lab": lab,
                            "sections": sorted(sections), "announcement_id": a.get("id", ""), "title": title}

        for sent in [title, *_SENT.split(body)]:
            low = _lower(sent)
            cancel, makeup, held = bool(_CANCEL.search(low)), bool(_MAKEUP.search(low)), bool(_HELD_ON.search(low))
            if not (cancel or makeup or held) or not (_CLASS.search(low) or _CLASS.search(low_title)):
                continue
            if _EXAMISH.search(low) and not _CLASS_WORD.search(low):
                continue                                       # a quiz or an exam (a make-up exam) moved: not a class
            lab = bool(_LAB.search(low) or _LAB.search(low_title))
            days = _dates(low, posted)
            was = next((d for d in days if _WAS.search(low[max(0, d[1] - 40):d[1]]) or _WAS_TR.match(low[d[2]:])), None)
            if (makeup or (held and was)) and len(days) >= 2:  # "originally scheduled for X … rescheduled to Y"
                was = was or days[0]
                new = next(d for d in reversed(days) if d is not was)
                near = _CLOCK.search(low, new[2], new[2] + 40)
                add("cancelled", was[0].isoformat(), "", lab)
                add("makeup", new[0].isoformat(), _clock(near) if near else one_time, lab)
            elif makeup and days and not cancel:
                d = days[-1]
                near = _CLOCK.search(low, d[2], d[2] + 40)
                add("makeup", d[0].isoformat(), _clock(near) if near else one_time, lab)
            elif cancel and days:
                for d in days:
                    add("cancelled", d[0].isoformat(), "", lab)
            elif cancel:                                       # "There will be no labs this week."
                rel = next((k for k, rx in _REL if rx.search(low)), "")
                day = {"today": posted, "tomorrow": posted + dt.timedelta(days=1)}.get(rel)
                if day:
                    add("cancelled", day.isoformat(), "", lab)
                elif rel:
                    week_day = posted + dt.timedelta(days=7 if rel == "next_week" else 0)
                    add("cancelled", "", "", lab, week_day.isoformat())
    return sorted(out.values(), key=lambda x: (x["date"] or x["week_of"], x["kind"]))


# ------------------------------------------------------------------ the course's classes
def _minutes(hm: str) -> int:
    h, _, m = (hm or "0:0").partition(":")
    return int(h) * 60 + int(m or 0)


def _counted(day: dict[str, Any]) -> list[dict[str, Any]]:
    """A class day's recordings that count (not a duplicate, hidden or very short one; all of them if none does)."""
    return [r for r in day["recordings"] if not (r["duplicate"] or r["hidden"] or r["too_short"])] or day["recordings"]


def blocks(recs: list[dict[str, Any]]) -> list[list[str]]:
    """When a class met, from its recordings: [["08:30", "10:20"], ["13:00", "14:50"]] (parts less than half an hour
    apart are one block: a 3-hour class recorded in 3 parts; a morning and an afternoon class are two)."""
    out: list[list[str]] = []
    for r in sorted(recs, key=lambda r: r["time"]):
        if out and _minutes(r["time"]) - _minutes(out[-1][1]) <= 30:
            out[-1][1] = max(out[-1][1], r["end_time"])
        else:
            out.append([r["time"], r["end_time"]])
    return out


def usual_classes(days: list[dict[str, Any]], today: dt.date) -> dict[int, list[dict[str, Any]]]:
    """When the course meets, by weekday (Monday = 0): [{"kind", "blocks"}] as on its latest class day of that
    weekday, for the weekdays views.class_times counts as regular (not a one-off make-up class)."""
    from .views import class_times

    regular = class_times(days, today)
    out: dict[int, list[dict[str, Any]]] = {}
    latest: dict[int, str] = {}
    for d in sorted((d for d in days if d["mine"] is not False and d["date"] <= today.isoformat() and d["parts"]),
                    key=lambda d: d["date"]):
        wd = dt.date.fromisoformat(d["date"]).weekday()
        if wd not in regular:
            continue
        if latest.get(wd) != d["date"]:
            latest[wd], out[wd] = d["date"], []
        usual = [[_round5(a), _round5(b)] for a, b in blocks(_counted(d))]       # (a recording starts a minute late)
        out[wd] = [x for x in out[wd] if x["kind"] != d["kind"]] + [{"kind": d["kind"], "blocks": usual}]
    return out


def _round5(hm: str) -> str:
    """"08:31" → "08:30", "11:04" → "11:05": a class's usual time, from when its recording ran."""
    m = (_minutes(hm) + 2) // 5 * 5
    return f"{m // 60 % 24:02d}:{m % 60:02d}"


def _teaching_end(weeks: list[dict[str, Any]], first: Optional[dt.date]) -> Optional[dt.date]:
    """The last day classes are held by the usual schedule: the day before the last exam week from week 10 on (the
    final), else the last week the plan or the syllabus lists (15 weeks without one)."""
    if first is None:
        return None
    dated = [w for w in weeks if w.get("start")]
    finals = [w for w in dated if w.get("exam") and w["no"] >= 10]
    if finals:
        return dt.date.fromisoformat(finals[-1]["start"]) - dt.timedelta(days=1)
    if dated:
        return dt.date.fromisoformat(dated[-1]["end"] or dated[-1]["start"])
    return first + dt.timedelta(weeks=15, days=-1)


# ------------------------------------------------------------------ the calendar
def _week(w: dict[str, Any]) -> dict[str, Any]:
    return {"no": w["no"], "start": w["start"], "end": w["end"] or w["start"]}


def course_items(lib: Any, settings: Any, course: Any, now: dt.datetime,
                 warnings: Optional[dict[str, list[dict[str, Any]]]] = None
                 ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """One course's items, and what the page needs to know of it (its week 1, the end of its classes)."""
    from . import calendar_export, views
    from .materials import week_of

    today = now.date()
    cv = views.course_view(course, lib.recordings(course.id), settings, {}, warnings or {}, set())
    d = calendar_export._course(lib, settings, course, cv=cv, today=today)
    first, weeks = d["first"], {w["no"]: w for w in d["weeks"]}
    base = {"course_id": course.id, "code": course.code}
    items: list[dict[str, Any]] = []

    def week_no(day: str) -> Optional[int]:
        return week_of(dt.date.fromisoformat(day), first) if first and day else None

    # quizzes, midterms and finals as announced: a day, or a week
    covered: set[int] = set()
    for e in d["alerts"]["plan"]:
        src = {"type": "announcement", "id": e.get("date_from") or (e["sources"][0]["id"] if e["sources"] else "")}
        common = {**base, "id": f"{course.id}-{e['kind']}-{e['no']}", "kind": e["kind"], "title": e["name"],
                  "los": e["los"], "source": src}
        if e.get("date"):
            items.append({**common, "date": e["date"], "time": e.get("time") or "", "week_no": week_no(e["date"]),
                          "sure": "day"})
        else:
            w = weeks.get(e.get("week") or 0)
            if not w or not w["start"]:
                continue
            days = [x["date"] for x in w.get("classes") or []] if e["kind"] == "quiz" else []
            items.append({**common, "date": "", "week": _week(w), "days": days, "week_no": w["no"], "sure": "week"})
        if e["kind"] != "quiz" and e.get("week"):
            covered.add(e["week"])
    syllabus_url = (lib.course_info(course.id).get("syllabus") or {}).get("url", "")
    for w in d["weeks"]:                     # the syllabus' exam weeks no announcement covers
        if w["exam"] and w["start"] and w["no"] not in covered:
            items.append({**base, "id": f"{course.id}-syllabus-week{w['no']}", "kind": "exam",
                          "title": w["exam_label"] or "Exam", "date": "", "week": _week(w), "days": [],
                          "week_no": w["no"], "sure": "syllabus", "los": [],
                          "source": {"type": "syllabus", "url": syllabus_url, "week": w["no"]}})
    # IEULearn's assignments
    for a in lib.assignments(course.id):
        if not a["due"]:
            continue
        items.append({**base, "id": f"{course.id}-asg-{a['asg_id']}", "kind": "assignment", "title": a["title"],
                      "date": a["due"][:10], "time": a["due"][11:16], "week_no": week_no(a["due"][:10]),
                      "sure": "exact" if len(a["due"]) > 10 else "ieulearn_day", "done": bool(a["done"]),
                      "state": a["state"] or "", "source": {"type": "url", "url": a["url"]}})
    # class days: held (recorded), to come (by the usual schedule), make-up and cancelled ones
    prefs = settings.prefs(course.id)
    show_all = prefs.show_mode == "all"
    held: dict[str, dict[str, Any]] = {}
    for day in cv["days"]:
        if not day["parts"] or (day["mine"] is False and not show_all):
            continue
        b = blocks(_counted(day))
        held[f"{day['date']}|{day['kind']}"] = {
            **base, "id": f"{course.id}-class-{day['date']}-{day['kind']}", "kind": "class", "class_kind": day["kind"],
            "title": "", "date": day["date"], "time": b[0][0], "end_time": b[-1][1], "blocks": b,
            "week_no": week_no(day["date"]), "sure": "recorded", "transcribed": bool(day["any_done"]),
            "source": {"type": "day", "day_key": day["day_key"]}}
    mine = section_set(prefs.my_section or guess_my_section(course) or "")
    cancelled: set[tuple[str, bool]] = set()
    for ch in schedule_changes(lib.announcements(course.id)):
        if mine and ch["sections"] and not mine & set(ch["sections"]):
            continue                                    # another section's make-up or cancelled class
        kind = "lab" if ch["lab"] else "lecture"
        if ch["kind"] == "makeup":
            hit = held.get(f"{ch['date']}|{kind}") or next((h for k, h in held.items() if k.startswith(ch["date"] + "|")),
                                                         None)
            if hit:                          # the make-up class was held: it is the recording's day
                hit["makeup"] = True
                continue
            items.append({**base, "id": f"{course.id}-makeup-{ch['date']}", "kind": "makeup", "class_kind": kind,
                          "title": "", "date": ch["date"], "time": ch["time"], "week_no": week_no(ch["date"]),
                          "sure": "announced", "note": ch["title"],
                          "source": {"type": "announcement", "id": ch["announcement_id"]}})
            continue
        if ch["date"]:
            # a recording that day of what was cancelled (a lab, or any class): it was held after all
            if any(k.startswith(ch["date"] + "|") and (not ch["lab"] or k.endswith("|lab")) for k in held):
                continue
            cancelled.add((ch["date"], ch["lab"]))
            items.append({**base, "id": f"{course.id}-cancel-{ch['date']}-{kind}", "kind": "cancelled",
                          "class_kind": kind, "title": "", "date": ch["date"], "time": "", "week_no": week_no(ch["date"]),
                          "sure": "announced", "note": ch["title"],
                          "source": {"type": "announcement", "id": ch["announcement_id"]}})
        else:
            mon = dt.date.fromisoformat(ch["week_of"])
            mon -= dt.timedelta(days=mon.weekday())
            span = {"no": week_no(mon.isoformat()), "start": mon.isoformat(),
                    "end": (mon + dt.timedelta(days=6)).isoformat()}
            for i in range(7):
                cancelled.add(((mon + dt.timedelta(days=i)).isoformat(), ch["lab"]))
            items.append({**base, "id": f"{course.id}-cancel-w{mon.isoformat()}-{kind}", "kind": "cancelled",
                          "class_kind": kind, "title": "", "date": "", "week": span, "days": [], "week_no": span["no"],
                          "sure": "announced", "note": ch["title"],
                          "source": {"type": "announcement", "id": ch["announcement_id"]}})
    items += held.values()
    usual = usual_classes(cv["days"], today)
    end = _teaching_end(d["weeks"], first)
    if usual and end:
        from .views import holiday

        day = today
        last = min(end, today + dt.timedelta(days=AHEAD_DAYS))
        while day <= last:
            iso = day.isoformat()
            for u in usual.get(day.weekday(), []) if not holiday(day) else []:
                lab = u["kind"] == "lab"
                # held already (today's), or cancelled: "no class that day" cancels whatever the course has then,
                # "no lab" only its lab
                if f"{iso}|{u['kind']}" in held or (iso, False) in cancelled or (lab and (iso, True) in cancelled):
                    continue
                items.append({**base, "id": f"{course.id}-class-{iso}-{u['kind']}", "kind": "class",
                              "class_kind": u["kind"], "title": "", "date": iso, "time": u["blocks"][0][0],
                              "end_time": u["blocks"][-1][1], "blocks": u["blocks"], "week_no": week_no(iso),
                              "sure": "schedule", "source": {"type": "week", "week": week_no(iso)}})
            day += dt.timedelta(days=1)
    starts = [dt.date.fromisoformat(w["start"]) for w in d["weeks"] if w.get("start")]
    info = {"first": first.isoformat() if first else "", "end": end.isoformat() if end else "",
            "last": max(starts).isoformat() if starts else ""}
    return items, info


def entry_items(lib: Any, codes: dict[str, str]) -> list[dict[str, Any]]:
    return [{"id": f"own-{e['id']}", "entry_id": e["id"], "kind": "own", "course_id": e["course_id"] or "",
             "code": codes.get(e["course_id"] or "", ""), "title": e["title"], "date": e["date"], "time": e["time"] or "",
             "note": e["note"] or "", "sure": "yours", "exam_like": exam_like(e["title"]), "week_no": None,
             "source": {"type": "entry"}} for e in lib.calendar_entries()]


def holiday_items(start: dt.date, end: dt.date) -> list[dict[str, Any]]:
    """Turkey's public holidays between two days (views.HOLIDAYS and FEASTS): no classes then."""
    from .views import holiday

    out, day = [], start
    while day <= end:
        if holiday(day):
            out.append({"id": f"holiday-{day.isoformat()}", "kind": "holiday", "course_id": "", "code": "",
                        "title": "", "date": day.isoformat(), "time": "", "sure": "holiday", "week_no": None,
                        "source": {"type": "none"}})
        day += dt.timedelta(days=1)
    return out


def build(lib: Any, settings: Any, now: Optional[dt.datetime] = None) -> dict[str, Any]:
    """Everything the Calendar page shows: {"items", "courses", "term": {"start", "end"}, "today", "reminders"}."""
    from .calendar_export import istanbul_now

    now = now or istanbul_now()                  # (IEULearn's times are Turkey's, whatever zone this PC is set to)
    today = now.date()
    courses = [c for c in lib.courses() if c.code]
    warnings = lib.all_warnings()
    items: list[dict[str, Any]] = []
    firsts, ends = [], []
    for c in courses:
        got, info = course_items(lib, settings, c, now, warnings)
        items += got
        if info["first"]:
            firsts.append(dt.date.fromisoformat(info["first"]))
        ends += [dt.date.fromisoformat(x) for x in (info["end"], info["last"]) if x]
    codes = {c.id: c.code for c in courses}
    items += entry_items(lib, codes)
    dated = [dt.date.fromisoformat(i["date"]) for i in items if i.get("date")]
    start = min(firsts or dated or [today])
    start -= dt.timedelta(days=start.weekday())
    end = max([*ends, *dated, today + dt.timedelta(days=7)])
    items += holiday_items(start, end)
    items.sort(key=lambda i: (i.get("date") or (i.get("week") or {}).get("start", ""), i.get("time") or "",
                              i.get("code") or "", i["id"]))
    twins = Counter(c.code for c in courses)
    return {"items": items, "today": today.isoformat(), "now": now.strftime("%H:%M"),
            "term": {"start": start.isoformat(), "end": end.isoformat()},
            "courses": [{"id": c.id, "code": c.code, "name": settings.prefs(c.id).alias or c.name or c.code,
                         "section": c.section or "", "twin": twins[c.code] > 1} for c in courses],
            "reminders": reminder_prefs(lib)}
