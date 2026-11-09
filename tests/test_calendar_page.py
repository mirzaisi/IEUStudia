"""4.0.0, the Calendar page (calendar.js) in node: the month with today and this week standing out, a week's items in
its row's band, classes as small marks, the picked day's list with how sure each date is; the list by weeks with each
day's classes on one line and past weeks folded away; the kinds and a course shown alone; your own entry's dialog;
the reminders' choices; and what Home's Coming up and a course's strip take from it."""

import datetime as dt
import json
import shutil
import subprocess

import pytest

from test_v4_pages import HARNESS, WEB

node = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
PAGES = '["i18n.js", "match.js", "icons.js", "app.js", "effort.js", "course.js", "plans.js", "studypack.js", "youtube.js", "digest.js", "calendar.js"]'


def run(tmp_path, body):
    script = tmp_path / "page.js"
    src = HARNESS.replace('["i18n.js", "match.js", "icons.js", "app.js", "effort.js", "course.js", "plans.js", "studypack.js", "youtube.js"]', PAGES)
    assert PAGES in src
    script.write_text(src % {"web": json.dumps(WEB.as_posix()), "body": body}, encoding="utf-8")
    r = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


# the term around today (node's clock): a quiz announced for this week (two class days), a midterm next week, an
# assignment due tomorrow at 15:40 and one sent, classes, a cancelled class, your entry, and a quiz three weeks ago
DATA = """
  const pad = (n) => String(n).padStart(2, "0");
  const iso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const shift = (n) => { const d = new Date(); d.setHours(0, 0, 0, 0); d.setDate(d.getDate() + n); return iso(d); };
  const today = shift(0), dow = (new Date().getDay() + 6) % 7, mon = shift(-dow);
  const at = (n) => { const d = new Date(`${mon}T00:00:00`); d.setDate(d.getDate() + n); return iso(d); };
  S.courses = [{ id: "a", code: "MATH 250", display_name: "Linear Algebra", days: [], counts: {} }, { id: "b", code: "EEE 242", display_name: "Digital", days: [], counts: {} }];
  const items = [
    { id: "a-quiz-2", kind: "quiz", course_id: "a", code: "MATH 250", title: "Quiz 2", date: "", week: { no: 7, start: mon, end: at(6) }, days: [at(2), at(4)], week_no: 7, sure: "week", los: [3, 4], source: { type: "announcement", id: "q" } },
    { id: "a-midterm-0", kind: "midterm", course_id: "a", code: "MATH 250", title: "Midterm", date: "", week: { no: 8, start: at(7), end: at(13) }, days: [], week_no: 8, sure: "week", los: [], source: { type: "announcement", id: "t" } },
    { id: "a-quiz-1", kind: "quiz", course_id: "a", code: "MATH 250", title: "Quiz 1", date: at(-19), time: "", week_no: 4, sure: "day", los: [], source: { type: "announcement", id: "o" } },
    { id: "b-asg-3", kind: "assignment", course_id: "b", code: "EEE 242", title: "Lab 3", date: shift(1), time: "15:40", sure: "exact", done: false, state: "pending", source: { type: "url", url: "https://learn.ieu.edu.tr/student#assignments/3" } },
    { id: "b-asg-2", kind: "assignment", course_id: "b", code: "EEE 242", title: "Lab 2", date: shift(2), time: "15:40", sure: "exact", done: true, state: "submitted", source: { type: "url", url: "u" } },
    { id: "a-class-x", kind: "class", class_kind: "lecture", course_id: "a", code: "MATH 250", date: shift(1), time: "10:20", end_time: "12:10", blocks: [["10:20", "12:10"]], sure: "schedule", source: { type: "week", week: 7 } },
    { id: "b-class-x", kind: "class", class_kind: "lab", course_id: "b", code: "EEE 242", date: shift(1), time: "14:10", end_time: "15:40", blocks: [["14:10", "15:40"]], sure: "schedule", source: { type: "week", week: 7 } },
    { id: "a-cancel-x", kind: "cancelled", class_kind: "lecture", course_id: "a", code: "MATH 250", date: shift(3), time: "", sure: "announced", note: "No class on Friday", source: { type: "announcement", id: "n" } },
    { id: "own-e1", entry_id: "e1", kind: "own", course_id: "", code: "", title: "Study group", date: shift(2), time: "14:00", note: "Library", sure: "yours", source: { type: "entry" } },
  ];
  const cal = { ok: true, today, now: "12:00", items, term: { start: at(-42), end: at(70) }, reminders: { exams: 2, due_days: 1, due_hours: 3 },
    courses: [{ id: "a", code: "MATH 250", name: "Linear Algebra", section: "", twin: false }, { id: "b", code: "EEE 242", name: "Digital", section: "", twin: false }] };
  const asked = [];
  St.mocks.calendar = () => { asked.push("calendar"); return cal; };
  St.mocks.calendar_save = (e) => { asked.push(["save", e]); return e.title ? { ...cal, entry: { ...e, id: "e2" } } : { ok: false, error: "Give the entry a title." }; };
  St.mocks.calendar_delete = (id) => { asked.push(["delete", id]); return cal; };
  St.mocks.calendar_reminders = (p) => { asked.push(["reminders", p]); return { ok: true, reminders: { ...cal.reminders, ...p } }; };
  const view = () => plain(St.views.calendar(S.view));
  const text = (h) => h.replace(/<[^>]*>/g, " ").replace(/\\s+/g, " ").trim();
"""


@node
def test_the_month_shows_today_this_week_and_how_sure_each_date_is(tmp_path):
    out = run(tmp_path, DATA + """
  S.view = { name: "calendar" };
  await (St.onGo.calendar(S.view), tick());
  const h = view();
  out.today = (h.match(/class="cal-cell[^"]*today[^"]*"/) || [""])[0];
  out.nowRow = /class="cal-row now"/.test(h);
  out.band = (h.split('class="cal-row now"')[1] || "").split('class="cal-days"')[0];
  out.dots = (h.match(/class="cal-dot[ "]/g) || []).length;
  out.dayList = text(h.split('class="cal-day-head"')[1] || "");
  out.sel = S.cal.sel === today;
  St.A["cal-day"]({ dataset: { date: shift(1) } });
  out.tomorrow = text(view().split('class="cal-day-head"')[1] || "");
""")
    assert "today" in out["today"] and "sel" in out["today"] and out["nowRow"]
    assert "Quiz 2" in out["band"] and "s-week" in out["band"]                 # a week's item, dashed, in its row
    assert out["dots"] == 2 and out["sel"]
    assert "Quiz 2" in out["dayList"] and "Week from an announcement" in out["dayList"] and "Get ready" in out["dayList"]
    t = out["tomorrow"]
    assert "Lab 3" in t and "due 15:40" in t and "Exact time from IEULearn" in t
    assert "MATH 250 10:20–12:10" in t and "EEE 242 Lab 14:10–15:40" in t      # the day's classes on one line


@node
def test_the_list_goes_by_weeks_folds_the_past_and_filters(tmp_path):
    out = run(tmp_path, DATA + """
  S.view = { name: "calendar" }; S.cal.view = "list";
  await (St.onGo.calendar(S.view), tick());
  let h = view();
  out.weeks = [...h.matchAll(/class="cal-wg-head"><b>([^<]*)<\\/b>/g)].map((m) => m[1]);
  out.earlier = text((h.match(/<button[^>]*data-act="cal-past"[^>]*>.*?<\\/button>/) || [""])[0]);
  out.quiz1 = h.includes("Quiz 1");
  out.sent = text(h.split('data-item="b-asg-2"')[1].split('class="ci-acts"')[0]);
  out.cancelled = text(h.split('data-item="a-cancel-x"')[1].split('class="ci-acts"')[0]);
  out.own = h.split('data-item="own-e1"')[1].split('class="cal-item')[0].includes('data-act="cal-del"');
  St.A["cal-past"](); out.quiz1After = view().includes("Quiz 1");
  St.A["cal-kind"]({ dataset: { g: "classes" } }); h = view();
  out.noClasses = !h.includes("cal-classes") && !h.includes("a-cancel-x");
  St.A["cal-kind"]({ dataset: { g: "classes" } }); St.A["cal-only"]({ dataset: { id: "b" } }); h = view();
  out.onlyB = h.includes("Lab 3") && !h.includes("Quiz 2") && h.includes("Study group") === false;
""")
    assert out["weeks"][:2] == ["This week", "Next week"]
    assert out["earlier"] == "Show 1 earlier week" and not out["quiz1"] and out["quiz1After"]
    assert "Sent" in out["sent"] and "No class" in out["cancelled"] and "“No class on Friday”" in out["cancelled"]
    assert out["own"] and out["noClasses"] and out["onlyB"]


@node
def test_your_own_entry_is_added_checked_and_deleted(tmp_path):
    out = run(tmp_path, DATA + """
  S.view = { name: "calendar" };
  await (St.onGo.calendar(S.view), tick());
  St.A["cal-new"]({ dataset: { date: shift(5) } });
  out.form = overlay();
  const values = { title: "", course_id: "a", date: shift(5), time: "09:00", note: "" };
  const form = { dataset: { form: "cal-entry", id: "" }, elements: { namedItem: (k) => ({ value: values[k] }) }, querySelector: () => null };
  const submit = (f) => listeners.submit.forEach((fn) => fn({ target: f, preventDefault() { out.prevented = true; } }));
  submit(form); await tick();
  out.refused = overlay();
  values.title = "Study for Quiz 2";
  submit(form); await tick();
  out.saved = asked.find((x) => x[0] === "save" && x[1].title);
  out.toasts = told();
  St.ask = async () => true;
  await St.A["cal-del"]({ dataset: { id: "e1" } }); await tick();
  out.deleted = asked.find((x) => x[0] === "delete");
  St.A["cal-open"]({ dataset: { id: "own-e1" } });          // its pencil: the entry to change
  out.edit = overlay();
""")
    assert "Change this entry" in out["edit"] and 'value="Study group"' in out["edit"] and 'data-id="e1"' in out["edit"]
    assert 'value="14:00"' in out["edit"] and ">Library</textarea>" in out["edit"] and "Delete" in out["edit"]
    form = out["form"]
    assert 'data-form="cal-entry"' in form and "Add to your calendar" in form and 'type="date"' in form and 'type="time"' in form
    assert out["prevented"] and "Give the entry a title." in out["refused"]
    assert out["saved"][1] == {"id": "", "title": "Study for Quiz 2", "course_id": "a", "date": out["saved"][1]["date"],
                               "time": "09:00", "note": ""}
    assert "success: Added to your calendar" in out["toasts"] and out["deleted"] == ["delete", "e1"]


@node
def test_the_reminders_choices_and_what_home_and_a_course_take(tmp_path):
    out = run(tmp_path, DATA + """
  S.view = { name: "calendar" }; S.boot = { app: { notify_reminders: false } };
  await (St.onGo.calendar(S.view), tick());
  St.A["cal-remind"]();
  out.dialog = overlay();
  await St.A["cal-rem"]({ dataset: { key: "due_hours" }, value: "6" });
  out.patch = asked.find((x) => x[0] === "reminders");
  out.soon = text(St.calSoon());
  const d = { assignments: [{ id: "3", title: "Lab 3", due: shift(1), time: "15:40", done: false, week: 7, url: "https://learn.ieu.edu.tr/student#assignments/3" },
                            { id: "2", title: "Lab 2", due: shift(-3), time: "", done: true, week: 7, url: "u" }] };
  out.pills = text(St.calPills(S.courses[1], d));
  out.weekPills = St.calWeekPills(d, { no: 7 });
""")
    dialog = out["dialog"]
    assert "Reminders are turned off in Settings" in dialog and 'data-key="exams"' in dialog
    assert '<option value="2" selected>2 days before</option>' in dialog and '<option value="3" selected>3 hours before</option>' in dialog
    assert out["patch"] == ["reminders", {"due_hours": 6}]
    soon = out["soon"]
    assert "Lab 3" in soon and "Study group" in soon and "No class" in soon and "Lab 2" not in soon      # (sent: not)
    assert out["pills"].startswith("Lab 3 due") and "15:40" in out["pills"]
    assert out["weekPills"].count("pill due kind-assignment") == 2 and "sent" in out["weekPills"]
