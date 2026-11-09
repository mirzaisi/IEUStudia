"""The student's own files in a chat (uploads), what a chat sends to the AI ("What this chat sees"), and the in-app
viewers of all of it.

An upload is copied into the app's data (``chat-files/<chat id>/<upload id>/``, a new chat's in ``draft-<token>`` until
its first question), never referenced from where it was. A photo or screenshot is kept as a JPEG (the original too),
the text of a PDF, DOCX, PPTX or text file is read once and kept. They go to the AI with the rest of the chat's
material: their text under STUDENT'S FILES in the chat's instructions, photos and the pages of a PDF as pictures
(ChatGPT, Claude and Codex look at pictures), within a budget of ``MAX_PICTURES`` pictures and ``MAX_TEXT_TOKENS``
of text. The offline model reads text only, the passages that fit next to each question.
"""

from __future__ import annotations

import base64
import dataclasses as dc
import json
import logging
import os
import re
import shutil
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Optional

from . import ai
from .chatslides import PagePicture
from .i18n import t

log = logging.getLogger("studia")

MAX_FILES = 100                    # uploads per chat (the app's own limit: the AIs take far more per request)
MAX_PICTURES = 500                 # pictures from uploads per chat at most (photos and PDF pages); each AI's own limit
                                   # and its window decide first (chatlimits.py), shared with the course's slides
MAX_TEXT_TOKENS = 400_000          # of upload text per chat at most (all of it goes when the AI's window holds it)
PAGE_CHARS = 12_000                # a page's text beyond this is cut (unusual: then read in batches, or said so)
PAGE_BYTES = 95_000                # an average PDF page picture as sent (1024 px JPEG)
SHORT_TEXT = 300                   # a PDF page with less text than this is mostly a figure, a formula or handwriting
PICTURE_WIDTH = 1600               # a photo as kept (the AI gets a 1024 px copy)
SENT_WIDTH = 1024                  # a picture as the AI gets it (ai._small_jpeg)
FIGURE_TOKENS = 1050               # a PowerPoint slide's figure as sent (its shape unknown until drawn: as a 4:3 one)
A4 = 1.414                         # a page's height / width when it isn't known (a PDF added before it was kept)
PASSAGE_CHARS = 1500               # the offline model: the files' text in pieces of about this much...
LOCAL_CHARS = 3200                 # ...and the pieces that match a question, at most this much with it
MB = 1024 * 1024
KINDS = {
    "image": ("png", "jpg", "jpeg", "webp", "gif", "bmp"),     # not HEIC: an iPhone's are tile grids (check says so)
    "pdf": ("pdf",),
    "doc": ("docx", "pptx"),
    "text": ("txt", "md", "markdown", "csv", "tsv", "json", "xml", "yaml", "yml", "tex", "log", "py", "ipynb", "m",
             "r", "c", "h", "cpp", "hpp", "cc", "cs", "java", "js", "ts", "html", "css", "sql", "sh", "bat", "ps1",
             "go", "rs", "kt", "swift", "php", "rb", "lua", "asm", "s", "v", "vhd", "vhdl", "sv", "ino", "mat"),
}
MAX_BYTES = {"image": 15 * MB, "pdf": 40 * MB, "doc": 40 * MB, "text": 5 * MB}
_TARGET = re.compile(r"^(?:[0-9a-f]{12}|draft-[0-9a-f]{8,32})$")
_ID = re.compile(r"^[0-9a-f]{10}$")
_TOKEN = re.compile(r"^[0-9a-f]{8,32}$")


class Refused(ValueError):
    """A file that can't be added, with the plain reason (PyAV's errors are ValueErrors too: those are not this)."""


def root() -> Path:
    from .paths import profile_dir

    return profile_dir() / "chat-files"


def kind_of(name: str) -> str:
    ext = Path(name or "").suffix.lower().lstrip(".")
    return next((k for k, exts in KINDS.items() if ext in exts), "")


def folder(target: str) -> Path:
    """A chat's (its id) or a new chat's (``draft-<token>``) folder of uploads."""
    if not _TARGET.match(target or ""):
        raise ValueError(t("This chat no longer exists."))
    return root() / target


def draft_target(token: str) -> str:
    return f"draft-{token}" if _TOKEN.match(token or "") else ""


# ------------------------------------------------------------------ receiving a file
def receive(token: str, data_b64: str, first: bool) -> int:
    """One piece of a file the page sends (in pieces of about 1.5 MB); the size so far."""
    if not _TOKEN.match(token or ""):
        raise ValueError("bad upload token")
    part = root() / "incoming" / f"{token}.part"
    part.parent.mkdir(parents=True, exist_ok=True)
    data = base64.b64decode(data_b64 or "")
    with open(part, "wb" if first else "ab") as f:
        f.write(data)
    size = part.stat().st_size
    if size > max(MAX_BYTES.values()):
        part.unlink(missing_ok=True)
        raise ValueError(t("That file is too big ({size}). Files up to {max} can be added.", size=_mb(size),
                           max=_mb(max(MAX_BYTES.values()))))
    return size


def _mb(n: int) -> str:
    return f"{max(0.1, round(n / MB, 1)):g} MB"


def limits() -> dict[str, Any]:
    """For the page: what can be added, before sending a byte."""
    return {"files": MAX_FILES, "pictures": MAX_PICTURES, "bytes": MAX_BYTES,
            "kinds": {k: list(v) for k, v in KINDS.items()}}


def check(target: str, name: str, size: int) -> str:
    """The upload's kind, or a plain message why it can't be added."""
    kind = kind_of(name)
    if Path(name or "").suffix.lower() in (".heic", ".heif"):        # an iPhone's are grids of tiles: not read here
        raise ValueError(t("{name} is a HEIC photo, which can't be added. Share it as JPG instead (on an iPhone: "
                           "Settings → Camera → Formats → Most Compatible), or take a screenshot of it.", name=name))
    if not kind:
        raise ValueError(t("{name} can't be added: photos (PNG, JPG, WEBP), PDFs, Word and PowerPoint files and "
                           "text files (TXT, MD, CSV, code) can.", name=name or "?"))
    if size > MAX_BYTES[kind]:
        raise ValueError(t("{name} is too big ({size}). Files of this kind can be up to {max}.", name=name,
                           size=_mb(size), max=_mb(MAX_BYTES[kind])))
    if size <= 0:
        raise ValueError(t("{name} is empty.", name=name))
    if len(listing(target)) >= MAX_FILES:
        raise ValueError(t("A chat can hold up to {n} of your files. Remove one first.", n=MAX_FILES))
    return kind


def finish(target: str, token: str, name: str) -> dict[str, Any]:
    """The received file becomes an upload of ``target``: copied in, its picture and text made ready."""
    part = root() / "incoming" / f"{token}.part"
    if not _TOKEN.match(token or "") or not part.is_file():
        raise ValueError(t("The file did not arrive. Try again."))
    try:
        return add(target, part, name)
    finally:
        part.unlink(missing_ok=True)


def add(target: str, src: Path, name: str) -> dict[str, Any]:
    """Copy ``src`` in as an upload named ``name``; raises ValueError with a plain message when it can't be."""
    name = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", Path(str(name or "")).name).strip() or "file"
    kind = check(target, name, src.stat().st_size)
    ext = Path(name).suffix.lower().lstrip(".")
    uid = uuid.uuid4().hex[:10]
    box = folder(target) / uid
    box.mkdir(parents=True, exist_ok=True)
    original = box / f"original.{ext}"
    shutil.copyfile(src, original)
    meta: dict[str, Any] = {"id": uid, "name": name, "kind": kind, "ext": ext, "size": original.stat().st_size,
                            "added": time.time(), "pages": 0, "chars": 0}
    try:
        if kind == "image":
            meta["width"], meta["height"] = _to_jpeg(original, box / "picture.jpg")
        else:
            pages = _read_pages(original, kind, name)
            if not any(p.strip() for p in pages) and kind != "pdf":
                raise Refused(t("{name} has no text that could be read.", name=name))
            (box / "text.json").write_text(json.dumps(pages, ensure_ascii=False), encoding="utf-8")
            meta["pages"], meta["chars"] = len(pages), sum(len(p) for p in pages)
            if kind == "pdf" and not meta["pages"]:
                raise Refused(t("{name} has no pages.", name=name))
            if kind == "pdf":
                meta["ratio"] = _pdf_ratio(original)
    except Refused:
        shutil.rmtree(box, ignore_errors=True)
        raise
    except Exception as exc:  # noqa: BLE001 - a file that can't be read is not added
        log.info("Could not read upload %s: %s", name, exc)
        shutil.rmtree(box, ignore_errors=True)
        raise ValueError(t("{name} couldn't be read. Is it damaged or password-protected?", name=name)) from None
    (box / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return meta


def _read_pages(path: Path, kind: str, name: str = "") -> list[str]:
    from .materials import extract_pages

    if kind in ("pdf", "doc"):
        return extract_pages(path)
    raw = path.read_bytes()
    text = ""
    for enc in (("utf-16",) if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else ()) + ("utf-8-sig", "cp1254"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", "replace")
    if "\x00" in text[:4000]:
        raise Refused(t("{name} doesn't look like a text file.", name=name or path.name))
    chunks, cur = [], ""
    lines = []
    for line in text.replace("\r\n", "\n").split("\n"):          # ~3000 characters per "page"
        while len(line) > 3000:                                   # a very long line: at a word's end
            cut = line.rfind(" ", 0, 3000)
            cut = cut if cut > 1500 else 3000
            lines.append(line[:cut])
            line = line[cut:].lstrip(" ")
        lines.append(line)
    for line in lines:
        if len(cur) + len(line) > 3000 and cur:
            chunks.append(cur.rstrip())
            cur = ""
        cur += line + "\n"
    return chunks + ([cur.rstrip()] if cur.strip() else [])


def _exif_turn(path: Path) -> int:
    """How a JPEG's EXIF says to turn it (1 = as is; 3, 6, 8: 180°, 90° clockwise, 90° counter-clockwise)."""
    try:
        with open(path, "rb") as f:
            head = f.read(128 * 1024)
        i = head.find(b"Exif\x00\x00")
        if head[:2] != b"\xff\xd8" or i < 0:
            return 1
        tiff = head[i + 6:]
        little = tiff[:2] == b"II"
        num = lambda b: int.from_bytes(b, "little" if little else "big")   # noqa: E731
        ifd = num(tiff[4:8])
        for k in range(num(tiff[ifd:ifd + 2])):
            e = tiff[ifd + 2 + 12 * k: ifd + 14 + 12 * k]
            if num(e[0:2]) == 0x0112:
                return num(e[8:10])
    except (OSError, IndexError, ValueError):
        pass
    return 1


def _to_jpeg(src: Path, out: Path, width: int = PICTURE_WIDTH) -> tuple[int, int]:
    """A photo or screenshot as a JPEG (turned upright, transparency on white); its size."""
    import av
    import numpy as np

    from .slides import save_jpeg

    with av.open(str(src)) as c:
        streams = list(c.streams.video)
        if not streams:
            raise ValueError("no picture")
        frame = next(c.decode(video=0))
    turn = _exif_turn(src)
    w, h = (frame.height, frame.width) if turn in (6, 8) else (frame.width, frame.height)     # as it is seen
    if w < 2 or h < 2:
        raise ValueError("too small")
    # made as small as it is kept first (a 50 MP phone photo would be gigabytes as numbers), at even sizes for the
    # JPEG encoder; transparency (a PNG, a GIF) on white, in whole numbers
    scale = min(1.0, width / w)
    alpha = frame.format.has_palette or any(comp.is_alpha for comp in frame.format.components)
    arr = frame.reformat(width=max(2, round(frame.width * scale) // 2 * 2), height=max(2, round(frame.height * scale) // 2 * 2),
                         format="rgba" if alpha else "rgb24").to_ndarray()
    if alpha:
        a = arr[:, :, 3:4].astype(np.uint16)
        rgb = ((arr[:, :, :3] * a + 255 * (255 - a) + 127) // 255).astype(np.uint8)
    else:
        rgb = arr
    if turn == 3:
        rgb = np.rot90(rgb, 2)
    elif turn == 6:
        rgb = np.rot90(rgb, -1)
    elif turn == 8:
        rgb = np.rot90(rgb, 1)
    rgb = np.ascontiguousarray(rgb)
    frame = av.VideoFrame.from_ndarray(rgb, format="rgb24")
    save_jpeg(frame, out, max_width=width)
    return int(w), int(h)


# ------------------------------------------------------------------ the uploads of a chat
def listing(target: str) -> list[dict[str, Any]]:
    """The uploads of ``target``, oldest first."""
    try:
        base = folder(target)
    except ValueError:
        return []
    out = []
    if base.is_dir():
        for box in base.iterdir():
            try:
                meta = json.loads((box / "meta.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            out.append(meta)
    out.sort(key=lambda m: (m.get("added") or 0, m.get("id") or ""))
    return out


def ids(target: str) -> list[str]:
    return [m["id"] for m in listing(target)]


def get(target: str, uid: str) -> tuple[dict[str, Any], Path]:
    if not _ID.match(uid or ""):
        raise ValueError(t("That file is no longer in this chat."))
    box = folder(target) / uid
    try:
        return json.loads((box / "meta.json").read_text(encoding="utf-8")), box
    except (OSError, ValueError):
        raise ValueError(t("That file is no longer in this chat.")) from None


def remove(target: str, uid: str) -> None:
    _, box = get(target, uid)
    _delete(box)                                 # with the pictures drawn from it and the AI's copies of them


def adopt(token: str, chat_id: str) -> None:
    """A new chat's first question: the files added before it become the chat's."""
    src = root() / draft_target(token) if draft_target(token) else None
    if src is None or not src.is_dir():
        return
    dst = folder(chat_id)
    if dst.exists():
        for box in src.iterdir():
            shutil.move(str(box), str(dst / box.name))
        shutil.rmtree(src, ignore_errors=True)
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)


def forget(chat_id: str) -> None:
    try:
        _delete(folder(chat_id))
    except ValueError:
        pass


def clean(known: Any, max_draft_age_s: float = 2 * 86400) -> None:
    """Uploads of chats that are gone, of drafts never asked, and pieces of files that never arrived."""
    base = root()
    if not base.is_dir():
        return
    now = time.time()
    for p in base.iterdir():
        try:
            if p.name == "incoming":
                for f in p.iterdir():
                    if now - f.stat().st_mtime > 3600:
                        f.unlink(missing_ok=True)
            elif p.name.startswith("draft-"):
                if now - p.stat().st_mtime > max_draft_age_s:
                    _delete(p)
            elif _TARGET.match(p.name) and not known(p.name):
                _delete(p)
        except OSError:
            log.debug("Could not clean %s", p, exc_info=True)


# ------------------------------------------------------------------ what goes to the AI
@dc.dataclass
class UploadPicture:
    """A photo the student added: its JPEG."""
    file: str

    def path(self) -> Optional[Path]:
        return Path(self.file) if os.path.isfile(self.file) else None


class UploadPage(PagePicture):
    """A page of a PDF the student added, drawn only when it is sent (kept with the file: they go together)."""

    def folder(self) -> Optional[Path]:
        return pages_folder(Path(self.pdf))


@dc.dataclass
class PptxPicture:
    """A PowerPoint slide that is mostly a figure (its text says little of it): the slide's largest picture, drawn
    only when it is sent (kept with a student's file, so they go together; else in the cache of page pictures).
    Without it such a slide would reach the AI as its title alone, or not at all."""
    pptx: str
    slide: int

    def folder(self) -> Path:
        from .paths import app_dir

        return pages_folder(Path(self.pptx)) or app_dir() / "cache" / "pages"

    def path(self) -> Optional[Path]:
        try:
            return slide_figure(Path(self.pptx), self.slide, self.folder())
        except Exception:  # noqa: BLE001 - a picture that can't be drawn: the slide goes as text only
            log.debug("Could not draw the picture of slide %d of %s", self.slide, self.pptx, exc_info=True)
            return None


def pptx_figures(path: Path) -> set[int]:
    """The slides of a PowerPoint file that hold a picture."""
    try:
        return {n for n, pics in _pptx_media(Path(path)).items() if pics}
    except (OSError, zipfile.BadZipFile, KeyError, ValueError):
        return set()


def slide_figure(path: Path, slide: int, cache: Path) -> Optional[Path]:
    """A PowerPoint slide's largest picture as a JPEG in ``cache`` (drawn once; None: the slide has none)."""
    import hashlib
    import tempfile

    key = hashlib.sha1(f"{os.path.abspath(path)}|{os.path.getmtime(path)}".encode("utf-8")).hexdigest()[:12]
    jpg = cache / f"pptx-{key}-{int(slide)}-figure.jpg"
    if jpg.is_file():
        return jpg
    names = _pptx_media(path).get(int(slide), [])
    if not names:
        return None
    cache.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path) as z, tempfile.TemporaryDirectory() as tmp:
        name = max(names, key=lambda n: z.getinfo(n).file_size)
        src = Path(tmp) / Path(name).name
        src.write_bytes(z.read(name))
        _to_jpeg(src, jpg, PICTURE_WIDTH)
    return jpg


def pages_folder(path: Path) -> Optional[Path]:
    """Where the pictures drawn from a student's file are kept: in the file's own folder, so that they go with it
    (None for any other file)."""
    try:
        Path(path).resolve().relative_to(root().resolve())
    except (ValueError, OSError):
        return None
    return Path(path).parent / "pages"


def _drop_copies(where: Path) -> None:
    """The AI's copies of the pictures under ``where`` (ai._small_jpeg keeps one in the cache, named as it names
    them), removed before the pictures themselves."""
    import hashlib

    from .paths import app_dir

    kept = app_dir() / "cache" / "slides"
    if not kept.is_dir() or not where.is_dir():
        return
    for f in where.rglob("*.jpg"):
        try:
            st = f.stat()
            key = hashlib.sha1(f"{os.path.abspath(f)}|{st.st_size}|{st.st_mtime_ns}|{SENT_WIDTH}".encode("utf-8"))
            (kept / f"{key.hexdigest()[:20]}.jpg").unlink(missing_ok=True)
        except OSError:
            log.debug("Could not remove the copy of %s", f, exc_info=True)


def _delete(where: Path) -> None:
    _drop_copies(where)
    shutil.rmtree(where, ignore_errors=True)


def is_upload(picture: Any) -> bool:
    return isinstance(picture, (UploadPicture, UploadPage)) or (
        isinstance(picture, PptxPicture) and pages_folder(Path(picture.pptx)) is not None)


def _pages(box: Path) -> list[str]:
    try:
        return json.loads((box / "text.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def picture_tokens(width: Any, height: Any) -> int:
    """What a picture of the student's costs as sent (at most 1024 px wide; Claude counts width × height / 750, its
    long side brought to 1568 px at most): a portrait page about 2,000 tokens, a 16:9 slide 800."""
    w, h = max(1.0, float(width or 0)), max(1.0, float(height or 0))
    if w > SENT_WIDTH:
        w, h = SENT_WIDTH, h * SENT_WIDTH / w
    s = min(1.0, 1568 / max(w, h))
    return max(1, round(w * s * h * s / 750))


def _pdf_ratio(path: Path, fallback: float = A4) -> float:
    """A PDF's height / width, from its first page (how much its page pictures cost)."""
    import pypdfium2 as pdfium

    from .materials import PDFIUM_LOCK

    try:
        with PDFIUM_LOCK:
            pdf = pdfium.PdfDocument(str(path))
            try:
                page = pdf[0]
                w, h = page.get_size()
                page.close()
            finally:
                pdf.close()
        return round(max(0.2, min(5.0, h / max(1.0, w))), 3)
    except Exception:  # noqa: BLE001 - only an estimate
        return fallback


def _passages(label: str, text: str) -> list[str]:
    """A page's text in pieces of at most about ``PASSAGE_CHARS``, each with the page's label (the offline model)."""
    out, cur = [], ""
    for line in text.split("\n"):
        if cur and len(cur) + len(line) > PASSAGE_CHARS:
            out.append(cur.strip())
            cur = ""
        cur += line[:PASSAGE_CHARS] + "\n"
    return [f"{label}\n{p}" for p in out + [cur.strip()] if p]


def notes_folder(box: Path) -> Path:
    """Where the notes read from an upload are kept ("Read everything", chatread.py): in its own folder."""
    return box / "notes"


def collect(target: str, can_images: bool, local: bool = False) -> dict[str, Any]:
    """Every page of the uploads of ``target`` (chatread.Page: photos, then the pages of each file with their text
    whole and, for a PDF, a picture), before any budget."""
    from .chatread import Page

    files: list[tuple[dict[str, Any], dict[str, Any], list[Any]]] = []
    for m in listing(target):
        box = folder(target) / m["id"]
        item = {k: m.get(k) for k in ("id", "name", "kind", "ext", "size", "pages", "width", "height")}
        item.update({"text_tokens": 0, "pictures": 0, "picture_tokens": 0, "cut": False, "unread": False, "native": False,
                     "blank": 0})
        original = str(box / f"original.{m['ext']}")
        pages: list[Any] = []
        if m["kind"] == "image":
            if can_images and not local:
                pic = box / "picture.jpg"
                weight = int((pic.stat().st_size if pic.is_file() else 150_000) * 0.7) * 4 // 3   # sent at 1024 px
                pages.append(Page(label=f"[{m['name']}]", group="upload", file=m["id"], name=m["name"], page=0,
                                  picture=UploadPicture(str(pic)), tokens=picture_tokens(m.get("width"), m.get("height")),
                                  bytes=weight, priority=0, source=original, notes_dir=str(notes_folder(box))))
        else:
            cost = picture_tokens(SENT_WIDTH, SENT_WIDTH * (m.get("ratio") or A4))
            # a PowerPoint slide that is mostly a figure: its picture (slide_figure)
            figures = pptx_figures(Path(original)) if m.get("ext") == "pptx" and can_images and not local else set()
            for i, text in enumerate(_pages(box), start=1):
                label = f"[{m['name']} p.{i}]" if m["kind"] in ("pdf", "doc") else f"[{m['name']}" + (f" part {i}]" if m.get("pages", 0) > 1 else "]")
                text = re.sub(r"\n{3,}", "\n\n", (text or "").strip())
                pic: Any = None
                if m["kind"] == "pdf" and can_images and not local:
                    pic = UploadPage(original, i)
                elif i in figures and len(text) < SHORT_TEXT:
                    pic = PptxPicture(original, i)
                tokens = (cost if isinstance(pic, UploadPage) else FIGURE_TOKENS) if pic is not None else 0
                pages.append(Page(label=label, group="upload", file=m["id"], name=m["name"], page=i, text=text,
                                  picture=pic, tokens=tokens, bytes=PAGE_BYTES * 4 // 3 if pic is not None else 0,
                                  priority=1 if len(text) < SHORT_TEXT else 2,
                                  source=original, notes_dir=str(notes_folder(box))))
        files.append((m, item, pages))
    return {"files": files, "pages": [p for _, _, ps in files for p in ps], "local": local, "can_images": can_images}


def assemble(col: dict[str, Any], chosen: Optional[list[Any]] = None, max_text_tokens: Optional[int] = None,
             native: Any = ()) -> dict[str, Any]:
    """What the uploads send: ``text`` (for the chat's instructions; the offline model gets ``passages`` of it that
    match each question instead: local_pick), ``images`` ([(label, picture)]) and ``items`` (each upload with what
    goes of it and its size). ``chosen``: the pages that go as pictures (default: photos, then PDF pages, mostly-figure
    pages first, ``MAX_PICTURES`` of them); ``native``: the uploads (ids) that go whole as PDF files."""
    from .chatslides import PdfFile, allocate

    per = ai.chars_per_token()
    budget = int((MAX_TEXT_TOKENS if max_text_tokens is None else max_text_tokens) * per)
    local = col["local"]
    candidates = [p for p in col["pages"] if p.picture is not None]
    if chosen is None:
        chosen = allocate(candidates, MAX_PICTURES)
    pictured = {p.key for p in chosen}
    native = set(native)
    texts: list[str] = []
    passages: list[str] = []
    cut_pages: list[Any] = []
    items: list[dict[str, Any]] = []
    images: list[tuple[str, Any]] = []
    used = 0
    for m, item, pages in col["files"]:
        item = dict(item)
        items.append(item)
        whole = m["id"] in native
        mine = [p for p in pages if p.key in pictured or (whole and p.picture is not None)]
        item["pictures"], item["native"] = len(mine), whole
        # pages with no text and no picture the app can send (a Word page that is only a figure): named, not hidden
        item["blank"] = sum(1 for p in pages if not p.text and p.picture is None) if m["kind"] == "doc" else 0
        item["picture_tokens"] = sum(p.tokens + (ai.text_tokens(p.text, per) if whole else 0) for p in mine)
        if whole:
            images.append((f"[{m['name']}] (the whole PDF, {len(pages)} pages)",
                           PdfFile(str(Path(pages[0].source)), len(pages), m["name"])))
        else:
            images += [(p.label, p.picture) for p in mine]
        if m["kind"] == "image":
            continue
        if not pages:
            continue
        if not any(p.text for p in pages) and not item["pictures"]:
            item["unread"] = True                # a scan with no picture sent: nothing of it would reach the AI
            cut_pages += pages                   # (read as pictures in batches, when that is on and the AI sees them)
            continue
        lines = []
        for p in pages:
            text = p.text if len(p.text) <= PAGE_CHARS else p.text[:PAGE_CHARS].rstrip() + " …"
            if text is not p.text:               # a page of an unusual length: its rest is read in batches, or cut
                cut_pages.append(p)
                item["cut"] = True
            lines.append(f"{p.label}\n{text}" if text else f"{p.label} (no text: see the picture)"
                         if p.key in pictured or whole else
                         f"{p.label} (no text: only a figure, which the app can't send)" if p.picture is None
                         and m["kind"] == "doc" else
                         f"{p.label} (no text could be read from this page, and its picture isn't sent)")
        section = f"## {m['name']}\n\n" + "\n\n".join(lines)
        room = budget - used
        if len(section) > room:                  # the files' text so far fills the budget: this one is cut
            item["cut"] = True
            part = section[:max(0, room)]
            nl = part.rfind("\n")                # at a line's end; in a long line, at a word's
            part = part[:nl] if nl > len(part) * 0.7 else part[:part.rfind(" ")] if " " in part else part
            section = part + "\n\n(The rest of this file was left out: too long.)" if room > 500 else ""
            known = {p.key for p in cut_pages}
            cut_pages += [p for p, line in zip(pages, lines) if line not in section and p.key not in known]
        if section:
            texts.append(section)
            used += len(section)
            if any(p.text for p in pages):       # only the page labels of a scan: the pictures are what goes
                item["text_tokens"] = ai.text_tokens(section, per)
            if local:
                passages += [x for p in pages if p.text and f"{p.label}\n{p.text}" in section
                             for x in _passages(p.label, p.text)]
    for it in items:
        it["tokens"] = it["text_tokens"] + it["picture_tokens"]
        it["sent"] = ("text+pictures" if it["text_tokens"] and it["pictures"] else "pictures" if it["pictures"]
                      else "text" if it["text_tokens"] else "none")
    text = "\n\n".join(texts)
    n_pictures = sum(it["pictures"] for it in items)
    return {"text": text, "images": images, "items": items,
            "passages": passages, "text_tokens": sum(it["text_tokens"] for it in items), "pictures": n_pictures,
            "picture_tokens": sum(it["picture_tokens"] for it in items),
            "pictures_left_out": max(0, len(candidates) - n_pictures), "local": local, "can_images": col["can_images"],
            "cut_pages": cut_pages}


def build(target: str, can_images: bool, local: bool = False) -> dict[str, Any]:
    """What the uploads of ``target`` send on their own (a chat shares the budget with the course's slides and the AI's
    limits: chat.ChatService.context)."""
    return assemble(collect(target, can_images, local))


def local_pick(passages: list[str], question: str, max_chars: int = LOCAL_CHARS) -> str:
    """The offline model's share of the uploads with one question: the passages that match it (and the follow-up it
    is part of) best, best first, in at most ``max_chars``; the files' beginning when nothing matches."""
    from .search import overlap

    if not passages:
        return ""
    scores = overlap(question, passages)
    ranked = sorted(range(len(passages)), key=lambda i: (-scores[i], i)) if max(scores) else range(len(passages))
    out, used = [], 0
    for i in ranked:
        if used + len(passages[i]) <= max_chars:
            out.append(passages[i])
            used += len(passages[i]) + 2
    return "=== STUDENT'S FILES (the passages that match the question) ===\n\n" + "\n\n".join(out) if out else ""


def prompt_note(up: dict[str, Any]) -> str:
    """The line the chat's instructions get when the student added files."""
    if not up or not up["items"] or not (up["text"] or up["pictures"]):
        return ""
    s = ("Under STUDENT'S FILES you get the text of files the student added to this chat themselves (their notes, "
         "homework, photos, other material), labelled [file p.N]: cite them that way.")
    if up["pictures"]:
        s += (" Their photos and pictures of their PDF pages are attached to the student's first message with the "
              "same labels; trust the pictures for formulas, figures and handwriting.")
    return s


def local_text(up: Optional[dict[str, Any]]) -> str:
    """The offline model's share of the uploads, sent with each question (it reads what fits)."""
    return f"=== STUDENT'S FILES ===\n\n{up['text']}" if up and up.get("text") else ""


# ------------------------------------------------------------------ viewers
def data_url(path: Path, mime: str = "image/jpeg") -> str:
    return f"data:{mime};base64," + base64.b64encode(Path(path).read_bytes()).decode()


def pdf_pages(path: Path) -> int:
    import pypdfium2 as pdfium

    from .materials import PDFIUM_LOCK

    with PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(str(path))
        try:
            return len(pdf)
        finally:
            pdf.close()


def pdf_page(path: Path, page: int, zoom: float = 1.0) -> str:
    """A PDF page as a picture for the viewer (drawn sharper when zoomed in)."""
    from .materials import page_image

    width = 2000 if zoom > 1.3 else 1280
    out = page_image(Path(path), int(page), width, folder=pages_folder(Path(path)))
    if out is None:
        raise ValueError(t("That page isn't in the file."))
    return data_url(out)


def pptx_slides(path: Path) -> list[dict[str, Any]]:
    """Each slide's text and how many pictures it holds (no PowerPoint needed)."""
    from .materials import extract_pages

    texts = extract_pages(path)
    media = _pptx_media(path)
    return [{"text": text, "pictures": len(media.get(i, []))} for i, text in enumerate(texts, start=1)]


def _pptx_media(path: Path) -> dict[int, list[str]]:
    """The pictures of each slide (zip names), in the slide's order."""
    out: dict[int, list[str]] = {}
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        for n in names:
            m = re.fullmatch(r"ppt/slides/_rels/slide(\d+)\.xml\.rels", n)
            if not m:
                continue
            rels = z.read(n).decode("utf-8", "replace")
            pics = []
            for target in re.findall(r'Target="([^"]+)"[^>]*', rels):
                if "/media/" not in f"/{target}" and not target.startswith("../media/"):
                    continue
                full = os.path.normpath(os.path.join("ppt/slides", target)).replace("\\", "/")
                if full in names and full.lower().rsplit(".", 1)[-1] in ("png", "jpg", "jpeg", "gif", "bmp", "tif", "tiff", "webp"):
                    pics.append(full)
            out[int(m.group(1))] = list(dict.fromkeys(pics))
    return out


def pptx_pictures(path: Path, slide: int, cache: Path) -> list[str]:
    """A slide's pictures as small JPEGs (data URLs), drawn once."""
    import hashlib
    import tempfile

    key = hashlib.sha1(f"{os.path.abspath(path)}|{os.path.getmtime(path)}".encode("utf-8")).hexdigest()[:12]
    out = []
    with zipfile.ZipFile(path) as z:
        for k, name in enumerate(_pptx_media(path).get(int(slide), [])[:8]):
            jpg = cache / f"pptx-{key}-{slide}-{k}.jpg"
            if not jpg.is_file():
                with tempfile.TemporaryDirectory() as tmp:
                    src = Path(tmp) / Path(name).name
                    src.write_bytes(z.read(name))
                    try:
                        _to_jpeg(src, jpg, 1000)
                    except Exception:  # noqa: BLE001 - a picture that can't be drawn is left out
                        log.debug("Could not draw %s of %s", name, path, exc_info=True)
                        continue
            out.append(data_url(jpg))
    return out


def text_pages(path: Path, kind: str) -> list[str]:
    return _read_pages(path, kind)
