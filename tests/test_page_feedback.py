"""What the page tells you (app.js, course.js, digest.js, chatactions.js, plans.js run in node against the browser
preview's stand-ins): a sync that couldn't check everything says so calmly, also the app's own quiet one; a file
that is no longer on this PC says so and can be downloaded again; the weekly digest's buttons answer."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from studia import i18n

WEB = Path(i18n.__file__).parent / "web"

HARNESS = """
const web = %(web)s;
const stub = () => new Proxy(function () {}, { get: (t, k) => k === Symbol.toPrimitive ? () => "" : k === "length" ? 0 : stub(),
  apply: () => stub(), construct: () => stub() });
globalThis.window = globalThis;
const toasts = [];
const el = () => ({ innerHTML: "", style: {}, dataset: {}, classList: { toggle() {}, add() {}, remove() {} }, scrollTo() {},
  querySelector: () => null, querySelectorAll: () => [], addEventListener() {}, remove() {}, focus() {} });
const els = { "#top": el(), "#view": el(), "#toasts": { appendChild: (b) => toasts.push(b) } };
globalThis.document = new Proxy({}, { get: (t, k) => k === "compatMode" ? "CSS1Compat"
  : k === "createElement" ? () => el()
  : k === "querySelector" ? (s) => els[s] || stub()
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
for (const f of ["i18n.js", "icons.js", "app.js", "effort.js", "course.js", "plans.js", "digest.js", "chatactions.js"]) require(web + "/" + f);
const St = globalThis.Studia, S = St.S, A = St.A;
St.aiName = () => "Claude";
const tick = (ms = 30) => new Promise((r) => later(r, ms));
const ev = (e) => window.__lsEvent(e);
const plain = (h) => String(h).replace(/&#39;/g, "'").replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
const told = () => toasts.splice(0).map((b) => b.className.replace("toast ", "") + ": " + plain(b.innerHTML.replace(/<[^>]*>/g, "").trim()));
const view = () => plain(St.views[S.view.name](S.view));
const top = () => els["#top"].innerHTML.replace(/\\s+/g, " ");
const syncBtn = () => (top().match(/<button class="btn ghost sm ?(warn)?" data-act="sync"[^>]*>(.*?)<\\/button>/) || []).slice(1);
const out = {};
(async () => {
  S.mode = "app"; S.view = { name: "home" }; S.boot = {}; S.courses = [];
%(body)s
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""


def run(tmp_path, body):
    script = tmp_path / "page.js"
    script.write_text(HARNESS % {"web": json.dumps(WEB.as_posix()), "body": body}, encoding="utf-8")
    r = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


node = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")


@node
def test_the_quiet_sync_that_couldnt_check_panopto_says_so_without_popups(tmp_path):
    out = run(tmp_path, """
  ev({ type: "sync", phase: "start", message: "Syncing…", quiet: true });
  ev({ type: "sync", phase: "done", message: "Up to date", new_count: 0, errors: ["Panopto isn't answering right now."], quiet: true });
  out.btn = syncBtn(); out.toasts = told(); out.home = view();
  ev({ type: "sync", phase: "start", message: "Syncing…" });           // a sync that worked: back to normal
  ev({ type: "sync", phase: "done", message: "Up to date", new_count: 0, errors: [] });
  out.ok = syncBtn();
""")
    assert out["btn"][0] == "warn" and "Synced with problems" in out["btn"][1]
    assert out["toasts"] == []                                            # nothing pops up
    assert "Not everything could be checked." in out["home"] and "Panopto isn't answering right now." in out["home"]
    assert out["ok"][0] is None and "Synced" in out["ok"][1] and "problems" not in out["ok"][1]


@node
def test_a_sync_you_started_toasts_what_it_couldnt_check_and_keeps_it_in_the_top_bar(tmp_path):
    out = run(tmp_path, """
  ev({ type: "sync", phase: "start", message: "Syncing…" });
  ev({ type: "sync", phase: "done", message: "Up to date", new_count: 0, errors: ["Couldn't check new recordings for EEE 215."] });
  out.btn = syncBtn(); out.toasts = told();
""")
    assert out["btn"][0] == "warn" and "Synced with problems" in out["btn"][1]
    assert out["toasts"] == ["warning: Couldn't check new recordings for EEE 215."]


@node
def test_course_content_errors_are_shown(tmp_path):
    out = run(tmp_path, """
  const msg = "Some courses' files and announcements couldn't be checked. They're checked again at the next sync.";
  ev({ type: "sync", phase: "done", message: "Up to date", new_count: 0, errors: [] });
  ev({ type: "content", phase: "done", new_materials: 0, new_announcements: 0, errors: ["IEULearn timed out"] });
  out.mine = { toasts: told(), btn: syncBtn(), home: view().includes(msg + " IEULearn timed out") };
  ev({ type: "sync", phase: "done", message: "Up to date", new_count: 0, errors: [], quiet: true });
  ev({ type: "content", phase: "done", new_materials: 0, new_announcements: 0, errors: ["IEULearn timed out"], quiet: true });
  out.quiet = { toasts: told(), btn: syncBtn() };
  ev({ type: "content", phase: "done", new_materials: 0, new_announcements: 0, errors: [] });
  out.none = told();
""")
    assert out["mine"]["toasts"] == ["warning: Some courses' files and announcements couldn't be checked. They're checked "
                                     "again at the next sync. IEULearn timed out"]
    assert out["mine"]["btn"][0] == "warn" and "Synced with problems" in out["mine"]["btn"][1] and out["mine"]["home"]
    assert out["quiet"]["toasts"] == [] and out["quiet"]["btn"][0] == "warn"
    assert out["none"] == []


@node
def test_the_digest_s_download_to_do_says_it_is_downloading_and_goes_once_the_file_is_in(tmp_path):
    out = run(tmp_path, """
  let have = false;
  const week = { start: "2026-11-02", end: "2026-11-08", offset: 0, label: "2 Nov – 8 Nov 2026" };
  St.mocks.digest = (offset) => ({ ok: true, summary: {}, digest: { week, courses: [], todo: have ? [] : [
    { kind: "download", course: "EEE 215", course_id: "e", resource_id: "r1", text: "Download “Slides 1” (this week's material)" }] } });
  St.mocks.materials_download = (ids) => ({ ok: true, queued: ids.length });
  S.view = { name: "digest" }; St.onGo.digest(); await tick();
  out.before = view().includes('data-act="dg-get" data-id="r1"');
  A["dg-get"]({ dataset: { id: "r1" } }); await tick();
  out.toasts = told(); out.busy = view().includes('data-act="dg-get"') ? "button" : view().includes("Downloading") ? "downloading" : "";
  have = true;
  ev({ type: "material", id: "r1", state: "done", pages: 3 }); await tick();
  out.after = view().includes("Slides 1");
""")
    assert out["before"] and out["toasts"] == ["success: Downloading 1 file"]
    assert out["busy"] == "downloading" and out["after"] is False


@node
def test_fast_week_clicks_in_the_digest_keep_the_week_you_ended_on(tmp_path):
    out = run(tmp_path, """
  St.mocks.digest = (offset) => new Promise((r) => later(() => r({ ok: true, summary: {}, digest: { courses: [], todo: [],
    week: { start: "x" + offset, end: "", offset, label: "week " + offset } } }), offset === -1 ? 150 : 10));
  S.view = { name: "digest" };
  A["dg-week"]({ dataset: { d: "-1" } }); A["dg-week"]({ dataset: { d: "-1" } });
  await tick(300);
  out.offset = S.digest.offset; out.shown = S.digest.data.week.offset;
""")
    assert out == {"offset": -2, "shown": -2}


def test_a_file_moved_or_deleted_in_explorer_is_said_so(tmp_path):
    import types

    from studia.api import Api

    rows = {"r1": {"resource_id": "r1", "local_path": str(tmp_path / "gone.pdf")}, "r2": {"resource_id": "r2", "local_path": ""}}
    ns = types.SimpleNamespace(_s=types.SimpleNamespace(lib=types.SimpleNamespace(get_material=rows.get)))
    r = Api.material_open(ns, "r1", True)
    assert r == {"ok": False, "missing": True,
                 "error": "This file is no longer in its folder (moved or deleted). Download it again."}
    assert Api.material_open(ns, "r2") == {"ok": False, "missing": True, "error": "Download it first."}


@node
def test_show_in_folder_on_a_file_that_is_gone_says_so_and_offers_download_again(tmp_path):
    out = run(tmp_path, """
  const m = { id: "r1", title: "Slides 1", file_name: "Slides1.pdf", ext: "pdf", downloaded: true, outdated: false, pages: 3 };
  S.cd = { e: { id: "e", general: [], weeks: [{ no: 6, units: [{ id: "u", title: "Hafta 6", materials: [m] }], days: [] }] } };
  S.view = { name: "course", courseId: "e", tab: "materials" };
  let asked = [];
  St.mocks.material_open = (id, reveal) => (asked.push([id, !!reveal]), { ok: false, missing: true, error: "This file is no longer in its folder (moved or deleted). Download it again." });
  A["mat-reveal"]({ dataset: { id: "r1" } }); await tick();
  out.reveal = { asked, toasts: told(), downloaded: m.downloaded };
  m.downloaded = true; asked = [];
  A["mat-open"]({ dataset: { id: "r1" } }); await tick();
  out.open = { asked, toasts: told(), downloaded: m.downloaded };
""")
    gone = "error: This file is no longer in its folder (moved or deleted). Download it again."
    assert out["reveal"] == {"asked": [["r1", True]], "toasts": [gone], "downloaded": False}
    assert out["open"] == {"asked": [["r1", False]], "toasts": [gone], "downloaded": False}


@node
def test_files_attached_to_announcements_have_a_group_of_their_own(tmp_path):
    out = run(tmp_path, """
  const f = (id, title) => ({ id, title, file_name: title + ".pdf", ext: "pdf", downloaded: false, pages: 0 });
  const d = { id: "e", general: [{ id: "g", title: "Formula Sheet", materials: [f("g1", "Formula")] },
                                 { id: "announcements", title: "From announcements", materials: [f("ann-e-5961-0", "Quiz1 - Solutions")] }],
              weeks: [{ no: 6, units: [{ id: "u", title: "Hafta 6", materials: [f("r1", "Slides 1")] }], days: [] }] };
  out.groups = St.matGroups(d).map((g) => [g.label, g.mats.map((m) => m.id)]);
  const weeks = St.weeksHTML({ id: "e" }, { ...d, weeks: [] });
  out.cards = [...weeks.matchAll(/<div class="t">([^<]*)<\\/div>/g)].map((m) => m[1]);
  out.courseWide = weeks.split("From announcements")[0].includes("Quiz1 - Solutions");
  // its link in the announcement: downloaded with IEUStudia's IEULearn session and opened (the browser has none)
  const body = "Quiz1 - Solutions.pdf 142 KB (https://learn.ieu.edu.tr/student/announcements/5961/file/0)";
  out.link = St.linkText(body, "e").match(/data-act="[^"]*"( data-id="[^"]*")?/)[0];
  S.cd = { e: d };
  const asked = [], opened = [];
  St.mocks.materials_download = (ids, open) => (asked.push([ids, !!open]), { ok: true });
  A.url = (el) => opened.push(el.dataset.url);          // (the browser)
  A["ann-file"]({ dataset: { id: "ann-e-5961-0", url: "https://learn.ieu.edu.tr/student/announcements/5961/file/0" } });
  A["ann-file"]({ dataset: { id: "ann-e-9-0", url: "https://learn.ieu.edu.tr/student/announcements/9/file/0" } });
  await tick();
  out.asked = asked.length; out.opened = opened;
""")
    assert out["groups"] == [["Course-wide", ["g1"]], ["Week 6 · Hafta 6", ["r1"]],
                             ["From announcements", ["ann-e-5961-0"]]]
    assert out["cards"] == ["Course-wide", "From announcements"] and out["courseWide"] is False
    assert out["link"] == 'data-act="ann-file" data-id="ann-e-5961-0"'
    assert out["asked"] == 1                                    # the known one: downloaded and opened as a course file
    assert out["opened"] == ["https://learn.ieu.edu.tr/student/announcements/9/file/0"]     # not known yet: as before


@node
def test_an_ai_card_opens_the_lecture_at_its_moment_also_past_the_first_hour(tmp_path):
    out = run(tmp_path, """
  globalThis.setTimeout = later;
  S.courses = [{ id: "e", code: "EEE 215", days: [{ date: "2026-10-14", day_key: "e|14", mine: true }] }];
  const jumps = [];
  St.openDay = async () => {}; St.jumpTo = (part, s) => jumps.push([part, s]);
  for (const at of ["1:05:30", "12:40", "65:30"]) await St.runAction({ action: "open_lecture", date: "2026-10-14", at, part: 2 }, "e");
  await tick(400);
  out.jumps = jumps;
""")
    assert out["jumps"] == [[2, 3930], [2, 760], [2, 3930]]


@node
def test_no_speech_found_is_not_shown_as_the_microphone_being_off(tmp_path):
    out = run(tmp_path, """
  const rec = (id, error) => ({ id, status: "failed", error, no_sound: true, part: 1, time: "09:00", end_time: "09:45",
    duration_s: 2700, warnings: [{ level: "error", message: error }], viewer_url: "v" });
  const d = { date: "2026-10-14", day_key: "e|14", label: "Wed 14 Oct", recordings: [
    rec("a", "No speech was found in this recording (only noise or music). Check it on Panopto."),
    rec("b", "This recording has no sound on Panopto (the microphone was probably off). All available audio streams are silent: check it on Panopto or ask the lecturer.")] };
  const html = plain(St.dayCardHTML(d, { show_mode: "all", available_sections: [] }));
  out.a = html.slice(html.indexOf('data-rid="a"'), html.indexOf('data-rid="b"'));
  out.b = html.slice(html.indexOf('data-rid="b"'));
""")
    assert "No speech found" in out["a"] and "only noise or music: check it on Panopto" in out["a"]
    assert "microphone was off" not in out["a"]
    assert "No sound on Panopto" in out["b"] and "microphone was off" in out["b"]


@node
def test_recordings_ticked_on_one_course_page_are_not_selected_on_another(tmp_path):
    out = run(tmp_path, """
  const c = (id, code) => ({ id, code, display_name: code, days: [], counts: { new: 0, total: 0, done: 0 }, content: {} });
  S.courses = [c("a", "EEE 215"), c("b", "MATH 250")];
  St.sq = { card: () => "" };
  St.go({ name: "course", courseId: "a" });
  S.selected.add("a1"); S.selected.add("a2");
  St.go({ name: "course", courseId: "a", tab: "lectures" });       // another tab of the same course: kept
  out.tab = S.selected.size;
  St.go({ name: "course", courseId: "b" });
  out.other = S.selected.size;
""")
    assert out == {"tab": 2, "other": 0}


@node
def test_a_failed_review_sheet_study_pack_or_cheatsheet_is_named_as_itself(tmp_path):
    out = run(tmp_path, """
  for (const kind of ["prep", "pack", "sheet", "videos"]) St.onEvents.plan({ id: "p-" + kind, kind, status: "error", error: "Usage limit reached." });
  out.toasts = told();
""")
    assert out["toasts"] == ["error: Review sheet: Usage limit reached.", "error: Study pack: Usage limit reached.",
                             "error: Cheatsheet: Usage limit reached.", "error: Study plan: Usage limit reached."]


@node
def test_links_in_answers_and_notes_are_reached_and_opened_with_the_keyboard(tmp_path):
    out = run(tmp_path, """
  out.html = St.mdToHtml("See [Khan Academy](https://www.khanacademy.org/x) for more.");
  const link = (sel) => sel === "a[data-act][role=link]";
  const press = (key) => { let clicked = 0, stopped = 0;
    const target = { matches: link, click: () => clicked++ };
    listeners.keydown.forEach((f) => f({ key, target, preventDefault: () => stopped++, ctrlKey: false, altKey: false }));
    return [clicked, stopped]; };
  out.enter = press("Enter"); out.space = press(" ");
""")
    assert 'role="link" tabindex="0" data-act="url" data-url="https://www.khanacademy.org/x"' in out["html"]
    assert out["enter"] == [1, 1] and out["space"] == [0, 0]


@node
def test_a_file_of_a_unit_for_two_weeks_is_counted_and_downloaded_once_by_the_plan_and_pack_dialogs(tmp_path):
    """"Hafta 6 ve 7": its files are listed in week 6 and week 7. Picking both weeks offers each once, and unticking
    week 7 keeps the files week 6 still picks."""
    out = run(tmp_path, """
  require(web + "/studypack.js");
  els["#overlay"] = el();
  const asked = [];
  St.mocks.materials_download = (ids) => { asked.push(ids); return { ok: true, queued: ids.length }; };
  const shared = [{ id: "r1", name: "Slides1.pdf", pages: 0, ext: "pdf", ready: false, downloaded: false },
    { id: "r2", name: "Slides2.pdf", pages: 9, ext: "pdf", ready: true, downloaded: true }];
  St.mocks.plan_options = (cid) => ({ ok: true, course: { id: cid }, model: "sonnet", ai: { available: true }, assessments: [],
    weeks: [6, 7].map((no) => ({ no, start: "2026-11-0" + (no - 4), end: "", current: no === 7, past: no < 7, topic: "Circuits", exam: false,
      files: 2, files_ready: 1, missing: ["r1"], lectures: 0, lectures_done: 0 })) });
  St.mocks.pack_options = (cid) => ({ ok: true, course: { id: cid, code: "EEE 215" }, model: "sonnet", ai: { available: true },
    weeks: [6, 7].map((no) => ({ no, start: "2026-11-0" + (no - 4), current: no === 7, past: no < 7, topic: "Circuits", files: shared, days: [] })) });
  S.courses = [{ id: "e", code: "EEE 215" }];
  const overlay = () => plain(els["#overlay"].innerHTML);
  await A["plan-new"]({ dataset: { id: "e", kind: "prep" } }); await tick();
  A["pd-week"]({ dataset: { no: "6" }, checked: true }); await tick();
  out.plan = (overlay().match(/Download \\d+/) || [""])[0];
  await A["pd-download"](); await tick();
  await A["pack-new"]({ dataset: { id: "e" } }); await tick();          // week 7 (the current one) is picked
  A["pk-week"]({ dataset: { no: "6" } }); await tick();
  out.pack = (overlay().match(/Download \\d+/) || [""])[0];
  await A["pk-download"](); await tick();
  A["pk-week"]({ dataset: { no: "7" } }); await tick();                 // week 7 off: week 6 still picks Slides2
  out.picked = (overlay().match(/\\d+ files?(?= · )/) || [""])[0];
  out.asked = asked; out.toasts = told();
""")
    assert out["plan"] == "Download 1" and out["pack"] == "Download 1" and out["asked"] == [["r1"], ["r1"]]
    assert out["picked"] == "1 file"
