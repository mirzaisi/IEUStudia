"""A video study plan on the page (plans.js, run in node with a stand-in for app.js): the steps while it listens to
videos without captions and searches again, a video checked through what IEUStudia heard in it, each skill's quote,
and the concepts without a good video with why."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from studia import i18n

WEB = Path(i18n.__file__).parent / "web"

SCRIPT = """
globalThis.window = globalThis;
globalThis.document = { documentElement: {}, querySelector: () => null, querySelectorAll: () => [], addEventListener() {},
  getElementById: () => null };
require(%(i18n)s);
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const S = { view: { name: "plan", planId: "p1" }, courses: [], plans: [], system: {} };
window.Studia = { S, A: {}, I: (name) => `<i:${name}>`, esc, SPIN: () => "<spin>", toast() {}, modal() {}, closeModal() {},
  go() {}, renderView() {}, renderSidebar() {}, emptyHTML: () => "", skeleton: () => "", fmtK: (n) => n,
  call: () => Promise.resolve({ ok: true }), findCourse: () => ({ code: "MATH 240" }), views: {}, onGo: {}, onEvents: {},
  mocks: {}, mdToHtml: (s) => s };
require(%(plans)s);
const St = window.Studia, out = {};
const running = { id: "p1", course_id: "c", status: "running", stage: "listen", progress: 0.58, created_at: "2026-11-08",
  message: "Listening to 3 videos without captions: 2 of 3 · transcribing it", scope: { weeks: [7], minutes: 180 },
  stages_done: ["gather", "concepts", "search", "inspect"], concepts: [] };
S.planCache.p1 = running;
out.progress = St.views.plan({ planId: "p1" });
const quote = { t: 30, text: "so we standardize it by subtracting the mean", url: "u30", skill: "standardize X" };
const done = { ...running, id: "p2", status: "done", stage: "done", progress: 1, title: "Week 7",
  result: { title: "Week 7", summary: "s", total_minutes: 5, optional_minutes: 0, schedule: [], checklist: [], practice: [],
    supplements: [], changes: {}, concepts: [{ id: "c1", name: "Normal distribution" }],
    items: [{ n: 1, video: "vid00000001", title: "Standardizing", channel: "Ch", minutes: 5, role: "required", whole: false,
      start: 0, end: 300, url: "u", segments: [{ start: 0, end: 300, url: "u" }], concepts: ["c1"], state: "verified",
      captions: "speech, en", heard: { model: "large-v3" }, quotes: [quote], excerpts: [], errors: [], inspected: "x",
      teaches: [{ concept: "c1", state: "partial", skills: ["standardize X"], missing: ["use the 68-95-99.7 rule"],
        proofs: [{ skill: "standardize X", t: 30, text: quote.text, url: "u30" }] }] }],
    coverage: [{ concept: "c1", name: "Normal distribution", in_scope: true, state: "partial", items: [1], note: "",
      why: "9 videos from 6 YouTube searches were checked, and none of them teaches this.",
      skills: [{ text: "standardize X", items: [1], how: "video", quote: { item: 1, t: 30, text: quote.text } },
               { text: "use the 68-95-99.7 rule", items: [], how: "missing", quote: null }] }],
    listened: { heard: 2, seconds: 95, skipped: [{ n: 1, why: "it is longer than 120 minutes" }] },
    completeness: { goal: "week 7", headline: "Not complete for week 7.", level: "incomplete", complete: false, skills: 2,
      video: 1, optional: 0, supplement: 0, missing: 1, missing_list: [], supplement_list: [], extras: [],
      no_video: [{ concept: "c1", name: "Normal distribution", skills: ["use the 68-95-99.7 rule"],
        why: "9 videos from 6 YouTube searches were checked, and none of them teaches this." }] } } };
S.planCache.p2 = done;
S.planTab = "watch";
out.watch = St.views.plan({ planId: "p2" });
S.planTab = "coverage";
out.coverage = St.views.plan({ planId: "p2" });
process.stdout.write(JSON.stringify(out), () => process.exit(0));
"""


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    script = tmp_path_factory.mktemp("plans") / "plans.js"
    script.write_text(SCRIPT % {"i18n": json.dumps(str(WEB / "i18n.js")), "plans": json.dumps(str(WEB / "plans.js"))},
                      encoding="utf-8")
    return json.loads(subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8",
                                     check=True).stdout)


def test_the_steps_while_it_listens_and_searches_again(page):
    h = page["progress"]
    assert '<div class="pl-step now"><span class="dot"><spin></span>Listening</div>' in h
    assert '<div class="pl-step done"><span class="dot"><i:check></span>Captions</div>' in h
    assert h.index("Listening") < h.index("Verification") < h.index("More videos") < h.index("Your plan")
    assert "Listening to 3 videos without captions: 2 of 3 · transcribing it" in h
    assert "it downloads only the sound, transcribes it on this PC and deletes it" in h


def test_a_video_checked_through_what_was_heard_with_each_skills_quote(page):
    h = page["watch"]
    assert "Checked in what it says: it has no usable captions, so IEUStudia listened to it" in h
    assert "(shows: standardize X)" in h and "so we standardize it by subtracting the mean" in h
    assert 'title="At 0:30: “so we standardize it by subtracting the mean”"><i:check>standardize X' in h
    assert "No good video for Normal distribution:</b>" in h and "use the 68-95-99.7 rule. " in h
    assert "9 videos from 6 YouTube searches were checked, and none of them teaches this." in h
    assert ("<i:wave><span>IEUStudia listened to 2 videos that have no usable captions (on this PC). 1 more wasn&#39;t "
            "listened to: it is longer than 120 minutes.</span>") in h


def test_the_coverage_table_says_why_and_shows_the_quotes(page):
    h = page["coverage"]
    assert 'class="dim sm pl-why-none"><i:youtube> 9 videos from 6 YouTube searches were checked' in h
    assert 'title="At 0:30: “so we standardize it by subtracting the mean”"' in h


SUMMARY_SCRIPT = SCRIPT.split("const St = window.Studia, out = {};", 1)[0] + """
const St = window.Studia;
const nv = (i) => ({ concept: "c" + i, name: "Concept " + i, skills: ["skill " + i + "a", "skill " + i + "b"], missing: ["skill " + i + "b"],
  why: "Reason " + i + "." });
const plan = (noVideo, listened) => ({ id: "p3", course_id: "c", status: "done", stage: "done", progress: 1, title: "Week 7",
  created_at: "2026-11-08", scope: { weeks: [7], minutes: 180 }, stages_done: [], concepts: [],
  result: { title: "Week 7", summary: "s", total_minutes: 5, optional_minutes: 0, schedule: [], checklist: [], practice: [],
    supplements: [], changes: {}, concepts: [], items: [], coverage: [], listened,
    completeness: { goal: "week 7", headline: "Not complete for week 7.", level: "incomplete", complete: false, skills: 8,
      video: 0, optional: 0, supplement: 4, missing: 4, extras: [], no_video: noVideo,
      missing_list: noVideo.map((x) => x.missing[0] + " (" + x.name + ")"),
      supplement_list: noVideo.map((x) => x.skills[0] + " (" + x.name + ")") } } });
S.planTab = "watch";
S.planCache.p3 = plan([nv(1), nv(2)], { heard: 4, silent: 1, seconds: 90, skipped: [] });
const few = St.views.plan({ planId: "p3" });
S.planCache.p3 = plan([nv(1), nv(2), nv(3), nv(4)], { heard: 0, silent: 2, seconds: 30, skipped: [] });
const many = St.views.plan({ planId: "p3" });
process.stdout.write(JSON.stringify({ few, many }), () => process.exit(0));
"""


def test_the_summary_says_each_skill_without_a_good_video_once_and_folds_many(tmp_path):
    """The summary box listed 15 skills under "Only in the written supplements", then 16 "No good video for …" rows
    with the same skills again: about 1000 px before the plan itself."""
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    script = tmp_path / "summary.js"
    script.write_text(SUMMARY_SCRIPT % {"i18n": json.dumps(str(WEB / "i18n.js")), "plans": json.dumps(str(WEB / "plans.js"))},
                      encoding="utf-8")
    out = json.loads(subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8",
                                    check=True).stdout)
    few, many = out["few"], out["many"]
    assert "Only in the written supplements:" not in few and "Not covered:" not in few
    assert few.count("skill 1b") == 1 and "skill 1b <span class=\"pl-cp-miss\">(not covered)</span>" in few
    assert "No good video for Concept 2:" in few and "<details" not in few
    assert "IEUStudia listened to 4 videos that have no usable captions (on this PC). It also listened to 1 more, but " \
           "heard no speech in it." in few
    assert '<details class="pl-cp-fold">' in many and "No good video for 4 topics:" in many
    assert many.count("No good video for Concept") == 4                       # all four, inside the fold
    assert "IEUStudia listened to 2 videos that have no usable captions, but heard no speech in them." in many
