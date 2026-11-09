"""What the lecturer stressed in class: the moments of a course's transcribed lectures where the lecturer says that
something matters ("this is important", "be careful", "a common mistake", "I will ask this in the exam", "sınavda
çıkar", "bunu mutlaka bilin") or that it doesn't ("you are not responsible for this"). Found with English and
Turkish cue phrases, cleaned of noise (dates, rooms and other logistics are the Heads-up's; a bare "this is
important" gets the sentence it points at) and of repeats (the same point said again, in this lecture or a later
one), grouped by week with the week's topic, each with its quote, the lecture and the time. No AI, so it is free
and instant.

An optional summary by the chosen AI ranks them into a short "Know this for the exam" list whose every point cites
the moments it comes from (summary_prompt, clean_summary). Practice quizzes, flashcards and study packs take this
list as their priorities (prompt_lines)."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import re
import threading
from typing import Any, Iterable, Optional

from . import __version__, store
from .outputs.common import format_ts
from .outputs.highlights import _tr_lower

log = logging.getLogger("studia")
VERSION = 4                # bump when what is found changes (a kept list is then found again)
SAME_POINT_S = 120         # a second cue this soon after the first, in the same part: the same point
SAME_WORDS = 0.45          # this much of the key words in common (Jaccard): the same point said again, later
SAME_DAY_WORDS = 3         # in one lecture, this many key words in common: the same point
SHORT_WORDS = 22           # a paragraph this short gets its neighbour as context ("This is important." + what)
QUOTE_CHARS = 300
TOP_PER_WEEK = 6           # shown at first in a week (the others one click away)
TOP_SCORE = 1.0            # and only those that stand out at least this much (a weak cue said all the time doesn't)
LEAST_WORDS = 4            # key words a moment needs to say something (fewer: "So again, please be careful.")

# the lecturer says something matters (what to learn, not when or where): English, and Turkish on Turkish-lowercased
# text. Strong cues insist; weak ones are what many lecturers say all the time ("be careful", "important idea"), so
# they count for less, and less again the more often this course's lecturer says them. "critical" and "essential"
# only where they can't be maths ("critical point", "essential singularity"); never "the main idea" (a recap).
_STRONG_EN = re.compile(
    r"\b(?:(?:very|really|super|extremely|so) important|crucial|essential(?! singularit)|(?:this|that) is (?:very )?critical|"
    r"common mistakes?|typical mistakes?|(?:students|people|you) (?:often|always|usually) (?:make|forget|confuse|mix)|"
    r"don'?t forget|do not forget|never forget|you (?:should|must|have to|need to) (?:remember|know|understand|learn)|"
    r"(?:learn|memori[sz]e|know) (?:this|these|it|them) (?:well|by heart)|by heart|the trick|key point|"
    r"i (?:have )?warned you|pay (?:special |extra )?attention)\b", re.I)
_WEAK_EN = re.compile(r"\b(?:important|be careful|careful here|watch out|remember (?:this|that)|keep in mind|"
                      r"make sure (?:you|that))\b", re.I)
_STRONG_TR = re.compile(
    r"(?:\bçok önemli\w*|\bmutlaka\b|\bkesinlikle\b|\bçok kritik\b|\bbilmeniz (?:gerek|lazım|şart)\w*|"
    r"\bbilmek zorundasınız\b|\bezberle\w*|\bsık yapılan hata\w*|\bhata yapıyorsunuz\b|\bpüf nokta\w*|"
    r"\bkilit nokta\w*|\bunutmayın\w*|\bunutmayalım\b)")
_WEAK_TR = re.compile(r"(?:\bönemli\w*|\bdikkat\w*|\baklınızda\w*)")
# the lecturer ties it to an assessment: asked, an exam question, "you are responsible for" (strong) …
_ASK_EN = re.compile(
    r"\b(?:will be asked|(?:exam|quiz|midterm) questions?|"
    r"(?:you are|you're) responsible for|will (?:definitely |surely |probably |certainly )?(?:be|come|appear|show up) "
    r"(?:in|on) (?:the|your) (?:exam|quiz|midterm|final)|expect (?:this|a question|questions|something like this) "
    r"(?:in|on) (?:the|your) (?:exam|quiz|midterm|final)|(?:are|is) (?:all )?included in the (?:exam|quiz|midterm|final))\b",
    re.I)
_COMES = r"(?:çık(?!ma)\w*|sor(?!ma|ulma)\w*|gel(?!me)\w*)"         # comes, is asked (not "çıkmaz", "sormayacağım")
_ASK_TR = re.compile(
    rf"(?:\bsoru olarak\b|\bsınavda {_COMES}|\bsınav sorusu\w*|\bquizde {_COMES}|"
    rf"\bvizede {_COMES}|\bfinalde {_COMES}|\bsorumlusunuz\b|\bsorumlu olacaksınız\b)")
# "I will ask" alone is as often a question in class ("Now I will ask you something"), a student's ("Hocam, bir şey
# soracağım") or asking someone else ("koordinatöre soracağım"): tied to an exam only with one named in the paragraph
_ASK_BARE_EN = re.compile(r"\b(?:i will ask|i'?ll ask|i am going to ask|i'?m going to ask)\b", re.I)
_ASK_BARE_TR = re.compile(r"(?:\bsorarım\b|\bsoracağım\b)")
# the plainest hints ("we may ask something like this", "one question is from here", "üçüncü soru böyle olacak
# sınavda"): with an exam named in their sentence or the one before, never denied there, never asked back
_HINT_EN = re.compile(r"\b(?:(?:if|when) (?:i|we) ask|(?:we|i)(?: (?:may|might|can|could|will|shall)|'ll) ask|"
                      r"(?:a|one|the) question (?:like this|is from|from here))\b", re.I)
_HINT_TR = re.compile(r"(?:\bsoru(?!n|m|larınız)\w*[^.!?…]*\b(?:sınav|vize|final)\w*|"     # (not sorun, sorum, sorumlu)
                      r"\b(?:sınav|vize|final)\w*[^.!?…]*\bsoru(?!n|m|larınız)\w*)")
_EXAM_EN = re.compile(r"\b(?:exams?|quiz(?:zes)?|midterms?|finals?)\b", re.I)
_DENIED_EN = re.compile(r"(?:\b(?:not|never|cannot)\b|n['’]t\b)(?! (?:forget|worry|panic))", re.I)
_DENIED_TR = re.compile(r"\w+m[ae](?:z|y[ae]c[ae]k|d[ıi])\w*|\w+m[ıiuü]yor\w*|\bdeğil\w*|\byok\b")   # çıkmaz, olmayacak
# … or names it (weaker: "for the midterm you need …", "last year's exam had …"; never a date or a room)
_ABOUT_EN = re.compile(r"\b(?:exams?|quiz(?:zes)?|midterms?|final exams?|"
                       r"(?:in|on|for|before) the final\b(?!\s+(?!exams?\b)[a-z]))\b", re.I)    # (not "the final step")
_ABOUT_TR = re.compile(r"(?:\bsınav\w*|\bvize\w*|\bquiz\w*|\bara sınav\w*|\bfinal(?:de|da|e|i)\b)")
# an exam named counts only with something to do ("you need to understand", "learn", "çalışmanız lazım"), and not
# when it is about the grading or the format ("four quizzes, 5 percent each", "20 questions, the last one open-ended")
_TODO_EN = re.compile(r"\b(?:you (?:need|have|should|must|ought) to|you need|need to|should|must|study|learn|know|"
                      r"understand|practi[cs]e|remember|focus|revise|review)\b", re.I)
_TODO_TR = re.compile(r"(?:\blazım\b|\bgerek\w*|\bçalış\w*|\bbilmeniz\w*|\böğren\w*|\btekrar edin\w*|\bodaklan\w*)")
_GRADING_EN = re.compile(r"\b(?:\d+(?:[.,]\d+)?\s*(?:percent|%|points?|questions?)|multiple[- ]choices?|open[- ]ended|"
                         r"weights?|counts? (?:for|towards)|per ?cent)\b", re.I)
_GRADING_TR = re.compile(r"(?:\byüzde\s*\d+|%\s*\d+|\b\d+\s*puan\w*|\b\d+\s*soru\w*|\bçoktan seçmeli\w*|\bklasik\b)")
# … or says it is not needed
_NOT_EN = re.compile(
    r"\b(?:(?:you are|you're|you will be|you'll be) not responsible for|not responsible for|"
    r"won'?t be (?:asked|on the (?:exam|quiz|midterm|final)|in the (?:exam|quiz|midterm|final))|"
    r"will not be (?:asked|on the (?:exam|quiz|midterm|final)|in the (?:exam|quiz|midterm|final))|"
    r"not (?:on|in|part of) the (?:exam|quiz|midterm|final)|(?:i )?won'?t ask|(?:i|we) will not ask|"
    r"you (?:don'?t|do not) (?:need|have) to (?:know|learn)|no need to (?:know|learn)|"
    r"(?:isn'?t|not) important for (?:the|your) (?:exam|quiz|midterm|final)|you can skip)\b", re.I)
_NOT_TR = re.compile(
    r"(?:\bsorumlu değilsiniz\b|\bsorumlu olmayacaksınız\b|\bsınavda (?:çıkmaz|çıkmayacak|sorulmaz|sorulmayacak)\w*|"
    r"\bsormayacağım\b|\bsormam\b(?!\s+(?:lazım|gerek\w*|şart))|\bbilmenize gerek yok\b|"      # (not "sormam lazım")
    r"\b(?:sınav|vize|final|quiz)\w* (?:için )?(?:çok |pek )?önemli değil)")
# said of learning by heart or of a step ("you don't need to memorize it", "this step is not important", "önemli
# değil"), not of the exam: no cue at all (blanked before the stressed cues, so not "important" or "ezberle" either)
_SOFT_EN = re.compile(r"\b(?:(?:don'?t|do not|needn'?t|no need to) (?:need to |have to )?(?:memori[sz]e|remember)\w*"
                      r"(?: (?:this|that|it|them))?|(?:not|isn'?t) (?:that |so |very )?important)\b", re.I)
_SOFT_TR = re.compile(r"(?:\bönemli değil\w*|\bezberle\w* gerek yok\b|\bezberlemeyin\b)")
# when and where, how it is graded and handed in, and running a quiz in class: the Heads-up tab has those (a moment
# saying only that is no point to study). Not words maths uses ("points", "results", "room temperature", "percent")
_LOGISTICS_EN = re.compile(
    r"\b(?:room\s*[a-z]?-?\d+|classroom|lecture hall|o'?clock|\d{1,2}:\d{2}|\d{1,2}\s*(?:am|pm|a\.m\.|p\.m\.)|"
    r"(?:mon|tues|wednes|thurs|fri|satur|sun)days?|january|february|april|june|july|august|september|october|"
    r"november|december|next (?:week|lecture|class)|tomorrow|open[- ]book|closed[- ]book|calculators?|"
    r"cheat ?sheets?|formula sheets?|make-?up|postpone\w*|reschedul\w*|cancell?ed|announce\w*|ieulearn|"
    r"e-?mails?|attendance|grades?|graded|grading|(?:percent|%) of (?:the|your) (?:grade|final|total)|"
    r"submit\w*|submissions?|deadlines?|due (?:date|on|next|by)|homeworks?|assignments?|syllabus|office hours?|"
    r"phones?|bags?|pockets?|take your seats?|(?:start|begin|continue with) (?:our|the|your) (?:\w+ )?(?:quiz|exam|midterm)|"
    r"let'?s (?:get )?ready for|"
    r"(?:check|checked|grade|marked) (?:your|the) (?:\w+ )?(?:quiz|quizzes|exams?|papers?)|questions? about the (?:exam|quiz|midterm)|"
    r"good luck|good (?:scores?|marks?))\b",
    re.I)
_LOGISTICS_TR = re.compile(
    r"(?:\bderslik\w*|\bamfi\w*|\bsaat\s*\d|\b\d{1,2}:\d{2}\b|\bpazartesi\w*|\bsalı\b|\bsalı(?:ya|da|günü)\b|"
    r"\bçarşamba\w*|\bperşembe\w*|\bcuma(?:ya|da|günü)?\b|\bcumartesi\w*|\bocak\w*|\bşubat\w*|\bnisan\w*|"
    r"\bmayıs\w*|\bhaziran\w*|\btemmuz\w*|\bağustos\w*|\beylül\w*|\bkasım\w*|\baralık\w*|\bhaftaya\b|"
    r"\bgelecek hafta\w*|\byarın\w*|\bhesap makine\w*|\bkopya\b|\btelafi\w*|\bertele\w*|\biptal\w*|\bduyur\w*|"
    r"\be-?posta\w*|\bmail\w*|\byoklama\w*|\bnot(?:lar(?:ı|ınız)?|unuz)\b|\bpuan\w*|\bteslim\w*|\bson tarih\w*|"
    r"\bödev\w*|\bşube\w*|\btelefon\w*|\bçanta\w*|\biyi şanslar\b|\bkolay gelsin\b)")
_WORD = re.compile(r"[^\W\d_]{4,}")
# words that say nothing about the point itself (the cues, the class talk): left out when two moments are compared
_FILLER = set("""please careful important importance idea ideas main also again right okay guys actually because
remember about really should would could think there their these those which where what when something maybe
very just like know need have this that with from your will then they them here okay yeah thank thanks dear dears
friends everyone class lesson course today said says saying mean means meaning understand clear question questions
exam exams quiz quizzes midterm final forget make sure keep mind pay attention point points thing things sorry
önemli dikkat arkadaşlar evet tamam şimdi burada bunu şunu yani bakın""".split())
KINDS = ("exam", "stressed", "not_needed")       # in this order of weight: tied to an exam, stressed, not needed
_BASE = {"ask": 6.0, "about": 2.5, "strong": 3.0, "weak": 1.5, "not": 4.0}

_cache: dict[str, tuple[Any, list[dict[str, Any]]]] = {}
_cache_lock = threading.Lock()


def _turkish(text: str, lang: str) -> bool:
    return lang == "tr" or any(c in text for c in "çğışöüÇĞİŞÖÜ")


_TAG = re.compile(r"(?:,|\s)\s*(?:okay|ok|right|all right|alright|yes|tamam mı|değil mi)\s*$", re.I)


def _asked_back(text: str, span: tuple[int, int]) -> bool:
    """The cue is in a question to the class ("What was the most important part?", "Is it important?"); not when
    the question is only a tag asking them to agree ("…one question is from here, okay?")."""
    rest = text[span[1]:]
    end = re.search(r"[.!?…]", rest)
    return bool(end) and end.group(0) == "?" and not _TAG.search(rest[:end.start()])


def _sentences(text: str, span: tuple[int, int], before: int = 0) -> str:
    """The sentence ``span`` is in, with the ``before`` sentences ahead of it."""
    cuts = [m.end() for m in re.finditer(r"[.!?…]", text[:span[0]])]
    end = re.search(r"[.!?…]", text[span[1]:])
    return text[cuts[-1 - before] if len(cuts) > before else 0:span[1] + end.end() if end else len(text)]


def _hint(text: str, low: Optional[str]) -> Optional[tuple[tuple[int, int], str]]:
    """A plain exam hint (_HINT_EN, _HINT_TR) and where it is, or None. ("If I ask you in the exam, what is …?" ends
    in the question it would ask: that is no question to the class.)"""
    m = _HINT_EN.search(text)
    if m and _EXAM_EN.search(_sentences(text, m.span(), 1)) and not _DENIED_EN.search(_sentences(text, m.span())) \
            and (m.group(0).lower().startswith(("if", "when")) or not _asked_back(text, m.span())):
        return m.span(), re.sub(r"\s+", " ", m.group(0).lower())
    m = _HINT_TR.search(low) if low is not None else None
    if m and not _DENIED_TR.search(_sentences(low, m.span())) and not _asked_back(low, m.span()):
        return m.span(), "soru … sınav"
    return None


def cue(text: str, lang: str = "en") -> Optional[dict[str, Any]]:
    """The cue a paragraph has, or None: {"kind": "exam" | "stressed" | "not_needed", "span", "strength": "ask" |
    "about" | "strong" | "weak" | "not", "family": the cue as said ("be careful")}. None for a paragraph that is only
    about when, where or how an exam is held or work is handed in (the Heads-up's), and for a cue asked back as a
    question."""
    if not text:
        return None
    low = _tr_lower(text) if _turkish(text, lang) else None

    def find(en: re.Pattern, tr: re.Pattern) -> Optional[tuple[tuple[int, int], str]]:
        m = en.search(text) or (tr.search(low) if low is not None else None)
        return (m.span(), re.sub(r"\s+", " ", m.group(0).lower())) if m else None

    ask = find(_ASK_EN, _ASK_TR) or (find(_ASK_BARE_EN, _ASK_BARE_TR) if find(_ABOUT_EN, _ABOUT_TR) else None)
    no = find(_NOT_EN, _NOT_TR)
    if not ask and not no:
        ask = _hint(text, low)
    if no and not ask:
        return {"kind": "not_needed", "span": no[0], "strength": "not", "family": no[1]}
    if ask:
        if _asked_back(text, ask[0]) and find(_LOGISTICS_EN, _LOGISTICS_TR):
            return None
        return {"kind": "exam", "span": ask[0], "strength": "ask", "family": ask[1]}
    if find(_LOGISTICS_EN, _LOGISTICS_TR):
        return None
    text = _SOFT_EN.sub(lambda m: " " * len(m.group(0)), text)       # (find reads these from here on)
    low = _SOFT_TR.sub(lambda m: " " * len(m.group(0)), low) if low is not None else None
    for kind, strength, en, tr in (("exam", "about", _ABOUT_EN, _ABOUT_TR), ("stressed", "strong", _STRONG_EN, _STRONG_TR),
                                   ("stressed", "weak", _WEAK_EN, _WEAK_TR)):
        hit = find(en, tr)
        if not hit or _asked_back(text, hit[0]):
            continue
        if strength == "about" and (not find(_TODO_EN, _TODO_TR) or find(_GRADING_EN, _GRADING_TR)):
            continue                                       # only named, or how it is graded
        return {"kind": kind, "span": hit[0], "strength": strength, "family": hit[1]}
    return None


def logistics(text: str, lang: str = "en") -> bool:
    """When, where or how something is held, graded or handed in (the Heads-up's)."""
    low = _tr_lower(text) if _turkish(text, lang) else None
    return bool(_LOGISTICS_EN.search(text) or (low is not None and _LOGISTICS_TR.search(low)))


def classify(text: str, lang: str = "en") -> Optional[tuple[str, tuple[int, int]]]:
    """(kind, where its cue is) for a paragraph that says something matters (cue), or None."""
    c = cue(text, lang)
    return (c["kind"], c["span"]) if c else None


def _words(text: str) -> set[str]:
    return {w for w in (x.lower() for x in _WORD.findall(text or "")) if w not in _FILLER}


def same_point(a: set[str], b: set[str], same_day: bool = False) -> bool:
    """Two quotes saying the same thing: in one lecture, SAME_DAY_WORDS key words in common; else at least
    SAME_WORDS of them (Jaccard)."""
    if same_day:
        return len({w for w in a & b if len(w) >= 5}) >= SAME_DAY_WORDS
    if len(a) < 5 or len(b) < 5:
        return False
    return len(a & b) / len(a | b) >= SAME_WORDS


def _clean(text: str) -> str:
    text = re.sub(r"(?:\s*\[unclear\])+", " …", text or "")
    return re.sub(r"\s+", " ", text).strip()


def _quote(paras: list[Any], i: int, span: tuple[int, int]) -> tuple[str, list[int]]:
    """The quote of paragraph ``i`` around its cue, about QUOTE_CHARS long: a short paragraph ("Okay, this is very
    important.") with the paragraph it points at (the next one when the cue opens it, else the one before)."""
    from .alerts import _excerpt

    p = paras[i]
    text, (a, b) = p.text, span
    if len(text.split()) < SHORT_WORDS:
        if a < len(text) / 2 and i + 1 < len(paras):
            text = f"{text} {paras[i + 1].text}"
        elif i > 0:
            before = paras[i - 1].text
            before = before if len(before) <= 200 else "…" + before[-200:].split(" ", 1)[-1]
            a, b = a + len(before) + 1, b + len(before) + 1
            text = f"{before} {text}"
    quote, hl = _excerpt(text, (a, b), QUOTE_CHARS)
    clean = _clean(quote)
    if clean != quote:                               # "[unclear]" said as "…": find the cue again in the clean text
        cue_text = quote[hl[0]:hl[1]]
        k = clean.lower().find(cue_text.lower()) if cue_text else -1
        hl = [k, k + len(cue_text)] if k >= 0 else [0, 0]
    return clean, hl


def _signature(lib: Any, course_id: str, recs: list[Any]) -> Any:
    """What the list is found from: each transcript (by its file's time) and what of each recording counts."""
    from .alerts import _signature as alerts_signature

    sig, _ = alerts_signature(lib, course_id, recs, lib.transcribed_ids())
    return sig[:2]


def find(recs: list[Any], done: set[str]) -> list[dict[str, Any]]:
    """The stressed moments of these recordings, oldest first, repeats merged, each with a score: how much it
    stands out (a strong cue or one tied to an exam more than a weak one, and a weak one less the more often this
    lecturer says it; said again in this lecture or a later one: more) (``done``: the transcribed recordings)."""
    import math

    from .outputs.markdown import part_views

    by_day: dict[str, list[Any]] = {}
    for r in recs:
        by_day.setdefault(r.day_key, []).append(r)
    found: list[dict[str, Any]] = []
    lectures = 0
    for key in sorted(by_day, key=lambda k: min(r.start for r in by_day[k])):
        day_recs = by_day[key]
        ids = [r.delivery_id for r in day_recs if r.delivery_id in done]
        if not ids:
            continue
        lectures += 1
        for p in part_views(day_recs, store.load_transcripts(ids)):
            last: dict[str, float] = {}
            paras = p.paragraphs
            for i, para in enumerate(paras):
                words = para.text.replace("[unclear]", " ").split()
                if len(words) < 3 or (para.uncertain and len(words) < 8):
                    continue
                c = cue(para.text, para.lang)
                if c is None:
                    continue
                kind = c["kind"]
                if para.start - last.get(kind, -1e9) < SAME_POINT_S:      # still the same point
                    last[kind] = para.start
                    continue
                last[kind] = para.start
                quote, hl = _quote(paras, i, c["span"])
                if c["strength"] != "ask" and (len(_words(quote)) < LEAST_WORDS or logistics(quote, para.lang)):
                    continue                    # nothing said but the cue, or (with what it points at) logistics
                at = p.rec.start + dt.timedelta(seconds=para.start)
                found.append({"id": "s" + hashlib.sha1(f"{key}|{p.index}|{round(para.start)}".encode()).hexdigest()[:10],
                              "kind": kind, "text": quote, "hl": hl, "lang": para.lang, "day_key": key,
                              "day": key.split("|")[1] if key.count("|") >= 2 else at.date().isoformat(),
                              "date": at.isoformat(timespec="minutes"), "part": p.index, "t": round(para.start, 1),
                              "ts": format_ts(para.start), "viewer_url": p.rec.viewer_url,
                              "delivery_id": p.rec.delivery_id, "again": [], "_strength": c["strength"],
                              "_family": c["family"]})
    said: dict[str, int] = {}
    for it in found:
        said[it["_family"]] = said.get(it["_family"], 0) + 1
    kept: list[dict[str, Any]] = []
    words: list[set[str]] = []
    for it in found:                                # said again, here or in a later lecture: one point
        w = _words(it["text"])
        twin = next((k for k, x in enumerate(words) if kept[k]["kind"] == it["kind"]
                     and same_point(w, x, kept[k]["day_key"] == it["day_key"])), None)
        base = _BASE[it["_strength"]]
        if it["_strength"] in ("weak", "strong", "about"):          # the lecturer's habit counts for less
            base /= 1 + math.log2(1 + said[it["_family"]] / max(1, lectures))
        if twin is None:
            it["_base"] = base
            kept.append(it)
            words.append(w)
        else:
            k = kept[twin]
            k["again"].append({x: it[x] for x in ("day_key", "day", "date", "part", "t", "ts")})
            k["_base"] = max(k["_base"], base)
            words[twin] = words[twin] | w
    for it in kept:
        it["n"] = 1 + len(it["again"])
        it["score"] = round(it.pop("_base") + 0.8 * min(3, it["n"] - 1), 2)
        it.pop("_strength")
        it.pop("_family")
    return kept


def course_items(lib: Any, course_id: str, recordings: Optional[Iterable[Any]] = None) -> list[dict[str, Any]]:
    """The course's stressed moments (find), kept in memory and in the library (meta "stressed:<course>") until a
    transcript or a recording changes."""
    recs = list(recordings if recordings is not None else lib.recordings(course_id))
    sig = _signature(lib, course_id, recs)
    with _cache_lock:
        hit = _cache.get(course_id)
        if hit and hit[0] == sig:
            return hit[1]
    key = hashlib.sha256(repr((VERSION, __version__, sig)).encode("utf-8")).hexdigest()
    items: Optional[list[dict[str, Any]]] = None
    try:
        saved = json.loads(lib.get_meta(f"stressed:{course_id}") or "null")
        if isinstance(saved, dict) and saved.get("key") == key:
            items = saved["items"]
    except Exception:  # noqa: BLE001 - only a shortcut: found again
        log.debug("kept stressed list unreadable", exc_info=True)
    if items is None:
        items = find(recs, lib.transcribed_ids())
        try:
            lib.set_meta(f"stressed:{course_id}", json.dumps({"key": key, "items": items}, ensure_ascii=False))
        except Exception:  # noqa: BLE001 - found again next time
            log.debug("stressed list not kept", exc_info=True)
    with _cache_lock:
        _cache[course_id] = (sig, items)
    return items


def week_of(weeks: list[dict[str, Any]], day: str) -> Optional[dict[str, Any]]:
    return next((w for w in weeks if w.get("start") and w["start"] <= day[:10] <= (w.get("end") or w["start"])), None)


def topic_of(w: dict[str, Any]) -> str:
    return w.get("topic_en") or w.get("topic_tr") or " · ".join(u["title"] for u in w.get("units") or []) or ""


def grouped(items: list[dict[str, Any]], weeks: list[dict[str, Any]]) -> dict[str, Any]:
    """The moments by week (each with its topic, newest week first; in each the order they were said, the
    TOP_PER_WEEK that stand out most marked "top"), what the lecturer said isn't needed apart, and counts. Each item
    gets its week number."""
    out_weeks: dict[Any, dict[str, Any]] = {}
    not_needed = []
    for it in items:
        w = week_of(weeks, it["day"])
        it = {**it, "week": w["no"] if w else None}
        if it["kind"] == "not_needed":
            not_needed.append(it)
            continue
        key = w["no"] if w else None
        g = out_weeks.setdefault(key, {"no": key, "topic": topic_of(w) if w else "", "start": w["start"] if w else "",
                                       "end": (w.get("end") or "") if w else "", "items": []})
        g["items"].append(it)
    for g in out_weeks.values():
        best = {it["id"] for it in sorted(g["items"], key=lambda x: -x.get("score", 1))[:TOP_PER_WEEK]
                if it.get("score", 1) >= TOP_SCORE}
        for it in g["items"]:
            it["top"] = it["id"] in best
        g["more"] = len(g["items"]) - len(best)
    order = sorted(out_weeks.values(), key=lambda g: (g["no"] is None, -(g["no"] or 0)))
    counts = {k: sum(1 for it in items if it["kind"] == k) for k in KINDS}
    return {"weeks": order, "not_needed": not_needed, "counts": counts, "total": counts["exam"] + counts["stressed"]}


def ranked(items: list[dict[str, Any]], weeks: Optional[set[int]] = None,
           week_nos: Optional[dict[str, Optional[int]]] = None) -> list[dict[str, Any]]:
    """The moments that matter most first (their score: tied to an exam, insisted on, said again), the newest first
    among equals (``weeks``: only those weeks' moments; ``week_nos``: each day's week)."""
    pick = [it for it in items if it["kind"] != "not_needed"
            and (not weeks or (week_nos or {}).get(it["day"]) in weeks)]
    pick.sort(key=lambda it: it["date"], reverse=True)                     # (sorted is stable: newest within a rank)
    return sorted(pick, key=lambda it: -it.get("score", 1))[:200]


def prompt_lines(items: list[dict[str, Any]], refs: Optional[dict[str, str]] = None, limit: int = 30,
                 not_needed: Iterable[dict[str, Any]] = ()) -> tuple[str, dict[str, dict[str, Any]]]:
    """The moments as lines for the AI ("S3 (exam, said twice) [L2 part 1 14:05] “…”") and what each S number
    is. ``refs``: a class day's reference in the material (day key → "L2"), else its date."""
    lines, by_ref = [], {}
    pick = sorted(items[:limit], key=lambda it: it["date"])
    for n, it in enumerate(pick, 1):
        ref = f"S{n}"
        by_ref[ref] = it
        where = f"{(refs or {}).get(it['day_key']) or it['day']} part {it['part']} {it['ts']}"
        said = f", said {it['n']} times" if it.get("n", 1) > 1 else ""
        kind = "tied to an exam" if it["kind"] == "exam" else "stressed"
        lines.append(f"{ref} ({kind}{said}) [{where}] “{it['text']}”")
    for it in list(not_needed)[:8]:
        where = f"{(refs or {}).get(it['day_key']) or it['day']} part {it['part']} {it['ts']}"
        lines.append(f"- NOT NEEDED (the lecturer said so) [{where}] “{it['text']}”")
    return "\n".join(lines), by_ref


# ------------------------------------------------------------------ priorities of a study pack (studypack.py)
def for_pack(lib: Any, course_id: str, weeks: list[dict[str, Any]], nos: list[int],
             lectures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The stressed moments of a pack's weeks, most important first (at most 25), each with its lecture's reference
    in the pack's material ("L2", or its date when the pack doesn't read that lecture)."""
    try:
        items = course_items(lib, course_id)
    except Exception:  # noqa: BLE001 - a pack without priorities is still a pack
        log.debug("no stressed moments for the pack", exc_info=True)
        return []
    day_week = {day["date"]: w["no"] for w in weeks for day in w["days"]}
    refs = {x["day_key"]: x["ref"] for x in lectures}
    return [{"ref": refs.get(it["day_key"], it["day"]), "part": it["part"], "t": it["t"], "ts": it["ts"],
             "kind": it["kind"], "n": it.get("n", 1), "text": it["text"]}
            for it in ranked(items, set(nos), day_week)[:25]]


def _pack_line(m: dict[str, Any]) -> str:
    said = f", said {m['n']} times" if m.get("n", 1) > 1 else ""
    return (f"- ({'tied to an exam' if m['kind'] == 'exam' else 'stressed'}{said}) [{m['ref']} part {m['part']} "
            f"{m['ts']}] “{m['text']}”")


def pack_block(facts: dict[str, Any]) -> str:
    """The outline's part about what the lecturer stressed ("" when nothing was)."""
    moments = facts.get("stressed") or []
    if not moments:
        return ""
    return ("\n=== WHAT THE LECTURER STRESSED IN CLASS (quotes; teach each of these with extra care, as a key idea, "
            "a worked example or a common mistake where it fits) ===\n" + "\n".join(map(_pack_line, moments)) + "\n")


def chapter_block(facts: dict[str, Any], ch: dict[str, Any]) -> str:
    """For one chapter: the stressed moments said in the lecture passages it is taught from ("" when none)."""
    picked = []
    for m in facts.get("stressed") or []:
        for s in ch.get("sources", {}).get("lectures") or []:
            if s["ref"] == m["ref"] and (not s.get("part") or s["part"] == m["part"]) and \
                    (s.get("from") or 0) - 60 <= m["t"] <= (s["to"] if s.get("to") is not None else 1e9) + 60:
                picked.append(m)
                break
    if not picked:
        return ""
    return ("\nThe lecturer stressed these in this chapter's lectures: give each a key box, a worked example or a "
            "common-mistake box:\n" + "\n".join(map(_pack_line, picked)))


# ------------------------------------------------------------------ "Know this for the exam" (AI, on request)
SYSTEM = ("You help a university student prepare for an exam from what their lecturer stressed in class. You use only "
          "the moments you are given, cite them by their S numbers, and answer with the JSON asked for.")


def summary_prompt(course: dict[str, Any], lines: str, language: str, focus: str = "") -> str:
    return f"""TASK: The lecturer of {course['code']} ({course['name']}) stressed the moments below in class (quotes from automatic lecture transcripts: names and symbols may be misheard). Turn them into a short ranked list "Know this for the exam": what the student must know, most important first.
{focus}
Answer with one JSON object:
{{"items": [{{"point": "what to know, as one clear sentence or two (the formula, rule, method or warning itself)", "why": "a few words on why it ranks here (tied to an exam, said several times, a common mistake…)", "cites": ["S3", "S7"]}}]}}
Rules:
- 4 to 12 points. Merge moments about the same thing into one point that cites all of them.
- Rank: what the lecturer tied to an exam first, then what was said several times, then the rest.
- Every point cites at least one S number, only from the list. Never add anything the moments don't say.
- Write in {language} (the language this course is taught in). Maths in LaTeX ($...$).
- Leave out moments that are only about organisation, and say nothing about the NOT NEEDED ones except, if there are any, one last point listing what the lecturer said is not needed (it cites them too if they have S numbers; else no cites).

=== THE MOMENTS ===
{lines}"""


def clean_summary(raw: Any, by_ref: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """The AI's points, checked: each with a point and at least one known S number (or the closing "not needed"
    point), at most 12; the citations as the moments' ids."""
    rows = raw.get("items") if isinstance(raw, dict) else raw if isinstance(raw, list) else None
    out = []
    for x in rows or []:
        if not isinstance(x, dict):
            continue
        point = " ".join(str(x.get("point") or x.get("text") or x.get("title") or "").split())[:600]
        cites = x.get("cites") or x.get("citations") or x.get("sources") or []
        if isinstance(cites, str):
            cites = re.findall(r"S\d+", cites)
        ids = []
        for c in cites if isinstance(cites, list) else []:
            m = re.search(r"S\d+", str(c))
            if m and m.group(0) in by_ref and by_ref[m.group(0)]["id"] not in ids:
                ids.append(by_ref[m.group(0)]["id"])
        if not point or (not ids and not re.search(r"not needed|gerek yok|sorumlu değil", point, re.I)):
            continue
        out.append({"point": point, "why": " ".join(str(x.get("why") or "").split())[:200], "cites": ids})
    return out[:12]
