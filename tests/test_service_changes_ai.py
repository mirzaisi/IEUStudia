"""When ChatGPT (OpenAI's sign-in and API), Claude Code, the Codex CLI, YouTube (yt-dlp), Hugging Face or GitHub
answer in a form IEUStudia doesn't know: said as that service changing (health), not as a broken connection or an
empty answer, and what IEUStudia had stays (the ChatGPT sign-in is never emptied, the last model list is used)."""

import json
import sys
import types

import pytest
import requests

from studia import ai, chatgpt, components, health, updates, videos
from studia.health import ServiceChanged
from test_chatgpt import _Resp, _sign_in, _sse, openai  # noqa: F401 - the fake OpenAI (a fixture)


# ------------------------------------------------------------------ ChatGPT
def test_a_model_list_in_another_form_is_said_and_the_last_one_is_used(openai):
    _sign_in(openai)
    assert [m["slug"] for m in chatgpt.models()] == ["gpt-6.1-sol"] and not health.is_changed("openai")
    cache = chatgpt._models_path()
    cache.write_text(json.dumps({"at": 0, "models": json.loads(cache.read_text())["models"]}), encoding="utf-8")
    openai.models_answers = [(200, {"data": [{"id": "gpt-7"}]})]          # OpenAI's list, another way
    assert [m["slug"] for m in chatgpt.models()] == ["gpt-6.1-sol"]       # the last good list
    assert health.is_changed("openai", "models")
    openai.models_answers = [(404, {"detail": "Not Found"})]
    with pytest.raises(ServiceChanged, match="list your ChatGPT models"):
        chatgpt.models(refresh=True)
    chatgpt.models(refresh=True)                                         # as it was: over
    assert not health.is_changed("openai")


def test_a_renewal_in_another_form_never_empties_the_sign_in(openai, monkeypatch):
    _sign_in(openai)
    rec = chatgpt._load()
    rec["expires_at"] = 0
    chatgpt._save(rec)
    post = openai.post

    def renewal_v2(url, data=None, **kw):
        if url == chatgpt.TOKEN_URL and (data or {}).get("grant_type") == "refresh_token":
            return _Resp(200, {"token": {"access": "at-9"}, "refresh_token": "rt-9"})
        return post(url, data=data, **kw)

    monkeypatch.setattr(openai, "post", renewal_v2)
    with pytest.raises(ServiceChanged, match="sign you in to ChatGPT"):
        chatgpt.access_token()
    rec = chatgpt._load()
    assert rec["access_token"] == "at-1" and rec["refresh_token"] == "rt-9"   # nothing emptied, the new one kept
    assert chatgpt.signed_in()


def test_a_wifi_sign_in_page_instead_of_openai_is_not_openai_changing(openai):
    _sign_in(openai)
    portal = _Resp(200, ValueError("not JSON"))                # an HTML page where OpenAI's JSON should be
    openai.models_answers = [(200, ValueError("not JSON"))]
    with pytest.raises(RuntimeError, match="Can't reach ChatGPT") as e:
        chatgpt.models(refresh=True)
    assert not isinstance(e.value, ServiceChanged)
    rec = chatgpt._load()
    rec["expires_at"] = 0
    chatgpt._save(rec)
    post = openai.post
    openai.post = lambda url, data=None, **kw: portal if url == chatgpt.TOKEN_URL else post(url, data=data, **kw)
    with pytest.raises(RuntimeError, match="Can't reach ChatGPT"):
        chatgpt.access_token()
    assert chatgpt._load()["access_token"] == "at-1" and not health.is_changed("openai")   # (it was emptied before)


def test_a_sign_in_whose_tokens_come_in_another_form_is_said_and_keeps_nothing(openai):
    openai.token_answer = (200, {"tokens": {"id": "x"}})
    e = _sign_in(openai)
    assert e["state"] == "error" and "ChatGPT changed something on its side." in e["error"]
    assert health.is_changed("openai", "signin") and not chatgpt.signed_in()


def test_an_answer_stream_in_another_form_is_said_not_a_broken_connection(openai):
    _sign_in(openai)
    ask = lambda: chatgpt.one_shot("S", [{"type": "text", "text": "q"}])     # noqa: E731
    openai.stream_answers = [(200, _sse({"type": "answer.part", "text": "Hel"}, {"type": "answer.end"}))]
    with pytest.raises(ServiceChanged, match="read ChatGPT's answers"):
        ask()
    done = {"type": "response.completed", "response": {"usage": {"input_tokens": 3, "output_tokens": 1}, "output": [
        {"type": "message", "content": [{"type": "output_text", "text": "Hello"}]}]}}
    openai.stream_answers = [(200, _sse({"type": "response.created"}, done))]   # the text only in the end
    assert ask()["text"] == "Hello" and not health.is_changed("openai")
    openai.stream_answers = [(404, {"detail": "Not Found"})]                    # its address moved
    with pytest.raises(ServiceChanged):
        ask()
    openai.stream_answers = [(404, {"error": {"code": "model_not_found", "message": "The model does not exist."}})]
    with pytest.raises(RuntimeError, match="The model does not exist") as e:   # OpenAI's own words: no change
        ask()
    assert not isinstance(e.value, ServiceChanged)


def test_a_connection_that_ends_halfway_is_still_said_as_that(openai):
    _sign_in(openai)
    openai.stream_answers = [(200, _sse({"type": "response.created"}, {"type": "response.output_text.delta",
                                                                        "delta": "Hel"}))]
    with pytest.raises(RuntimeError, match="ended before the answer was complete") as e:
        chatgpt.one_shot("S", [{"type": "text", "text": "q"}])
    assert not isinstance(e.value, ServiceChanged) and not health.is_changed("openai")


# ------------------------------------------------------------------ Claude Code and the Codex CLI
def _printer(*lines, code=0):
    """A stand-in CLI: reads its input, prints these lines, ends with ``code``."""
    body = "; ".join(f"print({json.dumps(line)})" for line in lines)
    return [sys.executable, "-c", f"import sys; sys.stdin.read(); {body}; sys.exit({code})"]


@pytest.fixture
def claude(monkeypatch):
    monkeypatch.setattr(ai, "_require_claude", lambda: sys.executable)
    monkeypatch.setattr(ai, "claude_cli_info", lambda: {"available": True, "path": sys.executable, "version": "9.9.9"})

    def use(cmd):
        monkeypatch.setattr(ai, "_command", lambda model, effort, sp, partial=False: cmd)
    return use


def test_claude_codes_output_in_another_form_is_said(claude):
    claude(_printer(*[json.dumps({"kind": "message", "text": "Hi"})] * 3))
    with pytest.raises(ServiceChanged, match="read Claude's answers"):
        ai._claude_one_shot("S", [{"type": "text", "text": "x"}], timeout_s=30)
    claude(_printer(json.dumps({"type": "system"}), json.dumps({"type": "result", "subtype": "success",
                                                                "is_error": False, "answer": "Hi", "usage": {}})))
    with pytest.raises(ServiceChanged):                       # its result without the text where it was
        ai._claude_one_shot("S", [{"type": "text", "text": "x"}], timeout_s=30)
    claude(_printer(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Hi"}]}}),
                    json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "Hi",
                                "usage": {}})))
    assert ai._claude_one_shot("S", [{"type": "text", "text": "x"}], timeout_s=30)["text"] == "Hi"
    assert not health.is_changed("claude")


def test_claude_code_failing_is_still_said_as_its_failure(claude):
    claude([sys.executable, "-c", "import sys; sys.stdin.read(); sys.stderr.write('Error: Invalid API key - Please "
                                  "run /login\\n'); sys.exit(1)"])
    with pytest.raises(RuntimeError, match="Claude Code is not signed in") as e:
        ai._claude_one_shot("S", [{"type": "text", "text": "x"}], timeout_s=30)
    assert not isinstance(e.value, ServiceChanged) and not health.is_changed("claude")


def test_claude_codes_sign_in_answer_in_another_form_is_said(monkeypatch):
    monkeypatch.setattr(ai, "claude_cli_info", lambda: {"available": True, "path": "claude.exe", "version": "9.9.9"})
    monkeypatch.setattr(ai, "_run", lambda cmd, timeout=20, env=None: (0, '{"signedIn": true, "account": "x"}'))
    got = ai.claude_auth()
    assert got["logged_in"] is False and "Claude Code changed something on its side." in got["error"]
    assert health.is_changed("claude", "signin")
    monkeypatch.setattr(ai, "_run", lambda cmd, timeout=20, env=None: (0, '{"loggedIn": true, "subscriptionType": "max"}'))
    assert ai.claude_auth()["logged_in"] and not health.is_changed("claude")


@pytest.fixture
def codex(monkeypatch):
    monkeypatch.setattr(ai, "_codex_model", lambda: "")

    def use(cmd):
        monkeypatch.setattr(ai, "_codex_command", lambda work, model, effort, images=(): cmd)
    return use


def test_the_codex_clis_output_in_another_form_is_said(codex):
    ask = lambda: ai._codex_once("S", [{"type": "text", "text": "x"}], "sonnet", "", None, None, 30)   # noqa: E731
    codex(_printer(*[json.dumps({"event": "message", "text": "Hi"})] * 3))
    with pytest.raises(ServiceChanged, match="Codex CLI's answers"):
        ask()
    codex(_printer(json.dumps({"type": "thread.started"}),
                   json.dumps({"type": "item.completed", "item": {"type": "assistant_text", "content": "Hi"}}),
                   json.dumps({"type": "turn.completed", "usage": {}})))
    with pytest.raises(ServiceChanged):                       # a turn that ended without an answer it reads
        ask()
    codex(_printer(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "Hi"}}),
                   json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1}})))
    assert ask()["text"] == "Hi" and not health.is_changed("codex")
    codex(_printer(json.dumps({"type": "error", "message": "stream disconnected before completion"}), code=1))
    with pytest.raises(RuntimeError, match="could not be reached") as e:
        ask()
    assert not isinstance(e.value, ServiceChanged)


# ------------------------------------------------------------------ YouTube through yt-dlp
BROKEN = ("ERROR: [youtube] dQw4w9WgXcQ: Unable to extract initial player response; please report this issue on "
          "https://github.com/yt-dlp/yt-dlp/issues?q= , filling out the appropriate issue template.")


def test_yt_dlp_that_no_longer_reads_youtube_is_said_as_needing_an_update():
    err = videos._video_error(Exception(BROKEN), "x")
    assert err.kind == "changed" and "needs an update" in str(err)
    assert not health.is_changed("youtube")                  # one video alone: said for it, not a warning on Home

    def broken(key):
        raise videos._video_error(Exception(BROKEN), "x")

    videos._run_many(["a", "b", "c"], broken, 2, None, None, lambda k, r: k)
    assert health.is_changed("youtube", "videos") and "failed on all 3 (changed)" in health.details()
    health.ok("youtube", "videos")
    net = videos._video_error(Exception("Unable to download webpage: <urlopen error [Errno 11001] getaddrinfo "
                                        "failed>"), "x")
    bot = videos._video_error(Exception("Sign in to confirm you're not a bot. please report this issue"), "x")
    gone = videos._video_error(Exception("ERROR: [youtube] abc: Video unavailable"), "x")
    assert (net.kind, bot.kind, gone.kind) == ("network", "bot", "unavailable") and not health.is_changed("youtube")


def test_every_video_or_search_failing_alike_is_youtube_changing():
    label = lambda k, r: k                                    # noqa: E731

    def fail(kind):
        def run(key):
            raise videos.VideoError("x", kind)
        return run

    videos._run_many(["a", "b", "c"], fail("error"), 2, None, None, label)
    assert health.is_changed("youtube")
    health.ok("youtube", "videos")
    videos._run_many(["a", "b", "c"], lambda k: [], 2, None, None, label, empty_changed=True)   # every search empty
    assert health.is_changed("youtube")
    health.ok("youtube", "videos")
    videos._run_many(["a", "b"], fail("error"), 2, None, None, label)             # too few to tell
    videos._run_many(["a", "b", "c"], fail("rate_limit"), 2, None, None, label)   # YouTube limiting, not changed
    videos._run_many(["a", "b", "c"], lambda k: [] if k != "c" else [object()], 2, None, None, label)
    assert not health.is_changed("youtube")


# ------------------------------------------------------------------ Hugging Face and GitHub
class _Page:
    def __init__(self, status=200, body=None, text="", headers=None):
        self.status_code, self._body, self.text, self.headers = status, body, text, headers or {}

    def json(self):
        if self._body is None:
            raise ValueError("not JSON")
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code), response=types.SimpleNamespace(status_code=self.status_code))


def test_hugging_faces_file_list_in_another_form_is_said():
    pats = ["model.bin", "config.json"]
    assert components.hf_files(_Page(body={"siblings": [{"rfilename": "model.bin", "size": 9}]}), "r", pats) == \
        [("model.bin", 9)] and not health.is_changed("huggingface")
    with pytest.raises(ServiceChanged, match="download speech or offline AI models"):
        components.hf_files(_Page(body={"files": [{"path": "model.bin"}]}), "r", pats)
    health.ok("huggingface", "models")
    with pytest.raises(requests.HTTPError):                    # (said by httpdl: "doesn't have this file any more")
        components.hf_files(_Page(status=404, body={}), "r", pats)
    assert health.is_changed("huggingface")


class _GitHub:
    def __init__(self, api, latest):
        self.api, self.latest = api, latest

    def get(self, url, **kw):
        return self.api if "api.github.com" in url else self.latest


def test_githubs_release_answers_in_another_form_are_said_but_a_wifi_page_is_not(monkeypatch):
    monkeypatch.delenv("STUDIA_UPDATE_FEED", raising=False)
    ok = _GitHub(_Page(body={"tag_name": "v9.0.0", "assets": [], "body": "", "html_url": "u"}), None)
    assert updates._latest(ok)["version"] == "9.0.0" and not health.is_changed("github")
    moved = _GitHub(_Page(body={"message": "Moved Permanently", "url": "x"}), _Page(status=404))
    with pytest.raises(RuntimeError):
        updates._latest(moved)
    assert health.is_changed("github", "releases")
    health.ok("github", "releases")
    portal = _GitHub(_Page(text="<html>Sign in to the Wi-Fi</html>"), _Page(text="<html>Sign in</html>"))
    with pytest.raises(RuntimeError):
        updates._latest(portal)
    assert not health.is_changed("github")


def test_searches_in_a_channel_that_has_no_video_on_the_topics_are_no_change(monkeypatch):
    """3Blue1Brown has no video on Thevenin, MOSFETs or BJT biasing: its three empty in-channel searches said
    "YouTube changed something on its side" on Home."""
    monkeypatch.setattr(videos, "channel_videos", lambda cid, q, limit=4, cancel=None: [])
    pairs = [("UCYO_jab_esuFRV4b17AJtAw", q) for q in ("Thevenin equivalent circuit", "MOSFET small signal model",
                                                      "BJT biasing")]
    got = videos.channel_search_many(pairs)
    assert all(v == [] for v in got.values()) and not health.is_changed("youtube")
