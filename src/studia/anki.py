"""Flashcards for Anki: a deck as an .apkg file that Anki opens with a double click, written with the standard library
(sqlite3 and zipfile, no new dependency), and a plain tab-separated file Anki imports (File → Import) as a fallback.

The .apkg is a zip of "collection.anki2", an SQLite database with the tables Anki's collection has had since 2.1
(schema 11: col, notes, cards, revlog, graves; every Anki since reads it and upgrades it as it imports), and "media",
the list of pictures ("{}": none). It holds one note type, "IEUStudia", with the fields Front, Back and Source and
one card per note, and the deck ("IEUStudia::MATH 240", a subdeck Anki shows under IEUStudia). The note type, the
deck and each note keep the same id and guid every time, so importing the deck again updates the cards instead of
adding them twice; the cards arrive as new cards (Anki schedules them itself).

Formulas become MathJax, \\(…\\) and \\[…\\], which Anki draws; the Markdown the AI writes becomes simple HTML."""

from __future__ import annotations

import hashlib
import html
import json
import re
import sqlite3
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any, Iterable, Optional

MODEL_ID = 1731000000001          # the "IEUStudia" note type: the same in every deck (Anki reuses it)
MODEL_NAME = "IEUStudia"
FIELDS = ("Front", "Back", "Source")
CSS = """.card { font-family: "Segoe UI", Arial, sans-serif; font-size: 20px; text-align: left; color: #111;
  background: #fff; line-height: 1.45; padding: 4px 8px; }
.card.nightMode { color: #eee; background: #1e1e1e; }
.src { margin-top: 18px; font-size: 13px; color: #6b7280; }
hr#answer { margin: 14px 0; }
code { font-family: Consolas, monospace; font-size: 90%; }"""
QFMT = "{{Front}}"
AFMT = '{{FrontSide}}\n<hr id="answer">\n{{Back}}\n{{#Source}}<div class="src">{{Source}}</div>{{/Source}}'
SCHEMA = """
CREATE TABLE col (id integer primary key, crt integer not null, mod integer not null, scm integer not null,
  ver integer not null, dty integer not null, usn integer not null, ls integer not null, conf text not null,
  models text not null, decks text not null, dconf text not null, tags text not null);
CREATE TABLE notes (id integer primary key, guid text not null, mid integer not null, mod integer not null,
  usn integer not null, tags text not null, flds text not null, sfld integer not null, csum integer not null,
  flags integer not null, data text not null);
CREATE TABLE cards (id integer primary key, nid integer not null, did integer not null, ord integer not null,
  mod integer not null, usn integer not null, type integer not null, queue integer not null, due integer not null,
  ivl integer not null, factor integer not null, reps integer not null, lapses integer not null,
  left integer not null, odue integer not null, odid integer not null, flags integer not null, data text not null);
CREATE TABLE revlog (id integer primary key, cid integer not null, usn integer not null, ease integer not null,
  ivl integer not null, lastIvl integer not null, factor integer not null, time integer not null,
  type integer not null);
CREATE TABLE graves (usn integer not null, oid integer not null, type integer not null);
CREATE INDEX ix_notes_usn on notes (usn);
CREATE INDEX ix_cards_usn on cards (usn);
CREATE INDEX ix_revlog_usn on revlog (usn);
CREATE INDEX ix_cards_nid on cards (nid);
CREATE INDEX ix_cards_sched on cards (did, queue, due);
CREATE INDEX ix_revlog_cid on revlog (cid);
CREATE INDEX ix_notes_csum on notes (csum);
"""
_B91 = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!#$%&()*+,-./:;<=>?@[]^_`{|}~"


# ------------------------------------------------------------------ text
_MATH = re.compile(r"\$\$(.+?)\$\$|\\\[(.+?)\\\]|\\\((.+?)\\\)|(?<![\\$\w])\$(?=\S)([^$\n]+?)(?<=\S)\$(?!\d)", re.S)


def field_html(md: str) -> str:
    """A card's Markdown as the HTML Anki shows: formulas as \\(…\\) / \\[…\\] (MathJax), **bold**, *italics*,
    `code`, lists and line breaks; everything else escaped."""
    keep: list[str] = []

    def hold(m: re.Match) -> str:
        display, inline = m.group(1) or m.group(2), m.group(3) or m.group(4)
        body = html.escape((display if display is not None else inline).strip(), quote=False)
        keep.append(f"\\[{body}\\]" if display is not None else f"\\({body}\\)")
        return f"\x00{len(keep) - 1}\x00"

    text = _MATH.sub(hold, str(md or "").replace("\r\n", "\n").strip())
    text = html.escape(text, quote=False)
    text = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: f"<b>{m.group(1) or m.group(2)}</b>", text)
    text = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?!\w)", r"<i>\1</i>", text)
    text = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", text)
    lines = []
    for line in text.split("\n"):
        m = re.match(r"\s*(?:[-*+]|(\d+)[.)])\s+(.*)", line)
        lines.append((f"{m.group(1)}. " if m.group(1) else "• ") + m.group(2) if m else line)
    text = "<br>".join(lines)
    return re.sub(r"\x00(\d+)\x00", lambda m: keep[int(m.group(1))], text)


def plain(field: str) -> str:
    """A field without its markup (Anki's sort field and checksum)."""
    text = re.sub(r"<br\s*/?>", " ", field)
    return html.unescape(re.sub(r"<[^>]+>", "", text)).strip()


def tag(text: str) -> str:
    """A tag as Anki takes it: no spaces ("MATH 240" → "MATH_240")."""
    return re.sub(r"[\s\u3000]+", "_", str(text or "").strip()).strip("_")


def guid(key: str) -> str:
    """A note's guid from its card's id (base91, as Anki writes them): the same card has the same one in every
    export, so Anki updates it on a second import."""
    n = int.from_bytes(hashlib.sha256(key.encode("utf-8")).digest()[:8], "big")
    out = ""
    while n:
        n, r = divmod(n, len(_B91))
        out = _B91[r] + out
    return out or "a"


def deck_id(name: str) -> int:
    """A deck's id from its name (stable, within Anki's range of millisecond stamps)."""
    return 1_500_000_000_000 + int(hashlib.sha1(name.encode("utf-8")).hexdigest()[:10], 16) % 400_000_000_000


# ------------------------------------------------------------------ the package
def _model(did: int, now: int) -> dict[str, Any]:
    return {"id": MODEL_ID, "name": MODEL_NAME, "type": 0, "mod": now, "usn": -1, "sortf": 0, "did": did,
            "tmpls": [{"name": "Card 1", "ord": 0, "qfmt": QFMT, "afmt": AFMT, "bqfmt": "", "bafmt": "", "did": None,
                       "bfont": "", "bsize": 0}],
            "flds": [{"name": n, "ord": i, "sticky": False, "rtl": False, "font": "Arial", "size": 20, "media": []}
                     for i, n in enumerate(FIELDS)],
            "css": CSS, "latexPre": "\\documentclass[12pt]{article}\n\\special{papersize=3in,5in}\n"
                                    "\\usepackage{amssymb,amsmath}\n\\pagestyle{empty}\n\\setlength{\\parindent}{0in}\n"
                                    "\\begin{document}\n",
            "latexPost": "\\end{document}", "latexsvg": False, "req": [[0, "any", [0]]], "tags": [], "vers": []}


def _deck(did: int, name: str, desc: str, now: int) -> dict[str, Any]:
    return {"id": did, "name": name, "desc": desc, "mod": now, "usn": -1, "collapsed": False, "browserCollapsed": False,
            "conf": 1, "dyn": 0, "extendNew": 0, "extendRev": 0, "newToday": [0, 0], "revToday": [0, 0],
            "lrnToday": [0, 0], "timeToday": [0, 0]}


DCONF = {"id": 1, "name": "Default", "mod": 0, "usn": 0, "maxTaken": 60, "autoplay": True, "timer": 0, "replayq": True,
         "dyn": False,
         "new": {"delays": [1, 10], "ints": [1, 4, 7], "initialFactor": 2500, "order": 1, "perDay": 20, "bury": False,
                 "separate": True},
         "rev": {"perDay": 200, "ease4": 1.3, "fuzz": 0.05, "maxIvl": 36500, "ivlFct": 1, "minSpace": 1,
                 "bury": False, "hardFactor": 1.2},
         "lapse": {"delays": [10], "mult": 0, "minInt": 1, "leechFails": 8, "leechAction": 1}}


def write_apkg(path: Path, deck_name: str, notes: Iterable[dict[str, Any]], description: str = "",
               now: Optional[float] = None) -> int:
    """Write ``notes`` ({"id", "front", "back", "source", "tags"}, front and back as Markdown, source as plain
    text) as the deck ``deck_name`` to ``path`` (.apkg). Returns how many notes it holds."""
    now = time.time() if now is None else now
    sec, ms = int(now), int(now * 1000)
    did = deck_id(deck_name)
    conf = {"activeDecks": [did], "curDeck": did, "newSpread": 0, "collapseTime": 1200, "timeLim": 0, "estTimes": True,
            "dueCounts": True, "curModel": str(MODEL_ID), "nextPos": 1, "sortType": "noteFld", "sortBackwards": False,
            "addToCur": True, "dayLearnFirst": False, "schedVer": 2}
    decks = {"1": _deck(1, "Default", "", sec), str(did): _deck(did, deck_name, description, sec)}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "collection.anki2"
        db = sqlite3.connect(str(db_path))
        try:
            db.executescript(SCHEMA)
            db.execute("INSERT INTO col VALUES (1, ?, ?, ?, 11, 0, 0, 0, ?, ?, ?, ?, '{}')",
                       (sec - sec % 86400, ms, ms, json.dumps(conf), json.dumps({str(MODEL_ID): _model(did, sec)}),
                        json.dumps(decks), json.dumps({"1": DCONF})))
            n = 0
            seen: set[str] = set()
            for note in notes:
                key = str(note.get("id") or note.get("front") or "")
                g = guid(key)
                if not key or g in seen:
                    continue
                seen.add(g)
                fields = [field_html(note.get("front", "")), field_html(note.get("back", "")),
                          html.escape(str(note.get("source") or ""), quote=False)]
                if not plain(fields[0]) or not plain(fields[1]):
                    continue
                n += 1
                nid, cid = ms + n, ms + n                     # (two tables: the same stamp is no clash)
                tags = " ".join(dict.fromkeys(tag(x) for x in note.get("tags") or [] if tag(x)))
                sort = plain(fields[0])
                csum = int(hashlib.sha1(sort.encode("utf-8")).hexdigest()[:8], 16)
                db.execute("INSERT INTO notes VALUES (?, ?, ?, ?, -1, ?, ?, ?, ?, 0, '')",
                           (nid, g, MODEL_ID, sec, f" {tags} " if tags else "", "\x1f".join(fields), sort, csum))
                db.execute("INSERT INTO cards VALUES (?, ?, ?, 0, ?, -1, 0, 0, ?, 0, 0, 0, 0, 0, 0, 0, 0, '')",
                           (cid, nid, did, sec, n))
            db.commit()
        finally:
            db.close()
        part = path.with_name(path.name + ".part")
        with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(db_path, "collection.anki2")
            z.writestr("media", "{}")
        part.replace(path)
    return n


def write_tsv(path: Path, deck_name: str, notes: Iterable[dict[str, Any]]) -> int:
    """The same cards as a text file Anki imports (File → Import): its headers pick the separator, HTML, the
    "Basic" note type, the deck and the tags' column; the source goes under the answer."""
    def cell(s: str) -> str:
        s = s.replace("\t", " ").replace("\r", "").replace("\n", "<br>")
        return f'"{s.replace(chr(34), chr(34) * 2)}"' if '"' in s else s

    rows = ["#separator:tab", "#html:true", "#notetype:Basic", f"#deck:{deck_name}", "#tags column:3"]
    n = 0
    for note in notes:
        front, back = field_html(note.get("front", "")), field_html(note.get("back", ""))
        if not plain(front) or not plain(back):
            continue
        if note.get("source"):
            back += f'<br><br><small>{html.escape(str(note["source"]), quote=False)}</small>'
        tags = " ".join(dict.fromkeys(tag(x) for x in note.get("tags") or [] if tag(x)))
        rows.append("\t".join((cell(front), cell(back), cell(tags))))
        n += 1
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    part = Path(path).with_name(Path(path).name + ".part")
    part.write_bytes(("\n".join(rows) + "\n").encode("utf-8"))
    part.replace(path)
    return n
