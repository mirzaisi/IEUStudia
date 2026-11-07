"""IEULearn (learn.ieu.edu.tr) client: login, dashboard parsing and the LTI 1.3 launch into Panopto."""

from __future__ import annotations

import html
import logging
import re
from typing import Optional
from urllib.parse import unquote, urljoin, urlparse

import requests

from .domain import Course, Profile
from .i18n import t
from .net import Form, NetworkError, parse_forms, parse_iframes

log = logging.getLogger("studia")
BASE = "https://learn.ieu.edu.tr"
_MOVED = (301, 302, 303, 307, 308)


class AuthError(Exception):
    """Wrong or missing credentials (message is shown to the user)."""

    def __init__(self, message: str, server_error: bool = False) -> None:
        super().__init__(message)
        self.server_error = server_error     # said along with a server error: maybe about the server, not the password


class AccountAction(AuthError):
    """IEULearn took the password but wants something done on its site first: accept its terms, set a new password
    for an expired one, finish a profile. Only the student can, in a browser (the page offers to open IEULearn).
    Not a wrong password; still, the saved one isn't tried again in the background until the student signs in here
    again (every sign-in would land on the same page)."""

    open_site = True
    why = "action"            # "password_change": a new password is wanted (kept with the saved one, see sync.py)


class Busy(NetworkError):
    """IEULearn answered, but not with a sign-in (busy, throttled, under maintenance): not a wrong password, and not a
    lost connection either (a sign-in in the background isn't tried again at once, see SyncService.ensure_login)."""


class SiteChanged(RuntimeError):
    """IEULearn answered with a page IEUStudia doesn't know where it expects your courses: maintenance, or a site
    that changed (then IEUStudia needs an update)."""


class NeedLogin(Exception):
    """The session expired and there are no stored credentials to sign in again."""


def _busy() -> Busy:
    return Busy(t("IEULearn didn't finish signing you in (it may be busy or under maintenance). Your password was not "
                  "rejected: try again later."))


def _foreign() -> Busy:
    return Busy(t("A page that isn't IEULearn's came back instead (a Wi-Fi that wants you to sign in first, a firewall "
                  "or an antivirus can do this). Open learn.ieu.edu.tr in your browser: sign in to the Wi-Fi if it "
                  "asks, or allow IEUStudia in your antivirus or firewall, then try again."))


def _action() -> AccountAction:
    return AccountAction(t("IEULearn wants something from you before it lets you in (for example accepting its terms "
                           "or changing your password). Open learn.ieu.edu.tr in your browser, sign in and do what it "
                           "asks, then sign in here again."))


def _new_password() -> AccountAction:
    err = AccountAction(t("IEULearn wants you to change your password first. Open learn.ieu.edu.tr in your browser, "
                          "sign in and set a new password, then sign in here with the new one."))
    err.why = "password_change"
    return err


def account_action(why: str) -> AccountAction:
    """The AccountAction kept with a saved password that isn't used (secrets_store.rejected_for), after a restart:
    what IEULearn wanted, not "your password wasn't accepted" (a student may reset a password that works)."""
    return _new_password() if why == "password_change" else _action()


# Where IEULearn may send /student instead of your courses (the sign-in page aside). Only a page that asks something
# of the student is an AccountAction (the saved password then waits for a sign-in here); a maintenance page or its
# home is IEULearn being busy, and a teaching account's pages aren't the student pages IEUStudia reads.
_ACTION_PAGE = re.compile(r"terms|kvkk|agreement|consent|privacy|gizlilik|onay|s[oö]zle[sş]me|ko[sş]ul|password|"
                          r"parola|[sş]ifre|profil|complete|tamamla|verif|do[gğ]rula|policy|politika", re.I)
_STAFF_PAGE = re.compile(r"^/(instructor|teacher|lecturer|staff|admin|academic|akademik|ogretmen|egitmen)", re.I)
_DOWN_PAGE = re.compile(r"maint|bak[iı]m|error|hata|unavailable|^/$|^/home|^/index|^/default", re.I)


def _sent_away(where: str) -> Exception:
    """What a redirect of /student to ``where`` (a path of IEULearn's, not its sign-in page) means."""
    if _ACTION_PAGE.search(where):
        return _action()
    if _STAFF_PAGE.search(where):
        return SiteChanged(t("IEULearn opens this account's teaching pages instead of its student pages, so IEUStudia "
                             "can't list your courses. IEUStudia works with student accounts."))
    if _DOWN_PAGE.search(where):
        return Busy(t("IEULearn isn't showing your courses right now (it may be under maintenance). Your password was "
                      "not rejected: try again later."))
    return Busy(t("IEULearn didn't open your courses page: it sent IEUStudia to another of its pages ({path}). It may "
                  "be under maintenance, or want something from you first: open learn.ieu.edu.tr in your browser to "
                  "see. Your password was not rejected.", path=where))


def _moved_to(r: requests.Response) -> Optional[str]:
    """Where a redirect sends you (its path), or None for an answer that isn't one."""
    if r.status_code not in _MOVED:
        return None
    return urlparse(urljoin(BASE + "/", r.headers.get("Location") or "")).path or "/"


def is_courses_page(page: str) -> bool:
    """The student's own page (their name, their course cards): what /student shows when signed in."""
    return any(mark in (page or "") for mark in ('class="ov-user-name"', 'class="dash-course"', 'id="coursesRoot"'))


def _passwords(f: Form) -> list[str]:
    return [n.lower() for n in f.fields if re.search(r"password|parola|sifre|şifre", n, re.I)]


def _is_sign_in(f: Form) -> bool:
    """IEULearn's sign-in form (id loginForm: an email and one password)."""
    return f.id == "loginForm" or (_passwords(f) == ["password"] and ("email" in f.fields or "username" in f.fields))


def _sign_in_form(page: str) -> Optional[Form]:
    return next((f for f in parse_forms(page) if _is_sign_in(f)), None)


def _password_change(page: str) -> bool:
    """A form to set a new password (an expired one): two password fields, or one named new or confirm."""
    for f in parse_forms(page):
        pw = _passwords(f)
        if not _is_sign_in(f) and (len(pw) >= 2 or any(re.search(r"new|yeni|confirm|onay", n) for n in pw)):
            return True
    return False


def _hints(message: str, username: str, password: str) -> str:
    """IEULearn's own words for a refused sign-in, and what may be behind it that its words don't say."""
    extra = []
    if re.search(r"lock|kilit|too many|çok fazla|blok|engel", message, re.I):
        extra.append(t("Wait as long as IEULearn says before you try again: every try can make the lock last longer."))
    if password != password.strip():
        extra.append(t("Your password starts or ends with a space (maybe copied along with it): if the space isn't "
                       "part of your password, remove it and try again."))
    if re.search(r"[çğıöşüÇĞİÖŞÜ]", username):
        extra.append(t("Your username has Turkish letters (like ı, İ or ş): EkoID usernames are written with plain "
                       "letters (i, s…), so check how you typed it."))
    elif " " in username.strip():
        extra.append(t("Your username has a space in it: EkoID usernames have none."))
    return " ".join([message, *extra])


_TITLE = re.compile(r"^\s*(?:(\d{4}-\d)\s+)?([A-ZÇĞİÖŞÜ]{2,6})\s*(\d{2,4}[A-Z]?)\s+(.*?)\s*$")
_SECTION_TAIL = re.compile(r"\s+(\d{1,2}(?:\s*-\s*\d{1,2})?)\s*$")


def split_course_title(title: str) -> tuple[str, str, str]:
    """``"2026-1 EEE 215 Blgsyr. ... Giriş 0 - 1"`` → ``("EEE 215", "Blgsyr. ... Giriş", "0 - 1")``."""
    text = re.sub(r"\s+", " ", html.unescape(title or "")).strip()
    m = _TITLE.match(text)
    if not m:
        return "", text, ""
    code = f"{m.group(2)} {m.group(3)}"
    rest = m.group(4)
    section = ""
    sm = _SECTION_TAIL.search(rest)
    if sm and len(rest) > len(sm.group(0)) + 2:
        section = re.sub(r"\s+", " ", sm.group(1)).strip()
        rest = rest[: sm.start()].strip()
    return code, rest.strip(" -–"), section


def _attr(tag: str, name: str) -> str:
    m = re.search(fr'{name}="([^"]*)"', tag)
    return html.unescape(m.group(1)).strip() if m else ""


def parse_dashboard(page: str) -> tuple[Profile, list[Course]]:
    name = re.search(r'class="ov-user-name"[^>]*>(.*?)<', page, re.S)
    number = re.search(r'class="ov-user-role"[^>]*>(.*?)<', page, re.S)
    profile = Profile(name=html.unescape(name.group(1)).strip() if name else "",
                      student_number=html.unescape(number.group(1)).strip() if number else "")
    courses: list[Course] = []
    seen: set[str] = set()
    for pos, m in enumerate(re.finditer(r'<article class="dash-course"[^>]*>(.*?)</article>', page, re.S)):
        tag = m.group(0)[: m.group(0).find(">") + 1]
        body = m.group(1)
        cid = _attr(tag, "data-course-id")
        if not cid or cid in seen:
            continue
        seen.add(cid)
        title = _attr(tag, "data-course-title")
        code, cname, section = split_course_title(title)
        inst = re.search(r'class="dash-course-school"[^>]*title="([^"]*)"', body)
        term = re.search(r'class="dash-course-class"[^>]*>(.*?)<', body, re.S)
        cover = re.search(r"""class="dash-course-cover"[^>]*background-image:\s*url\(['"]?([^'")]+)""", body)
        prog = re.search(r'(?:İlerleme|Progress)\s*:\s*(\d{1,3})\s*%', body)
        courses.append(Course(
            id=cid, title=title, code=code or title[:12], name=cname, section=section,
            instructor=html.unescape(inst.group(1)).strip() if inst else "",
            term=html.unescape(term.group(1)).strip() if term else "",
            status=_attr(tag, "data-course-status") or "ongoing", position=pos,
            cover_url=html.unescape(cover.group(1)).strip() if cover else "",
            progress=int(prog.group(1)) if prog else None,
        ))
    return profile, courses


def parse_course_list(page: str) -> Optional[list[Course]]:
    """The "My courses" list the dashboard loads when its courses tab opens (~2 KB on the wire instead of the
    dashboard's ~80 KB): the same course cards, without the term and the progress, and no name or student number.
    A card's status is the tab it is in (this term's / past courses). None when the page isn't that list, or its
    cards don't add up to its tabs' counts (or the counts are gone: the page changed): the caller loads the
    dashboard."""
    if 'id="coursesRoot"' not in page:
        return None
    _, courses = parse_dashboard(page)
    counts = [int(n) for n in re.findall(r'class="tab-count"[^>]*>\s*(\d+)\s*<', page)]
    if not courses or not counts or sum(counts) != len(courses):
        return None
    by_id = {c.id: c for c in courses}
    past = page.find('id="tab-past"')
    for m in re.finditer(r'<article class="dash-course"[^>]*>(.*?)</article>', page, re.S):
        tag = m.group(0)[: m.group(0).find(">") + 1]
        c = by_id.pop(_attr(tag, "data-course-id"), None)
        if c is None:
            continue
        c.status = _attr(tag, "data-course-status") or ("past" if 0 <= past < m.start() else "ongoing")
        inst = re.search(r'class="dash-course-school"[^>]*>(.*?)<', m.group(1), re.S)
        c.instructor = c.instructor or (html.unescape(inst.group(1)).strip() if inst else "")
    return courses


def extract_folder_id(urls: list[str]) -> Optional[str]:
    for u in urls:
        d = unquote(unquote(u or ""))
        m = re.search(r"folderID=\"?([0-9a-fA-F-]{36})", d)
        if m:
            return m.group(1).lower()
    return None


def _not_down(r: requests.Response, signing_in: bool = False) -> None:
    """IEULearn answering with a server error (maintenance, overload) is not a wrong password."""
    if r.status_code >= 500:
        raise Busy(t("IEULearn isn't responding right now (it may be down for maintenance). Your password was "
                     "not rejected: try again later.") if signing_in else
                   t("IEULearn isn't responding right now (it may be down for maintenance). Try again later."))


def _elsewhere(r: requests.Response) -> Optional[Exception]:
    """A sign-in that ended on another site: IEULearn now signs in somewhere else (its sign-in changed: IEUStudia
    needs an update), or a filter's block page took its place."""
    host = (urlparse(r.url or "").hostname or "").lower()
    if not host or host == urlparse(BASE).hostname:
        return None
    log.warning("Signing in to IEULearn ended on %s", host)
    if re.search(r"login|sso|auth|account|cas\.", host):
        return SiteChanged(t("IEULearn now signs you in on another site ({host}), which IEUStudia can't do yet. Check "
                             "for an IEUStudia update in Settings → About.", host=host))
    return _foreign()


class IEULearn:
    BASE = BASE

    def __init__(self, session: requests.Session) -> None:
        self.s = session

    def is_logged_in(self) -> bool:
        r = self.s.get(f"{BASE}/student", allow_redirects=False)
        _not_down(r)
        return r.status_code == 200 and _sign_in_form(r.text) is None

    def login(self, username: str, password: str) -> None:
        if not username or not password:
            raise AuthError(t("Please enter your EkoID username and password."))
        page = self.s.get(f"{BASE}/login")
        _not_down(page, signing_in=True)
        away = _elsewhere(page)
        if away is not None:
            raise away
        form = _sign_in_form(page.text)
        token = form.fields.get("csrf_token", "") if form else ""
        if not token:
            m = re.search(r'name="csrf_token"\s+value="([^"]+)"', page.text)
            token = m.group(1) if m else ""
        if form is None and not token:      # a throttle, a firewall or a maintenance page instead of the sign-in page
            log.warning("IEULearn's sign-in page has no sign-in form (HTTP %d, %d bytes)", page.status_code,
                        len(page.text or ""))
            if page.status_code == 200:     # (or a page that changed: then it stays like this)
                raise Busy(t("IEULearn didn't show its sign-in page as IEUStudia knows it (it may be under maintenance, "
                             "or the page changed). Your password was not rejected: try again later. If it's still "
                             "like this tomorrow, check for an IEUStudia update in Settings → About."))
            raise Busy(t("IEULearn didn't show its sign-in page right now (it may be busy or under maintenance). Your "
                         "password was not rejected: try again later."))
        r = self.s.post(f"{BASE}/login", data={"csrf_token": token, "email": username.strip(),
                                               "password": password, "remember": "1"})
        if r.ok and "/student" in r.url and "loginForm" not in r.text and not _password_change(r.text):
            return
        if _password_change(r.text):       # an expired password: a new one is set on IEULearn itself
            raise _new_password()
        form_again = _sign_in_form(r.text) is not None
        msg = self._login_error(r.text) if form_again else ""
        if not msg:                 # after a server error, only IEULearn's own words on its sign-in form are shown
            _not_down(r, signing_in=True)
        if form_again:
            raise AuthError(_hints(msg or t("Sign-in failed. Check your EkoID username and password."), username,
                                   password), server_error=r.status_code >= 500)
        away = _elsewhere(r)
        if away is not None:
            raise away
        if not r.ok:                # a throttle or a firewall instead of an answer: not a refused password
            raise _busy()
        self._landed_elsewhere(r)

    def _landed_elsewhere(self, r: requests.Response) -> None:
        """The sign-in went to a page of IEULearn's other than your courses, with no sign-in form on it. IEULearn's
        own /student says what happened: signed in after all (your courses open), IEULearn wants something first
        (it sends /student to its terms, a profile, a new password: AccountAction), or the sign-in didn't take
        (busy, maintenance)."""
        check = self.s.get(f"{BASE}/student", allow_redirects=False)
        _not_down(check, signing_in=True)
        where = _moved_to(check)
        if where is not None and where.startswith("/student") and where != "/student":     # (/student/: the same)
            check = self.s.get(f"{BASE}{where}", allow_redirects=False)
            _not_down(check, signing_in=True)
            where = _moved_to(check)
        if check.status_code == 200 and is_courses_page(check.text):
            log.info("Signed in to IEULearn (it showed %s first)", urlparse(r.url or "").path)
            return
        if where is not None and "/login" not in where:
            log.warning("IEULearn signed in but sends /student to %s", where)
            raise _sent_away(where)
        asks = [f for f in parse_forms(r.text) + parse_forms(check.text) if not _is_sign_in(f)
                and not re.search(r"logout|cikis|search|ara\b", f.action, re.I)]
        if check.status_code == 200 and _sign_in_form(check.text) is None and asks:
            log.warning("IEULearn signed in but showed a page with a form at %s", urlparse(r.url or "").path)
            raise _action()
        raise _busy()

    @staticmethod
    def _login_error(page: str) -> str:
        for m in re.finditer(r'<(?:div|p|span)[^>]*class="[^"]*(?:error|alert|invalid)[^"]*"[^>]*>(.*?)</', page or "", re.S):
            text = re.sub(r"<[^>]+>|\s+", " ", html.unescape(m.group(1))).strip()
            if 3 < len(text) < 200:
                return text
        return ""

    def dashboard(self) -> tuple[Profile, list[Course]]:
        """Your name and courses from /student. A redirect to the sign-in page is an expired session (NeedLogin); one
        to anywhere else is IEULearn wanting something first (AccountAction). A page that isn't your courses page is
        never taken for "no courses": maintenance, or a site that changed, is said (SiteChanged)."""
        r = self.s.get(f"{BASE}/student", allow_redirects=False)
        where = _moved_to(r)
        if where is not None and where.startswith("/student") and where != "/student":
            r = self.s.get(f"{BASE}{where}", allow_redirects=False)       # IEULearn moved the page within its own
            where = _moved_to(r)                                          # (also /student/: the same page)
        if where is not None:
            if "/login" in where:
                raise NeedLogin(t("Your IEULearn session has expired."))
            log.warning("IEULearn sends /student to %s", where)
            raise _sent_away(where)
        _not_down(r)
        if r.status_code == 429:
            raise Busy(t("IEULearn is busy right now (too many requests). Try again in a few minutes."))
        if r.status_code in (404, 410):          # (its raw "404 Client Error: … for url" reached the student)
            log.warning("IEULearn's /student answered %d", r.status_code)
            raise SiteChanged(t("IEULearn's page with your courses isn't where it was (error {code}): its pages may "
                                "have changed. Your courses on this PC stay as they are. Check for an IEUStudia "
                                "update in Settings → About.", code=r.status_code))
        if r.status_code in (401, 403):
            log.warning("IEULearn's /student answered %d", r.status_code)
            raise Busy(t("IEULearn refused to show your courses (error {code}). Your password was not rejected. If "
                         "learn.ieu.edu.tr opens in your browser on this network, sign in there to see whether it lets "
                         "you into its student pages; if it doesn't open, a network filter may block it: try another "
                         "network, such as your phone's hotspot.", code=r.status_code))
        if r.status_code >= 400:
            log.warning("IEULearn's /student answered %d", r.status_code)
            raise Busy(t("IEULearn answered with an error ({code}) instead of your courses. Try again later; if it "
                         "stays like this, open learn.ieu.edu.tr in your browser to see what it shows.",
                         code=r.status_code))
        if not is_courses_page(r.text):
            if _sign_in_form(r.text) is not None:
                raise NeedLogin(t("Your IEULearn session has expired."))
            log.warning("IEULearn's /student isn't the courses page (%d bytes)", len(r.text or ""))
            raise SiteChanged(t("IEULearn showed a page IEUStudia doesn't recognise instead of your courses: it may be "
                                "under maintenance, or its pages changed. Your courses on this PC stay as they are. If "
                                "it's still like this tomorrow, check for an IEUStudia update in Settings → About."))
        return parse_dashboard(r.text)

    def course_list(self) -> Optional[list[Course]]:
        """The courses from the "My courses" list (parse_course_list); like dashboard(), it checks the sign-in."""
        r = self.s.get(f"{BASE}/student/courses/overview-fragment", allow_redirects=False)
        where = _moved_to(r)
        if where is not None and "/login" in where:
            raise NeedLogin(t("Your IEULearn session has expired."))
        _not_down(r)
        if r.status_code != 200:                # moved, or refused another way: the dashboard decides
            return None
        return parse_course_list(r.text)

    def has_panopto(self, course_id: str) -> bool:
        r = self.s.get(f"{BASE}/student/courses/{course_id}")
        return f"student/panopto?course={course_id}" in r.text

    def launch_panopto(self, course_id: str) -> Optional[str]:
        """Run the LTI 1.3 launch for a course; Panopto cookies end up in the session.

        Returns the Panopto folder id of the course, or None when the course has no Panopto.
        Raises RuntimeError when the launch runs but Panopto does not hand back a folder.
        """
        r = self.s.get(f"{BASE}/student/panopto?course={course_id}&embed=1", allow_redirects=False)
        if r.status_code in (301, 302, 303):
            if "login" in (r.headers.get("Location") or ""):
                raise NeedLogin(t("Your IEULearn session has expired."))
            return None
        if r.status_code not in (200, 403, 404):    # a server error, throttling: not "no Panopto" (tried again)
            raise RuntimeError(t("Panopto did not open the folder of course {course}.", course=course_id))
        frames = [f for f in parse_iframes(r.text) if "lti/course-launch" in f]
        if not frames:
            return None  # this course has no Panopto tool
        r = self.s.get(urljoin(BASE, frames[0]))
        urls: list[str] = []
        for _ in range(3):  # course-launch form -> /lti/auth form -> Panopto LTI.aspx
            form = next((f for f in parse_forms(r.text)
                         if f.id == "ltiAutoForm" or "/lti" in f.action.lower()), None)
            if form is None:
                break
            r = self.s.post(urljoin(r.url, form.action), data=form.fields)
            urls += [h.headers.get("Location", "") for h in r.history] + [h.url for h in r.history] + [r.url]
        folder = extract_folder_id(urls)
        if folder is None:
            raise RuntimeError(t("Panopto did not open the folder of course {course}.", course=course_id))
        return folder
