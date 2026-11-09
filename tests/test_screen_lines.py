"""The screen timeline where students and the AI meet it: the "[Screen n, …]" lines in every AI prompt
(outputs/ai_copy.py), the captions under the pictures (the transcript page, the Markdown file), the Screens filter,
and the search's "screen" results."""

import datetime as dt
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from studia import i18n, store
from studia.domain import Course, Recording, Segment, Transcript
from studia.slides import Slide

COURSE = Course(id="c1", title="t", code="EEE 215", name="Circuits", instructor="Dr. X")
WEB = Path(i18n.__file__).parent / "web"
node = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")


def _rec(did="a"):
    return Recording(delivery_id=did, course_id="c1", folder_id="f", folder_path="", name="L",
                     start=dt.datetime(2026, 10, 13, 11, 0), duration_s=900, viewer_url=f"https://v/{did}")


def _tr(did="a"):
    segs = [Segment(s, s + 8, f"Passage at {s} seconds about the circuit.", "en", 0.9) for s in (5, 100, 200, 330, 460)]
    return Transcript(delivery_id=did, segments=segs, duration_s=900, speech_s=600, language_seconds={"en": 600},
                      model="m", device="cpu", engine_version="t", created_at="2026-10-13T12:00:00")


def _timeline(tmp_path, did="a"):
    pics = []
    for i in (1, 2, 3):
        p = tmp_path / f"s-{i:02d}.jpg"
        p.write_bytes(b"jpg")
        pics.append(str(p))
    store.save_slides(did, [Slide(1, 0.0, 90.0, "slides/x/s-01.jpg", pics[0]),
                            Slide(2, 90.0, 300.0, "slides/x/s-02.jpg", pics[1]),
                            Slide(1, 300.0, 420.0, "slides/x/s-01.jpg", pics[0]),        # shown again
                            Slide(3, 420.0, 900.0, "slides/x/s-03.jpg", pics[2])])
    store.save_screens(did, {"v": 1, "made": "2026-11-12T10:20:00", "times": "exact", "powerpoint": True, "screens": {
        "1": {"title": "Δ - Y transformation", "text": "Apply superposition\n" + "e1 = Rc(Ra+Rb) I1 " * 40,
              "from": "powerpoint", "slide": 15, "lang": "en"},
        "2": {"title": "", "text": "", "from": "ocr", "ocr": "windows en-US"},
        "3": {"title": "", "text": "", "from": "none"}}})


def test_every_ai_prompt_gets_the_screen_lines(tmp_path):
    from studia.outputs.ai_copy import compact_day

    _timeline(tmp_path)
    text = compact_day(COURSE, [_rec()], {"a": _tr()})              # (no slide pictures asked for: the lines still)
    lines = text.splitlines()
    first = next(i for i, x in enumerate(lines) if x.startswith("[Screen 1,"))
    assert lines[first].startswith("[Screen 1, 00:00-01:30] Δ - Y transformation · Apply superposition; e1 = ")
    assert lines[first].endswith(" …") and len(lines[first]) < 380                            # capped
    assert lines[first + 1].startswith("[00:05] Passage at 5")
    assert "[Screen 2, 01:30-05:00] (writing not read)" in lines                             # OCR read nothing
    assert "[Screen 1, 05:00-07:00] Δ - Y transformation (shown again)" in lines              # its title again
    assert not any(x.startswith("[Screen 3") for x in lines)                                  # no words read: no line
    assert lines.index("[Screen 2, 01:30-05:00] (writing not read)") == lines.index("[01:40] Passage at 100 seconds "
                                                                                       "about the circuit.") - 1
    tight = compact_day(COURSE, [_rec()], {"a": _tr()}, screens="titles")
    assert "[Screen 1, 00:00-01:30] Δ - Y transformation" in tight.splitlines()
    assert "superposition" not in tight
    assert "[Screen" not in compact_day(COURSE, [_rec()], {"a": _tr()}, screens="none")
    assert "[Screen" not in compact_day(COURSE, [_rec()], {"a": _tr()}, only_starred=True)


def test_titles_only_never_says_a_read_screen_was_not_read():
    """A board read by OCR has its words but no title: with titles only (the offline AI, a whole-course chat near its
    limit) its words are left out, and the AI must not be told the writing couldn't be read."""
    from studia.outputs.ai_copy import screen_line

    board = {"from": "ocr", "title": "", "text": "KVL: -10 + 2 i1 + 4 (i1 - i2) = 0"}
    assert screen_line(7, 754, 900, board) == "[Screen 7, 12:34-15:00] KVL: -10 + 2 i1 + 4 (i1 - i2) = 0"
    assert screen_line(7, 754, 900, board, titles_only=True) == ""
    blank = {"from": "ocr", "title": "", "text": ""}
    assert screen_line(7, 754, 900, blank, titles_only=True) == "[Screen 7, 12:34-15:00] (writing not read)"
    titled = {"from": "powerpoint", "title": "Mesh analysis", "text": "Write KVL around each mesh"}
    assert screen_line(7, 754, 900, titled, titles_only=True) == "[Screen 7, 12:34-15:00] Mesh analysis"


def test_the_screen_lines_leave_the_emphasised_lines_to_the_plans():
    from studia.plans import _STARRED

    assert not _STARRED.match("[Screen 1, 00:00-01:30] ⭐ quiz")
    assert _STARRED.match("[01:40] (slide 2) ⭐ quiz next week")


def test_a_lecture_without_a_timeline_reads_as_before():
    from studia.outputs.ai_copy import compact_day

    text = compact_day(COURSE, [_rec()], {"a": _tr()})
    assert "[Screen" not in text and "[00:05] Passage at 5" in text


def test_the_prompts_say_what_the_screen_lines_are(tmp_path, monkeypatch):
    from studia import ai, chat
    from studia.outputs.ai_copy import SCREEN_LINES

    assert SCREEN_LINES in chat.system_prompt(1, "full", True) and SCREEN_LINES in chat.system_prompt(2, "full", False)


def test_the_markdown_file_captions_each_picture(tmp_path):
    from studia.outputs.markdown import render_day

    _timeline(tmp_path)
    md = render_day(COURSE, [_rec()], {"a": _tr()}, {"a": store.load_slides("a")})
    assert "![Slide 1](slides/x/s-01.jpg)\n\n*Slide 1 · 00:00–01:30 · Δ - Y transformation*" in md
    assert "*Slide 2 · 01:30–05:00*" in md and "*Slide 1 · 05:00–07:00 · Δ - Y transformation*" in md


def test_the_transcript_page_gets_each_pictures_caption(tmp_path):
    from studia import views
    from studia.config import Settings
    from studia.library import Library

    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([COURSE])
    lib.merge_recordings("c1", [_rec()], first_sync=True)
    store.save_transcript(_tr())
    lib.set_status("a", "done")
    _timeline(tmp_path)
    d = views.day_detail(lib, Settings(), _rec().day_key, {})
    firsts = [(p["slide"], p["slide_range"], p["slide_title"], p["slide_t0"]) for p in d["parts"][0]["paragraphs"]]
    assert firsts[0] == (1, "00:00–01:30", "Δ - Y transformation", 0.0)
    assert firsts[1] == (2, "01:30–05:00", "", 90.0)
    assert firsts[3] == (1, "05:00–07:00", "Δ - Y transformation", 300.0)


def test_the_search_finds_the_words_on_a_screen(tmp_path):
    from studia import search
    from studia.library import Library

    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([COURSE])
    lib.merge_recordings("c1", [_rec()], first_sync=True)
    store.save_transcript(_tr())
    lib.set_status("a", "done")
    _timeline(tmp_path)
    lib.index.screens("a")
    res = search.results(lib, "superposition", typing=False)
    group = next(g for g in res["groups"] if g["kind"] == "screen")
    item = group["items"][0]
    assert item["day_key"] == _rec().day_key and item["moments"][0]["slide"] == 1
    assert item["moments"][0]["ts"] == "00:00" and item["moments"][0]["title"] == "Δ - Y transformation"
    lib.index.drop_lecture("a")
    assert not any(g["kind"] == "screen" for g in search.results(lib, "superposition", typing=False)["groups"])


# ----------------------------------------------------------------------------- the page
HARNESS = """
const web = %(web)s;
const stub = () => new Proxy(function () {}, { get: (t, k) => k === Symbol.toPrimitive ? () => "" : k === "length" ? 0 : stub(),
  apply: () => stub(), construct: () => stub() });
globalThis.window = globalThis;
const el = () => ({ innerHTML: "", style: {}, dataset: {}, classList: { toggle() {}, add() {}, remove() {}, contains: () => false }, scrollTo() {},
  querySelector: () => null, querySelectorAll: () => [], addEventListener() {}, remove() {}, focus() {}, contains: () => false, scrollTop: 0 });
const toasts = [];
const els = { "#top": el(), "#view": el(), "#overlay": el(), "#toasts": { appendChild: (b) => toasts.push(b) } };
globalThis.document = new Proxy({}, { get: (t, k) => k === "compatMode" ? "CSS1Compat" : k === "activeElement" ? null : k === "hidden" ? false
  : k === "createElement" ? () => el() : k === "querySelector" ? (s) => (s in els ? els[s] : stub())
  : k === "getElementById" ? (id) => els["#" + id] || null : k === "querySelectorAll" ? () => []
  : k === "addEventListener" ? () => {} : stub() });
globalThis.localStorage = { getItem: () => null, setItem() {} };
globalThis.navigator = { userAgent: "node", platform: "Win32" };
globalThis.CSS = { escape: (s) => s };
globalThis.addEventListener = () => {}; globalThis.matchMedia = () => ({ matches: false, addEventListener() {} });
globalThis.requestAnimationFrame = (f) => f();
const later = setTimeout;
globalThis.setInterval = () => 0; globalThis.setTimeout = () => 0;
for (const f of ["i18n.js", "match.js", "icons.js", "app.js"]) require(web + "/" + f);
const St = globalThis.Studia, S = St.S, A = St.A;
const tick = (ms = 30) => new Promise((r) => later(r, ms));
const plain = (h) => String(h).replace(/<\\/?(mark|span|b|small)[^>]*>/g, "").replace(/<[^>]*>/g, " ").replace(/&#39;/g, "'").replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&").replace(/\\s+/g, " ");
const out = {};
(async () => {
  S.mode = "app"; S.view = { name: "home" }; S.boot = {}; S.courses = []; S.system.ai = { available: true, provider: "claude" };
%(body)s
  process.stdout.write(JSON.stringify(out), () => process.exit(0));
})().catch((e) => { console.error(e); process.exit(1); });
"""


def _page(tmp_path, body):
    script = tmp_path / "page.js"
    script.write_text(HARNESS % {"web": json.dumps(WEB.as_posix()), "body": body}, encoding="utf-8")
    r = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


DAY = """
  const P = (t, slide, extra) => ({ t, e: t + 8, ts: "00:0" + Math.round(t / 60), text: "Passage at " + t, lang: "en", tag: "", star: false, tags: [], uncertain: false,
    slide, slide_image: slide ? "file:///x/s-0" + slide + ".jpg" : null, slide_w: 1600, slide_h: 900, slide_t0: null, slide_range: "", slide_title: "", ...extra });
  const day = { day_key: "c1|2026-10-13|lecture", title: "EEE 215 · Lecture", date_label: "13 October", subtitle: "", course: { id: "c1", code: "EEE 215", name: "Circuits" },
    kind: "lecture", languages: { en: 1 }, highlights: [], marks: [], files: {}, notes: [], screens_again: { current: null, waiting: [] },
    parts: [{ index: 1, id: "a", title: "Part 1", time_range: "11:00–11:15", viewer_url: "https://v/a", status: "done", slides: 2, warnings: [], anomalies: [], paragraphs: [
      P(5, 1, { slide_t0: 0, slide_range: "00:00–01:30", slide_title: "Δ - Y transformation" }), P(40, 1),
      P(100, 2, { slide_t0: 90, slide_range: "01:30–05:00" }), P(200, 2)] }] };
  S.courses = [{ id: "c1", code: "EEE 215", name: "Circuits", days: [{ day_key: day.day_key, any_done: true }, { day_key: "c1|2026-10-20|lecture", any_done: true }] }];
  S.view = { name: "day", dayKey: day.day_key, from: { name: "home" } }; S.day = JSON.parse(JSON.stringify(day)); S.dayFind = "";
"""


@node
def test_the_transcript_page_captions_the_screens_and_filters_them(tmp_path):
    out = _page(tmp_path, DAY + """
  St.renderView(); await tick();
  const view = () => plain(document.querySelector("#view").innerHTML);
  const html = () => document.querySelector("#view").innerHTML;
  out.caption = view().includes("Slide 1 · 00:00–01:30 · Δ - Y transformation");
  out.panopto = (html().match(/data-act="ts" data-url="https:\\/\\/v\\/a" data-t="0"/g) || []).length;
  out.play_without_player = html().includes("scr-play");
  St.player = { open: (d, p, s) => { out.played = [d, p, s]; } };
  St.renderView(); out.play_with_player = html().includes("scr-play");
  A["scr-play"]({ dataset: { part: "1", t: "90" } });
  A["scr-only"]();
  out.filtered = { paras: (html().match(/class="para /g) || []).length, said: (html().match(/class="scr-said"/g) || []).length,
    text: view().includes("Passage at 100 Passage at 200") };
  A["scr-only"]();
  out.back = (html().match(/class="para /g) || []).length;
  out.again_button = html().includes('data-act="scr-again"');
  A["scr-again"]();
  out.dialog = plain(document.querySelector("#overlay").innerHTML);
""")
    assert out["caption"] and out["panopto"] == 1
    assert out["play_without_player"] is False and out["play_with_player"] is True
    assert out["played"] == ["c1|2026-10-13|lecture", 1, 90]
    assert out["filtered"] == {"paras": 0, "said": 2, "text": True} and out["back"] == 4
    assert out["again_button"]
    assert "25 to 95 MB a lecture" in out["dialog"] and "All 2 lectures of EEE 215" in out["dialog"]
    assert "This lecture" in out["dialog"]


@node
def test_a_screen_result_in_the_search_says_its_slide_and_time(tmp_path):
    out = _page(tmp_path, """
  const it = { kind: "screen", key: "c1|2026-10-13|lecture", course_id: "c1", course_code: "EEE 215", every: true, score: 1, date: "2026-10-13",
    day_key: "c1|2026-10-13|lecture", time: "11:00", kind_label: "Lecture", parts: 1, more: 0,
    moments: [{ delivery_id: "a", t: 600, ts: "10:00", part: 1, slide: 12, title: "Δ - Y", snippet: { text: "Δ - Y · Apply superposition", marks: [[10, 23]] } }] };
  S.search = { q: "superposition", res: { query: "superposition", total: 1, counts: { screen: 1 }, courses: [], groups: [{ kind: "screen", count: 1, items: [it] }] }, items: [] };
  els["#search-results"] = el();
  S.view = { name: "library" }; St.renderView(); await tick();
  out.html = plain(els["#search-results"].innerHTML);
  St.mocks.get_day = () => ({ ok: false, error: "x" });
  let opened = null;
  const realOpen = St.openDay;
  A["sr-open"] && (S.search.items = S.search.items || []);
  out.items = S.search.items.length;
""")
    assert "Screens" in out["html"] and "Slide 12 · 10:00" in out["html"] and "Apply superposition" in out["html"]
    assert out["items"] == 1
