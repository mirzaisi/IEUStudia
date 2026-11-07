"""New versions: IEUStudia looks at the project's latest release on GitHub (at most about once a day, unless that is
turned off in Settings) and can update itself. The installer of that release is downloaded, checked against the
release's SHA256SUMS.txt and run once the window has closed; it installs over this copy and opens IEUStudia again.

Downloaded by the app rather than a browser, the installer carries no "came from the internet" mark, so Windows
doesn't stop it with a SmartScreen warning. The checksum is what makes sure it is the file that was released."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from . import __version__
from .i18n import t

log = logging.getLogger("studia")
REPO = "mirzaisi/IEUStudia"
RELEASES = f"https://github.com/{REPO}/releases"
CHECK_EVERY_S = 20 * 3600       # about once a day (a little less, so a daily start doesn't keep just missing it)
RETRY_S = 3600                  # after a failed look (offline, or GitHub's hourly limit on a shared network)
EARLY_EXIT_S = 6                # the installer runs for the whole install: one that ends sooner didn't run
UPDATE_ROOM_MB = 700            # the installer, and the new version beside the old one until it takes its place
_lock = threading.Lock()


def _parse(version: str) -> tuple[int, ...]:
    """ "v2.10.1" → (2, 10, 1); "2.3" → (2, 3, 0); anything else → ()."""
    m = re.match(r"v?(\d+(?:\.\d+)*)", (version or "").strip())
    if not m:
        return ()
    parts = [int(x) for x in m.group(1).split(".")]
    return tuple(parts + [0] * (3 - len(parts)))


def newer(version: str, than: str = __version__) -> bool:
    a, b = _parse(version), _parse(than)
    return bool(a) and a > b


def installed() -> bool:
    """An installed copy (Inno Setup put its uninstaller next to the exe), which an installer can update; not a run
    from source or from the portable zip."""
    return bool(getattr(sys, "frozen", False)) and Path(sys.executable).with_name("unins000.exe").exists()


# ----------------------------------------------------------------------------- what is known
def _dir() -> Path:
    from .paths import app_dir

    d = app_dir() / "updates"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _state_file() -> Path:
    from .paths import app_dir

    return app_dir() / "update.json"


def _load() -> dict[str, Any]:
    try:
        d = json.loads(_state_file().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(state: dict[str, Any]) -> None:
    p = _state_file()
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        log.debug("Could not save %s", p, exc_info=True)


def status() -> dict[str, Any]:
    """What the last look found, without going online. When the last update's installer didn't bring the new version
    (Windows or an antivirus stopped it, it was cancelled, it failed), that is said once: ``failed`` with the version,
    and the error the update dialog shows (``state`` "error"), until :func:`note_version` at the next start."""
    st = _load()
    latest = st.get("latest") if isinstance(st.get("latest"), dict) else {}
    version = str(latest.get("version") or "")
    out = {"current": __version__, "available": newer(version), "version": version,
           "notes": str(latest.get("notes") or ""), "page": str(latest.get("page") or f"{RELEASES}/latest"),
           "can_install": installed() and bool(latest.get("installer")),
           "checked_at": float(st.get("checked_at") or 0), "error": str(st.get("error") or "")}
    tried = st.get("attempt") if isinstance(st.get("attempt"), dict) else {}
    if newer(str(tried.get("version") or ""), __version__):
        out.update(failed=str(tried["version"]), state="error", error=t(
            "The update to {version} didn't finish, so you still have {current}; nothing of yours changed. Windows or "
            "an antivirus may have stopped its installer. Try again; if it stops again, download the installer from "
            "the release page and run it yourself.", version=tried["version"], current=__version__))
    return out


def due() -> bool:
    st, now = _load(), time.time()
    return now - float(st.get("checked_at") or 0) >= CHECK_EVERY_S and now - float(st.get("tried_at") or 0) >= RETRY_S


# ----------------------------------------------------------------------------- looking
def _session() -> Any:
    from .net import plain_session

    s = plain_session()
    s.headers["User-Agent"] = f"IEUStudia/{__version__} (+https://github.com/{REPO})"
    return s


def _latest(s: Any) -> dict[str, Any]:
    """The latest release: from GitHub's API (with its notes and files), or, when the API says no (its hourly limit
    is per address, which a campus network shares), from where github.com's "latest" link points."""
    feed = os.environ.get("STUDIA_UPDATE_FEED") or f"https://api.github.com/repos/{REPO}/releases/latest"   # (tests)
    r = s.get(feed, timeout=(10, 20), headers={"Accept": "application/vnd.github+json"})
    try:
        d = r.json() if r.status_code == 200 else None
    except ValueError:                                  # a Wi-Fi sign-in page or a network filter, not GitHub
        d = None
    if isinstance(d, dict) and d.get("tag_name"):
        tag = str(d.get("tag_name") or "")
        assets: Optional[dict[str, str]] = {str(a.get("name")): str(a.get("browser_download_url"))
                                            for a in d.get("assets") or [] if a.get("browser_download_url")}
        notes, page = str(d.get("body") or ""), str(d.get("html_url") or "")
    else:
        r = s.get(f"{RELEASES}/latest", timeout=(10, 20), allow_redirects=False)
        m = re.search(r"/releases/tag/([^/?#]+)", r.headers.get("Location") or "")
        if not m:
            raise RuntimeError(t("GitHub didn't answer as expected (a Wi-Fi sign-in page or a network filter can do "
                                 "this). Try again later. (HTTP {code})", code=r.status_code))
        tag, assets, notes, page = m.group(1), None, "", ""
    version = tag.lstrip("v")
    if not _parse(version):
        raise RuntimeError(t("GitHub's newest release has no version number ({tag}).", tag=repr(tag)))
    name, base = f"IEUStudia-Setup-{version}.exe", f"{RELEASES}/download/{tag}"
    if assets is None:                                  # not listed: the release build always uploads both
        installer, sums = f"{base}/{name}", f"{base}/SHA256SUMS.txt"
    else:
        installer, sums = assets.get(name, ""), assets.get("SHA256SUMS.txt", "")
    return {"version": version, "tag": tag, "notes": notes[:6000], "page": page or f"{RELEASES}/tag/{tag}",
            "installer": installer if sums else "", "sums": sums}


def check(force: bool = False, session: Any = None) -> dict[str, Any]:
    """Look for a new version (unless the last look is recent enough and ``force`` is off); returns status()."""
    with _lock:
        if not force and not due():
            return status()
        st = _load()
        st["tried_at"] = time.time()
        try:
            st["latest"] = _latest(session or _session())
            st["checked_at"], st["error"] = time.time(), ""
            log.info("Newest IEUStudia release: %s (this is %s)", st["latest"]["version"], __version__)
        except Exception as exc:  # noqa: BLE001 - offline, GitHub's limit, an odd answer: try again later
            from .net import plain_error

            st["error"] = plain_error(exc)
            log.info("Could not look for a new version: %s", exc)
        _save(st)
        return status()


def note_version() -> str:
    """The version the app ran as the last time, when that was another one (it was just updated); "" otherwise. Once
    a start has seen it (status() said whether the last update failed), the update's attempt is forgotten."""
    with _lock:
        st = _load()
        before = str(st.get("ran") or "")
        if before == __version__ and "attempt" not in st:
            return ""
        st.pop("attempt", None)
        st["ran"] = __version__
        _save(st)
        return before if before != __version__ else ""


def tell_once(version: str) -> bool:
    """True the first time this is asked for a version (the tray helper's notification, once per version)."""
    with _lock:
        st = _load()
        if st.get("told") == version:
            return False
        st["told"] = version
        _save(st)
        return True


# ----------------------------------------------------------------------------- updating
def _expected_hash(s: Any, sums_url: str, name: str) -> str:
    r = s.get(sums_url, timeout=(10, 30))
    r.raise_for_status()
    for line in r.text.splitlines():
        parts = line.strip().split()
        if len(parts) == 2 and parts[1].lstrip("*") == name and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            return parts[0].lower()
    raise RuntimeError(t("The release doesn't list a checksum for {name}, so it isn't installed.", name=name))


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while buf := fh.read(4 * 1024 * 1024):
            h.update(buf)
    return h.hexdigest()


def _room_for_update() -> None:
    """The installer is downloaded, then the new version is written beside the old one before it takes its place:
    without room for that the update would fail halfway, so it says so before it starts."""
    import shutil

    places = {_dir()}
    if getattr(sys, "frozen", False):
        places.add(Path(sys.executable).parent)
    for place in places:
        try:
            free = shutil.disk_usage(place).free
        except OSError:
            continue
        if free < UPDATE_ROOM_MB * 1024 * 1024:
            raise RuntimeError(t("The update needs about {need} MB of free disk space on {drive}, and {free} MB are free. "
                                 "Free up some space, then try again.", need=UPDATE_ROOM_MB,
                                 drive=place.anchor or str(place), free=free // (1024 * 1024)))


def download(progress: Optional[Callable[[int, int], None]] = None,
             cancel: Optional[threading.Event] = None) -> Path:
    """The new version's installer in ``<app data>\\updates``, checked against the release's checksums."""
    from . import httpdl

    latest = _load().get("latest") or {}
    url, sums = str(latest.get("installer") or ""), str(latest.get("sums") or "")
    if not url or not newer(str(latest.get("version") or "")):
        raise RuntimeError(t("There's no newer version to install."))
    _room_for_update()
    s = _session()
    name = url.rsplit("/", 1)[-1]
    dest = _dir() / name
    try:                                 # a stop says why in plain words (no internet, GitHub blocked here, …)
        expected = _expected_hash(s, sums, name)
        httpdl.download(url, dest, session=s, progress=progress, cancel=cancel, parts=4)
    except Exception as exc:  # noqa: BLE001 - the same error, worded for the student
        httpdl.raise_explained(exc, "github.com")
    if _sha256(dest) != expected:
        dest.unlink(missing_ok=True)
        raise RuntimeError(t("The downloaded installer isn't the released file (its checksum doesn't match), so it "
                             "wasn't run. Nothing changed; try again later."))
    log.info("Update %s downloaded and checked", name)
    return dest


def run_installer(path: Path) -> bool:
    """Start the installer once this process is about to end: it installs without questions (showing its progress)
    and opens IEUStudia again (/RELAUNCH=1, see packaging/IEUStudia.iss), also when it fails (the old copy stays).

    The installer runs as long as the install does, so one that ends within EARLY_EXIT_S didn't run (an antivirus
    stopped it as it started, say): False then, and the app says so and opens again. The attempt is noted first, so
    the next start also says it when the new version didn't come up for another reason (see status())."""
    from .launch import DETACHED

    m = re.match(r"IEUStudia-Setup-(.+?)\.exe$", path.name)
    with _lock:
        st = _load()
        st["attempt"] = {"version": m.group(1) if m else "", "at": time.time()}
        _save(st)
    cmd = [str(path), "/SILENT", "/NORESTART", "/RELAUNCH=1"]
    try:                                         # what the installer did, beside IEUStudia's own log (Logs button)
        from .paths import logs_dir

        cmd.append(f"/LOG={logs_dir() / ('update-install-' + (m.group(1) if m else 'x') + '.log')}")
    except Exception:  # noqa: BLE001 - only a log
        pass
    try:
        proc = subprocess.Popen(cmd, close_fds=True, creationflags=DETACHED)
    except OSError:
        log.exception("Could not start the update %s", path)
        return False
    log.info("Started the update: %s", path.name)
    try:
        code = proc.wait(timeout=EARLY_EXIT_S)
    except subprocess.TimeoutExpired:
        return True                                  # installing: it opens IEUStudia again when it's done
    if code == 0:
        return True
    log.warning("The update's installer ended at once (exit code %s): it didn't run", code)
    return False


def cleanup() -> None:
    """Installers of versions that are installed now (or older) aren't needed any more."""
    try:
        for f in _dir().glob("IEUStudia-Setup-*"):
            m = re.match(r"IEUStudia-Setup-(.+?)\.exe", f.name)
            if m and not newer(m.group(1)):
                f.unlink(missing_ok=True)
    except OSError:
        log.debug("Could not tidy the updates folder", exc_info=True)
