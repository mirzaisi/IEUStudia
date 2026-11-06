"""IEULearn (learn.ieu.edu.tr) client: login, dashboard parsing and the LTI 1.3 launch into Panopto."""

from __future__ import annotations

import html
import logging
import re
from typing import Optional
from urllib.parse import unquote, urljoin

import requests

from .domain import Course, Profile
from .i18n import t
from .net import NetworkError, parse_forms, parse_iframes

log = logging.getLogger("studia")
BASE = "https://learn.ieu.edu.tr"


class AuthError(Exception):
    """Wrong or missing credentials (message is shown to the user)."""

    def __init__(self, message: str, server_error: bool = False) -> None:
        super().__init__(message)
        self.server_error = server_error     # said along with a server error: maybe about the server, not the password


class NeedLogin(Exception):
    """The session expired and there are no stored credentials to sign in again."""


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
        raise NetworkError(t("IEULearn isn't responding right now (it may be down for maintenance). Your password was "
                             "not rejected: try again later.") if signing_in else
                           t("IEULearn isn't responding right now (it may be down for maintenance). Try again later."))


class IEULearn:
    BASE = BASE

    def __init__(self, session: requests.Session) -> None:
        self.s = session

    def is_logged_in(self) -> bool:
        r = self.s.get(f"{BASE}/student", allow_redirects=False)
        _not_down(r)
        return r.status_code == 200

    def login(self, username: str, password: str) -> None:
        if not username or not password:
            raise AuthError(t("Please enter your EkoID username and password."))
        page = self.s.get(f"{BASE}/login")
        _not_down(page, signing_in=True)
        form = next((f for f in parse_forms(page.text) if "password" in f.fields or f.id == "loginForm"), None)
        token = form.fields.get("csrf_token", "") if form else ""
        if not token:
            m = re.search(r'name="csrf_token"\s+value="([^"]+)"', page.text)
            token = m.group(1) if m else ""
        if form is None and not token:      # a throttle, a firewall or a maintenance page instead of the sign-in page
            raise NetworkError(t("IEULearn didn't show its sign-in page right now (it may be busy or under "
                                 "maintenance). Your password was not rejected: try again later."))
        r = self.s.post(f"{BASE}/login", data={"csrf_token": token, "email": username.strip(),
                                               "password": password, "remember": "1"})
        if r.ok and "/student" in r.url and "loginForm" not in r.text:
            return
        form_again = any("password" in f.fields or f.id == "loginForm" for f in parse_forms(r.text))
        msg = self._login_error(r.text) if form_again else ""
        if not msg:                 # after a server error, only IEULearn's own words on its sign-in form are shown
            _not_down(r, signing_in=True)
        if not form_again:
            # no sign-in form came back: a throttle, a firewall or a maintenance page, not a refused password
            raise NetworkError(t("IEULearn didn't finish signing you in (it may be busy or under maintenance). "
                                 "Your password was not rejected: try again later."))
        raise AuthError(msg or t("Sign-in failed. Check your EkoID username and password."),
                        server_error=r.status_code >= 500)

    @staticmethod
    def _login_error(page: str) -> str:
        for m in re.finditer(r'<(?:div|p|span)[^>]*class="[^"]*(?:error|alert|invalid)[^"]*"[^>]*>(.*?)</', page or "", re.S):
            text = re.sub(r"<[^>]+>|\s+", " ", html.unescape(m.group(1))).strip()
            if 3 < len(text) < 200:
                return text
        return ""

    def dashboard(self) -> tuple[Profile, list[Course]]:
        r = self.s.get(f"{BASE}/student", allow_redirects=False)
        if r.status_code in (301, 302, 303):
            raise NeedLogin(t("Your IEULearn session has expired."))
        _not_down(r)
        r.raise_for_status()
        return parse_dashboard(r.text)

    def course_list(self) -> Optional[list[Course]]:
        """The courses from the "My courses" list (parse_course_list); like dashboard(), it checks the sign-in."""
        r = self.s.get(f"{BASE}/student/courses/overview-fragment", allow_redirects=False)
        if r.status_code in (301, 302, 303):
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
