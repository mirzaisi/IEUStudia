"""Reasoning levels on the page (effort.js, run in node with a stand-in for app.js): the "Use High reasoning?" question
closed with Escape or a click outside counts as "no", so the chat's chip and Settings show the level still in use; the
weekly digest's chooser keeps a level only once it is confirmed."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from studia import i18n

WEB = Path(i18n.__file__).parent / "web"

SCRIPT = """
globalThis.window = globalThis;
const store = {}, chips = [];
globalThis.localStorage = { getItem: (k) => store[k] || null, setItem: (k, v) => { store[k] = v; } };
const el = (id) => ({ set outerHTML(h) { chips.push([id, (h.match(/value="(\\w+)" selected/) || [])[1]]); } });
globalThis.document = { documentElement: {}, querySelector: () => null, querySelectorAll: () => [], addEventListener() {},
  getElementById: (id) => (id.startsWith("eff-") ? el(id) : null) };
require(%(i18n)s);
const calls = [], draws = [];
let shown = "";
const S = { system: { ai: { provider: "chatgpt", available: true, efforts: ["low", "medium", "high", "xhigh", "max"] } },
  settings: { ai_effort: { "chatgpt.chat": "low", "chatgpt.digest": "low" } }, view: { name: "chat" },
  chat: { draft: null, data: { id: "c1", effort: "low", messages: [], context_tokens: 0 } } };
window.Studia = { S, A: {}, I: () => "", esc: (s) => String(s), toast() {}, fmtK: (n) => n, aiName: () => "ChatGPT",
  renderView: () => draws.push(S.view.name),
  call: (name, ...args) => { calls.push([name, ...args]); return Promise.resolve(name === "chat_update" ? { ok: true, chat: { effort: args[1].effort } } : { ok: true, settings: S.settings }); },
  modal: (h) => { shown = h; },
  closeModal: () => { shown = ""; const hook = window.Studia.onModalClose; window.Studia.onModalClose = null; if (hook) hook(); } };
require(%(effort)s);
const St = window.Studia, A = St.A, tick = () => new Promise((r) => setTimeout(r, 5));
const out = {};
(async () => {
  A["eff-chip"]({ value: "max" }); await tick();
  out.asked = !!shown;
  St.closeModal(); await tick();                         // Escape
  out.chipEscape = { calls: calls.splice(0), chips: chips.splice(0) };
  S.view = { name: "settings" };
  A["eff-set"]({ dataset: { use: "chat" }, value: "high" }); await tick();
  St.closeModal(); await tick();                         // a click outside
  out.settingsEscape = { calls: calls.splice(0), draws: draws.splice(0) };
  S.view = { name: "chat" };
  A["eff-chip"]({ value: "max" }); await tick();
  A["eff-yes"](); await tick();
  out.chipYes = { calls: calls.splice(0), chips: chips.splice(0), open: !!shown };
  out.inline = St.effortInline("dg", "digest");
  A["eff-inline"]({ dataset: { ctx: "dg" }, value: "high" }); await tick();
  out.inlineAsked = !!shown;
  St.closeModal(); await tick();
  out.inlineEscape = { pick: St.effortPick("dg"), chips: chips.splice(0) };
  A["eff-inline"]({ dataset: { ctx: "dg" }, value: "medium" }); await tick();
  out.inlineMedium = { pick: St.effortPick("dg"), chips: chips.splice(0), asked: !!shown };
  console.log(JSON.stringify(out));
})();
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_closing_the_heavy_level_question_keeps_the_level_in_use(tmp_path):
    script = tmp_path / "effort.js"
    script.write_text(SCRIPT % {"i18n": json.dumps(str(WEB / "i18n.js")), "effort": json.dumps(str(WEB / "effort.js"))},
                      encoding="utf-8")
    out = json.loads(subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8",
                                    check=True).stdout)
    assert out["asked"]
    assert out["chipEscape"] == {"calls": [], "chips": [["eff-chip", "low"]]}       # not applied, and shown so
    assert out["settingsEscape"] == {"calls": [], "draws": ["settings"]}            # not saved, the row drawn again
    assert out["chipYes"] == {"calls": [["chat_update", "c1", {"effort": "max"}]], "chips": [["eff-chip", "max"]],
                              "open": False}
    # the weekly digest: its own chooser, starting at the setting
    assert 'value="low" selected' in out["inline"] and 'data-act="eff-inline"' in out["inline"]
    assert out["inlineAsked"]
    assert out["inlineEscape"] == {"pick": "", "chips": [["eff-inline-dg", "low"]]}
    assert out["inlineMedium"] == {"pick": "medium", "chips": [["eff-inline-dg", "medium"]], "asked": False}


def test_app_closes_a_dialog_through_its_waiting_answer():
    js = (WEB / "app.js").read_text(encoding="utf-8")
    body = js[js.index("function closeModal()"):js.index("function onUpdate(")]
    assert "Studia.onModalClose" in body and "hook()" in body
