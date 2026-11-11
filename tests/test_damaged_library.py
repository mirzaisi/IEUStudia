"""A damaged library.db (a disk that didn't keep its writes, a "PC cleaner"): the app puts it aside and goes back to
the newest copy it kept before an update (or a new library), says so on the first screen, and links the lectures
transcribed since. A library that is only locked, or a full disk, is never touched. The start-up screen's Try again
starts the library again."""

import datetime as dt
import os
import sqlite3

import pytest

from studia import __version__, paths, store
from studia.domain import Course, Recording, Segment, Transcript
from studia.library import Library
from studia.services import RELINK_KEY, Services


def _rec(did):
    return Recording(delivery_id=did, course_id="c1", folder_id="f", folder_path="", name=f"rec {did}",
                     start=dt.datetime(2026, 10, 13, 11), duration_s=2400, viewer_url="u")


def _transcript(did):
    return Transcript(did, [Segment(0, 5, "Vectors and their spans", "en")], 2400, 2000, {"en": 2000}, "small", "cpu",
                      "e1", "2026-10-13T12:00:00")


def _library_with_a_copy():
    """A library used by 4.0.0, opened by this version (which keeps backups\\library-4.0.0.db), then one more lecture
    transcribed: in its transcript file only, as the copy was made before it."""
    lib = Library(paths.db_path(), app_version="4.0.0")
    lib.upsert_courses([Course(id="c1", title="2026-1 MATH 240 P 1", code="MATH 240", name="P")])
    lib.merge_recordings("c1", [_rec("r1"), _rec("r2")], first_sync=True)
    lib.close()
    Library(paths.db_path(), app_version=__version__).close()
    assert (paths.profile_dir() / "backups" / "library-4.0.0.db").is_file()
    store.save_transcript(_transcript("r2"))


def _damage(path):
    with open(path, "r+b") as f:
        f.write(b"\0" * 100)                      # the header: "file is not a database"


def test_a_library_that_fails_to_open_is_closed_at_once(tmp_path):
    path = tmp_path / "library.db"
    Library(path).close()
    _damage(path)
    with pytest.raises(sqlite3.DatabaseError):
        Library(path)
    os.replace(path, tmp_path / "aside.db")       # Windows: "used by another process" while it stayed open


def test_a_damaged_library_comes_back_from_the_newest_good_copy(tmp_path):
    _library_with_a_copy()
    backups = paths.profile_dir() / "backups"
    (backups / "library-3.3.1.db").write_bytes(b"\0" * 4096)        # newer, but damaged too: skipped
    os.utime(backups / "library-3.3.1.db", (2e9, 2e9))
    _damage(paths.db_path())
    s = Services()
    try:
        assert [c.id for c in s.lib.courses(True)] == ["c1"]
        assert s.lib.transcribed_ids() == {"r2"} and s.lib.get_recording("r2").status == "done"
        assert len(s.notes) == 1 and "damaged" in s.notes[0] and str(paths.profile_dir()) in s.notes[0]
        aside = [p.name for p in paths.profile_dir().glob("library.db.damaged-*")]
        assert len(aside) == 1 and aside[0].count("-") == 2   # library.db.damaged-<day>-<time>
    finally:
        s.shutdown()
        s.lib.close()


def test_with_no_copy_a_new_library_starts_and_lectures_are_linked_after_a_sync(tmp_path):
    lib = Library(paths.db_path(), app_version=__version__)
    lib.upsert_courses([Course(id="c1", title="t", code="MATH 240", name="P")])
    lib.close()
    store.save_transcript(_transcript("r1"))
    _damage(paths.db_path())
    s = Services()
    try:
        assert s.lib.courses(True) == [] and "no copy" in s.notes[0]
        assert s.lib.get_meta(RELINK_KEY) == dt.date.today().isoformat()
        s.lib.upsert_courses([Course(id="c1", title="t", code="MATH 240", name="P")])     # a sync brings them back
        s.lib.merge_recordings("c1", [_rec("r1")], first_sync=True)
        s.sync.after_sync()
        assert s.lib.transcribed_ids() == {"r1"} and s.lib.get_recording("r1").status == "done"
    finally:
        s.shutdown()
        s.lib.close()


@pytest.mark.parametrize("code, message", [(5, "database is locked"), (13, "database or disk is full"),
                                           (10, "disk I/O error")])
def test_a_locked_library_or_a_full_disk_is_never_replaced(monkeypatch, code, message):
    from studia import services

    _library_with_a_copy()

    def failing(*args, **kwargs):
        exc = sqlite3.OperationalError(message)
        exc.sqlite_errorcode = code
        raise exc

    monkeypatch.setattr(services, "Library", failing)
    with pytest.raises(sqlite3.OperationalError):
        Services()
    assert not list(paths.profile_dir().glob("library.db.damaged-*"))


def test_the_command_line_never_replaces_the_library():
    _library_with_a_copy()
    _damage(paths.db_path())
    with pytest.raises(sqlite3.DatabaseError):
        Services(migrate_output=False)
    assert not list(paths.profile_dir().glob("library.db.damaged-*"))


def test_try_again_on_the_start_up_screen_starts_the_library_again():
    from studia.api import Api

    api = Api()
    tries = []

    def starter():
        tries.append(1)
        if len(tries) == 1:
            api._startup_failed("locked")
        else:
            api._set_services(Services())

    api._starter = starter
    starter()
    assert api.get_bootstrap()["ok"] is False
    assert api.start_again()["ok"]
    try:
        assert api.get_bootstrap()["ok"] is True and len(tries) == 2
    finally:
        api._services.shutdown()
        api._services.lib.close()
