"""A plan, study pack, cheatsheet or review sheet says what its AI does now in every event while it runs (aiwait):
the requests made with the plan's cancel event report to its wait, from the threads that write chapters too."""

import threading

from studia import aiwait
from studia.plans import PlanRunner
from test_plans import env, fake_ask, wait_done  # noqa: F401 - env is a fixture


def test_a_plan_s_events_say_what_its_ai_does_now(env):
    asked = []
    base = fake_ask(asked)

    def ask(system, prompt, model="sonnet", cancel=None, **kw):
        w = aiwait.of(cancel)                       # what ai.one_shot's requests do (ai.py, chatgpt.py)
        w.begin(size=1000)
        w.thinking("**Listing the concepts**\n\nThe quiz covers")
        out = base(system, prompt, model=model, cancel=cancel, **kw)
        w.text("{}")
        w.end(True)
        return out

    runner = PlanRunner(env.svc, ask=ask)
    pid = runner.create("c", {"weeks": [1], "focus": "Quiz 2", "los": [3], "minutes": 60})
    runner.start(pid)
    assert wait_done(env.lib, pid)["status"] == "done"
    plan_events = [e for e in env.events if e.get("type") == "plan" and e["id"] == pid]
    waits = [e["wait"] for e in plan_events if e.get("wait")]
    assert waits and all(w["where"] == "videos" for w in waits)
    assert any(w.get("lines") == ["Listing the concepts"] for w in waits)
    assert any(w["phase"] == "think" for w in waits)
    assert plan_events[-1]["status"] == "done" and "wait" not in plan_events[-1]      # over: nothing to wait for
    assert pid not in runner._waits


def test_study_notes_say_what_the_ai_does_now(tmp_path, monkeypatch):
    """The notes' events carry their wait; the notes being written stream as before; the end carries none."""
    import time
    import types

    from studia.api import Api
    from studia.config import Settings

    out = tmp_path / "notes.md"

    def ai_notes(day_key, template, include_slides, model, cancel, on_text, effort=""):
        w = aiwait.of(cancel)
        w.begin(size=2000, pictures=12)
        w.thinking("**Picking the formulas**")
        on_text("# Notes")
        w.text("# Notes")
        w.end(True)
        out.write_text("# Notes\nDone.", encoding="utf-8")
        return out

    a = Api.__new__(Api)
    pushed = []
    a._push, a._ai_cancel, a._ready, a._starts_lock = pushed.append, None, threading.Event(), threading.Lock()
    a._ready.set()
    a._services = types.SimpleNamespace(settings=Settings(), ai_notes=ai_notes)
    assert a.ai_notes("c|2026-10-13|lecture", "summary")["ok"]
    end = time.monotonic() + 10
    while a._ai_cancel is not None and time.monotonic() < end:
        time.sleep(0.02)
    ai_events = [e for e in pushed if e.get("type") == "ai"]
    assert ai_events[0]["phase"] == "start" and ai_events[0]["wait"]["where"] == "notes"
    waits = [e["wait"] for e in ai_events if e["phase"] == "progress"]
    assert any(w["phase"] == "send" and w["pictures"] == 12 for w in waits)
    assert any(w.get("lines") == ["Picking the formulas"] for w in waits)
    assert ai_events[-1]["phase"] == "done" and "wait" not in ai_events[-1] and ai_events[-1]["text"] == "# Notes\nDone."


def test_a_new_step_counts_its_own_requests_and_pace():
    w = aiwait.Wait(None, kind="pack.outline", where="pack")
    w.units(1, 3)
    w._ended.append(30.0)
    w.set_kind("pack.sections")                     # the plan's step changed (PlanRunner._emit): its own count, pace
    assert "units" not in w.snapshot() and not w._ended
    w.units(0, 5, 2)
    assert w.snapshot()["units"] == [0, 5]
    w.set_kind("pack.sections")                     # the same step again: nothing is lost
    assert w.snapshot()["units"] == [0, 5]


def test_a_chapter_written_in_another_thread_reports_to_its_plan():
    """A study pack writes two chapters at once: both report to the pack's wait (by its cancel event), each its own."""
    cancel = threading.Event()
    seen = []
    w = aiwait.Wait(seen.append, where="pack", kind="pack.sections")
    w.units(0, 4, 2)
    go = threading.Barrier(2)

    def chapter(n):
        aiwait.of(cancel).begin(size=100)
        go.wait()
        aiwait.of(cancel).text("x" * (300 * n))

    with aiwait.tracking(cancel, w):
        threads = [threading.Thread(target=chapter, args=(n,)) for n in (1, 2)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        s = w.snapshot()
    assert s["busy"] == 2 and s["phase"] == "write" and s["units"] == [0, 4]
