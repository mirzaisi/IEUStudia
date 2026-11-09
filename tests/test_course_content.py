"""Course content: IEULearn pages and the public syllabus (parsers), the lecturer's heads-up (alerts) and assessment
plan, weeks for plans without dates, and the dashboard cover pictures."""

import datetime as dt
import sqlite3

import pytest

from studia import alerts, coursesite, store
from studia.alerts import _excerpt, assessment_plan, categories, course_alerts, matches
from studia.config import Settings
from studia.coursesite import Announcement, Resource, Unit, parse_announcements, parse_overview, parse_unit_plan
from studia.domain import Course, Recording, Segment, Transcript
from studia.ieulearn import parse_dashboard
from studia.library import Library
from studia.materials import unit_week
from studia.syllabus import parse_syllabus
from studia.views import _cover_uri, class_times, classes_over, course_detail, exam_topic, week_classes

# ------------------------------------------------------------------ IEULearn pages
UNIT_PLAN = """
<article class="curriculum-item is-open" data-item-id="501">
  <span class="ovup-title-text" title="Hafta 1">Hafta 1</span>
  <div class="item-subtitle">23 Eyl - 29 Eyl</div>
  <div class="ovup-syl-desc-body"><p>Read chapter 1</p></div>
  <button data-rv-payload="{&quot;id&quot;:9001,&quot;title&quot;:&quot;Week01&quot;,&quot;type&quot;:&quot;pdf&quot;,&quot;file_ext&quot;:&quot;PDF&quot;,&quot;url&quot;:&quot;https://x/f.pdf&quot;,&quot;file_name&quot;:&quot;Week01.pdf&quot;,&quot;download_url&quot;:&quot;https://learn.ieu.edu.tr/student/courses/1/resources/9001/download&quot;}"></button>
</article>
<article class="curriculum-item" data-item-id="502">
  <span class="ovup-title-text" title="Hafta 15">Hafta 15</span>
  <div class="item-subtitle">29 Ara - 4 Oca</div>
</article>
<article class="curriculum-item" data-item-id="503"><span class="ovup-title-text" title="Konu 3">Konu 3</span></article>
"""


def test_parse_unit_plan_dates_and_files():
    units = parse_unit_plan(UNIT_PLAN, "2026-1 MATH 240 Mühendisler için Olasılık 1")
    assert [u.id for u in units] == ["501", "502", "503"]
    assert (units[0].start, units[0].end) == (dt.date(2026, 9, 23), dt.date(2026, 9, 29))
    assert (units[1].start, units[1].end) == (dt.date(2026, 12, 29), dt.date(2027, 1, 4))    # across New Year
    assert units[2].start is None and units[0].readings == "Read chapter 1"
    r = units[0].resources[0]
    assert (r.id, r.title, r.ext, r.file_name) == ("9001", "Week01", "pdf", "Week01.pdf")
    assert r.download_url.endswith("/resources/9001/download")


def test_parse_announcements():
    page = """<article class="ov-ann-card is-new"><h4 class="ov-ann-card-title">Quiz 1 <span class="ov-badge-new">Yeni</span></h4>
      <div class="ov-ann-card-meta"><span>Dr X</span><span>19.10.2026 15:53</span></div>
      <div class="st-ov-ann-full"><p>Our first quiz is next week.</p><p>Good luck!</p></div></article>"""
    (a,) = parse_announcements(page)
    assert (a.title, a.author, a.posted_at) == ("Quiz 1", "Dr X", dt.datetime(2026, 10, 19, 15, 53))
    assert a.body == "Our first quiz is next week.\nGood luck!"


def test_an_announcement_keeps_its_link_addresses_and_attached_files():
    page = """<article class="ov-ann-card"><h4 class="ov-ann-card-title">Makeup Lesson</h4>
      <div class="ov-ann-card-meta"><span>Dr X</span><span>27.10.2026 09:00</span></div>
      <div class="st-ov-ann-full"><p>Link: <a href="https://teams.microsoft.com/l/meetup-join/19%3a1?a=1&amp;b=2">Makeup Lesson | Toplantıya Katıl | Microsoft Teams</a></p><p>Sheet: <a href="https://docs.google.com/x">https://docs.google.com/x</a></p><div class="ov-ann-card-files st-ann-attachments" aria-label="Ekler"><a class="ov-ann-file"
      href="https://learn.ieu.edu.tr/student/announcements/5961/file/0" title="Quiz1 - Solutions.pdf"><span
      class="ov-ann-file-name">Quiz1 - Solutions.pdf</span> <span class="ov-ann-file-size">142 KB</span></a></div>
      </div></article>"""
    (a,) = parse_announcements(page)
    assert a.body == ("Link: Makeup Lesson | Toplantıya Katıl | Microsoft Teams "
                      "(https://teams.microsoft.com/l/meetup-join/19%3a1?a=1&b=2)\nSheet: https://docs.google.com/x\n"
                      "Quiz1 - Solutions.pdf 142 KB (https://learn.ieu.edu.tr/student/announcements/5961/file/0)")
    (f,) = a.files                                   # (the Teams and Google links are no attached files)
    assert (f.title, f.file_name, f.ext, f.kind) == ("Quiz1 - Solutions", "Quiz1 - Solutions.pdf", "pdf", "announcement")
    assert f.url == f.download_url == "https://learn.ieu.edu.tr/student/announcements/5961/file/0"


ANN_PAGE = """<article class="ov-ann-card"><h4 class="ov-ann-card-title">Quiz 1 solutions</h4>
  <div class="ov-ann-card-meta"><span>Dr X</span><span>27.10.2026 09:00</span></div>
  <div class="st-ov-ann-full"><p>Here they are.</p><div class="ov-ann-card-files"><a class="ov-ann-file"
  href="https://learn.ieu.edu.tr/student/announcements/5961/file/0" title="Quiz1 - Solutions.pdf"><span
  class="ov-ann-file-name">Quiz1 - Solutions.pdf</span> <span class="ov-ann-file-size">142 KB</span></a><a
  class="ov-ann-file" href="https://learn.ieu.edu.tr/student/announcements/5961/file/1"><span
  class="ov-ann-file-name">Grades.xlsx</span></a></div></div></article>"""


def test_announcement_files_are_course_files_of_their_own(tmp_path):
    """3.3.1 let you open an announcement's attached files from the announcement only: they never came into the
    course folder, so Export for AI chat, chats, the file search and the files list didn't have them."""
    import types

    from studia.catchup import course_needs
    from studia.chatslides import course_files
    from studia.library import ANN_UNIT
    from studia.materials import MaterialStore

    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="c", title="2026-1 MATH 240 P 1", code="MATH 240", name="P")])
    lib.merge_units("c", [Unit("u1", 0, "Hafta 1", dt.date(2026, 9, 21), dt.date(2026, 9, 27), "",
                               [Resource("r1", "Week 1", "Week1.pdf", "pdf", "pdf", "u", "/d/r1")])], first_sync=True)
    lib.merge_announcements("c", [], first_sync=True)
    (ann,) = parse_announcements(ANN_PAGE)          # side by side on the page: each file on a line of its own
    assert ann.body.splitlines()[1:] == ["Quiz1 - Solutions.pdf 142 KB (https://learn.ieu.edu.tr/student/announcements/5961/file/0)",
                                         "Grades.xlsx (https://learn.ieu.edu.tr/student/announcements/5961/file/1)"]
    assert lib.merge_announcements("c", parse_announcements(ANN_PAGE), first_sync=False)
    files = {m["resource_id"]: m for m in lib.materials("c") if m["unit_id"] == ANN_UNIT}
    assert sorted(files) == ["ann-c-5961-0", "ann-c-5961-1"]
    quiz = files["ann-c-5961-0"]
    assert (quiz["title"], quiz["file_name"], quiz["ext"], quiz["is_new"]) == ("Quiz1 - Solutions", "Quiz1 - Solutions.pdf",
                                                                              "pdf", 1)
    assert quiz["first_seen"].startswith("2026-10-27")             # when the lecturer posted it
    # the weekly plan comes again without them, and later IEULearn's list no longer has the announcement: they stay
    lib.merge_units("c", [Unit("u1", 0, "Hafta 1", dt.date(2026, 9, 21), dt.date(2026, 9, 27), "",
                               [Resource("r1", "Week 1", "Week1.pdf", "pdf", "pdf", "u", "/d/r1")])], first_sync=False)
    lib.merge_announcements("c", [Announcement("other", "Hello", "Dr X", dt.datetime(2026, 11, 2, 9), "Hi")],
                            first_sync=False)
    assert {m["resource_id"]: m["gone"] for m in lib.materials("c", include_gone=True)} == \
        {"r1": 0, "ann-c-5961-0": 0, "ann-c-5961-1": 0}
    # their own group on the course page (and in Export for AI chat, which takes the page's groups)
    d = course_detail(lib, Settings(), "c")
    (group,) = [u for u in d["general"] if u["id"] == ANN_UNIT]
    assert group["title"] == "From announcements" and [m["id"] for m in group["materials"]] == ["ann-c-5961-0",
                                                                                               "ann-c-5961-1"]
    assert not [u for u in d["general"] if u["id"] == ""]       # not "No longer in the weekly plan"
    # a full or course sync downloads them; a chat can take the PDF; they go to their own folder
    (need,) = course_needs(lib, Settings(), ["c"])
    assert {"ann-c-5961-0", "ann-c-5961-1"} <= set(need.materials)
    assert "ann-c-5961-0" in {f["id"] for f in course_files(lib, "c", [])["files"]}
    got = []
    session = types.SimpleNamespace(get=lambda url, **kw: got.append(url) or _PdfAnswer(url))
    path = MaterialStore(types.SimpleNamespace(lib=lib, output_root=tmp_path / "out")).download("ann-c-5961-0", session)
    assert got == ["https://learn.ieu.edu.tr/student/announcements/5961/file/0"]     # with the IEULearn session
    assert path.parent.name == "From announcements" and path.name == "Quiz1 - Solutions.pdf"
    assert lib.get_material("ann-c-5961-0")["local_path"] == str(path)
    lib.close()


def test_files_of_announcements_known_before_the_update_are_not_new(tmp_path):
    """3.x kept an announcement without its files. The first sync after the update reads them, and they showed as new
    course files ("2 new") although they were posted weeks before. A new announcement's files are new."""
    def attached(n, name):
        url = f"https://learn.ieu.edu.tr/student/courses/7/announcements/{n}/file/0"
        return Resource("", name.rsplit(".", 1)[0], name, "announcement", "pdf", url, url)

    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="7", title="2026-1 EEE 215", code="EEE 215", name="Circuits")])
    old = Announcement("k1", "Quiz 1 solutions", "Hoca", dt.datetime(2026, 10, 1, 10, 0), "See the file")
    lib.merge_announcements("7", [old], first_sync=True)              # 3.x: the announcement, not its file
    old.files = [attached(11, "Quiz1 - Solutions.pdf")]
    new = Announcement("k2", "Quiz 2 solutions", "Hoca", dt.datetime(2026, 11, 5, 10, 0), "See the file",
                       [attached(12, "Quiz2 - Solutions.pdf")])
    assert lib.merge_announcements("7", [new, old], first_sync=False) == ["k2"]
    assert {m["file_name"]: m["is_new"] for m in lib.materials("7")} == {"Quiz1 - Solutions.pdf": 0,
                                                                         "Quiz2 - Solutions.pdf": 1}
    lib.close()


class _PdfAnswer:
    def __init__(self, url):
        self.url, self.status_code, self.headers = url, 200, {"content-type": "application/pdf", "content-length": "9"}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self):
        pass

    def iter_content(self, size):
        yield b"%PDF-1.7 "


def test_parse_overview():
    page = """<ol class="ov-intro-lo-list"><li><span class="ov-intro-lo-no">1.</span> <span class="ov-intro-lo-text">olasılığı açıklar</span></li></ol>
      <span class="ov-ects-week-no">1</span> <span class="ov-ects-week-topic">Örnek uzay</span>
      <ul><li class="ov-ects-assess-item"><span class="ov-ects-assess-name">Quiz</span><span class="ov-ects-assess-count">4</span>
        <span class="ov-ects-assess-pct">20 %</span></li></ul><p class="ov-intro-paragraph">Bu derste olasılık.</p>"""
    ov = parse_overview(page)
    assert [(o.no, o.text) for o in ov.outcomes] == [(1, "olasılığı açıklar")]
    assert [(w.no, w.topic) for w in ov.weeks] == [(1, "Örnek uzay")]
    assert [(g.name, g.count, g.weight) for g in ov.grading] == [("Quiz", 4, 20.0)]
    assert ov.description == "Bu derste olasılık."


def test_parse_dashboard_cover_and_progress():
    page = """<article class="dash-course" data-course-id="7" data-course-status="ongoing" data-course-title="2026-1 MATH 240 Olasılık 1">
      <a href="h" class="dash-course-cover" style="background-image:url('https://learn.ieu.edu.tr/img/covers/course-cover-3.jpg?v=1')"></a>
      <span class="dash-course-school" title="Dr X">Dr X</span><span class="dash-course-students"><i></i><span>İlerleme: 40%</span></span>
      </article>"""
    _, (c,) = parse_dashboard(page)
    assert c.cover_url == "https://learn.ieu.edu.tr/img/covers/course-cover-3.jpg?v=1" and c.progress == 40


SYLLABUS = """<div id="course_name">Probability for Engineers</div>
<table><tr><td><strong>Code</strong></td><td><strong>Semester</strong></td><td><strong>Theory</strong></td>
  <td><strong>Lab</strong></td><td><strong>Local Credits</strong></td><td><strong>ECTS</strong></td></tr>
  <tr><td>MATH 240</td><td>Fall</td><td>3</td><td>0</td><td>3</td><td>6</td></tr></table>
<div id="pre_requisites">MATH 154</div>
<table><tr><td><strong>Course Language</strong></td><td>English</td></tr>
  <tr><td><strong>Course Lecturer(s)</strong></td><!-- old --><td>Dr X</td></tr></table>
<ul id="outcome"><li>explain probability</li><li>use Bayes' theorem</li></ul>
<table id="weeks"><tr id="hafta_1"><td>1</td><td>Sample spaces</td><td>Ch. 2.1</td></tr></table>
EVALUATION SYSTEM <table><tr><td>Quizzes</td><td>4</td><td>20</td></tr><tr><td>Midterm</td><td>1</td><td>30</td></tr></table>"""


def test_parse_syllabus():
    s = parse_syllabus(SYLLABUS, "MATH 240", "https://ce.ieu.edu.tr/x")
    assert s.name == "Probability for Engineers" and s.outcomes == ["explain probability", "use Bayes' theorem"]
    assert (s.semester, s.ects, s.prerequisites, s.language, s.lecturers) == ("Fall", "6", "MATH 154", "English", "Dr X")
    assert s.weeks == [{"no": 1, "subject": "Sample spaces", "preparation": "Ch. 2.1"}]
    assert s.grading == [{"activity": "Quizzes", "number": 4, "weight": 20.0}, {"activity": "Midterm", "number": 1, "weight": 30.0}]
    assert parse_syllabus("<html>no outcomes</html>") is None


# ------------------------------------------------------------------ heads-up: what the lecturer said
@pytest.mark.parametrize("text,lang,expected", [
    ("Next week, we have a quiz.", "en", "quiz"),
    ("This will be on the final.", "en", "final"),
    ("The midterm covers chapters one to four.", "en", "midterm"),
    ("Submit your project report by Friday.", "en", "project"),
    ("The homework is due next Monday.", "en", "homework"),
    ("You are responsible for LO3 in the exam.", "en", "exam"),
    ("There is no class next Tuesday.", "en", "schedule"),
    ("This is really important.", "en", "important"),
    ("Haftaya ara sınav var, LO1-LO4 sorumlusunuz.", "tr", "midterm"),
    ("Ödevleri cuma gününe kadar teslim edin.", "tr", "homework"),
    ("Finalde bu konudan soru gelecek.", "tr", "final"),
])
def test_alert_categories(text, lang, expected):
    assert categories(text, lang)[0] == expected


@pytest.mark.parametrize("text", [
    "So an assignment gives these probabilities to three outcomes.",
    "In the final step we add the two terms.",
    "We project the vector onto the plane.",
    "These two terms cancel out, so the answer is zero.",
    "For example, take a fair coin.",
])
def test_ordinary_lecture_talk_is_not_a_warning(text):
    assert categories(text) == []


def test_excerpt_points_at_the_cue():
    text = "Intro sentence about limits. " * 12 + "Next week we have a quiz on chapter two. " + "More words follow here. " * 10
    ex, hl = _excerpt(text, matches(text)["quiz"], 160)
    assert ex.startswith("…Next week") and ex[hl[0]:hl[1]] == "quiz" and len(ex) <= 162


TABLE = {"id": "a1", "title": "Quiz Takvimi", "posted_at": "2026-10-05T10:00", "first_seen": "", "body":
         "Quiz ve ara sınav takvimi aşağıdaki gibidir.\n\nSınav\n\nHafta\n\nKapsam\n\nQuiz 1\n\n5. hafta\n\nLO1–LO2\n\n"
         "Ara Sınav\n\n8. hafta\n\n—\n\nQuiz 2\n\n12. hafta\n\nLO5–LO6\n\n------\n\nAssessment\n\nWeek\n\nCoverage\n\n"
         "Quiz 1\n\nWeek 5\n\nLO1–LO2\n\nMidterm\n\nWeek 8\n\n—"}
NOTE = {"id": "a2", "title": "About Quiz 1", "posted_at": "2026-10-16T09:00", "first_seen": "",
        "body": "Our first quiz will be held next week. **Inverse Matrices (Learning Outcome 3)** will be covered."}
PROSE = {"id": "a3", "title": "Course rules", "posted_at": "2026-09-22T09:00", "first_seen": "",
         "body": "Four quizzes will be given. Quizzes will be held in the weeks specified in the course plan "
                 "(Weeks 3, 5, 10 and 12), during a class hour."}


def test_assessment_plan_from_a_table_and_a_note():
    plan = {e["name"]: e for e in assessment_plan([TABLE, NOTE])}
    assert (plan["Quiz 1"]["week"], plan["Quiz 1"]["los"]) == (5, [1, 2, 3])
    assert (plan["Midterm"]["week"], plan["Midterm"]["los"]) == (8, [])
    assert (plan["Quiz 2"]["week"], plan["Quiz 2"]["los"]) == (12, [5, 6])
    assert [s["id"] for s in plan["Quiz 1"]["sources"]] == ["a2", "a1"]          # newest first


def test_assessment_plan_from_prose():
    assert [(e["name"], e["week"]) for e in assessment_plan([PROSE])] == [("Quiz 1", 3), ("Quiz 2", 5), ("Quiz 3", 10), ("Quiz 4", 12)]


def _ann(i, title, body, posted="2026-09-22T09:00"):
    return {"id": i, "title": title, "posted_at": posted, "first_seen": "", "body": body}


def test_numbered_midterms_capitals_and_quizzes_named_with_their_weeks():
    two = _ann("m", "Exam schedule", "Midterm 1 — Week 7 — LO1–LO3\nMidterm 2 — Week 12 — LO4–LO6")
    assert {e["name"]: (e["week"], e["los"]) for e in assessment_plan([two])} == \
        {"Midterm 1": (7, [1, 2, 3]), "Midterm 2": (12, [4, 5, 6])}
    vize = _ann("v", "Sınav takvimi", "1. Vize\n7. hafta\n2. Vize\n12. hafta")
    assert [(e["name"], e["week"]) for e in assessment_plan([vize])] == [("Midterm 1", 7), ("Midterm 2", 12)]
    caps = _ann("c", "QUIZ 1 AND MIDTERM", "QUIZ 1\nWEEK 4\nMIDTERM\nWEEK 8")
    assert [(e["name"], e["week"]) for e in assessment_plan([caps])] == [("Quiz 1", 4), ("Midterm", 8)]
    table = _ann("t", "Quizzes", "Quiz 1\nWeek 3\nQuiz 2\nWeek 5")
    later = _ann("l", "Update", "Quiz 3 and Quiz 4 will be held in weeks 10 and 12.", "2026-10-20T09:00")
    assert [(e["name"], e["week"]) for e in assessment_plan([table, later])] == \
        [("Quiz 1", 3), ("Quiz 2", 5), ("Quiz 3", 10), ("Quiz 4", 12)]
    one = _ann("x", "Plan", "Midterm\nWeek 8")                  # "Midterm" and "Midterm 1" of one course: one exam
    note = _ann("n", "About Midterm 1", "It covers LO1 and LO2.", "2026-10-01T09:00")
    assert [(e["name"], e["week"], e["los"]) for e in assessment_plan([one, note])] == [("Midterm 1", 8, [1, 2])]
    assert [e["name"] for e in assessment_plan([_ann("w", "Plan", "Midterm 2. hafta")])] == ["Midterm"]   # week 2


def test_the_weeks_an_assessment_covers_are_not_the_week_it_is_held_in():
    plan = lambda *anns: [(e["name"], e["week"]) for e in assessment_plan(list(anns))]   # noqa: E731
    assert plan(_ann("a", "Quiz 2", "Quiz 2 covers weeks 1-4 and will be held in week 5.")) == [("Quiz 2", 5)]
    assert plan(_ann("b", "Plan", "Quiz 1\nWeeks 1-3\nWeek 4\nQuiz 2\nWeeks 4-6\nWeek 7")) == [("Quiz 1", 4), ("Quiz 2", 7)]
    table = _ann("t", "Quizzes", "Quiz 1\nWeek 4\nQuiz 2\nWeek 7")
    covers = _ann("c", "About Quiz 3", "Quiz 3 will cover weeks 8 and 9.", "2026-10-20T09:00")
    assert plan(table, covers) == [("Quiz 1", 4), ("Quiz 2", 7)]                  # not moved to weeks 8 and 9
    rest = _ann("r", "Quizzes", "Quiz 1 will be in week 4; quizzes 2, 3 and 4 in weeks 8, 10 and 12.")
    assert plan(rest) == [("Quiz 2", 8), ("Quiz 3", 10), ("Quiz 4", 12)]
    assert plan(_ann("q", "Quizzes", "Quiz 1-4 will be held in weeks 3, 6, 9 and 12.")) == \
        [("Quiz 1", 3), ("Quiz 2", 6), ("Quiz 3", 9), ("Quiz 4", 12)]


def test_ordinals_turkish_week_lists_pairs_on_a_line_and_postponements():
    plan = lambda *anns: [(e["name"], e["week"]) for e in assessment_plan(list(anns))]   # noqa: E731
    old = _ann("o", "Quiz-Midterm-Final Exam Schedule", "1st Quiz 4th Week Will be Announced\n2nd Quiz 7th Week Will be "
               "Announced\nMidterm 8th Week Will be Announced\n3rd Quiz 11th Week\n4th Quiz 14th Week\nFinal 15th Week",
               "2026-09-26T09:00")
    new = _ann("n", "New Quiz-Midterm-Final Exam Schedule", "1st Quiz 4th Week\n2nd Quiz 10th Week\nMidterm 8th Week\n"
               "3rd Quiz 12th Week\n4th Quiz 14th Week\nFinal 16th Week", "2026-10-27T09:00")
    content = _ann("c", "MIDTERM CONTENT", "The midterm covers everything up to and including the definition of “Slope” "
                   "in the Week 5 notes.", "2026-11-04T09:00")
    assert plan(old, new, content) == [("Quiz 1", 4), ("Midterm", 8), ("Quiz 2", 10), ("Quiz 3", 12), ("Quiz 4", 14),
                                       ("Final", 16)]
    table = _ann("t", "Quizzes", "Quiz 1\nWeek 4\nQuiz 2\nWeek 7")
    moved = _ann("m", "Change", "Dear students, Quiz 2 has been postponed to the 10th week due to the national holiday "
                 "on 29 October.", "2026-10-20T09:00")
    assert plan(table, moved) == [("Quiz 1", 4), ("Quiz 2", 10)]
    assert plan(table, _ann("e", "Erteleme", "Quiz 2 10. haftaya ertelendi.", "2026-10-20T09:00")) == \
        [("Quiz 1", 4), ("Quiz 2", 10)]
    assert plan(table, _ann("r", "Quiz 2", "Quiz 2 results from week 7 are out; the make-up will be held in week 9.",
                            "2026-10-20T09:00")) == [("Quiz 1", 4), ("Quiz 2", 7)]
    assert plan(_ann("tr", "Sınavlar", "Quiz 1–4\n3., 5., 10. ve 12. haftalarda yapılacaktır.")) == \
        [("Quiz 1", 3), ("Quiz 2", 5), ("Quiz 3", 10), ("Quiz 4", 12)]
    assert plan(_ann("k", "Quizler", "Quiz 1-4 sırasıyla 2., 4., 6. ve 8. haftaların konularını kapsayacaktır.")) == []
    assert plan(_ann("w", "Quizler", "Quiz 1-4: 3, 5, 10 ve 12. haftalarda")) == []         # not Quiz 1 in week 12
    assert plan(_ann("p", "Plan", "Quiz 1: Week 3, Quiz 2: Week 5, Quiz 3: Week 10")) == \
        [("Quiz 1", 3), ("Quiz 2", 5), ("Quiz 3", 10)]
    assert plan(_ann("q", "Plan", "Midterm: Week 8. Quiz 2: Week 10.")) == [("Midterm", 8), ("Quiz 2", 10)]


@pytest.mark.parametrize("body", ["Midterm Exam 1\nWeek 7\nMidterm Exam 2\nWeek 12",
                                  "Midterm-1: Week 7\nMidterm-2: Week 12", "Midterm I\nWeek 7\nMidterm II\nWeek 12"])
def test_two_midterms_however_they_are_written(body):
    assert [(e["name"], e["week"]) for e in assessment_plan([_ann("m", "Exams", body)])] == \
        [("Midterm 1", 7), ("Midterm 2", 12)]


def test_an_edited_announcement_brings_its_new_text(tmp_path):
    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="c", title="t", code="MATH 240", name="P")])
    lib.merge_announcements("c", [Announcement("a1", "Quizzes", "Dr X", dt.datetime(2026, 10, 1, 9), "Quiz 2\n6. hafta")],
                            first_sync=True)
    lib.merge_announcements("c", [Announcement("a1", "Quizzes", "Dr X", dt.datetime(2026, 10, 1, 9), "Quiz 2\n5. hafta")],
                            first_sync=False)
    (a,) = lib.announcements("c")
    assert a["body"] == "Quiz 2\n5. hafta" and not a["is_new"]
    assert [(e["name"], e["week"]) for e in course_alerts(lib, "c")["plan"]] == [("Quiz 2", 5)]


def test_an_announcement_whose_title_was_edited_stays_one_announcement(tmp_path):
    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="c1", title="t", code="MATH 240", name="P"),
                        Course(id="c2", title="t", code="MATH 250", name="L")])
    when = dt.datetime(2026, 10, 1, 9)
    hw = Announcement("h", "Homework 1", "Dr X", when, "Due Friday")      # another post in the same minute
    for cid in ("c1", "c2"):
        assert lib.merge_announcements(cid, [Announcement("a1", "Quiz 2 on Thursady", "Dr X", when, "Room 301"), hw],
                                       first_sync=False) == (["a1", "h"] if cid == "c1" else [])
    for _ in range(2):                                  # (and the syncs after that: still the one kept)
        for cid in ("c1", "c2"):                        # the title corrected a minute later: a new id from its title
            assert lib.merge_announcements(cid, [Announcement("a2", "Quiz 2 on Thursday", "Dr X", when, "Room 301"),
                                                 hw], first_sync=False) == []
            assert sorted(a["title"] for a in lib.announcements(cid)) == ["Homework 1", "Quiz 2 on Thursday"]


def test_the_same_post_in_two_courses_is_kept_in_both(tmp_path):
    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="c1", title="t", code="MATH 240", name="P"),
                        Course(id="c2", title="t", code="MATH 250", name="L")])
    when = dt.datetime(2026, 10, 1, 9)                 # same title, author and minute: the same id from IEULearn
    news = [lib.merge_announcements(cid, [Announcement("a1", "Quiz 2", "Dr X", when, body)], first_sync=False)
            for _ in range(2) for cid, body in (("c1", "Quiz 2\n7. hafta"), ("c2", "Quiz 2\n9. hafta"))]
    assert news == [["a1"], [], [], []]                # one post, one piece of news
    assert [(a["body"], a["is_new"]) for a in lib.announcements("c1")] == [("Quiz 2\n7. hafta", 1)]
    assert [(a["body"], a["is_new"]) for a in lib.announcements("c2")] == [("Quiz 2\n9. hafta", 0)]
    assert [e["week"] for e in course_alerts(lib, "c1")["plan"]] == [7]


def _course_with_lecture(tmp_path):
    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="c", title="2026-1 MATH 240 P 1", code="MATH 240", name="P", term="T")])
    rec = Recording(delivery_id="r1", course_id="c", folder_id="f", folder_path="", name="n",
                    start=dt.datetime(2026, 10, 14, 9, 0), duration_s=3000, viewer_url="https://v/r1")
    lib.merge_recordings("c", [rec], first_sync=True)
    t = Transcript(delivery_id="r1", segments=[Segment(0, 6, "Today we continue with conditional probability.", "en", 0.9),
                                              Segment(60, 66, "Next week we have a quiz on this topic.", "en", 0.9),
                                              Segment(70, 75, "Remember, the quiz is in the first hour.", "en", 0.9),
                                              Segment(900, 906, "Bu konu finalde çıkacak.", "tr", 0.9)],
                   duration_s=3000, speech_s=30, language_seconds={"en": 20, "tr": 6}, model="m", device="cpu",
                   engine_version="t", created_at="2026-10-14T12:00:00")
    lib.save_transcript(t, store.save_transcript(t))
    lib.set_status("r1", "done")
    lib.merge_announcements("c", [Announcement("a1", "Class Rescheduling", "Dr X", dt.datetime(2026, 10, 19, 15, 53),
                                               "Our class has been rescheduled to Saturday.")], first_sync=False)
    return lib, rec


def test_course_alerts_from_lectures_and_announcements(tmp_path):
    lib, rec = _course_with_lecture(tmp_path)
    al = course_alerts(lib, "c")
    found = [(i["category"], i["source"]) for i in al["items"]]
    assert found[0] == ("schedule", "announcement") and ("final", "lecture") in found
    quiz = [i for i in al["items"] if i["category"] == "quiz"]
    assert len(quiz) == 1                       # the reminder 10 s later is the same point
    q = quiz[0]
    assert (q["day_key"], q["part"], q["t"], q["ts"]) == (rec.day_key, 1, 60.0, "01:00")
    assert q["text"][q["hl"][0]:q["hl"][1]] == "quiz"
    assert course_alerts(lib, "c") is al        # cached until a transcript or an announcement changes
    lib.merge_announcements("c", [Announcement("a2", "Homework 1", "Dr X", dt.datetime(2026, 10, 20, 9, 0),
                                               "Homework 1 is due next Monday.")], first_sync=False)
    assert course_alerts(lib, "c")["items"][0]["category"] == "homework"


# ------------------------------------------------------------------ weeks, exams, covers
def test_numbered_units_and_the_term_calendar(tmp_path):
    assert unit_week({"title": "Konu 3", "start_date": ""}, None) == 3
    assert unit_week({"title": "LAB MATERIALS", "start_date": ""}, None) is None
    first = dt.date(2026, 9, 21)                 # a Monday; this plan's week 5 starts on the Sunday before
    assert unit_week({"title": "Hafta 5", "start_date": "2026-10-18", "end_date": "2026-10-24"}, first) == 5
    assert unit_week({"title": "Hafta 4", "start_date": "2026-10-12", "end_date": "2026-10-17"}, first) == 4
    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="a", title="2026-1 EEE 215 X 1", code="EEE 215", name="X", term="T"),
                        Course(id="b", title="2026-1 MATH 250 Y 3", code="MATH 250", name="Y", term="T")])
    lib.merge_units("a", [Unit("u1", 0, "Hafta 1", dt.date(2026, 9, 23), dt.date(2026, 9, 29))], first_sync=True)
    lib.merge_units("b", [Unit("k3", 0, "Konu 3")], first_sync=True)
    lib.merge_recordings("b", [Recording(delivery_id="r", course_id="b", folder_id="f", folder_path="", name="n",
                                         start=dt.datetime(2026, 10, 7, 10, 0), duration_s=2400, viewer_url="v")],
                         first_sync=True)
    assert lib.term_start("T") == "2026-09-23"
    d = course_detail(lib, Settings(), "b")
    w3 = next(w for w in d["weeks"] if w["no"] == 3)
    assert w3["start"] == "2026-10-05" and [u["title"] for u in w3["units"]] == ["Konu 3"] and len(w3["days"]) == 1
    assert d["alerts"]["items"] == [] and d["alerts"]["plan"] == []


def test_a_unit_for_two_weeks_shows_its_files_in_both(tmp_path):
    """EEE 215's undated "Hafta 6 ve 7 Capacitive and Inductive circuits": its files are week 7's too (the current
    week said "No files for this week"), the weekly digest offers them, and a plan over both weeks reads them once."""
    from studia.digest import weekly_digest
    from studia.plans import Scope, gather

    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="e", title="2026-1 EEE 215 X 1", code="EEE 215", name="Circuits", term="T")])
    first = dt.date(2026, 9, 21)
    files = [Resource(f"r{i}", f"Slides {i}", f"Slides{i}.pdf", "pdf", "pdf", "u", "d") for i in (1, 2, 3)]
    lib.merge_units("e", [Unit(f"u{n}", n, f"Hafta {n}", first + dt.timedelta(weeks=n - 1),
                               first + dt.timedelta(weeks=n - 1, days=6)) for n in range(1, 6)]
                    + [Unit("u67", 6, "Hafta 6 ve 7 Capacitive and Inductive circuits", resources=files),
                       Unit("k8", 8, "Konu 8 and 9 review")], first_sync=True)
    now = dt.datetime(2026, 11, 6, 12, 0)
    d = course_detail(lib, Settings(), "e", now=now)
    weeks = {w["no"]: w for w in d["weeks"]}
    for n in (6, 7):
        assert [m["id"] for u in weeks[n]["units"] for m in u["materials"]] == ["r1", "r2", "r3"]
    assert weeks[7]["current"]
    assert [u["title"] for u in weeks[8]["units"]] == ["Konu 8 and 9 review"] and 9 not in weeks   # a topic number
    dg = weekly_digest(lib, Settings(), 0, today=now.date(), now=now)
    assert sorted(x["resource_id"] for x in dg["todo"] if x["kind"] == "download") == ["r1", "r2", "r3"]
    pack = gather(lib, Settings(), "e", Scope(kind="prep", weeks=[6, 7]))
    assert sorted(m["id"] for m in pack["materials_missing"]) == ["r1", "r2", "r3"]


def test_a_file_s_own_week_beats_its_topic_number_on_the_weeks_tab(tmp_path):
    """MATH 250: undated "Konu N" units whose files are named for the week after from Konu 10 on (the midterm week
    shifts the topics): the Weeks tab, and so the export, review sheets and the digest, place them as chat does."""
    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="m", title="2026-1 MATH 250 L 1", code="MATH 250", name="Linear Algebra", term="T")])
    names = {8: "MATH_250_Week 8-9", 9: "MATH_250_Week 9-10", 10: "MATH_250_Week 11", 11: "MATH_250_Week 12"}
    res = lambda i, name: Resource(i, name, f"{name}.pdf", "pdf", "pdf", "u", "d")     # noqa: E731
    lib.merge_units("m", [Unit("k1", 1, "Hafta 1", dt.date(2026, 9, 21), dt.date(2026, 9, 27))]
                    + [Unit(f"k{n}", n, f"Konu {n}", resources=[res(f"m{n}", name)]) for n, name in names.items()]
                    + [Unit("k7", 7, "Konu 7", resources=[res("m7", "Konu 7 notes"), res("c5", "Chapter 5 slides")])],
                    first_sync=True)
    d = course_detail(lib, Settings(), "m", now=dt.datetime(2026, 11, 9, 12, 0))
    files = {w["no"]: sorted(m["id"] for u in w["units"] for m in u["materials"]) for w in d["weeks"]}
    assert {n: files.get(n) for n in range(7, 13)} == {7: ["c5", "m7"], 8: ["m8"], 9: ["m8", "m9"], 10: ["m9"],
                                                       11: ["m10"], 12: ["m11"]}     # (a chapter is no week)
    assert [u["title"] for w in d["weeks"] if w["no"] == 11 for u in w["units"]] == ["Konu 10"]


def test_a_file_asked_for_twice_in_one_go_is_downloaded_once():
    import threading

    from studia.api import Api

    a = Api.__new__(Api)
    a._mat_lock, a._mat_queue, a._mat_running = threading.Lock(), [], True     # (a download is running already)
    pushed = []
    a._push = pushed.append
    assert a.materials_download(["r1", "r2", "r1"])["queued"] == 2 and a._mat_queue == ["r1", "r2"]
    assert len(pushed) == 2


def _rec(cid, i, day, hour, minutes=45):
    return Recording(delivery_id=f"{cid}{i}", course_id=cid, folder_id="f", folder_path="", name="n",
                     start=dt.datetime(2026, 10, day, hour, 30), duration_s=minutes * 60, viewer_url="v")


def test_the_class_days_tell_when_an_announced_quiz_is_held(tmp_path):
    """IEULearn weeks of a Wednesday course run from Wednesday to Tuesday. A quiz announced for week 5 ("in an
    unannounced class hour") is held on its Wednesday, so on Friday it is done although the week is not over."""
    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="p", title="2026-1 MATH 240 P 1", code="MATH 240", name="P", term="T"),
                        Course(id="m", title="2026-1 MATH 250 M 3", code="MATH 250", name="M", term="T")])
    first = dt.date(2026, 9, 23)
    lib.merge_units("p", [Unit(f"u{n}", n, f"Hafta {n}", first + dt.timedelta(weeks=n - 1),
                               first + dt.timedelta(weeks=n - 1, days=6)) for n in range(1, 9)], first_sync=True)
    lib.merge_recordings("p", [_rec("p", 0, 7, 8), _rec("p", 1, 7, 9), _rec("p", 2, 14, 8), _rec("p", 3, 14, 9)],
                         first_sync=True)
    lib.merge_announcements("p", [Announcement("a1", "About the course", "Dr X", dt.datetime(2026, 9, 22, 14, 0),
                                               "Quiz 1–4\nWeeks 3, 5, 10 and 12, in an unannounced class hour")],
                            first_sync=True)
    friday_evening = dt.datetime(2026, 10, 23, 19, 0)
    d = course_detail(lib, Settings(), "p", now=friday_evening)
    assert [(e["name"], e["week"]) for e in d["alerts"]["plan"]][:2] == [("Quiz 1", 3), ("Quiz 2", 5)]
    w5, w6 = (next(w for w in d["weeks"] if w["no"] == n) for n in (5, 6))
    assert (w5["start"], w5["end"], w5["current"], w5["past"]) == ("2026-10-21", "2026-10-27", True, False)
    assert w5["classes"] == [{"date": "2026-10-21", "end": "10:15"}] and w6["classes"][0]["date"] == "2026-10-28"
    assert classes_over(w5["classes"], friday_evening)
    assert not classes_over(w5["classes"], dt.datetime(2026, 10, 21, 10, 0))      # in class: not yet
    assert not classes_over(w6["classes"], friday_evening) and not classes_over([], friday_evening)

    # a Friday course without dates in its plan: weeks from the term's calendar (Monday to Sunday)
    lib.merge_units("m", [Unit(f"k{n}", n, f"Konu {n}") for n in range(1, 9)], first_sync=True)
    lib.merge_recordings("m", [_rec("m", 0, 16, 10, 150), _rec("m", 1, 23, 10, 150)], first_sync=True)
    m = course_detail(lib, Settings(), "m", now=friday_evening)
    w7 = next(w for w in m["weeks"] if w["no"] == 7)
    assert (w7["start"], w7["classes"]) == ("2026-11-02", [{"date": "2026-11-06", "end": "13:00"}])


def test_class_times_follow_your_section_and_skip_extra_recordings():
    rec = lambda end, **kw: {"end_time": end, "duplicate": False, "hidden": False, "too_short": False, **kw}  # noqa: E731
    days = [{"date": "2026-10-14", "mine": True, "recordings": [rec("09:20"), rec("11:15"), rec("23:50", duplicate=True)]},
            {"date": "2026-10-15", "mine": False, "recordings": [rec("16:00")]},              # another section
            {"date": "2026-10-16", "mine": None, "recordings": [rec("12:55"), rec("13:10", too_short=True)]},
            {"date": "2026-08-03", "mine": True, "recordings": [rec("17:00")]}]               # long ago
    times = class_times(days, dt.date(2026, 10, 23))
    assert times == {2: "11:15", 4: "12:55"}
    assert week_classes("2026-10-19", "2026-10-25", times) == [{"date": "2026-10-21", "end": "11:15"},
                                                               {"date": "2026-10-23", "end": "12:55"}]
    assert week_classes("", "", times) == [] and week_classes("2026-10-19", "2026-10-25", {}) == []


def test_a_one_off_make_up_class_is_not_a_class_day():
    rec = {"end_time": "10:50", "duplicate": False, "hidden": False, "too_short": False}
    days = [{"date": d, "mine": None, "recordings": [rec]} for d in ("2026-09-30", "2026-10-07", "2026-10-14",
                                                                     "2026-10-21", "2026-10-17")]   # Wednesdays, one Saturday
    assert class_times(days, dt.date(2026, 10, 23)) == {2: "10:50"}


@pytest.mark.parametrize("topic,exam", [("Midterm Exam", True), ("MIDTERM", True), ("Ara Sınav", True), ("ARA SINAV", True),
                                        ("Vize", True), ("Final", True), ("FINAL EXAM", True), ("Final exam", True),
                                        ("Project analysis; final report", False), ("Review for the final", False)])
def test_exam_week_topics(topic, exam):
    assert exam_topic(topic) == exam


def test_old_library_gets_the_new_course_columns(tmp_path):
    db = sqlite3.connect(tmp_path / "old.db")
    db.execute("CREATE TABLE courses(id TEXT PRIMARY KEY, title TEXT, code TEXT, name TEXT, instructor TEXT, term TEXT, "
               "status TEXT, section TEXT, folder_id TEXT, has_panopto INTEGER DEFAULT 1, position INTEGER DEFAULT 0, "
               "active INTEGER DEFAULT 1)")
    db.execute("INSERT INTO courses(id, title, code, name) VALUES('c', 't', 'MATH 240', 'P')")
    db.commit()
    db.close()
    lib = Library(tmp_path / "old.db")
    c = lib.get_course("c")
    assert (c.cover_url, c.progress) == ("", None)
    lib.upsert_courses([Course(id="c", title="t", code="MATH 240", name="P", cover_url="https://x/c.jpg", progress=40)])
    assert (lib.get_course("c").cover_url, lib.get_course("c").progress) == ("https://x/c.jpg", 40)


def test_cover_pictures_are_cached_once(monkeypatch):
    calls = []

    class Resp:
        ok, content, headers = True, b"\xff\xd8jpeg", {"content-type": "image/jpeg"}

    monkeypatch.setattr(coursesite.requests, "get", lambda url, timeout: calls.append(url) or Resp())
    url = "https://learn.ieu.edu.tr/img/covers/course-cover-1.jpg?v=2"
    assert _cover_uri(url) == ""
    path = coursesite.fetch_cover(url)
    assert path.read_bytes() == b"\xff\xd8jpeg" and coursesite.fetch_cover(url) == path and calls == [url]
    assert _cover_uri(url) == path.as_uri()


def test_alert_cache_is_per_course(tmp_path):
    alerts._cache.clear()
    lib, _ = _course_with_lecture(tmp_path)
    course_alerts(lib, "c")
    assert set(alerts._cache) == {"c"}


def test_the_heads_up_is_kept_between_starts_until_something_it_is_found_from_changes(tmp_path, monkeypatch):
    import dataclasses
    import os

    lib, rec = _course_with_lecture(tmp_path)
    reads = []
    load = store.load_transcripts
    monkeypatch.setattr(store, "load_transcripts", lambda ids: reads.append(list(ids)) or load(ids))

    def start():                                # a new start: nothing in memory, the library opened again
        alerts._cache.clear()
        return course_alerts(Library(tmp_path / "lib.db"), "c")

    alerts._cache.clear()
    first = course_alerts(lib, "c")
    assert reads == [["r1"]] and first["items"]
    assert start() == first and reads == [["r1"]]                 # the same items, no transcript read
    moved = course_alerts(lib, "c", [dataclasses.replace(rec, viewer_url="https://v/other")])
    assert {i.get("viewer_url") for i in moved["items"]} == {None, "https://v/other"} and len(reads) == 2   # shown
    assert start() == first and len(reads) == 3

    t = store.load_transcript("r1")                                # transcribed again: a new transcript
    t.segments.append(Segment(1200, 1206, "The midterm is next week.", "en", 0.9))
    store.save_transcript(t)
    path = store.transcript_path("r1")
    os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 10**9))
    again = start()
    assert len(reads) == 4 and "midterm" in again["counts"] and again != first
    assert start() == again and len(reads) == 4

    lib.merge_announcements("c", [Announcement("a2", "Homework 1", "Dr X", dt.datetime(2026, 10, 20, 9, 0),
                                               "Homework 1 is due next Monday.")], first_sync=False)
    news = start()
    assert len(reads) == 5 and news["items"][0]["category"] == "homework"
    monkeypatch.setattr(alerts, "ALERTS_VERSION", alerts.ALERTS_VERSION + 1)     # the matching changed
    assert start() == news and len(reads) == 6
    alerts._cache.clear()


def test_course_files_keep_their_extension_and_two_of_one_name_keep_both(tmp_path):
    import types

    from studia.materials import MaterialStore

    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="c", title="2026-1 MATH 240 P 1", code="MATH 240", name="P", term="T")])
    long_name = "Lecture notes on " + "conditional probability and Bayes " * 4 + "final.pdf"
    lib.merge_units("c", [Unit("u1", 1, "Hafta 1", dt.date(2026, 9, 21), dt.date(2026, 9, 27), "",
                               [Resource("r1", "Notes", long_name, "pdf", "pdf", "u", "d"),
                                Resource("r2", "Homework", "Homework.pdf", "pdf", "pdf", "u", "d"),
                                Resource("r3", "Homework (new)", "Homework.pdf", "pdf", "pdf", "u", "d")])],
                    first_sync=True)
    store_ = MaterialStore(types.SimpleNamespace(lib=lib, output_root=tmp_path / "out"))
    first = store_.target_path(lib.get_material("r1"))
    assert first.suffix == ".pdf" and len(first.name) <= 120
    hw = store_.target_path(lib.get_material("r2"))
    lib.set_material_file("r2", str(hw), 10, "u")
    assert store_.target_path(lib.get_material("r2")) == hw                     # its own file: the same name
    assert store_.target_path(lib.get_material("r3")).name == "Homework (r3).pdf"


def test_early_in_the_term_every_class_day_counts():
    rec = {"end_time": "09:50", "duplicate": False, "hidden": False, "too_short": False}
    days = [{"date": d, "mine": None, "recordings": [rec]} for d in ("2026-09-21", "2026-09-23", "2026-09-28")]
    assert class_times(days, dt.date(2026, 9, 29)) == {0: "09:50", 2: "09:50"}   # Wednesday seen once so far
    unrecorded = [{"date": d, "mine": None, "recordings": [rec]} for d in ("2026-09-21", "2026-09-28", "2026-09-30")]
    assert class_times(unrecorded, dt.date(2026, 10, 6)) == {0: "09:50", 2: "09:50"}   # week 1's Wednesday missing


def test_more_ways_of_writing_quiz_and_midterm_numbers():
    plan = lambda *anns: [(e["name"], e["week"]) for e in assessment_plan(list(anns))]   # noqa: E731
    table = _ann("t", "Quizzes", "Quiz 1\nWeek 4\nQuiz 2\nWeek 7")
    hashes = _ann("h", "Update", "Quiz #3 and #4 will be held in weeks 10 and 12.", "2026-10-20T09:00")
    assert plan(table, hashes) == [("Quiz 1", 4), ("Quiz 2", 7), ("Quiz 3", 10), ("Quiz 4", 12)]
    assert plan(_ann("p", "Exams", "Midterm I will announce later.\nMidterm\nWeek 8")) == [("Midterm", 8)]
    assert plan(_ann("x", "Exams", "Midterm 2 hours\nWeek 8")) == [("Midterm", 8)]          # a duration, no number


@pytest.mark.parametrize("body", ["Quizzes 1 to 4 will be held in weeks 3, 6, 9 and 12.",
                                  "Quizzes 1 through 4 will be held in weeks 3, 6, 9 and 12.",
                                  "Four quizzes (Quiz 1 to Quiz 4) will be given in weeks 3, 6, 9 and 12."])
def test_quiz_numbers_written_as_a_range_in_words(body):
    assert [(e["name"], e["week"]) for e in assessment_plan([_ann("q", "Quizzes", body)])] == \
        [("Quiz 1", 3), ("Quiz 2", 6), ("Quiz 3", 9), ("Quiz 4", 12)]


def test_the_day_of_a_date_is_no_midterm_or_quiz_number():
    plan = lambda *anns: [(e["name"], e["week"]) for e in assessment_plan(list(anns))]   # noqa: E731
    old = _ann("o", "Exams", "Midterm\nWeek 8")
    moved = _ann("n", "Midterm moved", "Midterm Exam - 2 December (Week 11)", "2026-10-20T09:00")
    assert plan(old, moved) == [("Midterm", 11)]                                   # one midterm, at its new week
    assert plan(_ann("v", "Sınav", "Vize Sınavı 2 Aralık\n11. hafta")) == [("Midterm", 11)]
    assert plan(_ann("d", "Exams", "Midterm - 2 May (Week 12)")) == [("Midterm", 12)]
    assert plan(_ann("m", "Update", "Midterm 2 may be moved to week 13.")) == [("Midterm 2", 13)]
    assert plan(_ann("u", "Exams", "Midterm 1 Nov 3\nWeek 7")) == [("Midterm 1", 7)]          # the day after the month
    assert plan(_ann("f", "Exams", "Midterm 1 November 5.\nWeek 7\nMidterm 2 December 10.\nWeek 12")) == \
        [("Midterm 1", 7), ("Midterm 2", 12)]
    assert plan(_ann("k", "Sınavlar", "Vize 2 Aralıkta\n10. hafta")) == [("Midterm", 10)]
    one = _ann("o", "Sınav takvimi", "Ara Sınav\n8. hafta")
    for title, body in (("Ara Sınavı Ertelendi", "Ara Sınavı 2.12.2026\n10. hafta"),
                        ("Sınav", "Ara Sınavı 3 Ara\n10. hafta"), ("Exams", "Midterm - 2.12.2026 (Week 10)")):
        assert plan(one, _ann("n", title, body, "2026-10-20T09:00")) == [("Midterm", 10)], body
    for title, body in (("Ara Sınavı 3. Şube Sınıf Listesi", "LO1-LO4"), ("Sınav", "Vize 2 saatlik olacak\nLO1-LO4")):
        assert [(e["name"], e["los"]) for e in assessment_plan([one, _ann("n", title, body)])] == \
            [("Midterm", [1, 2, 3, 4])], title
    table = _ann("q", "Quizzes", "Quiz 1\nWeek 4\nQuiz 2\nWeek 7\nQuiz 3\nWeek 10\nQuiz 4\nWeek 13")
    for body in ("Quizzes 1 to 4 will cover the topics of weeks 2, 4, 6 and 8 respectively.",
                 "Quiz 2 - 12 May will cover the topics of weeks 5 and 6.",
                 "Quiz 2 - 13:30, Room D-201, in weeks 5 and 6"):
        assert plan(table, _ann("x", "Quizzes", body, "2026-10-20T09:00")) == \
            [("Quiz 1", 4), ("Quiz 2", 7), ("Quiz 3", 10), ("Quiz 4", 13)], body
    table = _ann("t", "Quizzes", "Quiz 1\nWeek 4\nQuiz 2\nWeek 7\nQuiz 3\nWeek 10\nQuiz 4\nWeek 13")
    dash = _ann("x", "Quiz 2", "Dear students, Quiz 2 - 3 November - will cover the topics of weeks 5 and 6",
                "2026-10-20T09:00")
    assert plan(table, dash) == [("Quiz 1", 4), ("Quiz 2", 7), ("Quiz 3", 10), ("Quiz 4", 13)]


def test_turkish_and_longer_midterm_names():
    plan = lambda *anns: [(e["name"], e["week"]) for e in assessment_plan(list(anns))]   # noqa: E731
    assert plan(_ann("a", "Ara Sınavı", "Ara Sınavı: 8. hafta")) == [("Midterm", 8)]     # the ı is no Roman I
    assert plan(_ann("b", "Sınavlar", "Ara Sınavı 1\n7. hafta\nAra Sınavı 2\n12. hafta")) == \
        [("Midterm 1", 7), ("Midterm 2", 12)]
    assert plan(_ann("e", "Exams", "Midterm Examination 2\nWeek 12")) == [("Midterm 2", 12)]
    table = _ann("t", "Exams", "Midterm 1\nWeek 7\nMidterm 2\nWeek 12")
    guide = _ann("g", "Midterm 1 study guide", "It covers LO1-LO3.", "2026-10-20T09:00")
    assert {e["name"]: (e["week"], e["los"]) for e in assessment_plan([table, guide])} == \
        {"Midterm 1": (7, [1, 2, 3]), "Midterm 2": (12, [])}
    second = _ann("s", "EEE 215 Ara Sınavı", "Konular: LO5-LO8", "2026-10-20T09:00")
    assert all(5 not in e["los"] for e in assessment_plan([table, second]) if e["name"] == "Midterm 1")
