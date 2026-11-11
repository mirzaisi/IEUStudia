"""Wires settings, library, sync, jobs, outputs, slides, export and AI together (used by the GUI and the CLI)."""

from __future__ import annotations

import datetime as dt
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

import requests

from . import __version__, store
from .config import Settings, apply_patch, load_settings, save_settings, unreadable as config_unreadable
from .domain import Course, Recording
from .i18n import N_, date_long, t
from .library import Library
from .log import setup_logging
from .outputs.mathmd import dollar_math
from .outputs.writer import (atomic_write, course_dir, day_file_name, day_stem, safe_name, write_day, write_local,
                             write_pack)
from .paths import db_path
from .sync import SyncService

log = logging.getLogger("studia")
RENAME_KEY = "output_renamed"          # library meta: an output folder rename that is not finished yet
TWINS_KEY = "twin_folders"             # library meta: "1" once the folders of courses sharing a code were put right
ADOPTED_KEY = "shared_audio_adopted"   # library meta: "1" once an added profile's audio from before 4.0 was moved
RELINK_KEY = "relink_transcripts"      # library meta: the day a damaged library was replaced (transcripts linked again)
MOVE_KEY = "output_moving"             # library meta: an output folder change whose copy isn't finished ({"from", "to"})
# AI notes: the end of the file name (English), and the label shown (t() it there)
NOTE_KINDS = {"notes": N_("Study notes"), "exam": N_("Exam focus"), "flashcards": N_("Flashcards")}
NOTE_SLIDES = 60          # slide pictures that go with a lecture's AI notes at most


def note_slides(images: list[Any]) -> list[Any]:
    """The slide pictures AI notes send: all of them, else NOTE_SLIDES spread over the whole lecture (the first 60
    were only the first part or two of a long day)."""
    n = len(images)
    if n <= NOTE_SLIDES:
        return list(images)
    return [images[round(j * (n - 1) / (NOTE_SLIDES - 1))] for j in range(NOTE_SLIDES)]


def _course_transcripts(lib: Library, course_recs: list[Recording]) -> dict[str, Any]:
    done = lib.transcribed_ids()
    return store.load_transcripts([r.delivery_id for r in course_recs if r.delivery_id in done])


def _slides_key(screens: list[tuple[Any, float]]) -> str:
    """What the slides of a recording were made from: the capture's version ("v2", slides.VERSION), then its screen
    streams (the file in each URL, without the per-request query) and their start offsets. Panopto processing the
    recording again gives new files. A stream whose stretches moved (a part cut out, a pause) adds them, so slides
    made before the edit are made again; one Panopto only split where nothing moved keeps the key it had. A camera
    stream read as the screen (a tablet) is marked "DV"."""
    from .slides import VERSION

    def moved(src: Any, offset: float) -> str:
        if all(abs(at - a - offset) <= 0.5 for a, _, at in src.spans):
            return ""
        return " " + ",".join(f"{a:.1f}-{b:.1f}@{at:.1f}" for a, b, at in src.spans)
    return f"v{VERSION}\n" + "\n".join(f"{urlsplit(src.url).path}@{offset:.1f}{moved(src, offset)}"
                                        f"{' DV' if getattr(src, 'tag', '') == 'DV' else ''}" for src, offset in screens)


def _unversioned(key: str) -> str:
    """The key as versions before 4.0 wrote it (no "v2" line): their capture of the same streams."""
    head, _, rest = key.partition("\n")
    return rest if head.startswith("v") and head[1:].isdigit() else key


SLIDES_TRIES = 3          # captures of the same streams that came out incomplete before what they got is kept as it is
CAMERA = "camera"         # a key file's first line: the recording's camera stream was looked at, and is no screen
PPT_PICTURE = re.compile(r"-p\d+\.jpg$")    # Panopto's own picture of a PowerPoint slide (screens.py), not captured


def _failed_tries(text: str, key: str) -> int:
    """How many captures from these screen streams came out incomplete (a key file "failed N" + the key)."""
    head, _, rest = text.partition("\n")
    if rest not in (key, _unversioned(key)) or not head.startswith("failed "):
        return 0
    try:
        return int(head[7:])
    except ValueError:
        return 0


def capture_version(delivery_id: str) -> int:
    """Which capture made a recording's pictures: 2 (4.0's), 1 (an earlier version, the 6 % comparison; an index
    without a key file too), 0 when it has none."""
    index = store.slides_path(delivery_id)
    if not index.is_file():
        return 0
    try:
        text = index.with_suffix(".key").read_text(encoding="utf-8")
    except OSError:
        return 1
    head = text.split("\n", 2)[1 if text.startswith("failed ") else 0] if text else ""
    return int(head[1:]) if head.startswith("v") and head[1:].isdigit() else 1


def _slides_done(delivery_id: str, key: str, streams: int) -> bool:
    """The recording's slides exist (index and every image) and were made from these screen streams (by this version
    or an earlier one: capturing them again is the student's choice, or the background job's for the old captures
    it knows to be short). An index from before the key was kept counts if the recording has one screen stream
    (older versions read only the first); it gets the key, so a change is noticed from then on. An incomplete capture
    is made again, up to SLIDES_TRIES times."""
    from .slides import load_index

    index = store.slides_path(delivery_id)
    if not index.is_file() or not all(os.path.isfile(s.path) for s in load_index(index) if s.path):
        return False
    key_file = index.with_suffix(".key")
    try:
        text = key_file.read_text(encoding="utf-8")
        return text in (key, _unversioned(key)) or _failed_tries(text, key) >= SLIDES_TRIES
    except FileNotFoundError:
        if streams != 1:
            return False
        try:
            key_file.write_text(_unversioned(key), encoding="utf-8")     # (made by an earlier version)
        except OSError:
            pass
        return True
    except OSError:
        return False


def _no_screen(delivery_id: str, key: str) -> bool:
    """The recording's camera stream was looked at before and shows no screen (a classroom camera)."""
    try:
        return store.slides_path(delivery_id).with_suffix(".key").read_text(encoding="utf-8") == f"{CAMERA}\n{key}"
    except OSError:
        return False


def sweep_audio_cache(lib: Library, keep_audio: bool, days: float = 7.0) -> int:
    """Delete downloaded audio (and parts of unfinished downloads) older than ``days`` whose lecture is not waiting
    in the queue saved across restarts. With "Keep downloaded audio" on, transcribed lectures keep theirs. Only this
    profile's lectures: the first profile's folder held every profile's audio before 4.0, and another profile's (kept
    by its own setting) waits there until that profile's next start moves it (adopt_shared_audio). Returns how many
    files were deleted."""
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


def _sweep_export_leftovers(root: Path, days: float = 1.0) -> int:
    """The unfinished zips (``<name>.zip.tmp``, up to hundreds of MB) of exports for an AI chat that IEUStudia closed in
    the middle of, or a power cut stopped: deleted a day later (never one being written now). Returns how many."""
    import time

    cutoff, n = time.time() - days * 86400, 0
    try:                                                   # (course_export.EXPORTS: not imported for the start)
        found = [*root.glob("Exports/*.zip.tmp"), *root.glob("*/Exports/*.zip.tmp")]
    except OSError:
        return 0
    for f in found:
        try:
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink()
                n += 1
        except OSError:
            continue
    if n:
        log.info("Deleted %d unfinished export zips", n)
    return n


def _good_backup(folder: Path) -> Optional[Path]:
    """The newest copy in ``backups`` (library-<version>.db, kept before an update) that reads well, opened read-only."""
    import sqlite3

    try:
        copies = sorted(folder.glob("library-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return None
    for p in copies:
        try:
            con = sqlite3.connect(p.as_uri() + "?mode=ro", uri=True)
            try:
                ok = con.execute("PRAGMA quick_check").fetchone()[0] == "ok"
            finally:
                con.close()
        except (sqlite3.Error, OSError, ValueError):
            ok = False
        if ok:
            return p
        log.warning("The library copy %s is damaged too", p)
    return None


def _written_at(db: Path) -> float:
    """When a library was last written (its write-ahead file is the newer one while it is open); 0 when it has none."""
    times = []
    for p in (db, db.with_name(db.name + "-wal")):
        try:
            times.append(p.stat().st_mtime)
        except OSError:
            pass
    return max(times, default=0.0)


def adopt_shared_audio(lib: Library, before: float) -> int:
    """An added profile's downloaded audio from before 4.0, when every profile's went to the first one's folder: moved
    once into its own folder, so its player and a lecture transcribed again find it there, and its own start-up sweep
    keeps or deletes it as its own setting says. Only files of its own lectures written before ``before`` (when its
    library was last written before this start): a newer one there is the first profile's own, downloaded since (a
    lecture both follow). Returns how many files were moved."""
    from .paths import audio_cache_dir, shared_audio_dir

    own, shared = audio_cache_dir(), shared_audio_dir()
    if own == shared or lib.get_meta(ADOPTED_KEY) == "1":
        return 0
    n = 0
    for f in list(shared.iterdir()) if shared.is_dir() else []:
        try:
            if (not f.is_file() or f.stat().st_mtime > before or (own / f.name).exists()
                    or lib.get_recording(f.name.split(".")[0]) is None):
                continue
            os.replace(f, own / f.name)
            n += 1
        except OSError as exc:          # (held by another program: it stays where it was)
            log.info("%s couldn't be moved to this profile's audio folder: %s", f.name, exc)
    lib.set_meta(ADOPTED_KEY, "1")
    return n


def _move_aside(src: Path, dest: Path) -> Path:
    """Move a file to ``dest``, never over another file: the same file there already lets this copy go, another one
    keeps both (the moved one as "name (2).ext")."""
    import filecmp

    if dest.exists():
        if dest.is_file() and src.is_file() and filecmp.cmp(src, dest, shallow=False):
            src.unlink()
            return dest
        n = 2
        while (dest.with_name(f"{dest.stem} ({n}){dest.suffix}")).exists():
            n += 1
        dest = dest.with_name(f"{dest.stem} ({n}){dest.suffix}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.replace(src, dest)
    return dest


def _unlink_quietly(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _shown_elsewhere(pictures: set[str], delivery_id: str) -> set[str]:
    """Which of these pictures another recording's slide index still shows: versions before 4.0 named them by the
    start minute only, so two lectures of a day that start in the same minute (files uploaded together) share them."""
    from .paths import transcripts_dir
    from .slides import load_index

    if not pictures:
        return set()
    wanted = {os.path.normcase(os.path.normpath(p)): p for p in pictures}
    out: set[str] = set()
    for f in transcripts_dir().glob("*.slides.json"):
        if f.name.split(".")[0] != delivery_id:
            out.update(wanted[k] for k in (os.path.normcase(os.path.normpath(s.path)) for s in load_index(f) if s.path)
                       if k in wanted)
    return out


def _glob_safe(text: str) -> str:
    """A file name as a glob pattern that matches only itself ("[" and "]" in a course name)."""
    return "".join(f"[{ch}]" if ch in "[]*?" else ch for ch in text)


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


def _capture(svc: Any, job: Any, screens: list[tuple[Any, float]], session: requests.Session,
             again: bool = False) -> tuple[list[Any], bool]:
    """slides_task's capture: (the slides, whether they were made now)."""
    from .cancel import Cancelled
    from .slides import STILL_SHARE, Incomplete, extract_slides, final_pass, still_share

    rec = svc.lib.get_recording(job.id)
    course = svc.lib.get_course(rec.course_id) if rec else None
    if rec is None or course is None or not screens:
        return (store.load_slides(job.id) if rec is not None else []), False
    key = _slides_key(screens)
    if not again and _slides_done(job.id, key, len(screens)):
        svc.log.info("Slides for %s are there already", job.id)
        return store.load_slides(job.id), False
    key_file = store.slides_path(job.id).with_suffix(".key")
    key_file.parent.mkdir(parents=True, exist_ok=True)
    if all(getattr(src, "tag", "") == "DV" for src, _ in screens):
        # no screen stream: the camera's is read like one only when it shows a screen (a tablet recorded as the
        # camera), told from its first keyframes (~3 MB); a classroom camera is looked at once
        if _no_screen(job.id, key):
            return [], False
        share = still_share(session, screens[0][0].url, cancel=job.cancel)
        svc.log.info("%s has no screen stream; its camera stream stands still in %s of its first frames",
                     job.id, "?" if share is None else f"{share:.0%}")
        if share is None:
            raise RuntimeError("The camera stream's first frames couldn't be fetched.")
        if share < STILL_SHARE:
            key_file.write_text(f"{CAMERA}\n{key}", encoding="utf-8")
            return [], False
    stem = day_stem(course, rec.start.date(), rec.kind)
    folder = course_dir(svc.output_root, course, svc.lib.courses(True)) / "slides" / stem
    try:
        tries = _failed_tries(key_file.read_text(encoding="utf-8"), key)
    except OSError:
        tries = 0
    before = {s.path for s in store.load_slides(job.id) if s.path}
    key_file.write_text("", encoding="utf-8")   # being made (images are written over): not done until the end
    slides: list[Any] = []
    seen: list[Any] = []                      # the pictures kept so far, shared by the streams
    failed: Optional[Exception] = None
    for n, (src, offset) in enumerate(screens):
        def read(i: int, total: int, n: int = n) -> None:    # how far it is (the jobs pane, once it waits for it)
            job.slides_read = (n + (i / total if total else 1.0)) / len(screens)
        try:
            # named by the start minute and the recording: several files uploaded together start in the same
            # minute, and one recording's slides wrote over the other's
            slides += extract_slides(session, src.url, folder, f"slides/{stem}", f"{rec.start:%H%M}-{job.id[:8]}",
                                     offset, progress=read, cancel=job.cancel, seen=seen, spans=src.spans)
        except Cancelled:
            raise
        except Exception as exc:  # noqa: BLE001 - the other streams' slides are still worth having
            svc.log.info("A screen stream of %s failed: %s", job.id, exc)
            if isinstance(exc, Incomplete):    # the connection dropped: what came is kept meanwhile
                slides += exc.slides
            failed = exc
    if failed is not None and not slides:
        raise failed
    slides = final_pass(slides, seen)
    store.save_slides(job.id, slides)
    # an earlier capture's pictures this one has no use for (Panopto's own slide pictures are the timeline's), unless
    # another lecture still shows them
    unused = {p for p in before - {s.path for s in slides} if not PPT_PICTURE.search(p)}
    for old in unused - _shown_elsewhere(unused, job.id):
        _unlink_quietly(Path(old))
    # complete; or a stream failed: made again next time, its slides so far kept after SLIDES_TRIES tries
    key_file.write_text(key if failed is None else f"failed {tries + 1}\n{key}", encoding="utf-8")
    svc.log.info("%d slides for %s", len({s.index for s in slides}), job.id)
    return slides, True


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
        written = _written_at(db_path())                           # (before this start writes it: adopt_shared_audio)
        self.lib = self._open_library(migrate_output)              # a new version keeps a copy first
        self.lib.index.notes, self.lib.index.note_days = self.notes_for_search, self.days_for_search
        # a command-line run while the app is open (it holds its single-instance lock): the lectures, chats and plans
        # the app is working on, and its files, are its own; nothing of theirs is reset, moved or swept from here
        self.beside_app = not migrate_output and _app_open()
        if not self.beside_app:
            self.lib.reset_interrupted()
        if migrate_output:
            self._migrate_output_root()
            self._finish_move()
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
        from .screens import ScreenWorker, _under_test

        self.screens = ScreenWorker(self)          # screen timelines of lectures transcribed before; captures asked for
        if self.beside_app:
            self.log.info("The app is open: its queue, plans and files are left to it")
            return
        if migrate_output and not _under_test():  # the app's own (never a command-line run, never a test)
            self.screens.start()
        self._relink_transcripts()
        self._write_unwritten()
        _sweep_export_leftovers(Path(self.settings.output_dir))
        self.sync.after_sync = self._after_sync
        try:
            self.move_days()                       # (also those a tray helper's check found)
        except Exception:  # noqa: BLE001 - never stop the start for it
            log.exception("Moving the files of lectures to their class day failed")
        try:
            self.repair_twin_folders()
        except Exception:  # noqa: BLE001 - never stop the start for it (tried again next time)
            log.exception("Putting the files of courses that share a code in their own folders failed")
        try:
            n = adopt_shared_audio(self.lib, written)
            if n:
                self.log.info("Moved %d downloaded audio files from before 4.0 to this profile's own folder", n)
        except OSError:
            self.log.debug("Moving this profile's audio from before 4.0 failed", exc_info=True)
        try:
            n = sweep_audio_cache(self.lib, self.settings.keep_audio)
            if n:
                self.log.info("Deleted %d old downloaded audio files", n)
        except OSError:
            self.log.debug("Audio cache sweep failed", exc_info=True)
        from .engine.checkpoint import sweep       # lectures stopped mid-transcription and never continued

        sweep()

    def _open_library(self, recover: bool) -> Library:
        """The profile's library. A damaged file (not a lock, a full disk or a read error: those are said as they
        are) is put aside, and the newest copy kept in backups that reads well takes its place, or a new library when
        there is none; the first screen says which. Only the app does this (``recover``): it holds the single-instance
        lock, so the tray helper or a command-line run never restores behind its back."""
        import shutil
        import sqlite3

        path = db_path()
        try:
            return Library(path, app_version=__version__)
        except sqlite3.DatabaseError as exc:
            if not recover or (getattr(exc, "sqlite_errorcode", 0) or 0) & 0xFF not in (sqlite3.SQLITE_CORRUPT,
                                                                                            sqlite3.SQLITE_NOTADB):
                raise
            damaged = exc
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        try:
            for end in ("-shm", "-wal", ""):        # the main file last: an old -wal never meets a new library
                src = path.with_name(path.name + end)
                if src.exists():
                    os.replace(src, path.with_name(f"{path.name}.damaged-{stamp}{end}"))
        except OSError:
            log.warning("The damaged library %s could not be put aside", path, exc_info=True)
            raise damaged from None
        log.warning("The library %s was damaged (%s): kept as %s.damaged-%s", path, damaged, path.name, stamp)
        backup = _good_backup(path.parent / "backups")
        if backup is not None:
            shutil.copyfile(backup, path)
            log.info("The library came back from %s", backup)
        lib = Library(path, app_version=__version__)
        lib.set_meta(RELINK_KEY, dt.date.today().isoformat())     # transcripts made after the copy: linked again
        if backup is not None:
            self.notes.append(t("Your library file was damaged, so IEUStudia went back to the copy it kept on {date}. "
                                "Chats, practice and plans from after that day may be missing. The damaged file is "
                                "kept in {folder}.", date=date_long(dt.date.fromtimestamp(backup.stat().st_mtime)),
                                folder=str(path.parent)))
        else:
            self.notes.append(t("Your library file was damaged and there was no copy to go back to, so IEUStudia "
                                "started a new one. Sync to bring your courses back. The damaged file is kept in "
                                "{folder}.", folder=str(path.parent)))
        return lib

    def _relink_transcripts(self) -> None:
        """After a library came back from a copy (or started anew): the lectures transcribed since are transcribed
        again in it (their transcripts are files of their own), once a sync brought their recordings back (for a
        month: syncs that missed a course, a course opened later)."""
        from .paths import transcripts_dir

        since = self.lib.get_meta(RELINK_KEY) or ""
        if not since:
            return
        try:
            if (dt.date.today() - dt.date.fromisoformat(since)).days > 30:
                raise ValueError(since)
        except ValueError:
            self.lib.set_meta(RELINK_KEY, "")
            return
        done, n = self.lib.transcribed_ids(), 0
        for f in transcripts_dir().glob("*.json"):
            did = f.name[:-5]
            if "." in did or did.startswith("local-") or did in done or self.lib.get_recording(did) is None:
                continue
            tr = store.load_transcript(did)
            if tr is not None:
                self.lib.save_transcript(tr, f)
                self.lib.set_status(did, "done")
                n += 1
        if n:
            self.log.info("%d transcribed lectures linked again in the library", n)

    def _after_sync(self) -> None:
        self._relink_transcripts()
        self.move_days()

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
        self.screens.shutdown()

    def capture_screens_again(self, day_key: str = "", course_id: str = "") -> int:
        """Capture the screens of a class day's (or a whole course's) transcribed lectures again, in the background
        (ScreenWorker). Returns how many lectures were added: class days, as the page counts them (not their
        recordings' parts)."""
        done = self.lib.transcribed_ids()
        if day_key:
            recs = self.lib.day_recordings(day_key)
        else:
            recs = sorted(self.lib.recordings(course_id), key=lambda r: r.start, reverse=True)
        recs = [r for r in recs if r.delivery_id in done and not r.delivery_id.startswith("local-")]
        added = set(self.screens.capture_again([r.delivery_id for r in recs]))
        return len({r.day_key for r in recs if r.delivery_id in added})

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
        if self._follow_moved_documents(old):
            return
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

    def _follow_moved_documents(self, old: Path) -> bool:
        """Windows moved the Documents folder after the set-up (OneDrive's "Back up your folders", or Location in
        its Properties), and the output folder in it went along: the app follows it there, says so once and returns
        True. Only a folder in the user's own local Documents counts, which is the one such a move takes away: a
        folder the student chose elsewhere (a USB stick that isn't plugged in) is waited for, as before."""
        from .paths import documents_dir

        docs, local = documents_dir(), Path.home() / "Documents"
        if old.parent != local or docs == local:
            return False
        new, apps = docs / old.name, self._app_folders()

        def holds(folder: Path) -> bool:
            return any((folder / n).is_dir() for n in apps)
        if not new.is_dir() or holds(old) or not (holds(new) or not old.exists()):
            return False
        try:
            old.rmdir()                         # the empty folder an earlier start made again (never one with files)
        except OSError:
            pass
        self._finish_rename(old, new)
        self.notes.append(t("Your Documents folder moved (for example to OneDrive), and your IEUStudia files went "
                            "with it. IEUStudia now uses {folder}.", folder=str(new)))
        return True

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
        import json
        import shutil

        old_root, new_root = Path(old_root), Path(new_root)
        if not old_root.exists() or old_root.resolve() == new_root.resolve():
            return 0

        def copy(src: str, dst: str) -> str:            # what a copy cut off before got across is not copied again
            try:
                if os.path.getsize(dst) == os.path.getsize(src):
                    return dst
            except OSError:
                pass
            return shutil.copy2(src, dst)

        # noted until it is done: IEUStudia closed in the middle, the next start goes on with it (_finish_move)
        self.lib.set_meta(MOVE_KEY, json.dumps({"from": str(old_root), "to": str(new_root)}, ensure_ascii=False))
        copied = 0
        for name in sorted(self._app_folders()):
            src = old_root / name
            if src.is_dir() and not new_root.resolve().is_relative_to(src.resolve()):
                shutil.copytree(src, new_root / name, dirs_exist_ok=True, copy_function=copy)
                copied += 1
        self.rebase_output_paths(old_root, new_root)
        self.lib.set_meta(MOVE_KEY, "")
        return copied

    def _finish_move(self) -> None:
        """A change of the output folder whose copy IEUStudia was closed in the middle of: it goes on now, before
        anything is written in the new folder (the files of courses not copied yet seemed gone, and their paths still
        named the old folder). One the student changed again since is let go; an old folder that isn't there (a drive
        not plugged in) waits for the next start."""
        import json

        try:
            begun = json.loads(self.lib.get_meta(MOVE_KEY) or "{}")
        except ValueError:
            begun = {}
        if not begun.get("to"):
            return
        if Path(begun["to"]) != Path(self.settings.output_dir):
            self.lib.set_meta(MOVE_KEY, "")
            return
        if not Path(begun["from"]).is_dir():
            return
        try:
            n = self.relocate_outputs(Path(begun["from"]), Path(begun["to"]))
            self.log.info("The copy to the new output folder went on: %d folders", n)
        except OSError:
            self.log.warning("Going on with the copy to the new output folder failed", exc_info=True)

    def move_days(self) -> int:
        """Transcribed lectures whose class day changed (Panopto's date was when the recording was uploaded, and the
        date in its name replaced it): their files are written under the new day. The old day is written again
        without them or, when no transcribed lecture is left there, its files are removed; its AI notes take the new
        day's name (when that is free) and its AI packs are made again on demand. Slide images stay where they are
        (the day files link them). The chats holding the old day get the new one. A lecture that moved to another of
        your courses' folders takes its AI notes and slide images to that course's folder (a chat stays with its
        course), and both courses' study packs are written again. Returns how many lectures moved. A move stays noted
        until it is done (doing one again is harmless)."""
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
                other = course.id != rec.course_id         # it moved to another of your courses
                try:
                    keep = any(r.delivery_id in done for r in self.lib.day_recordings(old_key)) \
                        or any(k == old_key for _, k in moves[n + 1:])     # (another moves from there next)
                    if not other:                  # (a chat sends only its own course's days)
                        self.lib.move_chat_day(old_key, rec.day_key, keep)
                    if other:
                        self._move_slides(course, all_courses, rec)
                    self._leave_day(course, all_courses, old_key, rec, done)
                    self.write_outputs(did, pack=False)
                except Exception as exc:           # e.g. a file open in another program: tried again next time
                    log.info("The files of %s can't be moved to its class day yet: %s", did, exc)
                    continue
                settled.append([did, old_key])
                courses.update({rec.course_id, course.id})
                moved += 1
                log.info("%s moved from %s to %s", did, old_key, rec.day_key)
                for day in (old_key, rec.day_key):          # its AI notes went with it: searched under the new day
                    self.lib.index.day_notes(day)
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
        # (to the folder of the course it is in now: another of your courses when it moved there)
        new_course = self.lib.get_course(rec.course_id) or course
        new_folder = course_dir(self.output_root, new_course, all_courses) if new_course.id != course.id else folder
        new_stem = day_stem(new_course, rec.start.date(), rec.kind)
        for note in NOTE_KINDS:
            old, new = self._notes_path(folder, stem, note), self._notes_path(new_folder, new_stem, note)
            if old.is_file() and not new.exists():
                new.parent.mkdir(parents=True, exist_ok=True)
                os.replace(old, new)
        for ext in ("pdf", "zip"):
            (folder / "AI packs" / safe_name(f"{stem} - AI pack.{ext}")).unlink(missing_ok=True)

    def _move_slides(self, course: Course, all_courses: list[Course], rec: Recording) -> None:
        """A lecture that moved to another of your courses: its slide images go to that course's folder, under its
        class day there (the day file there links them relative to that folder)."""
        slides = store.load_slides(rec.delivery_id)
        target = self.lib.get_course(rec.course_id)
        if not slides or target is None:
            return
        old_root = course_dir(self.output_root, course, all_courses).resolve()
        new_root = course_dir(self.output_root, target, all_courses)
        if new_root.resolve() == old_root:
            return
        stem = day_stem(target, rec.start.date(), rec.kind)
        moved: dict[str, tuple[str, str]] = {}           # old path -> (image, path): a slide shown twice is one file
        try:
            for s in slides:
                if s.path in moved:
                    s.image, s.path = moved[s.path]
                    continue
                src = Path(s.path)
                try:
                    if not src.is_file() or old_root not in src.resolve().parents:
                        continue
                except OSError:
                    continue
                name = src.name
                dest = new_root / "slides" / stem / name
                if dest.exists():                        # (another lecture's of that day: never written over)
                    name = f"{rec.delivery_id[:8]}-{name}"
                    dest = dest.with_name(name)
                dest.parent.mkdir(parents=True, exist_ok=True)
                os.replace(src, dest)                    # (one held by another program: the move is tried again)
                key = s.path
                moved[key] = (f"slides/{stem}/{name}", str(dest))
                s.image, s.path = moved[key]
        finally:
            if moved:                                    # what moved is noted, also when one couldn't move yet
                store.save_slides(rec.delivery_id, slides)
                for folder in {Path(p).parent for p in moved}:
                    try:
                        folder.rmdir()                   # only when nothing else is left in it
                    except OSError:
                        pass

    def repair_twin_folders(self) -> int:
        """Courses that share a code (a retake, another section) whose second one came before 3.3.1: until then a
        course's folder was named from the course list of the moment, so the first course's files stayed in the
        folder it had alone ("MATH 250") while the rest went to "MATH 250 (sec 1)", and two courses of the same
        section shared one folder. Once, for each such group: the course that came first keeps the folder that holds
        most of its files (it gets it back when 3.3.1 gave it to the other), each course's files that the library
        knows (downloaded files, slide pictures, AI notes and packs, study packs, cheatsheets and review sheets) go
        to its own folder, its day files are written there again, and nothing is deleted but the old copies of day
        files written again. A file that can't move yet (open in another program) is tried at the next start.
        Returns how many files moved."""
        if self.lib.get_meta(TWINS_KEY) == "1":
            return 0
        groups: dict[str, list[Course]] = {}
        for c in self.lib.courses(True):
            groups.setdefault(safe_name((c.code or c.name or f"Course {c.id}").strip()).lower(), []).append(c)
        moved, complete = 0, True
        for group in (g for g in groups.values() if len(g) > 1):
            try:
                moved += self._repair_twins(group)
            except OSError as exc:
                complete = False
                log.info("The folders of %s can't be put right yet: %s", group[0].code, exc)
        if complete:
            self.lib.set_meta(TWINS_KEY, "1")
        return moved

    def _repair_twins(self, group: list[Course]) -> int:
        """One group of courses that share a code (see repair_twin_folders)."""
        import re as _re

        root = Path(self.output_root)
        base = safe_name((group[0].code or group[0].name or f"Course {group[0].id}").strip())
        # the names IEUStudia gave these courses' folders, then and now (a folder of another name is yours: left alone)
        names = {base} | {c.folder for c in group if c.folder}
        for c in group:
            sec = f"sec {c.section}" if c.section else ""
            m = _re.match(r"\s*(\d{4}-\d)\b", c.title or "")
            names |= {f"{base} ({sec or c.id})", f"{base} ({c.id})", f"{base} {c.id}"}
            if m:
                names.add(f"{base} ({' '.join(x for x in (m.group(1), sec) if x)})")
        names = {safe_name(n).lower() for n in names}
        # (a folder that can't be read, on a drive not connected: OSError, and the repair is tried at the next start)
        cands = {d.name for d in root.iterdir() if d.is_dir() and d.name.lower() in names}
        cands |= {c.folder for c in group if c.folder}
        owned = self._twin_files(group, root, cands)
        counts: dict[str, dict[str, int]] = {}
        for cid, path, _ in owned:
            top = path.relative_to(root).parts[0]
            counts.setdefault(cid, {})[top] = counts.setdefault(cid, {}).get(top, 0) + 1
        stems: dict[str, set[str]] = {}
        for c in group:
            for r in self.lib.recordings(c.id):
                stems.setdefault(day_stem(c, r.start.date(), r.kind), set()).add(c.id)
        for stem, owners in stems.items():       # its day files too: where its lectures are
            for folder in cands if len(owners) == 1 else ():
                if (root / folder / f"{stem}.md").is_file():
                    (cid,) = owners
                    counts.setdefault(cid, {})[folder] = counts.setdefault(cid, {}).get(folder, 0) + 1
        arrived = self.lib.arrived()
        order = sorted(group, key=lambda c: (arrived.get(c.id) or "9999", c.id))
        taken: set[str] = set()
        folders: dict[str, str] = {}
        for c in order:                  # the first course first: it keeps the folder most of its files are in
            here = sorted(counts.get(c.id, {}).items(), key=lambda kv: (-kv[1], kv[0] != c.folder))
            name = next((f for f, _ in here if f.lower() not in taken), "")
            if not name and c.folder and c.folder.lower() not in taken:
                name = c.folder
            if not name:
                name = self._fresh_folder(c, base, root, taken | {x.lower() for x in cands})
            folders[c.id] = name
            taken.add(name.lower())
        changed = {cid: f for cid, f in folders.items() if f != next(c.folder for c in group if c.id == cid)}
        if changed:
            self.lib.set_folders(changed)
            log.info("Course folders of %s: %s", base, folders)
        moves: dict[str, str] = {}
        try:
            for cid, path, rel in owned:
                dest = root / folders[cid] / rel
                if path == dest or not path.exists():
                    continue
                new = _move_aside(path, dest)
                moves[str(path)] = str(new)
        finally:
            self._note_moves(moves)
        # a course whose files were elsewhere (moved now, or a day file in another of the group's folders): its day
        # files (and study pack) written again where they are now, the old copies gone. The others are left alone.
        stray = {cid for cid, per in counts.items() if any(f != folders[cid] for f in per)}
        redo = [c for c in group if c.id in stray or c.id in changed]
        for c in redo:
            self.rewrite_course(c.id)
        if redo:
            self._drop_old_day_files(group, root, cands, folders)
        if moves:
            log.info("%d files of %s moved to their own course's folder", len(moves), base)
        return len(moves)

    @staticmethod
    def _fresh_folder(course: Course, base: str, root: Path, taken: set[str]) -> str:
        """A folder name no course has and no folder on disk holds (as Library._name_folders names one)."""
        import re as _re

        sec = f"sec {course.section}" if course.section else ""
        m = _re.match(r"\s*(\d{4}-\d)\b", course.title or "")
        names = [f"{base} ({sec or course.id})"] + ([f"{base} ({' '.join(x for x in (m.group(1), sec) if x)})"]
                                                     if m else []) + [f"{base} ({course.id})", f"{base} {course.id}"]
        return next((n for n in map(safe_name, names) if n.lower() not in taken and not (root / n).exists()),
                    safe_name(f"{base} {course.id} {len(taken)}"))

    def _twin_files(self, group: list[Course], root: Path, cands: set[str]) -> list[tuple[str, Path, Path]]:
        """(course id, file, its place in a course folder) of every file of these courses that the library knows of and
        that is in one of their folders: downloaded files, slide pictures, study packs, cheatsheets and review sheets
        (each in the plan's result), and a class day's AI notes and AI packs (only when no other course of the group has
        a class day of the same name)."""
        ids = {c.id for c in group}
        out: dict[Path, tuple[str, Path, Path]] = {}

        def add(cid: str, value: str) -> None:
            if not value:
                return
            path = Path(value)
            try:
                rel = path.relative_to(root)
            except ValueError:
                return
            if len(rel.parts) > 1 and rel.parts[0] in cands and path.exists():
                out.setdefault(path, (cid, path, Path(*rel.parts[1:])))

        for _, cid, local in self.lib.material_paths(ids):
            add(cid, local)
        with_slides = store.slide_ids()
        stems: dict[str, set[str]] = {}
        for c in group:
            for r in self.lib.recordings(c.id):
                stems.setdefault(day_stem(c, r.start.date(), r.kind), set()).add(c.id)
                if r.delivery_id in with_slides:
                    for sl in store.load_slides(r.delivery_id):
                        add(c.id, sl.path)
        for cid in ids:
            for brief in self.lib.plans(cid):
                plan = self.lib.plan(brief["id"]) or {}
                result = plan.get("result") if isinstance(plan.get("result"), dict) else {}
                for key in ("file", "pdf"):
                    add(cid, str(result.get(key) or ""))
                if result.get("file"):
                    f = Path(str(result["file"]))
                    images = f.with_name(f"{f.stem} (images)")
                    if images.is_dir():
                        for pic in images.iterdir():
                            add(cid, str(pic))
        for stem, owners in stems.items():
            if len(owners) != 1:
                continue
            (cid,) = owners
            for folder in cands:
                for note in NOTE_KINDS:
                    add(cid, str(self._notes_path(root / folder, stem, note)))
                for ext in ("pdf", "zip"):
                    add(cid, str(root / folder / "AI packs" / safe_name(f"{stem} - AI pack.{ext}")))
        return list(out.values())

    def _note_moves(self, moves: dict[str, str]) -> None:
        """The paths kept for files that moved: downloaded files, plan files, slide pictures."""
        if not moves:
            return
        from .paths import transcripts_dir
        from .slides import load_index, save_index

        self.lib.move_paths(moves)
        for f in transcripts_dir().glob("*.slides.json"):
            slides, changed = load_index(f), False
            for sl in slides:
                if sl.path in moves:
                    sl.path, changed = moves[sl.path], True
            if changed:
                save_index(f, slides)

    def _drop_old_day_files(self, group: list[Course], root: Path, cands: set[str], folders: dict[str, str]) -> None:
        """A course's day files and subtitles written again in its own folder: the old copies in the group's other
        folders go (they are made from the transcripts, nothing else), as do the empty folders left over."""
        mine = {f.lower() for f in folders.values()}
        for c in group:
            own = root / folders[c.id]
            days = {day_stem(c, r.start.date(), r.kind) for r in self.lib.recordings(c.id)}
            others = {day_stem(x, r.start.date(), r.kind) for x in group if x.id != c.id for r in self.lib.recordings(x.id)}
            for stem in days - others:
                if not (own / f"{stem}.md").is_file():
                    continue
                for folder in cands - {folders[c.id]}:
                    _unlink_quietly(root / folder / f"{stem}.md")
                    subs = root / folder / "subtitles"
                    for srt in (subs.glob(f"{_glob_safe(stem)} - Part *.srt") if subs.is_dir() else []):
                        _unlink_quietly(srt)
        for folder in cands:
            if folder.lower() in mine:
                continue
            pack = root / folder / safe_name(f"{group[0].code or group[0].name} - Study pack.md")
            _unlink_quietly(pack)
            for d in sorted((p for p in (root / folder).rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)):
                try:
                    d.rmdir()            # only when empty
                except OSError:
                    pass
            try:
                (root / folder).rmdir()
            except OSError:
                pass

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

    def slides_task(self, job: Any, screens: list[tuple[Any, float]], session: requests.Session,
                    again: bool = False) -> list[Any]:
        """The slides of a recording from all its screen streams (one per stretch of screen sharing), numbered
        through, a slide shown again in a later stream kept once, and then its screen timeline (screens.py:
        PowerPoint's exact slide times, titles and text from ``job.info``, the words read on the other screens).
        Slides made before from the same streams are kept as they are, unless ``again`` (the student asked to capture
        them again). A camera stream (a recording with no screen stream) is first looked at: captured only when it
        shows a screen (a tablet)."""
        from .cancel import Cancelled
        from .screens import build_timeline, lecture_lock

        with lecture_lock(job.id):
            slides, fresh = _capture(self, job, screens, session, again)
            try:
                return build_timeline(self, job.id, getattr(job, "info", None), session, fresh=fresh,
                                      cancel=job.cancel) or slides
            except Cancelled:
                raise
            except Exception as exc:  # noqa: BLE001 - the pictures are there; the timeline is made again later
                self.log.warning("The screen timeline of %s couldn't be made: %s", job.id, exc, exc_info=True)
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

    def ai_inputs(self, day_key: str, include_slides: bool,
                  screens: str = "full") -> tuple[str, list[tuple[str, Path]], Course, list[Recording]]:
        """A class day for study notes: its compact text and (``include_slides``) its slide pictures, each with the
        label the AI cites it by, "[Part 2, slide 5]" (every part numbers its slides from 1, as the text marks them).
        ``screens``: the screen lines' words, or their titles only (compact_day)."""
        from .outputs.ai_copy import compact_day
        from .outputs.markdown import part_views

        course, recs = self.day(day_key)
        ids = [r.delivery_id for r in recs]
        trans = store.load_transcripts(ids)
        slides = {i: store.load_slides(i) for i in ids}
        text = compact_day(course, recs, trans, slides if include_slides else None, screens=screens)
        images: list[tuple[str, Path]] = []
        if include_slides:
            for p in part_views(recs, trans, slides):         # the parts the text has, numbered as there
                seen: set[int] = set()
                for s in p.slides if p.transcript else []:
                    if s.index not in seen and s.path and os.path.isfile(s.path):
                        seen.add(s.index)
                        images.append((f"[Part {p.index}, slide {s.index}]", Path(s.path)))
        return text, images, course, recs

    def ai_notes(self, day_key: str, template: str, include_slides: bool, model: str,
                 cancel: Optional[threading.Event] = None, on_text: Optional[Callable[[str], None]] = None,
                 effort: str = "") -> Path:
        from .ai import PLAN_AIS, PROVIDER_LABELS, provider, run_claude
        from .outputs.figures import note_slides_for, place_slides
        from .doclang import document_language
        from .outputs.prompts import ai_prompt, slides_note

        reads_images = provider() in PLAN_AIS
        # the offline model's request is tight: the screens' titles only
        text, images, course, recs = self.ai_inputs(day_key, include_slides and reads_images,
                                                    "titles" if provider() == "local" else "full")
        if not text:
            raise RuntimeError(t("Transcribe this class day first."))
        info = self.lib.course_info(course.id) if course is not None and getattr(self, "lib", None) else {}
        # (the language: Settings → AI, else the course's, as its quizzes and packs: the syllabus', else the lecture's)
        prompt = ai_prompt(template, **document_language({"syllabus": (info or {}).get("syllabus")},
                                                         getattr(self, "settings", None), {"pack": text}))
        sent = note_slides(images)
        if include_slides and sent:          # the slides, and (notes, exam focus) the ones to show beside the text
            prompt += slides_note(len(sent), len(images), figures=template != "flashcards")
        if "\n[Screen " in text:
            from .outputs.ai_copy import SCREEN_LINES

            prompt += f"\n\n{SCREEN_LINES}"
        stem = day_stem(course, recs[0].start.date(), recs[0].kind)
        path = self._notes_path(course_dir(self.output_root, course, self.lib.courses(True)), stem, template)
        # the folder can take the file, before the AI uses any of the student's plan: a drive not plugged in, a folder
        # Windows' ransomware protection or an antivirus guards, a full disk (a real write: mkdir alone passes then)
        path.parent.mkdir(parents=True, exist_ok=True)
        probe = path.with_name(path.name + ".tmp")
        probe.write_bytes(b"")
        probe.unlink(missing_ok=True)
        res = run_claude(prompt, text, sent, model=model, effort=effort, cancel=cancel, on_text=on_text)
        res["text"] = place_slides(res["text"], note_slides_for(sent), path.parent)
        usage = res.get("usage") or {}
        tokens_in = sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens",
                                                         "cache_read_input_tokens"))
        who = PROVIDER_LABELS.get(provider()) or "AI"
        footer = (f"\n\n---\n*Generated by {who} ({res.get('model')}) via IEUStudia · "
                  f"{tokens_in:,} input / {usage.get('output_tokens', 0):,} output tokens.*\n")
        body = dollar_math(res["text"].rstrip()) + footer
        try:
            atomic_write(path, body)                       # never half a file (the app closed, a power cut)
        except OSError as exc:                             # the old file held open (Word), the disk filled up meanwhile:
            from .net import plain_error                   # what the AI wrote is kept in the profile's own folder
            from .outputs.figures import linked_images
            from .paths import profile_dir, write_durably

            copy = profile_dir() / "Unsaved notes" / path.name
            copy.parent.mkdir(parents=True, exist_ok=True)
            write_durably(copy, linked_images(body, path.parent).encode("utf-8"))
            log.warning("AI notes %s could not be saved (%s): kept in %s", path, exc, copy)
            raise RuntimeError(t("{error} A copy of the notes is kept in {copy}.", error=plain_error(exc),
                                 copy=str(copy))) from exc
        with self.lib._lock:                              # found by the search from now on: committed here, as no
            self.lib.index.day_notes(day_key)              # Library write follows (closing the app dropped them, and
            self.lib.db.commit()                           # the open write locked the library for the command line)
        return path

    def notes_for_search(self, day_key: str) -> list[tuple[str, str, str, str]]:
        """A class day's AI notes for the search index: [(kind, label, Markdown, when written)]."""
        try:
            files = self.day_files(day_key)
        except (KeyError, OSError):
            return []
        out = []
        for kind in files.get("notes_kinds", []):
            p = Path(files[f"notes:{kind}"])
            try:
                out.append((kind, NOTE_KINDS.get(kind, NOTE_KINDS["notes"]), p.read_text(encoding="utf-8"),
                            dt.datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")))
            except OSError:
                continue
        return out

    def days_for_search(self) -> list[str]:
        """The class days that may have AI notes (those with a transcribed lecture)."""
        done = self.lib.transcribed_ids()
        return sorted({r.day_key for r in self.lib.recordings(include_gone=True) if r.delivery_id in done})
