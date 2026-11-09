"""4.0.0, what a review of the pages found (the page scripts run in node against stand-ins for the window): with the
Screens filter on, a moment opened from anywhere lands on its passage; the top bar's chip for a practice quiz or
cards says so and opens the course's Practice tab; closing flashcard study with Escape or a click beside it updates
what is left today; a quiz being written changes its row in place (the focus stays) and says what the AI does; the
Practice page counts the stressed moments that stand out; the Anki dialog has Cancel; before any course is synced,
New study plan, Search and Full sync say to sync first; What's new links the release page; the sign-in screen says
how to reach the maker. And Practice's save dialogs start in the course's folder with the file's type, as the Export
menu's do."""

import datetime as dt
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from studia import i18n
from studia.domain import Course

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
const els = { "#root": el(), "#top": el(), "#view": el(), "#overlay": el(), "#search-results": el(), "#cu-body": el(),
  "#toasts": { appendChild: (b) => toasts.push(b) } };
const doc = { activeElement: null };
globalThis.qsa = null;                 // a test's stand-in for the elements on the page (querySelectorAll)
globalThis.document = new Proxy({}, { get: (t, k) => k === "compatMode" ? "CSS1Compat"
  : k === "activeElement" ? doc.activeElement
  : k === "hidden" ? false
  : k === "createElement" ? () => ({ ...el(), querySelector: () => ({ addEventListener() {} }) })     // (a toast's button)
  : k === "querySelector" ? (s) => (s in els ? els[s] : stub())
  : k === "getElementById" ? (id) => els["#" + id] || null
  : k === "querySelectorAll" ? (s) => (globalThis.qsa ? globalThis.qsa(s) : [])
  : k === "addEventListener" ? () => {} : stub() });
globalThis.localStorage = { getItem: () => null, setItem() {} };
globalThis.navigator = { userAgent: "node", platform: "Win32" };
globalThis.CSS = { escape: (s) => s };
globalThis.addEventListener = () => {}; globalThis.removeEventListener = () => {};
globalThis.matchMedia = () => ({ matches: false, addEventListener() {} });
globalThis.requestAnimationFrame = (f) => f();
const later = setTimeout;
globalThis.setInterval = () => 0; globalThis.setTimeout = () => 0;      // no boot, no polling
for (const f of ["i18n.js", "match.js", "icons.js", "app.js", "contact.js", "effort.js", "course.js", "plans.js", "studypack.js",
                 "catchup.js", "waits.js", "practice.js"]) require(web + "/" + f);
const St = globalThis.Studia, S = St.S, A = St.A;
St.aiName = () => "Claude";
const turkish = %(tr)s;
const tick = (ms = 30) => new Promise((r) => later(r, ms));
const ev = (e) => window.__lsEvent(e);
const plain = (h) => String(h).replace(/&#39;/g, "'").replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
const told = () => toasts.splice(0).map((b) => b.className.replace("toast ", "") + ": " + plain(b.innerHTML.replace(/<[^>]*>/g, "").trim()));
const overlay = () => plain(els["#overlay"].innerHTML);
const out = {};
(async () => {
  S.mode = "app"; S.view = { name: "home" }; S.boot = {}; S.courses = []; S.system.ai = { available: true, provider: "claude" };
%(body)s
  process.stdout.write(JSON.stringify(out), () => process.exit(0));
})().catch((e) => { console.error(e); process.exit(1); });
"""


def run(tmp_path, body):
    script = tmp_path / "page.js"
    script.write_text(HARNESS % {"web": json.dumps(WEB.as_posix()), "body": body,
                                 "tr": json.dumps(i18n.table("tr"), ensure_ascii=False)}, encoding="utf-8")
    r = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


# ------------------------------------------------------------------ PT-1: the Screens filter and a moment opened
DAY = """
  const P = (t, slide) => ({ t, e: t + 8, ts: "00:00", text: "Passage at " + t + " about the circuit", lang: "en", tag: "", star: false,
    tags: [], uncertain: false, slide, slide_image: "file:///x/s-0" + slide + ".jpg", slide_w: 1600, slide_h: 900, slide_t0: null,
    slide_range: "", slide_title: "" });
  const day = { day_key: "c1|2026-10-13|lecture", title: "EEE 215 · Lecture", date_label: "13 October", subtitle: "",
    course: { id: "c1", code: "EEE 215", name: "Circuits" }, kind: "lecture", languages: { en: 1 }, highlights: [], marks: [],
    files: {}, notes: [], screens_again: { current: null, waiting: [] },
    parts: [{ index: 1, id: "a", title: "Part 1", time_range: "11:00–11:50", viewer_url: "https://v/a", status: "done", slides: 2,
      warnings: [], anomalies: [], paragraphs: [P(5, 1), P(40, 1), P(100, 2), P(200, 2)] }] };
  S.courses = [{ id: "c1", code: "EEE 215", name: "Circuits", display_name: "Circuits", counts: {}, days: [{ day_key: day.day_key, any_done: true }] }];
  // the transcript's paragraphs as the window has them: each one drawn, with what was flashed and scrolled to
  const flashed = [];
  globalThis.qsa = (sel) => sel !== ".para" ? [] : [...els["#view"].innerHTML.matchAll(/<div class="para [^"]*" data-part="(\\d+)" data-t="([\\d.]+)" data-e="([\\d.]+)"/g)]
    .map((m) => ({ dataset: { part: m[1], t: m[2], e: m[3] }, isConnected: true, offsetWidth: 0, style: {},
      classList: { add: (c) => { if (c === "flash") flashed.push(+m[2]); }, remove() {} }, scrollIntoView() {} }));
  const paras = () => globalThis.qsa(".para").length;
  const open = () => { S.view = { name: "day", dayKey: day.day_key, from: { name: "home" } }; S.day = JSON.parse(JSON.stringify(day)); S.dayFind = ""; St.renderView(); };
"""


@node
def test_with_the_screens_filter_on_a_moment_opens_at_its_passage(tmp_path):
    out = run(tmp_path, DAY + """
  open();
  A["scr-only"]();                                  // the Screens button
  out.filtered = { on: S.screensOnly, paras: paras() };
  St.jumpTo(1, 100);                                // a Highlights item, a mark, a Practice moment, a chat's citation, a search hit
  out.moment = { on: S.screensOnly, paras: paras(), flashed: flashed.splice(0) };
  A["scr-only"]();
  St.jumpTo(null, 40, "a");                         // by its recording (a search hit)
  out.byRecording = { on: S.screensOnly, flashed: flashed.splice(0) };
  // a search result that opens the lecture's notes with the words: the passages with them, as finding does
  A["scr-only"]();
  St.Mock.get_day = () => ({ ok: true, day: JSON.parse(JSON.stringify(day)) });
  await St.openDay(day.day_key, false, { find: "circuit", notes: "summary" });
  out.notes = { on: S.screensOnly, paras: paras() };
  // a lecture without screens keeps the filter for the next one that has them
  S.screensOnly = true; open();
  S.day.parts[0].slides = 0; S.day.parts[0].paragraphs.forEach((x) => { x.slide_image = null; }); St.renderView();
  St.jumpTo(1, 200);
  out.noScreens = { on: S.screensOnly, flashed: flashed.splice(0) };
""")
    assert out["filtered"] == {"on": True, "paras": 0}
    assert out["moment"] == {"on": False, "paras": 4, "flashed": [100]}       # it stayed at the top, nothing shown
    assert out["byRecording"] == {"on": False, "flashed": [40]}
    assert out["notes"] == {"on": False, "paras": 4}
    assert out["noScreens"] == {"on": True, "flashed": [200]}


# ------------------------------------------------------------------ PT-2: the top bar's chip for a quiz or cards
@node
def test_the_chip_for_a_quiz_or_cards_names_it_and_opens_the_course_s_practice_tab(tmp_path):
    out = run(tmp_path, """
  const now = Date.now() / 1000, went = [];
  St.go = (v) => went.push(v);
  const wait = (where) => ({ where, ai: "Claude", since: now - 8, at: now, phase: "think", lines: [] });
  ev({ type: "plan", id: "q1", course_id: "c1", kind: "quiz", status: "running", wait: wait("quiz") });
  const chip = () => plain(St.waitChip()).replace(/<[^>]*>/g, " ").replace(/\\s+/g, " ").trim();
  S.view = { name: "home" }; out.home = chip();
  S.view = { name: "practice" }; out.practicePage = chip();
  S.view = { name: "course", courseId: "c1", tab: "practice" }; out.ownTab = chip();
  S.view = { name: "course", courseId: "c1", tab: "weeks" }; out.ownCourseOtherTab = chip();
  S.view = { name: "course", courseId: "c2", tab: "practice" }; out.otherCourse = chip();
  S.view = { name: "plans" }; out.plansPage = chip();
  A["aw-open"]({ dataset: { key: "plan:q1" } });
  ev({ type: "plan", id: "q1", course_id: "c1", kind: "quiz", status: "done", title: "Thevenin" });
  ev({ type: "plan", id: "k1", course_id: "c1", kind: "cards", status: "running", wait: wait("cards") });
  S.view = { name: "home" }; out.cards = chip();
  A["aw-open"]({ dataset: { key: "plan:k1" } });
  ev({ type: "plan", id: "k1", course_id: "c1", kind: "cards", status: "done", title: "Week 2" });
  ev({ type: "plan", id: "v1", course_id: "c1", kind: "videos", status: "running", wait: wait("videos") });
  out.videos = chip();
  A["aw-open"]({ dataset: { key: "plan:v1" } });
  ev({ type: "plan", id: "v1", course_id: "c1", kind: "videos", status: "error", error: "x" });
  t.load({ lang: "tr", table: turkish });
  ev({ type: "plan", id: "q2", course_id: "c1", kind: "quiz", status: "running", wait: wait("quiz") });
  out.quizTr = chip();
  ev({ type: "plan", id: "q2", course_id: "c1", kind: "quiz", status: "done" });
  ev({ type: "plan", id: "k2", course_id: "c2", kind: "cards", status: "running", wait: wait("cards") });
  out.cardsTr = chip();
  out.went = went;
""")
    assert out["home"].startswith("Writing your quiz") and out["practicePage"].startswith("Writing your quiz")
    assert out["ownTab"] == ""                                     # that tab shows the run already
    assert out["ownCourseOtherTab"].startswith("Writing your quiz") and out["otherCourse"].startswith("Writing your quiz")
    assert out["plansPage"].startswith("Writing your quiz")       # a quiz is not on the Study plans page
    assert out["cards"].startswith("Making your cards") and out["videos"].startswith("Building your video plan")
    assert out["went"] == [{"name": "course", "courseId": "c1", "tab": "practice"},
                           {"name": "course", "courseId": "c1", "tab": "practice"}, {"name": "plan", "planId": "v1"}]
    assert out["quizTr"].startswith("Deneme sınavın yazılıyor") and out["cardsTr"].startswith("Kartların hazırlanıyor")


# ------------------------------------------------------------------ PT-4, PT-6, PT-8, PT-10: Practice
PRACTICE = """
  const asked = [];
  const answer = (name, fn) => { St.mocks[name] = (...a) => { asked.push(name); return fn(...a); }; };
  const card = (id) => ({ id, q: "What is " + id + "?", a: "This.", source: null, stressed: false, state: null,
    next: { again: 60, hard: 360, good: 600, easy: 345600 } });
  answer("cards_session", () => ({ ok: true, cards: [card("k1"), card("k2"), card("k3")] }));
  answer("cards_grade", (cid, id, g) => ({ ok: true, state: { due: new Date(Date.now() + 4 * 86400e3).toISOString().slice(0, 19), step: -1,
    next: { again: 60, hard: 300, good: 600, easy: 86400 } } }));
  const stressed = { weeks: [], not_needed: [], counts: {}, total: 0 };
  const quiz = { id: "q9", course_id: "c1", title: "", status: "running", progress: 0.2, message: "Writing 10 questions (Sonnet · Low)…",
    weeks: [6], days: [], focus: "", questions: 10 };
  answer("practice_course", () => ({ ok: true, deck: { total: 10, today: 3, learnt: 1, lecture_days: 2, files: 0, weeks: [5, 6] },
    quizzes: [JSON.parse(JSON.stringify(quiz))], card_sets: [], stressed }));
  answer("practice_summary", () => ({ ok: true, due: 3, courses: [{ id: "c1", code: "MATH 250", deck: { total: 10, today: 3 }, quizzes: 1,
    quizzes_busy: 1, last_try: null, material: 4, stressed: 20 }] }));
  S.courses = [{ id: "c1", code: "MATH 250", display_name: "Linear Algebra", days: [], counts: {} }];
  const Pr = S.practice;
"""


@node
def test_closing_flashcards_with_escape_or_a_click_beside_updates_what_is_left_today(tmp_path):
    out = run(tmp_path, PRACTICE + """
  S.view = { name: "course", courseId: "c1", tab: "practice" };
  const session = async (grades) => {
    await A["fc-study"]({ dataset: { cid: "c1" } }); await tick();
    for (const g of grades) { A["fc-flip"](); await A["fc-grade"]({ dataset: { g } }); }
    await tick(); asked.length = 0;
  };
  await session(["easy"]);
  out.answerShown = (A["fc-flip"](), overlay().includes('data-act="fc-close"'));   // (with the answer shown: no Stop)
  St.closeModal(); await tick();                 // Escape (app.js) and a click beside the dialog both close it so
  out.escape = { asked: asked.splice(0), study: Pr.study };
  await session(["easy", "good", "easy"]);       // every card: "Done for today"
  out.done = overlay().includes("Done for today");
  St.closeModal(); await tick();
  out.escapeDone = asked.splice(0);
  await session([]);
  St.closeModal(); await tick();
  out.nothingDone = asked.splice(0);             // no card answered: nothing to count again
  await session(["easy"]);
  A["fc-close"](); await tick();                 // Stop
  out.stop = asked.splice(0);
""")
    assert out["answerShown"] is False
    assert out["escape"] == {"asked": ["practice_course", "practice_summary"], "study": None}
    assert out["done"] and out["escapeDone"] == ["practice_course", "practice_summary"]
    assert out["nothingDone"] == []
    assert out["stop"] == ["practice_course", "practice_summary"]       # once, not twice


@node
def test_a_quiz_being_written_changes_its_row_in_place_and_says_what_the_ai_does(tmp_path):
    out = run(tmp_path, PRACTICE + """
  const now = Date.now() / 1000;
  let drawn = 0;
  St.views.course = () => { drawn++; return "<redrawn>"; };
  St.views.practice = () => { drawn++; return "<redrawn>"; };
  S.view = { name: "course", courseId: "c1", tab: "practice" };
  await St.practice.load("c1"); await tick();
  const tab = () => St.practice.tab(S.courses[0]);
  // the window's elements of the drawn tab: the row's bar and step, its wait line (kept: the same ones each time)
  const kept = {};
  const prog = (id) => { const bar = { style: {} }, msg = { textContent: "" };
    return { dataset: { id }, bar, msg, querySelector: (s) => (s === ".bar i" ? bar : s === ".pr-msg" ? msg : null) }; };
  globalThis.qsa = (sel) => {
    const html = els["#view"].innerHTML;
    if (sel === ".pr-prog[data-id]") return [...html.matchAll(/class="pr-prog" data-id="([^"]+)"/g)].map((m) => kept["p" + m[1]] || (kept["p" + m[1]] = prog(m[1])));
    if (sel === ".pl-wait[data-wait]") return [...html.matchAll(/class="pl-wait" data-wait="([^"]+)"/g)].map((m) => kept["w" + m[1]] || (kept["w" + m[1]] = { dataset: { wait: m[1] }, innerHTML: "" }));
    return [];
  };
  els["#view"].innerHTML = tab(); drawn = 0;
  const e = (x) => ev({ type: "plan", id: "q9", course_id: "c1", kind: "quiz", status: "running", title: "", progress: 0.2,
    message: "Writing 10 questions (Sonnet · Low)…", wait: { where: "quiz", ai: "Claude", since: now - 30, at: now, phase: "think",
    lines: ["Choosing what to ask"], eta: 200 }, ...x });
  e({});                                          // its first wait line: drawn once
  out.first = drawn; drawn = 0;
  els["#view"].innerHTML = tab();
  out.row = plain(els["#view"].innerHTML).replace(/<[^>]*>/g, " ").replace(/\\s+/g, " ");
  e({ progress: 0.5 });
  e({ progress: 0.6, message: "Checking the answers…", wait: { where: "quiz", ai: "Claude", since: now - 30, at: now, phase: "write", tokens: 900 } });
  out.inPlace = { drawn, bar: kept.pq9.bar.style.width, msg: kept.pq9.msg.textContent, wait: plain(kept.wq9.innerHTML).replace(/<[^>]*>/g, "") };
  S.view = { name: "practice" };                  // the Practice page shows only that it is being written
  e({ progress: 0.7 });
  out.practicePage = drawn;
  S.view = { name: "course", courseId: "c1", tab: "practice" };
  e({ status: "done", title: "Thevenin and Norton", progress: 1 }); await tick();
  out.done = drawn > 0;
""")
    assert out["first"] == 1
    assert "Writing 10 questions (Sonnet · Low)…" in out["row"] and "Claude is thinking…" in out["row"]
    assert "Thinking: Choosing what to ask" in out["row"] and "so far" in out["row"]
    assert out["inPlace"]["drawn"] == 0                            # it was drawn again on every event, taking the focus
    assert out["inPlace"]["bar"] == "60%" and out["inPlace"]["msg"] == "Checking the answers…"
    assert "Claude is writing…" in out["inPlace"]["wait"] and "900 tokens written" in out["inPlace"]["wait"]
    assert out["practicePage"] == 0 and out["done"]


@node
def test_the_practice_page_counts_the_stressed_moments_that_stand_out_and_anki_has_cancel(tmp_path):
    out = run(tmp_path, PRACTICE + """
  S.view = { name: "practice" };
  await St.onGo.practice(); await tick();
  const page = () => plain(St.views.practice({ name: "practice" })).replace(/<[^>]*>/g, " ").replace(/\\s+/g, " ");
  out.page = page();
  await St.practice.load("c1"); await tick();
  A["fc-export"]({ dataset: { cid: "c1" } });
  out.anki = overlay();
  t.load({ lang: "tr", table: turkish });
  out.pageTr = page();
  A["fc-export"]({ dataset: { cid: "c1" } });
  out.ankiTr = overlay();
""")
    assert "Stressed in class: 20 moments that stand out" in out["page"]       # the tab counts all 30 ("30 moments")
    assert "Derste vurgulananlardan öne çıkan 20 an" in out["pageTr"]
    assert '<button class="btn" data-act="modal-close">Cancel</button>' in out["anki"]
    assert '<button class="btn" data-act="modal-close">İptal</button>' in out["ankiTr"]


# ------------------------------------------------------------------ FS-2, FS-3: before any course is synced
@node
def test_before_any_course_new_study_plan_search_and_full_sync_say_to_sync_first(tmp_path):
    out = run(tmp_path, """
  let options = 0;
  St.mocks.plan_options = () => { options++; return { ok: false, error: "This course is no longer in your list." }; };
  S.view = { name: "plans" };
  await A["plan-new"]({ dataset: {} }); await tick();
  out.plan = { toasts: told(), asked: options, dialog: overlay() };
  // Search
  S.search = { q: "quiz 1", res: { query: "quiz 1", total: 0, counts: {}, courses: [], groups: [] }, items: [], course: "", kind: "" };
  S.view = { name: "library" }; St.renderView();
  out.search = plain(els["#search-results"].innerHTML).replace(/<[^>]*>/g, " ").replace(/\\s+/g, " ");
  // Full sync: nothing to estimate yet, and its button syncs
  St.mocks.catch_up_estimate = () => ({ ok: true, courses: [], lectures: 0, audio_s: 0, materials: 0, work_s: 0, speed: 0.6,
    gpu: false, audio_mb: 0, needs_section: [], unknown: 0 });
  S.view = { name: "home" };
  await A["full-sync"](); await tick();
  out.fullSync = plain(els["#cu-body"].innerHTML);
  let synced = 0;
  St.Mock.sync = () => { synced++; return { ok: true }; };
  A["cu-go"](); await tick();
  out.synced = { synced, dialog: overlay(), running: S.catchup.running };
  // once there are courses: as before
  S.courses = [{ id: "c1", code: "EEE 215", display_name: "Circuits", name: "Circuits", days: [], counts: {} }];
  S.view = { name: "library" }; St.renderView();
  out.searchLater = plain(els["#search-results"].innerHTML).replace(/<[^>]*>/g, " ").replace(/\\s+/g, " ");
  t.load({ lang: "tr", table: turkish });
  S.courses = []; St.renderView();
  out.searchTr = plain(els["#search-results"].innerHTML).replace(/<[^>]*>/g, " ").replace(/\\s+/g, " ");
""")
    assert out["plan"] == {"toasts": ["error: Sync your courses first."], "asked": 0, "dialog": ""}
    assert "Nothing to search yet. Sync your courses first" in out["search"] and "spelling" not in out["search"]
    assert "No courses yet. Sync first to bring them in" in out["fullSync"]
    assert "transcribed already" not in out["fullSync"] and "Also download materials" not in out["fullSync"]
    assert 'class="sync-pick"' not in out["fullSync"]                 # (an empty box of courses to pick)
    button = out["fullSync"].split('data-act="cu-go"')[1].split(">")[0]
    assert "disabled" not in button and "Sync now" in out["fullSync"].split('data-act="cu-go"')[1]
    assert out["synced"] == {"synced": 1, "dialog": "", "running": None}
    assert "Check the spelling" in out["searchLater"]
    assert "Henüz aranacak bir şey yok. Önce derslerini eşitle" in out["searchTr"]


# ------------------------------------------------------------------ FS-4, FS-5: What's new, the sign-in screen
@node
def test_whats_new_links_the_release_page_and_the_sign_in_screen_says_how_to_reach_the_maker(tmp_path):
    out = run(tmp_path, """
  S.boot = { version: "4.0.0", updated_from: "3.3.1", app: { onboarded: true }, last_sync: new Date().toISOString() };
  S.lastSync = S.boot.last_sync;
  S.update = { version: "4.0.0", notes: "## What's new\\n\\n- Practice: quizzes and flashcards", page: "https://github.com/mirzaisi/IEUStudia/releases/tag/v4.0.0" };
  St.afterBoot();
  out.whatsNew = overlay();
  const opened = [];
  St.Mock.open_url = (u) => { opened.push(u); return { ok: true }; };
  await A["update-page"](); await tick();
  out.opened = opened;
  S.mode = "signin"; S.signin = { busy: false, error: "Can't reach learn.ieu.edu.tr. Check your internet connection." }; S.profiles = [];
  St.render();
  out.signin = els["#root"].innerHTML;
  t.load({ lang: "tr", table: turkish });
  St.render();
  out.signinTr = els["#root"].innerHTML;
""")
    assert 'data-act="update-page"' in out["whatsNew"] and "Release page" in out["whatsNew"]
    assert out["whatsNew"].index("Release page") < out["whatsNew"].index('data-act="modal-close"')
    assert out["opened"] == ["https://github.com/mirzaisi/IEUStudia/releases/tag/v4.0.0"]
    card = out["signin"].split('class="card signin-card"')[1]
    form, after = card.split("</form>")
    assert 'class="su-contact"' not in form                    # its buttons would send the sign-in form
    assert 'class="su-contact"' in after and "Questions, or something not working? Message me." in after
    assert "instagram.com/mirzaisi" in after and "linkedin.com/in/mirzaisi" in after
    assert "Soru ya da çalışmayan bir şey mi var? Bana yaz." in out["signinTr"]


# ------------------------------------------------------------------ PT-9: Practice's save dialogs
def test_practice_saves_in_the_course_s_folder_with_the_file_s_type(tmp_path, monkeypatch):
    from webview.util import parse_file_type

    from studia import quizzes
    from studia.api import Api
    from studia.outputs.writer import course_dir
    from studia.services import Services

    a = Api(Services())
    a._push = lambda e: None
    lib = a._s.lib
    lib.upsert_courses([Course(id="c", title="2026-1 MATH 240 P 1", code="MATH 240", name="Probability", term="T")])
    folder = course_dir(a._s.output_root, lib.get_course("c"), lib.courses(True))
    lib.create_plan("q1", "c", {"kind": "quiz", "weeks": [5], "files": [], "days": [], "picked": True})
    lib.update_plan("q1", status="done", progress=1.0, title="Bayes theorem",
                    result={"kind": "quiz", "title": "Bayes theorem", "made": dt.date.today().isoformat(), "questions": []})
    lib.create_plan("k1", "c", {"kind": "cards", "weeks": [5], "files": ["r1"], "days": [], "picked": True})
    lib.update_plan("k1", status="done", progress=1.0, result={"kind": "cards", "cards": [
        {"id": "k1-1", "q": "What is Bayes' theorem?", "a": "P(A|B) = P(B|A)P(A)/P(B)",
         "source": {"kind": "file", "id": "r1", "name": "Week05.pdf", "page": 3, "week": 5}}]})
    made = tmp_path / "made.pdf"
    made.write_bytes(b"%PDF-1.7 quiz")
    monkeypatch.setattr(quizzes, "make_pdf", lambda s, plan: made)
    seen, typed = [], {}

    class Window:                      # Windows' save dialog: the student types a name without its ending
        def create_file_dialog(self, kind, directory="", save_filename="", file_types=()):
            for f in file_types:
                parse_file_type(f)
            seen.append((directory, save_filename, file_types))
            return (str(tmp_path / "picked" / typed["name"]),)

    a._window = Window()
    (tmp_path / "picked").mkdir()
    typed["name"] = "my quiz"
    r = a.quiz_pdf("q1", "save")
    assert r["ok"] and r["saved"] == str(tmp_path / "picked" / "my quiz.pdf")
    assert (tmp_path / "picked" / "my quiz.pdf").read_bytes() == b"%PDF-1.7 quiz"
    assert seen[-1] == (str(folder), "MATH 240 - Practice quiz - Bayes theorem.pdf", ("PDF document (*.pdf)",))
    typed["name"] = "deck"
    r = a.cards_export("c", "apkg")
    assert r["ok"] and r["cards"] == 1 and r["saved"] == str(tmp_path / "picked" / "deck.apkg")
    assert seen[-1] == (str(folder), "MATH 240 flashcards.apkg", ("Anki deck (*.apkg)",))
    typed["name"] = "deck.txt"
    assert a.cards_export("c", "txt")["saved"] == str(tmp_path / "picked" / "deck.txt")
    assert seen[-1] == (str(folder), "MATH 240 flashcards.txt", ("Text file (*.txt)",))
    i18n.set_language("tr")
    try:
        a.cards_export("c", "apkg")
    finally:
        i18n.set_language("en")
    assert seen[-1][2] == ("Anki destesi (*.apkg)",)
    a._s.plans.shutdown()
