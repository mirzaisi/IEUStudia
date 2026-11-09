"""Answers that are not whole are never taken for whole ones (deep QA round 1, llm/chatgpt-incomplete-or-refusal-as-
complete): ChatGPT's answer cut off by response.incomplete, a refusal, an answer "completed" with no text. And a chat
that no longer fits its model is said before it fails (llm/chat-size-not-rechecked).

ChatGPT is answered by test_service_errors' local HTTP server standing in for api.openai.com (no network)."""

import dataclasses as dc
import types

import pytest

from studia import aierrors, ai, appconfig, chatgpt, chatlimits, chatread
from test_chat import DAY1, DAY2, env, final, wait_for  # noqa: F401 - env is a fixture (the fake Claude Code)
from test_chatread import Streams, lim, notes_home, real_files  # noqa: F401 - notes_home is a fixture
from test_service_errors import ask, openai  # noqa: F401 - openai is a fixture

TEXT = [{"type": "response.output_text.delta", "delta": "### [Week04.pdf p.1]\nOhm's law $V = IR$ "},
        {"type": "response.output_text.delta", "delta": "...\n### [Week04.pdf p.2]\nThe voltage div"}]


def incomplete(reason):
    return {"type": "response.incomplete", "response": {"incomplete_details": {"reason": reason},
                                                        "usage": {"input_tokens": 900, "output_tokens": 4000}}}


def test_an_answer_cut_off_by_chatgpt_is_not_taken_for_a_whole_one(openai):
    openai.answers.append((200, {}, [*TEXT, incomplete("max_output_tokens")]))
    with pytest.raises(aierrors.Dropped, match=r"stopped before the end.*lower reasoning level") as cut:
        ask()
    assert cut.value.text and len(openai.requests) == 1          # some came: not asked again here (the caller knows)
    openai.answers.append((200, {}, [*TEXT, incomplete("content_filter")]))
    with pytest.raises(aierrors.Dropped, match=r"safety filter stopped its answer"):
        ask()
    openai.answers.append((200, {}, [*TEXT, incomplete("something_new")]))
    with pytest.raises(aierrors.Dropped, match=r"stopped its answer before the end \(something_new\)"):
        ask()
    # cut off before any text: said as before
    openai.answers.append((200, {}, [incomplete("max_output_tokens")]))
    with pytest.raises(RuntimeError, match=r"stopped before answering \(max_output_tokens\)"):
        ask()


def test_a_refusal_is_said_never_an_empty_answer(openai):
    refusal = [{"type": "response.output_item.added", "item": {"type": "message", "content": []}},
               {"type": "response.refusal.delta", "delta": "I can't help "},
               {"type": "response.refusal.delta", "delta": "with that."},
               {"type": "response.completed", "response": {"usage": {"input_tokens": 900, "output_tokens": 9}}}]
    openai.answers.append((200, {}, refusal))
    with pytest.raises(RuntimeError, match=r"ChatGPT declined to answer: I can't help with that\.") as no:
        ask()
    assert not isinstance(no.value, aierrors.Dropped)            # not asked again: it would decline again
    # the refusal only in the completed response's output
    only_there = {"type": "response.completed", "response": {"output": [{"type": "message", "content": [
        {"type": "refusal", "refusal": "No."}]}], "usage": {}}}
    openai.answers.append((200, {}, [{"type": "response.output_item.added", "item": {}}, only_there]))
    with pytest.raises(RuntimeError, match=r"declined to answer: No\."):
        ask()


def test_an_answer_completed_with_no_text_is_asked_again_then_said(openai):
    empty = [{"type": "response.output_item.added", "item": {"type": "reasoning"}},
             {"type": "response.completed", "response": {"usage": {"input_tokens": 900, "output_tokens": 300}}}]
    openai.answers += [(200, {}, empty), (200, {}, [{"type": "response.output_text.delta", "delta": "Hi"},
                                                    {"type": "response.completed", "response": {"usage": {}}}])]
    assert ask()["text"] == "Hi" and len(openai.requests) == 2 and openai.waits == [aierrors.DROP_DELAYS[0]]
    openai.answers += [(200, {}, empty)]
    with pytest.raises(aierrors.Dropped, match=r"finished without writing an answer"):
        ask(drops=0)


def test_a_reading_keeps_only_the_whole_pages_of_a_cut_off_answer(notes_home, tmp_path, monkeypatch):
    """End to end: page 7 of 10 is cut off by response.incomplete. Pages 1-6 are kept, page 7's half note is not (it
    was kept for good before: every later question reused it), and the part is asked again for pages 7-10."""
    appconfig.update({"ai_provider": "chatgpt"})
    monkeypatch.setattr(ai, "image_block", lambda p: {"type": "image", "source": {"media_type": "image/jpeg",
                                                                                  "data": "eA=="}})
    monkeypatch.setattr(chatgpt, "access_token", lambda: "token")
    monkeypatch.setattr(chatgpt, "models", lambda refresh=False: [{"slug": "gpt-6.1-sol"}])
    monkeypatch.setattr(chatgpt, "_measure_clock", lambda r: None)

    class Cut(Streams):
        def post(self, url, json=None, **kw):
            r = super().post(url, json=json, **kw)
            if len(self.bodies) == 1:         # page 7's label and half its note, then "incomplete" (not the end)
                lines = list(r.iter_lines())
                at = next(i for i, x in enumerate(lines) if b"Notes of [Week04.pdf p.7]: " in x)
                lines = lines[:at + 2] + self.event(incomplete("max_output_tokens"))
                r = types.SimpleNamespace(status_code=200, headers={}, iter_lines=lambda: iter(lines), close=lambda: None)
            return r

    api = Cut()
    monkeypatch.setattr(chatgpt, "_session", lambda: api)
    pages = real_files(tmp_path, ["Week04"], pages=10)
    gpt = lim(provider="chatgpt", model="", batch_pictures=10)
    r = chatread.read(pages, "q", gpt, chatread.default_request(gpt, ""), waits=(0.01,))
    assert r["read"] == 10 and not r["unread"] and len(api.bodies) == 2
    assert api.labels(2) == [p.label for p in pages[6:]]          # page 7 again: its cut-off note was not kept
    assert chatread.cached(pages, gpt)[pages[6].key].startswith("Notes of [Week04.pdf p.7]: $V = IR$")


# ---------------------------------------------------------------------------------------------------- a chat's size
def small(env, model, window):
    """``model``'s window (and its share for a chat's material) as small as ``window`` tokens: this test's lectures
    are a few hundred tokens."""
    real = chatlimits.of

    def of(provider="", m=""):
        got = real(provider, m)
        return dc.replace(got, window=window, context=window * 2 // 3) if got.model == model else got
    env.mp.setattr(chatlimits, "of", of)


def test_a_chat_too_big_for_a_smaller_model_keeps_its_model(env):
    """Sonnet to Haiku to save usage: refused as a new chat that big would be, and the chat keeps Sonnet."""
    chat = env.svc.create("c", [DAY1, DAY2], model="sonnet")
    small(env, "haiku", 600)
    with pytest.raises(RuntimeError, match=r"This chat is too big for Haiku \(~\dk tokens, its limit is 0k\)\. Keep "
                                           r"Sonnet, or start a new chat"):
        env.svc.update(chat["id"], {"model": "haiku"})
    assert env.lib.get_chat(chat["id"])["model"] == "sonnet"
    assert env.svc.update(chat["id"], {"model": "opus"})["model"] == "opus"        # a model as big: always fine
    assert env.svc.update(chat["id"], {"model": "sonnet", "title": "x"})["title"] == "x"


def test_a_chat_that_no_longer_fits_its_model_says_what_to_do_before_asking(env):
    """The AI's model got smaller since the chat was made (another AI or ChatGPT model in Settings): the question says
    so plainly, and no process is started that would fail with "Prompt is too long"."""
    chat = env.svc.create("c", [DAY1, DAY2], model="sonnet")
    small(env, "sonnet", 200)
    started = []
    env.mp.setattr(ai, "new_session", lambda *a, **k: started.append(a))
    seq = env.svc.send(chat["id"], "What is on the quiz?")["seq"]
    assert wait_for(lambda: final(env.events, chat["id"], seq) is not None)
    e = final(env.events, chat["id"], seq)
    assert e["state"] == "error" and not started
    assert "This chat no longer fits in Sonnet" in e["error"] and "Pick a bigger model in the chat's model menu" in e["error"]
    ctx = {"tokens": 300_000, "lim": dc.replace(chatlimits.of("claude", "sonnet"), provider="chatgpt", window=272_000)}
    assert "ChatGPT model chosen in Settings → AI (~300k tokens, it holds 272k)" in env.svc._too_big(ctx, "")
    assert env.svc._too_big({**ctx, "tokens": 271_000}, "") is None         # over the app's margin only: it still works


def test_the_ai_s_own_too_long_errors_are_said_plainly():
    told = "This chat's material and your question are more than {ai}'s model can read at once."
    assert ai.friendly_error("Prompt is too long").startswith(told.format(ai="Claude"))
    assert ai._claude_failure("API Error: 400 prompt is too long: 205000 tokens > 200000 maximum").args[0].startswith(
        told.format(ai="Claude"))
    body = {"error": {"code": "context_length_exceeded", "message": "Your input exceeds the context window of this model."}}
    assert chatgpt._api_error(400, body).startswith(told.format(ai="ChatGPT"))
    assert chatgpt._api_error(400, {"error": {"message": "Your input exceeds the context window of this model."}}) \
        .startswith(told.format(ai="ChatGPT"))
    assert ai._codex_error("unexpected status 400: context_length_exceeded").startswith(told.format(ai="ChatGPT"))
    assert ai.friendly_error("Claude AI usage limit reached|1792568400").startswith("You have reached")   # unchanged
