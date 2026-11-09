"""The new-chat dialog (app.js run in node against the browser preview's stand-ins): only the text of the chosen days
can make a chat too big (the whole course is read in parts, the slide pictures get the room the text leaves), so
Start chat starts what the dialog allows and says why when it can't; the first question's size is the app's own
count once that comes."""

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
const el = () => ({ innerHTML: "", style: {}, dataset: {}, classList: { toggle() {}, add() {}, remove() {}, contains: () => false },
  scrollTo() {}, querySelector: () => null, querySelectorAll: () => [], addEventListener() {}, remove() {}, focus() {} });
const els = { "#overlay": el(), "#cp-cost-line": el(), "#eff-note-cp": el(), "#toasts": { appendChild: (b) => toasts.push(b) } };
globalThis.document = new Proxy({}, { get: (t, k) => k === "compatMode" ? "CSS1Compat"
  : k === "createElement" ? () => el()
  : k === "querySelector" ? (s) => els[s] || stub()
  : k === "querySelectorAll" ? () => []
  : k === "getElementById" ? (id) => els["#" + id] || null
  : k === "addEventListener" ? () => {} : stub() });
globalThis.localStorage = { getItem: () => null, setItem() {} };
globalThis.navigator = { userAgent: "node", platform: "Win32" };
globalThis.CSS = { escape: (s) => s };
globalThis.addEventListener = () => {}; globalThis.matchMedia = () => ({ matches: false, addEventListener() {} });
globalThis.requestAnimationFrame = (f) => f();
const later = setTimeout;
globalThis.setInterval = () => 0; globalThis.setTimeout = () => 0;      // no boot, no polling
for (const f of ["i18n.js", "icons.js", "app.js", "effort.js", "notices.js", "chatfiles.js"]) require(web + "/" + f);
globalThis.setTimeout = later;                                        // the dialog asks for its estimate a moment later
const St = globalThis.Studia, S = St.S, M = St.Mock;
const tick = (ms = 30) => new Promise((r) => later(r, ms));
const plain = (h) => String(h).replace(/<[^>]*>/g, "").replace(/&#39;/g, "'").replace(/&amp;/g, "&").replace(/\\s+/g, " ").trim();
const told = () => toasts.splice(0).map((b) => b.className.replace("toast ", "") + ": " + plain(b.innerHTML));
// MATH 240 at exam time: 7 class days, 646 slides captured
const DAYS = [...Array(7)].map((_, i) => ({ day_key: `17933|d${i}|lecture`, date: `2026-10-${10 + i}`, label: `Day ${i + 1}`,
  kind: "lecture", kind_label: "Lecture", parts: 3, stars: 2, slides: i ? 92 : 94, tokens: 17465, hl_tokens: 1500,
  start: `2026-10-${16 - i}T08:30:00` }));
let limit = 121600, asked = [];
M.chat_courses = () => ({ ok: true, courses: [{ id: "17933", code: "MATH 240", name: "Probability for Engineers", days: 7 }], limits: {} });
M.chat_scope = (cid) => ({ ok: true, course: { id: cid, code: "MATH 240", name: "Probability" }, days: DAYS, max_tokens: limit, max_slides: 400, limits: {} });
// the app: what doesn't fit whole is read in parts, the pictures get the room the text leaves
M.chat_estimate = (cid, days, focus, slides) => { asked.push([focus, days.length, slides]);
  return { ok: true, tokens: focus === "course" ? 114582 : 3000 + days.length * 17465 + (slides ? 40000 : 0), images: 0, slides_total: 646,
    days: days.length, read: { on: focus === "course", auto: true, pages: 0, total: 0 }, max_tokens: limit }; };
const dialog = () => els["#overlay"].innerHTML;
// the dialog as drawn; its cost line as the app's count paints it in place (its own element here)
const state = () => { const h = dialog(), go = (h.match(/<button[^>]*data-act="cp-go"[^>]*>/) || [""])[0];
  return { open: !!h, tooBig: h.includes("more than one chat can hold"), startable: !!go && !go.includes("disabled"),
    cost: plain((h.match(/<div id="cp-cost-line">(.*?)<\\/div>/) || ["", ""])[1]), slides: plain((h.match(/\\d+ slides? · formulas and figures · [^<]*/) || [""])[0]),
    painted: plain(els["#cp-cost-line"].innerHTML) }; };
const out = {};
(async () => {
  S.mode = "app"; S.view = { name: "home" }; S.boot = {}; S.courses = [];
  S.system = { ...S.system, ai: { available: true, provider: "claude", can_images: true } };
%(body)s
  process.stdout.write(JSON.stringify(out), () => process.exit(0));      // after the output is out (Linux pipes)
})().catch((e) => { console.error(e); process.exit(1); });
"""


def run(tmp_path, body):
    script = tmp_path / "picker.js"
    script.write_text(HARNESS % {"web": json.dumps(WEB.as_posix()), "body": body}, encoding="utf-8")
    r = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


node = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")


@node
def test_the_whole_course_starts_even_when_its_transcripts_are_more_than_one_request(tmp_path):
    """The case 3.3.0 promised: a long course's whole transcripts (125k) on a 121.6k window are read in parts first.
    Start chat was enabled but did nothing (go() still checked the page's own sum)."""
    out = run(tmp_path, """
  St.chatPicker("17933"); await tick();
  window.__cp.focus("course"); out.before = state(); await tick(400);
  out.counted = state(); out.asked = asked.slice(-1)[0];
  window.__cp.model("sonnet"); out.redrawn = state();                 // drawn again, the same choice: the app's count
  window.__cp.slides(true); await tick(400);
  out.slides = state();
  window.__cp.go(); await tick();
  out.toasts = told(); out.closed = !dialog();
  out.draft = S.chat.draft && { focus: S.chat.draft.focus, days: S.chat.draft.days.length, slides: S.chat.draft.slides };
""")
    before, counted = out["before"], out["counted"]
    assert not before["tooBig"] and before["startable"]
    assert "≈122k tokens" in before["cost"]               # (at most the window: the rest is read in parts)
    assert out["asked"] == ["course", 7, False]
    assert "≈115k tokens" in counted["painted"] and counted["startable"]     # the app's own count, in place
    assert "≈115k tokens" in out["redrawn"]["cost"]
    assert not out["slides"]["tooBig"] and out["slides"]["startable"]       # slides on: still fits
    assert out["toasts"] == [] and out["closed"]
    assert out["draft"] == {"focus": "course", "days": 7, "slides": True}


@node
def test_slides_never_make_chosen_days_too_big(tmp_path):
    """5 days with slides on a 380k window: 90k of text plus up to 400 pictures of 800 tokens summed to 410k and was
    refused, though the app sends only the pictures that fit."""
    out = run(tmp_path, """
  limit = 380000;
  St.chatPicker("17933"); await tick();
  window.__cp.none(); for (const d of DAYS.slice(0, 5)) window.__cp.day(d.day_key, true);
  window.__cp.slides(true); out.before = state(); await tick(400);
  out.counted = state();
  window.__cp.go(); await tick();
  out.toasts = told();
  out.draft = S.chat.draft && { focus: S.chat.draft.focus, days: S.chat.draft.days.length, slides: S.chat.draft.slides };
""")
    before = out["before"]
    assert not before["tooBig"] and before["startable"]
    assert "≈380k tokens" in before["cost"]                       # 90k of text, the pictures in the 290k left
    assert before["slides"] == "400 slides · formulas and figures · ≈290k more tokens"
    assert "≈130k tokens" in out["counted"]["painted"]            # then the app's own count
    assert out["toasts"] == [] and out["draft"] == {"focus": "full", "days": 5, "slides": True}


@node
def test_days_whose_text_is_too_much_are_refused_and_start_chat_says_why(tmp_path):
    out = run(tmp_path, """
  limit = 60000;
  St.chatPicker("17933"); await tick();
  window.__cp.all(); out.all = state();
  window.__cp.go(); await tick();
  out.toasts = told(); out.draft = !!S.chat.draft; out.open = !!dialog();
  window.__cp.none(); window.__cp.go(); await tick();
  out.none = told();
  window.__cp.focus("highlights"); window.__cp.all(); out.highlights = state();
""")
    assert out["all"]["tooBig"] and not out["all"]["startable"]
    assert out["toasts"] == ["error: That is more than one chat can hold (≈60.0k tokens). Pick fewer days, or Highlights only."]
    assert out["draft"] is False and out["open"]
    assert out["none"] == ["info: Pick at least one transcribed class day."]
    assert not out["highlights"]["tooBig"] and out["highlights"]["startable"]      # the way out it names works
