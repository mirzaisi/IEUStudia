"""A lecture's finished passages on disk, so a transcription stopped halfway (IEUStudia closed, the PC lost power)
goes on from them instead of from the start.

Only where that gives the very transcript an uninterrupted run gives. Passages don't depend on each other: each one
is decoded in each language with the same fixed prompt (core.PROMPTS, never the text before it), and its
language-ID prior comes from its own encoder pass. What does depend on the whole lecture (its main language, the
choice of language per passage, the second look at unclear passages) runs after the last passage, from the
candidates kept here. Batches are kept as they were made (on the graphics card a batch of other passages can change
the last digits of a result), with the size the next one had, so the passages left are batched as they would have
been. Anything that changes a result changes the key, and a checkpoint with another key is started again: the
recording's samples, the model, the device and number format, the languages, the beam and batch sizes, the versions
of IEUStudia and of CTranslate2.

The file: a first line with the key, then one line per batch, appended and flushed to the disk as each batch ends.
A line cut short by a power cut is dropped (and the batches after it, if any)."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Optional, Sequence

log = logging.getLogger("studia")
VERSION = 1
KEEP_DAYS = 14          # one never continued (the lecture was forgotten): deleted after this long


def folder() -> Path:
    from ..paths import app_dir

    return app_dir() / "cache" / "transcribe"


def path_for(delivery_id: str) -> Path:
    name = delivery_id if re.fullmatch(r"[\w-]{1,80}", delivery_id) else hashlib.sha1(delivery_id.encode()).hexdigest()
    return folder() / f"{name}.jsonl"


def audio_digest(audio: Any) -> str:
    """The recording's samples (the same file decodes to the same samples). ~0.3 s for 90 minutes."""
    import numpy as np

    a = np.ascontiguousarray(audio)
    h = hashlib.blake2b(digest_size=16)
    h.update(f"{a.dtype}:{a.shape}".encode())
    h.update(memoryview(a).cast("B"))
    return h.hexdigest()


def make_key(**parts: Any) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


def _pack(d: Any) -> Optional[list]:
    return None if d is None else [d.lang, d.text, d.avg_logprob, d.no_speech_prob, d.compression_ratio, d.n_tokens]


def _unpack(v: Optional[list]) -> Any:
    from .core import Decoded

    return None if v is None else Decoded(str(v[0]), str(v[1]), float(v[2]), float(v[3]), float(v[4]), int(v[5]))


class Checkpoint:
    """One lecture's checkpoint. Writing it never fails a transcription: a disk that refuses it only turns it off."""

    def __init__(self, delivery_id: str, key: str) -> None:
        self.path = path_for(delivery_id)
        self.key = key
        self._fh: Optional[Any] = None
        self._off = False

    def load(self) -> list[tuple[list[int], list[list[Any]], list[Any], int]]:
        """The batches done before: (passage indexes, their candidates, their priors, the next batch's size). Empty
        when there are none for this key."""
        try:
            lines = self.path.read_bytes().split(b"\n")
        except OSError:
            return []
        try:
            head = json.loads(lines[0])
        except ValueError:
            return []
        if not isinstance(head, dict) or head.get("v") != VERSION or head.get("key") != self.key:
            return []
        out = []
        for raw in lines[1:]:
            try:
                b = json.loads(raw)
                out.append(([int(i) for i in b["idx"]], [[_unpack(d) for d in c] for c in b["c"]], list(b["p"]),
                             int(b["next"])))
            except (ValueError, KeyError, TypeError, IndexError):
                break                     # cut short (or the end): what follows can't be trusted
        return out

    def start(self, kept: Sequence[tuple[list[int], list[list[Any]], list[Any], int]]) -> None:
        """Write the file again with the batches that are kept (none: a new one), then append to it."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            with open(tmp, "wb") as f:
                f.write(json.dumps({"v": VERSION, "key": self.key}).encode() + b"\n")
                for idx, cands, priors, nxt in kept:
                    f.write(self._line(idx, cands, priors, nxt))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
            self._fh = open(self.path, "ab")      # noqa: SIM115 - closed by close()
        except OSError as exc:
            log.info("No checkpoint for this transcription: %s", exc)
            self._off = True

    @staticmethod
    def _line(idx: Sequence[int], cands: Sequence[Sequence[Any]], priors: Sequence[Any], nxt: int) -> bytes:
        return json.dumps({"idx": list(idx), "c": [[_pack(d) for d in c] for c in cands], "p": list(priors),
                           "next": nxt}, ensure_ascii=False).encode("utf-8") + b"\n"

    def add(self, idx: Sequence[int], cands: Sequence[Sequence[Any]], priors: Sequence[Any], nxt: int) -> None:
        if self._off or self._fh is None:
            return
        try:
            self._fh.write(self._line(idx, cands, priors, nxt))
            self._fh.flush()
            os.fsync(self._fh.fileno())
        except OSError as exc:
            log.info("Checkpoint stopped: %s", exc)
            self._off = True

    def close(self) -> None:
        if self._fh is not None:
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None

    def discard(self) -> None:
        self.close()
        discard_path(self.path)


def discard_path(path: Path) -> None:
    for p in (path, path.with_name(path.name + ".tmp")):
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass


def discard(delivery_id: str) -> None:
    """Forget a lecture's checkpoint (it was cancelled, or forgotten from the queue)."""
    discard_path(path_for(delivery_id))


def sweep(days: float = KEEP_DAYS) -> int:
    """Delete checkpoints not touched for ``days``. Returns how many."""
    cutoff, n = time.time() - days * 86400, 0
    try:
        files = list(folder().iterdir())
    except OSError:
        return 0
    for f in files:
        try:
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink()
                n += 1
        except OSError:
            continue
    return n
