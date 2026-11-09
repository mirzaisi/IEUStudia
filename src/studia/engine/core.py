"""Building blocks of the code-switching transcription pipeline.

Why this exists: Whisper decodes with a single language token. When that token is wrong it
*translates* (English speech decoded as <|tr|> comes out as Turkish and vice versa), and its
language-ID head is unreliable on accented English (a Turkish lecturer's English often scores
"60-85 % Turkish"). We therefore:

1. cut the audio into short speech *units* at natural pauses (Silero VAD),
2. decode every unit in each candidate language with one shared encoder pass (batched),
3. keep, per unit, the hypothesis the model itself finds most likely (average log-probability),
   with small, explicit tie-breakers.

Measured on real IEU lectures this picks the right language where the LID head fails, and it is
cheap with large-v3-turbo because its decoder is tiny compared to the encoder.
"""

from __future__ import annotations

import contextlib
import dataclasses as dc
import math
import re
import threading
import time
import unicodedata
from typing import Callable, Iterable, Iterator, Optional, Sequence

import numpy as np

from ..cancel import check

SR = 16000


# --------------------------------------------------------------------------- speech units

@dc.dataclass
class Unit:
    start: int                      # sample index (inclusive)
    end: int                        # sample index (exclusive)

    @property
    def dur(self) -> float:
        return (self.end - self.start) / SR


def speech_units(
    speech: Sequence[dict],
    *,
    max_unit_s: float = 14.0,
    merge_gap_s: float = 0.6,
    min_unit_s: float = 1.6,
    tiny_merge_gap_s: float = 2.5,
) -> list[Unit]:
    """Merge VAD speech segments (``{"start","end"}`` in samples) into decoding units.

    Units break at pauses longer than ``merge_gap_s`` (language switches nearly always happen
    at a pause) and never exceed ``max_unit_s`` unless a tiny fragment has to be absorbed
    (Whisper hallucinates on sub-second clips, so those are merged into a neighbour).
    """
    segs = sorted(((int(s["start"]), int(s["end"])) for s in speech if s["end"] > s["start"]))
    units: list[Unit] = []
    for s, e in segs:
        if units:
            cur = units[-1]
            if (s - cur.end) / SR <= merge_gap_s and (e - cur.start) / SR <= max_unit_s:
                cur.end = max(cur.end, e)
                continue
        units.append(Unit(s, e))

    # absorb tiny units into the closer neighbour when the pause is short
    i = 0
    while i < len(units):
        u = units[i]
        if u.dur >= min_unit_s or len(units) == 1:
            i += 1
            continue
        gap_prev = (u.start - units[i - 1].end) / SR if i > 0 else math.inf
        gap_next = (units[i + 1].start - u.end) / SR if i + 1 < len(units) else math.inf
        limit = max_unit_s + 6.0
        if gap_prev <= gap_next and gap_prev <= tiny_merge_gap_s and (u.end - units[i - 1].start) / SR <= limit:
            units[i - 1].end = u.end
            del units[i]
            continue
        if gap_next <= tiny_merge_gap_s and (units[i + 1].end - u.start) / SR <= limit:
            units[i + 1].start = u.start
            del units[i]
            continue
        i += 1
    return units


# --------------------------------------------------------------------------- decoding

@dc.dataclass
class Decoded:
    lang: str
    text: str
    avg_logprob: float
    no_speech_prob: float
    compression_ratio: float
    n_tokens: int

    @property
    def conf(self) -> float:
        return float(math.exp(max(-5.0, min(0.0, self.avg_logprob))))


@dc.dataclass
class DecodeOptions:
    beam_size: int = 5
    patience: float = 1.0
    length_penalty: float = 1.0
    repetition_penalty: float = 1.0
    no_repeat_ngram_size: int = 0
    temperature: float = 0.0
    best_of: int = 1
    tokens_per_second: float = 14.0      # generous cap; stops runaway repetition early
    batch_size: int = 16


def compression_ratio(text: str) -> float:
    import zlib

    b = text.encode("utf-8")
    return len(b) / max(1, len(zlib.compress(b)))


class UnitDecoder:
    """Batched Whisper decoding of units with a per-item language token (CTranslate2 directly)."""

    def __init__(self, whisper_model) -> None:  # faster_whisper.WhisperModel
        from faster_whisper.audio import pad_or_trim
        from faster_whisper.tokenizer import Tokenizer
        from faster_whisper.transcribe import get_suppressed_tokens

        self.wm = whisper_model
        self.ct2 = whisper_model.model
        self._pad = pad_or_trim
        self._tok = {}
        self._Tokenizer = Tokenizer
        base = self.tokenizer("en")
        self.suppress = list(get_suppressed_tokens(base, [-1]))
        self.max_length = whisper_model.max_length

    def tokenizer(self, lang: str):
        if lang not in self._tok:
            self._tok[lang] = self._Tokenizer(self.wm.hf_tokenizer, self.ct2.is_multilingual,
                                              task="transcribe", language=lang)
        return self._tok[lang]

    def prompt(self, lang: str) -> list[int]:
        t = self.tokenizer(lang)
        prev = PROMPTS.get(lang)
        head = [t.sot_prev] + t.encode(" " + prev) if prev else []
        return head + [t.sot, t.language, t.transcribe, t.no_timestamps]

    def features(self, audio: np.ndarray, units: Sequence[Unit]) -> np.ndarray:
        fe = self.wm.feature_extractor
        return np.stack([self._pad(fe(audio[u.start:u.end])[..., :-1]) for u in units]).astype(np.float32)

    def encode(self, feats: np.ndarray):
        return self.wm.encode(feats)

    def generate(self, encoder_output, units: Sequence[Unit], langs: Sequence[str],
                 opts: DecodeOptions) -> list[Decoded]:
        prompts = [self.prompt(lang) for lang in langs]
        longest = max(u.dur for u in units)
        max_new = int(longest * opts.tokens_per_second) + 24
        max_length = min(self.max_length, len(prompts[0]) + max_new)
        kwargs = dict(
            max_length=max_length,
            suppress_blank=True,
            suppress_tokens=self.suppress,
            return_scores=True,
            return_no_speech_prob=True,
            repetition_penalty=opts.repetition_penalty,
            no_repeat_ngram_size=opts.no_repeat_ngram_size,
            length_penalty=opts.length_penalty,
        )
        if opts.temperature > 0:
            kwargs.update(beam_size=1, num_hypotheses=max(1, opts.best_of), sampling_topk=0,
                          sampling_temperature=opts.temperature)
        else:
            kwargs.update(beam_size=opts.beam_size, patience=opts.patience)
        results = self.ct2.generate(encoder_output, prompts, **kwargs)
        out: list[Decoded] = []
        for lang, res in zip(langs, results):
            ids = res.sequences_ids[0]
            n = len(ids)
            cum = res.scores[0] * (n ** opts.length_penalty)
            avg = cum / (n + 1)
            text = self.tokenizer(lang).decode(ids).strip()
            out.append(Decoded(lang, text, float(avg), float(res.no_speech_prob),
                               compression_ratio(text) if text else 0.0, n))
        return out

    def language_prior(self, encoder_output, langs: Sequence[str] = ("en", "tr")) -> list[Optional[dict[str, float]]]:
        """The language-ID head's probabilities of ``langs`` per passage, scaled to sum 1 (None: none of them)."""
        out: list[Optional[dict[str, float]]] = []
        for probs in self.ct2.detect_language(encoder_output):
            p = {tok.strip("<|>"): float(prob) for tok, prob in probs}
            total = sum(p.get(lang, 0.0) for lang in langs)
            out.append({lang: p.get(lang, 0.0) / total for lang in langs} if total > 0 else None)
        return out


# --------------------------------------------------------------------------- language choice

_TR_CHARS = set("çğıöşüâîûÇĞİÖŞÜÂÎÛ")
_TR_WORDS = set("""ve bir bu için ne da de çok var yok değil olarak şimdi evet tamam ama yani mı mi mu mü
gibi daha kadar sonra hani şey arkadaşlar hocam burada şu o ben sen biz siz onlar nasıl neden niye işte
hadi bakın bakalım bunu şunu onu bunun şunun burda orda diye ile ya veya hem böyle şöyle öyle olan oldu
olur olması gerekiyor lazım tane kere sonuç zaman aynı her hiç belki sadece iki üç dört beş altı yedi
sekiz dokuz on sıfır eksi artı çarpı bölü eşittir peki mesela aynen tabi tabii bence hepsi gelelim olsun
oluyor demek zaten yine bile sanki galiba herhalde acaba hemen biraz bazen""".split())
_EN_WORDS = set("""the and is of to a in that it we you this for on are with be as have so what if can not
right okay now let's here there then which do does will would should could our your they he she has had
were was an or but by from at about because just also these those how when where why one two three four
five six seven eight nine ten zero minus plus times equal equals over divided""".split())
_WORD = re.compile(r"[a-zçğıöşüâîû']+")
# Numbers said as words, and the words of a sum ("three over two", "x eşittir iki"): a matrix or a binary number read
# out repeats them (clean_text), and a few such words are speech, not noise (hallucination_score)
NUMBER_WORDS = {
    "en": frozenset("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
                    "sixteen seventeen eighteen nineteen twenty half plus minus times over equals".split()),
    "tr": frozenset("sıfır bir iki üç dört beş altı yedi sekiz dokuz on yirmi buçuk artı eksi çarpı bölü "
                    "eşittir".split()),
}
_NUMBERS_IN = {lang: frozenset(w.replace("ı", "i") for w in words) for lang, words in NUMBER_WORDS.items()}  # "SIFIR"


def tr_lower(s: str) -> str:
    """Turkish-aware lowercase (I→ı, İ→i) for Turkish text."""
    return s.replace("I", "ı").replace("İ", "i").lower()


def neutral_lower(s: str) -> str:
    """Lowercase that works for mixed EN/TR text: I→i (English), İ→i, keeps ı."""
    return s.replace("İ", "i").lower()


def text_language(text: str) -> Optional[str]:
    """Very small EN/TR text classifier used for tagging and tie-breaks (None = unsure)."""
    low = neutral_lower(text)
    words = _WORD.findall(low)
    if not words:
        return None
    tr = sum(w in _TR_WORDS for w in words) + 1.5 * sum(any(c in _TR_CHARS for c in w) for w in words)
    # q, w and x do not exist in Turkish; "th" is very rare
    en = sum(w in _EN_WORDS for w in words) + 0.7 * sum(bool(re.search(r"[qwx]|th", w)) for w in words)
    n = len(words)
    if tr >= max(1.0, 0.18 * n) and tr > 1.4 * en:
        return "tr"
    if en >= max(1.0, 0.15 * n) and en > 1.4 * tr:
        return "en"
    return None


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKC", neutral_lower(text))
    return re.sub(r"[^\w]+", " ", t).strip()


# Short punctuated "previous text" prompts: they make Whisper write proper sentences
# (capitalisation 59 % -> 98 % on a real lecture) without steering the content.
PROMPTS = {
    "en": "Hello everyone. Today, we will continue with the lecture.",
    "tr": "Merhaba arkadaşlar. Bugün derse devam ediyoruz.",
}


def is_prompt_echo(text: str, lang: str) -> bool:
    """Whisper sometimes repeats the prompt on silence; detect that."""
    n = _norm(text)
    p = _norm(PROMPTS.get(lang, ""))
    return bool(n) and bool(p) and (n == p or (len(n) > 12 and n in p))


STRONG_HALLUCINATIONS = {
    "altyazı m k", "altyazı mk", "altyazı", "altyazılar", "subtitles by the amara org community",
    "izlediğiniz için teşekkür ederim", "izlediğiniz için teşekkürler", "izlediğiniz için teşekkür ederiz",
    "abone olmayı unutmayın", "beğenmeyi ve abone olmayı unutmayın", "thanks for watching",
    "thank you for watching", "please subscribe", "like and subscribe",
    "bir sonraki videoda görüşmek üzere", "bir sonraki videoda görüşürüz",
}
WEAK_HALLUCINATIONS = {
    "you", "thank you", "thanks", "thank you very much", "thank you so much", "thanks for listening",
    "see you", "see you around", "see you next time", "we ll see you next time", "see you in the next video",
    "bye", "bye bye", "goodbye", "one two three", "one two three four", "subscribe",
    "teşekkürler", "teşekkür ederim", "çok teşekkür ederim", "görüşmek üzere", "hoşça kalın", "hoşçakalın",
    "i love you", "i love you too", "thank you everyone", "thank you all", "see you later", "good night",
    "so", "okay", "oh", "hmm", "uh", "um", "i", "the", "evet", "merhaba",
}
_ALLOWED_LETTERS = set("abcdefghijklmnopqrstuvwxyzçğıöşüâîû")


_WEAK_VOCAB = {w for p in WEAK_HALLUCINATIONS for w in p.split()} | {"very", "much", "all", "guys", "again"}
_STRONG_PATTERNS = re.compile(
    r"(sesli betimleme|betimlemesi|altyaz|abone ol|izlediğiniz için|amara|subtitles? by|transcribed by|"
    r"captions? by|www\.|\.com\b|\.org\b|kanalıma|subscribe to|thanks for watching)")


def foreign_script(text: str) -> bool:
    """True when the text uses letters outside English/Turkish (Vietnamese, Portuguese, ... hallucinations)."""
    letters = [c for c in neutral_lower(text) if c.isalpha()]
    odd = sum(1 for c in letters if c not in _ALLOWED_LETTERS)
    return odd >= 1 and odd > 0.04 * len(letters)


def _only_filler(n: str) -> bool:
    words = n.split()
    return bool(words) and all(w in _WEAK_VOCAB for w in words)


def _says_numbers(words: Sequence[str], lang: str) -> bool:
    """Digits, or numbers in words of the language decoded ("Three plus three.", "You must be zero.")."""
    return any(re.search(r"\d", w) or w.replace("ı", "i") in _NUMBERS_IN.get(lang, ()) for w in words)


def hallucination_score(d: Decoded, unit_dur: float) -> float:
    """0 = fine, 1 = certainly a hallucination."""
    n = _norm(d.text)
    if not n:
        return 1.0
    if n in STRONG_HALLUCINATIONS or is_prompt_echo(d.text, d.lang) or _STRONG_PATTERNS.search(neutral_lower(d.text)):
        return 1.0
    if foreign_script(d.text):
        return 0.95
    if d.no_speech_prob > 0.6 and d.avg_logprob < -0.8:
        return 0.9
    if (n in WEAK_HALLUCINATIONS or _only_filler(n)) and (d.conf < 0.62 or unit_dur < 1.2):
        return 0.8
    words = n.split()
    if d.conf < 0.45 and len(words) <= 4 and not _says_numbers(words, d.lang):
        return 0.76   # a few low-confidence words without numbers: almost always noise
    if d.compression_ratio > 2.8:
        return 0.8
    if d.compression_ratio > 2.4:
        return 0.5
    return 0.0


def choose(cands: Sequence[Decoded], unit_dur: float, dominant: str, prior: Optional[dict[str, float]] = None
           ) -> Decoded:
    """Pick the most plausible hypothesis of one unit among its language candidates."""
    def score(d: Decoded) -> float:
        s = d.avg_logprob
        if not d.text.strip():
            return -99.0
        s -= 2.0 * hallucination_score(d, unit_dur)
        if d.compression_ratio > 2.4:
            s -= 0.4
        if d.lang == dominant:
            s += 0.015            # tiny tie-break toward the lecture's main language
        if prior:                 # the language-ID head (large-v3-turbo only, see Engine._decode_units)
            s += 0.2 * (prior.get(d.lang, 0.0) - 0.5)
        return s

    best = max(cands, key=score)
    if len(cands) > 1:
        # Same words in both languages (numbers, "Pivot.", English the TR decode kept as English):
        # the language is the one the text is written in. Unsure: three or more words without a Turkish letter
        # are English (the main language alone tagged "Vectors, linearly independent." Turkish in a Turkish
        # lecture); a word or two ("Pivot.", "Peki.") or Turkish letters go to the lecture's main language.
        import difflib

        ref = _norm(best.text)
        same = [c for c in cands if difflib.SequenceMatcher(None, _norm(c.text), ref).ratio() >= 0.85]
        if len(same) > 1:
            guess = text_language(best.text)
            if not guess:
                english = len(ref.split()) >= 3 and not any(ch in _TR_CHARS for ch in best.text.lower())
                guess = "en" if english else dominant
            for c in same:
                if c.lang == guess:
                    return c
    return best


def neutral_text(text: str) -> bool:
    """Numbers, with single letters and names like C1 between them ("2 1 0 1 0 0", "C1, A1."): the words do not
    show a language. Words do ("Ne o?", "O." are Turkish), and so does a Turkish ending on a number ("1'i")."""
    words = _norm(text).split()
    return (any(ch.isdigit() for ch in text) and not re.search(r"\d['’][a-zçğıöşü]", text.lower())
            and all(len(w) <= 1 or any(ch.isdigit() for ch in w) for w in words))


def segment_language(d: Decoded) -> str:
    """Language tag for the output. Trust the decode language unless the text clearly disagrees; text that shows
    no language (neutral_text) is English."""
    if neutral_text(d.text):
        return "en"
    guess = text_language(d.text)
    if guess and guess != d.lang and len(_WORD.findall(tr_lower(d.text))) >= 4:
        return guess
    return d.lang


# --------------------------------------------------------------------------- text cleanup

_REPEAT = re.compile(r"\b(\w+(?:\W+\w+){0,6}?)(?:\W+\1\b){2,}", re.IGNORECASE)
_ANY_NUMBER_WORD = frozenset().union(*_NUMBERS_IN.values())


def _numbers_only(group: str) -> bool:
    """Digits, number words and single letters, nothing else ("1, 0, 0", "sıfır, bir", "x, y")."""
    words = re.findall(r"\w+", neutral_lower(group))
    return all(w.isdigit() or len(w) == 1 or w.replace("ı", "i") in _ANY_NUMBER_WORD for w in words)


def _collapse(m: re.Match) -> str:
    """Words said three times or more in a row are Whisper looping ("go go go go go" -> "go"). Numbers are not: a
    matrix or a binary number read out repeats them ("1, 0, 0, 0, 1"). A loop of numbers long enough to be made up
    compresses well, and hallucination_score drops it before this."""
    return m.group(0) if _numbers_only(m.group(1)) else m.group(1)


def clean_text(text: str) -> str:
    t = text.strip()
    t = _REPEAT.sub(_collapse, t)
    t = re.sub(r"\s+([,.!?;:])", r"\1", t)
    t = re.sub(r"\s{2,}", " ", t)
    return t.strip()


def run_batches(items: Sequence[int], batch_size: int, fn: Callable[[list[int]], None],
                cancel: Optional[threading.Event], on_progress: Callable[[int], None]) -> None:
    done = 0
    for i in range(0, len(items), batch_size):
        check(cancel)
        chunk = list(items[i:i + batch_size])
        fn(chunk)
        done += len(chunk)
        on_progress(done)


def sort_by_duration(units: Sequence[Unit], idx: Iterable[int]) -> list[int]:
    """Batch similar lengths together so generation finishes at similar steps."""
    return sorted(idx, key=lambda i: units[i].dur)


# --------------------------------------------------------------------------- sharing the processor
# The offline AI and the speech engine slow each other down badly on the processor: measured on a laptop, the AI wrote
# four times slower while a transcription ran there. While the AI writes on the processor (the student waits for it),
# a transcription on the processor waits between batches (it is background work).
_ai = threading.Condition()
_ai_writing = 0


@contextlib.contextmanager
def processor_for_ai() -> Iterator[None]:
    """Held while the offline AI writes on the processor."""
    global _ai_writing
    with _ai:
        _ai_writing += 1
    try:
        yield
    finally:
        with _ai:
            _ai_writing -= 1
            _ai.notify_all()


def ai_writing() -> bool:
    return _ai_writing > 0


def wait_for_ai(cancel: Optional[threading.Event]) -> float:
    """Wait while the offline AI writes on the processor (cancel works meanwhile); the seconds waited."""
    started = time.monotonic()
    with _ai:
        while _ai_writing:
            check(cancel)
            _ai.wait(0.5)
    return time.monotonic() - started
