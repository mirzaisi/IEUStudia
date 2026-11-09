"""Subtitles of a recording, as SubRip (.srt, for watching a downloaded video with captions in most players) or WebVTT
(.vtt, for players in a browser): the same cues in both, with their times from the start of the recording."""

from __future__ import annotations

from typing import Iterable

from ..domain import Segment

MIN_S = 0.5                       # a cue stays on screen at least this long, where the next one leaves room


def _ts(sec: float, sep: str = ",") -> str:
    ms = int(round(max(0.0, sec) * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def _wrap(text: str, width: int = 42) -> str:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    if len(lines) > 2:  # keep it to two lines
        half = (len(words) + 1) // 2
        lines = [" ".join(words[:half]), " ".join(words[half:])]
    return "\n".join(lines)


def _split(seg: Segment, max_s: float = 7.0, max_chars: int = 84) -> list[tuple[float, float, str]]:
    words = seg.text.split()
    dur = max(0.01, seg.end - seg.start)
    if dur <= max_s and len(seg.text) <= max_chars:
        return [(seg.start, seg.end, seg.text)]
    n = max(int(-(-dur // max_s)), int(-(-len(seg.text) // max_chars)))
    per = -(-len(words) // n)
    out = []
    for i in range(0, len(words), per):
        chunk = words[i:i + per]
        a = seg.start + dur * i / len(words)
        b = seg.start + dur * min(len(words), i + per) / len(words)
        out.append((a, b, " ".join(chunk)))
    return out


def cues(segments: Iterable[Segment]) -> list[tuple[float, float, str]]:
    """(start, end, text) of each cue, in order: a long segment in pieces of at most 7 s and two lines, a passage in
    another language than English marked ("[TR] …"). A cue lasts at least MIN_S, but never into the next one (two
    cues on screen at once stack up in most players)."""
    raw: list[tuple[float, float, str]] = []
    for seg in segments:
        if not seg.text.strip():
            continue
        text = seg.text if seg.lang in ("en", "") else f"[{seg.lang.upper()}] {seg.text}"
        raw += _split(Segment(seg.start, seg.end, text, seg.lang, seg.conf))
    raw.sort(key=lambda c: c[0])
    out = []
    for i, (a, b, text) in enumerate(raw):
        end = max(b, a + MIN_S)
        if i + 1 < len(raw) and raw[i + 1][0] > a:
            end = min(end, raw[i + 1][0])
        out.append((a, max(end, a + 0.001), _wrap(text)))
    return out


def render_srt(segments: Iterable[Segment]) -> str:
    return "\n".join(f"{n}\n{_ts(a)} --> {_ts(b)}\n{text}\n" for n, (a, b, text) in enumerate(cues(segments), start=1))


def _vtt_text(text: str) -> str:
    # a cue's text is markup in WebVTT: its own <, > and & are written as entities, and "-->" can't be in it
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("-->", "->")


def render_vtt(segments: Iterable[Segment]) -> str:
    body = "\n".join(f"{n}\n{_ts(a, '.')} --> {_ts(b, '.')}\n{_vtt_text(text)}\n"
                     for n, (a, b, text) in enumerate(cues(segments), start=1))
    return "WEBVTT\n\n" + body
