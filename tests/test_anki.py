"""The Anki export: an .apkg is a zip of "collection.anki2" (Anki's collection tables, schema 11) and "media"; it
holds the IEUStudia note type (Front, Back, Source), the deck, one new card per note, the tags; formulas become
MathJax; the same card keeps its guid in every export (a second import updates it); and the tab-separated file Anki
imports has its headers and escapes what would break a row."""

import hashlib
import json
import sqlite3
import zipfile

from studia import anki

NOTES = [
    {"id": "l1", "front": "What is $P(A\\mid B)$?", "back": "**The** chance of $A$ once $B$ is known:\n$$P(A\\mid B)=\\frac{P(A\\cap B)}{P(B)}$$",
     "source": "MATH 240 · Lecture Wed 21 Oct, part 1, 14:05", "tags": ["MATH 240", "week_05", "stressed"]},
    {"id": "f2", "front": "Ohm's law <with a tag>", "back": "- $V = IR$\n- \\(I = V/R\\)", "source": "", "tags": ["EEE 242"]},
    {"id": "f3", "front": "", "back": "no front: left out"},
    {"id": "l1", "front": "a repeat of l1", "back": "left out"},
]


def open_apkg(path, tmp_path):
    with zipfile.ZipFile(path) as z:
        assert sorted(z.namelist()) == ["collection.anki2", "media"]
        assert json.loads(z.read("media")) == {}
        z.extract("collection.anki2", tmp_path / "x")
    db = sqlite3.connect(str(tmp_path / "x" / "collection.anki2"))
    db.row_factory = sqlite3.Row
    return db


def test_the_package_has_anki_s_tables_and_the_cards(tmp_path):
    path = tmp_path / "out" / "MATH 240 flashcards.apkg"
    assert anki.write_apkg(path, "IEUStudia::MATH 240", NOTES, "From IEUStudia", now=1_762_675_200) == 2
    db = open_apkg(path, tmp_path)
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert tables == {"col", "notes", "cards", "revlog", "graves"}
    cols = {t: [r[1] for r in db.execute(f"PRAGMA table_info({t})")] for t in ("col", "notes", "cards")}
    assert cols["notes"] == ["id", "guid", "mid", "mod", "usn", "tags", "flds", "sfld", "csum", "flags", "data"]
    assert cols["cards"] == ["id", "nid", "did", "ord", "mod", "usn", "type", "queue", "due", "ivl", "factor", "reps",
                             "lapses", "left", "odue", "odid", "flags", "data"]
    col = db.execute("SELECT * FROM col").fetchone()
    assert col["ver"] == 11 and col["id"] == 1
    models = json.loads(col["models"])
    m = models[str(anki.MODEL_ID)]
    assert [f["name"] for f in m["flds"]] == ["Front", "Back", "Source"] and m["type"] == 0
    assert m["tmpls"][0]["qfmt"] == "{{Front}}" and "{{Back}}" in m["tmpls"][0]["afmt"] and "{{Source}}" in m["tmpls"][0]["afmt"]
    decks = json.loads(col["decks"])
    did = anki.deck_id("IEUStudia::MATH 240")
    assert decks[str(did)]["name"] == "IEUStudia::MATH 240" and decks[str(did)]["desc"] == "From IEUStudia"
    assert "1" in decks and "1" in json.loads(col["dconf"]) and json.loads(col["conf"])["curDeck"] == did
    notes = db.execute("SELECT * FROM notes ORDER BY id").fetchall()
    assert len(notes) == 2 and all(n["mid"] == anki.MODEL_ID for n in notes)
    front, back, source = notes[0]["flds"].split("\x1f")
    assert front == "What is \\(P(A\\mid B)\\)?"                                   # MathJax, as Anki draws it
    assert back == "<b>The</b> chance of \\(A\\) once \\(B\\) is known:<br>\\[P(A\\mid B)=\\frac{P(A\\cap B)}{P(B)}\\]"
    assert source == "MATH 240 · Lecture Wed 21 Oct, part 1, 14:05"
    assert notes[0]["tags"] == " MATH_240 week_05 stressed "
    assert notes[0]["sfld"] == "What is \\(P(A\\mid B)\\)?"
    assert notes[0]["csum"] == int(hashlib.sha1(notes[0]["sfld"].encode()).hexdigest()[:8], 16)
    f2 = notes[1]["flds"].split("\x1f")
    assert f2[0] == "Ohm's law &lt;with a tag&gt;" and f2[1] == "• \\(V = IR\\)<br>• \\(I = V/R\\)" and f2[2] == ""
    cards = db.execute("SELECT * FROM cards ORDER BY id").fetchall()
    assert [(c["nid"], c["did"], c["ord"], c["type"], c["queue"], c["due"]) for c in cards] == \
        [(notes[0]["id"], did, 0, 0, 0, 1), (notes[1]["id"], did, 0, 0, 0, 2)]
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    db.close()


def test_a_card_keeps_its_guid_and_the_deck_its_id(tmp_path):
    a, b = tmp_path / "a.apkg", tmp_path / "b.apkg"
    anki.write_apkg(a, "IEUStudia::MATH 240", NOTES[:1], now=1000)
    anki.write_apkg(b, "IEUStudia::MATH 240", [dict(NOTES[0], back="changed")], now=2000)
    g = [open_apkg(p, tmp_path / p.stem).execute("SELECT guid FROM notes").fetchone()[0] for p in (a, b)]
    assert g[0] == g[1] == anki.guid("l1") and anki.guid("l1") != anki.guid("l2")
    assert anki.deck_id("IEUStudia::MATH 240") == anki.deck_id("IEUStudia::MATH 240") != anki.deck_id("IEUStudia::EEE 242")


def test_the_text_file_anki_imports(tmp_path):
    path = tmp_path / "cards.txt"
    rows = [{"front": "Tab\tin it", "back": 'Says "hi"\nand $x^2$', "source": "Week05.pdf, page 3", "tags": ["MATH 240"]},
            {"front": "x", "back": ""}]
    assert anki.write_tsv(path, "IEUStudia::MATH 240", rows) == 1
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[:5] == ["#separator:tab", "#html:true", "#notetype:Basic", "#deck:IEUStudia::MATH 240", "#tags column:3"]
    front, back, tags = lines[5].split("\t")
    assert front == "Tab in it" and tags == "MATH_240"
    assert back == '"Says ""hi""<br>and \\(x^2\\)<br><br><small>Week05.pdf, page 3</small>"'
