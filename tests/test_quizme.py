"""Quiz me from where you are (4.2): "/quiz" in a chat becomes a scored quiz saved to the Practice tab (the AI gets
a practice quiz's instruction, its block is read and checked without AI, its sources found in the chat's lectures),
a quiz on the stressed moments the student picked, and card sessions for one week or one lecture (fake AI, no
network)."""

import datetime as dt
import json
import sys
import time
import types

import pytest

from studia import ai, appconfig, cards, chatquiz, quizzes, store
from studia.chat import ChatService
from studia.config import Settings
from studia.coursesite import Resource, Unit
from studia.domain import Course, Recording, Segment, Transcript
from studia.library import Library
from studia.plans import Scope
from studia.services import Services

DAY = "c|2026-09-23|lecture"
DAY2 = "c|2026-09-30|lecture"


def wait_for(cond, timeout=15.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def _lecture(lib, rid, start, texts):
    rec = Recording(delivery_id=rid, course_id="c", folder_id="f", folder_path="", name="n", start=start,
                    duration_s=3000, viewer_url="v")
    lib.merge_recordings("c", [rec], first_sync=False)
    tr = Transcript(delivery_id=rid, segments=[Segment(a, a + 6, s, "en", 0.9) for a, s in texts], duration_s=3000,
                    speech_s=12, language_seconds={"en": 12}, model="m", device="cpu", engine_version="t",
                    created_at="2026-09-23T12:00:00")
    lib.save_transcript(tr, store.save_transcript(tr))
    lib.set_status(rid, "done")
    return rec


@pytest.fixture
def env(tmp_path, monkeypatch):
    appconfig.update({"ai_provider": "claude"})
    monkeypatch.setattr(ai, "_require_claude", lambda: sys.executable)
    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="c", title="2026-1 MATH 240 P 1", code="MATH 240", name="Probability", term="T")])
    lib.merge_units("c", [Unit("u1", 0, "Hafta 1", dt.date(2026, 9, 21), dt.date(2026, 9, 27), "",
                               [Resource("r1", "Week01", "Week01.pdf", "pdf", "pdf", "u", "d")]),
                          Unit("u2", 1, "Hafta 2", dt.date(2026, 9, 28), dt.date(2026, 10, 4), "", [])], first_sync=True)
    lib.set_material_file("r1", str(tmp_path / "Week01.pdf"), 10, "u")
    (tmp_path / "Week01.pdf").write_bytes(b"%PDF")
    lib.set_material_text("r1", ["Conditional probability: P(A|B) = P(A and B)/P(B), defined when P(B) > 0.",
                                 "Bayes theorem: P(B|A) = P(A|B)P(B)/P(A), with the total probability rule below."])
    lib.set_course_info("c", syllabus={"outcomes": ["use conditional probability", "apply Bayes' theorem"],
                                       "language": "English",
                                       "weeks": [{"no": 1, "subject": "Conditional probability", "preparation": ""}]})
    _lecture(lib, "r", dt.datetime(2026, 9, 23, 9, 0), [
        (0, "Today: conditional probability, given that B happened, restricts the sample space to B."),
        (60, "I will ask a Bayes question on the quiz, so practise the total probability rule."),
        (300, "The multiplication rule gives the probability of an intersection from a conditional probability.")])
    events = []
    ns = types.SimpleNamespace(lib=lib, emit=events.append, output_root=tmp_path / "out", settings=Settings())
    ns.day = types.MethodType(Services.day, ns)
    svc = ChatService(ns)
    yield types.SimpleNamespace(lib=lib, svc=svc, events=events, ns=ns, tmp=tmp_path, mp=monkeypatch)
    svc.shutdown()


class FakeSession:
    """A chat's AI: the answer it was given, and what it got."""

    def __init__(self, answer):
        self.answer, self.got = answer, []

    def alive(self):
        return True

    def ask(self, content, on_text, stop):
        self.got.append(content)
        on_text(self.answer)
        return {"text": self.answer, "usage": {"input_tokens": 10, "output_tokens": 50}, "stopped": False}


def ask(env, chat_id, text, answer):
    fake = FakeSession(answer)
    env.mp.setattr(env.svc, "_session", lambda chat: (fake, None))
    seq = env.svc.send(chat_id, text)["seq"]
    done = lambda: [e for e in env.events if e.get("type") == "chat" and e["seq"] == seq and e["state"] != "streaming"]  # noqa: E731
    assert wait_for(lambda: done()), "no answer"
    return done()[-1], fake


QUIZ = {"title": "Conditional probability", "topics": ["Conditional probability", "Bayes"], "questions": [
    {"type": "mc", "question": "What does conditioning on B do?", "options": ["Restricts the sample space to B",
     "Doubles P(B)", "Makes A and B independent", "Nothing"], "answer": "A", "explanation": "It restricts it.",
     "wrong": {"B": "No.", "C": "No.", "D": "No."}, "source": "L1 part 1 00:00", "los": [1]},
    {"type": "short", "question": "Which rule did the lecturer say to practise for the quiz?",
     "answer": "The total probability rule.", "points": ["total probability rule"], "source": "P1 01:00"},
    {"type": "short", "question": "How does the multiplication rule give an intersection probability?",
     "answer": "From a conditional probability times a marginal.", "points": ["conditional", "intersection"]}]}


def test_quiz_in_a_chat_is_saved_scored_and_its_sources_found(env):
    chat = env.svc.create("c", [DAY])
    answer = "Here is your quiz.\n```ieustudia-quiz\n" + json.dumps(QUIZ) + "\n```"
    e, fake = ask(env, chat["id"], "/quiz 3", answer)
    assert e["state"] == "done" and e["text"] == answer                    # the answer as written: the page hides it
    asked = fake.got[0][-1]["text"]                                        # the AI got the quiz's instruction
    assert asked.startswith("(From the IEUStudia app") and "exactly 3 exam-style questions" in asked
    assert "```ieustudia-quiz" in asked and "=== COURSE MATERIAL ===" not in asked
    assert "L1 = the lecture of 23 Sep 2026 (week 1)" in asked and "Quiz the student on the lectures" in asked
    assert "never call an option by its letter" in asked and "in English" in asked        # write_prompt's own rules
    assert "S1 (tied to an exam) [L1 part 1 01:00]" in asked                               # what the lecturer stressed
    msgs = env.lib.chat_messages(chat["id"])
    assert msgs[0]["text"] == "/quiz 3"                                     # the chat shows what the student typed
    assert env.lib.get_chat(chat["id"])["title"] == "Conditional probability"     # named after its quiz, not "/quiz 3"
    made = e["usage"]["quiz"]
    assert made["questions"] == 3 and made["title"] == "Conditional probability"
    plan = env.lib.plan(made["id"])
    assert plan["status"] == "done" and plan["scope"]["kind"] == "quiz" and plan["scope"]["source"] == "chat"
    assert plan["scope"]["chat_id"] == chat["id"] and plan["scope"]["days"] == [DAY] and plan["scope"]["weeks"] == [1]
    q1, q2, q3 = plan["result"]["questions"]
    assert (q1["source"]["kind"], q1["source"]["t"], q1["found"]) == ("lecture", 0.0, False)
    assert (q2["source"]["t"], q2["found"]) == (60.0, False)                # "P1 01:00": the chat's one lecture
    assert q3["source"] and q3["source"]["ts"] == "05:00" and q3["found"]   # named none: found by its words
    assert plan["result"]["coverage"]["lectures_cut"] == [] and plan["result"]["source"] == "chat"
    assert json.loads(json.dumps(env.lib.chat_messages(chat["id"])[1]["usage"]))["quiz"]["id"] == made["id"]
    ev = [x for x in env.events if x.get("type") == "plan"]
    assert ev and ev[-1]["id"] == made["id"] and ev[-1]["source"] == "chat" and ev[-1]["kind"] == "quiz"
    # a follow-up "/quiz" is on the conversation (most of all the last answer)
    e2, fake2 = ask(env, chat["id"], "/quiz", "No quiz this time, sorry.")
    assert "what this conversation covered" in fake2.got[0][-1]["text"] and "exactly 5" in fake2.got[0][-1]["text"]
    assert e2["usage"]["quiz_error"].startswith("The quiz in this answer could not be read")
    assert e2["text"] == "No quiz this time, sorry." and "quiz" not in e2["usage"]
    assert len([p for p in env.lib.plans("c") if p["scope"].get("source") == "chat"]) == 1
    assert env.lib.get_chat(chat["id"])["title"] == "Conditional probability"


def test_a_broken_block_leaves_the_answer_and_saves_nothing(env):
    chat = env.svc.create("c", [DAY])
    broken = 'Sure!\n```ieustudia-quiz\n{"questions": [{"type": "mc", "question": "Half a quiz'
    e, _ = ask(env, chat["id"], "/quiz 5", broken)
    assert e["state"] == "done" and e["text"] == broken and "quiz_error" in e["usage"]
    assert env.lib.plans("c") == []
    e, fake = ask(env, chat["id"], "What is Bayes?", "It reverses a conditional probability.")
    assert fake.got[0][-1]["text"] == "What is Bayes?" and "quiz" not in e["usage"] and "quiz_error" not in e["usage"]


def test_the_offline_model_is_told_quizzes_need_chatgpt_or_claude(env):
    chat = env.svc.create("c", [DAY])
    env.mp.setattr(ai, "provider", lambda: "local")
    with pytest.raises(RuntimeError, match="Quizzes need ChatGPT or Claude"):
        env.svc.send(chat["id"], "/quiz 5")


@pytest.mark.parametrize("text, want", [
    ("/quiz", {"n": 5, "topics": ""}), ("/quiz 10", {"n": 10, "topics": ""}), ("  /Quiz 3 bayes rule ", {"n": 3, "topics": "bayes rule"}),
    ("/quiz 99", {"n": 20, "topics": ""}), ("/quiz 0", {"n": 1, "topics": ""}), ("/quizzes", None), ("quiz me", None),
    ("/plan quiz 2", None)])
def test_the_quiz_command(text, want):
    assert chatquiz.command(text) == want


def test_the_chat_quiz_prompt_names_each_reference_as_the_chat_does():
    facts = {"span": "weeks 1–2", "pack": "", "language": "English", "taught_in": "English", "quant": False,
             "stressed_lines": "", "focus": {"name": "Quiz 2", "week": 3, "los": [{"no": 1, "text": "use Bayes"}]},
             "lectures": [{"ref": "L1", "day_key": DAY2, "date": "2026-09-30", "week": 2, "parts": []},
                          {"ref": "L2", "day_key": DAY, "date": "2026-09-23", "week": 1, "parts": []}],
             "files": [{"ref": "F1", "id": "r1", "name": "Week01", "pages": 2}]}
    text = chatquiz.prompt(facts, 4, "Bayes", True, [DAY, DAY2], {"r1": "Week01.pdf"})
    assert "L1 = the lecture of 30 Sep 2026 (week 2) (L2 in this chat)" in text
    assert "F1 = the file Week01.pdf (2 pages" in text and "LO1: use Bayes" in text
    assert "The student also asked: Bayes" in text and "1. The lectures and files of this chat" in text
    assert text.rstrip().endswith("The app turns it into the quiz.")
    assert chatquiz.block("ok\n```ieustudia-quiz\n{\"a\": 1}\n```\nbye") == '{"a": 1}'
    assert chatquiz.block("```json\n[]\n```") == "```json\n[]\n```"


CODE_QUIZ = {"questions": [
    {"type": "mc", "question": "What does this print?\n\n```java\nint x = 5;\nSystem.out.println(x + 1);\n```",
     "options": ["6", "5", "x + 1", "nothing"], "answer": "A", "source": "L1"},
    {"type": "mc", "question": "Which keyword makes a constant in Java?", "options": ["final", "static", "const", "var"],
     "answer": "A", "source": "L1"}]}


def test_a_question_with_code_in_it_keeps_the_chat_quiz_whole():
    """A question holding a code block ("```java" inside its JSON string) closed the ```ieustudia-quiz block there:
    the quiz in the answer "could not be read" and nothing was saved."""
    for body in (json.dumps(CODE_QUIZ, indent=2), json.dumps(CODE_QUIZ)):
        answer = f"Here is your quiz.\n\n```ieustudia-quiz\n{body}\n```\nGood luck!"
        assert json.loads(chatquiz.block(answer)) == CODE_QUIZ
        assert len(quizzes.read_answer(chatquiz.block(answer))["questions"]) == 2
    glued = f"Here is your quiz.\n\n```ieustudia-quiz\n{json.dumps(CODE_QUIZ)}```"     # (its fence right after it)
    assert json.loads(chatquiz.block(glued)) == CODE_QUIZ


# ------------------------------------------------------------------ a quiz on the moments the student picked
def test_a_quiz_on_picked_stressed_moments(env):
    from studia import stressed

    items = stressed.course_items(env.lib, "c")
    hit = next(it for it in items if "Bayes question" in it["text"])
    sc = Scope.from_json({"kind": "quiz", "weeks": [1], "days": [DAY], "picked": True, "stressed_ids": [hit["id"], 5]})
    assert sc.stressed_ids == [hit["id"], "5"]
    facts = quizzes.gather(env.lib, env.ns.settings, "c", sc)
    assert facts["stressed_only"] and "Bayes question" in facts["stressed_lines"]
    prompt = quizzes.write_prompt(sc, facts, 5, quizzes.mix(5, "mixed", False))
    assert "The student picked the S moments below" in prompt and "more questions on these" not in prompt
    none = quizzes.gather(env.lib, env.ns.settings, "c", Scope.from_json({"kind": "quiz", "weeks": [1], "days": [DAY],
                                                                          "picked": True, "stressed_ids": ["nope"]}))
    assert not none["stressed_only"] and none["stressed_lines"] == ""
    plain = quizzes.gather(env.lib, env.ns.settings, "c", Scope.from_json({"kind": "quiz", "weeks": [1]}))
    assert not plain["stressed_only"] and "more questions on these" in quizzes.write_prompt(
        Scope(kind="quiz"), plain, 5, quizzes.mix(5, "mixed", False))


# ------------------------------------------------------------------ card sessions by week and by lecture
NOTES1 = "Q: What is $P(A\\mid B)$?\nA: $P(A\\cap B)/P(B)$ (Part 1, 00:30)\n\nQ: What does conditioning do?\nA: Restricts the sample space.\n"
NOTES2 = "Q: What is a random variable?\nA: A function from outcomes to numbers.\n"


@pytest.fixture
def api(tmp_path):
    from studia.api import Api
    from studia.outputs.writer import course_dir, day_stem

    a = Api(Services())
    a._push = (events := []).append
    lib = a._s.lib
    lib.upsert_courses([Course(id="c", title="2026-1 MATH 240 P 1", code="MATH 240", name="Probability", term="T")])
    lib.merge_units("c", [Unit("u1", 0, "Week 1", dt.date(2026, 9, 21), dt.date(2026, 9, 27), "", []),
                          Unit("u2", 1, "Week 2", dt.date(2026, 9, 28), dt.date(2026, 10, 4), "", [])], first_sync=True)
    course = lib.get_course("c")
    for rid, start, notes in (("d1", dt.datetime(2026, 9, 23, 9), NOTES1), ("d2", dt.datetime(2026, 9, 30, 9), NOTES2)):
        rec = _lecture(lib, rid, start, [(0, "Today some probability."), (30, "More probability here.")])
        path = a._s._notes_path(course_dir(a._s.output_root, course, lib.courses(True)),
                                day_stem(course, rec.start.date(), rec.kind), "flashcards")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(notes, encoding="utf-8")
    return a


def test_card_sessions_for_one_lecture_and_one_week(api):
    days = sorted({r.day_key for r in api._s.lib.recordings("c")})
    first = api.cards_session("c", [], [days[0]])
    assert first["ok"] and first["total"] == 2 and len(first["cards"]) == 2
    assert {c["source"]["day_key"] for c in first["cards"]} == {days[0]}
    week2 = api.cards_session("c", [2])
    assert [c["q"] for c in week2["cards"]] == ["What is a random variable?"] and week2["total"] == 1
    assert len(api.cards_session("c")["cards"]) == 3
    # graded in the lecture's session: kept in the deck's own schedule
    for c in first["cards"]:
        assert api.cards_grade("c", c["id"], "easy")["ok"]
    again = api.cards_session("c", [], [days[0]])
    assert again["cards"] == [] and again["total"] == 2                    # all learnt: nothing today, two in all
    tab = api.practice_course("c")["deck"]
    assert tab["week_counts"] == {"1": {"total": 2, "today": 0}, "2": {"total": 1, "today": 1}}
    assert tab["weeks"] == [1, 2]


def test_week_counts_respect_the_new_cards_left_today(tmp_path):
    lib = Library(tmp_path / "lib.db")
    deck = [{"id": f"k{i}", "week": 1 + i % 2, "origin": "files", "source": {}} for i in range(6)]
    now = dt.datetime(2026, 11, 19, 12)
    assert cards.week_counts(lib, deck, "c", now, new_per_day=2) == {1: {"total": 3, "today": 2}, 2: {"total": 3, "today": 2}}
    assert cards.picked(deck, {1}) == [deck[0], deck[2], deck[4]]
