"""The offline AI option: a small instruct model (Qwen3, Apache-2.0) run by CTranslate2 — the engine that already runs
the speech model, so nothing else is installed. Good enough for short study notes, a weekly summary and simple
questions about a lecture; study plans, review sheets and study packs need ChatGPT or Claude.

Long transcripts are summarised in parts first (the small model keeps a few thousand tokens in view); the summary is
kept for the other kinds of notes of the same lecture. A chat gets, for each question, the lecture passages that match
it. On an NVIDIA GPU it writes ~40 words/s; on the processor a few words per second, so notes for a lecture take minutes
there.
"""

from __future__ import annotations

import collections
import contextlib
import hashlib
import itertools
import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from .cancel import Cancelled
from .i18n import N_, t

log = logging.getLogger("studia")
IDLE_UNLOAD_S = 600
_lock = threading.Lock()
_loaded: dict[str, Any] = {}          # key -> {"gen", "tok", "used", "device", "room", "answer"}
# On the graphics card every token of the prompt and of the answer takes memory. Measured on an RTX 4070 Laptop (8 GB):
# the model loaded (MiB) and what each token adds (MiB). The 4B model at the old 6,000-token prompt filled the card and
# wrote ~1 token/s; with 1,840 + 1,500 tokens it slowed to 11 tokens/s (Windows lends the processor's memory), with
# 1,420 + 1,500 it wrote 25/s; at 3,600 + 200 it ran out of memory.
GPU_MEMORY = {"qwen3-1.7b": (2200, 0.55), "qwen3-4b": (5200, 0.85)}
GPU_SPARE_MB = 600                    # the screen, the app's window and other programs use the card too
MAX_GPU_BUDGET, MAX_GPU_ANSWER = 6000, 3000
MIN_GPU_BUDGET = 1000                 # out of graphics memory with less: the processor
CPU_BUDGET, CPU_ANSWER = 2500, 1200   # the processor is slow with long prompts and writes a few words per second
# frees the graphics card before the model loads there: True if it freed something (jobs.py: an idle speech model)
make_room: Optional[Callable[[], bool]] = None


def _preset(key: str) -> dict[str, Any]:
    from .components import LLM_PRESETS

    return LLM_PRESETS[key]


def _make_room() -> bool:
    try:
        return bool(make_room and make_room())
    except Exception:  # noqa: BLE001 - only a chance to free memory
        log.warning("Freeing the graphics card failed", exc_info=True)
        return False


def _out_of_memory(exc: BaseException) -> bool:
    low = str(exc).lower()
    # the card's memory (cuBLAS says ALLOC_FAILED or NOT_INITIALIZED when it can't get any); the PC's (std::bad_alloc)
    return any(w in low for w in ("out of memory", "bad_alloc", "alloc_failed", "not_initialized"))


# what the student reads when the model can't run, instead of CTranslate2's own words ("Unable to open file
# 'model.bin'", "std::bad_alloc"); each names a place that has the way out (Settings → AI shows Set up AI for the
# offline model, and the setup's AI step has the models)
NO_MEMORY = N_("There isn't enough free memory for the offline model. Close other programs (a browser with many tabs "
               "uses a lot), then try again; or switch to the Basic offline model: Settings → AI → Set up AI.")
DAMAGED = N_("The offline model's files on this PC are damaged, so it can't be loaded. Download it again: Settings → "
             "AI → Set up AI, then go on to the download (only the model is downloaded).")
ENGINE = N_("The engine that runs the offline model could not start on this PC ({error}). Reinstalling IEUStudia "
            "usually fixes it.")


def _mark_damaged(key: str) -> None:
    """The model's files didn't load on the processor: it counts as not downloaded (Settings → AI offers it again)
    and its next download fetches every file afresh (components.download_hf), not only the ones of the wrong size."""
    from .components import llm_dir

    d = llm_dir(key)
    try:
        if d.is_dir():
            (d / ".damaged").write_text("1", encoding="utf-8")
            (d / ".complete").unlink(missing_ok=True)
    except OSError:
        log.warning("The offline model %s could not be marked as damaged", key, exc_info=True)


def _load_failure(key: str, exc: BaseException) -> RuntimeError:
    log.warning("The offline AI model %s could not be loaded: %r", key, exc)
    if _out_of_memory(exc) or isinstance(exc, MemoryError):
        return RuntimeError(t(NO_MEMORY))
    if isinstance(exc, ImportError):
        return RuntimeError(t(ENGINE, error=str(exc)[:200]))
    _mark_damaged(key)
    return RuntimeError(t(DAMAGED))


def _load(key: str, cpu: bool = False) -> dict[str, Any]:
    """The model, loaded once. ``cpu``: (again) on the processor, after the graphics card ran out of memory."""
    from .components import llm_dir, llm_ready
    from .engine.models import cuda_compute_type, resolve_device, setup_cuda_dlls

    with _lock:
        hit = _loaded.get(key)
        if hit and not (cpu and hit["device"] == "cuda"):
            hit["used"] = time.monotonic()
            return hit
        if not llm_ready(key):
            raise RuntimeError(t("The offline AI model is not downloaded yet (Settings → AI)."))
        setup_cuda_dlls()
        try:
            import ctranslate2
            from tokenizers import Tokenizer
        except (ImportError, OSError) as exc:                     # its native library did not load on this PC
            raise _load_failure(key, exc if isinstance(exc, ImportError) else ImportError(str(exc))) from exc

        _loaded.clear()                      # one model at a time (also frees the one on the graphics card)
        device = "cpu" if cpu else resolve_device("auto")[0]
        threads = max(1, (os.cpu_count() or 4) // 2)
        started = time.monotonic()

        def generator(device: str) -> Any:
            ctype = cuda_compute_type(("int8_float16", "int8_float32", "int8")) if device == "cuda" else "int8"
            return ctranslate2.Generator(str(llm_dir(key)), device=device, compute_type=ctype,
                                         intra_threads=0 if device == "cuda" else threads)

        if device == "cuda":
            _make_room()
        try:
            try:
                gen = generator(device)
            except RuntimeError as exc:
                if device != "cuda" or not (_out_of_memory(exc) or _card_cant(exc)):
                    raise
                log.warning("The offline AI model can't run on the graphics card; it runs on the processor (%s)", exc)
                device = "cpu"
                gen = generator(device)
            tok = Tokenizer.from_file(str(llm_dir(key) / "tokenizer.json"))
        except Exception as exc:  # noqa: BLE001 - a damaged file, no memory: said plainly
            raise _load_failure(key, exc) from exc
        log.info("Offline AI model %s loaded on %s in %.1fs", key, device, time.monotonic() - started)
        hit = {"gen": gen, "tok": tok, "used": time.monotonic(), "device": device}
        hit["room"], hit["answer"] = _gpu_room(key) if device == "cuda" else (CPU_BUDGET, CPU_ANSWER)
        _loaded[key] = hit
        threading.Thread(target=_idle_unloader, args=(key, hit), daemon=True).start()
        return hit


def _less_memory(key: str) -> bool:
    """After the graphics card ran out of memory: free the idle speech model, else halve the prompt, else move to the
    processor (the transcriber halves its batches the same way). False if nothing is left to try."""
    if _make_room():
        return True
    with _lock:
        hit = _loaded.get(key)
        if hit is None or hit["device"] != "cuda":
            return False
        if hit["room"] // 2 >= MIN_GPU_BUDGET:
            hit["room"] //= 2
            return True
    _load(key, cpu=True)
    return True


def _card_cant(exc: BaseException) -> bool:
    """The graphics card failed to run the model, not because it was full: the processor runs it this time.
    hardware.remember_card_failed decides whether that is remembered for this PC (a card too old for CTranslate2 at
    once, a one-off fault never), so the speech model doesn't try it either."""
    text = str(exc).lower()
    if _out_of_memory(exc) or not any(w in text for w in ("cuda", "cublas", "cudnn")):
        return False
    try:
        from .hardware import remember_card_failed

        remember_card_failed(str(exc), "offline AI")
    except Exception:  # noqa: BLE001 - only a memory for the next starts
        log.debug("remembering the card failed", exc_info=True)
    return True


def _with_less_memory(key: str, run: Callable[[], Any]) -> Any:
    """run(); when the graphics card runs out of memory, again with less (see _less_memory); when it can't run the
    model at all, again on the processor."""
    moved = False
    while True:
        try:
            return run()
        except RuntimeError as exc:
            if not _out_of_memory(exc):
                if moved or device_of(key) != "cuda" or not _card_cant(exc):
                    raise
                log.warning("The graphics card can't run the offline AI; it runs on the processor (%s)", exc)
                _load(key, cpu=True)
                moved = True
                continue
            if not _less_memory(key):
                log.warning("The offline AI ran out of memory with nothing left to try: %s", exc)
                raise RuntimeError(t(NO_MEMORY)) from exc
            log.warning("The offline AI ran out of graphics memory; trying again with less (%s)", exc)


def _idle_unloader(key: str, hit: dict[str, Any]) -> None:
    while True:
        time.sleep(60)
        with _lock:
            if _loaded.get(key) is not hit:      # unloaded, or loaded again (e.g. on the processor)
                return
            if time.monotonic() - hit["used"] > IDLE_UNLOAD_S:
                _loaded.pop(key, None)
                log.info("Offline AI model unloaded (idle)")
                return


def device_of(key: str) -> str:
    with _lock:
        hit = _loaded.get(key)
    return hit["device"] if hit else ""


def _prompt(messages: list[dict[str, str]], template: str, think: bool = False) -> str:
    """ChatML (Qwen). The hybrid Qwen3 gets an empty think block so it answers directly; ``think``: it begins its own
    (generate() closes it after a budget of tokens)."""
    out = []
    for m in messages:
        out.append(f"<|im_start|>{m['role']}\n{m['content'].strip()}<|im_end|>\n")
    out.append("<|im_start|>assistant\n")
    if template == "qwen3":
        out.append("<think>\n" if think else "<think>\n\n</think>\n\n")
    return "".join(out)


THINK_TOKENS = 600            # "Thinking on": what the model may think before it answers (each a few hundredths of
                              # a second on a graphics card, about a tenth on the processor)


def think_budget(key: str, on: bool) -> int:
    """The thinking tokens of one answer (they take room from the text it reads: the window is small)."""
    return min(THINK_TOKENS, budget(key) // 3) if on and _preset(key).get("template", "qwen3") == "qwen3" else 0


def count_tokens(text: str, key: str) -> int:
    try:
        return len(_load(key)["tok"].encode(text).ids)
    except Exception:  # noqa: BLE001
        return int(len(text) / 3.5)


def generate(messages: list[dict[str, str]], key: str, max_tokens: int = 900,
             on_text: Optional[Callable[[str], None]] = None, cancel: Optional[threading.Event] = None,
             think: int = 0) -> dict[str, Any]:
    """The model's answer to ``messages``, at most ``max_tokens`` long. ``think``: it first thinks, at most that many
    tokens (not shown, not part of the answer), then answers."""
    m = _load(key)
    tok, gen = m["tok"], m["gen"]
    prompt = _prompt(messages, _preset(key).get("template", "qwen3"), think=think > 0)
    tokens = tok.encode(prompt).tokens
    ids: list[int] = []
    thought: list[int] = []
    started = time.monotonic()
    from .engine.core import processor_for_ai

    def run(prefix: list[str], limit: int, ends: list[str], out: list[int], show: bool) -> None:
        for step in gen.generate_tokens(prefix, max_length=limit, sampling_temperature=0.3, sampling_topk=20,
                                        end_token=ends):
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            out.append(step.token_id)
            if show and on_text and len(out) % 6 == 0:
                on_text(tok.decode(out))

    # on the processor a transcription waits meanwhile: running both made the AI write four times slower
    with processor_for_ai() if m["device"] != "cuda" else contextlib.nullcontext():
        if think > 0:                  # the thinking, closed after its budget; then the answer after it
            if on_text:
                on_text(t("*Thinking…*"))
            run(tokens, think, ["</think>", "<|im_end|>", "<|endoftext|>"], thought, False)
            words = tok.decode(thought).replace("</think>", "").strip()
            tokens = tok.encode(f"{prompt}{words}\n</think>\n\n").tokens
        run(tokens, max_tokens, ["<|im_end|>", "<|endoftext|>"], ids, True)
    with _lock:
        m["used"] = time.monotonic()
    text = tok.decode(ids).replace("<think>", "").replace("</think>", "").strip()
    if on_text:
        on_text(text)
    return {"text": text, "usage": {"input_tokens": len(tokens), "output_tokens": len(ids)},
            "seconds": round(time.monotonic() - started, 1), "model": key}


def _device(key: str) -> str:
    """Where the model writes: where it is loaded, or else where it will be."""
    from .engine.models import resolve_device

    return device_of(key) or resolve_device("auto")[0]


def _sizes(key: str) -> tuple[int, int]:
    with _lock:
        hit = _loaded.get(key)
    if hit:
        return hit["room"], hit["answer"]
    return _gpu_room(key) if _device(key) == "cuda" else (CPU_BUDGET, CPU_ANSWER)


def budget(key: str) -> int:
    """How many input tokens one request may carry (less after the graphics card ran out of memory)."""
    return _sizes(key)[0]


def answer_tokens(key: str) -> int:
    """How long an answer may get (study notes are asked to end well before it)."""
    return _sizes(key)[1]


def _gpu_room(key: str) -> tuple[int, int]:
    """(prompt, answer) tokens that fit on this graphics card next to the model: half for the answer, up to 3,000 (an
    8 GB card: 6,000 + 3,000 for 1.7B, 1,400 + 1,400 for 4B)."""
    from .engine.models import detect_gpu

    loaded_mb, per_token = GPU_MEMORY.get(key, GPU_MEMORY["qwen3-4b"])
    vram = detect_gpu(quick=True).get("vram_mb") or 8192
    fits = int((vram - loaded_mb - GPU_SPARE_MB) / per_token)
    answer = max(MIN_GPU_BUDGET, min(MAX_GPU_ANSWER, fits // 2)) // 100 * 100
    return max(MIN_GPU_BUDGET, min(MAX_GPU_BUDGET, fits - answer)) // 100 * 100, answer


def _chunks(text: str, key: str, size: int) -> list[str]:
    parts, cur, n = [], [], 0
    for para in text.split("\n"):
        cost = count_tokens(para, key) + 1
        if cur and n + cost > size:
            parts.append("\n".join(cur))
            cur, n = [], 0
        cur.append(para)
        n += cost
    if cur:
        parts.append("\n".join(cur))
    return parts


ENGLISH = "Write in English (keep technical terms as they are)."


CONDENSE = ("You condense lecture transcripts into dense, factual bullet notes: every definition, formula, example and "
            "announcement (quizzes, exams, homework). At most about {words} words. " + ENGLISH)
CONDENSE_V = 2                # the condensing above: notes condensed before it changed are not reused
TASK_ROOM = 600               # tokens kept for the task next to the condensed notes (the study notes' takes ~500)
PASS_TOKENS, PASS_MIN = 450, 150                  # what one part is condensed to: at most, at least
TOKENS_PER_WORD = 1.8         # the model's notes (Markdown, LaTeX): 1,200 tokens were 659 words
# answer tokens per word asked for, so that the answer ends well before its cap: the 1.7B model writes study notes about
# twice as long as asked (asked 300 words: cut off at 1,200 tokens; 250: 488 words, complete); the 4B one keeps to it
ASKED_WORD_TOKENS = {"qwen3-1.7b": 4.8, "qwen3-4b": 3.3}
LENGTH = ("Keep the whole answer under about {words} words, with short sections, so that it ends complete. Don't "
          "mention the word count.")
CACHE_FILES = 200             # condensed lectures kept in the cache folder (a few KB each)
_cache_lock = threading.Lock()
_condensed: "collections.OrderedDict[str, dict[str, Any]]" = collections.OrderedDict()   # cache file -> entry


def _words(tokens: float) -> int:
    return max(50, int(tokens / TOKENS_PER_WORD) // 10 * 10)


def _cache_file(text: str, key: str, limit: int) -> Path:
    from .paths import app_dir

    digest = hashlib.sha256(f"{CONDENSE_V}\n{key}\n{limit}\n{text}".encode("utf-8")).hexdigest()[:32]
    return app_dir() / "cache" / "llm" / f"{digest}.json"


def _cached(path: Path) -> Optional[dict[str, Any]]:
    """{"text": the condensed text, "tasks": the kinds of notes written from it} or None."""
    with _cache_lock:
        entry = _condensed.get(str(path))
    if entry is not None:
        return entry
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not (isinstance(entry, dict) and isinstance(entry.get("text"), str) and isinstance(entry.get("tasks"), list)):
        return None
    _remember(path, entry, save=False)
    return entry


def _remember(path: Path, entry: dict[str, Any], save: bool = True) -> None:
    with _cache_lock:
        _condensed[str(path)] = entry
        _condensed.move_to_end(str(path))
        while len(_condensed) > 8:
            _condensed.popitem(last=False)
    if not save:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
        kept = sorted(path.parent.glob("*.json"), key=lambda p: p.stat().st_mtime)
        for old in kept[:-CACHE_FILES]:
            old.unlink(missing_ok=True)
    except OSError:
        log.warning("The condensed lecture could not be kept", exc_info=True)


def _condense(text: str, key: str, limit: int, cancel: Optional[threading.Event],
              on_text: Optional[Callable[[str], None]], usage: dict[str, int]) -> str:
    """The text condensed part by part until it fits next to the task (each part asked for a length that leaves room
    for all of them; a very long day goes round twice)."""
    room = max(limit // 2, limit - TASK_ROOM)
    size = count_tokens(text, key)
    while size > room:
        parts = _chunks(text, key, limit - 400)
        per = max(PASS_MIN, min(PASS_TOKENS, room // len(parts) - 10))
        cap = per * 5 // 4
        notes = []
        for i, part in enumerate(parts, 1):
            if on_text:
                on_text(t("*Reading part {i} of {n}…*", i=i, n=len(parts)))
            r = generate([{"role": "system", "content": CONDENSE.format(words=_words(per))},
                          {"role": "user", "content": part}], key, cap, None, cancel)
            note = r["text"]
            if r["usage"]["output_tokens"] >= cap and "\n" in note:
                note = note.rsplit("\n", 1)[0]               # cut off: without its unfinished line
            notes.append(note)
            for k in usage:
                usage[k] += r["usage"][k]
        shorter = "Condensed notes of the lecture, in order:\n\n" + "\n\n".join(notes)
        n = count_tokens(shorter, key)
        if n >= size:
            break
        text, size = shorter, n
    return text


def one_shot(system_prompt: str, text: str, key: str, cancel: Optional[threading.Event] = None,
             on_text: Optional[Callable[[str], None]] = None, max_tokens: Optional[int] = None,
             think: bool = False) -> dict[str, Any]:
    """One request; a text longer than the model's comfortable size is condensed part by part first. The condensed text
    is kept (in memory and the cache folder) for the other kinds of notes of the same lecture; the same kind asked for
    again ("Generate again") condenses afresh. The answer is asked to end well before its cap and says when it was cut
    off anyway. ``think``: "Thinking on" (it thinks first; the text it reads is that much shorter)."""
    return _with_less_memory(key, lambda: _one_shot(system_prompt, text, key, cancel, on_text, max_tokens, think))


def _one_shot(system_prompt: str, text: str, key: str, cancel: Optional[threading.Event],
              on_text: Optional[Callable[[str], None]], max_tokens: Optional[int],
              think: bool = False) -> dict[str, Any]:
    from .engine.core import processor_for_ai

    with contextlib.suppress(Exception):     # loaded first: on a full card it loads on the processor, with its sizes
        _load(key)                           # (an error shows when it writes, as before)
    gpu = _device(key) == "cuda"
    cap = max_tokens or answer_tokens(key)
    words = max(50, int(cap / ASKED_WORD_TOKENS.get(key, 4.8)) // 10 * 10)
    system = f"{system_prompt}\n{ENGLISH}\n{LENGTH.format(words=words)}"
    task = hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()[:16]
    kept: Optional[tuple[Path, dict[str, Any]]] = None       # the cache file and its condensed text
    # on the processor, held from the first part to the answer: generate() lets go after each part, and a waiting
    # transcription would start a batch in between (generate() holds it as well: the count nests)
    with processor_for_ai() if not gpu else contextlib.nullcontext():
        thinking = think_budget(key, think)
        limit = budget(key) - thinking
        usage = {"input_tokens": 0, "output_tokens": 0}
        if count_tokens(system, key) + count_tokens(text, key) > limit:
            path = _cache_file(text, key, limit)
            entry = _cached(path)
            if entry is None or task in entry["tasks"]:      # the first time, or this kind again: condensed afresh
                entry = {"text": _condense(text, key, limit, cancel, on_text, usage), "tasks": []}
                _remember(path, entry)
            kept = path, entry
            text = entry["text"]
        r = generate([{"role": "system", "content": system}, {"role": "user", "content": text}],
                     key, cap, on_text, cancel, **({"think": thinking} if thinking else {}))
    if r["usage"]["output_tokens"] >= cap:
        r["text"] += "\n\n" + t("*(Cut off here: the offline model reached its length limit.)*")
        if on_text:
            on_text(r["text"])
    if kept:
        path, entry = kept
        _remember(path, {"text": entry["text"], "tasks": [x for x in entry["tasks"] if x != task][-19:] + [task]})
    for k in usage:
        r["usage"][k] += usage[k]
    return r


CHAT_SYSTEM = ("You answer a university student's questions about their own lectures. Below are the passages of the "
               "lectures that match the question: speech-recognition transcripts, mostly English with Turkish passages "
               "tagged [TR], each paragraph starting with [mm:ss]. Answer briefly from them and cite the moments you use "
               "like {cite}. If they don't cover the question, say so.")
CITE_ONE, CITE_MANY = "[P2 14:05] (part, time)", "[L2 P1 05:10] (lecture L2, part, time)"   # as the app links them


def lecture_excerpts(lectures: str, question: str, key: str, max_tokens: int) -> str:
    """The paragraphs of the chat's lectures that match the question best (matched as the search matches: search.py),
    in lecture order under their headings, in at most ``max_tokens``. A question that matches nothing gets the
    emphasised (⭐) moments, then an even spread."""
    from .search import overlap

    blocks: list[tuple[str, str, str]] = []          # (lecture heading, part heading, paragraph)
    day = part = ""
    for line in lectures.splitlines():
        if line.startswith("# "):
            day, part = line, ""
        elif line.startswith("## "):
            part = line
        elif line.strip():
            blocks.append((day, part, line))
    if not blocks:
        return ""
    scores = [s + (0.5 if "⭐" in p else 0.0) for s, (_, _, p) in zip(overlap(question, [p for _, _, p in blocks]), blocks)]
    if max(scores) >= 1:
        # equally good matches: the newest lectures first (they say what is coming next)
        ranked = [i for i in sorted(range(len(blocks)), key=lambda i: (-scores[i], -i)) if scores[i] > 0]
    else:
        ranked = [i for i in reversed(range(len(blocks))) if "⭐" in blocks[i][2]]       # newest first
        ranked += [i for i in range(0, len(blocks), max(1, len(blocks) // 40)) if i not in ranked]
    chosen: set[int] = set()
    used = 0
    for i in ranked:
        cost = count_tokens(blocks[i][2], key) + 1
        if used + cost <= max_tokens:
            chosen.add(i)
            used += cost
        if used >= max_tokens - 20:
            break
    out: list[str] = []
    last_day = last_part = None
    for i in sorted(chosen):
        d, p, para = blocks[i]
        if d != last_day:
            out.append(d)
            last_day, last_part = d, None
        if p and p != last_part:
            out.append(p)
            last_part = p
        out.append(para)
    return "\n".join(out)


def _fit(text: str, key: str, max_tokens: int) -> str:
    n = count_tokens(text, key)
    if n <= max_tokens:
        return text
    return text[:max(0, int(len(text) * max_tokens / max(1, n)) - 20)].rsplit("\n", 1)[0] + "\n…"


def _course_essentials(brief: str) -> str:
    """The course line and the announced assessments of a chat's course brief (the rest is too much for the model)."""
    lines = brief.splitlines()
    out = lines[:1]
    for i, line in enumerate(lines):
        if line.startswith("Assessments the lecturer announced"):
            out += [line] + list(itertools.takewhile(lambda x: x.startswith("  "), lines[i + 1:]))[:10]
            break
    return "\n".join(out)


class LocalSession:
    """A chat with the offline model. Its window holds a few thousand tokens, so instead of the whole lectures it gets
    short instructions, the course's announced assessments and, for each question, the lecture passages that match it
    best; earlier turns are kept short."""

    def __init__(self, system_prompt: str, key: str, think: bool = False) -> None:
        self.key_name = key
        self.think = think                    # "Thinking on" (the chat can change it between questions)
        self.model = key
        self.key: Any = None
        head, _, self._lectures = system_prompt.partition("\n\n=== LECTURES ===\n\n")
        self._course = _course_essentials(head.partition("\n\n=== COURSE ===\n\n")[2])
        self._cite = CITE_MANY if re.search(r"(?m)^# L\d+ · ", self._lectures) else CITE_ONE   # several: L1, L2…
        self.history: list[dict[str, str]] = []
        self.last_used = time.monotonic()
        self._turn = threading.Lock()

    def alive(self) -> bool:
        return True

    def busy(self) -> bool:
        return self._turn.locked()

    def seed(self, history: list[dict[str, Any]]) -> None:
        """The conversation so far, from the library (a new session for an earlier chat)."""
        self.history = [{"role": m["role"], "content": (m.get("text") or "")[-1500:]} for m in history
                        if m.get("role") in ("user", "assistant") and (m.get("text") or "").strip()][-4:]

    def _recent(self) -> list[dict[str, str]]:
        """The last exchange, shortened: the model's window is small."""
        return [{"role": m["role"], "content": m["content"][:800]} for m in self.history[-2:]]

    def _system(self, question: str, extra: str) -> str:
        k = self.key_name
        fixed = (f"{CHAT_SYSTEM.format(cite=self._cite)}\n{ENGLISH}"
                 + (f"\n\n=== COURSE ===\n{self._course}" if self._course else ""))
        used = count_tokens(fixed, k) + sum(count_tokens(m["content"], k) for m in self._recent())
        room = max(300, budget(k) - think_budget(k, self.think) - used - count_tokens(question, k) - 50)
        recent = " ".join(m["content"] for m in self.history[-2:] if m["role"] == "user")    # follow-up questions
        # the other passages are usually short: the lectures give up only what they need, at most a quarter
        share = room - min(count_tokens(extra, k), room // 4) if extra else room
        lectures = lecture_excerpts(self._lectures, f"{question} {recent}", k, share)
        more = _fit(extra, k, room - count_tokens(lectures, k)) if extra else ""
        return (fixed + (f"\n\n=== LECTURE PASSAGES ===\n{lectures}" if lectures else "")
                + (f"\n\n=== OTHER MATCHING PASSAGES ===\n{more}" if more.strip() else ""))

    def ask(self, content: list[dict[str, Any]], on_text: Optional[Callable[[str], None]] = None,
            stop: Optional[threading.Event] = None, timeout_s: float = 900) -> dict[str, Any]:
        texts = [b["text"] for b in content if b.get("type") == "text" and (b.get("text") or "").strip()]
        question = texts[-1].strip() if texts else ""
        extra = "\n\n".join(texts[:-1])           # matching passages of course files and of other lectures
        def turn() -> dict[str, Any]:
            msgs = [{"role": "system", "content": self._system(question, extra)}]
            msgs += self._recent()
            msgs.append({"role": "user", "content": question})
            thinking = think_budget(self.key_name, self.think)
            return generate(msgs, self.key_name, 700, on_text, stop, **({"think": thinking} if thinking else {}))

        with self._turn:
            try:
                r = _with_less_memory(self.key_name, turn)
                stopped = False
            except Cancelled:
                return {"text": "", "usage": {}, "stopped": True}
            self.history += [{"role": "user", "content": question[-1500:]},
                             {"role": "assistant", "content": r["text"][-1500:]}]
            self.last_used = time.monotonic()
            return {"text": r["text"], "usage": r["usage"], "stopped": stopped}

    def kill(self) -> None:
        pass

    def terminate(self) -> None:
        pass

    def close(self) -> None:
        pass
