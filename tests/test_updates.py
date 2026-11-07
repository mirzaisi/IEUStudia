"""New versions: finding the latest release on GitHub (also when its API says no), and updating with a checked
installer."""

import hashlib
import json
import time
import types

import pytest

from studia import updates

INSTALLER = b"MZ pretend installer of 9.9.0"
SUMS = (f"{hashlib.sha256(INSTALLER).hexdigest()}  IEUStudia-Setup-9.9.0.exe\r\n"
        f"{'0' * 64}  IEUStudia-9.9.0-portable.zip\r\n")
API_ANSWER = {"tag_name": "v9.9.0", "html_url": "https://github.com/mirzaisi/IEUStudia/releases/tag/v9.9.0",
              "body": "**New**\n- Updates itself.",
              "assets": [{"name": n, "browser_download_url": f"https://github.com/mirzaisi/IEUStudia/releases/download/v9.9.0/{n}"}
                         for n in ("IEUStudia-Setup-9.9.0.exe", "IEUStudia-9.9.0-portable.zip", "SHA256SUMS.txt")]}


class Site:
    """GitHub as the updater sees it: the API, the "latest" redirect and the checksums file."""

    def __init__(self, api_status=200, sums=SUMS):
        self.api_status, self.sums, self.asked = api_status, sums, []

    def get(self, url, **kw):
        self.asked.append(url)
        if "api.github.com" in url:
            return types.SimpleNamespace(status_code=self.api_status, json=lambda: API_ANSWER, headers={})
        if url.endswith("/releases/latest"):
            return types.SimpleNamespace(status_code=302, headers={"Location": "https://github.com/mirzaisi/IEUStudia/releases/tag/v9.9.0"})
        if url.endswith("SHA256SUMS.txt"):
            return types.SimpleNamespace(status_code=200, text=self.sums, raise_for_status=lambda: None)
        raise AssertionError(url)


def test_versions_compare_as_numbers():
    assert updates.newer("2.10.0", "2.9.9") and updates.newer("v3", "2.99.1") and updates.newer("2.3.1", "2.3")
    assert not updates.newer("2.3.0", "2.3") and not updates.newer("", "2.3.0") and not updates.newer("latest", "1.0")


def test_a_new_release_is_found_and_the_next_look_waits_a_day(monkeypatch):
    monkeypatch.setattr(updates, "installed", lambda: True)
    site = Site()
    st = updates.check(session=site)
    assert st["available"] and st["version"] == "9.9.0" and st["can_install"] and "Updates itself" in st["notes"]
    assert st["page"].endswith("/tag/v9.9.0") and not updates.due()
    assert updates.check(session=site) == st and len(site.asked) == 1            # not again within the day


def test_without_the_api_the_latest_link_still_tells_the_version(monkeypatch):
    monkeypatch.setattr(updates, "installed", lambda: True)
    st = updates.check(force=True, session=Site(api_status=403))                  # GitHub's hourly limit, shared
    assert st["available"] and st["version"] == "9.9.0" and st["can_install"] and st["notes"] == ""


def test_a_page_that_isnt_githubs_answer_falls_back_too(monkeypatch):
    class Portal(Site):                                # a Wi-Fi sign-in page answers in GitHub's place
        def get(self, url, **kw):
            if "api.github.com" in url:
                return types.SimpleNamespace(status_code=200, json=lambda: json.loads("<html>"), headers={})
            return super().get(url, **kw)

    monkeypatch.setattr(updates, "installed", lambda: True)
    assert updates.check(force=True, session=Portal())["version"] == "9.9.0"


def test_a_failed_look_is_tried_again_after_an_hour_not_at_once(monkeypatch):
    class Offline:
        def get(self, url, **kw):
            import requests

            raise requests.ConnectionError("no route")

    st = updates.check(session=Offline())
    assert not st["available"] and st["error"] and not updates.due()
    state = json.loads(updates._state_file().read_text(encoding="utf-8"))
    state["tried_at"] = time.time() - updates.RETRY_S - 1
    updates._state_file().write_text(json.dumps(state), encoding="utf-8")
    assert updates.due()


def test_the_installer_is_used_only_when_its_checksum_matches(monkeypatch):
    from studia import httpdl

    monkeypatch.setattr(updates, "installed", lambda: True)
    monkeypatch.setattr(updates, "_session", lambda: Site())
    updates.check(force=True, session=Site())
    written = []

    def fake_download(url, dest, **kw):
        written.append(url)
        dest.write_bytes(fake_download.payload)
        return dest

    monkeypatch.setattr(httpdl, "download", fake_download)
    fake_download.payload = INSTALLER
    path = updates.download()
    assert path.name == "IEUStudia-Setup-9.9.0.exe" and path.read_bytes() == INSTALLER
    fake_download.payload = b"something else"
    with pytest.raises(RuntimeError, match="checksum doesn't match"):
        updates.download()
    assert not path.exists()
    monkeypatch.setattr(updates, "_session", lambda: Site(sums="nothing useful\n"))
    with pytest.raises(RuntimeError, match="doesn't list a checksum"):
        updates.download()


def test_installers_of_installed_versions_are_tidied_and_a_new_version_is_told_once():
    d = updates._dir()
    for name in ("IEUStudia-Setup-1.0.0.exe", "IEUStudia-Setup-1.0.0.exe.part0", "IEUStudia-Setup-99.0.0.exe"):
        (d / name).write_bytes(b"x")
    updates.cleanup()
    assert sorted(p.name for p in d.iterdir()) == ["IEUStudia-Setup-99.0.0.exe"]
    assert updates.tell_once("9.9.0") and not updates.tell_once("9.9.0") and updates.tell_once("9.9.1")


def test_updating_waits_for_a_running_transcription(monkeypatch):
    from studia.api import Api
    from studia.services import Services

    monkeypatch.setattr(updates, "installed", lambda: True)
    monkeypatch.setattr(Api, "_update_loop", lambda self: None)       # no real look on GitHub meanwhile
    updates.check(force=True, session=Site())
    api = Api(Services())
    monkeypatch.setattr(api._s.jobs, "busy", lambda: True)
    r = api.update_install()
    assert not r["ok"] and "transcription is still running" in r["error"]
    monkeypatch.setattr(updates, "installed", lambda: False)                      # the portable zip
    r = api.update_install()
    assert not r["ok"] and "can't update itself" in r["error"]


def test_the_first_start_after_an_update_is_noticed_once(monkeypatch):
    from studia import __version__

    assert updates.note_version() == ""                       # the first start ever: nothing was updated
    monkeypatch.setattr(updates, "__version__", "99.0.0")
    assert updates.note_version() == __version__ and updates.note_version() == ""


class _Installer:
    """The update's installer as Popen sees it: ``code`` is how it ended within the first seconds (None: still
    installing)."""
    started: list = []

    def __init__(self, code):
        self.code = code

    def __call__(self, cmd, **kw):
        _Installer.started.append(cmd)
        return self

    def wait(self, timeout=None):
        import subprocess

        if self.code is None:
            raise subprocess.TimeoutExpired("IEUStudia-Setup.exe", timeout)
        return self.code


@pytest.mark.parametrize("code,started", [(None, True), (0, True), (1, False), (-1073741819, False)])
def test_an_installer_that_ends_at_once_did_not_run(monkeypatch, tmp_path, code, started):
    """An antivirus that stops the installer as it starts: the window had closed and nothing came back. Now the app
    says so and opens again (app.py), as when Windows doesn't let it start at all."""
    monkeypatch.setattr(updates.subprocess, "Popen", _Installer(code))
    assert updates.run_installer(tmp_path / "IEUStudia-Setup-99.0.0.exe") is started
    cmd = _Installer.started[-1]
    assert cmd[1:4] == ["/SILENT", "/NORESTART", "/RELAUNCH=1"]
    assert cmd[4].startswith("/LOG=") and cmd[4].endswith("update-install-99.0.0.log")    # beside IEUStudia's log


def test_windows_not_starting_the_installer_at_all_is_false(monkeypatch, tmp_path):
    def refused(cmd, **kw):
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(updates.subprocess, "Popen", refused)
    assert updates.run_installer(tmp_path / "IEUStudia-Setup-99.0.0.exe") is False


def test_an_update_that_did_not_finish_is_said_at_the_next_start_once(monkeypatch, tmp_path):
    """The installer started, then was stopped, cancelled or failed: the next start used to show the old version and
    the same 'Update' button, unexplained. Now it says what happened and what to do, once."""
    from studia import __version__

    monkeypatch.setattr(updates, "installed", lambda: True)
    updates.check(force=True, session=Site())
    updates.note_version()                                   # (the start before the update)
    monkeypatch.setattr(updates.subprocess, "Popen", _Installer(None))
    assert updates.run_installer(tmp_path / "IEUStudia-Setup-9.9.0.exe")
    st = updates.status()                                    # the next start: still the old version
    assert st["failed"] == "9.9.0" and st["state"] == "error" and st["available"] and st["can_install"]
    assert "didn't finish" in st["error"] and __version__ in st["error"] and "release page" in st["error"]
    assert updates.note_version() == ""                      # the start has seen it: not again
    assert "failed" not in updates.status() and updates.status()["error"] == ""
    # it worked: the new version runs and says nothing of the kind
    assert updates.run_installer(tmp_path / "IEUStudia-Setup-9.9.0.exe")
    monkeypatch.setattr(updates, "__version__", "9.9.0")
    assert "failed" not in updates.status() and updates.note_version() == __version__
    assert "attempt" not in json.loads(updates._state_file().read_text(encoding="utf-8"))


def test_an_update_without_room_on_the_disk_says_so_before_it_starts(monkeypatch):
    import shutil

    from studia import httpdl

    monkeypatch.setattr(updates, "installed", lambda: True)
    monkeypatch.setattr(updates, "_session", lambda: Site())
    updates.check(force=True, session=Site())
    monkeypatch.setattr(shutil, "disk_usage", lambda p: types.SimpleNamespace(free=300 * 1024 * 1024))
    monkeypatch.setattr(httpdl, "download", lambda *a, **k: pytest.fail("nothing is downloaded"))
    with pytest.raises(RuntimeError, match="needs about 700 MB of free disk space") as err:
        updates.download()
    assert "300 MB are free" in str(err.value)


def test_an_update_download_that_stops_says_why(monkeypatch):
    import requests

    from studia import httpdl, net

    monkeypatch.setattr(updates, "installed", lambda: True)
    monkeypatch.setattr(updates, "_session", lambda: Site())
    monkeypatch.setattr(net, "online", lambda timeout=5.0: True)
    updates.check(force=True, session=Site())

    def blocked(url, dest, **kw):
        raise requests.ConnectionError("getaddrinfo failed")

    monkeypatch.setattr(httpdl, "download", blocked)
    with pytest.raises(net.NetworkError, match="github.com can't be reached from this network"):
        updates.download()
