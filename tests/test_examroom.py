"""The Exam room (4.2.0, item 2): one page per coming quiz or exam, also for a final known only from the syllabus
(no announcement, no sources), with the day (announced, the student's own, its week's classes), its share of the
grade (a Final Project is not the final), the weeks it covers and where they come from (the student can change
them), the material of those weeks as counts, what was practised and condensed for it, the class days read (kept in
the library now, imported once from the page's storage), Get everything, cram sessions that leave the schedule alone,
and reminders that say what a quiz covers and what is still missing."""

import datetime as dt

import pytest

from studia import agent, cards, examroom, practice, quizzes
from studia.coursesite import Announcement, Resource, Unit
from studia.domain import Course, Recording
from studia.plans import Scope

START = dt.date(2026, 9, 14)                  # week 1's Monday
NOW = dt.datetime(2026, 11, 27, 10, 0)        # Friday of week 11
NO_SPEECH = [{"code": "no_speech", "level": "error", "message": "No speech was found in this recording (only noise or "
                                                                "music). Check it on Panopto."}]
GRADING = [{"activity": "Quizzes / Studio Critiques", "number": 4, "weight": 20.0},
           {"activity": "Midterm", "number": 1, "weight": 30.0}, {"activity": "Final Project", "number": 1, "weight": 10.0},
           {"activity": "Final Exam", "number": 1, "weight": 40.0}]


def _rec(did, day, hour=9):
    return Recording(delivery_id=did, course_id="c", folder_id="f", folder_path="", name="Lecture",
                     start=dt.datetime(2026, 11, day, hour, 0), duration_s=3000, viewer_url=f"https://v/{did}")


def _units():
    files = {10: [Resource("w10", "Week10", "Week10.pdf", "pdf", "pdf", "u", "d")],
             11: [Resource("w11", "Week11", "Week11.pdf", "pdf", "pdf", "u", "d")],
             3: [Resource("w3", "Week03", "Week03.pdf", "pdf", "pdf", "u", "d")]}
    return [Unit(f"u{w}", w, f"Hafta {w}", START + dt.timedelta(weeks=w - 1), START + dt.timedelta(weeks=w - 1, days=6),
                 "", files.get(w, [])) for w in range(1, 13)] + \
        [Unit("cw", 99, "Course Documents", None, None, "",
              [Resource("q3s", "Quiz3 - Solutions", "Quiz3 - Solutions.pdf", "pdf", "pdf", "u", "d")])]


@pytest.fixture
def api(tmp_path):
    """MATH 240: Quiz 3 (week 12) covers weeks 10 and 11 as announced; its solutions are a course-wide file; Quiz 4 was
    announced only by its day; the syllabus has a midterm (week 8) and a final exam (week 16) and the grading. Week 10:
    a lecture transcribed (16 Nov) and one not (18 Nov), its slides on this PC; week 11: a lecture with no speech on
    Panopto (23 Nov) and one not transcribed (25 Nov), its slides not downloaded."""
    from studia.api import Api
    from studia.services import Services

    a = Api(Services())
    a._push = lambda e: None
    lib = a._s.lib
    lib.upsert_courses([Course(id="c", title="2026-1 MATH 240 P 1", code="MATH 240", name="Probability", term="T")])
    lib.merge_units("c", _units(), first_sync=True)
    lib.set_course_info("c", syllabus={"language": "English", "outcomes": ["count", "use Bayes", "find distributions"],
                                       "grading": GRADING, "weeks": [{"no": 8, "subject": "Midterm", "preparation": ""},
                                                                     {"no": 16, "subject": "Final Exam", "preparation": ""}]})
    lib.merge_announcements("c", [
        Announcement("a1", "Quizzes", "Dr X", dt.datetime(2026, 9, 20, 9), "Quiz 1\nWeek 4\nQuiz 2\nWeek 7\nQuiz 3\nWeek 12"),
        Announcement("a2", "About Quiz 3", "Dr X", dt.datetime(2026, 11, 2, 9), "Quiz 3 will cover weeks 10 and 11. LO2 and LO3."),
        Announcement("a3", "Quiz 4", "Dr X", dt.datetime(2026, 11, 20, 9), "Our 4th quiz will be held on Friday, December 11.")],
        first_sync=True)
    lib.merge_recordings("c", [_rec("r1", 16), _rec("r2", 18), _rec("r3", 23), _rec("r4", 25)], first_sync=True)
    lib.set_status("r1", "done")
    lib.set_status("r3", "failed", NO_SPEECH[0]["message"])
    lib.set_warnings("r3", NO_SPEECH)
    p = tmp_path / "Week10.pdf"
    p.write_bytes(b"%PDF")
    lib.set_material_file("w10", str(p), 10, "u")
    return a


def _room(api, key, now=NOW):
    return examroom.room(api._s, "c", key, (), now)


def _day(n):
    return f"c|2026-11-{n}|lecture"


# ------------------------------------------------------------------ which ones, and when
def test_every_coming_quiz_and_exam_has_a_room_also_a_final_known_only_from_the_syllabus(api):
    final = _room(api, "syllabus:Final Exam:16")
    a = final["assessment"]
    assert (a["name"], a["kind"], a["syllabus"], a["sources"]) == ("Final Exam", "final", True, [])
    assert a["weeks"] == list(range(1, 17)) and a["origin"]["from"] == "guessed"
    assert final["when"] == {"date": "2026-12-28", "time": "", "from": "week", "others": [], "end": "2027-01-03",
                             "days": 31}
    assert final["share"] == {"activity": "Final Exam", "weight": 40.0, "number": 1, "each": 40.0}    # not the project
    assert final["said"] == [] and final["assessment"]["los"] == []
    assert _room(api, "Final Exam")["assessment"]["key"] == "syllabus:Final Exam:16"         # by its name too
    assert _room(api, "Quiz 9") is None and examroom.room(api._s, "nope", "quiz:3", (), NOW) is None
    assert api.exam_room("c", "Quiz 9") == {"ok": False, "gone": True,
                                             "error": "This assessment is no longer in the course's plan."}


def test_the_day_is_the_announced_one_the_students_own_or_the_weeks(api):
    q3 = _room(api, "quiz:3")
    assert q3["when"] == {"date": "2026-11-30", "time": "", "from": "class", "others": ["2026-12-02"], "days": 3}
    q4 = _room(api, "Quiz 4")                                   # announced only by its day: week 1's date gives week 13
    assert (q4["assessment"]["week"], q4["when"]["date"], q4["when"]["from"]) == (13, "2026-12-11", "announced")
    assert q4["assessment"]["origin"] == {"from": "guessed", "since": "Quiz 3", "announcement": None}
    r = api.exam_set_day("c", "syllabus:Final Exam:16", "2027-01-08")
    assert r["ok"] and r["room"]["when"]["from"] == "yours" and r["room"]["when"]["date"] == "2027-01-08"
    d = api.course_detail("c")["course"]
    final = next(e for e in d["assessments"] if e["key"] == "syllabus:Final Exam:16")
    assert final["on"] == {"date": "2027-01-08", "time": "", "from": "yours", "others": []}     # (the course page's chip)
    assert api.exam_set_day("c", "syllabus:Final Exam:16", "8 Jan") == {"ok": False, "error": "That is not a date."}
    assert api.exam_set_day("c", "syllabus:Final Exam:16", "")["room"]["when"]["from"] == "week"


def test_the_weeks_say_where_they_come_from_and_the_student_can_change_them(api):
    a = _room(api, "quiz:3")["assessment"]
    assert (a["weeks"], a["weeks_from"], a["guessed"]) == ([10, 11], "announced", [9, 10, 11, 12])
    assert a["origin"] == {"from": "announced", "since": "Midterm", "announcement": {"id": "a2", "title": "About Quiz 3"}}
    assert [x["no"] for x in a["los"]] == [2, 3] and a["los"][0]["en"] == "use Bayes"
    for rows in (api.quiz_options("c")["assessments"], api.plan_options("c")["assessments"]):    # the same list
        assert next(e for e in rows if e["key"] == "quiz:3")["weeks"] == a["weeks"]
    r = api.exam_set_weeks("c", "quiz:3", [9, 10, 11])
    assert r["room"]["assessment"]["weeks"] == [9, 10, 11] and r["room"]["assessment"]["weeks_from"] == "yours"
    assert api.exam_set_weeks("c", "quiz:3", [])["room"]["assessment"]["weeks_from"] == "announced"


@pytest.mark.parametrize("grading,kind,want", [
    (GRADING, "quiz", 5.0), (GRADING, "midterm", 30.0), (GRADING, "final", 40.0),
    ([{"activity": "Final Project", "number": 1, "weight": 40}], "final", None),
    ([{"activity": "Kısa Sınav", "number": 2, "weight": 10}], "quiz", 5.0),
    ([{"activity": "Ara Sınav", "number": 2, "weight": 50}], "midterm", 25.0),
    ([{"activity": "Final Sınavı", "number": 1, "weight": "35"}], "final", 35.0),
    ([{"activity": "Midterm", "number": 1, "weight": 0}], "midterm", None),
    ([], "quiz", None),
])
def test_its_share_of_the_grade(grading, kind, want):
    got = examroom.grade_share(grading, {"kind": kind})
    assert (got["each"] if got else None) == want


def _course(labels, plan=(), n=16):
    """A course dict as views.course_detail gives it: 16 weeks from START, the syllabus's exam labels by week."""
    weeks = [{"no": no, "start": (START + dt.timedelta(weeks=no - 1)).isoformat(),
              "end": (START + dt.timedelta(weeks=no - 1, days=6)).isoformat(), "exam": no in labels,
              "exam_label": labels.get(no, ""), "past": False, "classes": []} for no in range(1, n + 1)]
    return {"id": "c", "weeks": weeks, "alerts": {"plan": [
        {"kind": k, "no": no, "name": name, "week": week, "los": [], "sources": [{"id": "a", "title": "A"}],
         "date": date, "time": "", "covers": [], "covers_from": ""} for k, no, name, week, date in plan]}}


def test_a_two_week_exam_period_counts_to_its_first_week_and_is_ahead_until_its_end():
    d = _course({15: "Final Exam", 16: "Final Exam"})
    e = examroom.find(d, "syllabus:Final Exam:16")
    assert e["week"] == 16 and e["first_week"] == 15
    on = examroom.when(d, e, None, dt.date(2026, 12, 23))          # (Wednesday of week 15)
    assert on == {"date": "2026-12-21", "time": "", "from": "week", "others": [], "end": "2027-01-03"}
    one = examroom.find(_course({8: "Midterm"}), "Midterm")
    assert examroom.when(_course({8: "Midterm"}), one, None)["end"] == "2026-11-08"   # its week's last day


def test_a_syllabus_exam_week_opens_the_exam_it_is_part_of():
    """The week cards, Home and the Calendar opened a syllabus exam week by its label: one folded into an announced
    exam a week away (or a two-week period's first week) said "no longer in the course's plan", and of two
    same-named weeks the first opened. By its label and week, each finds its own."""
    d = _course({8: "Midterm Exam", 15: "Final Exam", 16: "Final Exam"},
                [("midterm", 0, "Midterm", 9, ""), ("final", 0, "Final", 16, "")])
    assert [e["key"] for e in quizzes.of(d)] == ["midterm:0", "final:0"]
    assert examroom.find(d, "syllabus:Midterm Exam:8")["key"] == "midterm:0"
    assert examroom.find(d, "syllabus:Final Exam:15")["key"] == "final:0"
    period = _course({15: "Final Exam", 16: "Final Exam"})
    assert examroom.find(period, "syllabus:Final Exam:15")["key"] == "syllabus:Final Exam:16"
    two = _course({7: "Midterm Exam", 12: "Midterm Exam"})
    assert examroom.find(two, "syllabus:Midterm Exam:12")["week"] == 12
    assert examroom.find(two, "syllabus:Midterm Exam:7")["week"] == 7
    assert examroom.find(two, "syllabus:Quiz Day:3") is None
    # a reminder of a syllabus week finds it, and its click opens that one
    ev = {"kind": "exam", "name": "Midterm Exam", "week": 12, "course_id": "c"}
    assert examroom.event_key(ev) == "syllabus:Midterm Exam:12"
    assert examroom.event_key({"kind": "quiz", "name": "Quiz 3", "week": 12}) == "Quiz 3"


class _Meta:
    def __init__(self):
        self.meta = {}

    def get_meta(self, k):
        return self.meta.get(k)

    def set_meta(self, k, v):
        self.meta[k] = v


def test_the_weeks_and_day_set_for_a_syllabus_exam_stay_once_an_announcement_names_it():
    """They were kept under the syllabus exam's key ("syllabus:Midterm Exam:10"); once an announcement named the exam
    its key became "midterm:0" (and "Midterm" folded into "Midterm 1" became "midterm:1"), and the Exam room, the
    course page's chip, the Calendar and every dialog went back to the guessed weeks and the week's first day."""
    from studia import calendar_export

    lib = _Meta()
    old = "syllabus:Midterm Exam:10"
    assert [e["key"] for e in quizzes.assessments(_course({10: "Midterm Exam", 16: "Final Exam"}), lib)][0] == old
    quizzes.set_weeks(lib, "c", old, [6, 7, 8, 9])
    examroom.set_day(lib, "c", old, "2026-11-19")
    for no, name in ((0, "Midterm"), (1, "Midterm 1")):
        d = _course({10: "Midterm Exam", 16: "Final Exam"}, [("midterm", no, name, 10, "")])
        e = next(x for x in quizzes.assessments(d, lib) if x["kind"] == "midterm")
        assert (e["key"], e["weeks"], e["weeks_from"]) == (f"midterm:{no}", [6, 7, 8, 9], "yours")
        assert (e["on"]["date"], e["on"]["from"]) == ("2026-11-19", "yours")
        assert next(x for x in calendar_export.listed(lib, "c", d) if x["kind"] == "midterm")["yours"] == "2026-11-19"
        assert next(x for x in quizzes.assessments(d, lib) if x["kind"] == "final")["weeks_from"] == "guessed"
    # set under "Midterm", then "Midterm 1" is announced: the same exam
    lib = _Meta()
    quizzes.set_weeks(lib, "c", "midterm:0", [5, 6])
    d = _course({}, [("midterm", 1, "Midterm 1", 10, ""), ("midterm", 2, "Midterm 2", 14, "")])
    assert [(x["key"], x["weeks_from"]) for x in quizzes.assessments(d, lib)] == \
        [("midterm:1", "yours"), ("midterm:2", "guessed")]
    # the syllabus exam still there keeps its own; one a long way off is another exam
    d = _course({10: "Midterm Exam"}, [("midterm", 0, "Midterm", 14, "")])
    assert quizzes.former_keys(d, next(x for x in quizzes.assessments(d) if x["key"] == "midterm:0")) == []


def test_set_before_it_was_announced_its_room_shows_them_and_clearing_them_there_clears_them(api):
    api.exam_set_weeks("c", "syllabus:Midterm:8", [5, 6, 7])
    api.exam_set_day("c", "syllabus:Midterm:8", "2026-11-05")
    api._s.lib.merge_announcements("c", [Announcement("a4", "Midterm", "Dr X", dt.datetime(2026, 10, 20, 9),
                                                      "The midterm exam will be held in week 8.")], first_sync=False)
    r = _room(api, "midterm:0")
    assert (r["assessment"]["weeks"], r["assessment"]["weeks_from"]) == ([5, 6, 7], "yours")
    assert (r["when"]["date"], r["when"]["from"]) == ("2026-11-05", "yours")
    assert api.exam_set_weeks("c", "midterm:0", [])["room"]["assessment"]["weeks_from"] == "guessed"
    assert api.exam_set_day("c", "midterm:0", "")["room"]["when"]["from"] == "week"
    assert not quizzes.own_weeks(api._s.lib, "c", "syllabus:Midterm:8")
    assert not examroom.own_day(api._s.lib, "c", "syllabus:Midterm:8")


def test_two_assessments_of_one_name_each_have_their_own_quizzes_and_sheets():
    """A quiz made for the first "Midterm Exam" (weeks 1-8) was counted in the second's room (weeks 9-13) too: only
    the name was matched."""
    every = quizzes.of(_course({8: "Midterm Exam", 13: "Midterm Exam"}))
    one, two = every
    assert one["name"] == two["name"] and (one["weeks"], two["weeks"]) == (list(range(1, 9)), list(range(9, 14)))
    made = {"kind": "quiz", "weeks": one["weeks"], "focus": "Midterm Exam", "focus_key": one["key"]}
    assert examroom._for_it(made, one, every) and not examroom._for_it(made, two, every)
    sheet = {"kind": "prep", "weeks": [], "focus": "Midterm Exam", "focus_key": two["key"]}
    assert examroom._for_it(sheet, two, every) and not examroom._for_it(sheet, one, every)
    old = {"kind": "quiz", "weeks": list(range(1, 9)), "focus": "Midterm Exam"}      # (no key: by its weeks only)
    assert examroom._for_it(old, one, every) and not examroom._for_it(old, two, every)
    # a name one alone has is still its own, also under a key it had before it was announced
    alone = quizzes.of(_course({16: "Final Exam"}, [("midterm", 0, "Midterm", 8, "")]))
    final, mid = next(e for e in alone if e["kind"] == "final"), next(e for e in alone if e["kind"] == "midterm")
    assert examroom._for_it({"weeks": [], "focus": "Final Exam"}, final, alone)
    assert examroom._for_it({"weeks": [], "focus": "Midterm", "focus_key": "syllabus:Midterm:8"}, mid, alone)
    assert not examroom._for_it({"weeks": [], "focus": "Midterm", "focus_key": "syllabus:Midterm:8"}, final, alone)


def test_a_quiz_announced_only_by_its_day_still_has_a_room():
    """Week 1's date unknown: no week, so not among the assessments; its pill and Calendar row opened "no longer in
    the course's plan"."""
    d = _course({}, [("quiz", 2, "Quiz 2", None, "2099-11-20")])
    assert quizzes.of(d) == []
    e = examroom.find(d, "Quiz 2")
    assert (e["key"], e["weeks"], e["ahead"]) == ("quiz:2", [], True)
    assert examroom.find(d, "quiz:2")["name"] == "Quiz 2"
    assert examroom.when(d, e, None)["from"] == "announced"


# ------------------------------------------------------------------ material, read ticks and Get everything
def test_the_material_of_its_weeks_as_counts(api):
    m = _room(api, "quiz:3")["material"]
    assert [x["day_key"] for x in m["days"]] == [_day(16), _day(18), _day(25)]       # 23 Nov has no speech: apart
    assert (m["lectures"], m["transcribed"], m["silent"], m["busy"]) == (3, 1, 1, 0)
    assert m["todo"] == [_day(18), _day(25)]
    assert {f["id"]: (f["downloaded"], f["tied"]) for f in m["files"]} == {"w10": (True, False), "w11": (False, False),
                                                                          "q3s": (False, True)}   # its solutions
    assert m["missing"] == ["w11", "q3s"]
    counts = _room(api, "quiz:3")["counts"]
    assert counts["material"] == {"have": 2, "of": 6}
    job = {"id": "r2", "stage": "transcribing", "progress": 0.2, "detail": ""}
    busy = examroom.room(api._s, "c", "quiz:3", [job], NOW)["material"]
    assert busy["busy"] == 1 and busy["todo"] == [_day(25)]                           # being transcribed: not again


def test_the_days_read_are_kept_in_the_library_and_imported_once(api):
    assert api.reads_import([_day(16), _day(18), "x|old|lecture"]) == {"ok": True, "added": 3}
    assert api.reads_import([_day(16), _day(18)]) == {"ok": True, "added": 0}         # again: nothing new
    m = _room(api, "quiz:3")["material"]
    assert (m["read"], m["readable"]) == (1, 1)                                      # only a transcribed day counts
    assert [x["read"] for x in m["days"]] == [True, True, False]
    assert api.reads_set([_day(16)], False)["changed"] == 1
    assert api._s.lib.day_reads([_day(16), _day(18)]) == {_day(18)}


def test_get_everything_queues_its_lectures_first_and_downloads_its_files(api):
    calls = []
    api.transcribe_day = lambda k, batch=False: calls.append(("tx", k, batch)) or {"ok": True, "queued": 1}
    api.materials_download = lambda ids: calls.append(("dl", ids)) or {"ok": True, "queued": len(ids)}
    r = api.exam_get_everything("c", "quiz:3")
    assert r == {"ok": True, "lectures": 2, "parts": 2, "files": 2, "unknown": 0, "needs_section": False}
    assert calls == [("tx", _day(18), True), ("tx", _day(25), True), ("dl", ["w11", "q3s"])]


# ------------------------------------------------------------------ practice, cram, condensed
def _quiz(lib, pid, focus, weeks, created="2026-11-20T10:00:00"):
    lib.create_plan(pid, "c", Scope(kind="quiz", weeks=weeks, focus=focus).to_json(), title=pid)
    qs = [{"n": 1, "type": "mc", "q": "Bayes?", "options": ["a", "b", "c", "d"], "answer": 0, "los": [2], "topic": "Bayes"},
          {"n": 2, "type": "mc", "q": "Total probability?", "options": ["a", "b", "c", "d"], "answer": 1, "los": [3],
           "topic": "Total probability"}]
    lib.update_plan(pid, status="done", result={"questions": qs})


def test_what_was_practised_and_condensed_for_it(api):
    lib = api._s.lib
    _quiz(lib, "q3", "Quiz 3", [10, 11])
    _quiz(lib, "wk10", "", [10])                                 # "Quiz me on this week": on its weeks, counts
    _quiz(lib, "old", "Quiz 1", [2, 3])                          # another quiz's: doesn't
    practice.save_attempt(lib, "q3", "c", {"answers": [{"n": 1, "got": 1, "chose": 0}, {"n": 2, "got": 0, "chose": 2}]})
    practice.save_attempt(lib, "old", "c", {"answers": [{"n": 1, "got": 0, "chose": 1}]})
    lib.create_plan("k1", "c", Scope(kind="cards").to_json())
    lib.update_plan("k1", status="done", result={"kind": "cards", "cards": [
        {"id": "f1", "q": "Bayes' theorem?", "a": "P(B|A)=…", "source": {"kind": "file", "id": "w10", "page": 3, "week": 10}},
        {"id": "f2", "q": "Counting?", "a": "n!", "source": {"kind": "file", "id": "w3", "page": 1, "week": 3}}]})
    for pid, kind, weeks, focus in (("s1", "sheet", [10, 11], ""), ("p1", "prep", [], "Quiz 3"), ("s2", "sheet", [1, 2, 3], "")):
        lib.create_plan(pid, "c", Scope(kind=kind, weeks=weeks, focus=focus).to_json(), title=pid)
    lib.update_plan("s1", status="done")
    r = _room(api, "quiz:3")
    p = r["practice"]
    assert sorted(q["id"] for q in p["quizzes"]) == ["q3", "wk10"] and (p["answers"], p["tried"]) == (2, 1)
    assert p["cards"] == {"total": 2, "due": 0, "new": 2, "learnt": 0, "reviews": 0}      # week 10's, and the miss
    los = {x["no"]: (x["tried"], x["right"], x["status"]) for x in p["mastery"]["los"]}
    assert los == {2: (1, 1.0, "few"), 3: (1, 0.0, "few")}                          # counts, from its quizzes only
    assert [(x["plan_id"], x["n"]) for x in p["mistakes"]] == [("q3", 2)] and p["missed"] == 1
    assert sorted(x["id"] for x in r["condensed"]) == ["p1", "s1"]
    assert r["counts"]["practised"] == {"answers": 2, "quizzes": 1, "reviews": 0}
    assert r["counts"]["condensed"] == {"done": 1, "of": 2}
    final = _room(api, "syllabus:Final Exam:16")
    assert sorted(q["id"] for q in final["practice"]["quizzes"]) == ["old", "q3", "wk10"]   # its weeks hold them all
    assert all(x["status"] != "untested" for x in final["practice"]["mastery"]["los"])  # (no outcomes named for it)


def test_cram_goes_through_every_card_of_the_weeks_and_leaves_the_schedule_alone(api):
    lib = api._s.lib
    deck = [{"id": f"x{i}", "q": f"q{i}", "a": "a", "origin": "files", "source": {}, "week": 10 if i < 25 else 3,
             "stressed": False} for i in range(30)]
    cards.grade(lib, "c", "x0", "easy", NOW)                                          # learnt: due in 4 days
    before = cards.states(lib, "c")
    normal = cards.session(lib, deck, "c", NOW, weeks={10}, new_per_day=5)
    cram = cards.session(lib, deck, "c", NOW, weeks={10}, new_per_day=5, cram=True)
    assert len(normal) == 4 and len(cram) == 25 and all(c["cram"] and c["next"] == {} for c in cram)   # (5 new a day)
    assert cram[0]["state"]["step"] == -1                                             # (what it knows: shown, kept)
    again = cards.grade(lib, "c", "x0", "again", NOW, cram=True)
    good = cards.grade(lib, "c", "x1", "good", NOW, cram=True)
    assert (again["step"], again["cram"], good["step"]) == (0, True, -1)
    assert again["due"] == "2026-11-27T10:01:00"                                      # again in this sitting
    assert cards.states(lib, "c") == before                                           # card_state untouched
    with lib._lock:
        rows = lib.db.execute("SELECT card_id, grade, cram FROM card_reviews ORDER BY rowid").fetchall()
    assert [tuple(r) for r in rows] == [("x0", "easy", 0), ("x0", "again", 1), ("x1", "good", 1)]
    s = cards.summary(lib, deck, "c", NOW)
    assert (s["reviewed_today"], s["due"], s["learnt"]) == (1, 0, 1)                  # the cram reviews don't count
    with pytest.raises(ValueError):
        cards.grade(lib, "c", "x1", "maybe", NOW, cram=True)


def test_the_cram_flag_through_the_api(api, monkeypatch):
    deck = [{"id": "y1", "q": "q", "a": "a", "origin": "files", "source": {}, "week": 10, "stressed": False}]
    monkeypatch.setattr(type(api), "_deck", lambda self, cid: deck)
    r = api.cards_session("c", [10], [], True)
    assert r["ok"] and [c["id"] for c in r["cards"]] == ["y1"] and r["cards"][0]["cram"]
    assert api.cards_grade("c", "y1", "good", True)["state"]["cram"]
    assert cards.states(api._s.lib, "c") == {}


# ------------------------------------------------------------------ reminders
def test_a_reminder_says_what_it_covers_and_what_is_missing(api):
    lib, s = api._s.lib, api._s.settings
    note = examroom.reminder_note(lib, s, {"kind": "quiz", "course_id": "c", "name": "Quiz 3"})
    assert note == "It covers weeks 10–11; 2 lectures aren't transcribed yet, 2 files aren't on this PC."
    assert examroom.reminder_note(lib, s, {"kind": "assignment", "course_id": "c", "name": "Quiz 3"}) == ""
    assert examroom.reminder_note(lib, s, {"kind": "quiz", "course_id": "c", "name": "Quiz 9"}) == ""
    ev = {"uid": "c-quiz-3", "course": "MATH 240", "name": "Quiz 3", "course_id": "c", "kind": "quiz", "classes": [],
          "ends": [], "los": [], "start": "2026-11-30", "end": "2026-12-06", "week": 12}
    title, text, payload = agent.reminders([ev], dt.datetime(2026, 11, 29, 18), {}, {"exams": 1},
                                           about=lambda e: examroom.reminder_note(lib, s, e))
    assert text == ("MATH 240: Quiz 3 is in week 12, which starts tomorrow (Mon 30 Nov). It covers weeks 10–11; "
                    "2 lectures aren't transcribed yet, 2 files aren't on this PC.")
    assert payload == "ready:c:Quiz 3"                                               # a click: its Exam room
    broken = agent.reminders([ev], dt.datetime(2026, 11, 29, 18), {}, {"exams": 1}, about=lambda e: 1 / 0)
    assert broken[1].endswith("which starts tomorrow (Mon 30 Nov).")                # the reminder still comes
