"""The lecture player on the page (player.js in node, with a stand-in for app.js, a fake <video> and a small DOM): Play
beside Export opens the part where you stopped (else where Panopto had you; a moment asked for wins), the view has the
video, the slide of this second with its title and text, the speed 1x to 2x, the parts, "Open in Panopto at this
moment" and the help line (Panopto's statistics, the data an hour); the passage being said is marked and kept in view
unless you scrolled lately; a click on a passage or a slide jumps there; Space, the arrows, J and L act only when you're
not typing; a stream that breaks off is opened again once (sound only when the picture can't play), then said in plain
words with Try again; the end of a part goes on to the next; leaving closes the stream; a search hit plays in it while
it's open; in Turkish."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from studia import i18n

WEB = Path(i18n.__file__).parent / "web"

SCRIPT = r"""
globalThis.window = globalThis;
const winL = {}, docL = {};
window.addEventListener = (type, fn) => { (winL[type] = winL[type] || []).push(fn); };
const store = {};
globalThis.localStorage = { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } };
const cls = () => { const s = new Set(); return { add: (c) => s.add(c), remove: (c) => s.delete(c), contains: (c) => s.has(c), toggle: (c, on) => (on ? s.add(c) : s.delete(c)), has: s }; };
function fakeVideo() {
  const h = {};
  const v = { className: "", controls: false, preload: "", currentTime: 0, duration: NaN, paused: true, readyState: 0, videoWidth: 0,
    playbackRate: 1, error: null, parentNode: null, _src: null, plays: 0, webkitAudioDecodedByteCount: 1000, webkitVideoDecodedByteCount: 1000,
    setAttribute() {}, getAttribute(n) { return n === "src" ? this._src : null; }, removeAttribute(n) { if (n === "src") this._src = null; },
    set src(u) { this._src = u; }, get src() { return this._src; },
    load() {}, play() { this.paused = false; this.plays++; return Promise.resolve(); }, pause() { this.paused = true; fire("pause"); },
    addEventListener(type, fn) { (h[type] = h[type] || []).push(fn); }, remove() { this.parentNode = null; } };
  const fire = (type) => (h[type] || []).forEach((f) => f({ type }));
  v.fire = fire;
  return v;
}
const videos = [];
function para(i) { return { dataset: { i: String(i) }, classList: cls(), offsetTop: i * 400 }; }
const paras = {};
const box = { clientHeight: 300, scrollTop: 0, scrolled: [], closest(s) { return s === "#lp-tx" ? box : null; },
  querySelector(s) {
    if (s === ".lp-para.now") return Object.values(paras).find((p) => p.classList.contains("now")) || null;
    const m = /data-i="(\d+)"/.exec(s); if (!m) return null;
    return paras[m[1]] = paras[m[1]] || para(+m[1]);
  },
  scrollTo(o) { this.scrollTop = o.top; this.scrolled.push(o.top); } };
const stage = { insertBefore(node) { node.parentNode = stage; } };
const els = { "#view": { innerHTML: "", scrollTop: 120 }, "#lp-tx": box, "#lp-stage": stage, "#overlay": { innerHTML: "" },
  "#lp-wait": { innerHTML: "", classList: cls() }, "#lp-screen": { innerHTML: "" }, "#lp-speeds": { innerHTML: "" } };
globalThis.document = { documentElement: {}, createElement: (tag) => { const v = fakeVideo(); videos.push(v); return v; },
  querySelector: (s) => els[s] || null, addEventListener: (type, fn) => { (docL[type] = docL[type] || []).push(fn); },
  body: { contains: () => true } };
require(%(i18n)s);
const turkish = %(table)s;
const calls = [], toasts = [];
let answers = {};
const DAY = { day_key: "c|2026-11-04|lecture", title: "EEE 215 · Lecture", date_label: "Wednesday, 4 November 2026", course: { id: "c", code: "EEE 215" },
  parts: [{ index: 1, id: "d1", viewer_url: "https://panopto.example/Viewer.aspx?id=d1", paragraphs: [
      { t: 0, e: 40, ts: "00:00", text: "Good morning." }, { t: 43, e: 200, ts: "00:43", text: "Some examples on RLC circuits." },
      { t: 972, e: 990, ts: "16:12", text: "Let's look at this circuit." }, { t: 991, e: 1100, ts: "16:31", text: "So, now I have an inductor." }] },
    { index: 2, id: "d2", viewer_url: "https://panopto.example/Viewer.aspx?id=d2", paragraphs: [{ t: 0, e: 30, ts: "00:00", text: "Part two." }] }] };
const SCREENS = [{ n: 1, t0: 0, t1: 18.2, image: "file:///s/1.jpg", w: 1600, h: 900, title: "", text: "" },
  { n: 4, t0: 972.4, t1: 1179.5, image: "file:///s/4.jpg", w: 1600, h: 900, title: "Example 2", text: "Find R, L, tau" }];
const S = { view: { name: "day", dayKey: DAY.day_key }, day: DAY, search: { items: [] } };
const St = window.Studia = { S, A: {}, views: {}, after: {}, crumbs: {}, onEvents: {}, mocks: {},
  I: (n) => `[${n}]`, SPIN: () => "(spin)", esc: (s) => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;"),
  toast: (m, kind) => toasts.push([m, kind]),
  call: (name, ...args) => { calls.push([name, ...args]); const a = answers[name]; return Promise.resolve(typeof a === "function" ? a(...args) : a || { ok: true }); },
  renderView() { const v = S.view; if (St.views[v.name]) { els["#view"].innerHTML = St.views[v.name](v); if (St.after[v.name]) St.after[v.name](v); } },
  renderTop() {}, go(view) { S.view = view; St.renderView(); },
  openDay(dayKey, refresh, to) { calls.push(["openDay", dayKey, to, S.view.from ? S.view.from.name : null]); S.view = { name: "day", dayKey }; } };
require(%(page)s);
const A = St.A, P = () => St.player.state, tick = () => new Promise((r) => setTimeout(r, 5));
const key = (k, target) => { const e = { key: k, target: target || {}, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, stopPropagation() {} }; winL.keydown.forEach((f) => f(e)); return e.defaultPrevented; };
const open1 = (extra) => ({ ok: true, token: "tok1", url: `http://127.0.0.1:5000/m/${(extra && extra.token) || "tok1"}/lecture.mp4`, kind: "video", start: 600,
  duration: 2718, bytes: 285326849, delivery_id: "d1", viewer_url: "https://panopto.example/Viewer.aspx?id=d1", screens: SCREENS, note: "", local: false, ...extra });
const out = {};
(async () => {
  out.btn = St.player.btn(false);
  out.partBtn = St.player.partBtn({ index: 2 });
  out.hitRow = St.player.hitRow("<button class=\"sr-hit\">x</button>", 3, 1);
  // Play beside Export: Panopto's resume point (nothing kept on this PC yet)
  answers.player_open = open1();
  A["lp-play"]({ dataset: {} }); await tick(); await tick();
  const v = P().video;
  out.go = { view: S.view.name, from: S.view.from.name, fromScroll: S.view.from.scroll, src: v.src, plays: v.plays, rate: v.playbackRate, inStage: v.parentNode === stage };
  out.opened = calls.filter((c) => c[0] === "player_open");
  out.view = els["#view"].innerHTML;
  out.crumbs = St.crumbs.player();
  // it plays: the passage of this second marked and in view, the slide of this second beside the video
  v.readyState = 4; v.duration = 2718; v.videoWidth = 1024; v.fire("loadedmetadata");
  v.currentTime = 975; v.fire("timeupdate");
  out.now = Object.keys(paras).filter((i) => paras[i].classList.contains("now"));
  out.scrolledTo = box.scrolled.slice(-1)[0];
  out.screen = els["#lp-screen"].innerHTML;
  // you scroll the transcript: it stops following for a while
  docL.wheel[0]({ type: "wheel", target: box });
  box.scrolled = [];
  v.currentTime = 995; v.fire("timeupdate");
  out.followedAfterScroll = box.scrolled.length;
  // a click on a passage (or a slide): there, playing
  v.pause();
  A["lp-jump"]({ dataset: { t: "43" } });
  out.jump = { at: v.currentTime, paused: v.paused };
  // the keys; not while typing, not with a dialog open
  v.currentTime = 100;
  out.keys = [key(" "), v.paused];
  key("ArrowRight"); out.keys.push(v.currentTime); key("j"); out.keys.push(v.currentTime); key("L"); out.keys.push(v.currentTime); key("ArrowLeft"); out.keys.push(v.currentTime);
  const input = { closest: (s) => (s.includes("input") ? input : null) };
  out.typing = [key("l", input), key(" ", input), v.currentTime];
  els["#overlay"].innerHTML = "<div class=modal></div>"; out.dialog = key("l"); els["#overlay"].innerHTML = "";
  // the speed, kept for next time
  A["lp-speed"]({ dataset: { v: "1.5" } });
  out.speed = [v.playbackRate, store["studia-play-speed"], els["#lp-speeds"].innerHTML];
  // Open in Panopto at this moment: Panopto's viewer at this second; this one pauses
  v.play(); v.currentTime = 754.6;
  A["lp-panopto"]();
  out.panopto = [calls.filter((c) => c[0] === "open_url").pop(), v.paused];
  // the stream breaks off: opened again once from there; a picture it can't decode: the sound-only podcast
  answers.player_open = open1({ token: "tok2", url: "http://127.0.0.1:5000/m/tok2/lecture.m4a", kind: "audio", bytes: 13000000 });
  v.currentTime = 800; v.error = { code: 4 }; v.fire("error"); await tick(); await tick();
  out.retry = { ask: calls.filter((c) => c[0] === "player_open").pop(), src: v.src, closed: calls.filter((c) => c[0] === "player_close").map((c) => c[1]) };
  v.fire("loadedmetadata");
  out.audioView = els["#view"].innerHTML;
  answers.player_open = { ok: false, error: "Panopto can't be reached, so the lecture can't play. Check your internet connection, then press Try again." };
  v.error = { code: 2 }; v.fire("error"); await tick();
  out.secondError = els["#view"].innerHTML;
  // Try again; then the end of part 1: part 2 follows from its start
  answers.player_open = open1();
  A["lp-retry"](); await tick(); await tick();
  out.retried = { error: P().error, src: v.src };
  v.fire("loadedmetadata");
  answers.player_open = open1({ token: "tok3", url: "http://127.0.0.1:5000/m/tok3/lecture.mp4", delivery_id: "d2", start: 0, screens: [] });
  v.readyState = 4; v.currentTime = 2717; v.fire("ended"); await tick(); await tick();
  out.next = { part: P().part, ask: calls.filter((c) => c[0] === "player_open").pop(), kept1: store["studia-play:d1"] || null, toast: toasts.pop() };
  // a search hit while it's open: it plays there (part 1, 16:12)
  S.search.items = [{ kind: "lecture", day_key: DAY.day_key, moments: [{ part: 1, t: 972, delivery_id: "d1" }] }];
  answers.player_open = open1({ token: "tok4" });
  out.hit = St.player.hit(S.search.items[0], 0); await tick(); await tick();
  out.hitAt = { part: P().part, src: P().video.src };
  // Back: the transcript at the moment you were at; the stream ends, where you were is kept
  P().video.readyState = 4; P().video.duration = 2718; P().video.fire("loadedmetadata"); P().video.currentTime = 1000;
  A["lp-back"]();
  out.back = { opened: calls.filter((c) => c[0] === "openDay").pop(), closed: calls.filter((c) => c[0] === "player_close").pop(), kept: store["studia-play:d1"], state: !!P() };
  // the next time: on from where you stopped (yours before Panopto's); a moment asked for wins
  S.view = { name: "day", dayKey: DAY.day_key }; S.day = DAY;
  answers.player_open = open1({ token: "tok5" });
  St.player.open(DAY.day_key, 1); await tick(); await tick();
  out.resumed = P().video.src;
  St.player.close(); S.view = { name: "day", dayKey: DAY.day_key };
  answers.player_open = open1({ token: "tok6" });
  St.player.open(DAY.day_key, 1, 43); await tick(); await tick();
  out.asked = P().video.src;
  // Sound only (a phone hotspot): from this moment, and kept for next time; then the picture again
  P().video.readyState = 4; P().video.fire("loadedmetadata"); P().video.currentTime = 50;
  answers.player_open = open1({ token: "tok6b", kind: "audio", bytes: 13000000 });
  A["lp-sound"](); await tick(); await tick();
  out.sound = { ask: calls.filter((c) => c[0] === "player_open").pop(), kept: store["studia-play-sound"], src: P().video.src, on: /lp-sound on/.test(els["#view"].innerHTML) };
  P().video.fire("loadedmetadata");
  answers.player_open = open1({ token: "tok6c" });
  A["lp-sound"](); await tick(); await tick();
  out.soundOff = { ask: calls.filter((c) => c[0] === "player_open").pop(), kept: store["studia-play-sound"], on: /lp-sound on/.test(els["#view"].innerHTML) };
  // an error when it opens, in plain words, with Try again and Open in Panopto
  St.player.close(); S.view = { name: "day", dayKey: DAY.day_key };
  answers.player_open = { ok: false, error: "IEUStudia couldn't sign in to Panopto, so the lecture can't play here." };
  St.player.open(DAY.day_key, 2); await tick(); await tick();
  out.failedOpen = els["#view"].innerHTML;
  // leaving by another page: it closes by itself
  answers.player_open = open1({ token: "tok7" });
  A["lp-retry"](); await tick(); await tick();
  S.view = { name: "home" };
  await new Promise((r) => setTimeout(r, 1100));
  out.leftClosed = [!!P(), calls.filter((c) => c[0] === "player_close").pop()];
  // in Turkish
  t.load({ lang: "tr", table: turkish });
  S.view = { name: "day", dayKey: DAY.day_key }; S.day = DAY;
  answers.player_open = open1({ token: "tok8" });
  St.player.open(DAY.day_key, 1); await tick(); await tick();
  P().video.currentTime = 975; P().video.fire("timeupdate");
  out.tr = { view: els["#view"].innerHTML, btn: St.player.btn(false), screen: els["#lp-screen"].innerHTML };
  out.calls = calls.filter((c) => c[0] !== "openDay").map((c) => c[0]);
  process.stdout.write(JSON.stringify(out), () => process.exit(0));      // after the output is out (Linux pipes)
})();
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_the_lecture_player_page(tmp_path):
    script = tmp_path / "player.js"
    script.write_text(SCRIPT % {"i18n": json.dumps(str(WEB / "i18n.js")), "page": json.dumps(str(WEB / "player.js")),
                                "table": json.dumps(i18n.table("tr"), ensure_ascii=False)}, encoding="utf-8")
    run = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    # the buttons: Play beside Export, on a part's heading, beside a search moment
    assert 'data-act="lp-play"' in out["btn"] and "[play] Play</button>" in out["btn"] and "disabled" not in out["btn"]
    assert 'data-part="2"' in out["partBtn"] and "Play this part here" in out["partBtn"]
    assert out["hitRow"].startswith('<div class="lp-hitrow"><button class="sr-hit">x</button><button') and \
        'data-act="lp-hit" data-i="3" data-m="1"' in out["hitRow"] and 'title="Play from this moment"' in out["hitRow"]
    # it opens where Panopto had you (600 s), playing, in the player view (back to the transcript page where it was)
    assert out["opened"] == [["player_open", "c|2026-11-04|lecture", 1, ""]]
    assert out["go"] == {"view": "player", "from": "day", "fromScroll": 120, "src": "http://127.0.0.1:5000/m/tok1/lecture.mp4#t=600",
                         "plays": 1, "rate": 1, "inStage": True}
    view = out["view"]
    for bit in ('data-act="lp-speed" data-v="1.25"', "1.25×", "2×", 'data-act="lp-part" data-part="2"', "Open in Panopto at this moment",
                "Playing here doesn't count in Panopto's viewing statistics.",
                "It uses about 380 MB of internet data an hour (mind it on a phone hotspot).",
                "Keys: Space plays or pauses, ← and → go 5 s, J and L go 10 s.", "Some examples on RLC circuits.",
                "[presentation]<span>Slide 4 · 16:12–19:39 · Example 2</span>", 'class="lp no-math"'):
        assert bit in view, bit
    assert view.index("Slide 4 · 16:12–19:39") < view.index("Let's look at this circuit.")
    assert out["crumbs"].endswith("EEE 215 · Lecture <span class=\"dim\">· Wednesday, 4 November 2026 · Part 1</span>")
    # following: the passage of 16:15 marked and a third down the box; the slide of that second with its title and text
    assert out["now"] == ["2"] and out["scrolledTo"] == 800 - 300 / 3
    assert "Slide 4 · 16:12–19:39" in out["screen"] and "Example 2" in out["screen"] and "Find R, L, tau" in out["screen"]
    assert 'src="file:///s/4.jpg"' in out["screen"] and 'data-act="lp-screen-step" data-by="-1"' in out["screen"]
    assert out["followedAfterScroll"] == 0                     # you scrolled a moment ago: it stays where you are
    assert out["jump"] == {"at": 43, "paused": False}
    # Space pauses; → +5, J −10, L +10, ← −5; nothing while typing or with a dialog open
    assert out["keys"] == [True, True, 105, 95, 105, 100]
    assert out["typing"] == [False, False, 100] and out["dialog"] is False
    assert out["speed"][:2] == [1.5, "1.5"] and 'class="on" data-act="lp-speed" data-v="1.5"' in out["speed"][2]
    assert out["panopto"] == [["open_url", "https://panopto.example/Viewer.aspx?id=d1&start=754"], True]
    # broke off with a picture it can't decode: the sound-only podcast from 800 s, the old stream closed
    assert out["retry"]["ask"] == ["player_open", "c|2026-11-04|lecture", 1, "video"]
    assert out["retry"]["src"] == "http://127.0.0.1:5000/m/tok2/lecture.m4a#t=800" and "tok1" in out["retry"]["closed"]
    assert 'class="lp-stage audio' in out["audioView"] and 'id="lp-pic" class="lp-pic"' in out["audioView"]
    assert "It uses about 20 MB of internet data an hour" in out["audioView"]
    # broke off again, and Panopto can't be reached: said, with Try again and Open in Panopto
    err = out["secondError"]
    assert "This lecture can't play here right now" in err and "Check your internet connection, then press Try again." in err
    assert 'data-act="lp-retry"' in err and 'data-act="lp-panopto"' in err
    assert out["retried"] == {"error": "", "src": "http://127.0.0.1:5000/m/tok1/lecture.mp4#t=800"}
    # the end of part 1: part 2 from its start; part 1 starts afresh next time
    assert out["next"]["part"] == 2 and out["next"]["ask"] == ["player_open", "c|2026-11-04|lecture", 2, ""]
    assert out["next"]["kept1"] is None and out["next"]["toast"] == ["Part 2", "info"]
    # a search hit while it's open: there
    assert out["hit"] is True and out["hitAt"] == {"part": 1, "src": "http://127.0.0.1:5000/m/tok4/lecture.mp4#t=972"}
    # Back: the transcript at 1000 s of part 1 (its own way back kept), the stream closed, the moment kept
    assert out["back"]["opened"] == ["openDay", "c|2026-11-04|lecture", {"t": 1000, "id": "d1"}, None]
    assert out["back"]["closed"] == ["player_close", "tok4"] and json.loads(out["back"]["kept"])["t"] == 1000
    assert out["back"]["state"] is False
    assert out["resumed"] == "http://127.0.0.1:5000/m/tok5/lecture.mp4#t=1000"     # yours, not Panopto's 600
    assert out["asked"] == "http://127.0.0.1:5000/m/tok6/lecture.mp4#t=43"
    assert out["sound"] == {"ask": ["player_open", "c|2026-11-04|lecture", 1, "video"], "kept": "1",
                            "src": "http://127.0.0.1:5000/m/tok6b/lecture.mp4#t=50", "on": True}
    assert out["soundOff"] == {"ask": ["player_open", "c|2026-11-04|lecture", 1, ""], "kept": "0", "on": False}
    failed = out["failedOpen"]
    assert "IEUStudia couldn&#39;t sign in" in failed or "IEUStudia couldn't sign in" in failed
    assert 'data-act="lp-retry"' in failed and "Open in Panopto at this moment" not in failed
    assert out["leftClosed"] == [False, ["player_close", "tok7"]]
    tr = out["tr"]
    for bit in ("1,25×", "Hız", "Bölüm 2", "Panopto&#39;da bu andan aç", "Burada izlemen Panopto",
                "Saatte yaklaşık 380 MB internet verisi kullanır", "Slayt 4 · 16:12–19:39 · Example 2"):
        assert bit in tr["view"] or bit.replace("&#39;", "'") in tr["view"], bit
    assert "Oynat</button>" in tr["btn"] and "Slayt 4" in tr["screen"]
    assert "get_day" not in out["calls"]                       # the transcript page's own lecture: not asked again
