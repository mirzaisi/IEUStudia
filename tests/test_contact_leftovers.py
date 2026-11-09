"""Small things the owner found (fx-contact): a message shown before a language switch comes back in the new language
(why a Claude Code or Codex sign-in, an install or ChatGPT's sign-in failed); the sidebar names the chosen AI from the
start instead of changing a moment after the window opens; a copy the clipboard refuses shows the text to copy by
hand everywhere, not only in the connection steps; and a pasted address that never came from any sign-in is not
called "an earlier sign-in's"."""

import json
import shutil
import threading
import time
import urllib.parse

import pytest

from studia import ai, ai_setup, chatgpt, i18n
from test_chatgpt import _follow, _sign_in, openai  # noqa: F401 - openai is a fixture
from test_cli_signin import _wait, cli  # noqa: F401 - cli is a fixture
from test_page_feedback import run

node = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")


@pytest.fixture
def switch():
    """switch("tr"): the language as Settings switches it (messages made from now on are in it); English after."""
    yield i18n.set_language
    i18n.set_language("en")


@pytest.fixture
def no_jobs():
    """No install left behind for the next test (an install's state is kept for the page)."""
    yield
    ai_setup._jobs.pop("claude", None)


def tr(text: str) -> str:
    out = i18n.table("tr")[text]
    assert out and out != text
    return out


# ------------------------------------------------------------------ messages made again in the new language
def test_a_kept_message_is_made_again_in_the_language_of_the_moment(switch):
    said = i18n.Retold(lambda: i18n.t("Try again"))
    assert said == "Try again" and isinstance(said, str)                 # a str like any other, as it was made
    switch("tr")
    assert i18n.retell(said) == "Tekrar dene" and said == "Try again"
    assert i18n.retell("plain text") == "plain text" and i18n.retell(None) is None and i18n.retell(3) == 3
    made = []

    def once():                                   # can't be made again (what it needs is gone): the words it had
        made.append(1)
        if len(made) > 1:
            raise RuntimeError("gone")
        return i18n.t("Try again")

    kept = i18n.Retold(once)
    assert i18n.retell(kept) == "Tekrar dene"


def test_a_failed_claude_code_sign_in_is_said_again_in_the_new_language(cli, switch):
    """The case: Claude Code's sign-in failed, the student switched to Turkish in Settings (the page reloads and asks
    again), and the step still said why in English until the next try."""
    cli.start("claude", "claude-fails")
    english = "Claude Code could not save your sign-in on this PC. Click the button to try again."
    assert _wait("claude", lambda s: s["state"] == "error")["error"] == english
    assert cli.events[-1]["error"] == english                             # told then, in the language of then
    switch("tr")
    assert ai.login_state("claude")["error"] == tr(english)
    assert json.dumps(ai.login_state("claude"), ensure_ascii=False)      # (the page gets plain text)
    switch("en")
    assert ai.login_state("claude")["error"] == english


def test_a_failed_install_is_said_again_in_the_new_language(monkeypatch, switch, no_jobs):
    def full_disk(emit, cancel):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(ai_setup, "_install_claude", full_disk)
    ai_setup.start_install("claude", lambda e: None)
    deadline = time.monotonic() + 10
    while ai_setup.job("claude").get("state") != "error" and time.monotonic() < deadline:
        time.sleep(0.02)
    english = "Your disk is full, so Claude Code could not be installed. Free up about 1 GB, then try again."
    assert ai_setup.job("claude")["error"] == english
    switch("tr")
    assert ai_setup.job("claude")["error"] == tr(english)


def test_installed_but_not_starting_is_said_again_in_the_new_language(monkeypatch, switch, no_jobs):
    """A failure already said in words (Plain): made again too."""
    monkeypatch.setattr(ai_setup, "_install_claude", lambda emit, cancel: None)
    monkeypatch.setattr(ai_setup, "_installed", lambda which: False)
    ai_setup.start_install("claude", lambda e: None)
    deadline = time.monotonic() + 10
    while ai_setup.job("claude").get("state") != "error" and time.monotonic() < deadline:
        time.sleep(0.02)
    english = ai_setup.job("claude")["error"]
    assert english.startswith("Claude Code was installed, but it doesn't start yet")
    switch("tr")
    assert ai_setup.job("claude")["error"] == tr(english)


def test_a_chatgpt_sign_in_that_failed_is_said_again_in_the_new_language(openai, switch, monkeypatch):  # noqa: F811
    e = _sign_in(openai, browser={"params": {"error": "access_denied"}})
    english = e["error"]
    assert english.startswith("You didn't allow IEUStudia to use your ChatGPT plan.")
    switch("tr")
    assert chatgpt.status()["sign_in"]["error"] == tr(english)
    switch("en")
    monkeypatch.setattr(chatgpt, "SIGN_IN_TIMEOUT_S", 1.0)               # the page never opened, time ran out
    # this PC's facts out of it: GitHub's Windows runner runs as administrator, which adds that note to the message
    monkeypatch.setattr(chatgpt, "_system", lambda name: name == "edge_available")
    done = threading.Event()
    chatgpt.start_sign_in(lambda ev: ev["state"] != "waiting" and done.set(), open_browser=lambda link: True)
    assert done.wait(10)
    english = chatgpt.status()["sign_in"]["error"]
    assert "never opened in a browser" in english
    switch("tr")
    assert chatgpt.status()["sign_in"]["error"] == tr(english)


# ------------------------------------------------------------------ the address pasted from the browser
def test_a_made_up_address_is_not_called_an_earlier_sign_ins(openai):  # noqa: F811
    first = chatgpt.start_sign_in(open_browser=lambda link: True)
    q1 = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(_follow(first["link"])[1]).query))
    second = chatgpt.start_sign_in(open_browser=lambda link: True)          # Continue with ChatGPT again
    q2 = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(_follow(second["link"])[1]).query))
    made_up = chatgpt.paste_address(f"{q2['redirect_uri']}?code=c0de&state=nope&client_id=oaiapp_123")
    assert made_up["state"] == "refused" and made_up["error"].startswith(
        "This isn't an address from the sign-in IEUStudia started.") and "earlier" not in made_up["error"]
    earlier = chatgpt.paste_address(f"{q1['redirect_uri']}?code=c0de&state={q1['state']}&client_id=oaiapp_123")
    assert earlier["state"] == "refused" and "belongs to an earlier sign-in" in earlier["error"]
    for said in (made_up, earlier):            # said while this sign-in waits: its row has Open the page again
        assert "Open the page again" in said["error"] and "Continue with ChatGPT" not in said["error"]
    assert chatgpt.status()["sign_in"]["state"] == "waiting"


# ------------------------------------------------------------------ the AI's name on the first screen
def test_the_first_screen_names_the_ai_chosen_now(monkeypatch):
    from studia import appconfig
    from studia.api import Api
    from studia.services import Services

    api = Api(Services())
    monkeypatch.setattr(api, "_system_info", lambda: None)
    appconfig.update({"ai_provider": "chatgpt"})
    assert api.get_bootstrap()["system"]["ai"] == {"provider": "chatgpt", "label": "ChatGPT", "available": False,
                                                   "can_plans": True, "can_images": True}    # no look yet on this PC
    seen = {"provider": "chatgpt", "label": "ChatGPT", "available": True, "version": "Continue with ChatGPT",
            "can_plans": True, "can_images": True, "efforts": ["low"]}
    api._s.lib.set_meta("system_info", json.dumps({"gpu": {"available": False}, "ai": seen}))
    assert api.get_bootstrap()["system"]["ai"] == seen                   # the last look, when it is the same AI
    appconfig.update({"ai_provider": "claude"})                          # another AI chosen since the last look
    boot = api.get_bootstrap()["system"]
    assert boot["ai"]["provider"] == "claude" and boot["ai"]["label"] == "Claude" and boot["ai"]["available"] is False
    assert boot["gpu"] == {"available": False}
    appconfig.update({"ai_provider": "local"})
    i18n.set_language("tr")
    try:
        assert api.get_bootstrap()["system"]["ai"]["label"] == "Çevrimdışı model"
    finally:
        i18n.set_language("en")


SIDEBAR = """
  const label = () => (els["#nav"].innerHTML.match(/data-view="chat">.*?<span class="code">(.*?)<\\/span>/s) || [])[1];
  els["#nav"] = { innerHTML: "" }; els["#sfoot"] = { innerHTML: "" };
  S.profiles = []; S.view = { name: "home" };
  S.system.ai = { available: false };                    // nothing known yet (no look on this PC so far)
  St.renderSidebar(); out.unknown = label();
  S.system.ai = { provider: "chatgpt", label: "ChatGPT", available: false };
  St.renderSidebar(); out.known = label();
  S.system.ai = { provider: "none", label: "", available: false };
  St.renderSidebar(); out.none = label();
"""


@node
def test_the_sidebar_shows_the_ais_name_once_it_is_known_and_never_a_wrong_one(tmp_path):
    out = run(tmp_path, SIDEBAR)
    assert "Ask" not in out["unknown"] and "skel" in out["unknown"]     # nothing to read until it's known
    assert out["known"] == "Ask ChatGPT" and out["none"] == "Ask AI"


# ------------------------------------------------------------------ a copy the clipboard refuses
COPIES = """
  const shown = [];
  St.copied = (r, text, done) => shown.push({ ok: r.ok, text, done });   // setup.js's Copy failed dialog
  St.Mock.copy_text = () => ({ ok: false, error: "Could not access the clipboard." });
  St.Mock.copy_for_ai = () => ({ ok: false, chars: 12, tokens: 3, text: "Transcript text", error: "Could not access the clipboard." });
  S.day = { day_key: "c|2026-10-14|lecture", date_label: "Wed 14 Oct", course: { code: "MATH 240" }, marks: [],
    parts: [{ index: 1, viewer_url: "https://panopto.ieu.edu.tr/v?id=x" }] };
  await A["copy-moment"]({ dataset: { part: "1", t: "75" } });
  S.chat.data = { id: "c1", messages: [{ seq: 2, text: "**Bayes**" }] };
  await A["chat-copy"]({ dataset: { seq: "2" } });
  await St.copyAI("notes");                                               // Copy for AI → Study notes prompt + transcript
  St.Mock.copy_for_ai = () => ({ ok: false, error: "Nothing transcribed yet." });
  await St.copyAI("notes");                                               // no text at all: the error, as before
  out.shown = shown; out.toasts = told();
"""


@node
def test_copy_buttons_outside_the_connection_steps_show_the_text_to_copy_by_hand(tmp_path):
    out = run(tmp_path, COPIES)
    moment, answer, for_ai, nothing = out["shown"]
    assert moment["ok"] is False and moment["text"] == "MATH 240 · Wed 14 Oct, 01:15: https://panopto.ieu.edu.tr/v?id=x&start=75"
    assert moment["done"] == "Link copied. Classmates open it on Panopto with their own IEU login."
    assert answer == {"ok": False, "text": "**Bayes**", "done": "Answer copied (as Markdown)."}
    assert for_ai["text"] == "Transcript text" and for_ai["done"].startswith("Copied 12 characters")
    assert nothing["text"] == ""                                          # (the dialog then shows the error)
    assert out["toasts"] == []                                            # not a red toast that leaves it nowhere


def test_copy_for_ai_gives_the_text_back_when_the_clipboard_refuses(monkeypatch):
    from studia import api as api_mod
    from studia.api import Api
    from studia.services import Services

    api = Api(Services())
    monkeypatch.setattr(api_mod, "set_clipboard", lambda text: False)
    monkeypatch.setattr(api._s, "day", lambda key: (None, []))
    import studia.outputs.ai_copy as ai_copy

    monkeypatch.setattr(ai_copy, "build_ai_text", lambda template, course, days: "The lecture's text")
    r = api.copy_for_ai("c|2026-10-14|lecture")
    assert r["ok"] is False and r["text"] == "The lecture's text" and r["error"] == "Could not access the clipboard."
    monkeypatch.setattr(api_mod, "set_clipboard", lambda text: True)
    r = api.copy_for_ai("c|2026-10-14|lecture")
    assert r["ok"] is True and "text" not in r


COPY_LAYER = """
globalThis.window = globalThis;
globalThis.location = { hash: "" };
const listeners = [], body = { children: [], appendChild(b) { this.children.push(b); b.parentNode = this; } };
let focused = "page", shown = "", modals = 0;
const zip = { focus() { focused = "zip"; } };                  // the export's "The zip is ready", open in #overlay
const els = { "#overlay .modal": zip, "#cn-copybox": null };
function element() {
  return { dataset: {}, className: "", id: "", innerHTML: "", removed: false, querySelector: () => ({}),
    remove() { this.removed = true; body.children = body.children.filter((x) => x !== this); } };
}
globalThis.document = { documentElement: {}, body, querySelectorAll: () => [], createElement: () => element(),
  querySelector: (s) => (s in els ? els[s] : null), getElementById: (id) => body.children.find((x) => x.id === id) || null,
  addEventListener: (name, f, capture) => listeners.push([name, f, !!capture]) };
require(%(i18n)s);
const S = { mode: "app", view: { name: "home" }, boot: { app: {} } };
window.Studia = { S, A: {}, I: (n) => `[${n}]`, SPIN: () => "(spin)", esc: (s) => String(s), onEvents: {}, mocks: {},
  toast() {}, renderView() {}, render() {}, call: () => Promise.resolve({ ok: false, error: "busy" }),
  modal: (h) => { shown = h; modals++; }, closeModal: () => { shown = ""; }, trapTab() {} };
require(%(setup)s);
const St = window.Studia, out = {};
const key = (k) => { const e = { key: k, prevented: false, stopped: false, preventDefault() { this.prevented = true; },
  stopPropagation() { this.stopped = true; } }; for (const [n, f, cap] of listeners) if (n === "keydown" && cap) f(e); return e; };
St.copied({ ok: false, error: "Copy failed" }, "This zip holds my MATH 240 course…", "Copied.");
const layer = document.getElementById("copy-fail");
out.layer = !!layer && layer.innerHTML.includes("This zip holds my MATH 240 course") && layer.innerHTML.includes("cn-copy-close");
out.modalsOver = modals;                                        // the dialog under it was not replaced
St.A["cn-copy-close"]();
out.closed = !document.getElementById("copy-fail") && focused === "zip";
St.copied({ ok: false, error: "Copy failed" }, "C:/x/Exports/MATH 240.zip", "Path copied.");
const esc = key("Escape");
out.escape = !document.getElementById("copy-fail") && esc.stopped && esc.prevented;
els["#overlay .modal"] = null;                                  // no dialog open: the Copy failed dialog itself
St.copied({ ok: false, error: "Copy failed" }, "a link", "Link copied.");
out.alone = modals === 1 && shown.includes("Copy failed") && !document.getElementById("copy-fail");
process.stdout.write(JSON.stringify(out), () => process.exit(0));
"""


@node
def test_copy_failed_over_the_exports_dialog_leaves_that_dialog_as_it_is(tmp_path):
    """Copy the first message refused by the clipboard: the Copy failed dialog took the place of "The zip is ready", so
    after Close the zip's path, Show in folder and the upload steps were gone."""
    import subprocess

    from pathlib import Path

    web = Path(i18n.__file__).parent / "web"
    script = tmp_path / "layer.js"
    script.write_text(COPY_LAYER % {"i18n": json.dumps(str(web / "i18n.js")), "setup": json.dumps(str(web / "setup.js"))},
                      encoding="utf-8")
    out = json.loads(subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8",
                                    check=True).stdout)
    assert out == {"layer": True, "modalsOver": 0, "closed": True, "escape": True, "alone": True}


def test_an_update_check_that_failed_is_said_again_in_the_new_language(switch, monkeypatch):
    from studia import updates

    class Offline:
        headers: dict = {}

        def get(self, *a, **k):
            import requests

            raise requests.ConnectionError("Max retries exceeded (NameResolutionError: [Errno 11001] getaddrinfo failed)")

    monkeypatch.setattr(updates, "_session", lambda: Offline())
    updates.check()
    english = updates.status()["error"]
    assert english
    switch("tr")
    turkish = updates.status()["error"]
    assert turkish and turkish != english                       # made again in the language of the moment


def test_a_failed_test_message_is_said_again_in_the_new_language(switch, monkeypatch, no_jobs):
    def refuse(*a, **k):
        raise RuntimeError("Your account does not have access to Claude Code")

    monkeypatch.setattr(ai, "_claude_one_shot", refuse)
    ai_setup.test("claude")
    english = ai_setup.last_test("claude")["error"]
    assert english == "Your Claude plan doesn't include Claude Code. You need Claude Pro or Max."
    switch("tr")
    assert ai_setup.last_test("claude")["error"] == tr(english)
    ai_setup.forget_test("claude")
