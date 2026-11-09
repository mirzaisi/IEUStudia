"""Signing in to IEULearn, case by case: a sign-in that lands on a page IEULearn wants you to finish (its terms, an
expired password) says to open IEULearn in the browser (never "try again later" for ever); a refused one carries
hints for a pasted space, Turkish letters and a lock; a course page IEULearn won't open is that course's problem, not
an expired session; automatic sign-ins never come in bursts (a burst could lock the account)."""

import types

import pytest
import requests

from studia import secrets_store, sync as sync_mod
from studia.api import Api, same_username
from studia.config import Settings
from studia.coursesite import CourseClosed, CourseSite
from studia.domain import Course, Profile
from studia.ieulearn import BASE, AccountAction, AuthError, Busy, IEULearn, NeedLogin, PageChanged, SiteChanged
from studia.library import Library
from studia.net import NetworkError

FORM = ('<form id="loginForm" method="post" action="https://learn.ieu.edu.tr/login"><input name="csrf_token" '
        'value="tok"><input name="email"><input name="password" type="password"></form>')
COURSES = ('<div class="ov-user-name">Ali Veli</div><div id="dashCourseGrid"><article class="dash-course" '
           'data-course-id="7" data-course-title="2026-1 EEE 242 Sayısal Tasarım 1"></article></div>')


class Site:
    """IEULearn by address: (method, path) -> (status, body, where it ended, Location)."""

    def __init__(self, pages):
        self.pages, self.asked = pages, []

    def _go(self, method, url, **kw):
        path = url.replace(BASE, "").split("?")[0]
        self.asked.append((method, path))
        status, body, final, location = (self.pages[(method, path)] + (None,))[:4]
        final = final if final.startswith("http") else f"{BASE}{final}"

        def raise_for_status():
            if status >= 400:
                raise requests.HTTPError(f"{status} Error for url: {final}")

        return types.SimpleNamespace(status_code=status, text=body, url=final, ok=status < 400,
                                     headers={"Location": location} if location else {}, raise_for_status=raise_for_status)

    def get(self, url, **kw):
        return self._go("GET", url, **kw)

    def post(self, url, **kw):
        return self._go("POST", url, **kw)


def _login(after_post, student=None):
    pages = {("GET", "/login"): (200, FORM, "/login"), ("POST", "/login"): after_post}
    if student:
        pages[("GET", "/student")] = student
    site = Site(pages)
    IEULearn(site).login("ali.veli", "right")
    return site


def test_a_terms_page_after_signing_in_says_to_finish_it_on_ieulearn():
    with pytest.raises(AccountAction, match="Open learn.ieu.edu.tr in your browser, sign in and do what it asks") as e:
        _login((200, '<form action="/kvkk/accept" method="post"><input type="checkbox" name="ok" checked></form>', "/kvkk"),
               (302, "", "/student", "/kvkk"))
    assert e.value.open_site and not e.value.server_error


def test_an_expired_password_page_says_to_set_a_new_one_on_ieulearn():
    page = ('<form action="/password/update" method="post"><input name="password" type="password">'
            '<input name="password_confirmation" type="password"></form>')
    with pytest.raises(AccountAction, match="change your password first"):
        _login((200, page, "/password/expired"))


def test_landing_somewhere_else_but_signed_in_is_a_sign_in():
    site = _login((200, "<h1>Duyurular</h1>", "/"), (200, COURSES, "/student"))
    assert ("GET", "/student") in site.asked


def test_a_sign_in_that_didnt_take_is_still_busy_not_your_password():
    with pytest.raises(Busy, match="not rejected"):
        _login((200, "<h1>Lütfen bekleyin</h1>", "/wait"), (302, "", "/student", f"{BASE}/login"))


def test_a_sign_in_that_ended_on_another_site():
    with pytest.raises(Busy, match="A page that isn't IEULearn's came back"):
        _login((200, "<h1>Blocked by your network</h1>", "https://filter.example.net/blocked"))
    with pytest.raises(SiteChanged, match=r"another site \(login.microsoftonline.com\).*Settings → About"):
        _login((200, "<form></form>", "https://login.microsoftonline.com/common/oauth2"))


def test_a_sign_in_page_without_its_form_may_be_maintenance_or_a_changed_page():
    with pytest.raises(Busy, match="as IEUStudia knows it.*not rejected.*Settings → About"):
        IEULearn(Site({("GET", "/login"): (200, "<h1>IEULearn</h1><p>Yeni giriş</p>", "/login")})).login("ali", "pw")
    with pytest.raises(Busy, match="right now \\(it may be busy"):
        IEULearn(Site({("GET", "/login"): (429, "<h1>Too Many Requests</h1>", "/login")})).login("ali", "pw")


@pytest.mark.parametrize("user,password,hint", [
    ("ali.veli", "right ", "starts or ends with a space"),
    ("İlker.yılmaz", "right", "Turkish letters"),
    ("ali veli", "right", "has a space in it"),
])
def test_a_refusal_says_what_may_be_behind_it(user, password, hint):
    site = Site({("GET", "/login"): (200, FORM, "/login"),
                 ("POST", "/login"): (200, FORM + '<div class="alert">E-posta veya şifre hatalı.</div>', "/login")})
    with pytest.raises(AuthError, match=hint) as e:
        IEULearn(site).login(user, password)
    assert str(e.value).startswith("E-posta veya şifre hatalı.") and not isinstance(e.value, AccountAction)


def test_a_lock_says_to_wait_and_a_plain_refusal_has_no_hints():
    site = Site({("GET", "/login"): (200, FORM, "/login"),
                 ("POST", "/login"): (200, FORM + '<div class="alert">Hesabınız 15 dakika kilitlendi.</div>', "/login")})
    with pytest.raises(AuthError, match="every try can make the lock last longer"):
        IEULearn(site).login("ali.veli", "right")
    site.pages[("POST", "/login")] = (200, FORM + '<div class="alert">Invalid credentials.</div>', "/login")
    with pytest.raises(AuthError) as e:
        IEULearn(site).login("ali.veli", "right")
    assert str(e.value) == "Invalid credentials."


@pytest.mark.parametrize("answer,kind,words", [
    ((302, "", "/student", f"{BASE}/login"), NeedLogin, "session has expired"),
    ((302, "", "/student", "/kvkk"), AccountAction, "wants something from you"),
    ((200, "<h1>Bakımdayız</h1>", "/student"), Busy, "may be under maintenance"),       # down for a while, not changed
    ((200, "<h1>Yeni öğrenci paneli</h1>", "/student"), SiteChanged, "doesn't recognise"),   # a page that changed
    ((200, FORM, "/student"), NeedLogin, "session has expired"),
    ((429, "", "/student"), Busy, "too many requests"),
])
def test_the_courses_page_never_takes_another_page_for_no_courses(answer, kind, words):
    from studia import health

    with pytest.raises(kind, match=words):
        IEULearn(Site({("GET", "/student"): answer})).dashboard()
    assert health.is_changed("ieulearn", "courses") is (kind is SiteChanged)


@pytest.mark.parametrize("answer,kind,words", [
    # any redirect but the sign-in page was "IEULearn wants something from you" (and stopped the saved password)
    ((302, "", "/student", "/maintenance"), Busy, r"may be under maintenance\)\. Your password was not rejected"),
    ((302, "", "/student", "/"), Busy, "may be under maintenance"),
    ((302, "", "/student", "/instructor"), SiteChanged, "teaching pages"),
    ((302, "", "/student", "/somewhere/new"), Busy, r"another of its pages \(/somewhere/new\)"),
    ((302, "", "/student", "/terms/accept"), AccountAction, "wants something from you"),
    # its raw "403 Client Error: Forbidden for url: …" reached the sync and the sign-in screen
    ((403, "", "/student"), Busy, r"refused to show your courses \(error 403\)"),
    ((401, "", "/student"), Busy, r"error 401"),
    ((404, "", "/student"), SiteChanged, r"isn't where it was \(error 404\)"),
    ((418, "", "/student"), Busy, r"answered with an error \(418\)"),
])
def test_the_courses_page_answering_otherwise_is_said_plainly(answer, kind, words):
    with pytest.raises(kind, match=words):
        IEULearn(Site({("GET", "/student"): answer})).dashboard()


def test_the_courses_page_with_a_slash_is_the_same_page():
    site = Site({("GET", "/student"): (302, "", "/student", "/student/"), ("GET", "/student/"): (200, COURSES, "/student/")})
    profile, courses = IEULearn(site).dashboard()
    assert profile.name == "Ali Veli" and ("GET", "/student/") in site.asked


def test_a_sign_in_sent_on_to_a_maintenance_page_keeps_the_password():
    with pytest.raises(Busy, match="Your password was not rejected"):
        _login((200, "<h1>Bakım</h1>", "/maintenance"), (302, "", "/student", "/maintenance"))


def test_what_ieulearn_wanted_is_still_said_after_a_restart(learn, tmp_path):
    """The reason lived in memory only: after a restart (and in the tray helper) it became "didn't accept your saved
    password", and a student might reset a password that works."""
    secrets_store.set_credentials("ali", "right")
    learn.answer = AccountAction("IEULearn wants something from you before it lets you in.")
    with pytest.raises(AccountAction):
        _service(tmp_path).ensure_login()
    with pytest.raises(AccountAction, match="wants something from you"):
        _service(tmp_path).ensure_login()                 # a new start: the same reason, from secrets.bin
    from studia.ieulearn import _new_password

    secrets_store.set_credentials("ali", "right")
    learn.answer = _new_password()
    with pytest.raises(AccountAction):
        _service(tmp_path).ensure_login()
    with pytest.raises(AccountAction, match="change your password first"):
        _service(tmp_path).ensure_login()
    secrets_store.set_credentials("ali", "right")         # signed in again: nothing kept
    assert secrets_store.rejected_for() == ""
    learn.answer = AuthError("Invalid credentials.")
    with pytest.raises(AuthError):
        _service(tmp_path).ensure_login()
    with pytest.raises(NeedLogin, match="didn't accept your saved password"):
        _service(tmp_path).ensure_login()


def test_the_courses_page_moved_within_ieulearn_is_followed_once():
    site = Site({("GET", "/student"): (302, "", "/student", "/student/home"), ("GET", "/student/home"): (200, COURSES, "/student/home")})
    profile, courses = IEULearn(site).dashboard()
    assert profile.name == "Ali Veli" and [c.id for c in courses] == ["7"]


@pytest.mark.parametrize("answer,kind,words", [
    ((302, "", "/x", f"{BASE}/student/courses"), CourseClosed, "may not be open to students yet"),   # as the real site does
    ((302, "", "/x", f"{BASE}/login"), NeedLogin, "session has expired"),
    ((503, "", "/x"), Busy, "isn't responding right now"),
    ((429, "", "/x"), Busy, "too many requests"),
])
def test_a_course_page_ieulearn_wont_open_is_that_courses_problem(answer, kind, words):
    with pytest.raises(kind, match=words):
        CourseSite(Site({("GET", "/student/courses/7/overview/unit-plan"): answer})).unit_plan("7")


# ------------------------------------------------------------------ automatic sign-ins: never a burst
@pytest.fixture
def learn(monkeypatch):
    st = types.SimpleNamespace(logged_in=False, answer=None, logins=0)

    class FakeLearn:
        def __init__(self, s):
            pass

        def is_logged_in(self):
            return st.logged_in

        def login(self, user, password):
            st.logins += 1
            if st.answer is not None:
                raise st.answer
            st.logged_in = True

    monkeypatch.setattr(sync_mod, "IEULearn", FakeLearn)
    return st


def _service(tmp_path):
    return sync_mod.SyncService(Library(tmp_path / "lib.db"), Settings)


@pytest.mark.parametrize("answer", [Busy("IEULearn didn't finish signing you in."),
                                    AuthError("Kimlik servisine ulaşılamadı.", server_error=True),
                                    SiteChanged("IEULearn now signs you in on another site."),
                                    # (its copy raised a TypeError: PageChanged is made from its service and part)
                                    PageChanged("ieulearn", "signin", "IEULearn now signs you in on another site "
                                                                      "(login.ieu.edu.tr).")])
def test_ieulearn_answering_without_a_sign_in_isnt_asked_again_at_once(learn, tmp_path, answer):
    sync = _service(tmp_path)
    secrets_store.set_credentials("ali", "right")
    learn.answer = answer
    for _ in range(5):                                   # downloads, files and the helper asking one after another
        with pytest.raises(type(answer)) as e:
            sync.ensure_login()
        assert str(e.value) == str(answer) and getattr(e.value, "part", None) == getattr(answer, "part", None)
    assert learn.logins == 1 and not secrets_store.rejected()
    learn.answer = None
    sync.allow_sign_in()                                 # you pressed Sync: tried at once
    sync.ensure_login()
    assert learn.logins == 2 and learn.logged_in


def test_a_lost_connection_is_tried_again_as_soon_as_it_is_back(learn, tmp_path):
    sync = _service(tmp_path)
    secrets_store.set_credentials("ali", "right")
    learn.answer = NetworkError("Can't reach learn.ieu.edu.tr. Check your internet connection.")
    with pytest.raises(NetworkError):
        sync.ensure_login()
    learn.answer = None
    sync.ensure_login()
    assert learn.logins == 2


def test_the_wait_ends_by_itself(learn, tmp_path):
    sync = _service(tmp_path)
    secrets_store.set_credentials("ali", "right")
    learn.answer = Busy("busy")
    with pytest.raises(Busy):
        sync.ensure_login()
    learn.answer = None
    when, error = sync._login_failed
    sync._login_failed = (when - sync.LOGIN_RETRY_AFTER_S - 1, error)      # five minutes later (the app syncs every 30)
    sync.ensure_login()
    assert learn.logins == 2


def test_a_page_to_finish_on_ieulearn_stops_the_saved_password_and_keeps_saying_why(learn, tmp_path):
    sync = _service(tmp_path)
    secrets_store.set_credentials("ali", "right")
    learn.answer = AccountAction("IEULearn wants something from you before it lets you in.")
    for _ in range(3):
        with pytest.raises(AccountAction, match="wants something from you"):
            sync.ensure_login()
    assert learn.logins == 1 and secrets_store.rejected() and not sync.has_credentials()   # the jobs wait for you
    learn.answer = None
    secrets_store.set_credentials("ali", "right")      # signed in again on the sign-in screen
    sync.ensure_login()
    assert learn.logins == 2


# ------------------------------------------------------------------ a course's pages: its own problem, in words
def _content_service(tmp_path, monkeypatch, answers):
    from studia import coursesite, syllabus

    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="a", title="2026-1 MATH 240 P 1", code="MATH 240", name="P"),
                        Course(id="b", title="2026-1 EEE 215 E 1", code="EEE 215", name="E"),
                        Course(id="c", title="2026-1 EEE 242 S 1", code="EEE 242", name="S")])
    asked = []

    class FakeSite:
        def __init__(self, s):
            pass

        def unit_plan(self, cid, title):
            asked.append(cid)
            answer = answers.get(cid)
            if callable(answer):
                answer = answer()
            if isinstance(answer, BaseException):
                raise answer
            return []

        def announcements(self, cid):
            return []

        def overview(self, cid):
            return types.SimpleNamespace(to_json=lambda: {})

    monkeypatch.setattr(coursesite, "CourseSite", FakeSite)
    monkeypatch.setattr(syllabus, "fetch_syllabus", lambda code, session=None: None)
    return lib, sync_mod.SyncService(lib, Settings), asked


def test_each_courses_problem_is_one_plain_line_naming_the_courses(tmp_path, monkeypatch):
    gone = requests.HTTPError("404 Client Error: Not Found for url: https://learn.ieu.edu.tr/student/courses/c",
                              response=types.SimpleNamespace(status_code=404))
    closed = CourseClosed("IEULearn didn't open this course's page: the course may not be open to students yet, or you "
                          "no longer have access to it.")
    lib, sync, _ = _content_service(tmp_path, monkeypatch, {"a": closed, "b": closed, "c": gone})
    errors = sync.sync_content()["errors"]
    assert errors == ["EEE 215, MATH 240: IEULearn didn't open this course's page: the course may not be open to "
                      "students yet, or you no longer have access to it.",
                      "EEE 242: IEULearn didn't find the course's pages."]
    (tmp_path / "2").mkdir()
    lib, sync, _ = _content_service(tmp_path / "2", monkeypatch, {"a": Busy("IEULearn is busy right now (too many "
                                    "requests). Try again in a few minutes."), "b": ValueError("bad page")})
    res = sync.sync_content()
    # a page the parser fails on is IEULearn's page changing: no error line of its own, health says what (and the
    # course's other parts, its announcements here, are still read)
    assert res["errors"] == ["MATH 240: IEULearn is busy right now (too many requests). Try again in a few minutes."]
    assert res["changed"] == ["ieulearn"] and lib.course_info("b").get("content_at")
    assert sync._content_problem(ValueError("bad page")).startswith("IEUStudia couldn't read the course's pages")


def test_a_session_that_runs_out_halfway_is_signed_in_again_once_not_the_sign_in_screen(tmp_path, monkeypatch, learn):
    left = {"b": 1}

    def expired_once():
        if left["b"]:
            left["b"] -= 1
            return NeedLogin("Your IEULearn session has expired.")
        return None

    lib, sync, asked = _content_service(tmp_path, monkeypatch, {"b": expired_once})
    secrets_store.set_credentials("ali", "right")
    res = sync.sync_content()
    assert res["errors"] == [] and learn.logins == 1 and asked.count("b") == 2
    assert lib.course_info("b").get("content_at")


def test_a_course_that_asks_for_a_sign_in_again_right_after_one_is_said_not_looped(tmp_path, monkeypatch, learn):
    lib, sync, asked = _content_service(tmp_path, monkeypatch, {"b": lambda: NeedLogin("expired")})
    secrets_store.set_credentials("ali", "right")
    res = sync.sync_content()
    assert res["errors"] == ["EEE 215: IEULearn asked to sign in again for its pages although IEUStudia had just "
                             "signed in."]
    assert asked.count("b") == 2 and learn.logins == 1


# ------------------------------------------------------------------ the same person, however the keyboard typed it
@pytest.mark.parametrize("a,b,same", [("ilker.x@std.ieu.edu.tr", "İlker.x@std.ieu.edu.tr", True),
                                      ("ilker.x", " ILKER.X ", True), ("isik.a", "ışık.a", True),
                                      ("ali.veli", "ali.velioglu", False)])
def test_one_ekoid_however_it_was_typed(a, b, same):
    assert same_username(a, b) is same


def test_typing_your_username_with_a_turkish_capital_i_signs_you_in_not_a_new_profile(monkeypatch):
    from studia import accounts

    calls = []
    a = Api.__new__(Api)
    a._ready = types.SimpleNamespace(wait=lambda s: True)
    a._services = types.SimpleNamespace(sync=types.SimpleNamespace(
        sign_in=lambda u, p: calls.append(("sign_in", u)) or Profile(name="İlker", username=u)))
    monkeypatch.setattr(accounts, "active", lambda: types.SimpleNamespace(id="main", username="ilker.x@std.ieu.edu.tr"))
    monkeypatch.setattr(accounts, "remember", lambda *a: None)
    monkeypatch.setattr(Api, "profile_add", lambda self, u, p: calls.append(("profile_add", u)) or {"ok": False})
    monkeypatch.setattr(Api, "sync", lambda self: {"ok": True})
    assert a.sign_in("İlker.x@std.ieu.edu.tr", "pw")["ok"] and calls == [("sign_in", "İlker.x@std.ieu.edu.tr")]


def test_the_page_is_told_to_offer_ieulearn_for_a_page_to_finish_there():
    a = Api.__new__(Api)
    a._ready = types.SimpleNamespace(wait=lambda s: True)
    a._services = types.SimpleNamespace(sync=types.SimpleNamespace(sign_in=lambda u, p: (_ for _ in ()).throw(
        AccountAction("IEULearn wants you to change your password first."))))
    from studia import accounts

    accounts_active = accounts.active
    try:
        accounts.active = lambda: types.SimpleNamespace(id="main", username="")
        res = a.sign_in("ali", "pw")
    finally:
        accounts.active = accounts_active
    assert res == {"ok": False, "error": "IEULearn wants you to change your password first.", "need_login": True,
                   "open_site": True}
