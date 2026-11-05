"""Course materials (slides, PDFs, PowerPoints, Word files) from IEULearn: downloaded on demand into the
course folder, their text extracted (per page / slide) for search and AI, page images rendered for Claude.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import re
import threading
import zipfile
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urljoin

import requests

from .cancel import check
from .i18n import t
from .outputs.writer import course_dir, safe_name
from .paths import app_dir

log = logging.getLogger("studia")
TEXT_KINDS = {"pdf", "pptx", "docx", "txt", "md"}
# PDFium (pypdfium2) is not thread-safe, and the viewer, a chat, an upload and a study pack can each be in it at once:
# every use of it holds this lock (a crash in it would close the whole app)
PDFIUM_LOCK = threading.RLock()


# ------------------------------------------------------------------ weeks
def week_starts(units: list[dict[str, Any]]) -> Optional[dt.date]:
    """The first day of week 1: the earliest dated unit."""
    starts = [dt.date.fromisoformat(u["start_date"]) for u in units if u.get("start_date")]
    return min(starts) if starts else None


def week_of(day: dt.date, first: Optional[dt.date]) -> Optional[int]:
    if first is None:
        return None
    n = (day - first).days // 7 + 1
    return n if 1 <= n <= 30 else None


_NUMBERED = re.compile(r"^\s*(?:hafta|week|konu|topic|unit|ünite|bölüm|chapter)\s*(\d{1,2})\b", re.I)


def unit_week(unit: dict[str, Any], first: Optional[dt.date]) -> Optional[int]:
    """A unit's week: from its dates (their middle, so a plan whose week starts a day early still lands right),
    else from a numbered title ("Konu 5", "Hafta 5", "Week 5")."""
    if unit.get("start_date"):
        start = dt.date.fromisoformat(unit["start_date"])
        end = dt.date.fromisoformat(unit["end_date"]) if unit.get("end_date") else start
        return week_of(start + (end - start) / 2 if end > start else start, first)
    m = _NUMBERED.match(unit.get("title") or "")
    return int(m.group(1)) if m and 1 <= int(m.group(1)) <= 30 else None


# ------------------------------------------------------------------ text extraction
def _xml_text(xml: str, para_tag: str, text_tag: str) -> str:
    paras = []
    for p in re.findall(rf"<{para_tag}\b[^>]*>(.*?)</{para_tag}>", xml, re.S):
        text = "".join(re.findall(rf"<{text_tag}\b[^>]*>(.*?)</{text_tag}>", p, re.S))
        if text.strip():
            paras.append(text)
    import html

    return html.unescape("\n".join(paras))


def extract_pages(path: Path) -> list[str]:
    """Text of each page (PDF), slide (PPTX) or ~page (DOCX/TXT). Empty strings for pages without text."""
    ext = path.suffix.lower().lstrip(".")
    if ext == "pdf":
        import pypdfium2 as pdfium

        with PDFIUM_LOCK:
            pdf = pdfium.PdfDocument(str(path))
            try:
                out = []
                for i in range(len(pdf)):
                    page = pdf[i]
                    tp = page.get_textpage()
                    out.append(re.sub(r"[ \t]+\n", "\n", tp.get_text_range().replace("\r\n", "\n")).strip())
                    tp.close()
                    page.close()
                return out
            finally:
                pdf.close()
    if ext == "pptx":
        with zipfile.ZipFile(path) as z:
            names = sorted((n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                           key=lambda n: int(re.search(r"(\d+)", n.rsplit("/", 1)[1]).group(1)))
            out = []
            for n in names:
                text = _xml_text(z.read(n).decode("utf-8", "replace"), "a:p", "a:t")
                notes = n.replace("slides/slide", "notesSlides/notesSlide")
                if notes in z.namelist():
                    extra = _xml_text(z.read(notes).decode("utf-8", "replace"), "a:p", "a:t")
                    if extra.strip():
                        text += "\n[Speaker notes] " + extra
                out.append(text.strip())
            return out
    if ext in ("docx", "txt", "md"):
        if ext == "docx":
            with zipfile.ZipFile(path) as z:
                text = _xml_text(z.read("word/document.xml").decode("utf-8", "replace"), "w:p", "w:t")
        else:
            text = path.read_text(encoding="utf-8", errors="replace")
        chunks, cur = [], ""
        for para in text.split("\n"):                       # ~3000 characters per "page"
            if len(cur) + len(para) > 3000 and cur:
                chunks.append(cur.strip())
                cur = ""
            cur += para + "\n"
        return chunks + ([cur.strip()] if cur.strip() else [])
    return []


PAGE_COPIES = 1500              # page pictures kept (about 150 KB each); beyond that the oldest go


def page_image(pdf_path: Path, page_no: int, width: int = 1280, folder: Optional[Path] = None) -> Optional[Path]:
    """A JPEG of one PDF page (cached under a name that stays the same between runs), for Claude to look at
    diagrams, tables and handwriting. ``folder``: kept there instead of the shared cache (a student's own PDF: its
    pages go when it is removed)."""
    import hashlib

    cache = folder or app_dir() / "cache" / "pages"
    cache.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(str(pdf_path).encode("utf-8")).hexdigest()[:10]
    out = cache / f"{safe_name(pdf_path.stem, 60)}-{key}-{page_no}-{width}.jpg"
    if out.exists() and out.stat().st_mtime >= pdf_path.stat().st_mtime:
        return out
    if folder is None and sum(1 for _ in os.scandir(cache)) > PAGE_COPIES + 100:   # now and then, not for every page
        from .ai import _prune_copies

        _prune_copies(cache, PAGE_COPIES)
    import av
    import pypdfium2 as pdfium

    from .slides import save_jpeg

    with PDFIUM_LOCK:
        if out.exists() and out.stat().st_mtime >= pdf_path.stat().st_mtime:
            return out                                  # drawn by another caller while this one waited: not replaced under a reader
        pdf = pdfium.PdfDocument(str(pdf_path))
        try:
            if not 1 <= page_no <= len(pdf):
                return None
            page = pdf[page_no - 1]
            scale = width / max(1.0, page.get_width())
            arr = page.render(scale=scale, rev_byteorder=True).to_numpy()
            page.close()
        finally:
            pdf.close()
        frame = av.VideoFrame.from_ndarray(arr[:, :, :3].copy(), format="rgb24")
        save_jpeg(frame, out, max_width=width)          # the same page drawn twice at once never shares its file
    return out


# ------------------------------------------------------------------ downloads
class MaterialStore:
    def __init__(self, services: Any) -> None:
        self._s = services
        self._lock = threading.Lock()
        self._busy: set[str] = set()

    @property
    def lib(self) -> Any:
        return self._s.lib

    def folder_for(self, material: dict[str, Any]) -> Path:
        course = self.lib.get_course(material["course_id"])
        units = self.lib.units(material["course_id"])
        unit = next((u for u in units if u["unit_id"] == material["unit_id"]), None)
        first = week_starts(units)
        week = unit_week(unit, first) if unit else None
        label = (f"Week {week:02d} - {unit['title']}" if week else unit["title"]) if unit else "Other"
        return course_dir(self._s.output_root, course, self.lib.courses(True)) / "Materials" / safe_name(label, 90)

    def target_path(self, material: dict[str, Any]) -> Path:
        """Where a course file is saved: its own name (a long one is shortened before the extension, which Windows
        needs to open it), and the IEULearn id added when another file of the course already has that name."""
        name = material.get("file_name") or f"{material['title']}.{material.get('ext') or 'bin'}"
        if material.get("ext") and not name.lower().endswith("." + material["ext"]):
            name += "." + material["ext"]
        stem, ext = os.path.splitext(name)
        ext = re.sub(r'[<>:"/\\|?*\x00-\x1f\s]', "", ext)[:12]
        path = self.folder_for(material) / (safe_name(stem, 120 - len(ext)) + ext)
        others = self.lib.materials(material.get("course_id"), include_gone=True)
        taken = {os.path.normcase(m["local_path"]) for m in others
                 if m.get("local_path") and m.get("resource_id") != material.get("resource_id")}
        if os.path.normcase(str(path)) in taken:
            path = path.with_name(f"{safe_name(stem, 100 - len(ext))} ({material.get('resource_id')}){ext}")
        return path

    def download(self, resource_id: str, session: requests.Session,
                 progress: Callable[[int, int], None] = lambda a, b: None,
                 cancel: Optional[threading.Event] = None) -> Path:
        m = self.lib.get_material(resource_id)
        if m is None:
            raise RuntimeError(t("This material is no longer listed on IEULearn."))
        with self._lock:
            if resource_id in self._busy:
                raise RuntimeError(t("Already downloading."))
            self._busy.add(resource_id)
        part: Optional[Path] = None
        try:
            dest = self.target_path(m)
            dest.parent.mkdir(parents=True, exist_ok=True)
            part = dest.with_name(dest.name + ".part")
            url = m.get("download_url") or m.get("url")
            with session.get(urljoin("https://learn.ieu.edu.tr/", url), stream=True, timeout=60) as r:
                if "text/html" in r.headers.get("content-type", ""):
                    if "login" in r.url:
                        from .ieulearn import NeedLogin

                        raise NeedLogin(t("Your IEULearn session has expired."))
                    raise RuntimeError(t("IEULearn sent a web page instead of the file. Try again later."))
                r.raise_for_status()
                total = int(r.headers.get("content-length") or 0)
                done = 0
                with open(part, "wb") as f:
                    for chunk in r.iter_content(256 * 1024):
                        check(cancel)
                        f.write(chunk)
                        done += len(chunk)
                        progress(done, total)
            os.replace(part, dest)
            self.lib.set_material_file(resource_id, str(dest), dest.stat().st_size, m.get("url") or "")
            log.info("Downloaded %s (%d KB)", dest.name, dest.stat().st_size // 1024)
            return dest
        finally:
            with self._lock:
                self._busy.discard(resource_id)
            if part is not None and part.exists():             # an interrupted download leaves nothing behind
                try:
                    part.unlink()
                except OSError:
                    pass

    def index_text(self, resource_id: str) -> int:
        """Extract and index the text of a downloaded material; returns the number of pages."""
        m = self.lib.get_material(resource_id)
        if not m or not m.get("local_path") or not os.path.isfile(m["local_path"]):
            return 0
        path = Path(m["local_path"])
        if path.suffix.lower().lstrip(".") not in TEXT_KINDS:
            self.lib.set_material_text(resource_id, [], "unsupported")
            return 0
        try:
            pages = extract_pages(path)
        except Exception as exc:  # noqa: BLE001 - a broken file must not stop the rest
            log.warning("Text extraction failed for %s: %s", path.name, exc)
            self.lib.set_material_text(resource_id, [], "failed")
            return 0
        self.lib.set_material_text(resource_id, pages, "done" if any(p.strip() for p in pages) else "no-text")
        return len(pages)
