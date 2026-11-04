"""Panopto recordings that are still being recorded, recordings uploaded days later (their name has the date), and the
simple checks that tell when something about a recording looks off."""

import datetime as dt

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
