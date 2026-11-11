"""4.1.0, interface leftovers of 4.0: the top bar says "Graphics card" in plain words (and gives way in a narrow window,
so "Background checks off" stays readable); Settings' section links stay on one line; a button greyed out for a
reason other than the AI says why when clicked; the setup's and Settings' switches have names for screen readers; a
quiz just started brings its row into view instead of a toast that covered its Stop button; a card set's error is
said in the language of now; a lecture Panopto replaced with a new copy says "Replaced on Panopto" where its old
transcript is still shown; Settings > Storage removes the Codex CLI IEUStudia installed when no AI choice uses it.
(The one Export menu on the course page: test_exportmenu_page.py and test_export_page.py.)"""

import datetime as dt
import json
import re
import shutil
import subprocess
import types
from pathlib import Path

import pytest
from test_v4_ux import run

from studia import i18n, store
from studia.config import Settings
from studia.domain import Course, Recording, Segment, Transcript
from studia.library import Library

WEB = Path(i18n.__file__).parent / "web"
node = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
TR = json.dumps(i18n.table("tr"), ensure_ascii=False)


# ------------------------------------------------------------------ the page (app.js and the pages it needs, in node)
PAGE = """
  // the top bar: the graphics card in plain words, background checks off said in full
  S.system.gpu = { available: true, name: "NVIDIA GeForce RTX 4070 Laptop GPU", vram_mb: 8188, libraries: true, used: true, fits: true };
  S.settings = { device: "auto" }; S.boot = { app: { onboarded: true, tray: false } }; S.view = { name: "home" };
  St.renderTop(); out.top = els["#top"].innerHTML;
  // a greyed-out button that says why when clicked (and does nothing else)
  let ran = 0; A["x-test"] = () => { ran += 1; };
  out.whyOff = [St.whyOff("Because."), St.whyOff("")];
  const btn = { dataset: { act: "x-test", why: "Transcribe a lecture first." }, tagName: "BUTTON", disabled: false, closest: (s) => (s === "[data-act]" ? btn : null) };
  listeners.click.forEach((f) => f({ target: btn }));
  out.whyToast = toasts.map((b) => plain(b.innerHTML)); out.ran = ran;
  // a course with nothing transcribed: Ask says why; the Export button is on (its menu says why, and has Export for AI chat)
  const course = (id, done) => ({ id, code: "PHYS 101", display_name: "Physics", days: [], counts: { total: 3, done, done_any: done, days: 3, days_done: done }, content: {} });
  S.courses = [course("p", 0), course("m", 2)];
  const cd = { weeks: [], general: [], materials: { total: 0, downloaded: 0 }, syllabus: {}, announcements: [], outcomes_en: [], outcomes_tr: [], grading: [], alerts: { items: [], counts: {}, plan: [] } };
  S.cd.p = cd; S.cd.m = cd;
  S.view = { name: "course", courseId: "p" }; const none = St.views.course(S.view);
  out.askNone = (none.match(/<button[^>]*data-act="ask-course"[^>]*>/) || [""])[0];
  out.cxButtons = (none.match(/data-act="cx-open"/g) || []).length;
  S.view = { name: "course", courseId: "m" }; const some = St.views.course(S.view);
  out.askSome = (some.match(/<button[^>]*data-act="ask-course"[^>]*>/) || [""])[0];
  // the weekly digest: another week's summary being written
  S.digest = { data: { todo: [], week: { offset: 0, label: "9 Nov – 15 Nov" }, courses: [] }, running: true, busyOffset: -1, getting: new Set() };
  out.digest = (St.views.digest().match(/<button[^>]*data-act="dg-summary"[^>]*>/) || [""])[0];
  S.digest.busyOffset = 0;
  out.digestHere = (St.views.digest().match(/<button[^>]*data-act="dg-summary"[^>]*>/) || [""])[0];
  // a recording Panopto replaced: its row and its part's heading say so
  const day = { day_key: "m|2026-10-14|lecture", date: "2026-10-14", label: "Wednesday, 14 October 2026", kind: "lecture", parts: 1, duration_s: 2900, any_done: false,
    recordings: [{ id: "new", part: 1, time: "10:30", end_time: "11:18", duration_s: 2900, status: "available", warnings: [], anomalies: [], replaced: false },
                 { id: "old", part: null, time: "10:30", end_time: "10:51", duration_s: 1288, status: "done", duplicate: true, warnings: [], anomalies: [], replaced: true }] };
  out.dayCard = plain(St.dayCardHTML(day, { id: "m", show_mode: "all" }));
  out.tag = [St.replacedTag({ replaced: true }), St.replacedTag({ replaced: false })];
  // a card set's error, in the language of now (cards.py keeps the English words)
  const P = S.practice;
  P.course.m = { deck: {}, quizzes: [], stressed: null, card_sets: [{ id: "s1", status: "error", message: "Bir sorun çıktı",
    error: "None of the picked files is on this PC yet. Download them on the course page first." },
    { id: "s2", status: "error", error: "{ai}'s answer could not be read (it was not valid JSON). Try again." },
    { id: "s3", status: "error", error: "ChatGPT'nin kullanım sınırına ulaştın." }] };
  S.view = { name: "course", courseId: "m", tab: "practice" };
  out.cardsEn = plain(St.views.course(S.view));
  t.load({ lang: "tr", table: %(tr)s });
  out.cardsTr = plain(St.views.course(S.view));
  out.askTr = (St.views.course({ name: "course", courseId: "p" }).match(/data-why="[^"]*"/) || [""])[0];
  out.tagTr = plain(St.replacedTag({ replaced: true }));
  t.load({ lang: "en", table: {} });
  // a quiz started: no toast over its row's Stop button; its row is brought into view
  toasts.length = 0;
  St.mocks.quiz_options = () => ({ ok: true, model: "sonnet", ai: { available: true }, quant: false, language: "English", exams: [],
    weeks: [{ no: 7, current: true, topic: "Bayes", files: [{ id: "f", name: "Week07.pdf", pages: 12, ready: true, downloaded: true }], days: [] }] });
  St.mocks.quiz_estimate = () => ({ ok: true, estimate: null });
  St.mocks.quiz_create = () => ({ ok: true, plan: { id: "q1", course_id: "m", status: "queued", progress: 0, scope: { weeks: [7] } } });
  St.mocks.practice_course = () => ({ ok: true, ...P.course.m });
  await A["qz-new"]({ dataset: { cid: "m" } }); await tick();
  await A["qz-go"]({ disabled: false, innerHTML: "" }); await tick();
  out.quizToasts = toasts.map((b) => plain(b.innerHTML));
  out.quizRow = P.course.m.quizzes.map((q) => q.id);
"""


@node
def test_the_interface_leftovers_on_the_page(tmp_path):
    out = run(tmp_path, PAGE % {"tr": TR})
    top = out["top"]
    assert 'class="chip ok gpu-chip"' in top and "Graphics card</span>" in top and "GPU ·" not in top
    assert "Transcribes on the graphics card (NVIDIA GeForce RTX 4070 Laptop GPU): much faster than on the processor." in top
    assert '<span class="lbl">Background checks off</span>' in top
    assert out["whyOff"] == ['aria-disabled="true" data-why="Because."', ""]
    assert out["whyToast"] == ["Transcribe a lecture first."] and out["ran"] == 0
    assert 'data-why="Transcribe a lecture of this course first: then you can ask Claude about it."' in out["askNone"]
    assert "disabled" not in out["askNone"].replace("aria-disabled", "") and "data-why" not in out["askSome"]
    assert out["cxButtons"] == 0                     # one Export menu (exportmenu.js: not loaded here), no second button
    assert 'data-why="Another week&#39;s summary is being written. Try again when it&#39;s done."' in out["digest"]
    assert " disabled " in out["digestHere"] + " " and "data-why" not in out["digestHere"]   # this week's: shown being written
    assert "Transcribed Replaced on Panopto" in out["dayCard"] and out["dayCard"].count("Replaced on Panopto") == 1
    assert 'class="pill replaced"' in out["tag"][0] and "Panopto put a new copy" in out["tag"][0] and out["tag"][1] == ""
    assert "Cards from files: None of the picked files is on this PC yet. Download them on the course page first." in out["cardsEn"]
    assert "Cards from files: Claude's answer could not be read (it was not valid JSON). Try again." in out["cardsEn"]
    assert "Cards from files: ChatGPT'nin kullanım sınırına ulaştın." in out["cardsEn"]          # (an AI's own: as said)
    tr = i18n.table("tr")
    assert tr["None of the picked files is on this PC yet. Download them on the course page first."] in out["cardsTr"]
    assert tr["{ai}'s answer could not be read (it was not valid JSON). Try again."].replace("{ai}", "Claude") in out["cardsTr"]
    assert tr["Transcribe a lecture of this course first: then you can ask {ai} about it."].replace("{ai}", "Claude") in out["askTr"]
    assert out["tagTr"] == tr["Replaced on Panopto"]
    assert out["quizToasts"] == [] and out["quizRow"] == ["q1"]


def test_the_top_bar_and_settings_links_fit_a_small_window():
    css = (WEB / "styles.css").read_text(encoding="utf-8")
    assert ".topbar .bg-off .lbl { display: none" not in css                       # readable at 1024 px
    assert re.search(r"@media \(max-width: 1220px\) \{ \.topbar \.gpu-chip \{ display: none; \} \}", css)
    nav = re.search(r"\.set-nav \{[^}]*\}", css).group(0)
    assert "flex-wrap: nowrap" in nav and "overflow-x: auto" in nav                # one line, or a sideways scroll
    assert ".set-nav .btn { flex: none;" in css
    practice = (WEB / "practice.js").read_text(encoding="utf-8")
    assert "Writing your quiz. It takes a few minutes" not in practice
    assert 'P.stressing ? St.whyOff(t("{ai} is still ranking another course\'s moments.' in practice
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert "S.ai.dayKey !== d.day_key ? whyOff(t(\"{ai} is still writing for another lecture." in app


# ------------------------------------------------------------------ setup.js: switch names, the Codex CLI's Remove
SETUP = """
globalThis.window = globalThis;
globalThis.location = { hash: "" };
globalThis.document = { documentElement: {}, querySelector: () => null, querySelectorAll: () => [], addEventListener() {},
  getElementById: () => null };
require(%(i18n)s);
const calls = [], toasts = [], asked = [];
const S = { mode: "app", view: { name: "settings" }, boot: { app: { tray: false, ai_provider: "chatgpt" } }, settings: { model: "small" } };
const storage = (bytes, inUse) => ({ ok: true, usage: { ai_tools: bytes }, free_gb: 80, data_dir: "C:/x", codex: { bytes, in_use: inUse },
  catalog: { speech: [{ id: "small", label: "Small", size_mb: 484, ready: true }], cuda: {}, llm: [] } });
const answers = { storage: () => storage(4.1e8, false), delete_component: () => storage(0, false), ai_status: () => ({ ok: true }) };
window.Studia = { S, A: {}, I: (n) => `[${n}]`, SPIN: () => "(spin)", esc: (s) => String(s ?? "").replace(/"/g, "&quot;"), onEvents: {}, mocks: {},
  toast: (m, kind) => toasts.push([m, kind]), renderView() {}, render() {}, renderTop() {}, modal() {}, closeModal() {},
  skeleton: () => "(skeleton)", sq: { about: () => "" }, ask: async (o) => { asked.push(o); return true; },
  call: (name, ...args) => { calls.push([name, ...args]); return Promise.resolve(answers[name] ? answers[name](...args) : { ok: true }); } };
require(%(setup)s);
const St = window.Studia, A = St.A, U = St.setup, tick = () => new Promise((r) => setTimeout(r, 5));
const out = {};
(async () => {
  U.st = { facts: { disk: {} }, catalog: { speech: [] }, verdict: { blockers: [] } };
  U.ch = { tray: true, startup: true, notify_recordings: true }; U.step = 4;
  out.background = St.setupHTML();
  St.settingsMore(); await tick();
  out.settings = St.settingsMore();
  const del = { dataset: { act: "del-component", id: "codex", name: "Codex CLI (for ChatGPT)", about: (out.settings.match(/data-id="codex"[^>]*data-about="([^"]*)"/) || [])[1] } };
  await A["del-component"](del); await tick();
  out.asked = asked.map((o) => [o.title, o.text, o.ok, !!o.danger]);
  out.calls = calls.map((c) => c.join(" "));
  out.after = St.settingsMore();
  S.boot.app.ai_provider = "codex";
  answers.storage = () => storage(4.1e8, true); U.storage = storage(4.1e8, true);
  out.inUse = St.settingsMore();
  process.stdout.write(JSON.stringify(out), () => process.exit(0));
})().catch((e) => { console.error(e); process.exit(1); });
"""


@node
def test_switches_have_names_and_the_codex_cli_can_be_removed(tmp_path):
    script = tmp_path / "setup.js"
    script.write_text(SETUP % {"i18n": json.dumps(str(WEB / "i18n.js")), "setup": json.dumps(str(WEB / "setup.js"))}, encoding="utf-8")
    r = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    switches = re.findall(r'<input type="checkbox" class="switch"[^>]*>', out["background"])
    assert len(switches) == 6 and all("aria-label=" in s for s in switches)
    assert 'data-key="notify_recordings"' in out["background"] and 'aria-label="New lecture recordings"' in out["background"]
    named = re.findall(r'<input type="checkbox" class="switch" data-act="app-bool"[^>]*>', out["settings"])
    assert len(named) == 7 and all("aria-label=" in s for s in named)
    assert 'data-key="check_updates"' in out["settings"] and 'aria-label="Look for new versions by itself"' in out["settings"]
    row = re.search(r'<div class="set-row"><div class="txt"><div class="t">Codex CLI \(for ChatGPT\)</div><div class="d">([^<]*)</div></div>(.*?)</div>', out["settings"])
    assert row and row.group(1) == "391 MB · not in use" and 'data-act="del-component" data-id="codex"' in row.group(2)
    assert out["asked"] == [["Remove it from this PC?", "Codex CLI (for ChatGPT): IEUStudia installed it for ChatGPT through the "
                             "Codex CLI, which you don't use now. If you choose that again, IEUStudia installs it again.", "Remove", True]]
    assert "delete_component codex" in out["calls"] and out["calls"][-1].startswith("ai_status")
    assert 'data-id="codex"' not in out["after"] and 'data-id="codex"' not in out["inUse"]          # gone; in use: kept
    assert 'data-id="speech:' not in out["settings"]                                              # (the one in use)


# ------------------------------------------------------------------ the Codex CLI IEUStudia installed: removed
def _codex_here():
    from studia import ai_setup

    exe = ai_setup.codex_home() / "bin" / "codex.exe"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(b"\0" * 5000)
    (ai_setup.tools_dir() / "codex-setup").mkdir(parents=True, exist_ok=True)
    (ai_setup.tools_dir() / "codex-setup" / "codex-1.0.0-x.tgz").write_bytes(b"\0" * 1000)
    (ai_setup.tools_dir() / "claude-config").mkdir(parents=True, exist_ok=True)       # (Claude Code's: kept)
    return exe


def test_the_codex_cli_is_removed_only_when_no_ai_choice_uses_it(isolated_home, monkeypatch, tmp_path):
    from studia import ai_setup, appconfig, components, hardware
    from studia.api import Api

    monkeypatch.setattr(hardware, "check", lambda: {"disk": {"data_free_gb": 80.0, "data_dir": str(isolated_home)}})
    monkeypatch.setattr(components, "catalog", lambda facts=None: {"speech": [], "cuda": {}, "llm": []})
    api = Api()
    api._services = types.SimpleNamespace(settings=Settings(output_dir=str(tmp_path / "out")))
    api._ready.set()
    assert api.storage()["codex"] == {"bytes": 0, "in_use": False}
    exe = _codex_here()
    assert ai_setup.codex_size() == 6000 and ai_setup.codex_exe() == str(exe)
    appconfig.update({"ai_provider": "codex"})                     # ChatGPT through the Codex CLI: it stays
    assert api.storage()["codex"] == {"bytes": 6000, "in_use": True}
    r = api.delete_component("codex")
    assert not r["ok"] and "Pick another AI in Settings → AI first." in r["error"] and exe.is_file()
    appconfig.update({"ai_provider": "chatgpt"})
    with ai_setup._lock:
        ai_setup._jobs["codex"] = {"state": "running"}             # being installed: not now
    try:
        r = api.delete_component("codex")
        assert not r["ok"] and "being installed right now" in r["error"] and exe.is_file()
    finally:
        with ai_setup._lock:
            ai_setup._jobs.pop("codex", None)
    r = api.delete_component("codex")
    assert r["ok"] and r["codex"] == {"bytes": 0, "in_use": False}
    assert not ai_setup.codex_home().exists() and not (ai_setup.tools_dir() / "codex-setup").exists()
    assert ai_setup.codex_exe() == "" and (ai_setup.tools_dir() / "claude-config").is_dir()


def test_a_codex_cli_still_running_is_left_whole(isolated_home, monkeypatch):
    from studia import ai_setup

    exe = _codex_here()
    real = Path.rename

    def busy(self, target):
        if self == ai_setup.codex_home():
            raise PermissionError(32, "The process cannot access the file because it is being used by another process")
        return real(self, target)

    monkeypatch.setattr(Path, "rename", busy)
    with pytest.raises(ai_setup.Plain, match="still running"):
        ai_setup.remove_codex()
    assert exe.is_file()


# ------------------------------------------------------------------ a lecture Panopto replaced with a new copy
def _rec(did, minute, dur):
    return Recording(delivery_id=did, course_id="c", folder_id="f", folder_path="", name=did,
                     start=dt.datetime(2026, 10, 14, 10, 30) + dt.timedelta(minutes=minute), duration_s=dur, viewer_url="u")


def _transcribe(lib, did):
    t = Transcript(delivery_id=did, segments=[Segment(0, 5, "Today: random variables.", "en", 0.9)], duration_s=5,
                   speech_s=5, language_seconds={"en": 5}, model="m", device="cpu", engine_version="x",
                   created_at="2026-10-20T10:00:00")
    lib.save_transcript(t, store.save_transcript(t))
    lib.set_status(did, "done")


def test_the_old_copy_of_a_replaced_lecture_says_so(tmp_path):
    from studia.views import all_course_views, course_detail, day_detail, replaced_ids

    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="c", title="t", code="MATH 240", name="Probability")])
    lib.merge_recordings("c", [_rec("old", 0, 1288)], first_sync=True)
    _transcribe(lib, "old")
    lib.merge_recordings("c", [_rec("new", 0.5, 2900)], first_sync=False)        # the old copy left Panopto
    assert replaced_ids(lib) == {"old"} and replaced_ids(lib, "c") == {"old"} and replaced_ids(lib, "x") == set()
    key = "c|2026-10-14|lecture"

    def rows(views):
        (c,) = [v for v in views if v["id"] == "c"]
        return {r["id"]: r["replaced"] for d in c["days"] for r in d["recordings"]}

    assert rows(all_course_views(lib, Settings(), [])) == {"old": True, "new": False}
    assert course_detail(lib, Settings(), "c")["counts"]["done"] == 0      # (the course page: the new copy counts)
    assert [(p["id"], p["replaced"]) for p in day_detail(lib, Settings(), key, {})["parts"]] == [("old", True)]
    _transcribe(lib, "new")                                      # once the new copy is transcribed, the day shows it
    assert [(p["id"], p["replaced"]) for p in day_detail(lib, Settings(), key, {})["parts"]] == [("new", False)]
    assert rows(all_course_views(lib, Settings(), [])) == {"old": True, "new": False}     # (still listed: said why)
    assert replaced_ids(types.SimpleNamespace()) == set()        # a library without these: no label
