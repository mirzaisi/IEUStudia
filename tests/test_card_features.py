"""On the graphics card a batch's features are computed with NumPy's BLAS in the (raised) thread alone: its helper
threads run below normal while IEUStudia transcribes, and with other programs busy the card waited for them (5-14x
slower lectures). On the processor nothing changes (the transcripts stay as they were)."""

import contextlib
import types

import numpy as np

from studia.engine import transcriber


class _Decoder:
    def __init__(self, seen):
        self.seen = seen

    def features(self, audio, units):
        self.seen.append(("features", len(units)))
        return np.zeros((len(units), 128, 3000), dtype=np.float32)


def _engine(device, seen):
    eng = transcriber.Engine("large-v3", "cpu")
    eng._device = device
    eng._decoder = _Decoder(seen)
    return eng


def test_features_on_the_card_use_one_blas_thread_and_on_the_processor_as_before(monkeypatch):
    seen = []

    @contextlib.contextmanager
    def one_thread():
        seen.append("one blas thread")
        yield

    monkeypatch.setattr(transcriber, "_blas_in_this_thread", one_thread)
    units = [types.SimpleNamespace(start=0, end=16000)] * 3
    assert _engine("cuda", seen)._features(np.zeros(16000, np.float32), units).shape == (3, 128, 3000)
    assert seen == ["one blas thread", ("features", 3)]
    seen.clear()
    _engine("cpu", seen)._features(np.zeros(16000, np.float32), units)
    assert seen == [("features", 3)]


def test_the_blas_limit_is_one_thread_inside_and_as_before_after():
    from threadpoolctl import threadpool_info

    blas = [x for x in threadpool_info() if x.get("user_api") == "blas"]
    before = [x["num_threads"] for x in blas]
    with transcriber._blas_in_this_thread():
        inside = [x["num_threads"] for x in threadpool_info() if x.get("user_api") == "blas"]
    after = [x["num_threads"] for x in threadpool_info() if x.get("user_api") == "blas"]
    assert all(n == 1 for n in inside) and after == before


def test_without_threadpoolctl_the_features_are_made_as_before(monkeypatch):
    import builtins

    real = builtins.__import__

    def no_threadpoolctl(name, *a, **k):
        if name == "threadpoolctl":
            raise ImportError("not here")
        return real(name, *a, **k)

    monkeypatch.setattr(transcriber, "_blas", None)
    monkeypatch.setattr(builtins, "__import__", no_threadpoolctl)
    with transcriber._blas_in_this_thread():
        pass


def _blas_threads():
    from threadpoolctl import threadpool_info

    return [x["num_threads"] for x in threadpool_info() if x.get("user_api") == "blas"]


def test_two_threads_overlapping_put_the_count_back_whichever_leaves_first():
    """Every lecture's first batch: the transcribe thread computes it while the features thread computes the next.
    A in, B in, A out (still 1 for B), B out: the count is what it was, not 1 for the rest of the session."""
    import threading

    before = _blas_threads()
    a_in, b_in, a_out = threading.Event(), threading.Event(), threading.Event()
    seen = {}

    def a():
        with transcriber._blas_in_this_thread():
            a_in.set()
            b_in.wait(5)
        a_out.set()

    def b():
        a_in.wait(5)
        with transcriber._blas_in_this_thread():
            b_in.set()
            a_out.wait(5)
            seen["b after a left"] = _blas_threads()

    ta, tb = threading.Thread(target=a), threading.Thread(target=b)
    ta.start(), tb.start()
    ta.join(10), tb.join(10)
    assert all(n == 1 for n in seen["b after a left"])        # B still on one thread after A left
    assert _blas_threads() == before                            # and the count back as it was
    with transcriber._blas_in_this_thread():
        pass
    assert _blas_threads() == before