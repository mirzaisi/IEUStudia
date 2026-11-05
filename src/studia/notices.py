"""Clear warnings for the student, from the AI and from the app.

From the AI: the rule every Markdown-writing prompt gets, so the model says plainly what it cannot do or what needs the
student's attention as a GitHub-style alert block ("> [!WARNING]" …). The page (app.js), the study pack's HTML and PDF
(studypack.md_html) draw those as coloured boxes; the Markdown files keep the syntax, which GitHub and Obsidian draw too.

From the app: notices about a chat's material (lectures not transcribed, files not downloaded, slides trimmed, pictures
the AI cannot take), stored with the answer they belong to (its usage["notices"]) and drawn by web/notices.js. They are
never part of what the AI reads: the conversation it gets is made of the messages' text only.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

KINDS = ("note", "tip", "important", "warning", "caution")

ALERTS_MD = (
    "When something needs the student's attention, say it plainly in a GitHub alert block on lines of their own, "
    "for example:\n> [!WARNING]\n> The slides of week 5 were not given to me, so this part is from general knowledge.\n"
    "Use [!WARNING] when material is missing, cut or unreadable (a lecture not transcribed, slides or a file you did "
    "not get, audio marked [unclear]) or when your answer is a guess; [!CAUTION] for something that would cost marks "
    "if wrong (the lecturer and the slides disagree, a formula you could not check); [!NOTE] for what you cannot do "
    "(open links, see files you were not given, start the app's actions yourself) or where the material ends and "
    "general knowledge begins; [!IMPORTANT] for an exam fact the lecturer stressed; [!TIP] for a useful next step. "
    "One or two short sentences each, only when they matter, never for routine remarks."
)

ALERTS_JSON = (
    "When the material is missing, cut, unreadable or too thin for something asked, never invent it: leave it out "
    "or say so plainly in the field meant for it, so the app can warn the student."
)

_ALERT = re.compile(r"^\s*>\s*\[!(" + "|".join(KINDS) + r")\]", re.I | re.M)


def has_alert(md: str) -> bool:
    """Whether a Markdown text holds an alert block."""
    return bool(_ALERT.search(md or ""))


def notice(code: str, kind: str = "warning", **params: Any) -> dict[str, Any]:
    """One notice: ``code`` says what happened (web/notices.js words it in the language shown), ``kind`` its colour
    (warning, error, info, tip), ``params`` its numbers and dates."""
    return {"code": code, "kind": kind, **params}


_ENGLISH = {      # for the exported Markdown, which stays English (the page words them in its language: notices.js)
    "days_gone": ("{n} class day of this chat is no longer in IEUStudia, so the AI could not read it.",
                  "{n} class days of this chat are no longer in IEUStudia, so the AI could not read them."),
    "not_transcribed": ("{n} recording of these lectures is not transcribed, so the AI did not get it ({dates}).",
                        "{n} recordings of these lectures are not transcribed, so the AI did not get them ({dates})."),
    "files_missing": ("{n} chosen slide file is not downloaded, so the AI did not get it.",
                      "{n} chosen slide files are not downloaded, so the AI did not get them."),
    "files_cut": ("The chosen slide files were too long: the AI got only their first part.",) * 2,
    "slides_trimmed": ("The AI got {sent} of the {total} slide pictures from the videos (the most a chat takes).",) * 2,
    "slides_trimmed_files": ("The AI got {sent} of the {total} video slides: the chosen slide files used the other "
                             "{files} of the {max} pictures a chat takes.",) * 2,
    "no_pictures": ("This AI cannot read pictures: the slides went as text only.",) * 2,
    "pages_unreadable": ("{n} page of the slide files could not be turned into a picture.",
                         "{n} pages of the slide files could not be turned into pictures."),
    "uploads_unreadable": ("{n} page or photo of your own files could not be sent as a picture, so the AI did "
                           "not see it.",
                           "{n} pages or photos of your own files could not be sent as pictures, so the AI did "
                           "not see them."),
}
ALERT_OF = {"warning": "WARNING", "error": "CAUTION", "info": "NOTE", "tip": "TIP"}


def markdown(n: dict[str, Any]) -> str:
    """A notice as a GitHub alert block, in English (for the exported chat)."""
    code = n.get("code", "")
    if code == "slides_trimmed" and n.get("files"):          # the course's slide files took part of the pictures
        code = "slides_trimmed_files"
    one, many = _ENGLISH.get(code, ("{text}",) * 2)
    count = n.get("n", 1)
    params = {**n, "dates": ", ".join(n.get("dates") or []), "text": n.get("text", "")}
    text = (one if count == 1 else many).format(**params)
    return alert(ALERT_OF.get(n.get("kind", ""), "WARNING"), text)


def alert(kind: str, text: str) -> str:
    """A GitHub alert block (``kind``: "WARNING", "CAUTION", …) with every line of ``text`` quoted, so a text of
    several lines stays inside it."""
    return f"> [!{kind}]\n" + "\n".join(f"> {line}".rstrip() for line in (text.splitlines() or [""]))


def _sig(n: dict[str, Any]) -> tuple:
    return tuple(sorted((k, repr(v)) for k, v in n.items()))


def fresh(notices: Iterable[dict[str, Any]], history: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """The notices not already shown with an earlier answer of the chat (a new AI process gets the same material:
    the student is told once)."""
    seen = {_sig(n) for m in history for n in ((m.get("usage") or {}).get("notices") or [])}
    out, here = [], set()
    for n in notices:
        s = _sig(n)
        if s not in seen and s not in here:
            here.add(s)
            out.append(n)
    return out
