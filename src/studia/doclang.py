"""The language of the study documents the AI writes (Settings → AI → "Language of study documents"): the one the
course is taught in (the default, as before), or English or Turkish whatever the course.

Every document goes through :func:`document_language`: study notes, exam focus and flashcards (services.ai_notes),
study packs (outline, chapters, figures' captions), cheatsheets, exam review sheets, video plans' text, practice
quizzes and cards, "Sum it up with AI" (practice.py) and, over its courses, the weekly digest's summary
(:func:`for_courses`). Chats are not documents: they answer in the language the student asks in.

A document in another language than the course's keeps the course's technical terms as its exams use them, with the
meaning beside them the first time (outputs/prompts.py's ``terms_rule`` says so in one sentence)."""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

CHOICES = ("course", "en", "tr")           # the setting's values (config.Settings.doc_language)
DEFAULT = "course"
NAMES = {"en": "English", "tr": "Turkish"}  # as the prompts name them (the page shows them translated)


def taught_in(d: dict[str, Any], facts: Optional[dict[str, Any]] = None) -> str:
    """The language the course is taught in: "English" or "Turkish" - the syllabus's course language, else what the
    lectures (their [TR] passages) and files of ``facts["pack"]`` are mostly in."""
    lang = ((d or {}).get("syllabus") or {}).get("language", "").lower()
    if "turk" in lang or "türk" in lang:
        return "Turkish" if not ("engl" in lang or "ingiliz" in lang or "İngiliz" in lang) else "English"
    if "engl" in lang or "ingiliz" in lang:
        return "English"
    pack = (facts or {}).get("pack", "")
    lines = [x for x in pack.split("\n") if re.match(r"\[\d+(?::\d{2}){1,2}\]", x)]
    if lines:
        tr = sum(1 for x in lines if "[TR]" in x[:40])
        return "Turkish" if tr > len(lines) / 2 else "English"
    letters = [c for c in pack if c.isalpha()]
    turkish = sum(1 for c in letters if c in "çğışöüÇĞİŞÖÜ")
    return "Turkish" if letters and turkish / len(letters) > 0.01 else "English"


def choice(settings: Any = None) -> str:
    """The setting: "course", "en" or "tr" (no settings, or an unknown value: "course")."""
    v = getattr(settings, "doc_language", DEFAULT) if settings is not None else DEFAULT
    return v if v in CHOICES else DEFAULT


def document_language(d: dict[str, Any], settings: Any = None, facts: Optional[dict[str, Any]] = None) -> dict[str, str]:
    """The language a document of the course ``d`` is written in, by the setting: {"language": "English" or
    "Turkish", "taught_in": the course's own language} (the two differ only when the student chose so)."""
    course = taught_in(d, facts)
    return {"language": NAMES.get(choice(settings), course), "taught_in": course}


def for_courses(languages: Iterable[str], settings: Any = None) -> dict[str, str]:
    """The weekly digest's summary, over several courses: as :func:`document_language`, the courses' language being
    the one most of them are taught in (English on a tie, or with none)."""
    langs = [x for x in languages if x]
    course = "Turkish" if langs and langs.count("Turkish") > len(langs) / 2 else "English"
    return {"language": NAMES.get(choice(settings), course), "taught_in": course}
