""""Read everything": a chat's chosen slides and files that don't fit in one request to the AI are gone through in
batches before it answers, so that every page is read.

Each batch is one request with as many pages as fit (chatlimits.Limits: tokens, pictures, bytes), as pictures where the
AI takes them, with their text. It writes one set of compact notes per page that serves every question (definitions,
formulas, figures described, worked examples step by step, what the page stresses for exams), so a question about
an exam or a problem never starts another reading. The notes are kept per file and AI (a student's own file: in its
folder, so they go with it), so a follow-up question, a new chat about the same course or an answer tried again never
reads a page twice. A batch whose notes miss pages sends those again once; what still can't be read is told to the
student (notices.py) and to the AI. The answer then gets all the notes (within the room the chat keeps for them:
fit_notes) and the pages that matter most for the question as pictures.
"""

from __future__ import annotations

import dataclasses as dc
import hashlib
import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from . import ai
from .cancel import Cancelled

log = logging.getLogger("studia")

VERSION = 2                   # the reading prompt: notes read before it changed are read again (2: one set for all)
PAGE_CHARS = 6000             # a page's text in a batch (the picture has the rest)
NOTE_CHARS = 1200             # a page's notes as kept (a dense page: about 390 tokens)
NOTE_LABEL_TOKENS = 15        # a page's label above its notes
BATCH_PAGES = 60              # pages per batch: their notes must fit in one answer
LOCAL_BATCH_PAGES = 8         # the offline model writes short answers
BATCH_OVERHEAD = 700          # the reading instructions and the page list, per batch
TOOL_OVERHEAD = {"codex": 18_500}   # what the AI's own tool adds to each request (the Codex CLI's instructions:
                                    # 19,337 input tokens for a batch of three tiny pages, measured Nov 2026)
RELEVANT_PAGES = 6            # the pages that matter most for a question, sent with it as pictures
MANY_FILES = 4                # "Read everything" is on by default for this many chosen files and more
FOCUS = ("These notes serve every later question about the page (understanding it, preparing for an exam, solving "
         "problems), so: write each worked example's method step by step with its given values and result, give every "
         "formula needed to solve such problems, and mark what the page stresses (boxed results, 'important', 'exam', "
         "'quiz') with ⭐. Keep each page's notes under about 1,000 characters.")
READ_SYSTEM = (
    "You read course material for a university engineering student, page by page, so that the app can answer the "
    "student's questions from your notes without the pages. You get some pages, each introduced by its label in square "
    "brackets, as text extracted automatically and, where there is one, as a picture: trust the picture for formulas, "
    "figures, tables and handwriting.\n\n"
    "Read EVERY page. For each page, in the order given, write a line '### ' followed by the page's label exactly as "
    "given (with its brackets), then compact notes: every definition, formula (in LaTeX, $...$), value, step of a worked "
    "example, and what each figure, circuit, graph or table shows. Keep what is specific to the page; skip decoration and "
    "repeated headers. A page with nothing of substance: write '(nothing of substance)'. {focus}\n\n"
    "Write nothing else: no introduction and no summary at the end."
)


@dc.dataclass
class Page:
    """One page of a chat's material (a PDF or PowerPoint page, a photo, a slide from the video, a piece of a text file)."""
    label: str                # "[Week04_Student.pdf p.3]", as the chat's instructions and the AI cite it
    group: str                # "course", "upload", "video"
    file: str                 # the file's id (a material's, an upload's; "video")
    name: str                 # the file's name
    page: int                 # 1, 2, … (0: a photo or a video slide)
    text: str = ""            # its text as extracted
    picture: Any = None       # what can be sent as its picture (chatslides.PagePicture, chatfiles.UploadPicture, a Path)
    tokens: int = 0           # its picture's tokens as sent
    bytes: int = 0            # its picture's weight in a request (base64)
    priority: int = 2         # 0: a photo (it has no text), 1: mostly a figure, 2: its text says much of it
    source: str = ""          # its file on this PC (the notes are kept per file)
    notes_dir: str = ""       # where its notes are kept ("" = the app's cache)

    @property
    def key(self) -> str:
        return f"{self.group}:{self.file}:{self.page}:{self.label}"


def default_on(chosen: Optional[bool], focus: str, n_files: int, provider: str) -> bool:
    """Whether a chat reads everything: as the student chose; else on for the whole course or many files (off for the
    offline model, which reads slowly: the student turns it on)."""
    if chosen is not None:
        return bool(chosen)
    return provider != "local" and (focus == "course" or n_files >= MANY_FILES)


def prompt_note(on: bool) -> str:
    """The line the chat's instructions get when pages are read for it."""
    return ("The pages you don't get as pictures (or whose text was too long to include) are read for you in parts; "
            "their notes come with a question under NOTES ON THE PAGES YOU DON'T SEE IN FULL, and the pages that "
            "matter most for it as pictures. Use them as the pages themselves.") if on else ""


def page_text_tokens(p: Page, per: float) -> int:
    return ai.text_tokens(p.text[:PAGE_CHARS], per) + 12


def note_max(per: float) -> int:
    """The most a page's notes take, with its label (parse keeps ``NOTE_CHARS`` of them)."""
    return int(NOTE_CHARS / (per or ai.chars_per_token())) + NOTE_LABEL_TOKENS


def note_cost(p: Page, per: float) -> int:
    """A page's notes, about (a page with little on it gets short notes; a figure is described; a full page up to
    ``note_max``: the room kept for them is what they can really take)."""
    return min(note_max(per), NOTE_LABEL_TOKENS + 30 + ai.text_tokens(p.text, per) // 2
               + (200 if p.picture is not None else 0))


def notes_cost(pages: Iterable[Page], per: float) -> int:
    return sum(note_cost(p, per) for p in pages)


def batches(pages: list[Page], lim: Any, pictures: bool, per: float = 0.0) -> list[list[Page]]:
    """The pages in batches that each fit in one request (in order; every page in exactly one)."""
    per = per or ai.chars_per_token(lim.provider)
    most = LOCAL_BATCH_PAGES if lim.provider == "local" else BATCH_PAGES
    out: list[list[Page]] = []
    cur: list[Page] = []
    tok = pics = size = 0
    for p in pages:
        pic = pictures and p.picture is not None
        cost = page_text_tokens(p, per) + (p.tokens if pic else 0) + note_max(per)
        if cur and (tok + cost > lim.batch_tokens or len(cur) >= most or (pic and pics + 1 > lim.batch_pictures)
                    or (pic and lim.request_bytes and size + p.bytes > lim.request_bytes)):
            out.append(cur)
            cur, tok, pics, size = [], 0, 0, 0
        cur.append(p)
        tok += cost
        if pic:
            pics += 1
            size += p.bytes
    if cur:
        out.append(cur)
    return out


# ---------------------------------------------------------------------------------------------------- the notes kept
def _fingerprint(source: str) -> str:
    try:
        st = os.stat(source)
        return f"{os.path.abspath(source)}|{st.st_size}|{st.st_mtime_ns}"
    except OSError:
        return source


def _store(p: Page, lim: Any) -> Path:
    from .paths import app_dir

    key = hashlib.sha1(f"{VERSION}|{lim.key}|{_fingerprint(p.source or p.key)}".encode("utf-8")).hexdigest()[:24]
    base = Path(p.notes_dir) if p.notes_dir else app_dir() / "cache" / "reads"
    return base / f"{key}.json"


_lock = threading.Lock()


def _load(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def cached(pages: Iterable[Page], lim: Any) -> dict[str, str]:
    """The notes already read of ``pages`` (by page key) by this AI."""
    out: dict[str, str] = {}
    files: dict[Path, dict[str, str]] = {}
    for p in pages:
        path = _store(p, lim)
        if path not in files:
            files[path] = _load(path)
        if p.label in files[path]:
            out[p.key] = files[path][p.label]
    return out


def _keep(found: list[tuple[Page, str]], lim: Any) -> None:
    by: dict[Path, dict[str, str]] = {}
    for p, note in found:
        by.setdefault(_store(p, lim), {})[p.label] = note
    with _lock:
        for path, new in by.items():
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                data = {**_load(path), **new}
                tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
                tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                os.replace(tmp, path)
            except OSError:
                log.warning("The notes of %s could not be kept", path, exc_info=True)


# ---------------------------------------------------------------------------------------------------- reading
_HEAD = re.compile(r"^\s*#{1,4}\s*(\[[^\n]*?\])[ \t]*:?[ \t]*$", re.M)


def _canon(label: str) -> str:
    return " ".join(label.strip().strip("[]").lower().split())


def parse(text: str, labels: list[str]) -> dict[str, str]:
    """The notes of each page in an answer: {label: notes}, only labels that were asked for."""
    want = {_canon(x): x for x in labels}
    out: dict[str, str] = {}
    heads = list(_HEAD.finditer(text or ""))
    for i, m in enumerate(heads):
        label = want.get(_canon(m.group(1)))
        body = (text[m.end():heads[i + 1].start() if i + 1 < len(heads) else len(text)]).strip()
        if label and body:
            note = body if len(body) <= NOTE_CHARS else body[:NOTE_CHARS].rsplit("\n", 1)[0] + " …"
            out[label] = (out[label] + "\n" + note) if label in out else note
    return out


def _content(batch: list[Page], pictures: bool, question: str) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = []
    for p in batch:
        text = p.text.strip()[:PAGE_CHARS]
        content.append({"type": "text", "text": f"{p.label}\n{text}" if text else f"{p.label} (no text: see the picture)"
                        if pictures and p.picture is not None else f"{p.label} (no text could be read)"})
        if pictures and p.picture is not None:
            path = p.picture.path() if hasattr(p.picture, "path") else p.picture
            if path is not None:
                content.append(ai.image_block(Path(path)))
            else:
                content[-1]["text"] += " (its picture could not be made)"
    ask = (f"The student's question, for context only (the notes are kept for every later question, so note "
           f"everything important, not only what answers it): {question.strip()[:600]}\n\n" if question.strip() else "")
    content.append({"type": "text", "text": f"{ask}Pages in this part, each to be noted under its label: "
                                            + ", ".join(p.label for p in batch)})
    return content


Request = Callable[[str, list[dict[str, Any]], Optional[threading.Event]], dict[str, Any]]


def default_request(lim: Any, model: str) -> Request:
    """One reading request to the chosen AI: light reasoning, no extended thinking (it notes, it doesn't solve)."""
    effort = "off" if lim.provider == "local" else "low"

    def run(system: str, content: list[dict[str, Any]], cancel: Optional[threading.Event]) -> dict[str, Any]:
        return ai.one_shot(system, content, model or "sonnet", effort, cancel, None, 900, think=False)
    return run


def read(pages: list[Page], question: str, lim: Any, request: Request,
         cancel: Optional[threading.Event] = None,
         progress: Optional[Callable[[dict[str, Any]], None]] = None) -> dict[str, Any]:
    """Every page's notes (``notes``: page key → notes), reading in batches what isn't kept yet. ``progress`` gets
    {"done", "total" (batches), "pages", "read" (pages with notes)} before each batch and at the end. Raises Cancelled
    when ``cancel`` is set (what was read is kept: the next run goes on from there) and the AI's error when a batch
    fails (the same). ``model_refused``: the model Codex turned down in a batch (it read with its default instead)."""
    pictures = lim.pictures > 0
    notes = cached(pages, lim)
    before = len(notes)
    todo = [p for p in pages if p.key not in notes]
    plan = batches(todo, lim, pictures)
    usage = {"input_tokens": 0, "output_tokens": 0}
    state = {"done": 0, "total": len(plan), "pages": len(pages), "read": len(notes), "cached": before}
    system = READ_SYSTEM.format(focus=FOCUS)
    unread: list[Page] = []

    def ask(batch: list[Page]) -> list[Page]:
        """One request; the pages it left out."""
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        r = request(system, _content(batch, pictures, question), cancel)      # (Stop meanwhile: its notes are kept)
        for k in usage:
            usage[k] += int((r.get("usage") or {}).get(k) or 0)
        if r.get("model_refused"):                   # Codex turned the chosen model down (said to the student once)
            state["model_refused"] = r["model_refused"]
        got = parse(r.get("text") or "", [p.label for p in batch])
        found = [(p, got[p.label]) for p in batch if p.label in got]
        if found:
            _keep(found, lim)
            for p, note in found:
                notes[p.key] = note
        return [p for p in batch if p.label not in got]

    for batch in plan:
        if progress:
            progress(dict(state))
        missing = ask(batch)
        if missing:                                  # left out: asked once more, on their own
            log.info("Read everything: %d page(s) left out of a batch, asked again", len(missing))
            unread += ask(missing)
        state["done"] += 1
        state["read"] = len(notes)
    state.update(read=len(notes), unread=[p.label for p in unread], usage=usage)
    if progress:
        progress(dict(state))
    return {**state, "notes": notes}


# ---------------------------------------------------------------------------------------------------- the answer
def notes_text(pages: list[Page], notes: dict[str, str]) -> str:
    """The notes for the AI that answers, in the material's order, by file."""
    out: list[str] = []
    name = None
    for p in pages:
        note = notes.get(p.key)
        if not note:
            continue
        if p.name != name:
            name = p.name
            out.append(f"## {name}")
        out.append(f"{p.label}\n{note}")
    if not out:
        return ""
    head = ("=== NOTES ON THE PAGES YOU DON'T SEE IN FULL ===\n(Every page below was read for you, as a picture where "
            "it has one, and noted under its label: use these notes like the pages themselves and cite them by the "
            "label.)")
    return head + "\n\n" + "\n\n".join(out)


def _short(text: str, cap: int) -> str:
    if len(text) <= cap:
        return text
    cut = text[:max(0, cap)]
    return (cut.rsplit(" ", 1)[0] if " " in cut[cap // 2:] else cut).rstrip() + " …"


def fit_notes(pages: list[Page], notes: dict[str, str], max_tokens: int,
              per: float = 0.0) -> tuple[dict[str, str], bool]:
    """The notes within ``max_tokens`` (as notes_text writes them): each page's shortened to the same longest length
    that fits, when they don't all fit whole (dense notes, or a conversation with little room left). Returns the notes
    and whether they were shortened."""
    per = per or ai.chars_per_token()

    def size(ns: dict[str, str]) -> int:
        return ai.text_tokens(notes_text(pages, ns), per)

    if size(notes) <= max_tokens:
        return notes, False
    keys = [p.key for p in pages if notes.get(p.key)]
    lo, hi = 0, max((len(notes[k]) for k in keys), default=0)
    while lo < hi:                                   # the longest notes per page that fit
        mid = (lo + hi + 1) // 2
        if size({**notes, **{k: _short(notes[k], mid) for k in keys}}) <= max_tokens:
            lo = mid
        else:
            hi = mid - 1
    return {**notes, **{k: _short(notes[k], lo) for k in keys}}, True


LOCAL_CHARS = 3000            # the offline model: the notes that match a question, at most this much with it


def pick(pages: list[Page], notes: dict[str, str], question: str, max_chars: int = LOCAL_CHARS) -> str:
    """The offline model's share of the notes with one question: those that match it best, in the material's order."""
    from .local_llm import _keywords

    want = _keywords(question)
    rows = [(len(want & _keywords(notes[p.key])), i, f"{p.label}\n{notes[p.key]}") for i, p in enumerate(pages)
            if notes.get(p.key)]
    if not rows:
        return ""
    ranked = sorted(rows, key=lambda r: (-r[0], r[1])) if any(r[0] for r in rows) else rows
    out, used = [], 0
    for score, i, text in ranked:
        if used + len(text) <= max_chars:
            out.append((i, text))
            used += len(text) + 2
    return ("=== NOTES ON THE COURSE FILES (read for you; the ones that match the question) ===\n\n"
            + "\n\n".join(text for _, text in sorted(out)))


def relevant(pages: list[Page], notes: dict[str, str], question: str, most: int) -> list[Page]:
    """The pages (with a picture) whose notes and text match the question best, at most ``most``, in order."""
    from .local_llm import _keywords

    want = _keywords(question)
    if most <= 0 or not want:
        return []
    scored = [(len(want & _keywords(f"{notes.get(p.key, '')} {p.text}")), i, p) for i, p in enumerate(pages)
              if p.picture is not None]
    best = sorted((x for x in scored if x[0] > 0), key=lambda x: (-x[0], x[1]))[:most]
    return [p for _, _, p in sorted(best, key=lambda x: x[1])]


def estimate(pages: list[Page], lim: Any, per: float = 0.0) -> dict[str, Any]:
    """What reading ``pages`` takes: requests, tokens read and written, the pages already read (kept)."""
    per = per or ai.chars_per_token(lim.provider)
    known = cached(pages, lim) if pages else {}
    todo = [p for p in pages if p.key not in known]
    plan = batches(todo, lim, lim.pictures > 0, per)
    pictures = lim.pictures > 0
    tokens = sum(page_text_tokens(p, per) + (p.tokens if pictures and p.picture is not None else 0) for p in todo)
    return {"pages": len(pages), "cached": len(known), "batches": len(plan),
            "tokens": tokens + len(plan) * (BATCH_OVERHEAD + TOOL_OVERHEAD.get(lim.provider, 0)),
            "output": notes_cost(todo, per),
            "pictures": sum(1 for p in todo if pictures and p.picture is not None)}


def ranges(numbers: Iterable[int]) -> str:
    """1, 2, 3, 5, 7, 8 → "p.1–3, 5, 7–8"."""
    ns = sorted(set(n for n in numbers if n))
    if not ns:
        return ""
    out, start, prev = [], ns[0], ns[0]
    for n in ns[1:] + [0]:
        if n == prev + 1:
            prev = n
            continue
        out.append(f"{start}–{prev}" if prev > start else f"{start}")
        start = prev = n
    return "p." + ", ".join(out)
