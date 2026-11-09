"""Fake engine for UI development (STUDIA_FAKE_ENGINE=1): no GPU, no model download."""

from __future__ import annotations

import datetime as dt
import time
from typing import Any, Optional

import numpy as np

from ..cancel import check
from ..domain import Segment, Transcript

SR = 16000


class StubEngine:
    model_name = "stub"
    loaded = True

    def ensure_model(self, progress, cancel=None) -> None:
        progress("model-load", 1.0, "Stub model ready")

    def unload(self) -> None:
        pass

    @property
    def device_info(self) -> dict[str, Any]:
        return {"device": "cpu", "compute_type": "none", "gpu": "", "warning": ""}

    def transcribe(self, audio: np.ndarray, progress, cancel=None, meta: Optional[dict] = None, **kw: Any) -> Transcript:
        dur = len(audio) / SR
        segs = []
        t = 0.0
        i = 0
        while t < dur and i < 400:
            check(cancel)
            lang = "tr" if i % 9 == 4 else "en"
            text = ("Arkadaşlar, bu kısım sınavda çıkacak, dikkat edin." if lang == "tr"
                    else f"This is sentence {i + 1} of the stub transcript; remember this for the exam." if i % 13 == 0
                    else f"This is sentence {i + 1} of the stub transcript.")
            segs.append(Segment(round(t, 2), round(min(dur, t + 6), 2), text, lang, 0.9))
            t += 7
            i += 1
            if i % 20 == 0:
                progress("transcribe", min(0.95, t / max(dur, 1)), f"Transcribing {i} passages")
                time.sleep(0.05)
        return Transcript(delivery_id=(meta or {}).get("delivery_id", ""), segments=segs, duration_s=dur, speech_s=dur,
                          language_seconds={"en": dur * 0.9, "tr": dur * 0.1}, model="stub", device="cpu",
                          engine_version="stub", created_at=dt.datetime.now().isoformat(timespec="seconds"),
                          meta=dict(meta or {}, quality={"warnings": [], "stats": {}}))
