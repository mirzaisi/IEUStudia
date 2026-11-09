"""The "Export for AI chat" dialog on the page (export.js in node with a stand-in for app.js): the weeks with their
number, topic and dates, Everything up to now picked at first, Select all and Clear, what goes in, the course files to
download first or leave out, the size and time; one click starts one export; while it works it says what it is doing
(the file it downloads, the week, the screens, writing the zip) with a bar and Cancel; at the end Show in folder,
Copy path and how to upload it to ChatGPT; a failure says why with Try again; closed, the end comes as a toast; opened
again while one is being made, it shows that one."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from studia import i18n

WEB = Path(i18n.__file__).parent / "web"

SCRIPT = """
globalThis.window = globalThis;
globalThis.location = { hash: "" };
globalThis.document = { documentElement: {}, querySelector: () => null, querySelectorAll: () => [], addEventListener() {},
  getElementById: () => null };
require(%(i18n)s);
const turkish = %(table)s;
const calls = [], toasts = [];
let shown = "";
const MB = 1048576;
const day = (n) => new Date(Date.UTC(2026, 8, 21 + n)).toISOString().slice(0, 10);
const week = (no, now, extra) => ({ no, start: day(7 * (no - 1)), end: day(7 * (no - 1) + 6), topic: "Topic " + no, current: no === 3, now,
  lectures: 1, untranscribed: 0, days: 1, files: 1, missing: [], links: 0, screens: 10, courses: ["MATH 240"],
  bytes: { text: 20000, screen_text: 21000, screens: 10 * 140000, files: 500000 }, ...(extra || {}) });
const answers = {
  course_export_state: { ok: true, running: false, export: null },
  course_export_options: { ok: true, all: false, courses: [{ id: "c", code: "MATH 240" }],
    weeks: [week(1, true), week(2, true, { missing: ["m2"], untranscribed: 1 }), week(3, true), week(4, false, { lectures: 0, files: 0, screens: 0 })],
    general: { files: 1, missing: [], bytes: 300000 }, loose: 0, base: 4000, notices: 5000, folder: "C:/x/Exports",
    free: 50 * 1024 * MB, chatgpt_max: 512 * MB },
  course_export_start: { ok: true, id: "x1" },
};
const S = { courses: [{ id: "c", code: "MATH 240", display_name: "Probability" }, { id: "e", code: "EEE 215", display_name: "Circuits" }] };
window.Studia = { S, A: {}, I: (n) => `[${n}]`, SPIN: () => "(spin)", esc: (s) => String(s ?? "").replace(/</g, "&lt;"), onEvents: {}, mocks: {},
  toast: (m, kind, action) => toasts.push([m, kind, action ? action.act : ""]), modal: (h) => { shown = h; },
  closeModal: () => { shown = ""; const hook = window.Studia.onModalClose; window.Studia.onModalClose = null; if (hook) hook(); },
  call: (name, ...args) => { calls.push([name, ...args]); return Promise.resolve(answers[name] || { ok: true }); } };
require(%(page)s);
const St = window.Studia, A = St.A, tick = () => new Promise((r) => setTimeout(r, 5));
const out = {};
(async () => {
  await A["cx-open"]({ dataset: { id: "c" } }); await tick();
  out.pick = shown;
  out.optionsCall = calls.find((c) => c[0] === "course_export_options");
  A["cx-inc"]({ dataset: { k: "screens" }, checked: false }); out.noScreens = shown;
  A["cx-inc"]({ dataset: { k: "screens" }, checked: true });
  A["cx-all"](); out.all = shown;
  A["cx-none"](); out.none = shown;
  A["cx-now"](); out.now = shown;
  A["cx-week"]({ dataset: { no: "3" }, checked: false }); out.two = shown;
  A["cx-week"]({ dataset: { no: "3" }, checked: true });
  A["cx-dl"]({ value: "no" }); out.leave = shown;
  A["cx-dl"]({ value: "yes" });
  const go1 = A["cx-go"](), go2 = A["cx-go"]();               // a double click
  out.starting = shown;
  await go1; await go2; await tick();
  out.startCalls = calls.filter((c) => c[0] === "course_export_start");
  out.run0 = shown;
  const ev = (e) => St.onEvents.course_export({ type: "course_export", id: "x1", course_id: "c", title: "MATH 240", ...e });
  ev({ phase: "progress", step: "download", i: 2, n: 5, name: "Week05.pdf", done: MB, total: 3 * MB, fraction: 0.3 }); out.dl = shown;
  ev({ phase: "progress", step: "start", estimate: 84 * MB, fraction: 0 });
  ev({ phase: "progress", step: "week", course: "MATH 240", ci: 1, cn: 1, week: 5, wi: 3, wn: 7, fraction: 0.4 }); out.week = shown;
  ev({ phase: "progress", step: "screens", i: 120, n: 568, fraction: 0.5 }); out.screens = shown;
  ev({ phase: "progress", step: "zip", fraction: 0.99 }); out.zip = shown;
  await A["cx-cancel"](); out.cancelling = shown;
  await A["cx-cancel"]();                                       // (clicked again: asked once)
  out.cancelCalls = calls.filter((c) => c[0] === "course_export_cancel").length;
  ev({ phase: "done", name: "MATH 240 - everything up to 7 Nov 2026.zip", bytes: 84.9 * MB, path: "C:/x/Exports/MATH 240.zip",
       untranscribed: 1, missing: 2, renamed: true, first_message: "This zip holds my MATH 240 course…", too_big: false });
  out.done = shown;
  await A["cx-show"](); await A["cx-copy-path"](); await A["cx-copy-msg"]();
  out.after = calls.slice(-3);
  ev({ phase: "error", message: "Your disk is full. Free up some space, then try again." }); out.error = shown;
  A["cx-again"](); out.again = shown;
  St.closeModal();                                              // closed: the end comes as a toast
  ev({ phase: "done", name: "z.zip", bytes: MB, path: "C:/x/z.zip" });
  ev({ phase: "error", message: "Boom." });
  out.toasts = toasts;
  answers.course_export_options = { ...answers.course_export_options, all: true,
    weeks: [week(1, true, { topic: "", courses: ["MATH 240", "EEE 215"], lectures: 2 })] };
  await A["cx-open"]({ dataset: { id: "" } }); await tick();
  out.allCourses = shown;
  out.allCall = calls.filter((c) => c[0] === "course_export_options").slice(-1)[0];
  t.load({ lang: "tr", table: turkish });
  A["cx-now"](); out.turkish = shown;
  t.load({ lang: "en", table: {} });
  answers.course_export_state = { ok: true, running: true, export: { type: "course_export", id: "x9", phase: "progress",
    step: "files", i: 3, n: 9, title: "EEE 215", fraction: 0.7, course_id: "e", course: "EEE 215", ci: 2, cn: 5, week: 4, wi: 4, wn: 7 } };
  St.closeModal();
  await A["cx-open"]({ dataset: { id: "c" } }); await tick();
  out.reopened = shown;
  out.calls = calls.map((c) => c[0]);
  console.log(JSON.stringify(out));
  process.stdout.write("", () => process.exit(0));      // after the output is out (Linux pipes)
})();
"""


def _mb(html):
    return float(re.search(r"About ([\d.]+) MB", html).group(1))


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_the_export_dialog(tmp_path):
    script = tmp_path / "export.js"
    script.write_text(SCRIPT % {"i18n": json.dumps(str(WEB / "i18n.js")), "page": json.dumps(str(WEB / "export.js")),
                                "table": json.dumps(i18n.table("tr"), ensure_ascii=False)}, encoding="utf-8")
    run = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    pick = out["pick"]
    assert out["optionsCall"] == ["course_export_options", "c"]
    # the weeks: number, topic and dates; Everything up to now picked (weeks begun), the coming ones not
    for no in (1, 2, 3):
        assert re.search(rf'data-act="cx-week" data-no="{no}" checked', pick)
    assert re.search(r'data-act="cx-week" data-no="4" >', pick)
    assert "W1" in pick and "Topic 2" in pick and "21 Sep – 27 Sep" in pick
    assert "everything up to now" in pick and '<option value="c" selected' in pick and "this week" in pick
    # what goes in: all four, on; a course file to download first (the default) or leave out; the untranscribed lecture
    for k in ("transcripts", "screens", "files", "notices"):
        assert f'data-act="cx-inc" data-k="{k}" checked' in pick
    assert "1 course file of these weeks is not on this PC." in pick and 'value="yes" checked' in pick
    assert "1 lecture of these weeks isn't transcribed yet: the zip lists it in its week." in pick
    assert re.search(r"About [\d.]+ MB · less than a minute · 1 file to download first", pick)
    assert 'data-act="cx-go" >' in pick and "Pick at least one week." not in pick
    # the screens left out make it smaller; Select all, Clear, a week unticked
    assert _mb(out["noScreens"]) < _mb(pick) and 'data-k="screens" >' in out["noScreens"]
    assert 'data-no="4" checked' in out["all"] and "4 weeks picked" in out["all"]
    assert 'data-act="cx-go" disabled' in out["none"] and "Pick at least one week." in out["none"]
    assert "everything up to now" in out["now"] and "2 weeks picked" in out["two"]
    assert 'value="no" checked' in out["leave"] and "file to download first" not in out["leave"]
    # one click, one export: the button says it is starting, and a double click asks once
    assert "Starting…" in out["starting"] and 'data-act="cx-go" disabled' in out["starting"]
    assert out["startCalls"] == [["course_export_start", "c", {"weeks": None, "transcripts": True, "screens": True,
                                                                "files": True, "notices": True, "download": True}]]
    assert "Exporting MATH 240…" in out["run0"] and 'data-act="cx-cancel"' in out["run0"]
    # what it is doing, with a bar
    assert "Downloading file 2 of 5: Week05.pdf (1.0 MB / 3.0 MB)" in out["dl"] and "width:30%" in out["dl"]
    assert "Week 5 (3 of 7): transcripts and course files" in out["week"] and "About 84 MB" in out["week"]
    assert "Adding screens 120 of 568" in out["screens"] and "week 5 (3/7)" in out["screens"]
    assert "Writing the zip…" in out["zip"] and "width:99%" in out["zip"]
    assert "Cancelling…" in out["cancelling"] and 'data-act="cx-cancel" disabled' in out["cancelling"]
    assert out["cancelCalls"] == 1
    # the end: where it is, Show in folder, Copy path, how to upload it, what isn't in it
    done = out["done"]
    assert "The zip is ready" in done and "MATH 240 - everything up to 7 Nov 2026.zip" in done and "85 MB" in done
    assert "C:/x/Exports/MATH 240.zip" in done
    for act in ("cx-show", "cx-copy-path", "cx-copy-msg"):
        assert f'data-act="{act}"' in done
    assert "Drag the zip into the chat" in done and "Open ChatGPT and start a new chat." in done
    assert "this one got a new name" in done and "1 lecture isn't transcribed yet" in done
    assert "2 course files aren't in the zip" in done
    assert out["after"] == [["course_export_show", "C:/x/Exports/MATH 240.zip"], ["copy_text", "C:/x/Exports/MATH 240.zip"],
                            ["copy_text", "This zip holds my MATH 240 course…"]]
    # a failure says why, with Try again back to the weeks
    assert "The zip couldn't be made." in out["error"] and "Your disk is full." in out["error"]
    assert 'data-act="cx-again"' in out["error"] and 'data-act="cx-go"' in out["again"]
    # with the dialog closed, the end is a toast (with Show in folder)
    assert ["The zip for ChatGPT is ready: z.zip (1.0 MB)", "success", "cx-show"] in out["toasts"]
    assert ["Boom.", "error", ""] in out["toasts"] and ["Copied", "success", ""] in out["toasts"]
    # all courses: the weeks say which courses have something then
    assert '<option value="" selected' in out["allCourses"] and "MATH 240, EEE 215" in out["allCourses"]
    assert out["allCall"] == ["course_export_options", ""]
    # in Turkish
    assert "Şu ana kadar her şey" in out["turkish"] and "Dışa aktar" in out["turkish"] and "Ders metinleri" in out["turkish"]
    # opened again while one is being made: that one, not a new dialog
    re_open = out["reopened"]
    assert "Exporting EEE 215…" in re_open and "Adding course files 3 of 9" in re_open
    assert "EEE 215 (2 of 5 courses)" in re_open and 'data-act="cx-go"' not in re_open
    assert "About 84 MB" not in re_open                         # (the size of the export before it is not shown)
    assert out["calls"][-1] == "course_export_state"


def test_the_buttons_are_on_the_course_page_and_home():
    course = (WEB / "course.js").read_text(encoding="utf-8")
    assert course.count('data-act="cx-open"') == 2 and 'data-act="cx-open" data-id="${c.id}"' in course
    assert 'data-act="cx-open" data-id=""' in course
    page = (WEB / "index.html").read_text(encoding="utf-8")
    assert page.index('src="app.js"') < page.index('src="course.js"') < page.index('src="export.js"')
    assert 'href="export.css"' in page


COPY = """
globalThis.window = globalThis;
globalThis.location = { hash: "" };
globalThis.document = { documentElement: {}, querySelector: () => null, querySelectorAll: () => [], addEventListener() {},
  getElementById: () => null };
require(%(i18n)s);
const copies = [], toasts = [];
window.Studia = { S: { courses: [] }, A: {}, I: (n) => `[${n}]`, SPIN: () => "", esc: (s) => String(s ?? ""), onEvents: {},
  mocks: {}, toast: (m, kind) => toasts.push([m, kind]), modal() {}, closeModal() {},
  copied: (r, text, done) => copies.push({ ok: r.ok, text, done }),          // setup.js's Copy failed dialog
  call: (name) => Promise.resolve(name === "copy_text" ? { ok: false, error: "Copy failed" } : { ok: true }) };
require(%(page)s);
const St = window.Studia;
(async () => {
  St.S.cexp = { phase: "done", path: "C:/x/Exports/MATH 240.zip", first_message: "This zip holds my MATH 240 course…" };
  await St.A["cx-copy-path"](); await St.A["cx-copy-msg"]();
  process.stdout.write(JSON.stringify({ copies, toasts }), () => process.exit(0));
})();
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_a_copy_the_clipboard_refuses_shows_the_text_to_copy_by_hand(tmp_path):
    """Copy path and Copy the first message go through the Copy failed dialog (which shows the text), not a toast."""
    script = tmp_path / "copy.js"
    script.write_text(COPY % {"i18n": json.dumps(str(WEB / "i18n.js")), "page": json.dumps(str(WEB / "export.js"))},
                      encoding="utf-8")
    out = json.loads(subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8",
                                    check=True, timeout=60).stdout)
    assert [c["text"] for c in out["copies"]] == ["C:/x/Exports/MATH 240.zip", "This zip holds my MATH 240 course…"]
    assert all(c["ok"] is False for c in out["copies"]) and out["toasts"] == []
