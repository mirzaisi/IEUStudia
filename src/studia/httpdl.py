"""Robust HTTP file download: parallel Range requests, per-part retries, resume, cancellation.

Used for the speech and offline AI models, the graphics card's files and the update's installer (Panopto media uses
it too, see ``panopto.py``). A download waits out a dropped connection (each part goes on from what it has on disk),
asks for a fresh link when the one it had expired (Hugging Face's last about an hour, GitHub's a few minutes), and
waits for an antivirus that holds the finished file for a moment. :func:`explain` says why one stopped, in words for
anyone and with what to do.
"""

from __future__ import annotations

import errno
import json
import logging
import os
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, NamedTuple, Optional
from urllib.parse import urlparse

import requests

from .cancel import Cancelled, check
from .i18n import t
from .net import connection_lost, plain_session

log = logging.getLogger("studia")
ProgressFn = Callable[[int, int], None]  # (bytes_done, bytes_total)

CHUNK = 256 * 1024
MIN_PART = 8 * 1024 * 1024
PATIENCE_S = 180.0          # a part that gets nothing keeps trying this long (a Wi-Fi drop, a PC woken from sleep);
#                             the clock starts again with every byte, so a connection that comes and goes never adds up
HOLD_S = 30.0               # how long a finished file may be held by an antivirus scanning it
TRIES = 8                   # tries of a part for a server's error or a file of ours that can't be written
EXPIRED = (401, 403, 404, 410)    # what a signed link answers once it expired: the original address gives a fresh one
PASSING = (408, 429, 500, 502, 503, 504)


class NotTheFile(requests.HTTPError):
    """A web page came instead of the file (a Wi-Fi sign-in page, a network filter's page): the message says so."""


class _Changed(IOError):
    """The file on the server is another one now: the parts on disk don't fit it (the next try starts afresh)."""


class _Look(NamedTuple):
    url: str           # where the redirects led
    total: int
    ranges: bool
    version: str       # the file's ETag or Last-Modified
    html: bool         # a web page answered


def _sleep(seconds: float, cancel: Optional[threading.Event]) -> None:
    end = time.monotonic() + seconds
    while True:
        check(cancel)
        left = end - time.monotonic()
        if left <= 0:
            return
        time.sleep(min(0.25, left))


def _look(session: requests.Session, url: str, attempts: int = 3,
          cancel: Optional[threading.Event] = None) -> _Look:
    for attempt in range(1, attempts + 1):
        try:
            with session.get(url, headers={"Range": "bytes=0-0"}, stream=True, timeout=(15, 45),
                             allow_redirects=True) as r:
                r.raise_for_status()
                version = r.headers.get("ETag") or r.headers.get("Last-Modified") or ""
                html = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower() == "text/html"
                if r.status_code == 206 and "/" in r.headers.get("Content-Range", ""):
                    return _Look(r.url, int(r.headers["Content-Range"].rsplit("/", 1)[1]), True, version, html)
                return _Look(r.url, int(r.headers.get("Content-Length") or 0), False, version, html)
        except (requests.ConnectionError, requests.Timeout):
            if attempt == attempts:
                raise
            _sleep(2.0 * attempt, cancel)
    raise IOError("unreachable")


def _probe(session: requests.Session, url: str, attempts: int = 3,
           cancel: Optional[threading.Event] = None) -> tuple[str, int, bool, str]:
    """Follow redirects with a 1-byte range request; return (final_url, total_size, ranges_ok, version), the version
    being the file's ETag or Last-Modified (to tell a changed file from the one whose parts are on disk)."""
    found = _look(session, url, attempts, cancel)
    return found.url, found.total, found.ranges, found.version


class _Link:
    """The address the parts download from, shared by them. A signed link expires (Hugging Face's after about an
    hour): the first part that is refused asks the original address for a fresh one, and the others use it."""

    RENEW_WINDOW_S = 600.0      # at most 3 fresh links in this long: more is a real refusal. (A cap for the whole
                                # download failed one of hours on a slow line after its 4th expiry, as "refused, try a
                                # hotspot", when Try again would just have gone on.)

    def __init__(self, session: requests.Session, url: str, final: str, total: int,
                 cancel: Optional[threading.Event]) -> None:
        self.session, self.url, self.final, self.total, self.cancel = session, url, final, total, cancel
        self.lock = threading.Lock()
        self.renewals: list[float] = []

    def renew(self, refused: str) -> bool:
        """A fresh link after ``refused`` was turned down: False when none helps (the original gives the same)."""
        with self.lock:
            if self.final != refused:                  # another part got a fresh one meanwhile
                return True
            now = time.monotonic()
            self.renewals = [at for at in self.renewals if now - at < self.RENEW_WINDOW_S]
            if len(self.renewals) >= 3:
                return False
            self.renewals.append(now)
            found = _look(self.session, self.url, cancel=self.cancel)   # refused itself: that answer is raised
            if self.total and found.total != self.total:
                raise _Changed(t("The file changed on the server while it downloaded. Try again: it starts afresh."))
            if found.url == refused:
                return False
            log.info("The download link had expired; a fresh one was given")
            self.final = found.url
            return True


def _fetch_range(session: requests.Session, link: _Link, part: Path, start: int, end: int,
                 counter: list[int], lock: threading.Lock, cancel: Optional[threading.Event],
                 patience: float = PATIENCE_S) -> None:
    """Download bytes [start, end] into ``part``, resuming whatever is already there. A dropped connection is
    waited out for ``patience`` seconds without a byte (it starts again once data flows); a server's error or a
    file that can't be written is tried TRIES times; a refused link is renewed once for all parts."""
    want = end - start + 1
    since: Optional[float] = None          # since when this part gets nothing
    tries = 0
    while True:
        have = part.stat().st_size if part.exists() else 0
        if have > want:  # corrupt leftover: start this part again
            part.unlink()
            with lock:
                counter[0] -= have
            have = 0
        if have == want:
            return
        url = link.final
        try:
            with session.get(url, headers={"Range": f"bytes={start + have}-{end}"},
                             stream=True, timeout=(15, 60)) as r:
                if r.status_code in EXPIRED:
                    if link.renew(url):
                        continue
                    raise requests.HTTPError(t("expected HTTP 206, got {code}", code=r.status_code), response=r)
                whole = r.status_code == 200 and start + have == 0 and end + 1 == link.total   # one part: all of it
                if r.status_code != 206 and not whole:              # (200: the server ignored the range)
                    raise requests.HTTPError(t("expected HTTP 206, got {code}", code=r.status_code), response=r)
                if whole and part.exists():
                    part.unlink()
                with open(part, "ab") as fh:
                    for chunk in r.iter_content(CHUNK):
                        check(cancel)
                        if chunk:
                            fh.write(chunk)
                            with lock:
                                counter[0] += len(chunk)
            got = part.stat().st_size
            if got == want:
                return
            if got <= have:                               # answered, but with nothing: as good as an error
                raise IOError(t("Download did not complete; please retry."))
            since, tries = None, 0                        # cut off halfway: it goes on at once
        except Cancelled:
            raise
        except (requests.RequestException, OSError) as exc:
            now = time.monotonic()
            if (part.stat().st_size if part.exists() else 0) > have:
                since, tries = None, 0                    # it got something: a new drop gets the whole time again
            since = since or now
            tries += 1
            status = getattr(getattr(exc, "response", None), "status_code", 0) or 0
            dropped = connection_lost(exc)
            lasting = (isinstance(exc, _Changed) or getattr(exc, "errno", None) == errno.ENOSPC
                       or (isinstance(exc, requests.HTTPError) and status not in (0, 200, *PASSING)))
            if lasting or (dropped and now - since >= patience) or (not dropped and tries >= TRIES):
                raise
            log.info("Download part retry %d (%s)", tries, exc)
            _sleep(min(20.0, 0.8 * 2 ** min(tries, 5)), cancel)


def move_into_place(src: Path, dst: Path, cancel: Optional[threading.Event] = None,
                    hold: Optional[float] = None) -> None:
    """``os.replace``, waited out while an antivirus holds one of the files: it opens a new file to scan it, often
    just as the download finishes. Still held after ``hold`` seconds (HOLD_S): the PermissionError is raised (the
    file stays, so the next try only moves it)."""
    end = time.monotonic() + (HOLD_S if hold is None else hold)
    while True:
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if time.monotonic() >= end:
                raise
            log.info("%s is held by another program (an antivirus scanning it?); waiting", Path(src).name)
        _sleep(0.5, cancel)


def _join(part_files: list[Path], tmp: Path, cancel: Optional[threading.Event] = None) -> None:
    """The parts, in order, as one file: the first part grows and each other one is deleted once appended, so the
    disk never holds the download twice (a 3 GB model needs about 3 GB, not 6), only one part more. Without room for
    that it stops before touching anything, so the next try only joins."""
    need = max((pf.stat().st_size for pf in part_files[1:]), default=0)
    if shutil.disk_usage(tmp.parent).free < need + 16 * 1024 * 1024:
        raise OSError(errno.ENOSPC, "No space left on device", str(tmp.parent))
    move_into_place(part_files[0], tmp, cancel)
    with open(tmp, "ab") as out:
        for pf in part_files[1:]:
            with open(pf, "rb") as fh:
                while buf := fh.read(4 * 1024 * 1024):
                    out.write(buf)
            try:                                 # already appended: a part an antivirus still holds is only left over
                pf.unlink()
            except OSError as exc:
                log.info("Could not delete %s: %s", pf.name, exc)


def _stream_plain(session: requests.Session, url: str, tmp: Path, counter: list[int],
                  lock: threading.Lock, cancel: Optional[threading.Event]) -> None:
    with session.get(url, stream=True, timeout=(15, 60)) as r:
        r.raise_for_status()
        with open(tmp, "wb") as fh:
            for chunk in r.iter_content(CHUNK):
                check(cancel)
                if chunk:
                    fh.write(chunk)
                    with lock:
                        counter[0] += len(chunk)


def download(url: str, dest: Path, *, session: Optional[requests.Session] = None,
             progress: Optional[ProgressFn] = None, cancel: Optional[threading.Event] = None,
             parts: int = 6, patience: float = PATIENCE_S) -> Path:
    """Download ``url`` to ``dest`` atomically (``.partN`` files make it resumable). A file that was all downloaded
    the last time, but couldn't be moved into place (an antivirus held it), is only moved."""
    session = session or plain_session()
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    found = _look(session, url, cancel=cancel)
    if found.html:
        raise NotTheFile(t("Instead of the file, {site} sent a web page. A Wi-Fi sign-in page or a network filter "
                           "can do this: open any website in your browser (sign in to the Wi-Fi if it asks), then "
                           "try again.", site=urlparse(url).hostname or urlparse(found.url).hostname or ""))
    final_url, total, ranges_ok, version = found.url, found.total, found.ranges, found.version
    if dest.exists() and total and dest.stat().st_size == total:
        if progress:
            progress(total, total)
        return dest
    stamp = {"total": total, "version": version}
    marker = dest.with_name(dest.name + ".parts.json")
    try:
        before = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        before = None
    if before is not None and before != stamp:      # the file changed (e.g. re-encoded): its old parts don't fit
        log.info("%s changed since its partial download; starting again", dest.name)
        for old in dest.parent.glob(dest.name + ".part*"):
            old.unlink(missing_ok=True)
    marker.write_text(json.dumps(stamp), encoding="utf-8")

    if not ranges_ok or total < MIN_PART:
        parts = 1
    bounds: list[tuple[int, int]] = []
    if ranges_ok and total:
        size = -(-total // parts)
        bounds = [(i * size, min(total, (i + 1) * size) - 1) for i in range(parts) if i * size < total]
    part_files = [dest.with_name(f"{dest.name}.part{i}") for i in range(len(bounds))]
    tmp = dest.with_name(dest.name + ".part")
    if before == stamp and total and tmp.is_file() and tmp.stat().st_size == total:
        log.info("%s was all downloaded the last time; only moving it into place", dest.name)
    else:
        _fetch(session, url, final_url, total, bounds, part_files, tmp, progress, cancel, patience)
    if total and tmp.stat().st_size != total:
        tmp.unlink(missing_ok=True)
        for pf in part_files:
            pf.unlink(missing_ok=True)
        raise IOError(t("Download size mismatch for {name}; please retry.", name=dest.name))
    move_into_place(tmp, dest, cancel)
    for pf in part_files:                            # the download is complete: a part left over doesn't matter now
        try:
            pf.unlink(missing_ok=True)
        except OSError:
            pass
    if not any(pf.exists() for pf in part_files):    # one left over keeps the stamp, so a changed file won't reuse it
        marker.unlink(missing_ok=True)
    if progress:
        size = dest.stat().st_size
        progress(size, total or size)
    return dest


def _fetch(session: requests.Session, url: str, final_url: str, total: int, bounds: list[tuple[int, int]],
           part_files: list[Path], tmp: Path, progress: Optional[ProgressFn], cancel: Optional[threading.Event],
           patience: float) -> None:
    """The bytes into ``tmp``: the parts side by side, then joined (or one plain stream when ranges don't work)."""
    lock = threading.Lock()
    counter = [sum(min(p.stat().st_size, e - s + 1) for p, (s, e) in zip(part_files, bounds) if p.exists())]
    stop = threading.Event()

    def reporter() -> None:
        while not stop.wait(0.25):
            with lock:
                done = counter[0]
            if progress:
                progress(min(done, total) if total else done, total)

    rep = threading.Thread(target=reporter, name="dl-progress", daemon=True)
    rep.start()
    try:
        if not bounds:
            _stream_plain(session, final_url, tmp, counter, lock, cancel)
        else:
            link = _Link(session, url, final_url, total, cancel)
            with ThreadPoolExecutor(max_workers=len(bounds), thread_name_prefix="dl") as pool:
                futures = [pool.submit(_fetch_range, session, link, pf, s, e, counter, lock, cancel, patience)
                           for pf, (s, e) in zip(part_files, bounds)]
                for f in futures:
                    f.result()
            _join(part_files, tmp, cancel)
    finally:
        stop.set()
        rep.join(timeout=1)


# ----------------------------------------------------------------------------- why it stopped
def explain(exc: BaseException, site: str = "") -> BaseException:
    """Why a download stopped, in words for anyone and with what to do, as the same kind of error (a caller still
    tells a connection problem from a full disk): ``exc`` itself when its own message says it well enough. ``site``
    is where it comes from as a student would know it (huggingface.co, pypi.org, github.com: the servers behind them
    have other names). The original stays chained to it, for the log."""
    from . import net

    if isinstance(exc, (Cancelled, NotTheFile, net.NetworkError)):
        return exc
    if isinstance(exc, requests.exceptions.SSLError):        # an antivirus scanning HTTPS, a Wi-Fi sign-in page
        return net.NetworkError(net.plain_error(exc, f"https://{site}/" if site else ""))
    if connection_lost(exc):
        if site and net.online():
            return net.NetworkError(t(
                "{site} can't be reached from this network, although the internet works. A network filter (on a "
                "school, dorm or company network) or an antivirus may block it: try another network, such as your "
                "phone's hotspot, or allow IEUStudia in your antivirus.", site=site))
        return net.NetworkError(t("This PC can't reach the internet right now. Check the Wi-Fi or the cable (on "
                                  "campus Wi-Fi, make sure you are signed in to it), then try again."))
    response = getattr(exc, "response", None)
    if isinstance(exc, requests.HTTPError) and response is not None:
        code = response.status_code
        where = site or urlparse(getattr(response, "url", "") or "").hostname or ""
        if code in (403, 407, 451):
            return requests.HTTPError(t(
                "{site} refused the download (error {code}). A network filter (on a school, dorm or company "
                "network) can do this: try another network, such as your phone's hotspot.", site=where, code=code))
        # 401: Hugging Face answers it for a file or repo that is gone, renamed or private, not only a filter: another
        # network can't help then (and a student would spend mobile data trying), a newer IEUStudia can
        if code in (401, 404, 410):
            return requests.HTTPError(t(
                "{site} doesn't have this file any more (error {code}). Look for a new version of IEUStudia in "
                "Settings → About: it may get the file from its new place.", site=where, code=code))
        if code == 429:
            return requests.HTTPError(t("{site} is limiting downloads right now (error 429). Wait a few minutes, "
                                        "then try again.", site=where))
        if where:                                             # its own name, not its download server's
            return requests.HTTPError(t("{host} answered with an error ({code}). Try again in a while.", host=where,
                                        code=code))
        return exc
    if isinstance(exc, FileNotFoundError) and exc.filename:   # one of the download's own files was taken away
        return OSError(t("{name} disappeared while it was downloading. An antivirus may have removed it: look in its "
                         "quarantine or history, allow IEUStudia there, then try again.",
                         name=os.path.basename(str(exc.filename))))
    return exc


def raise_explained(exc: BaseException, site: str) -> None:
    """Raise ``exc`` as :func:`explain` words it (``raise`` in an ``except`` block keeps the traceback)."""
    better = explain(exc, site)
    if better is exc:
        raise exc
    raise better from exc
