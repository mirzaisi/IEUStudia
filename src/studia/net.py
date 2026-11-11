"""HTTP helpers: a tuned requests session, HTML form parsing and cookie (de)serialisation."""

from __future__ import annotations

import dataclasses as dc
import errno
import html
import logging
import os
import ssl
import sys
import threading
import time
import weakref
from html.parser import HTMLParser
from typing import Any, Callable, Optional
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .i18n import t

log = logging.getLogger("studia")
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36")
DEFAULT_TIMEOUT = (6, 45)       # a server that doesn't answer in 6 s (a lost SYN is resent at 3 s) is down: retried
RETRY_AFTER_MAX_S = 10          # a "come back in an hour" (503 Retry-After) must not hold the sync for an hour


class NetworkError(requests.ConnectionError):
    """A connection problem, with a message that can be shown to the user. ``host``: the site that couldn't be
    reached, when known (a course file's link to another site is that file's problem, not IEULearn's)."""

    host = ""


def _network_error(message: str, host: str) -> NetworkError:
    err = NetworkError(message)
    err.host = host
    return err


def use_windows_certificates() -> None:
    """HTTPS trusts the certificates Windows trusts, as the browser does. An antivirus that scans HTTPS, or a company
    proxy, adds its own to Windows; Python's built-in list doesn't have them, so every connection would fail."""
    try:
        import truststore

        truststore.inject_into_ssl()
    except Exception:  # noqa: BLE001 - the built-in list still works everywhere else
        log.debug("Windows certificates not used", exc_info=True)


def _host(exc: BaseException, url: str = "") -> str:
    req = getattr(exc, "request", None)
    return urlparse(url or getattr(req, "url", "") or "").hostname or ""


def plain_error(exc: BaseException, url: str = "") -> str:
    """What went wrong, in words for anyone: the connection, a full disk, memory, a file Windows would not let us
    write. Other errors keep their own message (most are already written for people). Each message is a whole
    sentence in both forms (with and without the name), never a sentence with a word put in: Turkish needs that."""
    if isinstance(exc, NetworkError):
        return str(exc)
    host = _host(exc, url)
    if isinstance(exc, requests.exceptions.SSLError):          # before ConnectionError: it is one
        if cert_out_of_date(exc):                              # a wrong date on this PC, not an antivirus
            return clock_tls_text(host)
        return (t("A secure connection to {host} was blocked. Antivirus web or HTTPS scanning, or a Wi-Fi sign-in page, "
                  "can do this: open any website in your browser, then try again.", host=host) if host else
                t("A secure connection to the server was blocked. Antivirus web or HTTPS scanning, or a Wi-Fi sign-in "
                  "page, can do this: open any website in your browser, then try again."))
    if isinstance(exc, requests.exceptions.ProxyError) or _socks(exc):        # (one too)
        return proxy_refused()
    if isinstance(exc, requests.Timeout) or (isinstance(exc, requests.ConnectionError) and read_timed_out(exc)):
        return (t("{host} took too long to answer. Check your internet connection and try again.", host=host) if host
                else t("The server took too long to answer. Check your internet connection and try again."))
    if isinstance(exc, requests.ConnectionError):
        return (t("Can't reach {host}. Check your internet connection (on campus Wi-Fi, make sure you are signed in to "
                  "it), then try again.", host=host) if host else
                t("Can't reach the server. Check your internet connection (on campus Wi-Fi, make sure you are signed in "
                  "to it), then try again."))
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        code = exc.response.status_code
        return (t("{host} answered with an error ({code}). Try again in a while.", host=host, code=code) if host
                else t("The server answered with an error ({code}). Try again in a while.", code=code))
    if isinstance(exc, MemoryError):
        return t("Not enough memory. Close other programs and try again.")
    if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
        return t("Your disk is full. Free up some space, then try again.")
    if isinstance(exc, PermissionError):
        name = os.path.basename(str(exc.filename or ""))
        if sys.platform != "win32":
            return (t("IEUStudia isn't allowed to use {name}: check that your user account may read and write it.",
                      name=name) if name else
                    t("IEUStudia isn't allowed to use a file: check that your user account may read and write it."))
        return (t("Windows did not let IEUStudia use {name}: it may be open in another program (or syncing), or your "
                  "antivirus or Windows' ransomware protection blocked it.", name=name) if name else
                t("Windows did not let IEUStudia use a file: it may be open in another program (or syncing), or your "
                  "antivirus or Windows' ransomware protection blocked it."))
    long_path = str(getattr(exc, "filename", "") or "")       # Windows' path limit (long paths off): 206, or "not found"
    if isinstance(exc, OSError) and (getattr(exc, "winerror", None) == 206 or exc.errno == errno.ENAMETOOLONG or (
            len(long_path) >= 260 and getattr(exc, "winerror", None) in (2, 3))):
        if sys.platform != "win32":                # (there: one name longer than 255 bytes, not Windows' path limit)
            return (t("{path} is too long a name for this computer. Choose a shorter folder in Settings → Files.",
                      path=long_path) if long_path else
                    t("A name is too long for this computer. Choose a shorter folder in Settings → Files."))
        return (t("{path} is too long for Windows (a whole path can have at most 259 characters). Choose a shorter "
                  "folder in Settings → Files, for example one right in Documents.", path=long_path) if long_path else
                t("A path is too long for Windows (a whole path can have at most 259 characters). Choose a shorter "
                  "folder in Settings → Files, for example one right in Documents."))
    if getattr(exc, "winerror", None) in (3, 21, 53, 67):        # path, drive, network path or name not found
        where = str(getattr(exc, "filename", "") or "")
        return (t("{where} can't be reached: is the drive (or the network) connected, and is the folder still there?",
                  where=where) if where else
                t("A folder can't be reached: is the drive (or the network) connected, and is the folder still there?"))
    if isinstance(exc, FileNotFoundError):
        name = os.path.basename(str(exc.filename or ""))
        return (t("{name} isn't there any more: was it moved or deleted?", name=name) if name else
                t("A file isn't there any more: was it moved or deleted?"))
    text = str(exc) or exc.__class__.__name__
    return text if text.endswith((".", "!", "?")) else text + "."


# A certificate's dates: a PC whose date is wrong (months or years off: a flat clock battery, a date typed by hand)
# fails every secure connection on them. Windows says so with a code, and in its own language (Turkish words on a
# Turkish Windows); OpenSSL, Node.js (Claude Code) and Rust (the Codex CLI) say it in English.
CERT_DATE_CODES = (0x800B0101, 9, 10)     # CERT_E_EXPIRED (Windows: expired or not valid yet); OpenSSL's 9 and 10
CERT_DATE_SAID = ("not within its validity period", "certificate has expired", "certificate is not yet valid",
                  "cert_has_expired", "cert_not_yet_valid", "certificate expired", "certificate not valid yet",
                  "notvalidyet", "date_invalid")


def cert_date_said(text: str) -> bool:
    """A program's words (Claude Code, the Codex CLI) say a certificate's dates failed."""
    low = (text or "").lower()
    return any(w in low for w in CERT_DATE_SAID)


def _chain(exc: BaseException) -> list[BaseException]:
    """The error and those behind it (requests → urllib3 → ssl: as reason, cause, context or argument)."""
    seen: dict[int, BaseException] = {}
    todo: list[Any] = [exc]
    while todo and len(seen) < 30:
        e = todo.pop()
        if not isinstance(e, BaseException) or id(e) in seen:
            continue
        seen[id(e)] = e
        todo += [getattr(e, "reason", None), e.__cause__, e.__context__, *getattr(e, "args", ())]
    return list(seen.values())


def cert_out_of_date(exc: BaseException) -> bool:
    """A secure connection that failed on a certificate's dates: what a wrong date on this PC causes. Windows' code
    is looked for all along the error's chain, so a Turkish Windows is understood too."""
    if any(getattr(e, "verify_code", None) in CERT_DATE_CODES for e in _chain(exc)):
        return True
    return cert_date_said(str(exc)) or any(w in str(exc).lower() for w in ("validity period", "has expired",
                                                                             "not yet valid"))


def cert_refused(exc: BaseException) -> bool:
    """The other side's certificate wasn't accepted (an antivirus or a network changing secure connections, a Wi-Fi
    sign-in page): not a connection that broke off halfway."""
    return (any(isinstance(e, ssl.SSLCertVerificationError) for e in _chain(exc))
            or "certificate verify failed" in str(exc).lower() or cert_out_of_date(exc))


def clock_tls_text(host: str = "") -> str:
    """Why a secure connection failed on a certificate's dates, with what this PC's clock says and how to set it."""
    now = time.strftime("%Y-%m-%d %H:%M")
    if sys.platform != "win32":
        return (t("A secure connection to {host} failed because a certificate looks out of date. That happens when this "
                  "computer's date or time is wrong (it says {now}): set the time automatically in your system "
                  "settings (Date & Time), then try again. If the date is right, a security program or the network "
                  "is changing secure connections.", host=host, now=now) if host else
                t("A secure connection failed because a certificate looks out of date. That happens when this "
                  "computer's date or time is wrong (it says {now}): set the time automatically in your system "
                  "settings (Date & Time), then try again. If the date is right, a security program or the network "
                  "is changing secure connections.", now=now))
    return (t("A secure connection to {host} failed because a certificate looks out of date. That happens when this PC's "
              "date or time is wrong (it says {now}): turn on Set time automatically in Settings → Time & language → "
              "Date & time, then try again. If the date is right, a security program or the network is changing "
              "secure connections.", host=host, now=now) if host else
            t("A secure connection failed because a certificate looks out of date. That happens when this PC's date or "
              "time is wrong (it says {now}): turn on Set time automatically in Settings → Time & language → Date & "
              "time, then try again. If the date is right, a security program or the network is changing secure "
              "connections.", now=now))


def proxy_refused() -> str:
    """A proxy server set on this PC (Windows' settings, or HTTPS_PROXY) refused the connection or isn't there."""
    if sys.platform != "win32":
        return t("The proxy server set on this computer didn't let IEUStudia through. If you don't use a proxy, remove "
                 "it from your network settings (or the HTTPS_PROXY variable); otherwise check it with whoever set it up.")
    return t("The proxy server set on this PC didn't let IEUStudia through. If you don't use a proxy, turn it off in "
             "Windows Settings → Network & internet → Proxy; otherwise check it with whoever set it up.")


def _socks(exc: BaseException) -> bool:
    """A SOCKS proxy is set on this PC (ALL_PROXY or HTTPS_PROXY "socks5://…", or Windows' Socks proxy setting), which
    IEUStudia can't go through: requests says "Missing dependencies for SOCKS support", for every connection."""
    return isinstance(exc, requests.exceptions.InvalidSchema) and "socks" in str(exc).lower()


def _drop_stale_ca_bundles() -> None:
    """REQUESTS_CA_BUNDLE or CURL_CA_BUNDLE left on this PC by a program since removed, naming a file that isn't there:
    every connection failed with "Could not find a suitable TLS CA certificate bundle". Such a variable is left out
    (one naming a file that is there, a company's certificates, is used as before)."""
    for name in ("REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
        value = os.environ.get(name, "")
        if value and not os.path.exists(value):
            os.environ.pop(name, None)
            log.info("%s names a file that isn't there (%s): left out", name, value)


class _TimeoutSession(requests.Session):
    def request(self, method, url, **kwargs):  # type: ignore[override]
        kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
        try:
            return super().request(method, url, **kwargs)
        except requests.exceptions.InvalidSchema as exc:
            if not _socks(exc):
                raise
            raise _network_error(proxy_refused(), urlparse(url).hostname or url) from exc
        except (requests.ConnectionError, requests.Timeout) as exc:
            # the site that failed: a redirect's target, not always the address asked for
            failed = getattr(getattr(exc, "request", None), "url", "") or url
            host = urlparse(failed).hostname or urlparse(url).hostname or url
            if isinstance(exc, requests.exceptions.SSLError):
                raise _network_error(plain_error(exc, failed), host) from exc
            if isinstance(exc, requests.exceptions.ProxyError):
                raise _network_error(proxy_refused(), host) from exc
            if read_timed_out(exc):                             # it took the connection, then didn't answer
                raise _network_error(t("{host} took too long to answer: it may be busy, or the connection is weak. "
                                       "Try again in a few minutes.", host=host), host) from exc
            raise _network_error(t("Can't reach {host}. Check your internet connection.", host=host), host) from exc


def read_timed_out(exc: BaseException) -> bool:
    """The server took the connection, then didn't answer in time: requests' ReadTimeout, or (for a GET the adapter
    tried again) the ConnectionError that urllib3's retries end in, whose reason is the read that timed out. A slow
    IEULearn was told as "Can't reach … Check your internet connection" that way."""
    from urllib3.exceptions import MaxRetryError, ReadTimeoutError

    if isinstance(exc, requests.exceptions.ReadTimeout):
        return True
    reason = exc.args[0] if exc.args else None
    if isinstance(reason, MaxRetryError):
        reason = reason.reason
    return isinstance(reason, ReadTimeoutError)


# ----------------------------------------------------------------------------- TLS contexts
# requests hands urllib3 the path of certifi's bundle for every connection, and urllib3 then builds a new TLS context
# and reads the bundle into it: about 0.4 s of CPU per new connection (OpenSSL reads the file 2 bytes at a time),
# 10 s for one check of the tray helper. Each session now gets one context, filled from the bundle read once into
# memory (about 80 ms). One per session and not one for all: truststore holds a context's lock through a whole
# handshake and Windows' check of the certificate, so a server that stalls would hold up everyone else's connections.
# Trust stays as it was: Windows decides first (truststore, once use_windows_certificates has run), the bundle is
# its fallback. Filling a context holds Python's lock (the GIL) all along, 30-80 ms in which the window can't answer,
# and a full sync makes dozens of sessions: a session gone leaves its context to the next one (_spare).
_ours: "weakref.WeakSet[ssl.SSLContext]" = weakref.WeakSet()
_ca_lock = threading.Lock()
_tls_classes: dict[type, type] = {}
_ca_pem = ""
_spare: list[ssl.SSLContext] = []         # contexts of sessions gone (no lock: given back from a finalizer)
SPARE_MAX = 8


def _shared_class(base: type) -> type:
    cls = _tls_classes.get(base)
    if cls is None:
        class SharedContext(base):  # type: ignore[valid-type, misc]
            """urllib3 sets ALPN again for every connection, and truststore passes that on without its lock while
            another thread of the session may be in a handshake on the same context: set once, it stays as it is."""
            _alpn: Optional[list[str]] = None

            def set_alpn_protocols(self, alpn_protocols):  # type: ignore[no-untyped-def]
                protocols = list(alpn_protocols)
                if protocols != self._alpn:
                    super().set_alpn_protocols(protocols)
                    self._alpn = protocols

        cls = _tls_classes[base] = SharedContext
    return cls


def tls_context() -> ssl.SSLContext:
    """A TLS context for one session: what urllib3 makes for one connection, with certifi's bundle from memory."""
    global _ca_pem
    import certifi
    import urllib3.util.ssl_ as u3

    with _ca_lock:
        if not _ca_pem:
            with open(certifi.where(), encoding="ascii") as f:
                _ca_pem = f.read()
    ref = u3.create_urllib3_context()
    ctx = _shared_class(u3.SSLContext)(ssl.PROTOCOL_TLS_CLIENT)
    for name in ("options", "minimum_version", "maximum_version", "verify_flags", "post_handshake_auth",
                 "verify_mode", "check_hostname", "hostname_checks_common_name"):
        setattr(ctx, name, getattr(ref, name))
    ctx.load_verify_locations(cadata=_ca_pem)    # from memory: 80 ms instead of reading the file in 2-byte bits
    ctx.set_alpn_protocols(u3.ALPN_PROTOCOLS)
    _ours.add(ctx)
    return ctx


def _session_context() -> ssl.SSLContext:
    """A spare context (no session uses it any more) or a new one. Only one of the kind tls_context makes now: one
    made before truststore came in doesn't work after."""
    import urllib3.util.ssl_ as u3

    kind = _shared_class(u3.SSLContext)
    while True:
        try:
            ctx = _spare.pop()
        except IndexError:
            return tls_context()
        if type(ctx) is kind:
            return ctx


def _give_back(ctx: ssl.SSLContext) -> None:
    if len(_spare) < SPARE_MAX:
        _spare.append(ctx)


class _Adapter(HTTPAdapter):
    """Connects through the session's own TLS context. Only for the usual case (verify=True, no client certificate,
    no proxy); anything else goes requests' own way. IEUStudia never turns verification off: these contexts always
    require a valid certificate for the host."""

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):  # type: ignore[no-untyped-def]
        if "ssl_context" not in pool_kwargs:
            ctx = pool_kwargs["ssl_context"] = _session_context()
            weakref.finalize(self, _give_back, ctx)      # still one per session: never two sessions' at once
        super().init_poolmanager(connections, maxsize, block=block, **pool_kwargs)

    def build_connection_pool_key_attributes(self, request, verify, cert=None):  # type: ignore[no-untyped-def]
        host_params, pool_kwargs = super().build_connection_pool_key_attributes(request, verify, cert)
        if verify is not True or cert:
            pool_kwargs["ssl_context"] = None      # (None takes it out of the pool's settings)
        return host_params, pool_kwargs

    def cert_verify(self, conn, url, verify, cert):  # type: ignore[no-untyped-def]
        import urllib3.util.ssl_ as u3

        kw = getattr(conn, "conn_kw", None) or {}
        ours = kw.get("ssl_context") in _ours              # (each connection of the pool gets it)
        if ours and verify is True and not cert and url.lower().startswith("https"):
            if not isinstance(kw["ssl_context"], u3.SSLContext):
                # made before use_windows_certificates: once truststore is in, Python's own contexts fail (an
                # endless recursion in ssl.py's verify_mode)
                kw["ssl_context"] = tls_context()
            conn.cert_reqs = "CERT_REQUIRED"
            conn.ca_certs = conn.ca_cert_dir = None   # the context has the bundle: urllib3 would read it again
            return
        super().cert_verify(conn, url, verify, cert)


ONLINE_EVERY_S = 10.0           # while the connection is gone, it is looked for this often


def connection_lost(exc: BaseException) -> bool:
    """A connection problem (no network, a timeout, a connection dropped halfway), not an answer from a server."""
    if isinstance(exc, (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError)):
        return True
    return isinstance(exc, OSError) and exc.errno in (errno.ECONNRESET, errno.ECONNABORTED, errno.ECONNREFUSED,
                                                      errno.ENETDOWN, errno.ENETUNREACH, errno.EHOSTUNREACH,
                                                      errno.ETIMEDOUT)


def online(timeout: float = 5.0, stop: Callable[[], bool] = lambda: False) -> bool:
    """Panopto or IEULearn answers at all (any answer, an error page too). False only when neither can be reached:
    Wi-Fi off, the PC just woken from sleep, a VPN switching. A blocked secure connection (an antivirus, a Wi-Fi
    sign-in page) counts as online: waiting wouldn't fix it. ``stop()`` is asked between the two looks (Stop waiting
    then acts within one look, not two)."""
    from .ieulearn import BASE as learn
    from .panopto import BASE as panopto

    s = plain_session()
    try:
        for url in (panopto, learn):
            if stop():
                return False
            try:
                s.head(url, timeout=(timeout, timeout), allow_redirects=False)
                return True
            except requests.exceptions.SSLError:
                return True
            except requests.exceptions.ProxyError:
                # a proxy set on this PC refuses: its own message, at once. Where the PC can't say whether it is
                # online (Linux, an older Windows), a refusing proxy is said too, instead of 15 minutes of waiting
                # that end in "No internet connection"
                if pc_connectivity() in ("internet", ""):
                    return True
                continue
            except requests.RequestException:
                continue
        return False
    finally:
        s.close()


OFFLINE_LOOKS = 90              # with no connection, it is looked for at most this often (~15 minutes while awake)
BLOCKED_S = 90.0                # Windows says this PC is online, yet IEULearn and Panopto don't answer: said after this
UNTIL_ONLINE_WAITS = 5          # a connection that keeps coming and going: the work is tried this often, then said why


class Offline(NetworkError):
    """The connection was waited for as long as it makes sense: the message says why it can't get through."""


def pc_connectivity() -> str:
    """What Windows itself says about this PC's connection, which a block of IEUStudia alone (a firewall, an
    antivirus) or IEU's sites being down doesn't change: "internet", "portal" (a Wi-Fi that wants you to sign in
    first), "local" (a network without the internet), "none" (no network) or "" (unknown: not Windows, or no answer)."""
    if sys.platform != "win32":
        return ""
    try:
        import ctypes

        class Hint(ctypes.Structure):          # NL_NETWORK_CONNECTIVITY_HINT
            _fields_ = [("level", ctypes.c_int), ("cost", ctypes.c_int), ("approaching", ctypes.c_ubyte),
                        ("over", ctypes.c_ubyte), ("roaming", ctypes.c_ubyte)]

        fn = ctypes.WinDLL("iphlpapi").GetNetworkConnectivityHint       # Windows 10 2004 and later
        fn.restype = ctypes.c_long
        hint = Hint()
        if fn(ctypes.byref(hint)) != 0:
            return ""
        return {1: "none", 2: "local", 3: "internet", 4: "portal"}.get(hint.level, "")
    except Exception:  # noqa: BLE001 - an older Windows: unknown
        log.debug("No connectivity hint from Windows", exc_info=True)
        return ""


def _reason(why: str) -> str:
    """Why IEULearn and Panopto can't be reached, from what Windows says: a sentence that ends in what to do."""
    if why == "none":
        return t("This PC isn't connected to a network: turn on Wi-Fi or plug in the cable.")
    if why == "local":
        return t("This network doesn't reach the internet. If it's a Wi-Fi that wants you to sign in first (campus, "
                 "dorm, café), open any website in your browser and sign in there.")
    if why == "portal":
        return t("This Wi-Fi wants you to sign in first: open any website in your browser and sign in to the Wi-Fi.")
    return ""


def waiting_text(why: str, files: bool = False) -> str:
    """What a sync (or the course files, ``files``) says while it waits for the connection."""
    if why == "internet":
        return t("This PC is online, but IEULearn and Panopto don't answer IEUStudia: trying again for a minute or two.")
    lead = t("Course files wait for the internet connection.") if files else t("Waiting for the internet connection.")
    tail = t("They download by themselves once it's back.") if files else t("The sync goes on by itself.")
    return " ".join(x for x in (lead, _reason(why), tail) if x)


def offline_text(why: str) -> str:
    """Why waiting for the connection stopped, and what to do."""
    if why == "internet":
        return t("This PC is online, but IEUStudia can't reach IEULearn or Panopto. A firewall, an antivirus, a VPN or "
                 "a proxy may be blocking IEUStudia, or IEULearn may be down: open learn.ieu.edu.tr in your browser. If "
                 "it opens there, allow IEUStudia in your antivirus or firewall (or turn the VPN off), then try again.")
    if why in ("none", "local", "portal"):
        return t("No internet connection, so IEUStudia stopped waiting. {reason} Then try again.", reason=_reason(why))
    return t("No internet connection: IEULearn and Panopto can't be reached. Check your connection (on campus Wi-Fi, "
             "make sure you're signed in to it), then try again.")


def wait_online(stop: Callable[[], bool] = lambda: False, told: Optional[Callable[[str], None]] = None) -> bool:
    """Wait until :func:`online`: True. False when ``stop()`` says to give up first (checked four times a second).
    ``told(why)`` hears what Windows says about the connection (see pc_connectivity) whenever that changes. Never
    endlessly: raises Offline, with the reason, after BLOCKED_S when Windows says this PC is online (something blocks
    IEUStudia alone, or IEU's sites are down: waiting won't help), else after OFFLINE_LOOKS looks (counted while the
    PC is awake: a PC asleep for hours goes on when it wakes up)."""
    looks, blocked_since, said = 0, 0.0, None
    while not online(stop=stop):
        if stop():
            return False
        why = pc_connectivity()
        if told is not None and why != said:
            said = why
            told(why)
        looks += 1
        now = time.monotonic()
        blocked_since = (blocked_since or now) if why == "internet" else 0.0
        if looks >= OFFLINE_LOOKS or (blocked_since and now - blocked_since >= BLOCKED_S):
            log.warning("Stopped waiting for the connection (Windows: %s; %d looks)", why or "unknown", looks)
            raise Offline(offline_text(why))
        end = now + ONLINE_EVERY_S
        while time.monotonic() < end:
            if stop():
                return False
            time.sleep(min(0.25, ONLINE_EVERY_S))
    return True


def until_online(fn: Callable[[], Any], waiting: Callable[[], None] = lambda: None,
                 partial: Callable[[Any], bool] = lambda result: False, *,
                 told: Optional[Callable[[str], None]] = None, stop: Callable[[], bool] = lambda: False) -> Any:
    """``fn()``; when it fails for want of any connection (not one server that is down), or comes back
    ``partial(result)`` (some courses couldn't be reached) while there is none, ``waiting()`` says so, the
    connection is waited for (wait_online: ``told`` hears why, ``stop`` ends it, never endlessly) and ``fn()`` runs
    again, at most UNTIL_ONLINE_WAITS times. Raises Offline with the reason when the wait ends without it."""
    for _ in range(UNTIL_ONLINE_WAITS):
        try:
            result = fn()
        except (requests.RequestException, OSError) as exc:
            if not connection_lost(exc) or online():
                raise
            log.info("No internet connection (%s); waiting for it", exc)
        else:
            if not partial(result) or online():
                return result
            log.info("The connection went away halfway; waiting for it")
        waiting()
        if not wait_online(stop, told):
            raise Offline(t("Stopped waiting for the internet connection. Sync again once you're online."))
    return fn()                                          # the last try: its own error, whatever it is


def make_session() -> requests.Session:
    _drop_stale_ca_bundles()
    s = _TimeoutSession()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9,tr;q=0.8",
    })
    retry = Retry(total=3, connect=2, read=2, backoff_factor=0.6, status_forcelist=[502, 503, 504],
                  allowed_methods=frozenset({"GET", "HEAD"}), raise_on_status=False,
                  retry_after_max=RETRY_AFTER_MAX_S)
    adapter = _Adapter(max_retries=retry, pool_connections=8, pool_maxsize=16)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


def plain_session() -> requests.Session:
    """A session like requests.Session() (no retries, no default timeout) that connects through the shared TLS
    contexts: for public sites (updates, models, the syllabus, ChatGPT)."""
    _drop_stale_ca_bundles()
    s = requests.Session()
    s.mount("https://", _Adapter())
    return s


@dc.dataclass
class Form:
    action: str
    method: str = "get"
    id: str = ""
    fields: dict[str, str] = dc.field(default_factory=dict)


class _Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: list[Form] = []
        self.iframes: list[str] = []
        self._form: Optional[Form] = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        if tag == "form":
            self._form = Form(action=a.get("action", ""), method=(a.get("method") or "get").lower(), id=a.get("id", ""))
            self.forms.append(self._form)
        elif tag == "input" and self._form is not None and a.get("name"):
            if a.get("type", "").lower() in ("checkbox", "radio") and "checked" not in a:
                return
            self._form.fields[a["name"]] = a.get("value", "")
        elif tag == "iframe" and a.get("src"):
            self.iframes.append(a["src"])

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self._form = None


def parse_forms(page: str) -> list[Form]:
    p = _Parser()
    p.feed(page or "")
    return p.forms


def parse_iframes(page: str) -> list[str]:
    p = _Parser()
    p.feed(page or "")
    return [html.unescape(src) for src in p.iframes]


def export_cookies(session: requests.Session) -> list[dict[str, Any]]:
    out = []
    for c in session.cookies:
        out.append({"name": c.name, "value": c.value, "domain": c.domain, "path": c.path,
                    "expires": c.expires, "secure": bool(c.secure)})
    return out


def import_cookies(session: requests.Session, cookies: list[dict[str, Any]]) -> None:
    now = time.time()
    for c in cookies:
        exp = c.get("expires")
        if exp and exp < now:
            continue
        session.cookies.set(c["name"], c.get("value", ""), domain=c.get("domain", ""), path=c.get("path", "/"),
                            secure=bool(c.get("secure")), expires=exp)


def clone_session(session: requests.Session) -> requests.Session:
    """A new session (for another thread) carrying the same cookies and headers."""
    s = make_session()
    s.headers.update(session.headers)
    import_cookies(s, export_cookies(session))
    return s


def merge_cookies(into: requests.Session, src: requests.Session) -> None:
    import_cookies(into, export_cookies(src))
