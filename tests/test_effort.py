"""Reasoning levels (effort.py): what each AI takes, the settings from before them, and what each AI is sent."""

import json
import types

import pytest

from studia import ai, appconfig, chatgpt, effort, local_llm
from studia.config import apply_patch, load_settings, save_settings
from studia.paths import settings_path

OLD = {"ai_model": "opus", "plan_model": "", "verify_model": "haiku", "ai_model_light": "sonnet"}


@pytest.fixture
def codex_home(tmp_path, monkeypatch):
    """The Codex CLI's model list (models_cache.json), in the shape the CLI keeps it."""
    home = tmp_path / "codex"
    home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))

    def write(*models):
        rows = [{"slug": slug, "visibility": vis, "supported_reasoning_levels": [{"effort": e} for e in levels]}
                for slug, levels, vis in models]
        (home / "models_cache.json").write_text(json.dumps({"models": rows}), encoding="utf-8")
    return write


def test_settings_from_before_keep_what_every_use_got(tmp_path):
    settings_path().parent.mkdir(parents=True, exist_ok=True)
    settings_path().write_text(json.dumps(OLD), encoding="utf-8")
    s = load_settings()
    # ChatGPT: Thorough chat, "Same as chat" for plans, Quick checking, Standard digest
    for p in ("chatgpt", "codex"):
        assert [s.ai_effort[f"{p}.{u}"] for u in effort.USES] == ["high", "high", "low", "medium"]
    assert all(s.ai_effort[f"claude.{u}"] == "low" for u in effort.USES)          # Claude Code always ran at low
    assert all(s.ai_effort[f"local.{u}"] == "off" for u in effort.USES)
    assert s.ai_model == "opus" and s.verify_model == "haiku"                    # Claude's model choice stays


def test_a_new_student_gets_the_old_defaults_and_choices_are_kept():
    s = load_settings()
    assert s.ai_effort["chatgpt.chat"] == "medium" and s.ai_effort["chatgpt.verify"] == "low"
    assert s.ai_effort["claude.chat"] == "low" and s.ai_effort["local.chat"] == "off"
    s = apply_patch(s, {"ai_effort": {**s.ai_effort, "claude.chat": "max", "claude.plan": "ultra", "local.chat": "high",
                                      "nobody.chat": "low", "claude.nothing": "low"}})
    save_settings(s)
    s = load_settings()
    assert s.ai_effort["claude.chat"] == "max"
    assert s.ai_effort["claude.plan"] == "low" and s.ai_effort["local.chat"] == "off"     # not theirs: the default
    assert "nobody.chat" not in s.ai_effort and "claude.nothing" not in s.ai_effort
    s = apply_patch(s, {"ai_model": "haiku"})                    # a later model change doesn't touch the levels
    assert s.ai_effort["chatgpt.chat"] == "medium"


def test_each_ai_offers_its_own_levels(codex_home, monkeypatch):
    assert effort.supported("claude") == ("low", "medium", "high", "xhigh", "max")
    assert effort.supported("local") == ("off", "on")
    assert effort.supported("none") == ()
    codex_home(("gpt-6.1-sol", ["low", "medium", "high", "xhigh", "max", "ultra"], "list"),
               ("gpt-6-luna", ["low", "medium", "high"], "list"), ("hidden", ["low"], "hide"))
    appconfig.update({"codex_model": ""})
    assert effort.supported("codex") == ("low", "medium", "high", "xhigh", "max")      # "ultra" is never offered
    appconfig.update({"codex_model": "gpt-6-luna"})
    assert effort.supported("codex") == ("low", "medium", "high")
    # "Continue with ChatGPT": its own list first (with the levels kept from OpenAI's answer), else the CLI's
    chatgpt._models_path().write_text(json.dumps({"at": 0, "models": [
        {"slug": "gpt-6-luna", "display_name": "Luna", "levels": ["minimal", "low", "medium"]}]}), encoding="utf-8")
    assert effort.supported("chatgpt") == ("low", "medium")
    chatgpt._models_path().write_text(json.dumps({"at": 0, "models": [{"slug": "gpt-6-luna"}]}), encoding="utf-8")
    assert effort.supported("chatgpt") == ("low", "medium", "high")
    codex_home()
    chatgpt._models_path().write_text(json.dumps({"at": 0, "models": [{"slug": "new-model"}]}), encoding="utf-8")
    assert effort.supported("chatgpt") == effort.SAFE                          # nothing known: what every model takes


def test_the_level_sent_is_the_request_s_else_the_old_chat_s_else_the_setting(codex_home):
    s = load_settings()
    s = apply_patch(s, {"ai_effort": {**s.ai_effort, "chatgpt.chat": "xhigh", "claude.plan": "high"}})
    codex_home(("gpt-6-luna", ["low", "medium", "high"], "list"))
    appconfig.update({"codex_model": "gpt-6-luna"})
    chatgpt._models_path().write_text(json.dumps({"at": 0, "models": [
        {"slug": "gpt-6-luna", "levels": ["low", "medium", "high", "xhigh"]}]}), encoding="utf-8")
    assert effort.resolve("chat", provider="chatgpt", settings=s) == "xhigh"
    assert effort.resolve("chat", "low", provider="chatgpt", settings=s) == "low"
    assert effort.resolve("chat", legacy="haiku", provider="chatgpt", settings=s) == "low"   # an old "Quick" chat
    assert effort.resolve("chat", "max", provider="chatgpt", settings=s) == "xhigh"          # the nearest it takes
    assert effort.resolve("chat", provider="codex", settings=s) == "medium"
    assert effort.resolve("chat", "xhigh", provider="codex", settings=s) == "high"           # Luna: up to high
    assert effort.resolve("plan", provider="claude", settings=s) == "high"
    assert effort.resolve("plan", legacy="opus", provider="claude", settings=s) == "high"    # Claude: a model, no level
    assert effort.resolve("chat", "on", provider="local", settings=s) == "on"
    assert effort.resolve("chat", "high", provider="local", settings=s) == "off"             # not the offline model's
    assert effort.resolve("chat", "max", provider="none", settings=s) == ""
    assert effort.heavy("high") and effort.heavy("max") and not effort.heavy("medium") and not effort.heavy("on")


def test_chatgpt_gets_reasoning_effort(monkeypatch):
    sent = []
    monkeypatch.setattr(chatgpt, "models", lambda refresh=False: [{"slug": "gpt-6.1-sol"}])
    monkeypatch.setattr(chatgpt, "_stream", lambda body, *a: sent.append(body) or ("ok", {}))
    chatgpt.one_shot("S", [{"type": "text", "text": "q"}], "sonnet", "max")
    chatgpt.one_shot("S", [{"type": "text", "text": "q"}], "opus")             # no level: the old model level's
    assert [b["reasoning"]["effort"] for b in sent] == ["max", "high"]


def test_codex_gets_model_reasoning_effort(tmp_path, monkeypatch):
    monkeypatch.setattr(ai, "codex_cli_info", lambda: {"available": True, "path": "codex", "version": "1"})
    monkeypatch.setattr(ai, "_codex_model", lambda: "")
    assert 'model_reasoning_effort="xhigh"' in ai._codex_command(tmp_path, "sonnet", "xhigh")
    assert 'model_reasoning_effort="low"' in ai._codex_command(tmp_path, "haiku", "")


def test_claude_code_gets_effort(tmp_path, monkeypatch):
    monkeypatch.setattr(ai, "_require_claude", lambda: "claude")
    cmd = ai._command("opus", "max", tmp_path / "s.txt")
    assert cmd[cmd.index("--effort") + 1] == "max" and cmd[cmd.index("--model") + 1] == "opus"
    cmd = ai._command("sonnet", "", tmp_path / "s.txt")
    assert cmd[cmd.index("--effort") + 1] == "low"                              # as it always ran


def test_the_offline_model_thinks_only_when_asked(monkeypatch):
    appconfig.update({"ai_provider": "local", "local_model": "qwen3-1.7b"})
    asked = []
    monkeypatch.setattr(local_llm, "_load", lambda key: {"device": "cuda", "room": 6000, "answer": 1200})
    monkeypatch.setattr(local_llm, "_loaded", {"qwen3-1.7b": {"device": "cuda", "room": 6000, "answer": 1200}})
    monkeypatch.setattr(local_llm, "count_tokens", lambda text, key: len(text.split()))
    monkeypatch.setattr(local_llm, "generate", lambda msgs, key, cap, on_text=None, cancel=None, think=0: (
        asked.append(think) or {"text": "ok", "usage": {"input_tokens": 1, "output_tokens": 1}}))
    ai.one_shot("Notes", [{"type": "text", "text": "a lecture"}])
    ai.one_shot("Notes", [{"type": "text", "text": "a lecture"}], effort="on")
    assert asked == [0, local_llm.THINK_TOKENS]
    s = ai.new_session("Instructions\n\n=== LECTURES ===\n\n# L1\nText", effort="on")
    assert s.think
    s.ask([{"type": "text", "text": "q"}])
    assert asked[-1] == local_llm.THINK_TOKENS


def test_thinking_comes_first_and_stays_out_of_the_answer(monkeypatch):
    class Tok:
        def encode(self, text):
            return types.SimpleNamespace(tokens=list(text))

        def decode(self, ids):
            return "".join(chr(i) for i in ids)

    prefixes = []

    class Gen:
        def generate_tokens(self, prefix, max_length, end_token, **kw):
            prefixes.append(("".join(prefix), max_length, end_token))
            words = "Let me think.</think>" if "".join(prefix).endswith("<think>\n") else "The answer."
            for ch in words[:max_length]:
                yield types.SimpleNamespace(token_id=ord(ch))

    model = {"tok": Tok(), "gen": Gen(), "device": "cuda", "used": 0}
    monkeypatch.setattr(local_llm, "_load", lambda key: model)
    shown = []
    r = local_llm.generate([{"role": "user", "content": "Q"}], "qwen3-1.7b", 50, shown.append, None, think=30)
    assert r["text"] == "The answer." and "think" not in shown[-1]
    assert prefixes[0][1] == 30 and "</think>" in prefixes[0][2]
    assert prefixes[1][0].endswith("<think>\nLet me think.\n</think>\n\n") and prefixes[1][1] == 50
    prefixes.clear()
    local_llm.generate([{"role": "user", "content": "Q"}], "qwen3-1.7b", 50)
    assert len(prefixes) == 1 and prefixes[0][0].endswith("<think>\n\n</think>\n\n")       # thinking off, as before


def test_study_notes_send_the_chosen_level(monkeypatch):
    from studia import services

    appconfig.update({"ai_provider": "claude"})
    got = {}
    monkeypatch.setattr(ai, "run_claude", lambda prompt, text, images, **k: got.update(k) or {"text": "x", "usage": {}})
    svc = types.SimpleNamespace(ai_inputs=lambda day, slides, screens="full": ("text", [], None, []))
    with pytest.raises(Exception):     # no recordings to name the file after: only what was sent matters here
        services.Services.ai_notes(svc, "c|2026-10-13|lecture", "notes", False, "sonnet", effort="high")
    assert got["effort"] == "high" and got["model"] == "sonnet"



def test_the_weekly_digest_runs_at_the_level_asked_for_it(tmp_path, monkeypatch, codex_home):
    import threading
    import time

    from studia import digest
    from studia.api import Api
    from studia.config import Settings

    got = []
    monkeypatch.setattr(ai, "provider", lambda: "chatgpt")
    monkeypatch.setattr(chatgpt, "_models_path", lambda: tmp_path / "none.json")      # low, medium and high
    monkeypatch.setattr(ai, "claude_info", lambda: {"available": True})
    monkeypatch.setattr(ai, "one_shot", lambda sp, content, model, level, *a, **k: got.append(level) or {"text": "Sum."})
    monkeypatch.setattr(digest, "weekly_digest", lambda lib, settings, offset: {"week": {"start": "2026-10-19"}})
    monkeypatch.setattr(digest, "digest_prompt", lambda d: "facts")
    monkeypatch.setattr(digest, "digest_markdown", lambda d, text: text)
    a = Api.__new__(Api)
    pushed = []
    a._push, a._ai_cancel, a._ready = pushed.append, None, threading.Event()
    a._ready.set()
    a._services = types.SimpleNamespace(settings=Settings(ai_effort={"chatgpt.digest": "low"}), output_root=tmp_path,
                                        lib=types.SimpleNamespace(set_meta=lambda *x: None))
    for level in ("high", ""):
        assert a.digest_summary(0, level)["ok"]
        end = time.monotonic() + 10
        while a._ai_cancel is not None and time.monotonic() < end:
            time.sleep(0.02)
    assert got == ["high", "low"]                     # this request's level, else the one in Settings
