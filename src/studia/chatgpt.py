"""ChatGPT through "Continue with ChatGPT": OpenAI's official way for open-source apps that run on the user's own PC to
use the user's ChatGPT plan (Sign in with ChatGPT, https://developers.openai.com/siwc/token-sharing-open-source).

The user signs in and agrees on OpenAI's own page in their browser. IEUStudia receives OAuth tokens that only allow
Responses API requests on the user's plan - not their ChatGPT conversations - keeps them encrypted with Windows DPAPI,
renews them, and sends requests to https://api.openai.com/v1/responses. No password, API key or extra program is
involved. The user can see and limit the app's usage, or disconnect it, in ChatGPT's settings.

Flow (OAuth 2 authorization code with PKCE, a loopback callback on 127.0.0.1, no client secret):
  first sign-in: client_id=dynamic_agent_client registers IEUStudia for the account; the issued client id is kept and
  reused; every installation has its own opaque host id (urn:uuid:...).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import http.server
import json
import logging
import os
import re
import secrets
import threading
import time
import urllib.parse
import uuid
import webbrowser
from pathlib import Path
from typing import Any, Callable, Optional

from . import aierrors
from .cancel import Cancelled
from .i18n import t

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

_lock = threading.RLock()          # the credential record and its (rotating) refresh token
_attempt: dict[str, Any] = {}      # the sign-in under way: {"state": "waiting"|"done"|"error", "error", "cancel"}
_jwks: dict[str, Any] = {}


def _session() -> Any:
    from .net import plain_session

    s = plain_session()
    s.headers["User-Agent"] = "IEUStudia (+https://github.com/mirzaisi/IEUStudia)"
    return s


# ----------------------------------------------------------------------------- the stored sign-in
def _dir() -> Path:
    from .paths import app_dir

    return app_dir()


def host_id() -> str:
    """This installation's opaque host id for OpenAI (created once, never derived from the user)."""
    path = _dir() / "chatgpt-host.json"
    try:
        hid = json.loads(path.read_text(encoding="utf-8")).get("ext_agent_host_id", "")
        if isinstance(hid, str) and hid.startswith("urn:uuid:"):
            return hid
    except (OSError, ValueError):
        pass
    hid = f"urn:uuid:{uuid.uuid4()}"
    path.write_text(json.dumps({"ext_agent_host_id": hid}), encoding="utf-8")
    return hid


def _protect(data: bytes) -> bytes:
    from .secrets_store import protect

    return protect(data)


def _unprotect(data: bytes) -> bytes:
    from .secrets_store import unprotect

    return unprotect(data)


def _load() -> dict[str, Any]:
    path = _dir() / "chatgpt.bin"
    if not path.is_file():
        return {}
    try:
        rec = json.loads(_unprotect(path.read_bytes()).decode("utf-8"))
        return rec if isinstance(rec, dict) else {}
    except (OSError, ValueError):
        log.warning("The ChatGPT sign-in could not be decrypted; sign in again")
        return {}


def _save(rec: dict[str, Any]) -> None:
    path = _dir() / "chatgpt.bin"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(_protect(json.dumps(rec).encode("utf-8")))
    os.replace(tmp, path)


def signed_in() -> bool:
    """Signed in, and allowed to use the ChatGPT plan."""
    rec = _load()
    return bool(rec.get("refresh_token") and PLAN_SCOPE in (rec.get("scopes") or []))


def status() -> dict[str, Any]:
    rec = _load()
    with _lock:
        attempt = {k: v for k, v in _attempt.items() if k in ("state", "error", "link", "page", "opened", "launched")}
        if attempt.get("state") != "waiting":
            attempt.pop("link", None)
            attempt.pop("page", None)
    return {"signed_in": bool(rec.get("refresh_token")), "email": rec.get("email", ""),
            "plan_allowed": PLAN_SCOPE in (rec.get("scopes") or []), "registered": bool(rec.get("client_id")),
            "sign_in": attempt}


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


def _launch(open_browser: Callable[[str], Any], link: str) -> bool:
    """Ask for the sign-in page in the browser; False when no browser could be started (None: the opener doesn't
    say, taken as started)."""
    try:
        return open_browser(link) is not False
    except Exception as exc:  # noqa: BLE001 - the set-up offers the link to copy
        log.warning("ChatGPT sign-in: no browser could be started: %s", exc)
        return False


def start_sign_in(emit: Callable[[dict[str, Any]], None] = lambda e: None, *, consent: bool = False,
                  open_browser: Callable[[str], Any] = webbrowser.open) -> dict[str, Any]:
    """Open OpenAI's sign-in page in the browser and wait (in the background) for it to come back to IEUStudia.

    The browser is sent to a page of IEUStudia's own first (``link``, on 127.0.0.1), which forwards it to OpenAI.
    That way IEUStudia knows when the page really opened in a browser (``opened``: on some PCs nothing opens, or it
    opens behind the window). ``page`` is OpenAI's page itself, to copy into a browser by hand: it works even where
    something blocks the first link. The outcome goes to ``emit`` as
    {"type": "chatgpt", "state": "done"|"error", "error", "email"}; the page opening as
    {"type": "chatgpt", "state": "waiting", "opened": True}."""
    with _lock:
        if _attempt.get("state") == "waiting":
            _attempt["cancel"].set()                     # a new click replaces an attempt that was left open
        cancel = threading.Event()
        _attempt.clear()
        _attempt.update(state="", error="", cancel=cancel)      # "waiting" once IEUStudia listens for the browser
    rec = _load()
    client_id = rec.get("client_id") or FIRST_CLIENT
    state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    result: dict[str, Any] = {}                    # {"state": "done"|"error", "error", "email"} once the browser is back

    def complete(q: dict[str, str]) -> dict[str, Any]:
        """What the callback brought: tokens kept, or why not."""
        if q.get("error"):
            return {"state": "error", "error": t("You didn't allow IEUStudia to use your ChatGPT plan.")
                    if q["error"] == "access_denied" else
                    t("OpenAI's sign-in did not finish: {why}", why=q.get("error_description") or q["error"])}
        if client_id != FIRST_CLIENT and q.get("client_id") and q["client_id"] != client_id:
            return {"state": "error", "error": t("OpenAI returned a different app registration. Try again.")}
        issued = q.get("client_id") or (client_id if client_id != FIRST_CLIENT else "")
        if not issued:
            return {"state": "error", "error": t("OpenAI did not finish registering IEUStudia for your account. Try again.")}
        try:
            new = exchange_code(q.get("code", ""), issued, verifier, redirect_uri, nonce, previous=rec)
        except Exception as exc:  # noqa: BLE001 - shown in the set-up and in the browser
            log.warning("ChatGPT sign-in failed: %s", exc)
            return {"state": "error", "error": str(exc)[:300] or t("Signing in did not work.")}
        return {"state": "done", "error": "", "email": new.get("email", "")}

    claim = threading.Lock()                       # one callback is taken (requests come on threads of their own)

    def opened() -> None:
        with _lock:
            first = _attempt.get("cancel") is cancel and not _attempt.get("opened")
            if first:
                _attempt["opened"] = True
        if first:
            log.info("ChatGPT sign-in: the browser opened the sign-in page")
            emit({"type": "chatgpt", "state": "waiting", "opened": True, "error": "", "email": ""})

    class Handler(http.server.BaseHTTPRequestHandler):
        timeout = 15                               # a connection a browser opens ahead and never uses holds nothing up

        def do_GET(self) -> None:  # noqa: N802 - http.server's naming
            url = urllib.parse.urlsplit(self.path)
            if url.path == START:                  # our own link: on to OpenAI's sign-in page
                if result or cancel.is_set():
                    self._page(t("Sign-in not completed"), t("This sign-in has ended. Start it again from IEUStudia."), 410)
                    return
                opened()
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
            q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
            with claim:
                if result or not hmac.compare_digest(q.get("state", ""), state):
                    self._page(t("Sign-in not completed"), t("This page did not come from the sign-in IEUStudia "
                               "started. Close it and try again from IEUStudia."), 400)
                    return
                opened()                           # a copied OpenAI link can come back without our page
                out = complete(q)                  # before answering, so the page tells the truth
                result.update(out)
            if out["state"] == "done":
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

    try:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    except OSError as exc:                         # security software that forbids listening, even on 127.0.0.1
        log.warning("ChatGPT sign-in: could not wait for the browser on 127.0.0.1: %s", exc)
        why = t("IEUStudia could not wait for the browser to come back ({why}). A security program may be blocking "
                "it; allow IEUStudia in it and try again.", why=exc)
        with _lock:
            if _attempt.get("cancel") is cancel:
                _attempt.update(state="error", error=why)
        raise RuntimeError(why) from exc
    server.timeout = 0.5
    port = server.server_address[1]
    redirect_uri = f"http://127.0.0.1:{port}{CALLBACK}"
    link = f"http://127.0.0.1:{port}{START}"
    auth_url = authorize_url(client_id=client_id, redirect_uri=redirect_uri, state=state, nonce=nonce,
                             challenge=challenge, rec=rec, consent=consent)

    def finish(state_: str, error: str = "", email: str = "") -> None:
        with _lock:
            if _attempt.get("cancel") is cancel:
                _attempt.update(state=state_, error=error)
        emit({"type": "chatgpt", "state": state_, "error": error, "email": email})

    def run() -> None:
        try:
            started = time.monotonic()
            while not result and not cancel.is_set() and time.monotonic() - started < SIGN_IN_TIMEOUT_S:
                server.handle_request()
            if cancel.is_set() and not result:
                log.info("ChatGPT sign-in: stopped before it came back")
                return
            if not result:
                with _lock:
                    seen = bool(_attempt.get("opened")) if _attempt.get("cancel") is cancel else True
                log.warning("ChatGPT sign-in: nothing came back in %d min (the page %s)", SIGN_IN_TIMEOUT_S // 60,
                            "opened in a browser" if seen else "never opened in a browser")
                finish("error", t("The sign-in page was not completed in time. Try again.") if seen else
                       t("The sign-in page never opened in a browser. Try again; if nothing opens, click Copy link "
                         "and paste it into your browser's address bar."))
                return
            log.info("ChatGPT sign-in: %s", "signed in" if result["state"] == "done" else "came back without it")
            finish(result["state"], result.get("error", ""), result.get("email", ""))
        except Exception as exc:  # noqa: BLE001 - shown in the set-up
            log.warning("ChatGPT sign-in failed: %s", exc)
            finish("error", str(exc)[:300] or t("Signing in did not work."))
        finally:
            server.server_close()

    with _lock:
        if _attempt.get("cancel") is cancel:
            _attempt.update(state="waiting", link=link, page=auth_url, opened=False, launched=False)
    threading.Thread(target=run, name="chatgpt-sign-in", daemon=True).start()
    launched = _launch(open_browser, link)
    log.info("ChatGPT sign-in: waiting on 127.0.0.1:%d; %s", port,
             "the browser was asked to open the page" if launched else "no browser could be started")
    with _lock:
        if _attempt.get("cancel") is cancel:
            _attempt["launched"] = launched
    return {"state": "waiting", "link": link, "page": auth_url, "launched": launched}


def reopen_sign_in(open_browser: Callable[[str], Any] = webbrowser.open) -> dict[str, Any]:
    """The sign-in page once more for the sign-in under way (it didn't show up, or its tab was closed): the same
    link, so OpenAI's answer still comes back to it. Nothing under way: {"state": ""}."""
    with _lock:
        waiting = _attempt.get("state") == "waiting"
        link, page = (_attempt.get("link", ""), _attempt.get("page", "")) if waiting else ("", "")
    if not link:
        return {"state": ""}
    launched = _launch(open_browser, link)
    log.info("ChatGPT sign-in: the page asked for again (%s)", "started" if launched else "no browser could be started")
    return {"state": "waiting", "link": link, "page": page, "launched": launched}


def cancel_sign_in() -> None:
    with _lock:
        if _attempt.get("state") == "waiting":
            _attempt["cancel"].set()
            _attempt.update(state="", error="")
            _attempt.pop("link", None)
            _attempt.pop("page", None)


def _post_form(url: str, data: dict[str, str]) -> dict[str, Any]:
    r = _session().post(url, data=data, timeout=30)
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code != 200:
        code = body.get("error") if isinstance(body.get("error"), str) else (body.get("error") or {}).get("code", "")
        err = RuntimeError(body.get("error_description") or code or f"HTTP {r.status_code}")
        err.code = code or str(r.status_code)  # type: ignore[attr-defined]
        raise err
    return body


def exchange_code(code: str, client_id: str, verifier: str, redirect_uri: str, nonce: str,
                  previous: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Trade the callback's code for tokens, check the ID token, and keep the sign-in (encrypted)."""
    tok = _post_form(TOKEN_URL, {"grant_type": "authorization_code", "client_id": client_id, "code": code,
                                 "code_verifier": verifier, "redirect_uri": redirect_uri, "resource": API})
    claims = validate_id_token(tok.get("id_token", ""), client_id, nonce)
    previous = previous or {}
    if previous.get("subject") and previous.get("client_id") == client_id and previous["subject"] != claims["sub"]:
        raise RuntimeError(t("You signed in with a different ChatGPT account than before. Sign out first to switch."))
    rec = _record(tok, client_id, claims)
    with _lock:
        _save(rec)
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
    if not _jwks.get("keys") or time.time() - _jwks.get("at", 0) > 3600:
        s = _session()
        conf = s.get(CONFIG_URL, timeout=30)
        conf.raise_for_status()
        r = s.get(conf.json()["jwks_uri"], timeout=30)
        r.raise_for_status()
        _jwks.update(keys=r.json().get("keys") or [], at=time.time())
    return _jwks["keys"]


def validate_id_token(id_token: str, client_id: str, nonce: str) -> dict[str, Any]:
    """The ID token's claims, after checking its signature (OpenAI's published keys), issuer, audience, expiry and
    nonce. Anything wrong: a RuntimeError, and the sign-in is not kept."""
    try:
        head_b64, body_b64, sig_b64 = id_token.split(".")
        header = json.loads(_b64url_decode(head_b64))
        claims = json.loads(_b64url_decode(body_b64))
    except (ValueError, TypeError) as exc:
        raise RuntimeError(t("OpenAI's answer to the sign-in could not be read.")) from exc
    if header.get("alg") != "RS256":
        raise RuntimeError(t("OpenAI's sign-in token uses an unexpected signature ({alg}).", alg=header.get("alg")))
    key = next((k for k in _keys() if k.get("kid") == header.get("kid") and k.get("kty") == "RSA"), None)
    if key is None:
        _jwks.clear()                                        # keys rotate: fetch them once more
        key = next((k for k in _keys() if k.get("kid") == header.get("kid") and k.get("kty") == "RSA"), None)
    if key is None or not _verify_rs256(f"{head_b64}.{body_b64}".encode("ascii"), _b64url_decode(sig_b64),
                                        int.from_bytes(_b64url_decode(key["n"]), "big"),
                                        int.from_bytes(_b64url_decode(key["e"]), "big")):
        raise RuntimeError(t("OpenAI's sign-in token could not be verified."))
    aud = claims.get("aud")
    auds = aud if isinstance(aud, list) else [aud]
    if claims.get("iss") != AUTH or client_id not in auds or not claims.get("sub"):
        raise RuntimeError(t("OpenAI's sign-in token is not meant for IEUStudia."))
    if float(claims.get("exp") or 0) < time.time() - 60:
        raise RuntimeError(t("OpenAI's sign-in token has expired. Try again."))
    if not hmac.compare_digest(str(claims.get("nonce") or ""), nonce):
        raise RuntimeError(t("OpenAI's sign-in token does not belong to this sign-in. Try again."))
    return claims


# ----------------------------------------------------------------------------- tokens
_TERMINAL = {"invalid_grant", "invalid_refresh_token", "token_expired", "refresh_token_expired",
             "refresh_token_invalidated", "refresh_token_reused"}


def access_token() -> str:
    """A valid access token, renewed shortly before it expires (refreshes never overlap)."""
    with _lock:
        rec = _load()
        if not rec.get("refresh_token"):
            raise RuntimeError(t("ChatGPT is not signed in. Settings → AI → Continue with ChatGPT."))
        if PLAN_SCOPE not in (rec.get("scopes") or []):
            raise RuntimeError(t("IEUStudia may not use your ChatGPT plan yet. Go to Settings → AI → Continue with "
                                 "ChatGPT and allow it."))
        if rec.get("access_token") and float(rec.get("expires_at") or 0) - REFRESH_MARGIN_S > time.time():
            return rec["access_token"]
        try:
            tok = _post_form(TOKEN_URL, {"grant_type": "refresh_token", "client_id": rec["client_id"],
                                         "refresh_token": rec["refresh_token"], "resource": API})
        except RuntimeError as exc:
            if getattr(exc, "code", "") in _TERMINAL:
                rec.update(access_token="", refresh_token="")
                _save(rec)
                raise RuntimeError(t("Your ChatGPT sign-in has ended. Settings → AI → Continue with ChatGPT.")) from exc
            raise
        rec.update(access_token=tok.get("access_token", ""), refresh_token=tok.get("refresh_token") or rec["refresh_token"],
                   expires_at=time.time() + float(tok.get("expires_in") or 3600),
                   scopes=sorted(set(str(tok.get("scope") or " ".join(rec.get("scopes") or [])).split())),
                   saved_at=time.time())
        _save(rec)
        return rec["access_token"]


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
    """OpenAI doesn't let apps use this account's plan (Free and Go, as OpenAI decides). The set-up tells it apart by
    this class, not by the (translated) message."""


def _api_failure(status: int, body: dict[str, Any]) -> RuntimeError:
    err = body.get("error") if isinstance(body.get("error"), dict) else {}
    kind = NotEligible if err.get("code") == "subscription_sharing_user_not_eligible" else RuntimeError
    return kind(_api_error(status, body))


def _api_error(status: int, body: dict[str, Any]) -> str:
    err = body.get("error") if isinstance(body.get("error"), dict) else {}
    code = err.get("code") or ""
    if code == "subscription_sharing_usage_limit_exceeded" or status == 429:
        return t("You have reached your ChatGPT usage limit for apps. See {url}.", url=USAGE_URL)
    if code == "subscription_sharing_user_not_eligible":
        return t("OpenAI doesn't let apps like IEUStudia use your ChatGPT plan. ChatGPT Plus and Pro can be used "
                 "(for Free and Go, OpenAI decides).")
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
    while True:
        try:
            text, usage = _stream(body, cancel, on_text, timeout_s)
            return {"text": text, "usage": usage, "cost_usd": None, "seconds": round(time.monotonic() - started, 1),
                    "model": slug}
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
