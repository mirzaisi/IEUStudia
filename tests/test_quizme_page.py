"""Quiz me from where you are, on the page (app.js, course.js, chatactions.js and practice.js run in node with the
browser preview's stand-ins): a lecture's "Quiz me" opens the quiz dialog set to that lecture and its "Practise N
cards" studies them in the deck's own schedule, a week card's menu, ticking stressed moments for a quiz, studying
one week's cards, and "/quiz" in a chat (its card only once the answer is done, "Quiz me on this" under the last
answer)."""

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
const el = () => ({ innerHTML: "", style: {}, dataset: {}, classList: { toggle() {}, add() {}, remove() {}, contains: () => false }, scrollTo() {},
  querySelector: () => null, querySelectorAll: () => [], addEventListener() {}, remove() {}, focus() {}, contains: () => false, scrollTop: 0 });
const toasts = [];
const els = { "#top": el(), "#view": el(), "#overlay": el(), "#toasts": { appendChild: (b) => toasts.push(b.innerHTML) } };
globalThis.document = new Proxy({}, { get: (t, k) => k === "compatMode" ? "CSS1Compat" : k === "activeElement" ? null : k === "hidden" ? false
  : k === "createElement" ? () => ({ ...el(), querySelector: () => el() })          // (a toast's button)
  : k === "querySelector" ? (s) => (s in els ? els[s] : stub())
  : k === "getElementById" ? (id) => els["#" + id] || null : k === "querySelectorAll" ? () => []
  : k === "addEventListener" ? () => {} : stub() });
globalThis.localStorage = { getItem: () => null, setItem() {} };
globalThis.navigator = { userAgent: "node", platform: "Win32" };
globalThis.CSS = { escape: (s) => s };
globalThis.addEventListener = () => {}; globalThis.matchMedia = () => ({ matches: false, addEventListener() {} });
globalThis.requestAnimationFrame = (f) => f();
const later = setTimeout;
globalThis.setInterval = () => 0; globalThis.setTimeout = () => 0;
for (const f of ["i18n.js", "match.js", "icons.js", "app.js", "course.js", "studypack.js", "chatactions.js", "practice.js"]) require(web + "/" + f);
const St = globalThis.Studia, S = St.S, A = St.A;
const tick = (ms = 30) => new Promise((r) => later(r, ms));
const calls = [];
const spy = (name, answer) => { St.mocks[name] = (...a) => { calls.push([name, ...a]); return typeof answer === "function" ? answer(...a) : answer; }; };
const called = (name) => calls.filter((c) => c[0] === name).map((c) => c.slice(1));
const overlay = () => els["#overlay"].innerHTML;
const DAY = "c1|2026-10-13|lecture", DAY2 = "c1|2026-10-20|lecture";
const OPTS = { ok: true, model: "sonnet", ai: { available: true }, assessments: [{ name: "Quiz 2", kind: "quiz", ahead: true, weeks: [5, 6], los: [] }],
  weeks: [{ no: 5, topic: "Bayes", files: [{ id: "f5", name: "W5.pdf", ready: true, pages: 3 }],
            days: [{ day_key: DAY, date: "2026-10-13", label: "13 Oct", kind: "Lecture", ready: true, mine: false }] },
          { no: 6, topic: "Random variables", files: [], days: [{ day_key: DAY2, date: "2026-10-20", label: "20 Oct", kind: "Lecture", ready: true, mine: true }] }] };
spy("quiz_options", OPTS);
spy("quiz_estimate", { ok: true, estimate: null });
spy("quiz_create", (cid, sc) => ({ ok: true, plan: { id: "q9", status: "queued", scope: sc } }));
const out = {};
(async () => {
  S.mode = "app"; S.view = { name: "home" }; S.boot = {}; S.settings = {}; S.system.ai = { available: true, provider: "claude", can_plans: true };
  S.courses = [{ id: "c1", code: "MATH 240", name: "Probability", display_name: "Probability", days: [{ day_key: DAY, date: "2026-10-13", any_done: true }],
    counts: { done: 1, total: 2, new: 0 }, content: {} }];
%(body)s
  process.stdout.write(JSON.stringify(out), () => process.exit(0));
})().catch((e) => { console.error(e); process.exit(1); });
"""


def _page(tmp_path, body):
    script = tmp_path / "page.js"
    script.write_text(HARNESS % {"web": json.dumps(WEB.as_posix()), "body": body}, encoding="utf-8")
    r = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


LECTURE = """
  const P = (t) => ({ t, e: t + 8, ts: "00:0" + Math.round(t / 60), text: "Passage at " + t, lang: "en", tag: "", star: false, tags: [], uncertain: false });
  const day = { day_key: DAY, title: "MATH 240 · Lecture", date_label: "13 October", subtitle: "", course: { id: "c1", code: "MATH 240", name: "Probability" },
    kind: "lecture", languages: { en: 1 }, highlights: [], marks: [], files: {}, screens_again: { current: null, waiting: [] },
    notes: [{ kind: "flashcards", label: "Flashcards", md: "Q: a\\nA: b", cards: [{ q: "a", a: "b" }, { q: "c", a: "d" }, { q: "e", a: "f" }] }],
    parts: [{ index: 1, id: "a", title: "Part 1", time_range: "11:00–11:15", viewer_url: "https://v/a", status: "done", slides: 0, warnings: [], anomalies: [], paragraphs: [P(5), P(40)] }] };
  S.view = { name: "day", dayKey: DAY, from: { name: "home" } }; S.day = day; S.dayFind = "";
"""


@node
def test_a_lecture_quizzes_and_studies_its_own_cards(tmp_path):
    out = _page(tmp_path, LECTURE + """
  St.renderView(); await tick();
  const html = els["#view"].innerHTML;
  out.quizFirst = html.indexOf('data-act="day-quiz"') >= 0 && html.indexOf('data-act="day-quiz"') < html.indexOf('data-act="menu-copy"');
  out.quizPrimary = /class="btn primary sm" data-act="day-quiz"/.test(html) && /class="btn ghost sm" data-act="menu-copy"/.test(html);
  out.practise = /data-act="cards-practise"[^>]*>[^<]*<[^>]*>[^<]*Practise 3 cards/.test(html) || html.includes("Practise 3 cards");
  // Quiz me: the dialog, set to this lecture (not your section's, still offered), then one click writes it
  A["day-quiz"](); await tick();
  out.dialog = overlay();
  await A["qz-go"]({ disabled: false, innerHTML: "" }); await tick();
  out.created = called("quiz_create");
  // Practise 3 cards: the deck's own session for this lecture (none in the deck: said so)
  spy("cards_session", { ok: true, cards: [], total: 0, aside: 0 });
  S.view = { name: "day", dayKey: DAY, from: { name: "home" } }; S.day = day;
  A["cards-practise"](); await tick();
  out.session = called("cards_session");
  out.toast = toasts.slice(-1)[0] || "";
  // no AI in the app: Copy for AI stays the first button
  S.system.ai = { available: false, provider: "" };
  St.renderView(); await tick();
  const plain = els["#view"].innerHTML;
  out.noAI = { quiz: plain.includes('data-act="day-quiz"'), copyPrimary: /class="btn primary sm" data-act="menu-copy"/.test(plain) };
""")
    assert out["quizFirst"] and out["quizPrimary"] and out["practise"]
    assert 'cp-mode on" data-act="qz-mode" data-val="lecture"' in out["dialog"]
    assert f'<option value="c1|2026-10-13|lecture" selected>' in out["dialog"] and "Write the quiz" in out["dialog"]
    sc = out["created"][0][1]
    assert out["created"][0][0] == "c1" and sc["days"] == ["c1|2026-10-13|lecture"] and sc["files"] == [] and sc["weeks"] == [5]
    assert sc["questions"] == 10 and sc["kind"] == "quiz"
    assert out["session"] == [["c1", [], ["c1|2026-10-13|lecture"]]]
    assert "This lecture&#39;s cards aren&#39;t in your deck yet." in out["toast"] or "aren't in your deck yet" in out["toast"]
    assert out["noAI"] == {"quiz": False, "copyPrimary": True}


WEEKS = """
  const mat = (id, downloaded) => ({ id, title: id, file_name: id + ".pdf", ext: "pdf", kind: "pdf", downloaded, gone: false, outdated: false, is_new: false, pages: 2, text: "" });
  const d = { id: "c1", code: "MATH 240", outcomes_en: [], outcomes_tr: [], general: [], content_at: "x",
    alerts: { items: [], counts: {}, plan: [{ kind: "quiz", no: 2, name: "Quiz 2", week: 5, los: [], sources: [] }] },
    weeks: [{ no: 5, start: "2026-10-12", end: "2026-10-18", topic_en: "Bayes", topic_tr: "", readings: "", past: true, current: false, exam: false,
              units: [{ id: "u5", title: "Hafta 5", materials: [mat("m1", true), mat("m2", false)] }],
              days: [{ day_key: DAY, date: "2026-10-13", label: "13 Oct", kind_label: "Lecture", parts: 2, transcribed: true, any_done: true, mine: true },
                     { day_key: "c1|2026-10-15|lecture", date: "2026-10-15", label: "15 Oct", kind_label: "Lecture", parts: 1, transcribed: false, any_done: false, mine: true }] },
            { no: 7, start: "2026-10-26", end: "2026-11-01", topic_en: "Unread", topic_tr: "", readings: "", past: true, current: false, exam: false,
              units: [{ id: "u7", title: "Hafta 7", materials: [mat("m7", false)] }], days: [] },
            { no: 9, start: "2026-11-09", end: "2026-11-15", topic_en: "Later", topic_tr: "", readings: "", past: false, current: false, exam: false, units: [], days: [] }] };
  S.cd.c1 = d; S.weeksOpen = { c1: new Set([5]) };
  S.view = { name: "course", courseId: "c1", tab: "weeks" };
"""


@node
def test_a_week_card_has_a_menu_and_its_quiz_pill_opens_get_ready(tmp_path):
    out = _page(tmp_path, WEEKS + """
  const html = St.weeksHTML(S.courses[0], d);
  out.menus = (html.match(/data-act="menu-week"/g) || []).length;
  out.pill = /<button class="pill due kind-quiz pill-btn" data-act="ready-open" data-cid="c1" data-name="Quiz 2"/.test(html);
  let items = null;
  St.menu = (anchor, list) => { items = list; };
  const seen = {};
  St.practice.quizDialog = (cid, p) => { seen.quiz = [cid, p]; };
  St.practice.study = (cid, o) => { seen.study = [cid, o]; };
  St.chatPicker = (cid, p) => { seen.ask = [cid, p.days]; };
  St.runAction = (a, cid) => { seen.tx = [a, cid]; };
  spy("materials_download", { ok: true });
  A["menu-week"]({ dataset: { cid: "c1", no: "5" } });
  out.labels = items.map((it) => (it === "-" ? "-" : it.label));
  items.filter((it) => it !== "-").forEach((it) => it.run());
  out.seen = seen;
  out.download = called("materials_download");
  // a week with nothing to read yet: nothing to quiz on, its file to download
  A["menu-week"]({ dataset: { cid: "c1", no: "7" } });
  out.unread = items.map((it) => (it === "-" ? "-" : it.label));
  // without ChatGPT or Claude: no quiz, no chat
  S.system.ai = { available: false };
  A["menu-week"]({ dataset: { cid: "c1", no: "5" } });
  out.plain = items.map((it) => (it === "-" ? "-" : it.label));
""")
    assert out["menus"] == 2 and out["pill"]                  # a week with nothing yet has no menu
    assert out["unread"] == ["Study this week's cards", "-", "Download 1 file"]
    assert out["labels"] == ["Quiz me on this week", "Study this week's cards", "Ask about this week", "-", "Download 1 file",
                             "Transcribe 1 lecture"]
    assert out["seen"]["quiz"] == ["c1", {"mode": "weeks", "weeks": [5]}]
    assert out["seen"]["study"] == ["c1", {"weeks": [5]}]
    assert out["seen"]["ask"] == ["c1", ["c1|2026-10-13|lecture"]]
    assert out["seen"]["tx"] == [{"action": "transcribe", "weeks": [5]}, "c1"]
    assert out["download"] == [[["m2"]]]
    assert out["plain"] == ["Study this week's cards", "-", "Transcribe 1 lecture"]          # (m2 is being downloaded)


PRACTICE = """
  const item = (id, day_key, day, score, top) => ({ id, kind: "stressed", text: "Moment " + id, hl: [0, 6], day, day_key, part: 1, t: 60, ts: "01:00", n: 1, score, top });
  const COURSE = { ok: true, deck: { total: 9, due: 2, new: 1, today: 3, learnt: 4, lecture: 9, files: 0, lecture_days: 2, weeks: [5, 6],
      week_counts: { "5": { total: 5, today: 3 }, "6": { total: 4, today: 0 } } },
    quizzes: [{ id: "qc", course_id: "c1", title: "From the chat", status: "done", weeks: [5], days: [DAY], focus: "", questions: 5, source: "chat", tries: 0, created_at: "2026-11-19T10:00:00" }],
    card_sets: [],
    stressed: { total: 3, counts: { exam: 0, stressed: 3, not_needed: 1 }, untranscribed: 0, summary: null,
      weeks: [{ no: 6, topic: "RV", items: [item("s3", DAY2, "2026-10-20", 1.5, true)] },
              { no: 5, topic: "Bayes", items: [item("s1", DAY, "2026-10-13", 3, true), item("s2", DAY, "2026-10-13", 0.5, false)] }],
      not_needed: [{ ...item("s9", DAY, "2026-10-13", 1, false), kind: "not_needed", week: 5 }] } };
  spy("practice_course", COURSE);
  spy("cards_session", { ok: true, cards: [], total: 4 });
  S.view = { name: "course", courseId: "c1", tab: "practice" };
  St.onGo.course(S.view); await tick();
"""


@node
def test_ticked_moments_become_a_quiz_and_one_week_s_cards_are_studied(tmp_path):
    out = _page(tmp_path, PRACTICE + """
  let tab = St.practice.tab(S.courses[0]);
  out.boxes = (tab.match(/data-act="st-pick"/g) || []).length;          // s3 and s1 stand out; s2 said in passing, s9 not needed
  out.button = tab.includes("Quiz me on these") && tab.includes("From a chat");
  out.weekBtn = tab.includes('data-act="menu-fc-week"');
  // nothing ticked: the moments that stand out, the most important first
  A["st-quiz"]({ dataset: { cid: "c1" } }); await tick();
  out.all = overlay();
  // one ticked: only it
  A["st-pick"]({ checked: true, dataset: { cid: "c1", id: "s1" } });
  tab = St.practice.tab(S.courses[0]);
  out.ticked = tab.includes("Quiz me on 1 moment") && tab.includes('st-row on');
  A["st-quiz"]({ dataset: { cid: "c1" } }); await tick();
  out.one = overlay();
  await A["qz-go"]({ disabled: false, innerHTML: "" }); await tick();
  out.created = called("quiz_create").map((c) => c[1]);
  out.cleared = St.practice.tab(S.courses[0]).includes("Quiz me on these");
  // Study one week: each week with what it has today, then that week's session
  let items = null;
  St.menu = (anchor, list) => { items = list; };
  A["menu-fc-week"]({ dataset: { cid: "c1" } });
  out.weeks = items.map((it) => [it.label, it.desc]);
  items[1].run(); await tick();
  out.session = called("cards_session");
  out.toast = toasts.slice(-1)[0] || "";
  // nothing due from it today: its toast offers them all (a cram session: the schedule left alone, the Exam room's)
  await A["fc-cram"]({ dataset: { cid: "c1", weeks: "6" } }); await tick();
  out.cram = called("cards_session").slice(-1)[0];
""")
    assert out["boxes"] == 2 and out["button"] and out["weekBtn"]
    assert 'cp-mode on" data-act="qz-mode" data-val="stressed"' in out["all"] and "2 moments the lecturer stressed" in out["all"]
    assert out["all"].index("Moment s1") < out["all"].index("Moment s3")              # the most important first
    assert out["ticked"] and "1 moment the lecturer stressed" in out["one"] and "Moment s3" not in out["one"]
    sc = out["created"][0]
    assert sc["stressed_ids"] == ["s1"] and sc["days"] == ["c1|2026-10-13|lecture"] and sc["weeks"] == [5] and sc["files"] == []
    assert out["cleared"]
    assert out["weeks"] == [["Week 5", "3 to study today"], ["Week 6", "4 cards, done for today"]]
    assert out["session"] == [["c1", [6], []]] and "Nothing from week 6 to study today." in out["toast"]
    assert 'data-act="fc-cram" data-cid="c1" data-weeks="6"' in out["toast"] and "Go through them all" in out["toast"]
    assert out["cram"] == ["c1", [6], [], True]


@node
def test_a_quiz_in_a_chat_shows_as_a_card_only_when_it_is_done(tmp_path):
    out = _page(tmp_path, """
  const block = "Here is your quiz.\\n```ieustudia-quiz\\n{\\"questions\\": [{\\"question\\": \\"What is P(A|B)?\\", \\"answer\\": \\"B\\"}";
  const msgs = [{ seq: 1, role: "user", text: "Explain Bayes", status: "done", usage: {} },
    { seq: 2, role: "assistant", text: "Bayes reverses a conditional.", status: "done", usage: {} },
    { seq: 3, role: "user", text: "/quiz 5", status: "done", usage: {} },
    { seq: 4, role: "assistant", text: block, status: "streaming", usage: {} }];
  S.chat.data = { id: "ch", course_id: "c1", messages: msgs, days: [] };
  const writing = St.actionCards(block, 4, true, msgs[3]);
  out.writing = [writing.text, writing.html.includes("Writing your quiz"), writing.html.includes("P(A|B)")];
  msgs[3] = { ...msgs[3], text: block + "]}\\n```", status: "done", usage: { quiz: { id: "pq", title: "Bayes", questions: 5 } } };
  const done = St.actionCards(msgs[3].text, 4, false, msgs[3]);
  out.done = [done.text, /data-act="qz-play" data-id="pq"/.test(done.html), done.html.includes("5 questions · saved to Practice")];
  const bad = { ...msgs[3], text: block, usage: { quiz_error: "x" } };
  const failed = St.actionCards(bad.text, 4, false, bad);
  out.failed = [failed.text === block, failed.html.includes("could not be read")];
  // "Quiz me on this" only under the last finished answer, not under a "/quiz" one
  msgs.splice(2, 2);
  out.chip = [St.actionCards(msgs[1].text, 2, false, msgs[1]).html.includes('data-act="chat-quiz"'),
              St.actionCards(msgs[1].text, 2, true, { ...msgs[1], status: "streaming" }).html.includes("chat-quiz")];
  msgs.push({ seq: 3, role: "user", text: "more?", status: "done", usage: {} }, { seq: 4, role: "assistant", text: "Yes.", status: "done", usage: {} });
  out.chipOld = St.actionCards(msgs[1].text, 2, false, msgs[1]).html.includes("chat-quiz");
  A["chat-suggest"] = (el) => { out.sent = el.dataset.text; };
  A["chat-quiz"]();
  // "/quiz 5" is sent to the AI; with the offline model it is not, and it says why
  out.slash = [St.slashCommand("/quiz 5"), St.slashCommand("/plan quiz 2")];
  S.system.ai = { available: true, provider: "local", can_plans: false };
  out.offline = St.slashCommand("/quiz");
  out.toast = toasts.slice(-1)[0] || "";
""")
    assert out["writing"] == ["Here is your quiz.", True, False]
    assert out["done"] == ["Here is your quiz.", True, True]
    assert out["failed"] == [True, True]
    assert out["chip"] == [True, False] and out["chipOld"] is False and out["sent"] == "/quiz"
    assert out["slash"] == [False, True] and out["offline"] is True and "Quizzes need ChatGPT or Claude" in out["toast"]


@node
def test_a_new_chat_s_practice_questions_are_a_quiz_and_a_failed_one_is_tried_again(tmp_path):
    """A new chat's suggestion 'Give me 5 practice questions with answers' wrote a wall of text next to the playable
    quiz: it is now a /quiz (not with the offline model). A quiz stopped or unread has Try again (the same /quiz) under
    the last answer, and a saved quiz's answer has no Copy (its text is the quiz's JSON)."""
    out = _page(tmp_path, """
  els["#chat-head"] = {}; els["#chat-msgs-inner"] = {};
  S.chat.draft = { course_id: "c1", days: [DAY], labels: [{ key: DAY, n: 1, label: "13 Oct", kind: "Lecture" }], model: "sonnet", tokens: 0, slidesTotal: 0 };
  S.chat.id = null; S.chat.data = null; S.view = { name: "chat" };
  const view = () => { St.renderView(); return els["#view"].innerHTML; };
  const sugg = (html) => [...html.matchAll(/data-act="chat-suggest" data-text="([^"]*)">([^<]*)</g)].map((m) => [m[1], m[2]]);
  out.one = sugg(view());
  S.chat.draft.labels.push({ key: DAY2, n: 2, label: "20 Oct", kind: "Lecture" });
  out.many = sugg(view());
  S.system.ai = { available: true, provider: "local", can_plans: false };
  out.offline = sugg(view());
  S.system.ai = { available: true, provider: "claude", can_plans: true };
  // a stopped quiz and an unread one under the last answer: Try again sends the same /quiz
  const msgs = [{ seq: 1, role: "user", text: "/quiz 3 capacitor energy", status: "done", usage: {} },
    { seq: 2, role: "assistant", text: "Here is your quiz.", status: "stopped", usage: {} }];
  S.chat.draft = null; S.chat.data = { id: "ch", course_id: "c1", title: "t", messages: msgs, days: [], model: "sonnet" };
  out.stopped = St.actionCards(msgs[1].text, 2, false, msgs[1]).html;
  msgs[1] = { ...msgs[1], status: "done", text: "Sure!\\n```ieustudia-quiz\\n{", usage: { quiz_error: "x" } };
  out.unread = St.actionCards(msgs[1].text, 2, false, msgs[1]).html;
  msgs.push({ seq: 3, role: "user", text: "thanks", status: "done", usage: {} }, { seq: 4, role: "assistant", text: "You're welcome.", status: "done", usage: {} });
  out.older = St.actionCards(msgs[1].text, 2, false, msgs[1]).html;
  // a saved quiz: its card, no Copy; any other answer keeps its Copy
  msgs.splice(2, 2);
  msgs[1] = { ...msgs[1], text: "Here.\\n```ieustudia-quiz\\n{}\\n```", usage: { quiz: { id: "pq", title: "Capacitors", questions: 3 } } };
  msgs.push({ seq: 3, role: "user", text: "And now?", status: "done", usage: {} }, { seq: 4, role: "assistant", text: "Plain.", status: "done", usage: {} });
  S.chat.id = "ch"; S.view = { name: "chat", chatId: "ch" };
  const html = view();
  out.saved = html.slice(html.indexOf('data-seq="2"'), html.indexOf('data-seq="3"'));
  out.plain = html.slice(html.indexOf('data-seq="4"'));
  const two = (m) => { msgs[1] = { ...msgs[1], ...m }; const h = view(); return h.slice(h.indexOf('data-seq="2"'), h.indexOf('data-seq="3"')); };
  out.stoppedCopy = two({ status: "stopped", text: "Here.\\n```ieustudia-quiz\\n{\\"questions\\": [", usage: {} }).includes("chat-copy");
  out.unreadCopy = two({ status: "done", text: "Here is a quiz in Markdown.", usage: { quiz_error: "x" } }).includes("chat-copy");
""")
    assert out["one"][-1] == ["/quiz 5", "Quiz me on this lecture: 5 scored questions."] and len(out["one"]) == 6
    assert out["many"][-1] == ["/quiz 8", "Quiz me on these lectures: 8 scored questions."]
    assert out["offline"][-1][0].startswith("Give me 8 practice questions") and out["offline"][-1][0] == out["offline"][-1][1]
    for html, said in ((out["stopped"], "Stopped before the quiz was finished."),
                       (out["unread"], "The quiz in this answer could not be read, so it wasn&#39;t saved.")):
        assert said in html.replace("'", "&#39;") and 'data-act="chat-suggest" data-text="/quiz 3 capacitor energy"' in html
        assert "Type /quiz" not in html and "Try again" in html
    assert "Type /quiz to try again" in out["older"] and "chat-suggest" not in out["older"]          # not the last one
    assert 'data-act="qz-play" data-id="pq"' in out["saved"] and "chat-copy" not in out["saved"]
    assert "chat-copy" in out["plain"] and out["stoppedCopy"] is False and out["unreadCopy"] is True


@node
def test_a_chat_begun_with_quiz_takes_its_quiz_s_name_in_the_header(tmp_path):
    """The header kept "/quiz 5" until the chat was opened again: the chat was renamed after its quiz on the app's
    side, and only the chat list was told."""
    out = _page(tmp_path, """
  els["#chat-head"] = {}; els["#chat-items"] = {};
  const msgs = [{ seq: 1, role: "user", text: "/quiz 5", status: "done", usage: {} },
    { seq: 2, role: "assistant", text: "", status: "streaming", usage: {} }];
  S.chat.data = { id: "ch", course_id: "c1", title: "/quiz 5", messages: msgs, days: [], model: "sonnet", running: true };
  S.chat.id = "ch"; S.chat.draft = null; S.view = { name: "chat", chatId: "ch" };
  const row = { id: "ch", course_id: "c1", course_code: "MATH 240", title: "/quiz 5", scope_label: "13 Oct · Lecture", model: "sonnet", updated_at: "2026-11-19T10:00:00" };
  St.Mock.chat_list = () => ({ ok: true, chats: [{ ...row }] });
  St.renderChatHead(); await tick();
  out.before = els["#chat-head"].innerHTML.includes("<h2 title=\\"/quiz 5\\">");
  row.title = "Bayes and conditional probability";          // the quiz is written and saved: the chat is named after it
  window.__lsEvent({ type: "chat", chat_id: "ch", seq: 2, state: "done", text: "Here it is.", usage: { quiz: { id: "pq", title: row.title, questions: 5 } } });
  await tick(60);
  out.after = els["#chat-head"].innerHTML;
  out.list = els["#chat-items"].innerHTML.includes("Bayes and conditional probability");
""")
    assert out["before"]
    assert '<h2 title="Bayes and conditional probability">' in out["after"] and "/quiz 5" not in out["after"]
    assert out["list"]
