import datetime as dt

from studia.domain import Course, Recording, Segment, Transcript
from studia.library import Library


def rec(did, minute, dur=2400.0, kind="lecture"):
    return Recording(delivery_id=did, course_id="c1", folder_id="f", folder_path="", name=f"rec {did}",
                     start=dt.datetime(2026, 10, 13, 11, 0) + dt.timedelta(minutes=minute), duration_s=dur,
                     viewer_url="u", kind=kind)


def test_merge_new_duplicates_and_empty_listing(tmp_path):
    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="c1", title="t", code="EEE 215", name="n")])
    assert lib.merge_recordings("c1", [rec("a", 0), rec("b", 50)], first_sync=True) == []
    assert not any(r.is_new for r in lib.recordings("c1"))
    # later sync: one new recording + an exact duplicate of it
    new = lib.merge_recordings("c1", [rec("a", 0), rec("b", 50), rec("c", 100), rec("d", 100, 2400.5)], first_sync=False)
    assert new == ["c"]
    d = lib.get_recording("d")
    assert d.duplicate_of == "c" and not d.is_new
    # an empty listing (auth hiccup) must not mark everything as gone
    lib.merge_recordings("c1", [], first_sync=False)
    assert len(lib.recordings("c1")) == 4


def test_a_reupload_with_sound_is_kept_and_the_silent_copy_is_its_duplicate(tmp_path):
    """The lecturer's first upload had no sound (it failed so); the fixed copy has the same start and length. The
    copy kept is the one with sound, also in a library that marked it the other way round before."""
    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="c1", title="t", code="MATH 250", name="n")])
    lib.merge_recordings("c1", [rec("a", 0)], first_sync=True)
    lib.set_warnings("a", [{"code": "no_speech", "level": "error", "message": "Panopto'da bu kaydın sesi yok."}])
    lib.set_status("a", "failed", "Panopto'da bu kaydın sesi yok.")      # (the words follow the UI's language)
    lib.merge_recordings("c1", [rec("a", 0), rec("b", 0, 2400.4)], first_sync=False)
    assert lib.get_recording("b").duplicate_of is None and lib.get_recording("a").duplicate_of == "b"
    lib2 = Library(tmp_path / "old.db")                  # failed before the warning existed: only its words say it
    lib2.upsert_courses([Course(id="c1", title="t", code="MATH 250", name="n")])
    lib2.merge_recordings("c1", [rec("a", 0), rec("b", 0, 2400.4)], first_sync=True)
    assert lib2.get_recording("b").duplicate_of == "a"
    lib2.set_status("a", "failed", "Panopto has no sound for this recording (only a picture).")
    lib2.merge_recordings("c1", [rec("a", 0), rec("b", 0, 2400.4)], first_sync=False)
    assert lib2.get_recording("b").duplicate_of is None and lib2.get_recording("a").duplicate_of == "b"


def test_a_transcribed_recording_panopto_replaced_is_not_kept_as_another_part(tmp_path):
    """EEE 242 Lecture5.2: the live upload broke off after 21 minutes and Panopto listed it as finished; days later
    the whole lecture came as a new session with the same start, and the first one left the folder. Transcribed in
    between, the old copy stayed as a part of its own: the day file, study pack and chats had its 21 minutes twice."""
    from studia.outputs.common import select_parts

    def t(did):
        return Transcript(delivery_id=did, segments=[Segment(0, 5, "x", "en", 0.9)], duration_s=5, speech_s=5,
                          language_seconds={"en": 5}, model="m", device="cpu", engine_version="x",
                          created_at="2026-10-20T10:00:00")

    for order in ("same sync", "later sync"):
        lib = Library(tmp_path / f"{order}.db")
        lib.upsert_courses([Course(id="c1", title="t", code="EEE 242", name="n")])
        lib.merge_recordings("c1", [rec("5.1", 0, 2700), rec("old", 116, 1288)], first_sync=True)
        lib.save_transcript(t("old"), tmp_path / "old.json")
        other = Recording(delivery_id="sec2", course_id="c1", folder_id="f", folder_path="", name="sec 2",
                          start=rec("old", 116).start, duration_s=3000, viewer_url="u", section_tag="2")
        if order == "later sync":                          # the old copy leaves first, the new one comes later
            lib.merge_recordings("c1", [rec("5.1", 0, 2700), other], first_sync=False)
            assert lib.get_recording("old").duplicate_of is None      # (not another section's lecture)
        lib.merge_recordings("c1", [rec("5.1", 0, 2700), rec("new", 116, 2138), other], first_sync=False)
        assert lib.get_recording("old").duplicate_of == "new", order
        day = [r for r in lib.recordings("c1") if r.section_tag == ""]
        assert [r.delivery_id for r in select_parts(day, {"old": t("old")})] == ["5.1", "old"]    # until it's done
        assert [r.delivery_id for r in select_parts(day, {"old": t("old"), "new": t("new")})] == ["5.1", "new"]
        lib.close()


def test_status_and_fts_search(tmp_path):
    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="c1", title="t", code="EEE 215", name="n")])
    lib.merge_recordings("c1", [rec("a", 0)], first_sync=False)
    t = Transcript(delivery_id="a", segments=[Segment(0, 5, "The Thevenin equivalent circuit", "en", 0.9),
                                              Segment(6, 9, "Arkadaşlar bu sınavda çıkacak", "tr", 0.9)],
                   duration_s=10, speech_s=9, language_seconds={"en": 5, "tr": 3}, model="m", device="cpu",
                   engine_version="x", created_at="2026-10-20T10:00:00")
    lib.save_transcript(t, tmp_path / "a.json")
    lib.set_status("a", "done")
    assert lib.get_recording("a").status == "done" and not lib.get_recording("a").is_new
    from studia.search import results

    lecture = lambda q: next(g["items"] for g in results(lib, q)["groups"] if g["kind"] == "lecture")[0]["moments"][0]
    assert lecture("theven")["delivery_id"] == "a"
    assert lecture("sınavda")["t"] == 6
    lib.set_status("a", "transcribing")
    lib.reset_interrupted()
    assert lib.get_recording("a").status == "done"   # transcript exists -> back to done


def test_an_empty_weekly_plan_keeps_the_weeks_and_removed_weeks_keep_downloaded_files(tmp_path):
    from studia.config import Settings
    from studia.coursesite import Resource, Unit
    from studia.views import course_detail

    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="c", title="2026-1 MATH 240 P 1", code="MATH 240", name="P", term="T")])
    weeks = [Unit(f"u{n}", n, f"Hafta {n}", dt.date(2026, 9, 21) + dt.timedelta(weeks=n - 1),
                  dt.date(2026, 9, 27) + dt.timedelta(weeks=n - 1), "",
                  [Resource(f"r{n}", f"Week {n}", f"w{n}.pdf", "pdf", "pdf", "u", "d")]) for n in (1, 2)]
    lib.merge_units("c", weeks, first_sync=True)
    lib.set_material_file("r1", str(tmp_path / "w1.pdf"), 10, "u")
    lib.merge_units("c", [], first_sync=False)                    # a page without the plan: a hiccup
    assert [u["unit_id"] for u in lib.units("c")] == ["u1", "u2"]
    lib.merge_units("c", weeks[1:], first_sync=False)            # week 1 removed from IEULearn
    d = course_detail(lib, Settings(), "c")
    (kept,) = [g for g in d["general"] if g["title"] == "No longer in the weekly plan"]
    assert [m["id"] for m in kept["materials"]] == ["r1"] and kept["materials"][0]["gone"]


def test_a_new_version_keeps_a_copy_of_the_library_first(tmp_path):
    path = tmp_path / "library.db"
    lib = Library(path, app_version="2.1.1")
    lib.upsert_courses([Course(id="c", title="t", code="MATH 240", name="P")])
    lib.close()
    Library(path, app_version="2.1.1").close()                   # same version: no copy
    assert not (tmp_path / "backups").exists()
    lib = Library(path, app_version="2.2.0")
    assert lib.get_meta("app_version") == "2.2.0" and lib.get_course("c") is not None
    lib.close()
    copy = Library(tmp_path / "backups" / "library-2.1.1.db")
    assert copy.get_course("c").code == "MATH 240"
    copy.close()
    Library(tmp_path / "fresh.db", app_version="2.2.0").close()  # a new library: nothing to keep
    assert [p.name for p in (tmp_path / "backups").iterdir()] == ["library-2.1.1.db"]


def test_a_course_that_left_the_dashboard_stays_while_it_holds_your_transcripts(tmp_path):
    from studia import store
    from studia.config import Settings
    from studia.domain import Segment, Transcript
    from studia.views import all_course_views

    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="a", title="t", code="MATH 240", name="P"),
                        Course(id="b", title="t", code="EEE 215", name="E"),
                        Course(id="c", title="t", code="PHYS 101", name="F")])
    lib.merge_recordings("b", [Recording(delivery_id="r1", course_id="b", folder_id="f", folder_path="", name="n",
                                         start=dt.datetime(2026, 10, 13, 9), duration_s=3000, viewer_url="v")],
                         first_sync=True)
    t = Transcript(delivery_id="r1", segments=[Segment(0, 5, "Hello.", "en", 0.9)], duration_s=3000, speech_s=5,
                   language_seconds={"en": 5}, model="m", device="cpu", engine_version="t", created_at="x")
    lib.save_transcript(t, store.save_transcript(t))
    lib.set_status("r1", "done")
    lib.upsert_courses([Course(id="a", title="t", code="MATH 240", name="P")])     # b and c left the dashboard
    views = all_course_views(lib, Settings(), [])
    assert [(v["code"], v["inactive"]) for v in views] == [("MATH 240", False), ("EEE 215", True)]   # c: nothing yours


def test_last_term_s_courses_marked_past_become_earlier_once_a_new_term_begins(tmp_path):
    from studia import store
    from studia.config import Settings
    from studia.views import all_course_views

    lib = Library(tmp_path / "lib.db")
    fall = [Course(id=f"f{i}", title=f"2026-1 EEE 21{i} X 1", code=f"EEE 21{i}", name="X", term="2026-1") for i in (1, 2)]
    lib.upsert_courses(fall)
    lib.upsert_courses([Course(**{**c.__dict__, "status": "past"}) for c in fall])     # the term's end: all past
    assert [c.id for c in lib.courses()] == ["f1", "f2"]                              # (exams, make-ups still come)
    spring = Course(id="s1", title="2026-2 EEE 301 Y 1", code="EEE 301", name="Y", term="2026-2")
    lib.upsert_courses([Course(**{**c.__dict__, "status": "past"}) for c in fall] + [spring])
    assert [c.id for c in lib.courses()] == ["s1"]
    lib.merge_recordings("f1", [rec("r1", 0)], first_sync=True)                        # f1 holds a transcript
    t = Transcript(delivery_id="r1", segments=[Segment(0, 9, "Hello", "en", 0.9)], duration_s=2400, speech_s=9,
                   language_seconds={"en": 9}, model="m", device="cpu", engine_version="t", created_at="2026-11-01T12:00:00")
    lib.save_transcript(t, store.save_transcript(t))
    views = {v["id"]: v for v in all_course_views(lib, Settings(), [])}
    assert sorted(views) == ["f1", "s1"] and views["f1"]["inactive"] and views["f1"]["past"] and not views["s1"]["past"]


def test_a_course_keeps_its_folder_when_a_second_course_with_its_code_arrives(tmp_path):
    import sqlite3
    from pathlib import Path

    from studia.outputs.writer import course_dir

    lib = Library(tmp_path / "lib.db")
    fall = Course(id="16988", title="2026-1 MATH 250 Linear Algebra 3", code="MATH 250", name="L", section="3")
    feng = [Course(id=f"e{s}", title=f"2026-1 FENG 495 P {s}", code="FENG 495", name="P", section=s) for s in "13"]
    lib.upsert_courses([fall, *feng])                       # twins that arrive together: named as they always were
    folder = lambda cid: course_dir(Path("out"), lib.get_course(cid), lib.courses(True)).name     # noqa: E731
    assert [folder(c) for c in ("16988", "e1", "e3")] == ["MATH 250", "FENG 495 (sec 1)", "FENG 495 (sec 3)"]
    retake = Course(id="20113", title="2027-1 MATH 250 Linear Algebra 3", code="MATH 250", name="L", section="3")
    again = Course(id="23001", title="2027-2 MATH 250 Linear Algebra 3", code="MATH 250", name="L", section="3")
    lib.upsert_courses([fall, *feng, retake])
    lib.upsert_courses([retake, again])                    # (the fall course gone from the dashboard)
    assert [folder(c) for c in ("16988", "20113", "23001")] == ["MATH 250", "MATH 250 (sec 3)", "MATH 250 (2027-2 sec 3)"]

    db = sqlite3.connect(tmp_path / "old.db")              # a library from before: the names it always had
    db.execute("CREATE TABLE courses(id TEXT PRIMARY KEY, title TEXT, code TEXT, name TEXT, instructor TEXT, term TEXT, "
               "status TEXT, section TEXT, folder_id TEXT, has_panopto INTEGER DEFAULT 1, position INTEGER DEFAULT 0, "
               "active INTEGER DEFAULT 1)")
    db.executemany("INSERT INTO courses(id, title, code, name, section) VALUES(?,?,?,?,?)",
                   [("a", "t", "EEE 242", "X", "1"), ("b", "t", "EEE 242", "X", "2"), ("c", "t", "EEE 215", "Y", "")])
    db.commit()
    db.close()
    old = Library(tmp_path / "old.db")
    assert {c.id: c.folder for c in old.courses(True)} == {"a": "EEE 242 (sec 1)", "b": "EEE 242 (sec 2)", "c": "EEE 215"}
