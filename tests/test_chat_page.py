"""A chat on the page (app.js + chatfiles.js run in node against the browser preview's stand-ins): the files the
student adds to a new chat stay with it when its lectures are changed, and its first question waits until they are
added."""

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
const listeners = {}, els = {};                         // els: the page's elements a test reads (by selector)
globalThis.document = new Proxy({}, { get: (t, k) => k === "compatMode" ? "CSS1Compat"
  : k === "querySelector" ? (s) => els[s] || stub()
  : k === "addEventListener" ? (n, f) => { (listeners[n] = listeners[n] || []).push(f); } : stub() });
globalThis.localStorage = { getItem: () => null, setItem() {} };
globalThis.navigator = { userAgent: "node", platform: "Win32" };
globalThis.addEventListener = () => {}; globalThis.matchMedia = () => ({ matches: false, addEventListener() {} });
const later = setTimeout;
globalThis.setInterval = () => 0; globalThis.setTimeout = () => 0;      // no boot, no polling
globalThis.FileReader = class { readAsDataURL(b) { b.arrayBuffer().then((a) => { this.result = "data:x;base64," + Buffer.from(a).toString("base64"); this.onload(); }); } };
for (const f of ["i18n.js", "icons.js", "app.js", "effort.js", "notices.js", "chatfiles.js"]) require(web + "/" + f);
const St = globalThis.Studia, S = St.S, A = St.A;
St.aiName = () => "Claude";
const tick = (ms = 30) => new Promise((r) => later(r, ms));
const drop = (...names) => listeners.drop.forEach((f) => f({ preventDefault() {},
  dataTransfer: { types: ["Files"], files: names.map((n) => new File(["notes of " + n], n)) } }));
const DAY = "17933|x|lecture";
const draft = () => St.startDraft({ course_id: "17933", course_code: "MATH 240", days: [DAY], focus: "full", slides: false, model: "sonnet",
  labels: [{ key: DAY, n: 1, label: "Wed 14 Oct", kind: "Lecture" }] });
const names = () => ((St.mocks.chat_inputs(S.chat.draft ? { uploads: S.chat.draft.uploads } : { chat_id: S.chat.id }).uploads) || []).map((u) => u.name);
const out = {};
(async () => {
  S.view = { name: "chat" };
%(body)s
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""


def run(tmp_path, body):
    script = tmp_path / "chat.js"
    script.write_text(HARNESS % {"web": json.dumps(WEB.as_posix()), "body": body}, encoding="utf-8")
    r = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_changing_a_new_chat_s_lectures_keeps_its_files(tmp_path):
    out = run(tmp_path, """
  draft(); await tick();
  drop("notes.txt"); await tick(100);
  out.token = S.chat.draft.uploads; out.before = names();
  A["draft-change"](); await tick();
  window.__cp.go(); await tick();
  out.after = S.chat.draft.uploads; out.files = names();
""")
    assert out["token"] and out["after"] == out["token"]
    assert out["before"] == out["files"] == ["notes.txt"]


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_a_new_chat_s_first_question_waits_for_its_files(tmp_path):
    out = run(tmp_path, """
  draft(); await tick();
  const part = St.mocks.chat_upload_part; let release;
  St.mocks.chat_upload_part = (...a) => new Promise((r) => { release = () => r(part(...a)); });
  drop("big.pdf"); await tick();
  A["chat-suggest"]({ dataset: { text: "What is on the quiz?" } }); await tick();
  out.waiting = { draft: !!S.chat.draft, id: S.chat.id, uploading: St.chatUploading() };
  release(); await tick(100);
  out.added = { uploading: St.chatUploading(), files: names() };
  A["chat-suggest"]({ dataset: { text: "What is on the quiz?" } }); await tick(100);
  out.sent = { draft: !!S.chat.draft, id: !!S.chat.id };
""")
    assert out["waiting"] == {"draft": True, "id": None, "uploading": True}       # not sent while the file is added
    assert out["added"] == {"uploading": False, "files": ["big.pdf"]}
    assert out["sent"] == {"draft": False, "id": True}


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_the_preview_s_new_chat_keeps_its_files_after_the_first_question(tmp_path):
    out = run(tmp_path, """
  draft(); await tick();
  drop("notes.txt", "pic.png"); await tick(150);
  A["chat-suggest"]({ dataset: { text: "Check my notes" } }); await tick(100);
  out.chat = !!S.chat.id; out.files = names();
""")
    assert out == {"chat": True, "files": ["notes.txt", "pic.png"]}


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_a_chat_s_panel_is_read_again_when_the_chat_is_opened_again(tmp_path):
    out = run(tmp_path, """
  globalThis.setTimeout = later;                         // the panel reads what the chat sees a moment after it is drawn
  const r = await St.call("chat_create", "17933", [DAY], "full", false, "sonnet", "", null, "");
  const read = St.mocks.chat_inputs; let reads = 0;
  St.mocks.chat_inputs = (...a) => { reads++; return read(...a); };
  St.go({ name: "chat", chatId: r.chat.id }); await tick(300);
  out.opened = reads;
  St.renderView(); await tick(300);                      // drawn again: nothing changed, not read again
  out.redrawn = reads;
  St.go({ name: "settings" }); await tick(50);
  St.go({ name: "chat", chatId: r.chat.id }); await tick(300);
  out.reopened = reads;
""")
    assert out == {"opened": 1, "redrawn": 1, "reopened": 2}


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_a_new_chat_s_head_counts_everything_and_the_video_slides_that_fit(tmp_path):
    out = run(tmp_path, """
  globalThis.setTimeout = later;
  els["#chat-head"] = {};
  const read = St.mocks.chat_inputs;                     // 40 pictures of the chosen slide files: 20 of the video's go
  St.mocks.chat_inputs = (sp) => ({ ...read(sp), video: sp.slides ? { count: 20, tokens: 16000 } : { count: 0, tokens: 0 } });
  draft(); await tick(300);
  out.off = els["#chat-head"].innerHTML;
  S.chat.draft.slides = true; St.renderChatHead(); await tick(300);
  out.on = els["#chat-head"].innerHTML;
""")
    assert "Slides (3)" in out["off"]                    # off: the slides captured (the preview has 3)
    assert "Slides (20)" in out["on"] and "tokens of lectures" not in out["on"]
    assert "~" in out["on"] and " tokens</span>" in out["on"]


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_the_a_lot_to_read_warning_is_for_the_first_question_only(tmp_path):
    out = run(tmp_path, """
  globalThis.setTimeout = later;
  els["#cf-side"] = { querySelector: () => null };
  const r = await St.call("chat_create", "17933", [DAY, "17933|y|lecture"], "full", false, "sonnet", "", null, "");
  St.go({ name: "chat", chatId: r.chat.id }); await tick(300);
  out.before = els["#cf-side"].innerHTML;
  A["chat-suggest"]({ dataset: { text: "What is on the quiz?" } }); await tick(300);
  out.after = els["#cf-side"].innerHTML;
""")
    assert "That is a lot to read" in out["before"]                 # about 64k tokens with the first question
    assert "That is a lot to read" not in out["after"] and "cf-total" in out["after"]


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_read_everything_has_its_switch_its_cost_and_its_progress(tmp_path):
    """"Read everything" (chatread.py): on for the whole course, its switch and what it reads in the panel, the chat's
    progress while the pages are read, what an answer says it read, and a stop that keeps what was read."""
    out = run(tmp_path, """
  globalThis.setTimeout = later;
  els["#cf-side"] = { querySelector: () => null };
  const course = await St.call("chat_create", "17933", [DAY], "course", false, "sonnet", "", null, "", null);
  out.auto = course.chat.read;
  St.go({ name: "chat", chatId: course.chat.id }); await tick(300);
  out.panel = els["#cf-side"].innerHTML;
  const off = await St.call("chat_create", "17933", [DAY], "full", false, "sonnet", "", null, "", null);
  St.go({ name: "chat", chatId: off.chat.id }); await tick(300);
  out.panelOff = els["#cf-side"].innerHTML;
  out.progress = St.chatReadingHTML({ seq: 2, status: "streaming", text: "", usage: { reading: { done: 1, total: 3, pages: 30, read: 12 } } }).body;
  out.foot = St.chatReadingHTML({ seq: 2, status: "done", text: "x", usage: { read: { pages: 30, read: 30, cached: 4, done: 3, total: 3 } } }).foot;
  out.again = St.chatReadingHTML({ seq: 4, status: "done", text: "x", usage: { read: { pages: 30, again: true } } });
  out.stopped = St.chatReadingHTML({ seq: 2, status: "stopped", text: "", usage: { read: { pages: 30, read: 12, stopped: true } } }).body;
  out.big = St.effortBig.read({ batches: 3, tokens: 90000 });
  out.small = St.effortBig.read({ batches: 1, tokens: 9000 });
""")
    assert out["auto"]["on"] and out["auto"]["auto"] and out["auto"]["pages"] == 15
    assert 'data-act="cf-read"' in out["panel"] and "checked" in out["panel"].split('data-act="cf-read"')[1][:40]
    assert "goes through the 15 pages that don" in out["panel"] and "15 read first" in out["panel"]
    assert "Notes of the pages read first" in out["panel"]
    assert "Turn on Read everything" in out["panelOff"] and "15 read first" not in out["panelOff"]
    assert "Reading slides: part 2 of 3…" in out["progress"] and "12 of 30 pages read" in out["progress"]
    assert "Went through all 30 pages first" in out["foot"] and "4 read before" in out["foot"]
    assert out["again"] == {"body": "", "foot": ""}                   # a follow-up that reused the notes: nothing to say
    assert "Stopped while reading the slides." in out["stopped"] and "12 of 30 pages were read and kept" in out["stopped"]
    assert "about 90.0k tokens in 3 requests" in out["big"] and out["small"] == ""


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_a_large_read_that_is_on_by_itself_shows_its_cost_and_asks_once_before_the_first_question(tmp_path):
    """"Read everything" on by itself (the whole course, 4 files and more): the new-chat dialog says what it reads and
    costs, and the first question asks once (as turning it on does): Send without reading turns it off for that chat,
    closing the question sends nothing, and once said yes it isn't asked again."""
    out = run(tmp_path, """
  globalThis.setTimeout = later;
  const kept = {};
  globalThis.localStorage = { getItem: (k) => (k in kept ? kept[k] : null), setItem: (k, v) => { kept[k] = String(v); } };
  const asked = [];
  St.modal = (html) => { asked.push(html); };
  St.closeModal = () => { const h = St.onModalClose; St.onModalClose = null; if (h) h(); };
  const course = () => St.startDraft({ course_id: "17933", course_code: "MATH 240", days: [DAY], focus: "course", slides: false,
    model: "sonnet", labels: [{ key: DAY, n: 1, label: "Wed 14 Oct", kind: "Lecture" }] });
  // the new-chat dialog: the switch with what it reads and costs
  els["#cp-read-cost"] = { innerHTML: "" };
  course(); await tick(100);
  A["draft-change"](); await tick(100);
  window.__cp.focus("course"); await tick(400);
  out.dialog = els["#cp-read-cost"].innerHTML;
  St.closeModal();
  // the first question: asked; closing the question sends nothing
  A["chat-suggest"]({ dataset: { text: "What is on the quiz?" } }); await tick(100);
  out.first = asked.length; out.ask = asked[0] || "";
  St.closeModal(); await tick(100);
  out.closed = { draft: !!S.chat.draft, id: S.chat.id };
  // Send without reading: sent, with Read everything off for that chat
  A["chat-suggest"]({ dataset: { text: "What is on the quiz?" } }); await tick(100);
  window.__eff.no(); await tick(200);
  out.without = { draft: !!S.chat.draft, read_all: S.chat.data && S.chat.data.read_all, asked: asked.length };
  // Read everything and send: sent with it on, and not asked again
  course(); await tick(100);
  A["chat-suggest"]({ dataset: { text: "Explain Bayes" } }); await tick(100);
  window.__eff.yes(); await tick(200);
  out.yes = { read_all: S.chat.data && S.chat.data.read_all, on: S.chat.data && S.chat.data.read.on, asked: asked.length };
  course(); await tick(100);
  A["chat-suggest"]({ dataset: { text: "And the posterior?" } }); await tick(200);
  out.again = { sent: !S.chat.draft, asked: asked.length };
""")
    assert "15 pages read first, about 61.5k tokens in 2 requests" in out["dialog"]
    assert "Reading everything is a lot" in out["dialog"]                     # the effort group's warning
    assert out["first"] == 1 and "Read everything is on by itself" in out["ask"]
    assert "about 61.5k tokens in 2 requests" in out["ask"] and "Send without reading" in out["ask"]
    assert out["closed"] == {"draft": True, "id": None}                        # closed: nothing sent
    assert out["without"] == {"draft": False, "read_all": False, "asked": 2}
    assert out["yes"] == {"read_all": None, "on": True, "asked": 3}
    assert out["again"] == {"sent": True, "asked": 3}                          # once said yes: not asked again
