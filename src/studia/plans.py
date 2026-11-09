"""Video study plans. For a course and a scope (weeks, an upcoming quiz, extra topics, what the student finds hard),
find YouTube videos that really teach what the course material asks for, verify them through their captions (not their
titles), and build an ordered viewing plan with a concept checklist, a coverage table, written supplements for the gaps
and practice drawn from the course material.

Stages (each one's output is saved, so an interrupted plan resumes where it stopped):
  gather   - the course material for the scope: outcomes, weekly topics, slides/PDF text, lectures (no AI)
  concepts - every topic of the weeks as one concept each, with the skills it takes (including the cases the
             material practises), whether the focus (a quiz's outcomes) needs it, YouTube queries, caption terms
             and practice from the material (AI)
  search   - YouTube search per concept (yt-dlp)
  inspect  - captions and chapters of the best candidates (yt-dlp)
  listen   - the candidates whose captions can't show what they teach (none, another language, automatic ones far
             too short or garbled) are listened to: their sound only is downloaded, transcribed on this PC and
             deleted (listen.py); the transcript is then read like captions
  verify   - which videos really teach each concept and which of its skills, where, with quotes from the captions
             and the mistakes they show (AI, light model)
  more     - every concept and skill no checked video teaches yet gets more searches (other words, Turkish, the
             missing skills themselves), more candidates and another check, until it is covered or nothing new is
             found; what stays without a good video is said, with why
  assemble - the plan within the time budget (AI); titles, channels and links come from the real data

The AI's answers are checked in code: quotes must be found in the captions, a skill counts for a video only with a
quote of it teaching that skill, "full" coverage needs every skill, topics the focus does not ask for are never
required, and the coverage and completeness statements are computed from the verified skills - not taken from the AI.
"""

from __future__ import annotations

import dataclasses as dc
import datetime as dt
import json
import logging
import math
import re
import threading
import time
import unicodedata
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterable, Optional

from . import store, ytprefs
from .cancel import Cancelled
from .i18n import N_, language, t, tn
from .notices import ALERTS_JSON, ALERTS_MD

log = logging.getLogger("studia")

STAGES = [                                           # labels in English: t() to show them
    ("gather", N_("Reading the course material")),
    ("concepts", N_("Working out what you need to learn")),
    ("search", N_("Searching YouTube")),
    ("inspect", N_("Reading the videos' captions")),
    ("listen", N_("Listening to videos without captions")),
    ("verify", N_("Checking which videos really teach each concept")),
    ("more", N_("Looking for videos for what isn't covered yet")),
    ("assemble", N_("Building your plan")),
]
STAGE_NAMES = [s for s, _ in STAGES]
PREP_STAGES = [("gather", N_("Reading the course material")), ("write", N_("Writing your exam review sheet"))]
LABELS = dict(STAGES + PREP_STAGES)
PACK_CHARS = 90_000           # course material sent for the concept list (~29k tokens)
MAT_CHARS = 9_000             # per file
DAY_CHARS = 12_000            # per lecture day at most
DAY_MIN = 1_500               # a lecture day that would get less is left out (and the plan says so)
CANDIDATES = 5                # kept per concept after the search
INSPECT = 3                   # inspected per concept the focus needs
INSPECT_EXTRA = 2             # inspected per extra concept (a topic of these weeks the focus does not ask for)
MAX_VIDEOS = 40               # inspected in total
CROSS = 2                     # long videos found for other concepts, also checked for a concept when they talk about it
CROSS_MIN_S = 15 * 60         # (lectures that teach several topics in one go)
CROSS_SCORE = 6.0             # how much they must talk about it (videos.windows score)
PREFERRED_SEARCHED = 3        # the student's preferred channels searched themselves for each topic
VERIFY_BATCH = 3              # concepts per verification request
VERIFY_PARALLEL = 3           # verification requests at once (each one waits ~1–2 min for the model)
EXCERPT_CHARS = 2_000         # caption text per part sent for verification (~2 minutes of speech)
MAX_CONCEPTS = 28
VERIFY_MODEL = "haiku"
PROOF_WORDS = 5               # a skill's quote shorter than this proves little
# more: what no checked video teaches yet gets more searches and candidates, round after round, until it is covered or
# a round finds nothing new to check
MORE_ROUNDS = 3
MORE_QUERIES = 4              # new searches the AI writes per concept (two a round, plus a Turkish one with both)
MORE_PER_CONCEPT = 3          # new candidates inspected per concept and round
MORE_CROSS = 2                # videos already read for other concepts, also checked for one when they talk about it
MORE_VIDEOS = 36              # inspected in all the rounds together
MORE_PAIRS = 12               # (concept, video) pairs per verification request (about a first check's size)
MORE_REQUESTS = 6             # verification requests in all the rounds together

SYSTEM = ("You are an expert university tutor who builds video study plans for an engineering student. You work only "
          "from the material you are given, you never invent facts about videos, and you answer with one JSON object "
          "in exactly the requested shape (no Markdown around it). " + ALERTS_JSON)


@dc.dataclass
class Scope:
    kind: str = "videos"         # "videos": a video study plan; "prep": an exam review sheet (no videos)
    weeks: list[int] = dc.field(default_factory=list)
    focus: str = ""              # an assessment from the course's plan, e.g. "Quiz 2"
    los: list[int] = dc.field(default_factory=list)   # learning outcomes to focus on
    topics: str = ""             # extra topics to recap
    difficulties: str = ""       # what the student finds hard
    minutes: int = 180           # viewing time budget
    deadline: str = ""           # YYYY-MM-DD
    allow_tr: bool = False       # Turkish videos too (older plans; now video_lang != "en")
    model: str = ""              # Claude model for the concept list and the plan ("" = the app's setting)
    effort: str = ""             # reasoning level for them, a review sheet, study pack or cheatsheet ("" = Settings')
    verify_effort: str = ""      # reasoning level for checking the videos ("" = Settings')
    extras: bool = False         # with a focus: also videos for the topics of these weeks the focus does not ask for
    video_lang: str = ""         # the videos' language: "en", "tr" or "both" (ytprefs.LANGS)
    files: list[str] = dc.field(default_factory=list)   # a study pack: the files picked (empty: all of the weeks)
    days: list[str] = dc.field(default_factory=list)    # and the lectures (day keys)
    picked: bool = False         # the chooser's ticks: then an empty files/days list means none of them

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "Scope":
        names = {f.name for f in dc.fields(cls)}
        s = cls(**{k: v for k, v in (d or {}).items() if k in names})
        s.weeks = sorted({int(w) for w in s.weeks or [] if str(w).isdigit()})
        s.los = sorted({int(n) for n in s.los or [] if str(n).isdigit()})
        s.minutes = max(15, min(1200, int(s.minutes or 180)))
        s.kind = s.kind if s.kind in ("videos", "prep", "pack", "sheet") else "videos"   # pack/sheet: studypack/cheatsheet.py
        if s.video_lang not in ("en", "tr", "both"):
            s.video_lang = "both" if s.allow_tr else "en"
        s.allow_tr = s.video_lang != "en"
        s.files = [str(x) for x in s.files or []]
        s.days = [str(x) for x in s.days or []]
        return s

    def to_json(self) -> dict[str, Any]:
        return dc.asdict(self)


def main_model(scope: "Scope", settings: Any) -> str:
    """The model for the concept list, the plan, review sheets and study packs: the plan's own, else the settings'."""
    return scope.model or getattr(settings, "plan_model", "") or getattr(settings, "ai_model", "") or "sonnet"


def model_shown(model: str, level: str = "") -> str:
    """Who does a step, as its progress text names it: Claude's model ("Sonnet · Low"), the ChatGPT model chosen in
    Settings with its reasoning level ("GPT-6.1-Sol · Extra high"), or the offline model - never a Claude model name
    while the AI is ChatGPT (``model`` is the app's Claude model level, which ChatGPT doesn't use)."""
    from .ai import _codex_model, provider
    from .effort import LABELS

    p = provider()
    lv = t(LABELS[level]) if level in LABELS else ""
    if p == "chatgpt":
        from . import appconfig
        from .chatgpt import _models_path

        try:
            rows = json.loads(_models_path().read_text(encoding="utf-8")).get("models") or []
        except (OSError, ValueError, AttributeError):
            rows = []
        chosen = appconfig.load().codex_model
        row = next((r for r in rows if isinstance(r, dict) and r.get("slug") == chosen), None) or \
            next((r for r in rows if isinstance(r, dict) and r.get("slug")), None)
        name = (row or {}).get("display_name") or (row or {}).get("slug") or "ChatGPT"
    elif p == "codex":
        name = _codex_model() or "ChatGPT"
    elif p == "local":
        return t("Offline model")
    else:
        name = (model or "sonnet").title()
    return f"{name} · {lv}" if lv else name


def levels(scope: "Scope", settings: Any) -> tuple[str, str]:
    """The reasoning levels (effort.py) of the plan or pack itself and of checking the videos: the plan's own, else
    (a ChatGPT plan from before) the one its model level meant, else the settings'."""
    from .effort import resolve

    return (resolve("plan", scope.effort, legacy=scope.model, settings=settings),
            resolve("verify", scope.verify_effort, settings=settings))


def _capital(s: str) -> str:
    """``s`` with a capital first letter (in Turkish, i → İ)."""
    first = "İ" if s[:1] == "i" and language() == "tr" else s[:1].upper()
    return first + s[1:]


def _week_span(weeks: list[int], shown: bool = False) -> str:
    """"week 3, weeks 5–7": in English for the AI and the exported files, ``shown`` in the language shown."""
    if not weeks:
        return t("all weeks so far") if shown else "all weeks so far"
    runs: list[list[int]] = []
    for w in weeks:
        if runs and w == runs[-1][1] + 1:
            runs[-1][1] = w
        else:
            runs.append([w, w])
    if shown:
        return ", ".join(t("week {n}", n=a) if a == b else t("weeks {a}–{b}", a=a, b=b) for a, b in runs)
    return ", ".join(f"week {a}" if a == b else f"weeks {a}–{b}" for a, b in runs)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + " …(cut)"


# ------------------------------------------------------------------ gather (no AI)
_STARRED = re.compile(r"\[[\d:]+\] (?:\(slide \d+\) )?(?:\[\w+\] )?⭐ ")     # a compact_day line the lecturer emphasised


def _shares(budget: int, sizes: list[int]) -> list[int]:
    """``budget`` split as evenly as it goes: all of a text (at most DAY_CHARS) when that is less than an equal share,
    equal shares of the rest for the others."""
    out, left = [0] * len(sizes), budget
    order = sorted(range(len(sizes)), key=lambda i: sizes[i])
    for n, i in enumerate(order):
        out[i] = max(0, min(sizes[i], DAY_CHARS, left // (len(order) - n)))
        left -= out[i]
    return out


def _fit_days(sizes: list[int], heads: list[int], budget: int) -> tuple[list[int], dict[int, int]]:
    """Which lecture days go into the pack and the characters each gets: every day, when each gets at least DAY_MIN
    (or all of a shorter one); else the short days and as many of the others as fit, spread over the scope (the
    days left out are reported)."""
    def spread(days: list[int], n: int) -> list[int]:
        return [days[round(j * (len(days) - 1) / max(1, n - 1))] for j in range(n)]

    short = [i for i, size in enumerate(sizes) if size <= DAY_MIN]
    long = [i for i, size in enumerate(sizes) if size > DAY_MIN]
    tries = [short + spread(long, n) for n in range(len(long), -1, -1)]
    tries += [spread(list(range(len(sizes))), n) for n in range(len(sizes), 0, -1)]   # too many short ones
    for keep in tries:
        keep = sorted(set(keep))
        shares = _shares(budget - sum(heads[i] for i in keep), [sizes[i] for i in keep])
        if keep and all(s >= min(sizes[i], DAY_MIN) for i, s in zip(keep, shares)):
            return keep, dict(zip(keep, shares))
    return [], {}


def _excerpt(day: str, room: int) -> str:
    """A class day (``compact_day``'s text) in ``room`` characters: all of it when it fits, else its ⭐ paragraphs
    first, then paragraphs spread over all its parts - in the order they were said."""
    if len(day) <= room:
        return day
    lines = day.split("\n")
    note = "(Excerpts: the moments the lecturer emphasised (⭐) and passages from across the whole lecture.)"
    fixed, units = [0], []                       # the headings and blank lines; the paragraphs (with any run-on line)
    for i, line in enumerate(lines[1:], 1):
        if line.startswith("["):
            units.append([i])
        elif line.startswith("#") or not line.strip() or not units:
            fixed.append(i)
        else:
            units[-1].append(i)
    cost = [sum(len(lines[i]) + 1 for i in u) for u in units]
    size = sum(len(lines[i]) + 1 for i in fixed) + len(note) + 1
    taken: set[int] = set()

    def take(k: int) -> bool:
        nonlocal size
        if k in taken or size + cost[k] > room:
            return False
        taken.add(k)
        size += cost[k]
        return True

    for k, u in enumerate(units):
        if _STARRED.match(lines[u[0]]):
            take(k)
    for _ in range(4):                           # evenly spread picks; again into what is left
        rest = [k for k in range(len(units)) if k not in taken]
        n = min(len(rest), int((room - size) / (sum(cost[k] for k in rest) / len(rest)))) if rest else 0
        added = sum(take(rest[int((j + 0.5) * len(rest) / n)]) for j in range(n))
        if not added:
            break
    keep = set(fixed) | {i for k in taken for i in units[k]}
    body = [line for i, line in enumerate(lines) if i in keep]
    return "\n".join([body[0], note, *body[1:]])


def gather(lib: Any, settings: Any, course_id: str, scope: Scope) -> dict[str, Any]:
    """The course material for the scope as one text (for the concept list) and the facts the later stages use."""
    from .outputs.ai_copy import compact_day
    from .views import course_detail

    d = course_detail(lib, settings, course_id)
    if d is None:
        raise KeyError(t("This course is no longer in your list."))
    course = lib.get_course(course_id)
    weeks = [w for w in d["weeks"] if not scope.weeks or w["no"] in scope.weeks]
    if not scope.weeks:                                       # default: everything up to this week
        weeks = [w for w in weeks if w["past"] or w["current"]] or d["weeks"]
    plan = (d.get("alerts") or {}).get("plan") or []
    out = [f"COURSE: {d['code']} — {d['syllabus'].get('name') or d['display_name']}"]
    if d.get("objectives"):
        out.append(f"Course objectives: {d['objectives']}")
    if d["outcomes_en"]:
        out.append("LEARNING OUTCOMES (English syllabus numbering):")
        out += [f"  LO{i}: {lo}" for i, lo in enumerate(d["outcomes_en"], 1)]
    if d["outcomes_tr"] and d["outcomes_tr"] != d["outcomes_en"]:
        out.append("LEARNING OUTCOMES on IEULearn (Turkish; numbering may differ):")
        out += [f"  LO{i}: {lo}" for i, lo in enumerate(d["outcomes_tr"], 1)]
    if plan:
        out.append("ASSESSMENTS ANNOUNCED BY THE LECTURER:")
        out += [f"  {e['name']}: week {e['week'] or '?'}" + (f", covers LO{', LO'.join(map(str, e['los']))}" if e["los"] else "")
                for e in plan]
    out.append("WEEKS IN SCOPE:")
    for w in weeks:
        topic = " / ".join(x for x in (w["topic_en"], w["topic_tr"]) if x) or ", ".join(u["title"] for u in w["units"])
        out.append(f"  Week {w['no']} ({w['start']} to {w['end']}): {topic}" + (f" — readings: {w['readings']}" if w["readings"] else ""))
    keys = [a for a in (d.get("alerts") or {}).get("items", []) if a["category"] != "important"
            and any(w["start"] and w["start"] <= a["date"][:10] <= (w["end"] or w["start"]) for w in weeks)]
    if keys:
        out.append("WHAT THE LECTURER SAID ABOUT ASSESSMENTS IN THESE WEEKS:")
        out += [f"  [{a['category']}] {a['text'][:300]}" for a in keys[:12]]
    used, missing, budget = [], [], PACK_CHARS - sum(len(x) + 1 for x in out)
    mat_budget = int(budget * 0.62)
    seen: set[str] = set()                  # a unit for two weeks ("Hafta 6 ve 7") is in both: its files once
    for w in weeks:
        for u in w["units"]:
            for m in u["materials"]:
                if m["id"] in seen:
                    continue
                seen.add(m["id"])
                if not m["downloaded"] or m["text"] != "done":
                    missing.append({"id": m["id"], "title": m["title"], "week": w["no"]})
                    continue
                pages = lib.material_pages(m["id"])
                body, size = [], 0
                for n, page in enumerate(pages, 1):
                    page = " ".join(page.split())
                    if not page:
                        continue
                    piece = f"[p.{n}] {page}"
                    if size + len(piece) > min(MAT_CHARS, mat_budget):
                        body.append("…(rest of the file left out)")
                        break
                    body.append(piece)
                    size += len(piece)
                if body:
                    text = f"\n=== FILE (week {w['no']}): {m['file_name'] or m['title']} ===\n" + "\n".join(body)
                    out.append(text)
                    mat_budget -= len(text)
                    used.append({"id": m["id"], "title": m["title"], "week": w["no"]})
    done = lib.transcribed_ids()
    recs = lib.recordings(course_id)
    days, days_missing = [], []
    for w in weeks:
        for day in w["days"]:
            if not day["any_done"]:
                if day.get("mine") is not False:
                    days_missing.append({"day_key": day["day_key"], "date": day["date"], "week": w["no"]})
                continue
            day_recs = [r for r in recs if r.day_key == day["day_key"]]
            trans = store.load_transcripts([r.delivery_id for r in day_recs if r.delivery_id in done])
            full = compact_day(course, day_recs, trans)
            if full:
                days.append(({"day_key": day["day_key"], "date": day["date"], "week": w["no"]},
                             f"\n=== LECTURE (week {w['no']}) ===\n", full))
    # the lectures share what is left evenly, so a long scope (a final) reaches its last weeks too
    budget = PACK_CHARS - sum(len(x) + 1 for x in out)
    keep, rooms = _fit_days([len(full) for _, _, full in days], [len(head) + 1 for _, head, _ in days], budget)
    for i in keep:
        _, head, full = days[i]
        out.append(head + _excerpt(full, rooms[i]))
    pack = "\n".join(out)
    return {"pack": pack, "chars": len(pack), "materials_used": used, "materials_missing": missing,
            "days_used": [days[i][0] for i in keep], "days_missing": days_missing,
            "days_left_out": [d for i, (d, _, _) in enumerate(days) if i not in rooms],
            "course": {"id": course_id, "code": d["code"], "name": d["display_name"]},
            "weeks": [{"no": w["no"], "start": w["start"], "end": w["end"], "topic": w["topic_en"] or w["topic_tr"]}
                      for w in weeks],
            "los_en": d["outcomes_en"], "los_tr": d["outcomes_tr"], "assessments": plan}


def _scope_text(scope: Scope, facts: dict[str, Any]) -> str:
    parts = [f"Scope: {_week_span([w['no'] for w in facts['weeks']])}"]
    if scope.focus:
        parts.append(f"preparing for: {scope.focus}")
    if scope.los:
        parts.append("focus learning outcomes: " + ", ".join(f"LO{n}" for n in scope.los))
    if scope.topics.strip():
        parts.append(f"extra topics to recap: {scope.topics.strip()}")
    if scope.difficulties.strip():
        parts.append(f"the student finds these hard: {scope.difficulties.strip()}")
    return " · ".join(parts)


def focused(scope: Scope) -> bool:
    """Whether the plan prepares for something narrower than its weeks (a quiz, chosen outcomes)."""
    return bool(scope.focus or scope.los)


def target(scope: Scope, weeks: Optional[list[int]] = None, shown: bool = False) -> str:
    """What the plan is for, in words: "Quiz 2 (LO1, LO2)", "the focus outcomes (LO3)" or "weeks 4–5" (in English for
    the AI; ``shown``: in the language shown, for the student)."""
    los = ", ".join(f"LO{n}" for n in scope.los)
    if scope.focus:
        return scope.focus + (f" ({los})" if los else "")
    if los:
        return t("the focus outcomes ({los})", los=los) if shown else f"the focus outcomes ({los})"
    return _week_span(scope.weeks if weeks is None else weeks, shown)


def searched(c: dict[str, Any], scope: Scope) -> bool:
    """Whether a concept gets videos: everything the focus needs, the extras only when asked for."""
    return c.get("in_scope", True) or scope.extras or not focused(scope)


# ------------------------------------------------------------------ concepts (AI)
def query_count(scope: Scope, in_scope: bool = True) -> int:
    """How many of a concept's queries are searched: 2 (an explanation, a worked example) - with both languages
    also the Turkish one; an extra topic gets one (two with both languages)."""
    both = scope.video_lang == "both"
    return (3 if both else 2) if in_scope else (2 if both else 1)


def concept_queries(c: dict[str, Any], scope: Scope) -> list[str]:
    """The searches made for a concept (with both languages its last query is the Turkish one: always kept)."""
    qs, n = c.get("queries") or [], query_count(scope, c.get("in_scope", True))
    if scope.video_lang == "both" and len(qs) > n:
        return [*qs[:n - 1], qs[-1]]
    return qs[:n]


def concepts_prompt(scope: Scope, facts: dict[str, Any]) -> str:
    queries = {
        "en": 'queries (2 YouTube searches in English: one for a clear university-level explanation that names the '
              'subject, e.g. "linear algebra pivot columns free variables", and one for a worked example)',
        "tr": 'queries (2 YouTube searches in Turkish - the student wants Turkish videos: one for a clear '
              'university-level explanation that names the subject, e.g. "lineer cebir pivot sütunları serbest '
              'değişkenler", and one for a worked example, e.g. "... örnek soru çözümü")',
        "both": 'queries (3 YouTube searches - the student wants videos in English and in Turkish: in English one for a '
                'clear university-level explanation that names the subject, e.g. "linear algebra pivot columns free '
                'variables", and one for a worked example; then one in Turkish, e.g. "lineer cebir pivot sütunları '
                'örnek soru çözümü")'}[scope.video_lang]
    terms = ("in English" if scope.video_lang == "en" else
             "in English and the same in Turkish, as Turkish teachers say them (e.g. \"eigenvalue\", \"özdeğer\"), "
             "so they are found in Turkish captions too; up to 14 in all")
    if focused(scope):
        what = target(scope)
        serves = (f"the focus outcomes ({', '.join(f'LO{n}' for n in scope.los)})" if scope.los
                  else f"what {what} covers (see the assessments and what the lecturer said)")
        in_scope = (f"in_scope: true if {what} needs the concept — it serves {serves}, or the concepts that do clearly "
                    f"build on it; false for topics of these weeks that {what} does not ask for (they become optional "
                    f"extras). Decide from the outcomes and the lecturer's words, not from the week alone. An "
                    f"application of a method (e.g. network flow or circuits solved with linear systems) is in scope "
                    f"only when the outcomes or the lecturer name it.")
    else:
        in_scope = "in_scope: true for every concept (the student wants everything in these weeks)."
    return f"""TASK: List what the student must learn for this scope, as concepts to study with videos.
Rules:
- Go through the material of every week in scope and list EVERY topic it teaches, so that nothing is silently left out. Each concept is ONE teachable unit: one idea, one procedure or one application. Never lump topics together: e.g. "network flow", "electrical circuits", "migration models" and "Leontief models" are four concepts, not one "applications" concept. 6–{MAX_CONCEPTS} concepts (fewer for a single week), in the order they should be learned, prerequisites first.
- {in_scope}
- skills: 2–6 things the student must be able to DO for the concept, including the cases the material practises or tests that one basic example would miss (e.g. a rectangular matrix, a column without a pivot, an inconsistent system, a parameter that changes the number of solutions, the 2×2 shortcut as well as the general method). Short phrases.
- For each concept also: id ("c1", "c2", …), name (plain language), summary (what the student must be able to do; 1–2 sentences), weeks (week numbers), los (outcome numbers from the English list; [] if unclear), kind ("concept", "procedure" or "application"), importance ("core", "supporting" or "prerequisite" — prerequisite only if the material clearly assumes it), focus (true if it matches what the student finds hard), cues (2–4 ways an exam question signals that the concept is needed), terms (4–8 words or short phrases a teacher would SAY when explaining the concept and its skills, {terms}; used to find them in video captions), {queries}.
- practice: for every in-scope concept, one question DRAWN FROM THE COURSE MATERIAL (an example, exercise or slide), preferring its harder skills, with a short worked answer and its source (file and page, or lecture date). Maths in LaTeX between dollars, every backslash doubled as JSON needs (e.g. "$P(A\\\\mid B)=\\\\frac{{P(A\\\\cap B)}}{{P(B)}}$").
- gaps: what the scope needs but the material does not show ("" if nothing).
Return JSON: {{"concepts": [{{"id": "c1", "name": "", "summary": "", "weeks": [], "los": [], "kind": "", "importance": "", "in_scope": true, "focus": false, "skills": [], "cues": [], "terms": [], "queries": []}}], "practice": [{{"concept": "c1", "question": "", "answer": "", "source": ""}}], "gaps": ""}}

{_scope_text(scope, facts)}

=== COURSE MATERIAL ===
{facts['pack']}"""


def clean_concepts(raw: Any, scope: Optional[Scope] = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    """The concept list, checked: unique ids, known values, lists of strings - and the scope enforced: without a
    focus everything is in scope; with focus outcomes, a concept tied only to other outcomes is an extra (unless
    the focus builds on it)."""
    raw = raw if isinstance(raw, dict) else {}
    scope = scope or Scope()
    concepts, seen = [], set()
    for i, c in enumerate(raw.get("concepts") or [], 1):
        if not isinstance(c, dict) or not str(c.get("name") or "").strip():
            continue
        cid = str(c.get("id") or f"c{i}").strip() or f"c{i}"
        if cid in seen:
            cid = f"c{i}"
        seen.add(cid)
        lst = lambda k: [str(x).strip() for x in c.get(k) or [] if str(x).strip()]   # noqa: E731
        ints = lambda k: [int(x) for x in c.get(k) or [] if str(x).strip().isdigit()]   # noqa: E731
        importance = c.get("importance") if c.get("importance") in ("core", "supporting", "prerequisite") else "core"
        los = ints("los")
        in_scope = c.get("in_scope") is not False
        if not focused(scope):
            in_scope = True
        elif scope.los and los and not set(los) & set(scope.los) and importance != "prerequisite":
            in_scope = False
        concepts.append({"id": cid, "name": str(c["name"]).strip(), "summary": str(c.get("summary") or "").strip(),
                         "weeks": ints("weeks"), "los": los,
                         "kind": c.get("kind") if c.get("kind") in ("procedure", "application") else "concept",
                         "importance": importance, "in_scope": in_scope,
                         "focus": bool(c.get("focus")), "skills": list(dict.fromkeys(lst("skills")))[:6],
                         "cues": lst("cues")[:4], "terms": lst("terms")[:14],
                         "queries": lst("queries")[:3] or [str(c["name"]).strip()]})
    concepts = concepts[:MAX_CONCEPTS]
    if not concepts:
        raise RuntimeError(t("The AI found no concepts in the material for this scope."))
    if not any(c["in_scope"] for c in concepts):        # an outcome mix-up: better a broad plan than an empty one
        for c in concepts:
            c["in_scope"] = True
    ids = {c["id"] for c in concepts}
    practice = [{"concept": str(p.get("concept") or ""), "question": str(p.get("question") or "").strip(),
                 "answer": str(p.get("answer") or "").strip(), "source": str(p.get("source") or "").strip()}
                for p in raw.get("practice") or [] if isinstance(p, dict) and str(p.get("question") or "").strip()]
    practice = [p for p in practice if p["concept"] in ids or not p["concept"]]
    rank = {c["id"]: (not c["in_scope"], i) for i, c in enumerate(concepts)}
    practice.sort(key=lambda p: rank.get(p["concept"], (True, len(rank))))
    return concepts, practice, str(raw.get("gaps") or "").strip()


# ------------------------------------------------------------------ quotes: what the captions really say
_STAMP = re.compile(r"\[(\d+(?::\d{2}){1,2})\]")
_TOKEN = re.compile(r"\[(\d+(?::\d{2}){1,2})\]|[^\W_]+")      # a time mark, or a word
_SENTENCE = re.compile(r"(?<=[.!?;:])\s+")


def _secs(stamp: str) -> int:
    s = 0
    for part in stamp.split(":"):
        s = s * 60 + int(part)
    return s


def _norm(word: str) -> str:
    folded = unicodedata.normalize("NFKD", word.casefold())
    return "".join(ch for ch in folded if not unicodedata.combining(ch))


def _words(text: str) -> list[str]:
    return [_norm(m.group()) for m in _TOKEN.finditer(str(text or "")) if not m.group(1)]


def _run(q: list[str], part: dict[str, Any]) -> tuple[int, float, str]:
    """The longest run of the words ``q`` (in order) in a caption excerpt: (length, when it was said - from the
    excerpt's "[m:ss]" marks, before the first one its start -, the captions' own words for it)."""
    text = str(part.get("text") or "")
    when = float(part.get("start") or 0)
    words: list[tuple[str, float, int, int]] = []
    for m in _TOKEN.finditer(text):
        if m.group(1):
            when = float(_secs(m.group(1)))
        else:
            words.append((_norm(m.group()), when, m.start(), m.end()))
    best, end = 0, -1
    prev = [0] * (len(q) + 1)
    for i, (w, *_) in enumerate(words):
        cur = [0] * (len(q) + 1)
        for j, x in enumerate(q):
            if w == x:
                cur[j + 1] = prev[j] + 1
                if cur[j + 1] > best:
                    best, end = cur[j + 1], i
        prev = cur
    if not best:
        return 0, 0.0, ""
    first, last = words[end - best + 1], words[end]
    return best, first[1], _sentence(text, first[2], last[3])


def _sentence(text: str, a: int, b: int, before: int = 100, after: int = 140) -> str:
    """``text[a:b]`` widened to the sentence around it when its ends are near (the captions' own words for context),
    without the time marks."""
    seg = text[max(0, a - before):a]
    stops = list(re.finditer(r"[.!?\]]\s", seg))
    lo = max(0, a - before) + stops[-1].end() if stops else a
    m = re.search(r"[.!?](?=\s|$)", text[b:b + after])
    hi = b + m.end() if m else b
    return " ".join(_STAMP.sub(" ", text[lo:hi]).split())


def match_quote(quote: str, parts: list[dict[str, Any]]) -> Optional[tuple[float, str]]:
    """Where ``quote`` is in the caption excerpts ``parts``: (time, the captions' exact words) - or None when they
    don't contain it (most of its words in a row, case and punctuation aside), i.e. it is not a real quote. A quote
    stitched together from several sentences keeps its longest sentence that is really there."""
    tries = [quote, *(s for s in _SENTENCE.split(str(quote or "")) if s.strip() != str(quote).strip())]
    best: Optional[tuple[int, float, str]] = None
    for n, piece in enumerate(tries):
        q = _words(piece)
        if not q or (n and len(q) < 5):                 # a stray short sentence proves little
            continue
        need = len(q) if len(q) < 4 else max(4, math.ceil(0.6 * len(q)))
        for part in parts or []:
            hit = _run(q, part)
            if hit[0] >= need and (best is None or hit[0] > best[0]):
                best = hit
        if best and n == 0:
            break                                       # the whole quote is there
    return (best[1], best[2]) if best else None


def find_quote(quote: str, parts: list[dict[str, Any]]) -> Optional[float]:
    """When ``quote`` was said (see :func:`match_quote`), or None if the captions don't contain it."""
    m = match_quote(quote, parts)
    return m[0] if m else None


# ------------------------------------------------------------------ verify (Claude, light model)
def verify_prompt(batch: list[dict[str, Any]], videos: dict[str, dict[str, Any]], excerpts: dict[str, dict[str, list]]) -> str:
    from .videos import fmt_ts

    blocks = []
    for c in batch:
        lines = [f"CONCEPT {c['id']} — {c['name']}: {c['summary']} (terms: {', '.join(c['terms'])})"]
        if c.get("skills"):
            lines.append("  skills: " + "; ".join(f"{i}) {s}" for i, s in enumerate(c["skills"], 1)))
        for vid, wins in (excerpts.get(c["id"]) or {}).items():
            v = videos.get(vid) or {}
            chapters = " · ".join(f"{fmt_ts(ch['start'])} {ch['title']}" for ch in v.get("chapters", [])[:12])
            kind = ("transcribed from its sound by IEUStudia" if v.get("caption_kind") == "speech"
                    else v.get("caption_kind") or "none")
            lines.append(f"  VIDEO {vid} — \"{v.get('title', '')}\" by {v.get('channel', '')} · {fmt_ts(v.get('duration_s', 0))}"
                         f" · captions: {kind} {v.get('caption_lang', '')}")
            if chapters:
                lines.append(f"    chapters: {chapters}")
            for w in wins:
                lines.append(f"    caption excerpt {fmt_ts(w['start'])}–{fmt_ts(w['end'])}: \"{w['text']}\"")
        blocks.append("\n".join(lines))
    return f"""TASK: For each concept, judge each candidate YouTube video ONLY from the evidence given: its chapters and caption excerpts ("[m:ss]" marks the time; automatic captions and transcripts can contain recognition errors). Does the video actually TEACH the concept and its skills the way the course needs them?
For every (concept, video) pair return:
- coverage: "full" (teaches the concept and every listed skill), "partial" (teaches some of the skills, or only part of the concept), "none" (does not teach it, or only names it).
- skills: every listed skill the excerpts show it TEACHING or working through (not one it only names), each as its number n, the time t in seconds and a quote of 8–25 words copied word for word from the excerpt where it does that, e.g. {{"n": 2, "t": 312, "quote": "so to standardize it we subtract the mean and then divide by the standard deviation"}}. A skill without such a quote does not count.
- start, end: seconds of the part that teaches it (from the [m:ss] marks and the chapters).
- level ("intro", "course" or "advanced"); examples (true if it works through an example or problem); quality (1–5: clarity and correctness as far as the captions show).
- quotes: 1–2 short quotes copied word for word from the excerpts (at most 20 words each) that show the teaching of the concept, each with t, its time in seconds.
- errors: mistakes of the presenter that the captions show (a wrong sign, rule or definition, an arithmetic slip), each with t and what is wrong and the correct version, e.g. {{"t": 754, "text": "says det = ad + bc; it is ad − bc"}}. Recognition errors are NOT mistakes. Usually [].
- note: at most 8 words, e.g. "different notation", "square matrices only", "very fast"; usually "".
Reply with the JSON on a single line (no indentation, no code fence, nothing else): {{"verdicts": [{{"concept": "c1", "video": "VIDEO_ID", "coverage": "partial", "skills": [{{"n": 1, "t": 0, "quote": ""}}], "start": 0, "end": 0, "level": "course", "examples": true, "quality": 4, "quotes": [{{"t": 0, "text": ""}}], "errors": [], "note": ""}}]}}

""" + "\n\n".join(blocks)


def _skill_number(value: Any, count: int) -> int:
    """A skill's number in a verdict (1-based), or 0 when it isn't one of the ``count`` listed."""
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return 0
    return n if 1 <= n <= count else 0


def clean_verdicts(raw: Any, asked: dict[str, set[str]], videos: dict[str, dict[str, Any]],
                   concepts: Optional[list[dict[str, Any]]] = None,
                   excerpts: Optional[dict[str, dict[str, list]]] = None) -> dict[str, dict[str, dict]]:
    """The verdicts, checked: only the pairs asked about, parts within the video, "full" only with every skill, and
    only the quotes that really are in the captions shown (with their time from there). A skill counts as taught only
    with its own quote of the video teaching it (``proofs``: {skill: {"t", "text"}}, the captions' words); the skills
    the AI named without one are kept apart (``claimed``: all it named). A video the AI says teaches the concept but
    no quote of which is found in the captions doesn't count (coverage "none", ``unproven``)."""
    skills_of = {c["id"]: c.get("skills") or [] for c in concepts or []}
    out: dict[str, dict[str, dict]] = {}
    for v in (raw.get("verdicts") if isinstance(raw, dict) else None) or []:
        if not isinstance(v, dict):
            continue
        cid, vid = str(v.get("concept") or ""), str(v.get("video") or "")
        if vid not in asked.get(cid, set()):
            continue                                              # only pairs we asked about
        dur = float((videos.get(vid) or {}).get("duration_s") or 0)
        try:
            start, end = max(0.0, float(v.get("start") or 0)), float(v.get("end") or dur)
        except (TypeError, ValueError):
            start, end = 0.0, dur
        if dur:
            end = min(end, dur) if end > start else dur
        try:
            quality = max(1, min(5, int(v.get("quality") or 3)))
        except (TypeError, ValueError):
            quality = 3
        said = v.get("coverage") if v.get("coverage") in ("full", "partial", "none") else "none"
        coverage = said
        skills = skills_of.get(cid, [])
        parts = (excerpts or {}).get(cid, {}).get(vid) or []
        claimed: list[str] = []
        proofs: dict[str, dict[str, Any]] = {}
        nums = v.get("skills")
        for s in (nums if isinstance(nums, list) else list(range(1, len(skills) + 1)) if said == "full" else []):
            n = _skill_number(s.get("n") if isinstance(s, dict) else s, len(skills))
            if not n:
                continue
            skill = skills[n - 1]
            claimed += [skill] if skill not in claimed else []
            text = str((s.get("quote") or s.get("text") or "") if isinstance(s, dict) else "").strip().strip("\"“”'")
            hit = match_quote(text, parts) if len(_words(text)) >= PROOF_WORDS else None
            if hit and skill not in proofs:
                proofs[skill] = {"t": round(hit[0]), "text": hit[1][:240]}         # the captions' words
        taught = [s for s in skills if s in proofs]
        quotes = []
        for q in v.get("quotes") or []:
            text = str((q.get("text") if isinstance(q, dict) else q) or "").strip().strip("\"“”'")
            hit = match_quote(text, parts) if text else None
            if hit and all(abs(hit[0] - x["t"]) > 5 for x in quotes):
                quotes.append({"t": round(hit[0]), "text": hit[1][:240]})      # the captions' words, not the AI's
        unproven = said != "none" and not quotes and not proofs
        if said == "none" or unproven:
            coverage, taught = "none", []
        elif said == "full" and len(taught) < len(skills):
            coverage = "partial"                                  # "full" means every skill, each with its quote
        elif said == "partial" and skills and len(taught) == len(skills):
            coverage = "full"
        errors = []
        for e in v.get("errors") or []:
            text = str((e.get("text") if isinstance(e, dict) else e) or "").strip()
            if not text:
                continue
            try:
                at = max(0.0, float(e.get("t") or 0)) if isinstance(e, dict) else start
            except (TypeError, ValueError):
                at = start
            errors.append({"t": round(min(at, dur) if dur else at), "text": text[:240]})
        proofs = {s: p for s, p in proofs.items() if s in taught}
        evidence = quotes[0]["text"] if quotes else next(iter(proofs.values()), {}).get("text", "")
        out.setdefault(cid, {})[vid] = {
            "coverage": coverage, "skills": taught, "proofs": proofs, "claimed": claimed if said != "none" else [],
            "said": said, "unproven": unproven,
            "start": round(start), "end": round(end), "level": str(v.get("level") or "course"),
            "examples": bool(v.get("examples")), "quality": quality, "quotes": quotes[:2], "errors": errors[:3],
            "evidence": evidence, "note": str(v.get("note") or "")[:120]}
    return out


# ------------------------------------------------------------------ assemble (Claude)
def assemble_prompt(scope: Scope, facts: dict[str, Any], concepts: list[dict], candidates: dict[str, list[dict]],
                    previous: Optional[dict[str, Any]], today: dt.date, gaps: Optional[dict[str, list[str]]] = None,
                    why: Optional[dict[str, dict[str, str]]] = None) -> str:
    """``gaps``: what no checked video teaches, per concept (with ``why`` the search found none): the plan says so and
    covers it in a supplement."""
    from .videos import fmt_ts

    what = target(scope, [w["no"] for w in facts["weeks"]])
    lines, skipped = [], []
    gaps, why = gaps or {}, why or {}
    for c in concepts:
        if not searched(c, scope):
            skipped.append(c["name"])
            continue
        flags = ["IN SCOPE" if c.get("in_scope", True) else f"EXTRA, not on {what}", c["importance"]]
        flags += ["the student finds it hard"] if c["focus"] else []
        lines.append(f"{c['id']} [{' · '.join(flags)}] {c['name']} — {c['summary']}")
        skills = c.get("skills") or []
        if skills:
            lines.append("   skills: " + "; ".join(f"{i}) {s}" for i, s in enumerate(skills, 1)))
        for cand in candidates.get(c["id"], []):
            seen = [f"{_count(cand['views'])} views" if cand.get("views") else "",
                    f"{_count(cand['likes'])} likes" if cand.get("likes") is not None and cand.get("views") else "",
                    "a channel the student prefers" if cand.get("preferred") else ""]
            head = (f"   - {cand['video']} \"{cand['title']}\" ({', '.join(x for x in [cand['channel'], fmt_ts(cand['duration_s']), *seen] if x)}): ")
            if cand["state"] != "verified":
                lines.append(head + ("UNVERIFIED (not checked)" if cand.get("unread") else "UNVERIFIED (no captions)"))
                continue
            nums = [str(skills.index(s) + 1) for s in cand.get("skills") or [] if s in skills]
            taught = (f", teaches skills {', '.join(nums)}" if nums else ", teaches none of the skills") if skills else ""
            lines.append(head + f"{cand['coverage']} at {fmt_ts(cand['start'])}–{fmt_ts(cand['end'])} ({cand['minutes']} min)"
                         f"{taught}, level {cand['level']}, quality {cand['quality']}/5{', worked examples' if cand['examples'] else ''}"
                         + "".join(f"; MISTAKE at {fmt_ts(e['t'])}: {e['text']}" for e in cand.get("errors") or [])
                         + (f"; {cand['note']}" if cand["note"] else ""))
        if c["id"] in gaps:
            missing = gaps[c["id"]]
            lines.append("   NO CHECKED VIDEO teaches " + (f"skills {', '.join(str(skills.index(s) + 1) for s in missing if s in skills)}"
                                                           if missing else "this concept")
                         + (f" ({why[c['id']]['en']})" if (why.get(c["id"]) or {}).get("en") else "")
                         + ": say so in the summary and cover it in a supplement.")
    if skipped:
        lines.append(f"(Also in these weeks but not on {what}, so no videos were searched: {'; '.join(skipped)}.)")
    prev = ""
    if previous and previous.get("items"):
        prev_lines = [f"  {it['n']}. {it['video']} \"{it.get('title', '')}\" {fmt_ts(it.get('start', 0))}–{fmt_ts(it.get('end', 0))} "
                      f"[{it.get('role')}] for {', '.join(it.get('concepts', []))}" for it in previous["items"]]
        prev = ("\nThis is an UPDATE of the previous plan below: keep the items that still fit, replace weaker ones, and list "
                "the changes in changes.added (new item numbers), changes.replaced ([{\"old\": \"old title\", \"new\": n, "
                "\"why\": \"\"}]) and changes.removed ([{\"old\": \"old title\", \"why\": \"\"}]).\nPREVIOUS PLAN:\n"
                + "\n".join(prev_lines))
    deadline = f"Deadline: {scope.deadline} (today is {today.isoformat()})." if scope.deadline else f"Today is {today.isoformat()}."
    return f"""TASK: Build the final video study plan from the verified candidates.
Rules:
- Required and prerequisite items are only for IN SCOPE concepts. For every in-scope concept choose items that TOGETHER teach all its skills (check each candidate's skill numbers): prefer one strong video; combine videos or parts when one does not teach everything. Skills that no candidate teaches go into a supplement.
- EXTRA concepts are not on {what}: at most one short "optional" item each, and only while the required items leave time in the budget.
- An item is one video. "segments" lists the parts to watch ([[start, end], …] in seconds; several parts of one video are fine), using the verified parts; a whole video only if most of it is relevant.
- Time budget: {scope.minutes} minutes for the required and prerequisite items together. If it is not enough, keep the most important in-scope items required, make the rest optional, and say so in the summary.
- Prefer candidates without a MISTAKE. If you use one with a MISTAKE, say it in limits and correct it in a supplement.
- Between candidates that teach equally well, prefer the student's preferred channels and the better received videos (more likes per view; many views alone do not make a video good).
- Order: prerequisites first, then the course order. Number the items 1, 2, 3…
- role: "required", "prerequisite", "optional" (useful, not needed) or "alternative" (instead of another item: set alternative_to to that item's number).
- why: what the student gets from the item (at most 20 words). limits: what it leaves out or does differently from the course (at most 15 words; "" if nothing).
- summary: 2–3 honest sentences: what the plan covers, what is left to the supplements and what it does not cover. Never claim more than the verified skills show.
- supplements: for every in-scope concept with skills that no chosen item teaches, a short correct explanation of exactly those skills with a small worked example (Markdown; maths in LaTeX, $...$ in a sentence and $$...$$ on lines of their own, every backslash doubled as JSON needs); also the corrections of mistakes in chosen videos.
- checklist: for every in-scope concept, a plain-language explanation (at most 2 sentences) and how to recognise a question that needs it.
- notes: per concept, what the student should know about how it is covered (at most 12 words), only when useful.
- schedule: split the items over the days until the deadline (or over about a week), each day at most ~60 minutes, required items first.
- Write in English, concisely. Use only video ids from the list.{prev}
Return JSON: {{"title": "", "summary": "", "items": [{{"n": 1, "video": "VIDEO_ID", "segments": [[0, 0]], "role": "required", "alternative_to": null, "concepts": ["c1"], "why": "", "limits": ""}}], "schedule": [{{"day": "Mon 26 Oct", "items": [1, 2]}}], "notes": [{{"concept": "c1", "note": ""}}], "checklist": [{{"concept": "c1", "explain": "", "recognize": [""]}}], "supplements": [{{"concept": "c3", "text": ""}}], "changes": {{"added": [], "replaced": [], "removed": []}}}}

{_scope_text(scope, facts)}. {deadline} Course: {facts['course']['code']} — {facts['course']['name']}.
CONCEPTS AND CANDIDATE VIDEOS:
""" + "\n".join(lines)


def _count(n: Any) -> str:
    """1234 -> "1.2k", 1_731_857 -> "1.7M" (for the AI)."""
    n = int(n or 0)
    for size, unit in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if n >= size:
            return f"{n / size:.1f}".rstrip("0").rstrip(".") + unit
    return str(n)


def _video_ref(value: Any, videos: dict[str, dict]) -> str:
    """The video an item means: its id - also when the AI wrote a link or put words around it."""
    text = str(value or "").strip()
    if text in videos:
        return text
    return next((w for w in re.findall(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{11}(?![A-Za-z0-9_-])", text) if w in videos),
                text)


def _chapter_for(v: dict[str, Any], terms: list[str]) -> Optional[dict[str, Any]]:
    """The chapter whose title names the most of ``terms`` (None when none does): where to open an unchecked video."""
    from .videos import _terms, _tokens

    tl = _terms(terms)
    best, top = None, 0.0
    for ch in v.get("chapters") or []:
        title = " ".join(_tokens(str(ch.get("title") or "")))
        score = sum(term.weight for term in tl if term.rx.search(title))
        if score > top:
            best, top = ch, score
    return best


def _segments(it: dict[str, Any], dur: float) -> list[list[float]]:
    """An item's parts to watch, within the video, with a little lead-in, sorted and merged (gaps under 30 s)."""
    raw = it.get("segments")
    if not isinstance(raw, list) or not raw:
        raw = [[it.get("start"), it.get("end")]]
    out = []
    for s in raw:
        if isinstance(s, dict):
            a, b = s.get("start"), s.get("end")
        elif isinstance(s, (list, tuple)) and len(s) >= 2:
            a, b = s[0], s[1]
        else:
            continue
        try:
            a = max(0.0, float(a or 0))
            b = float(b) if b not in (None, "") else dur
        except (TypeError, ValueError):
            continue
        if dur and (b <= a or b > dur):
            b = dur
        if b <= a:
            continue
        out.append([max(0.0, a - 20) if a > 40 else 0.0, b])          # a little lead-in
    if not out:
        return [[0.0, dur]]
    out.sort()
    merged = [out[0]]
    for a, b in out[1:]:
        if a <= merged[-1][1] + 30:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return merged


def _overlaps(segs: list[list[float]], start: float, end: float) -> bool:
    return any(a < end and start < b for a, b in segs)


def _listed(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def listened_summary(vids: dict[str, dict], used: dict[str, Any]) -> dict[str, Any]:
    """What the plan listened to: {"heard": how many videos, "silent": how many more it listened to and heard no
    speech in, "seconds": how long it took, "skipped": [{"n", "why", "why_en"}] - the videos without usable captions
    it didn't listen to, by reason}."""
    from .listen import reason_text

    skipped: dict[str, int] = {}
    for v in vids.values():
        code = (v.get("listen") or {}).get("skipped")
        if code and code != "no_speech":
            skipped[code] = skipped.get(code, 0) + 1
    return {"heard": sum(1 for v in vids.values() if v.get("caption_kind") == "speech"),
            "silent": sum(1 for v in vids.values() if (v.get("listen") or {}).get("skipped") == "no_speech"),
            "seconds": round(float(used.get("wall_s") or 0)),
            "skipped": [{"n": n, "why": reason_text(code), "why_en": reason_text(code, True)}
                        for code, n in sorted(skipped.items(), key=lambda x: -x[1])]}


def unheard_text(v: dict[str, Any], english: bool = False) -> str:
    """How a video without usable captions was judged - by its title, description and chapters only -, with why
    IEUStudia didn't listen to it when it tried (``v``: the plan's row of the video, or an item's ``not_heard``)."""
    from .listen import reason_text

    code = str(v.get("not_heard") or (v.get("listen") or {}).get("skipped") or "")
    if not code:
        say = N_("title, description and chapters only (no captions available)")
        return say if english else t(say)
    if code == "no_speech":                  # (listened to: there was nothing to hear)
        say = N_("title, description and chapters only: it has no usable captions, and IEUStudia heard no speech in "
                 "it when it listened")
        return say if english else t(say)
    say = N_("title, description and chapters only: it has no usable captions, and IEUStudia didn't listen to it "
             "because {why}")
    return say.format(why=reason_text(code, True)) if english else t(say, why=reason_text(code))


def finalize(raw: Any, concepts: list[dict], candidates: dict[str, list[dict]], videos: dict[str, dict],
             practice: list[dict], gaps: str, *, verdicts: Optional[dict[str, dict[str, dict]]] = None,
             excerpts: Optional[dict[str, dict[str, list]]] = None, scope: Optional[Scope] = None,
             goal: str = "", goal_en: str = "",
             blocked: Optional[Callable[[str, str], bool]] = None,
             why: Optional[dict[str, dict[str, str]]] = None) -> dict[str, Any]:
    """The AI's plan, checked against the real data: only known videos (none of a ``blocked`` channel), parts within
    the video, exact titles, channels, views, likes and links, the viewing time recomputed - and what each item
    teaches, the coverage of every concept and skill and how complete the plan is, all from the verdicts (the AI's own
    claims about coverage are not used). A video whose captions weren't checked opens at the chapter about it.
    ``why``: for the concepts the search found no good video for, why ({concept: {"text", "en"}}, see why_no_video)."""
    from .videos import fmt_ts, watch_url

    raw = raw if isinstance(raw, dict) else {}
    scope = scope or Scope()
    by_id = {c["id"]: c for c in concepts}
    verdict = {(cand["video"], cid): cand for cid, cands in candidates.items() for cand in cands if cand.get("state") == "verified"}
    for cid, per in (verdicts or {}).items():
        for vid, vd in per.items():
            verdict.setdefault((vid, cid), vd)
    items, by_n = [], {}
    for it in raw.get("items") or []:
        if not isinstance(it, dict):
            continue
        vid = _video_ref(it.get("video"), videos)
        v = videos.get(vid)
        if v is None or v.get("error") or v.get("wrong_lang"):
            continue
        if blocked is not None and blocked(v.get("channel_id", ""), v.get("channel", "")):
            continue
        dur = float(v.get("duration_s") or 0)
        listed = [c for c in dict.fromkeys(str(x) for x in it.get("concepts") or []) if c in by_id]
        concepts_of = [c for c in listed if (verdict.get((vid, c)) or {}).get("coverage") != "none"]
        if listed and not concepts_of:
            continue                                  # checked: it teaches none of them
        segs = _segments(it, dur)
        teaches = []
        for c in concepts_of:
            vd = verdict.get((vid, c))
            skills = by_id[c].get("skills") or []
            if vd and vd.get("coverage") in ("full", "partial"):
                if not _overlaps(segs, vd["start"], vd["end"]):              # the teaching part must be in the plan
                    segs = _segments({"segments": [*segs, [vd["start"], vd["end"]]]}, dur)
                got = [s for s in vd.get("skills") or [] if s in skills]
                proofs = vd.get("proofs") or {}
                teaches.append({"concept": c, "state": vd["coverage"], "skills": got,
                                "missing": [s for s in skills if s not in got],
                                "proofs": [{"skill": s, "t": proofs[s]["t"], "text": proofs[s]["text"],
                                            "url": watch_url(vid, proofs[s]["t"])} for s in got if s in proofs]})
            else:
                teaches.append({"concept": c, "state": "unchecked", "skills": [], "missing": list(skills)})
        found = [vd for c in concepts_of if (vd := verdict.get((vid, c)))]
        said = [*(q for vd in found for q in vd.get("quotes") or []),
                *({"t": p["t"], "text": p["text"], "skill": s} for vd in found for s, p in (vd.get("proofs") or {}).items())]
        quotes: list[dict[str, Any]] = []
        for q in sorted(said, key=lambda q: q["t"]):                 # each moment once (a skill's quote names it)
            same = next((x for x in quotes if abs(x["t"] - q["t"]) <= 5), None)
            if same is None:
                quotes.append({**q, "url": watch_url(vid, q["t"])})
            elif q.get("skill") and not same.get("skill"):
                same["skill"] = q["skill"]
        errors = sorted({e["t"]: {**e, "url": watch_url(vid, e["t"])} for vd in found for e in vd.get("errors") or []}.values(),
                        key=lambda e: e["t"])
        parts = sorted({(round(p["start"]), round(p["end"])): {"start": round(p["start"]), "end": round(p["end"]), "text": p["text"]}
                        for c in concepts_of for p in ((excerpts or {}).get(c) or {}).get(vid) or []}.values(),
                       key=lambda p: p["start"])
        whole = len(segs) == 1 and segs[0][0] == 0 and (not dur or segs[0][1] >= dur - 15)
        secs = sum(b - a for a, b in segs)
        state = ("verified" if any(x["state"] in ("full", "partial") for x in teaches)
                 else "unverified" if not v.get("has_captions") else "unchecked")
        extra = bool(concepts_of) and not any(by_id[c].get("in_scope", True) for c in concepts_of)
        role = it.get("role") if it.get("role") in ("required", "prerequisite", "optional", "alternative") else "required"
        if extra and role in ("required", "prerequisite"):
            role = "optional"                         # the focus does not ask for it
        chapter = None
        if state != "verified" and whole:             # unchecked: open it where its chapters say the topic starts
            chapter = _chapter_for(v, [x for c in concepts_of for x in [*(by_id[c].get("terms") or []), by_id[c]["name"]]])
        n = len(items) + 1
        by_n[it.get("n", n)] = n
        items.append({
            "n": n, "video": vid, "title": v.get("title", ""), "channel": v.get("channel", ""),
            "channel_id": v.get("channel_id", ""), "view_count": v.get("view_count"), "like_count": v.get("like_count"),
            "upload_date": v.get("upload_date", ""),
            "weeks": sorted({w for c in concepts_of for w in by_id[c].get("weeks") or []}),
            "chapter": {"start": round(chapter["start"]), "title": chapter["title"]} if chapter else None,
            "url": watch_url(vid, (chapter["start"] if chapter else segs[0][0]) or None), "duration_s": dur,
            "start": round(segs[0][0]), "end": round(segs[-1][1]), "whole": whole,
            "segments": [{"start": round(a), "end": round(b), "url": watch_url(vid, a or None)} for a, b in segs],
            "minutes": max(1, round(secs / 60)) if secs > 0 else max(1, round(dur / 60)),
            "role": role, "alternative_to": it.get("alternative_to"), "concepts": concepts_of, "extra": extra,
            "why": str(it.get("why") or "").strip(), "limits": str(it.get("limits") or "").strip(),
            "teaches": teaches, "state": state, "quotes": quotes[:6], "errors": errors[:3], "excerpts": parts[:6],
            "captions": f"{v.get('caption_kind')}, {v.get('caption_lang')}" if v.get("has_captions") else "",
            "heard": ({k: (v.get("heard") or {}).get(k) for k in ("model", "device", "why", "upto")}
                      if v.get("caption_kind") == "speech" else None),
            "not_heard": str((v.get("listen") or {}).get("skipped") or ""),
            # with captions this goes only into the exported plan (English); the page shows the other one
            "inspected": ((f"what it says, transcribed from its sound on this PC ({(v.get('heard') or {}).get('model') or 'speech model'})"
                           + (f" in its first {round((v.get('heard') or {})['upto'] / 60)} minutes"
                              if (v.get("heard") or {}).get("upto") else "")
                           if v.get("caption_kind") == "speech" else f"captions ({v.get('caption_kind')}, {v.get('caption_lang')})")
                          + f": {len(parts)} part{'s' if len(parts) != 1 else ''} read"
                          + (f" ({', '.join(f'{fmt_ts(p['start'])}–{fmt_ts(p['end'])}' for p in parts[:4])})" if parts else "")
                          + (f" + {len(v.get('chapters') or [])} chapters" if v.get("chapters") else ""))
                         if v.get("has_captions") else unheard_text(v),
            "evidence": quotes[0]["text"] if quotes else "",
        })
    for it in items:
        if it["alternative_to"] is not None:
            it["alternative_to"] = by_n.get(it["alternative_to"])
    names = {c["id"]: c["name"] for c in concepts}
    supplements = [{"concept": str(s.get("concept") or ""), "name": names.get(str(s.get("concept")), ""),
                    "text": str(s.get("text") or "").strip()}
                   for s in raw.get("supplements") or [] if isinstance(s, dict) and str(s.get("text") or "").strip()]
    notes: dict[str, str] = {}
    for x in [*_listed(raw.get("notes")), *_listed(raw.get("coverage"))]:
        if isinstance(x, dict) and x.get("concept") and str(x.get("note") or "").strip():
            notes.setdefault(str(x["concept"]), str(x["note"]).strip())
    why = _left_out(concepts, items, verdict, videos, why, blocked)
    coverage = _coverage(concepts, items, supplements, notes, scope, why)
    checklist = [{"concept": str(x.get("concept") or ""), "name": names.get(str(x.get("concept")), ""),
                  "explain": str(x.get("explain") or "").strip(), "recognize": [str(r) for r in x.get("recognize") or [] if str(r).strip()][:4]}
                 for x in raw.get("checklist") or [] if isinstance(x, dict) and str(x.get("concept")) in by_id]
    for c in concepts:
        if c.get("in_scope", True) and not any(x["concept"] == c["id"] for x in checklist):
            checklist.append({"concept": c["id"], "name": c["name"], "explain": c["summary"], "recognize": c["cues"]})
    order = {c["id"]: i for i, c in enumerate(concepts)}
    checklist.sort(key=lambda x: (not by_id[x["concept"]].get("in_scope", True), order[x["concept"]]))
    schedule = [{"day": str(s.get("day") or ""), "items": [by_n[n] for n in s.get("items") or [] if n in by_n]}
                for s in raw.get("schedule") or [] if isinstance(s, dict)]
    for s in schedule:
        s["minutes"] = sum(items[n - 1]["minutes"] for n in s["items"])
    changes = raw.get("changes") if isinstance(raw.get("changes"), dict) else {}
    total = sum(it["minutes"] for it in items if it["role"] in ("required", "prerequisite"))
    return {
        "title": str(raw.get("title") or "Video study plan").strip(), "summary": str(raw.get("summary") or "").strip(),
        "items": items, "schedule": [s for s in schedule if s["items"]],
        "total_minutes": total,
        "optional_minutes": sum(it["minutes"] for it in items if it["role"] in ("optional", "alternative")),
        "coverage": coverage, "checklist": checklist, "supplements": supplements, "practice": practice,
        "completeness": completeness(coverage, scope, goal, total, goal_en),
        "concepts": [{k: c.get(k, d) for k, d in (("id", ""), ("name", ""), ("summary", ""), ("importance", "core"),
                                                  ("focus", False), ("weeks", []), ("los", []), ("in_scope", True),
                                                  ("skills", []))} for c in concepts],
        "gaps": gaps, "changes": {k: changes.get(k) or [] for k in ("added", "replaced", "removed")},
    }


def _left_out(concepts: list[dict], items: list[dict], verdict: dict[tuple[str, str], dict], videos: dict[str, dict],
              why: Optional[dict[str, dict[str, str]]], blocked: Optional[Callable[[str, str], bool]] = None
              ) -> dict[str, dict[str, str]]:
    """``why`` with, for each in-scope concept, the skills a checked video teaches that no item of the plan does: the
    plan left those videos out - because of their mistakes or a poor explanation (as the check found), or it didn't use
    them - and says so."""
    out = {cid: dict(x) for cid, x in (why or {}).items()}
    for c in concepts:
        if not c.get("in_scope", True):
            continue
        on_plan = {s for it in items for x in it.get("teaches") or []
                   if x["concept"] == c["id"] and x["state"] in ("full", "partial") for s in x["skills"]}
        off = {s for s in c.get("skills") or [] if s not in on_plan}
        teachers = [vd for (vid, cid), vd in verdict.items() if cid == c["id"] and off & set(vd.get("skills") or [])
                    and vd.get("coverage") in ("full", "partial")
                    and not (blocked is not None and blocked((videos.get(vid) or {}).get("channel_id", ""),
                                                             (videos.get(vid) or {}).get("channel", "")))]
        if not teachers:
            continue
        poor = all(vd.get("errors") or int(vd.get("quality") or 3) <= 2 for vd in teachers)
        old = out.get(c["id"]) or {}
        named = bool(old.get("text"))        # with why no checked video teaches its other skills: which ones are these
        if poor and named:
            one = N_("The checked video that teaches {what} has mistakes or explains it poorly, so the plan leaves it "
                     "out.")
            many = N_("The {n} checked videos that teach {what} have mistakes or explain it poorly, so the plan leaves "
                      "them out.")
        elif poor:
            one = N_("The checked video that teaches this has mistakes or explains it poorly, so the plan leaves it out.")
            many = N_("The {n} checked videos that teach this have mistakes or explain it poorly, so the plan leaves them "
                      "out.")
        elif named:
            one = N_("A checked video teaches {what}, but the plan doesn't use it.")
            many = N_("{n} checked videos teach {what}, but the plan doesn't use them.")
        else:
            one = N_("A checked video teaches this, but the plan doesn't use it.")
            many = N_("{n} checked videos teach this, but the plan doesn't use them.")
        n = len(teachers)
        skills = [s for s in c.get("skills") or [] if s in off and any(s in (vd.get("skills") or []) for vd in teachers)]
        say = one if n == 1 else many
        shown, en = t(say, n=n, what=_named(skills, False)), say.format(n=n, what=_named(skills, True))
        out[c["id"]] = {"text": " ".join(x for x in (old.get("text"), shown) if x),
                        "en": " ".join(x for x in (old.get("en"), en) if x)}
    return out


def _coverage(concepts: list[dict], items: list[dict], supplements: list[dict], notes: dict[str, str],
              scope: Scope, why: Optional[dict[str, dict[str, str]]] = None) -> list[dict[str, Any]]:
    """Per concept and skill, how the plan covers it - from the verdicts of the items it uses:
    each skill by "video" (a required item teaches it, with a quote of it doing so), "optional" (only optional items
    do), "supplement" or "missing"; the concept "verified" (every skill in a checked video), "partial", "supplement",
    "unverified" (only videos whose captions could not be checked), "missing" - or "skipped": an extra the plan leaves
    out. A concept with a skill no chosen video teaches says why no good video was found for it (``why``), when the
    search said."""
    with_sup = {s["concept"] for s in supplements}
    out = []
    for c in concepts:
        cid, skills = c["id"], c.get("skills") or []
        its = [it for it in items if cid in it["concepts"]]
        taught = [(it, x) for it in its for x in it.get("teaches", []) if x["concept"] == cid and x["state"] in ("full", "partial")]
        rows = []
        for s in skills:
            ns = [it["n"] for it, x in taught if s in x["skills"]]
            core = any(it["role"] in ("required", "prerequisite") for it, x in taught if s in x["skills"])
            proof = next(({"item": it["n"], "t": p["t"], "text": p["text"], "url": p.get("url", "")}
                          for it, x in taught for p in x.get("proofs") or [] if p["skill"] == s), None)
            rows.append({"text": s, "items": ns, "quote": proof,
                         "how": "video" if core else "optional" if ns else "supplement" if cid in with_sup else "missing"})
        if skills:
            on_video = sum(r["how"] in ("video", "optional") for r in rows)
            state = "verified" if on_video == len(skills) else "partial" if on_video or taught else ""
        else:
            state = "verified" if any(x["state"] == "full" for _, x in taught) else "partial" if taught else ""
        if not state:
            state = ("unverified" if its else "supplement" if cid in with_sup else "missing")
        if not c.get("in_scope", True) and state == "missing":
            state = "skipped"
        reason = (why or {}).get(cid) or {}
        lacking = state != "verified" and state != "skipped" and searched(c, scope)
        out.append({"concept": cid, "name": c["name"], "in_scope": c.get("in_scope", True), "state": state,
                    "items": [it["n"] for it in its], "skills": rows, "supplement": cid in with_sup,
                    "searched": searched(c, scope), "note": notes.get(cid, ""),
                    "why": str(reason.get("text") or "") if lacking else "",
                    "why_en": str(reason.get("en") or "") if lacking else ""})
    return out


def completeness(coverage: list[dict], scope: Scope, goal: str, required_minutes: int,
                 goal_en: str = "") -> dict[str, Any]:
    """How complete the plan is for what it is for, counted over the skills of the in-scope concepts (a concept
    without skills counts as one), with an honest one-line verdict: ``headline`` for the page (``goal`` in the
    language shown), ``headline_en`` for the exported plan (``goal_en``, English)."""
    units = []
    for cv in coverage:
        if not cv["in_scope"]:
            continue
        if cv["skills"]:
            units += [(cv["name"], r["text"], r["how"]) for r in cv["skills"]]
        else:
            how = {"verified": "video", "partial": "video", "supplement": "supplement"}.get(cv["state"], "missing")
            units.append((cv["name"], "", how))
    count = {k: sum(1 for u in units if u[2] == k) for k in ("video", "optional", "supplement", "missing")}
    n = len(units)
    goal = goal or target(scope, shown=True)
    goal_en = goal_en or target(scope)
    # how the skills are covered, as a list: "5 in checked video parts, 1 only in the written supplements"
    ways = [(N_("{n} in checked video parts"), count["video"]), (N_("{n} only in optional videos"), count["optional"]),
            (N_("{n} only in the written supplements"), count["supplement"])]
    how, how_en = ", ".join(t(say, n=k) for say, k in ways if k), ", ".join(say.format(n=k) for say, k in ways if k)
    if not n:
        level, say = "incomplete", N_("Nothing in your material for {goal} could be planned.")
    elif count["missing"]:
        level, say = "incomplete", N_("Not complete for {goal}: {missing} of the {n} skills found in your material have "
                                      "no checked video and no written supplement.")
    elif count["video"] == n:
        level, say = "full", N_("Complete for {goal}: all {n} skills found in your material are taught in checked video "
                                "parts.")
    elif count["supplement"] + count["optional"] <= n / 4:
        level, say = "mostly", N_("Complete for {goal}: all {n} skills found in your material are covered — {how}.")
    elif count["supplement"] >= count["optional"]:   # covered, but much of it only in writing
        level, say = "written", N_("Covered for {goal}, but partly only in writing: of the {n} skills found in your "
                                   "material, {how}.")
    else:                                        # covered, but much of it only in videos marked optional
        level, say = "optional", N_("Covered for {goal}, but partly only in optional videos: of the {n} skills found in "
                                    "your material, {how}.")
    headline = _capital(t(say, goal=goal, how=how, missing=count["missing"], n=n))   # Turkish may begin with the goal
    headline_en = say.format(goal=goal_en, how=how_en, missing=count["missing"], n=n)
    extras = [cv for cv in coverage if not cv["in_scope"]]
    # the concepts the focus needs with something no checked video teaches, and why the search found none
    no_video = []
    for cv in coverage:
        off = [r["text"] for r in cv["skills"] if r["how"] in ("supplement", "missing")]
        if cv["in_scope"] and (off or (not cv["skills"] and cv["state"] not in ("verified", "partial"))):
            no_video.append({"concept": cv.get("concept", ""), "name": cv["name"], "skills": off,
                             "missing": [r["text"] for r in cv["skills"] if r["how"] == "missing"],
                             "why": cv.get("why", ""), "why_en": cv.get("why_en", "")})
    return {"goal": goal, "goal_en": goal_en, "complete": bool(n) and not count["missing"], "level": level,
            "headline": headline, "headline_en": headline_en, "skills": n, **count, "no_video": no_video,
            "missing_list": [f"{s} ({name})" if s else name for name, s, how in units if how == "missing"][:15],
            "supplement_list": [f"{s} ({name})" if s else name for name, s, how in units if how == "supplement"][:15],
            "extras": [{"name": cv["name"], "items": cv["items"], "state": cv["state"]} for cv in extras],
            "extras_searched": scope.extras, "focused": focused(scope),
            "budget": scope.minutes, "required_minutes": required_minutes,
            "over_budget": max(0, required_minutes - scope.minutes)}


def plan_markdown(result: dict[str, Any], course: dict[str, Any], scope: Scope) -> str:
    """The plan as a Markdown file (Obsidian / VS Code / any editor)."""
    from .videos import fmt_ts

    names = {c["id"]: c["name"] for c in result.get("concepts", [])}
    out = [f"# {result['title']}", "", f"*{course.get('code', '')} — {course.get('name', '')} · {_week_span(scope.weeks)}"
           + (f" · for {scope.focus}" if scope.focus else "") + f" · made with IEUStudia on {dt.date.today():%d %b %Y}*", ""]
    if result.get("summary"):
        out += [result["summary"], ""]
    cp = result.get("completeness") or {}
    if cp.get("headline"):
        out.append(f"> **{cp.get('headline_en') or cp['headline']}**")
        if cp.get("missing_list"):
            out.append(f"> Not covered: {'; '.join(cp['missing_list'])}.")
        if cp.get("supplement_list"):
            out.append(f"> In the written supplements: {'; '.join(cp['supplement_list'])}.")
        for x in cp.get("no_video") or []:
            out.append(f"> No good video for {x['name']}" + (f" ({'; '.join(x['skills'])})" if x.get("skills") else "")
                       + (f": {x['why_en']}" if x.get("why_en") else "."))
        heard = result.get("listened") or {}
        if heard.get("heard") or heard.get("skipped") or heard.get("silent"):
            silent = int(heard.get("silent") or 0)
            out.append("> " + " ".join(x for x in [
                f"Listened to {heard['heard']} video{'s' if heard['heard'] != 1 else ''} without usable captions "
                f"(transcribed on this PC)." if heard.get("heard") else "",
                f"Listened to {silent} more without usable captions and heard no speech in "
                f"{'it' if silent == 1 else 'them'}." if silent else "",
                *(f"{x['n']} more {'was' if x['n'] == 1 else 'were'} not listened to: {x['why_en']}."
                  for x in heard.get("skipped") or [])] if x))
        if cp.get("extras"):
            out.append(f"> {extras_line(cp)}")
        out.append("")
    out += [f"**Viewing time:** {result['total_minutes']} min required" +
            (f" + {result['optional_minutes']} min optional" if result.get("optional_minutes") else "")
            + (f" — {cp['over_budget']} min over your {cp['budget']} min" if cp.get("over_budget") else ""), ""]
    ch = result.get("changes") or {}
    if any(ch.get(k) for k in ("added", "replaced", "removed")):
        out += ["## What changed", ""]
        for r in ch.get("replaced") or []:
            out.append(f"- Replaced {r.get('old', '') if isinstance(r, dict) else r}" + (f" — {r.get('why')}" if isinstance(r, dict) and r.get("why") else ""))
        for r in ch.get("removed") or []:
            out.append(f"- No longer needed: {r.get('old', '') if isinstance(r, dict) else r}" + (f" — {r.get('why')}" if isinstance(r, dict) and r.get("why") else ""))
        if ch.get("added"):
            out.append(f"- Added: items {', '.join(map(str, ch['added']))}")
        out.append("")
    out += ["## Viewing plan", ""]
    goal = cp.get("goal_en") or cp.get("goal") or target(scope)
    for it in result["items"]:
        segs = it.get("segments") or [{"start": it["start"], "end": it["end"], "url": it["url"]}]
        part = ("whole video" if it["whole"] else
                " and ".join(f"[{fmt_ts(s['start'])}–{fmt_ts(s['end'])}]({s['url']})" for s in segs))
        seen = "".join(f" · {_count(it[k])} {w}" for k, w in (("view_count", "views"), ("like_count", "likes")) if it.get(k))
        out.append(f"{it['n']}. **[{it['title']}]({it['url']})** — {it['channel']}{seen} · {part} · {it['minutes']} min · "
                   f"*{it['role']}*" + (f" (instead of {it['alternative_to']})" if it.get("alternative_to") else "")
                   + (f" · extra, not on {goal}" if it.get("extra") else ""))
        if it.get("teaches"):
            for tch in it["teaches"]:
                what = ("teaches " + ", ".join(tch["skills"]) if tch["skills"] else
                        {"full": "teaches it", "partial": "only partly", "unchecked": "not checked for this"}.get(tch["state"], ""))
                left = f" — not here: {', '.join(tch['missing'])}" if tch["missing"] and tch["state"] != "unchecked" else ""
                out.append(f"   - {names.get(tch['concept'], tch['concept'])}: {what}{left}")
        else:
            out.append(f"   - Teaches: {', '.join(names.get(c, c) for c in it['concepts'])}")
        if it["why"]:
            out.append(f"   - Why: {it['why']}")
        if it.get("limits"):
            out.append(f"   - Limits: {it['limits']}")
        for e in it.get("errors") or []:
            out.append(f"   - ⚠ Possible mistake at [{fmt_ts(e['t'])}]({e['url']}): {e['text']}")
        inspected = it["inspected"] if it.get("captions") else unheard_text(it, english=True)
        out.append(f"   - Checked: {inspected}" + (f" — “{it['evidence']}”" if it.get("evidence") and not it.get("quotes") else ""))
        for q in it.get("quotes") or []:
            out.append(f"     - [{fmt_ts(q['t'])}]({q['url']}) “{q['text']}”" + (f" *({q['skill']})*" if q.get("skill") else ""))
    if result.get("schedule"):
        out += ["", "## Schedule", ""]
        out += [f"- **{s['day']}**: items {', '.join(map(str, s['items']))} ({s['minutes']} min)" for s in result["schedule"]]
    out += ["", "## Concept checklist", ""]
    for x in result["checklist"]:
        out.append(f"- [ ] **{x['name'] or names.get(x['concept'], x['concept'])}** — {x['explain']}")
        for r in x["recognize"]:
            out.append(f"  - Recognise it: {r}")
    mark = {"video": "✓", "optional": "✓ (optional video)", "supplement": "✎ supplement", "missing": "✗"}
    out += ["", "## Coverage", "", "| Concept | For | State | Skills | Items | Note |", "|---|---|---|---|---|---|"]
    for cv in result["coverage"]:
        skills = "; ".join(f"{mark.get(r['how'], '')} {r['text']}" + (f" (#{', #'.join(map(str, r['items']))})" if r["items"] else "")
                           for r in cv.get("skills") or [])
        out.append(f"| {names.get(cv['concept'], cv['concept'])} | {'yes' if cv.get('in_scope', True) else 'extra'} | "
                   f"{cv['state']} | {skills or '—'} | {', '.join(map(str, cv['items'])) or '—'} | "
                   f"{' '.join(x for x in (cv['note'], cv.get('why_en') or '') if x)} |")
    if result.get("supplements"):
        out += ["", "## Written supplements", ""]
        for s in result["supplements"]:
            out += [f"### {s['name'] or names.get(s['concept'], s['concept'])}", "", s["text"], ""]
    if result.get("practice"):
        out += ["", "## Practice (from your course material)", ""]
        for i, p in enumerate(result["practice"], 1):
            out += [f"**{i}. {p['question']}**" + (f" *({p['source']})*" if p.get("source") else ""), "",
                    f"> {p['answer']}".replace("\n", "\n> "), ""]
    if result.get("gaps"):
        out += ["", f"*Not in the material: {result['gaps']}*"]
    out += ["", "*The videos were checked by AI through their captions (a video without usable captions through what "
                "IEUStudia heard in it, transcribed on this PC), and AI can make mistakes: the quotes above let you check "
                "what each video says.*"]
    return "\n".join(out).rstrip() + "\n"


def extras_line(cp: dict[str, Any]) -> str:
    """The topics of these weeks that the focus does not ask for, and what the plan does with them."""
    extras = cp.get("extras") or []
    with_video = [x["name"] for x in extras if x["items"]]
    line = f"Also in these weeks but not on {cp.get('goal_en') or cp.get('goal', 'the focus')}: {', '.join(x['name'] for x in extras)}."
    if with_video:
        line += f" Optional videos for {', '.join(with_video)}."
    if len(with_video) < len(extras):
        rest = "the others" if with_video else "them"
        line += f" No checked video for {rest}." if cp.get("extras_searched") else f" This plan does not cover {rest}."
    return line


# ------------------------------------------------------------------ exam review sheet (one request)
SYSTEM_PREP = ("You are an expert university tutor writing exam preparation material for an engineering student. "
               "You use only the course material you are given (say so when it is thin instead of inventing), you "
               "write clear Markdown with LaTeX maths ($...$ in a sentence, $$...$$ on lines of their own), and you "
               "deliver the complete sheet without asking questions.\n\n" + ALERTS_MD)


def prep_title(scope: Scope, facts: dict[str, Any], shown: bool = False) -> str:
    """"Quiz 2 review sheet": in English for its file, ``shown`` in the language shown."""
    what = scope.focus or _capital(_week_span([w["no"] for w in facts["weeks"]], shown))
    return t("{what} review sheet", what=what) if shown else f"{what} review sheet"


def prep_prompt(scope: Scope, facts: dict[str, Any], questions: int = 10) -> str:
    return f"""TASK: Write an exam review sheet for the scope below, from the course material only.
Use exactly these sections:
## What it covers
The assessment (if any), the weeks and the learning outcomes involved (quote their text), and what the lecturer said about it (short quotes with dates).
## Key ideas
Per learning outcome or topic: the essential definitions, results and methods as short bullets, each with where it is in the material (file and page, or lecture date).
## Formula sheet
Every formula the scope needs, one line each on when to use it.
## Question types
The kinds of questions to expect (from the material's examples and the lecturer's hints), how to recognise each, and the method to use.
## Practice
{questions} questions from easy to exam level in the style of the course material. After each question, its worked solution as a block quote starting with "**Solution.**".
## Common mistakes
The traps students fall into on these topics.
## Last-day checklist
6–10 checkbox items ("- [ ] …").

{_scope_text(scope, facts)}

=== COURSE MATERIAL ===
{facts['pack']}"""


# ------------------------------------------------------------------ the stages' pieces
def find_terms(c: dict[str, Any], missing: Optional[list[str]] = None) -> list[str]:
    """What to look for in a video's captions for a concept: its terms, its skills (``missing``: only those no checked
    video teaches yet) and its name."""
    return [*c.get("terms", []), *(c.get("skills", []) if missing is None else missing), c["name"]]


def video_row(info: Any, langs: tuple[str, ...]) -> dict[str, Any]:
    """What a plan keeps of an inspected video - or of why YouTube refused it -, with whether it is in the plan's
    languages and, when it is, what is wrong with its captions (videos.caption_problem; "" when nothing)."""
    from . import videos

    if isinstance(info, Exception):
        return {"error": str(info), "error_kind": str(getattr(info, "kind", "error"))}
    row = {"title": info.title, "channel": info.channel, "duration_s": info.duration_s,
           "caption_kind": info.caption_kind, "caption_lang": info.caption_lang,
           "has_captions": info.has_captions, "upload_date": info.upload_date,
           "view_count": info.view_count, "like_count": getattr(info, "like_count", None),
           "channel_id": getattr(info, "channel_id", ""), "language": getattr(info, "language", ""),
           "chapters": [{"start": ch.start, "end": ch.end, "title": ch.title} for ch in info.chapters],
           "caption_problem": "", "heard": dict(getattr(info, "heard", None) or {})}
    if not videos.in_languages(info, langs):
        row["wrong_lang"] = True                              # spoken in another language: not a candidate
    else:
        row["caption_problem"] = videos.caption_problem(info, langs)
    return row


def excerpt_parts(info: Any, terms: list[str], top: int) -> list[dict[str, Any]]:
    """The parts of a video's captions that talk most about ``terms``, as timed text for the verification - the
    opening when no term is found (it still tells a lot)."""
    from . import videos

    wins = videos.windows(info, terms, top=top)
    if not wins and info.cues:
        wins = [{"start": 0.0, "end": min(info.duration_s or 180, 180), "score": 0}]
    return [videos.stamped_part(info, w["start"], w["end"], limit=EXCERPT_CHARS) for w in wins]


def best_rows(rows: list[dict[str, Any]], n: int) -> list[dict[str, Any]]:
    """The candidates the plan-writing request gets for a concept: the ``n`` best (``rows`` best first), and after
    them the best one for each skill none of those teaches. The more searches find videos for the missing skills
    only, and those rank last: cut to ``n``, the plan never saw them and said it didn't use them."""
    def teaches(r: dict[str, Any]) -> set[str]:
        good = r.get("state") == "verified" and r.get("coverage") in ("full", "partial")
        return set(r.get("skills") or []) if good else set()

    chosen = list(rows[:n])
    taught = {s for r in chosen for s in teaches(r)}
    for r in rows[n:]:
        new = teaches(r) - taught
        if new:
            chosen.append(r)
            taught |= new
    return chosen


def gaps_of(concepts: list[dict[str, Any]], verdicts: dict[str, dict[str, dict]]) -> dict[str, list[str]]:
    """What no checked video teaches yet: {concept id: its skills without a video quoted teaching them} - a concept
    without skills is there (with []) when no checked video teaches it at all."""
    out: dict[str, list[str]] = {}
    for c in concepts:
        good = [vd for vd in (verdicts.get(c["id"]) or {}).values() if vd.get("coverage") in ("full", "partial")]
        skills = c.get("skills") or []
        if skills:
            have = {s for vd in good for s in vd.get("skills") or []}
            if any(s not in have for s in skills):
                out[c["id"]] = [s for s in skills if s not in have]
        elif not good:
            out[c["id"]] = []
    return out


def more_queries_prompt(scope: Scope, facts: dict[str, Any], concepts: list[dict[str, Any]],
                        gaps: dict[str, list[str]], tried: dict[str, list[str]]) -> str:
    """For the concepts with gaps: new YouTube searches, aimed at what no checked video teaches yet."""
    lang = {"en": "in English", "tr": "in Turkish, with the words Turkish teachers use",
            "both": "in English, and the last one in Turkish with the words Turkish teachers use (e.g. \"özdeğer\")"}
    lines = []
    for c in concepts:
        if c["id"] not in gaps:
            continue
        missing = gaps[c["id"]]
        lines.append(f"{c['id']} — {c['name']}: {c['summary']}")
        lines.append("   not taught by any video checked so far: " + ("; ".join(missing) if missing else "the concept itself"))
        lines.append("   already searched: " + "; ".join(f"\"{q}\"" for q in tried.get(c["id"], [])))
    return f"""TASK: YouTube searches for what the videos checked so far do not teach. For each concept below write {MORE_QUERIES} new YouTube searches, best first, {lang[scope.video_lang]}, that would find a video teaching exactly what is still missing: first a narrow one for the missing skills themselves (e.g. "normal approximation binomial continuity correction"), then ones that say it differently from the searches already made (other words a teacher would use, "example", "exam problem", the name of the method). 3–8 words each, naming the subject; never one already searched.
Reply with the JSON on a single line: {{"searches": [{{"concept": "c1", "queries": ["", ""]}}]}}

Course: {facts['course']['code']} — {facts['course']['name']}
""" + "\n".join(lines)


def clean_more_queries(raw: Any, gaps: dict[str, list[str]], tried: dict[str, list[str]]) -> dict[str, list[str]]:
    """The AI's new searches: only for concepts with gaps, none already made, at most MORE_QUERIES each (and one more
    in Turkish with both languages)."""
    out: dict[str, list[str]] = {}
    for x in (raw.get("searches") if isinstance(raw, dict) else None) or []:
        if not isinstance(x, dict) or str(x.get("concept") or "") not in gaps:
            continue
        cid = str(x["concept"])
        done = {" ".join(q.casefold().split()) for q in tried.get(cid, [])}
        qs = []
        for q in x.get("queries") or []:
            q = " ".join(str(q or "").split())[:120]
            if q and " ".join(q.casefold().split()) not in done and q not in qs:
                qs.append(q)
        out[cid] = qs[:MORE_QUERIES + 1]
    return out


def fallback_queries(c: dict[str, Any], missing: list[str]) -> list[str]:
    """Searches made in code when the AI wrote none: the concept with each missing skill, then with an example."""
    name = c["name"]
    qs = [" ".join(f"{name} {s}".split()[:9]) for s in missing[:2]]
    return qs + [f"{name} example problem", f"{name} explained"]


def _named(skills: Iterable[str], english: bool) -> str:
    """Skills in quotes, joined as the language joins them: “A”, “B” and “C”."""
    from .i18n import join

    items = [f"“{s}”" for s in skills]
    if not english:
        return join(items)
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}" if items else ""


def why_no_video(searches: int, found: int, checked: int, unheard: dict[str, str], other_lang: int,
                 refused: int, blocked: bool, capped: bool = False, stopped: bool = False, silent: int = 0,
                 what: Optional[list[str]] = None) -> tuple[str, str]:
    """Why the search found no good video for a concept (or some of its skills), in plain words: (shown, English).
    ``found``: the candidates it found, ``checked``: those the AI judged, ``unheard``: {video: why it wasn't listened
    to} for those without usable captions, ``blocked``: YouTube refused the connection (a bot check), ``capped``: the
    plan's limit of more checks was reached before its new candidates were checked, ``stopped``: the plan's limits
    were used up before more could be searched for it (not the search being exhausted), ``silent``: videos without
    usable captions it listened to and heard no speech in, ``what``: the skills this is about, when the concept has
    others that a checked video does teach (they are named: its other reasons are about those)."""
    from .listen import reason_text

    def say(english: bool) -> str:
        def count(one: str, many: str, n: int, **kw: Any) -> str:
            return (one if n == 1 else many).format(n=n, **kw) if english else tn(n, one, many, **kw)

        def text(s: str) -> str:
            return s if english else t(s)

        out = []
        if blocked and not checked:
            out.append(text(N_("YouTube stopped answering IEUStudia for now (it asked to confirm it isn't a bot), so "
                               "no video could be checked.")))
        elif not found:
            out.append(count(N_("No video in the plan's language came up in {n} YouTube search."),
                             N_("No video in the plan's language came up in {n} YouTube searches."), searches))
        elif not checked and other_lang and not unheard:
            out.append(text(N_("The videos found are in another language.")))
        elif checked and what:
            out.append(count(N_("{n} video from {searches} was checked, and it doesn't teach {what}."),
                             N_("{n} videos from {searches} were checked, and none of them teaches {what}."), checked,
                             searches=count(N_("{n} YouTube search"), N_("{n} YouTube searches"), searches),
                             what=_named(what, english)))
        elif checked:
            out.append(count(N_("{n} video from {searches} was checked, and it doesn't teach this."),
                             N_("{n} videos from {searches} were checked, and none of them teaches this."), checked,
                             searches=count(N_("{n} YouTube search"), N_("{n} YouTube searches"), searches)))
        if unheard:
            why = reason_text(next(iter(unheard.values())), english)
            if checked:
                out.append(count(N_("{n} more video had no usable captions, and IEUStudia didn't listen to it because "
                                    "{why}."),
                                 N_("{n} more videos had no usable captions, and IEUStudia didn't listen to them "
                                    "because {why}."), len(unheard), why=why))
            elif len(unheard) == 1:                  # (two texts, not one with a count: the Turkish differs more)
                say1 = N_("The video found has no usable captions, and IEUStudia didn't listen to it because {why}.")
                out.append(say1.format(why=why) if english else t(say1, why=why))
            else:
                say2 = N_("The {n} videos found have no usable captions, and IEUStudia didn't listen to them because "
                          "{why}.")
                out.append(say2.format(n=len(unheard), why=why) if english else t(say2, n=len(unheard), why=why))
        if silent:
            out.append(count(N_("IEUStudia listened to {n} video without usable captions and heard no speech in it."),
                             N_("IEUStudia listened to {n} videos without usable captions and heard no speech in them."),
                             silent))
        if refused and not (blocked and not checked):
            out.append(count(N_("YouTube refused {n} video."), N_("YouTube refused {n} videos."), refused))
        if capped:
            out.append(text(N_("More videos were found for it, but the plan's limit of extra checks was reached first.")))
        elif stopped:
            out.append(text(N_("The plan's limit of extra videos and checks was reached before IEUStudia could look "
                               "further for it.")))
        return " ".join(out)

    return say(False), say(True)


def _hit(h: dict[str, Any]) -> Any:
    """A search hit kept in a plan (a dict) as the object videos.rank takes."""
    return SimpleNamespace(**{"description": "", "channel_id": "", "like_count": None, "view_count": None,
                              "duration_s": 0.0, "channel": "", "title": "", **h})


def _hit_json(h: Any) -> dict[str, Any]:
    return h.to_json() if hasattr(h, "to_json") else dict(vars(h))


# ------------------------------------------------------------------ the runner
class PlanRunner:
    """Builds plans one at a time on a background thread; progress goes out as ``{"type": "plan", ...}`` events."""

    def __init__(self, services: Any, ask: Optional[Callable[..., tuple[Any, dict]]] = None,
                 ask_text: Optional[Callable[..., dict]] = None) -> None:
        self._s = services
        self._ask = ask                  # tests replace Claude with these two
        self._ask_text = ask_text
        self._lock = threading.Lock()
        self._queue: list[str] = []
        self._cancel: dict[str, threading.Event] = {}
        self._worker: Optional[threading.Thread] = None
        if not getattr(services, "beside_app", False):      # (a command line next to the open app: its plans run)
            services.lib.pause_running_plans()

    # ---- public
    def create(self, course_id: str, scope: dict[str, Any], parent_id: str = "") -> str:
        scope = dict(scope or {})
        if scope.get("video_lang") not in ytprefs.LANGS and not scope.get("allow_tr"):
            scope["video_lang"] = ytprefs.load().lang             # the language chosen in Settings
        s = Scope.from_json(scope)
        from .ai import provider
        from .effort import LEGACY, OPENAI

        if provider() in OPENAI and s.model:          # ChatGPT has no Claude model: the plan keeps only its level
            if not s.effort and parent_id and s.model in LEGACY:
                s.effort = LEGACY[s.model]            # a new version of a plan from before reasoning levels
            s.model = ""
        pid = uuid.uuid4().hex[:12]
        self._s.lib.create_plan(pid, course_id, s.to_json(), "", parent_id)
        return pid

    def start(self, plan_id: str, from_stage: str = "") -> None:
        plan = self._s.lib.plan(plan_id)
        if plan is None:
            raise KeyError(t("This plan no longer exists."))
        if from_stage in STAGE_NAMES:                 # redo a stage and everything after it
            data = plan["data"]
            for st in STAGE_NAMES[STAGE_NAMES.index(from_stage):]:
                data.pop(st, None)
                for extra in {"verify": ("verify_parts",), "more": ("more_parts",), "listen": ("listened",),
                              "search": ("search_failed",)}.get(st, ()):
                    data.pop(extra, None)                # its finished requests (what it listened to) too
            self._s.lib.update_plan(plan_id, data=data)
        with self._lock:
            if plan_id in self._queue or plan_id in self._cancel:
                return
            self._queue.append(plan_id)
            self._cancel[plan_id] = threading.Event()
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._work, name="plans", daemon=True)
                self._worker.start()
        self._s.lib.update_plan(plan_id, status="queued", message=t("Waiting…"), error="")
        self._emit(plan_id)

    def cancel(self, plan_id: str) -> None:
        with self._lock:
            ev = self._cancel.get(plan_id)
            if plan_id in self._queue:
                self._queue.remove(plan_id)
                self._cancel.pop(plan_id, None)
                self._s.lib.update_plan(plan_id, status="paused", message=t("Stopped — resume to continue"))
                self._emit(plan_id)
        if ev is not None:
            ev.set()

    def busy(self) -> bool:
        with self._lock:
            return bool(self._cancel)

    def shutdown(self) -> None:
        with self._lock:
            events = list(self._cancel.values())
            self._queue.clear()
        for ev in events:
            ev.set()

    # ---- the work
    def _work(self) -> None:
        while True:
            with self._lock:
                if not self._queue:
                    self._worker = None
                    return
                plan_id = self._queue.pop(0)
                cancel = self._cancel.get(plan_id) or threading.Event()
            try:
                self._run(plan_id, cancel)
            except Cancelled:
                self._s.lib.update_plan(plan_id, status="paused", message=t("Stopped — resume to continue"))
            except Exception as exc:  # noqa: BLE001 - reported on the plan
                log.exception("Plan %s failed", plan_id)
                # the page's notice (notices.js) adds that Resume goes on from this step, keeping the steps done
                self._s.lib.update_plan(plan_id, status="error", error=str(exc) or exc.__class__.__name__,
                                        message=t("Something went wrong"))
            finally:
                with self._lock:
                    self._cancel.pop(plan_id, None)
                self._emit(plan_id)

    def _emit(self, plan_id: str, **extra: Any) -> None:
        p = self._s.lib.plan_brief(plan_id)
        if p is None:
            return
        self._s.emit({"type": "plan", "id": plan_id, "course_id": p["course_id"], "status": p["status"],
                      "stage": p["stage"], "progress": p["progress"], "message": p["message"], "error": p["error"],
                      "title": p["title"], "kind": (p.get("scope") or {}).get("kind", ""), **extra})

    def _ask_json(self, prompt: str, model: str, cancel: threading.Event, level: str = "") -> tuple[Any, dict]:
        from .ai import ask_json

        return (self._ask or ask_json)(SYSTEM, prompt, model=model, effort=level, cancel=cancel)

    def _stage(self, plan_id: str, name: str, progress: float, message: str = "") -> None:
        label = t(LABELS[name])
        self._s.lib.update_plan(plan_id, status="running", stage=name, progress=round(progress, 3),
                                message=message or label + "…")
        self._emit(plan_id)

    def _run(self, plan_id: str, cancel: threading.Event) -> None:
        from . import videos

        plan = self._s.lib.plan(plan_id)
        if plan is None:
            return
        scope = Scope.from_json(plan["scope"])
        data: dict[str, Any] = plan["data"] or {}
        usage: dict[str, dict[str, int]] = plan["usage"] or {}
        model = main_model(scope, self._s.settings)
        verify_with = getattr(self._s.settings, "verify_model", "") or VERIFY_MODEL
        level, verify_level = levels(scope, self._s.settings)
        verify_shown = model_shown(verify_with, verify_level)

        def save(**more: Any) -> None:
            self._s.lib.update_plan(plan_id, data=data, usage=usage, **more)

        def add_usage(stage: str, res: dict[str, Any]) -> None:
            u = usage.setdefault(stage, {})
            for k, v in (res.get("usage") or {}).items():
                u[k] = u.get(k, 0) + int(v or 0)
            u["model"] = res.get("model", "")

        def check() -> None:
            if cancel.is_set():
                raise Cancelled()

        if scope.kind in ("pack", "sheet"):            # a study pack or a cheatsheet: stages of its own
            from . import cheatsheet, studypack

            (cheatsheet if scope.kind == "sheet" else studypack).run(self, plan, scope, data, usage, model, cancel)
            return
        # gather
        if "gather" not in data:
            self._stage(plan_id, "gather", 0.02)
            data["gather"] = gather(self._s.lib, self._s.settings, plan["course_id"], scope)
            if plan.get("parent_id") and scope.kind == "videos":
                data.update(self._reusable(plan["parent_id"], scope, data["gather"]))
            save()
        facts = data["gather"]
        check()
        if scope.kind == "prep":
            self._write_prep(plan, scope, facts, model, cancel, add_usage, save, data)
            return
        # concepts
        if "concepts" not in data:
            self._stage(plan_id, "concepts", 0.08, t("Working out what you need to learn ({model})…", model=model_shown(model, level)))
            raw, res = self._ask_json(concepts_prompt(scope, facts), model, cancel, level)
            concepts, practice, gaps = clean_concepts(raw, scope)
            add_usage("concepts", res)
            data["concepts"] = {"concepts": concepts, "practice": practice, "gaps": gaps}
            save()
        concepts = data["concepts"]["concepts"]
        todo_c = [c for c in concepts if searched(c, scope)]
        qs_of = {c["id"]: concept_queries(c, scope) for c in todo_c}         # extras: fewer searches
        prefs = ytprefs.load()                    # the channels the student prefers / never wants (read once per run)
        langs = ytprefs.caption_langs(scope.video_lang)
        check()
        # search
        if "search" not in data:
            queries = sorted({q for c in todo_c for q in qs_of[c["id"]]})
            done_q = [0]

            def on_search(n: int, total: int, label: str) -> None:
                done_q[0] = n
                self._stage(plan_id, "search", 0.25 + 0.15 * n / max(1, total),
                            t("Searching YouTube: “{query}” ({n}/{total})", query=label, n=n, total=total))

            self._stage(plan_id, "search", 0.25, t("Searching YouTube for {n} concepts…", n=len(todo_c)))
            found = videos.search_many(queries, limit=8, cancel=cancel, progress=on_search)
            # the preferred channels are also searched themselves (their first few, with each topic's first query)
            mine = [(ch["id"], qs_of[c["id"]][0]) for c in todo_c if c.get("in_scope", True) and qs_of[c["id"]]
                    for ch in prefs.allow[:PREFERRED_SEARCHED]]
            if mine:
                self._stage(plan_id, "search", 0.4, t("Searching your preferred channels…"))
                own = videos.channel_search_many(mine, cancel=cancel)
            else:
                own = {}
            check()
            cands: dict[str, list[dict]] = {}
            failures = 0
            for c in todo_c:
                hits, seen = [], set()
                results = [found.get(q) for q in qs_of[c["id"]]]
                results += [own.get(key) for key in mine if key[1] == (qs_of[c["id"]] or [""])[0]]
                for res_q in results:
                    if isinstance(res_q, Exception) or res_q is None:
                        failures += 1
                        continue
                    for h in res_q:
                        if h.id not in seen:
                            seen.add(h.id)
                            hits.append(h)
                ranked = videos.rank(hits, c["terms"] + [c["name"]], allow=prefs.is_allowed, block=prefs.is_blocked,
                                     lang="" if scope.video_lang == "both" else scope.video_lang)[:CANDIDATES]
                cands[c["id"]] = [h.to_json() for h in ranked]
            if failures and not any(cands.values()):
                err = next((r for r in found.values() if isinstance(r, Exception)), None)
                raise RuntimeError(t("YouTube search failed: {error}", error=err) if err else t("YouTube search failed."))
            data["search"] = cands
            data["search_failed"] = {q: r.kind for q, r in found.items() if isinstance(r, videos.VideoError)}
            save()
        cands = data["search"]
        check()
        # inspect
        if "inspect" not in data:
            order = sorted(todo_c, key=lambda c: (not c.get("in_scope", True), c["importance"] != "core", not c["focus"]))
            per = {c["id"]: INSPECT if c.get("in_scope", True) else INSPECT_EXTRA for c in todo_c}
            ids: list[str] = []
            for c in order:
                for h in cands.get(c["id"], [])[:per[c["id"]]]:
                    if h["id"] not in ids and len(ids) < MAX_VIDEOS:
                        ids.append(h["id"])

            def on_inspect(n: int, total: int, label: str) -> None:
                self._stage(plan_id, "inspect", 0.40 + 0.13 * n / max(1, total),
                            t("Reading captions: {title} ({n}/{total})", title=label[:60], n=n, total=total))

            self._stage(plan_id, "inspect", 0.40, t("Reading the captions of {n} videos…", n=len(ids)))
            infos = videos.inspect_many(ids, langs=langs, cancel=cancel, progress=on_inspect)
            check()

            def usable(vid: str) -> bool:
                info = infos.get(vid)
                return info is not None and not isinstance(info, Exception) and videos.in_languages(info, langs)

            # a video in another language (or one YouTube refused) leaves its concept short: its next candidates
            next_ids: list[str] = []
            for c in order:
                hs = [h["id"] for h in cands.get(c["id"], [])]
                need = per[c["id"]] - sum(1 for v in hs if v in infos and usable(v))
                for v in hs:
                    if need <= 0 or len(ids) + len(next_ids) >= MAX_VIDEOS:
                        break
                    if v not in infos:
                        next_ids += [v] if v not in next_ids else []
                        need -= 1
            if next_ids:
                self._stage(plan_id, "inspect", 0.53, t("Reading the captions of {n} videos…", n=len(next_ids)))
                infos.update(videos.inspect_many(next_ids, langs=langs, cancel=cancel))
                check()
            chosen = {c["id"]: [h["id"] for h in cands.get(c["id"], []) if h["id"] in infos and usable(h["id"])][:per[c["id"]]]
                      for c in todo_c}
            vids: dict[str, dict] = {}
            excerpts: dict[str, dict[str, list]] = {}
            hear: dict[str, dict[str, Any]] = {}            # chosen videos whose captions can't show what they teach
            for vid, info in infos.items():
                vids[vid] = video_row(info, langs)
                if vids[vid].get("error") or vids[vid].get("wrong_lang"):
                    continue                                 # refused, or spoken in another language: not a candidate
                for c in order:
                    if vid not in chosen[c["id"]]:
                        continue
                    if vids[vid]["caption_problem"]:
                        hear.setdefault(vid, {"video": vid, "problem": vids[vid]["caption_problem"], "concepts": []})
                        hear[vid]["concepts"].append(c["id"])
                    if info.has_captions:                    # (poor captions too: they are all there is if not heard)
                        excerpts.setdefault(c["id"], {})[vid] = excerpt_parts(info, find_terms(c),
                                                                              3 if c.get("in_scope", True) else 2)
            lectures = [(vid, info) for vid, info in infos.items() if not isinstance(info, Exception)
                        and info.has_captions and (info.duration_s or 0) >= CROSS_MIN_S
                        and not vids[vid].get("wrong_lang") and not vids[vid].get("caption_problem")]
            for c in todo_c:                       # long lectures found for one concept often teach the next ones too
                if not c.get("in_scope", True):
                    continue
                scored = []
                for vid, info in lectures:
                    if vid in excerpts.get(c["id"], {}):
                        continue
                    wins = videos.windows(info, find_terms(c), top=2)
                    if wins and wins[0]["score"] >= CROSS_SCORE:
                        scored.append((wins[0]["score"], vid, info, wins))
                for _, vid, info, wins in sorted(scored, key=lambda s: -s[0])[:CROSS]:
                    excerpts.setdefault(c["id"], {})[vid] = [videos.stamped_part(info, w["start"], w["end"],
                                                                                 limit=EXCERPT_CHARS) for w in wins]
            for c in todo_c:                                           # search-only metadata for uninspected candidates
                for h in cands.get(c["id"], []):
                    vids.setdefault(h["id"], {"title": h["title"], "channel": h["channel"], "duration_s": h["duration_s"],
                                              "caption_kind": "", "caption_lang": "", "has_captions": False,
                                              "view_count": h.get("view_count"), "channel_id": h.get("channel_id", ""),
                                              "chapters": [], "uninspected": True})
            data["inspect"] = {"videos": vids, "excerpts": excerpts, "listen": list(hear.values())}
            save()
        vids = data["inspect"]["videos"]
        excerpts = data["inspect"]["excerpts"]
        check()
        by_id = {c["id"]: c for c in todo_c}
        # listen: the chosen videos whose captions can't show what they teach
        if "listen" not in data:
            todo = [x for x in data["inspect"].get("listen") or [] if x.get("video") in vids]
            data["listen"] = self._listen(plan_id, "listen", todo, vids, excerpts, by_id, langs, data, cancel, save)
            save()
        check()
        # verify
        if "verify" not in data:
            todo = [c for c in todo_c if excerpts.get(c["id"])]
            batches = [todo[i:i + VERIFY_BATCH] for i in range(0, len(todo), VERIFY_BATCH)]
            finished: dict[str, dict] = data.setdefault("verify_parts", {})   # kept when the plan stops before the end
            work = [(" ".join(c["id"] for c in b), b, {c["id"]: excerpts[c["id"]] for c in b}) for b in batches]
            self._judge(plan_id, "verify", work, vids, finished, (verify_with, verify_level, verify_shown), cancel,
                        save, add_usage, (0.62, 0.74))
            check()
            verdicts: dict[str, dict] = {}
            for k, _, _ in work:
                for cid, per in finished.get(k, {}).items():
                    verdicts.setdefault(cid, {}).update(per)
            data["verify"] = verdicts
            data.pop("verify_parts", None)
            save()
        check()
        # more: what no checked video teaches yet
        if "more" not in data:
            data["more"] = self._more(plan_id, scope, facts, todo_c, cands, qs_of, vids, excerpts, data["verify"], prefs,
                                      langs, data, cancel, save, add_usage, (verify_with, verify_level, verify_shown))
            data.pop("more_parts", None)
            save()
        more = data["more"]
        vids = {**vids, **(more.get("videos") or {})}
        excerpts = {cid: {**(excerpts.get(cid) or {}), **((more.get("excerpts") or {}).get(cid) or {})}
                    for cid in {*excerpts, *(more.get("excerpts") or {})}}
        verdicts = {cid: {**(data["verify"].get(cid) or {}), **((more.get("verdicts") or {}).get(cid) or {})}
                    for cid in {*data["verify"], *(more.get("verdicts") or {})}}
        why = self._why(todo_c, cands, qs_of, vids, verdicts, more, data.get("search_failed") or {})
        check()
        # assemble
        candidates: dict[str, list[dict]] = {}
        for c in todo_c:
            rows = []
            hits = {h["id"]: h for h in [*cands.get(c["id"], []), *((more.get("hits") or {}).get(c["id"]) or [])]}
            for vid in [*hits, *(v for v in excerpts.get(c["id"], {}) if v not in hits)]:   # + lectures checked for it
                v = vids.get(vid) or {}
                if not v or v.get("error") or v.get("wrong_lang"):
                    continue
                h = hits.get(vid) or {}
                if prefs.is_blocked(v.get("channel_id") or h.get("channel_id", ""), v.get("channel") or h.get("channel", "")):
                    continue                                  # blocked since the search
                vd = (verdicts.get(c["id"]) or {}).get(vid)
                if vd and vd["coverage"] == "none":
                    continue
                views, likes = v.get("view_count") or h.get("view_count"), v.get("like_count")
                base = {"video": vid, "title": v.get("title") or h.get("title", ""), "channel": v.get("channel") or h.get("channel", ""),
                        "duration_s": v.get("duration_s") or h.get("duration_s") or 0, "views": views, "likes": likes,
                        "preferred": prefs.is_allowed(v.get("channel_id") or h.get("channel_id", ""), v.get("channel") or h.get("channel", "")),
                        "received": videos.reception(views, likes)}
                if vd:
                    rows.append({**base, "state": "verified", **vd, "minutes": max(1, round((vd["end"] - vd["start"]) / 60))})
                elif not v.get("has_captions"):
                    rows.append({**base, "state": "unverified", "unread": bool(v.get("uninspected"))})
            rows.sort(key=lambda r: (r["state"] != "verified", r.get("coverage") != "full", -len(r.get("skills") or []),
                                     bool(r.get("errors")), -(r.get("quality") or 0), not r["preferred"], -r["received"]))
            if any(r["state"] == "verified" for r in rows):
                rows = [r for r in rows if r["state"] == "verified"]       # unchecked ones only when nothing was verified
            candidates[c["id"]] = best_rows(rows, 5 if c.get("in_scope", True) else 2)
        previous = None
        if plan.get("parent_id"):
            parent = self._s.lib.plan(plan["parent_id"])
            previous = (parent or {}).get("result") or None
        self._stage(plan_id, "assemble", 0.88, t("Building your plan ({model})…", model=model_shown(model, level)))
        raw, res = self._ask_json(assemble_prompt(scope, facts, concepts, candidates, previous, dt.date.today(),
                                                  gaps_of(todo_c, verdicts), why), model, cancel, level)
        add_usage("assemble", res)
        result = finalize(raw, concepts, candidates, vids, data["concepts"]["practice"], data["concepts"]["gaps"],
                          verdicts=verdicts, excerpts=excerpts, scope=scope,
                          goal=target(scope, [w["no"] for w in facts["weeks"]], shown=True),
                          goal_en=target(scope, [w["no"] for w in facts["weeks"]]), blocked=prefs.is_blocked, why=why)
        try:                                     # the channels' pictures for the plan's cards (cached for a month)
            pics = videos.channels([it["channel_id"] for it in result["items"] if it.get("channel_id")], cancel=cancel)
        except Exception as exc:  # noqa: BLE001 - a missing picture never fails a plan
            log.info("Channel pictures: %s", exc)
            pics = {}
        for it in result["items"]:
            ch = pics.get(it.get("channel_id") or "")
            it["channel_avatar"] = ch.avatar if ch else ""
        result["listened"] = listened_summary(vids, data.get("listened") or {})
        result["materials_missing"] = facts.get("materials_missing", [])
        result["days_missing"] = len(facts.get("days_missing", []))
        result["days_used"] = len(facts.get("days_used", []))
        result["days_left_out"] = len(facts.get("days_left_out", []))
        result["materials_used"] = len(facts.get("materials_used", []))
        try:
            result["file"] = str(self._write_markdown(plan, result, facts, scope, data, save))
        except OSError:
            log.warning("Could not write the plan file", exc_info=True)
        save(result=result, title=result["title"], status="done", stage="done", progress=1.0,
             message=t("{n} videos · {minutes} min", n=len(result["items"]), minutes=result["total_minutes"]))

    def _judge(self, plan_id: str, stage: str, work: list[tuple[str, list[dict], dict[str, dict[str, list]]]],
               vids: dict[str, dict], finished: dict[str, dict], who: tuple[str, str, str], cancel: threading.Event,
               save: Callable[..., None], add_usage: Callable[[str, dict], None], span: tuple[float, float]) -> None:
        """The verification requests of ``work`` - (key, concepts, their caption parts per video) - VERIFY_PARALLEL at
        a time; each one's checked verdicts go into ``finished`` under its key (saved at once: a plan stopped meanwhile
        keeps them). ``who``: the model, its reasoning level and how the progress names them."""
        model, level, shown = who
        lo, hi = span
        left = [w for w in work if w[0] not in finished]

        def judge(w: tuple[str, list[dict], dict[str, dict[str, list]]]) -> tuple[dict, dict]:
            _, batch, parts = w
            asked = {c["id"]: set(parts.get(c["id"], {})) for c in batch}
            raw, res = self._ask_json(verify_prompt(batch, vids, parts), model, cancel, level)
            return clean_verdicts(raw, asked, vids, batch, parts), res

        say = (lambda n, total: t("Checking the captions against the course ({n}/{total}, {model})…", n=n, total=total,
                                  model=shown)) if stage == "verify" else \
            (lambda n, total: t("Checking more videos against the course ({n}/{total}, {model})…", n=n, total=total,
                                model=shown))
        if stage == "verify":
            first = tn(len(left), "Checking the captions against the course ({n} request, {model})…",
                       "Checking the captions against the course ({n} requests, {model})…", model=shown)
        else:
            first = say(len(work) - len(left), len(work))
        self._stage(plan_id, stage, lo + (hi - lo) * (len(work) - len(left)) / max(1, len(work)), first)
        with ThreadPoolExecutor(max_workers=VERIFY_PARALLEL, thread_name_prefix="plan-verify") as pool:
            futures = {pool.submit(judge, w): w for w in left}
            try:
                for f in as_completed(futures):
                    per_concept, res = f.result()
                    add_usage(stage, res)
                    finished[futures[f][0]] = per_concept
                    save()
                    n = sum(w[0] in finished for w in work)
                    self._stage(plan_id, stage, lo + (hi - lo) * n / len(work), say(n, len(work)))
            except BaseException:
                for f in futures:              # one failed: don't start the ones still waiting
                    f.cancel()
                raise

    def _more(self, plan_id: str, scope: Scope, facts: dict[str, Any], todo_c: list[dict], cands: dict[str, list[dict]],
              qs_of: dict[str, list[str]], base_vids: dict[str, dict], base_excerpts: dict[str, dict[str, list]],
              base_verdicts: dict[str, dict[str, dict]], prefs: Any, langs: tuple[str, ...], data: dict[str, Any],
              cancel: threading.Event, save: Callable[..., None], add_usage: Callable[[str, dict], None],
              who: tuple[str, str, str]) -> dict[str, Any]:
        """More searches, candidates and checks for what no checked video teaches yet: round after round (at most
        MORE_ROUNDS), each concept with a gap gets new searches (the AI's, aimed at the missing skills: other words,
        Turkish, the skills themselves; made in code when the AI wrote none), its best new candidates - also the ones
        the first search found but didn't read, and videos read for other concepts that talk about it -, listening
        where their captions can't be used, and a check; until nothing is missing or a round finds nothing new to
        check (the search is exhausted). Returns what was found: {"videos", "excerpts", "verdicts", "hits", "queries",
        "rounds", "failed"}. The rounds' finished requests are kept in data["more_parts"] (a plan stopped meanwhile
        goes on from them: the searches and videos come from the cache)."""
        from . import videos

        parts = data.setdefault("more_parts", {})
        by_id = {c["id"]: c for c in todo_c}
        mv: dict[str, dict] = {}                                      # the videos read in these rounds
        mx: dict[str, dict[str, list]] = {}                           # their caption parts, per concept
        mvd: dict[str, dict[str, dict]] = {}                          # their verdicts
        hits_of: dict[str, list[dict]] = {}                           # the new candidates, per concept
        tried = {cid: list(qs) for cid, qs in qs_of.items()}
        rounds: list[dict[str, Any]] = []
        failed: dict[str, str] = {}
        capped: dict[str, int] = {}                                   # concepts the plan's limits left candidates
        checked: dict[str, int] = {}                                  # unchecked for, and got checks for: the round
        stopped: set[str] = set()                                     # concepts the limits stopped looking for
        requests = inspected = 0
        lang = "" if scope.video_lang == "both" else scope.video_lang

        def norm(q: str) -> str:
            return " ".join(q.casefold().split())

        def row(vid: str) -> dict[str, Any]:
            return {**(base_vids.get(vid) or {}), **(mv.get(vid) or {})}

        def asked(cid: str) -> set[str]:
            return {*(base_verdicts.get(cid) or {}), *(mvd.get(cid) or {}), *(base_excerpts.get(cid) or {}),
                    *(mx.get(cid) or {})}

        def verdicts_now() -> dict[str, dict[str, dict]]:
            return {cid: {**(base_verdicts.get(cid) or {}), **(mvd.get(cid) or {})} for cid in {*base_verdicts, *mvd}}

        for r in range(1, MORE_ROUNDS + 1):
            if cancel.is_set():
                raise Cancelled()
            gaps = gaps_of(todo_c, verdicts_now())
            if not gaps:
                break
            if r > 1 and requests >= MORE_REQUESTS:      # nothing more can be checked: no more searches either (they
                stopped.update(gaps)                     # would find only what nobody reads)
                break
            searching = r == 1 or inspected < MORE_VIDEOS     # no new video can be read: only the ones read, again
            # what matters most first (the plan's limits may not reach the last ones): what the focus needs, the core
            # concepts, then the ones missing the most
            rank = {"core": 0, "supporting": 1, "prerequisite": 2}
            gaps = dict(sorted(gaps.items(), key=lambda g: (not by_id[g[0]].get("in_scope", True), not by_id[g[0]].get("focus"),
                                                            rank.get(by_id[g[0]].get("importance"), 1),
                                                            -max(1, len(g[1])))))
            lo, hi = 0.74 + 0.04 * (r - 1), 0.74 + 0.04 * r
            self._stage(plan_id, "more", lo, tn(len(gaps), "More searches for {n} concept no checked video covers yet…",
                                                "More searches for {n} concepts no checked video covers yet…"))
            if "queries" not in parts:                                # the AI's searches, once for all the rounds
                try:
                    raw, res = self._ask_json(more_queries_prompt(scope, facts, todo_c, gaps, tried), who[0], cancel,
                                              who[1])
                    add_usage("more", res)
                    parts["queries"] = clean_more_queries(raw, gaps, tried)
                except Cancelled:
                    raise
                except Exception as exc:  # noqa: BLE001 - the searches are made in code then
                    if cancel.is_set():
                        raise Cancelled() from exc
                    log.warning("The AI wrote no more searches (%s): made in code", exc)
                    parts["queries"] = {}
                save()
            new_q: dict[str, list[str]] = {}
            for cid, missing in gaps.items():
                ai = list(parts["queries"].get(cid) or [])
                if scope.video_lang == "both" and len(ai) > 1:
                    ai = [ai[0], ai[-1], *ai[1:-1]]                    # (its Turkish one, the last, comes early)
                done = {norm(q) for q in tried.get(cid, [])}
                fresh = [q for q in dict.fromkeys([*ai, *fallback_queries(by_id[cid], missing)]) if norm(q) not in done]
                new_q[cid] = fresh[:2] if searching else []
                tried.setdefault(cid, []).extend(new_q[cid])
            queries = sorted({q for qs in new_q.values() for q in qs})

            def on_search(n: int, total: int, label: str) -> None:
                self._stage(plan_id, "more", lo + (hi - lo) * 0.25 * n / max(1, total),
                            t("Searching YouTube for what isn't covered yet: “{query}” ({n}/{total})", query=label,
                              n=n, total=total))

            found = videos.search_many(queries, limit=8, cancel=cancel, progress=on_search) if queries else {}
            failed.update({q: res.kind for q, res in found.items() if isinstance(res, videos.VideoError)})
            mine = [(ch["id"], new_q[cid][0]) for cid in gaps if new_q.get(cid) and by_id[cid].get("in_scope", True)
                    for ch in prefs.allow[:PREFERRED_SEARCHED]]
            own = videos.channel_search_many(mine, cancel=cancel) if mine else {}
            if cancel.is_set():
                raise Cancelled()
            pairs: dict[str, list[str]] = {}
            for cid, missing in gaps.items():
                c = by_id[cid]
                pool: list[Any] = []
                for q in new_q.get(cid, []):
                    got = found.get(q)
                    pool += [] if got is None or isinstance(got, Exception) else list(got)
                for key, got in own.items():
                    if new_q.get(cid) and key[1] == new_q[cid][0] and not isinstance(got, Exception):
                        pool += list(got)
                pool += [_hit(h) for h in cands.get(cid, []) if (base_vids.get(h["id"]) or {}).get("uninspected")]
                skip, uniq = asked(cid), {}
                for h in pool:
                    bad = row(h.id).get("error") or row(h.id).get("wrong_lang")
                    if h.id not in skip and not bad and h.id not in uniq:
                        uniq[h.id] = h
                pick = videos.rank(list(uniq.values()), find_terms(c, missing), allow=prefs.is_allowed,
                                   block=prefs.is_blocked, lang=lang)[:MORE_PER_CONCEPT]
                known = {h["id"] for h in hits_of.get(cid, [])}
                hits_of.setdefault(cid, []).extend(_hit_json(h) for h in pick if h.id not in known)
                pairs[cid] = [h.id for h in pick]
            ids = [vid for vid in dict.fromkeys(v for vs in pairs.values() for v in vs)
                   if vid not in mv and (vid not in base_vids or base_vids[vid].get("uninspected"))]
            cut = set(ids[max(0, MORE_VIDEOS - inspected):])               # beyond the plan's limit of videos read
            ids = ids[:max(0, MORE_VIDEOS - inspected)]
            capped.update({cid: r for cid, vs in pairs.items() if set(vs) & cut})

            def on_inspect(n: int, total: int, label: str) -> None:
                self._stage(plan_id, "more", lo + (hi - lo) * (0.25 + 0.2 * n / max(1, total)),
                            t("Reading captions: {title} ({n}/{total})", title=label[:60], n=n, total=total))

            infos = videos.inspect_many(ids, langs=langs, cancel=cancel, progress=on_inspect) if ids else {}
            inspected += len(ids)
            for vid, info in infos.items():
                mv[vid] = video_row(info, langs)
            if cancel.is_set():
                raise Cancelled()
            # videos read before (for another concept) that talk about what is missing, and the new ones
            readable = [vid for vid in dict.fromkeys([*base_vids, *mv]) if row(vid).get("has_captions")
                        and not row(vid).get("wrong_lang") and not row(vid).get("error")
                        and not row(vid).get("caption_problem")]
            known_infos = videos.inspect_many([v for v in readable if v not in infos], langs=langs, cancel=cancel)
            known_infos.update(infos)
            for cid, missing in gaps.items():
                pairs[cid] = [v for v in pairs[cid] if v in known_infos or v in mv]
                scored = []
                for vid in readable:
                    info = known_infos.get(vid)
                    if vid in asked(cid) or vid in pairs[cid] or info is None or isinstance(info, Exception):
                        continue
                    wins = videos.windows(info, find_terms(by_id[cid], missing), top=1)
                    if wins and wins[0]["score"] >= CROSS_SCORE:
                        scored.append((wins[0]["score"], vid))
                pairs[cid] += [vid for _, vid in sorted(scored, key=lambda x: -x[0])[:MORE_CROSS]]
            hear: dict[str, dict[str, Any]] = {}
            for cid, missing in gaps.items():
                for vid in pairs[cid]:
                    rw = row(vid)
                    if rw.get("error") or rw.get("wrong_lang"):
                        continue
                    info = known_infos.get(vid)
                    if rw.get("caption_problem") and vid in mv:
                        hear.setdefault(vid, {"video": vid, "problem": rw["caption_problem"], "concepts": []})
                        hear[vid]["concepts"].append(cid)
                    if info is not None and not isinstance(info, Exception) and info.has_captions:
                        mx.setdefault(cid, {})[vid] = excerpt_parts(info, find_terms(by_id[cid], missing), 3)
            listened = self._listen(plan_id, "more", list(hear.values()), mv, mx, by_id, langs, data, cancel, save,
                                    missing=gaps, span=(lo + (hi - lo) * 0.45, lo + (hi - lo) * 0.65))
            work: list[tuple[str, list[dict], dict[str, dict[str, list]]]] = []
            batch: list[dict] = []
            part: dict[str, dict[str, list]] = {}
            for cid in gaps:
                new = {vid: x for vid, x in (mx.get(cid) or {}).items() if vid not in (mvd.get(cid) or {})}
                if not new:
                    continue
                if batch and sum(len(x) for x in part.values()) + len(new) > MORE_PAIRS:
                    work.append((f"r{r} " + " ".join(f"{k}:{','.join(sorted(v))}" for k, v in part.items()), batch, part))
                    batch, part = [], {}
                batch.append(by_id[cid])
                part[cid] = new
            if batch:
                work.append((f"r{r} " + " ".join(f"{k}:{','.join(sorted(v))}" for k, v in part.items()), batch, part))
            left_out = work[max(0, MORE_REQUESTS - requests):]
            work = work[:max(0, MORE_REQUESTS - requests)]
            capped.update({cid: r for _, b, _ in left_out for cid in (c["id"] for c in b)})
            if not searching:                           # (nothing read for it again either: the limits stopped it)
                stopped.update(cid for cid in gaps if not any(cid in part_ for _, _, part_ in work))
            rounds.append({"round": r, "gaps": {cid: len(m) for cid, m in gaps.items()}, "queries": new_q,
                           "inspected": len(ids), "heard": len(listened["heard"]),
                           "pairs": sum(len(x) for _, _, ps in work for x in ps.values()), "requests": len(work)})
            if not work:
                break                                                  # nothing new to check: the search is exhausted
            finished = parts.setdefault("verdicts", {})
            self._judge(plan_id, "more", work, {**base_vids, **mv}, finished, who, cancel, save, add_usage,
                        (lo + (hi - lo) * 0.65, hi))
            requests += len(work)
            for k, _, _ in work:
                for cid, per in finished.get(k, {}).items():
                    mvd.setdefault(cid, {}).update(per)
                    if per:
                        checked[cid] = r
            save()
        left = gaps_of(todo_c, verdicts_now())
        # left unchecked by the limits after its last check (a concept checked in a later round was looked at again)
        cut_off = sorted(cid for cid, at in capped.items() if cid in left and at >= checked.get(cid, 0))
        return {"videos": mv, "excerpts": mx, "verdicts": mvd, "hits": hits_of, "rounds": rounds, "failed": failed,
                "capped": cut_off, "stopped": sorted(cid for cid in stopped if cid in left and cid not in cut_off),
                "queries": {cid: qs[len(qs_of.get(cid, [])):] for cid, qs in tried.items() if qs[len(qs_of.get(cid, [])):]}}

    def _why(self, todo_c: list[dict], cands: dict[str, list[dict]], qs_of: dict[str, list[str]], vids: dict[str, dict],
             verdicts: dict[str, dict[str, dict]], more: dict[str, Any],
             search_failed: dict[str, str]) -> dict[str, dict[str, str]]:
        """For each concept with something no checked video teaches: why the search found no good video for it, from
        what was searched, found, checked, listened to and refused."""
        from .videos import BLOCKING_KINDS

        failed = {**search_failed, **(more.get("failed") or {})}
        out: dict[str, dict[str, str]] = {}
        skills_of = {c["id"]: list(c.get("skills") or []) for c in todo_c}
        for cid, missing in gaps_of(todo_c, verdicts).items():
            queries = {*qs_of.get(cid, []), *((more.get("queries") or {}).get(cid) or [])}
            found = {h["id"] for h in cands.get(cid, [])} | {h["id"] for h in (more.get("hits") or {}).get(cid) or []}
            checked = set(verdicts.get(cid) or {})
            unheard: dict[str, str] = {}
            other = refused = silent = 0
            for vid in found:
                rw = vids.get(vid) or {}
                if rw.get("wrong_lang"):
                    other += 1
                elif rw.get("error"):
                    refused += 1
                elif vid not in checked and (rw.get("listen") or {}).get("skipped") == "no_speech":
                    silent += 1                   # listened to: nothing to hear
                elif vid not in checked and (rw.get("listen") or {}).get("skipped"):
                    unheard[vid] = rw["listen"]["skipped"]
            blocked = (any(failed.get(q) in BLOCKING_KINDS for q in queries)
                       or any((vids.get(v) or {}).get("error_kind") in BLOCKING_KINDS for v in found))
            some = missing and len(missing) < len(skills_of.get(cid) or [])   # (its other skills: a video teaches them)
            text, en = why_no_video(len(queries), len(found), len(checked), unheard, other, refused, blocked,
                                    capped=cid in set(more.get("capped") or []),
                                    stopped=cid in set(more.get("stopped") or []), silent=silent,
                                    what=list(missing) if some else None)
            out[cid] = {"text": text, "en": en}
        return out

    def _listen(self, plan_id: str, stage: str, todo: list[dict[str, Any]], vids: dict[str, dict],
                excerpts: dict[str, dict[str, list]], by_id: dict[str, dict], langs: tuple[str, ...],
                data: dict[str, Any], cancel: threading.Event, save: Callable[..., None],
                missing: Optional[dict[str, list[str]]] = None, span: tuple[float, float] = (0.55, 0.62)) -> dict:
        """Listens to the videos of ``todo`` ([{"video", "problem", "concepts"}], most important first) as far as the
        plan's limits allow (listen.py), and reads what was heard like captions: the videos' rows in ``vids`` and their
        parts in ``excerpts`` (for each concept that chose them) are made from it. ``missing``: per concept, only the
        skills to look for (a round of more searches). Returns {"heard": [ids], "skipped": {id: why}}."""
        from . import listen as hearing, videos

        out: dict[str, Any] = {"heard": [], "skipped": {}}
        if not todo:
            return out
        used = data.setdefault("listened", {})
        lo, hi = span
        steps = {"download": N_("downloading its sound"), "model": N_("loading the speech model"),
                 "transcribe": N_("transcribing it")}
        shown = [0.0, ""]                      # when the progress was last saved, and of what (a download's every chunk
                                               # calls it: twice a second is plenty)

        def progress(i: int, n: int, step: str, frac: float) -> None:
            now, what = time.monotonic(), f"{i} {step}"
            if what == shown[1] and now - shown[0] < 0.5:
                return
            shown[0], shown[1] = now, what
            head = tn(n, "Listening to {n} video without captions: {i} of {n}",
                      "Listening to {n} videos without captions: {i} of {n}", i=i)
            self._stage(plan_id, stage, lo + (hi - lo) * (i - 1 + max(0.0, min(1.0, frac))) / max(1, n),
                        f"{head} · {t(steps.get(step, steps['transcribe']))}")

        rows = {x["video"]: x for x in todo}
        heard = hearing.listen(getattr(self._s, "jobs", None), self._s.settings,
                               [(vid, SimpleNamespace(duration_s=(vids.get(vid) or {}).get("duration_s") or 0,
                                                     language=(vids.get(vid) or {}).get("language") or ""),
                                 x.get("problem", "")) for vid, x in rows.items()],
                               used, langs, cancel, progress)
        save()
        for vid, res in heard.items():
            x = rows[vid]
            if not res.get("heard"):
                vids[vid]["listen"] = {"skipped": res.get("skipped") or "failed"}
                out["skipped"][vid] = vids[vid]["listen"]["skipped"]
                continue
            try:
                info = videos.inspect(vid, langs=langs, cancel=cancel)        # (from the cache: with what was heard)
            except videos.VideoError as exc:
                log.info("Couldn't read %s again after listening: %s", vid, exc)
                vids[vid]["listen"] = {"skipped": "failed"}
                out["skipped"][vid] = "failed"
                continue
            vids[vid] = {**vids[vid], **video_row(info, langs), "listen": {"heard": True}}
            for cid in x.get("concepts") or []:
                c = by_id.get(cid)
                if c is not None and info.has_captions:
                    excerpts.setdefault(cid, {})[vid] = excerpt_parts(
                        info, find_terms(c, (missing or {}).get(cid)), 3 if c.get("in_scope", True) else 2)
            out["heard"].append(vid)
        return out

    def _reusable(self, parent_id: str, scope: Scope, facts: dict[str, Any]) -> dict[str, Any]:
        """The finished stages of the plan being updated, when only the viewing time or the deadline changed and the
        course material is the same: the concepts and the checked videos would be the same, so only the plan itself
        is built again."""
        parent = self._s.lib.plan(parent_id) or {}
        old = parent.get("data") or {}

        def rest(s: Scope) -> dict[str, Any]:
            return {**s.to_json(), "minutes": 0, "deadline": ""}

        same = old.get("gather") == json.loads(json.dumps(facts))          # as it was saved
        if not same or rest(Scope.from_json(parent.get("scope") or {})) != rest(scope):
            return {}
        out: dict[str, Any] = {}
        for st in STAGE_NAMES[1:-1]:                    # concepts … more (a plan from before listening: up to inspect)
            if st not in old:
                partial = {"verify": "verify_parts", "more": "more_parts"}.get(st, "")
                if partial in old:
                    out[partial] = old[partial]
                break
            out[st] = old[st]
            extra = {"search": "search_failed", "listen": "listened"}.get(st, "")
            if extra in old:
                out[extra] = old[extra]
        return out

    def _write_prep(self, plan: dict[str, Any], scope: Scope, facts: dict[str, Any], model: str,
                    cancel: threading.Event, add_usage: Callable[[str, dict], None], save: Callable[..., None],
                    data: Optional[dict[str, Any]] = None) -> None:
        """An exam review sheet: one request that writes a Markdown sheet from the gathered material."""
        from .ai import one_shot

        pid = plan["id"]
        self._stage(pid, "write", 0.15, t("Writing your exam review sheet ({model})…", model=model_shown(model, levels(scope, self._s.settings)[0])))
        progress = [0.15, 0.0]

        def on_text(so_far: str) -> None:       # the sheet is long: show that it is being written
            now = time.monotonic()
            if now - progress[1] > 1.5:
                progress[1] = now
                progress[0] = min(0.95, 0.15 + len(so_far) / 30_000)
                self._stage(pid, "write", progress[0],
                            t("Writing your exam review sheet… ({k}k characters)", k=len(so_far) // 1000))

        prompt = prep_prompt(scope, facts)
        run = self._ask_text or one_shot
        res = run(SYSTEM_PREP, [{"type": "text", "text": prompt}], model, levels(scope, self._s.settings)[0], cancel,
                  on_text)
        add_usage("write", res)
        md = (res.get("text") or "").strip()
        if len(md) < 200:
            raise RuntimeError(t("The AI returned an empty review sheet. Try again."))
        title = prep_title(scope, facts, shown=True)
        english = prep_title(scope, facts)                     # the file's name and heading stay English
        result = {"kind": "prep", "title": title, "markdown": md,
                  "materials_missing": facts.get("materials_missing", []),
                  "materials_used": len(facts.get("materials_used", [])), "days_used": len(facts.get("days_used", [])),
                  "days_left_out": len(facts.get("days_left_out", []))}
        try:
            result["file"] = str(self._write_file(plan, english, f"# {english}\n\n*{facts['course']['code']} · made with "
                                                  f"IEUStudia on {dt.date.today():%d %b %Y}*\n\n{md}\n", "Review sheets",
                                                  data, save))
        except OSError:
            log.warning("Could not write the review sheet file", exc_info=True)
        save(result=result, title=title, status="done", stage="done", progress=1.0, message=t("Review sheet ready"))

    def _write_file(self, plan: dict[str, Any], title: str, text: str, sub: str,
                    data: Optional[dict[str, Any]] = None, save: Callable[[], None] = lambda: None) -> Path:
        """<course>/<sub>/<date> - <title>.md: a name of its own (studypack.file_stem: " (2)" when another plan's file
        has it), kept in ``data`` and saved before the file is written, so writing again keeps it."""
        from .outputs.mathmd import dollar_math
        from .outputs.writer import course_dir
        from .studypack import file_stem

        course = self._s.lib.get_course(plan["course_id"])
        folder = course_dir(self._s.output_root, course, self._s.lib.courses(True)) / sub
        folder.mkdir(parents=True, exist_ok=True)
        data = {} if data is None else data
        if not data.get("stem"):
            file_stem(plan, title, folder, (".md",), data)          # a long title keeps its .md
            save()
        path = folder / (data["stem"] + ".md")
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(dollar_math(text).encode("utf-8"))
        tmp.replace(path)
        return path

    def _write_markdown(self, plan: dict[str, Any], result: dict[str, Any], facts: dict[str, Any], scope: Scope,
                        data: Optional[dict[str, Any]] = None, save: Callable[[], None] = lambda: None) -> Path:
        return self._write_file(plan, result["title"], plan_markdown(result, facts["course"], scope), "Study plans",
                                data, save)


def estimate(lib: Any, settings: Any, course_id: str, scope: dict[str, Any]) -> dict[str, Any]:
    """Rough cost before running: tokens per stage and what the plan will be based on."""
    from .ai import PROMPT_OVERHEAD, chars_per_token

    s, per = Scope.from_json(scope), chars_per_token()
    if s.kind in ("pack", "sheet"):
        from . import cheatsheet, studypack

        return (cheatsheet if s.kind == "sheet" else studypack).estimate(lib, settings, course_id, s)
    facts = gather(lib, settings, course_id, s)
    pack = int(facts["chars"] / per) + PROMPT_OVERHEAD + 900
    if s.kind == "prep":
        return {"main_model": main_model(s, settings), "verify_model": "", "main_input": pack,
                "effort": levels(s, settings)[0], "verify_effort": "", "verify_requests": 0,
                "main_output": 7000, "verify_input": 0, "verify_output": 0, "concepts": 0, "weeks": facts["weeks"],
                "materials_used": facts["materials_used"], "materials_missing": facts["materials_missing"],
                "days_used": facts["days_used"], "days_left_out": facts["days_left_out"],
                "minutes": t("about 1–3 minutes")}
    n = max(6, min(MAX_CONCEPTS, 4 + 3 * len(facts["weeks"])))
    m = n if not focused(s) or s.extras else max(4, round(n * 0.6))          # the concepts that get videos
    batches = -(-m // VERIFY_BATCH)
    excerpts_in = int((INSPECT + 1) * 2.5 * EXCERPT_CHARS / per)   # per concept: ~4 videos, 2–3 parts each
    verify_in = batches * (PROMPT_OVERHEAD + 900 + VERIFY_BATCH * excerpts_in)
    # the more searches: about half the concepts still miss a skill after the first check; for them the searches'
    # request, then their new candidates checked MORE_PAIRS at a time
    pairs = (m + 1) // 2 * (MORE_PER_CONCEPT + 1)
    more = min(MORE_REQUESTS, -(-pairs // MORE_PAIRS))
    more_in = PROMPT_OVERHEAD + 300 * m + more * (PROMPT_OVERHEAD + 900 + int(MORE_PAIRS * 2.5 * EXCERPT_CHARS / per))
    assemble_in = PROMPT_OVERHEAD + 1800 + m * 380
    effort, verify_effort = levels(s, settings)
    return {"main_model": main_model(s, settings), "verify_model": getattr(settings, "verify_model", "") or VERIFY_MODEL,
            "effort": effort, "verify_effort": verify_effort, "verify_requests": batches + 1 + more,
            "main_input": pack + assemble_in, "main_output": n * 220 + 1500 + 5000, "verify_input": verify_in + more_in,
            "verify_output": (batches * VERIFY_BATCH * 4 + pairs) * 220 + 60 * m, "concepts": n,
            "weeks": facts["weeks"], "materials_used": facts["materials_used"],
            "materials_missing": facts["materials_missing"], "days_used": facts["days_used"],
            "days_left_out": facts["days_left_out"],
            # measured on three weeks of 20 concepts each (ChatGPT, medium): 12–19 minutes, of which listening and the
            # more searches 4–9
            "minutes": t("about 10–20 minutes")}
