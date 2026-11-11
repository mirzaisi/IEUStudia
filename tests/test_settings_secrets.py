import json

from studia import secrets_store
from studia.config import apply_patch, load_settings, save_settings
from studia.paths import secrets_path, settings_path


def test_settings_roundtrip_and_tolerance():
    s = load_settings()
    assert s.model == "large-v3-turbo" and s.output_dir
    s = apply_patch(s, {"theme": "dark", "course_prefs": {"19226": {"show_mode": "all", "my_section": "1"}}})
    save_settings(s)
    raw = json.loads(settings_path().read_text(encoding="utf-8"))
    raw["unknown_future_key"] = 1
    raw["parallel_downloads"] = 99
    settings_path().write_text(json.dumps(raw), encoding="utf-8")
    s2 = load_settings()
    assert s2.theme == "dark" and s2.prefs("19226").my_section == "1" and s2.parallel_downloads == 4


def test_unreadable_settings_are_kept_aside_not_written_over():
    from studia import config

    s = apply_patch(load_settings(), {"course_prefs": {"19226": {"my_section": "3"}}})
    save_settings(s)
    good = settings_path().read_bytes()
    settings_path().write_bytes(b"\0" * len(good))               # a power cut kept the rename but not the data
    s = load_settings()
    assert config.unreadable() and s.prefs("19226").my_section is None     # the defaults, and the app says so
    save_settings(apply_patch(s, {"theme": "dark"}))
    (aside,) = settings_path().parent.glob("settings.json.unreadable-*")
    assert aside.read_bytes() == b"\0" * len(good)                # kept as it was
    assert load_settings().theme == "dark" and not config.unreadable()


def test_the_app_says_when_it_started_with_default_settings(isolated_home):
    from studia.api import Api
    from studia.services import Services

    settings_path().parent.mkdir(parents=True, exist_ok=True)
    settings_path().write_text("{\"theme\": \"da", encoding="utf-8")
    svc = Services()
    try:
        api = Api(svc)
        (note,) = api.get_bootstrap()["notes"]
        assert "couldn't read your settings" in note and str(settings_path().parent) in note
        assert api.get_bootstrap()["notes"] == []                 # told once, not again at a reload (F5)
    finally:
        svc.shutdown()


def test_dpapi_secrets_roundtrip():
    secrets_store.set_credentials("student", "pässwörd!")
    secrets_store.save_cookies([{"name": "PHPSESSID", "value": "v", "domain": "learn.ieu.edu.tr", "path": "/"}])
    assert b"student" not in secrets_path().read_bytes()          # encrypted at rest
    assert secrets_store.get_credentials() == ("student", "pässwörd!")
    assert secrets_store.load_cookies()[0]["name"] == "PHPSESSID"
    secrets_store.clear_credentials()
    assert secrets_store.get_credentials() is None
    secrets_path().write_bytes(b"garbage")
    assert secrets_store.load_cookies() == []                     # corrupt file -> empty, no crash


def test_a_secrets_file_that_cant_be_read_is_never_written_over(monkeypatch):
    import pytest

    secrets_store.set_credentials("student", "pw")
    stored = secrets_path().read_bytes()
    real = secrets_store.unprotect

    def blocked(data):                                             # a read blocked for a moment (antivirus, say)
        raise OSError(5, "Access is denied")
    monkeypatch.setattr(secrets_store, "unprotect", blocked)
    with pytest.raises(OSError):                                   # the sync logs it and tries again later
        secrets_store.save_cookies([{"name": "PHPSESSID", "value": "v", "domain": "learn.ieu.edu.tr", "path": "/"}])
    secrets_store.mark_rejected("pw")
    assert secrets_path().read_bytes() == stored                   # the username and password are still there
    monkeypatch.setattr(secrets_store, "unprotect", real)
    assert secrets_store.get_credentials() == ("student", "pw")
    monkeypatch.setattr(secrets_store, "unprotect", blocked)
    secrets_store.set_credentials("student", "new")                # signing in again: the old file is kept aside
    assert secrets_path().with_name("secrets.bin.unreadable").read_bytes() == stored
    monkeypatch.setattr(secrets_store, "unprotect", real)
    assert secrets_store.get_credentials() == ("student", "new")


def test_can_resume_needs_a_password_or_session(tmp_path):
    from studia.library import Library
    from studia.sync import SyncService

    sync = SyncService(Library(tmp_path / "lib.db"), load_settings)
    assert not sync.can_resume()                                   # e.g. a library copied from elsewhere
    secrets_store.save_cookies([{"name": "PHPSESSID", "value": "v", "domain": "learn.ieu.edu.tr", "path": "/"}])
    assert sync.can_resume()
    secrets_store.clear_all()
    secrets_store.set_credentials("student", "pw")
    assert sync.can_resume()


def test_the_app_and_its_helper_take_turns_with_the_secrets():
    """Two processes (the window and the tray helper) must not overwrite each other's secrets.bin."""
    import os
    import subprocess
    import sys
    import time

    hold = "import time, studia.secrets_store as s\nwith s._turn():\n    print('held', flush=True)\n    time.sleep(1.5)\n"
    other = subprocess.Popen([sys.executable, "-c", hold], stdout=subprocess.PIPE, env=dict(os.environ))
    try:
        assert other.stdout.readline().strip() == b"held"
        started = time.monotonic()
        secrets_store.set_credentials("student", "new-password")       # waits for the other process
        assert time.monotonic() - started > 0.8
    finally:
        other.wait(10)
    assert secrets_store.get_credentials() == ("student", "new-password")
    assert not list(secrets_path().parent.glob("secrets.bin.*tmp"))


def test_a_refusal_belongs_to_the_password_that_was_refused():
    secrets_store.set_credentials("student", "old-password")
    secrets_store.mark_rejected("new-password")                  # the helper tried an older one meanwhile: no
    assert secrets_store.get_credentials() == ("student", "old-password")
    secrets_store.mark_rejected("old-password")
    assert secrets_store.rejected() and secrets_store.get_credentials() is None
    d = secrets_store._load()                                     # 2.1.1 saves a new password but keeps the flag
    d["password"] = "new-password"
    secrets_store._save(d)
    assert not secrets_store.rejected() and secrets_store.get_credentials() == ("student", "new-password")


def test_secrets_are_on_the_disk_before_they_take_the_old_files_place(monkeypatch):
    """Rewritten at every sync (the cookies): without the flush, a power cut soon after could leave secrets.bin with
    its size but only zeros, and the student typing the password again."""
    import os

    seen = []
    real_fsync, real_replace = os.fsync, os.replace
    monkeypatch.setattr(secrets_store.os, "fsync", lambda fd: seen.append("fsync") or real_fsync(fd))
    monkeypatch.setattr(secrets_store.os, "replace", lambda a, b: seen.append("replace") or real_replace(a, b))
    secrets_store.save_cookies([{"name": "PHPSESSID", "value": "v", "domain": "learn.ieu.edu.tr", "path": "/"}])
    assert seen == ["fsync", "replace"] and secrets_store.load_cookies()[0]["value"] == "v"
