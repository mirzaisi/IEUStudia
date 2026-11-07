"""The offline model with a fake generator: condensing once per lecture, lengths that end before the cap, running out of
graphics memory, and the chat's room for the lecture passages."""

import collections
import re
import sys
import types

import pytest

from studia import local_llm
from studia.i18n import t
from studia.paths import app_dir

CUT = t("*(Cut off here: the offline model reached its length limit.)*")


def lecture(lines):
    return "\n".join(f"[00:{i % 60:02d}] a sentence about the topic of the lecture, number {i}" for i in range(lines))


@pytest.fixture
def fake(monkeypatch):
    """A model that counts words as tokens; each condensed part is as long as it was asked to be."""
    calls = []
    state = {"device": "cuda", "budget": 3000, "final_tokens": 5}

    def generate(msgs, key, max_tokens=900, on_text=None, cancel=None):
        system, user = msgs[0]["content"], msgs[-1]["content"]
        calls.append({"system": system, "user": user, "max": max_tokens})
        if system.startswith("You condense"):
            words = int(re.search(r"At most about (\d+) words", system).group(1))
            text = "\n".join(["- a point of the part"] * (words // 5))
            return {"text": text, "usage": {"input_tokens": len(user.split()), "output_tokens": len(text.split())}}
        out = state["final_tokens"] or max_tokens
        return {"text": "## Notes", "usage": {"input_tokens": len(user.split()), "output_tokens": out}}

    monkeypatch.setattr(local_llm, "_condensed", collections.OrderedDict())
    monkeypatch.setattr(local_llm, "generate", generate)
    monkeypatch.setattr(local_llm, "count_tokens", lambda text, key: len(text.split()))
    monkeypatch.setattr(local_llm, "budget", lambda key: state["budget"])
    monkeypatch.setattr(local_llm, "device_of", lambda key: state["device"])
    monkeypatch.setattr(local_llm, "_gpu_room", lambda key: (state["budget"], 3000))
    return types.SimpleNamespace(calls=calls, state=state)


def passes(calls):
    return [c for c in calls if c["system"].startswith("You condense")]


def test_a_lecture_is_condensed_once_for_every_kind_of_notes(fake):
    text = lecture(600)
    local_llm.one_shot("Study notes task", text, "k")
    assert len(passes(fake.calls)) > 2
    assert len(list((app_dir() / "cache" / "llm").glob("*.json"))) == 1
    fake.calls.clear()
    local_llm.one_shot("Flashcards task", text, "k")           # another kind: the same condensed text
    assert len(fake.calls) == 1 and fake.calls[0]["user"].startswith("Condensed notes of the lecture")
    local_llm._condensed.clear()                                 # the app was restarted: from the cache folder
    fake.calls.clear()
    local_llm.one_shot("Exam focus task", text, "k")
    assert len(fake.calls) == 1
    fake.calls.clear()
    local_llm.one_shot("Study notes task", text, "k")           # "Generate again": condensed afresh
    assert len(passes(fake.calls)) > 2
    fake.calls.clear()
    local_llm.one_shot("Flashcards task", text, "k")            # ...and that one is shared again
    assert len(fake.calls) == 1
    fake.calls.clear()
    local_llm.one_shot("Flashcards task", text + "\n[59:59] transcribed again", "k")     # another text: its own
    assert len(passes(fake.calls)) > 2
    fake.calls.clear()
    fake.state["budget"] = 1500                                  # e.g. on the processor: condensed to fit that
    local_llm.one_shot("Flashcards task", text, "k")
    assert len(passes(fake.calls)) > 2


def test_offline_answers_are_asked_to_end_before_their_cap(fake):
    for device, cap, words in (("cuda", 3000, 620), ("cpu", 1200, 250)):
        fake.state["device"] = device
        fake.calls.clear()
        r = local_llm.one_shot(f"Study notes on the {device}", lecture(600), "k")
        final = fake.calls[-1]
        assert final["max"] == cap and f"under about {words} words" in final["system"]
        assert r["text"] == "## Notes"                           # it ended before the cap: nothing added
        for p in passes(fake.calls):                             # every part has room in the answer's prompt
            assert "At most about 250 words" in p["system"] and p["max"] == 562
    fake.state["final_tokens"] = 0                               # it ran into the cap anyway: said so
    shown = []
    r = local_llm.one_shot("Short task", "a short text", "k", on_text=shown.append)
    assert r["text"] == "## Notes\n\n" + CUT and shown[-1] == r["text"]


def test_a_very_long_day_is_condensed_until_it_fits(fake):
    local_llm.one_shot("Study notes", lecture(8000), "k")       # ~90,000 words: 38 parts at the shortest length
    rounds = [p for p in passes(fake.calls) if p["user"].startswith("Condensed notes")]
    assert rounds and len(passes(fake.calls)) > 37
    final = fake.calls[-1]
    assert len(final["system"].split()) + len(final["user"].split()) <= 3000


def test_a_part_cut_off_by_its_cap_loses_only_its_unfinished_line(fake, monkeypatch):
    def generate(msgs, key, max_tokens=900, on_text=None, cancel=None):
        fake.calls.append({"system": msgs[0]["content"], "user": msgs[-1]["content"], "max": max_tokens})
        if msgs[0]["content"].startswith("You condense"):
            return {"text": "- one point\n- another point\n- half a", "usage": {"input_tokens": 1,
                                                                                "output_tokens": max_tokens}}
        return {"text": "## Notes", "usage": {"input_tokens": 1, "output_tokens": 5}}

    monkeypatch.setattr(local_llm, "generate", generate)
    local_llm.one_shot("Study notes", lecture(600), "k")
    assert "- another point" in fake.calls[-1]["user"] and "half a" not in fake.calls[-1]["user"]


def test_out_of_graphics_memory_frees_the_speech_model_then_halves_the_prompt_then_uses_the_processor(monkeypatch):
    loaded = {"k": {"device": "cuda", "room": 3000, "answer": 3000, "used": 0.0}}
    monkeypatch.setattr(local_llm, "_loaded", loaded)
    monkeypatch.setattr(local_llm, "_condensed", collections.OrderedDict())
    monkeypatch.setattr(local_llm, "count_tokens", lambda text, key: len(text.split()))
    freed = []
    monkeypatch.setattr(local_llm, "make_room", lambda: not freed and not freed.append(1))     # an idle speech model

    def load(key, cpu=False):
        if not cpu and key in loaded:                               # loaded once, as the real one
            return loaded[key]
        loaded[key] = {"device": "cpu", "room": local_llm.CPU_BUDGET, "answer": local_llm.CPU_ANSWER, "used": 0.0}
        return loaded[key]

    tried = []

    def generate(msgs, key, max_tokens=900, on_text=None, cancel=None):
        tried.append((loaded[key]["device"], loaded[key]["room"], max_tokens))
        if loaded[key]["device"] == "cuda":
            raise RuntimeError("CUDA failed with error out of memory")
        return {"text": "ok", "usage": {"input_tokens": 1, "output_tokens": 1}}

    monkeypatch.setattr(local_llm, "_load", load)
    monkeypatch.setattr(local_llm, "generate", generate)
    assert local_llm.one_shot("S", "a short text", "k")["text"] == "ok"
    assert tried == [("cuda", 3000, 3000), ("cuda", 3000, 3000), ("cuda", 1500, 3000), ("cpu", 2500, 1200)]

    def broken(msgs, key, max_tokens=900, on_text=None, cancel=None):
        raise RuntimeError("something else")

    monkeypatch.setattr(local_llm, "generate", broken)          # any other error is not retried
    with pytest.raises(RuntimeError, match="something else"):
        local_llm.one_shot("S", "a short text", "k")


def test_a_model_that_loads_on_the_processor_after_all_gets_the_processors_sizes(monkeypatch):
    from studia.engine import models

    loaded = {}
    monkeypatch.setattr(local_llm, "_loaded", loaded)
    monkeypatch.setattr(local_llm, "_condensed", collections.OrderedDict())
    monkeypatch.setattr(local_llm, "count_tokens", lambda text, key: len(text.split()))
    monkeypatch.setattr(models, "resolve_device", lambda pref: ("cuda", "float16"))      # a card, but it is full

    def load(key, cpu=False):
        loaded[key] = {"device": "cpu", "room": local_llm.CPU_BUDGET, "answer": local_llm.CPU_ANSWER, "used": 0.0}
        return loaded[key]

    asked = []
    monkeypatch.setattr(local_llm, "_load", load)
    monkeypatch.setattr(local_llm, "generate", lambda msgs, key, max_tokens=900, on_text=None, cancel=None: (
        asked.append(max_tokens) or {"text": "ok", "usage": {"input_tokens": 1, "output_tokens": 1}}))
    local_llm.one_shot("S", "a short text", "k")
    assert asked == [local_llm.CPU_ANSWER]


def test_the_model_frees_the_card_first_and_loads_on_the_processor_when_it_is_full(monkeypatch, tmp_path):
    from studia import components
    from studia.engine import models

    monkeypatch.setattr(local_llm, "_loaded", {})
    monkeypatch.setattr(components, "llm_ready", lambda key: True)
    monkeypatch.setattr(components, "llm_dir", lambda key: tmp_path)
    monkeypatch.setattr(models, "resolve_device", lambda pref: ("cuda", "float16"))
    monkeypatch.setattr(models, "setup_cuda_dlls", lambda: [])
    monkeypatch.setattr(models, "cuda_compute_type", lambda prefer=(): prefer[0])
    monkeypatch.setattr(models, "detect_gpu", lambda quick=False: {"available": True, "vram_mb": 8188})
    events, full = [], []

    class Generator:
        def __init__(self, path, device, compute_type, intra_threads):
            events.append(("load", device))
            if device == "cuda" and full:
                raise RuntimeError("CUDA failed with error out of memory")

    monkeypatch.setitem(sys.modules, "ctranslate2", types.SimpleNamespace(Generator=Generator))
    monkeypatch.setitem(sys.modules, "tokenizers",
                        types.SimpleNamespace(Tokenizer=types.SimpleNamespace(from_file=lambda path: "tok")))
    monkeypatch.setattr(local_llm, "make_room", lambda: events.append(("free speech model",)) or True)
    m = local_llm._load("qwen3-4b")
    assert events == [("free speech model",), ("load", "cuda")] and m["device"] == "cuda"
    assert local_llm.budget("qwen3-4b") == local_llm.answer_tokens("qwen3-4b") == 1400     # what fits on 8 GB
    assert local_llm._load("qwen3-4b") is m                      # loaded once
    events.clear()
    assert local_llm._load("qwen3-4b", cpu=True)["device"] == "cpu" and events == [("load", "cpu")]
    assert local_llm.budget("qwen3-4b") == local_llm.CPU_BUDGET and local_llm.answer_tokens("qwen3-4b") == 1200
    local_llm._loaded.clear()
    events.clear()
    full.append(1)                                               # e.g. a transcription holds the card
    assert local_llm._load("qwen3-1.7b")["device"] == "cpu"
    local_llm._loaded.clear()
    assert local_llm._gpu_room("qwen3-1.7b") == (6000, 3000)    # the small model: long prompts and answers fit
    assert events == [("free speech model",), ("load", "cuda"), ("load", "cpu")]


def test_the_offline_chat_gives_the_lectures_the_room_the_other_passages_leave(monkeypatch):
    monkeypatch.setattr(local_llm, "budget", lambda key: 2500)
    monkeypatch.setattr(local_llm, "count_tokens", lambda text, key: int(len(text) / 3.5))
    paragraphs = "\n".join(f"[{i // 60:02d}:{i % 60:02d}] The lecturer explains the matrix example number {i} again."
                           for i in range(600))
    session = local_llm.LocalSession("Instructions\n\n=== LECTURES ===\n\n# MATH 250 · 23 Oct\n## Part 1\n"
                                     + paragraphs, "k")
    extra = "[MATH 250 · 16 Oct, Part 2 05:10] The matrix inverse exists when the determinant is not zero. " * 3
    system = session._system("What is the matrix example?", extra)
    assert "=== OTHER MATCHING PASSAGES ===" in system and extra.strip()[:60] in system
    assert local_llm.count_tokens(system, "k") > 2500 * 0.9      # a quarter kept for ~100 tokens left it ~20% empty


@pytest.fixture
def engine(monkeypatch, tmp_path):
    """CTranslate2 and the tokenizer stood in for: .fail["load"] / .fail["tok"] make them fail with that error."""
    from studia import components
    from studia.engine import models

    monkeypatch.setattr(local_llm, "_loaded", {})
    monkeypatch.setattr(components, "llm_ready", lambda key: True)
    monkeypatch.setattr(components, "llm_dir", lambda key: tmp_path)
    monkeypatch.setattr(models, "resolve_device", lambda pref: ("cpu", "int8"))
    monkeypatch.setattr(models, "setup_cuda_dlls", lambda: [])
    fail = {}

    class Generator:
        def __init__(self, path, device, compute_type, intra_threads):
            if "load" in fail:
                raise fail["load"]

    def from_file(path):
        if "tok" in fail:
            raise fail["tok"]
        return "tok"

    monkeypatch.setitem(sys.modules, "ctranslate2", types.SimpleNamespace(Generator=Generator))
    monkeypatch.setitem(sys.modules, "tokenizers", types.SimpleNamespace(Tokenizer=types.SimpleNamespace(from_file=from_file)))
    return types.SimpleNamespace(fail=fail)


def test_a_model_that_wont_load_is_said_with_what_to_do(engine, tmp_path):
    """CTranslate2's own words ("Unable to open file 'model.bin'", "std::bad_alloc") meant nothing to a student. The
    message used to send them to Settings → Storage, which doesn't list the model in use (nor removes it), and to a
    Set up AI that Settings showed only for a model not downloaded: now the model counts as not downloaded, so Settings
    offers it again, and its next download fetches every file afresh."""
    (tmp_path / ".complete").write_text("{}", encoding="utf-8")
    engine.fail["load"] = RuntimeError("Unable to open file 'model.bin' in model 'C:\\Users\\a\\llm\\qwen3-1.7b'")
    with pytest.raises(RuntimeError, match=r"^The offline model's files on this PC are damaged, so it can't be loaded\. "
                                           r"Download it again: Settings → AI → Set up AI"):
        local_llm._load("qwen3-1.7b")
    assert (tmp_path / ".damaged").is_file() and not (tmp_path / ".complete").exists()
    engine.fail.clear()
    engine.fail["tok"] = Exception("expected value at line 1 column 1")       # a damaged tokenizer.json
    with pytest.raises(RuntimeError, match="files on this PC are damaged"):
        local_llm._load("qwen3-1.7b")
    engine.fail.clear()
    engine.fail["load"] = RuntimeError("std::bad_alloc")                       # the PC's memory, on the processor
    with pytest.raises(RuntimeError, match=r"^There isn't enough free memory for the offline model\. Close other programs"):
        local_llm._load("qwen3-1.7b")


def test_an_engine_that_does_not_load_on_this_pc_is_said(engine, monkeypatch):
    monkeypatch.setitem(sys.modules, "ctranslate2", None)                      # its native library failed to load
    with pytest.raises(RuntimeError, match=r"^The engine that runs the offline model could not start on this PC \(.*\)\. Reinstalling"):
        local_llm._load("qwen3-1.7b")


def test_a_damaged_model_is_downloaded_again_whole(monkeypatch, tmp_path):
    from studia import components, httpdl

    target = tmp_path / "qwen3-1.7b"
    target.mkdir()
    (target / "model.bin").write_bytes(b"x" * 10)                  # the right size, and damaged all the same
    (target / ".damaged").write_text("1", encoding="utf-8")
    got = []

    class Answer:
        def raise_for_status(self):
            pass

        def json(self):
            return {"siblings": [{"rfilename": "model.bin", "size": 10}, {"rfilename": "tokenizer.json", "size": 2}]}

    session = types.SimpleNamespace(get=lambda *a, **k: Answer())
    monkeypatch.setattr(components, "_session", lambda: session)

    def download(url, dest, **kw):
        got.append(dest.name)
        dest.write_bytes(b"y" * (10 if dest.name == "model.bin" else 2))
        return dest

    monkeypatch.setattr(httpdl, "download", download)
    components.download_hf("someone/model", target, ["*"], lambda d, n: None)
    assert sorted(got) == ["model.bin", "tokenizer.json"] and (target / "model.bin").read_bytes() == b"y" * 10
    assert not (target / ".damaged").exists() and (target / ".complete").is_file()


def test_a_card_that_cant_get_memory_is_taken_as_full_not_broken():
    """cuBLAS says ALLOC_FAILED or NOT_INITIALIZED when the card's memory is taken (a browser, a game): that passes."""
    assert local_llm._out_of_memory(RuntimeError("cuBLAS failed with status CUBLAS_STATUS_ALLOC_FAILED"))
    assert local_llm._out_of_memory(RuntimeError("cuBLAS failed with status CUBLAS_STATUS_NOT_INITIALIZED"))
    assert not local_llm._out_of_memory(RuntimeError("CUDA failed with error no kernel image is available"))


def test_running_out_of_memory_with_nothing_left_to_try_is_said(monkeypatch):
    monkeypatch.setattr(local_llm, "_loaded", {"k": {"device": "cpu", "room": 2500, "answer": 1200, "used": 0.0}})
    monkeypatch.setattr(local_llm, "make_room", None)

    def run():
        raise RuntimeError("std::bad_alloc")

    with pytest.raises(RuntimeError, match=r"^There isn't enough free memory for the offline model"):
        local_llm._with_less_memory("k", run)
