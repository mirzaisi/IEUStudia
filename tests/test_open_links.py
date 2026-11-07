"""Links on PCs whose default browser doesn't open: Microsoft Edge instead, and when nothing opens the page says so
and the link is copied. Which browser opened ChatGPT's sign-in page, and why not the default one. No browser is
started and no window opens: the Windows calls are replaced."""

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from studia import api as api_mod
from studia import chatgpt, i18n, osutil

WEB = Path(i18n.__file__).parent / "web"


def _api():
    from studia.services import Services

    return api_mod.Api(Services())


@pytest.fixture
def no_reach(monkeypatch):
    """The sign-in's look at whether OpenAI can be reached: not from the tests."""
    monkeypatch.setattr(chatgpt, "_reach", lambda track: None)
    chatgpt._attempt.clear()
    yield
    chatgpt.cancel_sign_in()


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


def test_continue_with_chatgpt_can_be_opened_again_and_stopped_from_the_page(monkeypatch, no_reach):
    opened = []
    monkeypatch.setattr(osutil.impl(), "open_link", lambda url, way="": opened.append((url, way)) or {
        "ok": True, "browser": "Microsoft Edge" if way in ("", "edge") else "Brave", "via": way or "edge",
        "why": "" if way else "no_default"})
    api = _api()
    r = api.ai_login("chatgpt")
    assert r["ok"] and r["state"] == "waiting" and r["launched"] and opened == [(r["link"], "")]
    assert (r["browser"], r["why"]) == ("Microsoft Edge", "no_default")         # the page says why Edge
    si = chatgpt.status()["sign_in"]
    assert (si["browser"], si["via"], si["why"]) == ("Microsoft Edge", "edge", "no_default")
    # Edge showed nothing: Open the page again moves on to Windows' own open, Open in Microsoft Edge is Edge
    again = api.ai_login_again()
    assert again["ok"] and again["link"] == r["link"] and opened[-1] == (r["link"], "shell") and again["way"] == "shell"
    edge = api.ai_login_edge()
    assert edge["ok"] and edge["way"] == "edge" and opened[-1] == (r["link"], "edge") and edge["launched"]
    assert chatgpt.status()["sign_in"]["why"] == "no_default"                    # why the first try used Edge: kept
    assert api.ai_login_cancel() == {"ok": True}
    assert chatgpt.status()["sign_in"]["state"] == ""
    assert api.ai_login_again() == {"ok": True, "state": ""} and api.ai_login_edge() == {"ok": True, "state": ""}


def test_chatgpts_own_sign_in_has_no_sign_in_with_a_code(monkeypatch, no_reach):
    """Device code (a code typed on another device) is OpenAI's for Codex only: asked with IEUStudia's own client id,
    OpenAI answered HTTP 400 invalid_client "This app is unavailable". So ChatGPT's own sign-in has none: asked for
    one, the page opens in the browser as always, and nothing asks OpenAI for a code. (The Codex CLI's own sign-in
    with a code stays: test_cli_signin.)"""
    from studia import ai

    opened = []
    monkeypatch.setattr(osutil.impl(), "open_link", lambda url, way="": opened.append(url) or {
        "ok": True, "browser": "Brave", "via": "default", "why": ""})
    monkeypatch.setattr(ai, "start_login", lambda *a, **kw: pytest.fail("not the CLI's sign-in"))
    r = _api().ai_login("chatgpt", False, True)
    assert r["ok"] and r["state"] == "waiting" and "device" not in r and "code" not in r and opened == [r["link"]]
    assert r["link"].startswith("http://127.0.0.1:") and r["page"].startswith(chatgpt.AUTHORIZE_URL)
    assert not chatgpt.status()["sign_in"].get("device")
    code = Path(chatgpt.__file__).read_text(encoding="utf-8").lower()
    for gone in ("deviceauth", "usercode", "device_code", "device_auth", "grant-type:device"):
        assert gone not in code, gone


def test_the_sign_in_looks_whether_ieustudias_own_window_stayed_in_front(monkeypatch, no_reach):
    """The page opened in the browser while IEUStudia's window stayed in front: the app asks Windows which window is
    in front (its own, by handle), not a guess."""
    import http.client
    import urllib.parse

    monkeypatch.setattr(osutil.impl(), "open_link", lambda url, way="": {"ok": True, "browser": "Brave",
                                                                         "via": "default", "why": ""})
    monkeypatch.setattr(chatgpt, "BEHIND_S", 0.05)
    asked = []
    monkeypatch.setattr(api_mod, "is_foreground", lambda hwnd: asked.append(hwnd) or True)
    api = _api()
    api._hwnd = 4242
    r = api.ai_login("chatgpt")
    u = urllib.parse.urlsplit(r["link"])
    conn = http.client.HTTPConnection(u.hostname, u.port, timeout=5)      # the browser asks for the page
    conn.request("GET", u.path)
    assert conn.getresponse().status == 302
    conn.close()
    end = time.monotonic() + 5
    while time.monotonic() < end and not chatgpt.status()["sign_in"].get("behind"):
        time.sleep(0.02)
    assert chatgpt.status()["sign_in"]["behind"] is True and asked == [4242]
    api._hwnd = 0                                            # no window shown yet: never said to be in front
    assert api._in_front() is False


def test_copy_details_and_a_made_up_address_from_the_page(monkeypatch, no_reach):
    copied = []
    monkeypatch.setattr(api_mod, "set_clipboard", lambda text: copied.append(text) or True)
    monkeypatch.setattr(osutil.impl(), "open_link", lambda url: {"ok": False, "browser": "", "via": "", "why": ""})
    api = _api()
    assert api.ai_login("chatgpt")["launched"] is False
    r = api.ai_login_details()
    assert r["ok"] and copied == [r["text"]] and "no_browser" in r["text"] and "IEUStudia" in r["text"]
    r = api.ai_login_paste("hello")
    assert r["ok"] and r["state"] == "refused" and "isn't the address" in r["error"]
    monkeypatch.setattr(api_mod, "set_clipboard", lambda text: False)
    assert api.ai_login_details()["ok"] is False


def test_default_apps_opens_windows_settings_or_says_how(monkeypatch):
    calls = []
    monkeypatch.setattr(osutil.impl(), "open_default_apps", lambda: calls.append(1) or True)
    api = _api()
    assert api.open_default_apps() == {"ok": True} and calls == [1]
    monkeypatch.setattr(osutil.impl(), "open_default_apps", lambda: False)
    r = api.open_default_apps()
    assert r["ok"] is False and "Default apps" in r["error"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' Settings")
def test_default_apps_is_windows_settings_page(monkeypatch):
    from studia import winutil

    started = []
    monkeypatch.setattr(winutil.os, "startfile", lambda target: started.append(target), raising=False)
    assert winutil.open_default_apps() and started == ["ms-settings:defaultapps"]

    def blocked(target):
        raise OSError("blocked")

    monkeypatch.setattr(winutil.os, "startfile", blocked, raising=False)
    assert winutil.open_default_apps() is False


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
def test_the_page_learns_which_browser_opened_and_why_not_the_default(monkeypatch):
    """What the sign-in says: "Windows has no working default browser, so the page opened in Microsoft Edge"."""
    from studia import winutil

    tried, started = [], []
    monkeypatch.setattr(winutil, "_elevated", lambda: False)
    monkeypatch.setattr(winutil, "_edge", lambda: r"C:\Edge\msedge.exe")
    monkeypatch.setattr(winutil.subprocess, "Popen", lambda args, **kw: started.append(args))
    shell = {"ok": True}
    monkeypatch.setattr(winutil, "_shell_open", lambda target, wait_s=20.0: tried.append(target) or (shell["ok"], "x"))
    brave = r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"
    monkeypatch.setattr(winutil, "_default_browser", lambda scheme: (True, brave))
    assert winutil.open_link("http://127.0.0.1:5000/sign-in") == {"ok": True, "browser": "Brave", "via": "default",
                                                                  "why": ""}
    shell["ok"] = False                                      # the default one doesn't start
    assert winutil.open_link("https://example.com/") == {"ok": True, "browser": "Microsoft Edge", "via": "edge",
                                                         "why": "default_failed"}
    monkeypatch.setattr(winutil, "_default_browser", lambda scheme: (True, ""))     # none set, or uninstalled
    assert winutil.open_link("https://example.com/")["why"] == "no_default"
    # http set to another browser than https (the first link is http): the one that opens https links
    started.clear()
    chrome = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    monkeypatch.setattr(winutil, "_default_browser", lambda scheme: (True, chrome if scheme == "https" else brave))
    assert winutil.open_link("http://127.0.0.1:5000/sign-in") == {"ok": True, "browser": "Google Chrome",
                                                                  "via": "https", "why": ""}
    assert started == [[chrome, "http://127.0.0.1:5000/sign-in"]]
    # nothing at all
    monkeypatch.setattr(winutil, "_default_browser", lambda scheme: (True, ""))
    monkeypatch.setattr(winutil, "_edge", lambda: "")
    assert winutil.open_link("https://example.com/") == {"ok": False, "browser": "", "via": "", "why": "no_default"}
    assert winutil.open_in_browser("https://example.com/") is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' shell")
def test_each_way_open_the_page_again_takes_and_edge_whatever_the_default_is(monkeypatch):
    """Open the page again moves on through these, Open in Microsoft Edge is the second: the default browser's program
    started with the link, Microsoft Edge (its program, else its microsoft-edge: link) and Windows' own open. A way
    that can't start anything here says so (the caller moves on), never with a "Pick an app" window."""
    from studia import winutil

    tried, started = [], []
    monkeypatch.setattr(winutil, "_elevated", lambda: False)
    monkeypatch.setattr(winutil, "_shell_open", lambda target, wait_s=20.0: tried.append(target) or (True, ""))
    monkeypatch.setattr(winutil.subprocess, "Popen", lambda args, **kw: started.append(args))
    monkeypatch.setattr(winutil, "_edge", lambda: r"C:\Edge\msedge.exe")
    brave = r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"
    monkeypatch.setattr(winutil, "_default_browser", lambda scheme: (True, brave))
    link = "http://127.0.0.1:5000/sign-in"
    assert winutil.open_link(link, "default") == {"ok": True, "browser": "Brave", "via": "default", "why": ""}
    assert started == [[brave, link]] and tried == []
    started.clear()
    assert winutil.open_link(link, "edge") == {"ok": True, "browser": "Microsoft Edge", "via": "edge", "why": ""}
    assert started == [[r"C:\Edge\msedge.exe", link]] and tried == []          # Edge, whatever the default is
    started.clear()
    assert winutil.open_link(link, "shell") == {"ok": True, "browser": "Brave", "via": "shell", "why": ""}
    assert tried == [link] and started == []
    tried.clear()
    monkeypatch.setattr(winutil, "_edge", lambda: "")                           # no Edge program: its own link
    assert winutil.open_link(link, "edge")["ok"] and tried == ["microsoft-edge:" + link]
    tried.clear()
    monkeypatch.setattr(winutil, "_default_browser", lambda scheme: (True, ""))  # no default browser at all
    assert winutil.open_link(link, "default")["ok"] is False and winutil.open_link(link, "shell")["ok"] is False
    assert tried == [] and started == []
    # nor Edge (no program, nothing opens its links): its link isn't handed to Windows, which would only bring up a
    # "Pick an app" window; run as administrator, not to Explorer either (it always starts: Edge said to be asked)
    assert winutil.open_link(link, "edge")["ok"] is False and tried == [] and started == []
    monkeypatch.setattr(winutil, "_elevated", lambda: True)
    assert winutil.open_link(link, "edge")["ok"] is False and tried == [] and started == []
    monkeypatch.setattr(winutil, "_elevated", lambda: False)
    store = r"C:\Program Files\WindowsApps\TheBrowserCompany.Arc_1.2\Arc.exe"   # a Store browser: only the shell
    monkeypatch.setattr(winutil, "_default_browser", lambda scheme: (True, store))
    assert winutil.open_link(link, "default")["ok"] is False and started == []
    # run as administrator: started as the signed-in user, through Explorer
    monkeypatch.setattr(winutil, "_elevated", lambda: True)
    monkeypatch.setattr(winutil, "_default_browser", lambda scheme: (True, brave))
    assert winutil.open_link(link, "edge")["ok"] and started[-1] == ["explorer.exe", "microsoft-edge:" + link]
    assert winutil.open_link(link, "default")["ok"] and started[-1] == ["explorer.exe", link]
    # whether Edge is on this PC: its program, or its microsoft-edge: link
    monkeypatch.setattr(winutil, "_edge", lambda: "")
    monkeypatch.setattr(winutil, "_default_browser", lambda scheme: (True, r"C:\Edge\msedge.exe" if scheme == "microsoft-edge" else ""))
    assert winutil.edge_available()
    monkeypatch.setattr(winutil, "_default_browser", lambda scheme: (True, ""))
    assert not winutil.edge_available()


def test_the_ways_on_linux(monkeypatch):
    """Linux: Microsoft Edge only when it is installed; the desktop's own open is the default browser there, so it
    isn't one more way. Nothing is started: the desktop is replaced."""
    from studia import linuxutil

    started = []
    monkeypatch.setattr(linuxutil, "_detached", lambda cmd: started.append(cmd) or True)
    monkeypatch.setattr(linuxutil, "open_in_browser", lambda url: started.append(["default", url]) or True)
    monkeypatch.setattr(linuxutil.shutil, "which", lambda name: None)
    assert not linuxutil.edge_available() and not linuxutil.elevated()
    assert linuxutil.open_link("http://x/", "edge")["ok"] is False and started == []
    assert linuxutil.open_link("http://x/", "shell")["ok"] is False and started == []
    assert linuxutil.open_link("http://x/", "default") == {"ok": True, "browser": "", "via": "default", "why": ""}
    monkeypatch.setattr(linuxutil.shutil, "which", lambda name: "/usr/bin/microsoft-edge" if name == "microsoft-edge" else None)
    assert linuxutil.edge_available()
    assert linuxutil.open_link("http://x/", "edge") == {"ok": True, "browser": "Microsoft Edge", "via": "edge", "why": ""}
    assert started[-1] == ["/usr/bin/microsoft-edge", "http://x/"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' shell")
def test_browser_names_for_the_page_and_the_report(monkeypatch):
    from studia import winutil

    assert winutil.browser_name(r"C:\Program Files\Mozilla Firefox\firefox.exe") == "Firefox"
    assert winutil.browser_name(r"C:\Users\x\AppData\Local\Programs\Opera\launcher.exe") == "Opera"
    assert winutil.browser_name(r"C:\Program Files\WindowsApps\TheBrowserCompany.Arc_1.2\Arc.exe") == "Arc"
    assert winutil.browser_name(r"C:\Tools\mybrowser.exe") == "Mybrowser" and winutil.browser_name("") == ""
    monkeypatch.setattr(winutil, "_default_browser", lambda scheme: (True, r"C:\Users\student\AppData\Local\Vivaldi\Application\vivaldi.exe")
                        if scheme == "https" else (True, ""))
    assert winutil.default_browsers() == {"https": "Vivaldi", "http": "none set"}      # no paths: they hold the name


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


# ----------------------------------------------------------------------------- folders and files that don't open
def test_a_folder_or_file_that_doesnt_open_says_why_in_plain_words(monkeypatch, tmp_path):
    """Folder, Text file, Open logs: a course folder not made yet is made; a failure comes back in plain words (the
    page shows it) instead of Windows' "[WinError 2] The system cannot find the file specified"."""
    from pathlib import Path

    opened = []
    monkeypatch.setattr(api_mod, "open_path", lambda p: opened.append(Path(p)))
    api = _api()
    folder = tmp_path / "out" / "EEE 215"
    monkeypatch.setattr(api._s, "day_files", lambda key: {"folder": str(folder), "md": ""})
    assert api.open_file("17933|x|lecture", "folder") == {"ok": True} and folder.is_dir() and opened == [folder]
    assert api.open_course_folder("not-a-course") == {"ok": True} and api._s.output_root.is_dir()

    def gone(p):
        raise FileNotFoundError(2, "The system cannot find the file specified", str(p))

    monkeypatch.setattr(api_mod, "open_path", gone)
    r = api.open_file("17933|x|lecture", "md")              # no text file yet: its folder, which then fails
    assert r["ok"] is False and "isn't there any more" in r["error"] and "WinError" not in r["error"]

    def blocked(p):
        raise PermissionError(13, "Access is denied", str(p))

    monkeypatch.setattr(api_mod, "open_path", blocked)
    for r in (api.open_logs(), api.open_course_folder("not-a-course")):
        assert r["ok"] is False and r["error"] and "Access is denied" not in r["error"]


PAGE = """
const web = %(web)s;
const stub = () => new Proxy(function () {}, { get: (t, k) => k === Symbol.toPrimitive ? () => "" : k === "length" ? 0 : stub(),
  apply: () => stub(), construct: () => stub() });
globalThis.window = globalThis;
const toasts = [];
const el = () => ({ innerHTML: "", style: {}, dataset: {}, classList: { toggle() {}, add() {}, remove() {} }, scrollTo() {},
  querySelector: () => null, querySelectorAll: () => [], addEventListener() {}, remove() {}, focus() {} });
const els = { "#top": el(), "#view": el(), "#toasts": { appendChild: (b) => toasts.push(b) } };
globalThis.document = new Proxy({}, { get: (t, k) => k === "compatMode" ? "CSS1Compat"
  : k === "createElement" ? () => el() : k === "querySelector" ? (s) => els[s] || stub() : k === "querySelectorAll" ? () => []
  : k === "addEventListener" ? () => {} : stub() });
globalThis.localStorage = { getItem: () => null, setItem() {} };
globalThis.navigator = { userAgent: "node", platform: "Win32" };
globalThis.CSS = { escape: (s) => s };
globalThis.addEventListener = () => {}; globalThis.matchMedia = () => ({ matches: false, addEventListener() {} });
globalThis.requestAnimationFrame = (f) => f();
globalThis.setInterval = () => 0; globalThis.setTimeout = () => 0;      // no boot, no polling
for (const f of ["i18n.js", "icons.js", "app.js", "effort.js", "course.js", "plans.js", "digest.js"]) require(web + "/" + f);
const St = globalThis.Studia, S = St.S, A = St.A;
const told = () => toasts.splice(0).map((b) => b.className.replace("toast ", "") + ": " + b.innerHTML.replace(/<[^>]*>/g, "").trim()
  .replace(/&#39;/g, "'").replace(/&amp;/g, "&"));
const out = {};
(async () => {
  S.mode = "app"; S.view = { name: "home" }; S.boot = {}; S.courses = [];
  St.mocks.open_output_file = () => ({ ok: true });
  const said = St.said, passed = [];
  St.said = (p) => { passed.push("said"); return said(p); };
  S.update = { page: "https://github.com/mirzaisi/IEUStudia/releases/tag/v9" };
  S.day = { day_key: "17933|x|lecture" };
  await A["update-page"]();
  await A.ts({ dataset: { url: "https://panopto.ieu.edu.tr/Panopto/Pages/Viewer.aspx?id=a", t: "75.4" } });
  await A.file({ dataset: { what: "md" } });
  await A["open-folder"]({ dataset: { id: "c1" } });
  await A.logs();
  await A["dg-file"]({ dataset: { path: "C:/IEUStudia/digest.md" } });
  out.passed = passed.length;
  out.quiet = told();
  await said(Promise.resolve({ ok: false, copied: true, error: "No browser could be opened on this PC. The link was copied." }));
  await said(Promise.resolve({ ok: false, error: "EEE 215 isn't there any more: was it moved or deleted?" }));
  out.answer = await said(Promise.resolve({ ok: true, x: 1 }));
  out.toasts = told();
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_the_page_says_when_a_link_folder_or_file_didnt_open(tmp_path):
    """Release page, a transcript's timestamp, Text file, Folder, Open logs, the digest's file: each passes the answer
    to Studia.said, which says why when it didn't open (and calmly that the link was copied)."""
    script = tmp_path / "page.js"
    script.write_text(PAGE % {"web": json.dumps(WEB.as_posix())}, encoding="utf-8")
    r = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["passed"] == 6 and out["quiet"] == []                    # each one, and nothing said when it worked
    assert out["toasts"] == ["info: No browser could be opened on this PC. The link was copied.",
                             "error: EEE 215 isn't there any more: was it moved or deleted?"]
    assert out["answer"] == {"ok": True, "x": 1}
