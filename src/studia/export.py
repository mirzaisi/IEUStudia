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
from .outputs.prompts import ai_prompt
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

# The AI pack's PDF in the app's document look (the study pack's stylesheet and the exports' parts, outputs/doc.py):
# the slides large, each passage under the slide on screen when it began
_CSS = """
.xp .sp-fig { margin: .9em auto .7em; }
.xp .sp-fig img { max-width: 100%; max-height: 118mm; }
.xp p.xp-pa { break-inside: avoid; }
.xp .xp-star { color: #b45309; }
.xp .xp-unc { color: #8a6d1d; }
.xp .xp-hl li { margin: .2em 0; }
"""


def _pack_css() -> str:
    from .outputs.doc import EXTRA_CSS
    from .studypack import CSS_FILE

    return CSS_FILE.read_text(encoding="utf-8") + EXTRA_CSS + _CSS


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
    cls = "xp-pa xp-tr" if tag else "xp-pa"
    marks = (f'<span class="xp-tag">{tag}</span>' if tag else "") + ('<span class="xp-star">⭐ </span>' if para.star else "")
    marks += '<span class="xp-unc">⚠ </span>' if para.uncertain else ""
    return f'<p class="{cls}"><span class="xp-ts">[{format_ts(para.start)}]</span>{marks}{html.escape(para.text)}</p>'


def render_pack_html(course: Course, recordings: Sequence[Recording], transcripts: Mapping[str, Any],
                     slides: Mapping[str, Sequence[Any]], image_for: Mapping[str, str]) -> str:
    """The AI pack's page to print: a cover with what it is, the lecturer's highlights, then each part with its slides
    and what was said while each was on screen, in the app's document look (as the Export menu's documents)."""
    recs = sorted(recordings, key=lambda r: r.start)
    parts = [p for p in part_views(recs, transcripts, slides) if p.transcript]
    first = parts[0].rec
    done = [p.transcript for p in parts]
    esc = lambda s: html.escape(str(s), quote=False)       # noqa: E731
    title = f"{course.code or course_display(course)} · {kind_label(first.kind)} · {long_date(first.start.date())}"
    out = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>{esc(title)}</title>",
           f"<style>{_pack_css()}</style></head><body class='sp-page'><article class='sp-doc xp'>",
           f"<header class='sp-cover'><div class='sp-kicker'>{esc(course.code or course.name)} · AI pack</div>"
           f"<h1>{esc(title)}</h1>"]
    meta = [f"<b>Course:</b> {esc(course_display(course))}"]
    if course.instructor:
        meta.append(f"<b>Lecturer:</b> {esc(course.instructor)}")
    meta.append(f"<b>Recordings:</b> {len(parts)} part{'s' if len(parts) != 1 else ''} · "
                f"{format_duration(sum(p.rec.duration_s for p in parts))}")
    shares = language_shares(done)
    if shares:
        meta.append(f"<b>Languages:</b> {format_language_shares(shares)}")
    meta.append(f"<b>Made with:</b> {esc(engine_summary(done))}")
    out += [f"<div class='xp-meta'>{m}</div>" for m in meta]
    out.append("</header><p class='xp-small'>Each slide is followed by what the lecturer said while it was on screen. "
               "Times are from the start of each part. TR marks a passage in Turkish, ⭐ what the lecturer stressed, ⚠ "
               "a passage the speech recognition was unsure of. The transcript is automatic, so now and then a word is "
               "wrong.</p>")
    notes = [(p.index, w) for p in parts for w in p.warnings if w.get("level") in ("warning", "error")]
    if notes:
        out.append("<div class='sp-alert sp-alert-warning' role='note'><div class='sp-alert-h'><span class='sp-alert-i'>"
                   "!</span><b>Audio notes</b></div><ul>" + "".join(
                       f"<li>Part {i}: {esc(w.get('message', ''))}</li>" for i, w in notes) + "</ul></div>")
    stars = [(p, para) for p in parts for para in p.paragraphs if para.star]
    if stars:
        out.append("<h2 class='xp-h1'>⭐ Highlights</h2><ul class='xp-hl'>")
        for p, para in stars[:16]:
            label = ", ".join(LABELS.get(t, t) for t in para.tags)
            out.append(f"<li><b>Part {p.index} · {format_ts(para.start)}</b> · <i>{esc(label)}</i>: "
                       f"“{esc(excerpt(para.text, para.tags))}”</li>")
        out.append("</ul>")
    for n, p in enumerate(parts):
        out.append(f"<section><h2 class='xp-h1{' xp-page' if n or stars else ''}'>Part {p.index} · {time_range(p.rec)} "
                   f"({format_duration(p.rec.duration_s)})</h2>")
        # each passage under the slide on screen when it began (as part_views), its figure captioned with the time
        # that showing began: a slide shown twice has an entry for each showing (and one for each build-up step)
        sl, k, current = p.slides, 0, None
        for para in p.paragraphs:
            while k + 1 < len(sl) and sl[k + 1].t <= para.start + 0.5:
                k += 1
            if para.slide is not None and para.slide != current and sl and sl[k].index == para.slide:
                j = k
                while j and sl[j - 1].index == sl[k].index:
                    j -= 1
                s = sl[j]
                src = image_for.get(sl[k].path) or sl[k].image
                out.append(f"<figure class='sp-fig sp-frame'><img src='{html.escape(src)}' alt=''><figcaption>Slide "
                           f"{s.index} · on screen from {format_ts(s.t)}</figcaption></figure>")
                current = para.slide
            out.append(_p(para))
        out.append("</section>")
    out.append("</article></body></html>")
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
        (stage / "PROMPT.md").write_bytes((ai_prompt("notes") + "\n").encode("utf-8"))
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
