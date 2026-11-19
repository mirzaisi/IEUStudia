"""The exam kit (kit.py): Export for AI chat aimed at a coming quiz or exam (a syllabus final too), with "00 Start
here.md", the prompts (the app's quiz and card rules, PRs #7, #8 and #10's language rules), short references in the
file names that stay the same in every zip of a course, and the kit kept for what comes back; then what ChatGPT or
Claude wrote, pasted on the Practice tab, read in the forms web chats write it (fenced JSON, several blocks, loose JSON
with Turkish keys, Markdown; cards as JSON, a table, tab-separated lines), checked without AI, saved as a quiz or a
card set that plays and feeds the mistakes loop like the app's own. No network and no AI anywhere here."""

import datetime as dt
import json
import types
import zipfile

import pytest

from studia import cards, kit, practice, store
from studia import course_export as ce
from studia.config import Settings
from studia.coursesite import Announcement, Resource, Unit
from studia.domain import Course, Recording, Segment, Transcript
from studia.library import Library

TODAY = dt.date(2026, 10, 8)              # week 3: Quiz 2 (week 4) and the final are ahead


def _file(path, data=b"%PDF-1.7 data"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path)


def _transcribe(lib, rec, texts):
    segs = [Segment(i * 150.0, i * 150.0 + 25, x, "en", 0.9) for i, x in enumerate(texts)]      # 00:00, 02:30, 05:00
    tr = Transcript(delivery_id=rec.delivery_id, segments=segs, duration_s=rec.duration_s, speech_s=60,
                    language_seconds={"en": 60}, model="m", device="cpu", engine_version="t",
                    created_at="2026-10-01T12:00:00")
    lib.save_transcript(tr, store.save_transcript(tr))
    lib.set_status(rec.delivery_id, "done")


def units(extra=()):
    return [
        Unit("u1", 0, "Hafta 1", dt.date(2026, 9, 21), dt.date(2026, 9, 27), "", [
            Resource("r1", "Week 1 slides", "Week01.pdf", "pdf", "pdf", "u1", "/d/r1")]),
        Unit("u2", 1, "Hafta 2", dt.date(2026, 9, 28), dt.date(2026, 10, 4), "", [
            Resource("r2", "Conditional probability", "Conditional.pdf", "pdf", "pdf", "u2", "/d/r2")]),
        Unit("u3", 2, "Hafta 3", dt.date(2026, 10, 5), dt.date(2026, 10, 11), "", [
            *extra, Resource("r3", "Bayes", "Bayes theorem.pdf", "pdf", "pdf", "u3", "/d/r3")]),
        Unit("ug", 3, "Formula Sheet", None, None, "", [
            Resource("r9", "Formula sheet", "Formulas.pdf", "pdf", "pdf", "u9", "/d/r9")])]


def make_env(tmp_path, settings=None):
    lib = Library(tmp_path / "lib.db")
    files = tmp_path / "files"
    lib.upsert_courses([Course(id="c", title="2026-1 MATH 240 Olasılık 1", code="MATH 240", name="Probability",
                               term="2026-2027 Fall", instructor="Dr Özkut")])
    lib.merge_units("c", units(), first_sync=True)
    for rid, name in (("r1", "Week01.pdf"), ("r2", "Conditional.pdf"), ("r3", "Bayes theorem.pdf"), ("r9", "Formulas.pdf")):
        lib.set_material_file(rid, _file(files / name), 13, "u")
    lib.set_material_text("r1", ["Sample spaces: the set of all outcomes of an experiment.",
                                 "Events are subsets of the sample space; the empty event never happens."])
    lib.set_material_text("r2", ["Conditional probability: P(A|B) = P(A and B)/P(B), defined when P(B) > 0.",
                                 "The multiplication rule: P(A and B) = P(A|B)P(B), used for sequential experiments."])
    lib.set_material_text("r3", ["Bayes theorem: P(B|A) = P(A|B)P(B)/P(A), turning a likelihood into a posterior.",
                                 "The total probability rule: P(A) is the sum of P(A|B_i)P(B_i) over a partition.",
                                 "Example: a medical screening test with sensitivity 0.95, specificity 0.95 and "
                                 "prevalence 0.01; the posterior probability of disease after a positive test."])
    lib.set_material_text("r9", ["Formulas: counting, conditional probability, Bayes theorem."])
    lib.set_course_info("c", syllabus={
        "name": "Probability for Engineers", "language": "English",
        "outcomes": ["use counting", "compute conditional probabilities", "apply Bayes' theorem"],
        "grading": [{"activity": "Quizzes", "number": 4, "weight": 20.0},
                    {"activity": "Final Project", "number": 1, "weight": 10.0},
                    {"activity": "Final Exam", "number": 1, "weight": 50.0}],
        "weeks": [{"no": n, "subject": s, "preparation": ""} for n, s in
                  ((1, "Sample Spaces"), (2, "Conditional Probability"), (3, "Bayes"), (4, "Random Variables"),
                   (15, "Final Exam"))]})
    lib.merge_announcements("c", [
        Announcement("a1", "Quiz 2", "Dr Özkut", dt.datetime(2026, 10, 1, 10, 0),
                     "Quiz 2 will be in week 4 and will cover weeks 2 and 3.\nIt covers Learning Outcome 2.")],
        first_sync=True)
    recs = [Recording(delivery_id=did, course_id="c", folder_id="f", folder_path="", name=f"MATH 240 {did}",
                      start=start, duration_s=3000, viewer_url=f"https://panopto/{did}")
            for did, start in (("d1", dt.datetime(2026, 9, 23, 8, 30)), ("d2", dt.datetime(2026, 9, 30, 8, 30)),
                               ("d3", dt.datetime(2026, 10, 7, 8, 30)))]
    lib.merge_recordings("c", recs, first_sync=True)
    _transcribe(lib, recs[0], ["Today: sample spaces and events.", "An event is a subset of the sample space.",
                               "Next week we have a quiz on sample spaces, so you need to study the events."])
    _transcribe(lib, recs[1], ["Today: conditional probability and the multiplication rule.",
                               "This will be on the quiz, so you need to know the multiplication rule.",
                               "An example with two dice: the second throw given the first.",
                               "Another example with a deck of cards drawn without replacement.",
                               "The proof of the multiplication rule won't be on the exam."])
    _transcribe(lib, recs[2], ["Bayes theorem today, with the total probability rule in the denominator.",
                               "You need to know the total probability rule for the quiz, practise it.",
                               "A medical test example: sensitivity, specificity and prevalence give the posterior."])
    settings = settings or Settings(ai_model="sonnet")
    svc = types.SimpleNamespace(lib=lib, settings=settings, output_root=tmp_path / "out", day_files=lambda day: {})
    api = types.SimpleNamespace(_s=svc, _deck=lambda cid: cards.deck(svc, cid))
    return types.SimpleNamespace(lib=lib, settings=settings, svc=svc, api=api, out=tmp_path / "out", tmp=tmp_path)


def at_today(monkeypatch):
    """The course page's weeks as on TODAY (which are past, which still ahead: views.course_detail asks the clock)."""
    class Clock(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(TODAY.year, TODAY.month, TODAY.day, 12, 0)

    monkeypatch.setattr("studia.views.dt.datetime", Clock)
    monkeypatch.setattr("studia.views.dt.date", type("D", (dt.date,), {"today": classmethod(lambda c: TODAY)}))


@pytest.fixture
def env(tmp_path, monkeypatch):
    at_today(monkeypatch)
    return make_env(tmp_path)


def export(env, **choice):
    return ce.build(env.lib, env.settings, env.out, "c", ce.Choice(**choice), today=TODAY)


def read(out, name):
    with zipfile.ZipFile(out["path"]) as z:
        return z.read(f"{out['name'][:-4]}/{name}").decode("utf-8")


def names(out):
    with zipfile.ZipFile(out["path"]) as z:
        return {n.split("/", 1)[1] for n in z.namelist()}


# ------------------------------------------------------------------ the zip: brief, prompts, references
def test_the_dialog_offers_the_coming_quizzes_and_the_syllabus_final(env):
    o = ce.options(env.lib, env.settings, env.out, "c", today=TODAY)
    got = {a["key"]: a for a in o["assessments"]}
    assert got["quiz:2"]["weeks"] == [2, 3] and got["quiz:2"]["weeks_from"] == "announced" and got["quiz:2"]["week"] == 4
    final = next(a for a in o["assessments"] if a["key"].startswith("syllabus:"))
    assert final["kind"] == "final" and final["week"] == 15 and final["weeks"] == list(range(1, 16)) and final["date"]
    assert ce.options(env.lib, env.settings, env.out, "", today=TODAY)["assessments"] == []      # (all courses: none)
    assert ce.Choice.of({"focus": "quiz:2"}).focus == "quiz:2" and ce.Choice.of({"focus": 3}).focus == ""


def test_a_kit_for_a_quiz_has_its_brief_prompts_and_references(env):
    out = export(env, weeks=[2, 3], focus="quiz:2")
    inside = names(out)
    assert {"00 Start here.md", "Prompts/Practice quiz.md", "Prompts/Flashcards.md", "Prompts/Cheatsheet.md"} <= inside
    assert "Week 02 - Conditional Probability/Course files/F2 - Conditional.pdf" in inside
    assert "Week 03 - Bayes/Lectures/L3 2026-10-07 Wed - Lecture/Transcript.md" in inside
    assert "General course files/Formula Sheet/F4 - Formulas.pdf" in inside
    start = read(out, "00 Start here.md")
    assert start.startswith("# Start here: Quiz 2 · MATH 240 · Probability for Engineers")
    assert "- When: week 4 (Mon 12 Oct to Sun 18 Oct 2026), in 4 days." in start
    assert "- It covers: weeks 2–3 (as the lecturer announced it)." in start
    assert "- Weight: Quizzes: 20% of the grade in all (4 of them, about 5% each)." in start
    assert "  - LO2: compute conditional probabilities" in start
    assert "announcement “Quiz 2”: “Quiz 2 will be in week 4 and will cover weeks 2 and 3." in start
    assert "[L3 part 1 02:30]: “You need to know the total probability rule for the quiz" in start
    assert "Next week we have a quiz on sample spaces" not in start        # (said in week 1: about Quiz 1)
    assert "S1 (tied to an exam) [L2 part 1 02:30] “This will be on the quiz" in start
    nothing = start.split("## Not needed (the lecturer said so)")[1].split("## ")[0]
    assert "[L2 part 1 10:00]" in nothing and "The proof of the multiplication rule won't be on the exam." in nothing
    assert "- F2 = the file Conditional.pdf (2 pages), week 2: Week 02 - Conditional Probability/Course files/F2 - " \
           "Conditional.pdf" in start
    assert "- L3 = the lecture of Wed 7 Oct 2026 (week 3): Week 03 - Bayes/Lectures/L3 2026-10-07 Wed - Lecture/" \
           "Transcript.md" in start
    assert "F1 =" not in start                                 # (week 1 isn't in this zip)
    # the quiz prompt: the app's own task, priorities, JSON and rules, with the kit's id and references
    k = out["kit"]
    quiz = read(out, "Prompts/Practice quiz.md")
    assert quiz.rstrip() == next(p["text"] for p in k["prompts"] if p["key"] == "quiz")
    assert f'{{"kit": "{k["id"]}", "title":' in quiz and "exactly 10 exam-style questions" in quiz
    assert "1. The course material in the zip I uploaded (weeks 2–3)" in quiz
    assert "The student prepares for Quiz 2 (week 4), which covers these learning outcomes: LO2: compute" in quiz
    assert "S1 (tied to an exam) [L2 part 1 02:30]" in quiz and "Read 00 Start here.md in the zip first." in quiz
    assert "=== REFERENCES" in quiz and "F3 = the file Bayes theorem.pdf (3 pages), week 3" in quiz
    assert "in English (the language this course is taught in)" in quiz and "=== LANGUAGES ===" not in quiz
    assert quiz.rstrip().endswith("Nothing before or after it.") and "=== COURSE MATERIAL ===" not in quiz
    cards_p = read(out, "Prompts/Flashcards.md")
    assert '"cards": [{"front"' in cards_p and k["id"] in cards_p and "Make 30 flashcards" in cards_p
    sheet = read(out, "Prompts/Cheatsheet.md")
    assert "Make me a cheatsheet for Quiz 2" in sheet and "★" in sheet and "Write in English" in sheet
    # the README and the first message point at it; the index holds the references
    readme = read(out, "README.md")
    assert "I'm preparing for Quiz 2: read 00 Start here.md first" in out["first_message"]
    assert "## Then practise with it" in readme and "press Ctrl+V" in readme and "00 Start here.md: what Quiz 2 covers" in readme
    assert "## References" in read(out, "Index of files.md") and "- L2 = the lecture of" in read(out, "Index of files.md")
    # the kit is kept for what comes back
    saved = kit.load(env.lib, "c")
    assert saved["id"] == k["id"] and saved["weeks"] == [2, 3] and saved["key"] == "quiz:2" and saved["focus"] == "Quiz 2"
    assert saved["refs"]["F2"]["id"] == "r2" and saved["refs"]["L3"]["day_key"].startswith("c|2026-10-07")
    assert saved["stressed"]["S1"]["kind"] == "exam" and saved["name"] == out["name"]


def test_without_for_a_course_zip_still_has_prompts_and_references(env):
    out = export(env, weeks=[3])
    inside = names(out)
    assert "00 Start here.md" not in inside and "Prompts/Practice quiz.md" in inside
    assert out["kit"]["focus"] == "" and not out["kit"]["start"]
    quiz = read(out, "Prompts/Practice quiz.md")
    assert "Read 00 Start here.md" not in quiz and "The student prepares for" not in quiz
    assert "I'm preparing for" not in out["first_message"]
    every = ce.build(env.lib, env.settings, env.out, "", ce.Choice(weeks=[3]), today=TODAY)      # all courses: as before
    assert every["kit"] is None and not any(n.startswith("Prompts/") for n in names(every))


def test_references_stay_the_same_in_every_zip_of_a_course(env):
    first = export(env, weeks=[3])
    assert "Week 03 - Bayes/Course files/F3 - Bayes theorem.pdf" in names(first)        # (the course's order)
    every = export(env)
    inside = names(every)
    assert "Week 01 - Sample Spaces/Course files/F1 - Week01.pdf" in inside
    assert "Week 03 - Bayes/Course files/F3 - Bayes theorem.pdf" in inside
    assert "Week 02 - Conditional Probability/Lectures/L2 2026-09-30 Wed - Lecture/Transcript.md" in inside
    text = read(every, "Week 02 - Conditional Probability/Lectures/L2 2026-09-30 Wed - Lecture/Transcript.md")
    assert text.startswith("# L2 · MATH 240") and 'Cite this lecture as L2, with the part and the time' in text
    assert "F3 - Bayes theorem.pdf" in read(every, "Week 03 - Bayes/Week overview.md")
    # a file new to the course gets the next number, the others keep theirs
    env.lib.merge_units("c", units([Resource("r0", "Extra", "Extra.pdf", "pdf", "pdf", "u0", "/d/r0")]), first_sync=False)
    env.lib.set_material_file("r0", _file(env.tmp / "files" / "Extra.pdf"), 13, "u")
    again = names(export(env, weeks=[3]))
    assert {"Week 03 - Bayes/Course files/F3 - Bayes theorem.pdf", "Week 03 - Bayes/Course files/F5 - Extra.pdf"} <= again


def test_the_prompts_follow_the_language_rules(tmp_path, monkeypatch):
    """Turkish documents for an English course: the quiz's questions, options and answers stay in English (PRs #7,
    #8), a card's front in English and its back in Turkish (PR #10), each said again after the material."""
    at_today(monkeypatch)
    env = make_env(tmp_path, Settings(ai_model="sonnet", doc_language="tr"))
    out = export(env, weeks=[2, 3], focus="quiz:2")
    quiz = read(out, "Prompts/Practice quiz.md")
    assert "Write each question, its options and its answer (the model answer or the final result) in English" in quiz
    assert "what explains them in Turkish" in quiz
    assert quiz.split("=== LANGUAGES ===")[1].startswith("\nQuestions, options and answers in English")
    assert quiz.index("=== REFERENCES") < quiz.index("=== LANGUAGES ===") < quiz.index("=== HOW TO ANSWER ===")
    flash = read(out, "Prompts/Flashcards.md")
    assert "Write each card's \"front\" (the term or the question) in English" in flash
    assert '=== LANGUAGES ===\n"front" in English; "back" and "topic" in Turkish.' in flash
    sheet = read(out, "Prompts/Cheatsheet.md")
    assert "Write in Turkish, headings included. Keep technical terms in English" in sheet


def test_a_final_known_only_from_the_syllabus_gets_a_brief(env):
    key = next(a["key"] for a in kit.choices(ce.plan_course(env.lib, env.settings, "c", ce.Choice(), TODAY).detail)
               if a["kind"] == "final")
    out = export(env, focus=key)
    start = read(out, "00 Start here.md")
    assert start.startswith("# Start here: Final Exam") and "(the whole term)" in start
    assert "- Weight: Final Exam: 50% of the grade." in start and "Final Project" not in start
    # what was said of the quizzes, and "the exam" said in class without naming the final, isn't about the final
    assert "## What the lecturer said about it" not in start
    assert "S1 (tied to an exam)" in start and "## Not needed" in start            # (what was stressed still is)


def test_the_brief_and_the_dialog_say_what_the_exam_room_says(env):
    """The day set in the Exam room ("Set the exact day") is the dialog's and the brief's; the weight is the room's
    grade share (examroom.grade_share): a final jury is not the final, a Turkish "Yarıyıl Sonu Sınavı" is."""
    from studia import examroom

    examroom.set_day(env.lib, "c", "quiz:2", "2026-10-14")
    o = ce.options(env.lib, env.settings, env.out, "c", today=TODAY)
    assert next(a for a in o["assessments"] if a["key"] == "quiz:2")["date"] == "2026-10-14"
    start = read(export(env, weeks=[2, 3], focus="quiz:2"), "00 Start here.md")
    assert "- When: Wed 14 Oct 2026 (week 4, the day I set), in 6 days." in start
    grading = {"grading": [{"activity": "Final Jury", "number": 1, "weight": 30.0},
                           {"activity": "Yarıyıl Sonu Sınavı", "number": 1, "weight": 40.0}]}
    assert kit._weight(grading, {"kind": "final"}) == "Yarıyıl Sonu Sınavı: 40% of the grade"


# ------------------------------------------------------------------ what comes back: a quiz
CHATGPT = """Here's your practice quiz! Paste it into IEUStudia.

```json
{"kit": "%(kit)s", "title": "Conditional probability and Bayes",
 "topics": ["conditional probability", "multiplication rule", "Bayes theorem", "total probability"],
 "questions": [
  {"type": "mc", "question": "Which formula defines $P(A\\mid B)$?",
   "options": ["$P(A\\cap B)/P(B)$", "$P(A)P(B)$", "$P(B)/P(A)$", "$P(A)+P(B)$"], "answer": "A",
   "explanation": "Conditioning divides the joint probability by the condition's.",
   "wrong": {"B": "Only for independent events.", "C": "Upside down.", "D": "That is no probability rule."},
   "source": "F2 p.1", "topic": "conditional probability", "los": [2], "stressed": ["S1"], "level": "easy"},
  {"type": "short", "question": "Why does Bayes' theorem need the total probability rule?",
   "answer": "Its denominator P(A) is found with the total probability rule.", "points": ["names the denominator"],
   "source": "L3 (Part 1, 02:30)", "topic": "Bayes theorem", "los": [3]},
  {"type": "problem", "question": "A test has sensitivity 0.95 and prevalence 0.01. Find the posterior.",
   "steps": ["P(+) = 0.95*0.01 + 0.05*0.99 = 0.059", "P(D|+) = 0.0095/0.059 = 0.161"], "answer": "about 0.16",
   "source": "F3 - Bayes theorem.pdf, page 40", "topic": "Bayes theorem"},
  {"type": "mc", "question": "What is the multiplication rule for two events?",
   "options": ["P(A and B) = P(A|B)P(B)", "P(A and B) = P(A)+P(B)", "P(A or B) = P(A)P(B)", "P(A|B) = P(B|A)"],
   "answer": 0, "explanation": "Multiply the conditional by the condition's probability.", "source": ""},
  {"type": "mc", "question": "Which option is right?", "options": ["a", "b"], "answer": "A"}
 ]}
```

Good luck on Quiz 2!"""

TURKISH_KEYS = """```json
{"başlık": "Koşullu olasılık",
 "sorular": [
  {"tür": "çoktan seçmeli", "soru": "Koşullu olasılık P(A|B) nasıl tanımlanır?",
   "seçenekler": {"A": "P(A ve B)/P(B)", "B": "P(A)P(B)", "C": "P(B)/P(A)", "D": "1 - P(A)"},
   "doğru cevap": "A", "açıklama": "Ortak olasılık koşulun olasılığına bölünür.",
   "kaynak": "F2 s. 1", "konu": "koşullu olasılık"},
  {"tür": "kısa cevap", "soru": "Bayes teoremi neden toplam olasılık kuralına ihtiyaç duyar?",
   "model cevap": "Paydadaki P(A) toplam olasılık kuralıyla bulunur.", "puanlar": ["paydayı söyler"],
   "kaynak": "L3, bölüm 1, 02:30"},
 ]
}
```"""

CLAUDE_MD = """# Practice Quiz: Bayes

## Question 1
What does the total probability rule give in Bayes' theorem?

A) The numerator
B) The denominator P(A)
C) The prior P(B)
D) The likelihood

**Answer:** B
**Explanation:** The denominator is the total probability of the evidence.
**Source:** F3 - Bayes theorem.pdf, page 2

## Question 2
In the medical test example, what three numbers give the posterior?

**Answer:** Sensitivity, specificity and prevalence.
**Source:** L3 part 1 at 05:00
"""


def test_a_quiz_pasted_from_chatgpt_plays_with_its_sources(env):
    k = export(env, weeks=[2, 3], focus="quiz:2")["kit"]
    r = kit.pasted(env.api, "c", CHATGPT % {"kit": k["id"]})
    assert r["ok"] and r["kind"] == "quiz" and r["kit"] == k["id"] and r["focus"] == "Quiz 2"
    assert (r["read"], r["questions"], r["unused"]) == (5, 4, 1)          # two options: no clear answer
    assert r["named"] == 2 and r["found"] + r["no_source"] == 2
    p = env.lib.plan(r["id"])
    assert p["status"] == "done" and p["scope"]["kind"] == "quiz" and p["scope"]["source"] == "import"
    assert p["scope"]["focus"] == "Quiz 2" and p["title"] == "Conditional probability and Bayes"
    q1, q2, q3, q4 = p["result"]["questions"]
    assert (q1["source"]["kind"], q1["source"]["id"], q1["source"]["page"]) == ("file", "r2", 1) and not q1["found"]
    assert q1["los"] == [2] and q1["stressed"] and q1["source"].get("found") is None
    assert (q2["source"]["kind"], q2["source"]["part"], q2["source"]["ts"]) == ("lecture", 1, "02:30")
    assert q2["source"]["day_key"].startswith("c|2026-10-07")
    # page 40 of a 3-page file: found by its words instead, and said so where the page shows the source
    assert q3["found"] and q3["source"]["found"] and q3["source"]["kind"] in ("file", "lecture")
    assert q4["type"] == "mc" and q4["options"][q4["answer"]].startswith("P(A and B) = P(A|B)")
    assert p["result"]["coverage"]["total"] == 4 and p["result"]["source"] == "import"
    brief = practice.quiz_brief(env.lib.plan_brief(r["id"]), [])
    assert brief["source"] == "import" and brief["questions"] == 4 and brief["focus"] == "Quiz 2"


def test_an_imported_quiz_feeds_the_mistakes_loop(env):
    export(env, weeks=[2, 3], focus="quiz:2")
    r = kit.pasted(env.api, "c", CHATGPT % {"kit": "00000000"})
    p = env.lib.plan(r["id"])
    row = practice.save_attempt(env.lib, r["id"], "c", {"answers": [{"n": 1, "got": 0, "chose": 2}, {"n": 2, "got": 1}]})
    carded = cards.add_missed(env.lib, "c", r["id"], p["result"], row["answers"])
    deck = cards.deck(env.svc, "c")
    missed = [c for c in deck if c["origin"] == "quiz"]
    assert carded and [c["id"] for c in missed] == carded and missed[0]["quiz"]["plan_id"] == r["id"]
    assert missed[0]["source"]["id"] == "r2"


def test_loose_json_with_turkish_keys_and_a_trailing_comma(env):
    export(env, weeks=[2, 3])
    r = kit.pasted(env.api, "c", TURKISH_KEYS)
    assert r["ok"] and r["kind"] == "quiz" and r["questions"] == 2 and r["named"] == 2
    q1, q2 = env.lib.plan(r["id"])["result"]["questions"]
    assert q1["type"] == "mc" and q1["options"][q1["answer"]] == "P(A ve B)/P(B)" and q1["source"]["id"] == "r2"
    assert q2["type"] == "short" and q2["model"].startswith("Paydadaki") and q2["source"]["ts"] == "02:30"


def test_markdown_from_claude_and_a_block_per_question(env):
    export(env, weeks=[2, 3])
    r = kit.pasted(env.api, "c", CLAUDE_MD)
    assert r["ok"] and r["questions"] == 2 and r["named"] == 2
    q1, q2 = env.lib.plan(r["id"])["result"]["questions"]
    assert q1["source"]["page"] == 2 and q1["source"]["name"] == "Bayes theorem.pdf"
    assert q2["source"]["kind"] == "lecture" and q2["source"]["ts"] == "05:00"
    blocks = "\n\n".join(
        f"**Question {i}**\n```json\n" + json.dumps({"question": f"Question {i} about the total probability rule?",
                                                     "options": ["one", "two", "three", "four"], "answer": "C",
                                                     "source": "F3 p.2"}) + "\n```" for i in (1, 2, 3))
    r = kit.pasted(env.api, "c", blocks)
    assert r["ok"] and r["questions"] == 3 and r["named"] == 3


def test_an_answer_cut_short_keeps_its_whole_questions(env):
    """The chat stopped in the middle of question 4 (a long answer, "Continue generating" not pressed): the three
    whole questions are read, and the import says so by its counts."""
    export(env, weeks=[2, 3], focus="quiz:2")
    cut = (CHATGPT % {"kit": "00000000"}).split('{"type": "mc", "question": "What is the multiplication rule')[0]
    cut += '{"type": "mc", "question": "What is the multipl'
    assert kit.read_quiz(cut)["questions"] and len(kit.read_quiz(cut)["questions"]) == 3
    r = kit.pasted(env.api, "c", cut)
    assert r["ok"] and r["read"] == 3 and r["questions"] == 3
    cards_cut = (CARDS_JSON % {"kit": "x"}).split('{"front": "Total probability rule"')[0] + '{"front": "Total prob'
    rows, said, _ = kit.read_cards(cards_cut)
    assert said and [c["q"] for c in rows] == ["Conditional probability P(A|B)", "Multiplication rule"]


def test_a_quiz_from_another_chat_finds_its_sources_by_their_words(env):
    """No kit, no references: each question's source is found by its words in the course so far, and labelled."""
    text = json.dumps({"questions": [{"question": "In the medical screening test example, what does a positive test "
                                                  "give with sensitivity, specificity and prevalence?",
                                      "answer": "The posterior probability of disease.", "points": ["posterior"]}]})
    r = kit.pasted(env.api, "c", text)
    assert r["ok"] and r["questions"] == 1 and r["found"] == 1 and r["kit"] == ""
    q = env.lib.plan(r["id"])["result"]["questions"][0]
    assert q["found"] and q["source"]["found"]


def test_what_cannot_be_read_is_said_plainly(env):
    r = kit.pasted(env.api, "c", "Sure! Which topics do you want the quiz on?")
    assert not r["ok"] and "found no quiz and no flashcards" in r["error"]
    assert kit.pasted(env.api, "c", "   ")["error"] == "Paste the answer first."
    assert "no longer in your list" in kit.pasted(env.api, "x", CLAUDE_MD)["error"]
    bad = '{"questions": [{"question": "What is it?", "options": ["a", "b"], "answer": "A"}]}'
    assert "none had a clear right answer" in kit.pasted(env.api, "c", bad)["error"]
    assert not [p for p in env.lib.plans("c") if (p.get("scope") or {}).get("source") == "import"]


# ------------------------------------------------------------------ what comes back: flashcards
CARDS_JSON = """```json
{"kit": "%(kit)s", "cards": [
 {"front": "Conditional probability P(A|B)", "back": "$P(A\\cap B)/P(B)$, for $P(B)>0$", "source": "F2 p.1", "topic": "conditional"},
 {"front": "Multiplication rule", "back": "P(A and B) = P(A|B)P(B)", "source": "L2 part 1 02:30"},
 {"front": "Total probability rule", "back": "Sum of P(A|B_i)P(B_i) over a partition", "source": "F3 p.2"},
 {"front": "Multiplication rule", "back": "the same card twice", "source": "F2 p.2"}
]}
```"""

CARDS_TABLE = """| Ön yüz | Arka yüz | Kaynak |
|---|---|---|
| Örnek uzay | Bir deneyin tüm sonuçlarının kümesi | F1 s. 1 |
| Olay | Örnek uzayın bir alt kümesi | F1 s. 2 |"""

CARDS_TSV = "Bayes theorem\tP(B|A) = P(A|B)P(B)/P(A)\nPosterior\tThe probability after the evidence\n"


def test_cards_pasted_join_the_deck_once(env):
    k = export(env, weeks=[1, 2, 3])["kit"]
    r = kit.pasted(env.api, "c", CARDS_JSON % {"kit": k["id"]})
    assert r["ok"] and r["kind"] == "cards" and (r["read"], r["cards"], r["dupes"]) == (4, 3, 1) and r["named"] == 3
    deck = {c["q"]: c for c in cards.deck(env.svc, "c")}
    assert deck["Multiplication rule"]["source"]["kind"] == "lecture" and deck["Multiplication rule"]["stressed"]
    assert deck["Conditional probability P(A|B)"]["source"]["id"] == "r2" and deck["Total probability rule"]["week"] == 3
    p = env.lib.plan(r["id"])
    assert p["scope"]["kind"] == "cards" and p["scope"]["source"] == "import" and p["status"] == "done"
    again = kit.pasted(env.api, "c", CARDS_JSON % {"kit": k["id"]})
    assert not again["ok"] and again["error"] == "These cards are all in your deck already."
    r = kit.pasted(env.api, "c", CARDS_TABLE)
    assert r["ok"] and r["kind"] == "cards" and r["cards"] == 2 and r["named"] == 2
    assert {"Örnek uzay", "Olay"} <= {c["q"] for c in cards.deck(env.svc, "c")}
    r = kit.pasted(env.api, "c", CARDS_TSV, want="cards")
    assert r["ok"] and r["cards"] == 2


def test_what_a_paste_holds(env):
    assert kit.kind_of(CLAUDE_MD) == "quiz"                    # its "Answer:" lines aren't cards
    assert kit.kind_of(CARDS_JSON % {"kit": "x"}) == "cards" and kit.kind_of(CARDS_TABLE) == "cards"
    assert kit.kind_of("Q: What is a sample space?\nA: The set of all outcomes.") == "cards"
    assert kit.kind_of("Hello there") == ""
    assert kit.read_quiz(CHATGPT % {"kit": "ab12cd34"})["kit"] == "ab12cd34"


def test_a_pasted_quiz_with_code_in_a_question_is_read_whole():
    """A question holding a code block ("```java" inside its JSON string) cut the pasted block there: "found no quiz
    and no flashcards in what you pasted"."""
    quiz = {"questions": [
        {"type": "mc", "question": "What does this print?\n\n```java\nint x = 5;\nSystem.out.println(x + 1);\n```",
         "options": ["6", "5", "x + 1", "nothing"], "answer": "A", "source": "L1"},
        {"type": "mc", "question": "Which keyword makes a constant in Java?", "options": ["final", "static", "const",
                                                                                    "var"], "answer": "A", "source": "L1"}]}
    for fence in ("json", "ieustudia-quiz", ""):
        text = f"Here is the quiz.\n\n```{fence}\n{json.dumps(quiz, indent=2)}\n```\nAnything else?"
        assert kit.kind_of(text) == "quiz", fence
        assert [q["question"][:9] for q in kit.read_quiz(text)["questions"]] == ["What does", "Which key"], fence


def test_sources_as_web_chats_write_them():
    names = [("bayes theorem.pdf", "F3"), ("bayes theorem", "F3")]
    assert kit.normal_source("F3 - Bayes theorem.pdf, page 12", names) == "F3 p.12"
    assert kit.normal_source("F2 s. 4", names) == "F2 p.4" and kit.normal_source("F2, sayfa 4", names) == "F2 p.4"
    assert kit.normal_source("L2 (Part 1, 14:05)", names) == "L2 part 1 14:05"
    assert kit.normal_source("L3, bölüm 2, 1:02:03", names) == "L3 part 2 1:02:03"
    assert kit.normal_source("Bayes theorem.pdf, p. 2", names) == "F3 p.2"
    assert kit.normal_source("the lecture notes", names) == "the lecture notes"
    assert kit.normal_source({"ref": "F1", "page": 2}, names) == {"ref": "F1", "page": 2}


def test_the_api_methods(env, monkeypatch):
    from studia.api import Api

    a = Api.__new__(Api)
    a._services = env.svc
    a._ready = __import__("threading").Event()
    a._ready.set()
    monkeypatch.setattr(Api, "_deck", lambda self, cid: cards.deck(env.svc, cid))
    export(env, weeks=[2, 3])
    r = a.quiz_import("c", CLAUDE_MD)
    assert r["ok"] and r["kind"] == "quiz"
    r = a.quiz_import("c", CARDS_TSV)
    assert r["ok"] and r["kind"] == "cards"
    assert not a.cards_import("c", "nothing here")["ok"]
