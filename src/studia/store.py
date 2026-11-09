"""Canonical transcript / slide-index files in the app folder (the Markdown in the output folder is derived)."""

from __future__ import annotations

import json
import os
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Optional

from .domain import Transcript
from .paths import transcripts_dir
from .slides import Slide, load_index, save_index

_lock = threading.Lock()
# transcripts read or saved lately, by their file (not only their id: the same id in another profile's or home's
# folder is another transcript)
_cache: "OrderedDict[str, Transcript]" = OrderedDict()
_MAX = 80           # a whole big course (~55 lectures in two terms of one section): ~0.5 MB each with its paragraphs
_slide_ids: dict[Path, tuple[int, frozenset[str]]] = {}     # transcripts folder -> (its mtime, ids with slides)


def transcript_path(delivery_id: str) -> Path:
    return transcripts_dir() / f"{delivery_id}.json"


def slides_path(delivery_id: str) -> Path:
    return transcripts_dir() / f"{delivery_id}.slides.json"


def screens_path(delivery_id: str) -> Path:
    """The recording's screen timeline (screens.py): each screen's title, text and where they came from."""
    return transcripts_dir() / f"{delivery_id}.screens.json"


SWAP_TRIES, SWAP_WAIT = 8, 0.25     # a file held by another program: tried again for about two seconds


def _swap_in(tmp: Path, path: Path) -> None:
    """The new file takes the old one's place. An antivirus or another program reading the old transcript can hold it
    for a moment: the swap is tried again for about two seconds (as outputs.writer.atomic_write does)."""
    for attempt in range(SWAP_TRIES):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == SWAP_TRIES - 1:
                try:
                    tmp.unlink()
                except OSError:
                    pass
                raise
            time.sleep(SWAP_WAIT)


def save_transcript(t: Transcript) -> Path:
    path = transcript_path(t.delivery_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as f:             # on the disk before it takes the old one's place (a power cut then)
        f.write(json.dumps(t.to_json(), ensure_ascii=False).encode("utf-8"))
        f.flush()
        os.fsync(f.fileno())
    _swap_in(tmp, path)
    with _lock:
        _cache[str(path)] = t
        _cache.move_to_end(str(path))
    return path


def load_transcript(delivery_id: str) -> Optional[Transcript]:
    path = transcript_path(delivery_id)
    key = str(path)
    with _lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    if not path.exists():
        return None
    try:
        t = Transcript.from_json(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return None
    with _lock:
        _cache[key] = t
        while len(_cache) > _MAX:
            _cache.popitem(last=False)
    return t


def load_transcripts(ids) -> dict[str, Transcript]:
    out = {}
    for i in ids:
        t = load_transcript(i)
        if t is not None:
            out[i] = t
    return out


def save_slides(delivery_id: str, slides: list[Slide]) -> None:
    path = slides_path(delivery_id)
    save_index(path, slides)
    with _lock:                         # known at once, even when the folder's time didn't move (same clock tick)
        known = _slide_ids.get(path.parent)
        if known is not None:
            _slide_ids[path.parent] = (known[0], known[1] | {delivery_id})


def load_slides(delivery_id: str) -> list[Slide]:
    p = slides_path(delivery_id)
    return [s for s in load_index(p) if not s.path or os.path.isfile(s.path)] if p.exists() else []


def save_screens(delivery_id: str, doc: dict) -> None:
    """The screen timeline, whole or not at all (written as slides.save_index writes the slide index)."""
    path = screens_path(delivery_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(json.dumps(doc, ensure_ascii=False, indent=1).encode("utf-8"))
        f.flush()
        os.fsync(f.fileno())
    _swap_in(tmp, path)
    with _lock:
        _screens.pop(str(path), None)


_screens: "OrderedDict[str, tuple[int, dict]]" = OrderedDict()     # its file -> (the file's mtime, its timeline)


def load_screens(delivery_id: str) -> Optional[dict]:
    """The screen timeline ({"v", "made", "times", "screens": {"12": {"title", "text", "from", ...}}}), None when the
    recording has none (or it can't be read). Read once per change of the file."""
    path = screens_path(delivery_id)
    try:
        stamp = path.stat().st_mtime_ns
    except OSError:
        return None
    with _lock:
        hit = _screens.get(str(path))
        if hit is not None and hit[0] == stamp:
            _screens.move_to_end(str(path))
            return hit[1]
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or not isinstance(doc.get("screens"), dict):
        return None
    with _lock:
        _screens[str(path)] = (stamp, doc)
        while len(_screens) > 4 * _MAX:
            _screens.popitem(last=False)
    return doc


def slide_ids() -> frozenset[str]:
    """The recordings that have a slide index. The folder is listed again only when something in it changed (its
    modified time follows every file made or replaced in it, by this process or another)."""
    folder = transcripts_dir()
    try:
        stamp = folder.stat().st_mtime_ns
    except OSError:
        return frozenset()
    with _lock:
        known = _slide_ids.get(folder)
    if known is not None and known[0] == stamp:
        return known[1]
    ids = frozenset(p.name.split(".")[0] for p in folder.glob("*.slides.json"))
    with _lock:
        _slide_ids[folder] = (stamp, ids)
    return ids


def forget(delivery_id: str) -> None:
    with _lock:
        _cache.pop(str(transcript_path(delivery_id)), None)
        _screens.pop(str(screens_path(delivery_id)), None)
