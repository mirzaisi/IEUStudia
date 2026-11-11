"""Practice quizzes: exam-style questions the chosen AI writes from a course's material (the chosen weeks' IEULearn
files and transcribed lectures, or one lecture with the slides captured from its video), in the language of study
documents (doclang.py: the one the course is taught in, unless the student chose English or Turkish): multiple choice with four options (and why each wrong one is wrong), short answers with what a full
answer says, and, for maths and engineering courses, worked problems with their steps. Every question says where it
comes from (a file's page, or a lecture's moment) so the app opens it there. What the lecturer stressed
(stressed.py), the learning outcomes of the next quiz or midterm (alerts.assessment_plan, the syllabus) and the
chosen weeks weigh more; each quiz says what it covers and what it left out, worked out in code.

A quiz is a plan of kind "quiz" (plans.PlanRunner runs it, as study packs), so it runs in the background, can be
stopped and resumed, and is kept with its course. Its stages:
  gather - the picked files and lectures (studypack.gather), the priorities, the quiz's language (no AI)
  write  - the questions (AI; JSON, or Markdown when an AI writes that: both are read, in the forms ChatGPT and
           Claude vary between)
  check  - code keeps the questions it can use (four options and one right answer, a source it can open), finds
           the source of one that named none by its words, and works out the coverage
The student's tries and scores are kept apart (practice.py, quiz_attempts), per profile."""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import logging
import math
import re
import threading
from pathlib import Path
from typing import Any, Optional

from .cancel import Cancelled
from .doclang import document_language, taught_in
from .i18n import N_, t, tn
from .outputs.prompts import in_language

log = logging.getLogger("studia")

STAGES = [("gather", N_("Reading the course material")), ("write", N_("Writing the questions")),
          ("check", N_("Checking the questions"))]
LABELS = dict(STAGES)
COUNTS = (5, 10, 15, 20)
DEFAULT_COUNT = 10
TYPES = ("mixed", "mc", "short", "problems")
LECTURE_SLIDES = 40           # captured slides sent with a one-lecture quiz at most
NEAR_S = 180                  # a question from this close to a stressed moment tests it
_QUANT = re.compile(r"^(?:MATH|EEE|EE|ELEC|PHYS|PHY|CE|ME|MECH|IE|ISE|CHEM|CHE|STAT|FENG|CS|SE|CMPE|COMP|ECON|"
                    r"FIN|ENGR|MAT|MCE|BME|AE|CIVIL|CEN|ENE)\b", re.I)
SYSTEM = ("You are an experienced university lecturer writing practice exam questions for a student, only from the "
          "course material you are given (lecture transcripts may mishear names and symbols: fix them from context). "
          "You write exactly what is asked, maths in LaTeX, and never ask questions back.")


# ------------------------------------------------------------------ gather (no AI)
course_language = taught_in      # the language the course is taught in (doclang.py; the documents' is document_language)


def quantitative(d: dict[str, Any], facts: Optional[dict[str, Any]] = None) -> bool:
    """A course with problems to work out (maths, engineering, physics, economics…): by its code, else by how much
    of its material is formulas."""
    if _QUANT.match(d.get("code") or ""):
        return True
    pack = (facts or {}).get("pack", "")
    return len(re.findall(r"[=^∫∑√±≤≥]|\\frac|\d\s*[+*/×÷-]\s*\d", pack)) > max(40, len(pack) / 400)


def _syllabus_exams(d: dict[str, Any], plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The syllabus' exam weeks no announcement covers, as Home, the course page and the Calendar show them: each as
    an assessment ("Midterm", "Final Exam"; a two-week exam period is one, until its last week). One a week away from
    an announced exam of its kind is that exam, moved."""
    said = [(e["kind"], e["week"]) for e in plan if e["kind"] != "quiz"]
    out: list[dict[str, Any]] = []
    last: Optional[dict[str, Any]] = None
    for w in sorted(d.get("weeks") or [], key=lambda w: w["no"]):
        name = (w.get("exam_label") or t("Exam")) if w.get("exam") and w.get("start") else ""
        kind = "final" if re.search(r"f[iİı]nal", name, re.I) else "midterm"
        if not name or any(n == w["no"] or (k == kind and abs(n - w["no"]) <= 1) for k, n in said):
            last = None
        elif last is not None and last["week"] == w["no"] - 1 and last["name"].casefold() == name.casefold():
            last["week"] = w["no"]
        else:
            last = {"kind": kind, "no": 0, "name": name, "week": w["no"], "los": [], "sources": [], "syllabus": True}
            out.append(last)
    return out


def assessments(d: dict[str, Any]) -> list[dict[str, Any]]:
    """The assessments the lecturer announced (alerts.assessment_plan) and the syllabus' exam weeks no announcement
    covers, each with the weeks it most likely covers (as the plan dialog: a quiz since the one before it, Midterm 2
    since Midterm 1) and whether it is still ahead."""
    past = {w["no"] for w in d.get("weeks") or [] if w.get("past")}
    plan = [e for e in (d.get("alerts") or {}).get("plan") or [] if e.get("week")]
    plan += _syllabus_exams(d, plan)
    out, prev, midterm, mids = [], 0, 0, 0
    for e in sorted(plan, key=lambda e: e["week"]):
        if e["kind"] == "midterm":
            mids += 1
            if e.get("syllabus"):                       # numbered among the midterms: a second one follows the first
                e = {**e, "no": mids}
            start = midterm + 1 if e["no"] >= 2 else 1
            midterm = e["week"]
        else:
            start = 1 if e["kind"] == "final" else prev + 1
        out.append({**e, "weeks": list(range(min(start, e["week"]), e["week"] + 1)), "ahead": e["week"] not in past})
        prev = e["week"]
    return out


def _focus(d: dict[str, Any], scope: Any) -> Optional[dict[str, Any]]:
    """The assessment the quiz prepares for (an announced one, or a syllabus exam week: the one still ahead when two
    have its name), with its learning outcomes' text (the syllabus's)."""
    named = [x for x in assessments(d) if x["name"] == scope.focus] if scope.focus else []
    e = next((x for x in named if x["ahead"]), named[0] if named else None)
    los = sorted(set(e["los"] if e else []) | set(scope.los or []))
    if not e and not los:
        return None
    outcomes = d.get("outcomes_en") or d.get("outcomes_tr") or []
    return {"name": e["name"] if e else "", "week": e["week"] if e else None,
            "los": [{"no": n, "text": outcomes[n - 1] if 0 < n <= len(outcomes) else ""} for n in los]}


def gather(lib: Any, settings: Any, course_id: str, scope: Any) -> dict[str, Any]:
    """The picked material (studypack.gather) and what weighs more: the assessment's outcomes, the stressed moments
    of these weeks; the course's language, whether it has problems to work out, each lecture part's length."""
    from . import stressed, studypack
    from .views import course_detail

    facts = studypack.gather(lib, settings, course_id, scope)
    d = course_detail(lib, settings, course_id) or {}
    for x in facts["lectures"]:
        for p in x["parts"]:
            rec = lib.get_recording(p["delivery_id"])
            p["dur"] = float(rec.duration_s or 0) if rec else 0.0
    nos = {w["no"] for w in facts["weeks"]}
    day_week = {}
    for w in d.get("weeks") or []:
        for day in w["days"]:
            day_week[day["date"]] = w["no"]
    items = stressed.course_items(lib, course_id)
    picked_days = {x["day_key"] for x in facts["lectures"]}
    mine = [it for it in stressed.ranked(items, nos, day_week)
            if not scope.days or it["day_key"] in picked_days]
    refs = {x["day_key"]: x["ref"] for x in facts["lectures"]}
    lines, by_ref = stressed.prompt_lines(mine, refs, 30, [it for it in items if it["kind"] == "not_needed"
                                                         and day_week.get(it["day"]) in nos])
    facts.update(**document_language(d, settings, facts), quant=quantitative(d, facts), focus=_focus(d, scope),
                 outcomes=d.get("outcomes_en") or d.get("outcomes_tr") or [], stressed_lines=lines,
                 stressed={ref: {k: it[k] for k in ("id", "day_key", "part", "t", "ts", "kind", "text")}
                           for ref, it in by_ref.items()},
                 one_lecture=bool(scope.days) and len(scope.days) == 1 and not scope.files)
    return facts


# ------------------------------------------------------------------ write (AI)
def mix(n: int, kinds: str, quant: bool) -> dict[str, int]:
    """How many questions of each kind: all of one kind, or (mixed) half multiple choice, a fifth short answers and
    the rest worked problems for a course with problems to work out; else seven in ten multiple choice."""
    n = max(1, int(n or DEFAULT_COUNT))
    if kinds == "mc":
        return {"mc": n, "short": 0, "problem": 0}
    if kinds == "short":
        return {"mc": 0, "short": n, "problem": 0}
    if kinds == "problems":
        return {"mc": 0, "short": 0, "problem": n} if quant else {"mc": 0, "short": n, "problem": 0}
    if quant:
        mc, short = max(1, round(n * 0.5)), max(1, round(n * 0.2)) if n >= 4 else 0
        return {"mc": mc, "short": short, "problem": max(0, n - mc - short)}
    mc = max(1, round(n * 0.7))
    return {"mc": mc, "short": n - mc, "problem": 0}


def write_prompt(scope: Any, facts: dict[str, Any], n: int, kinds: dict[str, int]) -> str:
    parts = [x for x in (f"{kinds['mc']} multiple-choice" if kinds["mc"] else "",
                         f"{kinds['short']} short-answer" if kinds["short"] else "",
                         f"{kinds['problem']} worked problems" if kinds["problem"] else "") if x]
    want = ", ".join(parts[:-1]) + " and " + parts[-1] if len(parts) > 1 else parts[0]
    prio = [f"1. The material below ({facts['span']}): every question comes from it and from nothing else."]
    f = facts.get("focus")
    if f and f.get("los"):
        los = "; ".join(f"LO{x['no']}" + (f": {x['text']}" if x["text"] else "") for x in f["los"])
        name = f"{f['name']} (week {f['week']})" if f.get("name") and f.get("week") else f.get("name") or "the next exam"
        prio.append(f"2. The student prepares for {name}, which covers these learning outcomes: {los}. Most "
                    "questions test them; tag each question with the outcomes it tests (\"los\").")
    elif f and f.get("name"):
        prio.append(f"2. The student prepares for {f['name']}" + (f" (week {f['week']})" if f.get("week") else "") + ".")
    if facts.get("stressed_lines"):
        prio.append(f"{len(prio) + 1}. What the lecturer stressed (the S moments below): more questions on these, "
                    "the ones tied to an exam first; tag each question with the moments it tests (\"stressed\"). "
                    "Nothing on what the lecturer said is not needed.")
    prio.append(f"{len(prio) + 1}. Otherwise spread the questions over all the topics of the material, one per topic "
                "before a second on any topic.")
    extra = f"\nThe student also asked: {scope.topics.strip()}" if (scope.topics or "").strip() else ""
    slides = ("\nThe slides captured from the lecture video are attached as pictures, each labelled [Part n, slide m]; "
              "the transcript's (slide m) marks show when each was on screen. Use them (formulas, figures, worked "
              "examples), and cite the lecture moment where the slide was shown.") if facts.get("one_lecture") else ""
    lectures = " (cite the [mm:ss] time written before the passage)" if facts["lectures"] else ""
    return f"""TASK: Write a practice quiz of exactly {n} exam-style questions: {want}. Write every question, option, answer and explanation {in_language(facts['language'], facts.get('taught_in', ''))}{extra}
Priorities, in this order:
{chr(10).join(prio)}
Answer with one JSON object:
{{"title": "a short title naming the topics (not the course code)",
 "topics": ["every topic of the material, a few words each, also those no question tests"],
 "questions": [
  {{"type": "mc", "question": "…", "options": ["…", "…", "…", "…"], "answer": "B", "explanation": "why the right option is right", "wrong": {{"A": "why this option is wrong", "C": "…", "D": "…"}}, "source": "F1 p.12", "topic": "…", "los": [3], "stressed": ["S2"], "level": "medium"}},
  {{"type": "short", "question": "…", "answer": "the model answer, 1–3 sentences", "points": ["what a full answer must say", "…"], "source": "L1 part 2 14:05", "topic": "…", "los": [], "stressed": [], "level": "easy"}},
  {{"type": "problem", "question": "a problem with numbers to work out", "steps": ["each step with its working", "…"], "answer": "the final result, with units", "source": "F2 p.4", "topic": "…", "los": [], "stressed": [], "level": "hard"}}]}}
Rules:
- Like the questions this course's lecturer asks in a quiz or midterm: the material's own methods, examples and notation, from understanding to applying; no trivia about the material itself ("what is on slide 5", "what did the lecturer joke about").
- Multiple choice: exactly four options, one of them right; the wrong ones are mistakes students really make; "wrong" says for each wrong option why it is wrong. The right answer's letter varies across the quiz. No "all/none of the above". In "explanation" and "wrong", never call an option by its letter or its place (the app reorders the options): say what it says.
- Short answers: answerable in a few sentences; "points" lists what a full answer must contain.
- Worked problems: realistic numbers, every step with its working, the result checked, units where they apply.
- "source": where the question comes from: a file page as "F1 p.12" or a lecture moment as "L1 part 2 14:05"{lectures}. Only the references listed below.
- "topic": the topic the question tests, written exactly as in "topics".
- Maths in LaTeX: $...$ in a sentence, $$...$$ on a line of its own. No two questions test the same thing.{slides}

=== WHAT THE LECTURER STRESSED ===
{facts.get('stressed_lines') or '(nothing found in these lectures)'}

=== COURSE MATERIAL ===
{facts['pack']}"""


# ------------------------------------------------------------------ reading the AI's answer
_LETTER = "ABCDEF"
_LETTERS = "abcdef"
_KEYS = {"question": ("question", "q", "prompt", "stem", "text", "title", "soru"),
         "type": ("type", "kind", "format", "question_type", "questionType"),
         "options": ("options", "choices", "alternatives", "answers_list", "secenekler", "şıklar"),
         "answer": ("answer", "correct", "correct_answer", "correctAnswer", "correct_option", "correctOption",
                    "right_answer", "key", "answer_key", "cevap"),
         "index": ("answer_index", "correct_index", "correctIndex", "answerIndex", "correct_idx"),
         "why": ("explanation", "why", "rationale", "reason", "reasoning", "feedback", "aciklama", "açıklama"),
         "wrong": ("wrong", "why_wrong", "distractors", "incorrect", "wrong_explanations", "distractor_explanations",
                   "option_explanations", "explanations", "wrong_answers"),
         "points": ("points", "key_points", "keyPoints", "rubric", "marking", "criteria", "must_include"),
         "model": ("model_answer", "modelAnswer", "expected_answer", "sample_answer", "final_answer", "finalAnswer",
                   "result"),
         "steps": ("steps", "solution_steps", "solutionSteps", "working", "worked_solution", "workedSolution",
                   "solution"),
         "source": ("source", "sources", "ref", "reference", "references", "from", "cite", "citation", "kaynak"),
         "topic": ("topic", "subject", "concept", "konu"),
         "los": ("los", "learning_outcomes", "lo", "outcomes", "learningOutcomes"),
         "stressed": ("stressed", "stressed_refs", "stressed_moments", "s_refs"),
         "level": ("level", "difficulty")}


def _get(d: dict[str, Any], what: str) -> Any:
    for k in _KEYS[what]:
        if k in d and d[k] not in (None, "", [], {}):
            return d[k]
    return None


def _text(v: Any, limit: int = 4000) -> str:
    if isinstance(v, (list, tuple)):
        v = "\n".join(_text(x, limit) for x in v)
    elif isinstance(v, dict):
        v = _get(v, "question") or v.get("text") or v.get("value") or ""
    s = str(v or "").replace("\r\n", "\n").strip()
    return s[:limit]


def _kind(v: Any, has_options: bool, has_steps: bool) -> str:
    s = re.sub(r"[\s_-]+", "", str(v or "").lower())
    if s in ("mc", "mcq", "multiplechoice", "choice", "multiple", "test", "coktansecmeli", "çoktanseçmeli",
             "singlechoice", "singleanswer"):
        return "mc"
    if s.startswith(("problem", "worked", "calculation", "numerical", "computation", "exercise", "calc", "solve")):
        return "problem"
    if s.startswith(("short", "open", "written", "essay", "kısa", "kisa", "freeresponse", "freetext", "explain")):
        return "short"
    return "mc" if has_options else "problem" if has_steps else "short"


_PREFIX = re.compile(r"^\s*(?:\*\*)?\(?([A-Fa-f])(?:\)|\.|:|\s*[-–]\s)\s*(?:\*\*)?\s*")


def _options(v: Any) -> tuple[list[str], Optional[int], list[str]]:
    """Options as written (strings, objects, or a letter → text map): their texts, the one marked right (if one is),
    and the reasons written on them."""
    texts, right, why = [], None, []
    if isinstance(v, dict):
        v = [v[k] for k in sorted(v, key=lambda k: str(k).strip().upper())]
    for i, o in enumerate(v if isinstance(v, list) else []):
        if isinstance(o, dict):
            texts.append(_text(o.get("text") or o.get("option") or o.get("label") or o.get("value")
                               or o.get("content") or "", 800))
            if o.get("correct") is True or o.get("is_correct") is True or o.get("isCorrect") is True:
                right = i
            why.append(_text(_get(o, "why") or "", 800))
        else:
            texts.append(_text(o, 800))
            why.append("")
    if texts and all(_PREFIX.match(x) for x in texts):
        texts = [_PREFIX.sub("", x, count=1) for x in texts]
    return texts, right, why


def _norm(s: str) -> str:
    return re.sub(r"[\W_]+", " ", str(s or "").lower()).strip()


def _same(s: str) -> str:
    """The key of "the same text": case and spaces only (in maths every symbol counts)."""
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


def _answer_index(v: Any, texts: list[str], zero: Optional[bool]) -> Optional[int]:
    """The right option from what the AI wrote: a letter ("B", "(b)", "Option B", "B) …"), the option's text, or a
    number (``zero``: the quiz counts from 0)."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)) and float(v).is_integer():
        n = int(v)
        n = n if zero or (zero is None and n == 0) else n - 1
        return n if 0 <= n < len(texts) else None
    if isinstance(v, list) and len(v) == 1:
        return _answer_index(v[0], texts, zero)
    if isinstance(v, dict):
        return _answer_index(v.get("letter") or v.get("option") or v.get("text") or v.get("index"), texts, zero)
    s = str(v).strip()
    norm = _norm(s)
    if not re.fullmatch(r"\(?[A-Fa-f]\)?[.):]?", s):     # the option's own text ("a constant" is no letter A)
        for i, x in enumerate(texts):
            if norm and (_same(x) == _same(s) or _norm(x) == norm):
                return i
    m = re.match(r"^\s*(?:(?:option|choice|answer|seçenek|şık|cevap)\s*[:\-]?\s*)?\(?([A-Fa-f])\)?(?=$|[\s.):,])", s, re.I)
    if m:
        k = _LETTERS.index(m.group(1).lower())
        return k if k < len(texts) else None
    if re.fullmatch(r"\d", s):
        return _answer_index(int(s), texts, zero)
    for i, x in enumerate(texts):
        if len(norm) > 3 and _norm(x).startswith(norm):
            return i
    return None


_NAMED = re.compile(r"^\s*(?:option\s*|choice\s*|seçenek\s*)?\(?([A-Fa-f])\)?\s*(?:[:.)–-]|is wrong|is incorrect)\s*(.*)$",
                    re.I | re.S)


def _wrong(v: Any, n: int, right: Optional[int], written: list[str]) -> list[str]:
    """Why each option is wrong, by option (the right one's ""): from a letter map, a list for every option or for
    the wrong ones only, lines or objects naming their option ("A: …", "(C) …"), or the reasons on the options."""
    out = [w if i != right else "" for i, w in enumerate(written)] + [""] * max(0, n - len(written))
    out = out[:n]

    def put(letter: str, why: Any) -> None:
        k = _LETTERS.index(letter.lower())
        if k < n:
            out[k] = _text(why, 800)

    if isinstance(v, dict):
        for k, why in v.items():
            m = re.match(r"\s*(?:option\s*)?\(?([A-Fa-f])\b", str(k), re.I)
            if m:
                put(m.group(1), why)
    elif isinstance(v, list):
        named = [_NAMED.match(str(x)) for x in v] if v and all(isinstance(x, str) for x in v) else []
        if v and all(isinstance(x, dict) for x in v):
            for x in v:
                m = re.match(r"\s*\(?([A-Fa-f])\b", str(x.get("option") or x.get("letter") or x.get("choice") or ""))
                if m:
                    put(m.group(1), _get(x, "why") or x.get("text") or "")
        elif named and all(named):
            for m in named:
                put(m.group(1), m.group(2).strip())
        elif len(v) == n:
            out = [_text(x, 800) if i != right else "" for i, x in enumerate(v)]
        elif right is not None and len(v) == n - 1:
            rest = iter(_text(x, 800) for x in v)
            out = ["" if i == right else next(rest) for i in range(n)]
    elif isinstance(v, str) and v.strip():
        for m in re.finditer(r"(?:^|\n|;\s*)\s*(?:option\s*)?\(?([A-Fa-f])\)?\s*[:.)-]\s*([^\n;]+)", v, re.I):
            put(m.group(1), m.group(2).strip())
    if right is not None and 0 <= right < n:
        out[right] = ""
    return out


def _steps(v: Any) -> list[str]:
    if isinstance(v, list):
        return [_text(x, 1500) for x in v if _text(x, 1500)]
    s = _text(v, 6000)
    if not s:
        return []
    parts = re.split(r"\n\s*(?:(?:step\s*)?\d+[.):]|[-*•])\s+|\n{2,}", "\n" + s, flags=re.I)
    parts = [p.strip() for p in parts if p.strip()]
    return parts if len(parts) > 1 else [s]


def _list(v: Any) -> list[str]:
    if isinstance(v, list):
        return [_text(x, 600) for x in v if _text(x, 600)]
    s = _text(v, 3000)
    return [x.strip(" -*•\t") for x in re.split(r"\n|;\s+", s) if x.strip(" -*•\t")] if s else []


def _ints(v: Any) -> list[int]:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return [int(v)]
    if isinstance(v, str):
        return [int(x) for x in re.findall(r"\d{1,2}", v)]
    return [n for x in v if (n := next(iter(_ints(x)), None)) is not None] if isinstance(v, list) else []


def _refs(v: Any) -> list[str]:
    if isinstance(v, str):
        return re.findall(r"S\d+", v)
    return [m.group(0) for x in v if (m := re.search(r"S\d+", str(x)))] if isinstance(v, list) else []


def _json_body(text: str) -> Any:
    """The JSON in an answer: whole, in a ```json fence, or the list or object it starts with after a sentence
    (whichever comes first: "Sure! [{…}] Good luck." is a list)."""
    from .ai import JSON_FENCE, _tex_backslashes

    body = (text or "").strip()
    fence = JSON_FENCE.search(body)
    body = fence.group(1).strip() if fence else body
    if "\\" in body:
        body = _tex_backslashes(body)
    spans = [(body.find(a), body.rfind(b)) for a, b in (("[", "]"), ("{", "}"))]
    for a, b in [(0, len(body) - 1)] + sorted(x for x in spans if 0 <= x[0] < x[1]):
        try:
            return json.loads(body[a:b + 1])
        except ValueError:
            continue
    return None


def _question_lists(data: Any) -> tuple[dict[str, Any], list[tuple[str, Any]]]:
    """The quiz object and its questions, each with the kind its list's name says ({"multiple_choice": […],
    "problems": […]} as some answers group them)."""
    if isinstance(data, list):
        return {}, [("", q) for q in data]
    if not isinstance(data, dict):
        return {}, []
    if _get(data, "question") and (_get(data, "options") or _get(data, "answer")):
        return {}, [("", data)]                       # one question on its own
    for k in ("quiz", "data", "result", "output"):
        if isinstance(data.get(k), dict) and not any(isinstance(v, list) for v in data.values()):
            data = data[k]
            break
    out = []
    for k, v in data.items():
        if not isinstance(v, list) or k in ("topics", "los", "outcomes"):
            continue
        name = re.sub(r"[\s_-]+", "", str(k).lower())
        hint = "mc" if "choice" in name or name in ("mcq", "mcqs", "mc") else "problem" if "problem" in name \
            else "short" if "short" in name or "open" in name else ""
        if name in ("questions", "items", "quiz", "sorular") or hint:
            out += [(hint, q) for q in v if isinstance(q, (dict, str))]
    return data, out


def read_answer(text: str) -> dict[str, Any]:
    """The AI's quiz as {"title", "topics", "questions": [raw question dicts with their "type" hint]}: its JSON
    (in a fence, after a sentence, a bare list, grouped by kind), else the questions written as Markdown."""
    data = _json_body(text)
    quiz, qs = _question_lists(data)
    if qs:
        out = []
        for hint, q in qs:
            q = q if isinstance(q, dict) else {"question": q}
            if hint and not _get(q, "type"):
                q = {**q, "type": hint}
            out.append(q)
        topics = quiz.get("topics") if isinstance(quiz.get("topics"), list) else []
        return {"title": _text(quiz.get("title"), 200), "topics": [_text(x, 200) for x in topics], "questions": out}
    return read_markdown(text)


_MD_Q = re.compile(r"^\s*(?:#{1,4}\s*)?(?:\*\*)?\s*(?:(?:question|soru|q)\s*)?(\d{1,2})\s*(?:[.):]|\*\*)\s*(?:\*\*)?\s*(.*)$",
                   re.I)
_MD_OPT = re.compile(r"^\s*(?:[-*]\s*)?(?:\*\*)?\(?([A-Da-d])(?:\)|\.|:)\s*(?:\*\*)?\s*(.+)$")
_MD_LABEL = re.compile(r"^\s*(?:[-*]\s*)?(?:\*\*|__)?\s*(correct answer|answer|final answer|doğru cevap|doğru yanıt|cevap|"
                       r"yanıt|explanation|why|açıklama|source|kaynak|from|solution|steps|çözüm|adımlar|key points|"
                       r"points|anahtar noktalar|why the others are wrong|wrong options|topic|konu|type)\s*(?:\*\*|__)?\s*"
                       r"[:：]\s*(?:\*\*|__)?\s*(.*)$", re.I)
_MD_HEAD = re.compile(r"(?:(?:question|soru|q)\s*(\d{1,2})(?:\s*(?:of|/)\s*\d{1,2})?|(\d{1,2})\s*[.)]?\s*(?:question|soru))"
                      r"\s*[.:)]?\s*(?:\([^()\n]{1,40}\))?", re.I)


def _md_heading(line: str) -> Optional[int]:
    """The number of a line that only heads a question, whose text is on the lines after it ("## Question 1",
    "**Soru 2**", "### Q3", "### 4.", "Question 5 (multiple choice)"); else None."""
    s = line.strip()
    marked = s.startswith(("#", "**", "__"))
    s = re.sub(r"^#{1,4}\s*", "", s).replace("**", "").replace("__", "").strip()
    m = _MD_HEAD.fullmatch(s) or (re.fullmatch(r"(\d{1,2})\s*[.):]?", s) if marked else None)
    return int(next(g for g in m.groups() if g)) if m else None


_LABELS_MD = {"correct answer": "answer", "answer": "answer", "doğru cevap": "answer", "doğru yanıt": "answer",
              "cevap": "answer", "yanıt": "answer", "final answer": "final", "explanation": "why", "why": "why",
              "açıklama": "why", "source": "source", "kaynak": "source", "from": "source", "solution": "steps",
              "steps": "steps", "çözüm": "steps", "adımlar": "steps", "key points": "points", "points": "points",
              "anahtar noktalar": "points", "why the others are wrong": "wrong", "wrong options": "wrong",
              "topic": "topic", "konu": "topic", "type": "type"}


def read_markdown(text: str) -> dict[str, Any]:
    """Questions written as Markdown ("1. …" / "### Question 1: …", or a heading of its own, "## Question 1", with the
    question on the lines after it; options "A) …", "**Answer:** B", "Explanation:", "Source: F1 p.3", "Solution:"
    with numbered steps)."""
    title = ""
    qs: list[dict[str, Any]] = []
    cur: Optional[dict[str, Any]] = None
    field, blank, headed = "", True, False
    for raw in (text or "").replace("\r\n", "\n").split("\n"):
        line = raw.rstrip()
        after_blank, blank = blank, not line.strip()
        if line.strip() and _md_heading(line) is not None:       # its text follows
            cur = {"question": "", "options": []}
            qs.append(cur)
            field, headed = "question", True
            continue
        if not title and re.match(r"^\s*#\s+\S", line) and not qs and cur is None:
            title = re.sub(r"^\s*#\s+", "", line).strip("*# ")
            continue
        m = _MD_Q.match(line)
        if headed and m and not re.match(r"^\s*(?:#|question|soru|q\s*\d)", line, re.I):
            m = None                         # questions under headings: a numbered line is a list in the current one
        # a numbered line in a list of steps is a step, unless it is marked as a question or is the next question's
        # number after a blank line
        listing = cur is not None and field in ("steps", "points", "wrong") and not (
            re.match(r"^\s*(?:#|\*\*|question|soru|q\s*\d)", line, re.I) or (after_blank and m and int(m.group(1)) == len(qs) + 1))
        if m and m.group(2).strip() and not listing:
            cur = {"question": m.group(2).strip(" *"), "options": []}
            qs.append(cur)
            field = "question"
            continue
        if cur is None:
            continue
        lab = _MD_LABEL.match(line)
        if lab:
            field = _LABELS_MD[lab.group(1).lower()]
            value = lab.group(2).strip(" *")
            if field in ("steps", "points", "wrong"):
                cur.setdefault(field, [])
                if value:
                    cur[field].append(value)
            elif field == "final":
                cur["model_answer"] = value
            else:
                cur[{"why": "explanation"}.get(field, field)] = value
            continue
        opt = _MD_OPT.match(line)
        if opt and field in ("question", "options") and len(cur["options"]) < 6:
            cur["options"].append(opt.group(2).strip())
            field = "options"
            continue
        if not line.strip():
            continue
        body = re.sub(r"^\s*(?:\d+[.)]|[-*•])\s+", "", line).strip()
        if field in ("steps", "points", "wrong"):
            cur[field].append(body)
        elif field == "question":
            cur["question"] = (cur["question"] + "\n" + line.strip()).strip()
        elif field in ("why", "explanation"):
            cur["explanation"] = (cur.get("explanation", "") + "\n" + line.strip()).strip()
        elif field == "answer" and not cur["options"]:
            cur["answer"] = (cur.get("answer", "") + "\n" + line.strip()).strip()
    for q in qs:
        if not q["options"]:
            q.pop("options")
    return {"title": title, "topics": [], "questions": qs}


def answered(raw: dict[str, Any]) -> bool:
    """Whether a question read has something to check an answer by (options, an answer, steps or the points a full
    answer makes). None has: the answer was misread (a worked solution's numbered steps taken for questions)."""
    return any(isinstance(q, dict) and any(_get(q, k) for k in ("options", "answer", "index", "steps", "points"))
               for q in raw.get("questions") or [])


# ------------------------------------------------------------------ where a question comes from
_REF_F = re.compile(r"\b(F\d{1,3})\b(?:[\s,;:]*(?:pp?\.?|pages?|sayfa|s\.)\s*(\d{1,4}))?", re.I)
_REF_L = re.compile(r"\b(L\d{1,3})\b(?:[\s,;:·]*(?:part|bölüm|p\.?)\s*(\d{1,2}))?(?:[\s,;:·@]*(?:at\s*)?"
                    r"(\d{1,2}:\d{2}(?::\d{2})?))?", re.I)


def _secs(s: str) -> Optional[float]:
    if not s or not re.fullmatch(r"\d{1,2}(?::\d{2}){1,2}", s.strip()):
        return None
    n = 0.0
    for x in s.strip().split(":"):
        n = n * 60 + int(x)
    return n


def parse_source(v: Any) -> Optional[dict[str, Any]]:
    """The source the AI named: {"ref": "F1", "page": 12} or {"ref": "L1", "part": 2, "t": 845.0} (page, part and
    time may be missing); None when it named none."""
    if isinstance(v, list):
        for x in v:
            got = parse_source(x)
            if got:
                return got
        return None
    if isinstance(v, dict):
        ref = str(v.get("ref") or v.get("file") or v.get("lecture") or v.get("id") or v.get("reference") or "")
        m = re.search(r"\b([FL]\d{1,3})\b", ref, re.I)
        if not m:
            return parse_source(" ".join(str(x) for x in v.values() if isinstance(x, (str, int))))
        ref = m.group(1).upper()
        if ref.startswith("F"):
            pages = v.get("pages")
            page = _ints(v.get("page") or (pages[0] if isinstance(pages, list) and pages else pages) or "")
            return {"ref": ref, "page": page[0] if page else None}
        part = _ints(v.get("part") or "")
        at = v.get("at") or v.get("time") or v.get("from") or v.get("t") or v.get("timestamp") or ""
        secs = float(at) if isinstance(at, (int, float)) and not isinstance(at, bool) else _secs(str(at))
        return {"ref": ref, "part": part[0] if part else None, "t": secs}
    s = str(v or "")
    m = _REF_F.search(s)
    if m:
        return {"ref": m.group(1).upper(), "page": int(m.group(2)) if m.group(2) else None}
    m = _REF_L.search(s)
    if m:
        return {"ref": m.group(1).upper(), "part": int(m.group(2)) if m.group(2) else None,
                "t": _secs(m.group(3) or "")}
    return None


def check_source(src: Optional[dict[str, Any]], facts: dict[str, Any]) -> Optional[dict[str, Any]]:
    """A source the app can open: a file of the material and a page it has, or a lecture of it with a part it has and
    a time within that part (rounded into it); None for anything else."""
    from .outputs.common import format_ts

    if not src:
        return None
    if src["ref"].startswith("F"):
        f = next((x for x in facts["files"] if x["ref"] == src["ref"]), None)
        page = src.get("page")
        if not f or not page or not 1 <= int(page) <= f["pages"]:
            return None
        return {"kind": "file", "ref": f["ref"], "id": f["id"], "name": f["name"], "page": int(page), "week": f["week"]}
    x = next((y for y in facts["lectures"] if y["ref"] == src["ref"]), None)
    if not x:
        return None
    parts = {p["part"]: p for p in x["parts"]}
    part = src.get("part") or (min(parts) if len(parts) == 1 else None)
    at = src.get("t")
    if part not in parts or at is None:
        return None
    dur = parts[part].get("dur") or 0
    if dur and at > dur + 30:
        return None
    at = min(float(at), dur) if dur else float(at)
    return {"kind": "lecture", "ref": x["ref"], "day_key": x["day_key"], "date": x["date"], "week": x["week"],
            "part": part, "t": at, "ts": format_ts(at)}


_STOP = set("""about above after again against also among because been before being below between both could does
doing down during each even every from further have having here into itself just more most much must never only
other over same should since some such than that their them then there these they this those through under until
very what when where which while with within would your yours ve bir bu şu için ile gibi daha çok olan olarak ise
veya hem ama ancak kadar sonra önce nasıl neden hangi""".split())


def _content_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[^\W\d_]{4,}", (text or "").lower()) if w not in _STOP}


def locate(question: dict[str, Any], units: list[tuple[dict[str, Any], set[str]]],
           least: int = 3) -> Optional[dict[str, Any]]:
    """The source of a question that named none the app could open: the file page or lecture passage that shares
    most words with it (at least ``least``, and a fifth of its words; the shorter one when two share as many),
    else None."""
    words = _content_words(" ".join([question.get("q", ""), question.get("why", ""), question.get("model", ""),
                                     " ".join(question.get("steps") or []), " ".join(question.get("options") or [])]))
    need = max(least, round(len(words) * 0.2))
    best, score = None, 0.0
    for src, w in units:
        common = len(words & w)
        s = common - min(0.5, len(w) / 2000)
        if common >= need and s > score:
            best, score = src, s
    return best


def source_units(lib: Any, course_id: str, facts: dict[str, Any]) -> list[tuple[dict[str, Any], set[str]]]:
    """Every file page and lecture passage of the material, with its words (for locate)."""
    from .outputs.common import format_ts
    from .studypack import lecture_text

    out: list[tuple[dict[str, Any], set[str]]] = []
    for f in facts["files"]:
        for n, page in enumerate(lib.material_pages(f["id"]), 1):
            if page.strip():
                out.append(({"kind": "file", "ref": f["ref"], "id": f["id"], "name": f["name"], "page": n,
                             "week": f["week"]}, _content_words(page)))
    for x in facts["lectures"]:
        part = 0
        for line in lecture_text(lib, course_id, x["day_key"]).split("\n"):
            m = re.match(r"## Part (\d+)", line)
            if m:
                part = int(m.group(1))
                continue
            ts = re.match(r"\[(\d+(?::\d{2}){1,2})\]\s*(.*)", line)
            if ts and part:
                at = _secs(ts.group(1)) or 0.0
                out.append(({"kind": "lecture", "ref": x["ref"], "day_key": x["day_key"], "date": x["date"],
                             "week": x["week"], "part": part, "t": at, "ts": format_ts(at)},
                            _content_words(ts.group(2))))
    return out


# ------------------------------------------------------------------ checking the questions
_UNIT_POWER = re.compile(r"(?<![\w$\\])([A-Za-z]{1,4})\$([\^_](?:\{[^$}\n]{1,12}\}|[^$\s]{1,6}))\$")


def _clean_tex(s: str) -> str:
    """Formulas as $…$ (\\(…\\) too), and a unit written "s$^2$" (a dollar right after a letter, which the page
    can't draw) as $\\mathrm{s}^2$."""
    from .outputs.mathmd import dollar_math

    return _UNIT_POWER.sub(r"$\\mathrm{\1}\2$", dollar_math(s or "")).strip()


def _safe_to_shuffle(options: list[str]) -> bool:
    joined = " ".join(options).lower()
    return not re.search(r"\b(?:all|none|both) of (?:the|these)\b|\b[a-d] (?:and|or|ve|veya) [a-d]\b|"
                         r"\bhepsi\b|\bhiçbiri\b|\byukarıdaki\w*", joined)


_MATH = re.compile(r"\$\$.+?\$\$|\$[^$\n]+\$|\\\(.+?\\\)", re.S)
_NAMES_OPTION = re.compile(
    r"(?i:\b(?:answer|option|choice|cevap|yanıt|seçenek|şık)\w*(?:\s+(?:is|was))?\W{0,3})\(?[A-D]\b"
    r"|\b[A-D]\)?(?=\s*['’]|\s+(?:is|was|would|are|şıkk\w*|seçeneğ\w*)\b)"
    r"|\([A-D]\)|(?<![\w$\\])[B-D](?![\w$])"
    r"|(?i:\b(?:first|second|third|fourth|last|ilk|birinci|ikinci|üçüncü|dördüncü|son)\s+(?:option|choice|seçenek|şık)\w*)")


def names_option(why: str, wrong: list[str]) -> bool:
    """Do the explanation or the reasons call an option by its letter or place ("B is right", "the answer is C",
    "B şıkkı", "the first option", a reason starting with its own letter)? Such a question keeps its order (a false
    alarm only costs it its shuffle). Maths ($P(A)$) and units ("3 A") name no option."""
    if any(_NAMES_OPTION.search(_MATH.sub(" ", x or "")) for x in [why, *wrong]):
        return True
    return any(w and re.match(rf"\s*\(?{_LETTER[i]}\b", w) for i, w in enumerate(wrong[:4]))


def shuffle_right(options: list[str], right: int, wrong: list[str], seed: str, slot: int) -> tuple[list[str], int, list[str]]:
    """The options reordered (the same way each time: by ``seed``) so the right one lands on ``slot``: the AIs put it
    on B or C far more often than chance. Options naming others ("both A and B", "all of the above") stay put."""
    if not _safe_to_shuffle(options) or right == slot:
        return options, right, wrong
    order = [i for i in range(len(options)) if i != right]
    k = int(hashlib.sha1(seed.encode("utf-8")).hexdigest()[:6], 16)
    order = order[k % len(order):] + order[:k % len(order)]
    order.insert(slot, right)
    return [options[i] for i in order], slot, [wrong[i] for i in order]


def clean_question(q: dict[str, Any], zero: Optional[bool]) -> Optional[dict[str, Any]]:
    """One question the app can use, or None: a text; multiple choice with exactly four options (a fifth wrong one is
    dropped) and one right answer; a short answer with its model answer; a problem with its steps or result."""
    text = _clean_tex(_text(_get(q, "question"), 3000))
    text = re.sub(r"^\s*(?:\*\*)?(?:question|soru|q)\s*\d*\s*[:.)]\s*(?:\*\*)?\s*", "", text, flags=re.I)
    if len(text) < 8:
        return None
    texts, marked, written = _options(_get(q, "options"))
    steps = _steps(_get(q, "steps")) if not texts else []
    kind = _kind(_get(q, "type"), bool(texts), bool(steps))
    out: dict[str, Any] = {"type": kind, "q": text, "topic": _text(_get(q, "topic"), 160),
                           "los": sorted({n for n in _ints(_get(q, "los") or []) if 0 < n < 40}),
                           "stressed": list(dict.fromkeys(_refs(_get(q, "stressed") or []))),
                           "level": _text(_get(q, "level"), 20).lower(), "raw_source": _get(q, "source"),
                           "why": _clean_tex(_text(_get(q, "why"), 3000))}
    if kind == "mc":
        if len(texts) < 4 or any(not x for x in texts[:4]):
            return None
        right = marked if marked is not None else _answer_index(_get(q, "index"), texts, True) \
            if _get(q, "index") is not None else _answer_index(_get(q, "answer"), texts, zero)
        if right is None:
            return None
        wrong = _wrong(_get(q, "wrong"), len(texts), right, written)
        if len(texts) > 4:                              # a fifth option: the last wrong ones go
            keep = [right] + [i for i in range(len(texts)) if i != right][:3]
            keep.sort()
            right = keep.index(right)
            texts, wrong = [texts[i] for i in keep], [wrong[i] for i in keep]
        if len({_same(x) for x in texts}) < 4:
            return None
        out.update(options=[_clean_tex(x) for x in texts], answer=right, wrong=[_clean_tex(x) for x in wrong])
        return out
    model = _get(q, "model") or (_get(q, "answer") if not isinstance(_get(q, "answer"), (dict, list)) else "")
    out["model"] = _clean_tex(_text(model, 3000))
    if kind == "problem":
        out["steps"] = [_clean_tex(x) for x in (steps or _steps(_get(q, "steps")))]
        if not out["steps"] and not out["model"]:
            return None
        if not out["model"] and out["steps"]:
            out["model"] = out["steps"][-1]
        return out
    out["points"] = [_clean_tex(x) for x in _list(_get(q, "points"))]
    if not out["model"] and not out["points"]:
        return None
    return out


def clean_quiz(raw: dict[str, Any], facts: dict[str, Any], units: Optional[list] = None,
               seed: str = "") -> dict[str, Any]:
    """The AI's quiz, checked: usable questions only, each once; the right answers spread over A–D (not where its
    reasons name an option's letter); each with a source the app can open (the one named, else found by its words);
    los and stressed moments only as known."""
    qs = raw.get("questions") or []
    ints = [v for q in qs if isinstance(q, dict) for v in [_get(q, "answer")]
            if isinstance(v, int) and not isinstance(v, bool)]
    zero = True if 0 in ints else False if ints and max(ints) >= 4 else None
    known_los = set(range(1, len(facts.get("outcomes") or []) + 1)) or None
    stressed = facts.get("stressed") or {}
    out, seen = [], set()
    for q in qs:
        if not isinstance(q, dict):
            continue
        c = clean_question(q, zero)
        if c is None or _same(c["q"]) in seen:
            continue
        seen.add(_same(c["q"]))
        src = check_source(parse_source(c.pop("raw_source")), facts)
        c["found"] = False
        if src is None and units:
            src = locate(c, units)
            c["found"] = src is not None
        c["source"] = src
        c["los"] = [n for n in c["los"] if known_los is None or n in known_los]
        c["stressed"] = [stressed[r]["id"] for r in c["stressed"] if r in stressed]
        out.append(c)
    mcs = [c for c in out if c["type"] == "mc"]
    if seed:                                            # the right answer's letter as if drawn, the same each time
        slots = [int(hashlib.sha1(f"{seed}|{i}|slot".encode()).hexdigest()[:8], 16) % 4 for i in range(len(mcs))]
        for i, c in enumerate(mcs):
            if names_option(c["why"], c["wrong"]):     # "B is right": moved, it would contradict what the page says
                continue
            c["options"], c["answer"], c["wrong"] = shuffle_right(c["options"], c["answer"], c["wrong"],
                                                                  f"{seed}|{i}", slots[i])
    for n, c in enumerate(out, 1):
        c["n"] = n
    return {"title": _text(raw.get("title"), 140), "topics": [x for x in raw.get("topics") or [] if x][:60],
            "questions": out}


def coverage(quiz: dict[str, Any], facts: dict[str, Any]) -> dict[str, Any]:
    """What the quiz covers and what it left out, worked out from its questions and the material (not the AI's
    word): its weeks, files and lectures, how many questions of each kind, the outcomes of the assessment it
    prepares for (with how many questions test each), the stressed moments it tests, and what it could not use
    (files not on this PC, lectures not transcribed or cut for length, topics no question tests)."""
    qs = quiz["questions"]
    kinds = {k: sum(1 for q in qs if q["type"] == k) for k in ("mc", "short", "problem")}
    files = sorted({q["source"]["name"] for q in qs if q.get("source") and q["source"]["kind"] == "file"})
    lectures = sorted({q["source"]["date"] for q in qs if q.get("source") and q["source"]["kind"] == "lecture"})
    focus = facts.get("focus") or {}
    los = [{"no": x["no"], "text": x["text"], "n": sum(1 for q in qs if x["no"] in q["los"])} for x in focus.get("los") or []]
    hit = {i for q in qs for i in q["stressed"]}
    for q in qs:                                       # a question from right where a moment was stressed tests it
        s = q.get("source")
        if s and s["kind"] == "lecture":
            hit |= {m["id"] for m in (facts.get("stressed") or {}).values()
                    if m["day_key"] == s["day_key"] and m["part"] == s["part"] and abs(m["t"] - s["t"]) <= NEAR_S}
    stressed_ids = {m["id"] for m in (facts.get("stressed") or {}).values() if m.get("kind") != "not_needed"}
    def stems(text: str) -> set[str]:                  # "standardising" and "standard deviation" share "standa"
        return {w[:6] for w in _content_words(text)}

    tested = [stems(" ".join([q["topic"], q["q"], q.get("why", ""), q.get("model", ""), *(q.get("wrong") or []),
                              *(q.get("points") or []), *(q.get("steps") or []), *(q.get("options") or [])])) for q in qs]
    named = {_same(q["topic"]) for q in qs if q.get("topic")}        # the topic a question names (as listed)
    topics_missing = []
    for tp in quiz.get("topics") or []:
        w = stems(tp)
        if _same(tp) in named:
            continue
        if w and not any(len(w & x) >= max(1, math.ceil(len(w) * 0.6)) for x in tested):
            topics_missing.append(tp)
    lecture_refs = {x["ref"]: x for x in facts["lectures"]}
    return {"weeks": facts.get("weeks") or [], "span": facts.get("span", ""), "kinds": kinds, "total": len(qs),
            "files": files, "lectures": lectures, "files_all": [f["name"] for f in facts["files"]],
            "lectures_all": [x["date"] for x in facts["lectures"]],
            "focus": {"name": focus.get("name", ""), "week": focus.get("week")} if focus else None, "los": los,
            "stressed_total": len(stressed_ids), "stressed_hit": len(hit & stressed_ids),
            "topics_missing": topics_missing[:12], "found": sum(1 for q in qs if q.get("found")),
            "no_source": sum(1 for q in qs if not q.get("source")),
            "materials_missing": [m["title"] for m in facts.get("materials_missing") or []],
            "days_missing": [x["date"] for x in facts.get("days_missing") or []],
            "files_cut": [f["name"] for f in facts.get("files_cut") or []],
            "lectures_cut": sorted({lecture_refs[r]["date"] for r in (facts.get("cut") or []) + (facts.get("left_out") or [])
                                    if r in lecture_refs})}


# ------------------------------------------------------------------ the stages (run by plans.PlanRunner)
def run(runner: Any, plan: dict[str, Any], scope: Any, data: dict[str, Any], usage: dict[str, Any], model: str,
        cancel: threading.Event) -> None:
    """Write (or resume) the quiz ``plan``; each finished stage is saved at once."""
    from .ai import one_shot
    from .plans import model_shown
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
        s.lib.update_plan(pid, data=data, usage=usage)
    facts = data["gather"]
    if not facts["files"] and not facts["lectures"]:
        raise RuntimeError(t("None of the picked files is on this PC and none of the lectures is transcribed yet."))
    check()
    n = scope.questions if scope.questions in COUNTS else DEFAULT_COUNT
    kinds = mix(n, scope.qtypes if scope.qtypes in TYPES else "mixed", facts.get("quant", False))
    if "write" not in data:
        stage("write", 0.1, tn(n, "Writing {n} question ({model})…", "Writing {n} questions ({model})…", model=shown))
        content: list[dict[str, Any]] = [{"type": "text", "text": write_prompt(scope, facts, n, kinds)}]
        if facts.get("one_lecture") and hasattr(s, "ai_inputs"):
            content += _slide_blocks(s, scope.days[0])
        ask = runner._ask_text or one_shot
        res = ask(SYSTEM, content, model, level, cancel, None)
        _usage_add(usage, "write", res)
        raw = read_answer(res.get("text") or "")
        if not answered(raw):                          # not a quiz it could read: asked once more
            log.info("The quiz answer could not be read; asking again")
            check()
            again = ask(SYSTEM, content + [{"type": "text", "text": "Reply with the JSON object only."}], model, level,
                        cancel, None)
            _usage_add(usage, "write", again)
            second = read_answer(again.get("text") or "")
            raw = second if second["questions"] else raw
            if not raw["questions"]:
                from .ai import ai_name

                raise RuntimeError(t("{ai}'s answer could not be read (it was not valid JSON). Try again.", ai=ai_name()))
        data["write"] = raw
        s.lib.update_plan(pid, data=data, usage=usage, title=raw.get("title") or "")
    check()
    stage("check", 0.9)
    raw = data["write"]
    units = source_units(s.lib, plan["course_id"], facts)
    quiz = clean_quiz(raw, facts, units, seed=pid)
    if not quiz["questions"]:
        raise RuntimeError(t("The AI's questions could not be used (no question had a clear right answer). Try again."))
    title = quiz["title"] or facts["span"].capitalize()
    result = {"kind": "quiz", "title": title, "made": dt.date.today().isoformat(), "language": facts["language"],
              "questions": quiz["questions"], "topics": quiz["topics"], "coverage": coverage(quiz, facts),
              "course": facts["course"], "asked": n}
    data["check"] = True
    s.lib.update_plan(pid, data=data, usage=usage, result=result, title=title, status="done", stage="done",
                      progress=1.0, message=tn(len(quiz["questions"]), "{n} question", "{n} questions"))


def _slide_blocks(s: Any, day_key: str) -> list[dict[str, Any]]:
    """A one-lecture quiz: the slides captured from its video, labelled as the transcript marks them, at most
    LECTURE_SLIDES spread over the lecture."""
    from .ai import image_block

    try:
        _, images, _, _ = s.ai_inputs(day_key, True)
    except Exception:  # noqa: BLE001 - a quiz without the pictures is still a quiz
        log.debug("no slides for the quiz", exc_info=True)
        return []
    if len(images) > LECTURE_SLIDES:
        step = len(images) / LECTURE_SLIDES
        images = [images[int(i * step)] for i in range(LECTURE_SLIDES)]
    out: list[dict[str, Any]] = []
    for label, path in images:
        try:
            block = image_block(Path(path))
        except Exception:  # noqa: BLE001 - one picture that can't be read is left out
            continue
        out += [{"type": "text", "text": label}, block]
    return out


def estimate(lib: Any, settings: Any, course_id: str, scope: Any) -> dict[str, Any]:
    """Roughly what a quiz costs: tokens in and out, and the time."""
    from .ai import PROMPT_OVERHEAD, chars_per_token
    from .plans import main_model
    from .studypack import _level

    facts = gather(lib, settings, course_id, scope)
    n = scope.questions if scope.questions in COUNTS else DEFAULT_COUNT
    per = chars_per_token()
    slides = 0
    if facts.get("one_lecture"):
        slides = min(LECTURE_SLIDES, sum(p["slides"] for x in facts["lectures"] for p in x["parts"]))
    tokens_in = facts["chars"] / per + PROMPT_OVERHEAD + 2_500 + slides * 800
    tokens_out = 600 + n * 420
    a = 1 + round(n / 6)
    return {"main_model": main_model(scope, settings), "effort": _level(scope, settings),
            "main_input": int(tokens_in), "main_output": int(tokens_out), "requests": 1, "files": len(facts["files"]),
            "lectures": len(facts["lectures"]), "materials_missing": facts["materials_missing"],
            "days_missing": facts["days_missing"], "files_cut": facts["files_cut"], "language": facts["language"],
            "taught_in": facts.get("taught_in", ""), "quant": facts["quant"], "slides": slides,
            "minutes": t("about {a}–{b} minutes", a=a, b=a * 2 + 1)}


# ------------------------------------------------------------------ the printed quiz (the study pack's look)
def _src_text(src: Optional[dict[str, Any]]) -> str:
    if not src:
        return ""
    if src["kind"] == "file":
        return f"{src['name']}, page {src['page']}"
    return f"Lecture {src['date']}, part {src['part']}, {src['ts']}"


def _line(md: str) -> str:
    """One paragraph of Markdown as inline HTML (beside a letter or a label), formulas kept for KaTeX."""
    from .studypack import md_html

    out = md_html(md or "")
    return out[3:-4] if out.startswith("<p>") and out.endswith("</p>") and out.count("<p>") == 1 else out


def document_body(plan: dict[str, Any]) -> str:
    """The quiz as a document: a cover with what it covers, the questions (room to answer), then the answers with
    every explanation, on pages of their own."""
    from .studypack import md_html

    r = plan.get("result") or {}
    cov = r.get("coverage") or {}
    esc = lambda s: html.escape(str(s or ""), quote=False)       # noqa: E731
    course = (r.get("course") or {}).get("code", "")
    made = r.get("made") or (plan.get("created_at") or "")[:10]
    try:
        made = f"{dt.date.fromisoformat(made):%d %b %Y}"
    except ValueError:
        pass
    covers = [f"{len(r.get('questions') or [])} questions on {esc(cov.get('span') or '')}"]
    if cov.get("focus") and cov["focus"].get("name"):
        covers.append(f"for {esc(cov['focus']['name'])}")
    left = []
    if cov.get("materials_missing"):
        left.append("files not on the PC: " + esc(", ".join(cov["materials_missing"])))
    if cov.get("days_missing"):
        left.append("lectures not transcribed: " + esc(", ".join(cov["days_missing"])))
    if cov.get("topics_missing"):
        left.append("no question on: " + esc(", ".join(cov["topics_missing"])))
    out = [f'<header class="sp-cover"><div class="sp-kicker">{esc(course)} · Practice quiz</div>'
           f'<h1>{esc(r.get("title") or plan.get("title") or "Practice quiz")}</h1>'
           f'<div class="sp-sub">{" ".join(covers)} · made with IEUStudia on {esc(made)}</div>'
           + (f'<div class="sp-sources">Left out: {"; ".join(left)}</div>' if left else "")
           + '<div class="sp-sources">Written by AI from your course material: check anything that looks wrong '
             'against your slides and lectures. The answers are at the end.</div></header>']
    qs = r.get("questions") or []
    body = []
    for q in qs:
        part = [f'<div class="qz-q"><div class="qz-n">{q["n"]}</div><div class="qz-body">{md_html(q["q"])}']
        if q["type"] == "mc":
            part.append('<ol class="qz-opts" type="A">' + "".join(f"<li>{_line(x)}</li>" for x in q["options"]) + "</ol>")
        else:
            part.append(f'<div class="qz-space {"big" if q["type"] == "problem" else ""}"></div>')
        src = _src_text(q.get("source"))
        if src:
            part.append(f'<div class="sp-src">From {esc(src)}</div>')
        body.append("".join(part) + "</div></div>")
    out.append(f'<section class="sp-ch qz-part"><h2>Questions</h2>{"".join(body)}</section>')
    keys = []
    for q in qs:
        bits = [f'<div class="qz-q"><div class="qz-n">{q["n"]}</div><div class="qz-body">']
        if q["type"] == "mc":
            bits.append(f'<p><b>{_LETTER[q["answer"]]}.</b> {_line(q["options"][q["answer"]])}</p>')
            if q.get("why"):
                bits.append(md_html(q["why"]))
            wrong = [(i, w) for i, w in enumerate(q.get("wrong") or []) if w and i != q["answer"]]
            if wrong:
                bits.append('<ul class="qz-wrong">' + "".join(f"<li><b>{_LETTER[i]}</b>: {_line(w)}</li>" for i, w in wrong)
                            + "</ul>")
        elif q["type"] == "problem":
            bits.append('<ol class="qz-steps">' + "".join(f"<li>{_line(x)}</li>" for x in q.get("steps") or []) + "</ol>")
            if q.get("model"):
                bits.append(f'<p><b>Answer:</b> {_line(q["model"])}</p>')
        else:
            bits.append(md_html(q.get("model") or ""))
            if q.get("points"):
                bits.append('<ul class="qz-points">' + "".join(f"<li>{_line(x)}</li>" for x in q["points"]) + "</ul>")
            if q.get("why"):
                bits.append(md_html(q["why"]))
        keys.append("".join(bits) + "</div></div>")
    out.append(f'<section class="sp-ch qz-part"><h2>Answers</h2>{"".join(keys)}</section>')
    return "".join(out)


QUIZ_CSS = """
.qz-q { display: flex; gap: 12px; margin: 0 0 16px; break-inside: avoid; }
.qz-n { flex: none; width: 26px; height: 26px; border-radius: 13px; background: #eef2ff; color: #1d4ed8;
  font-weight: 700; display: flex; align-items: center; justify-content: center; font-size: 13px; }
.qz-body { flex: 1; min-width: 0; }
.qz-body > p:first-child { margin-top: 3px; }
.qz-opts { margin: 6px 0 4px; padding-left: 24px; }
.qz-opts li { margin: 3px 0; }
.qz-space { height: 70px; border-bottom: 1px dashed #cbd5e1; margin: 6px 0; }
.qz-space.big { height: 190px; }
.qz-wrong, .qz-points, .qz-steps { margin: 6px 0; padding-left: 22px; }
.qz-part + .qz-part { break-before: page; }
.qz-opts p, .qz-wrong p, .qz-points p, .qz-steps p { margin: 0; }
@media print {                     /* the questions right under the cover; the answers on pages of their own */
  .sp-cover { padding-top: 0; }
  .sp-cover + .qz-part { break-before: auto; margin-top: 1.2em; }
}
"""


def files_for(s: Any, plan: dict[str, Any]) -> tuple[Path, Path]:
    """Where a quiz's files go: <course>/Practice quizzes/<date> - <title>.html / .pdf."""
    from .outputs.writer import course_dir
    from .studypack import file_stem

    course = s.lib.get_course(plan["course_id"])
    folder = course_dir(s.output_root, course, s.lib.courses(True)) / "Practice quizzes"
    title = (plan.get("result") or {}).get("title") or plan.get("title") or "Practice quiz"
    data = plan.setdefault("data", {}) if isinstance(plan.get("data"), dict) else {}
    stem = file_stem(plan, title, folder, (".html", ".pdf"), data)
    return folder / f"{stem}.html", folder / f"{stem}.pdf"


def make_pdf(s: Any, plan: dict[str, Any]) -> Optional[Path]:
    """The quiz's PDF in the app's document look (written again each time: it is small)."""
    from .studypack import _print_to, _write, document_html

    if plan.get("status") != "done":
        return None
    html_path, pdf_path = files_for(s, plan)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    page = document_html(document_body(plan), (plan.get("result") or {}).get("title") or "Practice quiz",
                         html_path.parent).replace("</style>", QUIZ_CSS + "</style>", 1)
    _write(html_path, page)
    s.lib.update_plan(plan["id"], data=plan.get("data") or {},
                      result={**(plan.get("result") or {}), "file": str(html_path)})
    return pdf_path if _print_to(html_path, pdf_path) else None
