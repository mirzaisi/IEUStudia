"""Chat with Claude: material, one process per conversation, streaming, stop, restarts, errors (fake CLI)."""

import datetime as dt
import json
import os
import re
import sys
import tempfile
import time
import types
from pathlib import Path

import pytest

from studia import ai, appconfig, chat as chat_mod, store
from studia.chat import ChatService, replay_text
from studia.config import Settings
from studia.domain import Course, Recording, Segment, Transcript
from studia.library import Library
from studia.services import Services
from studia.slides import Slide

FAKE = str(Path(__file__).with_name("fake_claude.py"))
DAY1, DAY2 = "c|2026-10-13|lecture", "c|2026-10-20|lecture"


def wait_for(cond, timeout=15.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))          # Claude work folders stay in the test dir
    appconfig.update({"ai_provider": "claude"})                      # also on a PC without Claude Code (CI)
    monkeypatch.setattr(ai, "_require_claude", lambda: sys.executable)
    monkeypatch.setattr(ai, "_command", lambda model, effort, sp, partial=False: [sys.executable, FAKE, str(sp)])
    with ai._limits_lock:
        ai._limits.clear()
    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="c", title="t", code="MATH 240", name="Probability", instructor="Dr X")])
    recs = [Recording(delivery_id=f"r{i}", course_id="c", folder_id="f", folder_path="", name="n",
                      start=dt.datetime(2026, 10, d, h, 30), duration_s=2400, viewer_url=f"https://v/r{i}")
            for i, (d, h) in enumerate([(13, 8), (13, 9), (20, 8)])]
    lib.merge_recordings("c", recs, first_sync=True)
    for i, r in enumerate(recs):
        t = Transcript(delivery_id=r.delivery_id, segments=[Segment(0, 5, f"Lecture text {i}.", "en", 0.9),
                                                            Segment(9, 12, "This will be on the quiz.", "en", 0.9)],
                       duration_s=2400, speech_s=10, language_seconds={"en": 10}, model="m", device="cpu",
                       engine_version="t", created_at="2026-10-20T10:00:00")
        lib.save_transcript(t, store.save_transcript(t))
        lib.set_status(r.delivery_id, "done")
    events = []
    ns = types.SimpleNamespace(lib=lib, emit=events.append, output_root=tmp_path / "out", settings=Settings())
    ns.day = types.MethodType(Services.day, ns)
    svc = ChatService(ns)
    yield types.SimpleNamespace(svc=svc, lib=lib, events=events, tmp=tmp_path, mp=monkeypatch)
    svc.shutdown()


def final(events, chat_id, seq):
    done = [e for e in events if e.get("type") == "chat" and e["chat_id"] == chat_id and e["seq"] == seq
            and e["state"] != "streaming"]
    return done[-1] if done else None


def ask(env, chat_id, text):
    seq = env.svc.send(chat_id, text)["seq"]
    assert wait_for(lambda: final(env.events, chat_id, seq) is not None), "no answer"
    return final(env.events, chat_id, seq)


def test_material_is_ordered_labelled_and_can_be_highlights_only(env):
    assert env.svc.ordered_days("c", [DAY2, DAY1, DAY2, "c|2026-10-01|lecture"]) == [DAY1, DAY2]
    ctx = env.svc.context("c", [DAY1, DAY2])
    assert "# L1 · MATH 240 – Probability · Lecture · Tuesday, 13 October 2026" in ctx["system"]
    assert "# L2 · MATH 240" in ctx["system"] and "[L2 P1 05:10]" in ctx["system"]
    assert "## Part 2 (09:30–10:10)" in ctx["system"] and "Lecture text 1." in ctx["system"]
    one = env.svc.context("c", [DAY1])
    assert "# MATH 240" in one["system"] and "[P2 14:05]" in one["system"] and "L1" not in one["system"]
    hl = env.svc.context("c", [DAY1, DAY2], focus="highlights")
    assert "⭐ This will be on the quiz." in hl["system"] and "Lecture text" not in hl["system"]
    body = lambda c: c["system"].split("=== LECTURES ===")[1]   # noqa: E731
    assert len(body(hl)) < len(body(ctx))
    opts = env.svc.scope_options("c")
    assert [d["day_key"] for d in opts["days"]] == [DAY2, DAY1]          # newest first
    assert opts["days"][1]["parts"] == 2 and opts["days"][1]["stars"] == 2


def test_turns_stream_reuse_one_process_and_persist(env):
    chat = env.svc.create("c", [DAY1], model="haiku")
    assert chat["title"] == "" and chat["context_tokens"] > 0 and chat["days"][0]["label"] == "Tue 13 Oct"
    e1 = ask(env, chat["id"], "What is on the quiz?")
    assert e1["state"] == "done" and e1["text"] == "Answer 1 (images 0, replay False, lectures 1): What is on the quiz?"
    assert any(e["state"] == "streaming" and e["text"] for e in env.events if e.get("type") == "chat")
    e2 = ask(env, chat["id"], "And the homework?")
    assert e2["text"].startswith("Answer 2 (images 0, replay False")       # same process remembers the chat
    assert e2["usage"]["cache_read_input_tokens"] == 100
    v = env.svc.view(chat["id"])
    assert [(m["role"], m["status"]) for m in v["messages"]] == [("user", "done"), ("assistant", "done")] * 2
    assert v["title"] == "What is on the quiz?" and v["limits"]["five_hour"]["used"] == 0.25
    assert json.loads(env.lib.get_meta("claude_limits"))["seven_day"]["used"] == 0.5
    assert [c["id"] for c in env.svc.list()] == [chat["id"]]


def test_new_process_gets_the_earlier_conversation(env):
    chat = env.svc.create("c", [DAY1, DAY2])
    ask(env, chat["id"], "First question")
    env.svc._live[chat["id"]].terminate()                 # e.g. closed after being idle, or crashed
    e = ask(env, chat["id"], "Second question")
    assert e["text"].startswith("Answer 1 (images 0, replay True, lectures 2)")
    text = replay_text(env.lib.chat_messages(chat["id"])[:2])
    assert "Student: First question" in text and "You: Answer 1" in text


def test_stop_keeps_the_partial_answer_and_the_conversation(env):
    chat = env.svc.create("c", [DAY1])
    seq = env.svc.send(chat["id"], "SLOW please")["seq"]
    assert wait_for(lambda: any(e.get("seq") == seq and e["text"] for e in env.events if e.get("type") == "chat"))
    env.svc.stop(chat["id"])
    assert wait_for(lambda: final(env.events, chat["id"], seq) is not None)
    f = final(env.events, chat["id"], seq)
    assert f["state"] == "stopped" and f["text"].startswith("word") and len(f["text"]) < 1500
    assert env.lib.chat_messages(chat["id"])[1]["status"] == "stopped"
    assert ask(env, chat["id"], "next")["text"].startswith("Answer 2 (images 0, replay False")


def test_stop_before_a_new_process_got_its_first_message(env):
    chat = env.svc.create("c", [DAY1])
    ask(env, chat["id"], "First question")
    env.svc._live[chat["id"]].terminate()                 # next question needs a new process + replay
    original = env.svc._session

    def session_then_stop(c):                             # Stop pressed while the new process starts
        res = original(c)
        env.svc.stop(c["id"])
        return res

    env.mp.setattr(env.svc, "_session", session_then_stop)
    seq = env.svc.send(chat["id"], "Second")["seq"]
    assert wait_for(lambda: final(env.events, chat["id"], seq) is not None)
    assert final(env.events, chat["id"], seq)["state"] == "stopped"
    env.mp.setattr(env.svc, "_session", original)
    assert ask(env, chat["id"], "Third")["text"].startswith("Answer 1 (images 0, replay True")


def test_partial_answer_is_saved_and_delete_stops_it(env):
    env.mp.setattr(chat_mod, "SAVE_EVERY_S", 0.05)
    chat = env.svc.create("c", [DAY1])
    seq = env.svc.send(chat["id"], "SLOW please")["seq"]
    assert wait_for(lambda: env.lib.chat_messages(chat["id"])[1]["text"].startswith("word"))   # saved while streaming
    env.svc.delete(chat["id"])
    assert wait_for(lambda: final(env.events, chat["id"], seq) is not None)
    assert final(env.events, chat["id"], seq)["state"] == "stopped"
    assert env.svc.list() == [] and env.lib.get_chat(chat["id"]) is None
    assert wait_for(lambda: not env.svc._live and not env.svc._running)


def test_errors_are_reported_and_the_chat_recovers(env):
    chat = env.svc.create("c", [DAY1])
    e = ask(env, chat["id"], "CRASH now")
    assert e["state"] == "error" and "boom" in e["error"]
    assert ask(env, chat["id"], "again")["text"].startswith("Answer 1 (images 0, replay True")
    e = ask(env, chat["id"], "LIMIT")
    assert e["state"] == "error" and "usage limit" in e["error"]
    with pytest.raises(RuntimeError, match="at least one"):
        env.svc.create("c", [])
    with pytest.raises(ValueError):
        env.svc.send(chat["id"], "   ")


def test_try_again_replaces_the_failed_answer(env):
    env.mp.setenv("FAKE_CLAUDE_FLAKY_MARKER", str(env.tmp / "flaky-once"))
    chat = env.svc.create("c", [DAY1])
    assert ask(env, chat["id"], "First")["state"] == "done"
    failed = ask(env, chat["id"], "FLAKY question")
    assert failed["state"] == "error"
    with pytest.raises(RuntimeError):
        env.svc.retry(chat["id"], 2)                      # not the last answer
    res = env.svc.retry(chat["id"], failed["seq"])
    assert res["question"] == "FLAKY question" and res["removed_from"] == 3
    assert wait_for(lambda: final(env.events, chat["id"], res["seq"]) and
                    final(env.events, chat["id"], res["seq"])["state"] == "done")
    msgs = env.lib.chat_messages(chat["id"])
    assert [(m["role"], m["status"]) for m in msgs] == [("user", "done"), ("assistant", "done")] * 2
    assert [m["text"] for m in msgs if m["role"] == "user"] == ["First", "FLAKY question"]
    assert "replay True" in msgs[-1]["text"]              # a fresh process got the clean history


def test_limit_messages_are_explained():
    assert ai.friendly_error("You've hit your session limit · resets 10:40am (Europe/Istanbul)") == \
        "You have reached your Claude usage limit. It resets 10:40am (Europe/Istanbul)."
    assert ai.friendly_error("Claude AI usage limit reached|1792568400").startswith("You have reached your Claude usage limit. It resets at ")
    assert ai.friendly_error("Invalid API key · Please run /login").startswith("Claude Code is not signed in")
    assert ai.friendly_error("something else") == "Claude: something else"
    from studia import i18n

    i18n.set_language("tr")
    try:                                  # Claude Code's English time, in the Turkish way
        assert ai.friendly_error("You've hit your session limit · resets 10:40am (Europe/Istanbul)").endswith(": 10:40.")
        assert ai.friendly_error("You've hit your weekly limit · resets Nov 1, 9pm (Europe/Istanbul)").endswith(": 1 Kas 21:00.")
        assert ai.friendly_error("You've hit your limit · resets soon").endswith(": soon.")          # unknown: as said
    finally:
        i18n.set_language("en")


def test_a_limit_that_resets_another_day_says_the_day():
    from studia import i18n

    at = int(dt.datetime(2025, 11, 3, 10, 40).timestamp())
    assert ai.friendly_error(f"Claude AI usage limit reached|{at}") == \
        "You have reached your Claude usage limit. It resets at Mon 03 Nov, 10:40."     # the English of 2.4
    i18n.set_language("tr")
    try:
        assert ai.friendly_error(f"Claude AI usage limit reached|{at}").endswith(": 3 Kas Pzt, 10:40.")
    finally:
        i18n.set_language("en")


def test_too_much_material_is_refused(env):
    env.mp.setattr(chat_mod, "MAX_CONTEXT_TOKENS", 50)
    with pytest.raises(RuntimeError, match="too much"):
        env.svc.create("c", [DAY1, DAY2])


def test_slides_go_with_the_first_message_only(env):
    img = env.tmp / "s1.jpg"
    img.write_bytes(b"jpg")
    store.save_slides("r0", [Slide(1, 0.0, 8.0, "slides/x/s1.jpg", str(img)), Slide(2, 8.0, 20.0, "x", str(img)),
                             Slide(1, 20.0, 30.0, "slides/x/s1.jpg", str(img))])
    env.mp.setattr(ai, "image_block", lambda p: {"type": "image", "source": {"type": "base64",
                                                                               "media_type": "image/jpeg", "data": ""}})
    assert env.svc.context("c", [DAY1], slides=False)["slides_total"] == 2      # known even when not attached
    chat = env.svc.create("c", [DAY1], slides=True)
    assert chat["slides"] and chat["images"] == 2
    assert "(slide 1)" in env.svc.context("c", [DAY1], slides=True)["system"]
    assert ask(env, chat["id"], "q1")["text"].startswith("Answer 1 (images 2")
    assert ask(env, chat["id"], "q2")["text"].startswith("Answer 2 (images 0")


def test_chatgpt_gets_the_labelled_slides_and_the_conversation_with_every_question(env):
    img = env.tmp / "s1.jpg"
    img.write_bytes(b"jpg")
    store.save_slides("r0", [Slide(1, 0.0, 8.0, "slides/x/s1.jpg", str(img)), Slide(2, 8.0, 20.0, "x", str(img))])
    env.mp.setattr(ai, "image_block", lambda p: {"type": "image", "source": {"type": "base64",
                                                                               "media_type": "image/jpeg", "data": "x"}})
    appconfig.update({"ai_provider": "chatgpt"})
    sent = []
    env.mp.setattr(ai, "_chatgpt_one_shot", lambda system, content, *a, **k: sent.append(content)
                   or {"text": f"Answer {len(sent)}", "usage": {}})
    chat = env.svc.create("c", [DAY1], slides=True)
    ask(env, chat["id"], "q1")
    ask(env, chat["id"], "q2")
    for content in sent:                                  # each request is new: the slides go with every one
        assert [b["type"] for b in content] == ["text", "text", "image", "text", "image", "text"]
        assert content[1]["text"] == "[Part 1, slide 1]" and content[3]["text"] == "[Part 1, slide 2]"
    assert "[Student]\nq1\n\n[You]\nAnswer 1" in sent[1][-1]["text"]
    assert sent[1][-1]["text"].endswith("=== NEW QUESTION ===\nq2")
    env.svc._live.pop(chat["id"])                         # e.g. the app was restarted
    ask(env, chat["id"], "q3")
    assert "[Student]\nq1" in sent[2][-1]["text"] and "[You]\nAnswer 2" in sent[2][-1]["text"]
    assert "We already talked" not in sent[2][-1]["text"]


def test_the_offline_model_reads_the_passages_that_match_each_question(env):
    from studia import local_llm

    filler = "We continue with the next example on the board and look at the numbers once more."
    segs = [Segment(i * 10.0, i * 10.0 + 6, filler, "en", 0.9) for i in range(300)]
    segs[180] = Segment(1800, 1806, "Bayes theorem relates the conditional probabilities of two events.", "en", 0.9)
    t = Transcript(delivery_id="r2", segments=segs, duration_s=3000, speech_s=1800, language_seconds={"en": 1800},
                   model="m", device="cpu", engine_version="t", created_at="2026-10-20T10:00:00")
    env.lib.save_transcript(t, store.save_transcript(t))
    appconfig.update({"ai_provider": "local"})
    seen = []
    env.mp.setattr(local_llm, "budget", lambda key: 2500)
    env.mp.setattr(local_llm, "count_tokens", lambda text, key: int(len(text) / 3.5))
    env.mp.setattr(local_llm, "generate", lambda msgs, key, *a, **k: seen.append(msgs)
                   or {"text": f"Answer {len(seen)}", "usage": {"input_tokens": 1, "output_tokens": 1}})
    chat = env.svc.create("c", [DAY2])
    assert ask(env, chat["id"], "What does Bayes theorem say?")["state"] == "done"
    system = seen[0][0]["content"]
    assert "Bayes theorem relates" in system and "# MATH 240" in system and "## Part 1" in system
    assert sum(local_llm.count_tokens(m["content"], "k") for m in seen[0]) < 2500
    ask(env, chat["id"], "Can you give an example?")       # a follow-up stays on the topic, with the last exchange
    assert "Bayes theorem relates" in seen[1][0]["content"]
    assert [m["role"] for m in seen[1]] == ["system", "user", "assistant", "user"]
    env.svc._live.pop(chat["id"])                         # e.g. the app was restarted
    ask(env, chat["id"], "And one more?")
    assert [m["content"] for m in seen[2][1:]] == ["Can you give an example?", "Answer 2", "And one more?"]
    assert "[P2 14:05]" in system and "[L2 P1" not in system      # one lecture: cited by part and time
    many = env.svc.create("c", [DAY1, DAY2])
    ask(env, many["id"], "What is on the quiz?")
    assert "[L2 P1 05:10]" in seen[-1][0]["content"]            # several: by lecture too, as the app links them


def test_the_offline_model_gets_the_assessments_not_the_announcements_and_the_newest_lectures_first():
    from studia import local_llm

    brief = ("Course: MATH 240 Probability\nAssessments the lecturer announced:\n  Quiz 1: week 3\n  Quiz 2: week 5\n"
             "Latest announcements:\n  [2026-10-07] Room change: the lecture moves to M01.")
    assert local_llm._course_essentials(brief) == ("Course: MATH 240 Probability\nAssessments the lecturer announced:\n"
                                                   "  Quiz 1: week 3\n  Quiz 2: week 5")
    lectures = "\n\n".join(f"# L{n} · MATH 240\n## Part 1\n[00:{n:02d}] The quiz is next week, lecture {n}."
                           for n in range(1, 11))
    text = local_llm.lecture_excerpts(lectures, "When is the quiz?", "k", 40)      # room for a few of them only
    assert "lecture 10." in text and "lecture 1." not in text


def test_the_could_not_be_written_note_never_goes_into_the_files(env):
    ns = env.svc._s
    ns.write_outputs = types.MethodType(Services.write_outputs, ns)
    env.lib.set_warnings("r0", [{"code": "not_written", "level": "warning", "message": "Transcribed, but its files…"},
                                {"code": "quiet", "level": "warning", "message": "The audio is very quiet."}])
    out = ns.write_outputs("r0")
    text = Path(out["md"]).read_text(encoding="utf-8")
    assert "very quiet" in text and "could not be written" not in text and "Transcribed, but" not in text


def test_each_kind_of_ai_notes_has_its_own_file(env):
    ns = env.svc._s
    for name in ("ai_notes", "ai_inputs", "day_files"):
        setattr(ns, name, types.MethodType(getattr(Services, name), ns))
    ns._notes_path = Services._notes_path
    env.mp.setattr(ai, "run_claude", lambda prompt, text, images, **k: {"text": prompt[:20], "usage": {}, "model": "m"})
    notes = ns.ai_notes(DAY1, "notes", False, "sonnet")
    cards = ns.ai_notes(DAY1, "flashcards", False, "sonnet")
    assert notes.name.endswith(" - Study notes.md") and cards.name.endswith(" - Flashcards.md")
    assert notes.read_text(encoding="utf-8") != cards.read_text(encoding="utf-8")
    os.utime(notes, (1, 1))
    files = ns.day_files(DAY1)
    assert files["notes_kinds"] == ["flashcards", "notes"] and files["notes"] == str(cards)
    assert files["notes:notes"] == str(notes) and "notes:exam" not in files


def test_study_notes_label_each_slide_with_its_part_and_spread_a_long_days_slides(env):
    """Every part numbers its slides from 1: study notes sent the pictures unlabelled (part 2's "(slide 1)" read as
    the day's first picture, part 1's) and only the first 60 (the first part or two of a long day)."""
    from studia.api import Api

    ns = env.svc._s
    for name in ("ai_notes", "ai_inputs"):
        setattr(ns, name, types.MethodType(getattr(Services, name), ns))
    ns._notes_path = Services._notes_path
    img = env.tmp / "s.jpg"
    img.write_bytes(b"jpg")
    sent = []
    env.mp.setattr(ai, "one_shot", lambda system, content, *a, **k: sent.append(content) or {"text": ""})
    env.mp.setattr(ai, "image_block", lambda p: {"type": "image", "source": {"data": str(p)}})
    ai.run_claude("Notes.", "[00:00] The lecture.", [("[Part 2, slide 5]", img)])
    assert [b.get("text") or b["type"] for b in sent[0][1:3]] == ["[Part 2, slide 5]", "image"]   # right before it
    got = []
    env.mp.setattr(ai, "run_claude", lambda prompt, text, images, **k: got.append((prompt, images)) or
                   {"text": "n", "usage": {}, "model": "m"})
    env.mp.setattr(ai, "claude_info", lambda: {})
    for rid in ("r0", "r1"):           # slide 1 shown again later is sent once
        store.save_slides(rid, [Slide(1, 0.0, 8.0, "x", str(img)), Slide(2, 8.0, 20.0, "x", str(img)),
                                Slide(1, 20.0, 30.0, "x", str(img))])
    ns.ai_notes(DAY1, "notes", True, "sonnet")
    prompt, images = got[-1]
    assert [x for x, _ in images] == ["[Part 1, slide 1]", "[Part 1, slide 2]", "[Part 2, slide 1]", "[Part 2, slide 2]"]
    assert "each labelled [Part n, slide m]" in prompt and "Refer to a slide as (Part n, slide m)" in prompt
    assert "spread over the lecture" not in prompt
    for rid in ("r0", "r1"):           # a long day: 100 slides
        store.save_slides(rid, [Slide(i, i * 10.0, i * 10.0 + 9, "x", str(img)) for i in range(1, 51)])
    ns.ai_notes(DAY1, "notes", True, "sonnet")
    prompt, images = got[-1]
    labels = [x for x, _ in images]
    assert len(labels) == 60 and labels[0] == "[Part 1, slide 1]" and labels[-1] == "[Part 2, slide 50]"
    assert sum(x.startswith("[Part 2,") for x in labels) == 30 and len(set(labels)) == 60
    assert "60 of the 100 slides are attached, spread over the lecture" in prompt
    est = Api.ai_estimate(types.SimpleNamespace(_s=ns), DAY1, True)["estimate"]
    assert est["images"] == 60 and est["slides"] == 100


def test_study_notes_show_the_slides_they_explain_and_flashcards_dont_ask_for_them(env):
    """4.0: a slide's diagram goes next to the text that explains it: the AI names it on a line of its own, the saved
    notes link the slide's picture from their folder (any Markdown viewer, the app and the exports show it)."""
    from studia.outputs.figures import local_image

    ns = env.svc._s
    for name in ("ai_notes", "ai_inputs"):
        setattr(ns, name, types.MethodType(getattr(Services, name), ns))
    ns._notes_path = Services._notes_path
    img = env.tmp / "slide.jpg"
    img.write_bytes(b"\xff\xd8jpeg")
    for rid in ("r0", "r1"):
        store.save_slides(rid, [Slide(1, 0.0, 8.0, "x", str(img)), Slide(2, 8.0, 20.0, "x", str(img))])
    got = []
    answer = "## RC\n\nThe circuit.\n\n![The RC circuit](Part 2, slide 2)\n\n![Not sent](Part 7, slide 1)\n\nEnd."
    env.mp.setattr(ai, "run_claude", lambda prompt, text, images, **k: got.append(prompt) or
                   {"text": answer, "usage": {}, "model": "m"})
    env.mp.setattr(ai, "claude_info", lambda: {})
    path = ns.ai_notes(DAY1, "notes", True, "sonnet")
    assert "![what it shows, in a few words](Part n, slide m)" in got[-1] and "Write in English" in got[-1]
    text = path.read_text(encoding="utf-8")
    link = re.search(r"!\[The RC circuit \(Part 2, slide 2\)\]\(([^)]+)\)", text).group(1)
    assert not link.startswith(("file:", "/")) and local_image(link, path.parent) == img.resolve()
    assert "Not sent" not in text
    ns.ai_notes(DAY1, "flashcards", True, "sonnet")
    assert "![" not in got[-1] and "Refer to a slide as (Part n, slide m)" in got[-1]


def test_ai_notes_are_in_the_search_index_for_good_once_written(env):
    """The notes' rows were added without a commit: closing the app (os._exit) dropped them for good, and the open
    write kept the command line from opening the library."""
    ns = env.svc._s
    for name in ("ai_notes", "ai_inputs", "day_files", "notes_for_search"):
        setattr(ns, name, types.MethodType(getattr(Services, name), ns))
    ns._notes_path = Services._notes_path
    env.lib.index.notes = ns.notes_for_search
    env.mp.setattr(ai, "run_claude", lambda prompt, text, images, **k: {"text": "## Bayes\n\nThe posterior.", "usage": {},
                                                                        "model": "m"})
    ns.ai_notes(DAY1, "notes", False, "sonnet")
    assert not env.lib.db.in_transaction
    other = Library(env.lib.path)                        # another process, or the next start
    try:
        rows = other.db.execute("SELECT COUNT(*) FROM search_doc WHERE kind='note' AND ref=?", (DAY1,)).fetchone()[0]
    finally:
        other.db.close()
    assert rows >= 1


def test_another_ai_choice_gets_its_own_session(env):
    chat = env.svc.create("c", [DAY1])
    ask(env, chat["id"], "q1")
    first = env.svc._live[chat["id"]]
    made = []

    class Stub:
        key, last_used = None, 0.0

        def alive(self):
            return True

        def close(self):
            pass

        terminate = close

    env.mp.setattr(ai, "new_session", lambda *a, **k: made.append(Stub()) or made[-1])
    appconfig.update({"ai_provider": "chatgpt"})
    session, _ = env.svc._session(env.lib.get_chat(chat["id"]))
    assert session is made[0] and session is not first and session.key[0] == "chatgpt"
    assert env.svc._session(env.lib.get_chat(chat["id"]))[0] is session


def test_export_rename_and_delete(env):
    chat = env.svc.create("c", [DAY1])
    ask(env, chat["id"], "Summarise")
    assert env.svc.update(chat["id"], {"title": "  Quiz   prep "})["title"] == "Quiz prep"
    path = env.svc.export(chat["id"])
    text = path.read_text(encoding="utf-8")
    assert path.parent.name == "AI chats" and path.name.endswith(f"Quiz prep [{chat['id'][:6]}].md")
    assert "**You:** Summarise" in text and "**Claude:**" in text and "Tuesday, 13 October 2026" in text
    env.svc.delete(chat["id"])
    assert env.svc.list() == [] and env.lib.chat_messages(chat["id"]) == []


def test_an_exported_chat_names_the_ai_that_answered_not_the_one_chosen_now(env):
    """Weeks of chatting with Claude, then the student switches to ChatGPT (the Claude plan ended) and exports an old
    chat: it still says Claude wrote the answers (the file is kept, or handed in)."""
    from studia import exports

    chat = env.svc.create("c", [DAY1])
    ask(env, chat["id"], "Summarise")
    assert env.lib.chat_messages(chat["id"])[-1]["usage"]["ai"] == "claude"
    appconfig.update({"ai_provider": "chatgpt"})
    text = env.svc.export(chat["id"]).read_text(encoding="utf-8")
    assert "**Claude:**" in text and "Chat with Claude in IEUStudia" in text and "**Model:** Claude " in text
    assert "ChatGPT" not in text
    doc = exports.copy_text(types.SimpleNamespace(lib=env.lib, chat=env.svc, day=env.svc._s.day), f"chat:{chat['id']}", "")
    assert "Chat with Claude" in doc and "Claude " in doc and "ChatGPT" not in doc


def test_a_chat_without_a_title_or_a_question_is_exported_as_new_chat(env):
    chat = env.svc.create("c", [DAY1])             # its first question could not be sent
    path = env.svc.export(chat["id"])
    assert path.read_text(encoding="utf-8").startswith("# New chat\n")
    assert path.name.endswith(f" - New chat [{chat['id'][:6]}].md")


def test_a_chat_keeps_its_reasoning_level(env):
    made = []

    class Stub:
        key, last_used, effort = None, 0.0, ""

        def alive(self):
            return True

        def close(self):
            pass

        terminate = close

    def new_session(*a, **k):
        made.append(Stub())
        made[-1].effort = k.get("effort")
        return made[-1]

    env.mp.setattr(ai, "new_session", new_session)
    env.mp.setenv("CODEX_HOME", str(env.tmp))                         # no model list of the Codex CLI either
    chat = env.svc.create("c", [DAY1], level="high")
    assert chat["effort"] == "high"
    first, _ = env.svc._session(env.lib.get_chat(chat["id"]))
    assert first.effort == "high"
    assert env.svc.update(chat["id"], {"effort": "max"})["effort"] == "max"
    second, _ = env.svc._session(env.lib.get_chat(chat["id"]))
    assert second is not first and second.effort == "max"           # Claude's process keeps its level: a new one
    appconfig.update({"ai_provider": "chatgpt"})                     # no model list here: low, medium and high
    gpt, _ = env.svc._session(env.lib.get_chat(chat["id"]))
    assert gpt.effort == "high"                                      # the nearest level it takes
    env.svc.update(chat["id"], {"effort": "low", "title": "Quiz"})
    assert env.svc._session(env.lib.get_chat(chat["id"]))[0] is gpt and gpt.effort == "low"     # the next question's
    env.svc.update(chat["id"], {"effort": "ultra"})                  # not a level: ignored
    assert env.lib.get_chat(chat["id"])["scope"]["effort"] == "low"
    # a ChatGPT chat from before reasoning levels: its "Thorough" stays high
    env.lib.create_chat("old", "c", {"days": [DAY1], "focus": "full", "slides": False}, "opus")
    assert env.svc.view("old")["effort"] == "high"


def test_a_new_chatgpt_chat_takes_the_settings_level_not_its_model_s(env):
    env.mp.setenv("CODEX_HOME", str(env.tmp))                         # no model list: low, medium and high
    appconfig.update({"ai_provider": "chatgpt"})
    env.svc._s.settings.ai_effort = {"chatgpt.chat": "low"}
    chat = env.svc.create("c", [DAY1], model="sonnet")                # the dialog's select left alone
    assert env.lib.get_chat(chat["id"])["scope"]["effort"] == ""       # a chat from now: no old model level
    assert chat["effort"] == "low"
    env.svc._s.settings.ai_effort = {"chatgpt.chat": "high"}
    assert env.svc.view(chat["id"])["effort"] == "high"               # follows Settings until it is given its own
    env.lib.create_chat("old", "c", {"days": [DAY1], "focus": "full", "slides": False}, "opus")
    assert env.svc.summary(env.lib.get_chat("old"))["effort"] == "high"   # the chat list shows each chat's level
    assert env.svc.summary(env.lib.get_chat(chat["id"]))["effort"] == "high"
