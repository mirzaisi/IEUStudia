"""Small shared helpers for the output renderers.

Formatting of timestamps, durations, dates and language names, plus the logic that
decides which recordings of a class day are rendered as "Part 1..n". Everything here is
pure (no I/O) and locale-independent, so files look the same on every PC.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from collections.abc import Iterable, Mapping
from typing import Any

from ..domain import Course, Recording, Transcript
from ..i18n import N_

log = logging.getLogger("studia")

#: Recordings shorter than this are false starts and are not shown as parts (unless transcribed).
MIN_PART_S = 60.0

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
WEEKDAYS_SHORT = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December")

LANGUAGE_NAMES = {
    "en": "English", "tr": "Turkish", "de": "German", "fr": "French", "es": "Spanish",
    "it": "Italian", "ar": "Arabic", "ru": "Russian", "fa": "Persian", "az": "Azerbaijani",
    "nl": "Dutch", "pt": "Portuguese", "el": "Greek", "zh": "Chinese", "ja": "Japanese",
    "ko": "Korean", "uk": "Ukrainian", "pl": "Polish",
}
_LANGUAGE_ALIASES = {"english": "en", "turkish": "tr", "german": "de", "french": "fr"}

#: The language that is *not* tagged in transcripts (all other languages get "[XX]").
BASE_LANGUAGE = "en"


# --------------------------------------------------------------------------- time & date

def format_ts(seconds: float) -> str:
    """Timestamp relative to the start of a part: ``mm:ss`` below one hour, else ``h:mm:ss``.

    Seconds are floored (a paragraph starting at 12.9 s is shown as 00:12, so jumping to the
    timestamp never skips its first words). Negative or invalid input gives ``00:00``.
    """
    try:
        total = int(max(0.0, float(seconds)))
    except (TypeError, ValueError):
        total = 0
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def format_duration(seconds: float) -> str:
    """Human duration rounded to minutes: ``40 min``, ``2 h 0 min``, ``<1 min``."""
    try:
        secs = max(0.0, float(seconds))
    except (TypeError, ValueError):
        secs = 0.0
    minutes = int(secs / 60 + 0.5)
    if minutes == 0:
        return "<1 min" if secs > 0 else "0 min"
    h, m = divmod(minutes, 60)
    return f"{h} h {m} min" if h else f"{m} min"


def to_date(value: Any) -> dt.date:
    """Accept a ``date``, ``datetime`` or ISO string and return a ``date``."""
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        try:
            return dt.date.fromisoformat(value.strip()[:10])
        except ValueError:
            pass
    raise ValueError(f"Not a date: {value!r}")


def parse_iso_datetime(value: Any) -> dt.datetime | None:
    """Parse an ISO timestamp into a *naive* wall-clock datetime (None if impossible)."""
    if isinstance(value, dt.datetime):
        return value.replace(tzinfo=None)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return dt.datetime.fromisoformat(value.strip()).replace(tzinfo=None)
    except ValueError:
        return None


def long_date(d: dt.date) -> str:
    """``Tuesday, 13 October 2026`` (English, independent of the Windows locale)."""
    return f"{WEEKDAYS[d.weekday()]}, {d.day} {MONTHS[d.month - 1]} {d.year}"


def weekday_short(d: dt.date) -> str:
    return WEEKDAYS_SHORT[d.weekday()]


def day_label(d: dt.date) -> str:
    """``2026-10-13 (Tue)`` — compact and sortable, used by the study pack."""
    return f"{d.isoformat()} ({weekday_short(d)})"


def kind_label(kind: str) -> str:
    k = (kind or "lecture").strip().lower()
    return {"lecture": N_("Lecture"), "lab": N_("Lab")}.get(k, k.title() or "Lecture")   # English (file names); t() to show it


def recording_end(rec: Recording) -> dt.datetime:
    return rec.start + dt.timedelta(seconds=max(0.0, float(rec.duration_s or 0.0)))


def time_range(rec: Recording) -> str:
    """Wall-clock range of a recording, e.g. ``11:19–11:59``."""
    return f"{rec.start:%H:%M}–{recording_end(rec):%H:%M}"


# --------------------------------------------------------------------------- languages

def normalize_lang(code: Any, default: str = "") -> str:
    """``"EN"`` / ``"en-US"`` / ``"english"`` → ``"en"``; empty → *default*."""
    c = str(code or "").strip().lower()
    if not c:
        return default
    c = _LANGUAGE_ALIASES.get(c, c)
    return re.split(r"[-_]", c, maxsplit=1)[0] or default


def language_name(code: str) -> str:
    c = normalize_lang(code)
    return LANGUAGE_NAMES.get(c, c.upper() or "Unknown")


def lang_tag(code: str) -> str:
    """Tag shown before non-English passages: ``"TR"`` (empty for English)."""
    c = normalize_lang(code, BASE_LANGUAGE)
    return "" if c == BASE_LANGUAGE else c.upper()


def language_shares(transcripts: Iterable[Transcript]) -> dict[str, float]:
    """Share of speech per language (0..1), largest first.

    Uses ``Transcript.language_seconds`` and falls back to segment durations when a
    transcript does not carry it.
    """
    totals: dict[str, float] = {}
    for tx in transcripts:
        secs = {normalize_lang(k): float(v or 0) for k, v in (tx.language_seconds or {}).items()}
        if sum(secs.values()) <= 0:
            secs = {}
            for s in tx.segments:
                lang = normalize_lang(s.lang, BASE_LANGUAGE)
                secs[lang] = secs.get(lang, 0.0) + max(0.0, float(s.end) - float(s.start))
        for k, v in secs.items():
            if k and v > 0:
                totals[k] = totals.get(k, 0.0) + v
    total = sum(totals.values())
    if total <= 0:
        return {}
    return {k: v / total for k, v in sorted(totals.items(), key=lambda kv: -kv[1])}


def main_language(transcripts: Iterable[Transcript]) -> str:
    """The language most of the speech is in: "en", "tr" ("" with none)."""
    return next(iter(language_shares(transcripts)), "")


def format_language_shares(shares: Mapping[str, float]) -> str:
    """``English 96 % · Turkish 4 %`` (tiny shares shown as ``<1 %``, never "100 %" next to another)."""
    items = [(k, v) for k, v in shares.items() if v > 0]
    out = []
    for k, v in items:
        pct = round(v * 100)
        if pct < 1:
            text = "<1"
        else:
            text = str(min(pct, 99) if len(items) > 1 else pct)
        out.append(f"{language_name(k)} {text} %")
    return " · ".join(out)


# --------------------------------------------------------------------------- course & recordings

def course_display(course: Course) -> str:
    """``EEE 215 – Electric Circuits`` (just the code when there is no name)."""
    code = (course.code or "").strip()
    name = (course.name or "").strip()
    if code and name:
        return f"{code} – {name}"
    return code or name or (course.title or "").strip() or f"Course {course.id}"


def course_short(course: Course) -> str:
    return (course.code or "").strip() or (course.name or "").strip() or f"Course {course.id}"


def as_transcript_map(transcripts: Any) -> dict[str, Transcript]:
    """Accept ``{delivery_id: Transcript}``, a list of Transcripts (``None`` entries allowed) or None."""
    if not transcripts:
        return {}
    if isinstance(transcripts, Mapping):
        return {str(k): v for k, v in transcripts.items() if v is not None}
    out: dict[str, Transcript] = {}
    for tx in transcripts:
        if tx is not None:
            out[str(tx.delivery_id)] = tx
    return out


def is_too_short(rec: Recording) -> bool:
    if (rec.status or "") == "too_short":
        return True
    dur = float(rec.duration_s or 0.0)
    return 0.0 < dur < MIN_PART_S


def select_parts(recordings: Iterable[Recording], transcripts: Any) -> list[Recording]:
    """The recordings of one class day that are rendered as "Part 1..n", ordered by start time.

    Transcribed recordings are always kept. Untranscribed ones are skipped when they are a
    duplicate, hidden by the user, or a false start (< ``MIN_PART_S``). A transcribed duplicate
    is skipped when its original is transcribed too, and an untranscribed original is skipped
    when its duplicate carries the transcript (the same class hour never appears twice).
    """
    tmap = as_transcript_map(transcripts)
    recs = sorted(recordings, key=lambda r: (r.start, r.delivery_id))
    ids = {r.delivery_id for r in recs}
    covered_by_duplicate = {r.duplicate_of for r in recs if r.duplicate_of and r.delivery_id in tmap}
    parts: list[Recording] = []
    for r in recs:
        if r.delivery_id in tmap:
            if r.duplicate_of and r.duplicate_of in ids and r.duplicate_of in tmap:
                continue
        elif r.duplicate_of or r.hidden or is_too_short(r) or r.delivery_id in covered_by_duplicate:
            continue
        parts.append(r)
    return parts


def group_days(recordings: Iterable[Recording]) -> list[tuple[dt.date, str, list[Recording]]]:
    """Group recordings into class days ``(date, kind, recordings)``, chronologically."""
    groups: dict[tuple[dt.date, str], list[Recording]] = {}
    for r in recordings:
        groups.setdefault((r.start.date(), (r.kind or "lecture").lower()), []).append(r)
    days = [(d, k, sorted(rs, key=lambda r: r.start)) for (d, k), rs in groups.items()]
    days.sort(key=lambda x: (x[0], x[2][0].start, x[1]))
    return days


# --------------------------------------------------------------------------- transcript metadata

def device_label(device: str) -> str:
    d = (device or "").strip().lower()
    if d.startswith(("cuda", "gpu")):
        return "GPU"
    if d.startswith("cpu"):
        return "CPU"
    return (device or "").strip()


def model_label(model: str) -> str:
    m = (model or "").strip().rsplit("/", 1)[-1]
    if not m:
        return ""
    return m if "whisper" in m.lower() else f"Whisper {m}"


def app_version_label() -> str:
    """``IEUStudia 1.0`` (major.minor of the running app)."""
    try:
        from .. import __version__
    except ImportError:  # pragma: no cover - package always has a version
        __version__ = ""
    short = ".".join(str(__version__).split(".")[:2])
    return f"IEUStudia {short}".strip()


def transcribed_on(transcripts: Iterable[Transcript]) -> str:
    """Date (``YYYY-MM-DD``) of the most recent transcript, or ``""``."""
    dates = []
    for tx in transcripts:
        stamp = parse_iso_datetime(tx.created_at)
        if stamp is not None:
            dates.append(stamp.date())
    return max(dates).isoformat() if dates else ""


def engine_summary(transcripts: Iterable[Transcript]) -> str:
    """``IEUStudia 1.0 (Whisper large-v3-turbo, GPU)``."""
    models: list[str] = []
    devices: list[str] = []
    for tx in transcripts:
        m, d = model_label(tx.model), device_label(tx.device)
        if m and m not in models:
            models.append(m)
        if d and d not in devices:
            devices.append(d)
    detail = ", ".join(models + devices)
    return f"{app_version_label()} ({detail})" if detail else app_version_label()


# --------------------------------------------------------------------------- markdown

_MD_SPECIAL = re.compile(r"([\\`*_\[\]<])")


def md_escape(text: str) -> str:
    """Escape the few characters that would change the meaning of transcript text in Markdown."""
    return _MD_SPECIAL.sub(r"\\\1", text or "")


def md_link(label: str, url: str) -> str:
    url = (url or "").strip()
    if not url:
        return label
    if any(c in url for c in " ()<>"):
        url = "<" + url.replace("<", "%3C").replace(">", "%3E") + ">"
    return f"[{label}]({url})"
