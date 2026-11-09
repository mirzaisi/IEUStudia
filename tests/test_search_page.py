"""The search on the page (app.js, course.js, plans.js and match.js in node, against stand-ins for the window): the
results search.py gives are shown grouped with their counts and filters, the words found marked (a word with its
number as one), partial matches labelled, a typo offered its correction; finding in a transcript shows the passages
and highlights with the words only, marked, and how many; the files filter, the chat list and the plan list filter
with the same matching."""

import datetime as dt
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from studia import i18n, search
from studia.coursesite import Announcement, Resource, Unit
from studia.domain import Course, Recording, Segment, Transcript
from studia.library import Library

WEB = Path(i18n.__file__).parent / "web"
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")

HARNESS = """
const web = %(web)s, data = %(data)s;
const stub = () => new Proxy(function () {}, { get: (t, k) => k === Symbol.toPrimitive ? () => "" : k === "length" ? 0 : stub(),
  apply: () => stub(), construct: () => stub() });
globalThis.window = globalThis;
const el = () => ({ innerHTML: "", style: {}, dataset: {}, classList: { toggle() {}, add() {}, remove() {}, contains: () => false },
  scrollTo() {}, scrollIntoView() {}, querySelector: () => null, querySelectorAll: () => [], addEventListener() {}, remove() {},
  focus() {}, getBoundingClientRect: () => ({ top: 0, bottom: 0, left: 0 }) });
const ids = ["#top", "#view", "#nav", "#sfoot", "#search-results", "#search-browse", "#search-busy", "#find-count", "#hl-slot",
  "#mat-list", "#chat-items", "#pl-lists"];
const els = Object.fromEntries(ids.map((k) => [k, el()]));
globalThis.document = new Proxy({}, { get: (t, k) => k === "compatMode" ? "CSS1Compat"
  : k === "createElement" ? () => el()
  : k === "querySelector" ? (s) => els[s] || stub()
  : k === "querySelectorAll" ? () => []
  : k === "getElementById" ? (s) => els["#" + s] || stub()
  : k === "addEventListener" ? () => {} : stub() });
globalThis.localStorage = { getItem: () => null, setItem() {} };
globalThis.navigator = { userAgent: "node", platform: "Win32" };
globalThis.CSS = { escape: (s) => s };
globalThis.addEventListener = () => {}; globalThis.matchMedia = () => ({ matches: false, addEventListener() {} });
globalThis.requestAnimationFrame = (f) => f();
globalThis.setInterval = () => 0; globalThis.setTimeout = () => 0;
for (const f of ["i18n.js", "match.js", "icons.js", "app.js", "course.js", "plans.js"]) require(web + "/" + f);
Match.load(data.rules);
const St = globalThis.Studia, S = St.S;
S.mode = "app"; S.boot = {}; St.setCourses(data.courses);
const out = {};
%(body)s
console.log(JSON.stringify(out));
process.stdout.write("", () => process.exit(0));
"""


def run(tmp_path, data, body):
    script = tmp_path / "page.js"
    script.write_text(HARNESS % {"web": json.dumps(WEB.as_posix()), "data": json.dumps(data, ensure_ascii=False),
                                 "body": body}, encoding="utf-8")
    r = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@pytest.fixture
def lib(tmp_path):
    from studia import store

    store._cache.clear()
    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="p", title="t", code="MATH 240", name="Probability")])
    lib.merge_recordings("p", [Recording(delivery_id="p1", course_id="p", folder_id="f", folder_path="", name="n",
                                         start=dt.datetime(2026, 10, 7, 9, 30), duration_s=3000, viewer_url="v")],
                         first_sync=True)
    lib.save_transcript(Transcript(delivery_id="p1", segments=[
        Segment(0, 4, "Good morning.", "en", 0.9), Segment(60, 64, "Next week we have the first quiz.", "en", 0.9),
        Segment(200, 205, "One more thing about the quiz: bring a pencil.", "en", 0.9)], duration_s=3000,
        speech_s=60, language_seconds={"en": 60}, model="m", device="cpu", engine_version="t", created_at="x"), "p1.json")
    lib.merge_units("p", [Unit("u3", 3, "Hafta 3", dt.date(2026, 10, 5), dt.date(2026, 10, 11), "",
                               [Resource("w3", "Week03_Student", "Week03_Student.pdf", "pdf", "pdf", "u", "d")])],
                    first_sync=True)
    lib.set_material_text("w3", ["Quiz 1 covers weeks 1 to 3", "Bayes' theorem"])
    lib.merge_announcements("p", [Announcement("a1", "About Quiz 1", "Dr X", dt.datetime(2026, 10, 9, 9),
                                               "The first quiz is next week.")], first_sync=True)
    return lib


def _data(lib, *queries):
    return {"rules": search.rules(), "courses": [{"id": "p", "code": "MATH 240", "display_name": "Probability",
                                                 "counts": {}, "days": []}],
            "res": {q: search.results(lib, q, typing=False) for q in queries}}


def test_the_results_are_grouped_counted_and_marked(lib, tmp_path):
    out = run(tmp_path, _data(lib, "quiz one", "probabilty"), """
  S.view = { name: "library" };
  S.search = { q: "quiz one", res: data.res["quiz one"], course: "", kind: "", busy: false, items: [] };
  St.renderView(); out.page = els["#search-results"].innerHTML;
  S.search = { q: "probabilty", res: data.res["probabilty"], course: "", kind: "", busy: false, items: [] };
  St.renderView(); out.typo = els["#search-results"].innerHTML;
""")
    page = out["page"]
    assert 'data-kind="announcement"' in page and 'data-kind="lecture"' in page and 'data-kind="file"' in page
    assert '<span>Announcements</span><span class="n">1</span>' in page
    assert "<mark>Quiz 1</mark>" in page                                   # the word and its number as one mark
    assert "<mark>first quiz</mark>" in page
    assert page.index("About <mark>Quiz 1</mark>") < page.index("<mark>first quiz</mark>")   # the title first
    assert "some of the words" not in page.split('data-kind="lecture"')[0]


def test_a_typo_is_offered_its_correction(lib, tmp_path):
    lib.set_material_text("w3", ["probability " * 3, "probability and more probability", "probability again"])
    out = run(tmp_path, _data(lib, "probabilty"), """
  S.view = { name: "library" };
  S.search = { q: "probabilty", res: data.res["probabilty"], course: "", kind: "", busy: false, items: [] };
  St.renderView(); out.typo = els["#search-results"].innerHTML;
""")
    assert "Did you mean <b>probability</b>?" in out["typo"] and 'data-q="probability"' in out["typo"]


def test_finding_in_a_transcript_shows_the_passages_with_the_words(lib, tmp_path):
    day = {"day_key": "p|2026-10-07|lecture", "title": "MATH 240 · Lecture", "date_label": "Wed 7 Oct", "subtitle": "",
           "languages": {"en": 1}, "course": {"id": "p"}, "notes": [], "marks": [], "files": {"md": "", "pdf": ""},
           "highlights": [{"part": 1, "t": 60, "ts": "01:00", "tags": ["quiz"], "text": "Next week we have the first quiz."},
                          {"part": 1, "t": 0, "ts": "00:00", "tags": ["important"], "text": "Good morning."}],
           "parts": [{"index": 1, "id": "p1", "viewer_url": "v", "time_range": "", "slides": 0, "status": "done",
                      "warnings": [], "anomalies": [], "paragraphs": [
                          {"t": 0, "e": 4, "ts": "00:00", "text": "Good morning."},
                          {"t": 60, "e": 64, "ts": "01:00", "text": "Next week we have the first quiz."},
                          {"t": 200, "e": 205, "ts": "03:20", "text": "One more thing about the quiz: bring a pencil."}]}]}
    out = run(tmp_path, {**_data(lib), "day": day}, """
  S.view = { name: "day", dayKey: data.day.day_key }; S.day = data.day; S.dayFind = "quiz one";
  St.renderView(); out.page = els["#view"].innerHTML;
""")
    page = out["page"]
    paras = page.split('class="para ')[1:]
    assert [("display:none" in p) for p in paras] == [True, False, True]      # only "the first quiz" is a quiz one
    assert "<mark>first quiz</mark>" in paras[1]
    assert "1 passage" in page
    highlights = page.split('class="card hl-card"')[1].split("</div></div>")[0]
    assert "<mark>first quiz</mark>" in highlights and "Good morning" not in highlights


def test_the_files_chats_and_plans_are_filtered_with_the_same_matching(lib, tmp_path):
    out = run(tmp_path, _data(lib), """
  const mats = [{ id: "w1", title: "Week01_Student", file_name: "Week01_Student.pdf", ext: "pdf" },
                { id: "w3", title: "Week03_Student", file_name: "Week03_Student.pdf", ext: "pdf" },
                { id: "x", title: "Exam examples", file_name: "examples.pdf", ext: "pdf" }];
  S.cd = { p: { general: [], weeks: [{ no: 1, topic_en: "Intro", start: "", end: "", units: [{ materials: mats }] }],
                materials: { new: 0 }, announcements: [] } };
  St.inputs["mat-filter"]({ dataset: { id: "p" }, value: "week 3" });     // (what typing in the filter does)
  out.files = els["#mat-list"].innerHTML;
  St.inputs["mat-filter"]({ dataset: { id: "p" }, value: "exam" });
  out.exam = els["#mat-list"].innerHTML;
  S.chat.list = [{ id: "c1", title: "Bayes questions", course_code: "MATH 240", scope_label: "", first_question: "" },
                 { id: "c2", title: "Thevenin", course_code: "EEE 215", scope_label: "", first_question: "" }];
  S.chat.filter = "bayes"; S.view = { name: "chat" };
  St.renderView(); out.chats = els["#view"].innerHTML;
""")
    names = lambda html: [x.split("</button>")[0] for x in html.split('class="mat-name"')[1:]]   # noqa: E731
    assert len(names(out["files"])) == 1 and "<mark>Week03</mark>_Student" in names(out["files"])[0]
    assert len(names(out["exam"])) == 1 and "<mark>Exam</mark> examples" in names(out["exam"])[0]   # not "examples"
    chats = out["chats"]
    assert "<mark>Bayes</mark> questions" in chats and "Thevenin" not in chats.split('id="chat-items"')[1]


# ------------------------------------------------------------------ fx-330: what the checks found
def _day(*texts):
    return {"day_key": "p|2026-10-07|lecture", "title": "MATH 240 · Lecture", "date_label": "Wed 7 Oct", "subtitle": "",
            "languages": {"en": 1}, "course": {"id": "p"}, "notes": [], "marks": [], "files": {"md": "", "pdf": ""},
            "highlights": [], "parts": [{"index": 1, "id": "p1", "viewer_url": "v", "time_range": "", "slides": 0,
                                         "status": "done", "warnings": [], "anomalies": [], "paragraphs": [
                                             {"t": 60 * i, "e": 60 * i + 4, "ts": f"0{i}:00", "text": x}
                                             for i, x in enumerate(texts)]}]}


def test_a_whole_word_of_the_library_is_never_taken_for_the_start_of_a_longer_one_on_the_page(lib, tmp_path):
    """In MATH 250's 9 Oct lecture, "exam" in the find box showed 14 passages, every one "example"; the files filter
    "exam" showed only "EEE 215 Week 6 examples CL1": a short list without the word took it for a word being typed."""
    data = {**_data(lib), "day": _day("This is an example.", "Two more examples here.", "Nothing else.")}
    data["rules"] = {**data["rules"], "known": ["exam", "exams", "week"]}       # (search_rules sends the library's)
    out = run(tmp_path, data, """
  S.view = { name: "day", dayKey: data.day.day_key }; S.day = data.day; S.dayFind = "exam";
  St.renderView(); out.page = els["#view"].innerHTML;
  S.cd = { p: { general: [], weeks: [{ no: 6, topic_en: "Intro", start: "", end: "", units: [{ materials: [
    { id: "x", title: "EEE 215 Week 6 examples CL1", file_name: "examples.pdf", ext: "pdf" },
    { id: "y", title: "Week 6 slides", file_name: "w6.pdf", ext: "pdf" }] }] }], materials: { new: 0 }, announcements: [] } };
  St.inputs["mat-filter"]({ dataset: { id: "p" }, value: "exam" });
  out.files = els["#mat-list"].innerHTML;
  S.dayFind = "exa"; S.finder = null; St.renderView(); out.typing = els["#view"].innerHTML;
""")
    paras = out["page"].split('class="para ')[1:]
    assert all("display:none" in p for p in paras) and "No match." in out["page"]
    assert "examples" not in "".join(x.split("</button>")[0] for x in out["files"].split('class="mat-name"')[1:])
    assert "<mark>example</mark>" in out["typing"]                          # a word still being typed is a prefix


def test_a_result_with_some_of_the_words_opens_on_the_passages_with_some_of_them(lib, tmp_path):
    """Every lecture result for "homework 2" had some of the words: the transcript opened with all 727 paragraphs
    hidden, "No match.", and the moment that was clicked never showed."""
    data = {**_data(lib), "day": _day("Homework is due on Friday.", "The homework was hard.", "Nothing here."),
            "both": _day("Homework 2 is due on Friday.", "The homework is long.", "Nothing here.")}
    out = run(tmp_path, data, """
  S.view = { name: "day", dayKey: data.day.day_key }; S.day = data.day; S.dayFind = "homework 2";
  St.renderView(); out.page = els["#view"].innerHTML;
  S.day = data.both; S.dayFind = "homework 2"; S.dayFindSome = true; S.finder = null;   // opened from a "some" result
  St.renderView(); out.flag = els["#view"].innerHTML;
  S.dayFindSome = false; S.finder = null; St.renderView(); out.typed = els["#view"].innerHTML;
""")
    hidden = lambda html: [("display:none" in p) for p in html.split('class="para ')[1:]]     # noqa: E731
    assert hidden(out["page"]) == [False, False, True] and "2 passages with some of the words" in out["page"]
    assert hidden(out["flag"]) == [False, False, True]
    assert hidden(out["typed"]) == [False, True, True] and "1 passage" in out["typed"]      # typed: all the words


def test_show_more_reaches_past_the_first_40_and_enter_opens_only_whats_current(lib, tmp_path):
    """"Show all 96" listed 40 with no way to the rest; and Enter right after the last letter opened the first
    result of the letters before ("About Quizzes" for "quiz 2")."""
    data = _data(lib, "quiz")
    out = run(tmp_path, data, """
  S.view = { name: "library" };
  const res = data.res["quiz"]; res.groups.forEach((g) => { g.count = 96; });
  S.search = { q: "quiz", res, course: "", kind: res.groups[0].kind, busy: false, items: [] };
  St.renderView(); out.page = els["#search-results"].innerHTML;
  S.search = { q: "quiz 2", res, course: "", kind: "", busy: true, items: [] };
  out.stale = St.searchEnter("quiz 2"); out.waits = S.search.openFirst;
  S.search = { q: "quiz", res, course: "", kind: "", busy: false, items: [] };
  out.current = St.searchEnter("quiz");
""")
    assert 'data-act="search-more"' in out["page"] and "of 96)" in out["page"]
    assert out["stale"] is False and out["waits"] == "quiz 2" and out["current"] is True


def test_enter_on_the_search_page_opens_its_own_first_result_not_one_the_quick_box_drew(lib, tmp_path):
    """The quick box (Ctrl K) keeps the rows it drew in the page, hidden, ahead of the Search page's results. In the
    real app: "laplace" in the quick box, Enter for all its results, then "first quiz" typed on the Search page and
    Enter opened EEE 215's lecture about Laplace (the quick box's first row), not the first quiz result."""
    data = _data(lib, "quiz")
    out = run(tmp_path, data, """
  S.view = { name: "library" };
  const opened = [];
  els[".sr-hit"] = { click: () => opened.push("quick box"), focus() {} };          // the first row in the page
  els["#search-results .sr-hit"] = { click: () => opened.push("search page"), focus() {} };
  S.search = { q: "quiz", res: data.res["quiz"], course: "", kind: "", busy: false, items: [] };
  out.current = St.searchEnter("quiz");
  out.opened = opened;
""")
    assert out["current"] is True and out["opened"] == ["search page"]
