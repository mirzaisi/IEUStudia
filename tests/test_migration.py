"""The app's earlier names (Studia, LectureScribe): the data folder and the default output folders get the IEUStudia
name once, the stored paths follow them, and nothing is lost."""

import json
import os

import pytest

from studia import accounts, paths, services
from studia.config import Settings, load_settings, save_settings
from studia.coursesite import Resource, Unit
from studia.domain import Course
from studia.library import Library
from studia.services import Services
from studia.slides import Slide, load_index, save_index


def _real_mode(tmp_path, monkeypatch):
    monkeypatch.delenv("STUDIA_HOME", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "local"))     # Linux
    monkeypatch.setattr(paths, "documents_dir", lambda: tmp_path / "docs")
    monkeypatch.setattr(accounts, "documents_dir", lambda: tmp_path / "docs")
    paths._legacy_in_use.cache_clear()


def test_data_folder_is_moved_once(tmp_path, monkeypatch):
    _real_mode(tmp_path, monkeypatch)
    old = tmp_path / "local" / "LectureScribe"
    (old / "transcripts").mkdir(parents=True)
    (old / "library.db").write_text("db")
    (old / "transcripts" / "x.json").write_text("{}")
    assert paths.app_dir() == old                       # not moved yet (old app running?): keep using it
    assert paths.migrate_legacy_data() == "moved"
    new = tmp_path / "local" / "IEUStudia"
    assert (new / "library.db").read_text() == "db" and (new / "transcripts" / "x.json").exists()
    assert not old.exists() and paths.app_dir() == new
    assert paths.migrate_legacy_data() == "none"


def test_studia_data_folder_wins_over_an_older_one(tmp_path, monkeypatch):
    _real_mode(tmp_path, monkeypatch)
    studia, lecturescribe = tmp_path / "local" / "Studia", tmp_path / "local" / "LectureScribe"
    for folder, text in ((studia, "studia"), (lecturescribe, "old")):
        folder.mkdir(parents=True)
        (folder / "library.db").write_text(text)
    (studia / "chatgpt.bin").write_bytes(b"\x01")
    assert paths.app_dir() == studia
    assert paths.migrate_legacy_data() == "moved"
    new = tmp_path / "local" / "IEUStudia"
    assert (new / "library.db").read_text() == "studia" and (new / "chatgpt.bin").exists()
    assert not studia.exists() and lecturescribe.exists() and paths.app_dir() == new


def test_data_folder_merges_into_a_new_folder_without_library(tmp_path, monkeypatch):
    _real_mode(tmp_path, monkeypatch)
    old, new = tmp_path / "local" / "Studia", tmp_path / "local" / "IEUStudia"
    (old / "logs").mkdir(parents=True)
    (old / "library.db").write_text("db")
    (old / "logs" / "studia.log").write_text("old")
    (new / "logs").mkdir(parents=True)                  # e.g. created by an earlier start
    (new / "logs" / "studia.log").write_text("new")
    (new / "models").mkdir()                            # empty: the old models take its place
    (old / "models" / "small").mkdir(parents=True)
    (old / "models" / "small" / "model.bin").write_bytes(b"m")
    assert paths.migrate_legacy_data() == "moved"
    assert (new / "library.db").exists() and (new / "models" / "small" / "model.bin").exists()
    assert (new / "logs" / "studia.log").read_text() == "new" and (old / "logs" / "studia.log").exists()


def test_a_new_data_folder_with_only_empty_folders_is_replaced(tmp_path, monkeypatch):
    _real_mode(tmp_path, monkeypatch)
    old, new = tmp_path / "local" / "Studia", tmp_path / "local" / "IEUStudia"
    (old / "models").mkdir(parents=True)
    (old / "library.db").write_text("db")
    (new / "models").mkdir(parents=True)
    assert paths.migrate_legacy_data() == "moved"
    assert (new / "library.db").exists() and not old.exists()


def test_a_merge_that_fails_keeps_the_old_folder_and_is_tried_again(tmp_path, monkeypatch):
    _real_mode(tmp_path, monkeypatch)
    old, new = tmp_path / "local" / "Studia", tmp_path / "local" / "IEUStudia"
    (old / "transcripts").mkdir(parents=True)
    (old / "transcripts" / "t.json").write_text("{}")    # (an empty folder moved first is pruned and made again)
    for name in ("library.db", "settings.json", "secrets.bin"):
        (old / name).write_text(name)
    (new / "logs").mkdir(parents=True)
    (new / "logs" / "studia.log").write_text("x")
    real = paths.shutil.move

    def busy(src, dst):
        if src.endswith("settings.json"):
            raise PermissionError(32, "in use")
        return real(src, dst)

    monkeypatch.setattr(paths.shutil, "move", busy)
    assert paths.migrate_legacy_data().startswith("failed")
    assert (old / "library.db").exists() and paths.app_dir() == old      # the library moves last: still the old one
    monkeypatch.setattr(paths.shutil, "move", real)
    assert paths.migrate_legacy_data() == "moved"
    assert all((new / n).exists() for n in ("library.db", "settings.json", "secrets.bin", "transcripts", "logs"))
    assert not old.exists() and paths.app_dir() == new


def test_data_folder_is_not_touched_with_studia_home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "local"))     # Linux
    (tmp_path / "local" / "Studia").mkdir(parents=True)
    (tmp_path / "local" / "Studia" / "library.db").write_text("db")
    assert paths.migrate_legacy_data() == "skipped"
    assert (tmp_path / "local" / "Studia" / "library.db").exists()


def test_renamed_output_path(tmp_path, monkeypatch):
    _real_mode(tmp_path, monkeypatch)
    docs = tmp_path / "docs"
    assert paths.renamed_output_path(docs / "Studia") == docs / "IEUStudia"
    assert paths.renamed_output_path(docs / "LectureScribe") == docs / "IEUStudia"
    assert paths.renamed_output_path(docs / "Studia - Ayşe Yılmaz") == docs / "IEUStudia - Ayşe Yılmaz"
    assert paths.renamed_output_path(docs / "Studia (p-1a2b3c4d)") == docs / "IEUStudia - p-1a2b3c4d"
    for kept in (docs / "IEUStudia", docs / "IEUStudia - Bob", docs / "Studia backup", docs / "Studia (old)",
                 tmp_path / "elsewhere" / "Studia", docs / "Studia" / "MATH 240"):
        assert paths.renamed_output_path(kept) is None, kept


def test_profile_output_folders_have_one_name(tmp_path, monkeypatch):
    _real_mode(tmp_path, monkeypatch)
    acc = accounts.create("bob", "Bob B", "2024")
    assert accounts.output_dir_for(acc, "Bob B") == tmp_path / "docs" / "IEUStudia - Bob B"
    assert accounts.output_dir_for(acc, "") == tmp_path / "docs" / "IEUStudia - bob"
    assert accounts.output_dir_for(acc, ' ?:* ') == tmp_path / "docs" / f"IEUStudia - {acc.id}"
    assert accounts.output_dir_for(acc, "x" * 59 + " y") == tmp_path / "docs" / ("IEUStudia - " + "x" * 59)
    accounts.activate(acc.id)
    assert paths.default_output_path() == tmp_path / "docs" / f"IEUStudia - {acc.id}"
    accounts.activate("main")
    assert paths.default_output_path() == tmp_path / "docs" / "IEUStudia"


def _seed(out, elsewhere):
    """Files in the output folder ``out`` and the paths the app keeps for them: a downloaded material, a study plan,
    a saved weekly digest and a slide. Plus a material downloaded somewhere else, whose path must not change."""
    files = {"pdf": out / "MATH 240" / "Materials" / "Week 1" / "notes.pdf",
             "plan": out / "MATH 240" / "Study plans" / "plan.md",
             "digest": out / "Weekly digests" / "2026-10-19 weekly digest.md",
             "slide": out / "MATH 240" / "slides" / "day" / "s1.jpg", "other": elsewhere / "other.pdf"}
    for f in files.values():
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"x")
    lib = Library(paths.db_path())
    lib.upsert_courses([Course(id="c", title="2026-1 MATH 240 P 1", code="MATH 240", name="P")])
    lib.merge_units("c", [Unit("u1", 0, "Week 1", resources=[Resource("m1", "Notes"), Resource("m2", "Other")])],
                    first_sync=True)
    lib.set_material_file("m1", str(files["pdf"]), 1, "u")
    lib.set_material_file("m2", str(files["other"]), 1, "u")
    lib.create_plan("p1", "c", {})
    lib.update_plan("p1", result={"file": str(files["plan"]), "items": [1]})
    lib.set_meta("digest:2026-10-19", json.dumps({"text": "The week", "file": str(files["digest"])}))
    lib.close()
    save_index(paths.transcripts_dir() / "r1.slides.json", [Slide(1, 0, 5, "slides/day/s1.jpg", str(files["slide"]))])
    save_settings(Settings(output_dir=str(out)))
    return files


def _stored(s):
    return {"pdf": s.lib.get_material("m1")["local_path"], "other": s.lib.get_material("m2")["local_path"],
            "plan": s.lib.plan("p1")["result"]["file"], "digest": json.loads(s.lib.get_meta("digest:2026-10-19"))["file"],
            "slide": load_index(paths.transcripts_dir() / "r1.slides.json")[0].path}


def _close(s):
    s.shutdown()
    s.lib.close()


@pytest.mark.parametrize("app_open", [True, False])
def test_the_command_line_leaves_the_open_apps_queue_and_plans_alone(monkeypatch, app_open):
    import datetime as dt

    from studia.domain import Recording

    lib = Library(paths.db_path())
    lib.upsert_courses([Course(id="c", title="t", code="MATH 240", name="P")])
    lib.merge_recordings("c", [Recording(delivery_id="r1", course_id="c", folder_id="f", folder_path="", name="r1",
                                         start=dt.datetime(2026, 10, 13, 11), duration_s=2400, viewer_url="u")],
                         first_sync=True)
    lib.set_status("r1", "transcribing")
    lib.create_plan("p1", "c", {"kind": "prep"})
    lib.update_plan("p1", status="running")
    lib.close()
    monkeypatch.setattr(services, "_app_open", lambda: app_open)
    s = Services(migrate_output=False)                    # `studia list` while the app is (or isn't) open
    try:
        assert s.lib.get_recording("r1").status == ("transcribing" if app_open else "available")
        assert s.lib.plan("p1")["status"] == ("running" if app_open else "paused")
    finally:
        _close(s)


@pytest.mark.parametrize("old_name", ["Studia", "LectureScribe"])
def test_output_folder_is_renamed_and_the_stored_paths_follow(tmp_path, monkeypatch, old_name):
    _real_mode(tmp_path, monkeypatch)
    old, new = tmp_path / "docs" / old_name, tmp_path / "docs" / "IEUStudia"
    files = _seed(old, tmp_path / "elsewhere")
    new.mkdir()                                           # empty (made by an earlier start): no reason to keep the old name
    s = Services()
    try:
        assert s.settings.output_dir == str(new) and load_settings().output_dir == str(new)
        assert not old.exists() and (new / "MATH 240" / "Materials" / "Week 1" / "notes.pdf").is_file()
        stored = _stored(s)
        for key in ("pdf", "plan", "digest", "slide"):
            assert stored[key] == str(new / files[key].relative_to(old)), key
        assert stored["other"] == str(files["other"]) and s.lib.plan("p1")["result"]["items"] == [1]
    finally:
        _close(s)


def test_added_profile_output_folder_is_renamed(tmp_path, monkeypatch):
    _real_mode(tmp_path, monkeypatch)
    accounts.activate(accounts.create("bob", "Bob B", "2024").id)
    old, new = tmp_path / "docs" / "Studia - Bob B", tmp_path / "docs" / "IEUStudia - Bob B"
    files = _seed(old, tmp_path / "elsewhere")
    s = Services()
    try:
        assert s.settings.output_dir == str(new) and not old.exists()
        assert _stored(s)["pdf"] == str(new / files["pdf"].relative_to(old))
    finally:
        _close(s)


def test_output_folder_keeps_its_name_when_the_new_one_is_taken(tmp_path, monkeypatch):
    _real_mode(tmp_path, monkeypatch)
    old, new = tmp_path / "docs" / "Studia", tmp_path / "docs" / "IEUStudia"
    files = _seed(old, tmp_path / "elsewhere")
    new.mkdir()
    (new / "mine.md").write_text("x")
    s = Services()
    try:
        assert s.settings.output_dir == str(old) and (new / "mine.md").is_file()
        assert _stored(s)["pdf"] == str(files["pdf"]) and files["pdf"].is_file()
    finally:
        _close(s)


def test_a_rename_that_stopped_halfway_is_finished(tmp_path, monkeypatch):
    _real_mode(tmp_path, monkeypatch)
    old, new = tmp_path / "docs" / "Studia", tmp_path / "docs" / "IEUStudia"
    files = _seed(old, tmp_path / "elsewhere")
    os.replace(old, new)                                  # renamed, then the app stopped before it saved the new paths
    s = Services()
    try:
        assert s.settings.output_dir == str(new) and not old.exists()
        assert _stored(s)["digest"] == str(new / files["digest"].relative_to(old))
    finally:
        _close(s)


def test_a_noted_rename_is_finished_even_if_the_old_folder_came_back(tmp_path, monkeypatch):
    _real_mode(tmp_path, monkeypatch)
    old, new = tmp_path / "docs" / "Studia", tmp_path / "docs" / "IEUStudia"
    files = _seed(old, tmp_path / "elsewhere")
    os.replace(old, new)
    lib = Library(paths.db_path())
    lib.set_meta(services.RENAME_KEY, json.dumps({"from": str(old), "to": str(new)}))
    lib.close()
    (old / "MATH 240").mkdir(parents=True)                # e.g. an editor saved an open transcript again
    (old / "MATH 240" / "day.md").write_text("x")
    s = Services()
    try:
        assert s.settings.output_dir == str(new) and _stored(s)["pdf"] == str(new / files["pdf"].relative_to(old))
        assert s.lib.get_meta(services.RENAME_KEY) == "" and (old / "MATH 240" / "day.md").exists()
    finally:
        _close(s)


def test_a_folder_put_back_without_the_apps_files_does_not_hold_the_rename(tmp_path, monkeypatch):
    _real_mode(tmp_path, monkeypatch)
    old, new = tmp_path / "docs" / "Studia", tmp_path / "docs" / "IEUStudia"
    files = _seed(old, tmp_path / "elsewhere")
    os.replace(old, new)                                  # another profile sharing the folder renamed it
    (old / ".obsidian").mkdir(parents=True)
    s = Services()
    try:
        assert s.settings.output_dir == str(new) and _stored(s)["slide"] == str(new / files["slide"].relative_to(old))
    finally:
        _close(s)


def test_an_output_folder_that_is_not_there_is_left_alone(tmp_path, monkeypatch):
    _real_mode(tmp_path, monkeypatch)
    (tmp_path / "docs").mkdir()
    save_settings(Settings(output_dir=str(tmp_path / "docs" / "Studia")))     # e.g. on a drive that is not connected
    s = Services()
    try:
        assert s.settings.output_dir == str(tmp_path / "docs" / "Studia")
    finally:
        _close(s)


def test_the_command_line_never_renames_the_output_folder(tmp_path, monkeypatch):
    _real_mode(tmp_path, monkeypatch)
    old = tmp_path / "docs" / "Studia"
    _seed(old, tmp_path / "elsewhere")
    s = Services(migrate_output=False)
    try:
        assert s.settings.output_dir == str(old) and old.is_dir()
    finally:
        _close(s)


def test_output_folder_in_use_is_renamed_on_a_later_start(tmp_path, monkeypatch):
    _real_mode(tmp_path, monkeypatch)
    old, new = tmp_path / "docs" / "Studia", tmp_path / "docs" / "IEUStudia"
    files = _seed(old, tmp_path / "elsewhere")
    real = os.replace

    def busy(src, dst):
        if src == old:
            raise PermissionError(32, "The process cannot access the file because it is being used by another process")
        return real(src, dst)

    monkeypatch.setattr(os, "replace", busy)
    s = Services()
    try:
        assert s.settings.output_dir == str(old) and files["pdf"].is_file() and _stored(s)["pdf"] == str(files["pdf"])
    finally:
        _close(s)
    monkeypatch.setattr(os, "replace", real)
    s = Services()
    try:
        assert s.settings.output_dir == str(new) and _stored(s)["pdf"] == str(new / files["pdf"].relative_to(old))
    finally:
        _close(s)
