"""Desktop window (pywebview + WebView2) hosting the web UI.

Start-up order, for a window that appears at once: the window opens first (the page shows its start-up
screen), while the app's services start on a background thread; the UI's first call waits for them.

Threading rule: pywebview runs event handlers (shown, loaded, …) on worker threads. They must not touch
WinForms objects (``window.native``): pythonnet keeps the GIL during such calls while WinForms waits for
the UI thread, which in turn waits for the GIL — the window then freezes ("Not Responding").
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

from . import APP_NAME, __version__
from .i18n import t
from .osutil import acquire_single_instance, focus_window, set_app_id

ASSETS = Path(__file__).parent / "assets"
WINDOWS = sys.platform == "win32"
WEB = Path(__file__).parent / "web"
HANG_AFTER_S = 45
log = logging.getLogger("studia")
# the .NET assemblies the window needs, in a release build's _internal folder: pythonnet's (clr) and WebView2's
NET_ASSEMBLIES = ("pythonnet/runtime/*.dll", "clr_loader/ffi/dlls/*/*.dll", "webview/lib/*.dll",
                  "webview/lib/runtimes/*/native/*.dll")
_unblocked: list[str] = []          # (for the log, once it is set up)


def _unblock_bundle(root: Optional[Path] = None) -> list[Path]:
    """A portable copy unzipped with Windows' Extract All keeps the zip's "came from the internet" mark on its files,
    and .NET refuses to load a marked assembly (pythonnet's, WebView2's): the window could never open ("Failed to
    resolve Python.Runtime.Loader.Initialize"). The mark is a small stream beside the file (``file:Zone.Identifier``);
    removing it is what Properties → Unblock does. Only these few files and IEUStudia.exe (marked, it brings a
    security question at every sign-in): nothing else checks the mark. ``root``: the folder of IEUStudia.exe, by
    default this release build's (nothing to do from source). Returns the files still marked: a folder IEUStudia may
    not change (the start then explains how to unblock the zip)."""
    if root is None:
        if not (WINDOWS and getattr(sys, "frozen", False)):
            return []
        root, exe = Path(sys.executable).parent, Path(sys.executable)
    else:
        exe = root / "IEUStudia.exe"
    left = []
    for f in [exe] + [p for pattern in NET_ASSEMBLIES for p in sorted((root / "_internal").glob(pattern))]:
        try:
            os.remove(f"{f}:Zone.Identifier")
            _unblocked.append(f.name)
        except PermissionError:
            left.append(f)
        except OSError:              # not marked (also a drive that can't hold the mark, such as a FAT USB stick)
            continue
    return left


LOAD_GIVE_UP_S = 60                 # the page normally loads in a second or two after the window shows
WEBVIEW2_PAGE = "https://developer.microsoft.com/microsoft-edge/webview2/#download"


class _BlankWindowGuard(logging.Handler):
    """The window stays empty when WebView2 is there but doesn't start (damaged by a "debloat" tool, blocked by
    security software, its data folder can't be made): pywebview only logs that, to a console the windowed app
    doesn't have. Here its warnings and errors go to IEUStudia's log, and a failed start, or a page still not loaded
    LOAD_GIVE_UP_S after the start while the window shows and answers, is said in a message with what to do.
    (A window that never shows, or stops answering, is the start-up window's to handle: splash.py.)"""

    def __init__(self, window: Any, showing: Any = lambda: True) -> None:
        super().__init__(logging.WARNING)
        self.window, self.showing = window, showing
        self.loaded, self.told = threading.Event(), threading.Event()
        self.timer = threading.Timer(LOAD_GIVE_UP_S, self._not_loaded)
        self.timer.daemon = True

    def install(self) -> "_BlankWindowGuard":
        logging.getLogger("pywebview").addHandler(self)
        self.timer.start()
        return self

    def remove(self) -> None:
        self.timer.cancel()
        logging.getLogger("pywebview").removeHandler(self)

    def page_loaded(self) -> None:
        self.loaded.set()
        self.timer.cancel()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            text = record.getMessage()
        except Exception:  # noqa: BLE001
            text = str(record.msg)
        log.log(record.levelno, "pywebview: %s", text, exc_info=record.exc_info)
        if "WebView2 initialization failed" in text and not self.loaded.is_set():
            self._tell(text, failed=True)

    def _not_loaded(self) -> None:
        if self.loaded.is_set() or not self.showing():
            return
        log.warning("The window shows, but its page hasn't loaded %d s after the start", LOAD_GIVE_UP_S)
        self._tell("", failed=False)

    def _tell(self, detail: str, failed: bool) -> None:
        if self.told.is_set():
            return
        self.told.set()
        threading.Thread(target=self._ask, args=(detail, failed), name="blank-window", daemon=True).start()

    def _ask(self, detail: str, failed: bool) -> None:
        from .osutil import message_box

        try:
            from .paths import logs_dir

            where = str(logs_dir())
        except Exception:  # noqa: BLE001
            where = "%LOCALAPPDATA%\\IEUStudia\\logs"
        head = (t("IEUStudia's window stays empty: Microsoft Edge WebView2, which shows its pages, didn't start on "
                  "this PC.") if failed else
                t("IEUStudia's window is still empty after a minute: Microsoft Edge WebView2, which shows its pages, "
                  "isn't answering."))
        fix = t("To fix it: restart the PC and open IEUStudia again. If the window stays empty, repair WebView2 in "
                "Windows Settings → Apps → Installed apps → Microsoft Edge WebView2 Runtime → Modify, or install it "
                "again from Microsoft's page. A security program or a PC cleaner can also block it: allow "
                "msedgewebview2.exe there.")
        more = t("Details are in the log folder ({where}).", where=where) + (f"\n{detail[:600]}" if detail else "")
        ask = (t("Open Microsoft's WebView2 page now? IEUStudia closes either way.") if failed else
               t("Close IEUStudia now? (No: keep waiting.)"))
        flags = 0x4 | (0x10 if failed else 0x30) | 0x10000 | 0x40000      # Yes/No, its icon, in front of the window
        yes = message_box(f"{head}\n\n{fix}\n\n{more}\n\n{ask}", APP_NAME, flags) == 6
        if failed and yes:
            import webbrowser

            webbrowser.open(WEBVIEW2_PAGE)
        if (failed or yes) and not self.loaded.is_set():
            log.info("Closing the empty window")
            try:
                self.window.destroy()
            except Exception:  # noqa: BLE001
                log.debug("could not close the window", exc_info=True)


def _blocked_start(exc: BaseException, left: list[Path]) -> bool:
    """The start failed because Windows' internet mark is still on the .NET files (see _unblock_bundle; the mark on
    IEUStudia.exe itself doesn't stop a start)."""
    text = f"{exc!r} {exc}"
    return any(f.suffix.lower() == ".dll" for f in left) or any(
        s in text for s in ("Python.Runtime.Loader.Initialize", "0x80131515", "Operation is not supported"))


def _early_language() -> None:
    """The language for a message shown before start-up has set it (or after a crash): the choice in Settings, else
    Windows' language; English if even that can't be read."""
    from . import i18n

    try:
        i18n.load_language()
    except Exception:  # noqa: BLE001 - say it in English rather than not at all
        log.debug("language not loaded", exc_info=True)


def _on_shown(api: Any, instance: str = "") -> None:
    """Window handle (taskbar flash) and icon, with plain Win32 calls only (see the threading rule)."""
    from .osutil import mark_instance, own_main_window, set_window_icon

    if instance:
        mark_instance(instance, "shown")
    hwnd = 0
    for _ in range(40):
        hwnd = own_main_window()
        if hwnd:
            break
        time.sleep(0.05)
    api._hwnd = hwnd
    from .osutil import process_age

    log.info("Window shown %.1f s after launch", process_age())
    ico = ASSETS / "icon.ico"
    if hwnd and ico.exists():
        set_window_icon(hwnd, ico)


def _start_watchdog(folder: Path) -> Optional[Any]:
    """If the page has not loaded after HANG_AFTER_S, write every thread's stack to startup-hang.txt."""
    import faulthandler

    try:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "startup-hang.txt"
        if path.exists() and path.stat().st_size:           # keep the last report for diagnosis
            os.replace(path, folder / "startup-hang-previous.txt")
        f = open(path, "w", encoding="utf-8")                # noqa: SIM115 - faulthandler writes to it later
        faulthandler.dump_traceback_later(HANG_AFTER_S, exit=False, file=f)
        return f
    except OSError:
        return None


def _stop_watchdog(f: Optional[Any]) -> None:
    import faulthandler

    faulthandler.cancel_dump_traceback_later()
    if f is None or f.closed:
        return
    name = f.name
    f.close()
    try:
        if os.path.getsize(name) == 0:
            os.remove(name)
    except OSError:
        pass


def main(argv: Optional[list[str]] = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["--splash"]:                 # a release build: the start-up window is this exe too
        from .splash import main as splash_main

        sys.exit(splash_main(args[1:]))
    left = _unblock_bundle()                     # before anything loads .NET (the window, the self-test)
    if "--agent" in args or args[:1] == ["--selftest"]:
        from .net import use_windows_certificates

        use_windows_certificates()               # before any connection is made (the window: in _main)
    if "--agent" in args:                        # the tray helper (no window)
        from .agent import main as agent_main

        agent_main(args)
        return
    if args[:1] == ["--selftest"]:               # checks a release build: imports and bundled files, no window
        sys.exit(_selftest(args[1] if len(args) > 1 else ""))
    try:
        if _main(args) in ("restart", "closed"):
            _end_now()
    except Exception as exc:  # noqa: BLE001 - last resort: never fail silently under pythonw
        import traceback

        from .osutil import message_box

        log.exception("IEUStudia crashed")
        try:                                     # the start-up window just closes: this message says why
            from .osutil import instance_name, mark_instance

            mark_instance(instance_name(), "crashed")
        except Exception:  # noqa: BLE001
            pass
        try:                                     # the folder in use (an earlier name's until it is moved)
            from .paths import logs_dir

            where = str(logs_dir())
        except Exception:  # noqa: BLE001
            where = "%LOCALAPPDATA%\\IEUStudia\\logs"
        _early_language()
        head, tail = t("IEUStudia could not start:"), t("Details are in the log folder ({where}).", where=where)
        if WINDOWS and getattr(sys, "frozen", False) and _blocked_start(exc, left):    # a portable zip, still marked
            message_box(t("IEUStudia could not start: Windows blocked some of its files because the zip it came in "
                          "was downloaded from the internet.") + "\n\n"
                        + t("To fix it: delete this IEUStudia folder, right-click the downloaded zip file, choose "
                            "Properties, tick Unblock at the bottom and click OK, then extract it again. Or use the "
                            "installer (IEUStudia-Setup.exe) from the same release page instead: it needs none of "
                            "this.") + f"\n\n{tail}", APP_NAME, 0x10)
            return
        message_box(f"{head}\n\n{exc}\n\n{traceback.format_exc(limit=3)}\n{tail}", APP_NAME, 0x10)


def _selftest(out: str) -> int:
    """Import everything the app needs and check its bundled files; write the result as JSON to ``out``."""
    import importlib
    import json

    report: dict[str, Any] = {"version": __version__, "frozen": bool(getattr(sys, "frozen", False)), "ok": True,
                              "modules": {}, "files": {}}
    gui = ("webview.platforms.winforms", "studia.tray", "studia.splash") if WINDOWS else \
        ("webview.platforms.qt", "studia.linuxutil", "studia.linux_secrets")
    for mod in ("numpy", "requests", "av", "ctranslate2", "faster_whisper", "onnxruntime", "tokenizers", "pypdfium2",
                "yt_dlp", "truststore", "webview", "studia.services", "studia.agent", "studia.local_llm", *gui):
        try:
            m = importlib.import_module(mod)
            report["modules"][mod] = str(getattr(m, "__version__", "ok"))
        except Exception as exc:  # noqa: BLE001
            report["modules"][mod] = f"FAILED: {exc!r}"
            report["ok"] = False
    files = ["web/index.html", "web/app.js", "web/setup.js", "web/styles.css", "web/math.js", "web/vendor/katex/katex.min.js",
             "assets/icon.ico", "assets/logo.svg"]
    if WINDOWS and not report["frozen"]:
        files.append("splash.py")               # a release runs the start-up window from the exe itself
    for rel in files:
        p = Path(__file__).parent / rel
        report["files"][rel] = p.is_file()
        report["ok"] = report["ok"] and p.is_file()
    try:
        import faster_whisper

        vad = Path(faster_whisper.__file__).parent / "assets" / "silero_vad_v6.onnx"
        report["files"]["faster_whisper/assets/silero_vad_v6.onnx"] = vad.is_file()
        report["ok"] = report["ok"] and vad.is_file()
    except Exception:  # noqa: BLE001
        report["ok"] = False
    text = json.dumps(report, indent=1)
    if out:
        Path(out).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0 if report["ok"] else 1


def _windowless_python() -> str:
    from .launch import windowless_python

    return windowless_python()


def _start_splash(dark: bool, recovered: bool, marker: Optional[Path] = None, logs: Optional[Path] = None,
                  lang: str = "en") -> None:
    """The native start-up window (its own process, so it shows and animates at once, whatever this one is busy
    with); it closes itself when the real window appears. ``marker``: the instance file where start-up notes its
    phase; ``logs``: where a stuck start leaves its diagnostics. Windows only (it is plain Win32)."""
    import subprocess

    if not WINDOWS:
        return

    from .launch import app_command, splash_command

    restart = app_command()          # how the start-up window starts a fresh copy if this one gets stuck
    try:
        subprocess.Popen(splash_command(str(os.getpid()), "dark" if dark else "light", "--python", restart[0],
                                        *(["--marker", str(marker)] if marker else []),
                                        *(["--logs", str(logs)] if logs else []),
                                        *(["--recovered"] if recovered else []), "--lang", lang), close_fds=True)
    except OSError:
        log.debug("no start-up window", exc_info=True)


def _webview2_missing() -> bool:
    """Without the WebView2 runtime the window cannot show: say so (with the download) instead of failing."""
    if not WINDOWS:
        return False                             # Linux uses Qt WebEngine, a dependency of the package
    from .hardware import webview2_version

    if webview2_version():
        return False
    from .osutil import message_box

    _early_language()
    if message_box(t("IEUStudia needs the Microsoft Edge WebView2 Runtime, which is not installed on this PC.")
                   + "\n\n" + t("It is free and comes with Windows 11 and current Windows 10. Open the download page "
                                "now?"), APP_NAME, 0x4 | 0x30) == 6:
        import webbrowser

        webbrowser.open("https://developer.microsoft.com/microsoft-edge/webview2/#download")
    return True


def _note_window_creation(webview: Any, instance: str) -> None:
    """Mark the moment pywebview starts creating the WinForms window (after .NET has loaded). Creating it has
    been seen to hang inside Windows (CreateWindowEx waiting on an ALPC reply); the start-up window restarts
    the app when the window still isn't there well after this mark."""
    from .osutil import mark_instance

    init = getattr(webview, "initialize", None)
    if init is None or getattr(init, "_studia_marks", False):
        return

    def initialize(*args: Any, **kwargs: Any) -> Any:
        lib = init(*args, **kwargs)
        create = getattr(lib, "create_window", None)
        if create is not None and not getattr(create, "_studia_marks", False):
            def create_window(window: Any) -> Any:
                if getattr(window, "uid", "") == "master":
                    mark_instance(instance, "window")
                return create(window)

            create_window._studia_marks = True    # type: ignore[attr-defined]
            lib.create_window = create_window
        return lib

    initialize._studia_marks = True                # type: ignore[attr-defined]
    webview.initialize = initialize


# WebView2 asks a page's permission questions (reading the clipboard, the camera, notifications…) in a bar of its own,
# in the browser's words and style. IEUStudia answers them itself instead, so that bar never shows: its own page may
# read the clipboard and play sound; everything else it doesn't use is turned down, and so is anything a page that
# isn't IEUStudia's asks for (the window shows no other: links open in the browser).
PAGE_ALLOWS = frozenset({"ClipboardRead", "Autoplay"})


def permission_answer(uri: str, kind: str) -> bool:
    """Whether IEUStudia's window allows a permission it was asked for: ``uri``, the page asking (IEUStudia's own is a
    file: page, see _main); ``kind``, the name of WebView2's CoreWebView2PermissionKind."""
    return (uri or "").lower().startswith("file:") and kind in PAGE_ALLOWS


def _on_permission(sender: Any, args: Any) -> None:
    """WebView2's PermissionRequested (on the window's thread): answered at once, so its own question never shows."""
    try:
        from Microsoft.Web.WebView2.Core import CoreWebView2PermissionState  # type: ignore[import-not-found]

        kind = args.PermissionKind
        kind = str(kind.ToString() if hasattr(kind, "ToString") else kind)
        uri = str(args.Uri or "")
        allow = permission_answer(uri, kind)
        args.State = CoreWebView2PermissionState.Allow if allow else CoreWebView2PermissionState.Deny
        try:
            args.SavesInProfile = False          # asked again next time: this answer stays the app's, never stored
        except Exception:  # noqa: BLE001 - an older WebView2: the answer counts all the same
            pass
        log.info("WebView2 asked for %s (%s): %s", kind, uri[:60], "allowed" if allow else "turned down")
    except Exception:  # noqa: BLE001 - left to WebView2 then
        log.debug("A permission request could not be answered", exc_info=True)


def _answer_permissions(webview: Any) -> None:
    """Once WebView2 has started in the window, its permission questions go to _on_permission. Hooked in where
    pywebview gets WebView2 ready (its window's thread, where WebView2 raises its events too), before the window is
    made: webview.initialize loads that part of pywebview."""
    init = getattr(webview, "initialize", None)
    if init is None or getattr(init, "_studia_permissions", False):
        return

    def initialize(*args: Any, **kwargs: Any) -> Any:
        lib = init(*args, **kwargs)
        chrome = getattr(sys.modules.get("webview.platforms.edgechromium"), "EdgeChrome", None)
        ready = getattr(chrome, "on_webview_ready", None)
        if ready is not None and not getattr(ready, "_studia_permissions", False):
            def on_webview_ready(self: Any, sender: Any, event: Any) -> Any:
                out = ready(self, sender, event)
                try:
                    if event.IsSuccess:
                        sender.CoreWebView2.PermissionRequested += _on_permission
                except Exception:  # noqa: BLE001 - WebView2 shows its own questions then
                    log.debug("Permission requests not hooked", exc_info=True)
                return out

            on_webview_ready._studia_permissions = True    # type: ignore[attr-defined]
            chrome.on_webview_ready = on_webview_ready
        return lib

    initialize._studia_permissions = True          # type: ignore[attr-defined]
    webview.initialize = initialize


def _other_instance(name: str) -> bool:
    """Another copy holds the single-instance lock. Bring its window (or its start-up window) forward. If it has
    shown nothing for a while it is stuck: offer to replace it. True if this copy should start after all."""
    import time

    from .osutil import (acquire_single_instance, bring_to_front, find_class_window, focus_window, is_hung, kill_tree,
                         message_box, read_instance)

    if read_instance(name).get("phase") == "closing" and acquire_single_instance(name, wait_s=20):
        return True                           # its window was closed a moment ago and it has ended now: start
    deadline = time.monotonic() + 3          # a double-click starts two copies at once: let the first one show up
    while True:
        if focus_window(APP_NAME):
            return False
        splash = find_class_window("StudiaSplash")
        if splash:
            bring_to_front(splash)
            return False
        if time.monotonic() > deadline:
            break
        time.sleep(0.1)
    info = read_instance(name)
    if time.time() - float(info.get("started") or 0) < 60:
        return False                          # still starting; its own start-up window handles a hang
    _early_language()
    if message_box(t("IEUStudia is already running, but its window is not showing and it does not respond.") + "\n\n"
                   + t("Close that copy and start IEUStudia again?"), APP_NAME, 0x4 | 0x30) != 6:   # MB_YESNO, IDYES
        return False
    if info.get("pid"):
        kill_tree(int(info["pid"]))
    return acquire_single_instance(name, wait_s=15)


def _window_size() -> tuple[int, int, bool, Optional[int], Optional[int]]:
    """(width, height, maximized, x, y): 1280 x 820 in the middle of the screen's usable area, or maximized where that
    doesn't fit (a 1366 x 768 laptop, or 1920 x 1080 at 150 %), so the buttons at the bottom of the setup and the queue
    bar are never under the taskbar. (Windows would otherwise place it a little further down at every start.)"""
    from .osutil import work_area

    try:
        left, top, w, h = work_area()
    except Exception:  # noqa: BLE001
        left = top = w = h = 0.0
    if not (w and h):
        return 1280, 820, False, None, None
    width, height = int(min(1280, w - 16)), int(min(820, h - 16))
    return (width, height, (width, height) != (1280, 820),
            int(left + (w - width) / 2), int(top + (h - height) / 2))


def _relaunch(argv: list[str]) -> bool:
    """Start a fresh copy of the app (after switching profiles), windowless like this one. It waits for this
    process to exit (the single-instance lock) before it opens its window. False: it couldn't be started."""
    import subprocess

    from .launch import app_command

    args = [a for a in argv if a not in ("--restarted", "--recovered")] + ["--restarted"]
    try:
        subprocess.Popen(app_command(*args), close_fds=True,
                         creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        log.info("Restarting for another profile")
        return True
    except OSError:
        log.exception("Could not restart IEUStudia")
        return False


def _end_now() -> None:
    """End this copy at once, once its window has closed. Python would first wait for every thread pool's queued work
    (a sync's course listings, the parts of a download: minutes on slow Wi-Fi), even work that background threads
    started, and this copy holds the single-instance lock until it ends: the next copy of a profile switch waits for
    it with nothing on screen and gives up after 30 s, and opening IEUStudia again said it was "running but not
    responding". Nothing is lost by not waiting: downloads go on from their parts, the library's writes are on disk
    (SQLite), files are written whole or not at all, and the lectures still to transcribe were kept for the next
    start. The log's last lines are written first."""
    if os.environ.get("PYTEST_CURRENT_TEST"):    # a test that went through main(): it must not end the test run
        raise RuntimeError("app._end_now() would end the test run: patch it in the test")
    try:
        logging.shutdown()
    finally:
        os._exit(0)


def _main(argv: Optional[list[str]] = None) -> Optional[str]:
    """The app's window, until it closes. "restart": a profile switch started the next copy; "closed": the window was
    closed (main() ends this copy at once after either, see _end_now); None: it didn't open, or an update runs."""
    argv = list(sys.argv[1:] if argv is None else argv)
    debug = "--debug" in argv
    from .osutil import instance_name, write_instance

    name = instance_name()
    target = argv[argv.index("--open") + 1] if "--open" in argv[:-1] else ""
    if not acquire_single_instance(name, wait_s=30 if "--restarted" in argv else 0):
        if target:                               # e.g. a notification was clicked: the open window shows it
            from .osutil import request_open

            request_open(name, target)
        if not _other_instance(name):
            return
    if _webview2_missing():
        return
    write_instance(name)
    set_app_id()
    from . import appconfig, i18n
    from .accounts import activate
    from .config import load_settings
    from .log import setup_logging
    from .paths import app_dir, logs_dir, migrate_legacy_data
    from .osutil import process_age, windows_dark_mode

    moved = migrate_legacy_data()            # before anything opens the data folder
    appconfig.migrate()                      # the AI choice as an earlier version stored it
    i18n.load_language()                     # before anything makes a message (threads, start-up window, tray)
    account = activate()                     # whose library, settings and password this run uses
    theme = load_settings().theme
    dark = theme == "dark" or (theme == "system" and windows_dark_mode())
    from .osutil import _instance_file

    _start_splash(dark, "--recovered" in argv, _instance_file(name), logs_dir(), i18n.language())   # on screen at once
    # HTTPS trusts Windows' certificates from here on: before any connection is made (nothing above connects), and
    # not earlier, as it loads requests and the SSL setup (~0.2 s) that the start-up window shouldn't wait for
    from .net import use_windows_certificates

    use_windows_certificates()
    setup_logging(debug)
    log.info("IEUStudia %s starting (python %s), profile %s", __version__, sys.version.split()[0], account.id)
    if _unblocked:
        log.info("Removed Windows' internet mark from %s (a portable copy from Extract All)", ", ".join(_unblocked))
    if "--recovered" in argv:
        log.warning("Restarted by the start-up window after a stuck start (details: startup-stall.txt here)")
    if moved != "none":
        log.info("Data folder of the app's earlier name: %s", moved)

    def thread_error(args: threading.ExceptHookArgs) -> None:   # pythonw has no console: keep a trace
        log.error("Unhandled error in thread %s", getattr(args.thread, "name", "?"),
                  exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    threading.excepthook = thread_error
    import webview

    from .api import Api

    log.info("Modules loaded %.1f s after launch", process_age())
    api = Api()
    api._open_target = target
    width, height, maximized, x, y = _window_size()
    # file:// (not pywebview's http server) so the page may show slide images stored on disk
    window = webview.create_window(APP_NAME, url=(WEB / "index.html").as_uri(), js_api=api, width=width, height=height,
                                   x=x, y=y, min_size=(min(900, width), min(560, height)), maximized=maximized,
                                   background_color="#0e1016" if dark else "#f5f6fa", text_select=True)
    api._attach(window)
    started: dict[str, Any] = {}

    def start_services() -> None:
        try:
            from .services import Services

            services = Services(debug=debug)
        except Exception as exc:  # noqa: BLE001 - shown on the start-up screen
            log.exception("Services failed to start")
            api._startup_failed(t("IEUStudia could not open its data folder or library: {error}", error=exc))
            return
        started["services"] = services
        api._set_services(services)
        log.info("Services ready %.1f s after launch", process_age())
        try:                                     # the tray helper, if the user wants it running
            from .agent import ensure_running

            ensure_running()
        except Exception:  # noqa: BLE001
            log.debug("helper start failed", exc_info=True)

    threading.Thread(target=start_services, name="services-start", daemon=True).start()
    watchdog = _start_watchdog(logs_dir())

    def showing() -> bool:                       # the window is there and answers (else the start-up window acts)
        from .osutil import is_hung

        return bool(api._hwnd) and not is_hung(api._hwnd)

    blank = _BlankWindowGuard(window, showing).install() if WINDOWS else None   # WebView2 that doesn't start

    def on_loaded() -> None:
        if blank is not None:
            blank.page_loaded()
        _stop_watchdog(watchdog)
        log.info("UI loaded %.1f s after launch", process_age())

    def on_closing() -> bool:     # runs on the UI thread
        services = started.get("services")
        if api._restart or api._update_installer:     # switching profiles or updating: the user already confirmed
            if api._setup_cancel is not None:         # the setup's download stops (next time it goes on from there)
                api._setup_cancel.set()
            return True
        # every question first: whoever keeps the window open at the second one has nothing cancelled
        setup = api._setup_running
        if setup and not api._close_ok and not window.create_confirmation_dialog(     # (asked before the page did)
                t("Download in progress"), t("IEUStudia is still downloading what it needs. Quit anyway? Next time "
                                             "the download continues where it stopped.")):
            return False
        busy = services is not None and services.jobs.busy()
        if busy and not api._close_ok and services.settings.ask_before_closing:
            if api._ask_close():                  # the page asks, with "Don't ask again" (web/failsafe.js)
                return False
            if not window.create_confirmation_dialog(
                    t("Transcription in progress"), t("IEUStudia is still transcribing. Quit anyway? Next time, press "
                                                      "Continue on the home page: the lectures go on where they "
                                                      "stopped.")):
                return False
        if setup and api._setup_cancel is not None:
            api._setup_cancel.set()
        if busy:
            services.jobs.shutdown()
        return True

    window.events.shown += lambda: _on_shown(api, name)
    window.events.loaded += on_loaded
    window.events.closing += on_closing
    _note_window_creation(webview, name)
    if WINDOWS:
        _answer_permissions(webview)             # WebView2's permission questions: answered by IEUStudia itself
    # Qt's developer tools stop pywebview's first call into the page from returning: debug logs only on Linux
    try:
        webview.start(gui="edgechromium" if WINDOWS else "qt", debug=debug and WINDOWS, private_mode=False,
                      storage_path=str(app_dir() / "webview"), icon=None if WINDOWS else str(ASSETS / "icon.png"))
    finally:
        if blank is not None:
            blank.remove()
    _stop_watchdog(watchdog)
    try:                                         # a start meanwhile waits for this copy instead of calling it stuck
        from .osutil import mark_instance

        mark_instance(name, "closing")
    except Exception:  # noqa: BLE001 - only a note for the next start
        log.debug("marking the closing failed", exc_info=True)
    if api._notifier is not None:                # its icon next to the clock goes with the window
        api._notifier.close()
    if started.get("services") is not None:
        started["services"].shutdown()
    log.info("IEUStudia closed")
    if api._restart:
        if _relaunch(argv):
            return "restart"                     # main() ends this copy at once (see _end_now)
        from .osutil import message_box

        message_box(t("IEUStudia couldn't open again by itself after switching profiles. Open it from the Start menu: "
                      "it opens the profile you chose.") if WINDOWS else
                    t("IEUStudia couldn't open again by itself after switching profiles. Start it again: it opens the "
                      "profile you chose."), APP_NAME, 0x30)
    elif api._update_installer:                  # its installer replaces this copy, then opens the new one
        from . import updates
        from .osutil import release_single_instance

        release_single_instance()
        if not updates.run_installer(api._update_installer):       # blocked by an antivirus, say
            from .osutil import message_box

            message_box(t("IEUStudia couldn't start the update: Windows or your antivirus didn't let the installer "
                          "run. IEUStudia opens again. You can also download the new version from its release page."),
                        APP_NAME, 0x30)
            _relaunch(argv)
        return None
    # closed: ended at once too (main), not after a sync's or a download's queued work, which held the single-instance
    # lock for minutes: opening IEUStudia again meanwhile said it was "running but not responding"
    return "closed"


if __name__ == "__main__":
    main()
