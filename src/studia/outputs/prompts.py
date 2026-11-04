"""Prompts that turn a transcript (+ slides) into study material with ChatGPT / Claude."""

from __future__ import annotations

from ..i18n import N_

TRANSCRIPT_NOTES = (
    "About the material: it is an automatic speech-recognition transcript of a university lecture, so names, "
    "symbols and formulas may be misheard — fix obvious recognition errors from context (and from the slides, "
    "if attached) but say when you are guessing. Timestamps are [mm:ss] from the start of each part. Passages "
    "tagged [TR] were spoken in Turkish: use them, translating when you quote. ⭐ marks moments the lecturer "
    "emphasised (exam, important, homework, schedule); ⚠ and [unclear] mark audio that was hard to understand. "
    "Answer in English unless I ask otherwise."
)

TEMPLATES: dict[str, dict[str, str]] = {          # labels in English; t() to show one (the prompts stay English)
    "notes": {
        "label": N_("Study notes"),
        "prompt": (
            "You are my study assistant. Turn the lecture into complete, well-structured study notes.\n\n"
            "1. **Overview** — 3–5 sentences: what was covered and how it connects to the course.\n"
            "2. **Topics, in lecture order** — for each: a heading; a clear explanation; every definition, "
            "theorem and formula (LaTeX: $P(A\\mid B)$ in a sentence, $$...$$ on lines of their own for a displayed "
            "formula); the lecturer's worked examples step by step; "
            "and the timestamp where it starts, e.g. (Part 2, 14:05). If slides are attached, refer to them "
            "(\"Slide 7\") and use them to correct formulas.\n"
            "3. **What the lecturer emphasised** — everything marked ⭐ or described as important / likely on "
            "the exam, with timestamps.\n"
            "4. **Announcements & deadlines** — homework, quizzes, exams, schedule changes.\n"
            "5. **Check yourself** — 5 short questions with answers.\n"
            "6. **Possibly misheard** — places that look wrong or are marked ⚠/[unclear], so I can re-watch them.\n\n"
        ),
    },
    "exam": {
        "label": N_("Exam focus"),
        "prompt": (
            "You are helping me prepare for an exam. From the lecture, extract only what is most likely "
            "to be examined:\n\n"
            "1. **Lecturer's explicit hints** — everything said to be important, on the exam/quiz, or to be "
            "remembered (⭐ moments), quoted briefly with timestamps.\n"
            "2. **Core concepts & formulas** — the minimum set I must know, each with a one-line explanation "
            "and when to use it.\n"
            "3. **Typical exam questions** — 5–8 realistic questions in the lecturer's style (based on the "
            "examples worked in class), with concise solutions.\n"
            "4. **Common mistakes** the lecturer warned about.\n"
            "5. **Announcements** — dates, deadlines, quiz/exam rules.\n\n"
        ),
    },
    "flashcards": {
        "label": N_("Flashcards"),
        "prompt": (
            "Create 20–30 flashcards from the lecture, covering definitions, formulas, key ideas and the "
            "worked examples. Format each as:\n\nQ: …\nA: …  (Part n, mm:ss)\n\n"
            "Keep answers short and precise; put formulas in LaTeX between single dollars ($...$). Prefer what the lecturer emphasised (⭐).\n\n"
        ),
    },
    "plain": {"label": N_("Transcript only"), "prompt": ""},
}


def ai_prompt(template: str) -> str:
    tpl = TEMPLATES.get(template) or TEMPLATES["notes"]
    if not tpl["prompt"]:
        return ""
    return tpl["prompt"] + TRANSCRIPT_NOTES


PROMPTS_MD = f"""# Using your transcripts with ChatGPT or Claude

IEUStudia writes one Markdown file per class day (all parts of the lecture) and a **Study pack**
per course with every transcribed lecture in one file. In the app you can also export an **AI pack**
(PDF with the slides next to what was said + the transcript), which is the best thing to upload.

**How to use:** upload the AI pack PDF (or the day's `.md` / the course *Study pack*) to ChatGPT or
Claude, then paste one of the prompts below. In IEUStudia, **Copy for AI** puts a prompt and the
transcript on your clipboard in one step.

## Study notes

{TEMPLATES['notes']['prompt']}{TRANSCRIPT_NOTES}

## Exam focus

{TEMPLATES['exam']['prompt']}{TRANSCRIPT_NOTES}

## Flashcards

{TEMPLATES['flashcards']['prompt']}{TRANSCRIPT_NOTES}

## Tips

- For a whole course before an exam, upload the course **Study pack** and use *Exam focus*.
- Ask follow-ups such as "Explain the example at Part 2, 14:05 step by step" or "Quiz me on this lecture".
- If something looks wrong, check the timestamp in Panopto — the transcript is automatic.
"""
