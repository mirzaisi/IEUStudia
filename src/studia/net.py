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
from typing import Any, Optional
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
    """A connection problem, with a message that can be shown to the user."""


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
        return (t("A secure connection to {host} was blocked. Antivirus web or HTTPS scanning, or a Wi-Fi sign-in page, "
                  "can do this: open any website in your browser, then try again.", host=host) if host else
                t("A secure connection to the server was blocked. Antivirus web or HTTPS scanning, or a Wi-Fi sign-in "
                  "page, can do this: open any website in your browser, then try again."))
    if isinstance(exc, requests.Timeout):
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


class _TimeoutSession(requests.Session):
    def request(self, method, url, **kwargs):  # type: ignore[override]
        kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
        try:
            return super().request(method, url, **kwargs)
        except requests.exceptions.SSLError as exc:
            raise NetworkError(plain_error(exc, url)) from exc
        except (requests.ConnectionError, requests.Timeout) as exc:
            host = urlparse(url).hostname or url
            raise NetworkError(t("Can't reach {host}. Check your internet connection.", host=host)) from exc


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


def make_session() -> requests.Session:
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
