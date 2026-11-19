"""4.2.0's fixes from the last two real-app journeys on the page (v42-fix3), in node with the stand-ins the other page
tests use: a quiz written from an Exam room shows its row once the course page is drawn (it was at the top, the row
out of sight, as if nothing had happened), and the room's Go over them shows the Mistakes card."""

import json
import shutil
import subprocess

import pytest

import test_v42_qpractice_page as qp
from test_examroom_page import run as room_run

node = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")


def _practice(tmp_path, body):
    """The Practice tab's stand-in, with go() kept (the view it was asked for)."""
    page = qp.PAGE.replace("go: () => {},", "go: (v) => { globalThis.gone.push(v); },").replace(
        "globalThis.window = globalThis;", "globalThis.window = globalThis; globalThis.gone = [];", 1)
    script = tmp_path / "page.js"
    script.write_text(page % {"i18n": json.dumps(str(qp.WEB / "i18n.js")), "practice": json.dumps(str(qp.WEB / "practice.js")),
                              "mistakes": json.dumps(str(qp.WEB / "mistakes.js")), "body": body}, encoding="utf-8")
    r = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


# ------------------------------------------------------------------ the row of a quiz just started
@node
def test_a_quiz_written_from_another_page_shows_its_row_once_the_course_page_is_drawn(tmp_path):
    out = _practice(tmp_path, """
  const wk = (no, current) => ({ no, current, past: !current, topic: "T" + no, files: [{ id: "f" + no, name: "W" + no + ".pdf", pages: 3, ready: true, downloaded: true }], days: [] });
  ANSWERS.quiz_options = () => ({ ok: true, model: "sonnet", ai: { available: true }, quant: false, weak: { missed: 0, attempts: 0 },
    weeks: [wk(9), wk(10, true)], assessments: [{ key: "quiz:3", kind: "quiz", no: 3, name: "Quiz 3", week: 10, weeks: [9, 10], los: [], ahead: true, files: [] }] });
  ANSWERS.quiz_estimate = () => ({ ok: true, estimate: null });
  ANSWERS.quiz_create = (cid, sc) => ({ ok: true, plan: { id: "z9", scope: sc, status: "queued" } });
  S.view = { name: "exam", courseId: "c1", key: "quiz:3" };                       // (the Exam room)
  await St.practice.quizDialog("c1", { mode: "next", key: "quiz:3" }); await tick();
  await A["qz-go"]({ disabled: false, innerHTML: "" }); await tick();
  const v = gone.pop();
  out.view = { ...v };
  // its row, drawn: scrolled to only once the course's details are in too (they move it down)
  const row = { style: {}, scrolled: null, scrollIntoView(o) { this.scrolled = o; } };
  const bar = { dataset: { id: "z9" }, closest: (s) => (s === ".pr-quiz" ? row : null) };
  let mq = null;
  document.querySelectorAll = (sel) => (sel === ".pr-prog[data-id]" ? [bar] : []);
  document.querySelector = (sel) => (sel === '[data-act="mq-weak"]' ? mq : null);
  P().course.c1 = P().course.c1 || { quizzes: [] };
  St.after.course(v); out.early = [v.show, row.scrolled];
  S.cd = { c1: { id: "c1" } };
  St.after.course(v); out.drawn = [v.show, row.scrolled, row.style.scrollMarginBottom];
  row.scrolled = null; St.after.course(v); out.again = row.scrolled;                // (once: not on every redraw)
  // the Exam room's Go over them: the Mistakes card
  const card = { style: {}, scrolled: null, scrollIntoView(o) { this.scrolled = o; } };
  mq = { closest: (s) => (s === ".mq-card" ? card : null) };
  const m = { name: "course", courseId: "c1", tab: "practice", show: "mistakes" };
  St.after.course(m); out.mistakes = [m.show, card.scrolled];
  const other = { name: "course", courseId: "c1", tab: "weeks", show: "mistakes" };
  card.scrolled = null; St.after.course(other); out.otherTab = [other.show, card.scrolled];
""")
    assert out["view"] == {"name": "course", "courseId": "c1", "tab": "practice", "show": "z9"}
    assert out["early"] == ["z9", None]                                  # not drawn with the course's details yet
    assert out["drawn"] == [None, {"block": "center", "behavior": "instant"}, "72px"]
    assert out["again"] is None
    assert out["mistakes"] == [None, {"block": "center", "behavior": "instant"}]
    assert out["otherTab"] == ["mistakes", None]


@node
def test_a_missed_question_s_card_in_a_cram_says_it_comes_back_in_this_sitting_not_that_it_is_marked(tmp_path):
    """In a cram an MC card answered wrong said 'The app marks it Again: it comes back soon.', though a cram changes no
    card's schedule (and its header said 'flashcards' where the others said 'cram')."""
    out = qp._page(tmp_path, """
  const mc = (id, extra) => card(id, { origin: "quiz", quiz: { type: "mc", title: "Quiz 3 practice", options: ["a", "b", "c", "d"], answer: 0, wrong: ["", "x", "y", "z"] }, ...extra });
  ANSWERS.cards_grade = (cid, id, g, cram) => ({ ok: true, state: g === "again"
    ? { step: 0, due: new Date(Date.now() + 60e3).toISOString().slice(0, 19), next: {}, cram: !!cram }
    : { step: -1, due: new Date(Date.now() + 86400e3).toISOString().slice(0, 19), next: {}, cram: !!cram } });
  SESSION = { ok: true, cards: [mc("m1"), mc("m2")], total: 2, aside: 0, cram: true };
  await St.practice.study("c1", { weeks: [9, 10], cram: true }); await tick();
  out.head = shown.html;
  A["fc-pick"]({ dataset: { i: "2" } }); await tick(); out.wrong = shown.html;
  St.mistakes && A["fc-mc-next"] && await A["fc-mc-next"](); await tick();
  A["fc-pick"]({ dataset: { i: "0" } }); await tick(); out.right = shown.html;
  // a day's sitting (not a cram): marked as before
  St.closeModal();
  SESSION = { ok: true, cards: [mc("d1", { cram: false })], total: 1, aside: 0, cram: false };
  await St.practice.study("c1"); await tick();
  A["fc-pick"]({ dataset: { i: "1" } }); await tick(); out.day = shown.html;
""")
    assert "MATH 240 · cram" in out["head"] and "flashcards" not in out["head"].split("</b>")[0]
    assert "It comes back once more before you finish." in out["wrong"] and "marks it Again" not in out["wrong"]
    assert "Going over it now doesn&#39;t change when it comes back." in out["right"] or \
        "Going over it now doesn't change when it comes back." in out["right"]
    assert "The app marks it Again: it comes back soon." in out["day"]


@node
def test_the_room_of_an_exam_that_is_over_says_so_and_offers_nothing_to_get(tmp_path):
    """A midterm whose week was over still said 'Mon 2 Nov · done', 'Get ready for it', 'Still missing … Get
    everything' and 'Set the exact day': one click queued 16 lectures for an exam already written."""
    out = room_run(tmp_path, """
  const over = { date: inDays(-5), time: "", from: "class", others: [], days: -5 };
  St.mocks.exam_room = () => ({ ok: true, room: room(QUIZ3, { when: over }) });
  St.openReady("c", "quiz:3"); await tick();
  out.past = text(view()); out.pastHtml = view();
  const week = { date: inDays(-12), time: "", from: "week", others: [], days: -12, end: inDays(-6) };
  St.mocks.exam_room = () => ({ ok: true, room: room(QUIZ3, { when: week }) });
  St.openReady("c", "quiz:3"); await tick();
  out.week = text(view());
  const thisWeek = { date: inDays(-2), time: "", from: "week", others: [], days: -2, end: inDays(4) };
  St.mocks.exam_room = () => ({ ok: true, room: room(QUIZ3, { when: thisWeek }) });
  St.openReady("c", "quiz:3"); await tick();
  out.thisWeek = text(view());
  St.mocks.exam_room = () => ({ ok: true, room: room(QUIZ3) });
  St.openReady("c", "quiz:3"); await tick();
  out.ahead = text(view());
""")
    p = out["past"]
    assert "It was on " in p and "Set the exact day" not in p and "Change the day" not in p
    assert "Get everything" not in p and "Still missing" not in p and 'data-act="er-get"' not in out["pastHtml"]
    assert "Go over it" in p and "Get ready for it" not in p and "Practice quiz for it" in p    # (its tools stay)
    assert "It was in the week of " in out["week"] and "exam schedule on IEULearn" not in out["week"]
    assert "this week" in out["thisWeek"] and "Get ready for it" in out["thisWeek"]          # (its week isn't over)
    a = out["ahead"]
    assert "Get ready for it" in a and "Set the exact day" in a and "Get everything" in a and "It was" not in a


@node
def test_the_room_says_when_a_week_already_taught_has_no_lecture_here_and_leaves_exam_weeks_out(tmp_path):
    """Quiz 3's room said 'Everything of these weeks is on this PC.' with a green tick while week 9's class, held
    since the last sync, wasn't in IEUStudia; the final's said 'Nothing yet for week 8, week 16' (exam weeks)."""
    out = room_run(tmp_path, """
  S.cd = { c: { id: "c", weeks: [{ no: 3, days: [{ day_key: "x" }] }], assessments: [] } };
  const mat = { days: [{ day_key: DAY, date: "2026-11-16", label: "", kind_label: "Lecture", week: 10, transcribed: true, any_done: true, live: false, busy: false, read: false }],
    files: [{ id: "w9", title: "Week09", name: "Week09.pdf", ext: "pdf", downloaded: true, week: 9, tied: false }],
    lectures: 1, silent: 0, transcribed: 1, busy: 0, readable: 1, read: 0, downloaded: 1, todo: [], missing: [] };
  St.mocks.exam_room = () => ({ ok: true, room: room(QUIZ3, { material: mat }) });
  St.openReady("c", "quiz:3"); await tick();
  out.unseen = text(view()); out.unseenHtml = view();
  // all its weeks have a lecture: the green tick as before
  St.mocks.exam_room = () => ({ ok: true, room: room({ ...QUIZ3, weeks: [10] }, { material: mat }) });
  St.openReady("c", "quiz:3"); await tick();
  out.all = text(view());
  // week 9 without a lecture, but the course has one in week 11: no class that week (a holiday), nothing to sync
  S.cd.c.weeks.push({ no: 11, days: [{ day_key: "y" }] });
  St.mocks.exam_room = () => ({ ok: true, room: room(QUIZ3, { material: mat }) });
  St.openReady("c", "quiz:3"); await tick();
  out.holiday = text(view());
  // the final: its exam week (16) isn't a week with "nothing yet"
  St.mocks.exam_room = () => ({ ok: true, room: room(FINAL) });
  St.openReady("c", FINAL.key); await tick();
  out.final = view().split('class="er-week dim sm er-none">')[1].split("</div>")[0];
  // asking about these weeks with none transcribed: the tooltip says why it can't
  const none = { ...mat, days: mat.days.map((x) => ({ ...x, transcribed: false, any_done: false })) };
  St.mocks.exam_room = () => ({ ok: true, room: room(QUIZ3, { material: none }) });
  St.openReady("c", "quiz:3"); await tick();
  out.ask = view().split('data-act="er-ask"')[1].split(">")[0];
""")
    assert "No lecture of week 9 is in IEUStudia yet." in out["unseen"] and "Everything of these weeks" not in out["unseen"]
    assert 'data-act="sync"' in out["unseenHtml"]
    assert "Everything of these weeks is on this PC." in out["all"]
    assert "Everything of these weeks is on this PC." in out["holiday"] and "No lecture of" not in out["holiday"]
    assert "16" not in out["final"] and "Nothing yet for" in out["final"]
    assert "0 transcribed" not in out["ask"] and 'title="Transcribe a lecture of these weeks first' in out["ask"]


@node
def test_the_chat_s_exam_room_chip_is_named_as_the_page_and_left_out_without_exams(tmp_path):
    out = room_run(tmp_path, """
  S.chat = S.chat || {}; S.chat.data = { course_id: "c" }; S.chat.draft = null;
  S.cd = { c: { id: "c", weeks: [], assessments: [{ key: "quiz:3", name: "Quiz 3", ahead: true }] } };
  out.some = St.chatToolsHTML();
  S.cd.c.assessments = [];
  out.none = St.chatToolsHTML();
""")
    assert 'data-cmd="ready"' in out["some"] and "Exam room" in out["some"] and "Get ready" not in out["some"]
    assert 'data-cmd="ready"' not in out["none"] and 'data-cmd="sync"' in out["none"]


@node
def test_the_exam_room_s_go_over_them_asks_for_the_mistakes_card(tmp_path):
    out = room_run(tmp_path, """
  St.openReady("c", "quiz:3"); await tick();
  A["er-practice"]({ dataset: { cid: "c" } }); await tick();
  out.view = { name: S.view.name, courseId: S.view.courseId, tab: S.view.tab, show: S.view.show };
""")
    assert out["view"] == {"name": "course", "courseId": "c", "tab": "practice", "show": "mistakes"}
