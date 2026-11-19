"""Checked answers on the page (checks.js on practice.js and mistakes.js, in node with a stand-in for app.js): a worked
problem's "Final answer" box, checked by the app (right: full marks at once; not the same: the solution with "Partly
(method right)" and "I didn't know it"; a result it can't read: the student marks it, no error), a key the app could
not confirm said under its solution, the answers saved with the result typed and who marked them, the end screen's
"Mark my written answers" (one request, its wait, the marks coming back), each row saying who marked it with a way to
change the mark (on the end screen and in the Mistakes list), and the Turkish."""

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
const shown = { html: "" }, calls = [], toasts = [], fields = {};
const modalEl = { classList: { add() {} }, querySelector: () => null };
globalThis.document = { documentElement: {}, addEventListener() {}, querySelectorAll: () => [],
  querySelector: (sel) => (sel in fields ? fields[sel] : sel === "#overlay .modal" || sel === ".modal" ? (shown.html ? modalEl : null) : sel.startsWith("#overlay .") ? (shown.html.includes(sel.slice(10)) ? modalEl : null) : null),
  getElementById: () => null };
require(%(i18n)s);
const QUIZ = { id: "q1", course_id: "c1", title: "Circuits", status: "done", result: { title: "Circuits", removed: [], not_here: [],
  coverage: { span: "week 4", weeks: [{ no: 4, topic: "Dividers" }], kinds: { short: 1, problem: 4 }, files: [], lectures: [], los: [] },
  questions: [
    { n: 1, type: "problem", q: "Find $v_2$.", steps: ["Divide: $12 \\cdot 2/6$"], model: "$v_2 = 4$ V", verified: true, numeric: true, source: null },
    { n: 2, type: "problem", q: "Find the current.", steps: ["$I = V/R$"], model: "$I = 2$ mA", verified: true, numeric: true, source: null,
      check: { expr: "2", unit: "mA", of: "the current in $R_2$" } },
    { n: 3, type: "short", q: "Why is a voltmeter's resistance high?", model: "So it draws almost no current.", points: ["little current"], source: null },
    { n: 4, type: "problem", q: "Find the power.", steps: ["$P = I^2 R$"], model: "$P = 0.53$ W", unverified: true, source: null },
    { n: 5, type: "problem", q: "Find the time constant.", steps: ["$\\tau = RC$"], model: "$\\tau = 1$ ms", numeric: true, source: null }] } };
const VERDICT = { 1: { verdict: "right" }, 2: { verdict: "wrong", unit: false }, 5: { verdict: "unknown" } };
let MARKED = null;
const ANSWERS = {
  quiz_get: () => ({ ok: true, quiz: JSON.parse(JSON.stringify(QUIZ)), attempts: [] }),
  quiz_check_number: (id, n) => ({ ok: true, ...VERDICT[n] }),
  quiz_attempt: (id, a) => ({ ok: true, attempt: { id: "att1", ...a }, attempts: [], carded: [] }),
  quiz_mark: () => ({ ok: true, n: 4 }),
  quiz_mark_set: (plan, att, n, got) => ({ ok: true, attempt: { id: att, answers: [{ n, got, mark: "self", ai_got: 0.5, ai_why: "You left out the current.", text: "so it doesn't load it" }] } }),
  practice_course: () => ({ ok: true, deck: { total: 0, due: 0, new: 0, today: 0, weeks: [] }, quizzes: [], card_sets: [], stressed: { total: 0, counts: {}, weeks: [], not_needed: [] },
    mistakes: [{ plan_id: "q1", n: 3, title: "Circuits", at: "2026-11-19T10:00:00", misses: 1, q: QUIZ.result.questions[2],
      answer: { n: 3, got: 0.5, mark: "ai", ai_got: 0.5, ai_why: "You left out the current.", ai_by: "Claude", text: "so it doesn't load it", attempt: "att1" }, card_id: "qx3", due: "" }], weak: null }),
  practice_summary: () => ({ ok: true, due: 0, courses: [] }),
};
const S = { view: { name: "home" }, courses: [{ id: "c1", code: "EEE 215", display_name: "Circuits" }], system: { ai: { available: true, provider: "claude", label: "Claude" } }, settings: {} };
window.Studia = { S, views: {}, onEvents: {}, onGo: {}, crumbs: {}, after: {}, mocks: {}, A: {},
  call: (name, ...args) => { calls.push([name, ...args]); return Promise.resolve(ANSWERS[name] ? ANSWERS[name](...args) : { ok: true }); },
  esc: (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])),
  I: (n) => `[${n}]`, SPIN: () => "~", toast: (m, level, act) => toasts.push([m, level]), modal: (h) => { shown.html = h; },
  closeModal: () => { shown.html = ""; }, go: () => {}, renderView: () => {}, renderSidebar: () => {}, skeleton: () => "<skel>",
  mdToHtml: (s) => String(s), renderMath: () => {}, findCourse: (id) => S.courses.find((c) => c.id === id), hue: () => 200,
  aiName: () => "Claude", aiHint: () => "Set up AI", aiBtn: (ok) => (ok ? "" : "disabled"), isChatGPT: () => false, emptyHTML: (i, a) => a, fmtK: (n) => `${Math.round(n / 1000)}k`,
  openDay: () => Promise.resolve(), jumpTo: () => {}, menu: () => {}, ask: () => Promise.resolve(true), waitLine: (w) => `[wait ${w.phase}]`,
  player: { open: () => {} }, pack: { weekSpan: (w) => (w || []).map((n) => "week " + n).join(", ") } };
require(%(practice)s);
require(%(mistakes)s);
require(%(checks)s);
const St = window.Studia, A = St.A, tick = () => new Promise((r) => setTimeout(r, 5)), P = () => St.S.practice;
const btn = () => ({ tagName: "BUTTON", disabled: false, innerHTML: "" });
const out = {};
(async () => {
  A["qz-play"]({ dataset: { id: "q1" } }); await tick();
  A["qz-start"]();
  out.q1 = shown.html;
  // 1: "4 V" typed: the app says it is right; full marks, on to the next
  fields["#qz-text"] = { value: "12*2/6" }; fields["#qz-final"] = { value: " 4 V " };
  const b = btn();
  const checking = A["qz-check"](b);
  out.checking = b.innerHTML;
  await checking; await tick();
  out.right = shown.html;
  A["qz-next"]();
  // 2: "6 mA": not the same; the solution, "Partly (method right)" and "I didn't know it"
  out.q2 = shown.html;
  fields["#qz-text"] = { value: "I = 12/2k" }; fields["#qz-final"] = { value: "6 mA" };
  await A["qz-check"](btn()); await tick();
  out.wrong = shown.html;
  A["qz-self"]({ dataset: { got: "0.5" } });
  // 3: a short answer: no box, the student marks it
  out.q3 = shown.html;
  delete fields["#qz-final"]; fields["#qz-text"] = { value: "so it doesn't load the circuit" };
  await A["qz-check"](btn());
  out.self3 = shown.html;
  A["qz-self"]({ dataset: { got: "1" } });
  // 4: a key the app could not confirm: no box; its solution says so
  out.q4 = shown.html;
  fields["#qz-text"] = { value: "P = 0.47 W" };
  await A["qz-check"](btn());
  out.unconfirmed = shown.html;
  A["qz-self"]({ dataset: { got: "0" } });
  // 5: a result the app can't read: the student marks it, no error
  fields["#qz-text"] = { value: "" }; fields["#qz-final"] = { value: "about a millisecond" };
  await A["qz-check"](btn()); await tick();
  out.unknown = shown.html;
  A["qz-self"]({ dataset: { got: "1" } }); await tick(); await tick();
  out.saved = calls.filter((c) => c[0] === "quiz_attempt").map((c) => c[2].answers)[0];
  out.end = shown.html;
  out.checks = calls.filter((c) => c[0] === "quiz_check_number");
  // "Mark my written answers": one request, its wait, then the marks
  await A["ck-mark"](); await tick();
  out.markCall = calls.filter((c) => c[0] === "quiz_mark");
  out.marking = shown.html;
  St.onEvents.quizmark({ type: "quizmark", phase: "progress", attempt_id: "att1", plan_id: "q1", course_id: "c1", wait: { phase: "think" } });
  out.waiting = shown.html;
  St.onEvents.quizmark({ type: "quizmark", phase: "done", attempt_id: "att1", plan_id: "q1", course_id: "c1", marked: 4, sent: 4, score: 2.5,
    answers: [{ n: 1, got: 1, mark: "app", app_got: 1, final: "4 V" }, { n: 2, got: 0, mark: "ai", ai_got: 0, ai_why: "The resistance is 6 kΩ, not 2 kΩ.", ai_by: "Claude", final: "6 mA", app_got: 0 },
      { n: 3, got: 0.5, mark: "ai", ai_got: 0.5, ai_why: "You left out the current.", ai_by: "Claude", text: "so it doesn't load the circuit" },
      { n: 4, got: 0, mark: "ai", ai_got: 0, ai_why: "", ai_by: "Claude", text: "P = 0.47 W" }, { n: 5, got: 1, mark: "ai", ai_got: 1, ai_why: "", ai_by: "Claude", final: "about a millisecond" }] });
  out.marked = shown.html;
  A["mq-row"]({ dataset: { key: "q1|3" } });
  out.row3 = shown.html;
  await A["ck-set"]({ dataset: { cid: "c1", plan: "q1", attempt: "att1", n: "3", got: "1" } }); await tick();
  out.setCall = calls.filter((c) => c[0] === "quiz_mark_set").pop();
  out.row3after = shown.html;
  A["mq-row"]({ dataset: { key: "q1|4" } });
  out.row4 = shown.html;
  // an error is said once, and the button is back
  P().run.marking = null; P().run.answers[2] = { ...P().run.answers[2], ai_got: undefined, mark: "self" };
  delete P().run.answers[2].ai_got;
  await A["ck-mark"](); await tick();
  St.onEvents.quizmark({ type: "quizmark", phase: "error", attempt_id: "att1", plan_id: "q1", message: "You've hit your limit" });
  out.afterError = shown.html;
  // ---- the Mistakes list: who marked it, and the change
  S.view = { name: "course", courseId: "c1", tab: "practice" };
  St.onGo.course(S.view); await tick();
  out.tab = St.practice.tab(S.courses[0]);              // (its row q1|3 is still open from the end screen)
  // ---- in Turkish: a problem with its box
  t.load({ lang: "tr", table: %(table)s });
  A["qz-play"]({ dataset: { id: "q1" } }); await tick();
  A["qz-start"]();
  out.tr = shown.html;
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
def test_checked_answers_on_the_page(tmp_path):
    out = run(tmp_path, PAGE, practice="practice.js", mistakes="mistakes.js", checks="checks.js")
    q1 = out["q1"]
    assert 'id="qz-final"' in q1 and "Final answer" in q1 and "Your result, like 3.3 kΩ or 0.25" in q1
    assert "The app checks it for you. Leave it empty to mark it yourself." in q1
    assert 'data-act="qz-check">Check my answer<' in q1 and "Show the solution" not in q1
    assert "Checking…" in out["checking"]
    assert 'The app checks this result: <span class="md">the current in $R_2$</span>. Leave it empty' in out["q2"]
    right = out["right"]
    assert "Right! The app checked your result." in right and "Your result: 4 V" in right
    assert "The app checked the numbers of this solution." in right and 'data-act="qz-next"' in right
    assert "I got it" not in right and "How did you do?" not in right
    wrong = out["wrong"]
    # (it names the result its box checks: a problem asks for several, and the answer below has them all)
    assert 'Not the same as the result this box checks:</b> <span class="md">the current in $R_2$</span>' in wrong
    assert "Your result: 6 mA" in wrong and "Solution" in wrong
    assert "Partly (method right)" in wrong and "I didn't know it" in wrong and "I got it" not in wrong
    assert 'id="qz-final"' not in out["q3"] and "Check my answer" in out["q3"]           # a short answer: no box
    assert "I got it" in out["self3"] and "The app checked" not in out["self3"]
    assert 'id="qz-final"' not in out["q4"] and "Show the solution" in out["q4"]          # a key not confirmed: no box
    assert "The app could not confirm this answer. Check it against its source." in out["unconfirmed"]
    unknown = out["unknown"]
    assert "The app couldn't read your result as one number, so mark it yourself." in unknown
    assert "Your result: about a millisecond" in unknown and "I got it" in unknown and "Partly" in unknown
    assert not [x for x in out["toasts"] if x[1] == "error" and "read" in x[0]]          # no error for that
    assert [c[2:] for c in out["checks"]] == [[1, "4 V"], [2, "6 mA"], [5, "about a millisecond"]]
    saved = {a["n"]: a for a in out["saved"]}
    assert saved[1]["got"] == 1 and saved[1]["mark"] == "app" and saved[1]["final"] == "4 V" and saved[1]["text"] == "12*2/6"
    assert saved[2]["got"] == 0.5 and saved[2]["mark"] == "self" and saved[2]["app_got"] == 0 and saved[2]["final"] == "6 mA"
    assert saved[3]["mark"] == "self" and "final" not in saved[3]
    assert saved[5]["got"] == 1 and saved[5]["mark"] == "self" and saved[5]["final"] == "about a millisecond"
    end = out["end"]
    assert "Mark my written answers" in end and "compares your 4 answers with the model answers" in end
    assert out["markCall"] == [["quiz_mark", "q1", "att1", ""]]
    assert "Claude is marking your 4 written answers…" in out["marking"] and 'data-act="ck-stop"' in out["marking"]
    assert "[wait think]" in out["waiting"]
    marked = out["marked"]
    assert "Claude marked 4 written answers. Open one to see why, or change its mark." in marked
    assert 'data-act="ck-mark"' not in marked and "You got 2.5 of 5" in marked
    row3 = out["row3"]
    assert "Marked by Claude: <b>partly</b>" in row3 and "You left out the current." in row3
    assert row3.count('data-act="ck-set"') == 3 and 'data-got="0.5" aria-pressed="true"' in row3
    assert out["setCall"] == ["quiz_mark_set", "q1", "att1", 3, 1]
    assert "Marked by you: <b>right</b>" in out["row3after"]
    assert "The app could not confirm this answer." in out["row4"]
    assert "Mark my written answers" in out["afterError"] and ["You've hit your limit", "error"] in out["toasts"]
    tab = out["tab"]
    assert "Marked by Claude: <b>partly</b>" in tab and 'data-attempt="att1"' in tab and "Change the mark:" in tab
    tr = out["tr"]
    assert "Sonuç" in tr and "Sonucun, örneğin 3,3 kΩ ya da 0,25" in tr and "Cevabımı kontrol et" in tr
