"""Practice quizzes: the AI's answer read in the forms ChatGPT and Claude vary between (JSON in a fence or after a
sentence, a bare list, questions grouped by kind, options as strings, objects or a letter map, the right answer as a
letter, a number counted from 0 or 1, or the option's text; Markdown), the checks (four options and one right answer,
a source the app can open, found by its words when none was named), the right answers spread over A–D, the coverage
worked out in code, the whole run with the AI replaced by a fake, the student's tries and the printed quiz."""

import datetime as dt
import json
import time
import types

import pytest

from studia import quizzes, store
from studia.config import Settings
from studia.coursesite import Resource, Unit
from studia.domain import Course, Recording, Segment, Transcript
from studia.library import Library
from studia.plans import PlanRunner, Scope

FACTS = {"files": [{"ref": "F1", "id": "r1", "name": "Week05.pdf", "pages": 20, "week": 5}],
         "lectures": [{"ref": "L1", "day_key": "c|2026-10-21|lecture", "date": "2026-10-21", "week": 5,
                       "parts": [{"part": 1, "delivery_id": "d1", "slides": 3, "dur": 3000.0},
                                 {"part": 2, "delivery_id": "d2", "slides": 0, "dur": 1500.0}]}],
         "outcomes": ["use conditional probability", "apply Bayes' theorem", "compute expectations"],
         "stressed": {"S1": {"id": "s-a", "day_key": "c|2026-10-21|lecture", "part": 1, "t": 845.0, "ts": "14:05",
                             "kind": "exam", "text": "Bayes will be on the quiz"}}}

CLAUDE = """Here is your quiz.

```json
{"title": "Bayes' theorem",
 "topics": ["conditional probability", "Bayes' theorem", "total probability", "independence of events", "Markov chains"],
 "questions": [
  {"type": "mc", "question": "What is $P(A\\mid B)$ when $A$ and $B$ are independent?",
   "options": ["$P(A)$", "$P(B)$", "$P(A)P(B)$", "$1$"], "answer": "A",
   "explanation": "Independence means knowing $B$ changes nothing.",
   "wrong": {"B": "That is the condition's own probability.", "C": "That is $P(A\\cap B)$.", "D": "Only if $A$ is sure."},
   "source": "F1 p.12", "topic": "independence of events", "los": [1, 9], "stressed": ["S1", "S7"], "level": "easy"},
  {"type": "short", "question": "Why does Bayes' theorem need the total probability rule?",
   "answer": "The denominator $P(B)$ is usually unknown and is found by total probability.",
   "points": ["names the denominator", "says it is found by total probability"],
   "source": {"ref": "L1", "part": 1, "at": "14:20"}, "topic": "Bayes' theorem", "los": [2]},
  {"type": "problem", "question": "A test is 95% accurate and 1% have the disease. Find $P(D\\mid +)$.",
   "steps": ["$P(+) = 0.95\\cdot 0.01 + 0.05\\cdot 0.99 = 0.059$", "$P(D\\mid +) = 0.0095/0.059 \\approx 0.161$"],
   "answer": "about 0.16", "source": "L1 part 2 05:00"}]}
```"""

CHATGPT = """{"quiz": {"title": "Probability",
  "multiple_choice": [
   {"prompt": "Which equals P(A and B)?",
    "choices": [{"text": "P(A|B)P(B)", "correct": true, "explanation": "The multiplication rule."},
                {"text": "P(A)+P(B)", "explanation": "That is for disjoint events' union."},
                {"text": "P(A|B)/P(B)", "explanation": "Divides instead of multiplying."},
                {"text": "P(B|A)", "explanation": "A conditional, not a joint probability."}],
    "source": {"file": "F1", "pages": [3, 4]}},
   {"prompt": "P(B)=0.5, P(A|B)=0.4. P(A and B)?", "choices": ["0.2", "0.9", "0.8", "0.1"], "correct_index": 0,
    "why_wrong": ["", "Adds them.", "Divides them.", "Halves the wrong one."], "source": "F1, page 5"}],
  "short_answer": [{"question": "Define independence.", "model_answer": "P(A and B) = P(A)P(B).",
                    "key_points": "the product rule; both directions"}],
  "problems": [{"question": "Compute E[X] for a fair die.", "solution": "1. Sum the faces: 21.\\n2. Divide by 6: 3.5.",
                "final_answer": "3.5", "reference": "L1 part 1 at 10:30"}]}}"""

MARKDOWN = """# Quick quiz

1. **What does $P(A|B)$ mean?**
A) The chance of A and B
B) The chance of A once B is known
C) The chance of B once A is known
D) The chance of A or B
**Answer:** B
**Explanation:** Conditioning on B shrinks the sample space to B.
**Why the others are wrong:**
- A: that is the joint probability
- C: the condition is swapped
- D: that is the union
**Source:** F1 p.2

2. A coin is tossed twice. What is the chance of two heads?
**Solution:**
1. Each toss is heads with chance 1/2.
2. The tosses are independent, so multiply: 1/4.
**Final answer:** 1/4
**Source:** L1 part 1 03:10

3. Explain the law of total probability.
**Answer:** Split the sample space into cases and add P(A|B_i)P(B_i).
**Key points:**
- the cases cover everything
- the cases don't overlap
"""


def test_claude_json_in_a_fence():
    raw = quizzes.read_answer(CLAUDE)
    q = quizzes.clean_quiz(raw, FACTS)
    assert q["title"] == "Bayes' theorem" and len(q["topics"]) == 5
    mc, short, prob = q["questions"]
    assert (mc["type"], mc["answer"], mc["options"][0]) == ("mc", 0, "$P(A)$")
    assert mc["wrong"] == ["", "That is the condition's own probability.", "That is $P(A\\cap B)$.", "Only if $A$ is sure."]
    assert mc["source"] == {"kind": "file", "ref": "F1", "id": "r1", "name": "Week05.pdf", "page": 12, "week": 5}
    assert mc["los"] == [1] and mc["stressed"] == ["s-a"]                # LO9 and S7 don't exist
    assert short["model"].startswith("The denominator") and len(short["points"]) == 2
    assert short["source"]["kind"] == "lecture" and short["source"]["t"] == 860 and short["source"]["ts"] == "14:20"
    assert prob["type"] == "problem" and len(prob["steps"]) == 2 and prob["model"] == "about 0.16"
    assert prob["source"]["part"] == 2 and prob["source"]["t"] == 300


CODE_QUIZ = {"title": "Loops in C", "questions": [
    {"type": "mc", "question": "What does this print?\n```c\nfor (int i = 0; i < 3; i++)\n    printf(\"%d\", i);\n```",
     "options": ["012", "123", "0123", "3"], "answer": "A", "source": "F1 p.4"},
    {"type": "mc", "question": "Which loop always runs its body at least once?", "options": ["do-while", "while", "for",
     "none"], "answer": "A", "source": "F1 p.5"}]}


@pytest.mark.parametrize("text", [json.dumps(CODE_QUIZ), "```json\n" + json.dumps(CODE_QUIZ, indent=1) + "\n```",
                                  "Here is your quiz.\n\n```json\n" + json.dumps(CODE_QUIZ) + "\n```\nGood luck!"])
def test_a_code_block_inside_a_question_is_part_of_it(text):
    """A programming course's "What does this print?" holds a ``` block inside the JSON: the fence around the answer
    is the one on lines of its own (the inner one cut the answer down to the code, and the quiz was unreadable)."""
    q = quizzes.clean_quiz(quizzes.read_answer(text), FACTS)["questions"]
    assert len(q) == 2 and "```c\nfor (int i = 0" in q[0]["q"]


def test_an_interval_in_a_markdown_quiz_stays_markdown():
    text = ("## Quiz\n\n**1. X is uniform on [0, 1]. What is E[X]?**\nA) 0\nB) 1/2\nC) 1\nD) 2\n**Answer: B**\n\n"
            "**2. What is P(X < 1/4)?**\nA) 1/4\nB) 1/2\nC) 3/4\nD) 1\n**Answer: A**")
    q = quizzes.clean_quiz(quizzes.read_answer(text), FACTS)["questions"]
    assert [x["answer"] for x in q] == [1, 0] and "[0, 1]" in q[0]["q"]


def test_chatgpt_grouped_by_kind_with_option_objects_and_indexes():
    q = quizzes.clean_quiz(quizzes.read_answer(CHATGPT), FACTS)["questions"]
    assert [x["type"] for x in q] == ["mc", "mc", "short", "problem"]
    assert q[0]["answer"] == 0 and q[0]["why"] == "" and q[0]["wrong"][1] == "That is for disjoint events' union."
    assert q[0]["source"]["page"] == 3
    assert q[1]["answer"] == 0 and q[1]["wrong"] == ["", "Adds them.", "Divides them.", "Halves the wrong one."]
    assert q[1]["source"]["page"] == 5
    assert q[2]["points"] == ["the product rule", "both directions"]
    assert q[3]["steps"] == ["Sum the faces: 21.", "Divide by 6: 3.5."] and q[3]["model"] == "3.5"
    assert q[3]["source"]["t"] == 630


def test_markdown_quiz():
    raw = quizzes.read_answer(MARKDOWN)
    assert raw["title"] == "Quick quiz"
    q = quizzes.clean_quiz(raw, FACTS)["questions"]
    assert [x["type"] for x in q] == ["mc", "problem", "short"]
    assert q[0]["answer"] == 1 and q[0]["wrong"][0] == "that is the joint probability" and q[0]["wrong"][1] == ""
    assert q[0]["source"]["page"] == 2 and q[0]["q"] == "What does $P(A|B)$ mean?"
    assert q[1]["steps"] == ["Each toss is heads with chance 1/2.", "The tosses are independent, so multiply: 1/4."]
    assert q[1]["model"] == "1/4" and q[1]["source"]["t"] == 190
    assert q[2]["points"] == ["the cases cover everything", "the cases don't overlap"]


HEADED = r"""# Practice quiz

## Question 1
What is the mean of a Poisson random variable with parameter $\lambda$?

A) $\lambda^2$
B) $\lambda$
C) $\sqrt{\lambda}$
D) $1/\lambda$

**Answer:** B
**Source:** L1 part 2 12:30

## Question 2
A fair coin is tossed 10 times. What is the probability of exactly 3 heads?

**Solution:**
1. This is binomial with $n=10$, $p=0.5$.
2. $P(X=3) = \binom{10}{3} 0.5^{10}$

3. $= 120/1024 \approx 0.117$

**Final answer:** $\approx 0.117$
**Source:** F1 p.7
"""


def test_a_markdown_quiz_with_question_headings():
    """"## Question 1" on a line of its own, the question on the next: the numbered steps of a worked solution are
    steps, not questions."""
    q = quizzes.clean_quiz(quizzes.read_answer(HEADED), FACTS)["questions"]
    assert [(x["type"], x["q"][:22]) for x in q] == [("mc", "What is the mean of a "), ("problem", "A fair coin is tossed ")]
    assert q[0]["answer"] == 1 and q[0]["options"][1] == r"$\lambda$" and q[0]["source"]["t"] == 750
    assert len(q[1]["steps"]) == 3 and q[1]["model"] == r"$\approx 0.117$" and q[1]["source"]["page"] == 7


@pytest.mark.parametrize("head", ["## Question {n}", "### Question {n}", "**Question {n}**", "**Question {n}:**", "Question {n}",
                                  "### Q{n}", "## Soru {n}", "### {n}.", "### {n}. Soru", "### Question {n} (multiple choice)",
                                  "### Question {n} of 2", "**Soru {n}/2**"])
def test_every_question_heading(head):
    body = "\nWhich distribution is memoryless{x}?\n\nA) Binomial\nB) Normal\nC) Exponential\nD) Uniform\n\n**Answer:** C\n"
    text = "# Quiz\n\n" + "\n".join(head.format(n=n) + body.format(x=x) for n, x in ((1, ""), (2, " here")))
    q = quizzes.clean_quiz(quizzes.read_answer(text), FACTS)["questions"]
    assert [(x["q"], x["answer"]) for x in q] == [("Which distribution is memoryless?", 2), ("Which distribution is memoryless here?", 2)]


@pytest.mark.parametrize("answer,options,zero,want", [
    ("C", ["a", "b", "c", "d"], None, 2), ("(b)", ["a", "b", "c", "d"], None, 1), ("Option D", ["a", "b", "c", "d"], None, 3),
    ("B) 0.3", ["0.1", "0.3", "0.5", "0.7"], None, 1), ("0.5", ["0.1", "0.3", "0.5", "0.7"], None, 2),
    ("a constant", ["a constant", "zero", "one", "infinity"], None, 0), (2, ["a", "b", "c", "d"], False, 1),
    (2, ["a", "b", "c", "d"], True, 2), (0, ["a", "b", "c", "d"], None, 0), ("Because", ["a", "b", "c", "d"], None, None),
    (["B"], ["a", "b", "c", "d"], None, 1), (7, ["a", "b", "c", "d"], None, None),
])
def test_the_right_answer_in_every_form(answer, options, zero, want):
    assert quizzes._answer_index(answer, options, zero) == want


def test_numbers_count_from_one_unless_a_zero_is_seen():
    one = {"questions": [{"question": "Pick the second option please", "options": ["w", "x", "y", "z"], "answer": 2},
                         {"question": "Pick the fourth option please", "options": ["w", "x", "y", "z"], "answer": 4}]}
    assert [q["answer"] for q in quizzes.clean_quiz(one, FACTS)["questions"]] == [1, 3]
    zero = {"questions": [{"question": "Pick the third option please", "options": ["w", "x", "y", "z"], "answer": 2},
                          {"question": "Pick the first option please", "options": ["w", "x", "y", "z"], "answer": 0}]}
    assert [q["answer"] for q in quizzes.clean_quiz(zero, FACTS)["questions"]] == [2, 0]


def test_unusable_questions_are_dropped_and_a_fifth_option_goes():
    raw = {"questions": [
        {"question": "Only three options here?", "options": ["a", "b", "c"], "answer": "A"},
        {"question": "No answer given at all?", "options": ["a", "b", "c", "d"]},
        {"question": "Two options read the same?", "options": ["x", "x", "y", "z"], "answer": "C"},
        {"question": "Five options, E is the extra one", "options": ["a", "b", "c", "d", "e"], "answer": "E",
         "wrong": {"A": "wa", "B": "wb", "C": "wc", "D": "wd"}},
        {"question": "Five options, E is the extra one", "options": ["a", "b", "c", "d"], "answer": "A"},   # repeat
        {"question": "Short with nothing?", "type": "short"},
        {"q": "Bare letters as options", "options": {"B": "two", "A": "one", "D": "four", "C": "three"}, "answer": "c"}]}
    q = quizzes.clean_quiz(raw, FACTS)["questions"]
    assert [x["q"] for x in q] == ["Five options, E is the extra one", "Bare letters as options"]
    assert q[0]["options"] == ["a", "b", "c", "e"] and q[0]["answer"] == 3 and q[0]["wrong"] == ["wa", "wb", "wc", ""]
    assert q[1]["options"] == ["one", "two", "three", "four"] and q[1]["answer"] == 2
    assert [x["n"] for x in q] == [1, 2]


def test_a_bare_list_after_a_sentence_and_option_prefixes():
    text = 'Sure! [{"question": "What is 2+2?", "options": ["A) 3", "B) 4", "C) 5", "D) 22"], "answer": "B"}] Good luck.'
    q = quizzes.clean_quiz(quizzes.read_answer(text), FACTS)["questions"][0]
    assert q["options"] == ["3", "4", "5", "22"] and q["answer"] == 1


@pytest.mark.parametrize("v,want", [
    ("F1 p.12", {"ref": "F1", "page": 12}), ("[F1, page 3]", {"ref": "F1", "page": 3}), ("F1 pp. 4-6", {"ref": "F1", "page": 4}),
    ({"ref": "L1", "part": 2, "at": "14:05"}, {"ref": "L1", "part": 2, "t": 845.0}),
    ("(L1, Part 1, 1:02:03)", {"ref": "L1", "part": 1, "t": 3723.0}), ("L1 14:05", {"ref": "L1", "part": None, "t": 845.0}),
    ({"lecture": "L1", "time": 30}, {"ref": "L1", "part": None, "t": 30.0}), ("the slides", None), (None, None),
])
def test_sources_as_written(v, want):
    assert quizzes.parse_source(v) == want


def test_only_sources_the_app_can_open():
    ok = quizzes.check_source({"ref": "F1", "page": 20}, FACTS)
    assert ok and ok["page"] == 20
    assert quizzes.check_source({"ref": "F1", "page": 21}, FACTS) is None          # the file has 20 pages
    assert quizzes.check_source({"ref": "F2", "page": 1}, FACTS) is None
    assert quizzes.check_source({"ref": "L1", "part": 2, "t": 1600.0}, FACTS) is None   # part 2 is 25 minutes
    assert quizzes.check_source({"ref": "L1", "part": 2, "t": 1510.0}, FACTS)["t"] == 1500.0
    assert quizzes.check_source({"ref": "L1", "part": None, "t": 10.0}, FACTS) is None  # two parts: which one?
    one = {**FACTS, "lectures": [{**FACTS["lectures"][0], "parts": FACTS["lectures"][0]["parts"][:1]}]}
    assert quizzes.check_source({"ref": "L1", "part": None, "t": 10.0}, one)["part"] == 1


def test_a_question_without_a_source_is_found_by_its_words():
    units = [({"kind": "file", "ref": "F1", "page": 7}, quizzes._content_words("The binomial distribution counts successes in n trials")),
             ({"kind": "file", "ref": "F1", "page": 8}, quizzes._content_words("Bayes theorem inverts conditional probability using total probability"))]
    q = {"q": "How does Bayes theorem use total probability?", "why": "conditional probability is inverted"}
    assert quizzes.locate(q, units)["page"] == 8
    assert quizzes.locate({"q": "Something else entirely"}, units) is None


def test_the_right_answers_are_spread_and_their_reasons_follow():
    raw = {"questions": [{"question": f"Question number {i} here", "options": [f"right {i}", f"w1 {i}", f"w2 {i}", f"w3 {i}"],
                          "answer": "A", "wrong": {"B": f"why1 {i}", "C": f"why2 {i}", "D": f"why3 {i}"}} for i in range(12)]}
    q = quizzes.clean_quiz(raw, FACTS, seed="plan1")["questions"]
    assert len({x["answer"] for x in q}) >= 3                                       # not always A
    for i, x in enumerate(q):
        assert x["options"][x["answer"]] == f"right {i}" and x["wrong"][x["answer"]] == ""
        assert {x["wrong"][k] for k in range(4) if k != x["answer"]} == {f"why1 {i}", f"why2 {i}", f"why3 {i}"}
        assert x["wrong"][x["options"].index(f"w1 {i}")] == f"why1 {i}"
    assert [x["answer"] for x in quizzes.clean_quiz(raw, FACTS, seed="plan1")["questions"]] == [x["answer"] for x in q]
    keep = ["one", "two", "Both A and B", "none"]
    assert quizzes.shuffle_right(keep, 2, ["a", "b", "", "d"], "s", 0) == (keep, 2, ["a", "b", "", "d"])


def test_reasons_that_name_a_letter_keep_the_order_they_name():
    """The options are reordered to spread the right answers; a question whose explanation or reasons call an option
    by its letter keeps its order, so "B is right" and "Why C is wrong" still match what the student sees."""
    qs = [{"type": "mc", "question": f"Two {10 * (i + 1)} ohm resistors in parallel give what resistance?",
           "options": ["the sum", "half of one", "one of them", "a quarter of one"], "answer": "B",
           "explanation": "B is right: two equal resistors in parallel give half of one.",
           "wrong": {"A": "A adds them as if in series.", "C": "C is one resistor alone.", "D": "D halves twice."},
           "source": "F1 p.3"} for i in range(6)]
    q = quizzes.clean_quiz(quizzes.read_answer(json.dumps({"questions": qs})), FACTS, seed="3f2a9c1b7d10")["questions"]
    assert [(x["answer"], x["options"][1], x["wrong"][2]) for x in q] == [(1, "half of one", "C is one resistor alone.")] * 6


@pytest.mark.parametrize("why,wrong,names", [
    ("The correct answer is B because I = V/R = 3 A.", [], True),
    ("Correct answer: B. I = V/R.", [], True),
    ("Unlike option A, which multiplies V and R, this divides.", [], True),
    ("Choosing C means you used one resistor alone.", [], True),
    ("Doğru cevap B'dir: I = V/R = 3 A.", [], True),
    ("B şıkkı doğrudur, çünkü I = V/R.", [], True),
    ("", ["A seçeneği V ile R'yi çarpar.", "", "", ""], True),
    ("", ["A adds them as if in series.", "", "", ""], True),          # a reason starting with its own letter
    ("The first option adds them as if in series.", [], True),
    ("Ohm's law: $I = V/R$, so the current is 3 A.", ["Two equal resistors in parallel: $10/2 = 5\\,\\Omega$."], False),
    ("Independence means knowing $B$ changes nothing.", ["", "That is $P(A\\cap B)$.", "", ""], False),
    ("By definition $P(A\\mid B) = P(A\\cap B)/P(B)$.", [], False),
    ("Paralel bağlı iki eşit direncin eşdeğeri birinin yarısıdır.", ["", "Bu seri bağlantıdır.", "", ""], False),
])
def test_what_names_an_option_by_its_letter(why, wrong, names):
    assert quizzes.names_option(why, wrong) is names


def test_coverage_is_worked_out_from_the_questions():
    quiz = quizzes.clean_quiz(quizzes.read_answer(CLAUDE), FACTS)
    facts = {**FACTS, "focus": {"name": "Quiz 2", "week": 6, "los": [{"no": 1, "text": "use conditional probability"},
                                                                    {"no": 3, "text": "compute expectations"}]},
             "materials_missing": [{"title": "Week05_Teacher"}], "days_missing": [{"date": "2026-10-23"}],
             "files_cut": [], "cut": [], "left_out": [], "weeks": [{"no": 5, "topic": "Bayes"}], "span": "week 5"}
    c = quizzes.coverage(quiz, facts)
    assert c["kinds"] == {"mc": 1, "short": 1, "problem": 1} and c["total"] == 3
    assert c["los"] == [{"no": 1, "text": "use conditional probability", "n": 1}, {"no": 3, "text": "compute expectations", "n": 0}]
    assert c["stressed_total"] == 1 and c["stressed_hit"] == 1
    assert c["topics_missing"] == ["Markov chains"]                 # the others: in a question, its options or reasons
    quiz["topics"].append("det(kA) and det(A+B)")                     # a formula's words: none, but a question names it
    quiz["questions"][2]["topic"] = "det(kA) and det(A+B)"
    assert quizzes.coverage(quiz, facts)["topics_missing"] == ["Markov chains"]
    assert c["materials_missing"] == ["Week05_Teacher"] and c["days_missing"] == ["2026-10-23"]
    assert c["files"] == ["Week05.pdf"] and c["lectures"] == ["2026-10-21"]


def test_units_written_after_a_letter_are_drawn():
    """"12 s$^2$" can't be drawn (a dollar right after a letter is money or text to the page): it becomes a formula."""
    assert quizzes._clean_tex(r"so $\sigma^2=12$ s$^2$, 4 m$^2$, cm$^{3}$; $x^2$ and \(y\) stay") == \
        r"so $\sigma^2=12$ $\mathrm{s}^2$, 4 $\mathrm{m}^2$, $\mathrm{cm}^{3}$; $x^2$ and $y$ stay"


def test_mix_language_and_problems():
    assert quizzes.mix(10, "mixed", True) == {"mc": 5, "short": 2, "problem": 3}
    assert quizzes.mix(10, "mixed", False) == {"mc": 7, "short": 3, "problem": 0}
    assert quizzes.mix(5, "problems", False) == {"mc": 0, "short": 5, "problem": 0}
    assert sum(quizzes.mix(15, "mixed", True).values()) == 15
    assert quizzes.course_language({"syllabus": {"language": "English"}}) == "English"
    assert quizzes.course_language({"syllabus": {"language": "Turkish"}}) == "Turkish"
    assert quizzes.course_language({"syllabus": {}}, {"pack": "[00:10] [TR] bir\n[00:20] [TR] iki\n[00:30] three"}) == "Turkish"
    assert quizzes.quantitative({"code": "EEE 242"}) and not quizzes.quantitative({"code": "PSY 101"}, {"pack": "minds"})


def _course(exams, plan=(), now=8):
    """A course's weeks 1-16 as course_detail gives them (``exams``: week -> the syllabus' topic for an exam week; the
    weeks before ``now`` are past) and the assessments its announcements named."""
    weeks = [{"no": n, "start": (dt.date(2026, 9, 14) + dt.timedelta(weeks=n - 1)).isoformat(), "exam": n in exams,
              "exam_label": exams.get(n, ""), "past": n < now} for n in range(1, 17)]
    return {"weeks": weeks, "alerts": {"plan": [{"los": [], "sources": [{"id": "a"}], **e} for e in plan]}}


def test_the_syllabus_exam_weeks_are_assessments_too():
    """Home, the course page and the Calendar showed EEE 215's midterm this week from the syllabus, while Practice
    greyed out "The next exam" ("No quiz or midterm announced yet"): only announced ones counted there. Now each
    syllabus exam week no announcement covers counts, with the weeks it covers, and the quiz prepares for it."""
    d = _course({8: "Midterm", 16: "Final Exam"})
    got = [(e["name"], e["kind"], e["weeks"], e["ahead"]) for e in quizzes.assessments(d)]
    assert got == [("Midterm", "midterm", list(range(1, 9)), True), ("Final Exam", "final", list(range(1, 17)), True)]
    focus = quizzes._focus(d, Scope(kind="quiz", focus="Midterm"))
    assert focus == {"name": "Midterm", "week": 8, "los": []}                # the prompt says what it prepares for
    assert "The student prepares for Midterm (week 8)." in quizzes.write_prompt(
        Scope(kind="quiz"), {**FACTS, "span": "week 5", "language": "English", "focus": focus, "pack": ""}, 10,
        {"mc": 10, "short": 0, "problem": 0})
    # only quizzes announced: the syllabus' midterm still counts (each exam week on its own)
    d = _course({8: "Midterm", 16: "Final Exam"}, [{"kind": "quiz", "no": 1, "name": "Quiz 1", "week": 5}])
    assert [(e["name"], e["weeks"]) for e in quizzes.assessments(d)] == [
        ("Quiz 1", [1, 2, 3, 4, 5]), ("Midterm", list(range(1, 9))), ("Final Exam", list(range(1, 17)))]
    # an announced midterm covers its week: not twice, and its outcomes are kept
    d = _course({8: "Midterm", 16: "Final Exam"}, [{"kind": "midterm", "no": 1, "name": "Midterm 1", "week": 8,
                                                     "los": [2, 3]}])
    assert [(e["name"], e["los"]) for e in quizzes.assessments(d)] == [("Midterm 1", [2, 3]), ("Final Exam", [])]
    # ... and one announced a week after the syllabus' is that exam, moved (not a second midterm)
    d = _course({8: "Midterm", 16: "Final Exam"}, [{"kind": "midterm", "no": 0, "name": "Midterm", "week": 9}])
    assert [(e["name"], e["week"], e["weeks"]) for e in quizzes.assessments(d)] == [
        ("Midterm", 9, list(range(1, 10))), ("Final Exam", 16, list(range(1, 17)))]
    # two midterms in the syllabus (a Turkish one too): the second covers the weeks after the first; a two-week exam
    # period is one exam, still ahead in its second week; a past one isn't ahead
    d = _course({6: "Midterm Exam 1", 11: "ARA SINAV", 12: "ARA SINAV", 16: "FİNAL"}, now=12)
    assert [(e["name"], e["kind"], e["weeks"], e["ahead"]) for e in quizzes.assessments(d)] == [
        ("Midterm Exam 1", "midterm", list(range(1, 7)), False), ("ARA SINAV", "midterm", list(range(7, 13)), True),
        ("FİNAL", "final", list(range(1, 17)), True)]


# ------------------------------------------------------------------ the whole run
@pytest.fixture
def env(tmp_path, monkeypatch):
    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="c", title="2026-1 MATH 240 P 1", code="MATH 240", name="Probability", term="T")])
    lib.merge_units("c", [Unit("u1", 0, "Hafta 1", dt.date(2026, 9, 21), dt.date(2026, 9, 27), "",
                               [Resource("r1", "Week01", "Week01.pdf", "pdf", "pdf", "u", "d")])], first_sync=True)
    lib.set_material_file("r1", str(tmp_path / "Week01.pdf"), 10, "u")
    (tmp_path / "Week01.pdf").write_bytes(b"%PDF")
    lib.set_material_text("r1", ["Conditional probability: P(A|B) = P(A and B)/P(B), defined when P(B) > 0.",
                                 "Bayes theorem: P(B|A) = P(A|B)P(B)/P(A), with the total probability rule below."])
    lib.set_course_info("c", syllabus={"outcomes": ["use conditional probability", "apply Bayes' theorem"],
                                       "language": "English",
                                       "weeks": [{"no": 1, "subject": "Conditional probability", "preparation": ""}]})
    rec = Recording(delivery_id="r", course_id="c", folder_id="f", folder_path="", name="n",
                    start=dt.datetime(2026, 9, 23, 9, 0), duration_s=3000, viewer_url="v")
    lib.merge_recordings("c", [rec], first_sync=True)
    tr = Transcript(delivery_id="r", segments=[
        Segment(0, 5, "Today: conditional probability, given that B happened.", "en", 0.9),
        Segment(60, 66, "I will ask a Bayes question on the quiz, so practise the total probability rule.", "en", 0.9)],
        duration_s=3000, speech_s=11, language_seconds={"en": 11}, model="m", device="cpu", engine_version="t",
        created_at="2026-09-23T12:00:00")
    lib.save_transcript(tr, store.save_transcript(tr))
    lib.set_status("r", "done")
    svc = types.SimpleNamespace(lib=lib, settings=Settings(ai_model="sonnet"), emit=lambda e: None,
                                output_root=tmp_path / "out")
    return types.SimpleNamespace(lib=lib, svc=svc, tmp=tmp_path)


def wait_done(lib, pid, timeout=10):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        p = lib.plan_brief(pid)
        if p["status"] in ("done", "error", "paused"):
            return p
        time.sleep(0.02)
    raise AssertionError("the quiz did not finish")


def test_a_quiz_is_written_checked_and_kept(env, monkeypatch):
    asked = []
    answers = iter(["I can't do JSON today.", json.dumps({"title": "Conditional probability", "topics": ["Bayes"],
        "questions": [{"type": "mc", "question": "What is P(A|B)?", "options": ["P(A and B)/P(B)", "P(A)P(B)", "P(B)/P(A)", "1"],
                       "answer": "A", "explanation": "The definition.", "wrong": ["", "Only when independent.", "Upside down.", "No."],
                       "source": "F1 p.1", "los": [1], "stressed": ["S1"]},
                      {"type": "short", "question": "Why practise the total probability rule for Bayes?",
                       "answer": "It gives the denominator of Bayes' theorem.", "points": ["denominator"]}]})])

    def ask_text(system, content, model="sonnet", effort="low", cancel=None, on_text=None):
        asked.append(content[0]["text"])
        return {"text": next(answers), "usage": {"input_tokens": 100, "output_tokens": 50}, "model": model}

    runner = PlanRunner(env.svc, ask_text=ask_text)
    pid = runner.create("c", {"kind": "quiz", "weeks": [1], "questions": 5, "qtypes": "mixed", "focus": ""})
    runner.start(pid)
    p = wait_done(env.lib, pid)
    assert p["status"] == "done", p
    prompt = asked[0]
    assert "exactly 5 exam-style questions: 2 multiple-choice, 1 short-answer and 2 worked problems" in prompt
    assert "in English (the language this course is taught in)" in prompt
    assert "S1 (tied to an exam) [L1 part 1 01:00]" in prompt and "[F1 p.1] Conditional probability" in prompt
    assert "never call an option by its letter" in prompt and "why B is right" not in prompt   # the app reorders them
    assert len(asked) == 2                                          # the first answer couldn't be read: asked again
    full = env.lib.plan(pid)
    r = full["result"]
    assert r["kind"] == "quiz" and r["title"] == "Conditional probability" and len(r["questions"]) == 2
    q1, q2 = r["questions"]
    assert q1["source"]["name"] == "Week01.pdf" and q1["stressed"] and q1["los"] == [1]
    assert q2["source"] and q2["found"]                     # it named none: found by its words (page 2 or the lecture)
    assert (q2["source"].get("page"), q2["source"].get("ts")) in ((2, None), (None, "01:00"))
    assert r["coverage"]["stressed_hit"] == 1 and r["coverage"]["kinds"]["mc"] == 1
    # tries are kept apart, per quiz
    from studia import practice

    practice.save_attempt(env.lib, pid, "c", {"answers": [{"n": 1, "got": 1}, {"n": 2, "got": 0.5}]})
    practice.save_attempt(env.lib, pid, "c", {"answers": [{"n": 2, "got": 1}], "only": [2]})
    tries = practice.attempts(env.lib, pid)
    assert [(a["score"], a["total"], a["only"]) for a in tries] == [(1.0, 1, [2]), (1.5, 2, [])]
    brief = practice.quiz_brief(env.lib.plan_brief(pid), tries)
    assert brief["tries"] == 2 and brief["best"]["score"] == 1.5           # a retry of missed ones isn't the best
    # the printed quiz: the questions, then the answers
    body = quizzes.document_body(full)
    assert body.index("Questions") < body.index("Answers") and "Week01.pdf, page 1" in body
    assert "Only when independent." in body and 'type="A"' in body
    printed = []
    monkeypatch.setattr("studia.studypack.print_pdf", lambda h, out: printed.append(h) or out.write_bytes(b"%PDF") or True)
    pdf = quizzes.make_pdf(env.svc, env.lib.plan(pid))
    assert pdf and pdf.exists() and pdf.parent.name == "Practice quizzes" and printed
    html_file = printed[0].read_text(encoding="utf-8") if hasattr(printed[0], "read_text") else ""
    assert "qz-q" in html_file


def test_an_answer_misread_as_questions_is_asked_again(env):
    """Markdown read as questions with nothing to answer them by (a worked solution's numbered steps): the AI is asked
    once more, and its quiz is used."""
    answers = iter(["Let me work one first.\n1. This is binomial with $n=10$.\n2. So $P(X=3)$ is about 0.117.",
                    json.dumps({"title": "Binomial", "questions": [
                        {"type": "mc", "question": "What is the mean of a binomial variable?", "options": ["np", "n", "p", "np(1-p)"],
                         "answer": "A", "source": "F1 p.1"}]})])
    asked = []

    def ask_text(system, content, model="sonnet", effort="low", cancel=None, on_text=None):
        asked.append(content[-1]["text"])
        return {"text": next(answers), "usage": {}}

    runner = PlanRunner(env.svc, ask_text=ask_text)
    pid = runner.create("c", {"kind": "quiz", "weeks": [1]})
    runner.start(pid)
    p = wait_done(env.lib, pid)
    assert p["status"] == "done", p
    assert len(asked) == 2 and asked[1] == "Reply with the JSON object only."
    assert [q["q"] for q in env.lib.plan(pid)["result"]["questions"]] == ["What is the mean of a binomial variable?"]


def test_an_answer_with_no_usable_question_fails_plainly(env):
    def ask_text(system, content, model="sonnet", effort="low", cancel=None, on_text=None):
        return {"text": json.dumps({"questions": [{"question": "Broken one?", "options": ["a", "b"]}]}), "usage": {}}

    runner = PlanRunner(env.svc, ask_text=ask_text)
    pid = runner.create("c", {"kind": "quiz", "weeks": [1]})
    runner.start(pid)
    p = wait_done(env.lib, pid)
    assert p["status"] == "error" and "no question had a clear right answer" in p["error"]


def test_quizzes_and_card_sets_stay_out_of_the_study_plans_list():
    from studia.api import Api
    from studia.services import Services

    api = Api(Services())
    for pid, kind in (("q1", "quiz"), ("k1", "cards"), ("p1", "pack")):
        api._s.lib.create_plan(pid, "c", Scope(kind=kind).to_json())
    assert [p["id"] for p in api.plan_list()["plans"]] == ["p1"]
    assert Scope.from_json({"kind": "quiz", "questions": "15", "qtypes": "mc"}).questions == 15
