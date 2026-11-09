"""The 4.0 capture (slides.py) on a made-up screen stream (screen_fixture: H.264 in a fragmented MP4, served as HLS
with byte ranges): a slide change that moves only a few percent of the screen (PowerPoint's presenter view), two
pages that differ only in small text, a page written on until it changes, a written page followed by a blank one,
build-up steps on a dark slide, the last pass over the pictures, and a camera stream that is a tablet against one that
is a camera (capture-audit.md)."""

import datetime as dt
import logging
import os
import types

import numpy as np
import pytest

import screen_fixture as F
from studia import slides


def _th(g):
    return g.reshape(108, 2, 192, 2).mean(axis=(1, 3)).astype(np.int16)


def presenter(seed):
    """PowerPoint's presenter view: a dark screen, the slide on part of it, the next slide small beside it."""
    img = F.blank(25)
    img[20:150, 10:250] = 235
    img[20:80, 270:374] = 235
    sl = F.page(3, seed, x=20, y=30, per=34)
    img[26:128, 14:246] = sl[26:128, 14:246]
    return img


def capture(tmp_path, frames, **kw):
    st = F.Stream(frames)
    out = slides.extract_slides(st, st.url, tmp_path, "slides/x", "s", **kw)
    return out, st


def shows(s, pictures):
    return F.which(s.path, pictures)


def test_a_slide_change_on_part_of_the_screen_is_seen(tmp_path):
    a, b = presenter(0), presenter(10)
    assert 0.02 < slides.changed_fraction(_th(a), _th(b)) < 0.06       # presenter view: a few percent move
    out, st = capture(tmp_path, F.segments((a, 36), (b, 36)))
    assert [(s.index, s.t, s.end) for s in out] == [(1, 0.0, 36.0), (2, 36.0, 72.0)]
    assert [shows(s, {"a": a, "b": b}) for s in out] == ["a", "b"]
    assert st.served < len(st.data)                                      # keyframes only, not the whole video
    (tmp_path / "old").mkdir()
    old, _ = capture(tmp_path / "old", F.segments((a, 36), (b, 36)), change_threshold=0.06)
    assert [s.index for s in old] == [1]                                 # at 6 % slide a was never kept


def _small_text_pages():
    y = 16 + 14 * 5
    p = F.page(14, 0, per=60)
    p[y:y + 7, 20:320] = 235
    F.write(p, 20, y, 60, seed=7, step=2)
    q = p.copy()                                                         # one line of other words
    q[y:y + 7, 20:320] = 235
    F.write(q, 25, y, 59, seed=99, step=2)
    return p, q


def test_two_pages_that_differ_in_small_text_stay_two_and_a_page_shown_again_keeps_its_number(tmp_path):
    p, q = _small_text_pages()
    x = F.page(9, 50, per=40)
    assert slides.changed_fraction(_th(p), _th(q)) < 0.015              # the old "same picture" took them for one
    assert not slides.same_picture(p, q) and slides.same_picture(p, p.copy())
    out, _ = capture(tmp_path, F.segments((p, 27), (x, 27), (q, 27)))
    assert [s.index for s in out] == [1, 2, 3]
    assert shows(out[2], {"p": p, "q": q, "x": x}) == "q"
    (tmp_path / "again").mkdir()
    out, _ = capture(tmp_path / "again", F.segments((p, 27), (x, 27), (p, 27)))
    assert [s.index for s in out] == [1, 2, 1] and out[2].path == out[0].path


def test_a_page_written_on_until_it_changes_is_one_picture_as_complete_as_its_last_keyframe(tmp_path):
    pages = [F.page(1 + k // 2, 0) for k in range(27)]                   # a line more every two seconds
    nxt = F.page(5, 80, x=60)
    out, _ = capture(tmp_path, pages + [nxt] * 18)
    assert [(s.index, s.t) for s in out] == [(1, 0.0), (1, 18.0), (2, 27.0)]
    assert shows(out[0], {"0": pages[0], "9": pages[9], "18": pages[18], "26": pages[26]}) == "18"


def test_a_written_page_followed_by_a_blank_one_keeps_the_written_page(tmp_path):
    written = F.page(8, 3)
    out, _ = capture(tmp_path, F.segments((written, 27), (F.blank(), 27)))
    assert [s.index for s in out] == [1] and shows(out[0], {"w": written, "b": F.blank()}) == "w"


def test_build_up_steps_on_a_dark_slide_make_one_picture(tmp_path):
    step1 = F.page(3, 0, bg=20, ink=240)
    step2 = F.page(6, 0, bg=20, ink=240)
    assert slides.added_only(_th(step1), _th(step2))                     # light writing added to a dark slide
    assert not slides.added_only(_th(step2), _th(step1))                 # (taken away: not the same thing)
    d = _th(step2) - _th(step1)
    assert float(np.mean(d > 28)) > 0.003                               # the old rule: "something got lighter"
    nxt = F.page(4, 40, bg=20, ink=240, x=100)
    out, _ = capture(tmp_path, F.segments((step1, 18), (step2, 18), (nxt, 18)))
    assert [(s.index, s.t) for s in out] == [(1, 0.0), (1, 18.0), (2, 36.0)]
    assert shows(out[0], {"1": step1, "2": step2}) == "2"


def test_the_last_pass_drops_a_picture_a_later_one_shows_whole(tmp_path):
    a = F.page(4, 0)
    b = F.page(6, 30, x=80)
    a_more = F.page(8, 0)                                                # page a again, with more written on it
    seen = []
    tmp = tmp_path / "pass"
    tmp.mkdir()
    st = F.Stream(F.segments((a, 27), (b, 27), (a_more, 27)))
    out = slides.extract_slides(st, st.url, tmp, "slides/x", "s", seen=seen)
    assert [s.index for s in out] == [1, 2, 3]
    final = slides.final_pass(out, seen)
    assert [(s.index, s.t, s.end) for s in final] == [(1, 0.0, 27.0), (2, 27.0, 54.0), (1, 54.0, 81.0)]
    assert sorted(os.listdir(tmp)) == ["s-01.jpg", "s-02.jpg"]          # its file renamed to its new number
    assert shows(final[0], {"a": a, "more": a_more, "b": b}) == "more" and final[0].image == "slides/x/s-01.jpg"
    assert [k.slide.index for k in seen] == [1, 2]


def test_renumber_follows_first_appearance_and_renames_captured_pictures(tmp_path):
    from studia.slides import Slide, renumber

    for n in (3, 5):
        (tmp_path / f"s-0{n}.jpg").write_bytes(b"%d" % n)
    (tmp_path / "s-p07.jpg").write_bytes(b"ppt")                         # Panopto's own picture: keeps its name
    entries = [Slide(5, 0.0, 10.0, "slides/x/s-05.jpg", str(tmp_path / "s-05.jpg")),
               Slide(9, 10.0, 20.0, "slides/x/s-p07.jpg", str(tmp_path / "s-p07.jpg")),
               Slide(3, 20.0, 30.0, "slides/x/s-03.jpg", str(tmp_path / "s-03.jpg")),
               Slide(5, 30.0, 40.0, "slides/x/s-05.jpg", str(tmp_path / "s-05.jpg"))]
    out = renumber(entries)
    assert [(s.index, s.image) for s in out] == [(1, "slides/x/s-01.jpg"), (2, "slides/x/s-p07.jpg"),
                                                 (3, "slides/x/s-03.jpg"), (1, "slides/x/s-01.jpg")]
    assert (tmp_path / "s-01.jpg").read_bytes() == b"5" and (tmp_path / "s-03.jpg").read_bytes() == b"3"
    assert sorted(os.listdir(tmp_path)) == ["s-01.jpg", "s-03.jpg", "s-p07.jpg"]


def test_a_camera_streams_pictures_are_read_past_its_sound(tmp_path):
    """A camera stream carries the microphone in the same fragments, its samples first: the picture is the video
    track's first sample (MATH 240's tablet: "the camera stream's first frames couldn't be fetched")."""
    page = F.page(6, 0)
    st = F.Stream(F.segments((page, 27)), sound=True)
    init = st.data[:int(st.media.split('BYTERANGE="')[1].split("@")[0])]
    r = slides.media_playlist(st, st.url, prefer="video")
    _, segs = slides.parse_playlist(r.text, r.url)
    head = st.data[segs[0].offset:segs[0].offset + segs[0].length]
    track = slides.video_track(init)
    assert track is not None and slides.first_sample_range(head) != slides.first_sample_range(head, track)
    st_out = slides.extract_slides(st, st.url, tmp_path, "slides/x", "c")
    assert [s.index for s in st_out] == [1] and shows(st_out[0], {"page": page, "blank": F.blank()}) == "page"


def test_a_tablet_in_the_camera_stream_stands_still_and_a_camera_does_not():
    rng = np.random.default_rng(1)
    base = F.page(6, 0)
    camera = [np.clip(base.astype(np.int16) + rng.integers(-20, 21, base.shape), 0, 255).astype(np.uint8)
              for _ in range(9 * 12)]                                    # sensor noise: nothing stands still
    tablet = F.segments((F.page(3, 0), 40), (F.page(9, 0), 40), (F.page(5, 20), 28))
    cam = F.Stream(camera, sound=True)
    assert slides.still_share(cam, cam.url) < slides.STILL_SHARE
    tab = F.Stream(tablet, sound=True)                                   # (its sound comes first)
    assert slides.still_share(tab, tab.url) >= slides.STILL_SHARE


# ----------------------------------------------------------------------------- through Services.slides_task
def _services(tmp_path):
    from studia.domain import Course, Recording
    from studia.library import Library

    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="c", title="t", code="MATH 240", name="n")])
    lib.merge_recordings("c", [Recording(delivery_id="r1", course_id="c", folder_id="f", folder_path="", name="r1",
                                         start=dt.datetime(2026, 10, 21, 9, 0), duration_s=600, viewer_url="u")],
                         first_sync=True)
    return types.SimpleNamespace(lib=lib, output_root=tmp_path / "out", log=logging.getLogger("test"),
                                 settings=types.SimpleNamespace(capture_slides=True))


def test_a_recording_without_a_screen_stream_gets_its_tablet_but_not_its_camera(tmp_path, monkeypatch):
    from studia import store
    from studia.panopto import MediaSource, Panopto
    from studia.services import Services, capture_version

    info = {"Delivery": {"Streams": [{"Tag": "DV", "StreamUrl": "https://p.test/Sessions/x.screen.hls/master.m3u8",
                                      "RelativeStart": 0}]}}
    (src, off), = Panopto(None).screen_sources(info)
    assert src.tag == "DV" and Panopto(None).screen_sources(info, cameras=False) == []
    svc = _services(tmp_path)
    job = types.SimpleNamespace(id="r1", cancel=None, info=None)
    rng = np.random.default_rng(2)
    base = F.page(6, 0)
    cam = F.Stream([np.clip(base.astype(np.int16) + rng.integers(-20, 21, base.shape), 0, 255).astype(np.uint8)
                    for _ in range(9 * 6)])
    assert Services.slides_task(svc, job, [(src, off)], cam) == []
    assert store.slides_path("r1").with_suffix(".key").read_text(encoding="utf-8").startswith("camera\n")
    asked = cam.served
    assert Services.slides_task(svc, job, [(src, off)], cam) == [] and cam.served == asked   # looked at once
    tablet = F.Stream(F.segments((F.page(3, 0), 40), (F.page(9, 0), 40), (F.page(5, 20), 28)), sound=True)
    store.slides_path("r1").with_suffix(".key").unlink()
    out = Services.slides_task(svc, job, [(src, off)], tablet)
    assert len({s.index for s in out}) >= 2 and all(os.path.isfile(s.path) for s in out)
    key = store.slides_path("r1").with_suffix(".key").read_text(encoding="utf-8")
    assert key.startswith("v2\n") and key.endswith(" DV") and capture_version("r1") == 2


def test_an_earlier_versions_capture_of_the_same_streams_is_kept(tmp_path, monkeypatch):
    from studia import store
    from studia.panopto import MediaSource
    from studia.services import Services, _slides_key, _unversioned, capture_version
    from studia.slides import Slide

    calls = []

    def extract(session, url, folder, rel, prefix, offset, progress=None, cancel=None, seen=None, spans=()):
        calls.append(url)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{prefix}-01.jpg"
        path.write_bytes(b"jpg")
        return [Slide(1, offset, offset + 5, f"{rel}/{path.name}", str(path))]

    monkeypatch.setattr(slides, "extract_slides", extract)
    svc = _services(tmp_path)
    job = types.SimpleNamespace(id="r1", cancel=None, info=None)
    screens = [(MediaSource("hls", "https://p/Sessions/s1.screen.hls/master.m3u8"), 0.1)]
    Services.slides_task(svc, job, screens, None)
    key = _slides_key(screens)
    assert key.startswith("v2\n") and capture_version("r1") == 2
    store.slides_path("r1").with_suffix(".key").write_text(_unversioned(key), encoding="utf-8")   # 3.x wrote this
    assert capture_version("r1") == 1
    Services.slides_task(svc, job, screens, None)
    assert len(calls) == 1                                               # kept: capturing again is asked for
    Services.slides_task(svc, job, screens, None, again=True)
    assert len(calls) == 2 and capture_version("r1") == 2
