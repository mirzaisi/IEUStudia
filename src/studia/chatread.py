""""Read everything": a chat's chosen slides and files that don't fit in one request to the AI are gone through in
parts before it answers, so that every page is read (and, for the whole course, the lectures whose transcripts don't
fit whole: chat.py).

Each part is one request with as many pages as fit (chatlimits.Limits: tokens, pictures, bytes; at most
``BATCH_PAGES``), as pictures where the AI takes them, with their text. It writes one set of compact notes per page
that serves every question (definitions, formulas, figures described, worked examples step by step, what the page
stresses for exams), so a question about an exam or a problem never starts another reading.

The notes are kept as they arrive, page by page while the answer streams (not only when a part is done: with
Continue with ChatGPT; Claude Code and the Codex CLI give a part's answer in one piece at its end, so for them a part
is kept when it is done), on disk, per file by its content (a student's own file: in its folder, so they go with it),
for every AI on the student's plan. So a dropped connection, Stop, closing the app, a follow-up question, Try again or
a new chat about the same course reads only the pages that have no notes yet. A connection that drops while a part is read is asked again by itself
after a short wait (said on screen), with the pages still missing; a part that keeps failing without any new notes is
split in two. Two parts are read at once (the AI writes notes at about the same pace in each: measured on ChatGPT,
Nov 2026, ~48 tokens a second, ~3 s a page), and the progress says live which pages, for how long, how many have come
and about how long the rest takes. A part whose answer misses pages asks for those again once; what still can't be
read is told to the student (notices.py) and to the AI. The answer then gets all the notes (within the room the chat
keeps for them: fit_notes) and the pages that matter most for the question as pictures.
"""

from __future__ import annotations

import dataclasses as dc
import hashlib
import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from . import ai, aierrors
from .cancel import Cancelled

log = logging.getLogger("studia")

VERSION = 2                   # the reading prompt: notes read before it changed are read again (2: one set for all)
STORE = 3                     # how the notes are kept (3: by the file's content, for every AI on the student's plan;
                              # 2: by the AI and the file's path, size and time: still read, and moved over)
PAGE_CHARS = 6000             # a page's text in a part (the picture has the rest)
NOTE_CHARS = 1200             # a page's notes as kept (a dense page: about 390 tokens)
NOTE_LABEL_TOKENS = 15        # a page's label above its notes
BATCH_PAGES = 30              # pages per part. Measured on ChatGPT (Nov 2026, the owner's EEE 215 slides): 60 pages
                              # took 200 s (3.6 MB sent, the first notes after 13 s), 30 took 91 s, 15 took 57 s, the
                              # answer streaming steadily (never quiet for more than 5 s). The stream that dropped did
                              # so 2 minutes into a 3-minute part: shorter parts finish more often between such drops,
                              # and they keep two readers busy evenly
BATCH_PAGES_BY = {"codex": 60}      # the Codex CLI adds 18,500 tokens of its own to every request: fewer, larger parts
LOCAL_BATCH_PAGES = 8         # the offline model writes short answers
BATCH_OVERHEAD = 700          # the reading instructions and the page list, per part
TOOL_OVERHEAD = {"codex": 18_500}   # what the AI's own tool adds to each request (the Codex CLI's instructions:
                                    # 19,337 input tokens for a batch of three tiny pages, measured Nov 2026)
READERS = 2                   # parts read at once (the study packs and cheat sheets also ask two at once)
SERIAL = ("local", "codex")   # one at a time: the offline model is one process; Codex CLIs share one sign-in file
DROP_WAITS = (3.0, 10.0, 30.0)  # the waits before asking a part again after its connection dropped (said on screen)
TRIES = 4                     # tries in a row without a new page's notes before a dropped part gives up (with the
                              # notes kept); a failure on the service's side: 2 (it was asked again inside already)
MOST_TRIES = 8                # tries of one part at most, however much each one brought
KEEP_EVERY_S = 2.0            # notes that came while a part streams are written to disk at most this often
PROGRESS_EVERY_S = 1.0        # the page hears how a part goes at most this often (and at once when it starts or ends)
SECONDS_PER_PAGE = 3.2        # a reader's pace until the real one is known (ChatGPT: 3.0-3.8 s a page, measured)
FIRST_NOTES_S = 12.0          # and the wait for a part's first notes (5-13 s, measured)
RELEVANT_PAGES = 6            # the pages that matter most for a question, sent with it as pictures
VIDEO_SLOT = "slide"          # a slide from the video: its own picture file, one set of notes in it
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
LECTURE_NOTE = (
    "Some pieces are from a lecture's transcript (automatic speech recognition, so names and symbols may be misheard: "
    "fix the obvious ones; [TR] marks Turkish). Each line starts with its time [mm:ss] in that part of the lecture, and "
    "⭐ marks what the lecturer stressed. Note them under their label the same way, in order: what is taught, every "
    "definition, formula and example, each point with its [mm:ss]; quote word for word, with its [mm:ss] and ⭐, what "
    "the lecturer says about exams, quizzes, homework, deadlines and what is important. Up to about 1,200 characters "
    "a piece."
)


@dc.dataclass
class Page:
    """One page of a chat's material (a PDF or PowerPoint page, a photo, a slide from the video, a piece of a text file,
    a piece of a lecture's transcript)."""
    label: str                # "[Week04_Student.pdf p.3]", "[L2 P1 00:00–09:40]", as the chat's instructions and the AI
                              # cite it
    group: str                # "course", "upload", "video", "lecture"
    file: str                 # the file's id (a material's, an upload's; "video"; a lecture's day key)
    name: str                 # the file's name (a lecture: "L2 · Tue 30 Sep")
    page: int                 # 1, 2, … (0: a photo or a video slide)
    text: str = ""            # its text as extracted
    picture: Any = None       # what can be sent as its picture (chatslides.PagePicture, chatfiles.UploadPicture, a Path)
    tokens: int = 0           # its picture's tokens as sent
    bytes: int = 0            # its picture's weight in a request (base64)
    priority: int = 2         # 0: a photo (it has no text), 1: mostly a figure, 2: its text says much of it
    source: str = ""          # its file on this PC (the notes are kept per file, by its content)
    notes_dir: str = ""       # where its notes are kept ("" = the app's cache)

    @property
    def key(self) -> str:
        return f"{self.group}:{self.file}:{self.page}:{self.label}"

    @property
    def slot(self) -> str:
        """Its place among its file's notes, whatever its label says: its page; for a piece of a transcript its text;
        a slide from the video is a picture file of its own, and its number is only its place among one chat's slides
        ("[L4 Part 3, slide 8]" is page 163 in one chat and 134 in another): the one slot of its file."""
        if self.group == "lecture":
            return "t" + hashlib.sha1(self.text.encode("utf-8", "ignore")).hexdigest()[:16]
        if self.group == "video":
            return VIDEO_SLOT
        return f"p{self.page}"


def default_on(chosen: Optional[bool], focus: str, n_files: int, provider: str) -> bool:
    """Whether a chat reads everything: as the student chose; else on for the whole course or many files (off for the
    offline model, which reads slowly: the student turns it on)."""
    if chosen is not None:
        return bool(chosen)
    return provider != "local" and (focus == "course" or n_files >= MANY_FILES)


def prompt_note(on: bool, lectures: bool = False) -> str:
    """The line the chat's instructions get when pages are read for it (``lectures``: and lectures that don't fit
    whole)."""
    if not on:
        return ""
    return ("The pages you don't get as pictures (or whose text was too long to include)"
            + (", and the lectures you don't get whole," if lectures else "")
            + " are read for you in parts; their notes come with a question under NOTES ON THE PAGES YOU DON'T SEE IN "
              "FULL, and the pages that matter most for it as pictures. Use them as the pages themselves."
            + (" Cite a lecture's moments from its notes as [L2 P1 05:10], with the times the notes give." if lectures
               else ""))


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


def part_pages(provider: str) -> int:
    """The most pages one part of the reading takes with this AI."""
    return LOCAL_BATCH_PAGES if provider == "local" else BATCH_PAGES_BY.get(provider, BATCH_PAGES)


def batches(pages: list[Page], lim: Any, pictures: bool, per: float = 0.0) -> list[list[Page]]:
    """The pages in parts that each fit in one request (in order; every page in exactly one)."""
    per = per or ai.chars_per_token(lim.provider)
    most = part_pages(lim.provider)
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
def family(lim: Any) -> str:
    """Whose notes serve whom: every AI on the student's plan reads the pages as pictures and text alike, so their
    notes serve each other (a chat that changes from Sonnet to Opus, or from ChatGPT to Claude, reads nothing again);
    the offline model reads the text only, in short parts: its own."""
    return "local" if lim.provider == "local" else "plan"


_ids: dict[str, tuple[int, int, str]] = {}           # a file's path -> (size, time, its content's hash)
_ids_lock = threading.Lock()


def content_id(source: str) -> str:
    """A file's content as one hash, so its notes stay its own wherever it moves (a new IEUStudia folder, a chat's
    files moving into the chat) and whenever it is downloaded again unchanged; a changed file is read again."""
    try:
        st = os.stat(source)
    except OSError:
        return "path:" + os.path.abspath(source)
    with _ids_lock:
        hit = _ids.get(source)
    if hit and hit[:2] == (st.st_size, st.st_mtime_ns):
        return hit[2]
    h = hashlib.sha1()
    try:
        with open(source, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return "path:" + os.path.abspath(source)
    with _ids_lock:
        _ids[source] = (st.st_size, st.st_mtime_ns, h.hexdigest())
    return h.hexdigest()


def _base(p: Page) -> Path:
    from .paths import app_dir

    return Path(p.notes_dir) if p.notes_dir else app_dir() / "cache" / "reads"


def _store(p: Page, lim: Any) -> Path:
    """Where a page's notes are kept: with its file's other pages, by the file's content."""
    what = content_id(p.source) if p.source else "key:" + p.key
    key = hashlib.sha1(f"{STORE}|{VERSION}|{family(lim)}|{what}".encode("utf-8")).hexdigest()[:24]
    return _base(p) / f"{key}.json"


def _fingerprint(source: str) -> str:
    try:
        st = os.stat(source)
        return f"{os.path.abspath(source)}|{st.st_size}|{st.st_mtime_ns}"
    except OSError:
        return source


LEGACY_KEYS = ("chatgpt", "codex", "claude:sonnet", "claude:opus", "claude:haiku")


def _legacy(p: Page, lim: Any) -> list[Path]:
    """Where IEUStudia kept a page's notes before 3.3 (by the AI, and the file's path, size and time; by label)."""
    if p.group == "lecture":
        return []
    keys = [lim.key] + ([k for k in LEGACY_KEYS if k != lim.key] if family(lim) == "plan" else [])
    stamp = _fingerprint(p.source or p.key)
    return [_base(p) / (hashlib.sha1(f"{VERSION}|{k}|{stamp}".encode("utf-8")).hexdigest()[:24] + ".json")
            for k in keys]


_lock = threading.Lock()


def _load(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def cached(pages: Iterable[Page], lim: Any, move: bool = False) -> dict[str, str]:
    """The notes already read of ``pages`` (by page key). ``move``: notes found where an earlier version kept them
    are kept the new way too (a reading does that; the chat's sizing only looks)."""
    out: dict[str, str] = {}
    files: dict[Path, dict[str, str]] = {}
    found: list[tuple[Page, str]] = []

    def load(path: Path) -> dict[str, str]:
        if path not in files:
            files[path] = _load(path)
        return files[path]

    for p in pages:
        kept = load(_store(p, lim))
        note = kept.get(p.slot)
        if note is None and p.group == "video" and len(kept) == 1:     # (kept under its place in one chat before)
            note = next(iter(kept.values()))
            found.append((p, note))
        if note is None:
            note = next((old[p.label] for old in map(load, _legacy(p, lim)) if p.label in old), None)
            if note is not None:
                found.append((p, note))
        if note is not None:
            out[p.key] = note
    if move and found:
        _keep(found, lim)
    return out


def _keep(found: list[tuple[Page, str]], lim: Any) -> None:
    by: dict[Path, dict[str, str]] = {}
    for p, note in found:
        by.setdefault(_store(p, lim), {})[p.slot] = note
    with _lock:
        for path, new in by.items():
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                data = {**_load(path), **new}
                tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
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


_LABEL_COMING = re.compile(r"\s*#{1,4}\s*\[")


def complete(text: str, labels: list[str]) -> str:
    """The part of an answer still streaming whose pages are whole: everything before the last page's label asked for,
    or before a label still coming at its very end (that page's notes may still be coming, or were cut off by a
    dropped connection)."""
    want = {_canon(x) for x in labels}
    cut = None
    for m in _HEAD.finditer(text or ""):
        if _canon(m.group(1)) in want:
            cut = m.start()
    tail = (text or "").rfind("\n") + 1
    if tail and _LABEL_COMING.match(text, tail) and (cut is None or tail > cut):
        cut = tail
    return text[:cut] if cut is not None else ""


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


Request = Callable[..., dict[str, Any]]
"""One reading request: ``request(system, content, cancel, on_text, on_wait)`` → {"text", "usage"} (ai.one_shot's);
``on_text`` gets the answer so far as it streams, ``on_wait`` hears of a wait the AI's own retries make."""


def default_request(lim: Any, model: str) -> Request:
    """One reading request to the chosen AI: light reasoning, no extended thinking (it notes, it doesn't solve). A
    connection that drops is asked again by the reading itself (``read``), which knows what came."""
    effort = "off" if lim.provider == "local" else "low"

    def run(system: str, content: list[dict[str, Any]], cancel: Optional[threading.Event],
            on_text: Optional[Callable[[str], None]] = None,
            on_wait: Optional[Callable[[dict[str, Any]], None]] = None) -> dict[str, Any]:
        return ai.one_shot(system, content, model or "sonnet", effort, cancel, on_text, 900, think=False, drops=0,
                           on_wait=on_wait)
    return run


def _files(batch: list[Page]) -> list[dict[str, Any]]:
    """Which pages a part has, file by file in order (for the progress): {"name", "kind" (the pages' group), "pages"},
    and for a lecture's pieces "lecture" (its number) and "date" (its day): the page names it in its language (its
    name is the AI's, in English)."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for p in batch:
        f = out.setdefault((p.group, p.name), {"name": p.name, "kind": p.group, "pages": []})
        f["pages"].append(p.page)
        if p.group == "lecture" and "date" not in f:
            n = re.match(r"\[L(\d+) ", p.label)
            day = (p.file.split("|") + ["", ""])[1]
            f.update(lecture=int(n.group(1)) if n else 0, date=day if re.fullmatch(r"\d{4}-\d\d-\d\d", day) else "")
    return list(out.values())


def read(pages: list[Page], question: str, lim: Any, request: Request,
         cancel: Optional[threading.Event] = None,
         progress: Optional[Callable[[dict[str, Any]], None]] = None,
         waits: tuple[float, ...] = DROP_WAITS, readers: int = 0) -> dict[str, Any]:
    """Every page's notes (``notes``: page key → notes), reading in parts what isn't kept yet, ``readers`` parts at
    once (0: as many as this AI takes, ``READERS``). Each page's notes are kept as soon as they have come whole, while
    the answer streams. A part whose connection drops (or whose service fails) is asked again after ``waits`` with the
    pages still missing, split in two when a try brought nothing new; a page left out of an answer is asked for once
    more on its own.

    ``progress`` gets the state as it changes (at most every ``PROGRESS_EVERY_S`` while text streams): {"done",
    "total" (parts), "pages", "read" (pages with notes), "cached" (read before), "noted" (this time), "started",
    "eta" (seconds left, about), "at" (when that was said), "parts": the parts being read [{"n", "from", "to" (their
    first and last page's place among ``pages``), "size", "files" (which pages: _files), "started", "noted",
    "words", "try", "retry": {"why", "status", "wait", "until", "split"} while waiting to ask again}]}.

    Raises Cancelled when ``cancel`` is set and the AI's error when a part fails for good; either way every page whose
    notes came is kept, so the next run reads only the rest. ``model_refused``: the model Codex turned down in a part
    (it read with its default instead)."""
    pictures = lim.pictures > 0
    notes = cached(pages, lim, move=True)
    before = len(notes)
    place = {p.key: i for i, p in enumerate(pages, start=1)}
    todo = [p for p in pages if p.key not in notes]
    queue: list[list[Page]] = batches(todo, lim, pictures)
    usage = {"input_tokens": 0, "output_tokens": 0}
    started = time.time()
    state: dict[str, Any] = {"done": 0, "total": len(queue), "pages": len(pages), "read": len(notes), "cached": before,
                             "noted": 0, "started": started, "parts": [],
                             **({"lectures": n_lec} if (n_lec := sum(1 for p in pages if p.group == "lecture")) else {})}
    n_readers = 1 if lim.provider in SERIAL else max(1, readers or READERS)
    unread: list[Page] = []
    mu = threading.Condition(threading.RLock())
    flow: dict[str, Any] = {"first": None, "said": 0.0, "failed": None, "busy": 0}
    halt = threading.Event()                      # Stop, or another part failed for good: every part stops now
    over = threading.Event()

    def stopped() -> bool:
        return halt.is_set() or (cancel is not None and cancel.is_set())

    def watch() -> None:                          # Stop reaches every part's request at once
        while not over.wait(0.2):
            if cancel is not None and cancel.is_set():
                halt.set()
                with mu:
                    mu.notify_all()
                return
    if cancel is not None:
        threading.Thread(target=watch, name="read-stop", daemon=True).start()

    def eta() -> int:
        """Seconds left, about: the pages still missing at the pace so far (every reader together), or at the usual
        pace until a few pages have come."""
        now = time.time()
        left = len(pages) - len(notes) - len(unread)
        if left <= 0:
            return 0
        first = flow["first"]
        pace = state["noted"] / (now - first) if first and state["noted"] >= 5 and now - first > 5 else 0.0
        pace = pace or n_readers / SECONDS_PER_PAGE
        return int(round((0 if first else max(0.0, FIRST_NOTES_S - (now - started))) + left / pace))

    def tell(now_: bool = False) -> None:
        if progress is None:
            return
        with mu:
            t0 = time.monotonic()
            if not now_ and t0 - flow["said"] < PROGRESS_EVERY_S:
                return
            flow["said"] = t0
            state["read"] = len(notes)
            snap = {**state, "eta": eta(), "at": round(time.time(), 1),
                    "parts": [dict(x, retry=dict(x["retry"]) if x.get("retry") else None) for x in state["parts"]]}
        progress(snap)

    def ask(batch: list[Page], info: dict[str, Any]) -> list[Page]:
        """One request, its notes kept as they come; the pages it left out."""
        if stopped():
            raise Cancelled()
        labels = [p.label for p in batch]
        own = {p.label: p for p in batch}
        got: dict[str, str] = {}
        pending: list[tuple[Page, str]] = []
        seen = {"text": "", "saved": time.monotonic()}

        def take(text: str, whole: bool) -> None:
            found = parse(text if whole else complete(text, labels), labels)
            fresh = [(own[x], v) for x, v in found.items() if got.get(x) != v]
            if not fresh:
                return
            with mu:
                for p, v in fresh:
                    if p.key not in notes:
                        state["noted"] += 1
                    notes[p.key] = got[p.label] = v
                flow["first"] = flow["first"] or time.time()
                pending.extend(fresh)
                info["noted"] = len(got)

        def flush(force: bool = False) -> None:
            if pending and (force or time.monotonic() - seen["saved"] >= KEEP_EVERY_S):
                out = list(pending)
                pending.clear()
                seen["saved"] = time.monotonic()
                _keep(out, lim)

        def on_text(so_far: str) -> None:
            grew = so_far[max(0, len(seen["text"]) - 1):]
            seen["text"] = so_far
            if info.get("retry"):                 # the AI asked again by itself, and its answer is coming
                info["retry"] = None
                tell(True)
            if "\n#" in grew or so_far.startswith("#") and len(grew) == len(so_far):   # a page's label began
                take(so_far, False)
                flush()
            info["words"] = len(so_far) // 6
            tell()

        def on_wait(w: dict[str, Any]) -> None:   # the AI's own wait before asking again (a failure on its side)
            said = info["retry"] = {"why": w.get("why") or "service", "status": int(w.get("status") or 0),
                                    "wait": float(w.get("wait") or 0),
                                    "until": round(time.time() + float(w.get("wait") or 0), 1), "split": False}
            tell(True)

            def over() -> None:                   # the wait is over: it asks again (the part's line shows the part)
                with mu:
                    if info.get("retry") is said:
                        info["retry"] = None
                tell(True)

            timer = threading.Timer(max(0.0, said["wait"]), over)
            timer.daemon = True
            timer.start()

        system = READ_SYSTEM.format(focus=FOCUS) + (
            "\n\n" + LECTURE_NOTE if any(p.group == "lecture" for p in batch) else "")
        try:
            r = request(system, _content(batch, pictures, question), halt, on_text, on_wait)
        except BaseException:
            take(seen["text"], False)              # what came whole is kept (the last page may have been cut off)
            flush(True)
            raise
        info["retry"] = None
        with mu:
            for k in usage:
                usage[k] += int((r.get("usage") or {}).get(k) or 0)
            if r.get("model_refused"):            # Codex turned the chosen model down (said to the student once)
                state["model_refused"] = r["model_refused"]
        take(r.get("text") or seen["text"], True)
        flush(True)
        return [p for p in batch if p.label not in got]

    def part(batch: list[Page], n: int, again: bool = False) -> list[Page]:
        """One part, asked again after a dropped connection (smaller when a try brought nothing): the pages its
        answers left out."""
        info: dict[str, Any] = {"n": n, "try": 0, "retry": None, "again": again}
        with mu:
            state["parts"].append(info)
        tries = fails = 0
        try:
            while True:
                with mu:
                    batch = [p for p in batch if p.key not in notes]
                if not batch:
                    return []
                tries += 1
                info.update(size=len(batch), files=_files(batch), started=round(time.time(), 1), noted=0, words=0,
                            retry=None, **{"try": tries, "from": place[batch[0].key], "to": place[batch[-1].key]})
                tell(True)
                try:
                    return ask(batch, info)
                except Cancelled:
                    raise
                except Exception as exc:  # noqa: BLE001 - asked again, or raised as it is
                    if not aierrors.worth_again(exc) or stopped():
                        raise
                    fails = 0 if info.get("noted") else fails + 1      # (this try's own pages: not the other part's)
                    most = TRIES if isinstance(exc, aierrors.Dropped) else 2
                    if fails >= most or tries >= MOST_TRIES:
                        log.warning("Read everything: part %d failed %d time(s) in a row, %d page(s) still to read: %s",
                                    n, fails, len([p for p in batch if p.key not in notes]), exc)
                        with mu:
                            state["tries"] = tries          # (said with what was kept)
                        raise
                    with mu:
                        rest = [p for p in batch if p.key not in notes]
                        split = bool(fails) and len(rest) > 1 and not again
                        if split:                 # nothing came: the other half waits its turn, as a part of its own
                            half = (len(rest) + 1) // 2
                            queue.insert(0, rest[half:])
                            state["total"] += 1
                            rest = rest[:half]
                            mu.notify_all()
                    batch = rest
                    wait = waits[min(max(1, fails), len(waits)) - 1] if waits else 0.0
                    why = "dropped" if isinstance(exc, aierrors.Dropped) else "service"
                    log.info("Read everything: part %d %s (%s); asking again in %.0f s for %d page(s)%s", n,
                             "dropped" if why == "dropped" else "failed on the AI's side", exc, wait, len(rest),
                             ", split in two" if split else "")
                    info["retry"] = {"why": why, "status": int(getattr(exc, "status", 0) or 0), "wait": wait,
                                     "until": round(time.time() + wait, 1), "split": split}
                    tell(True)
                    if wait:
                        aierrors.wait(wait, halt)
        finally:
            with mu:
                state["parts"].remove(info)

    def reader() -> None:
        while True:
            with mu:
                while not queue and flow["busy"] and not stopped() and flow["failed"] is None:
                    mu.wait(0.5)            # another part may still give back half of itself
                if not queue or stopped() or flow["failed"] is not None:
                    return
                batch = queue.pop(0)
                flow["busy"] += 1
                n = state["done"] + flow["busy"]
            try:
                left = part(batch, n)
                if left:                     # left out of its answer: asked for once more, on their own
                    log.info("Read everything: %d page(s) left out of a part, asked again", len(left))
                    still = part(left, n, again=True)
                    with mu:
                        unread.extend(still)
                with mu:
                    state["done"] += 1
            except BaseException as exc:  # noqa: BLE001 - raised by read() below
                with mu:
                    if flow["failed"] is None and not isinstance(exc, Cancelled):
                        flow["failed"] = exc
                if not isinstance(exc, Cancelled):
                    halt.set()               # the other parts stop too (keeping what came)
            finally:
                with mu:
                    flow["busy"] -= 1
                    mu.notify_all()
                tell(True)

    try:
        others = [threading.Thread(target=reader, name=f"read-{i}", daemon=True) for i in range(1, n_readers)]
        for th in others:
            th.start()
        reader()
        for th in others:
            th.join()
    finally:
        over.set()
    state.update(read=len(notes), unread=[p.label for p in unread], usage=usage, parts=[])
    if flow["failed"] is not None:
        tell(True)
        raise flow["failed"]
    if stopped():
        tell(True)
        raise Cancelled()
    tell(True)
    return {**{k: v for k, v in state.items() if k not in ("parts", "started", "noted")}, "notes": notes}


# ---------------------------------------------------------------------------------------------------- the answer
def notes_text(pages: list[Page], notes: dict[str, str]) -> str:
    """The notes for the AI that answers, in the material's order, by file."""
    out: list[str] = []
    name = None
    lectures = False
    for p in pages:
        note = notes.get(p.key)
        if not note:
            continue
        if p.name != name:
            name = p.name
            out.append(f"## {name}")
        out.append(f"{p.label}\n{note}")
        lectures = lectures or p.group == "lecture"
    if not out:
        return ""
    head = ("=== NOTES ON THE PAGES YOU DON'T SEE IN FULL ===\n(Every page below was read for you, as a picture where "
            "it has one, and noted under its label: use these notes like the pages themselves and cite them by the "
            "label." + (" The lectures among them were read from their full transcripts, piece by piece: cite their "
                        "moments as [L2 P1 05:10], with the times the notes give." if lectures else "") + ")")
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
    from .search import overlap

    have = [(i, p) for i, p in enumerate(pages) if notes.get(p.key)]
    if not have:
        return ""
    scores = overlap(question, [notes[p.key] for _, p in have])
    rows = [(s, i, f"{p.label}\n{notes[p.key]}") for s, (i, p) in zip(scores, have)]
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
    from .search import overlap

    pictured = [(i, p) for i, p in enumerate(pages) if p.picture is not None]
    if most <= 0 or not pictured:
        return []
    scores = overlap(question, [f"{notes.get(p.key, '')} {p.text}" for _, p in pictured])
    best = sorted(((s, i, p) for s, (i, p) in zip(scores, pictured) if s > 0), key=lambda x: (-x[0], x[1]))[:most]
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
