"""When the AI service fails on its side (the owner's study plan stopped at "What to learn" with "ChatGPT: HTTP 500"):
"Continue with ChatGPT" asks again after a 5xx or a 429 that is not the usage limit (respecting Retry-After, never once
Stop was pressed), keeps the error's body in the log without tokens, and the student reads what happened and what to
do - for ChatGPT, the Codex CLI and Claude Code alike. And a plan names the AI that really works on it: the ChatGPT
model and its reasoning level, never "(sonnet)" while the AI is ChatGPT.

ChatGPT is answered by a real local HTTP server standing in for api.openai.com (status, headers, body as sent)."""

import http.server
import json
import logging
import threading

import pytest

from studia import aierrors, ai, appconfig, chatgpt, plans
from studia.cancel import Cancelled
from test_plans import env, fake_ask, wait_done  # noqa: F401 - env is a fixture

OK = [{"type": "response.output_text.delta", "delta": "Hi"}, {"type": "response.completed", "response": {"usage": {}}}]


class FakeOpenAI(http.server.ThreadingHTTPServer):
    """api.openai.com/v1/responses: answers in the order given, (status, headers, body: dict, text or SSE events)."""

    def __init__(self):
        self.answers, self.requests = [], []

        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                outer.requests.append({"path": self.path, "auth": self.headers.get("Authorization"),
                                       "body": json.loads(body or b"{}")})
                status, headers, payload = outer.answers.pop(0)
                if isinstance(payload, list):
                    data = b"".join(b"data: " + json.dumps(ev).encode() + b"\n\n" for ev in payload)
                    ctype = "text/event-stream"
                else:
                    data = (json.dumps(payload) if isinstance(payload, dict) else payload).encode()
                    ctype = "application/json" if isinstance(payload, dict) else "text/html"
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        super().__init__(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.serve_forever, daemon=True).start()


@pytest.fixture
def openai(monkeypatch):
    server = FakeOpenAI()
    monkeypatch.setattr(chatgpt, "API", f"http://127.0.0.1:{server.server_address[1]}/v1")
    monkeypatch.setattr(chatgpt, "access_token", lambda: "eyJsecret.token.value")
    monkeypatch.setattr(chatgpt, "models", lambda refresh=False: [{"slug": "gpt-6.1-sol", "display_name": "GPT-6.1-Sol"}])
    waits = []

    def wait(seconds, cancel):                     # no real waiting: what would have been waited is recorded
        waits.append(seconds)
        if cancel is not None and cancel.is_set():
            raise Cancelled()
    monkeypatch.setattr(aierrors, "wait", wait)
    server.waits = waits
    yield server
    server.shutdown()
    server.server_close()


def ask(**kw):
    return chatgpt.one_shot("Be brief.", [{"type": "text", "text": "hello"}], effort="xhigh", **kw)


def test_a_500_is_asked_again_and_then_answered(openai, caplog):
    openai.answers = [(500, {}, {"error": {"message": "The server had an error while processing your request.",
                                           "type": "server_error"}}),
                      (502, {}, "<html>Bad gateway</html>"), (200, {}, OK)]
    with caplog.at_level(logging.INFO, logger="studia"):
        assert ask()["text"] == "Hi"
    assert len(openai.requests) == 3 and openai.waits == list(aierrors.DELAYS[:2])
    assert openai.requests[0]["body"] == openai.requests[2]["body"]          # the same request each time
    assert "server had an error while processing" in caplog.text and "Bad gateway" in caplog.text   # the body is kept
    assert "eyJsecret" not in caplog.text                                     # never the token


def test_after_the_last_try_the_student_reads_what_happened_and_what_to_do(openai):
    openai.answers = [(500, {}, "")] * 4
    with pytest.raises(aierrors.ServiceError) as err:
        ask()
    msg = str(err.value)
    assert len(openai.requests) == 4 and err.value.status == 500 and err.value.tries == 4
    assert "HTTP 500" not in msg and "ChatGPT had a problem on its side" in msg and "error 500" in msg
    assert "not caused by you" in msg and "asked 4 times" in msg and "Wait a few minutes" in msg
    assert "smaller request" in msg


def test_retry_after_is_respected_and_a_long_one_is_not_waited_for(openai):
    openai.answers = [(503, {"Retry-After": "20"}, {}), (200, {}, OK)]
    assert ask()["text"] == "Hi" and openai.waits == [20.0]
    openai.waits.clear()
    openai.answers = [(429, {"Retry-After": "600"}, {"error": {"message": "Rate limit reached"}})]
    with pytest.raises(aierrors.ServiceError, match="too many requests right now") as err:
        ask()
    assert err.value.status == 429 and openai.waits == []


def test_the_usage_limit_and_refusals_are_not_asked_again(openai):
    openai.answers = [(429, {}, {"error": {"code": "subscription_sharing_usage_limit_exceeded"}})]
    with pytest.raises(RuntimeError, match="usage limit") as err:
        ask()
    assert not isinstance(err.value, aierrors.ServiceError) and len(openai.requests) == 1
    openai.answers = [(400, {}, {"error": {"message": "Invalid value: 'input'."}})]
    with pytest.raises(RuntimeError, match="^ChatGPT: Invalid value"):
        ask()
    assert len(openai.requests) == 2 and openai.waits == []


def test_stop_ends_the_wait_and_a_stopped_request_is_not_asked_again(openai):
    cancel = threading.Event()
    openai.answers = [(500, {}, {})]
    cancel.set()
    with pytest.raises(Cancelled):
        ask(cancel=cancel)
    assert len(openai.requests) == 1 and openai.waits == []
    real = threading.Event()
    openai.answers = [(500, {}, {}), (200, {}, OK)]
    openai.waits.clear()

    def wait(seconds, cancel_):                    # Stop pressed while waiting
        real.set()
        cancel_.set()
        raise Cancelled()
    aierrors_wait = aierrors.wait
    try:
        aierrors.wait = wait
        with pytest.raises(Cancelled):
            ask(cancel=threading.Event())
    finally:
        aierrors.wait = aierrors_wait
    assert real.is_set() and len(openai.requests) == 2


def test_the_real_wait_ends_at_once_on_stop():
    cancel = threading.Event()
    threading.Timer(0.05, cancel.set).start()
    with pytest.raises(Cancelled):
        aierrors.wait(30, cancel)


def test_a_stream_that_fails_on_openais_side_before_any_text_is_asked_again(openai):
    failed = [{"type": "response.failed", "response": {"error": {"code": "server_error", "message": "oops"}}}]
    openai.answers = [(200, {}, failed), (200, {}, OK)]
    assert ask()["text"] == "Hi" and len(openai.requests) == 2
    half = [{"type": "response.output_text.delta", "delta": "Hal"}, *failed]
    openai.answers = [(200, {}, half)]                     # text already shown: not started over
    with pytest.raises(RuntimeError, match="oops"):
        ask()


def test_retry_after_dates_and_delays():
    assert aierrors.retry_after({"Retry-After": "7"}) == 7.0
    assert aierrors.retry_after({"retry-after": "Wed, 21 Oct 2015 07:28:00 GMT"}) == 0.0     # in the past
    assert aierrors.retry_after({}) is None and aierrors.retry_after({"Retry-After": "soon"}) is None
    assert aierrors.delay(0, None) == aierrors.DELAYS[0] and aierrors.delay(0, 60) == 60
    assert aierrors.delay(0, 3600) is None and aierrors.delay(len(aierrors.DELAYS), None) is None


@pytest.mark.parametrize("ai_name, message, status", [
    ("Claude", 'API Error: 500 {"type":"error","error":{"type":"api_error","message":"Internal server error"}}', 500),
    ("Claude", 'API Error: 529 {"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}', 529),
    ("Claude", "Repeated 529 Overloaded errors", 529),
    ("ChatGPT", "unexpected status 500 Internal Server Error: {\"error\":{\"type\":\"server_error\"}}", 500),
    ("ChatGPT", "exceeded retry limit, last status: 503 Service Unavailable", 503),
    ("ChatGPT", "We're currently experiencing high demand, which may cause temporary errors.", 0),
])
def test_cli_failures_on_the_services_side_are_explained(ai_name, message, status):
    exc = ai._claude_failure(message) if ai_name == "Claude" else ai._codex_failure(message)
    assert isinstance(exc, aierrors.ServiceError) and exc.status == status
    assert str(exc).startswith(f"{ai_name} ") and "Wait a few minutes" in str(exc) and "API Error" not in str(exc)


def test_other_cli_errors_stay_as_they_were():
    assert not isinstance(ai._claude_failure("You've hit your session limit · resets 10:40am"), aierrors.ServiceError)
    assert "usage limit" in str(ai._claude_failure("You've hit your session limit · resets 10:40am"))
    assert str(ai._claude_failure("something odd")) == "Claude: something odd"
    assert "usage limit for Codex" in str(ai._codex_failure("You've hit your usage limit. Try again later."))
    assert str(ai._codex_failure("model not found")) == "ChatGPT: model not found"


def test_a_plan_that_fails_on_the_services_side_says_resume_keeps_the_steps(env):
    def ask_(system, prompt, model="sonnet", cancel=None, **kw):
        if prompt.startswith("TASK: List what the student must learn"):
            raise aierrors.ServiceError(aierrors.explain("ChatGPT", 500, 4), 500, 4)
        return fake_ask([])(system, prompt, model)

    runner = plans.PlanRunner(env.svc, ask=ask_)
    pid = runner.create("c", {"weeks": [1]})
    runner.start(pid)
    p = wait_done(env.lib, pid)
    assert p["status"] == "error" and p["stage"] == "concepts"
    assert "had a problem on its side" in p["error"]          # the page's notice adds that Resume keeps the steps
    assert "gather" in env.lib.plan(pid)["data"]                       # the step done is kept


# ----------------------------------------------------------------------------- "(sonnet)" under ChatGPT
def _chatgpt(monkeypatch, tmp_path, slug=""):
    monkeypatch.setattr(ai, "provider", lambda: "chatgpt")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "no-codex"))
    appconfig.update({"codex_model": slug})
    levels = ["low", "medium", "high", "xhigh", "max"]
    chatgpt._models_path().write_text(json.dumps({"at": 0, "models": [
        {"slug": "gpt-6.1-sol", "display_name": "GPT-6.1-Sol", "levels": levels},
        {"slug": "gpt-6-luna", "display_name": "GPT-6-Luna", "levels": levels}]}), encoding="utf-8")


def test_the_progress_names_the_chatgpt_model_and_level(monkeypatch, tmp_path):
    _chatgpt(monkeypatch, tmp_path)
    assert plans.model_shown("sonnet", "xhigh") == "GPT-6.1-Sol · Extra high"
    appconfig.update({"codex_model": "gpt-6-luna"})
    assert plans.model_shown("haiku", "low") == "GPT-6-Luna · Low"
    monkeypatch.setattr(ai, "provider", lambda: "codex")
    monkeypatch.setattr(ai, "_codex_model", lambda: "gpt-6-luna")
    assert plans.model_shown("sonnet", "medium") == "gpt-6-luna · Medium"
    monkeypatch.setattr(ai, "provider", lambda: "claude")
    assert plans.model_shown("sonnet", "low") == "Sonnet · Low" and plans.model_shown("opus") == "Opus"
    monkeypatch.setattr(ai, "provider", lambda: "local")
    assert plans.model_shown("sonnet", "off") == "Offline model"


def test_a_chatgpt_plan_never_shows_a_claude_model(env, monkeypatch, tmp_path):
    _chatgpt(monkeypatch, tmp_path)
    shown = []
    real_stage = plans.PlanRunner._stage

    def stage(self, plan_id, name, progress, message=""):
        shown.append(message)
        real_stage(self, plan_id, name, progress, message)
    monkeypatch.setattr(plans.PlanRunner, "_stage", stage)
    runner = plans.PlanRunner(env.svc, ask=fake_ask([]))
    pid = runner.create("c", {"weeks": [1], "model": "sonnet", "effort": "xhigh"})   # what the page sends
    assert env.lib.plan(pid)["scope"]["model"] == ""                  # ChatGPT has no Claude model
    runner.start(pid)
    assert wait_done(env.lib, pid)["status"] == "done"
    text = " ".join(shown).lower()
    assert "(gpt-6.1-sol · extra high)" in text and "sonnet" not in text and "haiku" not in text


def test_a_new_version_of_an_old_chatgpt_plan_keeps_its_level(env, monkeypatch, tmp_path):
    _chatgpt(monkeypatch, tmp_path)
    runner = plans.PlanRunner(env.svc, ask=fake_ask([]))
    old = runner.create("c", {"weeks": [1]})
    new = runner.create("c", {"weeks": [1], "model": "opus"}, parent_id=old)      # saved before reasoning levels
    assert env.lib.plan(new)["scope"]["effort"] == "high" and env.lib.plan(new)["scope"]["model"] == ""
    monkeypatch.setattr(ai, "provider", lambda: "claude")
    claude = runner.create("c", {"weeks": [1], "model": "opus"})
    assert env.lib.plan(claude)["scope"]["model"] == "opus"           # Claude keeps its model
