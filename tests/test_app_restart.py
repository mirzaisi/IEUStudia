"""Switching profiles from the window: the app closes its window and starts a fresh copy for the other account
(the GUI toolkit is replaced by a fake, so this runs without a window)."""

import subprocess
import sys
import threading
import types

import pytest

from studia import accounts


class _Handlers(list):
    def __iadd__(self, fn):
        self.append(fn)
        return self


class FakeWindow:
    def __init__(self, api):
        self.api = api
        self.events = types.SimpleNamespace(shown=_Handlers(), loaded=_Handlers(), closing=_Handlers())
        self.closed = threading.Event()
        self.closing_result = None

    def destroy(self):
        self.closing_result = [fn() for fn in self.events.closing]
        self.closed.set()


def test_switching_profiles_restarts_into_the_other_account(isolated_home, monkeypatch):
    from studia import app, osutil

    winutil = osutil.impl()          # winutil on Windows, linuxutil on Linux

    other = accounts.create("bob", "Bob B", "2024")
    seen = {}
    fake = types.ModuleType("webview")

    def create_window(title, url=None, js_api=None, **kw):
        seen["window"] = FakeWindow(js_api)
        return seen["window"]

    def start(**kw):
        w = seen["window"]
        api = w.api
        assert api._ready.wait(30)
        seen["switch"] = api.profile_switch(other.id)
        assert w.closed.wait(10), "the window was not closed for the restart"

    fake.create_window, fake.start = create_window, start
    monkeypatch.setitem(sys.modules, "webview", fake)
    monkeypatch.setattr(winutil, "acquire_single_instance", lambda *a, **k: True)
    monkeypatch.setattr(app, "acquire_single_instance", lambda *a, **k: True)
    monkeypatch.setattr(app, "set_app_id", lambda *a, **k: None)
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: calls.append(cmd))
    app._main(["--debug"])
    assert seen["switch"]["ok"] and accounts.load()[0] == other.id
    assert seen["window"].closing_result == [True]                       # no "transcription running" question
    restarts = [c for c in calls if c[1:3] == ["-m", "studia.app"]]
    splashes = [c for c in calls if any(str(x).endswith("splash.py") for x in c)]
    assert len(restarts) == 1 and restarts[0][3:] == ["--debug", "--restarted"]
    if sys.platform != "win32":
        assert not splashes                                              # no start-up window on Linux
        return
    assert len(splashes) == 1 and splashes[0][1:3] == ["-I", "-S"] and splashes[0][5] in ("light", "dark")
    assert splashes[0][6:8] == ["--python", restarts[0][0]]             # a restart from it uses the app's Python


@pytest.mark.parametrize("answers,closes", [([True, False], False), ([False], False), ([True, True], True)])
def test_closing_asks_every_question_before_it_cancels_anything(isolated_home, monkeypatch, answers, closes):
    from studia import app, osutil

    winutil = osutil.impl()          # winutil on Windows, linuxutil on Linux

    seen = {}
    fake = types.ModuleType("webview")

    def create_window(title, url=None, js_api=None, **kw):
        seen["window"] = FakeWindow(js_api)
        return seen["window"]

    def start(**kw):
        w = seen["window"]
        api = w.api
        assert api._ready.wait(30)
        monkeypatch.setattr(api._s.jobs, "busy", lambda: True)                        # a transcription runs
        monkeypatch.setattr(api._s.jobs, "cancel", lambda job_id=None: seen.update(jobs_cancelled=True))
        api._setup_running, api._setup_cancel = True, threading.Event()              # and the setup downloads
        api._ask_close = lambda: False           # a page that can't ask: Windows' own question (see the test below)
        asked = iter(answers)
        w.create_confirmation_dialog = lambda title, text: next(asked)
        w.destroy()
        seen["download_cancelled"] = api._setup_cancel.is_set()
        api._setup_running = False

    fake.create_window, fake.start = create_window, start
    monkeypatch.setitem(sys.modules, "webview", fake)
    monkeypatch.setattr(winutil, "acquire_single_instance", lambda *a, **k: True)
    monkeypatch.setattr(app, "acquire_single_instance", lambda *a, **k: True)
    monkeypatch.setattr(app, "set_app_id", lambda *a, **k: None)
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: None)
    app._main([])
    assert seen["window"].closing_result == [closes]
    assert seen["download_cancelled"] == closes and seen.get("jobs_cancelled", False) == closes


@pytest.mark.parametrize("dont_ask", [False, True])
def test_closing_while_transcribing_asks_in_the_page_once(isolated_home, monkeypatch, dont_ask):
    from studia import app, osutil

    winutil = osutil.impl()          # winutil on Windows, linuxutil on Linux

    seen = {}
    fake = types.ModuleType("webview")

    def create_window(title, url=None, js_api=None, **kw):
        seen["window"] = FakeWindow(js_api)
        return seen["window"]

    def start(**kw):
        w = seen["window"]
        api = w.api
        assert api._ready.wait(30)
        monkeypatch.setattr(api._s.jobs, "busy", lambda: True)                        # a transcription runs
        monkeypatch.setattr(api._s.jobs, "shutdown", lambda: seen.update(stopped=True))
        pushed = []
        api._push = pushed.append
        w.create_confirmation_dialog = lambda title, text: pytest.fail("asked in a window of its own")
        w.destroy()                                      # the window's ×: the page asks, nothing stops yet
        seen["asked"] = [e for e in pushed if e.get("type") == "close-ask"]
        seen["first"], seen["stopped_first"] = w.closing_result, seen.get("stopped", False)
        api.close_seen()
        api.close_now(dont_ask)                          # Close in the page
        assert w.closed.wait(10)
        seen["setting"] = api._s.settings.ask_before_closing

    fake.create_window, fake.start = create_window, start
    monkeypatch.setitem(sys.modules, "webview", fake)
    monkeypatch.setattr(winutil, "acquire_single_instance", lambda *a, **k: True)
    monkeypatch.setattr(app, "acquire_single_instance", lambda *a, **k: True)
    monkeypatch.setattr(app, "set_app_id", lambda *a, **k: None)
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: None)
    app._main([])
    assert len(seen["asked"]) == 1 and seen["first"] == [False] and not seen["stopped_first"]
    assert seen["window"].closing_result == [True] and seen["stopped"]
    assert seen["setting"] is (not dont_ask)


def test_an_update_windows_wont_start_is_said_and_the_app_opens_again(isolated_home, monkeypatch, tmp_path):
    from studia import app, updates, osutil

    winutil = osutil.impl()          # winutil on Windows, linuxutil on Linux

    seen, shown, calls = {}, [], []
    fake = types.ModuleType("webview")

    def create_window(title, url=None, js_api=None, **kw):
        seen["window"] = FakeWindow(js_api)
        return seen["window"]

    def start(**kw):
        api = seen["window"].api
        assert api._ready.wait(30)
        api._update_installer = tmp_path / "IEUStudia-Setup-9.9.0.exe"
        seen["window"].destroy()

    fake.create_window, fake.start = create_window, start
    monkeypatch.setitem(sys.modules, "webview", fake)
    monkeypatch.setattr(winutil, "acquire_single_instance", lambda *a, **k: True)
    monkeypatch.setattr(app, "acquire_single_instance", lambda *a, **k: True)
    monkeypatch.setattr(app, "set_app_id", lambda *a, **k: None)
    monkeypatch.setattr(updates, "run_installer", lambda path: False)          # an antivirus blocked it
    monkeypatch.setattr(winutil, "message_box", lambda text, title="", flags=0: shown.append(text) or 1)
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: calls.append(cmd))
    app._main([])
    assert shown and "couldn't start the update" in shown[0]
    assert [c for c in calls if c[1:3] == ["-m", "studia.app"]]                 # opened again


@pytest.mark.parametrize("area,size", [((0, 0, 1440, 852), (1280, 820, False, 80, 16)),    # fits: centred
                                       ((0, 0, 1366, 720), (1280, 704, True, 43, 8)),      # 1366 x 768: maximized
                                       ((0, 0, 1280, 672), (1264, 656, True, 8, 8)),       # 1920 x 1080 at 150 %
                                       ((0, 40, 1920, 1000), (1280, 820, False, 320, 130)),  # taskbar at the top
                                       ((0, 0, 0, 0), (1280, 820, False, None, None))])    # unknown
def test_the_window_fits_the_screen(monkeypatch, area, size):
    from studia import app, osutil

    winutil = osutil.impl()          # winutil on Windows, linuxutil on Linux

    monkeypatch.setattr(winutil, "work_area", lambda: area)
    assert app._window_size() == size


@pytest.mark.skipif(sys.platform != "win32", reason="the start-up window and the Windows certificate store are Windows only")
def test_windows_certificates_come_after_the_start_up_window_and_before_anything_connects(isolated_home, monkeypatch):
    from studia import app, net, winutil

    order = []
    fake = types.ModuleType("webview")

    def create_window(title, url=None, js_api=None, **kw):
        order.append("window")                   # the services (the first connections) start after this
        order.append(FakeWindow(js_api))
        return order[-1]

    def start(**kw):
        w = order[-1]
        assert w.api._ready.wait(30)
        w.destroy()

    fake.create_window, fake.start = create_window, start
    monkeypatch.setitem(sys.modules, "webview", fake)
    monkeypatch.setattr(winutil, "acquire_single_instance", lambda *a, **k: True)
    monkeypatch.setattr(app, "acquire_single_instance", lambda *a, **k: True)
    monkeypatch.setattr(app, "set_app_id", lambda *a, **k: None)
    monkeypatch.setattr(net, "use_windows_certificates", lambda: order.append("certificates"))
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: order.append(
        "start-up window" if any(str(x).endswith("splash.py") for x in cmd) else "other"))
    app._main([])
    assert [x for x in order if isinstance(x, str)][:3] == ["start-up window", "certificates", "window"]


@pytest.mark.parametrize("args", [["--agent"], ["--selftest", "out.json"]])
def test_the_helper_and_the_selftest_use_windows_certificates_first(monkeypatch, args):
    from studia import agent, app, net

    order = []
    monkeypatch.setattr(net, "use_windows_certificates", lambda: order.append("certificates"))
    monkeypatch.setattr(agent, "main", lambda argv: order.append("helper"))
    monkeypatch.setattr(app, "_selftest", lambda out: order.append("selftest") or 0)
    try:
        app.main(args)
    except SystemExit:
        pass
    assert order == ["certificates", "helper" if args == ["--agent"] else "selftest"]


def test_nothing_before_the_start_up_window_loads_the_network_libraries():
    """What _main imports before it spawns the start-up window: requests and the SSL setup only come after it."""
    code = ("import sys, studia.app\n"
            "from studia import accounts, appconfig, config, hardware, i18n, launch, log, paths, winutil\n"
            "print([m for m in ('requests', 'urllib3', 'ssl', 'truststore') if m in sys.modules])")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert out.stdout.strip() == "[]", out.stderr
