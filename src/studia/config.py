"""User settings, stored as JSON in the app folder (atomic writes, tolerant of old/unknown keys)."""

from __future__ import annotations

import dataclasses as dc
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional

from .paths import default_output_path, settings_path

log = logging.getLogger("studia")


@dc.dataclass
class CoursePrefs:
    show_mode: str = "mine"            # "mine" | "all"
    my_section: Optional[str] = None   # None = guess from the IEULearn course title
    alias: Optional[str] = None


@dc.dataclass
class Settings:
    output_dir: str = ""
    model: str = "large-v3-turbo"
    device: str = "auto"               # auto | cuda | cpu
    languages: list[str] = dc.field(default_factory=lambda: ["en", "tr"])
    keep_audio: bool = False
    ask_before_closing: bool = True    # closing the window while lectures are transcribed: the page asks first
    write_srt: bool = True
    capture_slides: bool = True        # extract slides from Panopto's screen recording
    min_duration_s: int = 60
    theme: str = "system"              # system | light | dark
    text_size: int = 100               # transcripts, notes and chat, in percent (90-130)
    parallel_downloads: int = 2
    first_run_done: bool = False
    ai_model: str = "sonnet"           # model alias passed to the Claude CLI (chat, study notes)
    plan_model: str = ""               # study plans, review sheets and study packs ("" = ai_model)
    verify_model: str = "haiku"        # checking video captions against the course (many small requests)
    ai_model_light: str = "haiku"      # weekly digest
    ai_effort: dict[str, str] = dc.field(default_factory=dict)   # reasoning level per "provider.use" (effort.py)
    doc_language: str = "course"       # study documents in: "course" (its own language) | "en" | "tr" (doclang.py)
    quiz_questions: int = 10          # a new practice quiz starts with this many questions (QUIZ_SIZES)
    cards_per_day: int = 20            # new flashcards a day per course at most (CARDS_PER_DAY; cards.py)
    export_prefs: dict[str, Any] = dc.field(default_factory=dict)   # the Export menu's last format per place (exports.py)
    course_prefs: dict[str, CoursePrefs] = dc.field(default_factory=dict)

    def prefs(self, course_id: str) -> CoursePrefs:
        return self.course_prefs.get(str(course_id)) or CoursePrefs()

    def to_json(self) -> dict[str, Any]:
        return dc.asdict(self)


_lock = threading.Lock()
QUIZ_SIZES = (5, 10, 15, 20)               # the quiz dialog's choices
CARDS_PER_DAY = (10, 20, 30, 50)           # Settings → Practice's choices


def _from_dict(raw: dict[str, Any]) -> Settings:
    s = Settings()
    known = {f.name for f in dc.fields(Settings)}
    for key, value in raw.items():
        if key not in known or key == "course_prefs":
            continue
        default = getattr(s, key)
        try:
            if isinstance(default, bool):
                value = bool(value)
            elif isinstance(default, int):
                value = int(value)
            elif isinstance(default, list):
                value = [str(v) for v in value] if isinstance(value, list) else default
            elif isinstance(default, dict):
                value = dict(value) if isinstance(value, dict) else default
            elif isinstance(default, str):
                value = str(value)
        except (TypeError, ValueError):
            continue
        setattr(s, key, value)
    prefs = raw.get("course_prefs") or {}
    if isinstance(prefs, dict):
        pk = {f.name for f in dc.fields(CoursePrefs)}
        for cid, p in prefs.items():
            if isinstance(p, dict):
                s.course_prefs[str(cid)] = CoursePrefs(**{k: v for k, v in p.items() if k in pk})
    if not s.output_dir:                # created when the first file is written (Documents may be write-protected)
        s.output_dir = str(default_output_path())
    if s.theme not in ("system", "light", "dark"):
        s.theme = "system"
    if s.device not in ("auto", "cuda", "cpu"):
        s.device = "auto"
    for key, fallback in (("ai_model", "sonnet"), ("plan_model", ""), ("verify_model", "haiku"), ("ai_model_light", "haiku")):
        if getattr(s, key) not in ("haiku", "sonnet", "opus") and not (key == "plan_model" and getattr(s, key) == ""):
            setattr(s, key, fallback)
    from .effort import clean, migrate         # settings saved before reasoning levels: the levels they meant

    s.ai_effort = {**migrate(raw), **clean(s.ai_effort)}
    from .doclang import CHOICES, DEFAULT

    if s.doc_language not in CHOICES:
        s.doc_language = DEFAULT
    s.parallel_downloads = max(1, min(4, s.parallel_downloads))
    s.text_size = max(90, min(130, s.text_size))
    if s.quiz_questions not in QUIZ_SIZES:
        s.quiz_questions = min(QUIZ_SIZES, key=lambda n: abs(n - s.quiz_questions))
    s.cards_per_day = max(1, min(200, s.cards_per_day))
    return s


_unreadable: set[str] = set()      # settings files that couldn't be read: kept aside before one is saved over them


def unreadable() -> bool:
    """The active profile's settings.json couldn't be read at the last load: the defaults are in use."""
    return str(settings_path()) in _unreadable


def load_settings() -> Settings:
    path = settings_path()
    with _lock:
        try:
            raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            _unreadable.discard(str(path))
        except (OSError, ValueError):
            log.warning("settings.json is unreadable; using defaults (the file is kept aside, not written over)")
            _unreadable.add(str(path))
            raw = {}
    return _from_dict(raw if isinstance(raw, dict) else {})


def save_settings(settings: Settings, path: Optional[Path] = None) -> None:
    """Write the settings (of the active profile, or to ``path`` for a profile being set up). One that couldn't be read
    is first kept as settings.json.unreadable-<time>; the file is on the disk before the swap (a power cut)."""
    path = path or settings_path()
    tmp = path.with_name(path.name + ".tmp")
    with _lock:
        if str(path) in _unreadable:
            try:
                os.replace(path, path.with_name(f"{path.name}.unreadable-{time.strftime('%Y%m%d-%H%M%S')}"))
            except FileNotFoundError:
                pass
            _unreadable.discard(str(path))
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(settings.to_json(), ensure_ascii=False, indent=2))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)


def apply_patch(settings: Settings, patch: dict[str, Any]) -> Settings:
    """Return new settings with ``patch`` applied (validated like a load)."""
    merged = settings.to_json()
    for k, v in (patch or {}).items():
        if k == "course_prefs" and isinstance(v, dict):
            merged.setdefault("course_prefs", {}).update(v)
        else:
            merged[k] = v
    return _from_dict(merged)


# ------------------------------------------------------------------ Settings' groups and "Back to the defaults"
# What each group of Settings puts back: the student's own settings (this file), the PC's (appconfig.py) and the
# reminders' timing (agenda.py). Never what was set up or chosen on purpose elsewhere: the speech model the setup
# picked for this PC, the folder the files are in (moving it moves them), the AI service, the language, running in
# the background and starting with Windows.
GROUPS: dict[str, dict[str, tuple[str, ...]]] = {
    "transcription": {"settings": ("device", "capture_slides", "ask_before_closing", "min_duration_s")},
    "files": {"settings": ("write_srt", "keep_audio")},
    "ai": {"settings": ("ai_model", "plan_model", "verify_model", "ai_model_light", "ai_effort", "doc_language")},
    "practice": {"settings": ("quiz_questions", "cards_per_day")},
    "notifications": {"app": ("notify_recordings", "notify_announcements", "notify_materials", "notify_reminders",
                              "sync_minutes"),
                      "reminders": ("exams", "due_days", "due_hours")},
    "appearance": {"settings": ("theme", "text_size")},
}


def defaults(group: str) -> dict[str, dict[str, Any]]:
    """A group's defaults, by where they are kept: {"settings": {...}, "app": {...}, "reminders": {...}}."""
    from .agenda import REMIND_DEFAULTS
    from .appconfig import AppConfig

    keys = GROUPS[group]
    # (a fresh profile's settings as loaded: each AI's reasoning levels filled in, as effort.py's defaults)
    base = {"settings": _from_dict({}).to_json(), "app": AppConfig().to_json(), "reminders": dict(REMIND_DEFAULTS)}
    return {where: {k: base[where][k] for k in names} for where, names in keys.items()}


def all_defaults() -> dict[str, dict[str, dict[str, Any]]]:
    """Every group's defaults (the page shows "Back to the defaults" only for a group that differs from them)."""
    return {g: defaults(g) for g in GROUPS}
