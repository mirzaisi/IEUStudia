"""The Practice tools through the app's own API (api.Api with real services, the AI replaced by fakes): a course's
Practice tab (its deck from the lectures' Flashcards notes, quizzes, what the lecturer stressed), studying and
grading cards, the Anki export, a file page as a source, a quiz's tries and its deletion, and the AI's "Know this
for the exam" list kept with its citations."""

import datetime as dt
import json
import time
import zipfile

import pytest

from studia import store
from studia.coursesite import Resource, Unit
from studia.domain import Course, Recording, Segment, Transcript
from studia.plans import Scope

NOTES = r"""Q: What is $P(A\mid B)$?
A: $P(A\cap B)/P(B)$ (Part 1, 00:30)

Q: When are two events independent?
A: When $P(A\cap B)=P(A)P(B)$ (Part 1, 20:00)
"""


@pytest.fixture
def api(tmp_path):
    from studia.api import Api
    from studia.outputs.writer import course_dir, day_stem
    from studia.services import Services

    a = Api(Services())
    a._push = (events := []).append
    a.events = events
    lib = a._s.lib
    lib.upsert_courses([Course(id="c", title="2026-1 MATH 240 P 1", code="MATH 240", name="Probability", term="T")])
    lib.merge_units("c", [Unit("u1", 0, "Week 1", dt.date(2026, 9, 21), dt.date(2026, 9, 27), "",
                               [Resource("r1", "Week01", "Week01.pdf", "pdf", "pdf", "u", "d")])], first_sync=True)
    lib.set_material_file("r1", str(tmp_path / "Week01.pdf"), 10, "u")
    (tmp_path / "Week01.pdf").write_bytes(b"%PDF")
    lib.set_material_text("r1", ["Conditional probability", "Bayes theorem and the total probability rule"])
    rec = Recording(delivery_id="d1", course_id="c", folder_id="f", folder_path="", name="n",
                    start=dt.datetime(2026, 9, 23, 9, 0), duration_s=3000, viewer_url="v")
    lib.merge_recordings("c", [rec], first_sync=True)
    tr = Transcript(delivery_id="d1", segments=[
        Segment(0, 5, "Today conditional probability.", "en", 0.9),
        Segment(30, 36, "Be careful: P of A given B is not P of B given A, students always mix them up.", "en", 0.9)],
        duration_s=3000, speech_s=11, language_seconds={"en": 11}, model="m", device="cpu", engine_version="t",
        created_at="2026-09-23T12:00:00")
    lib.save_transcript(tr, store.save_transcript(tr))
    lib.set_status("d1", "done")
    course = lib.get_course("c")
    path = a._s._notes_path(course_dir(a._s.output_root, course, lib.courses(True)),
                            day_stem(course, rec.start.date(), rec.kind), "flashcards")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(NOTES, encoding="utf-8")
    return a


def test_the_practice_tab_studying_and_the_anki_export(api, tmp_path):
    r = api.practice_course("c")
    assert r["ok"], r
    assert r["deck"]["total"] == 2 and r["deck"]["today"] == 2 and r["deck"]["weeks"] == [1]
    st = r["stressed"]
    assert st["total"] == 1 and st["weeks"][0]["no"] == 1 and "mix them up" in st["weeks"][0]["items"][0]["text"]
    s = api.cards_session("c")
    assert [c["q"] for c in s["cards"]][0] == r"What is $P(A\mid B)$?"            # near the stressed moment: first
    g = api.cards_grade("c", s["cards"][0]["id"], "easy")
    assert g["ok"] and g["state"]["step"] == -1 and set(g["state"]["next"]) == {"again", "hard", "good", "easy"}
    assert api.cards_grade("c", s["cards"][0]["id"], "maybe")["ok"] is False
    assert api.practice_course("c")["deck"]["today"] == 1
    summary = api.practice_summary(True)
    assert summary["due"] == 1 and summary["courses"][0]["stressed"] == 1
    opened = []
    api._open_here = lambda path, **k: opened.append(path) or {"ok": True}
    out = api.cards_export("c", "open")
    assert out["ok"] and out["cards"] == 2 and opened and opened[0].suffix == ".apkg"
    assert opened[0].parent.name == "Flashcards" and zipfile.ZipFile(opened[0]).namelist() == ["collection.anki2", "media"]
    assert api.cards_export("c", "open", [7])["ok"] is False                    # no cards in week 7
    page = api.practice_source("c", "r1", 2)
    assert page["ok"] and page["text"].startswith("Bayes theorem") and page["pages"] == 2 and page["picture"] == ""
    assert api.practice_source("x", "r1", 1)["ok"] is False


def test_a_quiz_s_tries_and_deleting_it(api):
    lib = api._s.lib
    lib.create_plan("q1", "c", Scope(kind="quiz", weeks=[1], questions=5).to_json(), "Bayes")
    lib.update_plan("q1", status="done", result={"kind": "quiz", "questions": [{"n": 1, "type": "mc"}]})
    got = api.quiz_get("q1")
    assert got["ok"] and got["quiz"]["result"]["questions"] and "data" not in got["quiz"]
    api.quiz_attempt("q1", {"answers": [{"n": 1, "got": 1}, {"n": 2, "got": 0}]})
    lib.create_plan("q2", "c", Scope(kind="quiz", weeks=[1], questions=10).to_json(), "")
    lib.update_plan("q2", status="running")
    tab = api.practice_course("c")
    q = next(x for x in tab["quizzes"] if x["id"] == "q1")
    # 5 asked, 1 kept (the checks drop unusable or repeated ones): the list said "5 questions", Start "1 question"
    assert q["tries"] == 1 and q["best"]["score"] == 1 and q["questions"] == 1
    assert next(x for x in tab["quizzes"] if x["id"] == "q2")["questions"] == 10        # being written: as asked
    api.quiz_delete("q2")
    assert all(p["id"] != "q1" for p in api.plan_list()["plans"])
    assert api.quiz_delete("q1")["ok"] and api.quiz_get("q1")["ok"] is False
    with lib._lock:
        assert lib.db.execute("SELECT COUNT(*) FROM quiz_attempts").fetchone()[0] == 0
    assert api.quiz_get("nope")["ok"] is False


def test_know_this_for_the_exam_with_a_fake_ai(api, monkeypatch):
    from studia import ai, practice

    monkeypatch.setattr(practice, "_ai_ready", lambda: None)
    asked = []

    def ask_json(system, prompt, **kw):
        asked.append(prompt)
        return {"items": [{"point": "P(A|B) is not P(B|A).", "why": "a common mistake", "cites": ["S1", "S5"]},
                          {"point": "Invented", "cites": []}]}, {"usage": {"input_tokens": 10}}

    monkeypatch.setattr(ai, "ask_json", ask_json)
    assert api.stressed_summarize("c")["ok"]
    for _ in range(200):
        if any(e.get("phase") in ("done", "error") for e in api.events):
            break
        time.sleep(0.02)
    assert [e["phase"] for e in api.events if e["type"] == "stressed"] == ["start", "done"], api.events
    assert "S1 (stressed) [2026-09-23 (week 1) part 1 00:30]" in asked[0] and "Write in English" in asked[0]
    sm = api.practice_course("c")["stressed"]["summary"]
    assert [x["point"] for x in sm["items"]] == ["P(A|B) is not P(B|A)."] and len(sm["items"][0]["cites"]) == 1
    assert sm["new"] == 0 and "from" not in sm
    assert json.loads(api._s.lib.get_meta("stressed-ai:c"))["items"][0]["cites"] == sm["items"][0]["cites"]
