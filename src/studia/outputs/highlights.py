"""Spot what students care about: exam hints, "this is important", homework, schedule changes.

Curated English + Turkish cue phrases (word-boundary regexes, Turkish-aware lowercasing).
Recall matters more than perfect precision: a highlight is a pointer, the full text is right there.
"""

from __future__ import annotations

import re

from ..i18n import N_

_EN = {
    "exam": (r"\b(exams?|midterms?|final exams?|in the final|on the final|for the final|quiz(?:zes)?|"
             r"in the test|on the test|will be asked|i will ask|exam question)\b"),
    "important": (r"\b(important|crucial|essential|key point|pay attention|be careful|careful here|don'?t forget|"
                  r"do not forget|remember this|you should remember|you must remember|keep in mind|"
                  r"common mistake|make sure you|this is the trick|very critical)\b"),
    "homework": r"\b(homework|assignments?|projects? (?:is|are) due|due (?:date|on|next)|deadline|submit|submission|lab report)\b",
    "schedule": (r"\b(next week|next lecture|next class|next time we|tomorrow|no class|class is cancell?ed|"
                 r"make-?up class|office hours?)\b"),
}
_TR = {
    "exam": (r"(\bsınav\w*|\bvize\w*|\bfinal(?:de|da|e|i)?\b|\bquiz\w*|\bbütünleme\w*|\bsorarım\b|\bsoracağım\b|"
             r"sınavda çık\w*)"),
    "important": (r"(\bönemli\w*|\bdikkat\w*|\bunutmayın\w*|\bunutmayalım\b|\baklınızda\w*|\bnot alın\b|\bmutlaka\b|"
                  r"\bkesinlikle\b|\bçok kritik\b|\bbilmeniz gerek\w*)"),
    "homework": r"(\bödev\w*|\bteslim\w*|\bson gün\w*|\bson tarih\w*|\bproje(?:si|niz|nizi|ler|leri|yi)?\b)",
    "schedule": r"(\bhaftaya\b|\bgelecek hafta\w*|\bbir sonraki ders\w*|\byarın\w*|\bders yok\b|\biptal\w*|\btelafi\w*)",
}
_EN_RE = {k: re.compile(v, re.I) for k, v in _EN.items()}
_TR_RE = {k: re.compile(v) for k, v in _TR.items()}


def _tr_lower(s: str) -> str:
    return s.replace("I", "ı").replace("İ", "i").lower()


def detect(text: str, lang: str = "en") -> list[str]:
    """Cue categories found in a paragraph (order: exam, important, homework, schedule)."""
    if not text:
        return []
    found = []
    turkish = lang == "tr" or any(c in text for c in "çğışöüÇĞİŞÖÜ")
    low_tr = _tr_lower(text)
    for key in ("exam", "important", "homework", "schedule"):
        if key == "exam":
            if _exam(text, low_tr, turkish):
                found.append(key)
        elif _EN_RE[key].search(text) or (turkish and _TR_RE[key].search(low_tr)):
            found.append(key)
    return found


# a passing "I will ask" / "soracağım" is no exam hint: only with an exam named in the paragraph too
_BARE_ASK = re.compile(r"i will ask|sorarım|soracağım")
_EXAM_NAMED_EN = re.compile(r"\b(?:exams?|examinations?|midterms?|final exams?|quiz(?:zes)?)\b", re.I)
_EXAM_NAMED_TR = re.compile(r"\bsınav\w*|\bvize\w*|\bfinal(?:de|da|e|i)?\b|\bquiz\w*|\bbütünleme\w*")


def _exam(text: str, low_tr: str, turkish: bool) -> bool:
    hits = [m.group(0).lower() for m in _EN_RE["exam"].finditer(text)]
    if turkish:
        hits += [m.group(0) for m in _TR_RE["exam"].finditer(low_tr)]
    if any(not _BARE_ASK.fullmatch(x) for x in hits):
        return True
    return bool(hits) and bool(_EXAM_NAMED_EN.search(text) or (turkish and _EXAM_NAMED_TR.search(low_tr)))


LABELS = {"exam": N_("Exam"), "important": N_("Important"), "homework": N_("Homework"), "schedule": N_("Schedule")}


def excerpt(text: str, tags: list[str], limit: int = 170) -> str:
    """A short excerpt around the first cue (whole text if short)."""
    if len(text) <= limit:
        return text
    pos = None
    low_tr = _tr_lower(text)
    for key in tags:
        m = _EN_RE[key].search(text) or _TR_RE[key].search(low_tr)
        if m:
            pos = m.start()
            break
    pos = pos or 0
    start = max(0, pos - limit // 3)
    end = min(len(text), start + limit)
    start = max(0, end - limit)
    if start > 0:
        sp = text.find(" ", start)
        start = sp + 1 if 0 <= sp < pos else start
    if end < len(text):
        sp = text.rfind(" ", start, end)
        end = sp if sp > pos else end
    return ("…" if start > 0 else "") + text[start:end].strip() + ("…" if end < len(text) else "")
