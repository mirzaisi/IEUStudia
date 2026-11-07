"""How to start IEUStudia's processes: the app window, the tray helper (``--agent``) and the start-up window
(``--splash``), both for an installed release (one ``IEUStudia.exe``, built with PyInstaller) and for a source
install (``pythonw -m studia.app``). Also the "start with Windows" registry entry."""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
# Windows' own switch for each Run entry (Task Manager's and Settings' "Startup apps"): a binary value whose first byte
# is even (2) when it may start and odd (3) when it was turned off there; no value means on. Off, Windows skips the
# Run entry at sign-in, whatever it says.
APPROVED_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
RUN_VALUE = "IEUStudia"
DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
log = logging.getLogger("studia")
_told_off = False


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def windowless_python() -> str:
    exe = Path(sys.executable)
    if exe.name.lower() == "python.exe" and (exe.parent / "pythonw.exe").exists():
        exe = exe.parent / "pythonw.exe"
    return str(exe)


def app_command(*args: str) -> list[str]:
    """The app (or, with ``--agent``, the tray helper) as a new process without a console window."""
    if frozen():
        return [sys.executable, *args]
    return [windowless_python(), "-m", "studia.app", *args]


def splash_command(*args: str) -> list[str]:
    """The start-up window: in a release the same exe (``--splash``), from source the base interpreter in isolated
    mode, which starts in a blink."""
    if frozen():
        return [sys.executable, "--splash", *args, "--frozen"]
    base = Path(getattr(sys, "_base_executable", "") or sys.executable)
    exe = base.with_name("pythonw.exe") if base.with_name("pythonw.exe").exists() else Path(windowless_python())
    return [str(exe), "-I", "-S", str(Path(__file__).with_name("splash.py")), *args]


def spawn(cmd: list[str]) -> bool:
    from .osutil import normal_priority

    try:
        with normal_priority():          # not the lower priority the app has while it transcribes
            subprocess.Popen(cmd, close_fds=True, creationflags=DETACHED, start_new_session=sys.platform != "win32")
        return True
    except OSError:
        return False


# ----------------------------------------------------------------------------- start with Windows
def _quote(parts: list[str]) -> str:
    return subprocess.list2cmdline(parts)


def _turned_off_in_windows() -> bool:
    """IEUStudia's switch in Windows' Startup apps (Task Manager, Settings → Apps → Startup) is off."""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, APPROVED_KEY) as k:
            data = winreg.QueryValueEx(k, RUN_VALUE)[0]
    except OSError:
        return False
    return isinstance(data, (bytes, bytearray)) and len(data) > 0 and bool(data[0] & 1)


def startup_enabled() -> bool:
    """Does Windows start the tray helper at sign-in: the Run entry is there and Windows' own switch for it is on."""
    global _told_off
    if sys.platform != "win32":
        from .linuxutil import startup_enabled

        return startup_enabled()
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            on = bool(winreg.QueryValueEx(k, RUN_VALUE)[0])
    except OSError:
        return False
    if on and _turned_off_in_windows():
        if not _told_off:
            _told_off = True
            log.info("Start with Windows is turned off in Windows' Startup apps (Task Manager): Windows skips it")
        return False
    return on


def set_startup(on: bool) -> bool:
    """Start the tray helper when you sign in to Windows (HKCU Run key: no admin rights needed). Asked for in
    IEUStudia, it is also switched on again in Windows' Startup apps, or Windows would still skip it. True when it is
    now as asked; Windows or an antivirus may refuse the change (an unknown program starting by itself is what they
    watch for): that is logged and False, never an error that would stop the setup."""
    if sys.platform != "win32":
        from .linuxutil import set_startup as linux_startup

        try:
            linux_startup(on, app_command("--agent", "--at-login"))
        except OSError as exc:
            log.warning("Could not change the start at login (%s)", exc)
            return False
        return True
    import winreg

    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            if on:
                winreg.SetValueEx(k, RUN_VALUE, 0, winreg.REG_SZ, _quote(app_command("--agent", "--at-login")))
            else:
                try:
                    winreg.DeleteValue(k, RUN_VALUE)
                except FileNotFoundError:
                    pass
    except OSError as exc:
        log.warning("Windows did not let IEUStudia %s starting with Windows (%s)", "turn on" if on else "turn off", exc)
        return False
    if on and _turned_off_in_windows():
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, APPROVED_KEY, 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, RUN_VALUE)
            log.info("Start with Windows: turned on again in Windows' Startup apps too")
        except OSError as exc:
            log.warning("Could not turn IEUStudia on in Windows' Startup apps (%s)", exc)
            return False
    return startup_enabled() == on
