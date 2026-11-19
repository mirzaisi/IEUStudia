"""Transcription jobs: downloads run in parallel, transcription on the GPU worker (a second one shares the loaded
model when the PC allows it), slides alongside, and a finishing thread writes the files while the next lecture runs.

Pipeline per recording:  Panopto details → audio download → decode (CPU) ──► GPU: transcribe
                                         └► screen keyframes → slides (parallel) ─┘──► write files

Order: what you ask for now (a class day, Get ready, selected lectures) goes before the batch of a full or course
sync, and "Do next" puts a lecture before everything still waiting. An extra download lane lets such a lecture
overtake the batch lectures that are already downloaded and waiting for their turn.
"""

from __future__ import annotations

import contextlib
import dataclasses as dc
import datetime as dt
import errno
import hashlib
import heapq
import itertools
import json
import logging
import os
import queue
import re
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterator, Optional

import requests

from . import hls, local_llm, net, store
from .cancel import Cancelled
from .config import Settings
from .i18n import date_short, pct, t
from .ieulearn import AuthError, NeedLogin
from .library import Library
from .net import plain_error
from .panopto import NeedPanoptoAuth, NotReady, Panopto
from .paths import audio_cache_dir
from .quality import SILENT_DBFS, NoSpeech, is_silent
from .osutil import KeepAwake, set_low_priority

if TYPE_CHECKING:
    import numpy as np

log = logging.getLogger("studia")


def decode_audio(path: Path, cancel: Optional[threading.Event] = None) -> np.ndarray:
    """Decode with PyAV, loaded on first use: importing it takes seconds and the app starts without it."""
    from .audio import decode_audio as _decode

    return _decode(path, cancel)
Emit = Callable[[dict[str, Any]], None]
IDLE_UNLOAD_S = 600   # free the GPU memory 10 minutes after the last job
DECODED_MAX = 2       # decoded lectures in memory at once (~345 MB each for 90 minutes): one transcribing, one next
LOW_MEMORY_GB = 6.0   # below this (e.g. 4 GB laptops) only one
MORE_MEMORY_GB = 12.0  # from this (16 GB PCs) one more: a second lecture on the graphics card, or one more ready
# With 2, two lectures are transcribed at once on a graphics card with its one model (8 GB doesn't hold two large-v3):
# the card works through one lecture's batch while the processor prepares the other's. The card does one batch at a
# time, so the transcripts are the same, and only with enough processors and free memory (see _may_share). One: on the
# owner's RTX 4070 Laptop (large-v3, three 16-minute lectures as a Full sync) one lecture at a time already kept the
# card's calls running 99 % of the time, and two were no faster (121-134 s against 122-124 s), took up to ~700 MB more
# of the card, and finished the newest lecture twice as late (80-91 s against 44 s: it shared the card with the next).
GPU_WORKERS = 1
SHARE_MIN_CPUS = 8    # logical processors
SHARE_FREE_GB = 2.0   # free memory for the second lecture's audio (~345 MB for 90 minutes) and its features
PENDING_KEY = "outputs_pending"     # library meta: recordings whose files in the output folder could not be written
QUEUE_KEY = "transcribe_queue"      # library meta: the lectures still to transcribe, offered again after a restart
NOW, BATCH = 0, 1                   # queue ranks: asked for now, a full or course sync's batch ("Do next" goes below 0)
_ETA = re.compile(r"\s*·\s*~(\d+)s left")      # the engine's time left: an untranslated marker (the page says it)
_PASSAGES = re.compile(r"^\D*(\d+)\D+(\d+)\D*$")  # "Transcribing passage 40 of 600" (in either language): done, of

_STATUS = {"queued": "queued", "downloading": "downloading", "transcribing": "transcribing",
           "writing": "transcribing", "done": "done", "failed": "failed", "processing": "processing"}
# what a lecture is doing, finer than its stage (the jobs pane draws them as steps): waiting, downloading, waiting for
# a place to decode its audio ("place"), decoding, finding speech, waiting for the speech model ("turn"), loading the
# speech model, transcribing, finishing slides, writing files
_STEP = {"queued": "wait", "downloading": "download", "transcribing": "transcribe", "writing": "write"}


def _no_sound(exc: BaseException) -> bool:
    """Panopto has no stream with sound (audio.NoAudioTrack). That module is loaded once a stream was decoded; it is
    not imported just to ask (importing PyAV takes seconds)."""
    audio = sys.modules.get(f"{__package__}.audio")
    return audio is not None and isinstance(exc, audio.NoAudioTrack)


def _no_sound_error() -> Exception:
    from .audio import NoAudioTrack

    return NoAudioTrack(t("Panopto has no sound for this recording (only a picture), so there's nothing to transcribe."))


_CARD_FAILED = ("CUDA failed", "cuBLAS failed", "cuDNN failed")     # CTranslate2's words for the card's errors


def _card_stopped(engine: Any, exc: BaseException) -> bool:
    """The graphics card failed while the engine on it transcribed: an error of the card itself (a driver reset or
    update, "unspecified launch failure", "illegal memory access"), or out of memory with one passage at a time."""
    return (getattr(engine, "device", "") == "cuda" and isinstance(exc, RuntimeError)
            and str(exc).lstrip().startswith(_CARD_FAILED))


def _out_of_memory(exc: BaseException) -> bool:
    low = str(exc).lower()
    return "out of memory" in low or "alloc_failed" in low


def _card_stopped_text(exc: BaseException) -> str:
    if _out_of_memory(exc):
        return t("The graphics card ran out of memory while this lecture was transcribed: another program may be using "
                 "it. Close other programs (games, video editors), then transcribe the lecture again.")
    return t("The graphics card stopped working while this lecture was transcribed (a driver update or reset can do "
             "this). Close IEUStudia, open it again and transcribe the lecture again.")


class DamagedAudio(ValueError):
    """A downloaded recording's file can't be decoded, also when downloaded again; it is deleted (see _prepare)."""


def _damaged(exc: BaseException) -> bool:
    """Decoding a downloaded file failed on what is in it (FFmpeg's "Invalid data found when processing input", or
    audio.decode_audio's "the file seems to be damaged"), not on the disk, the memory or a cancel."""
    if isinstance(exc, (Cancelled, OSError, MemoryError)) or _no_sound(exc):
        return False
    av = sys.modules.get("av")
    return isinstance(exc, ValueError) or (av is not None and isinstance(exc, av.error.FFmpegError))


def _forget_download(dest: Path) -> None:
    """A downloaded file and what is left of its download (its parts and their marker, an HLS stream's segments so far
    and theirs): the next try starts afresh."""
    for f in [dest, *dest.parent.glob(dest.name + ".*")]:
        try:
            f.unlink(missing_ok=True)
        except OSError:
            pass


class ModelFailed(RuntimeError):
    """The speech model couldn't be downloaded or loaded for a lecture; the message says why and what to do."""


class EngineBusy(RuntimeError):
    """The speech model can't be lent now: lectures are transcribing or waiting (they go first: ``why`` "lectures"), or
    the setup is still downloading (``why`` "setup")."""

    def __init__(self, message: str, why: str = "lectures") -> None:
        super().__init__(message)
        self.why = why


def model_failure(phase: str, exc: BaseException) -> str:
    """What a lecture says when the speech model couldn't be downloaded (``phase`` "download") or loaded ("load")."""
    from .components import DownloadDamaged
    from .engine.models import ModelDamaged

    if isinstance(exc, (DownloadDamaged, ModelDamaged)):    # deleted: the next try downloads it afresh (it says so)
        return str(exc)
    why = plain_error(exc)
    if phase == "download":
        if why.rstrip(".") == str(exc).rstrip("."):          # the error's own words, not written for people:
            if isinstance(exc, requests.HTTPError):          # e.g. "expected HTTP 206, got 403" (a link that expired)
                why = t("The download server answered with an error.")
            elif isinstance(exc, (requests.RequestException, ConnectionError)):    # "Connection broken: ..."
                why = t("The connection broke off before the download was complete. Check your internet connection "
                        "(on campus Wi-Fi, make sure you are signed in to it).")
        return t("The speech model couldn't be downloaded. {error} What was downloaded is kept: transcribing the "
                 "lecture again goes on from there.", error=why)
    text = str(exc).lower()
    if isinstance(exc, MemoryError) or "bad_alloc" in text or "out of memory" in text:
        return t("There wasn't enough free memory to load the speech model. Close other programs (a browser with many "
                 "tabs uses a lot) or choose a smaller model in Settings → Transcription, then transcribe the lecture "
                 "again.")
    return t("The speech model couldn't be loaded: {error} Close IEUStudia, open it again and transcribe the lecture "
             "again. If it keeps happening, send the log (Settings → About → Logs) to the person who helps you with "
             "IEUStudia.", error=why)


def _to_int16(audio: np.ndarray) -> np.ndarray:
    """A decoded lecture waiting for its turn, in half the memory. The decoder's samples are whole numbers / 32768,
    so _to_float32 gives back the very same values. Scales ``audio`` in place: it is dropped right after."""
    import numpy as np

    if audio.dtype != np.float32:
        return audio
    audio *= 32768.0
    return audio.astype(np.int16)


def _to_float32(audio: np.ndarray) -> np.ndarray:
    """The lecture's samples for transcribing it, as the decoder made them (see _to_int16)."""
    import numpy as np

    if audio.dtype != np.int16:
        return audio
    out = audio.astype(np.float32)
    out *= 1.0 / 32768.0
    return out


def _memory_gb() -> float:
    from .hardware import memory_gb

    try:
        return memory_gb()[0] or LOW_MEMORY_GB
    except (AttributeError, OSError):
        return LOW_MEMORY_GB


def _free_memory_gb() -> float:
    from .hardware import memory_gb

    try:
        return memory_gb()[1]
    except (AttributeError, OSError):
        return 0.0


@dc.dataclass
class Job:
    id: str
    title: str
    course_id: str = ""
    course_code: str = ""
    local_path: Optional[Path] = None
    stage: str = "queued"
    progress: float = 0.0
    detail: str = ""
    error: str = ""
    cancel: threading.Event = dc.field(default_factory=threading.Event)
    audio: Optional[np.ndarray] = None   # decoded; as int16 while it waits for its turn (see _to_int16)
    speech: Any = None                   # where its speech is (the engine's find_speech), found by the download thread
    audio_path: Optional[Path] = None
    info: Optional[dict] = None
    slides: Optional[Future] = None
    attempts: int = 0
    holds_audio: bool = False        # one of the DECODED_MAX places for decoded audio
    no_sound: bool = False           # failed because the recording has no sound (the page shows it by this flag)
    no_audio: set = dc.field(default_factory=set)    # Panopto streams found without sound: not fetched again
    unwritten: bool = False          # done, but its files in the output folder could not be written yet
    prev_status: str = "available"
    started: float = dc.field(default_factory=time.monotonic)
    finished: bool = False
    duration_s: float = 0.0          # the recording's length (for the batch's time left)
    rank: int = NOW                  # lower goes first (see NOW, BATCH)
    seq: int = 0                     # order within a rank
    token: int = 0                   # its current place in a queue: an older entry for it is skipped
    where: str = ""                  # "dl" waiting to download, "prep" downloading, "gpu" ready, "run" transcribing
    paused_s: float = 0.0            # time paused in the middle of transcribing it (the engine's time left counts it)
    day_key: str = ""                # its class day (the page counts lectures, not their parts)
    step: str = ""                   # what it does now (see _STEP), and since when each step began (monotonic)
    steps: list = dc.field(default_factory=list)
    dl_done: int = 0                 # the audio download: bytes so far, of dl_total, at dl_speed bytes a second
    dl_total: int = 0
    dl_speed: float = 0.0
    dl_mark: tuple = (0.0, 0)        # (when, bytes) the speed was last measured from
    model: str = ""                  # the speech model it is transcribed with, and where ("cuda" or "cpu")
    device: str = ""
    notes: list = dc.field(default_factory=list)     # warnings in plain words (the GPU couldn't be used, quality)
    anomalies: list = dc.field(default_factory=list)  # something looks wrong with the result: [{"kind", "text"}]
    audio_s: float = 0.0             # the decoded audio's length
    passages: list = dc.field(default_factory=lambda: [0, 0])     # transcribed so far, of all its passages
    own_speed: bool = True           # its transcribing time is its own: not gone on from a checkpoint, the card unshared
    model_phase: str = ""            # what the speech model was doing for it last: "download" or "load" (see _ensure_model)
    slides_read: float = 0.0         # how much of its screen recording the slide capture has gone through (0 to 1)

    def spent(self, step: str) -> float:
        """Seconds it has spent in this step so far (all its times in it)."""
        now, out = time.monotonic(), 0.0
        for k, (s, at) in enumerate(self.steps):
            if s == step:
                out += (self.steps[k + 1][1] if k + 1 < len(self.steps) else now) - at
        return out

    def metrics(self) -> dict[str, Any]:
        """What the jobs pane shows of a finished lecture, besides its view (only the final event carries it)."""
        return {"audio_s": round(self.audio_s or self.duration_s, 1),
                "passages": list(self.passages) if self.passages[1] else None,
                # (no speed when this run did only part of the work, or the card also worked for another lecture)
                "transcribe_s": round(max(0.0, self.spent("transcribe") - self.paused_s), 1) if self.own_speed else None}

    def view(self) -> dict[str, Any]:
        detail, eta = self.detail, None
        m = _ETA.search(detail)              # the engine's "… · ~120s left": the page shows it as "about 2 min left"
        if m:
            detail, eta = (detail[:m.start()] + detail[m.end():]).strip(), int(m.group(1))
        return {"id": self.id, "title": self.title, "course_code": self.course_code, "course_id": self.course_id,
                "stage": self.stage, "progress": round(self.progress, 3), "detail": detail, "eta_s": eta,
                "error": self.error, "local": self.local_path is not None, "first": self.rank < NOW,
                "day": self.day_key or self.id, "no_sound": self.no_sound,
                # the jobs pane: its steps (seconds after it was queued), the download, the model, warnings
                "step": self.step, "steps": [[s, round(at - self.started, 1)] for s, at in self.steps],
                "age_s": round(time.monotonic() - self.started, 1), "slides": self.slides is not None,
                "dl": [self.dl_done, self.dl_total, round(self.dl_speed)] if self.dl_total else None,
                "model": self.model, "device": self.device, "notes": list(self.notes),
                "anomalies": [dict(a) for a in self.anomalies]}


class JobManager:
    def __init__(self, library: Library, sync: Any, settings: Callable[[], Settings],
                 engine_factory: Callable[[Settings], Any], emit: Emit,
                 writer: Callable[[str], dict[str, Any]], slides_task: Callable[[Job, Any, Any, requests.Session], Any],
                 log_: Optional[logging.Logger] = None) -> None:
        self.lib = library
        self.sync = sync
        self.settings = settings
        self.engine_factory = engine_factory
        self.emit = emit
        self.write_outputs = writer
        self.slides_task = slides_task
        self.log = log_ or log
        self._jobs: dict[str, Job] = {}
        self._lock = threading.RLock()
        # the queues (lock order: self._lock, then self._q): lectures waiting to download, in order of (rank, seq),
        # and lectures downloaded and decoded, ready to transcribe. Moving a lecture up adds a new entry; the old one
        # is skipped (its token no longer matches).
        self._q = threading.Condition()
        self._dl_heap: list[tuple[int, int, int, Job]] = []
        self._gpu_q: "queue.PriorityQueue[tuple[int, int, int, Job]]" = queue.PriorityQueue()
        self._tokens = itertools.count(1)
        self._seqs = itertools.count(1)
        self._top = 0                                    # "Do next" ranks: -1, -2, … (the latest asked goes first)
        self._lanes = 1                                  # download threads that take any lecture (see _start)
        self._held: dict[int, tuple[Job, bool]] = {}     # lectures the download threads hold (job, overtaking)
        # places for decoded audio in memory: one lecture transcribing, one ready (downloads still run ahead, as
        # files on disk). A free place goes to the waiting lecture that comes first in the queue. A PC with little
        # memory keeps only the one it transcribes: decoding the next one takes seconds, swapping takes much longer.
        # One with more memory keeps one more (a second lecture on the graphics card, or one more ready).
        memory = _memory_gb()
        self._places = 1 if memory < LOW_MEMORY_GB else DECODED_MAX + (memory >= MORE_MEMORY_GB)
        self._place_wait: dict[int, Job] = {}
        self._run = threading.Event()                    # cleared while the queue is paused
        self._run.set()
        self._paused_at = 0.0
        self._closing = False                            # the app closes: what is still waiting is kept
        self._waiting = ""                               # downloads wait: "offline" or "signin" (see _wait_for_net)
        self._saved: list[str] = self._load_saved()      # lectures still to transcribe (QUEUE_KEY), in order
        self._awake = KeepAwake("IEUStudia is transcribing lectures")
        self._started = False
        self._engine = None
        self._engine_key: Optional[tuple] = None
        self._engine_users = 0                           # lectures transcribing with it now (see _use_engine)
        self._engine_free = threading.Condition(self._lock)
        self._driving: set[int] = set()                  # transcribe threads raised to drive the card (drive_card)
        self._finishing: "queue.Queue[tuple[Job, list]]" = queue.Queue()   # transcribed: slides, files (_finish_loop)
        self._slides_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="slides")
        self._last_emit: dict[str, float] = {}
        self._idle_gen = 0
        self._preloading = False
        # the graphics card stopped working in the middle of a lecture (a driver reset or update): CUDA's error lasts
        # until IEUStudia closes, so the processor's engine that took over stays (see _transcribe)
        self._card_stopped = False
        self.setup_busy: Callable[[], bool] = lambda: False      # the setup still downloads models (the app sets it)

    # ------------------------------------------------------------------ public
    def _start(self) -> None:
        if self._started:
            return
        self._started = True
        # the offline AI frees an idle speech model before it loads on the graphics card (8 GB holds only one of them)
        local_llm.make_room = self.unload_engine
        # enough download threads to fill the places for decoded audio (they decode and find the speech too, on one
        # processor each: a PC with few processors keeps the number chosen in Settings)
        self._lanes = max(self.settings().parallel_downloads,
                          min(4, self._places - 1, max(1, (os.cpu_count() or 4) // 4)))
        for i in range(self._lanes):
            threading.Thread(target=self._dl_loop, name=f"download-{i + 1}", daemon=True).start()
        threading.Thread(target=self._dl_loop, args=(True,), name="download-next", daemon=True).start()
        for i in range(max(1, GPU_WORKERS)):
            threading.Thread(target=self._gpu_loop, args=(i,), name="transcribe" + (f"-{i + 1}" if i else ""),
                             daemon=True).start()
        threading.Thread(target=self._finish_loop, name="finish", daemon=True).start()

    def enqueue(self, ids: list[str], batch: bool = False) -> int:
        """Queue lectures. ``batch``: a full or course sync's; lectures asked for one by one go before those (one
        already waiting in such a batch moves ahead). Returns how many were queued or moved.

        Newest lecture first: a batch is ordered by the recordings' start (a later sync's newer lecture goes before
        the older ones still waiting), and one request's lectures go newest first, after those asked for earlier."""
        n = 0
        with self._lock:
            self._idle_gen += 1          # cancels a pending idle unload
            self._start()
            self._new_batch()
            recs = {did: self.lib.get_recording(did) for did in dict.fromkeys(ids)}
            odd = self._anomalies([d for d, r in recs.items() if r is not None and d not in self._jobs])
            for did in sorted(recs, key=lambda d: recs[d].start if recs[d] else dt.datetime.min, reverse=True):
                if did in self._jobs:
                    job = self._jobs[did]
                    if not batch and job.rank >= BATCH and not job.finished and job.where != "run":
                        with self._q:
                            job.rank, job.seq = NOW, next(self._seqs)
                            self._requeue(job)
                        n += 1
                    continue
                rec = recs[did]
                if rec is None:
                    continue
                course = self.lib.get_course(rec.course_id)
                code = course.code if course else ""
                job = Job(id=did, title=f"{code} · {date_short(rec.start.date())} {rec.start:%H:%M}".strip(" ·"),
                          course_id=rec.course_id, course_code=code, prev_status=rec.status,
                          duration_s=max(0.0, rec.duration_s), rank=BATCH if batch else NOW,
                          seq=-int(rec.start.timestamp()) if batch else next(self._seqs), day_key=rec.day_key,
                          anomalies=odd.get(did, []))
                self._jobs[did] = job
                self.lib.set_status(did, "queued")
                self._put_dl(job)
                detail, step = self._wait_text()       # (no connection: it says so)
                self._update(job, "queued", 0.0, detail, force=True, step=step)
                n += 1
            if self._jobs:
                self._working(True)
                self._preload()
            self._remember()
        self._emit_queue()
        return n

    def _anomalies(self, ids: list[str]) -> dict[str, list[dict[str, str]]]:
        """What looks odd about these recordings (the jobs pane shows it); never a reason not to queue them."""
        if not ids:
            return {}
        from .anomalies import for_ids

        try:
            return for_ids(self.lib, ids, min_s=self.settings().min_duration_s)
        except Exception:  # noqa: BLE001 - only a note
            self.log.warning("Anomaly checks failed", exc_info=True)
            return {}

    def enqueue_local(self, path: Path) -> str:
        did = "local-" + hashlib.sha1(str(Path(path).resolve()).encode()).hexdigest()[:12]
        with self._lock:
            self._idle_gen += 1
            self._start()
            self._new_batch()
            if did not in self._jobs:
                job = Job(id=did, title=Path(path).name, local_path=Path(path), seq=next(self._seqs))
                self._jobs[did] = job
                self._put_dl(job)
                self._update(job, "queued", 0.0, t("Waiting"), force=True)
            self._working(True)
            self._preload()
        self._emit_queue()
        return did

    def cancel(self, job_id: Optional[str] = None) -> None:
        with self._lock:
            jobs = [self._jobs[job_id]] if job_id and job_id in self._jobs else ([] if job_id else list(self._jobs.values()))
        for job in jobs:
            job.cancel.set()
            if job.stage == "queued":
                self._finish(job, "cancelled")

    def shutdown(self) -> None:
        """IEUStudia closes: stop, but keep the lectures still to transcribe for the next start (the one being
        transcribed starts again then)."""
        with self._lock:
            self._closing = True
        self.cancel(None)
        set_low_priority(False)

    def first(self, job_id: str) -> bool:
        """Do this lecture next: before everything still waiting (the one being transcribed finishes first)."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.finished or job.local_path is not None:
                return False
            self._top -= 1
            with self._q:
                job.rank = self._top
                self._requeue(job)
            self._remember()
        self._emit_queue()
        return True

    def pause(self, on: bool) -> None:
        """Pause or continue: nothing new starts, and the lecture being transcribed stops after its current
        passages. The PC may sleep while paused. Cancel still works."""
        with self._lock:
            if on == (not self._run.is_set()) or (on and not self._jobs):
                return
            b = getattr(self, "_batch", None)
            if on:
                self._run.clear()
                self._paused_at = time.monotonic()
                self._awake.off()
            else:
                if b is not None:
                    b["paused"] = b.get("paused", 0.0) + time.monotonic() - self._paused_at
                self._run.set()
                if self._jobs:
                    self._awake.on()
        self._emit_queue()

    def paused(self) -> bool:
        return not self._run.is_set()

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [j.view() for j in sorted(self._jobs.values(), key=self._order)]

    @staticmethod
    def _order(j: Job) -> tuple[int, int, int]:
        """Queue order for the page: the lecture being transcribed, one being finished (its slides, its files), then
        the others in the order they come."""
        return (0 if j.where == "run" and j.stage != "writing" else 1 if j.where == "run" else 2, j.rank, j.seq)

    def _new_batch(self) -> None:
        """Start counting a new batch when work is added to an idle queue (call with the lock held)."""
        if not self._jobs:
            self._batch = {"done": 0, "ended": 0, "failed": 0, "audio_done": 0.0, "started": time.monotonic(),
                           "paused": 0.0}

    def batch(self) -> dict[str, Any]:
        """How far the current batch is: finished / total jobs and, once one lecture is done, the time left at the
        speed measured so far (downloads, model load and both languages included; time paused is not)."""
        with self._lock:
            b = getattr(self, "_batch", None)
            # a job counted as ended stays in the queue a moment longer (its status is written first): not twice
            jobs = [j for j in self._jobs.values() if not j.finished]
        if not b or not jobs:
            return {}
        inflight = sum(j.duration_s * j.progress for j in jobs if j.stage in ("transcribing", "writing"))
        left = sum(j.duration_s for j in jobs) - inflight
        now = time.monotonic()
        elapsed = now - b["started"] - b.get("paused", 0.0) - (now - self._paused_at if self.paused() else 0.0)
        work = b["audio_done"] + inflight
        eta = round(left * elapsed / work) if b["audio_done"] > 0 and elapsed > 30 else None
        return {"finished": b["ended"], "done": b["done"], "failed": b["failed"], "total": b["ended"] + len(jobs),
                "left_s": eta}

    # ------------------------------------------------------------------ what is still to do, across restarts
    def _load_saved(self) -> list[str]:
        try:
            ids = json.loads(self.lib.get_meta(QUEUE_KEY) or "[]")
        except ValueError:
            return []
        return [str(i) for i in ids] if isinstance(ids, list) else []

    def _remember(self) -> None:
        """Store the lectures still to transcribe: the queue in its order, then any left from an earlier start that
        wasn't continued or forgotten yet (call with the lock held)."""
        now = [j.id for j in sorted(self._jobs.values(), key=lambda j: (j.rank, j.seq))
               if j.local_path is None and not j.finished]
        self._saved = now + [i for i in self._saved if i not in now]
        self.lib.set_meta(QUEUE_KEY, json.dumps(self._saved))

    def _forget(self, did: str) -> None:
        with self._lock:
            if did in self._saved:
                self._saved = [i for i in self._saved if i != did]
                self.lib.set_meta(QUEUE_KEY, json.dumps(self._saved))

    def leftover(self) -> list[str]:
        """Lectures that were still waiting when IEUStudia last closed (or stopped), not transcribed since."""
        with self._lock:
            ids = [i for i in self._saved if i not in self._jobs]
        out = []
        for did in ids:
            r = self.lib.get_recording(did)
            if r is not None and r.status != "done" and not r.duplicate_of and not r.hidden:
                out.append(did)
        return out

    def leftover_view(self) -> Optional[dict[str, Any]]:
        ids = self.leftover()
        if not ids:
            return None
        recs = [r for r in (self.lib.get_recording(i) for i in ids) if r is not None]
        courses = {c.id: c.code for c in self.lib.courses(True)}
        return {"count": len(recs), "days": len({r.day_key for r in recs}),
                "audio_s": round(sum(max(0.0, r.duration_s) for r in recs)),
                "courses": sorted({courses.get(r.course_id, "") for r in recs} - {""})}

    def resume_leftover(self) -> int:
        return self.enqueue(self.leftover())

    def forget_leftover(self) -> None:
        with self._lock:
            self._saved = [i for i in self._saved if i in self._jobs]
            self.lib.set_meta(QUEUE_KEY, json.dumps(self._saved))

    # ------------------------------------------------------------------ queues (call with self._q held)
    def _push_dl(self, job: Job) -> None:
        job.token, job.where = next(self._tokens), "dl"
        heapq.heappush(self._dl_heap, (job.rank, job.seq, job.token, job))

    def _push_gpu(self, job: Job) -> None:
        job.token, job.where = next(self._tokens), "gpu"
        self._gpu_q.put((job.rank, job.seq, job.token, job))

    def _requeue(self, job: Job) -> None:
        """Its rank changed: a new entry where it waits (the old one is skipped), and the threads look again."""
        if job.where == "dl":
            self._push_dl(job)
        elif job.where == "gpu":
            self._push_gpu(job)
        self._q.notify_all()                     # it may now overtake, or get the next free place

    def _put_dl(self, job: Job) -> None:
        with self._q:
            self._push_dl(job)
            self._q.notify_all()

    def _put_gpu(self, job: Job) -> None:
        with self._q:
            self._held.pop(id(job), None)
            self._push_gpu(job)
            self._q.notify_all()

    def _let_go(self, job: Job) -> None:
        """A download thread is done with this lecture (it failed, was cancelled, or waits to be tried again)."""
        with self._q:
            self._held.pop(id(job), None)
            if job.where == "prep":
                job.where = ""
            self._q.notify_all()

    def _take_dl(self, overtake: bool) -> Job:
        """The next lecture to download. The overtaking thread only takes one that comes before every lecture the
        other download threads hold (they may all be waiting for their turn with batch lectures)."""
        with self._q:
            while True:
                while self._dl_heap and self._dl_heap[0][2] != self._dl_heap[0][3].token:
                    heapq.heappop(self._dl_heap)          # it moved up the queue since: this is its old place
                if self._dl_heap:
                    head = self._dl_heap[0][3]
                    others = [j for j, lane in self._held.values() if not lane]
                    if not overtake or (len(others) >= self._lanes
                                        and all((head.rank, head.seq) < (j.rank, j.seq) for j in others)):
                        heapq.heappop(self._dl_heap)
                        head.where = "prep"
                        self._held[id(head)] = (head, overtake)
                        return head
                self._q.wait()

    def _working(self, on: bool) -> None:
        """While lectures are queued: the PC doesn't fall asleep (unless paused) and the programs you use come first."""
        if on:
            if self._run.is_set():
                self._awake.on()         # Windows' idle timer must not put the PC to sleep mid-batch
        else:
            self._awake.off()
            self._run.set()              # a pause ends with the queue
        set_low_priority(on)

    def _hold(self, job: Job, count: bool = False) -> None:
        """Wait while the queue is paused (Cancel still works). ``count``: paused in the middle of transcribing."""
        if self._run.is_set():
            return
        t0 = time.monotonic()
        while not self._run.wait(0.25):
            if job.cancel.is_set():
                raise Cancelled()
        if count:
            job.paused_s += time.monotonic() - t0

    def active_ids(self) -> set[str]:
        with self._lock:
            return set(self._jobs)

    def job(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def busy(self) -> bool:
        with self._lock:
            return bool(self._jobs)

    def note_unwritten(self, delivery_id: str, pending: bool) -> None:
        """Remember (or forget) a transcript whose files in the output folder are missing."""
        with self._lock:
            ids = set(self.unwritten())
            ids = ids | {delivery_id} if pending else ids - {delivery_id}
            self.lib.set_meta(PENDING_KEY, json.dumps(sorted(ids)))

    def unwritten(self) -> list[str]:
        try:
            ids = json.loads(self.lib.get_meta(PENDING_KEY) or "[]")
        except ValueError:
            return []
        return [str(i) for i in ids] if isinstance(ids, list) else []

    def engine_loaded(self) -> bool:
        return self._engine is not None and getattr(self._engine, "loaded", False)

    def unload_engine(self) -> bool:
        """Free the speech model unless a lecture is transcribing or waiting, or a study plan has it (lend_engine).
        True if it was loaded."""
        with self._lock:
            if self._engine is None or self._jobs or self._engine_users:
                return False
            was = self.engine_loaded()
            engine, self._engine, self._engine_key = self._engine, None, None
        engine.unload()          # (not under the lock: a model loading ahead finishes first, and the page must not wait)
        self.log.info("Speech model unloaded (idle) to free GPU memory")
        return was

    @contextlib.contextmanager
    def lend_engine(self, model: str, device: str) -> Iterator[Any]:
        """The speech model for something other than a lecture - a study plan listening to YouTube videos - while no
        lecture is transcribing or waiting (raises EngineBusy otherwise; the borrower watches busy() and stops when
        one is queued meanwhile). It is the lectures' own engine when ``model`` and ``device`` are those of Settings
        (loaded once, kept for the lectures and freed when idle, as after a lecture), else one of its own, freed
        afterwards (the small model on the processor while Settings has a bigger one)."""
        settings = self.settings()
        want = dc.replace(settings, model=model, device=device)
        with self._lock:
            if self._jobs:
                raise EngineBusy(t("Your lectures are being transcribed."))
            if self.setup_busy():
                raise EngineBusy(t("The setup is still downloading."), "setup")
        if self._key(want) != self._key(settings):
            engine = self.engine_factory(want)
            try:
                yield engine
            finally:
                engine.unload()
            return
        engine = self._use_engine(settings)
        try:
            yield engine
        finally:
            self._done_with_engine()
            with self._lock:
                idle = not self._jobs and self._engine is not None
            if idle:
                self._schedule_idle_unload()

    def _schedule_idle_unload(self) -> None:
        with self._lock:
            self._idle_gen += 1
            gen = self._idle_gen

        def run() -> None:
            if gen == self._idle_gen:
                self.unload_engine()

        timer = threading.Timer(IDLE_UNLOAD_S, run)
        timer.daemon = True
        timer.start()

    # ------------------------------------------------------------------ events
    def queue_event(self) -> dict[str, Any]:
        return {"type": "queue", "jobs": self.snapshot(), "batch": self.batch(), "paused": self.paused(),
                "waiting": self._waiting}

    def _emit_queue(self) -> None:
        self.emit(self.queue_event())

    def _update(self, job: Job, stage: str, progress: float, detail: str, force: bool = False,
                step: str = "") -> None:
        """``step``: what it does now when its stage doesn't say (decoding, finding speech …; see _STEP)."""
        with self._lock:                 # _finish marks a job finished under this lock
            if job.finished:             # e.g. cancelled while its worker was still busy with it: that is final
                return
            changed = stage != job.stage
            job.stage, job.progress, job.detail = stage, max(0.0, min(1.0, progress)), detail
            now = time.monotonic()
            step = step or _STEP.get(stage, stage)
            if step != job.step:         # a new step: the pane shows it at once
                job.step = step
                job.steps.append((step, now))
                force = True
            if not (force or changed) and now - self._last_emit.get(job.id, 0.0) < 0.25:
                return
            self._last_emit[job.id] = now
            if changed and job.local_path is None and stage in _STATUS:
                self.lib.set_status(job.id, _STATUS[stage])
            self.emit({"type": "job", **job.view()})
        if changed:
            self._emit_queue()

    def _take_audio_place(self, job: Job) -> None:
        """Wait until fewer than DECODED_MAX decoded lectures are in memory (a slow processor may take an hour per
        lecture while the downloads are long done). Of the lectures waiting, the one first in the queue gets the
        free place. Cancel works while waiting."""
        if job.holds_audio:
            return
        if not self._try_place(job):
            try:
                self._update(job, "queued", 0.0, t("Waiting to prepare the audio") if job.local_path else
                             t("Downloaded, waiting to prepare the audio"), force=True, step="place")
                while not self._try_place(job, 0.5):
                    if job.cancel.is_set():
                        raise Cancelled()
            finally:
                with self._q:
                    self._place_wait.pop(id(job), None)
                    self._q.notify_all()
        if job.cancel.is_set():          # cancelled just as its turn came
            self._drop_audio(job)
            raise Cancelled()

    def _try_place(self, job: Job, wait: float = 0.0) -> bool:
        with self._q:
            self._place_wait[id(job)] = job
            if wait:
                self._q.wait(wait)
            first = min(self._place_wait.values(), key=lambda j: (j.rank, j.seq))
            if self._places > 0 and first is job:
                self._places -= 1
                job.holds_audio = True
                del self._place_wait[id(job)]
                return True
            return False

    def _drop_audio(self, job: Job) -> None:
        job.audio = job.speech = None
        with self._q:
            held, job.holds_audio = job.holds_audio, False
            if held:
                self._places += 1
                self._q.notify_all()

    def _finish(self, job: Job, stage: str, error: str = "") -> None:
        with self._lock:
            if job.finished:          # e.g. cancelled while queued, then picked up by a worker
                return
            job.finished = True
            current = self._jobs.get(job.id) is job
        job.error = error
        self._drop_audio(job)
        self._let_go(job)
        if (current and job.local_path is None and (job.no_sound or stage == "cancelled" and not self._closing)
                and not self.settings().keep_audio):
            # no sound, or the student cancelled it: its downloaded audio would stay in the cache for good. A lecture
            # still queued when IEUStudia closes keeps it (the next start goes on), as does one that failed to
            # download (it is tried again).
            for f in audio_cache_dir().glob(f"{job.id}.*"):
                self._unlink(f)
        if current and stage == "cancelled" and not self._closing:
            from .engine.checkpoint import discard      # what was transcribed of it: you cancelled it

            discard(job.id)
        with self._lock:
            b = getattr(self, "_batch", None)
            if b is not None and current:
                b["ended"] += 1
                if stage == "done":
                    b["done"] += 1
                    b["audio_done"] += job.duration_s
                    b["last_done"] = {"id": job.id, "title": job.title, "local": job.local_path is not None}
                elif stage in ("failed", "processing"):
                    b["failed"] += 1
            if current and not self._closing and job.local_path is None:
                try:
                    self._forget(job.id)      # done, failed, not ready, or cancelled: no longer waiting
                except Exception:  # noqa: BLE001 - a full disk, a library locked for long: see below
                    self.log.warning("Taking %s off the stored queue failed", job.id, exc_info=True)
        status, why = "", ""
        if job.local_path is None and current:
            if stage == "cancelled":
                status = job.prev_status if job.prev_status in ("done", "available") else "available"
            elif stage == "failed" and job.prev_status == "done":
                status = "done"               # transcribing it again failed: the earlier transcript is still there
            elif stage in _STATUS:
                status, why = _STATUS[stage], error
            # a full disk or a library locked for long is logged, and the lecture still leaves the queue: raised here,
            # it ended the worker's thread, and every later lecture waited for good (and the PC never slept)
            try:
                if status:
                    self.lib.set_status(job.id, status, why)
            except Exception:  # noqa: BLE001
                self.log.warning("Saving how %s ended (%s) failed", job.id, status, exc_info=True)
        job.stage = stage
        if not current:   # a newer job for the same recording owns the UI row and the status
            return
        with self._lock:
            if self._jobs.get(job.id) is job:     # never drop a newer job queued under the same id
                self._jobs.pop(job.id, None)
                self._last_emit.pop(job.id, None)
            idle = not self._jobs
            if idle:
                self._working(False)
            b = dict(getattr(self, "_batch", None) or {})
        evt = {"type": "job", **job.view(), **job.metrics(), "final": True, "batch_done": idle, "status": status,
               "unwritten": job.unwritten}
        if idle and not self._closing:            # what the whole batch came to (the notification says it)
            evt["batch_result"] = {"done": b.get("done", 0), "failed": b.get("failed", 0), "ended": b.get("ended", 0),
                                   "last_done": b.get("last_done")}
        self.emit(evt)
        self._emit_queue()
        if idle and self._engine is not None:
            self._schedule_idle_unload()

    @staticmethod
    def _unlink(path: Path) -> None:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:                   # e.g. still open in an antivirus scan: the start-up sweep takes it later
            pass

    # ------------------------------------------------------------------ download side
    def _dl_loop(self, overtake: bool = False) -> None:
        """A download thread. ``overtake``: the extra one, for lectures that come before the ones the other
        threads hold."""
        while True:
            job = self._take_dl(overtake)
            if job.cancel.is_set():
                self._let_go(job)
                self._finish(job, "cancelled")
                continue
            try:
                self._hold(job)                   # paused: no new download starts
                self._gate(job)                   # nor while there is no connection
                self._prepare(job)
                self._find_speech(job)
                if job.cancel.is_set():
                    raise Cancelled()
                self._update(job, "queued", 0.0, t("Waiting to be transcribed") if job.local_path else
                             t("Downloaded, waiting to be transcribed"), step="turn")         # Cancel ends it at once
                self._put_gpu(job)
                continue
            except BaseException as exc:  # noqa: BLE001 - every way out gives back the place for its audio
                self._drop_audio(job)
                self._let_go(job)
                error = exc
            if isinstance(error, Cancelled) or job.cancel.is_set():     # whatever the error, Cancel was pressed
                self._finish(job, "cancelled")
            elif isinstance(error, NotReady):
                self._finish(job, "processing", str(error))
            elif isinstance(error, NoSpeech):             # found while finding its speech (see _find_speech)
                self._no_speech(job, error)
            elif isinstance(error, (NeedLogin, AuthError)) and not getattr(error, "server_error", False):
                # the saved password no longer works (or there is none): never tried again in the background
                self.log.warning("Downloads wait for a new sign-in: %s", error)
                self._wait_for_net(job, "signin")
            elif (isinstance(error, (requests.RequestException, OSError)) and job.local_path is None
                  and net.connection_lost(error) and not net.online()):
                self.log.info("No connection for %s (%s); the downloads wait for it", job.id, error)
                self._wait_for_net(job, "offline")
            elif isinstance(error, (requests.RequestException, OSError)):
                # a full disk, or a file of this PC that can't be read, won't be fixed by trying again (a file in
                # the app's own cache held for a moment by an antivirus may be)
                lasting = getattr(error, "errno", None) == errno.ENOSPC or job.local_path is not None
                job.attempts += 1
                if job.attempts <= 2 and not lasting and not job.cancel.is_set():
                    self.log.info("Download problem for %s (%s); retrying", job.id, error)
                    self._update(job, "queued", 0.0, t("Connection problem, retrying shortly"), force=True)
                    retry = threading.Timer(4.0 * job.attempts, self._put_dl, args=(job,))
                    retry.daemon = True
                    retry.start()
                else:
                    self.log.warning("Download of %s failed: %s", job.id, error)
                    self._finish(job, "failed", plain_error(error) if lasting else
                                 t("The download failed. {error}", error=plain_error(error)))
            elif isinstance(error, Exception):
                self.log.error("Preparing %s failed", job.id, exc_info=error)
                if job.local_path is None and _no_sound(error):
                    # Panopto has no stream with sound: the page knows it by this code, not by the (translated) text
                    job.no_sound = True
                    self._warn_no_sound(job, str(error))
                self._finish(job, "failed", plain_error(error))
            else:
                raise error

    def _wait_text(self) -> tuple[str, str]:
        """What a lecture still to download says while the downloads wait (see _wait_for_net), and its step."""
        if self._waiting == "offline":
            return t("Waiting for the internet connection"), "offline"
        if self._waiting == "signin":
            return t("IEULearn needs you to sign in again. Press Sync to sign in; the downloads then go on by "
                     "themselves."), "signin"
        return t("Waiting"), "wait"

    def _wait_for_net(self, job: Job, why: str) -> None:
        """No internet connection (``why`` "offline": Wi-Fi off, the PC asleep, a VPN switching), or IEULearn no
        longer takes the saved password ("signin"): the lecture goes back to its place (not counted as a try: its
        download goes on from its parts), and no download starts until one thread, looking every
        net.ONLINE_EVERY_S, finds the connection back (or a new sign-in). Lectures already downloaded go on
        transcribing meanwhile. Cancel works; closing the app keeps them for the next start."""
        with self._lock:
            watch = not self._waiting
            if watch or why == "signin":       # a wrong password outlasts the connection coming back
                self._waiting = why
        with self._q:
            self._push_dl(job)
            self._q.notify_all()
        self._show_waiting()
        if watch:
            threading.Thread(target=self._watch_net, name="wait-online", daemon=True).start()

    def _watch_net(self) -> None:
        why = self._waiting
        while True:
            end = time.monotonic() + net.ONLINE_EVERY_S
            while time.monotonic() < end:
                if self._closing:
                    return
                time.sleep(0.05)
            with self._lock:
                why, idle = self._waiting, not self._jobs
            try:
                if idle or (self.sync.has_credentials() if why == "signin" else net.online()):
                    break
            except Exception:  # noqa: BLE001 - not yet
                self.log.debug("Still waiting (%s)", why, exc_info=True)
        self.log.info("The downloads go on (waited: %s)", why)
        with self._lock:
            self._waiting = ""
        with self._q:
            self._q.notify_all()
        self._show_waiting()

    def _show_waiting(self) -> None:
        """The lectures still to download say why they wait (or that they only wait their turn again)."""
        detail, step = self._wait_text()
        with self._lock:
            jobs = [j for j in self._jobs.values() if j.local_path is None and j.stage == "queued"
                    and j.step in ("wait", "offline", "signin") and j.where in ("dl", "prep")]
        for j in jobs:
            self._update(j, "queued", 0.0, detail, force=True, step=step)
        self._emit_queue()

    def _gate(self, job: Job) -> None:
        """Before a download: wait while the downloads wait for the connection or a sign-in (Cancel works)."""
        if job.local_path is not None or not self._waiting:
            return
        detail, step = self._wait_text()
        self._update(job, "queued", 0.0, detail, force=True, step=step)
        while self._waiting:
            if job.cancel.is_set():
                raise Cancelled()
            with self._q:
                self._q.wait(0.25)

    def _panopto(self, job: Job) -> tuple[Panopto, requests.Session, dict]:
        s = self.sync.worker_session()
        p = Panopto(s)
        try:
            return p, s, p.delivery_info(job.id)
        except NeedPanoptoAuth:
            self.sync.ensure_panopto(job.course_id or None)
            s = self.sync.worker_session()
            p = Panopto(s)
            return p, s, p.delivery_info(job.id)

    def _prepare(self, job: Job) -> None:
        if job.local_path is not None:
            self._take_audio_place(job)
            self._update(job, "downloading", 0.5, t("Reading the file"), step="decode")
            job.audio = decode_audio(job.local_path, job.cancel)
            job.audio_s = len(job.audio) / 16000           # (the decoder's 16 kHz)
            return
        rec = self.lib.get_recording(job.id)
        if rec is not None and rec.live:          # nothing to download yet: Panopto isn't asked
            raise NotReady(t("This lecture is still being recorded. It can be transcribed once Panopto has finished it."))
        self._update(job, "downloading", 0.0, t("Contacting Panopto"), force=True)
        p, s, info = self._panopto(job)
        job.info = info
        sources = p.media_sources(job.id, info)
        settings = self.settings()
        if settings.capture_slides and job.slides is None:
            screens = p.screen_sources(info)          # every stretch of screen sharing, in time order
            if screens:
                job.slides = self._slides_pool.submit(self.slides_task, job, screens, self.sync.worker_session())
        last_exc: Optional[BaseException] = None
        silent: Optional[np.ndarray] = None
        silent_path: Optional[Path] = None
        for i, src in enumerate(sources):
            if src.url in job.no_audio:
                continue
            if silent is not None:
                # the first audio was silent: only fetch a camera (HLS) stream that demonstrably has sound
                if src.kind != "hls":
                    continue
                self._update(job, "downloading", 0.0, t("The audio is silent: checking Panopto's other streams"))
                try:
                    peak = hls.audio_peak_dbfs(s, src.url)
                except Exception as exc:  # noqa: BLE001
                    self.log.info("Probe of %s failed: %s", src.kind, exc)
                    continue
                if peak is None or peak < SILENT_DBFS:
                    continue
            elif src.kind != "audio_podcast" and p.has_sound(src) is False:
                # its header says it has no sound track (a video-only podcast or camera stream): not downloaded (some
                # hundred MB) only to find that out. If the header can't be read, it is downloaded as before.
                self.log.info("%s: %s has no audio track; trying another stream", job.id, src.kind)
                job.no_audio.add(src.url)
                if last_exc is None or _no_sound(last_exc):     # a failed download says more
                    last_exc = _no_sound_error()
                continue
            dest = audio_cache_dir() / f"{job.id}.{'m4a' if src.kind == 'audio_podcast' else 'mp4'}"

            def prog(done: int, total: int) -> None:
                frac = done / total if total else 0.0
                now, (at, was) = time.monotonic(), job.dl_mark
                if not at or done < was:                  # a new download (another stream)
                    job.dl_mark, job.dl_speed = (now, done), 0.0
                elif now - at >= 1.0:                     # the speed over the last second or more, smoothed a little
                    speed = (done - was) / (now - at)
                    job.dl_speed = speed if not job.dl_speed else 0.5 * speed + 0.5 * job.dl_speed
                    job.dl_mark = (now, done)
                job.dl_done, job.dl_total = done, total if total > 1000 else 0
                self._update(job, "downloading", frac,
                             t("Downloading audio {done}/{total} MB", done=f"{done / 1e6:.0f}", total=f"{total / 1e6:.0f}")
                             if total > 1000 else t("Downloading audio"))

            audio = None
            for again in (False, True):               # a downloaded file that turns out damaged: downloaded once more
                try:
                    p.download(src, dest, prog, job.cancel, prefer="audio")
                except Cancelled:
                    raise
                except Exception as exc:  # noqa: BLE001 - try the next source
                    self.log.info("Source %s failed for %s: %s", src.kind, job.id, exc)
                    last_exc = exc
                    break
                job.audio_path = dest
                self._take_audio_place(job)
                self._update(job, "downloading", 1.0, t("Decoding audio"), force=True, step="decode")  # after the last MB
                try:
                    audio = decode_audio(dest, job.cancel)
                    break
                except Exception as exc:  # noqa: BLE001 - only these two are handled here
                    if _no_sound(exc):
                        # a stream Panopto made without sound (from the screen recording, say): the next one may
                        # have it
                        self.log.info("%s: %s has no audio track; trying another stream", job.id, src.kind)
                        job.no_audio.add(src.url)
                        if last_exc is None or _no_sound(last_exc):     # a failed download says more
                            last_exc = _no_sound_error()
                        job.audio_path = None
                        self._unlink(dest)
                        break
                    if not _damaged(exc):
                        raise
                    # its end never reached the disk (a power cut while it downloaded), say: the file the cache
                    # keeps would fail the same way at every try, with FFmpeg's own words
                    self.log.warning("%s: the downloaded %s can't be decoded (%s); %s", job.id, src.kind, exc,
                                     "trying another stream" if again else "downloading it again")
                    job.audio_path = None
                    _forget_download(dest)
                    last_exc = DamagedAudio(t("The lecture's audio couldn't be read: the downloaded file was damaged, "
                                              "so it was deleted. Transcribe the lecture again to download it afresh."))
            if audio is None:
                continue
            if is_silent(audio) and i + 1 < len(sources):
                self.log.info("%s: %s audio is silent; looking for another stream", job.id, src.kind)
                if silent_path is not None and silent_path != dest:
                    self._unlink(silent_path)
                silent, silent_path = audio, dest
                continue
            job.audio = audio
            break
        if job.audio is None:
            if silent is not None:
                job.audio, job.audio_path = silent, silent_path    # the engine reports "no sound" with a clear message
            else:
                raise last_exc or RuntimeError(t("No downloadable audio for this recording."))
        elif silent_path is not None and silent_path != job.audio_path:
            self._unlink(silent_path)          # the silent stream's file: another stream had the sound
        job.audio_s = len(job.audio) / 16000                # (the decoder's 16 kHz)
        self.sync.absorb(s)

    def _find_speech(self, job: Job) -> None:
        """Find the lecture's speech and check its audio here, in the download thread, so the graphics card doesn't
        wait for it (~10 s an hour of audio); a recording without speech fails here (quality.NoSpeech). The lecture
        then waits for its turn as int16."""
        find = self._speech_finder(self.settings())
        if find is not None:
            self._update(job, "downloading", 1.0, t("Finding speech"), force=True, step="speech")
            job.speech = find(job.audio, job.cancel, self._meta(job).get("expected_duration", 0.0))
            job.passages = [0, len(getattr(job.speech, "units", None) or [])]
        job.audio = _to_int16(job.audio)

    def _speech_finder(self, settings: Settings) -> Optional[Callable[..., Any]]:
        """The engine's find_speech (no model needed): of the engine in use, or of one made for these settings that
        is never loaded. None for an engine without it."""
        with self._lock:
            engine = self._engine if self._engine_key == self._key(settings) else None
        if engine is None:
            try:
                engine = self.engine_factory(settings)
            except Exception:  # noqa: BLE001 - e.g. an unknown model: transcribing the lecture says so
                return None
        return getattr(engine, "find_speech", None)

    @staticmethod
    def _meta(job: Job) -> dict[str, Any]:
        meta: dict[str, Any] = {"delivery_id": job.id}
        if job.info:
            d = job.info.get("Delivery") or {}
            meta.update(expected_duration=float(d.get("Duration") or 0.0), recording_name=d.get("SessionName", ""))
        if job.local_path is not None:
            meta["source_path"] = str(job.local_path)
        return meta

    def _no_speech(self, job: Job, exc: NoSpeech) -> None:
        if job.local_path is None:
            job.no_sound = True
            self._warn_no_sound(job, str(exc))
        self._finish(job, "failed", str(exc))

    def _warn_no_sound(self, job: Job, message: str) -> None:
        """The lecture's "no speech" warning (the page knows it by its code). A library write that fails (a full disk)
        is logged: the lecture still ends (_finish), and the worker's thread goes on."""
        try:
            self.lib.set_warnings(job.id, [{"code": "no_speech", "level": "error", "message": message}])
        except Exception:  # noqa: BLE001
            self.log.warning("Saving the no-speech note of %s failed", job.id, exc_info=True)

    # ------------------------------------------------------------------ GPU side
    @staticmethod
    def _key(settings: Settings) -> tuple:
        return (settings.model, settings.device, tuple(settings.languages))

    def _get_engine(self, settings: Settings) -> Any:
        key = self._key(settings)
        with self._lock:
            if self._engine is not None and self._engine_key == key:
                return self._engine
        made = self.engine_factory(settings)          # (outside the lock: the first one imports the engine's code)
        with self._lock:
            while True:
                if self._engine is not None and self._engine_key == key:     # the other thread was quicker
                    return self._engine
                if not self._engine_users:
                    break
                # another lecture still transcribes with the model of the earlier settings: the card holds one
                self._engine_free.wait(0.5)
            old, self._engine, self._engine_key = self._engine, made, key
        if old is not None:
            old.unload()
        return made

    def _use_engine(self, settings: Settings) -> Any:
        """The engine for these settings, counted as in use until _done_with_engine (it is not swapped meanwhile)."""
        while True:
            engine = self._get_engine(settings)
            with self._lock:
                if self._engine is engine:
                    self._engine_users += 1
                    return engine

    def _done_with_engine(self) -> None:
        with self._lock:
            self._engine_users -= 1
            self._engine_free.notify_all()

    def _may_share(self) -> bool:
        """A second lecture may be transcribed now, with the model already loaded on the graphics card: the queue isn't
        paused, the PC has the processors and free memory for it, and the offline AI isn't on the card too."""
        with self._lock:
            engine = self._engine
            if (engine is None or not self._jobs or not self._run.is_set()
                    or self._engine_key != self._key(self.settings())
                    or not getattr(engine, "loaded", False) or getattr(engine, "device", "") != "cuda"):
                return False
        if (os.cpu_count() or 1) < SHARE_MIN_CPUS or _free_memory_gb() < SHARE_FREE_GB:
            return False
        from .engine.transcriber import _ai_on_gpu

        return not _ai_on_gpu()

    def _drive_card(self, engine: Any) -> None:
        """This transcribe thread hands the card its batches: on a graphics card it runs at a normal program's
        priority, like the engine's own threads (see winutil.drive_card); on the processor as the rest again."""
        from .osutil import drive_card

        me = threading.get_native_id()
        on = getattr(engine, "device", "") == "cuda"
        if on != (me in self._driving):
            drive_card([me], on)
            (self._driving.add if on else self._driving.discard)(me)

    def _preload(self) -> None:
        """Lectures are queued: load the speech model while the first one downloads, not after it (6-25 s for
        large-v3). Not while the setup still downloads it (call with the lock held)."""
        if self._preloading or self.engine_loaded() or self.setup_busy():
            return
        self._preloading = True
        threading.Thread(target=self._load_ahead, name="model-preload", daemon=True).start()

    def _load_ahead(self) -> None:
        """Only a model on disk (the first lecture downloads one, and Cancel stops that). A load that fails here is
        tried again, and reported, by the first lecture."""
        from .engine.models import model_is_downloaded

        ok = False
        try:
            settings = self.settings()
            if not model_is_downloaded(settings.model):
                return
            with self._lock:
                busy = any(j.where == "run" for j in self._jobs.values())       # the transcribe thread loads it
                other = self._engine is not None and self._engine_key != self._key(settings)
                if busy or other or not self._jobs:
                    return
            engine = self._get_engine(settings)
            with self._lock:
                gone = not self._jobs                          # cancelled meanwhile: nothing to load it for
            if gone or getattr(engine, "loaded", True):
                return

            def progress(stage: str, frac: float, detail: str) -> None:
                phase = {"model-download": "download"}.get(stage, "load")
                self.emit({"type": "engine", "phase": phase, "progress": round(frac, 3), "message": detail})

            engine.ensure_model(progress, None)
            ok = True
        except Exception:  # noqa: BLE001
            self.log.info("Loading the speech model ahead failed", exc_info=True)
            self.emit({"type": "engine", "phase": "failed", "progress": 0.0, "message": ""})   # the page hides it
        finally:
            with self._lock:
                self._preloading = False
        if ok:
            self.emit({"type": "engine", "phase": "ready", "progress": 1.0, "message": "Model ready"})

    def _gpu_loop(self, worker: int = 0) -> None:
        """A transcribe thread. ``worker`` 1 and up: the second lecture on the graphics card, only while the PC
        allows it (see _may_share); it never loads a model."""
        while True:
            if worker and not self._may_share():
                time.sleep(1.0)
                continue
            try:
                _, _, token, job = self._gpu_q.get(timeout=1.0 if worker else None)
            except queue.Empty:
                continue
            late = worker and not self._may_share()           # e.g. paused, or the settings changed meanwhile
            with self._q:                                     # (lock order: self._lock is never taken under it)
                if token != job.token:            # it moved up the queue since: this is its old place
                    continue
                if late:
                    self._push_gpu(job)
                    self._q.notify_all()
                    continue
                before = lambda j: (j.rank, j.seq) < (job.rank, job.seq)    # noqa: E731
                if (any(j.holds_audio and before(j) for j, _ in self._held.values())
                        or (self._places > 0 and any(before(j) for j in self._place_wait.values()))):
                    # a lecture that comes before this one is being decoded, or is about to take the place that just
                    # came free: put this one back and take the first one in a moment
                    self._push_gpu(job)
                    self._q.wait(0.5)
                    continue
                job.where = "run"
            if job.cancel.is_set():
                self._finish(job, "cancelled")
                continue
            try:
                self._hold(job)                   # paused: the next lecture doesn't start
                if job.cancel.is_set():           # cancelled while paused (the pause ended with the queue)
                    raise Cancelled()
                self._transcribe(job)
            except Cancelled:
                self._finish(job, "cancelled")
            except NoSpeech as exc:
                self._no_speech(job, exc)
            except Exception as exc:  # noqa: BLE001
                self.log.exception("Transcribing %s failed", job.id)
                self._finish(job, "failed", plain_error(exc))
            finally:
                self._drop_audio(job)

    def _engine_progress(self, job: Job, stage: str, frac: float, detail: str) -> None:
        phase = {"model-download": "download", "model-load": "load"}.get(stage)
        if phase:
            job.model_phase = phase
            # the load's last step is "ready": also when the engine loads the model again on its own (in transcribe)
            done = phase == "load" and frac >= 1.0
            self.emit({"type": "engine", "phase": "ready" if done else phase, "progress": round(frac, 3),
                       "message": detail})
            self._update(job, "transcribing", 0.0, detail, step="model")
            return
        paused = job.paused_s             # this message's estimate was made before any pause that starts now
        self._hold(job, count=stage == "transcribe")      # paused: stop between passages (a download never waits)
        m = _ETA.search(detail)
        done = (frac - 0.05) / 0.88                       # the share of passages done (see Engine._decode_units)
        if m and paused and stage == "transcribe" and done > 0:
            # the engine counts the time paused as time spent: take it out of its estimate
            left = max(0, round(int(m.group(1)) - paused * (1 - done) / done))
            detail = f"{detail[:m.start()]} · ~{left}s left{detail[m.end():]}"
        if stage == "transcribe":
            with self._lock:
                on_card = [j for j in self._jobs.values() if j.where == "run" and j.step == "transcribe"]
            if len(on_card) > 1 or (on_card and on_card[0] is not job):
                for j in on_card + [job]:
                    j.own_speed = False
            n = _PASSAGES.match(_ETA.sub("", detail))
            if n and int(n.group(1)) <= int(n.group(2)) and job.passages[1] in (0, int(n.group(2))):
                job.passages = [int(n.group(1)), int(n.group(2))]
        self._update(job, "transcribing", frac, detail, force=not m,    # a message without a time left is rare: show it
                     step="speech" if stage == "vad" else "transcribe")

    def _ensure_model(self, engine: Any, job: Job) -> None:
        """Download (the first time) and load the speech model for this lecture. However that ends, the page hears
        it: its queue bar shows "Downloading the speech model" until "ready" or "failed", and a cancel, a lost
        connection or a model that won't load used to leave that bar spinning, with no button, over an empty
        queue. A download or load that fails says so, with what to do (model_failure)."""
        job.model_phase = ""
        try:
            engine.ensure_model(lambda st, f, d: self._engine_progress(job, st, f, d), job.cancel)
        except BaseException as exc:
            self.emit({"type": "engine", "phase": "failed", "progress": 0.0, "message": ""})
            if job.model_phase and isinstance(exc, Exception) and not isinstance(exc, Cancelled):
                raise ModelFailed(model_failure(job.model_phase, exc)) from exc     # (_gpu_loop logs both)
            raise

    def _card_note(self, engine: Any, model: str) -> str:
        """The graphics card couldn't run the speech model and the processor took over (Engine.ensure_model): that is
        remembered for this PC (hardware.remember_card_failed), so the next starts go to the processor at once and the
        setup and Settings say why; said once at once, and in each such lecture's note. A card that was only full
        (another program's memory) is not remembered: that passes. One that runs it again (chosen in Settings) is
        used again. "": nothing to say."""
        from . import hardware

        err = getattr(engine, "gpu_error", "") or ""
        chosen = getattr(engine, "device_pref", "auto") == "cuda"        # "Graphics card" chosen: tried every time
        try:
            if getattr(engine, "device", "") == "cuda":
                hardware.forget_card_failed()
                return ""
            if not err:
                # passed over at once because it failed before (Automatic): each such lecture says so, not only
                # Settings after a restart
                if getattr(engine, "device_pref", "auto") == "auto" and hardware.card_failed():
                    return t("The processor transcribed this lecture: your graphics card couldn't run the speech model "
                             "before. To try the card again, choose Graphics card in Settings → Transcription.")
                return ""
            if hardware.card_error_kind(err) == "passing":   # only full, or a one-off fault: the next lecture tries it
                return ""
            was = bool(hardware.card_failed())
            # one failure counts once: a lecture that goes on with the engine that fell back doesn't count it again
            first = (not getattr(engine, "card_counted", False) and hardware.remember_card_failed(err, model)
                     and not was)
            engine.card_counted = True
            if not hardware.card_failed():                   # seen once: the next lecture tries the card again
                return t("Your graphics card couldn't run the speech model this time, so the processor transcribed "
                         "this lecture. The next lecture tries the card again.")
        except Exception:  # noqa: BLE001 - only a memory for the next starts
            self.log.warning("Remembering the graphics card's failure failed", exc_info=True)
            first = False
        note = (t("Your graphics card couldn't run the speech model, so the processor transcribed this lecture. "
                  "Graphics card is chosen in Settings → Transcription, so each lecture tries it first; choose "
                  "Automatic there to go to the processor at once.") if chosen else
                t("Your graphics card couldn't run the speech model, so the processor transcribed this lecture. "
                  "IEUStudia uses the processor from now on.") if model == "small" else
                t("Your graphics card couldn't run the speech model, so the processor transcribed this lecture, which "
                  "is much slower with this model. IEUStudia uses the processor from now on: choose the Small model "
                  "in Settings → Transcription for faster transcripts."))
        if first:
            self.emit({"type": "toast", "level": "warning", "message": note})
        return note

    def _let_fallback_go(self, engine: Any) -> None:
        """The graphics card failed and the processor took over (Engine.ensure_model keeps that engine): it goes after
        the lecture, so the next one loads the model again and really tries the card, as its note says (a card that was
        only full, or failed once, often works then). Kept on Automatic when the card is remembered as unusable (the
        next engine would go to the processor at once), and while a study plan still uses it."""
        if not getattr(engine, "gpu_error", "") or getattr(engine, "device", "") == "cuda" or self._card_stopped:
            return                    # (a card that stopped working mid-batch can't work again before a restart)
        from . import hardware

        try:
            if getattr(engine, "device_pref", "auto") == "auto" and hardware.card_unusable():
                return
        except Exception:  # noqa: BLE001 - only how soon the card is tried again
            self.log.debug("Asking about the graphics card failed", exc_info=True)
        with self._lock:
            if self._engine is not engine or self._engine_users:
                return
            self._engine, self._engine_key = None, None
        engine.unload()
        self.log.info("The speech model ran on the processor after the graphics card failed; the next lecture tries "
                      "the card again")

    def _run_engine(self, engine: Any, job: Job, settings: Settings) -> Any:
        """The lecture's transcript, with this engine (its model loaded first if need be)."""
        units = getattr(job.speech, "units", None)
        if getattr(engine, "loaded", False) and units is not None:
            # the model is there: its first batch is transcribing, not "Loading the speech model" (that step took
            # the first batch's time, ~9 s, as if the model loaded again for every lecture)
            self._update(job, "transcribing", 0.05, t("Transcribing passage {done} of {n}", done=0, n=len(units)),
                         force=True, step="transcribe")
        elif not getattr(engine, "loaded", False):
            self._update(job, "transcribing", 0.0, t("Preparing the speech model"), force=True, step="model")
        self._ensure_model(engine, job)
        self.emit({"type": "engine", "phase": "ready", "progress": 1.0, "message": "Model ready"})
        self._drive_card(engine)
        info = getattr(engine, "device_info", None) or {}           # (the pane says what it is transcribed with)
        job.model, job.device = getattr(engine, "model_name", "") or settings.model, info.get("device", "")
        if self._card_stopped and getattr(engine, "gpu_error", "") and job.device != "cuda":
            note = t("The graphics card stopped working during the batch (a driver update or reset can do this), so "
                     "the processor transcribed this lecture. Close IEUStudia and open it again to use the card again.")
        else:
            note = self._card_note(engine, job.model) or info.get("warning")
        if note and note not in job.notes:
            job.notes.append(note)
        job.audio = _to_float32(job.audio)                # its turn: the samples as decoded
        found = {"speech": job.speech} if job.speech is not None else {}
        transcript = engine.transcribe(job.audio, lambda st, f, d: self._engine_progress(job, st, f, d),
                                       job.cancel, self._meta(job), **found)
        if getattr(engine, "resumed_passages", 0):    # (Job.metrics)
            job.own_speed = False
        return transcript

    def _drop_engine(self, engine: Any) -> None:
        """An engine whose graphics card stopped working: the next lecture makes a new one, as after a change of the
        settings (a study plan still using it finishes first)."""
        with self._lock:
            while self._engine is engine and self._engine_users:
                self._engine_free.wait(0.5)
            if self._engine is engine:
                self._engine, self._engine_key = None, None
        try:
            engine.unload()
        except Exception:  # noqa: BLE001 - it is dropped anyway
            self.log.debug("Unloading the engine whose card stopped failed", exc_info=True)

    def _transcribe(self, job: Job) -> None:
        if self.setup_busy():                     # the speech model (or the graphics card's files) are on their way
            self._update(job, "transcribing", 0.0, t("Waiting for the speech model to finish downloading"), force=True,
                         step="model")
            while self.setup_busy():
                if job.cancel.is_set():
                    raise Cancelled()
                time.sleep(0.5)
        settings = self.settings()
        for again in (False, True):
            engine = self._use_engine(settings)
            stopped: Optional[BaseException] = None
            try:
                transcript = self._run_engine(engine, job, settings)
            except Exception as exc:
                if not _card_stopped(engine, exc):
                    raise
                stopped = exc
            finally:
                self._done_with_engine()
                self._let_fallback_go(engine)
            if stopped is None:
                break
            # the graphics card failed in the middle of the lecture (a driver reset or update, a fault): such an error
            # lasts until the app closes, so every later lecture would fail on that engine within seconds. The lecture
            # is transcribed once more with a fresh one, which loads on the processor when the card stays broken.
            self.log.warning("The graphics card stopped working while %s was transcribed (%s)%s", job.id, stopped,
                             "" if again else "; the speech model loads again")
            self._card_stopped = self._card_stopped or not _out_of_memory(stopped)
            job.own_speed = False
            self._drop_engine(engine)
            if again:                     # in plain words, not CUDA's (it failed again with the fresh model)
                raise ModelFailed(_card_stopped_text(stopped)) from stopped
        self._drop_audio(job)
        transcript.delivery_id = job.id
        path = store.save_transcript(transcript)
        quality = (transcript.meta or {}).get("quality") or {}
        warnings = quality.get("warnings") or []
        job.notes += [w["message"] for w in warnings if isinstance(w, dict) and w.get("message")]
        if job.local_path is None:
            self.lib.save_transcript(transcript, path, quality=quality.get("stats"))
            self.lib.set_warnings(job.id, warnings)
        # saved: its checkpoint goes only now (a save that failed went on from it the next time)
        from .engine.checkpoint import discard

        discard(job.id)
        self.note_unwritten(job.id, True)         # until its files are written (closing now writes them next start)
        # its slides and files are finished by another thread: this one takes the next lecture meanwhile. Its step
        # moves on now, so a wait behind another lecture's slides doesn't count as transcribing (Job.metrics)
        if job.slides is not None:
            self._update(job, "writing", 0.97, t("Finishing slides"), force=True, step="slides")
        else:
            self._update(job, "writing", 0.99, t("Writing files"), force=True)
        self._finishing.put((job, warnings))

    SLIDES_WAIT_S = 900

    def _wait_slides(self, job: Job) -> None:
        """The slide capture still going on once the lecture is transcribed: how far it is through the screen recording
        is said each second ("Finishing slides: 40% of the screen recording read"), never a step that just sits."""
        end = time.monotonic() + self.SLIDES_WAIT_S
        while True:
            try:
                job.slides.result(timeout=min(1.0, max(0.01, end - time.monotonic())))
                return
            except FutureTimeout:
                if time.monotonic() >= end:
                    raise
                if job.slides_read > 0:
                    self._update(job, "writing", 0.97, t("Finishing slides: {pct} of the screen recording read",
                                                         pct=pct(job.slides_read)), step="slides")

    def _finish_loop(self) -> None:
        """Finishes transcribed lectures one after the other: waits for their slides, writes their files."""
        while True:
            job, warnings = self._finishing.get()
            try:
                self._complete(job, warnings)
            except Exception as exc:  # noqa: BLE001
                self.log.exception("Finishing %s failed", job.id)
                self._finish(job, "failed", plain_error(exc))

    def _complete(self, job: Job, warnings: list) -> None:
        settings = self.settings()
        if job.slides is not None:
            self._update(job, "writing", 0.97, t("Finishing slides"), force=True, step="slides")
            try:
                self._wait_slides(job)
            except Exception as exc:  # noqa: BLE001 - slides are optional
                self.log.warning("Slides for %s failed: %s", job.id, exc)
                job.notes.append(t("The slides couldn't be captured; the transcript is complete."))
        self._update(job, "writing", 0.99, t("Writing files"), force=True)
        try:
            self.write_outputs(job.id)
            self.note_unwritten(job.id, False)
        except OSError as exc:            # the transcript is saved: its files are written again at the next start
            self.log.warning("Writing the files of %s failed: %s", job.id, exc)
            self.note_unwritten(job.id, True)
            job.unwritten = True
            if job.local_path is None:
                self.lib.set_warnings(job.id, warnings + [{
                    "code": "not_written", "level": "warning",
                    "message": t("Transcribed, but its files in your IEUStudia folder could not be written. {error} "
                                 "IEUStudia tries again the next time it starts; you can also choose another folder in "
                                 "Settings → Files.", error=plain_error(exc))}])
        if job.audio_path and not settings.keep_audio:
            for p in (job.audio_path,):
                try:
                    Path(p).unlink(missing_ok=True)
                except OSError:
                    pass
        self._finish(job, "done")
