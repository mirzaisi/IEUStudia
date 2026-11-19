"""4.2.0 QA of the Exam room and its ways in (v42-qroom): what the lecturer said is one list for the room and the exam
kit's brief; a moment said there isn't repeated under "Stressed"; a stopped or failed quiz in the room can be resumed
as on the Practice tab; the day set in a room ("Set the exact day") is the Calendar's, the .ics file's and the
reminders' day too, and the Calendar lists the quizzes and exams the room has (a syllabus exam week folded into an
announced exam, a two-week exam period as one), each opening its own room. No network, no AI."""

import datetime as dt

import pytest

from studia import agenda, agent, calendar_export, examroom, kit, quizzes, stressed, views
from studia.config import Settings
from studia.coursesite import Announcement, Unit
from studia.domain import Course
from studia.library import Library
from test_examroom_page import node, run

START = dt.date(2026, 9, 14)                  # week 1's Monday


def _wk(n, day=0):
    return (START + dt.timedelta(weeks=n - 1, days=day)).isoformat()


# ------------------------------------------------------------------ what the lecturer said: one list
def _said_course():
    """Quiz 2 (week 5), the midterm (week 8) and Quiz 3 (week 10) as announced, the final from the syllabus (week 16);
    heads-ups newest first, as alerts.course_alerts gives them."""
    weeks = [{"no": n, "start": _wk(n), "end": _wk(n, 6), "exam": n == 16, "exam_label": "Final Exam" if n == 16 else "",
              "past": False, "classes": []} for n in range(1, 17)]
    plan = [{"kind": k, "no": no, "name": name, "week": w, "los": [], "sources": [{"id": src, "title": src}], "date": "",
             "time": "", "covers": [], "covers_from": ""}
            for k, no, name, w, src in (("quiz", 2, "Quiz 2", 5, "a2"), ("midterm", 0, "Midterm", 8, "am"),
                                        ("quiz", 3, "Quiz 3", 10, "a3"))]

    def said_in(n, cat, text):
        return {"source": "lecture", "category": cat, "categories": [cat], "text": text, "date": _wk(n, 2) + "T10:00",
                "day_key": f"c|{_wk(n, 2)}|lecture", "part": 1, "t": 60.0 * n, "ts": "01:00"}

    items = [said_in(12, "exam", "This will be in the exam, learn it."),
             said_in(9, "quiz", "We have a quiz next week, study the exponential."),
             said_in(9, "final", "The midterm and the quizzes don't count in the final exam."),
             {"source": "announcement", "category": "quiz", "categories": ["quiz"], "announcement_id": "a3",
              "title": "About Quiz 3", "text": "Quiz 3 will cover weeks 9 and 10.", "date": _wk(8, 1) + "T09:00"},
             said_in(7, "exam", "This will be in the exam."),
             said_in(6, "important", "Important: the quiz rules."),
             said_in(4, "quiz", "Next week we have a quiz on counting."),
             said_in(3, "quiz", "Quiz 3 will be about the exponential distribution.")]
    return {"id": "c", "weeks": weeks, "alerts": {"plan": plan, "items": items}}


def test_what_the_lecturer_said_is_about_the_one_it_names_or_the_next_one():
    """The room listed every heads-up of its kind in its weeks (a final got the midterm's "the exam", a quiz the
    final's) and the kit's brief another list; now both have one: by its number, else the next one after it was said."""
    d = _said_course()
    got = {e["key"]: [a["text"] for a in examroom.said(d, e)] for e in quizzes.of(d)}
    assert got["quiz:2"] == ["Next week we have a quiz on counting."]
    assert got["quiz:3"] == ["We have a quiz next week, study the exponential.", "Quiz 3 will cover weeks 9 and 10.",
                             "Quiz 3 will be about the exponential distribution."]          # its number: said early
    assert got["midterm:0"] == ["This will be in the exam."]                       # "the exam" before the midterm
    assert got["syllabus:Final Exam:16"] == ["This will be in the exam, learn it.",       # after it: the final's
                                             "The midterm and the quizzes don't count in the final exam."]
    # the exam kit's brief says the same, as its lines
    for e in quizzes.of(d):
        lines = kit._said(d, e, {})
        assert len(lines) == len(got[e["key"]]) and all(x in ln for x, ln in zip(got[e["key"]], lines))


def test_a_moment_said_about_it_is_not_repeated_under_stressed(monkeypatch):
    d = _said_course()
    quiz3 = next(e for e in quizzes.of(d) if e["key"] == "quiz:3")
    heard = examroom.said(d, quiz3)
    day = f"c|{_wk(9, 2)}|lecture"
    moments = [{"id": "s1", "kind": "exam", "text": "a quiz next week", "day_key": day, "day": _wk(9, 2), "part": 1, "t": 552.0},
               {"id": "s2", "kind": "stressed", "text": "the memoryless property", "day_key": day, "day": _wk(9, 2), "part": 1,
                "t": 1300.0}]
    monkeypatch.setattr(stressed, "course_items", lambda lib, cid: moments)
    monkeypatch.setattr(stressed, "ranked", lambda items, nos, day_week: list(items))
    d["weeks"][8]["days"] = [{"date": _wk(9, 2)}]
    assert [m["id"] for m in examroom.stressed_moments(None, "c", d, quiz3, heard)] == ["s2"]       # (9 min, 30 s apart)
    assert [m["id"] for m in examroom.stressed_moments(None, "c", d, quiz3)] == ["s1", "s2"]


def test_the_export_dialogs_for_says_the_day_the_room_says():
    """The room said "Fri 11 Dec" (its week's class day) and the export dialog's "For" said 7 Dec (the week's start)."""
    d = _said_course()
    d["weeks"][9]["classes"] = [{"date": _wk(10, 4), "end": "12:00"}]                 # (week 10's Friday class)
    on = {e["key"]: e["on"]["date"] for e in quizzes.assessments(d)}
    got = {c["key"]: c["date"] for c in kit.choices(d)}
    assert got["quiz:3"] == on["quiz:3"] == _wk(10, 4) and got["quiz:2"] == _wk(5)


# ------------------------------------------------------------------ the day set in a room: Calendar, .ics, reminders
@pytest.fixture
def lib(tmp_path):
    """MATH 240: the midterm announced for week 8 (the syllabus says week 9: the same exam, moved), Quiz 3 for week
    12; the syllabus's final is a two-week exam period (weeks 15 and 16). No lectures, so no class days."""
    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="c", title="2026-1 MATH 240 P 1", code="MATH 240", name="Probability", term="T")])
    lib.merge_units("c", [Unit(f"u{n}", n, f"Hafta {n}", START + dt.timedelta(weeks=n - 1),
                               START + dt.timedelta(weeks=n - 1, days=6)) for n in range(1, 17)], first_sync=True)
    lib.set_course_info("c", syllabus={"weeks": [{"no": 9, "subject": "Midterm Exam"}, {"no": 15, "subject": "Final Exam"},
                                                 {"no": 16, "subject": "Final Exam"}]})
    lib.merge_announcements("c", [
        Announcement("a1", "Quizzes", "Dr", dt.datetime(2026, 9, 20, 9), "Quiz 3\nWeek 12"),
        Announcement("a2", "Midterm", "Dr", dt.datetime(2026, 10, 20, 9), "The midterm will be held in week 8.")],
        first_sync=True)
    return lib


NOW = dt.datetime(2026, 11, 27, 10, 0)


def _exams(cal):
    return {i["id"]: i for i in cal["items"] if i["kind"] in ("quiz", "midterm", "final", "exam")}


def test_the_calendar_lists_what_the_exam_room_has_and_each_opens_its_room(lib):
    """The Calendar listed the syllabus's week-9 "Midterm Exam" beside the midterm announced for week 8 (two
    midterms, two reminders), and a two-week final as two items; Home's Coming up opened such a week by its name:
    "no longer in the course's plan"."""
    cal = agenda.build(lib, Settings(), now=NOW)
    items = _exams(cal)
    assert sorted(items) == ["c-midterm-0", "c-quiz-3", "c-syllabus-week15"]
    final = items["c-syllabus-week15"]
    assert (final["week"], final["key"], final["sure"]) == ({"no": 15, "start": _wk(15), "end": _wk(16, 6)},
                                                            "syllabus:Final Exam:16", "syllabus")
    d = views.course_detail(lib, Settings(), "c", (), NOW)
    assert {i["key"] for i in items.values()} == {e["key"] for e in d["assessments"]}
    assert all(examroom.find(d, i["key"])["key"] == i["key"] for i in items.values())
    events = calendar_export.assessment_events(lib, Settings(), NOW.date())
    assert [(e["uid"], e["key"], e["start"], e["end"]) for e in events] == [
        ("c-quiz-3", "quiz:3", _wk(12), _wk(12, 6)), ("c-syllabus-week15", "syllabus:Final Exam:16", _wk(15), _wk(16, 6))]
    assert events[1]["notes"][0] == "Week 15–16 in the syllabus: Final Exam."


def test_the_day_set_in_the_room_is_the_calendars_the_files_and_the_reminders(lib):
    """'Set the exact day' changed the room, the course page's chip and the kit's brief, but the Calendar, the .ics
    file and the reminders kept the week."""
    examroom.set_day(lib, "c", "quiz:3", "2026-12-03")
    examroom.set_day(lib, "c", "syllabus:Final Exam:16", "2026-12-30")
    items = _exams(agenda.build(lib, Settings(), now=NOW))
    quiz, final = items["c-quiz-3"], items["c-syllabus-week15"]
    assert (quiz["date"], quiz["sure"], quiz["key"], quiz["week_no"]) == ("2026-12-03", "set", "quiz:3", 12)
    assert (final["date"], final["sure"], final["week_no"]) == ("2026-12-30", "set", 16)
    events = calendar_export.all_events(lib, Settings(), NOW.date())
    q = next(e for e in events if e["uid"] == "c-quiz-3")
    assert (q["start"], q["end"], q["yours"]) == ("2026-12-03", "2026-12-03", True)
    assert q["notes"][0] == "On Thu 3 Dec 2026, the day you set in its Exam room."
    text = calendar_export.ics(events, dt.datetime(2026, 11, 27, 9, 0, tzinfo=dt.timezone.utc)).replace("\r\n ", "")
    assert "DTSTART;VALUE=DATE:20261203" in text.split("UID:c-quiz-3@ieustudia")[1].split("END:VEVENT")[0]
    # reminded two days before that day (not before its week), in words that say whose day it is; a click: its room
    assert agent.reminders(events, dt.datetime(2026, 11, 28, 18, 0), {}, {"exams": 2}) is None
    seen: dict = {}
    title, body, payload = agent.reminders(events, dt.datetime(2026, 12, 1, 18, 0), seen, {"exams": 2})
    assert body == "MATH 240: Quiz 3 is on Thu 3 Dec, the day you set." and payload == "ready:c:quiz:3"
    assert agent.reminders(events, dt.datetime(2026, 12, 2, 9, 0), seen, {"exams": 2}) is None      # once
    # its day forgotten: the week again
    examroom.set_day(lib, "c", "quiz:3", "")
    assert _exams(agenda.build(lib, Settings(), now=NOW))["c-quiz-3"]["sure"] == "week"


def test_the_digest_opens_the_room_of_a_syllabus_exam_and_says_its_set_day(lib):
    """The digest's to-do for a syllabus's exam this week offered "Make a plan" instead of its Exam room, listed a
    syllabus week folded into an announced exam as an exam of its own, and kept the week of a day set in the room."""
    from studia.digest import digest_prompt, weekly_digest

    dg = weekly_digest(lib, Settings(), 0, now=dt.datetime(2026, 11, 4, 10, 0))          # week 8: the midterm
    assert [e["name"] for e in dg["courses"][0]["coming"]] == ["Midterm"]               # (not week 9's "Midterm Exam")
    dg = weekly_digest(lib, Settings(), 0, now=dt.datetime(2026, 12, 23, 10, 0))         # week 15: the exam period
    todo = next(x for x in dg["todo"] if x["kind"] == "prepare")
    assert (todo["name"], todo["key"]) == ("Final Exam", "syllabus:Final Exam:16")
    examroom.set_day(lib, "c", "quiz:3", "2026-12-03")
    dg = weekly_digest(lib, Settings(), 0, now=dt.datetime(2026, 12, 1, 10, 0))           # week 12
    quiz = next(e for e in dg["courses"][0]["coming"] if e["key"] == "quiz:3")
    assert (quiz["set"], quiz["this_week"], quiz["done"]) == ("2026-12-03", True, False)
    assert "Quiz 3 in week 12 (2026-11-30–2026-12-06), on 2026-12-03 (the day the student set)" in digest_prompt(dg)


# ------------------------------------------------------------------ the page
@node
def test_the_calendars_exam_room_button_opens_the_room_by_its_key_and_says_whose_day_it_is(tmp_path):
    out = run(tmp_path, """
  const today = inDays(0);
  S.cal.data = { today, now: "12:00", term: { start: inDays(-60), end: inDays(60) }, reminders: {}, courses: [{ id: "c", code: "MATH 240", name: "P", section: "", twin: false }],
    items: [{ id: "c-quiz-3", key: "quiz:3", kind: "quiz", course_id: "c", code: "MATH 240", title: "Quiz 3", date: inDays(3), time: "", week_no: 12, sure: "set", los: [],
      source: { type: "announcement", id: "a2" } },
      { id: "c-syllabus-week15", key: "syllabus:Final Exam:16", kind: "exam", course_id: "c", code: "MATH 240", title: "Final Exam", date: "",
        week: { no: 15, start: inDays(20), end: inDays(33) }, days: [], week_no: 15, sure: "syllabus", los: [], source: { type: "syllabus", url: "" } }] };
  S.cal.view = "list"; S.view = { name: "calendar" };
  out.html = St.views.calendar(S.view); out.text = text(out.html);
""")
    assert 'data-act="ready-open" data-cid="c" data-key="quiz:3"' in out["html"]
    assert 'data-act="ready-open" data-cid="c" data-key="syllabus:Final Exam:16"' in out["html"]
    assert "The day you set in its Exam room" in out["text"]


@node
def test_the_course_page_says_the_day_set_in_the_room(tmp_path):
    """The room and its chip said the day the student set; the Coming up strip and the week card's pill kept the week."""
    out = run(tmp_path, """
  const day = inDays(4), w12 = { no: 12, start: inDays(-2), end: inDays(4), current: true, past: false, exam: false, topic_en: "Bayes",
    topic_tr: "", units: [], days: [], classes: [] };
  S.cd = { c: { id: "c", code: "MATH 240", general: [], outcomes_en: [], outcomes_tr: [], grading: [], syllabus: {}, materials: { total: 0, downloaded: 0, new: 0 },
    announcements: [], counts: {}, weeks: [w12],
    alerts: { items: [], counts: {}, plan: [{ kind: "quiz", no: 3, name: "Quiz 3", week: 12, los: [], sources: [{ id: "a2", title: "About Quiz 3" }] }] },
    assessments: [{ ...QUIZ3, on: { date: day, time: "", from: "yours", others: [] } }] } };
  S.view = { name: "course", courseId: "c" };
  out.page = text(St.views.course(S.view)); out.day = t.date(day, "dmw");
  out.rel = St.planRel(S.cd.c, S.cd.c.alerts.plan[0]);
""")
    assert f"Quiz 3 {out['day']}" in out["page"]                          # Coming up
    assert out["page"].count(f"Quiz 3 · {out['day']}") == 1                # the week card's pill
    assert out["rel"]["date"] == out["rel"]["days"][0] and out["rel"]["time"] == ""


@node
def test_a_stopped_or_failed_quiz_in_the_room_can_be_resumed_and_stressed_is_short(tmp_path):
    out = run(tmp_path, """
  const base = room(QUIZ3), q = (id, status, extra) => ({ id, course_id: "c", title: "", status, weeks: [9, 10], days: [], focus: "Quiz 3",
    questions: 10, tries: 0, last: null, best: null, message: "", error: "", ...extra });
  const quizzes = [base.practice.quizzes[0], q("qp", "paused", { message: "Stopped: resume to continue" }),
    q("qe", "error", { error: "Claude's usage limit was reached." }), q("qr", "running", { message: "Writing questions…" })];
  const many = Array.from({ length: 7 }, (_, i) => ({ id: "s" + i, kind: "stressed", text: "Moment " + i, hl: [0, 0], day_key: DAY,
    day: "2026-11-16", part: 1, t: 100 * i, ts: "01:00", n: 1, score: 1, week: 10 }));
  St.mocks.exam_room = () => ({ ok: true, room: room(QUIZ3, { practice: { ...base.practice, quizzes }, stressed: many }) });
  A["ready-open"]({ dataset: { cid: "c", key: "quiz:3" } }); await tick();
  out.html = view(); out.text = text(view());
  A["er-more"](); out.more = text(view());
  A["ready-open"]({ dataset: { cid: "c", key: "quiz:3" } }); await tick(); out.again = text(view());
""")
    h, t = out["html"], out["text"]
    assert 'data-act="qz-play" data-id="q3"' in h
    rows = {x.split('"')[0]: x for x in h.split('data-act="plan-resume" data-id="')[1:]}
    assert set(rows) == {"qp", "qe"}                                      # (the one being written can't be resumed)
    assert "Stopped: resume to continue" in t and "Claude's usage limit was reached." in t
    assert t.count("being written") == 1 and "Resume" in t
    assert "Moment 3" in t and "Moment 4" not in t and "Show 3 more" in t
    assert "Moment 6" in out["more"] and "Show 3 more" not in out["more"]
    assert "Show 3 more" in out["again"]                                   # (another visit: short again)
