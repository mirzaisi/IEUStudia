"""Course files whose link doesn't give a file: a sign-in page of another site (OneDrive, Microsoft Forms), a web page,
a file removed from IEULearn, IEULearn down, a connection that breaks off halfway. Each ends with its own plain
message; the queue never goes round and round, and a failed sign-in stops it after one try (no repeated sign-ins)."""

import datetime as dt
import threading
import types

import pytest
import requests

from studia import net
from studia.api import Api
from studia.coursesite import Resource, Unit
from studia.domain import Course
from studia.ieulearn import AuthError, NeedLogin
from studia.library import Library
from studia.materials import MaterialStore, check_answer
from studia.net import NetworkError


class Answer:
    """A streamed answer, as requests gives it."""

    def __init__(self, url, status=200, ctype="application/pdf", chunks=(b"%PDF-1.7 ",), fail=None):
        self.url, self.status_code = url, status
        self.headers = {"content-type": ctype, "content-length": str(sum(len(c) for c in chunks))}
        self._chunks, self._fail = chunks, fail

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Error for url: {self.url}", response=self)

    def iter_content(self, size):
        yield from self._chunks
        if self._fail:
            raise self._fail


LEARN = "https://learn.ieu.edu.tr"


@pytest.mark.parametrize("answer,kind,words", [
    (Answer(f"{LEARN}/login", ctype="text/html"), NeedLogin, "session has expired"),
    (Answer("https://login.microsoftonline.com/common/oauth2/authorize?x=1", ctype="text/html; charset=utf-8"),
     RuntimeError, "link to login.microsoftonline.com, which wants you to sign in there"),
    (Answer("https://www.youtube.com/watch?v=x", ctype="text/html"), RuntimeError, "a link to a web page on www.youtube.com"),
    (Answer("https://example.org/gone.pdf", status=404, ctype="text/html"), RuntimeError, "leads nowhere any more"),
    (Answer("https://files.example.org/x.pdf", status=403, ctype="application/xml"), RuntimeError,
     "files.example.org refused this file"),
    (Answer(f"{LEARN}/student/courses", ctype="text/html"), RuntimeError, "showed a page instead of this file"),
    (Answer(f"{LEARN}/student/courses/1/resources/9/download", status=404), RuntimeError, "no longer on IEULearn"),
    (Answer(f"{LEARN}/student/courses/1/resources/9/download", status=403), RuntimeError, "may no longer have access"),
    (Answer(f"{LEARN}/student/courses/1/resources/9/download", status=503, ctype="text/html"), NetworkError,
     "isn't responding right now"),
])
def test_what_came_back_instead_of_the_file_is_named(answer, kind, words):
    with pytest.raises(kind, match=words):
        check_answer(answer)
    check_answer(Answer(f"{LEARN}/student/courses/1/resources/9/download"))        # a file: nothing to say
    check_answer(Answer("https://cdn.example.org/slides.pdf"))                      # a link to a file elsewhere: fine


def _store(tmp_path):
    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="c", title="2026-1 MATH 240 P 1", code="MATH 240", name="P", term="T")])
    lib.merge_units("c", [Unit("u1", 1, "Hafta 1", dt.date(2026, 9, 21), dt.date(2026, 9, 27), "",
                               [Resource("r1", "Week 1", "Week1.pdf", "pdf", "pdf", "u", "/student/courses/c/r1")])],
                    first_sync=True)
    return lib, MaterialStore(types.SimpleNamespace(lib=lib, output_root=tmp_path / "out"))


def test_a_connection_that_breaks_off_halfway_is_said_plainly_and_leaves_no_part_file(tmp_path):
    lib, store = _store(tmp_path)
    broke = requests.exceptions.ChunkedEncodingError("Connection broken: IncompleteRead(10 bytes read)")
    session = types.SimpleNamespace(get=lambda url, **kw: Answer(f"{LEARN}/x", chunks=(b"%PDF", b"half"), fail=broke))
    with pytest.raises(NetworkError, match="The connection to learn.ieu.edu.tr broke off during the download"):
        store.download("r1", session)
    assert not list((tmp_path / "out").rglob("*.part")) and lib.get_material("r1")["local_path"] in ("", None)
    ok = types.SimpleNamespace(get=lambda url, **kw: Answer(f"{LEARN}/x", chunks=(b"%PDF", b"-whole")))
    assert store.download("r1", ok).read_bytes() == b"%PDF-whole"


def _api(download, ensure_login=lambda: None):
    a = Api.__new__(Api)
    a._mat_lock, a._mat_queue, a._mat_running = threading.Lock(), ["m1", "m2"], True
    a._courses_dirty = threading.Event()
    pushed = []
    a._push = pushed.append
    a._services = types.SimpleNamespace(
        materials=types.SimpleNamespace(download=download, index_text=lambda rid: 3),
        sync=types.SimpleNamespace(worker_session=lambda: object(), absorb=lambda s: None, ensure_login=ensure_login))
    a._ready = threading.Event()
    a._ready.set()
    return a, pushed


def _run(a):
    worker = threading.Thread(target=a._material_worker, daemon=True)
    worker.start()
    worker.join(10)
    assert not worker.is_alive(), "the files went round and round"


def test_a_file_that_keeps_landing_on_a_sign_in_page_fails_once_and_the_next_file_still_comes():
    """A lecturer's OneDrive link (login.microsoftonline.com) used to send the queue round forever: IEULearn's own
    sign-in was fine each time, so the file went back to the front, and every file after it waited."""
    tries, logins = {"m1": 0, "m2": 0}, []

    def download(rid, session, progress):
        tries[rid] += 1
        if rid == "m1":
            raise NeedLogin("Your IEULearn session has expired.")
        return f"C:/x/{rid}.pdf"

    a, pushed = _api(download, lambda: logins.append(1))
    _run(a)
    ends = [(e["id"], e["state"]) for e in pushed if e["state"] in ("done", "error")]
    assert ends == [("m1", "error"), ("m2", "done")] and tries == {"m1": 2, "m2": 1} and logins == [1]
    assert "Open it from the course's page on learn.ieu.edu.tr" in next(e for e in pushed if e["state"] == "error")["error"]
    assert pushed[-1] == {"type": "material", "id": "", "state": "idle"}


@pytest.mark.parametrize("problem,words", [
    (AuthError("Invalid credentials."), "Invalid credentials. Press Sync to sign in again, then download the files again."),
    (NeedLogin("IEULearn didn't accept your saved password (was it changed?). Sign in again."), "Press Sync to sign in again"),
    (NetworkError("IEULearn isn't responding right now (it may be down for maintenance). Try again later."),
     "isn't responding right now"),
])
def test_signing_in_again_failing_stops_the_files_after_one_try(problem, words):
    logins = []

    def ensure_login():
        logins.append(1)
        raise problem

    def download(rid, session, progress):
        raise NeedLogin("Your IEULearn session has expired.")

    a, pushed = _api(download, ensure_login)
    _run(a)
    errors = [(e["id"], e.get("quiet", False)) for e in pushed if e["state"] == "error"]
    assert logins == [1] and errors == [("m1", False), ("m2", True)]          # one sign-in, said once
    assert words in pushed[[e["state"] for e in pushed].index("error")]["error"]


def test_any_other_failure_of_a_file_is_said_in_plain_words(monkeypatch):
    def download(rid, session, progress):
        if rid == "m1":
            raise OSError(28, "No space left on device")
        return "C:/x/m2.pdf"

    a, pushed = _api(download)
    _run(a)
    err = next(e for e in pushed if e["state"] == "error")
    assert err["id"] == "m1" and err["error"].startswith("Your disk is full")
    assert [(e["id"], e["state"]) for e in pushed if e["state"] == "done"] == [("m2", "done")]


def test_a_link_to_a_site_that_cant_be_reached_fails_that_file_only(monkeypatch):
    """A lecturer's old site that no longer exists (or one blocked on campus): it failed every IEULearn file queued
    after it, quietly, with "check your internet connection", on every Download all and Full sync."""
    from studia import api as api_mod

    monkeypatch.setattr(api_mod.time, "sleep", lambda s: None)
    monkeypatch.setattr(net, "online", lambda timeout=5.0: True)     # IEULearn and Panopto answer
    tries = {"m1": 0, "m2": 0}

    def download(rid, session, progress):
        tries[rid] += 1
        if rid == "m1":                                              # as net._TimeoutSession raises it
            err = NetworkError("Can't reach old-site.example.com. Check your internet connection.")
            err.host = "old-site.example.com"
            raise err
        return "C:/x/m2.pdf"

    a, pushed = _api(download)
    _run(a)
    ends = [(e["id"], e["state"], e.get("quiet", False)) for e in pushed if e["state"] in ("done", "error")]
    assert ends == [("m1", "error", False), ("m2", "done", False)] and tries == {"m1": 1, "m2": 1}
    err = next(e for e in pushed if e["state"] == "error")["error"]
    assert "link to old-site.example.com, which can't be reached" in err and "internet connection" not in err


def test_a_redirect_whose_target_cant_be_reached_names_that_site():
    """IEULearn's link sent on to another site: the error names the site that failed, not IEULearn."""
    def send(self, request, **kw):
        if "learn.ieu.edu.tr" in request.url:
            r = requests.Response()
            r.status_code, r.url, r.request = 302, request.url, request
            r.headers["Location"] = "https://old-site.example.com/notes.pdf"
            return r
        raise requests.ConnectionError("Name not resolved", request=request)

    s = net.make_session()
    for adapter in s.adapters.values():
        adapter.send = types.MethodType(send, adapter)
    with pytest.raises(NetworkError, match="Can't reach old-site.example.com") as err:
        s.get("https://learn.ieu.edu.tr/student/resource/1")
    assert err.value.host == "old-site.example.com"


def test_a_connection_that_keeps_coming_and_going_stops_the_files_after_a_few_rounds(monkeypatch):
    from studia import api as api_mod

    monkeypatch.setattr(api_mod.time, "sleep", lambda s: None)
    monkeypatch.setattr(net, "online", lambda timeout=5.0: False)
    waits = []
    monkeypatch.setattr(net, "wait_online", lambda told=None, **kw: waits.append(1))

    def download(rid, session, progress):
        raise NetworkError("Can't reach learn.ieu.edu.tr. Check your internet connection.")

    a, pushed = _api(download)
    _run(a)
    assert len(waits) == 3
    errors = [e for e in pushed if e["state"] == "error"]
    assert "keeps dropping" in errors[0]["error"] and [e.get("quiet", False) for e in errors] == [False, True]


def test_a_short_wifi_drop_still_waits_without_asking_the_real_network(monkeypatch):
    from studia import api as api_mod

    monkeypatch.setattr(api_mod.time, "sleep", lambda s: None)
    monkeypatch.setattr(net, "online", lambda timeout=5.0: True)     # (IEULearn's own answer is what counts here)
    tries = {"m1": 0, "m2": 0}

    def download(rid, session, progress):
        tries[rid] += 1
        if rid == "m1":
            raise NetworkError("Can't reach learn.ieu.edu.tr. Check your internet connection.")
        return "C:/x/m2.pdf"

    a, pushed = _api(download)
    _run(a)
    assert tries == {"m1": 4, "m2": 0}                                # 3 waits, then the rest stop: said once
    assert [(e["id"], e.get("quiet", False)) for e in pushed if e["state"] == "error"] == [("m1", False), ("m2", True)]
