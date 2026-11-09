"""Study packs: a complete teaching document the AI writes from the files and lectures the student picks - every
concept of that material in teaching order, explained step by step, with worked examples, formulas (KaTeX),
diagrams the AI draws as SVG (sanitised: shapes and text only), the course's own slide pages (rendered from the
IEULearn PDFs) and slides captured from the lecture videos. Shown in the app and saved as HTML and as a PDF (the same
HTML, printed by Edge headless - or Qt WebEngine on Linux without Chrome) with a table of contents and page numbers.

Stages (each one's output is saved with the plan, so an interrupted pack resumes where it stopped):
  gather   - the picked files (page by page) and lectures (their transcripts), no AI
  outline  - the chapters in teaching order, the concepts each one teaches, the pages and lecture passages it is
             taught from and the figures it needs (AI, JSON)
  sections - each chapter written in full from its own material (AI, two at a time; each saved when it is done)
  figures  - the diagrams of each chapter drawn as SVG (AI); slide pages and lecture frames need no AI
  assemble - the document (HTML) and its PDF, in <course>/Study packs

A pack is a plan of kind "pack" (plans.PlanRunner runs it), so the plan list, events, stop and resume are shared.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import quote

from . import aiwait, store
from .cancel import Cancelled
from .i18n import N_, t, tn
from .notices import ALERTS_MD, KINDS
from .outputs.prompts import STYLE, written_in
from .stressed import chapter_block, for_pack, pack_block      # what the lecturer stressed: the priorities

log = logging.getLogger("studia")

STAGES = [("gather", N_("Reading the course material")), ("outline", N_("Planning the chapters")),
          ("sections", N_("Writing the chapters")), ("figures", N_("Drawing the figures")),
          ("assemble", N_("Putting your study pack together"))]
LABELS = dict(STAGES)
OUTLINE_CHARS = 150_000       # material the outline (a cheatsheet's topic list) sees (~43k tokens), and more when
FILES_CHARS = 300_000         # the files need it: up to this (every page of about eight weeks of slides), with
LECTURES_MIN = 60_000         # at least this of the lectures next to them (their passages are read whole later)
FILE_CHARS = 32_000           # of one file in a chapter's material
SECTION_CHARS = 36_000        # material one chapter is written from
MAX_CHAPTERS = 12
MAX_FIGURES = 3               # per chapter
SECTION_PARALLEL = 2          # chapters written at once
FIGURE_WIDTH = 1400           # slide pages rendered from the PDFs (px)
CHAPTER_PICTURES = 6          # pages with a figure a chapter's writer sees (about 800 tokens each)
PICTURE_WIDTH = 1024          # (px)

SYSTEM = ("You are an experienced university lecturer writing a study pack: a complete teaching document built only on "
          "the course material you are given (where the slides are terse, explain with standard textbook knowledge in "
          "the course's notation and scope, never against it). Maths in LaTeX: $...$ in a sentence, $$...$$ on lines "
          "of their own. You deliver what is asked without asking questions.\n" + STYLE + "\n\n" + ALERTS_MD)


def _level(scope: Any, settings: Any) -> str:
    """The reasoning level for the pack's (or cheatsheet's) requests: the one the student chose in its dialog, else
    the plans level of Settings → AI (plans.levels, effort.py)."""
    from .plans import levels

    return levels(scope, settings)[0]


# ------------------------------------------------------------------ gather (no AI)
def _lecture(lib: Any, course_id: str, day_key: str) -> tuple[Any, list, dict, dict]:
    """A class day's course, recordings, transcripts and captured slides."""
    course = lib.get_course(course_id)
    recs = [r for r in lib.recordings(course_id) if r.day_key == day_key]
    done = lib.transcribed_ids()
    trans = store.load_transcripts([r.delivery_id for r in recs if r.delivery_id in done])
    slides = {r.delivery_id: store.load_slides(r.delivery_id) for r in recs}
    return course, recs, trans, slides


def lecture_text(lib: Any, course_id: str, day_key: str) -> str:
    from .outputs.ai_copy import compact_day

    course, recs, trans, slides = _lecture(lib, course_id, day_key)
    return compact_day(course, recs, trans, slides) if recs and trans else ""


def _file_text(lib: Any, f: dict[str, Any], pages: Optional[tuple[int, int]] = None, limit: int = FILE_CHARS) -> str:
    """A file as "[F1 p.3] …" lines (``pages``: only those), at most ``limit`` characters."""
    out, size = [], 0
    for n, page in enumerate(lib.material_pages(f["id"]), 1):
        if pages and not pages[0] <= n <= pages[1]:
            continue
        page = " ".join(page.split())
        if not page:
            continue
        piece = f"[{f['ref']} p.{n}] {page}"
        if size + len(piece) > limit:
            if not out and limit >= 600:                 # its first page alone is longer: a part of it
                out.append(piece[:limit - 40].rsplit(" ", 1)[0] + " …(cut)")
            out.append("…(rest of the file left out)")
            break
        out.append(piece)
        size += len(piece) + 1
    return "\n".join(out)


def _outline_room(scope: Any, settings: Any) -> int:
    """The most material the outline (a cheatsheet's topic list) may carry, in characters: FILES_CHARS and
    LECTURES_MIN, less when the model's window is smaller (it also holds the answer and the reasoning before it)."""
    from . import appconfig, chatlimits
    from .ai import CHARS_PER_TOKEN, CHARS_PER_TOKEN_BY
    from .plans import main_model

    p = appconfig.load().ai_provider
    p = p if p in ("chatgpt", "codex") else "claude"        # (packs need one of these)
    window = chatlimits.of(p, main_model(scope, settings)).window
    return min(FILES_CHARS + LECTURES_MIN, int(window * 0.55 * CHARS_PER_TOKEN_BY.get(p, CHARS_PER_TOKEN)))


def _file_head(f: dict[str, Any], pictures: bool = True) -> str:
    """A file's line in the material; ``pictures``: with the pages that have a figure (for the outline, which picks
    them; a chapter's writer read it as pages it was not given)."""
    from .outputs.figures import pages_text

    pics = f"; pages with a figure: {pages_text(f['pictures'])}" if pictures and f.get("pictures") else ""
    return (f"\n=== FILE {f['ref']} (week {f['week']}): {f['name']}, {f['pages']} pages"
            f"{', PDF' if f['pdf'] else ''}{pics} ===\n")


def picture_pages(f: dict[str, Any]) -> Optional[list[int]]:
    """The pages of a gathered PDF that have a picture worth showing (a circuit, a graph, a diagram: outputs/figures.py),
    [] for another kind of file; None when its pages can't be looked at (not there, not readable)."""
    from .outputs.figures import page_pictures

    if not f.get("pdf"):
        return []
    found = page_pictures(Path(f["path"])) if f.get("path") else None
    return sorted(found) if found is not None else None


def _lecture_head(x: dict[str, Any]) -> str:
    shots = ", ".join(f"part {p['part']}: slides 1–{p['slides']}" for p in x["parts"] if p["slides"])
    return (f"\n=== LECTURE {x['ref']} (week {x['week']}, {x['date']})"
            f"{'; captured slides ' + shots if shots else ''} ===\n")


def gather(lib: Any, settings: Any, course_id: str, scope: Any) -> dict[str, Any]:
    """The picked files and lectures (``scope.files`` / ``scope.days``; empty: all of the weeks, unless the chooser's
    ticks (``scope.picked``) left them empty), as one text for the outline, with references (F1…, L1…) the later
    stages use."""
    from .outputs.markdown import part_views
    from .plans import _excerpt, _fit_days, _shares, _week_span
    from .views import course_detail

    d = course_detail(lib, settings, course_id)
    if d is None:
        raise KeyError(t("This course is no longer in your list."))
    weeks = [w for w in d["weeks"] if not scope.weeks or w["no"] in scope.weeks]
    if not scope.weeks:
        weeks = [w for w in weeks if w["past"] or w["current"]] or d["weeks"]
    pick_f, pick_d = set(scope.files or []), set(scope.days or [])
    only_f, only_d = bool(pick_f or getattr(scope, "picked", False)), bool(pick_d or getattr(scope, "picked", False))
    head = [f"COURSE: {d['code']}, {d['syllabus'].get('name') or d['display_name']}"]
    if d["outcomes_en"]:
        head.append("LEARNING OUTCOMES:")
        head += [f"  LO{i}: {lo}" for i, lo in enumerate(d["outcomes_en"], 1)]
    head.append("WEEKS:")
    for w in weeks:
        topic = " / ".join(x for x in (w["topic_en"], w["topic_tr"]) if x) or ", ".join(u["title"] for u in w["units"])
        head.append(f"  Week {w['no']}: {topic}")
    files, lectures, missing, days_missing = [], [], [], []
    seen: set[str] = set()                  # a unit for two weeks ("Hafta 6 ve 7") is in both: its files once
    for w in weeks:
        for u in w["units"]:
            for m in u["materials"]:
                if (only_f and m["id"] not in pick_f) or m["id"] in seen:
                    continue
                seen.add(m["id"])
                if not m["downloaded"] or m["text"] != "done":
                    missing.append({"id": m["id"], "title": m["title"], "week": w["no"]})
                    continue
                row = lib.get_material(m["id"]) or {}
                path = row.get("local_path") or ""
                n = len(lib.material_pages(m["id"]))
                if n:
                    files.append({"ref": f"F{len(files) + 1}", "id": m["id"], "name": m["file_name"] or m["title"],
                                  "week": w["no"], "pages": n, "path": path,
                                  "pdf": (m.get("ext") or Path(path).suffix.lstrip(".")).lower() == "pdf"})
                    pics = picture_pages(files[-1])
                    if pics is not None:                 # (else unknown: any of its pages may be a figure)
                        files[-1]["pictures"] = pics
        for day in reversed(w["days"]):          # in the order they were taught (the course page: newest first)
            if (only_d and day["day_key"] not in pick_d) or (not only_d and day.get("mine") is False):
                continue
            if not day["any_done"]:
                days_missing.append({"day_key": day["day_key"], "date": day["date"], "week": w["no"]})
                continue
            course, recs, trans, slides = _lecture(lib, course_id, day["day_key"])
            parts = [{"part": p.index, "delivery_id": p.rec.delivery_id, "slides": len(p.slides)}
                     for p in part_views(recs, trans, slides) if p.transcript]
            if parts:
                lectures.append({"ref": f"L{len(lectures) + 1}", "day_key": day["day_key"], "date": day["date"],
                                 "week": w["no"], "parts": parts})
    # every page of the files first, as far as the room goes (the plan names the pages each chapter or topic is taught
    # from, and what it never saw is in none); the lectures share the rest, at least LECTURES_MIN (their excerpts:
    # the chapters and entries read the passages the plan names in full)
    out, cut, files_cut = list(head), [], []
    whole = [_file_text(lib, f, limit=10 ** 9) for f in files]
    heads = [len(_file_head(f)) + 1 for f in files]
    need = sum(len(x) + 1 for x in out) + sum(heads) + sum(len(x) for x in whole)
    total = min(_outline_room(scope, settings), max(OUTLINE_CHARS, need + (LECTURES_MIN if lectures else 0)))
    least = min(LECTURES_MIN, int(total * 0.4)) if lectures else 0
    budget = total - least - sum(len(x) + 1 for x in out) - sum(heads)
    for f, text, room in zip(files, whole, _shares(budget, [len(x) for x in whole], cap=max(0, budget))):
        if room < len(text):
            text = _file_text(lib, f, limit=room)
            cut.append(f["ref"])
            got = re.findall(rf"^\[{f['ref']} p\.(\d+)\]", text, re.M)       # the last page it reached
            files_cut.append({"ref": f["ref"], "name": f["name"], "seen": int(got[-1]) if got else 0,
                              "pages": f["pages"]})
        out.append(_file_head(f) + text)
    texts = [(x, _lecture_head(x), lecture_text(lib, course_id, x["day_key"])) for x in lectures]
    full_chars = sum(len(p) for f in files for p in lib.material_pages(f["id"])) + sum(len(x) for _, _, x in texts)
    budget = total - sum(len(x) + 1 for x in out)
    keep, rooms = _fit_days([len(full) for _, _, full in texts], [len(h) + 1 for _, h, _ in texts], budget,
                            cap=max(0, budget))         # a lecture that fits goes whole (not a video plan's 12k)
    for i in keep:
        x, h, full = texts[i]
        out.append(h + _excerpt(full, rooms[i]))
        if rooms[i] < len(full):
            cut.append(x["ref"])
    pack = "\n".join(out)
    from .quizzes import course_language

    return {"pack": pack, "chars": len(pack), "full_chars": full_chars, "files": files, "lectures": lectures, "cut": cut,
            "language": course_language(d, {"pack": pack}),         # the language the documents are written in
            "files_cut": files_cut,
            "left_out": [texts[i][0]["ref"] for i in range(len(texts)) if i not in rooms],
            "materials_missing": missing, "days_missing": days_missing,
            "course": {"id": course_id, "code": d["code"], "name": d["display_name"]},
            "weeks": [{"no": w["no"], "topic": w["topic_en"] or w["topic_tr"]} for w in weeks],
            "span": _week_span([w["no"] for w in weeks]),
            "stressed": for_pack(lib, course_id, d["weeks"], [w["no"] for w in weeks], lectures)}   # (priorities)


# ------------------------------------------------------------------ outline (AI)
def outline_prompt(scope: Any, facts: dict[str, Any]) -> str:
    hard = f"\nThe student finds these hard (explain them with extra care): {scope.difficulties.strip()}" \
        if (scope.difficulties or "").strip() else ""
    return f"""TASK: Plan a study pack: one complete teaching document that teaches a student everything in the course material below ({facts['span']}): the textbook chapter these weeks never had.{hard}
Answer with one JSON object:
{{"title": "a short title naming the topics (not the course code)",
 "intro": "2–3 plain sentences for the student: what the pack covers and how to use it",
 "concepts": [{{"name": "a concept", "chapter": "c1"}}],
 "chapters": [{{"id": "c1", "title": "…", "summary": "1–2 sentences", "teaches": ["concept", "…"],
   "sources": {{"files": [{{"ref": "F1", "pages": [3, 9]}}], "lectures": [{{"ref": "L1", "part": 1, "from": "12:00", "to": "41:30"}}]}},
   "figures": [{{"id": "c1f1", "kind": "diagram", "brief": "exactly what to draw: shapes, arrows, axes, labels, values", "caption": "…"}},
               {{"id": "c1f2", "kind": "slide", "ref": "F1", "page": 4, "caption": "…"}},
               {{"id": "c1f3", "kind": "frame", "ref": "L1", "part": 1, "slide": 7, "caption": "…"}}]}}]}}
Rules:
- "concepts": EVERY definition, result, method, model and type of problem in the material, each with the chapter that teaches it. Nothing in the material may be left out.
- {chapters_range(facts)} chapters in teaching order (what a chapter needs comes before it), following the material's order unless it jumps; a short topic shares a chapter rather than getting its own. Each concept in exactly one chapter's "teaches".
- "sources": the file pages ([first, last]) and lecture passages (part, from–to as m:ss or h:mm:ss) each chapter is taught from.
- "figures": up to {MAX_FIGURES} per chapter, only where a picture teaches better than words, never for decoration. "slide": a page listed as having a figure, when that figure (a circuit, graph, diagram or table) is what the chapter explains; it is shown cropped to the picture. "frame": a slide captured from a lecture, "(slide N)" in that lecture part, when it shows board work or a figure the files lack. "diagram": drawn for the pack where the material describes a picture it doesn't show, or one makes an idea clear (a circuit, graph of a function, tree, timeline, block diagram, geometry), described precisely.
- Use only the references (F…, L…) listed below. Titles in {facts.get('language') or 'English'}.
{pack_block(facts)}
=== COURSE MATERIAL ===
{facts['pack']}"""


def chapter_count(full_chars: int) -> int:
    """About how many chapters the material makes (the estimate counts with it too): a 45-slide deck about 3–5."""
    return max(3, min(MAX_CHAPTERS, round(full_chars / 14_000) + 2))


def chapters_range(facts: dict[str, Any]) -> str:
    """The chapters the outline may make: about chapter_count, a little more when the material asks for it (a pack
    of nine chapters from one 45-slide deck read long and cost twice its estimate)."""
    if not facts.get("full_chars"):
        return f"3–{MAX_CHAPTERS}"
    n = chapter_count(facts["full_chars"])
    return f"{n}–{min(MAX_CHAPTERS, n + 2)}"


def _str(v: Any, limit: int = 400) -> str:
    return " ".join(str(v or "").split())[:limit]


def _secs(v: Any) -> Optional[float]:
    s = str(v or "").strip()
    if not re.fullmatch(r"\d+(?::\d{1,2}){0,2}", s):
        return None
    n = 0.0
    for x in s.split(":"):
        n = n * 60 + int(x)
    return n


def clean_sources(src: Any, facts: dict[str, Any]) -> dict[str, list]:
    """The pages and lecture passages the AI names for a chapter (or a cheatsheet's topic), checked: known references
    only, pages within the file, a lecture part that exists, times that make sense."""
    files = {f["ref"]: f for f in facts["files"]}
    lectures = {x["ref"]: x for x in facts["lectures"]}
    src = src if isinstance(src, dict) else {}
    fs = []
    for s in src.get("files") or []:
        f = files.get(str((s or {}).get("ref", "")).strip()) if isinstance(s, dict) else None
        if not f:
            continue
        pages = s.get("pages") if isinstance(s.get("pages"), list) else []
        try:
            a, b = (int(pages[0]), int(pages[-1])) if pages else (1, f["pages"])
        except (TypeError, ValueError):
            a, b = 1, f["pages"]
        a, b = max(1, min(a, b)), min(f["pages"], max(a, b))
        if a <= b:
            fs.append({"ref": f["ref"], "pages": [a, b]})
    ls = []
    for s in src.get("lectures") or []:
        x = lectures.get(str((s or {}).get("ref", "")).strip()) if isinstance(s, dict) else None
        if not x:
            continue
        part = s.get("part")
        part = int(part) if str(part).isdigit() and any(p["part"] == int(part) for p in x["parts"]) else 0
        a, b = _secs(s.get("from")), _secs(s.get("to"))
        ls.append({"ref": x["ref"], "part": part, "from": a if a is not None else 0,
                   "to": b if b is not None and (a is None or b > a) else None})
    return {"files": fs, "lectures": ls}


def clean_outline(raw: Any, facts: dict[str, Any]) -> dict[str, Any]:
    """The AI's outline, checked: known references only, pages within the file, a slide page only of a PDF and with
    a picture on it, a frame only of a slide that was captured, unique ids, at most MAX_CHAPTERS chapters and
    MAX_FIGURES figures each."""
    if not isinstance(raw, dict):
        raw = {}
    files = {f["ref"]: f for f in facts["files"]}
    lectures = {x["ref"]: x for x in facts["lectures"]}
    chapters, idmap = [], {}
    for c in [c for c in raw.get("chapters") or [] if isinstance(c, dict) and _str(c.get("title"))][:MAX_CHAPTERS]:
        cid = f"c{len(chapters) + 1}"
        title = _str(c.get("title"), 120)
        sources = clean_sources(c.get("sources"), facts)
        figs = []
        for g in c.get("figures") or []:
            if not isinstance(g, dict) or len(figs) >= MAX_FIGURES:
                continue
            kind, cap = str(g.get("kind", "")).strip(), _str(g.get("caption"), 240)
            fid = f"{cid}f{len(figs) + 1}"
            if kind == "diagram" and _str(g.get("brief")):
                figs.append({"id": fid, "kind": "diagram", "brief": _str(g.get("brief"), 900), "caption": cap})
            elif kind == "slide":
                f = files.get(str(g.get("ref", "")).strip())
                page = g.get("page")
                # a page with a picture on it (one gathered before 4.0 knows none: any page of the PDF)
                if f and f["pdf"] and str(page).isdigit() and 1 <= int(page) <= f["pages"] \
                        and ("pictures" not in f or int(page) in f["pictures"]):
                    figs.append({"id": fid, "kind": "slide", "ref": f["ref"], "page": int(page), "caption": cap})
            elif kind == "frame":
                x = lectures.get(str(g.get("ref", "")).strip())
                part, no = g.get("part"), g.get("slide")
                p = next((p for p in (x or {}).get("parts", []) if str(part).isdigit() and p["part"] == int(part)), None)
                if p and str(no).isdigit() and 1 <= int(no) <= p["slides"]:
                    figs.append({"id": fid, "kind": "frame", "ref": x["ref"], "part": p["part"], "slide": int(no),
                                 "caption": cap})
        teaches = [_str(x, 160) for x in c.get("teaches") or [] if _str(x)][:30]
        chapters.append({"id": cid, "title": title, "summary": _str(c.get("summary"), 500), "teaches": teaches,
                         "sources": sources, "figures": figs})
        idmap.setdefault(str(c.get("id", "")), cid)          # the concept list names the AI's ids
    if not chapters:
        raise RuntimeError(t("The AI's outline had no chapters. Try again."))
    ids = {c["id"] for c in chapters}
    concepts = [{"name": _str(k.get("name"), 160), "chapter": idmap.get(str(k.get("chapter", "")), "")}
                for k in raw.get("concepts") or [] if isinstance(k, dict) and _str(k.get("name"))][:200]
    return {"title": _str(raw.get("title"), 140) or facts["span"].capitalize(), "intro": _str(raw.get("intro"), 800),
            "chapters": chapters, "concepts": concepts, "language": facts.get("language") or "",
            "uncovered": [k["name"] for k in concepts if k["chapter"] not in ids]}


# ------------------------------------------------------------------ sections (AI)
def _slice(day: str, part: int, a: float, b: Optional[float]) -> str:
    """The lines of a lecture text (compact_day) in ``part`` between ``a`` and ``b`` seconds (part 0: every part)."""
    out, cur, keep = [], 0, False
    for line in day.split("\n"):
        m = re.match(r"## Part (\d+)", line)
        if m:
            cur = int(m.group(1))
            keep = not part or cur == part
            if keep:
                out.append(line)
            continue
        if not keep:
            continue
        ts = re.match(r"\[(?:Screen \d+, )?(\d+(?::\d{2}){1,2})[\]-]", line)      # (a screen line: at its start)
        if ts:
            s = _secs(ts.group(1)) or 0
            if s < a - 30 or (b is not None and s > b + 30):
                continue
        elif cur == 0:
            continue
        out.append(line)
    return "\n".join(out)


def chapter_material(lib: Any, course_id: str, facts: dict[str, Any], ch: dict[str, Any]) -> str:
    """The pages and lecture passages a chapter is taught from (the whole outline material when it names none)."""
    files = {f["ref"]: f for f in facts["files"]}
    lectures = {x["ref"]: x for x in facts["lectures"]}
    from .plans import _excerpt

    out, room = [], SECTION_CHARS
    for s in ch["sources"]["files"]:
        f = files[s["ref"]]
        body = _file_text(lib, f, (s["pages"][0], s["pages"][1]), limit=max(0, room))
        if body:
            text = _file_head(f, pictures=False) + body
            out.append(text)
            room -= len(text)
    days: dict[str, str] = {}
    for s in ch["sources"]["lectures"]:
        if room < 1_500:
            break
        x = lectures[s["ref"]]
        if x["day_key"] not in days:
            days[x["day_key"]] = lecture_text(lib, course_id, x["day_key"])
        body = _slice(days[x["day_key"]], s["part"], s["from"], s["to"])
        if body.strip():
            text = _lecture_head(x) + _excerpt(body, room)
            out.append(text)
            room -= len(text)
    return "\n".join(out) if out else facts["pack"][:SECTION_CHARS]


def chapter_pictures(facts: dict[str, Any], ch: dict[str, Any], most: int = CHAPTER_PICTURES) -> list[tuple[str, Path]]:
    """The pages with a figure a chapter is taught from (the ones it shows first), as pictures for its writer: a
    slide's circuit, values and equations are often pictures its text doesn't carry. [("[F1 p.4]", picture)]"""
    from .materials import page_image

    files = {f["ref"]: f for f in facts["files"]}
    lectures = {x["ref"]: x for x in facts["lectures"]}
    out: list[tuple[str, Path]] = []
    for g in ch["figures"]:                               # a lecture slide it shows: the one captured
        x = lectures.get(g.get("ref", "")) if g["kind"] == "frame" else None
        part = next((p for p in (x or {}).get("parts", []) if p["part"] == g.get("part")), None)
        if part and len(out) < most:
            shot = next((sl for sl in store.load_slides(part["delivery_id"]) if sl.index == g["slide"]), None)
            if shot and shot.path and Path(shot.path).is_file():
                out.append((f"[{g['ref']} part {g['part']}, slide {g['slide']}]", Path(shot.path)))
    pages = [(g["ref"], g["page"]) for g in ch["figures"] if g["kind"] == "slide"]
    for s in ch["sources"]["files"]:
        f = files.get(s["ref"]) or {}
        pages += [(s["ref"], p) for p in f.get("pictures") or [] if s["pages"][0] <= p <= s["pages"][1]]
    for ref, page in dict.fromkeys(pages):
        f = files.get(ref) or {}
        if len(out) >= most or not f.get("pdf") or not f.get("path") or not Path(f["path"]).is_file():
            continue
        try:
            img = page_image(Path(f["path"]), page, width=PICTURE_WIDTH)
        except Exception:  # noqa: BLE001 - a page that can't be drawn: written from its text
            log.info("Page %s of %s could not be drawn for its chapter", page, f.get("name"), exc_info=True)
            img = None
        if img:
            out.append((f"[{ref} p.{page}]", Path(img)))
    return out


def picture_blocks(pictures: list[tuple[str, Path]]) -> list[dict[str, Any]]:
    """Pictures as a request's content: each one's label, then the picture."""
    from .ai import image_block

    out: list[dict[str, Any]] = []
    for label, path in pictures:
        try:
            out += [{"type": "text", "text": label}, image_block(path)]
        except Exception:  # noqa: BLE001 - one that can't be read is left out
            log.info("A page picture could not be read", exc_info=True)
    return out


def section_prompt(outline: dict[str, Any], ch: dict[str, Any], material: str, scope: Any,
                   pictures: Optional[list[str]] = None) -> str:
    chapters = outline["chapters"]
    n = chapters.index(ch) + 1
    toc = "\n".join(f"{i}. {c['title']}: {', '.join(c['teaches']) or c['summary']}"
                    for i, c in enumerate(chapters, 1))
    figs = "\n".join(f"[[FIGURE {g['id']}]] ({g['caption'] or g.get('brief', '')})" for g in ch["figures"])
    hard = f"\nThe student finds these hard; explain them with extra care: {scope.difficulties.strip()}" \
        if (scope.difficulties or "").strip() else ""
    return f"""TASK: Write chapter {n} of {len(chapters)} of the study pack "{outline['title']}": "{ch['title']}".
The whole pack (teach only this chapter's concepts; refer to the others by their chapter number):
{toc}

This chapter teaches: {', '.join(ch['teaches']) or ch['summary']}.{hard}
Teach it as a good textbook chapter for a student meeting these ideas for the first time:
- Start with what the idea is for and the intuition, then the definitions, then the results and methods, each step explained.
- Every method gets at least one fully worked example (every step, the numbers checked), the material's own examples and notation where it has them.
- Point out the mistakes students make and how to avoid them.
- End with a "check" box: 3–5 questions in the course's style, each followed by its answer.
- An alert only when the student must act on it (material missing, the slides possibly wrong); not to say an example is your own.
Format (Markdown):
- Subheadings with ### (and ####), never # or ##; don't repeat the chapter title.
- Boxes: a line ":::kind Title", the box's text, then a line ":::". Kinds: definition, key (an important result or rule), example (a worked example: the question, then the solution), mistake (a common mistake), check (the closing questions and answers). Only for those, not around every paragraph.
- A table where the material compares cases or lists values.
{('- Figures: put each marker [[FIGURE id]] below on a line of its own, right after the paragraph that explains its figure, and refer to the figure in words ("the figure below"):' + chr(10) + figs) if figs else '- No figures in this chapter.'}
{('The pages of the material with figures are attached as pictures, labelled ' + ', '.join(pictures) + ': read the circuits, values and equations on them.' + chr(10)) if pictures else ''}{written_in(outline.get("language", ""))} Answer with the chapter's Markdown only.

=== THIS CHAPTER'S COURSE MATERIAL ===
{material}"""


_FIG = re.compile(r"\[\[\s*FIGURE\s+([\w-]+)\s*\]\]", re.I)


def clean_section(md: str, ch: dict[str, Any]) -> str:
    """A chapter's Markdown: no top-level headings (## → ###), known figure markers only, each figure placed once
    (one the AI did not place goes before the closing questions)."""
    md = md.replace("\x00", "").strip()
    md = re.sub(r"^```(?:markdown|md)?\s*\n(.*)\n```\s*$", r"\1", md, flags=re.S)       # wrapped in a fence
    lines = md.split("\n")
    if lines and re.match(r"#{1,2}\s", lines[0]) and _str(lines[0].lstrip("# ")).lower() in ch["title"].lower():
        lines = lines[1:]                                                             # the title again
    md = "\n".join(re.sub(r"^#{1,2}(\s)", r"###\1", x) for x in lines).strip()
    # a marker in a line of text goes on a line of its own (it is only drawn there); what describes it beside it,
    # "(a Venn diagram)" or "— the circuit", is the caption the figure already has
    def alone(m: re.Match) -> str:
        before, after = m.group(1).rstrip(), m.group(3).strip()
        if re.match(r"[-—–:(]", after):
            after = ""
        return (before + "\n\n" if before.strip() else "") + m.group(2) + ("\n\n" + after if after else "")

    md = re.sub(r"^(.*?)[ \t]*(\[\[\s*FIGURE\s+[\w-]+\s*\]\])(.*)$", alone, md, flags=re.M)
    known, placed = {g["id"] for g in ch["figures"]}, set()

    def mark(m: re.Match) -> str:
        fid = m.group(1)
        if fid not in known or fid in placed:
            return ""
        placed.add(fid)
        return f"[[FIGURE {fid}]]"

    md = _FIG.sub(mark, md)
    rest = "\n\n".join(f"[[FIGURE {g['id']}]]" for g in ch["figures"] if g["id"] not in placed)
    if rest:
        at = md.find("\n:::check")
        md = f"{md[:at]}\n\n{rest}\n{md[at:]}" if at >= 0 else f"{md}\n\n{rest}"
    return md


# ------------------------------------------------------------------ figures (AI draws SVG; slides and frames as they are)
def figure_prompt(ch: dict[str, Any], md: str) -> str:
    briefs = "\n".join(f"{g['id']}: {g['brief']}" + (f" (caption: {g['caption']})" if g["caption"] else "")
                       for g in ch["figures"] if g["kind"] == "diagram")
    return f"""TASK: Draw the figures of the study pack chapter below as SVG.
Figures to draw:
{briefs}

For each one, answer exactly:
=== FIGURE <id> ===
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 <height>">…</svg>
Caption: <one short sentence>

Rules: a clean, correct textbook figure that matches the chapter's text, notation and numbers, simple enough to read at a glance; nothing decorative. Only these elements: g, path, line, polyline, polygon, rect, circle, ellipse, text, tspan, defs, marker (for arrowheads). No script, style, image, foreignObject, links or CSS classes. Width 640; height as needed (200–420); 16px clear at the edges. font-family="Segoe UI, Arial, sans-serif", font-size 13–15; no label touches a line or another label; every axis, arrow and part labelled. Lines and text #1f2937 (stroke-width 1.5–2), one accent #1d4ed8 for what the figure is about, #dc2626 only for a contrast or an error; light fills (#eff5ff, #f3f4f6); no background rectangle. Circuits: straight wires at right angles, resistors as zigzags, capacitors as two plates, inductors as loops, sources as circles with + and −, every element's name and value beside it. Graphs: axes with arrowheads, names and a few tick values. Maths in labels as plain Unicode text (θ, ω, x², √, ≤, →, subscripts with <tspan baseline-shift="sub" font-size="11">), not LaTeX.

=== THE CHAPTER ===
{md[:16_000]}"""


def parse_figures(text: str, ids: set[str]) -> dict[str, dict[str, str]]:
    """The "=== FIGURE id ===" blocks of the answer: {id: {"svg", "caption"}} (raw SVG, sanitised later)."""
    out: dict[str, dict[str, str]] = {}
    for m in re.finditer(r"===\s*FIGURE\s+([\w-]+)\s*===\s*(.*?)(?====\s*FIGURE|\Z)", text or "", re.S):
        fid, body = m.group(1), m.group(2)
        svg = re.search(r"<svg\b.*?</svg>", body, re.S | re.I)
        if fid in ids and svg:
            cap = re.search(r"Caption:\s*(.+)", body[svg.end():])
            out[fid] = {"svg": svg.group(0), "caption": _str(cap.group(1), 240) if cap else ""}
    return out


# ---- the SVG sanitiser: what the AI draws goes into the app's own page, so only shapes and text survive
SVG_NS = "http://www.w3.org/2000/svg"
XLINK = "http://www.w3.org/1999/xlink"
_TAGS = {"svg", "g", "defs", "title", "desc", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon",
         "text", "tspan", "marker", "lineargradient", "radialgradient", "stop", "clippath", "use", "symbol"}
_UNWRAP = {"a", "switch"}                       # their content is kept, the element is not
_ATTRS = {a.lower(): a for a in (
    "id x y x1 y1 x2 y2 cx cy r rx ry fx fy width height d points transform viewBox preserveAspectRatio fill "
    "fill-opacity fill-rule stroke stroke-width stroke-opacity stroke-dasharray stroke-dashoffset stroke-linecap "
    "stroke-linejoin stroke-miterlimit opacity font-family font-size font-weight font-style text-anchor "
    "dominant-baseline alignment-baseline baseline-shift letter-spacing word-spacing text-decoration dx dy rotate "
    "textLength lengthAdjust marker-start marker-mid marker-end markerWidth markerHeight markerUnits refX refY orient "
    "offset stop-color stop-opacity gradientUnits gradientTransform spreadMethod clip-path clip-rule clipPathUnits "
    "visibility vector-effect paint-order shape-rendering href").split()}
_STYLE = {"fill", "fill-opacity", "fill-rule", "stroke", "stroke-width", "stroke-opacity", "stroke-dasharray",
          "stroke-dashoffset", "stroke-linecap", "stroke-linejoin", "stroke-miterlimit", "opacity", "font-family",
          "font-size", "font-weight", "font-style", "text-anchor", "dominant-baseline", "letter-spacing",
          "text-decoration", "stop-color", "stop-opacity", "marker-start", "marker-mid", "marker-end", "visibility",
          "paint-order", "baseline-shift"}
_URL = re.compile(r"url\(\s*['\"]?#([\w.-]+)['\"]?\s*\)", re.I)
_BAD = re.compile(r"javascript:|vbscript:|data:|expression\(|@import|\\|&#|<|>", re.I)
_XML_ENTITIES = {"amp", "lt", "gt", "quot", "apos"}


def _safe_value(name: str, value: str, prefix: str) -> Optional[str]:
    value = " ".join(str(value).split())
    if len(value) > (40_000 if name in ("d", "points") else 600) or _BAD.search(value):
        return None
    if "url(" in value.lower():
        if len(_URL.findall(value)) != value.lower().count("url("):
            return None                                       # only url(#id): never a file or a web address
        value = _URL.sub(lambda m: f"url(#{prefix}{m.group(1)})", value)
    return value


def sanitize_svg(svg: str, prefix: str = "") -> Optional[str]:
    """An SVG the AI drew, rebuilt from an allow-list: shapes, text, markers and gradients with their presentation
    attributes; no scripts, event handlers, styles, images, foreign content or links out (``href`` only to
    ``#id``); ids are given ``prefix`` (several figures share one page) and references to them follow. None when it
    is not a readable SVG."""
    if not svg or len(svg) > 300_000 or re.search(r"<!DOCTYPE|<!ENTITY", svg, re.I):
        return None
    svg = re.sub(r"<\?xml[^>]*\?>", "", svg).strip()
    svg = re.sub(r"&([A-Za-z][A-Za-z0-9]*);",                 # HTML entities (&nbsp; &times;) as characters
                 lambda m: m.group(0) if m.group(1) in _XML_ENTITIES else html.unescape(m.group(0)), svg)
    if "xlink:" in svg and "xmlns:xlink" not in svg:     # often used without being declared
        svg = re.sub(r"<svg\b", f'<svg xmlns:xlink="{XLINK}"', svg, count=1)
    if not re.search(r"<svg\b[^>]*\bxmlns\s*=", svg[:2000]):
        svg = re.sub(r"<svg\b", f'<svg xmlns="{SVG_NS}"', svg, count=1)
    try:
        root = ET.fromstring(svg)
    except ET.ParseError:
        return None
    if _local(root.tag) != "svg":
        return None
    count = [0]

    def attrs(el: ET.Element) -> list[tuple[str, str]]:
        out = []
        for k, v in el.attrib.items():
            ns, name = (k[1:].split("}", 1) if k.startswith("{") else ("", k))
            if ns not in ("", XLINK) or (ns == XLINK and name != "href"):
                continue
            canon = _ATTRS.get(name.lower())
            if name.lower() == "style":
                decls = []
                for decl in str(v).split(";"):
                    p, _, val = decl.partition(":")
                    p = p.strip().lower()
                    val = _safe_value(p, val, prefix) if p in _STYLE else None
                    if val:
                        decls.append(f"{p}: {val}")
                if decls:
                    out.append(("style", "; ".join(decls)))
                continue
            if not canon:
                continue                                    # on…, class, style sheets, anything unknown
            if canon == "href":
                if not re.fullmatch(r"#[\w.-]+", str(v).strip()):
                    continue
                out.append(("href", f"#{prefix}{str(v).strip()[1:]}"))
                continue
            val = _safe_value(canon, v, prefix)
            if val is None:
                continue
            if canon == "id":
                if not re.fullmatch(r"[\w.-]+", val):
                    continue
                val = prefix + val
            out.append((canon, val))
        return out

    def write(el: ET.Element, top: bool = False) -> str:
        tag = _local(el.tag)
        ns = el.tag[1:].split("}", 1)[0] if el.tag.startswith("{") else ""
        count[0] += 1
        if count[0] > 6_000:
            raise ValueError("too many elements")
        inner = html.escape(el.text or "", quote=False) + "".join(
            write(c) + html.escape(c.tail or "", quote=False) for c in el)
        if ns != SVG_NS or tag.lower() in _UNWRAP:
            return inner if tag.lower() in _UNWRAP and ns == SVG_NS else ""
        if tag.lower() not in _TAGS:
            return ""
        a = attrs(el)
        if top:
            a = [(k, v) for k, v in a if k not in ("width", "height", "id")]
            if not any(k == "viewBox" for k, _ in a):
                w = _num(el.get("width")) or 640
                h = _num(el.get("height")) or 360
                a.insert(0, ("viewBox", f"0 0 {w:g} {h:g}"))
            a = [("xmlns", SVG_NS), *a, ("class", "sp-svg"), ("role", "img")]
        canon = {"lineargradient": "linearGradient", "radialgradient": "radialGradient", "clippath": "clipPath"}
        name = canon.get(tag.lower(), tag.lower())
        return f"<{name}{''.join(f' {k}={chr(34)}{html.escape(v)}{chr(34)}' for k, v in a)}>{inner}</{name}>"

    try:
        out = write(root, top=True)
    except (ValueError, RecursionError):
        return None
    if not re.search(r"<(path|rect|circle|ellipse|line|polyline|polygon|text)\b", out):
        return None                                     # nothing drawn
    return out


def _local(tag: Any) -> str:
    return str(tag).rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _num(v: Any) -> Optional[float]:
    m = re.match(r"\s*([\d.]+)", str(v or ""))
    try:
        return float(m.group(1)) if m else None
    except ValueError:
        return None


# ------------------------------------------------------------------ Markdown → HTML (the subset the chapters use)
ALERT_LABELS = {"note": "Note", "tip": "Tip", "important": "Important", "warning": "Warning", "caution": "Caution"}
ALERT_SIGNS = {"note": "i", "tip": "✓", "important": "★", "warning": "!", "caution": "×"}
BOXES = {"definition": "Definition", "key": "Key idea", "example": "Example", "mistake": "Common mistake",
         "check": "Check yourself", "note": "Note", "theorem": "Key idea", "tip": "Note", "warning": "Common mistake"}
_MATH = re.compile(r"\$\$.+?\$\$|\\\[.+?\\\]|\\\(.+?\\\)|(?<![\\$\w])\$(?=\S)[^$\n]+?(?<=\S)\$(?!\d)", re.S)


def _inline(text: str, keep: list[str]) -> str:
    """One line or paragraph of text: escaped, with **bold**, *italic*, `code` and [links](…) (their text); the
    formulas (already set aside in ``keep``) come back as written, for KaTeX to draw."""
    s = html.escape(text, quote=False)
    s = re.sub(r"`([^`]+)`", lambda m: f"<code>{m.group(1)}</code>", s)
    s = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"__(?=\S)(.+?)(?<=\S)__", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![\w*])\*(?=\S)(.+?)(?<=\S)\*(?![\w*])", r"<em>\1</em>", s)
    s = re.sub(r"(?<![\w_])_(?=\S)(.+?)(?<=\S)_(?![\w_])", r"<em>\1</em>", s)
    s = re.sub(r"\[([^\]]+)\]\((?:[^)\s]+)\)", r"\1", s)
    s = re.sub(r"\[ \]", "☐", s)
    # a formula keeps the punctuation around it on its line ("i(τ)" then "." alone on the next one read badly): a word
    # joiner (U+2060) between them, which KaTeX leaves where it is
    s = re.sub(r"(\x00\d+\x00)(?=[.,;:!?)\]])", "\\1\u2060", re.sub(r"(?<=[(\[])(\x00\d+\x00)", "\u2060\\1", s))
    return re.sub(r"\x00(\d+)\x00", lambda m: html.escape(keep[int(m.group(1))], quote=False), s)


def md_html(md: str, figure: Callable[[str], str] = lambda fid: "",
            image: Optional[Callable[[str, str], str]] = None) -> str:
    """A chapter's Markdown as HTML: ### headings, paragraphs, lists (nested), tables, block quotes, code, rules,
    ":::kind Title" boxes, [[FIGURE id]] lines (``figure(id)``: its HTML) and pictures on a line of their own
    ("![caption](src)": ``image(caption, src)``, else left out). All text is escaped; formulas stay as written ($…$,
    $$…$$) for KaTeX."""
    keep: list[str] = []
    shown = figure

    def figure(ref: str, src: Optional[str] = None) -> str:   # noqa: F811 - [[FIGURE id]] and pictures, one way down
        return shown(ref) if src is None else image(ref, src) if image else ""

    def hold(m: re.Match) -> str:
        text = m.group(0)
        if "\n" in text:            # a formula over lines in a quote or an alert: without the "> " of its next lines
            q = re.match(r"[ \t]*((?:>[ \t]?)+)", m.string[m.string.rfind("\n", 0, m.start()) + 1:m.start()])
            if q:                   # (as many as the line it opens on has: "> 0" in it stays a relation)
                text = re.sub(r"\n[ \t]*(?:>[ \t]?){1,%d}" % q.group(1).count(">"), "\n", text)
        keep.append(text)
        return f"\x00{len(keep) - 1}\x00"

    blocks: list[str] = []                  # fenced code first: no formulas or Markdown inside
    md = re.sub(r"^```[^\n]*\n(.*?)^```[ \t]*$", lambda m: (blocks.append(m.group(1)), f"\x01{len(blocks) - 1}\x01")[1],
                md.replace("\x00", "").replace("\x01", "").replace("\r\n", "\n"), flags=re.S | re.M)
    md = _MATH.sub(hold, md)
    return _blocks(md.split("\n"), keep, blocks, figure)


_PICTURE = re.compile(r"!\[([^\]\n]*)\]\(\s*<?([^)\s>]+)>?\s*\)")      # (outputs/figures.py IMAGE_LINE)


def box_head(kind: str, title: str) -> tuple[str, str]:
    """A box's label and title without saying the same twice: ":::check Check" is "Check yourself", ":::example
    Example 4" is "Example 4", ":::example Worked example: RC discharge" is "Example · RC discharge"."""
    label, title = BOXES.get(kind, BOXES["note"]), " ".join((title or "").split())
    words = {label.lower(), kind, "worked example", "example", "check yourself", "check", "common mistake", "mistake",
             "definition", "key idea", "key result", "note"}
    for w in sorted(words, key=len, reverse=True):
        if title.lower().startswith(w) and (len(title) == len(w) or not title[len(w)].isalnum()):
            rest = title[len(w):].strip(" :.-–—")
            num = re.match(r"(\d+(?:\.\d+)*[a-z]?|[A-Z])(?:$|[\s:.,)–—-]+)", rest)   # "Example 4", "Example 2.3: …"
            if num:
                label, rest = f"{label} {num.group(1)}", rest[num.end():].strip(" :.-–—")
            return label, rest[:1].upper() + rest[1:]
    return label, title


def _blocks(lines: list[str], keep: list[str], code: list[str], figure: Callable[..., str]) -> str:
    out: list[str] = []
    para: list[str] = []
    i = 0

    def flush() -> None:
        if para:
            out.append(f"<p>{_inline(' '.join(x.strip() for x in para), keep)}</p>")
            para.clear()

    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if not s:
            flush()
            i += 1
            continue
        m = re.fullmatch(r":::\s*(\w+)\s*(.*)", s)
        if m and m.group(1).lower() in BOXES:
            flush()
            j = i + 1
            while j < len(lines) and lines[j].strip() != ":::":
                j += 1
            kind = m.group(1).lower()
            cls = {"theorem": "key", "tip": "note", "warning": "mistake"}.get(kind, kind)
            label, title = box_head(kind, m.group(2))
            title = _inline(title, keep)
            out.append(f'<div class="sp-box sp-{cls}"><div class="sp-box-h"><b>{html.escape(label)}</b>'
                       f'{" · " + title if title else ""}</div>{_blocks(lines[i + 1:j], keep, code, figure)}</div>')
            i = j + 1
            continue
        if s == ":::":
            i += 1
            continue
        m = _FIG.fullmatch(s)
        if m:
            flush()
            out.append(figure(m.group(1)))
            i += 1
            continue
        m = _PICTURE.fullmatch(s)
        if m:
            flush()
            out.append(figure(m.group(1), m.group(2)))
            i += 1
            continue
        m = re.fullmatch(r"\x01(\d+)\x01", s)
        if m:
            flush()
            out.append(f"<pre><code>{html.escape(code[int(m.group(1))].rstrip(), quote=False)}</code></pre>")
            i += 1
            continue
        m = re.fullmatch(r"\x00(\d+)\x00", s)
        if m and keep[int(m.group(1))].startswith(("$$", "\\[")):
            flush()
            out.append(f'<div class="sp-math">{html.escape(keep[int(m.group(1))], quote=False)}</div>')
            i += 1
            continue
        m = re.fullmatch(r"(#{1,6})\s+(.*?)\s*#*", s)
        if m:
            flush()
            level = min(5, max(3, len(m.group(1))))
            out.append(f"<h{level}>{_inline(m.group(2), keep)}</h{level}>")
            i += 1
            continue
        if re.fullmatch(r"(?:-\s*){3,}|(?:\*\s*){3,}|(?:_\s*){3,}", s):
            flush()
            out.append("<hr>")
            i += 1
            continue
        if s.startswith(">"):
            flush()
            j = i
            while j < len(lines) and lines[j].strip().startswith(">"):
                j += 1
            inner = [re.sub(r"^\s*>\s?", "", x) for x in lines[i:j]]
            m = re.fullmatch(r"\[!(\w+)\]\s*(.*)", inner[0].strip())
            if m and m.group(1).lower() in KINDS:          # "> [!WARNING]": an alert box (notices.py)
                kind = m.group(1).lower()
                title = _inline(m.group(2), keep) if m.group(2) else ALERT_LABELS[kind]
                out.append(f'<div class="sp-alert sp-alert-{kind}" role="note"><div class="sp-alert-h">'
                           f'<span class="sp-alert-i" aria-hidden="true">{ALERT_SIGNS[kind]}</span><b>{title}</b></div>'
                           f'{_blocks(inner[1:], keep, code, figure)}</div>')
                i = j
                continue
            out.append(f"<blockquote>{_blocks(inner, keep, code, figure)}</blockquote>")
            i = j
            continue
        if s.startswith("|") and i + 1 < len(lines) and re.fullmatch(r"\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?",
                                                                       lines[i + 1].strip()):
            flush()
            j = i + 2
            while j < len(lines) and lines[j].strip().startswith("|"):
                j += 1

            def cells(row: str) -> list[str]:
                return [c.strip() for c in row.strip().strip("|").split("|")]

            head = "".join(f"<th>{_inline(c, keep)}</th>" for c in cells(lines[i]))
            body = "".join("<tr>" + "".join(f"<td>{_inline(c, keep)}</td>" for c in cells(r)) + "</tr>"
                           for r in lines[i + 2:j])
            out.append(f'<div class="sp-table"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>')
            i = j
            continue
        if _ITEM.match(line):
            flush()
            j = i
            while j < len(lines):
                if lines[j].strip():
                    if not (_ITEM.match(lines[j]) or lines[j].startswith((" ", "\t"))):
                        break
                    j += 1
                    continue
                # a blank line: the list goes on when the next line with text is an item or indented (an answer under
                # its question, a formula between two steps), as in the app's chats
                k = j
                while k < len(lines) and not lines[k].strip():
                    k += 1
                if k == len(lines) or not (_ITEM.match(lines[k]) or re.match(r"(?: {2,}|\t)\S", lines[k])):
                    break
                j = k
            out.append(_list(lines[i:j], keep, code, figure))
            i = j
            continue
        para.append(line)
        i += 1
    flush()
    return "".join(out)


_ITEM = re.compile(r"\s*(?:[-*+]|\d+[.)])\s+")


def _list(lines: list[str], keep: list[str], code: list[str], figure: Callable[[str], str]) -> str:
    """Consecutive list lines as nested <ul>/<ol> (by indentation); an indented line without a marker, or a blank
    one, continues the item above it. A numbered list keeps its first number ("<ol start=3>": an answer or a
    formula between the items of a "Check yourself" box doesn't make them 1, 1, 1)."""
    items: list[tuple[int, bool, list[str], int]] = []
    for line in lines:
        m = re.match(r"(\s*)([-*+]|\d+[.)])\s+(.*)", line)
        if m:
            items.append((len(m.group(1).replace("\t", "    ")), m.group(2)[0].isdigit(), [m.group(3)],
                          int(m.group(2)[:-1]) if m.group(2)[0].isdigit() else 1))
        elif items:
            items[-1][2].append(line.strip())
    html_out, stack = [], []                          # stack: (indent, tag)
    for indent, ordered, text, number in items:
        tag = "ol" if ordered else "ul"
        while stack and indent < stack[-1][0]:
            html_out.append(f"</li></{stack.pop()[1]}>")
        if stack and indent == stack[-1][0] and stack[-1][1] != tag:      # "- a" then "1. b": a list of its own
            html_out.append(f"</li></{stack.pop()[1]}>")
        if not stack or indent > stack[-1][0]:
            html_out.append(f'<{tag} start="{number}">' if ordered and number != 1 else f"<{tag}>")
            stack.append((indent, tag))
        else:
            html_out.append("</li>")
        while len(text) > 1 and not text[-1]:         # the blank lines before the next item
            text = text[:-1]
        body = text[0] if len(text) == 1 else "\n".join(text)
        inner = _blocks(body.split("\n"), keep, code, figure) if len(text) > 1 else _inline(body, keep)
        if inner.startswith("<p>") and inner.count("<p>") == 1 and inner.endswith("</p>"):
            inner = inner[3:-4]
        html_out.append(f"<li>{inner}")
    while stack:
        html_out.append(f"</li></{stack.pop()[1]}>")
    return "".join(html_out)


# ------------------------------------------------------------------ the document
CSS_FILE = Path(__file__).resolve().parent / "web" / "studypack.css"
# How a pack's document is drawn: raised when the code draws it differently (md_html, document_body), as the
# stylesheet's own changes are seen by itself. A finished pack laid out with another is laid out again from its
# saved data, no AI, before its PDF is opened or saved (make_pdf).
LAYOUT = 3           # 2 (4.0): numbered lists keep their numbers, formulas in quotes, wide lines wrap on paper;
                     # 3 (4.0): lectures named by their day, pictures on a line of their own


def layout() -> str:
    return f"{LAYOUT}-{hashlib.sha1(CSS_FILE.read_bytes()).hexdigest()[:12]}"


def figure_html(fig: dict[str, Any], number: str, src: Callable[[str], str]) -> str:
    """A figure as HTML: the sanitised SVG, or the image ``src(path)`` gives; "" when it could not be made."""
    cap = html.escape(fig.get("caption") or "", quote=False)
    if fig.get("svg"):
        body = fig["svg"]
    elif fig.get("image"):
        what = "Lecture slide" if fig["kind"] == "frame" else "Course slide"
        body = f'<img src="{html.escape(src(fig["image"]))}" alt="{what}">'
        cap = f"{cap} <span class=\"sp-src\">({html.escape(fig.get('source', ''), quote=False)})</span>" \
            if fig.get("source") else cap
    else:
        return ""
    return (f'<figure class="sp-fig sp-{fig["kind"]}">{body}<figcaption><b>Figure {number}.</b> {cap}</figcaption>'
            "</figure>")


def document_body(outline: dict[str, Any], sections: dict[str, str], figures: dict[str, dict[str, Any]],
                  meta: dict[str, Any], src: Callable[[str], str], pages: Optional[dict[str, int]] = None) -> str:
    """The pack's body: cover, contents (with page numbers once they are known: ``pages``) and the chapters."""
    esc = lambda s: html.escape(str(s or ""), quote=False)       # noqa: E731
    sources = []
    if meta.get("files"):
        sources.append("Files: " + ", ".join(esc(f["name"]) for f in meta["files"]))
    if meta.get("lectures"):
        from .cheatsheet import _day

        sources.append("Lectures: " + ", ".join(esc(_day(x["date"])) for x in meta["lectures"]))
    out = [f'<header class="sp-cover"><div class="sp-kicker">{esc(meta["course"])} · Study pack</div>'
           f'<h1>{esc(outline["title"])}</h1><div class="sp-sub">{esc(meta["span"])} · made with IEUStudia on '
           f'{esc(meta["date"])}</div>' + (f'<p class="sp-intro">{_inline(outline["intro"], [])}</p>' if outline.get("intro") else "")
           + "".join(f'<div class="sp-sources">{s}</div>' for s in sources)
           + '<div class="sp-sources">Written by AI from your course material: check anything that looks wrong '
             'against your slides and lectures.</div></header>']
    rows = "".join(f'<li><a href="#{c["id"]}"><span class="n">{n}</span><span class="t">{esc(c["title"])}</span>'
                   f'<span class="p">{(pages or {}).get(c["id"], "")}</span></a></li>'
                   for n, c in enumerate(outline["chapters"], 1))
    out.append(f'<nav class="sp-toc"><h2>Contents</h2><ol>{rows}</ol></nav>')
    for n, c in enumerate(outline["chapters"], 1):
        numbers = {g["id"]: f"{n}.{k}" for k, g in enumerate(c["figures"], 1)}

        def fig(fid: str) -> str:
            return figure_html(figures[fid], numbers.get(fid, ""), src) if fid in figures else ""

        out.append(f'<section class="sp-ch" id="{c["id"]}"><h2><span class="n">{n}</span> {esc(c["title"])}</h2>'
                   f'{md_html(sections.get(c["id"], ""), fig)}</section>')
    return "".join(out)


def document_html(body: str, title: str, folder: Optional[Path] = None) -> str:
    """The document as a page of its own (the file and the PDF): the app's stylesheet for it, and KaTeX (a copy in
    ``folder``, the kept file's, so it works anywhere; else the app's)."""
    from .outputs.mathmd import katex_tags

    css = CSS_FILE.read_text(encoding="utf-8")
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{html.escape(title)}</title>'
            f"<style>{css}</style></head><body class=\"sp-page\"><article class=\"sp-doc\">{body}</article>"
            f"{katex_tags(folder)}</body></html>")


# ------------------------------------------------------------------ PDF
_QT_PRINT = r"""
import sys
from qtpy.QtCore import QMarginsF, QTimer, QUrl
from qtpy.QtGui import QPageLayout, QPageSize
from qtpy.QtWidgets import QApplication
try:
    from qtpy.QtWebEngineCore import QWebEnginePage
except ImportError:
    from qtpy.QtWebEngineWidgets import QWebEnginePage
app = QApplication(sys.argv[:1])
page = QWebEnginePage()
done = lambda *a: app.quit()
page.pdfPrintingFinished.connect(done)
layout = QPageLayout(QPageSize(QPageSize.A4), QPageLayout.Portrait, QMarginsF(0, 0, 0, 0))
page.loadFinished.connect(lambda ok: QTimer.singleShot(1500, lambda: page.printToPdf(sys.argv[2], layout)))
QTimer.singleShot(120000, app.quit)
page.load(QUrl.fromLocalFile(sys.argv[1]))
app.exec_() if hasattr(app, "exec_") else app.exec()
"""


def print_pdf(html_path: Path, pdf_path: Path) -> bool:
    """``html_path`` printed to ``pdf_path``: by Edge (or Chrome) headless - the engine of the app's own window on
    Windows - or, on Linux without one, by Qt WebEngine (the Linux window's engine) in a process of its own."""
    from .export import find_browser, html_to_pdf

    pdf_path.unlink(missing_ok=True)
    if find_browser():
        return html_to_pdf(html_path, pdf_path)
    if sys.platform.startswith("linux") and not getattr(sys, "frozen", False):
        try:
            subprocess.run([sys.executable, "-c", _QT_PRINT, str(html_path), str(pdf_path)], capture_output=True,
                           timeout=180, env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
        except (OSError, subprocess.TimeoutExpired):
            log.warning("Qt WebEngine could not print the study pack", exc_info=True)
        return pdf_path.exists() and pdf_path.stat().st_size > 0
    log.warning("No Edge/Chrome found: the study pack has no PDF")
    return False


def chapter_pages(pdf_path: Path, titles: list[str]) -> dict[int, int]:
    """On which page of the PDF each chapter starts (chapters start on a page of their own, their heading first; the
    page number in the footer may be read before it): {chapter index: page number}."""
    import pypdfium2 as pdfium

    def norm(s: str) -> str:
        return re.sub(r"[^\w]+", "", s).lower()

    from .materials import PDFIUM_LOCK

    out: dict[int, int] = {}
    with PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(str(pdf_path))
        try:
            heads = []
            for i in range(len(pdf)):
                tp = pdf[i].get_textpage()
                lines = [x for x in tp.get_text_range().replace("\r", "").split("\n") if x.strip()]
                tp.close()
                lines = [x for x in lines if not re.fullmatch(r"\s*\d+\s*(/\s*\d+)?\s*", x)]
                heads.append(norm(" ".join(lines[:3])))
        finally:
            pdf.close()
    k = 0
    for n, title in enumerate(titles):
        want = norm(f"{n + 1}{title}")[:40]
        for i in range(k, len(heads)):
            if heads[i].startswith(want):
                out[n], k = i + 1, i + 1
                break
    return out


# ------------------------------------------------------------------ the stages (run by plans.PlanRunner)
def _usage_add(usage: dict[str, Any], stage: str, res: dict[str, Any]) -> None:
    u = usage.setdefault(stage, {})
    for k, v in (res.get("usage") or {}).items():
        u[k] = u.get(k, 0) + int(v or 0)
    u["model"] = res.get("model", "")


def run(runner: Any, plan: dict[str, Any], scope: Any, data: dict[str, Any], usage: dict[str, Any], model: str,
        cancel: threading.Event) -> None:
    """Build (or resume) the study pack ``plan``; every finished stage, chapter and figure is saved at once."""
    from .ai import ask_json, one_shot
    from .plans import model_shown

    pid, s = plan["id"], runner._s
    level = _level(scope, s.settings)
    shown = model_shown(model, level)       # "GPT-6.1-Sol · High", "Sonnet · Low"
    lock = threading.Lock()

    def stage(name: str, progress: float, message: str = "") -> None:
        s.lib.update_plan(pid, status="running", stage=name, progress=round(progress, 3),
                          message=message or t(LABELS[name]) + "…")
        runner._emit(pid)

    def save(**more: Any) -> None:
        with lock:
            s.lib.update_plan(pid, data=data, usage=usage, **more)

    def check() -> None:
        if cancel.is_set():
            raise Cancelled()

    def ask_text(prompt: str, more: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
        return (runner._ask_text or one_shot)(SYSTEM, [{"type": "text", "text": prompt}, *(more or [])], model, level,
                                              cancel, None)

    # gather
    if "gather" not in data:
        stage("gather", 0.02)
        data["gather"] = gather(s.lib, s.settings, plan["course_id"], scope)
        save()
    facts = data["gather"]
    if not facts["files"] and not facts["lectures"]:
        raise RuntimeError(t("None of the picked files is on this PC and none of the lectures is transcribed yet."))
    check()
    # outline
    if "outline" not in data:
        stage("outline", 0.05, t("Planning the chapters ({model})…", model=shown))
        raw, res = (runner._ask or ask_json)(SYSTEM, outline_prompt(scope, facts), model=model, effort=level,
                                            cancel=cancel)
        _usage_add(usage, "outline", res)
        data["outline"] = clean_outline(raw, facts)
        save(title=data["outline"]["title"])
    outline = data["outline"]
    chapters = outline["chapters"]
    check()
    # sections
    done: dict[str, str] = data.setdefault("sections", {})
    left = [c for c in chapters if c["id"] not in done]
    if left:
        def progress() -> None:
            n = len(done)
            stage("sections", 0.12 + 0.6 * n / len(chapters),
                  t("Writing chapter {n} of {total} ({model})…", n=min(n + 1, len(chapters)), total=len(chapters),
                    model=shown))
            aiwait.of(cancel).units(n, len(chapters), SECTION_PARALLEL)      # the chapters still to come (time left)

        def write(ch: dict[str, Any]) -> tuple[str, str, dict]:
            material = chapter_material(s.lib, plan["course_id"], facts, ch) + chapter_block(facts, ch)
            pictures = picture_blocks(chapter_pictures(facts, ch))   # the pages it explains: figures text can't carry
            res = ask_text(section_prompt(outline, ch, material, scope, [b["text"] for b in pictures if "text" in b]),
                           pictures)
            md = clean_section(res.get("text") or "", ch)
            if len(md) < 300:
                raise RuntimeError(t("The AI returned an empty chapter. Try again."))
            return ch["id"], md, res

        progress()
        def keep(f: Any) -> None:
            cid, md, res = f.result()
            with lock:
                if cid not in done:
                    done[cid] = md
                    _usage_add(usage, "sections", res)

        failed: Optional[BaseException] = None
        with ThreadPoolExecutor(max_workers=SECTION_PARALLEL, thread_name_prefix="pack-write") as pool:
            futures = [pool.submit(write, c) for c in left]
            try:
                for f in as_completed(futures):
                    keep(f)
                    save()
                    progress()
            except BaseException as exc:  # noqa: BLE001 - raised again below
                failed = exc
                for f in futures:                  # don't start the ones still waiting
                    f.cancel()
        if failed is not None:                     # a chapter that was being written meanwhile is kept
            for f in futures:
                if f.done() and not f.cancelled() and f.exception() is None:
                    keep(f)
            save()
            raise failed
    check()
    # figures: what the AI draws, per chapter
    figs: dict[str, dict[str, Any]] = data.setdefault("figures", {})
    drawn: list[str] = data.setdefault("figures_done", [])
    todo = [c for c in chapters if c["id"] not in drawn and any(g["kind"] == "diagram" for g in c["figures"])]
    for n, ch in enumerate(todo):
        stage("figures", 0.74 + 0.16 * n / max(1, len(todo)),
              t("Drawing the figures of chapter {n} ({model})…", n=chapters.index(ch) + 1, model=shown))
        ids = {g["id"] for g in ch["figures"] if g["kind"] == "diagram"}
        res = ask_text(figure_prompt(ch, done.get(ch["id"], "")))
        _usage_add(usage, "figures", res)
        for fid, fig in parse_figures(res.get("text") or "", ids).items():
            clean = sanitize_svg(fig["svg"], prefix=f"{fid}-")
            if clean:
                brief = next(g for g in ch["figures"] if g["id"] == fid)
                figs[fid] = {"kind": "diagram", "svg": clean, "caption": brief["caption"] or fig["caption"]}
        drawn.append(ch["id"])
        save()
        check()
    # assemble
    stage("assemble", 0.92)
    if not data.get("stem"):
        files_for(s, plan, outline["title"], data)               # its file name, kept before anything is written
        save()
    result = assemble(s, plan, outline, facts, done, figs, data)
    data["assembled"] = True
    save(result=result, title=result["title"], status="done", stage="done", progress=1.0,
         message=tn(len(chapters), "{n} chapter · {figures} figures", "{n} chapters · {figures} figures",
                    figures=result["figures"]))


def _pictures(lib: Any, plan: dict[str, Any], facts: dict[str, Any], outline: dict[str, Any],
              folder: Path, keep: bool = False) -> dict[str, dict[str, Any]]:
    """The slide pages (cropped to their picture: outputs/figures.py) and lecture frames the outline asks for, as JPEGs
    in ``folder``: {figure id: figure}. ``keep``: one already there is used as it is (a pack laid out again: its
    source file may have moved since)."""
    from .materials import page_image
    from .outputs.figures import figure_image

    files = {f["ref"]: f for f in facts["files"]}
    lectures = {x["ref"]: x for x in facts["lectures"]}
    out: dict[str, dict[str, Any]] = {}
    for c in outline["chapters"]:
        for g in c["figures"]:
            had = folder / f"{g['id']}.jpg"
            if keep and g["kind"] in ("slide", "frame") and had.is_file():
                out[g["id"]] = {"kind": g["kind"], "image": str(had), "caption": g["caption"],
                                "source": _source(facts, g)}
                continue
            try:
                if g["kind"] == "slide":
                    f = files[g["ref"]]
                    img = None
                    if f["path"] and Path(f["path"]).is_file():
                        cropped = folder / f"{g['id']}.jpg"
                        img = cropped if figure_image(Path(f["path"]), g["page"], cropped, FIGURE_WIDTH,
                                                      g.get("caption", "")) else \
                            page_image(Path(f["path"]), g["page"], width=FIGURE_WIDTH)
                elif g["kind"] == "frame":
                    x = lectures[g["ref"]]
                    part = next(p for p in x["parts"] if p["part"] == g["part"])
                    sl = [s for s in store.load_slides(part["delivery_id"]) if s.index == g["slide"]]
                    img = Path(sl[0].path) if sl and sl[0].path else None
                else:
                    continue
                if img and img.is_file():
                    folder.mkdir(parents=True, exist_ok=True)
                    dest = folder / f"{g['id']}.jpg"
                    if img != dest:                                     # (a cropped picture is made there)
                        shutil.copyfile(img, dest)
                    out[g["id"]] = {"kind": g["kind"], "image": str(dest), "caption": g["caption"],
                                    "source": _source(facts, g)}
            except Exception:  # noqa: BLE001 - a figure that can't be made is left out
                log.warning("Study pack figure %s could not be made", g["id"], exc_info=True)
    return out


def file_stem(plan: dict[str, Any], title: str, folder: Path, ends: tuple[str, ...],
              data: Optional[dict[str, Any]] = None) -> str:
    """A pack's or cheatsheet's file name without its ending: <date> - <title>, chosen once and kept in ``data``
    ("stem"), with " (2)", " (3)"… when another one's files already have that name. Without ``data`` (the app's page)
    the name kept, else (made before it was kept) <date> - <title>."""
    from .outputs.writer import safe_name

    kept = (data if data is not None else plan.get("data") or {}).get("stem")
    if kept:
        return kept
    stamp = (plan.get("created_at") or dt.datetime.now().isoformat())[:10]
    stem = base = safe_name(f"{stamp} - {title}", 110)
    if data is not None:
        n = 1
        while any((folder / f"{stem}{e}").exists() for e in ends):
            n += 1
            stem = f"{base} ({n})"
        data["stem"] = stem
    return stem


def files_for(s: Any, plan: dict[str, Any], title: str,
              data: Optional[dict[str, Any]] = None) -> tuple[Path, Path, Path]:
    """Where a pack's files go: <course>/Study packs/<date> - <title>.html / .pdf and a folder of its images (see
    file_stem: with ``data``, a name no other pack has, kept there)."""
    from .outputs.writer import course_dir

    course = s.lib.get_course(plan["course_id"])
    folder = course_dir(s.output_root, course, s.lib.courses(True)) / "Study packs"
    stem = file_stem(plan, title, folder, (".html", ".pdf", " (images)"), data)
    return folder / f"{stem}.html", folder / f"{stem}.pdf", folder / f"{stem} (images)"


def assemble(s: Any, plan: dict[str, Any], outline: dict[str, Any], facts: dict[str, Any], sections: dict[str, str],
             drawn: dict[str, dict[str, Any]], data: Optional[dict[str, Any]] = None, made: Optional[dt.date] = None,
             keep_images: bool = False) -> dict[str, Any]:
    """Write the document and its PDF; the result the app shows. ``made``: the day it says it was made (today);
    ``keep_images``: the slide pictures already beside it are used (relayout)."""
    made = made or dt.date.today()
    html_path, pdf_path, img_dir = files_for(s, plan, outline["title"], data)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    figures = {**_pictures(s.lib, plan, facts, outline, img_dir, keep_images), **drawn}
    meta = meta_of(facts, made)
    rel = lambda p: quote(f"{img_dir.name}/{Path(p).name}")   # noqa: E731 - next to the HTML file ("#" in a title)
    titles = [c["title"] for c in outline["chapters"]]
    body = document_body(outline, sections, figures, meta, rel)
    _write(html_path, document_html(body, outline["title"], html_path.parent))
    pdf_ok = False
    try:
        if _print_to(html_path, pdf_path):
            pdf_ok = True
            pages = chapter_pages(pdf_path, titles)                  # the contents get their page numbers
            if pages:
                _write(html_path, document_html(document_body(
                    outline, sections, figures, meta, rel,
                    {c["id"]: pages[i] for i, c in enumerate(outline["chapters"]) if i in pages}), outline["title"], html_path.parent))
                _print_to(html_path, pdf_path)
    except Exception:  # noqa: BLE001 - the pack is still shown in the app
        log.warning("The study pack's PDF could not be made", exc_info=True)
    return {"kind": "pack", "title": outline["title"], "file": str(html_path), "pdf": str(pdf_path) if pdf_ok else "",
            "layout": layout(), "made": made.isoformat(),
            "figures": sum(1 for c in outline["chapters"] for g in c["figures"] if g["id"] in figures),
            "chapters": [{"id": c["id"], "title": c["title"]} for c in outline["chapters"]],
            "concepts": len(outline.get("concepts") or []), "uncovered": outline.get("uncovered") or [],
            "files": len(facts["files"]), "lectures": len(facts["lectures"]), "cut": facts.get("cut") or [],
            "files_cut": facts.get("files_cut") or [],
            "materials_missing": facts["materials_missing"], "days_missing": facts["days_missing"]}


def meta_of(facts: dict[str, Any], made: Optional[dt.date] = None) -> dict[str, Any]:
    made = made or dt.date.today()
    return {"course": facts["course"]["code"], "span": facts["span"].capitalize(), "files": facts["files"],
            "lectures": facts["lectures"], "date": f"{made.day} {made:%b %Y}"}


def made_on(plan: dict[str, Any]) -> dt.date:
    """The day a finished pack or sheet was made (its documents say so): kept in its result, else (made before 4.0)
    the day it was started."""
    for v in ((plan.get("result") or {}).get("made"), plan.get("created_at")):
        try:
            return dt.date.fromisoformat(str(v or "")[:10])
        except ValueError:
            continue
    return dt.date.today()


def _write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(text.encode("utf-8"))
    tmp.replace(path)


def _print_to(html_path: Path, pdf_path: Path) -> bool:
    """``html_path`` printed to ``pdf_path`` through a file beside it: the PDF there is replaced only by a whole new
    one (a print that fails, or an earlier PDF open in a viewer, leaves it as it was)."""
    part = pdf_path.with_name(pdf_path.name + ".part")
    try:
        if not print_pdf(html_path, part):
            return False
        os.replace(part, pdf_path)
        return True
    except OSError:
        log.warning("The PDF %s could not be replaced (open in another program?)", pdf_path.name, exc_info=True)
        return False
    finally:
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass


def shown_body(s: Any, plan: dict[str, Any]) -> str:
    """The document's body for the app's page: the same HTML as the file, its images by their full file:// address.
    A cheatsheet's (cheatsheet.py) too: the API shows both."""
    if (plan.get("scope") or {}).get("kind") == "sheet":
        from .cheatsheet import shown_body as sheet_body

        return sheet_body(s, plan)
    data = plan.get("data") or {}
    outline, facts = data.get("outline"), data.get("gather")
    if not outline or not facts:
        return ""
    _, _, img_dir = files_for(s, plan, outline["title"])
    figures = dict(data.get("figures") or {})
    for c in outline["chapters"]:
        for g in c["figures"]:
            p = img_dir / f"{g['id']}.jpg"
            if g["kind"] != "diagram" and p.is_file():
                figures[g["id"]] = {"kind": g["kind"], "image": str(p), "caption": g["caption"],
                                    "source": _source(facts, g)}
    return document_body(outline, data.get("sections") or {}, figures, meta_of(facts, made_on(plan)),
                         lambda p: Path(p).as_uri())


def _source(facts: dict[str, Any], g: dict[str, Any]) -> str:
    """Where a figure comes from, under its caption: "Week 6.pdf, page 4", "lecture of 28 Oct 2026, part 1, slide 7"."""
    if g["kind"] == "slide":
        f = next((f for f in facts["files"] if f["ref"] == g["ref"]), None)
        return f"{f['name']}, page {g['page']}" if f else ""
    x = next((x for x in facts["lectures"] if x["ref"] == g["ref"]), None)
    if not x:
        return ""
    try:
        day = f"{dt.date.fromisoformat(str(x['date'])[:10]):%d %b %Y}".lstrip("0")
    except ValueError:
        day = x["date"]
    return f"lecture of {day}, part {g['part']}, slide {g['slide']}"


def relayout(s: Any, plan: dict[str, Any]) -> Optional[dict[str, Any]]:
    """A finished pack or cheatsheet laid out again from its saved data, no AI: its HTML and PDF written again under
    their names, with the day it was made (a sheet made before 3.2.2 printed its symbols' meanings over the next
    column, a pack before 4.0 cut a formula wider than the page). Its new result (saved), or None when its data can't
    make it: its files stay as they were. A PDF that couldn't be printed again (Edge busy, the old one open in a
    viewer) stays too, and it is tried again the next time."""
    r = plan.get("result") or {}
    data = dict(plan.get("data") or {})
    if not data.get("stem") and r.get("file"):                    # (kept since 3.0: the same file names)
        data["stem"] = Path(r["file"]).stem
    made = made_on(plan)
    try:
        if r.get("kind") == "sheet":
            from . import cheatsheet

            if not data.get("topics") or not data.get("gather"):
                return None
            new = cheatsheet.assemble(s, plan, data["topics"], data["gather"], cheatsheet._all_entries(data),
                                      data.get("checks") or {}, data, made=made)
        else:
            if not data.get("outline") or not data.get("gather") or not data.get("sections"):
                return None
            new = assemble(s, plan, data["outline"], data["gather"], data["sections"], data.get("figures") or {}, data,
                           made=made, keep_images=True)
    except Exception:  # noqa: BLE001 - its files stay as they were
        log.warning("The %s %s could not be laid out again", r.get("kind") or "pack", plan.get("id"), exc_info=True)
        return None
    if not new["pdf"]:
        old = r.get("pdf") or ""
        new = {**new, "pdf": old if old and Path(old).is_file() else "", "layout": r.get("layout", "")}
        if "pages" in r:
            new["pages"] = r["pages"]
    result = {**r, **new}
    s.lib.update_plan(plan["id"], result=result)
    return result


def _laid_out(r: dict[str, Any]) -> bool:
    """Are this finished pack's (or sheet's) files there, laid out the way the app lays them out now?"""
    if r.get("kind") == "sheet":
        from .cheatsheet import layout as now
    else:
        now = layout
    have = (r.get("pdf") and Path(r["pdf"]).is_file()) or (r.get("file") and Path(r["file"]).is_file())
    return bool(have) and r.get("layout") == now()


def make_pdf(s: Any, plan: dict[str, Any]) -> Optional[Path]:
    """The pack's (or cheatsheet's) PDF, printed again when it is missing (deleted, or Edge was busy when it was
    made); one made with an older layout, or with its files gone, is laid out again first (relayout)."""
    r = plan.get("result") or {}
    if r.get("kind") == "prep":                     # an exam review sheet (plans.py): printed from its Markdown
        from .plans import prep_pdf

        return prep_pdf(s, plan)
    if r.get("kind") in ("pack", "sheet") and not _laid_out(r):
        r = relayout(s, plan) or r
    if r.get("pdf") and Path(r["pdf"]).is_file():
        return Path(r["pdf"])
    html_path = Path(r.get("file") or "")
    if not html_path.is_file():
        return None
    pdf = html_path.with_suffix(".pdf")
    if not print_pdf(html_path, pdf):
        return None
    s.lib.update_plan(plan["id"], result={**r, "pdf": str(pdf)})
    return pdf


# ------------------------------------------------------------------ what the dialog offers, and the cost
def options(lib: Any, settings: Any, course_id: str) -> dict[str, Any]:
    """The weeks with their files and lectures, for picking the material."""
    from .views import course_detail

    d = course_detail(lib, settings, course_id)
    if d is None:
        raise KeyError(t("This course is no longer in your list."))
    weeks = []
    for w in d["weeks"]:
        files = [{"id": m["id"], "name": m["file_name"] or m["title"], "pages": m["pages"], "ext": m["ext"],
                  "ready": m["downloaded"] and m["text"] == "done", "downloaded": m["downloaded"]}
                 for u in w["units"] for m in u["materials"]]
        days = [{"day_key": x["day_key"], "date": x["date"], "label": x["label"], "kind": x["kind_label"],
                 "ready": x["any_done"], "mine": x.get("mine") is not False} for x in w["days"]]
        if files or days or w["topic_en"] or w["topic_tr"]:
            weeks.append({"no": w["no"], "start": w["start"], "current": w["current"], "past": w["past"],
                          "topic": w["topic_en"] or w["topic_tr"] or " · ".join(u["title"] for u in w["units"]),
                          "files": files, "days": days})
    return {"course": {"id": d["id"], "code": d["code"], "name": d["display_name"]}, "weeks": weeks}


def estimate(lib: Any, settings: Any, course_id: str, scope: Any) -> dict[str, Any]:
    """Roughly what a pack costs before it starts: tokens in and out, requests and time."""
    from .ai import IMAGE_TOKENS, PROMPT_OVERHEAD, chars_per_token
    from .plans import main_model

    per = chars_per_token()
    facts = gather(lib, settings, course_id, scope)
    full = facts["full_chars"]
    n = chapter_count(full)                        # (the outline makes n to n + 2: chapters_range)
    section_in = min(SECTION_CHARS, max(6_000, int(full / n * 1.3)))
    figures = n                                                        # about one drawing request per chapter
    pictures = min(n * CHAPTER_PICTURES, sum(len(f.get("pictures") or []) for f in facts["files"]))
    tokens_in = (facts["chars"] / per + PROMPT_OVERHEAD + 1_200) + n * (section_in / per + PROMPT_OVERHEAD + 1_600) \
        + figures * (5_000 + PROMPT_OVERHEAD) + pictures * IMAGE_TOKENS          # (the pages with figures, seen)
    tokens_out = 3_500 + n * 5_500 + figures * 2_500
    a = 3 + round(n * 2.5 / SECTION_PARALLEL) + figures
    return {"main_model": main_model(scope, settings), "effort": _level(scope, settings),
            "main_input": int(tokens_in), "main_output": int(tokens_out), "requests": 1 + n + figures, "chapters": n, "files": len(facts["files"]), "lectures": len(facts["lectures"]),
            "materials_missing": facts["materials_missing"], "days_missing": facts["days_missing"],
            "files_cut": facts["files_cut"],
            "minutes": t("about {a}–{b} minutes", a=a, b=a * 2)}


def progress_info(data: dict[str, Any], kind: str = "pack") -> dict[str, Any]:
    """For the plan page while a pack is being made: the stages done and the chapters (written or not); a
    cheatsheet's (``kind`` "sheet") from cheatsheet.py."""
    if kind == "sheet":
        from .cheatsheet import progress_info as sheet_info

        return sheet_info(data)
    outline = data.get("outline") or {}
    written = data.get("sections") or {}
    chapters = outline.get("chapters", [])
    drawn = set(data.get("figures_done") or [])
    wrote = bool(chapters) and all(c["id"] in written for c in chapters)
    steps = {"gather": "gather" in data, "outline": bool(outline), "sections": wrote,
             "figures": wrote and all(c["id"] in drawn or not any(g["kind"] == "diagram" for g in c["figures"])
                                      for c in chapters)}
    return {"stages_done": [s for s, ok in steps.items() if ok],
            "chapters": [{"id": c["id"], "title": c["title"], "done": c["id"] in written} for c in chapters]}
