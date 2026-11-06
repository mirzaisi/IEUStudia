"""'Continue with ChatGPT' on the page (setup.js in node with a stand-in for app.js): while it waits there is always
a way out (the page again, its link to copy, Cancel), the page says when no browser opened or the page hasn't shown
up, and the browser opening the page is noticed. The case behind it: on a friend's PC nothing opened, and closing
the sign-in tab left the list waiting with no button for 10 minutes."""

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
  process.exit(0);
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
    assert "hasn't shown the sign-in page yet" in out["slow"] and "behind this window" in out["slow"]
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
  process.exit(0);
})();
"""


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
