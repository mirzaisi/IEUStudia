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
        _main(args)
    except Exception as exc:  # noqa: BLE001 - last resort: never fail silently under pythonw
        import traceback

        from .osutil import message_box

        log.exception("IEUStudia crashed")
        try:                                     # the folder in use (an earlier name's until it is moved)
            from .paths import logs_dir

            where = str(logs_dir())
        except Exception:  # noqa: BLE001
            where = "%LOCALAPPDATA%\\IEUStudia\\logs"
        _early_language()
        head, tail = t("IEUStudia could not start:"), t("Details are in the log folder ({where}).", where=where)
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


def _other_instance(name: str) -> bool:
    """Another copy holds the single-instance lock. Bring its window (or its start-up window) forward. If it has
    shown nothing for a while it is stuck: offer to replace it. True if this copy should start after all."""
    import time

    from .osutil import (acquire_single_instance, bring_to_front, find_class_window, focus_window, is_hung, kill_tree,
                         message_box, read_instance)

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


def _relaunch(argv: list[str]) -> None:
    """Start a fresh copy of the app (after switching profiles), windowless like this one. It waits for this
    process to exit (the single-instance lock) before it opens its window."""
    import subprocess

    from .launch import app_command

    args = [a for a in argv if a not in ("--restarted", "--recovered")] + ["--restarted"]
    try:
        subprocess.Popen(app_command(*args), close_fds=True,
                         creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        log.info("Restarting for another profile")
    except OSError:
        log.exception("Could not restart IEUStudia")


def _main(argv: Optional[list[str]] = None) -> None:
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

    def on_loaded() -> None:
        _stop_watchdog(watchdog)
        log.info("UI loaded %.1f s after launch", process_age())

    def on_closing() -> bool:     # runs on the UI thread
        services = started.get("services")
        if api._restart or api._update_installer:     # switching profiles or updating: the user already confirmed
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
    # Qt's developer tools stop pywebview's first call into the page from returning: debug logs only on Linux
    webview.start(gui="edgechromium" if WINDOWS else "qt", debug=debug and WINDOWS, private_mode=False,
                  storage_path=str(app_dir() / "webview"), icon=None if WINDOWS else str(ASSETS / "icon.png"))
    _stop_watchdog(watchdog)
    if api._notifier is not None:                # its icon next to the clock goes with the window
        api._notifier.close()
    if started.get("services") is not None:
        started["services"].shutdown()
    log.info("IEUStudia closed")
    if api._restart:
        _relaunch(argv)
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


if __name__ == "__main__":
    main()
