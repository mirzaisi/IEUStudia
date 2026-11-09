"""Flashcards on the Practice page (practice.js in node, with a stand-in for app.js) near a study day's start: a card
the student has just learnt never comes back in the same sitting, even when its day starts at 04:00 a few minutes
later (Good again would space it out twice); a forgotten card and a learning step still do."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from studia import i18n

WEB = Path(i18n.__file__).parent / "web"

SCRIPT = r"""
globalThis.window = globalThis;
const shown = { html: "" };
const modalEl = { classList: { add() {} }, querySelector: () => null };
globalThis.document = { documentElement: {}, addEventListener() {}, querySelectorAll: () => [], getElementById: () => null,
  querySelector: (sel) => (shown.html && (sel === ".modal" || sel === "#overlay .modal") ? modalEl : null) };
require(%(i18n)s);
const p2 = (n) => String(n).padStart(2, "0");
const local = (min) => {                     // a due time as Python writes it: local, no zone
  const d = new Date(Date.now() + min * 60e3);
  return `${d.getFullYear()}-${p2(d.getMonth() + 1)}-${p2(d.getDate())}T${p2(d.getHours())}:${p2(d.getMinutes())}:${p2(d.getSeconds())}`;
};
const card = (id) => ({ id, q: "Question " + id, a: "Answer " + id, source: null, stressed: false, state: null,
  next: { again: 60, hard: 330, good: 600, easy: 345600 } });
const GRADED = {
  k1: { step: -1, interval: 1, due: local(10) },     // learnt at 03:50: its study day starts at 04:00
  k2: { step: 0, interval: 0, due: local(1) },       // forgotten: a few cards later
  k3: { step: 1, interval: 0, due: local(10) },      // a learning step: at the end
};
const S = { view: { name: "home" }, courses: [{ id: "c1", code: "MATH 240" }], system: { ai: { available: true } }, settings: {} };
window.Studia = { S, views: {}, onEvents: {}, onGo: {}, crumbs: {}, after: {}, mocks: {}, A: {},
  call: (name, cid, id, g) => Promise.resolve(name === "cards_session" ? { ok: true, cards: [card("k1"), card("k2"), card("k3")] }
    : name === "cards_grade" ? { ok: true, state: { ...GRADED[id], next: { again: 60, hard: 330, good: 600, easy: 86400 } } } : { ok: true }),
  esc: (s) => String(s ?? ""), I: (n) => `[${n}]`, SPIN: () => "~", toast: () => {}, modal: (h) => { shown.html = h; },
  closeModal: () => { shown.html = ""; }, go: () => {}, renderView: () => {}, renderSidebar: () => {}, skeleton: () => "",
  mdToHtml: (s) => String(s), renderMath: () => {}, findCourse: (id) => S.courses.find((c) => c.id === id) };
require(%(practice)s);
const A = window.Studia.A, tick = () => new Promise((r) => setTimeout(r, 5));
(async () => {
  A["fc-study"]({ dataset: { cid: "c1" } }); await tick();
  for (const g of ["good", "again", "good"]) { A["fc-flip"](); A["fc-grade"]({ dataset: { g } }); await tick(); }
  const out = { queue: window.Studia.S.practice.study.queue.map((c) => c.id) };
  process.stdout.write(JSON.stringify(out), () => process.exit(0));
})();
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_a_learnt_card_waits_for_its_study_day(tmp_path):
    script = tmp_path / "study.js"
    script.write_text(SCRIPT % {"i18n": json.dumps(str(WEB / "i18n.js")), "practice": json.dumps(str(WEB / "practice.js"))},
                      encoding="utf-8")
    run = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    assert out["queue"] == ["k1", "k2", "k3", "k2", "k3"]           # k1 (learnt) is not shown again in this sitting
