"""Windows' own text recognition (Windows.Media.Ocr) for the screen timeline (screens.py).

Nothing to install: pythonnet, which the app already has for its window (pywebview's WinForms backend loads .NET
Framework through it), projects the WinRT types from .NET Framework, as Windows PowerShell 5.1 does. It reads the
languages Windows has (Settings → Time & language → Language: a language's "Optical character recognition" part):
the student's own (the first of their Windows languages that has it) and Turkish when it is there; each line is
kept as the engine that read more of it read it. ~0.4 s a picture. No handwriting; formulas come out in pieces.

Only the words of the lecture go in: a picture is read in the part of the screen that changes from screen to screen
(the slide, the shared screen in a meeting window: not the toolbars, the taskbar or the participants' tiles), and
lines that look like a meeting's chat (a name and a time, "joined the meeting", the lines under them), words found
in the same place on most pictures (names on tiles, window titles) and student numbers or e-mail addresses are left
out: classmates' names and messages must never reach an AI prompt or the search.

Where there is no OCR (Linux, a Windows without the WinRT parts, no language with OCR), available() is False and
the timeline is made without words; nothing else changes.
"""

from __future__ import annotations

import dataclasses as dc
import logging
import re
import sys
import threading
from collections import Counter
from typing import TYPE_CHECKING, Any, Iterable, Optional

if TYPE_CHECKING:
    import numpy as np

log = logging.getLogger("studia")
_lock = threading.Lock()                 # one recognition at a time (the job's capture and the background job)
_state: dict[str, Any] = {}              # the WinRT types and engines, made once
GRID = 4                                 # the slide area is found on a grid of 4x4 thumbnail pixels
MIN_W = 900                              # a slide area narrower than this (in the picture's pixels) is read at 2x
TEXT_MAX = 2000


@dc.dataclass
class Line:
    text: str
    x: float
    y: float
    w: float
    h: float

    @property
    def cx(self) -> float:
        return self.x + self.w / 2


@dc.dataclass
class Reading:
    title: str
    text: str
    engine: str


# ----------------------------------------------------------------------------- WinRT through pythonnet
def _winrt() -> dict[str, Any]:
    """The WinRT types (raises where there are none)."""
    if "types" in _state:
        return _state["types"]
    if sys.platform != "win32":
        raise RuntimeError("Windows OCR is Windows only")
    import clr  # noqa: F401  (pythonnet, as pywebview loads it: .NET Framework)
    import System
    from System import Type

    clr.AddReference("System.Runtime.WindowsRuntime")

    def winrt(name: str, assembly: str = "Windows.Foundation") -> Any:
        typ = Type.GetType(f"{name}, {assembly}, ContentType=WindowsRuntime")
        if typ is None:
            raise RuntimeError(f"WinRT type {name} not found")
        return typ

    rt = "System.Runtime.WindowsRuntime, Version=4.0.0.0, Culture=neutral, PublicKeyToken=b77a5c561934e089"
    ext = Type.GetType("System.WindowsRuntimeSystemExtensions, " + rt)
    buf = Type.GetType("System.Runtime.InteropServices.WindowsRuntime.WindowsRuntimeBufferExtensions, " + rt)
    if ext is None or buf is None:
        raise RuntimeError("System.Runtime.WindowsRuntime not found")
    types = {
        "System": System, "Type": Type,
        "OcrEngine": winrt("Windows.Media.Ocr.OcrEngine"), "OcrResult": winrt("Windows.Media.Ocr.OcrResult"),
        "Language": winrt("Windows.Globalization.Language"),
        "SoftwareBitmap": winrt("Windows.Graphics.Imaging.SoftwareBitmap"),
        "BitmapPixelFormat": winrt("Windows.Graphics.Imaging.BitmapPixelFormat"),
        "as_task": next(m for m in ext.GetMethods() if m.Name == "AsTask" and len(m.GetParameters()) == 1
                        and m.GetParameters()[0].ParameterType.Name == "IAsyncOperation`1"),
        "as_buffer": next(m for m in buf.GetMethods() if m.Name == "AsBuffer" and len(m.GetParameters()) == 1),
    }
    _state["types"] = types
    return types


def _prop(obj: Any, name: str, typ: Any = None) -> Any:
    return (typ or obj.GetType()).GetProperty(name).GetValue(obj, None)


def _items(obj: Any) -> Iterable[Any]:
    from System.Collections import IEnumerable

    e = IEnumerable(obj).GetEnumerator()
    while e.MoveNext():
        yield e.Current


def languages() -> list[str]:
    """The OCR languages Windows has here ("en-US", "tr-TR"...); [] where there is no OCR."""
    try:
        with _lock:
            w = _winrt()
            langs = w["OcrEngine"].GetProperty("AvailableRecognizerLanguages").GetValue(None, None)
            return [str(_prop(lang, "LanguageTag", w["Language"])) for lang in _items(langs)]
    except Exception as exc:  # noqa: BLE001 - no OCR here
        log.debug("Windows OCR isn't available: %s", exc)
        return []


def _engines() -> list[tuple[str, Any]]:
    """[(language tag, engine)]: the student's own languages' engine, and Turkish's when Windows has it."""
    if "engines" in _state:
        return _state["engines"]
    w = _winrt()
    sysm, args = w["System"], w["System"].Array[w["System"].Object]
    out: list[tuple[str, Any]] = []
    own = w["OcrEngine"].GetMethod("TryCreateFromUserProfileLanguages").Invoke(None, None)
    if own is not None:
        lang = _prop(own, "RecognizerLanguage")
        out.append((str(_prop(lang, "LanguageTag")), own))
    have = [str(_prop(lang, "LanguageTag", w["Language"])) for lang in
            _items(w["OcrEngine"].GetProperty("AvailableRecognizerLanguages").GetValue(None, None))]
    tr = next((x for x in have if x.lower().startswith("tr")), None)
    if tr and not any(tag.lower().startswith("tr") for tag, _ in out):
        lang = sysm.Activator.CreateInstance(w["Language"], args([tr]))
        eng = w["OcrEngine"].GetMethod("TryCreateFromLanguage").Invoke(None, args([lang]))
        if eng is not None:
            out.append((tr, eng))
    _state["engines"] = out
    return out


def available() -> bool:
    """Windows' OCR can be used here: the WinRT types load and at least one language has it."""
    if "available" not in _state:
        try:
            with _lock:
                _state["available"] = bool(_engines())
        except Exception as exc:  # noqa: BLE001 - Linux, an old Windows, a build without pythonnet
            log.info("Windows OCR isn't available: %s", exc)
            _state["available"] = False
    return bool(_state["available"])


def _recognize(engine: Any, gray: np.ndarray) -> list[Line]:
    import numpy as np
    from System import IntPtr
    from System.Runtime.InteropServices import Marshal

    w = _winrt()
    sysm = w["System"]
    args = sysm.Array[sysm.Object]
    g = np.ascontiguousarray(gray, dtype=np.uint8)
    h, wd = g.shape
    # the picture goes to .NET in one copy: pythonnet's own conversion (Array[Byte](bytes)) goes byte by byte while
    # holding Python's lock, 0.3-1 s for a slide, and every click in the window waited for it
    data = sysm.Array.CreateInstance(sysm.Byte, g.nbytes)
    Marshal.Copy(IntPtr(g.ctypes.data), data, 0, g.nbytes)
    buf = w["as_buffer"].Invoke(None, args([data]))
    fmt = sysm.Enum.Parse(w["BitmapPixelFormat"], "Gray8")
    make = next(m for m in w["SoftwareBitmap"].GetMethods() if m.Name == "CreateCopyFromBuffer"
                and len(m.GetParameters()) == 4)
    bmp = make.Invoke(None, args([buf, fmt, sysm.Int32(wd), sysm.Int32(h)]))
    op = engine.GetType().GetMethod("RecognizeAsync").Invoke(engine, args([bmp]))
    task = w["as_task"].MakeGenericMethod(sysm.Array[w["Type"]]([w["OcrResult"]])).Invoke(None, args([op]))
    res = _prop(task, "Result")
    out = []
    for line in _items(_prop(res, "Lines")):
        boxes = [_prop(word, "BoundingRect") for word in _items(_prop(line, "Words"))]
        if not boxes:
            continue
        xs = [float(_prop(r, "X")) for r in boxes]
        ys = [float(_prop(r, "Y")) for r in boxes]
        x1 = max(float(_prop(r, "X")) + float(_prop(r, "Width")) for r in boxes)
        y1 = max(float(_prop(r, "Y")) + float(_prop(r, "Height")) for r in boxes)
        out.append(Line(" ".join(str(_prop(line, "Text")).split()), min(xs), min(ys), x1 - min(xs), y1 - min(ys)))
    return out


CHECK_PICTURE = "assets/ocr-check.png"         # "Lecture 7: Ohm's law and circuits", black on white
CHECK_WORDS = ("lecture", "ohm", "law", "circuits")


def self_check() -> dict[str, Any]:
    """For the app's --selftest (a release build): Windows' text reading works inside it, from loading pythonnet and
    the WinRT types to reading a small picture with a few words (as the screen timeline reads a slide). "skipped" on
    Linux and where Windows has no OCR language; "failed" when something of it doesn't load or the words don't come
    back. Says nothing on its own: the result is the self-test's."""
    if sys.platform != "win32":
        return {"ok": True, "status": "skipped", "why": "Windows' text reading is Windows only"}
    from pathlib import Path

    try:
        with _lock:
            _winrt()
            engines = _engines()
    except Exception as exc:  # noqa: BLE001 - said in the report
        return {"ok": False, "status": "failed", "why": f"it doesn't load: {exc!r}"[:300]}
    if not engines:
        return {"ok": True, "status": "skipped", "why": "Windows has no OCR language here"}
    gray = _gray(str(Path(__file__).parent / CHECK_PICTURE))
    if gray is None:
        return {"ok": False, "status": "failed", "why": f"{CHECK_PICTURE} can't be read"}
    try:
        lines, engine = read_lines(gray)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "status": "failed", "why": f"reading failed: {exc!r}"[:300]}
    text = " ".join(ln.text for ln in lines)
    found = [w for w in CHECK_WORDS if w in text.lower()]
    ok = len(found) >= len(CHECK_WORDS) - 1          # (one word misread is still Windows reading)
    return {"ok": ok, "status": "read" if ok else "failed", "engine": engine, "text": text[:200],
            **({} if ok else {"why": "the words didn't come back"})}


def max_side() -> int:
    try:
        return int(_winrt()["OcrEngine"].GetProperty("MaxImageDimension").GetValue(None, None))
    except Exception:  # noqa: BLE001
        return 2600


def read_lines(gray: np.ndarray) -> tuple[list[Line], str]:
    """A grey picture's lines (pixel boxes), read by each engine; where two engines read the same line, the longer
    reading is kept. And the engines' label ("windows en-US", "windows en-US+tr-TR")."""
    with _lock:
        engines = _engines()
        readings = [(tag, _recognize(eng, gray)) for tag, eng in engines]
    lines: list[Line] = []
    for _, got in readings:
        for ln in got:
            same = next((i for i, o in enumerate(lines) if _overlap(o, ln) > 0.5), None)
            if same is None:
                lines.append(ln)
            elif _score(ln.text) > _score(lines[same].text):
                lines[same] = ln
    return lines, "windows " + "+".join(tag for tag, _ in engines)


# letters only a Turkish reading has: the English engine reads "Olasılık" as "Olaslllk", about as long, so a line's
# readings are weighed by them too, not by length alone (measured on Turkish slides: 81 % of words read with length
# alone, the engines' English reading of a Turkish line was often kept)
_TR_ONLY = frozenset("ıİğĞşŞ")


def _score(text: str) -> int:
    return len(text) + 5 * sum(c in _TR_ONLY for c in text)


def _overlap(a: Line, b: Line) -> float:
    ix = max(0.0, min(a.x + a.w, b.x + b.w) - max(a.x, b.x))
    iy = max(0.0, min(a.y + a.h, b.y + b.h) - max(a.y, b.y))
    inter = ix * iy
    small = min(a.w * a.h, b.w * b.h)
    return inter / small if small > 0 else 0.0


# ----------------------------------------------------------------------------- the part of the screen to read
def _longest_run(ok: Iterable[bool], gap: int = 1) -> tuple[int, int]:
    """[start, end) of the longest run of True (gaps of up to ``gap`` False inside it allowed)."""
    best, start, miss, end = (0, 0), None, 0, 0
    for i, v in enumerate(list(ok) + [False] * (gap + 1)):
        if v:
            start = i if start is None else start
            miss, end = 0, i + 1
            if end - start > best[1] - best[0]:
                best = (start, end)
        elif start is not None:
            miss += 1
            if miss > gap:
                start, miss = None, 0
    return best


def content_box(thumbs: list[Optional[np.ndarray]]) -> Optional[tuple[float, float, float, float]]:
    """The part of the screen that changes from screen to screen, as shares of the picture (x0, y0, x1, y1). On a grid
    of 4x4 thumbnail pixels, the cells where consecutive pictures differ often enough; then the longest band of rows,
    and of columns, where many of them are: the slide or the shared screen, without a meeting's chat beside it, the
    participants' tiles above it or the taskbar below it (they change too, but in few cells). On the audit's
    lectures this is the hand-made crop to a few pixels. None when it can't be told (under 3 pictures, no change)."""
    import numpy as np

    ths = [t for t in thumbs if t is not None]
    ths = [t for t in ths if t.shape == ths[0].shape] if ths else []
    if len(ths) < 3:
        return None
    h, w = ths[0].shape
    heat = np.zeros((h, w), np.float64)
    for a, b in zip(ths, ths[1:]):
        heat += np.abs(a.astype(np.int16) - b.astype(np.int16)) > 28
    heat /= len(ths) - 1
    gh, gw = h // GRID, w // GRID
    cells = heat[:gh * GRID, :gw * GRID].reshape(gh, GRID, gw, GRID).mean(axis=(1, 3))
    if not (cells > 0).any():
        return None
    on = cells >= 0.3 * float(np.percentile(cells[cells > 0], 90))
    rows, cols = on.sum(axis=1), on.sum(axis=0)
    r0, r1 = _longest_run(rows >= 0.25 * rows.max())
    c0, c1 = _longest_run(cols >= 0.25 * cols.max())
    if r1 - r0 < 2 or c1 - c0 < 2:
        return None
    # half a cell more on every side: a slide's edge letters, a line that changed only a little
    return (max(0.0, (c0 - 0.5) / gw), max(0.0, (r0 - 0.5) / gh), min(1.0, (c1 + 0.5) / gw), min(1.0, (r1 + 0.5) / gh))


# ----------------------------------------------------------------------------- what is left out
_TIME = r"(?:[01]?\d|2[0-3])[:.][0-5]\d(?:\s?[AP]M)?"
_NAME = r"[A-ZÇĞİÖŞÜ][\wçğıöşüÇĞİÖŞÜ'’.-]+"
CHAT_HEAD = re.compile(rf"^(?:{_NAME}\s+){{1,4}}{_NAME}\s*(?:\(\w+\)\s*)?(?:,\s*)?(?:Dün\s+|Yesterday\s+|Bugün\s+|Today\s+)?{_TIME}\s*$")
CHAT_EVENT = re.compile(r"toplantıya (?:davet edildi|katıldı)|toplantıdan ayrıldı|joined the meeting|left the meeting|"
                        r"was invited to the meeting|added .* to the meeting|katılımcılar yanıtlanıyor|"
                        r"bir ileti yazın|type a message|meeting chat|toplantı sohbeti|raised (?:their|a) hand|"
                        r"el kaldırdı|lobide bekliyor|waiting in the lobby|in the lobby|kabul ediliyor|being admitted",
                        re.I)
BUTTON = re.compile(r"(?:reddet|kabul et|admit|deny|view lobby|lobiyi görüntüle|herkes lobi|lobby|dismiss|join)", re.I)
PRIVATE = re.compile(r"\b20\d{9}\b|[\w.+-]+@[\w-]+\.[\w.]+")      # IEU student numbers, e-mail addresses
CUT = re.compile(r"(?:\.\s*\.|…|„|\.\s*-|_)[\s.]*$")                # a name cut short on a tile: "Jonatha…", "Mehme.-"


def _chat_column(lines: list[Line], width: float) -> Optional[float]:
    """Where a meeting's chat starts (its left edge, in pixels) when the lines show one: a name with a time ("Ayşe
    Yılmaz 09:16"). (A notice such as "waiting in the lobby" can be anywhere: it goes on its own.)"""
    heads = [ln.x for ln in lines if CHAT_HEAD.match(ln.text)]
    if not heads:
        return None
    return min(heads) - 0.04 * width


def _short(ln: Line) -> bool:
    return len(ln.text) <= 18 and len(ln.text.split()) <= 3


def _name_like(ln: Line) -> bool:
    """A short line with no plain lowercase word in it ("Beren T", "Özgün", "qaOan" as OCR reads a name), unlike a
    slide's "then the resulting"."""
    return _short(ln) and not any(re.fullmatch(r"[a-zçğıöşü]{3,}[.,;:]?", tok) for tok in ln.text.split())


def _tile_rows(lines: list[Line]) -> set[int]:
    """Name-like lines side by side on one line, well apart (two or more): names on the participants' tiles, a
    notice's buttons. (A slide's own short labels are rarely so.)"""
    short = [ln for ln in lines if _name_like(ln)]
    out: set[int] = set()
    for a in short:
        for b in short:
            gap = max(b.x - (a.x + a.w), a.x - (b.x + b.w))
            if b is not a and abs((b.y + b.h / 2) - (a.y + a.h / 2)) <= 0.6 * max(a.h, b.h) \
                    and gap >= 2.5 * max(a.h, b.h):
                out.add(id(a))
                break
    return out


def _near_notice(lines: list[Line]) -> set[int]:
    """The lines of a meeting notice ("... is waiting in the lobby", its name line and buttons): those within a few
    lines above and below one, beside it."""
    out: set[int] = set()
    for n in (ln for ln in lines if CHAT_EVENT.search(ln.text)):
        for ln in lines:
            if abs(ln.y - n.y) <= 3 * n.h and ln.x < n.x + n.w + 4 * n.h and ln.x + ln.w > n.x - 4 * n.h:
                out.add(id(ln))
    return out


def clean(pictures: list[list[Line]], width: float,
          area: Optional[tuple[float, float]] = None) -> list[list[Line]]:
    """The lines of a lecture's pictures without what isn't the lecture's: a meeting's chat (from the column where a
    name and a time or a meeting notice appear, on every picture once seen on one), meeting notices and their lines,
    the names on the participants' tiles (short lines side by side, a name cut short, a short line on many pictures),
    lines found in the same place on most pictures (window titles, a presenter's name tag) and lines with a student
    number or an e-mail address. In a meeting's window (a chat, tiles or a notice seen on some picture), the short
    lines at the very top of the read part too (``area``: its top and bottom in pixels): the tiles' row of names."""
    cols = [c for c in (_chat_column(p, width) for p in pictures) if c is not None]
    chat_x = min(cols) if cols else None
    w = max(1.0, width)
    meeting = chat_x is not None or sum(1 for p in pictures if _tile_rows(p) or _near_notice(p)) >= 1
    top = area[0] + 0.1 * (area[1] - area[0]) if meeting and area is not None else None

    def place(ln: Line) -> tuple[str, int, int]:
        return ln.text.lower(), round(ln.cx / w * 20), round(ln.y / w * 20)

    seen: Counter[tuple[str, int, int]] = Counter()
    words: Counter[str] = Counter()
    for p in pictures:
        for key in {place(ln) for ln in p}:
            seen[key] += 1
        for text in {ln.text.lower() for ln in p if _name_like(ln)}:
            words[text] += 1
    often = max(3, int(0.3 * len(pictures) + 0.999)) if len(pictures) >= 5 else None
    short_often = max(3, int(0.15 * len(pictures) + 0.999)) if len(pictures) >= 5 else None
    out = []
    for p in pictures:
        drop = _tile_rows(p) | _near_notice(p)
        keep = []
        for ln in p:
            if id(ln) in drop or (chat_x is not None and ln.x >= chat_x):
                continue
            if top is not None and ln.y + ln.h / 2 < top and _short(ln):
                continue
            if CHAT_HEAD.match(ln.text) or CHAT_EVENT.search(ln.text) or PRIVATE.search(ln.text):
                continue
            if _short(ln) and (CUT.search(ln.text) or BUTTON.fullmatch(ln.text.strip())):
                continue
            if often is not None and seen[place(ln)] >= often:
                continue
            if short_often is not None and _name_like(ln) and words[ln.text.lower()] >= short_often:
                continue
            if not re.search(r"[^\W_]", ln.text):          # punctuation only
                continue
            keep.append(ln)
        out.append(keep)
    return out


def title_and_text(lines: list[Line]) -> tuple[str, str]:
    """A picture's title (its top line, when it is clearly bigger than the others) and its text (the other lines,
    top to bottom, one a line)."""
    if not lines:
        return "", ""
    lines = sorted(lines, key=lambda ln: (ln.y, ln.x))
    heights = sorted(ln.h for ln in lines)
    median = heights[(len(heights) - 1) // 2]
    top = lines[0]
    title = ""
    if len(lines) >= 2 and top.h >= 1.25 * median and len(top.text) >= 3:
        title, lines = top.text, lines[1:]
    text = "\n".join(ln.text for ln in lines)
    return title[:300], text[:TEXT_MAX]


# ----------------------------------------------------------------------------- a lecture's pictures
def _gray(path: str, width: Optional[int] = None) -> Optional[np.ndarray]:
    import av

    try:
        with av.open(path) as c:
            f = next(c.decode(video=0))
    except Exception:  # noqa: BLE001 - a picture that can't be read is passed over
        return None
    w, h = f.width, f.height
    if width:
        h, w = max(2, int(round(h * width / w / 2)) * 2), width
    return f.reformat(width=w, height=h, format="gray").to_ndarray()


def read_screens(paths: list[str], want: set[str], cancel: Optional[threading.Event] = None) -> dict[str, Reading]:
    """The title and text of the pictures in ``want``, of a lecture whose pictures are ``paths`` (in time order: the
    part of the screen that changes is found from all of them)."""
    import numpy as np

    from .cancel import Cancelled

    thumbs = [_gray(p, 192) for p in paths]
    box = content_box([t.astype(np.int16) if t is not None else None for t in thumbs])
    todo = [p for p in paths if p in want]
    pictures: list[list[Line]] = []
    widths, engine, area = [], "", None
    limit = max_side()
    for p in todo:
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        g = _gray(p)
        if g is None:
            pictures.append([])
            widths.append(1.0)
            continue
        H, W = g.shape
        x0, y0, x1, y1 = (0, 0, W, H) if box is None else (int(box[0] * W), int(box[1] * H), int(box[2] * W),
                                                             int(box[3] * H))
        area = area or (float(y0), float(y1))
        crop = np.ascontiguousarray(g[y0:y1, x0:x1])
        scale = 1.0
        if crop.shape[1] < MIN_W and 2 * max(crop.shape) <= limit:      # a small slide area: read at twice the size
            crop = np.ascontiguousarray(np.repeat(np.repeat(crop, 2, axis=0), 2, axis=1))
            scale = 2.0
        elif max(crop.shape) > limit:
            step = int(np.ceil(max(crop.shape) / limit))
            crop = np.ascontiguousarray(crop[::step, ::step])
            scale = 1.0 / step
        lines, engine = read_lines(crop)
        pictures.append([Line(ln.text, ln.x / scale + x0, ln.y / scale + y0, ln.w / scale, ln.h / scale)
                         for ln in lines])
        widths.append(float(W))
    width = max(widths, default=1.0)
    out = {}
    for p, lines in zip(todo, clean(pictures, width, area if box is not None else None)):
        # (no title from OCR: on handwriting the biggest line is often not words; the slide's words are the text)
        title, text = title_and_text(lines)
        out[p] = Reading("", "\n".join(x for x in (title, text) if x)[:TEXT_MAX], engine)
    return out
