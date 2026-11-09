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
