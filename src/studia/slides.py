"""Slides / screen capture from Panopto's SCREEN stream — without downloading the whole video.

Panopto serves the screen recording as HLS: one fragmented MP4 addressed with byte ranges, ~9 s
per segment, every segment starting with a keyframe. A 47-minute lecture is ~300 MB at 1080p, but
we only need one frame per segment: we fetch each fragment's header + first sample (~100 KB) with
HTTP Range requests, decode that keyframe with the H.264 parameters from the init segment, detect
slide changes on tiny thumbnails, and keep the *last* frame of every slide (handwritten notes are
then complete). Result: ~10x less data and the slide images aligned to the transcript.

A slide seen in one keyframe only (on screen ~5-12 s) is kept when the frame 5 s into its segment still shows it
(that one segment is fetched whole), so a quick slide is not lost while scrolling and flicking through are still
passed over; a slide whose next one only adds to it (more writing, the next build-up step) is kept once, complete.
Measured on three lectures against the recordings decoded at 1 frame/s: screen views of >= 6 s missed went from 5 to 2
(one is the recorder's set-up), for 3-38% more data (0.6-22 MB a lecture).
"""

from __future__ import annotations

import dataclasses as dc
import functools
import io
import itertools
import json
import logging
import os
import re
import struct
import threading
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterator, Optional
from urllib.parse import urljoin

import requests

from .cancel import Cancelled

if TYPE_CHECKING:   # PyAV and NumPy load only when slides are extracted (they take seconds to import)
    import av
    import numpy as np
from .hls import Segment as _Segment, get_range as _get, media_playlist, parse_playlist  # noqa: F401

log = logging.getLogger("studia")
FETCH_AHEAD = 16          # screen frames fetched ahead of the one being compared
HOLD_S = 5.0              # a slide seen in one keyframe only counts when it is still there this much later
STEADY = 0.03             # ... changed by less than this (share of the thumbnail)


@dc.dataclass
class Slide:
    index: int          # 1-based slide number within the recording
    t: float            # seconds from the start of the recording when it appears
    end: float
    image: str          # path relative to the course output folder (used in Markdown)
    path: str = ""      # absolute path of the JPEG


def _boxes(buf: bytes, start: int = 0, end: Optional[int] = None) -> Iterator[tuple[str, int, int]]:
    """Yield (type, box_start, box_end) for the boxes in buf[start:end]."""
    end = len(buf) if end is None else end
    pos = start
    while pos + 8 <= end:
        size, typ = struct.unpack(">I4s", buf[pos:pos + 8])
        header = 8
        if size == 1:
            size = struct.unpack(">Q", buf[pos + 8:pos + 16])[0]
            header = 16
        elif size == 0:
            size = end - pos
        if size < header:
            return
        yield typ.decode("latin-1"), pos, pos + size
        pos += size


def find_avcc(init: bytes) -> Optional[bytes]:
    i = init.find(b"avcC")
    if i < 4:
        return None
    size = struct.unpack(">I", init[i - 4:i])[0]
    return init[i + 4:i - 4 + size]


def first_sample_range(head: bytes) -> Optional[tuple[int, int]]:
    """(offset, size) of the first video sample of an fMP4 fragment, relative to the fragment start."""
    for typ, s, e in _boxes(head):
        if typ != "moof":
            continue
        default_size, base = 0, s
        for t2, s2, e2 in _boxes(head, s + 8, min(e, len(head))):
            if t2 != "traf":
                continue
            data_offset, size = None, None
            for t3, s3, e3 in _boxes(head, s2 + 8, min(e2, len(head))):
                body = s3 + 8
                if t3 == "tfhd":
                    flags = int.from_bytes(head[body + 1:body + 4], "big")
                    p = body + 8                     # version/flags + track_ID
                    if flags & 0x01:
                        base = struct.unpack(">Q", head[p:p + 8])[0]
                        p += 8
                    if flags & 0x02:
                        p += 4
                    if flags & 0x08:
                        p += 4
                    if flags & 0x10:
                        default_size = struct.unpack(">I", head[p:p + 4])[0]
                elif t3 == "trun":
                    flags = int.from_bytes(head[body + 1:body + 4], "big")
                    p = body + 8                     # version/flags + sample_count
                    if flags & 0x01:
                        data_offset = struct.unpack(">i", head[p:p + 4])[0]
                        p += 4
                    if flags & 0x04:
                        p += 4
                    if flags & 0x100:
                        p += 4
                    size = struct.unpack(">I", head[p:p + 4])[0] if flags & 0x200 else default_size
            if data_offset is not None and size:
                return base + data_offset, size
        return None
    return None


# ----------------------------------------------------------------------------- frames

def decode_keyframe(avcc: bytes, sample: bytes) -> Optional[av.VideoFrame]:
    import av

    ctx = av.CodecContext.create("h264", "r")
    ctx.extradata = avcc
    # one thread: the 8 fetching threads already decode side by side, and starting FFmpeg's own threads (one per
    # core) for every frame kept Python's lock (the GIL) busy: 8 threads decode 200 slide frames in 0.21 s instead of
    # 0.75, and a thread waiting for the lock (the window's) waits under 1 ms instead of ~14 ms (90th percentile)
    ctx.thread_count = 1
    frames = list(ctx.decode(av.Packet(sample)))
    frames += list(ctx.decode(None))
    return frames[0] if frames else None


def thumb(frame: av.VideoFrame) -> np.ndarray:
    import numpy as np

    return frame.reformat(width=192, height=108, format="gray").to_ndarray().astype(np.int16)


def changed_fraction(a: np.ndarray, b: np.ndarray) -> float:
    import numpy as np

    return float(np.mean(np.abs(a - b) > 28))


def is_blank(t: np.ndarray) -> bool:
    return float(t.std()) < 4.0


def added_only(a: np.ndarray, b: np.ndarray) -> bool:
    """``b`` is ``a`` with things added and nothing taken away: more handwriting, or the next step of a slide that
    builds up. Then ``b`` shows all of ``a`` and ``a`` need not be kept. Most of ``a`` must still be there: a screen
    that is darker overall (a video player, a dark slide) gains nothing brighter either, but it is not ``a``."""
    import numpy as np

    d = b - a
    return float(np.mean(d > 28)) < 0.003 and float(np.mean(np.abs(d) > 28)) < 0.35


def save_jpeg(frame: av.VideoFrame, path: Path, max_width: int = 1600) -> None:
    import av

    w, h = frame.width, frame.height
    if w > max_width:
        h = int(round(h * max_width / w / 2)) * 2
        w = max_width
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.stem + ".tmp" + path.suffix)
    with av.open(str(tmp), mode="w", format="image2") as out:
        st = out.add_stream("mjpeg", rate=1)
        st.width, st.height, st.pix_fmt = w, h, "yuvj420p"
        st.codec_context.options = {"qmin": "2", "qmax": "3"}
        f = frame.reformat(width=w, height=h, format="yuvj420p")
        f.pts = 0
        for pkt in st.encode(f):
            out.mux(pkt)
        for pkt in st.encode(None):
            out.mux(pkt)
    tmp.replace(path)


# ----------------------------------------------------------------------------- extraction

def _fetch_keyframe(session: requests.Session, seg: _Segment, avcc: bytes) -> Optional[av.VideoFrame]:
    head = _get(session, seg.url, seg.offset, seg.offset + min(seg.length or 65536, 65536) - 1)
    rng = first_sample_range(head)
    if rng is None:
        return None
    off, size = rng
    need = off + size
    if need > len(head):
        head += _get(session, seg.url, seg.offset + len(head), seg.offset + need - 1)
    return decode_keyframe(avcc, head[off:need])


def _frame_after(session: requests.Session, seg: _Segment, init_bytes: bytes, after_s: float) -> Optional[av.VideoFrame]:
    """The frame ``after_s`` seconds into a segment (or its last one): the whole segment is fetched and decoded from
    its keyframe, so this is for the few slides seen in one keyframe only."""
    import av

    if not seg.length:
        return None
    data = init_bytes + _get(session, seg.url, seg.offset, seg.offset + seg.length - 1)
    last = None
    with av.open(io.BytesIO(data), mode="r", format="mp4") as c:
        st = c.streams.video[0]
        st.codec_context.thread_count = 1
        first = None
        for f in c.decode(st):
            if f.time is None:
                continue
            first = f.time if first is None else first
            last = f
            if f.time - first >= after_s:
                break
    return last


def extract_slides(session: requests.Session, master_url: str, out_dir: Path, rel_prefix: str, prefix: str,
                   offset: float = 0.0, progress: Callable[[int, int], None] = lambda a, b: None,
                   cancel: Optional[threading.Event] = None, change_threshold: float = 0.06,
                   min_slide_s: float = 12.0, seen: Optional[list[tuple[np.ndarray, Slide]]] = None) -> list[Slide]:
    """Sample the screen stream, detect slide changes, write ``<out_dir>/<prefix>-NN.jpg``.

    ``rel_prefix`` is the folder of the images relative to the Markdown file (e.g. "slides/<day>"). ``seen``: the
    slides kept so far as (thumbnail, slide), shared by the calls for one recording's screen streams: a slide shown
    again in a later stream is not saved twice, and the numbering goes on.
    """
    r = media_playlist(session, master_url, prefer="video")
    init, segs = parse_playlist(r.text, r.url)
    if not init or not segs:
        raise RuntimeError("Unsupported screen-recording format.")
    init_bytes = _get(session, init[0], init[2], init[2] + init[1] - 1) if init[1] else session.get(init[0]).content
    avcc = find_avcc(init_bytes)
    if not avcc:
        raise RuntimeError("The screen recording is not H.264.")

    slides: list[Slide] = []
    saved = seen if seen is not None else []            # (thumb, slide) for de-duplication
    cur_start, cur_thumb, cur_frame, cur_last_t = None, None, None, 0.0
    cur_seg, cur_n = None, 0                            # the current slide's last keyframe's segment, its keyframes
    total = len(segs)

    def finish(end_t: float) -> None:
        if cur_frame is None or cur_start is None:
            return
        th = thumb(cur_frame)
        if is_blank(th):
            return
        dup = next((s for t_, s in saved if changed_fraction(t_, th) < 0.015), None)
        if dup is not None:
            slides.append(Slide(dup.index, cur_start + offset, end_t + offset, dup.image, dup.path))
            return
        last = saved[-1][1] if saved else None
        if last is not None and slides and slides[-1].index == last.index and added_only(saved[-1][0], th):
            # the slide before, with more written on it (or its next build-up step): one picture, the complete one
            save_jpeg(cur_frame, Path(last.path))
            saved[-1] = (th, last)
            slides.append(Slide(last.index, cur_start + offset, end_t + offset, last.image, last.path))
            return
        idx = len(saved) + 1
        name = f"{prefix}-{idx:02d}.jpg"
        path = out_dir / name
        save_jpeg(cur_frame, path)
        slide = Slide(idx, cur_start + offset, end_t + offset, f"{rel_prefix}/{name}", str(path))
        saved.append((th, slide))
        slides.append(slide)

    def _held(seg: Optional[_Segment], n: int) -> bool:
        """A slide seen in one keyframe only, and the next keyframe already differs: kept when it is still on screen
        ``HOLD_S`` later (a slide shown for ~5–12 s, not a moment of scrolling or flicking through)."""
        nonlocal cur_frame
        if n != 1 or seg is None or cur_thumb is None or seg is segs[0]:     # the first moments: the recorder's set-up
            return False
        try:
            later = _frame_after(session, seg, init_bytes, HOLD_S)
        except Exception as exc:  # noqa: BLE001 - then it is treated as before
            log.debug("Segment at %.0f s failed: %s", seg.t, exc)
            return False
        if later is None or changed_fraction(cur_thumb, thumb(later)) >= STEADY:
            return False
        cur_frame = later                                  # the same slide, a few seconds more complete
        return True

    with ThreadPoolExecutor(max_workers=8, thread_name_prefix="slides") as pool:
        pending: deque[Future] = deque()        # a few frames ahead, not all: a decoded 1080p frame is ~3 MB
        ahead = iter(segs)
        for i, seg in enumerate(segs):
            for s in itertools.islice(ahead, max(0, FETCH_AHEAD - len(pending))):
                pending.append(pool.submit(_fetch_keyframe, session, s, avcc))
            fut = pending.popleft()
            if cancel is not None and cancel.is_set():
                for f in pending:
                    f.cancel()
                raise Cancelled()
            try:
                frame = fut.result()
            except Exception as exc:  # noqa: BLE001 - a missing frame only lowers time precision
                log.debug("Keyframe %d failed: %s", i, exc)
                frame = None
            progress(i + 1, total)
            if frame is None:
                continue
            th = thumb(frame)
            if cur_thumb is None:
                cur_start, cur_thumb, cur_frame, cur_n = seg.t, th, frame, 1
            elif changed_fraction(cur_thumb, th) > change_threshold and (
                    seg.t - cur_start >= min_slide_s or _held(cur_seg, cur_n)):
                finish(seg.t)
                cur_start, cur_thumb, cur_frame, cur_n = seg.t, th, frame, 1
            else:
                cur_thumb, cur_frame = th, frame          # same slide, keep the most complete version
                cur_n += 1
            cur_seg, cur_last_t = seg, seg.t + seg.dur
        finish(cur_last_t)
    return slides


@functools.lru_cache(maxsize=4096)
def _jpeg_size(path: str, mtime_ns: int) -> tuple[int, int]:
    try:
        with open(path, "rb") as f:
            data = f.read(16384)
    except OSError:
        return 0, 0
    i = 2                                                   # after SOI
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0x01, 0xFF) or 0xD0 <= marker <= 0xD7:
            i += 1 if marker == 0xFF else 2
            continue
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):   # SOFn
            return int.from_bytes(data[i + 7:i + 9], "big"), int.from_bytes(data[i + 5:i + 7], "big")
        i += 2 + int.from_bytes(data[i + 2:i + 4], "big")
    return 0, 0


def jpeg_size(path: str) -> tuple[int, int]:
    """(width, height) from the JPEG header, (0, 0) if unknown — lets the UI reserve the image's space."""
    try:
        return _jpeg_size(path, os.stat(path).st_mtime_ns)
    except OSError:
        return 0, 0


def save_index(path: Path, slides: list[Slide]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")                 # never a half-written index, even if the app stops
    with open(tmp, "w", encoding="utf-8") as f:                    # on the disk before the swap (a power cut)
        f.write(json.dumps([dc.asdict(s) for s in slides], ensure_ascii=False, indent=1))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def load_index(path: Path) -> list[Slide]:
    try:
        return [Slide(**d) for d in json.loads(path.read_text(encoding="utf-8"))]
    except (OSError, ValueError, TypeError):
        return []
