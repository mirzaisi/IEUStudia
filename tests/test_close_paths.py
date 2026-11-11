"""Update now and switching profiles close the window too: while the AI writes something or an Export for AI chat is
being made, they say so first (switching asks, as for a transcription). Closing stops an export in time for it to
take its unfinished zip away, and those a closing or a power cut left are deleted a day later."""

import os
import threading
import time

import pytest

from studia import accounts, aiwait, updates
from studia.api import Api
from studia.services import Services, _sweep_export_leftovers


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(Api, "_update_loop", lambda self: None)       # no real look on GitHub
    a = Api(Services())
    a._push = lambda e: None
    yield a
    a._s.shutdown()
    a._s.lib.close()


def _export_running(api):
    run = {"id": "x", "course_id": "", "running": True, "cancel": threading.Event(), "last": {}}
    api._cexp = run
    return run


@pytest.mark.parametrize("what", ["ai", "export"])
def test_update_now_waits_for_the_ai_or_an_export(api, monkeypatch, what):
    monkeypatch.setattr(updates, "status", lambda: {"available": True, "can_install": True})
    if what == "ai":
        monkeypatch.setattr(aiwait, "running", lambda: ["pack"])
    else:
        _export_running(api)
    r = api.update_install()
    assert not r["ok"] and ("The AI is still writing" if what == "ai" else "Export for AI chat") in r["error"]
    assert not api._update_running


@pytest.mark.parametrize("what", ["ai", "export"])
def test_switching_profiles_asks_while_the_ai_writes_or_an_export_is_made(api, monkeypatch, what):
    other = accounts.create("bob", "Bob B", "2024")
    if what == "ai":
        monkeypatch.setattr(aiwait, "running", lambda: ["chat"])
    else:
        _export_running(api)
    r = api.profile_switch(other.id)
    assert not r["ok"] and r["busy"] and "Switching stops it here" in r["ask"] and not api._restart
    r = api.profile_switch(other.id, True)                    # "Switch anyway"
    assert r["ok"] and api._restart
    accounts.set_active("main")


def test_closing_stops_an_export_and_waits_for_it_to_tidy_up(api):
    run = _export_running(api)
    tidied = []

    def export():                                             # course_export.build: cancelled, its .zip.tmp removed
        run["cancel"].wait(5)
        time.sleep(0.1)
        tidied.append(1)
        run["running"] = False

    threading.Thread(target=export, daemon=True).start()
    api._end_export()
    assert tidied and not run["running"]


def test_unfinished_export_zips_are_deleted_a_day_later(tmp_path):
    old = [tmp_path / "Exports" / "All courses - Weeks 1-3.zip.tmp", tmp_path / "MATH 240" / "Exports" / "w.zip.tmp"]
    kept = [tmp_path / "MATH 240" / "Exports" / "new.zip.tmp", tmp_path / "MATH 240" / "Exports" / "done.zip"]
    for f in old + kept:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"x")
    long_ago = time.time() - 2 * 86400
    for f in old + kept[1:]:
        os.utime(f, (long_ago, long_ago))
    assert _sweep_export_leftovers(tmp_path) == 2
    assert not any(f.exists() for f in old) and all(f.exists() for f in kept)


def test_the_exports_folder_name_is_the_exports():
    from studia.course_export import EXPORTS

    assert EXPORTS == "Exports"                               # (services sweeps it without importing course_export)
