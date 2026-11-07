"""The Linux helpers (linuxutil, linux_secrets, the Linux branch of ai) with the desktop replaced by fakes: the
keyring, notify-send, systemd-inhibit and the terminal. They run on every system, so Windows checks them too."""

import base64
import json
import os
import subprocess
import time
import types

import pytest

from studia import linux_secrets, linuxutil


# ----------------------------------------------------------------------------- the keyring's key
class FakeItem:
    def __init__(self, secret):
        self.secret = secret

    def get_secret(self):
        return self.secret


class FakeCollection:
    """A Secret Service collection that keeps text secrets as text, as KeePassXC may."""

    def __init__(self, *secrets):
        self.items = [FakeItem(s) for s in secrets]
        self.created = []

    def search_items(self, attrs):
        assert attrs == linux_secrets._ATTRS
        return iter(self.items)

    def create_item(self, label, attrs, secret, replace=False, content_type="text/plain"):
        self.created.append((label, attrs, secret, replace, content_type))
        self.items.append(FakeItem(secret.decode("ascii").encode("utf-8")))     # back as text


def test_the_key_is_stored_as_text_and_read_back_the_same():
    col = FakeCollection()
    key = linux_secrets._collection_key(col)
    assert len(key) == 32
    (label, attrs, secret, replace, content_type), = col.created
    assert secret == base64.b64encode(key) and content_type == "text/plain" and not replace
    assert linux_secrets._collection_key(col) == key and len(col.created) == 1          # the next start: same key


def test_a_key_stored_as_raw_bytes_still_opens_what_it_encrypted():
    raw = bytes(range(200, 232))
    col = FakeCollection(raw)
    assert linux_secrets._collection_key(col) == raw and not col.created


@pytest.mark.parametrize("secret", [b"\xef\xbf\xbd" * 20, b"not base64!", base64.b64encode(b"short")])
def test_an_unusable_key_is_never_replaced(secret):
    col = FakeCollection(secret)
    with pytest.raises(OSError, match="can't use"):
        linux_secrets._collection_key(col)
    assert not col.created                                  # what it encrypted may still be opened once it's fixed


def test_a_usable_key_next_to_an_unusable_one_is_used():
    key = os.urandom(32)
    col = FakeCollection(b"garbage", base64.b64encode(key))
    assert linux_secrets._collection_key(col) == key and not col.created


# ----------------------------------------------------------------------------- the instance and open-request files
@pytest.fixture
def run_dir(tmp_path, monkeypatch):
    run = tmp_path / "run-user"
    run.mkdir()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(run))
    monkeypatch.setattr(linuxutil, "_alive", lambda pid: True)         # (os.kill(pid, 0) ends a process on Windows)
    raised = []
    monkeypatch.setattr(linuxutil, "bring_to_front", lambda hwnd: raised.append(hwnd))
    return types.SimpleNamespace(path=run, raised=raised)


def test_the_instance_files_are_in_the_users_own_folder(run_dir, monkeypatch, isolated_home):
    import tempfile

    name = linuxutil.instance_name()
    linuxutil.write_instance(name)
    linuxutil.mark_instance(name, "shown")
    info = linuxutil.read_instance(name)
    assert info["pid"] == os.getpid() and info["phase"] == "shown"
    linuxutil.request_open(name, "home")
    short = name.split("\\")[-1]
    assert {p.name for p in run_dir.path.iterdir()} == {short + ".json", short + "-open.json"}
    assert linuxutil._lock_path(name).parent == run_dir.path
    assert not list(os.scandir(tempfile.gettempdir()))                 # nothing in the shared temp folder
    monkeypatch.delenv("XDG_RUNTIME_DIR")                             # none: a folder in the app's data folder
    assert linuxutil._instance_file(name).parent == isolated_home / "run"
    assert linuxutil._lock_path(name).parent == isolated_home / "run"
    assert linuxutil.take_open_request(name) == "" and not isolated_home.exists()   # a reader makes no folders
    linuxutil.write_instance(name)                                    # the first file written makes it
    assert (isolated_home / "run" / (short + ".json")).is_file()


def test_a_second_start_only_brings_the_window_forward(run_dir):
    name = linuxutil.instance_name()
    linuxutil.write_instance(name)
    assert linuxutil.focus_window("IEUStudia")
    assert linuxutil.take_open_request(name) == "" and run_dir.raised == [1]     # forward, on the page it shows
    assert linuxutil.take_open_request(name) == "" and run_dir.raised == [1]


def test_a_second_start_keeps_the_page_a_notification_asked_for(run_dir):
    name = linuxutil.instance_name()
    linuxutil.write_instance(name)
    linuxutil.request_open(name, "course:a:lectures")                 # a click, not shown yet
    assert linuxutil.focus_window("IEUStudia")
    assert linuxutil.take_open_request(name) == "course:a:lectures" and run_dir.raised == [1]
    assert linuxutil.focus_window("IEUStudia")                         # and a click after a plain start wins
    linuxutil.request_open(name, "course:b:announcements")
    assert linuxutil.take_open_request(name) == "course:b:announcements"


def test_a_request_left_over_is_replaced_and_not_answered(run_dir):
    name = linuxutil.instance_name()
    linuxutil.write_instance(name)
    f = linuxutil._open_file(name)
    old = time.time() - 600
    f.write_text(json.dumps({"target": "course:a:lectures", "at": old}), encoding="utf-8")
    os.utime(f, (old, old))
    assert linuxutil.focus_window("IEUStudia")                         # the window never took it (it hung)
    assert linuxutil.take_open_request(name) == "" and run_dir.raised == [1]
    f.write_text(json.dumps({"target": "home", "at": old}), encoding="utf-8")
    assert linuxutil.take_open_request(name) == "" and run_dir.raised == [1]   # too old: nothing happens
    assert not f.exists()


def test_no_running_copy_means_nothing_to_focus(run_dir, monkeypatch):
    monkeypatch.setattr(linuxutil, "_alive", lambda pid: False)
    linuxutil.write_instance(linuxutil.instance_name())
    assert not linuxutil.focus_window("IEUStudia")
    assert not linuxutil._open_file(linuxutil.instance_name()).exists()


# ----------------------------------------------------------------------------- keeping the PC awake
class FakeProc:
    def __init__(self, cmd, **kw):
        self.cmd, self.ended = cmd, False

    def poll(self):
        return 0 if self.ended else None

    def terminate(self):
        self.ended = True

    def wait(self, timeout=None):
        return 0


def test_the_awake_lock_ends_with_the_app(monkeypatch):
    procs = []
    monkeypatch.setattr(linuxutil.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(linuxutil.subprocess, "Popen", lambda cmd, **kw: procs.append(FakeProc(cmd, **kw)) or procs[-1])
    awake = linuxutil.KeepAwake("IEUStudia is transcribing lectures")
    awake.on()
    awake.on()                                                         # once
    (proc,) = procs
    assert proc.cmd[0] == "/usr/bin/systemd-inhibit" and "--why=IEUStudia is transcribing lectures" in proc.cmd
    assert proc.cmd[-4:] == ["tail", f"--pid={os.getpid()}", "-f", "/dev/null"]     # not "sleep infinity"
    linuxutil._all_awake_off()                                         # what runs at exit
    assert proc.ended
    awake.off()                                                        # twice is fine


# ----------------------------------------------------------------------------- notifications
def _run_fake(results):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        rc, out, err = results.pop(0) if results else (0, "", "")
        return subprocess.CompletedProcess(cmd, rc, out, err)

    return calls, run


def test_an_old_notify_send_shows_notifications_without_the_open_action(monkeypatch, caplog):
    monkeypatch.setattr(linuxutil, "_no_actions", False)
    calls, run = _run_fake([(1, "", "Unknown option --action=default=Open\n"), (0, "", "")])
    monkeypatch.setattr(linuxutil.subprocess, "run", run)
    clicked = []
    linuxutil._send("/usr/bin/notify-send", "New lecture", "EEE 215", lambda: clicked.append(1))
    assert len(calls) == 2 and any(a.startswith("--action") for a in calls[0])
    assert not any(a.startswith("--action") for a in calls[1]) and calls[1][-2:] == ["New lecture", "EEE 215"]
    linuxutil._send("/usr/bin/notify-send", "Again", "x", lambda: clicked.append(1))
    assert len(calls) == 3 and not any(a.startswith("--action") for a in calls[2])   # remembered
    assert not clicked


def test_a_click_on_open_calls_back_and_a_failure_is_logged(monkeypatch, caplog):
    import logging

    monkeypatch.setattr(linuxutil, "_no_actions", False)
    calls, run = _run_fake([(0, "default\n", ""), (1, "", "Could not connect to the notification server")])
    monkeypatch.setattr(linuxutil.subprocess, "run", run)
    clicked = []
    linuxutil._send("/usr/bin/notify-send", "New lecture", "EEE 215", lambda: clicked.append(1))
    assert clicked == [1]
    with caplog.at_level(logging.DEBUG, logger="studia"):
        linuxutil._send("/usr/bin/notify-send", "New lecture", "EEE 215", lambda: clicked.append(1))
    assert len(calls) == 2 and clicked == [1] and not linuxutil._no_actions          # no retry for other errors
    assert "notify-send exited with 1" in caplog.text and "notification server" in caplog.text


# ----------------------------------------------------------------------------- signing in to Claude Code
def test_the_sign_in_needs_no_terminal_and_keeps_the_apps_claude_profile(monkeypatch):
    """Claude Code's sign-in runs without a window (IEUStudia reads its link and its end), so no terminal app is
    needed; it signs in IEUStudia's own Claude Code profile, not a developer's personal one."""
    from studia import ai, ai_setup

    monkeypatch.setattr(ai_setup, "sys", types.SimpleNamespace(platform="linux"))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(ai, "claude_cli_info", lambda: {"available": True, "path": "/home/ali/.local/bin/claude",
                                                        "version": "2.1.318"})
    cmd, env = ai._login_command("claude")
    assert cmd == ["/home/ali/.local/bin/claude", "auth", "login", "--claudeai"]
    assert env["CLAUDE_CONFIG_DIR"] == str(ai_setup.tools_dir() / "claude-config")
