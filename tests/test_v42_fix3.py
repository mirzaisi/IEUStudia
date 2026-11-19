"""4.2.0's fixes from the last two real-app journeys (v42-fix3), with fakes, no network, no AI: a multiple-choice
question whose options differ only in case (f(2) and F(2)) is kept, and an answer given as an option's text picks that
option; a final's Exam room doesn't list, under "Stressed in class", a remark about an earlier quiz or exam."""

from studia import examroom, quizzes, stressed
from test_quizzes import FACTS
from test_v42_qroom import _said_course, _wk


# ------------------------------------------------------------------ f(2) is not F(2)
def test_options_that_differ_only_in_case_are_four_options():
    """A probability course's classic: f(2) (the density) against F(2) (the distribution). The question was dropped
    because the options were compared without case (3 different), and an answer given as the option's text picked the
    first option that matched without case."""
    raw = {"questions": [
        {"type": "mc", "question": "Which gives P(X <= 2) for a continuous X?",
         "options": ["$f(2)$", "$F(2)$", "$1-F(2)$", "$0$"], "answer": "B"},
        {"type": "mc", "question": "Which gives P(a < X <= b)?",
         "options": ["f(b)-f(a)", "F(b)-F(a)", "F(a)-F(b)", "f(a)+f(b)"], "answer": "F(b)-F(a)"},
        {"type": "mc", "question": "Which is the pdf of X at 2?",
         "options": ["F(2)", "f(2)", "F'(0)", "1"], "answer": "f(2)"},
        {"type": "mc", "question": "Which one is the CDF?", "options": ["cdf", "CDF", "pdf", "PDF"], "answer": "CDF"}]}
    got = quizzes.clean_quiz(raw, FACTS)["questions"]
    assert len(got) == 4
    assert [q["options"][q["answer"]] for q in got] == ["$F(2)$", "F(b)-F(a)", "f(2)", "CDF"]


def test_two_questions_that_differ_only_in_case_are_both_kept_and_the_same_one_twice_is_not():
    q = {"type": "short", "answer": "It is the area under the density up to 2."}
    raw = {"questions": [{**q, "question": "What does F(2) mean for X?"}, {**q, "question": "What does f(2) mean for X?"},
                         {**q, "question": "What  does F(2) mean for X?"}]}
    got = quizzes.clean_quiz(raw, FACTS)["questions"]
    assert [x["q"] for x in got] == ["What does F(2) mean for X?", "What does f(2) mean for X?"]


def test_an_answer_written_without_case_still_finds_its_option():
    raw = {"questions": [{"type": "mc", "question": "Which rule turns P(B|A) into P(A|B)?",
                          "options": ["Bayes' rule", "The product rule", "Total probability", "Independence"],
                          "answer": "bayes' RULE"}]}
    assert quizzes.clean_quiz(raw, FACTS)["questions"][0]["answer"] == 0


# ------------------------------------------------------------------ a final's room: no earlier exam's remark
def test_a_final_s_room_leaves_out_remarks_stressed_about_an_earlier_quiz_or_exam(monkeypatch):
    """MATH 240's Final room listed, as "About the exam", the lecturer's coverage remark for the midterm ("These all
    topics are included in the exam … up to week 6"), and a "This will be on the quiz" remark (a quiz long over)."""
    d = _said_course()
    for w in d["weeks"]:
        w["days"] = [{"date": _wk(w["no"], 2)}]

    def at(n, sid, kind, text, t):
        return {"id": sid, "kind": kind, "text": text, "lang": "en", "day_key": f"c|{_wk(n, 2)}|lecture",
                "day": _wk(n, 2), "part": 1, "t": t}

    moments = [at(7, "mid", "exam", "These all topics are included in the exam. Starting from week one up to week 6.",
                  425.0),                                              # (the heads-up's "the exam" before the midterm)
               at(4, "quiz", "exam", "This will be on the quiz, so practise it.", 900.0),   # (no heads-up item)
               at(12, "fin", "exam", "This will be in the exam, learn it.", 725.0),
               at(13, "fin2", "exam", "This kind of question is in the exam.", 1500.0),
               at(5, "plain", "stressed", "The memoryless property is very important.", 300.0)]
    monkeypatch.setattr(stressed, "course_items", lambda lib, cid: moments)
    monkeypatch.setattr(stressed, "ranked", lambda items, nos, day_week: [m for m in items if day_week.get(m["day"]) in nos])
    every = {e["key"]: e for e in quizzes.of(d)}
    final = every["syllabus:Final Exam:16"]
    final = {**final, "weeks": list(range(1, 17))}
    got = [m["id"] for m in examroom.stressed_moments(None, "c", d, final, examroom.said(d, final))]
    assert got == ["fin2", "plain"]               # ("fin" is under what the lecturer said; the earlier ones are gone)
    midterm = {**every["midterm:0"], "weeks": list(range(1, 8))}
    got = [m["id"] for m in examroom.stressed_moments(None, "c", d, midterm, examroom.said(d, midterm))]
    assert got == ["plain"]                       # (its own remark is under what was said; the quiz's is gone)
    quiz3 = {**every["quiz:3"], "weeks": [12, 13]}
    got = [m["id"] for m in examroom.stressed_moments(None, "c", d, quiz3, examroom.said(d, quiz3))]
    assert got == ["fin", "fin2"]                 # about the final, still to come: kept
