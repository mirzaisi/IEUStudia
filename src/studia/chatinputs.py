"""What a chat sends to the AI, piece by piece ("What this chat sees" beside the chat), the student's own files added
to it, and the in-app viewers of each piece. Built from the same context the chat sends (chat.ChatService.context),
so the panel shows exactly what goes."""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any, Optional

from . import ai, chatfiles, chatslides, store
from .i18n import date_long, t
from .outputs.ai_copy import compact_day

log = logging.getLogger("studia")

PASSAGE_CHARS = 7000            # knowledge.retrieve's most, sent with a question when passages match it


def spec_of(svc: Any, spec: Any) -> dict[str, Any]:
    """A chat ({"chat_id"}) or a new chat not asked yet ({"course_id", "days", "focus", "slides", "send",
    "uploads": its token}) as the material it sends."""
    from .chat import FOCUS

    spec = spec if isinstance(spec, dict) else {"chat_id": str(spec or "")}
    if spec.get("chat_id"):
        chat = svc.lib.get_chat(str(spec["chat_id"]))
        if chat is None:
            raise RuntimeError(t("This chat no longer exists."))
        scope = chat.get("scope") or {}
        return {"course_id": chat["course_id"], "days": [str(k) for k in scope.get("days") or []],
                "focus": scope.get("focus", "full"), "slides": bool(scope.get("slides")), "send": scope.get("send"),
                "target": chat["id"], "chat": chat, "model": chat.get("model") or "", "read_all": scope.get("read_all")}
    course_id = str(spec.get("course_id") or "")
    focus = spec.get("focus") if spec.get("focus") in FOCUS else "full"
    keys = svc.all_days(course_id) if focus == "course" else [str(k) for k in spec.get("days") or []]
    return {"course_id": course_id, "days": svc.ordered_days(course_id, keys), "focus": focus,
            "slides": bool(spec.get("slides")), "send": spec.get("send"),
            "target": chatfiles.draft_target(str(spec.get("uploads") or "")), "chat": None,
            "model": str(spec.get("model") or ""),
            "read_all": None if spec.get("read_all") is None else bool(spec.get("read_all"))}


def _ctx(svc: Any, sp: dict[str, Any]) -> dict[str, Any]:
    return svc.context(sp["course_id"], sp["days"], sp["focus"], sp["slides"], sp["send"], sp["target"], sp["model"],
                       sp["read_all"])


def upload(svc: Any, spec: Any, token: str, name: str) -> dict[str, Any]:
    """A file the page sent (chatfiles.receive) becomes one of the chat's; refused when the chat can't hold it (the
    lectures alone fill the window: a file's own text and pictures never are, as what doesn't fit is read in batches
    or cut, and said so)."""
    sp = spec_of(svc, spec)
    if not sp["target"]:
        raise RuntimeError(t("This chat no longer exists."))
    item = chatfiles.finish(sp["target"], token, name)
    ctx = _ctx(svc, sp)
    if ctx["tokens"] > ctx["limit"]:
        chatfiles.remove(sp["target"], item["id"])
        raise RuntimeError(t("{name} doesn't fit in this chat: it would hold ~{k}k tokens, the limit is {max}k. "
                             "Remove other files or pick fewer class days.", name=item["name"],
                             k=ctx["tokens"] // 1000, max=ctx["limit"] // 1000))
    if sp["chat"] is not None:
        svc.lib.update_chat(sp["chat"]["id"])          # it moves up in the list, as when its slides change
    return {"item": item, "inputs": inputs(svc, spec, ctx)}


def remove_upload(svc: Any, spec: Any, upload_id: str) -> dict[str, Any]:
    sp = spec_of(svc, spec)
    chatfiles.remove(sp["target"], upload_id)
    return {"inputs": inputs(svc, spec)}


def inputs(svc: Any, spec: Any, ctx: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """What the chat sends to the AI with each question, piece by piece, with sizes, and what "Read everything" reads
    in batches first (``read``)."""
    sp = spec_of(svc, spec)
    ctx = ctx or _ctx(svc, sp)
    parts, per = ctx["parts"], ai.chars_per_token()
    p = parts["provider"]
    conv_tokens, turns = 0, 0
    if sp["chat"] is not None:
        msgs = [m for m in svc.lib.chat_messages(sp["chat"]["id"])
                if (m.get("text") or "").strip() and m.get("status") != "streaming"]
        if p in ("chatgpt", "codex"):                # the last turns go with every question (ai.ChatGPTSession)
            msgs = msgs[-2 * ai.ChatGPTSession.MAX_TURNS:]
        elif p == "local":                           # the last exchange, shortened (local_llm.LocalSession)
            msgs = msgs[-2:]
        turns = sum(1 for m in msgs if m["role"] == "user")
        conv_tokens = sum(ai.text_tokens((m.get("text") or "")[-800:] if p == "local" else m.get("text") or "", per)
                          for m in msgs)
    up = parts["uploads"]
    return {
        "provider": p, "ai": ai.ai_name(), "focus": parts["focus"], "days": parts["days"],
        "brief_tokens": parts["brief_tokens"], "instructions_tokens": parts["instructions_tokens"],
        "files": ctx["files"], "video": {"count": len(parts["video"]), "tokens": len(parts["video"]) * ai.IMAGE_TOKENS},
        "uploads": up["items"] if up else [], "upload_pictures_left_out": up["pictures_left_out"] if up else 0,
        "conversation": {"turns": turns, "tokens": conv_tokens},
        "passages_tokens": ai.text_tokens("x" * PASSAGE_CHARS, per),
        "material_tokens": ctx["tokens"], "total": ctx["tokens"] + conv_tokens, "max_tokens": ctx["limit"],
        "can_images": p in ("claude", "chatgpt", "codex"), "limits": {**chatfiles.limits(), "pictures": ctx["lim"].pictures},
        "target": sp["target"], "read": ctx["read"], "read_all": sp["read_all"], "notes_tokens": parts["notes_tokens"],
        "window": ctx["lim"].window,
    }


def file_view(svc: Any, spec: Any, ref: str, page: int = 0, zoom: float = 1.0) -> dict[str, Any]:
    """One piece of what the chat sends, for the viewer: a class day's transcript as sent, a slide file (its pages,
    ``page`` drawn), the slides from the video (``page``: one of them), a file the student added."""
    sp = spec_of(svc, spec)
    kind, _, rest = str(ref or "").partition(":")
    page = int(page or 0)
    if kind == "day":
        if rest not in sp["days"]:
            raise RuntimeError(t("That class day isn't part of this chat."))
        n = sp["days"].index(rest) + 1
        d = svc._load_days(sp["course_id"], [rest])[0]
        if d is None:
            raise RuntimeError(t("That class day is no longer in your library."))
        _, course, recs, trans = d
        sl = {r.delivery_id: store.load_slides(r.delivery_id) for r in recs} if sp["slides"] else None
        text = compact_day(course, recs, trans, sl, label=f"L{n}" if len(sp["days"]) > 1 else "",
                           only_starred=sp["focus"] in ("highlights", "course"))
        return {"kind": "transcript", "title": date_long(min(r.start for r in recs).date()), "text": text,
                "day_key": rest}
    if kind == "video":
        video = _ctx(svc, sp)["video"]
        if not page:
            return {"kind": "strip", "items": [label for label, _ in video]}
        if not 1 <= page <= len(video):
            raise RuntimeError(t("That slide is no longer in this chat."))
        data = ai._small_jpeg(Path(video[page - 1][1]))
        return {"kind": "picture", "data": "data:image/jpeg;base64," + base64.b64encode(data).decode()}
    if kind == "material":
        m = svc.lib.get_material(rest)
        if not m or m.get("course_id") != sp["course_id"]:
            raise RuntimeError(t("That file isn't part of this course."))
        path = Path(m.get("local_path") or "")
        if not path.is_file():
            raise RuntimeError(t("That file isn't on this PC yet. Download it on the course page."))
        pictured = []
        if path.suffix.lower() == ".pdf" and not page:
            pictured = _pictured(_ctx(svc, sp)["images"], str(path), upload=False)
        return _show(path, path.suffix.lower().lstrip("."), m.get("title") or chatslides.label_of(m), page, zoom, pictured)
    if kind == "upload":
        meta, box = chatfiles.get(sp["target"], rest)
        path = box / f"original.{meta['ext']}"
        if meta["kind"] == "image":
            return {"kind": "image", "title": meta["name"], "data": chatfiles.data_url(box / "picture.jpg")}
        pictured = []
        if meta["kind"] == "pdf" and not page:
            pictured = _pictured(_ctx(svc, sp)["images"], str(path), upload=True)
        return _show(path, meta["ext"], meta["name"], page, zoom, pictured, "text" if meta["kind"] == "text" else "doc")
    raise RuntimeError(t("That file is no longer in this chat."))


def _pictured(images: list[tuple[str, Any]], pdf: str, upload: bool) -> list[int]:
    """The pages of the PDF ``pdf`` that go as pictures (all of them when it goes whole as a file)."""
    out: list[int] = []
    for _, pic in images:
        if isinstance(pic, chatslides.PdfFile) and pic.pdf == pdf:
            return list(range(1, pic.pages + 1))
        if isinstance(pic, chatslides.PagePicture) and chatfiles.is_upload(pic) == upload and pic.pdf == pdf:
            out.append(pic.page)
    return out


def _show(path: Path, ext: str, title: str, page: int, zoom: float, pictured: list[int],
          text_kind: str = "doc") -> dict[str, Any]:
    if ext == "pdf":
        if page:
            return {"kind": "picture", "data": chatfiles.pdf_page(path, page, zoom)}
        return {"kind": "pdf", "title": title, "pages": chatfiles.pdf_pages(path), "pictured": pictured}
    if ext == "pptx":
        if page:
            from .paths import app_dir

            cache = chatfiles.pages_folder(path) or app_dir() / "cache" / "pages"      # a student's file: kept with it
            cache.mkdir(parents=True, exist_ok=True)
            return {"kind": "pictures", "pictures": chatfiles.pptx_pictures(path, page, cache)}
        return {"kind": "slides", "title": title, "slides": chatfiles.pptx_slides(path)}
    return {"kind": "text", "title": title, "pages": chatfiles.text_pages(path, text_kind)}
