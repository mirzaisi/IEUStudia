"""Reasoning effort: how long the AI thinks before it answers. A default per AI and per use (Settings → AI), which any
single request may change (the chat's composer, the notes, plan, review sheet, study pack and cheatsheet dialogs).

The levels are each AI's own, by their real names (shown as "Low", "Medium", "High", "Extra high", "Max"):

- ChatGPT ("Continue with ChatGPT") and the Codex CLI: the chosen model's ``supported_reasoning_levels`` in OpenAI's
  model list (low, medium, high, xhigh and max on the GPT-6 and GPT-5.6 models), sent as ``reasoning.effort`` and as
  Codex's ``model_reasoning_effort``. "ultra" is left out: it lets the model hand work to helpers, which a single
  answer here never does, and it is the heaviest;
- Claude (Claude Code): ``--effort`` low, medium, high, xhigh or max (all three of Haiku, Sonnet and Opus);
- the offline model (Qwen3): thinking off (it answers at once, as it always did) or on (it first thinks, within a
  budget of tokens, then answers).

Before this, ChatGPT had "Quick / Standard / Thorough" (low / medium / high) and Claude always ran with ``--effort low``;
:func:`migrate` turns old settings into the same levels, so nobody gets anything different.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional, Sequence

from .i18n import N_

LEVELS = ("low", "medium", "high", "xhigh", "max")        # in order, the lightest first
LOCAL_LEVELS = ("off", "on")                               # the offline model: thinking off / on
USES = ("chat", "plan", "verify", "digest")               # chat and study notes, plans and packs, checking videos, digest
PROVIDERS = ("chatgpt", "codex", "claude", "local")
HEAVY = ("high", "xhigh", "max")                           # can use a lot of the student's plan: the page warns once
LEGACY = {"haiku": "low", "sonnet": "medium", "opus": "high"}   # ChatGPT's old Quick / Standard / Thorough
SAFE = ("low", "medium", "high")                           # an OpenAI model whose levels aren't known: these always work
LABELS = {"low": N_("Low"), "medium": N_("Medium"), "high": N_("High"), "xhigh": N_("Extra high"), "max": N_("Max"),
          "off": N_("Thinking off"), "on": N_("Thinking on")}
OPENAI = ("chatgpt", "codex")


def defaults(provider: str) -> dict[str, str]:
    """What each use gets before anything is chosen: what it got before reasoning levels could be chosen."""
    if provider in OPENAI:
        return {"chat": "medium", "plan": "medium", "verify": "low", "digest": "low"}
    if provider == "local":
        return {u: "off" for u in USES}
    return {u: "low" for u in USES}                        # Claude Code always ran with --effort low


def vocabulary(provider: str) -> Sequence[str]:
    return LOCAL_LEVELS if provider == "local" else LEVELS


def migrate(raw: dict[str, Any]) -> dict[str, str]:
    """The levels of settings saved before they existed (``raw``: the settings file), so that every use keeps what it
    got: ChatGPT's Quick / Standard / Thorough for chat, plans, checking videos and the digest; low for Claude; thinking
    off for the offline model."""
    def old(key: str, fallback: str) -> str:
        return LEGACY.get(str(raw.get(key) or ""), fallback)

    chat = old("ai_model", "medium")
    gpt = {"chat": chat, "plan": old("plan_model", chat), "verify": old("verify_model", "low"),
           "digest": old("ai_model_light", "low")}
    out = {f"{p}.{u}": v for p in OPENAI for u, v in gpt.items()}
    for p in ("claude", "local"):
        out.update({f"{p}.{u}": v for u, v in defaults(p).items()})
    return out


def clean(value: Any) -> dict[str, str]:
    """A saved ``ai_effort`` with only known providers, uses and levels."""
    out: dict[str, str] = {}
    for key, level in (value.items() if isinstance(value, dict) else ()):
        p, _, use = str(key).partition(".")
        if p in PROVIDERS and use in USES and level in vocabulary(p):
            out[f"{p}.{use}"] = str(level)
    return out


# ------------------------------------------------------------------------------------- what each AI accepts
def _openai_rows(provider: str) -> list[dict[str, Any]]:
    """OpenAI's model list as this PC has it, without asking OpenAI: "Continue with ChatGPT"'s copy (chatgpt.py keeps
    the levels with it), then the Codex CLI's own copy (it lists the same models)."""
    rows: list[dict[str, Any]] = []
    if provider == "chatgpt":
        try:
            from .chatgpt import _models_path

            rows = json.loads(_models_path().read_text(encoding="utf-8")).get("models") or []
        except (OSError, ValueError, AttributeError):
            rows = []
    codex: list[dict[str, Any]] = []
    path = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "models_cache.json"
    try:
        for m in json.loads(path.read_text(encoding="utf-8")).get("models") or []:
            if isinstance(m, dict) and m.get("slug"):
                codex.append({"slug": m["slug"], "levels": [x.get("effort") for x in m.get("supported_reasoning_levels")
                                                            or [] if isinstance(x, dict)],
                              "listed": m.get("visibility", "list") == "list"})
    except (OSError, ValueError, AttributeError):
        pass
    if provider == "codex" or not rows:
        return [r for r in codex if r["listed"]] if provider == "codex" else codex
    known = {r["slug"]: r for r in codex}
    return [{**r, "levels": r.get("levels") or (known.get(r.get("slug")) or {}).get("levels") or []}
            for r in rows if isinstance(r, dict)]


def supported(provider: str, model: str = "") -> tuple[str, ...]:
    """The levels ``provider`` takes (for ChatGPT: the chosen model's, ``model`` = its slug, "" = the one in
    Settings)."""
    if provider == "local":
        return LOCAL_LEVELS
    if provider == "claude":
        return LEVELS
    if provider not in OPENAI:
        return ()
    if not model:
        from . import appconfig

        model = appconfig.load().codex_model
    rows = _openai_rows(provider)
    row = next((r for r in rows if r.get("slug") == model), None) or (rows[0] if rows else {})
    levels = tuple(x for x in LEVELS if x in (row.get("levels") or ()))
    return levels or SAFE


def nearest(level: str, levels: Sequence[str], vocab: Sequence[str] = LEVELS) -> str:
    """``level`` if offered, else the nearest lighter one that is (else the lightest offered)."""
    if level in levels or not levels:
        return level
    i = vocab.index(level) if level in vocab else 0
    lighter = [x for x in levels if x in vocab and vocab.index(x) <= i]
    return lighter[-1] if lighter else levels[0]


def setting(settings: Any, provider: str, use: str) -> str:
    saved = clean(getattr(settings, "ai_effort", None) or {})
    return saved.get(f"{provider}.{use}") or defaults(provider).get(use, "low")


def resolve(use: str, choice: str = "", *, legacy: str = "", provider: Optional[str] = None,
            settings: Any = None) -> str:
    """The level to send for ``use``: this request's ``choice``, else (for ChatGPT) the old model level a chat or a plan
    was saved with (``legacy``: "haiku" / "sonnet" / "opus"), else the setting; then the nearest the AI takes. ""
    when the AI takes none."""
    if provider is None:
        from .ai import provider as chosen

        provider = chosen()
    vocab = vocabulary(provider)
    levels = supported(provider)
    if not levels:
        return ""
    if choice in vocab:
        level = choice
    elif provider in OPENAI and legacy in LEGACY:
        level = LEGACY[legacy]
    else:
        if settings is None:
            from .config import load_settings

            settings = load_settings()
        level = setting(settings, provider, use)
    return nearest(level, levels, vocab)


def heavy(level: str) -> bool:
    return level in HEAVY
