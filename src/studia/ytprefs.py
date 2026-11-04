"""The student's YouTube choices for video study plans: the language of the videos (English, Turkish or both), and
the channels they prefer or never want to see. One small file per profile (``youtube.json``); a plan copies the
language when it is made (it can be changed per plan), the channel lists apply to every plan, also existing ones."""

from __future__ import annotations

import dataclasses as dc
import json
import logging
import os
import re
import threading
from typing import Any, Optional

from .paths import profile_dir

log = logging.getLogger("studia")

LANGS = ("en", "tr", "both")
MAX_CHANNELS = 200
_lock = threading.Lock()


@dc.dataclass
class Prefs:
    lang: str = "en"
    allow: list[dict[str, Any]] = dc.field(default_factory=list)    # [{"id", "name", "handle", "avatar"}]
    block: list[dict[str, Any]] = dc.field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return dc.asdict(self)

    def is_blocked(self, channel_id: str = "", name: str = "") -> bool:
        return _listed(self.block, channel_id, name)

    def is_allowed(self, channel_id: str = "", name: str = "") -> bool:
        return _listed(self.allow, channel_id, name)


def caption_langs(lang: str) -> tuple[str, ...]:
    """The caption languages to look for, best first: "en" -> ("en",), "tr" -> ("tr",), "both" -> ("en", "tr")."""
    return {"tr": ("tr",), "both": ("en", "tr")}.get(lang, ("en",))


def _key(name: str) -> str:
    return re.sub(r"[\W_]+", "", (name or "").casefold())


def _listed(items: list[dict[str, Any]], channel_id: str, name: str) -> bool:
    """By the channel's id; by its name only when the video's id is unknown (searches saved by older versions)."""
    if channel_id:
        return any(c.get("id") == channel_id for c in items)
    k = _key(name)
    return bool(k) and any(_key(str(c.get("name") or "")) == k for c in items)


def _path():
    return profile_dir() / "youtube.json"


def _clean(raw: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for c in raw if isinstance(raw, list) else []:
        if not isinstance(c, dict):
            continue
        cid, name = str(c.get("id") or "").strip(), " ".join(str(c.get("name") or "").split())
        if not re.fullmatch(r"UC[A-Za-z0-9_-]{22}", cid) or not name or any(x["id"] == cid for x in out):
            continue
        avatar = str(c.get("avatar") or "")
        out.append({"id": cid, "name": name[:100], "handle": str(c.get("handle") or "")[:60],
                    "avatar": avatar if avatar.startswith("https://") else ""})
    return out[:MAX_CHANNELS]


def load() -> Prefs:
    try:
        raw = json.loads(_path().read_bytes().decode("utf-8"))
    except (OSError, ValueError):
        raw = {}
    raw = raw if isinstance(raw, dict) else {}
    lang = raw.get("lang") if raw.get("lang") in LANGS else "en"
    return Prefs(lang, _clean(raw.get("allow")), _clean(raw.get("block")))


def _save(p: Prefs) -> None:
    path = _path()
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(json.dumps(p.to_json(), ensure_ascii=False, indent=1).encode("utf-8"))
    os.replace(tmp, path)


def set_lang(lang: str) -> Prefs:
    with _lock:
        p = load()
        p.lang = lang if lang in LANGS else "en"
        _save(p)
    return p


def set_channel(channel: dict[str, Any], which: Optional[str]) -> Prefs:
    """Puts a channel on the "allow" (preferred) or "block" (never shown) list - off the other one -, or with
    ``which`` None/"" takes it off both."""
    ch = _clean([channel])
    if not ch:
        raise ValueError("not a YouTube channel")
    with _lock:
        p = load()
        p.allow = [c for c in p.allow if c["id"] != ch[0]["id"]]
        p.block = [c for c in p.block if c["id"] != ch[0]["id"]]
        if which == "allow":
            p.allow = _clean([ch[0], *p.allow])
        elif which == "block":
            p.block = _clean([ch[0], *p.block])
        _save(p)
    return p
