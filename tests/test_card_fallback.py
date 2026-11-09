"""The graphics card fails and the processor takes over: the next lecture really tries the card again, one failure
counts once, and a card that stops working in the middle of a batch doesn't fail the rest of it."""

import pytest

from studia import hardware
from test_conn_run import _card_asked_afresh, _smi  # noqa: F401 - an autouse fixture
from test_jobs import FakeEngine, env, finals, wait_for  # noqa: F401 - env is a fixture

GTX1650 = {"name,memory.total,driver_version,compute_cap": "NVIDIA GeForce GTX 1650, 4096, 566.36, 7.5"}
FELL_BACK = "The GPU could not be used, so transcription runs on the CPU (slower)."


class _Card(FakeEngine):
    """Engine.ensure_model as it goes: each load tries the graphics card first, and fails with the next of ``fails``
    ("": it works); the processor then takes over. ``loads``: where each load went."""
    model_name = "large-v3-turbo"

    def __init__(self, fails, loads, device_pref="auto"):
        super().__init__()
        self.fails, self.loads, self.device_pref = fails, loads, device_pref
        self.loaded, self.device, self.gpu_error, self.device_info = False, "", "", {}

    def ensure_model(self, progress, cancel=None):
        if self.loaded:
            return
        error = self.fails.pop(0) if self.fails else ""
        self.loads.append("cpu" if error else "cuda")
        self.loaded, self.device, self.gpu_error = True, "cpu" if error else "cuda", error
        self.device_info = {"device": self.device, **({"warning": FELL_BACK} if error else {})}
        progress("model-load", 1.0, "ready")

    def unload(self):
        self.loaded, self.device = False, ""


@pytest.fixture
def card(env, monkeypatch):
    """A JobManager whose every engine is a new _Card (as the app makes one), with no model loaded ahead."""
    _smi(monkeypatch, GTX1650)
    monkeypatch.setattr("studia.engine.models.model_is_downloaded", lambda m: False)

    def make(fails, device_pref="auto"):
        jm = env["make"]()
        loads = []
        jm.engine_factory = lambda s: _Card(fails, loads, device_pref)
        return jm, loads

    yield make
    hardware.forget_card_failed()


def _both(env, jm):
    jm.enqueue(["r1", "r2"])
    assert wait_for(lambda: finals(env["events"], "r1") and finals(env["events"], "r2"))
    return {did: finals(env["events"], did)[0] for did in ("r1", "r2")}


def _record():
    return hardware._card_record(hardware._card_now())


@pytest.mark.parametrize("error", ["CUDA failed with error unknown error", "CUDA failed with error out of memory"])
def test_after_the_card_failed_once_the_next_lecture_tries_it_again(env, card, error):
    """3.3.0 kept the processor's engine: the second lecture never tried the card, and an unknown failure counted
    twice, so "IEUStudia uses the processor from now on" came after one failure (with a card that works)."""
    jm, loads = card([error])
    done = _both(env, jm)
    assert loads == ["cpu", "cuda"]                        # the second lecture loaded the model again, on the card
    assert sorted(e["device"] for e in done.values()) == ["cpu", "cuda"]
    assert not [e for e in env["events"] if e.get("type") == "toast"]
    assert _record() == {}                                 # the card worked again: nothing is remembered


def test_one_failure_counts_once_though_two_lectures_go_on_with_its_engine(env, monkeypatch):
    """The engine can't be let go while a study plan still uses it: the next lecture goes on with it."""
    from test_conn_run import _CardFails

    _smi(monkeypatch, GTX1650)
    env["state"]["engine"] = _CardFails("CUDA failed with error unknown error")    # loaded, and it stays so
    done = _both(env, env["make"]())
    for e in done.values():
        assert e["notes"] == ["Your graphics card couldn't run the speech model this time, so the processor "
                              "transcribed this lecture. The next lecture tries the card again."]
    assert not [e for e in env["events"] if e.get("type") == "toast"]
    assert _record()["count"] == 1 and hardware.card_failed() == {}
    hardware.forget_card_failed()


def test_a_card_that_cant_is_not_tried_again_on_automatic_but_is_when_chosen(env, card):
    cant = "CUDA failed with error no kernel image is available for execution on the device"
    jm, loads = card([cant, cant])
    _both(env, jm)
    assert loads == ["cpu"]                                # remembered at once: the processor's engine is kept
    assert hardware.card_failed()["count"] == 1
    hardware.forget_card_failed()
    env["events"].clear()
    jm, loads = card([cant, cant], device_pref="cuda")     # "Graphics card" in Settings: each lecture tries it first
    done = _both(env, jm)
    assert loads == ["cpu", "cpu"]
    assert all("so each lecture tries it first" in e["notes"][0] for e in done.values())


STOPPED = "CUDA failed with error unspecified launch failure"
STOPPED_NOTE = ("The graphics card stopped working during the batch (a driver update or reset can do this), so the "
                "processor transcribed this lecture. Close IEUStudia and open it again to use the card again.")


class _Stops(_Card):
    """The graphics card fails in the middle of a lecture of ``stop_on``. ``sticky`` (a driver reset, say): as with
    CUDA's sticky errors, the card then fails everything in this process, a fresh model's load too (Engine.ensure_model
    then loads on the processor); else it fails only that lecture's next run (``fault``: the card's words)."""

    def __init__(self, loads, state, stop_on, fault=STOPPED, sticky=True):
        super().__init__([], loads)
        self.state, self.stop_on, self.fault, self.sticky = state, stop_on, fault, sticky

    def ensure_model(self, progress, cancel=None):
        if not self.loaded and self.state.get("broken"):
            self.fails = [self.fault]
        super().ensure_model(progress, cancel)

    def transcribe(self, audio, progress, cancel, meta):
        did = meta["delivery_id"]
        if self.device == "cuda" and (did in self.state["stop_on"] or self.state.get("broken")):
            if not self.sticky:
                self.state["stop_on"].remove(did)
            self.state["broken"] = self.sticky
            raise RuntimeError(self.fault)
        return super().transcribe(audio, progress, cancel, meta)


def _stopping(env, monkeypatch, stop_on, **kw):
    """A JobManager whose every engine is a new _Stops (as the app makes one)."""
    _smi(monkeypatch, GTX1650)
    jm, loads, state = env["make"](), [], {"stop_on": list(stop_on)}
    jm.engine_factory = lambda s: _Stops(loads, state, stop_on, **kw)
    return jm, loads


def test_a_card_that_stops_mid_batch_hands_the_rest_of_it_to_the_processor(env, monkeypatch):
    """3.3.1 kept the engine on the broken card: every lecture after the fault failed within seconds with CUDA's own
    "unspecified launch failure", and the student found the whole overnight batch failed."""
    import datetime as dt

    from studia.domain import Recording

    env["lib"].merge_recordings("c", [Recording(delivery_id=d, course_id="c", folder_id="f", folder_path="", name=d,
                                                start=dt.datetime(2026, 10, 13, 11, i), duration_s=2400, viewer_url="u")
                                      for i, d in enumerate(["r1", "r2", "r3"])], first_sync=False)
    jm, loads = _stopping(env, monkeypatch, ["r2"])
    for did in ("r1", "r2", "r3"):                          # one after the other
        jm.enqueue([did])
        assert wait_for(lambda: finals(env["events"], did))
    done = {did: finals(env["events"], did)[0] for did in ("r1", "r2", "r3")}
    assert [done[d]["stage"] for d in ("r1", "r2", "r3")] == ["done"] * 3
    assert [done[d]["device"] for d in ("r1", "r2", "r3")] == ["cuda", "cpu", "cpu"]
    assert loads == ["cuda", "cpu"]         # loaded again once (on the processor: the card stays broken), then kept
    assert done["r2"]["notes"] == done["r3"]["notes"] == [STOPPED_NOTE]
    assert _record() == {} and not [e for e in env["events"] if e.get("type") == "toast"]     # nothing remembered


def test_a_card_that_fails_again_on_a_fresh_model_says_so_in_plain_words(env, monkeypatch):
    jm, loads = _stopping(env, monkeypatch, ["r1", "r1"], sticky=False)
    jm.enqueue(["r1"])
    assert wait_for(lambda: finals(env["events"], "r1"))
    final = finals(env["events"], "r1")[0]
    assert final["stage"] == "failed" and "CUDA" not in final["error"] and loads == ["cuda", "cuda"]
    assert final["error"].startswith("The graphics card stopped working while this lecture was transcribed")
    jm.enqueue(["r2"])                                      # the next lecture: a fresh model, on the card again
    assert wait_for(lambda: finals(env["events"], "r2"))
    assert finals(env["events"], "r2")[0]["stage"] == "done" and finals(env["events"], "r2")[0]["device"] == "cuda"
    assert loads == ["cuda", "cuda", "cuda"]


def test_a_card_out_of_memory_with_one_passage_is_tried_again_with_a_fresh_model(env, monkeypatch):
    """Another program took the card's memory: the fresh model fits again, and the card goes on (nothing is
    remembered, nothing said)."""
    jm, loads = _stopping(env, monkeypatch, ["r1"], fault="CUDA failed with error out of memory", sticky=False)
    done = _both(env, jm)
    assert [done[d]["stage"] for d in ("r1", "r2")] == ["done", "done"]
    assert [done[d]["device"] for d in ("r1", "r2")] == ["cuda", "cuda"] and loads == ["cuda", "cuda"]
    assert not done["r1"]["notes"] and not jm._card_stopped and _record() == {}
