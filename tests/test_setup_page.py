"""The setup's download step and footer (setup.js in node with a stand-in for app.js): a download that failed, was
cancelled or can't start (a full disk, a plan that couldn't be made) never ends the setup there, since everything but
transcription works without it; a download shows its speed and time left, says when nothing arrives and when it is
checking at the end; Windows refusing "start with Windows" is said and the setup goes on, and Settings says so too.

The case behind it: a student whose network blocks huggingface.co saw 'Failed' and 'Try again' forever, and could
never reach the sign-in, although courses, files, announcements and AI don't need the speech model."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from studia import i18n

WEB = Path(i18n.__file__).parent / "web"

SCRIPT = """
globalThis.window = globalThis;
globalThis.location = { hash: "" };
globalThis.document = { documentElement: {}, querySelector: () => null, querySelectorAll: () => [], addEventListener() {},
  getElementById: () => null };
let now = 1.8e12;
Date.now = () => now;
require(%(i18n)s);
const calls = [], toasts = [];
const answers = {
  setup_plan: () => ({ ok: true, items: [{ id: "speech:small", label: "Speech model (Small)", size_mb: 484 }], total_mb: 484, free_gb: 80, blockers: [] }),
  app_config: (patch) => ({ ok: true, app: { tray: true, start_with_windows: true, ...(patch || {}) }, startup: answers.startup }),
};
answers.startup = false;
const S = { mode: "setup", view: { name: "home" }, boot: { app: {} }, settings: {} };
window.Studia = { S, A: {}, I: (n) => `[${n}]`, SPIN: () => "(spin)", esc: (s) => String(s), onEvents: {}, mocks: {},
  toast: (m, kind) => toasts.push([m, kind]), renderView() {}, render() {}, renderTop() {}, modal() {}, closeModal() {},
  skeleton: () => "(skeleton)", sq: { about: () => "" },
  call: (name, ...args) => { calls.push([name, ...args]); return Promise.resolve(answers[name] ? answers[name](...args) : { ok: true }); } };
require(%(setup)s);
const St = window.Studia, A = St.A, U = St.setup, tick = () => new Promise((r) => setTimeout(r, 5));
St.render = () => {};
const DOWNLOAD = 5, ITEM = { id: "speech:small", label: "Speech model (Small)", size_mb: 484 };
const fresh = (dl, extra) => {
  U.st = { facts: { disk: { data_dir: "C:\\\\Users\\\\x\\\\AppData\\\\Local\\\\IEUStudia" } }, catalog: { speech: [{ id: "small", label: "Small" }] }, verdict: { blockers: [] } };
  U.ch = { speech: "small", ai: "none", tray: true, startup: true };
  U.step = DOWNLOAD; U.running = false; U.done = false; U.dl = dl;
  U.plan = { items: [ITEM], total_mb: 484, free_gb: 80, blockers: [], ...(extra || {}) };
};
const out = {};
(async () => {
  // never tried: download it (no way around it yet)
  fresh({});
  out.untried = St.setupHTML();
  // it failed (the site is blocked on this network): try again, or skip it
  fresh({ "speech:small": { state: "error", error: "huggingface.co can't be reached from this network." } });
  out.failed = St.setupHTML();
  calls.length = 0;
  A["su-skip-dl"]();
  await tick();
  out.skipCalls = JSON.parse(JSON.stringify(calls));
  out.afterSkipStep = U.step;
  out.signin = St.setupHTML();
  // cancelled: the same way past it
  fresh({ "speech:small": { state: "cancelled" } });
  out.cancelled = St.setupHTML();
  // the disk is too full: check again once there's room, or skip
  fresh({}, { blockers: ["There is not enough free disk space."] });
  out.blocked = St.setupHTML();
  calls.length = 0;
  A["su-plan-again"]();
  await tick();
  out.againCalls = calls.map((c) => c[0]);
  // the plan itself couldn't be made
  fresh({}, { items: [], total_mb: 0, blockers: ["IEUStudia is still starting. Try again in a moment."] });
  U.done = true;
  out.noPlan = St.setupHTML();
  // under way: its speed and the time left, then nothing arriving, then the checking at the end
  fresh({});
  U.running = true;
  const ev = (done, total) => St.onEvents.setup({ type: "setup", id: "speech:small", state: "downloading", done, total });
  ev(0, 0);
  out.starting = St.setupHTML();
  now += 20000;
  out.connecting = St.setupHTML();
  const MB = 1048576;
  for (let i = 0; i <= 4; i++) { ev(i * 10 * MB, 400 * MB); now += 1000; }
  out.flowing = St.setupHTML();
  for (let i = 0; i < 25; i++) { ev(40 * MB, 400 * MB); now += 1000; }
  out.stalled = St.setupHTML();
  ev(400 * MB, 400 * MB);
  out.finishing = St.setupHTML();
  // Cancel download: said at once (stopping can take a few seconds), then a way past it
  calls.length = 0;
  A["su-cancel"]();
  out.cancelling = St.setupHTML();
  out.cancelCalls = calls.map((c) => c[0]);
  St.onEvents.setup({ type: "setup", id: "speech:small", state: "cancelled" });
  St.onEvents.setup({ type: "setup", id: "", state: "idle", ok: false });
  out.afterCancel = St.setupHTML();
  // the background step: Windows doesn't let it start with Windows; said, and the setup goes on
  fresh({});
  U.step = 4; toasts.length = 0;
  await A["su-next"]({ dataset: {} });
  await tick();
  out.backgroundToasts = toasts.slice();
  out.afterBackgroundStep = U.step;
  // Settings: on in IEUStudia, off in Windows: said, with a button that turns it on again
  S.mode = "app"; S.view = { name: "settings" }; S.boot.app = { tray: true, start_with_windows: true };
  U.storage = { usage: {}, catalog: { speech: [], llm: [] } };
  U.startupReal = false; U.startupAt = now;
  out.settingsOff = St.settingsMore();
  answers.startup = true; toasts.length = 0;
  await A["startup-fix"]();
  out.fixToasts = toasts.slice();
  out.settingsOn = St.settingsMore();
  // this PC's check failed (IEUStudia still starting, say): the reason, and Try again on the same step
  S.mode = "setup";
  answers.setup_state = () => ({ ok: false, error: "IEUStudia is still starting. Try again in a moment." });
  U.st = null; U.error = "IEUStudia is still starting. Try again in a moment."; U.step = 1;
  out.pcFailed = St.setupHTML();
  calls.length = 0;
  await A["su-state-again"]();
  out.pcAgainCalls = calls.map((c) => c[0]);
  out.pcAgainStep = U.step;
  console.log(JSON.stringify(out));
  process.exit(0);
})().catch((e) => { console.error(e); process.exit(1); });
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_a_download_that_cant_be_done_now_never_ends_the_setup(tmp_path):
    script = tmp_path / "setup.js"
    script.write_text(SCRIPT % {"i18n": json.dumps(str(WEB / "i18n.js")), "setup": json.dumps(str(WEB / "setup.js"))},
                      encoding="utf-8")
    run = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    # never tried: only Download
    assert 'data-act="su-download"' in out["untried"] and "su-skip-dl" not in out["untried"]
    # failed: Try again, and a way past it that says what still works
    for html in (out["failed"], out["cancelled"]):
        assert 'data-act="su-skip-dl"' in html and 'data-act="su-download"' in html
        assert "only transcribing lectures waits for them" in html
    assert "Try again" in out["failed"] and "Cancelled" in out["cancelled"]
    # skipping notes what's left (Downloads at the top offers it again) and goes on to the sign-in, which says so
    assert ["app_config", {"pending_downloads": ["speech:small"]}] in out["skipCalls"]
    assert out["afterSkipStep"] == 6 and "Not finished yet" in out["signin"]
    # a full disk, or no plan at all: Check again (once there's room), or skip; the old disabled button is gone
    for html in (out["blocked"], out["noPlan"]):
        assert 'data-act="su-plan-again"' in html and 'data-act="su-skip-dl"' in html
        assert "disabled" not in html.split('class="su-foot"')[1]
    assert out["againCalls"] == ["setup_plan"]
    # under way: what it does, its speed and time left, a stall, the check at the end
    assert "Starting" in out["starting"] and "Still connecting" not in out["starting"]
    assert "Still connecting" in out["connecting"]
    assert "MB/s" in out["flowing"] and "left" in out["flowing"]
    assert "Nothing has arrived for 2" in out["stalled"] and "keeps trying" in out["stalled"]
    assert "Finishing: checking the files" in out["finishing"]
    for html in (out["starting"], out["flowing"]):
        assert "su-skip-dl" not in html and "Continue while it downloads" in html and "Cancel download" in html
    assert "Stopping the download" in out["cancelling"] and 'data-act="su-cancel"' not in out["cancelling"]
    assert out["cancelCalls"] == ["setup_cancel"]
    assert "Stopping the download" not in out["afterCancel"] and 'data-act="su-skip-dl"' in out["afterCancel"]
    assert "Cancelled" in out["afterCancel"]
    # Windows refused "start with Windows": told what to do, and the setup went on
    assert out["afterBackgroundStep"] == 5
    assert any("Task Manager" in m and "Startup apps" in m for m, _ in out["backgroundToasts"])
    # Settings: on here but off in Windows: said, with Turn it on again; fixed: said, and the note is gone
    assert 'data-act="startup-fix"' in out["settingsOff"] and "Startup apps" in out["settingsOff"]
    assert ["Done: IEUStudia starts with Windows again.", "success"] in out["fixToasts"]
    assert "startup-fix" not in out["settingsOn"]
    # this PC's check failed: never a Continue that stays greyed out with no way on
    assert 'data-act="su-state-again"' in out["pcFailed"] and "still starting" in out["pcFailed"]
    assert out["pcAgainCalls"][0] == "setup_state" and out["pcAgainStep"] == 1


MORE = SCRIPT.split("const out = {};")[0] + """
const out = {};
(async () => {
  // the plan itself couldn't be made: not "everything is already here" beside "0 GB free", and skipping still notes
  // what the choices need, so Downloads at the top offers it again
  fresh({}); U.plan = null;
  answers.setup_plan = () => ({ ok: false, error: "IEUStudia could not check this PC: some error." });
  await A["su-plan-again"](); await tick();
  out.failedPlan = St.setupHTML();
  calls.length = 0;
  A["su-skip-dl"](); await tick();
  out.failedSkip = JSON.parse(JSON.stringify(calls));
  out.failedSignin = St.setupHTML();
  // the offline AI's model failed: what waits is the offline AI, not transcription
  fresh({ "llm:qwen3-1.7b": { state: "error", error: "huggingface.co can't be reached from this network." } },
    { items: [{ id: "llm:qwen3-1.7b", label: "Offline AI model (Basic)", size_mb: 1745 }], total_mb: 1745 });
  U.ch.ai = "local"; U.ch.llm = "qwen3-1.7b";
  out.llmFailed = St.setupHTML();
  U.step = 6;
  out.llmSignin = St.setupHTML();
  // it fails after "Continue while it downloads": the toast says where its reason is
  fresh({}); U.running = true; U.step = 6; toasts.length = 0;
  St.onEvents.setup({ type: "setup", id: "speech:small", state: "error", error: "This PC can't reach the internet right now." });
  St.onEvents.setup({ type: "setup", id: "", state: "idle", ok: false });
  out.signinToasts = toasts.slice();
  console.log(JSON.stringify(out));
  process.exit(0);
})().catch((e) => { console.error(e); process.exit(1); });
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_what_waits_for_a_skipped_download_is_said_as_it_is(tmp_path):
    """The skip texts said only transcription waits, also when the offline AI's model was what failed; a plan that
    couldn't be made said everything was already here; the toast pointed above buttons on a step that has none."""
    script = tmp_path / "more.js"
    script.write_text(MORE % {"i18n": json.dumps(str(WEB / "i18n.js")), "setup": json.dumps(str(WEB / "setup.js"))},
                      encoding="utf-8")
    run = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    assert "couldn't work out what to download" in out["failedPlan"] and "could not check this PC" in out["failedPlan"]
    assert "already on this PC" not in out["failedPlan"] and "Free on" not in out["failedPlan"]
    assert ["app_config", {"pending_downloads": ["speech:small"]}] in out["failedSkip"]
    assert "Not finished yet. Click Downloads at the top to try again" in out["failedSignin"]
    assert "only the offline AI waits for it" in out["llmFailed"] and "transcription work without" in out["llmFailed"]
    assert "the offline AI works once it's done" in out["llmSignin"]
    assert any("Go Back to the Download step" in m for m, _ in out["signinToasts"])
