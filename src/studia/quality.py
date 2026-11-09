"""Audio safeguards: detect silent / truncated / quiet / clipped / noisy recordings and
low-confidence transcripts, and turn them into clear warnings for the student.

Warnings are dicts: ``{"code", "level" ("info"|"warning"|"error"), "message", "start"?, "end"?}``.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, Iterable, Optional, Sequence

from .i18n import num, t

if TYPE_CHECKING:   # NumPy loads only when audio is analysed (the UI imports worst_level at start)
    import numpy as np

SR = 16000


class NoSpeech(Exception):
    """Nothing to transcribe: the recording is silent or has no usable audio."""


SILENT_DBFS = -50.0


def peak(audio: np.ndarray) -> float:
    """The loudest sample's level (0..1), without an absolute-value copy of the whole lecture."""
    return max(float(audio.max()), -float(audio.min())) if audio.size else 0.0


def is_silent(audio: np.ndarray) -> bool:
    """Digital silence / microphone off (the loudest sample stays below -50 dBFS)."""
    return audio.size == 0 or peak(audio) < 10 ** (SILENT_DBFS / 20)


def _db(x: float) -> float:
    return 20.0 * math.log10(max(x, 1e-9))


def _mmss(sec: float) -> str:
    sec = int(max(0, sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def analyze_audio(audio: np.ndarray, speech: Sequence[tuple[int, int]], expected_duration: float = 0.0
                  ) -> tuple[list[dict[str, Any]], dict[str, Any], float]:
    """Inspect decoded audio + VAD speech spans (sample ranges).

    Returns ``(warnings, stats, gain)`` where ``gain`` is a linear factor to apply before
    transcription (1.0 = leave as is). Raises :class:`NoSpeech` when there is nothing to transcribe.
    """
    import numpy as np

    warnings: list[dict[str, Any]] = []
    n = len(audio)
    duration = n / SR
    if n == 0:
        raise NoSpeech(t("The recording has no audio."))
    loudest = peak(audio)
    speech_samples = sum(e - s for s, e in speech)
    speech_s = speech_samples / SR
    stats: dict[str, Any] = {"duration_s": round(duration, 1), "speech_s": round(speech_s, 1),
                             "speech_ratio": round(speech_s / duration, 3) if duration else 0.0,
                             "peak_dbfs": round(_db(loudest), 1)}

    if expected_duration and duration < expected_duration * 0.9 - 20:
        warnings.append({"code": "truncated", "level": "warning",
                         "message": t("The downloaded audio is shorter than the recording ({got} of {total}). The end "
                                      "may be missing.", got=_mmss(duration), total=_mmss(expected_duration))})

    if loudest < 10 ** (SILENT_DBFS / 20):
        raise NoSpeech(t("This recording has no sound on Panopto (the microphone was probably off). "
                         "All available audio streams are silent: check it on Panopto or ask the lecturer."))
    if speech_s < 8:
        raise NoSpeech(t("No speech was found in this recording (only noise or music). Check it on Panopto."))

    # loudness of the speech parts (sampled to keep it fast)
    mask = np.zeros(n, dtype=bool)
    for s, e in speech:
        mask[s:e] = True
    step = max(1, n // 4_000_000)
    sp = audio[::step][mask[::step]]
    non = audio[::step][~mask[::step]]
    speech_rms = float(np.sqrt(np.mean(sp ** 2))) if sp.size else 0.0
    noise_rms = float(np.sqrt(np.mean(non ** 2))) if non.size > SR else 0.0
    stats["speech_rms_dbfs"] = round(_db(speech_rms), 1)
    snr = _db(speech_rms) - _db(noise_rms) if noise_rms > 0 else 40.0
    stats["snr_db"] = round(snr, 1)
    clipped = float(np.mean(np.abs(audio[::step]) > 0.985))
    stats["clipped_ratio"] = round(clipped, 4)

    gain = 1.0
    if _db(speech_rms) < -38:
        gain = min(20.0, 10 ** ((-24 - _db(speech_rms)) / 20), 0.97 / max(loudest, 1e-6))
        warnings.append({"code": "quiet", "level": "info",
                         "message": t("The recording is very quiet ({db} dBFS); the volume was boosted ×{gain} before "
                                      "transcription.", db=num(stats["speech_rms_dbfs"], 1), gain=num(gain, 1))})
    if clipped > 0.005:
        warnings.append({"code": "clipping", "level": "warning",
                         "message": t("The audio is distorted (too loud / clipping); some words may be wrong.")})
    if snr < 8:
        warnings.append({"code": "noisy", "level": "warning",
                         "message": t("There is a lot of background noise; the transcript may contain errors.")})

    if duration > 120 and speech_s / duration < 0.2:
        warnings.append({"code": "little_speech", "level": "warning",
                         "message": t("Only {speech} of speech in {total} of recording.", speech=_mmss(speech_s),
                                      total=_mmss(duration))})
    # long gaps without speech (muted microphone, break, recording left running)
    spans = sorted(speech)
    edges = [(0, 0)] + spans + [(n, n)]
    for (s1, e1), (s2, e2) in zip(edges, edges[1:]):
        gap = (s2 - e1) / SR
        if gap >= 300:
            when = {"start": _mmss(e1 / SR), "end": _mmss(s2 / SR), "gap": _mmss(gap)}
            message = (t("No speech from {start} to {end} ({gap}, at the start): a break, a muted microphone or "
                         "the recording kept running.", **when) if e1 == 0 else
                       t("No speech from {start} to {end} ({gap}, at the end): a break, a muted microphone or "
                         "the recording kept running.", **when) if s2 == n else
                       t("No speech from {start} to {end} ({gap}): a break, a muted microphone or the recording "
                         "kept running.", **when))
            warnings.append({"code": "gap", "level": "info", "start": e1 / SR, "end": s2 / SR, "message": message})
    return warnings, stats, gain


def analyze_transcript(segments: Iterable[Any], speech_s: float) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Warnings derived from recognition confidence (segments need ``start``, ``end``, ``conf``)."""
    segs = list(segments)
    covered = sum(max(0.0, s.end - s.start) for s in segs)
    low = sum(max(0.0, s.end - s.start) for s in segs if s.conf < 0.45)
    stats = {"low_conf_ratio": round(low / covered, 3) if covered else 0.0,
             "coverage": round(covered / speech_s, 3) if speech_s else 0.0,
             "mean_conf": round(sum(s.conf for s in segs) / len(segs), 3) if segs else 0.0}
    warnings = []
    if covered and low / covered > 0.25:
        warnings.append({"code": "low_confidence", "level": "warning",
                         "message": t("About {n} % of the speech was hard to understand (unclear or noisy audio). "
                                      "Those passages are marked ⚠ in the transcript.", n=round(100 * low / covered))})
    if speech_s > 60 and covered / speech_s < 0.5:
        warnings.append({"code": "unclear", "level": "warning",
                         "message": t("Large parts of the speech could not be transcribed reliably.")})
    return warnings, stats


def worst_level(warnings: Iterable[dict[str, Any]]) -> Optional[str]:
    order = {"info": 0, "warning": 1, "error": 2}
    levels = [w.get("level", "info") for w in warnings]
    return max(levels, key=lambda l: order.get(l, 0)) if levels else None
