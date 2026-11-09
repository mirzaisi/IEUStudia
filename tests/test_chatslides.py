"""The course's slides from IEULearn in a chat: files matched to lecture dates through the weekly plan (and checked
against what was taught), the choices, the budget of pictures and text, and what is remembered per chat."""

import datetime as dt
import types

import pytest

from studia import ai, appconfig, chatslides, store
from studia.chat import ChatService
from studia.config import Settings
from studia.domain import Course, Recording, Segment, Transcript
from studia.library import Library
from studia.services import Services

DAY = "c|2026-10-14|lecture"          # week 4 of a plan that starts on 23 Sep


def _res(rid, title, ext="pdf"):
    return types.SimpleNamespace(id=rid, title=title, file_name=f"{title}.{ext}", kind="resource", ext=ext,
                                 url=f"https://l/{rid}", download_url=f"https://l/{rid}?dl")


def _unit(uid, pos, title, start, resources):
    s = dt.date.fromisoformat(start) if start else None
    return types.SimpleNamespace(id=uid, position=pos, title=title, start=s, end=s + dt.timedelta(days=6) if s else None,
                                 readings="", resources=resources)


@pytest.fixture
def env(tmp_path):
    appconfig.update({"ai_provider": "claude"})
    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="c", title="t", code="MATH 240", name="Probability")])
    weeks = [("u1", "2026-09-23", "Week01_Student"), ("u2", "2026-09-30", "Week02_Student"),
             ("u3", "2026-10-07", "Week03_Student"), ("u4", "2026-10-14", "Week04_Student"),
             ("u5", "2026-10-21", "Week05_Student")]
    units = [_unit("u0", 0, "Formula Sheet", "", [_res("f0", "Formula Sheet")])]
    units += [_unit(u, i + 1, f"Topic {i + 1}", d, [_res(f"w{i + 1}", name)]) for i, (u, d, name) in enumerate(weeks)]
    units.append(_unit("u6", 7, "Hafta 6 ve 7", "", [_res("w6", "Notes", "pptx")]))
    lib.merge_units("c", units, first_sync=True)
    texts = {"f0": ["formulas"], "w1": ["sample spaces and events"] * 3, "w2": ["counting permutations"] * 2,
             "w3": ["conditional probability multiplication rule", "tree diagrams"],
             "w4": ["bayes theorem posterior prior", "screening tests sensitivity", ""],
             "w5": ["random variables"] * 4, "w6": ["capacitors"]}
    for rid, pages in texts.items():
        if rid == "w5":
            continue                                        # listed on IEULearn, not downloaded
        f = tmp_path / f"{rid}.{'pptx' if rid == 'w6' else 'pdf'}"
        f.write_bytes(b"%PDF-1.4")
        lib.set_material_file(rid, str(f), 8, "u")
        lib.set_material_text(rid, pages)
    rec = Recording(delivery_id="r0", course_id="c", folder_id="f", folder_path="", name="n",
                    start=dt.datetime(2026, 10, 14, 8, 30), duration_s=2400, viewer_url="https://v/r0")
    lib.merge_recordings("c", [rec], first_sync=True)
    t = Transcript(delivery_id="r0", segments=[Segment(0, 5, "Today: Bayes theorem, the posterior and the prior.", "en", 0.9)],
                   duration_s=2400, speech_s=5, language_seconds={"en": 5}, model="m", device="cpu", engine_version="t",
                   created_at="2026-10-14T10:00:00")
    lib.save_transcript(t, store.save_transcript(t))
    lib.set_status("r0", "done")
    ns = types.SimpleNamespace(lib=lib, emit=lambda e: None, output_root=tmp_path / "out", settings=Settings())
    ns.day = types.MethodType(Services.day, ns)
    svc = ChatService(ns)
    yield types.SimpleNamespace(lib=lib, svc=svc)
    svc.shutdown()


def test_titles_name_their_weeks():
    assert chatslides.title_weeks("Hafta 6 ve 7 Capacitive and Inductive circuits") == [6, 7]
    assert chatslides.title_weeks("MATH_250_Week 8-9") == [8, 9]
    assert chatslides.title_weeks("Week01_Student") == [1]
    assert chatslides.title_weeks("Konu 12") == [12]
    assert chatslides.title_weeks("Lab Grades 22.10.26") == []
    assert chatslides.title_weeks("Week 3-19") == [3]                        # not a range of weeks


def test_files_follow_the_weekly_plan(env):
    cat = chatslides.course_files(env.lib, "c", [DAY])
    weeks = {f["id"]: f["weeks"] for f in cat["files"]}
    assert weeks == {"f0": [], "w1": [1], "w2": [2], "w3": [3], "w4": [4], "w5": [5], "w6": [6, 7]}
    assert cat["lecture_weeks"] == [4] and cat["modes"]["lecture"] == ["w4"]
    assert cat["modes"]["upto"] == ["w1", "w2", "w3", "w4"]               # no formula sheet: it has no week
    assert set(cat["modes"]["all"]) == set(weeks)
    # a date with no file of its own (week 8): the closest earlier week's
    later = chatslides.course_files(env.lib, "c", ["c|2026-11-11|lecture"])
    assert later["modes"]["lecture"] == ["w6"] and later["note"] == "closest"


def test_a_file_s_own_week_beats_its_unit_s_number(env, monkeypatch):
    # MATH 250: undated units "Konu N" whose files are named for the week after from Konu 10 on
    env.lib.upsert_courses([Course(id="m", title="m", code="MATH 250", name="Linear Algebra")])
    names = {8: "MATH_250_Week 8-9", 9: "MATH_250_Week 9-10", 10: "MATH_250_Week 11", 11: "MATH_250_Week 12",
             12: "MATH_250_Week 13", 13: "MATH_250_Week 14"}
    units = [_unit(f"k{n}", n, f"Konu {n}", "", [_res(f"m{n}", name)]) for n, name in names.items()]
    units.append(_unit("k7", 7, "Konu 7", "", [_res("m7", "Konu 7 notes")]))     # a file that names no week
    env.lib.merge_units("m", units, first_sync=True)
    monkeypatch.setattr(chatslides, "first_week_day", lambda lib, cid, units: dt.date(2026, 9, 21))
    cat = chatslides.course_files(env.lib, "m", ["m|2026-12-01|lecture"])             # week 11
    weeks = {f["id"]: f["weeks"] for f in cat["files"]}
    assert weeks == {"m7": [7], "m8": [8, 9], "m9": [9, 10], "m10": [11], "m11": [12], "m12": [13], "m13": [14]}
    assert cat["lecture_weeks"] == [11] and cat["modes"]["lecture"] == ["m10"]
    assert cat["modes"]["upto"] == ["m7", "m8", "m9", "m10"]                         # not next week's file


def test_a_chapter_in_a_file_s_name_is_not_a_week_in_chats_either(env, monkeypatch):
    """The Weeks tab counted only "Week"/"Hafta" in a file's name, a chat also "Chapter 5" (the book's chapter): the
    same file sat in week 3 on the course page and in weeks 3 and 5 in a chat. One rule now, in both."""
    from studia import views

    env.lib.upsert_courses([Course(id="p", title="p", code="MATH 240", name="Probability")])
    env.lib.merge_units("p", [_unit("k3", 3, "Konu 3", "", [_res("c5", "Chapter 5 Conditional probability"),
                                                             _res("p4", "Week 4 examples")])], first_sync=True)
    monkeypatch.setattr(chatslides, "first_week_day", lambda lib, cid, units: dt.date(2026, 9, 21))
    weeks = {f["id"]: f["weeks"] for f in chatslides.course_files(env.lib, "p", [])["files"]}
    assert weeks == {"c5": [3], "p4": [4]}
    for m in ({"title": "Chapter 5", "file_name": "Chapter_5.pdf"}, {"title": "Ünite 2 / Bölüm 7"},
              {"title": "MATH_250_Week 8-9", "file_name": "x.pdf"}, {"title": "Hafta 21"}):
        assert views._own_weeks(m) == chatslides.own_weeks(m)
    assert chatslides.own_weeks({"title": "Chapter 5", "file_name": "Week05_Student.pdf"}) == {5}
    assert chatslides.own_weeks({"title": "Hafta 21"}) == set()                 # past a term's weeks


def test_a_unit_that_names_its_weeks_keeps_them_with_the_file_s_own(env, monkeypatch):
    # EEE 215: the undated unit "Hafta 6 ve 7 ..." holds files named for week 6 that were taught in weeks 6 and 7
    env.lib.upsert_courses([Course(id="e", title="e", code="EEE 215", name="Circuits")])
    env.lib.merge_units("e", [_unit("h6", 6, "Hafta 6 ve 7 Capacitive and Inductive circuits", "",
                                    [_res("e1", "EEE 215 Week 6 v3 CL1"), _res("e2", "EEE 215 Week 6 examples CL1")])],
                        first_sync=True)
    monkeypatch.setattr(chatslides, "first_week_day", lambda lib, cid, units: dt.date(2026, 9, 21))
    weeks = {f["id"]: f["weeks"] for f in chatslides.course_files(env.lib, "e", ["e|2026-11-04|lecture"])["files"]}
    assert weeks == {"e1": [6, 7], "e2": [6, 7]}


def test_the_lecture_s_files_are_checked_against_what_was_taught(env):
    # the lecturer is a week behind: the week's file hardly matches, the one that does is taken instead
    cat = chatslides.course_files(env.lib, "c", ["c|2026-10-21|lecture"], said="bayes theorem posterior prior again")
    assert cat["lecture_weeks"] == [5] and cat["matched"] == ["w4"] and cat["modes"]["lecture"] == ["w4"]
    # what was taught matches the week's file: it stays alone
    same = chatslides.course_files(env.lib, "c", [DAY], said="bayes theorem posterior prior")
    assert same["matched"] == [] and same["modes"]["lecture"] == ["w4"]


def test_what_goes_text_pictures_and_size(env):
    b = chatslides.build(env.lib, "c", [DAY], {"mode": "upto"}, captured=3, can_images=True)
    s = b["summary"]
    assert [f["id"] for f in s["files"]] == ["w1", "w2", "w3", "w4"] and s["pages"] == 3 + 2 + 2 + 3
    assert "[Week04_Student.pdf p.1]\nbayes theorem posterior prior" in b["text"]
    assert "[Week04_Student.pdf p.3] (no text: see the picture)" in b["text"]
    assert s["page_images"] == 10 and s["video_images"] == 3
    assert [label for label, _ in b["images"]][:2] == ["[Week01_Student.pdf p.1]", "[Week01_Student.pdf p.2]"]
    # a page picture by its shape (one that can't be read: a 4:3 slide), a video slide 800
    assert chatslides.picture_cost(0.75) == 1049 and chatslides.picture_cost(9 / 16) == 786
    assert s["tokens"] == s["text_tokens"] + 10 * 1049 + 3 * ai.IMAGE_TOKENS and s["bytes"] > 10 * chatslides.IMAGE_BYTES
    # a PowerPoint goes as text only; pictures off: text only; a file that isn't downloaded is counted as missing
    p = chatslides.build(env.lib, "c", [DAY], {"mode": "pick", "files": ["w6", "w5"]}, can_images=True)["summary"]
    assert p["pages"] == 1 and p["page_images"] == 0 and p["missing"] == 1
    off = chatslides.build(env.lib, "c", [DAY], {"mode": "all", "images": False}, can_images=True)["summary"]
    assert off["page_images"] == 0 and off["pages"] == 12


def test_the_picture_budget_puts_figure_pages_first_and_leaves_the_video_a_third(env, monkeypatch):
    monkeypatch.setattr(chatslides, "MAX_IMAGES", 6)
    b = chatslides.build(env.lib, "c", [DAY], {"mode": "upto"}, captured=5, can_images=True)
    s = b["summary"]
    assert s["video_images"] == 2 and s["page_images"] == 4 and s["text_only_pages"] == 6
    # every page here has little text, so the first ones in file order go; with long text pages the empty one wins
    monkeypatch.setattr(chatslides, "SHORT_TEXT", 10)
    labels = [label for label, _ in chatslides.build(env.lib, "c", [DAY], {"mode": "upto"}, captured=5,
                                                      can_images=True)["images"]]
    assert "[Week04_Student.pdf p.3]" in labels


def test_the_offline_model_gets_matching_passages_instead(env):
    appconfig.update({"ai_provider": "local"})
    try:
        s = chatslides.build(env.lib, "c", [DAY], {"mode": "all"})["summary"]
    finally:
        appconfig.update({"ai_provider": "claude"})
    assert s["local"] and not s["files"] and s["tokens"] == 0


def test_a_chat_remembers_its_slides_and_sends_them(env):
    chat = env.svc.create("c", [DAY], send={"mode": "lecture"})
    assert chat["send"] == {"mode": "lecture", "files": [], "images": True} and chat["files"]["pages"] == 3
    ctx = env.svc.context("c", [DAY], send=chat["send"])
    assert "=== COURSE SLIDES ===" in ctx["system"] and "[Week04_Student.pdf p.2]" in ctx["system"]
    assert "Under COURSE SLIDES" in ctx["system"]
    assert [label for label, _ in ctx["images"]] == [f"[Week04_Student.pdf p.{i}]" for i in (1, 2, 3)]
    assert all(isinstance(p, chatslides.PagePicture) for _, p in ctx["images"])       # drawn only when sent
    # changed later: saved with the chat, and none clears it
    v = env.svc.update(chat["id"], {"send": {"mode": "pick", "files": ["w1", "w2"]}})
    assert v["send"]["files"] == ["w1", "w2"] and [f["id"] for f in v["files"]["files"]] == ["w1", "w2"]
    v = env.svc.update(chat["id"], {"send": {"mode": "none"}})
    assert v["send"]["mode"] == "none" and v["files"] is None
    assert "send" not in env.lib.get_chat(chat["id"])["scope"]
    opts = env.svc.slide_options("c", [DAY], send={"mode": "upto"})
    assert opts["modes"]["lecture"] == ["w4"] and opts["summary"]["pages"] == 10 and opts["tokens"] > 0


def test_the_slides_chooser_and_a_new_chat_count_the_student_s_files(env, tmp_path, monkeypatch):
    from studia import chat as chat_mod
    from studia import chatfiles

    chat = env.svc.create("c", [DAY])
    book = tmp_path / "book.txt"
    book.write_text("word " * 4000)
    chatfiles.add(chat["id"], book, "book.txt")
    bare = env.svc.slide_options("c", [DAY], send={"mode": "upto"})["tokens"]
    full = env.svc.slide_options("c", [DAY], send={"mode": "upto"}, uploads=chat["id"])["tokens"]
    assert full > bare + 1500
    assert env.svc.slide_options("c", [DAY], send={"mode": "upto"}, uploads="0123456789ab")["tokens"] == bare
    # slides beyond what the chat holds are never refused: their text is cut (and read in batches with "Read
    # everything"), the student's own files first; a change that makes the chat smaller is always fine
    monkeypatch.setattr(chat_mod, "MAX_CONTEXT_TOKENS", full - 12_000)        # less than the pages' pictures take
    v = env.svc.update(chat["id"], {"send": {"mode": "upto"}, "read_all": False})
    assert v["context_tokens"] <= full - 12_000 and v["files"]["page_images"] < 10 and v["uploads"][0]["cut"]
    ctx = env.svc.context("c", [DAY], send={"mode": "upto"}, uploads=chat["id"], read_all=False)
    # every page without its picture, and those of the book whose text was cut: "Read everything" would read them
    (left,) = [n for n in ctx["notices"] if n["code"] == "pictures_left"]
    assert left["total"] == 10 + 7 and left["n"] == 10 - v["files"]["page_images"] + ctx["read"]["pages"] - 10
    on = env.svc.context("c", [DAY], send={"mode": "upto"}, uploads=chat["id"], read_all=True)
    assert on["tokens"] <= full - 12_000 and on["read"]["on"] and not on["notices"]
    read = {p.label for p in on["read_pages"]}                                # every page not seen whole is read
    assert {f"[Week04_Student.pdf p.{i}]" for i in (1, 2, 3)} <= read and "[book.txt part 7]" in read
    monkeypatch.setattr(chat_mod, "MAX_CONTEXT_TOKENS", 100)
    assert env.svc.update(chat["id"], {"send": {"mode": "none"}})["send"]["mode"] == "none"
    monkeypatch.setattr(chat_mod, "MAX_CONTEXT_TOKENS", 900_000)
    # a new chat: its files (under its draft token) are in the estimate and named when the lectures alone fill it
    chatfiles.add("draft-0123456789abcdef", book, "book.txt")
    assert (env.svc.slide_options("c", [DAY], uploads="draft-0123456789abcdef")["tokens"]
            > env.svc.slide_options("c", [DAY])["tokens"] + 1500)
    monkeypatch.setattr(chat_mod, "MAX_CONTEXT_TOKENS", 300)
    with pytest.raises(RuntimeError, match=r"too much for one chat.*Your files take ~\dk of it: book\.txt\."):
        env.svc.create("c", [DAY], uploads="0123456789abcdef")


def test_page_pictures_keep_their_name_between_runs_and_old_ones_go(tmp_path, monkeypatch):
    import hashlib
    import os
    import subprocess
    import sys

    import pypdfium2 as pdfium

    from studia import materials
    from studia.paths import app_dir

    pdf = tmp_path / "Week 11.pdf"
    doc = pdfium.PdfDocument.new()
    doc.new_page(200, 300)
    doc.save(str(pdf))
    doc.close()
    out = materials.page_image(pdf, 1, width=200)
    assert out.name.endswith(f"-{hashlib.sha1(str(pdf).encode()).hexdigest()[:10]}-1-200.jpg")
    # another run (another str hash seed) finds the same picture
    code = "import sys; from pathlib import Path; from studia import materials; print(materials.page_image(Path(sys.argv[1]), 1, width=200))"
    env = {**os.environ, "PYTHONHASHSEED": "7", "PYTHONPATH": os.pathsep.join(sys.path)}
    again = subprocess.run([sys.executable, "-c", code, str(pdf)], capture_output=True, text=True, env=env, timeout=60)
    assert again.stdout.strip() == str(out)
    # the cache keeps the newest pictures only
    monkeypatch.setattr(materials, "PAGE_COPIES", 5)
    cache = app_dir() / "cache" / "pages"
    for i in range(110):
        (cache / f"old-{i}.jpg").write_bytes(b"x")
        os.utime(cache / f"old-{i}.jpg", (1000 + i, 1000 + i))
    small = materials.page_image(pdf, 1, width=120)
    left = {p.name for p in cache.glob("*.jpg")}
    assert left == {out.name, small.name, "old-106.jpg", "old-107.jpg", "old-108.jpg", "old-109.jpg"}
