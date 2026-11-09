"""The Practice page (practice.js, run in node with a stand-in for app.js): a quiz practised one question at a time
(the intro says what it covers and left out, a wrong choice says why, a short answer is graded by the student, a
skip counts as missed, the score is saved and "Try 2 missed questions" runs only those), the flashcards session
(the answer, the four answers with when each brings the card back, a forgotten card again in the same sitting), the
course tab (quizzes, cards due, the stressed moments by week with the AI's ranked list), the sidebar's badge, the
Home note, and quiz and card events kept out of the study plans' handler."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from studia import i18n

WEB = Path(i18n.__file__).parent / "web"

SCRIPT = r"""
globalThis.window = globalThis;
const shown = { html: "" }, calls = [], toasts = [], went = [];
const modalEl = { classList: { add() {} }, querySelector: (sel) => {
  const cls = (sel.match(/\.([\w-]+)(?![^\[]*\])/g) || []).pop();
  return cls && shown.html.includes(cls.slice(1)) ? { click() {} } : null; } };
globalThis.document = { documentElement: {}, addEventListener() {}, querySelectorAll: () => [],
  querySelector: (sel) => (sel === "#overlay .modal" || sel === ".modal" ? (shown.html ? modalEl : null) : sel.startsWith("#overlay .") ? (shown.html.includes(sel.slice(10)) ? modalEl : null) : null),
  getElementById: () => null };
require(%(i18n)s);
const QUIZ = { id: "q1", course_id: "c1", title: "Bayes' theorem", status: "done", result: { title: "Bayes' theorem",
  coverage: { span: "weeks 5–6", weeks: [{ no: 5, topic: "Bayes" }], kinds: { mc: 1, short: 1, problem: 1 }, files: ["Week05.pdf"],
    lectures: ["2026-10-21"], focus: { name: "Quiz 2", week: 7 }, los: [{ no: 3, text: "Bayes", n: 2 }, { no: 4, text: "", n: 0 }],
    stressed_total: 4, stressed_hit: 3, materials_missing: ["Week05_Teacher"], days_missing: [], files_cut: [], lectures_cut: [],
    topics_missing: ["independence"], no_source: 0 },
  questions: [
    { n: 1, type: "mc", q: "What is $P(A|B)$?", options: ["P(A and B)/P(B)", "P(A)P(B)", "P(B)/P(A)", "1"], answer: 0, why: "The definition.",
      wrong: ["", "Only when independent.", "Upside down.", "Never."], source: { kind: "file", id: "r1", name: "Week05.pdf", page: 12 }, level: "easy" },
    { n: 2, type: "short", q: "Why the total probability rule?", model: "It gives the denominator.", points: ["the denominator"],
      source: { kind: "lecture", day_key: "c1|2026-10-21|lecture", date: "2026-10-21", part: 1, t: 845, ts: "14:05" } },
    { n: 3, type: "problem", q: "Find P(D|+).", steps: ["P(+) = 0.059", "P(D|+) = 0.16"], model: "0.16", source: null }] } };
const COURSE = { ok: true, deck: { total: 12, due: 2, new: 1, today: 3, learnt: 4, lecture: 10, files: 2, lecture_days: 3, weeks: [5, 6] },
  quizzes: [{ id: "q1", course_id: "c1", title: "Bayes' theorem", status: "done", weeks: [5, 6], days: [], focus: "Quiz 2", questions: 10,
              created_at: "2026-11-08T10:00:00", tries: 2, best: { score: 8, total: 10 }, last: { score: 6.5, total: 10, answers: [{ n: 1, got: 0 }, { n: 2, got: 1 }] } },
            { id: "q2", course_id: "c1", title: "", status: "running", progress: 0.3, message: "Writing 10 questions (Sonnet · Low)…", weeks: [6], days: [], focus: "", questions: 10 }],
  card_sets: [],
  stressed: { total: 2, counts: { exam: 1, stressed: 1, not_needed: 1 }, untranscribed: 1,
    weeks: [{ no: 6, topic: "Random variables", items: [{ id: "s2", kind: "exam", text: "I will ask this in the midterm", hl: [0, 10], day: "2026-10-28", day_key: "c1|2026-10-28|lecture", part: 1, t: 600, ts: "10:00", n: 2 }] },
            { no: 5, topic: "Bayes", items: [{ id: "s1", kind: "stressed", text: "Be careful with the denominator", hl: [0, 10], day: "2026-10-21", day_key: "c1|2026-10-21|lecture", part: 2, t: 300, ts: "05:00", n: 1 }] }],
    not_needed: [{ id: "s3", kind: "not_needed", text: "You are not responsible for the proof", hl: [0, 3], day: "2026-10-21", day_key: "c1|2026-10-21|lecture", part: 1, t: 50, ts: "00:50", n: 1, week: 5 }],
    summary: { items: [{ point: "Know Bayes' theorem's denominator.", why: "stressed", cites: ["s1"] }], model: "Claude Sonnet", at: "2026-11-09T10:00:00", new: 1 } } };
const ANSWERS = {
  quiz_get: () => ({ ok: true, quiz: QUIZ, attempts: [] }),
  quiz_attempt: (id, a) => ({ ok: true, attempt: a, attempts: [] }),
  practice_course: () => COURSE,
  practice_summary: () => ({ ok: true, due: 5, courses: [{ id: "c1", code: "MATH 240", deck: { total: 12, today: 3 }, quizzes: 1, last_try: null },
                                                           { id: "c2", code: "EEE 242", deck: { total: 4, today: 2 }, quizzes: 0, last_try: null }] }),
  cards_session: () => ({ ok: true, cards: [
    { id: "k1", q: "What is $P(A|B)$?", a: "P(A and B)/P(B)", source: { kind: "lecture", day_key: "d", date: "2026-10-21", part: 1, t: 60, ts: "01:00" }, stressed: true, state: null, next: { again: 60, hard: 330, good: 600, easy: 345600 } },
    { id: "k2", q: "Bayes?", a: "P(B|A)P(A)/P(B)", source: { kind: "file", id: "r1", name: "Week05.pdf", page: 3 }, stressed: false, state: { step: -1 }, next: { again: 600, hard: 86400 * 3, good: 86400 * 8, easy: 86400 * 60 } }] }),
  cards_grade: (cid, id, g) => ({ ok: true, state: { due: new Date(Date.now() + (g === "again" ? 60e3 : 86400e3)).toISOString().slice(0, 19), next: { again: 60, hard: 300, good: 600, easy: 86400 } } }),
};
const planEvents = [];
const S = { view: { name: "home" }, courses: [{ id: "c1", code: "MATH 240", display_name: "Probability" }], system: { ai: { available: true, provider: "claude", label: "Claude" } }, settings: {} };
window.Studia = { S, views: {}, onEvents: { plan: (e) => planEvents.push(e.id) }, onGo: {}, crumbs: {}, after: {}, mocks: {}, A: {},
  call: (name, ...args) => { calls.push([name, ...args]); return Promise.resolve(ANSWERS[name] ? ANSWERS[name](...args) : { ok: true }); },
  esc: (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])),
  I: (n) => `[${n}]`, SPIN: () => "~", toast: (m, level, act) => toasts.push([m, level, act && act.label]), modal: (h) => { shown.html = h; },
  closeModal: () => { shown.html = ""; }, go: (v) => went.push(v), renderView: () => {}, renderSidebar: () => {}, skeleton: () => "<skel>",
  mdToHtml: (s) => String(s), renderMath: () => {}, findCourse: (id) => S.courses.find((c) => c.id === id), hue: () => 200,
  aiName: () => "Claude", aiHint: () => "Set up AI", aiBtn: (ok) => (ok ? "" : "disabled"), isChatGPT: () => false, emptyHTML: (i, a) => a, fmtK: (n) => `${Math.round(n / 1000)}k`,
  openDay: (d) => { went.push({ day: d }); return Promise.resolve(); }, jumpTo: () => {}, menu: () => {}, ask: () => Promise.resolve(true),
  pack: { weekSpan: (w) => (w || []).map((n) => "week " + n).join(", ") } };
require(%(practice)s);
const St = window.Studia, A = St.A, tick = () => new Promise((r) => setTimeout(r, 5));
const out = {};
(async () => {
  // ---- a quiz, one question at a time
  A["qz-play"]({ dataset: { id: "q1" } }); await tick();
  out.intro = shown.html;
  A["qz-start"]();
  out.q1 = shown.html;
  A["qz-pick"]({ dataset: { i: "1" } });
  A["qz-check"]();
  out.q1checked = shown.html;
  A["qz-next"]();
  out.q2 = shown.html;
  A["qz-check"]();
  out.q2checked = shown.html;
  A["qz-self"]({ dataset: { got: "1" } });
  out.q3 = shown.html;
  A["qz-skip"]();
  await tick();
  out.end = shown.html;
  out.saved = calls.filter((c) => c[0] === "quiz_attempt").map((c) => c[2]);
  out.retryButton = (shown.html.match(/data-act="qz-retry"[^>]*data-only="([^"]*)"/) || [])[1];
  A["qz-retry"]({ dataset: { id: "q1", only: out.retryButton } }); await tick();
  out.retry = shown.html;
  // ---- the flashcards session
  shown.html = "";
  A["fc-study"]({ dataset: { cid: "c1" } }); await tick();
  out.card1 = shown.html;
  A["fc-flip"]();
  out.card1shown = shown.html;
  A["fc-grade"]({ dataset: { g: "again" } }); await tick();
  out.queue = St.S.practice.study.queue.map((c) => c.id);
  A["fc-flip"](); A["fc-grade"]({ dataset: { g: "good" } }); await tick();
  A["fc-flip"](); A["fc-grade"]({ dataset: { g: "good" } }); await tick();
  out.studyEnd = shown.html;
  out.grades = calls.filter((c) => c[0] === "cards_grade").map((c) => c[3]);
  // ---- the course tab, the sidebar, Home
  S.view = { name: "course", courseId: "c1", tab: "practice" };
  St.onGo.course(S.view); await tick();
  out.tab = St.practice.tab(S.courses[0]);
  await St.onGo.practice(); await tick();
  out.nav = St.navExtra({ name: "practice" });
  out.page = St.views.practice({ name: "practice" });
  out.home = St.practice.homeNote();
  out.tabCount = St.practice.tabCount("c1");
  // ---- events
  St.onEvents.plan({ id: "p1", kind: "pack", status: "done" });
  St.onEvents.plan({ id: "q2", kind: "quiz", course_id: "c1", status: "done", title: "Weeks 5-6" });
  out.planEvents = planEvents;
  out.toasts = toasts;
  process.stdout.write(JSON.stringify(out), () => process.exit(0));
})();
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_the_practice_page(tmp_path):
    script = tmp_path / "practice.js"
    script.write_text(SCRIPT % {"i18n": json.dumps(str(WEB / "i18n.js")), "practice": json.dumps(str(WEB / "practice.js"))},
                      encoding="utf-8")
    run = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8")
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    # the intro: what it covers and what it left out, worked out by the app
    assert "What it covers" in out["intro"] and "What it left out" in out["intro"]
    assert "Tests 3 of the 4 moments the lecturer stressed in these weeks" in out["intro"]
    assert "For Quiz 2: LO3 (2 questions), LO4 (0 questions)" in out["intro"] and "No question tests LO4" in out["intro"]
    assert "Files not on this PC: Week05_Teacher" in out["intro"] and "No question on: independence" in out["intro"]
    # a wrong choice: the right answer, why it is right and why the chosen one is wrong; then where it comes from
    assert "Question 1 of 3" in out["q1"] and 'data-act="qz-check" disabled' in out["q1"]
    assert "Not quite. The answer is A." in out["q1checked"] and "Why B is wrong:" in out["q1checked"]
    assert "Only when independent." in out["q1checked"] and "Why the other options are wrong" in out["q1checked"]
    assert "From Week05.pdf, page 12" in out["q1checked"] and 'data-act="pr-page"' in out["q1checked"]
    assert "qz-opt wrong" in out["q1checked"] and "qz-opt right" in out["q1checked"]
    # a short answer: the model answer, then the student grades it
    assert "qz-input" in out["q2"] and "A good answer" in out["q2checked"] and "I got it" in out["q2checked"]
    assert "From the lecture of " in out["q2checked"] and 'data-t="845"' in out["q2checked"]
    assert "Worked problem" in out["q3"] and "Show the solution" in out["q3"]
    # the score, saved; the missed ones again, without the intro
    assert "You got 1 of 3" in out["end"] and "Try 2 missed questions" in out["end"]
    assert out["saved"] == [{"answers": [{"n": 1, "got": 0, "chose": 1, "skipped": False}, {"n": 2, "got": 1, "chose": None, "skipped": False},
                                         {"n": 3, "got": 0, "chose": None, "skipped": True}], "only": [], "started_at": out["saved"][0]["started_at"]}]
    assert out["retryButton"] == "1,3"
    assert "Question 1 of 2" in out["retry"] and "What is $P(A|B)$?" in out["retry"]
    # flashcards: the answer, four answers with when each brings it back; a forgotten card comes back this sitting
    assert "Show the answer" in out["card1"] and "Stressed by the lecturer" in out["card1"] and "P(A and B)/P(B)" not in out["card1"]
    assert "P(A and B)/P(B)" in out["card1shown"] and ">Again</b><span>1 min</span>" in out["card1shown"]
    assert ">Good</b><span>10 min</span>" in out["card1shown"] and ">Easy</b><span>4 days</span>" in out["card1shown"]
    assert "From the lecture of " in out["card1shown"]
    assert out["queue"] == ["k1", "k2", "k1"]
    assert out["grades"] == ["again", "good", "good"] and "Done for today" in out["studyEnd"]
    # the course tab
    tab = out["tab"]
    assert "Practice quizzes" in tab and "Bayes&#39;" not in tab and "Bayes' theorem" in tab and "Best: 8 of 10" in tab
    assert "Writing 10 questions" in tab and 'data-act="plan-stop"' in tab
    assert "Study 3 cards" in tab and "From the flashcards of 3 lectures and from files (2 cards)." in tab
    assert tab.index("Week 6") < tab.index("Week 5") and "said 2 times" in tab and 'data-act="pr-moment"' in tab
    assert "Know this for the exam" in tab and "Know Bayes" in tab and "cite-chip" in tab
    assert "The lecturer said these are not needed" in tab and "<mark>You</mark> are not responsible for the proof" in tab
    assert "1 new moment since: sum it up again to add it." in tab and "1 lecture of yours isn't transcribed yet" in tab
    assert "Sum it up again" in tab
    # the sidebar, the Practice page, Home
    assert "Practice" in out["nav"] and '<span class="count new"' in out["nav"] and ">5</span>" in out["nav"]
    assert "MATH 240" in out["page"] and "EEE 242" in out["page"] and "5 flashcards to study today" in out["page"]
    assert "5 flashcards due today" in out["home"] and "MATH 240: 3 · EEE 242: 2" in out["home"]
    assert out["tabCount"] == 3
    # a study pack's event goes on to the plans; a quiz's stays here
    assert out["planEvents"] == ["p1"]
    assert ["Quiz ready: Weeks 5-6", "success", "Practise"] in out["toasts"]
