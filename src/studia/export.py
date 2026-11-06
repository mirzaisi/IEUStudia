"""AI pack: a PDF that shows every slide next to what the lecturer said while it was on screen,
plus a ZIP (PDF + LLM-friendly transcript + slide images + prompt) for ChatGPT / Claude uploads.
The PDF is rendered by Microsoft Edge in headless mode (always present on Windows 11)."""

from __future__ import annotations

import html
import logging
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from .domain import Course, Recording
from .outputs.ai_copy import compact_day
from .outputs.common import (course_display, engine_summary, format_duration, format_language_shares, format_ts,
                             kind_label, lang_tag, language_shares, long_date, time_range)
from .outputs.highlights import LABELS, excerpt
from .outputs.markdown import part_views
from .outputs.prompts import TEMPLATES, TRANSCRIPT_NOTES
from .outputs.writer import course_dir, day_stem, safe_name

log = logging.getLogger("studia")

_BROWSERS = [
    r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
    r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
    r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
]

_CSS = """
@page { size: A4; margin: 13mm 13mm 14mm 13mm; }
* { box-sizing: border-box; }
body { font-family: "Segoe UI", system-ui, sans-serif; font-size: 10pt; line-height: 1.45; color: #1d2330; }
h1 { font-size: 17pt; margin: 0 0 4pt; } h2 { font-size: 12.5pt; margin: 14pt 0 6pt; color: #312e81;
  border-bottom: 1px solid #e3e6ef; padding-bottom: 3pt; break-after: avoid; }
.meta { color: #4a5266; font-size: 9pt; margin-bottom: 8pt; } .meta b { color: #1d2330; }
.legend { background: #f4f6fb; border-radius: 6pt; padding: 6pt 9pt; font-size: 8.6pt; color: #4a5266; }
.notes { background: #fff7e6; border-left: 3pt solid #f59e0b; padding: 5pt 9pt; font-size: 9pt; margin: 8pt 0; }
.hl { margin: 6pt 0 0 0; padding-left: 14pt; } .hl li { margin: 2pt 0; }
figure { margin: 10pt 0 6pt; break-inside: avoid; } figure img { width: 100%; max-height: 118mm; object-fit: contain;
  border: 1px solid #d9dde8; border-radius: 4pt; } figcaption { font-size: 8.4pt; color: #6b7385; margin-top: 2pt; }
p { margin: 0 0 5pt; break-inside: avoid; } .ts { font-weight: 600; color: #4338ca; font-variant-numeric: tabular-nums; }
.tr { background: #ecfdf5; } .tag { font-size: 7.6pt; font-weight: 700; color: #047857; border: 1px solid #a7f3d0;
  border-radius: 3pt; padding: 0 3pt; margin-right: 3pt; } .star { color: #b45309; } .unc { color: #8a6d1d; }
.part { break-before: page; } .part.first { break-before: auto; }
"""


def find_browser() -> Optional[str]:
    for p in _BROWSERS:
        path = os.path.expandvars(p)
        if os.path.isfile(path):
            return path
    for name in ("msedge", "chrome", "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
                 "microsoft-edge"):                # the last ones: Linux
        if shutil.which(name):
            return shutil.which(name)
    return None


def html_to_pdf(html_path: Path, pdf_path: Path, timeout: int = 180) -> bool:
    browser = find_browser()
    if not browser:
        log.warning("No Edge/Chrome found; the AI pack will not contain a PDF")
        return False
    with tempfile.TemporaryDirectory(prefix="ls-pdf-") as profile:
        cmd = [browser, "--headless=new", "--disable-gpu", "--no-first-run", "--no-pdf-header-footer",
               f"--user-data-dir={profile}", f"--print-to-pdf={pdf_path}", html_path.resolve().as_uri()]
        try:
            subprocess.run(cmd, capture_output=True, timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            log.warning("PDF rendering timed out")
            return False
    return pdf_path.exists() and pdf_path.stat().st_size > 0


def _p(para: Any) -> str:
    tag = lang_tag(para.lang)
    cls = ' class="tr"' if tag else ""
    marks = (f'<span class="tag">{tag}</span>' if tag else "") + ('<span class="star">⭐ </span>' if para.star else "")
    marks += '<span class="unc">⚠ </span>' if para.uncertain else ""
    return f'<p{cls}><span class="ts">[{format_ts(para.start)}]</span> {marks}{html.escape(para.text)}</p>'


def render_pack_html(course: Course, recordings: Sequence[Recording], transcripts: Mapping[str, Any],
                     slides: Mapping[str, Sequence[Any]], image_for: Mapping[str, str]) -> str:
    recs = sorted(recordings, key=lambda r: r.start)
    parts = [p for p in part_views(recs, transcripts, slides) if p.transcript]
    first = parts[0].rec
    done = [p.transcript for p in parts]
    title = f"{course.code or course_display(course)} · {kind_label(first.kind)} · {long_date(first.start.date())}"
    out = [f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(title)}</title>",
           f"<style>{_CSS}</style></head><body>", f"<h1>{html.escape(title)}</h1>"]
    meta = [f"<b>Course:</b> {html.escape(course_display(course))}"]
    if course.instructor:
        meta.append(f"<b>Lecturer:</b> {html.escape(course.instructor)}")
    meta.append(f"<b>Recordings:</b> {len(parts)} part(s) · {format_duration(sum(p.rec.duration_s for p in parts))}")
    shares = language_shares(done)
    if shares:
        meta.append(f"<b>Languages:</b> {format_language_shares(shares)}")
    meta.append(f"<b>Made with:</b> {html.escape(engine_summary(done))}")
    out.append(f"<div class='meta'>{' · '.join(meta)}</div>")
    out.append("<div class='legend'>Each slide is followed by what the lecturer said while it was on screen. "
               "Timestamps are [mm:ss] from the start of each part. <b>TR</b> = spoken in Turkish, ⭐ = emphasised "
               "by the lecturer, ⚠ = unclear audio. The transcript is automatic and may contain recognition errors."
               "</div>")
    notes = [(p.index, w) for p in parts for w in p.warnings if w.get("level") in ("warning", "error")]
    if notes:
        out.append("<div class='notes'><b>Audio notes:</b><br>" + "<br>".join(
            f"Part {i}: {html.escape(w.get('message', ''))}" for i, w in notes) + "</div>")
    stars = [(p, para) for p in parts for para in p.paragraphs if para.star]
    if stars:
        out.append("<h2>⭐ Highlights</h2><ul class='hl'>")
        for p, para in stars[:16]:
            label = ", ".join(LABELS.get(t, t) for t in para.tags)
            out.append(f"<li><b>Part {p.index} · {format_ts(para.start)}</b> — <i>{label}</i> — "
                       f"{html.escape(excerpt(para.text, para.tags))}</li>")
        out.append("</ul>")
    for n, p in enumerate(parts):
        out.append(f"<section class='part{' first' if n == 0 else ''}'>"
                   f"<h2>Part {p.index} · {time_range(p.rec)} ({format_duration(p.rec.duration_s)})</h2>")
        by_index = {s.index: s for s in p.slides}
        current = None
        for para in p.paragraphs:
            if para.slide is not None and para.slide != current and para.slide in by_index:
                s = by_index[para.slide]
                src = image_for.get(s.path) or s.image
                out.append(f"<figure><img src='{html.escape(src)}'><figcaption>Slide {s.index} · on screen from "
                           f"{format_ts(s.t)}</figcaption></figure>")
                current = para.slide
            out.append(_p(para))
        out.append("</section>")
    out.append("</body></html>")
    return "\n".join(out)


def build_ai_pack(output_root: Path, course: Course, all_courses: Sequence[Course], recordings: Sequence[Recording],
                  transcripts: Mapping[str, Any], slides: Optional[Mapping[str, Sequence[Any]]] = None
                  ) -> dict[str, Path]:
    """Write ``<course>/AI packs/<day> - AI pack.pdf`` and ``... .zip``; return their paths."""
    recs = sorted(recordings, key=lambda r: r.start)
    slides = slides or {}
    stem = day_stem(course, recs[0].start.date(), recs[0].kind)
    dest = course_dir(output_root, course, all_courses) / "AI packs"
    dest.mkdir(parents=True, exist_ok=True)
    pdf_path = dest / safe_name(f"{stem} - AI pack.pdf")
    zip_path = dest / safe_name(f"{stem} - AI pack.zip")
    with tempfile.TemporaryDirectory(prefix="ls-pack-") as tmp:
        stage = Path(tmp)
        (stage / "slides").mkdir()
        image_for: dict[str, str] = {}
        for sl in slides.values():
            for s in sl:
                if s.path and os.path.isfile(s.path) and s.path not in image_for:
                    name = f"slides/{Path(s.path).name}"
                    shutil.copy2(s.path, stage / name)
                    image_for[s.path] = name
        html_path = stage / "pack.html"
        html_path.write_text(render_pack_html(course, recs, transcripts, slides, image_for), encoding="utf-8")
        pdf_ok = html_to_pdf(html_path, stage / "pack.pdf")
        text = compact_day(course, recs, transcripts, slides)
        refs = "\n".join(f"- {name}" for name in sorted(set(image_for.values())))
        (stage / "transcript.md").write_text(
            text + (f"\n\n## Slide images\n(slide N = slides/<part>-NN.jpg)\n{refs}\n" if refs else "\n"), encoding="utf-8")
        (stage / "PROMPT.md").write_text(TEMPLATES["notes"]["prompt"] + TRANSCRIPT_NOTES + "\n", encoding="utf-8")
        tmp_zip = zip_path.with_name(zip_path.name + ".tmp")
        with zipfile.ZipFile(tmp_zip, "w", compression=zipfile.ZIP_DEFLATED) as z:
            if pdf_ok:
                z.write(stage / "pack.pdf", f"{stem}.pdf")
            z.write(stage / "transcript.md", "transcript.md")
            z.write(stage / "PROMPT.md", "PROMPT.md")
            for name in sorted(set(image_for.values())):
                z.write(stage / name, name, compress_type=zipfile.ZIP_STORED)
        # an earlier pack may be open in a program that locks it (a PDF viewer): the message names the file to close
        try:
            os.replace(tmp_zip, zip_path)
        except PermissionError as exc:
            tmp_zip.unlink(missing_ok=True)
            raise PermissionError(exc.errno, exc.strerror, str(zip_path)) from exc
        if pdf_ok:
            try:
                shutil.copy2(stage / "pack.pdf", pdf_path)
            except PermissionError as exc:
                raise PermissionError(exc.errno, exc.strerror, str(pdf_path)) from exc
        else:                                   # an earlier export's PDF is not this one's (it may lack parts)
            try:
                pdf_path.unlink(missing_ok=True)
            except OSError:
                log.warning("The earlier AI pack PDF could not be removed", exc_info=True)
    out = {"zip": zip_path, "folder": dest}
    if pdf_ok:                                  # no "pdf": the page says the PDF couldn't be made
        out["pdf"] = pdf_path
    return out
