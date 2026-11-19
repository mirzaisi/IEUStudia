"""What every exam tool reads (4.2.0, item 1): one list of each quiz's and exam's weeks (quizzes.assessments, kept as
d["assessments"]), the course-wide and announcement files, and class lists of student numbers kept away from the AI."""

import datetime as dt

import pytest

from studia import quizzes, views
from studia.coursesite import Announcement, Resource, Unit
from studia.domain import Course

START = dt.date(2026, 9, 14)                 # week 1's Monday


def _units(n=12, files=None):
    files = files or {}
    return [Unit(f"u{w}", w, f"Hafta {w}", START + dt.timedelta(weeks=w - 1), START + dt.timedelta(weeks=w - 1, days=6),
                 "", files.get(w, [])) for w in range(1, n + 1)]


@pytest.fixture
def api(tmp_path):
    from studia.api import Api
    from studia.services import Services

    a = Api(Services())
    a._push = lambda e: None
    lib = a._s.lib
    lib.upsert_courses([Course(id="c", title="2026-1 MATH 240 P 1", code="MATH 240", name="Probability", term="T")])
    lib.merge_units("c", _units(files={7: [Resource("w7", "Week07", "Week07.pdf", "pdf", "pdf", "u", "d")]}),
                    first_sync=True)
    lib.set_course_info("c", syllabus={"language": "English", "outcomes": ["count", "use Bayes"], "weeks": [
        {"no": 8, "subject": "Midterm", "preparation": ""}, {"no": 16, "subject": "Final Exam", "preparation": ""}]})
    lib.merge_announcements("c", [
        Announcement("a1", "Quizzes", "Dr X", dt.datetime(2026, 9, 20, 9), "Quiz 1\nWeek 4\nQuiz 2\nWeek 7\nQuiz 3\nWeek 12"),
        Announcement("a2", "About Quiz 3", "Dr X", dt.datetime(2026, 11, 2, 9), "Quiz 3 will cover weeks 10 and 11.")],
        first_sync=True)
    return a


def _weeks(rows):
    return [(e["key"], e["weeks"], e["weeks_from"]) for e in rows]


def test_the_quiz_dialog_the_plan_dialog_and_the_course_show_the_same_weeks(api):
    """The quiz dialog, the plan dialog and Get ready each worked the weeks out on their own (and the plan dialog
    knew no syllabus exam): now all three read d["assessments"]."""
    d = views.course_detail(api._s.lib, api._s.settings, "c")
    want = [("quiz:1", [1, 2, 3, 4], "guessed"), ("quiz:2", [5, 6, 7], "guessed"),
            ("syllabus:Midterm:8", list(range(1, 9)), "guessed"), ("quiz:3", [10, 11], "announced"),
            ("syllabus:Final Exam:16", list(range(1, 17)), "guessed")]
    assert _weeks(d["assessments"]) == want
    assert _weeks(api.quiz_options("c")["assessments"]) == want
    assert _weeks(api.plan_options("c")["assessments"]) == want
    q3 = d["assessments"][3]
    assert (q3["name"], q3["week"], q3["covers"], q3["covers_from"], q3["guessed"]) == \
        ("Quiz 3", 12, [10, 11], "a2", [9, 10, 11, 12])
    syl = d["assessments"][2]
    assert syl["sources"] == [] and syl["syllabus"] and syl["files"] == []          # (nothing may read sources[0])


def test_the_students_own_weeks_win_everywhere(api):
    lib = api._s.lib
    assert quizzes.set_weeks(lib, "c", "quiz:3", [9, "10", 11, 99]) == [9, 10, 11]
    for rows in (views.course_detail(lib, api._s.settings, "c")["assessments"], api.quiz_options("c")["assessments"],
                 api.plan_options("c")["assessments"]):
        q3 = next(e for e in rows if e["key"] == "quiz:3")
        assert (q3["weeks"], q3["weeks_from"], q3["covers"]) == ([9, 10, 11], "yours", [10, 11])
    quizzes.set_weeks(lib, "c", "quiz:3", [])                                      # back to the announced ones
    q3 = next(e for e in api.quiz_options("c")["assessments"] if e["key"] == "quiz:3")
    assert (q3["weeks"], q3["weeks_from"]) == ([10, 11], "announced")
    assert quizzes.own_weeks(None, "c", "quiz:3") == []


def test_a_quiz_prepares_for_the_weeks_the_announcement_named(api):
    from studia import plans
    from studia.plans import Scope

    d = views.course_detail(api._s.lib, api._s.settings, "c")
    assert quizzes._focus(d, Scope(kind="quiz", focus="Quiz 3"))["week"] == 12
    assert "Quiz 3: week 12, covers weeks 10–11" in plans.gather(api._s.lib, api._s.settings, "c",
                                                                 Scope(kind="prep", weeks=[7]))["pack"]
