"""Checked answers (4.2), the page's side (web/checks.js): a worked problem's result the student types is checked by the
app (numcheck.py, no AI), and the written answers of a finished try can be marked by the AI in one short request.

``quiz_check_number`` compares a typed result with the question's key (numcheck.reference: the key the app computed
again, or the answer itself when it is one result): "right" gives full marks at once ("mark": "app"), "wrong" shows
the solution and lets the student say "Partly (method right)", "unknown" (not one number it could read) leaves the
marking to the student, without an error.

``quiz_mark`` sends the try's written answers (short answers and worked problems with something typed, that the app
didn't check and the AI hasn't marked) with their model answers, not the course material: about 1-3k tokens. Each
comes back as 0, ½ or 1 with one line on what was missing ("mark": "ai"). ``quiz_mark_set`` is the student's own
mark over any of them (back to the app's or the AI's when it is the same). A try's score follows its marks, and
practice.mastery weighs each by who marked it (practice.MARKS: the app and the AI fully, the student half)."""

from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any, Optional

from . import numcheck
from .i18n import t
from .practice import _ai_ready, _bump, _safe, attempts, kept_answer

log = logging.getLogger("studia")
MOST = 1500                     # characters of one answer or model answer sent for marking
WRITTEN = ("short", "problem")
SYSTEM = ("You are a fair university teaching assistant marking a student's practice answers against the model "
          "answers. You mark only what is written, neither generously nor harshly, and never ask questions back.")


def _cut(s: Any, most: int = MOST) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= most else s[:most].rsplit(" ", 1)[0] + "…"


def question(result: dict[str, Any], n: Any) -> Optional[dict[str, Any]]:
    return next((q for q in result.get("questions") or [] if isinstance(q, dict) and q.get("n") == n), None)


def written(result: dict[str, Any], answers: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """The answers "Mark my written answers" sends: short answers and worked problems with something written, that
    the app didn't check (a typed result it compared) and the AI hasn't marked; not skipped, not removed."""
    removed = set(result.get("removed") or [])
    out = []
    for a in answers:
        q = question(result, a.get("n")) if isinstance(a, dict) else None
        if not q or q.get("type") not in WRITTEN or q["n"] in removed or a.get("skipped"):
            continue
        if a.get("mark") == "app" or "ai_got" in a:
            continue
        if str(a.get("text") or "").strip() or str(a.get("final") or "").strip():
            out.append((q, a))
    return out


def mark_prompt(items: list[tuple[dict[str, Any], dict[str, Any]]], language: str) -> str:
    rows = []
    for q, a in items:
        lines = [f"[{q['n']}] " + ("Worked problem" if q["type"] == "problem" else "Short answer"),
                 f"Question: {_cut(q.get('q'))}"]
        if q["type"] == "problem":
            if q.get("steps"):
                lines.append("Solution steps: " + " | ".join(_cut(s, 400) for s in q["steps"][:12]))
            lines.append(f"Result: {_cut(q.get('model'), 400)}")
            if q.get("unverified"):
                lines.append("(The app could not confirm this result: check it yourself.)")
        else:
            lines.append(f"Model answer: {_cut(q.get('model'))}")
            if q.get("points"):
                lines.append("A full answer must say: " + "; ".join(_cut(p, 300) for p in q["points"][:8]))
        lines.append(f"The student wrote: {_cut(a.get('text')) or '(nothing)'}")
        if str(a.get("final") or "").strip():
            lines.append(f"The student's final result: {_cut(a['final'], 200)}")
        rows.append("\n".join(lines))
    return f"""TASK: Mark a student's written answers to practice questions. Compare what the student wrote with the model answer (for a short answer, with the points a full answer must say; for a worked problem, with the solution).
Marks: 1 = right (everything that matters is there; other words, another valid method or a result rounded differently are fine); 0.5 = partly (the main idea or the right method, with a gap or a slip); 0 = wrong, or the main idea is missing.
Mark only what is written, not what the student may have meant. Two points that say the same thing in different ways (one follows directly from the other) are covered by either.
For each answer also write "missing": one short sentence to the student ("you") on what was missing or wrong, in {language or "English"}; "" when the mark is 1.
Answer with one JSON object: {{"marks": [{{"n": 1, "mark": 0.5, "missing": "…"}}]}}

=== ANSWERS ===
{(chr(10) * 2).join(rows)}"""


def _half(v: Any) -> Optional[float]:
    """0, ½ or 1 from what the AI wrote (0.5, "1/2", "½", "partly"…), else None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, str):
        s = v.strip().lower().replace(",", ".")
        words = {"right": 1.0, "correct": 1.0, "full": 1.0, "doğru": 1.0, "partly": 0.5, "partial": 0.5,
                 "half": 0.5, "kısmen": 0.5, "wrong": 0.0, "incorrect": 0.0, "none": 0.0, "yanlış": 0.0}
        if s in words:
            return words[s]
        s = {"½": "0.5", "1/2": "0.5"}.get(s, s)
        try:
            v = float(s)
        except ValueError:
            return None
    if not isinstance(v, (int, float)) or not 0 <= v <= 1:
        return None
    return round(float(v) * 2) / 2


def read_marks(data: Any, ns: list[int]) -> dict[int, tuple[float, str]]:
    """{question number: (mark, what was missing)} for the answers sent (``ns``), from the AI's JSON."""
    items = data.get("marks") or data.get("answers") or data.get("results") if isinstance(data, dict) else data
    out: dict[int, tuple[float, str]] = {}
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        try:
            n = int(re.sub(r"\D", "", str(it.get("n", it.get("question", "")))) or -1)
        except ValueError:
            continue
        m = _half(it.get("mark", it.get("score", it.get("got"))))
        if n in ns and m is not None:
            why = it.get("missing", it.get("why", it.get("feedback", "")))
            out[n] = (m, "" if m >= 1 else _cut(why, 300))
    return out


def set_mark(a: dict[str, Any], got: float) -> dict[str, Any]:
    """The student's own mark over the app's or the AI's: theirs ("self"), unless it is the same as one of those."""
    a["got"] = got
    for who, key in (("ai", "ai_got"), ("app", "app_got")):
        if key in a and float(a[key]) == got:
            a["mark"] = who
            break
    else:
        a["mark"] = "self"
    return a


def attempt(lib: Any, plan_id: str, attempt_id: str) -> Optional[dict[str, Any]]:
    return next((a for a in attempts(lib, plan_id) if a["id"] == attempt_id), None)


def save_answers(lib: Any, row: dict[str, Any], answers: list[dict[str, Any]]) -> dict[str, Any]:
    """A try's answers kept again (its score follows their marks)."""
    answers = [kept_answer(a) for a in answers]
    score = round(sum(a["got"] for a in answers), 2)
    with lib._lock:
        lib.db.execute("UPDATE quiz_attempts SET answers=?, score=? WHERE id=?",
                       (json.dumps(answers, ensure_ascii=False), score, row["id"]))
        lib.db.commit()
    _bump()
    return {**row, "answers": answers, "score": score}


class ChecksApi:
    """The checked answers' API methods (api.Api inherits them)."""

    _s: Any
    _ai_cancel: Optional[threading.Event]

    def _carded(self, plan: dict[str, Any], row: dict[str, Any], ns: list[int]) -> None:
        """Answers ``ns`` marked again: the ones now missed join the cards (as a try's do). Only those: the try's
        other missed questions keep their cards' spacing."""
        from .cards import add_missed

        if getattr(self._s.settings, "mistake_cards", True) and plan.get("status") == "done":
            changed = [a for a in row["answers"] if isinstance(a, dict) and a.get("n") in set(ns)]
            add_missed(self._s.lib, plan["course_id"], plan["id"], plan.get("result") or {}, changed)

    @_safe
    def quiz_check_number(self, plan_id: str, n: int, text: str) -> dict[str, Any]:
        """A typed result checked against the question's key: {"verdict": "right" | "wrong" | "unknown", "unit"}."""
        p = self._s.lib.plan(plan_id)
        q = question((p or {}).get("result") or {}, int(n)) if p else None
        if q is None:
            return {"ok": False, "error": t("This quiz no longer exists.")}
        return {"ok": True, **numcheck.mark(str(text or "")[:300], numcheck.reference(q))}

    @_safe
    def quiz_mark_set(self, plan_id: str, attempt_id: str, n: int, got: float) -> dict[str, Any]:
        """The student's own mark for one answer of a try (0, 0.5 or 1)."""
        lib = self._s.lib
        got = float(got)
        if got not in (0.0, 0.5, 1.0):
            return {"ok": False, "error": "got"}
        p = lib.plan(plan_id)
        row = attempt(lib, plan_id, str(attempt_id)) if p else None
        if row is None:
            return {"ok": False, "error": t("This try is no longer saved.")}
        q = question(p.get("result") or {}, int(n))
        a = next((x for x in row["answers"] if isinstance(x, dict) and x.get("n") == int(n)), None)
        if q is None or a is None or q.get("type") == "mc":
            return {"ok": False, "error": t("This answer can't be marked again.")}
        set_mark(a, got)
        row = save_answers(lib, row, row["answers"])
        self._carded(p, row, [int(n)])
        return {"ok": True, "attempt": row}

    @_safe
    def quiz_mark(self, plan_id: str, attempt_id: str, level: str = "") -> dict[str, Any]:
        """The try's written answers marked by the AI (one request, in the background): "quizmark" events, "start",
        "progress" (the wait), then "done" (the try's answers and score) or "error"."""
        from . import aiwait, net
        from .ai import ai_name, ask_json, provider
        from .cancel import Cancelled
        from .effort import resolve

        why = _ai_ready()
        if why:
            return {"ok": False, "error": why}
        lib = self._s.lib
        p = lib.plan(plan_id)
        row = attempt(lib, plan_id, str(attempt_id)) if p else None
        if row is None:
            return {"ok": False, "error": t("This try is no longer saved.")}
        result = p.get("result") or {}
        items = written(result, row["answers"])
        if not items:
            return {"ok": False, "error": t("There are no written answers to mark.")}
        model = getattr(self._s.settings, "ai_model", "") or "sonnet"
        effort = resolve("chat", str(level or ""), settings=self._s.settings)
        prompt = mark_prompt(items, str(result.get("language") or ""))
        sent = [q["n"] for q, _ in items]
        cancel = threading.Event()
        base = {"type": "quizmark", "plan_id": plan_id, "attempt_id": row["id"], "course_id": p["course_id"]}

        def run() -> None:
            wait = aiwait.Wait(lambda snap: self._push({**base, "phase": "progress", "wait": snap}),  # type: ignore[attr-defined]
                               ai=ai_name(), provider=provider(), model=model, level=effort, kind="mark", where="mark")
            try:
                self._push({**base, "phase": "start", "n": len(items), "wait": wait.snapshot()})  # type: ignore[attr-defined]
                with aiwait.tracking(cancel, wait):
                    data, _ = ask_json(SYSTEM, prompt, model=model, effort=effort, cancel=cancel, timeout_s=300)
                marks = read_marks(data, sent)
                if not marks:
                    raise RuntimeError(t("{ai}'s marks could not be read. Try again.", ai=ai_name()))
                now = attempt(lib, plan_id, row["id"]) or row           # (as it is now: a mark changed meanwhile)
                by = ai_name()
                marked = []
                for a in now["answers"]:
                    if isinstance(a, dict) and a.get("n") in marks and "ai_got" not in a:
                        got, missing = marks[a["n"]]
                        a.update(got=got, mark="ai", ai_got=got, ai_why=missing, ai_by=by)
                        marked.append(a["n"])
                done = save_answers(lib, now, now["answers"])
                self._carded(p, done, marked)
                self._push({**base, "phase": "done", "answers": done["answers"], "score": done["score"],  # type: ignore[attr-defined]
                            "marked": len(marks), "sent": len(sent)})
            except Exception as exc:  # noqa: BLE001 - said plainly on the page
                self._push({**base, "phase": "error", "cancelled": isinstance(exc, Cancelled),  # type: ignore[attr-defined]
                            "message": "" if isinstance(exc, Cancelled) else net.plain_error(exc) if str(exc)
                            else t("{ai} could not finish.", ai=ai_name())})
            finally:
                self._ai_cancel = None

        with getattr(self, "_starts_lock", threading.Lock()):     # (a double click: the second is told, not run)
            if self._ai_cancel is not None:
                return {"ok": False, "error": t("{ai} is already working on something.", ai=ai_name())}
            self._ai_cancel = cancel
        try:
            threading.Thread(target=run, name="quizmark", daemon=True).start()
        except BaseException:
            self._ai_cancel = None
            raise
        return {"ok": True, "n": len(items)}
