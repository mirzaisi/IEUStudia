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


# ------------------------------------------------------------------ course-wide and announcement files
ANN_URL = "https://learn.ieu.edu.tr/student/announcements/55/file/0"
ANN_FILE = "ann-c-55-0"
STUDENTS = "\n".join(f"{20210600000 + 37 * n} {60 + n % 40} {55 + n % 45}" for n in range(25))


def _pdf(tmp_path, name):
    p = tmp_path / "files" / name
    p.parent.mkdir(exist_ok=True)
    p.write_bytes(b"%PDF")
    return str(p)


@pytest.fixture
def course(api, tmp_path):
    """The fixture course with week 7's slides, a course-wide syllabus, the solutions of Quiz 2 attached to an
    announcement (week 8) and a lab's grade list in week 7."""
    lib = api._s.lib
    lib.merge_units("c", _units(files={7: [Resource("w7", "Week07", "Week07.pdf", "pdf", "pdf", "u", "d"),
                                           Resource("g7", "Lab Grades", "Lab Grades.pdf", "pdf", "pdf", "u", "d")]})
                    + [Unit("cw", 99, "Course Documents", None, None, "",
                            [Resource("c1", "Course Syllabus", "Syllabus.pdf", "pdf", "pdf", "u", "d")])], first_sync=True)
    lib.merge_announcements("c", [Announcement(
        "a3", "Quiz 2 solutions", "Dr X", dt.datetime(2026, 11, 2, 9),
        f"The solutions are attached.\nQuiz2 - Solutions.pdf 142 KB ({ANN_URL})",
        [Resource("", "Quiz2 - Solutions", "Quiz2 - Solutions.pdf", "announcement", "pdf", ANN_URL, ANN_URL)])],
        first_sync=True)
    for rid, name, pages in (("w7", "Week07.pdf", ["Bayes theorem: P(B|A) = P(A|B)P(B)/P(A)."]),
                             ("g7", "Lab Grades.pdf", [STUDENTS]),
                             ("c1", "Syllabus.pdf", ["Grading: two quizzes, a midterm and a final exam."]),
                             (ANN_FILE, "Quiz2 - Solutions.pdf", ["Quiz 2, question 1: the total probability rule."])):
        lib.set_material_file(rid, _pdf(tmp_path, name), 10, "u")
        lib.set_material_text(rid, pages)
    return api


def test_course_wide_and_announcement_files_reach_every_tool_when_picked(course):
    from studia import plans, studypack
    from studia.plans import Scope

    lib, s = course._s.lib, course._s.settings
    d = views.course_detail(lib, s, "c")
    q2 = next(e for e in d["assessments"] if e["key"] == "quiz:2")
    assert q2["files"] == [ANN_FILE]                                  # its posted solutions, by title and by name
    gen = {g["id"]: g for g in course.quiz_options("c")["general"]}
    assert (gen["c1"]["group"], gen["c1"]["week"], gen["c1"]["for"]) == ("course", None, [])
    assert (gen[ANN_FILE]["group"], gen[ANN_FILE]["week"], gen[ANN_FILE]["for"]) == ("announcement", 8, ["quiz:2"])
    assert set(g["id"] for g in course.plan_options("c")["general"]) == {"c1", ANN_FILE}
    # unticked by default: a scope without them reads only the week's own file, as before
    f = studypack.gather(lib, s, "c", Scope(kind="quiz", weeks=[7]))
    assert [(x["ref"], x["id"], x["week"]) for x in f["files"]] == [("F1", "w7", 7)]
    # picked: after the week's files (their F numbers stay), each once, a course-wide one without a week
    f = studypack.gather(lib, s, "c", Scope(kind="quiz", weeks=[7], files=["w7", ANN_FILE, "c1", "c1"], picked=True))
    assert [(x["ref"], x["id"], x["week"]) for x in f["files"]] == [("F1", "w7", 7), ("F2", "c1", None),
                                                                     ("F3", ANN_FILE, 8)]
    assert "=== FILE F2 (course-wide): Syllabus.pdf" in f["pack"]
    assert "=== FILE F3 (from an announcement, week 8): Quiz2 - Solutions.pdf" in f["pack"]
    # a question from the course-wide file: its source has no week, and the coverage still adds up
    src = quizzes.check_source({"ref": "F2", "page": 1}, f)
    assert src["week"] is None and src["id"] == "c1"
    cov = quizzes.coverage({"questions": [{"type": "mc", "los": [], "stressed": [], "topic": "grading", "q": "q",
                                           "source": src}], "topics": []}, {**f, "focus": None})
    assert cov["files"] == ["Syllabus.pdf"] and [w["no"] for w in cov["weeks"]] == [7]
    # a review sheet (plans.gather): the picked ones too
    pack = plans.gather(lib, s, "c", Scope(kind="prep", weeks=[7], files=["c1"]))["pack"]
    assert "=== FILE (course-wide): Syllabus.pdf" in pack and "Quiz2 - Solutions" not in pack
    assert "Syllabus.pdf" not in plans.gather(lib, s, "c", Scope(kind="prep", weeks=[7]))["pack"]


def test_a_class_list_is_kept_away_from_every_ai_tool(course, tmp_path):
    """EEE 215's "Lab Grades" (classmates' student numbers and marks) is left out of quizzes, cards, packs, review
    sheets, a chat's course files and uploads and the matching passages, unless the student says to include it."""
    from studia import chatfiles, chatslides, knowledge, plans, studypack
    from studia.plans import Scope

    lib, s = course._s.lib, course._s.settings
    number = "20210600037"
    for gather in (studypack.gather, quizzes.gather):
        f = gather(lib, s, "c", Scope(kind="quiz", weeks=[7]))
        assert [x["id"] for x in f["files"]] == ["w7"] and number not in f["pack"], gather.__module__
        assert f["materials_students"] == [{"id": "g7", "title": "Lab Grades", "week": 7}]
    f = plans.gather(lib, s, "c", Scope(kind="prep", weeks=[7]))
    assert number not in f["pack"] and [x["id"] for x in f["materials_students"]] == ["g7"]
    col = chatslides.collect(lib, "c", [], {"mode": "all"}, can_images=False)
    assert "g7" not in [x["file"]["id"] for x in col["files"]] and col["students"][0]["id"] == "g7"
    assert chatslides.assemble(col, local=False)["summary"]["students"][0]["id"] == "g7"
    assert all(src.get("resource_id") != "g7" for src in knowledge.retrieve(lib, "c", number)[1])
    # the dialogs mark it, so it isn't ticked
    week7 = next(w for w in course.quiz_options("c")["weeks"] if w["no"] == 7)
    assert {x["id"]: x["students"] for x in week7["files"]} == {"w7": False, "g7": True}
    assert next(w for w in course.plan_options("c")["weeks"] if w["no"] == 7)["students"] == \
        [{"id": "g7", "name": "Lab Grades.pdf"}]
    # "Include anyway"
    assert course.students_include("g7")["ok"]
    f = studypack.gather(lib, s, "c", Scope(kind="quiz", weeks=[7]))
    assert [x["id"] for x in f["files"]] == ["w7", "g7"] and f["materials_students"] == []
    assert any(src.get("resource_id") == "g7" for src in knowledge.retrieve(lib, "c", number)[1])
    assert chatslides.collect(lib, "c", [], {"mode": "all"}, can_images=False)["students"] == []
    # a chat's upload that lists students: not sent until the student says so
    src = tmp_path / "grades.txt"
    src.write_text(STUDENTS, encoding="utf-8")
    target = "draft-abcdef12"
    up = chatfiles.add(target, src, "grades.txt")
    assert up["students"]
    item = chatfiles.build(target, can_images=False)["items"][0]
    assert item["students"] and item["sent"] == "none"
    course.students_include(spec={"course_id": "c", "uploads": "abcdef12"}, upload_id=up["id"])
    item = chatfiles.build(target, can_images=False)["items"][0]
    assert not item["students"] and item["sent"] == "text"


def test_the_grade_list_check_is_kept_per_download(course, monkeypatch):
    from studia import materials

    lib = course._s.lib
    assert materials.lists_students(lib, "g7") and not materials.lists_students(lib, "w7")
    monkeypatch.setattr(materials, "text_lists_students", lambda texts: pytest.fail("read again"))
    assert materials.lists_students(lib, "g7")                        # (kept: the dialogs ask for every file)
    assert not materials.lists_students(lib, "missing-file")


def test_a_quiz_prepares_for_the_weeks_the_announcement_named(api):
    from studia import plans
    from studia.plans import Scope

    d = views.course_detail(api._s.lib, api._s.settings, "c")
    assert quizzes._focus(d, Scope(kind="quiz", focus="Quiz 3"))["week"] == 12
    assert "Quiz 3: week 12, covers weeks 10–11" in plans.gather(api._s.lib, api._s.settings, "c",
                                                                 Scope(kind="prep", weeks=[7]))["pack"]
