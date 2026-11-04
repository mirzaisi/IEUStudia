"""Big native libraries loaded without stopping the rest of the app.

Starting onnxruntime (the speech search's model runs on it) holds Python's lock (the GIL) and needs Windows' loader,
which loads one library at a time. In a full sync the first lecture's speech search started it while another thread
loaded the speech model's graphics card libraries: it waited 13 s for the loader with the GIL held, and every other
thread, the window's included, waited with it. So the two never load at the same time (LOAD_LOCK: waiting for it
doesn't hold the GIL), and onnxruntime's libraries are loaded by ctypes first, which doesn't hold the GIL either.
"""

from __future__ import annotations

import ctypes
import glob
import importlib.util
import logging
import os
import sys
import threading

log = logging.getLogger("studia")

LOAD_LOCK = threading.Lock()     # held while the speech model's libraries load, and while onnxruntime starts
_ORT_LIBRARIES = ("capi/*.dll", "capi/*.pyd")     # (onnxruntime.dll first: the .pyd needs it)


def preload(package: str, patterns: tuple[str, ...]) -> int:
    """Load these libraries of ``package`` with ctypes (Windows; not once it is imported). How many were loaded."""
    if sys.platform != "win32" or package in sys.modules:
        return 0
    try:
        spec = importlib.util.find_spec(package)
    except (ImportError, ValueError):
        return 0
    if spec is None or not spec.origin:
        return 0
    n = 0
    for pattern in patterns:
        for path in sorted(glob.glob(os.path.join(os.path.dirname(spec.origin), *pattern.split("/")))):
            try:
                ctypes.WinDLL(path)
                n += 1
            except OSError:                    # the import then loads it (and says what is wrong)
                log.debug("preloading %s failed", path, exc_info=True)
    return n


def import_onnxruntime() -> None:
    """Import onnxruntime once, never while the speech model loads (the speech search and the model call this)."""
    if "onnxruntime" in sys.modules:           # (also while another thread imports it: Python then waits for that)
        return
    with LOAD_LOCK:
        try:
            preload("onnxruntime", _ORT_LIBRARIES)
            import onnxruntime  # noqa: F401
        except ImportError:                    # faster-whisper says so when it needs it
            log.debug("onnxruntime is not available", exc_info=True)
