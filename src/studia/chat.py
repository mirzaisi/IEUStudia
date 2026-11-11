"""Free chat with Claude about the student's lectures: one class day, or several days of a course.

A chat's material (instructions + compact transcripts, optionally slide images) is fixed when it starts.
While a chat is in use it keeps one Claude Code process, so follow-up questions read the lectures from
Claude's prompt cache instead of paying for them again. Idle processes are closed after a while; the next
question then starts a fresh process that receives the slides and the earlier conversation again (as text), so
the process is kept for most of the hour Claude Code keeps a subscription's cache.
"""

from __future__ import annotations

import datetime as dt
import glob
import json
import logging
import os
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from . import ai, aiwait, appconfig, chatfiles, chatlimits, chatread, chatslides, effort, notices, store
from .cancel import Cancelled
from .catchup import no_sound
from .domain import Course, Recording
from .i18n import date_long, date_short, t, tn
from .knowledge import course_brief, retrieve
from .outputs.ai_copy import SCREEN_LINES, compact_day, part_lines
from .outputs.common import course_display, kind_label, long_date, time_range
from .outputs.markdown import part_views
from .outputs.mathmd import dollar_math
from .outputs.writer import atomic_write, course_dir, safe_name

log = logging.getLogger("studia")

MAX_CONTEXT_TOKENS = 900_000      # a chat's material at most; each AI's own share of its window comes first
                                  # (chatlimits: 850k on Claude Sonnet and Opus, 160k on Haiku, 80% of ChatGPT's)
MAX_SLIDES = 500                  # pictures per chat at most (each AI's own limit first: chatlimits.Limits.pictures)
INSTRUCTION_TOKENS = 400          # the notes on the slides and files in the instructions (plus 60 a file for the list
                                  # of what the AI got of each)
NATIVE_PAGES = 100                # a PDF sent whole as a file: at most this many pages (Claude's limit on 200k models)
NATIVE_BYTES = 20 * 1024 * 1024   # and at most this big
IDLE_CLOSE_S = 3300               # Claude Code keeps a subscription's prompt cache for an hour: a question after a
                                  # break still reads the slides and the conversation from it in the same process
MAX_LIVE = 2                      # Claude Code processes kept open at once (~160 MB of memory each while idle)
MODELS = ("haiku", "sonnet", "opus")
FOCUS = ("full", "highlights", "course")      # course: every lecture's full transcript (read in parts first when it
                                              # doesn't fit whole) + the chosen files + search per question
EMIT_EVERY_S = 0.08
SAVE_EVERY_S = 3.0
READING_SAVE_S = 5.0              # how far "Read everything" got, kept with the answer (said after a restart)


LEVEL_NAMES = (*effort.LEVELS, *effort.LOCAL_LEVELS)


def _set_level(session: Any, level: str) -> None:
    """A ChatGPT or offline chat's level for its next question (Claude's process keeps its own)."""
    if hasattr(session, "think"):
        session.think = level == "on"
    elif hasattr(session, "effort"):
        session.effort = level


def system_prompt(n_days: int, focus: str, slides: bool) -> str:
    multi = n_days > 1
    cite = "[L2 P1 05:10] (lecture, part, time)" if multi else "[P2 14:05] (part, time)"
    fmt = ("Format: a line starting with '#' begins a lecture" + (" (L1, L2, … in date order)" if multi else "")
           + ", '## Part N (hh:mm–hh:mm)' begins one recording of it, and every paragraph starts with [mm:ss] "
           "from the start of that part. ⭐ marks moments the lecturer emphasised (exam, quiz, important, homework, "
           "deadlines); ⚠ and [unclear] mark audio that was hard to understand.")
    if slides:
        fmt += " (slide N) is the slide on screen; the slide images are attached to the student's first message."
    fmt += " " + SCREEN_LINES                    # (the screen timeline's lines: outputs/ai_copy.py)
    lines = [
        "You are a friendly, precise study assistant for a university engineering student, chatting about the "
        "student's own lectures. They are given below as automatic speech-recognition transcripts: mostly English, "
        "with Turkish passages tagged [TR]. Names, symbols and formulas may be misheard — fix obvious errors from "
        "context and say when you are guessing.",
        "",
        fmt,
    ]
    if focus == "highlights":
        lines += ["", "Only the moments the lecturer emphasised (⭐) are included, not the full lectures; say so "
                      "when a question needs more than that."]
    lines += [
        "",
        "Under COURSE you get the course's learning outcomes (LO1, LO2, …), its weekly plan, the assessments the lecturer "
        "announced (with the outcomes each covers) and the latest announcements. Some questions come with passages "
        "from the course files (slides, PDFs) and from other lectures, found by keyword search: use the relevant ones "
        "and cite them as labelled, e.g. [Week05_Student.pdf p.12].",
        "",
        "How to answer:",
        "- Answer directly and concisely in Markdown; formulas in LaTeX: $...$ in a sentence, $$...$$ on lines of their "
        "own for a displayed formula (not \\(...\\) or \\[...\\]). Use lists or tables when they help.",
        f"- Base answers on the lectures and cite the moments you rely on as {cite}, so the student can jump there.",
        "- If the lectures do not cover something, say so; you may then answer from general knowledge, clearly "
        "labelled as such.",
        "- For questions about quizzes or exams, first list what the lecturer explicitly said (brief quotes with "
        "citations), then give your own prediction, clearly labelled as a prediction.",
        "- Reply in the language the student writes in.",
        "- " + notices.ALERTS_MD,
        "",
        "You are inside the IEUStudia app, which can do things for the student. When it would clearly help, or when "
        "the student asks for something the app does, end your answer with ONE block like this; the app shows it as "
        "a button the student can press (you never start anything yourself, so never say that you did):",
        "```ieustudia",
        '{"action": "video_plan", "weeks": [4, 5], "focus": "Quiz 2"}',
        "```",
        "Actions (all for this course): video_plan (YouTube videos for weeks, checked through their transcripts; "
        "weeks, focus), prep_pack (a written exam review sheet: key ideas, formulas, practice; weeks, focus), get_ready (checklist for an assessment; "
        "focus), transcribe (lectures of weeks, or of one date YYYY-MM-DD), download_files (weeks), study_notes "
        "(date), open_lecture (date, part, at \"mm:ss\"), weekly_digest, calendar (put the announced quizzes and "
        "exams in the student's calendar), sync_course (check IEULearn, transcribe and download what is missing). "
        "Use the week numbers and assessment names exactly as listed under COURSE.",
    ]
    return "\n".join(lines)


def replay_text(history: list[dict[str, Any]]) -> str:
    """The earlier conversation, for a new Claude process (after an idle close, a model switch or a crash)."""
    lines = ["(We already talked about these lectures. The conversation so far, oldest first:)", ""]
    for m in history:
        text = (m.get("text") or "").strip()
        if not text:
            continue
        if m["role"] == "user":
            lines.append(f"Student: {text}")
        else:
            lines.append(f"You: {text}" + (" (answer was stopped early)" if m.get("status") == "stopped" else ""))
        lines.append("")
    lines.append("(End of the earlier conversation. My new question:)")
    return "\n".join(lines)


PIECE_CHARS = 4000            # a lecture read in parts: its transcript in pieces of about this much (the notes of each:
                              # up to chatread.NOTE_CHARS, about a third of it)


def lecture_pieces(course: Course, recs: list[Recording], trans: dict, slides: Any, n: int,
                   key: str) -> list[chatread.Page]:
    """A class day's transcript in pieces, for a whole-course chat whose lectures don't all fit whole: read in parts
    like the slides (chatread), each piece labelled "[L2 P1 00:00–09:40]" (the lecture, its part, the times it covers)
    and its notes kept with the transcript's, by the piece's text."""
    start = min(r.start for r in recs)
    name = (f"L{n} · " if n else "") + long_date(start.date())
    out: list[chatread.Page] = []
    for p in part_views(recs, trans, slides):
        if not p.transcript:
            continue
        chunks: list[list[str]] = []
        cur: list[str] = []
        size = 0
        for line in part_lines(p):
            line = line[:PIECE_CHARS]
            if cur and size + len(line) + 1 > PIECE_CHARS:
                chunks.append(cur)
                cur, size = [], 0
            cur.append(line)
            size += len(line) + 1
        if cur:
            chunks.append(cur)
        for chunk in chunks:
            # (a screen line, "[Screen 3, 12:34-15:00] ...", by its start)
            first, last = (x[1:x.index("]")].split(", ", 1)[-1].split("-", 1)[0] if "]" in x else ""
                           for x in (chunk[0], chunk[-1]))
            label = f"[{f'L{n} ' if n else ''}P{p.index} {first}–{last}]"
            out.append(chatread.Page(label=label, group="lecture", file=key, name=name, page=len(out) + 1,
                                     text=f"Part {p.index} ({time_range(p.rec)})\n" + "\n".join(chunk),
                                     source=str(store.transcript_path(p.rec.delivery_id))))
    return out


AS_SAID = {"full": "the full transcript",
           "notes": "notes read for you from its full transcript, piece by piece (with each question, under NOTES ON "
                    "THE PAGES YOU DON'T SEE IN FULL, labelled like [L2 P1 00:00–09:40])",
           "highlights": "only the moments the lecturer emphasised (⭐): the full transcript did not fit, and Read "
                         "everything is off"}


def lectures_manifest(days: list[dict[str, Any]], untranscribed: list[Recording], focus: str) -> str:
    """What the AI got of each lecture (the whole course: full transcripts, notes of the ones read in parts, or
    highlights), so it never says it has less, or more, than it got."""
    if not days:
        return ""
    lines: list[str] = []
    forms = {d["as"] for d in days}
    n = len(days)
    ns = [int(d.get("n") or 0) for d in days]          # their numbers: a day that is gone keeps its own (L1, L3)
    if n > 1 and all(ns):
        span = f"L{ns[0]}–L{ns[-1]}" if ns == list(range(ns[0], ns[0] + n)) else ", ".join(f"L{x}" for x in ns)
    else:
        span = f"L1–L{n}"
    every = f"All {n} lectures ({span})" if n > 1 else "The lecture"
    if forms == {"full"}:
        lines.append(f"- {every}: the full transcript{'s' if n > 1 else ''} of every part that was transcribed.")
    elif forms == {"highlights"} and focus == "highlights":
        lines.append(f"- {every}: only the moments the lecturer emphasised (⭐), as the student chose.")
    else:
        lines += [f"- {d['ref']}: {AS_SAID[d['as']]}" for d in days]
    if untranscribed:
        dates = ", ".join(sorted({r.start.date().isoformat() for r in untranscribed}))
        lines.append(f"- Not transcribed, so you have nothing of them: {len(untranscribed)} recording"
                     f"{'s' if len(untranscribed) != 1 else ''} ({dates}).")
    return ("=== WHAT YOU GOT OF THE LECTURES ===\n" + "\n".join(lines)
            + "\nWhen a question needs more of a lecture than you got, say so plainly (a [!NOTE] block); never say you "
              "have less than this.")


def text_only_note(pages: list[Any], later: bool) -> str:
    """For the AI: the pages whose pictures its first message couldn't carry after all, although its list of what it
    got (WHAT YOU GOT OF THE CHOSEN FILES) says pictures; ``later``: they are read for it as notes instead."""
    by_file: dict[str, list[int]] = {}
    shots: list[str] = []
    for p in pages:
        if p.group == "video" or not p.page:
            shots.append(p.label)
        else:
            by_file.setdefault(p.name, []).append(p.page)
    named = [f"{name} {chatread.ranges(nums)}" for name, nums in by_file.items()] + shots
    return ("Correction to WHAT YOU GOT OF THE CHOSEN FILES: this message could not carry the pictures of "
            + "; ".join(named) + ", so you have only their text"
            + (". They are read for you as notes (they come with each question)." if later else
               ". If a question needs what only their pictures show, say so plainly (a [!NOTE] block)."))


def kept(reading: Optional[dict[str, Any]]) -> dict[str, Any]:
    """How far a reading got, as kept with the answer (not what only the live progress needs)."""
    return {k: v for k, v in (reading or {}).items() if k not in ("parts", "eta", "at", "started")}


def _clean_stale_workdirs(max_age_s: float = 12 * 3600) -> None:
    """Remove Claude work folders left behind by a crash (they hold a copy of the transcripts)."""
    now = time.time()
    for p in glob.glob(os.path.join(tempfile.gettempdir(), "studia-chat-*")) + glob.glob(os.path.join(tempfile.gettempdir(), "ls-chat-*")):
        try:
            if now - os.path.getmtime(p) > max_age_s:
                shutil.rmtree(p, ignore_errors=True)
        except OSError:
            pass


class ChatService:
    def __init__(self, services: Any) -> None:
        self._s = services
        self._lock = threading.Lock()
        self._live: dict[str, ai.ClaudeSession] = {}
        self._running: dict[str, threading.Event] = {}      # chat id -> stop event of the answer being written
        self._reading: dict[str, dict[str, Any]] = {}      # chat id -> how far "Read everything" got for that answer
        self._closing: list[threading.Thread] = []
        try:
            ai.restore_limits(json.loads(self.lib.get_meta("claude_limits") or "{}"))
        except ValueError:
            pass
        threading.Thread(target=_clean_stale_workdirs, daemon=True).start()
        threading.Thread(target=self._clean_files, daemon=True).start()
        threading.Thread(target=self._reaper, name="chat-reaper", daemon=True).start()

    @property
    def lib(self) -> Any:
        return self._s.lib

    # ------------------------------------------------------------------ material
    def _load_days(self, course_id: str, day_keys: list[str]) -> list[Optional[tuple[str, Course, list[Recording], dict]]]:
        out: list[Optional[tuple[str, Course, list[Recording], dict]]] = []
        for key in day_keys:
            try:
                course, recs = self._s.day(key)
            except KeyError:
                out.append(None)
                continue
            trans = store.load_transcripts([r.delivery_id for r in recs]) if course.id == course_id else {}
            out.append((key, course, recs, trans) if trans else None)
        return out

    def all_days(self, course_id: str) -> list[str]:
        """Every transcribed class day of a course (the "whole course" chat)."""
        done = self.lib.transcribed_ids()
        return list(dict.fromkeys(r.day_key for r in self.lib.recordings(course_id) if r.delivery_id in done))

    def ordered_days(self, course_id: str, day_keys: list[str]) -> list[str]:
        """The transcribed days among ``day_keys``, in lecture order (they become L1, L2, …)."""
        days = [d for d in self._load_days(course_id, list(dict.fromkeys(day_keys))) if d]
        days.sort(key=lambda d: min(r.start for r in d[2]))
        return [d[0] for d in days]

    def limit(self, model: str = "") -> int:
        """What a chat's material may take with the chosen AI and Claude's ``model`` (its window, less room for the
        conversation and the answer)."""
        return min(MAX_CONTEXT_TOKENS, chatlimits.of(ai.provider(), model or self._s.settings.ai_model).context)

    def context(self, course_id: str, day_keys: list[str], focus: str = "full", slides: bool = False,
                send: Any = None, uploads: str = "", model: str = "", read_all: Optional[bool] = None) -> dict[str, Any]:
        """System prompt and slide images for ``day_keys`` (already in lecture order; a day that is gone keeps
        its L-number so citations in older answers stay valid). ``send``: the course's slide files that go too
        (see chatslides); ``uploads``: the chat (its id, or ``draft-<token>``) whose own files go too (chatfiles);
        ``model``: Claude's model (its window and the pictures it takes: chatlimits); ``read_all``: "Read everything"
        (None: on for the whole course or many files). All the text of the chosen files goes when the window holds it;
        pictures as many as the AI takes, the rest read in batches first when "Read everything" is on (``read``).
        ``parts``: what each piece adds, for "What this chat sees"."""
        multi = len(day_keys) > 1
        loaded: list[tuple[int, str, Course, list[Recording], dict, dict, str]] = []   # each day with its text
        images: list[tuple[str, Path]] = []        # every slide image available, whether attached or not
        gone, untranscribed = 0, []                # for the student's notices (notices.py), never for the AI
        per = ai.chars_per_token()
        for n, d in enumerate(self._load_days(course_id, day_keys), start=1):
            if d is None:
                gone += 1
                continue
            key, course, recs, trans = d
            tags = {r.section_tag for r in recs if r.delivery_id in trans}     # not another section's recording
            untranscribed += [r for r in recs if r.delivery_id not in trans and r.section_tag in tags
                              and not (r.hidden or r.duplicate_of or r.live) and not self._skipped(r)]
            label = f"L{n}" if multi else ""
            sl = {r.delivery_id: store.load_slides(r.delivery_id) for r in recs}
            # the whole course: every lecture's full transcript (the ones that don't fit are read in parts below);
            # only the moments the lecturer stressed when the student chose "Highlights only"
            text = compact_day(course, recs, trans, sl if slides else None, label=label,
                               only_starred=focus == "highlights")
            if not text:
                continue
            loaded.append((n, key, course, recs, trans, sl, text))
            for p in part_views(recs, trans, sl):
                seen: set[int] = set()
                for s in p.slides if p.transcript else []:
                    if s.index not in seen and s.path and os.path.isfile(s.path):
                        seen.add(s.index)
                        images.append((f"[{label + ' ' if label else ''}Part {p.index}, slide {s.index}]",
                                       Path(s.path)))
        p = ai.provider()
        lim = chatlimits.of(p, model or self._s.settings.ai_model)
        limit = min(MAX_CONTEXT_TOKENS, lim.context)
        local = p == "local"
        can_pics = lim.pictures > 0
        said = "\n".join(x[6] for x in loaded)    # the lecture's slides are checked against what was taught
        send_n = chatslides.normalize(send)
        cs = chatslides.collect(self.lib, course_id, day_keys, send_n, can_pics, said) if send_n["mode"] != "none" else None
        cf = chatfiles.collect(uploads, can_images=can_pics, local=local) if uploads else None
        if cf is not None and not cf["files"]:
            cf = None
        captured = len(images) if slides else 0
        shots = [chatread.Page(label=label, group="video", file="video", name="video", page=i, picture=path,
                               tokens=ai.IMAGE_TOKENS, bytes=chatslides.VIDEO_IMAGE_BYTES * 4 // 3, priority=1,
                               source=str(path))
                 for i, (label, path) in enumerate(images[:captured], start=1)] if can_pics else []
        brief = course_brief(self.lib, self._s.settings, course_id)
        n_files = (len(cs["files"]) if cs else 0) + (len(cf["files"]) if cf else 0)
        read_on = chatread.default_on(read_all, focus, n_files, p)
        everything = (cs["pages"] if cs else []) + shots + (cf["pages"] if cf else [])
        # the whole course: every lecture goes whole when it all fits next to the rest at its smallest (the files'
        # pages as notes); else the latest lectures whole and the earlier ones read in parts like the slides (their
        # notes, with every question), or their highlights when the student turned Read everything off
        fixed = (ai.text_tokens(system_prompt(len(day_keys), focus, True) + brief, per) + ai.PROMPT_OVERHEAD
                 + INSTRUCTION_TOKENS + 60 * n_files + 40 * len(loaded))
        files_least = chatread.notes_cost([x for x in everything if x.text or x.picture is not None], per) \
            if read_on and not local else 0
        forms = self._lecture_forms(loaded, focus, limit - fixed - files_least, read_on and not local, per)
        texts: list[str] = []
        lecture_pages: list[Any] = []               # the lectures read in parts (chatread), with the slides' pages
        day_parts: list[dict[str, Any]] = []
        tight = local or any(v != "full" for v in forms.values())    # the screens' titles only (outputs/ai_copy.py)
        for n, key, course, recs, trans, sl, text in loaded:
            how = forms[key]
            pieces: list[Any] = []
            if how == "full" and tight and "\n[Screen " in text:
                text = compact_day(course, recs, trans, sl if slides else None, label=f"L{n}" if multi else "",
                                   only_starred=focus == "highlights", screens="titles")
            if how == "notes":
                pieces = lecture_pieces(course, recs, trans, sl if slides else None, n if multi else 0, key)
                lecture_pages += pieces
            elif how == "highlights":
                text = compact_day(course, recs, trans, sl if slides else None, label=f"L{n}" if multi else "",
                                   only_starred=True)
            if how != "notes":
                texts.append(text)
            start = min(r.start for r in recs)
            day_parts.append({"key": key, "n": n if multi else 0, "label": date_short(start.date()),
                              "long": date_long(start.date()), "kind": t(kind_label(recs[0].kind)), "as": how,
                              "parts": sum(1 for x in part_views(recs, trans) if x.transcript) if how == "notes"
                              else text.count("\n## Part "), "chars": len(text),
                              "tokens": chatread.notes_cost(pieces, per) if how == "notes" else ai.text_tokens(text, per),
                              "pieces": len(pieces),
                              "ref": (f"L{n} · " if multi else "") + f"{long_date(start.date())} ({kind_label(recs[0].kind).lower()})",
                              "seconds": sum(r.duration_s or 0 for r in recs if r.delivery_id in trans)})
        told = lectures_manifest(day_parts, untranscribed, focus) if multi or focus == "course" else ""
        # what must go whole (the lectures, the course's info, the instructions), then all the files' text that fits
        # (the student's own first), then as many pictures as the AI takes and the room left holds
        base = (ai.text_tokens(system_prompt(len(day_keys), focus, True) + brief + "\n\n".join(texts) + told, per)
                + ai.PROMPT_OVERHEAD + INSTRUCTION_TOKENS + 60 * n_files)   # with the notes and the list of files
        room = max(0, limit - base)
        # what one request may weigh (Claude: the whole conversation): the instructions and all the text count, and room
        # is kept for the notes and for the questions and answers that follow; the pictures get the rest
        base_bytes = (chatlimits.text_bytes(system_prompt(len(day_keys), focus, True) + brief + "\n\n".join(texts) + told)
                      + 4000 + 300 * n_files + chatlimits.CONVERSATION_BYTES)      # the files' notes and list

        def bytes_room(files: Any, up: Any, notes_tokens: int) -> Optional[int]:
            if not lim.request_bytes:
                return None
            text = (files["text"] if files else "") + (up["text"] if up and not local else "")
            return max(0, lim.request_bytes - base_bytes - chatlimits.text_bytes(text) - notes_bytes(notes_tokens))

        def notes_bytes(notes_tokens: int) -> int:
            return int(notes_tokens * per * 1.3)          # UTF-8 (Turkish, ⭐) and LaTeX: more than a byte a character
        candidates = ([x for x in cs["pages"] if x.picture is not None] if cs and not local else []) + shots \
            + ([x for x in cf["pages"] if x.picture is not None] if cf else [])

        def texts_for(chosen: list[Any], troom: int, native: tuple = (), native_up: tuple = ()) -> tuple[Any, Any]:
            u = chatfiles.assemble(cf, chosen, max_text_tokens=troom, native=native_up) if cf else None
            used = 0 if u is None or local else u["text_tokens"]
            f = (chatslides.assemble(cs, captured, chosen, sum(1 for x in chosen if x.group == "video"),
                                     max_text_tokens=max(0, troom - used), local=local, native=native) if cs else None)
            return f, u

        def unread(files: Any, up: Any, pictured: set[str]) -> tuple[set[str], list[Any]]:
            """The pages whose text was cut, and the pages to read in parts (not seen whole): the lectures that don't
            fit whole first."""
            cut = {x.key for x in (files["cut_pages"] if files else []) + (up["cut_pages"] if up else [])}
            if local:    # the offline model never reads the course's files whole: their pages are read for it
                return cut, [x for x in everything if x.text and (x.group == "course" or x.key in cut)]
            return cut, lecture_pages + [x for x in everything if x.key not in pictured
                                         and ((can_pics and x.picture is not None) or (x.key in cut and x.text))]

        squeeze, shaped = 0, None
        for _ in range(6):        # the files' text, the pictures and the notes of the rest, within what the chat holds
            troom = max(0, room - squeeze)
            files, up = texts_for([], troom)
            text_tokens = (files["summary"]["text_tokens"] if files else 0) + (up["text_tokens"] if up and not local else 0)
            reserve, chosen, native, native_up = 0, [], [], []
            for _ in range(3):                   # the notes of what isn't pictured need room too ("Read everything")
                chosen, native, native_up = self._pictures(lim, cs, cf, candidates,
                                                           max(0, troom - text_tokens - reserve), per,
                                                           bytes_room(files, up, reserve))
                keys = {x.key for x in chosen} | {x.key for x in (cs["pages"] if cs else []) if x.source in native} \
                    | {x.key for x in (cf["pages"] if cf else []) if x.file in native_up}
                need = chatread.notes_cost(unread(files, up, keys)[1], per) if read_on else 0
                if need <= reserve:
                    break
                reserve = need
            files, up = texts_for(chosen, troom, tuple(native), tuple(native_up))
            pictured = {x.key for x in chosen} | {x.key for x in (cs["pages"] if cs else []) if x.source in native} \
                | {x.key for x in (cf["pages"] if cf else []) if x.file in native_up}
            cut, leftover = unread(files, up, pictured)
            video = [(x.label, x.picture) for x in shots if x.key in pictured]
            attached = (files["images"] if files else []) + video
            manifest = self._manifest(files, up, cs, cf, shots, video, leftover if read_on else [], cut, captured,
                                      local, read_on)
            note = "\n\n".join(x for x in (chatslides.prompt_note(files["summary"]) if files else "",
                                           chatfiles.prompt_note(up) if up and not up["local"] else "",
                                           chatread.prompt_note(read_on and bool(leftover),
                                                                lectures=bool(lecture_pages))) if x)
            mine = up["text"] if up and not up["local"] else ""   # the offline model gets them with each question
            prompt = (system_prompt(len(day_keys), focus, bool(attached)) + (f"\n\n{note}" if note else "")
                      + (f"\n\n{manifest}" if manifest and not local else "")
                      + (f"\n\n{told}" if told and not local else "")
                      + (f"\n\n=== COURSE ===\n\n{brief}" if brief else "")
                      + (f"\n\n=== COURSE SLIDES ===\n\n{files['text']}" if files and files["text"] else "")
                      + (f"\n\n=== STUDENT'S FILES ===\n\n{mine}" if mine else "")
                      + "\n\n=== LECTURES ===\n\n" + "\n\n".join(texts))
            mine_pics = up["picture_tokens"] if up else 0     # the student's pictures: by their size (pages are tall)
            course_pics = files["summary"]["image_tokens"] - len(video) * ai.IMAGE_TOKENS if files else 0
            notes_tokens = chatread.notes_cost(leftover, per) if read_on else 0
            tokens = (ai.text_tokens(prompt, per) + ai.PROMPT_OVERHEAD + course_pics + len(video) * ai.IMAGE_TOKENS
                      + mine_pics + notes_tokens + (ai.text_tokens(up["text"], per) if up and up["local"] else 0))
            shaped = (files, up, cut, leftover, video, attached, prompt, mine_pics, course_pics, notes_tokens, tokens)
            if tokens <= limit or troom == 0 or not (cs or cf):
                break
            # text cut from a page comes back as its notes (about half of it): cut twice what is over
            squeeze += 2 * (tokens - limit) + 200
        files, up, cut, leftover, video, attached, prompt, mine_pics, course_pics, notes_tokens, tokens = shaped
        squeezed = bool(notes_tokens and tokens > limit)
        if squeezed:          # all the files' text is cut and their notes still don't fit whole: they are shortened
            less = min(notes_tokens, tokens - limit)            # to the room left (fit_notes); what is over without
            notes_tokens, tokens = notes_tokens - less, tokens - less     # them still is (the chat is too big)
        read = {"on": read_on, "auto": read_all is None, "pages": len(leftover),
                "total": len(everything) + len(lecture_pages), "lectures": len(lecture_pages),
                "lecture_days": sum(1 for d in day_parts if d["as"] == "notes"),
                "local": local, **(chatread.estimate(leftover, lim, per=per) if leftover else {}),
                **({"short": True} if squeezed else {})}
        if files is not None:
            files["summary"]["max_images"] = min(chatslides.MAX_IMAGES, lim.pictures)    # what this AI takes
            for fo in files["summary"]["files"]:
                fo["read"] = sum(1 for x in leftover if x.file == fo["id"]) if read_on else 0
        if up is not None:
            for it in up["items"]:
                it["read"] = sum(1 for x in leftover if x.file == it["id"]) if read_on else 0
        pictures = attached + (up["images"] if up else [])
        by_picture = {id(x.picture): [x] for x in everything if x.picture is not None}

        def covered(pic: Any) -> list[Any]:
            """The pages a picture (or a whole PDF) of the first message carries."""
            if isinstance(pic, chatslides.PdfFile):
                pdf = os.path.normcase(os.path.normpath(pic.pdf))
                return [x for x in everything if x.source and os.path.normcase(os.path.normpath(str(x.source))) == pdf]
            return by_picture.get(id(pic), [])
        known = (sum(d["tokens"] for d in day_parts if d["as"] != "notes")      # (their notes: in notes_tokens)
                 + ai.text_tokens(brief, per) + course_pics + len(video) * ai.IMAGE_TOKENS
                 + mine_pics + notes_tokens + (files["summary"]["text_tokens"] if files else 0)
                 + (up["text_tokens"] if up else 0))
        parts = {"days": day_parts, "focus": focus, "brief_tokens": ai.text_tokens(brief, per),
                 "instructions_tokens": max(0, tokens - known), "video": [label for label, _ in video],
                 "uploads": up, "provider": p, "notes_tokens": notes_tokens}
        return {"system": prompt, "images": pictures, "slides_total": len(images), "n_days": len(day_parts),
                "full_days": [d["key"] for d in day_parts if d["as"] == "full"],
                "video_images": len(video), "video": video, "files": files["summary"] if files else None,
                "said": said, "tokens": tokens, "parts": parts, "limit": limit, "lim": lim, "read": read,
                "read_pages": leftover if read_on else [], "base_tokens": base,
                # each picture's pages: one left out of the first message for its weight is read in batches instead
                "picture_pages": {label: covered(pic) for label, pic in pictures},
                # what a question may add (the notes, the pages that matter most as pictures), and the weight of the
                # first request that keeps room for the notes and the conversation (_answer)
                "room": max(notes_tokens, limit - (tokens - notes_tokens)),
                "first_bytes": max(0, lim.request_bytes - chatlimits.CONVERSATION_BYTES - notes_bytes(notes_tokens)),
                "notices": self._notices(gone, untranscribed, files, send, captured, len(video), lim, read_on,
                                         len([x for x in leftover if x.group not in ("video", "lecture")]), up)
                + ([notices.notice("lectures_highlights", n=sum(1 for d in day_parts if d["as"] == "highlights"))]
                   if focus == "course" and not local and any(d["as"] == "highlights" for d in day_parts) else []),
                "upload_text": chatfiles.local_text(up) if up and up["local"] else "",
                "upload_passages": up["passages"] if up and up["local"] else []}

    @staticmethod
    def _lecture_forms(loaded: list[tuple], focus: str, room: int, reading: bool, per: float) -> dict[str, str]:
        """How each class day goes: "full" (its whole transcript); for the whole course, when the full transcripts
        don't all fit ``room`` (what the window leaves next to the instructions, the course's info and the chosen
        files at their smallest), the latest lectures whole and the earlier ones read in parts ("notes", ``reading``)
        or, with Read everything off, only what the lecturer stressed ("highlights")."""
        forms = {x[1]: "highlights" if focus == "highlights" else "full" for x in loaded}
        if focus != "course" or not loaded:
            return forms
        full = {x[1]: ai.text_tokens(x[6], per) for x in loaded}
        if sum(full.values()) <= room:
            return forms
        small: dict[str, int] = {}
        for n, key, course, recs, trans, _sl, _text in loaded:
            small[key] = (chatread.notes_cost(lecture_pieces(course, recs, trans, None, n, key), per) if reading
                          else ai.text_tokens(compact_day(course, recs, trans, label=f"L{n}", only_starred=True), per))
        used = sum(small.values())
        forms = {key: "notes" if reading else "highlights" for key in forms}
        for x in reversed(loaded):             # the latest lectures whole first: what is asked about now
            extra = full[x[1]] - small[x[1]]
            if used + extra <= room:
                forms[x[1]] = "full"
                used += extra
        return forms

    @staticmethod
    def _pictures(lim: Any, cs: Optional[dict[str, Any]], cf: Optional[dict[str, Any]], candidates: list[Any],
                  room: int, per: float, size_room: Optional[int] = None) -> tuple[list[Any], list[str], list[str]]:
        """The pages that go as pictures within ``room`` tokens, ``size_room`` bytes (what the request's text and the
        room kept for the rest of the chat leave of it; None: the AI's whole limit) and the AI's limits, and the PDFs
        that go whole as files instead (course files by path, uploads by id). An AI that takes PDFs (Claude) gets a PDF
        as a file when that is better: when its pictures don't all fit in the request's size, and the file weighs less
        than them."""
        most_bytes = lim.request_bytes if size_room is None else size_room
        if lim.request_bytes and not most_bytes:
            return [], [], []                    # the text alone fills the request: no picture fits
        chosen = chatslides.allocate(candidates, lim.pictures, room, most_bytes) if candidates else []
        if not lim.pdf or len(chosen) == len(candidates):
            return chosen, [], []
        groups = [(0, f["file"]["path"], f["pages"]) for f in (cs["files"] if cs else []) if f["file"]["ext"] == "pdf"]
        groups += [(1, m["id"], pages) for m, _, pages in (cf["files"] if cf else []) if m["kind"] == "pdf"]
        whole: tuple[list[str], list[str]] = ([], [])
        count, tokens, size, keys = lim.pictures, room, most_bytes, set()
        for side, ref, pages in sorted(groups, key=lambda g: -sum(x.bytes for x in g[2])):   # the heaviest first
            pics = [x for x in pages if x.picture is not None]
            if not pics or len(pics) != len(pages) or len(pages) > NATIVE_PAGES:
                continue
            try:
                weight = os.path.getsize(pages[0].source) * 4 // 3
            except OSError:
                continue
            cost = sum(x.tokens + ai.text_tokens(x.text, per) for x in pages)   # each page: its picture and its text
            if (weight > NATIVE_BYTES * 4 // 3 or weight >= sum(x.bytes for x in pages) or len(pages) > count
                    or cost > tokens or weight > size or not chatslides.pdf_ok(pages[0].source, len(pages))):
                continue
            whole[side].append(ref)
            keys |= {x.key for x in pages}
            count, tokens, size = count - len(pages), tokens - cost, size - weight
        if not keys:
            return chosen, [], []
        rest = chatslides.allocate([x for x in candidates if x.key not in keys], count, tokens, size)
        if len(rest) + len(keys) <= len(chosen):        # no better than pictures alone
            return chosen, [], []
        return rest, whole[0], whole[1]

    @staticmethod
    def _manifest(files: Optional[dict[str, Any]], up: Optional[dict[str, Any]], cs: Optional[dict[str, Any]],
                  cf: Optional[dict[str, Any]], shots: list[Any], video: list[Any], read: list[Any], cut: set[str],
                  captured: int, local: bool, read_on: bool) -> str:
        """What the AI got of each chosen file, so it can say when something is missing."""
        if local:
            return ""
        notes = {x.key for x in read}
        lines: list[str] = []

        def one(name: str, pages: list[Any], pictured: set[str], whole: bool) -> str:
            n = len(pages)
            bits = []
            gone = [x.page for x in pages if x.key in cut]
            bits.append("the text of every page" + (f" except {chatread.ranges(gone)} (left out: too long)" if gone else ""))
            if whole:
                bits.append("the whole PDF as a file (every page as text and picture)")
            else:
                pics = [x.page for x in pages if x.key in pictured]
                if pics:
                    bits.append(f"pictures of {chatread.ranges(pics)}")
                later = [x.page for x in pages if x.key in notes]
                if later:
                    bits.append(f"{chatread.ranges(later)} read for you as notes (they come with each question)")
                rest = [x.page for x in pages if x.picture is not None and x.key not in pictured and x.key not in notes]
                if rest:
                    bits.append(f"no picture of {chatread.ranges(rest)}")
                blank = [x.page for x in pages if not x.text and x.picture is None
                         and not str(x.source).lower().endswith(".pdf")]
                if blank:
                    bits.append(f"nothing of {chatread.ranges(blank)} (no text, and its figure can't be sent)")
            return f"- {name} ({n} page{'s' if n != 1 else ''}): " + "; ".join(bits)

        images = {id(pic) for _, pic in (files["images"] if files else [])}
        for f in (cs["files"] if cs and files else []):
            pages = f["pages"]
            whole = any(isinstance(pic, chatslides.PdfFile) and pic.pdf == f["file"]["path"]
                        for _, pic in files["images"])
            lines.append(one(f["file"]["label"], pages, {x.key for x in pages if id(x.picture) in images}, whole))
        if files and files["summary"].get("missing"):
            lines.append(f"- {files['summary']['missing']} chosen slide file(s) are not on the student's PC: you did not "
                         f"get them.")
        up_pics = {id(pic) for _, pic in (up["images"] if up else [])}
        for m, item, pages in (cf["files"] if cf and up else []):
            whole = next((it["native"] for it in up["items"] if it["id"] == m["id"]), False)
            if m["kind"] == "image":
                lines.append(f"- {m['name']} (a photo): " + ("as a picture" if pages and id(pages[0].picture) in up_pics
                                                             else "not sent"))
            else:
                lines.append(one(m["name"], pages, {x.key for x in pages if id(x.picture) in up_pics}, whole))
        if captured:
            seen = len(video)
            later = sum(1 for x in shots if x.key in notes)
            lines.append(f"- {captured} slide(s) captured from the lecture videos: {seen} as pictures"
                         + (f", {later} more read for you as notes" if later else "")
                         + (f", {captured - seen - later} not sent" if captured - seen - later > 0 else ""))
        if not lines:
            return ""
        return ("=== WHAT YOU GOT OF THE CHOSEN FILES ===\n" + "\n".join(lines)
                + "\nIf a question needs a page you have only as text or as notes, or a file not listed here, say so "
                  "plainly (a [!NOTE] or [!WARNING] block).")

    def _skipped(self, r: Recording) -> bool:
        """A recording IEUStudia never transcribes on its own (too short, no sound): no notice that it is missing (its
        Transcribe button would queue nothing), as Transcribe for a class day leaves it out too."""
        return 0 < r.duration_s < self._s.settings.min_duration_s or no_sound(r, self.lib.warnings(r.delivery_id))

    @staticmethod
    def _notices(gone: int, untranscribed: list[Recording], built: Optional[dict[str, Any]], send: Any, video_total: int,
                 video_sent: int, lim: Any = None, read_on: bool = False, pages_left: int = 0,
                 up: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
        """What the student should know about a chat's material: class days gone, parts not transcribed, slide files
        not downloaded or cut, slide pictures trimmed or not taken by the AI, pages that went as text only while
        "Read everything" is off (notices.py; never sent to the AI)."""
        out = [notices.notice("days_gone", n=gone)] if gone else []
        if untranscribed:
            out.append(notices.notice("not_transcribed", n=len(untranscribed),
                                      dates=sorted({r.start.date().isoformat() for r in untranscribed})))
        files = built["summary"] if built else {}
        if files.get("missing"):          # their ids: the notice's Download button gets them
            chosen = set(chatslides.chosen_ids(built["catalog"], chatslides.normalize(send)))
            out.append(notices.notice("files_missing", n=files["missing"], ids=[
                f["id"] for f in built["catalog"]["files"] if f["id"] in chosen and not f["downloaded"]]))
        if files.get("cut") and not read_on:
            out.append(notices.notice("files_cut"))
        most = lim.pictures if lim is not None else MAX_SLIDES
        if ai.provider() not in ai.PLAN_AIS:
            if video_total or files.get("images_on") and files.get("files"):
                out.append(notices.notice("no_pictures"))
        elif video_sent < video_total and not read_on:
            course = files.get("page_images", sum(getattr(pic, "pages", 1) for _, pic in (built or {}).get("images") or []))
            out.append(notices.notice("slides_trimmed", "info", sent=video_sent, total=video_total,
                                      **({"files": course, "max": most} if course else {})))
        if pages_left and not read_on and ai.provider() in ai.PLAN_AIS:
            total = files.get("pages", 0) + sum(int(it.get("pages") or 0) for it in (up or {}).get("items") or [])
            out.append(notices.notice("pictures_left", "info", n=pages_left, total=max(total, pages_left)))
        return out

    def uploads_of(self, course_id: str, ref: str) -> str:
        """Whose files go with a chat being sized: a chat of this course (its id) or a new one (``draft-<token>``)."""
        ref = str(ref or "")
        if ref.startswith("draft-"):
            return chatfiles.draft_target(ref[6:])
        chat = self.lib.get_chat(ref) if ref else None
        return ref if chat is not None and chat.get("course_id") == course_id else ""

    @staticmethod
    def _files_part(ctx: dict[str, Any]) -> str:
        """The student's files' share of a chat that is too big, named (empty without files)."""
        items = (ctx["parts"]["uploads"] or {}).get("items") or []
        if not items:
            return ""
        names = ", ".join(i["name"] for i in items[:4]) + (", …" if len(items) > 4 else "")
        return " " + t("Your files take ~{u}k of it: {names}.", u=sum(i["tokens"] for i in items) // 1000, names=names)

    def slide_options(self, course_id: str, day_keys: list[str], focus: str = "full", captured: bool = False,
                      send: Any = None, uploads: str = "", model: str = "",
                      read_all: Optional[bool] = None) -> dict[str, Any]:
        """For the "send slides" chooser: the course's slide files, the files each choice takes, and what the
        choice (with the video's slides when ``captured``, the student's files of ``uploads``: uploads_of) adds to
        the chat."""
        days = self.ordered_days(course_id, self.all_days(course_id) if focus == "course" else day_keys)
        ctx = self.context(course_id, days, focus, captured, send, self.uploads_of(course_id, uploads), model, read_all)
        opts = chatslides.options(self.lib, course_id, days, send, ctx["slides_total"] if captured else 0, ctx["said"])
        return {**opts, "summary": ctx["files"] or opts["summary"], "read": ctx["read"],
                "video_total": ctx["slides_total"], "tokens": ctx["tokens"], "max_tokens": ctx["limit"]}

    def scope_options(self, course_id: str, model: str = "") -> dict[str, Any]:
        """The transcribed class days of a course with their size, for the "new chat" picker."""
        course = self.lib.get_course(course_id)
        if course is None:
            raise RuntimeError(t("Unknown course."))
        done = self.lib.transcribed_ids()
        keys = list(dict.fromkeys(r.day_key for r in self.lib.recordings(course_id) if r.delivery_id in done))
        days, per = [], ai.chars_per_token()
        for d in self._load_days(course_id, keys):
            if d is None:
                continue
            key, _, recs, trans = d
            full = compact_day(course, recs, trans)
            if not full:
                continue
            hl = compact_day(course, recs, trans, only_starred=True)
            parts = part_views(recs, trans)
            start = min(r.start for r in recs)
            days.append({
                "day_key": key, "date": start.date().isoformat(), "label": date_short(start.date()),
                "kind": recs[0].kind, "kind_label": t(kind_label(recs[0].kind)),
                "parts": sum(1 for p in parts if p.transcript),
                "stars": sum(1 for p in parts for para in p.paragraphs if para.star),
                "slides": sum(len({s.index for s in store.load_slides(r.delivery_id)}) for r in recs
                              if r.delivery_id in trans),
                "tokens": ai.text_tokens(full, per), "hl_tokens": ai.text_tokens(hl, per),
                "start": start.isoformat(),
            })
        days.sort(key=lambda x: x["start"], reverse=True)
        lim = chatlimits.of(ai.provider(), model or self._s.settings.ai_model)
        return {"course": {"id": course.id, "code": course.code, "name": course.name}, "days": days,
                "max_tokens": self.limit(model), "max_slides": min(MAX_SLIDES, lim.pictures),
                "limits": {m: self.limit(m) for m in MODELS} if lim.provider == "claude" else {}}

    def courses(self) -> list[dict[str, Any]]:
        """Courses that have at least one transcribed class day (an earlier term's too: a retake or make-up exam)."""
        done = self.lib.transcribed_ids()
        out = []
        for c in self.lib.courses(include_inactive=True):
            days = {r.day_key for r in self.lib.recordings(c.id) if r.delivery_id in done}
            if days:
                out.append({"id": c.id, "code": c.code, "name": c.name, "days": len(days)})
        return out

    # ------------------------------------------------------------------ chats
    def _scope_days(self, chat: dict[str, Any]) -> list[str]:
        return [str(k) for k in (chat.get("scope") or {}).get("days") or []]

    def _day_labels(self, chat: dict[str, Any], shown: bool = True) -> list[dict[str, Any]]:
        """The chat's class days with their labels in the language shown (``shown=False``: in English, as the exported
        chat says them)."""
        out = []
        for n, key in enumerate(self._scope_days(chat), start=1):
            try:
                _, recs = self._s.day(key)
                d = min(r.start for r in recs).date()
                kind = kind_label(recs[0].kind)
                out.append({"key": key, "n": n, "label": date_short(d), "kind": t(kind) if shown else kind,
                            "long": date_long(d) if shown else long_date(d), "gone": False})
            except KeyError:
                _, date_s, kind = (key.split("|") + ["", ""])[:3]
                kind = kind_label(kind)
                out.append({"key": key, "n": n, "label": date_s, "kind": t(kind) if shown else kind, "long": date_s,
                            "gone": True})
        return out

    def _title(self, chat: dict[str, Any], days: list[dict[str, Any]]) -> str:
        if chat.get("title"):
            return chat["title"]
        q = " ".join((chat.get("first_question") or "").split())
        if q:
            return q if len(q) <= 64 else q[:61].rstrip() + "…"
        return ""                           # no title yet: the page says "New chat" in its language

    def _scope_label(self, chat: dict[str, Any], days: list[dict[str, Any]]) -> str:
        if len(days) == 1:
            label = f"{days[0]['label']} · {days[0]['kind']}"
        elif days:
            label = tn(len(days), "{n} class day ({first} – {last})", "{n} class days ({first} – {last})",
                       first=days[0]["label"], last=days[-1]["label"])
        else:
            label = tn(0, "{n} class day", "{n} class days")
        if (chat.get("scope") or {}).get("focus") == "course":
            return tn(len(days), "Whole course · {n} lecture + course files", "Whole course · {n} lectures + course files")
        if (chat.get("scope") or {}).get("focus") == "highlights":
            label = t("{label} · highlights only", label=label)
        return label

    def summary(self, chat: dict[str, Any]) -> dict[str, Any]:
        course = self.lib.get_course(chat["course_id"])
        days = self._day_labels(chat)
        return {"id": chat["id"], "course_id": chat["course_id"], "course_code": course.code if course else "",
                "title": self._title(chat, days), "scope_label": self._scope_label(chat, days),
                "model": chat.get("model") or "sonnet", "updated_at": chat.get("updated_at"),
                "n_messages": chat.get("n_messages", 0), "running": chat["id"] in self._running,
                "effort": self._effort(chat),
                # the chat list's filter finds a chat by its first question too (a titled chat doesn't show it)
                "first_question": " ".join((chat.get("first_question") or "").split())[:300]}

    def list(self) -> list[dict[str, Any]]:
        out = []
        cutoff = (dt.datetime.now() - dt.timedelta(hours=1)).isoformat(timespec="seconds")
        for c in self.lib.chats():
            if c.get("n_messages"):
                out.append(self.summary(c))
            elif (c.get("created_at") or "") < cutoff and c["id"] not in self._running:
                self.lib.delete_chat(c["id"])          # started but never used
                chatfiles.forget(c["id"])
        return out

    def view(self, chat_id: str, ctx: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """The chat as the page shows it. ``ctx``: its context, when the caller just built it."""
        chat = next(iter(self.lib.chats(chat_id)), None)
        if chat is None:
            raise RuntimeError(t("This chat no longer exists."))
        scope = chat.get("scope") or {}
        if ctx is None:
            ctx = self.context(chat["course_id"], self._scope_days(chat), scope.get("focus", "full"),
                               bool(scope.get("slides")), scope.get("send"), chat_id, chat.get("model") or "",
                               scope.get("read_all"))
        course = self.lib.get_course(chat["course_id"])
        messages = [{"seq": m["seq"], "role": m["role"], "text": m["text"], "status": m["status"],
                     "usage": m.get("usage") or {}, "created_at": m["created_at"]} for m in self.lib.chat_messages(chat_id)]
        return {**self.summary(chat), "days": self._day_labels(chat), "focus": scope.get("focus", "full"),
                "slides": bool(scope.get("slides")), "context_tokens": ctx["tokens"], "images": ctx["video_images"],
                "send": chatslides.normalize(scope.get("send")), "files": ctx["files"],
                "course_name": course_display(course) if course else "", "messages": messages,
                "uploads": (ctx["parts"]["uploads"] or {}).get("items") or [], "limits": ai.usage_limits(),
                "read": ctx["read"], "read_all": scope.get("read_all"), "max_tokens": ctx["limit"]}

    def create(self, course_id: str, day_keys: list[str], focus: str = "full", slides: bool = False,
               model: str = "sonnet", level: str = "", send: Any = None, uploads: str = "",
               read_all: Optional[bool] = None) -> dict[str, Any]:
        """A new chat. ``level``: its reasoning level (effort.py; "" = the one in Settings); ``send``: the course's
        slide files that go along (chatslides); ``uploads``: the token of the files added before it (chatfiles);
        ``read_all``: "Read everything" (None: on for the whole course or many files)."""
        focus = focus if focus in FOCUS else "full"
        model = model if model in MODELS else "sonnet"
        days = self.ordered_days(course_id, self.all_days(course_id) if focus == "course" else day_keys)
        if not days:
            raise RuntimeError(t("Pick at least one transcribed class day."))
        send = chatslides.normalize(send)
        read_all = None if read_all is None else bool(read_all)
        ctx = self.context(course_id, days, focus, slides, send, chatfiles.draft_target(uploads), model, read_all)
        if ctx["tokens"] > ctx["limit"]:
            raise RuntimeError(t("That is too much for one chat (~{k}k tokens, the limit is {max}k). Pick fewer class "
                                 "days or use highlights only.", k=ctx["tokens"] // 1000, max=ctx["limit"] // 1000)
                               + self._files_part(ctx))
        chat_id = uuid.uuid4().hex[:12]
        scope = {"days": days, "focus": focus, "slides": bool(slides and ctx["slides_total"]),
                 "effort": level if level in LEVEL_NAMES else ""}   # "" = Settings' level (never the old model's)
        if send["mode"] != "none":
            scope["send"] = send
        if read_all is not None:
            scope["read_all"] = read_all
        self.lib.create_chat(chat_id, course_id, scope, model)
        chatfiles.adopt(uploads, chat_id)
        # the same material as saved (built again when the files added before it moved, or slides asked for none)
        return self.view(chat_id, ctx if not ctx["parts"]["uploads"] and (not slides or ctx["slides_total"]) else None)

    # ------------------------------------------------------------------ the student's files (chatfiles)
    def _clean_files(self) -> None:
        try:
            chatfiles.clean(lambda cid: self.lib.get_chat(cid) is not None)
        except Exception:  # noqa: BLE001 - tidying up only
            log.debug("Could not tidy the chats' files", exc_info=True)

    def update(self, chat_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        chat = self.lib.get_chat(chat_id)
        if chat is None:
            raise RuntimeError(t("This chat no longer exists."))
        fields: dict[str, Any] = {}
        if "title" in patch:
            fields["title"] = " ".join(str(patch["title"] or "").split())[:120]
        scope = dict((self.lib.get_chat(chat_id) or {}).get("scope") or {})
        if patch.get("model") in MODELS:
            if patch["model"] != chat.get("model"):   # a smaller window (Haiku): the chat must still fit in it
                self._fits(chat, scope, patch["model"])
            fields["model"] = patch["model"]          # the next question starts a new process with this model
        if patch.get("effort") in LEVEL_NAMES:        # the next question's reasoning level (Claude: a new process)
            fields["scope"] = scope = {**scope, "effort": patch["effort"]}
        if "read_all" in patch:                       # "Read everything": on, off, or None (as the chat's size says)
            scope = {k: v for k, v in scope.items() if k != "read_all"}
            if patch["read_all"] is not None:
                scope["read_all"] = bool(patch["read_all"])
            fields["scope"] = scope
        if "send" in patch or "slides" in patch:      # the slides that go along: the next question sends them anew
            if "slides" in patch:
                scope["slides"] = bool(patch["slides"])
            if "send" in patch:
                send = chatslides.normalize(patch["send"])
                if send["mode"] == "none":
                    scope.pop("send", None)
                else:
                    scope["send"] = send
            fields["scope"] = scope
            model = chat.get("model") or ""
            ctx = self.context(chat["course_id"], self._scope_days({"scope": scope}), scope.get("focus", "full"),
                               bool(scope.get("slides")), scope.get("send"), chat_id, model, scope.get("read_all"))
            old = chat.get("scope") or {}          # the student's files count too: they go with every question
            if ctx["tokens"] > ctx["limit"] and ctx["tokens"] > self.context(   # less than before: always fine
                    chat["course_id"], self._scope_days(chat), old.get("focus", "full"), bool(old.get("slides")),
                    old.get("send"), chat_id, model, old.get("read_all"))["tokens"]:
                raise RuntimeError(t("These slides don't fit in this chat: it would hold ~{k}k tokens, the limit is "
                                     "{max}k. Choose fewer files or turn off the pictures.", k=ctx["tokens"] // 1000,
                                     max=ctx["limit"] // 1000) + self._files_part(ctx))
        if fields:
            self.lib.update_chat(chat_id, **fields)
        return self.view(chat_id)

    def _fits(self, chat: dict[str, Any], scope: dict[str, Any], model: str) -> None:
        """Changing a chat's model (Claude's menu): refused when its material doesn't fit the new model's share, as
        create() refuses it; a move to a model at least as big is always fine."""
        if self.limit(model) >= self.limit(chat.get("model") or ""):
            return
        ctx = self.context(chat["course_id"], self._scope_days({"scope": scope}), scope.get("focus", "full"),
                           bool(scope.get("slides")), scope.get("send"), chat["id"], model, scope.get("read_all"))
        if ctx["tokens"] > ctx["limit"]:
            raise RuntimeError(t("This chat is too big for {model} (~{k}k tokens, its limit is {max}k). Keep {current}, "
                                 "or start a new chat with fewer class days or highlights only.",
                                 model=model.capitalize(), k=ctx["tokens"] // 1000, max=ctx["limit"] // 1000,
                                 current=(chat.get("model") or "sonnet").capitalize()))

    @staticmethod
    def _too_big(ctx: dict[str, Any], model: str) -> Optional[str]:
        """A chat whose material no longer fits its model's window (the AI or the ChatGPT model changed in Settings
        since it was made): what to do, before a question fails for good with the service's own words. Measured
        against the window itself, not the app's margin, so a chat a little over the margin still works."""
        lim = ctx.get("lim")
        if lim is None or lim.provider == "local" or not lim.window or ctx["tokens"] <= lim.window:
            return None
        k, most = ctx["tokens"] // 1000, lim.window // 1000
        if lim.provider == "claude":
            return t("This chat no longer fits in {model} (~{k}k tokens, {model} holds {max}k). Pick a bigger model in "
                     "the chat's model menu, or start a new chat with fewer class days or highlights only.",
                     model=(model or "sonnet").capitalize(), k=k, max=most)
        return t("This chat no longer fits in the ChatGPT model chosen in Settings → AI (~{k}k tokens, it holds "
                 "{max}k). Choose a model with a bigger window there, or start a new chat with fewer class days or "
                 "highlights only.", k=k, max=most)

    def delete(self, chat_id: str) -> None:
        self.stop(chat_id)
        self._drop(chat_id)
        self.lib.delete_chat(chat_id)
        chatfiles.forget(chat_id)

    def stop(self, chat_id: str) -> None:
        with self._lock:
            ev = self._running.get(chat_id)
        if ev is not None:
            ev.set()

    # ------------------------------------------------------------------ asking
    def send(self, chat_id: str, text: str) -> dict[str, Any]:
        chat = self.lib.get_chat(chat_id)
        if chat is None:
            raise RuntimeError(t("This chat no longer exists."))
        text = (text or "").strip()
        if not text:
            raise ValueError(t("Type a question first."))
        with self._lock:
            if chat_id in self._running:
                raise RuntimeError(t("{ai} is still answering the previous question.", ai=ai.ai_name()))
            stop = self._running[chat_id] = threading.Event()
        try:
            history = self.lib.chat_messages(chat_id)
            user_seq = self.lib.add_chat_message(chat_id, "user", text)
            seq = self.lib.add_chat_message(chat_id, "assistant", "", status="streaming")
        except Exception:
            with self._lock:
                self._running.pop(chat_id, None)
            raise
        threading.Thread(target=self._answer, args=(chat, history, text, seq, stop), name="chat", daemon=True).start()
        return {"user_seq": user_seq, "seq": seq}

    def retry(self, chat_id: str, seq: int) -> dict[str, Any]:
        """Ask the question behind a failed last answer again, replacing both (no duplicate in the history)."""
        msgs = self.lib.chat_messages(chat_id)
        if not msgs or msgs[-1]["seq"] != seq or msgs[-1]["role"] != "assistant" or msgs[-1]["status"] != "error":
            raise RuntimeError(t("Only a failed last answer can be tried again."))
        question = next((m for m in reversed(msgs) if m["role"] == "user"), None)
        if question is None:
            raise RuntimeError(t("Only a failed last answer can be tried again."))
        with self._lock:
            if chat_id in self._running:
                raise RuntimeError(t("{ai} is still answering the previous question.", ai=ai.ai_name()))
        self.lib.delete_chat_messages(chat_id, question["seq"])
        return {**self.send(chat_id, question["text"]), "removed_from": question["seq"], "question": question["text"]}

    def _emit(self, chat_id: str, seq: int, state: str, text: str, **extra: Any) -> None:
        self._s.emit({"type": "chat", "chat_id": chat_id, "seq": seq, "state": state, "text": text, **extra})

    def _session(self, chat: dict[str, Any]) -> tuple[ai.ClaudeSession, Optional[list[tuple[str, Path]]]]:
        """The chat's live Claude process, or a new one when there is none for this model and material; for a
        new one the slide images to attach to its first message are returned too (else None)."""
        cid = chat["id"]
        p, level = ai.provider(), self._effort(chat)
        material = {k: v for k, v in (chat.get("scope") or {}).items() if k != "effort"}
        material["uploads"] = chatfiles.ids(cid)        # a file added or removed: it all goes again
        # Claude's process keeps the level it was started with; the others take a new one with the next question
        key = (p, chat.get("model"), json.dumps(material, sort_keys=True), level if p == "claude" else "")
        with self._lock:
            live = self._live.get(cid)
            if live is not None and live.alive() and live.key == key:
                _set_level(live, level)
                return live, None
            old = self._live.pop(cid, None)
        if old is not None:
            self._close_later(old)
        scope = chat.get("scope") or {}
        ctx = self.context(chat["course_id"], self._scope_days(chat), scope.get("focus", "full"), bool(scope.get("slides")),
                           scope.get("send"), cid, chat.get("model") or "", scope.get("read_all"))
        if not ctx["n_days"]:
            raise RuntimeError(t("The class days of this chat are no longer available."))
        too_big = self._too_big(ctx, chat.get("model") or "")
        if too_big:
            raise RuntimeError(too_big)
        session = ai.new_session(ctx["system"], model=chat.get("model") or "sonnet", effort=level)
        session.key = key
        session.notices = ctx["notices"]                # shown with the answer it starts with (not sent to the AI)
        session.upload_passages = ctx["upload_passages"]   # the offline model: the student's files, matched per question
        # "Read everything": the pages read in batches before a question, and what this process has had of them
        session.full_days = ctx["full_days"]           # (the matching passages of other lectures leave them out)
        session.reading = {"pages": ctx["read_pages"], "lim": ctx["lim"], "model": chat.get("model") or "sonnet",
                           "room": ctx["room"], "first_bytes": ctx["first_bytes"], "on": ctx["read"]["on"],
                           "picture_pages": ctx["picture_pages"]}
        # what is in its conversation, counted only once a question really went (session.ask): Claude keeps it all,
        # the others get the first message's pictures again with every question
        session.notes_given = False                     # Claude got the notes (it keeps them)
        session.pages_sent = set()                      # the pages it got in full with a question
        session.pictures_sent = sum(getattr(pic, "pages", 1) for _, pic in ctx["images"])
        session.extra_tokens = 0                        # the notes and pictures added with questions (Claude)
        session.weight = chatlimits.text_bytes(ctx["system"])     # what its next request weighs before this question
        with self._lock:
            self._live[cid] = session
            idle = sorted((s.last_used, c) for c, s in self._live.items() if c != cid and c not in self._running)
            extra = [self._live.pop(c) for _, c in idle[:max(0, len(self._live) - MAX_LIVE)]]
        for s in extra:
            self._close_later(s)
        return session, ctx["images"]

    def _answer(self, chat: dict[str, Any], history: list[dict[str, Any]], question: str, seq: int,
                stop: threading.Event) -> None:
        cid = chat["id"]
        text = ""
        last_emit, last_save = [0.0], [time.monotonic()]
        shown: list[dict[str, Any]] = []               # the student's notices with this answer (never the AI's input)
        made_by = ai.provider()                        # kept with them: they name this AI, not the one chosen later
        # what the student waits for: getting the material ready, sending, thinking, writing, a retry (aiwait)
        # (its usage: the reading's progress is over once it says something: the page shows the wait then)
        wait = aiwait.Wait(lambda snap: self._emit(cid, seq, "streaming", text, wait=snap,
                                                   usage={"notices": shown} if shown else {}),
                           ai=ai.ai_name(), provider=made_by, model=chat.get("model") or "", level=self._effort(chat),
                           kind="chat", where="chat")

        def on_text(so_far: str) -> None:
            nonlocal text
            text = so_far
            now = time.monotonic()
            if now - last_emit[0] >= EMIT_EVERY_S:
                last_emit[0] = now
                self._emit(cid, seq, "streaming", so_far, wait=wait.snapshot(),
                           **({"usage": {"notices": shown}} if shown else {}))
            if now - last_save[0] >= SAVE_EVERY_S:     # keep the partial answer if the app is closed meanwhile
                last_save[0] = now
                self.lib.update_chat_message(cid, seq, so_far, "streaming")

        try:
            self._emit(cid, seq, "streaming", "", wait=wait.snapshot())
            session, images = self._session(chat)
            if images is not None:                          # a new process: everything goes with its first question
                wait.stage(kind="chat.first")
            content: list[dict[str, Any]] = []
            weight = getattr(session, "weight", 0)          # what the request weighs so far (chatlimits)
            if images is not None:                          # a new process: give it what it has not seen
                shown += notices.fresh(getattr(session, "notices", None) or [], history)
                if shown:
                    self._emit(cid, seq, "streaming", "", usage={"notices": shown})
                unread = unread_mine = heavy = 0
                mine = False
                # the pictures' real weight, within what the first request may carry (the room for the notes and the
                # conversation kept): a page that would go over goes as text only, and the student is told
                rd = getattr(session, "reading", None) or {}
                cap = rd.get("first_bytes") or 0
                dropped: list[Any] = []                     # the pages whose pictures were left out (chatread.Page)

                def add(label: str, block: dict[str, Any], pages: int = 1) -> bool:
                    nonlocal weight, heavy
                    w = chatlimits.block_bytes(block) + chatlimits.text_bytes(label) + 40
                    if cap and weight + w > cap:
                        heavy += pages
                        dropped.extend((rd.get("picture_pages") or {}).get(label) or [])
                        return False
                    content.extend([{"type": "text", "text": label}, block])
                    weight += w
                    return True

                def header(text: str) -> None:
                    nonlocal weight
                    content.append({"type": "text", "text": text})
                    weight += chatlimits.text_bytes(text) + 40

                start = len(content)            # where the pictures begin (ChatGPT/Codex resend from here to the last)
                if images:
                    if not chatfiles.is_upload(images[0][1]):
                        header("The slide images of these lectures, in order:")
                    for label, path in images:
                        if stop.is_set():
                            break
                        if (chatfiles.is_upload(path) or isinstance(path, chatslides.PdfFile) and chatfiles.pages_folder(
                                Path(path.pdf))) and not mine:
                            mine = True
                            header("Pictures of the files the student added, in order:")
                        if isinstance(path, chatslides.PdfFile):             # a whole PDF, as a file
                            pdf = path.path()
                            if pdf is None:
                                unread += path.pages
                                continue
                            add(label, ai.pdf_block(pdf, path.name), path.pages)
                            continue
                        if isinstance(path, (chatslides.PagePicture, chatfiles.UploadPicture, chatfiles.PptxPicture)):
                            upload = chatfiles.is_upload(path)          # a page: drawn now
                            path = path.path()
                            if path is None:            # the course's slide files, or the student's own files
                                if upload:
                                    unread_mine += 1
                                else:
                                    unread += 1
                                continue
                        add(label, ai.image_block(path))
                if unread:
                    shown += notices.fresh([notices.notice("pages_unreadable", n=unread)], history)
                if unread_mine:
                    shown += notices.fresh([notices.notice("uploads_unreadable", n=unread_mine)], history)
                if heavy:
                    later = self._text_only(session, dropped)
                    if dropped:                     # the AI's list of what it got says pictures: it is told otherwise,
                        note = text_only_note(dropped, later)   # before the pictures, so the AIs that get them again
                        content.insert(start, {"type": "text", "text": note})     # with every question get it too
                        weight += chatlimits.text_bytes(note) + 40
                    shown += notices.fresh([notices.notice("request_full", "info", n=heavy,
                                                           **({"read": True} if later else {}))], history)
                if history and hasattr(session, "seed"):
                    session.seed(history)               # it sends the conversation with every question itself
                elif history:
                    content.append({"type": "text", "text": replay_text(history)})
                    weight += chatlimits.text_bytes(content[-1]["text"])
            if hasattr(session, "seed"):                # the conversation goes with every question too
                weight += sum(chatlimits.text_bytes(q) + chatlimits.text_bytes(a)
                              for q, a in (getattr(session, "history", None) or [])[-ai.ChatGPTSession.MAX_TURNS:])
            asked_from = len(content)                       # what follows weighs on top of ``weight``
            blocks, read, commit = self._read(session, cid, seq, question, history, stop, shown, weight, wait)
            content += blocks
            if read and read.get("pages") and not read.get("again"):
                wait.read(int(read["pages"]))             # "Went through all N pages": said with what comes next
                wait.phase("prepare")                     # (the question is put together: the reading is over)
            shown += self._refused(read, history, shown)         # Codex turned the chosen model down while reading
            if read and read.get("full"):
                shown += notices.fresh([notices.notice("pictures_full", "info")], history)
            if read and read.get("unread"):
                shown += notices.fresh([notices.notice("pages_not_read", n=len(read["unread"]),
                                                       labels=read["unread"][:12])], history)
            scope = chat.get("scope") or {}
            try:                                            # course files and other lectures that match the question
                found, sources = retrieve(self.lib, chat["course_id"], question,
                                          skip_days=self._skip_days(chat, session))
            except Exception:  # noqa: BLE001 - the answer works without it
                log.debug("retrieval failed", exc_info=True)
                found, sources = "", []
            # the offline model: the passages of the student's files that match the question (and the one before it),
            # first, so that its small window keeps them before the other passages
            asked = " ".join([question] + [m.get("text") or "" for m in history[-2:] if m.get("role") == "user"])
            mine = chatfiles.local_pick(getattr(session, "upload_passages", None) or [], asked)
            if mine:
                content.append({"type": "text", "text": mine})
            if found:
                content.append({"type": "text", "text": found})
            content.append({"type": "text", "text": question})
            if stop.is_set():
                res = {"text": "", "usage": {}, "stopped": True}
                if images is not None:      # it never got its first message: start afresh next time
                    self._drop(cid)
            else:
                wait.expect(weight + sum(chatlimits.block_bytes(b) for b in content[asked_from:]),
                            0 if hasattr(session, "seed") else getattr(session, "pictures_sent", 0))
                with aiwait.tracking(stop, wait):
                    res = session.ask(content, on_text, stop)
                self._sent(session, content, res["text"])     # it went: what the conversation now holds
                commit()
                if res.get("model_refused"):                  # Codex turned the chosen model down: said once
                    shown += notices.fresh([notices.notice("codex_model", "info", model=res["model_refused"])], history)
            text = res["text"] or text
            state = "stopped" if res["stopped"] else "done"
            usage = {**(res["usage"] or {}), "ai": made_by, **({"sources": sources} if sources else {}),
                     **({"read": read} if read else {}),
                     **({"notices": shown, "notices_ai": made_by} if shown else {})}
            self.lib.update_chat_message(cid, seq, text, state, usage)
            if not session.alive():
                self._drop(cid)
            self._emit(cid, seq, state, text, usage=usage, limits=ai.usage_limits())
        except Cancelled:                           # stopped while the pages were read: what was read is kept
            reading = kept(self._reading.pop(cid, None))
            shown += self._refused(reading, history, shown)
            usage = {"read": {**reading, "stopped": True}, "ai": made_by,
                     **({"notices": shown, "notices_ai": made_by} if shown else {})}
            self.lib.update_chat_message(cid, seq, text, "stopped", usage)
            self._drop(cid)                         # it may not have got its first message: start afresh next time
            self._emit(cid, seq, "stopped", text, usage=usage, limits=ai.usage_limits())
        except Exception as exc:  # noqa: BLE001 - reported in the chat
            log.warning("Chat %s failed: %s", cid, exc)
            msg = str(exc) or t("{ai} could not answer.", ai=ai.ai_name())
            # failed while the pages were read (Try again goes on from there), or after (none is read again): said
            reading = kept(self._reading.get(cid))
            shown += self._refused(reading, history, shown)
            extra = {"read": {**reading, "failed": True}} if reading and reading.get("pages") else {}
            self.lib.update_chat_message(cid, seq, text, "error", {"error": msg, "notices_ai": made_by, **extra,
                                                                   **({"notices": shown} if shown else {})})
            self._drop(cid)
            self._emit(cid, seq, "error", text, error=msg, limits=ai.usage_limits(),
                       usage={"notices_ai": made_by, **extra, **({"notices": shown} if shown else {})})
        finally:
            self._reading.pop(cid, None)
            with self._lock:
                self._running.pop(cid, None)
            if self.lib.get_chat(cid) is None:     # deleted while its process was starting
                self._drop(cid)
            lim = ai.usage_limits()
            if lim:
                self.lib.set_meta("claude_limits", json.dumps(lim))

    def _skip_days(self, chat: dict[str, Any], session: Any) -> list[str]:
        """The class days whose whole transcript the AI has: no passages of them are searched for a question (the
        chosen days; for the whole course, the lectures that went whole)."""
        focus = (chat.get("scope") or {}).get("focus", "full")
        if focus == "full":
            return self._scope_days(chat)
        return list(getattr(session, "full_days", None) or []) if focus == "course" else []

    @staticmethod
    def _text_only(session: Any, dropped: list[Any]) -> bool:
        """The pages whose pictures the first message couldn't carry (their real weight was over what it may carry):
        with "Read everything" on they are read in batches like the rest (their notes take the room their pictures
        were counted in). Whether they are."""
        rd = getattr(session, "reading", None)
        session.pictures_sent = max(0, getattr(session, "pictures_sent", 0) - len(dropped))
        if not dropped or not rd or not rd.get("on") or rd.get("lim") is None or not rd["lim"].pictures:
            return False
        known = {p.key for p in rd.get("pages") or []}
        extra = [p for p in dropped if p.key not in known]
        rd["pages"] = list(rd.get("pages") or []) + extra
        rd["room"] = int(rd.get("room") or 0) + sum(p.tokens for p in extra)
        return True

    @staticmethod
    def _refused(read: Optional[dict[str, Any]], history: list[dict[str, Any]],
                 shown: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """The notice that Codex turned the chosen model down in a "Read everything" batch (the batch was read again
        with its default model, so the chat's own question never sees the refusal); taken out of ``read``, and not
        given twice (``shown``: this answer's notices so far)."""
        refused = read.pop("model_refused", "") if read else ""
        if not refused and ai.provider() == "codex":    # turned down in a retry that was then stopped or failed
            chosen = appconfig.load().codex_model
            refused = chosen if chosen and chosen in ai.codex_rejected() else ""
        if not refused:
            return []
        return notices.fresh([notices.notice("codex_model", "info", model=refused)],
                             [*history, {"usage": {"notices": shown}}])

    @staticmethod
    def _sent(session: Any, content: list[dict[str, Any]], answer: str) -> None:
        """A question went: what the AI's next request weighs before its own question (chatlimits). Claude keeps the
        whole conversation (every block and answer stays in it); the others get the first message's pictures again
        with every question (their conversation is counted per question)."""
        if hasattr(session, "seed"):
            session.weight = chatlimits.text_bytes(getattr(session, "system", "") or "") + sum(
                chatlimits.block_bytes(b) for b in getattr(session, "slides", None) or [])
        else:
            session.weight = (getattr(session, "weight", 0) + sum(chatlimits.block_bytes(b) for b in content)
                              + chatlimits.text_bytes(answer or ""))

    def _read(self, session: Any, cid: str, seq: int, question: str, history: list[dict[str, Any]],
              stop: threading.Event, shown: list[dict[str, Any]],
              weight: int = 0, wait: Any = None) -> tuple[list[dict[str, Any]], Optional[dict[str, Any]], Any]:
        """"Read everything" before a question: the pages that don't go whole are read in batches (chatread; kept, so
        only what isn't read yet), with progress in the chat. Returns the blocks for the question (the notes, the pages
        that matter most for it as pictures; this question's only), what was read, and what to note once the question
        has really gone (``commit``: a Stop before it leaves the session as it was).

        Claude keeps the notes in its conversation: they go once; the others get them every time. What a question adds
        stays within the room the chat kept for it (``reading["room"]`` tokens; Claude: less what earlier questions
        added) and within what one request may weigh (``weight``: the request so far): notes that would not fit are
        shortened, and pictures that would not fit are left out (``full``: said to the student)."""
        rd = getattr(session, "reading", None) or {}
        pages, lim = rd.get("pages") or [], rd.get("lim")
        if not pages or lim is None:
            return [], None, lambda: None
        stateless = hasattr(session, "seed")               # ChatGPT, the Codex CLI, the offline model
        local = lim.provider == "local"
        per = ai.chars_per_token(lim.provider)
        blocks: list[dict[str, Any]] = []
        room = int(rd.get("room") or 0) - (0 if stateless else getattr(session, "extra_tokens", 0))
        size = lim.request_bytes - weight if lim.request_bytes else None
        used = 0                                            # tokens this question adds

        saved = [0.0]

        def progress(st: dict[str, Any]) -> None:
            info = {k: v for k, v in st.items() if k != "notes"}
            self._reading[cid] = info
            if info.get("total"):                          # nothing to read: no progress to show
                self._emit(cid, seq, "streaming", "", usage={"reading": info, **({"notices": shown} if shown else {})},
                           **({"wait": wait.snapshot()} if wait is not None else {}))
                if time.monotonic() - saved[0] >= READING_SAVE_S:   # if the app closes now, the answer says what
                    saved[0] = time.monotonic()                     # was kept (its notes are on disk already)
                    self.lib.update_chat_message(cid, seq, "", "streaming", {"reading_kept": kept(info)})

        if getattr(session, "notes_given", False) and not stateless:
            notes = chatread.cached(pages, lim)             # already in the conversation
            res = {"pages": len(pages), "read": len(notes), "cached": len(notes), "done": 0, "total": 0,
                   "unread": [], "again": True}
        else:
            res = chatread.read(pages, question, lim, chatread.default_request(lim, rd.get("model", "")), stop,
                                progress)
            notes = res.pop("notes")
            if local:                                       # its window holds a few pages: the notes that match
                asked = " ".join([question] + [m.get("text") or "" for m in history[-2:] if m.get("role") == "user"])
                text = chatread.pick(pages, notes, asked)
            else:
                fitted, short = chatread.fit_notes(pages, notes, max(0, room), per)
                if short:
                    log.info("Read everything: the notes were shortened to fit the %d tokens left", room)
                    res["shortened"] = True
                text = chatread.notes_text(pages, fitted)
            if text:
                blocks.append({"type": "text", "text": text, "_turn": True})
                used += ai.text_tokens(text, per)
                if size is not None:
                    size -= chatlimits.block_bytes(blocks[-1])
        # never more pictures than the AI takes: those of the chat's first message go with every request (ChatGPT)
        # or stay in the conversation (Claude, which also keeps the ones sent with earlier questions); nor more than
        # the room left, in tokens and in the request's weight
        most = min(chatread.RELEVANT_PAGES, lim.pictures - getattr(session, "pictures_sent", 0))
        done = set() if stateless else getattr(session, "pages_sent", set())
        fresh = [p for p in pages if p.key not in done]
        best = chatread.relevant(fresh, notes, question, max(0, most)) if lim.pictures else []
        added: list[str] = []
        full = (len(chatread.relevant(fresh, notes, question, chatread.RELEVANT_PAGES)) - len(best)
                if lim.pictures and most < chatread.RELEVANT_PAGES else 0)      # the AI's count of pictures reached
        for p in best:
            if used + p.tokens > room or (size is not None and size < p.bytes):
                full += 1
                continue
            path = p.picture.path() if hasattr(p.picture, "path") else p.picture
            if path is None:
                continue
            label = {"type": "text", "text": f"{p.label} (in full: it matters for this question)", "_turn": True}
            block = {**ai.image_block(Path(path)), "_turn": True}
            w = chatlimits.block_bytes(block) + chatlimits.block_bytes(label)
            if size is not None and w > size:               # heavier than it was counted
                full += 1
                continue
            blocks += [label, block]
            added.append(p.key)
            used += p.tokens
            if size is not None:
                size -= w
        res["relevant"] = len(added)
        if full:
            res["full"] = full

        def commit() -> None:
            """The question went: Claude now holds these notes and pictures (the others get them again each time)."""
            if stateless:
                return
            session.notes_given = True
            session.pages_sent = getattr(session, "pages_sent", set()) | set(added)
            session.pictures_sent = getattr(session, "pictures_sent", 0) + len(added)
            session.extra_tokens = getattr(session, "extra_tokens", 0) + used
        return blocks, res, commit

    def _effort(self, chat: dict[str, Any]) -> str:
        """The chat's reasoning level: its own, else (a ChatGPT chat from before) the one its model level meant, else
        the one in Settings."""
        scope = chat.get("scope") or {}
        return effort.resolve("chat", scope.get("effort", ""), legacy="" if "effort" in scope else chat.get("model") or "",
                              settings=self._s.settings)

    # ------------------------------------------------------------------ processes
    def _close_later(self, session: ai.ClaudeSession) -> None:
        th = threading.Thread(target=session.close, name="chat-close", daemon=True)
        th.start()
        with self._lock:
            self._closing = [x for x in self._closing if x.is_alive()] + [th]

    def _drop(self, chat_id: str) -> None:
        with self._lock:
            s = self._live.pop(chat_id, None)
        if s is not None:
            self._close_later(s)

    def _reaper(self) -> None:
        while True:
            time.sleep(30)
            now = time.monotonic()
            with self._lock:
                idle = [c for c, s in self._live.items()
                        if c not in self._running and (now - s.last_used > IDLE_CLOSE_S or not s.alive())]
            for c in idle:
                self._drop(c)

    def shutdown(self) -> None:
        """App is closing: end every Claude process right away."""
        with self._lock:
            for ev in self._running.values():
                ev.set()
            sessions = list(self._live.values())
            self._live.clear()
            closing = list(self._closing)
        for s in sessions:
            s.terminate()
        for th in closing:
            th.join(timeout=6)

    # ------------------------------------------------------------------ export
    def export(self, chat_id: str) -> Path:
        v = self.view(chat_id)
        course = self.lib.get_course(v["course_id"])
        if course is None:
            raise RuntimeError(t("Unknown course."))
        en = self._day_labels(self.lib.get_chat(chat_id) or {}, shown=False)      # the exported file stays English
        days = ", ".join(f"{d['long']} ({d['kind']})" for d in en)
        answers = [m for m in v["messages"] if m["role"] != "user"]     # the AI that wrote them, not the one chosen now
        made_by = ai.answered_by(answers[-1]) if answers else ""
        title = v["title"] or "New chat"         # the heading and the file name stay English
        lines = [f"# {title}", "",
                 f"**Course:** {v['course_name']}  ", f"**Class days:** {days}  ",
                 f"**Model:** {ai.model_label(v['model'], v.get('effort', ''), made_by)}" + (" · highlights only" if v["focus"] == "highlights" else ""),
                 "", f"*Chat with {' and '.join(ai.chat_ais(answers))} in IEUStudia, exported "
                 f"{dt.datetime.now():%Y-%m-%d %H:%M}.*", "", "---", ""]
        for m in v["messages"]:
            if m["role"] == "user":
                lines += [f"**You:** {m['text'].strip()}", ""]
            else:              # the app's notices and an error as alert blocks (GitHub and Obsidian draw them)
                who = ai.PROVIDER_LABELS.get(ai.answered_by(m)) or "AI"
                notes = [notices.markdown(n) for n in (m["usage"].get("notices") or [])]
                if m["status"] == "error":
                    notes.append(notices.alert("CAUTION", f"{who} could not answer: {m['usage'].get('error') or 'error'}"))
                if not m["text"].strip() and not notes:
                    continue
                lines += [f"**{who}:**", ""] + [x for n in notes for x in (n, "")]
                if m["text"].strip():
                    lines += [m["text"].strip() + (" *(stopped)*" if m["status"] == "stopped" else ""), ""]
                lines += ["---", ""]
        stamp = ((self.lib.get_chat(chat_id) or {}).get("created_at") or "")[:10]
        name = safe_name(f"{stamp} - {title}" if stamp else title, limit=110) + f" [{chat_id[:6]}]"
        path = course_dir(self._s.output_root, course, self.lib.courses(True)) / "AI chats" / f"{name}.md"
        atomic_write(path, dollar_math("\n".join(lines).rstrip()) + "\n")
        return path
