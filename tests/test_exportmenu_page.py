"""The Export menu on the page (exportmenu.js in node with a stand-in for app.js and a small DOM): the button on a
lecture, a course and a chat; the format remembered (Word here) with its hint; a lecture's rows (transcript, the notes it
has, the AI pack) with Copy as text; subtitles (SRT or VTT) for the transcript only; a course's weeks with its options;
saving asks where (a cancelled dialog says nothing), says what it is making and how long it takes, then "Saved" with
Open and Show in folder, and the format is remembered; a failure says why; in Turkish."""

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
window.innerWidth = 1024; window.innerHeight = 700; window.addEventListener = () => {};
globalThis.location = { hash: "" };
const nodes = [];
function el(tag) {
  return { tag, className: "", id: "", innerHTML: "", style: {}, children: [], scrollTop: 0, offsetWidth: 336, offsetHeight: 420,
    setAttribute() {}, querySelector: () => null,
    remove() { let i = nodes.indexOf(this); if (i >= 0) nodes.splice(i, 1); i = toasts.children.indexOf(this); if (i >= 0) toasts.children.splice(i, 1); },
    get classList() { const self = this; return { contains: (c) => self.className.split(/\\s+/).includes(c) }; },
    appendChild(c) { this.children.push(c); } };
}
const toasts = el("div");
globalThis.document = { documentElement: {}, body: { appendChild(e) { nodes.push(e); } }, createElement: el,
  querySelector: (s) => s === "#menu" ? nodes.find((e) => e.id === "menu") || null : s === "#toasts" ? toasts : null,
  querySelectorAll: () => [], addEventListener() {}, getElementById: () => null };
require(%(i18n)s);
const turkish = %(table)s;
const calls = [], said = [];
let answers = {}, later = null;
const S = { settings: { export_prefs: { day: { fmt: "docx", sub: "srt" } } },
  day: { day_key: "c|2026-10-14|lecture", notes: [{ kind: "notes", label: "Study notes" }, { kind: "flashcards", label: "Flashcards" }], parts: [] } };
window.Studia = { S, A: {}, I: (n) => `[${n}]`, SPIN: () => "(spin)", esc: (s) => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;"),
  onEvents: {}, mocks: {}, toast: (m, kind, action) => said.push([m, kind, action ? action.act : ""]),
  copied: (r, text, done) => said.push(["copied", r.ok, done]),
  call: (name, ...args) => { calls.push([name, ...args]); const a = answers[name]; return typeof a === "function" ? a(...args) : Promise.resolve(a || { ok: true }); } };
require(%(page)s);
const St = window.Studia, A = St.A, tick = () => new Promise((r) => setTimeout(r, 5));
const menu = () => (nodes.find((e) => e.id === "menu") || {}).innerHTML || "";
const anchor = (place, id) => ({ dataset: { place, id }, getBoundingClientRect: () => ({ left: 500, right: 640, top: 100, bottom: 130 }) });
const out = {};
(async () => {
  out.dayBtn = St.exportBtn("day", "c|2026-10-14|lecture", false, false);
  out.chatBtn = St.exportBtn("chat", "ch1", true, true);
  const day = anchor("day", "c|2026-10-14|lecture");
  A["menu-export"](day); await tick();
  out.day = menu();
  out.where = [nodes.find((e) => e.id === "menu").style.left, nodes.find((e) => e.id === "menu").style.top];
  A["xp-fmt"]({ dataset: { fmt: "sub" } }); out.subs = menu();
  A["xp-sub"]({ dataset: { sub: "vtt" } }); out.vtt = menu();
  A["xp-fmt"]({ dataset: { fmt: "md" } }); out.md = menu();
  A["menu-export"](day); out.closed = menu();                    // the button again: closed
  // saved: where (the dialog), then made (a toast says so), then Saved with Open and Show in folder
  A["menu-export"](day); A["xp-fmt"]({ dataset: { fmt: "pdf" } });
  answers.export_pick = { ok: true, token: "t1", name: "MATH 240 - 2026-10-14 Wed - Lecture - Study notes.pdf", wait: 4, lectures: 1, zip: false };
  answers.export_write = () => new Promise((res) => { later = res; });
  const saving = A["xp-save"]({ dataset: { what: "notes:notes" } });
  A["xp-save"]({ dataset: { what: "notes:notes" } });            // a double click: one export
  await tick();
  out.afterPick = menu();
  out.busy = toasts.children.map((b) => [b.className, b.innerHTML]);
  later({ ok: true, path: "C:/Docs/IEUStudia/MATH 240/x.pdf", name: "x.pdf", folder: "C:/Docs/IEUStudia/MATH 240", bytes: 1000,
          prefs: { day: { fmt: "pdf", sub: "vtt", zip: false, notes: true } } });
  await saving; await tick();
  out.saved = toasts.children.map((b) => [b.className, b.innerHTML]);
  out.prefs = S.settings.export_prefs;
  await A["xp-open"]({ dataset: { path: "C:/Docs/IEUStudia/MATH 240/x.pdf" } });
  await A["xp-show"]({ dataset: { path: "C:/Docs/IEUStudia/MATH 240/x.pdf" } });
  // cancelled: nothing said, nothing made; a failure says why
  answers.export_pick = { ok: true, token: "" };
  A["menu-export"](day); await A["xp-save"]({ dataset: { what: "transcript" } }); await tick();
  answers.export_pick = { ok: false, error: "Transcribe this class day first." };
  A["menu-export"](day); await A["xp-save"]({ dataset: { what: "transcript" } }); await tick();
  answers.export_pick = { ok: true, token: "t2", name: "y.docx", wait: 1 };
  answers.export_write = { ok: false, error: "y.docx is open in another program, so it can't be saved over." };
  A["menu-export"](day); await A["xp-save"]({ dataset: { what: "pack" } }); await tick();
  // Copy as text
  answers.export_copy = { ok: true, chars: 41234 };
  A["menu-export"](day); await A["xp-copy"]({ dataset: { what: "transcript" } }); await tick();
  // a course: its weeks, its options, a zip that says how far it is
  answers.export_weeks = { ok: true, lectures: 9, notes: true, weeks: [{ no: 7, topic: "The Normal", current: true, lectures: 2 }, { no: 6, topic: "", lectures: 1 }] };
  const course = anchor("course", "c");
  let weeks;
  answers.export_weeks = new Promise((res) => { weeks = res; });
  A["menu-export"](course); await tick();
  out.loading = menu();
  weeks({ ok: true, lectures: 9, notes: true, weeks: [{ no: 7, topic: "The Normal", current: true, lectures: 2 }, { no: 6, topic: "", lectures: 1 }] });
  await tick(); await tick();
  out.course = menu();
  A["xp-opt"]({ dataset: { k: "zip" }, checked: true }); out.zip = menu();
  answers.export_pick = { ok: true, token: "t3", name: "MATH 240 - Week 7 - Lectures.zip", wait: 90, lectures: 2, zip: true };
  answers.export_write = () => new Promise((res) => { later = res; });
  const zipping = A["xp-save"]({ dataset: { what: "week:7" } }); await tick();
  out.zipBusy = toasts.children.map((b) => b.innerHTML).join("|");
  St.onEvents.xsave({ type: "xsave", token: "t3", i: 1, n: 2 });
  out.zipStep = toasts.children.map((b) => b.innerHTML).join("|");
  later({ ok: true, path: "C:/z.zip", name: "z.zip", folder: "C:/", bytes: 10 }); await zipping; await tick();
  answers.export_weeks = { ok: true, lectures: 0, notes: false, weeks: [] };
  A["menu-export"](course); await tick(); await tick(); out.none = menu();
  // a chat: no subtitles
  A["menu-export"](anchor("chat", "ch1")); out.chat = menu();
  t.load({ lang: "tr", table: turkish });
  A["xp-fmt"]({ dataset: { fmt: "docx" } }); out.chatTr = menu();
  A["menu-export"](day); out.dayTr = menu();
  out.dayBtnTr = St.exportBtn("day", "c|2026-10-14|lecture", false, true);
  out.calls = calls;
  out.said = said;
  console.log(JSON.stringify(out));
  process.stdout.write("", () => process.exit(0));      // after the output is out (Linux pipes)
})();
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_the_export_menu(tmp_path):
    script = tmp_path / "exportmenu.js"
    script.write_text(SCRIPT % {"i18n": json.dumps(str(WEB / "i18n.js")), "page": json.dumps(str(WEB / "exportmenu.js")),
                                "table": json.dumps(i18n.table("tr"), ensure_ascii=False)}, encoding="utf-8")
    run = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    # the buttons: a lecture's says Export; a chat's is a small icon, off while the chat is empty
    assert 'data-act="menu-export" data-place="day" data-id="c|2026-10-14|lecture"' in out["dayBtn"]
    assert "Export [chevron-down]" in out["dayBtn"] and "disabled" not in out["dayBtn"]
    assert "btn ghost icon sm" in out["chatBtn"] and "disabled" in out["chatBtn"] and "Export [" not in out["chatBtn"]
    # a lecture: Word remembered, its hint; the transcript, the notes it has (not exam focus), the AI pack, each copyable
    day = out["day"]
    assert re.search(r'class="on" data-act="xp-fmt" data-fmt="docx"', day) and "Best for editing, in Word or LibreOffice." in day
    for what in ("transcript", "notes:notes", "notes:flashcards", "pack"):
        assert f'data-act="xp-save" data-what="{what}"' in day and f'data-act="xp-copy" data-what="{what}"' in day
    assert "notes:exam" not in day and "The slides next to what was said, for ChatGPT or Claude" in day
    assert out["where"] == ["500px", "136px"]                         # under the button, as the app's menus are
    # subtitles: the transcript only, SRT or VTT
    assert 'data-what="transcript"' in out["subs"] and 'data-what="pack"' not in out["subs"]
    assert 'class="on" data-act="xp-sub" data-sub="srt"' in out["subs"] and "Each part as its own file" in out["subs"]
    assert 'class="on" data-act="xp-sub" data-sub="vtt"' in out["vtt"]
    assert "A prompt and the transcript, ready for ChatGPT or Claude" in out["md"] and "Obsidian" in out["md"]
    assert out["closed"] == ""
    # saving: one dialog for a double click; the menu closes; "Saving …" with the time; then Saved, Open, Show in folder
    picks = [c for c in out["calls"] if c[0] == "export_pick"]
    assert picks[0] == ["export_pick", "day:c|2026-10-14|lecture", "notes:notes", "pdf", {}]
    assert out["afterPick"] == ""
    busy = out["busy"]
    assert len(busy) == 1 and "xp-busy" in busy[0][0]
    assert "Saving MATH 240 - 2026-10-14 Wed - Lecture - Study notes.pdf…" in busy[0][1] and "This takes a few seconds." in busy[0][1]
    saved = out["saved"]
    assert len(saved) == 1 and "xp-saved" in saved[0][0] and "Saved x.pdf" in saved[0][1] and "C:/Docs/IEUStudia/MATH 240" in saved[0][1]
    assert 'data-act="xp-open" data-path="C:/Docs/IEUStudia/MATH 240/x.pdf"' in saved[0][1] and 'data-act="xp-show"' in saved[0][1]
    assert out["prefs"]["day"]["sub"] == "vtt"
    calls = [c for c in out["calls"] if c[0] in ("export_open", "export_write")]
    assert ["export_open", "C:/Docs/IEUStudia/MATH 240/x.pdf", False] in calls and ["export_open", "C:/Docs/IEUStudia/MATH 240/x.pdf", True] in calls
    assert ["export_write", "t1"] in calls and ["export_write", "t2"] in calls and len(picks) == 5
    assert len([c for c in calls if c[0] == "export_write"]) == 3                 # (none for the cancelled dialog)
    # cancelled: nothing; a failure: why; Copy as text
    assert ["Transcribe this class day first.", "error", ""] in out["said"]
    assert ["y.docx is open in another program, so it can't be saved over.", "error", ""] in out["said"]
    assert ["copied", True, "Copied as text (41,234 characters)."] in out["said"]
    assert ["export_copy", "day:c|2026-10-14|lecture", "transcript"] in out["calls"]
    assert len([s for s in out["said"] if s[1] == "error"]) == 2
    # a course: its weeks once they are read, the options; a zip says how far it is
    assert "Looking at the course's lectures…" in out["loading"]
    course = out["course"]
    assert 'data-what="all"' in course and "9 lectures" in course and "Week 7 · this week" in course and "The Normal · 2 lectures" in course
    assert 'data-k="notes" checked' in course and 'data-k="zip" >' in course and 'data-act="xp-copy"' not in course
    assert 'data-k="zip" checked' in out["zip"]
    assert ["export_pick", "course:c", "week:7", "pdf", {"zip": True, "notes": True}] in out["calls"]
    assert "This can take about 2 minutes." in out["zipBusy"] and "Lecture 2 of 2 · This can take about 2 minutes." in out["zipStep"]
    assert "No lecture of this course is transcribed yet." in out["none"]
    # a chat: no subtitles
    assert 'data-fmt="sub"' not in out["chat"] and 'data-what="chat"' in out["chat"] and "Your questions and the answers" in out["chat"]
    # in Turkish
    assert "Dışa aktar" in out["dayTr"] and "Ders metni" in out["dayTr"] and "Metin olarak kopyala" in out["dayTr"]
    assert "Bu sohbet" in out["chatTr"] and "disabled" in out["dayBtnTr"]


def test_the_menu_is_on_the_lecture_the_course_and_the_chat():
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert 'Studia.exportBtn("day", d.day_key, false, !hasText)' in app
    assert 'Studia.exportBtn("chat", c.id, true, !c.messages.length)' in app
    assert 'data-act="export"' not in app and "chat_export" not in app.split("const Mock")[0]   # (the old buttons)
    course = (WEB / "course.js").read_text(encoding="utf-8")
    assert 'St.exportBtn("course", c.id, false, !k.done)' in course
    page = (WEB / "index.html").read_text(encoding="utf-8")
    assert page.index('src="export.js"') < page.index('src="exportmenu.js"') and 'href="export.css"' in page
