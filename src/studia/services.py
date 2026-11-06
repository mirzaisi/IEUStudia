"""Wires settings, library, sync, jobs, outputs, slides, export and AI together (used by the GUI and the CLI)."""

from __future__ import annotations

import datetime as dt
import logging
import os
import threading
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

import requests

from . import __version__, store
from .config import Settings, apply_patch, load_settings, save_settings, unreadable as config_unreadable
from .domain import Course, Recording
from .i18n import N_, t
from .library import Library
from .log import setup_logging
from .outputs.mathmd import dollar_math
from .outputs.writer import (atomic_write, course_dir, day_file_name, day_stem, safe_name, write_day, write_local,
                             write_pack)
from .paths import db_path
from .sync import SyncService

log = logging.getLogger("studia")
RENAME_KEY = "output_renamed"          # library meta: an output folder rename that is not finished yet
# AI notes: the end of the file name (English), and the label shown (t() it there)
NOTE_KINDS = {"notes": N_("Study notes"), "exam": N_("Exam focus"), "flashcards": N_("Flashcards")}


def _course_transcripts(lib: Library, course_recs: list[Recording]) -> dict[str, Any]:
    done = lib.transcribed_ids()
    return store.load_transcripts([r.delivery_id for r in course_recs if r.delivery_id in done])


def _slides_key(screens: list[tuple[Any, float]]) -> str:
    """What the slides of a recording were made from: its screen streams (the file in each URL, without the
    per-request query) and their start offsets. Panopto processing the recording again gives new files."""
    return "\n".join(f"{urlsplit(src.url).path}@{offset:.1f}" for src, offset in screens)


SLIDES_TRIES = 3          # captures of the same streams that came out incomplete before what they got is kept as it is


def _failed_tries(text: str, key: str) -> int:
    """How many captures from these screen streams came out incomplete (a key file "failed N" + the key)."""
    head, _, rest = text.partition("\n")
    if rest != key or not head.startswith("failed "):
        return 0
    try:
        return int(head[7:])
    except ValueError:
        return 0


def _slides_done(delivery_id: str, key: str, streams: int) -> bool:
    """The recording's slides exist (index and every image) and were made from these screen streams. An index from
    before the key was kept counts if the recording has one screen stream (older versions read only the first); it
    gets the key, so a change is noticed from then on. An incomplete capture is made again, up to SLIDES_TRIES times."""
    from .slides import load_index

    index = store.slides_path(delivery_id)
    if not index.is_file() or not all(os.path.isfile(s.path) for s in load_index(index) if s.path):
        return False
    key_file = index.with_suffix(".key")
    try:
        text = key_file.read_text(encoding="utf-8")
        return text == key or _failed_tries(text, key) >= SLIDES_TRIES
    except FileNotFoundError:
        if streams != 1:
            return False
        try:
            key_file.write_text(key, encoding="utf-8")
        except OSError:
            pass
        return True
    except OSError:
        return False


def sweep_audio_cache(lib: Library, keep_audio: bool, days: float = 7.0) -> int:
    """Delete downloaded audio (and parts of unfinished downloads) older than ``days`` whose lecture is not waiting
    in the queue saved across restarts. With "Keep downloaded audio" on, transcribed lectures keep theirs. Only this
    profile's lectures: the folder is shared, and another profile's queue and setting aren't known here. Returns
    how many files were deleted."""
    import json
    import time

    from .jobs import QUEUE_KEY
    from .paths import audio_cache_dir

    try:
        queued = set(json.loads(lib.get_meta(QUEUE_KEY) or "[]"))
    except (ValueError, TypeError):
        return 0                                   # the queue can't be read: never delete a queued lecture's audio
    cutoff, n = time.time() - days * 86400, 0
    for f in audio_cache_dir().iterdir():
        did = f.name.split(".")[0]
        try:
            if not f.is_file() or did in queued or f.stat().st_mtime > cutoff:
                continue
            rec = lib.get_recording(did)
            if rec is None or (keep_audio and rec.status == "done"):
                continue
            f.unlink()
            n += 1
        except OSError:
            continue
    return n


def _app_open() -> bool:
    """The app's window is open (in another process: it holds the single-instance lock)."""
    try:
        from .agent import ui_running

        return ui_running()
    except Exception:  # noqa: BLE001 - unknown: as before
        log.debug("Checking for the open app failed", exc_info=True)
        return False


def _file_warnings(lib: Library, course_recs: list[Recording]) -> dict[str, list[dict[str, Any]]]:
    """The recordings' warnings as the files show them: "could not be written" never goes into the files themselves."""
    every = lib.all_warnings()
    return {r.delivery_id: [w for w in every.get(r.delivery_id, []) if w.get("code") != "not_written"]
            for r in course_recs}


class Services:
    def __init__(self, debug: bool = False, migrate_output: bool = True) -> None:
        """``migrate_output``: rename an output folder of the app's earlier name (only the app does that, never a
        command-line run next to it)."""
        self.log = setup_logging(debug)
        self._lock = threading.RLock()
        self._listeners: list[Callable[[dict[str, Any]], None]] = []
        self.settings: Settings = load_settings()
        self.notes: list[str] = []                 # told on the first screen (get_bootstrap)
        if config_unreadable():
            from .paths import settings_path

            self.notes.append(t("IEUStudia couldn't read your settings, so it started with the default ones. Check "
                                "your sections and your IEUStudia folder in Settings. The old file is kept in {folder}.",
                                folder=str(settings_path().parent)))
        self.lib = Library(db_path(), app_version=__version__)    # a new version keeps a copy first
        # a command-line run while the app is open (it holds its single-instance lock): the lectures, chats and plans
        # the app is working on, and its files, are its own; nothing of theirs is reset, moved or swept from here
        self.beside_app = not migrate_output and _app_open()
        if not self.beside_app:
            self.lib.reset_interrupted()
        if migrate_output:
            self._migrate_output_root()
        self.sync = SyncService(self.lib, lambda: self.settings, self.log)
        from .chat import ChatService
        from .jobs import JobManager
        from .materials import MaterialStore

        self.jobs = JobManager(self.lib, self.sync, lambda: self.settings, self._make_engine, self.emit,
                               self.write_outputs, self.slides_task, self.log)
        self.chat = ChatService(self)
        self.materials = MaterialStore(self)
        from .plans import PlanRunner

        self.plans = PlanRunner(self)
        if self.beside_app:
            self.log.info("The app is open: its queue, plans and files are left to it")
            return
        self._write_unwritten()
        self.sync.after_sync = self.move_days
        try:
            self.move_days()                       # (also those a tray helper's check found)
        except Exception:  # noqa: BLE001 - never stop the start for it
            log.exception("Moving the files of lectures to their class day failed")
        try:
            n = sweep_audio_cache(self.lib, self.settings.keep_audio)
            if n:
                self.log.info("Deleted %d old downloaded audio files", n)
        except OSError:
            self.log.debug("Audio cache sweep failed", exc_info=True)
        from .engine.checkpoint import sweep       # lectures stopped mid-transcription and never continued

        sweep()

    def _write_unwritten(self) -> None:
        """Write the files of transcripts whose files could not be written before (a file was open in another
        program, the folder was blocked or offline)."""
        for did in self.jobs.unwritten():
            try:
                self.write_outputs(did)
            except OSError as exc:
                log.info("The files of %s still can't be written: %s", did, exc)
                continue
            except Exception:  # noqa: BLE001 - e.g. the recording is gone; never stop the start for it
                log.exception("Writing the files of %s failed", did)
            self.jobs.note_unwritten(did, False)
            if not did.startswith("local-"):
                self.lib.set_warnings(did, [w for w in self.lib.warnings(did) if w.get("code") != "not_written"])

    def shutdown(self) -> None:
        self.plans.shutdown()
        self.chat.shutdown()

    def _migrate_output_root(self) -> None:
        """An output folder that still has the app's earlier name (Documents\\Studia or Documents\\LectureScribe, a
        profile's Documents\\Studia - <name>) gets the IEUStudia name once: it is renamed (same drive, so instant)
        unless the new name is taken, and the paths stored for its files follow. Folders the user chose keep theirs.

        The rename is noted in the library right away, so a start that stops before the paths and the setting follow
        (they can take seconds) is finished by the next one, even if something put a folder of the old name back."""
        import json

        from .paths import _home_override, renamed_output_path

        if _home_override() is not None:
            return
        try:
            begun = json.loads(self.lib.get_meta(RENAME_KEY) or "{}")
        except ValueError:
            begun = {}
        if begun.get("to") and Path(begun["to"]).is_dir():
            self._finish_rename(Path(begun["from"]), Path(begun["to"]))
            return
        old = Path(self.settings.output_dir)
        new = renamed_output_path(old)
        if new is None or not old.parent.is_dir():
            return
        try:
            if old.is_dir():
                if new.is_dir() and not any(new.iterdir()):
                    new.rmdir()                                # empty, e.g. made by an earlier start
                if not new.exists():
                    os.replace(old, new)
                    self.lib.set_meta(RENAME_KEY, json.dumps({"from": str(old), "to": str(new)}, ensure_ascii=False))
                elif any((old / name).is_dir() for name in self._app_folders()):
                    self.log.info("Keeping the output folder %s: %s exists too", old, new)
                    return
                # else another profile sharing the folder renamed it, and something put the old name back
            elif not new.is_dir():
                return                                         # neither is there (a drive not connected?): wait
        except OSError:                                        # e.g. a file in it is open: try again next start
            self.log.warning("Could not rename %s to %s", old, new, exc_info=True)
            return
        self._finish_rename(old, new)

    def _finish_rename(self, old: Path, new: Path) -> None:
        self.rebase_output_paths(old, new)
        self.update_settings({"output_dir": str(new)})
        self.lib.set_meta(RENAME_KEY, "")
        self.log.info("Output folder %s -> %s", old, new)

    def _app_folders(self) -> set[str]:
        """The folders the app writes in the output folder: one per course, local files, weekly digests."""
        from .outputs.writer import course_folder_name

        courses = self.lib.courses(True)
        return {course_folder_name(c, courses) for c in courses} | {"Local files", "Weekly digests"}

    def rebase_output_paths(self, old_root: Path, new_root: Path) -> None:
        """Point the paths stored for files in ``old_root`` (slide images, downloaded materials, plan and digest
        files) at the same files in ``new_root``, where they exist."""
        from .paths import transcripts_dir
        from .slides import load_index, save_index

        old_root, new_root = Path(old_root), Path(new_root)

        def moved(value: str) -> Optional[str]:
            try:
                path = new_root / Path(value).relative_to(old_root)
            except ValueError:                                 # not in the old folder (or not a path)
                return None
            return str(path) if path.exists() else None

        self.lib.rebase_paths(moved)
        for f in transcripts_dir().glob("*.slides.json"):
            slides, changed = load_index(f), False
            for s in slides:
                new = moved(s.path)
                if new:
                    s.path, changed = new, True
            if changed:
                save_index(f, slides)
                store.forget(f.name.split(".")[0])

    # ------------------------------------------------------------------ events / settings
    def subscribe(self, fn: Callable[[dict[str, Any]], None]) -> None:
        self._listeners.append(fn)

    def emit(self, evt: dict[str, Any]) -> None:
        for fn in list(self._listeners):
            try:
                fn(evt)
            except Exception:  # noqa: BLE001 - a broken listener must not break jobs
                self.log.debug("listener failed", exc_info=True)

    def update_settings(self, patch: dict[str, Any]) -> Settings:
        with self._lock:
            self.settings = apply_patch(self.settings, patch)
            save_settings(self.settings)
        return self.settings

    @staticmethod
    def _make_engine(settings: Settings) -> Any:
        if os.environ.get("STUDIA_FAKE_ENGINE") == "1":
            from .engine.stub import StubEngine

            return StubEngine()
        from .engine import Engine

        return Engine(settings.model, settings.device, settings.languages)

    # ------------------------------------------------------------------ lookups
    @property
    def output_root(self) -> Path:
        p = Path(self.settings.output_dir)
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError as exc:     # e.g. Windows' ransomware protection guards Documents: writing a file says so
            log.warning("The output folder %s can't be created: %s", p, exc)
        return p

    def day(self, day_key: str) -> tuple[Course, list[Recording]]:
        course_id = day_key.split("|", 1)[0]
        course = self.lib.get_course(course_id)
        if course is None:
            raise KeyError(t("Unknown course"))
        recs = sorted(self.lib.day_recordings(day_key), key=lambda r: r.start)
        if not recs:
            raise KeyError(t("Unknown class day"))
        return course, recs

    def day_files(self, day_key: str) -> dict[str, Any]:
        """The day's files that exist: "md", "pack", "pdf", "zip", "notes:<kind>" for each kind of AI notes,
        "notes" (the newest of those) and "notes_kinds" (newest first)."""
        course, recs = self.day(day_key)
        all_courses = self.lib.courses(True)
        folder = course_dir(self.output_root, course, all_courses)
        md = folder / day_file_name(course, recs[0].start.date(), recs[0].kind)
        stem = day_stem(course, recs[0].start.date(), recs[0].kind)
        pack = folder / safe_name(f"{course.code or course.name} - Study pack.md")
        out: dict[str, Any] = {"folder": str(folder), "md": str(md) if md.exists() else "",
                               "pack": str(pack) if pack.exists() else ""}
        pdf = folder / "AI packs" / safe_name(f"{stem} - AI pack.pdf")
        zp = folder / "AI packs" / safe_name(f"{stem} - AI pack.zip")
        out.update(pdf=str(pdf) if pdf.exists() else "", zip=str(zp) if zp.exists() else "")
        notes = []
        for kind in NOTE_KINDS:
            p = self._notes_path(folder, stem, kind)
            if p.exists():
                out[f"notes:{kind}"] = str(p)
                notes.append((p.stat().st_mtime, kind))
        notes.sort(reverse=True)
        out["notes_kinds"] = [k for _, k in notes]
        out["notes"] = out[f"notes:{notes[0][1]}"] if notes else ""
        return out

    @staticmethod
    def _notes_path(folder: Path, stem: str, kind: str) -> Path:
        return folder / "AI notes" / safe_name(f"{stem} - {NOTE_KINDS.get(kind, NOTE_KINDS['notes'])}.md")

    # ------------------------------------------------------------------ outputs
    def write_outputs(self, delivery_id: str, pack: bool = True) -> dict[str, Any]:
        """Write the files of a transcript's class day and (``pack``) its course's study pack."""
        if delivery_id.startswith("local-"):
            transcript = store.load_transcript(delivery_id)
            if transcript is None:
                return {}
            name = (transcript.meta or {}).get("source_path", delivery_id)
            return {"md": write_local(self.output_root, name, transcript)}
        rec = self.lib.get_recording(delivery_id)
        if rec is None:
            return {}
        course, day_recs = self.day(rec.day_key)
        all_courses = self.lib.courses(True)
        course_recs = self.lib.recordings(course.id)
        trans_all = _course_transcripts(self.lib, course_recs)
        ids = [r.delivery_id for r in day_recs]
        slides = {i: store.load_slides(i) for i in ids}
        return write_day(self.output_root, course, all_courses, day_recs, {i: trans_all[i] for i in ids if i in trans_all},
                         course_recs, trans_all, self.settings.write_srt, slides, _file_warnings(self.lib, course_recs),
                         pack=pack)

    def write_course_pack(self, course_id: str) -> Optional[Path]:
        """Write a course's study pack (after its days were written with ``pack=False``)."""
        course = self.lib.get_course(course_id)
        if course is None:
            return None
        course_recs = self.lib.recordings(course_id)
        return write_pack(self.output_root, course, self.lib.courses(True), course_recs,
                          _course_transcripts(self.lib, course_recs), _file_warnings(self.lib, course_recs))

    def relocate_outputs(self, old_root: Path, new_root: Path) -> int:
        """Copy the app's folders (course folders with transcripts, slides, materials, packs and notes; weekly
        digests) to a new output folder and point the stored paths there. The old folder is left untouched. Returns
        the number of folders copied."""
        import shutil

        old_root, new_root = Path(old_root), Path(new_root)
        if not old_root.exists() or old_root.resolve() == new_root.resolve():
            return 0
        copied = 0
        for name in sorted(self._app_folders()):
            src = old_root / name
            if src.is_dir() and not new_root.resolve().is_relative_to(src.resolve()):
                shutil.copytree(src, new_root / name, dirs_exist_ok=True)
                copied += 1
        self.rebase_output_paths(old_root, new_root)
        return copied

    def move_days(self) -> int:
        """Transcribed lectures whose class day changed (Panopto's date was when the recording was uploaded, and the
        date in its name replaced it): their files are written under the new day. The old day is written again
        without them or, when no transcribed lecture is left there, its files are removed; its AI notes take the new
        day's name (when that is free) and its AI packs are made again on demand. Slide images stay where they are
        (the day files link them). The chats holding the old day get the new one. Returns how many lectures moved.
        A move stays noted until it is done (doing one again is harmless)."""
        moves = self.lib.day_moves()
        if not moves:
            return 0
        done = self.lib.transcribed_ids()
        all_courses = self.lib.courses(True)
        courses: set[str] = set()
        settled: list[list[str]] = []
        moved = 0
        try:
            for n, (did, old_key) in enumerate(moves):
                rec = self.lib.get_recording(did)
                course = self.lib.get_course(old_key.split("|", 1)[0])
                if rec is None or course is None or did not in done or rec.day_key == old_key:
                    settled.append([did, old_key])
                    continue
                try:
                    keep = any(r.delivery_id in done for r in self.lib.day_recordings(old_key)) \
                        or any(k == old_key for _, k in moves[n + 1:])     # (another moves from there next)
                    self.lib.move_chat_day(old_key, rec.day_key, keep)
                    self._leave_day(course, all_courses, old_key, rec, done)
                    self.write_outputs(did, pack=False)
                except Exception as exc:           # e.g. a file open in another program: tried again next time
                    log.info("The files of %s can't be moved to its class day yet: %s", did, exc)
                    continue
                settled.append([did, old_key])
                courses.add(rec.course_id)
                moved += 1
                log.info("%s moved from %s to %s", did, old_key, rec.day_key)
        finally:
            self.lib.drop_day_moves(settled)
        for cid in courses:
            try:
                self.write_course_pack(cid)
            except OSError as exc:
                log.info("The study pack of %s can't be written: %s", cid, exc)
        return moved

    def _leave_day(self, course: Course, all_courses: list[Course], old_key: str, rec: Recording,
                   done: set[str]) -> None:
        _, date_s, kind = old_key.split("|")
        old_date = dt.date.fromisoformat(date_s)
        folder = course_dir(self.output_root, course, all_courses)
        stem = day_stem(course, old_date, kind)
        subs = folder / "subtitles"
        if self.settings.write_srt and subs.is_dir():    # numbered by part: written again for what is left
            for f in subs.iterdir():
                if f.name.startswith(f"{stem} - Part ") and f.suffix == ".srt":
                    f.unlink()
        left = [r for r in self.lib.day_recordings(old_key) if r.delivery_id in done]
        if left:
            self.write_outputs(left[0].delivery_id, pack=False)
            return
        (folder / day_file_name(course, old_date, kind)).unlink(missing_ok=True)
        new_stem = day_stem(course, rec.start.date(), rec.kind)
        for note in NOTE_KINDS:
            old, new = self._notes_path(folder, stem, note), self._notes_path(folder, new_stem, note)
            if old.is_file() and not new.exists():
                os.replace(old, new)
        for ext in ("pdf", "zip"):
            (folder / "AI packs" / safe_name(f"{stem} - AI pack.{ext}")).unlink(missing_ok=True)

    def rewrite_course(self, course_id: str) -> None:
        course = self.lib.get_course(course_id)
        if course is None:
            return
        done = self.lib.transcribed_ids()
        seen = set()
        for r in self.lib.recordings(course_id):
            if r.delivery_id in done and r.day_key not in seen:
                seen.add(r.day_key)
                self.write_outputs(r.delivery_id, pack=False)
        if seen:                        # the pack holds every day: once, not once per day
            self.write_course_pack(course_id)

    def slides_task(self, job: Any, screens: list[tuple[Any, float]], session: requests.Session) -> list[Any]:
        """The slides of a recording from all its screen streams (one per stretch of screen sharing), numbered
        through, a slide shown again in a later stream kept once. Slides made before from the same streams are
        kept as they are."""
        from .cancel import Cancelled
        from .slides import Incomplete, extract_slides

        rec = self.lib.get_recording(job.id)
        course = self.lib.get_course(rec.course_id) if rec else None
        if rec is None or course is None:
            return []
        key = _slides_key(screens)
        if _slides_done(job.id, key, len(screens)):
            self.log.info("Slides for %s are there already", job.id)
            return store.load_slides(job.id)
        stem = day_stem(course, rec.start.date(), rec.kind)
        folder = course_dir(self.output_root, course, self.lib.courses(True)) / "slides" / stem
        key_file = store.slides_path(job.id).with_suffix(".key")
        key_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            tries = _failed_tries(key_file.read_text(encoding="utf-8"), key)
        except OSError:
            tries = 0
        key_file.write_text("", encoding="utf-8")   # being made (images are written over): not done until the end
        slides: list[Any] = []
        seen: list[Any] = []                      # the slides kept so far, shared by the streams
        failed: Optional[Exception] = None
        for src, offset in screens:
            try:
                slides += extract_slides(session, src.url, folder, f"slides/{stem}", rec.start.strftime("%H%M"), offset,
                                         cancel=job.cancel, seen=seen)
            except Cancelled:
                raise
            except Exception as exc:  # noqa: BLE001 - the other streams' slides are still worth having
                self.log.info("A screen stream of %s failed: %s", job.id, exc)
                if isinstance(exc, Incomplete):    # the connection dropped: what came is kept meanwhile
                    slides += exc.slides
                failed = exc
        if failed is not None and not slides:
            raise failed
        store.save_slides(job.id, slides)
        # complete; or a stream failed: made again next time, its slides so far kept after SLIDES_TRIES tries
        key_file.write_text(key if failed is None else f"failed {tries + 1}\n{key}", encoding="utf-8")
        self.log.info("%d slides for %s", len({s.index for s in slides}), job.id)
        return slides

    def export_pack(self, day_key: str) -> dict[str, Path]:
        from .export import build_ai_pack

        course, recs = self.day(day_key)
        ids = [r.delivery_id for r in recs]
        trans = store.load_transcripts(ids)
        if not trans:
            raise RuntimeError(t("Transcribe this class day first."))
        slides = {i: store.load_slides(i) for i in ids}
        return build_ai_pack(self.output_root, course, self.lib.courses(True), recs, trans, slides)

    def ai_inputs(self, day_key: str, include_slides: bool) -> tuple[str, list[Path], Course, list[Recording]]:
        from .outputs.ai_copy import compact_day

        course, recs = self.day(day_key)
        ids = [r.delivery_id for r in recs]
        trans = store.load_transcripts(ids)
        slides = {i: store.load_slides(i) for i in ids}
        text = compact_day(course, recs, trans, slides if include_slides else None)
        images: list[Path] = []
        if include_slides:
            seen = set()
            for i in ids:
                for s in slides.get(i, []):
                    if s.path and s.path not in seen and os.path.isfile(s.path):
                        seen.add(s.path)
                        images.append(Path(s.path))
        return text, images, course, recs

    def ai_notes(self, day_key: str, template: str, include_slides: bool, model: str,
                 cancel: Optional[threading.Event] = None, on_text: Optional[Callable[[str], None]] = None,
                 effort: str = "") -> Path:
        from .ai import PLAN_AIS, PROVIDER_LABELS, provider, run_claude
        from .outputs.prompts import ai_prompt

        reads_images = provider() in PLAN_AIS
        text, images, course, recs = self.ai_inputs(day_key, include_slides and reads_images)
        if not text:
            raise RuntimeError(t("Transcribe this class day first."))
        prompt = ai_prompt(template)
        if include_slides and images:
            prompt += ("\n\nThe slide images are attached in order; the transcript marks each paragraph with "
                       "(slide N), the slide that was on screen.")
        res = run_claude(prompt, text, images[:60], model=model, effort=effort, cancel=cancel, on_text=on_text)
        stem = day_stem(course, recs[0].start.date(), recs[0].kind)
        path = self._notes_path(course_dir(self.output_root, course, self.lib.courses(True)), stem, template)
        path.parent.mkdir(parents=True, exist_ok=True)
        usage = res.get("usage") or {}
        tokens_in = sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens",
                                                         "cache_read_input_tokens"))
        who = PROVIDER_LABELS.get(provider()) or "AI"
        footer = (f"\n\n---\n*Generated by {who} ({res.get('model')}) via IEUStudia · "
                  f"{tokens_in:,} input / {usage.get('output_tokens', 0):,} output tokens.*\n")
        # never half a file (the app closed, a power cut)
        atomic_write(path, dollar_math(res["text"].rstrip()) + footer)
        return path
