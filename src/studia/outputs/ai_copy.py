"""Clipboard text for "Copy for AI": prompt + compact transcript (no Markdown decoration)."""

from __future__ import annotations

from typing import Any, Iterable, Optional, Sequence

from ..domain import Course, Recording
from .common import course_display, format_ts, kind_label, lang_tag, long_date, time_range
from .markdown import part_views
from .prompts import ai_prompt


def _noise(para: Any) -> bool:
    """A paragraph with nothing in it for the AI: only "[unclear]", or at most three words the speech recognition
    was unsure of (mostly its stock phrases in quiet moments), unless the lecturer emphasised it."""
    words = para.text.replace("[unclear]", " ").strip(" .,!?…").split()
    return not words or (para.uncertain and not para.star and len(words) <= 3)


def compact_day(course: Course, recordings: Iterable[Recording], transcripts: Any, slides: Any = None,
                label: str = "", only_starred: bool = False) -> str:
    """One class day as compact text. ``label`` (e.g. "L2") prefixes the heading when several days are
    combined; ``only_starred`` keeps just the paragraphs the lecturer emphasised (⭐). Paragraphs with nothing in
    them (``_noise``) are left out."""
    recs = sorted(recordings, key=lambda r: r.start)
    parts = [p for p in part_views(recs, transcripts, slides) if p.transcript]
    if not parts:
        return ""
    head = (f"# {label + ' · ' if label else ''}{course_display(course)} · {kind_label(parts[0].rec.kind)} · "
            f"{long_date(parts[0].rec.start.date())}")
    if course.instructor:
        head += f" · Lecturer: {course.instructor}"
    lines = [head]
    for p in parts:
        paras = part_lines(p, only_starred)
        if not paras and only_starred:
            continue
        lines.append(f"\n## Part {p.index} ({time_range(p.rec)})")
        lines += paras
    if only_starred and len(lines) == 1:
        lines.append("(Nothing was marked as emphasised in this lecture.)")
    return "\n".join(lines)


def part_lines(p: Any, only_starred: bool = False) -> list[str]:
    """One recording's paragraphs as compact_day writes them: "[mm:ss] (slide N) [TR] ⭐ text"."""
    out = []
    for para in p.paragraphs:
        if (para.star or not only_starred) and not _noise(para):
            tag = lang_tag(para.lang)
            marks = (f"[{tag}] " if tag else "") + ("⭐ " if para.star else "") + ("⚠ " if para.uncertain else "")
            slide = f"(slide {para.slide}) " if para.slide is not None else ""
            out.append(f"[{format_ts(para.start)}] {slide}{marks}{para.text}")
    return out


def build_ai_text(template: str, course: Course, days: Sequence[tuple[Sequence[Recording], Any]],
                  slides: Optional[Any] = None) -> str:
    body = "\n\n".join(t for t in (compact_day(course, recs, trans, slides) for recs, trans in days) if t)
    prompt = ai_prompt(template)
    if not prompt:
        return body
    return f"{prompt}\n\n=== LECTURE TRANSCRIPT ===\n\n{body}\n"
