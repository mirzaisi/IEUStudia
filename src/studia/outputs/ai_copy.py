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


SCREEN_TEXT_MAX = 300      # characters of a screen's words in an AI prompt
# what the screen lines are, for the prompts that have them (study notes, chats)
SCREEN_LINES = ("Lines starting [Screen n, mm:ss-mm:ss] are the text shown on screen n while the lecturer spoke (its "
                "title and words, read from the slide); when the slide picture is attached, trust the picture.")


def compact_day(course: Course, recordings: Iterable[Recording], transcripts: Any, slides: Any = None,
                label: str = "", only_starred: bool = False, screens: str = "full") -> str:
    """One class day as compact text. ``label`` (e.g. "L2") prefixes the heading when several days are
    combined; ``only_starred`` keeps just the paragraphs the lecturer emphasised (⭐). Paragraphs with nothing in
    them (``_noise``) are left out. ``screens``: the screen lines (part_lines) with their words ("full"), their
    titles only ("titles", for a request that is tight: the offline AI, a whole-course chat near its limit) or none."""
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
        paras = part_lines(p, only_starred, screens)
        if not paras and only_starred:
            continue
        lines.append(f"\n## Part {p.index} ({time_range(p.rec)})")
        lines += paras
    if only_starred and len(lines) == 1:
        lines.append("(Nothing was marked as emphasised in this lecture.)")
    return "\n".join(lines)


def screen_line(n: int, t0: float, t1: float, screen: Optional[dict], titles_only: bool = False,
                again: bool = False) -> str:
    """A screen of the timeline (screens.py) as the AI reads it: "[Screen 12, 34:10-36:40] title · text", its text
    on one line and capped; "(writing not read)" when OCR read nothing on it; "" when nothing was read (no OCR), and
    with ``titles_only`` for a screen whose words were read but that has no title (OCR's: its words are left out)."""
    s = screen or {}
    title = " ".join(str(s.get("title") or "").split())
    text = "; ".join(" ".join(x.split()) for x in str(s.get("text") or "").splitlines() if x.strip())
    if s.get("from") == "none" or not s:
        return ""
    unread = not text                        # (said from its own words: not when they are only left out here)
    if again or titles_only:
        text = ""
    if len(text) > SCREEN_TEXT_MAX:
        text = text[:SCREEN_TEXT_MAX].rsplit(" ", 1)[0].rstrip(" ;,") + " …"
    body = " · ".join(x for x in (title, text) if x)
    if not body:
        if again:
            return ""
        body = "(writing not read)" if s.get("from") == "ocr" and unread else ""
    if not body:
        return ""
    return f"[Screen {n}, {format_ts(t0)}-{format_ts(t1)}] {body}{' (shown again)' if again else ''}"


def _screen_lines(p: Any, screens: str) -> dict[int, str]:
    """{paragraph number: the screen line that goes before it} for one recording: each span of a screen before the
    first paragraph said while it was on (its spans and words: the recording's slide index and screen timeline)."""
    from .. import store

    if screens == "none" or not p.paragraphs or p.rec is None:
        return {}
    doc = store.load_screens(p.rec.delivery_id)
    if not doc or not doc.get("screens"):
        return {}
    spans = sorted(p.slides or store.load_slides(p.rec.delivery_id), key=lambda s: s.t)
    if not spans:
        return {}
    out: dict[int, str] = {}
    shown: set[int] = set()                  # screens described already: a span of one shown again gets its title
    done: set[int] = set()                   # spans (by place) whose line is written
    k = 0
    for i, para in enumerate(p.paragraphs):
        at = para.start + 0.5
        while k + 1 < len(spans) and spans[k + 1].t <= at:
            k += 1
        s = spans[k]
        if not s.t <= at < s.end or k in done:
            continue
        done.add(k)
        line = screen_line(s.index, s.t, s.end, doc["screens"].get(str(s.index)), screens == "titles",
                           again=s.index in shown)
        shown.add(s.index)
        if line:
            out[i] = line
    return out


def part_lines(p: Any, only_starred: bool = False, screens: str = "full") -> list[str]:
    """One recording's paragraphs as compact_day writes them: "[mm:ss] (slide N) [TR] ⭐ text", each screen of its
    timeline before the first paragraph said while it was on ("[Screen 12, 34:10-36:40] title · text"; see
    compact_day for ``screens``). With ``only_starred`` (the emphasised moments only) there are no screen lines."""
    lines = _screen_lines(p, screens) if not only_starred else {}
    out = []
    for i, para in enumerate(p.paragraphs):
        if (para.star or not only_starred) and not _noise(para):
            if i in lines:
                out.append(lines[i])
            tag = lang_tag(para.lang)
            marks = (f"[{tag}] " if tag else "") + ("⭐ " if para.star else "") + ("⚠ " if para.uncertain else "")
            slide = f"(slide {para.slide}) " if para.slide is not None else ""
            out.append(f"[{format_ts(para.start)}] {slide}{marks}{para.text}")
        elif i in lines and i + 1 < len(p.paragraphs):      # (a paragraph left out: its screen goes with the next)
            lines.setdefault(i + 1, lines[i])
    return out


def build_ai_text(template: str, course: Course, days: Sequence[tuple[Sequence[Recording], Any]],
                  slides: Optional[Any] = None) -> str:
    body = "\n\n".join(t for t in (compact_day(course, recs, trans, slides) for recs, trans in days) if t)
    prompt = ai_prompt(template)
    if not prompt:
        return body
    return f"{prompt}\n\n=== LECTURE TRANSCRIPT ===\n\n{body}\n"
