"""The Exam room (4.2): one page for each coming quiz or exam, the syllabus's own exam weeks too (a final known only
from the syllabus has no announcement). Built from what IEUStudia already knows, without AI:

  the assessment  - the weeks it covers and where they come from (quizzes.assessments: announced, guessed, or the
                    student's own), its learning outcomes, the day (announced, the student's own, its week's
                    classes, else its week) and its share of the grade (the syllabus's grading);
  material        - the lectures (your section's) and files of those weeks, with the files tied to it (its posted
                    solutions): how many are transcribed, on this PC and read, and what "Get everything" fetches;
  what was said   - the heads-ups about it, and the moments stressed in those weeks (stressed.py);
  practice        - its quizzes (made for it, or on its weeks) with their tries, the flashcards of those weeks, how
                    each learning outcome went (practice.mastery) and the questions still missed;
  condensed       - the cheatsheets, review sheets, study packs and video plans made for it.

Readiness is three counts (material, practised, condensed), never one percentage. The page (web/examroom.js)
opens the existing tools from here, set to these weeks; the class days read are kept in the library (day_reads)."""

from __future__ import annotations

import datetime as dt
import functools
import logging
import re
from typing import Any, Callable, Iterable, Optional

from .i18n import t, tn

log = logging.getLogger("studia")

SAID_MOST = 6                 # what the lecturer said about it: this many
STRESSED_MOST = 8             # the moments stressed in its weeks: the ones that stand out most
CONDENSED_KINDS = ("sheet", "prep", "pack", "videos")

_QUIZ = re.compile(r"quiz|k[ıi]sa\s*s[ıi]nav", re.I)
_MIDTERM = re.compile(r"mid-?term|ara\s*s[ıi]nav|vize", re.I)
_FINAL = re.compile(r"final|d[öo]nem\s*sonu|yar[ıi]y[ıi]l\s*sonu", re.I)
# a grading part named like an exam that is no exam ("Final Project", "Final Jury", "Final Report")
_NOT_EXAM = re.compile(r"proje|project|portfol|jury|j[üu]ri|presentation|sunum|report|rapor|paper|essay|[öo]dev|"
                       r"homework|assignment|studio|st[üu]dyo", re.I)


def _safe(fn: Callable[..., Any]) -> Callable[..., Any]:
    """api.safe around the method (api.py is loaded by the time one is called: Api inherits them)."""
    @functools.wraps(fn)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        from .api import safe

        return safe(fn)(self, *args, **kwargs)
    return wrapper


# ------------------------------------------------------------------ the day
def _day_meta(course_id: str, key: str) -> str:
    return f"exam-day:{course_id}:{key}"


def own_day(lib: Any, course_id: str, key: str) -> str:
    """The exact day the student set for an assessment ("YYYY-MM-DD"), "" for none."""
    if lib is None or not course_id or not key:
        return ""
    try:
        v = (lib.get_meta(_day_meta(course_id, key)) or "").strip()
        dt.date.fromisoformat(v)
    except (ValueError, TypeError, AttributeError):
        return ""
    return v


def set_day(lib: Any, course_id: str, key: str, day: Any) -> str:
    """Keep the exact day the student says an assessment is on ("" clears it). ValueError for one that is no date."""
    v = str(day or "").strip()[:10]
    if v:
        dt.date.fromisoformat(v)
    lib.set_meta(_day_meta(course_id, key), v)
    return v


def when(d: dict[str, Any], e: dict[str, Any], lib: Any = None, today: Optional[dt.date] = None) -> dict[str, Any]:
    """When an assessment is: {"date", "time", "from", "others"}. "from": "announced" (an announcement gave the
    day), "yours" (the student set it), "class" (a quiz announced for a week: the first of its week's class days
    still ahead, "others" the rest), "week" (its week's first day: the exam schedule has the day), "" (unknown)."""
    day = (today or dt.date.today()).isoformat()
    if e.get("date"):
        return {"date": e["date"], "time": e.get("time") or "", "from": "announced", "others": []}
    mine = own_day(lib, d.get("id") or "", e.get("key") or "")
    if mine:
        return {"date": mine, "time": "", "from": "yours", "others": []}
    w = next((x for x in d.get("weeks") or [] if x.get("no") == e.get("week")), None)
    if not w or not w.get("start"):
        return {"date": "", "time": "", "from": "", "others": []}
    classes = [c["date"] for c in w.get("classes") or []] if e.get("kind") == "quiz" else []
    if classes:
        ahead = [c for c in classes if c >= day]
        return {"date": (ahead or classes[-1:])[0], "time": "", "from": "class", "others": ahead[1:]}
    return {"date": w["start"], "time": "", "from": "week", "others": []}


def days_left(on: dict[str, Any], today: Optional[dt.date] = None) -> Optional[int]:
    if not on.get("date"):
        return None
    return (dt.date.fromisoformat(on["date"]) - (today or dt.date.today())).days


# ------------------------------------------------------------------ the grade
def grade_share(grading: Iterable[dict[str, Any]], e: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Its part of the grade, from the syllabus's grading: {"activity", "weight" (all of that part, %), "number",
    "each" (one of them: a quiz is one of the quizzes' weight)}; None when the syllabus doesn't say. A "Final
    Project" or "Final Jury" is not the final exam."""
    rx = {"quiz": _QUIZ, "midterm": _MIDTERM, "final": _FINAL}.get(e.get("kind") or "")
    if rx is None:
        return None
    for g in grading or []:
        name = str(g.get("activity") or "")
        if not rx.search(name) or _NOT_EXAM.search(name):
            continue
        try:
            weight = float(g.get("weight") or 0)
            number = int(g.get("number") or 0)
        except (TypeError, ValueError):
            continue
        if weight > 0:
            return {"activity": name, "weight": weight, "number": number,
                    "each": round(weight / number, 2) if number > 1 else weight}
    return None


# ------------------------------------------------------------------ which one
def find(d: dict[str, Any], key: str) -> Optional[dict[str, Any]]:
    """The course's assessment with this key (quizzes.assessment_key), or this name (the one still ahead when two
    have it: "ready:<course>:<name>" from a notification, Get ready's old way in)."""
    from . import quizzes

    every = quizzes.of(d)
    hit = next((e for e in every if e.get("key") == key), None)
    if hit is not None:
        return hit
    named = [e for e in every if e["name"].casefold() == str(key or "").strip().casefold()]
    return next((e for e in named if e.get("ahead")), named[0] if named else None)


def _covered(d: dict[str, Any], e: dict[str, Any]) -> list[dict[str, Any]]:
    nos = set(e.get("weeks") or [])
    return [w for w in d.get("weeks") or [] if w["no"] in nos]


def _topic(w: dict[str, Any]) -> str:
    return w.get("topic_en") or w.get("topic_tr") or " · ".join(u["title"] for u in w.get("units") or []) or ""


# ------------------------------------------------------------------ material
def material(d: dict[str, Any], e: dict[str, Any], busy: Iterable[str] = (), reads: Iterable[str] = ()) -> dict[str, Any]:
    """The lectures and files of the weeks it covers. Lectures: your section's class days (one with no speech on
    Panopto is said apart, not one to transcribe or read); ``busy``: the class days being transcribed now. Files:
    those of the weeks (each once) and the course-wide or announcement files tied to it. "todo": the class days to
    transcribe, "missing": the files to download (what Get everything fetches)."""
    weeks = _covered(d, e)
    busy, reads = set(busy), set(reads)
    every = sorted((dict(x, week=w["no"]) for w in weeks for x in w.get("days") or []
                    if x.get("mine") is not False and x.get("parts")), key=lambda x: x["date"])
    silent = [x for x in every if x.get("no_sound") and not x.get("transcribed")]
    days = [x for x in every if x not in silent]
    files: dict[str, dict[str, Any]] = {}
    for w in weeks:
        for u in w.get("units") or []:
            for m in u.get("materials") or []:
                if not m.get("gone"):
                    files.setdefault(m["id"], {**m, "week": w["no"], "tied": False})
    tied = set(e.get("files") or [])
    for u in d.get("general") or []:
        for m in u.get("materials") or []:
            if m["id"] in tied and not m.get("gone"):
                files.setdefault(m["id"], {**m, "week": None, "tied": True})
    todo = [x["day_key"] for x in days if not x.get("transcribed") and not x.get("live") and x["day_key"] not in busy]
    out_days = [{"day_key": x["day_key"], "date": x["date"], "label": x.get("label") or "",
                 "kind_label": x.get("kind_label") or "", "week": x["week"], "transcribed": bool(x.get("transcribed")),
                 "any_done": bool(x.get("any_done")), "live": bool(x.get("live")), "busy": x["day_key"] in busy,
                 "read": x["day_key"] in reads} for x in days]
    out_files = [{"id": m["id"], "title": m.get("title") or "", "name": m.get("file_name") or m.get("title") or "",
                  "ext": m.get("ext") or "", "downloaded": bool(m.get("downloaded")), "week": m["week"],
                  "tied": m["tied"]} for m in files.values()]
    return {"days": out_days, "files": out_files, "lectures": len(days), "silent": len(silent),
            "transcribed": sum(1 for x in days if x.get("transcribed")),
            "busy": sum(1 for x in days if x["day_key"] in busy and not x.get("transcribed")),
            "readable": sum(1 for x in days if x.get("any_done")),
            "read": sum(1 for x in days if x.get("any_done") and x["day_key"] in reads),
            "downloaded": sum(1 for m in files.values() if m.get("downloaded")),
            "todo": todo, "missing": [m["id"] for m in files.values() if not m.get("downloaded")]}


def _in_weeks(weeks: list[dict[str, Any]], iso: str) -> bool:
    day = (iso or "")[:10]
    return any(w.get("start") and w["start"] <= day <= (w.get("end") or w["start"]) for w in weeks)


def said(d: dict[str, Any], e: dict[str, Any]) -> list[dict[str, Any]]:
    """What the lecturer said about it: the announcements it comes from, and what was said in class in its weeks
    about an assessment of its kind (the heads-ups, alerts.py)."""
    weeks = _covered(d, e)
    srcs = {s.get("id") for s in e.get("sources") or []}
    kinds = {"quiz"} if e.get("kind") == "quiz" else {e.get("kind"), "exam"}
    out = []
    for a in (d.get("alerts") or {}).get("items") or []:
        if a.get("source") == "announcement":
            if a.get("announcement_id") in srcs:
                out.append(a)
        elif _in_weeks(weeks, a.get("date") or "") and kinds & set(a.get("categories") or [a.get("category")]):
            out.append(a)
    return out[:SAID_MOST]


def stressed_moments(lib: Any, course_id: str, d: dict[str, Any], e: dict[str, Any]) -> list[dict[str, Any]]:
    """The moments stressed in its weeks that stand out most (the same detector quizzes use: stressed.py)."""
    from . import stressed

    try:
        items = stressed.course_items(lib, course_id)
    except Exception:  # noqa: BLE001 - the room without them
        log.debug("Exam room: no stressed moments", exc_info=True)
        return []
    day_week = {x["date"]: w["no"] for w in d.get("weeks") or [] for x in w.get("days") or []}
    nos = set(e.get("weeks") or [])
    if not nos:
        return []
    keep = ("id", "kind", "text", "hl", "lang", "day_key", "day", "part", "t", "ts", "n", "score")
    return [{k: it.get(k) for k in keep} | {"week": day_week.get(it["day"])}
            for it in stressed.ranked(items, nos, day_week)[:STRESSED_MOST]]


# ------------------------------------------------------------------ practice and what was condensed
def _for_it(scope: dict[str, Any], e: dict[str, Any]) -> bool:
    """A quiz or a sheet is for this assessment: made for it, or on its weeks only."""
    weeks = {int(n) for n in scope.get("weeks") or [] if str(n).lstrip("-").isdigit()}
    return bool(scope.get("focus")) and scope.get("focus") == e["name"] or bool(weeks) and weeks <= set(e["weeks"])


def practice_part(svc: Any, course_id: str, d: dict[str, Any], e: dict[str, Any],
                  now: Optional[dt.datetime] = None) -> dict[str, Any]:
    """Its quizzes with their last and best tries, the flashcards of its weeks (and how many were studied), how
    each learning outcome and topic went in those quizzes (counts) and their questions still missed."""
    from . import cards as cards_mod, practice

    lib, now = svc.lib, now or dt.datetime.now()
    tries = practice.attempts(lib, course_id=course_id)
    mine = [p for p in lib.plans(course_id) if (p.get("scope") or {}).get("kind") == "quiz" and _for_it(p["scope"], e)]
    kept = practice.kept_questions(lib, [p["id"] for p in mine if p["status"] == "done"])
    quizzes = [practice.quiz_brief(p, [a for a in tries if a["plan_id"] == p["id"]], kept.get(p["id"])) for p in mine]
    ids = {p["id"] for p in mine}
    answers = sum(len(a["answers"]) for a in tries if a["plan_id"] in ids)
    deck: dict[str, Any] = {"total": 0, "due": 0, "new": 0, "learnt": 0, "reviews": 0}
    try:
        cards = cards_mod.picked(cards_mod.deck(svc, course_id), set(e["weeks"]) or {-1})
        st = cards_mod.states(lib, course_id)
        end = cards_mod.study_day(now)[1].isoformat(timespec="seconds")
        deck.update(total=len(cards), due=sum(1 for c in cards if c["id"] in st and (st[c["id"]]["due"] or "") < end),
                    new=sum(1 for c in cards if c["id"] not in st),
                    learnt=sum(1 for c in cards if c["id"] in st and int(st[c["id"]]["step"]) < 0))
        if cards:
            with lib._lock:
                deck["reviews"] = int(lib.db.execute(
                    f"SELECT COUNT(*) FROM card_reviews WHERE course_id=? AND card_id IN ({','.join('?' * len(cards))})",
                    [course_id, *(c["id"] for c in cards)]).fetchone()[0])
    except Exception:  # noqa: BLE001 - the room without the cards' counts
        log.warning("Exam room: the flashcards of %s could not be read", course_id, exc_info=True)
    outcomes = d.get("outcomes_en") or d.get("outcomes_tr") or []
    m = practice.mastery(lib, course_id, "", los=list(e.get("los") or []) or None, outcomes=outcomes, plans=ids)
    if not e.get("los"):                              # (no outcomes named for it: no "not tested yet" for every one)
        m["los"] = [r for r in m["los"] if r["status"] != "untested"]
    missed = [x for x in practice.mistakes(lib, course_id) if x["plan_id"] in ids]
    return {"quizzes": quizzes, "answers": answers, "tried": sum(1 for q in quizzes if q["tries"]), "cards": deck,
            "mastery": m, "mistakes": missed[:40], "missed": len(missed)}


def condensed(lib: Any, course_id: str, e: dict[str, Any]) -> list[dict[str, Any]]:
    """The cheatsheets, review sheets, study packs and video plans made for it (or on its weeks only)."""
    out = []
    for p in lib.plans(course_id):
        sc = p.get("scope") or {}
        kind = sc.get("kind") or "videos"
        if kind in CONDENSED_KINDS and _for_it(sc, e):
            out.append({"id": p["id"], "kind": kind, "status": p.get("status") or "", "title": p.get("title") or "",
                        "weeks": sorted(sc.get("weeks") or []), "focus": sc.get("focus") or "",
                        "created_at": p.get("created_at") or ""})
    return out


# ------------------------------------------------------------------ the room
def _origin(every: list[dict[str, Any]], e: dict[str, Any]) -> dict[str, Any]:
    """Where its weeks come from, for the page to say: {"from", "since" (a guess: the assessment before it),
    "announcement" (the one that named them: {"id", "title"})}."""
    out: dict[str, Any] = {"from": e.get("weeks_from") or "guessed", "since": "", "announcement": None}
    if out["from"] == "announced" and e.get("covers_from"):
        src = next((s for s in e.get("sources") or [] if s.get("id") == e["covers_from"]), None)
        out["announcement"] = {"id": e["covers_from"], "title": (src or {}).get("title") or ""}
    guess = e.get("guessed") or []
    if guess and e.get("kind") != "final":
        before = [x for x in every if x.get("week") == guess[0] - 1 and x["key"] != e["key"]
                  and (x["kind"] == e["kind"] or e["kind"] == "quiz")]
        out["since"] = before[0]["name"] if before else ""
    return out


def room(svc: Any, course_id: str, key: str, jobs: Iterable[dict[str, Any]] = (),
         now: Optional[dt.datetime] = None) -> Optional[dict[str, Any]]:
    """Everything the Exam room shows for one assessment (by its key, or its name), or None when the course or the
    assessment is gone."""
    from . import quizzes
    from .views import course_detail

    lib, now = svc.lib, now or dt.datetime.now()
    jobs = list(jobs)
    d = course_detail(lib, svc.settings, course_id, jobs, now)
    if d is None:
        return None
    e = find(d, key)
    if e is None:
        return None
    running = {j.get("id") for j in jobs}
    busy = {r.day_key for r in lib.recordings(course_id) if r.delivery_id in running}
    keys = [x["day_key"] for w in _covered(d, e) for x in w.get("days") or []]
    mat = material(d, e, busy, lib.day_reads(keys))
    on = e.get("on") or when(d, e, lib, now.date())
    outcomes_en, outcomes_tr = d.get("outcomes_en") or [], d.get("outcomes_tr") or []
    prac = practice_part(svc, course_id, d, e, now)
    made = condensed(lib, course_id, e)
    every = quizzes.of(d)
    return {
        "course": {"id": d["id"], "code": d.get("code") or "", "name": d.get("display_name") or d.get("name") or ""},
        "assessment": {k: e.get(k) for k in ("key", "kind", "no", "name", "week", "weeks", "weeks_from", "guessed",
                                             "covers", "covers_from", "ahead", "files", "sources", "syllabus")}
        | {"los": [{"no": n, "en": outcomes_en[n - 1] if 0 < n <= len(outcomes_en) else "",
                    "tr": outcomes_tr[n - 1] if 0 < n <= len(outcomes_tr) else ""} for n in e.get("los") or []],
           "origin": _origin(every, e)},
        "when": {**on, "days": days_left(on, now.date())},
        "share": grade_share(d.get("grading") or [], e),
        "weeks": [{"no": w["no"], "start": w.get("start") or "", "end": w.get("end") or "", "topic": _topic(w),
                   "current": bool(w.get("current")), "past": bool(w.get("past")), "exam": bool(w.get("exam"))}
                  for w in d.get("weeks") or []],
        "material": mat,
        "said": said(d, e),
        "stressed": stressed_moments(lib, course_id, d, e),
        "practice": prac,
        "condensed": made,
        "counts": {"material": {"have": mat["transcribed"] + mat["downloaded"],
                                "of": mat["lectures"] + len(mat["files"])},
                   "practised": {"answers": prac["answers"], "quizzes": prac["tried"],
                                 "reviews": prac["cards"]["reviews"]},
                   "condensed": {"done": sum(1 for p in made if p["status"] == "done"), "of": len(made)}},
        "others": [{"key": x["key"], "name": x["name"], "ahead": x["ahead"]} for x in every if x["key"] != e["key"]],
    }


# ------------------------------------------------------------------ reminders
def weeks_label(nos: Iterable[int]) -> str:
    """[8, 9, 11] → "weeks 8–9, week 11" (the page's words)."""
    runs: list[list[int]] = []
    for n in sorted(set(nos)):
        if runs and n == runs[-1][1] + 1:
            runs[-1][1] = n
        else:
            runs.append([n, n])
    return ", ".join(t("week {n}", n=a) if a == b else t("weeks {a}–{b}", a=a, b=b) for a, b in runs)


def reminder_note(lib: Any, settings: Any, event: dict[str, Any]) -> str:
    """A quiz's or an exam's reminder says what it covers and what is still missing: "It covers weeks 8–9; 2
    lectures aren't transcribed yet." ("" for anything else, or when the course doesn't say)."""
    from .views import course_detail

    if event.get("kind") in ("assignment", "own") or not event.get("course_id"):
        return ""
    d = course_detail(lib, settings, event["course_id"])
    e = find(d, event.get("name") or "") if d else None
    if e is None or not e.get("weeks"):
        return ""
    m = material(d, e)
    missing = [tn(len(m["todo"]), "{n} lecture isn't transcribed yet", "{n} lectures aren't transcribed yet")
               if m["todo"] else "",
               tn(len(m["missing"]), "{n} file isn't on this PC", "{n} files aren't on this PC") if m["missing"] else ""]
    missing = [x for x in missing if x]
    weeks = weeks_label(e["weeks"])
    return (t("It covers {weeks}; {missing}.", weeks=weeks, missing=", ".join(missing)) if missing
            else t("It covers {weeks}.", weeks=weeks))


# ------------------------------------------------------------------ the page's bridge (api.Api inherits it)
class ExamRoomApi:
    """The Exam room's API methods, and the read ticks."""

    _s: Any

    def _room_of(self, course_id: str, key: str) -> dict[str, Any]:
        r = room(self._s, str(course_id or ""), str(key or ""), self._s.jobs.snapshot())
        if r is None:
            return {"ok": False, "gone": True, "error": t("This assessment is no longer in the course's plan.")}
        return {"ok": True, "room": r}

    @_safe
    def exam_room(self, course_id: str, key: str) -> dict[str, Any]:
        """Everything the Exam room shows for one quiz or exam (``key``: its key, or its name)."""
        return self._room_of(course_id, key)

    @_safe
    def exam_set_weeks(self, course_id: str, key: str, weeks: Optional[list[int]] = None) -> dict[str, Any]:
        """The weeks the student says it covers ([]: back to the announced or guessed ones); the room again."""
        from . import quizzes

        quizzes.set_weeks(self._s.lib, str(course_id), str(key), weeks or [])
        return self._room_of(course_id, key)

    @_safe
    def exam_set_day(self, course_id: str, key: str, day: str = "") -> dict[str, Any]:
        """The exact day the student says it is on ("": not known); the room again."""
        try:
            set_day(self._s.lib, str(course_id), str(key), day)
        except ValueError:
            return {"ok": False, "error": t("That is not a date.")}
        return self._room_of(course_id, key)

    @_safe
    def exam_get_everything(self, course_id: str, key: str) -> dict[str, Any]:
        """What its weeks still miss: their lectures transcribed (your section's, before a full sync's batch) and
        their files downloaded. {"lectures" (class days queued), "parts", "files", "unknown", "needs_section"}."""
        r = self._room_of(course_id, key)
        if not r["ok"]:
            return r
        m = r["room"]["material"]
        lectures = parts = unknown = 0
        needs_section = False
        for k in m["todo"]:
            res = self.transcribe_day(k, batch=True)  # type: ignore[attr-defined]
            if res.get("ok"):
                lectures += 1 if res.get("queued") else 0
                parts += res.get("queued") or 0
                unknown += res.get("unknown") or 0
                needs_section = needs_section or bool(res.get("needs_section"))
        files = self.materials_download(m["missing"]) if m["missing"] else {"queued": 0}  # type: ignore[attr-defined]
        return {"ok": True, "lectures": lectures, "parts": parts, "files": files.get("queued") or 0,
                "unknown": unknown, "needs_section": needs_section}

    @_safe
    def reads_set(self, day_keys: list[str], on: bool = True) -> dict[str, Any]:
        """Class days ticked read (a transcript opened, or ticked in the Exam room), or unticked."""
        return {"ok": True, "changed": self._s.lib.set_read([str(k) for k in day_keys or []][:3000], bool(on))}

    @_safe
    def reads_import(self, day_keys: list[str]) -> dict[str, Any]:
        """The ticks the page kept in its own storage before 4.2 (this profile's), into the library: once, and
        doing it again adds nothing."""
        return {"ok": True, "added": self._s.lib.set_read([str(k) for k in day_keys or [] if k][:3000], True)}
