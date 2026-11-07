"""Signing in to Claude Code or the Codex CLI: their own sign-in runs without a window and IEUStudia watches it. The
link it prints is offered again (open, copy), Anthropic's code is passed on, Codex's device code is shown, Cancel and a
time limit end it, and when it ends without signing in the student reads why. The case behind it: the sign-in ran in a
console window nobody watched, so closing it (or the CLI failing at once) left the list waiting ten minutes with no
button. Stand-in CLIs (small Python scripts), no network, no browser."""

import logging
import sys
import time

import pytest

from studia import ai, osutil

FAKE = r"""
import sys, time
mode, flag = sys.argv[1], sys.argv[2]
def say(s, end="\n", err=False):
    f = sys.stderr if err else sys.stdout
    f.write(s + end); f.flush()
if mode == "claude":                 # as Claude Code 2.1.287 prints it (the link as a terminal hyperlink)
    url = "https://claude.ai/oauth/authorize?code=true&client_id=abc&state=xyz"
    say("Opening browser to sign in\u2026")
    say("If the browser didn't open, visit: \x1b]8;;" + url + "\x07" + url + "\x1b]8;;\x07")
    say("Paste code here if prompted > ", end="")
    line = sys.stdin.readline()
    if "#" in line:
        open(flag, "w").write("yes")
        say("\nLogin successful.")
        sys.exit(0)
    sys.exit(3)
if mode == "claude-fails":
    say("Opening browser to sign in\u2026")
    say("Login failed: Couldn't save your login. Try logging in again.", err=True)
    sys.exit(1)
if mode == "codex-port":
    say("Error logging in: Port 127.0.0.1:1455 is already in use", err=True)
    sys.exit(1)
if mode == "codex-split":            # the link arrives in two pieces
    say("Starting local login server on http://localhost:1455.\nIf your browser did not open, navigate to this URL to "
        "authenticate:\n\nhttps://auth.openai.com/oauth/auth", end="", err=True)
    time.sleep(0.6)
    say("orize?client_id=app_x&state=s1\n", err=True)
    time.sleep(60)
if mode == "keeps-output":            # starts a program (a browser, say) that keeps the output open after it ended
    import subprocess
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], stdout=sys.stdout, stderr=sys.stdout)
    open(flag + ".child", "w").write(str(child.pid))
    say("Opening browser to sign in…")
    time.sleep(60)
if mode == "codex-device":            # as Codex 0.156.1 prints it (checked with a scratch CODEX_HOME)
    say("\nWelcome to Codex [v\x1b[90m0.156.1\x1b[0m]\n\x1b[90mOpenAI's command-line coding agent\x1b[0m\n")
    say("Follow these steps to sign in with ChatGPT using device code authorization:\n")
    say("1. Open this link in your browser and sign in to your account")
    say("   \x1b[94mhttps://auth.openai.com/codex/device\x1b[0m\n")
    say("2. Enter this one-time code \x1b[90m(expires in 15 minutes)\x1b[0m")
    say("   \x1b[94mABCD-12345\x1b[0m\n")
    say("\x1b[90mContinue only if you started this login in Codex. If a website or another person gave you this code, "
        "cancel.\x1b[0m\n")
    time.sleep(60)
time.sleep(60)                        # "silent": waits for a browser that never comes back
"""


@pytest.fixture
def cli(tmp_path, monkeypatch):
    """start(which, mode): the sign-in started with a stand-in CLI; the events it sent are in .events."""
    script = tmp_path / "fake_cli.py"
    script.write_text(FAKE, encoding="utf-8")
    flag = tmp_path / "signed-in"
    mode = {"m": "silent"}
    monkeypatch.setattr(ai, "_login_command", lambda which, device=False: (
        [sys.executable, str(script), mode["m"], str(flag)], None))
    monkeypatch.setattr(ai, "claude_auth", lambda: {"logged_in": flag.exists(), "plan": "", "email": ""})
    monkeypatch.setattr(ai, "codex_auth", lambda: {"logged_in": flag.exists(), "plan": "", "email": ""})
    events = []

    class Cli:
        def start(self, which, m, device=False):
            mode["m"] = m
            return ai.start_login(which, events.append, device=device)

    c = Cli()
    c.events, c.flag = events, flag
    yield c
    ai.end_logins()
    ai._logins.clear()


def _wait(which, check, timeout=15):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        s = ai.login_state(which)
        if check(s):
            return s
        time.sleep(0.03)
    raise AssertionError(f"not reached: {ai.login_state(which)}")


def test_the_link_is_offered_and_a_code_from_the_page_signs_in(cli, caplog):
    caplog.set_level(logging.INFO, logger="studia")
    s = cli.start("claude", "claude")
    assert s["state"] == "waiting" and s["started"]
    s = _wait("claude", lambda s: s["page"] and s["asks_code"])
    assert s["page"] == "https://claude.ai/oauth/authorize?code=true&client_id=abc&state=xyz"     # no terminal codes
    assert any(e["state"] == "waiting" and e["page"] == s["page"] for e in cli.events)
    with pytest.raises(RuntimeError, match="isn't the whole code"):
        ai.login_code("claude", "abc")                         # half of it: said before it reaches Claude Code
    ai.login_code("claude", " abc#def \n")
    s = _wait("claude", lambda s: s["state"] != "waiting")
    assert s["state"] == "done" and s["error"] == "" and cli.events[-1]["state"] == "done"
    assert cli.events[-1]["type"] == "ai_cli_login" and cli.events[-1]["which"] == "claude"
    assert "Sign-in to claude ended with code 0" in caplog.text
    with pytest.raises(RuntimeError, match="isn't waiting any more"):
        ai.login_code("claude", "abc#def")


def test_a_sign_in_that_fails_at_once_says_why_and_logs_it(cli, caplog):
    caplog.set_level(logging.INFO, logger="studia")
    cli.start("claude", "claude-fails")
    s = _wait("claude", lambda s: s["state"] != "waiting")
    assert s["state"] == "error" and s["error"] == "Claude Code could not save your sign-in on this PC. Click the button to try again."
    assert cli.events[-1]["state"] == "error"
    assert "Login failed: Couldn't save your login" in caplog.text           # its own words, for the owner


def test_a_sign_in_closed_early_is_noticed_at_once(cli):
    cli.start("claude", "silent")
    ai._logins["claude"]["proc"].kill()                       # the program ended (closed, crashed) without a word
    s = _wait("claude", lambda s: s["state"] != "waiting")
    assert s["state"] == "error" and s["error"].startswith("The Claude Code sign-in ended before you were signed in")


def test_cancel_and_a_new_click_end_the_one_waiting(cli):
    cli.start("codex", "silent")
    first = ai._logins["codex"]["proc"]
    cli.start("codex", "silent")                              # clicked again: the first one is ended, not stacked
    first.wait(timeout=15)
    assert ai.login_state("codex")["state"] == "waiting" and ai._logins["codex"]["proc"] is not first
    time.sleep(0.3)
    assert [e["state"] for e in cli.events] == []             # the replaced one's end isn't announced
    assert ai.cancel_login("codex")
    s = _wait("codex", lambda s: s["state"] != "waiting")
    assert s["state"] == "" and s["error"] == "" and cli.events[-1]["state"] == ""
    assert not ai.cancel_login("codex")                       # nothing left to stop


def test_cancel_leaves_what_the_sign_in_started_alone_and_still_ends(cli):
    """Cancel ends the sign-in program only: a browser it started (a child of codex.exe when none was running) stays
    open. Such a program can keep the output open, and the list still hears the end."""
    import os
    import signal

    cli.start("codex", "keeps-output")
    marker = cli.flag.with_name(cli.flag.name + ".child")
    _wait("codex", lambda s: marker.exists())
    time.sleep(0.3)
    child = int(marker.read_text())
    try:
        assert ai.cancel_login("codex")
        t0 = time.monotonic()
        s = _wait("codex", lambda s: s["state"] != "waiting", timeout=10)
        assert s["state"] == "" and time.monotonic() - t0 < 6
        assert cli.events[-1]["state"] == ""
    finally:
        try:
            os.kill(child, signal.SIGTERM)                  # the stand-in "browser" was left running, as it should be
        except OSError:
            pass


def test_a_sign_in_left_open_ends_at_its_time_limit(cli, monkeypatch):
    monkeypatch.setattr(ai, "LOGIN_LIMIT_S", 1)
    cli.start("claude", "silent")
    s = _wait("claude", lambda s: s["state"] != "waiting")
    assert s["state"] == "error" and "waited 15 minutes" in s["error"]


def test_codex_port_in_use_is_said_with_the_way_round_it(cli):
    cli.start("codex", "codex-port")
    s = _wait("codex", lambda s: s["state"] != "waiting")
    assert s["state"] == "error" and s["error"].startswith("Another program is using the port the Codex CLI signs in")
    assert "sign in with a code instead" in s["error"]


def test_a_link_printed_in_two_pieces_is_read_whole(cli):
    cli.start("codex", "codex-split")
    s = _wait("codex", lambda s: s["page"])
    assert s["page"] == "https://auth.openai.com/oauth/authorize?client_id=app_x&state=s1"


def test_codex_device_sign_in_shows_its_page_and_code(cli):
    cli.start("codex", "codex-device", device=True)
    s = _wait("codex", lambda s: s["code"])
    assert s["device"] and s["page"] == "https://auth.openai.com/codex/device" and s["code"] == "ABCD-12345"


def test_the_page_can_be_opened_again_by_ieustudia(cli, monkeypatch):
    opened = []
    monkeypatch.setattr(osutil.impl(), "open_in_browser", lambda url: opened.append(url) or True)
    assert ai.reopen_login("claude")["launched"] is False and opened == []        # nothing waiting yet
    cli.start("claude", "claude")
    s = _wait("claude", lambda s: s["page"])
    r = ai.reopen_login("claude")
    assert r["launched"] and opened == [s["page"]]


def test_failures_that_cannot_be_read_still_say_what_to_do():
    f = ai._login_failure
    assert f("claude", 1, ["Opening browser to sign in…", "OAuth login failed: unable to connect to api.anthropic.com"], 3) \
        .startswith("Claude Code could not reach Anthropic")
    assert f("codex", 1, ["Error loading configuration: invalid type: string, expected a boolean"], 1).startswith(
        "The Codex CLI could not read its settings file (config.toml in the .codex folder of your user folder): "
        "Error loading configuration: invalid type: string, expected a boolean. Fix")
    assert f("codex", 1, ["WARNING: proceeding, even though we could not create PATH aliases", "Something odd"], 2) == \
        "The Codex CLI sign-in ended before you were signed in (code 1). Click the button to try again."
    assert f("claude", 0, [], 40).startswith("Claude Code said the sign-in worked, but it still reports you as signed out")
    # its network library's own words ("timeout of 15000ms exceeded") were shown as they are, with nothing to do
    assert f("claude", 1, ["Login failed: timeout of 15000ms exceeded"], 16).startswith(
        "Claude Code could not reach Anthropic: check your internet connection")


def test_a_cli_that_does_not_start_is_said_plainly(monkeypatch):
    monkeypatch.setattr(ai, "_login_command", lambda which, device=False: (["C:/nowhere/claude.exe", "auth"], None))
    with pytest.raises(RuntimeError, match=r"^Claude Code could not be started: .* Install it again under Settings → AI\.$"):
        ai.start_login("claude")


def test_the_page_starts_and_stops_it_through_the_api(cli, monkeypatch):
    from studia import api as api_mod
    from studia.services import Services

    api = api_mod.Api(Services())
    mode = {"m": "silent"}
    script_cmd = ai._login_command("claude")[0]
    monkeypatch.setattr(ai, "_login_command", lambda which, device=False: (
        script_cmd[:2] + [mode["m"] if not device else "codex-device", script_cmd[3]], None))
    r = api.ai_login("codex", False, True)
    assert r["ok"] and r["state"] == "waiting" and r["device"]
    assert api.ai_cli_cancel("codex", "login") == {"ok": True, "stopped": True}
    _wait("codex", lambda s: s["state"] == "")
    r = api.ai_login("claude")
    assert r["ok"] and r["state"] == "waiting" and not r["device"]          # (device is Codex's only)
    assert api.ai_cli_code("claude", "no-hash")["ok"] is False
    assert api.ai_cli_cancel("claude") == {"ok": True, "stopped": True}
