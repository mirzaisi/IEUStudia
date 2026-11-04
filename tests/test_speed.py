"""Full syncs keep the graphics card busy: one lecture at a time by default, a second one (GPU_WORKERS 2) shares the
one loaded model when the PC allows it, the threads that feed the card run as a normal program's, slides and files are
finished beside the next lecture, the pane doesn't say "Loading the speech model" for a model that is loaded, and the
places for decoded audio and the download threads follow the PC."""

import ctypes
import sys
import threading
import types

import pytest

from studia import jobs as jobs_mod
from studia import osutil
from studia.cancel import Cancelled
from studia.config import Settings
from studia.engine import transcriber as tr

from test_engine_pipeline import _fake_load
from test_jobs import FakeEngine, FakePanopto, FakeSync, env, finals, wait_for  # noqa: F401 - env is a fixture


class CardEngine(FakeEngine):
    """A model loaded on the graphics card; each lecture takes until the test lets it go."""
    device = "cuda"

    def __init__(self):
        super().__init__()
        self.go = threading.Event()
        self.now = self.most = 0
        self.count = threading.Lock()

    def ensure_model(self, progress, cancel=None):
        pass                                          # loaded: nothing to do, as the real engine

    def transcribe(self, audio, progress, cancel, meta):
        with self.count:
            self.now += 1
            self.most = max(self.most, self.now)
        try:
            while not self.go.wait(0.02):
                if cancel.is_set():
                    raise Cancelled()
            return super().transcribe(audio, progress, cancel, meta)
        finally:
            with self.count:
                self.now -= 1


@pytest.fixture
def roomy(env, monkeypatch):
    """A PC like the owner's: 16 GB, 20 logical processors, memory free, the offline AI not on the card."""
    monkeypatch.setattr(jobs_mod, "_memory_gb", lambda: 16.0)
    monkeypatch.setattr(jobs_mod, "_free_memory_gb", lambda: 8.0)
    monkeypatch.setattr(jobs_mod.os, "cpu_count", lambda: 20)
    monkeypatch.setattr(tr, "_ai_on_gpu", lambda: False)
    driven = []
    monkeypatch.setattr(osutil.impl(), "drive_card", lambda ids, on=True: driven.append((list(ids), on)) or 1)
    env["driven"] = driven
    return env


def _add(env, ids):
    import datetime as dt

    from studia.domain import Recording

    env["lib"].merge_recordings("c", [Recording(delivery_id=d, course_id="c", folder_id="f", folder_path="", name=d,
                                                start=dt.datetime(2026, 10, 15, 9, i), duration_s=2400, viewer_url="u")
                                      for i, d in enumerate(ids)], first_sync=False)


def test_one_lecture_at_a_time_on_the_graphics_card_by_default(roomy):
    # two at once were no faster on the owner's card and finished the newest lecture later (see jobs.GPU_WORKERS)
    assert jobs_mod.GPU_WORKERS == 1
    env = roomy
    env["state"]["engine"] = engine = CardEngine()
    jm = jobs_mod.JobManager(env["lib"], FakeSync(), lambda: Settings(parallel_downloads=1, capture_slides=False),
                             lambda s: engine, env["events"].append, lambda did: {}, lambda *a: [])
    jm.enqueue(["r1", "r2"], batch=True)
    assert wait_for(lambda: engine.most == 1)
    assert not wait_for(lambda: engine.most == 2, timeout=2.5)
    engine.go.set()
    assert wait_for(lambda: not jm.busy(), timeout=20)
    assert {finals(env["events"], i)[0]["stage"] for i in ("r1", "r2")} == {"done"}


def test_with_two_workers_a_second_lecture_shares_the_loaded_model_on_the_graphics_card(roomy, monkeypatch):
    monkeypatch.setattr(jobs_mod, "GPU_WORKERS", 2)
    env = roomy
    _add(env, ["r3"])
    env["state"]["engine"] = engine = CardEngine()
    made = []
    jm = jobs_mod.JobManager(env["lib"], FakeSync(), lambda: Settings(parallel_downloads=1, capture_slides=False),
                             lambda s: made.append(s) or engine, env["events"].append, lambda did: {}, lambda *a: [])
    jm.enqueue(["r1", "r2", "r3"], batch=True)
    assert wait_for(lambda: engine.most == 2)
    assert wait_for(lambda: sorted(j.where for j in jm._jobs.values()) == ["gpu", "run", "run"])   # a third place
    engine.go.set()
    assert wait_for(lambda: not jm.busy(), timeout=20)
    assert {finals(env["events"], i)[0]["stage"] for i in ("r1", "r2", "r3")} == {"done"}
    assert engine.most == 2 and not engine.unloaded                 # one model, never a second one
    # the thread that hands the card a lecture's batches runs as a normal program's, once per thread
    assert all(on for _, on in env["driven"]) and 1 <= len(env["driven"]) <= 2


def test_on_the_processor_or_with_the_offline_ai_on_the_card_one_lecture_at_a_time(roomy, monkeypatch):
    monkeypatch.setattr(jobs_mod, "GPU_WORKERS", 2)
    env = roomy
    env["state"]["engine"] = engine = CardEngine()
    engine.device = "cpu"
    jm = env["make"]()
    jm.enqueue(["r1", "r2"])
    assert wait_for(lambda: engine.most == 1)
    assert not wait_for(lambda: engine.most == 2, timeout=2.5)
    assert env["driven"] == []                                    # nothing raised for the processor
    engine.go.set()
    assert wait_for(lambda: not jm.busy(), timeout=20)
    engine.device = "cuda"
    jm._engine, jm._engine_key = engine, jm._key(jm.settings())
    assert not jm._may_share()                                    # nothing queued
    jm._jobs["x"] = jobs_mod.Job(id="x", title="x")
    assert jm._may_share()
    monkeypatch.setattr(tr, "_ai_on_gpu", lambda: True)
    assert not jm._may_share()
    monkeypatch.setattr(tr, "_ai_on_gpu", lambda: False)
    monkeypatch.setattr(jobs_mod, "_free_memory_gb", lambda: 1.0)
    assert not jm._may_share()                                    # little free memory
    monkeypatch.setattr(jobs_mod, "_free_memory_gb", lambda: 8.0)
    monkeypatch.setattr(jobs_mod.os, "cpu_count", lambda: 4)
    assert not jm._may_share()                                    # few processors
    monkeypatch.setattr(jobs_mod.os, "cpu_count", lambda: 20)
    jm._run.clear()
    assert not jm._may_share()                                    # paused
    jm._run.set()
    engine.loaded = False
    assert not jm._may_share()                                    # the second lecture never loads the model


def test_the_next_lecture_transcribes_while_the_last_ones_slides_are_finished(env, tmp_path, monkeypatch):
    monkeypatch.setattr(FakePanopto, "screen_sources", lambda self, info: ["screen"])
    gate = threading.Event()
    engine = FakeEngine()
    written = []
    settings = Settings(output_dir=str(tmp_path / "out"), parallel_downloads=1, capture_slides=True)
    jm = jobs_mod.JobManager(env["lib"], FakeSync(), lambda: settings, lambda s: engine, env["events"].append,
                             lambda did: written.append(did) or {}, lambda *a: gate.wait(10) and [])
    jm.enqueue(["r1", "r2"])
    assert wait_for(lambda: len(engine.audio_seen) == 2)          # both transcribed, the first one's slides not done
    assert written == [] and jm.busy() and "slides" in {jm.job(i).step for i in ("r1", "r2")}
    gate.set()
    assert wait_for(lambda: not jm.busy())
    assert sorted(written) == ["r1", "r2"]
    assert {finals(env["events"], i)[0]["stage"] for i in ("r1", "r2")} == {"done"}


def test_the_pane_says_loading_the_model_only_while_it_loads(env, monkeypatch):
    from studia.engine import models

    monkeypatch.setattr(models, "model_is_downloaded", lambda m: False)     # not loaded ahead: the first lecture loads it
    class Engine(FakeEngine):
        loaded = False

        def ensure_model(self, progress, cancel=None):
            if not self.loaded:
                progress("model-load", 0.0, "Loading the speech model")
                self.loaded = True

        @staticmethod
        def find_speech(audio, cancel=None, expected=0.0):
            return types.SimpleNamespace(units=[1, 2, 3])

        def transcribe(self, audio, progress, cancel, meta, speech=None):
            return super().transcribe(audio, progress, cancel, meta)

    env["state"]["engine"] = Engine()
    jm = env["make"]()
    jm.enqueue(["r1", "r2"])
    assert wait_for(lambda: not jm.busy())
    steps = {i: [s for s, _ in finals(env["events"], i)[0]["steps"]] for i in ("r1", "r2")}
    first, second = sorted(steps.values(), key=lambda s: "model" not in s)
    assert "model" in first and "model" not in second and "transcribe" in second


@pytest.mark.parametrize("gb, cpus, places, lanes", [(4, 20, 1, 1), (8, 20, 2, 1), (16, 20, 3, 2), (16, 4, 3, 1)])
def test_places_for_decoded_audio_and_download_threads_follow_the_pc(env, monkeypatch, gb, cpus, places, lanes):
    monkeypatch.setattr(jobs_mod, "_memory_gb", lambda: float(gb))
    monkeypatch.setattr(jobs_mod.os, "cpu_count", lambda: cpus)
    jm = env["make"]()
    jm._start()
    assert (jm._places, jm._lanes) == (places, lanes)
    jm = env["make"](parallel_downloads=3)                        # never fewer than chosen in Settings
    jm._start()
    assert jm._lanes == 3


# ------------------------------------------------------------------------------------- the engine's card threads
def test_the_models_own_threads_are_raised_when_it_loads_on_the_card(monkeypatch):
    _fake_load(monkeypatch)
    calls, snaps = [], iter([{1, 2}, {1, 2, 3, 4}])
    monkeypatch.setattr(osutil.impl(), "native_thread_ids", lambda: next(snaps))
    monkeypatch.setattr(osutil.impl(), "drive_card", lambda ids, on=True: calls.append((set(ids), on)) or 2)
    e = tr.Engine("large-v3")
    e.ensure_model(lambda *a: None)
    assert e.card_threads == {3, 4} and calls == [({3, 4}, True)]
    e.unload()
    assert e.card_threads == set()


def test_the_features_thread_runs_as_the_cards_threads(monkeypatch):
    from test_engine_pipeline import FakeDecoder, _decode

    calls = []
    monkeypatch.setattr(osutil.impl(), "drive_card", lambda ids, on=True: calls.append((list(ids), on)) or 1)
    monkeypatch.setattr(tr, "detect_gpu", lambda *a, **k: {"vram_mb": 8000})
    e = tr.Engine("large-v3-turbo")
    e._device, e._decoder = "cuda", FakeDecoder()
    _decode(e)
    assert len(calls) == 1 and calls[0][1] is True and calls[0][0] != [threading.get_native_id()]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' thread priorities")
def test_drive_card_raises_a_thread_to_a_normal_programs_priority_and_back():
    from ctypes import wintypes as wt

    from studia import winutil

    k32 = ctypes.WinDLL("kernel32")
    k32.GetCurrentThread.restype = wt.HANDLE
    k32.GetThreadPriority.argtypes = [wt.HANDLE]
    out = {}

    def run():
        me = threading.get_native_id()
        out["listed"] = me in winutil.native_thread_ids()
        out["set"] = winutil.drive_card([me])
        out["high"] = k32.GetThreadPriority(k32.GetCurrentThread())
        winutil.drive_card([me], False)
        out["back"] = k32.GetThreadPriority(k32.GetCurrentThread())

    th = threading.Thread(target=run)
    th.start()
    th.join()
    assert out == {"listed": True, "set": 1, "high": 2, "back": 0}


def test_on_linux_nothing_is_raised():
    from studia import linuxutil

    assert linuxutil.native_thread_ids() == set() and linuxutil.drive_card([1, 2]) == 0
