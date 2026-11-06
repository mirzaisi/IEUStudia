"""Syncing against a fake IEULearn and Panopto: what still works when Panopto is down, a changed password, a page that
comes back without your name, signing out while files download, a course whose recordings can't be listed."""

import datetime as dt
import types

import pytest

from studia import secrets_store, sync as sync_mod
from studia.config import Settings
from studia.domain import Course, Profile, Recording
from studia.ieulearn import AuthError, NeedLogin
from studia.library import Library
from studia.net import NetworkError

COURSES = [Course(id="a", title="2026-1 MATH 240 P 1", code="MATH 240", name="Probability"),
           Course(id="b", title="2026-1 EEE 215 E 2", code="EEE 215", name="Electronics")]


def _rec(cid, n):
    return Recording(delivery_id=f"{cid}{n}", course_id=cid, folder_id="f", folder_path="", name="lecture",
                     start=dt.datetime(2026, 10, 13 + n, 9, 0), duration_s=3000, viewer_url="v")


@pytest.fixture
def site(monkeypatch):
    st = types.SimpleNamespace(profile=Profile(name="Ali Veli", student_number="20250001"), courses=list(COURSES),
                               logged_in=True, password="right", logins=[], panopto=True, folders={"a": "fa", "b": "fb"},
                               listing={"a": [_rec("a", 0)], "b": [_rec("b", 0)]}, launches=[], server_error=False,
                               subfolders={}, asked=[], loaded=[], list_whole=True)

    class FakeLearn:
        def __init__(self, s):
            pass

        def dashboard(self):
            st.loaded.append("dashboard")
            if not st.logged_in:
                raise NeedLogin("expired")
            return Profile(**vars(st.profile)), [Course(**vars(c)) for c in st.courses]

        def course_list(self):                           # the "My courses" list: no term, no progress, no profile
            st.loaded.append("list")
            if not st.logged_in:
                raise NeedLogin("expired")
            if not st.list_whole:
                return None
            return [Course(**{**vars(c), "term": "", "progress": None, "cover_url": ""}) for c in st.courses]

        def is_logged_in(self):
            return st.logged_in

        def login(self, user, password):
            st.logins.append(password)
            if st.server_error:
                raise AuthError("Kimlik doğrulama servisine ulaşılamadı.", server_error=True)
            if password != st.password:
                raise AuthError("Invalid credentials.")
            st.logged_in = True

        def launch_panopto(self, cid):
            st.launches.append(cid)
            return st.folders.get(cid)

    class FakePanopto:
        def __init__(self, s):
            pass

        def is_authenticated(self):
            if isinstance(st.panopto, Exception):
                raise st.panopto
            return st.panopto

        @staticmethod
        def _heads(cid, folder):
            got = st.listing[cid]
            if isinstance(got, Exception):
                raise got
            newest = max(got, key=lambda r: r.start).delivery_id if got else ""
            return {folder: [len(got), newest], **{sub: [0, ""] for sub in st.subfolders.get(cid, [])}}

        def list_recordings(self, cid, folder, heads=None):
            st.asked.append(("list", cid))
            got = self._heads(cid, folder)
            if heads is not None:
                heads.update(got)
            return list(st.listing[cid])

        def probe(self, folder):
            cid = next(c for c, f in st.folders.items() if f == folder)
            st.asked.append(("probe", cid))
            return self._heads(cid, folder)

    monkeypatch.setattr(sync_mod, "IEULearn", FakeLearn)
    monkeypatch.setattr(sync_mod, "Panopto", FakePanopto)
    return st


def _service(tmp_path):
    lib = Library(tmp_path / "lib.db")
    return lib, sync_mod.SyncService(lib, Settings)


def test_a_folder_that_comes_back_empty_is_opened_again_through_ieulearn(site, tmp_path, monkeypatch):
    """Panopto shows a course's folder only for a while after IEULearn opened it, and the sign-in outlives that: the
    listing then says 0 recordings without an error. Such a course is opened again and listed once more."""
    lib, sync = _service(tmp_path)
    sync.sync()
    assert lib.has_recordings("a") and lib.has_recordings("b")
    site.launches.clear()
    site.listing["a"] = []                               # access gone: an empty answer
    orig = sync_mod.IEULearn.launch_panopto

    def launch(self, cid):
        if cid == "a":                                   # opening the course gives the folder back
            site.listing["a"] = [_rec("a", 0), _rec("a", 1)]
        return orig(self, cid)

    monkeypatch.setattr(sync_mod.IEULearn, "launch_panopto", launch)
    res = sync.sync()
    assert site.launches == ["a"] and "a1" in res.new_ids and not res.errors
    assert {r.delivery_id for r in lib.recordings("a")} == {"a0", "a1"}
    site.launches.clear()
    res = sync.sync()                                    # the folder answers again: nothing opened
    assert site.launches == [] and res.new_ids == []


def test_a_course_with_no_recordings_yet_is_opened_again_now_and_then(site, tmp_path, monkeypatch):
    """A new course (no lecture yet) lists empty, and so does one whose access ran out: it is opened again too, at
    most every few hours, so its first recordings show up."""
    lib, sync = _service(tmp_path)
    site.listing["b"] = []
    sync.sync()                                          # the first sync opens every course once
    assert sorted(site.launches) == ["a", "b"] and not lib.has_recordings("b")
    site.launches.clear()
    assert sync.sync().errors == [] and site.launches == []      # opened lately: not again
    hours_ago = (dt.datetime.now() - sync.REOPEN_EMPTY_EVERY - dt.timedelta(minutes=1)).isoformat()
    lib.set_meta("launched:b", hours_ago)
    lib.set_meta("listed:b", hours_ago)                  # (first listed then: a lecture since is new)
    orig = sync_mod.IEULearn.launch_panopto

    def launch(self, cid):
        site.listing["b"] = [Recording(**{**vars(_rec("b", 1)), "start": dt.datetime.now() - dt.timedelta(hours=2)})]
        return orig(self, cid)

    monkeypatch.setattr(sync_mod.IEULearn, "launch_panopto", launch)
    res = _helper_sync(sync)
    assert site.launches == ["b"] and res.new_ids == ["b1"] and res.errors == []


def test_a_course_with_no_recordings_whose_reopen_fails_is_tried_again_only_now_and_then(site, tmp_path, monkeypatch):
    """IEULearn failing to open a course with no recordings is said once, not at every sync; a course page that has
    no Panopto any more takes the course off Panopto (as for a new course), without an error."""
    lib, sync = _service(tmp_path)
    site.listing["b"] = []
    sync.sync()
    hours_ago = (dt.datetime.now() - sync.REOPEN_EMPTY_EVERY - dt.timedelta(minutes=1)).isoformat()
    lib.set_meta("launched:b", hours_ago)

    def launch(self, cid):
        site.launches.append(cid)
        raise RuntimeError("IEULearn answered 503")

    monkeypatch.setattr(sync_mod.IEULearn, "launch_panopto", launch)
    site.launches.clear()
    assert len(sync.sync().errors) == 1 and site.launches == ["b"]     # said
    res = sync.sync()
    assert site.launches == ["b"] and res.errors == []                 # tried at the first sync, not again
    lib.set_meta("launched:b", hours_ago)
    site.launches.clear()
    res = _helper_sync(sync)
    assert site.launches == ["b"] and len(res.errors) == 1             # tried (and said) once more after a while
    lib.set_meta("launched:b", hours_ago)
    monkeypatch.setattr(sync_mod.IEULearn, "launch_panopto", lambda self, cid: None)
    res = sync.sync()
    assert res.errors == [] and lib.get_course("b").folder_id is None and not lib.get_course("b").has_panopto


def test_an_empty_folder_is_never_taken_as_unchanged_by_a_probe_and_a_failed_reopen_is_said(site, tmp_path, monkeypatch):
    """The stored heads of an empty listing would match the probe of a folder whose access ran out: the helper lists
    such a course in full and opens it again; IEULearn not opening it is an error, not "Up to date"."""
    lib, sync = _service(tmp_path)
    sync.sync()
    site.listing["a"] = []                               # access gone, and IEULearn doesn't open the course again

    def launch(self, cid):
        site.launches.append(cid)
        raise RuntimeError("Panopto did not open the folder of course a.")

    monkeypatch.setattr(sync_mod.IEULearn, "launch_panopto", launch)
    site.launches.clear()
    for i in range(2):                                   # (the first probe sees the count change; then no probe)
        site.asked.clear()
        res = _helper_sync(sync)
        assert ("list", "a") in site.asked and (("probe", "a") not in site.asked or i == 0) and site.launches[-1] == "a"
        assert res.errors == ["Couldn't check new recordings for MATH 240: Panopto didn't answer. They're checked "
                              "again next time."]
    assert lib.has_recordings("a")                       # (an empty answer marks nothing gone)
    lib.set_meta("folders:a", '{"fa": [0, "", null]}')  # what 3.2.1 stored from such a listing
    site.asked.clear()
    _helper_sync(sync)
    assert ("list", "a") in site.asked and ("probe", "a") not in site.asked


def test_recordings_merged_before_the_sync_failed_are_still_handed_on(site, tmp_path, monkeypatch):
    """A new lecture is merged, then the sync fails (IEULearn down, a changed password): the library no longer calls
    it new at the next sync, so the sync hands it on (``partial``) before it raises."""
    lib, sync = _service(tmp_path)
    sync.sync()
    site.listing["a"] = [_rec("a", 0), _rec("a", 1)]
    site.logged_in = False                               # ... and no saved password to sign in again with
    told = []
    with pytest.raises(NeedLogin):                       # (merged by the early listing, before the dashboard)
        sync.sync(partial=told.append, full_every=sync.HELPER_PANOPTO_EVERY, dashboard=False)
    assert told[-1] == ["a1"] and lib.get_recording("a1").is_new
    site.logged_in = True
    site.listing["a"] = [_rec("a", 0), _rec("a", 1), _rec("a", 2)]
    orig, asked = sync_mod.Panopto.list_recordings, []

    def flaky(self, cid, folder, heads=None):            # the early listing of a fails; the second one works
        asked.append(cid)
        if asked.count(cid) == 1 and cid == "a":
            raise RuntimeError("500")
        return orig(self, cid, folder, heads)

    monkeypatch.setattr(sync_mod.Panopto, "list_recordings", flaky)
    monkeypatch.setattr(sync, "_reopen_empty", lambda *a: (_ for _ in ()).throw(NeedLogin("expired")))
    told.clear()
    with pytest.raises(NeedLogin):                       # merged after the dashboard, then the sync failed
        sync.sync(partial=told.append)
    assert told[-1] == ["a2"]


def test_a_download_panopto_refused_opens_its_course_again_although_the_sign_in_works(site, tmp_path):
    """DeliveryInfo says "unauthorized" once a course's access ran out, while the sign-in cookie still works: the
    download's retry opens that course through IEULearn again (not only when Panopto's sign-in is gone)."""
    lib, sync = _service(tmp_path)
    sync.sync()
    site.launches.clear()
    site.folders["a"] = "fa2"                            # (and the course moved to another folder meanwhile)
    sync.ensure_panopto("a")
    assert site.launches == ["a"] and lib.get_course("a").folder_id == "fa2"
    sync.ensure_panopto()                                # without a course: only when Panopto's sign-in is gone
    assert site.launches == ["a"]


def test_with_panopto_down_ieulearn_still_syncs(site, tmp_path):
    lib, sync = _service(tmp_path)
    site.panopto = NetworkError("Can't reach panopto.ieu.edu.tr. Check your internet connection.")
    res = sync.sync()
    assert res.errors == ["Panopto isn't answering right now: new recordings are checked again next time."]
    assert lib.get_meta("last_sync") and sync.profile.name == "Ali Veli" and len(lib.courses()) == 2


def test_courses_without_panopto_are_not_an_error(site, tmp_path):
    lib, sync = _service(tmp_path)
    site.panopto, site.folders = False, {}
    for _ in range(2):                                   # the second sync launches nothing, and still no error
        assert sync.sync().errors == []
    assert lib.get_meta("last_sync")


def test_a_course_whose_recordings_cant_be_listed_is_one_plain_line(site, tmp_path):
    lib, sync = _service(tmp_path)
    sync.sync()
    site.listing = {"a": RuntimeError("Expecting value: line 1 column 1 (char 0)"),
                    "b": RuntimeError("500 Server Error")}
    res = sync.sync()
    assert res.errors == ["Couldn't check new recordings for EEE 215, MATH 240: Panopto didn't answer. "
                          "They're checked again next time."]


def test_a_changed_password_is_tried_once_then_the_app_asks(site, tmp_path):
    lib, sync = _service(tmp_path)
    secrets_store.set_credentials("ali", "old-password")
    site.logged_in = False                               # the session expired, and the password was changed
    with pytest.raises(AuthError):
        sync.sync()
    assert site.logins == ["old-password"] and secrets_store.rejected() and not sync.has_credentials()
    with pytest.raises(NeedLogin, match="didn't accept your saved password"):
        sync.ensure_login()
    assert site.logins == ["old-password"]               # never tried again in the background
    secrets_store.set_credentials("ali", "right")        # signing in again clears it
    assert not secrets_store.rejected() and sync.has_credentials()


def test_ieulearns_words_along_with_a_server_error_dont_lock_out_the_saved_password(site, tmp_path):
    lib, sync = _service(tmp_path)
    secrets_store.set_credentials("ali", "right")
    site.logged_in, site.server_error = False, True     # the sign-in service is down and says so on the form
    with pytest.raises(AuthError, match="ulaşılamadı"):
        sync.sync()
    assert not secrets_store.rejected() and sync.has_credentials()
    site.server_error = False                            # back up: the next background sync signs in by itself
    assert sync.sync().errors == [] and site.logins == ["right", "right"]


def test_a_page_without_your_name_keeps_who_you_are(site, tmp_path):
    lib, sync = _service(tmp_path)
    sync.sync()
    site.profile = Profile(name="")
    sync.sync()
    assert (sync.profile.name, sync.profile.student_number) == ("Ali Veli", "20250001")


def test_signing_out_while_files_download_stays_signed_out(site, tmp_path):
    lib, sync = _service(tmp_path)
    worker = sync.worker_session()                       # a material download started before the sign-out
    worker.cookies.set("PHPSESSID", "still-valid", domain="learn.ieu.edu.tr")
    sync.sign_out()
    sync.absorb(worker)                                  # ...and finishes after it
    assert not sync.can_resume() and secrets_store.load_cookies() == []
    fresh = sync.worker_session()
    fresh.cookies.set("PHPSESSID", "new", domain="learn.ieu.edu.tr")
    sync.absorb(fresh)
    assert sync.can_resume()


def test_a_recording_that_moved_to_another_course_moves_and_is_not_new_again(tmp_path):
    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses(list(COURSES))
    lib.merge_recordings("a", [_rec("a", 0), _rec("x", 1)], first_sync=True)
    assert lib.merge_recordings("b", [_rec("x", 1)], first_sync=False) == []      # listed by both: stays in a
    assert lib.get_recording("x1").course_id == "a"
    lib.merge_recordings("a", [_rec("a", 0)], first_sync=False)                     # gone from a's folder
    for _ in range(2):
        assert lib.merge_recordings("b", [_rec("x", 1)], first_sync=False) == []
    assert lib.get_recording("x1").course_id == "b"


# ------------------------------------------------------------------ the helper's Panopto probes
def _helper_sync(sync):
    return sync.sync(full_every=sync.HELPER_PANOPTO_EVERY, dashboard=False)


def test_the_helper_probes_the_folders_and_lists_none_when_nothing_changed(site, tmp_path):
    lib, sync = _service(tmp_path)
    site.listing["a"] = [_rec("a", 0), _rec("a", 1)]
    sync.sync()                                          # the app: every folder in full
    assert sorted(site.asked) == [("list", "a"), ("list", "b")] and lib.get_meta("listed_full:a")
    site.asked.clear()
    site.listing["a"] = [_rec("a", 1), Recording(**{**vars(_rec("a", 0)), "name": "renamed"})]   # (same count)
    res = _helper_sync(sync)
    assert sorted(site.asked) == [("probe", "a"), ("probe", "b")] and res.new_ids == [] and res.errors == []
    assert {r.delivery_id: r.name for r in lib.recordings("a")} == {"a0": "lecture", "a1": "lecture"}   # waits


def test_a_probe_never_marks_a_recording_gone(site, tmp_path):
    lib, sync = _service(tmp_path)
    site.listing["a"] = [_rec("a", 0), _rec("a", 2)]
    sync.sync()
    site.listing["a"] = [_rec("a", 1), _rec("a", 2)]     # a0 deleted, a1 published late: same count, same newest
    assert _helper_sync(sync).new_ids == []
    assert sorted(r.delivery_id for r in lib.recordings("a")) == ["a0", "a2"]
    old = (dt.datetime.now() - sync.HELPER_PANOPTO_EVERY - dt.timedelta(minutes=5)).isoformat(timespec="seconds")
    for c in ("a", "b"):
        lib.set_meta(f"listed_full:{c}", old)            # hours later: the helper lists every folder in full
    site.asked.clear()
    assert _helper_sync(sync).new_ids == ["a1"]
    assert sorted(site.asked) == [("list", "a"), ("list", "b")] and lib.get_meta("listed_full:a") > old
    assert sorted(r.delivery_id for r in lib.recordings("a")) == ["a1", "a2"]


def test_a_new_recording_found_by_a_probe_is_listed_and_new(site, tmp_path):
    lib, sync = _service(tmp_path)
    sync.sync()
    site.asked.clear()
    site.listing["b"] = [_rec("b", 0), _rec("b", 1)]     # the count changed
    res = _helper_sync(sync)
    assert res.new_ids == ["b1"] and lib.get_recording("b1").is_new
    assert sorted(site.asked) == [("list", "b"), ("probe", "a"), ("probe", "b")]
    site.asked.clear()                                   # what it found is what the next probe compares with
    assert _helper_sync(sync).new_ids == [] and sorted(site.asked) == [("probe", "a"), ("probe", "b")]


def test_a_recording_added_and_one_deleted_is_seen_by_the_newest(site, tmp_path):
    lib, sync = _service(tmp_path)
    sync.sync()
    site.listing["a"] = [_rec("a", 1)]                   # a0 deleted, a1 added: the same count
    assert _helper_sync(sync).new_ids == ["a1"]
    assert [r.delivery_id for r in lib.recordings("a")] == ["a1"]    # (a full listing: a0 is gone)


def test_a_course_that_keeps_failing_doesnt_stop_the_others_probes(site, tmp_path):
    lib, sync = _service(tmp_path)
    sync.sync()
    site.listing["a"] = RuntimeError("500")              # a folder Panopto keeps refusing
    old = (dt.datetime.now() - sync.HELPER_PANOPTO_EVERY - dt.timedelta(minutes=5)).isoformat(timespec="seconds")
    for c in ("a", "b"):
        lib.set_meta(f"listed_full:{c}", old)
    for _ in range(2):
        site.asked.clear()
        assert _helper_sync(sync).errors                 # (said, and tried again next time)
    assert ("probe", "b") in site.asked and ("list", "b") not in site.asked    # b went back to probes


def test_a_new_subfolder_or_a_course_never_listed_in_full_is_listed(site, tmp_path):
    lib, sync = _service(tmp_path)
    sync.sync()
    site.asked.clear()
    site.subfolders["a"] = ["fa-labs"]
    lib.set_meta("folders:b", "")                        # (as after the update that brought the probes)
    _helper_sync(sync)
    assert sorted(site.asked) == [("list", "a"), ("list", "b"), ("probe", "a")]
    site.asked.clear()
    _helper_sync(sync)
    assert sorted(site.asked) == [("probe", "a"), ("probe", "b")]


def test_a_probe_says_what_a_full_listing_said_with_one_row_per_folder():
    from studia.panopto import Panopto

    def row(did, day):
        return {"DeliveryID": did.upper(), "SessionName": "Lecture", "FolderID": "F",
                "StartTime": f"/Date({int(dt.datetime(2026, 10, day, 9).timestamp() * 1000)})/", "Duration": 3000}

    folders = {"root": ([row("r2", 20), row("r1", 13)], [{"ID": "SUB", "Name": "Labs"}]), "sub": ([row("s1", 14)], [])}
    asked = []

    class Answer:
        def __init__(self, d):
            self.d = d

        def raise_for_status(self):
            pass

        def json(self):
            return {"d": self.d}

    class Session:
        cookies = types.SimpleNamespace(get=lambda *a, **k: "")

        def post(self, url, json, headers):
            q = json["queryParameters"]
            asked.append((q["folderID"], q["maxResults"]))
            rows, subs = folders[q["folderID"]]
            return Answer({"Results": rows[:q["maxResults"]], "TotalNumber": len(rows), "Subfolders": subs})

    p, heads = Panopto(Session()), {}
    recs = p.list_recordings("a", "root", heads)
    assert [(r.delivery_id, r.folder_path) for r in recs] == [("r2", ""), ("r1", ""), ("s1", "Labs")]
    assert heads == {"root": [2, "r2", None], "sub": [1, "s1", None]} and asked == [("root", 250), ("sub", 250)]
    asked.clear()
    assert p.probe("root") == heads and asked == [("root", 1), ("sub", 1)]


def test_the_apps_syncs_always_list_in_full(site, tmp_path):
    lib, sync = _service(tmp_path)
    _helper_sync(sync)                                   # no full listing yet: the helper's first check is one
    assert sorted(site.asked) == [("list", "a"), ("list", "b")]
    for _ in range(2):
        site.asked.clear()
        sync.sync()
        assert sorted(site.asked) == [("list", "a"), ("list", "b")]


# ------------------------------------------------------------------ the helper's course list instead of the dashboard
def test_the_helper_takes_the_courses_from_the_small_list_and_the_app_from_the_dashboard(site, tmp_path):
    lib, sync = _service(tmp_path)
    site.courses = [Course(**{**vars(c), "term": "2026-2027 Fall", "progress": 40, "cover_url": "c.jpg"}) for c in COURSES]
    sync.sync()
    assert site.loaded == ["dashboard"]
    site.loaded.clear()
    site.profile = Profile(name="")                      # (the list has no name: it is never asked for)
    for _ in range(2):
        res = _helper_sync(sync)
        assert res.errors == [] and len(res.courses) == 2
    assert site.loaded == ["list", "list"]
    assert (sync.profile.name, sync.profile.student_number) == ("Ali Veli", "20250001")
    assert {(c.id, c.term, c.progress, c.cover_url) for c in lib.courses()} == {
        ("a", "2026-2027 Fall", 40, "c.jpg"), ("b", "2026-2027 Fall", 40, "c.jpg")}                  # as stored
    site.loaded.clear()
    sync.sync()                                          # the app's syncs: always the dashboard
    sync.sync(full_every=sync.HELPER_PANOPTO_EVERY)
    assert site.loaded == ["dashboard", "dashboard"]


def test_the_helpers_first_check_or_a_list_that_doesnt_come_whole_loads_the_dashboard(site, tmp_path):
    lib, sync = _service(tmp_path)
    _helper_sync(sync)                                   # nobody's profile stored yet
    assert site.loaded == ["dashboard"] and sync.profile.name == "Ali Veli"
    site.loaded.clear()
    site.list_whole = False
    _helper_sync(sync)
    assert site.loaded == ["list", "dashboard"]


def test_a_dropped_or_a_new_course_shows_at_the_next_helper_check(site, tmp_path):
    lib, sync = _service(tmp_path)
    sync.sync()
    site.courses = [COURSES[0], Course(id="c", title="2026-1 PHYS 100 P 1", code="PHYS 100", name="Physics")]
    site.folders["c"] = "fc"
    site.listing["c"] = [_rec("c", 0)]
    res = _helper_sync(sync)
    assert [c.id for c in lib.courses()] == ["a", "c"] and site.loaded == ["dashboard", "list"]
    assert sorted(site.launches) == ["a", "b", "c"] and lib.get_course("c").folder_id == "fc"   # its folder found
    assert {r.course_id for r in lib.recordings("c")} == {"c"} and res.errors == []


def test_an_expired_session_is_caught_on_a_helper_check_as_on_the_dashboard(site, tmp_path):
    lib, sync = _service(tmp_path)
    sync.sync()
    secrets_store.set_credentials("ali", "right")
    site.loaded.clear()
    site.logged_in = False                               # the session expired: signs in again, as the app's sync does
    assert _helper_sync(sync).errors == [] and site.logins == ["right"]
    assert site.loaded == ["list", "dashboard", "dashboard"]    # (the dashboard's own retry, then after signing in)
    site.logged_in = False
    secrets_store.set_credentials("ali", "changed")      # the password was changed: tried once, then the app asks
    with pytest.raises(AuthError):
        _helper_sync(sync)
    assert site.logins == ["right", "changed"] and secrets_store.rejected()
    with pytest.raises(NeedLogin, match="didn't accept your saved password"):
        _helper_sync(sync)
    secrets_store.clear_credentials()                    # no password stored at all
    with pytest.raises(NeedLogin, match="Please sign in to IEULearn."):
        _helper_sync(sync)
    assert site.logins == ["right", "changed"]


def test_the_helpers_early_panopto_listing_runs_with_the_course_list(site, tmp_path):
    lib, sync = _service(tmp_path)
    sync.sync()
    site.asked.clear()
    site.listing["a"] = [_rec("a", 0), _rec("a", 1)]
    early = []
    res = sync.sync(partial=early.append, full_every=sync.HELPER_PANOPTO_EVERY, dashboard=False)
    assert early == [["a1"]] and res.new_ids == ["a1"] and site.loaded[-1] == "list"
    assert sorted(site.asked) == [("list", "a"), ("probe", "a"), ("probe", "b")]


def test_a_syllabus_that_cant_be_fetched_is_kept_and_looked_for_again_tomorrow(site, tmp_path, monkeypatch):
    from studia import coursesite, syllabus

    lib, sync = _service(tmp_path)
    lib.upsert_courses(list(COURSES))
    lib.set_course_info("a", syllabus={"weeks": [{"no": 8, "subject": "Midterm"}]}, syllabus_at="2026-01-01T00:00:00")

    class Site:
        def __init__(self, s):
            pass

        def unit_plan(self, cid, title):
            return []

        def announcements(self, cid):
            return []

        def overview(self, cid):
            return types.SimpleNamespace(to_json=lambda: {})

    monkeypatch.setattr(coursesite, "CourseSite", Site)
    monkeypatch.setattr(syllabus, "fetch_syllabus", lambda code, session=None: None)   # the department site is down
    sync.sync_content()
    a, b = lib.course_info("a"), lib.course_info("b")
    assert a["syllabus"]["weeks"][0]["subject"] == "Midterm"
    left = sync.SYLLABUS_EVERY - (dt.datetime.now() - dt.datetime.fromisoformat(a["syllabus_at"]))
    assert dt.timedelta(hours=23) < left <= dt.timedelta(days=1)
    assert b["syllabus"] == {} and b["syllabus_at"]                 # none stored: "no syllabus" is kept for a week


def _course_pages(monkeypatch):
    """IEULearn's course pages, counting what is asked of them."""
    from studia import coursesite, syllabus

    asked = []

    class Site:
        def __init__(self, s):
            pass

        def unit_plan(self, cid, title):
            asked.append(("units", cid))
            return []

        def announcements(self, cid):
            asked.append(("announcements", cid))
            return []

        def overview(self, cid):
            return types.SimpleNamespace(to_json=lambda: {})

    monkeypatch.setattr(coursesite, "CourseSite", Site)
    monkeypatch.setattr(syllabus, "fetch_syllabus", lambda code, session=None: None)
    return asked


def test_connecting_to_panopto_doesnt_load_the_dashboard_again(site, tmp_path, monkeypatch):
    lib, sync = _service(tmp_path)
    looked = []

    def launch(self, cid):
        site.launches.append(cid)
        site.panopto = True
        return site.folders.get(cid)

    monkeypatch.setattr(sync_mod.IEULearn, "is_logged_in", lambda self: looked.append(1) or site.logged_in)
    monkeypatch.setattr(sync_mod.IEULearn, "launch_panopto", launch)
    site.panopto = False
    assert sync.sync().errors == [] and sorted(site.launches) == ["a", "b"] and looked == []   # the dashboard answered
    secrets_store.set_credentials("ali", "right")
    site.logged_in, site.panopto = False, False         # both sign-ins expired overnight
    assert sync.sync().errors == [] and len(site.launches) == 3
    assert site.logins == ["right"] and looked == [1]   # once, to sign in again


def test_cookies_that_didnt_change_are_not_written_again(site, tmp_path, monkeypatch):
    lib, sync = _service(tmp_path)
    saved = []
    save = secrets_store.save_cookies
    monkeypatch.setattr(secrets_store, "save_cookies", lambda c, folder=None: saved.append(c) or save(c, folder))
    sync.sync()
    assert saved == []
    sync.http().cookies.set("PHPSESSID", "fresh", domain="learn.ieu.edu.tr")
    sync.sync()
    sync.sync()
    assert [[c["value"] for c in s] for s in saved] == [["fresh"]]
    assert sync_mod.SyncService(lib, Settings).http().cookies.get("PHPSESSID") == "fresh"


def test_the_helper_reads_unit_plans_every_few_hours_and_announcements_every_time(site, tmp_path, monkeypatch):
    asked = _course_pages(monkeypatch)
    lib, sync = _service(tmp_path)
    lib.upsert_courses(list(COURSES))
    every = sync.HELPER_UNITS_EVERY
    sync.sync_content(units_every=every)                 # new courses: everything
    assert sorted(asked) == [("announcements", "a"), ("announcements", "b"), ("units", "a"), ("units", "b")]
    asked.clear()
    lib.upsert_courses(list(COURSES) + [Course(id="c", title="2026-1 PHYS 100 P 1", code="PHYS 100", name="Physics")])
    sync.sync_content(units_every=every)                 # half an hour later: a new course's plan, no others
    assert sorted(asked) == [("announcements", "a"), ("announcements", "b"), ("announcements", "c"), ("units", "c")]
    asked.clear()
    lib.set_meta("units_at", (dt.datetime.now() - every - dt.timedelta(minutes=1)).isoformat(timespec="seconds"))
    sync.sync_content(units_every=every)
    assert {cid for kind, cid in asked if kind == "units"} == {"a", "b", "c"}
    asked.clear()
    sync.sync_content()                                  # the app (and the helper telling about new files): each time
    assert {cid for kind, cid in asked if kind == "units"} == {"a", "b", "c"}


def test_sections_of_one_course_fetch_its_syllabus_once(site, tmp_path, monkeypatch):
    from studia import syllabus

    _course_pages(monkeypatch)
    lib, sync = _service(tmp_path)
    lib.upsert_courses(list(COURSES) + [Course(id="c", title="2026-1 MATH 240 P 2", code="MATH 240", name="Probability")])
    codes = []
    monkeypatch.setattr(syllabus, "fetch_syllabus",
                        lambda code, session=None: codes.append(code) or syllabus.Syllabus(code=code, outcomes=["Bayes"]))
    sync.sync_content()
    assert sorted(codes) == ["EEE 215", "MATH 240"]
    assert lib.course_info("a")["syllabus"]["outcomes"] == lib.course_info("c")["syllabus"]["outcomes"] == ["Bayes"]
