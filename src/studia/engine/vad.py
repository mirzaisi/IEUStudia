"""Finding speech with faster-whisper's Silero VAD, without copies of the whole lecture.

faster-whisper's get_speech_timestamps pads a copy of the audio, and its model call builds another (every window with
the samples before it): about 2.9x the audio at the peak, +0.5 GB for a 52-minute lecture. Here the same model gets the
same inputs batch by batch (10,000 windows of 512 samples, each with the 64 samples before it, the state carried from
batch to batch, and the end zeroed as there), so the probabilities, and the spans, are the same. The rules that turn
them into spans are faster-whisper's (1.2.1), unchanged.
"""

from __future__ import annotations

import numpy as np

WINDOW = 512                 # samples per probability (32 ms)
CONTEXT = 64                 # samples before each window that the model sees too
BATCH = 10_000               # windows per model call (as faster-whisper: the same calls give the same numbers)


def speech_probs(audio: np.ndarray) -> np.ndarray:
    """The speech probability of every 512-sample window: faster-whisper's SileroVADModel(np.pad(audio, ...))."""
    from faster_whisper.vad import get_vad_model

    from ..nativeload import import_onnxruntime

    import_onnxruntime()                        # not while the speech model loads (it holds up the whole app)
    session = get_vad_model().session
    n = len(audio)
    rows = n // WINDOW + 1                      # np.pad always adds 1-512 zeros to the end
    end = rows * WINDOW
    h = np.zeros((1, 1, 128), dtype=np.float32)
    c = np.zeros((1, 1, 128), dtype=np.float32)
    out = []
    for r0 in range(0, rows, BATCH):
        r1 = min(rows, r0 + BATCH)
        lo = r0 * WINDOW - CONTEXT              # this batch's samples, the context of its first window included
        part = np.zeros(r1 * WINDOW - lo, dtype=np.float32)
        a, b = max(lo, 0), min(r1 * WINDOW, n)
        if b > a:
            part[a - lo:b - lo] = audio[a:b]
        if r1 * WINDOW == end:
            part[-CONTEXT:] = 0.0               # faster-whisper zeroes the last window's tail (its "context[-1] = 0")
        x = np.lib.stride_tricks.as_strided(part, shape=(r1 - r0, WINDOW + CONTEXT),
                                            strides=(WINDOW * part.itemsize, part.itemsize))
        o, h, c = session.run(None, {"input": np.ascontiguousarray(x), "h": h, "c": c})
        out.append(o)
    return np.concatenate(out, axis=0)


def get_speech_timestamps(audio: np.ndarray, vad_options, sampling_rate: int = 16000) -> list[dict]:
    """faster_whisper.vad.get_speech_timestamps (1.2.1) on speech_probs: the same spans.

    Its span rules below are copied from faster-whisper (MIT License, Copyright (c) 2023 SYSTRAN)."""
    threshold = vad_options.threshold
    neg_threshold = vad_options.neg_threshold
    min_speech_duration_ms = vad_options.min_speech_duration_ms
    max_speech_duration_s = vad_options.max_speech_duration_s
    min_silence_duration_ms = vad_options.min_silence_duration_ms
    window_size_samples = WINDOW
    speech_pad_ms = vad_options.speech_pad_ms
    min_speech_samples = sampling_rate * min_speech_duration_ms / 1000
    speech_pad_samples = sampling_rate * speech_pad_ms / 1000
    max_speech_samples = (sampling_rate * max_speech_duration_s - window_size_samples - 2 * speech_pad_samples)
    min_silence_samples = sampling_rate * min_silence_duration_ms / 1000
    min_silence_samples_at_max_speech = sampling_rate * 98 / 1000

    audio_length_samples = len(audio)
    speech_probs_ = speech_probs(audio)

    triggered = False
    speeches = []
    current_speech: dict = {}
    if neg_threshold is None:
        neg_threshold = max(threshold - 0.15, 0.01)

    # to save potential segment end (and tolerate some silence)
    temp_end = 0
    # to save potential segment limits in case of maximum segment size reached
    prev_end = next_start = 0

    for i, speech_prob in enumerate(speech_probs_):
        if (speech_prob >= threshold) and temp_end:
            temp_end = 0
            if next_start < prev_end:
                next_start = window_size_samples * i

        if (speech_prob >= threshold) and not triggered:
            triggered = True
            current_speech["start"] = window_size_samples * i
            continue

        if triggered and (window_size_samples * i) - current_speech["start"] > max_speech_samples:
            if prev_end:
                current_speech["end"] = prev_end
                speeches.append(current_speech)
                current_speech = {}
                # previously reached silence (< neg_thres) and is still not speech (< thres)
                if next_start < prev_end:
                    triggered = False
                else:
                    current_speech["start"] = next_start
                prev_end = next_start = temp_end = 0
            else:
                current_speech["end"] = window_size_samples * i
                speeches.append(current_speech)
                current_speech = {}
                prev_end = next_start = temp_end = 0
                triggered = False
                continue

        if (speech_prob < neg_threshold) and triggered:
            if not temp_end:
                temp_end = window_size_samples * i
            # condition to avoid cutting in very short silence
            if (window_size_samples * i) - temp_end > min_silence_samples_at_max_speech:
                prev_end = temp_end
            if (window_size_samples * i) - temp_end < min_silence_samples:
                continue
            else:
                current_speech["end"] = temp_end
                if (current_speech["end"] - current_speech["start"]) > min_speech_samples:
                    speeches.append(current_speech)
                current_speech = {}
                prev_end = next_start = temp_end = 0
                triggered = False
                continue

    if current_speech and (audio_length_samples - current_speech["start"]) > min_speech_samples:
        current_speech["end"] = audio_length_samples
        speeches.append(current_speech)

    for i, speech in enumerate(speeches):
        if i == 0:
            speech["start"] = int(max(0, speech["start"] - speech_pad_samples))
        if i != len(speeches) - 1:
            silence_duration = speeches[i + 1]["start"] - speech["end"]
            if silence_duration < 2 * speech_pad_samples:
                speech["end"] += int(silence_duration // 2)
                speeches[i + 1]["start"] = int(max(0, speeches[i + 1]["start"] - silence_duration // 2))
            else:
                speech["end"] = int(min(audio_length_samples, speech["end"] + speech_pad_samples))
                speeches[i + 1]["start"] = int(max(0, speeches[i + 1]["start"] - speech_pad_samples))
        else:
            speech["end"] = int(min(audio_length_samples, speech["end"] + speech_pad_samples))

    return speeches
