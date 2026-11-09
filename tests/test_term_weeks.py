"""A course's weeks: a term whose lecturers never date their weekly plans (week 1 from the term's first lecture
recording), files placed by the week their own names give, exam weeks from the syllabus (and what only talks about an
exam), and the syllabus exam weeks in Home's Coming up and the weekly digest."""

import datetime as dt

from studia import store
from studia.calendar_export import assessment_events
from studia.config import Settings
from studia.coursesite import Unit
from studia.digest import weekly_digest
from studia.domain import Course, Recording, Segment, Transcript
from studia.library import Library
from studia.views import course_detail


def _rec(cid, i, when, minutes=50, **kw):
    return Recording(delivery_id=f"{cid}-{i}", course_id=cid, folder_id="f", folder_path="", name=f"Lecture {i}",
                     start=when, duration_s=minutes * 60, viewer_url=f"https://v/{cid}-{i}", **kw)


def _transcribe(lib, rec):
    t = Transcript(delivery_id=rec.delivery_id, segments=[Segment(0, 30, "Today we look at limits and continuity.", "en", 0.9),
                                                          Segment(40, 70, "Remember the definition of a limit.", "en", 0.9)],
                   duration_s=rec.duration_s, speech_s=60, language_seconds={"en": 60}, model="m", device="cpu",
                   engine_version="t", created_at="2026-11-01T12:00:00")
    lib.save_transcript(t, store.save_transcript(t))
    lib.set_status(rec.delivery_id, "done")


def _undated_term(tmp_path):
    """The second profile's term: six courses, none with a date in its weekly plan ("Week 1", "Hafta 2", "Konu 3")."""
    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="m", title="2026-1 MATH 111 Calculus 1", code="MATH 111", name="Calculus", term="T"),
                        Course(id="p", title="2026-1 PSY 307 Psychology 1", code="PSY 307", name="Psychology", term="T")])
    lib.merge_units("m", [Unit(f"m{n}", n, f"Week {n}") for n in range(1, 9)], first_sync=True)
    lib.merge_units("p", [Unit(f"p{n}", n, f"Hafta {n}") for n in range(1, 9)], first_sync=True)
    lib.set_course_info("m", syllabus={"weeks": [{"no": n, "subject": "Midterm" if n == 8 else f"Topic {n}"}
                                                 for n in range(1, 9)]})
    monday = dt.datetime(2026, 9, 21, 9, 30)               # the term's first lecture: Monday 21 September
    recs = [_rec("m", n, monday + dt.timedelta(weeks=n - 1)) for n in range(1, 8)]
    recs.append(_rec("m", 99, dt.datetime(2025, 10, 6, 9, 30)))           # last year's lecture, copied into the folder
    recs.append(_rec("m", 98, dt.datetime(2026, 9, 7, 14, 0), minutes=2))   # the lecturer's test, before the term
    recs.append(_rec("m", 97, dt.datetime(2026, 9, 9, 10, 0)))             # one you hid
    lib.merge_recordings("m", recs, first_sync=True)
    lib.set_hidden("m-97", True)
    lib.merge_recordings("p", [_rec("p", n, monday + dt.timedelta(weeks=n - 1, days=2)) for n in range(1, 8)],
                         first_sync=True)
    for r in recs[:7]:
        _transcribe(lib, r)
    return lib


def test_a_term_without_dated_plans_counts_its_weeks_from_its_first_lecture(tmp_path):
    from studia.plans import Scope, gather as plan_gather
    from studia.studypack import gather as pack_gather

    lib = _undated_term(tmp_path)
    assert lib.term_start("T") == "2026-09-21"            # not last year's copy, the test or the hidden one
    now = dt.datetime(2026, 11, 9, 12, 0)
    d = course_detail(lib, Settings(), "m", now=now)
    weeks = {w["no"]: w for w in d["weeks"]}
    assert [x["date"] for x in weeks[1]["days"]] == ["2026-09-21"] and [x["date"] for x in weeks[7]["days"]] == ["2026-11-02"]
    assert sum(len(w["days"]) for w in d["weeks"]) == 7
    assert weeks[8]["current"] and weeks[8]["exam"] and weeks[7]["past"]
    p = course_detail(lib, Settings(), "p", now=now)
    assert [x["date"] for w in p["weeks"] if w["no"] == 2 for x in w["days"]] == ["2026-09-30"]

    dg = weekly_digest(lib, Settings(), -1, now=now)       # last week, 2-8 November
    lectures = {c["code"]: [x["date"] for x in c["lectures"]] for c in dg["courses"]}
    assert lectures == {"MATH 111": ["2026-11-02"], "PSY 307": ["2026-11-04"]}
    assert any(x["kind"] == "transcribe" and x["course"] == "PSY 307" for x in dg["todo"])

    assert len(plan_gather(lib, Settings(), "m", Scope(kind="prep", weeks=list(range(1, 8))))["days_used"]) == 7
    assert len(pack_gather(lib, Settings(), "m", Scope(kind="pack", weeks=list(range(1, 8))))["lectures"]) == 7

    events = assessment_events(lib, Settings(), today=now.date())
    assert [(e["course"], e["name"], e["start"]) for e in events] == [("MATH 111", "Midterm", "2026-11-09")]


def test_a_term_with_a_dated_plan_keeps_its_dates(tmp_path):
    lib = _undated_term(tmp_path)
    lib.merge_units("p", [Unit("p1", 0, "Hafta 1", dt.date(2026, 9, 23), dt.date(2026, 9, 29))], first_sync=False)
    assert lib.term_start("T") == "2026-09-23"
    lib2 = Library(tmp_path / "other.db")                  # no title says the term: any recording counts
    lib2.upsert_courses([Course(id="x", title="EEE 215", code="EEE 215", name="X", term="U")])
    lib2.merge_recordings("x", [_rec("x", 1, dt.datetime(2026, 2, 16, 9, 0))], first_sync=True)
    assert lib2.term_start("U") == "2026-02-16" and lib2.term_start("nothing") is None


def test_a_syllabus_exam_week_no_announcement_covers_is_in_the_digest(tmp_path):
    from studia.digest import digest_prompt

    lib = _undated_term(tmp_path)                          # MATH 111: the syllabus' Midterm in week 8, no plan posted
    dg = weekly_digest(lib, Settings(), 0, now=dt.datetime(2026, 11, 9, 12, 0))
    (c,) = [c for c in dg["courses"] if c["code"] == "MATH 111"]
    assert [(e["name"], e["week"], e["this_week"], e["done"]) for e in c["coming"]] == [("Midterm", 8, True, False)]
    (todo,) = [x for x in dg["todo"] if x["kind"] == "prepare"]
    assert todo["course"] == "MATH 111" and "name" not in todo        # (Get ready reads the plan: Make a plan instead)
    assert "TO DO: MATH 111: Midterm is this week" in digest_prompt(dg)
    before = weekly_digest(lib, Settings(), -1, now=dt.datetime(2026, 11, 9, 12, 0))    # the week before: coming
    assert [(e["name"], e["this_week"]) for e in next(c for c in before["courses"] if c["code"] == "MATH 111")["coming"]] \
        == [("Midterm", False)]
