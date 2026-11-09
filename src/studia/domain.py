"""Shared data types used across IEUStudia's modules.

Everything here is plain data (dataclasses) so it can be stored in SQLite / JSON and
sent to the UI without surprises. Datetimes for recordings are *naive local wall-clock*
times (Europe/Istanbul), which is how Panopto's session list reports them.
"""

from __future__ import annotations

import dataclasses as dc
import datetime as dt
from typing import Any, Optional


@dc.dataclass
class Profile:
    name: str
    student_number: str = ""
    username: str = ""


@dc.dataclass
class Course:
    id: str                      # IEULearn course id, e.g. "19226"
    title: str                   # full IEULearn title, e.g. "2026-1 EEE 215 Blgsyr. Müh. için ... 0 - 1"
    code: str                    # "EEE 215"
    name: str                    # cleaned name without term prefix / section suffix
    instructor: str = ""
    term: str = ""               # "2026-2027 Fall Undergrad"
    status: str = "ongoing"      # "ongoing" | "past"
    section: str = ""            # the user's section parsed from the title suffix ("1", "3", "0 - 1")
    folder_id: Optional[str] = None  # Panopto root folder for the course (None until first LTI launch)
    has_panopto: bool = True
    position: int = 0            # order on the IEULearn dashboard
    cover_url: str = ""          # the course's cover picture on the IEULearn dashboard
    progress: Optional[int] = None  # IEULearn's own "İlerleme" (progress %) shown on the dashboard
    folder: str = ""             # its folder in the output folder, named once by the library and kept


@dc.dataclass
class Recording:
    """One Panopto session (we key everything by its *delivery* id)."""

    delivery_id: str
    course_id: str
    folder_id: str
    folder_path: str             # "" for the course root folder, "Labs" for a subfolder, ...
    name: str                    # raw Panopto session name
    start: dt.datetime           # naive local wall-clock time
    duration_s: float
    viewer_url: str
    session_id: str = ""
    kind: str = "lecture"        # "lecture" | "lab"
    section_tag: str = ""        # section parsed from the name ("1", "2-3", "" if none)
    is_new: bool = False         # appeared since the previous sync
    duplicate_of: Optional[str] = None  # delivery id of the recording this one duplicates
    hidden: bool = False         # user chose to hide it
    status: str = "available"    # available|processing|queued|downloading|transcribing|done|failed (see ``live``)
    error: str = ""
    first_seen: str = ""         # ISO timestamp of first sync that saw it
    live: bool = False           # Panopto hasn't finished it: still being recorded (nothing to download yet)
    uploaded: str = ""           # Panopto's StartTime (ISO) when that was the upload and the name's date replaced it

    @property
    def day_key(self) -> str:
        """Recordings of the same course, day and kind are presented together as one 'class day'."""
        return f"{self.course_id}|{self.start.date().isoformat()}|{self.kind}"


@dc.dataclass
class Segment:
    start: float                 # seconds from the start of the recording
    end: float
    text: str
    lang: str                    # ISO 639-1 code: "en", "tr", ...
    conf: float = 0.0            # 0..1, exp(avg_logprob) of the decode the segment came from


@dc.dataclass
class Transcript:
    delivery_id: str             # Panopto delivery id, or "local-<hash>" for local files
    segments: list[Segment]
    duration_s: float            # audio duration
    speech_s: float              # seconds of detected speech
    language_seconds: dict[str, float]   # e.g. {"en": 2400.0, "tr": 130.0}
    model: str
    device: str
    engine_version: str
    created_at: str              # ISO timestamp
    timings: dict[str, float] = dc.field(default_factory=dict)
    meta: dict[str, Any] = dc.field(default_factory=dict)
    # meta keys (all optional): course_id, course_code, course_name, instructor, recording_name,
    # start (ISO), viewer_url, kind, source_path (local files)

    def to_json(self) -> dict[str, Any]:
        d = dc.asdict(self)
        return d

    @staticmethod
    def from_json(d: dict[str, Any]) -> "Transcript":
        segs = [Segment(**s) for s in d.get("segments", [])]
        fields = {f.name for f in dc.fields(Transcript)}
        kw = {k: v for k, v in d.items() if k in fields and k != "segments"}
        return Transcript(segments=segs, **kw)
