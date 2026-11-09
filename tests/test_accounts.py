"""Profiles: several IEULearn accounts on one PC, each with its own data; adding, switching, removing."""

import ctypes
import json
import os
import subprocess
import sys
import threading
import time

import pytest

from studia import accounts, paths, secrets_store
from studia.config import Settings, load_settings
from studia.domain import Course, Profile
from studia.library import Library


def test_first_profile_is_the_app_folder(isolated_home):
    active, accs = accounts.load()
    assert active == "main" and [a.id for a in accs] == ["main"] and accs[0].path == isolated_home
    accounts.activate()
    assert paths.db_path() == isolated_home / "library.db" and paths.transcripts_dir() == isolated_home / "transcripts"


def test_added_profile_has_its_own_data(isolated_home):
    acc = accounts.create("ayse.yilmaz", "Ayşe Yılmaz", "20240001")
    assert acc.dir == f"profiles/{acc.id}" and acc.path.is_dir()
    accounts.activate(acc.id)
    assert paths.profile_dir() == isolated_home / "profiles" / acc.id
    for p in (paths.db_path(), paths.settings_path(), paths.secrets_path(), paths.transcripts_dir()):
        assert acc.path in p.parents or p.parent == acc.path
    assert paths.default_output_dir() == acc.path / "output"
    accounts.activate("main")
    assert paths.db_path() == isolated_home / "library.db"


def test_identity_switching_and_removal(isolated_home):
    accounts.remember("main", "ali.v", "Ali V", "2023")
    acc = accounts.create("Bob.B", "Bob B", "2024")
    assert accounts.find("bob.b").id == acc.id and accounts.find("ALI.V").id == "main" and accounts.find("nobody") is None
    accounts.set_active(acc.id)
    assert accounts.active().id == acc.id and accounts.active().last_used
    with pytest.raises(ValueError):
        accounts.remove(acc.id)                       # active
    with pytest.raises(ValueError):
        accounts.remove("main")
    with pytest.raises(KeyError):
        accounts.set_active("p-00000000")
    accounts.set_active("main")
    (acc.path / "library.db").write_text("x")
    accounts.remove(acc.id)
    assert not acc.path.exists() and [a.id for a in accounts.load()[1]] == ["main"]


def test_registry_paths_cannot_point_elsewhere(isolated_home):
    paths.app_dir()
    (isolated_home / "profiles.json").write_text(json.dumps({"active": "p-12345678", "accounts": [
        {"id": "p-12345678", "dir": "..\\..\\Windows", "username": "x"}, {"id": "../evil", "dir": "C:/"}]}))
    active, accs = accounts.load()
    assert active == "p-12345678" and [a.id for a in accs] == ["main", "p-12345678"]
    assert accs[1].path == isolated_home / "profiles" / "p-12345678"


def _two_students(isolated_home):
    """The first student and an added one, each with a library that says whose it is; the added one is active."""
    accounts.remember("main", "ali.v", "Ali V", "2023")
    bob = accounts.create("bob.b", "Bob B", "2024")
    for acc, who in ((accounts.get("main"), Profile("Ali V", "2023", "ali.v")), (bob, Profile("Bob B", "2024", "bob.b"))):
        lib = Library(acc.path / "library.db")
        lib.set_meta("profile", json.dumps({"name": who.name, "student_number": who.student_number,
                                            "username": who.username}))
        lib.close()
    later = time.time() + 5
    os.utime(bob.path / "library.db", (later, later))         # Bob's library is the one used last
    accounts.set_active(bob.id)
    return bob


@pytest.mark.parametrize("damage", ["zeros", "gone"])
def test_a_damaged_profile_list_is_made_again_from_the_profiles_folders(isolated_home, damage):
    """A power cut just after profiles.json was written could bring it back as zeros: the first student's library
    opened for everyone, the next save wrote the list with only that one, and the added students' libraries stayed in
    their folders with nothing pointing at them (signing in again made a new, empty profile)."""
    bob = _two_students(isolated_home)
    registry = isolated_home / "profiles.json"
    if damage == "zeros":
        registry.write_bytes(b"\0" * registry.stat().st_size)
    else:
        registry.unlink()
    active, accs = accounts.load()
    assert active == bob.id and [a.id for a in accs] == ["main", bob.id]
    assert [(a.username, a.name, a.student_number) for a in accs] == [("ali.v", "Ali V", "2023"),
                                                                       ("bob.b", "Bob B", "2024")]
    assert accounts.find("BOB.B").id == bob.id                  # signing in again finds Bob's own library
    assert json.loads(registry.read_text(encoding="utf-8"))["active"] == bob.id     # written again at once
    kept = sorted(p.name for p in isolated_home.glob("profiles.json.unreadable-*"))
    assert len(kept) == (1 if damage == "zeros" else 0)        # the damaged one is kept aside, not written over
    accounts.remember("main", "ali.v", "Ali V", "2023")         # (each start remembers the active identity)
    assert [a.id for a in accounts.load()[1]] == ["main", bob.id]


def test_the_profile_list_and_the_app_settings_are_on_the_disk_before_the_swap(isolated_home, monkeypatch):
    from studia import appconfig

    flushed, real = [], os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: flushed.append(fd) or real(fd))
    accounts.create("bob.b", "Bob B", "2024")
    assert flushed
    flushed.clear()
    appconfig.update({"tray": True})
    assert flushed and appconfig.load().tray is True


def test_seed_copies_machine_choices_not_course_choices(isolated_home):
    base = Settings(model="large-v3", theme="dark", first_run_done=True, output_dir="C:/somewhere",
                    course_prefs={"1": __import__("studia.config", fromlist=["CoursePrefs"]).CoursePrefs(alias="x")})
    acc = accounts.create("bob", "Bob B", "2024")
    accounts.seed(acc, Profile(name="Bob B", student_number="2024", username="bob"),
                  [Course(id="c1", title="2026-1 MATH 240 P 1", code="MATH 240", name="P")], base)
    accounts.activate(acc.id)
    s = load_settings()
    assert (s.model, s.theme, s.first_run_done, s.course_prefs) == ("large-v3", "dark", False, {})
    assert s.output_dir == str(acc.path / "output")
    lib = Library(paths.db_path())
    assert [c.code for c in lib.courses()] == ["MATH 240"] and json.loads(lib.get_meta("profile"))["username"] == "bob"
    lib.close()


@pytest.fixture
def api(isolated_home, monkeypatch):
    from studia.api import Api
    from studia.services import Services
    from studia.sync import SyncService

    def check(username, password):
        if password != "right":
            from studia.ieulearn import AuthError
            raise AuthError("Wrong username or password.")
        return (Profile(name=username.title(), student_number="2024", username=username),
                [Course(id="c9", title="2026-1 EEE 215 X 1", code="EEE 215", name="X")], [{"name": "sid", "value": "1"}])

    monkeypatch.setattr(SyncService, "check_account", staticmethod(check))
    accounts.remember("main", "ali.v", "Ali V", "2023")
    a = Api(Services())
    yield a
    a._s.shutdown()


def test_add_switch_and_remove_profiles_through_the_api(api, isolated_home):
    assert api.profile_add("bob", "wrong")["ok"] is False
    assert api.profile_add("ali.v", "right")["error"].startswith("That is the profile")
    r = api.profile_add("bob", "right")
    assert r["ok"] and r["created"] and r["profile"]["name"] == "Bob" and r["profile"]["has_login"]
    bob = accounts.get(r["profile"]["id"])
    assert secrets_store._load(bob.path)["password"] == "right"
    assert api.profile_add("BOB", "right")["created"] is False                  # same person again: same profile
    views = api.profiles()["profiles"]
    assert [p["active"] for p in views] == [True, False]
    sw = api.profile_switch(bob.id)
    assert sw["ok"] and api._restart and accounts.load()[0] == bob.id          # the window closes, the app restarts
    api._restart = False
    accounts.set_active("main")
    assert api.profile_remove(bob.id)["ok"] and accounts.get(bob.id) is None


def test_signing_in_as_someone_else_creates_their_profile(api):
    r = api.sign_in("alice", "right")
    assert r["ok"] and r["switch_to"] and accounts.get(r["switch_to"]).username == "alice"
    assert accounts.load()[0] == "main"                                         # switching is the UI's next step


def test_switch_refuses_while_transcribing(api, monkeypatch):
    acc = accounts.create("bob", "Bob", "1")
    monkeypatch.setattr(api._s.jobs, "busy", lambda: True)
    cancelled = []
    monkeypatch.setattr(api._s.jobs, "cancel", lambda job=None: cancelled.append(job))
    r = api.profile_switch(acc.id)
    assert r["ok"] is False and r["busy"] and accounts.load()[0] == "main"
    assert api.profile_switch(acc.id, True)["ok"] and cancelled == [None]
    api._restart = False


def test_restart_command(monkeypatch, tmp_path):
    from studia import app

    (tmp_path / "python.exe").write_text("")
    (tmp_path / "pythonw.exe").write_text("")
    calls = []
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: calls.append(cmd))
    app._relaunch(["--debug", "--restarted"])
    assert calls == [[str(tmp_path / "pythonw.exe"), "-m", "studia.app", "--debug", "--restarted"]]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows named mutex")
def test_single_instance_waits_for_a_closing_copy():
    from studia import winutil

    name = f"Local\\Studia-Test-{time.time_ns()}"
    assert winutil.acquire_single_instance(name)
    first = winutil._mutex
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    assert not winutil.acquire_single_instance(name, wait_s=0.3)
    kernel32.CloseHandle(winutil._mutex)
    threading.Timer(0.4, lambda: kernel32.CloseHandle(first)).start()
    t0 = time.monotonic()
    assert winutil.acquire_single_instance(name, wait_s=5)
    assert 0.3 < time.monotonic() - t0 < 4
    kernel32.CloseHandle(winutil._mutex)


@pytest.mark.skipif(sys.platform == "win32", reason="Linux flock")
def test_single_instance_lock_on_linux_is_held_until_released(tmp_path, monkeypatch):
    from studia import linuxutil

    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    name = f"Local\\Studia-Test-{time.time_ns()}"
    assert not linuxutil.instance_held(name)
    assert linuxutil.acquire_single_instance(name)
    assert linuxutil.instance_held(name)
    fd = linuxutil._locks.pop(name)                       # as if another process held it
    t0 = time.monotonic()
    assert not linuxutil.acquire_single_instance(name, wait_s=0.3)
    assert time.monotonic() - t0 >= 0.3
    threading.Timer(0.4, lambda: os.close(fd)).start()
    assert linuxutil.acquire_single_instance(name, wait_s=5)
    linuxutil.release_single_instance()
    assert not linuxutil.instance_held(name)


TICKS_PAGE = """
const web = %(web)s, fs = require("fs"), saved = %(store)s;
const stub = () => new Proxy(function () {}, { get: (t, k) => k === Symbol.toPrimitive ? () => "" : k === "length" ? 0 : stub(),
  apply: () => stub(), construct: () => stub() });
globalThis.window = globalThis;
const el = () => ({ innerHTML: "", style: {}, dataset: {}, classList: { toggle() {}, add() {}, remove() {} }, scrollTo() {},
  querySelector: () => null, querySelectorAll: () => [], addEventListener() {}, remove() {}, focus() {} });
globalThis.document = new Proxy({}, { get: (t, k) => k === "compatMode" ? "CSS1Compat" : k === "createElement" ? () => el()
  : k === "querySelector" ? () => stub() : k === "querySelectorAll" ? () => [] : k === "addEventListener" ? () => {} : stub() });
const data = JSON.parse(fs.readFileSync(saved, "utf-8"));        // the browser storage every profile's page shares
globalThis.localStorage = { getItem: (k) => (k in data ? data[k] : null), setItem: (k, v) => { data[k] = String(v); } };
globalThis.navigator = { userAgent: "node", platform: "Win32" };
globalThis.CSS = { escape: (s) => s };
globalThis.addEventListener = () => {}; globalThis.matchMedia = () => ({ matches: false, addEventListener() {} });
globalThis.requestAnimationFrame = (f) => f();
globalThis.setInterval = () => 0; globalThis.setTimeout = () => 0;
for (const f of ["i18n.js", "icons.js", "app.js", "effort.js", "course.js"]) require(web + "/" + f);
const St = globalThis.Studia, S = St.S;
S.profileId = %(profile)s;                  // (boot() knows the profile only after the page's scripts have loaded)
S.day = { dayKey: "x" };
for (const k of %(opened)s) St.after.day({ dayKey: k });
for (const k of %(untick)s) St.A["rd-read"]({ dataset: { day: k }, checked: false });
fs.writeFileSync(saved, JSON.stringify(data));
process.stdout.write(JSON.stringify(data), () => process.exit(0));      // after the output is out (Linux pipes)
"""


@pytest.mark.skipif(not __import__("shutil").which("node"), reason="node is not installed")
def test_the_days_you_read_are_ticked_for_your_profile_only(tmp_path):
    """Every profile's page shares one browser storage, and the read ticks were kept under one key: the second student's
    Get ready showed the first one's lectures as read, and unticking one there unticked it for the first student."""
    from pathlib import Path

    web = Path(paths.__file__).parent / "web"
    store = tmp_path / "storage.json"
    store.write_text(json.dumps({"studia-read": json.dumps(["c|2026-10-07|lecture"])}), encoding="utf-8")   # (3.3.1)

    def launch(profile, opened=(), untick=()):
        script = tmp_path / "page.js"
        script.write_text(TICKS_PAGE % {"web": json.dumps(web.as_posix()), "store": json.dumps(str(store)),
                                        "profile": json.dumps(profile), "opened": json.dumps(list(opened)),
                                        "untick": json.dumps(list(untick))}, encoding="utf-8")
        r = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
        assert r.returncode == 0, r.stderr
        return {k: json.loads(v) for k, v in json.loads(r.stdout).items() if k.startswith("studia-read")}

    a, b, day = "main", "p-1234abcd", "c|2026-10-14|lecture"
    assert launch(a, [day]) == {"studia-read": ["c|2026-10-07|lecture", day]}     # the first profile: its old key
    assert launch(b, ["c|2026-10-21|lecture"]) == {"studia-read": ["c|2026-10-07|lecture", day],
                                                   f"studia-read:{b}": ["c|2026-10-21|lecture"]}
    got = launch(b, [day], untick=[day])        # the second student opens the day, then unticks it
    assert got["studia-read"] == ["c|2026-10-07|lecture", day] and got[f"studia-read:{b}"] == ["c|2026-10-21|lecture"]
