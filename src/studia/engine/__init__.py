"""Local transcription engine (faster-whisper / CTranslate2) with English-Turkish code-switching.

The heavy parts (NumPy, CTranslate2) load on first use: importing ``engine.models`` (model presets, GPU
check) at app start stays cheap. ``from studia.engine import Engine`` works as before.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .transcriber import ENGINE_VERSION, UNCLEAR, Engine  # noqa: F401

_SUBMODULES = ("checkpoint", "core", "models", "stub", "transcriber")


def __getattr__(name: str) -> Any:
    if name.startswith("__") or name in _SUBMODULES:      # let the import system load submodules itself
        raise AttributeError(name)
    for sub in ("models", "transcriber"):
        mod = importlib.import_module(f"{__name__}.{sub}")
        if hasattr(mod, name):
            return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
