"""Video study plans: the pipeline with Claude and YouTube replaced by fakes (no network, no Claude)."""

import dataclasses as dc
import datetime as dt
import re
import sys
import threading
import time
import types

import pytest

import studia
from studia import plans, store
from studia import videos as real_videos
from studia.ai import parse_json
from studia.config import Settings
from studia.coursesite import Resource, Unit
from studia.domain import Course, Recording, Segment, Transcript
from studia.library import Library
from studia.plans import PlanRunner, Scope, clean_concepts, clean_verdicts, find_quote, finalize, match_quote


# ------------------------------------------------------------------ fake YouTube
@dc.dataclass
class Hit:
    id: str
    title: str
    channel: str
    duration_s: float
    url: str = ""
    view_count: int = 1000
    upload_date: str = ""
    description: str = ""
    query: str = ""
    channel_id: str = ""
    like_count: int = None

    def to_json(self):
        return dc.asdict(self)


@dc.dataclass
class Ch:
    start: float
    end: float
    title: str


@dc.dataclass
class Info:
    id: str
    title: str
    channel: str
    duration_s: float
    caption_kind: str = "auto"
    caption_lang: str = "en"
    upload_date: str = ""
    view_count: int = 1
    chapters: list = dc.field(default_factory=list)
    cues: list = dc.field(default_factory=lambda: [1])
    like_count: int = None
    channel_id: str = ""
    language: str = ""

    @property
    def has_captions(self):
        return bool(self.caption_kind)


VIDEOS = {"v1": Info("v1", "Bayes' Theorem - The Simplest Case", "Dr. Trefor Bazett", 331, chapters=[Ch(0, 100, "Deriving")]),
          "v2": Info("v2", "Conditional probability explained", "Khan Academy", 600),
          "v3": Info("v3", "No captions here", "Someone", 400, caption_kind="", cues=[])}


def fake_videos(calls):
    m = types.ModuleType("studia.videos")

    def search_many(queries, limit=8, *, workers=4, cancel=None, progress=None):
        calls["search"] += 1
        calls.setdefault("queries", []).extend(queries)
        out = {}
        for i, q in enumerate(queries, 1):
            if progress:
                progress(i, len(queries), q)
            out[q] = [Hit(v.id, v.title, v.channel, v.duration_s, query=q) for v in VIDEOS.values()]
        return out

    def inspect_many(ids, *, workers=4, langs=("en",), cancel=None, progress=None):
        calls["inspect"] += 1
        calls["langs"] = langs
        return {i: VIDEOS.get(i) or Info(i, "Preferred video", "Preferred channel", 500, channel_id="UC" + "p" * 22,
                                         like_count=40) for i in ids}

    def rank(hits, terms, allow=None, block=None, **kw):      # as videos.rank does with the channel lists
        kept = [h for h in hits if not (block and block(h.channel_id, h.channel))]
        return sorted(kept, key=lambda h: not (allow and allow(h.channel_id, h.channel)))

    def channel_search_many(pairs, limit=4, *, workers=4, cancel=None):
        calls.setdefault("channel_searches", []).extend(pairs)
        return {p: [Hit(f"own{i:08d}", f"Preferred: {p[1]}", "Preferred channel", 500, query=p[1], channel_id=p[0])]
                for i, p in enumerate(pairs)}

    m.search_many = search_many
    m.inspect_many = inspect_many
    m.channel_search_many = channel_search_many
    m.channels = lambda ids, **kw: calls.setdefault("channels", []).extend(ids) or {
        "UC" + "p" * 22: real_videos.Channel("UC" + "p" * 22, "Preferred channel", avatar="https://yt3.ggpht.com/p=s88")}
    for name in ("in_languages", "reception", "_terms", "_tokens"):      # pure functions: the real ones
        setattr(m, name, getattr(real_videos, name))
    m.rank = rank
    m.windows = lambda info, terms, window_s=180, top=3: [{"start": 30.0, "end": 150.0, "score": 3, "text": f"{info.title} explained"}]
    m.caption_text = lambda info, start=None, end=None: "text"
    m.fmt_ts = lambda s: f"{int(s) // 60}:{int(s) % 60:02d}"
    m.stamped_text = lambda info, start=None, end=None, every_s=30, limit=2500: (
        f"[{m.fmt_ts(start or 0)}] {info.title} explained. So the posterior is the prior times the likelihood "
        f"[{m.fmt_ts((start or 0) + 30)}] divided by the evidence, and here is a tree diagram.")
    m.stamped_part = lambda info, start=None, end=None, every_s=30, limit=2500: {
        "start": start or 0, "end": end or 0, "text": m.stamped_text(info, start, end)}
    m.watch_url = lambda vid, start=None: f"https://www.youtube.com/watch?v={vid}" + (f"&t={int(start)}s" if start else "")
    return m


CONCEPTS = {"concepts": [
    {"id": "c1", "name": "Bayes' theorem", "summary": "Invert a conditional probability.", "weeks": [1], "los": [3],
     "kind": "procedure", "importance": "core", "in_scope": True, "focus": True, "cues": ["given P(B|A), find P(A|B)"],
     "skills": ["invert a conditional", "use a tree diagram"],
     "terms": ["Bayes", "posterior"], "queries": ["bayes theorem example", "bayes rule intuition"]},
    {"id": "c2", "name": "Conditional probability", "summary": "Use P(A|B).", "weeks": [1], "los": [3],
     "kind": "concept", "importance": "prerequisite", "in_scope": True, "focus": False, "cues": ["given that"],
     "skills": ["compute P(A|B) from a table"], "terms": ["given"], "queries": ["conditional probability"]},
    {"id": "c3", "name": "Independence", "summary": "Check whether events are independent.", "weeks": [1], "los": [4],
     "kind": "concept", "importance": "core", "in_scope": True, "focus": False, "cues": ["independent"],
     "skills": ["check P(A∩B) = P(A)P(B)"], "terms": ["independent"], "queries": ["independent events"]}],
    "practice": [{"concept": "c3", "question": "Are A and B independent?", "answer": "No.", "source": "Week01.pdf p.2"},
                 {"concept": "c1", "question": "A test is 95% accurate…", "answer": "≈0.16", "source": "Week01.pdf p.3"}],
    "gaps": ""}


def fake_ask(log, fail_verify=None):
    def ask(system, prompt, model="sonnet", cancel=None, **kw):
        log.append((model, prompt))
        usage = {"usage": {"input_tokens": 100, "output_tokens": 50}, "model": model}
        if prompt.startswith("TASK: List what the student must learn"):
            return CONCEPTS, usage
        if prompt.startswith("TASK: For each concept, judge"):
            if fail_verify and fail_verify.pop():
                raise RuntimeError("Claude hiccup")
            return {"verdicts": [
                {"concept": "c1", "video": "v1", "coverage": "full", "skills": [1], "start": 20, "end": 300,
                 "level": "course", "examples": True, "quality": 5, "note": "",
                 "quotes": [{"t": 40, "text": "so the posterior is the prior times the likelihood"},
                            {"t": 50, "text": "Bayes was a French mathematician who lived in Paris"}],     # not said
                 "errors": [{"t": 100, "text": "says P(A|B) = P(B|A); it is P(B|A)P(A)/P(B)"}]},
                {"concept": "c1", "video": "v2", "coverage": "none"},
                {"concept": "c2", "video": "v2", "coverage": "partial", "skills": [1], "start": 0, "end": 9999, "quality": 3},
                {"concept": "c9", "video": "v1", "coverage": "full"}]}, usage           # never asked: ignored
        if prompt.startswith("TASK: Build the final video study plan"):
            return {"title": "Bayes for Quiz 2", "summary": "Two videos.", "items": [
                {"n": 1, "video": "v2", "start": 0, "end": 600, "role": "prerequisite", "concepts": ["c2"], "why": "basics"},
                {"n": 2, "video": "v1", "segments": [[20, 120], [200, 300]], "role": "required", "concepts": ["c1"],
                 "why": "best", "limits": "no tree diagram"},
                {"n": 3, "video": "zzz", "start": 0, "end": 10, "role": "required", "concepts": ["c1"]},
                {"n": 4, "video": "v2", "start": 0, "end": 600, "role": "required", "concepts": ["c3"]}],
                "schedule": [{"day": "Mon", "items": [1, 2]}],
                "notes": [{"concept": "c1", "note": "trees only in the supplement"}],
                "checklist": [{"concept": "c1", "explain": "Flip it.", "recognize": ["P(A|B) from P(B|A)"]}],
                "supplements": [{"concept": "c1", "text": "Draw the tree: …"},
                                {"concept": "c2", "text": "P(A|B) = P(A∩B)/P(B)."}]}, usage
        raise AssertionError(prompt[:80])
    return ask


@pytest.fixture
def env(tmp_path, monkeypatch):
    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="c", title="2026-1 MATH 240 P 1", code="MATH 240", name="Probability", term="T")])
    lib.merge_units("c", [Unit("u1", 0, "Hafta 1", dt.date(2026, 9, 21), dt.date(2026, 9, 27), "Ch. 2",
                               [Resource("r1", "Week01", "Week01.pdf", "pdf", "pdf", "u", "d")])], first_sync=True)
    lib.set_material_file("r1", str(tmp_path / "Week01.pdf"), 10, "u")
    (tmp_path / "Week01.pdf").write_bytes(b"%PDF")
    lib.set_material_text("r1", ["Bayes theorem: P(A|B) = P(B|A)P(A)/P(B)", "Example: medical test"])
    lib.set_course_info("c", syllabus={"outcomes": ["define probability", "count", "use Bayes' theorem"],
                                       "weeks": [{"no": 1, "subject": "Conditional probability and Bayes", "preparation": ""}]})
    rec = Recording(delivery_id="r", course_id="c", folder_id="f", folder_path="", name="n",
                    start=dt.datetime(2026, 9, 23, 9, 0), duration_s=3000, viewer_url="v")
    lib.merge_recordings("c", [rec], first_sync=True)
    t = Transcript(delivery_id="r", segments=[Segment(0, 5, "Today: Bayes theorem. This will be on the quiz.", "en", 0.9)],
                   duration_s=3000, speech_s=5, language_seconds={"en": 5}, model="m", device="cpu", engine_version="t",
                   created_at="2026-09-23T12:00:00")
    lib.save_transcript(t, store.save_transcript(t))
    lib.set_status("r", "done")
    calls = {"search": 0, "inspect": 0}
    fake = fake_videos(calls)
    monkeypatch.setitem(sys.modules, "studia.videos", fake)
    monkeypatch.setattr(studia, "videos", fake, raising=False)
    events = []
    svc = types.SimpleNamespace(lib=lib, settings=Settings(ai_model="sonnet"), emit=events.append,
                                output_root=tmp_path / "out")
    return types.SimpleNamespace(lib=lib, svc=svc, events=events, calls=calls, tmp=tmp_path)


def wait_done(lib, pid, timeout=10):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        p = lib.plan_brief(pid)
        if p["status"] in ("done", "error", "paused"):
            return p
        time.sleep(0.02)
    raise AssertionError("plan did not finish")


def test_gather_collects_outcomes_weeks_files_and_lectures(env):
    facts = plans.gather(env.lib, env.svc.settings, "c", Scope(weeks=[1]))
    pack = facts["pack"]
    assert "LO3: use Bayes' theorem" in pack and "Week 1 (2026-09-21 to 2026-09-27)" in pack
    assert "[p.1] Bayes theorem" in pack and "Today: Bayes theorem" in pack
    assert [m["title"] for m in facts["materials_used"]] == ["Week01"] and len(facts["days_used"]) == 1


def test_plan_pipeline_end_to_end(env):
    asked = []
    runner = PlanRunner(env.svc, ask=fake_ask(asked))
    pid = runner.create("c", {"weeks": [1], "focus": "Quiz 2", "los": [3], "minutes": 60, "difficulties": "Bayes"})
    runner.start(pid)
    p = wait_done(env.lib, pid)
    assert p["status"] == "done", p
    full = env.lib.plan(pid)
    r = full["result"]
    assert r["title"] == "Bayes for Quiz 2" and [it["video"] for it in r["items"]] == ["v2", "v1", "v2"]   # unknown id dropped
    first, second, third = r["items"]
    assert second["title"] == "Bayes' Theorem - The Simplest Case" and second["channel"] == "Dr. Trefor Bazett"
    assert [(s["start"], s["end"]) for s in second["segments"]] == [(0, 120), (180, 300)] and second["minutes"] == 4
    assert second["segments"][1]["url"].endswith("&t=180s") and not second["whole"]
    # "full" without every skill is only partial; what it does not teach is said
    assert second["teaches"] == [{"concept": "c1", "state": "partial", "skills": ["invert a conditional"],
                                  "missing": ["use a tree diagram"]}]
    assert [q["t"] for q in second["quotes"]] == [30] and second["quotes"][0]["url"].endswith("&t=30s")   # made-up quote dropped
    assert second["errors"][0]["t"] == 100 and second["limits"] == "no tree diagram" and second["state"] == "verified"
    assert second["excerpts"][0]["text"].startswith("[0:30] Bayes' Theorem") and "1 part read" in second["inspected"]
    assert first["whole"] and first["role"] == "prerequisite" and first["inspected"].startswith("captions (auto, en)")
    # not on the quiz's outcome: never required, and said so
    assert third["extra"] and third["role"] == "optional" and third["teaches"][0]["state"] == "unchecked"
    cov = {c["concept"]: c for c in r["coverage"]}
    assert cov["c1"]["state"] == "partial" and [x["how"] for x in cov["c1"]["skills"]] == ["video", "supplement"]
    assert cov["c1"]["note"] == "trees only in the supplement"
    assert cov["c2"]["state"] == "verified" and not cov["c3"]["in_scope"] and not cov["c3"]["searched"]
    cp = r["completeness"]
    assert cp["complete"] and (cp["skills"], cp["video"], cp["supplement"], cp["missing"]) == (3, 2, 1, 0)
    assert cp["goal"] == "Quiz 2 (LO3)" and cp["level"] == "written"            # a third only in writing: said so
    assert cp["headline"].startswith("Covered for Quiz 2 (LO3), but partly only in writing: of the 3 skills")
    assert [x["name"] for x in cp["extras"]] == ["Independence"]
    assert [x["concept"] for x in r["checklist"]] == ["c1", "c2"] and r["practice"][0]["concept"] == "c1"
    assert r["supplements"][1]["name"] == "Conditional probability" and r["practice"][0]["source"] == "Week01.pdf p.3"
    assert r["total_minutes"] == first["minutes"] + second["minutes"] and r["optional_minutes"] == third["minutes"]
    assert r["schedule"][0]["minutes"] == r["total_minutes"]
    # the extra topic was not searched; the verifier saw the skills and the timed captions
    assert "independent events" not in env.calls["queries"] and "bayes theorem example" in env.calls["queries"]
    verify = next(q for m, q in asked if q.startswith("TASK: For each concept, judge"))
    assert "skills: 1) invert a conditional; 2) use a tree diagram" in verify and "[0:30] Bayes' Theorem" in verify
    assemble = next(q for m, q in asked if q.startswith("TASK: Build the final video study plan"))
    assert "teaches skills 1" in assemble and "MISTAKE at 1:40" in assemble
    assert "no videos were searched: Independence" in assemble
    md = (env.tmp / "out").rglob("*.md")
    text = next(md).read_text(encoding="utf-8")
    assert "## Viewing plan" in text and "Dr. Trefor Bazett" in text and "| Bayes' theorem | yes | partial |" in text
    assert "Covered for Quiz 2 (LO3)" in text and "Possible mistake at" in text and "Independence" in text
    assert [m for m, _ in asked] == ["sonnet", "haiku", "sonnet"]
    assert full["usage"]["verify"]["model"] == "haiku" and full["usage"]["concepts"]["input_tokens"] == 100
    assert any(e["type"] == "plan" and e["status"] == "done" for e in env.events)


def test_extras_are_searched_only_when_asked_for(env):
    runner = PlanRunner(env.svc, ask=fake_ask([]))
    pid = runner.create("c", {"weeks": [1], "focus": "Quiz 2", "los": [3], "extras": True})
    runner.start(pid)
    assert wait_done(env.lib, pid)["status"] == "done"
    assert "independent events" in env.calls["queries"]
    r = env.lib.plan(pid)["result"]
    assert r["completeness"]["extras_searched"] and r["items"][-1]["extra"]


def test_a_failed_plan_resumes_without_redoing_finished_stages(env):
    asked = []
    runner = PlanRunner(env.svc, ask=fake_ask(asked, fail_verify=[True]))
    pid = runner.create("c", {"weeks": [1]})
    runner.start(pid)
    p = wait_done(env.lib, pid)
    assert p["status"] == "error" and "hiccup" in p["error"]
    runner.start(pid)
    assert wait_done(env.lib, pid)["status"] == "done"
    assert (env.calls["search"], env.calls["inspect"]) == (1, 1)
    assert [m for m, _ in asked] == ["sonnet", "haiku", "haiku", "sonnet"]


def test_finished_verify_requests_are_kept_when_a_plan_stops(env, monkeypatch):
    monkeypatch.setattr(plans, "VERIFY_BATCH", 1)                    # one request per concept
    monkeypatch.setattr(plans, "VERIFY_PARALLEL", 1)
    asked, failed = [], []

    def ask(system, prompt, model="sonnet", cancel=None, **kw):
        if prompt.startswith("TASK: For each concept, judge"):
            cid = re.search(r"^CONCEPT (c\d)", prompt, re.M).group(1)
            asked.append(cid)
            if cid == "c2" and not failed:
                failed.append(cid)
                raise RuntimeError("usage limit")
        return fake_ask([])(system, prompt, model)

    runner = PlanRunner(env.svc, ask=ask)
    pid = runner.create("c", {"weeks": [1]})
    runner.start(pid)
    assert wait_done(env.lib, pid)["status"] == "error"
    data = env.lib.plan(pid)["data"]
    assert "verify" not in data and "c1" in data["verify_parts"] and "c2" not in data["verify_parts"]
    del asked[:]
    runner.start(pid)
    assert wait_done(env.lib, pid)["status"] == "done"
    assert "c1" not in asked and "c2" in asked                       # c1's answer was not asked for again
    full = env.lib.plan(pid)
    assert "verify_parts" not in full["data"] and full["data"]["verify"]["c1"]["v1"]["coverage"] == "partial"
    assert full["result"]["items"][1]["state"] == "verified"
    runner.start(pid, from_stage="verify")                            # redoing the stage asks everything again
    assert wait_done(env.lib, pid)["status"] == "done" and asked.count("c1") == 1


def test_an_update_of_only_the_time_reuses_the_concepts_and_the_checked_videos(env):
    asked = []
    runner = PlanRunner(env.svc, ask=fake_ask(asked))
    pid = runner.create("c", {"weeks": [1], "minutes": 120})
    runner.start(pid)
    wait_done(env.lib, pid)
    del asked[:]
    pid2 = runner.create("c", {"weeks": [1], "minutes": 60, "deadline": "2026-10-10"}, parent_id=pid)
    runner.start(pid2)
    assert wait_done(env.lib, pid2)["status"] == "done"
    assert len(asked) == 1 and asked[0][1].startswith("TASK: Build the final video study plan")
    assert (env.calls["search"], env.calls["inspect"]) == (1, 1)
    old, new = env.lib.plan(pid)["data"], env.lib.plan(pid2)["data"]
    assert new["concepts"] == old["concepts"] and new["verify"] == old["verify"]
    del asked[:]
    pid3 = runner.create("c", {"weeks": [1], "minutes": 60, "difficulties": "trees"}, parent_id=pid2)
    runner.start(pid3)                                                # another question: everything again
    assert wait_done(env.lib, pid3)["status"] == "done" and [m for m, _ in asked] == ["sonnet", "haiku", "sonnet"]


def test_lectures_share_the_pack_and_keep_their_emphasised_moments(env, monkeypatch):
    monday = dt.date(2026, 9, 21)
    env.lib.merge_units("c", [Unit(f"u{w}", w - 1, f"Hafta {w}", monday + dt.timedelta(7 * w - 7),
                                   monday + dt.timedelta(7 * w - 1), "", []) for w in (1, 2, 3)], first_sync=False)
    for w in (1, 2, 3):
        rec = Recording(delivery_id=f"w{w}", course_id="c", folder_id="f", folder_path="", name="n",
                        start=dt.datetime.combine(monday + dt.timedelta(7 * w - 3), dt.time(9)), duration_s=3000,
                        viewer_url="v")
        env.lib.merge_recordings("c", [rec], first_sync=False)
        segs = [Segment(i * 10, i * 10 + 4, f"Week {w} point {i} about trees.", "en", 0.9) for i in range(120)]
        segs[100] = Segment(1000, 1004, f"Week {w}: this will be on the exam.", "en", 0.9)
        t = Transcript(delivery_id=rec.delivery_id, segments=segs, duration_s=3000, speech_s=500,
                       language_seconds={"en": 500}, model="m", device="cpu", engine_version="t", created_at="x")
        env.lib.save_transcript(t, store.save_transcript(t))
        env.lib.set_status(rec.delivery_id, "done")
    pack = plans.gather(env.lib, env.svc.settings, "c", Scope(weeks=[1, 2, 3]))["pack"]
    head = pack.index("\n=== LECTURE")                                # the outcomes, weeks and files before them
    monkeypatch.setattr(plans, "PACK_CHARS", head + 7_000)            # room for about a third of each lecture
    facts = plans.gather(env.lib, env.svc.settings, "c", Scope(weeks=[1, 2, 3]))
    assert [d["week"] for d in facts["days_used"]] == [1, 1, 2, 3] and facts["days_left_out"] == []
    pack = facts["pack"]
    for w in (2, 3):                                                  # every week, not only the first ones
        assert f"⭐ Week {w}: this will be on the exam." in pack
        assert f"Week {w} point 3 about" in pack or f"Week {w} point 4 about" in pack     # the start …
        assert f"Week {w} point 115 about" in pack or f"Week {w} point 116 about" in pack  # … and the end
        assert f"Week {w} point 60 about" in pack or f"Week {w} point 61 about" in pack   # … and the middle
    assert pack.count("(Excerpts: the moments the lecturer emphasised") == 3 and len(pack) <= plans.PACK_CHARS
    monkeypatch.setattr(plans, "PACK_CHARS", head + 4_000)            # not enough for all: the short one and two long
    facts = plans.gather(env.lib, env.svc.settings, "c", Scope(weeks=[1, 2, 3]))   # ones, the first and the last
    assert [d["week"] for d in facts["days_used"]] == [1, 1, 3] and [d["week"] for d in facts["days_left_out"]] == [2]


def test_lecture_days_are_split_evenly():
    keep, rooms = plans._fit_days([500, 8_000, 8_000], [0, 0, 0], 10_000)
    assert keep == [0, 1, 2] and rooms == {0: 500, 1: 4_750, 2: 4_750}      # a short day leaves its share to the others
    keep, rooms = plans._fit_days([5_000] * 10, [30] * 10, 9_000)
    assert len(keep) == 5 and keep[0] == 0 and keep[-1] == 9 and min(rooms.values()) >= plans.DAY_MIN
    keep, rooms = plans._fit_days([5_000, 200, 5_000, 5_000], [30] * 4, 3_500)
    assert keep == [0, 1, 3] and rooms[1] == 200                         # a short day costs little: it stays
    assert plans._fit_days([], [], 9_000) == ([], {})
    day = "# MATH 240\n\n## Part 1 (09:00–09:50)\n" + "\n".join(f"[0{i}:00] Line {i}." for i in range(10))
    assert plans._excerpt(day, 10_000) == day
    cut = plans._excerpt(day + "\n[10:00] ⭐ The key idea.", 200)
    assert cut.startswith("# MATH 240\n(Excerpts:") and "## Part 1" in cut and "⭐ The key idea." in cut
    assert [n for n in range(10) if f"Line {n}." in cut] == [2, 7] and len(cut) <= 200      # spread over the lecture


def test_an_update_tells_claude_about_the_previous_plan(env):
    asked = []
    runner = PlanRunner(env.svc, ask=fake_ask(asked))
    pid = runner.create("c", {"weeks": [1]})
    runner.start(pid)
    wait_done(env.lib, pid)
    seen = []
    runner._ask = lambda system, prompt, model="sonnet", cancel=None, **kw: (seen.append(prompt), fake_ask([])(system, prompt, model))[1]
    pid2 = runner.create("c", {"weeks": [1], "minutes": 30}, parent_id=pid)
    runner.start(pid2)
    assert wait_done(env.lib, pid2)["status"] == "done"
    assert "This is an UPDATE of the previous plan" in seen[-1] and "Conditional probability explained" in seen[-1]


def test_cancel_before_it_runs(env):
    gate = threading.Event()

    def slow(system, prompt, model="sonnet", cancel=None, **kw):
        gate.wait(5)
        return fake_ask([])(system, prompt, model)

    runner = PlanRunner(env.svc, ask=slow)
    a, b = runner.create("c", {"weeks": [1]}), runner.create("c", {"weeks": [1]})
    runner.start(a)
    runner.start(b)
    runner.cancel(b)                                  # still queued: stops at once
    assert env.lib.plan_brief(b)["status"] == "paused"
    runner.cancel(a)
    gate.set()
    assert wait_done(env.lib, a)["status"] == "paused"


def test_finalize_checks_claudes_plan_against_the_real_data(monkeypatch):
    fake = fake_videos({"search": 0, "inspect": 0})
    monkeypatch.setitem(sys.modules, "studia.videos", fake)
    monkeypatch.setattr(studia, "videos", fake, raising=False)
    concepts =[{"id": "c1", "name": "A", "summary": "s", "importance": "core", "focus": False, "weeks": [], "los": [], "cues": ["x"]},
                {"id": "c2", "name": "B", "summary": "t", "importance": "core", "focus": False, "weeks": [], "los": [], "cues": []}]
    vids = {"v1": {"title": "T1", "channel": "C1", "duration_s": 300, "has_captions": True, "caption_kind": "manual",
                   "caption_lang": "en", "chapters": []}}
    raw = {"items": [{"n": 7, "video": "v1", "start": 100, "end": 9999, "role": "weird", "concepts": ["c1", "nope"]},
                     {"n": 8, "video": "v1", "start": 0, "end": 300, "role": "alternative", "alternative_to": 7, "concepts": ["c1"]},
                     {"n": 9, "video": "v1", "segments": [[50, 40], "x"], "concepts": ["c2"]}],
           "coverage": [{"concept": "c1", "state": "verified", "items": [7]}, {"concept": "c1", "state": "missing"}]}
    r = finalize(raw, concepts, {}, vids, [], "", verdicts={"c2": {"v1": {"coverage": "none"}}})
    a, b = r["items"]                                  # the third: checked, and it does not teach c2
    assert (a["start"], a["end"], a["role"], a["concepts"]) == (80, 300, "required", ["c1"])
    assert b["alternative_to"] == 1 and b["whole"] and r["optional_minutes"] == b["minutes"]
    # the AI said "verified", but nobody checked these captions for c1
    assert a["state"] == "unchecked" and a["teaches"][0]["state"] == "unchecked"
    assert {c["concept"]: (c["state"], c["items"]) for c in r["coverage"]} == {"c1": ("unverified", [1, 2]), "c2": ("missing", [])}
    assert [x["concept"] for x in r["checklist"]] == ["c1", "c2"]
    cp = r["completeness"]
    assert not cp["complete"] and cp["missing"] == 2 and cp["headline"].startswith("Not complete for all weeks so far")


def test_quotes_must_be_in_the_captions():
    parts = [{"start": 60, "end": 240, "text": "[1:00] Now, the pivot columns. [1:30] If a column has NO pivot, its "
                                              "variable is free \u2014 we can choose it. [2:00] Then back-substitute."}]
    assert find_quote("if a column has no pivot its variable is free", parts) == 90
    assert find_quote("\u201cIf a column has no pivot, its variable is free\u2026\u201d", parts) == 90      # case, punctuation
    assert find_quote("the pivot columns", parts) == 60
    assert find_quote("a free variable can be chosen, says the lecturer", parts) is None         # paraphrase
    assert find_quote("determinants are multiplicative", parts) is None and find_quote("", parts) is None
    assert find_quote("then back substitute", [{"start": 5, "text": "Then back-substitute."}]) == 5   # no marks: the start
    # what is shown are the captions' own words (with their sentence), never the AI's version of them
    assert match_quote("if a column has no pivot, the variable is free", parts) == (
        90, "If a column has NO pivot, its variable is free — we can choose it.")
    # a quote stitched from two places keeps the sentence that is really there
    assert match_quote("Everyone loves linear algebra and its many uses today. If a column has no pivot, its variable is free.",
                       parts) == (90, "If a column has NO pivot, its variable is free — we can choose it.")
    assert match_quote("Now, the pivot columns are what matter. Its variable is free, so we choose any number.", parts) is None


def test_verdicts_are_checked():
    concepts = [{"id": "c1", "skills": ["a", "b", "c"]}, {"id": "c2", "skills": ["x"]}]
    vids = {"v": {"duration_s": 600}}
    excerpts = {"c1": {"v": [{"start": 0, "end": 120, "text": "[0:00] hello [1:00] the row echelon form is unique"}]}}
    raw = {"verdicts": [
        {"concept": "c1", "video": "v", "coverage": "full", "skills": [1, 3, 9, "x"], "start": 30, "end": 9000,
         "quotes": [{"t": 3, "text": "the row echelon form is unique"}, {"t": 4, "text": "something never said at all"}],
         "errors": [{"t": 7000, "text": "wrong sign"}, {"t": 5}]},
        {"concept": "c2", "video": "v", "coverage": "full"},
        {"concept": "c2", "video": "w", "coverage": "full"}]}
    out = clean_verdicts(raw, {"c1": {"v"}, "c2": {"v"}}, vids, concepts, excerpts)
    v1 = out["c1"]["v"]
    assert v1["coverage"] == "partial" and v1["skills"] == ["a", "c"] and (v1["start"], v1["end"]) == (30, 600)
    assert v1["quotes"] == [{"t": 60, "text": "the row echelon form is unique"}] and v1["evidence"]
    assert v1["errors"] == [{"t": 600, "text": "wrong sign"}]
    assert out["c2"]["v"]["coverage"] == "full" and out["c2"]["v"]["skills"] == ["x"] and "w" not in out["c2"]


def test_concepts_are_cleaned_and_scope_is_clamped():
    cs, practice, gaps = clean_concepts({"concepts": [{"name": "A"}, {"id": "c1", "name": "B", "importance": "huge"}, {"x": 1}],
                                         "practice": [{"concept": "zz", "question": "q"}, {"question": "free"}]})
    assert [c["id"] for c in cs] == ["c1", "c2"] and cs[1]["importance"] == "core" and cs[0]["queries"] == ["A"]
    assert all(c["in_scope"] and c["skills"] == [] for c in cs)
    assert [p["question"] for p in practice] == ["free"] and gaps == ""
    with pytest.raises(RuntimeError):
        clean_concepts({"concepts": []})
    s = Scope.from_json({"weeks": ["3", 1, "x", 3], "minutes": 5, "los": [2], "unknown": 1})
    assert (s.weeks, s.minutes, s.los, s.extras) == ([1, 3], 15, [2], False)


def test_the_focus_decides_what_is_required():
    raw = {"concepts": [
        {"id": "a", "name": "Row reduction", "los": [1], "in_scope": True, "skills": ["x", "x", "y"]},
        {"id": "b", "name": "Network flow", "los": [4], "in_scope": True},            # another outcome: an extra
        {"id": "c", "name": "Matrix product", "los": [4], "importance": "prerequisite"},  # the focus builds on it
        {"id": "d", "name": "Leontief models", "los": [], "in_scope": False}]}          # the AI's call
    cs, _, _ = clean_concepts(raw, Scope(focus="Quiz 1", los=[1, 2]))
    assert {c["id"]: c["in_scope"] for c in cs} == {"a": True, "b": False, "c": True, "d": False}
    assert cs[0]["skills"] == ["x", "y"]
    cs, _, _ = clean_concepts(raw, Scope(weeks=[1, 2]))                  # no focus: the whole weeks
    assert all(c["in_scope"] for c in cs)
    cs, _, _ = clean_concepts({"concepts": [{"name": "A", "los": [5]}]}, Scope(focus="Quiz 1", los=[1]))
    assert cs[0]["in_scope"]                                             # never an empty plan


def test_parse_json_finds_the_object():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('Here it is: {"a": [1, 2]} hope it helps') == {"a": [1, 2]}
    assert parse_json("no json") is None


def test_exam_review_sheet(env):
    seen = []

    def write(system, content, model="sonnet", effort="low", cancel=None, on_text=None):
        seen.append((model, content[0]["text"]))
        on_text("## What it covers\n" + "x" * 50)
        return {"text": "## What it covers\nQuiz 2: LO3.\n\n## Practice\n1. Q?\n\n> **Solution.** 42\n" + "More. " * 60,
                "usage": {"input_tokens": 10, "output_tokens": 20}, "model": model}

    runner = PlanRunner(env.svc, ask=fake_ask([]), ask_text=write)
    pid = runner.create("c", {"kind": "prep", "weeks": [1], "focus": "Quiz 2"})
    runner.start(pid)
    assert wait_done(env.lib, pid)["status"] == "done"
    r = env.lib.plan(pid)["result"]
    assert r["kind"] == "prep" and r["title"] == "Quiz 2 review sheet" and "## Practice" in r["markdown"]
    assert seen[0][1].startswith("TASK: Write an exam review sheet") and "[p.1] Bayes theorem" in seen[0][1]
    assert env.calls == {"search": 0, "inspect": 0}
    text = next((env.tmp / "out").rglob("*review sheet.md")).read_text(encoding="utf-8")
    assert text.startswith("# Quiz 2 review sheet") and "**Solution.** 42" in text


def test_skills_only_in_optional_videos_are_not_called_written():
    from studia.plans import Scope, completeness

    def cov(*how):
        return [{"name": "Bayes", "in_scope": True, "state": "verified", "items": [],
                 "skills": [{"text": f"skill {i}", "how": h} for i, h in enumerate(how)]}]

    cp = completeness(cov("video", "optional", "optional"), Scope(focus="Quiz 2"), "Quiz 2", 30)
    assert cp["level"] == "optional" and cp["complete"]
    assert cp["headline"] == ("Covered for Quiz 2, but partly only in optional videos: of the 3 skills found in your "
                              "material, 1 in checked video parts, 2 only in optional videos.")
    assert completeness(cov("video", "supplement", "optional"), Scope(focus="Quiz 2"), "Quiz 2", 30)["level"] == "written"


def test_a_plan_sends_its_reasoning_levels(env):
    from studia import appconfig

    appconfig.update({"ai_provider": "claude"})
    sent = []

    def ask(system, prompt, model="sonnet", cancel=None, effort="", **kw):
        sent.append((prompt.split(":", 1)[1].split()[0], effort))
        return fake_ask([])(system, prompt, model)

    runner = PlanRunner(env.svc, ask=ask)
    pid = runner.create("c", {"weeks": [1], "effort": "high", "verify_effort": "medium"})
    runner.start(pid)
    assert wait_done(env.lib, pid)["status"] == "done"
    assert {e for w, e in sent if w == "For"} == {"medium"}                     # checking the videos
    assert {e for w, e in sent if w != "For"} == {"high"} and len(sent) >= 3    # the concepts and the plan
    est = plans.estimate(env.lib, env.svc.settings, "c", {"weeks": [1], "effort": "xhigh"})
    assert (est["effort"], est["verify_effort"]) == ("xhigh", "low") and est["verify_requests"] >= 1


def test_the_videos_language_and_the_channel_lists_in_the_pipeline(env, monkeypatch):
    from studia import ytprefs

    ytprefs.set_lang("both")                                    # Settings: English and Turkish videos
    ytprefs.set_channel({"id": "UC" + "k" * 22, "name": "Khan Academy"}, "block")
    ytprefs.set_channel({"id": "UC" + "p" * 22, "name": "Preferred channel"}, "allow")
    monkeypatch.setitem(VIDEOS, "v1", dc.replace(VIDEOS["v1"], language="hi", caption_lang="hi"))   # spoken Hindi
    asked, base = [], fake_ask([])

    def ask(system, prompt, model="sonnet", cancel=None, **kw):
        asked.append(prompt)
        raw, usage = base(system, prompt, model, cancel, **kw)
        if prompt.startswith("TASK: For each concept, judge"):
            raw = {"verdicts": [*raw["verdicts"], {"concept": "c1", "video": "own00000000", "coverage": "partial",
                                                   "skills": [1], "start": 0, "end": 400, "quality": 4}]}
        if prompt.startswith("TASK: Build the final video study plan"):
            raw = {**raw, "items": [*raw["items"], {"n": 5, "video": "own00000000", "role": "required", "concepts": ["c1"]}]}
        return raw, usage

    runner = PlanRunner(env.svc, ask=ask)
    pid = runner.create("c", {"weeks": [1], "focus": "Quiz 2", "los": [3]})
    runner.start(pid)
    assert wait_done(env.lib, pid)["status"] == "done"
    full = env.lib.plan(pid)
    assert full["scope"]["video_lang"] == "both" and env.calls["langs"] == ("en", "tr")
    assert "3 YouTube searches" in asked[0] and "the same in Turkish" in asked[0]
    # the preferred channel is searched itself, with each topic's first query
    assert env.calls["channel_searches"] == [("UC" + "p" * 22, "bayes theorem example"),
                                            ("UC" + "p" * 22, "conditional probability")]
    cands = full["data"]["search"]
    assert cands["c1"][0]["id"] == "own00000000"                                   # preferred: first
    assert all(h["channel"] != "Khan Academy" for hs in cands.values() for h in hs)  # blocked (by name: no id known)
    vids, excerpts = full["data"]["inspect"]["videos"], full["data"]["inspect"]["excerpts"]
    assert vids["v1"]["wrong_lang"] and all("v1" not in per for per in excerpts.values())
    assemble = next(q for q in asked if q.startswith("TASK: Build the final video study plan"))
    assert "a channel the student prefers" in assemble and "Khan Academy" not in assemble and '- v1 "' not in assemble
    items = full["result"]["items"]
    assert [it["video"] for it in items] == ["own00000000"]                        # v2 (blocked), v1 (Hindi) left out
    assert items[0]["channel_avatar"] == "https://yt3.ggpht.com/p=s88" and items[0]["like_count"] == 40
    assert items[0]["weeks"] == [1] and env.calls["channels"] == ["UC" + "p" * 22]


def test_a_video_in_another_language_is_replaced_by_the_next_candidate(env, monkeypatch):
    monkeypatch.setattr(plans, "INSPECT", 1)
    monkeypatch.setitem(VIDEOS, "v1", dc.replace(VIDEOS["v1"], language="hi", caption_lang="hi"))
    runner = PlanRunner(env.svc, ask=fake_ask([]))
    pid = runner.create("c", {"weeks": [1], "focus": "Quiz 2", "los": [3]})
    runner.start(pid)
    assert wait_done(env.lib, pid)["status"] == "done"
    data = env.lib.plan(pid)["data"]
    assert env.calls["inspect"] == 2 and data["inspect"]["videos"]["v1"]["wrong_lang"]
    assert {c: list(per) for c, per in data["inspect"]["excerpts"].items()} == {"c1": ["v2"], "c2": ["v2"]}
