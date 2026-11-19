"""The Practice tab checked in the real app (4.2), on the page (practice.js with mistakes.js in node, a stand-in for
app.js; app.js itself for the lecture page's button): "Practise N cards" counts its run and always opens it (a run of
cards not due yet says it leaves their schedule alone, its answers keep it so, its end says this lecture, and the
lecture page counts again after it); a lecture whose cards are set aside says so; a card set aside comes back with
Undo; a missed question's card says it is one; a deck of missed questions says where its cards come from; a try saved
before 4.2 says its answer wasn't kept; and the styles that keep the answer buttons in view and the answer boxes
tall enough."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from studia import i18n

WEB = Path(i18n.__file__).parent / "web"
node = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")

PAGE = r"""
globalThis.window = globalThis;
const shown = { html: "" }, calls = [], toasts = [], menus = [], notes = { drawn: 0 };
const modalEl = { classList: { add() {} }, querySelector: () => null };
globalThis.document = { documentElement: {}, addEventListener() {}, querySelectorAll: () => [],
  querySelector: (sel) => (sel === "#overlay .modal" || sel === ".modal" ? (shown.html ? modalEl : null) : null), getElementById: () => null };
require(%(i18n)s);
const DAY = "c1|2026-10-07|lecture";
const card = (id, extra) => ({ id, q: "Front " + id, a: "Back " + id, origin: "lecture", source: { kind: "lecture", day_key: DAY, date: "2026-10-07", part: 1, t: 30, ts: "00:30" },
  state: { step: -1 }, next: {}, cram: true, ...extra });
let SESSION = { ok: true, cards: [card("k1"), card("k2")], total: 2, aside: 0, cram: true };
const ANSWERS = {
  cards_session: () => SESSION,
  cards_grade: (cid, id, g, cram) => ({ ok: true, state: { step: -1, due: new Date(Date.now() + 86400e3).toISOString().slice(0, 19), next: {}, cram: !!cram } }),
  cards_edit: () => ({ ok: true, card: {} }),
  practice_course: () => ({ ok: true, deck: {}, quizzes: [], card_sets: [], stressed: { total: 0, weeks: [], not_needed: [] }, mistakes: [] }),
  practice_summary: () => ({ ok: true, due: 0, courses: [] }),
};
const S = { view: { name: "day", dayKey: DAY }, courses: [{ id: "c1", code: "MATH 240", display_name: "Probability" }],
  system: { ai: { available: true, provider: "claude", label: "Claude" } }, settings: {},
  day: { day_key: DAY, notes: [{ kind: "flashcards", label: "Flashcards", md: "", cards: [{}, {}, {}], practise: 2 }] } };
window.Studia = { S, views: {}, onEvents: {}, onGo: {}, crumbs: {}, after: {}, mocks: {}, A: {},
  call: (name, ...args) => { calls.push([name, ...args]); return Promise.resolve(ANSWERS[name] ? ANSWERS[name](...args) : { ok: true }); },
  esc: (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])),
  I: (n) => `[${n}]`, SPIN: () => "~", toast: (m, level, act) => toasts.push([m, level, act && act.label, act && act.act, act && act.data]),
  modal: (h) => { shown.html = h; }, closeModal: () => { shown.html = ""; if (window.Studia.onModalClose) { const f = window.Studia.onModalClose; window.Studia.onModalClose = null; f(); } },
  go: () => {}, renderView: () => {}, renderSidebar: () => {}, renderNotes: () => { notes.drawn++; }, skeleton: () => "<skel>",
  mdToHtml: (s) => String(s), renderMath: () => {}, findCourse: (id) => S.courses.find((c) => c.id === id), hue: () => 200,
  aiName: () => "Claude", aiHint: () => "Set up AI", aiBtn: (ok) => (ok ? "" : "disabled"), isChatGPT: () => false, emptyHTML: (i, a) => a, fmtK: (n) => `${n}`,
  openDay: () => Promise.resolve(), jumpTo: () => {}, menu: (el, items) => menus.push(items), ask: () => Promise.resolve(true),
  pack: { weekSpan: (w) => (w || []).map((n) => "week " + n).join(", ") } };
require(%(practice)s);
require(%(mistakes)s);
const St = window.Studia, A = St.A, tick = () => new Promise((r) => setTimeout(r, 5)), P = () => St.S.practice;
const out = {};
(async () {
%(body)s
  process.stdout.write(JSON.stringify(out), () => process.exit(0));
})().catch((e) => { console.error(e); process.exit(1); });
""".replace("(async () {", "(async () => {")


def _page(tmp_path, body):
    script = tmp_path / "page.js"
    script.write_text(PAGE % {"i18n": json.dumps(str(WEB / "i18n.js")), "practice": json.dumps(str(WEB / "practice.js")),
                              "mistakes": json.dumps(str(WEB / "mistakes.js")), "body": body}, encoding="utf-8")
    r = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@node
def test_a_lecture_s_cards_open_as_a_run_that_leaves_their_schedule_alone(tmp_path):
    out = _page(tmp_path, """
  await St.practice.study("c1", { days: [DAY] }); await tick();
  out.first = shown.html;
  out.toast = toasts.slice(-1)[0];
  A["fc-flip"](); await A["fc-grade"]({ dataset: { g: "good" } }); await tick();
  A["fc-flip"](); await A["fc-grade"]({ dataset: { g: "again" } }); await tick();
  out.grades = calls.filter((c) => c[0] === "cards_grade").map((c) => c.slice(1));
  while (P().study && P().study.queue[P().study.pos]) { A["fc-flip"](); await A["fc-grade"]({ dataset: { g: "good" } }); await tick(); }
  out.end = shown.html;
  SESSION = { ...SESSION, cards: [card("k1", { cram: false, state: null, next: { good: 600 } })], cram: false };
  A["fc-close"](); await tick(); await tick();
  out.recount = calls.filter((c) => c[0] === "cards_session").slice(-1)[0];
  out.practise = S.day.notes[0].practise; out.drawn = notes.drawn;
  // none of its cards in the deck any more: set aside, said so; not in the deck at all, said so
  SESSION = { ok: true, cards: [], total: 0, aside: 3, cram: false };
  await St.practice.study("c1", { days: [DAY] }); out.aside = toasts.slice(-1)[0][0];
  SESSION = { ok: true, cards: [], total: 0, aside: 0, cram: false };
  await St.practice.study("c1", { days: [DAY] }); out.none = toasts.slice(-1)[0][0];
""")
    assert "MATH 240 · cram" in out["first"] and "Front k1" in out["first"]
    assert out["toast"][0].startswith("None of this lecture's cards is due yet")
    assert out["grades"][:2] == [["c1", "k1", "good", True], ["c1", "k2", "again", True]]       # (cram: schedule kept)
    assert "Done with this lecture's cards" in out["end"] and "doesn't change when your cards come back" in out["end"]
    assert out["recount"] == ["cards_session", "c1", [], [DAY_KEY]] and out["practise"] == 1 and out["drawn"] == 1
    assert out["aside"] == "This lecture's cards are set aside. Bring them back on the Practice tab."
    assert out["none"] == "This lecture's cards aren't in your deck yet."


DAY_KEY = "c1|2026-10-07|lecture"


@node
def test_a_card_set_aside_comes_back_with_undo_and_a_missed_question_says_so(tmp_path):
    out = _page(tmp_path, """
  SESSION = { ok: true, cards: [card("q1", { origin: "quiz", cram: false, state: null, next: { good: 600 },
    quiz: { plan_id: "p1", n: 2, type: "short", title: "Bayes basics", model: "It renormalises." } }), card("k2", { cram: false, state: null, next: {} })], total: 2, aside: 0 };
  await St.practice.study("c1"); await tick();
  out.kind = shown.html;
  A["fc-card-menu"]({}); const items = menus.slice(-1)[0];
  out.items = items.map((x) => (x === "-" ? "-" : x.id));
  await items.find((x) => x.id === "aside").run(); await tick();
  out.toast = toasts.slice(-1)[0];
  out.queue = P().study.queue.map((c) => c.id);
  await A["fc-unaside"]({ dataset: out.toast[4] }); await tick();
  out.edits = calls.filter((c) => c[0] === "cards_edit").map((c) => c.slice(1));
  out.back = toasts.slice(-1)[0][0];
""")
    assert "A question you missed · Bayes basics" in out["kind"]
    assert out["items"] == ["edit", "aside", "-", "del"]
    assert out["toast"][:4] == ["Set aside. You can bring it back from the Flashcards card.", "success", "Undo", "fc-unaside"]
    assert out["toast"][4] == {"cid": "c1", "id": "q1"} and out["queue"] == ["k2"]
    assert out["edits"] == [["c1", "q1", {"off": "aside"}], ["c1", "q1", {"off": ""}]]
    assert out["back"] == "It's back in your deck."


@node
def test_the_flashcards_card_says_where_its_cards_come_from_and_an_old_try_s_answer(tmp_path):
    out = _page(tmp_path, """
  const c = S.courses[0];
  const tab = (deck, mistakes) => { P().course.c1 = { deck, quizzes: [], card_sets: [], stressed: { total: 0, weeks: [], not_needed: [] }, mistakes: mistakes || [] };
    S.view = { name: "course", courseId: "c1", tab: "practice" }; return St.practice.tab(c); };
  out.quizOnly = tab({ total: 4, today: 4, quiz: 4, lecture_days: 0, files: 0, weeks: [3] });
  out.mixed = tab({ total: 16, today: 2, quiz: 4, lecture_days: 1, files: 0, learnt: 2, weeks: [3] });
  out.lectures = tab({ total: 12, today: 2, quiz: 0, lecture_days: 1, files: 0, weeks: [3] });
  const q = { n: 2, type: "short", q: "What does conditioning do?", model: "Restricts it.", points: [], source: null };
  const row = (answer) => St.mistakes.row({ cid: "c1", plan: "p1" }, q, answer);
  St.mistakes.state.open.add("p1|2");
  out.old = row({ n: 2, got: 0, chose: null, skipped: false });
  out.empty = row({ n: 2, got: 0, chose: null, skipped: false, text: "", mark: "self" });
  out.typed = row({ n: 2, got: 0.5, text: "it shrinks the space", mark: "self" });
""")
    assert "Questions you missed in your quizzes. A few minutes a day is enough." in out["quizOnly"]
    assert "Made from files you picked" not in out["quizOnly"]
    assert ("From the flashcards of 1 lecture. A few minutes a day is enough. 4 cards are questions you missed in quizzes."
            in out["mixed"])
    assert "questions you missed" not in out["lectures"]
    assert "Not kept: this try is from an older version of IEUStudia." in out["old"]
    assert "You didn&#39;t write an answer." in out["empty"] or "You didn't write an answer." in out["empty"]
    assert "it shrinks the space" in out["typed"] and "older version" not in out["typed"]


@node
def test_the_lecture_page_counts_the_cards_its_run_holds(tmp_path):
    harness = (Path(__file__).parent / "test_quizme_page.py").read_text(encoding="utf-8")
    start, end = harness.index('HARNESS = """') + len('HARNESS = """'), harness.index('"""', harness.index('HARNESS = """') + 14)
    page = harness[start:end]
    body = """
  const day = { day_key: "c1|2026-10-13|lecture", title: "MATH 240 · Lecture", date_label: "13 October", subtitle: "", course: { id: "c1", code: "MATH 240", name: "Probability" },
    kind: "lecture", languages: { en: 1 }, highlights: [], marks: [], files: {}, screens_again: { current: null, waiting: [] },
    notes: [{ kind: "flashcards", label: "Flashcards", md: "Q: a\\nA: b", cards: [{ q: "a", a: "b" }, { q: "c", a: "d" }], practise: 5 }],
    parts: [{ index: 1, id: "a", title: "Part 1", time_range: "11:00–11:15", viewer_url: "https://v/a", status: "done", slides: 0, warnings: [], anomalies: [], paragraphs: [] }] };
  S.view = { name: "day", dayKey: day.day_key, from: { name: "home" } }; S.day = day; S.dayFind = "";
  const label = () => { St.renderView(); const h = els["#view"].innerHTML; const m = h.match(/data-act="cards-practise"[^>]*>[^<]*(?:<[^>]*>[^<]*)*?(Practise \\d+ cards?)/); return m ? m[1] : null; };
  out.five = label();
  day.notes[0].practise = 1; out.one = label();
  day.notes[0].practise = 0; out.none = label();
  delete day.notes[0].practise; out.fallback = label();
  day.notes[0].practise = -1; out.failed = label();
  out.exported = typeof St.renderNotes === "function";
"""
    script = tmp_path / "page.js"
    script.write_text(page % {"web": json.dumps(WEB.as_posix()), "body": body}, encoding="utf-8")
    r = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out == {"five": "Practise 5 cards", "one": "Practise 1 card", "none": None, "fallback": "Practise 2 cards",
                   "failed": "Practise 2 cards", "exported": True}


def _rule(css, selector):
    m = re.search(re.escape(selector) + r"[^{]*\{([^}]*)\}", css)
    return m.group(1) if m else ""


def test_the_answer_buttons_stay_in_view_and_the_answer_boxes_are_tall_enough():
    practice = (WEB / "practice.css").read_text(encoding="utf-8")
    mistakes = (WEB / "mistakes.css").read_text(encoding="utf-8")
    export = (WEB / "export.css").read_text(encoding="utf-8")
    sticky = _rule(practice, ".modal > .qz > .actions")
    assert "position: sticky" in sticky and "bottom: -22px" in sticky and "background: var(--surface)" in sticky
    assert ".modal > .fc-study > .actions" in practice
    assert "height: auto" in _rule(practice, ".qz-input") and "padding: 8px 12px" in _rule(practice, ".qz-input")
    assert "height: auto" in _rule(mistakes, ".mq-ed") and "padding: 8px 12px" in _rule(export, ".kit-text")
    # the end screen's own rows only: an opened row's steps and points keep their list look
    assert ".qz-results > li {" in practice and not re.search(r"\.qz-results li[ .{]", practice)
    assert "grid-column: 1 / -1" in _rule(mistakes, ".modal.qz-dlg .cp-modes.four > .cp-mode:last-child:nth-child(odd)")
