"""The window stays responsive while lectures download and transcribe: events reach the page in few calls,
onnxruntime never starts while the speech model's libraries load, and slow C calls that hold the GIL are avoided
(TLS contexts filled again, FFmpeg's threads per slide frame, CTranslate2 starting the graphics card driver)."""

import json
import shutil
import ssl
import subprocess
import sys
import threading
import time
import types

import pytest

from studia import nativeload
from studia.api import EVENT_GAP_S, Api, events_script


class _Window:
    def __init__(self):
        self.calls = []

    def run_js(self, script):
        self.calls.append((time.monotonic(), script))


def _wait(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while not cond() and time.monotonic() < end:
        time.sleep(0.01)
    return cond()


def test_a_burst_of_events_reaches_the_page_in_one_call(isolated_home):
    api = Api()
    win = api._window = _Window()
    for i in range(120):                        # a full sync queueing lectures: 120 jobs at once
        api._push({"type": "job", "id": f"j{i}", "stage": "queued"})
    api._push({"type": "job", "id": "j0", "stage": "downloading", "progress": 0.5})    # only the latest of j0
    api._push({"type": "toast", "level": "info", "message": "</script> hi"})
    assert _wait(lambda: win.calls)
    time.sleep(0.3)
    assert len(win.calls) == 1
    script = win.calls[0][1]
    assert "</script>" not in script
    events = json.loads(script[script.index("})([") + 3:-1].replace("<\\/", "</"))
    assert len(events) == 121 and events[0]["type"] == "toast"
    assert {e["id"]: e["stage"] for e in events[1:]}["j0"] == "downloading"

    api._push({"type": "toast", "level": "info", "message": "next"})        # the next burst waits its turn
    assert _wait(lambda: len(win.calls) == 2)
    assert win.calls[1][0] - win.calls[0][0] >= EVENT_GAP_S - 0.01


def _events(script):
    return json.loads(script[script.index("})([") + 3:-1].replace("<\\/", "</"))


def test_the_courses_go_once_a_burst_and_the_newest_wins(isolated_home):
    api = Api()
    built = []
    api._course_views = lambda: built.append(1) or [{"id": "fresh"}]
    win = api._window = _Window()
    api._push({"type": "courses", "courses": [{"id": "old"}]})      # a sync's early list, then its full one
    api._push({"type": "courses", "courses": [{"id": "newer"}]})
    api._push({"type": "sync", "phase": "done"})
    assert _wait(lambda: win.calls)
    time.sleep(0.1)
    assert [(e["type"], e.get("courses")) for e in _events(win.calls[0][1])] == [
        ("courses", [{"id": "newer"}]), ("sync", None)]                # where the last one was (before "done")

    api._push({"type": "courses", "courses": [{"id": "stale"}]})
    api._push({"type": "toast", "level": "info", "message": "x"})
    api._courses_dirty.set()                                           # a job ended meanwhile: built once, here
    assert _wait(lambda: len(win.calls) == 2)
    assert [(e["type"], e.get("courses")) for e in _events(win.calls[1][1])] == [
        ("courses", [{"id": "fresh"}]), ("toast", None)]
    assert len(built) == 1

    api._courses_dirty.set()                                           # nothing pushed: it comes last, as before
    api._push({"type": "toast", "level": "info", "message": "y"})
    assert _wait(lambda: len(win.calls) == 3)
    assert [e["type"] for e in _events(win.calls[2][1])] == ["toast", "courses"]


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_the_page_gets_every_event_in_order_even_when_one_fails():
    script = events_script([{"type": "a"}, {"type": "boom"}, {"type": "b", "text": "</div>"}])
    js = ("const seen = []; const console = { error: () => seen.push('error') }; const window = { __lsEvent: (e) => {"
          " if (e.type === 'boom') throw new Error('x'); seen.push(e.type + (e.text || '')); } };"
          f"{script}; process.stdout.write(JSON.stringify(seen));")
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
    assert json.loads(out.stdout) == ["a", "error", "b</div>"]
    assert subprocess.run(["node", "-e", "const window = {};" + script], timeout=30).returncode == 0  # no page yet


def test_onnxruntime_waits_while_the_speech_model_loads(monkeypatch):
    started = []

    def fake_preload(package, patterns):
        started.append(package)
        sys.modules["onnxruntime"] = types.ModuleType("onnxruntime")        # what the import then finds
        return 0

    real = sys.modules.pop("onnxruntime", None)
    monkeypatch.setattr(nativeload, "preload", fake_preload)
    try:
        with nativeload.LOAD_LOCK:              # the model's libraries are loading
            th = threading.Thread(target=nativeload.import_onnxruntime)
            th.start()
            time.sleep(0.2)
            assert started == []
        th.join(5)
        assert started == ["onnxruntime"]
        with nativeload.LOAD_LOCK:              # started once: later calls don't wait
            nativeload.import_onnxruntime()
        assert started == ["onnxruntime"]
    finally:
        sys.modules.pop("onnxruntime", None)
        if real is not None:
            sys.modules["onnxruntime"] = real


def test_a_session_gone_leaves_its_tls_context_to_the_next(monkeypatch):
    import gc

    from studia import net

    gc.collect()        # sessions earlier tests left behind go first: their contexts would mix with these
    monkeypatch.setattr(net, "_spare", [])
    made = []
    real = net.tls_context
    monkeypatch.setattr(net, "tls_context", lambda: made.append(1) or real())

    def ctx(s):
        return s.get_adapter("https://x/").poolmanager.connection_pool_kw["ssl_context"]

    a, b = net.make_session(), net.plain_session()
    first = ctx(a)
    assert ctx(b) is not first and len(made) == 2               # sessions alive at once: one context each
    del a
    gc.collect()
    c = net.make_session()
    assert ctx(c) is first and len(made) == 2                  # filled once, used again
    assert ctx(net.make_session()) is not first                # (c still has it)

    net._spare[:] = [ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)]    # another kind (made before truststore came in)
    assert type(ctx(net.make_session())) is not ssl.SSLContext and net._spare == []


def test_a_slide_frame_is_decoded_on_one_thread(monkeypatch):
    import av

    from studia import slides

    made = []

    class Ctx:
        thread_count = 0

        def decode(self, packet):
            return []

    class FakeCodecContext:
        @staticmethod
        def create(name, mode):
            made.append(Ctx())
            return made[-1]

    monkeypatch.setattr(av, "CodecContext", FakeCodecContext)
    assert slides.decode_keyframe(b"avcc", b"\0\0\0\1") is None
    assert [c.thread_count for c in made] == [1]       # not FFmpeg's threads, started anew for every frame


def test_the_graphics_card_driver_starts_before_ctranslate2_asks(monkeypatch):
    from studia.engine import models

    calls = []

    class Driver:
        def cuInit(self, flags):
            calls.append("cuInit")
            return 0

    fake_ct2 = types.ModuleType("ctranslate2")
    fake_ct2.get_cuda_device_count = lambda: calls.append("ctranslate2") or 1
    monkeypatch.setitem(sys.modules, "ctranslate2", fake_ct2)
    monkeypatch.setattr(models, "setup_cuda_dlls", lambda: [])
    monkeypatch.setattr(models.ctypes, "WinDLL", lambda name: Driver(), raising=False)
    monkeypatch.setattr(models.ctypes, "CDLL", lambda name: Driver())
    monkeypatch.setattr(models.subprocess, "run", lambda *a, **k: types.SimpleNamespace(stdout="Card, 8188\n"))
    models.detect_gpu.cache_clear()
    models._start_cuda_driver.cache_clear()
    try:
        assert models.detect_gpu() == {"available": True, "name": "Card", "vram_mb": 8188}
        assert calls == ["cuInit", "ctranslate2"]

        def missing(name):
            raise OSError("no driver")
        monkeypatch.setattr(models.ctypes, "WinDLL", missing, raising=False)
        monkeypatch.setattr(models.ctypes, "CDLL", missing)
        models._start_cuda_driver.cache_clear()
        assert models._start_cuda_driver() is False                  # no NVIDIA driver: nothing breaks
    finally:
        models.detect_gpu.cache_clear()
        models._start_cuda_driver.cache_clear()
