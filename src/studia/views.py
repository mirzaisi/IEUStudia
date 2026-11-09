"""Pure builders for the data shapes the UI renders (courses, class days, recordings, transcripts)."""

from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Collection, Iterable, Optional

from . import store
from .anomalies import course_anomalies
from .catchup import no_sound
from .config import Settings
from .domain import Course, Recording
from .i18n import date_long, t, weekday
from .library import ANN_UNIT
from .outputs.common import format_ts, kind_label, lang_tag, language_shares, time_range
from .outputs.highlights import LABELS, _tr_lower, excerpt
from .outputs.markdown import para_spans, part_views
from .quality import worst_level
from .sections import available_sections, classify, guess_my_section, parse_section_tag
from .slides import jpeg_size


def _too_short(rec: Recording, settings: Settings) -> bool:
    return 0 < rec.duration_s < settings.min_duration_s


def recording_view(rec: Recording, settings: Settings, mine: Optional[bool], part: Optional[int], parts: int,
                   job: Optional[dict[str, Any]], warnings: list[dict[str, Any]], has_slides: bool,
                   anomalies: Optional[list[dict[str, str]]] = None) -> dict[str, Any]:
    status = "live" if rec.live else rec.status            # (live: still being recorded)
    progress = detail = None
    error = rec.error or None
    if job:
        status = {"queued": "queued", "downloading": "downloading", "transcribing": "transcribing",
                  "writing": "transcribing"}.get(job["stage"], status)
        progress, detail = job["progress"], job["detail"]
    end = rec.start + dt.timedelta(seconds=rec.duration_s)
    return {
        "id": rec.delivery_id, "name": rec.name, "start": rec.start.isoformat(), "time": f"{rec.start:%H:%M}",
        "end_time": f"{end:%H:%M}", "duration_s": round(rec.duration_s), "part": part, "parts": parts,
        "kind": rec.kind, "section_tag": rec.section_tag, "mine": mine, "is_new": rec.is_new,
        "duplicate": bool(rec.duplicate_of), "hidden": rec.hidden, "too_short": _too_short(rec, settings),
        "status": status, "progress": progress, "detail": detail, "error": error, "viewer_url": rec.viewer_url,
        "warnings": [{"level": w.get("level", "info"), "message": w.get("message", "")} for w in warnings],
        "warn_level": worst_level(warnings), "has_slides": has_slides,
        "no_sound": no_sound(rec, warnings),         # (also one that failed so before 2.3: only its error says it)
        "anomalies": anomalies or [],
    }


def _day_label(d: dt.date) -> str:
    return date_long(d)          # Wednesday, 14 October 2026 / 14 Ekim 2026 Çarşamba


def _cover_uri(url: str) -> str:
    """The cached IEULearn cover picture of a course, if the content sync fetched it."""
    if not url:
        return ""
    from .coursesite import cover_file

    path = cover_file(url)
    return path.as_uri() if path.is_file() else ""


def course_view(course: Course, recs: list[Recording], settings: Settings, jobs: dict[str, dict[str, Any]],
                warnings: dict[str, list[dict[str, Any]]], slides_ids: Collection[str]) -> dict[str, Any]:
    prefs = settings.prefs(course.id)
    my_section = prefs.my_section or guess_my_section(course)
    sections = available_sections(recs)
    mine_map = classify(course, recs, my_section) if sections else {r.delivery_id: None for r in recs}
    days: dict[str, list[Recording]] = defaultdict(list)
    for r in recs:
        days[r.day_key].append(r)
    found = course_anomalies(recs, warnings, min_s=settings.min_duration_s)
    follow = len(sections) > 1 and prefs.show_mode != "all"      # several sections publish here and you follow yours
    day_views = []
    for key, rs in days.items():
        rs.sort(key=lambda r: (r.start, bool(r.duplicate_of), r.delivery_id))
        countable = [r for r in rs if not r.duplicate_of and not r.hidden and not _too_short(r, settings)]
        part_no = {r.delivery_id: i for i, r in enumerate(countable, start=1)}
        rviews = [recording_view(r, settings, mine_map.get(r.delivery_id), part_no.get(r.delivery_id), len(countable),
                                 jobs.get(r.delivery_id), warnings.get(r.delivery_id, []), r.delivery_id in slides_ids,
                                 found.get(r.delivery_id))
                  for r in rs]
        flags = [mine_map.get(r.delivery_id) for r in countable] or [None]
        mine = True if any(f is True for f in flags) else (False if all(f is False for f in flags) else None)
        # what the day's state is about: your section's parts when you follow yours (a day two sections share was
        # never done while the other one's part wasn't), else all of them (also another section's own day)
        scope = [r for r in countable if mine_map.get(r.delivery_id) is not False] if follow else countable
        scope = scope or countable
        done = [r for r in scope if r.status == "done"]
        # parts with no speech on Panopto (the microphone was off, or only noise or music): trying again finds the
        # same, so a day whose other parts are transcribed is transcribed, and one with nothing else has no sound
        # (as a full sync leaves them out: catchup.no_sound); not one being transcribed again now
        quiet = {r.delivery_id for r in scope if r.status != "done" and not r.live and r.delivery_id not in jobs
                 and no_sound(r, warnings.get(r.delivery_id, []))}
        settled = bool(scope) and len(done) + len(quiet) == len(scope)
        d = rs[0].start.date()
        day_views.append({
            "day_key": key, "date": d.isoformat(), "weekday": weekday(d, short=True), "label": _day_label(d),
            "kind": rs[0].kind, "kind_label": t(kind_label(rs[0].kind)), "mine": mine,
            "transcribed": settled and bool(done), "any_done": any(r.status == "done" for r in countable),
            # all of it that isn't transcribed has no speech (without "transcribed": none of it has: nothing to do)
            "no_sound": settled and bool(quiet),
            "duration_s": round(sum(r.duration_s for r in countable)), "parts": len(countable),
            "is_new": any(r.is_new for r in countable),
            "warn_level": worst_level([w for r in countable for w in warnings.get(r.delivery_id, [])]),
            "live": any(r.live for r in rs if not r.hidden),
            # finished parts still to transcribe
            "ready": any(not r.live and r.status != "done" and r.delivery_id not in quiet for r in scope),
            "anomalies": list(dict.fromkeys(a["text"] for r in rs if not r.hidden for a in found.get(r.delivery_id, []))),
            "recordings": rviews,
        })
    day_views.sort(key=lambda d: (d["date"], d["recordings"][0]["start"]), reverse=True)
    active = [r for r in recs if not r.duplicate_of and not r.hidden and not _too_short(r, settings)]
    # several sections publish here and you follow yours: count your section's lectures (badges, progress)
    own = active
    if follow:
        own = [r for r in active if mine_map.get(r.delivery_id) is not False]
    # what Home and the course page call lectures: your section's class days, as the Lectures and Weeks tabs show
    # them (a 3-hour class is recorded in 3 parts: "26 lectures" was 7); one with no speech on Panopto is said apart
    own_days = [d for d in day_views if d["parts"] and not (follow and d["mine"] is False)]
    return {
        "id": course.id, "code": course.code, "name": course.name,
        "display_name": prefs.alias or course.name or course.code, "instructor": course.instructor,
        "term": course.term, "section": course.section, "has_panopto": course.has_panopto,
        "cover": _cover_uri(course.cover_url), "lms_progress": course.progress,
        "show_mode": prefs.show_mode if sections else "all", "my_section": my_section,
        "available_sections": sections,
        "counts": {"total": len(own), "new": sum(1 for r in own if r.is_new),
                   "done": sum(1 for r in own if r.status == "done"),
                   "processing": sum(1 for r in own if r.status == "processing"),
                   "done_any": sum(1 for r in active if r.status == "done"),    # any section: enough for Claude
                   "other_sections": len(active) - len(own),
                   "days": len(own_days), "days_done": sum(1 for d in own_days if d["transcribed"]),
                   "days_no_sound": sum(1 for d in own_days if d["no_sound"] and not d["transcribed"])},
        "days": day_views,
    }


def all_course_views(lib: Any, settings: Settings, jobs: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    job_map = {j["id"]: j for j in jobs}
    warnings = lib.all_warnings()
    slides_ids = store.slide_ids()
    out = []
    all_recs = lib.recordings()
    by_course: dict[str, list[Recording]] = defaultdict(list)
    for r in all_recs:
        by_course[r.course_id].append(r)
    counts = lib.content_counts()
    active = lib.courses()
    # a course that left your IEULearn dashboard (dropped, or a term ago) stays while it holds something of yours
    done = lib.transcribed_ids()
    yours = ({r.course_id for r in all_recs if r.delivery_id in done}
             | {cid for cid, n in counts.items() if n["downloaded"]} | lib.chat_and_plan_courses())
    ids = {c.id for c in active}
    earlier = [c for c in lib.courses(True) if c.id not in ids and c.id in yours]
    for c in active + earlier:
        v = course_view(c, by_course.get(c.id, []), settings, job_map, warnings, slides_ids)
        v["inactive"] = c.id not in ids
        v["past"] = v["inactive"] and c.status == "past"           # (IEULearn lists it under Past courses)
        n = counts.get(c.id, {})
        v["content"] = {k: n.get(k, 0) for k in ("materials", "materials_new", "downloaded", "announcements_new")}
        out.append(v)
    return out


# an exam week's topic ("Midterm", "Ara Sınav", "Final Exam"); not "… final report" or "review for the final"
EXAM_TOPIC = re.compile(r"\b(?:mid-?terms?|exams?|examination|vize)\b|ara sınav|final sınav|^\s*final\b|\bfinal exam")
# what talks about an exam rather than holds one, taken out before EXAM_TOPIC looks: a week to get ready for it
# ("Review and Preparation for the Midterm", "Midterm review", "Ara sınav öncesi konuların gözden geçirilmesi") and
# a clinical topic ("Mental Status Exam I", "Physical examination"). "Review and Midterm Exam", "Midterm Exam and
# Review" and "Genel tekrar ve ara sınav" stay exam weeks: a missed exam week is worse than an extra one.
NOT_AN_EXAM = re.compile(
    r"\b(?:review|revision|preparation|preparing|prep|recap)\b(?:\s+\w+){0,3}?\s+(?:for|before|of|to)\s+(?:the\s+)?"
    r"(?:mid-?terms?|finals?|exams?|examinations?)(?:\s+exam(?:ination)?s?)?\b"
    r"|\b(?:mid-?terms?|finals?|exams?)(?:\s+exams?)?\s+(?:review|revision|preparation|prep|recap)\b"
    r"|\b(?:mental status|physical|clinical|neurological|medical|eye|oral)\s+exam(?:ination)?s?\b|\bexamination of\b"
    r"|(?:\b(?:ara|final|vize)\s+)?s[ıi]nav\w*\s+(?:öncesi|oncesi|hazırlı\w*|hazirli\w*)|\bvize\w*\s+(?:öncesi|oncesi|hazırlı\w*)")


def exam_topic(text: str) -> bool:
    """Is this week's topic an exam? Turkish ("ARA SINAV") and English in capitals ("MIDTERM") too; not a week that
    only gets ready for one, or a clinical "exam" (NOT_AN_EXAM)."""
    return bool(text) and any(EXAM_TOPIC.search(NOT_AN_EXAM.sub(" ", low)) for low in (_tr_lower(text), text.lower()))


def material_view(m: dict[str, Any]) -> dict[str, Any]:
    downloaded = bool(m.get("local_path")) and Path(m["local_path"]).is_file()
    return {"id": m["resource_id"], "title": m["title"], "file_name": m.get("file_name") or "", "ext": m.get("ext") or "",
            "kind": m.get("kind") or "", "size": m.get("size") or 0, "downloaded": downloaded,
            "outdated": downloaded and bool(m.get("downloaded_url")) and m.get("downloaded_url") != m.get("url"),
            "is_new": bool(m.get("is_new")), "pages": m.get("pages") or 0, "text": m.get("text_status") or "",
            "gone": bool(m.get("gone"))}


def class_times(days: list[dict[str, Any]], today: dt.date) -> dict[int, str]:
    """When the course meets: weekday (Monday = 0) → when its last class that day ends ("HH:MM"). From the lecture
    days of the last five weeks that are not another section's, or from all of them if there are none."""
    own = [d for d in days if d["mine"] is not False and d["date"] <= today.isoformat()]
    since = (today - dt.timedelta(weeks=5)).isoformat()
    held: list[tuple[dt.date, list[str]]] = []
    for d in [d for d in own if d["date"] >= since] or own:
        ends = [r["end_time"] for r in d["recordings"] if not (r["duplicate"] or r["hidden"] or r["too_short"])]
        if ends:
            held.append((dt.date.fromisoformat(d["date"]), ends))
    weeks: dict[int, set[tuple[int, int]]] = defaultdict(set)
    for date, _ in held:
        weeks[date.weekday()].add(date.isocalendar()[:2])
    every = set().union(*weeks.values()) if weeks else set()
    # not a one-off make-up class: a weekday seen in one week only counts until the course has met in three other
    # weeks without it (early in the term every weekday has been seen once, and a lecture may not have been recorded)
    regular = {wd for wd, ws in weeks.items() if len(ws) >= 2 or len(every - ws) < 3}
    out: dict[int, str] = {}
    for date, ends in held:
        if not regular or date.weekday() in regular:
            out[date.weekday()] = max(out.get(date.weekday(), ""), *ends)
    return out


# Turkey's public holidays on a fixed date: no classes (New Year, 23 April, 1 May, 19 May, 15 July, 30 August, 29 October)
HOLIDAYS = {(1, 1), (4, 23), (5, 1), (5, 19), (7, 15), (8, 30), (10, 29)}
# Ramazan and Kurban Bayramı move every year (Diyanet's calendar): add the next academic year's here
FEASTS = [("2026-03-20", 3), ("2026-05-27", 4), ("2027-03-09", 3), ("2027-05-16", 4)]
FEAST_DAYS = {(dt.date.fromisoformat(d) + dt.timedelta(days=i)).isoformat() for d, n in FEASTS for i in range(n)}


def holiday(d: dt.date) -> bool:
    """A public holiday in Turkey: no classes that day."""
    return (d.month, d.day) in HOLIDAYS or d.isoformat() in FEAST_DAYS


def week_classes(start: str, end: str, times: dict[int, str]) -> list[dict[str, str]]:
    """The course's classes in a week by its usual schedule, public holidays left out: [{"date", "end"}] (none if
    the schedule is unknown)."""
    if not (start and times):
        return []
    a, b = dt.date.fromisoformat(start), dt.date.fromisoformat(end or start)
    days = (a + dt.timedelta(days=i) for i in range((b - a).days + 1))
    return [{"date": d.isoformat(), "end": times[d.weekday()]} for d in days if d.weekday() in times and not holiday(d)]


def classes_over(classes: list[dict[str, str]], now: dt.datetime) -> bool:
    """Are all these class hours behind us? A quiz announced for a week is held in one of them, so then it is done.
    False without any classes: then only the week's end says so."""
    today, hm = now.date().isoformat(), f"{now:%H:%M}"
    return bool(classes) and all(c["date"] < today or (c["date"] == today and c["end"] <= hm) for c in classes)


def _own_weeks(m: dict[str, Any]) -> set[int]:
    """The weeks a file's own name or title gives, as a chat's files are matched (chatslides.own_weeks)."""
    from .chatslides import own_weeks

    return own_weeks(m)


def course_detail(lib: Any, settings: Settings, course_id: str, jobs: Iterable[dict[str, Any]] = (),
                  now: Optional[dt.datetime] = None) -> Optional[dict[str, Any]]:
    """Everything about one course, organised by week: topics (EN/TR), readings, materials, lecture days, when the
    course meets in each week, and what the lecturer said about quizzes, exams, projects and homework (``alerts``)."""
    from .alerts import course_alerts
    from .chatslides import _UNIT_WEEKS, title_weeks
    from .materials import unit_week, week_of, week_starts

    course = lib.get_course(course_id)
    if course is None:
        return None
    info = lib.course_info(course_id)
    ov, syl = info.get("overview") or {}, info.get("syllabus") or {}
    units = lib.units(course_id)
    mats: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for m in lib.materials(course_id):
        mats[m["unit_id"]].append(material_view(m))
    recs = lib.recordings(course_id)
    cv = course_view(course, recs, settings, {j["id"]: j for j in jobs}, lib.all_warnings(), store.slide_ids())
    first = week_starts(units)
    if first is None and course.term:      # no dates in this plan: the term's calendar from the other courses
        start = lib.term_start(course.term)
        if start:
            first = dt.date.fromisoformat(start)
            first -= dt.timedelta(days=first.weekday())
    tr_topics = {w["no"]: w["topic"] for w in ov.get("weeks", [])}
    en_weeks = {w["no"]: w for w in syl.get("weeks", [])}
    weeks: dict[int, dict[str, Any]] = {}

    def week(n: int) -> dict[str, Any]:
        if n not in weeks:
            start = first + dt.timedelta(days=7 * (n - 1)) if first else None
            topic_en = (en_weeks.get(n) or {}).get("subject", "")
            topic_tr = tr_topics.get(n, "")
            exam = next((x for x in (topic_en, topic_tr) if exam_topic(x)), "")
            weeks[n] = {"no": n, "start": start.isoformat() if start else "",
                        "end": (start + dt.timedelta(days=6)).isoformat() if start else "",
                        "topic_en": topic_en, "topic_tr": topic_tr,
                        "readings": (en_weeks.get(n) or {}).get("preparation", ""),
                        "exam": bool(exam), "exam_label": exam, "units": [], "days": []}
        return weeks[n]

    general = []
    for u in units:
        n = unit_week(u, first)
        item = {"id": u["unit_id"], "title": u["title"], "readings": u.get("readings", ""), "materials": mats.get(u["unit_id"], [])}
        if n is None:
            general.append(item)
            continue
        w = week(n)
        if u.get("start_date"):
            w["start"], w["end"] = u["start_date"], u.get("end_date") or w["end"]
        if not w["readings"] and u.get("readings"):
            w["readings"] = u["readings"]
        if u.get("start_date"):
            w["units"].append(item)
            continue
        # undated: in the weeks its title names ("Hafta 6 ve 7": week 7's too), and each file also in the weeks its
        # own name gives, as chat's slides are (MATH 250's "Konu 10" holds "MATH_250_Week 11.pdf", taught in week 11)
        named = bool(_UNIT_WEEKS.search(u["title"] or ""))
        unit_weeks = [n] + [x for x in title_weeks(u["title"]) if x != n] if named else [n]
        at: dict[int, list[dict[str, Any]]] = {x: [] for x in unit_weeks}
        for m in item["materials"]:
            own = _own_weeks(m)
            for x in sorted(own | set(unit_weeks)) if named else sorted(own) or unit_weeks:
                at.setdefault(x, []).append(m)
        for x, ms in sorted(at.items()):            # (a unit without files: at its own week)
            if ms or not item["materials"]:
                week(x)["units"].append(item if len(ms) == len(item["materials"]) else {**item, "materials": ms})
    known_units = {u["unit_id"] for u in units}
    stray = [m for uid, ms in mats.items() if uid not in known_units and uid != ANN_UNIT for m in ms]
    if stray:          # downloaded files of a week that left the plan: still on your PC, so still listed
        general.append({"id": "", "title": t("No longer in the weekly plan"), "readings": "", "materials": stray})
    if mats.get(ANN_UNIT):        # the files attached to its announcements: a group of their own
        general.append({"id": ANN_UNIT, "title": t("From announcements"), "readings": "", "materials": mats[ANN_UNIT]})
    for n in sorted(set(tr_topics) | set(en_weeks)):
        week(n)
    for d in cv["days"]:
        n = week_of(dt.date.fromisoformat(d["date"]), first) if first else None
        if n:
            week(n)["days"].append({k: d[k] for k in ("day_key", "date", "label", "kind_label", "parts", "transcribed",
                                                     "any_done", "no_sound", "is_new", "mine", "duration_s", "live",
                                                     "ready", "anomalies")})
    alerts = _label_other_sections(course_alerts(lib, course_id, recs), cv["days"])
    alerts = {**alerts, "plan": dated_plan(alerts.get("plan") or [], first)}
    for e in alerts.get("plan") or []:              # a week an assessment was announced for, also without a unit
        if e.get("week") and first:
            week(e["week"])
    now = now or dt.datetime.now()
    today = now.date().isoformat()
    times = class_times(cv["days"], now.date())
    out_weeks = sorted(weeks.values(), key=lambda w: w["no"])
    for w in out_weeks:
        w["current"] = bool(w["start"]) and w["start"] <= today <= (w["end"] or w["start"])
        w["past"] = bool(w["end"]) and w["end"] < today
        w["classes"] = week_classes(w["start"], w["end"], times)
    anns = [{"id": a["id"], "title": a["title"], "author": a["author"], "posted_at": a["posted_at"], "body": a["body"],
             "is_new": bool(a["is_new"])} for a in lib.announcements(course_id)]
    all_mats = [m for ms in mats.values() for m in ms]
    from .materials import week_of

    asgs = [assignment_view(a, week_of(dt.date.fromisoformat(a["due"][:10]), first) if a["due"] and first else None)
            for a in lib.assignments(course_id)]
    return {
        **{k: cv[k] for k in ("id", "code", "name", "display_name", "instructor", "term", "section", "has_panopto",
                              "counts", "my_section", "cover", "lms_progress")},
        "description": syl.get("description") or ov.get("description", ""),
        "objectives": syl.get("objectives", ""),
        "outcomes_en": syl.get("outcomes", []),
        "outcomes_tr": [o["text"] for o in sorted(ov.get("outcomes", []), key=lambda o: o["no"])],
        "grading": syl.get("grading") or [{"activity": g["name"], "number": g["count"], "weight": g["weight"]}
                                          for g in ov.get("grading", [])],
        "syllabus": {k: syl.get(k, "") for k in ("url", "name", "semester", "ects", "prerequisites", "language",
                                                 "coordinator", "lecturers", "assistants", "textbooks", "readings")},
        "weeks": out_weeks, "general": general, "announcements": anns,
        "materials": {"total": len(all_mats), "downloaded": sum(m["downloaded"] for m in all_mats),
                      "new": sum(m["is_new"] for m in all_mats)},
        "content_at": info.get("content_at", ""),
        "alerts": alerts,
        "assignments": asgs,                     # IEULearn's assignments: when each is due, and whether it was sent
    }


def assignment_view(a: dict[str, Any], week: Optional[int] = None) -> dict[str, Any]:
    """An assignment as the pages show it: the day ("due") and the time ("time", "" when IEULearn gave only the
    day), whether it was sent, and where it opens on IEULearn."""
    due = a.get("due") or ""
    return {"id": a["asg_id"], "title": a["title"], "due": due[:10], "time": due[11:16], "done": bool(a["done"]),
            "state": a.get("state") or "", "url": a.get("url") or "", "week": week}


def dated_plan(plan: list[dict[str, Any]], first: Optional[dt.date]) -> list[dict[str, Any]]:
    """The assessment plan with each one an announcement gave a day for in that day's week (copies: the plan is the
    heads-up's, kept between calls). Without week 1's date the weeks stay as announced."""
    from .materials import week_of

    out = []
    for e in plan:
        e = dict(e)
        if e.get("date") and first:
            e["week"] = week_of(dt.date.fromisoformat(e["date"]), first) or e.get("week")
        out.append(e)
    return out


def _label_other_sections(alerts: dict[str, Any], days: list[dict[str, Any]]) -> dict[str, Any]:
    """Mark heads-ups heard in another section's lecture ("Section 1"): its dates may differ for your section.
    (Matched by the recording's viewer link: one class day can hold several sections' recordings.)"""
    other: dict[str, tuple[str, str]] = {}            # viewer link -> (shown, English for the AI and exports)
    for d in days:
        for r in d["recordings"]:
            if r["mine"] is False and r.get("viewer_url"):
                tag = r["section_tag"] or parse_section_tag(r["name"])
                other[r["viewer_url"]] = ((t("Section {tag}", tag=tag), f"Section {tag}") if tag
                                          else (t("Other section"), "Other section"))
    if not other:
        return alerts
    items = [dict(it, other_section=other[it["viewer_url"]][0], other_section_en=other[it["viewer_url"]][1])
             if it.get("viewer_url") in other else it for it in alerts.get("items", [])]
    return {**alerts, "items": items}


def day_detail(lib: Any, settings: Settings, day_key: str, files: dict[str, str]) -> Optional[dict[str, Any]]:
    course_id, date_s, kind = day_key.split("|")
    course = lib.get_course(course_id)
    if course is None:
        return None
    recs = lib.day_recordings(day_key)
    if not recs:
        return None
    ids = [r.delivery_id for r in recs]
    trans = store.load_transcripts(ids)
    slides = {i: store.load_slides(i) for i in ids}
    warnings = {i: lib.warnings(i) for i in ids}
    found = course_anomalies(lib.recordings(course_id), warnings, min_s=settings.min_duration_s)
    parts = part_views(recs, trans, slides, warnings)
    shares = language_shares([p.transcript for p in parts if p.transcript])
    marks: dict[str, dict[float, dict[str, Any]]] = defaultdict(dict)       # the student's own marks
    for m in lib.marks(ids):
        marks[m["delivery_id"]][m["t"]] = m
    highlights, out_parts, my_marks = [], [], []
    for p in parts:
        sl = {s.index: s for s in p.slides}
        paras = []
        own = marks.get(p.rec.delivery_id, {})
        # the screen timeline (screens.py): each picture's caption, "Slide 12 · 34:10–36:40 · title"
        screens = ((store.load_screens(p.rec.delivery_id) or {}).get("screens") or {}) if p.slides else {}
        for para, span in zip(p.paragraphs, para_spans(p)):
            s = sl.get(para.slide) if para.slide is not None else None
            w, h = jpeg_size(s.path) if s and s.path else (0, 0)
            m = own.get(round(para.start, 1))
            scr = screens.get(str(para.slide)) or {} if s else {}
            paras.append({"t": round(para.start, 1), "e": round(para.end, 1), "ts": format_ts(para.start),
                          "text": para.text, "lang": para.lang, "tag": lang_tag(para.lang), "star": para.star,
                          "tags": para.tags, "uncertain": para.uncertain, "slide": para.slide,
                          "slide_image": Path(s.path).as_uri() if s and s.path else None, "slide_w": w, "slide_h": h,
                          "slide_t0": round(span.t, 1) if s and span else None,
                          "slide_range": f"{format_ts(span.t)}–{format_ts(span.end)}" if s and span else "",
                          "slide_title": " ".join(str(scr.get("title") or "").split()),
                          "mark": {"note": m["note"]} if m else None})
            if para.star:
                highlights.append({"part": p.index, "t": round(para.start, 1), "ts": format_ts(para.start),
                                   "text": excerpt(para.text, para.tags),
                                   "tags": [t(LABELS[x]) if x in LABELS else x for x in para.tags],   # English in files
                                   "viewer_url": p.rec.viewer_url})
        for at, m in sorted(own.items()):
            my_marks.append({"part": p.index, "id": p.rec.delivery_id, "t": at, "ts": format_ts(at),
                             "text": m["text"], "note": m["note"]})
        out_parts.append({
            "index": p.index, "id": p.rec.delivery_id, "title": t("Part {n}", n=p.index), "time_range": time_range(p.rec),
            "viewer_url": p.rec.viewer_url, "status": "live" if p.rec.live else p.rec.status, "paragraphs": paras,
            "anomalies": found.get(p.rec.delivery_id, []),
            "slides": len({s.index for s in p.slides}),
            "warnings": [{"level": w.get("level"), "message": w.get("message")} for w in p.warnings],
        })
    d = dt.date.fromisoformat(date_s)
    return {
        "day_key": day_key, "title": f"{course.code} · {t(kind_label(kind))}", "date_label": date_long(d),
        "subtitle": " · ".join(x for x in (course.name, course.instructor) if x),
        "course": {"id": course.id, "code": course.code, "name": course.name, "instructor": course.instructor},
        "kind": kind, "languages": {k: round(v, 3) for k, v in shares.items()},
        "highlights": highlights, "parts": out_parts, "files": files, "marks": my_marks,
    }
