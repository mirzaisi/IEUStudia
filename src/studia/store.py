"""Canonical transcript / slide-index files in the app folder (the Markdown in the output folder is derived)."""

from __future__ import annotations

import json
import os
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Optional

from .domain import Transcript
from .paths import transcripts_dir
from .slides import Slide, load_index, save_index

_lock = threading.Lock()
_cache: "OrderedDict[str, Transcript]" = OrderedDict()
_MAX = 80           # a whole big course (~55 lectures in two terms of one section): ~0.5 MB each with its paragraphs
_slide_ids: dict[Path, tuple[int, frozenset[str]]] = {}     # transcripts folder -> (its mtime, ids with slides)


def transcript_path(delivery_id: str) -> Path:
    return transcripts_dir() / f"{delivery_id}.json"


def slides_path(delivery_id: str) -> Path:
    return transcripts_dir() / f"{delivery_id}.slides.json"


def save_transcript(t: Transcript) -> Path:
    path = transcript_path(t.delivery_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as f:             # on the disk before it takes the old one's place (a power cut then)
        f.write(json.dumps(t.to_json(), ensure_ascii=False).encode("utf-8"))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    with _lock:
        _cache[t.delivery_id] = t
        _cache.move_to_end(t.delivery_id)
    return path


def load_transcript(delivery_id: str) -> Optional[Transcript]:
    with _lock:
        if delivery_id in _cache:
            _cache.move_to_end(delivery_id)
            return _cache[delivery_id]
    path = transcript_path(delivery_id)
    if not path.exists():
        return None
    try:
        t = Transcript.from_json(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return None
    with _lock:
        _cache[delivery_id] = t
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
        _cache.pop(delivery_id, None)
