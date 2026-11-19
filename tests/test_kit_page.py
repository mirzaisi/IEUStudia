"""The exam kit on the page, in node with a stand-in for app.js: the Export for AI chat dialog's "For" (the coming
quizzes and the syllabus final; one picks its weeks and says where they come from; it goes into the choice; the Exam
room's preset), the end with the first message and the three prompts, each with Copy (export.js); and the Practice
tab's "Paste from ChatGPT or Claude" (kit.js): the button on the quizzes card (practice.js), the dialog, what was read
(questions or cards, their sources, what couldn't be used) with Practise now, Study now and Undo, a failure said in the
dialog, and Ctrl+V on the tab, which never takes a paste meant for a text field or another dialog."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from studia import i18n

WEB = Path(i18n.__file__).parent / "web"

STAND_IN = r"""
globalThis.window = globalThis;
globalThis.location = { hash: "" };
const shown = { html: "" }, calls = [], toasts = [], listeners = {};
const modalEl = { classList: { add() {}, contains: () => true } };
globalThis.document = { documentElement: {}, body: { tagName: "BODY" }, activeElement: null,
  addEventListener: (type, fn) => { (listeners[type] = listeners[type] || []).push(fn); },
  querySelectorAll: () => [], getElementById: () => null,
  querySelector: (sel) => (sel === "#overlay .modal" || sel === ".modal" ? (shown.html ? modalEl : null) : null) };
document.activeElement = document.body;
require(%(i18n)s);
const turkish = %(table)s;
const answers = %(answers)s;
const S = { view: { name: "course", courseId: "c", tab: "practice" }, settings: {}, system: { ai: { available: true } },
  courses: [{ id: "c", code: "MATH 240", display_name: "Probability" }] };
window.Studia = { S, A: {}, views: {}, onEvents: {}, onGo: {}, crumbs: {}, mocks: {}, I: (n) => `[${n}]`, SPIN: () => "(spin)",
  esc: (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])),
  toast: (m, kind, action) => toasts.push([m, kind, action ? action.act : ""]), modal: (h) => { shown.html = h; },
  closeModal: () => { shown.html = ""; const hook = window.Studia.onModalClose; window.Studia.onModalClose = null; if (hook) hook(); },
  call: (name, ...args) => { calls.push([name, ...args]); const a = answers[name]; return Promise.resolve(typeof a === "function" ? a(...args) : a || { ok: true }); },
  go: () => {}, renderView: () => {}, renderSidebar: () => {}, skeleton: () => "<skel>", mdToHtml: (s) => String(s), renderMath: () => {},
  findCourse: (id) => S.courses.find((c) => c.id === id), hue: () => 200, aiName: () => "Claude", aiHint: () => "", aiBtn: () => "",
  isChatGPT: () => false,
  emptyHTML: (i, a) => a, fmtK: (n) => `${n}`, pack: { weekSpan: (w) => (w || []).join(", ") } };
const St = window.Studia, A = St.A, tick = () => new Promise((r) => setTimeout(r, 5));
const out = {};
const paste = (text, target) => { const ev = { clipboardData: { getData: () => text }, target: target || document.body, prevented: false,
  preventDefault() { this.prevented = true; } }; (listeners.paste || []).forEach((fn) => fn(ev)); return ev.prevented; };
"""

EXPORT = STAND_IN + r"""
require(%(page)s);
(async () => {
  await A["cx-open"]({ dataset: { id: "c" } }); await tick();
  out.pick = shown.html;
  A["cx-for"]({ value: "quiz:2" }); out.forQuiz = shown.html;
  A["cx-go"](); await tick();
  out.start = calls.filter((c) => c[0] === "course_export_start").pop();
  St.onEvents.course_export({ type: "course_export", id: "x1", course_id: "c", phase: "cancelled" });
  St.closeModal();
  await A["cx-open"]({ dataset: { id: "c" } }); await tick();
  A["cx-for"]({ value: "syllabus:Final Exam:15" }); out.forFinal = shown.html;
  A["cx-for"]({ value: "quiz:3" }); out.forGuess = shown.html;
  A["cx-for"]({ value: "" }); out.forNone = shown.html;
  St.closeModal();
  await A["cx-open"]({ dataset: { id: "c", key: "quiz:2" } }); await tick();       // the Exam room's button
  out.preset = shown.html;
  St.closeModal();
  await St.cexp.open("c", { key: "quiz:2" }); await tick();
  out.presetApi = shown.html;
  A["cx-go"](); await tick();
  const ev = (e) => St.onEvents.course_export({ type: "course_export", id: "x1", course_id: "c", title: "MATH 240", ...e });
  ev({ phase: "done", name: "MATH 240 - weeks 2-3 - 8 Oct 2026.zip", bytes: 1048576, path: "C:/x/z.zip",
       first_message: "This zip holds my MATH 240 course… I'm preparing for Quiz 2: read 00 Start here.md first.",
       kit: { id: "ab12cd34", focus: "Quiz 2", start: true, prompts: [{ key: "quiz", text: "QUIZ PROMPT" }, { key: "cards", text: "CARDS PROMPT" }, { key: "sheet", text: "SHEET PROMPT" }] } });
  out.done = shown.html;
  await A["cx-copy-prompt"]({ dataset: { k: "cards" } }); await A["cx-copy-prompt"]({ dataset: { k: "quiz" } });
  out.copies = calls.filter((c) => c[0] === "copy_text").map((c) => c[1]);
  t.load({ lang: "tr", table: turkish });
  ev({ phase: "done", name: "z.zip", bytes: 1, path: "C:/x/z.zip", first_message: "…", kit: { id: "x", focus: "Quiz 2", start: true, prompts: [{ key: "quiz", text: "Q" }] } });
  out.doneTr = shown.html;
  t.load({ lang: "en", table: {} });
  answers.course_export_options = { ...answers.course_export_options, all: true, assessments: [] };
  St.closeModal();
  await A["cx-open"]({ dataset: { id: "" } }); await tick();
  out.allCourses = shown.html;
  process.stdout.write(JSON.stringify(out), () => process.exit(0));
})();
"""

WEEK = lambda no, now: {"no": no, "start": f"2026-09-{20 + no:02d}", "end": f"2026-09-{26 + no:02d}", "topic": f"Topic {no}",  # noqa: E731
                        "current": no == 3, "now": now, "lectures": 1 if now else 0, "untranscribed": 0, "days": 1,
                        "files": 1 if now else 0, "missing": [], "links": 0, "screens": 0, "courses": ["MATH 240"],
                        "bytes": {"text": 1000, "screen_text": 0, "screens": 0, "files": 1000}}
EXPORT_ANSWERS = {
    "course_export_state": {"ok": True, "running": False, "export": None},
    "course_export_options": {"ok": True, "all": False, "courses": [{"id": "c", "code": "MATH 240"}],
                              "weeks": [WEEK(1, True), WEEK(2, True), WEEK(3, True), WEEK(4, False), WEEK(15, False)],
                              "general": {"files": 0, "missing": [], "bytes": 0}, "loose": 0, "base": 100, "notices": 100,
                              "folder": "C:/x", "free": 10 ** 12, "chatgpt_max": 512 * 1048576,
                              "assessments": [
                                  {"key": "quiz:2", "name": "Quiz 2", "kind": "quiz", "week": 4, "weeks": [2, 3],
                                   "weeks_from": "announced", "date": "2026-10-12"},
                                  {"key": "quiz:3", "name": "Quiz 3", "kind": "quiz", "week": 6, "weeks": [5, 6],
                                   "weeks_from": "guessed", "date": "2026-10-26"},
                                  {"key": "syllabus:Final Exam:15", "name": "Final Exam", "kind": "final", "week": 15,
                                   "weeks": list(range(1, 16)), "weeks_from": "guessed", "date": "2026-12-28"}]},
    "course_export_start": {"ok": True, "id": "x1"},
    "copy_text": {"ok": True},
}


def _run(tmp_path, script, answers, **pages):
    path = tmp_path / "run.js"
    path.write_text(script % {"i18n": json.dumps(str(WEB / "i18n.js")), "answers": json.dumps(answers),
                              "table": json.dumps(i18n.table("tr"), ensure_ascii=False),
                              **{k: json.dumps(str(WEB / v)) for k, v in pages.items()}}, encoding="utf-8")
    run = subprocess.run(["node", str(path)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_the_export_dialog_aims_at_a_coming_quiz_and_ends_with_the_prompts(tmp_path):
    out = _run(tmp_path, EXPORT, EXPORT_ANSWERS, page="export.js")
    pick = out["pick"]
    assert 'data-act="cx-for"' in pick and '<option value="" selected>No quiz or exam in particular' in pick
    assert ">Quiz 2 · week 4 · 12 Oct</option>" in pick and "Final Exam · week 15 · 28 Dec" in pick
    assert "00 Start here.md" not in pick and 'data-no="1" checked' in pick         # (everything up to now, as before)
    q = out["forQuiz"]
    assert '<option value="quiz:2" selected>' in q and "It covers weeks 2–3, as announced." in q
    assert "The zip gets 00 Start here.md" in q and 'data-no="2" checked' in q and 'data-no="1" checked' not in q
    assert out["start"][2]["weeks"] == [2, 3] and out["start"][2]["focus"] == "quiz:2"
    final = out["forFinal"]                                                          # (only the weeks with something)
    assert "It covers weeks 1–15: the whole term." in final
    assert all(f'data-no="{n}" checked' in final for n in (1, 2, 3)) and 'data-no="15" checked' not in final
    assert "IEUStudia's guess, so change the weeks below if it's wrong." in out["forGuess"]
    assert 'data-no="1" checked' in out["forGuess"]               # (no week of it has anything yet: up to now again)
    assert "00 Start here.md" not in out["forNone"] and 'data-no="3" checked' in out["forNone"]
    assert '<option value="quiz:2" selected>' in out["preset"] and '<option value="quiz:2" selected>' in out["presetApi"]
    done = out["done"]
    assert "I'm preparing for Quiz 2: read 00 Start here.md first." in done and "Then practise with it" in done
    for k in ("quiz", "cards", "sheet"):
        assert f'data-act="cx-copy-prompt" data-k="{k}"' in done
    assert "Practice quiz" in done and "Flashcards" in done and "Cheatsheet" in done
    assert "press Ctrl+V on this course's Practice tab" in done and "tells the AI what Quiz 2 covers" in done
    assert out["copies"] == ["CARDS PROMPT", "QUIZ PROMPT"]
    assert "Sonra onunla çalış" in out["doneTr"] and "Kopyala" in out["doneTr"]
    assert 'data-act="cx-for"' not in out["allCourses"]                           # (all courses: no For)


KIT = STAND_IN + r"""
require(%(practice)s);
require(%(kit)s);
(async () => {
  St.practice.tab({ id: "c", code: "MATH 240" }); await tick();
  out.tab = St.practice.tab({ id: "c", code: "MATH 240" });
  A["kit-paste"]({ dataset: { cid: "c" } });
  out.dialog = shown.html;
  await A["kit-add"](); out.empty = shown.html;
  answers.quiz_import = { ok: true, kind: "quiz", id: "p1", title: "Bayes", questions: 9, read: 10, unused: 1, named: 7, found: 1, no_source: 1 };
  St.closeModal();
  out.ignoredField = paste("{json}", { tagName: "TEXTAREA" });
  document.activeElement = { tagName: "INPUT", type: "search" };
  out.ignoredFocus = paste("{json}");
  document.activeElement = document.body;
  S.view = { name: "course", courseId: "c", tab: "weeks" };
  out.ignoredTab = paste("{json}");
  S.view = { name: "course", courseId: "c", tab: "practice" };
  shown.html = "<other dialog>";
  out.ignoredModal = paste("{json}");
  shown.html = "";
  out.taken = paste("{\"questions\": []}"); await tick(); await tick();
  out.importCall = calls.filter((c) => c[0] === "quiz_import").pop();
  out.quizDone = shown.html;
  A["kit-play"](); await tick();
  out.played = calls.filter((c) => c[0] === "quiz_get").length;
  answers.quiz_import = { ok: true, kind: "cards", id: "p2", title: "x", cards: 24, read: 26, dupes: 2, named: 21, found: 2, no_source: 1 };
  A["kit-paste"]({ dataset: { cid: "c" } }); await tick();
  out.reopened = shown.html;
  St.closeModal();
  paste("front\tback"); await tick(); await tick();
  out.cardsDone = shown.html;
  await A["kit-undo"](); await tick();
  out.undo = calls.filter((c) => c[0] === "cards_set_delete").pop();
  out.undoShown = shown.html;
  answers.quiz_import = { ok: false, error: "IEUStudia found no quiz and no flashcards in what you pasted." };
  paste("hello there"); await tick(); await tick();
  out.failed = shown.html;
  t.load({ lang: "tr", table: turkish });
  St.closeModal();
  A["kit-paste"]({ dataset: { cid: "c" } });
  out.turkish = shown.html;
  out.tabTr = St.practice.tab({ id: "c", code: "MATH 240" });
  out.toasts = toasts;
  process.stdout.write(JSON.stringify(out), () => process.exit(0));
})();
"""

KIT_ANSWERS = {
    "practice_course": {"ok": True, "deck": {"total": 0, "today": 0, "weeks": []}, "card_sets": [],
                        "quizzes": [{"id": "p1", "course_id": "c", "title": "Bayes", "status": "done", "weeks": [2, 3],
                                     "days": [], "focus": "Quiz 2", "source": "import", "questions": 9,
                                     "created_at": "2026-10-08T10:00:00", "tries": 0}],
                        "stressed": {"total": 0, "counts": {}, "weeks": [], "not_needed": []}},
    "practice_summary": {"ok": True, "due": 0, "courses": []},
    "quiz_get": {"ok": True, "quiz": {"id": "p1", "course_id": "c", "title": "Bayes", "status": "done",
                                      "result": {"title": "Bayes", "coverage": {}, "questions": []}}, "attempts": []},
    "cards_set_delete": {"ok": True},
}


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_a_quiz_or_cards_pasted_on_the_practice_tab(tmp_path):
    out = _run(tmp_path, KIT, KIT_ANSWERS, practice="practice.js", kit="kit.js")
    tab = out["tab"]
    assert 'data-act="kit-paste" data-cid="c"' in tab and "Paste from ChatGPT or Claude" in tab
    assert tab.index('data-act="kit-paste"') < tab.index('data-act="qz-new"')       # beside New quiz, on the quizzes card
    assert "From ChatGPT or Claude" in tab                                           # an imported quiz says so
    d = out["dialog"]
    assert 'id="kit-text"' in d and 'data-act="kit-add"' in d and "A code block, JSON or plain text all work." in d
    assert 'data-act="cx-open" data-id="c"' in d                                     # (no quiz yet: the export's way)
    assert "Paste the answer first." in out["empty"]
    # Ctrl+V: never a paste meant for a text field, another tab or another dialog
    assert not out["ignoredField"] and not out["ignoredFocus"] and not out["ignoredTab"] and not out["ignoredModal"]
    assert out["taken"] and out["importCall"] == ["quiz_import", "c", '{"questions": []}', ""]
    done = out["quizDone"]
    assert "Added to Practice" in done and "<b>Bayes</b>" in done and "9 questions are ready to play." in done
    assert "7 open the page or lecture moment they came from." in done
    assert "1 named no source the app could open, so it was found by its words." in done
    assert "1 has no source." in done and "1 more couldn't be used (no clear right answer, or asked twice)." in done
    assert 'data-act="kit-play"' in done and 'data-act="kit-undo"' in done and out["played"] == 1
    assert 'id="kit-text"' in out["reopened"] and "Added" not in out["reopened"]
    cards = out["cardsDone"]
    assert "Added to your flashcards" in cards and "24 flashcards joined your deck." in cards
    assert "2 were in your deck already, or pasted twice." in cards and 'data-act="kit-study"' in cards
    assert out["undo"] == ["cards_set_delete", "p2"] and out["undoShown"] == ""
    assert ["The pasted cards were taken out again.", "info", ""] in out["toasts"]
    assert "IEUStudia found no quiz and no flashcards" in out["failed"] and "hello there" in out["failed"]
    assert "ChatGPT ya da Claude'dan yapıştır" in out["turkish"] and "Alıştırma'ya ekle" in out["turkish"]
    assert "ChatGPT ya da Claude'dan yapıştır" in out["tabTr"]


def test_the_page_loads_the_kit_after_the_practice_tab():
    page = (WEB / "index.html").read_text(encoding="utf-8")
    assert page.index('src="practice.js"') < page.index('src="kit.js"')
    assert 'href="export.css"' in page                                               # (its looks)
    css = (WEB / "export.css").read_text(encoding="utf-8")
    assert ".kit-text" in css and ".cx-pr" in css
