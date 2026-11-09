"""A course's own content on IEULearn: learning outcomes, weekly topics, grading, the weekly unit plan with
its materials (slides, PDFs, PowerPoints) and the announcements.

All of it is server-rendered HTML: the course page and three panels it loads lazily
(``/student/courses/<id>/overview/unit-plan``, ``…/announcements``). Read-only.
"""

from __future__ import annotations

import dataclasses as dc
import datetime as dt
import hashlib
import html
import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

import requests

from .i18n import t
from .ieulearn import BASE, NeedLogin

log = logging.getLogger("studia")

_FOLD = str.maketrans("İIıŞşÇçĞğÜüÖö", "iiissccgguuoo")
# months by their names and short forms in Turkish and in English: IEULearn writes "23 Eyl - 29 Eyl" for a student
# whose IEULearn is in Turkish, and "23 Sep - 29 Sep" in English (its sign-in page is in English until you switch it)
MONTHS = {name: no for no, names in enumerate((
    "ocak oca january jan", "subat sub february feb", "mart mar march", "nisan nis april apr", "mayis may",
    "haziran haz june jun", "temmuz tem july jul", "agustos agu august aug", "eylul eyl september sept sep",
    "ekim eki october oct", "kasim kas november nov", "aralik ara december dec"), start=1) for name in names.split()}
_FULL = [name for name in MONTHS if len(name) > 4]
_DATE = re.compile(
    r"(?P<iy>\d{4})-(?P<im>\d{1,2})-(?P<id>\d{1,2})"                                  # 2026-09-23
    r"|\b(?P<na>\d{1,2})[./](?P<nb>\d{1,2})(?:[./](?P<ny>\d{4}|\d{2}))?\b"             # 23.09.2026, 9/23/2026, 23.09
    r"|\b(?P<dd>\d{1,2})\.?\s*(?P<dm>[^\W\d_]{3,})\.?(?:,?\s+(?P<dy>\d{4}))?"         # 23 Eyl, 23 Sep 2026
    r"|\b(?P<mm>[^\W\d_]{3,})\.?\s+(?P<md>\d{1,2})(?:st|nd|rd|th)?\b(?:,?\s+(?P<my>\d{4}))?")   # Sep 23, Sep 23, 2026
_CLOCK = re.compile(r"\s*,?\s*(?:at\s+|saat\s+)?(\d{1,2})[:.](\d{2})(?::\d{2})?(?:\s*([AaPp])\.?[Mm]\.?)?")
_undated: set[str] = set()          # courses whose unit plan had a subtitle without a date in it (said once in the log)


def _fold(word: str) -> str:
    """Turkish letters as plain ones, in lower case: "Şub" -> "sub", "NİSAN" -> "nisan", "Ağu" -> "agu"."""
    return word.translate(_FOLD).lower()


def month_of(word: str) -> Optional[int]:
    """A month from its name, its short form or a longer cut of it, in Turkish or English ("Eylül", "Eyl.", "Sept",
    "Agus"); None for any other word ("Hafta", "Week", "Marks")."""
    w = _fold(word.rstrip("."))
    if w in MONTHS:
        return MONTHS[w]
    return MONTHS.get(w[:3]) if len(w) > 3 and any(full.startswith(w) for full in _FULL) else None


def _in_term(month: int, year: int, semester: int) -> int:
    """The calendar year of a month in a term's academic year: fall (semester 1) runs from August of ``year`` to
    February; spring and summer are in ``year`` + 1."""
    return year + (1 if semester != 1 or month < 8 else 0)


def _date_of(m: re.Match, year: int, semester: int, us: bool = False) -> Optional[dt.date]:
    """The date one match of _DATE stands for (None when it is no date: "Hafta 1"). ``us``: a date in numbers is
    month first (9/23/2026, as English IEULearn may write it next to an AM/PM time); day first otherwise, unless the
    second number can't be a month."""
    g = m.groupdict()
    explicit: Optional[int] = None
    if g["iy"]:
        explicit, month, day = int(g["iy"]), int(g["im"]), int(g["id"])
    elif g["na"]:
        day, month = int(g["na"]), int(g["nb"])
        if (us and day <= 12) or month > 12:
            day, month = month, day
        if g["ny"]:
            explicit = int(g["ny"]) + (2000 if len(g["ny"]) == 2 else 0)
    elif g["dd"]:
        day, month = int(g["dd"]), month_of(g["dm"]) or 0
        explicit = int(g["dy"]) if g["dy"] else None
    else:
        day, month = int(g["md"]), month_of(g["mm"]) or 0
        explicit = int(g["my"]) if g["my"] else None
    if not 1 <= month <= 12:
        return None
    try:
        return dt.date(explicit or _in_term(month, year, semester), month, day)
    except ValueError:
        return None


def find_dates(text: str, year: int, semester: int) -> list[dt.date]:
    """The dates in a text, in order: "21 Eyl - 27 Eyl", "21 Sep - 27 Sep", "Sep 21 - Sep 27", "21.09.2026 -
    27.09.2026", "2026-09-21". A date without its year is in the course's term (see _in_term)."""
    return [d for d in (_date_of(m, year, semester) for m in _DATE.finditer(text or "")) if d]


def posted_when(text: str, today: Optional[dt.date] = None) -> Optional[dt.datetime]:
    """When an announcement was posted, from its card: "19.10.2026 15:53" (IEULearn in Turkish), and as another
    language may write it: "10/19/2026 3:53 PM", "19 Oct 2026 15:53", "Oct 19, 2026, 3:53 PM", "2026-10-19 15:53".
    Without a time it is the start of that day; without a year, the last such day up to today."""
    today = today or dt.date.today()
    us = bool(re.search(r"\d\s*[AaPp]\.?[Mm]\b", text or ""))
    for m in _DATE.finditer(text or ""):
        day = _date_of(m, today.year, 1, us)
        if day is None:
            continue
        if not any(m.group(k) for k in ("iy", "ny", "dy", "my")):              # no year written
            try:
                day = day.replace(year=today.year)
                if day > today:
                    day = day.replace(year=today.year - 1)
            except ValueError:                                                 # 29 February
                continue
        c = _CLOCK.match(text, m.end())
        hour, minute = (int(c.group(1)), int(c.group(2))) if c else (0, 0)
        if c and c.group(3):
            hour = hour % 12 + (12 if c.group(3).lower() == "p" else 0)
        try:
            return dt.datetime(day.year, day.month, day.day, hour, minute)
        except ValueError:
            continue
    return None


@dc.dataclass
class Outcome:
    no: int
    text: str


@dc.dataclass
class WeekTopic:
    no: int
    topic: str


@dc.dataclass
class GradeItem:
    name: str
    count: int
    weight: float          # percent of the final grade


@dc.dataclass
class Overview:
    description: str = ""
    outcomes: list[Outcome] = dc.field(default_factory=list)
    weeks: list[WeekTopic] = dc.field(default_factory=list)
    grading: list[GradeItem] = dc.field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return dc.asdict(self)

    @staticmethod
    def from_json(d: dict[str, Any]) -> "Overview":
        return Overview(d.get("description", ""), [Outcome(**o) for o in d.get("outcomes", [])],
                        [WeekTopic(**w) for w in d.get("weeks", [])], [GradeItem(**g) for g in d.get("grading", [])])


@dc.dataclass
class Resource:
    id: str
    title: str
    file_name: str = ""
    kind: str = ""           # pdf | presentation | document | link | …
    ext: str = ""
    url: str = ""
    download_url: str = ""


@dc.dataclass
class Unit:
    id: str
    position: int
    title: str
    start: Optional[dt.date] = None
    end: Optional[dt.date] = None
    readings: str = ""
    resources: list[Resource] = dc.field(default_factory=list)


@dc.dataclass
class Announcement:
    id: str
    title: str
    author: str
    posted_at: Optional[dt.datetime]
    body: str


def _text(fragment: str) -> str:
    """HTML fragment -> plain text with paragraph breaks."""
    s = re.sub(r"(?i)<br\s*/?>", "\n", fragment or "")
    s = re.sub(r"(?i)</(p|div|li|h\d|tr)>", "\n", s)
    s = html.unescape(re.sub(r"<[^>]+>", "", s))
    s = re.sub(r"[ \t ]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n\n", "\n".join(line.strip() for line in s.splitlines()))
    return s.strip()


def _inner(page: str, cls: str) -> str:
    m = re.search(rf'class="{re.escape(cls)}"[^>]*>(.*?)</', page, re.S)
    return _text(m.group(1)) if m else ""


def parse_overview(page: str) -> Overview:
    ov = Overview()
    m = re.search(r'class="ov-intro-lo-list"[^>]*>(.*?)</ol>', page, re.S)
    if m:
        for no, text in re.findall(r'class="ov-intro-lo-no"[^>]*>\s*(\d+)\.?\s*</span>\s*'
                                   r'<span class="ov-intro-lo-text"[^>]*>(.*?)</span>', m.group(1), re.S):
            ov.outcomes.append(Outcome(int(no), _text(text)))
    for no, topic in re.findall(r'class="ov-ects-week-no"[^>]*>\s*(\d+)\s*</span>\s*'
                                r'<span class="ov-ects-week-topic"[^>]*>(.*?)</span>', page, re.S):
        if not any(w.no == int(no) for w in ov.weeks):
            ov.weeks.append(WeekTopic(int(no), _text(topic)))
    for item in re.findall(r'<li class="ov-ects-assess-item"[^>]*>(.*?)</li>', page, re.S):
        name = _inner(item, "ov-ects-assess-name")
        count = re.search(r'class="ov-ects-assess-count"[^>]*>\s*(\d+)', item)
        pct = re.search(r'class="ov-ects-assess-pct"[^>]*>\s*([\d.,]+)\s*%', item)
        if name:
            ov.grading.append(GradeItem(name, int(count.group(1)) if count else 1,
                                        float(pct.group(1).replace(",", ".")) if pct else 0.0))
    d = re.search(r'class="ov-intro-paragraph"[^>]*>(.*?)</p>', page, re.S)
    ov.description = _text(d.group(1)) if d else ""
    return ov


def term_year(course_title: str) -> tuple[int, int]:
    """``"2026-1 MATH 240 …"`` -> (2026, 1): academic year 2026-27, first (fall) semester."""
    m = re.match(r"\s*(\d{4})-(\d)", course_title or "")
    if m:
        return int(m.group(1)), int(m.group(2))
    today = dt.date.today()
    return (today.year if today.month >= 8 else today.year - 1), (1 if today.month >= 8 or today.month == 1 else 2)


def parse_unit_plan(page: str, course_title: str = "") -> list[Unit]:
    year, sem = term_year(course_title)
    units: list[Unit] = []
    blocks = re.split(r'(?=<article class="curriculum-item)', page)
    for block in blocks:
        head = re.match(r'<article class="curriculum-item[^"]*"[^>]*data-item-id="(\d+)"', block)
        if not head:
            continue
        title = re.search(r'class="ovup-title-text"[^>]*title="([^"]*)"', block)
        title = html.unescape(title.group(1)).strip() if title else _inner(block, "item-title")
        sub = re.search(r'class="item-subtitle"[^>]*>(.*?)</div>', block, re.S)
        start = end = None
        if sub:
            words = _text(sub.group(1))
            dates = find_dates(words, year, sem)
            start = dates[0] if dates else None
            end = dates[1] if len(dates) > 1 else None
            if start and end and end < start:          # a unit across New Year
                end = end.replace(year=end.year + 1)
            if words and start is None and course_title not in _undated:
                _undated.add(course_title)             # the weeks then come from numbered titles only: said, once
                log.warning("A unit of %s has no date IEUStudia can read: %r", course_title[:40], words[:60])
        desc = re.search(r'class="ovup-syl-desc-body"[^>]*>(.*?)</div>', block, re.S)
        unit = Unit(head.group(1), len(units), title, start, end, _text(desc.group(1)) if desc else "")
        for raw in re.findall(r'data-rv-payload="([^"]+)"', block):
            try:
                p = json.loads(html.unescape(raw))
            except ValueError:
                continue
            if not p.get("id"):
                continue
            unit.resources.append(Resource(str(p["id"]), str(p.get("title") or p.get("file_name") or ""),
                                           str(p.get("file_name") or ""), str(p.get("type") or ""),
                                           str(p.get("file_ext") or "").lower(), str(p.get("url") or ""),
                                           str(p.get("download_url") or "")))
        units.append(unit)
    return units


_LINK = re.compile(r'(?is)<a\b[^>]*?\bhref\s*=\s*"(https?://[^"]+)"[^>]*>(.*?)</a>')


def _keep_links(fragment: str) -> str:
    """An announcement's links with their addresses, which plain text would lose: "<a href=U>T</a>" → "T (U)", or
    U alone where T is the address, so a Teams link or an attached file (a.ov-ann-file) still opens."""
    def one(m: re.Match) -> str:
        url, inner = m.group(1), m.group(2)
        text = _text(inner)
        return inner if html.unescape(url) in text else f"{inner} ({url})" if text else url
    return _LINK.sub(one, fragment or "")


def parse_announcements(page: str) -> list[Announcement]:
    out: list[Announcement] = []
    for card in re.findall(r'<article class="ov-ann-card[^"]*"[^>]*>(.*?)</article>', page, re.S):
        title = re.search(r'class="ov-ann-card-title"[^>]*>(.*?)</h4>', card, re.S)
        title_text = _text(re.sub(r'<span class="ov-badge-new".*?</span>', "", title.group(1), flags=re.S)) if title else ""
        meta = re.search(r'class="ov-ann-card-meta"[^>]*>(.*?)</div>', card, re.S)
        spans = [_text(s) for s in re.findall(r"<span[^>]*>(.*?)</span>", meta.group(1), re.S)] if meta else []
        author = spans[0] if spans else ""
        found = [w for w in (posted_when(s) for s in spans) if w]
        when = found[-1] if found else None            # (the date comes after the author's name)
        body = re.search(r'class="st-ov-ann-full"[^>]*>(.*)', card, re.S)
        body_text = _text(_keep_links(body.group(1))) if body else ""
        key = hashlib.sha1(f"{title_text}|{author}|{when}".encode("utf-8")).hexdigest()[:16]
        if title_text or body_text:
            out.append(Announcement(key, title_text, author, when, body_text))
    return out


def _count(page: str, pattern: str) -> int:
    return len(re.findall(pattern, page or ""))


def overview_problem(page: str, ov: Overview) -> str:
    """What doesn't add up on a course's page ("" when it reads as IEUStudia knows it): none of the parts the outcomes,
    weeks and grading come from, or parts whose items IEUStudia can't read. A course without outcomes is fine."""
    if "ov-intro" not in page and "ov-ects" not in page:
        return "no outcomes, weekly topics or grading parts on the course page"
    for marker, got, name in (('class="ov-intro-lo-no"', ov.outcomes, "learning outcomes"),
                              ('class="ov-ects-week-no"', ov.weeks, "weekly topics"),
                              ('class="ov-ects-assess-item"', ov.grading, "grading items")):
        if page.count(marker) and not got:
            return f"{page.count(marker)} {name} on the page, none read"
    return ""


def unit_plan_problem(page: str, units: list[Unit]) -> str:
    """What doesn't add up on a weekly unit plan ("" when it reads as IEUStudia knows it): no plan at all, units or
    files IEUStudia can't read. An empty plan IEULearn says is empty is fine."""
    items, files = _count(page, r'<article class="curriculum-item'), _count(page, r'class="ovup-syl-item"')
    payloads, read = _count(page, r"data-rv-payload="), sum(len(u.resources) for u in units)
    if "ovup-syl" not in page and not items:
        return "no unit plan on the page"
    if items > len(units):
        return f"{items} units on the page, {len(units)} read"
    if not units and "ovup-syl-empty" not in page:
        return "a unit plan without units and without its empty note"
    if units and all(not u.title for u in units):
        return f"{len(units)} units without their titles"
    if payloads > read:
        return f"{payloads} files on the page, {read} read"
    if files and not payloads:
        return f"{files} files on the page without what IEUStudia reads them from"
    return ""


def announcements_problem(page: str, items: list[Announcement]) -> str:
    """What doesn't add up on a course's announcements ("" when they read as IEUStudia knows them): no announcements
    panel, or announcements on it but none read. (The panel may hold fewer than its count says: only none is odd.)"""
    if 'class="ov-ann' not in page:
        return "no announcements panel"
    count = re.search(r'class="ov-ann-count"[^>]*>\s*(\d+)', page)
    said = max(int(count.group(1)) if count else 0, _count(page, r'class="ov-ann-card-title"'))
    if said and not items:
        return f"{said} announcements on the page, none read"
    if not items and not count and "ov-ann-empty" not in page:
        return "an announcements panel without announcements, a count or its empty note"
    return ""


class CourseClosed(RuntimeError):
    """IEULearn sent a course's page elsewhere (to the course list): the course isn't open to students yet, or you
    no longer have access to it. That course only: signing in again wouldn't change it."""


class CourseSite:
    def __init__(self, session: requests.Session) -> None:
        self.s = session

    def _get(self, path: str) -> str:
        from urllib.parse import urljoin, urlparse

        from .ieulearn import Busy

        r = self.s.get(f"{BASE}{path}", allow_redirects=False, timeout=(6, 30))
        if r.status_code in (301, 302, 303, 307, 308):
            where = urlparse(urljoin(BASE + "/", r.headers.get("Location") or "")).path
            if "/login" in where:           # (a course you can't open goes to /student/courses: not an expired session)
                raise NeedLogin(t("Your IEULearn session has expired."))
            raise CourseClosed(t("IEULearn didn't open this course's page: the course may not be open to students "
                                 "yet, or you no longer have access to it."))
        if r.status_code >= 500:
            raise Busy(t("IEULearn isn't responding right now (it may be down for maintenance). Try again later."))
        if r.status_code == 429:
            raise Busy(t("IEULearn is busy right now (too many requests). Try again in a few minutes."))
        r.raise_for_status()
        return r.text

    # Each part is checked against what its page should hold: one that doesn't add up raises ieulearn.PageChanged
    # (noted in health), so the course keeps what it had instead of an empty plan, no announcements or no outcomes.
    def overview(self, course_id: str) -> Overview:
        page = self._get(f"/student/courses/{course_id}")
        ov = parse_overview(page)
        return self._checked("course_page", page, overview_problem(page, ov), ov, course_id)

    def unit_plan(self, course_id: str, course_title: str = "") -> list[Unit]:
        page = self._get(f"/student/courses/{course_id}/overview/unit-plan")
        units = parse_unit_plan(page, course_title)
        return self._checked("unit_plan", page, unit_plan_problem(page, units), units, course_id)

    def announcements(self, course_id: str) -> list[Announcement]:
        page = self._get(f"/student/courses/{course_id}/overview/announcements")
        items = parse_announcements(page)
        return self._checked("announcements", page, announcements_problem(page, items), items, course_id)

    @staticmethod
    def _checked(part: str, page: str, problem: str, value: Any, course_id: str = "") -> Any:
        """``value`` when its page adds up; else what the page is: IEULearn's sign-in form (the session ran out), a
        maintenance page (busy for a while), or a page that changed (health: for that course; the other courses'
        pages reading fine don't make it over)."""
        from . import health
        from .ieulearn import Busy, _sign_in_form, changed, page_shape, under_maintenance

        if problem:
            if ("password" in page or "loginForm" in page) and _sign_in_form(page) is not None:
                raise NeedLogin(t("Your IEULearn session has expired."))
            roots = ("ovup-syl", "curriculum-item", "ov-ann", "ov-intro", "ov-ects")   # (a course's own words may say
            if under_maintenance(page) and not any(r in page for r in roots):      # "bakım": only a page without them)
                raise Busy(t("IEULearn isn't responding right now (it may be down for maintenance). Try again later."))
            raise changed(part, f"{problem} ({page_shape(page)})", where=course_id)
        health.ok("ieulearn", part, where=course_id)
        return value


def cover_file(url: str) -> Path:
    """Where a course's dashboard cover picture is cached (keyed by its URL, which carries a version)."""
    from .paths import app_dir

    return app_dir() / "cache" / "covers" / f"{hashlib.sha1(url.encode('utf-8')).hexdigest()[:16]}.jpg"


def fetch_cover(url: str) -> Optional[Path]:
    """Cache a cover picture (a public ~20 KB image) for the Home page; None if it isn't an image."""
    path = cover_file(url)
    if path.is_file():
        return path
    r = requests.get(url, timeout=20)
    if not r.ok or not r.headers.get("content-type", "").startswith("image/"):
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    tmp.write_bytes(r.content)
    tmp.replace(path)
    return path
