"""The Exam room on the page (4.2.0, item 2; examroom.js with app.js, course.js and practice.js in node): a final
known only from the syllabus has no sources and still gets its room, every way in that opened Get ready opens it
("ready-open", St.openReady, a notification's "ready:"), the course page's top has a "Quiz 3 in 4 days" chip,
readiness is three counts (never one percentage), Get everything, the weeks and the day can be changed, its buttons
open the existing tools set to its weeks (the quiz dialog on this assessment, a cram session that leaves the schedule
alone), the read ticks go to the library, and it prints."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from studia import i18n

WEB = Path(i18n.__file__).parent / "web"
node = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")

HARNESS = """
const web = %(web)s;
const stub = () => new Proxy(function () {}, { get: (t, k) => k === Symbol.toPrimitive ? () => "" : k === "length" ? 0 : stub(),
  apply: () => stub(), construct: () => stub() });
globalThis.window = globalThis;
const toasts = [];
const el = () => ({ innerHTML: "", style: {}, dataset: {}, classList: { toggle() {}, add() {}, remove() {}, contains: () => false }, scrollTo() {},
  querySelector: () => null, querySelectorAll: () => [], addEventListener() {}, remove() {}, focus() {}, contains: () => false, scrollTop: 0 });
const els = { "#top": el(), "#view": el(), "#overlay": el(), "#toasts": { appendChild: (b) => toasts.push(b.innerHTML) } };
globalThis.document = new Proxy({}, { get: (t, k) => k === "compatMode" ? "CSS1Compat" : k === "activeElement" ? null : k === "hidden" ? false
  : k === "createElement" ? () => ({ ...el(), querySelector: () => el() })
  : k === "querySelector" ? (s) => (s in els ? els[s] : stub())
  : k === "getElementById" ? (id) => els["#" + id] || null : k === "querySelectorAll" ? () => []
  : k === "addEventListener" ? () => {} : stub() });
globalThis.localStorage = { getItem: () => null, setItem() {} };
globalThis.navigator = { userAgent: "node", platform: "Win32" };
globalThis.location = { search: "", hash: "", href: "" };
globalThis.CSS = { escape: (s) => s };
globalThis.addEventListener = () => {}; globalThis.matchMedia = () => ({ matches: false, addEventListener() {} });
globalThis.requestAnimationFrame = (f) => f();
let printed = 0; globalThis.print = () => { printed++; };
const later = setTimeout;
globalThis.setInterval = () => 0; globalThis.setTimeout = () => 0;
for (const f of ["i18n.js", "match.js", "icons.js", "app.js", "effort.js", "course.js", "plans.js", "studypack.js", "picks.js",
  "calendar.js", "digest.js", "chatactions.js", "practice.js", "mistakes.js", "examroom.js"]) require(web + "/" + f);
const St = globalThis.Studia, S = St.S, A = St.A;
St.aiName = () => "Claude"; St.sq = { card: () => "", line: () => "" };
const tick = (ms = 30) => new Promise((r) => later(r, ms));
const text = (h) => String(h).replace(/<[^>]*>/g, " ").replace(/&#39;/g, "'").replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&").replace(/\\s+/g, " ").trim();
const view = () => els["#view"].innerHTML, overlay = () => els["#overlay"].innerHTML;
const calls = [];
const spy = (name, answer) => { St.mocks[name] = (...a) => { calls.push([name, ...a]); return typeof answer === "function" ? answer(...a) : answer; }; };
const called = (name) => calls.filter((c) => c[0] === name).map((c) => c.slice(1));
const pad = (n) => String(n).padStart(2, "0");
const inDays = (n) => { const d = new Date(); d.setDate(d.getDate() + n); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; };
const DAY = "c|2026-11-16|lecture", DAY2 = "c|2026-11-18|lecture";
const QUIZ3 = { key: "quiz:3", kind: "quiz", no: 3, name: "Quiz 3", week: 12, weeks: [9, 10], weeks_from: "announced", guessed: [9, 10, 11, 12], covers: [9, 10],
  covers_from: "a2", ahead: true, files: ["ann1"], sources: [{ id: "a2", title: "About Quiz 3" }], syllabus: false, los: [] };
const FINAL = { key: "syllabus:Final Exam:16", kind: "final", no: 0, name: "Final Exam", week: 16, los: [], weeks: Array.from({ length: 16 }, (_, i) => i + 1),
  weeks_from: "guessed", guessed: Array.from({ length: 16 }, (_, i) => i + 1), covers: [], covers_from: "", ahead: true, files: [], sources: [], syllabus: true };
const WEEKS = Array.from({ length: 16 }, (_, i) => ({ no: i + 1, start: "", end: "", topic: "Topic " + (i + 1), current: i === 9, past: i < 9, exam: i === 15 }));
function room(a, extra = {}) {
  return { course: { id: "c", code: "MATH 240", name: "Probability" },
    assessment: { ...a, los: a.key === "quiz:3" ? [{ no: 2, en: "use Bayes", tr: "" }] : [],
      origin: a.weeks_from === "announced" ? { from: "announced", since: "Midterm", announcement: { id: "a2", title: "About Quiz 3" } } : { from: a.weeks_from, since: "", announcement: null } },
    when: a.syllabus ? { date: inDays(31), time: "", from: "week", others: [], days: 31 } : { date: inDays(3), time: "", from: "class", others: [inDays(5)], days: 3 },
    share: a.syllabus ? { activity: "Final Exam", weight: 40, number: 1, each: 40 } : { activity: "Quizzes / Studio Critiques", weight: 20, number: 4, each: 5 },
    weeks: WEEKS,
    material: { days: [{ day_key: DAY, date: "2026-11-16", label: "", kind_label: "Lecture", week: 10, transcribed: true, any_done: true, live: false, busy: false, read: true },
      { day_key: DAY2, date: "2026-11-18", label: "", kind_label: "Lecture", week: 10, transcribed: false, any_done: false, live: false, busy: false, read: false }],
      files: [{ id: "w9", title: "Week09", name: "Week09.pdf", ext: "pdf", downloaded: true, week: 9, tied: false },
        { id: "w10", title: "Week10", name: "Week10.pdf", ext: "pdf", downloaded: false, week: 10, tied: false },
        { id: "ann1", title: "Quiz3 - Solutions", name: "Quiz3 - Solutions.pdf", ext: "pdf", downloaded: false, week: null, tied: true }],
      lectures: 2, silent: 1, transcribed: 1, busy: 0, readable: 1, read: 1, downloaded: 1, todo: [DAY2], missing: ["w10", "ann1"] },
    said: [{ source: "announcement", announcement_id: "a2", title: "About Quiz 3", text: "Quiz 3 will cover weeks 9 and 10.", hl: [0, 6], date: "2026-11-02T09:00", categories: ["quiz"] }],
    stressed: [{ id: "s1", kind: "exam", text: "This will be on the quiz: the total probability rule.", hl: [0, 4], day_key: DAY, day: "2026-11-16", part: 1, t: 610, ts: "10:10", n: 2, score: 3, week: 10 }],
    practice: { quizzes: [{ id: "q3", course_id: "c", title: "Bayes", status: "done", weeks: [9, 10], days: [], focus: "Quiz 3", questions: 10, tries: 1,
      last: { score: 6, total: 10 }, best: { score: 6, total: 10 } }], answers: 10, tried: 1,
      cards: { total: 12, due: 3, new: 9, learnt: 0, reviews: 4 },
      mastery: { los: [{ no: 2, text: "use Bayes", tried: 4, right: 1, questions: 4, quizzes: 1, share: 0.25, status: "shaky", missed: [{ plan_id: "q3", n: 2 }] }], topics: [] },
      mistakes: [{ plan_id: "q3", n: 2, title: "Bayes", at: "2026-11-17T10:00:00", misses: 1, q: { n: 2, type: "mc", q: "Bayes?", options: ["a", "b", "c", "d"], answer: 0, wrong: ["", "x", "y", "z"] },
        answer: { n: 2, got: 0, chose: 1 }, card_id: "k", due: "" }], missed: 1 },
    condensed: [{ id: "s1", kind: "sheet", status: "done", title: "Weeks 9–10", weeks: [9, 10], focus: "", created_at: "" }],
    counts: { material: { have: 2, of: 5 }, practised: { answers: 10, quizzes: 1, reviews: 4 }, condensed: { done: 1, of: 1 } },
    others: [], ...extra };
}
spy("exam_room", (cid, key) => (key === "Quiz 3" || key === "quiz:3" ? { ok: true, room: room(QUIZ3) }
  : key === "Final Exam" || key === FINAL.key ? { ok: true, room: room(FINAL) } : { ok: false, gone: true, error: "This assessment is no longer in the course's plan." }));
spy("exam_set_weeks", (cid, key, weeks) => ({ ok: true, room: room({ ...QUIZ3, weeks, weeks_from: "yours" }) }));
spy("exam_set_day", (cid, key, day) => ({ ok: true, room: room(QUIZ3, { when: { date: day, time: "", from: "yours", others: [], days: 5 } }) }));
spy("exam_get_everything", { ok: true, lectures: 1, parts: 2, files: 2, unknown: 0, needs_section: false });
spy("reads_set", { ok: true, changed: 1 });
spy("reads_import", { ok: true, added: 0 });
spy("course_detail", { ok: false });
const out = {};
(async () => {
  S.mode = "app"; S.view = { name: "home" }; S.boot = {}; S.settings = {}; S.system.ai = { available: true, provider: "claude", can_plans: true };
  S.courses = [{ id: "c", code: "MATH 240", name: "Probability", display_name: "Probability", days: [], counts: { done: 1, total: 2, new: 0 }, content: {}, available_sections: [] }];
%(body)s
  process.stdout.write(JSON.stringify(out), () => process.exit(0));
})().catch((e) => { console.error(e); process.exit(1); });
"""


def run(tmp_path, body):
    script = tmp_path / "page.js"
    script.write_text(HARNESS % {"web": json.dumps(WEB.as_posix()), "body": body}, encoding="utf-8")
    r = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@node
def test_a_final_known_only_from_the_syllabus_gets_its_room_without_sources(tmp_path):
    out = run(tmp_path, """
  A["ready-open"]({ dataset: { cid: "c", name: "Final Exam" } }); await tick();
  out.view = S.view; out.html = view(); out.text = text(view());
""")
    assert out["view"] == {"name": "exam", "courseId": "c", "key": "syllabus:Final Exam:16"}     # its key, once loaded
    t = out["text"]
    assert "Exam room · MATH 240" in t and "Final Exam" in t and "40% of your grade" in t
    assert "Covers weeks 1–16 · a guess: every week up to it" in t
    assert "its week starts then: the exam schedule on IEULearn has the day" in t and "in 31 days" in t
    assert 'data-act="plan-ann"' not in out["html"].split('class="card er-head"')[1].split("</div>")[0]   # no announcement


@node
def test_every_way_in_that_opened_get_ready_opens_the_room(tmp_path):
    out = run(tmp_path, """
  St.openReady("c", "Quiz 3"); await tick();
  out.alias = [S.view.name, S.view.key];
  S.view = { name: "home" };
  St.openTarget("ready:c:Quiz 3"); await tick();                       // a notification
  out.target = [S.view.name, S.view.key];
  A["er-back"](); out.back = S.view.name;
  out.asked = called("exam_room").map((c) => c[1]);
  St.openReady("c", "Quiz 9"); await tick();
  out.gone = text(view());
  // the course page: the pills of its plan, the syllabus's exam week, and the chip at its top
  S.cd = { c: { id: "c", code: "MATH 240", general: [], outcomes_en: [], outcomes_tr: [], grading: [], syllabus: {}, materials: { total: 0, downloaded: 0, new: 0 },
    announcements: [], counts: {}, weeks: [{ no: 12, start: inDays(-2), end: inDays(4), current: true, past: false, exam: false, topic_en: "Bayes", topic_tr: "", units: [], days: [], classes: [] },
      { no: 16, start: inDays(30), end: inDays(36), current: false, past: false, exam: true, exam_label: "Final Exam", topic_en: "Final Exam", topic_tr: "", units: [], days: [], classes: [] }],
    alerts: { items: [], counts: {}, plan: [{ kind: "quiz", no: 3, name: "Quiz 3", week: 12, los: [], sources: [{ id: "a2", title: "About Quiz 3" }] }] },
    assessments: [{ ...QUIZ3, on: { date: inDays(4), time: "", from: "class", others: [] } }, { ...FINAL, on: { date: inDays(30), time: "", from: "week", others: [] } }] } };
  S.view = { name: "course", courseId: "c" };
  const page = St.views.course(S.view);
  out.chip = (page.match(/<button class="stat er-chip[^>]*>.*?<\\/button>/s) || [""])[0];
  out.pills = [...page.matchAll(/data-act="ready-open" data-cid="c" data-name="([^"]+)"/g)].map((m) => m[1]);
  out.examPill = page.includes('data-act="ready-open" data-cid="c" data-key="syllabus:Final Exam:16" data-name="Final Exam"');
  out.near = St.examroom.chip(S.courses[0], { assessments: [{ ...QUIZ3, on: { date: inDays(1), from: "class" } }] });
  out.far = St.examroom.chip(S.courses[0], { assessments: [{ ...QUIZ3, on: { date: inDays(40), from: "class" } }] });
  out.none = St.examroom.chip(S.courses[0], { assessments: [{ ...QUIZ3, on: { date: inDays(-1), from: "class" } }] });
""")
    assert out["alias"] == ["exam", "quiz:3"] and out["target"] == ["exam", "quiz:3"] and out["back"] == "home"
    assert out["asked"] == ["Quiz 3", "Quiz 3"]
    assert "This assessment is no longer in the course's plan." in out["gone"]
    assert 'data-key="quiz:3"' in out["chip"] and 'data-act="ready-open"' in out["chip"] and "Quiz 3 in 4 days" in out["chip"]
    assert "Quiz 3" in out["pills"] and out["examPill"]
    assert "Quiz 3 tomorrow" in out["near"] and "soon" in out["near"] and out["far"] == "" and out["none"] == ""


@node
def test_readiness_is_three_counts_and_get_everything_fetches_what_is_missing(tmp_path):
    out = run(tmp_path, """
  A["ready-open"]({ dataset: { cid: "c", key: "quiz:3" } }); await tick();
  out.html = view(); out.text = text(view());
  await A["er-get"]({ disabled: false }); await tick();
  out.got = called("exam_get_everything"); out.toast = toasts.slice(-1)[0] || ""; out.mat = Object.keys(S.mat).sort();
  out.after = text(view());
  St.mocks.exam_room = () => ({ ok: true, room: room(QUIZ3, { material: { ...room(QUIZ3).material, todo: [], missing: [] } }) });
  await St.examroom.load("c", "quiz:3", true); out.ready = view();
""")
    t, h = out["text"], out["html"]
    assert "Material 2 of 5 lectures transcribed and files on this PC" in t
    assert "Practised 10 questions answered in 1 quiz · 4 flashcard answers" in t
    assert "Condensed 1 sheet ready" in t and "%" not in t.split("Material")[1].split("Still missing")[0]   # no percentage
    assert "Still missing: 1 lecture to transcribe · 2 files to download" in t
    assert re.search(r'class="btn primary noprint" data-act="er-get"', h)
    assert 'class="btn " data-act="er-quiz"' in h                        # the quiz isn't the first step yet
    assert out["got"] == [["c", "quiz:3"]] and out["mat"] == ["ann1", "w10"]
    assert "1 lecture queued (2 parts), before anything from a full sync · 2 files downloading" in out["toast"]
    assert "Downloading 2 files…" in out["after"]
    assert "Everything of these weeks is on this PC." in out["ready"] and 'class="btn primary" data-act="er-quiz"' in out["ready"]


@node
def test_what_was_said_stressed_practised_and_condensed_and_its_weeks(tmp_path):
    out = run(tmp_path, """
  A["ready-open"]({ dataset: { cid: "c", key: "quiz:3" } }); await tick();
  out.html = view(); out.text = text(view());
  A["mq-row"]({ dataset: { key: "c|er:lo:2" } }); out.open = text(view());
  await A["er-read"]({ dataset: { day: "c|2026-11-16|lecture" }, checked: false }); await tick();
  out.read = called("reads_set");
""")
    t, h = out["text"], out["html"]
    assert "What the lecturer said about it About Quiz 3 : Quiz 3 will cover weeks 9 and 10." in t
    assert 'data-act="alert-open" data-day="c|2026-11-16|lecture" data-part="1" data-t="610"' in h
    assert "About the exam" in t and "said 2 times" in t and "Week 10" in t
    assert "LO2 use Bayes 1 of 4 right · from 1 quiz Losing points" in t
    assert "1 question you missed is still open." in t and 'data-act="qz-play" data-id="q3"' in h
    assert "12 flashcards from these weeks · 3 due today" in t
    assert "Made for it Cheatsheet · Weeks 9–10" in t
    assert "Lectures read: 1 of 1" in t and "Course-wide and announcement files" in t and "Quiz3 - Solutions.pdf" in t
    assert "· tied to it" in t and "not on this PC" in t
    assert "Bayes?" in out["open"]                                       # its missed question, opened
    assert out["read"] == [[["c|2026-11-16|lecture"], False]]


@node
def test_its_buttons_open_the_tools_set_to_its_weeks(tmp_path):
    out = run(tmp_path, """
  A["ready-open"]({ dataset: { cid: "c", key: "quiz:3" } }); await tick();
  out.html = view();
  // the quiz dialog on this assessment, even when another one comes first
  const Q2 = { ...QUIZ3, key: "quiz:2", no: 2, name: "Quiz 2", weeks: [6, 7], files: [] };
  spy("quiz_options", { ok: true, model: "sonnet", ai: { available: true }, quant: false, language: "English", general: [], assessments: [Q2, QUIZ3],
    weeks: [6, 7, 9, 10].map((no) => ({ no, topic: "T" + no, files: [], days: [], missing: [], todo_days: [] })) });
  spy("quiz_estimate", { ok: true, estimate: null });
  A["er-quiz"](); await tick();
  out.dialog = text(overlay());
  // a cram session: every card of its weeks, the schedule left alone (no "in 3 days" on its buttons)
  spy("cards_session", { ok: true, total: 2, cards: [{ id: "k1", q: "Front", a: "Back", origin: "files", source: {}, week: 9, stressed: false, state: null, next: {}, cram: true },
    { id: "k2", q: "Front 2", a: "Back 2", origin: "files", source: {}, week: 10, stressed: false, state: null, next: {}, cram: true }] });
  spy("cards_grade", { ok: true, state: { due: new Date(Date.now() + 60000).toISOString(), step: 0, cram: true, next: {} } });
  A["er-cram"](); await tick();
  St.practice.study && (S.practice.study.shown = true); St.practice.drawStudy();
  out.cramCard = overlay();
  await St.practice.gradeCard("again"); await tick();
  out.grades = called("cards_grade"); out.queue = S.practice.study.queue.map((c) => c.id);
  out.sessions = called("cards_session");
  // a cheatsheet with its weeks, a review sheet for it, the export
  let packed = null, planned = null, exported = null;
  A["pack-new"] = (el) => { packed = { ...el.dataset }; }; A["plan-new"] = (el) => { planned = { ...el.dataset }; }; A["cx-open"] = (el) => { exported = { ...el.dataset }; };
  A["er-sheet"](); A["er-prep"](); A["er-export"]();
  out.packed = packed; out.planned = planned; out.exported = exported;
  let chat = null; St.chatPicker = (cid, o) => { chat = [cid, o.days]; }; A["er-ask"](); out.chat = chat;
  A["er-print"](); out.printed = printed;
""")
    assert "For Quiz 3" in out["dialog"] and "The weeks it covers: weeks 9–10" in out["dialog"]
    assert out["sessions"] == [["c", [9, 10], [], True]]
    assert "MATH 240 · cram</b>" in out["cramCard"]
    assert 'data-g="good" autofocus><b>Good</b><span></span>' in out["cramCard"]                  # no interval: it stays
    assert out["grades"] == [["c", "k1", "again", True]] and out["queue"].count("k1") == 2     # again in this sitting
    assert out["packed"] == {"id": "c", "kind": "sheet", "weeks": "9,10"}
    assert out["planned"] == {"id": "c", "kind": "prep", "focus": "Quiz 3"}
    assert out["exported"] == {"id": "c", "key": "quiz:3", "weeks": "9,10"}
    assert out["chat"] == ["c", ["c|2026-11-16|lecture"]] and out["printed"] == 1


@node
def test_the_weeks_and_the_day_can_be_changed(tmp_path):
    out = run(tmp_path, """
  A["ready-open"]({ dataset: { cid: "c", key: "quiz:3" } }); await tick();
  A["er-weeks"](); out.dialog = text(overlay());
  A["er-wk"]({ dataset: { no: "11" } }); A["er-wk"]({ dataset: { no: "9" } });
  await A["er-weeks-save"](); await tick();
  out.weeks = called("exam_set_weeks"); out.after = text(view());
  A["er-weeks"](); out.reset = text(overlay());
  els["#er-day-in"] = { value: "2026-12-04" };
  A["er-day"](); out.day = text(overlay());
  await A["er-day-save"](); await tick();
  out.days = called("exam_set_day"); out.afterDay = text(view());
""")
    assert "Which weeks does Quiz 3 cover?" in out["dialog"] and "W16" in out["dialog"]
    assert out["weeks"] == [["c", "quiz:3", [10, 11]]]
    assert "Covers weeks 10–11 · the weeks you set" in out["after"]
    assert "Back to the announced weeks (weeks 9–10)" in out["reset"]
    assert "When is Quiz 3?" in out["day"]
    assert out["days"] == [["c", "quiz:3", "2026-12-04"]] and "the day you set" in out["afterDay"]


@node
def test_the_evening_before_with_background_checks_off_it_offers_them_once(tmp_path):
    out = run(tmp_path, """
  S.boot = { app: { onboarded: true, tray: false } };
  St.mocks.exam_room = () => ({ ok: true, room: room(QUIZ3, { when: { date: inDays(1), time: "", from: "announced", others: [], days: 1 } }) });
  let on = 0; A["bg-on"] = () => { on++; };
  const store = {}; globalThis.localStorage = { getItem: (k) => store[k] || null, setItem: (k, v) => { store[k] = v; } };
  A["ready-open"]({ dataset: { cid: "c", key: "quiz:3" } }); await tick();
  out.offer = text(view());
  A["er-tray-on"](); out.on = on; out.after = text(view());
""")
    assert "Background checks are off, so no reminder comes while IEUStudia is closed." in out["offer"]
    assert out["on"] == 1 and "Background checks are off" not in out["after"]


@node
def test_an_exam_known_by_its_week_is_this_week_not_done_in_that_week(tmp_path):
    """An exam known only by its week read "done" from the second day of that week, and its chip went."""
    out = run(tmp_path, """
  const inWeek = { date: inDays(-2), time: "", from: "week", others: [], end: inDays(4), days: -2 };
  St.mocks.exam_room = () => ({ ok: true, room: room(FINAL, { when: inWeek }) });
  A["ready-open"]({ dataset: { cid: "c", key: FINAL.key } }); await tick();
  out.head = text(view());
  out.chip = St.examroom.chip(S.courses[0], { assessments: [{ ...FINAL, on: inWeek }] });
  out.over = St.examroom.chip(S.courses[0], { assessments: [{ ...FINAL, on: { ...inWeek, date: inDays(-9), end: inDays(-3) } }] });
""")
    assert "this week · its week starts then" in out["head"] and "done" not in out["head"].split("Covers")[0]
    assert "Final Exam this week" in out["chip"] and out["over"] == ""


@node
def test_an_announced_day_offers_no_day_to_set(tmp_path):
    """"Set the exact day" was offered for an announced day, and saving changed nothing."""
    out = run(tmp_path, """
  St.mocks.exam_room = () => ({ ok: true, room: room(QUIZ3, { when: { date: inDays(3), time: "", from: "announced", others: [], days: 3 } }) });
  A["ready-open"]({ dataset: { cid: "c", key: "quiz:3" } }); await tick();
  out.html = view();
""")
    assert 'data-act="er-day"' not in out["html"] and "the day announced" in text_of(out["html"])


@node
def test_a_heads_up_from_another_sections_lecture_says_so(tmp_path):
    out = run(tmp_path, """
  const said = [{ source: "lecture", day_key: DAY, part: 1, t: 60, ts: "01:00", date: "2026-11-16", text: "The quiz is on Wednesday",
    categories: ["quiz"], other_section: "Section 2", other_section_en: "Section 2" }];
  St.mocks.exam_room = () => ({ ok: true, room: room(QUIZ3, { said }) });
  A["ready-open"]({ dataset: { cid: "c", key: "quiz:3" } }); await tick();
  out.html = view();
""")
    assert 'class="pill other-sec"' in out["html"] and ">Section 2</span>" in out["html"]
    assert "Heard in another section&#39;s lecture" in out["html"] or "Heard in another section's lecture" in out["html"]


@node
def test_quiz_this_lo_in_the_room_is_for_its_assessment(tmp_path):
    out = run(tmp_path, """
  const base = room(QUIZ3);
  const mastery = { ...base.practice.mastery, los: [...base.practice.mastery.los, { no: 5, text: "use random variables", tried: 0, right: 0, questions: 0,
    quizzes: 0, share: 0, status: "untested", missed: [] }] };
  St.mocks.exam_room = () => ({ ok: true, room: room(QUIZ3, { practice: { ...base.practice, mastery } }) });
  A["ready-open"]({ dataset: { cid: "c", key: "quiz:3" } }); await tick();
  out.html = view();
""")
    assert 'data-act="mq-quiz-lo" data-cid="c" data-key="quiz:3" data-no="5"' in out["html"]


def text_of(h):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", h)).strip()


def test_the_page_prints_and_loads_after_the_pages_it_hooks_into():
    css = (WEB / "examroom.css").read_text(encoding="utf-8")
    page = (WEB / "index.html").read_text(encoding="utf-8")
    pr = css.split("@media print")[1]
    assert ".sidebar" in pr and ".topbar" in pr and ".queuebar" in pr and ".noprint" in pr and "html:has(.er-page)" in pr
    assert 'href="examroom.css"' in page
    order = [page.index(f'src="{f}"') for f in ("course.js", "studypack.js", "picks.js", "digest.js", "practice.js", "mistakes.js", "examroom.js")]
    assert order == sorted(order)                       # its events wrap course.js's, picks.js's, practice.js's
