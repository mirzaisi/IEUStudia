"""Filesystem locations used by IEUStudia.

Everything lives under ``%LOCALAPPDATA%\\IEUStudia`` unless the ``STUDIA_HOME``
environment variable points somewhere else (tests and portable setups use that).
Every helper creates its directory on first use, so callers never need to ``mkdir``.

The app used to be called Studia, and LectureScribe before that: :func:`migrate_legacy_data` moves the data folder
of an earlier name once, and :func:`renamed_output_path` gives their default output folders the new name.
"""

from __future__ import annotations

import ctypes
import functools
import os
import re
import shutil
import sys
import uuid
from pathlib import Path

APP_NAME = "IEUStudia"                        # the data folder in %LOCALAPPDATA% and the output folder in Documents
LEGACY_NAMES = ("Studia", "LectureScribe")    # the app's earlier names, newest first
HOME_ENV = "STUDIA_HOME"

# Known-folder id of the user's Documents folder (follows OneDrive / folder redirection).
_FOLDERID_DOCUMENTS = uuid.UUID("FDD39AD0-238F-46AF-ADB4-6C85480369C7")


def _ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


_made: set[Path] = set()      # data folders made (or found) already


def _data_dir(path: Path) -> Path:
    """:func:`_ensure_dir`, once per folder: the data folders are looked up all the time (a transcript's path for
    each lecture of a course), and a mkdir costs a failing CreateDirectory plus a stat each time. Whatever writes a
    file still makes its own parent folder."""
    if path not in _made:
        _ensure_dir(path)
        _made.add(path)
    return path


def _home_override() -> Path | None:
    value = os.environ.get(HOME_ENV, "").strip()
    return Path(value).expanduser() if value else None


def _local_root() -> Path:
    if sys.platform != "win32":                  # Linux: ~/.local/share (XDG_DATA_HOME)
        data = os.environ.get("XDG_DATA_HOME", "").strip()
        return Path(data) if data else Path.home() / ".local" / "share"
    local = os.environ.get("LOCALAPPDATA", "").strip()
    return Path(local) if local else Path.home() / "AppData" / "Local"


def _legacy_data() -> Path | None:
    """The data folder of an earlier name that still holds a library (the newest such name)."""
    root = _local_root()
    return next((root / name for name in LEGACY_NAMES if (root / name / "library.db").exists()), None)


def _prune_empty(path: Path) -> None:
    """Remove the empty folders in ``path``, and ``path`` itself if nothing else is in it (never a file)."""
    for d in sorted((p for p in path.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True) + [path]:
        try:
            d.rmdir()
        except OSError:
            pass


def migrate_legacy_data() -> str:
    """Move the data folder of the app's earlier name (``%LOCALAPPDATA%\\Studia`` or ``LectureScribe``) to
    ``%LOCALAPPDATA%\\IEUStudia`` (once, before anything uses it).

    Same drive, so it is a rename. A new folder that already exists without a library is dropped if it holds only
    empty folders; otherwise (e.g. logs) the old folder's entries are moved into it, the library last, so that the
    old folder stays in use, and the move is tried again next time, until everything else has moved.
    Returns "moved", "none", "skipped" or "failed: …"."""
    if _home_override() is not None:
        return "skipped"
    new, old = _local_root() / APP_NAME, _legacy_data()
    if old is None or (new / "library.db").exists():
        return "none"
    try:
        if new.is_dir():
            _prune_empty(new)
        if not new.exists():
            os.replace(old, new)
        else:
            for child in sorted(old.iterdir(), key=lambda c: c.name.startswith("library.db")):
                target = new / child.name
                if target.is_dir():
                    _prune_empty(target)
                if not target.exists():
                    shutil.move(str(child), str(target))
            if not any(old.iterdir()):
                old.rmdir()
        _legacy_in_use.cache_clear()
        return "moved"
    except OSError as exc:            # e.g. the old app is still running: keep using its folder for now
        return f"failed: {exc}"
    finally:
        _made.clear()                 # folders were moved or removed: make them again where needed


@functools.lru_cache(maxsize=1)
def _legacy_in_use() -> Path | None:
    """The data folder of an earlier name while it could not be moved yet (e.g. the old app was still running)."""
    return None if (_local_root() / APP_NAME / "library.db").exists() else _legacy_data()


def app_dir() -> Path:
    """``%LOCALAPPDATA%\\IEUStudia`` (or ``$STUDIA_HOME``; the folder of an earlier name until it is moved)."""
    override = _home_override()
    if override is not None:
        return _data_dir(override)
    return _data_dir(_legacy_in_use() or _local_root() / APP_NAME)


# Each IEULearn account ("profile") has its own library, transcripts, settings and stored password. The first one
# uses the app folder itself; added ones live in profiles\<id> (see accounts.py). Models, logs and caches are shared.
_profile_rel = ""


def set_profile_dir(rel: str = "") -> None:
    """Make the data helpers below point at an account's folder (relative to the app folder; "" = the app folder)."""
    global _profile_rel
    _profile_rel = (rel or "").replace("\\", "/").strip("/")


def profile_dir() -> Path:
    """The active account's data folder."""
    return _data_dir(app_dir() / _profile_rel) if _profile_rel else app_dir()


def settings_path() -> Path:
    return profile_dir() / "settings.json"


def secrets_path() -> Path:
    return profile_dir() / "secrets.bin"


def db_path() -> Path:
    return profile_dir() / "library.db"


def transcripts_dir() -> Path:
    """Canonical ``<delivery_id>.json`` transcript per recording."""
    return _data_dir(profile_dir() / "transcripts")


def audio_cache_dir() -> Path:
    """The active account's downloaded lecture audio. Each account has its own: two students on one PC who follow the
    same course download the same recordings, and one's start-up sweep (or a lecture done or cancelled) deleted the
    other's kept or queued audio. The first account's is the app folder's, as before."""
    return _ensure_dir(profile_dir() / "cache" / "audio")


def shared_audio_dir() -> Path:
    """Where every account's downloaded audio went before 4.0: the first account's own folder now (not made here)."""
    return app_dir() / "cache" / "audio"


def write_durably(path: Path, data: bytes) -> None:
    """A small file the app can't do without (the profiles' list, the app's own settings): written to a temporary
    file, on the disk, then put in the old one's place. Without the flush, a power cut or a crash soon after could bring
    it back with its size but only zeros in it (NTFS keeps a file's name and size safe, not its contents)."""
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def logs_dir() -> Path:
    return _ensure_dir(app_dir() / "logs")


@functools.lru_cache(maxsize=1)
def documents_dir() -> Path:
    """The user's real Documents folder (honours OneDrive/known-folder redirection; on Linux, xdg-user-dirs)."""
    if sys.platform != "win32":
        import subprocess

        try:
            out = subprocess.run(["xdg-user-dir", "DOCUMENTS"], capture_output=True, text=True, timeout=5).stdout.strip()
            if out and Path(out) != Path.home():     # xdg-user-dir answers $HOME when the folder isn't set up
                return Path(out)
        except (OSError, subprocess.SubprocessError):
            pass
        return Path.home() / "Documents"
    try:
        guid = ctypes.create_string_buffer(_FOLDERID_DOCUMENTS.bytes_le, 16)
        out = ctypes.c_void_p()
        shell32 = ctypes.WinDLL("shell32")
        ole32 = ctypes.WinDLL("ole32")
        hr = shell32.SHGetKnownFolderPath(guid, 0, None, ctypes.byref(out))
        try:
            if hr == 0 and out.value:
                return Path(ctypes.wstring_at(out.value))
        finally:
            if out.value:
                ole32.CoTaskMemFree(out)
    except (AttributeError, OSError):  # not on Windows / API unavailable
        pass
    return Path.home() / "Documents"


def profile_output_name(label: str) -> str:
    """The name of an added profile's own output folder in Documents: ``IEUStudia - <label>``."""
    return f"{APP_NAME} - {label}"


def default_output_path() -> Path:
    """:func:`default_output_dir` without creating it."""
    if _home_override() is not None:
        return profile_dir() / "output"
    if _profile_rel:
        return documents_dir() / profile_output_name(Path(_profile_rel).name)
    return documents_dir() / APP_NAME


def default_output_dir() -> Path:
    """Where Markdown transcripts go by default: ``Documents\\IEUStudia`` (an added profile gets its own folder,
    ``IEUStudia - <name>``, when it is created; this is only the fallback).

    When ``STUDIA_HOME`` is set (tests, portable mode) the output goes to
    ``<home>\\output`` (``<profile folder>\\output``) instead, so test runs never touch the real Documents folder.
    """
    return _ensure_dir(default_output_path())


# Studia, LectureScribe, a profile's "Studia - <name>" and the fallback "Studia (<profile id>)"
_LEGACY_OUTPUT = re.compile(r"(?:%s)(?: - (.+)| \((p-[0-9a-f]{8})\))?" % "|".join(LEGACY_NAMES))


def renamed_output_path(path: Path) -> Path | None:
    """The IEUStudia name for a default output folder of the app's earlier names: ``Documents\\Studia`` (or
    ``LectureScribe``) becomes ``Documents\\IEUStudia``, a profile's ``Studia - <name>`` becomes ``IEUStudia - <name>``.
    None for any other folder (one the user chose elsewhere, or one that already has the new name)."""
    path = Path(path)
    m = _LEGACY_OUTPUT.fullmatch(path.name)
    if m is None or path.parent not in (documents_dir(), Path.home() / "Documents"):
        return None
    label = m.group(1) or m.group(2)
    return path.with_name(profile_output_name(label) if label else APP_NAME)
