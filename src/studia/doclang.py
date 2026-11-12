"""The language of the study documents the AI writes (Settings → AI → "Language of study documents"): the one the
course is taught in (the default, as before), or English or Turkish whatever the course.

Every document goes through :func:`document_language`: study notes, exam focus and flashcards (services.ai_notes),
study packs (outline, chapters, figures' captions), cheatsheets, exam review sheets, video plans' text, practice
quizzes and cards, "Sum it up with AI" (practice.py) and, over its courses, the weekly digest's summary
(:func:`for_courses`). Chats are not documents: they answer in the language the student asks in.

A document in another language than the course's keeps the course's technical terms as its exams use them, with the
meaning beside them the first time (outputs/prompts.py's ``terms_rule`` says so in one sentence).

The few words IEUStudia itself writes into a document (a study pack's cover and contents, the boxes' and figures'
labels, a cheatsheet's or a practice quiz's headings, their dates) are in the document's language too: drawn inside
:func:`writing_in`, :func:`word` gives them in it (they are written in English in the code)."""

from __future__ import annotations

import contextlib
import contextvars
import datetime as dt
import re
from typing import Any, Iterable, Iterator, Optional

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


# ------------------------------------------------------------------ the words IEUStudia writes into a document
# English in the code; Turkish here (the app's own Turkish where it has the word: "Konu anlatımı", "Formül kâğıdı")
WORDS_TR = {
    # a study pack's cover and contents (studypack.py), a practice quiz's (quizzes.py)
    "Study pack": "Konu anlatımı",
    "Practice quiz": "Deneme sınavı",
    "Cheatsheet": "Formül kâğıdı",
    "made with IEUStudia on {date}": "IEUStudia ile {date} tarihinde hazırlandı",
    "Files: {list}": "Dosyalar: {list}",
    "Lectures: {list}": "Dersler: {list}",
    "Written by AI from your course material: check anything that looks wrong against your slides and lectures.":
        "Ders materyalinden yapay zeka yazdı: yanlış görünen her şeyi slaytlarına ve derslerine bakarak kontrol et.",
    "The answers are at the end.": "Cevaplar sonda.",
    "Contents": "İçindekiler",
    # boxes and alerts in the AI's Markdown (studypack.md_html, outputs/doc.py)
    "Definition": "Tanım",
    "Key idea": "Ana fikir",
    "Example": "Örnek",
    "Common mistake": "Sık yapılan hata",
    "Check yourself": "Kendini sına",
    "Note": "Not",
    "Tip": "İpucu",
    "Important": "Önemli",
    "Warning": "Uyarı",
    "Caution": "Dikkat",
    # figures
    "Figure {n}.": "Şekil {n}.",
    "Lecture slide": "Ders slaytı",
    "Course slide": "Ders dosyasından slayt",
    "{name}, page {page}": "{name}, sayfa {page}",
    "lecture of {date}, part {part}, slide {slide}": "{date} dersi, {part}. bölüm, slayt {slide}",
    # weeks
    "week {n}": "{n}. hafta",
    "weeks {a}–{b}": "{a}–{b}. haftalar",
    "all weeks so far": "şimdiye kadarki bütün haftalar",
    # a cheatsheet's entries and its line about what it was made from (cheatsheet.py)
    "Def": "Tanım",
    "Rule": "Kural",
    "Method": "Yöntem",
    "Unit": "Birim",
    "Idea": "Fikir",
    "if": "koşul",
    "{list} and {n} more files": "{list} ve {n} dosya daha",
    "lecture {list}": "ders: {list}",
    "lectures {list}": "dersler: {list}",
    "{list} and {n} more": "{list} ve {n} tane daha",
    # a practice quiz (quizzes.py)
    "{n} questions on {span}": "{span} konularından {n} soru",
    "for {name}": "{name} için",
    "Left out: {list}": "Dışarıda kalanlar: {list}",
    "files not on the PC: {list}": "bilgisayarda olmayan dosyalar: {list}",
    "lectures not transcribed: {list}": "yazıya dökülmemiş dersler: {list}",
    "no question on: {list}": "soru çıkmayan konular: {list}",
    "From {source}": "Kaynak: {source}",
    "Lecture {date}, part {part}, {ts}": "{date} dersi, {part}. bölüm, {ts}",
    "Questions": "Sorular",
    "Answers": "Cevaplar",
    "Answer:": "Cevap:",
}
_MONTHS = {"en": ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
           "tr": ("Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara")}
_PH = re.compile(r"\{(\w+)\}")
_writing: contextvars.ContextVar[str] = contextvars.ContextVar("studia_doc_language", default="en")


def code(language: str = "") -> str:
    """ "tr" for Turkish ("Turkish", "tr"), else "en": a document's language as its <html lang> gives it."""
    return "tr" if str(language or "").strip().lower() in ("tr", "turkish", "türkçe") else "en"


@contextlib.contextmanager
def writing_in(language: str = "") -> Iterator[str]:
    """While a document in ``language`` ("Turkish"/"tr"; anything else is English) is drawn, :func:`word`,
    :func:`date_text`, :func:`day_text` and :func:`weeks_text` give its words in it. Gives its code ("en", "tr")."""
    lang = code(language)
    token = _writing.set(lang)
    try:
        yield lang
    finally:
        _writing.reset(token)


def writing() -> str:
    """The language of the document being drawn ("en" outside :func:`writing_in`)."""
    return _writing.get()


def word(text: str, /, **params: Any) -> str:
    """One of the words IEUStudia writes into a document (``WORDS_TR``'s English), in the document's language, its
    {placeholders} filled."""
    s = WORDS_TR.get(text, text) if _writing.get() == "tr" else text
    return _PH.sub(lambda m: str(params[m.group(1)]) if m.group(1) in params else m.group(0), s) if params else s


def day_text(d: Any) -> str:
    """ "14 Oct" / "14 Eki" (an ISO date as text too; text that isn't one comes back as it is)."""
    try:
        d = d if isinstance(d, dt.date) else dt.date.fromisoformat(str(d)[:10])
    except ValueError:
        return str(d)
    return f"{d.day} {_MONTHS[_writing.get()][d.month - 1]}"


def date_text(d: Any) -> str:
    """ "12 Nov 2026" / "12 Kas 2026"."""
    try:
        d = d if isinstance(d, dt.date) else dt.date.fromisoformat(str(d)[:10])
    except ValueError:
        return str(d)
    return f"{day_text(d)} {d.year}"


def weeks_text(weeks: Iterable[Any]) -> str:
    """ "week 3, weeks 5–7" / "3. hafta, 5–7. haftalar" ("all weeks so far" without any): the week numbers, or
    the {"no": …} of a study pack's or a quiz's facts."""
    nums = [int(w["no"] if isinstance(w, dict) else w) for w in weeks]
    if not nums:
        return word("all weeks so far")
    runs: list[list[int]] = []
    for w in nums:
        if runs and w == runs[-1][1] + 1:
            runs[-1][1] = w
        else:
            runs.append([w, w])
    return ", ".join(word("week {n}", n=a) if a == b else word("weeks {a}–{b}", a=a, b=b) for a, b in runs)


def first_upper(s: str) -> str:
    """The first letter a capital (Turkish "i" is "İ"); the rest as it is (str.capitalize lowers it)."""
    if not s:
        return s
    head = "İ" if s[0] == "i" and _writing.get() == "tr" else s[0].upper()
    return head + s[1:]


def language_of(facts: dict[str, Any]) -> str:
    """The language a study pack's or a cheatsheet's text is in: the one its facts were gathered with (4.1), else
    (one made before) the course's as told from the material (taught_in)."""
    return (facts or {}).get("language") or taught_in({}, facts)