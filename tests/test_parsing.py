import datetime as dt

import pytest

from studia.domain import Course, Recording
from studia.ieulearn import extract_folder_id, parse_dashboard, split_course_title
from studia.panopto import parse_dotnet_date, parse_sessions
from studia.sections import available_sections, classify, guess_my_section, parse_section_tag


@pytest.mark.parametrize("title,expected", [
    ("2026-1 EEE 215 Blgsyr. Müh. için Elktrk. Devre &amp; Elektroniğe Giriş 0 - 1",
     ("EEE 215", "Blgsyr. Müh. için Elktrk. Devre & Elektroniğe Giriş", "0 - 1")),
    ("2026-1 EEE 242 Sayısal Tasarım 1", ("EEE 242", "Sayısal Tasarım", "1")),
    ("2026-1 MATH 240 Mühendisler için Olasılık 1", ("MATH 240", "Mühendisler için Olasılık", "1")),
    ("2026-1 MATH 250 Mühendisler İçin Doğrusal Cebir 3", ("MATH 250", "Mühendisler İçin Doğrusal Cebir", "3")),
    ("2026-1 FENG 495 Çift Ana Dal Bitirme Projesi I (Mühendislik Fakültesi) 3",
     ("FENG 495", "Çift Ana Dal Bitirme Projesi I (Mühendislik Fakültesi)", "3")),
])
def test_split_course_title(title, expected):
    assert split_course_title(title) == expected


DASHBOARD = """
<strong class="ov-user-name">Test Student</strong><span class="ov-user-role">20230000000</span>
<article class="dash-course" data-course-id="111" data-course-status="ongoing"
  data-course-name="x" data-course-title="2026-1 EEE 242 Sayısal Tasarım 1" data-course-href="h">
  <span class="dash-course-school" title="Jane Doe">Jane Doe</span>
  <span class="dash-course-class" title="T">2026-2027 Fall Undergrad</span></article>
<article class="dash-course" data-course-id="222" data-course-status="past"
  data-course-title="2025-2 MATH 153 Calculus &amp; Analysis 2" data-course-href="h">
  <span class="dash-course-school" title="John Roe">John Roe</span></article>
"""


def test_parse_dashboard():
    profile, courses = parse_dashboard(DASHBOARD)
    assert profile.name == "Test Student" and profile.student_number == "20230000000"
    assert [c.id for c in courses] == ["111", "222"]
    assert courses[0].code == "EEE 242" and courses[0].instructor == "Jane Doe"
    assert courses[0].term == "2026-2027 Fall Undergrad" and courses[0].section == "1"
    assert courses[1].status == "past" and courses[1].name == "Calculus & Analysis"


# the "My courses" list the dashboard loads (/student/courses/overview-fragment), cut down: no status, no term
COURSE_LIST = """
<div id="coursesRoot"><div class="courses-tabs">
  <button class="courses-tab active" data-tab="active">Dönem Dersleri <span class="tab-count">1</span></button>
  <button class="courses-tab" data-tab="past">Geçmiş Dersler <span class="tab-count">1</span></button></div>
<div class="courses-tab-content" id="tab-active">
<article class="dash-course" data-course-id="111" data-name="x"
  data-course-title="2026-1 EEE 242 Sayısal Tasarım 1" data-course-href="h" data-cover-image="u">
  <a href="h" class="dash-course-cover" style="background-image:url('https://learn.ieu.edu.tr/c-1.jpg?v=1')"></a>
  <span class="dash-course-school">Jane Doe</span><span class="dash-course-meta">Elektrik</span></article></div>
<div class="courses-tab-content" id="tab-past" style="display: none;">
<article class="dash-course" data-course-id="222" data-course-title="2025-2 MATH 153 Calculus &amp; Analysis 2">
  <span class="dash-course-school">John Roe</span></article></div></div>
"""


def test_the_course_list_has_the_dashboards_courses():
    from studia.ieulearn import parse_course_list

    _, dash = parse_dashboard(DASHBOARD)
    listed = parse_course_list(COURSE_LIST)
    keys = ("id", "title", "code", "name", "section", "instructor", "status", "position")
    assert [[getattr(c, k) for k in keys] for c in listed] == [[getattr(c, k) for k in keys] for c in dash]
    assert listed[0].cover_url == "https://learn.ieu.edu.tr/c-1.jpg?v=1" and listed[0].term == ""


def test_a_course_list_that_isnt_whole_is_not_used():
    from studia.ieulearn import parse_course_list

    assert parse_course_list(COURSE_LIST.replace('class="tab-count">1<', 'class="tab-count">3<', 1)) is None
    assert parse_course_list(DASHBOARD) is None                              # not the list
    assert parse_course_list('<div id="coursesRoot"><span class="tab-count">0</span></div>') is None
    assert parse_course_list("<html><form id='loginForm'></form></html>") is None
    assert parse_course_list(COURSE_LIST.replace('class="tab-count"', 'class="badge"')) is None   # no counts: changed


def test_extract_folder_id_plain_and_encoded():
    fid = "d893262b-1027-4262-a147-b4cb007bffcc"
    assert extract_folder_id([f"https://x/EmbeddedList.aspx?embedded=1&folderID={fid}&isLTIEmbed=true"]) == fid
    assert extract_folder_id([f"https://x/List.aspx?embedded=1#folderID=%22{fid}%22"]) == fid
    assert extract_folder_id(["https://x/other"]) is None


def test_dotnet_date_is_local_wall_clock():
    # Panopto shows "Tuesday, October 20, 2026 at 11:15 AM" for this value
    assert parse_dotnet_date("/Date(1792494951083)/") == dt.datetime(2026, 10, 20, 11, 15, 51, 83000)


def test_parse_sessions():
    d = {"Results": [{"DeliveryID": "ABC-1", "SessionID": "S1", "SessionName": "EEE 215 Section 1 Week 4 Lab",
                      "StartTime": "/Date(1792494951083)/", "Duration": 2400.5, "FolderID": "F",
                      "ViewerUrl": "https://v"}], "TotalNumber": 1}
    (r,) = parse_sessions(d, "19226", "Labs")
    assert r.delivery_id == "abc-1" and r.kind == "lab" and r.section_tag == "1" and r.duration_s == 2400.5


@pytest.mark.parametrize("name,tag", [
    ("Tuesday, October 13, 2026 at 12:10 PM EEE 215 Section 1", "1"),
    ("Wednesday, October 14, 2026 at 11:17:26 AM EEE 215 Sections 2-3", "2-3"),
    ("Wednesday, September 23, 2026 at 11:25:40 AM EEE Section 2*3", "2-3"),
    ("EEE215-2 Lab Week-4 15 Ekim 2026 Perşembe at 08:28:00", "2"),
    ("EEE 215 SECTION 3 WEEK 4 LAB 16 Ekim 2026 Cuma at 08:29:45", "3"),
    ("Tuesday, October 6, 2026 at 1:04:34 PM", ""),
    ("Lecture4.1-Friday, October 16, 2026 at 2:05:56 PM", ""),
    ("EEE215-2 22 Ekim 2026 Perşembe 08:29:22", "2"),
    ("Section-2 Labs 31 Ekim 2026 Cumartesi 08:29:01", "2"),
    ("27 Ekim 2026 Salı at 08:16:48 EEE 215 Section 1 Online", "1"),
    ("Sec. #3", "3"), ("Section: 2", "2"), ("Şube-1", "1"), ("Sections 10-11-12", "10-11-12"),
    # a date or a textbook number is not a section
    ("MATH 250 - 6 Kasım 2026 Cuma at 12:09:43", ""),
    ("MATH 250 - 6 KASIM 2026 Cuma", ""),
    ("EEE 215 - 23 Ekim 2026", ""),
    ("MATH 250 - 4 November 2026 Wednesday at 08:28:43", ""),
    ("MATH250-23 Ekim 2026", ""),
    ("MATH 250 - 23.10.2026", ""), ("MATH 250 - 23/10/2026", ""), ("MATH 250 - 2026-10-23", ""),
    ("MATH 250 - 08:29:22", ""),
    ("PSY 307 - 3. Hafta", ""),
    ("MATH 250 Section 2.1 Matrices", ""), ("Sec. 4.2 Eigenvalues", ""), ("Section 123", ""),
    ("EEE 215 Section 1 - 3 Kasım 2026 Salı", "1"),
    ("EEE 215 Section 2, 15 Ekim 2026", "2"),
    ("Section 1 - 24 Ekim 2026 Cumartesi 09:41:50", "1"),
    ("Section 1 - 2026-10-24", "1"),
    ("EEE 215 Sections 2-3 - 14 Ekim 2026", "2-3"),
    ("EEE215-2 - 22 Ekim 2026", "2"),
])
def test_parse_section_tag(name, tag):
    assert parse_section_tag(name) == tag


def test_lectures_named_by_their_date_are_not_another_sections():
    """A lecturer naming each lecture "MATH 250 - <day> <month> <year>": no day becomes a section, so with the
    section guessed from the IEULearn title ("… 3") the lectures are not all another section's."""
    course = Course(id="c", title="2026-1 MATH 250 Lineer Cebir 3", code="MATH 250", name="", section="3")
    recs = [_rec(f"d{day}", day, f"MATH 250 - {day} Ekim 2026 Cuma at 12:09:43") for day in (2, 9, 16, 23, 30)]
    assert [r.section_tag for r in recs] == [""] * 5 and available_sections(recs) == []
    assert classify(course, recs) == {r.delivery_id: None for r in recs}
    recs.append(_rec("s1", 13, "EEE 215 Section 1 - 13 Ekim 2026 Salı"))
    assert available_sections(recs) == ["1"] and classify(course, recs)["s1"] is False


def _rec(did, day, name, kind="lecture"):
    return Recording(delivery_id=did, course_id="c", folder_id="f", folder_path="", name=name,
                     start=dt.datetime(2026, 10, day, 11, 0), duration_s=2400, viewer_url="", kind=kind,
                     section_tag=parse_section_tag(name))


def test_classify_with_weekday_inference():
    course = Course(id="c", title="", code="EEE 215", name="", section="0 - 1")
    recs = [_rec("a", 13, "x Section 1"),          # Tuesday, mine
            _rec("b", 13, "Tuesday 1:04 PM"),       # Tuesday, untagged -> mine by weekday
            _rec("c", 14, "x Sections 2-3"),        # Wednesday, other
            _rec("d", 14, "Wednesday 1:05 PM"),     # Wednesday, untagged -> other
            _rec("e", 16, "Friday something")]      # Friday, no evidence -> unknown
    assert guess_my_section(course) is None                   # a merged course page: the app asks
    assert classify(course, recs) == {r.delivery_id: None for r in recs}
    res = classify(course, recs, my_section="1")
    assert res == {"a": True, "b": True, "c": False, "d": False, "e": None}
    assert available_sections(recs) == ["1", "2", "3"]
    assert classify(course, recs, my_section="2")["c"] is True


@pytest.mark.parametrize("suffix,guess", [("3", "3"), ("1", "1"), ("0 - 1", None), ("2-3", None), ("", None), ("0", None)])
def test_guess_my_section_only_from_a_single_section(suffix, guess):
    assert guess_my_section(Course(id="c", title="", code="X 100", name="", section=suffix)) == guess
