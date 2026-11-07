"""'Continue with ChatGPT' against a fake OpenAI: the browser round trip to the loopback callback, the code exchange,
the ID token checks (signature, issuer, audience, nonce), the encrypted sign-in, refreshing, streaming answers and
their errors, signing out. Also every way it can go wrong on a student's PC: a sign-in this PC can't decrypt, nothing
allowed to listen on 127.0.0.1, a browser asking for https://, the way back blocked (a pasted address), OpenAI out of
reach, a wrong PC clock, each error OpenAI sends back. No network: requests are answered by a fake session; the
"browser" is a local GET."""

import base64
import email.utils
import hashlib
import http.client
import json
import random
import socket
import ssl
import threading
import time
import urllib.parse
import urllib.request

import pytest
import requests

from studia import ai, ai_setup, chatgpt, paths


# ----------------------------------------------------------------------------- a small RSA key for signing ID tokens
def _is_prime(n, rng):
    if n < 4:
        return n in (2, 3)
    for p in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if n % p == 0:
            return n == p
    d, r = n - 1, 0
    while d % 2 == 0:
        d, r = d // 2, r + 1
    for _ in range(20):
        x = pow(rng.randrange(2, n - 1), d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _keypair(bits=1024, seed=7):
    rng = random.Random(seed)
    e = 65537

    def prime():
        while True:
            p = rng.getrandbits(bits // 2) | (1 << (bits // 2 - 1)) | 1
            if _is_prime(p, rng) and (p - 1) % e:
                return p

    p, q = prime(), prime()
    n = p * q
    return n, e, pow(e, -1, (p - 1) * (q - 1))


N, E, D = _keypair()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _int_b64(i: int) -> str:
    return _b64(i.to_bytes((i.bit_length() + 7) // 8, "big"))


def _sign(claims: dict, kid="k1", d=D) -> str:
    head = _b64(json.dumps({"alg": "RS256", "kid": kid}).encode())
    body = _b64(json.dumps(claims).encode())
    k = (N.bit_length() + 7) // 8
    digest_info = bytes.fromhex("3031300d060960864801650304020105000420") + hashlib.sha256(f"{head}.{body}".encode()).digest()
    em = b"\x00\x01" + b"\xff" * (k - len(digest_info) - 3) + b"\x00" + digest_info
    sig = pow(int.from_bytes(em, "big"), d, N).to_bytes(k, "big")
    return f"{head}.{body}.{_b64(sig)}"


# ----------------------------------------------------------------------------- a fake OpenAI
class _Resp:
    def __init__(self, status=200, body=None, lines=None, headers=None):
        self.status_code, self._body, self._lines = status, body if body is not None else {}, lines or []
        self.headers = headers or {}
        self.closed = False

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)

    def iter_lines(self):
        yield from self._lines

    def close(self):
        self.closed = True


def _sse(*events):
    out = []
    for ev in events:
        out += [f"event: {ev['type']}".encode(), b"data: " + json.dumps(ev).encode(), b""]
    return out


class FakeOpenAI:
    def __init__(self):
        self.calls = []
        self.issued = "oaiapp_123"
        self.nonce = ""
        self.scope = "chatgpt.tokens.use.direct email offline_access openid profile resource.invoke"
        self.claims = {}
        self.refresh_error = None
        self.stream_answers = []          # (status, body or SSE lines)
        self.token_answer = None          # the code exchange: (status, body), or an exception (the network)
        self.reach = None                 # the check at the start: a status, or an exception
        self.date = None                  # a Date header on every answer (OpenAI's clock)
        self.models_answers = []          # (status, body) before the usual list
        self.now = time.time              # OpenAI's clock (a test may set this PC's elsewhere)

    def _r(self, status=200, body=None, lines=None):
        return _Resp(status, body, lines, {"Date": self.date} if self.date else None)

    def get(self, url, timeout=0, headers=None, **kw):
        self.calls.append(("GET", url, None))
        if url == chatgpt.CONFIG_URL and timeout == chatgpt.REACH_TIMEOUT and self.reach is not None:
            if isinstance(self.reach, Exception):
                raise self.reach
            return self._r(self.reach, {})
        if url == chatgpt.CONFIG_URL:
            return self._r(body={"jwks_uri": "https://auth.openai.com/jwks", "revocation_endpoint": "https://auth.openai.com/revoke"})
        if url.endswith("/jwks"):
            return self._r(body={"keys": [{"kty": "RSA", "kid": "k1", "n": _int_b64(N), "e": _int_b64(E), "alg": "RS256"}]})
        if url == f"{chatgpt.API}/models":
            if self.models_answers:
                return self._r(*self.models_answers.pop(0))
            return self._r(body={"models": [{"slug": "gpt-6.1-sol", "display_name": "GPT-6.1", "visibility": "list"},
                                            {"slug": "hidden", "visibility": "hide"}]})
        raise AssertionError(url)

    def post(self, url, data=None, json=None, headers=None, stream=False, timeout=0, **kw):
        self.calls.append(("POST", url, data if data is not None else json))
        if url == chatgpt.TOKEN_URL and data["grant_type"] == "authorization_code":
            if self.token_answer is not None:
                if isinstance(self.token_answer, Exception):
                    raise self.token_answer
                return self._r(*self.token_answer)
            claims = {"iss": chatgpt.AUTH, "aud": self.issued, "sub": "user-1", "email": "student@example.com",
                      "exp": self.now() + 3600, "nonce": self.nonce, **self.claims}
            return self._r(body={"access_token": "at-1", "refresh_token": "rt-1", "id_token": _sign(claims),
                                 "token_type": "Bearer", "expires_in": 3600, "scope": self.scope})
        if url == chatgpt.TOKEN_URL and data["grant_type"] == "refresh_token":
            if self.refresh_error:
                return _Resp(400, {"error": self.refresh_error})
            return _Resp(body={"access_token": "at-2", "refresh_token": "rt-2", "expires_in": 3600, "scope": self.scope})
        if url.endswith("/revoke"):
            return _Resp(200, {})
        if url == f"{chatgpt.API}/responses":
            status, payload = self.stream_answers.pop(0)
            if isinstance(payload, Exception):                 # the network
                raise payload
            if isinstance(payload, _Resp):
                return payload
            return _Resp(status, payload if isinstance(payload, dict) else {}, payload if isinstance(payload, list) else None)
        raise AssertionError(url)


@pytest.fixture
def openai(monkeypatch):
    fake = FakeOpenAI()
    monkeypatch.setattr(chatgpt, "_session", lambda: fake)
    chatgpt._jwks.clear()
    chatgpt._attempt.clear()
    chatgpt._clock.clear()
    chatgpt._host.clear()
    yield fake
    chatgpt.cancel_sign_in()
    chatgpt._clock.clear()


def _follow(link):
    """What a browser does with IEUStudia's own link: asks for it, and is sent on (302) to OpenAI's sign-in page."""
    u = urllib.parse.urlsplit(link)
    conn = http.client.HTTPConnection(u.hostname, u.port, timeout=5)
    try:
        conn.request("GET", u.path + (f"?{u.query}" if u.query else ""))
        r = conn.getresponse()
        return r.status, r.getheader("Location") or ""
    finally:
        conn.close()


def _browser(fake, params=None, *, wrong_state=False):
    """A browser that opens IEUStudia's link, signs in at once and follows the redirect to the loopback callback."""
    def open_url(link):
        assert link.startswith("http://127.0.0.1:") and link.endswith(chatgpt.START)
        status, url = _follow(link)
        assert status == 302 and url.startswith(chatgpt.AUTHORIZE_URL)
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        fake.nonce, fake.authorize = q["nonce"], q

        def go():
            back = {"code": "c0de", "state": "nope" if wrong_state else q["state"], "client_id": fake.issued, **(params or {})}
            try:
                urllib.request.urlopen(f"{q['redirect_uri']}?{urllib.parse.urlencode(back)}", timeout=5).read()
            except Exception:  # noqa: BLE001 - a 400 for a wrong state
                pass
        threading.Thread(target=go, daemon=True).start()
        return True
    return open_url


def _sign_in(fake, **kw):
    events, done = [], threading.Event()
    chatgpt.start_sign_in(lambda e: (events.append(e), e["state"] != "waiting" and done.set()),
                          open_browser=_browser(fake, **kw.pop("browser", {})), **kw)
    assert done.wait(10), "no outcome"
    return events[-1]


def test_continue_with_chatgpt_registers_signs_in_and_keeps_it_encrypted(openai):
    e = _sign_in(openai)
    assert e == {"type": "chatgpt", "state": "done", "error": "", "email": "student@example.com", "code": ""}
    q = openai.authorize
    assert q["client_id"] == "dynamic_agent_client" and q["agent_name_hint"] == "IEUStudia"
    assert q["redirect_uri"].startswith("http://127.0.0.1:") and q["redirect_uri"].endswith("/auth/callback")
    assert q["scope"] == chatgpt.SCOPES and q["resource"] == chatgpt.API and q["code_challenge_method"] == "S256"
    assert q["ext_agent_host_id"].startswith("urn:uuid:") and q["ext_agent_host_id"] == chatgpt.host_id()
    exchange = next(c for c in openai.calls if c[0] == "POST" and c[2].get("grant_type") == "authorization_code")[2]
    assert exchange["client_id"] == "oaiapp_123" and exchange["redirect_uri"] == q["redirect_uri"]
    assert base64.urlsafe_b64encode(hashlib.sha256(exchange["code_verifier"].encode()).digest()).rstrip(b"=").decode() == q["code_challenge"]
    assert chatgpt.signed_in() and chatgpt.status()["email"] == "student@example.com"
    raw = (chatgpt._dir() / "chatgpt.bin").read_bytes()
    assert b"rt-1" not in raw and b"student@example.com" not in raw          # encrypted with DPAPI
    # the next sign-in reuses the issued client id and the hints, without the name hint
    _sign_in(openai)
    q = openai.authorize
    assert q["client_id"] == "oaiapp_123" and "agent_name_hint" not in q and q["login_hint"] == "student@example.com"
    assert q["id_token_hint"]


def test_declined_or_forged_sign_ins_are_not_kept(openai, monkeypatch):
    e = _sign_in(openai, browser={"params": {"error": "access_denied"}})
    assert e["state"] == "error" and "didn't allow" in e["error"] and not chatgpt.signed_in()
    openai.claims = {"aud": "someone-else"}
    assert "not meant for IEUStudia" in _sign_in(openai)["error"]
    openai.claims = {"nonce": "replayed"}
    assert "does not belong" in _sign_in(openai)["error"]
    openai.claims = {}
    monkeypatch.setattr(chatgpt, "_verify_rs256", lambda *a: False)
    assert "could not be verified" in _sign_in(openai)["error"] and not chatgpt.signed_in()


def test_a_callback_with_another_state_is_refused(openai, monkeypatch):
    monkeypatch.setattr(chatgpt, "SIGN_IN_TIMEOUT_S", 1.5)
    e = _sign_in(openai, browser={"wrong_state": True})
    assert e["state"] == "error" and "in time" in e["error"]
    assert not any(c[0] == "POST" for c in openai.calls)                        # no code exchanged


def _wait_for(cond, s=5.0):
    end = time.monotonic() + s
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_the_page_goes_through_ieustudias_own_link_so_its_opening_is_noticed(openai):
    events, links = [], []
    r = chatgpt.start_sign_in(events.append, open_browser=lambda link: links.append(link) or True)
    assert r["state"] == "waiting" and r["launched"] and r["link"] == links[0]
    port = urllib.parse.urlsplit(r["link"]).port
    si = chatgpt.status()["sign_in"]
    assert {k: si[k] for k in ("state", "error", "link", "page", "opened", "launched", "step")} == {
        "state": "waiting", "error": "", "link": r["link"], "page": r["page"], "opened": False, "launched": True,
        "step": "launched"}
    assert r["page"].startswith(chatgpt.AUTHORIZE_URL)      # to copy by hand: straight to OpenAI
    status, url = _follow(r["link"])
    assert status == 302 and url == r["page"]
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
    assert q["redirect_uri"] == f"http://127.0.0.1:{port}{chatgpt.CALLBACK}" and q["state"]
    assert chatgpt.status()["sign_in"]["opened"] and chatgpt.status()["sign_in"]["step"] == "opened"
    opened = [e for e in events if e.get("opened")]
    assert opened == [{"type": "chatgpt", "state": "waiting", "step": "opened", "opened": True}]
    assert [e["step"] for e in events if "step" in e] == ["listening", "opening", "launched", "opened"]
    _follow(r["link"])                                       # opened again: said once
    assert len([e for e in events if e.get("opened")]) == 1
    _wait_for(lambda: any("reach" in e for e in events))     # (the check at the start, said by itself)
    said = len(events)
    chatgpt.cancel_sign_in()
    si = chatgpt.status()["sign_in"]
    assert si["state"] == "" and "link" not in si and "page" not in si
    assert len(events) == said                               # stopping says nothing more


def test_no_browser_is_said_and_the_page_can_be_opened_again_or_stopped(openai):
    def broken(link):
        raise OSError("No application is associated with the specified file for this operation")

    r = chatgpt.start_sign_in(open_browser=broken)
    assert r["state"] == "waiting" and r["launched"] is False
    assert chatgpt.status()["sign_in"]["launched"] is False
    assert chatgpt.start_sign_in(open_browser=lambda link: False)["launched"] is False
    again = []
    r2 = chatgpt.reopen_sign_in(open_browser=lambda link, way="": again.append((link, way)) or True)
    si = chatgpt.status()["sign_in"]
    assert r2 == {"state": "waiting", "link": si["link"], "page": si["page"], "launched": True, "browser": "",
                  "via": "default", "way": "default", "again": 1} and again == [(r2["link"], "default")]
    assert si["launched"] is True                            # what the page shows follows it
    assert _follow(r2["link"])[0] == 302                     # the same sign-in: OpenAI still comes back to it
    chatgpt.cancel_sign_in()
    assert "link" not in chatgpt.status()["sign_in"] and "page" not in chatgpt.status()["sign_in"]
    assert chatgpt.reopen_sign_in(open_browser=lambda link: pytest.fail("nothing to open")) == {"state": ""}


def test_open_the_page_again_takes_another_way_each_time_and_keeps_one_that_worked(openai):
    """A browser can start and show nothing (stuck, showing its profile chooser, closed by Family Safety): each Open
    the page again takes the next way (the default browser, then Microsoft Edge, then Windows' own open) and says
    which. A way that brought the page is kept (its tab was only closed); one that can't start here is passed over."""
    used = []

    def opener(link, way=""):
        used.append(way)
        return {"ok": True, "browser": "Microsoft Edge" if way == "edge" else "Brave", "via": way or "default",
                "why": ""}

    r = chatgpt.start_sign_in(open_browser=lambda link: opener(link))   # open_link's own first way: the shell
    assert used == [""] and r["via"] == "default"
    ways = [chatgpt.reopen_sign_in(open_browser=opener)["way"] for _ in range(4)]
    assert ways == ["default", "edge", "shell", "default"] and used[1:] == ways
    si = chatgpt.status()["sign_in"]
    assert (si["way"], si["via"], si["again"], si["browser"]) == ("default", "default", 4, "Brave")
    _follow(r["link"])                                       # the page opened with it: the tab was only closed
    assert chatgpt.reopen_sign_in(open_browser=opener)["way"] == "default"

    def no_edge(link, way=""):
        used.append(way)
        return {"ok": way != "edge", "browser": "", "via": way, "why": ""}

    used.clear()
    out = chatgpt.reopen_sign_in(open_browser=no_edge)      # nothing came of it: Edge next, which can't start here
    assert used == ["edge", "shell"] and out["way"] == "shell" and out["launched"]
    # Open in Microsoft Edge: Edge only; when it doesn't start, what the page shows stays as it was
    out = chatgpt.reopen_sign_in(open_browser=no_edge, way="edge")
    si = chatgpt.status()["sign_in"]
    assert out["launched"] is False and (si["way"], si["launched"], si["again"]) == ("shell", True, 6)
    out = chatgpt.reopen_sign_in(open_browser=opener, way="edge")
    assert (out["way"], out["browser"], out["again"]) == ("edge", "Microsoft Edge", 7)
    assert chatgpt.reopen_sign_in(open_browser=opener)["way"] == "shell"     # after Edge: on to the next one
    # nothing at all starts: said as no browser
    out = chatgpt.reopen_sign_in(open_browser=lambda link, way="": False)
    assert out["launched"] is False and chatgpt.status()["sign_in"]["launched"] is False
    assert "asked again: edge" in chatgpt.details() and "opened" in chatgpt.details()
    # run as administrator, the first open went through Explorer, as the default browser's way does then: the first
    # Open the page again is Microsoft Edge, not the same Explorer once more
    chatgpt.start_sign_in(open_browser=lambda link: {"ok": True, "browser": "Brave", "via": "explorer", "why": ""})
    assert chatgpt.reopen_sign_in(open_browser=opener)["way"] == "edge"


def test_a_page_that_opened_behind_ieustudias_window_is_said(openai, monkeypatch):
    """The browser asked for IEUStudia's link (the page opened in it), but a moment later IEUStudia's window is still
    the one in front: Windows flashed the browser's taskbar button instead of bringing it forward. Said, and said no
    more once a later look finds the browser in front (Open the page again looks once more)."""
    monkeypatch.setattr(chatgpt, "BEHIND_S", 0.05)
    front = {"ours": True}
    events = []
    r = chatgpt.start_sign_in(events.append, open_browser=lambda link: True, in_front=lambda: front["ours"])
    assert not chatgpt.status()["sign_in"].get("behind")
    _follow(r["link"])
    assert _wait_for(lambda: chatgpt.status()["sign_in"].get("behind") is True)
    assert [e["behind"] for e in events if "behind" in e] == [True] and " behind" in chatgpt.details()
    front["ours"] = False                                    # the browser came forward
    _follow(r["link"])
    assert _wait_for(lambda: chatgpt.status()["sign_in"].get("behind") is False)
    assert [e["behind"] for e in events if "behind" in e] == [True, False]
    # the browser in front at once: nothing is said; nor without a way to tell, nor when the look fails
    for look in (lambda: False, None, lambda: 1 / 0):
        events = []
        r = chatgpt.start_sign_in(events.append, open_browser=lambda link: True, in_front=look)
        _follow(r["link"])
        time.sleep(0.3)
        assert not any("behind" in e for e in events) and not chatgpt.status()["sign_in"].get("behind")


def test_run_as_administrator_is_said_only_when_it_is_so(openai, monkeypatch):
    """A browser started from an app run as administrator may not show up (in Firefox 103 links did nothing): what to
    do is said only when IEUStudia really runs so, and when the time runs out on a page that never opened. Copy
    details says it too."""
    from studia import osutil

    monkeypatch.setattr(chatgpt, "SIGN_IN_TIMEOUT_S", 1.0)
    for elevated in (True, False):
        monkeypatch.setattr(osutil.impl(), "elevated", lambda e=elevated: e)
        events, done = [], threading.Event()
        r = chatgpt.start_sign_in(lambda e: (events.append(e), e["state"] != "waiting" and done.set()),
                                  open_browser=lambda link: True)
        assert r["elevated"] is elevated and chatgpt.status()["sign_in"]["elevated"] is elevated
        assert ("run as administrator" in chatgpt.details()) is elevated
        assert done.wait(10) and events[-1]["code"] == "timeout" and "never opened" in events[-1]["error"]
        assert ("without Run as administrator" in events[-1]["error"]) is elevated, events[-1]["error"]
    # the page opened: being run as administrator didn't stop it, so the end doesn't say it
    monkeypatch.setattr(osutil.impl(), "elevated", lambda: True)
    events, done = [], threading.Event()
    r = chatgpt.start_sign_in(lambda e: (events.append(e), e["state"] != "waiting" and done.set()),
                              open_browser=lambda link: True)
    _follow(r["link"])
    assert done.wait(10) and "not completed in time" in events[-1]["error"]
    assert "administrator" not in events[-1]["error"]


def test_a_page_that_never_opened_says_so_when_time_runs_out(openai, monkeypatch):
    monkeypatch.setattr(chatgpt, "SIGN_IN_TIMEOUT_S", 1.0)
    for open_it, said in ((False, "never opened in a browser"), (True, "not completed in time")):
        events, done = [], threading.Event()
        r = chatgpt.start_sign_in(lambda e: (events.append(e), e["state"] != "waiting" and done.set()),
                                  open_browser=lambda link: True)
        if open_it:
            _follow(r["link"])
        assert done.wait(10)
        assert events[-1]["state"] == "error" and said in events[-1]["error"]
        # the error shows only the step's own button: the text says to click it (the other ways come with a new try)
        assert "Click Continue with ChatGPT to start again" in events[-1]["error"]
        assert chatgpt.status()["sign_in"]["state"] == "error" and "link" not in chatgpt.status()["sign_in"]


def test_the_time_out_offers_microsoft_edge_only_when_it_wasnt_what_showed_nothing(openai, monkeypatch):
    """A page that never opened: the time-out names Open in Microsoft Edge for the next try, but not when Edge was the
    browser that showed nothing (Edge as the default browser, or Open in Microsoft Edge), nor when no browser could
    be started at all (Edge was tried then too), nor on a PC without Edge."""
    from studia import osutil

    monkeypatch.setattr(chatgpt, "SIGN_IN_TIMEOUT_S", 1.0)
    monkeypatch.setattr(osutil.impl(), "elevated", lambda: False)
    for edge, launched, offered in ((True, {"ok": True, "browser": "Brave", "via": "default"}, True),
                                    (True, {"ok": True, "browser": "Microsoft Edge", "via": "default"}, False),
                                    (True, {"ok": True, "browser": "Microsoft Edge", "via": "edge", "why": "no_default"},
                                     False),
                                    (True, {"ok": False, "browser": "", "via": "", "why": "default_failed"}, False),
                                    (False, {"ok": True, "browser": "Brave", "via": "default"}, False)):
        monkeypatch.setattr(osutil.impl(), "edge_available", lambda e=edge: e)
        events, done = [], threading.Event()
        chatgpt.start_sign_in(lambda e: (events.append(e), e["state"] != "waiting" and done.set()),
                              open_browser=lambda link, r=launched: r)
        assert done.wait(10) and events[-1]["code"] == "timeout" and "never opened" in events[-1]["error"]
        assert ("Open in Microsoft Edge" in events[-1]["error"]) is offered, (edge, launched)
        assert "Copy link" in events[-1]["error"]


def test_a_connection_the_browser_never_uses_holds_nothing_up(openai):
    ahead = []

    def browser(link):
        u = urllib.parse.urlsplit(link)
        ahead.append(socket.create_connection((u.hostname, u.port), timeout=5))   # opened ahead, never used
        return _browser(openai)(link)

    events, done = [], threading.Event()
    chatgpt.start_sign_in(lambda e: (events.append(e), e["state"] != "waiting" and done.set()), open_browser=browser)
    try:
        assert done.wait(10), "the unused connection held the sign-in up"
        assert events[-1]["state"] == "done"
    finally:
        for s_ in ahead:
            s_.close()


def test_listening_refused_by_security_software_still_signs_in_through_a_pasted_address(openai, monkeypatch):
    """A security program that forbids listening, even on 127.0.0.1: the browser goes straight to OpenAI's page, and
    the address of the error page it lands on afterwards brings the answer."""
    def refused(*a, **kw):
        raise PermissionError(10013, "An attempt was made to access a socket in a way forbidden by its access permissions")

    monkeypatch.setattr(chatgpt.http.server, "ThreadingHTTPServer", refused)
    opened, events = [], []
    r = chatgpt.start_sign_in(events.append, open_browser=lambda link: opened.append(link) or True)
    assert r["state"] == "waiting" and r["link"] == r["page"] and r["page"].startswith(chatgpt.AUTHORIZE_URL)
    assert opened == [r["page"]]                             # straight to OpenAI's page: nothing here to forward it
    si = chatgpt.status()["sign_in"]
    assert si["note"] == "listen" and any(e.get("note") == "listen" for e in events)
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(r["page"]).query))
    openai.nonce = q["nonce"]
    out = chatgpt.paste_address(f"{q['redirect_uri']}?code=c0de&state={q['state']}&client_id=oaiapp_123")
    assert out["state"] == "done" and chatgpt.signed_in()
    assert _wait_for(lambda: chatgpt.status()["sign_in"]["state"] == "done")


def test_signed_in_without_plan_use_is_said(openai):
    openai.scope = "email openid profile offline_access"
    assert _sign_in(openai)["state"] == "done"
    st = chatgpt.status()
    assert st["signed_in"] and not st["plan_allowed"] and not chatgpt.signed_in()
    with pytest.raises(RuntimeError, match="may not use your ChatGPT plan yet"):
        chatgpt.access_token()


def test_tokens_are_refreshed_and_an_ended_sign_in_is_forgotten(openai):
    _sign_in(openai)
    assert chatgpt.access_token() == "at-1"
    rec = chatgpt._load()
    rec["expires_at"] = time.time() + 30
    chatgpt._save(rec)
    assert chatgpt.access_token() == "at-2" and chatgpt._load()["refresh_token"] == "rt-2"
    refresh = [c[2] for c in openai.calls if c[0] == "POST" and c[2].get("grant_type") == "refresh_token"][0]
    assert refresh == {"grant_type": "refresh_token", "client_id": "oaiapp_123", "refresh_token": "rt-1", "resource": chatgpt.API}
    rec = chatgpt._load()
    rec["expires_at"] = 0
    chatgpt._save(rec)
    openai.refresh_error = "refresh_token_reused"
    with pytest.raises(RuntimeError, match="has ended"):
        chatgpt.access_token()
    assert not chatgpt.signed_in()
    assert chatgpt.status()["ended"] and chatgpt.status()["registered"]       # the set-up says it ended


def test_answers_stream_and_errors_are_explained(openai):
    _sign_in(openai)
    openai.stream_answers = [(200, _sse({"type": "response.output_text.delta", "delta": "Hel"},
                                        {"type": "response.output_text.delta", "delta": "lo"},
                                        {"type": "response.completed", "response": {"usage": {
                                            "input_tokens": 1200, "output_tokens": 3, "input_tokens_details": {"cached_tokens": 1000}}}}))]
    seen = []
    r = chatgpt.one_shot("Be brief.", [{"type": "text", "text": "Say hello"},
                                       {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "AAAA"}}],
                         model="opus", on_text=seen.append)
    assert r["text"] == "Hello" and seen[-1] == "Hello" and r["model"] == "gpt-6.1-sol"
    assert r["usage"] == {"input_tokens": 1200, "output_tokens": 3, "cache_read_input_tokens": 1000}
    body = [c[2] for c in openai.calls if c[1].endswith("/responses")][0]
    assert body["store"] is False and body["stream"] is True and body["instructions"] == "Be brief."
    assert body["reasoning"] == {"effort": "high"} and body["input"][0]["content"][1]["image_url"] == "data:image/jpeg;base64,AAAA"
    # a model without an effort setting: asked again without it
    openai.stream_answers = [(400, {"error": {"code": "subscription_sharing_unsupported_capability", "param": "reasoning.effort",
                                              "message": "unsupported"}}),
                             (200, _sse({"type": "response.output_text.delta", "delta": "ok"}, {"type": "response.completed", "response": {}}))]
    assert chatgpt.one_shot("x", [{"type": "text", "text": "y"}])["text"] == "ok"
    assert "reasoning" not in [c[2] for c in openai.calls if c[1].endswith("/responses")][-1]
    openai.stream_answers = [(429, {"error": {"code": "subscription_sharing_usage_limit_exceeded"}})]
    with pytest.raises(RuntimeError, match="usage limit"):
        chatgpt.one_shot("x", [{"type": "text", "text": "y"}])
    openai.stream_answers = [(200, _sse({"type": "response.output_text.delta", "delta": "par"},
                                        {"type": "response.failed", "response": {"error": {"code": "subscription_sharing_user_not_eligible"}}}))]
    with pytest.raises(chatgpt.NotEligible, match="Plus and Pro"):         # the set-up knows it by its class
        chatgpt.one_shot("x", [{"type": "text", "text": "y"}])
    openai.stream_answers = [(403, {"error": {"code": "subscription_sharing_user_not_eligible"}})]
    with pytest.raises(chatgpt.NotEligible, match="Use another account or workspace"):     # a workspace may be why
        chatgpt.one_shot("x", [{"type": "text", "text": "y"}])
    openai.stream_answers = [(403, {"error": {"code": "unsupported_country_region_territory", "message": "Country, "
                                              "region, or territory not supported"}})]
    with pytest.raises(RuntimeError, match="VPN or proxy"):
        chatgpt.one_shot("x", [{"type": "text", "text": "y"}])
    openai.stream_answers = [(200, _sse({"type": "response.output_text.delta", "delta": "half"}))]   # cut off
    with pytest.raises(RuntimeError, match="ended before"):
        chatgpt.one_shot("x", [{"type": "text", "text": "y"}])
    openai.stream_answers = [(0, requests.ConnectionError("HTTPSConnectionPool(host='api.openai.com'): Max retries"))]
    with pytest.raises(RuntimeError, match="^Can't reach ChatGPT. Check your internet connection"):
        chatgpt.one_shot("x", [{"type": "text", "text": "y"}])
    openai.stream_answers = [(0, requests.ReadTimeout("Read timed out."))]
    with pytest.raises(RuntimeError, match="did not answer in time"):
        chatgpt.one_shot("x", [{"type": "text", "text": "y"}])


def test_stop_ends_a_quiet_answer_at_once(openai):
    from studia.cancel import Cancelled

    _sign_in(openai)

    class Quiet(_Resp):                       # ChatGPT is thinking: nothing arrives until the stream is closed
        def __init__(self):
            super().__init__(200, {})
            self.gone = threading.Event()

        def iter_lines(self):
            self.gone.wait(10)
            raise ConnectionError("closed")
            yield b""                         # noqa: unreachable - makes this a generator

        def close(self):
            self.gone.set()

    openai.stream_answers = [(200, Quiet())]
    stop, out = threading.Event(), {}

    def ask():
        try:
            chatgpt.one_shot("x", [{"type": "text", "text": "y"}], cancel=stop)
        except BaseException as exc:  # noqa: BLE001
            out["exc"] = exc

    t = threading.Thread(target=ask)
    t.start()
    time.sleep(0.4)
    started = time.monotonic()
    stop.set()
    t.join(5)
    assert isinstance(out.get("exc"), Cancelled) and time.monotonic() - started < 2


def _no_cli():
    return {"available": False, "path": "", "version": ""}


_no_cli.cache_clear = lambda: None


def test_the_app_uses_it_for_chatgpt_and_signing_out_forgets_it(openai, monkeypatch):
    from studia import appconfig

    appconfig.update({"ai_provider": "chatgpt"})
    monkeypatch.setattr(ai, "codex_cli_info", _no_cli)
    assert not ai.ai_info()["available"]
    _sign_in(openai)
    info = ai.ai_info()
    assert info["available"] and info["label"] == "ChatGPT" and info["can_plans"] and info["can_images"]
    openai.stream_answers = [(200, _sse({"type": "response.output_text.delta", "delta": "OK"}, {"type": "response.completed", "response": {}}))]
    assert ai_setup.test("chatgpt")["ok"] and ai_setup.last_test("codex") == {}
    s = ai.ai_status("chatgpt")
    assert "claude" not in s and "codex" not in s
    s = s["chatgpt"]
    assert s["signed_in"] and s["plan_allowed"] and s["email"] == "student@example.com" and s["models"] == ["gpt-6.1-sol"]
    openai.stream_answers = [(200, _sse({"type": "response.output_text.delta", "delta": "Hi"}, {"type": "response.completed", "response": {}}))]
    assert ai.new_session("ctx").ask([{"type": "text", "text": "q"}])["text"] == "Hi"
    out = chatgpt.sign_out()
    assert out["revoked"] and not chatgpt.signed_in() and not (chatgpt._dir() / "chatgpt.bin").exists()
    revoke = [c[2] for c in openai.calls if c[1].endswith("/revoke")][0]
    assert revoke == {"token": "rt-1", "token_type_hint": "refresh_token", "client_id": "oaiapp_123"}


def test_the_codex_cli_choice_never_uses_the_app_sign_in(openai, monkeypatch):
    from studia import appconfig

    _sign_in(openai)
    appconfig.update({"ai_provider": "codex"})
    monkeypatch.setattr(ai, "codex_cli_info", _no_cli)
    assert not ai.ai_info()["available"]
    with pytest.raises(RuntimeError, match="Codex CLI is not installed"):
        ai.one_shot("x", [{"type": "text", "text": "y"}])
    assert not [c for c in openai.calls if c[1].endswith("/responses")]


def test_an_earlier_chatgpt_choice_moves_to_the_app_sign_in_once(openai):
    from studia import appconfig

    appconfig.save(appconfig.AppConfig(ai_provider="codex"))              # as an earlier version stored it
    _sign_in(openai)
    appconfig.migrate()
    assert appconfig.load().ai_provider == "chatgpt" and appconfig.load().version == 1
    appconfig.update({"ai_provider": "codex"})                            # chosen on purpose afterwards: kept
    appconfig.migrate()
    assert appconfig.load().ai_provider == "codex"


def test_an_earlier_codex_cli_choice_stays(openai, monkeypatch):
    from studia import appconfig

    monkeypatch.setattr(ai, "_codex_candidates", lambda: ["C:/tools/codex.exe"])
    appconfig.save(appconfig.AppConfig(ai_provider="codex"))              # the CLI, never signed in the other way
    appconfig.migrate()
    assert appconfig.load().ai_provider == "codex" and appconfig.load().version == 1


def test_an_earlier_chatgpt_choice_without_the_cli_moves_too(openai, monkeypatch):
    from studia import appconfig

    monkeypatch.setattr(ai, "_codex_candidates", lambda: [])
    appconfig.save(appconfig.AppConfig(ai_provider="codex"))              # chose ChatGPT but never signed in yet
    appconfig.migrate()
    assert appconfig.load().ai_provider == "chatgpt"


def test_rs256_verification():
    msg = b"header.payload"
    k = (N.bit_length() + 7) // 8
    digest_info = bytes.fromhex("3031300d060960864801650304020105000420") + hashlib.sha256(msg).digest()
    em = b"\x00\x01" + b"\xff" * (k - len(digest_info) - 3) + b"\x00" + digest_info
    sig = pow(int.from_bytes(em, "big"), D, N).to_bytes(k, "big")
    assert chatgpt._verify_rs256(msg, sig, N, E)
    assert not chatgpt._verify_rs256(b"header.other", sig, N, E)
    assert not chatgpt._verify_rs256(msg, sig[:-1], N, E)
    assert not chatgpt._verify_rs256(msg, (int.from_bytes(sig, "big") ^ 1).to_bytes(k, "big"), N, E)


def test_requests_that_begin_alike_share_a_cache_key_and_a_refusal_is_remembered(openai, monkeypatch):
    import copy

    _sign_in(openai)
    monkeypatch.setattr(chatgpt, "_turned_down", set())
    post = openai.post
    monkeypatch.setattr(openai, "post", lambda url, data=None, json=None, **kw: post(url, data, copy.deepcopy(json), **kw))

    def ok():
        return (200, _sse({"type": "response.output_text.delta", "delta": "ok"}, {"type": "response.completed", "response": {}}))

    lecture = [{"type": "text", "text": "=== LECTURE TRANSCRIPT ===\n\n[00:00] The lecture."}]
    openai.stream_answers = [ok(), ok(), ok()]
    chatgpt.one_shot("S", lecture + [{"type": "text", "text": "=== TASK ===\n\nStudy notes."}])
    chatgpt.one_shot("S", lecture + [{"type": "text", "text": "=== TASK ===\n\nFlashcards."}])
    chatgpt.one_shot("S", [{"type": "text", "text": "Another lecture."}])
    a, b, c = [x[2] for x in openai.calls if x[1].endswith("/responses")][-3:]
    assert a["prompt_cache_key"] == b["prompt_cache_key"] != c["prompt_cache_key"]
    assert a["prompt_cache_key"].startswith("ieustudia-") and "lecture" not in a["prompt_cache_key"].lower()
    # an account whose API turns the key down: asked again without it, and not sent again
    openai.stream_answers = [(400, {"error": {"code": "unknown_parameter", "param": "prompt_cache_key",
                                              "message": "Unknown parameter: 'prompt_cache_key'."}}), ok(), ok()]
    assert chatgpt.one_shot("S", lecture)["text"] == "ok" and chatgpt.one_shot("S", lecture)["text"] == "ok"
    bodies = [x[2] for x in openai.calls if x[1].endswith("/responses")][-3:]
    assert "prompt_cache_key" in bodies[0] and all("prompt_cache_key" not in x for x in bodies[1:])
    # other refusals are still errors
    openai.stream_answers = [(400, {"error": {"code": "invalid_value", "param": "input", "message": "bad input"}})]
    with pytest.raises(RuntimeError, match="bad input"):
        chatgpt.one_shot("S", lecture)


def test_a_chatgpt_chat_cuts_its_conversation_back_in_steps_and_keeps_one_cache_key():
    sent = []
    s = ai.ChatGPTSession("The lectures.")
    s.request = lambda system, content, *a, **k: sent.append((content[-1]["text"], k)) or {"text": f"A{len(sent)}",
                                                                                            "usage": {}}
    for i in range(1, 18):
        s.ask([{"type": "text", "text": f"Q{i}"}])
    convo = [t.split("=== NEW QUESTION ===")[0] for t, _ in sent]
    assert [c.count("[Student]") for c in convo[11:17]] == [11, 12, 8, 9, 10, 11]   # 12 turns at most, then the last 8
    assert all(c.startswith("=== CONVERSATION SO FAR ===\n\n[Student]\nQ6\n") for c in convo[13:17])  # same beginning
    keys = {k["cache_key"] for _, k in sent}
    assert len(keys) == 1 and keys.pop().startswith("ieustudia-chat-")


# ----------------------------------------------------------------------------- before the browser
def test_a_sign_in_this_pc_cant_decrypt_is_kept_aside_and_signing_in_again_works(openai, monkeypatch):
    """After a Windows password reset, or with a profile copied from another PC, DPAPI can't open chatgpt.bin: it is
    kept aside (not deleted), the set-up says to sign in again, and signing in registers IEUStudia anew."""
    _sign_in(openai)
    real = chatgpt._unprotect

    def lost_key(data):
        raise OSError(-2146893813, "Key not valid for use in specified state.")

    monkeypatch.setattr(chatgpt, "_unprotect", lost_key)
    st = chatgpt.status()
    assert not st["signed_in"] and st["unreadable"] and not st["registered"]
    assert (chatgpt._dir() / chatgpt.UNREADABLE).is_file() and not (chatgpt._dir() / "chatgpt.bin").exists()
    monkeypatch.setattr(chatgpt, "_unprotect", real)
    assert _sign_in(openai)["state"] == "done" and chatgpt.signed_in() and not chatgpt.status()["unreadable"]
    assert openai.authorize["client_id"] == "dynamic_agent_client"     # its client id was in the unreadable file
    # a damaged file (not a sign-in once decrypted): the same
    chatgpt._save(["not", "a", "record"])
    assert not chatgpt.status()["signed_in"] and chatgpt.status()["unreadable"]


def test_an_unreadable_sign_in_says_nothing_once_a_new_one_is_kept(openai, monkeypatch):
    """Signed in again after a Windows password reset, then signed out: the setup claimed the reset again."""
    _sign_in(openai)
    real = chatgpt._unprotect

    def lost_key(data):
        raise OSError(-2146893813, "Key not valid for use in specified state.")

    monkeypatch.setattr(chatgpt, "_unprotect", lost_key)
    assert chatgpt.status()["unreadable"]
    monkeypatch.setattr(chatgpt, "_unprotect", real)
    assert _sign_in(openai)["state"] == "done"
    chatgpt.sign_out()
    st = chatgpt.status()
    assert not st["signed_in"] and not st["unreadable"] and "unreadable" not in chatgpt.details()


def test_a_pc_clock_set_right_counts_at_once(openai, monkeypatch):
    """The warning said the clock was 6 hours ahead until the next answer from OpenAI, after it was set right."""
    real = time.time
    openai.now = real
    openai.claims = {"iat": int(real())}
    monkeypatch.setattr(time, "time", lambda: real() + 6 * 3600)
    assert _sign_in(openai)["state"] == "done" and chatgpt.status()["clock"] == 360
    monkeypatch.setattr(time, "time", real)                 # Set time automatically turned on
    st = chatgpt.status()
    assert st["clock"] == 0 and st["clock_text"] == ""


def test_copy_details_never_holds_the_email(openai):
    _sign_in(openai)
    openai.claims = {"sub": "user-2", "email": "other.student@example.com"}
    assert _sign_in(openai)["code"] == "other_account"
    text = chatgpt.details()
    assert "other_account" in text and "@example.com" not in text and "(email)" in text


def test_a_late_note_never_brings_the_waiting_back(openai):
    """A note that comes after the end (OpenAI out of reach, https tried) would show the spinner again."""
    events, done = [], threading.Event()
    chatgpt.start_sign_in(lambda e: (events.append(e), e["state"] != "waiting" and done.set()),
                          open_browser=_browser(openai))
    assert done.wait(10) and events[-1]["state"] == "done"
    seen = len(events)
    chatgpt._attempt["track"]("https", "late", moves=False, note="https")
    assert len(events) == seen and chatgpt.status()["sign_in"]["state"] == "done"
    assert "note" not in chatgpt.status()["sign_in"]


def test_the_sign_in_page_never_opens_inside_ieustudia():
    """"Sign in inside IEUStudia" is gone: OpenAI's instruction for this sign-in is to open the system browser
    (developers.openai.com/siwc), RFC 8252 says native apps must not use embedded user agents, and Google refuses to
    sign in inside embedded web views. No code, text or translation that leads to it is left."""
    from pathlib import Path

    from studia import api

    assert not any(hasattr(chatgpt, n) for n in ("sign_in_inside", "window_closed", "_close_window"))
    assert not any(hasattr(api.Api, n) for n in ("ai_login_inside", "_signin_window"))
    src = Path(chatgpt.__file__).parent
    for p in [*src.glob("*.py"), *(src / "web").glob("*.js"), *(src / "locales" / "tr").glob("*.json")]:
        text = p.read_text(encoding="utf-8").lower()
        for gone in ("inside ieustudia", "ai-login-inside", "ai_login_inside", "ieustudia içinde giriş",
                     "ieustudia sign-in window", "ieustudia window"):
            assert gone not in text, f"{p.name}: {gone}"


def test_a_keyring_that_isnt_there_right_now_keeps_the_file(openai, monkeypatch):
    """Linux: no keyring at the moment is not a lost sign-in; the file stays where it is."""
    _sign_in(openai)
    monkeypatch.setattr(chatgpt, "WINDOWS", False)
    monkeypatch.setattr(chatgpt, "_unprotect", lambda data: (_ for _ in ()).throw(OSError("No keyring is available")))
    assert not chatgpt.status()["signed_in"]
    assert (chatgpt._dir() / "chatgpt.bin").is_file() and not (chatgpt._dir() / chatgpt.UNREADABLE).exists()


def test_the_installations_id_not_being_saved_doesnt_stop_signing_in(openai, monkeypatch):
    real_write = chatgpt.Path.write_text

    def guarded(self, *a, **kw):
        if self.name == "chatgpt-host.json":
            raise PermissionError(13, "Access is denied", str(self))
        return real_write(self, *a, **kw)

    monkeypatch.setattr(chatgpt.Path, "write_text", guarded)
    assert _sign_in(openai)["state"] == "done"
    hid = openai.authorize["ext_agent_host_id"]
    assert hid.startswith("urn:uuid:") and chatgpt.host_id() == hid      # the same one for as long as it runs


def test_a_sign_in_that_cant_be_kept_is_said_plainly(openai, monkeypatch):
    def full(rec):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(chatgpt, "_save", full)
    e = _sign_in(openai)
    assert e["state"] == "error" and e["code"] == "save" and "disk is full" in e["error"]


def test_each_profile_has_its_own_chatgpt_sign_in(openai):
    """A shared PC: the second student doesn't silently use the first student's plan."""
    _sign_in(openai)
    host = chatgpt.host_id()
    paths.set_profile_dir("profiles/p-12345678")
    try:
        assert not chatgpt.signed_in() and not chatgpt.status()["registered"]
        assert chatgpt.host_id() == host                                  # one installation
        assert _sign_in(openai)["state"] == "done" and chatgpt.signed_in()
        assert openai.authorize["client_id"] == "dynamic_agent_client"   # registered for this student
    finally:
        paths.set_profile_dir("")
    assert chatgpt.signed_in() and chatgpt.status()["email"] == "student@example.com"


# ----------------------------------------------------------------------------- at OpenAI, and coming back
@pytest.mark.parametrize("params,code,said", [
    ({"error": "access_denied"}, "denied", "choose Allow"),
    ({"error": "access_denied", "error_description": "The resource owner or authorization server denied the request."},
     "denied", "choose Allow"),
    ({"error": "access_denied", "error_description": "Your workspace does not allow third-party apps"}, "workspace",
     "Use another account or workspace"),
    ({"error": "server_error", "error_description": "upstream timeout"}, "service", "few minutes"),
    ({"error": "temporarily_unavailable"}, "service", "few minutes"),
    ({"error": "invalid_scope"}, "app", "newer IEUStudia"),
    ({"error": "unauthorized_client"}, "client", "on OpenAI's side"),       # (registering: nothing kept to try again)
    ({"error": "invalid_client"}, "client", "try again in a few hours"),
    ({"error": "login_required"}, "again", "to the end"),
    ({"error": "something_new", "error_description": "Something odd happened"}, "other", "Something odd happened"),
])
def test_every_error_openai_sends_back_is_said_plainly(openai, params, code, said):
    e = _sign_in(openai, browser={"params": params})
    assert e["state"] == "error" and e["code"] == code and said in e["error"] and not chatgpt.signed_in()


def test_a_registration_openai_turns_down_once_is_kept_and_dropped_only_the_second_time(openai, monkeypatch):
    """OpenAI asks apps not to erase credentials over a temporary failure, and its "This app is unavailable" passed
    within hours for another app on this sign-in: one refusal keeps IEUStudia's registration (the next sign-in tries
    it again), only a second one in a row drops it (the next sign-in registers anew). Each says what happened."""
    _sign_in(openai)
    assert chatgpt.status()["registered"] and chatgpt.signed_in()
    e = _sign_in(openai, browser={"params": {"error": "invalid_client"}})
    assert e["code"] == "client" and "IEUStudia kept it" in e["error"] and "Continue with ChatGPT" in e["error"]
    assert chatgpt.status()["registered"] and chatgpt.signed_in()            # kept, its sign-in too
    assert chatgpt._load()["client_refused"] == 1 and "turned down once by OpenAI (kept)" in chatgpt.details()
    # the next try uses it again, and it works: the refusal is forgotten
    assert _sign_in(openai)["state"] == "done" and openai.authorize["client_id"] == "oaiapp_123"
    assert "client_refused" not in chatgpt._load() and not chatgpt._refusals
    # turned down after that: a first refusal again, kept; noted on disk, so a restart doesn't forget it
    e = _sign_in(openai, browser={"params": {"error": "unauthorized_client"}})
    assert "IEUStudia kept it" in e["error"] and chatgpt.status()["registered"]
    chatgpt._refusals.clear()                                                 # (IEUStudia started again)
    # a second refusal in a row, now at the code exchange: dropped and said; the next sign-in registers anew
    openai.token_answer = (401, {"error": "invalid_client", "error_description": "Client authentication failed"})
    e = _sign_in(openai)
    assert openai.authorize["client_id"] == "oaiapp_123"                     # (the kept one was tried once more)
    assert e["code"] == "client" and "connects anew next time" in e["error"]
    assert not chatgpt.status()["registered"] and not chatgpt._refusals
    openai.token_answer = None
    assert _sign_in(openai)["state"] == "done" and openai.authorize["client_id"] == "dynamic_agent_client"
    # a renewal that works clears a refusal too
    _sign_in(openai, browser={"params": {"error": "invalid_client"}})
    rec = chatgpt._load()
    rec["expires_at"] = 0
    chatgpt._save(rec)
    assert chatgpt.access_token() == "at-2" and "client_refused" not in chatgpt._load() and not chatgpt._refusals
    # the refusal can't be noted on disk (a full disk): it still counts for as long as IEUStudia runs
    real_save = chatgpt._save
    monkeypatch.setattr(chatgpt, "_save", lambda r: (_ for _ in ()).throw(OSError(28, "No space left on device"))
                        if "client_refused" in r else real_save(r))
    assert "IEUStudia kept it" in _sign_in(openai, browser={"params": {"error": "invalid_client"}})["error"]
    assert "connects anew" in _sign_in(openai, browser={"params": {"error": "invalid_client"}})["error"]
    assert not chatgpt.status()["registered"]


@pytest.mark.parametrize("answer,code,said", [
    (requests.ConnectionError("HTTPSConnectionPool(host='auth.openai.com', port=443): Max retries exceeded (Caused by "
                              "NameResolutionError(\"Failed to resolve 'auth.openai.com' ([Errno 11001] getaddrinfo "
                              "failed)\"))"), "network", "seems to be offline"),
    (requests.ConnectionError("[WinError 10061] No connection could be made because the target machine actively "
                              "refused it"), "network", "firewall, VPN or security program"),
    (requests.exceptions.SSLError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: certificate has "
                                  "expired"), "network", "date or time is wrong"),
    (requests.exceptions.SSLError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local "
                                  "issuer certificate"), "network", "Antivirus HTTPS scanning"),
    (requests.ReadTimeout("Read timed out. (read timeout=30)"), "network", "didn't answer in time"),
    (requests.exceptions.ProxyError("Unable to connect to proxy"), "network", "proxy"),
    ((429, {"error": "rate_limit_exceeded"}), "service", "too many sign-ins"),
    ((403, {"error": "unsupported_country_region_territory"}), "blocked", "VPN or proxy"),
    ((502, {}), "service", "HTTP 502"),
    ((400, {"error": "invalid_grant", "error_description": "Invalid authorization code"}), "expired_code",
     "already used or has expired"),
    ((400, {"error": "invalid_request", "error_description": "Something specific"}), "other", "Something specific"),
    ((401, {"error": "invalid_client"}), "client", "on OpenAI's side"),       # (registering: nothing kept)
])
def test_a_failed_code_exchange_says_why_in_plain_words(openai, answer, code, said):
    openai.token_answer = answer
    e = _sign_in(openai)
    assert e["state"] == "error" and e["code"] == code and said in e["error"]
    assert "HTTPSConnectionPool" not in e["error"] and "Errno" not in e["error"] and not chatgpt.signed_in()


def test_https_asked_of_the_listener_is_survived_logged_and_said(openai, caplog):
    """A browser's HTTPS-only mode, or a security program, turns IEUStudia's http://127.0.0.1 link into https://:
    the TLS handshake reaches the plain listener. It is noticed (not 15 s of a stuck connection), logged and said,
    and the listener keeps working."""
    events = []
    r = chatgpt.start_sign_in(events.append, open_browser=lambda link: True)
    port = urllib.parse.urlsplit(r["link"]).port
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    started = time.monotonic()
    with pytest.raises(OSError):                             # ssl.SSLError is one
        with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
            ctx.wrap_socket(s, server_hostname="127.0.0.1")
    assert time.monotonic() - started < 4
    assert _wait_for(lambda: chatgpt.status()["sign_in"].get("note") == "https")
    assert any(e.get("note") == "https" for e in events) and "https://" in caplog.text
    assert _follow(r["link"])[0] == 302                      # still answering


def test_the_address_pasted_from_the_browser_finishes_the_sign_in(openai):
    """Something blocks the way back to 127.0.0.1: the browser's error page still holds OpenAI's answer in its
    address bar, and pasting it finishes the sign-in. Anything else is refused in plain words."""
    r = chatgpt.start_sign_in(open_browser=lambda link: True)
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(_follow(r["link"])[1]).query))
    openai.nonce = q["nonce"]
    made_up = chatgpt.paste_address("https://www.google.com/search?q=chatgpt")
    assert made_up["state"] == "refused" and "isn't the address" in made_up["error"]
    assert chatgpt.paste_address(r["page"])["state"] == "refused"          # OpenAI's own page: not the way back
    wrong = chatgpt.paste_address(f"{q['redirect_uri']}?code=c0de&state=nope&client_id=oaiapp_123")
    assert wrong["state"] == "refused" and "earlier sign-in" in wrong["error"]
    # (said while this sign-in waits, when its row has Open the page again and no Continue with ChatGPT)
    assert "Open the page again" in wrong["error"] and "Continue with ChatGPT" not in wrong["error"]
    assert chatgpt.status()["sign_in"]["state"] == "waiting" and not chatgpt.signed_in()
    # as the browser shows it on its error page (made https:// by an HTTPS-only setting), with spaces around it
    shown = q["redirect_uri"].replace("http://", "https://") + f"?code=c0de&state={q['state']}&client_id=oaiapp_123"
    out = chatgpt.paste_address(f"  {shown}\n")
    assert out == {"state": "done", "error": "", "code": ""} and chatgpt.signed_in()
    # the same answer by another way too (the browser got through after all): the same outcome, traded once
    again = chatgpt._attempt["deliver"](dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(shown).query)), "browser")
    assert again["state"] == "done" and len([c for c in openai.calls if c[0] == "POST" and
                                             c[2].get("grant_type") == "authorization_code"]) == 1
    assert _wait_for(lambda: chatgpt.status()["sign_in"]["state"] == "done")
    late = chatgpt.paste_address(shown)                  # pasted again once it is through: done, calmly
    assert late == {"state": "done", "error": "", "code": ""}
    # a sign-in that was stopped: nothing waits for its address
    r = chatgpt.start_sign_in(open_browser=lambda link: True)
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(_follow(r["link"])[1]).query))
    chatgpt.cancel_sign_in()
    stopped = chatgpt.paste_address(f"{q['redirect_uri']}?code=c0de&state={q['state']}")
    assert stopped["state"] == "refused" and "No sign-in is waiting" in stopped["error"]


def _pasted(form, address):
    # (a line break in its query too: a break only in the path, which isn't read, proved nothing; one in the middle
    # of the state and one between two parameters, as an address wrapped over lines comes)
    q, cut = address.index("&scope="), address.index("state=") + 12
    return {"as is": address,
            "no http://": address.split("://", 1)[1],
            "localhost": address.replace("127.0.0.1", "localhost"),
            "spaces and line breaks": f"\n  {address[:30]}\r\n {address[30:q]} \n{address[q:cut]}\r\n{address[cut:]}  \n\t",
            "in quotes, ending a sentence": f"“{address}”.",
            "&amp;": address.replace("&", "&amp;")}[form]


@pytest.mark.parametrize("form", ["as is", "no http://", "localhost", "spaces and line breaks",
                                  "in quotes, ending a sentence", "&amp;"])
def test_a_pasted_address_finishes_the_sign_in_however_it_was_copied(openai, form):
    r = chatgpt.start_sign_in(open_browser=lambda link: True)
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(_follow(r["link"])[1]).query))
    openai.nonce = q["nonce"]
    address = f"{q['redirect_uri']}?code=c0de&scope=openid+email&state={q['state']}&client_id=oaiapp_123"
    assert chatgpt.paste_address(_pasted(form, address)) == {"state": "done", "error": "", "code": ""}
    assert chatgpt.signed_in()
    exchange = next(c for c in openai.calls if c[0] == "POST" and c[2].get("grant_type") == "authorization_code")[2]
    assert exchange["redirect_uri"] == q["redirect_uri"]      # the sign-in's own way back, whatever was pasted


def test_the_browser_and_a_pasted_address_both_finish_calmly_whichever_comes_first(openai):
    """The browser's way back keeps waiting while the student pastes: the first one wins and the other gets the same
    outcome. Never a "failed" for the way that came second, and an address cut short is said as cut short."""
    events, done = [], threading.Event()
    r = chatgpt.start_sign_in(lambda e: (events.append(e), e["state"] != "waiting" and done.set()),
                              open_browser=lambda link: True)
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(_follow(r["link"])[1]).query))
    openai.nonce = q["nonce"]
    address = f"{q['redirect_uri']}?code=c0de&state={q['state']}&client_id=oaiapp_123"
    cut = chatgpt.paste_address(address.split("&state=")[0])
    assert cut["state"] == "refused" and "cut short" in cut["error"] and chatgpt.status()["sign_in"]["state"] == "waiting"
    page = urllib.request.urlopen(address, timeout=5).read().decode("utf-8")   # the browser came back first
    assert "signed in" in page and done.wait(10) and events[-1]["state"] == "done"
    assert chatgpt.paste_address(f"  {address}\n") == {"state": "done", "error": "", "code": ""}
    assert [e["state"] for e in events if e["state"] != "waiting"] == ["done"]
    assert len([c for c in openai.calls if c[0] == "POST" and c[2].get("grant_type") == "authorization_code"]) == 1


# ----------------------------------------------------------------------------- what the student sees while waiting
def test_openai_out_of_reach_from_ieustudia_is_said_while_waiting(openai):
    """The browser may reach OpenAI while IEUStudia can't (a firewall rule for one program, a proxy only the browser
    uses): the sign-in couldn't finish, so it is said at once, with the cause."""
    for reach, said in ((requests.ConnectionError("getaddrinfo failed"), "seems to be offline"),
                        (requests.exceptions.SSLError("certificate verify failed: unable to get local issuer "
                                                      "certificate"), "Antivirus HTTPS scanning"),
                        (403, "VPN or proxy"), (503, "HTTP 503")):
        openai.reach, events = reach, []
        chatgpt.start_sign_in(events.append, open_browser=lambda link: True)
        assert _wait_for(lambda: any("reach" in e for e in events))
        got = next(e["reach"] for e in events if "reach" in e)
        assert got["ok"] is False and said in got["why"] and chatgpt.status()["sign_in"]["reach"] == got
    openai.reach, events = None, []
    chatgpt.start_sign_in(events.append, open_browser=lambda link: True)
    assert _wait_for(lambda: any("reach" in e for e in events))
    assert next(e["reach"] for e in events if "reach" in e) == {"ok": True, "why": ""}


def test_waiting_says_each_step_and_copy_details_holds_no_secrets(openai):
    events, done = [], threading.Event()
    chatgpt.start_sign_in(lambda e: (events.append(e), e["state"] != "waiting" and done.set()),
                          open_browser=_browser(openai))
    assert done.wait(10) and events[-1]["state"] == "done"
    steps = [e["step"] for e in events if "step" in e]
    assert steps[:2] == ["listening", "opening"] and steps[-3:] == ["returned", "exchanging", "verifying"]
    assert "opened" in steps
    text = chatgpt.details()
    q = openai.authorize
    for secret in (q["state"], q["nonce"], q["code_challenge"], "c0de", "rt-1", "at-1", "student@example.com",
                   chatgpt.AUTHORIZE_URL):
        assert secret not in text
    for said in ("IEUStudia", "listening", "opened", "returned", "exchanging", "verifying", "done", "Now: done"):
        assert said in text
    assert chatgpt.details().count("\n") >= 6


def test_a_pc_clock_hours_off_still_signs_in_and_is_said(openai, monkeypatch):
    """A wrong time zone "fixed" by typing the time leaves the clock hours ahead: the browser and HTTPS still work,
    and the sign-in must too (it used to say the token had expired, every time). The set-up says how far off it is."""
    real = time.time
    openai.now = real
    openai.claims = {"iat": int(real())}
    monkeypatch.setattr(time, "time", lambda: real() + 6 * 3600)
    assert _sign_in(openai)["state"] == "done" and chatgpt.signed_in()
    st = chatgpt.status()
    assert st["clock"] == 360 and "6 hours ahead" in st["clock_text"] and "automatically" in st["clock_text"]
    # OpenAI's Date header says it too (before any token): a clock behind
    chatgpt._clock.clear()
    monkeypatch.setattr(time, "time", lambda: real() - 3 * 86400)
    openai.date = email.utils.formatdate(real(), usegmt=True)
    events = []
    chatgpt.start_sign_in(events.append, open_browser=lambda link: True)
    assert _wait_for(lambda: any("clock" in e for e in events))
    got = next(e for e in events if "clock" in e)
    assert got["clock"] == -3 * 1440 and "3 days behind" in got["clock_text"]


def test_a_secure_connection_failing_on_a_wrong_date_says_so(openai):
    _sign_in(openai)
    openai.stream_answers = [(0, requests.exceptions.SSLError(
        "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: certificate is not yet valid"))]
    with pytest.raises(RuntimeError, match="date or time is wrong"):
        chatgpt.one_shot("x", [{"type": "text", "text": "y"}])


def test_a_refused_access_token_is_renewed_once_then_the_answer_comes(openai):
    """A clock set back after the token came makes an expired one look valid: OpenAI's 401 renews it once instead of
    telling the student to sign in again; a second refusal is said, never a loop."""
    _sign_in(openai)

    def ok():
        return (200, _sse({"type": "response.output_text.delta", "delta": "OK"}, {"type": "response.completed",
                                                                                    "response": {}}))

    def renewals():
        return len([c for c in openai.calls if c[0] == "POST" and c[2].get("grant_type") == "refresh_token"])

    openai.stream_answers = [(401, {"error": {"message": "Token expired"}}), ok()]
    assert chatgpt.one_shot("x", [{"type": "text", "text": "y"}])["text"] == "OK" and renewals() == 1
    openai.stream_answers = [(401, {}), (401, {})]
    with pytest.raises(RuntimeError, match="did not accept the sign-in"):
        chatgpt.one_shot("x", [{"type": "text", "text": "y"}])
    assert renewals() == 2
    # the plan's own refusals are never "renewed"
    openai.stream_answers = [(401, {"error": {"code": "subscription_sharing_user_not_eligible"}})]
    with pytest.raises(chatgpt.NotEligible):
        chatgpt.one_shot("x", [{"type": "text", "text": "y"}])
    assert renewals() == 2
    # the model list too
    chatgpt._models_cache_clear()
    openai.models_answers = [(401, {"error": {"message": "Token expired"}})]
    assert [m["slug"] for m in chatgpt.models()] == ["gpt-6.1-sol"] and renewals() == 3


def test_another_account_is_said_and_use_another_account_starts_afresh(openai):
    _sign_in(openai)
    openai.claims = {"sub": "user-2", "email": "other@example.com"}
    e = _sign_in(openai)
    assert e["code"] == "other_account" and "other@example.com" in e["error"]
    assert "Use another account or workspace" in e["error"] and chatgpt.status()["email"] == "student@example.com"
    chatgpt.sign_out()
    e = _sign_in(openai, fresh=True)
    q = openai.authorize
    assert q["client_id"] == "dynamic_agent_client" and "login_hint" not in q and "id_token_hint" not in q
    assert e["state"] == "done" and chatgpt.status()["email"] == "other@example.com"
