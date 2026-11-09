"""Markdown for one class day (all parts of a lecture/lab on one date)."""

from __future__ import annotations

import dataclasses as dc
from typing import Any, Iterable, Mapping, Optional, Sequence

from ..domain import Course, Recording, Transcript
from .common import (as_transcript_map, course_display, engine_summary, format_duration, format_language_shares,
                     format_ts, kind_label, lang_tag, language_shares, long_date, md_escape, md_link, select_parts,
                     time_range, transcribed_on)
from .highlights import LABELS, excerpt
from .paragraphs import Paragraph, build_paragraphs

UNCLEAR_MD = "*[unclear]*"


@dc.dataclass
class PartView:
    index: int
    rec: Recording
    transcript: Optional[Transcript]
    paragraphs: list[Paragraph]
    slides: list[Any]                 # objects with .index, .t (seconds), .image (path relative to the day file)
    warnings: list[dict[str, Any]]


def _paragraphs(t: Transcript) -> list[Paragraph]:
    """The transcript's paragraphs, built once per Transcript object: the store hands out the same object until it
    drops it, and a new transcription is a new object. Callers get copies (part_views sets .slide)."""
    built = t.__dict__.get("_paragraphs")
    if built is None or built[0] is not t.segments or built[1] != len(t.segments):
        built = (t.segments, len(t.segments), build_paragraphs(t.segments))
        t.__dict__["_paragraphs"] = built
    return [dc.replace(p, tags=list(p.tags)) for p in built[2]]


def part_views(recordings: Iterable[Recording], transcripts: Any, slides: Optional[Mapping[str, Sequence[Any]]] = None,
               warnings: Optional[Mapping[str, list[dict[str, Any]]]] = None) -> list[PartView]:
    tmap = as_transcript_map(transcripts)
    out = []
    for i, rec in enumerate(select_parts(recordings, tmap), start=1):
        t = tmap.get(rec.delivery_id)
        paras = _paragraphs(t) if t else []
        sl = list((slides or {}).get(rec.delivery_id) or [])
        if sl and paras:
            k = 0
            for p in paras:
                while k + 1 < len(sl) and sl[k + 1].t <= p.start + 0.5:
                    k += 1
                p.slide = sl[k].index if sl[k].t <= p.start + 0.5 else None
        w = list((warnings or {}).get(rec.delivery_id) or [])
        if t and not w:
            w = list(((t.meta or {}).get("quality") or {}).get("warnings") or [])
        out.append(PartView(i, rec, t, paras, sl, w))
    return out


def para_spans(p: PartView) -> list[Optional[Any]]:
    """For each paragraph of a part, the span of its slide index it falls in (its picture's appearance: .index, .t,
    .end), or None: as part_views gives a paragraph its slide."""
    sl = sorted(p.slides, key=lambda s: s.t)
    out: list[Optional[Any]] = []
    k = 0
    for para in p.paragraphs:
        at = para.start + 0.5
        while k + 1 < len(sl) and sl[k + 1].t <= at:
            k += 1
        out.append(sl[k] if sl and sl[k].t <= at else None)
    return out


def _screens(p: PartView) -> dict[str, Any]:
    """A part's screen timeline's screens (screens.py), {} when it has none."""
    from .. import store

    doc = store.load_screens(p.rec.delivery_id)
    return (doc.get("screens") or {}) if doc else {}


def screen_caption(s: Any, screen: Optional[dict[str, Any]]) -> str:
    """A picture's caption, as the transcript page shows it: "Slide 12 · 34:10–36:40 · Δ - Y transformation" (its
    title from the screen timeline, screens.py, when it has one)."""
    title = " ".join(str((screen or {}).get("title") or "").split())
    return " · ".join(x for x in (f"Slide {s.index}", f"{format_ts(s.t)}–{format_ts(s.end)}", title) if x)


def paragraph_text_md(p: Paragraph) -> str:
    text = md_escape(p.text).replace(r"\[unclear\]", UNCLEAR_MD)
    tag = lang_tag(p.lang)
    marks = []
    if tag:
        marks.append(f"**[{tag}]**")
    if p.star:
        marks.append("⭐")
    if p.uncertain:
        marks.append("⚠")
    return " ".join(marks + [text])


def render_day(course: Course, recordings: Iterable[Recording], transcripts: Any,
               slides: Optional[Mapping[str, Sequence[Any]]] = None,
               warnings: Optional[Mapping[str, list[dict[str, Any]]]] = None) -> str:
    recs = sorted(recordings, key=lambda r: r.start)
    parts = part_views(recs, transcripts, slides, warnings)
    first = parts[0].rec if parts else recs[0]
    kind = kind_label(first.kind)
    done = [p.transcript for p in parts if p.transcript]
    total = sum(p.rec.duration_s for p in parts)
    lines = [f"# {course.code or course_display(course)} · {kind} · {long_date(first.start.date())}", ""]
    meta = [f"**Course:** {md_escape(course_display(course))}"]
    if course.instructor:
        meta.append(f"**Lecturer:** {md_escape(course.instructor)}")
    links = " · ".join(md_link(f"Part {p.index}", p.rec.viewer_url) for p in parts)
    meta.append(f"**Recordings:** {len(parts)} part{'s' if len(parts) != 1 else ''} · {format_duration(total)}"
                + (f" — {links}" if links else ""))
    shares = language_shares(done)
    if shares:
        note = " — Turkish passages are marked **[TR]**" if any(k != "en" for k in shares) else ""
        meta.append(f"**Languages:** {format_language_shares(shares)}{note}")
    if done:
        meta.append(f"**Transcribed:** {transcribed_on(done)} · {engine_summary(done)}")
    lines += [m + "  " for m in meta]
    lines.append("")

    notes = [(p.index, w) for p in parts for w in p.warnings if w.get("level") in ("warning", "error")]
    if notes:
        lines.append("> ⚠ **Audio notes**")
        for idx, w in notes:
            lines.append(f"> - Part {idx}: {md_escape(w.get('message', ''))}")
        lines.append("")

    stars = [(p, para) for p in parts for para in p.paragraphs if para.star]
    if stars:
        lines += ["## ⭐ Highlights", ""]
        for p, para in stars[:14]:
            label = ", ".join(LABELS.get(t, t) for t in para.tags)
            tag = lang_tag(para.lang)
            lines.append(f"- **Part {p.index} · {format_ts(para.start)}** — *{label}* — "
                         f"{('[' + tag + '] ') if tag else ''}“{md_escape(excerpt(para.text, para.tags))}”")
        if len(stars) > 14:
            lines.append(f"- … and {len(stars) - 14} more marked ⭐ below.")
        lines.append("")
    lines += ["---", ""]

    for p in parts:
        lines.append(f"## Part {p.index} · {time_range(p.rec)} ({format_duration(p.rec.duration_s)})")
        lines.append("")
        if not p.transcript:
            lines += ["_Not transcribed yet._", ""]
            continue
        if not p.paragraphs:
            lines += ["_No speech was recognised in this part._", ""]
            continue
        shown_slide = None
        by_index = {s.index: s for s in p.slides}
        screens = _screens(p) if p.slides else {}
        for para, span in zip(p.paragraphs, para_spans(p)):
            if para.slide is not None and para.slide != shown_slide and para.slide in by_index:
                s = by_index[para.slide]
                cap = screen_caption(span or s, screens.get(str(s.index)))
                lines += [f"![Slide {s.index}]({s.image.replace(' ', '%20')})", "", f"*{md_escape(cap)}*", ""]
                shown_slide = para.slide
            lines.append(f"**[{format_ts(para.start)}]** {paragraph_text_md(para)}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"
