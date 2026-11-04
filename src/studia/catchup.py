"""Full sync / course sync: what a course (or every course) still needs after a sync - the lectures of your own
section that are not transcribed yet (newest first, so the lecturer's latest heads-ups show up first) and the
materials not downloaded yet. Nothing here uses Claude: notes, plans and chats stay on demand."""

from __future__ import annotations

import dataclasses as dc
import re
from pathlib import Path
from typing import Any, Iterable, Optional

from .config import Settings
from .sections import available_sections, classify, guess_my_section

READY = ("available", "failed")      # Panopto has finished processing it ("processing" ones are not ready yet, nor
                                     # one still being recorded: Recording.live)
_NO_SOUND = re.compile(r"no sound|no speech", re.I)     # one that failed so under 2.3 has only the error, no warning
# rough real-time factors (minutes of audio per minute of work), English + Turkish decoding included; large-v3 on an
# RTX 4070 measured ~13x
SPEED = {"gpu": {"large-v3-turbo": 22.0, "large-v3": 12.0, "medium": 16.0, "small": 25.0},
         "cpu": {"large-v3-turbo": 1.0, "large-v3": 0.4, "medium": 0.2, "small": 2.0}}     # measured on a laptop CPU


@dc.dataclass
class CourseNeeds:
    course_id: str
    code: str
    name: str
    lectures: list[tuple[str, str]]  # (delivery id, ISO start), newest first
    audio_s: float
    materials: list[str]             # resource ids
    other_sections: int              # skipped: lectures of the other sections
    needs_section: bool              # several sections publish here and yours is unknown: nothing is queued
    waiting: int                     # lectures held back until you choose your section
    sections: list[str] = dc.field(default_factory=list)      # the sections found here (the dialog asks with them)
    my_section: Optional[str] = None
    unknown: list[tuple[str, str]] = dc.field(default_factory=list)  # several sections, yours set, but these match
    unknown_audio_s: float = 0.0                                     # none: left out unless you include them

    def view(self) -> dict[str, Any]:
        return {"course_id": self.course_id, "code": self.code, "name": self.name, "lectures": len(self.lectures),
                "audio_s": round(self.audio_s), "materials": len(self.materials), "other_sections": self.other_sections,
                "needs_section": self.needs_section, "waiting": self.waiting, "sections": self.sections,
                "my_section": self.my_section, "unknown": len(self.unknown), "unknown_audio_s": round(self.unknown_audio_s)}


def _downloaded(m: dict[str, Any]) -> bool:
    return bool(m.get("local_path")) and Path(m["local_path"]).is_file()


def no_sound(rec: Any, warnings: Iterable[dict[str, Any]]) -> bool:
    """The lecture's last try found no sound or no speech on Panopto: trying again finds the same. A batch (a full or
    course sync, a class day) leaves it out; the student can still queue it by hand."""
    return (any(w.get("code") == "no_speech" for w in warnings)
            or (rec.status == "failed" and bool(_NO_SOUND.search(rec.error or ""))))


def course_needs(lib: Any, settings: Settings, course_ids: Optional[Iterable[str]] = None,
                 busy: Iterable[str] = (), include_unknown: Iterable[str] = ()) -> list[CourseNeeds]:
    """Per course (all active ones, or ``course_ids``): the work a full sync would queue. ``busy``: ids already queued.

    Only your section's lectures are queued. Where several sections publish, a lecture that matches none (no tag and
    no weekday to go by) is left out and counted, unless its course is in ``include_unknown`` (you said so for this
    run); while your section there is unknown, nothing is queued. A folder without section tags is one section's:
    all of it is yours."""
    wanted = set(course_ids) if course_ids is not None else None
    busy = set(busy)
    include = set(include_unknown)
    warnings = lib.all_warnings()
    out: list[CourseNeeds] = []
    for c in lib.courses():
        if wanted is not None and c.id not in wanted:
            continue
        recs = lib.recordings(c.id)
        prefs = settings.prefs(c.id)
        my_section = prefs.my_section or guess_my_section(c)
        sections = available_sections(recs)
        several = len(sections) > 1
        mine = classify(c, recs, my_section) if sections else {}
        todo, unknown, other, waiting = [], [], 0, 0
        for r in recs:
            if (r.duplicate_of or r.hidden or r.status not in READY or r.live or r.delivery_id in busy
                    or 0 < r.duration_s < settings.min_duration_s or no_sound(r, warnings.get(r.delivery_id, ()))):
                continue
            verdict = mine.get(r.delivery_id)
            if verdict is False:
                other += 1
            elif several and not my_section:
                waiting += 1
            elif several and verdict is None and c.id not in include:
                unknown.append(r)
            else:
                todo.append(r)
        todo.sort(key=lambda r: r.start, reverse=True)
        unknown.sort(key=lambda r: r.start, reverse=True)
        mats = [m["resource_id"] for m in lib.materials(c.id) if not m.get("gone") and not _downloaded(m)]
        out.append(CourseNeeds(c.id, c.code, prefs.alias or c.name or c.code,
                               [(r.delivery_id, r.start.isoformat()) for r in todo],
                               sum(max(0.0, r.duration_s) for r in todo), mats, other, several and not my_section,
                               waiting, sections, my_section if several else None,
                               [(r.delivery_id, r.start.isoformat()) for r in unknown],
                               sum(max(0.0, r.duration_s) for r in unknown)))
    return out


def speed(gpu: bool, model: str) -> float:
    return SPEED["gpu" if gpu else "cpu"].get(model, 12.0 if gpu else 0.6)


def summary(needs: list[CourseNeeds], gpu: bool, model: str = "large-v3-turbo") -> dict[str, Any]:
    audio = sum(n.audio_s for n in needs)
    return {"courses": [n.view() for n in needs], "lectures": sum(len(n.lectures) for n in needs),
            "audio_s": round(audio), "materials": sum(len(n.materials) for n in needs),
            "work_s": round(audio / speed(gpu, model)), "speed": speed(gpu, model), "gpu": gpu,
            "audio_mb": round(audio / 60 * 0.75),            # audio-only streams: ~0.75 MB a minute, deleted afterwards
            "needs_section": [n.code for n in needs if n.needs_section and n.waiting],
            "unknown": sum(len(n.unknown) for n in needs)}           # left out: not matched to your section


def queue_order(needs: list[CourseNeeds]) -> list[str]:
    """Every course's lectures in one list, newest first (the latest heads-ups across courses come first)."""
    return [did for did, _ in sorted((x for n in needs for x in n.lectures), key=lambda x: x[1], reverse=True)]
