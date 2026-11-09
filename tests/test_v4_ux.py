"""4.0.0, the last pass for students: the old Save-as-PDF code is gone; a quiz or exam an announcement gave a day for
shows that day (and its time) on the course page and Home, not only its week; the quiz dialog's reasoning note says
nothing about "several long requests" (a quiz is one request); an old study pack's or cheatsheet's PDF says it is
being made while it is laid out again; the digest lists announcements apart from what was said in class and asks
nobody to transcribe a lecture with no speech; the chat list gets each chat's first question for its filter; counts
in the plans' progress take their plural; closing the window while the AI writes something asks first.

Settings: how many questions a new quiz starts with and how many new flashcards a day (both used where they count),
how often the background checks look and when the reminders come (from Settings too), and "Back to the defaults" for
each group, which puts back only that group (never the speech model, the folder or the AI service)."""

import datetime as dt
import json
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from studia import i18n

WEB = Path(i18n.__file__).parent / "web"
node = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")


# ------------------------------------------------------------------ the page in node (app.js and the pages it needs)
HARNESS = """
const web = %(web)s;
const stub = () => new Proxy(function () {}, { get: (t, k) => k === Symbol.toPrimitive ? () => "" : k === "length" ? 0 : stub(),
  apply: () => stub(), construct: () => stub() });
globalThis.window = globalThis;
const toasts = [];
const el = () => ({ innerHTML: "", style: {}, dataset: {}, classList: { toggle() {}, add() {}, remove() {}, contains: () => false }, scrollTo() {},
  querySelector: () => null, querySelectorAll: () => [], addEventListener() {}, remove() {}, focus() {}, contains: () => false });
const els = { "#top": el(), "#view": el(), "#overlay": el(), "#pk-cost": el(), "#toasts": { appendChild: (b) => toasts.push(b) } };
const doc = { activeElement: null, all: [] };
globalThis.document = new Proxy({}, { get: (t, k) => k === "compatMode" ? "CSS1Compat"
  : k === "activeElement" ? doc.activeElement
  : k === "hidden" ? false
  : k === "createElement" ? () => el()
  : k === "querySelector" ? (s) => (s in els ? els[s] : stub())
  : k === "getElementById" ? (id) => els["#" + id] || null
  : k === "querySelectorAll" ? (s) => doc.all.filter((b) => b.matches(s))
  : k === "addEventListener" ? (n, f) => { (listeners[n] = listeners[n] || []).push(f); } : stub() });
const listeners = {};
globalThis.localStorage = { getItem: () => null, setItem() {} };
globalThis.navigator = { userAgent: "node", platform: "Win32" };
globalThis.location = { search: "", hash: "", href: "" };
globalThis.CSS = { escape: (s) => s };
globalThis.addEventListener = () => {}; globalThis.matchMedia = () => ({ matches: false, addEventListener() {} });
globalThis.requestAnimationFrame = (f) => f();
const later = setTimeout;
globalThis.setInterval = () => 0; globalThis.setTimeout = () => 0;      // no boot, no polling
for (const f of ["i18n.js", "match.js", "icons.js", "app.js", "solvequill.js", "effort.js", "course.js", "plans.js", "studypack.js", "cheatsheet.js",
  "digest.js", "failsafe.js", "practice.js", "settings.js"]) require(web + "/" + f);
const St = globalThis.Studia, S = St.S, A = St.A;
St.aiName = () => "Claude";
const tick = (ms = 30) => new Promise((r) => later(r, ms));
const ev = (e) => window.__lsEvent(e);
const plain = (h) => String(h).replace(/<[^>]*>/g, " ").replace(/&#39;/g, "'").replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&").replace(/\\s+/g, " ").trim();
const overlay = () => plain(els["#overlay"].innerHTML);
const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const add = (iso0, n) => { const d = new Date(iso0 + "T00:00:00"); d.setDate(d.getDate() + n); return iso(d); };
const out = {};
(async () => {
  S.mode = "app"; S.view = { name: "home" }; S.boot = {}; S.courses = []; S.system.ai = { available: true, provider: "claude" };
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


# ------------------------------------------------------------------ the old Save-as-PDF code
def test_the_old_save_as_pdf_code_is_gone():
    app = (WEB / "app.js").read_text(encoding="utf-8")
    for gone in ("exporting", "onExport", "export_pack", 'case "export"'):
        assert gone not in app, gone


# ------------------------------------------------------------------ an assessment's day, as an announcement gave it
COURSE = """
  const today = iso(new Date()), monday = add(today, -((new Date(today + "T00:00:00").getDay() + 6) % 7));
  const week = (no, start) => ({ no, start, end: add(start, 6), current: start <= today && today <= add(start, 6), past: add(start, 6) < today,
    exam: false, topic_en: "Topic " + no, topic_tr: "", units: [], days: [], classes: [], readings: "" });
  const src = [{ id: "a1", title: "Quiz dates", date: "2026-10-01T10:00" }];
  const plan = [{ kind: "quiz", no: 2, name: "Quiz 2", week: 8, los: [], sources: src, date: add(monday, 11), time: "10:30" },
    { kind: "quiz", no: 3, name: "Quiz 3", week: 9, los: [], sources: src, date: "", time: "" },
    { kind: "quiz", no: 1, name: "Quiz 1", week: 6, los: [], sources: src, date: add(monday, -3), time: "" }];
  S.courses = [{ id: "m", code: "MATH 250", display_name: "Linear Algebra", days: [], counts: { total: 0, done: 0 }, content: {} }];
  S.cd.m = { weeks: [week(6, add(monday, -14)), week(7, monday), week(8, add(monday, 7)), week(9, add(monday, 14))], general: [],
    materials: { total: 0, downloaded: 0 }, syllabus: {}, announcements: [], outcomes_en: [], outcomes_tr: [], grading: [],
    alerts: { items: [], counts: {}, plan } };
  const d = S.cd.m;
  out.rel = plan.map((e) => { const r = St.planRel(d, e); return [e.name, r.code, r.date || "", r.time || "", !!r.past]; });
  out.day = t.date(add(monday, 11), "dmw");
  S.view = { name: "course", courseId: "m" };
  out.page = plain(St.views.course(S.view));
  S.view = { name: "course", courseId: "m", tab: "alerts" };
  out.alerts = plain(St.views.course(S.view));
  S.view = { name: "home" }; S.profile = { name: "Deniz" }; S.lastSync = "2026-11-09T10:00:00";
  out.home = plain(St.views.home(S.view));
"""


@node
def test_an_assessment_announced_for_a_day_shows_that_day_on_the_course_page_and_home(tmp_path):
    out = run(tmp_path, COURSE)
    rel = {name: (code, date, time, past) for name, code, date, time, past in out["rel"]}
    assert rel["Quiz 2"][0] == "next-week" and rel["Quiz 2"][2] == "10:30" and not rel["Quiz 2"][3]
    assert rel["Quiz 3"][:3] == ("later", "", "")                       # no day given: its week, as before
    assert rel["Quiz 1"][0] == "done" and rel["Quiz 1"][3]              # its day is over (its week 6 too)
    when = f"{out['day']}, 10:30"
    assert f"Quiz 2 {when} · next week" in out["page"]                   # Coming up on the course page
    assert "Quiz 3 week 9 · in 2 weeks" in out["page"]
    assert f"Week 8 · {when} next week" in out["alerts"]                 # the assessment plan
    assert f"Quiz 2 next week · {when}" in out["home"]                   # Home's Coming up
    assert f"Quiz 2 · {out['day']}" in out["home"]                       # the course card


# ------------------------------------------------------------------ the quiz dialog's reasoning note
QUIZ = """
  S.system.ai = { available: true, provider: "claude", efforts: ["low", "medium", "high", "xhigh", "max"] };
  const note = (ctx, more) => { A["eff-pick"]({ dataset: { ctx }, value: "xhigh" });      // Extra high picked in the dialog
    return plain((St.effortField(ctx, "plan", "", more).match(/<div class="eff-note-slot"[^]*$/) || [""])[0]); };
  out.heavyNote = note("qz", { why: "" });
  out.cost = plain(St.effortCostNote({ main_input: 20000, main_output: 6000 }, { kind: "quiz", effort: "xhigh" }));
  out.packCost = plain(St.effortCostNote({ main_input: 20000, main_output: 6000 }, { kind: "pack", effort: "xhigh" }));
  out.planNote = note("pd", {});
"""


@node
def test_the_quiz_dialog_says_nothing_about_several_requests(tmp_path):
    out = run(tmp_path, QUIZ)
    assert out["heavyNote"].startswith("Extra high: Thinks much longer") and "requests" not in out["heavyNote"]
    assert out["cost"] == "At Extra high reasoning it also thinks longer, so it uses more than this estimate."
    assert "every one of its requests" in out["packCost"]                # a study pack: many requests
    assert "several long requests" in out["planNote"]                    # a plan's dialog keeps its words
    practice = (WEB / "practice.js").read_text(encoding="utf-8")
    assert 'St.effortField("qz", "plan", Q.effort, { attrs: \'data-qz="effort"\', why: "" })' in practice
    assert 'St.effortCostNote(e, { kind: "quiz"' in practice


# ------------------------------------------------------------------ a PDF being made says so
PDF = """
  globalThis.setTimeout = later;
  let answer = null;
  St.mocks.pack_pdf = () => new Promise((res) => { answer = res; });
  const btn = (how) => ({ dataset: { act: "pk-pdf", how, id: "p1" }, disabled: false, innerHTML: "",
    matches: (s) => s === '[data-act="pk-pdf"][data-id="p1"]' });
  const btns = [btn("save"), btn("open"), btn("folder")];
  doc.all = btns;
  const p = { id: "p1" };
  out.before = plain(St.pack.pdfActs(p));
  const going = A["pk-pdf"](btns[1]);
  await tick();
  out.during = { html: plain(St.pack.pdfActs(p)), btns: btns.map((b) => [plain(b.innerHTML), b.disabled]) };
  A["pk-pdf"](btns[0]);                                  // pressed again meanwhile: nothing more is asked
  answer({ ok: true }); await going; await tick();
  out.after = { html: plain(St.pack.pdfActs(p)), btns: btns.map((b) => [plain(b.innerHTML), b.disabled]) };
  out.prep = plain(St.pack.pdfBtn(p, "open", ""));
"""


@node
def test_an_old_pack_s_pdf_says_it_is_being_made(tmp_path):
    out = run(tmp_path, PDF)
    assert out["before"] == "Save PDF… Open PDF Show in folder"
    assert out["during"]["btns"] == [["Save PDF…", True], ["Making the PDF…", True], ["Show in folder", True]]
    assert out["during"]["html"] == "Save PDF… Making the PDF… Show in folder"       # drawn again: still busy
    assert out["after"]["btns"] == [["Save PDF…", False], ["Open PDF", False], ["Show in folder", False]]
    assert out["prep"] == "Open PDF"


# ------------------------------------------------------------------ the digest
def test_the_digest_lists_no_to_do_for_a_lecture_with_no_speech_and_says_so(tmp_path):
    from studia.config import Settings
    from studia.digest import digest_markdown, digest_prompt, weekly_digest
    from studia.library import Library

    from test_v4_pages import _fill

    lib = _fill(Library(tmp_path / "lib.db"))          # Mon 12 Oct: no speech; Wed 14 Oct: not transcribed yet
    d = weekly_digest(lib, Settings(min_duration_s=60), today=dt.date(2026, 10, 15))
    (c,) = d["courses"]
    lec = {x["date"]: x for x in c["lectures"]}
    assert lec["2026-10-12"]["no_sound"] and not lec["2026-10-14"]["no_sound"]
    todo = [x["day_key"] for x in d["todo"] if x["kind"] == "transcribe"]
    assert todo == [lec["2026-10-14"]["day_key"]]
    assert "(no speech in the recording)" in digest_prompt(d) and "(no speech in the recording)" in digest_markdown(d)


DIGEST = """
  S.digest.data = { week: { start: "2026-10-12", end: "2026-10-18", offset: 0, label: "12 – 18 Oct 2026" }, todo: [],
    courses: [{ id: "a", code: "MATH 250", name: "Linear Algebra", week: null, coming: [], new_files: [], announcements: [],
      lectures: [{ day_key: "a|2026-10-12|lecture", label: "Mon 12 Oct", kind: "Lecture", any_done: false, no_sound: true, stressed: [] },
        { day_key: "a|2026-10-14|lecture", label: "Wed 14 Oct", kind: "Lecture", any_done: false, no_sound: false, stressed: [] }],
      said: [{ category: "quiz", source: "announcement", title: "Quiz 2", text: "Quiz 2 is on Friday.", date: "2026-10-13" },
        { category: "quiz", source: "lecture", title: "", text: "Next week we have a quiz.", date: "2026-10-14" }] }] };
  S.view = { name: "digest" };
  const html = St.views.digest(S.view);
  out.page = plain(html);
  out.transcribe = (html.match(/data-act="tx-day"/g) || []).length;
"""


@node
def test_the_digest_page_tells_announcements_from_what_was_said_in_class(tmp_path):
    out = run(tmp_path, DIGEST)
    assert "The lecturer said" not in out["page"] and "Heads-up from the lecturer" in out["page"]
    assert "Quiz 2 : Quiz 2 is on Friday." in out["page"] and "“Next week we have a quiz.”" in out["page"]
    assert "No speech found" in out["page"] and out["transcribe"] == 1      # the silent lecture has no Transcribe


# ------------------------------------------------------------------ the chat list's filter, plural counts
def test_the_chat_list_gets_each_chat_s_first_question():
    from types import SimpleNamespace

    from studia.chat import ChatService
    from studia.config import Settings

    svc = ChatService.__new__(ChatService)
    svc._running = {}
    svc._s = SimpleNamespace(settings=Settings(), lib=SimpleNamespace(get_course=lambda cid: None))
    svc._day_labels = lambda chat: []
    chat = {"id": "c1", "course_id": "x", "title": "Bayes", "first_question": "  What is\nBayes' theorem? ",
            "scope": {"effort": "low"}}
    assert ChatService.summary(svc, chat)["first_question"] == "What is Bayes' theorem?"


def test_counts_in_the_plans_progress_take_their_plural():
    i18n.set_language("en")
    src = (Path(i18n.__file__).parent / "plans.py").read_text(encoding="utf-8")
    for one in ("Searching YouTube for {n} concept…", "Reading the captions of {n} video…", "{n} video · {minutes} min"):
        assert f'"{one}"' in src
    assert i18n.tn(1, "Reading the captions of {n} video…", "Reading the captions of {n} videos…") == "Reading the captions of 1 video…"


# ------------------------------------------------------------------ closing while the AI writes
def test_what_the_ai_is_writing_is_known_for_the_close_question():
    from studia import aiwait

    stop = threading.Event()
    assert aiwait.running() == []
    with aiwait.tracking(stop, aiwait.Wait(where="pack")):
        assert aiwait.running() == ["pack"]
    assert aiwait.running() == []


def test_the_close_question_says_what_the_ai_is_writing(isolated_home):
    from studia.api import Api
    from studia.services import Services

    api = Api(Services())
    pushed = []
    api._push = pushed.append
    assert api._ask_close(False, ["chat"])
    assert pushed[-1] == {"type": "close-ask", "transcribing": False, "ai": ["chat"]}


CLOSE = """
  ev({ type: "close-ask", transcribing: false, ai: ["pack"] });
  out.pack = overlay();
  ev({ type: "close-ask", transcribing: false, ai: ["chat"] });
  out.chat = overlay();
  ev({ type: "close-ask", transcribing: true, ai: ["notes", "quiz"] });
  out.both = overlay();
  ev({ type: "close-ask" });
  out.old = overlay();
"""


@node
def test_the_page_asks_before_closing_while_the_ai_writes(tmp_path):
    out = run(tmp_path, CLOSE)
    assert "Claude is still writing a study pack. If you close IEUStudia now, it stops. What's finished is kept" in out["pack"]
    assert "Lectures are still being transcribed" not in out["pack"]
    assert "Claude is still writing an answer in a chat. If you close IEUStudia now, it stops. You can ask again next time." in out["chat"]
    assert "Lectures are still being transcribed" in out["both"] and "Claude is still working on 2 things you asked for." in out["both"]
    assert "Lectures are still being transcribed" in out["old"] and "Claude" not in out["old"]   # (an older event)


# ------------------------------------------------------------------ Settings: the new ones, and Back to the defaults
def test_the_practice_settings_have_good_defaults_and_stay_in_range():
    from studia.config import Settings, apply_patch

    s = Settings()
    assert (s.quiz_questions, s.cards_per_day) == (10, 20)
    assert apply_patch(s, {"quiz_questions": "15", "cards_per_day": "30"}).quiz_questions == 15
    assert apply_patch(s, {"quiz_questions": 7}).quiz_questions == 5           # the nearest the dialog offers
    assert apply_patch(s, {"quiz_questions": 99}).quiz_questions == 20
    assert apply_patch(s, {"cards_per_day": 0}).cards_per_day == 1
    assert apply_patch(s, {"cards_per_day": 5000}).cards_per_day == 200
    assert apply_patch(s, {"cards_per_day": "x"}).cards_per_day == 20          # unreadable: the default


def test_each_group_puts_back_only_its_own_settings():
    from studia.agenda import REMIND_DEFAULTS
    from studia.appconfig import AppConfig
    from studia.config import GROUPS, Settings, all_defaults, defaults

    fields = {"settings": set(Settings().to_json()), "app": set(AppConfig().to_json()), "reminders": set(REMIND_DEFAULTS)}
    for group, keys in GROUPS.items():
        for where, names in keys.items():
            assert set(names) <= fields[where], (group, where)
    every = {k for g in GROUPS.values() for names in g.values() for k in names}
    for kept in ("model", "output_dir", "language", "ai_provider", "tray", "start_with_windows", "course_prefs"):
        assert kept not in every                                              # chosen on purpose: never put back
    ai = defaults("ai")["settings"]["ai_effort"]
    assert ai["claude.chat"] == "low" and ai["chatgpt.chat"] == "medium" and ai["local.chat"] == "off"
    assert set(all_defaults()) == set(GROUPS)


def test_back_to_the_defaults_puts_back_one_group(isolated_home):
    from studia import appconfig
    from studia.api import Api
    from studia.services import Services

    api = Api(Services())
    api._push = lambda e: None
    api._system_info = lambda: None                   # (a change of device looks at the PC again: not here)
    api.update_settings({"device": "cpu", "capture_slides": False, "model": "small", "min_duration_s": 300,
                         "quiz_questions": 20, "theme": "dark", "ai_effort": {"claude.chat": "max"}})
    out_dir = api._s.settings.output_dir
    appconfig.update({"notify_materials": True, "sync_minutes": 120, "tray": True})
    api.calendar_reminders({"exams": 7, "due_hours": 6})

    r = api.settings_reset("transcription")
    s = r["settings"]
    assert (s["device"], s["capture_slides"], s["min_duration_s"]) == ("auto", True, 60)
    assert s["model"] == "small" and s["output_dir"] == out_dir                 # the speech model and folder stay
    assert s["quiz_questions"] == 20 and s["theme"] == "dark"                   # other groups too
    assert api.settings_reset("practice")["settings"]["quiz_questions"] == 10
    assert api.settings_reset("appearance")["settings"]["theme"] == "system"
    assert api.settings_reset("ai")["settings"]["ai_effort"]["claude.chat"] == "low"

    r = api.settings_reset("notifications")
    assert r["app"]["notify_materials"] is False and r["app"]["sync_minutes"] == 30
    assert r["app"]["tray"] is True                                              # running in the background stays
    assert r["reminders"] == {"exams": 2, "due_days": 1, "due_hours": 3}
    assert api.calendar_reminders()["reminders"] == r["reminders"]               # the Calendar's Reminders agree
    assert api.settings_reset("nope")["ok"] is False
    d = api.settings_defaults()
    assert d["ok"] and d["defaults"]["practice"] == {"settings": {"quiz_questions": 10, "cards_per_day": 20}}


def test_new_flashcards_a_day_follow_the_setting(isolated_home):
    from studia import cards
    from studia.api import Api
    from studia.services import Services

    api = Api(Services())
    lib = api._s.lib
    deck = [{"id": f"k{i}", "q": "q", "a": "a", "origin": "files", "source": {}, "week": 1, "stressed": False}
            for i in range(30)]
    assert cards.summary(lib, deck, "c")["new"] == 20                            # the default
    assert cards.summary(lib, deck, "c", new_per_day=5)["new"] == 5
    assert len(cards.session(lib, deck, "c", new_per_day=5)) == 5
    api._deck = lambda course_id: deck
    api.update_settings({"cards_per_day": 10})
    assert len(api.cards_session("c")["cards"]) == 10                           # the Practice tab's session uses it


SETTINGS = r"""
  globalThis.setTimeout = later;
  const asked = [];
  St.ask = async (o) => { asked.push(o.title + " | " + o.text); return true; };
  St.mocks.settings_reset = (g) => { asked.push("reset " + g); return { ok: true, settings: { ...S.settings, quiz_questions: 10, cards_per_day: 20 } }; };
  S.settings = { theme: "system", text_size: 100, device: "auto", capture_slides: true, ask_before_closing: true, min_duration_s: 60,
    write_srt: true, keep_audio: false, quiz_questions: 15, cards_per_day: 20, ai_model: "sonnet", plan_model: "", verify_model: "haiku",
    ai_model_light: "haiku", ai_effort: {}, output_dir: "C:/x" };
  S.boot = { app: { tray: false, sync_minutes: 30, notify_recordings: true, notify_announcements: true, notify_materials: false, notify_reminders: true } };
  S.profiles = []; S.view = { name: "settings" };
  St.renderView(); await tick(); St.renderView();
  out.page = plain(els["#view"].innerHTML);
  out.changed = ["transcription", "files", "ai", "practice", "notifications", "appearance"].filter((g) => St.setChanged(g));
  out.every = St.checkEveryRow();
  out.rem = plain(St.reminderRows());
  await A["set-reset"]({ dataset: { group: "practice" } });
  out.asked = asked;
  out.after = [S.settings.quiz_questions, St.setChanged("practice")];
  S.settings.quiz_questions = 15;
  St.mocks.quiz_options = () => ({ ok: true, model: "sonnet", ai: { available: true }, quant: false, language: "English", exams: [],
    weeks: [{ no: 7, current: true, topic: "Bayes", files: [{ id: "f", name: "Week07.pdf", pages: 12, ready: true, downloaded: true }], days: [] }] });
  St.mocks.quiz_estimate = () => ({ ok: true, estimate: null });
  S.courses = [{ id: "m", code: "MATH 240", display_name: "Probability", days: [], counts: {} }];
  await A["qz-new"]({ dataset: { cid: "m" } }); await tick();
  out.quiz = (els["#overlay"].innerHTML.match(/<button type="button" class="on" data-act="qz-count" data-n="(\d+)"/) || [])[1];
"""


@node
def test_the_settings_page_has_practice_and_puts_a_changed_group_back(tmp_path):
    out = run(tmp_path, SETTINGS)
    assert "Questions in a new quiz" in out["page"] and "New flashcards a day" in out["page"]
    assert out["changed"] == ["practice"]                                      # only the group that differs
    assert out["page"].count("Back to the defaults") == 1
    assert "Check IEULearn every" in out["every"] and 'value="30" selected' in out["every"] and " disabled " in out["every"]   # (background checks off)
    assert "Quizzes and exams" in out["rem"] and "2 days before" in out["rem"] and "3 hours before" in out["rem"]
    assert out["asked"] == ["Back to the defaults for Practice? | A new quiz starts with 10 questions, and up to 20 new flashcards a day.",
                            "reset practice"]
    assert out["after"] == [10, False]
    assert out["quiz"] == "15"                                                 # a new quiz starts from the setting


# ------------------------------------------------------------------ an AI button without its AI says why; old chats stay readable
AI_OFF = r"""
  const asked = [];
  St.ask = async (o) => { asked.push(o.title + " | " + o.text); return true; };
  S.system.ai = { available: false, provider: "chatgpt", label: "ChatGPT" };
  out.off = [St.aiBtn(false), St.aiBtn(false, true), St.aiBtn(true)];
  S.system.ai = { available: true, provider: "local", label: "Offline model", can_plans: false };
  out.local = [St.aiBtn(false, true), St.aiBtn(false)];       // too small for quizzes; something else keeps a chat off
  S.system.ai = { available: false, provider: "chatgpt", label: "ChatGPT" };
  const btn = { dataset: { act: "qz-new", aiOff: "plans" }, tagName: "BUTTON", disabled: false,
    closest: (s) => (s === "[data-act]" ? btn : null) };
  listeners.click.forEach((f) => f({ target: btn }));
  await tick();
  out.asked = asked.splice(0); out.view = S.view.name;
  // a chat you had, while ChatGPT isn't connected: its messages, and what to do instead of the composer
  S.view = { name: "chat", chatId: "c1" }; S.chat.id = "c1"; S.chat.draft = null;
  S.chat.data = { id: "c1", title: "Bayes", course_id: "m", course_code: "MATH 240", days: [], model: "sonnet", effort: "low", messages: [
    { role: "user", text: "What is Bayes' theorem?" }, { role: "assistant", text: "It turns P(B|A) into P(A|B)." }] };
  S.chat.list = [];
  const main = St.renderView ? (St.renderView(), els["#view"].innerHTML) : "";
  out.chat = plain(main);
  out.composer = /data-form="chat"/.test(main);
"""


@node
def test_an_ai_button_without_its_ai_says_why_and_old_chats_stay_readable(tmp_path):
    out = run(tmp_path, AI_OFF)
    assert out["off"][0].startswith('aria-disabled="true" data-ai-off="any"') and 'data-ai-off="plans"' in out["off"][1]
    assert out["off"][2] == ""
    assert 'data-ai-off="plans"' in out["local"][0] and out["local"][1] == "disabled"
    assert out["asked"] == ["AI isn't set up yet | ChatGPT isn't connected yet. Connect it in Settings → AI to use this."]
    assert out["view"] == "settings"                                           # Set up AI: Settings → AI
    assert "It turns P(B|A) into P(A|B)." in out["chat"] and "You can read this chat; asking more needs the AI." in out["chat"]
    assert not out["composer"]
