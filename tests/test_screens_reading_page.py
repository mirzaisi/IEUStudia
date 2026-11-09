"""After the update, the screens of lectures transcribed before are read in the background (screens.ScreenWorker):
Settings → Storage says so in a quiet line while it runs, its count going up as lectures are done, and nothing pops
up (setup.js's Storage card, settings.js's row, run in node with app.js)."""

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
const toasts = [];
const el = () => ({ innerHTML: "", outerHTML: "", style: {}, dataset: {}, classList: { toggle() {}, add() {}, remove() {}, contains: () => false },
  scrollTo() {}, querySelector: () => null, querySelectorAll: () => [], addEventListener() {}, remove() {}, focus() {}, contains: () => false });
const els = { "#top": el(), "#view": el(), "#overlay": el(), "#toasts": { appendChild: (b) => toasts.push(b) } };
globalThis.document = new Proxy({}, { get: (t, k) => k === "compatMode" ? "CSS1Compat"
  : k === "activeElement" ? null : k === "hidden" ? false : k === "createElement" ? () => el()
  : k === "querySelector" ? (s) => (s in els ? els[s] : stub())
  : k === "getElementById" ? (id) => els["#" + id] || null
  : k === "querySelectorAll" ? () => [] : k === "addEventListener" ? () => {} : stub() });
globalThis.localStorage = { getItem: () => null, setItem() {} };
globalThis.navigator = { userAgent: "node", platform: "Win32" };
globalThis.location = { search: "", hash: "", href: "" };
globalThis.CSS = { escape: (s) => s };
globalThis.addEventListener = () => {}; globalThis.matchMedia = () => ({ matches: false, addEventListener() {} });
globalThis.requestAnimationFrame = (f) => f();
globalThis.setInterval = () => 0; globalThis.setTimeout = () => 0;      // no boot, no polling
for (const f of ["i18n.js", "match.js", "icons.js", "app.js"]) require(web + "/" + f);
globalThis.Studia.sq = { about: () => "" };                           // (another card's line, not this test's)
for (const f of ["setup.js", "settings.js"]) require(web + "/" + f);
const St = globalThis.Studia, S = St.S;
const ev = (e) => window.__lsEvent(e);
const plain = (h) => String(h).replace(/<[^>]*>/g, " ").replace(/&#39;/g, "'").replace(/\\s+/g, " ").trim();
const storage = () => { const h = St.settingsMore(); return h.slice(h.indexOf('id="set-storage"'), h.indexOf('id="set-about"')); };
const out = {};
S.mode = "app"; S.view = { name: "settings" }; S.boot = { app: {} };
out.idle = storage();
ev({ type: "screens_reading", done: 0, total: 72 });                  // the reading starts
out.started = storage();
els["#set-scr-reading"] = el();                                       // Settings is open: only the line changes
ev({ type: "screens_reading", done: 12, total: 72 });
out.line = els["#set-scr-reading"].outerHTML;
ev({ type: "screens_reading", done: 72, total: 72 });                 // done: the line goes
out.done = els["#set-scr-reading"].outerHTML;
out.after = storage();
out.toasts = toasts.length;
process.stdout.write(JSON.stringify(out), () => process.exit(0));
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_settings_storage_says_quietly_that_earlier_lectures_screens_are_being_read(tmp_path):
    script = tmp_path / "page.js"
    script.write_text(HARNESS % {"web": json.dumps(WEB.as_posix())}, encoding="utf-8")
    r = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert 'id="set-scr-reading" hidden' in out["idle"] and "Reading the screens" not in out["idle"]
    assert "Reading the screens of earlier lectures" in out["started"] and "Lectures read: 0 of 72" in out["started"]
    assert "waits while a lecture is transcribed" in out["started"]
    assert "Lectures read: 12 of 72" in out["line"] and 'class="set-row" id="set-scr-reading"' in out["line"]
    assert out["done"] == '<div id="set-scr-reading" hidden></div>' and "Reading the screens" not in out["after"]
    assert out["toasts"] == 0                                         # nothing pops up
