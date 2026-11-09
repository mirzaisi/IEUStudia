"""'Continue with ChatGPT' on the page (setup.js in node with a stand-in for app.js): while it waits there is always
a way out (the page again, its link to copy, Cancel), the page says when no browser opened or the page hasn't shown
up, and the browser opening the page is noticed. The case behind it: on a friend's PC nothing opened, and closing
the sign-in tab left the list waiting with no button for 10 minutes."""

import json
import re
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
require(%(i18n)s);
const calls = [], toasts = [];
let draws = 0;
const answers = {
  ai_login: { ok: true, state: "waiting", link: "http://127.0.0.1:52817/sign-in", page: "https://auth.openai.com/page", launched: false },
  ai_login_again: { ok: true, state: "waiting", link: "http://127.0.0.1:52817/sign-in", page: "https://auth.openai.com/page", launched: true },
  ai_login_cancel: { ok: true },
  ai_status: { ok: true, provider: "chatgpt", chosen: "chatgpt", chatgpt: { signed_in: false, plan_allowed: false, sign_in: {}, test: {} } },
};
const S = { mode: "app", view: { name: "settings" }, boot: { app: { ai_provider: "chatgpt" } } };
window.Studia = { S, A: {}, I: (n) => `[${n}]`, SPIN: () => "(spin)", esc: (s) => String(s), onEvents: {}, mocks: {},
  toast: (m, kind) => toasts.push([m, kind]), renderView: () => { draws++; },
  call: (name, ...args) => { calls.push([name, ...args]); return Promise.resolve(answers[name] || { ok: true }); } };
require(%(setup)s);
const St = window.Studia, A = St.A, U = St.setup, tick = () => new Promise((r) => setTimeout(r, 5));
const card = () => St.settingsAI();
const out = {};
(async () => {
  U.ai = { chatgpt: { signed_in: false, plan_allowed: false, sign_in: {}, test: {} } };
  out.before = card();
  await A["ai-login"]({ dataset: { id: "chatgpt" } }); await tick();
  out.noBrowser = card();
  await A["ai-login-again"](); await tick();
  out.again = card();
  out.calls = calls.splice(0).map((c) => c[0]);
  U.busy["login:chatgpt"] = Date.now() - 20000;            // 20 s later, still no page
  out.slow = card();
  St.onEvents.chatgpt({ type: "chatgpt", state: "waiting", opened: true, error: "", email: "" });
  out.opened = card();
  out.busyAfterOpened = !!U.busy["login:chatgpt"];
  await A["ai-login-cancel"](); await tick();
  out.cancelled = card();
  out.cancelCalls = calls.splice(0).map((c) => c[0]);
  out.busyAfterCancel = !!U.busy["login:chatgpt"];
  // a sign-in under way after the page was drawn again (F5): the way out is there too
  U.ai = { chatgpt: { signed_in: false, plan_allowed: false, sign_in: { state: "waiting", error: "", link: "http://127.0.0.1:1/sign-in", page: "https://auth.openai.com/page2", opened: false, launched: true }, test: {} } };
  U.busy = {};
  out.reloaded = card();
  out.toasts = toasts;
  console.log(JSON.stringify(out));
  process.stdout.write("", () => process.exit(0));      // after the output is out (Linux pipes)
})();
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_a_waiting_sign_in_always_has_a_way_out(tmp_path):
    script = tmp_path / "signin.js"
    script.write_text(SCRIPT % {"i18n": json.dumps(str(WEB / "i18n.js")), "setup": json.dumps(str(WEB / "setup.js"))},
                      encoding="utf-8")
    out = json.loads(subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8",
                                    check=True, timeout=60).stdout)
    assert 'data-act="ai-login"' in out["before"] and "ai-login-cancel" not in out["before"]
    # nothing opened on this PC: said, with the link to copy and a way out
    assert "No browser could be opened on this PC" in out["noBrowser"]
    for act in ('data-act="ai-login-again"', 'data-act="ai-login-cancel"',
                'data-act="ai-copy" data-text="https://auth.openai.com/page"'):
        assert act in out["noBrowser"] and act in out["again"]
    assert out["calls"] == ["ai_login", "ai_login_again"]
    # opened again, but the page hasn't shown up after a while: where it may be
    assert "hasn't shown the sign-in page" in out["slow"] and "taskbar" in out["slow"]
    # the browser asked for the page: the usual text, still with the way out
    assert "Finish on the sign-in page in your browser" in out["opened"] and 'data-act="ai-login-cancel"' in out["opened"]
    assert out["busyAfterOpened"]
    # Cancel: back to the button, nothing waiting
    assert out["cancelCalls"][0] == "ai_login_cancel" and not out["busyAfterCancel"]
    assert 'data-act="ai-login"' in out["cancelled"] and "ai-login-cancel" not in out["cancelled"]
    assert "(spin)" not in out["cancelled"].split("Continue with ChatGPT")[1][:400]
    # drawn again while waiting (no click on this page): the way out is there
    assert 'data-act="ai-login-cancel"' in out["reloaded"] and "https://auth.openai.com/page2" in out["reloaded"]
    assert out["toasts"] == []


CONNECT = """
globalThis.window = globalThis;
globalThis.location = { hash: "" };
globalThis.document = { documentElement: {}, querySelector: () => null, querySelectorAll: () => [], addEventListener() {},
  getElementById: () => null };
require(%(i18n)s);
const calls = [];
let shown = "";
const S = { mode: "setup", view: { name: "home" }, boot: { app: {} } };
window.Studia = { S, A: {}, I: (n) => `[${n}]`, SPIN: () => "(spin)", esc: (s) => String(s), onEvents: {}, mocks: {},
  toast() {}, renderView() {}, render() {}, modal: (h) => { shown = h; }, closeModal: () => { shown = ""; },
  call: (name, ...args) => { calls.push([name, ...args]); return Promise.resolve(name === "ai_login" ? { ok: true, state: "waiting", link: "l", page: "p", launched: true } : { ok: true }); } };
require(%(setup)s);
const St = window.Studia, A = St.A, U = St.setup, tick = () => new Promise((r) => setTimeout(r, 80));
St.render = () => {};
const out = {};
(async () => {
  U.step = 3; U.ch.ai = "chatgpt";                                  // the AI step, ChatGPT picked, not connected
  U.ai = { chatgpt: { signed_in: false, plan_allowed: false, sign_in: {}, test: {} } };
  await A["su-next"]({ dataset: {} });
  out.question = shown;
  await A["su-connect-now"]({ dataset: { id: "chatgpt" } }); await tick();
  out.closed = shown === "";
  out.calls = calls.map((c) => c[0] + (c[1] ? ":" + c[1] : ""));
  console.log(JSON.stringify(out));
  process.stdout.write("", () => process.exit(0));      // after the output is out (Linux pipes)
})();
"""


CASES = """
globalThis.window = globalThis;
globalThis.location = { hash: "" };
const els = { "#gpt-paste": { value: "  http://127.0.0.1:1/auth/callback?state=x  ", focus() {} }, "#gpt-paste-err": { textContent: "" } };
globalThis.document = { documentElement: {}, querySelector: (s) => els[s] || null, querySelectorAll: () => [], addEventListener() {},
  getElementById: () => null };
require(%(i18n)s);
const calls = [], toasts = [];
let shown = "";
const answers = {
  ai_login_edge: { ok: true, state: "waiting", link: "http://127.0.0.1:1/sign-in", page: "https://auth.openai.com/p", launched: true, browser: "Microsoft Edge", via: "edge", way: "edge", again: 1 },
  ai_login_paste: { ok: true, state: "refused", error: "Not the address." },
  ai_login_details: { ok: true, text: "report" },
  ai_switch_account: { ok: true, state: "waiting", link: "http://127.0.0.1:3/sign-in", page: "https://auth.openai.com/q", launched: true, browser: "Brave", via: "default", why: "" },
  ai_status: { ok: true, provider: "chatgpt", chosen: "chatgpt", chatgpt: { signed_in: false, plan_allowed: false, sign_in: {}, test: {} } },
};
const S = { mode: "app", view: { name: "settings" }, boot: { app: { ai_provider: "chatgpt" } } };
window.Studia = { S, A: {}, I: (n) => `[${n}]`, SPIN: () => "(spin)", esc: (s) => String(s).replace(/</g, "&lt;"), onEvents: {}, mocks: {},
  toast: (m, kind) => toasts.push([m, kind]), renderView() {}, render() {}, modal: (h) => { shown = h; }, closeModal: () => { shown = ""; },
  call: (name, ...args) => { calls.push([name, ...args]); return Promise.resolve(answers[name] || { ok: true }); } };
require(%(setup)s);
const St = window.Studia, A = St.A, U = St.setup, tick = () => new Promise((r) => setTimeout(r, 5));
const card = (a) => { U.ai = { chatgpt: { signed_in: false, plan_allowed: false, registered: false, sign_in: {}, test: {}, ...a } }; return St.settingsAI(); };
const waiting = (si, more) => card({ sign_in: { state: "waiting", link: "http://127.0.0.1:1/sign-in", page: "https://auth.openai.com/p", launched: true, ...si }, ...more });
const out = {};
(async () => {
  U.busy["login:chatgpt"] = Date.now();
  out.opening = waiting({ step: "opening" });
  out.returned = waiting({ step: "returned", opened: true });
  out.exchanging = waiting({ step: "exchanging", opened: true });
  out.verifying = waiting({ step: "verifying", opened: true });
  out.opened = waiting({ step: "opened", opened: true, browser: "Brave", via: "default" }, { registered: true });
  out.behind = waiting({ step: "opened", opened: true, browser: "Brave", via: "default", behind: true });
  out.behindNoName = waiting({ step: "opened", opened: true, behind: true });
  out.edge = waiting({ opened: true, browser: "Microsoft Edge", via: "edge", why: "no_default" });
  out.failed = waiting({ opened: true, browser: "Microsoft Edge", via: "edge", why: "default_failed" });
  out.https = waiting({ note: "https" });
  out.reach = waiting({ reach: { ok: false, why: "IEUStudia can't find OpenAI's sign-in <here>" } });
  out.nobrowser = waiting({ launched: false });
  out.listen = waiting({ note: "listen" });
  // no browser at all: neither the default nor Edge started (it said the page "opened in Microsoft Edge" as well)
  out.nobrowserWhy = waiting({ launched: false, why: "default_failed", via: "", browser: "" });
  out.early = waiting({ edge: true, browser: "Brave", via: "default" });      // Edge only after 15 s
  // IEUStudia run as administrator: the page still coming, already open, no browser at all
  out.adminEarly = waiting({ elevated: true, browser: "Brave", via: "default" });
  out.adminOpened = waiting({ elevated: true, step: "opened", opened: true, browser: "Brave", via: "default" });
  out.adminNoBrowser = waiting({ elevated: true, launched: false });
  window.LS_LINUX = true;
  out.linux = waiting({ step: "opened", opened: true });
  window.LS_LINUX = false;
  U.busy["login:chatgpt"] = Date.now() - 20000;
  out.slow = waiting({});                         // 15 s without the page
  out.slowEdge = waiting({ edge: true, browser: "Brave", via: "default" });
  out.slowViaEdge = waiting({ edge: true, browser: "Microsoft Edge", via: "edge" });       // Edge was what showed nothing
  out.slowEdgeDefault = waiting({ edge: true, browser: "Microsoft Edge", via: "default" }); // ...as the default browser
  // no working default browser (or it didn't start), so Edge was sent the page, and it hasn't shown it either
  out.slowNoDefault = waiting({ edge: true, browser: "Microsoft Edge", via: "edge", why: "no_default" });
  out.slowFailed = waiting({ edge: true, browser: "Microsoft Edge", via: "edge", why: "default_failed" });
  out.adminSlow = waiting({ elevated: true, edge: true, browser: "Brave", via: "default" });   // ...run as administrator
  // Open the page again: another way each time, said
  out.againEdge = waiting({ edge: true, again: 1, way: "edge", via: "edge", browser: "Microsoft Edge" });
  out.againShell = waiting({ edge: true, again: 2, way: "shell", via: "shell", browser: "Brave" });
  out.againDefault = waiting({ edge: true, again: 3, way: "default", via: "default", browser: "Brave", why: "default_failed" });
  U.busy["login:chatgpt"] = Date.now() - 100000;
  out.lost = waiting({ step: "opened", opened: true });
  out.behindLost = waiting({ step: "opened", opened: true, browser: "Brave", behind: true });
  delete U.busy["login:chatgpt"];
  out.workspace = card({ sign_in: { state: "error", error: "Admin says no", code: "workspace" } });
  out.timeout = card({ sign_in: { state: "error", error: "Not in time", code: "timeout" } });
  out.notEligible = card({ signed_in: true, plan_allowed: true, registered: true, test: { ok: false, code: "plan_not_eligible", error: "Not this plan" } });
  out.ended = card({ ended: true, registered: true });
  out.unreadable = card({ unreadable: true });
  out.clock = card({ signed_in: true, plan_allowed: true, clock: 360, clock_text: "Your PC's clock is 6 hours ahead." });
  S.mode = "setup"; U.step = 3; U.ch.ai = "chatgpt";
  out.setupSigned = card({ signed_in: true, plan_allowed: true, registered: true, email: "a@b.c", test: { ok: true, seconds: 2 } });
  S.mode = "app";
  // the actions
  waiting({ edge: true, browser: "Brave", via: "default" });
  await A["ai-login-edge"](); await tick();
  out.afterEdge = { si: U.ai.chatgpt.sign_in, html: St.settingsAI() };
  answers.ai_login_edge = { ok: true, state: "waiting", link: "http://127.0.0.1:1/sign-in", page: "https://auth.openai.com/p", launched: false, browser: "Microsoft Edge", via: "edge", way: "edge", again: 0 };
  waiting({ edge: true, browser: "Brave", via: "default" });
  await A["ai-login-edge"](); await tick();                    // Edge didn't start: said, the rest as it was
  out.edgeFailed = { si: U.ai.chatgpt.sign_in, toasts: toasts.splice(0) };
  waiting({ note: "listen" });
  A["ai-login-paste"](); out.pasteListen = shown;            // nothing listens for the browser on this PC
  card({});
  A["ai-login-paste"](); out.pasteDialog = shown;
  const go = { disabled: false, innerHTML: "", textContent: "" };
  await A["ai-login-paste-go"](go); await tick();
  out.pasteRefused = { err: els["#gpt-paste-err"].textContent, open: shown !== "", disabled: go.disabled };
  // the browser came back while the dialog was still open: it closes, calmly
  St.onEvents.chatgpt({ type: "chatgpt", state: "done", error: "", email: "a@b.c", code: "" });
  out.pasteAfterDone = { open: shown !== "", toasts: toasts.splice(0) };
  await A["ai-login-details"](); await tick();
  A["ai-switch"](); out.switchDialog = shown;
  card({ signed_in: true, plan_allowed: true, registered: true, email: "a@b.c" });
  await A["ai-switch-go"](); await tick();
  out.afterSwitch = { si: U.ai.chatgpt.sign_in, signed: U.ai.chatgpt.signed_in, html: St.settingsAI() };
  St.onEvents.chatgpt({ type: "chatgpt", state: "waiting", step: "opened", opened: true });
  out.afterEvent = U.ai.chatgpt.sign_in;
  St.onEvents.chatgpt({ type: "chatgpt", state: "error", error: "Admin says no", email: "", code: "workspace" });
  out.afterError = U.ai.chatgpt.sign_in;
  out.calls = calls.map((c) => c[0]);
  out.toasts = toasts;
  console.log(JSON.stringify(out));
  process.stdout.write("", () => process.exit(0));      // after the output is out (Linux pipes)
})();
"""


WAITING = ("opening", "returned", "exchanging", "verifying", "opened", "behind", "behindNoName", "edge", "failed",
           "https", "reach", "nobrowser", "listen", "nobrowserWhy", "early", "adminEarly", "adminOpened",
           "adminNoBrowser", "linux", "slow", "slowEdge", "slowViaEdge", "slowEdgeDefault", "slowNoDefault", "slowFailed",
           "adminSlow", "againEdge", "againShell",
           "againDefault", "lost", "behindLost")
ADMIN = "IEUStudia is running as administrator, and a browser may not open from it."
# the buttons the texts name, and what the card needs for each
WAYS = {"Copy link": 'data-act="ai-copy"', "Open the page again": 'data-act="ai-login-again"',
        "Paste the address": 'data-act="ai-login-paste"', "Open Default apps": 'data-act="open-default-apps"',
        "Use another account or workspace": 'data-act="ai-switch"', "Copy details": 'data-act="ai-login-details"',
        "Open in Microsoft Edge": 'data-act="ai-login-edge"'}


def missing_ways(html):
    """The buttons a text on this card points at that the card doesn't have."""
    text = re.sub(r"<[^>]+>", " ", html)
    return [name for name, act in WAYS.items() if name in text and act not in html]


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_the_sign_in_says_each_step_what_it_noticed_and_offers_the_less_common_ways(tmp_path):
    script = tmp_path / "cases.js"
    script.write_text(CASES % {"i18n": json.dumps(str(WEB / "i18n.js")), "setup": json.dumps(str(WEB / "setup.js"))},
                      encoding="utf-8")
    out = json.loads(subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8",
                                    check=True, timeout=60).stdout)
    # each step in words
    assert "Opening the sign-in page in your browser" in out["opening"]
    assert "OpenAI sent you back to IEUStudia" in out["returned"]
    assert "Getting your sign-in from OpenAI" in out["exchanging"] and "Checking your sign-in" in out["verifying"]
    assert "Finish on the sign-in page in your browser" in out["opened"] and "Opened with Brave." in out["opened"]
    assert "Can't find it?" in out["lost"] and "another desktop" in out["lost"]
    assert "Microsoft Edge" not in out["nobrowserWhy"] and "No browser could be opened" in out["nobrowserWhy"]
    assert "default browser didn't start" in out["nobrowserWhy"] and 'data-act="open-default-apps"' in out["nobrowserWhy"]
    # what an error page after Allow means, where the student looks
    assert "error page" in out["lost"].split('data-act="gpt-trouble"')[0] and "Paste the address" in out["lost"]
    # the page opened behind IEUStudia's window (it was still in front a moment later): said, with where to click
    assert ("The sign-in page opened in your browser (Brave), behind this window: click your browser on the taskbar "
            "at the bottom of the screen.") in out["behind"] and "Finish on the sign-in page" not in out["behind"]
    assert "Opened with Brave." not in out["behind"]                     # (said once, in the text that names it)
    assert "opened in your browser, behind this window" in out["behindNoName"]
    assert "behind this window: click your browser" in out["behindLost"] and "error page" in out["behindLost"]
    assert "behind this window: click" not in out["opened"]
    # the less common ways, out of the way under "Trouble?" (another account only when one is registered)
    for act in ("ai-login-paste", "ai-login-details", "open-default-apps"):
        assert f'data-act="{act}"' in out["opened"].split('data-act="gpt-trouble"')[1]
    assert 'data-act="ai-switch"' in out["opened"] and 'data-act="ai-switch"' not in out["opening"]
    assert "<details" in out["opened"] and "<details class=\"cn-trouble\" open" not in out["opened"]
    assert "btn primary" not in out["opened"].split('data-act="gpt-trouble"')[0]       # the calm usual way
    # what IEUStudia noticed, and the cure
    assert "no working default browser" in out["edge"] and 'data-act="open-default-apps"' in out["edge"]
    assert "default browser didn't start" in out["failed"] and 'data-act="open-default-apps"' in out["failed"]
    assert "so the page opened in Microsoft Edge." in out["edge"] and "so the page opened in Microsoft Edge." in out["failed"]
    # Edge only sent the page, which hasn't shown up: not said to have opened it (beside "hasn't shown the sign-in page")
    for case, why in (("slowNoDefault", "Windows has no working default browser"), ("slowFailed", "Your default browser didn't start")):
        assert f"{why}, so IEUStudia sent the page to Microsoft Edge." in out[case], case
        assert "opened in Microsoft Edge" not in out[case] and "hasn't shown the sign-in page" in out[case], case
    # the way to take now in front: the address when the way back is blocked, the link when no browser opens
    for case in ("https", "listen"):
        assert 'class="btn primary sm" data-act="ai-login-paste"' in out[case], case
    assert 'class="btn primary sm" data-act="ai-copy"' in out["nobrowser"]
    assert "Copy link" in out["nobrowser"] and "choose a default browser (see Trouble?)" in out["nobrowser"]
    assert "https://" in out["https"] and "Paste the address" in out["listen"]
    for case in ("https", "listen"):                         # the way back blocked: the address, not under Trouble?
        assert 'data-act="ai-login-paste"' in out[case].split('data-act="gpt-trouble"')[0], case
    assert 'data-act="ai-login-paste"' not in out["opened"].split('data-act="gpt-trouble"')[0]
    assert "can't reach OpenAI" in out["reach"] and "&lt;here&gt;" in out["reach"]      # OpenAI's words, escaped
    # nothing points at a way that isn't there: no sign-in window of IEUStudia's own, each named button on the card
    for case in WAITING:
        assert "inside IEUStudia" not in out[case] and "ai-login-inside" not in out[case], case
        assert not missing_ways(out[case]), (case, missing_ways(out[case]))
        # nor a sign-in with a code: OpenAI turns IEUStudia's client down for it (Codex's own one is the CLI's)
        assert "data-device" not in out[case] and "with a code" not in out[case], case
    # 15 s without the page: Open in Microsoft Edge in front, whatever the default browser is (not sooner, not
    # without Edge on this PC, not right after Edge itself showed nothing: asked with Open in Microsoft Edge, or as
    # the default browser, where "asked your browser (Microsoft Edge)" then offered the same one)
    front = lambda case: out[case].split('data-act="gpt-trouble"')[0]          # noqa: E731
    assert 'data-act="ai-login-edge"' not in front("early") and "Open in Microsoft Edge" not in front("early")
    assert 'class="btn primary sm" data-act="ai-login-edge"' in out["slowEdge"]
    assert "Click Open in Microsoft Edge" in out["slowEdge"].split('class="cn-wait"')[0]
    assert "IEUStudia asked your browser (Brave) to open it." in out["slowEdge"]
    assert "IEUStudia asked your browser (Microsoft Edge) to open it." in out["slowEdgeDefault"]
    for case in ("slow", "slowViaEdge", "slowEdgeDefault"):
        assert 'data-act="ai-login-edge"' not in front(case) and "Open in Microsoft Edge" not in front(case), case
        assert "hasn't shown the sign-in page" in out[case] and "Copy link and paste it" in out[case], case
    assert 'data-act="ai-login-edge"' not in out["slow"]                 # no Edge on this PC: not under Trouble? either
    assert "Family Safety" in out["slowEdge"].split('data-act="gpt-trouble"')[1]
    # run as administrator: said only then, and once: in front when the page hasn't shown up (15 s, or no browser at
    # all), else under Trouble?
    for case in ("adminSlow", "adminNoBrowser"):
        assert ADMIN in front(case) and out[case].count(ADMIN) == 1, case
    assert "without Run as administrator" in front("adminSlow") and 'data-act="ai-login-edge"' in front("adminSlow")
    for case in ("adminEarly", "adminOpened"):
        assert ADMIN not in front(case) and out[case].count(ADMIN) == 1, case
    for case in WAITING:
        assert case.startswith("admin") or "administrator" not in out[case], case
    # under Trouble?: what OpenAI's own page can show (it never reaches IEUStudia) and what to do, and the browser's
    # own troubles; none of it in front, where the usual way stays calm
    trouble = out["opened"].split('data-act="gpt-trouble"')[1]
    for said in ("This app is unavailable", "on OpenAI's side, not your PC", "doesn't allow this app",
                 "phone number", "whether you're human", "GoodbyeDPI/Zapret", "private window", "hotspot",
                 "Sorry, you have been blocked", "country or region", "antivirus", "127.0.0.1 instead of OpenAI's page",
                 "not on your phone", "Task Manager", "choose a profile", "Windows key + Tab"):
        assert said in trouble and said not in front("opened"), said
    assert 'data-url="https://status.openai.com"' in trouble
    assert "click Use another account or workspace and choose your personal account" in trouble   # (registered)
    assert "click Open the page again and choose your personal account" in out["opening"]        # (not yet)
    # "This app is unavailable" for a while: with a saved registration, a fresh one is the way out
    assert "choose your account again: IEUStudia then connects anew" in trouble
    assert "This app is unavailable" in out["opening"] and "connects anew" not in out["opening"]
    linux = out["linux"].split('data-act="gpt-trouble"')[1]
    assert "Task Manager" not in linux and "Windows key" not in linux and "open-default-apps" not in linux
    assert "Choose a default browser in your system settings." in linux and "This app is unavailable" in linux
    # Open the page again: which way it took
    assert "IEUStudia asked Microsoft Edge to open it." in out["againEdge"] and 'data-act="ai-login-edge"' not in front("againEdge")
    assert "asked Windows to open it again" in out["againShell"] and 'data-act="ai-login-edge"' in front("againShell")
    assert "IEUStudia asked your default browser (Brave) to open it again." in out["againDefault"]
    assert "didn't start" not in out["againDefault"]        # why the first try didn't use it: not after another way
    assert out["afterEdge"]["si"]["way"] == "edge" and "IEUStudia asked Microsoft Edge to open it." in out["afterEdge"]["html"]
    assert out["edgeFailed"]["si"].get("way") is None and out["edgeFailed"]["si"]["launched"]
    assert [m for m, k in out["edgeFailed"]["toasts"] if k == "error"] == [
        "Microsoft Edge didn't start on this PC. Click Copy link and paste it into any browser's address bar."]
    # after a failure: another account when that is the cure, always a report
    assert 'data-act="ai-switch"' in out["workspace"] and 'data-act="ai-login-details"' in out["workspace"]
    assert 'data-act="ai-switch"' not in out["timeout"] and 'data-act="ai-login-details"' in out["timeout"]
    assert '<button class="btn sm" data-act="ai-switch">[users]' in out["notEligible"]      # in the plan step's error
    assert "has ended" in out["ended"] and "can't be read on this PC" in out["unreadable"]
    for case in ("ended", "unreadable", "timeout"):
        assert 'class="btn primary sm" data-act="ai-login" data-id="chatgpt"' in out[case], case
    assert "6 hours ahead" in out["clock"] and "callout warning" in out["clock"]
    assert 'data-act="ai-switch"' in out["setupSigned"] and 'data-act="ai-signout"' not in out["setupSigned"]
    # the actions
    assert 'id="gpt-paste"' in out["pasteDialog"] and 'data-act="ai-login-paste-go"' in out["pasteDialog"]
    assert out["pasteRefused"] == {"err": "Not the address.", "open": True, "disabled": False}
    assert "whichever comes back first" in out["pasteDialog"] and "failed" not in out["pasteDialog"]
    # IEUStudia can't listen on this PC: nothing waits for the browser, so the dialog doesn't say it does
    assert 'id="gpt-paste"' in out["pasteListen"] and "keeps waiting for your browser" not in out["pasteListen"]
    assert out["pasteAfterDone"] == {"open": False, "toasts": [["Signed in with ChatGPT.", "success"]]}
    assert 'data-act="ai-switch-go"' in out["switchDialog"]
    assert out["afterSwitch"]["si"]["state"] == "waiting" and out["afterSwitch"]["si"]["browser"] == "Brave"
    assert not out["afterSwitch"]["signed"] and "ai-login-cancel" in out["afterSwitch"]["html"]
    assert out["afterEvent"]["opened"] and out["afterEvent"]["step"] == "opened" and out["afterEvent"]["state"] == "waiting"
    assert out["afterError"] == {"state": "error", "error": "Admin says no", "code": "workspace"}
    assert [c for c in out["calls"] if c != "ai_status"] == ["ai_login_edge", "ai_login_edge", "ai_login_paste",
                                                             "ai_login_details", "ai_switch_account"]
    assert all("report" not in m for m, _ in out["toasts"]) and ("success" in [k for _, k in out["toasts"]])


TURKISH = """
globalThis.window = globalThis;
globalThis.location = { hash: "" };
globalThis.document = { documentElement: {}, querySelector: () => null, querySelectorAll: () => [], addEventListener() {},
  getElementById: () => null };
require(%(i18n)s);
const S = { mode: "app", view: { name: "settings" }, boot: { app: { ai_provider: "chatgpt" } } };
window.Studia = { S, A: {}, I: (n) => `[${n}]`, SPIN: () => "(spin)", esc: (s) => String(s), onEvents: {}, mocks: {},
  toast() {}, renderView() {}, call: () => Promise.resolve({ ok: true }) };
require(%(setup)s);
const St = window.Studia, U = St.setup;
window.t.load({ lang: "tr", table: JSON.parse(require("fs").readFileSync(%(table)s, "utf8")) });
const waiting = (si, more) => {
  U.ai = { chatgpt: { signed_in: false, plan_allowed: false, registered: false, test: {}, ...more,
    sign_in: { state: "waiting", link: "http://127.0.0.1:1/sign-in", page: "https://auth.openai.com/p", launched: true, ...si } } };
  return St.settingsAI();
};
const out = {};
U.busy["login:chatgpt"] = Date.now();
out.opening = waiting({ step: "opening" });
out.opened = waiting({ step: "opened", opened: true, browser: "Brave", via: "default", edge: true }, { registered: true });
out.behind = waiting({ step: "opened", opened: true, browser: "Brave", via: "default", behind: true });
out.nobrowser = waiting({ launched: false, edge: true });
out.https = waiting({ note: "https", edge: true });
out.listen = waiting({ note: "listen" });
U.busy["login:chatgpt"] = Date.now() - 20000;
out.slowEdge = waiting({ edge: true, browser: "Brave", via: "default" });
out.slow = waiting({ browser: "Brave", via: "default" });
out.again = waiting({ edge: true, again: 1, way: "edge", via: "edge", browser: "Microsoft Edge" });
out.noDefault = waiting({ edge: true, browser: "Microsoft Edge", via: "edge", why: "no_default" });
U.busy["login:chatgpt"] = Date.now() - 100000;
out.lost = waiting({ step: "opened", opened: true, browser: "Brave", via: "default" });
process.stdout.write(JSON.stringify(out), () => process.exit(0));      // after the output is out (Linux pipes)
"""
# the buttons the Turkish texts name ("Sayfayı yeniden aç'a tıkla"), and what the card needs for each
WAYS_TR = {"Bağlantıyı kopyala": 'data-act="ai-copy"', "Sayfayı yeniden aç": 'data-act="ai-login-again"',
           "Tarayıcıdaki adresi yapıştır": 'data-act="ai-login-paste"',
           "Varsayılan uygulamaları aç": 'data-act="open-default-apps"',
           "Başka hesap ya da çalışma alanı kullan": 'data-act="ai-switch"', "Ayrıntıları kopyala": 'data-act="ai-login-details"',
           "Microsoft Edge'de aç": 'data-act="ai-login-edge"'}


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_the_turkish_sign_in_card_names_only_buttons_it_has_and_reads_as_said(tmp_path):
    """The card in Turkish, as most students see it: each button a text names is on it, nothing is left in English,
    and the errors OpenAI shows on its own page read "if it says this: do that", each part of the "if" as one."""
    table = tmp_path / "tr.json"
    table.write_text(json.dumps(i18n.table("tr"), ensure_ascii=False), encoding="utf-8")
    script = tmp_path / "turkish.js"
    script.write_text(TURKISH % {"i18n": json.dumps(str(WEB / "i18n.js")), "setup": json.dumps(str(WEB / "setup.js")),
                                 "table": json.dumps(str(table))}, encoding="utf-8")
    out = json.loads(subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8",
                                    check=True, timeout=60).stdout)
    for case, html in out.items():
        text = re.sub(r"<[^>]+>", " ", html)
        assert [n for n, act in WAYS_TR.items() if n in text and act not in html] == [], case
        assert not [n for n in WAYS if n in text], (case, [n for n in WAYS if n in text])     # (the English names)
        assert "inside IEUStudia" not in text and "IEUStudia içinde" not in text, case
    trouble = out["opened"].split('data-act="gpt-trouble"')[1]
    assert "doğrulamanı istiyorsa, boş kalıyorsa ya da hep yükleniyorsa: VPN" in trouble
    assert "kişisel hesabını seç; ya da çalışma alanının yöneticisine sor" in trouble
    assert "kişisel hesabını seç; ya da çalışma alanının yöneticisine sor" in out["opening"]       # (not registered)


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_connect_it_now_in_the_setup_really_starts_connecting(tmp_path):
    """A friend pressed "Şimdi bağla" (Connect it now) in the setup's "ChatGPT isn't connected yet" question: it only
    closed the question, so nothing happened at all, not even a spinner."""
    script = tmp_path / "connect.js"
    script.write_text(CONNECT % {"i18n": json.dumps(str(WEB / "i18n.js")), "setup": json.dumps(str(WEB / "setup.js"))},
                      encoding="utf-8")
    out = json.loads(subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8",
                                    check=True, timeout=60).stdout)
    assert 'data-act="su-connect-now" data-id="chatgpt"' in out["question"]
    assert 'data-act="modal-close">' not in out["question"].split("Connect it now")[0][-80:]
    assert out["closed"] and "ai_login:chatgpt" in out["calls"]
