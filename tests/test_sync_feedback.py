"""A sync you ask for while the app's own quiet sync runs is yours: its result comes aloud (errors, the sign-in
screen), and if the quiet one had already told its result, one more sync runs for you right after it."""

import threading
import types

from studia.api import Api


def _ns(sync, sync_content):
    lock = threading.Lock()
    pushed = []
    ns = types.SimpleNamespace(_push=pushed.append, _sync_lock=lock, _sync_ask=threading.Lock(), _sync_asked=False,
                               _remind=lambda: None, _courses_dirty=threading.Event(), _course_views=lambda: [],
                               _own_new=lambda views, ids: 0,
                               _s=types.SimpleNamespace(sync=types.SimpleNamespace(sync=sync, sync_content=sync_content),
                                                        lib=types.SimpleNamespace(get_meta=lambda k: "")))
    ns._run_sync = lambda quiet=False: Api._run_sync(ns, quiet)
    return ns, pushed


def _content():
    return {"new_materials": [], "new_announcements": [], "errors": []}


def test_pressing_sync_while_the_quiet_sync_lists_recordings_makes_its_result_yours():
    runs = []

    def sync(progress, on_courses):
        runs.append(1)
        assert Api.sync(ns) == {"ok": True, "running": True}       # F5 while the half-hourly sync runs
        progress("EEE 215")
        return types.SimpleNamespace(new_ids=[], profile=None, errors=["Panopto isn't answering right now."])

    ns, pushed = _ns(sync, _content)
    assert ns._sync_lock.acquire(blocking=False)
    assert Api._run_sync(ns, quiet=True) is True
    assert runs == [1] and not ns._sync_lock.locked() and not ns._sync_asked
    assert pushed[0]["phase"] == "start" and pushed[0]["quiet"]            # (before you asked)
    done = next(e for e in pushed if e["type"] == "sync" and e["phase"] == "done")
    assert "quiet" not in done and done["errors"] == ["Panopto isn't answering right now."]
    assert not any(e.get("quiet") for e in pushed[1:])


def test_a_need_login_after_you_asked_shows_the_sign_in_screen():
    def sync(progress, on_courses):
        Api.sync(ns)
        from studia.ieulearn import NeedLogin
        raise NeedLogin("Sign in again")

    ns, pushed = _ns(sync, _content)
    assert ns._sync_lock.acquire(blocking=False)
    assert Api._run_sync(ns, quiet=True) is False
    assert pushed[-1]["phase"] == "error" and pushed[-1]["need_login"] and "quiet" not in pushed[-1]
    assert not ns._sync_lock.locked()


def test_pressing_sync_after_the_quiet_sync_told_its_result_runs_one_more_for_you():
    runs, contents, ended = [], [], threading.Event()

    def sync(progress, on_courses):
        runs.append(1)
        return types.SimpleNamespace(new_ids=[], profile=None, errors=[])

    def sync_content():
        contents.append(1)
        if len(contents) == 1:
            assert Api.sync(ns)["running"]                          # the files and announcements were being checked
        else:
            ended.set()
        return _content()

    ns, pushed = _ns(sync, sync_content)
    assert ns._sync_lock.acquire(blocking=False)
    assert Api._run_sync(ns, quiet=True) is True
    assert ended.wait(10)
    for _ in range(200):
        if not ns._sync_lock.locked():
            break
        threading.Event().wait(0.02)
    assert runs == [1, 1] and not ns._sync_lock.locked() and not ns._sync_asked
    starts = [e for e in pushed if e["type"] == "sync" and e["phase"] == "start"]
    assert [bool(e.get("quiet")) for e in starts] == [True, False]       # the second one is yours, aloud
    assert ns._last_sync_ok is True


def test_a_sync_nobody_asked_about_runs_once():
    ns, pushed = _ns(lambda p, c: types.SimpleNamespace(new_ids=[], profile=None, errors=[]), _content)
    assert ns._sync_lock.acquire(blocking=False)
    assert Api._run_sync(ns, quiet=True) is True
    assert sum(1 for e in pushed if e["type"] == "sync" and e["phase"] == "start") == 1
    assert all(e["quiet"] for e in pushed if e["type"] in ("sync", "content")) and not ns._sync_lock.locked()
