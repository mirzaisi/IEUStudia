"""The first start after an update that didn't finish (app.js in node against the browser preview's stand-ins): the
update dialog opens by itself with what happened and what to do (Try again, the release page), instead of the old
version and the same Update button, unexplained."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from studia import i18n

WEB = Path(i18n.__file__).parent / "web"

SCRIPT = """
const web = %(web)s;
const stub = () => new Proxy(function () {}, { get: (t, k) => k === Symbol.toPrimitive ? () => "" : k === "length" ? 0 : stub(),
  apply: () => stub(), construct: () => stub() });
globalThis.window = globalThis;
const el = () => ({ innerHTML: "", style: {}, dataset: {}, classList: { toggle() {}, add() {}, remove() {} }, scrollTo() {},
  querySelector: () => null, querySelectorAll: () => [], addEventListener() {}, remove() {}, focus() {} });
const upTxt = { textContent: "" }, upBar = { classList: { toggle() {} }, firstElementChild: { style: {} } };
const els = { "#top": el(), "#view": el(), "#overlay": el(), "#toasts": { appendChild() {} },
  "#update-modal": { querySelector: (s) => (s === ".up-txt" ? upTxt : s === ".up-bar" ? upBar : null) } };
let now = 1.8e12;
Date.now = () => now;
globalThis.document = new Proxy({}, { get: (t, k) => k === "compatMode" ? "CSS1Compat"
  : k === "createElement" ? () => el()
  : k === "querySelector" ? (s) => els[s] || stub()
  : k === "querySelectorAll" ? () => []
  : k === "addEventListener" ? () => {} : stub() });
globalThis.localStorage = { getItem: () => null, setItem() {} };
globalThis.location = { hash: "" };
globalThis.navigator = { userAgent: "node", platform: "Win32" };
globalThis.CSS = { escape: (s) => s };
globalThis.addEventListener = () => {}; globalThis.matchMedia = () => ({ matches: false, addEventListener() {} });
globalThis.requestAnimationFrame = (f) => f();
globalThis.setInterval = () => 0; globalThis.setTimeout = () => 0;      // no boot, no polling
for (const f of ["i18n.js", "icons.js", "app.js", "course.js", "setup.js"]) require(web + "/" + f);
const St = globalThis.Studia, S = St.S;
const out = {};
const boot = (update) => {
  els["#overlay"].innerHTML = "";
  S.boot = { version: "3.2.2", app: { onboarded: true }, last_sync: new Date().toISOString() };
  S.lastSync = S.boot.last_sync; S.courses = []; S.view = { name: "home" };
  S.update = update;
  St.afterBoot();
  return els["#overlay"].innerHTML;
};
out.failed = boot({ current: "3.2.2", available: true, version: "3.3.0", can_install: true, failed: "3.3.0", state: "error",
  error: "The update to 3.3.0 didn't finish, so you still have 3.2.2; nothing of yours changed." });
out.available = boot({ current: "3.2.2", available: true, version: "3.3.0", can_install: true, error: "" });
// the update downloading: its speed and time left, and a stall, in the dialog's line
const ev = (done, total) => window.__lsEvent({ type: "update", state: "downloading", done, total });
const MB = 1048576;
ev(0, 0);
for (let i = 1; i <= 5; i++) { now += 1000; ev(i * 2 * MB, 100 * MB); }
out.flowing = upTxt.textContent;
for (let i = 0; i < 30; i++) { now += 1000; ev(10 * MB, 100 * MB); }
out.stalled = upTxt.textContent;
// Try again after a failed try: counted from this click, not from the last try's start
S.update = { ...S.update, state: "error", error: "x" };
St.A["update-go"]();
window.__lsEvent({ type: "update", state: "downloading", done: 0, total: 0 });
out.retry = upTxt.textContent;
out.retryBegan = S.update.began === now;
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_an_update_that_did_not_finish_opens_its_dialog_with_what_to_do(tmp_path):
    script = tmp_path / "update.js"
    script.write_text(SCRIPT % {"web": json.dumps(WEB.as_posix())}, encoding="utf-8")
    run = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout.strip().splitlines()[-1])
    out["failed"] = out["failed"].replace("&#39;", "'")
    assert "didn't finish" in out["failed"] and 'data-act="update-go"' in out["failed"]
    assert 'data-act="update-page"' in out["failed"] and "Try again" in out["failed"]
    assert out["available"] == ""                       # a plain new version opens nothing by itself
    assert out["flowing"].startswith("Downloading the new version · 10% of 105 MB · ")
    assert "MB/s" in out["flowing"] and "left" in out["flowing"]
    assert "Nothing has arrived for" in out["stalled"] and "keeps trying" in out["stalled"]
    assert out["retryBegan"] and "Still connecting" not in out["retry"]
