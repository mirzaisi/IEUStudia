"""Informative waits (aiwait): what an AI run is doing now, its thinking as short lines, the time left from speeds
measured on this PC (never made up), retries in words; requests report to the run whose cancel event they carry."""

import json
import sys
import threading
import time
import types
from pathlib import Path

import pytest

from studia import ai, aiwait, chatgpt, local_llm
from test_chat import DAY1, ask, env  # noqa: F401 - env is a fixture (a chat with the fake Claude Code)
from test_service_changes_ai import _printer
from test_service_errors import OK, openai  # noqa: F401 - openai is a fixture (a local server for api.openai.com)

FAKE = str(Path(__file__).with_name("fake_claude.py"))


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def go(self, s):
        self.t += s


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(aiwait, "_now", c)
    return c


@pytest.fixture
def speeds(tmp_path):
    return aiwait.Speeds(tmp_path / "speeds.json")


def wait_for(speeds, **kw):
    seen = []
    w = aiwait.Wait(seen.append, ai="Claude", provider="claude", model="sonnet", level="low", kind="notes",
                    where="notes", speeds=speeds, **kw)
    return w, seen


def test_a_thinking_summary_is_one_short_line():
    assert aiwait._line("**Comparing the two circuits**\n\nI am looking at the voltage divider and") == \
        "Comparing the two circuits"
    assert aiwait._line("**Planning**\n\nfirst.\n\n**Checking the units**\n\nThe") == "Checking the units"
    assert aiwait._line("The student wants notes. I will start with Ohm's law and then") == "The student wants notes."
    assert aiwait._line("Thinking about it") == "Thinking about it"
    long = aiwait._line("x" * 300 + ".")
    assert len(long) == aiwait.LINE_CHARS and long.endswith("…")
    assert aiwait._line("") == ""


def test_speeds_are_kept_and_the_closest_ones_used(speeds, tmp_path):
    speeds.note("claude", "sonnet", "low", "notes", first=10.0, rate=50.0, chars=3000.0)
    speeds.note("claude", "sonnet", "low", "notes", first=20.0, rate=50.0, chars=3000.0)
    row = speeds.row("claude", "sonnet", "low", "notes")
    assert 13 < row["first"] < 14 and row["n"] == 2                       # moved by a part of the new one (EMA)
    # another kind on the same model: its pace and first words from the model, its length unknown until measured
    other = speeds.row("claude", "sonnet", "low", "summary")
    assert other["rate"] == 50.0 and "chars" not in other
    speeds.note("claude", "opus", "high", "summary", chars=1200.0)
    assert speeds.row("claude", "sonnet", "low", "summary")["chars"] == 1200.0     # this AI's summaries: their length
    assert speeds.row("chatgpt", "gpt-6", "low", "notes") == {}
    assert speeds.upload() == 0 and speeds.upload(400_000) == 400_000 and speeds.upload(200_000) == 330_000
    again = aiwait.Speeds(tmp_path / "speeds.json")                       # kept on disk
    assert again.upload() == 330_000 and again.row("claude", "sonnet", "low", "notes")["n"] == 2
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    assert aiwait.Speeds(tmp_path / "bad.json").row("a", "b", "c", "d") == {}


def test_a_request_says_each_step_and_the_time_left_from_measured_speeds(clock, speeds):
    w, seen = wait_for(speeds)
    w.phase("prepare")
    assert seen[-1]["phase"] == "prepare" and "eta" not in seen[-1]
    w.begin(size=40 * 2 ** 20, pictures=120)
    s = w.snapshot()
    assert (s["phase"], s["size"], s["pictures"]) == ("send", 40 * 2 ** 20, 120) and "eta" not in s   # nothing known
    clock.go(10)
    w.upload(20 * 2 ** 20, 40 * 2 ** 20)
    assert w.snapshot()["up"] == [20 * 2 ** 20, 40 * 2 ** 20] and "eta" not in w.snapshot()    # no answer speeds yet
    speeds.note("claude", "sonnet", "low", "notes", first=8.0, rate=40.0, chars=4000.0)
    assert w.snapshot()["eta"] == 10 + 8 + 100                             # the rest of the upload, first words, writing
    clock.go(10)
    w.upload(40 * 2 ** 20, 40 * 2 ** 20)                                 # up: this connection's speed is kept
    assert w.snapshot()["phase"] == "wait" and speeds.upload() == pytest.approx(40 * 2 ** 20 / 20)
    clock.go(3)
    w.thinking("**Planning the notes**\n\nThe lecture covers")
    s = w.snapshot()
    assert s["phase"] == "think" and s["lines"] == ["Planning the notes"] and s["eta"] == 5 + 100
    w.thinking(" Ohm's law.")
    w.thinking("**Checking the formulas**", new=True)
    assert w.snapshot()["lines"] == ["Planning the notes", "Checking the formulas"]
    clock.go(2)
    w.text("# Notes\n" + "x" * 492)
    clock.go(5)
    w.text("# Notes\n" + "x" * 1992)                                      # 2000 characters in 5 s: 400 a second
    s = w.snapshot()
    assert s["phase"] == "write" and s["tokens"] == int(2000 / 3.1) and s["eta"] == 5      # 2000 left at its own pace
    w.end(ok=True)
    row = speeds.row("claude", "sonnet", "low", "notes")
    assert row["n"] == 2 and row["first"] == pytest.approx(8 + 0.35 * (5 - 8), abs=0.01)   # 5 s to the first words
    assert w.snapshot()["phase"] == "between" and w.snapshot()["busy"] == 0


def test_a_wait_longer_than_usual_says_so_instead_of_counting_below_zero(clock, speeds):
    speeds.note("claude", "sonnet", "low", "notes", first=10.0, rate=40.0, chars=1000.0)
    w, _ = wait_for(speeds)
    w.begin(size=1000)
    w.sent()
    clock.go(12)
    assert w.snapshot()["eta"] == 25 and "slow" not in w.snapshot()    # past the usual first words: the writing left
    clock.go(10)
    s = w.snapshot()
    assert "eta" not in s and s["slow"]
    w.text("y" * 1300)                                                 # longer than such answers are: no guess either
    clock.go(4)
    w.text("y" * 1400)
    assert "eta" not in w.snapshot() and w.snapshot()["slow"]


def test_a_retry_is_said_with_its_wait_and_try(clock, speeds):
    w, seen = wait_for(speeds)
    w.begin(size=100)
    w.text("half an ans")
    w.retry("overloaded", wait=8, attempt=2, of=10, status=529)
    s = seen[-1]
    assert s["phase"] == "retry" and s["retry"] == {"why": "overloaded", "wait": 8.0, "until": clock.t + 8, "n": 2,
                                                    "of": 10, "status": 529}
    assert s["step_at"] == clock.t + 8 and "tokens" not in s           # what came before is gone with the retry
    clock.go(9.5)
    assert w.snapshot()["phase"] == "send"                             # the wait is over: it is asked again
    w.text("A whole answer")
    assert w.snapshot()["phase"] == "write" and "retry" not in w.snapshot()


def test_parallel_requests_count_the_ones_still_to_come(clock, speeds):
    """A study pack writes two chapters at once: the time left counts the chapters still waiting at this run's pace."""
    w, _ = wait_for(speeds)
    w.stage("Writing chapter 1 of 5", 1, 5, kind="pack.sections")
    done = threading.Event()

    def chapter():
        w.begin(size=100)
        clock.go(30)
        w.text("z" * 900)
        w.end(ok=True)
        done.set()
    th = threading.Thread(target=chapter)
    th.start()
    th.join()
    w.units(1, 5, 2)
    w.begin(size=100)                                                  # chapters 2 and 3 now (this thread and another)
    other = threading.Thread(target=lambda: w.begin(size=100))
    other.start()
    other.join()
    s = w.snapshot()
    assert s["busy"] == 2 and s["units"] == [1, 5] and s["stage"]["label"] == "Writing chapter 1 of 5"
    assert s["eta"] == round(15 + 30 * 2 / 2)                          # half a chapter for these, then 2 more, 2 at once


def test_requests_report_to_the_run_whose_cancel_event_they_carry(speeds):
    cancel, other = threading.Event(), threading.Event()
    w, seen = wait_for(speeds)
    assert aiwait.of(cancel) is aiwait.NULL and not aiwait.of(None)
    aiwait.of(None).begin(size=5)                                      # nothing to tell: nothing happens
    with aiwait.tracking(cancel, w) as tracked:
        assert tracked is w and aiwait.of(cancel) is w and aiwait.of(other) is aiwait.NULL
        aiwait.of(cancel).begin(size=5)
    assert aiwait.of(cancel) is aiwait.NULL and seen[-1]["phase"] == "send"


def test_the_pc_stays_awake_while_any_run_goes_on(speeds, monkeypatch):
    """A study pack takes half an hour: Windows' idle timer must not put the laptop to sleep meanwhile (it then woke
    up to "took too long"). One request for all the runs, on at the first and off after the last."""
    from studia import osutil

    made = []

    class Awake:
        def __init__(self, reason):
            made.append(self)
            self.reason, self.on_ = reason, False

        def on(self):
            self.on_ = True

        def off(self):
            self.on_ = False

    monkeypatch.setattr(osutil.impl(), "KeepAwake", Awake)
    monkeypatch.setattr(aiwait, "_awake", None)
    monkeypatch.setattr(aiwait, "_awake_n", 0)
    a, b = threading.Event(), threading.Event()
    with aiwait.tracking(a, wait_for(speeds)[0]):
        assert made[0].on_ and made[0].reason == "IEUStudia is working with the AI"
        with aiwait.tracking(b, wait_for(speeds)[0]):                  # a chat while a pack is written
            assert made[0].on_
        assert made[0].on_                                              # the pack still goes on
    assert not made[0].on_ and len(made) == 1
    with pytest.raises(RuntimeError):
        with aiwait.tracking(a, wait_for(speeds)[0]):
            raise RuntimeError("the run failed")
    assert not made[0].on_ and aiwait._awake_n == 0


def test_one_request_across_a_sleep_doesnt_skew_the_estimates(speeds):
    """A request that went on across a sleep (8 hours to its first words) counts as at most 3 times as slow: the next
    answers don't say "about 2 hours left" for a minute's wait."""
    for _ in range(3):
        speeds.note("claude", "sonnet", "low", "chat", first=20.0, rate=50.0, chars=2000.0)
    speeds.note("claude", "sonnet", "low", "chat", first=8 * 3600.0, rate=0.1, chars=2000.0)
    row = speeds.row("claude", "sonnet", "low", "chat")
    assert row["first"] == pytest.approx(20 + 0.35 * 40) and row["rate"] == pytest.approx(50 - 0.35 * (50 - 50 / 3), abs=0.01)


def test_the_page_hears_steps_at_once_and_the_writing_at_most_twice_a_second(monkeypatch, clock, speeds):
    ticks = iter(range(0, 10_000))
    monkeypatch.setattr(aiwait.time, "monotonic", lambda: next(ticks) * 0.01)       # 10 ms between calls
    w, seen = wait_for(speeds)
    w.begin(size=10)
    n = len(seen)
    for i in range(1, 40):
        w.text("w" * i)
    assert 1 <= len(seen) - n <= 4                                     # the first words at once, then a few
    w.retry("dropped", wait=3, attempt=2, of=3)
    assert seen[-1]["phase"] == "retry"


# ---------------------------------------------------------------------------------------------- ChatGPT
def tracked(speeds):
    cancel = threading.Event()
    w, seen = wait_for(speeds)
    return cancel, w, seen


def test_a_heavy_chatgpt_question_says_how_much_has_gone_up(openai, speeds, monkeypatch):
    """The silent half minute after Read everything's last page: the question and its ~120 pictures going up. A heavy
    request goes up in pieces and says how far it is; the server still gets it whole, with its length."""
    monkeypatch.setattr(chatgpt, "_turned_down", set())
    monkeypatch.setattr(aiwait, "EMIT_EVERY_S", 0.0)          # (on this PC it goes up in a moment: every piece said)
    cancel, w, seen = tracked(speeds)
    openai.answers.append((200, {}, OK))
    picture = {"type": "image", "source": {"media_type": "image/jpeg", "data": "A" * 3_000_000}}
    with aiwait.tracking(cancel, w):
        res = chatgpt.one_shot("Be brief.", [picture, {"type": "text", "text": "hello"}], cancel=cancel)
    assert res["text"] == "Hi"
    body = openai.requests[0]["body"]
    assert body["input"][0]["content"][0]["image_url"].endswith("A" * 100)       # whole, as JSON
    assert body["reasoning"] == {"effort": "medium", "summary": "auto"}
    ups = [s["up"] for s in seen if "up" in s]
    assert ups and ups[-1][1] > 3_000_000 and all(a[0] <= b[0] for a, b in zip(ups, ups[1:]))
    assert seen[0]["phase"] == "send" and seen[0]["pictures"] == 1 and seen[0]["size"] > 3_000_000
    assert any(s["phase"] == "wait" for s in seen) and seen[-1]["phase"] == "between"   # up, then answered, then over
    assert speeds.row("chatgpt", "gpt-6.1-sol", "medium", "notes")["chars"] == 2


def test_chatgpt_s_reasoning_summaries_are_thinking_lines(openai, speeds, monkeypatch):
    monkeypatch.setattr(chatgpt, "_turned_down", set())
    cancel, w, seen = tracked(speeds)
    openai.answers.append((200, {}, [
        {"type": "response.created", "response": {}},
        {"type": "response.output_item.added", "item": {"type": "reasoning"}},
        {"type": "response.reasoning_summary_part.added", "part": {"type": "summary_text", "text": ""}},
        {"type": "response.reasoning_summary_text.delta", "delta": "**Reading the slides**\n\nThe lecture"},
        {"type": "response.reasoning_summary_text.delta", "delta": " covers Thevenin."},
        {"type": "response.reasoning_summary_part.added", "part": {"type": "summary_text", "text": ""}},
        {"type": "response.reasoning_summary_text.delta", "delta": "**Writing the notes**"},
        *OK]))
    with aiwait.tracking(cancel, w):
        chatgpt.one_shot("Be brief.", [{"type": "text", "text": "hello"}], cancel=cancel)
    thinking = [s for s in seen if s["phase"] == "think"]
    assert thinking and thinking[-1]["lines"] == ["Reading the slides", "Writing the notes"]
    assert any(s["phase"] == "write" and s["lines"] for s in seen)        # the lines stay while it writes


def test_a_chatgpt_retry_is_said_with_its_wait_and_try(openai, speeds, monkeypatch):
    monkeypatch.setattr(chatgpt, "_turned_down", set())
    cancel, w, seen = tracked(speeds)
    openai.answers += [(500, {}, {"error": {"message": "boom"}}), (200, {}, OK)]
    with aiwait.tracking(cancel, w):
        assert chatgpt.one_shot("Be brief.", [{"type": "text", "text": "hello"}], cancel=cancel)["text"] == "Hi"
    retry = [s["retry"] for s in seen if s["phase"] == "retry"]
    assert retry and retry[0]["why"] == "service" and retry[0]["status"] == 500 and retry[0]["n"] == 2
    assert retry[0]["of"] == 4 and retry[0]["wait"] == 4.0


def test_reasoning_summaries_turned_down_are_left_out_from_then_on(openai, monkeypatch):
    monkeypatch.setattr(chatgpt, "_turned_down", set())
    openai.answers += [(400, {}, {"error": {"message": "Unsupported parameter: 'reasoning.summary'.",
                                            "param": "reasoning.summary", "code": "unsupported_parameter"}}),
                       (200, {}, OK), (200, {}, OK)]
    assert chatgpt.one_shot("Be brief.", [{"type": "text", "text": "hello"}])["text"] == "Hi"
    assert [r["body"]["reasoning"] for r in openai.requests] == [{"effort": "medium", "summary": "auto"},
                                                                 {"effort": "medium"}]
    chatgpt.one_shot("Be brief.", [{"type": "text", "text": "again"}])
    assert openai.requests[-1]["body"]["reasoning"] == {"effort": "medium"}           # not asked for again


# ---------------------------------------------------------------------------------------------- Claude Code
def stream_json(*events):
    return [json.dumps(e) for e in events]


THINKING = [{"type": "stream_event", "event": {"type": "content_block_start", "index": 0,
                                               "content_block": {"type": "thinking", "thinking": ""}}},
            {"type": "stream_event", "event": {"type": "content_block_delta", "index": 0,
                                               "delta": {"type": "thinking_delta",
                                                         "thinking": "The student wants notes. I start with"}}},
            {"type": "stream_event", "event": {"type": "content_block_delta", "index": 0,
                                               "delta": {"type": "thinking_delta", "thinking": " Ohm's law."}}}]


@pytest.fixture
def claude(monkeypatch):
    monkeypatch.setattr(ai, "_require_claude", lambda: sys.executable)
    monkeypatch.setattr(ai, "claude_cli_info", lambda: {"available": True, "path": sys.executable, "version": "2.1.290"})

    def use(cmd):
        monkeypatch.setattr(ai, "_command", lambda model, effort, sp, partial=False: cmd)
    return use


def test_claude_s_thinking_text_and_own_retries_are_told(claude, speeds):
    """Study notes with Claude: the answer streams (partial messages), its thinking comes as lines, and Claude Code's
    own retry (Anthropic overloaded) is said with its wait and try."""
    claude(_printer(*stream_json(
        {"type": "system", "subtype": "init"},
        {"type": "system", "subtype": "api_retry", "attempt": 1, "max_retries": 10, "retry_delay_ms": 2000,
         "error_status": 529, "error": "overloaded"},
        {"type": "stream_event", "event": {"type": "message_start", "message": {}}},
        *THINKING,
        {"type": "stream_event", "event": {"type": "content_block_start", "index": 1, "content_block": {"type": "text"}}},
        {"type": "stream_event", "event": {"type": "content_block_delta", "index": 1,
                                           "delta": {"type": "text_delta", "text": "# Notes\n"}}},
        {"type": "stream_event", "event": {"type": "content_block_delta", "index": 1,
                                           "delta": {"type": "text_delta", "text": "Ohm's law: $V = IR$."}}},
        {"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "x"},
                                                      {"type": "text", "text": "# Notes\nOhm's law: $V = IR$."}]}},
        {"type": "result", "subtype": "success", "is_error": False, "result": "# Notes\nOhm's law: $V = IR$.",
         "usage": {"input_tokens": 10, "output_tokens": 20}})))
    cancel = threading.Event()
    w, seen = wait_for(speeds)
    texts = []
    with aiwait.tracking(cancel, w):
        res = ai._claude_one_shot("S", [{"type": "text", "text": "notes"}], "sonnet", "low", cancel, texts.append)
    assert res["text"] == "# Notes\nOhm's law: $V = IR$." and texts == ["# Notes", "# Notes\nOhm's law: $V = IR$."]
    retry = next(s for s in seen if s["phase"] == "retry")["retry"]
    assert (retry["why"], retry["n"], retry["of"], retry["wait"], retry["status"]) == ("overloaded", 2, 11, 2.0, 529)
    assert any(s["phase"] == "think" and s.get("lines") == ["The student wants notes."] for s in seen)
    assert seen[-1]["phase"] == "between" and speeds.row("claude", "sonnet", "low", "notes")["n"] == 1


def test_the_answers_time_limit_counts_only_the_time_the_pc_is_awake(claude, monkeypatch):
    """A laptop whose lid was closed mid-answer: Windows' time.monotonic counts the hours asleep, and the answer then
    ended at once as "took too long… pick a lower reasoning level". The limit counts the time awake."""
    from studia import osutil

    result = json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "ok", "usage": {}})
    claude([sys.executable, "-c", f"import sys, time; sys.stdin.read(); time.sleep(1.5); print({result!r})"])
    real, calls = time.monotonic, []

    def slept():                                                       # 16 minutes asleep right after the start
        calls.append(1)
        return real() + (16 * 60 if len(calls) > 1 else 0)

    monkeypatch.setattr(time, "monotonic", slept)
    assert ai._claude_one_shot("S", [{"type": "text", "text": "x"}], timeout_s=900)["text"] == "ok"
    monkeypatch.setattr(time, "monotonic", real)
    calls.clear()
    monkeypatch.setattr(osutil.impl(), "awake_time", slept)           # 16 minutes awake: that is too long
    with pytest.raises(RuntimeError, match="took too long"):
        ai._claude_one_shot("S", [{"type": "text", "text": "x"}], timeout_s=900)


def test_claude_code_is_asked_for_its_thinking_summaries_where_it_can(monkeypatch, tmp_path):
    monkeypatch.setattr(ai, "_require_claude", lambda: "claude.exe")
    monkeypatch.setitem(ai._thinking_flag, "off", False)
    for version, has in (("2.1.287", True), ("2.2.0", True), ("2.1.200", False), ("", False)):
        monkeypatch.setattr(ai, "claude_cli_info", lambda v=version: {"available": True, "version": v})
        cmd = ai._command("sonnet", "low", tmp_path / "s.txt", partial=True)
        assert ("--thinking-display" in cmd) is has and cmd[cmd.index("--include-partial-messages") - 1] == "--verbose"
    monkeypatch.setattr(ai, "claude_cli_info", lambda: {"available": True, "version": "2.3.0"})
    assert ai._thinking_flag_refused("error: unknown option '--thinking-display'") and ai._thinking_flag["off"]
    assert "--thinking-display" not in ai._command("sonnet", "low", tmp_path / "s.txt")    # never again this run
    assert not ai._thinking_flag_refused("error: unknown option '--effort'")


def test_a_claude_that_turns_the_thinking_flag_down_is_asked_again_without_it(claude, speeds, monkeypatch):
    monkeypatch.setitem(ai._thinking_flag, "off", False)
    calls = []
    good = _printer(*stream_json({"type": "result", "subtype": "success", "is_error": False, "result": "ok",
                                  "usage": {}}))
    bad = [sys.executable, "-c", "import sys; sys.stdin.read(); "
                                 "sys.stderr.write(\"error: unknown option '--thinking-display'\\n\"); sys.exit(1)"]
    monkeypatch.setattr(ai, "_command", lambda model, effort, sp, partial=False: calls.append(1) or (
        good if ai._thinking_flag["off"] else bad))
    assert ai._claude_one_shot("S", [{"type": "text", "text": "q"}], "sonnet", "low")["text"] == "ok"
    assert len(calls) == 2 and ai._thinking_flag["off"]


def test_a_chat_with_claude_says_its_thinking_and_retry(monkeypatch, speeds, tmp_path):
    monkeypatch.setattr(ai, "_require_claude", lambda: sys.executable)
    monkeypatch.setattr(ai, "_command", lambda model, effort, sp, partial=False: [sys.executable, FAKE, str(sp)])
    stop = threading.Event()
    w, seen = wait_for(speeds)
    session = ai.ClaudeSession("# L1 lecture", "sonnet", "low")
    try:
        with aiwait.tracking(stop, w):
            w.expect(30 * 2 ** 20, 120)          # the whole conversation goes with every question (chat.py knows)
            res = session.ask([{"type": "text", "text": "THINK RETRY What is on the quiz?"}], None, stop)
    finally:
        session.terminate()
    assert res["text"].startswith("Answer 1") and not res["stopped"]
    assert seen[0]["phase"] == "send" and seen[0]["size"] == 30 * 2 ** 20 and seen[0]["pictures"] == 120
    assert any(s["phase"] == "retry" and s["retry"]["why"] == "overloaded" for s in seen)
    assert any(s.get("lines") == ["Reading the lecture"] for s in seen)
    assert seen[-1]["phase"] == "between"


def test_a_chat_answer_says_each_step_in_its_events(env):
    """Every event of an answer that streams carries its wait: getting ready, sending (the first question with the whole
    material), Claude's thinking, the writing; the answer's end carries none."""
    chat = env.svc.create("c", [DAY1])
    done = ask(env, chat["id"], "THINK Is it on the quiz?")
    assert done["state"] == "done" and "wait" not in done
    waits = [e["wait"] for e in env.events if e.get("type") == "chat" and e["state"] == "streaming" and e.get("wait")]
    phases = [w["phase"] for w in waits]
    assert phases[0] == "prepare" and "send" in phases and "think" in phases
    assert phases.index("send") < phases.index("think")
    sent = next(w for w in waits if w["phase"] == "send")
    assert sent["where"] == "chat" and sent["ai"] == "Claude" and sent["size"] > 0
    assert any(w.get("lines") == ["Reading the lecture"] for w in waits)
    # the next question: its own kind (Claude has the lectures already), its speeds kept apart
    ask(env, chat["id"], "And the homework?")
    assert aiwait.SPEEDS.row("claude", "sonnet", "low", "chat").get("n", 0) >= 1
    assert aiwait.SPEEDS.row("claude", "sonnet", "low", "chat.first").get("n", 0) >= 1


def test_a_retry_claude_code_makes_while_reading_is_said_on_its_part(tmp_path):
    """Read everything with Claude: Claude Code waits by itself when Anthropic is overloaded; the part's line says so
    (as the app's own retries are said), and the reading's requests keep no speeds of an answer."""
    from studia import chatread
    from test_chatread import lim, page

    pages = [page(i, file=f"waits-{tmp_path.name}", text=f"Page {i}: Ohm's law", pic=False) for i in range(1, 4)]
    seen = []

    def request(system, content, cancel, on_text=None, on_wait=None):
        w = aiwait.of(cancel)
        w.begin(size=100)
        w.retry("overloaded", 2.0, 2, 11, 529)             # what ai._ClaudeStream does with Claude Code's api_retry
        labels = content[-1]["text"].split("each to be noted under its label: ")[1].split(", ")
        text = "\n\n".join(f"### {x}\nNotes of {x}." for x in labels)
        w.end(True)
        return {"text": text, "usage": {}}

    before = json.dumps(aiwait.SPEEDS.row("claude", "sonnet", "low", ""), sort_keys=True)
    r = chatread.read(pages, "q", lim(), request, progress=seen.append)
    assert r["read"] == 3
    retries = [x["retry"] for s in seen for x in s["parts"] if x.get("retry")]
    assert retries and retries[0]["why"] == "service" and retries[0]["status"] == 529 and retries[0]["wait"] == 2.0
    assert json.dumps(aiwait.SPEEDS.row("claude", "sonnet", "low", ""), sort_keys=True) == before


# ---------------------------------------------------------------------------------------------- the Codex CLI
def test_codex_s_reasoning_and_its_own_reconnects_are_told(monkeypatch, speeds):
    monkeypatch.setattr(ai, "_codex_model", lambda: "")
    cmd = _printer(*stream_json(
        {"type": "thread.started"}, {"type": "turn.started"},
        {"type": "error", "message": "Reconnecting... 2/5 (stream disconnected before completion)"},
        {"type": "item.completed", "item": {"type": "reasoning", "text": "**Reading the slides**\n\nThey show"}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "Hi"}},
        {"type": "turn.completed", "usage": {"input_tokens": 5, "output_tokens": 2}}))
    monkeypatch.setattr(ai, "_codex_command", lambda work, model, effort, images=(): cmd)
    cancel = threading.Event()
    w, seen = wait_for(speeds)
    with aiwait.tracking(cancel, w):
        assert ai._codex_once("S", [{"type": "text", "text": "x"}], "sonnet", "", cancel, None, 30)["text"] == "Hi"
    retry = next(s for s in seen if s["phase"] == "retry")["retry"]
    assert (retry["why"], retry["n"], retry["of"]) == ("dropped", 2, 5)
    assert any(s.get("lines") == ["Reading the slides"] for s in seen) and seen[-1]["phase"] == "between"


# ---------------------------------------------------------------------------------------------- the offline model
def test_the_offline_model_says_its_thinking_and_writing(monkeypatch, speeds):
    vocab: list[str] = []

    class Tok:
        def encode(self, text):
            return types.SimpleNamespace(tokens=text.split(), ids=list(range(len(text.split()))))

        def decode(self, ids):
            return " ".join(vocab[i] for i in ids)

    def words(text):
        out = []
        for x in text.split():
            vocab.append(x)
            out.append(types.SimpleNamespace(token_id=len(vocab) - 1))
        return out

    class Gen:
        def generate_tokens(self, prefix, max_length, sampling_temperature, sampling_topk, end_token):
            if "</think>" in end_token:
                yield from words("I should explain the divider first. Then the example with numbers.")
            else:
                yield from words("## Notes " + "word " * 30)

    monkeypatch.setattr(local_llm, "_load", lambda key, cpu=False: {"tok": Tok(), "gen": Gen(), "device": "cuda",
                                                                    "used": 0})
    monkeypatch.setattr(aiwait, "EMIT_EVERY_S", 0.0)          # (it writes in a moment here: every step said)
    cancel = threading.Event()
    w, seen = wait_for(speeds)
    w.provider = "local"
    with aiwait.tracking(cancel, w):
        r = local_llm.generate([{"role": "system", "content": "s"}, {"role": "user", "content": "q"}], "qwen3-4b",
                               200, None, cancel, think=40)
    assert r["text"].startswith("## Notes")
    assert any(s["phase"] == "think" and s.get("lines") for s in seen)
    writing = [s for s in seen if s["phase"] == "write"]
    assert writing and writing[-1]["tokens"] == 30          # the tokens it wrote, as it counts them
