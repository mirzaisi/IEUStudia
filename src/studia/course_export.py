"""Export for an AI chat: what IEUStudia has of one course (or of every course) for the weeks a student picks, as one
zip to upload to ChatGPT. Inside, a folder per week (for all courses: per course, then per week) with the lecture
transcripts, the screens shown in class each with what was said while it was on screen, and the course files; beside
them the course's overview, its announcements and the quizzes, exams and deadlines IEUStudia knows, a README in the
app's language with a first message to send, and an index of every file. The files themselves are English, like
IEUStudia's other files (the AI reads them; the student reads the README).

The zip goes to the course's folder (``<course>/Exports``; ``Exports`` in the IEUStudia folder for all courses), written
as ``<name>.zip.tmp`` and renamed at the end: cancelled or failed, nothing is left behind, and an earlier zip of the same
name that is open in another program keeps its name while this one gets a new one. Course files not on this PC are
downloaded first through the app's own download (the caller's ``download``), or listed as missing. Names in the zip are
Windows-safe with their Turkish letters, two files of one name in one folder are kept apart, and every path is short
enough (ZIP_PATH_MAX) to unzip in Downloads within Windows' path limit."""

from __future__ import annotations

import dataclasses as dc
import datetime as dt
import logging
import os
import re
import shutil
import threading
import time
import unicodedata
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from . import store, views
from .cancel import Cancelled, check
from .domain import Course, Recording
from .i18n import date_long, date_short, day_month, join, num, t, tn
from .outputs.ai_copy import _noise, compact_day
from .outputs.common import MONTHS, WEEKDAYS_SHORT, format_duration, format_ts, kind_label, lang_tag, long_date
from .outputs.markdown import part_views
from .outputs.writer import course_dir, course_folder_name, fit_name, safe_name
from .sections import parse_section_tag

log = logging.getLogger("studia")

# A path inside the zip: unzipped by Explorer into Downloads (C:\Users\<name>\Downloads\<the zip's name>\, about 70
# characters) it stays within Windows' 259
ZIP_PATH_MAX = 180
ROOT_MAX, COURSE_MAX, WEEK_MAX, DAY_MAX = 44, 20, 60, 34
WEEK_MIN = 24                    # a week's folder keeps this much of its name however deep the zip is
# below a week's folder, its deepest file: Lectures/<day>/Screens/Part 9 - Screen 999.jpg
BELOW_WEEK = len("/Lectures/") + DAY_MAX + len("/Screens/") + len("Part 9 - Screen 999.jpg")
NAME_MIN = 16                    # the least of a file's own name that is kept (the rest of the path is fixed)
EXPORTS = "Exports"
GENERAL = "General course files"
OTHER_LECTURES = "Other lectures"
LINK_KINDS = {"link", "url", "video", "youtube", "page", "embed", "website"}
# already compressed: stored as they are (deflating them again costs time and saves nothing)
STORED = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".zip", ".rar", ".7z", ".gz", ".mp3", ".mp4", ".m4a",
          ".mov", ".webm", ".pptx", ".docx", ".xlsx", ".odp", ".odt", ".ods"}
TEXT_SHARE = 0.43                # a transcript's words are about this much of its file in the app folder
ZIP_TEXT = 3.3                   # Markdown is about this much smaller in the zip
CHATGPT_MAX = 512 * 1024 * 1024  # ChatGPT takes files up to 512 MB
SPACE_SPARE = 20 * 1024 * 1024   # free space left on the disk besides the zip
KEY_CATS = ("quiz", "midterm", "final", "exam", "project", "homework", "schedule")
CAT_NAMES = {"quiz": "Quizzes", "midterm": "Midterm", "final": "Final", "exam": "Exams", "project": "Projects",
             "homework": "Homework and deadlines", "schedule": "Schedule changes"}


class ExportError(RuntimeError):
    """Why the export can't be made, in words for the student."""


@dc.dataclass
class Choice:
    """What the student picked: the weeks (None: everything up to today) and what goes in."""

    weeks: Optional[list[int]] = None
    transcripts: bool = True
    screens: bool = True             # the screens as pictures, each with what was said while it was shown
    files: bool = True               # course files
    notices: bool = True             # announcements, quizzes, exams and deadlines
    download: bool = True            # course files not on this PC: downloaded first (else listed as missing)

    @classmethod
    def of(cls, d: Optional[dict[str, Any]]) -> "Choice":
        d = d if isinstance(d, dict) else {}
        weeks: Optional[list[int]] = None
        if isinstance(d.get("weeks"), (list, tuple)):
            picked = set()
            for w in d["weeks"]:
                try:
                    if 1 <= int(w) <= 60:
                        picked.add(int(w))
                except (TypeError, ValueError):
                    continue
            weeks = sorted(picked)
        return cls(weeks, **{k: bool(d.get(k, True)) for k in ("transcripts", "screens", "files", "notices",
                                                                 "download")})


@dc.dataclass
class Material:
    id: str
    title: str
    file_name: str
    ext: str
    url: str
    path: str = ""                   # on this PC ("": not downloaded)
    size: int = 0
    link: bool = False               # a link (a web page, a video), not a file
    pages: int = 0
    reason: str = ""                 # why it couldn't be downloaded
    students: bool = False           # it lists students by number (a class's grades or groups): not put in the zip

    @property
    def shown(self) -> str:
        return self.file_name or self.title or self.id


@dc.dataclass
class Day:
    key: str
    date: dt.date
    kind: str
    recs: list[Recording]
    done: list[str]                  # the parts with a transcript (delivery ids)
    todo: list[int]                  # your section's parts not transcribed yet (part numbers)
    parts: int                       # your section's parts
    duration_s: float
    other: list[str] = dc.field(default_factory=list)   # only another section's lecture: its section numbers
    another: bool = False            # ... (False: yours, or a course with one section)
    live: bool = False
    silent: list[int] = dc.field(default_factory=list)  # your parts with no speech on Panopto: nothing to transcribe
    _slides: Optional[dict[str, list[Any]]] = None

    @property
    def other_text(self) -> str:
        return f"Section {', '.join(self.other)}" if self.other else "Another section" if self.another else ""

    def slides(self) -> dict[str, list[Any]]:
        if self._slides is None:
            self._slides = {i: store.load_slides(i) for i in self.done}
        return self._slides

    def screens(self) -> list[str]:
        """The screens' pictures, each once (a slide shown twice is one picture)."""
        return list(dict.fromkeys(s.path for sl in self.slides().values() for s in sl if s.path))

    def screen_bytes(self) -> int:
        n = 0
        for p in self.screens():
            try:
                n += os.path.getsize(p)
            except OSError:
                pass
        return n

    def text_bytes(self) -> int:
        n = 0
        for i in self.done:
            try:
                n += int(store.transcript_path(i).stat().st_size * TEXT_SHARE)
            except OSError:
                pass
        return n


@dc.dataclass
class Week:
    no: int
    start: str
    end: str
    topic: str
    topic_en: str
    topic_tr: str
    units: list[str]
    readings: str
    exam: str
    current: bool
    now: bool                        # begun by today: in "Everything up to now"
    days: list[Day]
    files: list[Material]


@dc.dataclass
class Plan:
    course: Course
    detail: dict[str, Any]
    weeks: list[Week]                # the chosen ones
    all_weeks: list[Week]
    general: list[tuple[str, list[Material]]]
    loose: list[Day]                 # lectures in no week (a plan without dates): with Everything up to now


# ------------------------------------------------------------------ dates (English, for the files) and names
def _date(iso: str) -> Optional[dt.date]:
    try:
        return dt.date.fromisoformat((iso or "")[:10])
    except ValueError:
        return None


def _en_day(d: dt.date, year: bool = True) -> str:
    return f"{WEEKDAYS_SHORT[d.weekday()]} {d.day} {MONTHS[d.month - 1][:3]}" + (f" {d.year}" if year else "")


def _en_dm(iso: str) -> str:
    d = _date(iso)
    return f"{d.day} {MONTHS[d.month - 1][:3]}" if d else ""


def _en_range(start: str, end: str) -> str:
    a, b = _date(start), _date(end)
    if not a:
        return ""
    if not b or b == a:
        return _en_day(a)
    return f"{_en_day(a, year=a.year != b.year)} to {_en_day(b)}"


def _range(start: str, end: str) -> str:
    """The week's dates in the app's language (the README)."""
    a, b = _date(start), _date(end)
    if not a:
        return ""
    return f"{day_month(a)} – {day_month(b)}" if b and b != a else day_month(a)


def _pct(x: Any) -> str:
    try:
        return f"{float(x):g}%"
    except (TypeError, ValueError):
        return f"{x}%"


def clean_name(name: str, limit: int) -> str:
    """A file or folder name Windows takes, at most ``limit`` characters, with its Turkish letters; a long one is cut
    at a word when one is near enough."""
    n = unicodedata.normalize("NFC", str(name or ""))
    n = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    if len(n) > limit:
        cut = n[:limit]
        space = cut.rfind(" ")
        n = cut[:space] if space >= limit * 0.6 else cut
    return safe_name(n.rstrip(" .,;:-–—·&(+"), max(1, limit))


def split_name(file_name: str, ext: str = "") -> tuple[str, str]:
    """``("Week 1. Intro", "")`` / ``("Slides", ".pdf")``: the extension only when it is one, else the known one."""
    stem, e = os.path.splitext(file_name or "")
    if not re.fullmatch(r"\.[A-Za-z0-9]{1,10}", e or ""):
        stem, e = file_name or "", ""
    if not e and ext and re.fullmatch(r"[A-Za-z0-9]{1,10}", ext):
        e = "." + ext.lower()
    return stem.strip() or "file", e


class Names:
    """The names taken in each folder of the zip: a second file of one name (Windows doesn't tell case apart) gets
    " (2)", and every path (``prefix`` characters of the zip's root folder included) stays within ZIP_PATH_MAX."""

    def __init__(self, prefix: int = 0) -> None:
        self.prefix = prefix
        self._taken: dict[str, set[str]] = defaultdict(set)
        self._folders: dict[tuple[str, str], str] = {}

    def take(self, folder: str, stem: str, ext: str = "", limit: int = 120, keep: bool = False) -> str:
        """A file's name in ``folder`` (``keep``: never cut, for names that mean something whole: "Screen 12")."""
        room = limit if keep else max(NAME_MIN, min(limit, ZIP_PATH_MAX - self.prefix - len(folder) - 1 - len(ext)))
        cand = clean_name(stem, room)
        taken = self._taken[folder]
        n = 1
        while (cand + ext).casefold() in taken:
            n += 1
            suffix = f" ({n})"
            cand = clean_name(stem, max(1, room - len(suffix))) + suffix
        taken.add((cand + ext).casefold())
        return cand + ext

    def folder(self, parent: str, name: str, limit: int) -> str:
        """A folder's path (the same one each time it is asked for)."""
        key = (parent, name)
        if key not in self._folders:
            own = self.take(parent, name, "", limit)
            self._folders[key] = f"{parent}/{own}" if parent else own
        return self._folders[key]


# ------------------------------------------------------------------ what goes in
def _material(view: dict[str, Any], row: Optional[dict[str, Any]]) -> Material:
    row = row or {}
    local = row.get("local_path") or ""
    size = 0
    have = bool(local) and os.path.isfile(local)
    if have:
        try:
            size = os.path.getsize(local)
        except OSError:
            have = False
    kind = (view.get("kind") or row.get("kind") or "").lower()
    link = not have and (kind in LINK_KINDS or not (view.get("file_name") or view.get("ext")))
    return Material(str(view["id"]), view.get("title") or "", view.get("file_name") or "", view.get("ext") or "",
                    row.get("url") or "", local if have else "", size, link, int(view.get("pages") or 0))


def _day(view: dict[str, Any], recs: list[Recording], done: set[str],
         warnings: Optional[dict[str, list[dict[str, Any]]]] = None) -> Optional[Day]:
    """A class day for the export: your section's parts (transcribed or not), or another section's that you had
    transcribed anyway (then it says whose). None when it has neither. A part whose recording has no speech on
    Panopto (catchup.no_sound: no sound, or only noise or music) is not "to transcribe": trying again finds the same."""
    from .catchup import no_sound

    own = [r for r in view["recordings"] if r["part"] and r["mine"] is not False]
    have = [r.delivery_id for r in sorted(recs, key=lambda r: r.start) if r.delivery_id in done]
    if not own and not have:
        return None
    tags: list[str] = []
    if not own:
        found = {n for r in recs if r.delivery_id in done for n in re.findall(r"\d+", r.section_tag or parse_section_tag(r.name))}
        tags = sorted(found, key=int)
    by_id = {r.delivery_id: r for r in recs}
    silent = [r["part"] for r in own if r["id"] not in done and r["id"] in by_id
              and no_sound(by_id[r["id"]], (warnings or {}).get(r["id"], ()))]
    d = _date(view["date"]) or dt.date.today()
    return Day(view["day_key"], d, view["day_key"].split("|")[-1] or "lecture", recs, have,
               [r["part"] for r in own if r["id"] not in done and r["part"] not in silent], len(own),
               float(view.get("duration_s") or 0), tags, not own, bool(view.get("live")), silent)


def plan_course(lib: Any, settings: Any, course_id: str, choice: Choice, today: dt.date) -> Optional[Plan]:
    """What of this course goes in: the chosen weeks with their class days and course files, the course-wide files,
    and (Everything up to now) the lectures in no week."""
    course = lib.get_course(course_id)
    detail = views.course_detail(lib, settings, course_id) if course else None
    if course is None or detail is None:
        return None
    rows = {m["resource_id"]: m for m in lib.materials(course_id)}
    recs = lib.recordings(course_id)
    by_day: dict[str, list[Recording]] = defaultdict(list)
    for r in recs:
        by_day[r.day_key].append(r)
    warnings = lib.all_warnings()
    cv = views.course_view(course, recs, settings, {}, warnings, store.slide_ids())
    done = lib.transcribed_ids()
    days: dict[str, Day] = {}
    for v in cv["days"]:
        d = _day(v, by_day[v["day_key"]], done, warnings)
        if d is not None:
            days[v["day_key"]] = d
    placed: set[str] = set()
    weeks = []
    stamp = today.isoformat()
    for w in detail["weeks"]:
        placed |= {x["day_key"] for x in w["days"]}
        ds = sorted((days[x["day_key"]] for x in w["days"] if x["day_key"] in days), key=lambda d: (d.date, d.kind))
        files = [_material(m, rows.get(m["id"])) for u in w["units"] for m in u["materials"]]
        units = [u["title"] for u in w["units"] if u.get("title")]
        topic = (w["topic_en"] or " · ".join(units) or w["topic_tr"] or "").strip()
        begun = (w["start"] <= stamp) if w["start"] else bool(files or ds)
        weeks.append(Week(w["no"], w["start"], w["end"], topic, w["topic_en"], w["topic_tr"], units,
                          w.get("readings") or "", w.get("exam_label") or "", bool(w.get("current")), begun, ds, files))
    general = []
    for u in detail["general"]:
        mats = [_material(m, rows.get(m["id"])) for m in u["materials"]]
        if mats:                             # (files of a week that left the plan: the app's title is translated)
            general.append((u["title"] or "Course files" if u["id"] else "No longer in the weekly plan", mats))
    picked = set(choice.weeks) if choice.weeks is not None else None
    # Everything up to now: the weeks begun that have a lecture or a file (a week you picked is there in any case)
    chosen = [w for w in weeks if (w.no in picked if picked is not None else w.now and (w.days or w.files))]
    loose = sorted((d for k, d in days.items() if k not in placed and d.date <= today),
                   key=lambda d: (d.date, d.kind)) if picked is None else []
    return Plan(course, detail, chosen, weeks, general, loose)


# A course file that lists students by number (a lab's grades, the project groups) is classmates' data: it stays out of
# a zip meant for ChatGPT. Told by what it holds, not by its name ("Ders Notları" and "Lab results template" are study
# material): many different numbers of one length of 8 to 11 digits, as student numbers are (not the fractional part
# of a number, "3,14159265", and not binary, "10110010")
_STUDENT_NO = re.compile(r"(?<!\d)(?<!\d[.,])\d{8,11}(?!\d)")
STUDENTS_MIN = 20                # different ones of one length


def lists_students(texts: Iterable[str]) -> bool:
    """Is this the text of a list of students (their numbers with marks or groups)?"""
    seen: dict[int, set[str]] = defaultdict(set)
    for text in texts:
        for n in _STUDENT_NO.findall(text or ""):
            if not set(n) <= {"0", "1"}:
                seen[len(n)].add(n)
    return any(len(v) >= STUDENTS_MIN for v in seen.values())


def _cells(path: Path) -> list[str]:
    """The text of a course file whose text the app doesn't keep: an Excel workbook's cells, a CSV's fields."""
    ext = path.suffix.lower()
    if ext == ".xlsx":
        out: list[str] = []
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                sheet = info.filename == "xl/sharedStrings.xml" or re.fullmatch(r"xl/worksheets/sheet\d+\.xml",
                                                                               info.filename)
                if sheet and info.file_size < 64 * 1024 * 1024:
                    xml = z.read(info).decode("utf-8", "replace")
                    for v in re.findall(r"<(?:t|v)\b[^>]*>([^<]*)</(?:t|v)>", xml):
                        if re.fullmatch(r"\d(?:\.\d+)?E\+?\d+", v):          # 2.0210602014E10: a number Excel wrote so
                            v = f"{float(v):.0f}"
                        out.append(v)
        return out
    if ext in (".csv", ".tsv"):
        text = path.read_text(encoding="utf-8", errors="replace")[:20_000_000]
        sep = ";" if text.count(";") > text.count(",") else ","
        return re.split(rf"[{sep}\t|\n]", text)
    return []


def _lists_students(lib: Any, m: Material) -> bool:
    """Does this course file list students? From the text the app keeps of it (MaterialStore.index_text), else from
    the file itself: one not read yet, or a kind the app keeps no text of (an Excel workbook)."""
    from .materials import TEXT_KINDS, extract_pages

    status = (lib.get_material(m.id) or {}).get("text_status") or ""
    if status in ("no-text", "failed"):                  # a scan, or a file that can't be read
        return False
    path = Path(m.path)
    try:
        if status == "done":
            texts = lib.material_pages(m.id)
        elif path.suffix.lower().lstrip(".") in TEXT_KINDS:
            texts = extract_pages(path)
        else:
            texts = _cells(path)
        return lists_students(texts)
    except Exception as exc:  # noqa: BLE001 - a file that can't be read is put in as before
        log.warning("Export: %s could not be read to check for a list of students: %s", m.id, exc)
        return False


def _leave_out_lists(lib: Any, plans: list["Plan"]) -> None:
    """Mark the chosen course files that list students (each file read once: a unit for two weeks is in both)."""
    found: dict[str, bool] = {}
    for p in plans:
        for m in _wanted(p):
            if m.path and not m.link:
                if m.id not in found:
                    found[m.id] = _lists_students(lib, m)
                m.students = found[m.id]


def export_courses(lib: Any, course_id: str) -> list[Course]:
    """The courses an export holds: this one, or (``""``) every course on your IEULearn dashboard."""
    if course_id:
        c = lib.get_course(course_id)
        return [c] if c else []
    return [c for c in lib.courses() if c.code or c.name]


def _wanted(plan: Plan) -> list[Material]:
    """The course files of the chosen weeks and of the whole course (a file of a unit for two weeks: in both)."""
    return [m for w in plan.weeks for m in w.files] + [m for _, ms in plan.general for m in ms]


def _days(plan: Plan) -> list[Day]:
    return [d for w in plan.weeks for d in w.days] + plan.loose


def export_folder(output_root: Path, lib: Any, course_id: str) -> Path:
    """Where the zip goes: the course's folder, or the IEUStudia folder for all courses."""
    if course_id:
        course = lib.get_course(course_id)
        if course is not None:
            return course_dir(output_root, course, lib.courses(True)) / EXPORTS
    return Path(output_root) / EXPORTS


# ------------------------------------------------------------------ the dialog's options and the estimate
def _week_numbers(plan: Plan) -> dict[int, dict[str, Any]]:
    out = {}
    for w in plan.all_weeks:
        files = [m for m in w.files if not m.link]
        shots = sum(len(d.screens()) for d in w.days)
        text = sum(d.text_bytes() for d in w.days)
        out[w.no] = {
            "no": w.no, "start": w.start, "end": w.end, "topic": w.topic, "current": w.current, "now": w.now,
            "lectures": sum(1 for d in w.days if d.done), "untranscribed": sum(1 for d in w.days if d.todo),
            "days": len(w.days), "files": sum(1 for m in files if m.path), "missing": [m.id for m in files if not m.path],
            "links": sum(1 for m in w.files if m.link), "screens": shots,
            "bytes": {"text": int(text * 1.12 / ZIP_TEXT) + (600 if w.days else 0),
                      "screen_text": int(text * 1.2 / ZIP_TEXT) if shots else 0,
                      "screens": sum(d.screen_bytes() for d in w.days),
                      "files": sum(m.size for m in files if m.path)}}
    return out


def options(lib: Any, settings: Any, output_root: Path, course_id: str = "",
            today: Optional[dt.date] = None) -> dict[str, Any]:
    """What the export dialog offers: the weeks (number, dates, topic; what each has and about how much it adds to the
    zip), the course-wide files, where the zip goes and the free space there. All courses: the weeks by number."""
    today = today or dt.date.today()
    courses = export_courses(lib, course_id)
    if course_id and not courses:
        raise ExportError(t("This course is no longer in your list."))
    plans = [p for p in (plan_course(lib, settings, c.id, Choice(), today) for c in courses) if p]
    weeks: dict[int, dict[str, Any]] = {}
    for p in plans:
        for no, w in _week_numbers(p).items():
            e = weeks.get(no)
            if e is None:
                e = weeks[no] = {**w, "courses": [], "bytes": dict(w["bytes"]), "missing": list(w["missing"])}
                if not course_id:
                    e["topic"] = ""
            else:
                starts = [x for x in (e["start"], w["start"]) if x]
                e["start"], e["end"] = (min(starts) if starts else ""), max(e["end"], w["end"])
                for k in ("lectures", "untranscribed", "days", "files", "links", "screens"):
                    e[k] += w[k]
                for k in e["bytes"]:
                    e["bytes"][k] += w["bytes"][k]
                e["missing"] += w["missing"]
                e["now"], e["current"] = e["now"] or w["now"], e["current"] or w["current"]
            if w["days"] or w["files"] or w["missing"]:
                e["courses"].append(p.course.code or p.course.name)
    gen = [m for p in plans for _, ms in p.general for m in ms if not m.link]
    folder = export_folder(output_root, lib, course_id)
    try:
        free = shutil.disk_usage(_existing(folder)).free
    except OSError:
        free = 0
    return {"courses": [{"id": c.id, "code": c.code, "name": c.name} for c in courses], "all": not course_id,
            "today": today.isoformat(), "weeks": [weeks[n] for n in sorted(weeks)],
            "general": {"files": sum(1 for m in gen if m.path), "missing": [m.id for m in gen if not m.path],
                        "bytes": sum(m.size for m in gen if m.path)},
            "loose": sum(len(p.loose) for p in plans),
            "base": sum(2000 + len(p.detail.get("description") or "") // 2 for p in plans),
            "notices": sum(2000 + int(sum(len(a.get("body") or "") for a in p.detail["announcements"]) / 2.5)
                           for p in plans),
            "folder": str(folder), "free": free, "chatgpt_max": CHATGPT_MAX}


def _existing(path: Path) -> Path:
    """The nearest folder of ``path`` that exists (the disk's free space is asked of it)."""
    p = Path(path)
    while not p.exists() and p.parent != p:
        p = p.parent
    return p


def estimate(plans: list[Plan], choice: Choice) -> tuple[int, int]:
    """About how big the zip gets, and how many bytes it reads to make it (for the progress)."""
    size = work = 0
    for p in plans:
        size += 3000 + len(p.detail.get("description") or "") // 2
        if choice.notices:
            size += 2000 + int(sum(len(a.get("body") or "") for a in p.detail["announcements"]) / 2.5)
        for d in _days(p):
            text = d.text_bytes()
            if choice.transcripts:
                size += int(text * 1.12 / ZIP_TEXT)
                work += int(text * 1.12)
            if choice.screens:
                pics = d.screen_bytes()
                size += int(text * 1.2 / ZIP_TEXT) + pics
                work += int(text * 1.2) + pics
        if choice.files:
            files = sum(m.size for m in _wanted(p) if m.path and not m.students)
            size += files
            work += files
        size += 1500 * (len(p.weeks) + 2)
    return size, work + 1


def size_text(n: float) -> str:
    """``85.2 MB`` / ``85,2 MB``."""
    mb = n / 1048576
    if mb >= 1024:
        return f"{num(mb / 1024, 1, False)} GB"
    if mb >= 10:
        return f"{num(mb, 0, False)} MB"
    if mb >= 1:
        return f"{num(mb, 1, False)} MB"
    return f"{max(1, round(n / 1024))} KB"


# ------------------------------------------------------------------ course files not on this PC
def _download(plans: list[Plan], choice: Choice, download: Optional[Callable[..., Any]],
              progress: Callable[[dict[str, Any]], None], cancel: Optional[threading.Event]) -> None:
    """Download the chosen course files that aren't on this PC (each once: a file of a unit for two weeks is in
    both); one that fails says why in the README. With no connection the rest wait no longer: they are listed."""
    from .net import NetworkError, Offline, plain_error

    copies: dict[str, list[Material]] = defaultdict(list)
    for p in plans:
        for m in _wanted(p):
            if not m.path and not m.link:
                copies[m.id].append(m)
    if not copies or not choice.download or download is None:
        return
    todo = list(copies)
    stopped = ""
    for i, rid in enumerate(todo, start=1):
        check(cancel)
        ms = copies[rid]
        if stopped:
            for m in ms:
                m.reason = stopped
            continue
        name = ms[0].shown
        progress({"step": "download", "i": i, "n": len(todo), "name": name, "done": 0, "total": 0,
                  "fraction": round((i - 1) / len(todo), 3)})

        def bytes_in(done: int, total: int, i: int = i, name: str = name) -> None:
            part = done / total if total else 0.0
            progress({"step": "download", "i": i, "n": len(todo), "name": name, "done": done, "total": total,
                      "fraction": round((i - 1 + min(1.0, part)) / len(todo), 3), "quiet": True})

        try:
            path = download(rid, bytes_in)
        except Cancelled:
            raise
        except (NetworkError, Offline) as exc:          # no connection: the rest would wait the same way
            stopped = plain_error(exc)
            for m in ms:
                m.reason = stopped
            log.warning("Export: the course files stopped downloading: %s", exc)
            continue
        except Exception as exc:  # noqa: BLE001 - that file is listed as missing, with why
            log.warning("Export: %s not downloaded: %s", rid, exc)
            for m in ms:
                m.reason = plain_error(exc) if str(exc) else t("Download failed")
            continue
        if path and os.path.isfile(str(path)):
            size = os.path.getsize(str(path))
            for m in ms:
                m.path, m.size, m.link = str(path), size, False


# ------------------------------------------------------------------ the zip
def _replace(src: Path, dst: Path) -> None:
    os.replace(src, dst)


def _place(tmp: Path, target: Path) -> Path:
    """The finished zip takes its name; one of that name open in another program (Windows won't replace it) keeps it,
    and this one is called "<name> (2).zip" (or the next number that is free)."""
    for n in range(1, 50):
        dest = target if n == 1 else target.with_name(f"{target.stem} ({n}){target.suffix}")
        try:
            _replace(tmp, dest)
            return dest
        except PermissionError:
            log.info("Export: %s is in use, trying another name", dest.name)
    raise ExportError(t("The zip couldn't be saved: Windows didn't let IEUStudia write in {folder}. Close the program "
                        "that has the earlier exports open, then try again.", folder=str(target.parent)))


def _remove(path: Path) -> None:
    for _ in range(8):
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:                  # an antivirus scanning it for a moment
            time.sleep(0.25)
        except OSError:
            break
    log.warning("Export: %s could not be removed", path)


class Zip:
    """The zip being written: entries under its root folder, names taken per folder, every file in the index, and the
    progress (by the bytes read of the ``work`` it will read)."""

    def __init__(self, path: Path, root: str, work: int, progress: Callable[[dict[str, Any]], None],
                 cancel: Optional[threading.Event]) -> None:
        self.path, self.root = path, root
        self.z = zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True,
                                 strict_timestamps=False)
        self.names = Names(len(root) + 1)
        self.index: list[tuple[str, str]] = []          # (path below the root, what it is)
        self.work, self.done = max(1, work), 0
        self.progress, self.cancel = progress, cancel
        self.state: dict[str, Any] = {}
        self._said = 0.0

    def tell(self, force: bool = False, **state: Any) -> None:
        if state:
            force = force or state.get("step") != self.state.get("step")
            self.state = {**self.state, **state}
        now = time.monotonic()
        if force or now - self._said >= 0.12:
            self._said = now
            self.progress({**self.state, "fraction": round(min(0.99, self.done / self.work), 3)})

    def folder(self, parent: str, name: str, limit: int) -> str:
        return self.names.folder(parent, name, limit)

    def text(self, folder: str, name: str, text: str, what: str) -> str:
        check(self.cancel)
        stem, ext = os.path.splitext(name)
        own = self.names.take(folder, stem, ext, keep=True)
        rel = f"{folder}/{own}" if folder else own
        data = (text.rstrip() + "\n").encode("utf-8")
        self.z.writestr(f"{self.root}/{rel}", data, compress_type=zipfile.ZIP_DEFLATED)
        self.index.append((rel, what))
        self.done += len(data)
        self.tell()
        return own

    def file(self, folder: str, stem: str, ext: str, src: str, what: str, keep: bool = False) -> Optional[str]:
        """A file from this PC under its name in the zip; None when it is gone (moved or deleted since)."""
        check(self.cancel)
        try:
            size = os.path.getsize(src)
        except OSError:
            return None
        own = self.names.take(folder, stem, ext, keep=keep)
        rel = f"{folder}/{own}" if folder else own
        try:
            self.z.write(src, f"{self.root}/{rel}",
                         compress_type=zipfile.ZIP_STORED if ext.lower() in STORED else zipfile.ZIP_DEFLATED)
        except FileNotFoundError:
            return None
        self.index.append((rel, what))
        self.done += size
        self.tell()
        return own

    def close(self) -> None:
        self.z.close()

    def abort(self) -> None:
        try:
            self.z.close()
        except Exception:  # noqa: BLE001 - a zip that failed halfway: closed as far as it goes, then removed
            log.debug("closing the unfinished zip failed", exc_info=True)


# ------------------------------------------------------------------ the files' text (English)
def _line(para: Any, slide: bool) -> str:
    tag = lang_tag(para.lang)
    marks = (f"[{tag}] " if tag else "") + ("⭐ " if para.star else "") + ("⚠ " if para.uncertain else "")
    shown = f"(slide {para.slide}) " if slide and para.slide is not None else ""
    return f"[{format_ts(para.start)}] {shown}{marks}{para.text}"


def _legend(screens: bool) -> str:
    out = ["---", "How to read this: times are [mm:ss] from the start of each part (h:mm:ss after an hour)."]
    if screens:
        out.append("(slide N) is the screen that was shown at that moment: its picture is Screens/Part P - Screen NN "
                   "(P is the part), and Transcript by screen.md has what was said while each screen was shown.")
    out.append("[TR] marks a passage in Turkish, ⭐ what the lecturer stressed, ⚠ a passage the speech recognition was "
               "unsure of. The transcript is automatic, so now and then a word is wrong.")
    return "\n".join(out)


def _screen_stem(part: int, index: int) -> str:
    return f"Part {part} - Screen {index:02d}"


def by_screen(course: Course, day: Day, trans: dict[str, Any], shots: dict[str, list[Any]],
              names: dict[str, str]) -> str:
    """What was said while each screen was shown, screen by screen (a screen shown again: each time on its own)."""
    recs = sorted(day.recs, key=lambda r: r.start)
    lines = [f"# {course.code or course.name} · {long_date(day.date)} · {kind_label(day.kind)} · what was said on "
             "each screen", "",
             "Each section is one screen (a slide or the board) as it was shown in class: its picture in the Screens "
             "folder, then what the lecturer said while it was on screen. Times are from the start of the part.", ""]
    for p in part_views(recs, trans, shots):
        if not p.transcript:
            continue
        sl = list(shots.get(p.rec.delivery_id) or [])
        # a screen shown again right after itself (the capture split one showing in two) is one showing
        shows: list[list[Any]] = []                # [first slide, end, its position in sl…]
        owner: list[int] = []
        for k, s in enumerate(sl):
            if shows and shows[-1][0].index == s.index and shows[-1][0].path == s.path:
                shows[-1][1] = max(shows[-1][1], s.end)
            else:
                shows.append([s, s.end])
            owner.append(len(shows) - 1)
        before: list[str] = []
        during: dict[int, list[str]] = defaultdict(list)
        k = 0
        for para in p.paragraphs:                  # each passage under the screen up when it began (as part_views)
            if _noise(para):
                continue
            while k + 1 < len(sl) and sl[k + 1].t <= para.start + 0.5:
                k += 1
            if sl and sl[k].t <= para.start + 0.5:
                during[owner[k]].append(_line(para, slide=False))
            else:
                before.append(_line(para, slide=False))
        if not sl:
            lines += [f"## Part {p.index} (no screens were captured)", ""] + (before or ["(nothing was said)"]) + [""]
            continue
        if before:
            lines += [f"## Part {p.index}, before the first screen", ""] + before + [""]
        seen: set[int] = set()
        for i, (s, end) in enumerate(shows):
            again = s.index in seen
            seen.add(s.index)
            name = names.get(s.path, "")
            lines += [f"## Part {p.index}, screen {s.index} ({format_ts(s.t)} to {format_ts(end)})"
                      + (", shown again" if again else ""),
                      f"Picture: Screens/{name}" if name else "Picture: (not in this export)", ""]
            lines += (during.get(i) or ["(nothing was said while it was shown)"]) + [""]
    return "\n".join(lines)


def _lecture_line(day: Day, rel: str, screens: int, choice: Choice) -> str:
    what = f"{_en_day(day.date, year=False)} · {kind_label(day.kind)}"
    if day.parts > 1:
        what += f" · {day.parts} parts"
    if day.duration_s:
        what += f" · {format_duration(day.duration_s)}"
    if day.another:
        what += f" · {day.other_text}'s lecture"
    if not day.done:
        return f"- {what}: " + ("still being recorded on Panopto, so not transcribed yet." if day.live
                                else "not transcribed in IEUStudia yet." if day.todo or not day.silent
                                else _no_speech(len(day.silent)))
    notes = ([f"Not transcribed yet: part{'s' if len(day.todo) > 1 else ''} {', '.join(map(str, day.todo))}."]
             if day.todo else []) + ([_silent_parts(day.silent) + "."] if day.silent else [])
    if not rel:
        return " ".join([f"- {what}: transcribed (the transcripts were left out of this export).", *notes])
    bits = (["Transcript.md"] if choice.transcripts else []) + (
        ["Transcript by screen.md", f"{screens} screens"] if screens else [])
    return f"- {what}: {rel}/ ({', '.join(bits)})" + (". " + " ".join(notes) if notes else "")


def _no_speech(parts: int) -> str:
    """A day whose recordings have no speech on Panopto (catchup.no_sound): Panopto has no sound for them, or only
    noise or music (the microphone was off, or it recorded only the room), so there is no transcript, and trying again
    finds the same."""
    return (f"Panopto's recording{'s' if parts > 1 else ''} of it {'have' if parts > 1 else 'has'} no speech (no sound, "
            "or only noise or music), so there is no transcript; use the week's course files for it.")


def _silent_parts(parts: list[int]) -> str:
    return (f"Part{'s' if len(parts) > 1 else ''} {', '.join(map(str, parts))} "
            f"{'have' if len(parts) > 1 else 'has'} no speech on Panopto's recording, so no transcript")


def _material_line(m: Material, rel: Optional[str], choice: Choice) -> str:
    if m.link:
        return f"- Link (not a file): {m.title or m.shown}" + (f" <{m.url}>" if m.url else "")
    if rel:
        return f"- {rel}" + (f" ({m.pages} page{'s' if m.pages > 1 else ''})" if m.pages else "")
    if not choice.files:
        return f"- {m.shown} (course files were left out of this export)"
    if m.students:
        return f"- {m.shown}: left out of this zip, as it lists students' numbers (a class's grades or groups)."
    if m.reason:
        return f"- {m.shown}: couldn't be downloaded ({m.reason.rstrip('.')}), so it isn't in this zip."
    return f"- {m.shown}: not downloaded in IEUStudia, so it isn't in this zip."


def _outcomes(detail: dict[str, Any]) -> list[str]:
    return list(detail.get("outcomes_en") or detail.get("outcomes_tr") or [])


def _other_topic(w: Week) -> str:
    """The week's topic in IEULearn's course overview when it says something else than the syllabus' (it is in the
    language it was typed in there, often English too: not a translation); "" when it is the same words."""
    same = lambda s: re.sub(r"[\W_]+", "", (s or "").casefold())     # noqa: E731 - case, punctuation and spaces aside
    other = (w.topic_tr or "").strip()
    return other if other and same(other) != same(w.topic) else ""


def course_title(course: Course, detail: dict[str, Any]) -> str:
    english = ((detail.get("syllabus") or {}).get("name") or "").strip()
    return f"{course.code or course.name} · {english or course.name or course.title}"


def course_overview(course: Course, detail: dict[str, Any], plan: Plan) -> str:
    syl = detail.get("syllabus") or {}
    lines = [f"# {course_title(course, detail)}", ""]
    if course.name and course.name not in lines[0]:
        lines.append(f"Name on IEULearn: {course.name}")
    for label, value in (("Lecturer", course.instructor), ("Lecturers in the syllabus", syl.get("lecturers")),
                         ("Assistants", syl.get("assistants")), ("Coordinator", syl.get("coordinator")),
                         ("Term", course.term), ("Section", course.section), ("Language", syl.get("language")),
                         ("ECTS", syl.get("ects")), ("Prerequisites", syl.get("prerequisites"))):
        value = re.sub(r"\s*\n\s*", ", ", str(value or "")).strip(" ,")
        if value and value != "-":
            lines.append(f"{label}: {value}")
    if detail.get("description"):
        lines += ["", "## Description", detail["description"].strip()]
    if detail.get("objectives"):
        lines += ["", "## Objectives", detail["objectives"].strip()]
    outs = _outcomes(detail)
    if outs:
        lines += ["", "## Learning outcomes (LO)"] + [f"- LO{i}: {o}" for i, o in enumerate(outs, start=1)]
    if detail.get("grading"):
        lines += ["", "## Grading"]
        for g in detail["grading"]:
            n = int(g.get("number") or 1)
            lines.append(f"- {g.get('activity')}: {_pct(g.get('weight'))}" + (f" in all ({n} of them)" if n > 1 else ""))
    for key, label in (("textbooks", "Textbooks"), ("readings", "Suggested readings")):
        if (syl.get(key) or "").strip():
            lines += ["", f"## {label}", syl[key].strip()]
    chosen = {w.no for w in plan.weeks}
    asgs = assignments(detail)
    if plan.all_weeks:
        lines += ["", "## Weekly plan", "", "(* = in this export)"]
        for w in plan.all_weeks:
            when = _en_range(w.start, w.end)
            bits = [f"Week {w.no}" + (f" ({when})" if when else ""), w.topic or "(no topic given)"]
            if _other_topic(w):
                bits.append(f"on IEULearn: {_other_topic(w)}")
            if w.exam:
                bits.append(f"exam week: {w.exam}")
            due = [f"{a.get('title') or 'Assignment'} ({_asg_when(a, year=False)})" for a in asgs if a.get("week") == w.no]
            if due:
                bits.append("assignment due: " + "; ".join(due))
            lines.append(f"- {'* ' if w.no in chosen else ''}{' · '.join(bits)}")
    weekless = [a for a in asgs if not a.get("week") or a["week"] not in {w.no for w in plan.all_weeks}]
    if weekless:                    # (a plan without dates: the assignments are listed on their own)
        lines += ["", "## Assignments on IEULearn", ""] + [
            f"- {a.get('title') or 'Assignment'}: due {_asg_when(a)}" for a in weekless]
    return "\n".join(lines)


def announcements_md(course: Course, detail: dict[str, Any]) -> str:
    anns = sorted(detail.get("announcements") or [], key=lambda a: a.get("posted_at") or "", reverse=True)
    lines = [f"# {course.code or course.name} · announcements on IEULearn", "", "Newest first.", ""]
    for a in anns:
        when = ""
        if a.get("posted_at"):
            try:
                at = dt.datetime.fromisoformat(a["posted_at"])
                when = f"{_en_day(at.date())}, {at:%H:%M}"
            except ValueError:
                when = a["posted_at"]
        lines += [f"## {a.get('title') or '(no title)'}", " · ".join(x for x in (when, a.get("author") or "") if x), "",
                  (a.get("body") or "").strip() or "(no text)", ""]
    return "\n".join(lines)


def assignments(detail: dict[str, Any]) -> list[dict[str, Any]]:
    """IEULearn's assignments that have a deadline (views.assignment_view), soonest first."""
    return sorted((a for a in detail.get("assignments") or [] if a.get("due")),
                  key=lambda a: (a["due"], a.get("time") or "", (a.get("title") or "").casefold()))


def _asg_when(a: dict[str, Any], year: bool = True) -> str:
    d = _date(a["due"])
    return (_en_day(d, year) if d else a["due"]) + (f", {a['time']}" if a.get("time") else "")


def _asg_sent(a: dict[str, Any], today: dt.date) -> str:
    """Whether it was sent, as IEUStudia last saw it (English, for the files)."""
    if a.get("done"):
        return "graded" if a.get("state") == "graded" else "sent"
    return "not sent yet" if a["due"] >= today.isoformat() else "not sent"


def assignment_line(a: dict[str, Any], today: dt.date) -> str:
    """One assignment for the files: "- Lab 1: due Wed 21 Oct 2026, 15:50 (week 5, over). Graded."."""
    stamp = today.isoformat()
    when = "over" if a["due"] < stamp else "due today" if a["due"] == stamp else "still ahead"
    return (f"- {a.get('title') or 'Assignment'}: due {_asg_when(a)} ("
            + (f"week {a['week']}, " if a.get("week") else "") + f"{when}). {_asg_sent(a, today).capitalize()}.")


def deadlines_md(course: Course, detail: dict[str, Any], today: dt.date) -> Optional[str]:
    """The quizzes, exams, projects, homework and schedule changes IEUStudia knows: the assessments announced (which
    week, which learning outcomes), IEULearn's assignments with their deadlines, the syllabus' exam weeks, and what was
    said about them in class and in the announcements. None when there is nothing."""
    alerts = detail.get("alerts") or {}
    plan = alerts.get("plan") or []
    weeks = {w["no"]: w for w in detail.get("weeks") or []}
    outs = _outcomes(detail)
    items = [a for a in alerts.get("items") or [] if a.get("category") in KEY_CATS]
    exam_weeks = [w for w in detail.get("weeks") or [] if w.get("exam")
                  and not any(e.get("week") == w["no"] and e.get("kind") != "quiz" for e in plan)]
    asgs = assignments(detail)
    if not (plan or items or exam_weeks or asgs):
        return None
    stamp = today.isoformat()
    lines = [f"# {course.code or course.name} · quizzes, exams, homework and deadlines", "",
             "What IEUStudia found in the course's announcements and in what the lecturer said in class (by words like "
             "\"quiz\" or \"ara sınav\", not by an AI). Check IEULearn for the exact days and rooms.", ""]
    if plan:
        lines += ["## Announced assessments", ""]
        for e in plan:
            w = weeks.get(e.get("week") or 0)
            line = f"- {e.get('name') or str(e.get('kind', '')).title()}"
            if e.get("week"):
                line += f": week {e['week']}"
                if w and w.get("start"):
                    state = ("over" if (w["end"] or w["start"]) < stamp else "this week" if w["start"] <= stamp
                             else "still ahead")
                    line += f" ({_en_range(w['start'], w['end'])}, {state})"
            else:
                line += ": the week was not announced"
            if e.get("los"):
                line += ". It covers " + ", ".join(f"LO{n}" for n in e["los"])
            src = (e.get("sources") or [{}])[0]
            if src.get("title"):
                d = _date(src.get("date") or "")
                line += f". As announced in “{src['title']}”" + (f" ({_en_day(d)})" if d else "")
            lines.append(line + ".")
            lines += [f"  - LO{n}: {outs[n - 1]}" for n in e.get("los") or [] if 1 <= n <= len(outs)]
        lines.append("")
    if asgs:
        lines += ["## Assignments on IEULearn", "",
                  "Each with the deadline IEULearn gives and whether it was sent, as IEUStudia last saw it (check "
                  "IEULearn before a deadline).", ""]
        lines += [assignment_line(a, today) for a in asgs]
        lines.append("")
    if exam_weeks:
        lines += ["## Exam weeks in the syllabus", ""]
        for w in exam_weeks:
            when = _en_range(w["start"], w["end"])
            lines.append(f"- Week {w['no']}" + (f" ({when})" if when else "") + f": {w.get('exam_label') or 'Exam'}")
        lines.append("")
    if items:
        lines += ["## What was said about them", "",
                  "Newest first. \"In class\" comes from the lecture transcripts (the part and the time point to the "
                  "moment in that lecture's Transcript.md).", ""]
        by_cat: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for it in items:
            by_cat[it["category"]].append(it)
        for cat in KEY_CATS:
            if not by_cat.get(cat):
                continue
            lines += [f"### {CAT_NAMES[cat]}", ""]
            for it in by_cat[cat]:
                d = _date(it.get("date") or "")
                when = _en_day(d) if d else ""
                if it.get("source") == "lecture":
                    kind = (it.get("day_key") or "lecture").split("|")[-1]
                    where = f"in class ({kind_label(kind).lower()}" + (f", part {it['part']}" if it.get("part") else "") \
                        + (f", {it['ts']}" if it.get("ts") else "") + ")"
                    if it.get("other_section_en"):
                        where += f", heard in {it['other_section_en']}'s lecture (the dates may differ for your section)"
                else:
                    where = f"announcement “{it.get('title') or ''}”"
                text = re.sub(r"\s+", " ", it.get("text") or "").strip()
                lines.append(f"- {when}, {where}: “{text}”")
            lines.append("")
    return "\n".join(lines)


def week_overview(course: Course, w: Week, plan: Plan, lectures: list[str], files: list[str], choice: Choice,
                  today: Optional[dt.date] = None) -> str:
    today = today or dt.date.today()
    lines = [f"# Week {w.no}" + (f": {w.topic}" if w.topic else ""), "", f"Course: {course_title(course, plan.detail)}"]
    when = _en_range(w.start, w.end)
    if when:
        lines.append(f"Dates: {when}")
    if _other_topic(w):
        lines.append(f"Topic on IEULearn: {_other_topic(w)}")
    units = [u for u in w.units if u and u != w.topic]
    if units:
        lines.append(f"IEULearn unit: {' · '.join(units)}")
    if w.exam:
        lines.append(f"Exam week: {w.exam}")
    due = [e for e in (plan.detail.get("alerts") or {}).get("plan") or [] if e.get("week") == w.no]
    if due:
        lines.append("Announced for this week: " + ", ".join(
            str(e.get("name") or "") + (f" (it covers {', '.join(f'LO{n}' for n in e['los'])})" if e.get("los") else "")
            for e in due))
    asgs = [a for a in assignments(plan.detail) if a.get("week") == w.no]
    if asgs:
        lines.append("Assignments due this week (IEULearn): " + "; ".join(
            f"{a.get('title') or 'Assignment'} (due {_asg_when(a, year=False)}, {_asg_sent(a, today)})" for a in asgs))
    if w.readings:
        lines += ["", f"Readings: {w.readings.strip()}"]
    lines += ["", "## Lectures", "", *(lectures or ["- No lectures were recorded this week."])]
    lines += ["", "## Course files", "", *(files or ["- No course files for this week."])]
    if choice.notices and w.start:
        anns = [a for a in plan.detail.get("announcements") or []
                if a.get("posted_at") and w.start <= a["posted_at"][:10] <= (w.end or w.start)]
        if anns:
            lines += ["", "## Announcements this week", "", "(the whole text is in Announcements.md)"]
            lines += [f"- {_en_dm(a['posted_at'])}: {a.get('title') or '(no title)'}"
                      for a in sorted(anns, key=lambda a: a["posted_at"])]
    return "\n".join(lines)


# ------------------------------------------------------------------ names of the export, the README and the index
def _runs(nos: Iterable[int], dash: str = "–") -> str:
    runs: list[list[int]] = []
    for n in sorted(set(nos)):
        if runs and n == runs[-1][1] + 1:
            runs[-1][1] = n
        else:
            runs.append([n, n])
    return ", ".join(str(a) if a == b else f"{a}{dash}{b}" for a, b in runs)


def span_name(choice: Choice, today: dt.date, every: Iterable[int] = ()) -> str:
    """The export's span in its file name (English, like IEUStudia's other files)."""
    stamp = f"{today.day} {MONTHS[today.month - 1][:3]} {today.year}"
    if choice.weeks is None:
        return f"everything up to {stamp}"
    nos, every = sorted(set(choice.weeks)), set(every)
    if every and every <= set(nos):
        return f"all weeks - {stamp}"
    return f"{'week' if len(nos) == 1 else 'weeks'} {_runs(nos, '-')} - {stamp}"


def span_text(choice: Choice, today: dt.date) -> str:
    """The span in the app's language (the README)."""
    if choice.weeks is None:
        return t("everything up to {date}", date=f"{day_month(today, long=True)} {today.year}")
    nos = sorted(set(choice.weeks))
    return t("week {n}", n=nos[0]) if len(nos) == 1 else t("weeks {weeks}", weeks=_runs(nos))


def first_message(courses: list[Course], choice: Choice, all_courses: Optional[list[Course]] = None,
                  every: Optional[bool] = None) -> str:
    """The first message to send with the zip, in the app's language (``every``: all courses' zip, a folder for each
    course, even with one course on the dashboard)."""
    every = len(courses) > 1 if every is None else every
    parts = []
    if choice.transcripts:
        parts.append(t("the lecture transcripts (with times)"))
    if choice.screens:
        parts.append(t("the screens shown in class (each with what was said while it was on screen)"))
    if choice.files:
        parts.append(t("the course files"))
    inside = join(parts) if parts else t("an overview of the week")
    if not every:
        head = t("This zip holds my {course} course, exported from IEUStudia: a folder for each week, with {inside}.",
                 course=courses[0].code or courses[0].name, inside=inside)
        tail = t("README.md and Index of files.md list everything. Unzip it and read the files before you answer. When "
                 "you explain something, say which week and lecture it comes from, with the time. My first question:")
    else:
        head = t("This zip holds my courses ({courses}), exported from IEUStudia: a folder for each course, and in it a "
                 "folder for each week, with {inside}.",
                 courses=", ".join(course_folder_name(c, all_courses or courses) for c in courses), inside=inside)
        tail = t("README.md and Index of files.md list everything. Unzip it and read the files before you answer. When "
                 "you explain something, say which course, week and lecture it comes from, with the time. My first "
                 "question:")
    if choice.notices:
        head += " " + t("The announcements and the quizzes, exams and deadlines are in it too.")
    return f"{head} {tail}"


def _how_to(message: str) -> list[str]:
    return ["## " + t("Use it in ChatGPT"), "",
            "1. " + t("Open ChatGPT (chatgpt.com or the app) and start a new chat."),
            "2. " + t("Drag this zip into the chat, or click + and pick it. There is no need to unzip it."),
            "3. " + t("Send this as your first message, with your question at the end:"), "",
            "> " + message, "",
            t("ChatGPT takes files up to 512 MB. For a bigger zip, export fewer weeks or leave out the screens."), ""]


@dc.dataclass
class Notes:
    """What isn't in the zip, for the README: lectures not transcribed yet, course files not downloaded (or that
    couldn't be), and the links (listed in their week, not files)."""

    lectures: list[str] = dc.field(default_factory=list)
    files: list[str] = dc.field(default_factory=list)
    links: set[str] = dc.field(default_factory=set)
    untranscribed: int = 0
    missing: set[str] = dc.field(default_factory=set)
    students: set[str] = dc.field(default_factory=set)      # files left out: lists of students


def course_readme(w: "_CourseWriter", choice: Choice, today: dt.date, message: str, alone: bool) -> str:
    p, counts, notes = w.plan, w.counts, w.notes
    lines = [f"# {course_title(p.course, p.detail)}: {span_text(choice, today)}", "",
             t("Made with IEUStudia on {date}.", date=date_long(today)) + " " + t(
                 "Inside: {lectures}, {screens} and {files}.",
                 lectures=tn(counts["lectures"], "{n} transcribed lecture", "{n} transcribed lectures"),
                 screens=tn(counts["screens"], "{n} screen", "{n} screens"),
                 files=tn(counts["files"], "{n} course file", "{n} course files")), ""]
    if alone:
        lines += _how_to(message)
    lines += ["## " + t("What is inside"), ""]
    if alone:
        lines += ["- " + t("README.md: this file."), "- " + t("Index of files.md: every file in the zip, with what it is.")]
    lines.append("- " + t("Course overview.md: the course's description, learning outcomes, grading and weekly plan."))
    asgs = assignments(p.detail) if choice.notices else []
    if choice.notices:
        lines.append("- " + t("Announcements.md: the course's announcements on IEULearn."))
        lines.append("- " + (t("Quizzes, exams and deadlines.md: the quizzes, exams, homework and deadlines that were "
                               "announced or mentioned in class, and the assignments on IEULearn with their deadlines.")
                             if asgs else
                             t("Quizzes, exams and deadlines.md: the quizzes, exams, homework and deadlines that were "
                               "announced or mentioned in class.")))
    if p.weeks:
        lines.append("- " + t("A folder for each week (Week 01 - …):"))
        lines.append("  - " + t("Week overview.md: the week's topic, dates, lectures, course files and announcements."))
        if choice.transcripts:
            lines.append("  - " + t("Lectures/<date>/Transcript.md: everything the lecturer said, with times."))
        if choice.screens:
            lines.append("  - " + t("Lectures/<date>/Transcript by screen.md: what was said while each screen (a slide "
                                    "or the board) was shown."))
            lines.append("  - " + t("Lectures/<date>/Screens/: the screens as pictures."))
        if choice.files:
            lines.append("  - " + t("Course files/: the week's slides and documents from IEULearn."))
    if choice.files and p.general:
        lines.append("- " + t("General course files/: files for the whole course, not for one week."))
    if p.loose:
        lines.append("- " + t("Other lectures/: lectures IEUStudia couldn't place in a week."))
    lines.append("")
    if w.week_lines:
        lines += ["## " + t("Weeks"), "", *w.week_lines, ""]
    if asgs:                        # IEULearn's assignments with their deadlines, as the Calendar shows them
        lines += ["## " + t("Assignments"), ""]
        for a in asgs:
            d = _date(a["due"])
            when = (date_long(d) if d else a["due"]) + (f", {a['time']}" if a.get("time") else "")
            sent = {"graded": t("Graded"), "sent": t("Sent"), "not sent yet": t("Not sent yet"),
                    "not sent": t("Not sent")}[_asg_sent(a, today)]
            lines.append(f"- {a.get('title') or t('Assignment')}: " + t("Due {when}", when=when) + f" · {sent}")
        lines.append("")
    if notes.lectures or notes.files or notes.links:
        lines += ["## " + t("Not in this zip"), "", *notes.lectures, *notes.files]
        if notes.links:
            lines.append("- " + tn(len(notes.links), "{n} link (a web page or a video) is listed in its week's "
                                   "overview with its address.", "{n} links (web pages or videos) are listed in their "
                                   "week's overview with their address."))
        lines.append("")
    return "\n".join(lines)


def all_readme(writers: list["_CourseWriter"], choice: Choice, today: dt.date, message: str) -> str:
    total = {k: sum(w.counts[k] for w in writers) for k in ("lectures", "screens", "files")}
    lines = ["# " + t("All courses: {span}", span=span_text(choice, today)), "",
             t("Made with IEUStudia on {date}.", date=date_long(today)) + " " + t(
                 "Inside: {lectures}, {screens} and {files}.",
                 lectures=tn(total["lectures"], "{n} transcribed lecture", "{n} transcribed lectures"),
                 screens=tn(total["screens"], "{n} screen", "{n} screens"),
                 files=tn(total["files"], "{n} course file", "{n} course files")), ""]
    lines += _how_to(message)
    lines += ["## " + t("What is inside"), "",
              "- " + t("README.md: this file."), "- " + t("Index of files.md: every file in the zip, with what it is."),
              "- " + t("A folder for each course, laid out like the export of that course alone: its own README.md, "
                       "Course overview.md and a folder for each week."), ""]
    lines += ["## " + t("Courses"), ""]
    for w in writers:
        lines.append(f"- {w.base}/: {course_title(w.course, w.plan.detail)} · "
                     + tn(w.counts["lectures"], "{n} lecture", "{n} lectures") + " · "
                     + tn(w.counts["files"], "{n} course file", "{n} course files"))
    if any(w.notes.lectures or w.notes.files for w in writers):
        lines += ["", t("What of a course isn't in the zip (lectures not transcribed yet, course files not "
                        "downloaded) is listed in that course's README.md.")]
    return "\n".join(lines)


def index_md(entries: list[tuple[str, str]], root: str) -> str:
    """Every file of the zip, folder by folder."""
    lines = ["# " + t("Index of files"), "", t("Every file in this zip, folder by folder."), ""]
    by_folder: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for rel, what in entries:
        folder, _, name = rel.rpartition("/")
        by_folder[folder].append((name, what))
    for folder in sorted(by_folder, key=lambda f: (f != "", f.casefold())):
        lines += [f"## {root}/{folder}" if folder else f"## {root}", ""]
        lines += [f"- `{name}`: {what}" for name, what in by_folder[folder]]
        lines.append("")
    return "\n".join(lines)


# ------------------------------------------------------------------ writing one course
class _CourseWriter:
    def __init__(self, z: Zip, plan: Plan, base: str, choice: Choice, today: dt.date, counters: dict[str, int],
                 ci: int, cn: int) -> None:
        self.z, self.plan, self.base, self.choice, self.today = z, plan, base, choice, today
        self.course = plan.course
        self.counters, self.ci, self.cn = counters, ci, cn
        self.notes = Notes()
        self.counts = {"lectures": 0, "screens": 0, "files": 0}       # files: different ones (not their copies)
        self._written: set[str] = set()
        self.week_lines: list[str] = []

    def write(self) -> None:
        p, ch, z = self.plan, self.choice, self.z
        code = p.course.code or p.course.name
        # a week's folder name as long as the deepest file below it allows (a course's folder in all courses' zip)
        room = ZIP_PATH_MAX - z.names.prefix - (len(self.base) + 1 if self.base else 0) - BELOW_WEEK
        week_max = max(WEEK_MIN, min(WEEK_MAX, room))
        for wi, w in enumerate(p.weeks, start=1):
            z.tell(force=True, step="week", course=code, ci=self.ci, cn=self.cn, week=w.no, wi=wi, wn=len(p.weeks))
            folder = z.folder(self.base, f"Week {w.no:02d}" + (f" - {w.topic}" if w.topic else ""), week_max)
            lectures = [self._day(folder, d, w.no) for d in w.days]
            files = self._files(folder, "Course files", w.files, w.no)
            z.text(folder, "Week overview.md", week_overview(p.course, w, p, lectures, files, ch, self.today),
                   t("week {n}: its topic, dates, lectures and course files", n=w.no))
            when = _range(w.start, w.end)
            self.week_lines.append(
                "- " + t("Week {n}", n=w.no) + (f" ({when})" if when else "") + (f": {w.topic}" if w.topic else "")
                + " · " + tn(sum(1 for d in w.days if d.done), "{n} lecture", "{n} lectures")
                + " · " + tn(sum(1 for m in w.files if not m.link), "{n} course file", "{n} course files"))
        if p.loose:
            z.tell(force=True, step="week", course=code, ci=self.ci, cn=self.cn, week=0, wi=len(p.weeks), wn=len(p.weeks))
            folder = z.folder(self.base, OTHER_LECTURES, week_max)
            for d in p.loose:
                self._day(folder, d, 0)
        if ch.files and p.general:
            gen = z.folder(self.base, GENERAL, week_max)
            for title, mats in p.general:
                self._files(gen, title, mats, 0)
        z.tell(force=True, step="course", course=code, ci=self.ci, cn=self.cn)
        z.text(self.base, "Course overview.md", course_overview(p.course, p.detail, p),
               t("the course: description, learning outcomes, grading, weekly plan"))
        if ch.notices:
            if p.detail.get("announcements"):
                z.text(self.base, "Announcements.md", announcements_md(p.course, p.detail),
                       t("the course's announcements on IEULearn"))
            deadlines = deadlines_md(p.course, p.detail, self.today)
            if deadlines:
                z.text(self.base, "Quizzes, exams and deadlines.md", deadlines,
                       t("quizzes, exams, homework and deadlines"))

    def _day(self, week_folder: str, d: Day, week_no: int) -> str:
        """A class day's folder (its transcript, its screens and what was said on each); its line in the week's
        overview. One not transcribed yet is only listed (and in the README)."""
        ch, z = self.choice, self.z
        where = t("Week {n}", n=week_no) if week_no else t("Other lectures")
        when, kind = date_short(d.date), t(kind_label(d.kind))
        if not d.done and not d.todo and d.silent and not d.live:      # nothing to transcribe: not "yet"
            self.notes.lectures.append("- " + t("{week}, {date} ({kind}): Panopto's recording has no speech (no sound, "
                                                "or only noise or music), so there is nothing to transcribe.",
                                                week=where, date=when, kind=kind))
            return _lecture_line(d, "", 0, ch)
        if not d.done:
            self.notes.untranscribed += 1
            self.notes.lectures.append("- " + t("{week}, {date} ({kind}): not transcribed in IEUStudia yet.",
                                                week=where, date=when, kind=kind))
            return _lecture_line(d, "", 0, ch)
        if d.todo:
            self.notes.untranscribed += 1
            self.notes.lectures.append("- " + tn(len(d.todo), "{week}, {date} ({kind}): part {parts} is not "
                                                 "transcribed yet.", "{week}, {date} ({kind}): parts {parts} are not "
                                                 "transcribed yet.", week=where, date=when, kind=kind,
                                                 parts=", ".join(map(str, d.todo))))
        if d.silent:
            self.notes.lectures.append("- " + tn(len(d.silent), "{week}, {date} ({kind}): part {parts} has no speech "
                                                 "on Panopto's recording, so it has no transcript.", "{week}, {date} "
                                                 "({kind}): parts {parts} have no speech on Panopto's recording, so "
                                                 "they have no transcript.", week=where, date=when, kind=kind,
                                                 parts=", ".join(map(str, d.silent))))
        if not (ch.transcripts or ch.screens):
            return _lecture_line(d, "", 0, ch)
        label = f"{d.date.isoformat()} {WEEKDAYS_SHORT[d.date.weekday()]} - {kind_label(d.kind)}" + (
            f" (sec {', '.join(d.other)})" if d.other else " (other sec)" if d.another else "")
        folder = z.folder(z.folder(week_folder, "Lectures", 20), label, DAY_MAX)
        recs = sorted(d.recs, key=lambda r: r.start)
        trans = store.load_transcripts(d.done)
        shots = d.slides() if ch.screens else {}
        names: dict[str, str] = {}
        if ch.screens:
            for p in part_views(recs, trans, shots):
                for s in shots.get(p.rec.delivery_id) or []:
                    if not s.path or s.path in names:
                        continue
                    self.counters["screens"] += 1
                    z.tell(step="screens", i=self.counters["screens"], n=self.counters["screens_total"])
                    ext = split_name(Path(s.path).name)[1] or ".jpg"
                    got = z.file(f"{folder}/Screens", _screen_stem(p.index, s.index), ext, s.path,
                                 t("part {part}, screen {n}, on screen from {start}", part=p.index, n=s.index,
                                   start=format_ts(s.t)), keep=True)
                    if got:
                        names[s.path] = got
        if ch.transcripts:
            text = compact_day(self.course, recs, trans, shots or None)
            if text:
                notes = []
                if d.todo:
                    notes.append(f"Not transcribed in IEUStudia yet: part{'s' if len(d.todo) > 1 else ''} "
                                 f"{', '.join(map(str, d.todo))}.")
                if d.silent:
                    notes.append(f"{_silent_parts(d.silent)} (no sound, or only noise or music).")
                if d.another:
                    notes.append(f"This is {d.other_text}'s lecture: the dates and details it gives may differ for "
                                 "your section.")
                z.text(folder, "Transcript.md", text + "".join("\n\n" + n for n in notes) + "\n\n" + _legend(bool(names)),
                       t("transcript: {kind}, {date}", kind=kind, date=when))
        if names:
            z.text(folder, "Transcript by screen.md", by_screen(self.course, d, trans, shots, names),
                   t("what was said on each screen: {kind}, {date}", kind=kind, date=when))
        self.counts["lectures"] += 1
        self.counts["screens"] += len(names)
        rel = folder[len(week_folder) + 1:] if folder.startswith(week_folder + "/") else folder
        return _lecture_line(d, rel, len(names), ch)

    def _files(self, parent: str, sub: str, mats: list[Material], week_no: int) -> list[str]:
        """Course files into ``parent/sub``; their lines for the week's overview (relative to ``parent``). A file that
        lists students (Material.students) stays out, and the README says so."""
        ch, z = self.choice, self.z
        out = []
        for m in mats:
            rel = None
            if m.students and ch.files:
                if m.id not in self.notes.students:
                    self.notes.students.add(m.id)
                    self.notes.files.append("- " + t(
                        "{where}: {name} was left out: it lists students' numbers (grades or groups), and your "
                        "classmates' details shouldn't be uploaded. Open it on IEULearn if you need it.",
                        where=t("Week {n}", n=week_no) if week_no else t("The whole course"), name=m.shown))
                out.append(_material_line(m, None, ch))
                continue
            if m.path and ch.files and not m.link:
                self.counters["files"] += 1
                z.tell(step="files", i=self.counters["files"], n=self.counters["files_total"])
                folder = z.folder(parent, sub, 40)
                stem, ext = split_name(m.file_name or Path(m.path).name, m.ext)
                if not ext:
                    ext = split_name(Path(m.path).name)[1]
                got = z.file(folder, stem, ext, m.path, t("course file of week {n}", n=week_no) if week_no else
                             t("course file for the whole course"))
                if got:
                    self._written.add(m.id)              # a unit for two weeks is in both: one file, counted once
                    self.counts["files"] = len(self._written)
                    rel = folder[len(parent) + 1:] + "/" + got
            if m.link:
                self.notes.links.add(m.id)
            elif rel is None and ch.files:
                where = t("Week {n}", n=week_no) if week_no else t("The whole course")
                self.notes.missing.add(m.id)
                self.notes.files.append("- " + (
                    t("{where}: {name} couldn't be downloaded: {reason}", where=where, name=m.shown,
                      reason=m.reason.rstrip(". ") + ".") if m.reason else
                    t("{where}: {name} isn't downloaded in IEUStudia.", where=where, name=m.shown)))
            out.append(_material_line(m, rel, ch))
        return out


# ------------------------------------------------------------------ the whole export
def _check_space(folder: Path, size: int) -> None:
    """Enough free space on the disk for a zip of about ``size`` (with some to spare)? Else why not, in plain words."""
    try:
        free = shutil.disk_usage(folder).free
    except OSError:                              # (a drive that doesn't say: the writing itself will tell)
        return
    need = int(size * 1.1) + SPACE_SPARE
    if free < need:
        raise ExportError(t("There isn't enough free space on the disk for this export: it needs about {need}, and "
                            "{free} is free. Free up some space, or leave out the screens or some weeks, and try again.",
                            need=size_text(need), free=size_text(free)))


def build(lib: Any, settings: Any, output_root: Path, course_id: str, choice: Choice, *,
          download: Optional[Callable[[str, Callable[[int, int], None]], Any]] = None,
          progress: Callable[[dict[str, Any]], None] = lambda e: None,
          cancel: Optional[threading.Event] = None, today: Optional[dt.date] = None) -> dict[str, Any]:
    """Make the zip of one course (``course_id``) or of every course (``""``); return where it is, how big, and what
    isn't in it. Raises ExportError (in plain words), Cancelled, or an OSError (a full disk), each after removing what
    it wrote."""
    today = today or dt.date.today()
    if choice.weeks is not None and not choice.weeks:
        raise ExportError(t("Pick at least one week."))
    courses = export_courses(lib, course_id)
    if not courses:
        raise ExportError(t("This course is no longer in your list.") if course_id else
                          t("There are no courses to export yet. Sync first."))
    progress({"step": "plan", "fraction": 0.0})
    plans = [p for p in (plan_course(lib, settings, c.id, choice, today) for c in courses) if p]
    check(cancel)
    every = not course_id
    all_courses = lib.courses(True)
    folder = export_folder(output_root, lib, course_id)
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        from .net import plain_error

        raise ExportError(t("The folder for the export ({folder}) can't be made. {error}", folder=str(folder),
                            error=plain_error(exc))) from exc
    _check_space(folder, estimate(plans, choice)[0])      # before anything is downloaded or written
    if choice.files:
        _download(plans, choice, download, progress, cancel)
        _leave_out_lists(lib, plans)
    check(cancel)
    span = span_name(choice, today, {w.no for p in plans for w in p.all_weeks})
    lead = "All courses" if every else course_folder_name(courses[0], all_courses)
    root = clean_name(f"{lead} - {span}", ROOT_MAX)
    target = folder / fit_name(folder, root, ".zip", 120, spare=len(".tmp"))
    size, work = estimate(plans, choice)
    _check_space(folder, size)                            # (with the files just downloaded)
    counters = {"screens": 0, "files": 0,
                "screens_total": sum(len(d.screens()) for p in plans for d in _days(p) if d.done) if choice.screens else 0,
                "files_total": sum(1 for p in plans for m in _wanted(p) if m.path and not m.link and not m.students)
                if choice.files else 0}
    progress({"step": "start", "estimate": size, "fraction": 0.0})
    tmp = target.with_name(target.name + ".tmp")
    message = first_message(courses, choice, all_courses, every)
    z = Zip(tmp, root, work, progress, cancel)
    writers: list[_CourseWriter] = []
    try:
        for ci, p in enumerate(plans, start=1):
            base = z.folder("", course_folder_name(p.course, all_courses), COURSE_MAX) if every else ""
            w = _CourseWriter(z, p, base, choice, today, counters, ci, len(plans))
            w.write()
            writers.append(w)
            z.text(base, "README.md", course_readme(w, choice, today, message, alone=not every),
                   t("what is in this course's folder") if every else
                   t("what is in this zip, and how to use it in ChatGPT"))
        z.tell(force=True, step="zip")
        if every:
            z.text("", "README.md", all_readme(writers, choice, today, message),
                   t("what is in this zip, and how to use it in ChatGPT"))
        z.text("", "Index of files.md", index_md(z.index + [("Index of files.md", t("this list"))], root),
               t("this list"))
        z.close()
        check(cancel)
        final = _place(tmp, target)
    except BaseException:
        z.abort()
        _remove(tmp)
        raise
    got = final.stat().st_size
    out = {"path": str(final), "folder": str(final.parent), "name": final.name, "bytes": got, "estimate": size,
           "renamed": final != target, "courses": len(writers),
           "lectures": sum(w.counts["lectures"] for w in writers), "screens": sum(w.counts["screens"] for w in writers),
           "files": sum(w.counts["files"] for w in writers),
           "untranscribed": sum(w.notes.untranscribed for w in writers),
           "missing": len(set().union(*(w.notes.missing for w in writers))) if writers else 0,
           "links": len(set().union(*(w.notes.links for w in writers))) if writers else 0,
           "students": len(set().union(*(w.notes.students for w in writers))) if writers else 0,
           "weeks": sorted({w.no for p in plans for w in p.weeks}), "first_message": message,
           "too_big": got > CHATGPT_MAX}
    log.info("Export: %s (%d KB; %d lectures, %d screens, %d files)", final.name, got // 1024, out["lectures"],
             out["screens"], out["files"])
    return out
