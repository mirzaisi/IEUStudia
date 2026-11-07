"""Fail-safes: a transcription stopped halfway goes on from its checkpoint (and gives the same transcript), the
downloads, syncs and course files wait for the internet connection (or a new sign-in) and go on by themselves, and
closing the window while lectures transcribe asks once in the page."""

import json
import threading
import time
import types
from pathlib import Path

import numpy as np
import pytest
import requests

from studia import jobs as jobs_mod
from studia import net
from studia.cancel import Cancelled
from studia.engine import checkpoint
from studia.engine import transcriber as tr
from studia.engine.core import SR, Decoded, Unit
from studia.ieulearn import AuthError

from test_jobs import FakeEngine, FakePanopto, env, finals, wait_for  # noqa: F401 - env is a fixture


# ---------------------------------------------------------------------------------------------- the checkpoint
class BatchDecoder:
    """Each result says which batch it came from (as a real one may, in its last digits) and the language-ID prior
    comes from the passage itself; runs out of graphics memory above ``max_batch``."""

    def __init__(self, max_batch=99):
        self.max_batch = max_batch
        self.decoded = []                       # the passages decoded, batch by batch

    def features(self, audio, batch):
        return np.array([u.start for u in batch])

    def encode(self, feats):
        return list(feats)

    def language_prior(self, enc, langs):
        return [{"en": (s // SR % 10) / 10, "tr": 1 - (s // SR % 10) / 10} for s in enc]

    def generate(self, enc, batch, langs, opts):
        if len(batch) > self.max_batch:
            raise RuntimeError("CUDA failed with error out of memory")
        self.decoded.append([u.start // SR for u in batch])
        first = batch[0].start // SR
        return [Decoded(lang, f"Passage {u.start // SR} {lang} from {first}.", -0.1 - first / 1000 if lang == "en"
                        else -0.5, 0.01 * first, 1.2, 6) for u, lang in zip(batch, langs)]


AUDIO = np.random.default_rng(0).uniform(-0.3, 0.3, 50 * SR).astype(np.float32)
UNITS = [Unit(k * SR, k * SR + SR * (1 + k % 5)) for k in range(40)]


def _engine(monkeypatch, max_batch=99, model="large-v3-turbo", languages=("en", "tr")):
    monkeypatch.setattr(tr, "detect_gpu", lambda *a, **k: {"vram_mb": 8000, "name": "Card"})
    e = tr.Engine(model, languages=languages)
    e._device, e._decoder, e._wm = "cuda", BatchDecoder(max_batch), object()
    return e


def passages(e):
    """How many passages the engine decoded (each in every language)."""
    return len({k for batch in e._decoder.decoded for k in batch})


def _run(e, stop_after=0, did="r1"):
    """Decode UNITS with the lecture's checkpoint; ``stop_after``: closed after that many batches."""
    cancel, batches = threading.Event(), [0]

    def progress(stage, frac, detail):
        if detail.startswith("Transcribing passage"):
            batches[0] += 1
            if batches[0] == stop_after:
                cancel.set()

    resume = e._checkpoint(did, AUDIO, UNITS, 1.0)
    chosen, conf = e._decode_units(AUDIO, UNITS, progress, cancel, resume)
    return [(d.text, d.lang, d.avg_logprob) for d in chosen], conf


@pytest.mark.parametrize("max_batch", [99, 5])
def test_a_transcription_stopped_halfway_goes_on_and_gives_the_same_transcript(monkeypatch, max_batch):
    whole, whole_conf = _run(_engine(monkeypatch, max_batch), did="other")
    e = _engine(monkeypatch, max_batch)                     # IEUStudia closes after the second batch
    with pytest.raises(Cancelled):
        _run(e, stop_after=2)
    done = passages(e)
    assert 0 < done < 40 and checkpoint.path_for("r1").is_file()
    e = _engine(monkeypatch, max_batch)                     # the next start: a new engine, the same lecture
    again, again_conf = _run(e)
    assert again == whole and again_conf == whole_conf      # passage by passage, to the last digit
    assert passages(e) == 40 - done     # only the passages left were decoded
    if max_batch == 5:                                      # batched as they were after running out of memory
        assert max(len(b) for b in e._decoder.decoded) <= 4


def test_a_checkpoint_of_other_settings_or_audio_is_started_again(monkeypatch):
    e = _engine(monkeypatch)
    with pytest.raises(Cancelled):
        _run(e, stop_after=1)
    e = _engine(monkeypatch, languages=("en",))             # another language list: its passages differ
    _run(e)
    assert passages(e) == 40
    e = _engine(monkeypatch)
    with pytest.raises(Cancelled):
        _run(e, stop_after=1)
    e = _engine(monkeypatch)
    e._compute = "int8_float16"                             # another number format
    _run(e)
    assert passages(e) == 40
    e = _engine(monkeypatch)
    with pytest.raises(Cancelled):
        _run(e, stop_after=1)
    e = _engine(monkeypatch)
    resume = e._checkpoint("r1", AUDIO * 0.5, UNITS, 1.0)   # another recording under the same id
    e._decode_units(AUDIO * 0.5, UNITS, lambda *a: None, None, resume)
    assert passages(e) == 40


def test_a_line_cut_short_by_a_power_cut_is_dropped(monkeypatch):
    whole, _ = _run(_engine(monkeypatch), did="other")
    e = _engine(monkeypatch)
    with pytest.raises(Cancelled):
        _run(e, stop_after=2)
    path = checkpoint.path_for("r1")
    lines = path.read_bytes().split(b"\n")
    assert len(lines) == 4 and lines[-1] == b""             # the key, two batches
    path.write_bytes(b"\n".join(lines[:2]) + b"\n" + lines[2][:40])      # the second batch only half written
    e = _engine(monkeypatch)
    assert _run(e)[0] == whole
    assert passages(e) == 40 - 16


def test_a_whole_transcript_leaves_no_checkpoint_and_a_cancel_or_old_one_is_deleted(monkeypatch, env):
    e = _engine(monkeypatch)
    speech = tr.Speech([(u.start, u.end) for u in UNITS], UNITS, [], {"speech_s": 1.0}, 1.0, 0.1)
    e.transcribe(AUDIO, lambda *a: None, None, {"delivery_id": "r1"}, speech=speech)
    assert not list(checkpoint.folder().glob("*"))
    checkpoint.path_for("old").parent.mkdir(parents=True, exist_ok=True)
    checkpoint.path_for("old").write_text("{}")
    assert checkpoint.sweep(days=-1) == 1 and not checkpoint.path_for("old").exists()
    assert checkpoint.path_for("../x y").parent == checkpoint.folder()     # an odd id stays in its folder

    class Stopping(FakeEngine):                             # stops halfway, as the engine does when cancelled
        def transcribe(self, audio, progress, cancel, meta):
            checkpoint.path_for(meta["delivery_id"]).write_text("{}")
            while not cancel.is_set():
                time.sleep(0.01)
            raise Cancelled()

    env["state"]["engine"] = Stopping()
    jm = env["make"]()
    jm.enqueue(["r1"])
    assert wait_for(lambda: checkpoint.path_for("r1").exists())
    jm.shutdown()                                           # closing the app: what is done stays for next time
    assert wait_for(lambda: finals(env["events"], "r1"))
    assert checkpoint.path_for("r1").exists()
    jm = env["make"]()
    jm.enqueue(["r2"])
    assert wait_for(lambda: checkpoint.path_for("r2").exists())
    jm.cancel("r2")                                         # you cancelled it: nothing to go on from
    assert wait_for(lambda: finals(env["events"], "r2"))
    assert wait_for(lambda: not checkpoint.path_for("r2").exists())


# ---------------------------------------------------------------------------------------------- the connection
@pytest.fixture
def offline(monkeypatch):
    state = {"online": False, "asked": 0}

    def online(timeout=5.0, stop=None):
        state["asked"] += 1
        return state["online"]

    monkeypatch.setattr(net, "online", online)
    monkeypatch.setattr(net, "ONLINE_EVERY_S", 0.05)
    return state


def test_the_downloads_wait_for_the_connection_and_go_on_by_themselves(env, offline):
    fail = {"left": 1}
    download = FakePanopto.download

    def flaky(self, src, dest, progress, cancel=None, prefer="audio"):
        if fail["left"]:
            fail["left"] -= 1
            raise net.NetworkError("Can't reach ieu.cloud.panopto.eu. Check your internet connection.")
        return download(self, src, dest, progress, cancel, prefer)

    env["mp"].setattr(FakePanopto, "download", flaky)
    jm = env["make"]()
    jm.enqueue(["r1", "r2"])
    ev = env["events"]
    assert wait_for(lambda: any(e.get("type") == "queue" and e.get("waiting") == "offline" for e in ev))
    assert wait_for(lambda: any(e.get("type") == "job" and e.get("step") == "offline" for e in ev))
    waiting = [e for e in ev if e.get("type") == "job" and e.get("step") == "offline"]
    assert waiting[-1]["stage"] == "queued" and waiting[-1]["detail"] == "Waiting for the internet connection"
    time.sleep(0.3)                                         # an hour without the internet: nothing fails
    assert not finals(ev, "r1") and not finals(ev, "r2") and FakePanopto.downloads == []
    offline["online"] = True
    assert wait_for(lambda: finals(ev, "r1") and finals(ev, "r2"))
    assert [finals(ev, d)[0]["stage"] for d in ("r1", "r2")] == ["done", "done"]
    assert jm.job("r1") is None and jm.queue_event()["waiting"] == ""


def test_a_wrong_password_holds_the_downloads_until_you_sign_in_again(env, offline):
    offline["online"] = True
    creds = {"ok": False}

    def ensure_panopto(self, cid=None):
        if not creds["ok"]:
            raise AuthError("Wrong username or password.")

    env["mp"].setattr(jobs_mod.Panopto, "delivery_info", lambda self, did: (_ for _ in ()).throw(
        jobs_mod.NeedPanoptoAuth("expired")) if not creds["ok"] else {"Delivery": {"Duration": 60.0}})
    from test_jobs import FakeSync

    env["mp"].setattr(FakeSync, "ensure_panopto", ensure_panopto)
    env["mp"].setattr(FakeSync, "has_credentials", lambda self: creds["ok"], raising=False)
    jm = env["make"]()
    jm.enqueue(["r1"])
    ev = env["events"]
    assert wait_for(lambda: any(e.get("type") == "job" and e.get("step") == "signin" for e in ev))
    time.sleep(0.3)
    assert not finals(ev, "r1") and jm.queue_event()["waiting"] == "signin"
    creds["ok"] = True                                      # you signed in again (Sync)
    assert wait_for(lambda: finals(ev, "r1"))
    assert finals(ev, "r1")[0]["stage"] == "done"


def test_until_online_waits_for_the_connection_and_runs_again(offline):
    calls, said = [], []

    def sync():
        calls.append(1)
        if len(calls) == 1:
            raise net.NetworkError("Can't reach learn.ieu.edu.tr.")
        if len(calls) == 2:
            offline["online"] = True
            return types.SimpleNamespace(errors=["EEE 215: Can't reach ieu.cloud.panopto.eu."])
        return types.SimpleNamespace(errors=[])

    threading.Timer(0.2, lambda: offline.update(online=True)).start()
    res = net.until_online(sync, lambda: said.append(1), lambda r: bool(r.errors))
    assert len(calls) == 2 and said == [1] and res.errors     # the connection was back: its errors are real ones
    offline["online"] = False
    with pytest.raises(requests.HTTPError):                 # an answer from a server is never waited out
        net.until_online(lambda: (_ for _ in ()).throw(requests.HTTPError("500")))


def test_a_sync_you_asked_for_waits_for_the_connection(offline):
    from studia.api import Api

    pushed, runs = [], []

    def sync(progress, on_courses):
        runs.append(1)
        if len(runs) == 1:
            threading.Timer(0.2, lambda: offline.update(online=True)).start()
            raise net.NetworkError("Can't reach learn.ieu.edu.tr. Check your internet connection.")
        return types.SimpleNamespace(new_ids=[], profile=None, errors=[])

    lock = threading.Lock()
    lock.acquire()
    content = {"new_materials": [], "new_announcements": [], "errors": []}
    ns = types.SimpleNamespace(_push=pushed.append, _sync_lock=lock, _remind=lambda: None,
                               _courses_dirty=threading.Event(),   # (the courses after the content: one build, see _dispatch)
                               _course_views=lambda: [], _own_new=lambda views, ids: 0,
                               _s=types.SimpleNamespace(sync=types.SimpleNamespace(sync=sync, sync_content=lambda progress=None: content),
                                                        lib=types.SimpleNamespace(get_meta=lambda k: "")))
    assert Api._run_sync(ns) is True and len(runs) == 2
    assert any(e.get("offline") and e["message"].startswith("Waiting for the internet connection") for e in pushed)
    assert not lock.locked()


def test_course_files_wait_for_the_connection(monkeypatch, offline):
    from studia import api as api_mod
    from studia.api import Api

    tries, pushed = {}, []

    def download(rid, session, progress):
        tries[rid] = tries.get(rid, 0) + 1
        if not offline["online"]:
            if tries[rid] == 4:                             # the PC wakes up while it waits
                threading.Timer(0.2, lambda: offline.update(online=True)).start()
            raise net.NetworkError("Can't reach learn.ieu.edu.tr.")
        return f"C:/x/{rid}.pdf"

    monkeypatch.setattr(api_mod.time, "sleep", lambda s: None)
    a = Api.__new__(Api)
    a._mat_lock, a._mat_queue, a._mat_running = threading.Lock(), ["m1", "m2"], True
    a._courses_dirty = threading.Event()
    a._push = pushed.append
    sync = types.SimpleNamespace(worker_session=lambda: object(), absorb=lambda s: None)
    a._services = types.SimpleNamespace(materials=types.SimpleNamespace(download=download, index_text=lambda rid: 3),
                                        sync=sync)
    a._ready = threading.Event()
    a._ready.set()
    a._material_worker()
    assert [(e["id"], e["state"]) for e in pushed if e.get("state") in ("done", "error")] == [("m1", "done"), ("m2", "done")]
    assert {e["id"] for e in pushed if e.get("offline")} == {"m1", "m2"}     # both said they wait for it


# ---------------------------------------------------------------------------------------------- closing
def test_closing_while_lectures_transcribe_asks_in_the_page_once(monkeypatch):
    from studia.api import Api

    pushed, closed, saved = [], [], []
    a = Api.__new__(Api)
    a._push = pushed.append
    a._close_ok, a._close_asked, a._close_seen = False, 0.0, 0.0
    a._window = types.SimpleNamespace(destroy=lambda: closed.append(1))
    a._services = types.SimpleNamespace(update_settings=saved.append)
    a._ready = threading.Event()
    a._ready.set()
    assert a._ask_close() is True and pushed == [{"type": "close-ask"}]     # the page asks
    a.close_seen()
    assert a._ask_close() is True                            # shown: closing again asks there again
    a._close_asked -= 10
    a._close_seen = 0.0                                      # a page that never showed it: Windows' own question
    assert a._ask_close() is False
    assert a.close_now(True)["ok"] and a._close_ok and saved == [{"ask_before_closing": False}]
    assert wait_for(lambda: closed == [1])


def test_the_close_question_is_in_the_page_and_the_setting_kept():
    from pathlib import Path

    from studia.config import Settings, apply_patch

    web = Path(jobs_mod.__file__).parent / "web"
    assert '<script src="failsafe.js"></script>' in (web / "index.html").read_text(encoding="utf-8")
    js = (web / "failsafe.js").read_text(encoding="utf-8")
    assert 'onEvents["close-ask"]' in js and 'call("close_now"' in js and 'call("close_seen")' in js
    assert Settings().ask_before_closing is True
    assert apply_patch(Settings(), {"ask_before_closing": False}).ask_before_closing is False
    assert json.loads(json.dumps(Settings().to_json()))["ask_before_closing"] is True


def test_a_camera_stream_download_goes_on_from_its_last_segment(tmp_path, monkeypatch):
    from studia import panopto

    playlist = "#EXTM3U\n" + "".join(f"#EXTINF:10,\nseg{k}.ts?token=a\n" for k in range(6))
    monkeypatch.setattr(panopto.hls, "media_playlist", lambda s, url, prefer: types.SimpleNamespace(
        text=playlist, url="https://x/s/index.m3u8"))
    fetched, down = [], {"on": True}

    class Session:
        def get(self, url):
            k = int(url.split("seg")[1].split(".")[0])
            if k == 3 and down["on"]:
                raise requests.ConnectionError("the Wi-Fi went away")
            fetched.append(k)
            return types.SimpleNamespace(content=bytes([65 + k]) * 1000, raise_for_status=lambda: None)

    dest = tmp_path / "r1.mp4"
    p = panopto.Panopto(Session())
    with pytest.raises(requests.ConnectionError):
        p.download(panopto.MediaSource("hls", "https://x/master.m3u8"), dest, lambda d, t: None)
    assert fetched == [0, 1, 2] and not dest.exists()
    with open(dest.with_name("r1.mp4.part"), "ab") as f:  # the app closed in the middle of the next segment
        f.write(b"D" * 300)
    down["on"] = False
    fetched.clear()
    playlist = playlist.replace("token=a", "token=b")    # the next start gets new links to the same segments
    p.download(panopto.MediaSource("hls", "https://x/master.m3u8"), dest, lambda d, t: None)
    assert fetched == [3, 4, 5]
    assert dest.read_bytes() == b"".join(bytes([65 + k]) * 1000 for k in range(6))
    assert not dest.with_name("r1.mp4.hls.json").exists()


def test_a_camera_stream_s_segments_are_on_the_disk_before_its_marker_says_so(tmp_path, monkeypatch):
    import os

    from studia import panopto

    playlist = "#EXTM3U\n" + "".join(f"#EXTINF:10,\nseg{k}.ts\n" for k in range(3))
    monkeypatch.setattr(panopto.hls, "media_playlist", lambda s, url, prefer: types.SimpleNamespace(
        text=playlist, url="https://x/s/index.m3u8"))
    seen = []

    def fsync(fd):
        seen.append(("fsync", os.fstat(fd).st_size))
        os.fsync(fd)

    def replace(a, b):
        seen.append(("marker", json.loads(Path(a).read_text(encoding="utf-8"))["size"]))
        os.replace(a, b)

    monkeypatch.setattr(panopto, "os", types.SimpleNamespace(fsync=fsync, replace=replace))
    session = types.SimpleNamespace(get=lambda url: types.SimpleNamespace(content=b"x" * 1000,
                                                                          raise_for_status=lambda: None))
    dest = tmp_path / "r1.mp4"
    panopto.Panopto(session).download(panopto.MediaSource("hls", "https://x/master.m3u8"), dest, lambda d, t: None)
    # each segment: the data synced, then the marker written whole (a file of its own, synced, put in place)
    assert [x for x in seen if x[0] == "marker"] == [("marker", 1000), ("marker", 2000), ("marker", 3000)]
    for k in (1, 2, 3):
        assert seen.index(("fsync", k * 1000)) < seen.index(("marker", k * 1000))
    assert dest.stat().st_size == 3000 and not dest.with_name("r1.mp4.hls.json").exists()
