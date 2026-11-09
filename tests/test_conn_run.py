"""Running the app when something goes wrong (fx-conn-run): every flow ends in success or in a plain message that says
what to do. The speech model's download or load that stops never leaves the queue bar spinning with no button; a
double click starts one AI run and one export."""

import threading
import time

import pytest

from studia.cancel import Cancelled
from studia.domain import Course
from test_jobs import FakeEngine, env, finals, wait_for  # noqa: F401 - env is a fixture
from test_page_feedback import node, run


class _ModelStops(FakeEngine):
    """A speech model that isn't on this PC: its download (or load) starts, then stops with ``fail``."""
    loaded = False

    def __init__(self, stage, fail):
        super().__init__()
        self.stage, self.stop = stage, fail

    def ensure_model(self, progress, cancel=None):
        progress("model-download", 0.0, "Downloading the small model (~484 MB, one time)")
        progress("model-download", 0.4, "Downloading model 180 / 484 MB")
        if self.stage == "load":
            progress("model-load", 0.0, "Loading the speech model")
        raise self.stop


@pytest.mark.parametrize("stage,fail,stage_end,says", [
    ("download", Cancelled(), "cancelled", ""),
    ("download", ConnectionError("[WinError 10054] An existing connection was forcibly closed by the remote host"),
     "failed", "The speech model couldn't be downloaded. The connection broke off before the download was complete."),
    ("download", OSError(28, "No space left on device"), "failed", "Your disk is full."),
    ("download", __import__("requests").exceptions.ChunkedEncodingError(
        "('Connection broken: IncompleteRead(1024 bytes read, 4096 more expected)', IncompleteRead(...))"), "failed",
     "The speech model couldn't be downloaded. The connection broke off before the download was complete."),
    ("download", __import__("requests").exceptions.ConnectionError("Max retries exceeded"), "failed",
     "The speech model couldn't be downloaded. Can't reach the server. Check your internet connection"),
    ("download", __import__("requests").HTTPError("expected HTTP 206, got 403"), "failed",
     "The speech model couldn't be downloaded. The download server answered with an error. What was downloaded"),
    ("load", RuntimeError("Unable to open file 'model.bin' in model 'C:\\models\\small'"), "failed",
     "The speech model couldn't be loaded: Unable to open file"),
    ("load", RuntimeError("std::bad_alloc"), "failed", "There wasn't enough free memory to load the speech model."),
    ("load", MemoryError(), "failed", "There wasn't enough free memory to load the speech model."),
])
def test_a_speech_model_that_stops_takes_its_bar_with_it_and_says_why(env, stage, fail, stage_end, says):
    env["state"]["engine"] = _ModelStops(stage, fail)
    jm = env["make"]()
    jm.enqueue(["r1"])
    assert wait_for(lambda: finals(env["events"], "r1"))
    end = finals(env["events"], "r1")[0]
    assert end["stage"] == stage_end
    assert says in (end.get("error") or ""), end.get("error")
    if stage == "download" and stage_end == "failed":     # it goes on where it stopped: transcribing again says so
        assert "What was downloaded is kept: transcribing the lecture again goes on from there." in end["error"]
    assert wait_for(lambda: [e for e in env["events"] if e.get("type") == "queue"][-1]["jobs"] == [])
    engine = [e for e in env["events"] if e.get("type") == "engine"]
    assert engine[-1]["phase"] == "failed"                # not "download" or "load": the page drops its bar
    assert not jm.busy()


def test_a_model_the_engine_loads_again_by_itself_ends_ready(env):
    """The engine may load the model again inside transcribe (it was freed meanwhile): its last step is "ready"."""
    class Reloads(FakeEngine):
        def transcribe(self, audio, progress, cancel, meta):
            progress("model-load", 0.0, "Loading the speech model")
            progress("model-load", 1.0, "Model ready (CPU)")
            return super().transcribe(audio, progress, cancel, meta)

    env["state"]["engine"] = Reloads()
    jm = env["make"]()
    jm.enqueue(["r1"])
    assert wait_for(lambda: finals(env["events"], "r1"))
    assert finals(env["events"], "r1")[0]["stage"] == "done"
    assert [e for e in env["events"] if e.get("type") == "engine"][-1]["phase"] == "ready"


QUEUE_BAR = """
  const bar = { innerHTML: "", hidden: false, offsetHeight: 40, style: {}, dataset: {}, contains: () => false,
    classList: { add: (c) => { if (c === "hidden") bar.hidden = true; }, remove: (c) => { if (c === "hidden") bar.hidden = false; },
      toggle() {}, contains: (c) => c === "hidden" && bar.hidden }, querySelector: () => null, querySelectorAll: () => [] };
  els["#queue"] = bar;
  const shown = () => (bar.hidden ? "hidden" : plain(bar.innerHTML.replace(/<[^>]*>/g, " ").replace(/\\s+/g, " ").trim()));
  const job = { id: "r1", title: "EEE 215 · lecture", stage: "transcribing", progress: 0, detail: "Preparing the speech model" };
  ev({ type: "queue", jobs: [job] });
  const now = performance.now;
  ev({ type: "engine", phase: "download", progress: 0.1, message: "Downloading model 48 / 484 MB" });
  performance.now = () => now.call(performance) + 30000;             // 30 s later, at 40 %: about a minute to go
  ev({ type: "engine", phase: "download", progress: 0.4, message: "Downloading model 180 / 484 MB" });
  out.downloading = shown();
  performance.now = () => now.call(performance) + 600000;            // the pace dropped: 25 % in 10 minutes
  ev({ type: "engine", phase: "download", progress: 0.35, message: "Downloading model 170 / 484 MB" });   // (again)
  ev({ type: "engine", phase: "download", progress: 0.6, message: "Downloading model 290 / 484 MB" });
  out.slower = shown();
  performance.now = now;
  ev({ type: "queue", jobs: [] });                     // cancelled (or failed) with no "failed" from an older app
  out.after = shown();
  out.engine = S.engine;
  ev({ type: "engine", phase: "load", progress: 0, message: "Loading the speech model" });   // a late one: no lecture
  out.late = shown();
  ev({ type: "queue", jobs: [{ ...job, id: "r2", detail: "Downloading" }] });
  out.next = shown();
  // a download that resumes: its first word is 0 %, then the parts on disk count at once (40 %): no speed. The real
  // pace is 1 % every 5 s, so about 5 minutes are left (it said "less than a minute left" for minutes)
  ev({ type: "engine", phase: "load", progress: 1, message: "" });
  ev({ type: "queue", jobs: [{ ...job, id: "r3" }] });
  const t0 = now.call(performance);
  performance.now = () => t0;
  ev({ type: "engine", phase: "download", progress: 0, message: "Downloading the small model (~484 MB, one time)" });
  performance.now = () => t0 + 1500;
  ev({ type: "engine", phase: "download", progress: 0.4, message: "Downloading model 194 / 484 MB" });
  performance.now = () => t0 + 30000;
  ev({ type: "engine", phase: "download", progress: 0.457, message: "Downloading model 221 / 484 MB" });
  out.resumed = shown();
  performance.now = now;
"""


@node
def test_the_queue_bar_never_spins_over_an_empty_queue(tmp_path):
    out = run(tmp_path, QUEUE_BAR)
    assert "Downloading the speech model (one time)" in out["downloading"] and "Cancel" in out["downloading"]
    assert "Downloading model 180 / 484 MB · about 1 min left" in out["downloading"]    # 30 % in 30 s: 60 % to go
    assert "Downloading model 290 / 484 MB · about " in out["slower"] and " left" in out["slower"]
    assert out["after"] == "hidden" and out["engine"] is None
    assert out["late"] == "hidden"
    assert "Loading the speech model" in out["next"]       # a new lecture while the model still loads: said
    assert "Downloading model 221 / 484 MB · about 5 min left" in out["resumed"]       # not "less than a minute"


# ------------------------------------------------------------------ one click, one run
def _api(monkeypatch):
    from studia import ai
    from studia import api as api_mod
    from studia.services import Services

    api = api_mod.Api(Services())
    api._push = lambda evt: None
    monkeypatch.setattr(api_mod, "reveal", lambda path: None)              # no Explorer window from a test
    monkeypatch.setattr(ai, "claude_info", lambda: time.sleep(0.2) or {"available": True, "can_plans": True})
    api._s.lib.upsert_courses([Course(id="c", title="2026-1 MATH 240 P 1", code="MATH 240", name="P", term="T")])
    return api


def _together(fn, n=2):
    """``fn`` called by n threads at once, as pywebview does with a double click (each call on a thread of its own)."""
    out, go = [], threading.Barrier(n)

    def one():
        go.wait()
        out.append(fn())
    threads = [threading.Thread(target=one) for _ in range(n)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(10)
    return out


def test_a_weekly_digest_that_cant_be_saved_says_why_in_plain_words(isolated_home, monkeypatch):
    """It said "[Errno 13] Permission denied: 'C:/Users/x/OneDrive/…/d.md'"."""
    from studia import ai, digest

    api = _api(monkeypatch)
    pushed = []
    api._push = pushed.append
    monkeypatch.setattr(digest, "weekly_digest", lambda lib, settings, offset=0: {"week": {"start": "2026-11-02"}})
    monkeypatch.setattr(digest, "digest_prompt", lambda d: "p")
    monkeypatch.setattr(digest, "digest_markdown", lambda d, text: text)

    def one_shot(*a, **k):
        raise PermissionError(13, "Permission denied", "C:/Users/x/OneDrive/IEUStudia/Weekly digests/d.md")

    monkeypatch.setattr(ai, "one_shot", one_shot)
    assert api.digest_summary(0)["ok"]
    assert wait_for(lambda: any(e.get("phase") == "error" for e in pushed if e.get("type") == "digest"))
    said = next(e["message"] for e in pushed if e.get("type") == "digest" and e.get("phase") == "error")
    assert "Errno" not in said and "d.md" in said and ("Windows did not let IEUStudia use" in said
                                                      or "isn't allowed to use" in said)


def test_a_double_click_on_write_study_pack_starts_one_plan(isolated_home, monkeypatch):
    api = _api(monkeypatch)
    started = []
    monkeypatch.setattr(api._s.plans, "start", lambda pid, from_stage="": started.append(pid))
    scope = {"kind": "pack", "weeks": [4], "files": ["r1"], "days": [], "picked": True}
    answers = _together(lambda: api.plan_create("c", scope))
    assert [a["ok"] for a in answers] == [True, True] and len(started) == 1
    assert answers[0]["plan"]["id"] == answers[1]["plan"]["id"] == started[0]
    assert len(api._s.lib.plans("c")) == 1
    other = api.plan_create("c", {**scope, "weeks": [5]})                  # another pack: one of its own
    assert other["ok"] and len(started) == 2 and other["plan"]["id"] == started[1]
    api._s.lib.update_plan(started[0], status="done")                     # the first one ended: asking again is new
    again = api.plan_create("c", scope)
    assert again["ok"] and len(started) == 3 and again["plan"]["id"] not in started[:2]
    # a new version of a plan likewise
    upd = _together(lambda: api.plan_update(started[1], {"difficulties": "Bayes"}))
    assert [a["ok"] for a in upd] == [True, True] and len(started) == 4 and upd[0]["plan"]["id"] == upd[1]["plan"]["id"]


def test_a_double_click_on_save_as_pdf_makes_one_export(isolated_home, monkeypatch):
    api = _api(monkeypatch)
    runs, gate = [], threading.Event()

    def export(day_key):
        runs.append(day_key)
        gate.wait(10)
        return {"zip": isolated_home / "pack.zip"}

    monkeypatch.setattr(api._s, "export_pack", export)
    answers = _together(lambda: api.export_pack("c|2026-10-14"))
    assert sorted(bool(a.get("running")) for a in answers) == [False, True] and all(a["ok"] for a in answers)
    assert wait_for(lambda: runs == ["c|2026-10-14"])
    assert api.export_pack("c|2026-10-21") == {"ok": True}                 # another day: its own export
    gate.set()
    assert wait_for(lambda: not api.__dict__.get("_exporting"))
    assert api.export_pack("c|2026-10-14") == {"ok": True}                 # done: it can be made again
    assert wait_for(lambda: runs.count("c|2026-10-14") == 2)


def test_a_pack_pdf_is_printed_once_and_one_that_cant_be_made_says_why(isolated_home, monkeypatch, tmp_path):
    from studia import api as api_mod
    from studia import export, studypack

    api = _api(monkeypatch)
    pid = api._s.plans.create("c", {"kind": "pack"})
    html = tmp_path / "MATH 240 - Study pack.html"
    api._s.lib.update_plan(pid, status="done", result={"file": str(html)})
    assert "no longer in its folder (moved or deleted)" in api.pack_pdf(pid, "open")["error"]
    html.write_text("<p>Bayes</p>", encoding="utf-8")
    monkeypatch.setattr(studypack, "print_pdf", lambda h, p: False)
    monkeypatch.setattr(export, "find_browser", lambda: "msedge.exe")       # Edge is there, but didn't print
    said = api.pack_pdf(pid, "open")["error"]
    assert "couldn't print the PDF" in said and str(html) in said
    monkeypatch.setattr(export, "find_browser", lambda: "")
    assert api.pack_pdf(pid, "open")["error"] == "The PDF could not be made: Microsoft Edge was not found."

    prints, opened = [], []

    def print_pdf(h, p):
        prints.append(p)
        time.sleep(0.3)
        p.write_bytes(b"%PDF-1.7")
        return True

    monkeypatch.setattr(studypack, "print_pdf", print_pdf)
    monkeypatch.setattr(api_mod, "open_path", lambda p: opened.append(p))
    answers = _together(lambda: api.pack_pdf(pid, "open"))                # Open pressed twice (or Save and Open)
    assert [a["ok"] for a in answers] == [True, True] and len(prints) == 1 and len(opened) == 2


def test_study_notes_start_once_and_a_file_that_cant_be_written_is_said_plainly(isolated_home, monkeypatch):
    api = _api(monkeypatch)
    pushed, gate = [], threading.Event()
    api._push = pushed.append

    def notes(day_key, template, include_slides, model, cancel, on_text, effort=""):
        gate.wait(10)
        raise PermissionError(13, "Access is denied", "C:/Users/x/Documents/IEUStudia/MATH 240/AI notes/n.md")

    monkeypatch.setattr(api._s, "ai_notes", notes)
    answers = _together(lambda: api.ai_notes("c|2026-10-14"))
    assert sorted(a["ok"] for a in answers) == [False, True]
    assert "is already working on something" in next(a for a in answers if not a["ok"])["error"]
    gate.set()
    assert wait_for(lambda: api._ai_cancel is None)
    err = [e for e in pushed if e.get("type") == "ai" and e.get("phase") == "error"]
    assert len(err) == 1 and "n.md" in err[0]["message"] and "Access is denied" not in err[0]["message"]


GO_TWICE = """
  require(web + "/studypack.js");
  let calls = 0, answer = null;
  const slow = () => { calls++; return new Promise((r) => { answer = r; }); };
  St.mocks.plan_create = slow; St.mocks.plan_update = slow;
  S.courses = [{ id: "c1", code: "MATH 240", display_name: "Probability" }];
  const btn = (label) => ({ dataset: {}, innerHTML: label, disabled: false });
  for (const [open, go, label] of [["pack-new", "pk-go", "Write study pack"], ["plan-new", "pd-go", "Build plan"]]) {
    calls = 0;
    await A[open]({ dataset: { id: "c1" } }); await tick();
    const b = btn(label);
    els[go === "pk-go" ? ".modal [data-act='pk-go']" : ".modal [data-act='pd-go']"] = b;
    const first = A[go](b), second = A[go](b);           // a double click (or a click on a dialog drawn again)
    await tick();
    const busy = { calls, disabled: b.disabled, label: plain(b.innerHTML.replace(/<[^>]*>/g, "").trim()) };
    answer({ ok: false, error: "ChatGPT is not connected." }); await first; await second; await tick();
    out[go] = { busy, after: { disabled: b.disabled, label: b.innerHTML }, toasts: told() };
  }
"""


@node
def test_the_dialogs_go_buttons_start_one_run_and_come_back_after_a_failure(tmp_path):
    out = run(tmp_path, GO_TWICE)
    for go, label in (("pk-go", "Write study pack"), ("pd-go", "Build plan")):
        assert out[go]["busy"] == {"calls": 1, "disabled": True, "label": "Starting…"}, out[go]
        assert out[go]["after"] == {"disabled": False, "label": label}
        assert "error: ChatGPT is not connected." in out[go]["toasts"]


SILENT = """
  const call = St.call;                                // (the preview's own transcribe_day can't fail: this one does)
  St.call = (name, ...a) => (name === "transcribe_day" ? Promise.resolve({ ok: false, error: "IEULearn needs you to sign in again." }) : call(name, ...a));
  require(web + "/studypack.js");
  St.mocks.materials_download = () => ({ ok: false, error: "IEUStudia is still starting. Try again in a moment." });
  St.mocks.plan_delete = () => ({ ok: false, error: "This plan no longer exists." });
  St.ask = () => Promise.resolve(true);                // Delete, in IEUStudia's own dialog
  S.courses = [{ id: "c1", code: "MATH 240", display_name: "Probability" }];
  await A["pack-new"]({ dataset: { id: "c1" } }); await tick();
  await A["pk-transcribe"](); out.pk_tr = told();
  await A["pk-download"](); out.pk_dl = told();
  await A["plan-new"]({ dataset: { id: "c1" } }); await tick();
  await A["pd-download"](); out.pd_dl = told();
  S.plans = [{ id: "p1" }]; S.planCache = { p1: {} };
  await A["plan-delete"]({ dataset: { id: "p1" } }); out.del = { toasts: told(), kept: S.plans.length };
"""


@node
def test_dialog_buttons_that_fail_say_so_instead_of_nothing_or_nothing_left(tmp_path):
    out = run(tmp_path, SILENT)
    assert out["pk_tr"] == ["error: IEULearn needs you to sign in again."]          # not "Nothing left to transcribe"
    assert out["pk_dl"] == out["pd_dl"] == ["error: IEUStudia is still starting. Try again in a moment."]
    assert out["del"] == {"toasts": ["error: This plan no longer exists."], "kept": 1}


# ------------------------------------------------------------------ switching profiles always opens the next one
END_NOW = """
import threading, time
from concurrent.futures import ThreadPoolExecutor

def background():                       # a sync's listing, or a download's parts, queued from a daemon thread
    ThreadPoolExecutor(2).submit(time.sleep, 60)

threading.Thread(target=background, daemon=True).start()
time.sleep(0.3)
from studia import app
app._end_now()
"""


def test_a_copy_that_switches_profiles_ends_at_once_though_a_download_still_runs():
    """Python waits at exit for every thread pool's work (here 60 s): the copy that switches profiles must not, as the
    next copy waits for its single-instance lock with nothing on screen and gives up after 30 s."""
    import subprocess
    import sys

    import os

    started = time.monotonic()
    env = {k: v for k, v in os.environ.items() if k != "PYTEST_CURRENT_TEST"}      # (the app outside a test)
    r = subprocess.run([sys.executable, "-c", END_NOW], capture_output=True, text=True, timeout=50, env=env,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert r.returncode == 0, r.stderr
    assert time.monotonic() - started < 15                                  # (the import takes a few seconds)
    from studia import app

    with pytest.raises(RuntimeError, match="patch it"):                  # never inside a test: it would end the run
        app._end_now()


def test_main_ends_the_copy_once_the_next_one_is_started(monkeypatch):
    from studia import app

    ended = []
    monkeypatch.setattr(app, "_end_now", lambda: ended.append(True))
    for why in ("restart", "closed"):                    # the next profile's copy started, or the window closed
        monkeypatch.setattr(app, "_main", lambda args, why=why: why)
        app.main([])
    assert ended == [True, True]
    monkeypatch.setattr(app, "_main", lambda args: None)  # it didn't open (another copy has the window), an update
    app.main([])
    assert ended == [True, True]


def _app_window(monkeypatch, start):
    import subprocess
    import sys
    import types

    from studia import app, osutil
    from test_app_restart import FakeWindow

    winutil = osutil.impl()
    seen = {"popen": [], "boxes": []}
    fake = types.ModuleType("webview")

    def create_window(title, url=None, js_api=None, **kw):
        seen["window"] = FakeWindow(js_api)
        return seen["window"]

    fake.create_window, fake.start = create_window, lambda **kw: start(seen)
    monkeypatch.setitem(sys.modules, "webview", fake)
    monkeypatch.setattr(winutil, "acquire_single_instance", lambda *a, **k: True)
    monkeypatch.setattr(app, "acquire_single_instance", lambda *a, **k: True)
    monkeypatch.setattr(app, "set_app_id", lambda *a, **k: None)
    monkeypatch.setattr(winutil, "message_box", lambda text, title="", flags=0: seen["boxes"].append(text) or 1)
    return app, seen


def test_switching_profiles_while_the_setup_downloads_asks_first_and_stops_the_download(isolated_home, monkeypatch):
    import subprocess

    from studia import accounts

    other = accounts.create("bob", "Bob B", "2024")

    def start(seen):
        w = seen["window"]
        api = w.api
        assert api._ready.wait(30)
        api._setup_running, api._setup_cancel = True, threading.Event()       # the first start's download goes on
        seen["asked"] = api.profile_switch(other.id)
        seen["active_meanwhile"] = accounts.load()[0]
        seen["switch"] = api.profile_switch(other.id, True)                 # "Switch profiles anyway?": yes
        assert w.closed.wait(10)
        seen["cancelled"] = api._setup_cancel.is_set()
        api._setup_running = False

    app, seen = _app_window(monkeypatch, start)
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: seen["popen"].append(cmd))
    assert app._main([]) == "restart"
    assert seen["asked"]["busy"] and "Switching stops the download now" in seen["asked"]["ask"]
    assert seen["active_meanwhile"] != other.id                             # nothing switched on the question
    assert seen["switch"]["ok"] and accounts.load()[0] == other.id
    assert seen["window"].closing_result == [True] and seen["cancelled"]
    assert [c for c in seen["popen"] if c[1:3] == ["-m", "studia.app"]]     # the next copy was started


@node
def test_an_account_added_while_something_runs_closes_its_dialog_when_the_switch_waits(tmp_path):
    """No to "switch profiles anyway?" left the add-account dialog with "Signing in…" spinning for ever, although
    the profile was made."""
    out = run(tmp_path, """
  St.ask = (q) => { out.asked = q; return Promise.resolve(false); };     // No, in IEUStudia's own dialog
  St.Mock.profile_switch = (id, force) => ({ ok: false, busy: true, ask: "IEUStudia is still downloading what it needs. Switching stops the download now." });
  els["#overlay"] = { innerHTML: "", querySelector: () => null };
  const form = { dataset: { form: "add-account" }, username: { value: "bob.b" }, password: { value: "pw" } };
  for (const f of listeners.submit || []) await f({ target: form, preventDefault() {} });
  await tick();
  out.overlay = els["#overlay"].innerHTML; out.toasts = told();
""")
    assert "Signing in" not in out["overlay"]
    assert any("added as a profile of its own" in m for m in out["toasts"])
    # asked in IEUStudia's own dialog, with the reason from the app under its question
    assert out["asked"]["title"] == "Switch profiles anyway?" and out["asked"]["danger"]
    assert out["asked"]["text"] == "IEUStudia is still downloading what it needs. Switching stops the download now."


def test_opening_the_app_while_its_last_copy_is_still_closing_waits_for_it_without_a_scary_question(isolated_home,
                                                                                                     monkeypatch):
    from studia import app, osutil

    winutil = osutil.impl()
    asked = []

    def start(seen):
        assert seen["window"].api._ready.wait(30)
        seen["window"].destroy()                                          # the window's ×, nothing running

    app_, seen = _app_window(monkeypatch, start)
    monkeypatch.setattr(__import__("subprocess"), "Popen", lambda cmd, **kw: None)
    assert app_._main([]) == "closed"
    name = winutil.instance_name()
    assert winutil.read_instance(name).get("phase") == "closing"          # what a start meanwhile reads

    monkeypatch.setattr(winutil, "acquire_single_instance", lambda n, wait_s=0: asked.append(wait_s) or True)
    monkeypatch.setattr(winutil, "message_box", lambda *a, **k: pytest.fail("asked whether to end a closing copy"))
    assert app._other_instance(name) is True and asked == [20]


def test_a_next_copy_that_cant_be_started_is_said(isolated_home, monkeypatch):
    import subprocess

    from studia import accounts

    other = accounts.create("bob", "Bob B", "2024")

    def start(seen):
        api = seen["window"].api
        assert api._ready.wait(30)
        assert api.profile_switch(other.id)["ok"]
        assert seen["window"].closed.wait(10)

    def popen(cmd, **kw):
        if cmd[1:3] == ["-m", "studia.app"]:
            raise OSError(1260, "This program is blocked by group policy")
        return None

    app, seen = _app_window(monkeypatch, start)
    monkeypatch.setattr(subprocess, "Popen", popen)
    assert app._main([]) == "closed"
    assert seen["boxes"] and "it opens the profile you chose." in seen["boxes"][0]


# ------------------------------------------------------------------ paths Windows takes
def _materials(root, tmp_path):
    import datetime as dt
    import types

    from studia.coursesite import Resource, Unit
    from studia.library import Library
    from studia.materials import MaterialStore

    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="c", title="2026-1 MATH 240 P 1", code="MATH 240", name="P", term="T")])
    unit = "Conditional probability, the law of total probability, Bayes' theorem and independence of events"
    long_a = "Lecture notes on conditional probability and Bayes theorem with worked examples, part one (A).pdf"
    long_b = "Lecture notes on conditional probability and Bayes theorem with worked examples, part one (B).pdf"
    lib.merge_units("c", [Unit("u1", 1, unit, dt.date(2026, 9, 21), dt.date(2026, 9, 27), "",
                               [Resource("r1", "Notes A", long_a, "pdf", "pdf", "u", "d"),
                                Resource("r2", "Notes B", long_b, "pdf", "pdf", "u", "d"),
                                Resource("r3", "Short", "con.pdf", "pdf", "pdf", "u", "d")])],
                    first_sync=True)
    return lib, MaterialStore(types.SimpleNamespace(lib=lib, output_root=root))


def _root(n):
    """An output folder of exactly ``n`` characters (target_path only works out paths: nothing is made there)."""
    from pathlib import Path

    base = Path("C:/Users/cagri.yilmaz/OneDrive - Izmir University of Economics/Documents/IEUStudia")
    if len(str(base)) > n:
        base = Path(str(base)[:n])
    return base.parent / (base.name + "x" * (n - len(str(base))))


@pytest.mark.parametrize("depth", [100, 150, 180])
def test_course_files_under_a_deep_output_folder_stay_within_windows_path_limit(tmp_path, depth):
    from studia.outputs.writer import MAX_PATH_CHARS

    # e.g. C:\Users\cagri.yilmaz\OneDrive - Izmir University of Economics\Documents\IEUStudia (85), or deeper
    lib, store_ = _materials(_root(depth), tmp_path)
    a = store_.target_path(lib.get_material("r1"))
    assert len(str(a)) + len(".part") <= MAX_PATH_CHARS and a.suffix == ".pdf"
    assert a.parent.name.startswith("Week 01 - Co")                      # the unit's name gave way, not all of it
    assert len(a.name) >= (60 if depth <= 150 else 30)                   # the file keeps a useful part of its name
    lib.set_material_file("r1", str(a), 10, "u")
    b = store_.target_path(lib.get_material("r2"))                       # the same start: kept apart by its id
    assert b != a and b.name.endswith(" (r2).pdf") and len(str(b)) + len(".part") <= MAX_PATH_CHARS
    assert store_.target_path(lib.get_material("r3")).name == "con_.pdf"   # a name Windows keeps for a device


def test_a_units_earlier_files_keep_their_folder_and_its_new_ones_join_them(tmp_path):
    """Files saved before the limit counted the whole path sit under the unit's 90-character folder name; new ones
    went to an almost identical second folder (88 characters, under an 85-character OneDrive root)."""
    from studia.materials import PART
    from studia.outputs.writer import MAX_PATH_CHARS

    lib, store_ = _materials(_root(85), tmp_path)
    new_folder = store_.folder_for(lib.get_material("r1"))
    old_folder = new_folder.parent / ("Week 01 - " + "Conditional probability, the law of total probability, Bayes' "
                                      "theorem and independence of events")[:90]
    assert old_folder != new_folder and old_folder.name.startswith(new_folder.name)
    lib.set_material_file("r1", str(old_folder / "Notes A.pdf"), 10, "u")         # downloaded by 3.2.2
    b = store_.target_path(lib.get_material("r2"))
    assert b.parent == old_folder and len(str(b)) + len(PART) <= MAX_PATH_CHARS
    (tmp_path / "fresh").mkdir()
    lib2, store2 = _materials(_root(85), tmp_path / "fresh")                       # nothing there yet: the new name
    assert store2.target_path(lib2.get_material("r2")).parent == store2.folder_for(lib2.get_material("r2"))


def test_a_short_output_folder_keeps_the_unit_names_as_they_were(tmp_path):
    lib, store_ = _materials(_root(len("C:/Users/mirza/Documents/IEUStudia")), tmp_path)
    a = store_.target_path(lib.get_material("r1"))
    assert len(a.parent.name) == 90 and a.name == lib.get_material("r1")["file_name"]


def test_an_output_folder_too_deep_for_any_name_says_to_choose_a_shorter_one(tmp_path):
    from studia.outputs.writer import PathTooLong

    lib, store_ = _materials(_root(240), tmp_path)
    with pytest.raises(PathTooLong) as e:
        store_.target_path(lib.get_material("r1"))
    assert "Choose a shorter folder in Settings → Files" in str(e.value)


def test_a_course_file_that_cant_be_saved_says_why_in_plain_words(isolated_home):
    import types

    from studia.api import Api
    from studia.outputs.writer import PathTooLong

    pushed = []
    errors = {"locked": PermissionError(13, "Access is denied", "C:/Users/x/Documents/IEUStudia/MATH 240/Materials/"
                                                                 "Week 01 - Counting/Slides.pdf.part"),
              "deep": PathTooLong("C:/x is too deep a folder for Windows: there is no room left in it for the name "
                                  "of Slides.pdf (a whole path can have at most 259 characters). Choose a shorter "
                                  "folder in Settings → Files, for example one right in Documents.")}

    def download(rid, session, progress):
        raise errors[rid]

    a = Api.__new__(Api)
    a._mat_lock, a._mat_queue, a._mat_running = threading.Lock(), ["locked", "deep"], True
    a._courses_dirty = threading.Event()
    a._push = pushed.append
    sync = types.SimpleNamespace(worker_session=lambda: object(), absorb=lambda s: None)
    a._services = types.SimpleNamespace(materials=types.SimpleNamespace(download=download, index_text=lambda rid: 3),
                                        sync=sync)
    a._ready = threading.Event()
    a._ready.set()
    a._material_worker()
    said = {e["id"]: e["error"] for e in pushed if e.get("state") == "error"}
    assert "Slides.pdf.part" in said["locked"] and "Access is denied" not in said["locked"]
    assert said["deep"] == str(errors["deep"])


@pytest.mark.parametrize("name,safe", [("con", "con_"), ("AUX.pdf", "AUX_.pdf"), ("nul.tar.gz", "nul_.tar.gz"),
                                       ("COM1", "COM1_"), ("lpt9 .txt", "lpt9_.txt"), ("Conference", "Conference"),
                                       ("console.pdf", "console.pdf"), ('a<b>:c"/d|e?*', "abcde"), (" x. ", "x")])
def test_names_windows_refuses_are_made_safe(name, safe):
    from studia.outputs.writer import safe_name

    assert safe_name(name) == safe


def test_a_local_file_with_a_long_title_is_written_under_a_deep_folder(tmp_path):
    from studia.domain import Segment, Transcript
    from studia.outputs.writer import MAX_PATH_CHARS, write_local

    root = tmp_path / ("x" * max(1, 150 - len(str(tmp_path))))
    t = Transcript(delivery_id="local", segments=[Segment(0, 1, "hello", "en", 0.9)], duration_s=1, speech_s=1,
                   language_seconds={"en": 1}, model="m", device="cpu", engine_version="t",
                   created_at="2026-10-20T10:00:00", meta={})
    title = "Signals and Systems Lecture 14 - The Laplace transform, region of convergence and inverse transforms"
    p = write_local(root, f"{title} (full lecture, 1080p, with captions and the board notes).mp4", t)
    assert p.is_file() and p.suffix == ".md" and len(str(p)) + len(".tmp") <= MAX_PATH_CHARS


def test_a_path_too_long_for_windows_is_said_with_what_to_do():
    import errno
    import sys

    from studia.net import plain_error

    long = "C:\\Users\\student\\" + "x" * 250 + "\\notes.pdf.part"
    errors = [OSError(errno.ENAMETOOLONG, "File name too long", long)]
    if sys.platform == "win32":
        errors += [OSError(None, "The filename or extension is too long", long, 206),
                   FileNotFoundError(None, "The system cannot find the path specified", long, 3)]
    for exc in errors:
        said = plain_error(exc)
        # (Linux: one name longer than 255 bytes; Windows' rule isn't named there)
        head = " is too long for Windows" if sys.platform == "win32" else " is too long a name for this computer"
        assert said.startswith(long + head) and "Settings → Files" in said, said
    if sys.platform == "win32":                       # a short path not found stays "can't be reached"
        assert "can't be reached" in plain_error(FileNotFoundError(None, "not found", "C:\\x\\y.pdf", 3))


# ------------------------------------------------------------------ a graphics card that can't run the speech model
GTX970 = "NVIDIA GeForce GTX 970, 4096, 566.36"


@pytest.fixture(autouse=True)
def _card_asked_afresh():
    """hardware._card_now keeps the card for a whole run: each test here asks its own (fake) one, and leaves none."""
    from studia import hardware

    hardware._card_now.cache_clear()
    yield
    hardware._card_now.cache_clear()


def _smi(monkeypatch, answers):
    """nvidia-smi as a fake: what it prints for each --query-gpu (an unknown field: its error, exit code 2). Other
    commands run as they would."""
    import subprocess

    from studia import hardware

    asked, real = [], subprocess.run

    def run(cmd, *a, **kw):
        query = next((x for x in cmd if str(x).startswith("--query-gpu=")), None) if isinstance(cmd, list) else None
        if query is None:
            return real(cmd, *a, **kw)
        query = query[len("--query-gpu="):]
        asked.append(query)
        out = answers.get(query, 'Field "compute_cap" is not a valid field to query.')
        return subprocess.CompletedProcess(cmd, 2 if "not a valid field" in out else 0, out + "\n", "")

    monkeypatch.setattr(hardware.subprocess, "run", run)
    hardware._card_now.cache_clear()
    return asked


def test_nvidia_smi_gives_the_compute_capability_and_an_older_driver_still_gives_the_rest(monkeypatch):
    from studia import hardware

    _smi(monkeypatch, {"name,memory.total,driver_version,compute_cap": GTX970 + ", 5.2"})
    assert hardware.nvidia_gpu() == {"present": True, "name": "NVIDIA GeForce GTX 970", "vram_mb": 4096,
                                     "driver": "566.36", "compute_cap": "5.2"}
    asked = _smi(monkeypatch, {"name,memory.total,driver_version": "NVIDIA GeForce GTX 1050, 4096, 512.15"})
    gpu = hardware.nvidia_gpu()
    assert gpu["present"] and gpu["driver"] == "512.15" and gpu["compute_cap"] == "" and len(asked) == 2


def test_a_card_too_old_for_the_speech_model_is_not_set_up_and_says_so():
    from studia import hardware
    from test_setup import _facts

    old = hardware.assess(_facts(gpu={"name": "NVIDIA GeForce GTX 970", "vram_mb": 4096, "driver": "566.36",
                                      "compute_cap": "5.2"}))
    assert old["gpu"] is False and old["recommended_model"] == "small" and old["recommended_llm"] == "qwen3-1.7b"
    assert any("too old a graphics card for the speech recognition" in w for w in old["warnings"])
    assert hardware.assess(_facts(gpu={"compute_cap": "6.1"}))["gpu"] is True          # a GTX 1050: it works
    assert hardware.assess(_facts())["gpu"] is True                                    # a driver that doesn't say


def test_a_card_that_couldnt_run_the_model_is_remembered_until_its_driver_changes(monkeypatch):
    from studia import hardware
    from studia.engine import models
    from test_setup import _facts

    _smi(monkeypatch, {"name,memory.total,driver_version,compute_cap": "NVIDIA GeForce RTX 3050, 4096, 566.36, 8.6"})
    monkeypatch.setattr(models, "cublas_available", lambda: True)
    monkeypatch.setattr(models, "detect_gpu", lambda quick=False: {"available": True, "name": "RTX 3050", "vram_mb": 4096})
    monkeypatch.setattr(models, "cuda_compute_type", lambda prefer=(): "float16")
    card = {"name": "NVIDIA GeForce RTX 3050", "vram_mb": 4096, "driver": "566.36", "compute_cap": "8.6"}
    assert models.resolve_device("auto") == ("cuda", "float16") and hardware.assess(_facts(gpu=card))["gpu"]

    hardware.remember_card_failed("CUDA failed with error no kernel image is available for execution on the device",
                                  "large-v3-turbo")
    assert models.resolve_device("auto") == ("cpu", "int8")                 # Automatic: the processor at once
    assert models.resolve_device("cuda") == ("cuda", "float16")             # "Graphics card" chosen: tried again
    verdict = hardware.assess(_facts(gpu=card))
    assert verdict["gpu"] is False and verdict["recommended_model"] == "small"
    assert any("couldn't run the speech model when IEUStudia tried it" in w for w in verdict["warnings"])
    assert hardware.assess(_facts(gpu={**card, "driver": "572.16"}))["gpu"]   # a driver update: tried again
    hardware.forget_card_failed()
    assert models.resolve_device("auto") == ("cuda", "float16")


class _CardFails(FakeEngine):
    """The model loaded on the processor after the graphics card failed (as Engine.ensure_model falls back)."""
    model_name = "large-v3-turbo"
    device = "cpu"

    def __init__(self, error):
        super().__init__()
        self.gpu_error = error
        self.device_info = {"device": "cpu", "warning": "The GPU could not be used, so transcription runs on the CPU "
                                                        "(slower)."}


def test_a_card_that_cant_run_the_model_is_said_once_and_remembered(env, monkeypatch):
    from studia import hardware

    _smi(monkeypatch, {"name,memory.total,driver_version,compute_cap": GTX970 + ", 5.2"})
    env["state"]["engine"] = _CardFails("CUDA failed with error no kernel image is available for execution on the device")
    jm = env["make"]()
    jm.enqueue(["r1"])
    assert wait_for(lambda: finals(env["events"], "r1"))
    jm.enqueue(["r2"])
    assert wait_for(lambda: finals(env["events"], "r2"))
    toasts = [e for e in env["events"] if e.get("type") == "toast"]
    assert len(toasts) == 1 and toasts[0]["level"] == "warning"
    assert "choose the Small model in Settings → Transcription" in toasts[0]["message"]
    for did in ("r1", "r2"):
        assert finals(env["events"], did)[0]["notes"] == [toasts[0]["message"]]
    assert hardware.card_failed()["model"] == "large-v3-turbo"


def test_a_card_that_was_only_full_is_not_remembered(env, monkeypatch):
    from studia import hardware

    _smi(monkeypatch, {"name,memory.total,driver_version,compute_cap": "NVIDIA GeForce RTX 4060, 8188, 566.14, 8.9"})
    env["state"]["engine"] = _CardFails("CUDA failed with error out of memory")
    jm = env["make"]()
    jm.enqueue(["r1"])
    assert wait_for(lambda: finals(env["events"], "r1"))
    assert finals(env["events"], "r1")[0]["notes"] == ["The GPU could not be used, so transcription runs on the CPU "
                                                       "(slower)."]
    assert not [e for e in env["events"] if e.get("type") == "toast"] and hardware.card_failed() == {}


def test_a_card_that_was_short_of_memory_or_had_a_one_off_fault_is_not_remembered(monkeypatch):
    """cuBLAS says ALLOC_FAILED when the card's memory is taken (a browser, a game on a 4 GB card): that made every
    later lecture go to the much slower processor until a driver update. Now only a card that can't run the code is
    remembered at once; another failure counts when it comes a second time, and Settings is told at once."""
    from studia import hardware

    _smi(monkeypatch, {"name,memory.total,driver_version,compute_cap": "NVIDIA GeForce GTX 1650, 4096, 566.36, 7.5"})
    told = []
    monkeypatch.setattr(hardware, "on_card_change", lambda: told.append(1))
    for passing in ("cuBLAS failed with status CUBLAS_STATUS_ALLOC_FAILED",
                    "cuBLAS failed with status CUBLAS_STATUS_NOT_INITIALIZED",
                    "CUDA failed with error unspecified launch failure"):
        assert hardware.remember_card_failed(passing, "large-v3-turbo") is False and hardware.card_failed() == {}
    odd = "CUDA failed with error something nobody has seen"
    assert hardware.remember_card_failed(odd, "large-v3-turbo") is False and hardware.card_failed() == {}
    assert told == []
    assert hardware.remember_card_failed(odd, "large-v3-turbo") is True and hardware.card_failed()["count"] == 2
    assert told == [1]
    hardware.forget_card_failed()
    assert told == [1, 1]
    assert hardware.remember_card_failed("CUDA failed with error no kernel image is available for execution on the "
                                         "device", "offline AI") is True
    hardware.forget_card_failed()


class _CardSkipped(FakeEngine):
    """The processor at once: the card failed with this driver before (Automatic), so it wasn't tried."""
    model_name = "large-v3-turbo"
    device = "cpu"
    device_info = {"device": "cpu"}


def test_a_lecture_that_skips_the_card_because_it_failed_before_says_so(env, monkeypatch):
    from studia import hardware

    _smi(monkeypatch, {"name,memory.total,driver_version,compute_cap": "NVIDIA GeForce GTX 1650, 4096, 566.36, 7.5"})
    hardware.remember_card_failed("CUDA failed with error no kernel image is available for execution on the device")
    env["state"]["engine"] = _CardSkipped()
    jm = env["make"]()
    jm.enqueue(["r1"])
    assert wait_for(lambda: finals(env["events"], "r1"))
    assert finals(env["events"], "r1")[0]["notes"] == [
        "The processor transcribed this lecture: your graphics card couldn't run the speech model before. To try the "
        "card again, choose Graphics card in Settings → Transcription."]
    hardware.forget_card_failed()


def test_a_card_that_failed_once_in_an_unknown_way_is_tried_again_next_time(env, monkeypatch):
    from studia import hardware

    _smi(monkeypatch, {"name,memory.total,driver_version,compute_cap": "NVIDIA GeForce GTX 1650, 4096, 566.36, 7.5"})
    env["state"]["engine"] = _CardFails("CUDA failed with error something nobody has seen")
    jm = env["make"]()
    jm.enqueue(["r1"])
    assert wait_for(lambda: finals(env["events"], "r1"))
    assert finals(env["events"], "r1")[0]["notes"] == [
        "Your graphics card couldn't run the speech model this time, so the processor transcribed this lecture. The "
        "next lecture tries the card again."]
    assert not [e for e in env["events"] if e.get("type") == "toast"] and hardware.card_failed() == {}
    hardware.forget_card_failed()


def test_the_offline_ai_goes_to_the_processor_when_the_card_cant_run_it(monkeypatch, tmp_path):
    import sys
    import types

    from studia import components, hardware, local_llm
    from studia.engine import models

    _smi(monkeypatch, {"name,memory.total,driver_version,compute_cap": GTX970 + ", 6.1"})
    monkeypatch.setattr(local_llm, "_loaded", {})
    monkeypatch.setattr(components, "llm_ready", lambda key: True)
    monkeypatch.setattr(components, "llm_dir", lambda key: tmp_path)
    monkeypatch.setattr(models, "resolve_device", lambda pref: ("cuda", "float16"))
    monkeypatch.setattr(models, "setup_cuda_dlls", lambda: [])
    monkeypatch.setattr(models, "cuda_compute_type", lambda prefer=(): prefer[0])
    monkeypatch.setattr(models, "detect_gpu", lambda quick=False: {"available": True, "vram_mb": 4096})
    monkeypatch.setattr(local_llm, "_make_room", lambda: False)
    loads, seen = [], []

    class Generator:                                 # it loads on the card, and fails at its first step there
        def __init__(self, path, device, compute_type, intra_threads):
            loads.append(device)

    monkeypatch.setitem(sys.modules, "ctranslate2", types.SimpleNamespace(Generator=Generator))
    monkeypatch.setitem(sys.modules, "tokenizers",
                        types.SimpleNamespace(Tokenizer=types.SimpleNamespace(from_file=lambda path: "tok")))

    def answer():
        seen.append(local_llm._load("qwen3-1.7b")["device"])
        if seen[-1] == "cuda":
            raise RuntimeError("CUDA failed with error no kernel image is available for execution on the device")
        return "ok"

    assert local_llm._with_less_memory("qwen3-1.7b", answer) == "ok"
    assert seen == ["cuda", "cpu"] and loads == ["cuda", "cpu"]
    assert hardware.card_failed()["model"] == "offline AI"

    def broken():                                    # the app's own errors are not taken for the card's
        raise RuntimeError("The offline AI model is not downloaded yet (Settings → AI).")

    local_llm._loaded.clear()
    loads.clear()
    local_llm._load("qwen3-1.7b")
    with pytest.raises(RuntimeError, match="not downloaded"):
        local_llm._with_less_memory("qwen3-1.7b", broken)
    assert loads == ["cuda"]
