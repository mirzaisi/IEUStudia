"""4.2.0's last fixes, through the app's own code with fakes (no AI, no network): a flashcard with no week (a missed
question of a pasted quiz whose source wasn't found) stays out of every set of some weeks (a cram, "Study one week",
the Exam room's count) and in the whole deck; an AI program that closes before it reads the request (Python's "[Errno
32] Broken pipe") is said in plain words with what to do, for one request, a chat, a quiz plan, Claude Code and the
Codex CLI; and a quiz on moments the student ticked in one lecture sends the slides around them (not 40 spread over
the lecture), as its estimate says."""

import errno
import sys

import pytest
import requests

from studia import ai, appconfig, cards, examroom, quizzes
from studia.plans import Scope
from studia.views import course_detail
from test_chat import wait_for
from test_mistakes import FIRST, api, make_quiz, tomorrow  # noqa: F401 - api is a fixture

NO_WEEK = "What is a random variable?"


# ------------------------------------------------------------------ a card with no week
def _pasted_quiz(lib):
    """A quiz pasted from ChatGPT over the weeks so far (1 and 2), whose question's source was not found."""
    lib.create_plan("q2", "c", Scope(kind="quiz", weeks=[1, 2], questions=1).to_json(), "Pasted")
    lib.update_plan("q2", status="done", result={"kind": "quiz", "title": "Pasted", "questions": [
        {"n": 1, "type": "short", "q": NO_WEEK, "model": "A number for each outcome.", "points": ["a function"],
         "why": "", "source": None, "los": [], "topic": "Random variables", "level": "easy", "stressed": []}]})


def _room_cards(api, weeks):
    d = course_detail(api._s.lib, api._s.settings, "c")
    e = {"key": "quiz:9", "name": "Quiz 9", "kind": "quiz", "weeks": weeks, "los": []}
    return examroom.practice_part(api._s, "c", d, e)["cards"]["total"]


def test_a_card_with_no_week_stays_out_of_the_sets_of_weeks_it_may_not_be_from(api):
    """A pasted quiz's question whose source wasn't found was given the quiz's last week, the coming quiz's (a paste's
    weeks are the course's weeks so far): MATH 240's cram for Quiz 3 held such cards and its Exam room counted them."""
    lib = api._s.lib
    make_quiz(lib, "q1", api.day)                       # on week 1 alone: its question without a source is week 1's
    _pasted_quiz(lib)                                   # on weeks 1-2 (the weeks so far)
    api.quiz_attempt("q1", {"answers": FIRST})
    api.quiz_attempt("q2", {"answers": [{"n": 1, "got": 0, "text": "no idea", "mark": "self"}]})
    deck = cards.deck(api._s, "c")
    card = {c["q"]: c for c in deck}
    assert card[NO_WEEK]["week"] is None and card[NO_WEEK]["weeks"] == [1, 2]     # (it was week 2)
    assert card["Why does Bayes' theorem need the total probability rule?"]["week"] == 1
    # a cram, "Study one week", a week's run, the Exam room: not of week 2 alone, nor of week 1
    for weeks in ([1], [2]):
        for cram in (True, False):
            run = api.cards_session("c", weeks, [], cram, "")
            assert run["ok"] and NO_WEEK not in {c["q"] for c in run["cards"]}, (weeks, cram)
        assert _room_cards(api, weeks) == (3 if weeks == [1] else 0)          # (q1's three misses are week 1's)
    assert api.cards_session("c", [2], [], True, "")["total"] == 0
    assert set(cards.week_counts(lib, deck, "c")) == {1}
    tab = api.practice_course("c")["deck"]
    assert tab["weeks"] == [1] and set(tab["week_counts"]) == {"1"}
    # weeks that hold all it may be from (an exam on weeks 1-3) take it; the whole deck keeps it
    assert NO_WEEK in {c["q"] for c in api.cards_session("c", [1, 2, 3], [], True, "")["cards"]}
    assert _room_cards(api, [1, 2, 3]) == 4
    assert NO_WEEK in {c["q"] for c in cards.session(lib, deck, "c", now=tomorrow())}
    assert NO_WEEK in {c["q"] for c in cards.picked(deck)}


def test_a_missed_question_takes_its_source_s_week_or_else_the_weeks_its_quiz_was_on():
    plan = {"id": "p", "title": "x", "scope": {"weeks": [3, 4]}}
    q = {"n": 1, "type": "short", "q": "Q?", "model": "A."}
    on = cards.quiz_item(plan, {**q, "source": {"kind": "file", "week": 3}})
    assert on["week"] == 3 and "weeks" not in on
    loose = cards.quiz_item(plan, {**q, "source": None})
    assert loose["week"] is None and loose["weeks"] == [3, 4]
    assert cards.quiz_item({**plan, "scope": {"weeks": [4]}}, {**q, "source": None})["week"] == 4
    nowhere = cards.quiz_item({**plan, "scope": {}}, {**q, "source": {}})
    assert nowhere["week"] is None and nowhere["weeks"] == []
    deck = [loose, nowhere, on]
    assert cards.picked(deck, {3}) == [on] and cards.picked(deck, {4}) == []
    assert cards.picked(deck, {3, 4}) == [loose, on] and cards.picked(deck, set(range(1, 17))) == [loose, on]
    assert cards.picked(deck) == deck                    # (the whole deck: a card from nowhere too)


# ------------------------------------------------------------------ an AI program that closed before it read
@pytest.fixture
def claude(tmp_path, monkeypatch):
    """Claude Code as a program that ends at once, never reading its input (``said``: what it prints on its error
    output first)."""
    import tempfile

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    appconfig.update({"ai_provider": "claude"})
    monkeypatch.setattr(ai, "_require_claude", lambda: sys.executable)
    said = {"text": ""}

    def command(model, effort, sp, partial=False):
        code = f"import sys; sys.stderr.write({said['text']!r}); sys.stderr.flush(); sys.exit(1)"
        return [sys.executable, "-c", code]

    monkeypatch.setattr(ai, "_command", command)
    return said


BIG = [{"type": "text", "text": "x" * 4_000_000}]       # (more than the pipe holds: written while the program ends)


def test_claude_code_that_closed_before_it_read_the_request_says_so_and_what_to_do(claude):
    with pytest.raises(RuntimeError) as got:
        ai.one_shot("system", BIG, "sonnet", "low")
    said = str(got.value)
    assert said == ai.t(ai.ENDED_EARLY) and "Errno" not in said
    assert "Try again" in said and "restart the PC" in said
    # what it said on its way out is what the student reads (here: not signed in)
    claude["text"] = "Invalid API key · Please run /login\n"
    with pytest.raises(RuntimeError) as got:
        ai.one_shot("system", BIG, "sonnet", "low")
    assert str(got.value) == "Claude Code is not signed in. Settings → AI → Sign in with Claude."


def test_python_s_words_for_this_pcs_errors_are_said_plainly_wherever_the_ai_is_asked(monkeypatch):
    appconfig.update({"ai_provider": "claude"})
    raised = {}

    def fail(*a, **k):
        raise raised["exc"]

    monkeypatch.setattr(ai, "_claude_one_shot", fail)
    for exc, want in ((BrokenPipeError(errno.EPIPE, "Broken pipe"), ai.t(ai.ENDED_EARLY)),
                      (OSError(errno.EINVAL, "Invalid argument"), ai.t(ai.ENDED_EARLY)),
                      (OSError(errno.ENOSPC, "No space left on device"),
                       "Your disk is full. Free up some space, then try again."),
                      (OSError(errno.EIO, "Input/output error"), ai.t(ai.PC_FAILED, ai=ai.ai_name()))):
        raised["exc"] = exc
        with pytest.raises(RuntimeError) as got:
            ai.one_shot("system", [{"type": "text", "text": "hi"}])
        assert str(got.value) == want and "Errno" not in str(got.value)
    # a connection's error is said where it is shown (net.plain_error, with its host): left as it is
    raised["exc"] = requests.ConnectionError("no route")
    with pytest.raises(requests.ConnectionError):
        ai.one_shot("system", [{"type": "text", "text": "hi"}])
    # a chat's turn (ChatGPT's session) too, and the Codex CLI's words for it
    appconfig.update({"ai_provider": "codex"})
    chat = ai.ChatGPTSession("system", cli=True)

    def broke(*a, **k):
        raise BrokenPipeError(errno.EPIPE, "Broken pipe")

    chat.request = broke
    with pytest.raises(RuntimeError) as got:
        chat.ask([{"type": "text", "text": "hi"}])
    assert str(got.value) == ai.t(ai.CODEX_ENDED_EARLY)


def test_a_program_s_own_error_line_in_python_s_words_is_said_plainly():
    assert ai.friendly_error("[Errno 32] Broken pipe") == ai.t(ai.ENDED_EARLY)
    assert ai.friendly_error("BrokenPipeError: [WinError 232] The pipe is being closed") == ai.t(ai.ENDED_EARLY)
    assert ai.friendly_error("[Errno 5] Input/output error") == "Claude Code stopped unexpectedly. Try again."
    assert ai.friendly_error("Error: something odd") == "Claude: Error: something odd"         # (its own words: kept)
    assert ai.friendly_error(ai.t(ai.ENDED_EARLY)) == ai.t(ai.ENDED_EARLY)                   # already said: as it is
    assert ai._codex_error("[Errno 32] Broken pipe") == ai.t(ai.CODEX_ENDED_EARLY)
    assert ai._codex_error("[Errno 5] Input/output error").startswith("The Codex CLI stopped unexpectedly.")


def test_a_quiz_whose_ai_closed_before_reading_shows_the_plain_reason(api, monkeypatch):
    appconfig.update({"ai_provider": "claude"})

    def broke(*a, **k):
        raise BrokenPipeError(errno.EPIPE, "Broken pipe")

    monkeypatch.setattr(ai, "_claude_one_shot", broke)
    plans = api._s.plans
    pid = plans.create("c", {"kind": "quiz", "weeks": [1], "questions": 5})
    plans.start(pid)
    assert wait_for(lambda: (api._s.lib.plan(pid) or {}).get("status") in ("error", "done"), 30)
    p = api._s.lib.plan(pid)
    assert p["status"] == "error" and p["error"] == ai.t(ai.ENDED_EARLY)


# ------------------------------------------------------------------ the stressed-moment quiz's estimate
def test_a_quiz_on_moments_of_one_lecture_sends_the_slides_around_them_and_its_estimate_counts_them(api, monkeypatch):
    """2 ticked moments of one 6-part lecture were estimated at 88.3k tokens and 12 over 5 lectures at 56.0k: both
    estimates were what their runs send, but one lecture's moments made a one-lecture quiz, which sent 40 of its slides
    spread over the whole lecture (32k tokens). Now such a quiz sends the slides on screen around its moments."""
    from studia import stressed, store
    from studia.slides import Slide

    monkeypatch.setattr(ai, "_small_jpeg", lambda path, width=1024: b"jpeg")
    lib, settings = api._s.lib, api._s.settings
    shots = []
    for i in range(1, 11):                               # ten slides of 5 minutes each
        p = api.tmp / f"s{i}.jpg"
        p.write_bytes(b"\xff\xd8\xff\xd9")
        shots.append(Slide(i, 300 * (i - 1), 300 * i, p.name, str(p)))
    store.save_slides("d1", shots)
    moment = next(it for it in stressed.course_items(lib, "c") if it["kind"] != "not_needed")
    assert (moment["part"], moment["t"]) == (1, 600.0)
    picked = Scope(kind="quiz", weeks=[1], days=[api.day], picked=True, stressed_ids=[moment["id"]], questions=10)
    facts = quizzes.gather(lib, settings, "c", picked)
    assert facts["one_lecture"] and facts["stressed_only"] and facts["near"] == [[1, 600.0]]
    assert [x[0] for x in quizzes.moment_slides(lib, "c", api.day, facts["near"])] == \
        ["[Part 1, slide 2]", "[Part 1, slide 3]"]                   # on screen within 3 minutes of 10:00
    assert quizzes.estimate(lib, settings, "c", picked)["slides"] == facts["near_slides"] == 2
    sent = quizzes._slide_blocks(api._s, api.day, "c", facts["near"])
    assert [b["text"] for b in sent if b["type"] == "text"] == ["[Part 1, slide 2]", "[Part 1, slide 3]"]
    assert sum(1 for b in sent if b["type"] == "image") == 2
    prompt = quizzes.write_prompt(picked, facts, 10, quizzes.mix(10, "mixed", False))
    assert "The slides on screen around the S moments are attached as pictures" in prompt
    # the lecture's own quiz (its page's Quiz me) still sends its slides spread over it, as many as LECTURE_SLIDES
    whole = Scope(kind="quiz", weeks=[1], days=[api.day], picked=True, questions=10)
    assert quizzes.estimate(lib, settings, "c", whole)["slides"] == 10
    assert "The slides captured from the lecture video are attached" in quizzes.write_prompt(
        whole, quizzes.gather(lib, settings, "c", whole), 10, quizzes.mix(10, "mixed", False))
    assert len(quizzes._slide_blocks(api._s, api.day)) == 20
