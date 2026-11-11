"""A notification-area (tray) icon with a menu and Windows notifications, in plain Win32 through ctypes.

``Tray.run()`` owns the calling thread (it runs the window message loop); ``notify()``, ``set_tooltip()`` and
``stop()`` may be called from any thread (they post messages). Notifications are the classic tray balloons, which
Windows 10/11 show as toasts in the notification centre.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import itertools
import logging
import threading
from pathlib import Path
from typing import Any, Callable, Optional

from .i18n import t

log = logging.getLogger("studia")

user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
WM_APP = 0x8000
WM_TRAY, WM_NOTIFY_POST, WM_TIP_POST = WM_APP + 1, WM_APP + 2, WM_APP + 3
WM_CLOSE, WM_DESTROY, WM_COMMAND, WM_NULL, WM_LBUTTONUP, WM_RBUTTONUP, WM_CONTEXTMENU = 0x10, 0x2, 0x111, 0x0, 0x202, 0x205, 0x7B
NIN_SELECT, NIN_KEYSELECT, NIN_BALLOONUSERCLICK = 0x400, 0x401, 0x405
NIM_ADD, NIM_MODIFY, NIM_DELETE, NIM_SETVERSION = 0, 1, 2, 4
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO, NIF_SHOWTIP = 0x1, 0x2, 0x4, 0x10, 0x80
NIIF_USER, NIIF_LARGE_ICON, NIIF_INFO = 0x4, 0x20, 0x1
NOTIFYICON_VERSION_4 = 4
MF_STRING, MF_SEPARATOR, MF_CHECKED, MF_GRAYED, MF_DEFAULT = 0x0, 0x800, 0x8, 0x1, 0x1000
TPM_RIGHTBUTTON, TPM_RETURNCMD, TPM_NONOTIFY = 0x2, 0x100, 0x80
WM_POWERBROADCAST, PBT_POWERSETTINGCHANGE, PBT_APMRESUMEAUTOMATIC = 0x218, 0x8013, 0x12


def _cut(text: str, units: int) -> str:
    """``text`` cut to what a WCHAR field of ``units`` + 1 holds: Windows counts UTF-16 units, and an emoji takes two
    ("📢 Quiz 3 hakkında" in an announcement's title made a notification too long, and it was lost)."""
    return (text or "").encode("utf-16-le")[:2 * units].decode("utf-16-le", "ignore")


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD), ("Data4", ctypes.c_ubyte * 8)]


# the screen: 0 off, 1 on, 2 dimmed (Windows sends the current state as soon as you register, then each change)
GUID_CONSOLE_DISPLAY_STATE = GUID(0x6FE69556, 0x704A, 0x47A0,
                                  (ctypes.c_ubyte * 8)(0x8F, 0x24, 0xC2, 0x8D, 0x93, 0x6F, 0xDA, 0x47))


class POWERBROADCAST_SETTING(ctypes.Structure):
    _fields_ = [("PowerSetting", GUID), ("DataLength", wt.DWORD), ("Data", wt.DWORD)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uID", wt.UINT), ("uFlags", wt.UINT),
                ("uCallbackMessage", wt.UINT), ("hIcon", wt.HICON), ("szTip", wt.WCHAR * 128), ("dwState", wt.DWORD),
                ("dwStateMask", wt.DWORD), ("szInfo", wt.WCHAR * 256), ("uVersion", wt.UINT),
                ("szInfoTitle", wt.WCHAR * 64), ("dwInfoFlags", wt.DWORD), ("guidItem", GUID),
                ("hBalloonIcon", wt.HICON)]


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("style", wt.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON), ("hCursor", wt.HANDLE),
                ("hbrBackground", wt.HBRUSH), ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR),
                ("hIconSm", wt.HICON)]


def _sigs() -> None:
    user32.DefWindowProcW.restype = LRESULT
    user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    user32.CreateWindowExW.restype = wt.HWND
    user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
    user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
    user32.LoadImageW.restype = wt.HANDLE
    user32.LoadImageW.argtypes = [wt.HINSTANCE, wt.LPCWSTR, wt.UINT, ctypes.c_int, ctypes.c_int, wt.UINT]
    user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    user32.CreatePopupMenu.restype = wt.HMENU
    user32.AppendMenuW.argtypes = [wt.HMENU, wt.UINT, ctypes.c_size_t, wt.LPCWSTR]
    user32.TrackPopupMenuEx.argtypes = [wt.HMENU, wt.UINT, ctypes.c_int, ctypes.c_int, wt.HWND, ctypes.c_void_p]
    user32.TrackPopupMenuEx.restype = wt.BOOL
    user32.DestroyMenu.argtypes = [wt.HMENU]
    user32.SetMenuDefaultItem.argtypes = [wt.HMENU, wt.UINT, wt.UINT]
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
    user32.SetForegroundWindow.argtypes = [wt.HWND]
    user32.RegisterWindowMessageW.argtypes = [wt.LPCWSTR]
    user32.RegisterWindowMessageW.restype = wt.UINT
    user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
    user32.DestroyWindow.argtypes = [wt.HWND]
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.UnregisterClassW.argtypes = [wt.LPCWSTR, wt.HINSTANCE]
    user32.DestroyIcon.argtypes = [wt.HICON]
    user32.RegisterPowerSettingNotification.restype = ctypes.c_void_p
    user32.RegisterPowerSettingNotification.argtypes = [wt.HANDLE, ctypes.POINTER(GUID), wt.DWORD]
    user32.UnregisterPowerSettingNotification.argtypes = [ctypes.c_void_p]
    shell32.Shell_NotifyIconW.argtypes = [wt.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
    shell32.Shell_NotifyIconW.restype = wt.BOOL
    kernel32.GetModuleHandleW.restype = wt.HMODULE
    kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]


MenuItem = tuple[int, str, bool, bool]          # (id, label, checked, enabled); id 0 = separator


class Tray:
    def __init__(self, tooltip: str, icon: Path, menu: Callable[[], list[MenuItem]],
                 on_command: Callable[[int], None], on_open: Callable[[Optional[Any]], None],
                 class_name: str = "IEUStudiaTray", title: str = "IEUStudia helper",
                 on_power: Optional[Callable[[str], None]] = None) -> None:
        """``class_name``: the installer finds the helper by "IEUStudiaTray" to close it before replacing files (see
        PrepareToInstall in packaging/IEUStudia.iss); other icons need a name of their own. ``on_power``: told "on"
        or "off" when the screen comes on or goes off (dimmed counts as on), and "resume" when the PC wakes up."""
        _sigs()
        self.tooltip = tooltip
        self.class_name, self.title = class_name, title
        self.icon_path = Path(icon)
        self.menu = menu
        self.on_command = on_command
        self.on_open = on_open                   # click on the icon (None) or on a notification (its payload)
        self.on_power = on_power
        self.hwnd = 0
        self._power_note: Optional[int] = None
        self._proc = WNDPROC(self._wndproc)      # kept: Windows calls it for as long as the window lives
        self._pending: list[tuple[str, str, Any]] = []
        self._payload: Any = None
        self._lock = threading.Lock()
        self._taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")
        self.ready = threading.Event()

    # ---- public (any thread)
    def notify(self, title: str, text: str, payload: Any = None) -> None:
        with self._lock:
            self._pending.append((_cut(title, 63), _cut(text, 255), payload))
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_NOTIFY_POST, 0, 0)

    def set_tooltip(self, text: str) -> None:
        self.tooltip = _cut(text, 127)
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_TIP_POST, 0, 0)

    def stop(self) -> None:
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)

    # ---- window
    def _data(self, flags: int) -> NOTIFYICONDATAW:
        d = NOTIFYICONDATAW()
        d.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        d.hWnd = self.hwnd
        d.uID = 1
        d.uFlags = flags
        return d

    def _add(self) -> None:
        d = self._data(NIF_MESSAGE | NIF_ICON | NIF_TIP | NIF_SHOWTIP)
        d.uCallbackMessage = WM_TRAY
        d.hIcon = self._icon_small
        d.szTip = _cut(self.tooltip, 127)
        shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(d))
        d.uVersion = NOTIFYICON_VERSION_4
        shell32.Shell_NotifyIconW(NIM_SETVERSION, ctypes.byref(d))

    def _remove(self) -> None:
        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._data(0)))

    def _show_pending(self) -> None:
        with self._lock:
            items, self._pending = self._pending, []
        for title, text, payload in items[-1:]:           # Windows shows one at a time: the newest wins
            self._payload = payload
            d = self._data(NIF_INFO)
            d.szInfoTitle = title
            d.szInfo = text or " "
            d.dwInfoFlags = NIIF_USER | NIIF_LARGE_ICON if self._icon_big else NIIF_INFO
            d.hBalloonIcon = self._icon_big
            shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(d))

    def _popup(self) -> None:
        items = self.menu()
        m = user32.CreatePopupMenu()
        for cid, label, checked, enabled in items:
            if cid == 0:
                user32.AppendMenuW(m, MF_SEPARATOR, 0, None)
            else:
                user32.AppendMenuW(m, MF_STRING | (MF_CHECKED if checked else 0) | (0 if enabled else MF_GRAYED), cid, label)
        if items:
            user32.SetMenuDefaultItem(m, items[0][0], 0)
        pt = wt.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        user32.SetForegroundWindow(self.hwnd)          # so the menu closes when you click elsewhere
        cmd = user32.TrackPopupMenuEx(m, TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY, pt.x, pt.y, self.hwnd, None)
        user32.PostMessageW(self.hwnd, WM_NULL, 0, 0)
        user32.DestroyMenu(m)
        if cmd:
            self._safe(self.on_command, int(cmd))

    @staticmethod
    def _safe(fn: Callable[..., Any], *args: Any) -> None:
        try:
            fn(*args)
        except Exception:  # noqa: BLE001 - a handler must never end the message loop
            log.exception("tray handler failed")

    def _wndproc(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        if msg == WM_TRAY:
            event = lparam & 0xFFFF
            if event in (WM_LBUTTONUP, NIN_SELECT, NIN_KEYSELECT):
                self._safe(self.on_open, None)
            elif event in (WM_CONTEXTMENU, WM_RBUTTONUP):
                self._popup()
            elif event == NIN_BALLOONUSERCLICK:
                self._safe(self.on_open, self._payload)
            return 0
        if msg == WM_NOTIFY_POST:
            self._show_pending()
            return 0
        if msg == WM_TIP_POST:
            d = self._data(NIF_TIP | NIF_SHOWTIP)
            d.szTip = _cut(self.tooltip, 127)
            shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(d))
            return 0
        if msg == self._taskbar_created and msg:       # Explorer restarted: put the icon back
            self._add()
            return 0
        if msg == WM_POWERBROADCAST and self.on_power is not None:
            event = self._power_event(wparam, lparam)
            if event:
                self._safe(self.on_power, event)
            return 1
        if msg == WM_CLOSE:
            if self._power_note:
                user32.UnregisterPowerSettingNotification(self._power_note)
                self._power_note = None
            self._remove()
            user32.DestroyWindow(hwnd)
            return 0
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    @staticmethod
    def _power_event(wparam: int, lparam: int) -> str:
        """WM_POWERBROADCAST → "on", "off", "resume" or "" (something else)."""
        if wparam == PBT_APMRESUMEAUTOMATIC:
            return "resume"
        if wparam == PBT_POWERSETTINGCHANGE and lparam:
            s = POWERBROADCAST_SETTING.from_address(lparam)
            if bytes(s.PowerSetting) == bytes(GUID_CONSOLE_DISPLAY_STATE) and s.DataLength >= 4:
                return "off" if s.Data == 0 else "on"
        return ""

    def run(self) -> None:
        hinst = kernel32.GetModuleHandleW(None)
        name = self.class_name
        cls = WNDCLASSEXW()
        cls.cbSize = ctypes.sizeof(WNDCLASSEXW)
        cls.lpfnWndProc = self._proc
        cls.hInstance = hinst
        cls.lpszClassName = name
        user32.RegisterClassExW(ctypes.byref(cls))
        self.hwnd = user32.CreateWindowExW(0, name, self.title, 0, 0, 0, 0, 0, None, None, hinst, None)
        ico = str(self.icon_path)
        self._icon_small = user32.LoadImageW(None, ico, 1, user32.GetSystemMetrics(49), user32.GetSystemMetrics(50), 0x10)
        self._icon_big = user32.LoadImageW(None, ico, 1, 64, 64, 0x10)
        self._add()
        if self.on_power is not None:                  # DEVICE_NOTIFY_WINDOW_HANDLE: as WM_POWERBROADCAST
            self._power_note = user32.RegisterPowerSettingNotification(
                self.hwnd, ctypes.byref(GUID_CONSOLE_DISPLAY_STATE), 0) or None
        self.ready.set()
        if self._pending:
            self._show_pending()
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        self.hwnd = 0
        user32.UnregisterClassW(name, hinst)
        for icon in (self._icon_small, self._icon_big):  # the app makes a new icon for each notification
            if icon:
                user32.DestroyIcon(icon)


_SERIAL = itertools.count(1)         # each notification icon's window class is new (the last may still be closing)


class Notifier:
    """Notifications from the app itself (the tray helper may not be running): an icon appears next to the clock for
    a notification and goes away when the window is opened from it, or after a while."""

    KEEP_S = 600

    def __init__(self, icon: Path, on_click: Callable[[Optional[Any]], None]) -> None:
        self.icon = Path(icon)
        self.on_click = on_click                 # the notification's payload, or None (the icon was clicked)
        self._tray: Optional[Tray] = None
        self._timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()

    def notify(self, title: str, text: str, payload: Any = None) -> None:
        with self._lock:
            if self._tray is None:
                tray = Tray("IEUStudia", self.icon, lambda: [(1, t("Open IEUStudia"), False, True)],
                            lambda cmd: self._clicked(None), self._clicked,
                            class_name=f"IEUStudiaNotify{id(self):x}{next(_SERIAL)}", title="IEUStudia notification")
                threading.Thread(target=tray.run, name="notifier", daemon=True).start()
                tray.ready.wait(3)
                self._tray = tray
            self._tray.notify(title, text, payload)
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self.KEEP_S, self.close)
            self._timer.daemon = True
            self._timer.start()

    def _clicked(self, payload: Optional[Any]) -> None:
        self.close()
        self.on_click(payload)

    def close(self) -> None:
        with self._lock:
            tray, self._tray = self._tray, None
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        if tray is not None:
            tray.stop()
