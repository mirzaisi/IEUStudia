"""ChatGPT's model list (fetched when its one-day cache is old): a failure on OpenAI's side or no network falls back to
the older list, and without one it is asked again like the request itself, not failing the plan at once."""

import json
import types

import pytest
import requests

from studia import aierrors, chatgpt
from studia.cancel import Cancelled

ROWS = [{"slug": "gpt-6.1-sol", "display_name": "GPT-6.1-Sol", "levels": ["low"]}]


@pytest.fixture
def fake(monkeypatch, tmp_path):
    monkeypatch.setattr(chatgpt, "_models_path", lambda: tmp_path / "chatgpt-models.json")
    monkeypatch.setattr(chatgpt, "access_token", lambda: "token")
    answers, waits = [], []

    def get(url, **kw):
        a = answers.pop(0)
        if isinstance(a, Exception):
            raise a
        status, body = a
        return types.SimpleNamespace(status_code=status, json=lambda: body, text=json.dumps(body), headers={})
    monkeypatch.setattr(chatgpt, "_session", lambda: types.SimpleNamespace(get=get))

    def wait(seconds, cancel):
        waits.append(seconds)
        if cancel is not None and cancel.is_set():
            raise Cancelled()
    monkeypatch.setattr(aierrors, "wait", wait)
    return types.SimpleNamespace(answers=answers, waits=waits, path=tmp_path / "chatgpt-models.json")


def test_an_old_list_is_used_when_openai_fails_or_the_network_is_down(fake):
    fake.path.write_text(json.dumps({"at": 0, "models": ROWS}), encoding="utf-8")       # a day old and more
    fake.answers += [(502, {}), requests.ConnectionError("down"), (500, {"error": {}})]
    assert chatgpt.models() == ROWS and chatgpt.models() == ROWS
    with pytest.raises(aierrors.ServiceError):                    # asked for a fresh list: no older one instead
        chatgpt.models(refresh=True)
    fake.answers.append((200, {"models": [{"slug": "gpt-7"}]}))
    assert [m["slug"] for m in chatgpt.models()] == ["gpt-7"]     # a working answer still replaces it
    fake.path.write_text(json.dumps({"at": 0, "models": ROWS}), encoding="utf-8")
    fake.answers.append((401, {}))                                 # not a failure on OpenAI's side: said as it is
    with pytest.raises(RuntimeError, match="did not accept the sign-in"):
        chatgpt.models()


def test_without_an_old_list_a_request_asks_for_it_again(fake, monkeypatch):
    sent = []
    monkeypatch.setattr(chatgpt, "_stream", lambda body, cancel, on_text, timeout_s: sent.append(body) or ("Hi", {}))
    fake.answers += [(503, {}), (500, {}), (200, {"models": [{"slug": "gpt-7"}]})]
    res = chatgpt.one_shot("Be brief.", [{"type": "text", "text": "hello"}])
    assert res["text"] == "Hi" and sent[0]["model"] == "gpt-7" and fake.waits == list(aierrors.DELAYS[:2])

    fake.path.unlink()
    fake.waits.clear()
    fake.answers += [(500, {})] * 4
    with pytest.raises(aierrors.ServiceError) as err:
        chatgpt.one_shot("Be brief.", [{"type": "text", "text": "hello"}])
    assert err.value.tries == 4 and "IEUStudia asked 4 times" in str(err.value) and len(fake.waits) == 3
