"""Small Windows helpers (clipboard, single instance, focus) via ctypes."""

from __future__ import annotations

import contextlib
import ctypes
import ctypes.wintypes as wt
import logging
import os
import queue
import subprocess
import threading
from pathlib import Path
from typing import Iterable, Iterator

log = logging.getLogger("studia")

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
ERROR_ALREADY_EXISTS = 183


def set_clipboard(text: str) -> bool:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GlobalAlloc.restype = wt.HGLOBAL
    kernel32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [wt.HGLOBAL]
    kernel32.GlobalUnlock.argtypes = [wt.HGLOBAL]
    user32.SetClipboardData.argtypes = [wt.UINT, wt.HANDLE]
    data = (text or "").encode("utf-16-le") + b"\x00\x00"
    for _ in range(10):
        if user32.OpenClipboard(None):
            break
        kernel32.Sleep(50)
    else:
        return False
    try:
        user32.EmptyClipboard()
        h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        p = kernel32.GlobalLock(h)
        ctypes.memmove(p, data, len(data))
        kernel32.GlobalUnlock(h)
        return bool(user32.SetClipboardData(CF_UNICODETEXT, h))
    finally:
        user32.CloseClipboard()


_mutex = None


def instance_name() -> str:
    """The single-instance lock's name; a separate data folder (``STUDIA_HOME``) is a separate instance."""
    import hashlib

    home = os.environ.get("STUDIA_HOME", "").strip()
    return "Local\\Studia-SingleInstance" + ("-" + hashlib.sha1(home.lower().encode()).hexdigest()[:8] if home else "")


def _instance_file(name: str) -> Path:
    import tempfile

    return Path(tempfile.gettempdir()) / (name.split("\\")[-1] + ".json")


def write_instance(name: str) -> None:
    """Remember which process holds the lock (so a later click can replace it if it got stuck)."""
    import json
    import time

    try:
        _instance_file(name).write_text(json.dumps({"pid": os.getpid(), "started": time.time()}), encoding="utf-8")
    except OSError:
        pass


def read_instance(name: str) -> dict:
    import json

    try:
        d = json.loads(_instance_file(name).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def request_open(name: str, target: str) -> None:
    """Ask the running app window to show a page ("course:<id>", "home"): the page goes in a file, then the event
    tells the window to look (see open_request_event)."""
    import json
    import time

    try:
        _instance_file(name).with_name(name.split("\\")[-1] + "-open.json").write_text(
            json.dumps({"target": target, "at": time.time()}), encoding="utf-8")
    except OSError:
        return
    event = open_request_event(name)
    if event:
        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.SetEvent.argtypes = kernel32.CloseHandle.argtypes = [wt.HANDLE]
        kernel32.SetEvent(event)
        kernel32.CloseHandle(event)


def open_request_event(name: str) -> int:
    """The named auto-reset event the app window's thread waits on for open requests (0 if Windows says no). When
    no window waits, this makes a new one that goes away with its handle: the request stays in its file."""
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.CreateEventW.restype = wt.HANDLE
    kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.BOOL, wt.LPCWSTR]
    return kernel32.CreateEventW(None, False, False, name.replace("SingleInstance", "Open")) or 0


def wait_event(event: int, timeout_s: float | None = None) -> bool:
    """Wait for an event to be set (without a timeout: as long as it takes); False if it wasn't or the wait failed."""
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
    kernel32.WaitForSingleObject.restype = wt.DWORD
    ms = 0xFFFFFFFF if timeout_s is None else int(timeout_s * 1000)              # INFINITE
    return kernel32.WaitForSingleObject(event, ms) == 0                          # WAIT_OBJECT_0


def take_open_request(name: str) -> str:
    import json
    import time

    f = _instance_file(name).with_name(name.split("\\")[-1] + "-open.json")
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
        f.unlink()
    except (OSError, ValueError):
        return ""
    return str(d.get("target") or "") if time.time() - float(d.get("at") or 0) < 60 else ""


def mark_instance(name: str, phase: str) -> None:
    """Note how far start-up got ("window": the WinForms window is being created, "shown"). The start-up window
    (splash.py) reads it to tell a window that got stuck being created from a merely slow start."""
    import json
    import time

    d = read_instance(name)
    if d.get("pid") != os.getpid():
        d = {"pid": os.getpid(), "started": time.time()}
    d.update(phase=phase, phase_at=time.time())
    f = _instance_file(name)
    tmp = f.with_name(f.name + ".tmp")
    try:
        tmp.write_text(json.dumps(d), encoding="utf-8")
        os.replace(tmp, f)                              # the splash never reads half a file
    except OSError:
        pass


def instance_held(name: str) -> bool:
    """Does a running process hold the single-instance mutex ``name``?"""
    k = ctypes.windll.kernel32
    k.OpenMutexW.restype = ctypes.c_void_p
    h = k.OpenMutexW(0x00100000, False, name)          # SYNCHRONIZE
    if h:
        k.CloseHandle(ctypes.c_void_p(h))
        return True
    return False


def find_class_window(class_name: str) -> int:
    user32 = ctypes.WinDLL("user32")
    user32.FindWindowW.restype = wt.HWND
    user32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
    return user32.FindWindowW(class_name, None) or 0


def is_hung(hwnd: int) -> bool:
    user32 = ctypes.WinDLL("user32")
    user32.IsHungAppWindow.argtypes = [wt.HWND]
    return bool(hwnd and user32.IsHungAppWindow(hwnd))


def bring_to_front(hwnd: int) -> None:
    user32 = ctypes.WinDLL("user32")
    user32.ShowWindow(hwnd, 9 if user32.IsIconic(hwnd) else 5)
    user32.SetForegroundWindow(hwnd)


def kill_tree(pid: int) -> None:
    """End a process and its children (WebView2 helpers)."""
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(int(pid))], capture_output=True, timeout=15,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError, ValueError):
        pass


def message_box(text: str, title: str = "IEUStudia", flags: int = 0x40) -> int:
    return ctypes.windll.user32.MessageBoxW(None, text, title, flags)


def acquire_single_instance(name: str = "Local\\Studia-SingleInstance", wait_s: float = 0.0) -> bool:
    """True if this is the only running instance. ``wait_s``: wait that long for a closing copy to exit (a restart)."""
    global _mutex
    import time

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = wt.HANDLE
    kernel32.CloseHandle.argtypes = [wt.HANDLE]
    deadline = time.monotonic() + wait_s
    while True:
        _mutex = kernel32.CreateMutexW(None, False, name)
        if ctypes.get_last_error() != ERROR_ALREADY_EXISTS:
            return True
        if time.monotonic() >= deadline:
            return False
        kernel32.CloseHandle(_mutex)      # our own handle would keep the mutex alive after the other copy exits
        _mutex = None
        time.sleep(0.2)


def release_single_instance() -> None:
    """Let go of the lock before this process ends (the installer of an update checks that IEUStudia isn't running)."""
    global _mutex
    if _mutex:
        ctypes.WinDLL("kernel32").CloseHandle(wt.HANDLE(_mutex))
        _mutex = None


def _main_window(title: str) -> int:
    """The app's WinForms main window (pywebview also creates small helper windows with the same title)."""
    user32 = ctypes.WinDLL("user32")
    found: list[int] = []
    proc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(hwnd, _):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        if buf.value == title and user32.IsWindowVisible(hwnd):
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls, 256)
            if "WindowsForms" in cls.value:
                found.append(hwnd)
        return True

    user32.EnumWindows(proc(cb), 0)
    return found[0] if found else 0


def own_main_window() -> int:
    """This process's main window (the largest visible WinForms top-level window), 0 if not shown yet.

    Sends no window messages, so it is safe to call from any thread."""
    user32 = ctypes.WinDLL("user32")
    pid = os.getpid()
    best = [0, 0]                                   # hwnd, area
    proc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(hwnd, _):
        owner = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls, 256)
            r = wt.RECT()
            if "WindowsForms" in cls.value and user32.GetWindowRect(hwnd, ctypes.byref(r)):
                area = (r.right - r.left) * (r.bottom - r.top)
                if area > best[1]:
                    best[:] = [hwnd, area]
        return True

    user32.EnumWindows(proc(cb), 0)
    return best[0]


def set_window_icon(hwnd: int, ico: str | Path) -> bool:
    """Title-bar / taskbar icon via posted WM_SETICON messages: never waits, safe from any thread.

    (Setting the WinForms ``Form.Icon`` property from a background thread can deadlock with the UI
    thread, which once froze the window at start-up.)"""
    user32 = ctypes.WinDLL("user32")
    user32.LoadImageW.restype = wt.HANDLE
    user32.LoadImageW.argtypes = [wt.HINSTANCE, wt.LPCWSTR, wt.UINT, ctypes.c_int, ctypes.c_int, wt.UINT]
    user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    ok = False
    for which, cx, cy in ((1, 11, 12), (0, 49, 50)):     # ICON_BIG: SM_CXICON/SM_CYICON, ICON_SMALL: SM_CXSMICON/…
        icon = user32.LoadImageW(None, str(ico), 1, user32.GetSystemMetrics(cx), user32.GetSystemMetrics(cy), 0x10)
        if icon:                                         # IMAGE_ICON, LR_LOADFROMFILE
            ok = bool(user32.PostMessageW(hwnd, 0x0080, which, icon)) or ok      # WM_SETICON
    return ok


def process_age() -> float:
    """Seconds since this process was created (includes Python's own start-up); 0.0 if unknown."""
    try:
        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.GetCurrentProcess.restype = wt.HANDLE
        ft = ctypes.POINTER(wt.FILETIME)
        kernel32.GetProcessTimes.argtypes = [wt.HANDLE, ft, ft, ft, ft]
        kernel32.GetSystemTimeAsFileTime.argtypes = [ft]
        created, exited, kernel, user, now = (wt.FILETIME() for _ in range(5))
        if not kernel32.GetProcessTimes(kernel32.GetCurrentProcess(), ctypes.byref(created), ctypes.byref(exited),
                                        ctypes.byref(kernel), ctypes.byref(user)):
            return 0.0
        kernel32.GetSystemTimeAsFileTime(ctypes.byref(now))
    except (OSError, ctypes.ArgumentError):
        return 0.0
    ticks = lambda f: (f.dwHighDateTime << 32) | f.dwLowDateTime   # noqa: E731 - 100 ns units
    return max(0.0, (ticks(now) - ticks(created)) / 1e7)


def windows_dark_mode() -> bool:
    """Windows is set to dark mode for apps."""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


def focus_window(title: str) -> bool:
    user32 = ctypes.WinDLL("user32")
    hwnd = _main_window(title)
    if not hwnd:
        return False
    user32.ShowWindow(hwnd, 9 if user32.IsIconic(hwnd) else 5)  # SW_RESTORE / SW_SHOW
    user32.SetForegroundWindow(hwnd)
    return True


class _FlashInfo(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("hwnd", wt.HWND), ("dwFlags", wt.DWORD), ("uCount", wt.UINT),
                ("dwTimeout", wt.DWORD)]


def is_foreground(hwnd: int) -> bool:
    """Is this window the one you're using?"""
    return bool(hwnd) and ctypes.WinDLL("user32").GetForegroundWindow() == hwnd


def flash_taskbar(hwnd: int) -> None:
    """Flash the taskbar button until the user switches to the window (only when it is in the background)."""
    if not hwnd:
        return
    user32 = ctypes.WinDLL("user32")
    if user32.GetForegroundWindow() == hwnd:
        return
    info = _FlashInfo(ctypes.sizeof(_FlashInfo), hwnd, 0x3 | 0xC, 0, 0)   # FLASHW_ALL | FLASHW_TIMERNOFG
    user32.FlashWindowEx(ctypes.byref(info))


def set_app_id(app_id: str = "Studia.App") -> None:
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except (AttributeError, OSError):
        pass


# ----------------------------------------------------------------------------- priority while transcribing
_BELOW_NORMAL, _NORMAL = 0x4000, 0x20
_prio_lock = threading.Lock()
_low = False
_eco = False                                        # Windows' efficiency mode (see background_work)
_starting = 0                                       # programs being started right now (see normal_priority)


def _priority_class(value: int) -> bool:
    try:
        k32 = ctypes.WinDLL("kernel32")
        k32.GetCurrentProcess.restype = wt.HANDLE
        k32.SetPriorityClass.argtypes = [wt.HANDLE, wt.DWORD]
        return bool(k32.SetPriorityClass(k32.GetCurrentProcess(), value))
    except (AttributeError, OSError):
        return False


class _PowerThrottling(ctypes.Structure):
    _fields_ = [("Version", wt.ULONG), ("ControlMask", wt.ULONG), ("StateMask", wt.ULONG)]


def _efficiency(on: bool) -> bool:
    """EcoQoS for this process (Windows runs it on the efficient cores at a lower clock), or Windows' own choice."""
    try:
        k32 = ctypes.WinDLL("kernel32")
        k32.GetCurrentProcess.restype = wt.HANDLE
        k32.SetProcessInformation.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
        state = _PowerThrottling(1, int(on), int(on))         # PROCESS_POWER_THROTTLING_EXECUTION_SPEED
        return bool(k32.SetProcessInformation(k32.GetCurrentProcess(), 4, ctypes.byref(state),   # ProcessPowerThrottling
                                              ctypes.sizeof(state)))
    except (AttributeError, OSError):
        return False


@contextlib.contextmanager
def background_work() -> Iterator[None]:
    """For the tray helper's checks: below-normal priority and EcoQoS, so the programs you use come first and the
    checks use less power. A program started meanwhile (the app) still starts as usual (see normal_priority)."""
    global _eco
    set_low_priority(True)
    with _prio_lock:
        _eco = True
        if not _starting:
            _efficiency(True)
    try:
        yield
    finally:
        with _prio_lock:
            _eco = False
            if not _starting:
                _efficiency(False)
        set_low_priority(False)


# ----------------------------------------------------------------------------- the threads that drive the graphics card
# While IEUStudia transcribes, its process runs below normal, threads included. The thread that hands the graphics card
# its work (CTranslate2's worker: thousands of small steps per batch, each waiting for the card) then waits for a free
# processor behind every program at normal priority, and the card sits idle meanwhile: with other programs busy, a
# lecture took 3.5-5.4x as long as the same Full sync's others. These few threads run as normal programs do instead
# (THREAD_PRIORITY_HIGHEST in a below-normal process is the priority of a normal program's thread), and Windows doesn't
# move them to its slower, power-saving cores. The work on the processor (decoding, finding speech, slides) stays below.
FEEDER_PRIORITY = 2                                 # THREAD_PRIORITY_HIGHEST: base 8 in a below-normal process
_THREAD_SET_INFORMATION, _THREAD_QUERY_LIMITED_INFORMATION = 0x20, 0x800


def native_thread_ids() -> set[int]:
    """The ids of this process's threads, those of native libraries included."""

    class _Entry(ctypes.Structure):
        _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ThreadID", wt.DWORD),
                    ("th32OwnerProcessID", wt.DWORD), ("tpBasePri", wt.LONG), ("tpDeltaPri", wt.LONG),
                    ("dwFlags", wt.DWORD)]

    try:
        k32 = ctypes.WinDLL("kernel32")
        k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
        k32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
        k32.Thread32First.argtypes = k32.Thread32Next.argtypes = [wt.HANDLE, ctypes.c_void_p]
        k32.CloseHandle.argtypes = [wt.HANDLE]
        snap = k32.CreateToolhelp32Snapshot(0x4, 0)               # TH32CS_SNAPTHREAD (every process's)
        if not snap or snap == wt.HANDLE(-1).value:
            return set()
    except (AttributeError, OSError):
        return set()
    pid, out = os.getpid(), set()
    entry = _Entry()
    entry.dwSize = ctypes.sizeof(_Entry)
    try:
        ok = k32.Thread32First(snap, ctypes.byref(entry))
        while ok:
            if entry.th32OwnerProcessID == pid:
                out.add(int(entry.th32ThreadID))
            ok = k32.Thread32Next(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    return out


def drive_card(thread_ids: Iterable[int], on: bool = True) -> int:
    """Run these threads as a normal program's (``on``) or as the rest of the process again. How many were set."""
    try:
        k32 = ctypes.WinDLL("kernel32")
        k32.OpenThread.restype = wt.HANDLE
        k32.OpenThread.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
        k32.SetThreadPriority.argtypes = [wt.HANDLE, ctypes.c_int]
        k32.SetThreadInformation.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
        k32.CloseHandle.argtypes = [wt.HANDLE]
    except (AttributeError, OSError):
        return 0
    # THREAD_POWER_THROTTLING_EXECUTION_SPEED: controlled, not throttled (always the fast cores' quality of service);
    # off again: Windows decides as for the rest of the process
    state = _PowerThrottling(1, 1 if on else 0, 0)
    n = 0
    for tid in thread_ids:
        h = k32.OpenThread(_THREAD_SET_INFORMATION | _THREAD_QUERY_LIMITED_INFORMATION, False, int(tid))
        if not h:
            continue
        try:
            if k32.SetThreadPriority(h, FEEDER_PRIORITY if on else 0):
                n += 1
            k32.SetThreadInformation(h, 3, ctypes.byref(state), ctypes.sizeof(state))   # ThreadPowerThrottling
        finally:
            k32.CloseHandle(h)
    return n


def set_low_priority(on: bool) -> None:
    """Below-normal priority while IEUStudia transcribes: the programs you use come first, and with nothing else
    running the transcription still gets the whole processor."""
    global _low
    with _prio_lock:
        if on != _low and (_starting or _priority_class(_BELOW_NORMAL if on else _NORMAL)):
            _low = on                                # (while a program starts, its end switches)


@contextlib.contextmanager
def normal_priority() -> Iterator[None]:
    """For starting other programs (a PDF viewer, the browser): Windows gives a program started by a process at
    below-normal priority that priority too, for as long as it runs."""
    global _starting
    with _prio_lock:
        _starting += 1
        if _low:
            _priority_class(_NORMAL)
        if _eco:
            _efficiency(False)
    try:
        yield
    finally:
        with _prio_lock:
            _starting -= 1
            if _low and not _starting:
                _priority_class(_BELOW_NORMAL)
            if _eco and not _starting:
                _efficiency(True)


class _ShellExecuteInfo(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("fMask", wt.ULONG), ("hwnd", wt.HWND), ("lpVerb", wt.LPCWSTR),
                ("lpFile", wt.LPCWSTR), ("lpParameters", wt.LPCWSTR), ("lpDirectory", wt.LPCWSTR), ("nShow", ctypes.c_int),
                ("hInstApp", wt.HINSTANCE), ("lpIDList", ctypes.c_void_p), ("lpClass", wt.LPCWSTR), ("hkeyClass", wt.HKEY),
                ("dwHotKey", wt.DWORD), ("hIcon", wt.HANDLE), ("hProcess", wt.HANDLE)]


def _site(url: str) -> str:
    """A link for the log without its query (a sign-in's carries codes)."""
    import urllib.parse

    u = urllib.parse.urlsplit(url)
    return f"{u.scheme}://{u.netloc}{u.path}"


def _shell_open(target: str, wait_s: float = 20.0) -> tuple[bool, str]:
    """ShellExecuteEx from a thread of its own with COM set up: pywebview's calls come on bare threads that end at
    once, and some default browsers (the Microsoft Store ones among them) are started through COM. No error box and
    no "pick an app" window: a failure comes back here instead."""
    out: dict[str, object] = {}

    def run() -> None:
        ole32 = ctypes.WinDLL("ole32")
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(_ShellExecuteInfo)]
        shell32.ShellExecuteExW.restype = wt.BOOL
        hr = ole32.CoInitializeEx(None, 0x2 | 0x4)       # COINIT_APARTMENTTHREADED | COINIT_DISABLE_OLE1DDE
        try:
            info = _ShellExecuteInfo(cbSize=ctypes.sizeof(_ShellExecuteInfo), lpFile=target, nShow=1,
                                     fMask=0x100 | 0x400)  # SEE_MASK_NOASYNC | SEE_MASK_FLAG_NO_UI; SW_SHOWNORMAL
            if shell32.ShellExecuteExW(ctypes.byref(info)):
                out["ok"] = True
            else:
                out["error"] = str(ctypes.WinError(ctypes.get_last_error()))
        except Exception as exc:  # noqa: BLE001 - said by the caller
            out["error"] = str(exc)
        finally:
            if hr in (0, 1):                               # S_OK, S_FALSE: this thread set COM up
                ole32.CoUninitialize()

    th = threading.Thread(target=run, name="open-url", daemon=True)
    th.start()
    th.join(wait_s)
    if th.is_alive():                                      # still starting a browser that takes its time
        return True, ""
    return bool(out.get("ok")), str(out.get("error", ""))


def _edge() -> str:
    """Microsoft Edge's program, on every Windows 10 and 11 PC (unless removed)."""
    import winreg

    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(root, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe") as k:
                exe = str(winreg.QueryValueEx(k, "")[0]).strip('"')
            if exe and Path(exe).is_file():
                return exe
        except OSError:
            pass
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
        exe = Path(base or "-") / "Microsoft" / "Edge" / "Application" / "msedge.exe"
        if base and exe.is_file():
            return str(exe)
    return ""


def _default_browser(scheme: str) -> tuple[bool, str]:
    """The program Windows opens ``scheme`` links with, as (known, program). known False: Windows didn't say, so
    only the shell can tell. program "": none is set, or it was removed. Asking first matters: the shell answers a
    link without a program with a "Pick an app" window and reports success, and http and https have separate
    settings (a PC can have only https set)."""
    try:
        query = ctypes.WinDLL("shlwapi").AssocQueryStringW
        query.argtypes = [wt.DWORD, ctypes.c_int, wt.LPCWSTR, wt.LPCWSTR, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
        query.restype = ctypes.c_long
        buf, size = ctypes.create_unicode_buffer(1024), wt.DWORD(1024)
        hr = query(0x1000 | 0x20, 2, scheme, "open", buf, ctypes.byref(size))   # IS_PROTOCOL | NOTRUNCATE; EXECUTABLE
    except (OSError, AttributeError, ValueError):
        return False, ""
    if hr == 0:
        program = buf.value
        gone = "windowsapps" not in program.lower() and not Path(program).is_file()   # Store apps can't be looked at
        return True, "" if gone else program
    return (True, "") if hr & 0xFFFFFFFF == 0x80070483 else (False, "")         # ERROR_NO_ASSOCIATION


def _start(program: str, url: str) -> bool:
    try:
        subprocess.Popen([program, url], close_fds=True)
        log.info("Opened %s with %s", _site(url), Path(program).name)
        return True
    except OSError as exc:
        log.warning("%s did not start: %s", Path(program).name, exc)
        return False


def _elevated() -> bool:
    """Run as administrator. A browser started from here would run so too, and can't reach the browser already open."""
    try:
        return bool(ctypes.WinDLL("shell32").IsUserAnAdmin())
    except (OSError, AttributeError):
        return False


def open_in_browser(url: str) -> bool:
    """Open a web page in the default browser, allowed to come to the front. When Windows has no working default
    browser (none set, or one that was removed), Microsoft Edge. False when nothing could be started: the caller
    says so and offers the link to copy."""
    with normal_priority():
        try:                                               # we are in front (the user just clicked): so may it be
            ctypes.WinDLL("user32").AllowSetForegroundWindow(wt.DWORD(0xFFFFFFFF))   # ASFW_ANY
        except (OSError, AttributeError):
            pass
        scheme = url.partition(":")[0].lower()
        known, program = _default_browser(scheme)
        log.info("Opening %s; default browser: %s", _site(url), program or ("none" if known else "not known"))
        if _elevated() and _start("explorer.exe", url):    # explorer runs as the signed-in user, and passes it on
            return True
        if program or not known:
            ok, error = _shell_open(url)
            if ok:
                return True
            log.warning("The default browser did not open %s: %s", _site(url), error or "no reason given")
        else:
            log.warning("Windows has no working browser set for %s links", scheme)
            other = _default_browser("https")[1] if scheme == "http" else ""
            if other and "windowsapps" not in other.lower() and _start(other, url):
                return True
        edge = _edge()
        if edge and _start(edge, url):
            return True
        ok, error = _shell_open("microsoft-edge:" + url)
        if ok:
            return True
        log.warning("No browser could be started for %s: %s", _site(url), error or "no reason given")
        return False


def open_path(path: str | Path) -> None:
    with normal_priority():
        os.startfile(str(path))  # type: ignore[attr-defined]


def reveal(path: str | Path) -> None:
    p = Path(path)
    with normal_priority():
        if p.is_file():
            subprocess.Popen(["explorer", "/select,", str(p)], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            os.startfile(str(p))  # type: ignore[attr-defined]


class TaskbarProgress:
    """The window's taskbar button as a progress bar (ITaskbarList3), set from one thread of its own. Cosmetic: if
    Windows says no, nothing happens."""

    OFF, INDETERMINATE, NORMAL, ERROR, PAUSED = 0x0, 0x1, 0x2, 0x4, 0x8

    def __init__(self) -> None:
        self._q: "queue.Queue[tuple[int, int, float]]" = queue.Queue()
        self._thread: threading.Thread | None = None
        self._dead = False                             # Windows said no: nothing more is queued

    def set(self, hwnd: int, state: int, fraction: float = 0.0) -> None:
        if not hwnd or self._dead:
            return
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="taskbar", daemon=True)
            self._thread.start()
        self._q.put((hwnd, state, max(0.0, min(1.0, fraction))))

    def _run(self) -> None:
        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD), ("Data4", ctypes.c_ubyte * 8)]

        try:
            ole32 = ctypes.windll.ole32
            ole32.CoInitializeEx(None, 0x2)                                   # COINIT_APARTMENTTHREADED
            clsid, iid = GUID(), GUID()
            ole32.CLSIDFromString("{56FDF344-FD6D-11d0-958A-006097C9A090}", ctypes.byref(clsid))   # TaskbarList
            ole32.CLSIDFromString("{EA1AFB91-9E28-4B86-90E9-9E9F8A5EEFAF}", ctypes.byref(iid))     # ITaskbarList3
            obj = ctypes.c_void_p()
            if ole32.CoCreateInstance(ctypes.byref(clsid), None, 0x1, ctypes.byref(iid), ctypes.byref(obj)) != 0:
                self._dead = True
                return
            vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]

            def method(index: int, *args: type) -> ctypes._CFuncPtr:
                return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *args)(vtable[index])

            hr_init = method(3)
            set_value = method(9, wt.HWND, ctypes.c_ulonglong, ctypes.c_ulonglong)
            set_state = method(10, wt.HWND, ctypes.c_int)
            hr_init(obj)
        except (AttributeError, OSError, ValueError):
            self._dead = True
            return
        shown = None
        while True:
            hwnd, state, fraction = self._q.get()
            while not self._q.empty():                 # only the newest counts
                hwnd, state, fraction = self._q.get_nowait()
            now = (hwnd, state, round(fraction * 1000))
            if now == shown:                           # a job's progress comes 4 times a second; the bar moves less
                continue
            shown = now
            try:
                set_state(obj, hwnd, state)
                if state in (self.NORMAL, self.PAUSED, self.ERROR):
                    set_value(obj, hwnd, now[2], 1000)
            except OSError:
                shown = None


def work_area() -> tuple[float, float, float, float]:
    """The primary screen's usable area (without the taskbar) as (left, top, width, height) in 96-dpi pixels, the unit
    pywebview places and sizes windows in; all 0 if unknown."""
    user32 = ctypes.windll.user32
    r = wt.RECT()
    if not user32.SystemParametersInfoW(0x30, 0, ctypes.byref(r), 0):      # SPI_GETWORKAREA
        return 0.0, 0.0, 0.0, 0.0
    scale = 1.0
    try:                                     # a DPI-aware process gets physical pixels
        if user32.IsProcessDPIAware():
            scale = (user32.GetDpiForSystem() or 96) / 96
    except (AttributeError, OSError):
        pass
    return r.left / scale, r.top / scale, (r.right - r.left) / scale, (r.bottom - r.top) / scale


class _ReasonContext(ctypes.Structure):
    _fields_ = [("Version", wt.ULONG), ("Flags", wt.DWORD), ("SimpleReasonString", wt.LPWSTR),
                ("_rest", ctypes.c_byte * 16)]           # the union's detailed form is larger


class KeepAwake:
    """While on, Windows' idle timer does not put the PC to sleep (the screen may still turn off; closing a laptop's
    lid still sleeps it). Listed by ``powercfg /requests`` with the reason. On and off from any thread."""

    def __init__(self, reason: str) -> None:
        self._reason = reason
        self._handle = None
        self._on = False

    def on(self) -> None:
        self._set(True)

    def off(self) -> None:
        self._set(False)

    def _set(self, on: bool) -> None:
        if on == self._on:
            return
        try:
            k32 = ctypes.windll.kernel32
            if self._handle is None:
                k32.PowerCreateRequest.restype = wt.HANDLE
                ctx = _ReasonContext(0, 0x1, self._reason)     # POWER_REQUEST_CONTEXT_VERSION, SIMPLE_STRING
                handle = k32.PowerCreateRequest(ctypes.byref(ctx))
                if not handle or handle == wt.HANDLE(-1).value:
                    return
                self._handle = handle
            fn = k32.PowerSetRequest if on else k32.PowerClearRequest
            if fn(wt.HANDLE(self._handle), 1):                # PowerRequestSystemRequired
                self._on = on
        except (AttributeError, OSError):
            pass
