"""Audio decoding with PyAV (bundled FFmpeg libraries, no ffmpeg.exe needed)."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Optional, Union

import av
import numpy as np

from .cancel import Cancelled, check
from .i18n import t

SAMPLE_RATE = 16000
log = logging.getLogger("studia")

__all__ = ["SAMPLE_RATE", "NoAudioTrack", "decode_audio", "media_duration", "Cancelled"]


class NoAudioTrack(ValueError):
    """A file with no sound track at all (a video-only stream, a screen recording)."""


def decode_audio(path: Union[str, Path], cancel: Optional[threading.Event] = None,
                 max_s: Optional[float] = None) -> np.ndarray:
    """Decode the first audio track of any media file to 16 kHz mono float32 in [-1, 1].

    Corrupt packets are skipped instead of aborting the whole decode, so a lecture with a
    damaged second or two still transcribes. The samples go straight into one array sized from
    the track's length, and frames are resampled in groups of ~0.5 M samples (as faster-whisper
    does): the same samples as frame by frame, with less work and no second copy of the lecture.
    ``max_s``: only the first that many seconds (a study plan listening to the start of a long video).
    """
    skipped = 0
    with av.open(str(path), mode="r") as container:
        streams = [s for s in container.streams if s.type == "audio"]
        if not streams:
            if not any(s.type == "video" for s in container.streams):
                # not even a picture: its track list is damaged (an .m4a whose end never reached the disk), it isn't
                # a recording without sound
                raise ValueError(t("The audio could not be decoded (the file seems to be damaged)."))
            raise NoAudioTrack(t("This file has no audio track."))
        stream = streams[0]
        stream.thread_type = "AUTO"
        seconds = (float(stream.duration * stream.time_base) if stream.duration and stream.time_base else
                   float(container.duration) / av.time_base if container.duration else 0.0)
        declared = seconds
        if max_s:
            seconds = min(seconds, max_s) if seconds else max_s
        limit = int(max_s * SAMPLE_RATE) if max_s else 0
        out = np.empty(int(seconds * SAMPLE_RATE) + SAMPLE_RATE, dtype=np.float32)
        n = 0

        def put(frames: list[av.AudioFrame]) -> None:
            nonlocal out, n
            for frame in frames:
                pcm = frame.to_ndarray().reshape(-1)
                if n + pcm.size > out.size:          # longer than its header said: a quarter more room
                    bigger = np.empty(max(n + pcm.size, out.size + out.size // 4), dtype=np.float32)
                    bigger[:n] = out[:n]
                    out = bigger
                out[n:n + pcm.size] = pcm
                n += pcm.size

        resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
        fifo: Optional[av.AudioFifo] = av.AudioFifo()
        packets = 0
        for packet in container.demux(stream):
            packets += 1
            if packets % 500 == 0:
                check(cancel)
            if limit and n >= limit:
                break
            try:
                frames = packet.decode()
            except av.error.FFmpegError:
                skipped += 1
                continue
            for frame in frames:
                frame.pts = None
                if fifo is not None:
                    try:
                        fifo.write(frame)
                    except ValueError:               # the format changed mid-file: frame by frame from here on
                        if fifo.samples:
                            put(resampler.resample(fifo.read()))
                        fifo = None
                    else:
                        if fifo.samples >= 500_000:
                            put(resampler.resample(fifo.read()))
                        continue
                put(resampler.resample(frame))
        if fifo is not None and fifo.samples:
            put(resampler.resample(fifo.read()))
        put(resampler.resample(None))
        if limit:
            n = min(n, limit)                        # (the frames come in groups: the last one may go past it)
    if skipped:
        log.warning("Skipped %d of %d corrupt audio packets in %s", skipped, packets, Path(path).name)
    if not n:
        # nothing decoded from a track whose header gives it a length: the file is damaged (an .m4a whose index at
        # the end is cut, say), not a recording without sound
        if skipped or declared >= 1.0:
            raise ValueError(t("The audio could not be decoded (the file seems to be damaged)."))
        return np.zeros(0, dtype=np.float32)
    out.resize(n, refcheck=False)                    # gives back the unused end (no copy)
    out *= 1.0 / 32768.0
    return out


def media_duration(path: Union[str, Path]) -> float:
    """Duration of a media file in seconds (0.0 if unknown)."""
    try:
        with av.open(str(path), mode="r") as container:
            if container.duration:
                return float(container.duration) / av.time_base
            for s in container.streams:
                if s.type == "audio" and s.duration and s.time_base:
                    return float(s.duration * s.time_base)
    except Exception:  # noqa: BLE001 - best effort only
        log.debug("Could not read duration of %s", path, exc_info=True)
    return 0.0
