"""2.4.0: search in Turkish however the i's were written, the first lectures of a course that had none, a class day
of several sections, public holidays, the weekly digest of a course whose weeks start mid-week, 4 GB laptops, long
plan titles, a PDF held open, file downloads over a Wi-Fi drop, flashcards to practise and the end-of-batch
notification."""

import datetime as dt
import sys
import threading
import types
from pathlib import Path

import pytest

from studia import hardware
from studia.api import Api
from studia.config import CoursePrefs, Settings
from studia.coursesite import Resource, Unit
from studia.domain import Course, Recording, Segment, Transcript
from studia.library import Library, _fts_word
from studia.services import Services


def _rec(did, cid, start, name="lecture", dur=2700):
    return Recording(delivery_id=did, course_id=cid, folder_id="f", folder_path="", name=name, start=start,
                     duration_s=dur, viewer_url="v")


# ------------------------------------------------------------------ search
def _searchable(tmp_path):
    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="c", title="t", code="MATH 240", name="P")])
    lib.merge_recordings("c", [_rec("a", "c", dt.datetime(2026, 10, 13, 9))], first_sync=True)
    lib.save_transcript(Transcript(delivery_id="a", segments=[
        Segment(0, 5, "Arkadaşlar bu konu sınavda çıkacak", "tr", 0.9),
        Segment(6, 9, "OLASILIK dağılımı ve İşte burada integral", "tr", 0.9),
        Segment(10, 14, "The Thevenin equivalent", "en", 0.9)], duration_s=15, speech_s=14,
        language_seconds={"tr": 9, "en": 4}, model="m", device="cpu", engine_version="x",
        created_at="2026-10-20T10:00:00"), tmp_path / "a.json")
    lib.merge_units("c", [Unit("u1", 1, "Hafta 1", dt.date(2026, 9, 21), dt.date(2026, 9, 27), "",
                               [Resource("m1", "Week01", "w1.pdf", "pdf", "pdf", "u", "d")])], first_sync=True)
    lib.set_material_text("m1", ["KISA SINAV kuralları", "İntegral nasıl alınır: kısmi integrasyon"])
    return lib


@pytest.mark.parametrize("query,t", [("sinav", 0), ("sınav", 0), ("SINAV", 0), ("olasilik", 6), ("olasılık", 6),
                                     ("işte", 6), ("İŞTE", 6), ("theven", 10)])
def test_turkish_words_are_found_however_their_is_are_written(tmp_path, query, t):
    lib = _searchable(tmp_path)
    assert [h["t"] for h in lib.search(query)] == [t]


def test_course_files_are_searched_the_same_way(tmp_path):
    lib = _searchable(tmp_path)
    assert [h["page"] for h in lib.search_materials("kısa sınav")] == [1]
    assert [h["page"] for h in lib.search_materials("integral")] == [2]
    hits = lib.search_any("İntegral nasıl alınır?")              # what the AI chat sends along with a question
    assert [h["page"] for h in hits["materials"]] == [2]
    assert _fts_word("sinav") == '("sinav" OR "sınav")' and _fts_word("ab", True) == '"ab"*'


def test_search_lists_the_course_file_pages_too(tmp_path):
    lib = _searchable(tmp_path)
    ns = types.SimpleNamespace(_s=types.SimpleNamespace(lib=lib), _ready=threading.Event())
    res = Api.search.__wrapped__(ns, "sınav")
    assert [r["delivery_id"] for r in res["results"]] == ["a"]
    assert [(m["title"], m["page"], m["course_code"]) for m in res["materials"]] == [("Week01", 1, "MATH 240")]


# ------------------------------------------------------------------ the first lectures of a course that had none
def test_the_first_lectures_of_a_course_that_had_none_are_new(tmp_path, monkeypatch):
    from studia import sync as sync_mod
    from studia.domain import Profile

    listing = {"a": [], "b": [_rec("b0", "b", dt.datetime(2026, 9, 22, 9))]}

    class Learn:
        def __init__(self, s):
            pass

        def dashboard(self):
            return Profile(name="Ali Veli"), [Course("a", "2026-1 MATH 240 P 1", "MATH 240", "P"),
                                              Course("b", "2026-1 EEE 215 E 1", "EEE 215", "E")]

        def is_logged_in(self):
            return True

        def launch_panopto(self, cid):
            return "f" + cid

    class Pan:
        def __init__(self, s):
            pass

        def is_authenticated(self):
            return True

        def list_recordings(self, cid, folder, heads=None):
            return list(listing[cid])

    monkeypatch.setattr(sync_mod, "IEULearn", Learn)
    monkeypatch.setattr(sync_mod, "Panopto", Pan)
    lib = Library(tmp_path / "lib.db")
    svc = sync_mod.SyncService(lib, Settings)
    assert svc.sync().new_ids == []                                # the first sync: what is there already
    lib.set_meta("listed:a", "2026-09-22T10:00:00")                # MATH 240 had no lectures that day
    assert svc.sync().new_ids == []
    listing["a"] = [_rec("a0", "a", dt.datetime(2026, 9, 21, 8, 30)),        # recorded before (published late)
                    _rec("a1", "a", dt.datetime(2026, 9, 23, 8, 30))]
    listing["b"] = listing["b"] + [_rec("b1", "b", dt.datetime(2026, 9, 29, 9))]
    assert sorted(svc.sync().new_ids) == ["a1", "b1"]
    assert {r.delivery_id: r.is_new for r in lib.recordings()} == {"a0": False, "a1": True, "b0": False, "b1": True}


# ------------------------------------------------------------------ a class day of two sections
def test_transcribe_on_a_class_day_leaves_out_the_other_sections_lecture(tmp_path):
    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="c", title="2026-1 EEE 215 Devreler 0 - 1", code="EEE 215", name="C",
                               section="0 - 1")])
    monday = dt.datetime(2026, 10, 19)
    lib.merge_recordings("c", [_rec("s1", "c", monday.replace(hour=9), "EEE 215 Section 1"),
                               _rec("s2", "c", monday.replace(hour=13), "EEE 215 Section 2"),
                               _rec("t2", "c", monday.replace(day=20, hour=9), "EEE 215 Section 2")], first_sync=True)
    queued = []
    services = types.SimpleNamespace(lib=lib, settings=Settings(course_prefs={"c": CoursePrefs(my_section="1")}))
    services.day = lambda key: Services.day(services, key)
    ns = types.SimpleNamespace(_s=services, transcribe=lambda ids: queued.append(ids) or {"ok": True})
    Api.transcribe_day.__wrapped__(ns, "c|2026-10-19|lecture")
    Api.transcribe_day.__wrapped__(ns, "c|2026-10-20|lecture")     # only the other section's that day: asked for
    services.settings = Settings(course_prefs={"c": CoursePrefs(my_section="1", show_mode="all")})
    Api.transcribe_day.__wrapped__(ns, "c|2026-10-19|lecture")     # all sections shown: both
    assert queued == [["s1"], ["t2"], ["s1", "s2"]]


# ------------------------------------------------------------------ holidays and weeks that start mid-week
def test_public_holidays_are_no_class_days():
    from studia.views import holiday, week_classes

    times = {2: "09:15", 3: "11:15"}                                # Wednesdays and Thursdays
    assert week_classes("2026-10-28", "2026-11-03", times) == [{"date": "2026-10-28", "end": "09:15"}]   # 29 Oct
    assert holiday(dt.date(2027, 3, 10)) and holiday(dt.date(2027, 5, 19)) and not holiday(dt.date(2027, 3, 12))


def test_the_digest_shows_the_course_week_that_covers_most_of_the_week_and_no_old_files(tmp_path):
    from studia.digest import weekly_digest

    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="p", title="2026-1 MATH 240 P 1", code="MATH 240", name="P", term="T")])
    first = dt.date(2026, 9, 23)                                    # a Wednesday: IEULearn weeks run Wed-Tue
    lib.merge_units("p", [Unit(f"u{n}", n, f"Hafta {n}", first + dt.timedelta(weeks=n - 1),
                               first + dt.timedelta(weeks=n - 1, days=6), "",
                               [Resource(f"r{n}", f"Week{n:02d}_Student", f"w{n}.pdf", "pdf", "pdf", f"u{n}", "d")])
                          for n in range(1, 8)], first_sync=True)
    d = weekly_digest(lib, Settings(), 0, today=dt.date(2026, 10, 29))
    (c,) = d["courses"]
    assert c["week"]["no"] == 6                                     # Wed 28 Oct - Tue 3 Nov, not week 5
    assert c["new_files"] == []                                     # the files there at the first sync aren't new
    assert not any("Week05" in t["text"] for t in d["todo"])


# ------------------------------------------------------------------ smaller fixes
def test_a_4_gb_laptop_is_not_turned_away():
    from test_setup import _facts

    v = hardware.assess(_facts(ram_gb=3.8, ram_installed_gb=4.0))
    assert v["ok"] and next(r for r in v["summary"] if r["label"] == "Memory")["ok"] == "warn"
    assert not hardware.assess(_facts(ram_gb=3.2, ram_installed_gb=3.0))["ok"]


def test_a_long_plan_title_keeps_its_md(tmp_path):
    from studia.plans import PlanRunner

    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="c", title="t", code="MATH 240", name="P")])
    runner = PlanRunner.__new__(PlanRunner)
    runner._s = types.SimpleNamespace(lib=lib, output_root=tmp_path / "out")
    path = runner._write_file({"course_id": "c", "created_at": "2026-10-27T10:00"}, "Weeks 1–6 " + "x" * 130, "# t\n",
                              "Study plans")
    assert path.suffix == ".md" and path.read_text(encoding="utf-8") == "# t\n"


def test_two_review_sheets_with_one_title_on_one_day_keep_their_own_files(tmp_path):
    from studia.plans import PlanRunner

    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="c", title="t", code="MATH 240", name="P")])
    runner = PlanRunner.__new__(PlanRunner)
    runner._s = types.SimpleNamespace(lib=lib, output_root=tmp_path / "out")
    first, second, saved = {}, {}, []
    a = runner._write_file({"course_id": "c", "created_at": "2026-10-27T10:00"}, "Weeks 1–6 review sheet", "# a\n",
                           "Review sheets", first, lambda: saved.append(dict(first)))
    b = runner._write_file({"course_id": "c", "created_at": "2026-10-27T15:00"}, "Weeks 1–6 review sheet", "# b\n",
                           "Review sheets", second)
    assert a.name == "2026-10-27 - Weeks 1–6 review sheet.md" and b.name == "2026-10-27 - Weeks 1–6 review sheet (2).md"
    assert a.read_text(encoding="utf-8") == "# a\n" and b.read_text(encoding="utf-8") == "# b\n"
    assert saved == [{"stem": a.stem}]                          # the name is kept before the file is written
    again = runner._write_file({"course_id": "c", "created_at": "2026-10-27T10:00"}, "Weeks 1–6 review sheet",
                               "# a2\n", "Review sheets", first)
    assert again == a and b.read_text(encoding="utf-8") == "# b\n"     # written again (a resume): its own name


@pytest.mark.skipif(sys.platform != "win32", reason="Windows file locking and its message")
def test_a_pdf_held_open_by_a_viewer_is_named(tmp_path, monkeypatch):
    from studia import export
    from studia.net import plain_error

    monkeypatch.setattr(export, "html_to_pdf", lambda html, pdf, timeout=180: pdf.write_bytes(b"%PDF") or True)

    def locked(src, dst, *a, **k):
        raise PermissionError(13, "The process cannot access the file because it is being used by another process")

    monkeypatch.setattr(export.shutil, "copy2", locked)
    course = Course(id="c", title="t", code="MATH 240", name="P")
    r = _rec("a", "c", dt.datetime(2026, 10, 14, 8, 30))
    t = Transcript(delivery_id="a", segments=[Segment(0, 5, "hello", "en", 0.9)], duration_s=5, speech_s=5,
                   language_seconds={"en": 5}, model="m", device="cpu", engine_version="x", created_at="2026-10-20")
    with pytest.raises(PermissionError) as err:
        export.build_ai_pack(tmp_path, course, [course], [r], {"a": t}, {})
    assert err.value.filename.endswith("AI pack.pdf")
    assert plain_error(err.value).startswith("Windows did not let IEUStudia use MATH 240") and "open in" in plain_error(err.value)


def test_an_ai_pack_whose_pdf_fails_never_hands_back_the_earlier_pdf(tmp_path, monkeypatch):
    from studia import export

    course = Course(id="c", title="t", code="MATH 240", name="P")
    r = _rec("a", "c", dt.datetime(2026, 10, 14, 8, 30))
    t = Transcript(delivery_id="a", segments=[Segment(0, 5, "hello", "en", 0.9)], duration_s=5, speech_s=5,
                   language_seconds={"en": 5}, model="m", device="cpu", engine_version="x", created_at="2026-10-20")
    monkeypatch.setattr(export, "html_to_pdf", lambda html, pdf, timeout=180: pdf.write_bytes(b"%PDF part 1") or True)
    first = export.build_ai_pack(tmp_path, course, [course], [r], {"a": t}, {})
    assert first["pdf"].read_bytes() == b"%PDF part 1"
    monkeypatch.setattr(export, "html_to_pdf", lambda html, pdf, timeout=180: False)    # Edge timed out this time
    again = export.build_ai_pack(tmp_path, course, [course], [r], {"a": t}, {})
    assert "pdf" not in again and again["zip"].is_file() and not first["pdf"].exists()


def test_file_downloads_wait_out_a_short_wifi_drop(monkeypatch):
    from studia import api as api_mod
    from studia.net import NetworkError

    tries, pushed = {"m1": 0}, []

    def download(rid, session, progress):
        tries[rid] = tries.get(rid, 0) + 1
        if rid == "m1" and tries[rid] < 3:
            raise NetworkError("Can't reach learn.ieu.edu.tr.")
        if rid == "dead":
            raise NetworkError("Can't reach learn.ieu.edu.tr.")
        return f"C:/x/{rid}.pdf"

    waits = []
    monkeypatch.setattr(api_mod.time, "sleep", waits.append)
    a = Api.__new__(Api)
    a._mat_lock, a._mat_queue, a._mat_running = threading.Lock(), ["m1", "m2"], True
    a._courses_dirty = threading.Event()
    a._push = pushed.append
    sync = types.SimpleNamespace(worker_session=lambda: object(), absorb=lambda s: None)
    a._services = types.SimpleNamespace(materials=types.SimpleNamespace(download=download, index_text=lambda rid: 3),
                                        sync=sync)
    a._ready = threading.Event()
    a._ready.set()
    a._material_worker()
    assert waits == [5, 15] and [(e["id"], e["state"]) for e in pushed if e["state"] in ("done", "error")] == [
        ("m1", "done"), ("m2", "done")]
    pushed.clear()
    a._mat_queue, a._mat_running = ["dead", "m3", "m4"], True       # the connection stays down
    a._material_worker()
    errors = [(e["id"], e.get("quiet", False)) for e in pushed if e["state"] == "error"]
    assert errors == [("dead", False), ("m3", True), ("m4", True)]  # said once, not three times


def test_flashcards_as_cards_to_practise():
    from studia.outputs.flashcards import parse_cards

    md = """# Flashcards

1. **Q:** What is Bayes' theorem?
   **A:** $P(A\\mid B) = P(B\\mid A)P(A)/P(B)$ (Part 1, 18:41)

Q2: Name the two rules
of counting.
A2: Product rule
and sum rule.

---
### Card 3
- **Question:** Unanswered question?

**Q:** Last one?
**A:** Yes (Part 2, 05:05)
"""
    cards = parse_cards(md)
    assert [c["q"] for c in cards] == ["What is Bayes' theorem?", "Name the two rules\nof counting.", "Last one?"]
    assert cards[0]["a"].startswith("$P(A") and (cards[0]["part"], cards[0]["t"]) == (1, 1121)
    assert cards[1]["a"] == "Product rule\nand sum rule." and cards[1]["t"] is None
    assert (cards[2]["part"], cards[2]["t"]) == (2, 305)
    assert parse_cards("") == [] and parse_cards("Just notes, no cards.") == []


@pytest.mark.parametrize("md,q,a,t", [
    ("**Q1**: What is X?\n**A1**: Y (Part 1, 05:30)", "What is X?", "Y (Part 1, 05:30)", 330),
    ("**Q**: What is X?\n**A**: Y", "What is X?", "Y", None),
    ("**1.** Q: What is X?\nA: Y", "What is X?", "Y", None),
    ("### Q1: What is X?\n**A:** Y (Part 2, 1:05:30)", "What is X?", "Y (Part 2, 1:05:30)", 3930),
    ("### What is X?\n\n**A:** Y", "What is X?", "Y", None),
    ("**Q: What is X?**\nA: Y", "What is X?", "Y", None),
    ("Q: Which is the height of a tree?\na) height\nb) number of heads\nA: a)", "Which is the height of a tree?\na) height\nb) number of heads", "a)", None),
    ("**1. Q:** What is X?\n**A:** Y", "What is X?", "Y", None),
    ("Q: What is X? A: Y (Part 1, 05:30)", "What is X?", "Y (Part 1, 05:30)", 330),
    ("Q: Bayes?\nA: P(A|B) (Part 2, 14:05–15:20)", "Bayes?", "P(A|B) (Part 2, 14:05–15:20)", 845),
    ("| # | Question | Answer |\n|---|---|---|\n| 1 | What is X? | Y (Part 1, 05:30) |", "What is X?", "Y (Part 1, 05:30)", 330),
    ("Q1. What is X?\nA1. Y (Part 1, 05:30)", "What is X?", "Y (Part 1, 05:30)", 330),
    ("q: What is X?\na: Y", "What is X?", "Y", None),
    ("Q: Consider the matrix A: [[1, 2], [3, 4]]. What is det(A)?\nA: -2 (Part 1, 12:40)",
     "Consider the matrix A: [[1, 2], [3, 4]]. What is det(A)?", "-2 (Part 1, 12:40)", 760),
])
def test_flashcards_in_the_ways_chatgpt_and_claude_write_them(md, q, a, t):
    from studia.outputs.flashcards import parse_cards

    (card,) = parse_cards(md)
    assert (card["q"], card["a"], card["t"]) == (q, a, t)


@pytest.mark.parametrize("result,local,expect", [
    ({"done": 1, "failed": 0, "last_done": {"id": "r1", "title": "MATH 240 · Fri 16 Oct 14:06", "local": False}}, False,
     ("Transcribed: MATH 240 · Fri 16 Oct 14:06", "It's ready to read. Click to open it.", "day:c|2026-10-16|lecture")),
    ({"done": 3, "failed": 1}, False, ("3 lectures transcribed", "They're ready to read in IEUStudia. 1 couldn't be "
                                       "transcribed: the course page says why.", "home")),
    ({"done": 0, "failed": 0}, False, None)])
def test_a_batch_done_in_the_background_is_told(tmp_path, result, local, expect):
    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="c", title="t", code="MATH 240", name="P")])
    lib.merge_recordings("c", [_rec("r1", "c", dt.datetime(2026, 10, 16, 14, 6))], first_sync=True)
    told = []
    ns = types.SimpleNamespace(_hwnd=0, _s=types.SimpleNamespace(lib=lib), _notify=lambda *a: told.append(a))
    Api._tell_batch_done(ns, {"id": "r2", "title": "MATH 240 · Fri 16 Oct 15:00", "local": local, "stage": "cancelled",
                              "batch_result": {**result, "ended": 4}})     # the last to end was another, cancelled
    assert told == ([expect] if expect else [])


@pytest.mark.skipif(sys.platform != "win32", reason="Inno Setup finds the Win32 tray window")
def test_the_installer_still_finds_the_background_helper():
    """The installer closes the helper by its window class before it replaces files; notification icons of the app
    itself have classes of their own."""
    import inspect

    from studia import tray

    iss = (Path(__file__).parents[1] / "packaging" / "IEUStudia.iss").read_text(encoding="utf-8")
    default = inspect.signature(tray.Tray.__init__).parameters["class_name"].default
    assert f"FindWindowByClassName('{default}')" in iss and default == "IEUStudiaTray"
    assert "class_name=f\"IEUStudiaNotify" in inspect.getsource(tray.Notifier)


def test_reminders_come_also_when_ieulearn_cant_be_reached():
    told, pushed = [], []

    def broken(*a, **k):
        raise OSError("Can't reach learn.ieu.edu.tr.")

    lock = threading.Lock()
    lock.acquire()
    ns = types.SimpleNamespace(_push=pushed.append, _sync_lock=lock, _remind=lambda: told.append(True),
                               _s=types.SimpleNamespace(sync=types.SimpleNamespace(sync=broken)))
    assert Api._run_sync(ns) is False
    assert told == [True] and pushed[-1]["phase"] == "error" and not lock.locked()


def test_what_was_told_is_merged_when_the_app_and_the_helper_both_write_it(isolated_home):
    from studia.agent import _load_seen, _save_seen

    helper = _load_seen()                                   # the helper loads, then checks IEULearn for a while
    _save_seen({"rem": ["b-quiz-2@2026-10-13"]})            # meanwhile the app reminds of the quiz
    helper.setdefault("rec", []).append("x1")
    _save_seen(helper)                                      # the helper saves what it loaded, plus a recording
    assert _load_seen() == {"rem": ["b-quiz-2@2026-10-13"], "rec": ["x1"]}
