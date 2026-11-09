"""Listening to YouTube videos for study plans. A candidate whose captions can't show what it teaches (it has none, they
are in another language, or YouTube's automatic ones are far too short or garbled: videos.caption_problem) is checked
by what is said in it all the same: its original sound track is downloaded (the smallest format, videos.download_audio),
transcribed on this PC with the app's own speech model, kept like captions (videos.remember_heard) and deleted. The
plan then checks it like a video with captions.

Lectures come first: nothing is listened to while one is transcribing or waiting to, and one queued meanwhile stops
the listening (the lecture doesn't wait behind it). The speech model is the one already downloaded for the lectures,
on the graphics card when it is free; else the small model on the processor; without either, nothing is listened to.
A plan listens to at most MAX_VIDEOS videos, to the first LISTEN_S of each (LISTEN_CPU_S on the processor; none
longer than MAX_MINUTES: too much to download), for at most MAX_SOUND_S of sound in all (MAX_SOUND_CPU_S on the
processor) and MAX_WALL_S of its time; each video it leaves out says why (REASONS). A video whose language YouTube
doesn't say is told apart by the model (Turkish or English): forced into the plan's language, a Turkish lecture came
back as an English translation and was taken for an English video.
"""

from __future__ import annotations

import logging
import shutil
import threading
import time
from typing import Any, Callable, Optional

from .cancel import Cancelled
from .i18n import N_, t
from .paths import app_dir

log = logging.getLogger("studia")

MAX_VIDEOS = 8              # listened to per plan (with its rounds of more searches)
LISTEN_S = 45 * 60          # of a longer video only its start is listened to (and checked)
LISTEN_CPU_S = 20 * 60      # on the processor (a 41-minute video was more than all of MAX_SOUND_CPU_S: never heard)
MAX_MINUTES = 120           # a longer one's sound is too much to download
MAX_SOUND_S = 120 * 60      # sound per plan on the graphics card (large-v3 there: ~2-4 s a minute)
MAX_SOUND_CPU_S = 40 * 60   # on the processor (the small model there: ~10-15 s a minute)
MAX_WALL_S = 12 * 60        # of the plan's time
FAST_CPU_MODEL = "small"    # the model for the processor (engine.models.MODEL_PRESETS)

# why a video wasn't listened to (English: t() to show them, with the counts as placeholders)
REASONS = {
    "too_long": N_("it is longer than {minutes} minutes"),
    "limit": N_("the plan listens to at most {videos} videos"),
    "time": N_("the plan's time for listening ran out"),
    "lectures": N_("your lectures were being transcribed, and they go first"),
    "no_model": N_("no speech model for it is on this PC"),
    "setup": N_("the setup was still downloading"),
    "refused": N_("YouTube didn't let IEUStudia download its sound"),
    "no_speech": N_("no speech was heard in it"),
    "failed": N_("listening to it failed"),
}


def reason_text(code: str, english: bool = False) -> str:
    """A skipped video's reason in words (``english``: for the AI and the exported plan)."""
    say = REASONS.get(code, REASONS["failed"])
    args = {"minutes": MAX_MINUTES, "videos": MAX_VIDEOS}
    return say.format(**args) if english else t(say, **args)


def speech_choice(settings: Any) -> tuple[str, str, str, str]:
    """The speech model to listen with: (model, device preference, where it runs - "cuda" or "cpu", "") - the
    lectures' own model when it is on this PC and the graphics card is free for it, else the small model on the
    processor - or ("", "", "", "no_model")."""
    from .engine.models import model_is_downloaded, resolve_device

    model, device = str(getattr(settings, "model", "") or ""), str(getattr(settings, "device", "auto") or "auto")
    if model and model_is_downloaded(model):
        where, _ = resolve_device(device)
        if where == "cuda" and not _ai_on_card():
            return model, device, "cuda", ""
        if model == FAST_CPU_MODEL and where == "cpu":
            return model, device, "cpu", ""                   # the lectures' own engine
    if model_is_downloaded(FAST_CPU_MODEL):
        return FAST_CPU_MODEL, "cpu", "cpu", ""
    return "", "", "", "no_model"


def _ai_on_card() -> bool:
    try:
        from .engine.transcriber import _ai_on_gpu

        return _ai_on_gpu()
    except Exception:  # noqa: BLE001 - never stops a plan
        return False


def languages_for(info: Any, wanted: tuple[str, ...]) -> tuple[str, ...]:
    """The languages to transcribe a video in: the one it is spoken in (with English beside Turkish: Turkish teachers
    say the English terms); when YouTube doesn't say, Turkish and English both, the model telling which one it is (so a
    Turkish video in an English plan is heard as Turkish, and left out as in another language); else every language
    the plan wants."""
    from .videos import _base

    spoken = _base(getattr(info, "language", "") or "")
    bases = tuple(dict.fromkeys(_base(x) for x in wanted)) or ("en",)
    if spoken in bases:
        return ("tr", "en") if spoken == "tr" else (spoken,)
    if not spoken:
        return tuple(dict.fromkeys(("tr", "en", *bases)))
    return bases


class _Either:
    """Cancelled when the plan is stopped, or when a lecture is queued (it goes first): ``is_set`` is all the engine and
    the downloads ask."""

    def __init__(self, cancel: threading.Event, lectures: Callable[[], bool]) -> None:
        self.cancel, self.lectures = cancel, lectures

    def is_set(self) -> bool:
        return self.cancel.is_set() or self.lectures()


Progress = Callable[[int, int, str, float], None]      # (which, of how many, step "download" | "model" | "transcribe", fraction)


def listen(jobs: Any, settings: Any, todo: list[tuple[str, Any, str]], used: dict[str, Any], wanted: tuple[str, ...],
           cancel: threading.Event, progress: Optional[Progress] = None) -> dict[str, dict[str, Any]]:
    """Listens to the videos of ``todo`` - (video id, its VideoInfo, its captions' problem), most important first - as
    far as the plan's limits allow. ``used``: what the plan listened to before (videos, sound_s, wall_s; updated).
    Returns {video id: {"heard": True, "model", "device", "seconds"} or {"skipped": reason code}}; a video heard
    before (videos.heard) costs nothing. Raises Cancelled when the plan is stopped."""
    from . import videos

    out: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    todo = [x for x in todo if not (x[0] in seen or seen.add(x[0]))]
    if not todo:
        return out
    for k in ("videos", "sound_s", "wall_s"):
        used.setdefault(k, 0)
    lectures = (lambda: bool(jobs.busy())) if jobs is not None else (lambda: False)
    either = _Either(cancel, lectures)
    model, device, on, why = speech_choice(settings) if jobs is not None else ("", "", "", "no_model")
    sound_cap = MAX_SOUND_CPU_S if on == "cpu" else MAX_SOUND_S
    upto = min(LISTEN_S, LISTEN_CPU_S) if on == "cpu" else LISTEN_S     # of each video, at most its first this
    stack: Optional[Any] = None                  # the borrowed engine, once a video needs it
    engine: Any = None
    started = time.monotonic()
    folder = app_dir() / "cache" / "listen"
    try:
        for i, (vid, info, problem) in enumerate(todo, 1):
            if cancel.is_set():
                raise Cancelled()
            if videos.heard(vid) is not None:
                out[vid] = {"heard": True, "cached": True}
                continue
            dur = float(getattr(info, "duration_s", 0) or 0)
            part = min(dur, upto) if dur else upto
            skip = (why or ("too_long" if dur > MAX_MINUTES * 60 else "")
                    or ("limit" if used["videos"] >= MAX_VIDEOS else "")
                    or ("time" if used["wall_s"] + (time.monotonic() - started) >= MAX_WALL_S
                        or used["sound_s"] + part > sound_cap else "")
                    or ("lectures" if lectures() else ""))
            if skip:
                out[vid] = {"skipped": skip}
                if skip == "lectures":
                    why = "lectures"                     # the rest wait for nothing either
                continue
            t0 = time.monotonic()

            def step(name: str, frac: float, i: int = i) -> None:
                if progress is not None:
                    progress(i, len(todo), name, frac)

            step("download", 0.0)
            try:
                path = videos.download_audio(vid, folder, cancel=either,
                                             progress=lambda d, n: step("download", d / n if n else 0.0))
            except videos.VideoError as exc:
                if cancel.is_set():
                    raise Cancelled() from exc
                if exc.kind == "cancelled":                 # a lecture was queued
                    out[vid] = {"skipped": "lectures"}
                    why = "lectures"
                    continue
                out[vid] = {"skipped": "refused", "error": str(exc)}
                if exc.kind in videos.BLOCKING_KINDS:      # YouTube refuses this connection: no more downloads now
                    why = "refused"
                continue
            try:
                samples = _decode(path, either, upto)
            except Exception as exc:  # noqa: BLE001 - a damaged or empty download
                if cancel.is_set():
                    raise Cancelled() from exc
                log.info("Couldn't decode the sound of %s: %s", vid, exc)
                out[vid] = {"skipped": "lectures" if lectures() else "failed"}
                continue
            finally:
                _unlink(path)
            if engine is None:
                step("model", 0.0)
                try:
                    stack = jobs.lend_engine(model, device)
                    engine = stack.__enter__()
                    engine.ensure_model(lambda st, frac, d: step("model", frac), either)
                except Exception as exc:  # noqa: BLE001 - lectures came first, or the model doesn't load
                    if stack is not None and engine is not None:
                        stack.__exit__(None, None, None)
                    stack = engine = None
                    if cancel.is_set():
                        raise Cancelled() from exc
                    why = "lectures" if lectures() else str(getattr(exc, "why", "") or "no_model")
                    log.info("No speech model to listen with: %s", exc)
                    out[vid] = {"skipped": why}
                    continue
            step("transcribe", 0.0)
            try:
                tr = _transcribe(engine, samples, either, languages_for(info, wanted),
                                 lambda st, frac, d: step("transcribe", frac))
            except Cancelled:
                if cancel.is_set():
                    raise
                out[vid] = {"skipped": "lectures"}
                why = "lectures"
                continue
            except Exception as exc:  # noqa: BLE001 - e.g. quality.NoSpeech, or the card ran out of memory
                if cancel.is_set():
                    raise Cancelled() from exc
                silent = exc.__class__.__name__ == "NoSpeech"
                (log.info if silent else log.warning)("Listening to %s failed: %s", vid, exc)
                out[vid] = {"skipped": "no_speech" if silent else "failed"}
                continue
            took = time.monotonic() - t0
            cues = [videos.Cue(s.start, s.end, " ".join(s.text.split())) for s in tr.segments
                    if s.text and s.text != "[unclear]"]
            if sum(len(c.text.split()) for c in cues) < 20:
                out[vid] = {"skipped": "no_speech"}
                continue
            lang = max(tr.language_seconds, key=tr.language_seconds.get) if tr.language_seconds else ""
            where = "cuda" if str(getattr(engine, "device", "") or tr.device).startswith("cuda") else "cpu"
            videos.remember_heard(vid, cues, lang or languages_for(info, wanted)[0], model=tr.model or model,
                                  device=where, why=problem, seconds=round(took, 1),
                                  upto=upto if dur > upto else 0)
            used["videos"] += 1
            used["sound_s"] += round(min(dur, upto) if dur else tr.duration_s, 1)
            out[vid] = {"heard": True, "model": tr.model or model, "device": where, "seconds": round(took, 1)}
            log.info("Listened to %s (%.0f s of sound) in %.1f s with %s on %s", vid, dur, took, model, where)
    finally:
        used["wall_s"] = round(used.get("wall_s", 0) + time.monotonic() - started, 1)
        if stack is not None:
            stack.__exit__(None, None, None)
        shutil.rmtree(folder, ignore_errors=True)
    return out


def _decode(path: Any, cancel: Any, upto: float = LISTEN_S) -> Any:
    from .audio import decode_audio

    return decode_audio(path, cancel, max_s=upto)


def _transcribe(engine: Any, samples: Any, cancel: Any, langs: tuple[str, ...], progress: Callable) -> Any:
    """One video's sound through the speech model, on the graphics card's own priority when it runs there (the app
    runs below the others while it works: see winutil.drive_card)."""
    on_card = getattr(engine, "device", "") == "cuda"
    me = threading.get_native_id()
    if on_card:
        _drive([me], True)
    try:
        return engine.transcribe(samples, progress, cancel, {}, languages=langs)
    finally:
        if on_card:
            _drive([me], False)


def _drive(ids: list[int], on: bool) -> None:
    try:
        from .osutil import drive_card

        drive_card(ids, on)
    except Exception:  # noqa: BLE001 - only a priority
        pass


def _unlink(path: Any) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
