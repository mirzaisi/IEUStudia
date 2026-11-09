"""The screen timeline (screens.py, <delivery>.screens.json): PowerPoint's slide changes merged with the captured
screens (exact times, titles and text, Panopto's own picture for a slide no capture shows), the words read on the
other screens, the file itself, and the background job for lectures transcribed before."""

import dataclasses as dc
import datetime as dt
import json
import logging
import os
import threading
import types
from pathlib import Path

import pytest

from studia import screens, store
from studia.screens import PptSpan, insert, match, merge, powerpoint
from studia.slides import Slide


def _event(t, slide, cap="", data="", count=1, kind="PowerPoint"):
    return {"Time": t, "EventTargetType": kind, "ObjectSequenceNumber": (count << 16) | slide, "Caption": cap,
            "Data": data, "ObjectIdentifier": "77"}


def _info(*events, duration=600.0):
    return {"Delivery": {"Duration": duration, "Timestamps": list(events), "Streams": []}}


def test_powerpoint_events_make_one_span_per_stretch_of_a_slide():
    info = _info(_event(4.4, 1, "Title slide", " Circuits  2 "), _event(30.0, 2, "Example 13", "Find vo"),
                 _event(40.0, 2, "", "Find vo using superposition", count=2),        # a build-up step
                 _event(70.0, 3, "", ""), _event(95.5, 2, "Example 13", "Find vo", count=3),     # slide 2 again
                 _event(50.0, 9, kind="ObjectVideo"), {"Time": "x", "EventTargetType": "PowerPoint"})
    spans = powerpoint(info)
    assert [(s.slide, s.t0, s.t1) for s in spans] == [(1, 4.4, 30.0), (2, 30.0, 70.0), (3, 70.0, 95.5),
                                                       (2, 95.5, 600.0)]
    assert spans[0].title == "Title slide" and spans[0].text == "Circuits 2"
    assert spans[1].title == "Example 13" and spans[1].text == "Find vo using superposition" and spans[1].seq >> 16 == 2
    assert powerpoint(None) == [] and powerpoint({"Delivery": {}}) == [] and powerpoint(_info()) == []


def _e(i, t, end, name=None):
    return Slide(i, t, end, f"slides/d/{name or f's-{i:02d}.jpg'}", f"/x/{name or f's-{i:02d}.jpg'}")


def test_captured_screens_take_powerpoints_exact_times_and_words():
    spans = [PptSpan(1, 2.0, 60.0, "Intro", ""), PptSpan(2, 60.0, 200.0, "KVL", "Sum of voltages"),
             PptSpan(3, 200.0, 420.0, "Example", "Find i1")]
    # the capture saw each change at the next keyframe (0-9 s late); screen 4 began in the middle of slide 3
    # (writing on it): its own time, no PowerPoint words
    entries = [_e(1, 0.0, 63.0), _e(2, 63.0, 207.0), _e(3, 207.0, 300.0), _e(4, 300.0, 420.0)]
    out, shows, missing = match(entries, spans)
    assert [(e.index, e.t, e.end) for e in out] == [(1, 2.0, 60.0), (2, 60.0, 200.0), (3, 200.0, 300.0),
                                                    (4, 300.0, 420.0)]
    assert {i: s.slide for i, s in shows.items()} == {1: 1, 2: 2, 3: 3} and missing == []
    assert [(e.index, e.t) for e in entries] == [(1, 0.0), (2, 63.0), (3, 207.0), (4, 300.0)]   # (copies)


def test_a_slide_the_capture_missed_gets_panoptos_picture_at_its_own_time(tmp_path):
    # presenter view: slides 14 and 15 came at 100 and 160, the capture saw nothing until slide 16 at 400; its
    # screen 2 (from 100) has the picture of its last keyframe: slide 15
    spans = [PptSpan(13, 0.0, 100.0, "13", ""), PptSpan(14, 100.0, 160.0, "Quadratic method", "P = 4 i1^2", "9", 14),
             PptSpan(15, 160.0, 400.0, "Example 2", "", "9", 15), PptSpan(16, 400.0, 500.0, "16", "")]
    entries = [_e(1, 0.0, 104.0), _e(2, 104.0, 403.0), _e(3, 403.0, 500.0)]
    out, shows, missing = match(entries, spans)
    assert [(e.index, e.t, e.end) for e in out] == [(1, 0.0, 104.0), (2, 160.0, 400.0), (3, 400.0, 500.0)]
    assert {i: s.slide for i, s in shows.items()} == {1: 13, 2: 15, 3: 16}
    assert [s.slide for s in missing] == [14]
    asked = []
    out, shows = merge(entries, spans, lambda s: asked.append(s.slide) or (f"slides/d/s-p{s.slide}.jpg", f"/x/p{s.slide}"))
    assert asked == [14]
    assert [(e.index, e.t, e.end, e.image) for e in out] == [
        (1, 0.0, 100.0, "slides/d/s-01.jpg"), (4, 100.0, 160.0, "slides/d/s-p14.jpg"),
        (2, 160.0, 400.0, "slides/d/s-02.jpg"), (3, 400.0, 500.0, "slides/d/s-03.jpg")]
    assert shows[4].title == "Quadratic method"


def test_a_missed_slide_inside_a_screen_splits_it_and_a_slide_shown_again_uses_its_picture():
    spans = [PptSpan(5, 0.0, 100.0, "Five", ""), PptSpan(6, 100.0, 140.0, "Six", ""), PptSpan(5, 140.0, 300.0, "Five", "")]
    entries = [_e(1, 3.0, 300.0)]                       # slide 6 came and went between two keyframes of one screen
    out, shows, missing = match(entries, spans)
    assert [s.slide for s in missing] == [5, 6]          # (its picture shows the second stretch of slide 5)
    out, shows = merge(entries, spans, lambda s: (f"slides/d/p{s.slide}.jpg", f"/x/p{s.slide}"))
    assert [(e.index, e.t, e.end) for e in out] == [(1, 0.0, 100.0), (2, 100.0, 140.0), (1, 140.0, 300.0)]
    # slide 5 shown again later where the capture missed it: its captured picture, nothing fetched
    spans = [PptSpan(5, 0.0, 60.0, "Five", ""), PptSpan(7, 60.0, 120.0, "Seven", ""), PptSpan(5, 120.0, 180.0, "Five", ""),
             PptSpan(8, 180.0, 240.0, "Eight", "")]
    entries = [_e(1, 4.0, 66.0), _e(2, 66.0, 126.0), _e(3, 126.0, 240.0)]     # screen 3's picture is slide 8
    fetched = []
    out, _ = merge(entries, spans, lambda s: fetched.append(s.slide))
    assert fetched == [] and [(e.index, e.t) for e in out] == [(1, 0.0), (2, 60.0), (1, 120.0), (3, 180.0)]


def test_insert_keeps_a_picture_that_ran_past_the_slide():
    entries = [_e(1, 0.0, 500.0)]
    out = insert(entries, PptSpan(3, 100.0, 160.0, "", ""), 2, "p", "/p")
    assert [(e.index, e.t, e.end) for e in out] == [(1, 0.0, 100.0), (2, 100.0, 160.0), (1, 160.0, 500.0)]
    assert insert(entries, PptSpan(3, 100.0, 102.0, "", ""), 2, "p", "/p") == entries       # too short


# ----------------------------------------------------------------------------- the file
def test_the_screens_file_round_trips_and_is_read_again_when_it_changes():
    assert store.load_screens("r1") is None
    doc = {"v": 1, "made": "2026-11-12T10:20:00", "times": "exact",
           "screens": {"12": {"title": "Δ - Y transformation", "text": "Apply superposition", "from": "powerpoint",
                              "slide": 15, "lang": "en"}}, "powerpoint": True}
    store.save_screens("r1", doc)
    assert store.load_screens("r1") == doc
    assert json.loads(store.screens_path("r1").read_text(encoding="utf-8"))["screens"]["12"]["title"] == "Δ - Y transformation"
    doc2 = dict(doc, times="keyframe")
    store.save_screens("r1", doc2)
    assert store.load_screens("r1")["times"] == "keyframe"
    store.screens_path("r1").write_text("{not json", encoding="utf-8")
    assert store.load_screens("r1") is None
    assert not list(store.screens_path("r1").parent.glob("*.tmp"))


def _svc(tmp_path, capture=True):
    from studia.domain import Course, Recording
    from studia.library import Library

    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="c", title="t", code="EEE 215", name="n")])
    lib.merge_recordings("c", [Recording(delivery_id="r1", course_id="c", folder_id="f", folder_path="", name="r1",
                                         start=dt.datetime(2026, 10, 13, 11, 0), duration_s=500, viewer_url="u")],
                         first_sync=True)
    return types.SimpleNamespace(lib=lib, output_root=tmp_path / "out", log=logging.getLogger("test"),
                                 settings=types.SimpleNamespace(capture_slides=capture))


def _pictures(tmp_path, n):
    folder = tmp_path / "out" / "EEE 215" / "slides" / "day"
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for i in range(1, n + 1):
        p = folder / f"1100-r1-{i:02d}.jpg"
        p.write_bytes(b"jpg%d" % i)
        out.append(p)
    return out


class Read:
    def __init__(self, texts):
        self.texts, self.asked = texts, []

    def __call__(self, order, wanted, cancel=None):
        self.asked.append((list(order), set(wanted)))
        return {p: types.SimpleNamespace(title=self.texts[Path(p).name][0], text=self.texts[Path(p).name][1],
                                         engine="windows en-US")
                for p in wanted if Path(p).name in self.texts}


def test_a_new_lectures_timeline_numbers_panoptos_picture_among_the_captured_ones(tmp_path, monkeypatch):
    from studia.panopto import Panopto

    svc = _svc(tmp_path)
    pics = _pictures(tmp_path, 3)
    store.save_slides("r1", [Slide(1, 0.0, 104.0, "slides/day/1100-r1-01.jpg", str(pics[0])),
                             Slide(2, 104.0, 403.0, "slides/day/1100-r1-02.jpg", str(pics[1])),
                             Slide(3, 403.0, 500.0, "slides/day/1100-r1-03.jpg", str(pics[2]))])
    info = _info(_event(0.0, 13, "Thirteen", "a"), _event(100.0, 14, "Fourteen", "b"), _event(160.0, 15, "Fifteen", "c"),
                 _event(400.0, 16, "", ""), duration=500.0)
    fetched = []
    monkeypatch.setattr(Panopto, "slide_image", lambda self, obj, n: fetched.append(n & 0xFFFF) or b"\xff\xd8\xff" + b"0" * 2000)
    read = Read({"1100-r1-03.jpg": ("Not PowerPoint", "read")})
    out = screens.build_timeline(svc, "r1", info, session=object(), fresh=True, read=read)
    assert fetched == [14]
    assert [(e.index, e.t, Path(e.path).name) for e in out] == [(1, 0.0, "1100-r1-01.jpg"), (2, 100.0, "1100-r1-p14.jpg"),
                                                                (3, 160.0, "1100-r1-03.jpg"), (4, 400.0, "1100-r1-04.jpg")]
    assert Path(out[2].path).read_bytes() == b"jpg2"                  # the captured picture 2, renamed to its number
    doc = store.load_screens("r1")
    assert doc["times"] == "exact" and doc["powerpoint"] is True
    assert {k: (v["from"], v.get("slide"), v["title"]) for k, v in doc["screens"].items()} == {
        "1": ("powerpoint", 13, "Thirteen"), "2": ("powerpoint", 14, "Fourteen"), "3": ("powerpoint", 15, "Fifteen"),
        "4": ("powerpoint", 16, "")}
    assert read.asked == []                                           # every screen had PowerPoint's words
    assert [e.index for e in store.load_slides("r1")] == [1, 2, 3, 4]


def test_an_old_lectures_timeline_keeps_its_numbers_and_reads_the_other_screens(tmp_path, monkeypatch):
    from studia.panopto import Panopto

    svc = _svc(tmp_path)
    pics = _pictures(tmp_path, 2)
    store.save_slides("r1", [Slide(1, 0.0, 104.0, "slides/day/1100-r1-01.jpg", str(pics[0])),
                             Slide(2, 104.0, 500.0, "slides/day/1100-r1-02.jpg", str(pics[1]))])
    monkeypatch.setattr(Panopto, "slide_image", lambda self, obj, n: b"\xff\xd8\xff" + b"1" * 2000)
    # Panopto couldn't be asked: the pictures are read, PowerPoint asked again later
    read = Read({"1100-r1-01.jpg": ("Kirchhoff", "Sum of currents"), "1100-r1-02.jpg": ("", "")})
    screens.build_timeline(svc, "r1", None, read=read)
    doc = store.load_screens("r1")
    assert doc["powerpoint"] is None and doc["times"] == "keyframe"
    s1 = doc["screens"]["1"]
    assert s1 == {"title": "Kirchhoff", "text": "Sum of currents", "from": "ocr", "ocr": "windows en-US",
                  "pic": f"1100-r1-01.jpg|4|{pics[0].stat().st_mtime_ns}"}          # (the picture as it was read)
    assert read.asked[0][0] == [str(pics[0]), str(pics[1])]          # every picture, in time order (the slide area)
    # later, with PowerPoint's events: slide 8 at 300 s was missed; the new picture is numbered after the others
    info = _info(_event(0.0, 7, "Seven", ""), _event(100.0, 9, "Nine", ""), _event(300.0, 8, "Eight", "e"), duration=500.0)
    out = screens.build_timeline(svc, "r1", info, session=object(), read=read)
    assert [(e.index, e.t) for e in out] == [(1, 0.0), (3, 100.0), (2, 300.0)]      # slide 9: a picture of its own
    doc = store.load_screens("r1")
    assert doc["powerpoint"] is True and doc["times"] == "exact"
    assert [doc["screens"][k]["title"] for k in "123"] == ["Seven", "Eight", "Nine"]
    # made again (the words read again, say): PowerPoint's matching stays as it was, nothing fetched
    monkeypatch.setattr(Panopto, "slide_image", lambda self, obj, n: pytest.fail("fetched again"))
    out2 = screens.build_timeline(svc, "r1", info, session=object(), read=read)
    assert [dc.astuple(e) for e in out2] == [dc.astuple(e) for e in out]


def test_a_start_while_panopto_cant_be_asked_keeps_the_words_read_before(tmp_path):
    """A lecture whose timeline was made while Panopto couldn't be asked is made again at every start until Panopto
    answers, and every one of its pictures was read again each time. The words read before are kept now, while the
    pictures are the ones they were read from."""
    svc = _svc(tmp_path)
    pics = _pictures(tmp_path, 2)
    store.save_slides("r1", [Slide(1, 0.0, 104.0, "slides/day/1100-r1-01.jpg", str(pics[0])),
                             Slide(2, 104.0, 500.0, "slides/day/1100-r1-02.jpg", str(pics[1]))])
    read = Read({"1100-r1-01.jpg": ("Kirchhoff", "Sum of currents"), "1100-r1-02.jpg": ("", "Nodes")})
    screens.build_timeline(svc, "r1", None, read=read)
    words = store.load_screens("r1")["screens"]
    screens.build_timeline(svc, "r1", None, read=read)                # the next start, still offline
    assert len(read.asked) == 1 and store.load_screens("r1")["screens"] == words
    pics[1].write_bytes(b"a picture captured again, under the same name")
    screens.build_timeline(svc, "r1", None, read=read)                # a picture changed: all are read again
    assert len(read.asked) == 2 and read.asked[1][1] == {str(pics[0]), str(pics[1])}
    screens.build_timeline(svc, "r1", None, fresh=True, read=read)    # captured just now: read
    assert len(read.asked) == 3


def test_no_powerpoint_is_remembered_and_capture_slides_off_fetches_nothing(tmp_path, monkeypatch):
    from studia.panopto import Panopto

    svc = _svc(tmp_path, capture=False)
    pics = _pictures(tmp_path, 1)
    store.save_slides("r1", [Slide(1, 0.0, 500.0, "slides/day/1100-r1-01.jpg", str(pics[0]))])
    screens.build_timeline(svc, "r1", _info(), read=None)
    doc = store.load_screens("r1")
    assert doc["powerpoint"] is False and doc["screens"] == {"1": {"title": "", "text": "", "from": "none"}}
    monkeypatch.setattr(Panopto, "slide_image", lambda self, obj, n: pytest.fail("Capture slides is off"))
    info = _info(_event(0.0, 1, "One", ""), _event(100.0, 2, "Two", ""), _event(400.0, 3, "", ""), duration=500.0)
    out = screens.build_timeline(svc, "r1", info, session=object(), read=None)
    assert [e.index for e in out] == [1]
    # a lecture without pictures: an empty timeline (asked once)
    store.slides_path("r1").unlink()
    assert screens.build_timeline(svc, "r1", _info(), read=None) == []
    assert store.load_screens("r1")["screens"] == {}


# ----------------------------------------------------------------------------- the background job
class FakeSync:
    def __init__(self, info=None, fail=False):
        self.info, self.fail, self.asked = info, fail, 0

    def worker_session(self):
        return types.SimpleNamespace()

    def ensure_panopto(self, course_id=None):
        pass

    def absorb(self, s):
        pass


def _worker(tmp_path, monkeypatch, info, fail=False):
    from studia.panopto import Panopto

    svc = _svc(tmp_path)
    svc.lib.set_status("r1", "done") if hasattr(svc.lib, "set_status") else None
    sync = FakeSync(info, fail)

    def delivery_info(self, did):
        sync.asked += 1
        if sync.fail:
            raise ConnectionError("offline")
        return sync.info
    monkeypatch.setattr(Panopto, "delivery_info", delivery_info)
    calls, written = [], []

    def slides_task(job, scr, session, again=False):
        calls.append(([s.tag for s, _ in scr], again))
        return [] if "cam" in scr[0][0].url else ["a picture"]
    svc.sync, svc.slides_task, svc.emit = sync, slides_task, lambda e: None
    svc.write_outputs = lambda did, pack=True: written.append(did)
    svc.jobs = types.SimpleNamespace(busy=lambda: False)
    w = screens.ScreenWorker(svc)
    w._thread = threading.current_thread()                     # (no thread of its own: the test drives it)
    return w, svc, calls, written


def test_the_background_job_captures_again_only_old_short_captures_and_tablets(tmp_path, monkeypatch):
    stream = lambda tag, url: {"Tag": tag, "StreamUrl": f"https://p/{url}.m3u8", "RelativeStart": 0}  # noqa: E731
    # an old capture (no key file) of a recording with two screen streams: captured again by itself
    info = {"Delivery": {"Duration": 500, "Timestamps": [], "Streams": [stream("SCREEN", "a"), stream("SCREEN", "b")]}}
    w, svc, calls, _ = _worker(tmp_path, monkeypatch, info)
    pics = _pictures(tmp_path, 1)
    store.save_slides("r1", [Slide(1, 0.0, 500.0, "slides/day/1100-r1-01.jpg", str(pics[0]))])
    w.work("r1")
    assert calls == [(["SCREEN", "SCREEN"], True)]
    # with a key file (made by 3.x or 4.0): only the timeline
    store.slides_path("r1").with_suffix(".key").write_text("x", encoding="utf-8")
    w.work("r1")
    assert len(calls) == 1 and store.load_screens("r1")["powerpoint"] is False
    # no pictures, no screen stream, a camera stream: looked at (slides_task tells a tablet from a camera)
    store.slides_path("r1").unlink()
    store.slides_path("r1").with_suffix(".key").unlink()
    w.svc.sync.info = {"Delivery": {"Duration": 500, "Timestamps": [], "Streams": [stream("DV", "cam")]}}
    w.work("r1")
    assert calls[-1] == (["DV"], False)
    # asked by the student: captured again whatever it is; a camera that shows no screen is said
    w.svc.sync.info = {"Delivery": {"Duration": 500, "Timestamps": [], "Streams": [stream("SCREEN", "a")]}}
    w.work("r1", again=True)
    assert calls[-1] == (["SCREEN"], True)
    w.svc.sync.info = {"Delivery": {"Duration": 500, "Timestamps": [], "Streams": [stream("DV", "cam")]}}
    with pytest.raises(RuntimeError, match="no screen recording"):
        w.work("r1", again=True)


def test_the_background_job_waits_for_panopto_and_for_transcription(tmp_path, monkeypatch):
    w, svc, calls, _ = _worker(tmp_path, monkeypatch, None, fail=True)
    pics = _pictures(tmp_path, 1)
    store.save_slides("r1", [Slide(1, 0.0, 500.0, "slides/day/1100-r1-01.jpg", str(pics[0]))])
    w.work("r1")                                                      # offline: the pictures' words only
    assert store.load_screens("r1")["powerpoint"] is None and svc.sync.asked == 1
    w.work("r1")
    assert svc.sync.asked == 1                                        # not asked again for a while
    with pytest.raises(RuntimeError):
        w.work("r1", again=True)                                      # asked by the student: said
    svc.lib.transcribed_ids = lambda: {"r1"}
    assert w._find_backlog() == ["r1"]                                # PowerPoint still to be asked
    svc.jobs = types.SimpleNamespace(busy=lambda: True)
    assert w._next() is None                                          # a lecture is being transcribed: waits
    assert w.capture_again(["r1", "r1"]) == ["r1"] and w.state()["waiting"] == ["r1"]
    w.shutdown()


class _Answer:
    """DeliveryInfo.aspx's answer, read by the real Panopto.delivery_info."""

    def __init__(self, body):
        self.status_code, self.body, self.url, self.headers = 200, body, "https://panopto.ieu.edu.tr/x", {}

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


def test_a_lecture_panopto_refuses_does_not_hold_up_the_others(tmp_path, monkeypatch):
    """A transcribed lecture deleted or replaced in Panopto since: DeliveryInfo answers with Panopto's own words about
    it. That was taken for Panopto being offline, so the lectures after it were made without PowerPoint (no exact
    times, titles or words) and came back at every start, the same one first again."""
    from studia.domain import Recording, Segment, Transcript
    from studia.panopto import Panopto

    svc = _svc(tmp_path)
    ids = ["new", "removed", "old"]                                   # newest first, as the background job goes
    recs = [Recording(delivery_id=d, course_id="c", folder_id="f", folder_path="", name=d,
                      start=dt.datetime(2026, 10, 27, 11, 0) - dt.timedelta(days=7 * i), duration_s=500, viewer_url="u")
            for i, d in enumerate(ids)]
    svc.lib.merge_recordings("c", recs, first_sync=False)
    folder = tmp_path / "out" / "EEE 215" / "slides" / "day"
    folder.mkdir(parents=True)
    for did in ids:                                                   # transcribed by 3.x: its pictures, no timeline
        tr = Transcript(delivery_id=did, segments=[Segment(0, 5, "words", "en", 0.9)], duration_s=500, speech_s=400,
                        language_seconds={"en": 400}, model="m", device="cpu", engine_version="3.3.1",
                        created_at="2026-10-28T10:00:00")
        svc.lib.save_transcript(tr, store.save_transcript(tr))
        pic = folder / f"1100-{did}-01.jpg"
        pic.write_bytes(b"jpg")
        store.save_slides(did, [Slide(1, 0.0, 500.0, f"slides/day/{pic.name}", str(pic))])
    svc.lib.merge_recordings("c", [r for r in recs if r.delivery_id != "removed"], first_sync=False)
    assert svc.lib.db.execute("SELECT gone FROM recordings WHERE delivery_id='removed'").fetchone()[0] == 1
    assert "removed" in {r.delivery_id for r in svc.lib.recordings()}          # kept: it is transcribed
    asked, signed_out = [], []

    def post(url, data=None, headers=None):
        did = data["deliveryId"]
        asked.append(did)
        if signed_out:
            return _Answer({"ErrorCode": 2, "LoginRedirect": True})
        if did == "removed":                                          # Panopto's words for a session that is no more
            return _Answer({"ErrorCode": 5, "ErrorMessage": "This session link is invalid.<br>",
                            "LoginRedirect": False})
        events = [_event(0.0, 1, "One", "a"), _event(200.0, 2, "Two", "b")] if did == "old" else []
        return _Answer({"Delivery": {"Duration": 500.0, "Streams": [], "PodcastStreams": [], "Timestamps": events}})

    def ensure_panopto(course_id=None):                               # IEULearn didn't open the course
        raise RuntimeError("Panopto didn't open EEE 215 through IEULearn, so the lecture couldn't be downloaded.")

    session = types.SimpleNamespace(post=post, cookies=types.SimpleNamespace(get=lambda *a, **k: ""))
    svc.sync = types.SimpleNamespace(worker_session=lambda: session, ensure_panopto=ensure_panopto,
                                     absorb=lambda s: None)
    svc.emit, svc.write_outputs = (lambda e: None), (lambda did, pack=True: None)
    svc.jobs = types.SimpleNamespace(busy=lambda: False)
    monkeypatch.setattr(Panopto, "slide_image", lambda self, obj, n: b"\xff\xd8\xff" + b"0" * 2000)
    w = screens.ScreenWorker(svc)
    w._thread = threading.current_thread()
    while (item := w._next()) is not None:                            # one start's backlog
        w.work(item[0], again=item[1])
    assert asked == ["new", "removed", "old"]                         # the one after it is still asked
    assert {did: store.load_screens(did)["powerpoint"] for did in ids} == {"new": False, "removed": False, "old": True}
    assert store.load_screens("old")["times"] == "exact"
    assert screens.ScreenWorker(svc)._find_backlog() == []            # nothing made again at the next start
    with pytest.raises(RuntimeError, match="Panopto says: “This session link is invalid.” The recording may have"):
        w.work("removed", again=True)                                 # the student asked: Panopto's words, not
    assert w._offline_until == 0.0                                    # "check your connection"; nothing waits
    signed_out.append(1)                                              # other failures still wait a while
    assert w._info(svc.lib.get_recording("old")) == (None, None) and w._offline_until > 0
    assert asked[-1] == "old" and w._info(svc.lib.get_recording("new")) == (None, None) and asked[-1] == "old"
    w.shutdown()


def test_the_capture_again_button_queues_a_day_or_a_course(tmp_path, monkeypatch):
    from studia.services import Services

    svc = _svc(tmp_path)
    svc.lib.transcribed_ids = lambda: {"r1"}
    got = []
    svc.screens = types.SimpleNamespace(capture_again=lambda ids: got.append(ids) or list(ids))
    assert Services.capture_screens_again(svc, day_key=svc.lib.get_recording("r1").day_key) == 1
    assert Services.capture_screens_again(svc, course_id="c") == 1 and got == [["r1"], ["r1"]]


def _days(svc):
    """Three more class days of EEE 215, transcribed: one in two parts (a1, a2), one of b, one of c1."""
    from studia.domain import Recording

    at = {"a1": dt.datetime(2026, 10, 20, 9, 0), "a2": dt.datetime(2026, 10, 20, 10, 0),
          "b": dt.datetime(2026, 10, 6, 9, 0), "c1": dt.datetime(2026, 9, 29, 9, 0)}
    svc.lib.merge_recordings("c", [svc.lib.get_recording("r1")] + [
        Recording(delivery_id=d, course_id="c", folder_id="f", folder_path="", name=d, start=s, duration_s=500,
                  viewer_url="u") for d, s in at.items()], first_sync=False)
    svc.lib.transcribed_ids = lambda: {"r1", *at}


def test_capturing_a_course_again_offline_says_so_once_and_counts_class_days(tmp_path, monkeypatch):
    """Offline, "All 4 lectures of EEE 215" said "Capturing the screens of 5 lectures" (it counted the recordings'
    parts) and then the same warning came every few seconds, once per part. Now the class days are counted, and when
    Panopto can't be reached the rest of what was asked stops and the page is told once."""
    from studia.services import Services

    w, svc, calls, _ = _worker(tmp_path, monkeypatch, None, fail=True)
    _days(svc)
    told = []
    svc.emit, svc.screens = told.append, w
    assert Services.capture_screens_again(svc, course_id="c") == 4                 # 4 class days, 5 parts
    assert Services.capture_screens_again(svc, course_id="c") == 0                 # (already waiting)
    while w._asked:
        w._do(*w._next())
    failed = [e for e in told if e.get("failed")]
    assert len(failed) == 1 and svc.sync.asked == 1, told                          # asked Panopto once, said once
    assert failed[0]["error"] == ("Panopto couldn't be reached, so 4 lectures weren't done. Check your connection "
                                  "and try again.")
    assert w.state() == {"current": None, "waiting": []} and not calls
    # one lecture in two parts: said once, without a count
    told.clear()
    assert Services.capture_screens_again(svc, day_key=svc.lib.get_recording("a1").day_key) == 1
    while w._asked:
        w._do(*w._next())
    assert [e["error"] for e in told if e.get("failed")] == [
        "Panopto couldn't be reached. Check your connection and try again."] and svc.sync.asked == 2
    w.shutdown()


def test_the_page_hears_how_far_the_earlier_lectures_screens_are_read(tmp_path, monkeypatch):
    """After the update, the earlier lectures' screens were read in the background for a quarter of an hour and
    nothing said so. Settings → Storage now has a quiet line counting the lectures read (screens_reading events):
    those with pictures and no timeline yet; one only asked again about PowerPoint reads nothing and isn't counted."""
    w, svc, calls, _ = _worker(tmp_path, monkeypatch, None, fail=True)
    _days(svc)
    told = []
    svc.emit = told.append
    pics = _pictures(tmp_path, 2)
    for did, pic in (("a1", pics[0]), ("b", pics[1])):                # pictures, no timeline yet: read
        store.save_slides(did, [Slide(1, 0.0, 500.0, f"slides/day/{pic.name}", str(pic))])
    store.save_slides("r1", [Slide(1, 0.0, 500.0, f"slides/day/{pics[0].name}", str(pics[0]))])
    store.save_screens("r1", {"v": 1, "times": "keyframe", "powerpoint": None,          # Panopto still to be asked
                              "screens": {"1": {"title": "", "text": "x", "from": "ocr"}}})
    while (item := w._next()) is not None:
        w._do(*item)
    assert [(e["done"], e["total"]) for e in told if e["type"] == "screens_reading"] == [(0, 2), (1, 2), (2, 2)]
    assert w.reading() == {"done": 2, "total": 2}
    w.shutdown()
