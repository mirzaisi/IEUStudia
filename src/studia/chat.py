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

from . import ai, chatslides, effort, store
from .domain import Course, Recording
from .i18n import date_long, date_short, t, tn
from .knowledge import course_brief, retrieve
from .outputs.ai_copy import compact_day
from .outputs.common import course_display, kind_label, long_date
from .outputs.markdown import part_views
from .outputs.mathmd import dollar_math
from .outputs.writer import atomic_write, course_dir, safe_name

log = logging.getLogger("studia")

MAX_CONTEXT_TOKENS = 150_000      # leaves room for the conversation in Claude's 200k context window
MAX_SLIDES = 60
IDLE_CLOSE_S = 3300               # Claude Code keeps a subscription's prompt cache for an hour: a question after a
                                  # break still reads the slides and the conversation from it in the same process
MAX_LIVE = 2                      # Claude Code processes kept open at once (~160 MB of memory each while idle)
MODELS = ("haiku", "sonnet", "opus")
FOCUS = ("full", "highlights", "course")      # course: every lecture's highlights + search per question
EMIT_EVERY_S = 0.08
SAVE_EVERY_S = 3.0


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
    lines = [
        "You are a friendly, precise study assistant for a university engineering student, chatting about the "
        "student's own lectures. They are given below as automatic speech-recognition transcripts: mostly English, "
        "with Turkish passages tagged [TR]. Names, symbols and formulas may be misheard — fix obvious errors from "
        "context and say when you are guessing.",
        "",
        fmt,
    ]
    if focus in ("highlights", "course"):
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
        self._closing: list[threading.Thread] = []
        try:
            ai.restore_limits(json.loads(self.lib.get_meta("claude_limits") or "{}"))
        except ValueError:
            pass
        threading.Thread(target=_clean_stale_workdirs, daemon=True).start()
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

    def context(self, course_id: str, day_keys: list[str], focus: str = "full", slides: bool = False,
                send: Any = None) -> dict[str, Any]:
        """System prompt and slide images for ``day_keys`` (already in lecture order; a day that is gone keeps
        its L-number so citations in older answers stay valid). ``send``: the course's slide files that go too
        (see chatslides)."""
        multi = len(day_keys) > 1
        texts: list[str] = []
        images: list[tuple[str, Path]] = []        # every slide image available, whether attached or not
        for n, d in enumerate(self._load_days(course_id, day_keys), start=1):
            if d is None:
                continue
            _, course, recs, trans = d
            label = f"L{n}" if multi else ""
            sl = {r.delivery_id: store.load_slides(r.delivery_id) for r in recs}
            text = compact_day(course, recs, trans, sl if slides else None, label=label,
                               only_starred=focus in ("highlights", "course"))
            if not text:
                continue
            texts.append(text)
            for p in part_views(recs, trans, sl):
                seen: set[int] = set()
                for s in p.slides if p.transcript else []:
                    if s.index not in seen and s.path and os.path.isfile(s.path):
                        seen.add(s.index)
                        images.append((f"[{label + ' ' if label else ''}Part {p.index}, slide {s.index}]",
                                       Path(s.path)))
        attached = images[:MAX_SLIDES] if slides else []
        said = "\n".join(texts)                   # the lecture's slides are checked against what was taught
        files = (chatslides.build(self.lib, course_id, day_keys, send, len(images) if slides else 0, said=said)
                 if chatslides.normalize(send)["mode"] != "none" else None)
        if files is not None:          # the course's pages first, the video's slides share what is left
            attached = files["images"] + attached[:files["video_images"]]
        brief = course_brief(self.lib, self._s.settings, course_id)
        note = chatslides.prompt_note(files["summary"]) if files else ""
        prompt = (system_prompt(len(day_keys), focus, bool(attached)) + (f"\n\n{note}" if note else "")
                  + (f"\n\n=== COURSE ===\n\n{brief}" if brief else "")
                  + (f"\n\n=== COURSE SLIDES ===\n\n{files['text']}" if files and files["text"] else "")
                  + "\n\n=== LECTURES ===\n\n" + "\n\n".join(texts))
        return {"system": prompt, "images": attached, "slides_total": len(images), "n_days": len(texts),
                "video_images": len(attached) - (len(files["images"]) if files else 0),
                "files": files["summary"] if files else None, "said": said,
                "tokens": ai.estimate_tokens(prompt, len(attached))["input"]}

    def slide_options(self, course_id: str, day_keys: list[str], focus: str = "full", captured: bool = False,
                      send: Any = None) -> dict[str, Any]:
        """For the "send slides" chooser: the course's slide files, the files each choice takes, and what the
        choice (with the video's slides when ``captured``) adds to the chat."""
        days = self.ordered_days(course_id, self.all_days(course_id) if focus == "course" else day_keys)
        ctx = self.context(course_id, days, focus, captured, send)
        return {**chatslides.options(self.lib, course_id, days, send, ctx["slides_total"] if captured else 0,
                                     ctx["said"]),
                "video_total": ctx["slides_total"], "tokens": ctx["tokens"], "max_tokens": MAX_CONTEXT_TOKENS}

    def scope_options(self, course_id: str) -> dict[str, Any]:
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
        return {"course": {"id": course.id, "code": course.code, "name": course.name}, "days": days,
                "max_tokens": MAX_CONTEXT_TOKENS, "max_slides": MAX_SLIDES}

    def courses(self) -> list[dict[str, Any]]:
        """Courses that have at least one transcribed class day."""
        done = self.lib.transcribed_ids()
        out = []
        for c in self.lib.courses():
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
            return t("Whole course · {n} lectures' highlights + course files", n=len(days))
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
                "effort": self._effort(chat)}

    def list(self) -> list[dict[str, Any]]:
        out = []
        cutoff = (dt.datetime.now() - dt.timedelta(hours=1)).isoformat(timespec="seconds")
        for c in self.lib.chats():
            if c.get("n_messages"):
                out.append(self.summary(c))
            elif (c.get("created_at") or "") < cutoff and c["id"] not in self._running:
                self.lib.delete_chat(c["id"])          # started but never used
        return out

    def view(self, chat_id: str, ctx: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """The chat as the page shows it. ``ctx``: its context, when the caller just built it."""
        chat = next(iter(self.lib.chats(chat_id)), None)
        if chat is None:
            raise RuntimeError(t("This chat no longer exists."))
        scope = chat.get("scope") or {}
        if ctx is None:
            ctx = self.context(chat["course_id"], self._scope_days(chat), scope.get("focus", "full"),
                               bool(scope.get("slides")), scope.get("send"))
        course = self.lib.get_course(chat["course_id"])
        messages = [{"seq": m["seq"], "role": m["role"], "text": m["text"], "status": m["status"],
                     "usage": m.get("usage") or {}, "created_at": m["created_at"]} for m in self.lib.chat_messages(chat_id)]
        return {**self.summary(chat), "days": self._day_labels(chat), "focus": scope.get("focus", "full"),
                "slides": bool(scope.get("slides")), "context_tokens": ctx["tokens"], "images": ctx["video_images"],
                "send": chatslides.normalize(scope.get("send")), "files": ctx["files"],
                "course_name": course_display(course) if course else "", "messages": messages,
                "limits": ai.usage_limits()}

    def create(self, course_id: str, day_keys: list[str], focus: str = "full", slides: bool = False,
               model: str = "sonnet", level: str = "", send: Any = None) -> dict[str, Any]:
        """A new chat. ``level``: its reasoning level (effort.py; "" = the one in Settings); ``send``: the course's
        slide files that go along (chatslides)."""
        focus = focus if focus in FOCUS else "full"
        model = model if model in MODELS else "sonnet"
        days = self.ordered_days(course_id, self.all_days(course_id) if focus == "course" else day_keys)
        if not days:
            raise RuntimeError(t("Pick at least one transcribed class day."))
        send = chatslides.normalize(send)
        ctx = self.context(course_id, days, focus, slides, send)
        if ctx["tokens"] > MAX_CONTEXT_TOKENS:
            raise RuntimeError(t("That is too much for one chat (~{k}k tokens, the limit is {max}k). Pick fewer class "
                                 "days or use highlights only.", k=ctx["tokens"] // 1000, max=MAX_CONTEXT_TOKENS // 1000))
        chat_id = uuid.uuid4().hex[:12]
        scope = {"days": days, "focus": focus, "slides": bool(slides and ctx["slides_total"]),
                 "effort": level if level in LEVEL_NAMES else ""}   # "" = Settings' level (never the old model's)
        if send["mode"] != "none":
            scope["send"] = send
        self.lib.create_chat(chat_id, course_id, scope, model)
        # the same material as saved; slides asked for but none found: saved without them, so the view builds it again
        return self.view(chat_id, ctx if not slides or ctx["slides_total"] else None)

    def update(self, chat_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        if self.lib.get_chat(chat_id) is None:
            raise RuntimeError(t("This chat no longer exists."))
        fields: dict[str, Any] = {}
        if "title" in patch:
            fields["title"] = " ".join(str(patch["title"] or "").split())[:120]
        if patch.get("model") in MODELS:
            fields["model"] = patch["model"]          # the next question starts a new process with this model
        scope = dict((self.lib.get_chat(chat_id) or {}).get("scope") or {})
        if patch.get("effort") in LEVEL_NAMES:        # the next question's reasoning level (Claude: a new process)
            fields["scope"] = scope = {**scope, "effort": patch["effort"]}
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
        if fields:
            self.lib.update_chat(chat_id, **fields)
        return self.view(chat_id)

    def delete(self, chat_id: str) -> None:
        self.stop(chat_id)
        self._drop(chat_id)
        self.lib.delete_chat(chat_id)

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
                           scope.get("send"))
        if not ctx["n_days"]:
            raise RuntimeError(t("The class days of this chat are no longer available."))
        session = ai.new_session(ctx["system"], model=chat.get("model") or "sonnet", effort=level)
        session.key = key
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

        def on_text(so_far: str) -> None:
            nonlocal text
            text = so_far
            now = time.monotonic()
            if now - last_emit[0] >= EMIT_EVERY_S:
                last_emit[0] = now
                self._emit(cid, seq, "streaming", so_far)
            if now - last_save[0] >= SAVE_EVERY_S:     # keep the partial answer if the app is closed meanwhile
                last_save[0] = now
                self.lib.update_chat_message(cid, seq, so_far, "streaming")

        try:
            self._emit(cid, seq, "streaming", "")
            session, images = self._session(chat)
            content: list[dict[str, Any]] = []
            if images is not None:                          # a new process: give it what it has not seen
                if images:
                    content.append({"type": "text", "text": "The slide images of these lectures, in order:"})
                    for label, path in images:
                        if stop.is_set():
                            break
                        if isinstance(path, chatslides.PagePicture):      # a course file's page: drawn now
                            path = path.path()
                            if path is None:
                                continue
                        content += [{"type": "text", "text": label}, ai.image_block(path)]
                if history and hasattr(session, "seed"):
                    session.seed(history)               # it sends the conversation with every question itself
                elif history:
                    content.append({"type": "text", "text": replay_text(history)})
            scope = chat.get("scope") or {}
            try:                                            # course files and other lectures that match the question
                found, sources = retrieve(self.lib, chat["course_id"], question,
                                          skip_days=self._scope_days(chat) if scope.get("focus", "full") == "full" else ())
            except Exception:  # noqa: BLE001 - the answer works without it
                log.debug("retrieval failed", exc_info=True)
                found, sources = "", []
            if found:
                content.append({"type": "text", "text": found})
            content.append({"type": "text", "text": question})
            if stop.is_set():
                res = {"text": "", "usage": {}, "stopped": True}
                if images is not None:      # it never got its first message: start afresh next time
                    self._drop(cid)
            else:
                res = session.ask(content, on_text, stop)
            text = res["text"] or text
            state = "stopped" if res["stopped"] else "done"
            usage = {**(res["usage"] or {}), **({"sources": sources} if sources else {})}
            self.lib.update_chat_message(cid, seq, text, state, usage)
            if not session.alive():
                self._drop(cid)
            self._emit(cid, seq, state, text, usage=usage, limits=ai.usage_limits())
        except Exception as exc:  # noqa: BLE001 - reported in the chat
            log.warning("Chat %s failed: %s", cid, exc)
            msg = str(exc) or t("{ai} could not answer.", ai=ai.ai_name())
            self.lib.update_chat_message(cid, seq, text, "error", {"error": msg})
            self._drop(cid)
            self._emit(cid, seq, "error", text, error=msg, limits=ai.usage_limits())
        finally:
            with self._lock:
                self._running.pop(cid, None)
            if self.lib.get_chat(cid) is None:     # deleted while its process was starting
                self._drop(cid)
            lim = ai.usage_limits()
            if lim:
                self.lib.set_meta("claude_limits", json.dumps(lim))

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
        who = ai.PROVIDER_LABELS.get(ai.provider()) or "AI"
        title = v["title"] or "New chat"         # the heading and the file name stay English
        lines = [f"# {title}", "",
                 f"**Course:** {v['course_name']}  ", f"**Class days:** {days}  ",
                 f"**Model:** {ai.model_label(v['model'], v.get('effort', ''))}" + (" · highlights only" if v["focus"] == "highlights" else ""),
                 "", f"*Chat with {who} in IEUStudia, exported {dt.datetime.now():%Y-%m-%d %H:%M}.*", "", "---", ""]
        for m in v["messages"]:
            if m["role"] == "user":
                lines += [f"**You:** {m['text'].strip()}", ""]
            elif m["text"].strip():
                lines += [f"**{who}:**", "", m["text"].strip() + (" *(stopped)*" if m["status"] == "stopped" else ""),
                          "", "---", ""]
        stamp = ((self.lib.get_chat(chat_id) or {}).get("created_at") or "")[:10]
        name = safe_name(f"{stamp} - {title}" if stamp else title, limit=110) + f" [{chat_id[:6]}]"
        path = course_dir(self._s.output_root, course, self.lib.courses(True)) / "AI chats" / f"{name}.md"
        atomic_write(path, dollar_math("\n".join(lines).rstrip()) + "\n")
        return path
