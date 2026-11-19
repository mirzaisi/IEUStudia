"""The exam kit: Export for AI chat (course_export.py) aimed at a coming quiz or exam, for the students who study with
ChatGPT or Claude on the web, and what those write from it pasted back into IEUStudia.

In a course's zip every course file and lecture has a short reference in front of its name ("F3 - Slides.pdf", the
lecture folder "L2 2026-10-14 Wed - Lecture"), the same in every zip of that course (``numbers``: a file or lecture
never numbered gets the next free number, kept in meta "kit-refs:<course>"), so a quiz pasted back from any of its
chats opens the right page or moment. Beside them, Prompts/ holds three prompts to paste into that chat: a practice
quiz (quizzes.write_prompt's JSON, rules and languages, as PRs #7 and #8 set them), flashcards (PR #10: the front in
the course's language, the back in the document's) and a cheatsheet. With an assessment picked ("For": one of
quizzes.assessments, a syllabus final too), "00 Start here.md" says what it covers and where that comes from, when it
is, what it weighs, its learning outcomes, what the lecturer said about it and stressed in those weeks, and what isn't
needed. Each zip's kit (its weeks, assessment, references and the stressed moments its prompts number S1…) is kept in
meta "export-refs:<id>", a course's newest ones in "export-kits:<course>".

Pasted back on the Practice tab (``pasted``), a quiz or flashcards are read in the forms web chats write them (JSON in
one code block or several, after a sentence, with Turkish keys or a trailing comma; Markdown; a table, tab-separated
lines or "Q: … A: …" for cards) and checked as the app's own (quizzes.clean_quiz, cards.clean_cards: no AI). A source
the chat named opens through the course's references; one it named wrongly or not at all is found by its words and
labelled so. They are saved as a finished quiz or card set (scope source "import"), which play, score and feed the
mistakes loop like the app's own. No AI request is made here: the student's own web chat does the writing."""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import logging
import re
import uuid
from typing import Any, Iterable, Optional

from .i18n import t, tn

log = logging.getLogger("studia")

REFS = "kit-refs:{}"            # a course's references: {"files": {resource id: n}, "lectures": {day key: n}}
KIT = "export-refs:{}"          # one zip's kit
KITS = "export-kits:{}"         # a course's kits, newest first
KITS_KEPT = 20
QUIZ_ASKED = 10                 # questions the quiz prompt asks for (the app's own default)
CARDS_ASKED = 30
CARDS_MOST = 120                # cards kept from one paste (cards.MAX_CARDS)
STRESSED_MOST = 25
SAID_MOST = 8
PASTE_MOST = 400_000            # characters read from one paste
NEAR_S = 180                    # a card from this close to a stressed moment is a stressed card (cards.NEAR_S)
PROMPTS = (("quiz", "Practice quiz.md"), ("cards", "Flashcards.md"), ("sheet", "Cheatsheet.md"))


class Unread(ValueError):
    """What was pasted holds no quiz or cards the app can use: why, in words for the student."""


def _meta(lib: Any, key: str, default: Any) -> Any:
    try:
        v = json.loads(lib.get_meta(key) or "null")
    except (TypeError, ValueError):
        return default
    return v if isinstance(v, type(default)) else default


# ------------------------------------------------------------------ references (F1…, L1…), the same in every zip
def numbers(lib: Any, course_id: str, files: Iterable[str] = (), days: Iterable[str] = ()) -> dict[str, str]:
    """The course's references: {resource id: "F3", day key: "L2"}. The files and lectures given that have none yet
    get the next free numbers, in the order given (the course's), and are kept."""
    raw = _meta(lib, REFS.format(course_id), {})
    have = {k: {str(i): n for i, n in (raw.get(k) or {}).items() if isinstance(n, int)} for k in ("files", "lectures")}
    added = False
    for k, ids in (("files", files), ("lectures", days)):
        last = max(have[k].values(), default=0)
        for i in ids:
            if i and i not in have[k]:
                last += 1
                have[k][i] = last
                added = True
    if added:
        lib.set_meta(REFS.format(course_id), json.dumps(have))
    return {**{i: f"F{n}" for i, n in have["files"].items()}, **{i: f"L{n}" for i, n in have["lectures"].items()}}


def course_order(lib: Any, plan: Any) -> tuple[list[str], list[str]]:
    """An export plan's course files (week by week, then the course-wide ones; links aside) and every class day of
    the course (by date), in the order they are numbered."""
    files = [m.id for w in plan.all_weeks for m in w.files if not m.link]
    files += [m.id for _, ms in plan.general for m in ms if not m.link]
    days = sorted({r.day_key for r in lib.recordings(plan.course.id) if r.day_key},
                  key=lambda k: ((k.split("|") + ["", ""])[1], k))
    return list(dict.fromkeys(files)), days


def _ref_key(ref: str) -> tuple[str, int]:
    return ref[:1], int(ref[1:]) if ref[1:].isdigit() else 0


def ref_lines(refs: dict[str, dict[str, Any]]) -> list[str]:
    """The zip's references for the AI: "F3 = the file Slides.pdf (24 pages), week 8: <its path in the zip>"."""
    from .course_export import _date, _en_day

    out = []
    for ref in sorted(refs, key=_ref_key):
        x = refs[ref]
        if x.get("kind") == "file":
            pages = int(x.get("pages") or 0)
            out.append(f"{ref} = the file {x.get('name') or ''}" + (f" ({pages} page{'s' if pages > 1 else ''})" if pages else "")
                       + (f", week {x['week']}" if x.get("week") else ", for the whole course") + f": {x.get('path') or ''}")
        else:
            d = _date(x.get("date") or "")
            out.append(f"{ref} = the lecture of {_en_day(d) if d else x.get('date') or ''}"
                       + (f" (week {x['week']})" if x.get("week") else "") + f": {x.get('path') or ''}/Transcript.md")
    return out


# ------------------------------------------------------------------ the assessment and what was said about it
def assessment(d: dict[str, Any], key: str) -> Optional[dict[str, Any]]:
    """The course's assessment of this key (quizzes.assessment_key), or None."""
    from .quizzes import of

    return next((e for e in of(d) if e.get("key") == key), None) if key else None


def _own_day(e: dict[str, Any]) -> str:
    """The exact day the student set for it in the Exam room (examroom.when, as quizzes.assessments gives it), "" for
    none."""
    on = e.get("on") or {}
    return on.get("date") or "" if on.get("from") == "yours" else ""


def choices(d: dict[str, Any]) -> list[dict[str, Any]]:
    """What the export dialog's "For" offers: the assessments still ahead (syllabus finals too), each with the weeks
    it covers, where they come from and its day as the Exam room says it (examroom.when: the announced one, else the
    one the student set, else its week's class day or first day)."""
    from .quizzes import of

    weeks = {w["no"]: w for w in d.get("weeks") or []}
    return [{"key": e["key"], "name": e["name"], "kind": e["kind"], "week": e["week"], "weeks": list(e.get("weeks") or []),
             "weeks_from": e.get("weeks_from") or "",
             "date": e.get("date") or (e.get("on") or {}).get("date") or (weeks.get(e["week"]) or {}).get("start") or ""}
            for e in of(d) if e.get("ahead")]


def _weight(d: dict[str, Any], e: dict[str, Any]) -> str:
    """What the assessment weighs, from the syllabus' grading ("Quizzes count 20% of the grade in all (4 of them,
    about 5% each)"); "" when the syllabus doesn't say. The Exam room's matching (examroom.grade_share): a final
    project or jury is not the final."""
    from .examroom import grade_share

    g = grade_share(d.get("grading") or [], e)
    if not g:
        return ""
    name, w, n = g["activity"].strip(), g["weight"], g["number"]
    return f"{name}: {w:g}% of the grade" + (f" in all ({n} of them, about {w / n:.3g}% each)" if n > 1 else "")


def _said(d: dict[str, Any], e: dict[str, Any], lrefs: dict[str, str]) -> list[str]:
    """What the lecturer said about this assessment, in class and in the announcements, newest first: the Exam
    room's own list (examroom.said), as the brief's lines."""
    from .course_export import _date, _en_day
    from .examroom import said

    out = []
    for it in said(d, e, SAID_MOST):
        day = _date(it.get("date") or "")
        text = " ".join(str(it.get("text") or "").split())
        if it.get("source") == "lecture":
            ref = lrefs.get(it.get("day_key") or "")
            where = (f"in class [{ref} part {it.get('part') or 1} {it.get('ts') or ''}]".replace(" ]", "]") if ref
                     else f"in class (part {it.get('part') or 1}, {it.get('ts') or ''})")
            if it.get("other_section_en"):            # (as the zip's heads-up says it)
                where += f", heard in {it['other_section_en']}'s lecture (the dates may differ for my section)"
        else:
            where = f"announcement “{it.get('title') or ''}”"
        out.append(f"- {_en_day(day) + ', ' if day else ''}{where}: “{text}”")
    return out


def _stressed(lib: Any, course_id: str, d: dict[str, Any], weeks: list[int],
              lrefs: dict[str, str]) -> tuple[str, dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """What the lecturer stressed in these weeks, as the prompts' lines (S1… with the zip's lecture references), what
    each S number is, and what they said isn't needed."""
    from . import stressed

    try:
        items = stressed.course_items(lib, course_id)
    except Exception:  # noqa: BLE001 - a kit without them is still a kit
        log.debug("no stressed moments for the kit", exc_info=True)
        return "", {}, []
    day_week = {day["date"]: w["no"] for w in d.get("weeks") or [] for day in w.get("days") or []}
    nos = set(weeks)
    top = stressed.ranked(items, nos or None, day_week)
    not_needed = [it for it in items if it["kind"] == "not_needed" and (not nos or day_week.get(it["day"]) in nos)]
    lines, by_ref = stressed.prompt_lines(top, lrefs, STRESSED_MOST, not_needed)
    keep = {r: {k: it.get(k) for k in ("id", "day_key", "part", "t", "ts", "kind", "text")} for r, it in by_ref.items()}
    return lines, keep, not_needed[:8]


def start_md(d: dict[str, Any], course: Any, e: dict[str, Any], kit: dict[str, Any], today: dt.date,
             not_needed: list[dict[str, Any]]) -> str:
    """"00 Start here.md": the exam brief the AI reads first (English, like the zip's other files)."""
    from .course_export import _date, _en_day, _en_range, course_title
    from .plans import _week_span

    weeks = {w["no"]: w for w in d.get("weeks") or []}
    w = weeks.get(e.get("week") or 0) or {}
    name = e["name"]
    lrefs = {x["day_key"]: r for r, x in kit["refs"].items() if x.get("kind") == "lecture"}
    lines = [f"# Start here: {name} · {course_title(course, d)}", "",
             f"I made this zip with IEUStudia to prepare for {name}. Read this file first: what it covers, what the "
             "lecturer said about it and stressed in those weeks, and the references (F1…, L1…) of the course files "
             "and lectures in this zip.", "", f"## {name}", ""]
    day = _date(e.get("date") or "")
    mine = None if day else _date(_own_day(e))
    if mine:                                        # (the exact day set in the Exam room)
        day = mine
        when = _en_day(day) + (f" (week {e['week']}, the day I set)" if e.get("week") else " (the day I set)")
    elif day:
        when = _en_day(day) + (f", {e['time']}" if e.get("time") else "") + (f" (week {e['week']})" if e.get("week") else "")
    else:
        span = _en_range(w.get("start") or "", w.get("end") or "")
        when = f"week {e['week']}" + (f" ({span})" if span else "") if e.get("week") else "not announced yet"
    first = day or _date(w.get("start") or "")
    if first:
        left = (first - today).days
        when += (" (over)" if left < 0 else ", today" if left == 0 else ", tomorrow" if left == 1 else f", in {left} days")
    lines.append(f"- When: {when}.")
    covers = _week_span(sorted(e.get("weeks") or []))
    origin = {"announced": "as the lecturer announced it",
              "yours": "as I set it",
              "guessed": ("the whole term" if e.get("kind") == "final" else
                          "IEUStudia's guess: the weeks since the one before it")}.get(e.get("weeks_from") or "", "")
    lines.append(f"- It covers: {covers}" + (f" ({origin})" if origin else "") + ".")
    weight = _weight(d, e)
    if weight:
        lines.append(f"- Weight: {weight}.")
    outs = d.get("outcomes_en") or d.get("outcomes_tr") or []
    los = [n for n in e.get("los") or [] if 1 <= n <= len(outs)]
    if los:
        lines.append("- The learning outcomes it covers:")
        lines += [f"  - LO{n}: {outs[n - 1]}" for n in los]
    said = _said(d, e, lrefs)
    if said:
        lines += ["", "## What the lecturer said about it", "", "Newest first, from the lectures (with the moment) and "
                  "the announcements.", "", *said]
    moments = [ln for ln in (kit.get("stressed_lines") or "").split("\n") if re.match(r"S\d+ ", ln)]
    if moments:
        lines += ["", "## What the lecturer stressed in these weeks", "",
                  "In the order they were said. The prompts in Prompts/ call them by these S numbers.", "",
                  *[f"- {ln}" for ln in moments]]
    if not_needed:
        lines += ["", "## Not needed (the lecturer said so)", ""]
        for it in not_needed:
            ref = lrefs.get(it.get("day_key") or "")
            where = f"{ref} part {it.get('part') or 1} {it.get('ts') or ''}".strip() if ref else str(it.get("day") or "")
            lines.append(f"- [{where}] “{' '.join(str(it.get('text') or '').split())}”")
    listed = [f"- {ln}" for ln in ref_lines(kit["refs"])] or ["- (no course files or lectures in this zip)"]
    lines += ["", "## References", "",
              "Each course file's name in this zip starts with its F number, and each lecture's folder with its L number. "
              "Cite a file page as \"F3 p.12\" and a lecture moment as \"L2 part 1 14:05\" (the part, then the [mm:ss] "
              "time written before the passage in its Transcript.md).", "", *listed,
              "", "## Prompts", "",
              "Prompts/Practice quiz.md, Prompts/Flashcards.md and Prompts/Cheatsheet.md are ready to paste into this chat."]
    return "\n".join(lines)


# ------------------------------------------------------------------ the prompts (English, with the language rules)
_HEAD = ("(From IEUStudia, the study app I made this zip with. I will paste your answer back into it: it plays the {what} "
         "and opens the page or lecture moment each {one} comes from.)")


def _aim(f: dict[str, Any]) -> str:
    """" The student prepares for Quiz 3 (week 10), which covers these learning outcomes: …" ("" without one)."""
    focus = f.get("focus") or {}
    if not focus.get("name"):
        return ""
    name = f"{focus['name']} (week {focus['week']})" if focus.get("week") else focus["name"]
    los = "; ".join(f"LO{x['no']}" + (f": {x['text']}" if x.get("text") else "") for x in focus.get("los") or [])
    return f" The student prepares for {name}" + (f", which covers these learning outcomes: {los}." if los else ".")


def _refs_block(f: dict[str, Any], what: str) -> str:
    return (f"=== REFERENCES (for {what}: the F number starts each course file's name in the zip, the L number each "
            f"lecture's folder) ===\n{f.get('refs') or '(none)'}")


def quiz_prompt(kid: str, f: dict[str, Any], n: int = QUIZ_ASKED) -> str:
    """The practice quiz to paste: quizzes.write_prompt's task, priorities, JSON and rules, and its language lines
    (said again at the end), on the zip's material; one ```json block, with the kit's id."""
    from . import quizzes
    from .outputs.prompts import quiz_languages_again
    from .plans import Scope

    kinds = quizzes.mix(n, "mixed", bool(f.get("quant")))
    body = quizzes.write_prompt(Scope(kind="quiz"), {**f, "pack": "", "one_lecture": False, "weak": None}, n, kinds)
    body = body.split("\n\n=== COURSE MATERIAL ===")[0]
    body = body.replace("1. The material below", "1. The course material in the zip I uploaded", 1)
    body = body.replace("Answer with one JSON object:\n{", f'Answer with one JSON object:\n{{"kit": "{kid}", ', 1)
    start = " Read 00 Start here.md in the zip first." if f.get("start") else ""
    refs = _refs_block(f, '"source"')
    again = quiz_languages_again(f.get("language") or "", f.get("taught_in") or "")
    return (f"{_HEAD.format(what='quiz with scores', one='question')}{start}\n\n{body}\n\n{refs}{again}\n\n"
            "=== HOW TO ANSWER ===\n"
            "Only the JSON object, in one ```json code block. Nothing before or after it.")


def cards_prompt(kid: str, f: dict[str, Any], n: int = CARDS_ASKED) -> str:
    """The flashcards to paste: the app's card rules (cards.cards_prompt) and PR #10's languages, on the zip's
    material (its lectures too), as one ```json block with the kit's id."""
    from .outputs.prompts import cards_languages, cards_languages_again

    lang, taught = f.get("language") or "", f.get("taught_in") or ""
    start = " Read 00 Start here.md in the zip first." if f.get("start") else ""
    return f"""{_HEAD.format(what='cards with spaced repetition', one='card')}{start}

TASK: Make {n} flashcards from the course material in the zip I uploaded ({f['span']}): the definitions, formulas, theorems, rules, units, methods and key facts a student must know for the exams, one idea per card.{_aim(f)} {cards_languages(lang, taught)}
Answer with one JSON object:
{{"kit": "{kid}", "cards": [{{"front": "a short question or cue", "back": "the answer: short and exact (a formula, a definition, the steps of a method)", "source": "F1 p.3", "topic": "a few words"}}]}}
Rules:
- The front asks one thing and makes sense on its own (name the concept; never "what is on this slide"). The back answers only that, in a sentence or two or a formula.
- Cover every topic of the material, what the lecturer stressed (below) first. Nothing on what the lecturer said is not needed.
- "source": where the card comes from: a file page as "F1 p.3" or a lecture moment as "L1 part 2 14:05" (the [mm:ss] time written before the passage). Only the references listed below.
- Maths in LaTeX: $...$ in a sentence, $$...$$ on a line of its own.

=== WHAT THE LECTURER STRESSED ===
{f.get('stressed_lines') or '(nothing found in these lectures)'}

{_refs_block(f, '"source"')}{cards_languages_again(lang, taught)}

=== HOW TO ANSWER ===
Only the JSON object, in one ```json code block. Nothing before or after it."""


def sheet_prompt(f: dict[str, Any]) -> str:
    """The cheatsheet to paste (read in the chat: it doesn't come back), in the document's language with the terms
    rule (outputs/prompts.written_in) and the house style."""
    from .outputs.prompts import STYLE, written_in

    focus = (f.get("focus") or {}).get("name") or ""
    start = " Read 00 Start here.md in the zip first." if f.get("start") else ""
    return f"""(From IEUStudia, the study app I made this zip with.){start}

TASK: Make me a cheatsheet{' for ' + focus if focus else ''} from the course material in the zip I uploaded ({f['span']}): a compact reference to revise from and to keep beside me while I solve the course's problems, two pages at most.{_aim(f)}
Rules:
- Group it by topic, in the order the course teaches them.
- Every formula with what its symbols mean and when to use it; every definition, rule and method in a line or two; units, special cases and the common mistakes.
- Mark what the lecturer stressed (the S moments below) with ★. Leave out what the lecturer said is not needed.
- After each entry, where it comes from, in brackets: (F3 p.12) or (L2 part 1 14:05).
- Maths in LaTeX: $...$ in a sentence, $$...$$ on a line of its own. Headings and short bullet points, no long paragraphs.
- {written_in(f.get('language') or '', f.get('taught_in') or '')}
- {STYLE}

=== WHAT THE LECTURER STRESSED ===
{f.get('stressed_lines') or '(nothing found in these lectures)'}

{_refs_block(f, 'the brackets')}"""


def make(lib: Any, settings: Any, plan: Any, choice: Any, today: dt.date, refs: dict[str, dict[str, Any]],
         kid: str) -> dict[str, Any]:
    """One course's kit for its zip: the brief (with an assessment picked), the three prompts, and what is kept of it
    (``refs``: the references written in the zip, each with what it is and its path there)."""
    from . import quizzes
    from .doclang import document_language
    from .plans import Scope, _week_span

    d = plan.detail
    e = assessment(d, getattr(choice, "focus", "") or "")
    weeks = sorted({w.no for w in plan.weeks})
    lrefs = {x["day_key"]: r for r, x in refs.items() if x.get("kind") == "lecture"}
    lines, by_ref, not_needed = _stressed(lib, plan.course.id, d, weeks, lrefs)
    facts = None
    if not str(((d.get("syllabus") or {}).get("language")) or "").strip():
        from . import studypack             # no language in the syllabus: the one its material is in, as the app's
        try:                                # own quizzes read it (quizzes.gather)
            facts = studypack.gather(lib, settings, plan.course.id, Scope(kind="quiz", weeks=weeks))
        except Exception:  # noqa: BLE001 - the syllabus's way then (English)
            log.warning("Exam kit: the language of %s's material could not be read", plan.course.id, exc_info=True)
    lang = document_language(d, settings, facts)
    f = {"span": _week_span(weeks), "language": lang["language"], "taught_in": lang["taught_in"],
         "quant": quizzes.quantitative(d), "lectures": [r for r, x in refs.items() if x.get("kind") == "lecture"],
         "focus": quizzes._focus(d, Scope(kind="quiz", focus=e["name"])) if e else None, "stressed_lines": lines,
         "refs": "\n".join(ref_lines(refs)), "start": bool(e)}
    kit = {"id": kid, "course_id": plan.course.id, "made": today.isoformat(), "weeks": weeks,
           "key": e["key"] if e else "", "focus": e["name"] if e else "", "refs": refs, "stressed": by_ref,
           "stressed_lines": lines, "language": lang["language"]}
    kit["start"] = start_md(d, plan.course, e, kit, today, not_needed) if e else ""
    texts = {"quiz": quiz_prompt(kid, f), "cards": cards_prompt(kid, f), "sheet": sheet_prompt(f)}
    kit["prompts"] = [{"key": k, "file": name, "text": texts[k]} for k, name in PROMPTS]
    return kit


def save(lib: Any, kit: dict[str, Any], name: str = "") -> None:
    """Keep a zip's kit (not its texts) for the quizzes and cards pasted back from its chat."""
    keep = {k: kit[k] for k in ("id", "course_id", "made", "weeks", "key", "focus", "refs", "stressed")}
    keep["name"] = name
    lib.set_meta(KIT.format(kit["id"]), json.dumps(keep, ensure_ascii=False))
    ids = [kit["id"]] + [i for i in _meta(lib, KITS.format(kit["course_id"]), []) if i != kit["id"]]
    for old in ids[KITS_KEPT:]:
        lib.set_meta(KIT.format(old), "")
    lib.set_meta(KITS.format(kit["course_id"]), json.dumps(ids[:KITS_KEPT]))


def load(lib: Any, course_id: str, kit_id: str = "") -> Optional[dict[str, Any]]:
    """The course's kit of this id, else its newest one (None when it has none)."""
    for i in ([kit_id] if kit_id else []) + _meta(lib, KITS.format(course_id), []):
        k = _meta(lib, KIT.format(i), {})
        if k.get("course_id") == course_id:
            return k
    return None


# ------------------------------------------------------------------ reading what was pasted
# (its closing fence ends a line: a question's own code, "```java" inside a JSON string, doesn't close it)
_FENCE = re.compile(r"```[ \t]*([\w+-]*)[^\n]*\n(.*?)(?:(?:\n|(?<=[}\]]))[ \t]*```[ \t\r]*$|\Z)", re.S | re.M)
_KIT_ID = re.compile(r"[\"']?\bkit[\"']?\s*[:=]\s*[\"']?([0-9a-f]{8})\b", re.I)
_TR = str.maketrans("çğıöşüâîûÇĞIİÖŞÜ", "cgiosuaiucgiiosu")
TR_KEYS = {
    "soru": "question", "soru_metni": "question", "secenekler": "options", "siklar": "options", "secenek": "options",
    "cevap": "answer", "dogru_cevap": "answer", "dogru_yanit": "answer", "yanit": "answer", "dogru_secenek": "answer",
    "dogru_sik": "answer", "cevap_anahtari": "answer", "aciklama": "explanation", "gerekce": "explanation",
    "neden": "explanation", "yanlis": "wrong", "yanlislar": "wrong", "yanlis_secenekler": "wrong",
    "neden_yanlis": "wrong", "yanlis_aciklamalari": "wrong", "celdiriciler": "wrong", "puanlar": "points",
    "anahtar_noktalar": "points", "kriterler": "points", "olmasi_gerekenler": "points", "model_cevap": "model_answer",
    "ornek_cevap": "model_answer", "beklenen_cevap": "model_answer", "sonuc": "model_answer", "adimlar": "steps",
    "cozum": "steps", "cozum_adimlari": "steps", "kaynak": "source", "kaynaklar": "source", "referans": "source",
    "konu": "topic", "konular": "topics", "baslik": "title", "tur": "type", "tip": "type", "soru_tipi": "type",
    "soru_turu": "type", "zorluk": "level", "seviye": "level", "ogrenme_ciktilari": "los", "ciktilar": "los",
    "vurgulanan": "stressed", "sorular": "questions", "kartlar": "cards", "on": "front", "on_yuz": "front",
    "arka": "back", "arka_yuz": "back", "terim": "term", "tanim": "definition", "sinav": "quiz", "metin": "text",
    "dogru": "correct", "dogru_mu": "correct"}
TYPE_VALUES = {"coktan_secmeli": "mc", "test": "mc", "kisa_cevap": "short", "kisa_yanit": "short", "acik_uclu": "short",
               "hesaplama": "problem", "cozumlu_problem": "problem", "sayisal": "problem", "problem": "problem"}
FRONTS = {"front", "q", "question", "soru", "term", "terim", "on", "on_yuz", "kavram", "concept", "prompt", "cue"}
BACKS = {"back", "a", "answer", "cevap", "definition", "tanim", "arka", "arka_yuz", "explanation", "aciklama", "yanit"}
SOURCES = {"source", "kaynak", "ref", "reference", "referans"}
TOPICS = {"topic", "konu"}


def _fold(s: Any) -> str:
    return re.sub(r"[\s-]+", "_", str(s or "").strip().translate(_TR).lower())


def _en_keys(v: Any) -> Any:
    """A quiz or cards written with Turkish keys ("soru", "seçenekler", "doğru cevap", "kaynak"…) as the English ones
    quizzes.py and cards.py read; an English key of its own wins."""
    if isinstance(v, list):
        return [_en_keys(x) for x in v]
    if not isinstance(v, dict):
        return v
    out: dict[Any, Any] = {}
    for k, x in v.items():
        name = TR_KEYS.get(_fold(k), k) if isinstance(k, str) else k
        if name != k and (name in v or name in out):
            name = k
        x = _en_keys(x)
        if name == "type" and isinstance(x, str):
            x = TYPE_VALUES.get(_fold(x), x)
        out[name] = x
    return out


def _blocks(text: str) -> list[str]:
    """The code blocks of an answer that may hold JSON (```json, ``` alone; one cut short at the end too)."""
    return [m.group(2) for m in _FENCE.finditer(text or "")
            if m.group(1).lower() in ("", "json", "json5", "jsonc", "js", "javascript", "ieustudia-quiz")]


def _loads(s: str) -> Any:
    """The JSON in a block or an answer (quizzes._json_body: LaTeX backslashes, a sentence around it), a trailing
    comma forgiven; of an answer cut short, the questions or cards that are whole (_salvage)."""
    from .quizzes import _json_body

    data = _json_body(s)
    if data is None:
        fixed = re.sub(r",(\s*[}\]])", r"\1", s or "")
        data = _json_body(fixed) if fixed != s else None
    return data if data is not None else _salvage(s or "")


_LIST_START = re.compile(r"[\"'](questions|sorular|cards|kartlar|flashcards)[\"']\s*:\s*\[")


def _salvage(s: str) -> Any:
    """An answer cut short (the chat stopped in the middle of its JSON): {"questions": […]} or {"cards": […]} of the
    items that are whole, None when none is."""
    from .ai import _tex_backslashes

    s = _tex_backslashes(s) if "\\" in s else s
    m = _LIST_START.search(s)
    if not m:
        return None
    dec, items, i = json.JSONDecoder(), [], m.end()
    while True:
        j = s.find("{", i)
        if j < 0 or s[i:j].strip(" \t\r\n,"):
            break
        try:
            item, i = dec.raw_decode(s, j)
        except ValueError:
            break
        items.append(item)
    return {m.group(1): items} if items else None


def kit_in(text: str) -> str:
    m = _KIT_ID.search(text or "")
    return m.group(1).lower() if m else ""


def read_quiz(text: str) -> dict[str, Any]:
    """A pasted quiz as quizzes.read_answer reads one ({"title", "topics", "questions"} and "kit": the id it echoes),
    also from several code blocks (a chat that wrote a block per question) and with Turkish keys; else its Markdown."""
    from . import quizzes

    title, topics, qs, kid = "", [], [], ""
    for body in _blocks(text) or [text]:
        data = _loads(body)
        if data is None:
            continue
        data = _en_keys(data)
        if isinstance(data, dict) and isinstance(data.get("kit"), str):
            kid = kid or data["kit"].strip().lower()
        quiz, found = quizzes._question_lists(data)
        for hint, q in found:
            q = q if isinstance(q, dict) else {"question": q}
            if hint and not quizzes._get(q, "type"):
                q = {**q, "type": hint}
            qs.append(q)
        if found and not title:
            title = quizzes._text(quiz.get("title"), 200)
            topics = [quizzes._text(x, 200) for x in quiz.get("topics") or []] if isinstance(quiz.get("topics"), list) else []
    if not qs:
        md = quizzes.read_markdown(text)
        title, qs = md["title"], md["questions"]
    return {"title": title, "topics": topics, "questions": qs, "kit": kid or kit_in(text)}


def _card_rows(data: Any) -> tuple[list[dict[str, Any]], bool]:
    """Cards from JSON: {"cards": […]} (or "flashcards", "items"), a bare list; front/back, question/answer, q/a,
    term/definition. Also whether it said it holds cards."""
    rows = data
    said = False
    if isinstance(data, dict):
        for k in ("cards", "flashcards", "flash_cards", "items", "deck"):
            if isinstance(data.get(k), list):
                rows, said = data[k], k != "items"
                break
    out = []
    for c in rows if isinstance(rows, list) else []:
        if not isinstance(c, dict):
            continue
        keys = {_fold(k): k for k in c}
        q = next((c[keys[k]] for k in ("front", "question", "q", "term", "prompt", "cue") if k in keys and c[keys[k]]), "")
        a = next((c[keys[k]] for k in ("back", "answer", "a", "definition", "explanation") if k in keys and c[keys[k]]), "")
        said = said or bool({"front", "back", "term", "definition"} & set(keys))
        if isinstance(a, list):
            a = "\n".join(f"- {x}" for x in a)
        src = next((c[keys[k]] for k in ("source", "ref", "reference", "page") if k in keys), None)
        out.append({"q": str(q).strip(), "a": str(a).strip(), "source": src,
                    "topic": str(c.get(keys.get("topic", "topic")) or "").strip()})
    return out, said


def _columns(cells: list[str]) -> Optional[tuple[int, int, Optional[int], Optional[int]]]:
    """The front, back, source and topic columns a header row names (None when it names no front and back)."""
    names = [_fold(re.sub(r"[*_`]", "", c)) for c in cells]
    q = next((i for i, n in enumerate(names) if n in FRONTS), None)
    a = next((i for i, n in enumerate(names) if n in BACKS), None)
    if q is None or a is None:
        return None
    return q, a, next((i for i, n in enumerate(names) if n in SOURCES), None), \
        next((i for i, n in enumerate(names) if n in TOPICS), None)


def _rows_with(cols: tuple[int, int, Optional[int], Optional[int]], rows: list[list[str]]) -> list[dict[str, Any]]:
    q, a, s, tp = cols
    out = []
    for cells in rows:
        cells = [c.strip() for c in cells]
        if max(q, a) < len(cells) and cells[q] and cells[a]:
            out.append({"q": cells[q], "a": cells[a], "source": cells[s] if s is not None and s < len(cells) else None,
                        "topic": cells[tp] if tp is not None and tp < len(cells) else ""})
    return out


def _table(text: str) -> list[dict[str, Any]]:
    """A Markdown table of cards (a Front and a Back column, in English or Turkish)."""
    rows = [[c for c in m.group(1).split("|")] for m in re.finditer(r"^\s*\|(.*)\|\s*$", text or "", re.M)]
    for i, cells in enumerate(rows):
        cols = _columns(cells)
        if cols:
            body = [r for r in rows[i + 1:] if not all(set(c.strip()) <= set("-: ") for c in r)]
            return _rows_with(cols, body)
    return []


def _delimited(text: str) -> list[dict[str, Any]]:
    """Cards as tab-separated lines (as Anki and Quizlet take them), or CSV with a Front/Back header row."""
    body = "\n".join(_blocks(text)) or text or ""
    lines = [ln for ln in body.replace("\r\n", "\n").split("\n") if ln.strip()]
    if not lines:
        return []
    tabs = sum(1 for ln in lines if "\t" in ln)
    delim = "\t" if tabs >= max(2, len(lines) // 2) else ";" if lines[0].count(";") >= 1 else ","
    rows = list(csv.reader(io.StringIO("\n".join(lines)), delimiter=delim))
    cols = _columns(rows[0]) if rows else None
    if cols:
        return _rows_with(cols, rows[1:])
    return _rows_with((0, 1, None, None), rows) if delim == "\t" else []


def read_cards(text: str) -> tuple[list[dict[str, Any]], bool, str]:
    """Pasted flashcards: [{"q", "a", "source", "topic"}], whether the text said they are cards (a "cards" list,
    front/back keys or columns), and the kit's id it echoes."""
    from .outputs.flashcards import parse_cards

    rows: list[dict[str, Any]] = []
    said, kid = False, ""
    for body in _blocks(text) or [text]:
        data = _loads(body)
        if data is None:
            continue
        data = _en_keys(data)
        if isinstance(data, dict) and isinstance(data.get("kit"), str):
            kid = kid or data["kit"].strip().lower()
        got, says = _card_rows(data)
        rows += got
        said = said or (says and bool(got))
    if not rows:
        rows = _table(text) or _delimited(text)
        said = bool(rows)
    if not rows:
        rows = [{"q": c["q"], "a": c["a"], "source": None, "topic": ""} for c in parse_cards(text or "")]
    rows = [r for r in rows if len(r["q"]) >= 3 and r["a"]]
    return rows, said and bool(rows), kid or kit_in(text)


def kind_of(text: str) -> str:
    """What a paste holds: "quiz", "cards" or "" (neither). A quiz wins unless the text says it holds cards: a
    Markdown quiz's "Answer:" lines read as cards too. Questions none of which can be used are still a quiz (the
    import then says why)."""
    from .quizzes import answered, clean_question

    rows, said, _ = read_cards(text)
    if said:
        return "cards"
    raw = read_quiz(text)
    if any(isinstance(q, dict) and clean_question(q, None) for q in raw["questions"]):
        return "quiz"
    if rows:
        return "cards"
    return "quiz" if raw["questions"] and answered(raw) else ""


# ------------------------------------------------------------------ where each question comes from
_REF = re.compile(r"\b([FL])(\d{1,3})\b")
_PAGE = re.compile(r"\b(?:pages?|pp|pg|p|sayfa|sf|s|slides?|slayt)\b\.?\s*(\d{1,4})", re.I)
_PART = re.compile(r"\b(?:part|bölüm|kısım|p)\b\.?\s*(\d{1,2})\b", re.I)
_TIME = re.compile(r"(?<![\d:])(\d{1,2}:\d{2}(?::\d{2})?)(?![\d:])")
_SOURCE_KEYS = ("source", "sources", "ref", "reference", "references", "from", "cite", "citation")


def normal_source(v: Any, names: list[tuple[str, str]]) -> Any:
    """A source as a web chat writes it ("F3 - Slides.pdf, page 12", "L2 (Part 1, 14:05)", a file's name with no
    reference) in the form quizzes.parse_source reads ("F3 p.12", "L2 part 1 14:05"); else as it was."""
    if isinstance(v, list):
        return [normal_source(x, names) for x in v]
    if not isinstance(v, str) or not v.strip():
        return v
    s = " ".join(v.split())
    m = _REF.search(s)
    ref = f"{m.group(1)}{m.group(2)}" if m else next((r for name, r in names if name in s.casefold()), "")
    if not ref:
        return v
    rest = s[m.end():] if m else s
    if ref.startswith("F"):
        page = _PAGE.search(rest) or _PAGE.search(s)
        return f"{ref} p.{page.group(1)}" if page else ref
    part, at = _PART.search(rest), _TIME.search(rest)
    return ref + (f" part {part.group(1)}" if part else "") + (f" {at.group(1)}" if at else "")


def _names(kit: Optional[dict[str, Any]]) -> list[tuple[str, str]]:
    """The kit's files by name (with and without the extension; the longest first), for a source that names one."""
    out = []
    for ref, x in ((kit or {}).get("refs") or {}).items():
        name = str(x.get("name") or "").casefold().strip() if x.get("kind") == "file" else ""
        for n in {name, name.rsplit(".", 1)[0]}:
            if len(n) >= 5:
                out.append((n, ref))
    return sorted(out, key=lambda x: -len(x[0]))


def _sourced(items: list[dict[str, Any]], keys: Iterable[str], names: list[tuple[str, str]]) -> list[str]:
    """Each item's sources made readable (normal_source), in place; the references they cite."""
    cited: list[str] = []
    for q in items:
        for k in keys:
            if k in q and q[k] not in (None, "", [], {}):
                q[k] = normal_source(q[k], names)
                cited += [f"{a}{b}" for a, b in _REF.findall(json.dumps(q[k], ensure_ascii=False))]
    return list(dict.fromkeys(cited))


def _kit_of(lib: Any, course_id: str, kit_id: str, items: list[dict[str, Any]],
            keys: Iterable[str]) -> tuple[Optional[dict[str, Any]], list[str]]:
    """The kit a paste comes from: the one whose id it names, else the course's newest when it cites references (a
    quiz from another chat, citing none, is checked against the whole course so far); and the references cited, the
    items' sources made readable on the way."""
    newest = load(lib, course_id, kit_id)
    cited = _sourced(items, keys, _names(newest))
    named = bool(kit_id) and newest is not None and newest.get("id") == kit_id
    return (newest if named or cited else None), cited


def _facts(lib: Any, settings: Any, course_id: str, kit: Optional[dict[str, Any]], cited: list[str],
           kind: str) -> dict[str, Any]:
    """The material a paste is checked against: its kit's weeks, files and lectures with those its sources cite
    (else the course's weeks so far), as quizzes.gather reads them, numbered with the course's references (a file or
    lecture the course never numbered gets one no chat can cite: it is found by its words)."""
    from . import quizzes
    from .plans import Scope
    from .views import course_detail

    ref_of = numbers(lib, course_id)
    by_ref = {r: i for i, r in ref_of.items()}
    d = course_detail(lib, settings, course_id) or {}
    week_of: dict[str, int] = {}
    for w in d.get("weeks") or []:
        for u in w.get("units") or []:
            for m in u.get("materials") or []:
                week_of.setdefault(m["id"], w["no"])
        for day in w.get("days") or []:
            week_of.setdefault(day["day_key"], w["no"])
    refs = (kit or {}).get("refs") or {}
    weeks = set((kit or {}).get("weeks") or [])
    files = {x["id"] for x in refs.values() if x.get("kind") == "file" and x.get("id")}
    days = {x["day_key"] for x in refs.values() if x.get("kind") == "lecture" and x.get("day_key")}
    for r in cited:
        i = by_ref.get(r)
        if i:
            (days if "|" in i else files).add(i)
            if week_of.get(i):
                weeks.add(week_of[i])
    weeks |= {week_of[i] for i in files | days if week_of.get(i)}
    scope = Scope.from_json({"kind": "quiz", "weeks": sorted(weeks), "files": sorted(files), "days": sorted(days),
                             "picked": bool(files or days), "focus": (kit or {}).get("focus") or ""})
    facts = quizzes.gather(lib, settings, course_id, scope)
    facts.update(pack="", chars=0, cut=[], left_out=[], files_cut=[], one_lecture=False)
    for prefix, key, items in (("F", "id", facts["files"]), ("L", "day_key", facts["lectures"])):
        for i, x in enumerate(items):
            x["ref"] = ref_of.get(x[key]) or f"{prefix}{1000 + i}"
    facts["stressed"] = dict((kit or {}).get("stressed") or {})
    facts["kind"] = kind
    return facts


def _found(src: Optional[dict[str, Any]], found: bool) -> Optional[dict[str, Any]]:
    """A source found by its words, marked so where the page shows it ("found by its words")."""
    return {**src, "found": True} if src and found else src


# ------------------------------------------------------------------ saving what was pasted
def import_quiz(lib: Any, settings: Any, course_id: str, text: str, kit_id: str = "") -> dict[str, Any]:
    """A pasted quiz, checked and saved as a finished quiz of the course; what was read of it."""
    from . import quizzes
    from .doclang import span_title
    from .plans import Scope

    raw = read_quiz(text)
    if not raw["questions"] or not quizzes.answered(raw):
        raise Unread(t("No quiz questions were found in what you pasted. Copy the whole answer from ChatGPT or Claude "
                       "(its copy button under the answer works) and paste it again."))
    kit, cited = _kit_of(lib, course_id, kit_id or raw["kit"], [q for q in raw["questions"] if isinstance(q, dict)],
                         _SOURCE_KEYS)
    facts = _facts(lib, settings, course_id, kit, cited, "quiz")
    pid = uuid.uuid4().hex[:12]
    quiz = quizzes.clean_quiz(raw, facts, quizzes.source_units(lib, course_id, facts), seed=pid)
    if not quiz["questions"]:
        raise Unread(t("The questions could not be used: none had a clear right answer. Ask for the quiz again with "
                       "the prompt from the zip, then paste the whole answer."))
    for q in quiz["questions"]:
        q["source"] = _found(q.get("source"), q.get("found"))
    title = quiz["title"] or span_title(facts)
    cov = quizzes.coverage(quiz, facts)
    focus = (kit or {}).get("focus") or ""
    kid = (kit or {}).get("id") or ""
    n, read = len(quiz["questions"]), len(raw["questions"])
    scope = {**Scope(kind="quiz", weeks=[w["no"] for w in facts.get("weeks") or []],
                     days=[x["day_key"] for x in facts["lectures"]], files=[f["id"] for f in facts["files"]],
                     picked=True, questions=n, focus=focus).to_json(), "source": "import", "kit": kid}
    result = {"kind": "quiz", "title": title, "made": dt.date.today().isoformat(), "language": facts.get("language") or "",
              "questions": quiz["questions"], "topics": quiz["topics"], "coverage": cov, "course": facts.get("course") or {},
              "asked": read, "source": "import", "kit": kid}
    lib.create_plan(pid, course_id, scope, title)
    lib.update_plan(pid, result=result, title=title, status="done", stage="done", progress=1.0,
                    message=tn(n, "{n} question", "{n} questions"))
    return {"kind": "quiz", "id": pid, "title": title, "questions": n, "read": read, "unused": max(0, read - n),
            "named": n - cov["found"] - cov["no_source"], "found": cov["found"], "no_source": cov["no_source"],
            "kit": kid, "focus": focus}


def import_cards(lib: Any, settings: Any, course_id: str, text: str, kit_id: str = "",
                 have: Iterable[str] = ()) -> dict[str, Any]:
    """Pasted flashcards, checked and saved as a finished card set of the course (those the deck has already, or the
    paste twice, once); what was read of them."""
    from . import cards, quizzes, stressed
    from .plans import Scope

    rows, _, said_kit = read_cards(text)
    if not rows:
        raise Unread(t("No flashcards were found in what you pasted. Copy the whole answer from ChatGPT or Claude and "
                       "paste it again."))
    kit, cited = _kit_of(lib, course_id, kit_id or said_kit, rows, ("source",))
    facts = _facts(lib, settings, course_id, kit, cited, "cards")
    pid = uuid.uuid4().hex[:12]
    seen = {cards.norm(q) for q in have}
    made = cards.clean_cards(rows, facts, pid, seen, quizzes.source_units(lib, course_id, facts))[:CARDS_MOST]
    if not made:
        raise Unread(t("These cards are all in your deck already."))
    try:
        moments = [m for m in stressed.course_items(lib, course_id) if m["kind"] != "not_needed"]
    except Exception:  # noqa: BLE001 - the cards without their stressed marks
        moments = []
    for c in made:
        s = c.get("source") or {}
        c["stressed"] = s.get("kind") == "lecture" and any(
            m["day_key"] == s.get("day_key") and m["part"] == s.get("part") and abs(m["t"] - (s.get("t") or 0)) <= NEAR_S
            for m in moments)
        c["source"] = _found(c.get("source"), c.get("found"))
    found = sum(1 for c in made if c.get("found"))
    none = sum(1 for c in made if not c.get("source"))
    title = t("Flashcards pasted from ChatGPT or Claude")
    kid = (kit or {}).get("id") or ""
    scope = {**Scope(kind="cards", weeks=[w["no"] for w in facts.get("weeks") or []],
                     files=[f["id"] for f in facts["files"]], picked=True).to_json(), "source": "import", "kit": kid}
    result = {"kind": "cards", "title": title, "made": dt.date.today().isoformat(), "cards": made,
              "files": sorted({c["source"]["name"] for c in made if (c.get("source") or {}).get("kind") == "file"}),
              "language": facts.get("language") or "", "source": "import", "kit": kid}
    lib.create_plan(pid, course_id, scope, title)
    lib.update_plan(pid, result=result, title=title, status="done", stage="done", progress=1.0,
                    message=tn(len(made), "{n} card", "{n} cards"))
    return {"kind": "cards", "id": pid, "title": title, "cards": len(made), "read": len(rows),
            "dupes": max(0, len(rows) - len(made)), "named": len(made) - found - none, "found": found,
            "no_source": none, "kit": kid}


def pasted(api: Any, course_id: str, text: str, export_id: str = "", want: str = "") -> dict[str, Any]:
    """The Practice tab's paste (practice.PracticeApi.quiz_import, cards_import): a quiz or flashcards (``want``:
    only cards), read, checked and saved; {"ok", "kind", what was read} or why nothing was."""
    from .practice import _bump

    lib, settings = api._s.lib, api._s.settings
    if lib.get_course(course_id) is None:
        return {"ok": False, "error": t("This course is no longer in your list.")}
    text = str(text or "")
    if not text.strip():
        return {"ok": False, "error": t("Paste the answer first.")}
    if len(text) > PASTE_MOST:
        return {"ok": False, "error": t("This is too long to be one quiz. Paste one answer at a time.")}
    kind = "cards" if want == "cards" else kind_of(text)
    try:
        if kind == "cards":
            got = import_cards(lib, settings, course_id, text, str(export_id or ""),
                               have=[c["q"] for c in api._deck(course_id)])
        elif kind == "quiz":
            got = import_quiz(lib, settings, course_id, text, str(export_id or ""))
        else:
            raise Unread(t("IEUStudia found no quiz and no flashcards in what you pasted. Copy the whole answer from "
                           "ChatGPT or Claude (its copy button under the answer works) and paste it again."))
    except Unread as exc:
        return {"ok": False, "error": str(exc)}
    _bump()
    log.info("Pasted %s: %s read, %s kept", got["kind"], got["read"], got.get("questions", got.get("cards")))
    return {"ok": True, **got}
