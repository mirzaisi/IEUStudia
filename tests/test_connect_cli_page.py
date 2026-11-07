"""Connecting Claude Code or the Codex CLI on the page (setup.js in node with a stand-in for app.js): every step that
waits shows what is going on and has a way out. Installing shows the download's size, speed and time left with a
Cancel button, and a stopped or failed install says what to do next."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from studia import i18n

WEB = Path(i18n.__file__).parent / "web"

HARNESS = """
globalThis.window = globalThis;
globalThis.location = { hash: "" };
const lines = [];                                  // the busy step's line, as the page finds it for a progress tick
globalThis.document = { documentElement: {}, querySelector: () => null, addEventListener() {}, getElementById: () => null,
  querySelectorAll: (sel) => (sel.includes("claude:tool") && globalThis.drawnBusy ? lines : []) };
require(%(i18n)s);
const calls = [], toasts = [];
let draws = 0;
const answers = {};
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const S = { mode: "app", view: { name: "settings" }, boot: { app: { ai_provider: %(which)s } } };
window.Studia = { S, A: {}, I: (n) => `[${n}]`, SPIN: () => "(spin)", esc, onEvents: {}, mocks: {},
  toast: (m, kind) => toasts.push([m, kind]), renderView: () => { draws++; }, render: () => { draws++; },
  call: (name, ...args) => { calls.push([name, ...args]); const a = answers[name]; return Promise.resolve(typeof a === "function" ? a(...args) : (a || { ok: true })); } };
require(%(setup)s);
const St = window.Studia, A = St.A, U = St.setup, tick = () => new Promise((r) => setTimeout(r, 5));
const card = () => St.settingsAI();
const out = {};
"""

INSTALL = HARNESS + """
const fresh = { available: false, version: "", logged_in: false, plan: "", email: "", job: {}, test: {}, login: {},
  plan_status: { ok: null, label: "", note: "" } };
answers.ai_status = () => ({ ok: true, claude: U.ai.claude });
answers.ai_install = { ok: true, job: { state: "running", line: "Starting…", error: "" } };
answers.ai_cli_cancel = { ok: true, stopped: true };
(async () => {
  U.ai = { claude: { ...fresh } };
  out.before = card();
  await A["ai-install"]({ dataset: { id: "claude" } }); await tick();
  St.onEvents.ai_setup({ type: "ai_setup", which: "claude", state: "running", error: "",
    line: "Downloading Claude Code 2.1.318… 120 of 327 MB · 2.4 MB/s · about 2 min left" });
  out.running = card();
  globalThis.drawnBusy = true; lines.push({ textContent: "" }); draws = 0;
  St.onEvents.ai_setup({ type: "ai_setup", which: "claude", state: "running", error: "", line: "Downloading Claude Code 2.1.318… 121 of 327 MB" });
  out.tick = lines[0].textContent; out.drawsForTick = draws;
  await A["ai-cli-cancel"]({ dataset: { id: "claude", what: "install" } }); await tick();
  globalThis.drawnBusy = false;
  St.onEvents.ai_setup({ type: "ai_setup", which: "claude", state: "cancelled", line: "", error: "" }); await tick();
  out.cancelled = card();
  St.onEvents.ai_setup({ type: "ai_setup", which: "claude", state: "error", line: "", error: "Could not download Claude Code: <b>no</b> connection." }); await tick();
  out.failed = card();
  out.calls = calls.map((c) => c.join(":"));
  out.toasts = toasts;
  console.log(JSON.stringify(out));
  process.stdout.write("", () => process.exit(0));      // after the output is out (Linux pipes)
})();
"""


SIGNIN_CLAUDE = HARNESS + """
answers.ai_status = () => ({ ok: true, claude: U.ai.claude });
answers.ai_login = (key, consent, device) => ({ ok: true, state: "waiting", page: "https://claude.ai/oauth/authorize?code=true",
  code: "", device: !!device, asks_code: true, error: "", started: Date.now() / 1000 });
answers.ai_cli_open = { ok: true, launched: true, state: "waiting" };
answers.ai_cli_cancel = { ok: true, stopped: true };
answers.ai_cli_code = { ok: true };
(async () => {
  U.ai = { claude: { available: true, version: "2.1.318", logged_in: false, plan: "", email: "", job: {}, test: {}, login: {},
    plan_status: { ok: null, label: "", note: "" } } };
  out.before = card();
  await A["ai-cli-login"]({ dataset: { id: "claude" } }); await tick();
  out.waiting = card();
  await A["ai-cli-open"]({ dataset: { id: "claude" } }); await tick();
  out.opened = card();
  U.codes.claude = "abc#def";
  await A["ai-cli-code"]({ dataset: { id: "claude" } }); await tick();
  U.ai.claude.login.started = Date.now() / 1000 - 30; delete U.opened.claude;
  out.slow = card();
  St.onEvents.ai_cli_login({ type: "ai_cli_login", which: "claude", state: "error", page: "", code: "", device: false,
    asks_code: false, started: 0, error: "The Claude Code sign-in ended before you were signed in (code 1). Click the button to try again." });
  await tick();
  out.ended = card();
  out.busyAfterEnd = !!U.busy["login:claude"];
  out.early = calls.map((c) => c.join(":"));
  calls.length = 0;
  await A["ai-login"]({ dataset: { id: "claude" } }); await tick();          // as "Connect it now" starts it
  out.connectNow = calls.map((c) => c.join(":"));
  await A["ai-cli-cancel"]({ dataset: { id: "claude", what: "login" } }); await tick();
  out.cancelled = card();
  out.calls = calls.map((c) => c.join(":"));
  out.toasts = toasts;
  console.log(JSON.stringify(out));
  process.stdout.write("", () => process.exit(0));      // after the output is out (Linux pipes)
})();
"""

SIGNIN_CODEX = HARNESS + """
answers.ai_status = () => ({ ok: true, codex: U.ai.codex });
answers.ai_login = (key, consent, device) => ({ ok: true, state: "waiting", page: device ? "https://auth.openai.com/codex/device" : "https://auth.openai.com/oauth/authorize?x=1",
  code: "", device: !!device, asks_code: false, error: "", started: Date.now() / 1000 });
(async () => {
  U.ai = { codex: { available: true, version: "0.157.0", logged_in: false, plan: "", email: "", job: {}, test: {}, login: {},
    plan_status: { ok: null, label: "", note: "" }, models: [] } };
  St.onEvents.ai_cli_login({ type: "ai_cli_login", which: "codex", state: "error", page: "", code: "", device: false, asks_code: false, started: 0,
    error: "Another program is using the port the Codex CLI signs in through (often a Codex sign-in that is still open somewhere). Close other Codex windows and try again, or sign in with a code instead." });
  await tick();
  out.portError = card();
  calls.length = 0;
  await A["ai-cli-login"]({ dataset: { id: "codex", device: "1" } }); await tick();
  out.deviceCalls = calls.map((c) => c.join(":"));
  out.gettingCode = card();
  U.ai.codex.login = { ...U.ai.codex.login, code: "ABCD-12345" };
  out.device = card();
  console.log(JSON.stringify(out));
  process.stdout.write("", () => process.exit(0));      // after the output is out (Linux pipes)
})();
"""


OLD = HARNESS + """
const base = { version: "", logged_in: false, plan: "", email: "", job: {}, test: {}, login: {}, plan_status: { ok: null, label: "", note: "" } };
(async () => {
  U.ai = { claude: { ...base, available: false, too_old: { path: "C:/npm/claude.exe", version: "1.0.30" } } };
  out.old = card();
  S.boot.app.ai_provider = "codex";
  U.ai = { codex: { ...base, models: [], available: false, problem: { path: "C:/x/codex.exe", error: "the file is damaged or not made for this PC" } } };
  out.broken = card();
  console.log(JSON.stringify(out));
  process.stdout.write("", () => process.exit(0));      // after the output is out (Linux pipes)
})();
"""


LOCAL = HARNESS + """
(async () => {
  S.mode = "setup"; U.step = 3; U.ch = { ai: "local", llm: "qwen3-1.7b" };
  U.st = { verdict: { recommended_llm: "qwen3-1.7b", gpu: false }, catalog: { speech: [], llm: [
    { id: "qwen3-1.7b", label: "Basic", description: "Short notes and simple questions", size_mb: 1745, ready: false },
    { id: "qwen3-4b", label: "Better", description: "Clearly better notes", size_mb: 4045, ready: false }] } };
  U.ai = null;                                       // the AI status is still loading (or could not be read)
  out.html = St.setupHTML();
  console.log(JSON.stringify(out));
  process.stdout.write("", () => process.exit(0));      // after the output is out (Linux pipes)
})();
"""


QUIET = HARNESS + """
const fresh = { available: false, version: "", logged_in: false, plan: "", email: "", job: {}, test: {}, login: {},
  plan_status: { ok: null, label: "", note: "" } };
const job = (line, more) => ({ type: "ai_setup", which: "claude", state: "running", error: "", line, ...(more || {}) });
answers.ai_cli_cancel = { ok: true, stopped: true };
(async () => {
  U.ai = { ok: true, claude: { ...fresh, job: job("Downloading Claude Code 2.1.318… 12 of 327 MB") } };   // (as ai_status gives it)
  draws = 0;                                     // another AI's card on screen: Claude's list isn't drawn
  for (let i = 0; i < 20; i++) St.onEvents.ai_setup(job("Downloading Claude Code 2.1.318… " + (13 + i) + " of 327 MB"));
  out.hiddenTicks = draws;
  // the 4-second look, with Claude's list on screen: only the line moved on, so only the line is written
  globalThis.drawnBusy = true; lines.push({ textContent: "" });
  answers.ai_status = () => ({ ok: true, claude: { ...fresh, job: job("Downloading Claude Code 2.1.318… 40 of 327 MB") } });
  draws = 0;
  await St.loadAi("claude"); await tick();
  out.lookDraws = draws; out.lookLine = lines[0].textContent;
  // something else changed: drawn
  answers.ai_status = () => ({ ok: true, claude: { ...fresh, git_bash: "C:/Git/bin/bash.exe", job: job("Downloading Claude Code 2.1.318… 41 of 327 MB") } });
  draws = 0;
  await St.loadAi("claude"); await tick();
  out.changedDraws = draws;
  // Cancel: "Stopping…" at once and its button gone, before the answer comes
  globalThis.drawnBusy = false;
  answers.ai_status = () => ({ ok: true, claude: { ...fresh, git_bash: "C:/Git/bin/bash.exe", job: job("Stopping…", { stopping: true }) } });
  const pressed = A["ai-cli-cancel"]({ dataset: { id: "claude", what: "install" } });
  out.stopping = card();
  await pressed; await tick();
  draws = 0;
  St.onEvents.ai_setup(job("Stopping…", { stopping: true }));     // what the install itself says then
  out.stoppingDraws = draws;
  out.calls = calls.map((c) => c.join(":"));
  console.log(JSON.stringify(out));
  process.stdout.write("", () => process.exit(0));      // after the output is out (Linux pipes)
})();
"""

REFUSED = HARNESS + """
answers.ai_login = (key, consent, device) => ({ ok: true, state: "waiting", page: "https://auth.openai.com/oauth/authorize?x=1",
  code: "", device: !!device, asks_code: false, error: "", started: Date.now() / 1000 });
answers.ai_status = () => ({ ok: true, [St.S.boot.app.ai_provider]: U.ai[St.S.boot.app.ai_provider] });
(async () => {
  // Codex's own status still says signed in, but the test message was refused as signed out
  U.ai = { codex: { available: true, version: "0.167.0", logged_in: true, plan: "chatgpt", email: "", job: {}, models: [], login: {},
    plan_status: { ok: null, label: "ChatGPT", note: "" },
    test: { ok: false, code: "signed_out", error: "The Codex CLI is not signed in. Sign in under Settings → AI.", seconds: 3, at: 1 } } };
  out.refused = card();
  calls.length = 0;
  await A["ai-cli-login"]({ dataset: { id: "codex" } }); await tick();
  out.again = calls.map((c) => c.join(":"));
  U.ai.codex.test = {};
  U.ai.codex.login = { state: "waiting", page: "https://auth.openai.com/oauth/authorize?x=1", started: Date.now() / 1000 };
  out.waiting = card();                           // signed in by its status, a new sign-in under way: it shows
  // a sign-in with a code that ended (its code expired): Try again gets a new code
  U.ai.codex = { ...U.ai.codex, logged_in: false, test: {}, login: { state: "error", device: true, page: "", code: "",
    error: "The code expired before the sign-in was finished. Click the button to get a new one." } };
  out.expired = card();
  // Claude with the wrong account (Claude Free): See plans, and signing in with another account
  S.boot.app.ai_provider = "claude";
  U.ai = { claude: { available: true, version: "2.1.318", logged_in: true, plan: "free", email: "s@x.com", job: {}, login: {}, test: {},
    plan_status: { ok: false, label: "Claude Free", note: "Claude Free doesn't include Claude Code." } } };
  out.wrongPlan = card();
  console.log(JSON.stringify(out));
  process.stdout.write("", () => process.exit(0));      // after the output is out (Linux pipes)
})();
"""

CONNECT_WAITING = HARNESS + """
answers.ai_login = { ok: true, state: "waiting", page: "https://claude.ai/oauth/authorize?code=true", started: Date.now() / 1000 };
St.modal = () => {}; St.closeModal = () => {};
(async () => {
  S.mode = "setup"; U.step = 3; U.ch = { ai: "claude" };
  U.ai = { claude: { available: true, version: "2.1.318", logged_in: false, job: {}, test: {}, plan_status: {},
    login: { state: "waiting", page: "https://claude.ai/oauth/authorize?code=true", started: Date.now() / 1000 } } };
  await A["su-connect-now"]({ dataset: { id: "claude" } }); await tick();
  out.whileWaiting = calls.map((c) => c[0]);
  U.ai.claude.login = {};
  await A["su-connect-now"]({ dataset: { id: "claude" } }); await tick();
  out.notWaiting = calls.map((c) => c[0]);
  console.log(JSON.stringify(out));
  process.stdout.write("", () => process.exit(0));      // after the output is out (Linux pipes)
})();
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_install_progress_is_written_in_place_and_cancel_says_it_is_stopping_at_once(tmp_path):
    """Every progress tick, and every 4-second look, drew the whole page again while it downloaded: the setup jumped
    to the top, an open part closed and a click on Cancel could be lost. Cancel then seemed to do nothing for up to a
    minute when the connection had stalled."""
    out = _run(tmp_path, QUIET)
    assert out["hiddenTicks"] == 0
    assert out["lookDraws"] == 0 and out["lookLine"] == "Downloading Claude Code 2.1.318… 40 of 327 MB"
    assert out["changedDraws"] == 1
    stopping = _step(out["stopping"], "claude:tool")
    assert "Stopping…" in stopping and "ai-cli-cancel" not in stopping and "(spin)" in stopping
    assert out["stoppingDraws"] == 0 and "ai_cli_cancel:claude:install" in out["calls"]


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_a_sign_in_the_service_refuses_offers_to_sign_in_again(tmp_path):
    out = _run(tmp_path, REFUSED, which="codex")
    refused = _step(out["refused"], "codex:signin")
    assert "OpenAI no longer accepts that sign-in" in refused and "Sign in again" in refused
    assert 'data-act="ai-cli-login" data-id="codex"' in refused and "(spin)" not in refused
    assert out["again"][0] == "ai_login:codex:false:false"
    waiting = _step(out["waiting"], "codex:signin")
    assert "(spin)" in waiting and 'data-what="login"' in waiting
    expired = _step(out["expired"], "codex:signin")
    assert 'data-act="ai-cli-login" data-id="codex" data-device="1">Try again' in expired
    assert "Sign in in the browser instead" in expired
    plan = _step(out["wrongPlan"], "claude:plan")
    assert "See plans" in plan and "Sign in with another account" in plan and "log out on claude.ai" in plan


LOCAL_SETTINGS = HARNESS + """
(async () => {
  S.boot.app.ai_provider = "local"; S.boot.app.local_model = "qwen3-4b";
  U.ai = { local: { models: [{ id: "qwen3-4b", label: "Better", ready: true }] } };
  out.ready = card();
  U.ai.local.models[0].ready = false;              // not downloaded, or marked damaged: offered again
  out.missing = card();
  console.log(JSON.stringify(out));
  process.stdout.write("", () => process.exit(0));      // after the output is out (Linux pipes)
})();
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_settings_has_the_way_the_offline_models_messages_name(tmp_path):
    """"Choose the Basic offline model in Settings → AI": Settings → AI had no way to it once a model was there."""
    out = _run(tmp_path, LOCAL_SETTINGS, which="local")
    assert "Another offline model" in out["ready"] and 'data-act="setup-ai"' in out["ready"]
    assert "Download the offline model" in out["missing"] and 'data-act="setup-ai"' in out["missing"]


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_connect_it_now_leaves_a_sign_in_under_way_alone(tmp_path):
    out = _run(tmp_path, CONNECT_WAITING)
    assert "ai_login" not in out["whileWaiting"]
    assert "ai_login" in out["notWaiting"]


def _run(tmp_path, script, which="claude"):
    path = tmp_path / "page.js"
    path.write_text(script % {"i18n": json.dumps(str(WEB / "i18n.js")), "setup": json.dumps(str(WEB / "setup.js")),
                              "which": json.dumps(which)}, encoding="utf-8")
    return json.loads(subprocess.run(["node", str(path)], capture_output=True, text=True, encoding="utf-8",
                                     check=True, timeout=60).stdout)


def _step(html, step):
    """The HTML of one step of the list (data-cn="claude:tool" …)."""
    start = html.index(f'data-cn="{step}"')
    end = html.find('<div class="cn-step', start)
    return html[start:end if end > 0 else len(html)]


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_installing_shows_its_progress_and_can_be_cancelled(tmp_path):
    out = _run(tmp_path, INSTALL)
    tool = _step(out["before"], "claude:tool")
    assert 'data-act="ai-install"' in tool and "btn sm primary" in tool          # the step's own button stands out
    running = _step(out["running"], "claude:tool")
    assert "120 of 327 MB · 2.4 MB/s · about 2 min left" in running and "(spin)" in running
    assert 'data-act="ai-cli-cancel" data-id="claude" data-what="install"' in running
    assert 'data-act="ai-install"' not in running                               # no second install while one runs
    # a progress tick changes only the line: the page is not drawn again (a click on Cancel isn't lost)
    assert out["tick"] == "Downloading Claude Code 2.1.318… 121 of 327 MB" and out["drawsForTick"] == 0
    assert "ai_cli_cancel:claude:install" in out["calls"]
    stopped = _step(out["cancelled"], "claude:tool")
    assert "Stopped. Click the button to go on" in stopped and 'data-act="ai-install"' in stopped and "(spin)" not in stopped
    failed = _step(out["failed"], "claude:tool")
    assert "&lt;b&gt;no&lt;/b&gt;" in failed and "<b>no</b>" not in failed     # the installer's words, not markup
    assert "Try again" in failed and 'data-act="ai-install"' in failed
    assert out["toasts"] == [["Could not download Claude Code: <b>no</b> connection.", "error"]]


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_the_offline_models_are_offered_even_before_the_ai_status_comes(tmp_path):
    out = _run(tmp_path, LOCAL)
    assert 'data-act="su-llm" data-id="qwen3-1.7b"' in out["html"] and "Short notes and simple questions" in out["html"]


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_a_cli_too_old_or_broken_is_said_with_the_button_that_fixes_it(tmp_path):
    out = _run(tmp_path, OLD)
    old = _step(out["old"], "claude:tool")
    assert "The Claude Code on this PC (1.0.30) is too old for IEUStudia" in old and "Update Claude Code" in old
    assert 'data-act="ai-install" data-id="claude"' in old
    assert "The Claude Code on this PC is too old for IEUStudia: update it below." in out["old"]
    broken = _step(out["broken"], "codex:tool")
    assert "The Codex CLI is on this PC but doesn't start (the file is damaged or not made for this PC)" in broken
    assert 'data-act="ai-install" data-id="codex"' in broken
    # the way round it by hand: Command Prompt when PowerShell is blocked, Codex's sign-in with a code
    assert "curl -fsSL https://claude.ai/install.cmd -o install.cmd &amp;&amp; install.cmd" in out["old"]
    assert "codex login --device-auth" in out["broken"]


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_claudes_sign_in_always_has_a_way_out_and_says_why_it_ended(tmp_path):
    out = _run(tmp_path, SIGNIN_CLAUDE)
    before = _step(out["before"], "claude:signin")
    assert 'data-act="ai-cli-login" data-id="claude"' in before and "btn sm primary" in before
    waiting = _step(out["waiting"], "claude:signin")
    assert "(spin)" in waiting and "Finish on Anthropic's page in your browser" in waiting
    assert 'data-act="ai-cli-open" data-id="claude"' in waiting
    assert 'data-act="ai-copy" data-text="https://claude.ai/oauth/authorize?code=true"' in waiting
    assert 'data-act="ai-cli-cancel" data-id="claude" data-what="login"' in waiting
    assert "data-cn-code" not in waiting and 'data-act="ai-cli-login"' not in waiting
    # opened from IEUStudia: that page shows a code to paste back, so the box for it is there
    opened = _step(out["opened"], "claude:signin")
    assert 'data-cn-code="claude"' in opened and 'data-act="ai-cli-code" data-id="claude"' in opened
    assert "ai_cli_code:claude:abc#def" in out["early"] and "ai_cli_open:claude" in out["early"]
    slow = _step(out["slow"], "claude:signin")
    assert "No sign-in page in your browser?" in slow and 'data-cn-code="claude"' in slow
    # it ended without signing in: why, the button again, nothing spinning
    ended = _step(out["ended"], "claude:signin")
    assert "ended before you were signed in (code 1)" in ended and "Try again" in ended
    assert 'data-act="ai-cli-login"' in ended and "(spin)" not in ended and "ai-cli-cancel" not in ended
    assert not out["busyAfterEnd"]
    assert out["connectNow"][0] == "ai_login:claude:false:false"         # "Connect it now" starts the same sign-in
    cancelled = _step(out["cancelled"], "claude:signin")
    assert "Sign in with Claude" in cancelled and "(spin)" not in cancelled
    assert ["Code sent. Claude Code finishes the sign-in now.", "info"] in out["toasts"]


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_codex_offers_a_sign_in_with_a_code_when_the_browser_cant_come_back(tmp_path):
    out = _run(tmp_path, SIGNIN_CODEX, which="codex")
    port = _step(out["portError"], "codex:signin")
    assert "Another program is using the port" in port and "Try again" in port
    assert 'data-act="ai-cli-login" data-id="codex" data-device="1"' in port
    assert out["deviceCalls"][0] == "ai_login:codex:false:true"
    assert "Getting a sign-in code from OpenAI" in _step(out["gettingCode"], "codex:signin")
    device = _step(out["device"], "codex:signin")
    assert "Your code: <b>ABCD-12345</b>" in device and 'data-text="ABCD-12345"' in device
    assert "Open the page, sign in with your ChatGPT account" in device
    assert 'data-text="https://auth.openai.com/codex/device"' in device
