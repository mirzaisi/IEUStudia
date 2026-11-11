"""Prompts that turn a transcript (+ slides) into study material with ChatGPT / Claude, and the house style every
document the AI writes for IEUStudia shares (STYLE: study notes, study packs, cheatsheets, review sheets, video plans,
the weekly digest)."""

from __future__ import annotations

from ..i18n import N_

# How the AI writes: like a good tutor, not like a brochure (kept short: it goes with every request)
STYLE = ("Write like a good tutor talking to one student: plain, concrete sentences that say why; detail where the "
         "exam needs it, nothing for show. No filler openings or closings (\"In summary\", \"Let's\"), buzzwords, "
         "hedging or em dashes. Bold only a term where it is defined; a short formula stays in its sentence.")


def terms_rule(language: str = "", taught_in: str = "") -> str:
    """For a document in another language than the course's (doclang.py): its exam terms stay as the exams use them
    ("" when the two agree)."""
    lang = language or "English"
    if not taught_in or taught_in == lang:
        return ""
    return f"Keep technical terms in {taught_in}, as the exams use them, with the {lang} meaning in brackets the first time."


def written_in(language: str = "", taught_in: str = "") -> str:
    """The language line: the document's language (doclang.document_language; English unless told otherwise), and the
    terms rule when the course is taught in another one."""
    lang = language or "English"
    rule = terms_rule(lang, taught_in)
    return f"Write in {lang}. {rule}" if rule else f"Write in {lang}, the language the course is taught in."


def in_language(language: str = "", taught_in: str = "") -> str:
    """As :func:`written_in`, for a sentence that names what to write first: "Write every question … " + this."""
    lang = language or "English"
    rule = terms_rule(lang, taught_in)
    return f"in {lang}. {rule}" if rule else f"in {lang} (the language this course is taught in)."


TRANSCRIPT_NOTES = (
    "About the material: an automatic transcript of a university lecture, so names, symbols and numbers may be "
    "misheard. Fix obvious errors from context and the slides; where a reading stays doubtful, say so once, briefly. "
    "Times are [mm:ss] from the start of each part. [TR] marks Turkish speech (translate what you quote), ⭐ what the "
    "lecturer stressed, ⚠ and [unclear] audio that was hard to hear."
)

TEMPLATES: dict[str, dict[str, str]] = {          # labels in English; t() to show one (the prompts stay English)
    "notes": {
        "label": N_("Study notes"),
        "prompt": (
            "Write study notes on this lecture for a student who missed it.\n"
            "- Open with two or three sentences: what the lecture covered and where it fits in the course.\n"
            "- Then a ## section per topic, in lecture order, its start time in the heading, e.g. \"## Bayes' theorem "
            "(Part 1, 09:54)\". Explain the idea, give every definition, result and formula the lecturer used, and work "
            "through the lecturer's examples step by step with the numbers checked.\n"
            "- ## What the lecturer stressed: what was called important or likely on the exam (⭐), with times.\n"
            "- ## Announcements: homework, quizzes, exams, schedule changes (leave it out when there were none).\n"
            "- ## Check yourself: five short questions, each followed by its answer.\n"
            "- Last, only if some passages were too unclear to use: one line each with its time, to re-watch.\n\n"
        ),
    },
    "exam": {
        "label": N_("Exam focus"),
        "prompt": (
            "Write an exam-focus sheet for this lecture: only what is likely to be examined.\n"
            "1. What the lecturer called important or said will be on an exam (⭐): a short quote and its time each.\n"
            "2. The core definitions and formulas, each with one line on when to use it.\n"
            "3. Five to eight exam questions in the lecturer's style, built on the examples worked in class, each with "
            "a short worked solution.\n"
            "4. The mistakes the lecturer warned about.\n"
            "5. Dates, deadlines and rules announced for quizzes and exams.\n\n"
        ),
    },
    "flashcards": {
        "label": N_("Flashcards"),
        "prompt": (
            "Write 20 to 30 flashcards on this lecture: definitions, formulas, key ideas and the worked examples, "
            "first what the lecturer stressed (⭐). Each card as:\n\nQ: …\nA: …  (Part n, mm:ss)\n\n"
            "Short, exact answers; formulas in LaTeX between single dollars ($...$).\n\n"
        ),
    },
    "plain": {"label": N_("Transcript only"), "prompt": ""},
}


def ai_prompt(template: str, language: str = "", taught_in: str = "") -> str:
    """The instruction for notes of ``template`` kind, in ``language`` ("" for English; doclang.document_language)."""
    tpl = TEMPLATES.get(template) or TEMPLATES["notes"]
    if not tpl["prompt"]:
        return ""
    return f"{tpl['prompt']}{TRANSCRIPT_NOTES}\n{STYLE} {written_in(language, taught_in)}"


# The slides sent with study notes: where they are in the transcript, and (notes, exam focus) the ones worth showing.
# A figure line the AI writes, "![caption](Part 2, slide 7)", becomes the slide's picture (outputs/figures.py).
NOTE_FIGURES_MAX = 6


def slides_note(sent: int, total: int, figures: bool = True) -> str:
    out = ("\n\nThe slide images are attached in order, each labelled [Part n, slide m]. In the transcript, (slide m) "
           "on a paragraph of Part n is that part's slide m, the one on screen (every part numbers its slides from "
           "1). Refer to a slide as (Part n, slide m).")
    if sent < total:
        out += f" {sent} of the {total} slides are attached, spread over the lecture; not the others."
    if figures:
        out += (f" Where a slide's diagram, graph, circuit, table or board work helps, show it on a line of its own "
                f"right after the text that explains it: ![what it shows, in a few words](Part n, slide m). At most "
                f"{NOTE_FIGURES_MAX}, only slides you were given, never a title slide or one of plain text.")
    return out


PROMPTS_MD = f"""# Using your transcripts with ChatGPT or Claude

IEUStudia writes one Markdown file per class day (all parts of the lecture) and a **Study pack**
per course with every transcribed lecture in one file. In the app you can also export an **AI pack**
(PDF with the slides next to what was said + the transcript), which is the best thing to upload.

**How to use:** upload the AI pack PDF (or the day's `.md` / the course *Study pack*) to ChatGPT or
Claude, then paste one of the prompts below. In IEUStudia, **Copy for AI** puts a prompt and the
transcript on your clipboard in one step.

## Study notes

{ai_prompt('notes')}

## Exam focus

{ai_prompt('exam')}

## Flashcards

{ai_prompt('flashcards')}

## Tips

- For a whole course before an exam, upload the course **Study pack** and use *Exam focus*.
- Ask follow-ups such as "Explain the example at Part 2, 14:05 step by step" or "Quiz me on this lecture".
- If something looks wrong, check the timestamp in Panopto: the transcript is automatic.
"""
