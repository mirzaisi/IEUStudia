"""The sign-in screen (app.js in node, with a stand-in for the app's API): a refused sign-in keeps the username you
typed and shows IEULearn's words; a page IEULearn wants you to finish (its terms, a new password) offers to open
learn.ieu.edu.tr in the browser; Caps Lock is said under the password."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from studia import i18n

WEB = Path(i18n.__file__).parent / "web"

HARNESS = """
const web = %(web)s;
const stub = () => new Proxy(function () {}, { get: (t, k) => k === Symbol.toPrimitive ? () => "" : k === "length" ? 0 : stub(),
  apply: () => stub(), construct: () => stub() });
globalThis.window = globalThis;
const el = () => ({ innerHTML: "", style: {}, dataset: {}, classList: { toggle() {}, add() {}, remove() {} }, scrollTo() {},
  querySelector: () => null, querySelectorAll: () => [], addEventListener() {}, remove() {}, focus() {} });
const els = { "#top": el(), "#view": el(), "#root": el(), "#toasts": { appendChild() {} }, "#si-caps": el(), "#overlay": el() };
const listeners = {};
globalThis.document = new Proxy({}, { get: (t, k) => k === "compatMode" ? "CSS1Compat"
  : k === "createElement" ? () => el()
  : k === "querySelector" ? (s) => els[s] || stub()
  : k === "querySelectorAll" ? () => []
  : k === "addEventListener" ? (n, f) => { (listeners[n] = listeners[n] || []).push(f); } : stub() });
globalThis.localStorage = { getItem: () => null, setItem() {} };
globalThis.sessionStorage = { getItem: () => null, setItem() {}, removeItem() {} };
globalThis.navigator = { userAgent: "node", platform: "Win32" };
globalThis.CSS = { escape: (s) => s };
globalThis.addEventListener = () => {}; globalThis.matchMedia = () => ({ matches: false, addEventListener() {} });
globalThis.requestAnimationFrame = (f) => f();
const later = setTimeout;
globalThis.setInterval = () => 0; globalThis.setTimeout = () => 0;
const calls = [];
let answer = {};
globalThis.pywebview = { api: {
  i18n: async () => ({ ok: true, lang: "en", table: {} }),
  get_bootstrap: async () => ({ ok: true, signed_in: false, app: { onboarded: true }, settings: {}, profiles: [] }),
  sign_in: async (u, p) => { calls.push(["sign_in", u, p]); return answer; },
  profile_add: async (u, p) => { calls.push(["profile_add", u]); return answer; },
  sync_stop: async () => { calls.push(["sync_stop"]); return { ok: true }; },
  open_url: async (u) => { calls.push(["open_url", u]); return { ok: true }; },
} };
for (const f of ["i18n.js", "icons.js", "app.js"]) require(web + "/" + f);
const St = globalThis.Studia, S = St.S, A = St.A;
const tick = (ms = 30) => new Promise((r) => later(r, ms));
const ev = (e) => window.__lsEvent(e);
const root = () => els["#root"].innerHTML.replace(/\\s+/g, " ");
const top = () => els["#top"].innerHTML.replace(/\\s+/g, " ");
const submit = (u, p) => listeners.submit.forEach((f) => f({ target: { dataset: { form: "signin" }, username: { value: u },
  password: { value: p } }, preventDefault() {} }));
const out = {};
(async () => {
  await tick(60);
%(body)s
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""


def run(tmp_path, body):
    script = tmp_path / "signin.js"
    script.write_text(HARNESS % {"web": json.dumps(WEB.as_posix()), "body": body}, encoding="utf-8")
    r = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


node = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")


@node
def test_a_refused_sign_in_keeps_your_username_and_a_page_to_finish_offers_ieulearn(tmp_path):
    out = run(tmp_path, """
  out.mode = S.mode; out.first = root();
  answer = { ok: false, error: "E-posta veya şifre hatalı. Your password starts or ends with a space.", need_login: true };
  submit(" ali.veli ", "right "); await tick();
  out.refused = root(); out.calls = calls.splice(0);
  answer = { ok: false, error: "IEULearn wants you to change your password first.", need_login: true, open_site: true };
  submit("ali.veli", "right"); await tick();
  out.action = root();
  ev({ type: "sync", phase: "error", need_login: true, message: "IEULearn didn't accept your saved password (was it changed?). Sign in again." });
  out.plain = root();
""")
    assert out["mode"] == "signin" and 'id="si-caps"' in out["first"] and "Caps Lock is on." in out["first"]
    assert out["calls"] == [["sign_in", "ali.veli", "right "]]                        # the password as you typed it
    assert 'value="ali.veli"' in out["refused"] and "starts or ends with a space" in out["refused"]
    assert 'data-url="https://learn.ieu.edu.tr/login"' not in out["refused"]
    assert "change your password first" in out["action"] and 'data-act="url" data-url="https://learn.ieu.edu.tr/login"' in out["action"]
    assert "Open learn.ieu.edu.tr in your browser" in out["action"]
    assert "saved password" in out["plain"] and 'data-url="https://learn.ieu.edu.tr/login"' not in out["plain"]


@node
def test_adding_another_account_offers_ieulearn_too_for_a_page_to_finish_there(tmp_path):
    out = run(tmp_path, """
  A["profile-add"]();
  answer = { ok: false, error: "IEULearn wants you to change your password first.", need_login: true, open_site: true };
  listeners.submit.forEach((f) => f({ target: { dataset: { form: "add-account" }, username: { value: "ayse.k" },
    password: { value: "pw" } }, preventDefault() {} }));
  await tick();
  out.dialog = els["#overlay"].innerHTML.replace(/\\s+/g, " "); out.calls = calls.splice(0);
""")
    assert out["calls"] == [["profile_add", "ayse.k"]]
    assert "change your password first" in out["dialog"] and 'data-url="https://learn.ieu.edu.tr/login"' in out["dialog"]
    assert 'value="ayse.k"' in out["dialog"]


@node
def test_a_page_to_finish_found_by_a_sync_also_offers_ieulearn(tmp_path):
    out = run(tmp_path, """
  ev({ type: "sync", phase: "error", need_login: true, open_site: true, message: "IEULearn wants something from you before it lets you in." });
  out.page = root();
""")
    assert "wants something from you" in out["page"] and 'data-url="https://learn.ieu.edu.tr/login"' in out["page"]
