"""Simple checks on a course's recordings that tell when something about one looks off (no AI involved). Each check
fires only on a clear signal, so that a mark on a lecture means something."""

from __future__ import annotations

import datetime as dt
import statistics
from collections import Counter, defaultdict
from typing import Any, Iterable, Optional

from .catchup import no_sound
from .domain import Recording
from .i18n import date_long, duration, join, t, weekday

PATTERN_DAYS = 6            # off_day: lecture days needed before the course's weekdays count as a pattern
PATTERN_WEEKDAYS = 3        # (on at most this many weekdays)
LENGTH_SAMPLES = 5          # short / long: recordings of a kind needed for "the course's usual length"
SHORT_FRACTION = 0.25       # under this share of the usual length...
SHORT_USUAL_MIN = 20 * 60   # ...of a course whose recordings are usually at least this long
LONG_FACTOR = 2.5           # over this many times the usual class day...
LONG_EXTRA = 60 * 60        # ...and at least this much longer
FUTURE = dt.timedelta(hours=1)


def course_anomalies(recs: Iterable[Recording], warnings: Optional[dict[str, list[dict[str, Any]]]] = None,
                     now: Optional[dt.datetime] = None, min_s: float = 0.0) -> dict[str, list[dict[str, str]]]:
    """What looks off about a course's recordings: {delivery id: [{"kind", "text"}]} (only those with something).
    ``warnings``: the stored transcription warnings by id; ``min_s``: shorter ones are shown as too short already.
    Duplicates are left out."""
    warnings = warnings or {}
    now = now or dt.datetime.now()
    recs = [r for r in recs if not r.duplicate_of]
    out: dict[str, list[dict[str, str]]] = defaultdict(list)

    def add(r: Recording, kind: str, text: str) -> None:
        out[r.delivery_id].append({"kind": kind, "text": text})

    # (finished ones the student hasn't hidden: what the course's pattern and usual length are made of)
    held = [r for r in recs if not r.live and not r.hidden and r.duration_s > 0 and r.start <= now]
    starts = Counter(r.start.replace(second=0) for r in recs if not r.hidden)
    weekdays = _class_weekdays([r for r in held if r.kind == "lecture" and r.duration_s >= min_s])
    usual: dict[str, tuple[float, float]] = {}            # kind → (median recording, median class day)
    for kind in {r.kind for r in held}:
        lens = [r.duration_s for r in held if r.kind == kind and r.duration_s >= min_s]
        days: dict[dt.date, float] = defaultdict(float)
        for r in held:
            if r.kind == kind and r.duration_s >= min_s:
                days[r.start.date()] += r.duration_s
        if len(lens) >= LENGTH_SAMPLES:
            usual[kind] = (statistics.median(lens), statistics.median(days.values()))

    for r in recs:
        if r.uploaded:
            try:
                up = dt.datetime.fromisoformat(r.uploaded)
            except ValueError:
                up = None
            if up is not None:
                add(r, "uploaded", t("Uploaded to Panopto on {date}; the date in its name is used", date=date_long(up)))
        if r.live:
            add(r, "live", t("Still being recorded"))
        elif r.status == "processing":
            add(r, "processing", t("Panopto is still processing it"))
        if not r.hidden and starts[r.start.replace(second=0)] > 1:
            add(r, "same_start", t("Another recording of this course starts at the very same minute: their order and "
                                   "date may be wrong"))
        if weekdays and r.kind == "lecture" and r.start.weekday() not in weekdays:
            add(r, "off_day", t("Recorded on a {day}, not one of this course's class days ({days})",
                                day=weekday(r.start), days=join(weekday(_day(d)) for d in sorted(weekdays))))
        if r.kind in usual and not r.live and r.duration_s >= min_s and r.duration_s > 0:
            each, day = usual[r.kind]
            if each >= SHORT_USUAL_MIN and r.duration_s < each * SHORT_FRACTION:
                add(r, "short", t("Much shorter than this course's usual {usual}", usual=duration(each)))
            elif r.duration_s > day * LONG_FACTOR and r.duration_s - day >= LONG_EXTRA:
                add(r, "long", t("Much longer than this course's usual {usual}", usual=duration(day)))
        if r.start > now + FUTURE:
            add(r, "future", t("Its date is still to come"))
        if no_sound(r, warnings.get(r.delivery_id, ())):
            add(r, "no_sound", t("No speech was found in it"))
    return dict(out)


def _class_weekdays(lectures: list[Recording]) -> set[int]:
    """The weekdays a course meets on (Monday = 0) when it has a clear pattern: at least PATTERN_DAYS lecture days on
    at most PATTERN_WEEKDAYS weekdays, each seen in two weeks or more. Empty when there is none (too early in the
    term, or lectures on many days)."""
    per_weekday = Counter(d.weekday() for d in {r.start.date() for r in lectures})
    regular = {wd for wd, n in per_weekday.items() if n >= 2}
    if not regular or len(regular) > PATTERN_WEEKDAYS or sum(per_weekday[wd] for wd in regular) < PATTERN_DAYS:
        return set()
    return regular


def _day(wd: int) -> dt.date:
    return dt.date(2026, 1, 5) + dt.timedelta(days=wd)       # (a Monday)


def for_ids(lib: Any, ids: Iterable[str], min_s: float = 0.0) -> dict[str, list[dict[str, str]]]:
    """The anomalies of these recordings (the jobs pane): each one's course is checked as a whole."""
    want = {str(i) for i in ids}
    courses = {r.course_id for r in (lib.get_recording(i) for i in want) if r is not None}
    if not courses:
        return {}
    warnings = lib.all_warnings()
    out: dict[str, list[dict[str, str]]] = {}
    for cid in courses:
        found = course_anomalies(lib.recordings(cid), warnings, min_s=min_s)
        out.update({i: v for i, v in found.items() if i in want})
    return out
