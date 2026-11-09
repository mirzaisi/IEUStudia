"""The weekly digest: per course, what happened this week (lectures and what the lecturer stressed, new files,
announcements) and what is coming (announced quizzes and exams), plus a to-do list — built from the library without
AI. Claude can turn it into a short written summary on request (``digest_prompt``).
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Optional

from . import store
from .i18n import N_, day_month, t
from .outputs.common import MONTHS, kind_label, long_date
from .outputs.markdown import part_views

KEY = ("quiz", "midterm", "final", "exam", "project", "homework", "schedule")


def _range_en(week: dict[str, Any]) -> str:
    """"28 Sep – 04 Oct 2026": the week in English, as the AI and the exported digest read it."""
    try:
        a, b = dt.date.fromisoformat(week["start"]), dt.date.fromisoformat(week["end"])
    except (KeyError, TypeError, ValueError):
        return str(week.get("label") or "")
    return f"{a.day:02d} {MONTHS[a.month - 1][:3]} – {b.day:02d} {MONTHS[b.month - 1][:3]} {b.year}"


def _kind(day_key: str) -> str:
    """A class day's kind ("lecture", "lab") from its key ("<course>|<date>|<kind>")."""
    return (day_key or "").rsplit("|", 1)[-1]


def _lecture_en(lec: dict[str, Any]) -> tuple[str, str]:
    """A lecture's kind and day in English ("Lecture", "Wednesday, 30 September 2026")."""
    return kind_label(_kind(lec.get("day_key", ""))), long_date(dt.date.fromisoformat(lec["date"]))


def week_bounds(offset: int = 0, today: Optional[dt.date] = None) -> tuple[dt.date, dt.date]:
    """Monday and Sunday of this week (``offset`` weeks earlier/later)."""
    today = today or dt.date.today()
    monday = today - dt.timedelta(days=today.weekday()) + dt.timedelta(weeks=offset)
    return monday, monday + dt.timedelta(days=6)


def _in(iso: str, a: dt.date, b: dt.date) -> bool:
    return bool(iso) and a.isoformat() <= iso[:10] <= b.isoformat()


def _overlap(w: dict[str, Any], a: dt.date, b: dt.date) -> int:
    """How many days of the course week ``w`` fall between ``a`` and ``b``."""
    if not w["start"]:
        return 0
    start, end = dt.date.fromisoformat(w["start"]), dt.date.fromisoformat(w["end"] or w["start"])
    return max(0, (min(b, end) - max(a, start)).days + 1)


def _week(weeks: list[dict[str, Any]], no: Any) -> Optional[dict[str, Any]]:
    """A course week by number, with its dates counted from a dated week when the plan does not list it."""
    w = next((x for x in weeks if x["no"] == no), None)
    if w and w["start"]:
        return w
    ref = next((x for x in weeks if x["start"]), None)
    if ref is None or not isinstance(no, int):
        return w
    start = dt.date.fromisoformat(ref["start"]) + dt.timedelta(weeks=no - ref["no"])
    return {"no": no, "start": start.isoformat(), "end": (start + dt.timedelta(days=6)).isoformat()}


def weekly_digest(lib: Any, settings: Any, offset: int = 0, today: Optional[dt.date] = None,
                  now: Optional[dt.datetime] = None) -> dict[str, Any]:
    from .views import classes_over, course_detail

    if now is None:
        now = dt.datetime.combine(today, dt.time()) if today else dt.datetime.now()
    today = now.date()
    mon, sun = week_bounds(offset, today)
    horizon = sun + dt.timedelta(days=14)
    courses, todo = [], []
    mats_by_course: dict[str, list[dict]] = {}
    for m in lib.materials():
        mats_by_course.setdefault(m["course_id"], []).append(m)
    for c in lib.courses():
        if not c.code:
            continue
        d = course_detail(lib, settings, c.id, now=now)
        if d is None:
            continue
        # the course week that covers most of this week (IEULearn's weeks of a Wednesday course run Wed-Tue)
        week = max((w for w in d["weeks"] if _overlap(w, mon, sun) > 0), key=lambda w: _overlap(w, mon, sun),
                   default=None)
        show_all = settings.prefs(c.id).show_mode == "all"
        lectures = []
        for w in d["weeks"]:
            for day in w["days"]:
                if not _in(day["date"], mon, sun) or not day["parts"]:     # nothing but too short or duplicate parts
                    continue
                if day["mine"] is False and not show_all:                   # another section's lecture
                    continue
                stressed = []
                if day["any_done"]:
                    recs = [r for r in lib.recordings(c.id) if r.day_key == day["day_key"]]
                    for p in part_views(recs, store.load_transcripts([r.delivery_id for r in recs])):
                        stressed += [para.text[:220] for para in p.paragraphs if para.star][:3]
                else:                       # "text" for the page, "en" for the AI and the exported digest
                    lab = _kind(day["day_key"]) == "lab"
                    say = N_("Transcribe the lab of {day}") if lab else N_("Transcribe the lecture of {day}")
                    todo.append({"kind": "transcribe", "course": c.code, "course_id": c.id, "day_key": day["day_key"],
                                 "text": t(say, day=day["label"]),
                                 "en": say.format(day=long_date(dt.date.fromisoformat(day["date"])))})
                lectures.append({"day_key": day["day_key"], "date": day["date"], "label": day["label"],
                                 "kind": day["kind_label"], "transcribed": day["transcribed"], "any_done": day["any_done"],
                                 "stressed": stressed[:3]})
        new_files = [{"id": m["resource_id"], "title": m["title"], "downloaded": bool(m.get("local_path"))}
                     for m in mats_by_course.get(c.id, []) if _in(m.get("first_seen") or "", mon, sun)]
        week_files = [m for w in d["weeks"] if week and w["no"] == week["no"] for u in w["units"] for m in u["materials"]]
        for m in week_files:
            if not m["downloaded"]:
                say = N_("Download “{title}” (this week's material)")
                todo.append({"kind": "download", "course": c.code, "course_id": c.id, "resource_id": m["id"],
                             "text": t(say, title=m["title"]), "en": say.format(title=m["title"])})
        anns = [{"id": a["id"], "title": a["title"], "posted_at": a["posted_at"]} for a in d["announcements"]
                if _in(a.get("posted_at") or "", mon, sun)]
        coming = []
        for e in (d.get("alerts") or {}).get("plan") or []:
            w = _week(d["weeks"], e.get("week"))
            if not (w and w["start"]):
                continue
            # a quiz is held in one of the week's classes; midterms and finals follow the exam schedule
            classes = (w.get("classes") or []) if e.get("kind", "quiz") == "quiz" else []
            first, last = (classes[0]["date"], classes[-1]["date"]) if classes else (w["start"], w["end"] or w["start"])
            if mon.isoformat() <= last and first <= horizon.isoformat():
                coming.append({"name": e["name"], "week": e["week"], "start": w["start"], "end": w["end"], "los": e["los"],
                               "this_week": first <= sun.isoformat() and last >= mon.isoformat(),
                               "dates": [x["date"] for x in classes],
                               "done": (w["end"] or w["start"]) < today.isoformat() or classes_over(classes, now)})
        planned = {e.get("week") for e in (d.get("alerts") or {}).get("plan") or [] if e.get("kind") != "quiz"}
        for w in d["weeks"]:        # an exam week of the syllabus no announcement covers (the calendar has it too)
            last = w["end"] or w["start"]
            if w.get("exam") and w["start"] and w["no"] not in planned and mon.isoformat() <= last \
                    and w["start"] <= horizon.isoformat():
                coming.append({"name": w.get("exam_label") or "Exam", "week": w["no"], "start": w["start"], "end": w["end"],
                               "los": [], "this_week": w["start"] <= sun.isoformat(), "dates": [],
                               "done": last < today.isoformat(), "exam_week": True})
        said = [{"category": a["category"], "text": a["text"][:240], "date": a["date"], "source": a["source"],
                 "other_section": a.get("other_section", ""),           # heard in another section: its dates may differ
                 "other_section_en": a.get("other_section_en", "")}     # the same in English, for the AI and the export
                for a in (d.get("alerts") or {}).get("items", []) if a["category"] in KEY and _in(a["date"], mon, sun)]
        if not (week or lectures or new_files or anns or coming or said):
            continue
        courses.append({"id": c.id, "code": c.code, "name": d["display_name"],
                        "week": {"no": week["no"], "topic": week["topic_en"] or week["topic_tr"] or
                                 " · ".join(u["title"] for u in week["units"])} if week else None,
                        "lectures": lectures, "new_files": new_files, "announcements": anns, "coming": coming,
                        "said": said[:6]})
    for c in courses:
        for e in c["coming"]:
            if e["this_week"] and not e["done"]:
                say = N_("{name} is this week ({los})") if e["los"] else N_("{name} is this week")
                values = {"name": e["name"], "los": "LO" + ", LO".join(map(str, e["los"]))}     # the name is an id
                todo.insert(0, {"kind": "prepare", "course": c["code"], "course_id": c["id"],
                                **({} if e.get("exam_week") else {"name": e["name"]}),   # (Get ready reads the plan)
                                "text": t(say, **values), "en": say.format(**values)})
    return {"week": {"start": mon.isoformat(), "end": sun.isoformat(), "offset": offset,
                     "label": f"{day_month(mon)} – {day_month(sun)} {sun.year}"}, "courses": courses, "todo": todo[:30]}


def digest_prompt(digest: dict[str, Any]) -> str:
    """For Claude: the week as facts, asking for a short motivating summary and a plan for the next days (in English
    whatever language the page shows)."""
    lines = [f"WEEK: {_range_en(digest['week'])}"]
    for c in digest["courses"]:
        lines.append(f"\nCOURSE {c['code']} — {c['name']}" + (f" (week {c['week']['no']}: {c['week']['topic']})" if c["week"] else ""))
        for lec in c["lectures"]:
            lines.append(f"  lecture {_lecture_en(lec)[1]} ({'transcribed' if lec['any_done'] else 'not transcribed yet'})")
            lines += [f"    stressed: {s}" for s in lec["stressed"]]
        for e in c["coming"]:
            lines.append(f"  {'done' if e['done'] else 'coming'}: {e['name']} in week {e['week']} ({e['start']}–{e['end']})"
                         + (f", class days {', '.join(e['dates'])}" if e["dates"] else "")
                         + (f", LO{', LO'.join(map(str, e['los']))}" if e["los"] else ""))
        for s in c["said"]:
            lines.append(f"  lecturer said ({s['category']}, {s['date'][:10]}"
                         + (f", in {s['other_section_en']}" if s.get("other_section_en") else "") + f"): {s['text']}")
        if c["new_files"]:
            lines.append("  new files: " + ", ".join(f["title"] for f in c["new_files"]))
        if c["announcements"]:
            lines.append("  announcements: " + "; ".join(a["title"] for a in c["announcements"]))
    lines.append("\nTO DO: " + "; ".join(f"{item['course']}: " * bool(item.get("course")) + (item.get("en") or item["text"])
                                         for item in digest["todo"]))
    return ("Write a short weekly digest for the student (Markdown, at most ~250 words): first 2–3 sentences on the week "
            "as a whole, then per course one or two bullets on what was covered and what matters next (quizzes, "
            "deadlines, what the lecturer stressed), then a prioritised list of 3–6 things to do in the next days. "
            "Use only these facts; no greetings.\n\n=== THE WEEK ===\n\n" + "\n".join(lines))


def digest_markdown(digest: dict[str, Any], summary: str = "") -> str:
    """The digest as a Markdown file (English, like the app's other exported files)."""
    out = [f"# Weekly digest — {_range_en(digest['week'])}", ""]
    if summary:
        out += [summary.strip(), ""]
    for c in digest["courses"]:
        out.append(f"## {c['code']} — {c['name']}")
        if c["week"]:
            out.append(f"*Week {c['week']['no']}: {c['week']['topic']}*")
        for e in c["coming"]:
            out.append(f"- **{e['name']}** — week {e['week']} ({e['start']} – {e['end']})" + (f", LO{', LO'.join(map(str, e['los']))}" if e["los"] else "")
                       + (" — done" if e["done"] else ""))
        for lec in c["lectures"]:
            kind, day = _lecture_en(lec)
            out.append(f"- {kind} {day}" + ("" if lec["any_done"] else " — not transcribed yet"))
            out += [f"  - ⭐ {s}" for s in lec["stressed"]]
        for s in c["said"]:
            out.append(f"- Lecturer ({s['category']}" + (f", {s['other_section_en']}" if s.get("other_section_en") else "")
                       + f"): {s['text']}")
        if c["new_files"]:
            out.append("- New files: " + ", ".join(f["title"] for f in c["new_files"]))
        out.append("")
    if digest["todo"]:
        out += ["## To do", ""] + [f"- [ ] {item['course']}: {item.get('en') or item['text']}" for item in digest["todo"]]
    return "\n".join(out).rstrip() + "\n"
