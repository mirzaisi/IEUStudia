"""The material pickers (4.2.0, item 1), in node with app.js: the course-wide and announcement files are offered
unticked (a quiz's posted solutions ticked for it), a class list is left out with "Include anyway" (asked first), the
quiz and cards dialogs have the plan dialog's Download and Transcribe rows, and the quiz dialog, the plan dialog and
Get ready show the same weeks (d.assessments)."""

import json
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
  querySelector: () => null, querySelectorAll: () => [], addEventListener() {}, remove() {}, focus() {}, contains: () => false });
const els = { "#top": el(), "#view": el(), "#overlay": el(), "#pk-cost": el(), "#toasts": { appendChild: (b) => toasts.push(b) } };
globalThis.document = new Proxy({}, { get: (t, k) => k === "compatMode" ? "CSS1Compat"
  : k === "activeElement" ? null
  : k === "hidden" ? false
  : k === "createElement" ? () => el()
  : k === "querySelector" ? (s) => (s in els ? els[s] : stub())
  : k === "getElementById" ? (id) => els["#" + id] || null
  : k === "querySelectorAll" ? () => []
  : k === "addEventListener" ? () => {} : stub() });
globalThis.localStorage = { getItem: () => null, setItem() {} };
globalThis.navigator = { userAgent: "node", platform: "Win32" };
globalThis.location = { search: "", hash: "", href: "" };
globalThis.CSS = { escape: (s) => s };
globalThis.addEventListener = () => {}; globalThis.matchMedia = () => ({ matches: false, addEventListener() {} });
globalThis.requestAnimationFrame = (f) => f();
const later = setTimeout;
globalThis.setInterval = () => 0; globalThis.setTimeout = () => 0;      // no boot, no polling
for (const f of ["i18n.js", "match.js", "icons.js", "app.js", "effort.js", "course.js", "plans.js", "studypack.js", "picks.js",
  "practice.js"]) require(web + "/" + f);
const St = globalThis.Studia, S = St.S, A = St.A;
St.aiName = () => "Claude"; St.sq = { card: () => "", line: () => "" };
const tick = (ms = 30) => new Promise((r) => later(r, ms));
const text = (h) => String(h).replace(/<[^>]*>/g, " ").replace(/&#39;/g, "'").replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&").replace(/\\s+/g, " ").trim();
const html = () => els["#overlay"].innerHTML;
const box = (id, act) => (html().match(new RegExp('<input[^>]*' + (act ? 'data-act="' + act + '"[^>]*' : "") + 'data-id="' + id + '"[^>]*>')) || [""])[0];
const out = {}, calls = [];
// one course: Quiz 3 (week 10) covers weeks 9 and 10 as announced, its solutions came with an announcement in week 9;
// week 9 has a class list and a file not on this PC, week 10 a lecture not transcribed
const QUIZ3 = { kind: "quiz", no: 3, name: "Quiz 3", week: 10, key: "quiz:3", los: [], sources: [{ id: "a1", title: "About Quiz 3" }],
  weeks: [9, 10], weeks_from: "announced", covers: [9, 10], ahead: true, files: ["ann1"] };
const FINAL = { kind: "final", no: 0, name: "Final Exam", week: 16, key: "syllabus:Final Exam:16", los: [], sources: [], syllabus: true,
  weeks: Array.from({ length: 16 }, (_, i) => i + 1), weeks_from: "guessed", ahead: true, files: [] };
const file = (id, extra) => ({ id, name: id + ".pdf", pages: 10, ext: "pdf", ready: true, downloaded: true, students: false, ...extra });
const DAY = "c|2026-11-18|lecture";
const start = (no) => `2026-${no < 8 ? "10" : "11"}-${String(no < 8 ? 26 : (no - 8) * 7 + 2).padStart(2, "0")}`;
const weeks = [8, 9, 10].map((no) => ({ no, start: start(no), current: no === 10, past: no < 10, topic: "Topic " + no,
  files: no === 9 ? [file("w9"), file("grades", { name: "Lab Grades.pdf", students: true }), file("w9b", { ready: false, downloaded: false, pages: 0 })] : [file("w" + no)],
  days: no === 10 ? [{ day_key: DAY, date: "2026-11-18", label: "Wed 18 Nov", kind: "Lecture", ready: false, mine: true }] : [],
  missing: no === 9 ? ["w9b"] : [], todo_days: no === 10 ? [DAY] : [] }));
const general = [{ id: "ann1", title: "Quiz3 - Solutions", name: "Quiz3 - Solutions.pdf", pages: 2, ext: "pdf", ready: true, downloaded: true,
  group: "announcement", unit: "From announcements", week: 9, for: ["quiz:3"], students: false },
  { id: "ann2", title: "Quiz3 - Practice", name: "Quiz3 - Practice.pdf", pages: 0, ext: "pdf", ready: false, downloaded: false,
  group: "announcement", unit: "From announcements", week: 9, for: ["quiz:3"], students: false },
  { id: "syl", title: "Syllabus", name: "Syllabus.pdf", pages: 3, ext: "pdf", ready: true, downloaded: true, group: "course",
  unit: "Course Documents", week: null, for: [], students: false }];
St.mocks.quiz_options = () => ({ ok: true, model: "sonnet", ai: { available: true }, quant: false, language: "English", weeks, general,
  assessments: [QUIZ3, FINAL] });
St.mocks.pack_options = () => ({ ok: true, model: "sonnet", ai: { available: true }, course: { id: "c" }, weeks, general });
St.mocks.quiz_estimate = () => ({ ok: true, estimate: null });
St.mocks.cards_estimate = () => ({ ok: true, estimate: null });
St.mocks.plan_estimate = () => ({ ok: true, estimate: null });
St.mocks.materials_download = (ids) => { calls.push(["download", ids]); return { ok: true, queued: ids.length }; };
St.picks.transcribe = (days) => { calls.push(["transcribe", days]); };      // (app.js's own preview answers transcribe_day)
St.mocks.students_include = (id) => { calls.push(["include", id]); return { ok: true }; };
St.ask = async (o) => { calls.push(["ask", o.title]); return true; };
(async () => {
  S.mode = "app"; S.view = { name: "home" }; S.boot = {}; S.system.ai = { available: true, provider: "claude" };
  S.courses = [{ id: "c", code: "MATH 240", display_name: "Probability", days: [], counts: {} }];
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
def test_the_quiz_dialog_offers_the_course_wide_files_leaves_a_class_list_out_and_fetches_what_is_missing(tmp_path):
    out = run(tmp_path, """
  await A["qz-new"]({ dataset: { cid: "c" } }); await tick();
  out.next = { ann: box("ann1"), syl: box("syl"), grades: box("grades"), w9: box("w9"), text: text(html()) };
  await A["qz-download"](); await tick();
  out.during = text(html());
  A["qz-transcribe"](); await tick();
  await A["students-ok"]({ dataset: { id: "grades", name: "Lab Grades.pdf" } }); await tick();
  out.after = { grades: box("grades"), calls: calls.splice(0) };
  A["qz-mode"]({ dataset: { val: "weeks" } });
  out.weeks = box("ann1");
""")
    n = out["next"]
    assert "checked" in n["ann"] and "checked" not in n["syl"]           # Quiz 3's solutions ticked for it, not the rest
    assert "checked" in n["w9"] and "disabled" in n["grades"] and "checked" not in n["grades"]
    assert "For Quiz 3" in n["text"] and "The weeks it covers: weeks 9–10" in n["text"]
    assert "Course-wide and announcement files" in n["text"] and "Quiz3 - Solutions.pdf · From an announcement, week 9" in n["text"]
    assert "Syllabus.pdf · Course Documents" in n["text"]
    assert "Left out: it lists students Include anyway" in n["text"]
    assert "Download 2" in n["text"] and "Transcribe 1" in n["text"]   # as the plan dialog; with Quiz 3's practice file
    assert "downloading…" in out["during"] and "Download" not in out["during"]
    assert out["after"]["calls"] == [["download", ["w9b", "ann2"]], ["transcribe", ["c|2026-11-18|lecture"]],
                                     ["ask", "Include this file?"], ["include", "grades"]]
    assert "checked" in out["after"]["grades"] and "disabled" not in out["after"]["grades"]   # let in: ticked
    assert "checked" not in out["weeks"]                                 # weeks you pick: none of them ticked


@node
def test_the_cards_dialog_offers_them_too_with_download(tmp_path):
    out = run(tmp_path, """
  await A["fc-make"]({ dataset: { cid: "c" } }); await tick();
  out.first = text(html());
  A["fm-week"]({ dataset: { no: "9" } }); await tick();
  out.text = text(html()); out.ann = box("ann1"); out.grades = box("grades"); out.w9 = box("w9");
  await A["fm-download"](); await tick();
  out.calls = calls.splice(0);
""")
    assert "Course-wide and announcement files" in out["first"] and "Download" not in out["first"]   # week 10: all here
    assert "checked" not in out["ann"] and "checked" in out["w9"]
    assert "Left out: it lists students" in out["text"] and "disabled" in out["grades"]
    assert "Download 1" in out["text"] and "Transcribe" not in out["text"]    # cards come from files only
    assert out["calls"] == [["download", ["w9b"]]]


@node
def test_the_quiz_dialog_the_plan_dialog_and_get_ready_show_the_same_weeks(tmp_path):
    out = run(tmp_path, """
  await A["qz-new"]({ dataset: { cid: "c" } }); await tick();
  out.quiz = text(html()).match(/The weeks it covers: ([^·]*?) Weeks you pick/)[1].trim();
  St.mocks.plan_options = () => ({ ok: true, course: { id: "c" }, model: "sonnet", ai: { available: true }, assessments: [QUIZ3, FINAL], general,
    weeks: [8, 9, 10].map((no) => ({ no, start: start(no), end: "", current: no === 10, past: no < 10, topic: "Topic " + no, exam: false, files: 1,
      files_ready: 1, missing: [], students: no === 9 ? [{ id: "grades", name: "Lab Grades.pdf" }] : [], lectures: 0, lectures_done: 0, todo_days: [] })) });
  await A["plan-new"]({ dataset: { id: "c" } }); await tick();
  out.plan = [...html().matchAll(/<input[^>]*data-act="pd-week" data-no="(\\d+)" checked/g)].map((m) => +m[1]);
  out.planGen = { ann: box("ann1", "pd-gen"), ann2: box("ann2", "pd-gen"), syl: box("syl", "pd-gen"), text: text(html()) };
  A["pd-assess"]({ dataset: { i: "-1" } });
  out.planNone = box("ann1", "pd-gen");
  els[".modal.rd"] = el();
  St.mocks.plan_list = () => ({ ok: true, plans: [] }); St.mocks.course_marks = () => ({ ok: true, marks: [] });
  S.cd = { c: { id: "c", general: [], outcomes_en: [], outcomes_tr: [], weeks: weeks.map((w) => ({ ...w, end: "", units: [], classes: [], days: [] })),
    alerts: { items: [], plan: [QUIZ3], counts: {} }, assessments: [QUIZ3, FINAL] } };
  await St.openReady("c", "Quiz 3"); await tick();
  out.ready = text(els[".modal.rd"].innerHTML);
""")
    assert out["quiz"] == "weeks 9–10"
    assert out["plan"] == [9, 10]
    assert "covers weeks 9–10" in out["ready"]
    assert "checked" in out["planGen"]["ann"] and "checked" not in out["planGen"]["syl"]   # For Quiz 3: its solutions
    assert "checked" in out["planGen"]["ann2"] and "Download 1" in out["planGen"]["text"]  # (read once it is in)
    assert "Lab Grades.pdf: Left out: it lists students Include anyway" in out["planGen"]["text"]
    assert "checked" not in out["planNone"]                             # just these weeks: none
