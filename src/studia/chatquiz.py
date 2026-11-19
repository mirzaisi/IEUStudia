"""Quiz me in a chat: "/quiz [n] [topics]" typed in Ask AI (or the "Quiz me on this" chip under an answer) turns the
chat into a scored practice quiz, saved to the course's Practice tab like the quizzes written there.

The chat shows what the student typed ("/quiz 5"); the AI gets an instruction made here instead: a practice quiz's
JSON, rules and languages (quizzes.write_prompt's own, so chat quizzes follow the same language rule and shape), the
course's learning outcomes and what the lecturer stressed in the chat's lectures, the references of its lectures
and files (L1…, F1…, as quizzes.gather numbers them), and one ```ieustudia-quiz {json}``` block to answer with. The
material itself is the chat's own: nothing is sent again, so the turn is cheap (Claude reads the lectures from its
cache).

When the answer is done, its block is read and checked as a written quiz's is (quizzes.read_answer, clean_quiz: no
AI): each question keeps a source the app can open, the one named or else one found by its words. It is saved as a
finished quiz (a plan of kind "quiz" whose scope says source "chat"), which the page shows as a "Play quiz" card under
the answer once it has finished. A block that can't be read leaves the answer as it was written."""

from __future__ import annotations

import datetime as dt
import logging
import re
import uuid
from typing import Any, Iterable, Optional

from .i18n import t, tn

log = logging.getLogger("studia")

COMMAND = re.compile(r"^\s*/quiz\b[ \t]*(\d{1,2})?[ \t]*(.*)$", re.I | re.S)
DEFAULT_N = 5
MOST = 20
FENCE = re.compile(r"```[ \t]*ieustudia-quiz[ \t]*\n?(.*?)(?:```|$)", re.S | re.I)
_PART_ONLY = re.compile(r"^\s*\[?\s*(?:P|part\s*|bölüm\s*)(\d{1,2})\b", re.I)


def command(text: str) -> Optional[dict[str, Any]]:
    """The quiz a message asks for: {"n" (questions, 1–20; 5 when none is said), "topics" (any words after it)}, or
    None for any other message."""
    m = COMMAND.match(text or "")
    if not m:
        return None
    n = int(m.group(1)) if m.group(1) else DEFAULT_N
    return {"n": max(1, min(MOST, n)), "topics": " ".join(m.group(2).split())[:300]}


def chat_weeks(d: dict[str, Any], day_keys: Iterable[str], file_ids: Iterable[str]) -> list[int]:
    """The course weeks the chat's lectures and files are in."""
    days, files = set(day_keys), set(file_ids)
    return [w["no"] for w in d.get("weeks") or []
            if any(x["day_key"] in days for x in w.get("days") or [])
            or any(m["id"] in files for u in w.get("units") or [] for m in u.get("materials") or [])]


def facts(lib: Any, settings: Any, course_id: str, day_keys: list[str], file_ids: list[str]) -> dict[str, Any]:
    """The chat's lectures and files as a quiz's material (quizzes.gather: their references, the language, the
    learning outcomes, the stressed moments): what the questions' sources are checked against."""
    from . import quizzes
    from .plans import Scope
    from .views import course_detail

    d = course_detail(lib, settings, course_id) or {}
    scope = Scope.from_json({"kind": "quiz", "weeks": chat_weeks(d, day_keys, file_ids), "days": list(day_keys),
                             "files": list(file_ids), "picked": True})
    out = quizzes.gather(lib, settings, course_id, scope)
    # the AI reads the chat's own material, not this text: nothing of it was cut for a quiz's room
    out.update(pack="", chars=0, cut=[], left_out=[], files_cut=[], one_lecture=False)
    return out


def references(f: dict[str, Any], chat_days: list[str], labels: Optional[dict[str, str]] = None) -> str:
    """The material's references (L1 = a lecture, F1 = a file), with the chat's own name for each where it differs."""
    from .doclang import date_text

    lines = []
    for x in f.get("lectures") or []:
        k = chat_days.index(x["day_key"]) + 1 if x["day_key"] in chat_days else 0
        also = f" (L{k} in this chat)" if len(chat_days) > 1 and k and f"L{k}" != x["ref"] else ""
        week = f" (week {x['week']})" if x.get("week") else ""
        lines.append(f"{x['ref']} = the lecture of {date_text(x['date'])}{week}{also}")
    for x in f.get("files") or []:
        name = (labels or {}).get(x["id"]) or x["name"]
        lines.append(f"{x['ref']} = the file {name} ({x['pages']} pages, cite its pages as \"{x['ref']} p.3\")")
    return "\n".join(lines) or "(none)"


def prompt(f: dict[str, Any], n: int, topics: str = "", follow_up: bool = False, chat_days: Optional[list[str]] = None,
           labels: Optional[dict[str, str]] = None) -> str:
    """What the AI gets for "/quiz": a practice quiz as quizzes.write_prompt asks for one (its shape, rules, languages,
    learning outcomes and stressed moments), on the chat's material, answered in one ```ieustudia-quiz block."""
    from . import quizzes
    from .outputs.prompts import quiz_languages_again
    from .plans import Scope

    kinds = quizzes.mix(n, "mixed", bool(f.get("quant")))
    body = quizzes.write_prompt(Scope(kind="quiz", topics=topics), {**f, "pack": "", "one_lecture": False}, n, kinds)
    body = body.split("\n\n=== COURSE MATERIAL ===")[0]          # the material is the chat's own, sent already
    body = body.replace("1. The material below", "1. The lectures and files of this chat", 1)
    what = ("Quiz the student on what this conversation covered, most of all your last answer; take the details, "
            "the examples and the sources from the lectures and files of this chat." if follow_up else
            "Quiz the student on the lectures and files of this chat.")
    again = quiz_languages_again(f.get("language") or "", f.get("taught_in") or "")
    return (f"(From the IEUStudia app, not typed by the student: they asked for a practice quiz they can take and "
            f"score in the app.) {what}\n\n{body}\n\n=== REFERENCES (for \"source\") ===\n"
            f"{references(f, chat_days or [], labels)}{again}\n\n=== HOW TO ANSWER ===\nOne short sentence for the "
            "student, then the JSON object in one block that starts with ```ieustudia-quiz on its own line and ends "
            "with ```. Nothing after the block. The app turns it into the quiz.")


def block(text: str) -> str:
    """The quiz in an answer: its ```ieustudia-quiz block (a block cut short too), else the whole answer (an AI that
    wrote it as ```json, or as Markdown)."""
    m = FENCE.search(text or "")
    return m.group(1).strip() if m else (text or "")


def _one_lecture(raw: dict[str, Any], f: dict[str, Any]) -> None:
    """A chat about one lecture cites "[P2 14:05]" (its part and time, no L): such a source is that lecture's."""
    if len(f.get("lectures") or []) != 1:
        return
    ref = f["lectures"][0]["ref"]
    for q in raw.get("questions") or []:
        if not isinstance(q, dict):
            continue
        for k in ("source", "sources", "ref", "reference", "kaynak"):
            v = q.get(k)
            m = _PART_ONLY.match(v) if isinstance(v, str) and not re.search(r"\b[FL]\d{1,3}\b", v, re.I) else None
            if m:
                q[k] = f"{ref} part {m.group(1)} {v[m.end():].strip(' ]')}"


def save(lib: Any, course_id: str, f: dict[str, Any], text: str, n: int, chat_id: str = "",
         files: Optional[list[str]] = None) -> Optional[dict[str, Any]]:
    """The answer's quiz, checked and saved as a finished quiz of the course: {"id", "title", "questions", "found",
    "no_source"}; None when it has no question the app can use (the answer then stays as it was written)."""
    from . import quizzes
    from .doclang import span_title
    from .plans import Scope

    raw = quizzes.read_answer(block(text))
    if not raw.get("questions") or not quizzes.answered(raw):
        return None
    _one_lecture(raw, f)
    pid = uuid.uuid4().hex[:12]
    quiz = quizzes.clean_quiz(raw, f, quizzes.source_units(lib, course_id, f), seed=pid)
    if not quiz["questions"]:
        return None
    title = quiz["title"] or span_title(f)
    cov = quizzes.coverage(quiz, f)
    scope = {**Scope(kind="quiz", weeks=[w["no"] for w in f.get("weeks") or []],
                     days=[x["day_key"] for x in f.get("lectures") or []], files=list(files or []), picked=True,
                     questions=n).to_json(), "source": "chat", "chat_id": chat_id}
    result = {"kind": "quiz", "title": title, "made": dt.date.today().isoformat(), "language": f.get("language") or "",
              "questions": quiz["questions"], "topics": quiz["topics"], "coverage": cov, "course": f.get("course") or {},
              "asked": n, "source": "chat", "chat_id": chat_id}
    lib.create_plan(pid, course_id, scope, title)
    lib.update_plan(pid, result=result, title=title, status="done", stage="done", progress=1.0,
                    message=tn(len(quiz["questions"]), "{n} question", "{n} questions"))
    return {"id": pid, "title": title, "questions": len(quiz["questions"]), "found": cov["found"],
            "no_source": cov["no_source"]}


def unread() -> str:
    """What the chat says under an answer whose quiz could not be read."""
    return t("The quiz in this answer could not be read, so it wasn't saved. Type /quiz to try again.")
