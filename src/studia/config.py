"""User settings, stored as JSON in the app folder (atomic writes, tolerant of old/unknown keys)."""

from __future__ import annotations

import dataclasses as dc
import json
import logging
import os
import threading
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
    course_prefs: dict[str, CoursePrefs] = dc.field(default_factory=dict)

    def prefs(self, course_id: str) -> CoursePrefs:
        return self.course_prefs.get(str(course_id)) or CoursePrefs()

    def to_json(self) -> dict[str, Any]:
        return dc.asdict(self)


_lock = threading.Lock()


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
    s.parallel_downloads = max(1, min(4, s.parallel_downloads))
    s.text_size = max(90, min(130, s.text_size))
    return s


def load_settings() -> Settings:
    path = settings_path()
    with _lock:
        try:
            raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except (OSError, ValueError):
            log.warning("settings.json is unreadable; using defaults")
            raw = {}
    return _from_dict(raw if isinstance(raw, dict) else {})


def save_settings(settings: Settings, path: Optional[Path] = None) -> None:
    """Write the settings (of the active profile, or to ``path`` for a profile being set up)."""
    path = path or settings_path()
    tmp = path.with_name(path.name + ".tmp")
    with _lock:
        tmp.write_text(json.dumps(settings.to_json(), ensure_ascii=False, indent=2), encoding="utf-8")
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
