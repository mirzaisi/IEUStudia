"""Waiting for the internet never goes on for ever, and says why it can't get through: this PC isn't on a network,
a Wi-Fi wants you to sign in first, or (what Windows itself says the PC is online, so the browser works) something
blocks IEUStudia alone or IEU's sites are down: then it stops after a minute or two and names firewall, antivirus,
VPN and proxy. A sync you asked for can be stopped while it waits; a proxy that refuses and a server that answers too
slowly are named."""

import threading
import time
import types

import pytest
import requests

from studia import net
from studia.api import Api
from studia.net import NetworkError, Offline
from test_page_feedback import node, run


@pytest.fixture
def offline(monkeypatch):
    state = types.SimpleNamespace(online=False, pc="none", looks=0)

    def online(timeout=5.0, stop=None):
        state.looks += 1
        return state.online

    monkeypatch.setattr(net, "online", online)
    monkeypatch.setattr(net, "pc_connectivity", lambda: state.pc)
    monkeypatch.setattr(net, "ONLINE_EVERY_S", 0.01)
    return state


def test_with_the_pc_online_but_ieulearn_unreachable_it_stops_soon_and_names_the_likely_causes(offline, monkeypatch):
    monkeypatch.setattr(net, "BLOCKED_S", 0.15)
    offline.pc = "internet"
    told = []
    started = time.monotonic()
    with pytest.raises(Offline) as e:
        net.wait_online(told=told.append)
    assert time.monotonic() - started < 5 and told == ["internet"]
    for words in ("This PC is online", "firewall", "antivirus", "VPN", "proxy", "open learn.ieu.edu.tr in your browser"):
        assert words in str(e.value)


@pytest.mark.parametrize("pc,words", [("none", "turn on Wi-Fi or plug in the cable"),
                                      ("portal", "This Wi-Fi wants you to sign in first"),
                                      ("local", "doesn't reach the internet"),
                                      ("", "on campus Wi-Fi, make sure you're signed in")])
def test_with_no_connection_it_waits_while_the_pc_is_awake_then_says_why(offline, monkeypatch, pc, words):
    monkeypatch.setattr(net, "OFFLINE_LOOKS", 4)
    offline.pc = pc
    with pytest.raises(Offline, match=words):
        net.wait_online()
    assert offline.looks == 4


def test_the_connection_coming_back_ends_the_wait_and_what_windows_says_is_told_when_it_changes(offline, monkeypatch):
    told = []
    changes = iter(["none", "none", "portal", "portal", "internet"])

    def pc():
        value = next(changes, "internet")
        if value == "internet":
            offline.online = True
        return value

    monkeypatch.setattr(net, "pc_connectivity", pc)
    assert net.wait_online(told=told.append) is True and told == ["none", "portal", "internet"]


def test_stop_ends_the_wait_at_once(offline):
    stop = threading.Event()
    threading.Timer(0.05, stop.set).start()
    assert net.wait_online(stop.is_set) is False


def test_a_sync_waiting_for_the_internet_can_be_stopped_and_says_so(offline):
    calls, said, why = [], [], []
    stop = threading.Event()

    def sync():
        calls.append(1)
        raise NetworkError("Can't reach learn.ieu.edu.tr. Check your internet connection.")

    threading.Timer(0.1, stop.set).start()
    with pytest.raises(Offline, match="Stopped waiting for the internet connection"):
        net.until_online(sync, lambda: said.append(1), stop=stop.is_set, told=why.append)
    assert calls == [1] and said == [1] and why == ["none"]


def test_a_connection_that_keeps_coming_and_going_is_tried_a_few_times_not_for_ever(offline, monkeypatch):
    calls = []

    def sync():
        calls.append(1)
        offline.online = False                            # it drops again as soon as the sync starts
        raise NetworkError("Can't reach learn.ieu.edu.tr. Check your internet connection.")

    monkeypatch.setattr(net, "wait_online", lambda stop, told: setattr(offline, "online", True) or True)
    monkeypatch.setattr(net, "online", lambda timeout=5.0: False)
    with pytest.raises(NetworkError, match="Can't reach learn.ieu.edu.tr"):
        net.until_online(sync)
    assert len(calls) == net.UNTIL_ONLINE_WAITS + 1


@pytest.mark.parametrize("pc,online", [("internet", True), ("none", False)])
def test_a_proxy_that_refuses_is_said_at_once_when_the_pc_is_online(monkeypatch, pc, online):
    def head(url, **kw):
        raise requests.exceptions.ProxyError("Cannot connect to proxy.")

    monkeypatch.setattr(net, "plain_session", lambda: types.SimpleNamespace(head=head, close=lambda: None))
    monkeypatch.setattr(net, "pc_connectivity", lambda: pc)
    assert net.online() is online            # online: the proxy's own message comes now, not after a long wait


def test_what_windows_says_about_the_connection_is_one_of_its_answers():
    assert net.pc_connectivity() in ("internet", "portal", "local", "none", "")    # (a local call, no network)


@pytest.mark.parametrize("error,words", [
    (requests.exceptions.ProxyError("Cannot connect to proxy."), "proxy server set on this"),
    (requests.exceptions.ReadTimeout("Read timed out."), "learn.ieu.edu.tr took too long to answer: it may be busy"),
    (requests.exceptions.ConnectTimeout("timed out"), "Can't reach learn.ieu.edu.tr"),
])
def test_a_proxy_that_refuses_and_a_slow_answer_are_named(monkeypatch, error, words):
    def request(self, method, url, **kw):
        raise error

    monkeypatch.setattr(requests.Session, "request", request)
    with pytest.raises(NetworkError, match=words):
        net.make_session().get("https://learn.ieu.edu.tr/student")
    assert net.plain_error(requests.exceptions.ProxyError("x")).startswith("The proxy server set on this")


def test_a_server_that_takes_the_connection_and_never_answers_is_slow_not_out_of_reach():
    """Through the real adapter: urllib3 asks again on a read that times out (GET), then requests turns its last
    error into a ConnectionError. That was said as "Can't reach … Check your internet connection" (the test above
    raises ReadTimeout itself, past the adapter). Local socket only."""
    import socket

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(16)
    held = []

    def accept():
        while True:
            try:
                held.append(srv.accept()[0])           # taken, never answered
            except OSError:
                return

    threading.Thread(target=accept, daemon=True).start()
    try:
        with pytest.raises(NetworkError, match=r"took too long to answer: it may be busy") as err:
            net.make_session().get(f"http://127.0.0.1:{srv.getsockname()[1]}/student", timeout=(2, 0.3))
        assert len(held) >= 2                                # it was asked again first
        assert net.plain_error(err.value.__cause__).startswith("127.0.0.1 took too long to answer")
    finally:
        srv.close()
        for c in held:
            c.close()


def _sync_api(sync):
    pushed = []
    lock = threading.Lock()
    lock.acquire()
    ns = types.SimpleNamespace(_push=pushed.append, _sync_lock=lock, _remind=lambda: None, _sync_stop=threading.Event(),
                               _courses_dirty=threading.Event(), _course_views=lambda: [], _own_new=lambda v, i: 0,
                               _s=types.SimpleNamespace(sync=types.SimpleNamespace(sync=sync, sync_content=lambda p=None: {
                                   "new_materials": [], "new_announcements": [], "errors": []}),
                                   lib=types.SimpleNamespace(get_meta=lambda k: "")))
    return ns, pushed


def test_a_sync_you_ask_for_may_sign_in_at_once_one_the_app_starts_may_not():
    """You pressed Sync: a sign-in that failed a moment ago is tried again at once (not after its 5 minutes)."""
    allowed = []

    def sync(progress, on_courses):
        return types.SimpleNamespace(errors=[], new_ids=[])

    for quiet in (False, True):
        ns, pushed = _sync_api(sync)
        ns._s.sync.allow_sign_in = lambda quiet=quiet: allowed.append(quiet)
        Api._run_sync(ns, quiet=quiet)
    assert allowed == [False]


def test_a_courses_page_that_shows_the_sign_in_form_is_signed_out():
    from studia.ieulearn import IEULearn
    from test_signin_cases import COURSES, FORM, Site

    assert not IEULearn(Site({("GET", "/student"): (200, FORM, "/student")})).is_logged_in()
    assert IEULearn(Site({("GET", "/student"): (200, COURSES, "/student")})).is_logged_in()


def test_pressing_stop_waiting_ends_the_sync_with_its_reason(offline):
    def sync(progress, on_courses):
        raise NetworkError("Can't reach learn.ieu.edu.tr. Check your internet connection.")

    ns, pushed = _sync_api(sync)
    offline.pc = "portal"
    threading.Timer(0.1, lambda: Api.sync_stop(ns)).start()
    assert Api._run_sync(ns) is False and not ns._sync_lock.locked()
    told = [e for e in pushed if e.get("offline") and e.get("why") == "portal"]
    assert told and told[0]["message"] == ("Waiting for the internet connection. This Wi-Fi wants you to sign in first: "
                                          "open any website in your browser and sign in to the Wi-Fi. The sync goes on "
                                          "by itself.")
    assert pushed[-1]["phase"] == "error" and pushed[-1]["message"].startswith("Stopped waiting for the internet")


@node
def test_the_top_bar_says_it_waits_for_the_internet_and_offers_to_stop(tmp_path):
    out = run(tmp_path, """
  let stopped = 0; St.mocks.sync_stop = () => { stopped++; return { ok: true }; };
  ev({ type: "sync", phase: "start", message: "Syncing…" });
  ev({ type: "sync", phase: "progress", offline: true, why: "none", message: "Waiting for the internet connection. This PC isn't connected to a network: turn on Wi-Fi or plug in the cable. The sync goes on by itself." });
  out.waiting = top(); out.home = view();
  A["sync-stop"](); await tick();
  out.stopped = stopped;
  ev({ type: "sync", phase: "progress", message: "Looking for new recordings…" });
  out.back = top();
  ev({ type: "sync", phase: "progress", offline: true, why: "internet", message: "This PC is online, but IEULearn and Panopto don't answer IEUStudia: trying again for a minute or two." });
  out.blocked = top();
  ev({ type: "sync", phase: "done", message: "Up to date", new_count: 0, errors: [] });
  ev({ type: "content", phase: "start" });
  ev({ type: "content", phase: "progress", offline: true, why: "none", message: "Waiting for the internet connection. The sync goes on by itself." });
  out.contentWaits = top();
""")
    assert 'data-act="sync-stop"' in out["waiting"] and "Waiting for the internet…" in out["waiting"]
    assert "Stop waiting" in out["waiting"] and "turn on Wi-Fi or plug in the cable" in out["home"]
    assert out["stopped"] == 1
    assert 'data-act="sync-stop"' not in out["back"] and "Looking for new recordings…" in out["back"]
    assert "IEULearn doesn't answer…" in out["blocked"]
    assert 'data-act="sync-stop"' in out["contentWaits"] and "Waiting for the internet…" in out["contentWaits"]


@node
def test_home_on_screen_says_why_it_waits_and_f5_or_stop_keep_it_honest(tmp_path):
    """The reason and what to do were only in the top bar's tooltip (Home on screen kept "Syncing…"); F5 or a click on
    the waiting label replaced the wait with a "Syncing…" spinner without Stop; Stop showed no reaction for seconds."""
    out = run(tmp_path, """
  let stopped = 0;
  St.mocks.sync_stop = () => { stopped++; return { ok: true }; };
  A.sync(); await tick();                                   // pressed on Home: Home is drawn ("Syncing…")
  ev({ type: "sync", phase: "start", message: "Syncing…" });
  ev({ type: "sync", phase: "progress", offline: true, why: "portal", message: "Waiting for the internet connection. This Wi-Fi wants you to sign in first: open any website in your browser and sign in to the Wi-Fi. The sync goes on by itself." });
  await tick();
  out.onScreen = els["#view"].innerHTML.replace(/\\s+/g, " ");
  A.sync(); await tick();                                   // F5 while it waits (the sync answers "running")
  out.afterF5 = top();
  A["sync-stop"](); await tick(0);
  out.stopping = top(); out.homeStopping = els["#view"].innerHTML.replace(/\\s+/g, " ");
  ev({ type: "sync", phase: "error", message: "Stopped waiting for the internet connection. Sync again once you're online." });
  await tick();
  out.afterStop = els["#view"].innerHTML.replace(/\\s+/g, " ");
  out.stopped = stopped;
""")
    assert "This Wi-Fi wants you to sign in first" in out["onScreen"] and 'data-act="sync-stop"' in out["onScreen"]
    assert "Stop waiting" in out["afterF5"] and "Waiting for the internet…" in out["afterF5"]
    assert "Stopping…" in out["stopping"] and 'data-act="sync-stop"' not in out["stopping"]
    assert "Stopping…" in out["homeStopping"] and out["stopped"] == 1
    assert "This Wi-Fi wants you to sign in first" not in out["afterStop"] and "Stopping…" not in out["afterStop"]


def test_stop_waiting_acts_between_the_two_looks(monkeypatch):
    looked = []

    class Session:
        def head(self, url, **kw):
            looked.append(url)
            raise requests.ConnectionError("no route")

        def close(self):
            pass

    monkeypatch.setattr(net, "plain_session", Session)
    stop = iter([False, True])
    assert net.online(stop=lambda: next(stop)) is False and len(looked) == 1


def test_a_refusing_proxy_is_said_at_once_where_the_pc_cant_say_it_is_online(monkeypatch):
    """Linux (and an older Windows) say nothing about the connection: a refusing proxy waited ~15 minutes, then
    "No internet connection"."""
    class Session:
        def head(self, url, **kw):
            raise requests.exceptions.ProxyError("Cannot connect to proxy.")

        def close(self):
            pass

    monkeypatch.setattr(net, "plain_session", Session)
    monkeypatch.setattr(net, "pc_connectivity", lambda: "")
    assert net.online() is True


def test_course_files_that_wait_too_long_stop_with_the_reason_said_once(offline, monkeypatch):
    from studia import api as api_mod

    monkeypatch.setattr(api_mod.time, "sleep", lambda s: None)
    monkeypatch.setattr(net, "BLOCKED_S", 0.05)
    offline.pc = "internet"
    a = Api.__new__(Api)
    a._mat_lock, a._mat_queue, a._mat_running = threading.Lock(), ["m1", "m2"], True
    a._courses_dirty = threading.Event()
    pushed = []
    a._push = pushed.append
    a._services = types.SimpleNamespace(
        materials=types.SimpleNamespace(download=lambda rid, s, p: (_ for _ in ()).throw(NetworkError("Can't reach "
                                        "learn.ieu.edu.tr. Check your internet connection.")), index_text=lambda rid: 0),
        sync=types.SimpleNamespace(worker_session=lambda: object(), absorb=lambda s: None))
    a._ready = threading.Event()
    a._ready.set()
    worker = threading.Thread(target=a._material_worker, daemon=True)
    worker.start()
    worker.join(10)
    assert not worker.is_alive()
    toasts = [e["message"] for e in pushed if e.get("type") == "toast"]
    assert toasts == ["This PC is online, but IEULearn and Panopto don't answer IEUStudia: trying again for a minute or two."]
    errors = [(e["id"], e.get("quiet", False), e["error"]) for e in pushed if e.get("state") == "error"]
    assert [(i, q) for i, q, _ in errors] == [("m1", False), ("m2", True)] and "firewall" in errors[0][2]
