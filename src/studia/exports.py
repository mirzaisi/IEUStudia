"""Easy exports from where the student is (the Export menu): a lecture's transcript, its notes (study notes, exam
focus, flashcards) or its AI pack; a week or the whole course, as one document or as a zip with a file for each
lecture; a chat. As PDF (for reading: the app's document look, printed by Edge as study packs are), Word (for
editing: outputs/docx.py), Markdown, plain text or subtitles (SRT, VTT), saved where the student picks in Windows'
save dialog (it starts in the course's folder, with a good name), or copied as text. Everything comes from what
IEUStudia has saved: nothing is fetched.

The documents are English, like IEUStudia's other files; what the AI wrote stays as it wrote it. The format and the
options used last are remembered per student (Settings.export_prefs), for each place (a lecture, a course, a chat)."""

from __future__ import annotations

import dataclasses as dc
import datetime as dt
import logging
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Callable, Optional

from . import store
from .domain import Course, Recording
from .i18n import t
from .outputs import doc as D
from .outputs.common import (course_display, engine_summary, format_duration, format_language_shares, format_ts,
                             kind_label, lang_tag, language_shares, long_date, normalize_lang, select_parts,
                             time_range, transcribed_on, as_transcript_map)
from .outputs.highlights import LABELS, excerpt
from .outputs.markdown import PartView, part_views
from .outputs.writer import MAX_PATH_CHARS, MIN_STEM, course_dir, day_stem

log = logging.getLogger("studia")

DOCS = ("pdf", "docx", "md", "txt")
SUBS = ("srt", "vtt")
EXT = {"pdf": ".pdf", "docx": ".docx", "md": ".md", "txt": ".txt", "srt": ".srt", "vtt": ".vtt"}
PLACES = ("day", "course", "chat")
NOTE_NAMES = {"notes": "Study notes", "exam": "Exam focus", "flashcards": "Flashcards"}    # English: names, titles
LEGEND = ("Times are from the start of each part. [TR] marks a passage in Turkish, ⭐ what the lecturer stressed, ⚠ a "
          "passage the speech recognition was unsure of. The transcript is automatic, so now and then a word is wrong.")
PACK_LEGEND = "Each slide is followed by what the lecturer said while it was on screen. " + LEGEND
NAME_MAX = 120                     # a file name the save dialog suggests (its folder's path aside)
Progress = Callable[[int, int], None]


class ExportError(RuntimeError):
    """Why it can't be exported, in words for the student."""


@dc.dataclass
class Job:
    """One export the student asked for, checked before the save dialog opens."""

    place: str                     # "day" | "course" | "chat"
    key: str                       # the class day's key, the course's id or the chat's id
    what: str                      # day: transcript · notes:<kind> · pack; course: all · week:<n>; chat: chat
    fmt: str                       # pdf · docx · md · txt · srt · vtt
    course: Course
    folder: Path                   # where the save dialog starts
    name: str                      # the name it suggests
    ext: str                       # the file's ending (".zip" for a zip)
    zip: bool = False              # a course: a file for each lecture, in a zip
    notes: bool = False            # a course: the lectures' AI notes too
    lectures: int = 1
    wait: float = 1.0              # about how many seconds it takes to make

    @property
    def scope(self) -> str:
        return f"{self.place}:{self.key}"


# ------------------------------------------------------------------ what the student picked
def _split_scope(scope: str) -> tuple[str, str]:
    place, _, key = (scope or "").partition(":")
    if place not in PLACES or not key:
        raise ExportError(t("This can't be exported."))
    return place, key


def can_print() -> bool:
    """Can a PDF be made on this PC (Edge or Chrome; on Linux run from source, Qt WebEngine)?"""
    from .export import find_browser

    return bool(find_browser()) or (sys.platform.startswith("linux") and not getattr(sys, "frozen", False))


def _no_pdf() -> str:
    return t("The PDF couldn't be made: Microsoft Edge wasn't found on this PC. Save it as Word instead.")


def plan(s: Any, scope: str, what: str, fmt: str, opts: Optional[dict[str, Any]] = None) -> Job:
    """The export of ``what`` from ``scope`` ("day:<key>", "course:<id>", "chat:<id>") as ``fmt``: checked (what it
    needs is there), named, and where it is suggested. Raises ExportError in plain words."""
    place, key = _split_scope(scope)
    opts = opts if isinstance(opts, dict) else {}
    if fmt not in EXT:
        raise ExportError(t("This can't be exported."))
    if fmt == "pdf" and not can_print():
        raise ExportError(_no_pdf())
    if place == "day":
        return _plan_day(s, key, what, fmt)
    if place == "course":
        return _plan_course(s, key, what, fmt, opts)
    return _plan_chat(s, key, fmt)


def _folder(s: Any, course: Course) -> Path:
    return course_dir(s.output_root, course, s.lib.courses(True))


def file_name(folder: Path, stem: str, ext: str) -> str:
    """The name the save dialog suggests: Windows-safe with its Turkish letters, a long one cut at a word, and the
    whole path within Windows' limit in ``folder`` (with room for the ".tmp" it is written as first)."""
    from .course_export import clean_name

    room = min(NAME_MAX, MAX_PATH_CHARS - 4 - len(str(folder)) - 1) - len(ext)
    return clean_name(stem, room if room >= MIN_STEM else 40) + ext


def _day(s: Any, key: str) -> tuple[Course, list[Recording]]:
    try:
        return s.day(key)
    except KeyError as exc:
        raise ExportError(t("This class day no longer exists.")) from exc


def _plan_day(s: Any, key: str, what: str, fmt: str) -> Job:
    course, recs = _day(s, key)
    trans = store.load_transcripts([r.delivery_id for r in recs])
    if not trans:
        raise ExportError(t("Transcribe this class day first."))
    first = min(recs, key=lambda r: r.start)
    stem = day_stem(course, first.start.date(), first.kind)
    folder = _folder(s, course)
    wait = 1.0
    if fmt in SUBS:
        if what != "transcript":
            raise ExportError(t("Subtitles are made from the transcript only."))
        ext = EXT[fmt] if len(subtitles(recs, trans, fmt)) <= 1 else ".zip"
        return Job("day", key, what, fmt, course, folder, file_name(folder, f"{stem} - Subtitles", ext), ext)
    if what == "transcript":
        suffix = "Transcript"
    elif what == "pack":
        suffix = "AI pack"
        wait = 6.0 if fmt == "pdf" else 2.0
    elif what.startswith("notes:") and what[6:] in NOTE_NAMES:
        if not s.day_files(key).get(what):
            raise ExportError(t("These notes are no longer in the course's folder (moved or deleted). Write them "
                                "again with Study notes."))
        suffix = NOTE_NAMES[what[6:]]
    else:
        raise ExportError(t("This can't be exported."))
    if fmt == "pdf":
        wait = max(wait, 4.0)
    return Job("day", key, what, fmt, course, folder, file_name(folder, f"{stem} - {suffix}", EXT[fmt]), EXT[fmt],
               wait=wait)


def _scope_days(s: Any, course_id: str, what: str,
                today: Optional[dt.date] = None) -> tuple[str, str, list[Any], list[Any]]:
    """A course's class days for ``what`` ("all" or "week:<n>"): (its label, its topic, the transcribed days, those
    not transcribed yet), built as Export for AI chat builds its weeks (course_export.plan_course)."""
    from .course_export import Choice, plan_course

    today = today or dt.date.today()
    if what == "all":
        plan_ = plan_course(s.lib, s.settings, course_id, Choice(), today)
        label, topic = "All lectures", ""
        days = [d for w in (plan_.all_weeks if plan_ else []) for d in w.days] + (plan_.loose if plan_ else [])
    elif what.startswith("week:") and what[5:].isdigit():
        n = int(what[5:])
        plan_ = plan_course(s.lib, s.settings, course_id, Choice(weeks=[n]), today)
        label = f"Week {n}"
        topic = next((w.topic for w in plan_.weeks), "") if plan_ else ""
        days = [d for w in (plan_.weeks if plan_ else []) for d in w.days]
    else:
        raise ExportError(t("This can't be exported."))
    if plan_ is None:
        raise ExportError(t("This course is no longer in your list."))
    seen: dict[str, Any] = {}
    for d in sorted(days, key=lambda d: (d.date, d.kind)):
        seen.setdefault(d.key, d)
    done = [d for d in seen.values() if d.done]
    todo = [d for d in seen.values() if not d.done and d.todo]
    return label, topic, done, todo


def _plan_course(s: Any, course_id: str, what: str, fmt: str, opts: dict[str, Any]) -> Job:
    course = s.lib.get_course(course_id)
    if course is None:
        raise ExportError(t("This course is no longer in your list."))
    label, _, days, _ = _scope_days(s, course_id, what)
    if not days:
        raise ExportError(t("No lecture of this week is transcribed yet.") if what != "all" else
                          t("No lecture of this course is transcribed yet."))
    zipped = bool(opts.get("zip")) or fmt in SUBS
    notes = bool(opts.get("notes")) and fmt in DOCS
    folder = _folder(s, course)
    code = course.code or course.name or "Course"
    stem = f"{code} - {label}" + ("" if what == "all" else " - Lectures") + (" - Subtitles" if fmt in SUBS else "")
    ext = ".zip" if zipped else EXT[fmt]
    n = len(days)
    wait = {"pdf": (3.0 * n) if zipped else 4.0 + 0.8 * n, "docx": 1.0 + 0.3 * n}.get(fmt, 1.0 + 0.05 * n)
    return Job("course", course_id, what, fmt, course, folder, file_name(folder, stem, ext), ext, zipped, notes, n,
               wait)


def _chat_row(s: Any, chat_id: str) -> tuple[dict[str, Any], Course]:
    chat = next(iter(s.lib.chats(chat_id)), None)              # (with its first question)
    if chat is None:
        raise ExportError(t("This chat no longer exists."))
    course = s.lib.get_course(chat["course_id"])
    if course is None:
        raise ExportError(t("This course is no longer in your list."))
    return chat, course


def _chat_title(s: Any, chat: dict[str, Any]) -> str:
    try:
        return s.chat.summary(chat)["title"] or "New chat"
    except Exception:  # noqa: BLE001 - (a chat service that can't say: its saved title, else its first question)
        q = " ".join((chat.get("first_question") or "").split())
        return chat.get("title") or (q if len(q) <= 64 else q[:61].rstrip() + "…") or "New chat"


def _plan_chat(s: Any, chat_id: str, fmt: str) -> Job:
    if fmt not in DOCS:
        raise ExportError(t("This can't be exported."))
    chat, course = _chat_row(s, chat_id)
    if not s.lib.chat_messages(chat_id):
        raise ExportError(t("This chat has no messages yet."))
    stamp = (chat.get("created_at") or "")[:10]
    title = _chat_title(s, chat)
    folder = _folder(s, course)
    return Job("chat", chat_id, "chat", fmt, course, folder,
               file_name(folder, f"{stamp} - {title}" if stamp else title, EXT[fmt]), EXT[fmt],
               wait=4.0 if fmt == "pdf" else 1.0)


# ------------------------------------------------------------------ a lecture as a document
def _lecture_title(course: Course, recs: list[Recording]) -> str:
    first = min(recs, key=lambda r: r.start)
    return f"{course.code or course_display(course)} · {kind_label(first.kind)} · {long_date(first.start.date())}"


def _meta(course: Course, parts: list[PartView], with_course: bool = True) -> list[list[D.Run]]:
    done = [p.transcript for p in parts if p.transcript]
    meta = [D.label("Course", course_display(course))] if with_course else []
    if course.instructor and with_course:
        meta.append(D.label("Lecturer", course.instructor))
    total = sum(p.rec.duration_s for p in parts)
    meta.append(D.label("Recordings", f"{len(parts)} part{'s' if len(parts) != 1 else ''} · {format_duration(total)}"))
    shares = language_shares(done)
    if shares:
        meta.append(D.label("Languages", format_language_shares(shares)))
    if done:
        meta.append(D.label("Transcribed", f"{transcribed_on(done)} · {engine_summary(done)}"))
    return meta


def passage(para: Any) -> D.Para:
    """A passage: its time, its language when not English, ⭐ when stressed, ⚠ when unsure, then what was said."""
    tag = lang_tag(para.lang)
    runs = [D.R(f"[{format_ts(para.start)}]", kind="time"), D.R(" ")]
    if tag:
        runs += [D.R(tag, kind="tag"), D.R(" ")]
    runs.append(D.R(("⭐ " if para.star else "") + ("⚠ " if para.uncertain else "") + para.text))
    return D.Para(runs, lang=normalize_lang(para.lang) if tag else "")


def _audio_notes(parts: list[PartView]) -> list[Any]:
    notes = [(p.index, w) for p in parts for w in p.warnings if w.get("level") in ("warning", "error")]
    if not notes:
        return []
    return [D.Box("warning", [D.R("Audio notes")],
                  [D.Items([D.Item([D.R(f"Part {i}: {w.get('message', '')}")]) for i, w in notes])])]


def _highlights(parts: list[PartView], level: int, most: int = 14) -> list[Any]:
    stars = [(p, para) for p in parts for para in p.paragraphs if para.star]
    if not stars:
        return []
    items = []
    for p, para in stars[:most]:
        tag = lang_tag(para.lang)
        items.append(D.Item([D.R(f"Part {p.index} · {format_ts(para.start)}", bold=True), D.R(" · "),
                             D.R(", ".join(LABELS.get(x, x) for x in para.tags), italic=True),
                             D.R(f": {('[' + tag + '] ') if tag else ''}“{excerpt(para.text, para.tags)}”")]))
    if len(stars) > most:
        items.append(D.Item([D.R(f"… and {len(stars) - most} more marked ⭐ below.")]))
    return [D.Heading(level, [D.R("⭐ Highlights")]), D.Items(items)]


def _part(p: PartView, level: int, slides: bool = False) -> list[Any]:
    out: list[Any] = [D.Heading(level, [D.R(f"Part {p.index} · {time_range(p.rec)} "
                                            f"({format_duration(p.rec.duration_s)})")])]
    if p.rec.viewer_url:
        out.append(D.Para([D.R("Watch on Panopto", link=p.rec.viewer_url)], style="small"))
    if not p.transcript:
        return out + [D.Para([D.R("Not transcribed yet.", italic=True)])]
    if not p.paragraphs:
        return out + [D.Para([D.R("No speech was recognised in this part.", italic=True)])]
    sl, k, current = list(p.slides), 0, None
    for para in p.paragraphs:
        if slides:          # each passage under the slide on screen when it began (as the AI pack's PDF always had)
            while k + 1 < len(sl) and sl[k + 1].t <= para.start + 0.5:
                k += 1
            if para.slide is not None and para.slide != current and sl and sl[k].index == para.slide:
                j = k
                while j and sl[j - 1].index == sl[k].index:
                    j -= 1
                if sl[k].path and os.path.isfile(sl[k].path):
                    caption = f"Slide {sl[j].index} · on screen from {format_ts(sl[j].t)}"
                    out.append(D.Figure(sl[k].path, [D.R(caption)]))
                current = para.slide
        out.append(passage(para))
    return out


def _load(s: Any, key: str) -> tuple[Course, list[Recording], list[PartView], dict[str, Any], dict[str, Any]]:
    course, recs = _day(s, key)
    recs = sorted(recs, key=lambda r: r.start)
    ids = [r.delivery_id for r in recs]
    trans = store.load_transcripts(ids)
    slides = {i: store.load_slides(i) for i in ids}
    warnings = {i: s.lib.warnings(i) for i in ids}
    return course, recs, part_views(recs, trans, slides, warnings), trans, slides


def transcript_doc(s: Any, key: str, pack: bool = False) -> D.Doc:
    """A class day's transcript (``pack``: its AI pack, each slide shown before what was said while it was on
    screen), in parts, with its highlights and audio notes."""
    course, recs, parts, _, _ = _load(s, key)
    blocks = [D.Para([D.R(PACK_LEGEND if pack else LEGEND)], style="small")] + _audio_notes(parts)
    blocks += _highlights(parts, 1, 16 if pack else 14)
    for p in parts:
        blocks += _part(p, 1, slides=pack)
    return D.Doc(_lecture_title(course, recs), kicker=f"{course.code or course.name} · "
                 + ("AI pack" if pack else "Transcript"), meta=_meta(course, parts), blocks=blocks)


def notes_doc(s: Any, key: str, kind: str) -> D.Doc:
    course, recs = _day(s, key)
    md = _notes_md(s, key, kind)
    if md is None:
        raise ExportError(t("These notes are no longer in the course's folder (moved or deleted). Write them again "
                            "with Study notes."))
    return D.Doc(_lecture_title(course, recs), kicker=f"{course.code or course.name} · {NOTE_NAMES[kind]}",
                 subtitle=course_display(course), blocks=[D.Markdown(md, top=1, base=_notes_dir(s, key, kind))],
                 lang="tr" if D.turkish(md) else "en")


def _notes_md(s: Any, key: str, kind: str) -> Optional[str]:
    path = s.day_files(key).get(f"notes:{kind}")
    try:
        return Path(path).read_text(encoding="utf-8") if path else None
    except OSError:
        return None


def _notes_dir(s: Any, key: str, kind: str) -> str:
    """The folder of a lecture's notes: the lecture slides they show are linked from there."""
    path = s.day_files(key).get(f"notes:{kind}")
    return str(Path(path).parent) if path else ""


def pack_text(s: Any, key: str) -> str:
    """The AI pack as text (Markdown or plain): the study-notes prompt, then the lecture as compact text with the
    slide on screen at each passage, ready to paste or upload into ChatGPT or Claude."""
    from .outputs.ai_copy import build_ai_text

    course, recs, _, trans, slides = _load(s, key)
    lang = getattr(s, "doc_language", None)          # (the notes' language, as the app's own notes: Settings → AI)
    return build_ai_text("notes", course, [(recs, trans)], slides,
                         lang=(lambda body: lang(course, body)) if lang else None)


# ------------------------------------------------------------------ a week or the whole course
def _lecture_label(d: Any) -> str:
    return f"{long_date(d.date)} · {kind_label(d.kind)}" + (f" ({d.other_text}'s lecture)" if d.other_text else "")


def course_doc(s: Any, course_id: str, what: str, notes: bool) -> D.Doc:
    """The lectures of a week (or of the whole course) in one document: a list of them, then each on a new page
    with its parts (and, with ``notes``, the AI notes written for it)."""
    course = s.lib.get_course(course_id)
    if course is None:
        raise ExportError(t("This course is no longer in your list."))
    label, topic, days, todo = _scope_days(s, course_id, what)
    meta = [D.label("Course", course_display(course))]
    if course.instructor:
        meta.append(D.label("Lecturer", course.instructor))
    if course.term:
        meta.append(D.label("Term", course.term))
    meta.append(D.label("Lectures", f"{len(days)} transcribed"
                        + (f", {len(todo)} not transcribed yet" if todo else "")))
    contents = [D.Item([D.R(_lecture_label(d))]) for d in days]
    contents += [D.Item([D.R(_lecture_label(d)), D.R(" (not transcribed yet)", italic=True)]) for d in todo]
    blocks: list[Any] = [D.Heading(1, [D.R("Lectures")]), D.Items(contents)]
    for d in days:
        _, recs, parts, _, _ = _load(s, d.key)
        blocks.append(D.Heading(1, [D.R(_lecture_label(d))], page=True))
        blocks.append(D.Para([D.R(" · ".join(D.plain(m) for m in _meta(course, parts, with_course=False)))],
                             style="small"))
        blocks += _audio_notes(parts)
        for p in parts:
            blocks += _part(p, 2)
        if notes:                      # each under a label (the notes have headings of their own)
            for kind, name in NOTE_NAMES.items():
                md = _notes_md(s, d.key, kind)
                if md:
                    blocks += [D.Para([D.R(name)], style="who"), D.Markdown(md, top=2, base=_notes_dir(s, d.key, kind))]
    title = f"{course.code or course.name} · {label}"
    return D.Doc(title, kicker="Lectures and notes" if notes else "Lectures",
                 subtitle=" · ".join(x for x in (topic, f"made with IEUStudia on {long_date(dt.date.today())}") if x),
                 meta=meta, blocks=blocks)


# ------------------------------------------------------------------ a chat
def chat_doc(s: Any, chat_id: str) -> D.Doc:
    """A chat as it went: what you asked, what the AI answered (as it wrote it), the app's notices with it."""
    from . import ai, notices
    from .outputs.mathmd import dollar_math

    chat, course = _chat_row(s, chat_id)
    msgs = s.lib.chat_messages(chat_id)
    answers = [m for m in msgs if m.get("role") != "user"]        # the AI that wrote them, not the one chosen now
    ais = " and ".join(ai.chat_ais(answers))
    days = []
    for key in (chat.get("scope") or {}).get("days") or []:
        try:
            _, recs = s.day(key)
            first = min(recs, key=lambda r: r.start)
            days.append(f"{long_date(first.start.date())} ({kind_label(first.kind)})")
        except KeyError:
            days.append(str(key).split("|")[1] if "|" in str(key) else str(key))
    try:
        summ = s.chat.summary(chat)
        model = ai.model_label(summ.get("model") or "sonnet", summ.get("effort", ""),
                               ai.answered_by(answers[-1]) if answers else "")
    except Exception:  # noqa: BLE001 - the chat's own model name will do
        model = chat.get("model") or ""
    meta = [D.label("Course", course_display(course))]
    if days:
        meta.append(D.label("Class days", ", ".join(days)))
    if model:
        meta.append(D.label("Model", model + (" · highlights only" if (chat.get("scope") or {}).get("focus") ==
                                                "highlights" else "")))
    meta.append([D.R(f"Chat with {ais} in IEUStudia, exported {dt.datetime.now():%Y-%m-%d %H:%M}.", italic=True)])
    blocks: list[Any] = []
    texts = []
    for m in msgs:
        text = (m.get("text") or "").strip()
        usage = m.get("usage") or {}
        if m.get("role") == "user":
            blocks.append(D.Para([D.R("You")], style="who"))
            blocks += [D.Para([D.R(x.strip())], style="question") for x in text.split("\n\n") if x.strip()]
            continue
        who = ai.PROVIDER_LABELS.get(ai.answered_by(m)) or "AI"
        found = [notices.markdown(n) for n in (usage.get("notices") or [])]
        if m.get("status") == "error":
            found.append(notices.alert("CAUTION", f"{who} could not answer: {usage.get('error') or 'error'}"))
        if not text and not found:
            continue
        blocks.append(D.Para([D.R(who)], style="who"))
        blocks += [D.Markdown(x, top=2) for x in found]
        if text:
            md = dollar_math(text) + (" *(stopped)*" if m.get("status") == "stopped" else "")
            blocks.append(D.Markdown(md, top=2))
            texts.append(text)
    return D.Doc(_chat_title(s, chat), kicker=f"{course.code or course.name} · Chat with {ais}", meta=meta,
                 blocks=blocks, lang="tr" if D.turkish(" ".join(texts)) else "en")


# ------------------------------------------------------------------ writing the files
def render(doc: D.Doc, fmt: str, folder: Path, name: str = "document") -> Path:
    """``doc`` as a file in ``folder``: Word, a PDF (printed by Edge), Markdown or plain text (with Windows' line
    endings and a byte order mark, so Notepad and older programs read its Turkish letters)."""
    path = folder / f"{name}{EXT[fmt]}"
    if fmt == "docx":
        from .outputs.docx import docx_bytes

        path.write_bytes(docx_bytes(doc))
    elif fmt == "md":
        path.write_bytes(D.to_markdown(doc).encode("utf-8"))
    elif fmt == "txt":
        path.write_bytes(_txt(D.to_text(doc)))
    elif fmt == "pdf":
        from .studypack import print_pdf

        page = folder / f"{name}.html"
        page.write_bytes(D.to_html(doc).encode("utf-8"))
        if not print_pdf(page, path):
            raise ExportError(_no_pdf() if not can_print() else
                              t("The PDF couldn't be made: Microsoft Edge stopped before it was done. Try once more, "
                                "or save it as Word instead."))
    else:
        raise ExportError(t("This can't be exported."))
    return path


def _txt(text: str) -> bytes:
    return b"\xef\xbb\xbf" + text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8")


def subtitles(recs: list[Recording], trans: dict[str, Any], fmt: str) -> list[tuple[int, str]]:
    """(part, its subtitles) for each part with a transcript, numbered as the transcript's parts."""
    from .outputs.srt import render_srt, render_vtt

    tmap = as_transcript_map(trans)
    out = []
    for i, rec in enumerate(select_parts(recs, tmap), start=1):
        tr = tmap.get(rec.delivery_id)
        if tr and tr.segments:
            out.append((i, (render_vtt if fmt == "vtt" else render_srt)(tr.segments)))
    return out


def _sub_bytes(text: str, fmt: str) -> bytes:
    """SRT with a byte order mark and Windows' line endings (what players on Windows read best); VTT as the web
    has it (UTF-8)."""
    return _txt(text) if fmt == "srt" else text.encode("utf-8")


class _Zip:
    """A zip whose files are under one folder (unzipped, they stay together), each name kept apart and short enough
    (course_export.Names)."""

    def __init__(self, path: Path, root: str) -> None:
        from .course_export import Names, clean_name

        self.root = clean_name(root, 60)
        self.names = Names(len(self.root) + 1)
        self.z = zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED)

    def add(self, stem: str, ext: str, data: bytes) -> str:
        name = self.names.take("", stem, ext)
        self.z.writestr(f"{self.root}/{name}", data,
                        compress_type=zipfile.ZIP_STORED if ext in (".pdf", ".docx") else zipfile.ZIP_DEFLATED)
        return name

    def close(self) -> None:
        self.z.close()


def _make(s: Any, job: Job, tmp: Path, progress: Progress) -> Path:
    """The export, made in the folder ``tmp``; its file there."""
    if job.place == "chat":
        return render(chat_doc(s, job.key), job.fmt, tmp)
    if job.place == "day":
        if job.fmt in SUBS:
            course, recs = _day(s, job.key)
            subs = subtitles(recs, store.load_transcripts([r.delivery_id for r in recs]), job.fmt)
            if not subs:
                raise ExportError(t("Transcribe this class day first."))
            if job.ext != ".zip":
                path = tmp / f"subtitles{EXT[job.fmt]}"
                path.write_bytes(_sub_bytes(subs[0][1], job.fmt))
                return path
            stem = day_stem(course, recs[0].start.date(), recs[0].kind)
            path = tmp / "subtitles.zip"
            z = _Zip(path, Path(job.name).stem)
            for i, text in subs:
                z.add(f"{stem} - Part {i}", EXT[job.fmt], _sub_bytes(text, job.fmt))
            z.close()
            return path
        if job.what == "pack" and job.fmt in ("md", "txt"):
            text = pack_text(s, job.key)
            path = tmp / f"pack{EXT[job.fmt]}"
            path.write_bytes(_txt(text) if job.fmt == "txt" else text.encode("utf-8"))
            return path
        if job.what.startswith("notes:"):
            return render(notes_doc(s, job.key, job.what[6:]), job.fmt, tmp)
        return render(transcript_doc(s, job.key, pack=job.what == "pack"), job.fmt, tmp)
    if not job.zip:
        return render(course_doc(s, job.key, job.what, job.notes), job.fmt, tmp)
    _, _, days, _ = _scope_days(s, job.key, job.what)
    path = tmp / "lectures.zip"
    z = _Zip(path, Path(job.name).stem)
    try:
        for i, d in enumerate(days, start=1):
            progress(i - 1, len(days))
            course, recs = _day(s, d.key)
            stem = day_stem(course, d.date, d.kind)
            if job.fmt in SUBS:
                for part, text in subtitles(recs, store.load_transcripts([r.delivery_id for r in recs]), job.fmt):
                    z.add(f"{stem} - Part {part}", EXT[job.fmt], _sub_bytes(text, job.fmt))
                continue
            made = render(transcript_doc(s, d.key), job.fmt, tmp, f"lecture-{i}")
            z.add(f"{stem} - Transcript", EXT[job.fmt], made.read_bytes())
            if job.notes:
                for kind, name in NOTE_NAMES.items():
                    if _notes_md(s, d.key, kind):
                        made = render(notes_doc(s, d.key, kind), job.fmt, tmp, f"lecture-{i}-{kind}")
                        z.add(f"{stem} - {name}", EXT[job.fmt], made.read_bytes())
        progress(len(days), len(days))
    finally:
        z.close()
    return path


def _put(made: Path, dest: Path) -> Path:
    """The file made, put at ``dest`` whole: written beside it first, then swapped in (an earlier file of that name
    open in Word or a PDF viewer is said, not half written over)."""
    from .net import plain_error

    part = dest.with_name(dest.name + ".tmp")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(made, part)
    except OSError as exc:
        _gone(part)
        raise ExportError(t("Windows didn't let IEUStudia save in {folder}: {error} Pick another folder, such as "
                            "Documents or the Desktop.", folder=str(dest.parent), error=plain_error(exc))) from exc
    try:
        os.replace(part, dest)
    except PermissionError as exc:
        _gone(part)
        raise ExportError(t("{name} is open in another program, so it can't be saved over. Close it there (Word or a "
                            "PDF viewer), then save again.", name=dest.name)) from exc
    except OSError as exc:
        _gone(part)
        raise ExportError(t("{name} couldn't be saved: {error}", name=dest.name, error=plain_error(exc))) from exc
    return dest


def _gone(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        log.warning("Export: %s could not be removed", path)


def write(s: Any, job: Job, dest: Path, progress: Optional[Progress] = None) -> Path:
    """Make the export and save it as ``dest`` (given its ending when the name typed in the dialog has none)."""
    dest = Path(dest)
    if dest.suffix.lower() != job.ext:
        dest = dest.with_name(dest.name + job.ext)
    with tempfile.TemporaryDirectory(prefix="ieus-export-") as tmp:
        made = _make(s, job, Path(tmp), progress or (lambda i, n: None))
        out = _put(made, dest)
    log.info("Export: %s %s as %s (%d KB)", job.place, job.what, job.fmt, out.stat().st_size // 1024)
    return out


def copy_text(s: Any, scope: str, what: str) -> str:
    """What "Copy as text" puts on the clipboard: the document as plain text."""
    place, key = _split_scope(scope)
    if place == "chat":
        return D.to_text(chat_doc(s, key))
    if place != "day":
        raise ExportError(t("This can't be exported."))
    _plan_day(s, key, what, "txt")                       # (it is there)
    if what == "pack":
        return pack_text(s, key)
    if what.startswith("notes:"):
        return D.to_text(notes_doc(s, key, what[6:]))
    return D.to_text(transcript_doc(s, key))


# ------------------------------------------------------------------ the menu's choices
def course_weeks(s: Any, course_id: str) -> dict[str, Any]:
    """What a course's Export menu offers: its weeks with transcribed lectures (newest first), how many lectures the
    whole course has, and whether any of them has AI notes."""
    from .course_export import Choice, plan_course

    course = s.lib.get_course(course_id)
    plan_ = plan_course(s.lib, s.settings, course_id, Choice(), dt.date.today()) if course else None
    if plan_ is None:
        raise ExportError(t("This course is no longer in your list."))
    weeks, keys = [], set()
    for w in plan_.all_weeks:
        done = [d for d in w.days if d.done]
        keys |= {d.key for d in done}
        if done:
            weeks.append({"no": w.no, "topic": w.topic, "start": w.start, "end": w.end, "current": w.current,
                          "lectures": len({d.key for d in done})})
    keys |= {d.key for d in plan_.loose if d.done}
    notes = False
    for key in keys:
        try:
            if s.day_files(key).get("notes_kinds"):
                notes = True
                break
        except (KeyError, OSError):
            continue
    return {"weeks": sorted(weeks, key=lambda w: -w["no"]), "lectures": len(keys), "notes": notes}


def prefs(settings: Any) -> dict[str, dict[str, Any]]:
    """The format and options last used in each place (PDF for reading, at first)."""
    raw = getattr(settings, "export_prefs", None)
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    for place in PLACES:
        p = raw.get(place) if isinstance(raw.get(place), dict) else {}
        out[place] = {"fmt": p.get("fmt") if p.get("fmt") in DOCS else "pdf",
                      "sub": p.get("sub") if p.get("sub") in SUBS else "srt",
                      "zip": bool(p.get("zip", False)), "notes": bool(p.get("notes", True))}
    return out


def remembered(settings: Any, job: Job) -> dict[str, dict[str, Any]]:
    """The prefs with this export's choices remembered (a document's format; subtitles' own; a course's options)."""
    out = prefs(settings)
    mine = out[job.place]
    if job.fmt in DOCS:
        mine["fmt"] = job.fmt
        if job.place == "course":
            mine["zip"], mine["notes"] = job.zip, job.notes
    else:
        mine["sub"] = job.fmt
    return out
