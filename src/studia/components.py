"""What IEUStudia downloads on first start, and what it takes on disk.

- the speech model (``engine.models.MODEL_PRESETS``, from Hugging Face),
- on PCs with an NVIDIA GPU: the CUDA runtime libraries the GPU needs (cuBLAS, from NVIDIA's official Python wheels
  on PyPI; only the DLLs are kept, in ``<app data>\\cuda``),
- optionally a small offline AI model (``LLM_PRESETS``) for basic notes and questions without a subscription.

All downloads resume after an interruption and are checked (sizes from Hugging Face, SHA-256 from PyPI).
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
import os
import re
import shutil
import sys
import threading
import zipfile
from pathlib import Path
from typing import Any, Callable, Optional

import requests

from . import httpdl
from .cancel import check
from .i18n import N_, t
from .net import plain_session

log = logging.getLogger("studia")
MB = 1024 * 1024
Progress = Callable[[int, int], None]

# Apache-2.0 models, converted to CTranslate2 int8 (runs with the speech engine already installed). Labels and
# descriptions in English: t() to show them.
LLM_PRESETS: dict[str, dict[str, Any]] = {
    "qwen3-1.7b": {"repo": "jncraton/Qwen3-1.7B-ct2-int8", "label": N_("Basic"), "size_mb": 1745,
                   "description": N_("Short notes and simple questions; runs on most PCs with 8 GB of memory "
                                     "(Qwen3 1.7B)."),
                   "template": "qwen3"},
    "qwen3-4b": {"repo": "jncraton/Qwen3-4B-Instruct-2507-ct2-int8", "label": N_("Better"), "size_mb": 4045,
                 "description": N_("Clearly better notes and answers; needs an NVIDIA graphics card with 8 GB of memory "
                                   "(Qwen3 4B)."),
                 "template": "qwen3-instruct"},
}
LLM_FILES = ["config.json", "generation_config.json", "model.bin", "tokenizer.json", "tokenizer_config.json",
             "vocabulary.*", "vocab.json"]
CUDA_WHEELS = [("nvidia-cublas-cu12", "12.8.", "nvidia/cublas/bin/"),
               ("nvidia-cuda-runtime-cu12", "12.8.", "nvidia/cuda_runtime/bin/")]
CUDA_DLLS = ("cublas64_12.dll", "cublasLt64_12.dll")
CUDA_SIZE_MB = 560                # download (the cuBLAS wheel); about 780 MB once unpacked
CUDA_DISK_MB = 780
WINDOWS = sys.platform == "win32"
if not WINDOWS:                   # Linux: CTranslate2's Linux build brings neither cuBLAS nor cuDNN
    CUDA_WHEELS = [("nvidia-cublas-cu12", "12.8.", "nvidia/cublas/lib/"),
                   ("nvidia-cudnn-cu12", "9.", "nvidia/cudnn/lib/")]
    CUDA_DLLS = ("libcublas.so.12", "libcublasLt.so.12", "libcudnn.so.9")
    CUDA_SIZE_MB, CUDA_DISK_MB = 1250, 2100


def _session() -> requests.Session:
    s = plain_session()
    s.headers["User-Agent"] = "IEUStudia (+https://github.com/mirzaisi/IEUStudia)"
    return s


# ----------------------------------------------------------------------------- locations
def app_root() -> Path:
    from .paths import app_dir

    return app_dir()


def cuda_dir() -> Path:
    return app_root() / "cuda"


def llm_dir(key: str) -> Path:
    return app_root() / "llm" / key


def _dir_size(p: Path) -> int:
    total = 0
    for root, _, files in os.walk(p):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


# ----------------------------------------------------------------------------- CUDA runtime (cuBLAS)
def cuda_sources() -> list[str]:
    """Folders that already provide cuBLAS for CUDA 12 (ours, the CUDA toolkit, or pip ``nvidia-*`` wheels)."""
    if not WINDOWS:
        return _linux_cuda_sources()
    found = []
    ours = cuda_dir()
    if (ours / ".complete").is_file() and all((ours / d).is_file() for d in CUDA_DLLS):   # not a download cut short
        found.append(str(ours))
    for key, val in os.environ.items():
        if key.upper().startswith("CUDA_PATH") and val:
            b = Path(val) / "bin"
            if all((b / d).is_file() for d in CUDA_DLLS):
                found.append(str(b))
    try:
        import nvidia  # type: ignore

        for base in nvidia.__path__:
            b = Path(base) / "cublas" / "bin"
            if all((b / d).is_file() for d in CUDA_DLLS):
                found.append(str(b))
    except ImportError:
        pass
    return found


def _linux_cuda_sources() -> list[str]:
    """Linux: our folder, else the pip ``nvidia-*`` wheels' lib folders, when together they have every library."""
    ours = cuda_dir()
    if (ours / ".complete").is_file() and all((ours / d).is_file() for d in CUDA_DLLS):
        return [str(ours)]
    dirs: list[str] = []
    try:
        import nvidia  # type: ignore

        for base in nvidia.__path__:
            dirs += [str(Path(base) / sub / "lib") for sub in ("cublas", "cudnn") if (Path(base) / sub / "lib").is_dir()]
    except ImportError:
        pass
    have = {f.name for d in dirs for f in Path(d).iterdir()}
    return dirs if all(lib in have for lib in CUDA_DLLS) else []


def cuda_ready() -> bool:
    return bool(cuda_sources())


def _pypi_wheel(package: str, prefix: str, session: requests.Session) -> dict[str, Any]:
    """The newest ``win_amd64`` wheel of ``package`` whose version starts with ``prefix``: url, size, sha256."""
    r = session.get(f"https://pypi.org/pypi/{package}/json", timeout=30)
    r.raise_for_status()
    releases = r.json().get("releases", {})

    def key(v: str) -> list[int]:
        return [int(x) for x in re.findall(r"\d+", v)]

    for version in sorted((v for v in releases if v.startswith(prefix)), key=key, reverse=True):
        for f in releases[version]:
            name = f["filename"]
            built = name.endswith("win_amd64.whl") if WINDOWS else ("manylinux" in name and name.endswith("x86_64.whl"))
            if built and not f.get("yanked"):
                return {"version": version, "url": f["url"], "size": int(f["size"]), "sha256": f["digests"]["sha256"],
                        "filename": f["filename"]}
    if not WINDOWS:
        raise RuntimeError(t("No Linux build of {package} {version}x was found on PyPI.", package=package,
                             version=prefix))
    raise RuntimeError(t("No Windows build of {package} {version}x was found on PyPI.", package=package, version=prefix))


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(4 * MB), b""):
            h.update(chunk)
    return h.hexdigest()


def download_cuda(progress: Progress, cancel: Optional[threading.Event] = None) -> Path:
    """cuBLAS + the CUDA runtime DLLs from NVIDIA's wheels on PyPI into ``<app data>\\cuda`` (resumable, verified).
    Every wheel is downloaded first, then checked and unpacked: the page's progress reaches the end with the last
    byte, and the quiet seconds of checking come after it (the page says "Finishing" then, not "nothing arrives")."""
    session = _session()
    wheels = [(_pypi_wheel(pkg, prefix, session), member) for pkg, prefix, member in CUDA_WHEELS]
    total = sum(w["size"] for w, _ in wheels)
    target = cuda_dir()
    target.mkdir(parents=True, exist_ok=True)
    tmp = target / "download"
    tmp.mkdir(exist_ok=True)
    done = 0
    for w, member in wheels:
        check(cancel)
        dest = tmp / w["filename"]
        base = done

        def cb(d: int, n: int, base: int = base) -> None:
            progress(base + d, total)

        if not (dest.is_file() and dest.stat().st_size == w["size"]):
            httpdl.download(w["url"], dest, session=session, progress=cb, cancel=cancel, parts=8)
        done += w["size"]
        progress(done, total)
    for w, member in wheels:
        check(cancel)
        dest = tmp / w["filename"]
        if _sha256(dest) != w["sha256"]:
            dest.unlink(missing_ok=True)
            raise RuntimeError(t("The download of {name} was damaged. Try again.", name=w["filename"]))
        with zipfile.ZipFile(dest) as z:
            for info in z.infolist():
                lib = info.filename.lower().endswith(".dll") if WINDOWS else ".so" in Path(info.filename).name
                if info.filename.startswith(member) and lib:
                    out = target / Path(info.filename).name
                    part = out.with_name(out.name + ".part")
                    with z.open(info) as src, open(part, "wb") as dst:
                        shutil.copyfileobj(src, dst, 4 * MB)
                    httpdl.move_into_place(part, out, cancel)   # never a half-written DLL under its real name
        dest.unlink(missing_ok=True)
    shutil.rmtree(tmp, ignore_errors=True)
    (target / ".complete").write_text(json.dumps({w["filename"]: w["version"] for w, _ in wheels}), encoding="utf-8")
    from .engine.models import reset_gpu_cache

    reset_gpu_cache()
    return target


# ----------------------------------------------------------------------------- offline AI models
def llm_ready(key: str) -> bool:
    d = llm_dir(key)
    return (d / ".complete").is_file() and (d / "model.bin").is_file() and (d / "tokenizer.json").is_file()


def hf_files(info: requests.Response, repo: str, patterns: list[str]) -> list[tuple[str, int]]:
    """The files of a Hugging Face repo matching ``patterns`` (name, size), from its API answer. An answer without
    its file list, or one the files can't be read from, is Hugging Face's API changing: raised as
    health.ServiceChanged (the files already on this PC stay)."""
    from . import health

    status = getattr(info, "status_code", 200)
    if status in (404, 410, 401):
        health.changed("huggingface", "models", f"the model's page answered HTTP {status}")
    info.raise_for_status()
    try:
        siblings = info.json().get("siblings")
        if not isinstance(siblings, list):
            raise KeyError("siblings")
        files = [(str(s["rfilename"]), int(s.get("size") or 0)) for s in siblings
                 if any(fnmatch.fnmatch(str(s["rfilename"]), p) for p in patterns)]
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise health.changed("huggingface", "models", f"the model's page has no file list IEUStudia reads "
                                                      f"({exc.__class__.__name__}: {exc})") from exc
    health.ok("huggingface", "models")
    return files


def download_hf(repo: str, target: Path, patterns: list[str], progress: Progress,
                cancel: Optional[threading.Event] = None, fallback_mb: int = 0) -> Path:
    """The files of a Hugging Face repo matching ``patterns`` into ``target`` (resumable)."""
    session = _session()
    target.mkdir(parents=True, exist_ok=True)
    info = session.get(f"https://huggingface.co/api/models/{repo}", params={"blobs": "true"}, timeout=30)
    files = hf_files(info, repo, patterns)
    if not any(name == "model.bin" for name, _ in files):
        raise RuntimeError(t("The model files of {repo} were not found on Hugging Face.", repo=repo))
    total = sum(size for _, size in files) or fallback_mb * MB
    done = 0
    damaged = target / ".damaged"          # it didn't load (local_llm): every file afresh, also those of the right size
    if damaged.is_file():
        for name, _ in files:
            (target / name).unlink(missing_ok=True)
    for name, size in sorted(files, key=lambda f: f[1]):
        check(cancel)
        base = done

        def cb(d: int, n: int, base: int = base) -> None:
            progress(base + d, total)

        dest = target / name
        if not (size and dest.is_file() and dest.stat().st_size == size):
            httpdl.download(f"https://huggingface.co/{repo}/resolve/main/{name}", dest, session=session,
                            progress=cb, cancel=cancel, parts=8)
        done += size or dest.stat().st_size
    damaged.unlink(missing_ok=True)
    (target / ".complete").write_text(json.dumps({"repo": repo}), encoding="utf-8")
    progress(total, total)
    return target


def download_llm(key: str, progress: Progress, cancel: Optional[threading.Event] = None) -> Path:
    p = LLM_PRESETS[key]
    return download_hf(p["repo"], llm_dir(key), LLM_FILES, progress, cancel, p["size_mb"])


def delete_llm(key: str) -> None:
    shutil.rmtree(llm_dir(key), ignore_errors=True)


# ----------------------------------------------------------------------------- the whole picture
def own_speech_model(key: str) -> bool:
    """IEUStudia's own copy of a speech model is complete in its own folder. A model that is ready only through the
    shared Hugging Face folder in the user folder (another program downloaded it, and may use it) is not: Settings →
    Storage never offers to remove that one."""
    from .engine.models import _complete, _models_root

    d = _models_root() / key
    return _complete(d) and (d / ".complete").exists()


def catalog(facts: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Every component with its size and whether it is on this PC (for the first-start screen and Settings); a speech
    model also says whether the copy is IEUStudia's own ("own"; else it is used from the shared Hugging Face folder)."""
    from .engine.models import MODEL_PRESETS, model_is_downloaded

    gpu = bool(facts and facts.get("gpu", {}).get("present"))
    return {
        "speech": [{"id": k, "label": t(v["label"]), "description": t(v["description"]), "size_mb": v["size_mb"],
                    "ready": model_is_downloaded(k), "own": own_speech_model(k)} for k, v in MODEL_PRESETS.items()],
        "cuda": {"needed": gpu, "ready": cuda_ready(), "size_mb": CUDA_SIZE_MB, "disk_mb": CUDA_DISK_MB},
        "llm": [{"id": k, "label": t(v["label"]), "description": t(v["description"]), "size_mb": v["size_mb"],
                 "ready": llm_ready(k)} for k, v in LLM_PRESETS.items()],
    }


def plan(speech: str, llm: str = "", gpu: bool = False) -> list[dict[str, Any]]:
    """The downloads a choice needs (only what is missing): [{id, label, size_mb}]."""
    from .engine.models import MODEL_PRESETS, model_is_downloaded

    out = []
    if speech and not model_is_downloaded(speech):
        out.append({"id": f"speech:{speech}", "label": t("Speech model ({name})", name=t(MODEL_PRESETS[speech]["label"])),
                    "size_mb": MODEL_PRESETS[speech]["size_mb"]})
    if gpu and not cuda_ready():
        out.append({"id": "cuda", "label": t("NVIDIA graphics card support (from NVIDIA)"), "size_mb": CUDA_SIZE_MB})
    if llm and not llm_ready(llm):
        out.append({"id": f"llm:{llm}", "label": t("Offline AI model ({name})", name=t(LLM_PRESETS[llm]["label"])),
                    "size_mb": LLM_PRESETS[llm]["size_mb"]})
    return out


def describe(component: str) -> Optional[dict[str, Any]]:
    """A component of :func:`plan` by its id ({id, label, size_mb}), whether or not it is downloaded."""
    from .engine.models import MODEL_PRESETS

    kind, _, key = component.partition(":")
    if kind == "speech" and key in MODEL_PRESETS:
        return {"id": component, "label": t("Speech model ({name})", name=t(MODEL_PRESETS[key]["label"])),
                "size_mb": MODEL_PRESETS[key]["size_mb"]}
    if kind == "cuda":
        return {"id": "cuda", "label": t("NVIDIA graphics card support (from NVIDIA)"), "size_mb": CUDA_SIZE_MB}
    if kind == "llm" and key in LLM_PRESETS:
        return {"id": component, "label": t("Offline AI model ({name})", name=t(LLM_PRESETS[key]["label"])),
                "size_mb": LLM_PRESETS[key]["size_mb"]}
    return None


def ready(component: str) -> bool:
    """Is this component of :func:`plan` on this PC? One this version doesn't know (another version noted it) has
    nothing to download."""
    from .engine.models import MODEL_PRESETS, model_is_downloaded

    kind, _, key = component.partition(":")
    if kind == "speech":
        return key not in MODEL_PRESETS or model_is_downloaded(key)
    if kind == "cuda":
        return cuda_ready()
    if kind == "llm":
        return key not in LLM_PRESETS or llm_ready(key)
    return True


def download(component: str, progress: Progress, cancel: Optional[threading.Event] = None) -> None:
    """One component of :func:`plan` by its id. When it stops, the error says why in plain words (no internet, the
    site blocked on this network, a full disk, an antivirus) and what to do: see httpdl.explain."""
    from .engine.models import download_model

    kind, _, key = component.partition(":")
    try:
        if kind == "speech":
            download_model(key, progress, cancel)
        elif kind == "cuda":
            download_cuda(progress, cancel)
        elif kind == "llm":
            download_llm(key, progress, cancel)
        else:
            raise ValueError(f"Unknown component {component!r}")
    except Exception as exc:  # noqa: BLE001 - the same error, worded for the student
        httpdl.raise_explained(exc, "pypi.org" if kind == "cuda" else "huggingface.co")


def disk_usage(output_dir: Optional[Path] = None) -> dict[str, int]:
    """Bytes used by IEUStudia: speech models, GPU libraries, offline AI, library and transcripts (with the copies kept
    before an upgrade), the Codex CLI the app installed, temporary files (caches, an update waiting to install, logs;
    the added profiles' downloaded audio too), outputs."""
    root = app_root()
    profiles = root / "profiles"
    try:
        added_cache = sum(_dir_size(p / "cache") for p in profiles.iterdir() if p.is_dir())
    except OSError:
        added_cache = 0
    out = {"speech_models": _dir_size(root / "models"), "gpu_libraries": _dir_size(root / "cuda"),
           "offline_ai": _dir_size(root / "llm"), "ai_tools": _dir_size(root / "tools"),
           "cache": (_dir_size(root / "cache") + _dir_size(root / "webview") + _dir_size(root / "updates")
                     + _dir_size(root / "logs") + added_cache),
           "library": 0}
    for f in root.glob("library.db*"):
        try:
            out["library"] += f.stat().st_size
        except OSError:
            pass
    out["library"] += (_dir_size(root / "transcripts") + _dir_size(root / "backups") + _dir_size(profiles)
                       - added_cache)
    if output_dir is not None:
        out["your_files"] = _dir_size(Path(output_dir))
    return out
