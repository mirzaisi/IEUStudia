"""4.0.0, the pages: lectures are counted as class days (a 3-hour class is recorded in 3 parts), and one with no
speech on Panopto is said apart instead of waiting to be transcribed for good; the study pack and cheatsheet dialog
ticks a file once the Download it offered brings it in; in an exam week the dialogs start from the weeks that have
material; a box being typed in keeps its focus and text when the page is drawn again; a lecture's end is never
followed by its older progress; "Hide very short recordings" shows on the course pages at once; Settings → Services
says each service's latest answer; counts in the chat and dialog sentences take their plural; the setup's contact
line stays in view; the tab bar has no stray scrollbar; a chat's title is shown whole on hover."""

import datetime as dt
import json
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

from studia import i18n
from studia.config import CoursePrefs, Settings
from studia.coursesite import Unit
from studia.domain import Course, Recording
from studia.library import Library

WEB = Path(i18n.__file__).parent / "web"
node = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")


def _rec(did, course, day, hour=9, name="Lecture", duration=3000, month=10):
    return Recording(delivery_id=did, course_id=course, folder_id="f", folder_path="", name=name,
                     start=dt.datetime(2026, month, day, hour, 0), duration_s=duration, viewer_url=f"https://v/{did}")


NO_SPEECH = [{"code": "no_speech", "level": "error", "message": "No speech was found in this recording (only noise or "
                                                                "music). Check it on Panopto."}]


def _lib(tmp_path):
    return _fill(Library(tmp_path / "lib.db"))


def _fill(lib):
    """MATH 250: Mon 5 Oct, two parts, both transcribed; Wed 7 Oct, three parts, two transcribed and one with no speech;
    Mon 12 Oct, two parts with no sound (one failed so before 2.3: only its error says it); Wed 14 Oct, one part not
    transcribed yet."""
    lib.upsert_courses([Course(id="a", title="2026-1 MATH 250 P 1", code="MATH 250", name="Linear Algebra", section="1")])
    lib.merge_recordings("a", [_rec("a1", "a", 5), _rec("a2", "a", 5, 10),
                               _rec("b1", "a", 7), _rec("b2", "a", 7, 10), _rec("b3", "a", 7, 11),
                               _rec("c1", "a", 12), _rec("c2", "a", 12, 10),
                               _rec("d1", "a", 14)], first_sync=True)
    for did in ("a1", "a2", "b1", "b2"):
        lib.set_status(did, "done")
    lib.set_status("b3", "failed", NO_SPEECH[0]["message"])
    lib.set_warnings("b3", NO_SPEECH)
    lib.set_status("c1", "failed", NO_SPEECH[0]["message"])
    lib.set_warnings("c1", NO_SPEECH)
    lib.set_status("c2", "failed", "This recording has no sound on Panopto.")            # (before 2.3: no warning)
    lib.merge_units("a", [Unit("u1", 0, "Hafta 1", dt.date(2026, 10, 5), dt.date(2026, 10, 11), "", []),
                          Unit("u2", 1, "Hafta 2", dt.date(2026, 10, 12), dt.date(2026, 10, 18), "", [])], first_sync=True)
    return lib


def _view(lib, settings=None, jobs=None):
    from studia import store, views

    settings = settings or Settings(min_duration_s=60)
    return views.course_view(lib.get_course("a"), lib.recordings("a"), settings, jobs or {}, lib.all_warnings(),
                             store.slide_ids())


# ------------------------------------------------------------------ lectures are class days; no speech is said apart
def test_lectures_are_class_days_and_one_with_no_speech_is_counted_apart(tmp_path):
    v = _view(_lib(tmp_path))
    days = {d["date"]: d for d in v["days"]}
    # all parts with sound transcribed, the rest has no speech: transcribed (it was "partly" with a Transcribe that
    # queued nothing); nothing of it has speech: no sound, nothing to transcribe
    assert {k: (d["transcribed"], d["any_done"], d["no_sound"], d["ready"]) for k, d in days.items()} == {
        "2026-10-05": (True, True, False, False), "2026-10-07": (True, True, True, False),
        "2026-10-12": (False, False, True, False), "2026-10-14": (False, False, False, True)}
    c = v["counts"]
    assert (c["days"], c["days_done"], c["days_no_sound"]) == (4, 2, 1)           # "2 of 3 lectures · 1 no speech"
    assert (c["total"], c["done"]) == (8, 4)                                       # the recordings stay as they were
    recs = {r["id"]: r for d in v["days"] for r in d["recordings"]}
    assert recs["c2"]["no_sound"] and recs["c1"]["no_sound"] and not recs["d1"]["no_sound"]   # (its error says it)


def test_a_lecture_without_speech_being_transcribed_again_by_hand_is_not_settled(tmp_path):
    v = _view(_lib(tmp_path), jobs={"c1": {"id": "c1", "stage": "transcribing", "progress": 0.2, "detail": ""}})
    day = next(d for d in v["days"] if d["date"] == "2026-10-12")
    assert not day["no_sound"] and day["ready"] and v["counts"]["days_no_sound"] == 0


def test_the_lecture_counts_follow_your_section(tmp_path):
    from studia import store, views

    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="b", title="2026-1 EEE 215 0 - 1", code="EEE 215", name="Circuits", section="0 - 1")])
    lib.merge_recordings("b", [_rec("b1", "b", 6, name="EEE 215 Section 1"), _rec("b2", "b", 6, 13, name="EEE 215 Section 3"),
                               _rec("b3", "b", 13, name="EEE 215 Section 1"), _rec("b4", "b", 13, 13, name="EEE 215 Section 3"),
                               _rec("b5", "b", 20, 13, name="EEE 215 Section 3")], first_sync=True)
    lib.set_status("b2", "done")
    s = Settings(course_prefs={"b": CoursePrefs(my_section="3", show_mode="mine")})
    v = views.course_view(lib.get_course("b"), lib.recordings("b"), s, {}, {}, store.slide_ids())
    c = v["counts"]
    assert (c["days"], c["days_done"]) == (3, 1)                 # section 3's class days (6, 13, 20 Oct)
    shared = next(d for d in v["days"] if d["date"] == "2026-10-06")     # section 1's part that day isn't yours:
    assert shared["transcribed"] and not shared["ready"]                 # yours is done ("partly" for good before)
    s.course_prefs["b"] = CoursePrefs(my_section="3", show_mode="all")   # every section's: all of its parts
    v = views.course_view(lib.get_course("b"), lib.recordings("b"), s, {}, {}, store.slide_ids())
    assert not next(d for d in v["days"] if d["date"] == "2026-10-06")["transcribed"]


def test_the_plan_dialog_leaves_a_lecture_without_speech_out_of_what_is_left_to_transcribe(tmp_path, isolated_home):
    from studia.api import Api
    from studia.services import Services

    api = Api(Services())
    _fill(api._s.lib)
    weeks = {w["no"]: w for w in api.plan_options("a")["weeks"]}
    assert (weeks[1]["lectures"], weeks[1]["lectures_done"], weeks[1]["todo_days"]) == (2, 2, [])
    assert (weeks[2]["lectures"], weeks[2]["lectures_done"]) == (1, 0)              # 12 Oct has no speech: not one
    assert [k.split("|")[1] for k in weeks[2]["todo_days"]] == ["2026-10-14"]


def test_the_sync_dialog_counts_class_days_as_lectures(tmp_path):
    from studia.catchup import course_needs, summary

    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="a", title="2026-1 MATH 250 P 1", code="MATH 250", name="Linear Algebra", section="1"),
                        Course(id="b", title="2026-1 EEE 215 0 - 1", code="EEE 215", name="Circuits", section="0 - 1")])
    lib.merge_recordings("a", [_rec(f"a{i}", "a", 5 + 2 * (i // 3), 9 + i % 3) for i in range(6)], first_sync=True)
    lib.merge_recordings("b", [_rec("b1", "b", 6, name="EEE 215 Section 1"), _rec("b2", "b", 6, 13, name="EEE 215 Section 3"),
                               _rec("b3", "b", 13, name="EEE 215 Section 1")], first_sync=True)
    sm = summary(course_needs(lib, Settings(min_duration_s=60)), gpu=True)
    by = {c["code"]: c for c in sm["courses"]}
    a, b = by["MATH 250"], by["EEE 215"]
    assert (a["lectures"], a["days"], sm["days"]) == (6, 2, 2)                    # 6 parts of 2 class days
    assert b["needs_section"] and (b["waiting"], b["waiting_days"]) == (3, 2)      # held back until you choose


@node
def test_home_and_the_course_page_say_class_days_and_the_lectures_with_no_speech(tmp_path):
    out = run(tmp_path, """
  St.sq = { card: () => "", line: () => "" };
  const c = (counts) => ({ id: "e", code: "EEE 215", display_name: "Circuits", has_panopto: true, days: [], content: {},
    available_sections: [], counts: { new: 0, processing: 0, done_any: 16, other_sections: 0, ...counts } });
  const silent = { day_key: "e|2026-09-30|lecture", date: "2026-09-30", label: "Wed 30 Sep", kind_label: "Lecture", parts: 3,
    transcribed: false, any_done: false, no_sound: true, is_new: false, mine: true, duration_s: 8000, live: false, ready: false, anomalies: [] };
  S.courses = [c({ total: 25, done: 16, days: 14, days_done: 10, days_no_sound: 4 })];
  S.cd = { e: { id: "e", weeks: [{ no: 2, start: "2026-09-28", end: "2026-10-04", past: true, current: true, exam: false, topic_en: "Nodal analysis",
    topic_tr: "", units: [], days: [silent], classes: [] }], general: [], materials: { total: 0, downloaded: 0, new: 0 }, syllabus: {},
    alerts: { items: [], plan: [], counts: {} }, announcements: [], outcomes_en: [], outcomes_tr: [], grading: [] } };
  S.view = { name: "home" };
  out.home = plain(St.views.home(S.view));
  S.view = { name: "course", courseId: "e" };
  out.course = plain(St.views.course(S.view));
  S.courses = [c({ total: 3, done: 3, days: 1, days_done: 1, days_no_sound: 0 })];
  out.one = plain(St.views.home({ name: "home" }));
""")
    bar = re.search(r'<span class="cc-bar" title="([^"]*)"><span>.*?</svg>(.*?)</span>.*?width:(\d+)%', out["home"], re.S)
    assert bar.group(2) == "10/10 lectures transcribed" and bar.group(3) == "100"        # it said 16/25, 64%
    assert bar.group(1) == "Lectures transcribed in IEUStudia · 4 more have no speech on Panopto"
    assert "<b>10</b>of 10 lectures transcribed" in out["course"]
    assert "<b>4</b>lectures have no speech on Panopto" in out["course"]
    chip = out["course"].split('class="lec ')[1].split('</div>')[0]
    assert "No speech found" in chip and 'data-act="tx-day"' not in chip              # its Transcribe queued nothing
    assert "1/1 lecture transcribed" in out["one"]


@node
def test_get_ready_leaves_a_lecture_without_speech_out_of_what_is_left(tmp_path):
    out = run(tmp_path, """
  St.sq = { card: () => "", line: () => "" };
  els[".modal.rd"] = el();
  St.mocks.plan_list = () => ({ ok: true, plans: [] }); St.mocks.course_marks = () => ({ ok: true, marks: [] });
  const day = (k, date, extra) => ({ day_key: k, date, label: date, kind_label: "Lecture", parts: 3, transcribed: false, any_done: false,
    no_sound: false, is_new: false, mine: true, duration_s: 8000, live: false, ready: true, anomalies: [], ...extra });
  S.courses = [{ id: "e", code: "MATH 250", display_name: "Linear Algebra", counts: {}, days: [
    { day_key: "e|1", recordings: [{ id: "r1", part: 1, mine: true, status: "failed", no_sound: true }] },
    { day_key: "e|2", recordings: [{ id: "r2", part: 1, mine: true, status: "done" }, { id: "r3", part: 2, mine: true, status: "failed", no_sound: true }] },
    { day_key: "e|3", recordings: [{ id: "r4", part: 1, mine: true, status: "available" }] }] }];
  S.cd = { e: { id: "e", general: [], outcomes_en: [], outcomes_tr: [],
    weeks: [{ no: 1, start: "2026-09-21", end: "2026-09-27", past: true, current: false, units: [], classes: [],
      days: [day("e|1", "2026-09-23", { no_sound: true, ready: false }), day("e|2", "2026-09-24", { transcribed: true, any_done: true, no_sound: true, ready: false })] },
      { no: 2, start: "2026-09-28", end: "2026-10-04", past: true, current: false, units: [], classes: [], days: [day("e|3", "2026-09-30")] }],
    alerts: { items: [], plan: [{ name: "Quiz 1", kind: "quiz", no: 1, week: 2, los: [], sources: [{ id: "a1", title: "Quiz 1" }] }], counts: {} } } };
  await St.openReady("e", "Quiz 1"); await tick();
  out.ready = plain(els[".modal.rd"].innerHTML).replace(/<[^>]*>/g, " ").replace(/\\s+/g, " ");
  out.button = (plain(els[".modal.rd"].innerHTML).match(/<button class="btn sm" data-act="rd-transcribe" title="([^"]*)"/) || [])[1];
""")
    assert "Lectures transcribed: 1 of 2" in out["ready"]                 # 23 Sep has no speech: not one to do
    assert "your section, in the weeks it covers · 1 more has no speech on Panopto" in out["ready"]
    assert out["button"] == "1 recording" and "Transcribe it" in out["ready"]   # not the parts without speech


@node
def test_the_sync_dialog_says_class_days(tmp_path):
    out = run(tmp_path, """
  require(web + "/catchup.js");
  els["#cu-body"] = el();
  S.courses = [{ id: "m", code: "MATH 250", display_name: "Linear Algebra", days: [], counts: {} }];
  St.mocks.catch_up_estimate = () => ({ ok: true, courses: [{ course_id: "m", code: "MATH 250", name: "Linear Algebra", lectures: 6, days: 2,
    audio_s: 17400, materials: 0, other_sections: 0, needs_section: false, waiting: 0, waiting_days: 0, sections: [], my_section: null,
    unknown: 0, unknown_audio_s: 0, unknown_days: 0 }], lectures: 6, days: 2, audio_s: 17400, materials: 0, work_s: 800, speed: 22, gpu: true,
    audio_mb: 217, needs_section: [], unknown: 0 });
  await A["course-sync"]({ dataset: { id: "m" } }); await tick();
  out.body = plain(els["#cu-body"].innerHTML).replace(/<[^>]*>/g, "");
  St.onEvents.catchup({ phase: "done", lectures: 6, days: 2, materials: 0, audio_s: 17400, waiting: [], unknown: 0 });
  out.toasts = told();
""")
    assert "2 lectures to transcribe" in out["body"] and "6 lectures" not in out["body"]   # 6 parts of 2 class days
    assert out["toasts"][-1].startswith("success: 2 lectures queued")


# ------------------------------------------------------------------ "Hide very short recordings" applies at once
def test_hide_very_short_recordings_sends_the_courses_at_once(isolated_home):
    from studia.api import Api
    from studia.services import Services

    api = Api(Services())
    lib = api._s.lib
    lib.upsert_courses([Course(id="e", title="2026-1 EEE 215 0 - 1", code="EEE 215", name="Circuits", section="1")])
    lib.merge_recordings("e", [_rec("s1", "e", 6, duration=37), _rec("s2", "e", 6, 10)], first_sync=True)
    pushed = []
    api._push = pushed.append
    api.update_settings({"min_duration_s": 60})
    pushed.clear()
    assert api.update_settings({"min_duration_s": 30})["ok"]
    (evt,) = pushed
    rec = {r["id"]: r for d in evt["courses"][0]["days"] for r in d["recordings"]}["s1"]
    assert evt["type"] == "courses" and rec["too_short"] is False and rec["part"] == 1
    pushed.clear()
    api.update_settings({"min_duration_s": 30, "theme": "dark"})                  # nothing about it changed: no event
    assert pushed == []


# ------------------------------------------------------------------ the page's events
class _Window:
    def __init__(self):
        self.calls = []

    def run_js(self, script):
        self.calls.append(script)


def _events(script):
    return json.loads(script[script.index("})([") + 3:-1].replace("<\\/", "</"))


def _wait(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while not cond() and time.monotonic() < end:
        time.sleep(0.01)
    return cond()


def test_a_lecture_s_end_is_not_followed_by_its_older_progress(isolated_home):
    from studia.api import Api

    api = Api()
    api._course_views = lambda: [{"id": "fresh"}]
    api._courses_dirty.clear()
    win = api._window = _Window()
    api._push({"type": "job", "id": "L1", "stage": "writing", "progress": 0.99, "detail": "Writing the files"})
    api._push({"type": "job", "id": "L1", "stage": "done", "final": True})
    api._push({"type": "job", "id": "L2", "stage": "transcribing", "progress": 0.4})
    api._push({"type": "queue", "jobs": []})
    assert _wait(lambda: win.calls)
    time.sleep(0.1)
    got = [(e["type"], e.get("id"), e.get("stage")) for e in _events(win.calls[0])]
    assert got == [("job", "L1", "done"), ("job", "L2", "transcribing"), ("queue", None, None)]   # no "writing" after it

    api._push({"type": "job", "id": "L1", "stage": "cancelled", "final": True})      # cancelled, then queued again:
    api._push({"type": "job", "id": "L1", "stage": "queued", "progress": 0.0})       # the new job's first event stays
    assert _wait(lambda: len(win.calls) == 2)
    assert [(e["id"], e["stage"]) for e in _events(win.calls[1])] == [("L1", "cancelled"), ("L1", "queued")]


def test_a_course_list_that_fails_to_build_does_not_stop_the_page_s_events(isolated_home):
    from studia.api import Api

    api = Api()

    def broken():
        raise RuntimeError("a damaged recording")

    api._course_views = broken
    win = api._window = _Window()
    api._courses_dirty.set()
    api._push({"type": "toast", "level": "info", "message": "one"})
    assert _wait(lambda: win.calls)
    api._push({"type": "toast", "level": "info", "message": "two"})                 # the thread is still there
    assert _wait(lambda: len(win.calls) == 2)
    assert [e["message"] for c in win.calls for e in _events(c)] == ["one", "two"]


# ------------------------------------------------------------------ Settings → Services: the latest answer
def test_services_say_the_latest_answer_not_the_last_one_written(isolated_home, monkeypatch):
    from studia import health

    stamps = iter(["2026-11-09T10:00:00", "2026-11-09T10:07:30"])
    monkeypatch.setattr(health, "_now", lambda: next(stamps))
    monkeypatch.setattr(health, "SEEN_EVERY_S", 10 ** 9)          # (the file's time is written again only so often)
    health.ok("panopto", "sessions")
    health.ok("panopto", "sessions")
    on_disk = json.loads(health._path().read_text(encoding="utf-8"))["parts"]["panopto.sessions"]["seen"]
    panopto = next(s for s in health.snapshot()["services"] if s["id"] == "panopto")
    assert on_disk == "2026-11-09T10:00:00" and panopto["seen"] == "2026-11-09T10:07:30"


@node
def test_settings_asks_for_the_services_when_it_opens_and_keeps_their_times_current(tmp_path):
    script = tmp_path / "svc.js"
    script.write_text("""
globalThis.window = globalThis;
globalThis.localStorage = { getItem: () => null, setItem() {} };
const card = { outerHTML: "", querySelector: () => null, contains: () => false };
const listeners = {};
globalThis.document = { documentElement: {}, hidden: false, activeElement: null, querySelector: () => null, querySelectorAll: () => [],
  getElementById: (id) => (id === "set-services" ? card : null), addEventListener: (n, f) => { listeners[n] = f; } };
const timers = [];
globalThis.setInterval = (f, ms) => { timers.push([f, ms]); return timers.length; };
globalThis.clearInterval = (id) => { if (timers[id - 1]) timers[id - 1][0] = null; };
require(%(i18n)s);
const calls = [];
let seen = new Date(Date.now() - 9 * 60000).toISOString();
const S = { view: { name: "home" }, sync: {}, boot: { services: { services: [{ id: "panopto", name: "Panopto", state: "ok", seen, parts: [] }], changed: [] } }, update: {} };
window.Studia = { S, A: {}, I: () => "", esc: (s) => String(s), toast() {}, rel: (iso) => Math.round((Date.now() - new Date(iso)) / 60000) + " min ago",
  call: (name) => { calls.push(name); return Promise.resolve({ ok: true, services: [{ id: "panopto", name: "Panopto", state: "ok", seen, parts: [] }], changed: [] }); },
  renderView() {}, renderTop() {}, go() {}, mocks: {}, onEvents: {}, settingsMore: () => "" };
require(%(services)s);
const St = window.Studia, out = {};
(async () => {
  S.view = { name: "settings" };
  seen = new Date().toISOString();                      // it answered just now (the start-up copy said 9 min ago)
  St.onGo.settings(S.view); await new Promise((r) => setTimeout(r, 20));
  out.opened = { calls: calls.splice(0), card: card.outerHTML };
  seen = new Date(Date.now() - 60000).toISOString();
  card.outerHTML = "";
  timers.filter((x) => x[0]).forEach(([f]) => f()); await new Promise((r) => setTimeout(r, 20));
  out.tick = { calls: calls.splice(0), card: card.outerHTML, every: timers[0][1] };
  S.view = { name: "home" };
  timers.filter((x) => x[0]).forEach(([f]) => f()); await new Promise((r) => setTimeout(r, 20));
  out.left = { calls: calls.splice(0), ticking: timers.filter((x) => x[0]).length };
  process.stdout.write(JSON.stringify(out), () => process.exit(0));
})();
""" % {"i18n": json.dumps(str(WEB / "i18n.js")), "services": json.dumps(str(WEB / "services.js"))}, encoding="utf-8")
    r = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["opened"]["calls"] == ["services_state"] and "(last answer 0 min ago)" in out["opened"]["card"]
    assert out["tick"]["calls"] == ["services_state"] and "(last answer 1 min ago)" in out["tick"]["card"]
    assert out["tick"]["every"] <= 30000
    assert out["left"] == {"calls": [], "ticking": 0}                  # not asked once Settings is left


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
const doc = { activeElement: null };
globalThis.document = new Proxy({}, { get: (t, k) => k === "compatMode" ? "CSS1Compat"
  : k === "activeElement" ? doc.activeElement
  : k === "hidden" ? false
  : k === "createElement" ? () => el()
  : k === "querySelector" ? (s) => (s in els ? els[s] : stub())
  : k === "getElementById" ? (id) => els["#" + id] || null
  : k === "querySelectorAll" ? () => []
  : k === "addEventListener" ? (n, f) => { (listeners[n] = listeners[n] || []).push(f); } : stub() });
const listeners = {};
globalThis.localStorage = { getItem: () => null, setItem() {} };
globalThis.navigator = { userAgent: "node", platform: "Win32" };
globalThis.CSS = { escape: (s) => s };
globalThis.addEventListener = () => {}; globalThis.matchMedia = () => ({ matches: false, addEventListener() {} });
globalThis.requestAnimationFrame = (f) => f();
const later = setTimeout;
globalThis.setInterval = () => 0; globalThis.setTimeout = () => 0;      // no boot, no polling
for (const f of ["i18n.js", "match.js", "icons.js", "app.js", "effort.js", "course.js", "plans.js", "studypack.js", "youtube.js"]) require(web + "/" + f);
const St = globalThis.Studia, S = St.S, A = St.A;
St.aiName = () => "Claude";
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
    script.write_text(HARNESS % {"web": json.dumps(WEB.as_posix()), "body": body}, encoding="utf-8")
    r = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


# ------------------------------------------------------------------ the study pack / cheatsheet dialog
PACK = """
  globalThis.setTimeout = later;                         // (the dialog's refresh waits 300 ms)
  let ready = false, asked = 0;
  St.mocks.pack_options = (cid) => { asked++; return { ok: true, course: { id: cid, code: "MATH 240" }, model: "sonnet", ai: { available: true },
    weeks: [{ no: 7, start: "2026-10-26", current: true, past: false, topic: "Bayes", days: [],
      files: [{ id: "a", name: "Week07_Student.pdf", pages: 12, ext: "pdf", ready: true, downloaded: true },
              { id: "b", name: "Week07_Teacher.pdf", pages: ready ? 40 : 0, ext: "pdf", ready, downloaded: ready }] }] }; };
  St.mocks.materials_download = (ids) => ({ ok: true, queued: ids.length });
  St.mocks.plan_estimate = () => ({ ok: true, estimate: { main_model: "sonnet", main_input: 20000, main_output: 9000, requests: 1, chapters: 1, files: 2, lectures: 0,
    materials_missing: [], days_missing: [], files_cut: [], minutes: "about 3–6 minutes" } });
  S.courses = [{ id: "m", code: "MATH 240", display_name: "Probability", days: [], counts: {} }];
  const box = (id) => (overlay().match(new RegExp('<input[^>]*data-id="' + id + '"[^>]*>')) || [""])[0];
  const row = (id) => (overlay().split('data-id="' + id + '"')[1] || "").split("</label>")[0].replace(/<[^>]*>/g, " ").replace(/\\s+/g, " ").trim();
  await A["pack-new"]({ dataset: { id: "m" } }); await tick();
  out.before = { box: box("b"), gap: /Download 1/.test(overlay()) };
"""


@node
def test_a_file_downloaded_from_the_pack_dialog_is_ticked_once_it_is_in(tmp_path):
    out = run(tmp_path, PACK + """
  await A["pk-download"](); await tick();
  out.during = { row: row("b"), gap: /Download 1/.test(overlay()), toasts: told() };
  ready = true;
  ev({ type: "material", id: "b", state: "done", pages: 40 }); await tick(450);
  out.after = { box: box("b"), row: row("b"), gap: /Download 1/.test(overlay()), asked, cost: plain(els["#pk-cost"].innerHTML) };
  A["pk-item"]({ dataset: { kind: "file", id: "b" }, checked: false }); await tick();        // you untick it:
  ev({ type: "material", id: "a", state: "done" }); await tick(450);                         // another file in
  out.kept = box("b");
  St.closeModal(); els["#overlay .modal.pk"] = null; const n = asked;                       // closed: stays closed
  ev({ type: "material", id: "b", state: "done" }); await tick(450);
  out.closed = { asked: asked - n, html: els["#overlay"].innerHTML };
""")
    assert "disabled" in out["before"]["box"] and "checked" not in out["before"]["box"] and out["before"]["gap"]
    assert out["during"]["row"].endswith("downloading…") and not out["during"]["gap"]       # not offered twice
    assert out["during"]["toasts"] == ["success: Downloading 1 file. It's ticked here once it's on your PC."]
    assert "checked" in out["after"]["box"] and "disabled" not in out["after"]["box"] and out["after"]["row"].endswith("40 pages")
    assert not out["after"]["gap"] and out["after"]["asked"] == 2
    assert "checked" not in out["kept"]
    assert out["closed"] == {"asked": 0, "html": ""}


@node
def test_a_failed_download_gives_the_pack_dialog_its_button_back(tmp_path):
    out = run(tmp_path, PACK + """
  await A["pk-download"](); await tick();
  ev({ type: "material", id: "b", state: "error", error: "Can't reach learn.ieu.edu.tr.", quiet: true }); await tick();
  out.after = { row: row("b"), gap: /Download 1/.test(overlay()) };
""")
    assert out["after"]["row"].endswith("not on this PC") and out["after"]["gap"]


@node
def test_in_an_exam_week_the_pack_dialog_starts_from_the_last_week_with_material(tmp_path):
    out = run(tmp_path, """
  const week = (no, files, extra = {}) => ({ no, start: "2026-10-" + (no + 10), current: no === 8, past: no < 8, topic: no === 8 ? "Midterm" : "Week " + no, days: [], files, ...extra });
  const f = (id, ready) => ({ id, name: id + ".pdf", pages: 9, ext: "pdf", ready, downloaded: ready });
  St.mocks.plan_estimate = () => ({ ok: true, estimate: { main_input: 1, main_output: 1, requests: 9, chapters: 3, files_cut: [], minutes: "" } });
  S.courses = [{ id: "e", code: "EEE 215", display_name: "Circuits", days: [], counts: {} }];
  const picked = () => [...overlay().matchAll(/class="pk-wk on[^"]*" data-act="pk-week" data-no="(\\d+)"/g)].map((m) => +m[1]);
  St.mocks.pack_options = (cid) => ({ ok: true, course: { id: cid }, model: "sonnet", ai: { available: true }, weeks: [week(6, [f("w6", true)]), week(7, [f("w7", true)]), week(8, [])] });
  await A["pack-new"]({ dataset: { id: "e" } }); await tick();
  out.empty = picked();
  St.mocks.pack_options = (cid) => ({ ok: true, course: { id: cid }, model: "sonnet", ai: { available: true }, weeks: [week(7, [f("w7", true)]), week(8, [f("w8", false)])] });
  await A["pack-new"]({ dataset: { id: "e" } }); await tick();
  out.newFile = picked();             // week 8's new slides are listed: it is picked, with its Download
""")
    assert out["empty"] == [7] and out["newFile"] == [8]


@node
def test_in_an_exam_week_the_plan_dialog_picks_the_weeks_the_exam_covers_and_never_plans_from_nothing(tmp_path):
    out = run(tmp_path, """
  const week = (no, exam, current) => ({ no, start: "2026-09-" + String(no + 10).padStart(2, "0"), end: "", current, past: !current && no < 15, topic: exam ? "Midterm" : "Topic " + no,
    exam, files: exam ? 0 : 1, files_ready: exam ? 0 : 1, missing: [], lectures: exam ? 0 : 1, lectures_done: exam ? 0 : 1, todo_days: [] });
  S.courses = [{ id: "e", code: "EEE 215", display_name: "Circuits", days: [], counts: {} }];
  St.mocks.plan_options = (cid) => ({ ok: true, course: { id: cid }, model: "sonnet", ai: { available: true }, assessments: [],
    weeks: [1, 2, 3, 4, 5, 6, 7, 8, 9].map((n) => week(n, n === 8, n === 8)) });
  await A["plan-new"]({ dataset: { id: "e" } }); await tick();
  const go = () => (overlay().match(/<button class="btn primary" data-act="pd-go"[^>]*>/) || [""])[0];
  out.midterm = { weeks: (overlay().match(/(\\d+) picked/) || [])[1], go: go() };
  A["pd-thisweek"](); await tick();
  out.alone = { go: go(), note: overlay().includes("An exam week has no files or lectures of its own: pick the weeks the exam covers.") };
  St.mocks.plan_options = (cid) => ({ ok: true, course: { id: cid }, model: "sonnet", ai: { available: true }, assessments: [],
    weeks: [6, 7, 8, 9, 10, 11, 12].map((n) => week(n, n === 8 || n === 12, n === 12)) });
  await A["plan-new"]({ dataset: { id: "e" } }); await tick();
  out.final = (overlay().match(/(\\d+) picked/) || [])[1];
  St.mocks.plan_options = (cid) => ({ ok: true, course: { id: cid }, model: "sonnet", ai: { available: true }, assessments: [],
    weeks: [7, 8, 9].map((n) => ({ ...week(n, n === 8, n === 8), files: 1 })) });
  await A["plan-new"]({ dataset: { id: "e" } }); await tick();
  out.withFiles = { weeks: (overlay().match(/(\\d+) picked/) || [])[1], go: go() };      // an exam week with a file of its own
""")
    assert out["midterm"]["weeks"] == "7" and "disabled" not in out["midterm"]["go"]     # weeks 1-7, not week 8 alone
    assert "disabled" in out["alone"]["go"] and out["alone"]["note"]
    assert out["final"] == "3"                                                        # after the midterm: weeks 9-11
    assert out["withFiles"]["weeks"] == "1" and "disabled" not in out["withFiles"]["go"]


# ------------------------------------------------------------------ typing while the page is drawn again
@node
def test_a_box_being_typed_in_keeps_its_focus_and_caret_when_the_page_is_drawn_again(tmp_path):
    out = run(tmp_path, """
  const old = { matches: (s) => s.includes("input[type=search]"), dataset: { act: "mat-filter", id: "e" }, selectionStart: 3, selectionEnd: 3 };
  const fresh = { focused: 0, range: null, focus() { this.focused++; doc.activeElement = this; }, setSelectionRange(a, b) { this.range = [a, b]; } };
  els["#view"].contains = (x) => x === old;
  els["#view"].querySelector = (s) => (s === '[data-act="mat-filter"][data-id="e"]' ? fresh : null);
  St.views.test = () => '<input type="search" data-act="mat-filter" data-id="e" value="wee">';
  S.view = { name: "test" }; doc.activeElement = old;
  St.renderView();                                     // a lecture's stage changed meanwhile
  out.kept = { focused: fresh.focused, range: fresh.range };
  const button = { matches: () => false, dataset: { act: "go" } };
  doc.activeElement = button; fresh.focused = 0;
  els["#view"].contains = (x) => x === button;
  St.renderView();
  out.button = fresh.focused;                          // only boxes to type in
  St.inputs["yt-q"]({ value: "3blue1brown" });         // the YouTube channel search keeps what is typed
  out.yt = { q: S.yt.q, card: St.settingsMore().includes('data-act="yt-q" value="3blue1brown"') };
""")
    assert out["kept"] == {"focused": 1, "range": [3, 3]} and out["button"] == 0
    assert out["yt"] == {"q": "3blue1brown", "card": True}


# ------------------------------------------------------------------ the chat list
@node
def test_a_chat_s_whole_title_shows_on_hover_and_is_cut_with_an_ellipsis(tmp_path):
    out = run(tmp_path, """
  S.chat.list = [{ id: "c1", title: "What will most likely be on the quiz this Friday?", course_code: "MATH 240", scope_label: "2 lectures",
    updated_at: new Date().toISOString(), model: "sonnet" }];
  S.view = { name: "chat" }; St.renderView();
  out.item = els["#view"].innerHTML.split('class="chat-items"')[1];
""")
    item = out["item"]
    assert 'title="What will most likely be on the quiz this Friday?"' in item
    assert '<span class="tt">What will most likely be on the quiz this Friday?</span>' in item
    css = (WEB / "styles.css").read_text(encoding="utf-8")
    assert re.search(r"\.chat-item \.t \.tt \{[^}]*text-overflow: ellipsis", css)


# ------------------------------------------------------------------ counts in sentences take their plural
@node
def test_counts_in_the_chat_and_dialog_sentences_take_their_plural(tmp_path):
    out = run(tmp_path, """
  out.one = St.inRequests(1); out.three = St.inRequests(3);
  out.big = St.effortBig.read({ batches: 1, tokens: 900000 });
  t.load({ lang: "tr", table: { "in {n} request": "{n} istekte" } });
  out.tr = St.inRequests(3);
""")
    assert (out["one"], out["three"], out["tr"]) == ("in 1 request", "in 3 requests", "3 istekte")
    assert "about 900k tokens in 1 request before the answer" in out["big"]


def test_no_count_is_put_before_a_plural_noun_without_its_singular():
    """t("… in {b} requests") read "in 1 requests": a count in a sentence goes through t.n (or a t.n piece)."""
    bad = []
    for p in sorted(WEB.glob("*.js")):
        if p.name in ("export.js", "cheatsheet.js"):    # (the export's and the cheatsheet's own pages)
            continue
        for m in re.finditer(r'\bt(?:\.html)?\(\s*"([^"]*\{\w+\} (?:requests|pages|pieces|pictures|chapters|lectures|files)\b[^"]*)"',
                             p.read_text(encoding="utf-8")):
            bad.append(m.group(1))
    always_many = ("Cancel all {n} lectures in the queue?",          # (asked only for more than one)
                   "{ai} got {sent} of the {total} video slides",    # (the most pictures a chat takes)
                   "{ai} sees formulas, figures and handwriting as they are (PDF files). Up to {n} pictures per chat")
    assert [b for b in bad if not b.startswith(always_many)] == []


@node
def test_the_pack_dialog_s_cost_line_says_one_request_and_one_chapter(tmp_path):
    out = run(tmp_path, PACK + """
  await tick(400);                                     // (its estimate: plan_estimate above says 1 request, 1 chapter)
  out.cost = plain(els["#pk-cost"].innerHTML).replace(/<[^>]*>/g, " ").replace(/\\s+/g, " ");
""")
    assert "About 29.0k tokens in 1 request (1 chapter), about 3–6 minutes." in out["cost"]


# ------------------------------------------------------------------ the look
def test_the_tab_bar_never_scrolls_up_and_down():
    css = (WEB / "styles.css").read_text(encoding="utf-8")
    rule = re.search(r"\n\.tabs \{([^}]*)\}", css).group(1)
    assert "overflow-x: auto" in rule and "overflow-y: hidden" in rule
    assert re.search(r"::-webkit-scrollbar-corner \{ background: transparent; \}", css)
