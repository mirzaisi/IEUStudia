"""4.2.0's last fixes on the page, in node with the stand-ins the other page tests use: a flashcard sitting counts
cards, not answers (a card answered Again and seen twice is one card); the Practice page's empty deck says missed quiz
questions become cards too; Home's course card shows a syllabus exam on the day set in its Exam room; Export for AI
chat leaves the screens out of a zip made for a quiz or exam (unless ticked) and says what they would add; and the
chat's header shows the model's name on a button whose menu says what each model is for, so the chat's name gets the
room."""

import json
import shutil

import pytest

import test_kit_page as kitpage
from test_chat_page import run as chat_run
from test_page_home import _page as home_page
from test_v42_qpractice_page import _page as practice_page

node = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
MB = 1048576


# ------------------------------------------------------------------ a sitting counts cards
@node
def test_a_sitting_counts_the_cards_gone_through_not_the_answers(tmp_path):
    out = practice_page(tmp_path, """
  const later = () => new Date(Date.now() + 86400e3).toISOString().slice(0, 19);
  ANSWERS.cards_grade = (cid, id, g, cram) => ({ ok: true, state: g === "again"
    ? { step: 0, due: new Date(Date.now() + 60e3).toISOString().slice(0, 19), next: {}, cram: !!cram }
    : { step: -1, due: later(), next: {}, cram: !!cram } });
  SESSION = { ok: true, cards: [card("k1"), card("k2"), card("k3")], total: 3, aside: 0, cram: true };
  await St.practice.study("c1", { weeks: [9, 10], cram: true }); await tick();
  A["fc-flip"](); await A["fc-grade"]({ dataset: { g: "again" } }); await tick();       // k1 comes back later
  out.afterAgain = shown.html;
  A["fc-flip"](); await A["fc-grade"]({ dataset: { g: "good" } }); await tick();
  out.afterOne = shown.html;
  const seen = [];
  while (P().study && P().study.queue[P().study.pos]) {
    seen.push(P().study.queue[P().study.pos].id);
    A["fc-flip"](); await A["fc-grade"]({ dataset: { g: "good" } }); await tick();
  }
  out.seen = seen; out.end = shown.html;
  // a normal sitting (cards due today) says the same
  ANSWERS.cards_grade = (cid, id, g) => ({ ok: true, state: g === "again"
    ? { step: 0, due: new Date(Date.now() + 60e3).toISOString().slice(0, 19), next: {} } : { step: -1, due: later(), next: {} } });
  SESSION = { ok: true, cards: [card("n1", { cram: false }), card("n2", { cram: false })], total: 2, aside: 0, cram: false };
  St.closeModal();
  await St.practice.study("c1"); await tick();
  A["fc-flip"](); await A["fc-grade"]({ dataset: { g: "again" } }); await tick();
  while (P().study && P().study.queue[P().study.pos]) { A["fc-flip"](); await A["fc-grade"]({ dataset: { g: "good" } }); await tick(); }
  out.normal = shown.html;
""")
    assert "3 left" in out["afterAgain"] and " done" not in out["afterAgain"]    # k1 is still to come again
    assert "2 left" in out["afterOne"] and "1 done" in out["afterOne"]
    assert out["seen"] == ["k3", "k1"]
    assert "Done with these weeks' cards" in out["end"] and "You went through 3 cards." in out["end"]
    assert "Done for today" in out["normal"] and "You went through 2 cards." in out["normal"]


@node
def test_the_practice_page_says_missed_questions_become_cards_too(tmp_path):
    out = practice_page(tmp_path, """
  ANSWERS.practice_summary = () => ({ ok: true, due: 0, courses: [{ id: "c1", code: "MATH 240", deck: { total: 0, today: 0 },
    quizzes: 0, material: 3, last_try: null, stressed: 0 }] });
  S.view = { name: "practice" };
  await St.onGo.practice(); await tick();
  out.page = St.views.practice();
""")
    assert "They come from a lecture's flashcards, from files and from the questions you miss in quizzes" in out["page"]


# ------------------------------------------------------------------ Home's course card
@node
def test_home_s_course_card_shows_a_syllabus_exam_on_the_day_set_in_its_exam_room(tmp_path):
    out = home_page(tmp_path, r"""
      const iso = (n) => { const d = new Date(); d.setDate(d.getDate() + n); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };
      const week = (no, from, label) => ({ no, start: iso(from), end: iso(from + 6), exam: !!label, exam_label: label || "", past: from < -6, days: [], units: [] });
      const weeks = [week(14, 0, ""), week(15, 7, "Final Exam"), week(16, 14, "Final Exam")];
      const final = (on) => ({ key: "syllabus:Final Exam:16", kind: "final", name: "Final Exam", week: 16, first_week: 15,
        syllabus: true, los: [], on: on || { date: "", from: "week" } });
      out.week = St.syllabusExam({ weeks, assessments: [final()] });
      out.set = St.syllabusExam({ weeks, assessments: [final({ date: iso(9), from: "yours" })] });
      out.setText = t.date(iso(9), "dmw");
      out.today = St.syllabusExam({ weeks, assessments: [final({ date: iso(0), from: "yours" })] });
      out.over = St.syllabusExam({ weeks: [week(15, -3, "Final Exam")], assessments: [final({ date: iso(-1), from: "yours" })] });
      out.none = St.syllabusExam({ weeks: [week(14, 0, "")], assessments: [] });""")
    assert out["week"] == {"name": "Final Exam", "when": "week 15"}
    assert out["set"] == {"name": "Final Exam", "when": out["setText"]}            # the day set in its Exam room
    assert out["today"] == {"name": "Final Exam", "when": "today"}
    assert out["over"] is None and out["none"] is None                            # its day is over: done


# ------------------------------------------------------------------ Export for AI chat: the screens of a kit
EXPORT_SCREENS = kitpage.STAND_IN + r"""
require(%(page)s);
const startOf = () => calls.filter((c) => c[0] === "course_export_start").pop()[2];
(async () => {
  await A["cx-open"]({ dataset: { id: "c" } }); await tick();
  out.plain = shown.html; out.plainScreens = /data-k="screens" checked/.test(shown.html);
  A["cx-for"]({ value: "quiz:2" });
  out.kit = shown.html; out.kitScreens = /data-k="screens" checked/.test(shown.html);
  A["cx-go"](); await tick();
  out.kitStart = startOf();
  St.onEvents.course_export({ type: "course_export", id: "x1", course_id: "c", phase: "cancelled" });
  A["cx-inc"]({ dataset: { k: "screens" }, checked: true });                    // ticked: they stay, For or not
  out.ticked = /data-k="screens" checked/.test(shown.html);
  A["cx-for"]({ value: "quiz:3" }); out.tickedKept = /data-k="screens" checked/.test(shown.html);
  A["cx-for"]({ value: "" }); out.tickedNone = /data-k="screens" checked/.test(shown.html);
  St.closeModal();
  await A["cx-open"]({ dataset: { id: "c", key: "quiz:2" } }); await tick();   // the Exam room's Export button
  out.room = /data-k="screens" checked/.test(shown.html);
  A["cx-inc"]({ dataset: { k: "notices" }, checked: false });                  // another box: For's "off" isn't kept
  St.closeModal();
  await A["cx-open"]({ dataset: { id: "c" } }); await tick();
  out.after = /data-k="screens" checked/.test(shown.html);
  process.stdout.write(JSON.stringify(out), () => process.exit(0));
})();
"""


@node
def test_a_zip_for_a_quiz_leaves_the_screens_out_unless_ticked_and_says_what_they_add(tmp_path):
    answers = json.loads(json.dumps(kitpage.EXPORT_ANSWERS))
    for w in answers["course_export_options"]["weeks"]:
        if w["now"]:
            w["screens"], w["bytes"] = 100, {"text": 200_000, "screen_text": 1 * MB, "screens": 30 * MB, "files": 2 * MB}
    out = kitpage._run(tmp_path, EXPORT_SCREENS, answers, page="export.js")
    assert out["plainScreens"] is True                               # without For: as before
    assert "They add about 93\u00a0MB." in out["plain"]              # weeks 1-3: 3 × 31 MB (kept on one line)
    assert out["kitScreens"] is False and "They add about 62\u00a0MB." in out["kit"]  # Quiz 2: weeks 2-3
    assert "About 4.4 MB" in out["kit"]                  # the zip's size without them (2 × 2.2 MB and 200 bytes)
    assert out["kitStart"]["screens"] is False and out["kitStart"]["focus"] == "quiz:2"
    assert out["ticked"] is True and out["tickedKept"] is True and out["tickedNone"] is True
    assert out["room"] is False and out["after"] is True


# ------------------------------------------------------------------ the styles
def test_a_topic_s_name_keeps_its_room_beside_its_counts_and_the_model_button_stays_small():
    """In the Exam room's column at 1024 px the name of a topic in "How well you know it" shrank to nothing and ran
    over its counts (the Turkish counts are long): it keeps some room, and in a narrow column the counts go under it."""
    mistakes = (kitpage.WEB / "mistakes.css").read_text(encoding="utf-8")
    styles = (kitpage.WEB / "styles.css").read_text(encoding="utf-8")
    assert "container-type: inline-size" in _rule(mistakes, ".mq-skills")
    name = _rule(mistakes, ".mq-skill-h .grow")
    assert "min-width: 7em" in name and "flex: 1 1 0" in name
    assert "flex-wrap: wrap" in _rule(mistakes, ".mq-skill-h")
    narrow = mistakes.split("@container (max-width: 560px)", 1)[1].split("\n}", 1)[0]
    assert ".mq-skill-h .mq-count" in narrow and "order: 9" in narrow and "flex-basis: 100%" in narrow
    assert "flex: none" in _rule(styles, ".model-btn") and ".model-sel" not in styles


def _rule(css, selector):
    import re

    m = re.search(re.escape(selector) + r"[^{]*\{([^}]*)\}", css)
    return m.group(1) if m else ""


# ------------------------------------------------------------------ the chat's header
@node
def test_the_chat_header_shows_the_model_s_name_and_its_menu_says_what_each_is_for(tmp_path):
    out = chat_run(tmp_path, """
  globalThis.setTimeout = later;
  els["#chat-head"] = {};
  const made = [], doc = globalThis.document;
  globalThis.document = new Proxy({}, { get: (t, k) => k === "createElement" ? () => {
      const m = { style: {}, setAttribute() {}, focus() {}, querySelector: () => null, offsetWidth: 220, offsetHeight: 120 };
      made.push(m); return m; }
    : k === "body" ? { appendChild() {} } : doc[k] });
  const btn = { getBoundingClientRect: () => ({ left: 600, top: 10, bottom: 40 }), dataset: { act: "chat-model" }, isConnected: true };
  const pick = (id) => made[made.length - 1].onclick({ target: { closest: () => ({ dataset: { menu: id } }) } });
  draft(); await tick(300);
  out.head = els["#chat-head"].innerHTML;
  A["chat-model"](btn);
  out.menu = made[made.length - 1].innerHTML;
  pick("opus"); await tick(300);
  out.draftModel = S.chat.draft.model; out.headOpus = els["#chat-head"].innerHTML;
  const r = await St.call("chat_create", "17933", [DAY], "full", false, "sonnet", "", null, "");
  St.go({ name: "chat", chatId: r.chat.id }); await tick(300);
  A["chat-model"](btn); pick("haiku"); await tick(300);
  out.chatModel = S.chat.data.model;
  out.headHaiku = els["#chat-head"].innerHTML;
""")
    button = out["head"].split('data-act="chat-model"', 1)[1].split("</button>", 1)[0]
    assert "<select" not in out["head"] and 'aria-haspopup="menu"' in out["head"]
    assert button.split(">", 1)[1].startswith("Sonnet") and "recommended" not in button.split(">", 1)[1]
    assert 'title="Model: Sonnet · recommended"' in out["head"]               # (what it is for: on hover)
    for name, what in (("Haiku", "lightest"), ("Sonnet", "recommended"), ("Opus", "best, uses the most")):
        assert f"<span>{name}<span class=\"d\">{what}</span></span>" in out["menu"]
    assert out["draftModel"] == "opus" and 'title="Model: Opus · best, uses the most">Opus' in out["headOpus"]
    assert out["chatModel"] == "haiku" and ">Haiku" in out["headHaiku"]
