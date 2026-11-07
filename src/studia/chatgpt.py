"""ChatGPT through "Continue with ChatGPT": OpenAI's official way for open-source apps that run on the user's own PC to
use the user's ChatGPT plan (Sign in with ChatGPT, https://developers.openai.com/siwc/token-sharing-open-source).

The user signs in and agrees on OpenAI's own page in their browser. IEUStudia receives OAuth tokens that only allow
Responses API requests on the user's plan - not their ChatGPT conversations - keeps them encrypted with Windows DPAPI,
renews them, and sends requests to https://api.openai.com/v1/responses. No password, API key or extra program is
involved. The user can see and limit the app's usage, or disconnect it, in ChatGPT's settings.

Flow (OAuth 2 authorization code with PKCE, a loopback callback on 127.0.0.1, no client secret):
  first sign-in: client_id=dynamic_agent_client registers IEUStudia for the account; the issued client id is kept and
  reused; every installation has its own opaque host id (urn:uuid:...).

OpenAI's answer can come back two ways, whichever comes first: the browser's way back to 127.0.0.1, or the address
pasted from the browser's error page (when something blocks 127.0.0.1). The page opens in the system browser only,
never in a window of IEUStudia's own: OpenAI's instruction for this sign-in is to open the system browser, RFC 8252
says native apps must not use embedded user agents, and Google refuses to sign in inside embedded web views. Each step
is noted with its time, for the page's waiting text and for Copy details.

There is no sign-in with a code either (OAuth's device code, a code typed on another device): OpenAI offers it to
Codex only, and asked with IEUStudia's own client id it answered HTTP 400 invalid_client "This app is unavailable".
The Codex CLI's own sign-in with a code (ai.py, Codex's client) is another sign-in and stays.
"""

from __future__ import annotations

import base64
import email.utils
import hashlib
import hmac
import html
import http.server
import json
import logging
import os
import platform
import re
import secrets
import socket
import sys
import threading
import time
import urllib.parse
import uuid
import webbrowser
from pathlib import Path
from typing import Any, Callable, Optional

from . import aierrors
from .cancel import Cancelled
from .i18n import t, tn

log = logging.getLogger("studia")

AUTH = "https://auth.openai.com"
AUTHORIZE_URL = f"{AUTH}/api/accounts/authorize"
TOKEN_URL = f"{AUTH}/api/accounts/oauth/token"
CONFIG_URL = f"{AUTH}/.well-known/openid-configuration"
API = "https://api.openai.com/v1"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
PLAN_SCOPE = "chatgpt.tokens.use.direct"
APP_NAME = "IEUStudia"
FIRST_CLIENT = "dynamic_agent_client"         # the registration entry point, never saved as the client id
CALLBACK = "/auth/callback"
START = "/sign-in"                              # IEUStudia's own link to the sign-in page (forwards to OpenAI)
SIGN_IN_TIMEOUT_S = 600
REFRESH_MARGIN_S = 120
USAGE_URL = "https://chatgpt.com/settings/usage"
EFFORT = {"haiku": "low", "sonnet": "medium", "opus": "high"}
MODELS_TTL_S = 24 * 3600
WINDOWS = sys.platform == "win32"
CLOCK_SLACK_S = 300                 # a PC clock further off OpenAI's than this is said (and allowed for)
REACH_TIMEOUT = (6, 10)             # the quick look, at the start, at whether OpenAI's sign-in can be reached
UNREADABLE = "chatgpt.bin.unreadable"           # a sign-in this PC can't decrypt any more, kept aside

_lock = threading.RLock()          # the credential record and its (rotating) refresh token
_attempt: dict[str, Any] = {}      # the sign-in under way (start_sign_in): "state" "waiting"|"done"|"error", its steps
_jwks: dict[str, Any] = {}
_clock: dict[str, float] = {}      # {"server": OpenAI's time, "mono": time.monotonic() then, "at": this PC's time then}
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")     # (kept out of Copy details)
_host: dict[str, str] = {}         # this installation's id, when it couldn't be saved
# what status() shows of the sign-in under way
_SHOWN = ("state", "error", "code", "link", "page", "opened", "launched", "step", "browser", "via", "why", "note",
          "reach", "edge", "elevated", "again", "way", "behind")
BEHIND_S = 3.0                      # this long after the browser asked for the page, IEUStudia's window still in front:
                                    # the page opened behind it (Windows flashes the browser's taskbar button instead)
# Open the page again: another way each time while the page doesn't open (osutil.open_link's ways), and the one each
# first open (open_link without a way, its "via") counts as. Run as administrator ("explorer"), the default browser's
# way goes through Explorer too: counted as that one, so the next is Microsoft Edge, not the same Explorer again.
WAYS = ("default", "edge", "shell")
_FIRST_WAY = {"https": "default", "default": "shell", "explorer": "default", "edge": "edge"}


class SignInError(RuntimeError):
    """A sign-in that didn't finish: a message for the student, and a code the page acts on ("denied", "workspace",
    "other_account", "client", "network", "blocked", "service", "verify", "save", "listen", "timeout", "other")."""

    def __init__(self, message: str, code: str = "") -> None:
        super().__init__(message)
        self.code = code


def _session() -> Any:
    from .net import plain_session

    s = plain_session()
    s.headers["User-Agent"] = "IEUStudia (+https://github.com/mirzaisi/IEUStudia)"
    return s


# ----------------------------------------------------------------------------- the stored sign-in
def _dir() -> Path:
    """Where the sign-in and its model list are kept: the profile in use, so each student on a shared PC uses their
    own ChatGPT plan. The first profile is the app folder itself, so a sign-in made before stays with it."""
    from .paths import profile_dir

    return profile_dir()


def _host_dir() -> Path:
    from .paths import app_dir

    return app_dir()


def host_id() -> str:
    """This installation's opaque host id for OpenAI (created once, never derived from the user; one for all the
    profiles). When it can't be saved (a full disk, a folder a security program guards), the one made now is used for
    as long as IEUStudia runs, so the sign-in still works."""
    path = _host_dir() / "chatgpt-host.json"
    try:
        hid = json.loads(path.read_text(encoding="utf-8")).get("ext_agent_host_id", "")
        if isinstance(hid, str) and hid.startswith("urn:uuid:"):
            return hid
    except (OSError, ValueError, AttributeError):
        pass
    hid = _host.get("id") or f"urn:uuid:{uuid.uuid4()}"
    try:
        path.write_text(json.dumps({"ext_agent_host_id": hid}), encoding="utf-8")
    except OSError as exc:
        if not _host.get("id"):
            log.warning("ChatGPT: this installation's id could not be saved (%s); one for this run is used", exc)
    _host["id"] = hid
    return hid


def _protect(data: bytes) -> bytes:
    from .secrets_store import protect

    return protect(data)


def _unprotect(data: bytes) -> bytes:
    from .secrets_store import unprotect

    return unprotect(data)


def _passing(exc: BaseException) -> bool:
    """A decryption failure that may pass (Linux: the keyring isn't there or is locked right now), not a file that
    this account can't open any more."""
    return not WINDOWS and isinstance(exc, OSError) and not str(exc).startswith(
        ("not a file IEUStudia encrypted", "the keyring's key doesn't open"))


def _load() -> dict[str, Any]:
    """The sign-in kept on this PC ({} when there is none). One this Windows account can't decrypt any more (after a
    Windows password reset, with a profile copied from another PC, a damaged file) is kept aside as UNREADABLE, and
    status() says to sign in again."""
    path = _dir() / "chatgpt.bin"
    if not path.is_file():
        return {}
    try:
        raw = path.read_bytes()
    except OSError as exc:                         # held for a moment (an antivirus scan, say): read again next time
        log.warning("The ChatGPT sign-in could not be read: %s", exc)
        return {}
    try:
        rec = json.loads(_unprotect(raw).decode("utf-8"))
        if not isinstance(rec, dict):
            raise ValueError("not a sign-in record")
        return rec
    except (OSError, ValueError) as exc:
        if _passing(exc):
            log.warning("The ChatGPT sign-in can't be decrypted right now: %s", exc)
            return {}
        try:
            os.replace(path, path.with_name(UNREADABLE))
            log.warning("The ChatGPT sign-in can't be decrypted on this PC any more (%s): kept aside as %s; signing "
                        "in again is needed", exc, UNREADABLE)
        except OSError as move:
            log.warning("The ChatGPT sign-in can't be decrypted (%s) nor kept aside (%s)", exc, move)
        return {}


def _save(rec: dict[str, Any]) -> None:
    path = _dir() / "chatgpt.bin"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(_protect(json.dumps(rec).encode("utf-8")))
    os.replace(tmp, path)
    try:                         # a new sign-in is kept: the old unreadable one says nothing any more (after a later
        (_dir() / UNREADABLE).unlink(missing_ok=True)       # sign-out it would claim a Windows password reset again)
    except OSError:
        pass


def _forget() -> None:
    """Drop the sign-in kept here without asking OpenAI (its registration isn't accepted any more): the next sign-in
    registers IEUStudia anew."""
    with _lock:
        try:
            (_dir() / "chatgpt.bin").unlink(missing_ok=True)
        except OSError as exc:
            log.warning("The ChatGPT sign-in could not be removed: %s", exc)
    _models_cache_clear()


def signed_in() -> bool:
    """Signed in, and allowed to use the ChatGPT plan."""
    rec = _load()
    return bool(rec.get("refresh_token") and PLAN_SCOPE in (rec.get("scopes") or []))


def status() -> dict[str, Any]:
    """The sign-in kept here, and the one under way. ``ended``: OpenAI ended it (renewing failed for good);
    ``unreadable``: one is kept aside that this PC can't decrypt; ``clock``: minutes this PC's clock is off OpenAI's
    (0 within CLOCK_SLACK_S), with ``clock_text`` saying how to fix it."""
    rec = _load()
    with _lock:
        attempt = {k: v for k, v in _attempt.items() if k in _SHOWN}
        if attempt.get("state") != "waiting":
            attempt.pop("link", None)
            attempt.pop("page", None)
    off = clock_off()
    return {"signed_in": bool(rec.get("refresh_token")), "email": rec.get("email", ""),
            "plan_allowed": PLAN_SCOPE in (rec.get("scopes") or []), "registered": bool(rec.get("client_id")),
            "ended": bool(rec.get("client_id")) and not rec.get("refresh_token"),
            "unreadable": not rec and (_dir() / UNREADABLE).is_file(),
            "clock": off, "clock_text": clock_message(off), "sign_in": attempt}


# ----------------------------------------------------------------------------- this PC's clock against OpenAI's
def _server_time(r: Any) -> Optional[float]:
    """When OpenAI made an answer (its Date header), as a timestamp; None without one."""
    try:
        date = (getattr(r, "headers", None) or {}).get("Date")
        return email.utils.parsedate_to_datetime(str(date)).timestamp() if date else None
    except (AttributeError, TypeError, ValueError, IndexError, OverflowError):
        return None


def _measure_clock(r: Any) -> None:
    """How far this PC's clock is from OpenAI's, from an answer that just came (its Date is in whole seconds:
    enough for a check that allows minutes)."""
    server = _server_time(r)
    if server is not None:
        _set_server(server)


def _set_server(server: float) -> None:
    """OpenAI's time now (``server``), carried forward from here with the monotonic clock: a PC clock set right
    afterwards (as the warning says) counts at once, instead of the warning staying until the next answer."""
    before = _skew()
    _clock.update(server=server, mono=time.monotonic(), at=time.time())
    skew = _skew()
    if abs(skew) > CLOCK_SLACK_S and abs(skew - before) > 60:
        log.warning("This PC's clock is %+.0f min off OpenAI's (fix: Set time automatically)", skew / 60)


def _skew() -> float:
    """This PC's clock minus OpenAI's, now, in seconds (0 when not measured)."""
    if "server" not in _clock:
        return 0.0
    return time.time() - _openai_now()


def _openai_now() -> float:
    """The time on OpenAI's clock as far as it is known: the last one it said, and the time gone by since."""
    if "server" not in _clock:
        return time.time()
    return _clock["server"] + (time.monotonic() - _clock["mono"])


def clock_off() -> int:
    """Minutes this PC's clock is off OpenAI's (+ ahead, - behind); 0 within CLOCK_SLACK_S or when not measured."""
    skew = _skew()
    return int(round(skew / 60)) if abs(skew) > CLOCK_SLACK_S else 0


def _amount(minutes: int) -> str:
    m = abs(int(minutes))
    if m < 120:
        return tn(m, "{n} minute", "{n} minutes")
    if m < 48 * 60:
        return tn(round(m / 60), "{n} hour", "{n} hours")
    return tn(round(m / 1440), "{n} day", "{n} days")


def clock_message(minutes: int) -> str:
    """"Your PC's clock is 6 hours ahead", and how to set it right; "" when it is right."""
    if not minutes:
        return ""
    if not WINDOWS:
        return (t("Your PC's clock is {amount} ahead. Set it right in your system settings (Date & Time: set the "
                  "time automatically).", amount=_amount(minutes)) if minutes > 0 else
                t("Your PC's clock is {amount} behind. Set it right in your system settings (Date & Time: set the "
                  "time automatically).", amount=_amount(minutes)))
    return (t("Your PC's clock is {amount} ahead. Set it right in Windows: Settings → Time & language → Date & time, "
              "turn on Set time automatically (and check the time zone).", amount=_amount(minutes)) if minutes > 0 else
            t("Your PC's clock is {amount} behind. Set it right in Windows: Settings → Time & language → Date & time, "
              "turn on Set time automatically (and check the time zone).", amount=_amount(minutes)))


def _clock_tls(exc: BaseException) -> bool:
    """A secure connection that failed on a certificate's dates: what a wrong date on this PC causes. By Windows' code
    too: its words are Turkish on a Turkish Windows, and only English words were looked for."""
    from .net import cert_out_of_date

    return cert_out_of_date(exc)


def _clock_tls_message() -> str:
    return t("A secure connection to OpenAI failed because a certificate looks out of date. That happens when this "
             "PC's date or time is wrong (it says {now}): turn on Set time automatically in Settings → Time & language "
             "→ Date & time, then try again. If the date is right, a security program or the network is changing "
             "secure connections.", now=time.strftime("%Y-%m-%d %H:%M"))


def _net_message(exc: BaseException) -> str:
    """Why IEUStudia itself couldn't talk to OpenAI's sign-in, in plain words, with what to do."""
    import requests

    low = str(exc).lower()
    if isinstance(exc, requests.exceptions.SSLError):
        return _clock_tls_message() if _clock_tls(exc) else t(
            "A secure connection to OpenAI (auth.openai.com) was blocked or changed on the way. Antivirus HTTPS "
            "scanning, a school or company network, or a Wi-Fi sign-in page can do this: open chatgpt.com in your "
            "browser, then try again.")
    if isinstance(exc, requests.exceptions.ProxyError):
        return t("The proxy set on this PC didn't let IEUStudia reach OpenAI. Check it in Settings → Network & internet "
                 "→ Proxy (or turn it off), then try again.")
    if isinstance(exc, requests.Timeout):
        return t("OpenAI's sign-in (auth.openai.com) didn't answer in time. Check your internet connection and try "
                 "again.")
    if isinstance(exc, requests.ConnectionError):
        if any(w in low for w in ("getaddrinfo", "name resolution", "nodename", "name or service", "11001", "11004")):
            return t("IEUStudia can't find OpenAI's sign-in (auth.openai.com): this PC seems to be offline, or its "
                     "DNS is blocked. Check your internet connection and try again.")
        return t("IEUStudia can't connect to OpenAI's sign-in (auth.openai.com). Check your internet connection; a "
                 "firewall, VPN or security program may be blocking IEUStudia.")
    return t("Talking to OpenAI's sign-in failed ({kind}). Check your internet connection and try again.",
             kind=exc.__class__.__name__)


# ----------------------------------------------------------------------------- signing in
def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def authorize_url(*, client_id: str, redirect_uri: str, state: str, nonce: str, challenge: str,
                  rec: Optional[dict[str, Any]] = None, consent: bool = False) -> str:
    """The URL of OpenAI's sign-in page for this attempt."""
    rec = rec or {}
    params = {"client_id": client_id, "ext_agent_host_id": host_id(), "response_type": "code",
              "redirect_uri": redirect_uri, "scope": SCOPES, "resource": API, "state": state, "nonce": nonce,
              "code_challenge_method": "S256", "code_challenge": challenge}
    if client_id == FIRST_CLIENT:
        params["agent_name_hint"] = APP_NAME
    else:
        if rec.get("id_token"):
            params["id_token_hint"] = rec["id_token"]
        if rec.get("email"):
            params["login_hint"] = rec["email"]
    if consent:
        params["prompt"] = "consent"
    return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params, quote_via=urllib.parse.quote)}"


_PAGE = """<!doctype html><meta charset="utf-8"><title>IEUStudia</title>
<body style="font:16px system-ui,sans-serif;display:grid;place-items:center;height:90vh;margin:0;background:#fafafa;color:#111">
<div style="max-width:440px;text-align:center"><h2 style="margin:0 0 8px">{title}</h2><p style="color:#555">{text}</p></div>"""


def _launch(open_browser: Callable[[str], Any], link: str) -> dict[str, Any]:
    """Ask for the sign-in page in the browser: {"ok", "browser", "via", "why"} (osutil.open_link's answer). An opener
    that answers True/False/None says only whether it started (None: taken as started)."""
    try:
        r = open_browser(link)
    except Exception as exc:  # noqa: BLE001 - the set-up offers the link to copy
        log.warning("ChatGPT sign-in: no browser could be started: %s", exc)
        return {"ok": False, "browser": "", "via": "", "why": ""}
    if isinstance(r, dict):
        return {"ok": bool(r.get("ok")), **{k: str(r.get(k) or "") for k in ("browser", "via", "why")}}
    return {"ok": r is not False, "browser": "", "via": "", "why": ""}


def _system(name: str) -> bool:
    """osutil's edge_available() or elevated(); False when it can't say (only a hint for the page)."""
    try:
        from . import osutil

        return bool(getattr(osutil, name)())
    except Exception:  # noqa: BLE001 - a hint less
        log.debug("ChatGPT sign-in: %s could not be told", name, exc_info=True)
        return False


def _open_link(link: str, way: str = "") -> Any:
    from .osutil import open_link

    return open_link(link, way)


def _query(text: str) -> dict[str, str]:
    """The parameters of an address, or of its part after "?" (what came back from OpenAI)."""
    part = text.split("?", 1)[1] if "?" in text else text
    try:
        return {k: v[0] for k, v in urllib.parse.parse_qs(part.split("#", 1)[0]).items()}
    except ValueError:
        return {}


def _callback_error(error: str, description: str) -> dict[str, Any]:
    """What an error OpenAI sent the browser back with means (OAuth's codes; OpenAI's own words where it gives some)."""
    why = " ".join((description or "").split())[:300]
    low = f"{error} {why}".lower()
    declined = ("user denied", "denied by the user", "denied by user", "resource owner", "cancel", "declin")
    if error == "access_denied" and (not why or any(w in low for w in declined)):
        return {"state": "error", "code": "denied", "error": t(
            "You didn't allow IEUStudia to use your ChatGPT plan. To connect it, click Continue with ChatGPT again and "
            "choose Allow on OpenAI's page.")}
    if error == "access_denied":
        return {"state": "error", "code": "workspace", "error": t(
            "OpenAI didn't let IEUStudia connect (“{why}”). If you clicked Cancel, click Continue with ChatGPT and "
            "choose Allow. If you picked a school or company workspace, its admin may not allow apps: click Use "
            "another account or workspace and pick your personal one.", why=why)}
    # (invalid_client and unauthorized_client: _client_refused, which keeps a saved registration the first time)
    if error in ("server_error", "temporarily_unavailable"):
        return {"state": "error", "code": "service", "error": t(
            "OpenAI's sign-in had a problem on its side ({why}). Try again in a few minutes.", why=why or error)}
    if error in ("login_required", "consent_required", "interaction_required", "account_selection_required"):
        return {"state": "error", "code": "again", "error": t(
            "OpenAI needs you to finish on its page. Click Continue with ChatGPT and go through OpenAI's page to the "
            "end.")}
    if error in ("invalid_scope", "invalid_request", "unsupported_response_type", "invalid_target"):
        return {"state": "error", "code": "app", "error": t(
            "OpenAI didn't accept IEUStudia's sign-in request ({why}). This needs a newer IEUStudia: look for one in "
            "Settings → About.", why=why or error)}
    return {"state": "error", "code": "other", "error": t("OpenAI's sign-in did not finish: {why}", why=why or error)}


_CLIENT_REFUSED = ("invalid_client", "unauthorized_client")
_refusals: dict[str, int] = {}      # client id -> refusals in a row, also when they couldn't be kept on disk


def _client_refused(client_id: str) -> dict[str, Any]:
    """OpenAI turned IEUStudia's client down in a sign-in (invalid_client or unauthorized_client, on the way back or
    at the code exchange). A saved registration is never dropped after one refusal: that can pass (OpenAI's page told
    another app on this sign-in "This app is unavailable" for a few hours), and OpenAI asks apps not to erase
    credentials over a temporary failure. It is kept, refused once, and the next sign-in tries it again; a second
    refusal in a row drops it, so the next sign-in registers IEUStudia anew. Signing in or renewing with it clears
    the count. A sign-in that was registering IEUStudia has nothing to keep. The outcome says which:
    {"state": "error", "code": "client", "error"}."""
    if client_id == FIRST_CLIENT:
        log.warning("ChatGPT sign-in: OpenAI turned down registering IEUStudia")
        return {"state": "error", "code": "client", "error": t(
            "OpenAI isn't letting IEUStudia connect to ChatGPT accounts right now. This is on OpenAI's side, not your "
            "PC: try again in a few hours.")}
    with _lock:
        rec = _load()
        kept = rec.get("client_id") == client_id
        times = max(int(rec.get("client_refused") or 0) if kept else 0, _refusals.get(client_id, 0)) + 1
        if times >= 2:
            _refusals.pop(client_id, None)
            if kept:
                _forget()
        else:
            _refusals[client_id] = times
            if kept:
                rec["client_refused"] = times
                try:
                    _save(rec)
                except OSError as exc:           # counted for as long as IEUStudia runs
                    log.warning("ChatGPT sign-in: the refusal could not be noted in the kept sign-in: %s", exc)
    if times >= 2:
        log.warning("ChatGPT sign-in: OpenAI turned IEUStudia's registration down a second time in a row: dropped, "
                    "the next sign-in registers IEUStudia anew")
        return {"state": "error", "code": "client", "error": t(
            "OpenAI turned down IEUStudia's connection to your account again (it may have been disconnected in "
            "ChatGPT's settings), so IEUStudia connects anew next time: click Continue with ChatGPT and allow "
            "IEUStudia on OpenAI's page.")}
    log.warning("ChatGPT sign-in: OpenAI turned IEUStudia's registration down: kept, the next sign-in tries it again")
    return {"state": "error", "code": "client", "error": t(
        "OpenAI turned down IEUStudia's connection to your account this time. That can pass, so IEUStudia kept it: "
        "click Continue with ChatGPT to try again. If OpenAI turns it down once more, IEUStudia connects anew.")}


def _client_accepted(client_id: str) -> None:
    """A sign-in or a renewal worked with this client id: earlier refusals of it don't count any more."""
    _refusals.pop(client_id, None)


def start_sign_in(emit: Callable[[dict[str, Any]], None] = lambda e: None, *, consent: bool = False,
                  open_browser: Callable[[str], Any] = webbrowser.open, fresh: bool = False,
                  in_front: Optional[Callable[[], bool]] = None) -> dict[str, Any]:
    """Open OpenAI's sign-in page in the browser and wait (in the background) for it to come back to IEUStudia.

    The browser is sent to a page of IEUStudia's own first (``link``, on 127.0.0.1), which forwards it to OpenAI.
    That way IEUStudia knows when the page really opened in a browser (``opened``: on some PCs nothing opens, or it
    opens behind the window). ``page`` is OpenAI's page itself, to copy into a browser by hand: it works even where
    something blocks the first link. ``fresh``: IEUStudia is registered anew, for another account or workspace (no
    kept client id). ``in_front()``: whether IEUStudia's own window is the one in front; a moment after the browser
    asks for the page, still True means the page opened behind it (``behind``). ``edge``: Microsoft Edge is there for
    Open in Microsoft Edge; ``elevated``: IEUStudia runs as administrator (said only then, if the page doesn't show up).

    Each step goes to ``emit`` as {"type": "chatgpt", "state": "waiting", "step", ...what the page shows}: "listening",
    "opening", "launched" or "no_browser" (with "browser", "via", "why"), "opened", "returned", "exchanging",
    "verifying"; notes come without "step" ("reach", "note": "https" or "listen"). The outcome:
    {"type": "chatgpt", "state": "done"|"error", "error", "email", "code"}."""
    with _lock:
        if _attempt.get("state") == "waiting":
            _attempt["cancel"].set()                     # a new click replaces an attempt that was left open
        cancel = threading.Event()
        _attempt.clear()
        _attempt.update(state="", error="", code="", cancel=cancel, steps=[], step="")   # "waiting" once it waits
    rec = {} if fresh else _load()
    client_id = rec.get("client_id") or FIRST_CLIENT
    state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    result: dict[str, Any] = {}                    # {"state": "done"|"error", "error", "email", "code"} once back
    claim = threading.Lock()                       # one answer is taken (they come on threads of their own)

    def track(name: str, detail: str = "", *, moves: bool = True, **shown: Any) -> None:
        """The sign-in got this far (``moves``), or something worth knowing happened: kept with its time for Copy
        details, logged, and said to the page with what it shows (``shown``). Nothing once it has ended: a late note
        mustn't make the page wait again."""
        with _lock:
            if _attempt.get("cancel") is not cancel or cancel.is_set() or _attempt.get("state") not in ("", "waiting"):
                return
            _attempt["steps"].append((time.time(), name, detail))
            _attempt.update(shown)
            if moves:
                _attempt["step"] = name
        log.info("ChatGPT sign-in: %s%s", name, f" ({detail})" if detail else "")
        emit({"type": "chatgpt", "state": "waiting", **({"step": name} if moves else {}), **shown})

    def opened(how: str) -> None:
        with _lock:
            first = _attempt.get("cancel") is cancel and not _attempt.get("opened")
            if first:
                _attempt["opened"] = True
        if first:
            track("opened", how, opened=True)

    def asked() -> None:
        """The browser asked for IEUStudia's own link: the page opens in it. Counted, so Open the page again keeps a
        way that brought the page (its tab was only closed) and moves on from one that didn't."""
        with _lock:
            if _attempt.get("cancel") is not cancel:
                return
            _attempt["asks"] = int(_attempt.get("asks") or 0) + 1
            again = _attempt["asks"] > 1
        if again:
            track("opened again", "browser", moves=False)
        opened("browser")
        if in_front is not None:
            timer = threading.Timer(BEHIND_S, look_behind)
            timer.daemon = True
            timer.start()

    def look_behind() -> None:
        """A moment after the browser asked for the page: is IEUStudia's window still the one in front? Then the page
        opened behind it. Said when that changes (Open the page again looks once more)."""
        if result or cancel.is_set():
            return
        try:
            front = bool(in_front())
        except Exception:  # noqa: BLE001 - only a hint
            log.debug("ChatGPT sign-in: which window is in front could not be told", exc_info=True)
            return
        with _lock:
            same = _attempt.get("cancel") is not cancel or bool(_attempt.get("behind")) == front
        if not same:
            track("behind" if front else "in front", "IEUStudia's window still in front" if front else "", moves=False,
                  behind=front)

    def https_tried() -> None:
        with _lock:
            first = _attempt.get("cancel") is cancel and _attempt.get("note") != "https"
        if first:
            log.warning("ChatGPT sign-in: the browser asked for https:// on 127.0.0.1 (an HTTPS-only setting or a "
                        "security program); IEUStudia's link is plain http")
            track("https", "a TLS handshake reached the plain listener", moves=False, note="https")

    def complete(q: dict[str, str]) -> dict[str, Any]:
        """What came back from OpenAI: the sign-in kept, or why not."""
        if q.get("error") in _CLIENT_REFUSED:      # kept the first time, registered anew after a second refusal
            return _client_refused(client_id)
        if q.get("error"):
            return _callback_error(q["error"], q.get("error_description", ""))
        if not q.get("code"):
            return {"state": "error", "code": "no_code", "error": t(
                "OpenAI sent your browser back without the sign-in code. Click Continue with ChatGPT to try again.")}
        if client_id != FIRST_CLIENT and q.get("client_id") and q["client_id"] != client_id:
            _forget()                              # so the next try registers IEUStudia anew instead of failing again
            return {"state": "error", "code": "client", "error": t(
                "OpenAI asked to register IEUStudia for your account again. Click Continue with ChatGPT once more.")}
        issued = q.get("client_id") or (client_id if client_id != FIRST_CLIENT else "")
        if not issued:
            return {"state": "error", "code": "client", "error": t(
                "OpenAI did not finish registering IEUStudia for your account. Try again.")}
        import requests

        try:
            track("exchanging")
            new = exchange_code(q["code"], issued, verifier, redirect_uri, nonce, previous=rec,
                                on_step=lambda s: track(s))
        except SignInError as exc:
            if exc.code == "client":               # (the code was for the saved registration, or a new one)
                return _client_refused(client_id)
            return {"state": "error", "code": exc.code, "error": str(exc)}
        except requests.RequestException as exc:
            log.warning("ChatGPT sign-in: OpenAI could not be reached: %s", exc)
            return {"state": "error", "code": "network", "error": _net_message(exc)}
        except Exception as exc:  # noqa: BLE001 - shown in the set-up and in the browser
            log.exception("ChatGPT sign-in failed")
            return {"state": "error", "code": "other", "error": t(
                "Something went wrong while finishing the sign-in ({kind}). Try again; if it happens again, click Copy "
                "details and send them when you ask for help.", kind=exc.__class__.__name__)}
        return {"state": "done", "code": "", "error": "", "email": new.get("email", "")}

    def deliver(q: dict[str, str], how: str) -> dict[str, Any]:
        """OpenAI's answer, whichever way it came (the browser's way back, a pasted address): taken once; the same
        answer again gets the same outcome. {"state": "mismatch"}: not this sign-in's; {"state": "ended"}: this
        sign-in was stopped."""
        with claim:
            if not hmac.compare_digest(str(q.get("state") or ""), state):
                return {"state": "mismatch"}
            if result:
                return dict(result)
            if cancel.is_set():
                return {"state": "ended"}
            opened(how)                            # a copied OpenAI link can come back without our page
            track("returned", how)
            out = complete(q)                      # before answering, so the browser's page tells the truth
            result.update(out)
            return out

    class Handler(http.server.BaseHTTPRequestHandler):
        timeout = 15                               # a connection a browser opens ahead and never uses holds nothing up

        def handle(self) -> None:
            try:
                first = self.connection.recv(1, socket.MSG_PEEK)
            except OSError:                        # opened ahead and never used, or dropped
                return
            if first == b"\x16":                   # a TLS handshake: https:// asked of this plain http listener
                https_tried()
                return
            super().handle()

        def do_GET(self) -> None:  # noqa: N802 - http.server's naming
            url = urllib.parse.urlsplit(self.path)
            if url.path == START:                  # our own link: on to OpenAI's sign-in page
                if result or cancel.is_set():
                    self._page(t("Sign-in not completed"), t("This sign-in has ended. Start it again from IEUStudia."), 410)
                    return
                asked()
                self.send_response(302)
                self.send_header("Location", auth_url)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if url.path != CALLBACK:
                self.send_error(404)
                return
            out = deliver(_query(url.query), "browser")
            if out["state"] == "mismatch":
                self._page(t("Sign-in not completed"), t("This page did not come from the sign-in IEUStudia "
                           "started. Close it and try again from IEUStudia."), 400)
            elif out["state"] == "ended":
                self._page(t("Sign-in not completed"), t("This sign-in has ended. Start it again from IEUStudia."), 410)
            elif out["state"] == "done":
                self._page(t("You're signed in"), t("IEUStudia can now use your ChatGPT plan. You can close this tab."))
            else:
                self._page(t("ChatGPT was not connected"), t("{error} You can close this tab and try again from "
                           "IEUStudia.", error=html.escape(out["error"])))

        def _page(self, title: str, text: str, code: int = 200) -> None:
            body = _PAGE.format(title=title, text=text).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:   # the callback URL holds a code: keep it out of the logs
            pass

    server: Optional[http.server.ThreadingHTTPServer]
    try:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.timeout = 0.5
        port = server.server_address[1]
        refused = ""
    except OSError as exc:
        # security software that forbids listening, even on 127.0.0.1: OpenAI's answer can still come back through
        # the address pasted from the browser's error page (a port nothing listens on)
        server, port, refused = None, 49152 + secrets.randbelow(16000), str(exc)
        log.warning("ChatGPT sign-in: could not wait for the browser on 127.0.0.1 (%s): a pasted address has to "
                    "bring the answer", exc)
    redirect_uri = f"http://127.0.0.1:{port}{CALLBACK}"
    auth_url = authorize_url(client_id=client_id, redirect_uri=redirect_uri, state=state, nonce=nonce,
                             challenge=challenge, rec=rec, consent=consent)
    link = f"http://127.0.0.1:{port}{START}" if server is not None else auth_url

    def finish(state_: str, error: str = "", email: str = "", code: str = "") -> None:
        with _lock:
            if _attempt.get("cancel") is cancel:
                _attempt.update(state=state_, error=error, code=code, step=state_)
                _attempt["steps"].append((time.time(), state_, f"[{code}] {error}" if error else ""))
        if error:
            log.warning("ChatGPT sign-in: %s [%s] %s", state_, code, error)
        else:
            log.info("ChatGPT sign-in: %s", state_)
        emit({"type": "chatgpt", "state": state_, "error": error, "email": email, "code": code})

    def run() -> None:
        try:
            started = time.monotonic()
            while not result and not cancel.is_set() and time.monotonic() - started < SIGN_IN_TIMEOUT_S:
                if server is not None:
                    server.handle_request()
                else:
                    cancel.wait(0.5)
            if cancel.is_set() and not result:
                log.info("ChatGPT sign-in: stopped before it came back")
                return
            if not result:
                with _lock:
                    current = _attempt.get("cancel") is cancel
                    seen = bool(_attempt.get("opened")) if current else True
                    note = _attempt.get("note", "") if current else ""
                    # Microsoft Edge to try next time: not when Edge was what showed nothing (Open in Microsoft Edge,
                    # or Edge as the default browser), nor when no browser started at all (Edge was tried then too)
                    edge = current and bool(_attempt.get("edge")) and _attempt.get("launched") is not False and \
                        _attempt.get("via") != "edge" and _attempt.get("browser") != "Microsoft Edge"
                    elevated = bool(_attempt.get("elevated")) if current else False
                log.warning("ChatGPT sign-in: nothing came back in %d min (the page %s)", SIGN_IN_TIMEOUT_S // 60,
                            "opened in a browser" if seen else "never opened in a browser")
                # (the error shows only the step's button, Continue with ChatGPT: the other ways come with a new try,
                # and an old address can't finish a sign-in that ended)
                if note == "https":
                    text = t("The sign-in didn't come back to IEUStudia: your browser tried https://, which IEUStudia "
                             "doesn't use. Click Continue with ChatGPT to start again; while it waits, use Paste the "
                             "address from your browser (the address of the error page it shows after you allow "
                             "IEUStudia).")
                elif note == "listen":
                    text = t("The sign-in didn't come back in time: IEUStudia can't listen for your browser on this "
                             "PC. Click Continue with ChatGPT to start again; while it waits, use Paste the address "
                             "from your browser (the address of the error page it shows).")
                elif seen:
                    text = t("The sign-in page was not completed in time. Click Continue with ChatGPT to start again. "
                             "If your browser shows an error page after you allow IEUStudia, copy its address and use "
                             "Paste the address from your browser (under Trouble?).")
                else:
                    text = (t("The sign-in page never opened in a browser. Click Continue with ChatGPT to start again; "
                              "if nothing opens, click Open in Microsoft Edge, or Copy link and paste it into your "
                              "browser's address bar.") if edge else
                            t("The sign-in page never opened in a browser. Click Continue with ChatGPT to start again; "
                              "if nothing opens, click Copy link and paste it into your browser's address bar."))
                    if elevated:                   # (said only then: a browser started from it may not show up)
                        text = f"{text} {admin_note()}"
                finish("error", text, code="timeout")
                return
            finish(result["state"], result.get("error", ""), result.get("email", ""), result.get("code", ""))
        except Exception as exc:  # noqa: BLE001 - shown in the set-up
            log.exception("ChatGPT sign-in failed")
            finish("error", t("Something went wrong while waiting for the sign-in ({kind}). Try again.",
                              kind=exc.__class__.__name__), code="other")
        finally:
            if server is not None:
                server.server_close()

    edge = _system("edge_available")                # "Open in Microsoft Edge" is offered
    elevated = _system("elevated")                  # run as administrator: said if the page doesn't show up
    with _lock:
        if _attempt.get("cancel") is cancel:
            _attempt.update(state="waiting", link=link, page=auth_url, opened=False, launched=None, result=result,
                            deliver=deliver, track=track, edge=edge, elevated=elevated, asks=0, asks_then=0, way="",
                            again=0)
    admin = ", run as administrator" if elevated else ""          # (for Copy details)
    first = ", registering IEUStudia" if client_id == FIRST_CLIENT else ""
    if server is not None:
        track("listening", f"127.0.0.1:{port}{first}{admin}")
    else:
        track("listening", f"refused: {refused}{admin}", note="listen")
    threading.Thread(target=run, name="chatgpt-sign-in", daemon=True).start()
    threading.Thread(target=_reach, args=(track,), name="chatgpt-reach", daemon=True).start()
    track("opening")
    out = _launch(open_browser, link)
    with _lock:
        seen = bool(_attempt.get("opened"))
    track("launched" if out["ok"] else "no_browser", ", ".join(x for x in (out["browser"], out["via"], out["why"]) if x),
          moves=not seen, launched=out["ok"], browser=out["browser"], via=out["via"], why=out["why"])
    return {"state": "waiting", "link": link, "page": auth_url, "launched": out["ok"], "browser": out["browser"],
            "via": out["via"], "why": out["why"], "edge": edge, "elevated": elevated}


def admin_note() -> str:
    """What to do when IEUStudia runs as administrator (osutil.elevated) and the sign-in page doesn't show up: a
    browser started from an app run so may not open (Firefox once did nothing at all). Said only then."""
    return t("IEUStudia is running as administrator, and a browser may not open from it. Close IEUStudia and open it "
             "again from the Start menu, without Run as administrator.")


def _waiting(*keys: str) -> Optional[dict[str, Any]]:
    """These parts of the sign-in under way, or None when none waits."""
    with _lock:
        if _attempt.get("state") != "waiting":
            return None
        return {k: _attempt.get(k) for k in keys}


def reopen_sign_in(open_browser: Optional[Callable[..., Any]] = None, way: str = "") -> dict[str, Any]:
    """The sign-in page once more for the sign-in under way (it didn't show up, or its tab was closed): the same
    link, so OpenAI's answer still comes back to it. ``open_browser(link, way=...)`` opens it one way
    (osutil.open_link).

    Another way each time while the page doesn't open (a browser can start and show nothing: stuck, showing its
    profile chooser, closed by Family Safety): the default browser, then Microsoft Edge, then Windows' own open. A way
    that brought the page is kept (its tab was only closed); one that can't start anything here is passed over in the
    same click. ``way``: that one only ("edge": Open in Microsoft Edge); when it can't start, nothing else changes.
    {"state": "waiting", "link", "page", "launched", "browser", "via" (the way used), "way", "again"}; nothing under
    way: {"state": ""}."""
    a = _waiting("link", "page", "track", "way", "via", "asks", "asks_then", "again", "cancel")
    if not a or not a["link"]:
        return {"state": ""}
    opener = open_browser or _open_link
    if way:
        ways = [way]
    else:
        last = a["way"] or _FIRST_WAY.get(str(a["via"] or ""), "shell")
        at = WAYS.index(last) if last in WAYS else len(WAYS) - 1
        if int(a["asks"] or 0) <= int(a["asks_then"] or 0):      # the page didn't open after it: the next way
            at += 1
        ways = [WAYS[(at + i) % len(WAYS)] for i in range(len(WAYS))]
    with _lock:
        asks = int(_attempt.get("asks") or 0)
    out, used = {"ok": False, "browser": "", "via": "", "why": ""}, ways[0]
    for used in ways:
        out = _launch(lambda link, w=used: opener(link, way=w), a["link"])
        if out["ok"]:
            break
    detail = f"{used}: " + (", ".join(x for x in (out["browser"], out["via"]) if x) or
                            ("started" if out["ok"] else "nothing started"))
    if way and not out["ok"]:                     # that way only, and it didn't start: the rest stays as it was
        a["track"]("asked again", detail, moves=False)
        return {"state": "waiting", "link": a["link"], "page": a["page"], "launched": False, "browser": out["browser"],
                "via": used, "way": used, "again": int(a["again"] or 0)}
    again = int(a["again"] or 0) + 1
    with _lock:
        if _attempt.get("cancel") is a["cancel"]:
            _attempt["asks_then"] = asks
    a["track"]("asked again", detail, moves=False, launched=out["ok"], browser=out["browser"], via=used, way=used,
               again=again)
    return {"state": "waiting", "link": a["link"], "page": a["page"], "launched": out["ok"], "browser": out["browser"],
            "via": used, "way": used, "again": again}


def cancel_sign_in() -> None:
    with _lock:
        if _attempt.get("state") == "waiting":
            _attempt["cancel"].set()
            _attempt["steps"].append((time.time(), "cancelled", ""))
            _attempt.update(state="", error="", code="", step="")
            _attempt.pop("link", None)
            _attempt.pop("page", None)


_PASTE_EDGES = "<>\"'`“”‘’«».,;"        # around a pasted address: quotes, brackets, the end of a sentence


def paste_address(text: str) -> dict[str, Any]:
    """"Paste the address from your browser": the address OpenAI sent the browser back to (its page may say it can't
    be reached, when something blocks 127.0.0.1) finishes the sign-in as the way back would. {"state": "done" |
    "error" (the sign-in failed: said like any other way back) | "refused" (not an address that can finish it), "error"}.

    Taken however it was copied: with or without http://, with 127.0.0.1 or localhost, with spaces or line breaks in
    or around it, in quotes, its & written as &amp;. The browser's way back keeps waiting meanwhile: whichever comes
    first finishes the sign-in, and the other gets the same outcome (an address pasted after the browser came back
    says done, never that something failed)."""
    raw = html.unescape("".join(str(text or "").split())).strip(_PASTE_EDGES)
    q = _query(raw)
    if not q.get("code") and not q.get("error"):
        return {"state": "refused", "error": t(
            "This isn't the address IEUStudia needs. After you allow IEUStudia on OpenAI's page, copy the whole address "
            "from your browser's address bar: it starts with http://127.0.0.1 and has code= in it.")}
    if not q.get("state"):
        return {"state": "refused", "error": t(
            "This address is cut short. Copy the whole address from your browser's address bar (it has state= in it "
            "too) and paste it here.")}
    with _lock:            # waiting, or already answered (by the browser, say): the same outcome again
        deliver = _attempt.get("deliver") if _attempt.get("state") == "waiting" or _attempt.get("result") else None
    if deliver is None:
        return {"state": "refused", "error": t(
            "No sign-in is waiting for this address any more. Click Continue with ChatGPT and try again.")}
    out = deliver(q, "pasted address")
    if out["state"] == "mismatch":                 # (said while this sign-in waits: its row has Open the page again)
        return {"state": "refused", "error": t(
            "This address belongs to an earlier sign-in. Copy the address from the sign-in page that opened last, or "
            "close this and click Open the page again.")}
    if out["state"] == "ended":
        return {"state": "refused", "error": t(
            "No sign-in is waiting for this address any more. Click Continue with ChatGPT and try again.")}
    return {"state": out["state"], "error": out.get("error", ""), "code": out.get("code", "")}


def _reach(track: Callable[..., None]) -> None:
    """Can IEUStudia itself reach OpenAI's sign-in? The browser may while IEUStudia can't (a firewall rule or a
    security program for one program, a proxy only the browser uses), and the sign-in can't finish without it: said
    while waiting. OpenAI's answer also shows how far this PC's clock is off."""
    import requests

    started = time.monotonic()
    why = ""
    try:
        r = _session().get(CONFIG_URL, timeout=REACH_TIMEOUT)
        _measure_clock(r)
        status_ = int(getattr(r, "status_code", 200) or 200)
        if status_ == 403:
            why = t("OpenAI refused the connection from this network (HTTP 403). If you use a VPN or proxy, turn it off "
                    "and try again; ChatGPT isn't offered in every country.")
        elif status_ >= 400:
            why = t("OpenAI's sign-in is having trouble right now (HTTP {status}). Try again in a few minutes.",
                    status=status_)
    except requests.RequestException as exc:
        why = _net_message(exc)
    except Exception:  # noqa: BLE001 - only a check
        log.debug("The ChatGPT reach check failed", exc_info=True)
        return
    off = clock_off()
    detail = (f"auth.openai.com {'reached' if not why else 'NOT reached'} in {(time.monotonic() - started) * 1000:.0f}"
              f" ms" + (f"; PC clock {off:+d} min off" if off else ""))
    track("reach", detail, moves=False, reach={"ok": not why, "why": why}, clock=off, clock_text=clock_message(off))


def details() -> str:
    """A short report of the sign-in for whoever helps (Copy details): the app, the system, the browsers, each step
    with its time, how it ended. Nothing secret: no address of OpenAI's page (it holds the sign-in's codes and your
    email), no codes, no tokens."""
    from . import __version__
    from .i18n import language

    try:
        from .osutil import default_browsers

        browsers = default_browsers()
    except Exception:  # noqa: BLE001 - a report without it
        browsers = {}
    rec = _load()
    with _lock:
        a = {k: _attempt.get(k) for k in ("state", "code", "browser", "via", "why", "note")}
        steps = list(_attempt.get("steps") or [])
    lines = [f"IEUStudia {__version__}, ChatGPT sign-in, {platform.platform()}, language {language()}"]
    if browsers:
        lines.append("Default browser: " + ", ".join(f"{k} {v}" for k, v in browsers.items()))
    lines.append(f"Kept here: signed in {'yes' if rec.get('refresh_token') else 'no'}, registered "
                 f"{'yes' if rec.get('client_id') else 'no'}, plan allowed "
                 f"{'yes' if PLAN_SCOPE in (rec.get('scopes') or []) else 'no'}"
                 + (", turned down once by OpenAI (kept)" if rec.get("client_refused") else "")
                 + (", an unreadable one kept aside" if not rec and (_dir() / UNREADABLE).is_file() else ""))
    if "server" in _clock:
        lines.append(f"PC clock against OpenAI's: {_skew():+.0f} s")
    if not steps:
        lines.append("No sign-in started since IEUStudia opened.")
    for at, name, detail in steps:      # (an error can name the account: "You signed in as …"; no email in the report)
        detail = _EMAIL.sub("(email)", detail or "")
        lines.append(f"{time.strftime('%H:%M:%S', time.localtime(at))} {name}" + (f": {detail}" if detail else ""))
    if a["state"]:
        lines.append(f"Now: {a['state']}" + (f" [{a['code']}]" if a.get("code") else ""))
    return "\n".join(lines)


class _FormError(RuntimeError):
    """OpenAI's token endpoint said no: its HTTP status, error code and description."""

    def __init__(self, status: int, code: str, description: str) -> None:
        super().__init__(description or code or f"HTTP {status}")
        self.status, self.code, self.description = status, code, description


def _post_form(url: str, data: dict[str, str]) -> dict[str, Any]:
    r = _session().post(url, data=data, timeout=30)
    _measure_clock(r)
    body = _json(r)
    if r.status_code != 200:
        err = body.get("error")
        code = err if isinstance(err, str) else str((err or {}).get("code") or "") if isinstance(err, dict) else ""
        desc = body.get("error_description") or (err.get("message") if isinstance(err, dict) else "") or ""
        raise _FormError(r.status_code, code or str(r.status_code), " ".join(str(desc).split())[:300])
    return body


def _token_failure(exc: _FormError) -> SignInError:
    """OpenAI refused to trade the sign-in's code for tokens: why, in plain words."""
    if exc.code == "invalid_grant":
        return SignInError(t("The sign-in code was already used or has expired (this happens when the page is opened "
                             "twice, or after a long wait). Click Continue with ChatGPT to try again."), "expired_code")
    if exc.code in _CLIENT_REFUSED:              # (said by the sign-in: _client_refused, kept the first time)
        return SignInError(f"OpenAI turned IEUStudia's client down: {exc.description or exc.code}", "client")
    if exc.status == 429:
        return SignInError(t("OpenAI is getting too many sign-ins right now. Wait a minute, then try again."), "service")
    if exc.status == 403:
        return SignInError(t("OpenAI refused the sign-in from this network (HTTP 403). If you use a VPN or proxy, turn "
                             "it off and try again; ChatGPT isn't offered in every country."), "blocked")
    if exc.status >= 500:
        return SignInError(t("OpenAI's sign-in had a problem on its side (HTTP {status}). Try again in a few minutes.",
                             status=exc.status), "service")
    return SignInError(t("OpenAI did not accept the sign-in ({why}). Try again; if it happens again, click Copy details "
                         "and send them when you ask for help.", why=exc.description or exc.code), "other")


def exchange_code(code: str, client_id: str, verifier: str, redirect_uri: str, nonce: str,
                  previous: Optional[dict[str, Any]] = None,
                  on_step: Callable[[str], None] = lambda s: None) -> dict[str, Any]:
    """Trade the callback's code for tokens, check the ID token, and keep the sign-in (encrypted). A refusal is a
    SignInError; the network failing, a requests error (said by the caller)."""
    try:
        tok = _post_form(TOKEN_URL, {"grant_type": "authorization_code", "client_id": client_id, "code": code,
                                     "code_verifier": verifier, "redirect_uri": redirect_uri, "resource": API})
    except _FormError as exc:
        log.warning("ChatGPT sign-in: the token endpoint said HTTP %s %s: %s", exc.status, exc.code, exc.description)
        raise _token_failure(exc) from exc
    on_step("verifying")
    claims = validate_id_token(tok.get("id_token", ""), client_id, nonce)
    previous = previous or {}
    if previous.get("subject") and previous.get("client_id") == client_id and previous["subject"] != claims["sub"]:
        raise SignInError(t("You signed in as {email}, but IEUStudia is connected to another ChatGPT account. To "
                            "switch, click Use another account or workspace.", email=claims["email"]) if claims.get("email")
                          else t("You signed in with another ChatGPT account than the one IEUStudia is connected to. "
                                 "To switch, click Use another account or workspace."), "other_account")
    rec = _record(tok, client_id, claims)
    try:
        with _lock:
            _save(rec)
    except OSError as exc:
        from .net import plain_error

        log.warning("ChatGPT sign-in: it could not be kept: %s", exc)
        raise SignInError(t("IEUStudia couldn't keep your ChatGPT sign-in on this PC. {error}",
                            error=plain_error(exc)), "save") from exc
    _client_accepted(client_id)                       # (the new record has no refusal noted)
    _models_cache_clear()
    return rec


def _record(tok: dict[str, Any], client_id: str, claims: dict[str, Any]) -> dict[str, Any]:
    now = time.time()
    return {"email": claims.get("email", ""), "issuer": AUTH, "subject": claims["sub"], "client_id": client_id,
            "id_token": tok.get("id_token", ""), "access_token": tok.get("access_token", ""),
            "refresh_token": tok.get("refresh_token", ""), "token_type": tok.get("token_type", "Bearer"),
            "expires_at": now + float(tok.get("expires_in") or 3600),
            "scopes": sorted(set(str(tok.get("scope") or "").split())), "saved_at": now}


# ----------------------------------------------------------------------------- the ID token
def _verify_rs256(signing_input: bytes, signature: bytes, n: int, e: int) -> bool:
    """RSASSA-PKCS1-v1_5 with SHA-256 (what RS256 means), checked with the public key (n, e)."""
    k = (n.bit_length() + 7) // 8
    if len(signature) != k:
        return False
    em = pow(int.from_bytes(signature, "big"), e, n).to_bytes(k, "big")
    digest_info = bytes.fromhex("3031300d060960864801650304020105000420") + hashlib.sha256(signing_input).digest()
    expected = b"\x00\x01" + b"\xff" * (k - len(digest_info) - 3) + b"\x00" + digest_info
    return hmac.compare_digest(em, expected)


def _keys() -> list[dict[str, Any]]:
    """OpenAI's published signing keys (kept for an hour). The network failing: a requests error (said by the
    caller); an answer that isn't the keys: a SignInError."""
    if not _jwks.get("keys") or time.monotonic() - _jwks.get("at", 0) > 3600:
        s = _session()
        conf = s.get(CONFIG_URL, timeout=30)
        _measure_clock(conf)
        try:
            if conf.status_code != 200:
                raise ValueError(f"HTTP {conf.status_code}")
            r = s.get(conf.json()["jwks_uri"], timeout=30)
            if r.status_code != 200:
                raise ValueError(f"HTTP {r.status_code}")
            keys = r.json().get("keys") or []
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            log.warning("ChatGPT sign-in: OpenAI's signing keys could not be read: %s", exc)
            raise SignInError(t("OpenAI's sign-in could not be checked: its keys didn't load ({why}). Try again in a "
                                "few minutes.", why=exc), "service") from exc
        _jwks.update(keys=keys, at=time.monotonic())
    return _jwks["keys"]


def validate_id_token(id_token: str, client_id: str, nonce: str) -> dict[str, Any]:
    """The ID token's claims, after checking its signature (OpenAI's published keys), issuer, audience, expiry and
    nonce. Anything wrong: a SignInError ("verify"), and the sign-in is not kept.

    The expiry is checked on OpenAI's clock, not this PC's: a PC clock hours off (a wrong time zone "fixed" by
    typing the time) mustn't fail every sign-in. The difference comes from the Date of OpenAI's answer that brought
    the token, or else from the token's own issue time (it was made a moment ago)."""
    try:
        head_b64, body_b64, sig_b64 = id_token.split(".")
        header = json.loads(_b64url_decode(head_b64))
        claims = json.loads(_b64url_decode(body_b64))
        if not isinstance(header, dict) or not isinstance(claims, dict):
            raise ValueError("not a token")
    except (ValueError, TypeError) as exc:
        raise SignInError(t("OpenAI's answer to the sign-in could not be read."), "verify") from exc
    if header.get("alg") != "RS256":
        raise SignInError(t("OpenAI's sign-in token uses an unexpected signature ({alg}).", alg=header.get("alg")),
                          "verify")
    key = next((k for k in _keys() if k.get("kid") == header.get("kid") and k.get("kty") == "RSA"), None)
    if key is None:
        _jwks.clear()                                        # keys rotate: fetch them once more
        key = next((k for k in _keys() if k.get("kid") == header.get("kid") and k.get("kty") == "RSA"), None)
    if key is None or not _verify_rs256(f"{head_b64}.{body_b64}".encode("ascii"), _b64url_decode(sig_b64),
                                        int.from_bytes(_b64url_decode(key["n"]), "big"),
                                        int.from_bytes(_b64url_decode(key["e"]), "big")):
        raise SignInError(t("OpenAI's sign-in token could not be verified."), "verify")
    aud = claims.get("aud")
    auds = aud if isinstance(aud, list) else [aud]
    if claims.get("iss") != AUTH or client_id not in auds or not claims.get("sub"):
        raise SignInError(t("OpenAI's sign-in token is not meant for IEUStudia."), "verify")
    try:
        iat, exp = float(claims.get("iat") or 0), float(claims.get("exp") or 0)
    except (TypeError, ValueError):
        iat, exp = 0.0, 0.0
    if "at" not in _clock and iat:                           # no Date seen: the token was made a moment ago
        _set_server(iat)
    if exp < _openai_now() - 60:
        raise SignInError(t("OpenAI's sign-in token has expired. Try again."), "verify")
    if not hmac.compare_digest(str(claims.get("nonce") or ""), nonce):
        raise SignInError(t("OpenAI's sign-in token does not belong to this sign-in. Try again."), "verify")
    return claims


# ----------------------------------------------------------------------------- tokens
_TERMINAL = {"invalid_grant", "invalid_refresh_token", "token_expired", "refresh_token_expired",
             "refresh_token_invalidated", "refresh_token_reused"}


def access_token() -> str:
    """A valid access token, renewed shortly before it expires (refreshes never overlap)."""
    return _token(force=False)


def _token(force: bool) -> str:
    """The access token; ``force``: renewed now, whatever its time says (OpenAI refused it: a PC clock set back
    after it came makes an expired one look valid)."""
    with _lock:
        rec = _load()
        if not rec.get("refresh_token"):
            raise RuntimeError(t("ChatGPT is not signed in. Settings → AI → Continue with ChatGPT."))
        if PLAN_SCOPE not in (rec.get("scopes") or []):
            raise RuntimeError(t("IEUStudia may not use your ChatGPT plan yet. Go to Settings → AI → Continue with "
                                 "ChatGPT and allow it."))
        if not force and rec.get("access_token") and float(rec.get("expires_at") or 0) - REFRESH_MARGIN_S > time.time():
            return rec["access_token"]
        try:
            tok = _post_form(TOKEN_URL, {"grant_type": "refresh_token", "client_id": rec["client_id"],
                                         "refresh_token": rec["refresh_token"], "resource": API})
        except _FormError as exc:
            log.warning("ChatGPT: renewing the sign-in: HTTP %s %s: %s", exc.status, exc.code, exc.description)
            if exc.code in _TERMINAL:
                rec.update(access_token="", refresh_token="")
                _save(rec)
                raise RuntimeError(t("Your ChatGPT sign-in has ended. Settings → AI → Continue with ChatGPT.")) from exc
            if exc.status == 429:
                raise RuntimeError(t("OpenAI is busy right now and didn't renew IEUStudia's access to ChatGPT. Wait a "
                                     "minute, then try again.")) from exc
            if exc.status >= 500:
                raise RuntimeError(t("ChatGPT's sign-in service had a problem while renewing your sign-in (HTTP "
                                     "{status}). Try again in a few minutes.", status=exc.status)) from exc
            raise RuntimeError(t("ChatGPT didn't renew your sign-in ({why}). If it keeps happening, sign in again: "
                                 "Settings → AI → Continue with ChatGPT.", why=exc.description or exc.code)) from exc
        rec.update(access_token=tok.get("access_token", ""), refresh_token=tok.get("refresh_token") or rec["refresh_token"],
                   expires_at=time.time() + float(tok.get("expires_in") or 3600),
                   scopes=sorted(set(str(tok.get("scope") or " ".join(rec.get("scopes") or [])).split())),
                   saved_at=time.time())
        rec.pop("client_refused", None)              # OpenAI took the registration: an earlier refusal counts no more
        _client_accepted(rec["client_id"])
        try:
            _save(rec)
        except OSError as exc:
            from .net import plain_error

            log.warning("ChatGPT: the renewed sign-in could not be kept: %s", exc)
            raise RuntimeError(t("IEUStudia couldn't save ChatGPT's renewed access on this PC. {error}",
                                 error=plain_error(exc))) from exc
        return rec["access_token"]


def _renewed() -> bool:
    """After OpenAI refused the access token (HTTP 401): renewed once. False when that can't be done (then the 401
    itself is said)."""
    try:
        _token(force=True)
        log.info("ChatGPT refused the access token: renewed it")
        return True
    except Exception as exc:  # noqa: BLE001 - the refusal is said instead
        log.info("ChatGPT refused the access token, and renewing it failed: %s", exc)
        return False


def sign_out() -> dict[str, Any]:
    """Revoke the renewable session at OpenAI (best effort) and forget the sign-in on this PC, so the next sign-in
    may also be another account. ``revoked`` False: OpenAI did not confirm it (disconnect IEUStudia in ChatGPT's
    settings to be sure)."""
    with _lock:
        rec = _load()
        revoked = not rec.get("refresh_token")
        if rec.get("refresh_token"):
            try:
                s = _session()
                conf = s.get(CONFIG_URL, timeout=20).json()
                r = s.post(conf["revocation_endpoint"], data={"token": rec["refresh_token"],
                                                              "token_type_hint": "refresh_token",
                                                              "client_id": rec.get("client_id", "")}, timeout=20)
                revoked = r.status_code == 200
            except Exception as exc:  # noqa: BLE001 - signing out locally anyway
                log.info("Revoking the ChatGPT session failed: %s", exc)
        try:
            (_dir() / "chatgpt.bin").unlink(missing_ok=True)
        except OSError:
            _save({})
    _models_cache_clear()
    return {"revoked": revoked}


# ----------------------------------------------------------------------------- models and requests
def _models_path() -> Path:
    return _dir() / "chatgpt-models.json"


def _models_cache_clear() -> None:
    try:
        _models_path().unlink(missing_ok=True)
    except OSError:
        pass


def models(refresh: bool = False) -> list[dict[str, str]]:
    """The models this account may use, in OpenAI's order: [{"slug", "display_name", "levels"}] (cached for a day).
    When the list can't be fetched (no network, a failure on OpenAI's side), an older cached list is used."""
    import requests

    path = _models_path()
    stale: list[dict[str, str]] = []
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
        stale = cached.get("models") or []
        if not refresh and time.time() - cached.get("at", 0) < MODELS_TTL_S and stale:
            return stale
    except (OSError, ValueError, AttributeError):
        pass
    try:
        r = _session().get(f"{API}/models", headers={"Authorization": f"Bearer {access_token()}"}, timeout=30)
        if r.status_code == 401 and _unauthorized(_json(r)) and _renewed():      # once, with a renewed token
            r = _session().get(f"{API}/models", headers={"Authorization": f"Bearer {access_token()}"}, timeout=30)
    except requests.RequestException as exc:
        if stale and not refresh:
            log.info("ChatGPT's model list could not be fetched (%s): the older list is used", exc)
            return stale
        raise
    if r.status_code != 200:
        log.warning("ChatGPT's model list: HTTP %s: %s", r.status_code, _logged_body(r))
        if aierrors.retryable(r.status_code) and r.status_code != 429:
            if stale and not refresh:
                return stale
            raise aierrors.ServiceError(aierrors.explain("ChatGPT", r.status_code), r.status_code)
        raise _api_failure(r.status_code, _json(r))
    rows = [{"slug": m["slug"], "display_name": m.get("display_name") or m["slug"],
             "levels": [x.get("effort") for x in m.get("supported_reasoning_levels") or [] if isinstance(x, dict)],
             **{k: m[k] for k in ("context_window", "effective_context_window_percent") if isinstance(m.get(k), int)}}
            for m in (_json(r).get("models") or []) if isinstance(m, dict) and m.get("slug")
            and m.get("visibility", "list") == "list"]           # levels: the reasoning efforts it takes (effort.py);
    # context_window: how much one request holds (chatlimits.py), when the list says
    path.write_text(json.dumps({"at": time.time(), "models": rows}), encoding="utf-8")
    return rows


def _json(r: Any) -> dict[str, Any]:
    try:
        body = r.json()
        return body if isinstance(body, dict) else {}
    except ValueError:
        return {}


class NotEligible(RuntimeError):
    """OpenAI doesn't let apps use this account's plan (Free and Go, as OpenAI decides; or a workspace whose plan or
    admin doesn't allow it). The set-up tells it apart by this class, not by the (translated) message."""


_PLAN_CODES = ("subscription_sharing_usage_limit_exceeded", "subscription_sharing_user_not_eligible",
               "subscription_sharing_usage_unavailable", "subscription_sharing_user_unavailable")
_REGION_CODES = ("unsupported_country_region_territory", "unsupported_country", "unsupported_region")


def _unauthorized(body: dict[str, Any]) -> bool:
    """A 401 that a renewed access token may cure (not one about the plan)."""
    err = body.get("error") if isinstance(body.get("error"), dict) else {}
    return err.get("code") not in _PLAN_CODES


def _api_failure(status: int, body: dict[str, Any]) -> RuntimeError:
    err = body.get("error") if isinstance(body.get("error"), dict) else {}
    kind = NotEligible if err.get("code") == "subscription_sharing_user_not_eligible" else RuntimeError
    return kind(_api_error(status, body))


def _api_error(status: int, body: dict[str, Any]) -> str:
    err = body.get("error") if isinstance(body.get("error"), dict) else {}
    code = err.get("code") or ""
    if code == "subscription_sharing_usage_limit_exceeded" or status == 429:
        return t("You have reached your ChatGPT usage limit for apps. See {url}.", url=USAGE_URL)
    # (worded so the page's notice doesn't take them for a sign-in or a connection problem: web/notices.js)
    if code == "subscription_sharing_user_not_eligible":
        return t("OpenAI doesn't let apps use the ChatGPT plan of this account or workspace. Plus and Pro can be used "
                 "(for Free and Go, OpenAI decides). If you have Plus or Pro in another account or workspace, switch "
                 "to it: Settings → AI → Use another account or workspace.")
    if code in _REGION_CODES:
        return t("OpenAI doesn't offer ChatGPT to apps in the country or region you are in, as OpenAI sees it (a VPN "
                 "or proxy changes that). If you use one, turn it off and try again.")
    if code in ("subscription_sharing_usage_unavailable", "subscription_sharing_user_unavailable") or status == 503:
        return t("ChatGPT can't check your plan right now. Try again in a few minutes.")
    if code == "subscription_sharing_invalid_user" or status == 401:
        return t("ChatGPT did not accept the sign-in. Settings → AI → Continue with ChatGPT.")
    detail = err.get("message") or body.get("detail") or f"HTTP {status}"
    return f"ChatGPT: {detail}"[:400]


def _parts(content: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The app's content blocks (Anthropic's shape) as the Responses API takes them: text, pictures, and a document (a
    PDF) as a file, never dropped on the way."""
    out = []
    for b in content:
        src = b.get("source") or {}
        if b.get("type") == "text" and b.get("text"):
            out.append({"type": "input_text", "text": b["text"]})
        elif b.get("type") == "image" and src.get("data"):
            out.append({"type": "input_image", "image_url": f"data:{src.get('media_type', 'image/jpeg')};base64,{src['data']}"})
        elif b.get("type") == "document" and src.get("data"):
            mime = src.get("media_type", "application/pdf")
            out.append({"type": "input_file", "filename": str(b.get("title") or "file.pdf"),
                        "file_data": f"data:{mime};base64,{src['data']}"})
    return out


OPTIONAL = ("prompt_cache_key",)      # left out (for this run of the app) once the account's API turns one down
_turned_down: set[str] = set()


def prompt_cache_key(system_prompt: str, content: list[dict[str, Any]]) -> str:
    """The same key for requests that begin alike (the instructions and the first part of the message, e.g. a lecture
    for each kind of its notes), so OpenAI sends them where that beginning is already cached."""
    first = next((b.get("text", "") for b in content if b.get("type") == "text"), "")
    return "ieustudia-" + hashlib.sha256(f"{system_prompt}\x00{first}".encode("utf-8", "ignore")).hexdigest()[:24]


def one_shot(system_prompt: str, content: list[dict[str, Any]], model: str = "sonnet", effort: str = "",
             cancel: Optional[threading.Event] = None, on_text: Optional[Callable[[str], None]] = None,
             timeout_s: float = 900, history: Optional[list[tuple[str, str]]] = None,
             cache_key: str = "") -> dict[str, Any]:
    """One streamed request on the user's ChatGPT plan: {"text", "usage", "seconds", "model"}. ``effort``: the
    reasoning level (effort.py; "" = the one the app's old model level meant, "haiku" / "sonnet" / "opus" -> low /
    medium / high); the ChatGPT model is the one chosen in Settings,
    else the account's first listed one. ``cache_key``: OpenAI's prompt-cache key (default: from how the request
    begins)."""
    import requests

    from . import appconfig

    started = time.monotonic()
    chosen = appconfig.load().codex_model
    for tries in range(1, len(aierrors.DELAYS) + 2):     # no older list to fall back on: asked again like the request
        try:
            listed = models()
            break
        except aierrors.ServiceError as exc:
            wait = aierrors.delay(tries - 1, None)
            if wait is None:
                raise aierrors.ServiceError(aierrors.explain("ChatGPT", exc.status, tries), exc.status, tries) from exc
            log.info("ChatGPT's model list: HTTP %s; asking again in %.0f s", exc.status, wait)
            aierrors.wait(wait, cancel)
        except requests.RequestException as exc:
            raise _unreachable(exc) from exc
    slug = chosen if chosen and any(m["slug"] == chosen for m in listed) else (listed[0]["slug"] if listed else "")
    if not slug:
        raise RuntimeError(t("ChatGPT lists no models for your account."))
    turns: list[dict[str, Any]] = []
    for q, a in (history or [])[-8:]:
        turns += [{"role": "user", "content": [{"type": "input_text", "text": q}]},
                  {"role": "assistant", "content": [{"type": "output_text", "text": a}]}]
    body: dict[str, Any] = {"model": slug, "instructions": system_prompt,
                            "input": turns + [{"role": "user", "content": _parts(content)}], "store": False,
                            "stream": True, "reasoning": {"effort": effort or EFFORT.get(model, "medium")},
                            "prompt_cache_key": cache_key or prompt_cache_key(system_prompt, content)}
    for name in _turned_down:
        body.pop(name, None)
    adjusted = failed = 0                                   # requests changed after a refusal; failures on OpenAI's side
    renew, renewed = False, False                           # a 401: the access token renewed once, then asked again
    while True:
        try:
            if renew:
                renew = False
                _token(force=True)                          # its own failure ("ended", the network) is said as it is
            text, usage = _stream(body, cancel, on_text, timeout_s)
            return {"text": text, "usage": usage, "cost_usd": None, "seconds": round(time.monotonic() - started, 1),
                    "model": slug}
        except _Unauthorized as exc:
            if renewed:
                raise RuntimeError(str(exc)) from exc
            log.info("ChatGPT refused the access token (HTTP 401): renewing it and asking again")
            renew = renewed = True
        except _Unsupported as exc:
            adjusted += 1
            if adjusted < 3 and "reasoning" in exc.param and "reasoning" in body:
                body.pop("reasoning")                       # this model takes no effort setting: ask without it
                continue
            if adjusted < 3 and exc.param in OPTIONAL and exc.param in body:
                _turned_down.add(exc.param)                 # only a hint for OpenAI's cache: ask without it
                body.pop(exc.param)
                continue
            raise RuntimeError(f"ChatGPT: {exc}") from exc
        except _Transient as exc:                           # a failure on OpenAI's side: ask again a little later
            if cancel is not None and cancel.is_set():
                raise Cancelled() from exc
            wait = aierrors.delay(failed, exc.retry_after)
            failed += 1
            if wait is None:
                raise exc.final(failed) from exc
            log.info("ChatGPT: HTTP %s; asking again in %.0f s (try %d)", exc.status, wait, failed + 1)
            aierrors.wait(wait, cancel)
        except requests.RequestException as exc:            # the sign-in renewal or the request itself
            if cancel is not None and cancel.is_set():
                raise Cancelled() from exc
            raise _unreachable(exc, body_size(body)) from exc


def _unreachable(exc: Exception, size: int = 0) -> RuntimeError:
    import requests

    log.info("ChatGPT could not be reached (%d bytes to send): %s", size, exc)
    if isinstance(exc, requests.exceptions.SSLError) and _clock_tls(exc):
        return RuntimeError(_clock_tls_message())
    from .net import cert_refused

    if isinstance(exc, requests.exceptions.SSLError) and cert_refused(exc):    # something changes secure connections
        return RuntimeError(t("A secure connection to ChatGPT was blocked or changed on the way. Antivirus HTTPS "
                              "scanning, a school or company network, or a Wi-Fi sign-in page can do this: open "
                              "chatgpt.com in your browser, then try again."))
    if size >= BIG_BODY and isinstance(exc, (requests.ConnectionError, requests.Timeout)):
        # most likely the upload: the pictures go up again with every question
        return RuntimeError(t("Your question and its pictures (~{mb} MB) didn't finish uploading to ChatGPT: your "
                              "connection may be slow for this much. Try again, or send fewer slides.",
                              mb=max(1, round(size / 1024 / 1024))))
    if isinstance(exc, requests.Timeout):
        return RuntimeError(t("ChatGPT did not answer in time. Check your internet connection and try again."))
    return RuntimeError(t("Can't reach ChatGPT. Check your internet connection and try again."))


class _Unsupported(RuntimeError):
    def __init__(self, message: str, param: str) -> None:
        super().__init__(message)
        self.param = param or ""


class _Unauthorized(RuntimeError):
    """ChatGPT refused the access token (HTTP 401, not about the plan): one_shot renews it once and asks again."""


class _Transient(Exception):
    """A 5xx answer, or a 429 that is not the plan's usage limit (before any text came): one_shot asks again."""

    def __init__(self, status: int, body: dict[str, Any], retry_after: Optional[float] = None) -> None:
        super().__init__(f"HTTP {status}")
        self.status, self.body, self.retry_after = status, body, retry_after

    def final(self, tries: int) -> RuntimeError:
        """What the student reads when the last try failed too."""
        err = self.body.get("error") if isinstance(self.body.get("error"), dict) else {}
        if err.get("code") in ("subscription_sharing_usage_unavailable", "subscription_sharing_user_unavailable"):
            return _api_failure(self.status, self.body)       # "ChatGPT can't check your plan right now"
        return aierrors.ServiceError(aierrors.explain("ChatGPT", self.status, tries), self.status, tries)


_SECRET = re.compile(r"(?i)bearer\s+\S+|\beyJ[\w-]{8,}\.[\w-]+\.[\w-]*|\b(?:sk|rt|at)-[\w-]{12,}")


def _logged_body(r: Any) -> str:
    """An error answer's body for the log: short, on one line, anything token-like left out."""
    try:
        text = r.text if isinstance(getattr(r, "text", None), str) else json.dumps(r.json())
    except (ValueError, TypeError, AttributeError):
        text = ""
    return _SECRET.sub("[hidden]", " ".join(str(text).split()))[:800] or "(empty)"


_SERVICE_CODES = ("server_error", "internal_error", "overloaded", "service_unavailable", "rate_limit_exceeded")


def _failed_early(err: dict[str, Any], parts: list[str]) -> None:
    """A stream that failed on OpenAI's side before any text: asked again (once text came, it is not)."""
    if not parts and str(err.get("code") or err.get("type") or "") in _SERVICE_CODES:
        log.warning("ChatGPT's answer failed on its side: %s", _SECRET.sub("[hidden]", json.dumps(err))[:800])
        raise _Transient(0, {"error": err})


UPLOAD_BPS = 150_000          # bytes a second a request's body is given to go up (a 3 Mbit/s upload sends ~390 KB/s)
BIG_BODY = 4 * 1024 * 1024    # a request this heavy that fails on the way is said to be too heavy, not "no internet"


def body_size(body: dict[str, Any]) -> int:
    return len(json.dumps(body))


def upload_timeout(body: dict[str, Any]) -> float:
    """The first timeout of a request: requests keeps it on the socket while the body goes up (one write, one deadline),
    so a heavy request needs more than 30 s on a slow upload (42 MB failed after 30 s at 3 Mbit/s, measured)."""
    return max(30.0, body_size(body) / UPLOAD_BPS)


def _stream(body: dict[str, Any], cancel: Optional[threading.Event], on_text: Optional[Callable[[str], None]],
            timeout_s: float) -> tuple[str, dict[str, int]]:
    headers = {"Authorization": f"Bearer {access_token()}", "Content-Type": "application/json",
               "Accept": "text/event-stream"}
    r = _session().post(f"{API}/responses", headers=headers, json=body, stream=True,
                        timeout=(upload_timeout(body), timeout_s))
    _measure_clock(r)                              # every answer keeps the clock check up to date
    finished = threading.Event()

    def stopper() -> None:                         # Stop: close the stream at once, even while it is quiet
        while not finished.wait(0.3):
            if cancel is not None and cancel.is_set():
                r.close()
                return

    if cancel is not None:
        threading.Thread(target=stopper, daemon=True).start()
    try:
        if r.status_code != 200:
            logged = _logged_body(r)
            data = _json(r)
            err = data.get("error") if isinstance(data.get("error"), dict) else {}
            param = str(err.get("param") or "")
            headers = getattr(r, "headers", None) or {}
            log.warning("ChatGPT answered HTTP %s (model %s, %s reasoning, %d characters sent, request %s): %s",
                        r.status_code, body.get("model"), (body.get("reasoning") or {}).get("effort", "no"),
                        len(json.dumps(body.get("input") or [])), headers.get("x-request-id", "-"), logged)
            if err.get("code") == "subscription_sharing_unsupported_capability" or (r.status_code == 400 and param in OPTIONAL):
                raise _Unsupported(err.get("message") or "unsupported", param)
            if r.status_code == 401 and _unauthorized(data):
                raise _Unauthorized(_api_error(401, data))
            if aierrors.retryable(r.status_code) and err.get("code") not in ("subscription_sharing_usage_limit_exceeded",
                                                                             "subscription_sharing_user_not_eligible"):
                raise _Transient(r.status_code, data, aierrors.retry_after(headers))
            raise _api_failure(r.status_code, data)
        parts: list[str] = []
        usage: dict[str, int] = {}
        done = False
        for raw in r.iter_lines():
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            if not raw or not raw.startswith(b"data:"):
                continue
            payload = raw[5:].strip()
            if payload == b"[DONE]":
                break
            try:
                ev = json.loads(payload)
            except ValueError:
                continue
            kind = ev.get("type", "")
            if kind == "response.output_text.delta":
                parts.append(ev.get("delta") or "")
                if on_text:
                    on_text("".join(parts))
            elif kind == "response.completed":
                u = (ev.get("response") or {}).get("usage") or {}
                usage = {"input_tokens": int(u.get("input_tokens") or 0), "output_tokens": int(u.get("output_tokens") or 0),
                         "cache_read_input_tokens": int((u.get("input_tokens_details") or {}).get("cached_tokens") or 0)}
                done = True
                break
            elif kind == "response.failed":
                err = (ev.get("response") or {}).get("error") or {}
                _failed_early(err, parts)
                raise (_api_failure(0, {"error": err}) if err.get("code") else
                       RuntimeError(f"ChatGPT: {err['message']}" if err.get("message") else
                                    t("ChatGPT: the answer failed")))
            elif kind == "response.incomplete":
                why = ((ev.get("response") or {}).get("incomplete_details") or {}).get("reason")
                if parts:
                    done = True
                    break
                raise RuntimeError(t("ChatGPT stopped before answering ({why}).", why=why) if why else
                                   t("ChatGPT stopped before answering (stopped early)."))
            elif kind == "error":
                err = ev.get("error") if isinstance(ev.get("error"), dict) else {"message": ev.get("message")}
                _failed_early(err, parts)
                raise _api_failure(0, {"error": err})
        if not done:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            raise RuntimeError(t("The connection to ChatGPT ended before the answer was complete. Try again."))
        return "".join(parts), usage
    except (Cancelled, RuntimeError, _Transient):
        raise
    except Exception as exc:                        # the stream broke: closed by Stop, or the network
        if cancel is not None and cancel.is_set():
            raise Cancelled() from exc
        raise RuntimeError(t("The connection to ChatGPT broke off ({error}). Try again.",
                             error=exc.__class__.__name__)) from exc
    finally:
        finished.set()
        r.close()
