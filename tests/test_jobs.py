"""JobManager with fake Panopto / audio / engine: statuses, fallbacks, cancel, cleanup."""

import datetime as dt
import json
import threading
import time
import types

import numpy as np
import pytest

from studia import jobs as jobs_mod
from studia.config import Settings
from studia.domain import Course, Recording, Segment, Transcript
from studia.library import Library
from studia.panopto import MediaSource, NotReady
from studia.paths import audio_cache_dir
from studia.quality import NoSpeech


def wait_for(cond, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


class FakeSync:
    def worker_session(self):
        return object()

    def absorb(self, s):
        pass

    def ensure_panopto(self, cid=None):
        pass


class FakePanopto:
    sources = []                   # list[MediaSource], set per test
    downloads: list = []
    gate = None                    # threading.Event to hold downloads
    not_ready = False
    no_sound_header: set = set()   # streams whose header lists no sound track

    def __init__(self, session):
        pass

    def delivery_info(self, did):
        return {"Delivery": {"Duration": 60.0, "SessionName": "x"}}

    def media_sources(self, did, info):
        if FakePanopto.not_ready:
            raise NotReady("Panopto is still processing this recording.")
        return list(FakePanopto.sources)

    def screen_sources(self, info):
        return []

    def has_sound(self, src):
        return False if src.url in FakePanopto.no_sound_header else None    # None: can't tell, downloaded as before

    def download(self, src, dest, progress, cancel=None, prefer="audio"):
        if FakePanopto.gate is not None:
            FakePanopto.gate.wait(10)
        FakePanopto.downloads.append(src.kind)
        progress(5, 10)
        dest.write_text("silent" if "silent" in src.url else "mute" if "mute" in src.url else "loud")
        progress(10, 10)
        return dest


class FakeEngine:
    loaded = True

    def __init__(self, fail=None):
        self.fail = fail
        self.audio_seen = []
        self.unloaded = False

    def ensure_model(self, progress, cancel=None):
        progress("model-load", 1.0, "ready")

    def unload(self):
        self.unloaded = True

    def transcribe(self, audio, progress, cancel, meta):
        self.audio_seen.append(float(np.max(np.abs(audio))))
        if self.fail:
            raise self.fail
        progress("transcribe", 0.5, "half")
        return Transcript(delivery_id=meta["delivery_id"], segments=[Segment(0, 1, "hello", "en", 0.9)],
                          duration_s=1, speech_s=1, language_seconds={"en": 1}, model="m", device="cpu",
                          engine_version="t", created_at="2026-10-20T10:00:00", meta=dict(meta, quality={}))


@pytest.fixture
def env(tmp_path, monkeypatch):
    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="c", title="t", code="EEE 215", name="n")])
    lib.merge_recordings("c", [Recording(delivery_id=d, course_id="c", folder_id="f", folder_path="", name=d,
                                         start=dt.datetime(2026, 10, 13, 11, i), duration_s=2400, viewer_url="u")
                               for i, d in enumerate(["r1", "r2"])], first_sync=True)
    FakePanopto.sources = [MediaSource("audio_podcast", "https://x/loud")]
    FakePanopto.downloads = []
    FakePanopto.gate = None
    FakePanopto.not_ready = False
    FakePanopto.no_sound_header = set()
    monkeypatch.setattr(jobs_mod, "Panopto", FakePanopto)
    monkeypatch.setattr(jobs_mod, "_memory_gb", lambda: 8.0)     # DECODED_MAX places, whatever this PC has
    monkeypatch.setattr(jobs_mod, "decode_audio", lambda p, cancel=None: np.full(
        16000, 0.0 if open(p).read() == "silent" else 0.5, dtype=np.float32))
    events, written = [], []
    state = {"engine": FakeEngine()}

    def make(**kw):
        settings = Settings(**{"output_dir": str(tmp_path / "out"), "parallel_downloads": 1, "capture_slides": False,
                               **kw})
        return jobs_mod.JobManager(lib, FakeSync(), lambda: settings, lambda s: state["engine"], events.append,
                                   lambda did: written.append(did) or {}, lambda *a: [])

    return {"lib": lib, "events": events, "written": written, "make": make, "state": state, "mp": monkeypatch}


def finals(events, did):
    return [e for e in events if e.get("type") == "job" and e.get("id") == did and e.get("final")]


def test_happy_path_writes_files_and_cleans_audio(env):
    jm = env["make"]()
    jm.enqueue(["r1"])
    assert wait_for(lambda: finals(env["events"], "r1"))
    assert finals(env["events"], "r1")[0]["stage"] == "done"
    assert env["lib"].get_recording("r1").status == "done"
    assert env["written"] == ["r1"]
    stages = {e["stage"] for e in env["events"] if e.get("type") == "job" and e.get("id") == "r1"}
    assert {"queued", "downloading", "transcribing", "done"} <= stages
    assert not list(audio_cache_dir().glob("r1.*"))           # keep_audio=False


def test_not_ready_marks_processing(env):
    FakePanopto.not_ready = True
    jm = env["make"]()
    jm.enqueue(["r1"])
    assert wait_for(lambda: finals(env["events"], "r1"))
    rec = env["lib"].get_recording("r1")
    assert rec.status == "processing" and "processing" in rec.error


def test_silent_podcast_falls_back_to_loud_camera_stream(env):
    FakePanopto.sources = [MediaSource("audio_podcast", "https://x/silent"),
                           MediaSource("video_podcast", "https://x/silent-video"),
                           MediaSource("hls", "https://x/dv.m3u8")]
    env["mp"].setattr(jobs_mod.hls, "audio_peak_dbfs", lambda s, url: -6.0)
    jm = env["make"]()
    jm.enqueue(["r1"])
    assert wait_for(lambda: finals(env["events"], "r1"))
    assert FakePanopto.downloads == ["audio_podcast", "hls"]   # the video podcast is never fetched
    assert env["state"]["engine"].audio_seen == [0.5]
    assert not list(audio_cache_dir().glob("r1.*"))           # nor is the silent podcast's file left behind


def test_a_stream_without_any_sound_track_falls_back_to_the_next_one(env):
    from studia.audio import NoAudioTrack

    fake = jobs_mod.decode_audio

    def decode(path, cancel=None):
        if open(path).read() == "mute":              # a video-only stream (made from the screen recording)
            raise NoAudioTrack("This file has no audio track.")
        return fake(path, cancel)

    env["mp"].setattr(jobs_mod, "decode_audio", decode)
    FakePanopto.sources = [MediaSource("video_podcast", "https://x/mute"), MediaSource("hls", "https://x/dv.m3u8")]
    jm = env["make"]()
    jm.enqueue(["r1"])
    assert wait_for(lambda: finals(env["events"], "r1"))
    assert finals(env["events"], "r1")[0]["stage"] == "done" and FakePanopto.downloads == ["video_podcast", "hls"]
    FakePanopto.sources = [MediaSource("video_podcast", "https://x/mute")]   # no stream with sound: said plainly
    jm.enqueue(["r2"])
    assert wait_for(lambda: finals(env["events"], "r2"))
    rec = env["lib"].get_recording("r2")
    assert rec.status == "failed" and "no sound for this recording" in rec.error
    assert not list(audio_cache_dir().glob("r2.*"))


def test_a_stream_without_sound_hides_no_download_error_and_isnt_fetched_again(env):
    import requests

    from studia.audio import NoAudioTrack

    fake, tried = jobs_mod.decode_audio, []

    def decode(path, cancel=None):
        if open(path).read() == "mute":
            raise NoAudioTrack("This file has no audio track.")
        return fake(path, cancel)

    def download(self, src, dest, progress, cancel=None, prefer="audio"):
        tried.append(src.kind)
        if "broken" in src.url:
            raise requests.ConnectionError("Can't reach panopto.ieu.edu.tr.")
        dest.write_text("mute")
        return dest

    env["mp"].setattr(jobs_mod, "decode_audio", decode)
    env["mp"].setattr(FakePanopto, "download", download)
    env["mp"].setattr(jobs_mod.threading, "Timer", lambda s, fn, args=(): types.SimpleNamespace(start=lambda: fn(*args)))
    FakePanopto.sources = [MediaSource("audio_podcast", "https://x/broken"), MediaSource("video_podcast", "https://x/mute")]
    jm = env["make"]()
    jm.enqueue(["r1"])
    assert wait_for(lambda: finals(env["events"], "r1"))
    rec = env["lib"].get_recording("r1")
    assert rec.status == "failed" and rec.error.startswith("The download failed")     # the real problem, retried
    assert tried == ["audio_podcast", "video_podcast", "audio_podcast", "audio_podcast"]  # the mute one only once


def test_no_speech_is_reported_with_a_warning(env):
    env["state"]["engine"] = FakeEngine(fail=NoSpeech("This recording has no sound on Panopto."))
    jm = env["make"]()
    jm.enqueue(["r1"])
    assert wait_for(lambda: finals(env["events"], "r1"))
    assert env["lib"].get_recording("r1").status == "failed"
    assert env["lib"].warnings("r1")[0]["code"] == "no_speech"
    assert finals(env["events"], "r1")[-1]["no_sound"] is True        # the page shows it in any language at once
    assert not list(audio_cache_dir().glob("r1.*"))                    # trying again finds the same: not kept


def test_a_stream_whose_header_lists_no_sound_is_not_downloaded(env):
    FakePanopto.sources = [MediaSource("video_podcast", "https://x/video"), MediaSource("hls", "https://x/dv.m3u8"),
                           MediaSource("hls", "https://x/object.m3u8")]
    FakePanopto.no_sound_header = {s.url for s in FakePanopto.sources}
    jm = env["make"]()
    jm.enqueue(["r1"])
    assert wait_for(lambda: finals(env["events"], "r1"))
    rec = env["lib"].get_recording("r1")
    assert FakePanopto.downloads == []                         # the same answer, without hundreds of MB
    assert rec.status == "failed" and "no sound for this recording" in rec.error
    assert env["lib"].warnings("r1")[0]["code"] == "no_speech" and finals(env["events"], "r1")[-1]["no_sound"]
    FakePanopto.no_sound_header = {"https://x/video"}          # the camera stream has sound: only it is fetched
    jm.enqueue(["r2"])
    assert wait_for(lambda: finals(env["events"], "r2"))
    assert finals(env["events"], "r2")[-1]["stage"] == "done" and FakePanopto.downloads == ["hls"]


def test_cancelled_lectures_leave_no_audio_behind_but_closing_and_keep_audio_keep_it(env):
    env["state"]["engine"] = BlockingEngine()
    jm = env["make"]()
    jm.enqueue(["r1"])
    assert wait_for(lambda: jm.job("r1") is not None and jm.job("r1").stage == "transcribing")
    assert list(audio_cache_dir().glob("r1.*"))
    jm.cancel("r1")
    assert wait_for(lambda: finals(env["events"], "r1"))
    assert not list(audio_cache_dir().glob("r1.*"))
    closing = env["make"]()
    closing.enqueue(["r2"])
    assert wait_for(lambda: closing.job("r2") is not None and closing.job("r2").stage == "transcribing")
    closing.shutdown()                                             # the next start goes on with it
    assert wait_for(lambda: not closing.busy())
    assert list(audio_cache_dir().glob("r2.*"))
    (audio_cache_dir() / "r2.m4a").unlink()
    keep = env["make"](keep_audio=True)                            # "Keep downloaded audio": kept
    env["state"]["engine"] = BlockingEngine()
    keep.enqueue(["r2"])
    assert wait_for(lambda: keep.job("r2") is not None and keep.job("r2").stage == "transcribing")
    keep.cancel("r2")
    assert wait_for(lambda: not keep.busy()) and list(audio_cache_dir().glob("r2.*"))


def test_model_is_unloaded_when_idle(env):
    env["mp"].setattr(jobs_mod, "IDLE_UNLOAD_S", 0.3)
    jm = env["make"]()
    jm.enqueue(["r1"])
    assert wait_for(lambda: finals(env["events"], "r1"))
    assert wait_for(lambda: env["state"]["engine"].unloaded, timeout=5)
    assert not jm.engine_loaded()


def test_the_offline_ai_frees_the_speech_model_only_when_no_lecture_waits(env):
    from studia import local_llm

    env["mp"].setattr(local_llm, "make_room", None)
    jm = env["make"]()
    jm.enqueue(["r1"])
    assert wait_for(lambda: finals(env["events"], "r1"))
    assert local_llm.make_room == jm.unload_engine and jm.engine_loaded()
    FakePanopto.gate = threading.Event()          # r2 waits to download
    jm.enqueue(["r2"])
    assert not local_llm.make_room() and not env["state"]["engine"].unloaded
    FakePanopto.gate.set()
    assert wait_for(lambda: finals(env["events"], "r2"))
    assert local_llm.make_room() and env["state"]["engine"].unloaded and not jm.engine_loaded()
    assert not local_llm.make_room()              # nothing left to free


def test_cancel_queued_then_requeue_keeps_tracking_new_job(env):
    FakePanopto.gate = threading.Event()          # hold r1 in "downloading"
    jm = env["make"]()
    jm.enqueue(["r1"])
    assert wait_for(lambda: jm.job("r1") is not None and jm.job("r1").stage == "downloading")
    jm.enqueue(["r2"])                            # waits behind r1 (one download worker)
    jm.cancel("r2")
    assert env["lib"].get_recording("r2").status == "available"
    jm.enqueue(["r2"])                            # re-queue before the worker reaches the stale entry
    FakePanopto.gate.set()
    assert wait_for(lambda: any(e["stage"] == "done" for e in finals(env["events"], "r2")))
    assert env["lib"].get_recording("r2").status == "done"
    assert wait_for(lambda: not jm.busy())


def test_batch_progress_counts_finished_lectures_and_estimates_time_left(env):
    FakePanopto.gate = threading.Event()          # hold r2 (the newer one goes first) in "downloading"
    jm = env["make"]()
    jm.enqueue(["r1", "r2"])
    assert wait_for(lambda: jm.job("r2") is not None and jm.job("r2").stage == "downloading")
    b = jm.batch()
    assert b["finished"] == 0 and b["total"] == 2 and b["left_s"] is None     # no speed measured yet
    queue_events = [e for e in env["events"] if e.get("type") == "queue"]
    assert queue_events and queue_events[-1]["batch"]["total"] == 2
    jm._batch["started"] -= 120                   # pretend the first lecture took two minutes
    FakePanopto.gate.set()
    assert wait_for(lambda: finals(env["events"], "r2"))
    b = jm.batch()
    if b and b["finished"] == 1:
        assert b["total"] == 2 and b["left_s"] and b["left_s"] > 0
        assert b["done"] == 1 and b["failed"] == 0
    elif b:                                       # r1 finished as well, while the batch's files are still written
        assert b["finished"] == 2 and b["total"] == 2 and b["done"] == 2 and b["failed"] == 0
    assert wait_for(lambda: not jm.busy())
    assert jm.batch() == {}                       # idle: no batch
    FakePanopto.gate = threading.Event()          # held again, so it can't finish before it is counted
    jm.enqueue(["r1"])                            # a new batch starts from zero
    assert jm.batch()["finished"] == 0 and jm.batch()["total"] == 1
    FakePanopto.gate.set()
    assert wait_for(lambda: not jm.busy())


class BlockingEngine(FakeEngine):
    """A slow processor: each lecture takes until the test lets it go."""

    def __init__(self):
        super().__init__()
        self.go = threading.Event()

    def transcribe(self, audio, progress, cancel, meta):
        while not self.go.wait(0.05):
            if cancel.is_set():
                from studia.cancel import Cancelled
                raise Cancelled()
        return super().transcribe(audio, progress, cancel, meta)


def test_downloads_run_ahead_but_few_lectures_wait_decoded_and_cancel_ends_a_waiting_one(env):
    ids = [f"r{i}" for i in range(1, 7)]
    env["lib"].merge_recordings("c", [Recording(delivery_id=d, course_id="c", folder_id="f", folder_path="", name=d,
                                                start=dt.datetime(2026, 10, 14, 9, i), duration_s=2400, viewer_url="u")
                                      for i, d in enumerate(ids[2:])], first_sync=False)
    env["state"]["engine"] = engine = BlockingEngine()
    awake = []
    env["mp"].setattr(jobs_mod, "KeepAwake", lambda reason: type("K", (), {"on": lambda s: awake.append(True),
                                                                            "off": lambda s: awake.append(False)})())
    jm = env["make"](parallel_downloads=3)
    jm.enqueue(ids)
    assert awake == [True]
    assert wait_for(lambda: sum(1 for i in ids if (jm.job(i) and jm.job(i).detail.startswith("Downloaded"))) >= 2)
    time.sleep(0.3)
    holding = [i for i in ids if jm.job(i) is not None and jm.job(i).audio is not None]
    assert len(holding) <= jobs_mod.DECODED_MAX                     # one transcribing, one ready
    waiting = next(i for i in ids if jm.job(i) and jm.job(i).stage == "queued" and jm.job(i).detail.startswith("Downloaded"))
    jm.cancel(waiting)
    assert jm.job(waiting) is None and env["lib"].get_recording(waiting).status == "available"   # at once
    engine.go.set()
    assert wait_for(lambda: not jm.busy(), timeout=20)
    assert {e["stage"] for i in ids if i != waiting for e in finals(env["events"], i)} == {"done"}
    assert awake[-1] is False                                       # the PC may sleep again


def test_a_transcript_whose_files_cannot_be_written_is_still_done(env):
    def locked(did):
        raise PermissionError(13, "Access is denied", r"C:\Users\x\OneDrive\EEE 215 - Study pack.md")

    lib = env["lib"]
    jm = jobs_mod.JobManager(lib, FakeSync(), lambda: Settings(parallel_downloads=1, capture_slides=False),
                             lambda s: env["state"]["engine"], env["events"].append, locked, lambda *a: [])
    jm.enqueue(["r1"])
    assert wait_for(lambda: finals(env["events"], "r1"))
    assert finals(env["events"], "r1")[0]["stage"] == "done" and lib.get_recording("r1").status == "done"
    (w,) = lib.warnings("r1")
    assert w["code"] == "not_written" and "Study pack.md" in w["message"] and "tries again" in w["message"]
    assert jm.unwritten() == ["r1"]


def test_transcribing_again_that_fails_keeps_the_earlier_transcript(env):
    env["lib"].set_status("r1", "done")
    env["state"]["engine"] = FakeEngine(fail=RuntimeError("boom"))
    jm = env["make"]()
    jm.enqueue(["r1"])
    assert wait_for(lambda: finals(env["events"], "r1"))
    f = finals(env["events"], "r1")[0]
    assert f["stage"] == "failed" and f["status"] == "done" and env["lib"].get_recording("r1").status == "done"


def test_a_full_disk_is_said_plainly_and_not_retried_as_a_connection_problem(env):
    def full(src, dest, progress, cancel=None, prefer="audio"):
        raise OSError(28, "No space left on device")

    env["mp"].setattr(FakePanopto, "download", lambda self, *a, **k: full(*a, **k))
    FakePanopto.sources = [MediaSource("audio_podcast", "https://x/loud")]
    jm = env["make"]()
    jm.enqueue(["r1"])
    assert wait_for(lambda: finals(env["events"], "r1"))
    rec = env["lib"].get_recording("r1")
    assert rec.status == "failed" and rec.error.startswith("Your disk is full")
    assert not any("retrying" in (e.get("detail") or "") for e in env["events"] if e.get("id") == "r1")


def test_the_time_left_is_given_apart_from_the_detail():
    j = jobs_mod.Job(id="x", title="t", detail="Transcribing passage 40 of 600 · ~1500s left")
    v = j.view()
    assert v["detail"] == "Transcribing passage 40 of 600" and v["eta_s"] == 1500


def test_files_that_could_not_be_written_are_written_at_the_next_start(env):
    import types

    from studia.services import Services

    lib = env["lib"]
    lib.set_warnings("r1", [{"code": "not_written", "level": "warning", "message": "x"}, {"code": "gaps", "level": "info"}])
    jm = env["make"]()
    jm.note_unwritten("r1", True)
    jm.note_unwritten("r2", True)
    calls = []

    def write(did):
        calls.append(did)
        if did == "r2":
            raise PermissionError(13, "Access is denied")
        return {}

    Services._write_unwritten(types.SimpleNamespace(jobs=jm, lib=lib, write_outputs=write))
    assert calls == ["r1", "r2"] and jm.unwritten() == ["r2"]           # r2 is tried again next time
    assert [w["code"] for w in lib.warnings("r1")] == ["gaps"]


def test_a_lecture_cancelled_while_it_waits_stays_cancelled_when_its_turn_comes(env):
    env["mp"].setattr(jobs_mod, "DECODED_MAX", 1)
    env["state"]["engine"] = engine = BlockingEngine()
    jm = env["make"](parallel_downloads=2)
    jm.enqueue(["r1", "r2"])

    def waiting():                                     # whichever download finished second waits for the place
        return next((i for i in ("r1", "r2") if jm.job(i) and jm.job(i).detail.startswith("Downloaded")), None)

    assert wait_for(lambda: waiting() is not None)
    cancelled = waiting()
    jm.cancel(cancelled)
    cancelled_at = len(env["events"])
    engine.go.set()                                    # the other finishes: the place comes free, but this one is cancelled
    assert wait_for(lambda: not jm.busy())
    time.sleep(0.3)
    later = [e for e in env["events"][cancelled_at:] if e.get("type") == "job" and e.get("id") == cancelled]
    assert not later and env["lib"].get_recording(cancelled).status == "available"
    assert jm._places == 1 and not jm._place_wait       # the place was given back


def test_cancel_while_panopto_cant_be_reached_is_a_cancel_not_a_failure(env):
    import requests

    started = threading.Event()

    def unreachable(self, src, dest, progress, cancel=None, prefer="audio"):
        started.set()
        cancel.wait(5)                                        # Cancel is pressed while it tries
        raise requests.ConnectionError("Can't reach panopto.ieu.edu.tr.")

    env["mp"].setattr(FakePanopto, "download", unreachable)
    jm = env["make"]()
    jm.enqueue(["r1"])
    assert started.wait(5)
    jm.cancel("r1")
    assert wait_for(lambda: finals(env["events"], "r1"))
    assert finals(env["events"], "r1")[0]["stage"] == "cancelled" and env["lib"].get_recording("r1").status == "available"


# ------------------------------------------------------------------ order, pause, and the queue across restarts
def _more(env, ids, day=15):
    env["lib"].merge_recordings("c", [Recording(delivery_id=d, course_id="c", folder_id="f", folder_path="", name=d,
                                                start=dt.datetime(2026, 10, day, 8 + i, 0), duration_s=2400, viewer_url="u")
                                      for i, d in enumerate(ids)], first_sync=False)


class StepEngine(FakeEngine):
    """A slow processor that finishes one lecture each time the test says so, and notes the order they started in."""

    def __init__(self):
        super().__init__()
        self.started = []
        self.steps = threading.Semaphore(0)

    def transcribe(self, audio, progress, cancel, meta):
        self.started.append(meta["delivery_id"])
        while not self.steps.acquire(timeout=0.05):
            if cancel.is_set():
                from studia.cancel import Cancelled
                raise Cancelled()
        return super().transcribe(audio, progress, cancel, meta)


def test_lectures_asked_for_go_before_a_sync_batch_and_do_next_goes_first(env):
    _more(env, ["b4", "b3", "b2", "b1", "u1"])                    # the batch goes newest first: b1, b2, b3, b4
    env["state"]["engine"] = engine = StepEngine()
    jm = env["make"](parallel_downloads=1)
    jm.enqueue(["b1", "b2", "b3", "b4"], batch=True)              # a full sync's batch
    assert wait_for(lambda: engine.started == ["b1"])
    assert wait_for(lambda: jm.job("b3") is not None and jm.job("b3").detail.startswith("Downloaded"))  # b2 is ready
    jm.enqueue(["u1"])                                             # Transcribe on a class day
    assert wait_for(lambda: jm.job("u1").detail.startswith("Downloaded"))       # downloaded past the batch's b3, b4
    assert jm.first("b4") and not jm.first("nope")                 # "Do next"
    snap = jm.snapshot()
    assert [j["id"] for j in snap] == ["b1", "b4", "u1", "b2", "b3"] and [j["first"] for j in snap][:2] == [False, True]

    def waiting_for_a_place():
        with jm._q:
            return {j.id for j in jm._place_wait.values()}

    # before a lecture ends, the ones after it have got as far as they can (b2 is ready; the others wait for a place
    # to decode their audio), so the order doesn't depend on how fast the threads are
    settled = {1: lambda: {"b3", "u1"} <= waiting_for_a_place(),
               2: lambda: {"b3", "b4"} <= waiting_for_a_place()}       # b4 moved up: the extra lane downloaded it
    for n in range(1, 6):
        assert wait_for(lambda: len(engine.started) == n)
        assert wait_for(settled.get(n, lambda: True))
        engine.steps.release()
    assert wait_for(lambda: not jm.busy())
    assert engine.started == ["b1", "u1", "b4", "b2", "b3"]
    assert jm._places == jobs_mod.DECODED_MAX and not jm._held and not jm._place_wait


def test_do_next_beats_a_lecture_that_is_ready_while_it_is_decoded(env):
    """Decoding takes a while: the lecture moved up gets the place that comes free, and the one already ready waits."""
    _more(env, ["b4", "b3", "b2", "b1", "u1"])
    slow = jobs_mod.decode_audio

    def decode(path, cancel=None):
        time.sleep(0.4)
        return slow(path, cancel)

    env["mp"].setattr(jobs_mod, "decode_audio", decode)
    env["state"]["engine"] = engine = StepEngine()
    jm = env["make"](parallel_downloads=1)
    jm.enqueue(["b1", "b2", "b3", "b4"], batch=True)
    assert wait_for(lambda: engine.started == ["b1"])
    assert wait_for(lambda: jm.job("b3") is not None and jm.job("b3").detail.startswith("Downloaded"))
    assert jm.first("b3")
    assert jm.enqueue(["b4", "u1"]) == 2                           # asked for now: b4 moves ahead of the batch
    for n in range(1, 6):
        assert wait_for(lambda: len(engine.started) == n, timeout=15)
        time.sleep(0.6)                                            # the next ones download and decode meanwhile
        engine.steps.release()
    assert wait_for(lambda: not jm.busy(), timeout=15)
    assert engine.started[:2] == ["b1", "b3"] and engine.started.index("b2") > engine.started.index("b4")


def test_the_queue_is_newest_lecture_first(env):
    """A batch goes by the recordings' start, newest first, whichever sync queued it; one request's lectures go newest
    first too, before the batch, and "Do next" goes before them all."""
    _more(env, ["o1", "o2"], day=14)                               # 14 Oct 08:00 and 09:00
    _more(env, ["n1", "n2"], day=16)
    _more(env, ["h1", "h2"], day=12)                               # a class day's two parts, asked for by hand
    env["state"]["engine"] = engine = StepEngine()
    jm = env["make"](parallel_downloads=1)
    jm.enqueue(["r1"])
    assert wait_for(lambda: engine.started == ["r1"])
    jm.enqueue(["o1", "o2"], batch=True)
    jm.enqueue(["n1", "n2"], batch=True)                           # a later sync's newer lectures go before older ones
    jm.enqueue(["h1", "h2"])
    assert [j["id"] for j in jm.snapshot()] == ["r1", "h2", "h1", "n2", "n1", "o2", "o1"]
    assert jm.enqueue(["o1"]) == 1                                 # asked for by hand: after h1, before the batch
    assert jm.first("n1")
    assert [j["id"] for j in jm.snapshot()] == ["r1", "n1", "h2", "h1", "o1", "n2", "o2"]
    saved = json.loads(env["lib"].get_meta(jobs_mod.QUEUE_KEY))
    assert saved == ["n1", "r1", "h2", "h1", "o1", "n2", "o2"]     # what is offered again after a restart
    jm.cancel(None)
    assert wait_for(lambda: not jm.busy())


def test_a_lecture_reports_its_steps_model_and_notes_for_the_jobs_pane(env):
    class Engine(FakeEngine):
        model_name = "large-v3-turbo"
        device_info = {"device": "cpu", "warning": "The graphics card couldn't be used, so it runs on the processor."}

        def transcribe(self, audio, progress, cancel, meta):
            progress("transcribe", 0.5, "Transcribing passage 3 of 4 · ~5s left")
            time.sleep(0.2)
            progress("transcribe", 0.94, "Checking 1 unclear passage again · ~2s left")    # (not a count of passages)
            return super().transcribe(audio, progress, cancel, meta)

    env["state"]["engine"] = Engine()
    jm = env["make"]()
    jm.enqueue(["r1"])
    assert wait_for(lambda: finals(env["events"], "r1"))
    e = finals(env["events"], "r1")[0]
    steps = [s for s, _ in e["steps"]]
    assert steps[0] == "wait" and steps[-1] == "write" and "place" not in steps    # a place was free at once
    assert steps.index("download") < steps.index("decode") < steps.index("model") < steps.index("transcribe")
    assert [at for _, at in e["steps"]] == sorted(at for _, at in e["steps"]) and e["age_s"] >= e["steps"][-1][1]
    assert e["model"] == "large-v3-turbo" and e["device"] == "cpu" and e["anomalies"] == []
    assert e["notes"] == ["The graphics card couldn't be used, so it runs on the processor."]
    assert e["dl"] is None and e["slides"] is False                # a tiny download: no sizes to show
    # what the pane shows of it once finished: its audio's length, its passages and how long transcribing took
    assert e["audio_s"] == 1.0 and e["passages"] == [3, 4]
    at = dict((s, a) for s, a in e["steps"])
    assert 0.2 <= e["transcribe_s"] <= at["write"] - at["transcribe"] + 0.1 and e["transcribe_s"] <= e["age_s"]
    assert not any("passages" in x for x in env["events"] if x.get("type") == "job" and not x.get("final"))


def test_the_two_waits_are_two_steps(env):
    """A downloaded lecture waits for a place to decode its audio ("place"); a decoded one waits for the speech model
    ("turn"). The pane names them apart."""
    env["mp"].setattr(jobs_mod, "DECODED_MAX", 1)
    env["state"]["engine"] = engine = BlockingEngine()
    jm = env["make"](parallel_downloads=2)
    jm.enqueue(["r1", "r2"])
    assert wait_for(lambda: engine_running(jm) and any(jm.job(i).step == "place" for i in ("r1", "r2")))
    waiting = next(i for i in ("r1", "r2") if jm.job(i).step == "place")
    assert jm.job(waiting).detail == "Downloaded — waiting to prepare the audio"
    engine.go.set()
    assert wait_for(lambda: not jm.busy())
    steps = [s for s, _ in finals(env["events"], waiting)[0]["steps"]]
    assert steps.index("download") < steps.index("place") < steps.index("decode")

    env["mp"].setattr(jobs_mod, "DECODED_MAX", 2)                 # room for two: the second one waits decoded
    env["state"]["engine"] = engine = BlockingEngine()
    _more(env, ["r3", "r4"])
    jm = env["make"](parallel_downloads=2)
    jm.enqueue(["r3", "r4"])
    assert wait_for(lambda: engine_running(jm) and any(jm.job(i).step == "turn" for i in ("r3", "r4")))
    ready = next(i for i in ("r3", "r4") if jm.job(i).step == "turn")
    assert jm.job(ready).detail == "Downloaded — waiting to be transcribed" and jm.job(ready).holds_audio
    engine.go.set()
    assert wait_for(lambda: not jm.busy())
    steps = [s for s, _ in finals(env["events"], ready)[0]["steps"]]
    assert "place" not in steps and steps.index("decode") < steps.index("turn") < steps.index("model")


def engine_running(jm):
    with jm._lock:
        return any(j.where == "run" for j in jm._jobs.values())


def test_a_lecture_that_ends_as_more_is_queued_is_not_kept(env, monkeypatch):
    """A lecture that ended (cancelled, done) is not written back into what is still to do by a queue change."""
    jm = env["make"]()
    env["state"]["engine"] = BlockingEngine()
    _more(env, ["r3"])
    jm.enqueue(["r1"])
    assert wait_for(lambda: jm.job("r1") is not None and jm.job("r1").stage == "transcribing")
    real = env["lib"].set_status

    def slow(did, status, error=""):                             # the cancel is caught between its steps
        if did == "r1" and status == "available":
            jm.enqueue(["r3"])
        return real(did, status, error)

    monkeypatch.setattr(env["lib"], "set_status", slow)
    jm.cancel("r1")
    assert wait_for(lambda: jm.job("r1") is None)
    jm.cancel("r3")
    assert wait_for(lambda: not jm.busy())
    assert env["lib"].get_meta(jobs_mod.QUEUE_KEY) == "[]" and env["make"]().leftover() == []


def test_pause_stops_between_passages_and_cancel_still_works(env):
    import re

    class Passages(FakeEngine):
        def __init__(self):
            super().__init__()
            self.done = 0

        def transcribe(self, audio, progress, cancel, meta):
            for i in range(40):
                progress("transcribe", 0.05 + 0.88 * i / 40, f"Transcribing passage {i} of 40 · ~{40 - i}s left")
                self.done += 1
                time.sleep(0.02)
            return super().transcribe(audio, progress, cancel, meta)

    env["state"]["engine"] = engine = Passages()
    awake = []
    env["mp"].setattr(jobs_mod, "KeepAwake", lambda reason: type("K", (), {"on": lambda s: awake.append(True),
                                                                            "off": lambda s: awake.append(False)})())
    jm = env["make"]()
    jm.enqueue(["r1", "r2"])
    assert wait_for(lambda: engine.done >= 3)
    jm.pause(True)
    assert jm.paused() and awake[-1] is False                     # the PC may sleep while paused
    time.sleep(0.15)
    stopped = engine.done
    time.sleep(0.6)
    assert engine.done == stopped                                  # nothing goes on
    assert [e for e in env["events"] if e.get("type") == "queue"][-1]["paused"] is True
    resumed_at = len(env["events"])
    jm.pause(False)
    assert awake[-1] is True
    assert wait_for(lambda: not jm.busy())
    assert {f["stage"] for i in ("r1", "r2") for f in finals(env["events"], i)} == {"done"} and not jm.paused()
    # the engine counts the time paused as time spent: it is taken out of its estimates after the pause (the first
    # message after Continue was estimated before it). r2, the newer one, went first.
    after = [e for e in env["events"][resumed_at:] if e.get("type") == "job" and e.get("id") == "r2"
             and "passage" in (e.get("detail") or "") and e.get("eta_s") is not None]
    left = [(e["eta_s"], 40 - int(re.search(r"passage (\d+)", e["detail"]).group(1))) for e in after]
    assert left and left[0][0] == left[0][1] and any(eta < raw for eta, raw in left[1:])
    base = engine.done
    jm.enqueue(["r1"])
    assert wait_for(lambda: engine.done >= base + 2)
    jm.pause(True)
    jm.cancel("r1")                                                # cancel while paused
    assert wait_for(lambda: len(finals(env["events"], "r1")) == 2)
    assert finals(env["events"], "r1")[-1]["stage"] == "cancelled" and not jm.paused()     # the pause ends with the queue


def test_a_paused_lecture_cancelled_while_it_waits_for_its_turn_never_reaches_the_model(env):
    class Counting(FakeEngine):
        loads = 0

        def ensure_model(self, progress, cancel=None):
            Counting.loads += 1
            super().ensure_model(progress, cancel)

    env["state"]["engine"] = engine = Counting()
    FakePanopto.gate = gate = threading.Event()
    jm = env["make"]()
    jm.enqueue(["r1"])
    jm.pause(True)
    gate.set()
    assert wait_for(lambda: jm.job("r1") is not None and jm.job("r1").where == "run")    # its turn, held by the pause
    time.sleep(0.3)
    jm.cancel("r1")
    assert wait_for(lambda: not jm.busy())
    time.sleep(0.3)
    assert [f["stage"] for f in finals(env["events"], "r1")] == ["cancelled"]
    assert Counting.loads == 0 and engine.audio_seen == []


def test_no_speed_for_a_lecture_gone_on_from_a_checkpoint_or_sharing_the_card(env):
    class Resumed(FakeEngine):
        resumed_passages = 30                       # 30 of its passages came from the checkpoint

    env["state"]["engine"] = Resumed()
    jm = env["make"]()
    jm.enqueue(["r1"])
    assert wait_for(lambda: finals(env["events"], "r1"))
    e = finals(env["events"], "r1")[0]
    assert e["stage"] == "done" and e["transcribe_s"] is None and e["audio_s"] == 1.0
    # two lectures on the card at once: neither one's time is its own
    a, b = jobs_mod.Job(id="a", title="a"), jobs_mod.Job(id="b", title="b")
    for j in (a, b):
        j.where, j.step, j.stage = "run", "transcribe", "transcribing"
        jm._jobs[j.id] = j
    jm._engine_progress(a, "transcribe", 0.5, "Transcribing passage 8 of 40")
    assert a.metrics()["transcribe_s"] is None and b.metrics()["transcribe_s"] is None
    alone = jobs_mod.Job(id="c", title="c")
    alone.where, alone.step, alone.stage = "run", "transcribe", "transcribing"
    jm._jobs = {"c": alone}
    jm._engine_progress(alone, "transcribe", 0.5, "Transcribing passage 8 of 40")
    assert alone.metrics()["transcribe_s"] is not None


def test_the_lecture_on_the_card_comes_before_one_waiting_for_its_slides():
    j = lambda i, where, stage, seq: types.SimpleNamespace(id=i, where=where, stage=stage, rank=jobs_mod.NOW, seq=seq)  # noqa: E731
    order = [j("a", "run", "writing", 1), j("b", "run", "transcribing", 2), j("c", "gpu", "queued", 0)]
    assert [x.id for x in sorted(order, key=jobs_mod.JobManager._order)] == ["b", "a", "c"]


def test_the_queue_is_kept_when_the_app_closes_and_offered_again(env):
    _more(env, ["r3", "r4", "r5"])                                 # (r2 is a copy of r1: never offered)
    env["state"]["engine"] = BlockingEngine()
    jm = env["make"]()
    jm.enqueue(["r1", "r3"])
    jm.enqueue(["r4", "r5"], batch=True)
    assert wait_for(lambda: jm.job("r3") is not None and jm.job("r3").stage == "transcribing")   # the newer one
    jm.shutdown()                                                  # the app closes while r3 is being transcribed
    assert wait_for(lambda: not jm.busy())
    env["lib"].reset_interrupted()
    again = env["make"]()                                          # the next start
    assert again.leftover() == ["r3", "r1", "r5", "r4"]
    assert again.leftover_view() == {"count": 4, "days": 2, "audio_s": 9600, "courses": ["EEE 215"]}
    env["state"]["engine"] = FakeEngine()
    assert again.resume_leftover() == 4
    assert wait_for(lambda: not again.busy())
    assert again.leftover() == [] and env["lib"].get_meta(jobs_mod.QUEUE_KEY) == "[]"
    third = env["make"]()
    third.enqueue(["r1"])
    third.cancel("r1")                                             # cancelled by you: not offered again
    assert wait_for(lambda: not third.busy()) and third.leftover() == []
    env["state"]["engine"] = BlockingEngine()
    closing = env["make"]()
    closing.enqueue(["r3"])
    assert wait_for(lambda: closing.job("r3") is not None and closing.job("r3").stage == "transcribing")
    closing.shutdown()
    assert wait_for(lambda: not closing.busy())
    env["lib"].set_status("r3", "available")
    fourth = env["make"]()
    assert fourth.leftover() == ["r3"]
    fourth.forget_leftover()
    assert fourth.leftover() == [] and env["make"]().leftover() == []


def test_a_transcript_saved_before_its_files_are_written_is_written_at_the_next_start(env):
    """Closing while the slides are finished: the transcript is saved, and its files are noted as still to write."""
    gate, waiting = threading.Event(), threading.Event()

    def slides(job, *a):
        waiting.set()
        gate.wait(10)
        return []

    env["mp"].setattr(FakePanopto, "screen_sources", lambda self, info: [("https://x/screen", 0.0)])
    lib = env["lib"]
    jm = jobs_mod.JobManager(lib, FakeSync(), lambda: Settings(parallel_downloads=1, capture_slides=True),
                             lambda s: env["state"]["engine"], env["events"].append, lambda did: {}, slides)
    jm.enqueue(["r1"])
    assert waiting.wait(10) and wait_for(lambda: jm.job("r1") is not None and jm.job("r1").detail == "Finishing slides")
    assert jm.unwritten() == ["r1"]                  # if IEUStudia closed now, the next start writes its files
    gate.set()
    assert wait_for(lambda: not jm.busy())
    assert jm.unwritten() == [] and lib.get_recording("r1").status == "done"
