"""Local transcription engine (faster-whisper / CTranslate2) with English-Turkish code-switching."""

from __future__ import annotations

import dataclasses as dc
import datetime as dt
import logging
import os
import sys
import threading
import time
from collections import Counter
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, Optional, Sequence

import numpy as np

from .. import __version__
from ..cancel import Cancelled, check
from ..domain import Segment, Transcript
from ..i18n import num, t, tn
from ..nativeload import LOAD_LOCK, import_onnxruntime
from ..quality import NoSpeech, analyze_audio, analyze_transcript
from .core import (SR, DecodeOptions, Decoded, Unit, UnitDecoder, ai_writing, choose, clean_text,
                   hallucination_score, segment_language, sort_by_duration, speech_units, wait_for_ai)
from .models import (MODEL_PRESETS, delete_model, detect_gpu, download_model, local_model_path,
                     model_is_downloaded, resolve_device, setup_cuda_dlls)

__all__ = ["Engine", "Cancelled", "NoSpeech", "MODEL_PRESETS", "detect_gpu", "model_is_downloaded",
           "delete_model", "ENGINE_VERSION", "Speech", "find_speech"]

ENGINE_VERSION = "2.1"              # 2.1: numbers read out are kept whole (2.0's cleanup cut "0, 0, 0" to "0")
UNCLEAR = "[unclear]"
MIN_CONF = 0.38                     # below this in both languages a passage makes no sense: it becomes [unclear]
Progress = Callable[[str, float, str], None]


def _work(u: Unit) -> float:
    """A passage's share of the transcription time (see _decode_units)."""
    return 1.0 + u.dur / 12.0
log = logging.getLogger("studia")


def cpu_threads() -> int:
    """CTranslate2's threads on the processor: all but two logical processors, at most 8. On a 20-thread laptop i7,
    18 threads were slower than 8 while the PC was in use and took ~45 % more processor time; the transcripts are the
    same (checked on four lectures). NumPy's own threads are left alone: one thread for the features changed them."""
    return max(1, min((os.cpu_count() or 4) - 2, 8))


# The threads that drive the graphics card run at a normal program's priority while the rest of the app runs below it
# (winutil.drive_card): the card no longer waits whenever other programs keep the processor busy.
RAISE_CARD_THREADS = True
# CTranslate2's workers on the graphics card. With 2, two lectures' batches could run side by side instead of one after
# the other (see jobs.GPU_WORKERS), but each worker needs a batch's working memory of its own, and running out of it
# changes the batches (and the transcripts). One: on the owner's 8 GB RTX 4070 Laptop two (with two lectures at once)
# filled the card to 7.9 GB and made a Full sync of three 16-minute lectures take twice as long (248 s against 124 s).
CARD_REPLICAS = 1


def _drive_card_here() -> None:
    """This thread prepares the graphics card's next batch: it runs as the card's own threads do (see drive_card)."""
    if RAISE_CARD_THREADS:
        from ..osutil import drive_card

        drive_card([threading.get_native_id()])


def _ai_on_gpu() -> bool:
    """The offline AI model is loaded on the graphics card (it shares the card's memory with the speech model)."""
    ai = sys.modules.get(f"{__package__.rpartition('.')[0]}.local_llm")      # never imported: no model loaded either
    if ai is None:
        return False
    from ..components import LLM_PRESETS

    return any(ai.device_of(k) == "cuda" for k in LLM_PRESETS)


@dc.dataclass
class Speech:
    """Where the speech is in a recording, and what the audio checks found (see find_speech)."""
    spans: list[tuple[int, int]]
    units: list[Unit]
    warnings: list[dict[str, Any]]
    stats: dict[str, Any]
    gain: float
    seconds: float                   # how long finding it took


def _units(audio: np.ndarray, cancel: Optional[threading.Event]) -> tuple[list[tuple[int, int]], list[Unit]]:
    from faster_whisper.vad import VadOptions

    from .vad import get_speech_timestamps

    if len(audio) < SR // 2:
        return [], []
    check(cancel)
    opts = VadOptions(threshold=0.5, min_speech_duration_ms=200, min_silence_duration_ms=300,
                      speech_pad_ms=200, max_speech_duration_s=14.0)
    speech = get_speech_timestamps(audio, opts)
    check(cancel)
    return [(int(s["start"]), int(s["end"])) for s in speech], speech_units(speech)


def find_speech(audio: np.ndarray, cancel: Optional[threading.Event] = None, expected_duration: float = 0.0) -> Speech:
    """Find the speech (Silero VAD, on one processor thread) and check the audio. It needs no model: the download
    threads run it while the lecture before is transcribed. Raises quality.NoSpeech for a recording without speech."""
    t0 = time.monotonic()
    spans, units = _units(audio, cancel)
    took = time.monotonic() - t0
    log.info("%d speech units, %.0fs of speech in %.0fs of audio", len(units), sum(u.dur for u in units),
             len(audio) / SR)
    # safeguards: raises quality.NoSpeech for silent recordings, boosts quiet ones
    warnings, stats, gain = analyze_audio(audio, spans, expected_duration)
    return Speech(spans, units, warnings, stats, gain, took)


class Engine:
    """Load a Whisper model once and transcribe many recordings with it."""

    def __init__(self, model: str = "large-v3-turbo", device: str = "auto",
                 languages: Sequence[str] = ("en", "tr"), log: Optional[logging.Logger] = None) -> None:
        if model not in MODEL_PRESETS:
            raise ValueError(t("Unknown model '{model}'.", model=model))
        self.model_name = model
        self.device_pref = device
        self.languages = tuple(dict.fromkeys(l.strip().lower() for l in languages if l.strip())) or ("en",)
        self.log = log or logging.getLogger("studia")
        self._wm = None
        self._decoder: Optional[UnitDecoder] = None
        self._device = "cpu"
        self._compute = "int8"
        self._warning = ""
        self.gpu_error = ""                  # why the graphics card couldn't load the model ("": it could, or not tried)
        self._lock = threading.Lock()
        self._last_dominant = "en"
        self._mine = threading.local()       # per transcribing thread (two lectures may share the model on the card)
        self._oom_batch = 0
        self.card_threads: set[int] = set()  # the native threads that drive the graphics card (see drive_card)

    # ------------------------------------------------------------------ model lifecycle
    @property
    def loaded(self) -> bool:
        return self._wm is not None

    @property
    def resumed_passages(self) -> int:
        """How many passages of this thread's last lecture came from its checkpoint (not transcribed by this run)."""
        return getattr(self._mine, "resumed", 0)

    @property
    def device(self) -> str:
        """Where the loaded model runs ("cuda" or "cpu"); "" before it is loaded."""
        return self._device if self._wm is not None else ""

    @property
    def device_info(self) -> dict[str, Any]:
        gpu = detect_gpu()
        return {"device": self._device, "compute_type": self._compute,
                "gpu": gpu.get("name", "") if self._device == "cuda" else "", "warning": self._warning}

    def ensure_model(self, progress: Progress, cancel: Optional[threading.Event] = None) -> None:
        with self._lock:
            if self._wm is not None:
                return
            path = local_model_path(self.model_name)
            if path is None:
                size = MODEL_PRESETS[self.model_name]["size_mb"]
                progress("model-download", 0.0, t("Downloading the {model} model (~{size} MB, one time)",
                                                  model=self.model_name, size=size))

                def cb(done: int, total: int) -> None:
                    frac = done / total if total else 0.0
                    progress("model-download", frac,
                             t("Downloading model {done} / {total} MB", done=num(done / 1e6), total=num(total / 1e6)))

                path = download_model(self.model_name, cb, cancel)
            check(cancel)
            progress("model-load", 0.0, t("Loading the speech model"))
            # the speech search's onnxruntime first, then the card's libraries: the two loading at the same time held
            # up the whole app (nativeload)
            import_onnxruntime()
            with LOAD_LOCK:
                setup_cuda_dlls()
                from faster_whisper import WhisperModel

                from ..osutil import drive_card, native_thread_ids

                device, compute = resolve_device(self.device_pref)
                threads = 1 if device == "cuda" else cpu_threads()   # on the card, more threads only spin waiting
                t0 = time.monotonic()
                before = native_thread_ids() if device == "cuda" and RAISE_CARD_THREADS else set()
                try:
                    wm = WhisperModel(str(path), device=device, compute_type=compute, cpu_threads=threads,
                                      num_workers=CARD_REPLICAS if device == "cuda" else 1)
                    if device == "cuda":  # surface missing cuBLAS/cuDNN now rather than mid-job
                        wm.encode(np.zeros((1, wm.model.n_mels, 3000), dtype=np.float32))
                        if before:
                            # CTranslate2's worker (and the driver's threads), started by this load: Python's own
                            # threads started meanwhile (a download's parts) are not among them
                            self.card_threads = (native_thread_ids() - before
                                                 - {t.native_id for t in threading.enumerate()})
                            drive_card(self.card_threads)
                except Exception as exc:  # noqa: BLE001
                    if device != "cuda":
                        raise
                    self.log.warning("GPU initialisation failed, falling back to CPU: %s", exc)
                    self.gpu_error = str(exc) or exc.__class__.__name__     # (jobs.py remembers a card that can't)
                    self._warning = t("The GPU could not be used, so transcription runs on the CPU (slower).")
                    device, compute = "cpu", "int8"
                    drive_card(self.card_threads, False)
                    self.card_threads = set()
                    wm = WhisperModel(str(path), device=device, compute_type=compute, cpu_threads=cpu_threads())
            self._wm, self._device, self._compute = wm, device, compute
            self._oom_batch = 0              # a batch that ran out of GPU memory stays smaller until the model unloads
            self._decoder = UnitDecoder(wm)
            supported = set(getattr(wm, "supported_languages", []) or [])
            if supported:
                self.languages = tuple(l for l in self.languages if l in supported) or ("en",)
            self.log.info("Model %s loaded on %s/%s in %.1fs (%d threads drive the card)", self.model_name, device,
                          compute, time.monotonic() - t0, len(self.card_threads))
            progress("model-load", 1.0, t("Model ready (GPU)") if device == "cuda" else t("Model ready (CPU)"))

    def unload(self) -> None:
        """Release the model (and its GPU memory)."""
        with self._lock:
            if self._wm is not None:
                try:
                    self._wm.model.unload_model()
                except Exception:  # noqa: BLE001 - dropping the reference frees it anyway
                    pass
            self._wm = None
            self._decoder = None
            self._oom_batch = 0
            self.card_threads = set()

    find_speech = staticmethod(find_speech)

    # ------------------------------------------------------------------ transcription
    def transcribe(self, audio: np.ndarray, progress: Progress, cancel: Optional[threading.Event],
                   meta: Optional[dict[str, Any]] = None, speech: Optional[Speech] = None,
                   languages: Optional[Sequence[str]] = None) -> Transcript:
        """``speech``: find_speech's result for this audio, when a download thread found it already. ``languages``:
        the languages to decode this recording in (of those the model knows), instead of the engine's own - e.g. one
        for a YouTube video spoken in it (a study plan listening to it): each one more costs a decoding pass."""
        if self._wm is None or self._decoder is None:
            self.ensure_model(progress, cancel)
        assert self._wm is not None and self._decoder is not None
        langs = tuple(dict.fromkeys(x for x in (languages or ()) if x in self.languages
                                    or x in (getattr(self._wm, "supported_languages", None) or ())))
        self._mine.languages = langs or self.languages
        meta = dict(meta or {})
        timings: dict[str, float] = {}
        duration = len(audio) / SR

        if speech is None:
            progress("vad", 0.02, t("Finding speech"))
            speech = find_speech(audio, cancel, float(meta.get("expected_duration") or 0.0))
        timings["vad"] = speech.seconds
        units, warnings, stats = speech.units, speech.warnings, speech.stats
        speech_s = sum(u.dur for u in units)
        if speech.gain != 1.0:
            audio = np.clip(audio * speech.gain, -1.0, 1.0)

        t0 = time.monotonic()
        resume = self._checkpoint(str(meta.get("delivery_id") or ""), audio, units, speech.gain)
        decoded, best_conf = self._decode_units(audio, units, progress, cancel, resume)
        timings["decode"] = time.monotonic() - t0

        t0 = time.monotonic()
        progress("finalize", 0.97, t("Cleaning up"))
        segments: list[Segment] = []
        lang_secs: Counter[str] = Counter()
        dominant = getattr(self._mine, "dominant", self._last_dominant)     # this lecture's (see _decode_units)

        def unclear(u: Unit, conf: float) -> None:
            if segments and segments[-1].text == UNCLEAR and u.start / SR - segments[-1].end < 3.0:
                segments[-1].end = round(u.end / SR, 2)
                return
            segments.append(Segment(start=round(u.start / SR, 2), end=round(u.end / SR, 2), text=UNCLEAR,
                                    lang=dominant, conf=round(conf, 3)))

        for u, d, top in zip(units, decoded, best_conf):
            if d is None:
                continue
            if hallucination_score(d, u.dur) >= 0.75:
                if u.dur >= 4.0 and top < 0.45:
                    unclear(u, top)
                continue
            if top < MIN_CONF:  # neither language makes sense of it: say so instead of inventing words
                if u.dur >= 3.0:
                    unclear(u, top)
                continue
            text = clean_text(d.text)
            if not text:
                continue
            lang = segment_language(d)
            segments.append(Segment(start=round(u.start / SR, 2), end=round(u.end / SR, 2), text=text,
                                    lang=lang, conf=round(d.conf, 3)))
            lang_secs[lang] += u.dur
        post_warnings, post_stats = analyze_transcript(segments, speech_s)
        meta["quality"] = {"warnings": warnings + post_warnings, "stats": {**stats, **post_stats}}
        timings["finalize"] = time.monotonic() - t0
        progress("finalize", 1.0, t("Done"))
        if resume is not None:            # the transcript is whole: nothing to go on from
            resume.discard()
        info = self.device_info
        return Transcript(
            delivery_id=str(meta.get("delivery_id", "")),
            segments=segments,
            duration_s=round(duration, 2),
            speech_s=round(speech_s, 2),
            language_seconds={k: round(v, 1) for k, v in lang_secs.most_common()},
            model=self.model_name,
            device=info["device"] + (f" ({info['gpu']})" if info.get("gpu") else ""),
            engine_version=ENGINE_VERSION,
            created_at=dt.datetime.now().isoformat(timespec="seconds"),
            timings={k: round(v, 2) for k, v in timings.items()},
            meta=meta,
        )

    # ------------------------------------------------------------------ internals
    def _languages(self) -> tuple[str, ...]:
        """The languages this thread's recording is decoded in (see transcribe)."""
        return getattr(self._mine, "languages", None) or self.languages

    def _batch_size(self) -> int:
        if self._device != "cuda":
            return 4
        vram = detect_gpu().get("vram_mb") or 8000
        if self.model_name in ("large-v3", "medium"):
            # 8 spreads the cost of each decoding step (large-v3 then needs ~6.2 GB): not next to the offline AI
            return (4 if _ai_on_gpu() else 8) if vram >= 7500 else 2
        if self.model_name == "large-v3-turbo" and vram < 5000:
            return 4                     # batch 8 needs ~3.4 GB: more than a 4 GB card has to spare
        return 16 if vram >= 7500 else 8

    def _encode_generate(self, audio: np.ndarray, batch: list[Unit], langs_per_pass: list[list[str]],
                         opts: DecodeOptions, feats: Optional[np.ndarray] = None,
                         priors: Optional[list] = None) -> list[list[Decoded]]:
        """Encode once, generate once per language pass. Halves the batch on CUDA out-of-memory. ``feats``: the
        batch's features, when they were computed ahead. ``priors``: gets the language-ID head's prior of each
        passage (UnitDecoder.language_prior) appended."""
        dec = self._decoder
        assert dec is not None
        try:
            enc = dec.encode(dec.features(audio, batch) if feats is None else feats)
            lid = dec.language_prior(enc, self._languages()) if priors is not None else []
            passes = [dec.generate(enc, batch, langs, opts) for langs in langs_per_pass]
        except RuntimeError as exc:
            if "out of memory" not in str(exc).lower() or len(batch) == 1:
                raise
            half = len(batch) // 2
            self.log.warning("GPU out of memory with %d passages per batch; retrying with %d", len(batch), half)
            self._oom_batch = half
            a = self._encode_generate(audio, batch[:half], [l[:half] for l in langs_per_pass], opts, priors=priors)
            b = self._encode_generate(audio, batch[half:], [l[half:] for l in langs_per_pass], opts, priors=priors)
            return [x + y for x, y in zip(a, b)]
        if priors is not None:
            priors.extend(lid)
        return passes

    def _make_way_for_ai(self, frac: float, progress: Progress, cancel: Optional[threading.Event]) -> float:
        """Before a batch: while the offline AI writes on the processor, it goes first. The seconds waited."""
        if self._device == "cuda" or not ai_writing():
            return 0.0
        progress("transcribe", frac, t("Waiting while the offline AI writes"))
        return wait_for_ai(cancel)

    def _checkpoint(self, delivery_id: str, audio: np.ndarray, units: list[Unit], gain: float) -> Optional[Any]:
        """The lecture's checkpoint (engine/checkpoint.py), keyed by everything that changes its passages; the
        decoding options join the key in _decode_units. None without a recording to tie it to."""
        if not delivery_id or not units:
            return None
        from . import checkpoint

        try:
            t0 = time.monotonic()
            ct2 = sys.modules.get("ctranslate2")
            key = checkpoint.make_key(
                v=checkpoint.VERSION, id=delivery_id, audio=checkpoint.audio_digest(audio), gain=gain,
                units=[(u.start, u.end) for u in units], model=self.model_name, engine=ENGINE_VERSION, app=__version__,
                device=self._device, compute=self._compute, languages=list(self._languages()),
                card_workers=CARD_REPLICAS if self._device == "cuda" else 1,     # (how the card's batches run)
                ct2=getattr(ct2, "__version__", ""),       # the processor's threads, or the graphics card itself
                where=detect_gpu().get("name", "") if self._device == "cuda" else cpu_threads())
            self.log.debug("Checkpoint key made in %.2fs", time.monotonic() - t0)
            return checkpoint.Checkpoint(delivery_id, key)
        except Exception:  # noqa: BLE001 - only a way to go on later
            self.log.info("No checkpoint for %s", delivery_id, exc_info=True)
            return None

    def _decode_units(self, audio: np.ndarray, units: list[Unit], progress: Progress,
                      cancel: Optional[threading.Event], resume: Optional[Any] = None
                      ) -> tuple[list[Optional[Decoded]], list[float]]:
        """``resume``: the lecture's checkpoint (see _checkpoint): batches done before are taken from it, and each
        batch done now is added to it."""
        n = len(units)
        self._mine.resumed = 0
        if n == 0:
            return [], []
        langs = list(self._languages())
        opts = DecodeOptions(batch_size=self._batch_size(), beam_size=5 if self._device == "cuda" else 2)
        cands: list[list[Decoded]] = [[] for _ in range(n)]
        # large-v3-turbo's language-ID head, read off the encoder pass it already made (~0.3 s per 15 minutes on the
        # graphics card), helps choose the language: on 4 real lecture windows, English passages tagged Turkish fell
        # from 9 to 5 and the word error rate from 13.3 to 12.8 %. The other models' language-ID hears Turkish in a
        # Turkish lecturer's English: any weight made them worse.
        lid = self.model_name == "large-v3-turbo" and len(langs) > 1
        priors: list[Optional[dict[str, float]]] = [None] * n
        order = sort_by_duration(units, range(n))
        done = 0
        started = time.monotonic()
        # what a passage costs: one encoder pass (always 30 s of audio, however short the passage), plus decoding
        # that grows with its length (measured on a laptop processor: encoding is about 12 s of decoding)
        work_total = sum(_work(u) for u in units) or 1.0
        work_done = 0.0
        i = 0
        if resume is not None:
            from .checkpoint import make_key

            resume.key = make_key(base=resume.key, opts=dc.asdict(opts), lid=lid)
            kept = []
            for idx, got_c, got_p, size in resume.load():
                if idx != order[i:i + len(idx)] or len(got_c) != len(idx) or len(got_p) != len(idx):
                    break                         # not this lecture's batches after all: from here on, again
                for j, c, p in zip(idx, got_c, got_p):
                    cands[j], priors[j] = list(c), p
                kept.append((idx, got_c, got_p, size))
                i += len(idx)
                work_done += sum(_work(units[j]) for j in idx)
                # an earlier run out of memory on the graphics card made its batches smaller: the passages left are
                # batched as they were then (only ever smaller: another lecture may have run out of memory since)
                if size < opts.batch_size:
                    self._oom_batch = min(self._oom_batch or size, size)
            resume.start(kept)
            done = self._mine.resumed = i
            if i:
                self.log.info("Going on from a checkpoint: %d of %d passages were done", i, n)
                progress("transcribe", 0.05 + 0.88 * work_done / work_total,
                         t("Going on where it stopped: passage {done} of {n}", done=done, n=n))
        work_before = work_done            # the time left counts the speed of this run only
        # on the graphics card the processor computes the next batch's features while this batch runs (on the
        # processor itself that would only compete with CTranslate2's threads)
        ahead = (ThreadPoolExecutor(1, thread_name_prefix="features", initializer=_drive_card_here)
                 if self._device == "cuda" else None)
        nxt: Optional[tuple[list[int], Future]] = None
        try:
            while i < n:
                check(cancel)
                # the offline AI writing on the processor goes first (the time left leaves the wait out)
                started += self._make_way_for_ai(0.05 + 0.88 * work_done / work_total, progress, cancel)
                size = min(opts.batch_size, self._oom_batch or opts.batch_size)
                idx = order[i:i + size]
                batch = [units[j] for j in idx]
                # computed ahead for this very batch? (an out-of-memory split changes the batches after it)
                feats = nxt[1].result() if nxt is not None and nxt[0] == idx else None
                nxt = None
                if ahead is not None and i + size < n:
                    nidx = order[i + size:i + 2 * size]
                    nxt = (nidx, ahead.submit(self._decoder.features, audio, [units[j] for j in nidx]))
                got: Optional[list] = [] if lid else None
                passes = self._encode_generate(audio, batch, [[lang] * len(batch) for lang in langs], opts, feats,
                                               priors=got)
                for results in passes:
                    for j, d in zip(idx, results):
                        cands[j].append(d)
                for j, p in zip(idx, got or []):
                    priors[j] = p
                if resume is not None:            # with the size the next batch has (smaller after out of memory)
                    resume.add(idx, [cands[j] for j in idx], [priors[j] for j in idx],
                               min(opts.batch_size, self._oom_batch or opts.batch_size))
                i += len(idx)
                done += len(idx)
                work_done += sum(_work(u) for u in batch)
                frac = work_done / work_total
                elapsed = time.monotonic() - started
                ran = (work_done - work_before) / max(1e-9, work_total - work_before)
                eta = elapsed / ran * (1 - ran) if ran > 0.05 else None
                # the time left is an untranslated marker that jobs.py takes off (the page says it in its language)
                progress("transcribe", 0.05 + 0.88 * frac, t("Transcribing passage {done} of {n}", done=done, n=n)
                         + (f" · ~{int(eta)}s left" if eta else ""))
        finally:
            if ahead is not None:
                ahead.shutdown(wait=True, cancel_futures=True)
            if resume is not None:            # cancelled or failed: what is done stays for the next run
                resume.close()

        dominant = self._dominant(cands, units)
        self._last_dominant = self._mine.dominant = dominant
        chosen: list[Optional[Decoded]] = [choose(c, units[k].dur, dominant, priors[k]) if c else None
                                           for k, c in enumerate(cands)]
        best_conf = [max((d.conf for d in c if hallucination_score(d, units[k].dur) < 0.75), default=0.0)
                     for k, c in enumerate(cands)]
        chosen = self._retry_bad(audio, units, chosen, opts, cancel, progress, best_conf)
        return chosen, best_conf

    @staticmethod
    def _dominant(cands: list[list[Decoded]], units: list[Unit]) -> str:
        votes: Counter[str] = Counter()
        for c, u in zip(cands, units):
            if len(c) > 1:
                best = max(c, key=lambda d: d.avg_logprob)
                second = sorted(d.avg_logprob for d in c)[-2]
                if best.avg_logprob - second > 0.05:
                    votes[best.lang] += u.dur
            elif c:
                votes[c[0].lang] += u.dur
        return votes.most_common(1)[0][0] if votes else "en"

    def _retry_bad(self, audio: np.ndarray, units: list[Unit], chosen: list[Optional[Decoded]],
                   opts: DecodeOptions, cancel: Optional[threading.Event],
                   progress: Progress = lambda *a: None,
                   best_conf: Optional[list[float]] = None) -> list[Optional[Decoded]]:
        """Re-decode units with repetition loops / very low confidence using temperature fallback. A passage below
        MIN_CONF in both languages (``best_conf``) is not retried: it becomes [unclear] whatever a retry finds, and on
        the processor every retry costs another encoder pass."""
        dec = self._decoder
        assert dec is not None
        bad = [k for k, d in enumerate(chosen)
               if d is not None and d.text and (d.compression_ratio > 2.4 or d.avg_logprob < -1.0)
               and hallucination_score(d, units[k].dur) < 0.75 and (best_conf is None or best_conf[k] >= MIN_CONF)]
        if not bad:
            return chosen
        self.log.info("Retrying %d low-quality passages", len(bad))
        size = max(1, min(opts.batch_size, self._oom_batch or opts.batch_size))
        first = len(bad)
        for round_no, temp in enumerate((0.3, 0.6)):
            if not bad:
                break
            still = []
            by_lang: dict[str, list[int]] = {}
            for k in bad:
                by_lang.setdefault(chosen[k].lang, []).append(k)  # type: ignore[union-attr]
            groups = [ks[i:i + size] for ks in by_lang.values() for i in range(0, len(ks), size)]
            for g, idx in enumerate(groups):
                check(cancel)
                frac = 0.93 + 0.02 * (round_no + g / len(groups)) / 2
                self._make_way_for_ai(frac, progress, cancel)          # a retry is a batch too: the AI goes first
                progress("transcribe", frac,
                         tn(first, "Checking {n} unclear passage again", "Checking {n} unclear passages again"))
                self._make_way_for_ai(frac, progress, cancel)          # again: a pause just ended may find the AI writing
                batch = [units[k] for k in idx]
                langs = [chosen[k].lang for k in idx]  # type: ignore[union-attr]
                retry = self._encode_generate(audio, batch, [langs], DecodeOptions(temperature=temp, best_of=3))[0]
                for k, d in zip(idx, retry):
                    old = chosen[k]
                    assert old is not None
                    better = d.text and d.compression_ratio <= 2.4 and d.avg_logprob > old.avg_logprob
                    if better:
                        chosen[k] = d
                    if chosen[k].compression_ratio > 2.4 or chosen[k].avg_logprob < -1.0:  # type: ignore[union-attr]
                        still.append(k)
            bad = still
        return chosen
