"""Links on PCs whose default browser doesn't open: Microsoft Edge instead, and when nothing opens the page says so
and the link is copied. No browser is started: the Windows calls are replaced."""

import sys

import pytest

from studia import api as api_mod
from studia import chatgpt


def _api():
    from studia.services import Services

    return api_mod.Api(Services())


def test_a_link_that_no_browser_opens_is_said_and_copied(monkeypatch):
    copied = []
    monkeypatch.setattr(api_mod, "open_in_browser", lambda url: False)
    monkeypatch.setattr(api_mod, "set_clipboard", lambda text: copied.append(text) or True)
    api = _api()
    r = api.open_url("https://chatgpt.com/pricing")
    assert r["ok"] is False and r["copied"] and "copied" in r["error"] and copied == ["https://chatgpt.com/pricing"]
    monkeypatch.setattr(api_mod, "set_clipboard", lambda text: False)
    r = api.open_url("https://chatgpt.com/pricing")
    assert r["ok"] is False and not r["copied"] and "default browser" in r["error"]
    monkeypatch.setattr(api_mod, "open_in_browser", lambda url: True)
    assert api.open_url("https://chatgpt.com/pricing") == {"ok": True}
    monkeypatch.setattr(api_mod, "open_in_browser", lambda url: pytest.fail("only web links are opened"))
    assert api.open_url("file:///C:/Windows/notepad.exe") == {"ok": True}


def test_continue_with_chatgpt_can_be_opened_again_and_stopped_from_the_page(monkeypatch):
    opened = []
    monkeypatch.setattr(api_mod, "open_in_browser", lambda url: opened.append(url) or True)
    api = _api()
    try:
        r = api.ai_login("chatgpt")
        assert r["ok"] and r["state"] == "waiting" and r["launched"] and opened == [r["link"]]
        again = api.ai_login_again()
        assert again["ok"] and again["link"] == r["link"] and opened == [r["link"], r["link"]]
        assert api.ai_login_cancel() == {"ok": True}
        assert chatgpt.status()["sign_in"]["state"] == ""
        assert api.ai_login_again() == {"ok": True, "state": ""}
    finally:
        chatgpt.cancel_sign_in()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' shell")
def test_windows_falls_back_to_edge_when_the_default_browser_does_not_start(monkeypatch):
    from studia import winutil

    tried, started = [], []
    monkeypatch.setattr(winutil, "_elevated", lambda: False)
    monkeypatch.setattr(winutil, "_default_browser", lambda scheme: (True, r"C:\Browser\browser.exe"))
    monkeypatch.setattr(winutil, "_shell_open", lambda target, wait_s=20.0: tried.append(target) or (False, "no association"))
    monkeypatch.setattr(winutil, "_edge", lambda: r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
    monkeypatch.setattr(winutil.subprocess, "Popen", lambda args, **kw: started.append(args))
    assert winutil.open_in_browser("https://example.com/a?code=secret") is True
    assert tried == ["https://example.com/a?code=secret"]
    assert started == [[r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", "https://example.com/a?code=secret"]]
    # no Edge program found: Edge's own link; and when that fails too, False
    tried.clear()
    monkeypatch.setattr(winutil, "_edge", lambda: "")
    assert winutil.open_in_browser("https://example.com/") is False
    assert tried == ["https://example.com/", "microsoft-edge:https://example.com/"]
    # the default browser starting: nothing else is tried
    tried.clear()
    monkeypatch.setattr(winutil, "_shell_open", lambda target, wait_s=20.0: tried.append(target) or (True, ""))
    assert winutil.open_in_browser("https://example.com/") is True and tried == ["https://example.com/"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' shell")
def test_a_pc_without_a_working_default_browser_gets_no_pick_an_app_window(monkeypatch):
    """Windows answers a link with no program with a "Pick an app" window and says it worked: asked first."""
    from studia import winutil

    tried, started = [], []
    monkeypatch.setattr(winutil, "_elevated", lambda: False)
    monkeypatch.setattr(winutil, "_shell_open", lambda target, wait_s=20.0: tried.append(target) or (True, ""))
    monkeypatch.setattr(winutil.subprocess, "Popen", lambda args, **kw: started.append(args))
    monkeypatch.setattr(winutil, "_edge", lambda: r"C:\Edge\msedge.exe")
    # http has no program (a removed browser) but https does: that browser, for IEUStudia's own 127.0.0.1 link
    monkeypatch.setattr(winutil, "_default_browser", lambda scheme: (True, r"C:\Chrome\chrome.exe" if scheme == "https" else ""))
    assert winutil.open_in_browser("http://127.0.0.1:5000/sign-in") is True
    assert started == [[r"C:\Chrome\chrome.exe", "http://127.0.0.1:5000/sign-in"]] and tried == []
    # none set at all: Edge
    started.clear()
    monkeypatch.setattr(winutil, "_default_browser", lambda scheme: (True, ""))
    assert winutil.open_in_browser("https://example.com/") is True
    assert started == [[r"C:\Edge\msedge.exe", "https://example.com/"]] and tried == []
    # Windows can't say: the shell decides
    started.clear()
    monkeypatch.setattr(winutil, "_default_browser", lambda scheme: (False, ""))
    assert winutil.open_in_browser("https://example.com/") is True
    assert tried == ["https://example.com/"] and started == []
    # run as administrator: through Explorer, which runs as the signed-in user
    tried.clear()
    monkeypatch.setattr(winutil, "_elevated", lambda: True)
    monkeypatch.setattr(winutil, "_default_browser", lambda scheme: (True, r"C:\Chrome\chrome.exe"))
    assert winutil.open_in_browser("https://example.com/") is True
    assert started == [["explorer.exe", "https://example.com/"]] and tried == []


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' shell")
def test_the_default_browser_is_read_from_windows_without_opening_anything(monkeypatch):
    """The real question to Windows (it opens nothing), and a program that is gone counts as none."""
    from studia import winutil

    known, program = winutil._default_browser("https")
    assert isinstance(known, bool) and isinstance(program, str)
    known, program = winutil._default_browser("ieustudia-no-such-scheme-4f2a")
    assert program == ""


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' shell")
def test_the_shell_is_asked_from_a_thread_with_com_and_a_failure_comes_back(monkeypatch):
    """Never with the real shell: Windows answers an address it has no program for with a "Pick an app" window."""
    import threading

    from studia import winutil

    calls = []

    class Fn:
        def __init__(self, name, result):
            self.name, self.result = name, result

        def __call__(self, *args):
            calls.append((self.name, threading.current_thread().name, args))
            return self.result

    class Dll:
        def __init__(self, name, use_last_error=False):
            if name == "ole32":
                self.CoInitializeEx, self.CoUninitialize = Fn("init", 0), Fn("uninit", None)
            else:
                self.ShellExecuteExW = Fn("shell", 0)

    monkeypatch.setattr(winutil.ctypes, "WinDLL", Dll)
    monkeypatch.setattr(winutil.ctypes, "byref", lambda obj: obj)
    monkeypatch.setattr(winutil.ctypes, "get_last_error", lambda: 1155)
    ok, error = winutil._shell_open("https://example.com/", wait_s=5)
    assert ok is False and ("1155" in error or "associated" in error)
    assert [c[0] for c in calls] == ["init", "shell", "uninit"]
    assert all(c[1] == "open-url" for c in calls)                      # a thread of its own, with COM set up
    assert calls[0][2] == (None, 0x6)                                  # apartment-threaded, no OLE1 DDE
    info = calls[1][2][0]
    assert info.lpFile == "https://example.com/" and info.fMask == 0x500 and info.nShow == 1
    assert winutil._site("https://auth.openai.com/api/accounts/authorize?state=s&code=c") == \
        "https://auth.openai.com/api/accounts/authorize"
