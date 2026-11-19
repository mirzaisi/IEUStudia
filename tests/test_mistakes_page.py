"""The mistake loop on the page (practice.js with mistakes.js, and player.js, in node with a stand-in for app.js): the
end screen's rows open to your answer beside the right one, why yours was wrong and where it was taught; a replay
shows the slide and the words around that moment and plays just that part, then leads back to the results; "Remove
this question" (and Undo); the answers are saved with what was typed and who marked them, and the missed ones joined
the cards (Undo); the Practice tab's Mistakes and "Where you lose points" (counts, an untested outcome's "Quiz this
LO"); the quiz dialog's "My weak spots" (the default after two tries); a missed multiple-choice question in the
flashcards, marked by the app; a card edited, set aside, deleted; and the player stopping at the clip's end."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from studia import i18n

WEB = Path(i18n.__file__).parent / "web"
node = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")

PAGE = r"""
globalThis.window = globalThis;
const shown = { html: "" }, calls = [], toasts = [], went = [], menus = [], opened = [], fields = {};
const modalEl = { classList: { add() {} }, querySelector: (sel) => {
  const cls = (sel.match(/\.([\w-]+)(?![^\[]*\])/g) || []).pop();
  return cls && shown.html.includes(cls.slice(1)) ? { click() {} } : null; } };
globalThis.document = { documentElement: {}, addEventListener() {}, querySelectorAll: () => [],
  querySelector: (sel) => (sel in fields ? fields[sel] : sel === "#overlay .modal" || sel === ".modal" ? (shown.html ? modalEl : null) : sel.startsWith("#overlay .") ? (shown.html.includes(sel.slice(10)) ? modalEl : null) : null),
  getElementById: () => null };
require(%(i18n)s);
const DAY = "c1|2026-10-21|lecture";
const QUIZ = { id: "q1", course_id: "c1", title: "Bayes' theorem", status: "done", result: { title: "Bayes' theorem", removed: [], not_here: [],
  coverage: { span: "week 5", weeks: [{ no: 5, topic: "Bayes" }], kinds: { mc: 2, short: 1 }, files: [], lectures: [], los: [] },
  questions: [
    { n: 1, type: "mc", q: "What is $P(A|B)$?", options: ["P(A and B)/P(B)", "P(A)P(B)", "P(B)/P(A)", "1"], answer: 0, why: "The definition.",
      wrong: ["", "Only when independent.", "Upside down.", "Never."], source: { kind: "file", id: "r1", name: "Week05.pdf", page: 12 }, los: [3] },
    { n: 2, type: "short", q: "Why the total probability rule?", model: "It gives the denominator.", points: ["the denominator"],
      source: { kind: "lecture", day_key: DAY, date: "2026-10-21", part: 1, t: 845, ts: "14:05" }, found: true, los: [3] },
    { n: 3, type: "mc", q: "Are disjoint events independent?", options: ["Yes", "No", "Always", "Never"], answer: 1, why: "Only if one is impossible.",
      wrong: ["Disjoint means dependent.", "", "No.", "No."], source: null, los: [4] }] } };
const MISS = (n, chose, extra) => ({ plan_id: "q1", n, title: "Bayes' theorem", at: "2026-11-18T10:00:00", misses: 1, q: QUIZ.result.questions[n - 1],
  answer: { n, got: 0, chose, mark: n === 2 ? "self" : "app", text: n === 2 ? "no idea" : "", ...extra }, card_id: "qx" + n, due: "" });
const WEAK = { attempts: 2, quizzes: 1, answers: 6, los: [
    { no: 3, text: "use Bayes' theorem", tried: 3, right: 1.5, questions: 2, quizzes: 1, share: 0.4, status: "shaky", missed: [{ plan_id: "q1", n: 2 }] },
    { no: 4, text: "tell independent events", tried: 1, right: 1, questions: 1, quizzes: 1, share: 1, status: "some", missed: [] },
    { no: 5, text: "use random variables", tried: 0, right: 0, questions: 0, quizzes: 0, share: 0, status: "untested", missed: [] }],
  topics: [{ name: "Bayes' theorem", tried: 2, right: 0.5, questions: 2, quizzes: 1, share: 0.25, status: "shaky", missed: [{ plan_id: "q1", n: 2 }] }] };
const COURSE = { ok: true, deck: { total: 12, due: 2, new: 1, today: 3, learnt: 4, lecture: 10, files: 2, quiz: 3, aside: 2, lecture_days: 3, weeks: [5] },
  quizzes: [{ id: "q1", course_id: "c1", title: "Bayes' theorem", status: "done", weeks: [5], days: [], focus: "", questions: 3, tries: 2,
              created_at: "2026-11-08T10:00:00", best: { score: 1, total: 3 }, last: { score: 1, total: 3, answers: [] } }],
  card_sets: [], stressed: { total: 0, counts: {}, weeks: [], not_needed: [] },
  mistakes: [MISS(2, null), MISS(3, 0), MISS(1, 1), { ...MISS(1, 2), plan_id: "q2" }, { ...MISS(3, 3), plan_id: "q2" }, { ...MISS(2, null), plan_id: "q3" }], weak: WEAK };
let OPTIONS = { ok: true, model: "sonnet", ai: { available: true }, quant: false, assessments: [], weak: { missed: 3, attempts: 2, quizzes: 1, los: [3], topics: ["Bayes' theorem"] },
  weeks: [{ no: 5, current: true, topic: "Bayes", files: [{ id: "r1", name: "Week05.pdf", pages: 12, ready: true, downloaded: true }], days: [] }] };
const MC_CARD = { id: "qk1", q: "Are disjoint events independent?", a: "**No**", origin: "quiz", source: { kind: "lecture", day_key: DAY, date: "2026-10-21", part: 1, t: 845, ts: "14:05" },
  quiz: { plan_id: "q1", n: 3, type: "mc", title: "Bayes' theorem", options: ["No", "Yes", "Always", "Never"], answer: 0, why: "Only if one is impossible.", wrong: ["", "Disjoint means dependent.", "No.", "No."] },
  state: { step: -1 }, next: { again: 600, hard: 86400, good: 86400 * 3, easy: 86400 * 6 } };
const PLAIN = { id: "k2", q: "Bayes?", a: "P(B|A)P(A)/P(B)", origin: "files", source: { kind: "file", id: "r1", name: "Week05.pdf", page: 3 }, state: null, next: { again: 60, hard: 330, good: 600, easy: 345600 } };
const ANSWERS = {
  quiz_get: () => ({ ok: true, quiz: JSON.parse(JSON.stringify(QUIZ)), attempts: [] }),
  quiz_attempt: (id, a) => ({ ok: true, attempt: { id: "att1", ...a }, attempts: [], carded: ["qx1", "qx2", "qx3"] }),
  quiz_question: (id, n, action) => ({ ok: true, removed: action === "remove" ? [n] : [], not_here: action === "not_here" ? [n] : [] }),
  practice_moment: () => ({ ok: true, day_key: DAY, part: 1, t: 845, ts: "14:05", date: "2026-10-21",
    slide: { n: 7, t0: 800, t1: 950, range: "13:20–15:50", title: "Total probability", image: "file:///s/7.jpg" },
    lines: [{ t: 800, ts: "13:20", text: "Now the denominator.", at: false }, { t: 840, ts: "14:00", text: "This is the total probability rule.", at: true }],
    clip: { from: 800, until: 950, range: "13:20–15:50" } }),
  practice_course: () => COURSE,
  practice_summary: () => ({ ok: true, due: 0, courses: [] }),
  quiz_options: () => OPTIONS,
  quiz_estimate: () => ({ ok: true, estimate: { main_input: 9000, main_output: 4000, minutes: "about 3–7 minutes" } }),
  quiz_create: (cid, sc) => ({ ok: true, plan: { id: "q9", scope: sc, status: "queued" } }),
  cards_session: () => ({ ok: true, cards: [MC_CARD, PLAIN] }),
  cards_grade: (cid, id, g) => ({ ok: true, state: { step: -1, due: new Date(Date.now() + 86400e3).toISOString().slice(0, 19), next: {} } }),
  cards_edit: () => ({ ok: true, card: {} }),
};
const S = { view: { name: "home" }, courses: [{ id: "c1", code: "MATH 240", display_name: "Probability" }], system: { ai: { available: true, provider: "claude", label: "Claude" } }, settings: {} };
window.Studia = { S, views: {}, onEvents: {}, onGo: {}, crumbs: {}, after: {}, mocks: {}, A: {},
  call: (name, ...args) => { calls.push([name, ...args]); return Promise.resolve(ANSWERS[name] ? ANSWERS[name](...args) : { ok: true }); },
  esc: (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])),
  I: (n) => `[${n}]`, SPIN: () => "~", toast: (m, level, act) => toasts.push([m, level, act && act.label, act && act.act]), modal: (h) => { shown.html = h; },
  closeModal: () => { shown.html = ""; }, go: (v) => went.push(v), renderView: () => {}, renderSidebar: () => {}, skeleton: () => "<skel>",
  mdToHtml: (s) => String(s), renderMath: () => {}, findCourse: (id) => S.courses.find((c) => c.id === id), hue: () => 200,
  aiName: () => "Claude", aiHint: () => "Set up AI", aiBtn: (ok) => (ok ? "" : "disabled"), isChatGPT: () => false, emptyHTML: (i, a) => a, fmtK: (n) => `${Math.round(n / 1000)}k`,
  openDay: () => Promise.resolve(), jumpTo: () => {}, menu: (el, items) => menus.push(items), ask: () => Promise.resolve(true),
  player: { open: (...a) => opened.push(a) }, pack: { weekSpan: (w) => (w || []).map((n) => "week " + n).join(", ") } };
require(%(practice)s);
require(%(mistakes)s);
const St = window.Studia, A = St.A, tick = () => new Promise((r) => setTimeout(r, 5)), P = () => St.S.practice;
const out = {};
(async () => {
  // ---- a quiz: B on the first (wrong), the short answer typed and marked "I didn't know it", the third skipped
  A["qz-play"]({ dataset: { id: "q1" } }); await tick();
  A["qz-start"]();
  A["qz-pick"]({ dataset: { i: "1" } }); A["qz-check"](); A["qz-next"]();
  P().run.text = "it adds up the parts"; A["qz-check"](); A["qz-self"]({ dataset: { got: "0" } });
  A["qz-skip"](); A["qz-next"](); await tick();
  out.end = shown.html;
  out.saved = calls.filter((c) => c[0] === "quiz_attempt").map((c) => c[2].answers);
  out.carded = toasts.find((x) => x[3] === "mq-uncard") || null;     // (said on the end screen, not in a toast)
  out.cardedLine = (shown.html.match(/<p class="mq-carded[\s\S]*?<\/p>/) || [""])[0];
  // the first row opened: your answer beside the right one, why yours is wrong, the page it comes from
  A["mq-row"]({ dataset: { key: "q1|1" } });
  out.row1 = shown.html;
  A["mq-row"]({ dataset: { key: "q1|2" } });
  out.row2 = shown.html;
  // a replay: the slide and the words around it; Play plays only that part, and its way back is the results
  await A["mq-replay"]({ dataset: { cid: "c1", plan: "q1", n: "2", day: DAY, part: "1", t: "845" } }); await tick();
  out.moment = shown.html;
  out.momentCall = calls.filter((c) => c[0] === "practice_moment").pop();
  A["mq-play"]();
  out.played = opened.map((a) => [a[0], a[1], a[2], a[3].until, a[3].back.label]);
  out.closed = shown.html;
  opened[0][3].back.run();
  out.back = shown.html;
  // not the right place: said once, and back to the results
  await A["mq-replay"]({ dataset: { cid: "c1", plan: "q1", n: "2", day: DAY, part: "1", t: "845" } }); await tick();
  await A["mq-not-here"](); await tick();
  out.notHere = calls.filter((c) => c[0] === "quiz_question").pop();
  out.afterNotHere = shown.html;
  // Remove this question: out of the retry, said in its row, with Undo
  A["mq-row"]({ dataset: { key: "q1|3" } });
  await A["mq-remove"]({ dataset: { cid: "c1", plan: "q1", n: "3" } }); await tick();
  out.removed = shown.html;
  out.removeToast = toasts[toasts.length - 1];
  // the cards' Undo
  await A["mq-uncard"]({ dataset: { cid: "c1", ids: "qx1,qx2" } });
  out.uncard = calls.filter((c) => c[0] === "mistakes_uncard").pop();
  // ---- the course tab: Mistakes (five, then all), Where you lose points
  S.view = { name: "course", courseId: "c1", tab: "practice" };
  St.onGo.course(S.view); await tick();
  out.tab = St.practice.tab(S.courses[0]);
  A["mq-all"]({ dataset: { cid: "c1" } });
  A["mq-row"]({ dataset: { key: "c1|lo:3" } });
  out.tabAll = St.practice.tab(S.courses[0]);
  // "Quiz this LO", "Quiz my weak spots": the dialog
  shown.html = "";
  await A["mq-quiz-lo"]({ dataset: { cid: "c1", no: "5", text: "use random variables" } }); await tick();
  out.loDialog = shown.html;
  await A["mq-weak"]({ dataset: { cid: "c1" } }); await tick(); await new Promise((r) => setTimeout(r, 350));
  out.weakDialog = shown.html;
  out.weakEstimate = calls.filter((c) => c[0] === "quiz_estimate").pop();
  await A["qz-go"]({ dataset: {}, innerHTML: "" }); await tick();
  out.created = calls.filter((c) => c[0] === "quiz_create").pop();
  // after one try only: "My weak spots" is there, not the default
  OPTIONS = { ...OPTIONS, weak: { ...OPTIONS.weak, attempts: 1 } };
  await A["qz-new"]({ dataset: { cid: "c1" } }); await tick();
  out.oneTry = shown.html;
  OPTIONS = { ...OPTIONS, weak: { missed: 0, attempts: 0, quizzes: 0, los: [], topics: [] } };
  await A["qz-new"]({ dataset: { cid: "c1" } }); await tick();
  out.noMisses = shown.html;
  // ---- the flashcards: a missed multiple-choice question, marked by the app
  shown.html = "";
  A["fc-study"]({ dataset: { cid: "c1" } }); await tick();
  out.mcCard = shown.html;
  out.keyPicked = St.mistakes.studyKey({ key: "2", target: {}, preventDefault() {} }, P().study);
  out.mcPicked = shown.html;
  out.keyNext = St.mistakes.studyKey({ key: "Enter", target: {}, preventDefault() {} }, P().study); await tick();
  out.mcGrade = calls.filter((c) => c[0] === "cards_grade").map((c) => c[3]);
  out.plainCard = shown.html;
  // a card's menu: edit, set aside, delete
  A["fc-card-menu"]({ dataset: {} });
  out.menu = menus.pop().map((x) => (x === "-" ? "-" : x.id));
  A["fc-card-menu"]({ dataset: {} });
  const items = menus.pop();
  items.find((x) => x.id === "edit").run();
  out.editForm = shown.html;
  fields["#fc-ed-q"] = { value: "Bayes' theorem?" }; fields["#fc-ed-a"] = { value: "P(B|A) = P(A|B)P(B)/P(A)" };
  await A["fc-edit-save"](); await tick();
  out.edited = [calls.filter((c) => c[0] === "cards_edit").pop(), P().study.queue[P().study.pos].q];
  A["fc-card-menu"]({ dataset: {} });
  await menus.pop().find((x) => x.id === "aside").run(); await tick();
  out.aside = [calls.filter((c) => c[0] === "cards_edit").pop(), P().study.queue.length, shown.html.includes("Done for today")];
  await A["fc-bring-back"]({ dataset: { cid: "c1" } });
  out.bringBack = calls.filter((c) => c[0] === "cards_bring_back").pop();
  // ---- in Turkish
  t.load({ lang: "tr", table: %(table)s });
  out.trTab = St.practice.tab(S.courses[0]);
  process.stdout.write(JSON.stringify({ ...out, toasts }), () => process.exit(0));
})();
"""


def run(tmp_path, script, **pages):
    f = tmp_path / "page.js"
    f.write_text(script % {"i18n": json.dumps(str(WEB / "i18n.js")), **{k: json.dumps(str(WEB / v)) for k, v in pages.items()},
                           "table": json.dumps(i18n.table("tr"), ensure_ascii=False)}, encoding="utf-8")
    done = subprocess.run(["node", str(f)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@node
def test_the_mistake_loop_on_the_page(tmp_path):
    out = run(tmp_path, PAGE, practice="practice.js", mistakes="mistakes.js")
    end = out["end"]
    assert "Open a question to see your answer beside the right one" in end and end.count('data-act="mq-row"') == 3
    # saved with what was typed and who marked it
    assert out["saved"] == [[{"got": 0, "chose": 1, "n": 1, "skipped": False, "text": "", "mark": "app"},
                             {"got": 0, "text": "it adds up the parts", "n": 2, "chose": None, "skipped": False, "mark": "self"},
                             {"got": 0, "skipped": True, "chose": None, "n": 3, "text": "", "mark": "app"}]]
    assert out["carded"] is None
    assert "3 missed questions join your flashcards: they come back tomorrow." in out["cardedLine"]
    assert 'data-act="mq-uncard" data-cid="c1" data-ids="qx1,qx2,qx3"' in out["cardedLine"] and "Undo" in out["cardedLine"]
    row1 = out["row1"]
    assert "Your answer" in row1 and "<b>B</b> P(A)P(B)" in row1 and "Right answer" in row1 and "<b>A</b> P(A and B)/P(B)" in row1
    assert "Why yours is wrong:</b> Only when independent." in row1 and "Why:</b> The definition." in row1
    assert 'data-act="pr-page"' in row1 and "See the page" in row1 and "From Week05.pdf, page 12" in row1
    assert "Remove this question" in row1 and 'aria-expanded="true"' in row1
    row2 = out["row2"]
    assert "it adds up the parts" in row2 and "A good answer" in row2 and "Replay where it was taught" in row2
    assert "(found by its words)" in row2
    m = out["moment"]
    assert "Where it was taught" in m and 'src="file:///s/7.jpg"' in m and "Slide 7 · 13:20–15:50 · Total probability" in m
    assert 'class="mq-line at"' in m and "This is the total probability rule." in m and "Play this part (13:20–15:50)" in m
    assert "Back to your results" in m and "Not the right place" in m
    assert out["momentCall"] == ["practice_moment", "c1", "c1|2026-10-21|lecture", 1, 845]
    assert out["played"] == [["c1|2026-10-21|lecture", 1, 800, 950, "Back to your results"]] and out["closed"] == ""
    assert "qz-end" in out["back"] and "You got 0 of 3" in out["back"]           # the results again, after the clip
    assert out["notHere"] == ["quiz_question", "q1", 2, "not_here"] and "qz-end" in out["afterNotHere"]
    assert "You said its source was the wrong place" in out["afterNotHere"]
    assert "Removed from this quiz." in out["removed"] and 'data-only="1,2"' in out["removed"]
    assert out["removeToast"][:3] == ["Removed: it won't be asked again.", "success", "Undo"]
    assert out["uncard"] == ["mistakes_uncard", "c1", ["qx1", "qx2"]]
    # the course tab
    tab = out["tab"]
    assert "Mistakes to go over" in tab and tab.count('class="mq ') == 5 and "Show all 6" in tab and "Quiz my weak spots" in tab
    assert 'class="dim sm mq-meta" title="Bayes' in tab                      # the date beside it, the quiz on hover
    assert "Where you lose points" in tab and "1.5 of 3 right · from 1 quiz" in tab and "Losing points" in tab and "Getting there" in tab
    assert "By topic" in tab and "Not tested yet" in tab and "Quiz this LO" in tab and "LO5" in tab
    assert "2 cards are set aside." in tab and "Bring them back" in tab
    assert tab.index("Practice quizzes") < tab.index("Mistakes to go over") < tab.index("Flashcards")
    assert out["tabAll"].count('class="mq ') == 6 + 1 and "Show fewer" in out["tabAll"]       # + LO3's missed one, opened
    # the quiz dialog: an outcome to quiz; "My weak spots", the default after two tries
    assert "LO5: use random variables" in out["loDialog"] and 'data-val="weak"' in out["loDialog"]
    assert 'class="cp-mode on" data-act="qz-mode" data-val="weeks"' in out["loDialog"]
    w = out["weakDialog"]
    assert 'class="cp-mode on" data-act="qz-mode" data-val="weak"' in w and "Written around what you missed" in w
    assert "3 questions you missed · learning outcomes where you lose points: LO3 · topics: Bayes" in w
    assert 'cp-modes four' in w and "Pick at least one file or lecture." not in w
    assert out["weakEstimate"][2]["weak"] is True and out["created"][2]["weak"] is True and out["created"][2]["files"] == []
    assert 'data-val="weak"' in out["oneTry"] and 'class="cp-mode on" data-act="qz-mode" data-val="weak"' not in out["oneTry"]
    assert 'data-val="weak"' not in out["noMisses"] and "cp-modes four" not in out["noMisses"]
    # a missed multiple-choice question in the flashcards: its options, the app marks it
    mc = out["mcCard"]
    assert "A question you missed · Bayes" in mc and 'data-act="fc-pick" data-i="1"' in mc and "Show the answer" not in mc
    assert 'data-act="fc-card-menu"' in mc and "Keys: 1 to 4 pick an answer" in mc
    assert out["keyPicked"] is True and "Not quite. The answer is A." in out["mcPicked"] and "Why B is wrong:" in out["mcPicked"]
    assert "The app marks it Again: it comes back soon." in out["mcPicked"] and "Next card" in out["mcPicked"]
    assert out["keyNext"] is True and out["mcGrade"] == ["again"]
    assert "Show the answer" in out["plainCard"] and 'data-act="fc-card-menu"' in out["plainCard"]
    assert out["menu"] == ["edit", "aside", "-", "del"]
    assert "Edit this card" in out["editForm"] and 'id="fc-ed-q"' in out["editForm"] and "Bayes?" in out["editForm"]
    assert out["edited"] == [["cards_edit", "c1", "k2", {"q": "Bayes' theorem?", "a": "P(B|A) = P(A|B)P(B)/P(A)"}], "Bayes' theorem?"]
    assert out["aside"][0] == ["cards_edit", "c1", "k2", {"off": "aside"}] and out["aside"][2]
    assert out["bringBack"] == ["cards_bring_back", "c1"]
    # in Turkish
    tr = out["trTab"]
    for bit in ("Üzerinden geçmen gereken hatalar", "Nerede puan kaybediyorsun"):
        assert bit in tr, bit


LO_PAGE = PAGE.split("(async () => {")[0] + r"""
(async () => {
  OPTIONS = { ...OPTIONS, weak: { missed: 0, attempts: 0, quizzes: 0, los: [], topics: [] },
    assessments: [{ key: "quiz:4", kind: "quiz", no: 4, name: "Quiz 4", week: 6, weeks: [5], los: [3], ahead: true, files: [] },
                  { key: "final:0", kind: "final", no: 0, name: "Final", week: 14, weeks: [5], los: [3, 4], ahead: true, files: [] }] };
  // on the Practice tab: the weeks (not "For Quiz 4", the next one), the outcome sent
  await A["mq-quiz-lo"]({ dataset: { cid: "c1", no: "5", text: "use random variables" } }); await tick();
  out.tab = shown.html;
  await A["qz-go"]({ dataset: {}, innerHTML: "" }); await tick();
  out.tabCreated = calls.filter((c) => c[0] === "quiz_create").pop();
  // in the Final's Exam room: for the Final, the outcome sent
  await A["mq-quiz-lo"]({ dataset: { cid: "c1", no: "5", text: "use random variables", key: "final:0" } }); await tick();
  out.room = shown.html;
  await A["qz-go"]({ dataset: {}, innerHTML: "" }); await tick();
  out.roomCreated = calls.filter((c) => c[0] === "quiz_create").pop();
  process.stdout.write(JSON.stringify(out), () => process.exit(0));
})();
"""


@node
def test_quiz_this_lo_quizzes_that_outcome_not_the_next_assessments(tmp_path):
    """"Quiz this LO" opened the dialog set to the next assessment with its outcomes: LO5 was left as free text."""
    out = run(tmp_path, LO_PAGE, practice="practice.js", mistakes="mistakes.js")
    assert 'class="cp-mode on" data-act="qz-mode" data-val="weeks"' in out["tab"]
    sc = out["tabCreated"][2]
    assert (sc["focus"], sc["los"], sc["weeks"]) == ("", [5], [5]) and sc["topics"] == "LO5: use random variables"
    assert 'class="cp-mode on" data-act="qz-mode" data-val="next"' in out["room"] and "For Final" in out["room"]
    sc = out["roomCreated"][2]
    assert (sc["focus"], sc["los"]) == ("Final", [5])


FOUND_PAGE = PAGE.split("(async () => {")[0] + r"""
(async () => {
  A["qz-play"]({ dataset: { id: "q1" } }); await tick();
  A["qz-start"]();
  A["qz-pick"]({ dataset: { i: "0" } }); A["qz-check"](); out.first = shown.html; A["qz-next"]();
  P().run.text = "the denominator"; A["qz-check"](); out.second = shown.html;
  process.stdout.write(JSON.stringify(out), () => process.exit(0));
})();
"""


@node
def test_the_player_says_a_source_was_found_by_its_words(tmp_path):
    """The app's and a chat's quizzes keep "found" on the question, not its source: the player showed such a source
    as if the AI had cited it (a pasted quiz and the Mistakes list said "found by its words")."""
    out = run(tmp_path, FOUND_PAGE, practice="practice.js", mistakes="mistakes.js")
    assert "From Week05.pdf, page 12" in out["first"] and "(found by its words)" not in out["first"]
    assert "From the lecture of" in out["second"] and "(found by its words)" in out["second"]


PLAYER = r"""
globalThis.window = globalThis;
const store = {};
globalThis.localStorage = { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } };
const h = {};
const v = { currentTime: 0, duration: NaN, paused: true, readyState: 0, videoWidth: 0, playbackRate: 1, error: null, parentNode: null, _src: null,
  setAttribute() {}, getAttribute(n) { return n === "src" ? this._src : null; }, removeAttribute(n) { if (n === "src") this._src = null; },
  set src(u) { this._src = u; }, get src() { return this._src; }, load() {}, play() { this.paused = false; return Promise.resolve(); },
  pause() { this.paused = true; (h.pause || []).forEach((f) => f({})); }, addEventListener(type, fn) { (h[type] = h[type] || []).push(fn); }, remove() {} };
const fire = (type) => (h[type] || []).forEach((f) => f({ type }));
const els = { "#view": { innerHTML: "" }, "#lp-clip": { innerHTML: "" } };
globalThis.document = { documentElement: {}, createElement: () => v, querySelector: (s) => els[s] || null, addEventListener() {}, body: { contains: () => true } };
window.addEventListener = () => {};
require(%(i18n)s);
const calls = [], backs = [];
const DAY = { day_key: "c|2026-11-04|lecture", title: "EEE 215 · Lecture", date_label: "4 Nov", course: { id: "c", code: "EEE 215" },
  parts: [{ index: 1, id: "d1", viewer_url: "", paragraphs: [{ t: 800, e: 840, ts: "13:20", text: "Now the denominator." }] }] };
const S = { view: { name: "course", courseId: "c", tab: "practice" }, day: null, search: { items: [] } };
const St = window.Studia = { S, A: {}, views: {}, after: {}, crumbs: {}, onEvents: {}, mocks: {},
  I: (n) => `[${n}]`, SPIN: () => "~", esc: (s) => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;"), toast: () => {},
  call: (name, ...args) => { calls.push([name, ...args]); return Promise.resolve(name === "get_day" ? { ok: true, day: DAY }
    : name === "player_open" ? { ok: true, token: "tok", url: "http://127.0.0.1:5000/m/tok/lecture.mp4", kind: "video", start: 0, duration: 3000, delivery_id: "d1", screens: [] } : { ok: true }); },
  renderView() { const vw = S.view; if (St.views[vw.name]) { els["#view"].innerHTML = St.views[vw.name](vw); } }, renderTop() {},
  go(view) { S.view = view; St.renderView(); } };
require(%(player)s);
const A = St.A, tick = () => new Promise((r) => setTimeout(r, 5)), out = {};
(async () => {
  St.player.open(DAY.day_key, 1, 800, { until: 950, back: { label: "Back to your results", run: () => backs.push("results") } });
  await tick(); await tick();
  out.src = v.src;
  out.view = els["#view"].innerHTML;
  v.readyState = 4; v.duration = 3000; fire("loadedmetadata");
  v.currentTime = 900; fire("timeupdate");
  out.before = [v.paused, els["#lp-clip"].innerHTML];
  v.currentTime = 951; fire("timeupdate");
  out.after = [v.paused, els["#lp-clip"].innerHTML];
  A["lp-clip-more"]();
  out.more = [v.paused, els["#lp-clip"].innerHTML];
  v.currentTime = 990; fire("timeupdate");
  out.goesOn = v.paused;
  A["lp-clip-back"]();
  out.back = [backs, S.view.name, !!St.player.state];
  // a lecture played without a clip: no clip bar, Back as before
  S.view = { name: "course", courseId: "c", tab: "practice" };
  St.player.open(DAY.day_key, 1, 10); await tick(); await tick();
  out.plain = els["#view"].innerHTML.includes("lp-clip");
  A["lp-back"]();
  out.plainBack = backs.length;
  process.stdout.write(JSON.stringify(out), () => process.exit(0));
})();
"""


@node
def test_the_player_plays_a_clip_and_leads_back(tmp_path):
    out = run(tmp_path, PLAYER, player="player.js")
    assert out["src"] == "http://127.0.0.1:5000/m/tok/lecture.mp4#t=800"
    assert "Playing the part where it was taught (13:20–15:50). It stops by itself." in out["view"]
    assert 'data-act="lp-clip-back"' in out["view"] and "Back to your results" in out["view"]
    assert out["before"][0] is False
    assert out["after"][0] is True and "That was the part where it was taught (13:20–15:50)." in out["after"][1]
    assert "Keep playing" in out["after"][1]
    assert out["more"][0] is False and "Keep playing" not in out["more"][1] and "Back to your results" in out["more"][1]
    assert out["goesOn"] is False                                   # on from there: it doesn't stop again
    assert out["back"] == [["results"], "course", False]
    assert out["plain"] is False and out["plainBack"] == 1
