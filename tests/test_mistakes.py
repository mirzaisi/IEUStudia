"""The mistake loop (4.2) through the app's own API (api.Api with real services; no AI): a try keeps what was chosen
or typed and who marked it, its missed questions join the course's cards (due the next day, a multiple choice asked
again with its options reordered and marked by the app), the Mistakes list until a retry gets it right or its card is
spaced out, "Remove this question", a card edited, set aside or deleted, where a question was taught (the slide on
screen, the transcript around it, a clip), how each learning outcome went (counts, the minimum before "shaky"), and a
weak-spot quiz: its material narrowed to where the missed questions come from, both prompt blocks, and no question
asked again."""

import datetime as dt
import json

import pytest

from studia import cards, practice, quizzes, store
from studia.coursesite import Resource, Unit
from studia.domain import Course, Recording, Segment, Transcript
from studia.plans import Scope
from studia.slides import Slide

PAGES = ["Week 1: conditional probability and Bayes, the plan of the lecture",
         "Sample spaces and events: what an outcome is, unions and intersections of events",
         "Conditional probability: P(A|B) = P(A and B)/P(B), defined only when P(B) is above zero",
         "Worked example of conditional probability with two dice, counting the favourable outcomes",
         "Independence: A and B are independent when P(A and B) = P(A)P(B), not the same as disjoint",
         "Homework problems on counting, permutations and combinations for next week"]
SAID = [(0, "Good morning, today we start conditional probability."),
        (60, "Remember the sample space from last week, every outcome counts once."),
        (600, "Independence is not the same as disjoint events, students always confuse the two."),
        (1200, "Now the total probability rule, which gives the denominator of Bayes theorem."),
        (2400, "For next week read the chapter on random variables and their distributions.")]


@pytest.fixture
def api(tmp_path):
    from studia.api import Api
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
    lib.set_material_text("r1", PAGES)
    lib.set_course_info("c", syllabus={"outcomes": ["use conditional probability", "tell independent events",
                                                    "use random variables"], "language": "English",
                                       "weeks": [{"no": 1, "subject": "Conditional probability", "preparation": ""}]})
    rec = Recording(delivery_id="d1", course_id="c", folder_id="f", folder_path="", name="n",
                    start=dt.datetime(2026, 9, 23, 9, 0), duration_s=3000, viewer_url="v")
    lib.merge_recordings("c", [rec], first_sync=True)
    tr = Transcript(delivery_id="d1", segments=[Segment(t, t + 6, text, "en", 0.9) for t, text in SAID],
                    duration_s=3000, speech_s=30, language_seconds={"en": 30}, model="m", device="cpu",
                    engine_version="t", created_at="2026-09-23T12:00:00")
    lib.save_transcript(tr, store.save_transcript(tr))
    lib.set_status("d1", "done")
    a.day = lib.get_recording("d1").day_key
    a.tmp = tmp_path
    return a


def file_src(page):
    return {"kind": "file", "ref": "F1", "id": "r1", "name": "Week01.pdf", "page": page, "week": 1}


def lecture_src(day, at):
    return {"kind": "lecture", "ref": "L1", "day_key": day, "date": "2026-09-23", "week": 1, "part": 1, "t": at,
            "ts": f"{int(at // 60):02d}:{int(at % 60):02d}"}


def questions(day):
    return [
        {"n": 1, "type": "mc", "q": "What is $P(A|B)$?", "options": ["P(A and B)/P(B)", "P(A)P(B)", "P(B)/P(A)", "1"],
         "answer": 0, "why": "It is the definition.", "wrong": ["", "Only for independent events.", "Upside down.", "Never."],
         "source": file_src(3), "los": [1], "topic": "Conditional probability", "level": "easy", "stressed": []},
        {"n": 2, "type": "mc", "q": "When are A and B independent?", "options": ["When disjoint", "When P(AB)=P(A)P(B)",
         "Always", "Never"], "answer": 1, "why": "That is what independence means.",
         "wrong": ["Disjoint events are dependent.", "", "Not always.", "Sometimes they are."],
         "source": lecture_src(day, 600.0), "los": [2], "topic": "Independence", "level": "medium", "stressed": ["s1"]},
        {"n": 3, "type": "short", "q": "Why does Bayes' theorem need the total probability rule?",
         "model": "It gives the denominator.", "points": ["the denominator P(A)"], "why": "", "source": None,
         "los": [2], "topic": "Bayes' theorem", "level": "medium", "stressed": []},
        {"n": 4, "type": "mc", "q": "What does Bayes' theorem turn around?", "options": ["Sums", "Products",
         "The condition", "Nothing"], "answer": 2, "why": "P(A|B) from P(B|A).", "wrong": ["No.", "No.", "", "No."],
         "source": file_src(3), "los": [2], "topic": "Bayes theorem", "level": "easy", "stressed": []}]


def make_quiz(lib, pid, day, qs=None):
    lib.create_plan(pid, "c", Scope(kind="quiz", weeks=[1], questions=4).to_json(), "Probability basics")
    lib.update_plan(pid, status="done", result={"kind": "quiz", "title": "Probability basics",
                                                "questions": qs or questions(day)})


FIRST = [{"n": 1, "got": 0, "chose": 2, "mark": "app"}, {"n": 2, "got": 0, "chose": 0, "mark": "app"},
         {"n": 3, "got": 0.5, "text": "it normalises", "mark": "self"}, {"n": 4, "got": 1, "chose": 2, "mark": "app"}]


def tomorrow():
    return cards.study_day(dt.datetime.now())[1] + dt.timedelta(hours=2)


# ------------------------------------------------------------------ the try, its cards and the Mistakes
def test_a_try_keeps_its_answers_and_the_missed_questions_join_the_cards(api):
    lib = api._s.lib
    make_quiz(lib, "q1", api.day)
    r = api.quiz_attempt("q1", {"answers": FIRST + [{"n": 5, "got": "1"}]})
    assert r["ok"] and len(r["carded"]) == 3
    kept = r["attempt"]["answers"]
    assert kept[0] == {"n": 1, "got": 0.0, "chose": 2, "skipped": False, "text": "", "mark": "app"}
    assert kept[2]["text"] == "it normalises" and kept[2]["mark"] == "self"
    assert kept[4]["mark"] == "self" and kept[4]["got"] == 1.0            # no mark and no choice: the student's
    st = cards.states(lib, "c")
    first = st[r["carded"][0]]
    assert first["step"] == -1 and first["interval"] == 1 and first["first"] == ""
    assert first["due"] == cards.study_day(dt.datetime.now())[1].isoformat(timespec="seconds")     # tomorrow 04:00
    # in the deck; not due today (the answer was just seen), due tomorrow; new cards today not used up by them
    deck = cards.deck(api._s, "c")
    mine = [c for c in deck if c["origin"] == "quiz"]
    assert [c["q"] for c in mine] == ["When are A and B independent?", "What is $P(A|B)$?",     # (stressed first)
                                      "Why does Bayes' theorem need the total probability rule?"]
    assert mine[1]["a"] == "**P(A and B)/P(B)**\n\nIt is the definition." and mine[1]["week"] == 1
    assert mine[2]["a"] == "It gives the denominator.\n\n- the denominator P(A)" and mine[0]["stressed"]
    assert cards.summary(lib, deck, "c")["due"] == 0 and cards.summary(lib, deck, "c")["quiz"] == 3
    assert cards.summary(lib, deck, "c")["learnt"] == 0           # not "3 cards learnt." before one has come back
    later = cards.session(lib, deck, "c", now=tomorrow())
    mc = next(c for c in later if c["quiz"]["n"] == 2)
    assert sorted(mc["quiz"]["options"]) == sorted(questions(api.day)[1]["options"])
    assert mc["quiz"]["options"][mc["quiz"]["answer"]] == "When P(AB)=P(A)P(B)"
    assert mc["quiz"]["wrong"][mc["quiz"]["options"].index("When disjoint")] == "Disjoint events are dependent."
    # another review, another order (seeded by the reviews so far), always the right one marked right
    cards.grade(lib, "c", mc["id"], "good", tomorrow())
    assert cards.summary(lib, deck, "c", now=tomorrow())["learnt"] == 1
    again = cards._asked_again(mc, {"reps": 7})
    assert again["quiz"]["options"][again["quiz"]["answer"]] == "When P(AB)=P(A)P(B)"
    # an old try (before 4.2: no text, no mark) still reads
    with lib._lock:
        lib.db.execute("INSERT INTO quiz_attempts(id, plan_id, course_id, started_at, finished_at, score, total, answers,"
                       " only) VALUES('old','q1','c','2026-09-01T10:00:00','2026-09-01T10:10:00',1,2,?, '[]')",
                       (json.dumps([{"n": 1, "got": 1, "chose": 0}, {"n": 3, "got": 0}]),))
        lib.db.commit()
    tab = api.practice_course("c")
    assert tab["ok"] and next(q for q in tab["quizzes"] if q["id"] == "q1")["tries"] == 2
    assert practice.answer_mark({"n": 3, "got": 0}, questions(api.day)[2]) == "self"
    assert practice.answer_mark({"n": 1, "got": 1, "chose": 0}) == "app"


def test_the_mistakes_list_until_a_retry_gets_it_right_or_its_card_is_spaced_out(api):
    lib = api._s.lib
    make_quiz(lib, "q1", api.day)
    api.quiz_attempt("q1", {"answers": FIRST})
    again = api.quiz_attempt("q1", {"answers": [{"n": 1, "got": 1, "chose": 0}, {"n": 2, "got": 0, "chose": 3},
                                                {"n": 3, "got": 0, "skipped": True}], "only": [1, 2, 3]})
    assert again["carded"] == []                       # missed again: in the cards already (the page says nothing)
    open_ = api.practice_course("c")["mistakes"]
    assert [(m["n"], m["misses"]) for m in open_] == [(2, 2), (3, 2)]          # 1: right in the retry
    assert open_[0]["answer"]["chose"] == 3 and open_[0]["q"]["source"]["t"] == 600.0 and open_[0]["title"]
    # its card spaced out to 4 days: done
    with lib._lock:
        lib.db.execute("UPDATE card_state SET interval=4.5 WHERE card_id=?", (open_[0]["card_id"],))
        lib.db.commit()
    assert [m["n"] for m in practice.mistakes(lib, "c")] == [3]
    # its source wrong: no replay; then removed: out of the list, the deck, the counts; and back with Undo
    assert api.quiz_question("q1", 3, "not_here")["not_here"] == [3]
    assert practice.mistakes(lib, "c")[0]["q"]["source"] is None and practice.mistakes(lib, "c")[0]["q"]["not_here"]
    r = api.quiz_question("q1", 3, "remove")
    assert r["ok"] and r["removed"] == [3]
    assert practice.mistakes(lib, "c") == []
    assert all(c["quiz"]["n"] != 3 for c in cards.deck(api._s, "c") if c["origin"] == "quiz")
    assert next(q for q in api.practice_course("c")["quizzes"] if q["id"] == "q1")["questions"] == 3
    assert api.quiz_question("q1", 3, "restore")["removed"] == []
    assert [m["n"] for m in practice.mistakes(lib, "c")] == [3]
    assert api.quiz_question("q1", 3, "nonsense")["ok"] is False and api.quiz_question("nope", 1, "remove")["ok"] is False


def test_deleting_the_quiz_takes_its_cards_and_the_setting_turns_them_off(api):
    lib = api._s.lib
    make_quiz(lib, "q1", api.day)
    ids = api.quiz_attempt("q1", {"answers": FIRST})["carded"]
    assert set(ids) <= set(cards.states(lib, "c"))
    api.quiz_delete("q1")
    assert not set(ids) & set(cards.states(lib, "c")) and not [c for c in cards.deck(api._s, "c") if c["origin"] == "quiz"]
    # off in Settings → Practice: nothing joins, and the deck has none
    api.update_settings({"mistake_cards": False})
    make_quiz(lib, "q2", api.day)
    assert api.quiz_attempt("q2", {"answers": FIRST})["carded"] == []
    assert not [c for c in api._deck("c") if c["origin"] == "quiz"]
    api.update_settings({"mistake_cards": True})
    # Undo after a quiz: out of the deck, and not added again when missed again
    make_quiz(lib, "q3", api.day, [dict(q, q=q["q"] + " (again)") for q in questions(api.day)])
    ids = api.quiz_attempt("q3", {"answers": FIRST})["carded"]
    assert api.mistakes_uncard("c", ids)["removed"] == 3
    assert not set(ids) & {c["id"] for c in api._deck("c")}
    assert api.quiz_attempt("q3", {"answers": FIRST})["carded"] == []


def test_a_card_edited_set_aside_and_deleted(api):
    lib = api._s.lib
    make_quiz(lib, "q1", api.day)
    api.quiz_attempt("q1", {"answers": FIRST})
    by_n = {c["quiz"]["n"]: c["id"] for c in api._deck("c") if c["origin"] == "quiz"}
    one, two, three = by_n[1], by_n[2], by_n[3]
    assert api.cards_edit("c", three, {"q": "Why the total probability rule?", "a": "The denominator."})["ok"]
    edited = next(c for c in api._deck("c") if c["id"] == three)
    assert (edited["q"], edited["a"], edited["edited"]) == ("Why the total probability rule?", "The denominator.", True)
    assert api.cards_edit("c", three, {"q": " ", "a": ""})["ok"] is False
    assert api.cards_edit("c", two, {"off": "aside"})["ok"]
    deck = api._deck("c")
    assert cards.summary(lib, deck, "c")["aside"] == 1 and cards.summary(lib, deck, "c")["quiz"] == 2
    assert two not in {c["id"] for c in cards.session(lib, deck, "c", now=tomorrow())}
    assert api.cards_edit("c", one, {"off": "deleted"})["ok"]
    assert one not in {c["id"] for c in api._deck("c")} and one not in cards.states(lib, "c")
    assert api.practice_course("c")["deck"]["aside"] == 1
    assert api.cards_bring_back("c")["back"] == 1 and api.practice_course("c")["deck"]["aside"] == 0
    assert api.cards_edit("c", one, {"off": "maybe"})["ok"] is False
    # the Anki export: the multiple choice with its options on the front
    notes = cards.anki_notes([c for c in api._deck("c") if c["id"] == two], "MATH 240")
    assert notes[0]["front"].endswith("A) When disjoint\nB) When P(AB)=P(A)P(B)\nC) Always\nD) Never")
    assert "from_quiz" in notes[0]["tags"]


# ------------------------------------------------------------------ where it was taught
def test_where_a_question_was_taught_the_slide_the_words_and_a_clip(api):
    shots = []
    for i in (1, 2, 3):
        p = api.tmp / f"s{i}.jpg"
        p.write_bytes(b"\xff\xd8\xff\xd9")
        shots.append(p)
    store.save_slides("d1", [Slide(1, 0, 500, "s1.jpg", str(shots[0])), Slide(2, 500, 700, "s2.jpg", str(shots[1])),
                             Slide(3, 700, 3000, "s3.jpg", str(shots[2]))])
    store.save_screens("d1", {"v": 1, "screens": {"2": {"title": "Independence", "text": "P(AB)=P(A)P(B)", "from": "ocr"}}})
    m = api.practice_moment("c", api.day, 1, 610)
    assert m["ok"] and m["slide"]["n"] == 2 and m["slide"]["title"] == "Independence"
    assert m["slide"]["image"].startswith("file:") and m["slide"]["range"] == "08:20–11:40"
    assert m["clip"]["from"] == 570 and m["clip"]["until"] == 700          # its slide's 200 s: the 150 s around it
    said = [x["text"] for x in m["lines"]]
    assert said[2].startswith("Independence is not") and m["lines"][2]["at"] and len(said) == 5   # 3 before, 3 after
    long_one = api.practice_moment("c", api.day, 1, 2400)
    assert long_one["clip"] == {"from": 2360, "until": 2510, "range": "39:20–41:50"}
    store.save_slides("d1", [])                                            # a board or camera lecture: no slides
    plain = api.practice_moment("c", api.day, 1, 1200)
    assert plain["slide"] is None and (plain["clip"]["from"], plain["clip"]["until"]) == (1180, 1270)
    assert api.practice_moment("x", api.day, 1, 0)["ok"] is False
    assert api.practice_moment("c", "c|2020-01-01|lecture", 1, 0)["ok"] is False


# ------------------------------------------------------------------ how each learning outcome went
def test_mastery_counts_per_learning_outcome_and_topic(api):
    lib = api._s.lib
    make_quiz(lib, "q1", api.day)
    api.quiz_attempt("q1", {"answers": FIRST})
    w = api.practice_weak("c")
    assert w["ok"] and w["attempts"] == 1 and w["answers"] == 4
    lo = {r["no"]: r for r in w["los"]}
    # LO1: 0 of 1, one question only: too few to tell (2 at least); LO2: 1.5 of 3, the self-marked half counts half
    assert (lo[1]["tried"], lo[1]["right"], lo[1]["questions"], lo[1]["status"]) == (1, 0, 1, "few")
    assert (lo[2]["tried"], lo[2]["right"], lo[2]["questions"], lo[2]["quizzes"]) == (3, 1.5, 3, 1)
    assert lo[2]["share"] == round((0.5 * 0.5 + 1) / (1 + 0.5 + 1), 2) and lo[2]["status"] == "shaky"
    assert lo[3]["status"] == "untested" and lo[3]["text"] == "use random variables"
    assert [r["no"] for r in w["los"]] == [2, 1, 3]                       # where points are lost first
    assert {(m["plan_id"], m["n"]) for m in lo[2]["missed"]} == {("q1", 2), ("q1", 3)}
    topics = {r["name"]: r for r in w["topics"]}
    assert topics["Bayes' theorem"]["tried"] == 2 or topics.get("Bayes theorem", {}).get("tried") == 2   # one topic
    assert len(w["topics"]) == 3
    # a missed question's card reviewed: Again counts as a miss
    cid = cards.quiz_card_id("q1", questions(api.day)[1]["q"])
    cards.grade(lib, "c", cid, "again")
    assert {r["no"]: r for r in api.practice_weak("c")["los"]}[2]["tried"] == 4
    # right three times after: still one question, so never "solid" (3 questions at least); three right ones: solid
    for _ in range(3):
        api.quiz_attempt("q1", {"answers": [{"n": 1, "got": 1, "chose": 0, "mark": "app"}], "only": [1]})
    assert {r["no"]: r["status"] for r in practice.mastery(lib, "c")["los"]}[1] == "few"
    qs = [dict(questions(api.day)[0], n=i, q=f"Conditional question {i}?") for i in (1, 2, 3)]
    make_quiz(lib, "q2", api.day, qs)
    api.quiz_attempt("q2", {"answers": [{"n": i, "got": 1, "chose": 0} for i in (1, 2, 3)]})
    m = practice.mastery(lib, "c", outcomes=["a", "b", "c"])
    assert {r["no"]: r["status"] for r in m["los"]}[1] == "solid" and m["quizzes"] == 2
    only = practice.mastery(lib, "c", "Quiz 1", los=[1])
    assert [r["no"] for r in only["los"]] == [1] and only["focus"] == "Quiz 1"


def test_mastery_misses_are_the_latest_answers_not_the_oldest(api):
    """attempts() gives the newest try first, and each answer overwrote the last one kept: the oldest won, so a
    question just missed was left out of the weak spots and one fixed since stayed in them."""
    lib = api._s.lib
    make_quiz(lib, "q1", api.day)
    old = api.quiz_attempt("q1", {"answers": [{"n": 1, "got": 0, "chose": 2}, {"n": 2, "got": 1, "chose": 1}],
                                  "only": [1, 2]})["attempt"]
    hour_ago = (dt.datetime.now() - dt.timedelta(hours=1)).isoformat(timespec="seconds")
    with lib._lock:
        lib.db.execute("UPDATE quiz_attempts SET finished_at=? WHERE id=?", (hour_ago, old["id"]))
        lib.db.commit()
    api.quiz_attempt("q1", {"answers": [{"n": 1, "got": 1, "chose": 0}, {"n": 2, "got": 0, "chose": 0}],
                            "only": [1, 2]})
    missed = {r["no"]: [(x["plan_id"], x["n"]) for x in r["missed"]] for r in practice.mastery(lib, "c")["los"]}
    assert missed[1] == [] and missed[2] == [("q1", 2)]
    w = practice.weak_spots(lib, "c", "Quiz 1", los=[1, 2])
    assert [(x["plan_id"], x["n"]) for x in w["missed"]] == [("q1", 2)]
    # a card review after the try: the review is the latest answer
    cards.grade(lib, "c", cards.quiz_card_id("q1", questions(api.day)[1]["q"]), "good",
                dt.datetime.now() + dt.timedelta(minutes=5))
    missed = {r["no"]: r["missed"] for r in practice.mastery(lib, "c", now=dt.datetime.now() + dt.timedelta(hours=1))["los"]}
    assert missed[2] == []


# ------------------------------------------------------------------ a quiz on the weak spots
def test_a_weak_spot_quiz_reads_only_where_the_missed_questions_come_from(api):
    lib = api._s.lib
    make_quiz(lib, "q1", api.day)
    api.quiz_attempt("q1", {"answers": FIRST})
    api.quiz_attempt("q1", {"answers": [{"n": 4, "got": 1, "chose": 2}], "only": [4]})        # right twice
    sc = practice.weak_scope(lib, "c", {"kind": "quiz", "weak": True, "questions": 5})
    assert sc["files"] == ["r1"] and sc["days"] == [api.day] and sc["weeks"] == [1] and sc["picked"]
    assert sc["avoid"] == ["What does Bayes' theorem turn around?"]
    scope = Scope.from_json(sc)
    assert scope.weak and scope.avoid == sc["avoid"]
    facts = quizzes.gather(lib, api._s.settings, "c", scope)
    pack = facts["pack"]
    assert facts["narrowed"] and "[F1 p.2]" in pack and "[F1 p.3]" in pack and "[F1 p.4]" in pack
    assert "[F1 p.1]" not in pack and "[F1 p.5]" not in pack and "Homework problems" not in pack
    assert "Independence is not the same as disjoint" in pack                # 10:00, where question 2 was taught
    assert "total probability rule" not in pack and "Good morning" not in pack and "random variables and" not in pack
    whole = quizzes.gather(lib, api._s.settings, "c", Scope(kind="quiz", weeks=[1], questions=5))
    assert facts["chars"] < whole["chars"]
    est = quizzes.estimate(lib, api._s.settings, "c", scope)["main_input"]
    assert est < quizzes.estimate(lib, api._s.settings, "c", Scope(kind="quiz", weeks=[1], questions=5))["main_input"]
    prompt = quizzes.write_prompt(scope, facts, 5, quizzes.mix(5, "mixed", False))
    assert "=== THE STUDENT'S WEAK SPOTS ===" in prompt and "=== ALREADY ASKED, DO NOT REPEAT ===" in prompt
    assert "They chose: P(B)/P(A) (wrong because: Upside down.) | Right answer: P(A and B)/P(B)" in prompt
    assert "They wrote: it normalises" in prompt and "LO2: tell independent events (" in prompt
    assert "- What does Bayes' theorem turn around?" in prompt and "2. The student's weak spots" in prompt
    assert prompt.index("WEAK SPOTS") < prompt.index("=== COURSE MATERIAL ===")
    # what comes back: a question asked before (or one just missed) is dropped
    raw = {"title": "Weak spots", "questions": [
        {"type": "mc", "question": "What does Bayes' theorem turn around?", "options": ["a", "b", "c", "d"], "answer": "A"},
        {"type": "mc", "question": "When are A and B independent?", "options": ["a", "b", "c", "d"], "answer": "B"},
        {"type": "mc", "question": "Are disjoint events with positive probability independent?",
         "options": ["Yes", "No", "Sometimes", "Only if equal"], "answer": "B", "source": "L1 part 1 10:05"}]}
    quiz = quizzes.clean_quiz(raw, facts, quizzes.source_units(lib, "c", facts))
    assert [q["q"] for q in quiz["questions"]] == ["Are disjoint events with positive probability independent?"]
    assert quiz["questions"][0]["source"]["t"] == 605
    # nothing missed yet: no weak-spot quiz
    api.quiz_delete("q1")
    assert api.quiz_estimate("c", {"weak": True})["ok"] is False


def test_other_quizzes_keep_their_prompt(api):
    facts = quizzes.gather(api._s.lib, api._s.settings, "c", Scope(kind="quiz", weeks=[1], questions=5))
    prompt = quizzes.write_prompt(Scope(kind="quiz"), facts, 5, quizzes.mix(5, "mixed", False))
    assert "WEAK SPOTS" not in prompt and "DO NOT REPEAT" not in prompt and "weak" not in facts
    assert quizzes.weak_blocks({}) == ""
