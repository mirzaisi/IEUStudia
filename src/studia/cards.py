"""Course flashcards with spaced repetition: one deck per course, made of the flashcards the AI wrote for its lectures
(each lecture's "Flashcards" notes, read by outputs/flashcards.py) and the cards the AI makes from files the student
picks (a plan of kind "cards", run in the background like a study pack). Every card keeps where it comes from: the
lecture's moment, or the file's page.

Reviews follow a simple SM-2, as Anki does: a new card comes back after 1 and 10 minutes, then after a day; Again
starts it over, Hard, Good and Easy space it out by the card's ease. 20 new cards a day per course at most
(Settings → Practice changes it). A day runs from 04:00 to 04:00, as Anki's: a sitting past midnight is still the
evening's. What each student knows is kept in their own profile's library (card_state, card_reviews): several
students share a PC, and the page's own storage is the same for every profile on it.

The questions a student misses in a practice quiz join the deck too (origin "quiz", Settings → Practice can turn it
off): each comes back the next day, then a few days later. A multiple-choice one comes back as multiple choice, its
options in another order each time, and the app marks it (right is Good, wrong is Again). Its front is the question
(in the course's language, as the quiz asked it), its back the answer and why (the explanation in the document's
language, as the quiz wrote it). A card can be edited, set aside (kept, but not studied) or deleted: kept per course
in the library's meta ("card-edits:<course>")."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import math
import re
import threading
from pathlib import Path
from typing import Any, Callable, Optional

from .cancel import Cancelled
from .i18n import N_, t, tn

log = logging.getLogger("studia")

GRADES = ("again", "hard", "good", "easy")
NEW_PER_DAY = 20
LEARN_MIN = (1, 10)             # a new card's steps (minutes), then it graduates to a day
RELEARN_MIN = 10                # a forgotten card comes back after 10 minutes
GRADUATE_DAYS, EASY_DAYS = 1, 4
MAX_DAYS = 365
DAY_STARTS = 4                  # a study day starts at 04:00 (as Anki's): what is learnt at 23:50 is due in the morning
NEAR_S = 180                    # a lecture card this close to a stressed moment is a stressed card
STAGES = [("gather", N_("Reading the course material")), ("write", N_("Writing the cards")),
          ("check", N_("Checking the cards"))]
LABELS = dict(STAGES)
BATCH_CHARS = 45_000            # file text one cards request reads (~13k tokens)
MAX_CARDS = 120                 # made from one set of files at most
QUIZ_CAP = 10                   # missed questions of one quiz try that join the deck at most
SYSTEM = ("You make flashcards for a university student, only from the course material you are given, in its "
          "notation. You answer with the JSON asked for, maths in LaTeX, and never ask questions back.")


class Failed(RuntimeError):
    """A card set that couldn't be made, for a reason of its own. Its message stays with the set (the plan's error is
    the exception's text) as the English words t() looks up, with {ai} left for the page to fill in: the Practice tab
    says it in the language of the moment (practice.js), not in the one it was made in."""


def norm(text: str) -> str:
    return re.sub(r"[\W_]+", " ", str(text or "").lower()).strip()


def _cid(prefix: str, key: str, q: str) -> str:
    return prefix + hashlib.sha1(f"{key}|{norm(q)}".encode("utf-8")).hexdigest()[:15]


def quiz_card_id(plan_id: str, question: str) -> str:
    """The card a quiz's question becomes when it is missed: the same one for every try of that quiz."""
    return _cid("q", plan_id, question)


# ------------------------------------------------------------------ cards from missed quiz questions
def missed_questions(result: dict[str, Any], answers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The questions of one quiz try that join the deck: those not fully right, not removed from the quiz, each once,
    QUIZ_CAP at most (in the order they were asked)."""
    removed = set(result.get("removed") or [])
    qs = {q.get("n"): q for q in result.get("questions") or [] if isinstance(q, dict)}
    out, seen = [], set()
    for a in answers:
        if not isinstance(a, dict) or float(a.get("got") or 0) >= 1:
            continue
        q = qs.get(a.get("n"))
        if not q or q.get("n") in removed or not norm(q.get("q")) or norm(q["q"]) in seen:
            continue
        seen.add(norm(q["q"]))
        out.append(q)
        if len(out) >= QUIZ_CAP:
            break
    return out


def quiz_back(q: dict[str, Any]) -> str:
    """A missed question's card back, as text (Anki, the deck's "a"): the right answer and why. No words of the app's
    own: the answer is in the course's language and the why in the document's, as the quiz wrote them."""
    kind = q.get("type")
    if kind == "mc":
        opts = q.get("options") or []
        right = opts[q["answer"]] if isinstance(q.get("answer"), int) and 0 <= q["answer"] < len(opts) else ""
        return "\n\n".join(x for x in (f"**{right}**" if right else "", q.get("why") or "") if x)
    if kind == "problem":
        steps = "\n".join(f"{i}. {s}" for i, s in enumerate(q.get("steps") or [], 1))
        return "\n\n".join(x for x in (steps, f"**{q['model']}**" if q.get("model") else "") if x)
    points = "\n".join(f"- {p}" for p in q.get("points") or [])
    return "\n\n".join(x for x in (q.get("model") or "", points, q.get("why") or "") if x)


def quiz_item(plan: dict[str, Any], q: dict[str, Any]) -> dict[str, Any]:
    """A missed question as a card of the deck: front, back and source as any card's, and the question itself
    ("quiz") so a multiple-choice one is asked again with its options."""
    src = q.get("source") or {}
    weeks = (plan.get("scope") or {}).get("weeks") or []
    return {"id": quiz_card_id(plan["id"], q["q"]), "q": q["q"], "a": quiz_back(q), "origin": "quiz", "source": src,
            "week": src.get("week") or (max(weeks) if weeks else None), "stressed": bool(q.get("stressed")),
            "quiz": {"plan_id": plan["id"], "n": q.get("n"), "type": q.get("type"), "title": plan.get("title") or "",
                     **{k: q[k] for k in ("options", "answer", "wrong", "why", "model", "points", "steps") if k in q}}}


_quiz_cache: dict[tuple[str, str], tuple[Any, list[dict[str, Any]]]] = {}


def _quiz_items(lib: Any, course_id: str) -> list[dict[str, Any]]:
    """The deck's cards from the course's missed quiz questions (oldest try first), worked out again only when a
    quiz or a try changed."""
    from . import practice

    found = practice.quiz_results(lib, course_id)
    with lib._lock:
        n, last = lib.db.execute("SELECT COUNT(*), MAX(finished_at) FROM quiz_attempts WHERE course_id=?",
                                 (course_id,)).fetchone()
    stamp = (n, last, practice.generation(), tuple((pid, x["brief"].get("updated_at")) for pid, x in found.items()))
    key = (str(getattr(lib, "path", id(lib))), course_id)
    hit = _quiz_cache.get(key)
    if hit and hit[0] == stamp:
        return hit[1]
    out: list[dict[str, Any]] = []
    for a in reversed(practice.attempts(lib, course_id=course_id) if n else []):
        x = found.get(a["plan_id"])
        if x:
            out += [quiz_item(x["brief"], q) for q in missed_questions(x["result"], a["answers"])]
    _quiz_cache[key] = (stamp, out)
    return out


def add_missed(lib: Any, course_id: str, plan_id: str, result: dict[str, Any], answers: list[dict[str, Any]],
               now: Optional[dt.datetime] = None) -> list[str]:
    """A quiz try's missed questions join the deck: each new one is due the next study day, as if learnt (interval 1
    day), so it comes back tomorrow, then a few days later; one missed again comes back tomorrow at the latest. Not
    one the student deleted. The ids of the cards new in the deck (for the page's Undo)."""
    now = now or dt.datetime.now()
    due = (study_day(now)[0] + dt.timedelta(days=1)).isoformat(timespec="seconds")
    gone = {k for k, v in edits(lib, course_id).items() if v.get("off") == "deleted"}
    ids = [quiz_card_id(plan_id, q["q"]) for q in missed_questions(result, answers)]
    ids = [i for i in ids if i not in gone]
    with lib._lock:
        had = {r[0] for r in lib.db.execute(f"SELECT card_id FROM card_state WHERE card_id IN ({','.join('?' * len(ids))})",
                                            ids)} if ids else set()
        for cid in ids:
            # a new one: "first" left empty, so it doesn't count among the new cards started today
            lib.db.execute("INSERT INTO card_state(card_id, course_id, due, interval, ease, reps, lapses, step, relearn, "
                           "first, last) VALUES(?,?,?,1,2.5,0,0,-1,0,'','') ON CONFLICT(card_id) DO UPDATE SET "
                           "due=MIN(card_state.due, excluded.due), interval=MIN(card_state.interval, 1)",
                           (cid, course_id, due))
        lib.db.commit()
    return [i for i in ids if i not in had]


# ------------------------------------------------------------------ a card edited, set aside or deleted
_edit_lock = threading.Lock()


def edits(lib: Any, course_id: str) -> dict[str, dict[str, Any]]:
    """The course's cards the student changed: {card id: {"q", "a" (edited), "off": "aside" | "deleted"}}."""
    try:
        got = json.loads(lib.get_meta(f"card-edits:{course_id}") or "{}")
    except ValueError:
        got = {}
    return {str(k): v for k, v in got.items() if isinstance(v, dict)} if isinstance(got, dict) else {}


def edit(lib: Any, course_id: str, card_id: str, q: Optional[str] = None, a: Optional[str] = None,
         off: Optional[str] = None) -> dict[str, Any]:
    """Change a card: its front or back (``q``, ``a``), or ``off``: "aside" (kept, not studied), "deleted" (gone
    from the deck; what was learnt of it goes too) or "" (back in the deck)."""
    with _edit_lock:
        all_ = edits(lib, course_id)
        e = dict(all_.get(card_id) or {})
        if q is not None and q.strip():
            e["q"] = q.strip()[:2000]
        if a is not None and a.strip():
            e["a"] = a.strip()[:4000]
        if off is not None:
            if off in ("aside", "deleted"):
                e["off"] = off
            else:
                e.pop("off", None)
        if e:
            all_[card_id] = e
        else:
            all_.pop(card_id, None)
        lib.set_meta(f"card-edits:{course_id}", json.dumps(all_, ensure_ascii=False))
    if off == "deleted":
        with lib._lock:
            lib.db.execute("DELETE FROM card_state WHERE card_id=?", (card_id,))
            lib.db.commit()
    return e


def bring_back(lib: Any, course_id: str) -> int:
    """Every card set aside back in the deck; how many."""
    with _edit_lock:
        all_ = edits(lib, course_id)
        back = [k for k, v in all_.items() if v.get("off") == "aside"]
        for k in back:
            all_[k].pop("off", None)
            if not all_[k]:
                del all_[k]
        lib.set_meta(f"card-edits:{course_id}", json.dumps(all_, ensure_ascii=False))
    return len(back)


def _edited(cards: list[dict[str, Any]], changes: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for c in cards:
        e = changes.get(c["id"])
        if not e:
            out.append(c)
        elif e.get("off") != "deleted":
            out.append({**c, **{k: e[k] for k in ("q", "a") if e.get(k)}, "edited": bool(e.get("q") or e.get("a")),
                        "aside": e.get("off") == "aside"})
    return out


# ------------------------------------------------------------------ the deck
_parsed: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_parsed_lock = threading.Lock()


def _cards_in(path: str) -> list[dict[str, Any]]:
    """The cards of a lecture's Flashcards notes (read again only when the file changed)."""
    from .outputs.flashcards import parse_cards

    try:
        stamp = Path(path).stat().st_mtime
    except OSError:
        return []
    with _parsed_lock:
        hit = _parsed.get(path)
        if hit and hit[0] == stamp:
            return hit[1]
    try:
        cards = parse_cards(Path(path).read_text(encoding="utf-8"))
    except OSError:
        return []
    with _parsed_lock:
        _parsed[path] = (stamp, cards)
    return cards


def week_finder(lib: Any, course_id: str) -> Callable[[str], Optional[int]]:
    """A date's ("YYYY-MM-DD") week of the course, as the course page counts them (views.course_detail)."""
    from .materials import week_of, week_starts

    first = week_starts(lib.units(course_id))
    course = lib.get_course(course_id)
    if first is None and course is not None and course.term:
        start = lib.term_start(course.term)
        if start:
            first = dt.date.fromisoformat(start)
            first -= dt.timedelta(days=first.weekday())

    def find(day: str) -> Optional[int]:
        try:
            return week_of(dt.date.fromisoformat(day[:10]), first)
        except ValueError:
            return None

    return find


def deck(svc: Any, course_id: str) -> list[dict[str, Any]]:
    """The course's cards, in the order new ones are learnt: what the lecturer stressed first, then by week.
    Each: {"id", "q", "a", "origin" ("lecture", "files" or "quiz"), "source", "week", "stressed"}; a missed quiz
    question also has "quiz" (its options…), an edited card "edited", one set aside "aside". Deleted cards are left
    out."""
    from . import stressed as stressed_mod
    from .outputs.common import format_ts

    lib = svc.lib
    weekof = week_finder(lib, course_id)
    try:
        moments = stressed_mod.course_items(lib, course_id)
    except Exception:  # noqa: BLE001 - the deck without its stressed marks
        log.debug("no stressed moments for the deck", exc_info=True)
        moments = []
    near: dict[tuple[str, int], list[float]] = {}
    for m in moments:
        if m["kind"] != "not_needed":
            near.setdefault((m["day_key"], m["part"]), []).append(m["t"])
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    days = sorted({r.day_key for r in lib.recordings(course_id)}, key=lambda k: (k.split("|") + ["", ""])[1])
    for day in days:
        try:
            path = svc.day_files(day).get("notes:flashcards")
        except (KeyError, OSError):
            continue
        if not path:
            continue
        date = (day.split("|") + ["", ""])[1]
        for c in _cards_in(path):
            if not norm(c["q"]) or norm(c["q"]) in seen:
                continue
            seen.add(norm(c["q"]))
            part, at = c.get("part"), c.get("t")
            hot = at is not None and any(abs(at - x) <= NEAR_S for x in near.get((day, part or 1), []))
            out.append({"id": _cid("l", day, c["q"]), "q": c["q"], "a": c["a"], "origin": "lecture",
                        "source": {"kind": "lecture", "day_key": day, "date": date, "part": part or 1,
                                   "t": at, "ts": format_ts(at) if at is not None else ""},
                        "week": weekof(date), "stressed": hot})
    for brief in lib.plans(course_id):
        if (brief.get("scope") or {}).get("kind") != "cards" or brief.get("status") != "done":
            continue
        plan = lib.plan(brief["id"]) or {}
        for c in (plan.get("result") or {}).get("cards") or []:
            if not norm(c.get("q")) or norm(c["q"]) in seen:
                continue
            seen.add(norm(c["q"]))
            src = c.get("source") or {}
            out.append({"id": c.get("id") or _cid("f", brief["id"], c["q"]), "q": c["q"], "a": c["a"],
                        "origin": "files", "source": src, "week": src.get("week"), "stressed": bool(c.get("stressed"))})
    if getattr(getattr(svc, "settings", None), "mistake_cards", True):     # (Settings → Practice)
        for c in _quiz_items(lib, course_id):
            if norm(c["q"]) not in seen:
                seen.add(norm(c["q"]))
                out.append(c)
    order = sorted(range(len(out)), key=lambda i: (not out[i]["stressed"], out[i]["week"] or 99, i))
    return _edited([out[i] for i in order], edits(lib, course_id))


# ------------------------------------------------------------------ scheduling (SM-2, as Anki)
def study_day(now: dt.datetime) -> tuple[dt.datetime, dt.datetime]:
    """The study day ``now`` is in: from DAY_STARTS o'clock to the same hour the next morning (01:30 is still the
    evening before's)."""
    start = dt.datetime.combine((now - dt.timedelta(hours=DAY_STARTS)).date(), dt.time(DAY_STARTS))
    return start, start + dt.timedelta(days=1)


def _due_day(now: dt.datetime, days: float) -> dt.datetime:
    """The start of the study day ``days`` days after ``now``'s (a card is due for all of its day)."""
    return study_day(now)[0] + dt.timedelta(days=max(1, round(days)))


def schedule(state: Optional[dict[str, Any]], grade: str, now: dt.datetime) -> dict[str, Any]:
    """The card's state after a review: {"due", "interval" (days), "ease", "reps", "lapses", "step" (a learning
    step, -1 once learnt), "relearn", "first", "last"}."""
    if grade not in GRADES:
        raise ValueError(grade)
    s = {"interval": 0.0, "ease": 2.5, "reps": 0, "lapses": 0, "step": 0, "relearn": False,
         "first": now.isoformat(timespec="seconds"), **(state or {})}
    s["reps"] = int(s["reps"]) + 1
    s["last"] = now.isoformat(timespec="seconds")
    ivl, ease = float(s["interval"] or 0), float(s["ease"] or 2.5)
    if int(s["step"]) >= 0:                                   # learning (or relearning)
        steps = (RELEARN_MIN,) if s["relearn"] else LEARN_MIN
        step = min(int(s["step"]), len(steps) - 1)
        if grade == "again":
            s["step"], due = 0, now + dt.timedelta(minutes=steps[0])
        elif grade == "hard":                                 # the same step again, a little later
            later = (steps[step] + steps[step + 1]) / 2 if step + 1 < len(steps) else steps[step] * 1.5
            due = now + dt.timedelta(minutes=later)
        elif grade == "good" and step + 1 < len(steps):
            s["step"], due = step + 1, now + dt.timedelta(minutes=steps[step + 1])
        else:                                                 # good on the last step, or easy: learnt
            days = (max(ivl, 1.0) if s["relearn"] else GRADUATE_DAYS) if grade == "good" else \
                max(EASY_DAYS, ivl * 1.5 if s["relearn"] else EASY_DAYS)
            s.update(step=-1, relearn=False, interval=min(MAX_DAYS, days))
            due = _due_day(now, s["interval"])
    else:                                                     # learnt: spaced out by its ease
        if grade == "again":
            s.update(lapses=int(s["lapses"]) + 1, ease=max(1.3, ease - 0.2), interval=max(1.0, ivl * 0.5), step=0,
                     relearn=True)
            due = now + dt.timedelta(minutes=RELEARN_MIN)
        else:
            if grade == "hard":
                s["ease"], ivl = max(1.3, ease - 0.15), max(ivl + 1, ivl * 1.2)
            elif grade == "good":
                ivl = max(ivl + 1, ivl * ease)
            else:
                s["ease"], ivl = ease + 0.15, max(ivl + 1, ivl * ease * 1.3)
            s["interval"] = min(MAX_DAYS, ivl)
            due = _due_day(now, s["interval"])
    s["due"] = due.isoformat(timespec="seconds")
    return s


def preview(state: Optional[dict[str, Any]], now: dt.datetime) -> dict[str, int]:
    """In how many seconds each answer would bring the card back (for the buttons: "Good · 3 days"): a learning step's
    minutes, or a learnt card's days, as Anki says them (learnt at 23:50, it is due at 04:00: "1 day")."""
    out = {}
    for g in GRADES:
        s = schedule(state, g, now)
        due = dt.datetime.fromisoformat(s["due"])
        out[g] = max(1, round(s["interval"])) * 86400 if int(s["step"]) < 0 else \
            max(60, int((due - now).total_seconds()))
    return out


def states(lib: Any, course_id: str) -> dict[str, dict[str, Any]]:
    with lib._lock:
        rows = lib.db.execute("SELECT * FROM card_state WHERE course_id=?", (course_id,)).fetchall()
    return {r["card_id"]: {"due": r["due"], "interval": r["interval"], "ease": r["ease"], "reps": r["reps"],
                           "lapses": r["lapses"], "step": r["step"], "relearn": bool(r["relearn"]), "first": r["first"],
                           "last": r["last"]} for r in rows}


def learnt(s: Optional[dict[str, Any]]) -> bool:
    """A card past its learning steps and studied at least once: a missed quiz question joins the deck as if learnt
    (add_missed), but it isn't "learnt" before it has come back."""
    return bool(s) and int(s["step"]) < 0 and int(s.get("reps") or 0) > 0


def summary(lib: Any, cards: list[dict[str, Any]], course_id: str, now: Optional[dt.datetime] = None,
            new_per_day: Optional[int] = None) -> dict[str, Any]:
    """How the course's deck stands today (the study day: from 04:00): cards due now and later today, new ones to
    learn today (``new_per_day`` at most: Settings → Practice), how many are learnt, and how many were reviewed
    today. Cards set aside count only as "aside"."""
    now = now or dt.datetime.now()
    aside = sum(1 for c in cards if c.get("aside"))
    cards = [c for c in cards if not c.get("aside")]
    st = states(lib, course_id)
    start, end = (x.isoformat(timespec="seconds") for x in study_day(now))
    ids = {c["id"] for c in cards}
    due = sum(1 for c in cards if c["id"] in st and (st[c["id"]]["due"] or "") < end)
    fresh = sum(1 for c in cards if c["id"] not in st)
    started_today = sum(1 for k, s in st.items() if k in ids and start <= (s.get("first") or "") < end)
    new_today = min(fresh, max(0, (NEW_PER_DAY if new_per_day is None else new_per_day) - started_today))
    with lib._lock:          # (a cram session's reviews leave the schedule alone: not counted here)
        reviewed = lib.db.execute("SELECT COUNT(*) FROM card_reviews WHERE course_id=? AND at>=? AND at<? AND "
                                  "NOT cram", (course_id, start, end)).fetchone()[0]
    return {"total": len(cards), "due": due, "new": new_today, "new_total": fresh, "today": due + new_today,
            "learnt": sum(1 for c in cards if learnt(st.get(c["id"]))),
            "reviewed_today": int(reviewed), "lecture": sum(1 for c in cards if c["origin"] == "lecture"),
            "files": sum(1 for c in cards if c["origin"] == "files"),
            "quiz": sum(1 for c in cards if c["origin"] == "quiz"), "aside": aside,
            "lecture_days": len({c["source"].get("day_key") for c in cards if c["origin"] == "lecture"})}


def _asked_again(c: dict[str, Any], state: Optional[dict[str, Any]]) -> dict[str, Any]:
    """A missed multiple-choice question as it is asked this time: its options in another order at each review (the
    same within one), unless its explanation names an option by its letter."""
    from .quizzes import names_option, shuffle_right

    qz = c.get("quiz") or {}
    opts, right, wrong = qz.get("options") or [], qz.get("answer"), qz.get("wrong") or []
    if qz.get("type") != "mc" or not isinstance(right, int) or not 0 <= right < len(opts) or len(wrong) != len(opts):
        return c
    if names_option(qz.get("why") or "", wrong):
        return c
    seed = f"{c['id']}|{int((state or {}).get('reps') or 0)}"
    slot = int(hashlib.sha1(f"{seed}|slot".encode("utf-8")).hexdigest()[:8], 16) % len(opts)
    opts, right, wrong = shuffle_right(opts, right, wrong, seed, slot)
    return {**c, "quiz": {**qz, "options": opts, "answer": right, "wrong": wrong}}


def lecture_fronts(svc: Any, day_keys: Any) -> set[str]:
    """The fronts (as ``norm`` writes them) of these lectures' own Flashcards notes. The deck keeps a card once, under
    the first lecture that has it, so a card another lecture's notes also hold is found by its front."""
    out: set[str] = set()
    for day in day_keys or ():
        try:
            path = svc.day_files(day).get("notes:flashcards")
        except (KeyError, OSError, AttributeError):
            continue
        if path:
            out |= {norm(c["q"]) for c in _cards_in(path) if norm(c.get("q"))}
    return out


def picked(cards: list[dict[str, Any]], weeks: Optional[set[int]] = None,
           days: Optional[set[str]] = None, fronts: Optional[set[str]] = None,
           aside: bool = False) -> list[dict[str, Any]]:
    """The cards of those weeks, and of those lectures (``days``: their day keys: the lecture's own flashcards, the
    missed quiz questions it taught, and (``fronts``, lecture_fronts) its cards the deck keeps under another lecture);
    not the ones set aside (``aside``: only those)."""
    return [c for c in cards if (not weeks or c.get("week") in weeks)
            and (not days or (c.get("source") or {}).get("day_key") in days or norm(c.get("q")) in (fronts or ()))
            and bool(c.get("aside")) == aside]


def week_counts(lib: Any, cards: list[dict[str, Any]], course_id: str, now: Optional[dt.datetime] = None,
                new_per_day: Optional[int] = None) -> dict[int, dict[str, int]]:
    """Each week's cards: {week: {"total", "today"}} ("today": its cards due today and the new ones today's limit
    still takes), for the Practice tab's "Study one week"."""
    now = now or dt.datetime.now()
    st = states(lib, course_id)
    end = study_day(now)[1].isoformat(timespec="seconds")
    left = summary(lib, cards, course_id, now, new_per_day)["new"]
    out: dict[int, dict[str, int]] = {}
    for c in cards:
        w = c.get("week")
        if not w or c.get("aside"):
            continue
        row = out.setdefault(int(w), {"total": 0, "due": 0, "new": 0})
        row["total"] += 1
        if c["id"] not in st:
            row["new"] += 1
        elif (st[c["id"]]["due"] or "") < end:
            row["due"] += 1
    return {w: {"total": r["total"], "today": r["due"] + min(r["new"], left)} for w, r in sorted(out.items())}


def session(lib: Any, cards: list[dict[str, Any]], course_id: str, now: Optional[dt.datetime] = None,
            weeks: Optional[set[int]] = None, new_per_day: Optional[int] = None,
            days: Optional[set[str]] = None, cram: bool = False,
            fronts: Optional[set[str]] = None, whole: bool = False) -> list[dict[str, Any]]:
    """The cards to study now, in order: the ones in learning that are due, the reviews due today (the most overdue
    first), then today's new cards; each with its state and what each answer would do. ``weeks``, ``days``: only the
    cards of those weeks, or of those lectures (picked: their own flashcards, ``fronts``, and the questions missed
    there). Not the ones set aside; a missed multiple-choice question with its options reordered. ``cram`` (the Exam
    room's "Cram these weeks"): every card of them, due or not, in the deck's order; their answers leave the schedule
    alone (grade).
    A lecture's cards (``days``, its page's "Practise N cards") always come, all of them: the ones due and all its new
    ones (the student asked for this lecture, so the day's new-card limit doesn't hold them back), then the ones not
    due yet, each "cram" (its answer leaves its schedule as it is). So do ``cards`` given as a set of their own
    (``whole``: the flashcards just pasted, their "Study now")."""
    now = now or dt.datetime.now()
    st = states(lib, course_id)
    end = study_day(now)[1].isoformat(timespec="seconds")
    pick = picked(cards, weeks, days, fronts)
    early = lambda c: {**_asked_again(c, st.get(c["id"])), "state": st.get(c["id"]), "next": {}, "cram": True}  # noqa: E731
    if cram:
        return [early(c) for c in pick]
    due = sorted((c for c in pick if c["id"] in st and (st[c["id"]]["due"] or "") < end),
                 key=lambda c: (int(st[c["id"]]["step"]) < 0, st[c["id"]]["due"]))
    fresh = [c for c in pick if c["id"] not in st]
    rest: list[dict[str, Any]] = []
    if days or whole:
        taken = {c["id"] for c in due + fresh}
        rest = [early(c) for c in pick if c["id"] not in taken]
    else:
        fresh = fresh[:summary(lib, cards, course_id, now, new_per_day)["new"]]
    return [{**_asked_again(c, st.get(c["id"])), "state": st.get(c["id"]), "next": preview(st.get(c["id"]), now)}
            for c in due + fresh] + rest


def grade(lib: Any, course_id: str, card_id: str, how: str, now: Optional[dt.datetime] = None,
          cram: bool = False) -> dict[str, Any]:
    """Keep a review: the card's new state (in this profile's library) and the review in its log. ``cram``: only the
    review, marked so (card_state stays as it was); "again" brings the card back in a minute, in the same sitting."""
    now = now or dt.datetime.now()
    with lib._lock:
        row = lib.db.execute("SELECT * FROM card_state WHERE card_id=?", (card_id,)).fetchone()
    old = None if row is None else {"due": row["due"], "interval": row["interval"], "ease": row["ease"],
                                    "reps": row["reps"], "lapses": row["lapses"], "step": row["step"],
                                    "relearn": bool(row["relearn"]), "first": row["first"], "last": row["last"]}
    if cram:
        if how not in GRADES:
            raise ValueError(how)
        with lib._lock:
            lib.db.execute("INSERT INTO card_reviews(card_id, course_id, at, grade, interval, cram) VALUES(?,?,?,?,?,1)",
                           (card_id, course_id, now.isoformat(timespec="seconds"), how,
                            float((old or {}).get("interval") or 0)))
            lib.db.commit()
        again = how == "again"
        return {"due": (now + dt.timedelta(minutes=1) if again else study_day(now)[1]).isoformat(timespec="seconds"),
                "step": 0 if again else -1, "cram": True, "next": {}}
    s = schedule(old, how, now)
    with lib._lock:
        lib.db.execute("INSERT INTO card_state(card_id, course_id, due, interval, ease, reps, lapses, step, relearn, "
                       "first, last) VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(card_id) DO UPDATE SET "
                       "course_id=excluded.course_id, due=excluded.due, interval=excluded.interval, ease=excluded.ease, "
                       "reps=excluded.reps, lapses=excluded.lapses, step=excluded.step, relearn=excluded.relearn, "
                       "first=excluded.first, last=excluded.last",
                       (card_id, course_id, s["due"], s["interval"], s["ease"], s["reps"], s["lapses"], s["step"],
                        int(s["relearn"]), s["first"], s["last"]))
        lib.db.execute("INSERT INTO card_reviews(card_id, course_id, at, grade, interval) VALUES(?,?,?,?,?)",
                       (card_id, course_id, now.isoformat(timespec="seconds"), how, s["interval"]))
        lib.db.commit()
    return {**s, "next": preview(s, now)}


# ------------------------------------------------------------------ Anki
def source_text(src: dict[str, Any], course: str = "") -> str:
    """Where a card comes from, in words: "MATH 240 · Lecture 21 Oct 2026, part 1, 14:05" or "Week05.pdf, page 12"."""
    from .i18n import date_short

    if not src:
        return course
    if src.get("kind") == "file":
        where = t("{file}, page {page}", file=src.get("name") or "", page=src.get("page") or 1)
    else:
        try:
            day = date_short(dt.date.fromisoformat(src.get("date") or ""))
        except ValueError:
            day = src.get("date") or ""
        where = t("Lecture {day}, part {part}, {time}", day=day, part=src.get("part") or 1, time=src["ts"]) \
            if src.get("ts") else t("Lecture {day}", day=day)
    return f"{course} · {where}" if course else where


def anki_notes(cards: list[dict[str, Any]], code: str) -> list[dict[str, Any]]:
    from .anki import tag

    out = []
    for c in cards:
        tags = [tag(code), *([f"week_{c['week']:02d}"] if c.get("week") else []), "stressed" if c.get("stressed") else "",
                {"lecture": "from_lecture", "quiz": "from_quiz"}.get(c["origin"], "from_files")]
        qz, front = c.get("quiz") or {}, c["q"]
        if qz.get("type") == "mc" and not c.get("edited"):          # (asked with its options, as in the quiz)
            front += "\n\n" + "\n".join(f"{'ABCDEF'[i]}) {o}" for i, o in enumerate((qz.get("options") or [])[:6]))
        out.append({"id": c["id"], "front": front, "back": c["a"], "source": source_text(c["source"], code),
                    "tags": [x for x in tags if x]})
    return out


# ------------------------------------------------------------------ cards from files (AI, a plan of kind "cards")
def gather(lib: Any, settings: Any, course_id: str, scope: Any) -> dict[str, Any]:
    """The picked files (studypack.gather, no lectures), the cards' language (doclang.py) and what the lecturer
    stressed in these weeks (for the priorities)."""
    from . import stressed, studypack
    from .doclang import document_language
    from .views import course_detail

    scope.days, scope.picked = [], True
    facts = studypack.gather(lib, settings, course_id, scope)
    d = course_detail(lib, settings, course_id) or {}
    nos = {w["no"] for w in facts["weeks"]}
    day_week = {day["date"]: w["no"] for w in d.get("weeks") or [] for day in w["days"]}
    lines, _ = stressed.prompt_lines(stressed.ranked(stressed.course_items(lib, course_id), nos, day_week), None, 20)
    facts.update(**document_language(d, settings, facts), stressed_lines=lines, outcomes=d.get("outcomes_en") or [])
    return facts


def batches(lib: Any, facts: dict[str, Any], limit: int = BATCH_CHARS) -> list[dict[str, Any]]:
    """The files' pages in requests of about ``limit`` characters: [{"text", "pages", "refs"}]."""
    from .studypack import _file_head

    out: list[dict[str, Any]] = []
    cur: dict[str, Any] = {"text": [], "size": 0, "pages": 0, "refs": []}

    def flush() -> None:
        if cur["pages"]:
            out.append({"text": "\n".join(cur["text"]), "pages": cur["pages"], "refs": cur["refs"]})
        cur.update(text=[], size=0, pages=0, refs=[])

    for f in facts["files"]:
        head = _file_head(f)
        for n, page in enumerate(lib.material_pages(f["id"]), 1):
            line = f"[{f['ref']} p.{n}] {' '.join(page.split())}"
            if len(line) < 40:                                # a title or an empty page: nothing to learn
                continue
            line = line[:6000]
            if cur["size"] + len(line) > limit:
                flush()
            if f["ref"] not in cur["refs"]:
                cur["text"].append(head)
                cur["refs"].append(f["ref"])
            cur["text"].append(line)
            cur["size"] += len(line) + 1
            cur["pages"] += 1
    flush()
    return out


def cards_prompt(facts: dict[str, Any], batch: dict[str, Any], most: int, have: list[str]) -> str:
    from .outputs.prompts import cards_languages, cards_languages_again

    stressed = facts.get("stressed_lines") or ""
    known = "\n".join(f"- {q}" for q in have[:60])
    return f"""TASK: Make flashcards from these pages of the course's files ({facts['span']}): the definitions, formulas, theorems, rules, units, methods and key facts a student must know for the exams, one idea per card. {cards_languages(facts['language'], facts.get('taught_in', ''))}
Answer with one JSON object:
{{"cards": [{{"front": "a short question or cue", "back": "the answer: short and exact (a formula, a definition, the steps of a method)", "source": "F1 p.3", "topic": "a few words"}}]}}
Rules:
- 1 to 3 cards for each page that teaches something; none for title, contents or exercise-only pages. At most {most} cards.
- The front asks one thing and makes sense on its own (name the concept; never "what is on this slide"). The back answers only that, in a sentence or two or a formula.
- "source": the page the card comes from, as "F1 p.3", only the references below.
- Maths in LaTeX: $...$ in a sentence, $$...$$ on a line of its own.
- Give what the lecturer stressed (below) a card first if these pages cover it.{(chr(10) + "- These cards exist already; don't repeat them:" + chr(10) + known) if known else ""}

=== WHAT THE LECTURER STRESSED ===
{stressed or '(nothing found)'}

=== THE PAGES ===
{batch['text']}{cards_languages_again(facts['language'], facts.get('taught_in', ''))}"""


def read_cards(text: str) -> list[dict[str, Any]]:
    """The AI's cards: JSON ({"cards": [...]}, a bare list, front/back, question/answer, q/a, term/definition), else
    the "Q: … / A: …" Markdown that lecture flashcards are written in (outputs/flashcards.py)."""
    from .outputs.flashcards import parse_cards
    from .quizzes import _json_body

    data = _json_body(text)
    rows = data.get("cards") or data.get("flashcards") or data.get("items") if isinstance(data, dict) else data
    out = []
    for c in rows if isinstance(rows, list) else []:
        if not isinstance(c, dict):
            continue
        q = c.get("front") or c.get("question") or c.get("q") or c.get("term") or c.get("prompt") or ""
        a = c.get("back") or c.get("answer") or c.get("a") or c.get("definition") or c.get("explanation") or ""
        if isinstance(a, list):
            a = "\n".join(f"- {x}" for x in a)
        out.append({"q": str(q).strip(), "a": str(a).strip(), "source": c.get("source") or c.get("ref") or c.get("page"),
                    "topic": str(c.get("topic") or "").strip()})
    if not out:
        for c in parse_cards(text or ""):
            m = re.search(r"\(?\s*(F\d+\s*,?\s*p(?:age|\.)?\s*\d+)\s*\)?", c["a"]) or \
                re.search(r"\(?\s*(F\d+\s*,?\s*p(?:age|\.)?\s*\d+)\s*\)?", c["q"])
            a = (c["a"][:m.start()] + c["a"][m.end():]).strip() if m and m.group(0) in c["a"] else c["a"]
            out.append({"q": c["q"], "a": a, "source": m.group(1) if m else None, "topic": ""})
    return [c for c in out if len(c["q"]) >= 3 and c["a"]]


def clean_cards(rows: list[dict[str, Any]], facts: dict[str, Any], plan_id: str, seen: set[str],
                units: list) -> list[dict[str, Any]]:
    """The cards the deck can take: each once in the course (``seen``: the questions it has), maths as $…$, with a
    source page it can open (the one named, else found by its words)."""
    from .quizzes import _clean_tex, check_source, locate, parse_source

    out = []
    for c in rows:
        q, a = _clean_tex(c["q"])[:600], _clean_tex(c["a"])[:2000]
        if norm(q) in seen or not norm(q):
            continue
        seen.add(norm(q))
        src = check_source(parse_source(c.get("source")), facts)
        found = False
        if src is None:
            src = locate({"q": q, "why": a}, units, least=2)
            found = src is not None
        out.append({"id": _cid("f", plan_id, q), "q": q, "a": a, "topic": c.get("topic", "")[:120], "source": src,
                    "found": found, "stressed": False})
    return out


def run(runner: Any, plan: dict[str, Any], scope: Any, data: dict[str, Any], usage: dict[str, Any], model: str,
        cancel: threading.Event) -> None:
    """Make (or resume) the cards of the ``plan``: one request per batch of pages, each saved when it is done."""
    from .ai import one_shot
    from .plans import model_shown
    from .quizzes import source_units
    from .studypack import _level, _usage_add

    pid, s = plan["id"], runner._s
    level = _level(scope, s.settings)
    shown = model_shown(model, level)

    def stage(name: str, progress: float, message: str = "") -> None:
        s.lib.update_plan(pid, status="running", stage=name, progress=round(progress, 3),
                          message=message or t(LABELS[name]) + "…")
        runner._emit(pid)

    def check() -> None:
        if cancel.is_set():
            raise Cancelled()

    if "gather" not in data:
        stage("gather", 0.03)
        data["gather"] = gather(s.lib, s.settings, plan["course_id"], scope)
        data["batches"] = [{"pages": b["pages"], "refs": b["refs"]} for b in batches(s.lib, data["gather"])]
        s.lib.update_plan(pid, data=data, usage=usage)
    facts = data["gather"]
    if not facts["files"]:
        raise Failed(N_("None of the picked files is on this PC yet. Download them on the course page first."))
    check()
    parts = batches(s.lib, facts)
    written: dict[str, list] = data.setdefault("written", {})
    have = [c["q"] for c in deck(s, plan["course_id"])] if hasattr(s, "day_files") else []
    ask = runner._ask_text or one_shot
    for i, b in enumerate(parts):
        if str(i) in written:
            continue
        stage("write", 0.08 + 0.8 * i / max(1, len(parts)),
              t("Writing the cards ({model})…", model=shown) if len(parts) == 1 else
              t("Writing the cards: part {n} of {total} ({model})…", n=i + 1, total=len(parts), model=shown))
        most = min(40, max(6, b["pages"] * 2))
        prompt = cards_prompt(facts, b, most, have + [c["q"] for w in written.values() for c in w])
        res = ask(SYSTEM, [{"type": "text", "text": prompt}], model, level, cancel, None)
        _usage_add(usage, "write", res)
        rows = read_cards(res.get("text") or "")
        if not rows:
            check()
            again = ask(SYSTEM, [{"type": "text", "text": prompt + "\n\nReply with the JSON object only."}], model,
                        level, cancel, None)
            _usage_add(usage, "write", again)
            rows = read_cards(again.get("text") or "")
            if not rows:
                raise Failed(N_("{ai}'s answer could not be read (it was not valid JSON). Try again."))
        written[str(i)] = rows[:most]
        s.lib.update_plan(pid, data=data, usage=usage)
        check()
    stage("check", 0.92)
    seen = {norm(q) for q in have}
    units = source_units(s.lib, plan["course_id"], {**facts, "lectures": []})
    cards = clean_cards([c for i in sorted(written, key=int) for c in written[i]], facts, pid, seen, units)[:MAX_CARDS]
    if not cards:
        raise Failed(N_("The AI made no cards these files don't already have. Pick other files."))
    names = [f["name"] for f in facts["files"]]
    title = t("Cards from {files}", files=", ".join(names[:3]) + ("…" if len(names) > 3 else ""))
    result = {"kind": "cards", "title": title, "made": dt.date.today().isoformat(), "cards": cards,
              "files": names, "language": facts["language"], "materials_missing": facts["materials_missing"],
              "files_cut": [f["name"] for f in facts.get("files_cut") or []]}
    s.lib.update_plan(pid, data=data, usage=usage, result=result, title=title, status="done", stage="done",
                      progress=1.0, message=tn(len(cards), "{n} card", "{n} cards"))


def estimate(lib: Any, settings: Any, course_id: str, scope: Any) -> dict[str, Any]:
    """Roughly what making cards from these files costs."""
    from .ai import PROMPT_OVERHEAD, chars_per_token
    from .plans import main_model
    from .studypack import _level

    facts = gather(lib, settings, course_id, scope)
    parts = batches(lib, facts)
    per = chars_per_token()
    tokens_in = sum(len(b["text"]) / per + PROMPT_OVERHEAD + 900 for b in parts)
    cards = sum(min(40, max(6, b["pages"] * 2)) for b in parts)
    a = max(1, math.ceil(len(parts) * 1.5))
    return {"main_model": main_model(scope, settings), "effort": _level(scope, settings), "main_input": int(tokens_in),
            "main_output": int(cards * 90 + 300 * len(parts)), "requests": len(parts), "cards": cards,
            "files": len(facts["files"]), "pages": sum(b["pages"] for b in parts),
            "language": facts["language"], "taught_in": facts.get("taught_in", ""),       # (the dialog's line)
            "materials_missing": facts["materials_missing"], "minutes": t("about {a}–{b} minutes", a=a, b=a * 2 + 1)}
