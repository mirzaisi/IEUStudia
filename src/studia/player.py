"""The lecture player (4.0.0): a lecture played in the app, streamed from Panopto through a small proxy on 127.0.0.1.

Why a proxy: Panopto's media addresses carry no signed token; they work with the app's own Panopto sign-in (its
cookies, and the course opened through IEULearn lately: ``SyncService.ensure_panopto``). The app's page is a file:// page
in WebView2, which keeps cookies of its own: a ``<video>`` pointed at Panopto would get the sign-in page. So the page
plays ``http://127.0.0.1:<port>/m/<token>/lecture.mp4`` and the proxy fetches the lecture with the app's session.

The proxy (:class:`Proxy`: started on first use, on a daemon thread) serves only tokens it handed out (random; one ends
when the player closes, or after TOKEN_LIFE_S), and only to 127.0.0.1. A request's Range goes on to Panopto as it came
(Panopto keeps the podcast's ``moov`` box at the end, so the player asks for the end first), the status and range
headers come back as Panopto sent them, and the body goes on in PIECE pieces; a connection the player drops (a seek)
ends quietly. A refusal (401/403, or Panopto's sign-in page) signs in to Panopto again and asks DeliveryInfo for a fresh
address, once. Panopto's address carries the session's InvocationID: it is never logged.

What plays, best first (:func:`open_recording`): the podcast MP4 (picture and sound, ``PodcastStreams``), the
sound-only podcast (the page shows the captured screens as the picture), the lecture's own file on this PC (a recording
transcribed from a file, or the audio kept after transcribing), which also plays when Panopto can't be reached.
Nothing is reported to Panopto: plays in the app don't count in its viewing statistics.
"""

from __future__ import annotations

import dataclasses as dc
import http.server
import ipaddress
import json
import logging
import os
import re
import secrets
import threading
import time
import types
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

import requests

from . import store
from .health import ServiceChanged
from .i18n import t
from .ieulearn import AuthError, NeedLogin
from .net import connection_lost
from .panopto import BASE, NeedPanoptoAuth, Panopto
from .paths import audio_cache_dir, transcripts_dir
from .slides import jpeg_size

log = logging.getLogger("studia")

PIECE = 256 * 1024                 # the body goes on to the player in pieces this big
TOKEN_LIFE_S = 6 * 3600.0          # a token the player never closed (the window closed, a crash) ends after this long
TIMEOUT = (15, 60)                 # Panopto: to connect, and between two pieces of its answer
IDLE_S = 60                        # a connection the player keeps without reading (paused) is let go after this long
TYPES = {"video": "video/mp4", "audio": "audio/mp4"}
FILE_TYPES = {".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/mp4", ".webm": "video/webm",
              ".mkv": "video/x-matroska", ".m4a": "audio/mp4", ".aac": "audio/aac", ".mp3": "audio/mpeg",
              ".wav": "audio/wav", ".ogg": "audio/ogg", ".opus": "audio/ogg", ".flac": "audio/flac"}
_PATH = re.compile(r"/m/([A-Za-z0-9_-]{16,64})/[A-Za-z0-9_.-]{1,40}")
_RANGE = re.compile(r"\s*bytes=(\d*)-(\d*)\s*")


@dc.dataclass
class Source:
    """What a token plays: Panopto's address (``url``, fetched with ``session``) or a file on this PC (``path``)."""

    kind: str                                    # "video", "audio" (Panopto's podcasts) or "file"
    url: str = ""
    path: str = ""
    session: Any = None                          # a requests.Session with the app's Panopto sign-in
    renew: Optional[Callable[[], tuple[Any, str]]] = None   # a fresh sign-in and address: (session, url)
    content_type: str = "video/mp4"
    final: str = ""                              # where ``url`` led last time (its redirects, followed once)
    total: int = 0                               # the file's size in bytes, once known
    gen: int = 0                                 # + 1 at each fresh address (requests refused with an older one retry)
    why: Optional[BaseException] = None          # why the last fresh sign-in failed
    born: float = dc.field(default_factory=time.monotonic)
    lock: threading.Lock = dc.field(default_factory=threading.Lock, repr=False, compare=False)

    def drop_final(self, url: str) -> None:
        with self.lock:
            if self.final == url:
                self.final = ""


# ----------------------------------------------------------------------------- Panopto's answers
def _sign_in_page(r: Any) -> bool:
    """An answer that is Panopto's sign-in page (or any web page) instead of the lecture: the session ran out."""
    path = urlsplit(str(getattr(r, "url", "") or "")).path
    kind = str((getattr(r, "headers", None) or {}).get("Content-Type") or "").split(";")[0].strip().lower()
    return bool(re.search(r"/(?:Auth|Login)", path, re.I)) or kind == "text/html"


def refused(r: Any) -> bool:
    return r.status_code in (401, 403) or (r.status_code < 300 and _sign_in_page(r))


def _renew(src: Source, gen: int) -> bool:
    """A fresh sign-in and address for ``src`` (``gen``: the address the refused request had). False when it failed."""
    with src.lock:                           # (the other requests refused meanwhile wait for this one)
        if src.gen != gen:                   # another request got a fresh one meanwhile
            return True
        if src.renew is None:
            return False
        try:
            session, url = src.renew()
        except Exception as exc:  # noqa: BLE001 - said to the page as a refusal (open_recording says why)
            src.why = exc
            log.info("Player: Panopto refused the lecture and signing in again didn't help (%s)", type(exc).__name__)
            return False
        src.session, src.url, src.final, src.gen, src.why = session, url, "", src.gen + 1, None
        log.info("Player: Panopto refused the lecture; signed in again and took a fresh address")
        return True


def fetch(src: Source, headers: dict[str, str]) -> requests.Response:
    """Panopto's answer for ``src`` (streamed): from where its address led last time, else from the address itself;
    refused (401/403, the sign-in page), once more with a fresh sign-in and address. Raises requests.RequestException
    when Panopto can't be reached."""
    renewed = False
    while True:
        with src.lock:
            session, url, gen = src.session, src.final or src.url, src.gen
            direct = not src.final
        try:
            r = session.get(url, headers=headers, stream=True, timeout=TIMEOUT)
        except requests.RequestException:
            if direct:
                raise
            src.drop_final(url)              # the address it led to failed: from Panopto's own address again
            continue
        if not direct and (refused(r) or r.status_code in (404, 410)):
            r.close()                        # where it led ran out (a signed link elsewhere expires): from the start
            src.drop_final(url)
            continue
        if refused(r) and not renewed and src.renew is not None:
            renewed = True
            if _renew(src, gen):
                r.close()
                continue
        if direct and r.status_code in (200, 206) and not refused(r):
            with src.lock:
                if src.gen == gen:
                    src.final = str(r.url or url)
        return r


def _size(r: Any) -> int:
    """The whole file's size from an answer to a range request (or a whole one)."""
    cr = str(r.headers.get("Content-Range") or "")
    if "/" in cr and cr.rsplit("/", 1)[1].strip().isdigit():
        return int(cr.rsplit("/", 1)[1])
    cl = str(r.headers.get("Content-Length") or "")
    return int(cl) if r.status_code == 200 and cl.isdigit() else 0


def byte_range(header: Optional[str], size: int) -> Any:
    """A Range header against a file of ``size`` bytes: (first, last) byte, None for the whole file (no Range, or one
    not understood: several ranges), False when it can't be met (416)."""
    m = _RANGE.fullmatch(header or "")
    if not m or not (m.group(1) or m.group(2)):
        return None
    if m.group(1):
        first = int(m.group(1))
        if m.group(2) and int(m.group(2)) < first:
            return None
        last = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
    else:
        n = int(m.group(2))
        if n == 0:
            return False
        first, last = max(0, size - n), size - 1
    return (first, last) if first < size else False


# ----------------------------------------------------------------------------- the proxy
def _local(host: str) -> bool:
    try:
        return ipaddress.ip_address(host.split("%")[0]).is_loopback
    except ValueError:
        return False


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    proxy: "Proxy"

    def handle_error(self, request: Any, client_address: Any) -> None:   # a dropped connection: nothing to tell
        log.debug("Player: a request ended with an error", exc_info=True)


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"            # the player keeps its connection for the next range
    timeout = IDLE_S
    server: _Server

    def log_message(self, *args: Any) -> None:     # its lines would hold the token
        pass

    def do_GET(self) -> None:  # noqa: N802 - http.server's naming
        self._serve(True)

    def do_HEAD(self) -> None:  # noqa: N802
        self._serve(False)

    def _serve(self, body: bool) -> None:
        if not _local(str(self.client_address[0])):
            self._empty(403)
            return
        m = _PATH.fullmatch(urlsplit(self.path).path)
        src = self.server.proxy.get(m.group(1)) if m else None
        if src is None:
            self._empty(404)
        elif src.kind == "file":
            self._file(src, body)
        else:
            self._panopto(src, body)

    def _empty(self, code: int) -> None:
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _panopto(self, src: Source, body: bool) -> None:
        headers = {"Accept-Encoding": "identity"}
        if self.headers.get("Range"):
            headers["Range"] = str(self.headers["Range"])
        try:
            r = fetch(src, headers)
        except requests.RequestException as exc:
            log.info("Player: Panopto couldn't be reached (%s)", type(exc).__name__)
            self._empty(504 if isinstance(exc, requests.Timeout) else 502)
            return
        with r:
            status = r.status_code
            if refused(r):
                self._empty(403)
                return
            if status not in (200, 206, 416):
                log.info("Player: Panopto answered %s", status)
                self._empty(status if 400 <= status < 600 else 502)
                return
            total = _size(r)
            if total:
                src.total = total
            self.send_response(status)
            for name in ("Content-Range", "Content-Length", "Accept-Ranges"):
                if name == "Content-Length" and status == 416:
                    self.send_header(name, "0")           # Panopto's words about the range aren't passed on: had their
                elif r.headers.get(name):                 # length been, the player would wait for them (IDLE_S)
                    self.send_header(name, str(r.headers[name]))
            self.send_header("Content-Type", src.content_type)
            self.send_header("Cache-Control", "no-store")
            if status != 416 and not r.headers.get("Content-Length"):       # its end is where the connection ends
                self.send_header("Connection", "close")
                self.close_connection = True
            self.end_headers()
            if body and status != 416:
                self._pipe(r.iter_content(PIECE))

    def _pipe(self, pieces: Any) -> None:
        try:
            for piece in pieces:
                self.wfile.write(piece)
        except OSError:      # the player let go (a seek, the player closed) or Panopto's answer broke off: the player
            self.close_connection = True       # asks again for what it still needs (requests' errors are OSErrors)

    def _file(self, src: Source, body: bool) -> None:
        try:
            f = open(src.path, "rb")
        except OSError:
            self._empty(404)
            return
        with f:
            size = os.fstat(f.fileno()).st_size
            span = byte_range(self.headers.get("Range"), size)
            if span is False:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            first, last = span or (0, size - 1)
            self.send_response(206 if span else 200)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Type", src.content_type)
            self.send_header("Content-Length", str(max(0, last - first + 1)))
            if span:
                self.send_header("Content-Range", f"bytes {first}-{last}/{size}")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if not body:
                return
            f.seek(first)

            def pieces() -> Any:
                left = last - first + 1
                while left > 0:
                    piece = f.read(min(PIECE, left))
                    if not piece:
                        return
                    left -= len(piece)
                    yield piece

            self._pipe(pieces())


class Proxy:
    """The local server the player's ``<video>`` plays from (one for the app, started on first use)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._lock = threading.Lock()
        self._server: Optional[_Server] = None
        self._sources: dict[str, Source] = {}
        self._clock = clock

    def port(self) -> int:
        """Its port, the server started on first use (OSError when Windows or security software won't let it listen)."""
        with self._lock:
            if self._server is None:
                server = _Server(("127.0.0.1", 0), _Handler)
                server.proxy = self
                threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.5}, name="player-proxy",
                                 daemon=True).start()
                self._server = server
            return int(self._server.server_address[1])

    def add(self, src: Source) -> str:
        token = secrets.token_urlsafe(24)
        src.born = self._clock()
        with self._lock:
            now = self._clock()
            for k in [k for k, s in self._sources.items() if now - s.born > TOKEN_LIFE_S]:
                del self._sources[k]
            self._sources[token] = src
        return token

    def get(self, token: str) -> Optional[Source]:
        with self._lock:
            src = self._sources.get(token)
            if src is not None and self._clock() - src.born > TOKEN_LIFE_S:
                del self._sources[token]
                return None
            return src

    def forget(self, token: str) -> bool:
        with self._lock:
            return self._sources.pop(token, None) is not None

    def url(self, token: str, name: str = "lecture.mp4") -> str:
        return f"http://127.0.0.1:{self.port()}/m/{token}/{name}"

    def close(self) -> None:
        with self._lock:
            server, self._server = self._server, None
            self._sources.clear()
        if server is not None:
            server.shutdown()
            server.server_close()


_proxy = Proxy()


def close(token: str) -> bool:
    """The player closed: its token ends (the connections it still had end with their next piece)."""
    return _proxy.forget(token)


# ----------------------------------------------------------------------------- what plays
class _SignedOut(Exception):
    """Panopto didn't sign in (again)."""


class _Gone(Exception):
    """Panopto has no file for the recording any more (404/410)."""


def part_recording(lib: Any, day_key: str, part: int) -> Any:
    """Part ``part`` of a class day, numbered as the transcript page numbers them (outputs.common.select_parts)."""
    from .outputs.common import select_parts

    recs = lib.day_recordings(day_key)
    parts = select_parts(recs, store.load_transcripts([r.delivery_id for r in recs]))
    return parts[part - 1] if 1 <= part <= len(parts) else None


def local_file(delivery_id: str) -> Optional[Path]:
    """The lecture's own file on this PC: the file a recording was transcribed from, or the lecture's audio (or
    video) kept after transcribing it (Settings: keep the audio)."""
    if delivery_id.startswith("local-"):
        tr = store.load_transcript(delivery_id)
        src = str(((tr.meta or {}).get("source_path") or "") if tr is not None else "")
        return Path(src) if src and os.path.isfile(src) else None
    for ext in (".mp4", ".m4a"):
        p = audio_cache_dir() / f"{delivery_id}{ext}"
        try:
            if p.stat().st_size > 0:
                return p
        except OSError:
            pass
    return None


def screen_texts(delivery_id: str) -> dict[str, dict[str, Any]]:
    """The screens' titles and text, ``<delivery>.screens.json`` (the format of the screen timeline: {"v": 1,
    "screens": {"12": {"title", "text", "from", ...}}}); {} when there is none, or it can't be read."""
    try:
        data = json.loads((transcripts_dir() / f"{delivery_id}.screens.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    screens = data.get("screens") if isinstance(data, dict) else None
    return {str(k): v for k, v in screens.items() if isinstance(v, dict)} if isinstance(screens, dict) else {}


def screens(delivery_id: str) -> list[dict[str, Any]]:
    """The lecture's screens in time order, for the player: each appearance's number (the slide number the
    transcript uses), start and end (``.slides.json``), its picture, and its title and text when the screen timeline
    has them."""
    texts = screen_texts(delivery_id)
    out = []
    for s in sorted(store.load_slides(delivery_id), key=lambda x: x.t):
        w, h = jpeg_size(s.path) if s.path else (0, 0)
        x = texts.get(str(s.index)) or {}
        out.append({"n": s.index, "t0": round(float(s.t), 1), "t1": round(float(s.end), 1),
                    "image": Path(s.path).as_uri() if s.path else "", "w": w, "h": h,
                    "title": " ".join(str(x.get("title") or "").split())[:200],
                    "text": " ".join(str(x.get("text") or "").split())[:600]})
    return out


def address(info: dict[str, Any], delivery_id: str, avoid: str = "") -> tuple[str, str]:
    """The best podcast in a DeliveryInfo answer: (url, "video" | "audio"), ("", "") when Panopto has none yet.
    ``avoid`` "video": the sound-only one when there is one (the video one didn't play, or you chose sound only)."""
    d = info.get("Delivery") if isinstance(info.get("Delivery"), dict) else {}
    video = next((ps["StreamUrl"] for ps in d.get("PodcastStreams") or [] if isinstance(ps, dict)
                  and isinstance(ps.get("StreamUrl"), str) and ps["StreamUrl"].startswith("https://")), "")
    audio = f"{BASE}/Panopto/Podcast/Stream/{delivery_id}.mp4?mediaTargetType=audioPodcast" \
        if d.get("IsAudioPodcastEncodeComplete") else ""
    if video and (avoid != "video" or not audio):
        return video, "video"
    return (audio, "audio") if audio else ("", "")


def _sign_in_again(sync: Any, course_id: str) -> None:
    try:
        sync.ensure_panopto(course_id or None)
    except (ServiceChanged, NeedLogin, AuthError):
        raise
    except RuntimeError as exc:           # its words are about downloading: said for playing instead
        raise _SignedOut(str(exc)) from exc


def _delivery(sync: Any, rec: Any) -> tuple[Any, dict[str, Any]]:
    """DeliveryInfo of a recording, with a fresh sign-in to Panopto once when it asks for one."""
    s = sync.worker_session()
    try:
        info = Panopto(s).delivery_info(rec.delivery_id)
    except NeedPanoptoAuth:
        _sign_in_again(sync, rec.course_id)
        s = sync.worker_session()
        try:
            info = Panopto(s).delivery_info(rec.delivery_id)
        except NeedPanoptoAuth as exc:
            raise _SignedOut(str(exc)) from exc
    sync.absorb(s)                        # (a refreshed Panopto cookie is kept)
    return s, info


def _renewer(sync: Any, rec: Any, kind: str) -> Callable[[], tuple[Any, str]]:
    def renew() -> tuple[Any, str]:
        _sign_in_again(sync, rec.course_id)          # the course opened through IEULearn again: its access ran out
        s = sync.worker_session()
        info = Panopto(s).delivery_info(rec.delivery_id)
        sync.absorb(s)
        url, got = address(info, rec.delivery_id, "" if kind == "video" else "video")
        if not url or got != kind:
            raise RuntimeError("no podcast")
        return s, url
    return renew


def why(exc: BaseException) -> str:
    """Why the lecture can't play, in plain words, with what to do."""
    if isinstance(exc, (_SignedOut, NeedPanoptoAuth)):
        return t("IEUStudia couldn't sign in to Panopto, so the lecture can't play here. Try again in a few minutes. "
                 "If it keeps happening, open the course's Panopto page on learn.ieu.edu.tr in your browser once.")
    if isinstance(exc, (NeedLogin, AuthError, ServiceChanged)):
        return str(exc)                   # (their own words say what to do)
    if connection_lost(exc):
        return t("Panopto can't be reached, so the lecture can't play. Check your internet connection, then press "
                 "Try again.")
    if isinstance(exc, _Gone):
        return t("This recording is no longer on Panopto (it may have been removed or moved). Its transcript stays "
                 "here.")
    if isinstance(exc, requests.HTTPError) and exc.response is not None and exc.response.status_code >= 500:
        return t("Panopto isn't answering properly right now. Try again in a few minutes.")
    said = str(exc).strip() if isinstance(exc, RuntimeError) else ""
    if said:                              # Panopto's own words (DeliveryInfo's ErrorMessage)
        return t("Panopto says: “{said}” The recording may have been removed or moved. Its transcript stays here.",
                 said=said)
    return t("The lecture couldn't be loaded from Panopto. Try again, or open it in Panopto.")


def _probe(src: Source) -> None:
    """One byte of the lecture: Panopto lets it be played (a refusal gets a fresh sign-in now, not halfway through),
    and how big it is (for the data it uses)."""
    r = fetch(src, {"Range": "bytes=0-0", "Accept-Encoding": "identity"})
    with r:
        if refused(r):
            raise src.why if src.why is not None else _SignedOut("refused")
        if r.status_code in (404, 410):
            raise _Gone()
        r.raise_for_status()
        src.total = _size(r)


def _file_source(path: Path) -> Source:
    return Source("file", path=str(path), content_type=FILE_TYPES.get(path.suffix.lower(), "video/mp4"))


def _answer(src: Source, rec: Any, start: float, duration: float, note: str = "",
            proxy: Optional[Proxy] = None) -> dict[str, Any]:
    proxy = proxy or _proxy
    token = proxy.add(src)
    name = "lecture.m4a" if src.content_type.startswith("audio/") else "lecture.mp4"
    return {"ok": True, "token": token, "url": proxy.url(token, name),
            "kind": src.kind if src.kind != "file" else ("audio" if src.content_type.startswith("audio/") else "file"),
            "local": src.kind == "file", "start": max(0.0, float(start or 0.0)), "duration": float(duration or 0.0),
            "bytes": int(src.total or 0),
            "delivery_id": rec.delivery_id, "viewer_url": getattr(rec, "viewer_url", "") or "",
            "screens": screens(rec.delivery_id), "note": note}


def open_recording(sync: Any, rec: Any, avoid: str = "", proxy: Optional[Proxy] = None) -> dict[str, Any]:
    """Make a recording playable: {"ok": True, "token", "url" (the proxy's), "kind" ("video", "audio" or "file"),
    "start" (Panopto's resume point), "duration", "bytes", "delivery_id", "viewer_url", "screens", "note"}, or
    {"ok": False, "error"} in plain words with what to do. ``avoid`` "video": the sound-only podcast."""
    proxy = proxy or _proxy
    try:
        proxy.port()
    except OSError as exc:
        log.warning("Player: could not listen on 127.0.0.1 (%s)", exc)
        return {"ok": False, "error": t("IEUStudia couldn't start its player on this PC (security software may block "
                                        "it). Open the lecture in Panopto instead.")}
    duration = float(getattr(rec, "duration_s", 0.0) or 0.0)
    mine = local_file(rec.delivery_id)
    if rec.delivery_id.startswith("local-"):
        if mine is None:
            return {"ok": False, "error": t("The file this lecture was transcribed from is no longer on this PC (moved "
                                            "or deleted).")}
        return _answer(_file_source(mine), rec, 0.0, duration, proxy=proxy)
    if getattr(rec, "live", False):
        return {"ok": False, "error": t("This lecture is still being recorded. It can be played once Panopto has "
                                        "finished it.")}
    try:
        session, info = _delivery(sync, rec)
        url, kind = address(info, rec.delivery_id, avoid)
        if not url:
            if mine is not None:
                return _answer(_file_source(mine), rec, 0.0, duration, proxy=proxy)
            return {"ok": False, "error": t("Panopto is still preparing this recording for playing. Try again later, "
                                            "or open it in Panopto.")}
        src = Source(kind, url=url, session=session, renew=_renewer(sync, rec, kind), content_type=TYPES[kind])
        _probe(src)
    except Exception as exc:  # noqa: BLE001 - said in plain words (the copy on this PC plays if there is one)
        log.info("Player: %s can't play from Panopto (%s)", rec.delivery_id, type(exc).__name__)
        if mine is not None:
            return _answer(_file_source(mine), rec, 0.0, duration, proxy=proxy,
                           note=t("Panopto can't be reached right now: this is the lecture's copy on this PC."))
        out: dict[str, Any] = {"ok": False, "error": why(exc)}
        if isinstance(exc, (NeedLogin, AuthError)):
            out["need_login"] = True
        return out
    d = info.get("Delivery") or {}
    try:
        start = float(info.get("LastViewingPosition") or 0.0)
    except (TypeError, ValueError):
        start = 0.0
    try:
        duration = float(d.get("Duration") or 0.0) or duration
    except (TypeError, ValueError):
        pass
    return _answer(src, rec, start, duration, proxy=proxy)


def open_part(lib: Any, sync: Any, day_key: str, part: int = 1, avoid: str = "",
              proxy: Optional[Proxy] = None) -> dict[str, Any]:
    """:func:`open_recording` for part ``part`` of a class day (``day_key``); a ``day_key`` without "|" is a
    recording's own id (a lecture transcribed from a file on this PC)."""
    if "|" not in day_key:
        rec = lib.get_recording(day_key)
        if rec is None and day_key.startswith("local-") and store.load_transcript(day_key) is not None:
            rec = types.SimpleNamespace(delivery_id=day_key, course_id="", viewer_url="", duration_s=0.0, live=False)
    else:
        rec = part_recording(lib, day_key, part)
    if rec is None:
        return {"ok": False, "error": t("This lecture is no longer in your library.")}
    out = open_recording(sync, rec, avoid, proxy)
    out["part"] = part
    return out
