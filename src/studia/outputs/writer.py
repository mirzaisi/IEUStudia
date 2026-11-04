"""Write transcript files into the user's output folder (atomic, idempotent)."""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from ..domain import Course, Recording
from .common import as_transcript_map, group_days, kind_label, select_parts, weekday_short
from .markdown import render_day
from .pack import render_study_pack
from .prompts import PROMPTS_MD
from .srt import render_srt

log = logging.getLogger("studia")


def safe_name(name: str, limit: int = 150) -> str:
    n = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name or "")
    n = re.sub(r"\s+", " ", n).strip().rstrip(". ")
    return n[:limit].rstrip(". ") or "Untitled"


def course_folder_name(course: Course, all_courses: Sequence[Course]) -> str:
    code = (course.code or course.name or f"Course {course.id}").strip()
    twins = [c for c in all_courses if (c.code or c.name) == (course.code or course.name)]
    if len(twins) > 1:
        code += f" (sec {course.section})" if course.section else f" ({course.id})"
    return safe_name(code)


def day_stem(course: Course, date: dt.date, kind: str) -> str:
    return safe_name(f"{course.code or course.name} - {date.isoformat()} {weekday_short(date)} - {kind_label(kind)}")


def day_file_name(course: Course, date: dt.date, kind: str) -> str:
    return day_stem(course, date, kind) + ".md"


def atomic_write(path: Path, text: str) -> None:
    """Write to a temporary file, then swap it in. A sync client, an antivirus or an editor can hold the old file
    open for a moment: the swap is tried again for about two seconds."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:      # on the disk before the swap (a power cut)
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    for attempt in range(8):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 7:
                try:
                    tmp.unlink()
                except OSError:
                    pass
                raise
            time.sleep(0.25)


def course_dir(output_root: Path, course: Course, all_courses: Sequence[Course]) -> Path:
    return Path(output_root) / course_folder_name(course, all_courses)


def ensure_prompts(output_root: Path) -> Path:
    p = Path(output_root) / "PROMPTS.md"
    if not p.exists() or p.read_text(encoding="utf-8") != PROMPTS_MD:
        atomic_write(p, PROMPTS_MD)
    return p


def write_day(output_root: Path, course: Course, all_courses: Sequence[Course], recordings: Sequence[Recording],
              transcripts: Mapping[str, Any], all_course_recordings: Sequence[Recording],
              all_course_transcripts: Mapping[str, Any], write_srt: bool = True,
              slides: Optional[Mapping[str, Sequence[Any]]] = None,
              warnings: Optional[Mapping[str, list[dict[str, Any]]]] = None, pack: bool = True) -> dict[str, Path]:
    """Write the day file (+ subtitles) and refresh the course study pack and PROMPTS.md. ``pack=False``: leave the
    pack to the caller (rewriting every day of a course writes it once, after the last day)."""
    folder = course_dir(output_root, course, all_courses)
    recs = sorted(recordings, key=lambda r: r.start)
    first = recs[0]
    md_path = folder / day_file_name(course, first.start.date(), first.kind)
    atomic_write(md_path, render_day(course, recs, transcripts, slides, warnings))
    if write_srt:
        stem = day_stem(course, first.start.date(), first.kind)
        tmap = as_transcript_map(transcripts)
        for i, rec in enumerate(select_parts(recs, tmap), start=1):        # numbered as the day file's parts
            t = tmap.get(rec.delivery_id)
            if t:
                atomic_write(folder / "subtitles" / f"{stem} - Part {i}.srt", render_srt(t.segments))
    out = {"md": md_path, "folder": folder}
    if pack:
        out["pack"] = write_pack(output_root, course, all_courses, all_course_recordings, all_course_transcripts,
                                 warnings)
    ensure_prompts(output_root)
    return out


def write_pack(output_root: Path, course: Course, all_courses: Sequence[Course], recordings: Sequence[Recording],
               transcripts: Mapping[str, Any], warnings: Any = None) -> Path:
    folder = course_dir(output_root, course, all_courses)
    days = [(d, k, rs, transcripts) for d, k, rs in group_days(recordings)
            if any(r.delivery_id in transcripts for r in rs)]
    path = folder / safe_name(f"{course.code or course.name} - Study pack.md")
    atomic_write(path, render_study_pack(course, days, warnings))
    return path


def write_local(output_root: Path, name: str, transcript: Any) -> Path:
    """A transcript of a local media file (no course/recording metadata)."""
    from .paragraphs import build_paragraphs
    from .common import format_ts, lang_tag, md_escape

    folder = Path(output_root) / "Local files"
    path = folder / (safe_name(Path(name).stem) + ".md")
    source = f"Source: `{Path(name)}`"
    if path.exists() and source not in path.read_text(encoding="utf-8", errors="replace"):
        # another file of the same name (from another folder, or written by an earlier version, which didn't say
        # which file it came from): keep both
        tag = hashlib.sha1(str(Path(name)).lower().encode("utf-8")).hexdigest()[:6]
        path = folder / (safe_name(f"{Path(name).stem} ({Path(name).parent.name or tag})", 140) + ".md")
        if path.exists() and source not in path.read_text(encoding="utf-8", errors="replace"):
            path = folder / (safe_name(f"{Path(name).stem} ({tag})", 140) + ".md")
    lines = [f"# {md_escape(Path(name).stem)}", "", f"Transcribed {dt.date.today().isoformat()} with IEUStudia.",
             "", source, ""]
    for para in build_paragraphs(transcript.segments):
        tag = lang_tag(para.lang)
        lines.append(f"**[{format_ts(para.start)}]** {('**[' + tag + ']** ') if tag else ''}{md_escape(para.text)}")
        lines.append("")
    atomic_write(path, "\n".join(lines))
    return path
