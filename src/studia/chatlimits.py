"""How much one request to each AI can carry, as measured in November 2026, so that a chat sends all of the chosen
slides and files the way each AI really takes them (not a ZIP: none of these four can open one).

- **Claude** (Claude Code, as ai.py runs it): Sonnet and Opus have a 1M-token window and take up to 600 pictures or PDF
  pages per request; Haiku 4.5 has 200k and 100, and Claude Code silently keeps only the last 80 of 101 pictures, so
  the app stays at 80 there. PDFs go natively as document blocks (each page as text and as a picture). Anthropic's
  Messages API takes requests of up to 32 MB, which caps the pictures more than their number does; Claude Code sends
  the whole conversation with every question, so everything in it counts (the instructions, the first message's
  pictures, the notes and the pictures added with later questions, the answers).
- **ChatGPT** ("Continue with ChatGPT"): 400 tiny pictures in one request all read correctly (measured Nov 2026; 1,500
  documented, not tried); the model's own window (272k, or 500k on the GPT-6.1 models) decides first for real pages.
  Its file input takes a PDF but gives the model only the PDF's text (1 page: 71 tokens, each further page about 24,
  measured), so diagrams and scanned pages would be lost: its PDFs go as page pictures. A request must be uploaded
  within the connection's timeout: a 42 MB request failed on a 3 Mbit/s upload (chatgpt._stream now gives a large
  body more time), and a chat sends its pictures again with every question, so its pictures stay at 16 MB.
- **Codex CLI**: pictures only (``-i``), 620 read in one request; the window as for ChatGPT. It too sends the
  pictures again with every question.
- **Offline model**: text only, a few thousand tokens per request.

A chat's material that doesn't fit in one request is read in parts first ("Read everything", chatread.py): the slides
and files that don't go whole, and for the whole course the lectures whose full transcripts don't fit next to the rest
(chat.py); each part respects the same limits.
"""

from __future__ import annotations

import dataclasses as dc
import json
import os
from pathlib import Path
from typing import Any

MB = 1024 * 1024


@dc.dataclass(frozen=True)
class Limits:
    provider: str
    model: str
    window: int               # tokens in the model's context window
    context: int              # what a chat's material may take (the rest: the conversation and the answer)
    pictures: int             # pictures (and native PDF pages) in one request; 0: it can't see pictures
    pdf: bool                 # takes PDFs as files (document blocks)
    request_bytes: int        # the most one request may weigh, as the app counts it: its pictures and PDFs (base64)
                              # and its text (Claude: the whole conversation so far)
    batch_tokens: int         # one reading batch's material ("Read everything")
    batch_pictures: int       # pictures in one reading batch (its notes must fit in one answer too)

    @property
    def key(self) -> str:
        """Notes read by this AI are kept under this (another AI reads them again)."""
        return f"{self.provider}:{self.model}" if self.model else self.provider


CLAUDE_WINDOWS = {"haiku": 200_000, "sonnet": 1_000_000, "opus": 1_000_000}
CLAUDE_PICTURES = {"haiku": 80, "sonnet": 500, "opus": 500}     # Haiku: Claude Code keeps only the last 80
CHATGPT_PICTURES = 400        # measured on the sign-in endpoint (all 400 read); 1,500 documented, not tried
CODEX_PICTURES = 500          # 620 measured through the Codex CLI
OPENAI_WINDOW = 272_000       # the GPT-6 and GPT-5.6 models (the GPT-6.1 ones: 500k)
CLAUDE_REQUEST = 32 * MB      # Anthropic's limit on one request (the whole conversation, as Claude Code sends it)
CLAUDE_BYTES = 28 * MB        # what the app lets one request weigh: 4 MB left for JSON's own weight and estimates
CONVERSATION_BYTES = 1 * MB   # kept free in a chat's first request for the questions and answers that follow
OPENAI_BYTES = 16 * MB        # a ChatGPT or Codex chat uploads its pictures again with every question (3 Mbit/s:
                              # about 40 s): the rest is read in batches once instead
SHARE = 0.85                  # of a window a chat's material may take (the conversation and the answer need the rest)
LOCAL_CONTEXT = 150_000       # the offline model searches the material for each question: it never reads it whole


def text_bytes(text: str) -> int:
    """What text weighs in a request: UTF-8, and a little more for JSON's escapes (line breaks, quotes)."""
    return len(text.encode("utf-8")) * 21 // 20 if text else 0


def block_bytes(block: dict[str, Any]) -> int:
    """What one content block weighs in a request (a picture or a PDF: its base64; text: as above)."""
    data = (block.get("source") or {}).get("data")
    if isinstance(data, str):
        return len(data) + 120
    return text_bytes(str(block.get("text") or "")) + 40


def _openai_window(provider: str) -> int:
    """The chosen ChatGPT model's window, from the account's model list (Codex's, or "Continue with ChatGPT"'s);
    272k when it isn't known."""
    from . import ai, appconfig

    # Codex: the model it really runs (one it turned down runs as its default: ai._codex_model)
    chosen = ai._codex_model() if provider == "codex" else appconfig.load().codex_model
    rows: list[dict[str, Any]] = []
    try:
        if provider == "codex":
            path = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "models_cache.json"
            rows = [m for m in json.loads(path.read_text(encoding="utf-8")).get("models", []) if isinstance(m, dict)]
        else:
            from . import chatgpt

            cached = json.loads(chatgpt._models_path().read_text(encoding="utf-8"))
            rows = [m for m in cached.get("models") or [] if isinstance(m, dict)]
    except (OSError, ValueError, AttributeError, TypeError):
        rows = []
    listed = [m for m in rows if m.get("visibility", "list") == "list"]
    pick = next((m for m in listed if chosen and m.get("slug") == chosen), None)
    # the model itself when chosen; else the smallest listed (the default one is the account's to pick). Codex: a
    # chosen model it hasn't answered with yet may be turned down, and the same request then goes to its default, so
    # until then the smallest listed counts (the default's is not known: Codex picks it)
    trusted = pick is not None and (provider != "codex" or chosen in ai.codex_accepted())
    sized = [_effective(m) for m in ([pick] if trusted else listed) if isinstance(m.get("context_window"), int)]
    if not sized:
        return OPENAI_WINDOW
    return max(32_000, min(sized))


def _effective(model: dict[str, Any]) -> int:
    """A listed model's window as it can really be used (Codex lists the share it keeps: 95%)."""
    return int(model["context_window"]) * min(100, int(model.get("effective_context_window_percent") or 95)) // 100


def of(provider: str = "", model: str = "") -> Limits:
    """The limits of the AI ``provider`` (default: the chosen one) with Claude's model ``model`` ("haiku", "sonnet",
    "opus"; the others have one model per account, chosen in Settings)."""
    from . import ai

    p = provider or ai.provider()
    if p == "claude":
        m = model if model in CLAUDE_WINDOWS else "sonnet"
        window = CLAUDE_WINDOWS[m]
        pics = CLAUDE_PICTURES[m]
        return Limits(p, m, window, int(window * SHARE) if window > 200_000 else 160_000, pics, True, CLAUDE_BYTES,
                      batch_tokens=120_000, batch_pictures=min(60, pics))
    if p in ("chatgpt", "codex"):
        window = _openai_window(p)
        return Limits(p, "", window, int(window * 0.8), CODEX_PICTURES if p == "codex" else CHATGPT_PICTURES, False,
                      OPENAI_BYTES,
                      batch_tokens=min(120_000, window // 2), batch_pictures=60)
    if p == "local":
        from . import appconfig, local_llm

        key = appconfig.load().local_model
        try:
            room = local_llm.budget(key)
        except Exception:  # noqa: BLE001 - not downloaded or no card: the smallest
            room = 1400
        # a batch: the instructions (~250 tokens) and the notes it writes need room next to the pages
        return Limits(p, key, room, LOCAL_CONTEXT, 0, False, 0, batch_tokens=max(500, room - 700), batch_pictures=0)
    return Limits(p, "", 200_000, 150_000, 60, False, CLAUDE_BYTES, batch_tokens=100_000, batch_pictures=40)
