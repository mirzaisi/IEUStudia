"""A lecture whose end can't be saved (the library full or locked for long while it fails) still leaves the queue: the
worker's thread goes on with the next lecture, Cancel all empties the queue and the PC may sleep again."""

import sqlite3

import pytest

from studia.quality import NoSpeech

from test_jobs import FakeEngine, env, finals, wait_for  # noqa: F401 - env is a fixture


def _library_full_once(env, monkeypatch, method):
    lib = env["lib"]
    real = getattr(lib, method)
    failed = []

    def once(*args, **kwargs):
        if not failed and (method != "set_status" or args[1] == "failed"):     # the write of how it ended
            failed.append(args)
            raise sqlite3.OperationalError("database or disk is full")
        return real(*args, **kwargs)

    monkeypatch.setattr(lib, method, once)
    return failed


@pytest.mark.parametrize("error, method", [(OSError(28, "No space left on device"), "set_status"),
                                           (NoSpeech("No speech was found in this recording."), "set_warnings")])
def test_a_lecture_whose_end_cant_be_saved_never_stops_the_queue(env, monkeypatch, error, method):
    class FailsFirst(FakeEngine):
        def transcribe(self, audio, progress, cancel, meta):
            self.fail = error if meta["delivery_id"] == "r1" else None
            return super().transcribe(audio, progress, cancel, meta)

    env["state"]["engine"] = FailsFirst()
    failed = _library_full_once(env, monkeypatch, method)
    jm = env["make"]()
    jm.enqueue(["r1"])
    assert wait_for(lambda: finals(env["events"], "r1"))
    assert failed and finals(env["events"], "r1")[0]["stage"] == "failed"
    jm.enqueue(["r2"])                                       # the transcribe thread is still there
    assert wait_for(lambda: finals(env["events"], "r2"))
    assert finals(env["events"], "r2")[0]["stage"] == "done"
    assert wait_for(lambda: not jm.busy())                   # updates allowed, the PC may sleep


def test_a_download_whose_end_cant_be_saved_never_stops_the_downloads(env, monkeypatch):
    from studia import jobs as jobs_mod

    real = jobs_mod.JobManager._prepare
    calls = []

    def prepare(self, job):
        calls.append(job.id)
        if job.id == "r1":
            raise ValueError("Something unexpected")
        return real(self, job)

    monkeypatch.setattr(jobs_mod.JobManager, "_prepare", prepare)
    _library_full_once(env, monkeypatch, "set_status")
    jm = env["make"]()
    jm.enqueue(["r1"])
    assert wait_for(lambda: finals(env["events"], "r1"))
    jm.enqueue(["r2"])
    assert wait_for(lambda: finals(env["events"], "r2"))
    assert finals(env["events"], "r2")[0]["stage"] == "done" and wait_for(lambda: not jm.busy())
