"""Can this PC run IEUStudia, and how well? Windows version, processor, memory, disk and NVIDIA GPU.

``check()`` gathers the facts (fast: registry, Win32 calls and one ``nvidia-smi`` call) and ``assess()`` turns them
into a verdict: ``blockers`` (IEUStudia cannot work here; the first-start screen stops), ``warnings`` (it works,
with limits) and recommendations (speech model, whether the GPU is used, which offline AI model fits).
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import functools
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Optional

from .i18n import num, t

log = logging.getLogger("studia")

MIN_BUILD = 17763                 # Windows 10 1809: the oldest Windows 10 that WebView2 supports
MIN_RAM_GB = 4.0
MIN_CUDA_DRIVER = 527.0           # CUDA 12 on Windows
WEBVIEW2_GUID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
GB = 1024 ** 3
WINDOWS = sys.platform == "win32"
# no window, and a normal program's priority while the app transcribes below normal (see winutil._NORMAL)
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "NORMAL_PRIORITY_CLASS", 0)


class _MemoryStatus(ctypes.Structure):
    _fields_ = [("dwLength", wt.DWORD), ("dwMemoryLoad", wt.DWORD), ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def _meminfo() -> dict[str, float]:
    """Linux: /proc/meminfo in GB."""
    out: dict[str, float] = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, _, rest = line.partition(":")
            out[key] = int(rest.split()[0]) * 1024 / GB
    except (OSError, ValueError, IndexError):
        pass
    return out


def memory_gb() -> tuple[float, float]:
    """(total, available) physical memory in GB that Windows can use."""
    if not WINDOWS:
        m = _meminfo()
        return m.get("MemTotal", 0.0), m.get("MemAvailable", 0.0)
    st = _MemoryStatus()
    st.dwLength = ctypes.sizeof(_MemoryStatus)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
        return 0.0, 0.0
    return st.ullTotalPhys / GB, st.ullAvailPhys / GB


def installed_memory_gb() -> float:
    """The memory built into the PC, as its specs say ("8 GB"); 0 if unknown. Windows can use a little less: the
    firmware and the integrated graphics keep some, so an 8 GB laptop has about 7.7 GB usable."""
    if not WINDOWS:
        return 0.0                               # unknown on Linux: the checks go by what is usable
    kb = ctypes.c_ulonglong(0)
    try:
        if ctypes.windll.kernel32.GetPhysicallyInstalledSystemMemory(ctypes.byref(kb)):
            return kb.value / (1024 * 1024)
    except (AttributeError, OSError):
        pass
    return 0.0


def eight_gb(facts: dict[str, Any]) -> bool:
    """An 8 GB PC or more (by what is installed, else by what is usable, which is a few hundred MB less)."""
    installed = float(facts.get("ram_installed_gb") or 0)
    return installed >= 7.5 if installed else float(facts.get("ram_gb") or 0) >= 7.0


def enough_memory(facts: dict[str, Any]) -> bool:
    """The 4 GB minimum by what is installed, like :func:`eight_gb` (a 4 GB laptop lets Windows use 3.4-3.8 GB)."""
    installed = float(facts.get("ram_installed_gb") or 0)
    return installed >= MIN_RAM_GB - 0.5 if installed else float(facts.get("ram_gb") or 0) >= MIN_RAM_GB


def _reg(root: int, path: str, name: str) -> str:
    import winreg

    for flags in (winreg.KEY_READ | winreg.KEY_WOW64_64KEY, winreg.KEY_READ | winreg.KEY_WOW64_32KEY):
        try:
            with winreg.OpenKey(root, path, 0, flags) as k:
                return str(winreg.QueryValueEx(k, name)[0])
        except OSError:
            continue
    return ""


def cpu_info() -> dict[str, Any]:
    if not WINDOWS:
        name, flags = "", set()
        try:
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                key, _, val = line.partition(":")
                if key.strip() == "model name" and not name:
                    name = val.strip()
                elif key.strip() == "flags" and not flags:
                    flags = set(val.split())
        except OSError:
            pass
        return {"name": " ".join(name.split()) or platform.processor(), "cores": os.cpu_count() or 1,
                "avx": "avx" in flags, "avx2": "avx2" in flags, "arch": platform.machine()}
    import winreg

    feature = ctypes.windll.kernel32.IsProcessorFeaturePresent
    name = _reg(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0", "ProcessorNameString")
    return {"name": " ".join(name.split()) or platform.processor(), "cores": os.cpu_count() or 1,
            "avx": bool(feature(39)), "avx2": bool(feature(40)),          # PF_AVX_/PF_AVX2_INSTRUCTIONS_AVAILABLE
            "arch": platform.machine()}


def linux_info() -> dict[str, Any]:
    """The Linux counterpart of :func:`windows_info` (same keys; ``build`` is not a Windows build)."""
    name = ""
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if line.startswith("PRETTY_NAME="):
                name = line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return {"name": name or "Linux", "build": 0, "version": platform.release(), "edition": "", "linux": True,
            "x64": platform.machine().upper() in ("AMD64", "X86_64", "ARM64", "AARCH64") and sys.maxsize > 2 ** 32}


def windows_info() -> dict[str, Any]:
    if not WINDOWS:
        return linux_info()
    v = sys.getwindowsversion()
    import winreg

    display = _reg(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion", "DisplayVersion")
    edition = _reg(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion", "ProductName")
    name = "Windows 11" if v.build >= 22000 else ("Windows 10" if v.major == 10 else f"Windows {v.major}.{v.minor}")
    return {"name": name, "build": v.build, "version": display, "edition": edition,
            "x64": platform.machine().upper() in ("AMD64", "X86_64", "ARM64") and sys.maxsize > 2 ** 32}


def webview2_version() -> str:
    if not WINDOWS:
        return ""                                # the window is Qt WebEngine on Linux
    import winreg

    for root, path in ((winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_GUID}"),
                       (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_GUID}"),
                       (winreg.HKEY_CURRENT_USER, rf"Software\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_GUID}")):
        v = _reg(root, path, "pv")
        if v and v != "0.0.0.0":
            return v
    return ""


def nvidia_gpu() -> dict[str, Any]:
    """The first NVIDIA GPU according to nvidia-smi (installed with the driver): name, memory, driver version and
    compute capability ("8.9"; "" when the driver is too old to say)."""
    out: dict[str, Any] = {"present": False, "name": "", "vram_mb": 0, "driver": "", "compute_cap": ""}
    exe = shutil.which("nvidia-smi") or os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "nvidia-smi.exe")
    for fields in ("name,memory.total,driver_version,compute_cap", "name,memory.total,driver_version"):
        try:
            r = subprocess.run([exe, f"--query-gpu={fields}", "--format=csv,noheader,nounits"],
                               capture_output=True, text=True, timeout=10, creationflags=_NO_WINDOW)
        except (OSError, subprocess.SubprocessError):
            return out
        try:
            vals = [x.strip() for x in (r.stdout or "").strip().splitlines()[0].split(",")]
            name, mem, driver = vals[:3]
            out.update(present=True, name=name, vram_mb=int(float(mem)), driver=driver,
                       compute_cap=vals[3] if len(vals) > 3 and _cap_number(vals[3]) else "")
            return out
        except (IndexError, ValueError):
            continue                  # a driver from before 2022 doesn't know compute_cap: asked again without it
    return out


def _cap_number(cap: Any) -> float:
    try:
        return float(cap)
    except (TypeError, ValueError):
        return 0.0


# ------------------------------------------------------------------ a graphics card that can't run the speech model
# CTranslate2 (4.x) has code for compute capability 6.0 and up: a GTX 750 or 900 series card (5.x) passes the memory
# and driver checks but can't run the speech model or the offline AI. A card that failed when it was tried (another
# reason: a driver that doesn't work, a card the drivers can't use) is remembered for this PC, with its driver: the
# next starts go to the processor at once, and a driver update gets the card tried again.
MIN_COMPUTE = 6.0
CARD_FILE = "graphics-card.json"


def _card_file() -> Path:
    from .paths import app_dir

    return app_dir() / CARD_FILE


@functools.lru_cache(maxsize=1)
def _card_now() -> dict[str, Any]:
    """This PC's card, asked once a run (only when the speech model or the offline AI is about to use it)."""
    return nvidia_gpu()


# How a failure of the graphics card is taken (CTranslate2's words): one that means the card can't run the code at all
# (too old for it, a driver too old for it) is remembered at once; memory that ran out (cuBLAS says ALLOC_FAILED or
# NOT_INITIALIZED then) and a one-off fault pass, so the next lecture tries the card again; any other failure is
# remembered when it happens a second time with the same driver. (A card that was only full once made every later
# lecture go to the much slower processor until a driver update.)
CARD_CANT = ("no kernel image", "arch_mismatch", "not supported", "invalid device function",
             "driver version is insufficient", "unsupported gpu", "unsupported device")
CARD_PASSING = ("out of memory", "alloc_failed", "not_initialized", "bad_alloc", "unspecified launch failure",
                "illegal memory access", "launch timed out", "launch failed", "busy or unavailable")
on_card_change: Optional[Callable[[], None]] = None     # what is remembered changed (api.py: Settings is told again)


def card_error_kind(error: str) -> str:
    """"cant": the card can't run the model; "passing": it was only full, or a one-off fault; "": not known."""
    low = (error or "").lower()
    if any(w in low for w in CARD_PASSING):
        return "passing"
    return "cant" if any(w in low for w in CARD_CANT) else ""


def _card_record(gpu: dict[str, Any]) -> dict[str, Any]:
    """What is written about this card with this driver (also a failure seen once, not yet remembered)."""
    try:
        seen = json.loads(_card_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(seen, dict) or not seen.get("name"):
        return {}
    return seen if (gpu.get("name"), gpu.get("driver")) == (seen.get("name"), seen.get("driver")) else {}


def _card_changed() -> None:
    try:
        if on_card_change:
            on_card_change()
    except Exception:  # noqa: BLE001 - only a refresh of what Settings shows
        log.debug("telling about the graphics card failed", exc_info=True)


def card_failed(gpu: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """What is remembered of this graphics card failing to run the speech model ({} if nothing, or if the card or its
    driver has changed since: then it is tried again). ``gpu``: nvidia_gpu()'s facts, asked for when not given."""
    seen = _card_record(_card_now() if gpu is None else gpu)
    return seen if seen and seen.get("sure", True) else {}


def remember_card_failed(error: str, model: str = "") -> bool:
    """The graphics card failed to run the speech model or the offline AI (see CARD_CANT and CARD_PASSING): one that
    can't run it is remembered at once, and the processor is used from now on (until the card or its driver changes);
    another failure is remembered the second time. True: it is remembered now."""
    gpu = _card_now()
    kind = card_error_kind(error)
    if not gpu.get("present") or kind == "passing":
        return False
    before = _card_record(gpu)
    count = int(before.get("count", 1)) + 1 if before else 1
    sure = kind == "cant" or count >= 2
    try:
        _card_file().write_text(json.dumps({"name": gpu["name"], "driver": gpu["driver"], "error": (error or "")[:400],
                                            "model": model, "at": time.strftime("%Y-%m-%d %H:%M"), "count": count,
                                            "sure": sure}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        return False
    if sure and not before.get("sure"):
        _card_changed()
    return sure


def forget_card_failed() -> None:
    """The card ran the speech model after all (chosen in Settings): it is used again."""
    try:
        had = _card_file().is_file()
        _card_file().unlink(missing_ok=True)
    except OSError:
        return
    if had:
        _card_changed()


def card_unusable() -> bool:
    """The speech model and the offline AI shouldn't try the graphics card on their own ("Automatic"): it is too old
    for them, or it failed with this driver before. (Chosen in Settings, "Graphics card" still tries it.)"""
    gpu = _card_now()
    if not gpu.get("present"):
        return False
    cap = _cap_number(gpu.get("compute_cap"))
    return bool(cap and cap < MIN_COMPUTE) or bool(card_failed(gpu))


def _driver_number(driver: str) -> float:
    try:                                         # "566.36" on Windows, "595.99.02" on Linux
        major, _, rest = driver.partition(".")
        return float(f"{int(major)}.{int(rest.split('.')[0] or 0):02d}")
    except ValueError:
        return 0.0


def disk_free_gb(path: Path) -> float:
    p = Path(path)
    while not p.exists() and p.parent != p:
        p = p.parent
    try:
        return shutil.disk_usage(p).free / GB
    except OSError:
        return 0.0


def check(data_dir: Optional[Path] = None, output_dir: Optional[Path] = None) -> dict[str, Any]:
    """The facts about this PC (no judgement yet)."""
    if data_dir is None:
        from .paths import app_dir

        data_dir = app_dir()
    total, avail = memory_gb()
    facts = {"windows": windows_info(), "cpu": cpu_info(), "ram_gb": round(total, 1), "ram_free_gb": round(avail, 1),
             "ram_installed_gb": round(installed_memory_gb(), 1), "gpu": nvidia_gpu(), "webview2": webview2_version(),
             "disk": {"data_dir": str(data_dir), "data_free_gb": round(disk_free_gb(data_dir), 1)}}
    if output_dir is not None:
        facts["disk"].update(output_dir=str(output_dir), output_free_gb=round(disk_free_gb(output_dir), 1))
    return facts


def assess(facts: dict[str, Any], need_gb: float = 0.0) -> dict[str, Any]:
    """Blockers, warnings and recommendations from :func:`check`'s facts. ``need_gb``: what the chosen downloads need."""
    from .engine.models import MODEL_PRESETS

    w, cpu, gpu = facts["windows"], facts["cpu"], facts["gpu"]
    ram = float(facts.get("ram_gb") or 0)
    blockers: list[str] = []
    warnings: list[str] = []
    linux = bool(w.get("linux"))
    if not w.get("x64"):
        blockers.append(t("IEUStudia needs a 64-bit system.") if linux else t("IEUStudia needs 64-bit Windows."))
    if not linux and int(w.get("build") or 0) < MIN_BUILD:
        blockers.append(t("IEUStudia needs Windows 10 (version 1809 or newer) or Windows 11."))
    if not cpu.get("avx"):
        blockers.append(t("This processor can't run the speech recognition: it lacks the AVX instructions it needs "
                          "(most processors since about 2011 have them, but some low-cost Celeron and Pentium ones "
                          "don't)."))
    if ram and not enough_memory(facts):
        blockers.append(t("IEUStudia needs at least {min} GB of memory; this PC has {ram} GB.", min=num(MIN_RAM_GB),
                          ram=num(ram, 1)))
    free = float(facts.get("disk", {}).get("data_free_gb") or 0)
    if need_gb and free < need_gb + 1:
        blockers.append(t("Not enough free disk space: the downloads need about {need} GB plus room for your lectures, "
                          "but drive {drive} has {free} GB free.", need=num(need_gb, 1, group=False),
                          drive=Path(facts["disk"]["data_dir"]).anchor or "C:", free=num(free, 1, group=False)))
    if not linux and not facts.get("webview2"):
        warnings.append(t("The Microsoft Edge WebView2 Runtime wasn't found. IEUStudia's window needs it (it comes with "
                          "Windows 11 and up-to-date Windows 10)."))
    driver = _driver_number(gpu.get("driver", ""))
    cap = _cap_number(gpu.get("compute_cap"))
    old_card = bool(cap) and cap < MIN_COMPUTE
    failed = card_failed(gpu) if gpu.get("present") else {}
    cuda = (bool(gpu.get("present")) and driver >= MIN_CUDA_DRIVER and int(gpu.get("vram_mb") or 0) >= 3500
            and not old_card and not failed)
    if gpu.get("present") and not cuda:
        if driver and driver < MIN_CUDA_DRIVER:
            warnings.append(t("Your NVIDIA driver ({driver}) is too old for transcribing on the graphics card. Update it "
                              "from nvidia.com; until then the processor is used.", driver=gpu["driver"]))
        elif old_card:
            warnings.append(t("The {name} is too old a graphics card for the speech recognition (it needs a GeForce GTX "
                              "10 series or newer), so the processor transcribes.", name=gpu["name"]))
        elif failed:
            warnings.append(t("Your {name} couldn't run the speech model when IEUStudia tried it, so the processor "
                              "transcribes. Updating the NVIDIA driver (nvidia.com) may fix this: IEUStudia tries the "
                              "card again after a driver update.", name=gpu["name"]))
        else:
            warnings.append(t("The {name} has too little memory for the speech model; the processor is used.",
                              name=gpu["name"]))
    if not cuda:
        warnings.append(t("Without a suitable NVIDIA graphics card, the processor transcribes: with the small model an "
                          "hour of lecture takes roughly 20 minutes to an hour, depending on the processor."))
    if not cpu.get("avx2") and cpu.get("avx"):
        warnings.append(t("This is an older type of processor (no AVX2), so transcription on it will be slow."))
    eight = eight_gb(facts)
    if ram and not eight:
        warnings.append(t("With less than 8 GB of memory, use the small speech model and close other apps while "
                          "transcribing."))
    vram = int(gpu.get("vram_mb") or 0)
    # On the processor only the small model is practical: measured on a recent laptop CPU, small took 15-30 min
    # per hour of speech, medium more than 4 hours (every passage is decoded in English and in Turkish).
    model = "large-v3-turbo" if cuda else "small"
    llm = ""
    if cuda and vram >= 7000:                    # the 4B model needs the GPU; on a processor it is far too slow
        llm = "qwen3-4b"
    elif eight:
        llm = "qwen3-1.7b"
    models = {}
    for key in MODEL_PRESETS:
        ok = cuda or key in ("small", "medium")
        why = "" if ok else t("needs an NVIDIA graphics card (on the processor it would take hours per lecture)")
        if ok and key == "medium" and not eight:
            ok, why = False, t("needs 8 GB of memory")
        models[key] = {"ok": ok, "why": why}
    return {"ok": not blockers, "blockers": blockers, "warnings": warnings, "gpu": cuda,
            "recommended_model": model, "recommended_llm": llm, "models": models,
            "summary": _summary(facts, cuda)}


def _summary(facts: dict[str, Any], cuda: bool) -> list[dict[str, str]]:
    w, cpu, gpu = facts["windows"], facts["cpu"], facts["gpu"]
    cpu_value = (t("{name} · {n} threads", name=cpu["name"], n=cpu["cores"]) if cpu.get("avx2") else
                 t("{name} · {n} threads · older type, slower", name=cpu["name"], n=cpu["cores"]) if cpu.get("avx") else
                 t("{name} · {n} threads · can't transcribe (no AVX)", name=cpu["name"], n=cpu["cores"]))
    system = ({"label": t("System"), "value": t("{name} · Linux {version}", name=w["name"], version=w.get("version") or ""),
               "ok": "yes" if w.get("x64") else "no"} if w.get("linux") else
              {"label": "Windows", "value": t("{name} {version} (build {build})", name=w["name"],
                                              version=w.get("version") or "", build=w["build"]).replace("  ", " "),
               "ok": "yes" if w.get("x64") and w["build"] >= MIN_BUILD else "no"})
    rows = [system,
            {"label": t("Processor"), "value": cpu_value,
             "ok": "yes" if cpu.get("avx2") else ("warn" if cpu.get("avx") else "no")},
            {"label": t("Memory"), "value": t("{gb} GB", gb=num(facts.get("ram_installed_gb") or facts["ram_gb"])),
             "ok": "yes" if eight_gb(facts) else ("warn" if enough_memory(facts) else "no")},
            {"label": t("Graphics"), "value": (t("{name} · {gb} GB · driver {driver}", name=gpu["name"], gb=num(gpu["vram_mb"] / 1024),
                                                 driver=gpu["driver"]) if gpu.get("present")
                                               else t("No NVIDIA graphics card (the processor transcribes)")),
             "ok": "yes" if cuda else "warn"},
            {"label": t("Free disk space"), "value": t("{gb} GB on {drive}", gb=num(facts["disk"]["data_free_gb"], group=False),
                                                       drive=Path(facts["disk"]["data_dir"]).anchor),
             "ok": "yes" if facts["disk"]["data_free_gb"] >= 10 else ("warn" if facts["disk"]["data_free_gb"] >= 4 else "no")}]
    return rows
