"""4.1.0, deep QA round 2's page findings: a chat's unsent question stays with that chat (PG-1); Save PDF on a study
pack ends in .pdf (PG-2); the app's menus work with the keyboard (PG-3); another quiz keeps the answers of one in
progress (PG-4); AI labels in the page's language (PG-5); the focus comes back to the button a dialog was opened
with, also when the page was drawn again (PG-6); Settings → Storage never offers to remove a speech model IEUStudia
didn't download (the shared Hugging Face folder)."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from studia import i18n
from test_chat_page import run as run_chat
from test_page_feedback import run as run_page

WEB = Path(i18n.__file__).parent / "web"
node = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
TR = json.dumps(i18n.table("tr"), ensure_ascii=False)


# ------------------------------------------------------------------ PG-3 and PG-6: menus and dialogs with the keyboard
# app.js in node with a small DOM: elements made from the markup they are given (their buttons, inputs and boxes, with
# their data-* attributes), the focus, and selectors of the kinds app.js asks for here
DOM = """
globalThis.window = globalThis;
window.innerWidth = 1024; window.innerHeight = 700;
const listeners = {};
const match = (s, e) => s.split(",").some((one) => {
  one = one.trim();
  if (one === "button:not(:disabled)") return e.tagName === "BUTTON" && !e.disabled;
  if (one === "input:not(:disabled)") return e.tagName === "INPUT" && !e.disabled;
  if (/^input|^textarea/.test(one)) return e.tagName === "TEXTAREA" || (e.tagName === "INPUT" && (e.attrs.type || "text") === "text");
  if (one === "[autofocus]") return "autofocus" in e.attrs;
  if (one === ".modal") return /\\bmodal\\b/.test(e.attrs.class || "");
  const parts = [...one.matchAll(/\\[data-([\\w-]+)(?:="([^"]*)")?\\]/g)];
  return parts.length > 0 && parts.map((m) => m[0]).join("") === one
    && parts.every(([, k, v]) => { const key = k.replace(/-(\\w)/g, (x, c) => c.toUpperCase()); return key in e.dataset && (v === undefined || e.dataset[key] === v); });
});
function el(tag, attrs = {}) {
  const dataset = {};
  for (const k in attrs) if (k.startsWith("data-")) dataset[k.slice(5).replace(/-(\\w)/g, (x, c) => c.toUpperCase())] = attrs[k];
  const e = { tagName: tag.toUpperCase(), attrs, dataset, kids: [], up: null, isConnected: true, offsetParent: {}, disabled: "disabled" in attrs,
    style: {}, className: attrs.class || "", id: attrs.id || "", offsetWidth: 200, offsetHeight: 120, _html: "",
    classList: { add() {}, toggle() {}, contains: () => false },
    get innerHTML() { return this._html; },
    set innerHTML(h) {
      this.kids.forEach((k) => { k.isConnected = false; });
      this._html = h;
      this.kids = [...h.matchAll(/<(button|input|textarea|div|form)\\b([^>]*)>/g)].map((m) => {
        const a = {}; for (const x of m[2].matchAll(/([\\w-]+)(?:="([^"]*)")?/g)) a[x[1]] = x[2] === undefined ? "" : x[2];
        return el(m[1], a);
      });
      this.kids.forEach((k) => { k.up = this; });
    },
    setAttribute(k, v) { this.attrs[k] = v; }, getBoundingClientRect: () => ({ left: 100, right: 200, top: 50, bottom: 80 }),
    focus() { document.activeElement = this; }, remove() { this.isConnected = false; this.kids.forEach((k) => { k.isConnected = false; }); body.kids = body.kids.filter((x) => x !== this); },
    matches(s) { return match(s, this); }, closest: () => null, select() {}, setSelectionRange() {},
    // (flat: what follows a dialog's box in its markup is in it)
    inside() { return this.className.includes("modal") && this.up ? this.up.kids.slice(this.up.kids.indexOf(this) + 1) : this.kids; },
    contains(x) { return x === this || this.inside().includes(x); },
    querySelector(s) { return this.inside().find((k) => k.isConnected && match(s, k)) || null; },
    querySelectorAll(s) { return this.inside().filter((k) => k.isConnected && match(s, k)); },
    scrollTo() {}, addEventListener() {} };
  return e;
}
const body = el("body"); body.appendChild = (e) => { body.kids.push(e); };
const view = el("div", { id: "view" }), overlay = el("div", { id: "overlay" });
const fixed = { "#view": view, "#overlay": overlay, "#toasts": el("div"), "#root": el("div") };
const find = (s) => {
  if (s in fixed) return fixed[s];
  if (s === "#menu") return body.kids.find((k) => k.id === "menu") || null;
  if (s === "#overlay .modal") return overlay.querySelector(".modal");
  for (const box of [view, overlay, page, ...body.kids]) { const f = box.querySelector && box.querySelector(s); if (f) return f; }
  return null;
};
const page = el("div");                                   // the rest of the window: the top bar, the sidebar
globalThis.document = { documentElement: { dataset: {}, style: { setProperty() {} } }, body, activeElement: body,
  createElement: (tag) => el(tag), querySelector: find, querySelectorAll: () => [], getElementById: () => null,
  addEventListener: (n, f) => { (listeners[n] = listeners[n] || []).push(f); } };
globalThis.localStorage = { getItem: () => null, setItem() {} };
globalThis.sessionStorage = { getItem: () => null, setItem() {}, removeItem() {} };
globalThis.navigator = { userAgent: "node" }; globalThis.CSS = { escape: (s) => s };
globalThis.addEventListener = () => {}; globalThis.matchMedia = () => ({ matches: false, addEventListener() {} });
globalThis.requestAnimationFrame = (f) => f();
globalThis.setInterval = () => 0; globalThis.setTimeout = () => 0;
require(%(i18n)s); require(%(icons)s); require(%(app)s);
const St = window.Studia, S = St.S, A = St.A;
const key = (k, shift) => { const e = { key: k, shiftKey: !!shift, target: document.activeElement, prevented: false,
  preventDefault() { this.prevented = true; }, stopPropagation() {}, stopImmediatePropagation() {}, getModifierState: () => false };
  for (const f of listeners.keydown) f(e); return e; };
const at = () => { const a = document.activeElement; return a === body ? "body" : (a.dataset.menu || a.dataset.act || a.tagName) + (a.isConnected ? "" : " (gone)"); };
const out = {};
(() => {
%(body)s
  process.stdout.write(JSON.stringify(out), () => process.exit(0));
})();
"""


def run_dom(tmp_path, body):
    script = tmp_path / "dom.js"
    script.write_text(DOM % {"i18n": json.dumps(str(WEB / "i18n.js")), "icons": json.dumps(str(WEB / "icons.js")),
                             "app": json.dumps(str(WEB / "app.js")), "body": body}, encoding="utf-8")
    r = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@node
def test_the_app_s_menus_work_with_the_keyboard(tmp_path):
    out = run_dom(tmp_path, """
  S.mode = "app";
  page.innerHTML = '<button data-act="menu-copy"><button data-act="menu-more">';
  const opener = page.kids[0];
  const items = [{ id: "notes", label: "Study notes", run() { out.ran = "notes"; } }, "-", { id: "exam", label: "Exam focus", run() {} },
    { id: "course", label: "Whole course", run() {} }];
  opener.focus(); St.menu(opener, items);
  out.open = [at(), document.querySelector("#menu").attrs.role, (document.querySelector("#menu").innerHTML.match(/role="menuitem"/g) || []).length];
  const seq = [];
  for (const [k, shift] of [["ArrowDown"], ["ArrowDown"], ["ArrowDown"], ["ArrowUp"], ["Home"], ["End"]]) { key(k, shift); seq.push(at()); }
  out.keys = seq;
  const esc = key("Escape");
  out.escape = [at(), !!document.querySelector("#menu"), esc.prevented];
  St.menu(opener, items); key("End");
  const tab = key("Tab");                                   // past the last item: closed, on from its button
  out.tabOut = [at(), !!document.querySelector("#menu"), tab.prevented];
  St.menu(opener, items); const inside = key("Tab");       // inside: the browser's own Tab (this DOM doesn't move it)
  out.tabIn = [at(), !!document.querySelector("#menu"), inside.prevented];
  const back = key("Tab", true);                            // before the first: back to its button
  out.shiftTab = [at(), !!document.querySelector("#menu"), back.prevented];
  St.menu(opener, items);
  page.innerHTML = '<button data-act="menu-copy"><button data-act="menu-more">';   // its button drawn again meanwhile
  key("Escape");
  out.redrawn = [at(), document.activeElement === page.kids[0]];
""")
    assert out["open"] == ["notes", "menu", 3]               # the focus in the menu, on its first item
    assert out["keys"] == ["exam", "course", "notes", "course", "notes", "course"]
    assert out["escape"] == ["menu-copy", False, True]
    assert out["tabOut"] == ["menu-copy", False, False]      # (not prevented: the browser goes on from the button)
    assert out["tabIn"] == ["notes", True, False]
    assert out["shiftTab"] == ["menu-copy", False, True]
    assert out["redrawn"] == ["menu-copy", True]


@node
def test_the_focus_comes_back_after_a_dialog_also_when_the_page_was_drawn_again(tmp_path):
    out = run_dom(tmp_path, """
  S.mode = "app";
  let n = 0;
  St.views.calendar = () => `<button data-act="cal-month"><button data-act="cal-new">${n++}`;
  S.view = { name: "calendar" }; St.renderView();
  view.querySelector('[data-act="cal-new"]').focus();
  St.renderView();                                          // drawn again by an event: the same button keeps the focus
  out.redrawn = [at(), document.activeElement === view.querySelector('[data-act="cal-new"]')];
  St.modal('<input id="ce-title" autofocus><button data-act="modal-close">');
  out.inDialog = document.activeElement.attrs.id;
  St.closeModal(); St.renderView();                         // an entry added: closed, then the calendar drawn again
  out.added = [at(), document.activeElement === view.querySelector('[data-act="cal-new"]')];
  St.modal('<button data-act="qz-start" autofocus>');
  St.renderView();                                          // drawn again while the dialog is open (a quiz's row), then closed
  St.closeModal();
  out.closed = [at(), document.activeElement === view.querySelector('[data-act="cal-new"]')];
  view.querySelector('[data-act="cal-new"]').focus();
  St.views.settings = () => '<button data-act="cal-new">';
  St.go({ name: "settings" });                              // another page: no focus carried over to it
  out.otherPage = at();
""")
    assert out["redrawn"] == ["cal-new", True]
    assert out["inDialog"] == "ce-title"
    assert out["added"] == ["cal-new", True]
    assert out["closed"] == ["cal-new", True]
    assert out["otherPage"] == "cal-new (gone)"


# ------------------------------------------------------------------ PC-7: the sidebar in a short window
@node
def test_the_sidebar_says_how_many_courses_are_under_the_study_group(tmp_path):
    out = run_dom(tmp_path, """
  S.mode = "app";
  const course = (id, code) => ({ id, code, display_name: "Course " + id, counts: {}, content: {} });
  S.courses = [course("a", "EEE 215"), course("b", "EEE 242"), course("c", "MATH 240"), course("d", "FENG 495"), course("e", "FENG 495")];
  const html = el("div", { id: "nav" }); fixed["#nav"] = html; fixed["#sfoot"] = el("div");
  St.renderSidebar();
  out.twins = [...html.innerHTML.matchAll(/class="nav-item[^"]*(twin)?[^"]*" data-act="go" data-view="course" data-id="(\\w)"/g)].map((m) => m[2] + (/twin/.test(m[0]) ? ":twin" : ""));
  out.button = /<div class="nav-study"><button class="nav-more" data-act="nav-more" hidden>/.test(html.innerHTML);
  // the list as laid out: 5 courses of 30 px from 100; the Study group sticks at 220, or 195 with the line at its top
  const more = { hidden: true, lastElementChild: { textContent: "" } };
  const items = [0, 1, 2, 3, 4].map((i) => ({ getBoundingClientRect: () => ({ bottom: 130 + 30 * i - scroll }) }));
  let scroll = 0;
  const study = { getBoundingClientRect: () => ({ top: more.hidden ? 220 : 195 }), querySelector: () => more };
  fixed["#nav"] = { querySelector: () => study, querySelectorAll: () => items };
  const scrolled = (y) => { scroll = y; listeners.scroll.forEach((f) => f({ target: { id: "nav" } })); return more.hidden ? "" : more.lastElementChild.textContent; };
  out.said = [scrolled(0), scrolled(40), scrolled(100)];
  t.load({ lang: "tr", table: %s });
  out.tr = scrolled(0);
""" % TR)
    assert out["twins"] == ["a", "b", "c", "d:twin", "e:twin"]
    assert out["button"] is True
    assert out["said"] == ["2 more courses", "1 more course", ""]
    assert out["tr"] == "2 ders daha"


def test_the_short_window_rules_come_after_the_rules_they_change():
    """The sidebar's rows for a short window (a laptop at 125-150 %) win over the usual ones only when they come later."""
    css = (WEB / "styles.css").read_text(encoding="utf-8")
    short = css.index("@media (max-height: 760px)")
    assert css.index(".nav-item { width: 100%") < short and css.index(".nav-section { font-size") < short
    block = css[short:css.index("}\n", css.index(".sub { display: none; }", short)) + 2]
    assert ".nav-item { padding-top: 5px; padding-bottom: 5px; }" in block
    assert ".nav-item[data-view=course]:not(.twin) .sub { display: none; }" in block
    assert "@media (max-height: 600px) { .nav-study { position: static; } }" in css


# ------------------------------------------------------------------ PG-4: another quiz keeps one in progress
def run_practice(tmp_path, body):
    """practice.js in node with test_practice_page.py's stand-in for app.js, running ``body``."""
    from test_practice_page import SCRIPT

    head = SCRIPT.split("(async () => {")[0] % {"i18n": json.dumps(str(WEB / "i18n.js")), "practice": json.dumps(str(WEB / "practice.js"))}
    script = tmp_path / "practice.js"
    script.write_text(head + "(async () => {\n" + body + "\n  process.stdout.write(JSON.stringify(out), () => process.exit(0));\n"
                      "})().catch((e) => { console.error(e); process.exit(1); });\n", encoding="utf-8")
    r = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@node
def test_opening_another_quiz_keeps_the_answers_of_the_one_in_progress(tmp_path):
    out = run_practice(tmp_path, """
  ANSWERS.quiz_get = (id) => ({ ok: true, quiz: { ...QUIZ, id }, attempts: [] });
  const two = { ...COURSE, quizzes: [{ ...COURSE.quizzes[0], tries: 0, best: null, last: null }, { ...COURSE.quizzes[0], id: "q3", title: "Random variables", tries: 0, best: null, last: null }] };
  ANSWERS.practice_course = () => two;
  S.view = { name: "course", courseId: "c1", tab: "practice" };
  await St.practice.load("c1"); await tick();
  const buttons = () => { const h = St.practice.tab(S.courses[0]); return [...h.matchAll(/data-act="qz-play" data-id="(\\w+)">\\[play\\] ([^<]+)</g)].map((m) => m[1] + ": " + m[2].trim()); };
  A["qz-play"]({ dataset: { id: "q1" } }); await tick();
  A["qz-start"](); A["qz-pick"]({ dataset: { i: "0" } }); A["qz-check"](); A["qz-next"]();
  St.closeModal();
  out.before = buttons();
  A["qz-play"]({ dataset: { id: "q3" } }); await tick();          // another quiz: only its intro, then closed
  out.other = shown.html.includes("qz-start");
  St.closeModal();
  out.after = buttons();
  A["qz-play"]({ dataset: { id: "q1" } }); await tick();
  out.back = { id: St.S.practice.run.id, pos: St.S.practice.run.pos, answers: Object.keys(St.S.practice.run.answers) };
  A["qz-play"]({ dataset: { id: "q3" } }); await tick(); A["qz-start"](); St.closeModal();
  out.both = buttons();
""")
    assert out["before"] == ["q1: Continue", "q3: Practise"]
    assert out["other"] is True
    assert out["after"] == ["q1: Continue", "q3: Practise"]
    assert out["back"] == {"id": "q1", "pos": 1, "answers": ["1"]}
    assert out["both"] == ["q1: Continue", "q3: Continue"]


# ------------------------------------------------------------------ PG-5: the AI's label in the page's language
@node
def test_the_ai_that_wrote_a_summary_is_named_in_the_page_s_language(tmp_path):
    out = run_page(tmp_path, """
  const labels = ["Claude Haiku, low effort", "Claude Sonnet", "Claude Opus, extra high effort", "ChatGPT, medium reasoning",
    "ChatGPT, max reasoning", "Offline model", "AI", ""];
  out.en = labels.map(St.modelLabel);
  S.digest.data = { todo: [], week: { offset: 0, label: "9 Nov – 15 Nov" }, courses: [] };
  S.digest.summary = { text: "Week summary", model: "Claude Haiku, low effort", at: "2026-11-11T19:51:09" };
  S.view = { name: "digest" };
  t.load({ lang: "tr", table: %s });
  out.tr = labels.map(St.modelLabel);
  out.digest = (view().match(/Yapay zeka yazdı \\([^)]*\\)/) || [""])[0];
""" % TR)
    assert out["en"] == ["Claude Haiku, low effort", "Claude Sonnet", "Claude Opus, extra high effort", "ChatGPT, medium reasoning",
                         "ChatGPT, max reasoning", "Offline model", "AI", ""]
    assert out["tr"] == ["Claude Haiku, akıl yürütme: düşük", "Claude Sonnet", "Claude Opus, akıl yürütme: ekstra yüksek",
                         "ChatGPT, akıl yürütme: orta", "ChatGPT, akıl yürütme: maksimum", "Çevrimdışı model", "AI", ""]
    assert out["digest"] == "Yapay zeka yazdı (Claude Haiku, akıl yürütme: düşük)"


# ------------------------------------------------------------------ PG-1: a chat's unsent question stays with it
@node
def test_a_half_typed_question_stays_with_its_chat(tmp_path):
    out = run_chat(tmp_path, """
  S.system = S.system || {}; S.system.ai = { available: true };
  const main = { innerHTML: "" }; els["#chat-main"] = main;
  const box = () => { const m = /<textarea id="chat-input"[^>]*>([\\s\\S]*?)<\\/textarea>/.exec(main.innerHTML); return m ? m[1] : null; };
  const type = (v) => listeners.input.forEach((f) => f({ target: { id: "chat-input", value: v, dataset: {}, style: {} } }));
  const start = (cid, code, day) => St.startDraft({ course_id: cid, course_code: code, days: [day], focus: "full", slides: false,
    model: "sonnet", labels: [{ key: day, n: 1, label: "Thu 6 Nov", kind: "Lecture" }] });
  start("EEE215", "EEE 215", "EEE215|2026-11-05|lecture"); await tick();
  await A["chat-suggest"]({ dataset: { text: "first EEE question" } }); await tick();
  const a = S.chat.id;
  start("MATH250", "MATH 250", "MATH250|2026-11-06|lecture"); await tick();
  await A["chat-suggest"]({ dataset: { text: "first MATH question" } }); await tick();
  const b = S.chat.id;
  A["chat-open"]({ dataset: { id: a } }); await tick();
  type("Half-typed EEE 215 question about phasors");
  A["chat-open"]({ dataset: { id: b } }); await tick();
  out.inB = box();
  S.day = { day_key: "MATH250|2026-11-06|lecture", course: { id: "MATH250", code: "MATH 250" }, kind: "lecture", date_label: "Thu 6 Nov", marks: [] };
  A["ask-day"](); await tick();
  out.newChat = box();
  draft(); await tick();                                 // a new chat of another course, its question typed
  type("about the new lecture");
  A["draft-change"](); await tick(); window.__cp.go(); await tick();          // its lectures changed: same chat, same question
  out.changed = box();
  St.go({ name: "chat", chatId: a }); await tick();
  out.backInA = box();
  // sent while the student went to another chat: the question doesn't come back in A's box
  const send = St.Mock.chat_send; let release;
  St.Mock.chat_send = (...x) => new Promise((r) => { release = () => r(send(...x)); });
  S.chat.data.running = false;                           // (the stand-in's first answer never ends here: no timers)
  const sending = A["chat-suggest"]({ dataset: { text: "Half-typed EEE 215 question about phasors" } }); await tick();
  A["chat-open"]({ dataset: { id: b } }); await tick();
  release(); await sending; await tick();
  A["chat-open"]({ dataset: { id: a } }); await tick();
  out.afterSend = box();
""")
    assert out["inB"] == "" and out["newChat"] == ""
    assert out["changed"] == "about the new lecture"
    assert out["backInA"] == "Half-typed EEE 215 question about phasors"
    assert out["afterSend"] == ""


# ------------------------------------------------------------------ PG-2: Save PDF on a study pack ends in .pdf
def test_save_pdf_on_a_study_pack_ends_in_pdf_when_a_name_is_typed(tmp_path, monkeypatch):
    from webview.util import parse_file_type

    from studia import studypack
    from studia.api import Api
    from studia.domain import Course
    from studia.services import Services

    a = Api(Services())
    a._push = lambda e: None
    a._s.lib.upsert_courses([Course(id="c", title="2026-1 EEE 215 C 1", code="EEE 215", name="Circuits", term="T")])
    pid = a._s.plans.create("c", {"kind": "cheatsheet"})
    pdf = tmp_path / "packs" / "2026-11-09 - DC Circuit Analysis cheatsheet.pdf"
    pdf.parent.mkdir()
    pdf.write_bytes(b"%PDF-1.7 sheet")
    monkeypatch.setattr(studypack, "make_pdf", lambda s, plan: pdf)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    seen, typed = [], {}

    class Window:                      # the save dialog: the student types a name over the one suggested
        def create_file_dialog(self, kind, directory="", save_filename="", file_types=()):
            for f in file_types:
                parse_file_type(f)
            seen.append((directory, save_filename, file_types))
            return (typed["path"],) if typed["path"] else None

    a._window = Window()
    (tmp_path / "out").mkdir()
    for name, saved in (("EEE 215 formulas", "EEE 215 formulas.pdf"), ("Ch. 3 notes", "Ch. 3 notes.pdf"),
                        ("sheet.PDF", "sheet.PDF")):
        typed["path"] = str(tmp_path / "out" / name)
        r = a.pack_pdf(pid, "save")
        assert r == {"ok": True, "saved": str(tmp_path / "out" / saved)}
        assert (tmp_path / "out" / saved).read_bytes() == b"%PDF-1.7 sheet"
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["Ch. 3 notes.pdf", "EEE 215 formulas.pdf", "sheet.PDF"]
    assert seen[-1] == (str(tmp_path / "Downloads"), pdf.name, ("PDF document (*.pdf)",))       # Downloads, as before
    typed["path"] = str(pdf)                                   # its own file, the name kept: nothing to copy
    assert a.pack_pdf(pid, "save") == {"ok": True, "saved": str(pdf)} and pdf.read_bytes() == b"%PDF-1.7 sheet"
    typed["path"] = ""                                         # cancelled
    assert a.pack_pdf(pid, "save") == {"ok": True, "saved": ""}
    a._s.plans.shutdown()


# ------------------------------------------------------------------ Settings → Storage: only IEUStudia's own files
def _model_files(d: Path, own: bool) -> None:
    d.mkdir(parents=True)
    for f in ("model.bin", "config.json", "tokenizer.json"):
        (d / f).write_bytes(b"x")
    if own:
        (d / ".complete").write_text("{}", encoding="utf-8")


def test_a_speech_model_in_the_shared_hugging_face_folder_is_not_ieustudias_to_remove(tmp_path, monkeypatch):
    from studia import components
    from studia.api import Api
    from studia.engine import models
    from studia.services import Services

    hub = tmp_path / "hf" / "hub"
    monkeypatch.setenv("HF_HUB_CACHE", str(hub))
    shared = hub / "models--Systran--faster-whisper-large-v3" / "snapshots" / "abc"
    _model_files(shared, own=False)                            # another program's download ("Maximum accuracy")
    _model_files(models._models_root() / "medium", own=True)   # IEUStudia's own
    speech = {m["id"]: m for m in components.catalog()["speech"]}
    assert (speech["large-v3"]["ready"], speech["large-v3"]["own"]) == (True, False)
    assert (speech["medium"]["ready"], speech["medium"]["own"]) == (True, True)
    assert (speech["small"]["ready"], speech["small"]["own"]) == (False, False)
    a = Api(Services())
    a._push = lambda e: None
    a._s.settings.model = "small"
    r = a.delete_component("speech:large-v3")                  # asked anyway: refused, its files stay
    assert r["ok"] is False and "shared Hugging Face" in r["error"]
    assert sorted(p.name for p in shared.iterdir()) == ["config.json", "model.bin", "tokenizer.json"]
    r = a.delete_component("speech:medium")
    assert r["ok"] and not (models._models_root() / "medium").exists()


SETUP = """
globalThis.window = globalThis;
globalThis.location = { hash: "" };
globalThis.document = { documentElement: {}, querySelector: () => null, querySelectorAll: () => [], addEventListener() {},
  getElementById: () => null };
require(%(i18n)s);
const S = { mode: "app", view: { name: "settings" }, boot: { app: { tray: false, ai_provider: "claude" } }, settings: { model: "small" } };
const storage = { ok: true, usage: { speech_models: 2.1e9 }, free_gb: 80, data_dir: "C:/x", codex: { bytes: 0, in_use: false },
  catalog: { cuda: {}, llm: [], speech: [{ id: "large-v3-turbo", label: "Fast & accurate", size_mb: 1620, ready: false, own: false },
    { id: "large-v3", label: "Maximum accuracy", size_mb: 3090, ready: true, own: false },
    { id: "medium", label: "Medium", size_mb: 1530, ready: true, own: true }, { id: "small", label: "Small", size_mb: 484, ready: true, own: true }] } };
window.Studia = { S, A: {}, I: (n) => `[${n}]`, SPIN: () => "(spin)", esc: (s) => String(s ?? "").replace(/"/g, "&quot;"), onEvents: {}, mocks: {},
  toast() {}, renderView() {}, render() {}, renderTop() {}, modal() {}, closeModal() {}, skeleton: () => "(skeleton)", sq: { about: () => "" },
  call: (name) => Promise.resolve(name === "storage" ? storage : { ok: true }) };
require(%(setup)s);
const St = window.Studia, tick = () => new Promise((r) => setTimeout(r, 5));
const out = {};
(async () => {
  St.settingsMore(); await tick();
  const h = St.settingsMore();
  out.storage = h.slice(h.indexOf('id="set-storage"'), h.indexOf('id="set-about"'));
  t.load({ lang: "tr", table: %(tr)s });
  const tr = St.settingsMore();
  out.storageTr = tr.slice(tr.indexOf('id="set-storage"'), tr.indexOf('id="set-about"'));
  process.stdout.write(JSON.stringify(out), () => process.exit(0));
})().catch((e) => { console.error(e); process.exit(1); });
"""


@node
def test_settings_storage_lists_a_shared_speech_model_without_remove(tmp_path):
    script = tmp_path / "setup.js"
    script.write_text(SETUP % {"i18n": json.dumps(str(WEB / "i18n.js")), "setup": json.dumps(str(WEB / "setup.js")),
                               "tr": json.dumps(i18n.table("tr"), ensure_ascii=False)}, encoding="utf-8")
    r = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    removable = re.findall(r'data-act="del-component" data-id="([^"]+)"', out["storage"])
    assert removable == ["speech:medium"]                      # not the shared one, not the one in use
    assert "Speech model “Maximum accuracy”" in out["storage"]
    assert "in another program's folder (Hugging Face): IEUStudia uses it from there and doesn't remove it" in out["storage"]
    assert "Hugging Face" in out["storageTr"] and "silmez" in out["storageTr"]
