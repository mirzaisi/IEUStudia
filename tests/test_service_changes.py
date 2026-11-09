"""When a service IEUStudia reads changed on its side (IEULearn's pages, Panopto's answers), as opposed to being down:
it is noticed, said in plain words (which service, what doesn't work, that the data is safe, what to do), kept in one
place (health), and what IEUStudia had stays: no "0 new recordings", no courses or files vanishing, nothing marked gone
because a parse came back empty. The pages here are cut-down copies of the real ones, edited to simulate changes."""

import datetime as dt
import json
import types

import pytest
import requests

from studia import health, secrets_store, sync as sync_mod
from studia.config import Settings
from studia.coursesite import (CourseSite, announcements_problem, overview_problem, parse_announcements,
                               parse_overview, parse_unit_plan, unit_plan_problem)
from studia.domain import Course, Profile, Recording
from studia.health import ServiceChanged
from studia.ieulearn import BASE, Busy, IEULearn, PageChanged, SiteChanged
from studia.library import Library
from studia.panopto import Listing, NeedPanoptoAuth, Panopto, parse_sessions

# ------------------------------------------------------------------ IEULearn's pages, as they are (cut down)
CARD = ('<article class="dash-course" data-course-id="{id}" data-course-status="ongoing" data-course-title="{title}">'
        '<span class="dash-course-school" title="Jane Doe">Jane Doe</span>'
        '<span class="dash-course-class">2026-2027 Fall</span></article>')
DASHBOARD = ('<strong class="ov-user-name">Test Student</strong><span class="ov-user-role">20230000000</span>'
             '<div class="dash-course-track" id="dashCourseGrid">{cards}</div>')
TWO = DASHBOARD.format(cards=CARD.format(id="111", title="2026-1 EEE 242 Sayısal Tasarım 1")
                       + CARD.format(id="222", title="2026-1 MATH 240 Olasılık 1"))

UNITS = """<div class="ovup-syl ovup-syl--student">
<article class="curriculum-item" data-item-id="501"><div class="ovup-title-text" title="Week 1 - Intro"></div>
  <div class="item-subtitle">21 Eyl - 27 Eyl</div>
  <div class="ovup-syl-item" data-rv-payload="{&quot;id&quot;: 9001, &quot;title&quot;: &quot;Slides 1&quot;, &quot;file_name&quot;: &quot;w1.pdf&quot;, &quot;type&quot;: &quot;pdf&quot;, &quot;file_ext&quot;: &quot;pdf&quot;, &quot;url&quot;: &quot;/f/9001&quot;}"><span class="ovup-syl-item-name">Slides 1</span></div>
  <div class="ovup-syl-item" data-rv-payload="{&quot;id&quot;: 9002, &quot;title&quot;: &quot;Notes 1&quot;, &quot;file_name&quot;: &quot;n1.pdf&quot;, &quot;type&quot;: &quot;pdf&quot;, &quot;file_ext&quot;: &quot;pdf&quot;, &quot;url&quot;: &quot;/f/9002&quot;}"><span class="ovup-syl-item-name">Notes 1</span></div>
</article>
<article class="curriculum-item" data-item-id="502"><div class="ovup-title-text" title="Week 2 - More"></div>
  <div class="item-subtitle">28 Eyl - 4 Eki</div>
  <div class="ovup-syl-item" data-rv-payload="{&quot;id&quot;: 9003, &quot;title&quot;: &quot;Slides 2&quot;, &quot;file_name&quot;: &quot;w2.pdf&quot;, &quot;type&quot;: &quot;pdf&quot;, &quot;file_ext&quot;: &quot;pdf&quot;, &quot;url&quot;: &quot;/f/9003&quot;}"><span class="ovup-syl-item-name">Slides 2</span></div>
</article></div>"""
EMPTY_UNITS = ('<div class="ovup-syl ovup-syl--student"><div class="ovup-syl-empty"><p>Bu ders planında henüz '
               'görüntülenecek içerik yok.</p></div></div>')
ANNOUNCEMENTS = """<div class="ov-ann st-ov-ann" data-course="111"><header class="ov-ann-toolbar">
<span class="ov-ann-count">1</span></header>
<article class="ov-ann-card"><h4 class="ov-ann-card-title">Quiz 1 next week</h4>
<div class="ov-ann-card-meta"><span>Jane Doe</span><span>19.10.2026 15:53</span></div>
<div class="st-ov-ann-full"><p>Quiz 1 covers weeks 1-3.</p></div></article></div>"""
EMPTY_ANNOUNCEMENTS = ('<div class="ov-ann st-ov-ann"><span class="ov-ann-count">0</span><div class="ov-ann-empty">'
                       '<p>Bu derste henüz duyuru yayınlanmadı.</p></div></div>')
OVERVIEW = """<div class="ov-intro"><p class="ov-intro-paragraph">About the course.</p>
<ol class="ov-intro-lo-list"><li><span class="ov-intro-lo-no">1.</span> <span class="ov-intro-lo-text">Design circuits</span></li></ol></div>
<div class="ov-ects"><span class="ov-ects-week-no">1</span> <span class="ov-ects-week-topic">Intro</span>
<li class="ov-ects-assess-item"><span class="ov-ects-assess-name">Midterm</span><span class="ov-ects-assess-count">1</span><span class="ov-ects-assess-pct">40 %</span></li></div>"""


class Site:
    """IEULearn by path: (method, path) -> (status, body[, where it ended])."""

    def __init__(self, pages):
        self.pages, self.asked = pages, []
        self.cookies = requests.cookies.RequestsCookieJar()

    def _go(self, method, url, **kw):
        path = url.replace(BASE, "").split("?")[0]
        self.asked.append((method, path))
        status, body, final = (self.pages[(method, path)] + (path,))[:3]

        def raise_for_status():
            if status >= 400:
                raise requests.HTTPError(f"{status}", response=types.SimpleNamespace(status_code=status))

        return types.SimpleNamespace(status_code=status, text=body, ok=status < 400, headers={},
                                     url=final if final.startswith("http") else BASE + final,
                                     raise_for_status=raise_for_status, history=[])

    def get(self, url, **kw):
        return self._go("GET", url, **kw)

    def post(self, url, **kw):
        return self._go("POST", url, **kw)


def changed_parts():
    return {f"{s['id']}.{p['part']}" for s in health.snapshot()["services"] for p in s["parts"] if p["state"] == "changed"}


# ------------------------------------------------------------------ health: one place for every service
def test_a_change_is_kept_said_in_plain_words_and_over_once_it_reads_again():
    assert health.snapshot()["changed"] == []
    exc = health.changed("panopto", "sessions", "GetSessions answered without d.Results (student 20230000000)")
    assert isinstance(exc, ServiceChanged) and isinstance(exc, RuntimeError)
    text = str(exc)
    for words in ("Panopto changed something on its side.", "can't look for new recordings on Panopto right now",
                  "Everything on this PC is safe", "Check for an IEUStudia update in Settings → About",
                  "Copy details"):
        assert words in text
    snap = health.snapshot()
    assert snap["changed"] == ["panopto"]
    panopto = next(s for s in snap["services"] if s["id"] == "panopto")
    assert panopto["state"] == "changed" and panopto["message"] == text and panopto["since"]
    assert [p["part"] for p in panopto["parts"]] == ["sessions"]
    health.down("panopto", "sessions", "HTTP 503")          # down meanwhile: the change stays said
    assert health.is_changed("panopto", "sessions")
    health.ok("panopto", "sessions")                         # reads again (fixed by an update, or Panopto went back)
    assert not health.is_changed("panopto") and health.snapshot()["changed"] == []
    assert json.loads(health._path().read_text(encoding="utf-8"))["parts"]["panopto.sessions"]["state"] == "ok"


def test_a_change_an_older_version_saw_is_looked_at_anew_after_an_update(monkeypatch):
    health.changed("ieulearn", "unit_plan", "no unit plan on the page")
    monkeypatch.setattr(health, "__version__", "99.0.0")
    snap = health.snapshot()
    assert snap["changed"] == [] and next(s for s in snap["services"] if s["id"] == "ieulearn")["state"] == "unknown"
    assert not health.is_changed("ieulearn")


def test_the_details_for_the_developer_have_nothing_personal_in_them():
    health.changed("openai", "answers", "bearer abc.def.ghi from ali.veli@std.ieu.edu.tr, course 17855, "
                                        "folder 27271c8a-ff43-4e41-b447-b4e900918231, https://x.example/p?token=secret")
    health.ok("ieulearn", "courses")
    text = health.details()
    assert "ChatGPT: changed" in text and "IEULearn: ok" in text and "Panopto: not used yet" in text
    for secret in ("ali.veli", "17855", "27271c8a", "token=secret", "abc.def.ghi"):
        assert secret not in text


def test_the_helper_tells_about_each_change_once():
    assert health.news() == []
    health.changed("ieulearn", "courses", "x")
    health.changed("ieulearn", "unit_plan", "y")
    assert health.news() == ["IEULearn"]
    assert health.news() == []                               # told
    health.ok("ieulearn", "courses")
    health.ok("ieulearn", "unit_plan")
    health.changed("ieulearn", "courses", "again")           # a new change: told again
    assert health.news() == ["IEULearn"]


def test_the_tray_helper_tells_about_a_change_once_while_the_window_is_closed():
    from studia import agent

    assert agent.changed_note() is None
    health.changed("panopto", "sessions", "no d.Results")
    title, text, target = agent.changed_note()
    assert title == "Panopto changed something on its side" and "Everything on this PC is safe." in text
    assert target == "home" and agent.changed_note() is None


def test_the_page_gets_every_services_state_and_copies_the_details_without_anything_personal(monkeypatch):
    import queue

    from studia import api as api_mod

    copied = []
    monkeypatch.setattr(api_mod, "set_clipboard", lambda text: copied.append(text) or True)
    health.changed("ieulearn", "courses", "no course card (student 20231234567, ali.veli@std.ieu.edu.tr)")
    a = api_mod.Api.__new__(api_mod.Api)
    a._events = queue.Queue()
    st = a.services_state()
    assert st["ok"] and st["changed"] == ["ieulearn"] and len(st["services"]) == len(health.SERVICES)
    ieu = st["services"][0]
    assert ieu["title"] == "IEULearn changed something on its side." and ieu["key"].startswith("ieulearn@")
    assert a.services_copy()["ok"] and "IEULearn: changed" in copied[0]
    assert "20231234567" not in copied[0] and "ali.veli" not in copied[0]
    a._services_changed()                                    # what a sync sends the page
    assert a._events.get_nowait()["changed"] == ["ieulearn"]

    @api_mod.safe
    def ask(self):
        raise health.changed("openai", "answers", "a stream of events IEUStudia doesn't know")

    got = ask(a)
    assert got["ok"] is False and got["changed"] == "openai" and "ChatGPT changed something" in got["error"]


def test_two_processes_keep_each_others_states():
    health.changed("youtube", "videos", "unable to extract")
    data = json.loads(health._path().read_text(encoding="utf-8"))   # the tray helper notes another one meanwhile
    data["parts"]["github.releases"] = {"state": "ok", "since": "2026-11-08T10:00:00", "seen": "2026-11-08T10:00:00",
                                        "count": 1, "detail": "", "version": health.__version__}
    health._path().write_text(json.dumps(data), encoding="utf-8")
    health._cache["mtime"] = None                            # (a file written within the same second)
    health.ok("panopto", "lti")
    parts = json.loads(health._path().read_text(encoding="utf-8"))["parts"]
    assert {"youtube.videos", "github.releases", "panopto.lti"} <= set(parts)


# ------------------------------------------------------------------ IEULearn's courses page
def test_the_courses_page_as_it_is_reads_fine():
    profile, courses = IEULearn(Site({("GET", "/student"): (200, TWO)})).dashboard()
    assert [c.code for c in courses] == ["EEE 242", "MATH 240"] and profile.name == "Test Student"
    assert not health.is_changed("ieulearn")


@pytest.mark.parametrize("page,why", [
    (TWO.replace('data-course-id="222"', 'data-id="222"'), "without their number"),     # a card's attribute renamed
    (TWO.replace("<article", "<div").replace("</article>", "</div>"), "no course card"),  # the cards' markup changed
    (TWO.replace("data-course-title", "data-title"), "without their titles"),
    ('<strong class="ov-user-name">Test Student</strong><div id="grid2"></div>', "no course grid"),
])
def test_a_courses_page_whose_cards_changed_is_never_taken_for_no_courses(page, why):
    with pytest.raises(PageChanged) as e:
        IEULearn(Site({("GET", "/student"): (200, page)})).dashboard()
    assert isinstance(e.value, SiteChanged) and "IEULearn changed something on its side." in str(e.value)
    assert "ieulearn.courses" in changed_parts() and why in health.details()


def test_an_empty_course_grid_is_no_courses_not_a_change():
    profile, courses = IEULearn(Site({("GET", "/student"): (200, DASHBOARD.format(cards=""))})).dashboard()
    assert courses == [] and not health.is_changed("ieulearn")


# ------------------------------------------------------------------ IEULearn's sign-in page
FORM = ('<form id="loginForm" method="POST" action="https://learn.ieu.edu.tr/login"><input type="hidden" '
        'name="csrf_token" value="tok"><input type="text" name="email"><input type="password" name="password">'
        '</form>')


def test_a_sign_in_form_that_moved_is_said_and_noted_but_keeps_the_password():
    with pytest.raises(Busy, match="as IEUStudia knows it.*not rejected"):
        IEULearn(Site({("GET", "/login"): (200, "<h1>Yeni giriş</h1><p>EkoID ile gir</p>")})).login("ali", "pw")
    assert health.is_changed("ieulearn", "signin")
    health.ok("ieulearn", "signin")
    with pytest.raises(Busy, match="right now \\(it may be busy or under maintenance\\)"):
        IEULearn(Site({("GET", "/login"): (200, "<h1>Sistem bakımda</h1>")})).login("ali", "pw")
    assert not health.is_changed("ieulearn", "signin")       # maintenance: down for a while, not changed


def test_a_sign_in_through_a_form_ieustudia_doesnt_know_that_doesnt_take_is_a_change():
    unknown = FORM.replace('id="loginForm" ', "").replace('name="password"', 'name="pass"')
    pages = {("GET", "/login"): (200, unknown), ("POST", "/login"): (200, unknown, "/login"),
             ("GET", "/student"): (302, "", "/student")}
    site = Site(pages)
    original = site._go

    def go(method, url, **kw):                               # /student sends a session that didn't sign in to /login
        r = original(method, url, **kw)
        if (method, url.replace(BASE, "")) == ("GET", "/student"):
            r.headers = {"Location": f"{BASE}/login"}
        return r

    site._go = go
    with pytest.raises(Busy, match="as IEUStudia knows it"):
        IEULearn(site).login("ali", "pw")
    assert health.is_changed("ieulearn", "signin")


def test_signing_in_as_it_is_reads_fine():
    site = Site({("GET", "/login"): (200, FORM), ("POST", "/login"): (200, "<p>ok</p>", "/student")})
    IEULearn(site).login("ali", "pw")
    assert next(s for s in health.snapshot()["services"] if s["id"] == "ieulearn")["state"] == "ok"


# ------------------------------------------------------------------ a course's pages
def test_a_courses_pages_as_they_are_read_fine():
    units = parse_unit_plan(UNITS, "2026-1 EEE 242 x 1")
    assert [len(u.resources) for u in units] == [2, 1] and unit_plan_problem(UNITS, units) == ""
    assert unit_plan_problem(EMPTY_UNITS, parse_unit_plan(EMPTY_UNITS)) == ""
    assert announcements_problem(ANNOUNCEMENTS, parse_announcements(ANNOUNCEMENTS)) == ""
    assert announcements_problem(EMPTY_ANNOUNCEMENTS, parse_announcements(EMPTY_ANNOUNCEMENTS)) == ""
    ov = parse_overview(OVERVIEW)
    assert (len(ov.outcomes), len(ov.weeks), len(ov.grading)) == (1, 1, 1) and overview_problem(OVERVIEW, ov) == ""


@pytest.mark.parametrize("part,page,why", [
    ("unit_plan", UNITS.replace("data-rv-payload", "data-file"), "files on the page without"),
    ("unit_plan", UNITS.replace("&quot;id&quot;", "&quot;file_id&quot;"), "3 files on the page, 0 read"),
    ("unit_plan", UNITS.replace("data-item-id", "data-unit"), "units on the page, 0 read"),
    ("unit_plan", "<div class='plan-v2'>Week 1</div>", "no unit plan on the page"),
    ("unit_plan", '<div class="ovup-syl"></div>', "without its empty note"),
    ("unit_plan", UNITS.replace("ovup-title-text", "unit-name"), "units without their titles"),
    ("announcements", ANNOUNCEMENTS.replace("<article", "<section").replace("</article>", "</section>"),
     "none read"),
    ("announcements", "<div class='news'>Quiz</div>", "no announcements panel"),
    ("course_page", OVERVIEW.replace("ov-intro-lo-text", "lo-text"), "learning outcomes on the page, none read"),
    ("course_page", "<div class='course-v2'>About</div>", "no outcomes, weekly topics or grading parts"),
])
def test_a_course_page_that_changed_is_said_for_the_part_that_changed(part, page, why):
    site = CourseSite(Site({("GET", "/student/courses/7"): (200, page),
                            ("GET", "/student/courses/7/overview/unit-plan"): (200, page),
                            ("GET", "/student/courses/7/overview/announcements"): (200, page)}))
    read = {"unit_plan": lambda: site.unit_plan("7"), "announcements": lambda: site.announcements("7"),
            "course_page": lambda: site.overview("7")}[part]
    with pytest.raises(PageChanged):
        read()
    assert changed_parts() == {f"ieulearn.{part}"} and why in health.details()


def test_a_course_page_that_is_the_sign_in_form_or_maintenance_is_not_a_change():
    from studia.ieulearn import NeedLogin

    def site(page):
        return CourseSite(Site({("GET", "/student/courses/7/overview/unit-plan"): (200, page)}))

    with pytest.raises(NeedLogin):
        site(FORM).unit_plan("7")
    with pytest.raises(Busy, match="maintenance"):
        site("<h1>Sistem bakımda</h1>").unit_plan("7")
    assert not health.is_changed("ieulearn")
    with pytest.raises(PageChanged):          # a course about maintenance whose plan changed: still a change
        site(UNITS.replace("data-rv-payload", "data-file").replace("Week 1 - Intro", "Bakım Mühendisliği")).unit_plan("7")


def _content(tmp_path, monkeypatch, pages):
    """A library with one course whose files, announcements and outcomes were read once, then IEULearn's pages."""
    from studia import syllabus

    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="7", title="2026-1 EEE 242 Sayısal Tasarım 1", code="EEE 242", name="S")])
    s = sync_mod.SyncService(lib, Settings)
    monkeypatch.setattr(syllabus, "fetch_syllabus", lambda code, session=None: None)
    first = {"/student/courses/7": OVERVIEW, "/student/courses/7/overview/unit-plan": UNITS,
             "/student/courses/7/overview/announcements": ANNOUNCEMENTS}
    site = Site({("GET", p): (200, body) for p, body in first.items()})
    monkeypatch.setattr(sync_mod, "clone_session", lambda s_: site)
    assert s.sync_content()["errors"] == []
    for path, body in pages.items():
        site.pages[("GET", path)] = (200, body)
    lib.set_course_info("7", overview_at="2000-01-01T00:00:00")        # the outcomes are read again too
    return lib, s


def test_course_pages_that_changed_keep_the_files_announcements_and_outcomes_they_had(tmp_path, monkeypatch):
    lib, s = _content(tmp_path, monkeypatch, {
        "/student/courses/7/overview/unit-plan": UNITS.replace("data-rv-payload", "data-file"),
        "/student/courses/7/overview/announcements": "<div class='news'></div>",
        "/student/courses/7": "<div class='course-v2'></div>"})
    res = s.sync_content()
    assert res["errors"] == [] and res["changed"] == ["ieulearn"] and res["new_materials"] == []
    assert sorted(m["resource_id"] for m in lib.materials("7")) == ["9001", "9002", "9003"]
    assert not any(m["gone"] for m in lib.materials("7", include_gone=True))
    assert len(lib.announcements("7")) == 1 and lib.course_info("7")["overview"]["outcomes"]
    assert changed_parts() == {"ieulearn.unit_plan", "ieulearn.announcements", "ieulearn.course_page"}


def test_a_plan_whose_files_cant_be_read_any_more_never_makes_them_vanish(tmp_path):
    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="7", title="2026-1 EEE 242 S 1", code="EEE 242", name="S")])
    lib.merge_units("7", parse_unit_plan(UNITS), first_sync=True)
    bare = parse_unit_plan(UNITS.replace("data-rv-payload", "data-file"))    # (were it not caught as a change)
    assert [len(u.resources) for u in bare] == [0, 0]
    lib.merge_units("7", bare, first_sync=False)
    assert len(lib.materials("7")) == 3


def test_a_course_that_couldnt_be_read_at_its_first_sync_gets_no_news_flood_once_it_can(tmp_path, monkeypatch):
    from studia import syllabus

    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="7", title="2026-1 EEE 242 Sayısal Tasarım 1", code="EEE 242", name="S")])
    s = sync_mod.SyncService(lib, Settings)
    monkeypatch.setattr(syllabus, "fetch_syllabus", lambda code, session=None: None)
    site = Site({("GET", "/student/courses/7"): (200, OVERVIEW),
                 ("GET", "/student/courses/7/overview/unit-plan"): (200, "<div class='plan-v2'></div>"),
                 ("GET", "/student/courses/7/overview/announcements"): (200, ANNOUNCEMENTS)})
    monkeypatch.setattr(sync_mod, "clone_session", lambda s_: site)
    assert s.sync_content()["changed"] == ["ieulearn"]
    site.pages[("GET", "/student/courses/7/overview/unit-plan")] = (200, UNITS)    # fixed (an update)
    res = s.sync_content()
    assert res["changed"] == [] and res["new_materials"] == [] and len(lib.materials("7")) == 3


# ------------------------------------------------------------------ the sync: what changed, and what stays
COURSES = [Course(id="a", title="2026-1 MATH 240 P 1", code="MATH 240", name="Probability"),
           Course(id="b", title="2026-1 EEE 215 E 2", code="EEE 215", name="Electronics")]


def _rec(cid, n):
    return Recording(delivery_id=f"{cid}{n}", course_id=cid, folder_id="f", folder_path="", name="lecture",
                     start=dt.datetime(2026, 10, 13 + n, 9, 0), duration_s=3000, viewer_url="v")


@pytest.fixture
def site(monkeypatch):
    st = types.SimpleNamespace(courses=list(COURSES), dash=None, listing={"a": [_rec("a", 0)], "b": [_rec("b", 0)]},
                               folders={"a": "fa", "b": "fb"}, launches=[], auth=True)

    class FakeLearn:
        def __init__(self, s):
            pass

        def dashboard(self):
            if isinstance(st.dash, BaseException):
                raise st.dash
            return Profile(name="Ali Veli"), [Course(**vars(c)) for c in st.courses]

        def is_logged_in(self):
            return True

        def launch_panopto(self, cid):
            st.launches.append(cid)
            return st.folders.get(cid)

    class FakePanopto:
        def __init__(self, s):
            pass

        def is_authenticated(self):
            if isinstance(st.auth, BaseException):
                raise st.auth
            return st.auth

        def list_recordings(self, cid, folder, heads=None):
            got = st.listing[cid]
            if isinstance(got, BaseException):
                raise got
            if heads is not None:
                heads[folder] = [len(got), got[0].delivery_id if got else "", 4]
            return got

    monkeypatch.setattr(sync_mod, "IEULearn", FakeLearn)
    monkeypatch.setattr(sync_mod, "Panopto", FakePanopto)
    return st


def test_a_courses_page_that_changed_keeps_the_courses_and_still_looks_for_their_recordings(site, tmp_path):
    lib = Library(tmp_path / "lib.db")
    s = sync_mod.SyncService(lib, Settings)
    s.sync()
    site.dash = health.changed("ieulearn", "courses", "no course card", cls=PageChanged)
    site.listing["a"] = [_rec("a", 0), _rec("a", 1)]
    res = s.sync()
    assert res.changed == ["ieulearn"] and res.errors == [] and res.new_ids == ["a1"]
    assert {c.id for c in lib.courses()} == {"a", "b"}                   # none moved to "Earlier courses"
    assert s.profile.name == "Ali Veli"


def test_an_empty_course_list_never_drops_the_courses_and_is_said(site, tmp_path):
    lib = Library(tmp_path / "lib.db")
    s = sync_mod.SyncService(lib, Settings)
    s.sync()
    site.courses = []
    res = s.sync()
    assert {c.id for c in lib.courses()} == {"a", "b"} and res.changed == []
    assert res.errors == ["IEULearn shows no courses for you right now, so the ones on this PC stay as they are. If it "
                          "stays like this, open learn.ieu.edu.tr to see what it shows."]


def test_panoptos_answer_that_changed_is_never_0_new_recordings_and_marks_nothing_gone(site, tmp_path):
    lib = Library(tmp_path / "lib.db")
    s = sync_mod.SyncService(lib, Settings)
    s.sync()
    site.listing["a"] = site.listing["b"] = health.changed("panopto", "sessions", "no d.Results")
    site.launches.clear()
    res = s.sync()
    assert res.changed == ["panopto"] and res.errors == [] and res.new_ids == []
    assert site.launches == []                               # an answer that changed isn't opened again and again
    assert {r.delivery_id for r in lib.recordings()} == {"a0", "b0"} == {r.delivery_id for r in
                                                                         lib.recordings(include_gone=True)}


def test_panoptos_sign_in_check_that_changed_is_said_as_that_not_as_panopto_down(site, tmp_path):
    lib = Library(tmp_path / "lib.db")
    s = sync_mod.SyncService(lib, Settings)
    s.sync()
    site.auth = health.changed("panopto", "signin", "legacyLogin answered HTTP 404")
    res = s.sync()
    assert res.changed == ["panopto"] and res.errors == [] and len(lib.recordings()) == 2


def test_a_listing_with_fewer_rows_than_panopto_counted_marks_nothing_gone(site, tmp_path):
    lib = Library(tmp_path / "lib.db")
    s = sync_mod.SyncService(lib, Settings)
    site.listing["a"] = [_rec("a", 0), _rec("a", 1)]
    s.sync()
    short = Listing([_rec("a", 1)])
    short.complete = False
    site.listing["a"] = short
    s.sync()
    assert {r.delivery_id for r in lib.recordings("a")} == {"a0", "a1"}


# ------------------------------------------------------------------ Panopto's answers
class Answer:
    def __init__(self, status=200, body=None, text="", url="https://panopto.ieu.edu.tr/x", ctype="application/json"):
        self.status_code, self.body, self.text, self.url = status, body, text, url
        self.headers = {"content-type": ctype}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        if self.body is None:
            raise requests.exceptions.JSONDecodeError("Expecting value", self.text, 0)
        return self.body


class PSession:
    cookies = types.SimpleNamespace(get=lambda *a, **k: "")

    def __init__(self, answer):
        self.answer = answer

    def post(self, url, **kw):
        return self.answer

    def get(self, url, **kw):
        return self.answer


ROW = {"DeliveryID": "ABC-1", "SessionID": "S1", "SessionName": "EEE 215 Week 4", "StartTime": "/Date(1792494951083)/",
       "Duration": 2400.5, "FolderID": "F", "ViewerUrl": "https://v", "Status": 4}
SESSIONS = {"d": {"__type": "SessionResults", "Results": [ROW], "TotalNumber": 1, "Subfolders": []}}
SIGNED_OUT = {"d": {"__type": "SessionResults", "Results": [], "TotalNumber": 0, "Subfolders": None}}   # as it is


def test_panoptos_answers_as_they_are_read_fine():
    assert [r.delivery_id for r in Panopto(PSession(Answer(body=SESSIONS))).list_recordings("c", "f")] == ["abc-1"]
    listing = Panopto(PSession(Answer(body=SIGNED_OUT))).list_recordings("c", "f")
    assert listing == [] and listing.complete                # (the access ran out: an empty folder, as before)
    assert not health.is_changed("panopto")


@pytest.mark.parametrize("answer,why", [
    (Answer(body={"Results": [ROW], "TotalNumber": 1}), "without d.Results"),               # no "d"
    (Answer(body={"d": {"Sessions": [ROW], "TotalNumber": 1}}), "without d.Results"),        # renamed
    (Answer(body={"d": {"Results": [ROW], "Count": 1}}), "d.TotalNumber"),
    (Answer(body={"d": {"Results": [{**ROW, "StartTime": "2026-10-20T11:15:51Z"}], "TotalNumber": 1}}),
     "StartTime IEUStudia can't read"),
    (Answer(body={"d": {"Results": [{k.lower(): v for k, v in ROW.items()}], "TotalNumber": 1}}),
     "without DeliveryID or StartTime"),
    (Answer(body=None, text="<html>New Panopto</html>", ctype="text/html"), "instead of JSON"),
    (Answer(status=404, body=None, text="<html>404</html>"), "HTTP 404"),
])
def test_panoptos_listing_that_changed_is_a_change_never_an_empty_folder(answer, why):
    with pytest.raises(ServiceChanged) as e:
        Panopto(PSession(answer)).list_recordings("c", "f")
    assert "Panopto changed something on its side." in str(e.value) and "look for new recordings" in str(e.value)
    assert changed_parts() == {"panopto.sessions"} and why in health.details()


def test_a_row_that_cant_be_read_among_others_makes_the_listing_incomplete():
    odd = {**ROW, "DeliveryID": "ABC-2", "StartTime": "soon"}
    got = Panopto(PSession(Answer(body={"d": {"Results": [ROW, odd], "TotalNumber": 2}}))).list_recordings("c", "f")
    assert [r.delivery_id for r in got] == ["abc-1"] and not got.complete and not health.is_changed("panopto")
    playlist = {**ROW, "DeliveryID": None}                   # not a recording: passed over, as before
    got = Panopto(PSession(Answer(body={"d": {"Results": [ROW, playlist], "TotalNumber": 2}}))).list_recordings("c", "f")
    assert got.complete and len(got) == 1
    assert parse_sessions({"Results": [playlist]}, "c", "") == []


def test_the_sign_in_page_instead_of_json_is_a_sign_in_not_a_change():
    with pytest.raises(NeedPanoptoAuth):
        Panopto(PSession(Answer(body=None, text="<form>", ctype="text/html",
                                url="https://panopto.ieu.edu.tr/Panopto/Pages/Auth/Login.aspx"))).list_recordings("c", "f")
    assert not health.is_changed("panopto")


DELIVERY = {"Delivery": {"IsAudioPodcastEncodeComplete": True, "PodcastStreams": [{"StreamUrl": "https://p/a.mp4"}],
                         "Streams": [{"Tag": "DV", "StreamUrl": "https://p/dv/master.m3u8"}]}}


def test_a_delivery_that_changed_is_said_never_still_processing():
    p = Panopto(PSession(Answer(body=DELIVERY)))
    assert [s.kind for s in p.media_sources("d")] == ["audio_podcast", "video_podcast", "hls"]
    for body in ({"Session": {}}, {"Delivery": {"Media": []}}, {"Delivery": {"Streams": {"DV": "x"}}}):
        with pytest.raises(ServiceChanged, match="can't download lectures from Panopto"):
            Panopto(PSession(Answer(body=body))).delivery_info("d")
    assert changed_parts() == {"panopto.delivery"}
    health.ok("panopto", "delivery")
    with pytest.raises(RuntimeError, match="This session link is invalid.") as e:     # Panopto's own words
        Panopto(PSession(Answer(body={"ErrorCode": 5, "ErrorMessage": "This session link is invalid.<br>",
                                      "LoginRedirect": False}))).delivery_info("d")
    assert not isinstance(e.value, ServiceChanged)
    with pytest.raises(NeedPanoptoAuth):
        Panopto(PSession(Answer(body={"ErrorCode": 2, "LoginRedirect": True}))).delivery_info("d")


def test_panoptos_sign_in_check_that_is_gone_is_a_change():
    assert Panopto(PSession(Answer(status=401, body={"Message": "Authorization has been denied"}))).is_authenticated() \
        is False
    assert Panopto(PSession(Answer(status=200, body={}))).is_authenticated() is True
    with pytest.raises(ServiceChanged, match="can't check its Panopto sign-in"):
        Panopto(PSession(Answer(status=404, body=None, text="<html>"))).is_authenticated()


def test_a_stream_that_isnt_a_playlist_any_more_is_a_change():
    from studia import hls

    good = Answer(text="#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1,CODECS=\"mp4a.40.2\"\n1/index.m3u8\n")
    assert hls.media_playlist(PSession(good), "https://p/master.m3u8").text.startswith("#EXTM3U")
    with pytest.raises(ServiceChanged, match="sound or the slides"):
        hls.media_playlist(PSession(Answer(text="<html>player v2</html>", ctype="text/html")), "https://p/m.m3u8")


# ------------------------------------------------------------------ the way into Panopto (IEULearn's launch)
STAGE = ('<body class="st-pano"><div class="st-pano__frame-wrap"><iframe class="st-pano__frame" id="stPanoFrame" '
         'src="https://learn.ieu.edu.tr/lti/course-launch?course=7&amp;tool=3&amp;as=learner"></iframe></div></body>')


def test_a_panopto_page_without_its_launch_frame_is_a_change_not_no_panopto():
    moved = STAGE.replace("<iframe", "<panopto-frame").replace("src=", "data-src=")
    with pytest.raises(ServiceChanged, match="open Panopto through IEULearn"):
        IEULearn(Site({("GET", "/student/panopto"): (200, moved)})).launch_panopto("7")
    health.ok("panopto", "lti", where="7")                   # (that course's way in works again)
    assert IEULearn(Site({("GET", "/student/panopto"): (200, "<p>Bu derste Panopto yok.</p>")})).launch_panopto("7") \
        is None                                              # a course without Panopto stays one
    empty_stage = '<body class="st-pano"><div class="st-pano__empty">Bu derste Panopto yok.</div></body>'
    assert IEULearn(Site({("GET", "/student/panopto"): (200, empty_stage)})).launch_panopto("7") is None
    assert not health.is_changed("panopto")
    site = Site({("GET", "/student/panopto"): (200, STAGE), ("GET", "/lti/course-launch"): (200, "<p>Launching…</p>")})
    with pytest.raises(ServiceChanged):                      # the launch page lost its form
        IEULearn(site).launch_panopto("7")
    health.ok("panopto", "lti", where="7")
    site.pages[("GET", "/lti/course-launch")] = (200, "<h1>Hata</h1><p>Panopto'ya bağlanılamadı.</p>")
    with pytest.raises(RuntimeError) as e:                   # an error IEULearn shows there: tried again
        IEULearn(site).launch_panopto("7")
    assert not isinstance(e.value, ServiceChanged) and not health.is_changed("panopto")


def test_the_launch_as_it_is_reads_fine(monkeypatch):
    fid = "d893262b-1027-4262-a147-b4cb007bffcc"
    launch = '<form id="ltiAutoForm" action="https://learn.ieu.edu.tr/lti/auth" method="post"><input name="x" value="1"></form>'
    site = Site({("GET", "/student/panopto"): (200, STAGE), ("GET", "/lti/course-launch"): (200, launch),
                 ("POST", "/lti/auth"): (200, "<p>Panopto</p>",
                                         f"https://panopto.ieu.edu.tr/Panopto/Pages/Sessions/List.aspx#folderID=%22{fid}%22")})
    assert IEULearn(site).launch_panopto("7") == fid and not health.is_changed("panopto")


# ------------------------------------------------------------------ a part read per course: one course's page
def test_one_courses_page_that_changed_stays_one_change_while_the_others_read_fine():
    """EEE 242's plan changed, the other courses' read fine: the change flipped to "works" and back at every sync
    (no warning when the changed course was read first, a new one with a new key when it was read last, Hide gone)."""
    changed_plan = UNITS.replace("data-rv-payload", "data-file")
    site = Site({("GET", "/student/courses/7/overview/unit-plan"): (200, changed_plan),
                 ("GET", "/student/courses/8/overview/unit-plan"): (200, UNITS),
                 ("GET", "/student/courses/9/overview/unit-plan"): (200, UNITS)})
    keys = set()
    for order in (("7", "8", "9"), ("8", "9", "7"), ("9", "7", "8")):     # every sync reads them in its own order
        for cid in order:
            try:
                CourseSite(site).unit_plan(cid)
            except PageChanged:
                assert cid == "7"
        ieu = next(s for s in health.snapshot()["services"] if s["id"] == "ieulearn")
        assert ieu["state"] == "changed"
        keys.add(ieu["key"])
    assert len(keys) == 1                                     # one change: Hide hides it, the helper tells it once
    part = next(p for p in ieu["parts"] if p["part"] == "unit_plan")
    assert part["where"] == ["7"] and part["whole"] is False
    assert health.news() == ["IEULearn"] and health.news() == []
    assert "for 1 of the courses read" in health.details()
    site.pages[("GET", "/student/courses/7/overview/unit-plan")] = (200, UNITS)     # read again (an update, or IEULearn)
    CourseSite(site).unit_plan("7")
    assert not health.is_changed("ieulearn")


def test_a_course_not_seen_changed_for_days_is_let_go():
    health.changed("ieulearn", "announcements", "no announcement list", where="7")
    data = json.loads(health._path().read_text(encoding="utf-8"))
    data["parts"]["ieulearn.announcements"]["where"]["7"]["seen"] = "2026-01-01T10:00:00"     # (left the dashboard)
    health._path().write_text(json.dumps(data), encoding="utf-8")
    health._cache["mtime"] = None
    assert not health.is_changed("ieulearn") and health.snapshot()["changed"] == [] and health.news() == []
    health.ok("ieulearn", "announcements", where="8")
    assert not health.is_changed("ieulearn")


def test_a_part_that_changed_as_a_whole_isnt_over_because_one_course_reads():
    health.changed("panopto", "sessions", "GetSessions answered HTTP 404")       # the address is gone: every course
    health.ok("panopto", "sessions", where="7")
    assert health.is_changed("panopto", "sessions")
    health.ok("panopto", "sessions")
    assert not health.is_changed("panopto")


def test_a_sign_in_form_whose_field_was_renamed_is_a_change_and_the_password_is_kept():
    """Its own form (id loginForm) with "email" renamed to "username": IEUStudia posted email and password, IEULearn
    showed the form again with its error, and the saved (right) password was marked rejected."""
    renamed = FORM.replace('name="email"', 'name="username"')
    site = Site({("GET", "/login"): (200, renamed),
                 ("POST", "/login"): (200, renamed + '<div class="alert">Kullanıcı adı veya şifre hatalı.</div>', "/login")})
    with pytest.raises(PageChanged, match="Your password was not rejected") as e:
        IEULearn(site).login("ali", "right")
    assert isinstance(e.value, SiteChanged) and ("POST", "/login") not in site.asked
    assert health.is_changed("ieulearn", "signin") and "other fields" in health.details()


def test_a_service_that_doesnt_answer_is_said_as_that_in_settings():
    """Only Panopto's listing was ever noted as not answering: during an IEULearn outage Settings → Services kept
    saying "Works as IEUStudia expects"."""
    from studia import videos

    with pytest.raises(Busy):
        IEULearn(Site({("GET", "/login"): (503, "<h1>Service Unavailable</h1>")})).login("ali", "pw")
    state = {s["id"]: s["state"] for s in health.snapshot()["services"]}
    assert state["ieulearn"] == "down"
    videos._video_error(Exception("Unable to download webpage: <urlopen error [Errno 11001] getaddrinfo failed>"), "x")
    assert {s["id"]: s["state"] for s in health.snapshot()["services"]}["youtube"] == "down"
    IEULearn(Site({("GET", "/login"): (200, FORM), ("POST", "/login"): (200, "<p>ok</p>", "/student")})).login("ali", "pw")
    assert {s["id"]: s["state"] for s in health.snapshot()["services"]}["ieulearn"] == "ok"     # answering again
