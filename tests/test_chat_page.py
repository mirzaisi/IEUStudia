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
    assert out["chat"] is True and sorted(out["files"]) == ["notes.txt", "pic.png"]   # (in the order they finished)


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
  const now = Date.now() / 1000;
  const reading = (parts) => St.chatReadingHTML({ seq: 2, status: "streaming", text: "", usage: { reading: { done: 1, total: 3, pages: 30, read: 12, eta: 130, at: now, parts } } }).body;
  const pdf = (name, pages) => ({ name, kind: "course", pages });
  out.progress = reading([{ n: 2, from: 11, to: 20, size: 10, files: [pdf("Week04_Student.pdf", [3, 4, 5, 6, 7, 9, 12, 13, 14, 15])], started: now - 65.4, noted: 4, words: 300, try: 1 },
    { n: 3, from: 21, to: 30, size: 10, files: [pdf("Week05_Student.pdf", [1]), pdf("Week06_Student.pdf", [2, 3]), pdf("Week07_Student.pdf", [4])], started: now - 3, noted: 0, words: 0, try: 2 }]);
  out.others = reading([{ n: 1, size: 9, files: [{ name: "video", kind: "video", pages: [1, 2, 3, 4] }, { name: "L2 · Tuesday, 30 September 2026", kind: "lecture", lecture: 2, date: "2026-09-30", pages: [1, 2, 3, 4, 5] }], started: now - 30, noted: 0, words: 850, try: 1 }]);
  out.waiting = reading([{ n: 2, from: 11, to: 20, size: 10, names: [], started: now - 120, noted: 0, try: 1, retry: { why: "dropped", wait: 10, until: now + 7.6, split: true } },
    { n: 3, from: 21, to: 30, size: 10, names: [], started: now - 20, noted: 0, try: 1, retry: { why: "service", status: 502, wait: 12, until: now + 12, split: false } }]);
  out.oldProgress = St.chatReadingHTML({ seq: 2, status: "streaming", text: "", usage: { reading: { done: 1, total: 3, pages: 30, read: 12 } } }).body;
  out.failed = St.chatReadingHTML({ seq: 2, status: "error", text: "", usage: { read: { pages: 289, read: 240, done: 4, total: 5, failed: true, tries: 4 } } }).foot;
  out.closed = St.chatReadingHTML({ seq: 2, status: "stopped", text: "", usage: { reading_kept: { pages: 289, read: 120, done: 4, total: 10 } } }).body;
  out.reopened = St.chatReadingHTML({ seq: 2, status: "streaming", text: "", usage: { reading_kept: { pages: 289, read: 120 } } }).body;
  out.foot = St.chatReadingHTML({ seq: 2, status: "done", text: "x", usage: { read: { pages: 30, read: 30, cached: 4, done: 3, total: 3 } } }).foot;
  out.again = St.chatReadingHTML({ seq: 4, status: "done", text: "x", usage: { read: { pages: 30, again: true } } });
  out.stopped = St.chatReadingHTML({ seq: 2, status: "stopped", text: "", usage: { read: { pages: 30, read: 12, stopped: true } } }).body;
  // the whole course with a lecture that doesn't fit whole: the panel says it is read in parts, the others go whole
  const realInputs = St.mocks.chat_inputs;
  St.mocks.chat_inputs = (sp) => { const r = realInputs(sp);
    r.days = r.days.map((d, i) => ({ ...d, as: i ? "full" : "notes", pieces: i ? 0 : 9 }));
    r.read = { ...r.read, on: true, pages: 24, lectures: 9, lecture_days: 1, batches: 1, tokens: 30000, output: 4000 }; return r; };
  const both = await St.call("chat_create", "17933", [DAY, "17933|y|lecture"], "course", false, "sonnet", "", null, "", null);
  St.go({ name: "chat", chatId: both.chat.id }); await tick(300);
  out.lectures = els["#cf-side"].innerHTML;
  St.mocks.chat_inputs = realInputs;
  out.big = St.effortBig.read({ batches: 3, tokens: 90000 });
  out.small = St.effortBig.read({ batches: 1, tokens: 9000 });
""")
    assert out["auto"]["on"] and out["auto"]["auto"] and out["auto"]["pages"] == 15
    assert 'data-act="cf-read"' in out["panel"] and "checked" in out["panel"].split('data-act="cf-read"')[1][:40]
    assert "goes through the 15 pages that don" in out["panel"] and "15 read first" in out["panel"]
    assert "Notes of the pages read first" in out["panel"]
    assert "Turn on Read everything" in out["panelOff"] and "15 read first" not in out["panelOff"]
    assert "full transcript" in out["panel"] and "highlights only" not in out["panel"]     # the whole course: whole
    lectures = out["lectures"]
    assert "read in parts first: its notes go with each question" in lectures and "full transcript" in lectures
    assert "1 lecture doesn&#39;t fit whole next to the rest: Claude reads it in parts before it answers" in lectures
    assert "The other 1 go whole." in lectures
    assert "Among them: 1 lecture that doesn&#39;t fit whole, in 9 pieces of its transcript." in lectures
    # while the pages are read: which pages each part has, since when (its clock ticks on the page), how many came,
    # about how long the rest takes, and that Stop keeps them; never a bar without a word
    progress = out["progress"]
    assert "Reading your slides and files: 12 of 30 pages" in progress and 'width:40%' in progress
    assert "Part 2 of 3 · Week04_Student.pdf p. 3–7, 9, 12–15 · " in progress and ">1:05</span> · 4 of 10 pages noted" in progress
    assert "Part 3 of 3 · Week05_Student.pdf p. 1, Week06_Student.pdf p. 2–3, … · " in progress
    # (a lecture named in the page's language: its own name is the AI's, in English)
    assert "Part 1 of 3 · 4 slides from the video, L2 · Wed 30 Sep (5 pieces of its transcript) · " in out["others"]
    assert "about 850 words written" in out["others"]
    assert "Claude is looking at the pages · try 2" in progress
    assert "about 2 minutes left" in progress and "Stop keeps every page read so far: asking again reads only the rest." in progress
    # waiting to ask again after a dropped connection, or after the service's own failure: said, with a countdown
    assert "Part 2 of 3 · The connection to Claude dropped. Asking again in <span data-until=" in out["waiting"]
    assert ">8</span> s, in two smaller parts." in out["waiting"]
    assert "Claude had a problem on its side (error 502). Asking again in " in out["waiting"]
    assert "Reading your slides and files: 12 of 30 pages" in out["oldProgress"]         # an older app's news
    assert out["failed"].count("240 of 289 pages were read and kept: asking again reads only the 49 still missing.") == 1
    assert "IEUStudia had tried that part 4 times." in out["failed"]
    # the app closed while reading: said at the next start like a stop (still reading: the next news shows it)
    assert "Stopped while reading the slides." in out["closed"]
    assert "120 of 289 pages were read and kept: asking again reads only the 169 still missing." in out["closed"]
    assert out["reopened"] == ""
    assert "Went through all 30 pages first" in out["foot"] and "4 read before" in out["foot"]
    assert out["again"] == {"body": "", "foot": ""}                   # a follow-up that reused the notes: nothing to say
    assert "Stopped while reading the slides." in out["stopped"]
    assert "12 of 30 pages were read and kept: asking again reads only the 18 still missing." in out["stopped"]
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
