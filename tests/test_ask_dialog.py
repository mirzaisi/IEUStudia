"""Questions in IEUStudia's own dialog (fx-contact) instead of the window's confirm() and prompt() boxes: a title, plain
text, Cancel and a clear main button (red for what can't be undone), Enter and Escape, the focus back where it was,
and over a dialog that is open without closing it. WebView2's own permission questions are answered by the app for
its own page, so the browser's bar never shows. (app.js in node with stand-ins for the page; no window.)"""

import json
import re
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

from studia import i18n
from test_page_feedback import run

WEB = Path(i18n.__file__).parent / "web"
node = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")

DIALOG = """
globalThis.window = globalThis;
const listeners = {};
// just enough of an element for the dialog: its buttons and its box, found in the markup it was given
function element(tag) {
  const box = { tag, on: {}, removed: false, className: "", id: "", _html: "",
    set innerHTML(h) { this._html = h; this.parts = parse(this, h); }, get innerHTML() { return this._html; },
    addEventListener(n, f) { this.on[n] = f; }, remove() { this.removed = true; }, closest: () => null,
    querySelector(s) { return this.parts && this.parts[s] || null; }, querySelectorAll: () => [] };
  return box;
}
function parse(box, h) {
  const part = (attrs) => ({ ...attrs, isConnected: true, offsetParent: {}, focus() { focused = this.name; },
    select() { this.selected = true; }, closest(s) { return s.includes("button") || s === "[data-ask]" ? (this.button ? this : null) : null } });
  const yes = part({ name: "yes", button: true, dataset: { ask: "yes" } }), no = part({ name: "no", button: true, dataset: { ask: "no" } });
  const m = h.match(/id="ask-value"[^>]*value="([^"]*)"/);
  const input = m ? part({ name: "input", value: m[1].replace(/&amp;/g, "&").replace(/&quot;/g, '"') }) : null;
  return { "[data-ask='yes']": yes, "[data-ask=\\"yes\\"]": yes, "[data-ask='no']": no, "#ask-value": input,
    ".modal": part({ name: "modal" }), yes, no, input };
}
let focused = "page";
const opener = { isConnected: true, focus() { focused = "opener"; } };
const under = { innerHTML: '<div class="modal">Add another account</div>', isConnected: true, focus() { focused = "under"; },
  contains: () => false };
const els = { "#root": { innerHTML: "" }, "#overlay": { innerHTML: "" }, "#overlay .modal": null, "#toasts": { appendChild() {} } };
const body = { children: [], appendChild(b) { this.children.push(b); } };
globalThis.document = { documentElement: { dataset: {}, style: { setProperty() {} } }, body, activeElement: opener,
  createElement: (tag) => element(tag), querySelector: (s) => (s in els ? els[s] : null), querySelectorAll: () => [],
  addEventListener: (n, f) => { (listeners[n] = listeners[n] || []).push(f); } };
globalThis.localStorage = { getItem: () => null, setItem() {} };
globalThis.navigator = { userAgent: "node" }; globalThis.CSS = { escape: (s) => s };
globalThis.addEventListener = () => {}; globalThis.matchMedia = () => ({ matches: false, addEventListener() {} });
globalThis.requestAnimationFrame = (f) => f();
globalThis.setInterval = () => 0; const later = setTimeout; globalThis.setTimeout = () => 0;
require(%(i18n)s); require(%(icons)s); require(%(app)s);
const St = window.Studia;
const key = (k, target, repeat) => { const e = { key: k, target, repeat: !!repeat, defaultPrevented: false, stopped: false, preventDefault() { this.defaultPrevented = true; },
  stopImmediatePropagation() { this.stopped = true; } }; for (const f of listeners.keydown) { f(e); if (e.stopped) break; } return e; };
const top = () => body.children[body.children.length - 1];
const out = {};
(async () => {
  // a destructive question: its markup, the main button in red, the focus on Cancel
  let p = St.ask({ title: "Delete this chat?", text: "This <b>cannot</b> be undone.", ok: "Delete", danger: true });
  const box = top();
  out.html = box.innerHTML; out.layer = [box.id, box.className]; out.focusFirst = focused;
  // Enter (or Space) held down from the × that asked: its repeats answer nothing, on either button
  const held = [key("Enter", box.parts.no, true), key("Enter", box.parts.yes, true), key(" ", box.parts.yes, true)];
  out.held = { kept: held.every((e) => e.defaultPrevented && e.stopped), open: !box.removed };
  box.on.click({ target: box.parts.yes });
  out.clickYes = await p; out.removed = box.removed; out.focusBack = focused;
  // Escape is Cancel, Enter the main button; neither reaches the page's own keys (a dialog under it stays open)
  els["#overlay"].innerHTML = under.innerHTML; els["#overlay .modal"] = under;
  document.activeElement = opener; opener.isConnected = false;          // (the button that asked was drawn again)
  p = St.ask({ title: "Sign out?", ok: "Sign out" });
  const esc = key("Escape", top().parts.yes);
  out.escape = await p; out.escapeStopped = esc.stopped && esc.defaultPrevented;
  out.underStill = els["#overlay"].innerHTML === under.innerHTML; out.focusUnder = focused;
  p = St.ask({ title: "Sign out?", ok: "Sign out" });
  key("Enter", { closest: () => null });                                  // Enter somewhere in the dialog
  out.enter = await p;
  p = St.ask({ title: "Remove this profile?", ok: "Remove", danger: true });
  key("Enter", { closest: () => null });                                  // what can't be undone: Enter is Cancel
  out.enterDanger = await p;
  // Enter on the Cancel button presses that button (the click it makes answers), not the main one
  p = St.ask({ title: "Sign out?", ok: "Sign out" });
  const onCancel = key("Enter", top().parts.no);
  out.enterOnCancel = onCancel.defaultPrevented;
  top().on.click({ target: top().parts.no });
  out.cancelled = await p;
  // a click beside the dialog is Cancel; one inside it is nothing
  p = St.ask({ title: "Sign out?" });
  const b = top();
  b.on.click({ target: { closest: () => null } });
  out.insideOpen = !b.removed;
  b.on.click({ target: b });
  out.beside = await p;
  // a line of text: it starts with what there is, Enter keeps what was typed, Cancel is null
  p = St.ask({ title: "Name this chat", value: "Bayes & co", ok: "Rename" });
  const t1 = top();
  out.value = { start: t1.parts.input.value, selected: !!t1.parts.input.selected, focus: focused };
  t1.parts.input.value = "  Bayes, part 2 ";
  key("Enter", t1.parts.input);
  out.typed = await p;
  p = St.ask({ title: "Name this chat", value: "x" });
  key("Escape", top().parts.input);
  out.typedCancel = await p;
  // a new question while one is open: the first one is answered Cancel
  const first = St.ask({ title: "One" }), second = St.ask({ title: "Two" });
  out.replaced = await first;
  top().on.click({ target: top().parts.yes });
  out.second = await second;
  out.left = body.children.filter((x) => !x.removed).length;
  console.log(JSON.stringify(out));
  process.stdout.write("", () => process.exit(0));      // after the output is out (Linux pipes)
})().catch((e) => { console.error(e); process.exit(1); });
"""


@node
def test_the_dialog_asks_in_the_apps_own_style_and_answers_with_enter_escape_and_its_buttons(tmp_path):
    script = tmp_path / "ask.js"
    script.write_text(DIALOG % {k: json.dumps((WEB / f).as_posix()) for k, f in
                                (("i18n", "i18n.js"), ("icons", "icons.js"), ("app", "app.js"))}, encoding="utf-8")
    r = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    html = out["html"]
    assert out["layer"] == ["ask", "backdrop ask-back"]                   # a layer of its own, not the open dialog's
    assert 'role="alertdialog"' in html and 'aria-modal="true"' in html and 'aria-labelledby="ask-title"' in html
    assert '<h2 id="ask-title">Delete this chat?</h2>' in html
    assert "This &lt;b&gt;cannot&lt;/b&gt; be undone." in html            # plain text, never markup
    assert re.search(r'<button class="btn" data-ask="no">Cancel</button>\s*<button class="btn primary danger" data-ask="yes">Delete</button>', html)
    assert out["focusFirst"] == "no" and out["held"] == {"kept": True, "open": True}
    assert out["clickYes"] is True and out["removed"] and out["focusBack"] == "opener"     # only its own red button
    # Escape: Cancel, kept from the page (the dialog under it stays open and gets the focus back)
    assert out["escape"] is False and out["escapeStopped"] and out["underStill"] and out["focusUnder"] == "under"
    assert out["enter"] is True and out["enterDanger"] is False
    assert out["enterOnCancel"] is False and out["cancelled"] is False
    assert out["insideOpen"] and out["beside"] is False
    assert out["value"] == {"start": "Bayes & co", "selected": True, "focus": "input"}
    assert out["typed"] == "  Bayes, part 2 " and out["typedCancel"] is None
    assert out["replaced"] is False and out["second"] is True and out["left"] == 0


def _code(p: Path) -> str:
    src = re.sub(r"/\*.*?\*/", "", p.read_text(encoding="utf-8"), flags=re.S)
    return re.sub(r"(?m)//.*$", "", src)


def test_no_page_uses_the_windows_own_confirm_alert_or_prompt():
    """WebView2 shows those as "file:// says…" boxes in the browser's style: every question goes through Studia.ask."""
    bad = {}
    for p in sorted(WEB.glob("*.js")):
        code = _code(p)
        found = re.findall(r"(?<![\w$.])(?:confirm|alert|prompt)\s*\(", code) + re.findall(r"\bwindow\.(?:confirm|alert|prompt)\b", code)
        if found:
            bad[p.name] = found
    assert not bad, bad


CALLS = """
  const asked = []; let answer = false;
  St.ask = (q) => { asked.push(q); return Promise.resolve(typeof answer === "function" ? answer(q) : answer); };
  const calls = [];
  for (const name of ["cancel", "sign_out", "profile_remove", "chat_update", "chat_delete", "mark_set", "plan_delete", "transcribe_day"])
    St.mocks[name] = St.Mock[name] = (...a) => { calls.push([name, ...a]); return { ok: true, profiles: [], chat: { title: a[1] && a[1].title, model: "sonnet" } }; };
  S.queue = [{ id: "a", day: "d1" }, { id: "b", day: "d1" }, { id: "c", day: "d2" }];
  await A["cancel-all"](); answer = true; await A["cancel-all"]();
  S.profiles = [{ id: "p2", name: "Ayşe Demir" }];
  answer = false; await A["profile-remove"]({ dataset: { id: "p2" } });
  answer = false; await A.signout();
  S.day = { day_key: "k", marks: [{ id: "m1", t: 10, part: 1, note: "old", text: "x" }], parts: [{ index: 1, id: "m1", paragraphs: [{ t: 10, text: "x" }] }] };
  answer = "  my note  "; await A["mark-note"]({ dataset: { id: "m1", t: "10" } });
  S.chat.data = { id: "c1", title: "Bayes", messages: [], days: [] };
  answer = null; await A["chat-rename"]();
  out.asked = asked; out.calls = calls;
"""


@node
def test_each_question_of_the_page_is_asked_in_the_apps_own_dialog(tmp_path):
    out = run(tmp_path, CALLS)
    by = {q["title"]: q for q in out["asked"]}
    q = by["Cancel all 2 lectures in the queue?"]
    assert q["danger"] and q["ok"] == "Cancel all" and q["cancel"] == "Keep them" and q["text"] == "To stop for a while instead, use Pause."
    q = by["Remove the profile of Ayşe Demir from this PC?"]           # (not "Remove Ayşe Demir": the person)
    assert q["danger"] and q["ok"] == "Remove" and "Files already saved in its IEUStudia folder stay." in q["text"]
    q = by["Sign out?"]
    assert q["danger"] and q["ok"] == "Sign out" and "Your courses and transcripts stay here." in q["text"]
    q = by["A note for this passage"]
    assert q["value"] == "old" and q["ok"] == "Save"
    assert by["Name this chat"]["value"] == "Bayes" and by["Name this chat"]["ok"] == "Rename"
    names = [c[0] for c in out["calls"]]
    # No answers nothing; Yes does what was asked; a note is kept trimmed; Cancel in the name box keeps the name
    assert names.count("cancel") == 1 and ["cancel", None] in out["calls"]
    assert "profile_remove" not in names and "sign_out" not in names and "chat_update" not in names
    assert any(c[0] == "mark_set" and "my note" in c for c in out["calls"])


# ------------------------------------------------------------------ WebView2's own permission questions
def test_the_apps_own_page_may_read_the_clipboard_and_nothing_else_asks_in_the_window():
    from studia.app import permission_answer

    page = (WEB / "index.html").as_uri()
    assert permission_answer(page, "ClipboardRead") and permission_answer("file:///", "ClipboardRead")
    assert permission_answer(page, "Autoplay")
    for kind in ("Microphone", "Camera", "Geolocation", "Notifications", "OtherSensors", "FileReadWrite",
                 "MultipleAutomaticDownloads", "LocalFonts", "WindowManagement", "UnknownPermission", "6"):
        assert not permission_answer(page, kind), kind
    assert not permission_answer("https://chatgpt.com/", "ClipboardRead") and not permission_answer("", "ClipboardRead")


def test_webview2s_permission_questions_are_answered_before_its_bar_shows(monkeypatch):
    """pywebview gets WebView2 ready in the window (on_webview_ready): IEUStudia's answer is hooked in there, and each
    question gets Allow or Deny at once (not kept in WebView2's profile: the next one is asked again)."""
    from studia import app

    class Event:                                  # a .NET event: handlers are added with +=
        def __init__(self):
            self.handlers = []

        def __iadd__(self, f):
            self.handlers.append(f)
            return self

    class EdgeChrome:
        def on_webview_ready(self, sender, args):
            self.ready = True

    edge = types.ModuleType("webview.platforms.edgechromium")
    edge.EdgeChrome = EdgeChrome
    core = types.ModuleType("Microsoft.Web.WebView2.Core")
    core.CoreWebView2PermissionState = types.SimpleNamespace(Allow="Allow", Deny="Deny", Default="Default")
    monkeypatch.setitem(sys.modules, "webview.platforms.edgechromium", edge)
    monkeypatch.setitem(sys.modules, "Microsoft.Web.WebView2.Core", core)
    monkeypatch.setitem(sys.modules, "Microsoft", types.ModuleType("Microsoft"))
    started = []
    fake = types.SimpleNamespace(initialize=lambda *a, **k: started.append(a) or "winforms")
    app._answer_permissions(fake)
    app._answer_permissions(fake)                                     # (once only)
    assert fake.initialize("edgechromium") == "winforms" and started == [("edgechromium",)]
    sender = types.SimpleNamespace(CoreWebView2=types.SimpleNamespace(PermissionRequested=Event()))
    chrome = EdgeChrome()
    chrome.on_webview_ready(sender, types.SimpleNamespace(IsSuccess=True))
    assert chrome.ready and sender.CoreWebView2.PermissionRequested.handlers == [app._on_permission]

    class Kind:
        def __init__(self, name):
            self.name = name

        def ToString(self):                       # noqa: N802 - .NET's name
            return self.name

    page = (WEB / "index.html").as_uri()
    asks = [types.SimpleNamespace(PermissionKind=Kind(k), Uri=u, State="Default", SavesInProfile=True)
            for k, u in (("ClipboardRead", page), ("Camera", page), ("ClipboardRead", "https://example.com/"))]
    for a in asks:
        app._on_permission(None, a)
    assert [a.State for a in asks] == ["Allow", "Deny", "Deny"] and not any(a.SavesInProfile for a in asks)
    failed = types.SimpleNamespace(CoreWebView2=types.SimpleNamespace(PermissionRequested=Event()))
    EdgeChrome().on_webview_ready(failed, types.SimpleNamespace(IsSuccess=False))      # WebView2 didn't start
    assert failed.CoreWebView2.PermissionRequested.handlers == []
