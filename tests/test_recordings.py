"""Panopto recordings that are still being recorded, recordings uploaded days later (their name has the date), and the
simple checks that tell when something about a recording looks off."""

import datetime as dt
from pathlib import Path

import pytest
from test_jobs import FakePanopto, env, finals, wait_for  # noqa: F401 - env is a fixture
from test_sync import _helper_sync, _rec as _sync_rec, _service, site  # noqa: F401 - site is a fixture

from studia import store
from studia.anomalies import course_anomalies, for_ids
from studia.catchup import course_needs
from studia.config import Settings
from studia.domain import Course, Recording, Segment, Transcript
from studia.library import Library
from studia.panopto import _EPOCH, _head, name_start, parse_sessions

D = dt.datetime


def _date(when):
    return f"/Date({int((when - _EPOCH).total_seconds() * 1000)})/"


def _row(did, name, start, status=4, duration=3000.0):
    return {"DeliveryID": did.upper(), "SessionID": f"S-{did}", "SessionName": name, "StartTime": _date(start),
            "Duration": duration, "FolderID": "F", "ViewerUrl": f"https://v/{did}", "Status": status,
            "PodcastDetails": None if status != 4 else {"AudioPodcast": True}}


# ------------------------------------------------------------------ Panopto's listing
@pytest.mark.parametrize("name,when", [
    ("4 November 2026 Wednesday at 08:28:43", D(2026, 11, 4, 8, 28, 43)),
    ("24 October 2026 Saturday at 12_04_37_default_3648573c", D(2026, 10, 24, 12, 4, 37)),
    ("Friday, October 23, 2026 at 12:09:42 PM", D(2026, 10, 23, 12, 9, 42)),
    ("Lecture4.1-Friday, October 16, 2026 at 2:05:56 PM", D(2026, 10, 16, 14, 5, 56)),
    ("27 Ekim 2026 Salı at 08:16:48 EEE 215 Section 1 Online", D(2026, 10, 27, 8, 16, 48)),
    ("24 October 2026 Friday at 12_04_37_default_3648573c", None),          # the weekday doesn't match
    ("Wednesday, October 21, 2026 at 12:10 AM EEE 215 Section 2-3", None),    # typed by hand: no seconds
    ("Friday, October 2, 2026 at 18:50:00 PM", None),
    ("Week 5 lecture", None),
])
def test_the_date_and_time_in_a_recordings_name(name, when):
    assert name_start(name) == when


def test_parse_sessions_takes_the_names_date_for_an_upload_and_marks_one_being_recorded():
    rows = [
        _row("live", "4 November 2026 Wednesday at 08:28:43", D(2026, 11, 4, 8, 31, 39), status=2, duration=1312.4),
        _row("up1", "24 October 2026 Saturday at 12_04_37_default_3648573c", D(2026, 11, 3, 8, 11)),
        _row("up2", "21 October 2026 Wednesday at 08_29_03_default_575f3a88", D(2026, 10, 29, 22, 5, 13)),
        _row("m250", "Friday, October 23, 2026 at 12:09:42 PM", D(2026, 10, 23, 12, 9, 59)),
        # a recorder on another time zone: three hours off, to the minute; not an upload
        _row("tz", "27 Ekim 2026 Salı at 08:16:48 EEE 215 Section 1 Online", D(2026, 10, 27, 11, 17, 20)),
        _row("old", "Friday, October 23, 2025 at 12:09:42 PM", D(2026, 10, 23, 12, 9, 59)),   # (not that year)
    ]
    got = {r.delivery_id: r for r in parse_sessions({"Results": rows, "TotalNumber": len(rows)}, "17933", "")}
    assert got["live"].live and got["live"].start == D(2026, 11, 4, 8, 31, 39) and not got["live"].uploaded
    assert not any(r.live for k, r in got.items() if k != "live")
    assert got["up1"].start == D(2026, 10, 24, 12, 4, 37) and got["up1"].uploaded == "2026-11-03T08:11:00"
    assert got["up2"].start == D(2026, 10, 21, 8, 29, 3) and got["up2"].uploaded == "2026-10-29T22:05:13"
    for k, start in (("m250", D(2026, 10, 23, 12, 9, 59)), ("tz", D(2026, 10, 27, 11, 17, 20)),
                     ("old", D(2026, 10, 23, 12, 9, 59))):
        assert got[k].start == start and got[k].uploaded == "", k


def test_the_probe_head_carries_the_newest_recordings_status():
    d = {"TotalNumber": 22, "Results": [{"DeliveryID": "C0E6FE5F-1", "Status": 2}]}
    assert _head(d) == [22, "c0e6fe5f-1", 2]
    assert _head({"TotalNumber": 0, "Results": []}) == [0, "", None]


# ------------------------------------------------------------------ the library
def _rec(did, start, live=False, uploaded="", dur=3000.0, cid="c", kind="lecture", name=None):
    return Recording(delivery_id=did, course_id=cid, folder_id="f", folder_path="", name=name or did, start=start,
                     duration_s=dur, viewer_url=f"https://v/{did}", live=live, uploaded=uploaded, kind=kind)


def _lib(tmp_path):
    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="c", title="2026-1 MATH 240 P 1", code="MATH 240", name="Probability")])
    return lib


def _transcribe(lib, did):
    t = Transcript(delivery_id=did, segments=[Segment(0, 5, f"words of {did}", "en", 0.9)], duration_s=10,
                   speech_s=9, language_seconds={"en": 9}, model="m", device="cpu", engine_version="x",
                   created_at="2026-10-30T10:00:00")
    lib.save_transcript(t, store.save_transcript(t))
    lib.set_status(did, "done")


def test_one_being_recorded_is_new_when_it_has_finished(tmp_path):
    lib = _lib(tmp_path)
    lib.merge_recordings("c", [_rec("a", D(2026, 10, 28, 8, 30))], first_sync=True)
    assert lib.merge_recordings("c", [_rec("a", D(2026, 10, 28, 8, 30)), _rec("b", D(2026, 11, 4, 8, 31), True)],
                                first_sync=False) == []                    # not told about yet
    assert lib.get_recording("b").live and lib.live_courses() == {"c"}
    assert lib.live_courses("2026-11-05") == set()                         # (only the recent ones)
    lib.set_status("b", "processing", "This lecture is still being recorded.")    # someone asked for it anyway
    assert lib.merge_recordings("c", [_rec("a", D(2026, 10, 28, 8, 30)), _rec("b", D(2026, 11, 4, 8, 31), True)],
                                first_sync=False) == []
    new = lib.merge_recordings("c", [_rec("a", D(2026, 10, 28, 8, 30)), _rec("b", D(2026, 11, 4, 8, 31))],
                               first_sync=False)
    b = lib.get_recording("b")
    assert new == ["b"] and not b.live and b.status == "available" and b.error == "" and lib.live_courses() == set()


def test_one_that_was_not_ready_on_panopto_after_it_finished_is_tried_again_by_a_batch(tmp_path):
    """A lecture queued just after it finished, while Panopto still encodes it, gets "not ready": the next listing
    (unchanged) makes it ready to try again, and a full or course sync takes it."""
    lib = _lib(tmp_path)
    recs = [_rec("a", D(2026, 10, 28, 8, 30)), _rec("b", D(2026, 11, 4, 8, 31))]
    lib.merge_recordings("c", recs, first_sync=True)
    lib.set_status("b", "processing", "Panopto is still processing this recording. Try again later.")
    assert [d for d, _ in course_needs(lib, Settings())[0].lectures] == ["a"]
    assert lib.merge_recordings("c", recs, first_sync=False) == []          # (not new again)
    b = lib.get_recording("b")
    assert (b.status, b.error) == ("available", "")
    assert [d for d, _ in course_needs(lib, Settings())[0].lectures] == ["b", "a"]
    lib.merge_recordings("c", [_rec("b", D(2026, 11, 4, 8, 31), True)], first_sync=False)
    lib.set_status("b", "processing", "This lecture is still being recorded.")
    lib.merge_recordings("c", [_rec("b", D(2026, 11, 4, 8, 31), True)], first_sync=False)
    assert lib.get_recording("b").status == "processing"                   # still being recorded: stays


def test_an_upload_gets_its_names_date_and_a_transcribed_one_is_noted_to_move(tmp_path):
    lib = _lib(tmp_path)
    late = D(2026, 11, 3, 8, 11)
    lib.merge_recordings("c", [_rec("u1", late), _rec("u2", late, dur=2500)], first_sync=True)    # as 2.6.1 stored them
    _transcribe(lib, "u1")
    lib.merge_recordings("c", [_rec("u1", D(2026, 10, 24, 12, 4, 37), uploaded=late.isoformat()),
                               _rec("u2", D(2026, 10, 24, 13, 12, 46), uploaded=late.isoformat(), dur=2500)],
                         first_sync=False)
    u1 = lib.get_recording("u1")
    assert u1.start == D(2026, 10, 24, 12, 4, 37) and u1.uploaded == "2026-11-03T08:11:00" and u1.status == "done"
    assert lib.day_moves() == [["u1", "c|2026-11-03|lecture"]]           # u2 has no files yet
    assert lib.day_moves() == [["u1", "c|2026-11-03|lecture"]]           # noted until done
    lib.drop_day_moves(lib.day_moves())
    assert lib.day_moves() == []
    lib.merge_recordings("c", [_rec("u1", D(2026, 10, 24, 12, 4, 37), uploaded=late.isoformat()),
                               _rec("u2", D(2026, 10, 24, 13, 12, 46), uploaded=late.isoformat(), dur=2500)],
                         first_sync=False)
    assert lib.day_moves() == []                                          # nothing changed


def test_a_batch_leaves_out_one_being_recorded(tmp_path):
    lib = _lib(tmp_path)
    lib.merge_recordings("c", [_rec("a", D(2026, 10, 28, 8, 30)), _rec("b", D(2026, 11, 4, 8, 31), True)],
                         first_sync=True)
    (need,) = course_needs(lib, Settings(), ["c"])
    assert [d for d, _ in need.lectures] == ["a"]


def test_a_job_for_one_being_recorded_ends_before_asking_panopto(env):
    env["lib"].merge_recordings("c", [_rec("r1", D(2026, 10, 13, 11, 0), True, dur=2400)], first_sync=False)
    asked = []
    env["mp"].setattr(FakePanopto, "delivery_info", lambda self, did: asked.append(did))
    jm = env["make"]()
    jm.enqueue(["r1"])
    assert wait_for(lambda: finals(env["events"], "r1"))
    assert finals(env["events"], "r1")[0]["stage"] == "processing" and asked == []
    assert "still being recorded" in env["lib"].get_recording("r1").error


def test_asking_for_one_being_recorded_says_so(isolated_home):
    from studia.api import Api
    from studia.services import Services

    api = Api(Services())
    lib = api._s.lib
    lib.upsert_courses([Course(id="c", title="2026-1 MATH 240 P 1", code="MATH 240", name="Probability")])
    lib.merge_recordings("c", [_rec("a", D(2026, 10, 28, 8, 30)), _rec("b", D(2026, 11, 4, 8, 31), True)],
                         first_sync=True)
    asked = []
    api._s.jobs.enqueue = lambda ids, batch=False: asked.append(list(ids)) or len(ids)
    api._push = lambda evt: None
    api._s.jobs.queue_event = lambda: {}
    res = api.transcribe(["b"])
    assert not res["ok"] and "still being recorded" in res["error"] and asked == []
    assert api.transcribe(["a", "b"])["queued"] == 1 and asked == [["a"]]


def test_the_helper_lists_a_course_with_one_being_recorded_in_full(site, tmp_path):
    lib, sync = _service(tmp_path)
    sync.sync()
    now = dt.datetime.now().replace(microsecond=0)
    live = Recording(**{**vars(_sync_rec("a", 0)), "delivery_id": "alive", "start": now, "live": True})
    site.listing["a"] = [_sync_rec("a", 0), live]
    assert sync.sync().new_ids == []                     # the app: being recorded, not told about yet
    site.asked.clear()
    assert _helper_sync(sync).new_ids == []              # its probe looks the same, but it is listed in full
    assert sorted(site.asked) == [("list", "a"), ("probe", "b")]
    site.listing["a"] = [_sync_rec("a", 0), Recording(**{**vars(live), "live": False})]
    site.asked.clear()
    assert _helper_sync(sync).new_ids == ["alive"]       # finished: told about now
    site.asked.clear()
    _helper_sync(sync)
    assert sorted(site.asked) == [("probe", "a"), ("probe", "b")]


# ------------------------------------------------------------------ files follow a lecture to its class day
def test_a_transcribed_lecture_moves_to_its_class_day(isolated_home, tmp_path):
    from studia.outputs.writer import course_dir, day_file_name
    from studia.services import Services

    s = Services()
    try:
        s.settings.output_dir = str(tmp_path / "out")
        lib = s.lib
        course = Course(id="c", title="2026-1 MATH 240 P 1", code="MATH 240", name="Probability")
        lib.upsert_courses([course])
        late = D(2026, 11, 3, 8, 11)
        # two uploads and the lecture really held that day share the upload's day
        lib.merge_recordings("c", [_rec("u1", late), _rec("u2", late, dur=2500),
                                   _rec("held", D(2026, 11, 3, 13, 0))], first_sync=True)
        for did in ("u1", "u2", "held"):
            _transcribe(lib, did)
            s.write_outputs(did)
        folder = course_dir(s.output_root, course, lib.courses(True))
        old_day, new_day = folder / day_file_name(course, late.date(), "lecture"), \
            folder / day_file_name(course, dt.date(2026, 10, 24), "lecture")
        assert old_day.is_file() and "words of u1" in old_day.read_text(encoding="utf-8")
        listing = [_rec("u1", D(2026, 10, 24, 12, 4, 37), uploaded=late.isoformat()),
                   _rec("u2", D(2026, 10, 24, 13, 12, 46), uploaded=late.isoformat(), dur=2500),
                   _rec("held", D(2026, 11, 3, 13, 0))]
        lib.merge_recordings("c", listing, first_sync=False)
        assert s.move_days() == 2
        text = new_day.read_text(encoding="utf-8")
        assert "words of u1" in text and "words of u2" in text
        left = old_day.read_text(encoding="utf-8")                # the lecture held that day stays
        assert "words of held" in left and "words of u1" not in left
        # the one held that day moves too (its name had the date): nothing is left there, so the old day goes
        lib.merge_recordings("c", listing[:2] + [_rec("held", D(2026, 10, 24, 14, 10, 38), uploaded=late.isoformat())],
                             first_sync=False)
        assert s.move_days() == 1 and not old_day.exists() and "words of held" in new_day.read_text(encoding="utf-8")
        assert s.move_days() == 0
    finally:
        s.shutdown()
        s.lib.close()


def test_a_move_stays_noted_until_done_and_the_chats_follow_it(isolated_home, tmp_path):
    import json

    from studia.outputs.writer import course_dir, day_file_name, day_stem
    from studia.services import Services

    s = Services()
    try:
        s.settings.output_dir = str(tmp_path / "out")
        s.settings.write_srt = False
        lib = s.lib
        course = Course(id="c", title="2026-1 MATH 240 P 1", code="MATH 240", name="Probability")
        lib.upsert_courses([course])
        late, early = D(2026, 11, 3, 8, 11), D(2026, 10, 20, 8, 30)
        lib.merge_recordings("c", [_rec("early", early), _rec("u1", late), _rec("u2", late, dur=2500),
                                   _rec("held", D(2026, 11, 3, 13, 0))], first_sync=True)
        for did in ("early", "u1", "u2", "held"):
            _transcribe(lib, did)
            s.write_outputs(did)
        folder = course_dir(s.output_root, course, lib.courses(True))
        kept_srt = folder / "subtitles" / f"{day_stem(course, late.date(), 'lecture')} - Part 1.srt"
        kept_srt.parent.mkdir(parents=True, exist_ok=True)
        kept_srt.write_text("kept", encoding="utf-8")        # subtitles written while they were on
        old_key, new_key, early_key = "c|2026-11-03|lecture", "c|2026-10-24|lecture", "c|2026-10-20|lecture"
        lib.create_chat("ch1", "c", {"days": [early_key, old_key], "focus": "course"}, "sonnet")
        lib.create_chat("ch2", "c", {"days": [old_key]}, "sonnet")
        listing = [_rec("early", early), _rec("u1", D(2026, 10, 24, 12, 4, 37), uploaded=late.isoformat()),
                   _rec("u2", D(2026, 10, 24, 13, 12, 46), uploaded=late.isoformat(), dur=2500),
                   _rec("held", D(2026, 11, 3, 13, 0))]
        lib.merge_recordings("c", listing, first_sync=False)
        assert lib.day_moves() == [["u1", old_key], ["u2", old_key]]

        leave = s._leave_day
        def failing(course, all_courses, key, rec, done, fail=("u1", RuntimeError)):
            if rec.delivery_id == fail[0]:
                raise fail[1]("no")
            leave(course, all_courses, key, rec, done)
        s._leave_day = failing                                 # the first fails: the next is done, the first kept
        assert s.move_days() == 1 and lib.day_moves() == [["u1", old_key]]
        lib.set_meta("day_moves", json.dumps([["u1", old_key], ["u2", old_key]]))   # u2 again, as after a stop
        s._leave_day = lambda *a: failing(*a, fail=("u2", KeyboardInterrupt))
        with pytest.raises(KeyboardInterrupt):                 # stopped at u2: u1 is done, u2 stays noted
            s.move_days()
        assert lib.day_moves() == [["u2", old_key]]
        s._leave_day = leave
        assert s.move_days() == 1 and lib.day_moves() == []    # done again: harmless
        new_day = folder / day_file_name(course, dt.date(2026, 10, 24), "lecture")
        old_day = folder / day_file_name(course, late.date(), "lecture")
        text, left = new_day.read_text(encoding="utf-8"), old_day.read_text(encoding="utf-8")
        assert text.count("words of u1") == 1 and text.count("words of u2") == 1
        assert "words of held" in left and "words of u1" not in left and "words of u2" not in left
        assert kept_srt.read_text(encoding="utf-8") == "kept"   # subtitles off: the old day's are left alone
        # the old day keeps a lecture: the new day comes right after it, once
        assert lib.get_chat("ch1")["scope"] == {"days": [early_key, old_key, new_key], "focus": "course"}
        assert lib.get_chat("ch2")["scope"]["days"] == [old_key, new_key]
        # the last one leaves: the new day takes the old day's place (no second copy)
        lib.merge_recordings("c", listing[:3] + [_rec("held", D(2026, 10, 24, 14, 10, 38), uploaded=late.isoformat())],
                             first_sync=False)
        assert s.move_days() == 1
        assert lib.get_chat("ch1")["scope"]["days"] == [early_key, new_key]
        assert lib.get_chat("ch2")["scope"]["days"] == [new_key]
        lib.set_meta("day_moves", json.dumps([["held", old_key]]))
        assert s.move_days() == 1 and lib.get_chat("ch2")["scope"]["days"] == [new_key]
    finally:
        s.shutdown()
        s.lib.close()


def test_a_lecture_moved_to_another_of_your_courses_takes_its_files_there(isolated_home, tmp_path):
    """A lecturer moves a transcribed recording between the two FENG 495 sections' folders: the next sync gave it to
    the other course, but its day file, AI notes and slides stayed in the old course's folder (the new one got none),
    and the old course's study pack kept listing it."""
    from studia.outputs.writer import course_dir, day_file_name, day_stem
    from studia.services import Services
    from studia.slides import Slide

    s = Services()
    try:
        s.settings.output_dir = str(tmp_path / "out")
        lib = s.lib
        one = Course(id="17855", title="2026-1 FENG 495 Sec 1", code="FENG 495", name="Project", section="1")
        three = Course(id="19456", title="2026-1 FENG 495 Sec 3", code="FENG 495", name="Project", section="3")
        lib.upsert_courses([one, three])
        when = D(2026, 10, 8, 10, 0)
        lib.merge_recordings("17855", [_rec("x", when, cid="17855"), _rec("y", D(2026, 10, 1, 10, 0), cid="17855")],
                             first_sync=True)
        lib.merge_recordings("19456", [_rec("z", D(2026, 10, 9, 10, 0), cid="19456")], first_sync=True)
        for did in ("x", "y", "z"):
            _transcribe(lib, did)
            s.write_outputs(did)
        old_dir = course_dir(s.output_root, one, lib.courses(True))
        new_dir = course_dir(s.output_root, three, lib.courses(True))
        assert old_dir != new_dir
        old_stem, new_stem = day_stem(one, when.date(), "lecture"), day_stem(three, when.date(), "lecture")
        pic = old_dir / "slides" / old_stem / "1000-x-01.jpg"
        pic.parent.mkdir(parents=True, exist_ok=True)
        pic.write_bytes(b"jpg")
        store.save_slides("x", [Slide(1, 5.0, 60.0, f"slides/{old_stem}/{pic.name}", str(pic)),
                                Slide(2, 70.0, 90.0, f"slides/{old_stem}/{pic.name}", str(pic))])    # shown twice
        s.write_outputs("x")
        notes = s._notes_path(old_dir, old_stem, "notes")
        notes.parent.mkdir(parents=True, exist_ok=True)
        notes.write_text("my notes of x", encoding="utf-8")
        lib.create_chat("ch", "17855", {"days": [f"17855|{when.date()}|lecture"]}, "sonnet")
        # the lecturer moved x: 17855 lists it no more, 19456 does
        lib.merge_recordings("17855", [_rec("y", D(2026, 10, 1, 10, 0), cid="17855")], first_sync=False)
        lib.merge_recordings("19456", [_rec("z", D(2026, 10, 9, 10, 0), cid="19456"), _rec("x", when, cid="19456")],
                             first_sync=False)
        assert lib.get_recording("x").course_id == "19456"
        assert lib.day_moves() == [["x", f"17855|{when.date()}|lecture"]]
        assert s.move_days() == 1 and lib.day_moves() == []
        assert not (old_dir / day_file_name(one, when.date(), "lecture")).exists()
        new_day = new_dir / day_file_name(three, when.date(), "lecture")
        assert "words of x" in new_day.read_text(encoding="utf-8")
        assert s._notes_path(new_dir, new_stem, "notes").read_text(encoding="utf-8") == "my notes of x"
        moved = store.load_slides("x")
        assert {sl.image for sl in moved} == {f"slides/{new_stem}/{pic.name}"} and not pic.exists()
        assert all(Path(sl.path).read_bytes() == b"jpg" and Path(sl.path).parent.parent.parent == new_dir
                   for sl in moved)                                # one file for the slide shown twice
        assert not pic.parent.exists()                             # its old folder held nothing else
        assert lib.get_chat("ch")["scope"]["days"] == [f"17855|{when.date()}|lecture"]    # a chat stays with its course
        packs = {c.id: sorted(course_dir(s.output_root, c, lib.courses(True)).glob("*tudy pack*")) for c in (one, three)}
        assert packs["17855"] and packs["19456"]                   # both courses' study packs written again
        assert not any("words of x" in p.read_text(encoding="utf-8") for p in packs["17855"] if p.suffix == ".md")
        assert any("words of x" in p.read_text(encoding="utf-8") for p in packs["19456"] if p.suffix == ".md")
    finally:
        s.shutdown()
        s.lib.close()


def _twins(s, b_section, folders, files, days):
    """MATH 250 taken in 2026-1 (course 100, section 1) and course 200 with the same code (a retake in 2027-1, or
    another section) that came before 3.3.1, its folders as they were then: ``folders`` as 3.3.1 named them, ``files``
    {resource id: folder} its downloaded files, ``days`` {date: folder} its day files."""
    from studia.coursesite import Resource, Unit
    from studia.services import TWINS_KEY

    lib = s.lib
    lib.upsert_courses([Course(id="200", title=f"2027-1 MATH 250 Linear Algebra {b_section}", code="MATH 250",
                               name="Linear Algebra", section=b_section, position=0),
                        Course(id="100", title="2026-1 MATH 250 Linear Algebra 1", code="MATH 250", name="Linear Algebra",
                               section="1", position=1)])
    lib.merge_recordings("100", [_rec("a1", D(2026, 9, 24, 10, 0), cid="100"), _rec("a2", D(2026, 10, 8, 10, 0), cid="100")],
                         first_sync=True)
    lib.merge_recordings("200", [_rec("b1", D(2027, 10, 7, 10, 0), cid="200")], first_sync=True)
    for cid, when in (("100", "2026-09-20T10:00:00"), ("200", "2027-10-01T10:00:00")):   # 100 came first
        lib.db.execute("UPDATE recordings SET first_seen=? WHERE course_id=?", (when, cid))
    lib.db.commit()
    for did in ("a1", "a2", "b1"):
        _transcribe(lib, did)
    for cid in ("100", "200"):
        rids = [rid for rid in files if rid.startswith("ma" if cid == "100" else "mb")]
        lib.merge_units(cid, [Unit(f"u{cid}", 0, "Week 1", resources=[Resource(rid, rid, f"{rid}.pdf", "resource", "pdf",
                                                                                 f"https://l/{rid}", "") for rid in rids])],
                        first_sync=True)
    lib.set_folders(folders)
    root = s.output_root
    for rid, folder in files.items():
        path = root / folder / "Materials" / "Week 01 - Week 1" / f"{rid}.pdf"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(rid.encode())
        lib.set_material_file(rid, str(path), 3, "u")
    for day, folder in days.items():
        (root / folder).mkdir(parents=True, exist_ok=True)
        (root / folder / f"MATH 250 - {day} Thu - Lecture.md").write_text("old copy", encoding="utf-8")
    lib.set_meta(TWINS_KEY, "")
    return lib, root


def _where(lib, root):
    """Each downloaded file's course folder, and that it is the course's own."""
    out = {}
    for cid in ("100", "200"):
        for m in lib.materials(cid):
            path = Path(m["local_path"])
            assert path.read_bytes() == m["resource_id"].encode()
            out[m["resource_id"]] = path.relative_to(root).parts[0]
            assert out[m["resource_id"]] == lib.get_course(cid).folder
    return out


def _day_files(lib, root, written=("100", "200")):
    """Each course's day files are in its own folder: written again there (``written``), or left as they were."""
    for cid, days in (("100", ["2026-09-24", "2026-10-08"]), ("200", ["2027-10-07"])):
        for day in days:
            text = (root / lib.get_course(cid).folder / f"MATH 250 - {day} Thu - Lecture.md").read_text(encoding="utf-8")
            assert ("words of" in text) == (cid in written), (cid, day)


def test_a_retake_from_before_331_gives_the_first_course_its_folder_back(isolated_home, tmp_path):
    """The same section's retake shared the first course's folder until 3.3.1, and 3.3.1 then named that folder for
    the retake (listed first on the dashboard) and an empty new one for the first course: its files seemed gone."""
    from studia.services import Services

    s = Services()
    try:
        s.settings.output_dir = str(tmp_path / "out")
        shared = "MATH 250 (sec 1)"
        lib, root = _twins(s, "1", {"100": "MATH 250 (2026-1 sec 1)", "200": shared},
                           {"ma1": shared, "ma2": shared, "mb1": shared},
                           {"2026-09-24": shared, "2026-10-08": shared, "2027-10-07": shared})
        notes = s._notes_path(root / shared, "MATH 250 - 2026-09-24 Thu - Lecture", "notes")
        notes.parent.mkdir(parents=True, exist_ok=True)
        notes.write_text("my notes of a1", encoding="utf-8")
        assert s.repair_twin_folders() == 1                     # the retake's file: the first course's stay
        assert lib.get_course("100").folder == shared and lib.get_course("200").folder == "MATH 250 (2027-1 sec 1)"
        assert _where(lib, root) == {"ma1": shared, "ma2": shared, "mb1": "MATH 250 (2027-1 sec 1)"}
        assert notes.read_text(encoding="utf-8") == "my notes of a1"
        _day_files(lib, root)
        assert not (root / shared / "MATH 250 - 2027-10-07 Thu - Lecture.md").exists()   # the retake's old copy
        assert s.repair_twin_folders() == 0                     # once
    finally:
        s.shutdown()
        s.lib.close()


def test_the_folders_are_put_right_once_the_output_folder_can_be_reached(isolated_home, tmp_path):
    """The output folder on a USB or network drive that isn't connected at the first 4.0 start: the one-time repair
    was marked done without having looked, and the courses' files stayed mixed for good."""
    from studia.services import TWINS_KEY, Services

    s = Services()
    try:
        s.settings.output_dir = str(tmp_path / "out")
        shared = "MATH 250 (sec 1)"
        lib, root = _twins(s, "1", {"100": "MATH 250 (2026-1 sec 1)", "200": shared},
                           {"ma1": shared, "ma2": shared, "mb1": shared},
                           {"2026-09-24": shared, "2026-10-08": shared, "2027-10-07": shared})
        (tmp_path / "usb").write_bytes(b"")
        s.settings.output_dir = str(tmp_path / "usb" / "IEUStudia")    # a folder that can't be reached
        assert s.repair_twin_folders() == 0 and lib.get_meta(TWINS_KEY) != "1"     # still to do
        s.settings.output_dir = str(root)                                # the drive is back at a later start
        assert s.repair_twin_folders() == 1 and lib.get_meta(TWINS_KEY) == "1"
        assert _where(lib, root) == {"ma1": shared, "ma2": shared, "mb1": "MATH 250 (2027-1 sec 1)"}
    finally:
        s.shutdown()
        s.lib.close()


@pytest.mark.parametrize("b_section", ["1", "3"])
def test_the_first_course_s_files_from_before_its_twin_join_the_rest(isolated_home, tmp_path, b_section):
    """The first course was alone at first ("MATH 250"); once its twin came (before 3.3.1) its new files went to
    "MATH 250 (sec 1)", shared with a retake of the same section: its files were in two folders."""
    from studia.services import Services

    s = Services()
    try:
        s.settings.output_dir = str(tmp_path / "out")
        twin = "MATH 250 (sec 1)" if b_section == "1" else "MATH 250 (sec 3)"
        lib, root = _twins(s, b_section, {"100": "MATH 250 (sec 1)" if b_section == "3" else "MATH 250 (2026-1 sec 1)",
                                          "200": twin},
                           {"ma1": "MATH 250", "ma2": "MATH 250", "ma3": "MATH 250 (sec 1)", "mb1": twin},
                           {"2026-09-24": "MATH 250", "2026-10-08": "MATH 250 (sec 1)", "2027-10-07": twin})
        mine = root / "MATH 250 (copy)" / "MATH 250 - 2026-09-24 Thu - Lecture.md"     # a copy you made yourself
        mine.parent.mkdir()
        mine.write_text("kept", encoding="utf-8")
        s.repair_twin_folders()
        assert mine.read_text(encoding="utf-8") == "kept"
        assert lib.get_course("100").folder == "MATH 250"       # where most of its files are: its folder from before
        assert lib.get_course("200").folder == twin
        assert _where(lib, root) == {"ma1": "MATH 250", "ma2": "MATH 250", "ma3": "MATH 250", "mb1": twin}
        _day_files(lib, root, written=("100",))                 # the twin's files were all in its folder: left alone
        if b_section == "3":                                     # nothing of anyone's left there
            assert not (root / "MATH 250 (sec 1)").exists()
        else:
            assert not (root / twin / "MATH 250 - 2026-10-08 Thu - Lecture.md").exists()
    finally:
        s.shutdown()
        s.lib.close()


def test_a_file_already_in_place_or_held_is_never_written_over(tmp_path):
    from studia.services import _move_aside

    a, b = tmp_path / "a" / "x.pdf", tmp_path / "b" / "x.pdf"
    a.parent.mkdir()
    b.parent.mkdir()
    a.write_bytes(b"same")
    b.write_bytes(b"same")
    assert _move_aside(a, b) == b and not a.exists()           # the same file: this copy goes
    a.write_bytes(b"other")
    moved = _move_aside(a, b)
    assert moved.name == "x (2).pdf" and moved.read_bytes() == b"other" and b.read_bytes() == b"same"


# ------------------------------------------------------------------ what looks off
NOW = D(2026, 11, 4, 12, 0)


def _term(n=6, weekday_start=D(2026, 9, 23, 8, 30), dur=3000.0):
    """A course that meets on Wednesdays: one lecture a week."""
    return [_rec(f"w{i}", weekday_start + dt.timedelta(weeks=i), dur=dur) for i in range(n)]


def _kinds(found, did):
    return [a["kind"] for a in found.get(did, [])]


def test_nothing_looks_off_in_an_ordinary_term():
    assert course_anomalies(_term(), now=NOW) == {}


def test_an_upload_says_when_it_was_uploaded():
    found = course_anomalies(_term() + [_rec("u", D(2026, 10, 21, 9, 34, 56), uploaded="2026-10-29T22:05:07")],
                             now=NOW)
    assert found["u"] == [{"kind": "uploaded", "text": "Uploaded to Panopto on Thursday, 29 October 2026; the date in "
                                                       "its name is used"}]


def test_being_recorded_and_still_processing():
    recs = _term() + [_rec("live", D(2026, 11, 4, 8, 31), live=True, dur=900)]
    recs[0].status = "processing"
    found = course_anomalies(recs, now=NOW)
    assert _kinds(found, "live") == ["live"] and _kinds(found, "w0") == ["processing"]
    recs[0].status = "available"
    assert "w0" not in course_anomalies(recs, now=NOW)


def test_the_same_start_minute():
    late = D(2026, 11, 3, 8, 11)
    recs = _term(4) + [_rec("x", late), _rec("y", late + dt.timedelta(seconds=20), dur=2000)]
    found = course_anomalies(recs, now=NOW)
    assert _kinds(found, "x") == ["same_start"] and _kinds(found, "y") == ["same_start"]
    recs[-1].duplicate_of = "x"                          # marked a duplicate: not an anomaly
    assert course_anomalies(recs, now=NOW) == {}


def test_a_lecture_off_the_courses_days_only_with_a_clear_pattern():
    sat = _rec("sat", D(2026, 10, 24, 12, 4, 37))
    found = course_anomalies(_term(6) + [sat], now=NOW)
    assert _kinds(found, "sat") == ["off_day"] and "Saturday" in found["sat"][0]["text"]
    assert "Wednesday" in found["sat"][0]["text"]
    assert course_anomalies(_term(5) + [sat], now=NOW) == {}                # too early in the term
    spread = [_rec(f"s{i}", D(2026, 9, 21, 9, 0) + dt.timedelta(days=i % 5, weeks=i // 5)) for i in range(10)]
    assert course_anomalies(spread + [sat], now=NOW) == {}                  # no clear pattern (five weekdays)
    lab = _rec("lab", D(2026, 10, 23, 8, 30), kind="lab")
    assert "lab" not in course_anomalies(_term(6) + [lab], now=NOW)         # (labs have their own days)


def test_much_shorter_or_longer_than_usual():
    recs = _term() + [_rec("short", D(2026, 10, 28, 10, 0), dur=600), _rec("long", D(2026, 10, 21, 10, 0), dur=4 * 3600)]
    found = course_anomalies(recs, now=NOW, min_s=300)
    assert _kinds(found, "short") == ["short"] and _kinds(found, "long") == ["long"]
    assert "50 min" in found["short"][0]["text"]
    assert {k for k in found} == {"short", "long"}
    # under the shortest length the app takes: shown as too short already
    assert "short" not in course_anomalies(recs, now=NOW, min_s=900)
    # a course of short clips: its short ones are not flagged
    clips = _term(dur=900) + [_rec("clip", D(2026, 10, 28, 10, 0), dur=200)]
    assert course_anomalies(clips, now=NOW) == {}
    # one class day recorded in one go (three lessons) is not "long"
    parts = [_rec(f"p{i}{j}", D(2026, 9, 23, 8, 30) + dt.timedelta(weeks=i, minutes=60 * j)) for i in range(6)
             for j in range(3)]
    assert course_anomalies(parts + [_rec("whole", D(2026, 11, 4, 8, 30), dur=9000)], now=NOW) == {}


def test_a_date_in_the_future():
    found = course_anomalies(_term() + [_rec("f", NOW + dt.timedelta(hours=3))], now=NOW)
    assert _kinds(found, "f") == ["future"]
    assert course_anomalies(_term() + [_rec("f", NOW + dt.timedelta(minutes=30))], now=NOW) == {}


def test_no_sound():
    recs = _term()
    warnings = {"w1": [{"code": "no_speech", "level": "error", "message": "No speech."}],
                "w2": [{"code": "low_confidence", "level": "warning", "message": "Hard to hear."}]}
    found = course_anomalies(recs, warnings, now=NOW)
    assert _kinds(found, "w1") == ["no_sound"] and "w2" not in found


def test_anomalies_for_the_jobs_pane(tmp_path):
    lib = _lib(tmp_path)
    lib.merge_recordings("c", _term() + [_rec("u", D(2026, 10, 21, 9, 34), uploaded="2026-10-29T22:05:07")],
                         first_sync=True)
    got = for_ids(lib, ["u", "w1", "nope"])
    assert list(got) == ["u"] and got["u"][0]["kind"] == "uploaded"
    assert for_ids(lib, []) == {}


def test_a_queued_lecture_takes_its_anomalies_to_the_jobs_pane(env):
    env["lib"].merge_recordings("c", [_rec("r1", D(2026, 10, 21, 9, 34), uploaded="2026-10-29T22:05:07")],
                                first_sync=False)
    env["make"]().enqueue(["r1"])
    assert wait_for(lambda: finals(env["events"], "r1"))
    jobs = [e for e in env["events"] if e.get("type") == "job" and e.get("id") == "r1"]
    assert all([a["kind"] for a in e["anomalies"]] == ["uploaded"] for e in jobs)


def test_the_course_and_class_day_views_show_them(tmp_path):
    from studia.views import course_view

    lib = _lib(tmp_path)
    lib.merge_recordings("c", _term(5) + [_rec("live", D(2026, 10, 29, 8, 31), live=True, dur=900),
                                         _rec("u", D(2026, 10, 21, 9, 34), uploaded="2026-10-29T22:05:07")],
                         first_sync=True)
    cv = course_view(lib.get_course("c"), lib.recordings("c"), Settings(), {}, {}, set())
    days = {d["date"]: d for d in cv["days"]}
    live = days["2026-10-29"]
    assert live["live"] and live["recordings"][0]["status"] == "live"
    assert [a["kind"] for a in live["recordings"][0]["anomalies"]] == ["live"]
    assert days["2026-10-21"]["anomalies"] == ["Uploaded to Panopto on Thursday, 29 October 2026; the date in its "
                                               "name is used"]
    assert not days["2026-10-14"]["live"] and days["2026-10-14"]["anomalies"] == []


def test_a_class_day_still_being_recorded_can_transcribe_its_finished_parts(tmp_path):
    from studia.views import course_view

    lib = _lib(tmp_path)
    lib.merge_recordings("c", _term(3) + [_rec("p1", D(2026, 10, 28, 8, 30)),
                                         _rec("p2", D(2026, 10, 28, 10, 30), live=True, dur=900),
                                         _rec("only", D(2026, 10, 29, 8, 31), live=True, dur=900)], first_sync=True)
    days = {d["date"]: d for d in course_view(lib.get_course("c"), lib.recordings("c"), Settings(), {}, {}, set())["days"]}
    assert days["2026-10-28"]["live"] and days["2026-10-28"]["ready"]       # its first part is finished
    assert days["2026-10-29"]["live"] and not days["2026-10-29"]["ready"]   # nothing finished yet
    assert days["2026-09-23"]["ready"] and not days["2026-09-23"]["live"]
