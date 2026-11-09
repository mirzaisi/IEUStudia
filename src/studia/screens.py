"""The screen timeline of a recording: what was on screen when, with its title and text.

``<delivery>.slides.json`` (slides.py) stays the list of spans, one per appearance of a picture (index, t, end, image,
path); ``<delivery>.screens.json`` is the text layer next to it::

    {"v": 1, "made": "2026-11-12T10:20:00", "times": "exact",
     "screens": {"12": {"title": "Δ - Y transformation", "text": "Apply superposition ...", "from": "powerpoint",
                        "slide": 15, "lang": "en"},
                 "13": {"title": "", "text": "Check condition (2). ...", "from": "ocr", "ocr": "windows en-US"}}}

``times`` is "exact" when PowerPoint's own slide changes gave the spans their times, else "keyframe" (the capture's:
0-9 s late, 3 s at the median). ``from`` says where a screen's words came from: "powerpoint" (the slide's title and
text, from Panopto's PowerPoint events), "ocr" (Windows' own text recognition, ocr.py, of the part of the screen that
changes from screen to screen) or "none" (no words read). ``powerpoint`` (true, false, or null while Panopto hasn't
been asked) remembers whether the recording has PowerPoint events, so they are asked for once.

Made three ways: in the transcription job, right after the capture (Services.slides_task); by ScreenWorker in the
background for lectures transcribed before (the pictures on disk read, one DeliveryInfo request per lecture); and
when the student asks to capture a lecture's screens again (ScreenWorker too).
"""

from __future__ import annotations

import bisect
import collections
import contextlib
import dataclasses as dc
import datetime as dt
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from . import store
from .cancel import Cancelled
from .i18n import t, tn
from .slides import Slide, renumber

log = logging.getLogger("studia")
EARLY = 1.0          # a captured screen starting this much before a slide change is that change (rounding)
LATE = 9.5           # ... or this much after it: the capture sees a change at the next keyframe (one every ~9 s)
PIC_BACK = 8.5       # a captured screen's picture is its last keyframe: about this long before its end
MIN_PPT_S = 6.0      # a PowerPoint slide shown this long with no captured picture gets Panopto's own picture
TEXT_MAX = 2000      # characters of a screen's text kept in the file
_TR = re.compile(r"[ğışĞİŞ]|\b(?:ve|bir|ile|için|olarak|örnek|soru|çözüm|nedir|göre)\b", re.I)


@dc.dataclass
class PptSpan:
    """One stretch of a PowerPoint slide on screen (its events merged: build-up steps are events of their own)."""
    slide: int           # the slide's number in the deck
    t0: float
    t1: float
    title: str
    text: str
    obj: str = ""        # the event's ObjectIdentifier and ObjectSequenceNumber: Panopto's picture of the slide
    seq: int = 0

    @property
    def dur(self) -> float:
        return self.t1 - self.t0


def _clean(text: Any) -> str:
    return " ".join(str(text or "").split())


def _under_test() -> bool:
    """A test run: no OCR of its made-up pictures, and no background job reaching for Panopto."""
    return "PYTEST_CURRENT_TEST" in os.environ


def powerpoint(info: Optional[dict[str, Any]]) -> list[PptSpan]:
    """PowerPoint's slide changes from a DeliveryInfo answer: one span per stretch of a slide (events of the same slide
    one after the other merged), its title from the event's Caption, its text from Data (the longest of the merged
    events: build-up steps add lines). ObjectSequenceNumber holds the slide's number in its low 16 bits."""
    d = info.get("Delivery") if isinstance(info, dict) else None
    if not isinstance(d, dict):
        return []
    events = []
    for e in d.get("Timestamps") or []:
        if not isinstance(e, dict) or e.get("EventTargetType") != "PowerPoint":
            continue
        try:
            at, seq = float(e.get("Time")), int(e.get("ObjectSequenceNumber") or 0)
        except (TypeError, ValueError):
            continue
        if not 0 <= at < 1e7:                     # (NaN too)
            continue
        events.append((at, seq, _clean(e.get("Caption")), _clean(e.get("Data")), str(e.get("ObjectIdentifier") or "")))
    events.sort(key=lambda x: x[0])
    try:
        end = float(d.get("Duration") or 0.0)
    except (TypeError, ValueError):
        end = 0.0
    spans: list[PptSpan] = []
    for at, seq, cap, data, obj in events:
        n = seq & 0xFFFF
        if spans and spans[-1].slide == n:
            last = spans[-1]
            last.title = last.title or cap
            if len(data) > len(last.text):
                last.text, last.obj, last.seq = data, obj or last.obj, seq      # the fullest step's picture
            continue
        if spans:
            spans[-1].t1 = at
        spans.append(PptSpan(n, at, at, cap, data, obj, seq))
    if spans:
        spans[-1].t1 = max(end, spans[-1].t0 + MIN_PPT_S)
    return [s for s in spans if s.t1 > s.t0]


def lang_of(text: str) -> str:
    return "tr" if _TR.search(text or "") else "en"


# ----------------------------------------------------------------------------- the captured screens and PowerPoint
def _active(spans: list[PptSpan], starts: list[float], at: float) -> Optional[PptSpan]:
    i = bisect.bisect_right(starts, at) - 1
    return spans[i] if i >= 0 and spans[i].t0 <= at < spans[i].t1 else None


def match(entries: list[Slide], spans: list[PptSpan]) -> tuple[list[Slide], dict[int, PptSpan], list[PptSpan]]:
    """The captured screens (``entries``, keyframe times) set against PowerPoint's slide changes.

    A captured screen's picture is its last keyframe, so the slide it shows is the one on screen a keyframe before its
    end. A screen that began with that slide's change (up to ~9 s later: the next keyframe) starts at the change's
    exact time; one that began before it (a change the capture didn't see) starts there too, and the time before it
    is left to the slide before. A screen that began in the middle of a slide (writing added, another program) keeps
    its own time and gets no PowerPoint words. Returns the screens (times set), {picture number: the slide it shows},
    and the slides shown MIN_PPT_S or more that no captured picture shows (they get Panopto's own picture)."""
    out = [dc.replace(e) for e in sorted(entries, key=lambda e: (e.t, e.index))]
    if not spans:
        return out, {}, []
    starts = [s.t0 for s in spans]
    shows: dict[int, PptSpan] = {}
    began: set[int] = set()                                # the slides (by place) a captured screen began with
    covered: list[tuple[Slide, int]] = []                  # captured screens and the deck slide each one shows
    prev_t = float("-inf")
    for e in out:
        pic = _active(spans, starts, max(e.t, e.end - PIC_BACK))
        if pic is not None and e.t - pic.t0 <= LATE and pic.t0 > prev_t:   # began with that slide's change, or before
            e.t = pic.t0
            shows.setdefault(e.index, pic)
            began.add(id(pic))
            covered.append((e, pic.slide))
        prev_t = e.t
    for a, b in zip(out, out[1:]):                           # each one ends where the next begins
        if a.end > b.t:
            a.end = b.t
    missing = []
    for s in spans:
        if s.dur < MIN_PPT_S or id(s) in began:
            continue
        seen = sum(max(0.0, min(e.end, s.t1) - max(e.t, s.t0)) for e, n in covered if n == s.slide)
        if seen < 0.5 * s.dur:
            missing.append(s)
    return out, shows, missing


def insert(entries: list[Slide], span: PptSpan, index: int, image: str, path: str) -> list[Slide]:
    """A screen for a PowerPoint slide no captured picture shows, at the slide's own time: from its change until the
    next screen begins (or the slide ends). The screen that was on before it ends there, and goes on after it when it
    ran past it (its picture is the last thing it showed)."""
    t0 = span.t0
    nxt = min((e.t for e in entries if t0 < e.t < span.t1), default=span.t1)
    if nxt - t0 < MIN_PPT_S / 2:
        return entries
    out: list[Slide] = []
    for e in entries:
        if e.t <= t0 < e.end:                        # on screen when the slide came: cut there
            if e.t < t0:
                out.append(dc.replace(e, end=t0))
            if e.end > nxt:
                out.append(dc.replace(e, t=nxt))
        else:
            out.append(e)
    out.append(Slide(index, round(t0, 3), round(nxt, 3), image, path))
    out.sort(key=lambda e: (e.t, e.index))
    return out


def merge(entries: list[Slide], spans: list[PptSpan],
          picture: Callable[[PptSpan], Optional[tuple[str, str]]]) -> tuple[list[Slide], dict[int, PptSpan]]:
    """match, then a screen for each PowerPoint slide no captured picture shows: the captured picture of the same deck
    slide when there is one (shown again), else Panopto's own (``picture`` gives its image and path, or None)."""
    out, shows, missing = match(entries, spans)
    by_slide = {s.slide: i for i, s in shows.items()}
    nxt = max((e.index for e in out), default=0) + 1
    pics: dict[int, tuple[str, str]] = {e.index: (e.image, e.path) for e in out}
    for s in missing:
        index = by_slide.get(s.slide)
        if index is None:
            got = picture(s)
            if got is None:
                continue
            index, nxt = nxt, nxt + 1
            by_slide[s.slide] = index
            shows[index] = s
            pics[index] = got
        out = insert(out, s, index, *pics[index])
    return out, shows


# ----------------------------------------------------------------------------- the timeline file
_locks: dict[str, threading.RLock] = {}
_locks_lock = threading.Lock()


@contextlib.contextmanager
def lecture_lock(delivery_id: str) -> Iterator[None]:
    """One capture or timeline of a recording at a time (the transcription job and the background job)."""
    with _locks_lock:
        lock = _locks.setdefault(delivery_id, threading.RLock())
    with lock:
        yield


def _doc(times: str, screens: dict[str, dict[str, Any]], ppt: Optional[bool]) -> dict[str, Any]:
    return {"v": 1, "made": dt.datetime.now().replace(microsecond=0).isoformat(), "times": times, "screens": screens,
            "powerpoint": ppt}


def _pic_key(path: str) -> str:
    """A picture as it was read: its name, size and time (a capture made again writes over the same names)."""
    try:
        st = os.stat(path)
    except (OSError, ValueError):
        return ""
    return f"{os.path.basename(path)}|{st.st_size}|{st.st_mtime_ns}"


def _ppt_screen(s: PptSpan) -> dict[str, Any]:
    text = s.text[:TEXT_MAX]
    return {"title": s.title[:300], "text": text, "from": "powerpoint", "slide": s.slide,
            "lang": lang_of(f"{s.title} {text}")}


def default_reader() -> Optional[Callable[..., dict[str, Any]]]:
    """Windows' text recognition (ocr.read_screens), or None where there is none (Linux, a build without it; the
    tests, which read with their own fakes)."""
    if _under_test():
        return None
    try:
        from . import ocr
    except Exception:  # noqa: BLE001 - no OCR: the timeline is made without words
        return None
    return ocr.read_screens if ocr.available() else None


def build_timeline(svc: Any, delivery_id: str, info: Optional[dict[str, Any]], session: Any = None, *,
                   fresh: bool = False, cancel: Optional[threading.Event] = None,
                   read: Any = "default") -> list[Slide]:
    """Make (or make again) a recording's screen timeline from its captured pictures, PowerPoint's events in ``info``
    (its DeliveryInfo; None when Panopto couldn't be asked: then asked again later) and the words read on the other
    screens (``read``: ocr.read_screens; None for none). ``fresh``: the pictures were just captured (numbered again in
    time order with Panopto's pictures among them; otherwise new ones are numbered after the others, so the numbers
    AI notes and chats used stay). Returns the screens (slides.json's spans, saved when they changed)."""
    reader = default_reader() if read == "default" else read
    with lecture_lock(delivery_id):
        slides = store.load_slides(delivery_id)
        old = store.load_screens(delivery_id) or {}
        spans = powerpoint(info) if info is not None else []
        ppt: Optional[bool] = bool(spans) if info is not None else old.get("powerpoint")
        exact_before = not fresh and old.get("times") == "exact"     # matched before: the spans are PowerPoint's
        old_screens = old.get("screens") if isinstance(old.get("screens"), dict) else {}
        if not slides:
            store.save_screens(delivery_id, _doc("keyframe", {}, ppt))
            return []
        entries, shows = slides, {}
        if spans and not exact_before:
            entries, shows = merge(slides, spans, _pictures(svc, delivery_id, session, slides))
            if fresh:                                      # (renumber keeps the entries in their order)
                merged, entries = entries, renumber(entries)
                new = {a.index: b.index for a, b in zip(merged, entries)}
                shows = {new[i]: s for i, s in shows.items() if i in new}
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        screens: dict[str, dict[str, Any]] = {}
        for i, s in shows.items():
            screens[str(i)] = _ppt_screen(s)
        if exact_before:                                   # PowerPoint's words as they were
            for k, v in old_screens.items():
                if isinstance(v, dict) and v.get("from") == "powerpoint":
                    screens[k] = v
        first: dict[int, Slide] = {}
        for e in sorted(entries, key=lambda e: e.t):
            first.setdefault(e.index, e)
        todo = [i for i in first if str(i) not in screens]
        # made again with the same pictures (Panopto couldn't be asked last time, or now can): the words read before
        # are kept, not read again at every start. All or none: a picture is cleaned with the others it was read with
        kept = {i: old_screens.get(str(i)) for i in todo} if not fresh else {}
        if not all(isinstance(v, dict) and v.get("from") == "ocr" and v.get("pic")
                   and v["pic"] == _pic_key(first[i].path) for i, v in kept.items()):
            kept = {}
        texts: dict[str, Any] = {}
        if todo and reader is not None and not kept:
            order = [first[i].path for i in sorted(first, key=lambda i: first[i].t) if first[i].path]
            try:
                texts = reader(order, {first[i].path for i in todo if first[i].path}, cancel=cancel) or {}
            except Cancelled:
                raise
            except Exception:  # noqa: BLE001 - no words then; the pictures and times are still worth having
                log.warning("Reading the screens of %s failed", delivery_id, exc_info=True)
        for i in todo:
            got = texts.get(first[i].path)
            if i in kept:
                screens[str(i)] = kept[i]
            elif got is not None:
                screens[str(i)] = {"title": got.title[:300], "text": got.text[:TEXT_MAX], "from": "ocr",
                                   "ocr": got.engine, "pic": _pic_key(first[i].path)}
            else:
                screens[str(i)] = {"title": "", "text": "", "from": "none"}
        times = "exact" if (exact_before or (spans and shows)) else "keyframe"
        doc = _doc(times, dict(sorted(screens.items(), key=lambda kv: int(kv[0]))), ppt)
        if [dc.astuple(e) for e in entries] != [dc.astuple(e) for e in slides]:
            store.save_slides(delivery_id, entries)
            for f in {e.path for e in slides} - {e.path for e in entries}:
                if f and re.search(r"-p\d+\.jpg$", f):     # Panopto's pictures no screen shows any more
                    try:
                        os.remove(f)
                    except OSError:
                        pass
        store.save_screens(delivery_id, doc)
        try:
            svc.lib.index.screens(delivery_id)             # found by the search (kept: no other write may follow)
            with svc.lib._lock:
                svc.lib.db.commit()
        except AttributeError:
            pass
        except Exception:  # noqa: BLE001 - the search catches up at its next rebuild
            log.debug("Indexing the screens of %s failed", delivery_id, exc_info=True)
        log.info("Screen timeline of %s: %d screens (%s times, PowerPoint %s, %d read)", delivery_id, len(screens),
                 times, ppt, len(texts))
        return entries


def _pictures(svc: Any, delivery_id: str, session: Any, slides: list[Slide]) -> Callable[[PptSpan], Optional[tuple[str, str]]]:
    """Panopto's own picture of a PowerPoint slide (Image.aspx, ~100-250 KB), saved next to the captured ones as
    "<prefix>-pNN.jpg" (NN: the slide's number in the deck). One saved before is used again; none is fetched when
    "Capture slides" is off or Panopto can't be asked."""
    first = next((e for e in slides if e.path and re.search(r"-\d+\.jpg$", e.path)), None)
    if first is None:
        return lambda span: None
    folder = Path(first.path).parent
    prefix = re.sub(r"-\d+\.jpg$", "", Path(first.path).name)
    rel = first.image[:len(first.image) - len(Path(first.path).name)].rstrip("/")
    allowed = bool(getattr(getattr(svc, "settings", None), "capture_slides", True)) and session is not None

    def picture(span: PptSpan) -> Optional[tuple[str, str]]:
        name = f"{prefix}-p{span.slide:02d}.jpg"
        path = folder / name
        if not path.is_file():
            if not allowed or not span.obj:
                return None
            from .panopto import Panopto

            try:
                data = Panopto(session).slide_image(span.obj, span.seq)
            except Exception as exc:  # noqa: BLE001 - the captured pictures stay as they are
                log.info("Panopto's picture of slide %d couldn't be fetched: %s", span.slide, exc)
                return None
            if data is None:
                return None
            tmp = path.with_name(name + ".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, path)
        return f"{rel}/{name}" if rel else name, str(path)

    return picture


# ----------------------------------------------------------------------------- lectures transcribed before
class Unreachable(RuntimeError):
    """Panopto couldn't be asked about a lecture the student asked to capture again (offline, signed out, Panopto
    down): the others asked for would fail the same way."""


class ScreenWorker:
    """In the background, one lecture at a time and at a calm pace, while no lecture is being transcribed: the
    screen timeline of lectures transcribed before 4.0 (their pictures read, one DeliveryInfo request each for the
    PowerPoint events; "no PowerPoint" remembered). Captures made again by themselves only where the old capture is
    known to be short: an index without a key file on a recording with several screen streams (versions before 3.0
    read the first one only), and a tablet Panopto recorded as the camera (no pictures at all). Everything else is
    captured again only when the student asks (capture_again: a lecture or a course), before the rest."""

    START_S = 90.0       # after the app starts
    PACE_S = 3.0         # between two lectures
    OFFLINE_S = 600.0    # Panopto couldn't be reached: its requests wait this long

    def __init__(self, svc: Any) -> None:
        self.svc = svc
        self._cv = threading.Condition()
        self._asked: "collections.OrderedDict[str, None]" = collections.OrderedDict()
        self._asking: set[str] = set()                     # asked, and being captured now
        self._backlog: Optional[list[str]] = None
        self._tried: set[str] = set()
        self._unread: set[str] = set()                    # the backlog's lectures never read (Settings → Storage says
        self._read_total = self._read_done = 0             # how far their reading is: screens_reading events)
        self.current: Optional[str] = None
        self._stop = threading.Event()
        self.cancel = threading.Event()
        self._offline_until = 0.0
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------ the page
    def start(self, delay: Optional[float] = None) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, args=(self.START_S if delay is None else delay,),
                                        name="screens", daemon=True)
        self._thread.start()

    def shutdown(self) -> None:
        self._stop.set()
        self.cancel.set()
        with self._cv:
            self._cv.notify_all()

    def capture_again(self, ids: list[str]) -> list[str]:
        """Capture these lectures' screens again (the student asked), before anything else. Returns those added (one
        already waiting or being captured isn't added twice)."""
        added = []
        with self._cv:
            for did in ids:
                if did not in self._asked and did not in self._asking:
                    self._asked[did] = None
                    added.append(did)
            self._cv.notify_all()
        if added:
            self.start()                                   # (the first wait ends at once for what was asked)
            self._tell()
        return added

    def state(self) -> dict[str, Any]:
        """What the student asked for: the lecture being captured again now, and those waiting."""
        with self._cv:
            return {"current": self.current if self.current in self._asking else None,
                    "waiting": list(self._asked)}

    def _tell(self, **more: Any) -> None:
        try:
            self.svc.emit({"type": "screens", **self.state(), **more})
        except Exception:  # noqa: BLE001 - cosmetic
            log.debug("Telling the page failed", exc_info=True)

    def reading(self) -> dict[str, int]:
        """How far the background reading of earlier lectures' screens is: lectures done and in all (0 and 0 when
        there are none to read)."""
        with self._cv:
            return {"done": self._read_done, "total": self._read_total}

    def _tell_reading(self) -> None:
        """A quiet line in Settings → Storage while the earlier lectures' screens are read (no pop-up)."""
        try:
            self.svc.emit({"type": "screens_reading", **self.reading()})
        except Exception:  # noqa: BLE001 - cosmetic
            log.debug("Telling the page failed", exc_info=True)

    # ------------------------------------------------------------------ the loop
    def _run(self, delay: float) -> None:
        end = time.monotonic() + delay
        with self._cv:                                     # the app starts first; what the student asks, at once
            while not self._stop.is_set() and not self._asked and time.monotonic() < end:
                self._cv.wait(max(0.0, end - time.monotonic()))
        while not self._stop.is_set():
            try:
                item = self._next()
            except Exception:  # noqa: BLE001 - the library busy or changed: looked at again later
                log.warning("Finding the screens still to make failed", exc_info=True)
                item = None
            if item is None:
                with self._cv:
                    self._cv.wait(60.0)
                continue
            self._do(*item)
            self._stop.wait(self.PACE_S)

    def _do(self, did: str, asked: bool) -> None:
        """One lecture, and what the page is told of one the student asked for."""
        self.cancel.clear()
        try:
            self.work(did, again=asked)
        except Cancelled:
            pass
        except Exception as exc:  # noqa: BLE001 - one lecture never stops the others
            log.warning("The screens of %s couldn't be made: %s", did, exc, exc_info=True)
            if asked:
                self._failed(did, exc)
        finally:
            with self._cv:
                self.current = None
                self._asking.discard(did)
                counted = not asked and did in self._unread
                if counted:
                    self._unread.discard(did)
                    self._read_done += 1
            if asked:
                self._tell(done=did)
            if counted:
                self._tell_reading()

    def _failed(self, did: str, exc: BaseException) -> None:
        """Say why a lecture the student asked for failed. When Panopto can't be reached, the rest of what was asked
        would fail the same way, a warning every few seconds (one per recording part): it is dropped, and said once
        with how many lectures (class days) weren't done."""
        from .net import connection_lost, plain_error

        error = plain_error(exc)
        if isinstance(exc, Unreachable) or connection_lost(exc):
            with self._cv:
                left = list(self._asked)
                self._asked.clear()
            lib = self.svc.lib
            days = {getattr(lib.get_recording(x), "day_key", x) for x in [did, *left]}
            if len(days) > 1:
                error = tn(len(days), "Panopto couldn't be reached, so {n} lecture wasn't done. Check your connection "
                           "and try again.", "Panopto couldn't be reached, so {n} lectures weren't done. Check your "
                           "connection and try again.")
        self._tell(failed=did, error=error)

    def _next(self) -> Optional[tuple[str, bool]]:
        with self._cv:
            if self._asked:
                did, _ = self._asked.popitem(last=False)
                self.current = did
                self._asking.add(did)
                return did, True
        jobs = getattr(self.svc, "jobs", None)
        if jobs is not None and jobs.busy():                # transcription first: the rest waits
            return None
        if self._backlog is None:
            self._backlog = self._find_backlog()
            if self._read_total:
                self._tell_reading()
        while self._backlog:
            did = self._backlog.pop(0)
            if did in self._tried:
                continue
            self._tried.add(did)
            with self._cv:
                self.current = did
            return did, False
        return None

    def _find_backlog(self) -> list[str]:
        """Transcribed lectures (newest first) with no timeline, or one made while Panopto couldn't be asked."""
        lib = self.svc.lib
        done = lib.transcribed_ids()
        out, unread = [], set()
        for rec in sorted((r for r in lib.recordings() if r.delivery_id in done), key=lambda r: r.start,
                          reverse=True):
            did = rec.delivery_id
            doc = store.load_screens(did)
            if doc is None:
                if store.slides_path(did).is_file():
                    unread.add(did)                        # its pictures are read
                if did in unread or self.svc.settings.capture_slides:
                    out.append(did)
            elif doc.get("powerpoint") is None and doc.get("screens"):
                out.append(did)
        with self._cv:
            self._unread, self._read_total, self._read_done = unread, len(unread), 0
        return out

    # ------------------------------------------------------------------ one lecture
    def _info(self, rec: Any, asked: bool = False) -> tuple[Optional[dict[str, Any]], Any]:
        """(DeliveryInfo, the session it came with); (None, None) when Panopto can't be asked now (after a failed
        request it isn't asked again for a while, unless the student asked for this lecture). A recording Panopto
        refuses (deleted or replaced there) gets an answer without PowerPoint, and the next lectures are still asked."""
        if time.monotonic() < self._offline_until and not asked:
            return None, None
        from .panopto import NeedPanoptoAuth, Panopto, Refused

        sync = self.svc.sync
        try:
            s = sync.worker_session()
            try:
                info = Panopto(s).delivery_info(rec.delivery_id)
            except NeedPanoptoAuth:
                sync.ensure_panopto(rec.course_id or None)
                s = sync.worker_session()
                info = Panopto(s).delivery_info(rec.delivery_id)
        except Refused as exc:            # Panopto's own words about this one recording: it answered, nothing waits
            if asked:
                raise RuntimeError(t("Panopto says: “{said}” The recording may have been removed or moved. Its "
                                     "transcript stays here.", said=str(exc))) from exc
            log.info("Panopto refused %s: %s", rec.delivery_id, exc)
            return {"Delivery": {}}, None     # "no PowerPoint" is remembered: it leaves the backlog
        except Exception as exc:  # noqa: BLE001 - offline, signed out, Panopto busy: the words are read anyway
            log.info("Panopto couldn't be asked about %s: %s", rec.delivery_id, exc)
            self._offline_until = time.monotonic() + self.OFFLINE_S
            return None, None
        try:
            sync.absorb(s)
        except Exception:  # noqa: BLE001
            log.debug("Keeping Panopto's cookies failed", exc_info=True)
        return info, s

    def work(self, did: str, again: bool = False) -> None:
        """One lecture: its timeline, after capturing its screens again when asked (or known to be short)."""
        from .panopto import Panopto
        from .services import _no_screen, _slides_key

        svc = self.svc
        rec = svc.lib.get_recording(did)
        if rec is None or did.startswith("local-"):
            return
        info, session = self._info(rec, asked=again)
        screens = Panopto(session).screen_sources(info) if info is not None else []
        index = store.slides_path(did)
        has = index.is_file()
        multi_old = has and not index.with_suffix(".key").exists() and \
            sum(1 for src, _ in screens if src.tag == "SCREEN") > 1
        tablet = not has and bool(screens) and all(src.tag == "DV" for src, _ in screens) and \
            svc.settings.capture_slides and not _no_screen(did, _slides_key(screens))
        if again and info is None:
            raise Unreachable(t("Panopto couldn't be reached. Check your connection and try again."))
        before = [dc.astuple(e) for e in store.load_slides(did)]
        if (again or multi_old or tablet) and screens:
            log.info("Capturing the screens of %s again (%s)", did,
                     "asked" if again else "an old capture of one stream" if multi_old else "a tablet")
            job = type("ScreenJob", (), {"id": did, "cancel": self.cancel, "info": info})()
            got = svc.slides_task(job, screens, session, again=again or multi_old)
            if again and not got:                         # (a classroom camera, say: nothing that is a screen)
                raise RuntimeError(t("This lecture has no screen recording in Panopto."))
        elif again:
            build_timeline(svc, did, info, session, fresh=False, cancel=self.cancel)
            raise RuntimeError(t("This lecture has no screen recording in Panopto."))
        elif has or info is not None:
            build_timeline(svc, did, info, session, fresh=False, cancel=self.cancel)
        after = [dc.astuple(e) for e in store.load_slides(did)]
        doc = store.load_screens(did) or {}
        if after and (after != before or doc.get("screens")):
            try:
                svc.write_outputs(did, pack=False)        # the day's file: the pictures and captions as they are now
            except OSError as exc:
                log.info("The files of %s couldn't be written: %s", did, exc)
            self._tell(updated=did, day_key=rec.day_key)
