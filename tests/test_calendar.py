"""4.0.0, the calendar: IEULearn's assignment deadlines (the weekly plan's items, and the assignments panel's day and
time and whether each was sent), a weekly plan with only assignments read as it is, exam weeks that only talk about
an exam left out, the day an announcement gives for a quiz or an exam, make-up and cancelled classes, the Calendar
page's term (how sure each date is, class days held and to come), your own entries, the reminders' timing (never
twice, quiet when off) and the .ics file with stable ids."""

import datetime as dt
import sqlite3

import pytest

from studia import agenda, agent, calendar_export, coursesite, views
from studia.alerts import assessment_plan
from studia.config import CoursePrefs, Settings
from studia.coursesite import (Assignment, Unit, assignments_problem, parse_assignments, parse_unit_plan,
                               unit_plan_problem)
from studia.domain import Course, Recording
from studia.library import Library

TITLE = "2026-1 EEE 242 Sayısal Tasarım 1"


# ------------------------------------------------------------------ IEULearn's pages (made up, in IEULearn's shape)
def _plan_asg(aid, name, desc, done):
    return (f'<li class="ovup-syl-item-wrap{" is-sim-done" if done else ""}" data-syl-kind="assignment" data-syl-id="{aid}">'
            f'<a class="ovup-syl-item" href="https://learn.ieu.edu.tr/student/courses/7?asg={aid}#unitplan" data-asg-deep="{aid}">'
            '<span class="ovup-syl-item-ico fi fi-assignment" aria-hidden="true"><i class="fas fa-list-check"></i></span>'
            f'<span class="ovup-syl-item-main"><span class="ovup-syl-item-name" title="{name}">{name}</span>'
            f'<span class="ovup-syl-item-desc">{desc}</span></span><span class="ovup-syl-item-status" title="Tamamlandı" '
            f'aria-label="Tamamlandı"{"" if done else " hidden"}><i class="fas fa-check-circle"></i></span></a>'
            f'<div class="ovup-syl-item-body ovup-syl-item-body--lazy" data-lazy-kind="assignment" data-lazy-id="{aid}"></div></li>')


RESOURCE = ('<section class="ovup-syl-block"><ul class="ovup-syl-items"><li class="ovup-syl-item-wrap" '
            'data-sim-resource-id="9101" data-syl-kind="resource" data-syl-id="9101"><a class="ovup-syl-item" '
            'href="javascript:void(0)" data-rv-payload="{&quot;id&quot;:9101,&quot;title&quot;:&quot;Lab sheet&quot;,'
            '&quot;type&quot;:&quot;pdf&quot;,&quot;file_ext&quot;:&quot;pdf&quot;,&quot;url&quot;:&quot;/f/9101&quot;,'
            '&quot;file_name&quot;:&quot;lab.pdf&quot;}"><span class="ovup-syl-item-main"><span class="ovup-syl-item-name" '
            'title="Lab sheet">Lab sheet</span></span></a></li></ul></section>')


def _plan(with_files=True):
    return ('<div class="ovup-syl ovup-syl--student"><article class="curriculum-item" data-item-id="601">'
            '<span class="ovup-title-text" title="LAB MATERIALS">LAB MATERIALS</span>'
            '<section class="ovup-syl-folder" data-syl-kind="topic"><ul class="ovup-syl-items">'
            + _plan_asg("3001", "Lab 1", "Son teslim: 21 Eki", True) + _plan_asg("3002", "Lab 2", "Son teslim: 04 Kas", False)
            + "</ul></section>" + (RESOURCE if with_files else "") + "</article>"
            '<article class="curriculum-item" data-item-id="602"><span class="ovup-title-text" title="Konu 2">Konu 2</span>'
            "</article></div>")


def _card(aid, title, group, due_line, chip="pending"):
    return (f'<article class="sa-card st-assign" data-sa-group="{group}" data-sa-id="{aid}"><button type="button" '
            f'class="st-assign-link" data-sa-open="{aid}" data-sa-title="{title}" title="Ödevi aç"><span class="st-assign-date" '
            f'aria-hidden="true"><b>04</b><small>Kas</small></span><span class="st-assign-main"><span class="st-assign-title">'
            f'{title} <span class="st-assign-group" title="Grup">Grup</span></span><span class="st-assign-meta"><span '
            f'class="st-assign-dot" aria-hidden="true"></span>{TITLE} <span class="st-assign-meta-sep" aria-hidden="true">·'
            f'</span> {due_line} </span></span><span class="st-assign-chip st-assign-chip--{chip}">x</span></button></article>')


def _row(aid, title, due, sent):
    return (f'<tr><td><a href="https://learn.ieu.edu.tr/student/assignment/{aid}">{title}</a></td><td>{TITLE}</td>'
            f'<td>{due}</td><td><span class="sa-submit-pill is-{"yes" if sent else "no"}">x</span></td><td>x</td>'
            '<td class="sa-grade-cell">—</td></tr>')


def _panel(cards, rows=(), total=None):
    n = len(cards) if total is None else total
    return ('<div class="sa-page sa-page--overlay" data-sa-overlay="1"><div class="sa-stats"><div class="sa-stat"><span '
            f'class="sa-stat-text"><b>{n}</b><small>Toplam ödev</small></span></div></div>'
            + ('<div class="sa-list">' + "".join(cards) + "</div>" if cards else '<div class="sa-empty"><h3>Henüz ödev yok</h3></div>')
            + ('<table class="sa-grades-table"><tbody>' + "".join(rows) + "</tbody></table>" if rows else "") + "</div>")


PANEL = _panel([_card("3003", "Lab 3", "pending", "Son teslim 18 Kas 2026, 15:40"),
                _card("3002", "Lab 2", "submitted", "Son teslim 04 Kas 2026, 15:40", "done"),
                _card("3001", "Lab 1", "graded", "Son teslim 21 Eki 2026, 15:50", "done")],
               [_row("3003", "Lab 3", "18.11.2026 15:40", False), _row("3002", "Lab 2", "04.11.2026 15:40", True),
                _row("3001", "Lab 1", "21.10.2026 15:50", True)])


# ------------------------------------------------------------------ the weekly plan's assignments
def test_the_weekly_plans_assignments_are_read_with_their_day_and_tick():
    units = parse_unit_plan(_plan(), TITLE)
    assert [len(u.resources) for u in units] == [1, 0]
    asg = units[0].assignments
    assert [(a.id, a.title, a.due, a.done, a.unit_id) for a in asg] == [("3001", "Lab 1", "2026-10-21", True, "601"),
                                                                        ("3002", "Lab 2", "2026-11-04", False, "601")]
    assert asg[0].url == "https://learn.ieu.edu.tr/student/courses/7?asg=3001#unitplan"
    assert unit_plan_problem(_plan(), units) == ""


def test_a_plan_with_only_assignments_is_no_changed_page():
    """A lab or project course before its first slides: its plan holds only assignments (and a folder). 3.3.0 read
    it as "IEULearn changed" and stopped reading the plan, so its units weren't stored."""
    page = _plan(with_files=False)
    units = parse_unit_plan(page, TITLE)
    assert unit_plan_problem(page, units) == "" and len(units) == 2 and sum(len(u.assignments) for u in units) == 2
    broken = _plan().replace("data-rv-payload", "data-file")             # files IEUStudia can't read: still a change
    assert unit_plan_problem(broken, parse_unit_plan(broken, TITLE)) == "1 files on the page without what IEUStudia reads them from"


# ------------------------------------------------------------------ the assignments panel: the day and time
def test_the_assignments_panel_gives_each_ones_time_and_whether_it_was_sent():
    items = {a.id: a for a in parse_assignments(PANEL, TITLE)}
    assert {k: (a.title, a.due, a.state, a.done) for k, a in items.items()} == {
        "3003": ("Lab 3", "2026-11-18T15:40", "pending", False), "3002": ("Lab 2", "2026-11-04T15:40", "submitted", True),
        "3001": ("Lab 1", "2026-10-21T15:50", "graded", True)}
    assert items["3003"].url == "https://learn.ieu.edu.tr/student#assignments/3003"
    assert assignments_problem(PANEL, list(items.values())) == ""
    # no table: the card's line gives it; IEULearn in English with AM/PM
    only_cards = _panel([_card("5", "HW 1", "pending", "Due 18 Nov 2026, 3:40 PM")])
    assert [(a.due, a.done) for a in parse_assignments(only_cards, TITLE)] == [("2026-11-18T15:40", False)]
    english = _panel([_card("6", "HW 2", "pending", "Due 18 Nov 2026, 3:40 PM")], [_row("6", "HW 2", "11/18/2026 3:40 PM", False)])
    assert parse_assignments(english, TITLE)[0].due == "2026-11-18T15:40"
    sent_back = _panel([_card("7", "HW 3", "revision", "Son teslim 18 Kas 2026, 15:40")], [_row("7", "HW 3", "18.11.2026 15:40", True)])
    assert parse_assignments(sent_back, TITLE)[0].done is False           # sent back for a revision: still to send


@pytest.mark.parametrize("due,want", [("Son teslim: 21 Eki", "2026-10-21"), ("Son teslim 04 Kas 2026, 15:40", "2026-11-04T15:40"),
                                      ("04.11.2026 15:40", "2026-11-04T15:40"), ("11/04/2026 3:40 PM", "2026-11-04T15:40"),
                                      ("Due: 05 Jan", "2027-01-05"), ("Bekliyor", "")])
def test_due_dates_in_the_ways_ieulearn_writes_them(due, want):
    assert coursesite._due(due, 2026, 1) == want


def test_an_assignments_panel_ieustudia_cant_read_is_said_as_a_change():
    unread = PANEL.replace("data-sa-id", "data-sa-key")
    assert assignments_problem(unread, parse_assignments(unread, TITLE)) == "3 assignments on the page, 0 read"
    assert assignments_problem("<div class='tasks'></div>", []) == "no assignments panel"
    assert assignments_problem(_panel([], total=0), []) == ""                   # none yet: IEULearn's empty note
    no_days = _panel([_card("8", "HW", "pending", "x")])
    assert assignments_problem(no_days, parse_assignments(no_days, TITLE)) == "1 assignments without the day they are due"


# ------------------------------------------------------------------ the library keeps them
def test_assignments_are_merged_from_the_plan_and_the_panel(tmp_path):
    lib = Library(tmp_path / "lib.db")
    plan = [a for u in parse_unit_plan(_plan(), TITLE) for a in u.assignments]
    assert sorted(lib.merge_assignments("7", plan, None)) == ["3001", "3002"]
    assert {a["asg_id"]: a["due"] for a in lib.assignments("7")} == {"3001": "2026-10-21", "3002": "2026-11-04"}
    assert lib.merge_assignments("7", plan, parse_assignments(PANEL, TITLE)) == ["3003"]
    got = {a["asg_id"]: a for a in lib.assignments("7")}
    assert {k: (a["due"], a["done"], a["state"]) for k, a in got.items()} == {
        "3001": ("2026-10-21T15:50", 1, "graded"), "3002": ("2026-11-04T15:40", 1, "submitted"),
        "3003": ("2026-11-18T15:40", 0, "pending")}
    assert got["3002"]["url"].endswith("?asg=3002#unitplan") and got["3003"]["url"].endswith("#assignments/3003")
    lib.merge_assignments("7", plan, None)                       # the plan alone again: the times read stay
    assert lib.assignments("7")[0]["due"] == "2026-10-21T15:50"
    lib.merge_assignments("7", None, parse_assignments(_panel([_card("3003", "Lab 3", "pending", "Son teslim 18 Kas 2026, 15:40")]), TITLE))
    assert [a["asg_id"] for a in lib.assignments("7")] == ["3003"]          # the panel lists them all: the others went
    assert len(lib.assignments("7", include_gone=True)) == 3
    lib.merge_assignments("7", None, [])                         # an empty panel is more likely a hiccup
    assert [a["asg_id"] for a in lib.assignments("7")] == ["3003"]


def test_an_old_library_gets_the_calendars_tables(tmp_path):
    db = sqlite3.connect(tmp_path / "old.db")
    db.execute("CREATE TABLE courses(id TEXT PRIMARY KEY, title TEXT, code TEXT, name TEXT, instructor TEXT, term TEXT, "
               "status TEXT, section TEXT, folder_id TEXT, has_panopto INTEGER DEFAULT 1, position INTEGER DEFAULT 0, "
               "active INTEGER DEFAULT 1)")
    db.commit()
    db.close()
    lib = Library(tmp_path / "old.db")
    assert lib.assignments() == [] and lib.calendar_entries() == []
    lib.save_calendar_entry("e1", "Study group", "2026-11-20", "14:00")
    lib.save_calendar_entry("e1", "Study group (room B)", "2026-11-21")
    assert [(e["title"], e["date"], e["time"]) for e in lib.calendar_entries()] == [("Study group (room B)", "2026-11-21", "")]
    assert lib.delete_calendar_entry("e1") and not lib.delete_calendar_entry("e1")


# ------------------------------------------------------------------ the sync reads the panel with the weekly plan
def _site_sync(tmp_path, monkeypatch, panel):
    from studia import syllabus
    from studia import sync as sync_mod
    from test_service_changes import ANNOUNCEMENTS, OVERVIEW, Site

    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="7", title=TITLE, code="EEE 242", name="S")])
    s = sync_mod.SyncService(lib, Settings)
    monkeypatch.setattr(syllabus, "fetch_syllabus", lambda code, session=None: None)
    pages = {("GET", "/student/courses/7"): (200, OVERVIEW), ("GET", "/student/courses/7/overview/unit-plan"): (200, _plan()),
             ("GET", "/student/courses/7/overview/announcements"): (200, ANNOUNCEMENTS)}
    if panel is not None:
        pages[("GET", "/student/courses/7/overview/assignments")] = panel
    site = Site(pages)
    monkeypatch.setattr(sync_mod, "clone_session", lambda s_: site)
    return lib, s, site


def test_a_sync_reads_each_courses_assignments_with_its_weekly_plan(tmp_path, monkeypatch):
    lib, s, site = _site_sync(tmp_path, monkeypatch, (200, PANEL))
    res = s.sync_content()
    assert res["errors"] == [] and res["changed"] == []
    assert ("GET", "/student/courses/7/overview/assignments") in site.asked
    assert {a["asg_id"]: a["due"] for a in lib.assignments("7")}["3003"] == "2026-11-18T15:40"
    assert len(lib.materials("7")) == 1 and len(lib.units("7")) == 2


def test_an_assignments_panel_that_fails_never_costs_the_courses_files_or_news(tmp_path, monkeypatch):
    lib, s, _ = _site_sync(tmp_path, monkeypatch, (500, "<h1>Server Error</h1>"))
    res = s.sync_content()
    assert res["errors"] == [] and res["changed"] == []                      # (it is read again next time)
    assert len(lib.materials("7")) == 1 and len(lib.announcements("7")) == 1
    assert {a["asg_id"]: a["due"] for a in lib.assignments("7")} == {"3001": "2026-10-21", "3002": "2026-11-04"}
    (tmp_path / "b").mkdir()
    lib2, s2, _ = _site_sync(tmp_path / "b", monkeypatch, (200, PANEL.replace("data-sa-id", "data-sa-key")))
    res = s2.sync_content()
    assert res["changed"] == ["ieulearn"] and len(lib2.materials("7")) == 1     # a changed panel: said, the rest read


# ------------------------------------------------------------------ syllabus weeks that only talk about an exam
@pytest.mark.parametrize("topic", ["Midterm", "Ara Sınav", "Final Exam", "Final sınavı", "1. Ara Sınav", "First Midterm",
                                   "Second Midterm Exam", "Mid-term Exam", "Review and Midterm Exam",
                                   "Midterm Exam and Review", "Quiz 2 and Midterm Exam", "Exam", "Genel tekrar ve ara sınav"])
def test_exam_weeks_stay_exam_weeks(topic):
    assert views.exam_topic(topic)


@pytest.mark.parametrize("topic", ["Mental Status Exam I: Behavioral Aspects", "Mental Status Exam II: Cognitive Aspects",
                                   "Review and Preparation for the Midterm", "Ara sınav öncesi konuların gözden geçirilmesi",
                                   "Midterm Review", "Final Exam Preparation", "Preparation for the final exam",
                                   "Physical Examination", "Examination of fluid flow", "Final sınavına hazırlık"])
def test_weeks_that_only_talk_about_an_exam_are_no_exam_weeks(topic):
    assert not views.exam_topic(topic)


# ------------------------------------------------------------------ the day an announcement gives
def _ann(i, title, body, posted="2026-10-20T09:00"):
    return {"id": i, "title": title, "posted_at": posted, "first_seen": "", "body": body}


def test_the_day_an_announcement_gives_for_a_quiz_or_an_exam():
    plan = lambda *anns: [(e["name"], e["week"], e["date"], e["time"]) for e in assessment_plan(list(anns))]  # noqa: E731
    table = _ann("t", "About Quizzes", "Quiz 1\n5. hafta\nQuiz 2\n7. hafta\nAra Sınav\n8. hafta", "2026-10-05T11:24")
    quiz2 = _ann("q", "About Quiz 2", "2. quizimiz **6 Kasım Cuma günü** yapılacaktır.\n\nOur **2nd quiz** will be held on "
                 "**Friday, November 6**.", "2026-10-31T12:50")
    assert plan(table, quiz2) == [("Quiz 1", 5, "", ""), ("Quiz 2", 7, "2026-11-06", ""), ("Midterm", 8, "", "")]
    assert plan(_ann("m", "Midterm", "Midterm sınavımız 8 Kasım Pazar 17:30'da yapılacaktır.", "2026-11-01T10:00")) == \
        [("Midterm", None, "2026-11-08", "17:30")]
    assert plan(_ann("x", "Midterm", "The midterm exam will be held on December 2, 2026 at 6:00 PM in rooms M01-M04.")) == \
        [("Midterm", None, "2026-12-02", "18:00")]
    assert plan(_ann("f", "Final", "Final exam: 12.01.2027, 09:00", "2026-12-20T10:00")) == [("Final", None, "2027-01-12", "09:00")]
    # not the day it is held: papers shown, results, a holiday that moved it, what it covers
    assert plan(_ann("r", "Reviewing Q1 Papers", "You may review your quiz papers on Tuesday, 10 November 2026, 14:00–15:30.")) == []
    assert plan(_ann("g", "Quiz 1 Grades", "You can see the Quiz 1 grades on 3 November.")) == []
    assert plan(_ann("h", "Change", "Quiz 2 has been postponed to the 10th week due to the national holiday on 29 October.")) == \
        [("Quiz 2", 10, "", "")]
    later = _ann("l", "Quiz 2 moved", "Quiz 2 will be held in week 9.", "2026-11-02T09:00")     # newer: the week wins
    assert ("Quiz 2", 9, "", "") in plan(table, quiz2, later)


# ------------------------------------------------------------------ make-up and cancelled classes
def test_make_up_and_cancelled_classes_from_the_announcements():
    anns = [_ann("r", "Class Rescheduling Announcement", "Our MATH 240 class, originally scheduled for October 28, 2026, has "
                 "been rescheduled to Saturday, October 24, 2026, at 12:00 PM in classroom M01.\n\n28 Ekim 2026 tarihinde "
                 "yapılması planlanan dersimiz, 24 Ekim 2026 Cumartesi günü saat 12.00'de M01 numaralı derslikte yapılacaktır.",
                 "2026-10-19T15:53"),
            _ann("n", "No Lab on October 28 and 30", "Due to the Republic Day holiday, the lab sessions on October 28 will be "
                 "canceled. The lab sessions on Friday October 30 will also be canceled.", "2026-10-27T16:47"),
            _ann("w", "No Labs This Week", "There will be no labs this week.", "2026-10-13T23:42"),
            _ann("m", "Reminder – Section 2: Make-up Lab Lecture", "As the lab lecture on 29 October coincided with the "
                 "holiday, the make-up lecture will be held on 31 October.\nIt is in the same classroom at the same time "
                 "(08:30).", "2026-10-30T16:58"),
            _ann("q", "Quiz 2 moved", "Quiz 2 is cancelled and moved to 5 November.", "2026-10-30T10:00")]
    got = [(c["kind"], c["date"] or "week of " + c["week_of"], c["time"], c["lab"], c["sections"])
           for c in agenda.schedule_changes(anns)]
    assert got == [("cancelled", "week of 2026-10-13", "", True, []), ("makeup", "2026-10-24", "12:00", False, []),
                   ("cancelled", "2026-10-28", "", False, []), ("cancelled", "2026-10-28", "", True, []),
                   ("cancelled", "2026-10-29", "", True, ["2"]), ("cancelled", "2026-10-30", "", True, []),
                   ("makeup", "2026-10-31", "08:30", True, ["2"])]
    tr = [_ann("t", "Ders iptali", "Yarınki dersimiz iptal edilmiştir.", "2026-11-02T18:00"),
          _ann("u", "Telafi", "Telafi dersimiz 7 Kasım Cumartesi saat 10.00'da yapılacaktır.", "2026-11-02T18:00")]
    assert [(c["kind"], c["date"], c["time"]) for c in agenda.schedule_changes(tr)] == [("cancelled", "2026-11-03", ""),
                                                                                       ("makeup", "2026-11-07", "10:00")]
    # what is no class moved or cancelled: a quiz, a make-up exam, an online class on its own day, office hours
    assert agenda.schedule_changes([
        _ann("a", "Quiz", "Bu hafta quiz olmayacak, ders normal işlenecek."),
        _ann("b", "Make-up exam", "The make-up exam will be held on 20 November at 10:00."),
        _ann("c", "Online class", "Our class will be held on 5 November online, on Teams."),
        _ann("d", "Office hours", "Office hours are cancelled on 3 November.")]) == []


# ------------------------------------------------------------------ the Calendar page's term
def _rec(did, cid, day, hour=10, minute=20, minutes=110, kind="lecture"):
    return Recording(delivery_id=did, course_id=cid, folder_id="f", folder_path="", name="Lecture",
                     start=dt.datetime(2026, day[0], day[1], hour, minute), duration_s=minutes * 60,
                     viewer_url=f"https://v/{did}", kind=kind)


NOW = dt.datetime(2026, 11, 9, 12, 0)


def _term(tmp_path):
    """MATH 250 (Wednesdays 10:20-12:10, Fridays in two parts), its plan dated from 21 Sep; EEE 242 with IEULearn's
    assignments; PSY 430 whose syllabus only talks about exams in weeks 7 and 10."""
    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="a", title="2026-1 MATH 250 P 3", code="MATH 250", name="Linear Algebra", term="T", section="3"),
                        Course(id="b", title=TITLE, code="EEE 242", name="Digital Design", term="T", section="1"),
                        Course(id="c", title="2026-1 PSY 430 C 1", code="PSY 430", name="Clinical", term="T", section="1")])
    lib.merge_units("a", [Unit(f"u{n}", n, f"Hafta {n}", dt.date(2026, 9, 21) + dt.timedelta(weeks=n - 1),
                               dt.date(2026, 9, 27) + dt.timedelta(weeks=n - 1)) for n in range(1, 8)], first_sync=True)
    recs = []
    for w in range(7):
        wed, fri = dt.date(2026, 9, 23) + dt.timedelta(weeks=w), dt.date(2026, 9, 25) + dt.timedelta(weeks=w)
        recs.append(_rec(f"w{w}", "a", (wed.month, wed.day)))
        if fri != dt.date(2026, 10, 30):                     # no class on Friday 30 October (the announcement)
            recs += [_rec(f"f{w}a", "a", (fri.month, fri.day), minutes=50), _rec(f"f{w}b", "a", (fri.month, fri.day), 11, 20, 50)]
    lib.merge_recordings("a", recs, first_sync=True)
    lib.merge_announcements("a", [
        coursesite.Announcement("t", "About Quizzes", "Dr", dt.datetime(2026, 10, 5, 11, 24),
                                "Quiz 1\n5. hafta\nLO1–LO2\nQuiz 2\n7. hafta\nAra Sınav\n8. hafta"),
        coursesite.Announcement("q", "About Quiz 2", "Dr", dt.datetime(2026, 10, 31, 12, 50),
                                "Our 2nd quiz will be held on Friday, November 6."),
        coursesite.Announcement("n", "No class on Friday", "Dr", dt.datetime(2026, 10, 27, 9, 0),
                                "There will be no class on October 30. The make-up class will be held on Saturday, "
                                "November 7 at 12:00 in M01.")], first_sync=True)
    lib.set_course_info("a", syllabus={"url": "https://x/syl", "weeks": [{"no": 15, "subject": "Review", "preparation": ""},
                                                                          {"no": 16, "subject": "Final Exam", "preparation": ""}]})
    lib.merge_assignments("b", None, parse_assignments(PANEL, TITLE))
    lib.set_course_info("c", syllabus={"weeks": [{"no": 7, "subject": "Review and Preparation for the Midterm"},
                                                 {"no": 10, "subject": "Mental Status Exam I: Behavioral Aspects"},
                                                 {"no": 16, "subject": "Final Exam"}]})
    return lib


def test_the_calendar_holds_the_term_with_how_sure_each_date_is(tmp_path):
    lib = _term(tmp_path)
    agenda.save_entry(lib, {"title": "Study group", "date": "2026-11-12", "time": "14:00"}, NOW.date())
    cal = agenda.build(lib, Settings(), now=NOW)
    items = {i["id"]: i for i in cal["items"]}
    assert cal["today"] == "2026-11-09" and cal["term"]["start"] == "2026-09-21"
    q1, q2, mid = items["a-quiz-1"], items["a-quiz-2"], items["a-midterm-0"]
    assert (q1["sure"], q1["week"], q1["days"], q1["los"]) == ("week", {"no": 5, "start": "2026-10-19", "end": "2026-10-25"},
                                                               ["2026-10-21", "2026-10-23"], [1, 2])
    assert (q2["sure"], q2["date"], q2["week_no"]) == ("day", "2026-11-06", 7)        # the day the announcement gave
    assert (q2["source"]["type"], q2["source"]["id"]) == ("announcement", "q")
    assert (mid["sure"], mid["week"]["no"]) == ("week", 8)
    fin = items["a-syllabus-week16"]
    assert (fin["kind"], fin["sure"], fin["source"]) == ("exam", "syllabus", {"type": "syllabus", "url": "https://x/syl", "week": 16})
    assert not any(i["course_id"] == "c" and i["kind"] == "exam" and i["week"]["no"] in (7, 10) for i in cal["items"]
                   if i.get("week"))                                                  # PSY 430: only its final
    lab3 = items["b-asg-3003"]
    assert (lab3["date"], lab3["time"], lab3["sure"], lab3["done"]) == ("2026-11-18", "15:40", "exact", False)
    assert items["b-asg-3002"]["done"] and lab3["source"] == {"type": "url", "url": "https://learn.ieu.edu.tr/student#assignments/3003"}
    # class days: held (recorded), cancelled, made up, to come by the usual schedule, the holiday
    held = items["a-class-2026-11-04-lecture"]
    assert (held["sure"], held["blocks"], held["source"]["type"]) == ("recorded", [["10:20", "12:10"]], "day")
    assert items["a-class-2026-11-06-lecture"]["blocks"] == [["10:20", "12:10"]]        # two parts, one class
    assert "a-class-2026-10-30-lecture" not in items and items["a-cancel-2026-10-30-lecture"]["kind"] == "cancelled"
    assert (items["a-makeup-2026-11-07"]["time"], items["a-makeup-2026-11-07"]["sure"]) == ("12:00", "announced")
    nxt = items["a-class-2026-11-11-lecture"]
    assert (nxt["sure"], nxt["blocks"], nxt["week_no"]) == ("schedule", [["10:20", "12:10"]], 8)
    assert "a-class-2026-12-30-lecture" in items and "a-class-2027-01-06-lecture" not in items   # until the final's week
    assert items["holiday-2026-10-29"]["kind"] == "holiday"
    own = next(i for i in cal["items"] if i["kind"] == "own")
    assert (own["title"], own["date"], own["time"], own["sure"], own["exam_like"]) == ("Study group", "2026-11-12", "14:00", "yours", False)
    assert sorted(c["code"] for c in cal["courses"]) == ["EEE 242", "MATH 250", "PSY 430"]
    assert cal["reminders"] == {"exams": 2, "due_days": 1, "due_hours": 3}


def test_the_weekly_digest_lists_the_assignments_to_send(tmp_path):
    from studia.digest import digest_prompt, weekly_digest

    lib = _term(tmp_path)
    dg = weekly_digest(lib, Settings(), 0, now=NOW)
    asg = [x for x in dg["todo"] if x["kind"] == "assignment"]
    assert [(x["course"], x["text"], x["url"]) for x in asg] == [
        ("EEE 242", "Send “Lab 3” on IEULearn: due Wed 18 Nov at 15:40", "https://learn.ieu.edu.tr/student#assignments/3003")]
    kinds = [x["kind"] for x in dg["todo"]]
    assert kinds.index("assignment") < min((i for i, k in enumerate(kinds) if k in ("transcribe", "download")), default=99)
    assert "EEE 242: Send “Lab 3” on IEULearn: due Wed 18 Nov at 15:40" in digest_prompt(dg)
    assert not [x for x in weekly_digest(lib, Settings(), 2, now=NOW)["todo"] if x["kind"] == "assignment"]   # past it


def test_another_sections_make_up_class_is_left_out(tmp_path):
    lib = _term(tmp_path)
    lib.merge_announcements("a", [coursesite.Announcement("s", "Section 2: make-up class", "Dr", dt.datetime(2026, 11, 2, 9),
                                                          "The make-up class will be held on 14 November at 10:00.")],
                            first_sync=False)
    ids = {i["id"] for i in agenda.build(lib, Settings(), now=NOW)["items"]}
    assert "a-makeup-2026-11-14" not in ids                       # MATH 250 section 3
    s = Settings(course_prefs={"a": CoursePrefs(my_section="2")})
    assert "a-makeup-2026-11-14" in {i["id"] for i in agenda.build(lib, s, now=NOW)["items"]}


def test_your_own_entries_are_checked(tmp_path):
    lib = _term(tmp_path)
    today = NOW.date()
    for entry, error in [({"title": " ", "date": "2026-11-12"}, "Give the entry a title."),
                         ({"title": "x", "date": "12/11/2026"}, "Pick the day."),
                         ({"title": "x", "date": "2030-01-01"}, "Pick a day within two years of today."),
                         ({"title": "x", "date": "2026-11-12", "time": "25:00"}, "Write the time as 14:30, or leave it empty."),
                         ({"id": "nope", "title": "x", "date": "2026-11-12"}, "This entry isn't in your calendar any more.")]:
        with pytest.raises(ValueError, match=error.replace(".", r"\.")):
            agenda.save_entry(lib, entry, today)
    e = agenda.save_entry(lib, {"title": "  EEE 215   midterm ", "date": "2026-11-20", "time": "18:00", "course_id": "zz"}, today)
    assert (e["title"], e["course_id"]) == ("EEE 215 midterm", "")             # an unknown course: none
    assert agenda.exam_like(e["title"]) and agenda.exam_like("Kısa sınav") and not agenda.exam_like("Study group")
    agenda.save_entry(lib, {**e, "title": "EEE 215 midterm (M01)"}, today)
    assert [x["title"] for x in lib.calendar_entries()] == ["EEE 215 midterm (M01)"]


def test_the_reminders_timing_keeps_only_what_is_offered(tmp_path):
    lib = Library(tmp_path / "lib.db")
    assert agenda.reminder_prefs(lib) == {"exams": 2, "due_days": 1, "due_hours": 3}
    assert agenda.set_reminder_prefs(lib, {"exams": 1, "due_hours": 0}) == {"exams": 1, "due_days": 1, "due_hours": 0}
    with pytest.raises(ValueError):
        agenda.set_reminder_prefs(lib, {"due_days": 5})
    assert agenda.reminder_prefs(lib)["exams"] == 1


# ------------------------------------------------------------------ the .ics file
def test_the_calendar_file_holds_deadlines_and_your_entries_with_stable_ids(tmp_path):
    lib = _term(tmp_path)
    e = agenda.save_entry(lib, {"title": "Study group", "date": "2026-11-12", "time": "14:00"}, NOW.date())
    events = calendar_export.all_events(lib, Settings(), NOW.date())
    uids = [x["uid"] for x in events]
    assert "b-asg-3003" in uids and "b-asg-3002" not in uids and f"own-{e['id']}" in uids      # (sent: left out)
    assert "a-quiz-2" not in uids and "a-midterm-0" in uids and "a-syllabus-week16" in uids
    text = calendar_export.ics(events, dt.datetime(2026, 11, 9, 9, 0, tzinfo=dt.timezone.utc), {"exams": 3, "due_days": 1, "due_hours": 3})
    flat = text.replace("\r\n ", "")
    block = flat.split("UID:b-asg-3003@ieustudia")[1].split("END:VEVENT")[0]
    assert "DTSTART:20261118T124000Z" in block and "DTEND" not in block            # 15:40 in Turkey, an instant
    assert "TRIGGER:-P1D" in block and "TRIGGER:-PT3H" in block and "SUMMARY:EEE 242 · Lab 3 (due)" in block
    assert "URL:https://learn.ieu.edu.tr/student#assignments/3003" in block
    mid = flat.split("UID:a-midterm-0@ieustudia")[1].split("END:VEVENT")[0]
    assert "DTSTART;VALUE=DATE:20261109" in mid and "TRIGGER:-P3D" in mid and "STATUS:TENTATIVE" in mid
    quiet = calendar_export.ics(events, prefs={"exams": 0, "due_days": 0, "due_hours": 0})
    assert "VALARM" not in quiet
    assert all(len(line.encode("utf-8")) <= 75 for line in text.split("\r\n"))


# ------------------------------------------------------------------ the reminders
def _deadline(**kw):
    return {"uid": "b-asg-3003", "kind": "assignment", "course": "EEE 242", "course_id": "b", "name": "Lab 3",
            "start": "2026-11-18", "end": "2026-11-18", "date": "2026-11-18", "time": "15:40", "los": [], **kw}


def test_a_deadline_is_reminded_a_day_and_three_hours_before_each_once():
    seen = {}
    at = lambda *a: agent.reminders([_deadline()], dt.datetime(2026, 11, *a), seen)   # noqa: E731
    assert at(17, 15, 0) is None
    title, text, payload = at(17, 16, 0)
    assert (title, text, payload) == ("IEUStudia: a deadline is coming up",
                                      "EEE 242: Lab 3 is due tomorrow (Wed 18 Nov) at 15:40.", "calendar:b-asg-3003")
    assert at(17, 20, 0) is None and at(18, 12, 0) is None                    # said; the second one comes at 12:40
    assert at(18, 13, 0)[1] == "EEE 242: Lab 3 is due today at 15:40."
    assert at(18, 14, 0) is None and at(18, 16, 0) is None                    # said; then it is past
    late = {}                                                                 # the PC was off until 13:00 that day:
    assert agent.reminders([_deadline()], dt.datetime(2026, 11, 18, 13, 0), late)[1] == "EEE 242: Lab 3 is due today at 15:40."
    assert agent.reminders([_deadline()], dt.datetime(2026, 11, 18, 13, 30), late) is None     # one notification, not two
    assert agent.reminders([_deadline()], dt.datetime(2026, 11, 17, 16, 0), {}, {"due_days": 0, "due_hours": 0}) is None
    moved = _deadline(start="2026-11-20", date="2026-11-20")                 # a new deadline: said again
    assert agent.reminders([moved], dt.datetime(2026, 11, 19, 16, 0), seen)[1] == "EEE 242: Lab 3 is due tomorrow (Fri 20 Nov) at 15:40."


def test_a_deadline_without_its_time_is_reminded_the_evening_before_and_that_morning():
    seen, e = {}, _deadline(time="")
    assert agent.reminders([e], dt.datetime(2026, 11, 17, 16, 0), seen) is None
    assert agent.reminders([e], dt.datetime(2026, 11, 17, 18, 0), seen)[1] == "EEE 242: Lab 3 is due tomorrow (Wed 18 Nov). IEULearn has the time."
    assert agent.reminders([e], dt.datetime(2026, 11, 18, 7, 0), seen) is None
    assert agent.reminders([e], dt.datetime(2026, 11, 18, 8, 30), seen)[1] == "EEE 242: Lab 3 is due today. IEULearn has the time."


def test_quizzes_and_exams_come_two_days_before_and_your_entries_too():
    quiz = {"uid": "b-quiz-2", "course": "MATH 240", "name": "Quiz 2", "course_id": "b", "kind": "quiz", "classes": ["2026-10-13", "2026-10-15"],
            "ends": ["10:20", "12:50"], "los": [3, 4], "start": "2026-10-13", "end": "2026-10-15", "week": 5}
    seen = {}
    assert agent.reminders([quiz], dt.datetime(2026, 10, 11, 16, 0), seen) is None
    assert agent.reminders([quiz], dt.datetime(2026, 10, 11, 18, 0), seen)[1] == \
        "MATH 240: Quiz 2 may be on Tue 13 Oct or Thu 15 Oct, as announced. Covers LO3–LO4."
    assert agent.reminders([quiz], dt.datetime(2026, 10, 12, 18, 0), seen) is None              # once
    day = {"uid": "a-quiz-2", "course": "MATH 250", "name": "Quiz 2", "course_id": "a", "kind": "quiz", "classes": [], "ends": [],
           "los": [], "start": "2026-11-06", "end": "2026-11-06", "date": "2026-11-06", "time": "", "week": 7}
    title, text, payload = agent.reminders([day], dt.datetime(2026, 11, 4, 18, 0), seen)
    assert (text, payload) == ("MATH 250: Quiz 2 is on Fri 6 Nov, as announced.", "ready:a:Quiz 2")
    told_week = {"rem": ["a-quiz-2@2026-11-03"]}                  # told for its week already: not again for the day
    assert agent.reminders([day], dt.datetime(2026, 11, 4, 18, 0), told_week) is None
    mid = {**day, "uid": "a-midterm-0", "name": "Midterm", "kind": "midterm", "date": "2026-11-08", "time": "17:30", "week": 7}
    assert agent.reminders([mid], dt.datetime(2026, 11, 6, 19, 0), {})[1] == "MATH 250: Midterm is on Sun 8 Nov at 17:30, as announced."
    own = {"uid": "own-1", "kind": "own", "course": "", "course_id": "", "name": "Study group", "start": "2026-11-20",
           "end": "2026-11-20", "date": "2026-11-20", "time": "14:00", "los": []}
    title, text, payload = agent.reminders([own], dt.datetime(2026, 11, 19, 15, 0), {})
    assert (title, text, payload) == ("IEUStudia: from your calendar", "Study group: tomorrow (Fri 20 Nov) at 14:00.", "calendar:own-1")
    exam = {**own, "uid": "own-2", "name": "EEE 215 midterm", "time": "18:00", "exam_like": True}
    assert agent.reminders([exam], dt.datetime(2026, 11, 18, 12, 0), {}) is None             # like an exam: 2 days, from 17:00
    assert agent.reminders([exam], dt.datetime(2026, 11, 18, 18, 0), {})[1] == "EEE 215 midterm: on Fri 20 Nov at 18:00."
    assert agent.reminders([quiz], dt.datetime(2026, 10, 11, 18, 0), {}, {"exams": 0}) is None


def test_the_open_app_reminds_as_set_and_stays_quiet_when_reminders_are_off(tmp_path, isolated_home, monkeypatch):
    from studia import appconfig
    from studia.api import Api
    from studia.services import Services

    api = Api(Services())
    lib = api._s.lib
    lib.upsert_courses([Course(id="b", title=TITLE, code="EEE 242", name="S")])
    lib.merge_assignments("b", None, parse_assignments(PANEL, TITLE))
    told = []
    monkeypatch.setattr(api, "_notify", lambda *a: told.append(a))
    monkeypatch.setattr("studia.api.is_foreground", lambda hwnd: False)

    class Clock(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 17, 16, 0)

    monkeypatch.setattr("studia.api.dt.datetime", Clock)
    monkeypatch.setattr("studia.calendar_export.dt.date", type("D", (dt.date,), {"today": classmethod(lambda c: dt.date(2026, 11, 17))}))
    appconfig.update({"notify_reminders": False})
    api._remind()
    assert told == []
    appconfig.update({"notify_reminders": True})
    api._remind()
    api._remind()
    assert told == [("IEUStudia: a deadline is coming up", "EEE 242: Lab 3 is due tomorrow (Wed 18 Nov) at 15:40.",
                     "calendar:b-asg-3003")]


def test_the_calendar_api(tmp_path, isolated_home):
    from studia.api import Api
    from studia.services import Services

    api = Api(Services())
    r = api.calendar()
    assert r["ok"] and r["reminders"]["exams"] == 2 and r["items"] == [] or all(i["kind"] == "holiday" for i in r["items"])
    bad = api.calendar_save({"title": "", "date": dt.date.today().isoformat()})
    assert bad == {"ok": False, "error": "Give the entry a title."}
    day = (dt.date.today() + dt.timedelta(days=3)).isoformat()
    r = api.calendar_save({"title": "Study group", "date": day, "time": "14:00"})
    assert r["ok"] and r["entry"]["title"] == "Study group"
    assert [i["title"] for i in r["items"] if i["kind"] == "own"] == ["Study group"]
    r = api.calendar_delete(r["entry"]["id"])
    assert r["ok"] and not [i for i in r["items"] if i["kind"] == "own"]
    assert api.calendar_reminders({"exams": 9}) == {"ok": False, "error": "That reminder time isn't one IEUStudia offers."}
    assert api.calendar_reminders({"exams": 7})["reminders"]["exams"] == 7 and api.calendar_reminders()["reminders"]["exams"] == 7
