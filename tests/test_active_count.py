"""The anonymous daily count of copies in use (fx-contact, approved by the owner): with the update check, and only
when it is on, an installed copy downloads the tiny active.txt of its own version's GitHub release, at most once a
day, never retried in a loop, with nothing about the student (no ID, no cookie); a 404 (an older release without the
file) is passed over in silence. The release workflow attaches the file to every release, the notices say it, and
scripts/usage.py prints the numbers. No network: GitHub is a stand-in."""

import importlib.util
import json
import re
import threading
import types
from pathlib import Path

import pytest

from studia import __version__, i18n, updates

ROOT = Path(i18n.__file__).parents[2]
WEB = Path(i18n.__file__).parent / "web"
URL = f"https://github.com/mirzaisi/IEUStudia/releases/download/v{__version__}/active.txt"


class GitHub:
    """GitHub as the count sees it: active.txt (or a 404 for a release without it), and what each request carried."""

    def __init__(self, status=200, fail=None):
        self.status, self.fail, self.asked = status, fail, []
        self.cookies = types.SimpleNamespace(set_policy=lambda p: setattr(self, "policy", p), clear=lambda: None)
        self.headers = {}

    def get(self, url, **kw):
        self.asked.append((url, kw))
        if self.fail:
            raise self.fail
        return types.SimpleNamespace(status_code=self.status, content=b"IEUStudia", close=lambda: None)


@pytest.fixture(autouse=True)
def noticed():
    """The student has seen the setup's notice that says what the daily look does (it counts only then)."""
    from studia import appconfig

    appconfig.update({"terms_accepted": "2026-11-01"})


def test_the_count_downloads_this_versions_active_txt_once_a_day(monkeypatch):
    gh = GitHub()
    assert updates.count_in(session=gh) is True
    ((url, kw),) = gh.asked
    assert url == URL and kw.get("timeout") and not kw.get("params") and not kw.get("cookies")
    assert updates.count_in(session=gh) is False and len(gh.asked) == 1    # not again the same day
    monkeypatch.setattr(updates, "_today", lambda: "2099-01-02")           # the next day
    assert updates.count_in(session=gh) is True and len(gh.asked) == 2


def test_the_count_is_once_a_calendar_day_however_often_it_is_looked_for(monkeypatch):
    """The background helper looks about every 30 minutes: with a 20-hour gap a week counted 9 times (twice on
    Monday and Saturday), and usage.py's "a day" read about 20% high."""
    import datetime as dt

    gh, start = GitHub(), dt.datetime(2026, 11, 9, 0, 10)                  # Mon 00:10
    for k in range(7 * 48):                                               # a week, a look every 30 minutes
        monkeypatch.setattr(updates, "_today", lambda at=start + dt.timedelta(minutes=30 * k): at.date().isoformat())
        updates.count_in(session=gh)
    assert len(gh.asked) == 7


def test_nothing_is_counted_before_the_notice_was_seen_or_when_the_note_cant_be_kept(monkeypatch):
    """The first count came 20 s after the first start, while the setup's welcome step (the notice that describes
    it) was still open; and with update.json read-only, every look counted (3 in a row)."""
    from studia import appconfig

    gh = GitHub()
    appconfig.update({"terms_accepted": ""})
    assert updates.count_in(session=gh) is False and gh.asked == []
    appconfig.update({"terms_accepted": "2026-11-09"})
    monkeypatch.setattr(updates, "_save", lambda st: False)
    assert [updates.count_in(session=gh) for _ in range(3)] == [False, False, False] and gh.asked == []


def test_nothing_about_the_student_goes_with_it():
    s = updates._count_session()
    assert set(s.headers) <= {"User-Agent", "Accept-Encoding", "Accept", "Connection"}
    assert s.headers["User-Agent"] == f"IEUStudia/{__version__} (+https://github.com/mirzaisi/IEUStudia)"
    policy = s.cookies._policy                    # no cookie is kept from an answer, and none is ever sent
    assert policy.set_ok(None, None) is False and policy.return_ok(None, None) is False
    assert policy.domain_return_ok("github.com", None) is False


@pytest.mark.parametrize("gh", [GitHub(status=404), GitHub(fail=OSError("no route to host")),
                                GitHub(fail=__import__("requests").ConnectionError("offline"))])
def test_a_missing_file_or_no_connection_is_passed_over_and_not_tried_again_that_day(gh, caplog):
    assert updates.count_in(session=gh) is True                          # (asked once: that is the day's count)
    assert updates.count_in(session=gh) is False and len(gh.asked) == 1   # no retry, not in a loop either
    assert "Traceback" not in caplog.text and updates.status()["error"] == ""   # nothing said to the student


def test_a_clock_set_back_doesnt_stop_the_count_for_good():
    state = {"counted_on": "2099-12-31"}                                  # noted under a date that was wrong
    updates._state_file().write_text(json.dumps(state), encoding="utf-8")
    assert updates.count_in(session=GitHub()) is True


def test_the_app_counts_only_with_the_update_check_on_and_only_an_installed_copy(monkeypatch):
    from studia import appconfig
    from studia.api import Api

    counted, checked = [], []
    monkeypatch.setattr(updates, "installed", lambda: True)
    monkeypatch.setattr(updates, "cleanup", lambda: None)
    monkeypatch.setattr(updates, "count_in", lambda session=None: counted.append(1) or True)
    monkeypatch.setattr(updates, "check", lambda *a, **k: checked.append(1) or {"available": False})
    monkeypatch.setattr(updates, "due", lambda: True)
    waits = []

    def sleep(s):                     # the loop's own pauses: the first two pass, the third ends the test
        waits.append(s)
        if len(waits) > 2:
            raise SystemExit

    for on in (True, False):
        counted.clear(), checked.clear(), waits.clear()
        appconfig.update({"check_updates": on})
        with pytest.raises(SystemExit):
            Api._update_loop(types.SimpleNamespace(_push=lambda e: None), sleep=sleep)
        assert (counted, checked) == (([1, 1], [1, 1]) if on else ([], [])), on
    monkeypatch.setattr(updates, "installed", lambda: False)          # from source or the portable zip: nothing
    appconfig.update({"check_updates": True})
    counted.clear()
    Api._update_loop(types.SimpleNamespace(_push=lambda e: None), sleep=sleep)
    assert counted == []


def test_the_background_helper_counts_with_its_update_check_too(monkeypatch):
    from studia import agent, appconfig

    counted = []
    monkeypatch.setattr(updates, "installed", lambda: True)
    monkeypatch.setattr(updates, "check", lambda *a, **k: {"available": False, "version": ""})
    monkeypatch.setattr(updates, "count_in", lambda session=None: counted.append(1) or True)
    for on in (True, False):
        counted.clear()
        helper = types.SimpleNamespace(cfg=appconfig.update({"check_updates": on}), paused=False,
                                       tray=types.SimpleNamespace(notify=lambda *a: None))
        agent.Helper._check_update(helper)
        assert counted == ([1] if on else []), on


# ------------------------------------------------------------------ the release, the notices, the numbers
def test_every_release_attaches_active_txt_and_its_checksums_stay_as_they_were():
    wf = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert re.search(r"Set-Content build\\installer\\active\.txt ", wf)
    assert "build/installer/active.txt" in wf.split("files: |", 1)[1]
    sums = wf.split("$files = @(", 1)[1].split("\n", 1)[0]
    assert "active" not in sums                                            # SHA256SUMS lists what is run, as before


def test_the_notices_say_what_the_daily_look_does():
    setup = (WEB / "setup.js").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    said = "fetches a tiny file there so the number of people using each version can be counted. Nothing about you is sent."
    assert said in setup.split("const NOTICE", 1)[1].split("St.NOTICE", 1)[0]    # the setup's notice (and About's)
    assert said in setup.split('"Look for new versions by itself"', 1)[1].split("\n", 1)[0]
    privacy = readme.split("## Privacy", 1)[1].split("\n## ", 1)[0]
    assert "active.txt" in privacy and "once a day" in privacy and "nothing about you" in privacy.lower()
    assert "no analytics" not in privacy                                  # true no more: one anonymous count


def _usage():
    spec = importlib.util.spec_from_file_location("usage", ROOT / "scripts" / "usage.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


RELEASES = [
    {"tag_name": "v3.2.6", "published_at": "2026-11-09T10:00:00Z", "draft": False, "assets": [
        {"name": "IEUStudia-Setup-3.2.6.exe", "download_count": 40}, {"name": "IEUStudia-Setup.exe", "download_count": 12},
        {"name": "IEUStudia-3.2.6-portable.zip", "download_count": 5}, {"name": "SHA256SUMS.txt", "download_count": 41},
        {"name": "active.txt", "download_count": 90}]},
    {"tag_name": "v3.2.5", "published_at": "2026-11-07T10:00:00Z", "draft": False, "assets": [
        {"name": "IEUStudia-Setup-3.2.5.exe", "download_count": 100}, {"name": "IEUStudia-3.2.5-portable.zip", "download_count": 9}]},
]


def test_usage_prints_each_releases_installer_portable_and_active_counts():
    import datetime as dt

    usage = _usage()
    rows = usage.counts(RELEASES, now=dt.datetime(2026, 11, 11, 12, tzinfo=dt.timezone.utc))
    assert [r["tag"] for r in rows] == ["v3.2.6", "v3.2.5"]
    new, old = rows
    assert (new["installer"], new["portable"], new["active"], new["days"], new["per_day"]) == (52, 5, 90, 3, 30.0)
    assert (old["installer"], old["portable"], old["active"], old["per_day"]) == (100, 9, None, None)
    text = usage.table(rows)
    assert re.search(r"v3\.2\.6\s+2026-11-09\s+52\s+5\s+90\s+30\.0", text)
    assert re.search(r"v3\.2\.5\s+2026-11-07\s+100\s+9\s+-\s+-", text)
    assert re.search(r"all\s+152\s+14\s+90", text)


def test_usage_reads_every_page_of_the_public_api_without_a_token():
    usage = _usage()
    pages = {usage.API: (RELEASES[:1], '<https://api.github.com/x?page=2>; rel="next", <https://api.github.com/x?page=2>; rel="last"'),
             "https://api.github.com/x?page=2": (RELEASES[1:], "")}
    asked = []

    class Resp:
        def __init__(self, body, link):
            self.body, self.headers = json.dumps(body).encode(), {"Link": link}

        def read(self):
            return self.body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener(req, timeout):
        asked.append(req)
        body, link = pages[req.full_url]
        return Resp(body, link)

    got = usage.via_api(opener)
    assert [r["tag_name"] for r in got] == ["v3.2.6", "v3.2.5"] and len(asked) == 2
    assert all("Authorization" not in r.headers for r in asked)                  # no token
    # gh's pages, one JSON array after the other
    assert [r["tag_name"] for r in usage._pages(json.dumps(RELEASES[:1]) + "\n" + json.dumps(RELEASES[1:]))] == ["v3.2.6", "v3.2.5"]
    ran = types.SimpleNamespace(returncode=0, stdout=json.dumps(RELEASES))
    if usage.shutil.which("gh"):
        assert [r["tag_name"] for r in usage.via_gh(lambda *a, **k: ran)] == ["v3.2.6", "v3.2.5"]
