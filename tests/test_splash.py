"""The start-up window's stuck-window detection: the app marks when it starts creating its window; the splash
restarts it when the window is still missing well after that, and saves diagnostics first."""

import json
import os
import sys
import time
import types

import pytest

if sys.platform != "win32":
    pytest.skip("the start-up window is plain Win32", allow_module_level=True)

from studia import splash, winutil  # noqa: E402


def test_window_stall_is_judged_from_the_apps_marker(tmp_path):
    marker = tmp_path / "instance.json"
    s = splash.Splash(1234, "light", False, "pythonw.exe", str(marker), str(tmp_path))
    assert not s.window_stalled()                                          # no marker yet

    def mark(**d):
        marker.write_text(json.dumps(d), encoding="utf-8")

    mark(pid=1234, phase="window", phase_at=time.time() - 5)
    assert not s.window_stalled()                                          # creating it, but not for long
    mark(pid=1234, phase="window", phase_at=time.time() - splash.WINDOW_STALL_S - 1)
    assert s.window_stalled()
    mark(pid=999, phase="window", phase_at=time.time() - 60)
    assert not s.window_stalled()                                          # another (older) copy's marker
    mark(pid=1234, phase="shown", phase_at=time.time() - 60)
    assert not s.window_stalled()
    marker.write_text("{half a fi", encoding="utf-8")
    assert not s.window_stalled()
    assert not splash.Splash(1234, "light", False).window_stalled()         # started without a marker


class _FakeUser32:
    """Records window calls instead of making them (the test creates no window)."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda *a: self.calls.append(name) or 1


def _stuck_splash(tmp_path, monkeypatch, recovered):
    marker = tmp_path / "instance.json"
    marker.write_text(json.dumps({"pid": os.getpid(), "phase": "window", "phase_at": time.time() - 30}), encoding="utf-8")
    calls = []
    fake = _FakeUser32()
    monkeypatch.setattr(splash, "user32", fake)
    monkeypatch.setattr(splash, "main_window_state", lambda pid: "")
    monkeypatch.setattr(splash, "_kill_tree", lambda pid: calls.append(("kill", pid)))
    monkeypatch.setattr(splash.subprocess, "Popen", lambda cmd, **kw: calls.append(cmd))
    s = splash.Splash(os.getpid(), "light", recovered, "app-pythonw.exe", str(marker), str(tmp_path / "logs"))
    s.fonts = {"button": 0}
    for _ in range(12):                                   # the app is checked every 6th tick
        s._tick()
    return s, calls, fake


def test_a_stuck_window_is_restarted_at_once(tmp_path, monkeypatch):
    s, calls, fake = _stuck_splash(tmp_path, monkeypatch, recovered=False)
    assert ("kill", os.getpid()) in calls
    assert ["app-pythonw.exe", "-m", "studia.app", "--restarted", "--recovered"] in calls
    assert calls.count(["app-pythonw.exe", "-m", "studia.app", "--restarted", "--recovered"]) == 1
    assert s.restarting and "DestroyWindow" in fake.calls
    assert (tmp_path / "logs" / "startup-stall.txt").exists() and (tmp_path / "logs" / "startup-stall.dmp").exists()


def test_a_visible_but_busy_window_is_not_restarted_early(tmp_path, monkeypatch):
    monkeypatch.setattr(splash, "main_window_state", lambda pid: "hung")
    marker = tmp_path / "instance.json"
    marker.write_text(json.dumps({"pid": os.getpid(), "phase": "window", "phase_at": time.time() - 30}), encoding="utf-8")
    monkeypatch.setattr(splash, "user32", _FakeUser32())
    calls = []
    monkeypatch.setattr(splash, "_kill_tree", lambda pid: calls.append(("kill", pid)))
    s = splash.Splash(os.getpid(), "light", False, "app-pythonw.exe", str(marker), str(tmp_path / "logs"))
    for _ in range(12):
        s._tick()
    assert not calls and not s.restarting and not s.stall_handled   # the slower timers handle a hung window


def test_stuck_again_after_a_restart_asks(tmp_path, monkeypatch):
    s, calls, fake = _stuck_splash(tmp_path, monkeypatch, recovered=True)
    assert not calls and not s.restarting                 # no second automatic restart
    assert len(s.buttons) == 2 and s.status == "IEUStudia is stuck again"


@pytest.mark.parametrize("phase,said", [("window", True), ("", True), ("crashed", False), ("shown", False)])
def test_an_app_that_ends_before_its_window_without_a_word_is_said(tmp_path, monkeypatch, phase, said):
    """An antivirus ending IEUStudia as it starts: the start-up window used to just disappear, and nothing came.
    An error the app shows itself marks "crashed" first, and then only its own message speaks."""
    marker = tmp_path / "instance.json"
    marker.write_text(json.dumps({"pid": 4321, "phase": phase, "phase_at": time.time()}), encoding="utf-8")
    fake = _FakeUser32()
    monkeypatch.setattr(splash, "user32", fake)
    monkeypatch.setattr(splash, "_alive", lambda pid: False)
    s = splash.Splash(4321, "light", False, "app-pythonw.exe", str(marker), str(tmp_path / "logs"))
    s.fonts = {"button": 0}
    for _ in range(18):
        s._tick()
    if said:
        assert s.vanished and s.status == "IEUStudia closed while it was starting" and "antivirus" in s.detail
        assert len(s.buttons) == 2 and "DestroyWindow" not in fake.calls     # Restart and Close stay
    else:
        assert not s.vanished and "DestroyWindow" in fake.calls


def test_the_app_marks_a_start_that_failed_with_its_own_message(isolated_home, monkeypatch):
    from studia import app, osutil

    monkeypatch.setattr(osutil.impl(), "message_box", lambda text, title="", flags=0: 1)

    def fail(argv):
        raise RuntimeError("something else")

    monkeypatch.setattr(app, "_main", fail)
    app.main([])
    assert winutil.read_instance(winutil.instance_name()).get("phase") == "crashed"


def test_stall_diagnostics_list_modules_and_dump_stacks(tmp_path):
    splash.save_diagnostics(os.getpid(), tmp_path / "logs")
    txt = (tmp_path / "logs" / "startup-stall.txt").read_text(encoding="utf-8").lower()
    assert f"pid {os.getpid()}" in txt and "kernel32.dll" in txt and "python" in txt
    assert (tmp_path / "logs" / "startup-stall.dmp").stat().st_size > 10_000
    splash.save_diagnostics(os.getpid(), None)                             # no folder: nothing, no error


def test_app_marks_window_creation(tmp_path, monkeypatch):
    from studia import app

    monkeypatch.setattr(winutil, "_instance_file", lambda name: tmp_path / f"{name}.json")
    created = []
    lib = types.SimpleNamespace(create_window=lambda window: created.append(window.uid))
    fake = types.SimpleNamespace(initialize=lambda gui=None: lib)
    app._note_window_creation(fake, "inst")
    app._note_window_creation(fake, "inst")                                # idempotent
    fake.initialize("edgechromium").create_window(types.SimpleNamespace(uid="master"))
    d = json.loads((tmp_path / "inst.json").read_text(encoding="utf-8"))
    assert created == ["master"] and d["pid"] == os.getpid() and d["phase"] == "window"
    assert time.time() - d["phase_at"] < 5
    winutil.mark_instance("inst", "shown")
    assert json.loads((tmp_path / "inst.json").read_text(encoding="utf-8"))["phase"] == "shown"
    app._note_window_creation(types.SimpleNamespace(), "inst")             # a webview without initialize: no-op
