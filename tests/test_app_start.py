"""Starting the release build where Windows gets in the way: a portable zip unpacked with Windows' Extract All (the
internet mark on .NET's files stopped every start with 'Failed to resolve Python.Runtime.Loader.Initialize'), and
a WebView2 that is registered but doesn't start (an empty window forever, with nothing in the log)."""

import os
import sys
from pathlib import Path

import pytest

from studia import app

MARK = "[ZoneTransfer]\r\nZoneId=3\r\nReferrerUrl=https://github.com/\r\n"


@pytest.mark.skipif(sys.platform != "win32", reason="NTFS streams: Windows' internet mark")
def test_a_portable_copy_marked_as_from_the_internet_is_unblocked(tmp_path, monkeypatch):
    root = tmp_path / "IEUStudia"
    net = [root / "IEUStudia.exe", root / "_internal/pythonnet/runtime/Python.Runtime.dll",
           root / "_internal/clr_loader/ffi/dlls/amd64/ClrLoader.dll",
           root / "_internal/webview/lib/Microsoft.Web.WebView2.Core.dll",
           root / "_internal/webview/lib/Microsoft.Web.WebView2.WinForms.dll",
           root / "_internal/webview/lib/runtimes/win-x64/native/WebView2Loader.dll"]
    other = root / "_internal/numpy/_core/_multiarray_umath.pyd"     # nothing checks the mark on these: left alone
    for f in net + [other]:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"MZ")
        Path(f"{f}:Zone.Identifier").write_text(MARK, encoding="ascii")
    assert os.path.exists(f"{net[1]}:Zone.Identifier")            # (the streams really are there)
    assert app._unblock_bundle(root) == []
    assert not any(os.path.exists(f"{f}:Zone.Identifier") for f in net)
    assert os.path.exists(f"{other}:Zone.Identifier") and all(f.read_bytes() == b"MZ" for f in net)
    assert app._unblock_bundle(root) == []                         # every later start: nothing to do
    # a folder IEUStudia may not change: the file that stays marked is told (the start explains what to do)
    Path(f"{net[1]}:Zone.Identifier").write_text(MARK, encoding="ascii")
    remove = os.remove

    def refused(path):
        if str(path).endswith("Python.Runtime.dll:Zone.Identifier"):
            raise PermissionError(13, "Access is denied", str(path))
        return remove(path)

    monkeypatch.setattr(os, "remove", refused)
    assert app._unblock_bundle(root) == [net[1]]
    assert app._unblock_bundle(tmp_path / "nothing here") == []    # missing files are fine


def test_from_source_nothing_is_unblocked(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert app._unblock_bundle() == []


# ------------------------------------------------------------------ WebView2 there, but not starting
class _Window:
    def __init__(self):
        self.destroyed = 0

    def destroy(self):
        self.destroyed += 1


@pytest.fixture
def boxes(monkeypatch):
    """Message boxes and the browser as the guard uses them (nothing pops up): the answer is set by the test."""
    import logging
    import webbrowser

    from studia import osutil

    seen = {"shown": [], "opened": [], "answer": 6, "logged": []}
    monkeypatch.setattr(osutil.impl(), "message_box",
                        lambda text, title="", flags=0: seen["shown"].append((text, flags)) or seen["answer"])
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: seen["opened"].append(url))

    class Keep(logging.Handler):
        def emit(self, record):
            seen["logged"].append(record.getMessage())

    keep = Keep(logging.INFO)
    studia_log = logging.getLogger("studia")
    studia_log.addHandler(keep)
    old_level = studia_log.level
    studia_log.setLevel(logging.INFO)
    yield seen
    studia_log.removeHandler(keep)
    studia_log.setLevel(old_level)


def _wait(cond, seconds=5.0):
    import time

    end = time.monotonic() + seconds
    while not cond() and time.monotonic() < end:
        time.sleep(0.02)
    return cond()


def test_webview2_that_fails_to_start_is_said_once_logged_and_the_empty_window_closes(boxes):
    """WebView2 registered but broken: pywebview logged the failure to a console the app doesn't have, and the
    student had an empty window forever, with nothing in the log."""
    import logging

    window = _Window()
    guard = app._BlankWindowGuard(window).install()
    try:
        pywebview = logging.getLogger("pywebview")
        pywebview.error("WebView2 initialization failed with exception:\n System.Runtime.InteropServices."
                        "COMException (0x8007139F): The group or resource is not in the correct state")
        pywebview.error("WebView2 initialization failed with exception:\n the same, again")
        assert _wait(lambda: window.destroyed)
    finally:
        guard.remove()
    (text, flags), = boxes["shown"]                                  # said once
    assert "didn't start" in text and "Microsoft Edge WebView2 Runtime" in text and "Modify" in text
    assert "0x8007139F" in text and flags & 0xF == 0x4               # the error itself, and Yes/No
    assert boxes["opened"] == [app.WEBVIEW2_PAGE] and window.destroyed == 1
    assert any(m.startswith("pywebview: WebView2 initialization failed") for m in boxes["logged"])


def test_a_page_that_never_loads_is_said_and_waiting_on_is_allowed(boxes, monkeypatch):
    monkeypatch.setattr(app, "LOAD_GIVE_UP_S", 0.2)
    boxes["answer"] = 7                                              # No: keep waiting
    window = _Window()
    guard = app._BlankWindowGuard(window, showing=lambda: True).install()
    try:
        assert _wait(lambda: boxes["shown"])
    finally:
        guard.remove()
    (text, flags), = boxes["shown"]
    assert "still empty after a minute" in text and "keep waiting" in text and window.destroyed == 0
    assert boxes["opened"] == []


@pytest.mark.parametrize("loaded,showing", [(True, True), (False, False)])
def test_a_page_that_loads_or_a_window_the_start_up_window_handles_brings_no_message(boxes, monkeypatch, loaded,
                                                                                     showing):
    import time

    monkeypatch.setattr(app, "LOAD_GIVE_UP_S", 0.2)
    guard = app._BlankWindowGuard(_Window(), showing=lambda: showing).install()
    if loaded:
        guard.page_loaded()
    time.sleep(0.5)
    guard.remove()
    assert boxes["shown"] == []


@pytest.mark.parametrize("error,left,says_unblock", [
    (RuntimeError(r"Failed to resolve Python.Runtime.Loader.Initialize from C:\x\Python.Runtime.dll"), [], True),
    (OSError("Operation is not supported. (0x80131515)"), [], True),
    (RuntimeError("something else"), [Path("Python.Runtime.dll")], True),       # still marked: that's the reason
    (RuntimeError("something else"), [Path("IEUStudia.exe")], False),           # (the exe's mark stops nothing)
    (RuntimeError("something else"), [], False),
])
def test_a_start_windows_blocks_says_how_to_unblock_the_zip(isolated_home, monkeypatch, error, left, says_unblock):
    from studia import osutil

    shown = []
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(app, "_unblock_bundle", lambda root=None: list(left))

    def fail(argv):
        raise error

    monkeypatch.setattr(app, "_main", fail)
    monkeypatch.setattr(osutil.impl(), "message_box", lambda text, title="", flags=0: shown.append(text) or 1)
    app.main([])
    (text,) = shown
    if says_unblock and sys.platform == "win32":
        assert "Unblock" in text and "Properties" in text and "IEUStudia-Setup.exe" in text
        assert "Traceback" not in text
    else:
        assert "IEUStudia could not start:" in text and "Unblock" not in text
