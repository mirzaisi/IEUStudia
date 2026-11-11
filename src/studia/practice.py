"""The Practice tools' side of the page's bridge (web/practice.js): practice quizzes (quizzes.py), the course's
flashcards with spaced repetition (cards.py) and their Anki export (anki.py), and what the lecturer stressed
(stressed.py). api.Api inherits these methods, so they are called like its own (``self._s``: the services); each one
answers {"ok": false, "error"} instead of raising, as every API method does.

Quizzes and cards made from files are plans (kinds "quiz" and "cards", run by plans.PlanRunner); they are kept out of
the Study plans list (api.plan_list) and shown here. A student's tries at a quiz are kept in quiz_attempts, what they
know of each card in card_state: both in their own profile's library."""

from __future__ import annotations

import datetime as dt
import functools
import json
import logging
import threading
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from .i18n import t

log = logging.getLogger("studia")
PRACTICE_KINDS = ("quiz", "cards")


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
def save_attempt(lib: Any, plan_id: str, course_id: str, attempt: dict[str, Any]) -> dict[str, Any]:
    answers = [a for a in attempt.get("answers") or [] if isinstance(a, dict)][:200]
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
    be fewer than were asked (the list said "10 questions" of a quiz of 8)."""
    if not plan_ids:
        return {}
    with lib._lock:
        rows = lib.db.execute(f"SELECT id, result FROM plans WHERE id IN ({','.join('?' * len(plan_ids))})",
                              plan_ids).fetchall()
    out = {}
    for pid, raw in rows:
        try:
            qs = (json.loads(raw or "{}") or {}).get("questions")
        except (ValueError, AttributeError):
            continue
        if isinstance(qs, list):
            out[pid] = len(qs)
    return out


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
                "card_sets": sets, "stressed": self._stressed(course_id, d)}

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
        return {"ok": True, **opts, "assessments": quizzes.assessments(d), "quant": quizzes.quantitative(d),
                **document_language(d, self._s.settings), "ai": claude_info(),
                "model": self._s.settings.plan_model or self._s.settings.ai_model}

    @_safe
    def quiz_estimate(self, course_id: str, scope: dict[str, Any]) -> dict[str, Any]:
        from . import quizzes
        from .plans import Scope

        return {"ok": True, "estimate": quizzes.estimate(self._s.lib, self._s.settings, course_id,
                                                         Scope.from_json({**(scope or {}), "kind": "quiz"}))}

    @_safe
    def quiz_create(self, course_id: str, scope: dict[str, Any]) -> dict[str, Any]:
        why = _ai_ready()
        if why:
            return {"ok": False, "error": why}
        sc = {**(scope or {}), "kind": "quiz"}
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
        p = self._s.lib.plan_brief(plan_id)
        if p is None:
            return {"ok": False, "error": t("This quiz no longer exists.")}
        row = save_attempt(self._s.lib, plan_id, p["course_id"], attempt or {})
        return {"ok": True, "attempt": row, "attempts": attempts(self._s.lib, plan_id)}

    @_safe
    def quiz_delete(self, plan_id: str) -> dict[str, Any]:
        self._s.plans.cancel(plan_id)
        self._s.lib.delete_plan(plan_id)
        with self._s.lib._lock:
            self._s.lib.db.execute("DELETE FROM quiz_attempts WHERE plan_id=?", (plan_id,))
            self._s.lib.db.commit()
        return {"ok": True}

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
        cards = [c for c in self._deck(course_id) if not nos or c.get("week") in nos]
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
