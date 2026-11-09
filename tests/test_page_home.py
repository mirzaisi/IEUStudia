"""The Home screen's course pages (course.js, run in node with a stand-in for app.js): after a "courses" event Home
asks again only for the courses that changed (or whose details are old), and draws itself once per frame however
many answers arrive; a course's page likewise, and files downloading for a course ask for it once, after the last."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from studia import i18n

WEB = Path(i18n.__file__).parent / "web"

SCRIPT = """
globalThis.window = globalThis;
globalThis.document = { documentElement: {}, querySelector: () => null, querySelectorAll: () => [], addEventListener() {} };
globalThis.localStorage = { getItem: () => null, setItem() {} };
globalThis.CSS = { escape: (s) => s };
let frames = [];
globalThis.requestAnimationFrame = (f) => frames.push(f);
const frame = () => { const fs = frames; frames = []; fs.forEach((f) => f()); };
require(%(i18n)s);
const asked = [], draws = [];
const course = (id, done) => ({ id, code: "C " + id, counts: { total: 3, done }, days: [{ day_key: id + "|d", recordings: [
  { id: id + "r", status: "transcribing", progress: 0.1, detail: "passage 1 of 9" }] }] });
const S = { courses: ["a", "b", "c"].map((id) => course(id, 1)), view: { name: "home" }, sync: {} };
const detail = (cid) => ({ id: cid, weeks: [], general: [{ materials: [{ id: cid + "m1" }, { id: cid + "m2" }] }] });
window.Studia = { S, call: (name, cid) => { if (name === "course_detail") asked.push(cid); return Promise.resolve({ ok: true, course: detail(cid) }); },
  esc: (s) => s, I: () => "", SPIN: () => "", toast() {}, go() {}, renderView: () => draws.push(S.view.name), renderSidebar() {},
  emptyHTML: () => "", skeleton: () => "", openDay() {}, jumpTo() {}, A: {}, views: {}, onGo: {}, after: {}, onEvents: {},
  mocks: {} };
require(%(course)s);
const St = window.Studia, wait = (ms) => new Promise((r) => setTimeout(r, ms));
const out = {};
(async () => {
  St.onGo.home();                                       // the first visit: every course
  await wait(10); frame();
  out.first = { asked: asked.splice(0), draws: draws.splice(0) };
  const sync = (courses) => { S.courses = courses; St.onEvents.courses({}); };
  sync(["a", "b", "c"].map((id) => course(id, 1)));     // a sync that found nothing new
  await wait(300); frame();
  out.same = { asked: asked.splice(0), draws: draws.splice(0) };
  const busy = ["a", "b", "c"].map((id) => course(id, 1));
  busy[2].days[0].recordings[0].progress = 0.6; busy[2].days[0].recordings[0].detail = "passage 6 of 9";
  sync(busy);                                           // only a running job's progress moved
  await wait(300); frame();
  out.progress = { asked: asked.splice(0), draws: draws.splice(0) };
  sync([course("a", 1), course("b", 2), course("c", 1)]);   // b's lecture is transcribed
  await wait(300); frame();
  out.changed = { asked: asked.splice(0), draws: draws.splice(0) };
  const now = Date.now;
  Date.now = () => now() + 11 * 60000;                  // details from 11 minutes ago
  sync([course("a", 1), course("b", 2), course("c", 1)]);
  await wait(300); frame();
  Date.now = now;
  out.old = { asked: asked.splice(0).sort(), draws: draws.splice(0) };
  S.view = { name: "course", courseId: "a" };           // a frame that comes after you left Home draws nothing
  sync([course("a", 1), course("b", 3), course("c", 1)]);
  await wait(300); frame();
  out.left = { asked: asked.splice(0), draws: draws.splice(0) };
  sync([course("a", 2), course("b", 3), course("c", 1)]);   // the course page: when its course changed
  await wait(300); frame();
  out.page = { asked: asked.splice(0), draws: draws.splice(0) };
  S.mat = {};                                           // two of its files downloading: once the last is in
  const mat = (id, state) => St.onEvents.material({ id, state });
  mat("am1", "queued"); mat("am2", "queued"); mat("bm1", "queued");
  mat("am1", "done");
  await wait(300);
  out.file1 = asked.splice(0);
  mat("am2", "done");
  await wait(300);
  out.file2 = asked.splice(0);
  console.log(JSON.stringify(out));
})();
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_home_and_course_pages_ask_again_only_for_changed_courses_and_draw_once_per_frame(tmp_path):
    script = tmp_path / "home.js"
    script.write_text(SCRIPT % {"i18n": json.dumps(str(WEB / "i18n.js")), "course": json.dumps(str(WEB / "course.js"))},
                      encoding="utf-8")
    out = json.loads(subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8",
                                    check=True).stdout)
    assert out["first"] == {"asked": ["a", "b", "c"], "draws": ["home"]}          # three answers, one redraw
    assert out["same"] == {"asked": [], "draws": ["home"]}
    assert out["progress"] == {"asked": [], "draws": ["home"]}
    assert out["changed"] == {"asked": ["b"], "draws": ["home"]}
    assert out["old"] == {"asked": ["a", "b", "c"], "draws": ["home"]}
    assert out["left"] == {"asked": [], "draws": []}               # another course changed: the page stays
    assert out["page"] == {"asked": ["a"], "draws": ["course"]}
    assert out["file1"] == [] and out["file2"] == ["a"]           # (b's file waiting doesn't hold a back)


PAGE = """
globalThis.window = globalThis;
globalThis.document = { documentElement: {}, querySelector: () => null, querySelectorAll: () => [], addEventListener() {} };
globalThis.localStorage = { getItem: () => null, setItem() {} };
globalThis.CSS = { escape: (s) => s };
globalThis.requestAnimationFrame = () => {};
require(%(i18n)s);
const S = { courses: [], view: { name: "home" }, sync: {} };
window.Studia = { S, call: () => Promise.resolve({ ok: true }), I: () => "", SPIN: () => "", toast() {}, go() {},
  esc: (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])),
  renderView() {}, renderSidebar() {}, emptyHTML: () => "", skeleton: () => "", openDay() {}, jumpTo() {}, A: {}, views: {},
  onGo: {}, after: {}, onEvents: {}, mocks: {} };
require(%(course)s);
const St = window.Studia, out = {};
%(body)s
console.log(JSON.stringify(out));
"""


def _page(tmp_path, body):
    script = tmp_path / "page.js"
    script.write_text(PAGE % {"i18n": json.dumps(str(WEB / "i18n.js")), "course": json.dumps(str(WEB / "course.js")),
                              "body": body}, encoding="utf-8")
    return json.loads(subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8",
                                     check=True).stdout)


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_an_announcement_s_links_and_attached_files_can_be_clicked(tmp_path):
    out = _page(tmp_path, r"""out.html = St.linkText("Join: Lesson | Teams (https://teams.microsoft.com/l/x?a=1&b=2).\n" +
      "Quiz1.pdf 142 KB (https://learn.ieu.edu.tr/student/announcements/5961/file/0) <b>\n" +
      "https://teams.microsoft.com/l/meetup-join/19%3ameeting_ZmQ1YjQ2NjctMjM0Ny00@thread.v2/0");""")
    html = out["html"]
    assert ('(<a class="ext" role="link" tabindex="0" data-act="url" data-url="https://teams.microsoft.com/l/x?a=1&amp;b=2"'
            in html and ">https://teams.microsoft.com/l/x?a=1&amp;b=2</a>).\n" in html)
    assert ('data-url="https://learn.ieu.edu.tr/student/announcements/5961/file/0"' in html and
            ">https://learn.ieu.edu.tr/student/announcements/5961/file/0</a>) &lt;b&gt;\n" in html)   # text escaped
    assert ">teams.microsoft.com/l/meetup-join/19%3ameetin…</a>" in html                             # long: shortened


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_coming_up_has_the_syllabus_exam_weeks_no_announcement_covers(tmp_path):
    out = _page(tmp_path, r"""
      const iso = (n) => { const d = new Date(); d.setDate(d.getDate() + n); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };
      const week = (no, from, label) => ({ no, start: iso(from), end: iso(from + 6), exam: !!label, exam_label: label || "", past: from < -6 });
      const weeks = [week(7, -7, "Midterm"), week(8, 0, "Midterm Exam"), week(9, 7, ""), week(15, 49, "Final")];
      const names = (plan) => St.examWeeks({ weeks, alerts: { plan } }).map((x) => [x.name, x.w.no, x.r.now]);
      out.none = names([]);
      out.quiz = names([{ kind: "quiz", no: 2, name: "Quiz 2", week: 8, los: [], sources: [] }]);
      out.announced = names([{ kind: "midterm", no: 0, name: "Midterm", week: 8, los: [], sources: [] }]);""")
    assert out["none"] == [["Midterm Exam", 8, True]]          # not last week's, nor the final weeks away
    assert out["quiz"] == [["Midterm Exam", 8, True]]          # a quiz that week doesn't cover the midterm
    assert out["announced"] == []                              # announced: Coming up has it from the plan
