"""Whisper model presets, local model storage/download, GPU detection and device selection."""

from __future__ import annotations

import ctypes
import functools
import glob
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Optional

from ..cancel import check
from .. import httpdl
from ..i18n import N_, t
from ..net import plain_session

log = logging.getLogger("studia")

# Sizes are the download size of the CTranslate2 conversion (float16 weights). Labels and descriptions in English:
# t() to show them.
MODEL_PRESETS: dict[str, dict[str, Any]] = {
    "large-v3-turbo": {
        "repo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
        "label": N_("Fast & accurate"),
        "description": N_("Nearly the best quality, and fast with an NVIDIA graphics card."),
        "size_mb": 1620,
    },
    "large-v3": {
        "repo": "Systran/faster-whisper-large-v3",
        "label": N_("Maximum accuracy"),
        "description": N_("The best quality for hard audio and long Turkish passages; the slowest, even with an NVIDIA "
                          "graphics card."),
        "size_mb": 3090,
    },
    "medium": {
        "repo": "Systran/faster-whisper-medium",
        "label": N_("Medium"),
        "description": N_("More accurate than Small, but very slow without an NVIDIA graphics card (hours per lecture). "
                           "With one, Fast & accurate is both quicker and more accurate."),
        "size_mb": 1530,
    },
    "small": {
        "repo": "Systran/faster-whisper-small",
        "label": N_("Small"),
        "description": N_("The one for PCs without an NVIDIA graphics card: fast enough on the processor, a bit less "
                          "accurate."),
        "size_mb": 484,
    },
}
MODEL_FILE_PATTERNS = ["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"]


def _models_root() -> Path:
    try:
        from ..paths import app_dir

        base = app_dir()
    except ImportError:  # paths module unavailable (e.g. isolated engine use)
        base = Path(os.environ.get("STUDIA_HOME") or Path(os.environ.get("LOCALAPPDATA", Path.home())) / "IEUStudia")
    p = Path(base) / "models"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _repo(model: str) -> str:
    if model not in MODEL_PRESETS:
        raise ValueError(t("Unknown model '{model}'.", model=model))
    return MODEL_PRESETS[model]["repo"]


def _complete(d: Path) -> bool:
    return (d / "model.bin").is_file() and (d / "config.json").is_file() and (d / "tokenizer.json").is_file()


def _hf_cache_path(model: str) -> Optional[Path]:
    """Path of the model in the Hugging Face cache if it is fully downloaded there."""
    try:
        from huggingface_hub import snapshot_download

        p = Path(snapshot_download(_repo(model), allow_patterns=MODEL_FILE_PATTERNS, local_files_only=True))
        return p if _complete(p) else None
    except Exception:  # noqa: BLE001 - not cached / hub unavailable
        return None


def local_model_path(model: str) -> Optional[Path]:
    """Where the model is on disk (our own folder first, then the shared HF cache), or None."""
    own = _models_root() / model
    if _complete(own) and (own / ".complete").exists():
        return own
    return _hf_cache_path(model)


def _hf_cache_dir(model: str) -> Path:
    """The model's folder in the shared Hugging Face cache (there or not)."""
    env = os.environ
    root = Path(env.get("HF_HUB_CACHE") or (Path(env["HF_HOME"]) / "hub" if env.get("HF_HOME") else
                                             Path.home() / ".cache" / "huggingface" / "hub"))
    return root / f"models--{_repo(model).replace('/', '--')}"


def _hf_cache_has(model: str) -> bool:
    """The model is complete in the Hugging Face cache — a plain folder check (importing huggingface_hub to ask
    takes ~1 s, too slow for the settings page)."""
    snaps = _hf_cache_dir(model) / "snapshots"
    try:
        return any(_complete(d) for d in snaps.iterdir() if d.is_dir())
    except OSError:
        return False


def model_is_downloaded(model: str) -> bool:
    own = _models_root() / model
    return (_complete(own) and (own / ".complete").exists()) or _hf_cache_has(model)


_download_locks: dict[str, threading.Lock] = {}       # one download of a model at a time (see download_model)
_download_locks_guard = threading.Lock()


def download_model(model: str, progress: Callable[[int, int], None],
                   cancel: Optional[threading.Event] = None) -> Path:
    """Download the model files into %LOCALAPPDATA%\\IEUStudia\\models\\<model> (resumable). A lecture's own download
    and the setup's ("Download now") would write the same part files: the second one waits for the first (and can
    still be cancelled meanwhile), then finds the model there."""
    repo = _repo(model)
    with _download_locks_guard:
        lock = _download_locks.setdefault(model, threading.Lock())
    while not lock.acquire(timeout=0.25):
        check(cancel)
    try:
        target = _models_root() / model
        if _complete(target) and (target / ".complete").exists():      # already there (another download just got it)
            size = sum(f.stat().st_size for f in target.iterdir() if f.is_file())
            progress(size, size)
            return target
        target.mkdir(parents=True, exist_ok=True)
        session = plain_session()
        session.headers["User-Agent"] = "IEUStudia (+https://github.com/mirzaisi/IEUStudia)"
        info = session.get(f"https://huggingface.co/api/models/{repo}", params={"blobs": "true"}, timeout=30)
        from ..components import hf_files

        files = hf_files(info, repo, MODEL_FILE_PATTERNS)        # (its API changing is said as that: health)
        if not any(name == "model.bin" for name, _ in files):
            raise RuntimeError(t("Model files for {model} were not found on Hugging Face.", model=model))
        grand_total = sum(size for _, size in files) or MODEL_PRESETS[model]["size_mb"] * 1024 * 1024
        done_before = 0
        for name, size in sorted(files, key=lambda f: f[1]):
            check(cancel)
            dest = target / name
            base = done_before

            def cb(done: int, total: int, base: int = base) -> None:
                progress(base + done, grand_total)

            httpdl.download(f"https://huggingface.co/{repo}/resolve/main/{name}", dest,
                            session=session, progress=cb, cancel=cancel, parts=8)
            done_before += size or dest.stat().st_size
        (target / ".complete").write_text(json.dumps({"repo": repo}), encoding="utf-8")
        progress(grand_total, grand_total)
        return target
    finally:
        lock.release()


def delete_model(model: str) -> None:
    """Remove IEUStudia's own copy. A copy only in the shared Hugging Face cache (another program downloaded it, and
    may use it) is left there, and the error says so (Settings → Storage shows it)."""
    own = _models_root() / model
    if own.exists():
        shutil.rmtree(own, ignore_errors=True)
    elif _hf_cache_has(model):
        raise RuntimeError(t("IEUStudia didn't download this speech model: it uses the copy in the shared Hugging Face "
                             "cache, which other programs may need too, so it leaves it there. If nothing else uses "
                             "it, you can delete {folder} yourself.", folder=str(_hf_cache_dir(model))))


# ----------------------------------------------------------------------------- GPU / device

@functools.lru_cache(maxsize=1)
def setup_cuda_dlls() -> list[str]:
    """Make cuBLAS/cuDNN DLLs findable on Windows (CUDA toolkit or pip `nvidia-*` wheels); on Linux, load the
    shared libraries before CTranslate2 asks for them by name."""
    if sys.platform != "win32":
        return _preload_linux_cuda()
    dirs: list[str] = []
    try:  # downloaded on first start into <app data>/cuda (see components.py)
        from ..components import cuda_dir

        if (cuda_dir() / "cublas64_12.dll").is_file():
            dirs.append(str(cuda_dir()))
    except Exception:  # noqa: BLE001
        pass
    try:  # pip wheels: site-packages/nvidia/<lib>/bin
        import nvidia  # type: ignore

        for base in nvidia.__path__:
            dirs += [d for d in glob.glob(os.path.join(base, "*", "bin")) if os.path.isdir(d)]
    except ImportError:
        pass
    for key, val in os.environ.items():
        if key.upper().startswith("CUDA_PATH") and val:
            for sub in ("bin", os.path.join("bin", "x64")):
                d = os.path.join(val, sub)
                if os.path.isdir(d) and d not in dirs:
                    dirs.append(d)
    added = []
    for d in dirs:
        try:
            os.add_dll_directory(d)
            added.append(d)
        except OSError:
            pass
    if added:
        os.environ["PATH"] = os.pathsep.join(added + [os.environ.get("PATH", "")])
    return added


def _preload_linux_cuda() -> list[str]:
    """Load cuBLAS and cuDNN (ours or the pip wheels', see components.cuda_sources) globally: CTranslate2 opens
    them by soname, which then finds the copies already loaded, without LD_LIBRARY_PATH."""
    import ctypes

    try:
        from ..components import cuda_sources

        dirs = cuda_sources()
    except Exception:  # noqa: BLE001
        return []
    order = ("libcublasLt.so", "libcublas.so", "libcudnn.so", "libcudnn_")    # dependencies first
    libs = sorted((f for d in dirs for f in Path(d).glob("lib*.so.*")),
                  key=lambda f: next((i for i, p in enumerate(order) if f.name.startswith(p)), len(order)))
    for f in libs:
        try:
            ctypes.CDLL(str(f), mode=ctypes.RTLD_GLOBAL)
        except OSError:
            log.debug("could not load %s", f, exc_info=True)
    return dirs


@functools.lru_cache(maxsize=1)
def _start_cuda_driver() -> bool:
    """Start NVIDIA's driver through ctypes, which lets go of Python's lock (the GIL) meanwhile. CTranslate2's first
    call starts it too but keeps the lock: in a full sync that took ~1 s (Windows' loader busy with other libraries),
    and the window waited all along. Started here, CTranslate2 finds it running. True if it started."""
    try:
        lib = ctypes.WinDLL("nvcuda.dll") if sys.platform == "win32" else ctypes.CDLL("libcuda.so.1")
        return lib.cuInit(0) == 0
    except (OSError, AttributeError):            # no NVIDIA driver: CTranslate2 then says so as before
        return False


@functools.lru_cache(maxsize=2)             # both forms (quick for the display and the offline AI)
def detect_gpu(quick: bool = False) -> dict[str, Any]:
    """{"available": bool, "name": str, "vram_mb": int} for the first NVIDIA GPU usable by CTranslate2.
    ``quick``: only ask nvidia-smi (for the display; loading CTranslate2 takes ~1 s and is checked when
    transcribing anyway)."""
    result: dict[str, Any] = {"available": False, "name": "", "vram_mb": 0}
    if not quick:
        setup_cuda_dlls()
        _start_cuda_driver()
        try:
            import ctranslate2

            if ctranslate2.get_cuda_device_count() < 1:
                return result
            result["available"] = True
        except Exception:  # noqa: BLE001
            return result
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        first = (out.stdout or "").strip().splitlines()[0]
        name, mem = [x.strip() for x in first.split(",")[:2]]
        result.update(available=True, name=name, vram_mb=int(float(mem)))
    except Exception:  # noqa: BLE001
        if result["available"]:
            result["name"] = "NVIDIA GPU"
    return result


def reset_gpu_cache() -> None:
    """Forget what was found (after the GPU libraries were downloaded)."""
    setup_cuda_dlls.cache_clear()
    detect_gpu.cache_clear()


def cublas_available() -> bool:
    try:
        from ..components import cuda_ready

        return cuda_ready()
    except Exception:  # noqa: BLE001
        return True


def resolve_device(preference: str) -> tuple[str, str]:
    """(device, compute_type) for a user preference of "auto" | "cuda" | "cpu". The GPU is used only when its
    libraries (cuBLAS) are on this PC: without them CTranslate2 loads the model and then fails mid-way."""
    pref = (preference or "auto").lower()
    if pref != "cpu" and cublas_available() and detect_gpu()["available"] and (pref == "cuda" or not _card_unusable()):
        return "cuda", cuda_compute_type()
    return "cpu", "int8"


def _card_unusable() -> bool:
    """The card is too old for CTranslate2, or failed to run the model with this driver (hardware.card_unusable)."""
    try:
        from ..hardware import card_unusable

        return card_unusable()
    except Exception:  # noqa: BLE001 - never stops a model from loading
        return False


@functools.lru_cache(maxsize=4)
def cuda_compute_type(prefer: tuple[str, ...] = ("float16", "int8_float16", "int8_float32", "int8")) -> str:
    """The first of ``prefer`` this graphics card computes well: float16 needs a GeForce 20 series or newer;
    older cards (GTX 10 series) get an int8 type instead of failing and falling back to the processor."""
    try:
        import ctranslate2

        supported = set(ctranslate2.get_supported_compute_types("cuda"))
    except Exception:  # noqa: BLE001
        return prefer[0]
    return next((ct for ct in prefer if ct in supported), "float32")
