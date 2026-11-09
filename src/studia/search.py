"""Search: what a student types, matched the way they mean it, in everything IEUStudia keeps.

One index in the library (``search_doc``, and its FTS5 table ``search_fts`` over the words) holds the lectures'
transcripts (a row per paragraph, with the times its passages start, so a hit opens the transcript where the words
were said), the course files' pages and titles, the announcements, the weekly plan's topics, the AI study notes of
each class day and the chats. A word is kept folded: lower case, no accents, one kind of i ("SINAV", "Sınav" and
"sinav" are one word). Then:

- words are whole words: "exam" finds exam, exams and its Turkish forms, never "example";
- a word's forms come from its root at query time, among the words the index holds: English plurals (quizzes,
  probabilities) and Turkish endings with their vowel harmony (sınav: sınavı, sınavda, sınavlarında; olasılık:
  olasılığı, the k softens). Harmony also keeps "exam" from finding "examine" (its Turkish dative would be -ına);
- the last word is a prefix while it is typed ("theve" finds Thevenin), unless it is a word in its own right;
- a number is a number however it is written: "quiz one" finds Quiz 1, 1. quiz, Quiz-1, Q1, first quiz and birinci
  quiz, and only with the number next to its word ("one more thing about the quiz" is no "quiz 1");
- words that mean the same are found in both languages (midterm, ara sınav, vize; homework, assignment, ödev; some
  course terms: distribution and dağılım, probability and olasılık...);
- a longer word the index doesn't hold is taken for a typo of a near one (probabilty: probability), ranked lower,
  and the page offers the correction;
- "words in quotes" must stand together, in that order.

Rows with all the words come before rows with some, the words as typed before their other forms, synonyms and typos,
words close together before scattered ones, titles (a file's, an announcement's, a week's topics), announcements and
slide text before a passing mention in a lecture, and of two equal hits the newer. Results are grouped by what they
are (a class day with its best moments, a file with its best pages, ...), with counts per group and per course.

The page finds words in a transcript and filters lists with the same rules (``rules()``, match.js).
"""

from __future__ import annotations

import bisect
import datetime as dt
import functools
import logging
import math
import re
import sqlite3
import threading
import time
import unicodedata
from collections import defaultdict
from typing import Any, Callable, Iterable, Optional

log = logging.getLogger("studia")

INDEX_VERSION = "2"          # what the index holds and how its words are made: another value builds it again
                             # (2: formulas in notes and chats as plain signs, no Markdown tables' rulers)
KNOWN_WORDS = 60000          # the library's own words the page gets for its finding (match.js), at most
META_KEY = "search_index"
CANDIDATES = 1200            # rows looked at closely per query (the index's own ranking picks them)
NEWEST = 400                 # ... while only a letter or two is typed: the newest rows
KINDS = ("lecture", "file", "announcement", "week", "note", "chat")

# ------------------------------------------------------------------ words: folding and tokens
_WORD = re.compile(r"[^\W\d_]+|\d+")
_FOLD_I = str.maketrans({"İ": "i", "I": "i", "ı": "i"})
_VOWELS = frozenset("aeiou")
_APOS = ("'", "’", "ʼ", "`", "´")
# an abbreviation before a number is its word: Q1 is quiz 1, HW2 homework 2, Ch. 3 chapter 3
_ABBR = {"q": "quiz", "hw": "homework", "ch": "chapter", "eq": "equation", "fig": "figure", "thm": "theorem",
         "lec": "lecture", "wk": "week", "sec": "section"}
_ABBR_GAP = re.compile(r"[\s.#:(\-]{0,3}")
_ABBR_GAP_1 = re.compile(r"[#\-]?")          # a one-letter one only written on to its number: Q1, Q-1 (a "Q 0 1" is a signal)
_ORD_TAIL = frozenset({"st", "nd", "rd", "th", "inci", "nci", "uncu", "ncu"})     # 1st, 2nd, 1'inci, 3'üncü
_ORD_GAP = frozenset(("", *_APOS))


@functools.lru_cache(maxsize=65536)
def fold(word: str) -> str:
    """A word as the index keeps it: lower case, the Turkish i's one letter, no accents (ş s, ğ g, ö o...)."""
    low = word.translate(_FOLD_I).lower()
    if not low.isascii():
        low = "".join(c for c in unicodedata.normalize("NFKD", low) if not unicodedata.combining(c))
    if not low.isalnum():                    # what folding made of a ligature or a fraction: letters and digits only
        low = "".join(c for c in low if c.isalnum())
    return low


def prep(text: str) -> str:
    """The text as it is stored and searched (composed accents, so a word is one token however it was typed)."""
    return unicodedata.normalize("NFC", text or "")


def _camel(raw: str, start: int) -> Iterable[tuple[str, int]]:
    """A word that PDF text glued together ("ProbabilityWeek"): its parts. Only a lower-case run of 2 and more
    before a capital that starts a lower-case word is cut ("LaTeX", "iPhone", "McDonald" stay whole)."""
    prev = 0
    for i in range(2, len(raw) - 1):
        if raw[i].isupper() and raw[i - 1].islower() and raw[i - 2].islower() and raw[i + 1].islower():
            yield raw[prev:i], start + prev
            prev = i
    yield raw[prev:], start + prev


def tokens(text: str, upto: int = 0) -> list[tuple[str, int, int]]:
    """The words of a (prepped) text: (folded word, start, end). Letters and digits are separate words ("hw2"), a
    number loses its leading zeros ("Week01" is week 1) and the ending of an ordinal ("1st", "1'inci"), and an
    abbreviation before a number is its word ("Q1": quiz 1). ``upto``: stop after so many words (their places are
    all a snippet needs)."""
    out: list[tuple[str, int, int]] = []
    add = out.append
    for m in _WORD.finditer(text):
        raw = m.group()
        s = m.start()
        if raw[0].isdecimal():
            add(((raw.lstrip("0") or "0") if raw.isascii() else raw, s, s + len(raw)))
        elif raw.islower() or raw.istitle() or raw.isupper():   # (only a capital after small letters cuts a word)
            f = fold(raw)
            if f and not (f in _ORD_TAIL and out and out[-1][0].isdigit() and text[out[-1][2]:s] in _ORD_GAP):
                add((f, s, s + len(raw)))
        else:
            for piece, ps in _camel(raw, s):
                f = fold(piece)
                if f and not (f in _ORD_TAIL and out and out[-1][0].isdigit() and text[out[-1][2]:ps] in _ORD_GAP):
                    add((f, ps, ps + len(piece)))
        if upto and len(out) > upto:
            break
    for i in range(len(out) - 1):
        w, s, e = out[i]
        if w in _ABBR and out[i + 1][0].isdigit() and \
                (_ABBR_GAP_1 if len(w) == 1 else _ABBR_GAP).fullmatch(text, e, out[i + 1][1]):
            out[i] = (_ABBR[w], s, e)
    return out


def words(text: str) -> list[str]:
    return [w for w, _, _ in tokens(prep(text))]


# ------------------------------------------------------------------ word forms: English plurals, Turkish endings
# A Turkish ending is a chain of suffixes in this order, each optional: plural, possessive, case, -ki, "is"/person.
# In a template N is the narrow vowel (i after a, e, i; u after o, u: the folded ı/ü are i/u), W the wide one (a after
# a back vowel, e after a front one; either when folding hid which: i, o, u), D is d or t. What a template needs
# before it: V a vowel, C a consonant, 3 a third-person possessive (the n of "sınavında"), -3 anything else; it may
# set 3 (third person) or ki (a locative or genitive, which -ki can follow).
_SLOTS: tuple[tuple[tuple[str, str, str], ...], ...] = (
    (("lWr", "", ""),),
    (("Nm", "C", ""), ("m", "V", ""), ("Nn", "C", ""), ("n", "V", ""), ("N", "C", "3"), ("sN", "V", "3"),
     ("NmNz", "C", ""), ("mNz", "V", ""), ("NnNz", "C", ""), ("nNz", "V", ""), ("lWrN", "", "3")),
    (("N", "C-3", ""), ("yN", "V-3", ""), ("nN", "3", ""), ("W", "C-3", ""), ("yW", "V-3", ""), ("nW", "3", ""),
     ("DW", "-3", "ki"), ("nDW", "3", "ki"), ("DWn", "-3", ""), ("nDWn", "3", ""), ("Nn", "C-3", "ki"),
     ("nNn", "V", "ki"), ("lW", "C", ""), ("ylW", "V", ""), ("cW", "", "")),
    (("ki", "ki", ""),),
    (("DNr", "", ""), ("Nm", "C", ""), ("yNm", "V", ""), ("sNn", "", ""), ("Nz", "C", ""), ("yNz", "V", ""),
     ("lWr", "", "")),
)
_ENGLISH_ENDINGS = ("s", "es")
_ES_AFTER = ("s", "x", "z", "ch", "sh")          # boxes, matches, classes; not "bayes" (no "bay" + es)
_NAME_ENDINGS = ("ian", "ians", "ean", "eans")   # Bayes: Bayesian; Gauss: Gaussian; Laplace: Laplacian
NAME_Q = .8
_IRREGULAR = {"quizzes": "quiz", "matrices": "matrix", "vertices": "vertex", "indices": "index",
              "analyses": "analysis", "hypotheses": "hypothesis", "theses": "thesis", "criteria": "criterion",
              "phenomena": "phenomenon", "children": "child", "axes": "axis", "appendices": "appendix",
              "radii": "radius", "foci": "focus", "loci": "locus"}
_PLURALS: dict[str, list[str]] = defaultdict(list)
for _p, _s in _IRREGULAR.items():
    _PLURALS[_s].append(_p)
_SHORT_ROOT_ENDINGS = ("lar", "ler", "da", "de", "ta", "te")     # what a root of 3 letters takes: ara-da, ses-ler
_SOFT = {"p": "b", "t": "d", "k": "g"}           # kitap kitabı, olasılık olasılığı (ç c folds to c anyway)
_HARD = {v: k for k, v in _SOFT.items()}
MIN_ROOT = 3


def _state(word: str) -> tuple[Optional[bool], bool, bool]:
    """(back vowel? None when folding hid it, rounded?, ends in a vowel?) for the suffixes after ``word``."""
    last = next((c for c in reversed(word) if c in _VOWELS), "")
    return (True if last == "a" else False if last == "e" else None), last in "ou", bool(word) and word[-1] in _VOWELS


def _fits(need: str, vowel_end: bool, third: bool, ki: bool) -> bool:
    if need == "ki":
        return ki
    if need == "3":
        return third
    if need.endswith("-3") and third:
        return False
    if need.startswith("V") and not vowel_end:
        return False
    if need.startswith("C") and vowel_end:
        return False
    return True


def _expand(tpl: str, rest: str, back: Optional[bool], rnd: bool) -> list[tuple[int, Optional[bool], bool]]:
    """The ways ``tpl`` spells the start of ``rest``: (letters used, backness and roundness after it)."""
    ways = [(0, back, rnd)]
    for ch in tpl:
        nxt = []
        for i, b, r in ways:
            if i >= len(rest):
                continue
            c = rest[i]
            if ch == "N":
                if c == ("u" if r else "i"):
                    nxt.append((i + 1, b, r))
            elif ch == "W":
                if c in "ae" and (b is None or b == (c == "a")):
                    nxt.append((i + 1, c == "a", False))
            elif ch == "D":
                if c in "dt":
                    nxt.append((i + 1, b, r))
            elif c == ch:                       # a letter as written (the i of -ki is a front vowel)
                nxt.append((i + 1, False, False) if c in _VOWELS else (i + 1, b, r))
        ways = nxt
        if not ways:
            break
    return ways


@functools.lru_cache(maxsize=65536)
def _chain(back: Optional[bool], rnd: bool, vowel_end: bool, rest: str, slot: int = 0, third: bool = False,
           ki: bool = False) -> bool:
    if not rest:
        return True
    for s in range(slot, len(_SLOTS)):
        for tpl, need, sets in _SLOTS[s]:
            if not _fits(need, vowel_end, third, ki):
                continue
            for used, b, r in _expand(tpl, rest, back, rnd):
                if _chain(b, r, rest[used - 1] in _VOWELS, rest[used:], s + 1, sets == "3", sets == "ki"):
                    return True
    return False


def ending_ok(root: str, rest: str) -> bool:
    """Is ``rest`` an ending of ``root``: an English plural, or a chain of Turkish suffixes that fits its vowels?"""
    if not rest or rest == "s":
        return True
    if rest == "es":
        return root.endswith(_ES_AFTER)
    if len(root) <= MIN_ROOT and not rest.startswith(_SHORT_ROOT_ENDINGS):
        return False                   # a short root takes few endings: "the" + n is "then", "sin" + ce "since"
    back, rnd, vowel_end = _state(root)
    return _chain(back, rnd, vowel_end, rest)


@functools.lru_cache(maxsize=16384)
def roots(word: str) -> frozenset[str]:
    """The roots ``word`` may be a form of: itself, its English singular, and every start of it (3 letters or more,
    with a vowel) whose rest is a Turkish ending; a softened last consonant also as the dictionary writes it."""
    out = {word}
    if len(word) > MIN_ROOT and not word.isdigit():
        if word.endswith("ies") and len(word) > 4:
            out.add(word[:-3] + "y")
        if word.endswith("es") and word[:-2].endswith(_ES_AFTER):
            out.add(word[:-2])
        if word.endswith("s") and not word.endswith("ss"):
            out.add(word[:-1])
        if word in _IRREGULAR:
            out.add(_IRREGULAR[word])
        for k in range(MIN_ROOT, len(word)):
            head, rest = word[:k], word[k:]
            if rest in _VOWELS and len(head) <= MIN_ROOT:            # kısa is no form of "kıs" (kişi is one)
                continue
            if rest not in _ENGLISH_ENDINGS and any(c in _VOWELS for c in head) and ending_ok(head, rest):
                out.add(head)
    for r in list(out):
        if len(r) > MIN_ROOT and r[-1] in _HARD:
            out.add(r[:-1] + _HARD[r[-1]])
    return frozenset(out)


# ------------------------------------------------------------------ numbers, synonyms, common words
# a number word: (its value, how surely it means the number: "bir" is mostly "a", "one" often a pronoun)
_NUMBER_WORDS = {
    "zero": (0, .85), "one": (1, .85), "two": (2, .9), "three": (3, .9), "four": (4, .9), "five": (5, .9),
    "six": (6, .9), "seven": (7, .9), "eight": (8, .9), "nine": (9, .9), "ten": (10, .9), "eleven": (11, .9),
    "twelve": (12, .9), "thirteen": (13, .9), "fourteen": (14, .9), "fifteen": (15, .9), "sixteen": (16, .9),
    "twenty": (20, .9),
    "first": (1, .95), "second": (2, .9), "third": (3, .95), "fourth": (4, .95), "fifth": (5, .95),
    "sixth": (6, .95), "seventh": (7, .95), "eighth": (8, .95), "ninth": (9, .95), "tenth": (10, .95),
    "eleventh": (11, .95), "twelfth": (12, .95), "thirteenth": (13, .95), "fourteenth": (14, .95),
    "fifteenth": (15, .95), "sixteenth": (16, .95),
    "sıfır": (0, .85), "bir": (1, .6), "iki": (2, .85), "üç": (3, .85), "dört": (4, .85), "beş": (5, .8),
    "altı": (6, .6), "yedi": (7, .6), "sekiz": (8, .85), "dokuz": (9, .85),
    "birinci": (1, .95), "ilk": (1, .9), "ikinci": (2, .95), "üçüncü": (3, .95), "dördüncü": (4, .95),
    "beşinci": (5, .95), "altıncı": (6, .95), "yedinci": (7, .95), "sekizinci": (8, .95), "dokuzuncu": (9, .95),
    "onuncu": (10, .95),
    "ii": (2, .8), "iii": (3, .85), "iv": (4, .8),
}
NUMBER_WORDS = {fold(k): v for k, v in _NUMBER_WORDS.items()}
_NUMBER_FORMS: dict[int, dict[str, float]] = defaultdict(dict)
for _w, (_n, _q) in NUMBER_WORDS.items():
    _NUMBER_FORMS[_n][_w] = _q
# between a word and its number only these: "quiz 1, 2 and 3" numbers quiz three times
_NUMBER_JOINS = frozenset({"and", "or", "to", "ve", "veya", "ile", "ya", "da", "de", "through", "no", "number",
                           "numara"})
NEAR_NUMBER = 2              # a number counts for its word this many words before or after it

# words that mean the same (each a group; a member may be several words). What a student looks for most first
# (exams, homework, labs), then terms of courses taught in English to students who also say them in Turkish
SYNONYMS: tuple[tuple[str, ...], ...] = (
    ("exam", "examination", "sınav"),
    ("midterm", "mid term", "ara sınav", "arasınav", "vize"),
    ("final", "final exam", "final sınavı"),
    ("quiz", "kısa sınav", "küçük sınav"),
    ("homework", "home work", "assignment", "ödev"),
    ("project", "proje"),
    ("lab", "laboratory", "laboratuvar", "laboratuar"),
    ("lecture", "ders"),
    ("announcement", "duyuru"),
    ("solution", "çözüm"),
    ("week", "hafta"),
    ("deadline", "son teslim", "teslim tarihi"),
    ("grade", "puan"),
    ("slide", "slayt"),
    ("question", "soru"),
    ("answer", "cevap", "yanıt"),
    ("example", "örnek"),
    ("exercise", "alıştırma"),
    ("definition", "tanım"),
    ("theorem", "teorem"),
    ("proof", "ispat"),
    ("formula", "formül"),
    ("make up", "makeup", "mazeret sınavı", "telafi"),
    ("attendance", "yoklama"),
    ("lecturer", "instructor", "hoca"),
    ("probability", "olasılık"),
    ("distribution", "dağılım"),
    ("conditional", "koşullu"),
    ("random variable", "rastgele değişken", "rassal değişken"),
    ("expected value", "expectation", "beklenen değer"),
    ("variance", "varyans"),
    ("mean", "average", "ortalama"),
    ("standard deviation", "standart sapma"),
    ("independent", "bağımsız"),
    ("independence", "bağımsızlık"),
    ("event", "olay"),
    ("sample space", "örnek uzay", "örneklem uzayı"),
    ("matrix", "matris"),
    ("vector", "vektör"),
    ("eigenvalue", "özdeğer"),
    ("eigenvector", "özvektör"),
    ("linear", "doğrusal"),
    ("equation", "denklem"),
    ("circuit", "devre"),
    ("voltage", "gerilim", "voltaj"),
    ("current", "akım"),
    ("resistor", "resistance", "direnç"),
    ("capacitor", "kondansatör", "kapasitör"),
    ("inductor", "indüktör", "bobin"),
    ("power", "güç"),
    ("digital", "sayısal"),
    ("logic gate", "mantık kapısı"),
    ("function", "fonksiyon"),
    ("derivative", "türev"),
    ("graph", "grafik"),
)
_SYN_GROUPS: list[list[tuple[str, ...]]] = [[tuple(words(m)) for m in g] for g in SYNONYMS]
_SYN_FIRST: dict[str, list[tuple[int, tuple[str, ...]]]] = defaultdict(list)    # a member's first word: members
for _gi, _g in enumerate(_SYN_GROUPS):
    for _m in _g:
        _SYN_FIRST[_m[0]].append((_gi, _m))
SYNONYM_Q = .88              # a word of the same meaning, ranked a little below the word typed
FORM_Q = .92                 # another form of the word typed (exams for exam, sınavda for sınav)
PREFIX_Q = .7                # a word the last, unfinished word starts
TYPO_Q = {1: .55, 2: .45}    # a near spelling, by edits
FEW = 5                      # rows: a word being typed in fewer (and rare next to the words it starts) may be their start

STOP = frozenset(words(
    "the a an and or of to in on for with is are was were be been being it its this that these those what which who "
    "whom how why when where do does did done can could should would will shall may might must i you we they he she "
    "my your our their me us them about from by at as into than then there here not no yes if so but also just very "
    "more most some any all each every use used using get give tell explain show please mean means "
    "ve ile için mi mı mu mü da de ki ya ama gibi daha en çok olan nasıl neden hangi nedir nelerdir var yok ise veya "
    "hem her şey bu şu o ne"))

# words a question to the Ask AI says only to set the scene ("what did the lecturer say about the quiz?"): every
# lecture passage is the lecturer saying something, and "lecturer" finds "hoca", so the students' "Hocam" lines were
# sent instead of the quiz ones. Left out of its search (passages()) when the question asks for something else too
SCENE = frozenset(words("lecturer lecturers instructor instructors teacher teachers professor prof hoca hocam hocamız "
                        "say says said tell tells told talk talks talked mention mentions mentioned"))

# how much a row's kind and field count: a title or an announcement says more than a passing mention in a lecture
FIELD_WEIGHT = {("lecture", "body"): 1.0, ("file", "title"): 1.7, ("file", "body"): 1.2,
                ("announcement", "title"): 1.9, ("announcement", "body"): 1.35, ("week", "title"): 1.6,
                ("note", "body"): 1.05, ("chat", "title"): 1.3, ("chat", "body"): .9}


def rules() -> dict[str, Any]:
    """What match.js needs to match as this module does (sent to the page once)."""
    return {"version": INDEX_VERSION, "slots": [[list(t) for t in s] for s in _SLOTS], "abbr": _ABBR,
            "ordTail": sorted(_ORD_TAIL), "apos": list(_APOS), "irregular": _IRREGULAR, "esAfter": list(_ES_AFTER),
            "nameEndings": list(_NAME_ENDINGS), "soft": _SOFT, "shortRootEndings": list(_SHORT_ROOT_ENDINGS),
            "minRoot": MIN_ROOT, "numbers": {k: list(v) for k, v in NUMBER_WORDS.items()},
            "joins": sorted(_NUMBER_JOINS), "nearNumber": NEAR_NUMBER,
            "synonyms": [[list(m) for m in g] for g in _SYN_GROUPS], "stop": sorted(STOP), "few": FEW,
            "q": {"synonym": SYNONYM_Q, "form": FORM_Q, "prefix": PREFIX_Q, "typo1": TYPO_Q[1], "typo2": TYPO_Q[2],
                  "name": NAME_Q}}


# ------------------------------------------------------------------ typos
def distance(a: str, b: str, limit: int) -> int:
    """Edits from ``a`` to ``b`` (two letters swapped are one), or ``limit + 1`` when more than ``limit``."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev2: list[int] = []
    prev = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        best = i
        for j in range(1, len(b) + 1):
            v = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] != b[j - 1]))
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                v = min(v, prev2[j - 2] + 1)
            cur[j] = v
            best = min(best, v)
        if best > limit:
            return limit + 1
        prev2, prev = prev, cur
    return min(prev[-1], limit + 1)


class Vocab:
    """The words the index holds and in how many of its ``rows`` each is: their forms, prefixes and near spellings."""

    def __init__(self, df: dict[str, int], rows: int) -> None:
        self.df, self.rows = df, max(1, rows)
        self.terms = sorted(df)
        self.by_first: dict[str, list[str]] = defaultdict(list)
        for w in self.terms:
            if len(w) >= 4 and not w.isdigit():
                self.by_first[w[0]].append(w)
        self._family: dict[str, dict[str, float]] = {}

    def prefixed(self, p: str) -> list[str]:
        i = bisect.bisect_left(self.terms, p)
        j = i
        while j < len(self.terms) and self.terms[j].startswith(p):
            j += 1
        return self.terms[i:j]

    def family(self, word: str) -> dict[str, float]:
        """The index's words that are forms of ``word``: {word: 1.0 for itself, FORM_Q for another form}."""
        got = self._family.get(word)
        if got is not None:
            return got
        out: dict[str, float] = {}

        def add(v: str, q: float) -> None:
            if q > out.get(v, 0.0):
                out[v] = q

        if word in self.df:
            out[word] = 1.0
        if not word.isdigit() and len(word) >= 2:
            for r in roots(word):
                turkish = any(c in _VOWELS for c in r)         # a Turkish ending needs a root with a vowel ("th")
                for v in self.prefixed(r):
                    rest = v[len(r):]
                    if v != word and (ending_ok(r, rest) if turkish else rest in _ENGLISH_ENDINGS):
                        add(v, FORM_Q)
                if len(r) > MIN_ROOT and r[-1] in _SOFT:             # kitap: kitabı; olasılık: olasılığı
                    soft = r[:-1] + _SOFT[r[-1]]
                    for v in self.prefixed(soft):
                        rest = v[len(soft):]
                        if rest and rest[0] in _VOWELS and ending_ok(soft, rest):
                            add(v, FORM_Q)
                if r.endswith("y") and r[:-1] + "ies" in self.df:     # probability: probabilities
                    add(r[:-1] + "ies", FORM_Q)
                for p in _PLURALS.get(r, ()):
                    if p in self.df:
                        add(p, FORM_Q)
                if len(r) >= 4:                                     # a name's adjective: Bayesian, Laplacian
                    for stem in {r, r[:-1] if r.endswith("e") else r}:
                        for end in _NAME_ENDINGS:
                            if stem + end in self.df:
                                add(stem + end, NAME_Q)
        self._family[word] = out
        return out

    def near(self, word: str) -> list[tuple[int, str]]:
        """Words of the index a typo away from ``word`` (1 edit up to 7 letters, 2 from 8): [(edits, word)], the
        likeliest first. A word seen once is no correction (it is as likely a typo itself)."""
        limit = 1 if len(word) <= 7 else 2
        pool = set(self.by_first.get(word[0], ()))
        if len(word) > 1:                                          # the first two letters swapped
            pool.update(w for w in self.by_first.get(word[1], ()) if w[1:2] == word[0])
        found = []
        for w in pool:
            if w != word and abs(len(w) - len(word)) <= limit and self.df.get(w, 0) >= 2:
                d = distance(word, w, limit)
                if d <= limit:
                    found.append((d, -self.df[w], w))
        return [(d, w) for d, _, w in sorted(found)]


# ------------------------------------------------------------------ the query: concepts
class Alt:
    """One way a concept can be written: one word (any of ``seq[0]``) or several in a row; ``q``: how close it is to
    what was typed (1 = as typed)."""
    __slots__ = ("seq", "q")

    def __init__(self, seq: list[dict[str, float]], q: float = 1.0) -> None:
        self.seq, self.q = seq, q


class Concept:
    """A word of the query, words in quotes, a number, or the words of a group of synonyms."""
    __slots__ = ("typed", "alts", "number", "head", "stop", "weight", "fixed", "open", "phrase", "single", "multi")

    def __init__(self, typed: str, alts: list[Alt], number: bool = False, stop: bool = False, open_: bool = False,
                 phrase: bool = False) -> None:
        self.typed, self.alts, self.number, self.stop, self.open, self.phrase = typed, alts, number, stop, open_, phrase
        self.head = -1                       # a number: the concept it numbers ("quiz" in "quiz 1")
        self.weight = 1.0
        self.fixed = ""                      # a typo: the word it was taken for
        self.single: dict[str, float] = {}   # (prepare(): its one-word forms, and its forms of several words)
        self.multi: list[Alt] = []

    def terms(self, typed_only: bool = False) -> set[str]:
        return {w for a in self.alts if not typed_only or a.q >= 1 for pos in a.seq for w in pos}


_QUOTE_CHARS = "\"“”„«»"


def _pieces(text: str) -> list[tuple[bool, str, bool]]:
    """The query's parts: (in quotes, text, a quote still open)."""
    out, buf, i = [], [], 0
    while i < len(text):
        if text[i] in _QUOTE_CHARS:
            j = next((k for k in range(i + 1, len(text)) if text[k] in _QUOTE_CHARS), -1)
            out.append((False, "".join(buf), False))
            buf = []
            if j < 0:
                out.append((True, text[i + 1:], True))
                return out
            out.append((True, text[i + 1:j], False))
            i = j + 1
            continue
        buf.append(text[i])
        i += 1
    out.append((False, "".join(buf), False))
    return out


def _forms(vocab: Vocab, word: str) -> dict[str, float]:
    """A word's forms in the index; the word itself when the index has none (then no row has it)."""
    return vocab.family(word) or {word: 1.0}


def _number(word: str) -> Optional[int]:
    if word in NUMBER_WORDS:
        return NUMBER_WORDS[word][0]
    return int(word) if word.isdigit() and word.isascii() and len(word) < 7 else None


def _number_forms(n: int, typed: str) -> dict[str, float]:
    out = {str(n): 1.0, **_NUMBER_FORMS.get(n, {})}
    out[typed] = 1.0
    return out


def _synonym_at(ws: list[str], i: int) -> Optional[tuple[int, tuple[str, ...], int]]:
    """A group of synonyms one of whose members the query's words start with at ``i``: (group, member, words)."""
    best = None
    cands = list(_SYN_FIRST.get(ws[i], []))
    for r in sorted(roots(ws[i])):
        if r != ws[i]:
            cands += _SYN_FIRST.get(r, [])
    for gi, member in cands:
        n = len(member)
        if i + n <= len(ws) and all(m == w or m in roots(w) for m, w in zip(member, ws[i:i + n])):
            if best is None or n > best[2]:
                best = (gi, member, n)
    return best


def _prefix_forms(vocab: Vocab, w: str) -> dict[str, float]:
    forms = {v: PREFIX_Q for v in sorted(vocab.prefixed(w), key=lambda v: -vocab.df[v])[:40]}
    forms.update(vocab.family(w))
    return forms


def _typo(c: Concept, vocab: Vocab, word: str) -> None:
    """The near spellings of a word the index doesn't hold (or holds once or twice), added to its concept."""
    near = vocab.near(word)
    own = sum(vocab.df.get(v, 0) for a in c.alts for v in a.seq[0])
    if not near or vocab.df.get(near[0][1], 0) <= 3 * own:
        return
    c.fixed = near[0][1]
    for d, w in [x for x in near if x[0] == near[0][0]][:3]:       # the nearest spellings only
        c.alts.append(Alt([vocab.family(w) or {w: 1.0}], TYPO_Q[d]))


def parse(query: str, vocab: Vocab, typing: bool = True) -> list[Concept]:
    """The query's concepts. ``typing``: the last word may still be being typed (a prefix)."""
    text = prep(query)
    pieces = _pieces(text)
    trailing = typing and bool(text) and text[-1].isalnum()
    last = max((k for k, p in enumerate(pieces) if tokens(p[1])), default=-1)
    out: list[Concept] = []
    for k, (quoted, piece, still_open) in enumerate(pieces):
        ws = [w for w, _, _ in tokens(piece)]
        if not ws:
            continue
        open_last = trailing and k == last
        if quoted:
            seq = []
            for j, w in enumerate(ws):
                n = _number(w)
                if n is not None:
                    seq.append(_number_forms(n, w))
                elif j == len(ws) - 1 and still_open and open_last and w not in vocab.df:
                    seq.append(_prefix_forms(vocab, w) or {w: 1.0})
                else:
                    seq.append(_forms(vocab, w))
            out.append(Concept(piece.strip(), [Alt(seq)], phrase=True))
            continue
        first, i = len(out), 0

        def unfinished(w: str) -> bool:
            """Still being typed: what it may become ("theve": Thevenin). Also a word of its own that is rare next to
            the words it starts ("pro", once, before "probability": still being typed; "exam" is a word)."""
            if len(w) <= 2 or w not in vocab.df:
                return True
            fam = vocab.family(w)
            own = sum(vocab.df.get(v, 0) for v in fam)
            return own < FEW and own * 50 < sum(vocab.df[v] for v in vocab.prefixed(w) if v not in fam)

        while i < len(ws):
            w = ws[i]
            is_open = open_last and i == len(ws) - 1
            syn = _synonym_at(ws, i)                 # (whole words only: "exa" is no "exam" yet)
            if syn:
                gi, member, n = syn
                typed = tuple(ws[i:i + n])
                # (a member that is a common word, "mean", only as typed: "average" never finds "what does it mean")
                alts = [Alt([_forms(vocab, x) for x in m], 1.0 if m == member else SYNONYM_Q) for m in _SYN_GROUPS[gi]
                        if m == member or not all(x in STOP for x in m)]
                if typed != member:                                   # the forms typed, as typed
                    alts.insert(0, Alt([_forms(vocab, x) for x in typed], 1.0))
                grows = open_last and i + n == len(ws) and unfinished(typed[-1])
                if grows:
                    alts.append(Alt([_forms(vocab, x) for x in typed[:-1]] + [_prefix_forms(vocab, typed[-1])]))
                out.append(Concept(" ".join(typed), alts, open_=grows))
                i += n
                continue
            n = _number(w)
            if n is not None:
                out.append(Concept(w, [Alt([_number_forms(n, w)])], number=True))
            elif is_open and unfinished(w):
                c = Concept(w, [Alt([_prefix_forms(vocab, w) or {w: 1.0}])], open_=True)
                if len(w) >= 5 and not vocab.prefixed(w) and not vocab.family(w):
                    _typo(c, vocab, w)
                out.append(c)
            else:
                fam = vocab.family(w)
                c = Concept(w, [Alt([fam or {w: 1.0}])], stop=w in STOP)
                if not c.stop and len(w) >= 5 and sum(vocab.df.get(v, 0) for v in fam) <= 2:
                    _typo(c, vocab, w)
                if not fam and not c.fixed and len(w) >= 3:          # nothing at all: what it is the start of
                    c.alts.append(Alt([{v: q for v, q in _prefix_forms(vocab, w).items()}]))
                out.append(c)
            i += 1
        for j in range(first, len(out)):                              # a number's word: the one before, or after
            if out[j].number:
                for h in (j - 1, j + 1, j - 2, j + 2):
                    if first <= h < len(out) and not out[h].number and not out[h].stop:
                        out[j].head = h
                        break
    real = [c for c in out if not c.stop]
    for c in out:
        if c.stop and real:
            c.weight = .15
            continue
        # as rare as its words are together, its synonyms too: "lecturer" is rare, but it finds "hoca" (and the
        # students' "Hocam") everywhere, and weighed as the rare word it outranked the word that mattered ("quiz")
        df = max((vocab.df.get(w, 0) for a in c.alts if a.q >= SYNONYM_Q for pos in a.seq for w in pos), default=0)
        c.weight = math.log(1 + vocab.rows / (1 + df)) + .2
        if c.number and c.head >= 0:
            c.weight *= .6                   # a number says which of its word's: the word says what
    return out


def fixed_query(query: str, concepts: list[Concept]) -> str:
    """The query with its typos corrected ("" when it has none)."""
    fixes = {c.typed: c.fixed for c in concepts if c.fixed}
    if not fixes:
        return ""
    text = prep(query)
    out, pos = [], 0
    for w, s, e in tokens(text):
        if w in fixes:
            out.append(text[pos:s] + fixes[w])
            pos = e
    out.append(text[pos:])
    return "".join(out)


# ------------------------------------------------------------------ matching a row
def prepare(concepts: list[Concept]) -> frozenset[str]:
    """Ready the concepts for matching many rows: every word any of them can be (rows are only scanned for those)."""
    wanted: set[str] = set()
    for c in concepts:
        single: dict[str, float] = {}
        for a in c.alts:
            if len(a.seq) == 1:
                for w, q in a.seq[0].items():
                    if q * a.q > single.get(w, 0.0):
                        single[w] = q * a.q
            for pos in a.seq:
                wanted.update(pos)
        c.single, c.multi = single, [a for a in c.alts if len(a.seq) > 1]
    return frozenset(wanted)


def _occurrences(concepts: list[Concept], toks: list[str], wanted: frozenset[str]) -> list[list[tuple[int, int, float]]]:
    """Where each concept is in a row's words: [(start, end, quality)] per concept, best first."""
    pos: dict[str, list[int]] = defaultdict(list)
    for i, w in enumerate(toks):
        if w in wanted:
            pos[w].append(i)
    out: list[list[tuple[int, int, float]]] = []
    for c in concepts:
        occ: dict[int, tuple[int, int, float]] = {}
        for w, ps in pos.items():
            q = c.single.get(w)
            if q:
                for p in ps:
                    if p not in occ or occ[p][2] < q:
                        occ[p] = (p, p + 1, q)
        for a in c.multi:
            for w, q in a.seq[0].items():
                for p in pos.get(w, ()):
                    qq, end = q, p + 1
                    for pq in a.seq[1:]:
                        if end >= len(toks) or toks[end] not in pq:
                            qq = 0.0
                            break
                        qq = min(qq, pq[toks[end]])
                        end += 1
                    if qq:
                        qq *= a.q
                        if p not in occ or occ[p][2] < qq:
                            occ[p] = (p, end, qq)
        out.append(sorted(occ.values(), key=lambda o: -o[2]))
    for j, c in enumerate(concepts):          # a number counts only next to its word ("quiz 1", "1. quiz")
        if c.number and c.head >= 0 and out[j]:
            heads = out[c.head]
            out[j] = [o for o in out[j] if any(_numbers(toks, h, o) for h in heads)]
            if out[j]:                        # ... and then the word only where it has that number ("Quiz 2" isn't)
                out[c.head] = [h for h in heads if any(_numbers(toks, h, o) for o in out[j])]
    return out


def _numbers(toks: list[str], h: tuple[int, int, float], o: tuple[int, int, float]) -> bool:
    """Is the number at ``o`` one of the word at ``h``'s: right before or after it, or after it in a list of numbers.
    One before it doesn't count when the word has a number right after it ("1. Quiz 2" is quiz 2)."""
    if h[0] - NEAR_NUMBER <= o[0] < h[0]:
        after = toks[h[1]] if h[1] < len(toks) else ""
        return not (after.isdigit() or after in NUMBER_WORDS)
    if h[1] <= o[0] < h[1] + NEAR_NUMBER:
        return True
    if o[0] >= h[1]:
        return all(t.isdigit() or t in _NUMBER_JOINS or t in NUMBER_WORDS for t in toks[h[1]:o[0]])
    return False


def _window(occ: list[list[tuple[int, int, float]]]) -> tuple[int, int]:
    """The shortest run of words with one occurrence of every concept found: (start, end)."""
    lists = [o for o in occ if o]
    if not lists:
        return 0, 0
    if len(lists) == 1:
        o = lists[0][0]
        return o[0], o[1]
    events = sorted((o[0], o[1], k) for k, lst in enumerate(lists) for o in lst[:40])
    need, have, best = len(lists), defaultdict(int), (0, 1 << 30)
    left = 0
    for right in range(len(events)):
        have[events[right][2]] += 1
        while len(have) == need:
            span = (events[left][0], max(events[left][1], events[right][1]))
            if span[1] - span[0] < best[1] - best[0]:
                best = span
            k = events[left][2]
            have[k] -= 1
            if not have[k]:
                del have[k]
            left += 1
    return best


def score_row(concepts: list[Concept], toks: list[str], kind: str, field: str,
              wanted: Optional[frozenset[str]] = None) -> tuple[float, list[list[tuple[int, int, float]]], bool]:
    """How well a row matches: (score, occurrences per concept, all words found?). ``wanted``: prepare()'s."""
    occ = _occurrences(concepts, toks, prepare(concepts) if wanted is None else wanted)
    real = [j for j, c in enumerate(concepts) if not c.stop] or list(range(len(concepts)))
    total = sum(concepts[j].weight for j in real)
    found = [j for j in real if occ[j]]
    if not found:
        return 0.0, occ, False
    got = sum(concepts[j].weight for j in found)
    cover = got / total
    quality = sum(concepts[j].weight * occ[j][0][2] for j in found) / got
    s = cover * cover * (.5 + .5 * quality)
    every = len(found) == len(real)
    if every:
        s *= 1.6
    if len(found) > 1:
        a, b = _window([occ[j] for j in found])
        extra = max(0, (b - a) - sum(occ[j][0][1] - occ[j][0][0] for j in found))
        s *= 1 + .6 / (1 + extra / 2)
    phrase_missing = any(concepts[j].phrase and not occ[j] for j in real)
    if phrase_missing:
        s *= .5
    n_occ = sum(len(occ[j]) for j in found)
    s *= 1 + .25 * math.log1p(max(0, n_occ - len(found)))
    s *= 1 + .05 * sum(1 for j, c in enumerate(concepts) if c.stop and occ[j] and j not in real)
    s /= 1 + .1 * math.log1p(len(toks) / 60)
    return s * FIELD_WEIGHT.get((kind, field), 1.0), occ, every


# ------------------------------------------------------------------ snippets
# what a PDF's text has that the page can only draw as an empty box: control codes (a formula's big brackets come out
# as \x14 \x15), a symbol font's private-use codes (a matrix's bracket pieces, bullets) and unassigned ones. Left out
# of a snippet; U+FFFE is the hyphen of such PDFs (product, U+FFFE, of-maxterms; ISBN, U+FFFE, 10)
_UNSHOWN = frozenset(("Cc", "Cn", "Co", "Cs"))


def snippet(text: str, occ: list[list[tuple[int, int, float]]], size: int = 240) -> dict[str, Any]:
    """The part of a (prepped) ``text`` around its best run of matching words, with the words to mark:
    {"text", "marks": [[start, end], ...], "at": where that run starts in ``text``}. Whitespace is made single, and
    what the page can't draw is left out (_UNSHOWN)."""
    last = max((e for lst in occ for _, e, _ in lst), default=0)
    toks = tokens(text, last + 1)
    spans = sorted({(toks[s][1], toks[e - 1][2]) for lst in occ for s, e, _ in lst if e - 1 < len(toks)})
    lo = hi = 0
    if spans:
        a, b = _window([lst for lst in occ if lst])
        if a < len(toks):
            lo, hi = toks[a][1], toks[min(b, len(toks)) - 1][2]
    hi = min(hi, lo + size)
    start = max(0, lo - max(0, size - (hi - lo)) // 3)
    end = min(len(text), max(hi, start + size))
    while start > 0 and text[start - 1].isalnum():                 # whole words at both ends
        start -= 1
    while end < len(text) and text[end].isalnum():
        end += 1
    out: list[str] = []
    where = [0] * (end - start + 1)          # each character's place in the snippet (whitespace made single)
    for i in range(start, end):
        ch = text[i]
        where[i - start] = len(out)
        if ch.isspace():
            if out and out[-1] != " ":
                out.append(" ")
        elif ord(ch) == 0xFFFE:
            out.append("-")
        elif unicodedata.category(ch) not in _UNSHOWN:
            out.append(ch)
    body = "".join(out).rstrip()
    lead, tail = ("…" if start > 0 else ""), ("…" if end < len(text) else "")
    marks = [[where[s - start] + len(lead), min(len(body), where[e - 1 - start] + 1) + len(lead)]
             for s, e in spans if s >= start and e <= end]
    out = lead + body + tail
    if any(ord(ch) > 0xFFFF for ch in out):      # the page counts in UTF-16 (a PDF's 𝑃 is two there): its places
        units = [0]
        for ch in out:
            units.append(units[-1] + (2 if ord(ch) > 0xFFFF else 1))
        marks = [[units[a], units[b]] for a, b in marks]
    return {"text": out, "marks": marks, "at": lo}


# ------------------------------------------------------------------ the index in the library
_DDL = ("CREATE TABLE IF NOT EXISTS search_doc(id INTEGER PRIMARY KEY, kind TEXT, field TEXT, ref TEXT, "
        "sub TEXT DEFAULT '', course TEXT DEFAULT '', at TEXT DEFAULT '', text TEXT DEFAULT '', terms TEXT DEFAULT '', "
        "extra TEXT DEFAULT '')",
        "CREATE INDEX IF NOT EXISTS ix_search_doc ON search_doc(kind, ref)")
_FTS = ("CREATE VIRTUAL TABLE IF NOT EXISTS search_fts USING fts5(terms, content='search_doc', content_rowid='id', "
        "tokenize='unicode61 remove_diacritics 0')",
        "CREATE VIRTUAL TABLE IF NOT EXISTS search_vocab USING fts5vocab(search_fts, 'row')")
Doc = tuple[str, str, str, str, str, str, str, str]      # kind, field, ref, sub, course, at, text, extra


def _paras(segments: Iterable[Any]) -> list[tuple[float, str, str]]:
    """A transcript's paragraphs (the day page's), as (start, text, where each passage starts: "offset:time;...")."""
    from .outputs.paragraphs import UNCLEAR, build_paragraphs

    segs = [s for s in segments if (s.text or "").strip() and s.text.strip() != UNCLEAR]
    paras = build_paragraphs(segs)
    out, k = [], 0
    for n, p in enumerate(paras):
        stop = paras[n + 1].start if n + 1 < len(paras) else float("inf")
        parts, marks, size = [], [], 0
        while k < len(segs) and segs[k].start < stop - 1e-6:
            piece = prep(segs[k].text.strip())
            if piece:
                marks.append(f"{size}:{segs[k].start:.1f}")
                parts.append(piece)
                size += len(piece) + 1
            k += 1
        if parts:
            out.append((p.start, " ".join(parts), ";".join(marks)))
    return out


def moment(extra: str, at: int, start: float) -> float:
    """When the words at character ``at`` of a lecture paragraph were said (its passages' starts in ``extra``)."""
    t = start
    for part in (extra or "").split(";"):
        off, _, sec = part.partition(":")
        try:
            if int(off) > at:
                break
            t = float(sec)
        except ValueError:
            continue
    return t


def _chunks(text: str, size: int = 1200) -> list[str]:
    """Long text (an AI answer, notes) in pieces at paragraph ends, for snippets that are about one thing."""
    out, cur = [], ""
    for para in re.split(r"\n\s*\n", text or ""):
        para = para.strip()
        if not para:
            continue
        if cur and len(cur) + len(para) > size:
            out.append(cur)
            cur = ""
        cur = f"{cur}\n\n{para}" if cur else para
        while len(cur) > size * 2:
            out.append(cur[:size])
            cur = cur[size:]
    if cur:
        out.append(cur)
    return out


_MD = re.compile(r"(?m)^\s{0,3}(?:#{1,6}\s+|[-*+]\s+|>\s?)|\*\*|__|`")
_ACTIONS = re.compile(r"```ieustudia\s*[\s\S]*?(?:```|$)")    # the buttons an answer suggests (chatactions.js): no text
_MATH = re.compile(r"\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\]|\\\(([\s\S]+?)\\\)|(?<![\\$\w])\$(?!\s)([^$\n]+?)(?<!\s)\$(?!\w)")
_RULER = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?\s*$")     # a Markdown table's |---|---:|
_TEX_SIGNS = {"times": "×", "cdot": "·", "div": "÷", "pm": "±", "mp": "∓", "mid": "|", "leq": "≤", "le": "≤",
              "geq": "≥", "ge": "≥", "neq": "≠", "ne": "≠", "approx": "≈", "equiv": "≡", "infty": "∞", "to": "→",
              "rightarrow": "→", "Rightarrow": "⇒", "leftarrow": "←", "sum": "Σ", "prod": "Π", "int": "∫", "sqrt": "√",
              "cap": "∩", "cup": "∪", "in": "∈", "notin": "∉", "subset": "⊂", "subseteq": "⊆", "emptyset": "∅",
              "forall": "∀", "exists": "∃", "neg": "¬", "land": "∧", "lor": "∨", "oplus": "⊕", "angle": "∠",
              "degree": "°", "circ": "°", "partial": "∂", "nabla": "∇", "ldots": "…", "dots": "…", "cdots": "…",
              "alpha": "α", "beta": "β", "gamma": "γ", "Gamma": "Γ", "delta": "δ", "Delta": "Δ", "epsilon": "ε",
              "varepsilon": "ε", "zeta": "ζ", "eta": "η", "theta": "θ", "Theta": "Θ", "kappa": "κ", "lambda": "λ",
              "Lambda": "Λ", "mu": "μ", "nu": "ν", "xi": "ξ", "pi": "π", "Pi": "Π", "rho": "ρ", "sigma": "σ",
              "Sigma": "Σ", "tau": "τ", "phi": "φ", "varphi": "φ", "Phi": "Φ", "chi": "χ", "psi": "ψ", "Psi": "Ψ",
              "omega": "ω", "Omega": "Ω"}
_TEX_WORDS = re.compile(r"\\(?:text|mathrm|mathbf|mathit|mathsf|operatorname|textbf|textit|textrm|mbox)\s*\{([^{}]*)\}")
_TEX_FRAC = re.compile(r"\\[dt]?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}")
_TEX_CMD = re.compile(r"\\([A-Za-z]+)\*?|\\.")


def _plain_math(tex: str) -> str:
    """A formula as plain text, for the index and the snippets it shows: its signs as signs (\\times: ×, \\mid: |),
    \\frac{a}{b} as (a)/(b), \\text{…} as its words, layout commands (\\left, \\quad) dropped. So "times", "text"
    or "left" don't find a formula, and a word marked in a snippet never lands inside one ($…$ with a mark in it
    can't be drawn)."""
    s = _TEX_WORDS.sub(r" \1 ", tex)
    for _ in range(3):                                    # \frac{\frac{a}{b}}{c}: from the inside out
        s = _TEX_FRAC.sub(r"(\1)/(\2)", s)
    s = _TEX_CMD.sub(lambda m: _TEX_SIGNS.get(m.group(1) or "", " ") if m.group(1) else " ", s)
    return " ".join(s.replace("{", "").replace("}", "").split())


def _plain_md(text: str) -> str:
    """AI notes and answers as plain text: no Markdown marks or tables' rulers, formulas as their signs
    (_plain_math), no action blocks, no "Generated by" footer."""
    lines = [ln for ln in _ACTIONS.sub("", text or "").splitlines()
             if not ln.startswith("*Generated by") and ln.strip() != "---" and not _RULER.match(ln)]
    text = _MATH.sub(lambda m: " " + _plain_math(next(g for g in m.groups() if g is not None)) + " ", "\n".join(lines))
    text = re.sub(r"(?m)^\s*\|(.*)\|\s*$", lambda m: " · ".join(c.strip() for c in m.group(1).split("|")), text)
    return _MD.sub("", text)


class Index:
    """The search index of one library. The library calls the hooks when what it keeps changes; ``ensure`` builds the
    index once (the first start of a version with a new INDEX_VERSION, or a library from before the index)."""

    def __init__(self, lib: Any) -> None:
        self.lib = lib
        self.notes: Optional[Callable[[str], list[tuple[str, str, str, str]]]] = None   # day: [(kind, label, md, at)]
        self.note_days: Optional[Callable[[], list[str]]] = None
        self._changed = 0
        self._vocab: Optional[Vocab] = None
        self._vocab_at = -1
        self._build_lock = threading.Lock()
        self._built = threading.Event()
        with lib._lock:
            for sql in _DDL:
                lib.db.execute(sql)
            try:
                for sql in _FTS:
                    lib.db.execute(sql)
                self.fts = True
            except sqlite3.OperationalError:
                log.warning("SQLite FTS5 unavailable; search reads the whole index")
                self.fts = False
            row = lib.db.execute("SELECT value FROM meta WHERE key=?", (META_KEY,)).fetchone()
            if not row and not any(lib.db.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() for table in
                                   ("transcripts", "materials", "announcements", "chats", "units", "course_info")):
                # a new library: its index starts empty and the hooks keep it (nothing to build)
                lib.db.execute("INSERT INTO meta(key, value) VALUES(?, ?)", (META_KEY, INDEX_VERSION))
                row = (INDEX_VERSION,)
        if row and row[0] == INDEX_VERSION:
            self._built.set()

    # ---- writing
    def _drop(self, kind: str, ref: Optional[str] = None, refs: Optional[Iterable[str]] = None) -> None:
        db = self.lib.db
        if refs is not None:
            ids = list(refs)
            rows = []
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                rows += db.execute(f"SELECT id, terms FROM search_doc WHERE kind=? AND ref IN ({','.join('?' * len(chunk))})",
                                   (kind, *chunk)).fetchall()
        elif ref is None:
            rows = db.execute("SELECT id, terms FROM search_doc WHERE kind=?", (kind,)).fetchall()
        else:
            rows = db.execute("SELECT id, terms FROM search_doc WHERE kind=? AND ref=?", (kind, ref)).fetchall()
        if not rows:
            return
        if self.fts:
            db.executemany("INSERT INTO search_fts(search_fts, rowid, terms) VALUES('delete', ?, ?)",
                           [(r[0], r[1]) for r in rows])
        db.executemany("DELETE FROM search_doc WHERE id=?", [(r[0],) for r in rows])
        self._changed += 1

    def _add(self, docs: Iterable[Doc]) -> None:
        db = self.lib.db
        for kind, field, ref, sub, course, at, text, extra in docs:
            text = prep(text)
            terms = " ".join(w for w, _, _ in tokens(text))
            if not terms:
                continue
            cur = db.execute("INSERT INTO search_doc(kind, field, ref, sub, course, at, text, terms, extra) "
                             "VALUES(?,?,?,?,?,?,?,?,?)", (kind, field, ref, sub, course or "", at or "", text, terms,
                                                         extra or ""))
            if self.fts:
                db.execute("INSERT INTO search_fts(rowid, terms) VALUES(?, ?)", (cur.lastrowid, terms))
        self._changed += 1

    def _put(self, kind: str, ref: str, docs: list[Doc]) -> None:
        with self.lib._lock:
            self._drop(kind, ref)
            self._add(docs)

    # ---- what each source gives
    def lecture(self, delivery_id: str, transcript: Any = None) -> None:
        """A transcript's paragraphs (from ``transcript``, or its saved file)."""
        with self.lib._lock:
            rec = self.lib.db.execute("SELECT course_id, start FROM recordings WHERE delivery_id=?",
                                      (delivery_id,)).fetchone()
        segments = transcript.segments if transcript is not None else self._segments(delivery_id)
        if rec is None or not segments:
            with self.lib._lock:
                self._drop("lecture", delivery_id)
            return
        docs = [("lecture", "body", delivery_id, f"{start:.1f}", rec[0], rec[1] or "", text, marks)
                for start, text, marks in _paras(segments)]
        self._put("lecture", delivery_id, docs)

    def _segments(self, delivery_id: str) -> list[Any]:
        """A transcript's passages: from its file, else as the library keeps them (their text and start)."""
        from . import store
        from .domain import Segment

        t = store.load_transcript(delivery_id)
        if t is not None:
            return t.segments
        with self.lib._lock:
            rows = self.lib.db.execute("SELECT t, text FROM transcript_fts WHERE delivery_id=? ORDER BY t",
                                       (delivery_id,)).fetchall()
        return [Segment(float(a), float(rows[i + 1][0]) if i + 1 < len(rows) else float(a) + 3, b or "", "", 1.0)
                for i, (a, b) in enumerate(rows)]

    def lecture_moved(self, delivery_id: str, course_id: str, start: str) -> None:
        with self.lib._lock:
            self.lib.db.execute("UPDATE search_doc SET course=?, at=? WHERE kind='lecture' AND ref=?",
                                (course_id, start, delivery_id))

    def drop_lecture(self, delivery_id: str) -> None:
        with self.lib._lock:
            self._drop("lecture", delivery_id)

    def file(self, resource_id: str) -> None:
        """A course file: its title and the text of its pages."""
        db = self.lib.db
        m = db.execute("SELECT m.resource_id, m.course_id, m.title, m.file_name, m.first_seen, u.start_date "
                       "FROM materials m LEFT JOIN units u ON u.course_id=m.course_id AND u.unit_id=m.unit_id "
                       "WHERE m.resource_id=?", (resource_id,)).fetchone()
        if m is None:
            with self.lib._lock:
                self._drop("file", resource_id)
            return
        at = m[5] or (m[4] or "")[:10]
        title = m[2] or m[3] or ""
        docs: list[Doc] = [("file", "title", resource_id, "0", m[1], at, title, "")]
        name = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", m[3] or "")
        if name and words(name) != words(title):                    # its file name too ("Week01_Student.pdf")
            docs.append(("file", "title", resource_id, "name", m[1], at, name, ""))
        for page, text in db.execute("SELECT page, text FROM material_fts WHERE resource_id=? ORDER BY page",
                                     (resource_id,)):
            docs.append(("file", "body", resource_id, str(page), m[1], at, text, ""))
        self._put("file", resource_id, docs)

    def announcement(self, aid: str) -> None:
        a = self.lib.db.execute("SELECT id, course_id, title, posted_at, body, first_seen FROM announcements WHERE id=?",
                                (aid,)).fetchone()
        if a is None:
            with self.lib._lock:
                self._drop("announcement", aid)
            return
        from .alerts import _tidy

        at = a[3] or a[5] or ""
        self._put("announcement", aid, [("announcement", "title", aid, "", a[1], at, a[2] or "", ""),
                                        ("announcement", "body", aid, "", a[1], at, _tidy(a[4] or ""), "")])

    def weeks(self, course_id: str) -> None:
        """A course's weekly plan: each week's topics (the English syllabus, IEULearn's Turkish one, the units)."""
        from .materials import unit_week, week_starts

        lib = self.lib
        info = lib.course_info(course_id)
        ov, syl = info.get("overview") or {}, info.get("syllabus") or {}
        units = lib.units(course_id)
        first = week_starts(units)
        topics: dict[int, list[str]] = defaultdict(list)
        starts: dict[int, str] = {}
        for w in syl.get("weeks", []) or []:
            if isinstance(w, dict) and w.get("no"):
                topics[int(w["no"])].append(str(w.get("subject") or ""))
        for w in ov.get("weeks", []) or []:
            if isinstance(w, dict) and w.get("no"):
                topics[int(w["no"])].append(str(w.get("topic") or ""))
        for u in units:
            n = unit_week(u, first)
            if n:
                topics[n].append(u.get("title") or "")
                if u.get("start_date"):
                    starts.setdefault(n, u["start_date"])
        docs: list[Doc] = []
        for n in sorted(topics):
            seen, parts = set(), [f"Week {n}"]
            for x in topics[n]:
                x = " ".join(x.split())
                if x and fold(x) not in seen:
                    seen.add(fold(x))
                    parts.append(x)
            if len(parts) > 1:
                at = starts.get(n) or ((first + dt.timedelta(days=7 * (n - 1))).isoformat() if first else "")
                docs.append(("week", "title", course_id, str(n), course_id, at, " · ".join(parts), ""))
        self._put("week", course_id, docs)

    def chat(self, chat_id: str) -> None:
        db = self.lib.db
        c = db.execute("SELECT id, course_id, title, created_at FROM chats WHERE id=?", (chat_id,)).fetchone()
        if c is None:
            with self.lib._lock:
                self._drop("chat", chat_id)
            return
        docs: list[Doc] = []
        if c[2]:
            docs.append(("chat", "title", chat_id, "0", c[1], c[3] or "", c[2], ""))
        for seq, role, text, at in db.execute("SELECT seq, role, text, created_at FROM chat_messages WHERE chat_id=? "
                                              "AND status='done' ORDER BY seq", (chat_id,)):
            for k, piece in enumerate(_chunks(_plain_md(text))):     # (an answer's Markdown as plain text)
                docs.append(("chat", "body", chat_id, f"{seq}.{k}", c[1], at or c[3] or "", piece, role or ""))
        self._put("chat", chat_id, docs)

    def day_notes(self, day_key: str) -> None:
        """A class day's AI study notes (Services tells where they are: ``notes``)."""
        found = self.notes(day_key) if self.notes else []
        docs: list[Doc] = []
        course = day_key.split("|", 1)[0]
        for kind, label, md, at in found:
            for k, piece in enumerate(_chunks(_plain_md(md))):
                docs.append(("note", "body", day_key, f"{kind}.{k}", course, at, piece, label))
        self._put("note", day_key, docs)

    # ---- building it all
    @property
    def ready(self) -> bool:
        return self._built.is_set()

    def ensure(self, wait: float = 30.0) -> bool:
        """Build the index if it isn't (another thread building it: wait for that, up to ``wait`` seconds)."""
        if self._built.is_set():
            return True
        if self._build_lock.acquire(blocking=False):
            try:
                if not self._built.is_set():
                    self.rebuild()
            finally:
                self._build_lock.release()
            return True
        return self._built.wait(wait)

    def rebuild(self) -> None:
        """Everything again: each source written in its own short step, so the library stays usable meanwhile."""
        t0 = time.perf_counter()
        lib = self.lib
        with lib._lock:
            lib.db.execute("DELETE FROM search_doc")
            if self.fts:
                lib.db.execute("INSERT INTO search_fts(search_fts) VALUES('delete-all')")
            lib.db.execute("DELETE FROM meta WHERE key=?", (META_KEY,))
            lib.db.commit()
            ids = [r[0] for r in lib.db.execute(
                "SELECT t.delivery_id FROM transcripts t JOIN recordings r ON r.delivery_id=t.delivery_id")]
            mats = [r[0] for r in lib.db.execute("SELECT resource_id FROM materials")]
            anns = [r[0] for r in lib.db.execute("SELECT id FROM announcements")]
            courses = [r[0] for r in lib.db.execute("SELECT id FROM courses")]
            chats = [r[0] for r in lib.db.execute("SELECT id FROM chats")]
        steps: list[Callable[[], None]] = [functools.partial(self.lecture, i) for i in ids]
        steps += [functools.partial(self.file, i) for i in mats]
        steps += [functools.partial(self.announcement, i) for i in anns]
        steps += [functools.partial(self.weeks, i) for i in courses]
        steps += [functools.partial(self.chat, i) for i in chats]
        try:
            days = self.note_days() if self.note_days else []
        except Exception:  # noqa: BLE001 - notes are a nice-to-have in the index
            log.debug("Listing the AI notes for the search index failed", exc_info=True)
            days = []
        steps += [functools.partial(self.day_notes, d) for d in days]
        for n, step in enumerate(steps):
            try:
                step()
            except Exception:  # noqa: BLE001 - one bad source never keeps the rest out of the index
                log.exception("Indexing for search failed (%s)", getattr(step, "args", ""))
            if n % 25 == 24:
                with lib._lock:
                    lib.db.commit()
        with lib._lock:
            lib.db.execute("INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                           (META_KEY, INDEX_VERSION))
            lib.db.commit()
        self._built.set()
        log.info("Search index built in %.1f s (%d rows)", time.perf_counter() - t0, self.size())

    def size(self) -> int:
        with self.lib._lock:
            return self.lib.db.execute("SELECT COUNT(*) FROM search_doc").fetchone()[0]

    # ---- reading
    def vocab(self) -> Vocab:
        if self._vocab is None or self._vocab_at != self._changed:
            with self.lib._lock:
                at = self._changed
                if self.fts:
                    df = dict(self.lib.db.execute("SELECT term, doc FROM search_vocab"))
                else:
                    df = defaultdict(int)
                    for (terms,) in self.lib.db.execute("SELECT terms FROM search_doc"):
                        for w in set(terms.split()):
                            df[w] += 1
                rows = self.lib.db.execute("SELECT COUNT(*) FROM search_doc").fetchone()[0]
            self._vocab, self._vocab_at = Vocab(dict(df), rows), at
        return self._vocab

    def known(self, limit: int = KNOWN_WORDS) -> list[str]:
        """The words the index holds that are words in their own right, for the page's own finding (match.js): one
        lecture's paragraphs or one course's file names may not hold "exam", and there it must not be taken for the
        start of a longer word ("examples"). A word that is rare next to the words it starts is left out: it is still
        being typed (the owner's library: "pro" 16 rows beside 1,844 of probability, problem, process\u2026; "exam" 69
        beside 1,085 is a word)."""
        v = self.vocab()
        terms = v.terms
        cum = [0]
        for w in terms:
            cum.append(cum[-1] + v.df[w])
        out = []
        for i, w in enumerate(terms):
            if len(w) < 3 or w.isdigit():
                continue
            own = v.df[w]
            j = bisect.bisect_left(terms, w + "\uffff", i)               # terms[i:j]: the words it starts
            if own * 50 < cum[j] - cum[i] - own:
                continue
            out.append(w)
        if len(out) > limit:
            out = sorted(sorted(out, key=lambda w: -v.df[w])[:limit])
        return out

    def candidates(self, concepts: list[Concept], every: bool, course: str = "", kinds: Iterable[str] = (),
                   limit: int = CANDIDATES, newest: bool = False) -> list[tuple]:
        """Rows that have every concept (``every``) or any, best first by the index's own ranking (``newest``: the
        newest first): (id, kind, field, ref, sub, course, at, terms)."""
        need = [c for c in concepts if not c.stop] or concepts
        groups = []
        for c in need:
            alts = []
            for a in c.alts:
                parts = [" OR ".join(_q(w) for w in pos) for pos in a.seq if pos]
                if len(parts) == len(a.seq) and parts:
                    alts.append(" AND ".join(f"({p})" for p in parts))
            if alts:
                groups.append("(" + " OR ".join(f"({x})" for x in alts) + ")")
        if not groups:
            return []
        where, args = [], []
        if course:
            where.append("d.course=?")
            args.append(course)
        kinds = [k for k in kinds if k]
        if kinds:
            where.append(f"d.kind IN ({','.join('?' * len(kinds))})")
            args += kinds
        cols = "d.id, d.kind, d.field, d.ref, d.sub, d.course, d.at, d.terms"
        with self.lib._lock:
            if not self.fts:
                sql = f"SELECT {cols} FROM search_doc d" + (" WHERE " + " AND ".join(where) if where else "")
                return self.lib.db.execute(sql, args).fetchall()
            match = (" AND " if every else " OR ").join(groups)
            sql = (f"SELECT {cols} FROM search_fts f JOIN search_doc d ON d.id=f.rowid WHERE search_fts MATCH ?"
                   + "".join(" AND " + w for w in where) + (" ORDER BY d.at DESC" if newest else " ORDER BY f.rank")
                   + " LIMIT ?")
            try:
                return self.lib.db.execute(sql, [match, *args, limit]).fetchall()
            except sqlite3.OperationalError:
                log.debug("Search query failed: %s", match, exc_info=True)
                return []

    def docs(self, ids: Iterable[int]) -> dict[int, tuple[str, str]]:
        """The text and extra of rows: {id: (text, extra)}."""
        ids = list(ids)
        out: dict[int, tuple[str, str]] = {}
        with self.lib._lock:
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                for r in self.lib.db.execute(f"SELECT id, text, extra FROM search_doc WHERE id IN ({','.join('?' * len(chunk))})",
                                             chunk):
                    out[r[0]] = (r[1], r[2])
        return out


def _q(term: str) -> str:
    return '"' + term.replace('"', '""') + '"'


# ------------------------------------------------------------------ searching
class Hit:
    __slots__ = ("id", "kind", "field", "ref", "sub", "course", "at", "score", "occ", "every")

    def __init__(self, row: tuple, score: float, occ: list[list[tuple[int, int, float]]], every: bool) -> None:
        self.id, self.kind, self.field, self.ref, self.sub, self.course, self.at = row[:7]
        self.score, self.occ, self.every = score, occ, every


def find(index: Index, query: str, course: str = "", kinds: Iterable[str] = (), every_first: bool = True,
         typing: bool = True, limit: int = CANDIDATES,
         scene: frozenset[str] = frozenset()) -> tuple[list[Concept], list[Hit]]:
    """The rows that match ``query``, best first: (its concepts, hits). Rows with all the words are looked for
    first; when they are few, rows with some of them follow (lower)."""
    if not index.ensure():
        from .i18n import t

        raise RuntimeError(t("The search is still being prepared (the first start after an update). Try again in a "
                             "moment."))
    vocab = index.vocab()
    concepts = parse(query, vocab, typing)
    if not concepts:
        return concepts, []
    real = [c for c in concepts if not c.stop]
    quiet = [c for c in real if scene and set(words(c.typed)) <= scene]      # (``scene``: SCENE, for the Ask AI)
    if quiet and len(quiet) < len(real):
        for c in quiet:
            c.stop, c.weight = True, .15
    little = typed_little(concepts)                       # two letters typed: the newest, not a ranking of noise
    rows = index.candidates(concepts, True, course, kinds, NEWEST if little else limit, newest=little)
    seen = {r[0] for r in rows}
    if not little and (len([c for c in concepts if not c.stop]) > 1 or not every_first):
        if len(rows) < 40 or not every_first:
            rows += [r for r in index.candidates(concepts, False, course, kinds, limit // 2) if r[0] not in seen]
    wanted = prepare(concepts)
    days: dict[str, float] = {}
    stamps = [r[6] for r in rows if r[6]]
    lo = min(stamps) if stamps else ""
    span = (_days(lo, max(stamps)) if stamps else 0) or 1
    hits = []
    for r in rows:
        s, occ, every = score_row(concepts, r[7].split(), r[1], r[2], wanted)
        if s <= 0:
            continue
        if r[6] and lo:                                    # of two equal hits the newer
            d = days.get(r[6][:10])
            if d is None:
                d = days[r[6][:10]] = _days(lo, r[6])
            s *= 1 + .05 * d / span
        hits.append(Hit(r, s, occ, every))
    if little:
        hits.sort(key=lambda h: (h.at or "", h.score), reverse=True)
    else:
        hits.sort(key=lambda h: (h.every, h.score, h.at or ""), reverse=True)
    return concepts, hits


def typed_little(concepts: list[Concept]) -> bool:
    """Only a letter or two typed yet: most rows match, so the newest come first rather than a ranking of noise."""
    real = [c for c in concepts if not c.stop] or concepts
    return bool(real) and all(c.open and len(c.typed) <= 2 for c in real)


def _days(a: str, b: str) -> float:
    try:
        return max(0.0, (dt.date.fromisoformat(b[:10]) - dt.date.fromisoformat(a[:10])).days)
    except ValueError:
        return 0.0


# ------------------------------------------------------------------ results: what the page shows
GROUP_LIMIT = 8              # items per group (the page asks for more of one group)
MOMENTS = 3                  # a class day's best moments, a file's best pages shown (one each while all kinds are)
_MORE = {"lecture": .08, "file": .06, "announcement": 0.0, "week": 0.0, "note": .04, "chat": .04}


class Item:
    """One result: a class day, a file, an announcement, a week, a day's notes of one kind, or a chat."""
    __slots__ = ("kind", "key", "course", "score", "every", "hits", "at")

    def __init__(self, kind: str, key: str, course: str) -> None:
        self.kind, self.key, self.course = kind, key, course
        self.hits: list[Hit] = []
        self.score, self.every, self.at = 0.0, False, ""


def _group(lib: Any, hits: list[Hit], newest: bool = False) -> list[Item]:
    recs: dict[str, tuple] = {}
    ids = list({h.ref for h in hits if h.kind == "lecture"})
    with lib._lock:
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            for r in lib.db.execute("SELECT delivery_id, course_id, start, kind FROM recordings WHERE delivery_id IN "
                                    f"({','.join('?' * len(chunk))})", chunk):
                recs[r[0]] = r
    items: dict[tuple[str, str], Item] = {}
    for h in hits:
        if h.kind == "lecture":
            r = recs.get(h.ref)
            if r is None:
                continue
            key, course = f"{r[1]}|{(r[2] or '')[:10]}|{r[3]}", r[1]
        elif h.kind == "week":
            key, course = f"{h.ref}#{h.sub}", h.course
        elif h.kind == "note":
            key, course = f"{h.ref}#{h.sub.split('.')[0]}", h.course
        else:
            key, course = h.ref, h.course
        it = items.get((h.kind, key))
        if it is None:
            it = items[(h.kind, key)] = Item(h.kind, key, course)
        it.hits.append(h)
    out = []
    for it in items.values():
        it.hits.sort(key=lambda h: (h.every, h.score), reverse=True)
        best = it.hits[0]
        it.every = best.every
        n = len({(h.ref, h.sub) for h in it.hits if h.field == "body"})
        it.score = best.score * (1 + _MORE.get(it.kind, 0) * min(max(0, n - 1), 5))
        if it.kind in ("file", "announcement", "chat") and {h.field for h in it.hits} == {"title", "body"}:
            it.score *= 1.1                              # its title and its text both
        it.at = max((h.at or "" for h in it.hits), default="")
        out.append(it)
    out.sort(key=(lambda i: (i.at, i.score)) if newest else (lambda i: (i.every, i.score, i.at)), reverse=True)
    return out


def _best_pool(hits: list[Hit]) -> list[Hit]:
    """The hits to show and count "more" of: those with all the words when the best has them all."""
    return [h for h in hits if h.every] if hits and hits[0].every else hits


def _marked(text: str, occ: list[list[tuple[int, int, float]]], size: int = 240) -> dict[str, Any]:
    s = snippet(text, occ, size)
    return {"text": s["text"], "marks": s["marks"]}


def _context(lib: Any, items: list[Item]) -> dict[str, Any]:
    """What the shown items need from the library, each looked up once."""
    keys = defaultdict(list)
    for it in items:
        keys[it.kind].append(it.key)
    ctx: dict[str, Any] = {"courses": {c.id: c for c in lib.courses(True)},
                           "done": lib.transcribed_ids() if keys["lecture"] else set(),
                           "materials": lib.get_materials(keys["file"]) if keys["file"] else {}}
    with lib._lock:
        for kind, sql in (("announcement", "SELECT id, title, author, posted_at FROM announcements"),
                          ("chat", "SELECT id, title, updated_at FROM chats")):
            ids = keys[kind]
            ctx[kind] = {r[0]: r for r in lib.db.execute(f"{sql} WHERE id IN ({','.join('?' * len(ids))})", ids)} \
                if ids else {}
    return ctx


def _render(lib: Any, it: Item, docs: dict[int, tuple[str, str]], ctx: dict[str, Any]) -> dict[str, Any]:
    """An item as the page shows it (its snippets marked)."""
    from .i18n import t
    from .outputs.common import format_ts, kind_label

    c = ctx["courses"].get(it.course)
    base = {"kind": it.kind, "key": it.key, "course_id": it.course, "course_code": c.code if c else "",
            "every": it.every, "score": round(it.score, 4), "date": (it.at or "")[:10]}
    bodies = [h for h in it.hits if h.field == "body"]
    title_hit = next((h for h in it.hits if h.field == "title"), None)
    if it.kind == "lecture":
        cid, day, kind = (it.key.split("|") + ["", "", ""])[:3]
        recs = sorted(lib.day_recordings(it.key), key=lambda r: r.start)
        parts = {r.delivery_id: n for n, r in enumerate([r for r in recs if r.delivery_id in ctx["done"]], start=1)}
        first = recs[0] if recs else None
        pool = _best_pool(bodies)
        chosen = sorted(pool[:ctx["shown"]], key=lambda h: (parts.get(h.ref, 1), float(h.sub or 0)))   # lecture order
        moments = []
        for h in chosen:
            text, extra = docs.get(h.id, ("", ""))
            s = snippet(text, h.occ, 220)
            at = moment(extra, s["at"], float(h.sub or 0))
            moments.append({"delivery_id": h.ref, "t": at, "ts": format_ts(at), "part": parts.get(h.ref, 1),
                            "snippet": {"text": s["text"], "marks": s["marks"]}})
        return {**base, "day_key": it.key, "date": day, "time": f"{first.start:%H:%M}" if first else "",
                "kind_label": t(kind_label(kind)), "parts": len(parts), "moments": moments,
                "more": max(0, len(pool) - len(moments))}
    if it.kind == "file":
        m = ctx["materials"].get(it.key) or {}
        title_hit = next((h for h in it.hits if h.field == "title" and h.sub == "0"), None)
        title = title_hit and docs.get(title_hit.id)
        pool = _best_pool(bodies)
        pages = [{"page": int(h.sub or 0), "snippet": _marked(docs.get(h.id, ("", ""))[0], h.occ)}
                 for h in sorted(pool[:ctx["shown"]], key=lambda h: int(h.sub or 0))]       # in page order
        return {**base, "resource_id": it.key, "title": _marked(title[0], title_hit.occ, 400) if title else
                {"text": m.get("title") or m.get("file_name") or "", "marks": []},
                "ext": m.get("ext") or "", "downloaded": bool(m.get("local_path")), "pages": pages,
                "more": max(0, len({h.sub for h in pool}) - len(pages))}
    if it.kind == "announcement":
        a = ctx["announcement"].get(it.key) or ("", "", "", "")
        title = title_hit and docs.get(title_hit.id)
        body = bodies[0] if bodies else None
        return {**base, "id": it.key, "title": _marked(title[0], title_hit.occ, 400) if title else
                {"text": a[1] or "", "marks": []}, "author": a[2] or "", "posted_at": a[3] or "",
                "snippet": _marked(docs.get(body.id, ("", ""))[0], body.occ, 260) if body else None}
    if it.kind == "week":
        h = it.hits[0]
        s = _marked(docs.get(h.id, ("", ""))[0], h.occ, 300)
        lead = f"Week {h.sub} · "                  # (the title says which week)
        if s["text"].startswith(lead):
            n = len(lead)
            s = {"text": s["text"][n:], "marks": [[a - n, b - n] for a, b in s["marks"] if a >= n]}
        return {**base, "week": int(h.sub or 0), "snippet": s}
    if it.kind == "note":
        h = bodies[0]
        day_key, note_kind = it.key.split("#", 1)
        return {**base, "day_key": day_key, "note_kind": note_kind, "label": t(docs.get(h.id, ("", ""))[1] or "Study notes"),
                "date": day_key.split("|")[1] if "|" in day_key else base["date"],
                "snippet": _marked(docs.get(h.id, ("", ""))[0], h.occ)}
    # a chat
    row = ctx["chat"].get(it.key) or ("", "", "")
    title = title_hit and docs.get(title_hit.id)
    body = bodies[0] if bodies else None
    return {**base, "chat_id": it.key, "title": _marked(title[0], title_hit.occ, 300) if title else
            {"text": row[1] or t("New chat"), "marks": []}, "date": (row[2] or base["date"])[:10],
            "role": docs.get(body.id, ("", ""))[1] if body else "",
            "snippet": _marked(docs.get(body.id, ("", ""))[0], body.occ) if body else None}


def results(lib: Any, query: str, course: str = "", kind: str = "", limit: int = GROUP_LIMIT, typing: bool = True,
            quick: bool = False) -> dict[str, Any]:
    """What the search page shows for ``query``: the items of each kind (``limit`` each), filtered by course and kind,
    with the counts per kind and per course (each counted with the other filter), and a corrected query when a word
    looked like a typo."""
    t0 = time.perf_counter()
    concepts, hits = find(lib.index, query, typing=typing)
    if (course or kind) and len(hits) >= CANDIDATES // 2:        # the rows of the filter's own (others filled the cut)
        _, more = find(lib.index, query, course=course, kinds=[kind] if kind else (), typing=typing)
        have = {h.id for h in hits}
        hits += [h for h in more if h.id not in have]
    items = _group(lib, hits, typed_little(concepts))
    by_kind: dict[str, int] = defaultdict(int)
    by_course: dict[str, int] = defaultdict(int)
    for it in items:
        if not course or it.course == course:
            by_kind[it.kind] += 1
        if not kind or it.kind == kind:
            by_course[it.course] += 1
    shown = [it for it in items if (not course or it.course == course) and (not kind or it.kind == kind)]
    groups: dict[str, list[Item]] = {}
    for it in shown:
        groups.setdefault(it.kind, []).append(it)
    per = 3 if quick else limit
    pick = [it for g in groups.values() for it in g[:per]]
    need = [h.id for it in pick for h in it.hits[:MOMENTS + 2]]
    docs = lib.index.docs(need)
    ctx = _context(lib, pick)
    ctx["shown"] = MOMENTS if kind else 1                      # moments and pages per item
    courses = ctx["courses"]
    out_groups = []
    for k, g in sorted(groups.items(), key=lambda kv: (kv[1][0].every, kv[1][0].score), reverse=True):
        out_groups.append({"kind": k, "count": len(g), "items": [_render(lib, it, docs, ctx) for it in g[:per]]})
    top = []
    if quick:
        top = sorted((x for g in out_groups for x in g["items"]), key=lambda x: (x["every"], x["score"]), reverse=True)[:8]
    fixed = fixed_query(query, concepts)
    return {"query": query, "fixed": fixed, "total": len(shown), "counts": dict(by_kind),
            "courses": sorted(({"id": cid, "code": courses[cid].code if cid in courses else "", "n": n}
                               for cid, n in by_course.items()), key=lambda x: (-x["n"], x["code"])),
            "groups": out_groups, "top": top, "words": [c.typed for c in concepts if not c.stop],
            "took_ms": round((time.perf_counter() - t0) * 1000, 1)}


def overlap(question: str, texts: list[str]) -> list[float]:
    """How many of the question's words each text has, found as the search finds them (any form, numbers however
    written, words of the same meaning; common words left out): the number of words found, and a fraction for how
    close they are to what was asked. The offline model's passages and the notes sent with a question are picked
    by it."""
    toks = [[w for w, _, _ in tokens(prep(x))] for x in texts]
    df: dict[str, int] = defaultdict(int)
    for ts in toks:
        for w in set(ts):
            df[w] += 1
    concepts = parse(question, Vocab(dict(df), len(texts)), typing=False)
    real = [j for j, c in enumerate(concepts) if not c.stop]       # (a number's word is found by its place in all)
    if not real:
        return [0.0] * len(texts)
    wanted = prepare(concepts)
    out = []
    for ts in toks:
        occ = _occurrences(concepts, ts, wanted)
        found = [occ[j][0][2] for j in real if occ[j]]
        out.append(len(found) + .1 * sum(found) / len(real))
    return out


def passages(lib: Any, question: str, course: str, kinds: Iterable[str] = ("file", "lecture"),
             limit: int = 400) -> list[tuple[Hit, str, str]]:
    """Rows that match any of the question's words, best first, with their text: [(hit, text, extra)] (the Ask AI's
    search: the course files' pages and lecture paragraphs to send along with a question)."""
    concepts, hits = find(lib.index, question, course=course, kinds=kinds, every_first=False, typing=False, limit=limit,
                          scene=SCENE)
    if all(c.stop for c in concepts):                 # "what is the ... of": nothing to look for
        return []
    docs = lib.index.docs(h.id for h in hits[:limit])
    return [(h, *docs[h.id]) for h in hits[:limit] if h.id in docs]
