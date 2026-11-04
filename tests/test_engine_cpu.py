"""Low-end PCs: no retries of passages that end up [unclear] anyway, transcription on the processor makes way for the
offline AI, and a PC with little memory keeps one decoded lecture."""

import threading
import time

import numpy as np

from studia.engine import Engine
from studia.engine import core
from studia.engine import transcriber as tr
from studia.engine.core import SR, DecodeOptions, Decoded, Unit


class FakeDecoder:
    def __init__(self, results):
        self.results = results          # lang -> Decoded per unit index
        self.encoded = []

    def features(self, audio, batch):
        return [u.start for u in batch]

    def encode(self, feats):
        self.encoded.append(list(feats))
        return feats

    def generate(self, enc, batch, langs, opts):
        return [self.results[lang][u.start // SR] for u, lang in zip(batch, langs)]


def _d(lang, text, lp, cr=1.3):
    return Decoded(lang, text, lp, 0.0, cr, 8)


def test_a_passage_that_ends_up_unclear_is_not_decoded_again():
    # 0: clear English; 1: makes no sense in either language (becomes [unclear]); 2: a confident repetition loop
    loop = "go on go on go on go on go on go on go on go on go on go on go on go on"
    res = {"en": [_d("en", "So the pivot is here.", -0.2), _d("en", "We are going to look at the thing here now.", -1.2),
                  _d("en", loop, -0.3, cr=2.6)],
           "tr": [_d("tr", "Pivot burada.", -0.9), _d("tr", "Şimdi burada şuna bakacağız arkadaşlar tamam mı.", -1.3),
                  _d("tr", "Devam devam.", -1.4)]}
    e = Engine("small")
    e._device = "cpu"
    e._decoder = FakeDecoder(res)
    units = [Unit(k * SR, k * SR + 5 * SR) for k in range(3)]
    chosen, best = e._decode_units(np.zeros(10 * SR, dtype=np.float32), units, lambda *a: None, None)
    again = e._decoder.encoded[1:]                              # encoder passes after the first batch: the retries
    assert again and all(1 * SR not in r for r in again)       # the hopeless passage is never encoded again
    assert any(2 * SR in r for r in again)                      # the loop is
    assert best[1] < tr.MIN_CONF and chosen[0].text == "So the pivot is here."


def test_transcription_on_the_processor_waits_while_the_offline_ai_writes(monkeypatch):
    from studia.cancel import Cancelled

    monkeypatch.setattr(tr, "detect_gpu", lambda *a, **k: {"vram_mb": 8000})
    res = {"en": [_d("en", "One.", -0.2), None, _d("en", "Two.", -0.2)],
           "tr": [_d("tr", "Bir.", -0.9), None, _d("tr", "İki.", -0.9)]}
    units = [Unit(0, SR), Unit(2 * SR, 3 * SR)]
    audio = np.zeros(4 * SR, dtype=np.float32)
    released, details = threading.Event(), []

    def ai():
        with core.processor_for_ai():
            released.wait(5)

    def run(device, cancel=None, release_after=None):
        e = Engine("small")
        e._device = device
        e._decoder = FakeDecoder(res)
        writer = threading.Thread(target=ai)
        writer.start()
        while not core.ai_writing():
            time.sleep(0.01)
        if release_after:                                           # the AI is done writing after this long
            threading.Timer(release_after, released.set).start()
        started = time.monotonic()
        try:
            e._decode_units(audio, units, lambda stage, frac, detail: details.append(detail), cancel)
            return time.monotonic() - started
        finally:
            released.set()
            writer.join()
            released.clear()

    assert run("cpu", release_after=0.5) >= 0.45                    # the first batch waited until the AI was done
    assert details[0] == "Waiting while the offline AI writes" and not core.ai_writing()
    details.clear()
    run("cuda")
    assert details and "Waiting while the offline AI writes" not in details      # on the GPU it doesn't wait
    stop = threading.Event()
    threading.Timer(0.3, stop.set).start()
    try:
        run("cpu", stop)
        raise AssertionError("cancel should work while it waits")
    except Cancelled:
        pass


def test_retries_on_the_processor_wait_while_the_offline_ai_writes_too():
    # a confident repetition loop: tried again at two temperatures, one encoder pass each
    loop = "go on go on go on go on go on go on go on go on go on go on go on go on"
    res = {"en": [_d("en", loop, -0.3, cr=2.6)]}
    released = threading.Event()

    def ai():
        with core.processor_for_ai():
            released.wait(5)

    def run(device, release_after=None):
        e = Engine("small")
        e._device = device
        e._decoder = dec = FakeDecoder(res)
        encode, writing, details = dec.encode, [], []
        dec.encode = lambda feats: writing.append(core.ai_writing()) or encode(feats)
        writer = threading.Thread(target=ai)
        writer.start()
        while not core.ai_writing():
            time.sleep(0.01)
        if release_after:                                           # the AI is done writing after this long
            threading.Timer(release_after, released.set).start()
        started = time.monotonic()
        try:
            e._retry_bad(np.zeros(6 * SR, dtype=np.float32), [Unit(0, 5 * SR)], [res["en"][0]],
                         DecodeOptions(batch_size=4), None, lambda stage, frac, detail: details.append(detail), [0.9])
            return time.monotonic() - started, writing, details
        finally:
            released.set()
            writer.join()
            released.clear()

    took, writing, details = run("cpu", release_after=0.5)
    assert took >= 0.45 and writing == [False, False]             # no retry ran while the AI wrote
    assert details == ["Waiting while the offline AI writes"] + ["Checking 1 unclear passage again"] * 2
    took, writing, details = run("cuda")
    assert writing == [True, True] and "Waiting while the offline AI writes" not in details   # the GPU doesn't wait


def test_the_offline_ai_keeps_the_processor_between_the_parts_it_condenses(monkeypatch):
    from studia import local_llm

    def generate(msgs, key, max_tokens=900, on_text=None, cancel=None):
        with core.processor_for_ai():                     # as generate() does on the processor
            return {"text": "notes", "usage": {"input_tokens": 1, "output_tokens": 1}}

    monkeypatch.setattr(local_llm, "generate", generate)
    monkeypatch.setattr(local_llm, "budget", lambda key: 500)
    monkeypatch.setattr(local_llm, "count_tokens", lambda text, key: len(text.split()))
    text = "\n".join(f"[00:{i:02d}] a sentence about the topic of the lecture, number {i}" for i in range(60))
    for device in ("cpu", "cuda"):
        monkeypatch.setattr(local_llm, "device_of", lambda key: device)
        between = []                    # before each part, where a waiting transcription would start a batch
        local_llm.one_shot("S", text, "k", on_text=lambda s: between.append(core.ai_writing()))
        assert len(between) > 2 and between == [device == "cpu"] * len(between) and not core.ai_writing()


def test_a_pc_with_little_memory_keeps_one_decoded_lecture(tmp_path, monkeypatch):
    from studia import jobs
    from studia.config import Settings
    from studia.library import Library

    lib = Library(tmp_path / "lib.db")
    make = lambda: jobs.JobManager(lib, None, lambda: Settings(), lambda s: None, lambda e: None,  # noqa: E731
                                   lambda did: {}, lambda *a: [])
    monkeypatch.setattr(jobs, "_memory_gb", lambda: 3.9)
    assert make()._places == 1
    monkeypatch.setattr(jobs, "_memory_gb", lambda: 7.7)
    assert make()._places == jobs.DECODED_MAX == 2
    monkeypatch.setattr(jobs, "_memory_gb", lambda: 15.7)      # one more: a second lecture on the graphics card
    assert make()._places == 3
