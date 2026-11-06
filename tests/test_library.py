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
    assert lib.search("theven")[0]["delivery_id"] == "a"
    assert lib.search("sınavda")[0]["t"] == 6
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
