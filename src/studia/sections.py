"""Which recordings belong to *my* section of a course (pure heuristics, unit-tested).

Panopto folders often hold every section's recordings ("Section 1" on Tuesdays,
"Section 2-3" on Wednesdays, labs per section...). Names are inconsistent, so we combine
explicit tags with a weekday inference for untagged recordings.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Iterable, Optional

from .domain import Course, Recording

_TAG_PATTERNS = [
    # Section 1, Sections 2-3, Sec 3, Section-2, Section_2, Section: 2, Sec. #3
    re.compile(r"\bsect?(?:ion)?s?\.?\s*[-_:#]?\s*(\d{1,2}(?:\s*[-*,&/]\s*\d{1,2})*)", re.I),
    re.compile(r"\b[A-Z]{2,5}\s?\d{3}\s*-\s*(\d{1,2})\b"),                              # EEE215-2
    re.compile(r"\bşube\s*[-_:#]?\s*(\d{1,2}(?:\s*[-*,]\s*\d{1,2})*)", re.I),            # Şube 1, Şube-1
]


def parse_section_tag(name: str) -> str:
    """``"... Sections 2-3"`` → ``"2-3"``; ``"EEE215-2 Lab"`` → ``"2"``; untagged → ``""``."""
    for pat in _TAG_PATTERNS:
        m = pat.search(name or "")
        if m:
            nums = re.findall(r"\d{1,2}", m.group(1))
            return "-".join(nums)
    return ""


def section_set(tag: str) -> set[str]:
    """``"2-3"`` → {"2","3"}; ``"0 - 1"`` → {"0","1"}; numbers normalised (``"01"`` → ``"1"``)."""
    return {str(int(n)) for n in re.findall(r"\d{1,2}", tag or "")}


def guess_my_section(course: Course) -> Optional[str]:
    """The user's section from the IEULearn title suffix when it names exactly one ("… Cebir 3" → "3"). A merged
    course page ("0 - 1", "2-3") says nothing about the student's own section: None, and the app asks."""
    nums = section_set(course.section)
    return next(iter(nums)) if len(nums) == 1 and "0" not in nums else None


def available_sections(recs: Iterable[Recording]) -> list[str]:
    found: set[str] = set()
    for r in recs:
        found |= section_set(r.section_tag or parse_section_tag(r.name))
    return sorted(found, key=int)


def classify(course: Course, recs: list[Recording], my_section: Optional[str] = None) -> dict[str, Optional[bool]]:
    """Per delivery id: True = my section, False = another section, None = unknown (show it)."""
    mine = section_set(my_section) if my_section else section_set(guess_my_section(course) or "")
    result: dict[str, Optional[bool]] = {}
    if not mine:
        return {r.delivery_id: None for r in recs}
    by_weekday: dict[tuple[int, str], list[bool]] = defaultdict(list)
    for r in recs:
        tag = section_set(r.section_tag or parse_section_tag(r.name))
        if tag:
            verdict = bool(tag & mine)
            result[r.delivery_id] = verdict
            by_weekday[(r.start.weekday(), r.kind)].append(verdict)
    for r in recs:
        if r.delivery_id in result:
            continue
        votes = by_weekday.get((r.start.weekday(), r.kind), [])
        if votes and all(votes):
            result[r.delivery_id] = True
        elif votes and not any(votes):
            result[r.delivery_id] = False
        else:
            result[r.delivery_id] = None
    return result
