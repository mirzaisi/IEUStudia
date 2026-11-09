"""Slides / screen capture from Panopto's SCREEN stream — without downloading the whole video.

Panopto serves the screen recording as HLS: one fragmented MP4 addressed with byte ranges, ~9 s
per segment, every segment starting with a keyframe. We only need one frame per segment: we fetch
each fragment's header + first sample with HTTP Range requests, decode that keyframe with the H.264
parameters from the init segment, detect screen changes on tiny thumbnails, and keep the *last*
keyframe of every screen (handwritten notes are then as complete as they get). The keyframes are
about a third of a screen video's bytes (31 % over 8 measured lectures, 24-94 MB a lecture: they
carry most of what a screen video holds), and the pictures line up with the transcript.

A new screen starts when over 2 % of the thumbnail changed (at 6 % a slide that fills only part of the screen, as in
PowerPoint's presenter view or a lecture shared in a Teams window, went unnoticed: 4 of 17 and 27 of 59 screens were
missed; at 2 % 3 of 201 over 8 lectures, for ~4 % more data). A slide seen in one keyframe only (on screen ~5-12 s) is
kept when the frame 5 s into its segment still shows it (that one segment is fetched whole), so a quick slide is not
lost while scrolling and flicking through are still passed over; a slide whose next one only adds to it (more
writing, the next build-up step, light on dark too) is kept once, complete. A picture shown again keeps its number
(compared at 384 px, both ways, so two pages that differ only in small text stay two), and a last pass drops a picture
that a later one shows whole. A recording whose only video is the camera stream gets it read like a screen when its
first keyframes mostly stand still (a tablet recorded as the camera; a camera never stands still to the pixel).
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
VERSION = 2               # of the capture (services._slides_key): 1 compared at 6 %, read only SCREEN streams
FETCH_AHEAD = 16          # screen frames fetched ahead of the one being compared
CHANGE = 0.02             # a new screen: more than this share of the thumbnail changed
HOLD_S = 5.0              # a slide seen in one keyframe only counts when it is still there this much later
STEADY = 0.03             # ... changed by less than this (share of the thumbnail)
SAME = 0.02               # the same picture: under this share of either one's ink missing from the other (384 px)
MISSED_SHARE = 0.1        # more keyframes than this share failed to arrive: the capture is incomplete
MISSED_RUN = 5            # ... or this many at the end in a row (the connection was lost)
STILL_FRAMES = 30         # a camera stream's first keyframes looked at to tell a screen (a tablet) from a camera
STILL_SHARE = 0.3         # ... a screen when this share of their pairs stand still
INK = 28                  # grey levels: a pixel this far from the 9x9 around it is ink (text, strokes, edges)


class Incomplete(RuntimeError):
    """Too many of the screen recording's keyframes failed to arrive (the connection dropped): ``slides`` are the
    ones captured anyway, kept, but the capture isn't done."""

    def __init__(self, missed: int, total: int, slides: list["Slide"]):
        super().__init__(f"{missed} of {total} screen frames couldn't be fetched")
        self.slides = slides


@dc.dataclass
class Slide:
    index: int          # 1-based slide number within the recording
    t: float            # seconds from the start of the recording when it appears
    end: float
    image: str          # path relative to the course output folder (used in Markdown)
    path: str = ""      # absolute path of the JPEG


@dc.dataclass
class Kept:
    """A picture kept: its thumbnail (192x108), its 384-px copy (and that one's ink, worked out once) and the slide
    it was saved as. A recording's screen streams share the list (``extract_slides``' ``seen``)."""
    thumb: np.ndarray
    big: np.ndarray
    slide: Slide
    _ink: Optional[np.ndarray] = None

    @property
    def ink(self) -> np.ndarray:
        if self._ink is None:
            self._ink = ink(self.big)
        return self._ink

    def update(self, th: np.ndarray, g: np.ndarray, g_ink: Optional[np.ndarray] = None) -> None:
        self.thumb, self.big, self._ink = th, g, g_ink


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


class Codec(bytes):
    """A stream's H.264 parameters (avcC), with the track its pictures are in (``track``: a camera stream carries its
    sound in the same fragments, and its first sample may be the sound's; None: the first track)."""
    track: Optional[int] = None


def video_track(init: bytes) -> Optional[int]:
    """The id of the video track in an fMP4 init segment (the ``trak`` whose handler is "vide"); None if unknown."""
    try:
        for typ, s, e in _boxes(init):
            if typ != "moov":
                continue
            for t2, s2, e2 in _boxes(init, s + 8, min(e, len(init))):
                if t2 != "trak":
                    continue
                tid, handler = None, b""
                for t3, s3, e3 in _boxes(init, s2 + 8, min(e2, len(init))):
                    if t3 == "tkhd":
                        at = s3 + 12 + (16 if init[s3 + 8] == 1 else 8)    # version/flags, its two times
                        tid = struct.unpack(">I", init[at:at + 4])[0]
                    elif t3 == "mdia":
                        for t4, s4, e4 in _boxes(init, s3 + 8, min(e3, len(init))):
                            if t4 == "hdlr":
                                handler = init[s4 + 16:s4 + 20]          # version/flags, pre_defined, its type
                if handler == b"vide" and tid is not None:
                    return tid
    except (struct.error, IndexError):
        return None
    return None


def _codec(init: bytes) -> Optional[Codec]:
    """The stream's H.264 parameters with its video track (find_avcc's, as a Codec); None when it isn't H.264."""
    avcc = find_avcc(init)
    if not avcc:
        return None
    out = Codec(avcc)
    out.track = video_track(init)
    return out


def first_sample_range(head: bytes, track: Optional[int] = None) -> Optional[tuple[int, int]]:
    """(offset, size) of the first video sample of an fMP4 fragment, relative to the fragment start (of ``track``,
    when given: the fragment may hold the sound's samples too)."""
    for typ, s, e in _boxes(head):
        if typ != "moof":
            continue
        for t2, s2, e2 in _boxes(head, s + 8, min(e, len(head))):
            if t2 != "traf":
                continue
            default_size, base = 0, s
            data_offset, size, other = None, None, False
            for t3, s3, e3 in _boxes(head, s2 + 8, min(e2, len(head))):
                body = s3 + 8
                if t3 == "tfhd":
                    flags = int.from_bytes(head[body + 1:body + 4], "big")
                    other = track is not None and struct.unpack(">I", head[body + 4:body + 8])[0] != track
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
            if data_offset is not None and size and not other:
                return base + data_offset, size
        return None
    return None


# ----------------------------------------------------------------------------- frames

def decode_keyframe(avcc: bytes, sample: bytes) -> Optional[av.VideoFrame]:
    import av

    ctx = av.CodecContext.create("h264", "r")
    ctx.extradata = bytes(avcc)
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


def big(frame: av.VideoFrame) -> np.ndarray:
    """A 384-px wide grey copy (its own shape): what "the same picture" and the last pass compare."""
    h = max(2, int(round(384 * frame.height / max(1, frame.width) / 2)) * 2)
    return frame.reformat(width=384, height=h, format="gray").to_ndarray()


def changed_fraction(a: np.ndarray, b: np.ndarray) -> float:
    import numpy as np

    return float(np.mean(np.abs(a - b) > 28))


def is_blank(t: np.ndarray) -> bool:
    return float(t.std()) < 4.0


def local_median(a: np.ndarray) -> np.ndarray:
    """The median of the 9x9 pixels around each pixel: the background there (what is around text or a stroke)."""
    import numpy as np
    from numpy.lib.stride_tricks import sliding_window_view

    p = np.pad(np.asarray(a, dtype=np.int16), 4, mode="edge")
    win = sliding_window_view(p, (9, 9)).reshape(a.shape[0], a.shape[1], 81)
    return np.partition(win, 40, axis=-1)[..., 40]


def ink(a: np.ndarray) -> np.ndarray:
    """Pixels that stand out from what is around them: text and strokes, dark on light or light on dark, edges."""
    import numpy as np

    return np.abs(np.asarray(a, dtype=np.int16) - local_median(a)) > INK


def ink_missing(a: np.ndarray, b: np.ndarray, a_ink: Optional[np.ndarray] = None) -> float:
    """The share of ``a``'s ink that ``b`` doesn't show: no pixel of ``b`` within one pixel of it is within 28 grey
    levels (so a picture moved by a pixel, or blurred a little, still shows it). 1.0 for pictures of other sizes."""
    import numpy as np

    if a.shape != b.shape:
        return 1.0
    k = ink(a) if a_ink is None else a_ink
    n = int(k.sum())
    if not n:
        return 0.0
    f = np.asarray(a, dtype=np.int16)
    p = np.pad(np.asarray(b, dtype=np.int16), 1, mode="edge")
    h, w = f.shape
    near = np.abs(p[0:h, 0:w] - f)
    for y in range(3):
        for x in range(3):
            if y or x:
                np.minimum(near, np.abs(p[y:y + h, x:x + w] - f), out=near)
    return float((k & (near > INK)).sum() / n)


def same_picture(a: np.ndarray, b: np.ndarray, a_ink: Optional[np.ndarray] = None,
                 b_ink: Optional[np.ndarray] = None) -> bool:
    """Two 384-px pictures show the same thing: each one's ink is in the other (under 2 % missing), both ways. (The
    1.5 % of a 192x108 copy compared before took two pages that differ only in small text for one.)"""
    return ink_missing(a, b, a_ink) < SAME and ink_missing(b, a, b_ink) < SAME


def added_only(a: np.ndarray, b: np.ndarray) -> bool:
    """``b`` is ``a`` with things added and nothing taken away: more handwriting, or the next step of a slide that
    builds up. Then ``b`` shows all of ``a`` and ``a`` need not be kept. Of the pixels that changed, (almost) none went
    back towards the background around them in ``a`` (ink rubbed out, a page turned, a slide replaced); the rest moved
    away from it: ink added, dark on a light page or light on a dark one. Most of ``a`` must still be there: a screen
    that is darker overall (a video player, a dark slide) is not ``a`` with things added."""
    import numpy as np

    a16, b16 = np.asarray(a, dtype=np.int16), np.asarray(b, dtype=np.int16)
    ch = np.abs(b16 - a16) > 28
    n = ch.size
    if not ch.any():
        return True
    if ch.sum() / n >= 0.35:
        return False
    med = local_median(a16)
    toward = ch & (np.abs(b16 - med) < np.abs(a16 - med))
    return float(toward.sum() / n) < 0.003


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
    rng = first_sample_range(head, getattr(avcc, "track", None))
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


def _kept(segs: list[_Segment], spans: tuple[tuple[float, float, float], ...]) -> list[_Segment]:
    """The segments of a screen stream that the recording keeps, timed in the recording. ``spans`` are its stretches
    as (from, to, at) in the stream's own seconds (see panopto._spans). A stretch cut out in Panopto's editor, or
    recorded while the recorder was paused, is in the screen video but not in the sound: its keyframes are left out,
    and the ones after it move earlier."""
    out = []
    for seg in segs:
        span = next((g for g in spans if g[0] <= seg.t < g[1]), None)
        if span is not None:
            out.append(dc.replace(seg, t=span[2] + seg.t - span[0], dur=min(seg.dur, span[1] - seg.t)))
    return out


def extract_slides(session: requests.Session, master_url: str, out_dir: Path, rel_prefix: str, prefix: str,
                   offset: float = 0.0, progress: Callable[[int, int], None] = lambda a, b: None,
                   cancel: Optional[threading.Event] = None, change_threshold: float = CHANGE,
                   min_slide_s: float = 12.0, seen: Optional[list[Kept]] = None,
                   spans: tuple[tuple[float, float, float], ...] = ()) -> list[Slide]:
    """Sample the screen stream, detect slide changes, write ``<out_dir>/<prefix>-NN.jpg``.

    ``rel_prefix`` is the folder of the images relative to the Markdown file (e.g. "slides/<day>"). ``seen``: the
    pictures kept so far (Kept), shared by the calls for one recording's screen streams: a slide shown again in a
    later stream is not saved twice, and the numbering goes on (``final_pass`` then looks at all of them). ``spans``:
    the stretches of the stream the recording keeps (see _kept); they give the times then, not ``offset``.
    """
    r = media_playlist(session, master_url, prefer="video")
    init, segs = parse_playlist(r.text, r.url)
    if not init or not segs:
        raise RuntimeError("Unsupported screen-recording format.")
    if spans:
        segs, offset = _kept(segs, spans), 0.0
        if not segs:                                    # all of it cut out
            return []
    init_bytes = _get(session, init[0], init[2], init[2] + init[1] - 1) if init[1] else session.get(init[0]).content
    avcc = _codec(init_bytes)
    if not avcc:
        raise RuntimeError("The screen recording is not H.264.")

    slides: list[Slide] = []
    saved = seen if seen is not None else []            # the pictures kept, for "the same picture again"
    cur_start, cur_thumb, cur_frame, cur_last_t = None, None, None, 0.0
    cur_seg, cur_n = None, 0                            # the current slide's last keyframe's segment, its keyframes
    total = len(segs)
    missed, run = 0, 0                                  # keyframes that failed to arrive, those at the end in a row

    def finish(end_t: float) -> None:
        if cur_frame is None or cur_start is None:
            return
        th = thumb(cur_frame)
        if is_blank(th):
            return
        g = big(cur_frame)
        g_ink = ink(g)
        # the same picture as one kept before (a slide shown again): its number. (Only pictures close at 192 px are
        # compared at 384: the same picture is never far.)
        dup = next((k for k in saved if changed_fraction(k.thumb, th) < 0.15
                    and same_picture(k.big, g, k.ink, g_ink)), None)
        if dup is not None:
            slides.append(Slide(dup.slide.index, cur_start + offset, end_t + offset, dup.slide.image, dup.slide.path))
            return
        last = saved[-1] if saved else None
        if last is not None and slides and slides[-1].index == last.slide.index and added_only(last.thumb, th):
            # the slide before, with more written on it (or its next build-up step): one picture, the complete one
            save_jpeg(cur_frame, Path(last.slide.path))
            last.update(th, g, g_ink)
            slides.append(Slide(last.slide.index, cur_start + offset, end_t + offset, last.slide.image, last.slide.path))
            return
        idx = max((k.slide.index for k in saved), default=0) + 1
        name = f"{prefix}-{idx:02d}.jpg"
        path = out_dir / name
        save_jpeg(cur_frame, path)
        slide = Slide(idx, cur_start + offset, end_t + offset, f"{rel_prefix}/{name}", str(path))
        saved.append(Kept(th, g, slide, g_ink))
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
                missed, run = missed + 1, run + 1
            else:
                run = 0
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
    if missed > total * MISSED_SHARE or run >= min(MISSED_RUN, total):
        raise Incomplete(missed, total, slides)
    return slides


def final_pass(slides: list[Slide], kept: list[Kept]) -> list[Slide]:
    """The last pass over a recording's pictures: one whose ink a later picture shows whole (a near-duplicate: the
    same slide with a line more, a page shown again with more written on it) is dropped, its file deleted and its
    times given to that later picture. The pictures are then numbered again in the order they first appear (renumber),
    so the numbers have no gaps. ``kept`` is updated to match. Slides whose pictures ``kept`` doesn't hold are given
    back as they are."""
    by_index = {k.slide.index: k for k in kept if isinstance(k, Kept)}
    if not by_index or {s.index for s in slides} - set(by_index):
        return slides
    order = sorted(by_index)
    into: dict[int, int] = {}
    for pos, i in enumerate(order):
        a = by_index[i]
        for j in order[pos + 1:]:
            b = by_index[j]
            # (close at 192 px first: a later picture that shows this one whole can't differ much there)
            if ink_missing(a.thumb, b.thumb) < 0.15 and ink_missing(a.big, b.big, a.ink) < SAME:
                into[i] = j
                break

    def final(i: int) -> int:
        while i in into:
            i = into[i]
        return i

    out = [dc.replace(s, index=final(s.index), image=by_index[final(s.index)].slide.image,
                      path=by_index[final(s.index)].slide.path) for s in slides]
    for i in into:                                       # dropped: their files go
        try:
            Path(by_index[i].slide.path).unlink(missing_ok=True)
        except OSError:
            pass
    out = renumber(out)
    first: dict[int, Slide] = {}
    for s0, s1 in zip(slides, out):                      # (renumber keeps the entries in their order)
        first.setdefault(final(s0.index), s1)
    alive = []
    for i in sorted(first, key=lambda i: first[i].index):
        by_index[i].slide = dc.replace(first[i])
        alive.append(by_index[i])
    kept[:] = alive
    return out


def renumber(slides: list[Slide]) -> list[Slide]:
    """The pictures numbered 1, 2, 3 ... in the order they first appear. A captured picture's file ("...-NN.jpg") is
    renamed to its new number, in two steps (a number can move onto another picture's old name); one that can't be
    renamed, or whose new name another file has, keeps its name (the index says which picture it is)."""
    first: dict[int, float] = {}
    for s in slides:
        first[s.index] = min(first.get(s.index, s.t), s.t)
    number = {i: n for n, i in enumerate(sorted(first, key=lambda i: (first[i], i)), start=1)}
    pics = {s.index: (s.image, s.path) for s in slides}
    moves: dict[int, tuple[Path, Path]] = {}
    for i, (_, path) in pics.items():
        m = re.fullmatch(r"(.*-)(\d+)(\.jpg)", Path(path).name) if path and number[i] != i else None
        if m:
            moves[i] = (Path(path), Path(path).with_name(f"{m.group(1)}{number[i]:02d}{m.group(3)}"))
    leaving = {str(src) for src, _ in moves.values()}
    staged: dict[int, Path] = {}
    for i, (src, dst) in moves.items():
        if dst.exists() and str(dst) not in leaving:      # another file has that name: left as it is
            continue
        try:
            os.replace(src, src.with_name(src.name + ".renum"))
            staged[i] = src.with_name(src.name + ".renum")
        except OSError:
            log.debug("Renaming %s failed", src, exc_info=True)
    done: set[int] = set()
    for i, tmp in staged.items():
        src, dst = moves[i]
        try:
            os.replace(tmp, dst)
            done.add(i)
        except OSError:
            log.debug("Renaming %s failed", src, exc_info=True)
            try:
                os.replace(tmp, src)
            except OSError:
                pass
    for i in done:
        image, _ = pics[i]
        src, dst = moves[i]
        pics[i] = (image[:len(image) - len(src.name)] + dst.name if image.endswith(src.name) else image, str(dst))
    return [Slide(number[s.index], s.t, s.end, *pics[s.index]) for s in slides]


def still_share(session: requests.Session, master_url: str, frames: int = STILL_FRAMES,
                cancel: Optional[threading.Event] = None) -> Optional[float]:
    """The share of a stream's first ``frames`` keyframe pairs (9 s apart) that stand still: under 1 % of the
    thumbnail moved more than 8 grey levels. A screen does most of the time (a tablet 63 %, screen streams 53-100 %);
    a camera never does (sensor noise, people: 0-5 %). None when it can't be told (too few keyframes came)."""
    import numpy as np

    r = media_playlist(session, master_url, prefer="video")
    init, segs = parse_playlist(r.text, r.url)
    if not init or len(segs) < 2:
        return None
    init_bytes = _get(session, init[0], init[2], init[2] + init[1] - 1) if init[1] else session.get(init[0]).content
    avcc = _codec(init_bytes)
    if not avcc:
        return None

    def one(seg: _Segment) -> Optional[np.ndarray]:
        if cancel is not None and cancel.is_set():
            return None
        try:
            f = _fetch_keyframe(session, seg, avcc)
        except Exception as exc:  # noqa: BLE001 - a missing frame only leaves out its pairs
            log.debug("Keyframe at %.0f s failed: %s", seg.t, exc)
            return None
        return thumb(f) if f is not None else None

    with ThreadPoolExecutor(max_workers=8, thread_name_prefix="slides") as pool:
        ths = list(pool.map(one, segs[:frames]))
    if cancel is not None and cancel.is_set():
        raise Cancelled()
    pairs = [(a, b) for a, b in zip(ths, ths[1:]) if a is not None and b is not None]
    if len(pairs) < min(5, len(segs) - 1):
        return None
    still = sum(1 for a, b in pairs if float(np.mean(np.abs(a - b) > 8)) < 0.01)
    return still / len(pairs)


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
