"""Panopto (panopto.ieu.edu.tr) client: session lists, delivery details and media download."""

from __future__ import annotations

import dataclasses as dc
import datetime as dt
import hashlib
import json
import logging
import math
import os
import re
import struct
import threading
from pathlib import Path
from typing import Any, Callable, Iterator, Optional
from urllib.parse import urljoin, urlsplit

import requests

from . import health, hls, httpdl
from .cancel import check
from .domain import Recording
from .i18n import t
from .sections import parse_section_tag

log = logging.getLogger("studia")
BASE = "https://panopto.ieu.edu.tr"
_EPOCH = dt.datetime(1970, 1, 1)


class NeedPanoptoAuth(Exception):
    """The Panopto session is missing/expired (fix: run the LTI launch again)."""


class NotReady(Exception):
    """Panopto is still processing this recording."""


class Listing(list):
    """A course's recordings as Panopto listed them. ``complete`` False: fewer rows came than Panopto counted, or some
    couldn't be read, so one missing from it may still be there (nothing is marked gone)."""

    complete = True


def _keys(d: Any) -> str:
    """The keys of an answer, for the developer (what came instead of what IEUStudia reads)."""
    return ", ".join(sorted(str(k) for k in d)[:12]) if isinstance(d, dict) else type(d).__name__


def _sign_in_page(r: Any) -> bool:
    """An answer that is Panopto's sign-in page (the session ran out), not JSON."""
    return bool(re.search(r"/(?:Auth|Login)", urlsplit(str(getattr(r, "url", "") or "")).path, re.I))


def _type(r: Any) -> str:
    headers = getattr(r, "headers", None) or {}
    return str(headers.get("content-type") or "an answer of no type")


@dc.dataclass
class MediaSource:
    kind: str                 # "audio_podcast" | "video_podcast" | "hls"
    url: str
    est_bytes: Optional[int] = None
    # a screen stream's stretches the recording keeps: (from, to) in the stream's own seconds and where in the
    # recording that stretch begins (see _spans); () for all of it, from the stream's start offset
    spans: tuple[tuple[float, float, float], ...] = ()


def _spans(stream: dict[str, Any]) -> tuple[tuple[float, float, float], ...]:
    """A stream's RelativeSegments as (from, to, at). A stretch cut out in Panopto's editor, or recorded while the
    recorder was paused, is left out of the sound and the camera but stays in the screen video: there the stretches
    after it are that much later than in the recording. () when it has none, or one that can't be read."""
    out = []
    for g in stream.get("RelativeSegments") or []:
        try:
            span = (float(g["Start"]), float(g["End"]), float(g["RelativeStart"]))
        except (KeyError, TypeError, ValueError):
            return ()
        if not all(map(math.isfinite, span)) or span[1] <= span[0]:
            return ()
        out.append(span)
    return tuple(sorted(out))


def _write_marker(path: Path, data: dict[str, Any]) -> None:
    """A camera-stream download's marker, whole or not at all (a power cut leaves the one before)."""
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(data))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def parse_dotnet_date(value: str) -> Optional[dt.datetime]:
    """``"/Date(1792494951083)/"`` → naive local wall-clock time.

    Panopto's session list encodes the *local* (Istanbul) wall-clock time as if it were UTC,
    so the epoch offset is applied without any timezone conversion.
    """
    m = re.search(r"/Date\((-?\d+)", value or "")
    return _EPOCH + dt.timedelta(milliseconds=int(m.group(1))) if m else None


def _kind(name: str, folder_path: str) -> str:
    return "lab" if re.search(r"\blab", f"{folder_path} {name}", re.I) else "lecture"


def _fold(word: str) -> str:
    return word.translate(str.maketrans("İIıŞşÇçĞğÜüÖö", "iiissccgguuoo")).lower()


_MONTHS = {m: i for i, names in enumerate((
    "january ocak", "february subat", "march mart", "april nisan", "may mayis", "june haziran", "july temmuz",
    "august agustos", "september eylul", "october ekim", "november kasim", "december aralik"), start=1)
    for m in names.split()}
_WEEKDAYS = {d: i for i, names in enumerate((
    "monday pazartesi", "tuesday sali", "wednesday carsamba", "thursday persembe", "friday cuma",
    "saturday cumartesi", "sunday pazar")) for d in names.split()}
_W = r"([^\W\d_]+)"
_TIME = r",?\s+(?:at\s+|saat\s+)?(\d{1,2})[:_](\d{2})[:_](\d{2})(?:\s*([AP]M)\b)?"
# (pattern, where its groups have the day, month, year and weekday), the time after them:
# "4 November 2026 Wednesday at 08:28:43", "Friday, October 23, 2026 at 12:09:42 PM", "Cuma, 23 Ekim 2026 12:09:42"
_NAME_DATES = (
    (re.compile(r"\b(\d{1,2})\s+" + _W + r"\s+(\d{4}),?\s+" + _W + _TIME, re.I), (0, 1, 2, 3)),
    (re.compile(_W + r",?\s+" + _W + r"\s+(\d{1,2}),?\s+(\d{4})" + _TIME, re.I), (2, 1, 3, 0)),
    (re.compile(_W + r",?\s+(\d{1,2})\s+" + _W + r"\s+(\d{4})" + _TIME, re.I), (1, 2, 3, 0)),
)
NAME_DATE_LATE = dt.timedelta(hours=2)       # a start this much after the name's date and time: uploaded later
NAME_DATE_MAX = dt.timedelta(days=60)        # (a name further back than this is someone's own title, not the upload's)


def uploaded_later(start: dt.datetime, named: Optional[dt.datetime]) -> bool:
    """Whether a recording's StartTime (``start``) is when it was uploaded, its name's date and time (``named``) when
    it was recorded. A recorder on another time zone names it a whole number of hours off its start, to the minute
    ("27 Ekim 2026 Salı at 08:16:48" started 11:17:20): that one is not."""
    if named is None or not NAME_DATE_LATE < start - named <= NAME_DATE_MAX:
        return False
    late = (start - named).total_seconds()
    return not (late <= 14 * 3600 and abs(late - round(late / 3600) * 3600) <= 180)


def name_start(name: str) -> Optional[dt.datetime]:
    """The date and time Panopto puts in a recording's default name ("4 November 2026 Wednesday at 08:28:43", "Friday,
    October 23, 2026 at 12:09:42 PM"; "_" between the time's parts in an uploaded file's name), in English or Turkish,
    when the weekday matches the date. None when the name has none."""
    for pattern, (di, mi, yi, wi) in _NAME_DATES:
        for m in pattern.finditer(name or ""):
            g = m.groups()
            month, wday = _MONTHS.get(_fold(g[mi])), _WEEKDAYS.get(_fold(g[wi]))
            hour, minute, sec, ampm = int(g[4]), int(g[5]), int(g[6]), (g[7] or "").upper()
            if month is None or wday is None or (ampm and not 1 <= hour <= 12):
                continue
            if ampm:
                hour = hour % 12 + (12 if ampm == "PM" else 0)
            try:
                when = dt.datetime(int(g[yi]), month, int(g[di]), hour, minute, sec)
            except ValueError:
                continue
            if when.weekday() == wday:
                return when
    return None


def parse_sessions(d: dict[str, Any], course_id: str, folder_path: str,
                   unread: Optional[list[str]] = None) -> list[Recording]:
    """A page of GetSessions. A recording uploaded later (one recorded on the lecturer's own PC) has the upload time
    as its StartTime: the date and time in its name are when it was recorded. "Status" 4 is a finished recording; one
    still being recorded has 2 (and no podcast yet, a growing Duration). A row without a delivery (a playlist) is
    passed over, as always. ``unread`` gets what was wrong with each recording that couldn't be read; rows of which
    none can be read (their fields renamed, a date written another way: Panopto's answer changed) raise
    health.ServiceChanged, never "no recordings"."""
    out = []
    rows = d.get("Results") or []
    bad: list[str] = []
    if rows and not any(isinstance(r, dict) and ("DeliveryID" in r and "StartTime" in r) for r in rows):
        raise health.changed("panopto", "sessions", f"{len(rows)} rows without DeliveryID or StartTime: "
                                                    f"{_keys(rows[0])}", where=str(course_id))
    for row in rows:
        try:
            if not isinstance(row, dict):
                raise TypeError(f"a row that is a {type(row).__name__}")
            did = row.get("DeliveryID")
            if did is None:
                continue                  # not a recording (a playlist): passed over, as before
            did = did.lower() if isinstance(did, str) else ""
            start = parse_dotnet_date(row.get("StartTime") if isinstance(row.get("StartTime"), str) else "")
            if not did:
                raise TypeError(f"a DeliveryID that is a {type(row.get('DeliveryID')).__name__}")
            if start is None:
                raise TypeError(f"a StartTime IEUStudia can't read ({type(row.get('StartTime')).__name__})")
            name = (row.get("SessionName") or "").strip() or "Untitled recording"
            named, uploaded = name_start(name), ""
            if uploaded_later(start, named):
                start, uploaded = named, start.replace(microsecond=0).isoformat()
            out.append(Recording(
                delivery_id=did, course_id=str(course_id), folder_id=(row.get("FolderID") or "").lower(),
                folder_path=folder_path, name=name, start=start.replace(microsecond=0),
                duration_s=float(row.get("Duration") or 0.0),
                viewer_url=row.get("ViewerUrl") or f"{BASE}/Panopto/Pages/Viewer.aspx?id={did}",
                session_id=(row.get("SessionID") or "").lower(), kind=_kind(name, folder_path),
                section_tag=parse_section_tag(name), live=row.get("Status") not in (None, 4), uploaded=uploaded,
            ))
        except (TypeError, ValueError, AttributeError, OverflowError) as exc:   # a row that isn't what it was
            bad.append(str(exc) if isinstance(exc, TypeError) and str(exc).startswith("a ") else
                       f"{exc.__class__.__name__} in a row ({_keys(row)})")
    if bad and unread is not None:
        unread.extend(bad)
    if bad and not out:               # not one recording IEUStudia can read: Panopto's answer changed
        raise health.changed("panopto", "sessions", f"{len(bad)} rows, none read: {bad[0]}", where=str(course_id))
    if out:                           # (this course's rows read again: its change, if it had one, is over)
        health.ok("panopto", "sessions", where=str(course_id))
    return out


def _query(folder_id: str, page: int, rows: int = 250) -> dict[str, Any]:
    return {"queryParameters": {
        "query": None, "sortColumn": 1, "sortAscending": False, "maxResults": rows, "page": page,
        "startDate": None, "endDate": None, "folderID": folder_id, "bookmarked": False, "getFolderData": True,
        "isSharedWithMe": False, "isSubscriptionsPage": False, "includeArchived": True,
        "includeArchivedStateCount": False, "sessionListOnlyArchived": False, "includePlaylists": True}}


def _head(d: dict[str, Any]) -> list[Any]:
    """A folder's recording count, its newest recording (the list is newest first) and that one's status (4 when
    finished, so one still being recorded is noticed when it finishes): [35, "c0e6fe5f-…", 4]."""
    rows = d.get("Results") or [{}]
    first = rows[0] if isinstance(rows[0], dict) else {}
    did = first.get("DeliveryID")
    return [int(d.get("TotalNumber") or 0), did.lower() if isinstance(did, str) else "", first.get("Status")]


def _mp4_header(session: requests.Session, url: str, limit: int = 64 << 20) -> Optional[bytes]:
    """The ``ftyp`` and ``moov`` boxes of an MP4 file on the server, through range reads. Panopto's podcasts keep
    the ``moov`` (the track list) at the end, after the media data, so the top-level boxes are walked to find it."""
    final, total, ranges_ok, _ = httpdl._probe(session, url)
    if not ranges_ok or total <= 0:
        return None
    head = hls.get_range(session, final, 0, min(total, 65536) - 1)
    ftyp, pos = b"", 0
    for _ in range(32):
        if pos + 16 > total:
            return None
        box = head[pos:pos + 16] if pos + 16 <= len(head) else hls.get_range(session, final, pos, pos + 15)
        size, typ = struct.unpack(">I4s", box[:8])
        if size == 1:
            size = struct.unpack(">Q", box[8:16])[0]
        elif size == 0:
            size = total - pos
        if size < 8:
            return None
        if typ == b"ftyp":
            ftyp = head[pos:pos + size] if pos + size <= len(head) else hls.get_range(session, final, pos, pos + size - 1)
        elif typ == b"moov":
            if size > limit:
                return None
            moov = head[pos:pos + size] if pos + size <= len(head) else hls.get_range(session, final, pos, pos + size - 1)
            return ftyp + moov
        pos += size
    return None


class Panopto:
    BASE = BASE

    def __init__(self, session: requests.Session) -> None:
        self.s = session

    def _headers(self) -> dict[str, str]:
        token = self.s.cookies.get("csrfToken", domain="panopto.ieu.edu.tr") or ""
        return {"x-csrf-token": token, "x-requested-with": "XMLHttpRequest",
                "referer": f"{BASE}/Panopto/Pages/Sessions/List.aspx"}

    def is_authenticated(self) -> bool:
        """Is this session signed in to Panopto? (401 when not.) An answer of 404 or 410: the address it is asked at
        is gone (Panopto changed), raised as health.ServiceChanged."""
        r = self.s.get(f"{BASE}/Panopto/Api/v1/auth/legacyLogin", allow_redirects=False,
                       headers={"x-requested-with": "XMLHttpRequest"})
        if r.status_code in (404, 410):
            raise health.changed("panopto", "signin", f"legacyLogin answered HTTP {r.status_code}")
        if r.status_code == 200:
            health.ok("panopto", "signin")
        return r.status_code == 200

    # ------------------------------------------------------------------ listing
    def _sessions(self, folder_id: str, page: int, rows: int) -> dict[str, Any]:
        """One page of a folder's recordings (Data.svc/GetSessions). An answer without the parts IEUStudia reads (its
        "d", its Results list and their count) is Panopto's answer changing: raised as health.ServiceChanged, never
        taken for an empty folder. (Without a sign-in Panopto answers the same form, with no rows.)"""
        r = self.s.post(f"{BASE}/Panopto/Services/Data.svc/GetSessions", json=_query(folder_id, page, rows),
                        headers=self._headers())
        status = getattr(r, "status_code", 200)
        if status in (404, 410):
            raise health.changed("panopto", "sessions", f"GetSessions answered HTTP {status}")
        if status >= 500:
            health.down("panopto", "sessions", f"HTTP {status}")
        r.raise_for_status()
        try:
            body = r.json()
        except ValueError:
            if _sign_in_page(r):
                raise NeedPanoptoAuth(t("Panopto session expired.")) from None
            raise health.changed("panopto", "sessions", f"GetSessions answered {_type(r)} instead of JSON") from None
        d = body.get("d") if isinstance(body, dict) else None
        total = d.get("TotalNumber") if isinstance(d, dict) else None
        if not isinstance(d, dict) or not isinstance(d.get("Results"), list) or isinstance(total, bool) or \
                not isinstance(total, int):
            raise health.changed("panopto", "sessions", f"GetSessions answered without d.Results and d.TotalNumber "
                                                        f"({_keys(body)} / {_keys(d)})")
        if not isinstance(d.get("Subfolders") or [], list):
            raise health.changed("panopto", "sessions", f"GetSessions' Subfolders is a {type(d['Subfolders']).__name__}")
        health.ok("panopto", "sessions")
        return d

    def _folders(self, folder_id: str, rows: int) -> Iterator[tuple[str, str, dict[str, Any]]]:
        """A folder and its subfolders: id, path and the first page (``rows`` long) of each."""
        todo = [(folder_id, "")]
        seen: set[str] = set()
        while todo:
            fid, path = todo.pop(0)
            if fid in seen:
                continue
            seen.add(fid)
            d = self._sessions(fid, 0, rows)
            for sub in d.get("Subfolders") or []:
                if isinstance(sub, dict) and isinstance(sub.get("ID"), str) and sub["ID"]:
                    todo.append((sub["ID"].lower(), f"{path}{str(sub.get('Name') or '').strip()}/".lstrip("/")))
            yield fid, path, d

    def list_recordings(self, course_id: str, folder_id: str,
                        heads: Optional[dict[str, list[Any]]] = None) -> list[Recording]:
        """Every recording in a course's folder and its subfolders (a Listing: ``complete`` False when fewer came
        than Panopto counted, or some couldn't be read). ``heads`` gets what :meth:`probe` would say."""
        recs = Listing()
        unread: list[str] = []
        for fid, path, d in self._folders(folder_id, 250):
            if heads is not None:
                heads[fid] = _head(d)
            page, rows = 0, 0
            total = int(d.get("TotalNumber") or 0)
            while True:
                batch = parse_sessions(d, course_id, path, unread)
                recs.extend(batch)
                rows += len(d.get("Results") or [])
                page += 1
                if not batch or page * 250 >= total or page > 20:
                    break
                d = self._sessions(fid, page, 250)
            if rows < total:              # (a page that came back empty halfway: the rest may be there)
                recs.complete = False
        if unread:
            log.warning("Panopto listed %d recordings of %s IEUStudia can't read: %s", len(unread), course_id,
                        "; ".join(dict.fromkeys(unread))[:300])
            recs.complete = False
        for r_ in recs:
            r_.folder_path = r_.folder_path.rstrip("/")
        return recs

    def probe(self, folder_id: str) -> dict[str, list[Any]]:
        """Each folder's recording count and newest recording, through one-row answers (~5 KB each instead of
        ~4.4 KB per recording): {folder id: [count, newest delivery id, its status]}. A change means the folder needs
        listing."""
        return {fid: _head(d) for fid, _, d in self._folders(folder_id, 1)}

    # ------------------------------------------------------------------ details
    def delivery_info(self, delivery_id: str) -> dict[str, Any]:
        r = self.s.post(f"{BASE}/Panopto/Pages/Viewer/DeliveryInfo.aspx", data={
            "deliveryId": delivery_id, "isLiveNotes": "false", "refreshAuthCookie": "true",
            "isActiveBroadcast": "false", "isEditing": "false", "isKollectiveAgentInstalled": "false",
            "isEmbed": "false", "responseType": "json"}, headers=self._headers())
        status = getattr(r, "status_code", 200)
        if status in (404, 410):
            raise health.changed("panopto", "delivery", f"DeliveryInfo answered HTTP {status}")
        r.raise_for_status()
        try:
            data = r.json()
        except ValueError:
            if _sign_in_page(r):
                raise NeedPanoptoAuth(t("Panopto session expired.")) from None
            raise health.changed("panopto", "delivery", f"DeliveryInfo answered {_type(r)} instead of JSON") from None
        if not isinstance(data, dict):
            raise health.changed("panopto", "delivery", f"DeliveryInfo answered a {type(data).__name__}")
        if "Delivery" not in data:
            if data.get("ErrorCode") == 2 or data.get("LoginRedirect"):
                raise NeedPanoptoAuth(t("Panopto session expired."))
            if "ErrorCode" in data or "ErrorMessage" in data:      # Panopto's own words about this recording (its
                said = str(data.get("ErrorMessage") or "")          # "See other videos" link left out)
                said = " ".join(re.sub(r"<a\b.*?</a>|<[^>]+>", " ", said, flags=re.S | re.I).split())
                raise RuntimeError(said or t("Panopto refused the request."))
            raise health.changed("panopto", "delivery", f"DeliveryInfo answered without Delivery ({_keys(data)})")
        d = data["Delivery"]
        if not isinstance(d, dict) or not any(k in d for k in ("Streams", "PodcastStreams", "IsAudioPodcastEncodeComplete")) \
                or not isinstance(d.get("Streams") or [], list) or not isinstance(d.get("PodcastStreams") or [], list):
            raise health.changed("panopto", "delivery", f"Delivery without its streams ({_keys(d)})")
        health.ok("panopto", "delivery")
        return data

    def media_sources(self, delivery_id: str, info: Optional[dict[str, Any]] = None) -> list[MediaSource]:
        """Best-first list of sources that contain the lecture audio."""
        info = info or self.delivery_info(delivery_id)
        d = info["Delivery"]
        out: list[MediaSource] = []
        if d.get("IsAudioPodcastEncodeComplete"):
            out.append(MediaSource("audio_podcast",
                                   f"{BASE}/Panopto/Podcast/Stream/{delivery_id}.mp4?mediaTargetType=audioPodcast"))
        for ps in d.get("PodcastStreams") or []:
            if isinstance(ps, dict) and isinstance(ps.get("StreamUrl"), str) and ps["StreamUrl"]:
                out.append(MediaSource("video_podcast", ps["StreamUrl"]))
        for st in d.get("Streams") or []:
            if isinstance(st, dict) and isinstance(st.get("StreamUrl"), str) and \
                    str(st.get("Tag") or "").upper() != "SCREEN" and ".m3u8" in st["StreamUrl"]:
                out.append(MediaSource("hls", st["StreamUrl"]))
        if not out:
            raise NotReady(t("Panopto is still processing this recording. Try again later."))
        return out

    def screen_sources(self, info: dict[str, Any]) -> list[tuple[MediaSource, float]]:
        """The screen/slides videos (HLS) and their start offsets in seconds, in time order. Panopto makes one per
        stretch of screen sharing: a lecturer who stops and starts sharing again gives several. Each one's ``spans``:
        the stretches of it the recording keeps (an edit or a pause cuts the others out)."""
        out = [(MediaSource("hls", st["StreamUrl"], spans=_spans(st)), float(st.get("RelativeStart") or 0.0))
               for st in info["Delivery"].get("Streams") or []
               if isinstance(st, dict) and str(st.get("Tag") or "").upper() == "SCREEN"
               and isinstance(st.get("StreamUrl"), str) and st["StreamUrl"]]
        return sorted(out, key=lambda x: x[1])

    def has_sound(self, src: MediaSource) -> Optional[bool]:
        """Whether a stream has a sound track, from its header alone (a few small reads instead of the whole
        video): the init segment of an HLS stream, the ``moov`` box of an MP4 file. None if it can't be told."""
        try:
            if src.kind == "hls":
                r = hls.media_playlist(self.s, src.url, "audio")       # the variant a download would fetch
                init, _ = hls.parse_playlist(r.text, r.url)
                if init is None:
                    return None
                url, length, offset = init
                header = hls.get_range(self.s, url, offset, offset + length - 1) if length else self.s.get(url).content
            else:
                header = _mp4_header(self.s, src.url)
            return hls.has_audio_track(header) if header else None
        except Exception as exc:  # noqa: BLE001 - when in doubt, download it
            log.info("Could not read the header of %s: %s", src.kind, exc)
            return None

    # ------------------------------------------------------------------ download
    def download(self, src: MediaSource, dest: Path, progress: Callable[[int, int], None],
                 cancel: Optional[threading.Event] = None, prefer: str = "audio") -> Path:
        """Download a source to ``dest`` (resumable). ``prefer`` picks the HLS variant: "audio" = smallest
        variant that has an audio codec, "video" = highest resolution."""
        if src.kind != "hls":
            return httpdl.download(src.url, dest, session=self.s, progress=progress, cancel=cancel, parts=6)
        return self._download_hls(src.url, dest, progress, cancel, prefer)

    def _download_hls(self, master_url: str, dest: Path, progress: Callable[[int, int], None],
                      cancel: Optional[threading.Event], prefer: str) -> Path:
        r = hls.media_playlist(self.s, master_url, prefer)
        playlist, base = r.text, r.url
        uris = [urljoin(base, ln.strip()) for ln in playlist.splitlines() if ln.strip() and not ln.startswith("#")]
        map_m = re.search(r'#EXT-X-MAP:URI="([^"]+)"', playlist)
        files = list(dict.fromkeys(([urljoin(base, map_m.group(1))] if map_m else []) + uris))
        if not files:        # a playlist with nothing in it to fetch: never an empty file taken for the lecture
            raise health.changed("panopto", "stream", "a media playlist without segments")
        if len(files) == 1:  # single fragmented MP4 addressed with byte ranges: just fetch the file
            return httpdl.download(files[0], dest, session=self.s, progress=progress, cancel=cancel, parts=6)
        tmp = dest.with_name(dest.name + ".part")
        # resumable: the segments written so far are noted next to them (by their paths: the links' tokens change
        # from one request to the next), so a download cut off (the app closed, no connection) goes on from there
        marker = dest.with_name(dest.name + ".hls.json")
        key = hashlib.sha1("\n".join(urlsplit(u).path for u in files).encode()).hexdigest()
        start, size = 0, 0
        try:
            got = json.loads(marker.read_text(encoding="utf-8"))
            if got.get("key") == key and tmp.stat().st_size >= int(got["size"]):
                start, size = int(got["n"]), int(got["size"])
        except (OSError, ValueError, KeyError, TypeError):
            pass
        with open(tmp, "r+b" if start else "wb") as out:
            out.truncate(size)                   # a segment half written when it stopped: written again
            out.seek(size)
            for i, u in enumerate(files[start:], start):
                check(cancel)
                for attempt in range(4):
                    try:
                        seg = self.s.get(u)
                        seg.raise_for_status()
                        out.write(seg.content)
                        break
                    except requests.RequestException:
                        if attempt == 3:
                            raise
                out.flush()
                os.fsync(out.fileno())           # on the disk before the marker says so (a power cut: no zeros)
                _write_marker(marker, {"key": key, "n": i + 1, "size": out.tell()})
                progress(i + 1, len(files))
        tmp.replace(dest)
        marker.unlink(missing_ok=True)
        return dest
