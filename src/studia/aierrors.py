"""When the AI service itself fails: which answers are worth another try, how long to wait before it, and what the
student is told.

An HTTP 5xx answer (or Claude's "overloaded", Codex's "unexpected status 500") means the service had a problem on its
side: the request was fine and nothing the student did caused it. A 429 that is not the plan's usage limit asks the
app to slow down. "Continue with ChatGPT" (chatgpt.py) asks again itself, after a few seconds and as long as the
answer's Retry-After says; the Claude Code and Codex CLIs already do that inside, so for them the failure is only
explained. Either way the student reads what happened, that it is on the service's side, and what to do, instead of
"ChatGPT: HTTP 500".
"""

from __future__ import annotations

import email.utils
import re
import threading
import time
from typing import Any, Optional

from .cancel import Cancelled
from .i18n import t, tn

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504, 520, 521, 522, 523, 524, 529})
DELAYS = (4.0, 12.0, 30.0)        # the waits before the 2nd, 3rd and 4th try
MAX_WAIT_S = 90.0                 # a longer Retry-After: not waited for (the student is told to wait instead)


class ServiceError(RuntimeError):
    """The AI service failed on its side (5xx, overloaded) or turned the request away for now (429): not the
    student's doing, worth trying again later. ``status``: the HTTP status (0 = not known), ``tries``: how many times
    the app asked. The pages can tell it by ``kind`` ("service")."""

    kind = "service"

    def __init__(self, message: str, status: int = 0, tries: int = 1) -> None:
        super().__init__(message)
        self.status, self.tries = status, tries


def retryable(status: int) -> bool:
    return status in RETRY_STATUSES


def retry_after(headers: Any) -> Optional[float]:
    """The seconds an answer's Retry-After header asks for (a number or an HTTP date), None without one."""
    try:
        value = str((headers or {}).get("Retry-After") or (headers or {}).get("retry-after") or "").strip()
    except AttributeError:
        return None
    if not value:
        return None
    if re.fullmatch(r"\d+(\.\d+)?", value):
        return float(value)
    try:
        when = email.utils.parsedate_to_datetime(value)
        return max(0.0, when.timestamp() - time.time())
    except (TypeError, ValueError, IndexError, OverflowError):
        return None


def delay(attempt: int, asked: Optional[float]) -> Optional[float]:
    """How long to wait before try ``attempt + 2`` (``attempt``: 0 after the first failure), or None: no more tries
    (all used, or the service asks for a longer wait than is worth holding the request for)."""
    if attempt >= len(DELAYS):
        return None
    if asked is not None and asked > MAX_WAIT_S:
        return None
    return max(DELAYS[attempt], asked or 0.0)


def wait(seconds: float, cancel: Optional[threading.Event]) -> None:
    """Wait ``seconds``; Stop (``cancel``) ends the wait at once with Cancelled."""
    if cancel is None:
        time.sleep(seconds)
    elif cancel.wait(seconds):
        raise Cancelled()


def explain(ai: str, status: int = 0, tries: int = 1) -> str:
    """The plain words for a failure on the AI service's side: what happened, that it is not the student's doing,
    what to do."""
    if status == 429:
        first = t("{ai} is getting too many requests right now and asked IEUStudia to slow down.", ai=ai)
    elif status:
        first = t("{ai} had a problem on its side and did not answer (error {code}).", ai=ai, code=status)
    else:
        first = t("{ai} had a problem on its side and did not answer.", ai=ai)
    parts = [first, t("This was not caused by you or by IEUStudia.")]
    if tries > 1:
        parts.append(tn(tries, "IEUStudia asked {n} time.", "IEUStudia asked {n} times.", n=tries))
    parts.append(t("Wait a few minutes, then try again. If it keeps happening, try a smaller request (fewer weeks, "
                   "files or slides) or a lower reasoning level."))
    return " ".join(parts)


_STATUS = re.compile(r"\b(?:status|error|http)[\s:=]*(5\d\d|429)\b|\b(5\d\d|429)\s+(?:internal|bad gateway|service|"
                     r"gateway|too many|overloaded)", re.I)
_SERVICE_WORDS = ("internal server error", "overloaded", "server_error", "server error", "high demand",
                  "service unavailable", "bad gateway", "gateway timeout", "temporarily unavailable", "api_error")


def from_message(ai: str, message: str) -> Optional[ServiceError]:
    """A CLI's error text (Claude Code's "API Error: 500 …", "529 Overloaded"; Codex's "unexpected status 503 …")
    as a ServiceError, or None when it is not a failure on the service's side."""
    m = message or ""
    low = m.lower()
    found = _STATUS.search(m)
    status = int(found.group(1) or found.group(2)) if found else 0
    if status == 429 and ("usage limit" in low or "limit reached" in low or "hit your" in low):
        return None                                        # the plan's usage limit: explained elsewhere
    if not status and not any(w in low for w in _SERVICE_WORDS):
        return None
    if status == 529 or (not status and "overloaded" in low):
        status = 529
    return ServiceError(explain(ai, status), status)
