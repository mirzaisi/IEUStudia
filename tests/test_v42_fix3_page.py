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
def test_the_exam_room_s_go_over_them_asks_for_the_mistakes_card(tmp_path):
    out = room_run(tmp_path, """
  St.openReady("c", "quiz:3"); await tick();
  A["er-practice"]({ dataset: { cid: "c" } }); await tick();
  out.view = { name: S.view.name, courseId: S.view.courseId, tab: S.view.tab, show: S.view.show };
""")
    assert out["view"] == {"name": "course", "courseId": "c", "tab": "practice", "show": "mistakes"}
