"""What Claude should know about a course besides the lectures it reads: the learning outcomes, the weekly plan, the
assessments the lecturer announced and the latest announcements (``course_brief``, a few thousand characters, part of
a chat's fixed context), and, per question, the passages of the course files (slides, PDFs) and of other lectures
that match it (``retrieve``).
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Iterable

from .alerts import _tidy

BRIEF_CHARS = 7_000
EXCERPT_CHARS = 900


def course_brief(lib: Any, settings: Any, course_id: str, max_chars: int = BRIEF_CHARS) -> str:
    """Learning outcomes, weekly plan, announced assessments and recent announcements of a course, as plain text."""
    from .views import course_detail

    d = course_detail(lib, settings, course_id)
    if d is None:
        return ""
    out = [f"Course: {d['code']} — {d['syllabus'].get('name') or d['display_name']}"
           + (f" · lecturer {d['instructor']}" if d.get("instructor") else "") + (f" · {d['term']}" if d.get("term") else "")]
    if d["outcomes_en"]:
        out.append("Learning outcomes (English syllabus):")
        out += [f"  LO{i}: {t}" for i, t in enumerate(d["outcomes_en"], 1)]
    if d["outcomes_tr"] and d["outcomes_tr"] != d["outcomes_en"]:
        out.append("Learning outcomes on IEULearn (Turkish; numbering may differ):")
        out += [f"  LO{i}: {t}" for i, t in enumerate(d["outcomes_tr"], 1)]
    if d["grading"]:
        parts = []
        for g in d["grading"]:
            times = f" ×{g['number']}" if (g.get("number") or 1) > 1 else ""
            parts.append(f"{g['activity']}{times} {float(g['weight']):g}%")
        out.append("Grading: " + ", ".join(parts))
    weeks = [w for w in d["weeks"] if w["topic_en"] or w["topic_tr"] or w["units"]]
    if weeks:
        today = dt.date.today().isoformat()
        out.append(f"Weekly plan (today is {today}):")
        for w in weeks:
            topic = " / ".join(t for t in (w["topic_en"], w["topic_tr"]) if t) or ", ".join(u["title"] for u in w["units"])
            when = f" ({w['start']} to {w['end']})" if w["start"] else ""
            out.append(f"  Week {w['no']}{when}{' ← this week' if w['current'] else ''}: {topic}")
    plan = (d.get("alerts") or {}).get("plan") or []
    if plan:
        out.append("Assessments the lecturer announced:")
        out += [f"  {e['name']}: week {e['week'] or '?'}" + (f", covers LO{', LO'.join(map(str, e['los']))}" if e["los"] else "")
                for e in plan]
    anns = d.get("announcements") or []
    if anns:
        out.append("Latest announcements (newest first):")
        for a in anns[:6]:
            body = " ".join(_tidy(a.get("body") or "").split())
            out.append(f"  [{(a.get('posted_at') or '')[:10]}] {a['title']}: {body[:500]}" + ("…" if len(body) > 500 else ""))
    text = "\n".join(out)
    return text if len(text) <= max_chars else text[:max_chars].rsplit("\n", 1)[0] + "\n  …"


def retrieve(lib: Any, course_id: str, question: str, skip_days: Iterable[str] = (), max_chars: int = 7_000,
             limit: int = 6) -> tuple[str, list[dict[str, Any]]]:
    """Passages of the course's files and of lectures NOT already in the chat that match the question: the text to
    put before the question, and the sources (for the UI). Empty when nothing matches.

    They are found as the search page finds things (search.py): whole words in any of their forms, numbers however
    they are written, words of the same meaning in both languages. Pages and lecture paragraphs with more of the
    question's words come first; at most two passages of one class day."""
    from .outputs.common import format_ts
    from .search import passages, snippet

    found = passages(lib, question, course_id)
    skip = set(skip_days)
    parts, sources, size = [], [], 0
    for h, text, _ in [x for x in found if x[0].kind == "file" and x[0].field == "body"][:limit]:
        m = lib.get_material(h.ref)
        if not m:
            continue
        piece = f"[{m['file_name'] or m['title']} p.{h.sub}] {snippet(text, h.occ, EXCERPT_CHARS)['text']}"
        if size + len(piece) > max_chars:
            break
        parts.append(piece)
        size += len(piece)
        sources.append({"kind": "file", "resource_id": m["resource_id"], "title": m["title"], "page": int(h.sub)})
    recs: dict[str, Any] = {}
    seen_days: dict[str, int] = {}
    for h, text, _ in (x for x in found if x[0].kind == "lecture"):
        if h.ref not in recs:
            recs[h.ref] = lib.get_recording(h.ref)
        rec = recs[h.ref]
        if rec is None or rec.day_key in skip or seen_days.get(rec.day_key, 0) >= 2:
            continue
        seen_days[rec.day_key] = seen_days.get(rec.day_key, 0) + 1
        at = float(h.sub or 0)
        label = f"[lecture {rec.start:%a %d %b} {rec.start:%H:%M} recording, {format_ts(at)}]"
        piece = f"{label} {snippet(text, h.occ, 600)['text']}"
        if size + len(piece) > max_chars:
            break
        parts.append(piece)
        size += len(piece)
        sources.append({"kind": "lecture", "day_key": rec.day_key, "delivery_id": rec.delivery_id, "t": at})
        if len(sources) >= limit * 2:
            break
    if not parts:
        return "", []
    text = ("Passages from the course files (slides, PDFs) and from lectures not loaded above that match my question "
            "(found by keyword search, so some may be irrelevant; cite the ones you use as given):\n\n" + "\n\n".join(parts))
    return text, sources
