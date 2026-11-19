"""The Practice tools' side of the page's bridge (web/practice.js): practice quizzes (quizzes.py), the course's
flashcards with spaced repetition (cards.py) and their Anki export (anki.py), and what the lecturer stressed
(stressed.py). api.Api inherits these methods, so they are called like its own (``self._s``: the services); each one
answers {"ok": false, "error"} instead of raising, as every API method does.

Quizzes and cards made from files are plans (kinds "quiz" and "cards", run by plans.PlanRunner); they are kept out of
the Study plans list (api.plan_list) and shown here. A student's tries at a quiz are kept in quiz_attempts, what they
know of each card in card_state: both in their own profile's library.

The mistake loop (4.2): each answer of a try keeps what was chosen or typed and who marked it ("mark": "app" when the
app checked it, a multiple choice; "self" when the student said how it went; MARKS has the weight each counts with).
A missed question opens on the end screen beside the right answer, with the moment it was taught (``moment``: the
slide on screen, the transcript around it, a short clip to play); it joins the course's cards (cards.py) and stays in
the Practice tab's Mistakes until it is right in a retry or its card is spaced out to 4 days. ``mastery`` counts how
each learning outcome and topic went (never one percentage), and ``weak_spots`` is what a weak-spot quiz is written
around (quizzes.gather narrows the material to it)."""

from __future__ import annotations

import datetime as dt
import functools
import json
import logging
import re
import threading
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from .i18n import t

log = logging.getLogger("studia")
PRACTICE_KINDS = ("quiz", "cards")
# who marked an answer, and how much it counts in ``mastery``: the app's check (a multiple choice; checked numbers
# and the AI's marking add "app" and "ai") fully, the student's own "I got it" half (students over-mark)
MARKS = {"app": 1.0, "ai": 1.0, "self": 0.5}
HALF_LIFE_DAYS = 14             # a try this many days old counts half
SHAKY, SOLID = 0.6, 0.8         # under SHAKY with 2 questions or more: shaky; SOLID or more with 3 or more: solid
GRADUATED_DAYS = 4              # a missed question whose card is spaced out this far leaves the Mistakes list
CLIP_BEFORE, CLIP_AFTER, CLIP_MOST = 20, 70, 150      # a replay's clip around a moment with no slide on screen (s)
NEAR_S = 180                    # a weak-spot quiz reads a lecture this close to where a missed question was taught


def _safe(fn: Callable[..., Any]) -> Callable[..., Any]:
    """api.safe around the method (api.py is loaded by the time one is called: Api inherits them)."""
    @functools.wraps(fn)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        from .api import safe

        return safe(fn)(self, *args, **kwargs)
    return wrapper


def _ai_ready() -> Optional[str]:
    """Why quizzes and cards can't be made now ("" when they can)."""
    from .ai import claude_info

    info = claude_info()
    if not info["available"]:
        return t("Set up ChatGPT or Claude in Settings → AI first.")
    if info.get("can_plans") is False:
        return t("Quizzes and cards need ChatGPT or Claude (the offline model is too small for them).")
    return None


# ------------------------------------------------------------------ quiz tries (quiz_attempts)
def kept_answer(a: dict[str, Any]) -> dict[str, Any]:
    """One answer as a try keeps it: {"n", "got" (0 to 1), "chose" (the option picked, or None), "skipped", "text"
    (what was typed), "mark" (who marked it: MARKS)}, and anything else the page sent (a later check's own fields)."""
    out = dict(a)
    out["got"] = round(min(1.0, max(0.0, float(a.get("got") or 0))), 2)
    out["chose"] = a.get("chose") if isinstance(a.get("chose"), int) and not isinstance(a.get("chose"), bool) else None
    out["skipped"] = bool(a.get("skipped"))
    out["text"] = str(a.get("text") or "")[:4000]
    if out.get("mark") not in MARKS:
        out["mark"] = "app" if out["chose"] is not None else "self"
    return out


def answer_mark(a: dict[str, Any], q: Optional[dict[str, Any]] = None) -> str:
    """Who marked an answer; a try saved before 4.2 has no "mark": the app for a multiple choice, else the student."""
    m = a.get("mark")
    return m if m in MARKS else "app" if (q or {}).get("type") == "mc" or a.get("chose") is not None else "self"


def answer_weight(a: dict[str, Any], q: Optional[dict[str, Any]] = None) -> float:
    """How much an answer counts: by who marked it; a skip fully (not knowing it is sure)."""
    return 1.0 if a.get("skipped") else MARKS[answer_mark(a, q)]


def save_attempt(lib: Any, plan_id: str, course_id: str, attempt: dict[str, Any]) -> dict[str, Any]:
    answers = [kept_answer(a) for a in attempt.get("answers") or [] if isinstance(a, dict)][:200]
    total = len(answers)
    score = round(sum(min(1.0, max(0.0, float(a.get("got") or 0))) for a in answers), 2)
    row = {"id": uuid.uuid4().hex[:12], "plan_id": plan_id, "course_id": course_id,
           "started_at": str(attempt.get("started_at") or dt.datetime.now().isoformat(timespec="seconds"))[:25],
           "finished_at": dt.datetime.now().isoformat(timespec="seconds"), "score": score, "total": total,
           "answers": answers, "only": [int(n) for n in attempt.get("only") or [] if str(n).isdigit()]}
    with lib._lock:
        lib.db.execute("INSERT INTO quiz_attempts(id, plan_id, course_id, started_at, finished_at, score, total, "
                       "answers, only) VALUES(?,?,?,?,?,?,?,?,?)",
                       (row["id"], plan_id, course_id, row["started_at"], row["finished_at"], score, total,
                        json.dumps(answers, ensure_ascii=False), json.dumps(row["only"])))
        lib.db.commit()
    _bump()
    return row


def attempts(lib: Any, plan_id: Optional[str] = None, course_id: Optional[str] = None) -> list[dict[str, Any]]:
    """Tries, newest first (of one quiz, or of a course's quizzes)."""
    q, args = "SELECT * FROM quiz_attempts", []
    if plan_id:
        q, args = q + " WHERE plan_id=?", [plan_id]
    elif course_id:
        q, args = q + " WHERE course_id=?", [course_id]
    with lib._lock:
        rows = lib.db.execute(q + " ORDER BY finished_at DESC, rowid DESC", args).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        for k in ("answers", "only"):
            try:
                d[k] = json.loads(d[k] or "[]")
            except ValueError:
                d[k] = []
        out.append(d)
    return out


def quiz_brief(plan: dict[str, Any], tries: list[dict[str, Any]], kept: Optional[int] = None) -> dict[str, Any]:
    """A quiz for the lists: its state, title, size (``kept``: the questions a finished quiz kept; else as many as
    were asked), when it was made, and its last and best whole tries."""
    whole = [a for a in tries if not a["only"]]
    best = max(whole, key=lambda a: a["score"] / max(1, a["total"]), default=None)
    sc = plan.get("scope") or {}
    return {"id": plan["id"], "course_id": plan["course_id"], "title": plan.get("title") or "", "status": plan["status"],
            "stage": plan.get("stage", ""), "progress": plan.get("progress", 0), "message": plan.get("message", ""),
            "error": plan.get("error", ""), "created_at": plan.get("created_at", ""), "weeks": sc.get("weeks") or [],
            "days": sc.get("days") or [], "focus": sc.get("focus") or "",
            "questions": kept if kept is not None else sc.get("questions") or 0,
            "tries": len(tries), "last": tries[0] if tries else None, "best": best}


def kept_questions(lib: Any, plan_ids: list[str]) -> dict[str, int]:
    """How many questions each of these finished quizzes kept: the checks drop unusable or repeated ones, so it can
    be fewer than were asked (the list said "10 questions" of a quiz of 8); the ones the student removed don't
    count."""
    if not plan_ids:
        return {}
    with lib._lock:
        rows = lib.db.execute(f"SELECT id, result FROM plans WHERE id IN ({','.join('?' * len(plan_ids))})",
                              plan_ids).fetchall()
    out = {}
    for pid, raw in rows:
        try:
            res = json.loads(raw or "{}") or {}
            qs = res.get("questions")
        except (ValueError, AttributeError):
            continue
        if isinstance(qs, list):
            out[pid] = len(qs) - len(set(res.get("removed") or []) & {q.get("n") for q in qs if isinstance(q, dict)})
    return out


# ------------------------------------------------------------------ the course's finished quizzes (read once)
_gen = [0]                       # goes up with every try saved and every quiz question changed (caches follow it)
_results: dict[tuple[str, str], tuple[Any, dict[str, Any]]] = {}
_results_lock = threading.Lock()


def _bump() -> None:
    _gen[0] += 1


def generation() -> int:
    return _gen[0]


def quiz_results(lib: Any, course_id: str) -> dict[str, dict[str, Any]]:
    """The course's finished quizzes: {plan id: {"brief": the plan without its data, "result": its questions…}};
    each result read again only when its plan changed."""
    out: dict[str, dict[str, Any]] = {}
    where = str(getattr(lib, "path", id(lib)))
    for p in lib.plans(course_id):
        if (p.get("scope") or {}).get("kind") != "quiz" or p.get("status") != "done":
            continue
        key = (where, p["id"])
        with _results_lock:
            hit = _results.get(key)
        if not hit or hit[0] != (p.get("updated_at"), _gen[0]):
            full = lib.plan(p["id"]) or {}
            res = full.get("result") if isinstance(full.get("result"), dict) else {}
            hit = ((p.get("updated_at"), _gen[0]), res)
            with _results_lock:
                _results[key] = hit
        out[p["id"]] = {"brief": p, "result": hit[1]}
    return out


def question_action(lib: Any, plan_id: str, n: int, action: str) -> Optional[dict[str, Any]]:
    """A question of a finished quiz: "remove" (out of the quiz, its card, the Mistakes and the counts), "restore"
    (back), "not_here" (its source is wrong: no replay) or "here" (it is right after all). The quiz's result, or None
    when there is no such quiz."""
    p = lib.plan(plan_id)
    if p is None or (p.get("scope") or {}).get("kind") != "quiz":
        return None
    res = dict(p.get("result") or {})
    removed, wrong_src = set(res.get("removed") or []), set(res.get("not_here") or [])
    n = int(n)
    {"remove": removed.add, "restore": removed.discard, "not_here": wrong_src.add, "here": wrong_src.discard}[action](n)
    res.update(removed=sorted(removed), not_here=sorted(wrong_src))
    lib.update_plan(plan_id, result=res)
    _bump()
    return res


def _first_line(text: str, most: int = 160) -> str:
    line = str(text or "").strip().split("\n")[0]
    return line if len(line) <= most else line[:most].rsplit(" ", 1)[0] + "…"


# ------------------------------------------------------------------ the Mistakes list
def mistakes(lib: Any, course_id: str, plan_id: Optional[str] = None) -> list[dict[str, Any]]:
    """The questions still missed, the latest miss first: each missed in its latest answer (a retry that got it right
    takes it off), not removed, and its card (if it has one) not yet spaced out to GRADUATED_DAYS. Each: {"plan_id",
    "n", "title" (the quiz's), "at" (the try's end), "misses", "q" (the question), "answer" (the latest),
    "card_id", "due"}."""
    from .cards import quiz_card_id, states

    found = quiz_results(lib, course_id)
    latest: dict[tuple[str, int], tuple[dict[str, Any], dict[str, Any]]] = {}
    misses: dict[tuple[str, int], int] = {}
    for a in reversed(attempts(lib, plan_id=plan_id, course_id=course_id)):
        if a["plan_id"] not in found:
            continue
        for ans in a["answers"]:
            if not isinstance(ans, dict) or not isinstance(ans.get("n"), int):
                continue
            key = (a["plan_id"], ans["n"])
            latest[key] = (ans, a)
            if float(ans.get("got") or 0) < 1:
                misses[key] = misses.get(key, 0) + 1
    st = states(lib, course_id)
    out = []
    for (pid, n), (ans, a) in latest.items():
        res = found[pid]["result"]
        if float(ans.get("got") or 0) >= 1 or n in set(res.get("removed") or []):
            continue
        q = next((x for x in res.get("questions") or [] if isinstance(x, dict) and x.get("n") == n), None)
        if not q:
            continue
        cid = quiz_card_id(pid, q.get("q") or "")
        s = st.get(cid)
        if s and float(s.get("interval") or 0) >= GRADUATED_DAYS:
            continue
        if n in set(res.get("not_here") or []):
            q = {**q, "source": None, "not_here": True}
        out.append({"plan_id": pid, "n": n, "title": found[pid]["brief"].get("title") or res.get("title") or "",
                    "at": a["finished_at"], "misses": misses.get((pid, n), 0), "q": q, "answer": ans,
                    "card_id": cid, "due": (s or {}).get("due") or ""})
    out.sort(key=lambda x: (x["at"], -x["n"]), reverse=True)
    return out


# ------------------------------------------------------------------ where it was taught (a replay)
def moment(lib: Any, day_key: str, part: int, at: float) -> Optional[dict[str, Any]]:
    """The moment of a lecture a question comes from: the slide on screen then (its number, title, picture and when
    it was shown), the transcript around it (three paragraphs before and after; the one said then marked) and a
    clip to play: the slide's time on screen, else from CLIP_BEFORE s before to CLIP_AFTER s after, CLIP_MOST s at
    most. None when the lecture or its part isn't here."""
    from . import store
    from .outputs.common import format_ts
    from .outputs.markdown import part_views
    from .player import screen_texts

    recs = lib.day_recordings(day_key)
    if not recs:
        return None
    trans = store.load_transcripts([r.delivery_id for r in recs])
    slides = {r.delivery_id: store.load_slides(r.delivery_id) for r in recs}
    p = next((x for x in part_views(recs, trans, slides) if x.index == int(part or 1)), None)
    if p is None:
        return None
    at = max(0.0, float(at or 0))
    paras = p.paragraphs
    i = max((k for k, x in enumerate(paras) if x.start <= at + 0.5), default=0)
    lines = [{"t": round(x.start, 1), "ts": format_ts(x.start), "text": x.text, "lang": x.lang, "at": k == i}
             for k, x in enumerate(paras) if i - 3 <= k <= i + 3]
    shown = sorted(p.slides, key=lambda s: s.t)
    s = next((x for x in reversed(shown) if x.t <= at + 0.5), None)
    if s is not None and s.end and at > s.end + 5:          # (a camera or the board after the slide: none on screen)
        s = None
    slide = None
    if s is not None:
        scr = screen_texts(p.rec.delivery_id).get(str(s.index)) or {}
        slide = {"n": s.index, "t0": round(float(s.t), 1), "t1": round(float(s.end or s.t), 1),
                 "range": f"{format_ts(s.t)}–{format_ts(s.end or s.t)}",
                 "title": " ".join(str(scr.get("title") or "").split())[:200],
                 "image": Path(s.path).as_uri() if s.path and Path(s.path).is_file() else ""}
    a, b = (float(s.t), float(s.end or s.t)) if s is not None and (s.end or 0) > s.t else (at - CLIP_BEFORE, at + CLIP_AFTER)
    if b - a > CLIP_MOST:                                     # a slide shown for long: the stretch around the moment
        a = max(a, at - 40)
        b = min(b, a + CLIP_MOST)
    a = max(0.0, min(a, at))
    dur = float(p.rec.duration_s or 0)
    b = max(a + 10, min(b, dur) if dur else b)
    return {"day_key": day_key, "part": p.index, "t": round(at, 1), "ts": format_ts(at), "date": day_key.split("|")[1],
            "slide": slide, "lines": lines, "clip": {"from": round(a, 1), "until": round(b, 1),
                                                     "range": f"{format_ts(a)}–{format_ts(b)}"},
            "parts": len(trans)}


# ------------------------------------------------------------------ how each learning outcome and topic went
def _stems(text: str) -> frozenset[str]:
    from .quizzes import _content_words

    return frozenset(w[:6] for w in _content_words(text))


def mastery(lib: Any, course_id: str, focus: str = "", *, los: Optional[list[int]] = None,
            outcomes: Optional[list[str]] = None, now: Optional[dt.datetime] = None) -> dict[str, Any]:
    """How the course's practice went, per learning outcome, then per topic (topics grouped by their words' stems):
    counts, never one percentage. Every answer of every try counts, and every review of a missed question's card
    (Again a miss, anything else right); recent ones more (half as much after HALF_LIFE_DAYS), the student's own
    marks half (MARKS). A row: {"tried" (answers), "right" (their marks added up), "questions", "quizzes", "share"
    (the weighted share right, for the status only), "status": "untested" | "few" (one question only: too few to
    tell) | "shaky" (under SHAKY, with 2 questions or more) | "solid" (SOLID or more with 3 or more) | "some" (in
    between), "missed": the questions last missed [{"plan_id", "n"}]}. ``focus`` (an assessment's name) with ``los`` (its learning outcomes): only those outcomes' rows, and the
    questions that test them or come from a quiz for it. ``outcomes``: the course's, for their text and the
    untested ones."""
    from .cards import quiz_card_id

    now = now or dt.datetime.now()
    found = quiz_results(lib, course_id)
    tries = [a for a in attempts(lib, course_id=course_id) if a["plan_id"] in found]
    want = set(los or [])
    rows: dict[Any, dict[str, Any]] = {}

    def row(key: Any, label: str) -> dict[str, Any]:
        return rows.setdefault(key, {"key": key, "label": label, "tried": 0, "right": 0.0, "w": 0.0, "wr": 0.0,
                                     "qs": set(), "plans": set(), "last": {}, "names": {}})

    def count(q: dict[str, Any], pid: str, got: float, weight: float, at: str, card: bool = False) -> None:
        try:
            age = max(0.0, (now - dt.datetime.fromisoformat(at[:19])).total_seconds() / 86400)
        except ValueError:
            age = 0.0
        w = weight * 0.5 ** (age / HALF_LIFE_DAYS)
        keys = [("lo", n) for n in q.get("los") or [] if not want or n in want]
        if q.get("topic"):
            keys.append(("topic", _stems(q["topic"]) or q["topic"].casefold()))
        for k in keys:
            r = row(k, q["topic"] if k[0] == "topic" else f"LO{k[1]}")
            r["tried"] += 1
            r["right"] += got
            r["w"] += w
            r["wr"] += w * got
            r["qs"].add((pid, q.get("n")))
            if not card:
                r["plans"].add(pid)
            r["last"][(pid, q.get("n"))] = (at, got)
            if k[0] == "topic":
                r["names"][q["topic"]] = r["names"].get(q["topic"], 0) + 1

    asked: dict[tuple[str, Any], dict[str, Any]] = {}       # (quiz, question number) -> the question
    for pid, x in found.items():
        res, sc = x["result"], x["brief"].get("scope") or {}
        removed = set(res.get("removed") or [])
        for q in res.get("questions") or []:
            if not isinstance(q, dict) or q.get("n") in removed:
                continue
            if focus and not (sc.get("focus") == focus or want & set(q.get("los") or [])):
                continue
            asked[(pid, q.get("n"))] = q
    for a in tries:
        for ans in a["answers"]:
            q = asked.get((a["plan_id"], ans.get("n"))) if isinstance(ans, dict) else None
            if q:
                count(q, a["plan_id"], float(ans.get("got") or 0), answer_weight(ans, q), a["finished_at"])
    by_card = {quiz_card_id(pid, q.get("q") or ""): (pid, q) for (pid, _), q in asked.items()}
    if by_card:
        with lib._lock:
            reviews = lib.db.execute(f"SELECT card_id, at, grade FROM card_reviews WHERE course_id=? AND card_id IN "
                                     f"({','.join('?' * len(by_card))})", [course_id, *by_card]).fetchall()
        for cid, at, grade in reviews:
            pid, q = by_card[cid]
            count(q, pid, 0.0 if grade == "again" else 1.0, MARKS["app" if q.get("type") == "mc" else "self"], at,
                  card=True)
    texts = outcomes or []
    for n in sorted(want or (set(range(1, len(texts) + 1)) if texts else set())):
        row(("lo", n), f"LO{n}")

    def done(r: dict[str, Any]) -> dict[str, Any]:
        share = r["wr"] / r["w"] if r["w"] else 0.0
        nq = len(r["qs"])
        status = "untested" if not r["tried"] else "few" if nq < 2 else "shaky" if share < SHAKY else \
            "solid" if share >= SOLID and nq >= 3 else "some"
        missed = sorted((k for k, (at, got) in r["last"].items() if got < 1), key=lambda k: r["last"][k][0], reverse=True)
        out = {"tried": r["tried"], "right": round(r["right"], 2), "questions": nq, "quizzes": len(r["plans"]),
               "share": round(share, 2), "status": status, "missed": [{"plan_id": p, "n": n} for p, n in missed]}
        if r["key"][0] == "lo":
            n = r["key"][1]
            return {"no": n, "text": texts[n - 1] if 0 < n <= len(texts) else "", **out}
        return {"name": max(r["names"], key=r["names"].get), **out}

    order = {"shaky": 0, "some": 1, "few": 2, "untested": 3, "solid": 4}
    lo_rows = sorted((done(r) for r in rows.values() if r["key"][0] == "lo"), key=lambda x: (order[x["status"]], x["share"], x["no"]))
    topics = sorted((done(r) for r in rows.values() if r["key"][0] == "topic"),
                    key=lambda x: (order[x["status"]], x["share"], -x["tried"]))
    return {"los": lo_rows, "topics": topics, "answers": sum(len(a["answers"]) for a in tries),
            "attempts": len(tries), "quizzes": len({a["plan_id"] for a in tries}), "focus": focus}


def _q_text(found: dict[str, dict[str, Any]], pid: str, ans: dict[str, Any]) -> str:
    """The text of the question an answer is to (for its card's id)."""
    for q in found[pid]["result"].get("questions") or []:
        if isinstance(q, dict) and q.get("n") == ans.get("n"):
            return q.get("q") or ""
    return ""


# ------------------------------------------------------------------ a quiz on the weak spots
def weak_spots(lib: Any, course_id: str, focus: str = "", *, los: Optional[list[int]] = None,
               outcomes: Optional[list[str]] = None, most: int = 12) -> dict[str, Any]:
    """What a weak-spot quiz is written around: the questions still missed (``most`` of them, the latest first; with
    the option chosen and why it is wrong, or what was typed), the learning outcomes and topics where points are lost,
    and the questions answered right twice or more ("avoid": not to be asked again)."""
    m = mastery(lib, course_id, focus, los=los, outcomes=outcomes)
    open_ = mistakes(lib, course_id)
    if focus:
        keep = {(x["plan_id"], x["n"]) for r in m["los"] + m["topics"] for x in r["missed"]}
        open_ = [x for x in open_ if (x["plan_id"], x["n"]) in keep]
    missed = []
    for x in open_[:most]:
        q, a = x["q"], x["answer"]
        item = {"plan_id": x["plan_id"], "n": x["n"], "type": q.get("type"), "q": q.get("q") or "",
                "topic": q.get("topic") or "", "los": q.get("los") or [], "level": q.get("level") or "",
                "source": q.get("source")}
        if q.get("type") == "mc":
            opts, ch = q.get("options") or [], a.get("chose")
            item["right"] = opts[q["answer"]] if isinstance(q.get("answer"), int) and q["answer"] < len(opts) else ""
            if isinstance(ch, int) and 0 <= ch < len(opts):
                item["chose"] = opts[ch]
                item["why_wrong"] = (q.get("wrong") or [""] * len(opts))[ch] if ch < len(q.get("wrong") or []) else ""
        else:
            item["right"] = q.get("model") or ""
            item["typed"] = a.get("text") or ""
        item["skipped"] = bool(a.get("skipped"))
        missed.append(item)
    found = quiz_results(lib, course_id)
    right: dict[str, int] = {}
    text: dict[str, str] = {}
    for a in attempts(lib, course_id=course_id):
        if a["plan_id"] not in found:
            continue
        for ans in a["answers"]:
            if isinstance(ans, dict) and float(ans.get("got") or 0) >= 1:
                qt = _q_text(found, a["plan_id"], ans)
                if qt:
                    k = re.sub(r"\W+", " ", qt.casefold()).strip()
                    right[k] = right.get(k, 0) + 1
                    text[k] = qt
    weak = [r for r in m["los"] if r["status"] in ("shaky", "some", "few") and r["right"] < r["tried"]]
    topics = [r for r in m["topics"] if r["status"] in ("shaky", "some", "few") and r["right"] < r["tried"]]
    return {"missed": missed, "open": len(open_), "los": weak, "topics": topics[:8],
            "avoid": [text[k] for k, n in right.items() if n >= 2][:40], "attempts": m["attempts"],
            "quizzes": m["quizzes"]}


def weak_scope(lib: Any, course_id: str, scope: dict[str, Any]) -> dict[str, Any]:
    """A weak-spot quiz's scope: the files and lectures its missed questions come from (each of their weeks), the
    weeks of the quizzes the ones without a source come from, and what not to ask again."""
    w = weak_spots(lib, course_id, scope.get("focus") or "")
    found = quiz_results(lib, course_id)
    files, days, weeks = [], [], set()
    for x in w["missed"]:
        src = x.get("source") or {}
        if src.get("kind") == "file" and src.get("id"):
            files.append(src["id"])
        elif src.get("kind") == "lecture" and src.get("day_key"):
            days.append(src["day_key"])
        if src.get("week"):
            weeks.add(int(src["week"]))
        elif not src:
            weeks |= {int(n) for n in (found.get(x["plan_id"], {}).get("brief", {}).get("scope") or {}).get("weeks") or []}
    return {**scope, "kind": "quiz", "weak": True, "weeks": sorted(weeks), "files": list(dict.fromkeys(files)),
            "days": list(dict.fromkeys(days)), "picked": bool(files or days),
            "avoid": list(dict.fromkeys([*(scope.get("avoid") or []), *w["avoid"]]))[:60]}


class PracticeApi:
    """The Practice tools' API methods (api.Api inherits them)."""

    _s: Any                                   # (api.Api's: the services, the AI's cancel, _push, _start_plan…)
    _ai_cancel: Optional[threading.Event]

    # ---- what each course has
    def _deck(self, course_id: str) -> list[dict[str, Any]]:
        from .cards import deck

        return deck(self._s, course_id)

    def _stressed(self, course_id: str, d: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        from . import stressed
        from .views import course_detail

        d = d or course_detail(self._s.lib, self._s.settings, course_id) or {}
        items = stressed.course_items(self._s.lib, course_id)
        out = stressed.grouped(items, d.get("weeks") or [])
        try:
            saved = json.loads(self._s.lib.get_meta(f"stressed-ai:{course_id}") or "null")
        except ValueError:
            saved = None
        if isinstance(saved, dict):
            known = {it["id"] for it in items}
            saved["new"] = len(known - set(saved.get("from") or []))
            saved.pop("from", None)
        out["summary"] = saved if isinstance(saved, dict) else None
        out["untranscribed"] = sum(1 for w in d.get("weeks") or [] for day in w["days"]
                                   if day.get("mine") is not False and day.get("parts") and not day.get("any_done"))
        return out

    @_safe
    def practice_summary(self, full: bool = False) -> dict[str, Any]:
        """Each course's flashcards today (due, new, in all) and quizzes (how many, the last try); ``full``: also how
        many moments the lecturer stressed (the Practice page)."""
        from . import stressed
        from .cards import summary

        lib, out = self._s.lib, []
        done = lib.transcribed_ids()
        for c in lib.courses():
            if not c.code:
                continue
            try:
                cards = self._deck(c.id)
                deck = summary(lib, cards, c.id, new_per_day=self._s.settings.cards_per_day)
            except Exception:  # noqa: BLE001 - one course's deck that can't be read leaves the others
                log.warning("The flashcards of %s could not be read", c.code, exc_info=True)
                deck = {"total": 0, "due": 0, "new": 0, "today": 0}
            quizzes = [p for p in lib.plans(c.id) if (p.get("scope") or {}).get("kind") == "quiz"]
            tries = attempts(lib, course_id=c.id)
            row = {"id": c.id, "code": c.code, "deck": deck, "quizzes": len(quizzes),
                   "quizzes_busy": sum(1 for p in quizzes if p["status"] in ("queued", "running")),
                   "last_try": tries[0] if tries else None,           # what there is to practise from:
                   "material": sum(1 for r in lib.recordings(c.id) if r.delivery_id in done)      # lectures, files
                   + sum(1 for m in lib.materials(c.id) if m.get("text_status") == "done")}
            if full:
                try:
                    row["stressed"] = sum(1 for it in stressed.course_items(lib, c.id)       # (those that stand out)
                                          if it["kind"] != "not_needed" and it.get("score", 1) >= stressed.TOP_SCORE)
                except Exception:  # noqa: BLE001
                    row["stressed"] = 0
            out.append(row)
        return {"ok": True, "courses": out, "due": sum(r["deck"]["today"] for r in out)}

    @_safe
    def practice_course(self, course_id: str) -> dict[str, Any]:
        """A course's Practice tab: its deck today, its quizzes (newest first, with their tries), the card sets being
        made from files, and what the lecturer stressed (by week, with the AI's summary if one was made)."""
        from .cards import summary
        from .views import course_detail

        lib = self._s.lib
        d = course_detail(lib, self._s.settings, course_id)
        if d is None:
            return {"ok": False, "error": t("This course is no longer in your list.")}
        cards = self._deck(course_id)
        plans = lib.plans(course_id)
        tries = attempts(lib, course_id=course_id)
        mine = [p for p in plans if (p.get("scope") or {}).get("kind") == "quiz"]
        kept = kept_questions(lib, [p["id"] for p in mine if p["status"] == "done"])
        quizzes = [quiz_brief(p, [a for a in tries if a["plan_id"] == p["id"]], kept.get(p["id"])) for p in mine]
        sets = [{"id": p["id"], "title": p.get("title") or "", "status": p["status"], "message": p.get("message", ""),
                 "progress": p.get("progress", 0), "error": p.get("error", ""), "created_at": p.get("created_at", "")}
                for p in plans if (p.get("scope") or {}).get("kind") == "cards"]
        weeks = sorted({c["week"] for c in cards if c.get("week")})
        return {"ok": True, "deck": {**summary(lib, cards, course_id, new_per_day=self._s.settings.cards_per_day),
                                     "weeks": weeks}, "quizzes": quizzes,
                "card_sets": sets, "stressed": self._stressed(course_id, d),
                "mistakes": mistakes(lib, course_id)[:40],
                "weak": mastery(lib, course_id, outcomes=d.get("outcomes_en") or d.get("outcomes_tr") or [])}

    # ---- what the lecturer stressed: the AI's "Know this for the exam"
    @_safe
    def stressed_summarize(self, course_id: str, level: str = "") -> dict[str, Any]:
        """The chosen AI turns the stressed moments into a short ranked list with citations (one request); kept
        (meta "stressed-ai:<course>") and sent back as "stressed" events."""
        from . import net, quizzes, stressed
        from .ai import ai_name, ask_json, model_label
        from .doclang import document_language
        from .effort import resolve
        from .views import course_detail

        why = _ai_ready()
        if why:
            return {"ok": False, "error": why}
        if self._ai_cancel is not None:
            return {"ok": False, "error": t("{ai} is already working on something.", ai=ai_name())}
        lib = self._s.lib
        d = course_detail(lib, self._s.settings, course_id)
        if d is None:
            return {"ok": False, "error": t("This course is no longer in your list.")}
        items = stressed.course_items(lib, course_id)
        day_week = {day["date"]: w["no"] for w in d["weeks"] for day in w["days"]}
        top = stressed.ranked(items)
        if not top:
            return {"ok": False, "error": t("Nothing stressed in the transcribed lectures yet.")}
        lines, by_ref = stressed.prompt_lines(top, {k: f"{k.split('|')[1]} (week {day_week.get(k.split('|')[1], '?')})"
                                                    for k in {it["day_key"] for it in top}}, 60,
                                              [it for it in items if it["kind"] == "not_needed"])
        nxt = next((e for e in quizzes.assessments(d) if e["ahead"]), None)
        outcomes = d.get("outcomes_en") or d.get("outcomes_tr") or []
        focus = ""
        if nxt:
            los = "; ".join(f"LO{n}" + (f": {outcomes[n - 1]}" if 0 < n <= len(outcomes) else "") for n in nxt["los"])
            focus = f"The next assessment is {nxt['name']} (week {nxt['week']})" + (f", covering {los}" if los else "") + \
                ": rank what it covers higher."
        lang = document_language(d, self._s.settings)
        prompt = stressed.summary_prompt({"code": d["code"], "name": d["display_name"]}, lines, lang["language"], focus,
                                         lang["taught_in"])
        cancel = threading.Event()
        self._ai_cancel = cancel
        model = getattr(self._s.settings, "ai_model", "") or "sonnet"
        effort = resolve("chat", str(level or ""), settings=self._s.settings)

        def run() -> None:
            try:
                self._push({"type": "stressed", "phase": "start", "course_id": course_id})
                raw, res = ask_json(stressed.SYSTEM, prompt, model=model, effort=effort, cancel=cancel)
                points = stressed.clean_summary(raw, by_ref)
                if not points:
                    raise RuntimeError(t("{ai} found nothing to list. Try again.", ai=ai_name()))
                saved = {"items": points, "at": dt.datetime.now().isoformat(timespec="seconds"),
                         "model": model_label(model, effort), "from": [it["id"] for it in items],
                         "usage": res.get("usage") or {}}
                lib.set_meta(f"stressed-ai:{course_id}", json.dumps(saved, ensure_ascii=False))
                self._push({"type": "stressed", "phase": "done", "course_id": course_id})
            except Exception as exc:  # noqa: BLE001 - said plainly on the page
                from .cancel import Cancelled

                self._push({"type": "stressed", "phase": "error", "course_id": course_id,
                            "cancelled": isinstance(exc, Cancelled), "message": "" if isinstance(exc, Cancelled)
                            else net.plain_error(exc) if str(exc) else t("{ai} could not finish.", ai=ai_name())})
            finally:
                self._ai_cancel = None

        threading.Thread(target=run, name="stressed", daemon=True).start()
        return {"ok": True}

    # ---- quizzes
    @_safe
    def quiz_options(self, course_id: str) -> dict[str, Any]:
        """What the new-quiz dialog offers: the weeks with their files and lectures (as a study pack's), the
        announced assessments with the weeks they cover (the next one first), and the AI."""
        from . import quizzes, studypack
        from .ai import claude_info
        from .doclang import document_language
        from .views import course_detail

        d = course_detail(self._s.lib, self._s.settings, course_id)
        if d is None:
            return {"ok": False, "error": t("This course is no longer in your list.")}
        opts = studypack.options(self._s.lib, self._s.settings, course_id)
        w = weak_spots(self._s.lib, course_id, most=40)
        return {"ok": True, **opts, "assessments": quizzes.assessments(d), "quant": quizzes.quantitative(d),
                **document_language(d, self._s.settings), "ai": claude_info(),
                "model": self._s.settings.plan_model or self._s.settings.ai_model,
                "weak": {"missed": w["open"], "attempts": w["attempts"], "quizzes": w["quizzes"],
                         "los": [r["no"] for r in w["los"]], "topics": [r["name"] for r in w["topics"]]}}

    def _quiz_scope(self, course_id: str, scope: dict[str, Any]) -> tuple[dict[str, Any], str]:
        """The new quiz's scope; a weak-spot quiz's filled in from the questions still missed (weak_scope)."""
        sc = {**(scope or {}), "kind": "quiz"}
        if not sc.get("weak"):
            return sc, ""
        sc = weak_scope(self._s.lib, course_id, sc)
        if not sc["weeks"] and not sc["files"] and not sc["days"]:
            return sc, t("No missed questions to practise yet. Take a quiz first.")
        return sc, ""

    @_safe
    def quiz_estimate(self, course_id: str, scope: dict[str, Any]) -> dict[str, Any]:
        from . import quizzes
        from .plans import Scope

        sc, why = self._quiz_scope(course_id, scope)
        if why:
            return {"ok": False, "error": why}
        return {"ok": True, "estimate": quizzes.estimate(self._s.lib, self._s.settings, course_id, Scope.from_json(sc))}

    @_safe
    def quiz_create(self, course_id: str, scope: dict[str, Any]) -> dict[str, Any]:
        why = _ai_ready()
        if why:
            return {"ok": False, "error": why}
        sc, why = self._quiz_scope(course_id, scope)
        if why:
            return {"ok": False, "error": why}
        return self._start_plan(["quiz", course_id, sc], lambda: self._s.plans.create(course_id, sc))  # type: ignore[attr-defined]

    @_safe
    def quiz_get(self, plan_id: str) -> dict[str, Any]:
        p = self._s.lib.plan(plan_id)
        if p is None or (p.get("scope") or {}).get("kind") != "quiz":
            return {"ok": False, "error": t("This quiz no longer exists.")}
        p.pop("data", None)
        p.pop("usage", None)
        return {"ok": True, "quiz": p, "attempts": attempts(self._s.lib, plan_id)}

    @_safe
    def quiz_attempt(self, plan_id: str, attempt: dict[str, Any]) -> dict[str, Any]:
        """A try saved; its missed questions join the course's cards (Settings → Practice: "carded", their ids, for
        the page's Undo)."""
        from .cards import add_missed

        lib = self._s.lib
        p = lib.plan(plan_id)
        if p is None:
            return {"ok": False, "error": t("This quiz no longer exists.")}
        row = save_attempt(lib, plan_id, p["course_id"], attempt or {})
        carded = add_missed(lib, p["course_id"], plan_id, p.get("result") or {}, row["answers"]) \
            if getattr(self._s.settings, "mistake_cards", True) and p.get("status") == "done" else []
        return {"ok": True, "attempt": row, "attempts": attempts(lib, plan_id), "carded": carded}

    @_safe
    def quiz_delete(self, plan_id: str) -> dict[str, Any]:
        """The quiz, its tries and the cards its missed questions became."""
        from .cards import quiz_card_id

        lib = self._s.lib
        p = lib.plan(plan_id) or {}
        ids = [quiz_card_id(plan_id, q.get("q") or "") for q in (p.get("result") or {}).get("questions") or []
               if isinstance(q, dict)]
        self._s.plans.cancel(plan_id)
        lib.delete_plan(plan_id)
        with lib._lock:
            lib.db.execute("DELETE FROM quiz_attempts WHERE plan_id=?", (plan_id,))
            if ids:
                lib.db.execute(f"DELETE FROM card_state WHERE card_id IN ({','.join('?' * len(ids))})", ids)
            lib.db.commit()
        _bump()
        return {"ok": True}

    @_safe
    def quiz_question(self, plan_id: str, n: int, action: str) -> dict[str, Any]:
        """A question of a quiz removed (or back: "restore"), or its source marked wrong ("not_here", "here")."""
        if action not in ("remove", "restore", "not_here", "here"):
            return {"ok": False, "error": "action"}
        res = question_action(self._s.lib, plan_id, int(n), action)
        if res is None:
            return {"ok": False, "error": t("This quiz no longer exists.")}
        return {"ok": True, "removed": res["removed"], "not_here": res["not_here"]}

    @_safe
    def practice_moment(self, course_id: str, day_key: str, part: int = 1, at: float = 0) -> dict[str, Any]:
        """Where a question was taught: the slide on screen then, the transcript around it and a clip to play."""
        if str(day_key).split("|")[0] != course_id:
            return {"ok": False, "error": t("That lecture isn't part of this course.")}
        m = moment(self._s.lib, str(day_key), int(part or 1), float(at or 0))
        if m is None:
            return {"ok": False, "error": t("This lecture is no longer in your library.")}
        return {"ok": True, **m}

    @_safe
    def practice_weak(self, course_id: str, focus: str = "") -> dict[str, Any]:
        """How each learning outcome and topic went (mastery), counts only; ``focus``: an assessment's name (its
        learning outcomes, as the quiz dialog and the course page know them)."""
        from . import quizzes
        from .views import course_detail

        d = course_detail(self._s.lib, self._s.settings, course_id)
        if d is None:
            return {"ok": False, "error": t("This course is no longer in your list.")}
        los = None
        if focus:
            named = [e for e in quizzes.assessments(d) if e["name"] == focus]
            e = next((x for x in named if x["ahead"]), named[0] if named else None)
            los = list(e["los"]) if e else None
        m = mastery(self._s.lib, course_id, focus, los=los, outcomes=d.get("outcomes_en") or d.get("outcomes_tr") or [])
        open_ = mistakes(self._s.lib, course_id)
        if focus:
            keep = {(x["plan_id"], x["n"]) for r in m["los"] + m["topics"] for x in r["missed"]}
            open_ = [x for x in open_ if (x["plan_id"], x["n"]) in keep]
        return {"ok": True, **m, "mistakes": open_[:40]}

    @_safe
    def quiz_pdf(self, plan_id: str, how: str = "open") -> dict[str, Any]:
        """The quiz as a PDF in the app's document look: opened, saved where the student picks, or shown."""
        import shutil

        from . import quizzes

        p = self._s.lib.plan(plan_id)
        if p is None:
            return {"ok": False, "error": t("This quiz no longer exists.")}
        pdf = quizzes.make_pdf(self._s, p)
        if pdf is None:
            from .export import find_browser

            return {"ok": False, "error": t("The PDF could not be made: Microsoft Edge was not found.") if not find_browser()
                    else t("Microsoft Edge couldn't print the PDF. Try once more.")}
        if how == "save":           # as the Export menu's: the course's folder, "<code> - Practice quiz - <title>"
            course = self._s.lib.get_course(p["course_id"])
            title = (p.get("result") or {}).get("title") or p.get("title") or ""
            stem = " - ".join(x for x in ((course.code or course.name) if course else "", "Practice quiz", title) if x)
            to = self._save_course_file(p["course_id"], stem, ".pdf")  # type: ignore[attr-defined]
            if not to:
                return {"ok": True, "saved": ""}
            shutil.copyfile(pdf, to)
            return {"ok": True, "saved": str(to)}
        return self._open_here(pdf, show=how == "folder")  # type: ignore[attr-defined]

    # ---- flashcards
    @_safe
    def cards_session(self, course_id: str, weeks: Optional[list[int]] = None) -> dict[str, Any]:
        from .cards import session

        cards = session(self._s.lib, self._deck(course_id), course_id,
                        weeks={int(w) for w in weeks or [] if str(w).isdigit()} or None,
                        new_per_day=self._s.settings.cards_per_day)
        return {"ok": True, "cards": cards}

    @_safe
    def cards_grade(self, course_id: str, card_id: str, grade: str) -> dict[str, Any]:
        from .cards import GRADES, grade as keep

        if grade not in GRADES:
            return {"ok": False, "error": "grade"}
        return {"ok": True, "state": keep(self._s.lib, course_id, str(card_id), grade)}

    @_safe
    def cards_edit(self, course_id: str, card_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        """A card changed: its front and back ({"q", "a"}), or {"off": "aside" | "deleted" | ""}."""
        from .cards import edit

        ch = changes or {}
        off = ch.get("off")
        if off is not None and off not in ("aside", "deleted", ""):
            return {"ok": False, "error": "off"}
        if off is None and not str(ch.get("q") or "").strip() and not str(ch.get("a") or "").strip():
            return {"ok": False, "error": t("A card needs a front and a back.")}
        e = edit(self._s.lib, course_id, str(card_id), q=ch.get("q"), a=ch.get("a"), off=off)
        return {"ok": True, "card": e}

    @_safe
    def cards_bring_back(self, course_id: str) -> dict[str, Any]:
        """Every card set aside back in the course's deck."""
        from .cards import bring_back

        return {"ok": True, "back": bring_back(self._s.lib, course_id)}

    @_safe
    def mistakes_uncard(self, course_id: str, card_ids: list[str]) -> dict[str, Any]:
        """Undo, after a quiz: its missed questions out of the deck again (and not added again later)."""
        from .cards import edit

        ids = [str(x) for x in card_ids or [] if str(x).startswith("q")][:50]
        for cid in ids:
            edit(self._s.lib, course_id, cid, off="deleted")
        return {"ok": True, "removed": len(ids)}

    @_safe
    def cards_estimate(self, course_id: str, scope: dict[str, Any]) -> dict[str, Any]:
        from . import cards
        from .plans import Scope

        return {"ok": True, "estimate": cards.estimate(self._s.lib, self._s.settings, course_id,
                                                       Scope.from_json({**(scope or {}), "kind": "cards"}))}

    @_safe
    def cards_make(self, course_id: str, scope: dict[str, Any]) -> dict[str, Any]:
        why = _ai_ready()
        if why:
            return {"ok": False, "error": why}
        sc = {**(scope or {}), "kind": "cards", "days": [], "picked": True}
        if not sc.get("files"):
            return {"ok": False, "error": t("Pick at least one file.")}
        return self._start_plan(["cards", course_id, sc], lambda: self._s.plans.create(course_id, sc))  # type: ignore[attr-defined]

    @_safe
    def cards_set_delete(self, plan_id: str) -> dict[str, Any]:
        p = self._s.lib.plan_brief(plan_id)
        if p is None or (p.get("scope") or {}).get("kind") != "cards":
            return {"ok": False, "error": t("These cards no longer exist.")}
        self._s.plans.cancel(plan_id)
        self._s.lib.delete_plan(plan_id)
        return {"ok": True}

    @_safe
    def cards_export(self, course_id: str, how: str = "apkg", weeks: Optional[list[int]] = None) -> dict[str, Any]:
        """The course's deck (``weeks``: only those weeks' cards) for Anki: an .apkg opened right away ("open": Anki
        imports it), or saved where the student picks ("apkg", "txt": a text file Anki imports)."""
        from . import anki
        from .cards import anki_notes
        from .outputs.writer import course_dir, safe_name

        lib = self._s.lib
        course = lib.get_course(course_id)
        if course is None:
            return {"ok": False, "error": t("This course is no longer in your list.")}
        nos = {int(w) for w in weeks or [] if str(w).isdigit()}
        cards = [c for c in self._deck(course_id) if (not nos or c.get("week") in nos) and not c.get("aside")]
        if not cards:
            return {"ok": False, "error": t("This course has no flashcards yet.")}
        code = course.code or course.name
        notes = anki_notes(cards, code)
        deck_name = f"IEUStudia::{code}"
        desc = t("Flashcards from your {code} lectures and files, made with IEUStudia.", code=code)
        stem = safe_name(f"{code} flashcards" + (f" (weeks {min(nos)}-{max(nos)})" if nos else ""), 100)
        if how == "open":
            path = course_dir(self._s.output_root, course, lib.courses(True)) / "Flashcards" / f"{stem}.apkg"
            n = anki.write_apkg(path, deck_name, notes, desc)
            opened = self._open_here(path)  # type: ignore[attr-defined]
            return {**opened, "saved": str(path), "cards": n}
        ext = ".txt" if how == "txt" else ".apkg"
        to = self._save_course_file(course_id, stem, ext)  # type: ignore[attr-defined]     # (as the Export menu's)
        if not to:
            return {"ok": True, "saved": ""}
        n = (anki.write_tsv if how == "txt" else anki.write_apkg)(Path(to), deck_name, notes,
                                                                **({} if how == "txt" else {"description": desc}))
        return {"ok": True, "saved": str(to), "cards": n}

    @_safe
    def practice_source(self, course_id: str, resource_id: str, page: int = 1) -> dict[str, Any]:
        """A file page a question or card comes from, for the page viewer: the page as a picture (a PDF), else its
        text; with how many pages the file has."""
        from . import chatfiles

        m = self._s.lib.get_material(str(resource_id))
        if not m or m.get("course_id") != course_id:
            return {"ok": False, "error": t("That file isn't part of this course.")}
        texts = self._s.lib.material_pages(m["resource_id"])
        n = max(1, min(int(page or 1), max(1, len(texts))))
        path = Path(m.get("local_path") or "")
        out = {"ok": True, "name": m.get("file_name") or m.get("title") or "", "page": n, "pages": len(texts),
               "id": m["resource_id"], "text": texts[n - 1] if 0 < n <= len(texts) else "", "picture": ""}
        if path.is_file() and path.suffix.lower() == ".pdf":
            try:
                out["picture"] = chatfiles.pdf_page(path, n)
            except Exception:  # noqa: BLE001 - its text instead
                log.debug("page %s of %s not drawn", n, path, exc_info=True)
        return out
