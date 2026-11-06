"""Slides captured from Panopto's screen recording: a slide shown for only one keyframe is kept when it stays on screen,
and a slide whose next step only adds to it is kept once, complete. The frames in data/slide_thumbs.npz are the app's
own thumbnails of real lectures (an EEE 215 slide before and after its worked solution appears, two different
slides, and a MATH 240 page scrolled while being written on)."""

import os
import sys
import types
from pathlib import Path

import numpy as np
import pytest

from studia import slides
from studia.hls import Segment

FRAMES = np.load(Path(__file__).parent / "data" / "slide_thumbs.npz")
MADE = {}                                                  # frames made in a test, by name


def _f(name):
    return (MADE[name] if name in MADE else FRAMES[name]).astype(np.int16)


def _dark_screens():
    player = np.full((108, 192), 12, dtype=np.uint8)        # a black video player with a grey control bar
    player[96:104, 8:184] = 70
    header = np.full((108, 192), 20, dtype=np.uint8)        # a dark section slide, its white title on the light
    light = FRAMES["build_a"] > 200                         # slide's background (nothing gets brighter there)
    header[40:60, 30:160] = np.where(light[40:60, 30:160], 255, 20)
    return {"dark_player": player, "dark_header": header}


def test_a_slide_that_only_gains_writing_counts_as_added():
    assert slides.added_only(_f("build_a"), _f("build_b"))                # the solution written under the circuit
    assert slides.changed_fraction(_f("build_a"), _f("build_b")) > 0.06    # (a change the sampler does notice)
    assert not slides.added_only(_f("build_b"), _f("build_a"))             # rubbed out: not the same thing
    assert not slides.added_only(_f("other_a"), _f("other_b"))             # the next slide
    assert not slides.added_only(_f("ink_a"), _f("ink_b"))                 # scrolled while writing: both are kept


def _run(monkeypatch, tmp_path, shown, held=None, real=False, min_slide_s=12.0):
    """extract_slides over 9-second segments whose keyframes are ``shown`` (ints, or names of real frames);
    ``held``: what each segment shows 5 s in (segment index -> frame)."""
    monkeypatch.setattr(slides, "media_playlist", lambda s, u, prefer: types.SimpleNamespace(text=u, url=u))
    monkeypatch.setattr(slides, "parse_playlist", lambda text, url: (("init", 0, 0), [
        Segment(i * 9.0, 9.0, str(i), 0, 10) for i in range(len(shown))]))
    monkeypatch.setattr(slides, "find_avcc", lambda b: b"avcc")
    def fetch(session, seg, avcc):
        if shown[int(seg.url)] is None:                           # None: the request fails
            raise ConnectionError("no connection")
        return shown[int(seg.url)]
    monkeypatch.setattr(slides, "_fetch_keyframe", fetch)
    asked = []

    def after(session, seg, init, s):
        asked.append(int(seg.url))
        return (held or {}).get(int(seg.url))
    monkeypatch.setattr(slides, "_frame_after", after)
    if real:
        monkeypatch.setattr(slides, "thumb", lambda f: _f(f))
    else:
        monkeypatch.setattr(slides, "thumb", lambda f: np.full((4, 4), f, dtype=np.float32))
        monkeypatch.setattr(slides, "changed_fraction", lambda a, b: float(abs(a - b).mean() > 0))
        monkeypatch.setattr(slides, "is_blank", lambda t: False)
        monkeypatch.setattr(slides, "added_only", lambda a, b: False)
    monkeypatch.setattr(slides, "save_jpeg", lambda frame, path, max_width=1600: path.write_bytes(str(frame).encode()))
    session = types.SimpleNamespace(get=lambda url: types.SimpleNamespace(content=b""))
    out = slides.extract_slides(session, "m3u8", tmp_path, "slides/x", "s", min_slide_s=min_slide_s)
    return out, asked


def test_a_slide_seen_in_one_keyframe_is_kept_when_it_stays_on_screen(tmp_path, monkeypatch):
    # slide 2 is in one keyframe only (27 s); 5 s later it is still there: a real slide, not a moment of scrolling
    out, asked = _run(monkeypatch, tmp_path, [1, 1, 1, 2, 3, 3, 3], held={3: 2})
    assert [(s.index, s.t) for s in out] == [(1, 0.0), (2, 27.0), (3, 36.0)]
    assert asked == [3]                                       # only that segment was fetched whole
    assert (tmp_path / "s-02.jpg").read_bytes() == b"2"


def test_a_passing_frame_is_not_kept(tmp_path, monkeypatch):
    # 5 s later the screen already shows something else (flicking through, scrolling): as before, it is passed over
    out, asked = _run(monkeypatch, tmp_path, [1, 1, 1, 2, 3, 3, 3], held={3: 3})
    assert [(s.index, s.t) for s in out] == [(1, 0.0), (2, 27.0)]
    assert (tmp_path / "s-02.jpg").read_bytes() == b"3"       # the slide that followed, from 27 s
    # the recording's first moments (the recorder being set up) are never checked
    (tmp_path / "b").mkdir()
    out, asked = _run(monkeypatch, tmp_path / "b", [9, 1, 1, 1], held={0: 9})
    assert asked == [] and [s.index for s in out] == [1]


def test_a_slide_and_its_written_out_version_make_one_picture(tmp_path, monkeypatch):
    shown = ["other_a"] * 3 + ["build_a"] * 3 + ["build_b"] * 3
    out, _ = _run(monkeypatch, tmp_path, shown, real=True)
    assert [(s.index, s.t) for s in out] == [(1, 0.0), (2, 27.0), (2, 54.0)]     # the two stretches share slide 2
    assert sorted(os.listdir(tmp_path)) == ["s-01.jpg", "s-02.jpg"]
    assert (tmp_path / "s-02.jpg").read_bytes() == b"build_b"                    # the complete one


def test_a_darker_screen_after_a_light_slide_keeps_the_slide(tmp_path, monkeypatch):
    monkeypatch.setattr(sys.modules[__name__], "MADE", _dark_screens())
    for name in MADE:
        assert not slides.is_blank(_f(name))
        assert not slides.added_only(_f("build_a"), _f(name))     # nothing got brighter, but the slide is gone
    shown = ["build_a"] * 3 + ["dark_player"] * 3 + ["dark_header"] * 3
    out, _ = _run(monkeypatch, tmp_path, shown, real=True)
    assert [(s.index, s.t) for s in out] == [(1, 0.0), (2, 27.0), (3, 54.0)]
    assert (tmp_path / "s-01.jpg").read_bytes() == b"build_a"                    # the light slide's own picture


@pytest.mark.parametrize("name", ["build_a", "other_a", "ink_a"])
def test_real_slides_are_not_blank(name):
    assert not slides.is_blank(_f(name))


def test_a_capture_that_loses_its_connection_is_incomplete(tmp_path, monkeypatch):
    # the connection drops after slide 2: the slides so far come with the error, so they are kept but not final
    with pytest.raises(slides.Incomplete) as err:
        _run(monkeypatch, tmp_path, [1, 1, 2, 2] + [None] * 6, min_slide_s=1)
    assert [s.index for s in err.value.slides] == [1, 2]
    with pytest.raises(slides.Incomplete):                       # no slide at all: the same
        _run(monkeypatch, tmp_path, [None] * 12)
    out, _ = _run(monkeypatch, tmp_path, [1, None, 1, 1, 2, 2, 2, 2, 2, 2, 2], min_slide_s=1)
    assert [s.index for s in out] == [1, 2]                      # one frame missing now and then: complete
