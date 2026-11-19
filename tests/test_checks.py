"""Checked answers (4.2): a worked problem's key computed again from the AI's own "check" (confirmed, or flagged and
solved again in one batched request, once per quiz, also when the quiz is resumed), the result a student types
checked by the app, the written answers of a try marked by the AI in one request (with a fake AI: no network), the
student's own mark over any of them, and practice.mastery weighing each mark by who gave it."""

import datetime as dt
import json
import threading
import time
import types

import pytest

from studia import numcheck, practice, quizzes, store
from studia.config import Settings
from studia.coursesite import Resource, Unit
from studia.domain import Course, Recording, Segment, Transcript
from studia.library import Library
from studia.plans import PlanRunner, Scope

GOOD = {"type": "problem", "question": "A 12 V source feeds 2 kΩ and 4 kΩ in series. Find the voltage across 2 kΩ.",
        "steps": ["$v_2 = 12 \\cdot 2/(2+4)$"], "answer": "$v_2 = 4$ V",
        "check": {"expr": "12*2/(2+4)", "value": 4, "unit": "V"}, "source": "F1 p.1"}
BAD = {"type": "problem", "question": "A 10 mA current flows through 4.7 kΩ. Find the power it uses.",
       "steps": ["$P = I^2 R = (0.01)^2 \\cdot 4700$"], "answer": "$P = 0.53$ W",
       "check": {"expr": "0.01^2*4700", "value": 0.53, "unit": "W"}, "source": "F1 p.2"}
FORMULA = {"type": "problem", "question": "Differentiate $f(x) = x^3 + 2x$ with respect to $x$.",
           "steps": ["Power rule"], "answer": "$f'(x) = 3x^2 + 2$", "source": "F1 p.1"}
MC = {"type": "mc", "question": "Which law links voltage and current in a resistor?",
      "options": ["Ohm's law", "Kirchhoff's law", "Faraday's law", "Lenz's law"], "answer": "A", "source": "F1 p.1"}


# ------------------------------------------------------------------ the prompt and the checks
def test_the_prompt_asks_for_a_check_only_with_written_questions():
    facts = {"span": "week 1", "pack": "", "lectures": [], "language": "English", "taught_in": "English"}
    sc = Scope(kind="quiz")
    p = quizzes.write_prompt(sc, facts, 5, {"mc": 2, "short": 1, "problem": 2})
    assert '"check": {"expr": "12*4.7/(4.7+2.2)", "value": 8.17, "unit": "V"}' in p
    assert '"check" (every worked problem, and a short answer whose answer is one number)' in p
    assert "Leave \"check\" out when the answer is not one number" in p
    assert '"check" (every' not in quizzes.write_prompt(sc, facts, 5, {"mc": 5, "short": 0, "problem": 0})


def test_the_check_is_kept_and_the_key_confirmed_or_flagged():
    facts = {"files": [{"ref": "F1", "id": "r1", "name": "W1.pdf", "pages": 3, "week": 1}], "lectures": []}
    quiz = quizzes.clean_quiz({"questions": [GOOD, BAD, FORMULA, MC,
                                             {**GOOD, "question": "Same circuit, other words: the 2 kΩ voltage?",
                                              "check": "12*2/(2+4)"}]}, facts)
    g, b, f, m, e = quiz["questions"]
    assert g["check"] == {"expr": "12*2/(2+4)", "value": 4, "unit": "V"} and g["verified"] and g["numeric"]
    assert b["unverified"] and "verified" not in b and "numeric" not in b         # 0.47 W, not 0.53
    assert "check" not in f and "verified" not in f and "unverified" not in f and "numeric" not in f
    assert "check" not in m and "numeric" not in m
    assert e["check"] == {"expr": "12*2/(2+4)", "unit": ""} and e["verified"]       # a bare expression is a check
    cov = quizzes.coverage({**quiz, "topics": []}, {"files": facts["files"], "lectures": []})
    assert (cov["checked"], cov["unconfirmed"]) == (2, 1)


def test_a_repaired_key_replaces_the_old_one_only_when_the_app_confirms_it():
    facts = {"files": [], "lectures": []}
    qs = quizzes.clean_quiz({"questions": [BAD, {**BAD, "question": "Another power question, 20 mA through 1 kΩ?",
                                                 "answer": "$P = 0.5$ W", "check": {"expr": "0.02^2*1000", "value": 0.5,
                                                                                   "unit": "W"}}]}, facts)["questions"]
    assert all(q["unverified"] for q in qs)
    prompt = quizzes.repair_prompt(qs)
    assert "[1] A 10 mA current" in prompt and "The app computes that expression to 0.47: not the answer given." in prompt
    assert "Answer given: $P = 0.53$ W" in prompt and '"check" (every worked problem' in prompt
    answer = json.dumps({"questions": [
        {"n": 1, "steps": ["$P = (0.01)^2 \\cdot 4700 = 0.47$ W"], "answer": "$P = 0.47$ W",
         "check": {"expr": "0.01^2*4700", "value": 0.47, "unit": "W"}},
        {"n": 2, "answer": "$P = 0.5$ W", "check": {"expr": "0.02^2*1000", "value": 0.5, "unit": "W"}}]})
    assert quizzes.apply_repair(qs, answer) == 1
    assert qs[0]["verified"] and qs[0]["repaired"] and qs[0]["model"] == "$P = 0.47$ W" and "unverified" not in qs[0]
    assert qs[0]["steps"] == ["$P = (0.01)^2 \\cdot 4700 = 0.47$ W"] and qs[0]["n"] == 1
    assert qs[1]["unverified"] and qs[1]["model"] == "$P = 0.5$ W"          # 0.4 W still: it stays flagged
    assert quizzes.apply_repair(qs, "no JSON at all") == 0


# ------------------------------------------------------------------ the whole run: one repair request at most
@pytest.fixture
def env(tmp_path):
    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="c", title="2026-1 EEE 215 P 1", code="EEE 215", name="Circuits", term="T")])
    lib.merge_units("c", [Unit("u1", 0, "Hafta 1", dt.date(2026, 9, 21), dt.date(2026, 9, 27), "",
                               [Resource("r1", "Week01", "Week01.pdf", "pdf", "pdf", "u", "d")])], first_sync=True)
    lib.set_material_file("r1", str(tmp_path / "Week01.pdf"), 10, "u")
    (tmp_path / "Week01.pdf").write_bytes(b"%PDF")
    lib.set_material_text("r1", ["Voltage divider: v2 = vs R2/(R1+R2) for two resistors in series.",
                                 "Power in a resistor: P = I^2 R = V^2/R, in watts."])
    lib.set_course_info("c", syllabus={"outcomes": ["analyse resistive circuits"], "language": "English",
                                       "weeks": [{"no": 1, "subject": "Resistive circuits", "preparation": ""}]})
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


def test_flagged_keys_are_solved_again_in_one_request_once(env):
    asked = []
    fixed = json.dumps({"questions": [{"n": 2, "steps": ["$P = 0.47$ W"], "answer": "$P = 0.47$ W",
                                       "check": {"expr": "0.01^2*4700", "value": 0.47, "unit": "W"}}]})
    answers = iter([json.dumps({"title": "Resistive circuits", "questions": [GOOD, BAD, FORMULA]}), fixed])

    def ask_text(system, content, model="sonnet", effort="low", cancel=None, on_text=None):
        asked.append((system, content[0]["text"]))
        return {"text": next(answers), "usage": {"input_tokens": 100, "output_tokens": 50}, "model": model}

    runner = PlanRunner(env.svc, ask_text=ask_text)
    pid = runner.create("c", {"kind": "quiz", "weeks": [1], "questions": 5, "qtypes": "problems", "focus": ""})
    runner.start(pid)
    p = wait_done(env.lib, pid)
    assert p["status"] == "done", p
    assert len(asked) == 2 and asked[1][0] == quizzes.REPAIR_SYSTEM
    assert "[2] A 10 mA current" in asked[1][1] and "[1] A 12 V" not in asked[1][1]    # only the flagged one
    assert "COURSE MATERIAL" not in asked[1][1]                                      # (no material: small)
    full = env.lib.plan(pid)
    g, b, f = full["result"]["questions"]
    assert g["verified"] and b["verified"] and b["repaired"] and b["model"] == "$P = 0.47$ W"
    assert full["result"]["coverage"]["checked"] == 2 and full["result"]["coverage"]["unconfirmed"] == 0
    assert full["usage"]["repair"]["input_tokens"] == 100 and full["data"]["repair"] == fixed
    # resumed (the check stage again, as after a restart): the saved repair is used, no new request
    plan = env.lib.plan(pid)
    quizzes.run(runner, plan, Scope.from_json(plan["scope"]), plan["data"], plan["usage"], "sonnet", threading.Event())
    assert len(asked) == 2 and env.lib.plan(pid)["result"]["questions"][1]["verified"]


def test_a_repair_that_fails_leaves_the_quiz_usable_and_is_not_asked_again(env, monkeypatch):
    asked = []

    def ask_text(system, content, model="sonnet", effort="low", cancel=None, on_text=None):
        asked.append(system)
        if system == quizzes.REPAIR_SYSTEM:
            raise RuntimeError("You've hit your limit")
        return {"text": json.dumps({"title": "Power", "questions": [GOOD, BAD]}), "usage": {}}

    runner = PlanRunner(env.svc, ask_text=ask_text)
    pid = runner.create("c", {"kind": "quiz", "weeks": [1], "questions": 5, "qtypes": "problems"})
    runner.start(pid)
    assert wait_done(env.lib, pid)["status"] == "done"
    plan = env.lib.plan(pid)
    assert plan["data"]["repair"] == "" and plan["result"]["questions"][1]["unverified"]
    quizzes.run(runner, plan, Scope.from_json(plan["scope"]), plan["data"], plan["usage"], "sonnet", threading.Event())
    assert asked.count(quizzes.REPAIR_SYSTEM) == 1
    body = quizzes.document_body(env.lib.plan(pid))                 # the printed key says so too
    assert body.count("The app could not confirm this answer: check it against its source.") == 1


def test_a_quiz_with_every_key_confirmed_makes_no_repair_request(env):
    asked = []

    def ask_text(system, content, model="sonnet", effort="low", cancel=None, on_text=None):
        asked.append(system)
        return {"text": json.dumps({"title": "Divider", "questions": [GOOD, FORMULA, MC]}), "usage": {}}

    runner = PlanRunner(env.svc, ask_text=ask_text)
    pid = runner.create("c", {"kind": "quiz", "weeks": [1], "questions": 5})
    runner.start(pid)
    assert wait_done(env.lib, pid)["status"] == "done" and asked == [quizzes.SYSTEM]
    assert "repair" not in env.lib.plan(pid)["data"]


# ------------------------------------------------------------------ the API: a typed result, the AI's marks
@pytest.fixture
def api(tmp_path, monkeypatch):
    from studia.api import Api
    from studia.services import Services

    a = Api(Services())
    a._push = (events := []).append
    a.events = events
    lib = a._s.lib
    lib.upsert_courses([Course(id="c", title="2026-1 EEE 215 P 1", code="EEE 215", name="Circuits", term="T")])
    lib.set_course_info("c", syllabus={"outcomes": ["analyse resistive circuits", "explain power"],
                                       "language": "English", "weeks": [{"no": 1, "subject": "Circuits"}]})
    qs = [{"n": 1, "type": "problem", "q": "Find $v_2$ across 2 kΩ of 2 kΩ + 4 kΩ on 12 V.", "steps": ["divide"],
           "model": "$v_2 = 4$ V", "check": {"expr": "12*2/(2+4)", "value": 4, "unit": "V"}, "los": [1],
           "topic": "Voltage divider"},
          {"n": 2, "type": "short", "q": "Why is a voltmeter's resistance high?",
           "model": "So that it draws almost no current.", "points": ["little current", "doesn't change the circuit"],
           "los": [1], "topic": "Meters"},
          {"n": 3, "type": "mc", "q": "Which law?", "options": ["Ohm", "KCL", "KVL", "Faraday"], "answer": 0,
           "wrong": ["", "a", "b", "c"], "why": "v = iR", "los": [1], "topic": "Ohm's law"},
          {"n": 4, "type": "problem", "q": "Find the power of 10 mA in 4.7 kΩ.", "steps": ["P = I^2 R"],
           "model": "$P = 0.53$ W", "check": {"expr": "0.01^2*4700", "value": 0.53, "unit": "W"}, "los": [2],
           "topic": "Power"},
          {"n": 5, "type": "short", "q": "What is a node?", "model": "A point where elements meet.", "los": [2],
           "topic": "Nodes"}]
    for q in qs:
        numcheck.check_key(q)
    lib.create_plan("q1", "c", Scope(kind="quiz", weeks=[1], questions=5).to_json(), "Circuits")
    lib.update_plan("q1", status="done", result={"kind": "quiz", "title": "Circuits", "language": "English",
                                                 "questions": qs})
    monkeypatch.setattr("studia.checks._ai_ready", lambda: None)
    return a


def test_a_typed_result_is_checked_by_the_app(api):
    assert api.quiz_check_number("q1", 1, "4 V") == {"ok": True, "verdict": "right"}
    assert api.quiz_check_number("q1", 1, "4,0") == {"ok": True, "verdict": "right"}
    assert api.quiz_check_number("q1", 1, "4000 mV")["verdict"] == "right"
    assert api.quiz_check_number("q1", 1, "6 V") == {"ok": True, "verdict": "wrong", "unit": False}
    assert api.quiz_check_number("q1", 1, "4 A") == {"ok": True, "verdict": "wrong", "unit": True}
    assert api.quiz_check_number("q1", 1, "v2 = 12/3") == {"ok": True, "verdict": "right"}
    assert api.quiz_check_number("q1", 1, "about four")["verdict"] == "unknown"      # the student marks it
    assert api.quiz_check_number("q1", 4, "0.47 W")["verdict"] == "unknown"        # a key the app couldn't confirm
    assert api.quiz_check_number("q1", 2, "0.5")["verdict"] == "unknown"           # no number in the key
    assert not api.quiz_check_number("q9", 1, "4")["ok"] and not api.quiz_check_number("q1", 9, "4")["ok"]


TRY = [{"n": 1, "got": 1, "mark": "app", "app_got": 1, "text": "divider", "final": "4 V"},
       {"n": 2, "got": 1, "mark": "self", "text": "so it doesn't load the circuit"},
       {"n": 3, "got": 0, "chose": 2, "mark": "app"},
       {"n": 4, "got": 0.5, "mark": "self", "app_got": 0, "text": "P = I^2 R = 0.47 W", "final": "0.47 W"},
       {"n": 5, "got": 0, "skipped": True}]


def wait_event(api, phase, timeout=10):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        hit = [e for e in api.events if e.get("type") == "quizmark" and e.get("phase") == phase]
        if hit:
            return hit[-1]
        time.sleep(0.02)
    raise AssertionError(f"no {phase} event: {api.events}")


def test_written_answers_are_marked_by_the_ai_in_one_request(api, monkeypatch):
    sent = []

    def ask_json(system, text, **kw):
        sent.append(text)
        return {"marks": [{"n": 2, "mark": 0.5, "missing": "You didn't say it draws almost no current."},
                          {"n": 4, "mark": "1", "missing": "ignored when right"}, {"n": 3, "mark": 0},
                          {"n": 7, "mark": 1}]}, {"usage": {}}

    monkeypatch.setattr("studia.ai.ask_json", ask_json)
    row = api.quiz_attempt("q1", {"answers": TRY})["attempt"]
    assert row["score"] == 2.5
    r = api.quiz_mark("q1", row["id"])
    assert r == {"ok": True, "n": 2}
    done = wait_event(api, "done")
    assert len(sent) == 1
    text = sent[0]
    assert "[2] Short answer" in text and "[4] Worked problem" in text and "The student's final result: 0.47 W" in text
    assert "[1]" not in text and "[3]" not in text and "[5]" not in text     # the app's checks, a choice, a skip
    assert "(The app could not confirm this result: check it yourself.)" in text and "in English" in text
    assert "A full answer must say: little current; doesn't change the circuit" in text
    by_n = {a["n"]: a for a in done["answers"]}
    assert by_n[2]["got"] == 0.5 and by_n[2]["mark"] == "ai" and by_n[2]["ai_why"].startswith("You didn't say")
    assert by_n[4]["got"] == 1 and by_n[4]["mark"] == "ai" and by_n[4]["ai_why"] == ""
    assert by_n[3]["got"] == 0 and by_n[3]["mark"] == "app" and "ai_got" not in by_n[3]
    assert done["score"] == 2.5 and done["marked"] == 2
    saved = practice.attempts(api._s.lib, "q1")[0]
    assert saved["score"] == 2.5 and saved["answers"][1]["mark"] == "ai"
    assert [e["phase"] for e in api.events if e.get("type") == "quizmark"][0] == "start"
    # nothing left to mark: said plainly; and the AI is free again
    assert api.quiz_mark("q1", row["id"]) == {"ok": False, "error": "There are no written answers to mark."}
    assert api._ai_cancel is None


def test_an_unreadable_answer_or_a_busy_ai_is_said_plainly(api, monkeypatch):
    monkeypatch.setattr("studia.ai.ask_json", lambda *a, **k: ({"verdict": "fine"}, {"usage": {}}))
    row = api.quiz_attempt("q1", {"answers": TRY})["attempt"]
    api.quiz_mark("q1", row["id"])
    err = wait_event(api, "error")
    assert "marks could not be read" in err["message"] and not err["cancelled"]
    assert practice.attempts(api._s.lib, "q1")[0]["answers"][1]["mark"] == "self"       # nothing changed
    api._ai_cancel = threading.Event()
    assert "already working" in api.quiz_mark("q1", row["id"])["error"]
    api._ai_cancel = None
    assert not api.quiz_mark("q1", "nope")["ok"]


def test_the_student_can_change_any_mark_but_a_choice(api, monkeypatch):
    monkeypatch.setattr("studia.ai.ask_json", lambda *a, **k: ({"marks": [{"n": 2, "mark": 0, "missing": "No."},
                                                                          {"n": 4, "mark": 0.5}]}, {"usage": {}}))
    row = api.quiz_attempt("q1", {"answers": TRY})["attempt"]
    api.quiz_mark("q1", row["id"])
    wait_event(api, "done")
    r = api.quiz_mark_set("q1", row["id"], 2, 1)
    a2 = r["attempt"]["answers"][1]
    assert a2["got"] == 1 and a2["mark"] == "self" and a2["ai_got"] == 0 and r["attempt"]["score"] == 2.5
    assert api.quiz_mark_set("q1", row["id"], 2, 0)["attempt"]["answers"][1]["mark"] == "ai"     # the AI's again
    a1 = api.quiz_mark_set("q1", row["id"], 1, 0)["attempt"]["answers"][0]
    assert a1["mark"] == "self" and a1["got"] == 0
    assert api.quiz_mark_set("q1", row["id"], 1, 1)["attempt"]["answers"][0]["mark"] == "app"    # the app's again
    assert api.quiz_mark_set("q1", row["id"], 3, 1)["ok"] is False        # a multiple choice: the app's only
    assert api.quiz_mark_set("q1", row["id"], 2, 0.7)["ok"] is False
    # an answer marked wrong now joins the cards (as a try's missed questions do)
    from studia.cards import quiz_card_id, states

    st = states(api._s.lib, "c")
    assert quiz_card_id("q1", "Why is a voltmeter's resistance high?") in st


def test_mastery_weighs_a_mark_by_who_gave_it(api, monkeypatch):
    """LO1: a written answer the student marked right (half weight) and a choice the app marked wrong (full): a
    third. Once the AI marks the written one right too, it weighs fully: a half."""
    row = api.quiz_attempt("q1", {"answers": [{"n": 2, "got": 1, "mark": "self", "text": "it barely draws current"},
                                              {"n": 3, "got": 0, "chose": 1, "mark": "app"}]})["attempt"]
    lo1 = lambda: next(r for r in practice.mastery(api._s.lib, "c")["los"] if r["no"] == 1)    # noqa: E731
    assert lo1()["share"] == pytest.approx(0.33, abs=0.01)
    monkeypatch.setattr("studia.ai.ask_json", lambda *a, **k: ({"marks": [{"n": 2, "mark": 1}]}, {"usage": {}}))
    api.quiz_mark("q1", row["id"])
    wait_event(api, "done")
    assert lo1()["share"] == pytest.approx(0.5, abs=0.01)
    # the weak-spot quiz hears what was typed and the result in its box
    api.quiz_attempt("q1", {"answers": [{"n": 1, "got": 0, "mark": "app", "app_got": 0, "text": "12/2",
                                         "final": "6 V"}]})
    w = practice.weak_spots(api._s.lib, "c")
    assert next(m for m in w["missed"] if m["n"] == 1)["typed"] == "12/2 · result 6 V"


def test_the_mistakes_list_knows_each_answers_try(api):
    row = api.quiz_attempt("q1", {"answers": [{"n": 2, "got": 0, "mark": "self", "text": "no idea"}]})["attempt"]
    m = practice.mistakes(api._s.lib, "c")
    assert m[0]["answer"]["attempt"] == row["id"]


@pytest.mark.parametrize("v, want", [(1, 1.0), (0, 0.0), (0.5, 0.5), ("0,5", 0.5), ("1/2", 0.5), ("½", 0.5),
                                     ("partly", 0.5), ("right", 1.0), (0.4, 0.5), (2, None), (True, None),
                                     ("great", None), (-1, None)])
def test_a_mark_as_the_ai_writes_it(v, want):
    from studia.checks import _half

    assert _half(v) == want
