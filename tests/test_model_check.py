"""A power cut during the speech model's download can leave zeros inside it at the right size: model.bin is compared
with Hugging Face's SHA-256 before the model counts as downloaded (downloaded afresh once when it doesn't match), and
a model of IEUStudia's own that won't load is deleted so the next lecture downloads it afresh, instead of "Close
IEUStudia, open it again" at every try. The offline AI model is checked the same way."""

import hashlib
import json
import sys
import types

import pytest

from studia import components, httpdl, jobs
from studia.engine import models
from studia.engine import transcriber as tr

GOOD = b"weights" * 1000


class _Resp:
    status_code = 200

    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


def _hf(model_sha=hashlib.sha256(GOOD).hexdigest()):
    return {"siblings": [{"rfilename": "config.json", "size": 2},
                         {"rfilename": "tokenizer.json", "size": 2},
                         {"rfilename": "model.bin", "size": len(GOOD), "lfs": {"sha256": model_sha, "size": len(GOOD)}}]}


@pytest.fixture(autouse=True)
def no_shared_cache(monkeypatch, tmp_path):
    """Not this PC's own Hugging Face cache (it may hold the models)."""
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hf-cache"))


@pytest.fixture
def hub(monkeypatch):
    """Hugging Face's answer and a download that, for model.bin, first brings what a power cut leaves (zeros where
    the cut came, at the right size), then the real bytes; ``always_bad`` keeps the zeros."""
    state = {"body": _hf(), "always_bad": False, "fetched": []}

    class Session:
        headers = {}

        def get(self, url, params=None, timeout=0):
            return _Resp(state["body"])

    def download(url, dest, session=None, progress=None, cancel=None, parts=1):
        name = dest.name
        state["fetched"].append(name)
        if name == "model.bin":
            bad = state["always_bad"] or state["fetched"].count(name) == 1
            dest.write_bytes(GOOD[:3000] + b"\0" * (len(GOOD) - 3000) if bad else GOOD)
            if bad:                                    # (a part left over keeps its marker)
                (dest.parent / "model.bin.parts.json").write_text("{}")
        else:
            dest.write_bytes(b"{}")
        return dest

    monkeypatch.setattr(models, "plain_session", Session)
    monkeypatch.setattr(components, "_session", Session)
    monkeypatch.setattr(httpdl, "download", download)
    return state


def test_a_damaged_download_is_downloaded_afresh_before_it_counts(hub):
    path = models.download_model("small", lambda done, total: None)
    assert (path / "model.bin").read_bytes() == GOOD and (path / ".complete").is_file()
    assert hub["fetched"].count("model.bin") == 2 and not (path / "model.bin.parts.json").exists()
    assert models.model_is_downloaded("small")


def test_a_download_damaged_twice_is_said_and_never_counts(hub):
    hub["always_bad"] = True
    with pytest.raises(components.DownloadDamaged) as err:
        models.download_model("small", lambda done, total: None)
    assert not models.model_is_downloaded("small") and not (models._models_root() / "small" / "model.bin").exists()
    said = jobs.model_failure("download", err.value)
    assert said.startswith("The speech model's download was damaged") and "is kept" not in said


def test_a_model_without_hashes_downloads_as_before(hub):
    hub["body"] = {"siblings": [{"rfilename": n, "size": 2} for n in ("config.json", "model.bin", "tokenizer.json")]}
    models.download_model("small", lambda done, total: None)
    assert hub["fetched"].count("model.bin") == 1 and models.model_is_downloaded("small")


def test_the_offline_ai_model_is_checked_too(hub, tmp_path):
    target = tmp_path / "llm"
    components.download_hf("someone/model", target, ["*"], lambda d, n: None)
    assert (target / "model.bin").read_bytes() == GOOD and hub["fetched"].count("model.bin") == 2
    assert (target / ".complete").is_file()


# ------------------------------------------------------------------ a model that won't load
def _own_model(name="large-v3-turbo"):
    d = models._models_root() / name
    d.mkdir(parents=True)
    for f in ("config.json", "tokenizer.json"):
        (d / f).write_text("{}")
    (d / "model.bin").write_bytes(b"\0" * 64)
    (d / ".complete").write_text(json.dumps({"repo": "r"}))
    return d


def _loader(monkeypatch, error):
    class WM:
        def __init__(self, path, device, compute_type, cpu_threads, num_workers=1):
            raise error

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=WM))
    monkeypatch.setattr(tr, "setup_cuda_dlls", lambda: [])
    monkeypatch.setattr(tr, "import_onnxruntime", lambda: None)
    monkeypatch.setattr(tr, "resolve_device", lambda p: ("cpu", "int8"))


def test_a_downloaded_model_that_wont_load_is_downloaded_afresh_next_time(monkeypatch):
    d = _own_model()
    assert models.model_is_downloaded("large-v3-turbo")
    _loader(monkeypatch, RuntimeError("Invalid string length in model.bin"))
    with pytest.raises(models.ModelDamaged) as err:
        tr.Engine("large-v3-turbo").ensure_model(lambda *a: None)
    assert not d.exists() and not models.model_is_downloaded("large-v3-turbo")
    said = jobs.model_failure("load", err.value)
    assert "downloads afresh" in said and "Close IEUStudia" not in said


@pytest.mark.parametrize("error", [MemoryError(), RuntimeError("std::bad_alloc"),
                                   RuntimeError("Unable to open file 'model.bin' in model 'x'")])
def test_memory_or_a_held_file_never_deletes_the_model(monkeypatch, error):
    d = _own_model()
    _loader(monkeypatch, error)
    with pytest.raises(type(error)):
        tr.Engine("large-v3-turbo").ensure_model(lambda *a: None)
    assert (d / ".complete").is_file() and (d / "model.bin").is_file()


def test_the_shared_hugging_face_copy_is_never_deleted(monkeypatch, tmp_path):
    shared = tmp_path / "hf" / "snapshot"
    shared.mkdir(parents=True)
    (shared / "model.bin").write_bytes(b"\0")
    monkeypatch.setattr(tr, "local_model_path", lambda m: shared)
    _loader(monkeypatch, RuntimeError("Invalid string length in model.bin"))
    with pytest.raises(RuntimeError) as err:
        tr.Engine("large-v3-turbo").ensure_model(lambda *a: None)
    assert not isinstance(err.value, models.ModelDamaged) and (shared / "model.bin").exists()
