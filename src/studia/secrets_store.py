"""Credentials + session cookies, encrypted for the current Windows user with DPAPI.

Only this Windows account on this PC can decrypt ``secrets.bin``; nothing is ever logged. On Linux the file is
encrypted with a key kept in the desktop's keyring instead (see :mod:`linux_secrets`); without a keyring nothing is
stored, never in plain text.
"""

from __future__ import annotations

import contextlib
import ctypes
import ctypes.wintypes as wt
import hashlib
import json
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Any, Iterator, Optional

from .paths import secrets_path

log = logging.getLogger("studia")
WINDOWS = sys.platform == "win32"
_lock = threading.Lock()
_ENTROPY = b"LectureScribe/secrets/v1"
CRYPTPROTECT_UI_FORBIDDEN = 0x1


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> _Blob:
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def _crypt(data: bytes, protect: bool) -> bytes:
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    src, ent, out = _blob(data), _blob(_ENTROPY), _Blob()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    ok = fn(ctypes.byref(src), None, ctypes.byref(ent), None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out))
    if not ok:
        raise OSError(ctypes.get_last_error(), "DPAPI operation failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def protect(data: bytes) -> bytes:
    if not WINDOWS:
        from .linux_secrets import protect as linux_protect

        return linux_protect(data)
    return _crypt(data, True)


def unprotect(data: bytes) -> bytes:
    if not WINDOWS:
        from .linux_secrets import unprotect as linux_unprotect

        return linux_unprotect(data)
    return _crypt(data, False)


def _file(folder: Optional[Path]) -> Path:
    """The active profile's secrets, or those of the profile in ``folder`` (while it is being added)."""
    return Path(folder) / "secrets.bin" if folder else secrets_path()


if WINDOWS:
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateMutexW.restype = wt.HANDLE
    _k32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
    _k32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
    _k32.ReleaseMutex.argtypes = [wt.HANDLE]
    _k32.CloseHandle.argtypes = [wt.HANDLE]


@contextlib.contextmanager
def _turn(folder: Optional[Path] = None) -> Iterator[None]:
    """One reader-and-writer of ``secrets.bin`` at a time: the threads of this process, and the app and its tray
    helper (two processes) through a named mutex. Without it, one could write back what it read before the other
    saved a new password."""
    if not WINDOWS:
        with _lock, _file_lock(_file(folder)):
            yield
        return
    with _lock:
        name = "Local\\IEUStudia-secrets-" + hashlib.sha1(str(_file(folder)).lower().encode("utf-8")).hexdigest()[:16]
        handle = _k32.CreateMutexW(None, False, name)
        owned = bool(handle) and _k32.WaitForSingleObject(handle, 10_000) in (0, 0x80)   # WAIT_OBJECT_0, _ABANDONED
        try:
            yield
        finally:
            if owned:
                _k32.ReleaseMutex(handle)
            if handle:
                _k32.CloseHandle(handle)


@contextlib.contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    """Linux: the app and its helper take turns through an flock on ``secrets.lock`` next to the file."""
    import fcntl

    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path.with_name(path.name + ".lock"), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)                                     # closing it lets go of the lock


class Unreadable(OSError):
    """secrets.bin is there but couldn't be read or decrypted (a read blocked for a moment, say): it is never written
    over with less than it holds."""


def _load(folder: Optional[Path] = None, strict: bool = False) -> dict[str, Any]:
    """What secrets.bin holds; {} when there is none or it can't be read. ``strict`` (before writing it back): one
    that can't be read raises Unreadable instead, so the username and password in it aren't lost."""
    path = _file(folder)
    if not path.exists():
        return {}
    try:
        data = json.loads(unprotect(path.read_bytes()).decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError) as exc:
        if strict:
            log.warning("Stored credentials could not be read; secrets.bin is left as it is")
            raise Unreadable(f"secrets.bin could not be read: {exc}") from exc
        log.warning("Stored credentials could not be decrypted; they will be asked for again")
        return {}


def _load_for_new(folder: Optional[Path] = None) -> dict[str, Any]:
    """Before the user's own sign-in or sign-out is stored: a secrets.bin that can't be read is kept aside as
    secrets.bin.unreadable, not written over."""
    try:
        return _load(folder, strict=True)
    except Unreadable:
        path = _file(folder)
        os.replace(path, path.with_name(path.name + ".unreadable"))
        log.warning("The unreadable secrets.bin was kept as secrets.bin.unreadable")
        return {}


def _save(data: dict[str, Any], folder: Optional[Path] = None) -> None:
    path = _file(folder)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")          # each process its own
    with open(tmp, "wb") as f:       # on the disk before the swap: a power cut soon after (it is written at each sync)
        f.write(protect(json.dumps(data).encode("utf-8")))      # could leave it with its size but only zeros in it
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _digest(password: str) -> str:
    return hashlib.sha256(_ENTROPY + (password or "").encode("utf-8")).hexdigest()


def _refused(d: dict[str, Any]) -> bool:
    """IEULearn refused the password that is stored now (not an older one)."""
    return bool(d.get("password")) and d.get("rejected") == _digest(d["password"])


def get_credentials() -> Optional[tuple[str, str]]:
    """The stored username and password; None when there are none, or IEULearn refused them (see rejected)."""
    with _turn():
        d = _load()
    u, p = d.get("username"), d.get("password")
    return (u, p) if u and p and not _refused(d) else None


def set_credentials(username: str, password: str, folder: Optional[Path] = None) -> None:
    with _turn(folder):
        d = _load_for_new(folder)
        d.update(username=username, password=password)
        d.pop("rejected", None)
        d.pop("rejected_for", None)
        _save(d, folder)


def mark_rejected(password: str, why: str = "") -> None:
    """IEULearn refused ``password`` (it was changed): stop signing in with it until the user signs in again, so
    nothing keeps trying a wrong password in the background. A newer password stored meanwhile (by the app while
    the tray helper was signing in, say) is left alone, and so is a secrets.bin that can't be read now. ``why``:
    IEULearn took it but wants something done on its site first ("action", "password_change"), kept so that a
    restart says that, not that the password was refused."""
    with _turn():
        try:
            d = _load(strict=True)
        except Unreadable:
            return
        if d.get("password") and d["password"] == password:
            d["rejected"] = _digest(password)
            if why:
                d["rejected_for"] = why
            else:
                d.pop("rejected_for", None)
            _save(d)


def rejected() -> bool:
    with _turn():
        return _refused(_load())


def rejected_for() -> str:
    """Why the saved password isn't used: "" (IEULearn refused it), or what IEULearn wanted first (mark_rejected)."""
    with _turn():
        d = _load()
    return str(d.get("rejected_for") or "") if _refused(d) else ""


def clear_credentials() -> None:
    with _turn():
        d = _load_for_new()
        d.pop("username", None)
        d.pop("password", None)
        d.pop("rejected", None)
        d.pop("rejected_for", None)
        _save(d)


def load_cookies() -> list[dict[str, Any]]:
    with _turn():
        cookies = _load().get("cookies") or []
    return [c for c in cookies if isinstance(c, dict) and c.get("name")]


def save_cookies(cookies: list[dict[str, Any]], folder: Optional[Path] = None) -> None:
    """Raises Unreadable (an OSError: the caller logs it, and tries again with the next cookies) rather than write
    a secrets.bin that can't be read now over with cookies only."""
    with _turn(folder):
        d = _load(folder, strict=True)
        d["cookies"] = cookies
        _save(d, folder)


def clear_all() -> None:
    with _turn():
        try:
            secrets_path().unlink()
        except FileNotFoundError:
            pass
