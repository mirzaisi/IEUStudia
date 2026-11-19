"""The Practice tab checked in the real app (4.2): a lecture page's "Practise N cards" always opens that lecture's
cards (past the day's new-card limit; all learnt and not due: a run that leaves the schedule alone), N counts what the
run holds (its cards the deck keeps under another lecture, and the quiz questions missed on it), cards set aside are
said so; and a try saved before 4.2 (no typed text, no marks) still reads. Through api.Api with real services; no
AI, no network."""

import datetime as dt

import pytest

from studia import cards, practice, store
from studia.coursesite import Unit
from studia.domain import Course, Recording, Segment, Transcript
from studia.plans import Scope

NOTES1 = ("Q: What is $P(A\\mid B)$?\nA: $P(A\\cap B)/P(B)$ (Part 1, 00:30)\n\n"
          "Q: What does conditioning do?\nA: Restricts the sample space.\n\n"
          "Q: What is the complement rule?\nA: $P(A^c) = 1 - P(A)$.\n")
NOTES2 = ("Q: What is a random variable?\nA: A function from outcomes to numbers.\n\n"
          "Q: What is a PMF?\nA: The probability of each value.\n")
NOTES3 = ("Q: What does conditioning do?\nA: It restricts the sample space to B.\n\n"      # (lecture 1's notes have it)
          "Q: What does Bayes' theorem give?\nA: $P(A\\mid B)$ from $P(B\\mid A)$.\n")


def _lecture(lib, rid, start):
    rec = Recording(delivery_id=rid, course_id="c", folder_id="f", folder_path="", name="n", start=start,
                    duration_s=3000, viewer_url="v")
    lib.merge_recordings("c", [rec], first_sync=False)
    tr = Transcript(delivery_id=rid, segments=[Segment(0, 6, "Today some probability.", "en", 0.9),
                                               Segment(30, 36, "More probability here.", "en", 0.9)],
                    duration_s=3000, speech_s=12, language_seconds={"en": 12}, model="m", device="cpu",
                    engine_version="t", created_at="2026-09-23T12:00:00")
    lib.save_transcript(tr, store.save_transcript(tr))
    lib.set_status(rid, "done")
    return lib.get_recording(rid)


@pytest.fixture
def api(tmp_path):
    from studia.api import Api
    from studia.outputs.writer import course_dir, day_stem
    from studia.services import Services

    a = Api(Services())
    a._push = (events := []).append
    lib = a._s.lib
    lib.upsert_courses([Course(id="c", title="2026-1 MATH 240 P 1", code="MATH 240", name="Probability", term="T")])
    lib.merge_units("c", [Unit("u1", 0, "Week 1", dt.date(2026, 9, 21), dt.date(2026, 9, 27), "", []),
                          Unit("u2", 1, "Week 2", dt.date(2026, 9, 28), dt.date(2026, 10, 4), "", [])], first_sync=True)
    course = lib.get_course("c")
    a.days = []
    for rid, start, notes in (("d1", dt.datetime(2026, 9, 23, 9), NOTES1), ("d2", dt.datetime(2026, 9, 30, 9), NOTES2),
                              ("d3", dt.datetime(2026, 9, 25, 9), NOTES3)):
        rec = _lecture(lib, rid, start)
        path = a._s._notes_path(course_dir(a._s.output_root, course, lib.courses(True)),
                                day_stem(course, rec.start.date(), rec.kind), "flashcards")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(notes, encoding="utf-8")
        a.days.append(rec.day_key)
    return a


def test_a_lecture_s_cards_open_past_the_day_s_new_card_limit(api):
    d1, d2, _ = api.days
    api._s.settings.cards_per_day = 2
    week2 = api.cards_session("c", [2])                       # the day's two new cards: lecture 2's
    assert len(week2["cards"]) == 2
    for c in week2["cards"]:
        assert api.cards_grade("c", c["id"], "good")["ok"]
    whole = api.cards_session("c")["cards"]
    assert {c["id"] for c in whole} == {c["id"] for c in week2["cards"]}     # no new card left today for the deck
    first = api.cards_session("c", [], [d1])                # but a lecture's page opens all of its own
    assert len(first["cards"]) == 3 and first["total"] == 3 and not first["cram"]
    assert all(c["state"] is None and c["next"] for c in first["cards"])
    # its page's "Practise N cards" counts what that run holds
    assert api._lecture_cards(d1) == 3
    notes = api.get_day(d1)["day"]["notes"]
    assert [(n["kind"], n["practise"], len(n["cards"])) for n in notes] == [("flashcards", 3, 3)]


def test_a_lecture_with_nothing_due_opens_its_cards_without_changing_their_schedule(api):
    d1 = api.days[0]
    for c in api.cards_session("c", [], [d1])["cards"]:
        api.cards_grade("c", c["id"], "easy")
    before = cards.states(api._s.lib, "c")
    again = api.cards_session("c", [], [d1])
    assert again["cram"] and len(again["cards"]) == 3 and all(c["cram"] and c["next"] == {} for c in again["cards"])
    assert api._lecture_cards(d1) == 3
    for c in again["cards"]:
        assert api.cards_grade("c", c["id"], "again", True)["ok"]
    assert cards.states(api._s.lib, "c") == before
    assert d1 not in {c["source"].get("day_key") for c in api.cards_session("c")["cards"]}   # (the deck's: not due)


def test_a_lecture_s_run_holds_its_cards_kept_under_another_lecture_and_the_questions_missed_on_it(api):
    lib, (d1, _, d3) = api._s.lib, api.days
    deck = api._deck("c")
    shared = [c for c in deck if c["q"] == "What does conditioning do?"]
    assert len(shared) == 1 and shared[0]["source"]["day_key"] == d1            # the deck keeps it once
    run = api.cards_session("c", [], [d3])
    assert sorted(c["q"] for c in run["cards"]) == ["What does Bayes' theorem give?", "What does conditioning do?"]
    # a question missed in a quiz, taught in lecture 3: its card joins lecture 3's run and its count
    src = {"kind": "lecture", "ref": "L1", "day_key": d3, "date": "2026-09-25", "week": 1, "part": 1, "t": 30.0,
           "ts": "00:30"}
    lib.create_plan("qz", "c", Scope(kind="quiz", weeks=[1], questions=5).to_json(), "Basics")
    lib.update_plan("qz", status="done", result={"kind": "quiz", "title": "Basics", "questions": [
        {"n": 1, "type": "short", "q": "Why is $P(B)$ in the denominator?", "model": "It renormalises.",
         "points": ["renormalises"], "source": src, "los": [], "topic": "Conditional probability"}]})
    assert api.quiz_attempt("qz", {"answers": [{"n": 1, "got": 0, "text": "no idea", "mark": "self"}]})["carded"]
    run = api.cards_session("c", [], [d3])
    assert len(run["cards"]) == 2 and run["total"] == 3     # (the missed one is due tomorrow: in the run then)
    tomorrow = cards.study_day(dt.datetime.now())[1] + dt.timedelta(hours=2)
    later = cards.session(lib, api._deck("c"), "c", tomorrow, days={d3}, fronts=cards.lecture_fronts(api._s, [d3]))
    assert "Why is $P(B)$ in the denominator?" in [c["q"] for c in later]


def test_cards_set_aside_are_said_so_and_left_out(api):
    d2 = api.days[1]
    for c in api.cards_session("c", [], [d2])["cards"]:
        assert api.cards_edit("c", c["id"], {"off": "aside"})["ok"]
    run = api.cards_session("c", [], [d2])
    assert run["cards"] == [] and run["total"] == 0 and run["aside"] == 2
    assert api._lecture_cards(d2) == 0
    assert cards.picked(api._deck("c"), days={d2}, aside=True) and not cards.picked(api._deck("c"), days={d2})


def test_a_try_saved_before_4_2_still_reads(api):
    """4.1.2's page kept {n, got, chose, skipped}: no text, no mark. The Mistakes list, mastery, the cards and a
    changed mark work on it."""
    lib, d1 = api._s.lib, api.days[0]
    lib.create_plan("old", "c", Scope(kind="quiz", weeks=[1], questions=5).to_json(), "Old quiz")
    lib.update_plan("old", status="done", result={"kind": "quiz", "title": "Old quiz", "questions": [
        {"n": 1, "type": "mc", "q": "Which is $P(A^c)$?", "options": ["$1-P(A)$", "$P(A)$", "0", "1"], "answer": 0,
         "why": "Complement.", "wrong": ["", "No.", "No.", "No."], "source": None, "los": [1], "topic": "Complement"},
        {"n": 2, "type": "short", "q": "What does conditioning on $B$ do?", "model": "Restricts the sample space.",
         "points": ["restricts"], "source": None, "los": [1], "topic": "Conditioning"}]})
    import json

    with lib._lock:                                           # as 4.1.2 saved it, straight into the table
        lib.db.execute("INSERT INTO quiz_attempts(id, plan_id, course_id, started_at, finished_at, score, total, "
                       "answers, only) VALUES(?,?,?,?,?,?,?,?,?)",
                       ("t1", "old", "c", "2026-11-10T10:00:00", "2026-11-10T10:05:00", 0.5, 2,
                        json.dumps([{"n": 1, "got": 0, "chose": 2, "skipped": False},
                                    {"n": 2, "got": 0.5, "chose": None, "skipped": False}]), "[]"))
        lib.db.commit()
    practice._bump()
    tab = api.practice_course("c")
    assert tab["ok"] and sorted(m["n"] for m in tab["mistakes"]) == [1, 2]
    short = next(m for m in tab["mistakes"] if m["n"] == 2)
    assert "text" not in short["answer"] and short["answer"]["attempt"] == "t1"
    assert tab["weak"]["attempts"] == 1 and tab["deck"]["quiz"] == 2
    changed = api.quiz_mark_set("old", "t1", 2, 1)
    assert changed["ok"] and [a.get("mark") for a in changed["attempt"]["answers"]] == ["app", "self"]
    assert changed["attempt"]["score"] == 1
    assert api.quiz_check_number("old", 2, "3")["verdict"] == "unknown"
    assert api.cards_session("c", [], [d1])["ok"]
