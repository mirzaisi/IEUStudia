"""The graphics card doesn't wait for the processor: speech is found in the download thread, the next batch's features
are computed ahead, the model loads while the first lecture downloads; a waiting lecture is held as int16; thread and
batch sizes; the chunked VAD gives faster-whisper's numbers."""

import sys
import threading
import types

import numpy as np
import pytest

from studia import jobs as jobs_mod
from studia.engine import transcriber as tr
from studia.engine.core import SR, Decoded, Unit
from studia.quality import NoSpeech

from test_jobs import FakeEngine, FakePanopto, env, finals, wait_for  # noqa: F401 - env is a fixture


# ---------------------------------------------------------------------------------------------- threads and batches
def test_processor_threads_are_capped_at_8(monkeypatch):
    for cpus, want in ((20, 8), (16, 8), (12, 8), (10, 8), (8, 6), (4, 2), (2, 1), (None, 2)):
        monkeypatch.setattr(tr.os, "cpu_count", lambda c=cpus: c)
        assert tr.cpu_threads() == want


def _fake_load(monkeypatch, gpu_fails=False):
    made = []

    class WM:
        def __init__(self, path, device, compute_type, cpu_threads, num_workers=1):
            made.append((device, cpu_threads))
            self.model = types.SimpleNamespace(n_mels=128)

        def encode(self, x):
            if gpu_fails:
                raise RuntimeError("cublas64_12.dll is not found")

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=WM))
    monkeypatch.setattr(tr, "local_model_path", lambda m: "x")
    monkeypatch.setattr(tr, "setup_cuda_dlls", lambda: [])
    monkeypatch.setattr(tr, "resolve_device", lambda p: ("cuda", "float16"))
    monkeypatch.setattr(tr, "UnitDecoder", lambda wm: object())
    monkeypatch.setattr(tr.os, "cpu_count", lambda: 20)
    return made


def test_on_the_graphics_card_ctranslate2_gets_one_processor_thread(monkeypatch):
    made = _fake_load(monkeypatch)
    tr.Engine("large-v3").ensure_model(lambda *a: None)
    assert made == [("cuda", 1)]


def test_when_the_graphics_card_fails_the_processor_gets_its_threads(monkeypatch):
    made = _fake_load(monkeypatch, gpu_fails=True)
    e = tr.Engine("large-v3-turbo")
    e.ensure_model(lambda *a: None)
    assert made == [("cuda", 1), ("cpu", 8)] and e._device == "cpu"


def test_batch_sizes(monkeypatch):
    vram = {"mb": 8188}
    monkeypatch.setattr(tr, "detect_gpu", lambda *a, **k: {"vram_mb": vram["mb"]})
    ai = {"device": ""}
    fake_llm = types.SimpleNamespace(device_of=lambda key: ai["device"])
    monkeypatch.setitem(sys.modules, "studia.local_llm", fake_llm)

    def size(model):
        e = tr.Engine(model)
        e._device = "cuda"
        return e._batch_size()

    assert size("large-v3") == 8 and size("medium") == 8 and size("large-v3-turbo") == 16
    ai["device"] = "cuda"                                   # the offline AI shares the card: large-v3 stays at 4
    assert size("large-v3") == 4 and size("large-v3-turbo") == 16
    ai["device"] = "cpu"
    assert size("large-v3") == 8
    vram["mb"] = 6144
    assert size("large-v3") == 2 and size("large-v3-turbo") == 8
    vram["mb"] = 4096                                       # a 4 GB card can't hold turbo's batch of 8
    assert size("large-v3-turbo") == 4 and size("small") == 8
    e = tr.Engine("large-v3")
    e._device = "cpu"
    assert e._batch_size() == 4
    monkeypatch.delitem(sys.modules, "studia.local_llm")    # the offline AI never loaded
    vram["mb"] = 8188
    assert size("large-v3") == 8


# ---------------------------------------------------------------------------------------------- decoding
class FakeDecoder:
    """Records where features were computed; runs out of GPU memory above ``max_batch``."""

    def __init__(self, max_batch=99):
        self.max_batch = max_batch
        self.feature_threads = []
        self.batches = []
        self.lid_batches = []

    def features(self, audio, batch):
        self.feature_threads.append(threading.current_thread().name)
        return np.array([u.start for u in batch])

    def encode(self, feats):
        return list(feats)

    @staticmethod
    def prior(start):
        p = (start // SR % 10) / 10
        return {"en": p, "tr": 1 - p}

    def language_prior(self, enc, langs):
        self.lid_batches.append(len(enc))
        return [self.prior(s) for s in enc]

    def generate(self, enc, batch, langs, opts):
        assert enc == [u.start for u in batch]              # the features of this very batch
        self.batches.append(len(batch))
        if len(batch) > self.max_batch:
            raise RuntimeError("CUDA failed with error out of memory")
        return [Decoded(l, f"Passage {u.start // SR} {l}.", -0.1 if l == "en" else -0.5, 0.0, 1.2, 6)
                for u, l in zip(batch, langs)]


def _decode(e, n=40):
    units = [Unit(k * SR, k * SR + SR * (1 + k % 5)) for k in range(n)]
    chosen, _ = e._decode_units(np.zeros(SR, dtype=np.float32), units, lambda *a: None, None)
    return [d.text for d in chosen]


def test_on_the_graphics_card_the_next_batches_features_are_computed_ahead(monkeypatch):
    monkeypatch.setattr(tr, "detect_gpu", lambda *a, **k: {"vram_mb": 8000})
    want = [f"Passage {k} en." for k in range(40)]
    e = tr.Engine("large-v3-turbo")
    e._device, e._decoder = "cuda", FakeDecoder()
    assert _decode(e) == want
    threads = e._decoder.feature_threads
    me = threading.current_thread().name                     # only the first of the 3 batches is computed here
    assert len(threads) == 3 and threads.count(me) == 1 and all(t == me or t.startswith("features") for t in threads)
    e = tr.Engine("small")
    e._device, e._decoder = "cpu", FakeDecoder()
    assert _decode(e) == want                                # the same, and on the processor all in its own thread
    assert set(e._decoder.feature_threads) == {threading.current_thread().name}


def test_an_out_of_memory_batch_stays_smaller_for_the_next_lectures_until_the_model_unloads(monkeypatch):
    monkeypatch.setattr(tr, "detect_gpu", lambda *a, **k: {"vram_mb": 8000})
    want = [f"Passage {k} en." for k in range(40)]
    e = tr.Engine("large-v3-turbo")
    e._device, e._decoder = "cuda", FakeDecoder(max_batch=5)
    assert _decode(e) == want                                # features computed ahead for a batch size that changed
    assert e._decoder.batches[0] == 16 and e._oom_batch == 4
    e._decoder.batches.clear()
    assert _decode(e) == want
    assert max(e._decoder.batches) == 4                      # the next lecture doesn't run out of memory first
    e._wm = None
    e.unload()
    assert e._oom_batch == 0


def test_a_checkpoint_never_makes_the_batches_bigger_again(monkeypatch):
    monkeypatch.setattr(tr, "detect_gpu", lambda *a, **k: {"vram_mb": 8000})
    units = [Unit(k * SR, k * SR + SR * (1 + k % 5)) for k in range(40)]
    order = tr.sort_by_duration(units, range(40))

    class Resume:                                # a lecture stopped after one full batch (16), no out of memory
        key = "k"

        def load(self):
            idx = order[:16]
            return [(idx, [[Decoded("en", f"Passage {units[j].start // SR} en.", -0.1, 0.0, 1.2, 6)] for j in idx],
                     [None] * 16, 16)]

        start = add = lambda self, *a: None
        close = lambda self: None              # noqa: E731

    e = tr.Engine("large-v3-turbo")
    e._device, e._decoder = "cuda", FakeDecoder()
    e._oom_batch = 4                             # another lecture on the card ran out of memory meanwhile
    chosen, _ = e._decode_units(np.zeros(SR, dtype=np.float32), units, lambda *a: None, None, resume=Resume())
    assert [d.text for d in chosen] == [f"Passage {k} en." for k in range(40)]
    assert e._oom_batch == 4 and max(e._decoder.batches) == 4
    assert e.resumed_passages == 16                # (the jobs pane gives no speed for it)
    _decode(e)
    assert e.resumed_passages == 0


def test_only_large_v3_turbo_weighs_in_its_language_id_head(monkeypatch):
    monkeypatch.setattr(tr, "detect_gpu", lambda *a, **k: {"vram_mb": 8000})
    seen = {}
    choose = tr.choose

    def spy(cands, dur, dominant, prior=None):
        seen[int(cands[0].text.split()[1])] = prior          # "Passage <k> en."
        return choose(cands, dur, dominant, prior)

    monkeypatch.setattr(tr, "choose", spy)
    e = tr.Engine("large-v3-turbo")
    e._device, e._decoder = "cuda", FakeDecoder(max_batch=5)
    _decode(e)
    assert seen == {k: FakeDecoder.prior(k * SR) for k in range(40)}     # each passage its own, out of memory too
    for model in ("large-v3", "medium", "small"):
        seen.clear()
        e = tr.Engine(model)
        e._device, e._decoder = "cuda", FakeDecoder()
        _decode(e)
        assert len(seen) == 40 and set(seen.values()) == {None} and not e._decoder.lid_batches


def test_given_the_speech_the_engine_doesnt_look_for_it_again(monkeypatch):
    monkeypatch.setattr(tr, "detect_gpu", lambda *a, **k: {"vram_mb": 8000})
    monkeypatch.setattr(tr, "find_speech", lambda *a, **k: pytest.fail("found again"))
    e = tr.Engine("large-v3-turbo")
    e._device, e._decoder, e._wm = "cuda", FakeDecoder(), object()
    units = [Unit(0, 5 * SR), Unit(6 * SR, 12 * SR)]
    speech = tr.Speech([(u.start, u.end) for u in units], units, [], {"speech_s": 11.0}, 1.0, 1.5)
    stages = []
    out = e.transcribe(np.full(13 * SR, 0.1, dtype=np.float32), lambda st, f, d: stages.append(st), None,
                       {"delivery_id": "r1"}, speech=speech)
    assert [s.text for s in out.segments] == ["Passage 0 en.", "Passage 6 en."]
    assert out.timings["vad"] == 1.5 and "vad" not in stages and out.meta["quality"]["stats"]["speech_s"] == 11.0


def _transcribed(monkeypatch, texts, lp=-0.1):
    """The segments' text of Engine.transcribe for passages 6 s apart that decode (in English) as ``texts``, each with
    the average log-probability ``lp`` (one, or one per passage)."""
    from studia.engine.core import compression_ratio

    monkeypatch.setattr(tr, "detect_gpu", lambda *a, **k: {"vram_mb": 8000})
    lps = lp if isinstance(lp, list) else [lp] * len(texts)
    units = [Unit(k * 6 * SR, k * 6 * SR + 3 * SR) for k in range(len(texts))]

    class Says(FakeDecoder):
        def generate(self, enc, batch, langs, opts):
            ks = [u.start // (6 * SR) for u in batch]
            return [Decoded(lang, texts[k], lps[k], 0.0, compression_ratio(texts[k]), 8) for k, lang in zip(ks, langs)]

    e = tr.Engine("large-v3", languages=("en",))
    e._device, e._decoder, e._wm = "cpu", Says(), object()
    speech = tr.Speech([(u.start, u.end) for u in units], units, [], {"speech_s": 3.0 * len(units)}, 1.0, 0.1)
    out = e.transcribe(np.full(6 * SR * len(texts), 0.1, dtype=np.float32), lambda *a: None, None, {}, speech=speech)
    return [s.text for s in out.segments]


def test_numbers_read_out_reach_the_transcript_whole(monkeypatch):
    said = ["The identity matrix is 1, 0, 0, 0, 1, 0, 0, 0, 1.", "One, one, zero, zero, zero, one, zero, one.",
            "Matris sıfır, sıfır, sıfır, bir.", "So we go go go go go to the next one."]
    assert _transcribed(monkeypatch, said) == said[:3] + ["So we go to the next one."]


def test_a_few_unsure_words_with_numbers_stay_in_the_transcript(monkeypatch):
    import math

    said = ["Three plus three.", "Good afternoon, everyone.", "You must be zero.", "So the answer is six."]
    lps = [math.log(0.405), math.log(0.40), math.log(0.433), -0.1]
    assert _transcribed(monkeypatch, said, lps) == ["Three plus three.", "You must be zero.", "So the answer is six."]


def test_find_speech_checks_the_audio(monkeypatch):
    with pytest.raises(NoSpeech):
        tr.find_speech(np.zeros(30 * SR, dtype=np.float32))
    monkeypatch.setattr(tr, "_units", lambda audio, cancel: ([(0, 20 * SR)], [Unit(0, 20 * SR)]))
    s = tr.find_speech(np.full(30 * SR, 0.005, dtype=np.float32), None, 30.0)
    assert s.units == [Unit(0, 20 * SR)] and s.gain > 1.0 and s.stats["speech_s"] == 20.0


# ---------------------------------------------------------------------------------------------- chunked VAD
def test_the_chunked_vad_gives_faster_whispers_probabilities_and_spans():
    from faster_whisper.vad import VadOptions, get_speech_timestamps, get_vad_model

    from studia.engine import vad

    rng = np.random.default_rng(7)
    t = np.arange(10_240_000 + 300) / SR                        # two model calls of 10,000 windows, and a part
    talk = (np.sin(2 * np.pi * 0.21 * t) > 0.2) * (0.3 * np.sin(2 * np.pi * 180 * t) * np.sin(2 * np.pi * 3 * t))
    audio = (talk + 0.01 * rng.standard_normal(len(t))).astype(np.float32)
    opts = VadOptions(threshold=0.5, min_speech_duration_ms=200, min_silence_duration_ms=300, speech_pad_ms=200,
                      max_speech_duration_s=14.0)
    for x in (audio, audio[:5_120_000], audio[:5_120_000 - 30], audio[:20_000]):    # ends: 512-aligned, in the tail
        old = get_vad_model()(np.pad(x, (0, 512 - x.shape[0] % 512)))
        assert np.array_equal(vad.speech_probs(x), old)
        assert vad.get_speech_timestamps(x, opts) == get_speech_timestamps(x, opts)


# ---------------------------------------------------------------------------------------------- the jobs
def test_int16_holding_gives_back_the_decoders_samples():
    pcm = np.arange(-32768, 32768, dtype=np.int16)
    decoded = pcm.astype(np.float32)
    decoded *= 1.0 / 32768.0                                    # as audio.decode_audio makes them
    held = jobs_mod._to_int16(decoded.copy())
    assert held.dtype == np.int16 and np.array_equal(held, pcm)
    back = jobs_mod._to_float32(held)
    assert back.dtype == np.float32 and np.array_equal(back, decoded)


class FindingEngine(FakeEngine):
    def __init__(self, no_speech=False):
        super().__init__()
        self.loaded = False
        self.no_speech = no_speech
        self.found_in = []
        self.given = []
        self.loads = []
        self.audio = []

    def find_speech(self, audio, cancel, expected_duration):
        self.found_in.append((threading.current_thread().name, audio.dtype, expected_duration))
        if self.no_speech:
            raise NoSpeech("No speech was found in this recording (only noise or music). Check it on Panopto.")
        return "speech of " + str(len(audio))

    def ensure_model(self, progress, cancel=None):
        if self.loaded:
            return
        self.loads.append(threading.current_thread().name)
        self.loaded = True
        progress("model-load", 1.0, "ready")

    def transcribe(self, audio, progress, cancel, meta, speech=None):
        self.given.append(speech)
        self.audio.append(audio)
        return super().transcribe(audio, progress, cancel, meta)


@pytest.fixture
def finding(env):
    dec = []

    def decode(p, cancel=None):
        a = (np.arange(16000, dtype=np.int16) - 8000).astype(np.float32)
        a *= 1.0 / 32768.0
        dec.append(a.copy())
        return a

    env["mp"].setattr(jobs_mod, "decode_audio", decode)
    env["decoded"] = dec
    return env


def test_the_download_thread_finds_the_speech_and_the_lecture_waits_as_int16(finding):
    eng = finding["state"]["engine"] = FindingEngine()
    jm = finding["make"]()
    jm.enqueue(["r1"])
    assert wait_for(lambda: finals(finding["events"], "r1"))
    assert finals(finding["events"], "r1")[0]["stage"] == "done"
    assert eng.found_in == [("download-1", np.float32, 60.0)]          # in the download lane, before int16
    assert eng.given == ["speech of 16000"]
    assert eng.audio[0].dtype == np.float32 and np.array_equal(eng.audio[0], finding["decoded"][0])
    details = [e["detail"] for e in finding["events"] if e.get("type") == "job" and e.get("id") == "r1"]
    assert "Decoding audio" in details and "Finding speech" in details     # shown, right after the last MB too


def test_no_speech_found_in_the_download_thread_fails_the_lecture_as_before(finding):
    eng = finding["state"]["engine"] = FindingEngine(no_speech=True)
    jm = finding["make"]()
    jm.enqueue(["r1"])
    assert wait_for(lambda: finals(finding["events"], "r1"))
    rec = finding["lib"].get_recording("r1")
    assert rec.status == "failed" and rec.error.startswith("No speech was found")
    assert finding["lib"].warnings("r1")[0]["code"] == "no_speech"
    assert finals(finding["events"], "r1")[-1]["no_sound"] is True
    assert eng.given == []                                              # never handed to the graphics card


def test_the_model_loads_while_the_first_lecture_downloads(finding):
    finding["mp"].setattr("studia.engine.models.model_is_downloaded", lambda m: True)
    eng = finding["state"]["engine"] = FindingEngine()
    FakePanopto.gate = threading.Event()
    jm = finding["make"]()
    jm.enqueue(["r1"])
    assert wait_for(lambda: eng.loads == ["model-preload"])            # the download is still held
    assert jm.job("r1").stage == "downloading"
    assert wait_for(lambda: any(e.get("type") == "engine" and e.get("phase") == "ready" for e in finding["events"]))
    FakePanopto.gate.set()
    assert wait_for(lambda: finals(finding["events"], "r1"))
    assert eng.loads == ["model-preload"] and finals(finding["events"], "r1")[0]["stage"] == "done"


def test_no_model_load_ahead_while_the_setup_downloads_it_or_without_the_model(finding):
    finding["mp"].setattr("studia.engine.models.model_is_downloaded", lambda m: False)
    eng = finding["state"]["engine"] = FindingEngine()
    jm = finding["make"]()
    jm.enqueue(["r1"])
    assert wait_for(lambda: finals(finding["events"], "r1"))
    assert eng.loads == ["transcribe"]                                  # the lecture loads (or downloads) it
    finding["mp"].setattr("studia.engine.models.model_is_downloaded", lambda m: True)
    eng.loaded = False
    eng.loads.clear()
    jm.setup_busy = lambda: True
    jm._preload()
    assert not jm._preloading and eng.loads == []
