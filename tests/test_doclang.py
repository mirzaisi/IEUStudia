"""4.1: the language of study documents (Settings → AI). One function picks it from the course and the setting
(doclang.py: the course's language, the default, or English or Turkish); every document the AI writes asks for it,
and, written in another language than the course's, keeps the course's exam terms (one short sentence). The setting
saves, goes back with the AI group's "Back to the defaults" and reaches each kind of document (the AI replaced by
fakes). The page: the row in Settings → AI and each document dialog's quiet line, in English and Turkish."""

import datetime as dt
import json
import shutil
import time

import pytest

from studia import cards, cheatsheet, digest, doclang, i18n, plans, quizzes, store, stressed, studypack
from studia.config import GROUPS, Settings, apply_patch, defaults
from studia.coursesite import Resource, Unit
from studia.domain import Course, Recording, Segment, Transcript
from studia.outputs import prompts
from studia.plans import PlanRunner, Scope

RULE_TR = "Keep technical terms in English, as the exams use them, with the Turkish meaning in brackets the first time."
RULE_EN = "Keep technical terms in Turkish, as the exams use them, with the English meaning in brackets the first time."
ENGLISH = {"syllabus": {"language": "English"}}
TURKISH = {"syllabus": {"language": "Turkish"}}


# ------------------------------------------------------------------ the one function
@pytest.mark.parametrize("course, pack, taught", [
    (ENGLISH, "", "English"),
    (TURKISH, "", "Turkish"),
    ({"syllabus": {"language": "Türkçe"}}, "", "Turkish"),
    ({"syllabus": {"language": "English, Turkish"}}, "", "English"),          # both named: English, as before
    ({"syllabus": {}}, "[00:10] [TR] bir\n[00:20] [TR] iki\n[00:30] three", "Turkish"),   # by the lectures
    ({"syllabus": {}}, "[00:10] one\n[00:20] two", "English"),
    ({}, "", "English"),                                                       # nothing known: English
])
def test_the_language_is_the_courses_unless_the_student_chose_one(course, pack, taught):
    facts = {"pack": pack}
    assert doclang.taught_in(course, facts) == taught
    assert doclang.document_language(course, Settings(), facts) == {"language": taught, "taught_in": taught}
    assert doclang.document_language(course, None, facts) == {"language": taught, "taught_in": taught}
    assert doclang.document_language(course, Settings(doc_language="en"), facts) == {"language": "English", "taught_in": taught}
    assert doclang.document_language(course, Settings(doc_language="tr"), facts) == {"language": "Turkish", "taught_in": taught}
    assert doclang.document_language(course, Settings(doc_language="fr"), facts)["language"] == taught   # unknown: the course's
    assert quizzes.course_language(course, facts) == taught                   # the old name still answers


def test_the_digest_takes_the_language_most_of_its_courses_are_taught_in():
    assert doclang.for_courses(["English", "Turkish", "English"]) == {"language": "English", "taught_in": "English"}
    assert doclang.for_courses(["Turkish", "Turkish", "English"]) == {"language": "Turkish", "taught_in": "Turkish"}
    assert doclang.for_courses(["Turkish", "English"])["language"] == "English"             # a tie: English
    assert doclang.for_courses([])["language"] == "English"
    assert doclang.for_courses(["English"], Settings(doc_language="tr")) == {"language": "Turkish", "taught_in": "English"}


# ------------------------------------------------------------------ the prompts
def test_every_document_prompt_asks_for_its_language_and_keeps_the_exam_terms():
    same, other = {"language": "English", "taught_in": "English"}, {"language": "Turkish", "taught_in": "English"}
    assert prompts.terms_rule("English", "English") == "" and prompts.terms_rule("Turkish", "") == ""
    assert prompts.written_in("Turkish", "English") == f"Write in Turkish. {RULE_TR}"
    assert prompts.written_in("English", "Turkish") == f"Write in English. {RULE_EN}"
    assert prompts.written_in("Turkish", "Turkish") == "Write in Turkish, the language the course is taught in."
    for kind in ("notes", "exam", "flashcards"):
        assert prompts.ai_prompt(kind, **other).count(RULE_TR) == 1
        assert "Keep technical terms" not in prompts.ai_prompt(kind, **same)
    facts = {"span": "week 3", "pack": "", "files": [], "lectures": [], "stressed": [], "weeks": [{"no": 3}],
             "course": {"code": "EEE 215", "name": "Circuits"}, "stressed_lines": "", "outcomes": []}
    ch = {"id": "c1", "title": "A", "teaches": ["x"], "summary": "", "figures": [
        {"id": "c1f1", "kind": "diagram", "brief": "b", "caption": ""}]}

    def texts(lang):
        f = {**facts, **lang}
        outline = {"title": "T", "chapters": [ch], **lang}
        sheet = {"title": "T", **lang}
        return {
            "quiz": quizzes.write_prompt(Scope(kind="quiz"), f, 5, {"mc": 5, "short": 0, "problem": 0}),
            "cards": cards.cards_prompt(f, {"text": "", "pages": 1}, 10, []),
            "outline": studypack.outline_prompt(Scope(kind="pack"), f),
            "chapter": studypack.section_prompt(outline, ch, "", Scope(kind="pack")),
            "figures": studypack.figure_prompt(ch, "", outline),
            "topics": cheatsheet.topics_prompt(f),
            "entries": cheatsheet.entries_prompt(sheet, [{"id": "t1", "title": "A", "items": ["x"]}], "", "w"),
            "review": plans.prep_prompt(Scope(kind="prep"), f),
            "video plan": plans.assemble_prompt(Scope(kind="videos"), f, [], {}, None, dt.date(2026, 11, 11)),
            "sum it up": stressed.summary_prompt({"code": "EEE 215", "name": "Circuits"}, "", lang["language"], "",
                                                 lang["taught_in"]),
            "digest": digest.digest_prompt({"week": {"start": "2026-11-09", "end": "2026-11-15"}, "courses": [],
                                            "todo": [], **lang})}

    for kind, text in texts(other).items():
        assert "Turkish" in text and text.count(RULE_TR) == 1, kind              # once: the prompts stay lean
    for kind, text in texts(same).items():
        assert "Keep technical terms" not in text, kind
        assert "in English" in text or "Write in English" in text, kind
    tr = texts(other)
    assert "Write every question, option, answer and explanation in Turkish. " + RULE_TR in tr["quiz"]
    assert "Titles and captions in Turkish. " + RULE_TR in tr["outline"]
    assert "Captions in Turkish. " + RULE_TR in tr["figures"]
    assert "Titles and items in Turkish. " + RULE_TR in tr["topics"]
    assert "Write in Turkish. " + RULE_TR in tr["digest"]
    assert "in English (the language this course is taught in)." in texts(same)["quiz"]      # as before
    # a pack started before 4.1 (no taught_in in its outline): as before, no rule
    assert "Keep technical terms" not in studypack.section_prompt({"title": "T", "chapters": [ch], "language": "Turkish"},
                                                                  ch, "", Scope(kind="pack"))


# ------------------------------------------------------------------ the setting
def test_the_setting_saves_stays_valid_and_goes_back_with_the_ai_group(isolated_home):
    from studia.api import Api
    from studia.services import Services

    assert Settings().doc_language == "course"
    assert apply_patch(Settings(), {"doc_language": "tr"}).doc_language == "tr"
    assert apply_patch(Settings(), {"doc_language": "de"}).doc_language == "course"
    assert apply_patch(Settings(), {"doc_language": 3}).doc_language == "course"
    assert "doc_language" in GROUPS["ai"]["settings"] and defaults("ai")["settings"]["doc_language"] == "course"
    api = Api(Services())
    api._push = lambda e: None
    assert api.update_settings({"doc_language": "en", "theme": "dark"})["settings"]["doc_language"] == "en"
    assert Api(Services())._s.settings.doc_language == "en"                     # saved for the next start
    s = api.settings_reset("ai")["settings"]
    assert s["doc_language"] == "course" and s["theme"] == "dark"               # only the AI group goes back


# ------------------------------------------------------------------ each kind of document, through the app's API
@pytest.fixture
def api(tmp_path):
    from studia.api import Api
    from studia.services import Services

    a = Api(Services())
    a._push = (events := []).append
    a.events = events
    lib = a._s.lib
    lib.upsert_courses([Course(id="c", title="2026-1 EEE 215 P 1", code="EEE 215", name="Circuits", term="T")])
    lib.merge_units("c", [Unit("u1", 0, "Week 1", dt.date(2026, 9, 21), dt.date(2026, 9, 27), "",
                               [Resource("r1", "Week01", "Week01.pdf", "pdf", "pdf", "u", "d")])], first_sync=True)
    lib.set_material_file("r1", str(tmp_path / "Week01.pdf"), 10, "u")
    (tmp_path / "Week01.pdf").write_bytes(b"%PDF")
    lib.set_material_text("r1", ["Ohm's law: V = IR.", "Kirchhoff's current law: the currents into a node add to zero."])
    lib.set_course_info("c", syllabus={"language": "English", "outcomes": ["analyse resistive circuits"],
                                       "weeks": [{"no": 1, "subject": "Resistive circuits", "preparation": ""}]})
    rec = Recording(delivery_id="d1", course_id="c", folder_id="f", folder_path="", name="n",
                    start=dt.datetime(2026, 9, 23, 9, 0), duration_s=3000, viewer_url="v")
    lib.merge_recordings("c", [rec], first_sync=True)
    tr = Transcript(delivery_id="d1", segments=[
        Segment(0, 5, "Today: Ohm's law and Kirchhoff's current law.", "en", 0.9),
        Segment(30, 36, "Be careful with the signs of the currents, this will be on the quiz.", "en", 0.9)],
        duration_s=3000, speech_s=11, language_seconds={"en": 11}, model="m", device="cpu", engine_version="t",
        created_at="2026-09-23T12:00:00")
    lib.save_transcript(tr, store.save_transcript(tr))
    lib.set_status("d1", "done")
    return a


DAY = "c|2026-09-23|lecture"


def test_each_kind_of_document_gets_the_chosen_language(api, monkeypatch):
    from studia import ai, practice

    def langs(est):
        return est["language"], est["taught_in"]

    # the default: the course's language, English here, and no terms rule anywhere
    assert langs(api.quiz_estimate("c", {"weeks": [1]})["estimate"]) == ("English", "English")
    api.update_settings({"doc_language": "tr"})
    want = ("Turkish", "English")
    for kind in ("pack", "sheet", "prep", "videos"):                            # the dialogs' estimates say it
        assert langs(api.plan_estimate("c", {"kind": kind, "weeks": [1]})["estimate"]) == want, kind
    assert langs(api.quiz_estimate("c", {"weeks": [1]})["estimate"]) == want
    assert langs(api.cards_estimate("c", {"weeks": [1], "files": ["r1"]})["estimate"]) == want
    assert (api.quiz_options("c")["language"], api.quiz_options("c")["taught_in"]) == want
    s = api._s.settings
    for gather in (studypack.gather, quizzes.gather, cards.gather):
        f = gather(api._s.lib, s, "c", Scope(kind="pack", weeks=[1]))
        assert (f["language"], f["taught_in"]) == want, gather.__module__
    f = plans.gather(api._s.lib, s, "c", Scope(kind="prep", weeks=[1]))
    assert (f["language"], f["taught_in"]) == want and RULE_TR in plans.prep_prompt(Scope(kind="prep"), f)

    # study notes (and exam focus, flashcards)
    asked = []
    monkeypatch.setattr(ai, "run_claude", lambda prompt, text, images, **k: asked.append(prompt) or
                        {"text": "## Ohm\n\nNot.", "usage": {}, "model": "m"})
    api._s.ai_notes(DAY, "notes", False, "sonnet")
    assert f"Write in Turkish. {RULE_TR}" in asked[-1]

    # "Sum it up with AI"
    monkeypatch.setattr(practice, "_ai_ready", lambda: None)
    got = []
    monkeypatch.setattr(ai, "ask_json", lambda system, prompt, **kw: got.append(prompt) or (
        {"items": [{"point": "Mind the signs.", "why": "on the quiz", "cites": ["S1"]}]}, {"usage": {}}))
    assert api.stressed_summarize("c")["ok"]
    for _ in range(200):
        if any(e.get("type") == "stressed" and e.get("phase") in ("done", "error") for e in api.events):
            break
        time.sleep(0.02)
    assert got and f"Write in Turkish. {RULE_TR}" in got[0]

    # the weekly digest's summary
    d = digest.weekly_digest(api._s.lib, s, today=dt.date(2026, 9, 23))
    assert d["courses"] and d["courses"][0]["taught_in"] == "English" and (d["language"], d["taught_in"]) == want
    assert f"Write in Turkish. {RULE_TR}" in digest.digest_prompt(d)

    # English documents for a Turkish-taught course: the Turkish terms stay
    api._s.lib.set_course_info("c", syllabus={"language": "Turkish"})
    api.update_settings({"doc_language": "en"})
    assert langs(api.quiz_estimate("c", {"weeks": [1]})["estimate"]) == ("English", "Turkish")
    api._s.ai_notes(DAY, "exam", False, "sonnet")
    assert f"Write in English. {RULE_EN}" in asked[-1]


def test_a_quiz_written_in_turkish_for_an_english_course_asks_for_the_english_terms(api):
    asked = []
    answer = json.dumps({"title": "Ohm'un yasası", "topics": ["Ohm's law"], "questions": [
        {"type": "mc", "question": "Ohm's law (Ohm yasası) neyi söyler?", "options": ["V = IR", "V = I/R", "P = VI", "I = VR"],
         "answer": "A", "explanation": "Tanım.", "wrong": {"B": "Ters.", "C": "Güç.", "D": "Yanlış."}, "source": "F1 p.1"}]})

    def ask_text(system, content, model="sonnet", effort="low", cancel=None, on_text=None):
        asked.append(content[0]["text"])
        return {"text": answer, "usage": {"input_tokens": 100, "output_tokens": 50}, "model": model}

    api.update_settings({"doc_language": "tr"})
    runner = PlanRunner(api._s, ask_text=ask_text)
    pid = runner.create("c", {"kind": "quiz", "weeks": [1], "questions": 5, "qtypes": "mc", "focus": ""})
    runner.start(pid)
    t0 = time.monotonic()
    while api._s.lib.plan_brief(pid)["status"] not in ("done", "error", "paused") and time.monotonic() - t0 < 10:
        time.sleep(0.02)
    p = api._s.lib.plan(pid)
    assert p["status"] == "done", p.get("error")
    assert f"Write every question, option, answer and explanation in Turkish. {RULE_TR}" in asked[0]
    assert p["result"]["language"] == "Turkish"


# ------------------------------------------------------------------ the page (node: app.js and the pages)
node = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")

PAGE = r"""
  globalThis.setTimeout = later;
  els["#qz-cost"] = el(); els["#pd-cost"] = el(); els["#fm-cost"] = el();
  S.settings = { theme: "system", text_size: 100, device: "auto", capture_slides: true, ask_before_closing: true, min_duration_s: 60,
    write_srt: true, keep_audio: false, quiz_questions: 10, cards_per_day: 20, ai_model: "sonnet", plan_model: "", verify_model: "haiku",
    ai_model_light: "haiku", ai_effort: {}, output_dir: "C:/x", doc_language: "course" };
  S.boot = { app: { ai_provider: "claude", tray: false, sync_minutes: 30, notify_recordings: true, notify_announcements: true, notify_materials: false, notify_reminders: true } };
  S.profiles = []; S.view = { name: "settings" };
  St.renderView(); await tick(); St.renderView();
  out.page = plain(els["#view"].innerHTML);
  out.row = St.docLangRow();
  out.changed = St.setChanged("ai");
  await A.set({ dataset: { key: "doc_language", val: "tr" } });           // (the preview's update_settings keeps it)
  out.saved = S.settings.doc_language; out.changedAfter = St.setChanged("ai");
  out.rowAfter = St.docLangRow();
  const asked = [];
  St.ask = async (o) => { asked.push(o.text); return false; };
  await A["set-reset"]({ dataset: { group: "ai" } });
  out.resetText = asked[0];
  S.system.ai = { available: true, provider: "local" };
  out.local = St.docLangRow();
  S.system.ai = { available: true, provider: "claude" };
  out.lines = [St.docLangLine({ language: "English", taught_in: "English" }), St.docLangLine({ language: "Turkish", taught_in: "English" }),
    St.docLangLine(null)].map(plain);
  // the dialogs: the line comes with the estimate
  const est = { main_input: 20000, main_output: 4000, requests: 3, chapters: 3, cards: 20, pages: 9, minutes: "about 2–4 minutes",
    materials_used: [], days_used: [], files_cut: [], language: "Turkish", taught_in: "English" };
  const weeks = [{ no: 1, current: true, topic: "Circuits", files: [{ id: "f", name: "Week01.pdf", pages: 9, ready: true, downloaded: true }], days: [] }];
  St.mocks.quiz_options = () => ({ ok: true, model: "sonnet", ai: { available: true }, quant: true, assessments: [], weeks });
  St.mocks.quiz_estimate = () => ({ ok: true, estimate: est });
  St.mocks.pack_options = () => ({ ok: true, model: "sonnet", ai: { available: true }, weeks });
  St.mocks.plan_estimate = () => ({ ok: true, estimate: est });
  St.mocks.cards_estimate = () => ({ ok: true, estimate: est });
  St.mocks.plan_options = () => ({ ok: true, model: "sonnet", ai: { available: true }, assessments: [],
    weeks: [{ no: 1, current: true, start: "2026-09-21", end: "2026-09-27", topic: "Circuits", files: 1, files_ready: 1, missing: [], lectures: 0, lectures_done: 0, todo_days: [] }] });
  S.courses = [{ id: "m", code: "EEE 215", display_name: "Circuits", days: [], counts: {} }];
  await A["qz-new"]({ dataset: { cid: "m" } }); await tick(400);
  out.quiz = plain(els["#qz-cost"].innerHTML);
  await A["pack-new"]({ dataset: { id: "m", kind: "sheet" } }); await tick(400);
  out.sheet = plain(els["#pk-cost"].innerHTML);
  await A["plan-new"]({ dataset: { id: "m", kind: "prep" } }); await tick(400);
  out.prep = plain(els["#pd-cost"].innerHTML);
  await A["fc-make"]({ dataset: { cid: "m" } }); await tick(400);
  out.cards = plain(els["#fm-cost"].innerHTML);
  S.view = { name: "home" };
  A["doclang-settings"]();
  out.went = S.view.name;
  t.load({ lang: "tr", table: %(tr)s });
  out.trRow = plain(St.docLangRow());
  out.trLine = plain(St.docLangLine({ language: "Turkish", taught_in: "English" }));
  out.trSame = plain(St.docLangLine({ language: "English", taught_in: "English" }));
"""


@node
def test_settings_has_the_row_and_each_document_dialog_names_the_language(tmp_path):
    from test_v4_ux import run

    i18n.set_language("tr")
    try:
        table = json.dumps(i18n.table(), ensure_ascii=False)
    finally:
        i18n.set_language("en")
    out = run(tmp_path, PAGE.replace("%(tr)s", table))
    assert "Language of study documents" in out["page"] and "The course's language" in out["page"]
    assert 'id="set-doclang"' in out["row"] and 'data-key="doc_language" data-val="course" aria-pressed="true"' in out["row"]
    assert out["changed"] is False and out["saved"] == "tr" and out["changedAfter"] is True
    assert 'class="on" data-act="set" data-key="doc_language" data-val="tr"' in out["rowAfter"]
    assert out["resetText"].endswith("Study documents go back to the course's language.")
    assert out["local"] == ""                                                 # the offline model writes English
    assert out["lines"] == ["Written in English · change in Settings",
                            "Written in Turkish, exam terms in English · change in Settings", ""]
    for kind in ("quiz", "sheet", "prep", "cards"):
        assert "Written in Turkish, exam terms in English · change in Settings" in out[kind], (kind, out[kind])
    assert out["went"] == "settings"
    assert "Çalışma belgelerinin dili" in out["trRow"] and "Dersin dili" in out["trRow"]
    assert out["trLine"] == "Türkçe yazılır, sınav terimleri İngilizce · Ayarlar'dan değiştir"
    assert out["trSame"] == "İngilizce yazılır · Ayarlar'dan değiştir"
