"""The start-up window: a small native window in its own process. It appears a fraction of a second after the click
and keeps animating while the main process loads .NET and WebView2 (which can hold Python's interpreter lock for
seconds), and closes as soon as IEUStudia's real window is visible. If that never happens it says so and offers to
restart IEUStudia (and restarts it once by itself after a while).

The app notes its start-up phase in a small JSON file (``--marker``). Creating the WinForms window has been seen to
hang inside Windows (CreateWindowEx waiting for an ALPC reply that never comes); when the window is still missing
WINDOW_STALL_S after the app began creating it, this window saves diagnostics (``--logs``: startup-stall.txt with
the app's loaded modules, startup-stall.dmp with its thread stacks) and restarts the app at once.

Run as ``pythonw -I -S splash.py <app pid> <light|dark> --python <app's python> [--marker <file>] [--logs <dir>]
[--recovered]``: pure Win32 through ctypes and the standard library only, so it starts in a blink.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import subprocess
import sys
import time
from pathlib import Path

APP_NAME = "IEUStudia"
CLASS_NAME = "StudiaSplash"
WIDTH, HEIGHT = 380, 236                  # logical pixels
TICK_MS = 30
SLOW_S, STUCK_S, AUTO_RESTART_S = 12.0, 30.0, 75.0
WINDOW_STALL_S = 15.0                     # creating the window normally takes 1-2 s, even on a cold start
ID_RESTART, ID_CLOSE = 101, 102

# The start-up window runs with the standard library only (-I -S), so its few texts carry their own Turkish here;
# the app passes --lang. tests/test_i18n.py checks that every text given to _() has an entry.
TEXT_TR = {
    "Starting…": "Açılıyor…",
    "IEUStudia is not responding": "IEUStudia yanıt vermiyor",
    "It got stuck while opening its window.": "Penceresini açarken takıldı.",
    "Still starting…": "Hâlâ açılıyor…",
    "The first start after an update can take a little longer.": "Güncellemeden sonraki ilk açılış biraz uzun sürebilir.",
    "Restart IEUStudia": "Yeniden başlat",
    "Close": "Kapat",
    "IEUStudia is stuck again": "IEUStudia yine takıldı",
    "Windows did not finish creating its window.": "Windows pencereyi oluşturmayı bitiremedi.",
    "IEUStudia got stuck opening its window": "IEUStudia penceresini açarken takıldı",
    "Restarting it…": "Yeniden başlatılıyor…",
    "Restarting IEUStudia…": "IEUStudia yeniden başlatılıyor…",
    "IEUStudia closed while it was starting": "IEUStudia açılırken kapandı",
    "Your antivirus may have stopped it: allow it there.": "Antivirüsün durdurmuş olabilir: orada izin ver.",
}
LANG = "en"


def _(text: str) -> str:
    return TEXT_TR.get(text, text) if LANG == "tr" else text

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32")
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
ENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

WM_DESTROY, WM_PAINT, WM_CLOSE, WM_TIMER, WM_COMMAND, WM_SETFONT, WM_ERASEBKGND = 0x2, 0xF, 0x10, 0x113, 0x111, 0x30, 0x14
WM_SETICON, WM_NCHITTEST, HTCAPTION = 0x80, 0x84, 2
WS_POPUP, WS_VISIBLE, WS_CHILD, BS_PUSHBUTTON = 0x80000000, 0x10000000, 0x40000000, 0
WS_EX_APPWINDOW = 0x40000
CS_DROPSHADOW = 0x20000
IDC_APPSTARTING = 32650


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("style", wt.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON), ("hCursor", wt.HANDLE),
                ("hbrBackground", wt.HBRUSH), ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR),
                ("hIconSm", wt.HICON)]


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [("hdc", wt.HDC), ("fErase", wt.BOOL), ("rcPaint", wt.RECT), ("fRestore", wt.BOOL),
                ("fIncUpdate", wt.BOOL), ("rgbReserved", ctypes.c_byte * 32)]


def _sigs() -> None:
    user32.DefWindowProcW.restype = LRESULT
    user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    user32.CreateWindowExW.restype = wt.HWND
    user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
    user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
    user32.LoadCursorW.restype = wt.HANDLE
    user32.LoadCursorW.argtypes = [wt.HINSTANCE, ctypes.c_void_p]
    user32.LoadImageW.restype = wt.HANDLE
    user32.LoadImageW.argtypes = [wt.HINSTANCE, wt.LPCWSTR, wt.UINT, ctypes.c_int, ctypes.c_int, wt.UINT]
    user32.SendMessageW.restype = LRESULT
    user32.SendMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    user32.BeginPaint.restype = wt.HDC
    user32.BeginPaint.argtypes = [wt.HWND, ctypes.POINTER(PAINTSTRUCT)]
    user32.EndPaint.argtypes = [wt.HWND, ctypes.POINTER(PAINTSTRUCT)]
    user32.FillRect.argtypes = [wt.HDC, ctypes.POINTER(wt.RECT), wt.HBRUSH]
    user32.FrameRect.argtypes = [wt.HDC, ctypes.POINTER(wt.RECT), wt.HBRUSH]
    user32.DrawTextW.argtypes = [wt.HDC, wt.LPCWSTR, ctypes.c_int, ctypes.POINTER(wt.RECT), wt.UINT]
    user32.DrawIconEx.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, wt.HICON, ctypes.c_int, ctypes.c_int, wt.UINT,
                                  wt.HBRUSH, wt.UINT]
    user32.InvalidateRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT), wt.BOOL]
    user32.SetTimer.argtypes = [wt.HWND, ctypes.c_size_t, wt.UINT, ctypes.c_void_p]
    user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
    user32.IsHungAppWindow.argtypes = [wt.HWND]
    gdi32.CreateSolidBrush.restype = wt.HBRUSH
    gdi32.CreateFontW.restype = wt.HFONT
    gdi32.CreateFontW.argtypes = [ctypes.c_int] * 5 + [wt.DWORD] * 8 + [wt.LPCWSTR]
    gdi32.SelectObject.restype = wt.HGDIOBJ
    gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
    gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
    gdi32.CreateCompatibleDC.restype = wt.HDC
    gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
    gdi32.CreateCompatibleBitmap.restype = wt.HBITMAP
    gdi32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.BitBlt.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HDC, ctypes.c_int,
                             ctypes.c_int, wt.DWORD]
    gdi32.DeleteDC.argtypes = [wt.HDC]
    gdi32.SetTextColor.argtypes = [wt.HDC, wt.COLORREF]
    gdi32.SetBkMode.argtypes = [wt.HDC, ctypes.c_int]
    kernel32.OpenProcess.restype = wt.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
    kernel32.CloseHandle.argtypes = [wt.HANDLE]


def _rgb(hex_color: str) -> int:
    v = int(hex_color.lstrip("#"), 16)
    return ((v & 0xFF) << 16) | (v & 0xFF00) | ((v >> 16) & 0xFF)          # COLORREF is 0x00BBGGRR


THEMES = {
    "light": {"bg": "#ffffff", "frame": "#e4e4e7", "text": "#0a0a0a", "sub": "#71717a", "track": "#ececef", "bar": "#0a0a0a"},
    "dark": {"bg": "#0a0a0a", "frame": "#27272a", "text": "#fafafa", "sub": "#a1a1aa", "track": "#27272a", "bar": "#fafafa"},
}


def _alive(pid: int) -> bool:
    h = kernel32.OpenProcess(0x00100000, False, pid)         # SYNCHRONIZE
    if not h:
        return False
    try:
        return kernel32.WaitForSingleObject(h, 0) == 0x102     # WAIT_TIMEOUT: still running
    finally:
        kernel32.CloseHandle(h)


def main_window_state(pid: int) -> str:
    """"shown" when the app's main window is visible and answering, "hung" when it exists but does not answer,
    else "" (not created yet)."""
    found = [""]

    def cb(hwnd: int, _: int) -> bool:
        owner = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value != pid:
            return True
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        title = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, title, 256)
        if "WindowsForms" in cls.value and title.value == APP_NAME:
            if user32.IsHungAppWindow(hwnd):
                found[0] = "hung"
            elif user32.IsWindowVisible(hwnd):
                found[0] = "shown"
                return False
        return True

    user32.EnumWindows(ENUMPROC(cb), 0)
    return found[0]


class Splash:
    def __init__(self, app_pid: int, theme: str, recovered: bool, python: str = "", marker: str = "",
                 logs: str = "", frozen: bool = False) -> None:
        self.app_pid = app_pid
        self.python = python or sys.executable
        self.frozen = frozen                 # a release build: the app is IEUStudia.exe itself
        self.marker = Path(marker) if marker else None
        self.logs = Path(logs) if logs else None
        self.stall_handled = False
        self.colors = {k: _rgb(v) for k, v in THEMES.get(theme, THEMES["light"]).items()}
        self.recovered = recovered
        self.started = time.monotonic()
        self.phase = 0.0
        self.ticks = 0
        self.status = _("Starting…")
        self.detail = ""
        self.closing_in = -1
        self.buttons: list[int] = []
        self.restarting = False
        self.vanished = False                # the app ended before its window showed, without a word
        self.hwnd = 0
        self.dpi = 96
        self.fonts: dict[str, int] = {}
        self.icon = 0
        self._proc = WNDPROC(self._wndproc)                    # keep a reference: the window calls back into it

    def s(self, v: float) -> int:
        return int(round(v * self.dpi / 96))

    # ---- window
    def create(self) -> None:
        _sigs()
        try:                                                    # crisp on high-DPI screens
            user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except (AttributeError, OSError):
            pass
        try:                                                    # same taskbar button as the app
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Studia.App")
        except (AttributeError, OSError):
            pass
        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.style = CS_DROPSHADOW
        wc.lpfnWndProc = self._proc
        wc.hInstance = hinst
        wc.hCursor = user32.LoadCursorW(None, ctypes.c_void_p(IDC_APPSTARTING))   # the "working" pointer
        wc.lpszClassName = CLASS_NAME
        user32.RegisterClassExW(ctypes.byref(wc))
        try:
            self.dpi = user32.GetDpiForSystem() or 96
        except (AttributeError, OSError):
            self.dpi = 96
        w, h = self.s(WIDTH), self.s(HEIGHT)
        work = wt.RECT()
        user32.SystemParametersInfoW(0x30, 0, ctypes.byref(work), 0)          # SPI_GETWORKAREA
        x = work.left + (work.right - work.left - w) // 2
        y = work.top + (work.bottom - work.top - h) // 2
        self.hwnd = user32.CreateWindowExW(WS_EX_APPWINDOW, CLASS_NAME, APP_NAME, WS_POPUP, x, y, w, h,
                                           None, None, hinst, None)
        try:                                                    # rounded corners on Windows 11
            ctypes.windll.dwmapi.DwmSetWindowAttribute(wt.HWND(self.hwnd), 33, ctypes.byref(ctypes.c_int(2)), 4)
        except (AttributeError, OSError):
            pass
        ico = Path(__file__).with_name("assets") / "icon.ico"
        if ico.exists():
            self.icon = user32.LoadImageW(None, str(ico), 1, self.s(72), self.s(72), 0x10)
            for which, size in ((1, 32), (0, 16)):               # ICON_BIG, ICON_SMALL for the taskbar
                small = user32.LoadImageW(None, str(ico), 1, self.s(size), self.s(size), 0x10)
                if small:
                    user32.SendMessageW(self.hwnd, WM_SETICON, which, small)
        face_title = "Segoe UI Variable Display"
        self.fonts = {
            "title": gdi32.CreateFontW(-self.s(26), 0, 0, 0, 600, 0, 0, 0, 1, 0, 0, 5, 0, face_title),
            "status": gdi32.CreateFontW(-self.s(13.5), 0, 0, 0, 400, 0, 0, 0, 1, 0, 0, 5, 0, "Segoe UI"),
            "button": gdi32.CreateFontW(-self.s(13), 0, 0, 0, 400, 0, 0, 0, 1, 0, 0, 5, 0, "Segoe UI"),
        }
        user32.ShowWindow(self.hwnd, 5)                         # SW_SHOW
        user32.UpdateWindow(self.hwnd)
        user32.SetTimer(self.hwnd, 1, TICK_MS, None)

    def loop(self) -> None:
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    # ---- messages
    def _wndproc(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        try:
            if msg == WM_PAINT:
                self._paint(hwnd)
                return 0
            if msg == WM_ERASEBKGND:
                return 1
            if msg == WM_TIMER:
                self._tick()
                return 0
            if msg == WM_NCHITTEST:                             # drag it anywhere
                hit = user32.DefWindowProcW(hwnd, msg, wparam, lparam)
                return HTCAPTION if hit == 1 else hit
            if msg == WM_COMMAND:
                if wparam & 0xFFFF == ID_RESTART:
                    self.restart()
                elif wparam & 0xFFFF == ID_CLOSE:
                    self.quit_app()
                return 0
            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
        except Exception:  # noqa: BLE001 - a splash must never take the app down
            pass
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _tick(self) -> None:
        self.phase = (self.phase + TICK_MS / 1100.0) % 1.0
        self.ticks += 1
        elapsed = time.monotonic() - self.started
        if self.closing_in >= 0:
            self.closing_in -= 1
            if self.closing_in < 0:
                user32.DestroyWindow(self.hwnd)
            return
        if self.ticks % 6 == 0 and not self.vanished:         # every ~180 ms: is the app up yet?
            if not self.restarting and not _alive(self.app_pid):
                if self.ended_quietly():                      # gone without a word (an antivirus ended it, say)
                    self.on_vanished()
                else:
                    user32.DestroyWindow(self.hwnd)           # it closed (an error message shows why)
                return
            state = main_window_state(self.app_pid)
            if state == "shown":
                self.closing_in = 5                            # let its first frame paint, then go
            elif state == "" and not self.stall_handled and not self.restarting and self.window_stalled():
                self.on_stall()                                # no window at all (a busy one shows as "hung")
            elif elapsed > STUCK_S and not self.buttons:
                self.status = _("IEUStudia is not responding")
                self.detail = _("It got stuck while opening its window.")
                self._show_buttons()
            elif elapsed > SLOW_S and not self.buttons:
                self.status = _("Still starting…")
                self.detail = _("The first start after an update can take a little longer.")
            if self.buttons and elapsed > AUTO_RESTART_S and not self.recovered and state != "shown":
                self.restart()
        r = wt.RECT(0, self.s(150), self.s(WIDTH), self.s(HEIGHT))
        user32.InvalidateRect(self.hwnd, ctypes.byref(r), False)

    def _show_buttons(self) -> None:
        y, bw, bh, gap = self.s(186), self.s(132), self.s(30), self.s(10)
        x0 = (self.s(WIDTH) - 2 * bw - gap) // 2
        for i, (bid, text) in enumerate(((ID_RESTART, _("Restart IEUStudia")), (ID_CLOSE, _("Close")))):
            b = user32.CreateWindowExW(0, "BUTTON", text, WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON, x0 + i * (bw + gap), y,
                                       bw, bh, self.hwnd, wt.HMENU(bid), None, None)
            user32.SendMessageW(b, WM_SETFONT, self.fonts["button"], 1)
            self.buttons.append(b)
        user32.InvalidateRect(self.hwnd, None, True)

    def _paint(self, hwnd: int) -> None:
        ps = PAINTSTRUCT()
        hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
        w, h = self.s(WIDTH), self.s(HEIGHT)
        mem = gdi32.CreateCompatibleDC(hdc)                     # draw off-screen: no flicker
        bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
        old_bmp = gdi32.SelectObject(mem, bmp)
        self.draw(mem, w, h)
        gdi32.BitBlt(hdc, 0, 0, w, h, mem, 0, 0, 0x00CC0020)  # SRCCOPY
        gdi32.SelectObject(mem, old_bmp)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.EndPaint(hwnd, ctypes.byref(ps))

    def draw(self, mem: int, w: int, h: int) -> None:
        c = self.colors
        brushes = {k: gdi32.CreateSolidBrush(c[k]) for k in ("bg", "frame", "track", "bar")}
        full = wt.RECT(0, 0, w, h)
        user32.FillRect(mem, ctypes.byref(full), brushes["bg"])
        user32.FrameRect(mem, ctypes.byref(full), brushes["frame"])
        if self.icon:
            size = self.s(72)
            user32.DrawIconEx(mem, (w - size) // 2, self.s(30), self.icon, size, size, 0, None, 3)
        gdi32.SetBkMode(mem, 1)                                 # TRANSPARENT
        old_font = gdi32.SelectObject(mem, self.fonts["title"])
        gdi32.SetTextColor(mem, c["text"])
        r = wt.RECT(0, self.s(110), w, self.s(146))
        user32.DrawTextW(mem, APP_NAME, -1, ctypes.byref(r), 0x1 | 0x4 | 0x20)       # center, vcenter, single line
        gdi32.SelectObject(mem, self.fonts["status"])
        gdi32.SetTextColor(mem, c["text"] if self.buttons else c["sub"])
        r = wt.RECT(self.s(16), self.s(150), w - self.s(16), self.s(170))
        user32.DrawTextW(mem, self.status, -1, ctypes.byref(r), 0x1 | 0x4 | 0x20)
        if self.detail:
            gdi32.SetTextColor(mem, c["sub"])
            r = wt.RECT(self.s(16), self.s(168), w - self.s(16), self.s(186))
            user32.DrawTextW(mem, self.detail, -1, ctypes.byref(r), 0x1 | 0x4 | 0x20)
        if not self.buttons:                                    # indeterminate progress bar
            bw, bh, by = self.s(180), self.s(3), self.s(196)
            bx = (w - bw) // 2
            track = wt.RECT(bx, by, bx + bw, by + bh)
            user32.FillRect(mem, ctypes.byref(track), brushes["track"])
            seg = int(bw * 0.36)
            pos = int((bw + seg) * self.phase) - seg
            left, right = max(bx, bx + pos), min(bx + bw, bx + pos + seg)
            if right > left:
                user32.FillRect(mem, ctypes.byref(wt.RECT(left, by, right, by + bh)), brushes["bar"])
        gdi32.SelectObject(mem, old_font)
        for b in brushes.values():
            gdi32.DeleteObject(b)

    # ---- recovery
    def window_stalled(self) -> bool:
        """The app began creating its window (marker phase "window") more than WINDOW_STALL_S ago."""
        if self.marker is None:
            return False
        try:
            d = json.loads(self.marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return (isinstance(d, dict) and d.get("pid") == self.app_pid and d.get("phase") == "window"
                and time.time() - float(d.get("phase_at") or 0) > WINDOW_STALL_S)

    def ended_quietly(self) -> bool:
        """The app ended before its window showed and didn't say why: an error it shows marks the start-up phase
        "crashed" first (app.py), so its own message box explains instead. Without a marker it can't be told."""
        if self.marker is None:
            return False
        try:
            d = json.loads(self.marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return True
        return not (isinstance(d, dict) and d.get("pid") == self.app_pid and d.get("phase") in ("crashed", "shown"))

    def on_vanished(self) -> None:
        """Said, with Restart and Close: before, the start-up window just disappeared and nothing else came."""
        self.vanished = True
        self.status = _("IEUStudia closed while it was starting")
        self.detail = _("Your antivirus may have stopped it: allow it there.")
        if not self.buttons:
            self._show_buttons()

    def on_stall(self) -> None:
        self.stall_handled = True
        save_diagnostics(self.app_pid, self.logs)
        if self.recovered:                                     # stuck again right after a restart: let the user decide
            self.status = _("IEUStudia is stuck again")
            self.detail = _("Windows did not finish creating its window.")
            if not self.buttons:
                self._show_buttons()
        else:
            self.status = _("IEUStudia got stuck opening its window")
            self.detail = _("Restarting it…")
            self.restart()

    def restart(self) -> None:
        """End the stuck copy (and its WebView2 helpers) and start a fresh one."""
        if self.restarting:
            return
        self.restarting = True
        self.status, self.detail = _("Restarting IEUStudia…"), ""
        for b in self.buttons:
            user32.DestroyWindow(b)
        self.buttons = []
        _kill_tree(self.app_pid)
        try:
            cmd = [self.python] if self.frozen else [self.python, "-m", "studia.app"]
            subprocess.Popen(cmd + ["--restarted", "--recovered"], close_fds=True)
        except OSError:
            pass
        user32.DestroyWindow(self.hwnd)                         # the new copy shows its own start-up window

    def quit_app(self) -> None:
        _kill_tree(self.app_pid)
        user32.DestroyWindow(self.hwnd)


def save_diagnostics(pid: int, folder: "Path | None") -> None:
    """startup-stall.txt (time, the app's loaded modules: a DLL injected by another program shows up here) and
    startup-stall.dmp (a minidump with every thread's stack, no heap) in ``folder``; best effort, never raises."""
    if folder is None:
        return
    try:
        folder.mkdir(parents=True, exist_ok=True)
        lines = [f"Stuck start-up of pid {pid} at {time.strftime('%Y-%m-%d %H:%M:%S')}", "", "Loaded modules:"]
        lines += _modules(pid)
        (folder / "startup-stall.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:  # noqa: BLE001 - diagnostics only
        pass
    try:
        import msvcrt

        process = kernel32.OpenProcess(0x0400 | 0x0010, False, pid)   # PROCESS_QUERY_INFORMATION | PROCESS_VM_READ
        if not process:
            return
        try:
            dbghelp = ctypes.WinDLL("dbghelp")
            dbghelp.MiniDumpWriteDump.argtypes = [wt.HANDLE, wt.DWORD, wt.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                  ctypes.c_void_p, ctypes.c_void_p]
            with open(folder / "startup-stall.dmp", "wb") as f:
                # MiniDumpNormal | MiniDumpWithUnloadedModules | MiniDumpWithThreadInfo
                dbghelp.MiniDumpWriteDump(process, pid, msvcrt.get_osfhandle(f.fileno()), 0x20 | 0x1000,
                                          None, None, None)
        finally:
            kernel32.CloseHandle(process)
    except Exception:  # noqa: BLE001
        pass


def _modules(pid: int) -> list:
    psapi = ctypes.WinDLL("psapi")
    psapi.EnumProcessModulesEx.argtypes = [wt.HANDLE, ctypes.POINTER(wt.HMODULE), wt.DWORD, ctypes.POINTER(wt.DWORD),
                                           wt.DWORD]
    psapi.GetModuleFileNameExW.argtypes = [wt.HANDLE, wt.HMODULE, wt.LPWSTR, wt.DWORD]
    process = kernel32.OpenProcess(0x0400 | 0x0010, False, pid)
    if not process:
        return ["(cannot open the process)"]
    try:
        mods = (wt.HMODULE * 1024)()
        needed = wt.DWORD()
        if not psapi.EnumProcessModulesEx(process, mods, ctypes.sizeof(mods), ctypes.byref(needed), 0x3):
            return ["(cannot list modules)"]                          # 0x3: LIST_MODULES_ALL
        out = []
        for i in range(min(needed.value // ctypes.sizeof(wt.HMODULE), 1024)):
            buf = ctypes.create_unicode_buffer(520)
            if psapi.GetModuleFileNameExW(process, mods[i], buf, 520):
                out.append(buf.value)
        return out
    finally:
        kernel32.CloseHandle(process)


def _kill_tree(pid: int) -> None:
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, timeout=15,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        pass


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        return 2
    rest = argv[2:]
    def opt(flag: str) -> str:
        return rest[rest.index(flag) + 1] if flag in rest[:-1] else ""

    global LANG
    LANG = opt("--lang") or "en"
    splash = Splash(int(argv[0]), argv[1], "--recovered" in rest, opt("--python"), opt("--marker"), opt("--logs"),
                    "--frozen" in rest)
    splash.create()
    splash.loop()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
