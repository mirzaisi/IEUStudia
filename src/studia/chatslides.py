"""The course's slides from IEULearn that go with a chat: the files of the chat's lecture dates, of the course up to
them, all of them, or the ones the student picked, plus (optionally) the slides captured from the lecture video.

Each file is matched to its weeks through the course's weekly units (their dates, else a numbered title: "Hafta 6 ve
7", "Week 8-9"), a lecture date to its week through the term's first week. The chosen pages go to the AI as text
(in the chat's instructions) and, where the AI can look at pictures and the file is a PDF, as page images, within a
budget: at most ``MAX_IMAGES`` pictures per chat (provider limits and tokens), the text cut at ``MAX_TEXT_TOKENS``.
"""

from __future__ import annotations

import dataclasses as dc
import datetime as dt
import logging
import os
import re
from pathlib import Path
from typing import Any, Iterable, Optional

from . import ai

log = logging.getLogger("studia")

MODES = ("none", "lecture", "upto", "all", "pick")
SLIDE_KINDS = ("pdf", "pptx", "docx")
MAX_IMAGES = 60               # pictures per chat, the lecture video's slides included (Claude takes 100 per request)
MAX_TEXT_TOKENS = 60_000      # of course-file text (the lectures and the conversation need the rest of the window)
PAGE_CHARS = 2500             # a page's text beyond this is cut (a dense page; the picture has it all)
MATCH_KEEP = 0.75             # the week's files stay when they match what was said this well (the best file: 1)
SHORT_TEXT = 300              # a page with less text than this is mostly a figure, a formula or handwriting
IMAGE_BYTES = 95_000          # an average page picture as sent (1024 px JPEG), measured on the owner's course PDFs
VIDEO_IMAGE_BYTES = 70_000    # an average captured slide as sent

_WEEKS = re.compile(r"(?:week|hafta|konu|topic|unit|ünite|bölüm|chapter)[\s_.-]*0?(\d{1,2})"
                    r"(?:\s*(?:-|–|ve|and|&|,)\s*0?(\d{1,2}))?", re.I)


def normalize(send: Any) -> dict[str, Any]:
    """A chat's slide choice as saved: {"mode", "files" (picked ids, kept for "pick"), "images"}."""
    send = send if isinstance(send, dict) else {}
    mode = send.get("mode") if send.get("mode") in MODES else "none"
    files = [str(x) for x in send.get("files") or [] if str(x)][:200]
    return {"mode": mode, "files": files if mode == "pick" else [], "images": send.get("images") is not False}


def title_weeks(text: str) -> list[int]:
    """The weeks a title names: "Hafta 6 ve 7" → [6, 7], "MATH_250_Week 8-9" → [8, 9], "Week01_Student" → [1]."""
    out: list[int] = []
    for m in _WEEKS.finditer(text or ""):
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2) else a
        if not a <= b <= min(30, a + 3):         # not a range of weeks ("Week 3-19": week 3, something else)
            b = a
        if 1 <= a <= 30:
            out += range(a, b + 1)
    return sorted(set(out))


def first_week_day(lib: Any, course_id: str, units: list[dict[str, Any]]) -> Optional[dt.date]:
    """Monday-ish start of week 1: the earliest dated unit, else the term's (from the other courses), as the course
    page does."""
    from .materials import week_starts

    first = week_starts(units)
    if first is None:
        course = lib.get_course(course_id)
        start = lib.term_start(course.term) if course is not None and course.term else None
        if start:
            first = dt.date.fromisoformat(start)
            first -= dt.timedelta(days=first.weekday())
    return first


_UNIT_WEEKS = re.compile(r"\b(?:weeks?|hafta)\b", re.I)     # a unit title that names weeks, not a topic number


def file_weeks(m: dict[str, Any], unit: Optional[dict[str, Any]], first: Optional[dt.date]) -> list[int]:
    """A file's weeks: its unit's dates when the unit has them (they are what the lecturer planned); else the weeks
    its own name and title give, together with the weeks its unit's title names ("Hafta 6 ve 7" holding
    "Week 6 examples.pdf": weeks 6 and 7). A unit numbered by topic counts only when the file names no week
    (MATH 250's "Konu 10" holds "MATH_250_Week 11.pdf": week 11, not 10 and 11)."""
    from .materials import unit_week

    if unit and unit.get("start_date"):
        n = unit_week(unit, first)
        return [n] if n else []
    own = set(title_weeks(m.get("title") or "")) | set(title_weeks(m.get("file_name") or ""))
    title = unit["title"] if unit else ""
    if _UNIT_WEEKS.search(title or ""):
        return sorted(own | set(title_weeks(title)))
    return sorted(own) if own else title_weeks(title)


def label_of(m: dict[str, Any]) -> str:
    return m.get("file_name") or f"{m.get('title') or 'file'}.{m.get('ext') or ''}".rstrip(".")


def _day_date(key: str) -> Optional[dt.date]:
    try:
        return dt.date.fromisoformat(key.split("|")[1][:10])
    except (IndexError, ValueError):
        return None


def course_files(lib: Any, course_id: str, day_keys: Iterable[str], said: str = "") -> dict[str, Any]:
    """The course's slide files with their weeks, the weeks of the chat's lecture dates, and the files each choice
    ("lecture", "upto", "all") takes. ``said``: the lectures' transcripts, to check the week's files against what
    was taught (lecturers often run a week or two behind the plan)."""
    from .materials import week_of

    units = lib.units(course_id)
    by_id = {u["unit_id"]: u for u in units}
    first = first_week_day(lib, course_id, units)
    files = []
    for m in lib.materials(course_id):
        ext = (m.get("ext") or Path(m.get("file_name") or "").suffix.lstrip(".")).lower()
        if ext not in SLIDE_KINDS:
            continue
        unit = by_id.get(m.get("unit_id") or "")
        weeks = file_weeks(m, unit, first)
        local = m.get("local_path") or ""
        files.append({"id": m["resource_id"], "title": m.get("title") or label_of(m), "label": label_of(m), "ext": ext,
                      "weeks": weeks, "unit": unit["title"] if unit else "", "position": unit["position"] if unit else 999,
                      "pages": int(m.get("pages") or 0), "downloaded": bool(local and os.path.isfile(local)),
                      "path": local, "size": int(m.get("size") or 0)})
    files.sort(key=lambda f: (min(f["weeks"]) if f["weeks"] else 99, f["position"], f["title"].lower()))
    dates = sorted({d for d in (_day_date(k) for k in day_keys) if d})
    lecture_weeks = sorted({w for w in (week_of(d, first) for d in dates) if w})
    with_weeks = sorted({w for f in files for w in f["weeks"]})
    picked_weeks = [w for w in lecture_weeks if w in with_weeks]
    note = ""
    if lecture_weeks and not picked_weeks and with_weeks:
        # no file for that week (a week without slides, or the plan's numbering is off): the closest earlier week's
        # files, as lecturers often run behind the plan; else the closest later one
        last = max(lecture_weeks)
        earlier = [w for w in with_weeks if w <= last]
        near = max(earlier) if earlier else min(with_weeks)
        picked_weeks, note = [near], "closest"
    lecture = [f["id"] for f in files if set(f["weeks"]) & set(picked_weeks)]
    matched: list[str] = []
    if said and files:
        # the file whose text matches the lecture best (taught up to a week after the date's week): with the week's
        # files, or instead of them when they hardly match (the lecture was about an earlier week's slides)
        ranked = [(f, sc) for f, sc in match_files(lib, files, said)
                  if f["weeks"] and (not lecture_weeks or min(f["weeks"]) <= max(lecture_weeks) + 1)]
        if ranked and ranked[0][0]["id"] not in lecture:
            best_week = max((sc for f, sc in ranked if f["id"] in lecture), default=0.0)
            matched = [ranked[0][0]["id"]]
            lecture = matched if best_week < MATCH_KEEP else lecture + matched
            lecture = [f["id"] for f in files if f["id"] in lecture]          # in week order
    upto_week = max(lecture_weeks + picked_weeks) if (lecture_weeks or picked_weeks) else None
    upto = [f["id"] for f in files if f["weeks"] and upto_week is not None and min(f["weeks"]) <= upto_week]
    return {"files": files, "lecture_weeks": lecture_weeks, "file_weeks": picked_weeks, "note": note,
            "matched": matched, "modes": {"lecture": lecture, "upto": upto, "all": [f["id"] for f in files]}}


_WORD = re.compile(r"[a-zçğıöşü]{4,}")


def match_files(lib: Any, files: list[dict[str, Any]], said: str) -> list[tuple[dict[str, Any], float]]:
    """The files ranked by how well their text matches what the lecturer said (words that are rare among the
    course's files count most), each with its score relative to the best (1.0)."""
    import collections
    import math

    heard = collections.Counter(_WORD.findall(said.lower()))
    if not heard:
        return []
    vocab = {f["id"]: set(_WORD.findall(" ".join(_pages(lib, f)).lower())) for f in files}
    df = collections.Counter(w for v in vocab.values() for w in v)
    n = len(files)
    scored = []
    for f in files:
        v = vocab[f["id"]]
        if v:
            s = sum(math.log((n + 1) / df[w]) * min(c, 5) for w, c in heard.items() if w in v) / math.sqrt(len(v))
            scored.append((f, s))
    scored.sort(key=lambda x: -x[1])
    top = scored[0][1] if scored and scored[0][1] > 0 else 0
    return [(f, s / top) for f, s in scored] if top else []


def chosen_ids(catalog: dict[str, Any], send: dict[str, Any]) -> list[str]:
    mode = send["mode"]
    if mode == "pick":
        known = {f["id"] for f in catalog["files"]}
        return [x for x in send["files"] if x in known]
    return list(catalog["modes"].get(mode) or [])


@dc.dataclass
class PagePicture:
    """A PDF page to send as a picture, rendered only when it is sent (the chat's view only counts them)."""
    pdf: str
    page: int

    def folder(self) -> Optional[Path]:
        """Where its picture is kept (None: the shared cache of page pictures)."""
        return None

    def path(self) -> Optional[Path]:
        from .materials import page_image

        try:
            return page_image(Path(self.pdf), self.page, folder=self.folder())
        except Exception:  # noqa: BLE001 - a page that can't be drawn goes as text only
            log.debug("Could not draw page %d of %s", self.page, self.pdf, exc_info=True)
            return None


def _pages(lib: Any, f: dict[str, Any]) -> list[str]:
    pages = lib.material_pages(f["id"])
    if not any(p.strip() for p in pages) and f["downloaded"]:
        from .materials import extract_pages

        try:
            pages = extract_pages(Path(f["path"]))
        except Exception:  # noqa: BLE001 - a broken file is left out
            log.debug("Could not read %s", f["path"], exc_info=True)
            pages = []
    return pages


def build(lib: Any, course_id: str, day_keys: Iterable[str], send: Any, captured: int = 0,
          can_images: Optional[bool] = None, said: str = "") -> dict[str, Any]:
    """What the choice sends: ``text`` (for the chat's instructions), ``images`` ([(label, PagePicture)], in file and
    page order) and ``summary`` (for the page: the files, the counts, the size). ``captured``: how many slides from
    the lecture video go too (they share the picture budget, the course's pages first)."""
    send = normalize(send)
    local = ai.provider() == "local"        # its window holds a few thousand tokens: it gets matching passages instead
    can_images = ai.provider() in ("claude", "chatgpt", "codex") if can_images is None else can_images
    catalog = course_files(lib, course_id, day_keys, said if send["mode"] == "lecture" else "")
    ids = chosen_ids(catalog, send) if send["mode"] != "none" and not local else []
    picked = [f for f in catalog["files"] if f["id"] in set(ids)]
    per = ai.chars_per_token()
    texts: list[str] = []
    pictures: list[tuple[int, str, PagePicture]] = []        # (priority, label, picture)
    files_out, n_pages, missing = [], 0, 0
    for f in picked:
        if not f["downloaded"]:
            missing += 1
            continue
        pages = _pages(lib, f)
        n_pages += len(pages)
        files_out.append({"id": f["id"], "title": f["title"], "label": f["label"], "ext": f["ext"], "pages": len(pages),
                          "path": f["path"]})
        body = []
        for i, text in enumerate(pages, start=1):
            label = f"[{f['label']} p.{i}]"
            text = re.sub(r"\n{3,}", "\n\n", text.strip())
            if len(text) > PAGE_CHARS:
                text = text[:PAGE_CHARS].rstrip() + " …"
            body.append(f"{label}\n{text}" if text else f"{label} (no text: see the picture)" if f["ext"] == "pdf"
                        else f"{label} (no text)")
            if f["ext"] == "pdf" and can_images and send["images"]:
                pictures.append((0 if len(text) < SHORT_TEXT else 1, label, PagePicture(f["path"], i)))
        texts.append(f"## {f['label']}" + (f" (week {', '.join(map(str, f['weeks']))})" if f["weeks"] else "")
                     + "\n\n" + "\n\n".join(body))
    text = "\n\n".join(texts)
    cut = False
    if len(text) > MAX_TEXT_TOKENS * per:
        text = text[:int(MAX_TEXT_TOKENS * per)].rsplit("\n[", 1)[0] + "\n\n(The rest of the files was left out: too long.)"
        cut = True
    # the picture budget: the course's pages first (those that are mostly figures, formulas or handwriting before
    # those whose text says it all), the video's slides get what is left, at least a third when both go
    keep_video = min(captured, MAX_IMAGES // 3) if pictures else min(captured, MAX_IMAGES)
    room = MAX_IMAGES - keep_video
    chosen_pics = sorted(pictures, key=lambda p: p[0])[:room]
    order = {id(p[2]): n for n, p in enumerate(pictures)}
    chosen_pics.sort(key=lambda p: order[id(p[2])])
    video = min(captured, MAX_IMAGES - len(chosen_pics))
    images = [(label, pic) for _, label, pic in chosen_pics]
    text_tokens = ai.text_tokens(text, per) if text else 0
    left = len(text)
    for fo, section in zip(files_out, texts):    # each file's share ("What this chat sees"), in the text as sent
        path = fo.pop("path")
        fo["text_tokens"] = ai.text_tokens(section[:max(0, left)], per)
        left -= len(section) + 2
        fo["pictures"] = sum(1 for _, pic in images if pic.pdf == path)
        fo["tokens"] = fo["text_tokens"] + fo["pictures"] * ai.IMAGE_TOKENS
    summary = {
        "mode": send["mode"], "images_on": send["images"], "can_images": bool(can_images),
        "files": files_out, "missing": missing, "pages": n_pages, "page_images": len(images),
        "text_only_pages": n_pages - len(images), "video_images": video, "video_total": captured,
        "text_tokens": text_tokens, "image_tokens": (len(images) + video) * ai.IMAGE_TOKENS,
        "tokens": text_tokens + (len(images) + video) * ai.IMAGE_TOKENS,
        "bytes": len(text.encode("utf-8")) + len(images) * IMAGE_BYTES + video * VIDEO_IMAGE_BYTES,
        "cut": cut, "max_images": MAX_IMAGES, "local": local,
    }
    return {"text": text, "images": images, "video_images": video, "summary": summary, "catalog": catalog}


def options(lib: Any, course_id: str, day_keys: Iterable[str], send: Any, captured: int = 0,
            said: str = "") -> dict[str, Any]:
    """For the chooser: every slide file of the course (week, pages, on this PC or not), the files each choice
    takes, the lecture's weeks, and what the current choice sends."""
    keys = list(day_keys)
    b = build(lib, course_id, keys, send, captured, said=said)
    cat = b["catalog"] if normalize(send)["mode"] == "lecture" else course_files(lib, course_id, keys, said)
    files = [{k: f[k] for k in ("id", "title", "label", "ext", "weeks", "unit", "pages", "downloaded", "size")}
             for f in cat["files"]]
    return {"files": files, "modes": cat["modes"], "lecture_weeks": cat["lecture_weeks"],
            "file_weeks": cat["file_weeks"], "closest": cat["note"] == "closest", "matched": cat["matched"], "send": normalize(send),
            "summary": b["summary"], "provider": ai.provider()}


def prompt_note(summary: dict[str, Any]) -> str:
    """The line the chat's instructions get when course slides go with it."""
    if not summary.get("files"):
        return ""
    s = ("Under COURSE SLIDES you get the text of the course's slide files from IEULearn that the student chose, page "
         "by page, labelled [file p.N]: cite them that way.")
    if summary.get("page_images"):
        s += " Pictures of some of these pages are attached to the student's first message with the same labels; " \
             "the text was extracted automatically, so trust the pictures for formulas, figures and handwriting."
    return s
