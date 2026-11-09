"""Figures in the documents IEUStudia writes: the pictures on a course file's pages, found in the PDF itself (a study
pack shows a slide page cropped to its circuit or graph, and its outline only offers pages that have one), and the
lecture slides study notes show next to the text that explains them.

A page's pictures are its raster images of a useful size (not a logo, not the page's background) and its drawings: a
cluster of lines and shapes with only a few words in it (a circuit, a block diagram, a plot), not a table, a frame
around text or a QR code. What can't be told apart that way is not offered: a whole page of text is no figure."""

from __future__ import annotations

import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote, unquote, urlparse

log = logging.getLogger("studia")

Box = tuple[float, float, float, float]            # left, bottom, right, top in PDF points (the origin bottom left)
MIN_SHARE = 0.025          # a picture smaller than this share of the page is a logo or an icon
MAX_SHARE = 0.9            # one bigger is the page's background
CROP_MAX = 0.7             # pictures that cover more of the page than this: the whole page is shown
DRAW_MIN, DRAW_MAX = 0.03, 0.6      # a drawing's share of the page
DRAW_PATHS = 8             # the lines and shapes a drawing has at least
GAP = 7.0                  # points between two shapes of one drawing at most
MARGIN = 12.0              # points around a cropped picture
_cache: dict[tuple[str, int, int], dict[int, list[Box]]] = {}
_lock = threading.Lock()


def _area(b: Box) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _union(boxes: list[Box]) -> Box:
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def _near(a: Box, b: Box, gap: float) -> bool:
    return a[0] - gap <= b[2] and b[0] - gap <= a[2] and a[1] - gap <= b[3] and b[1] - gap <= a[3]


def _clusters(boxes: list[Box], gap: float) -> list[list[Box]]:
    """Boxes that touch (within ``gap``), grouped: one group per drawing."""
    groups: list[list[Box]] = []
    for b in sorted(boxes):
        joined = [g for g in groups if any(_near(b, x, gap) for x in g)]
        merged = [b] + [x for g in joined for x in g]
        groups = [g for g in groups if not any(g is j for j in joined)] + [merged]
    return groups


def _words(textpage: Any, box: Box) -> int:
    try:
        return len(textpage.get_text_bounded(*box).split())
    except Exception:  # noqa: BLE001 - a page whose text can't be read: as if it had none
        return 0


def _text_runs(textpage: Any) -> list[Box]:
    """The page's runs of text (pdfium joins the letters of a line into one box)."""
    try:
        return [textpage.get_rect(i) for i in range(textpage.count_rects())]
    except Exception:  # noqa: BLE001 - as if it had none
        return []


def _running_text(runs: list[Box], box: Box) -> bool:
    """Is there a line of running text in ``box`` (a run of text across most of it): a box of text with lines to
    write on, not a drawing with labels?"""
    width = box[2] - box[0]
    inside = [r for r in runs if r[0] >= box[0] - 2 and r[2] <= box[2] + 2 and r[1] >= box[1] - 2 and r[3] <= box[3] + 2]
    return sum(1 for r in inside if r[2] - r[0] > 0.5 * width) >= 2


def _page_pictures(page: Any, textpage: Any) -> list[Box]:
    import pypdfium2.raw as raw

    w, h = page.get_width(), page.get_height()
    size = w * h
    images: list[Box] = []
    paths: list[Box] = []
    for o in page.get_objects(max_depth=4):
        if o.type not in (raw.FPDF_PAGEOBJ_IMAGE, raw.FPDF_PAGEOBJ_PATH):
            continue
        try:
            l, b, r, t = o.get_bounds()
        except Exception:  # noqa: BLE001 - an object without bounds is not drawn
            continue
        box = (max(0.0, l), max(0.0, b), min(w, r), min(h, t))
        share = _area(box) / size
        if o.type == raw.FPDF_PAGEOBJ_IMAGE:
            if MIN_SHARE <= share <= MAX_SHARE:
                images.append(box)
            continue
        bw, bh = box[2] - box[0], box[3] - box[1]
        if share > 0.06 or (min(bw, bh) < 2.5 and max(bw / w, bh / h) > 0.5):
            continue                        # a frame around a box of text, a background shape, a rule across the page
        if bw < 0.02 * w and bh < 0.02 * h and abs(bw - bh) < 1.0:
            continue                        # a small square: a QR code's, a bullet
        paths.append(box)
    found = [b for b in _clusters(images, GAP)] if images else []
    out = [_union(g) for g in found]
    runs = _text_runs(textpage) if paths else []
    for g in _clusters(paths, GAP):
        box = _union(g)
        share = _area(box) / size
        bw, bh = box[2] - box[0], box[3] - box[1]
        if len(g) < DRAW_PATHS or not DRAW_MIN <= share <= DRAW_MAX or bw < 0.1 * w or bh < 0.04 * h \
                or not 1 / 8 <= bw / bh <= 8:
            continue                        # a few strokes, a strip down the margin
        if box[0] < 1.5 or box[1] < 1.5 or box[2] > w - 1.5 or box[3] > h - 1.5:
            continue                        # shapes that run off the page: the slide template's decoration
        lines = sum(1 for b in g if min(b[2] - b[0], b[3] - b[1]) < 1.5)
        words = _words(textpage, box)
        if lines > 0.8 * len(g) and words > 12:
            continue                        # a grid of lines with text in it: a table (the AI writes it out)
        if words > 18 + 220 * share or _running_text(runs, box):
            continue                        # more text than drawing: a box of text, a form
        out.append(_with_labels(box, runs, w, h))
    return out


def _with_labels(box: Box, runs: list[Box], w: float, h: float) -> Box:
    """A drawing's box grown to hold its labels: the short runs of text that touch it (an axis's name, a resistor's
    value), not a line of the text around it."""
    near = (box[0] - 14, box[1] - 14, box[2] + 14, box[3] + 14)
    labels = [r for r in runs if _near(r, near, 0) and r[2] - r[0] <= 0.15 * w and r[3] - r[1] <= 0.06 * h
              and not any(o is not r and abs(o[1] - r[1]) < 2 and 0 <= o[0] - r[2] < 6 for o in runs)]
    return _union([box, *labels]) if labels else box          # (a run with another just after it: a line of text)


def page_pictures(pdf: Path) -> Optional[dict[int, list[Box]]]:
    """The pictures on each page of a PDF that may be figures: {page number: [box]}, only pages that have one.
    Cached by the file's path, size and time; None when the file can't be read (nothing is known of its pages)."""
    try:
        st = os.stat(pdf)
    except OSError:
        return None
    key = (str(pdf), st.st_size, int(st.st_mtime))
    with _lock:
        if key in _cache:
            return _cache[key]
    import pypdfium2 as pdfium

    from ..materials import PDFIUM_LOCK

    out: dict[int, list[Box]] = {}
    try:
        with PDFIUM_LOCK:
            doc = pdfium.PdfDocument(str(pdf))
            try:
                for i in range(len(doc)):
                    page = doc[i]
                    tp = page.get_textpage()
                    try:
                        boxes = _page_pictures(page, tp)
                    finally:
                        tp.close()
                        page.close()
                    if boxes:
                        out[i + 1] = boxes
            finally:
                doc.close()
    except Exception as exc:  # noqa: BLE001 - a file pdfium can't read: nothing known of its pictures
        log.info("Could not look for pictures in %s: %s", pdf.name, exc)
        return None
    with _lock:
        if len(_cache) > 64:
            _cache.clear()
        _cache[key] = out
    return out


def pages_text(pages: list[int]) -> str:
    """[3, 4, 5, 9] -> "3–5, 9"."""
    out, run = [], []
    for p in sorted(set(pages)):
        if run and p == run[-1] + 1:
            run.append(p)
            continue
        if run:
            out.append(f"{run[0]}–{run[-1]}" if len(run) > 1 else str(run[0]))
        run = [p]
    if run:
        out.append(f"{run[0]}–{run[-1]}" if len(run) > 1 else str(run[0]))
    return ", ".join(out)


def crop_box(boxes: list[Box], width: float, height: float,
             score: Optional[Any] = None) -> Optional[Box]:
    """The part of a page to show for its pictures: around them (pictures side by side together; of pictures far
    apart, with text between them, the one ``score(box)`` rates best, else the biggest), with a margin; None (the
    whole page) when that is most of it."""
    if not boxes:
        return None
    groups = [_union(g) for g in _clusters(list(boxes), 30.0)]
    box = max(groups, key=_area)
    if score and len(groups) > 1:
        rated = sorted(((score(b), _area(b), b) for b in groups), reverse=True)
        if rated[0][0] == 0 or rated[0][0] == rated[1][0]:
            return None                     # can't tell which one is meant: the whole page shows it
        box = rated[0][2]
    if _area(box) / (width * height) > CROP_MAX:
        return None
    return (max(0.0, box[0] - MARGIN), max(0.0, box[1] - MARGIN), min(width, box[2] + MARGIN),
            min(height, box[3] + MARGIN))


def _hint_words(text: str) -> set[str]:
    """The words and numbers a caption and a figure's labels can share ("(3, 25)", "junction", "R1")."""
    return {w for w in re.findall(r"[^\W_]+", (text or "").lower()) if len(w) >= 3 or w.isdigit()}


def figure_image(pdf: Path, page_no: int, dest: Path, width: int = 1400, hint: str = "") -> bool:
    """A JPEG of page ``page_no``'s pictures at ``dest`` (about ``width`` pixels wide; the whole page when they
    cover most of it, or when none was found): False when the page can't be drawn. ``hint``: what the figure shows
    (its caption): of several pictures far apart on the page, the one whose words around it match it best."""
    import av
    import pypdfium2 as pdfium

    from ..materials import PDFIUM_LOCK
    from ..slides import save_jpeg

    boxes = (page_pictures(pdf) or {}).get(page_no) or []
    try:
        with PDFIUM_LOCK:
            doc = pdfium.PdfDocument(str(pdf))
            try:
                if not 1 <= page_no <= len(doc):
                    return False
                page = doc[page_no - 1]
                w, h = page.get_width(), page.get_height()
                want = _hint_words(hint)
                tp = page.get_textpage() if want else None
                try:
                    score = (lambda b: len(want & _hint_words(tp.get_text_bounded(          # its own labels
                        b[0] - 10, b[1] - 10, b[2] + 10, b[3] + 10)))) if tp else None
                    box = crop_box(boxes, w, h, score) or (0.0, 0.0, w, h)
                finally:
                    if tp:
                        tp.close()
                scale = min(4.0, width / max(1.0, box[2] - box[0]))
                arr = page.render(scale=scale, rev_byteorder=True,
                                  crop=(box[0], box[1], w - box[2], h - box[3])).to_numpy()
                page.close()
            finally:
                doc.close()
        frame = av.VideoFrame.from_ndarray(arr[:, :, :3].copy(), format="rgb24")
        dest.parent.mkdir(parents=True, exist_ok=True)
        save_jpeg(frame, dest, max_width=width)
    except Exception as exc:  # noqa: BLE001 - the caller shows the whole page instead (or no figure)
        log.info("Could not draw the picture of %s page %d: %s", pdf.name, page_no, exc)
        return False
    return dest.is_file()


# ------------------------------------------------------------------ lecture slides in study notes
NOTE_FIGURE = re.compile(r"^[ \t]*!\[([^\]\n]*)\]\(\s*Part\s*(\d+)\s*,\s*slide\s*(\d+)\s*\)[ \t]*$", re.I | re.M)
IMAGE_LINE = re.compile(r"!\[([^\]\n]*)\]\(\s*<?([^)\s>]+)>?\s*\)")


def file_link(path: Path, folder: Path) -> str:
    """``path`` as a Markdown link from ``folder``: relative (it keeps working when the whole folder moves, and every
    Markdown viewer shows it), else (another drive) its file:// address."""
    try:
        rel = os.path.relpath(path, folder).replace("\\", "/")
    except ValueError:
        return Path(path).resolve().as_uri()
    return quote(rel, safe="/")


def note_slides_for(sent: list[tuple[str, Any]]) -> dict[tuple[int, int], Path]:
    """The slides sent with study notes, by part and number: [("[Part 2, slide 5]", path)] -> {(2, 5): path}."""
    out: dict[tuple[int, int], Path] = {}
    for label, path in sent:
        m = re.search(r"Part\s*(\d+)\s*,\s*slide\s*(\d+)", str(label), re.I)
        if m and path:
            out[(int(m.group(1)), int(m.group(2)))] = Path(path)
    return out


def place_slides(md: str, slides: dict[tuple[int, int], Path], folder: Path, most: int = 6) -> str:
    """The figure lines study notes write, "![what it shows](Part n, slide m)", as pictures of those slides: Markdown
    images linked from the notes' ``folder``, captioned "what it shows (Part n, slide m)". A line about a slide that
    wasn't sent (``slides``: {(part, slide): its picture}), one shown already or one after ``most`` goes."""
    shown: set[tuple[int, int]] = set()

    def one(m: re.Match) -> str:
        key = (int(m.group(2)), int(m.group(3)))
        path = slides.get(key)
        if path is None or key in shown or len(shown) >= most:
            return ""
        shown.add(key)
        what = " ".join(m.group(1).replace("[", "(").replace("]", ")").split()).rstrip(".")
        return f"![{what + ' ' if what else ''}(Part {key[0]}, slide {key[1]})]({file_link(path, folder)})"

    return re.sub(r"\n{3,}", "\n\n", NOTE_FIGURE.sub(one, md))


def linked_images(md: str, base: str | Path | None) -> str:
    """Markdown with its pictures (on lines of their own) linked by their full file:// address, for a copy saved
    somewhere else; a picture that isn't on this PC is left out. Without ``base`` (no pictures of ours) as it is."""
    def one(m: re.Match) -> str:
        path = local_image(m.group(2), base)
        return f"![{m.group(1)}]({path.as_uri()})" if path else ""

    if not base or "![" not in (md or ""):
        return md
    return re.sub(r"^[ \t]*" + IMAGE_LINE.pattern + r"[ \t]*$", one, md, flags=re.M)


_SOURCE = re.compile(r"\s*\((Part\s*\d+\s*,\s*slide\s*\d+)\)\s*$", re.I)


def caption_parts(alt: str) -> tuple[str, str]:
    """A picture's caption and the source at its end: "RC circuit (Part 2, slide 7)" -> ("RC circuit",
    "Part 2, slide 7")."""
    m = _SOURCE.search(alt or "")
    return ((alt[:m.start()].strip(), m.group(1)) if m else ((alt or "").strip(), ""))


def image_html(alt: str, src: str, base: str | Path | None) -> str:
    """A picture on a line of its own in a document's Markdown, as the documents show figures: the picture (only one
    on this PC: local_image), its caption and source under it; "" when it isn't there."""
    import html

    path = local_image(src, base)
    if path is None:
        return ""
    cap, source = caption_parts(alt)
    src_html = f' <span class="sp-src">({html.escape(source, quote=False)})</span>' if source else ""
    return (f'<figure class="sp-fig sp-frame"><img src="{html.escape(path.as_uri())}" alt="">'
            f"<figcaption>{html.escape(cap, quote=False)}{src_html}</figcaption></figure>")


def local_image(src: str, base: str | Path | None) -> Optional[Path]:
    """The picture a Markdown image in a document links to, when it is one on this PC: a path relative to ``base``
    (the document's folder) or a file:// address, ending in .jpg, .jpeg or .png and there. Never a web address."""
    src = (src or "").strip()
    if not src or not re.search(r"\.(?:jpe?g|png)$", src.split("?")[0], re.I):
        return None
    if src.lower().startswith("file:"):
        u = urlparse(src)
        if u.netloc not in ("", "localhost"):
            return None                     # another computer's share: never
        raw = unquote(u.path)
        path = Path(raw[1:] if re.match(r"/[A-Za-z]:", raw) else raw)
    elif re.match(r"^[A-Za-z][\w+.-]*:", src) or src.startswith(("//", "\\\\")) or not base:
        return None
    else:
        path = Path(base) / unquote(src)
    try:
        path = path.resolve()
    except OSError:
        return None
    return path if path.is_file() else None
