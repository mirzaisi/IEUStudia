"""Long work that is not AI says how far it is too: a lecture whose slides are still being captured once it is
transcribed says how much of the screen recording is read (not a step that sits); a course file downloading says its
MB of MB next to its bar."""

import threading
import time
import types
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path

import pytest

from studia import i18n, jobs

WEB = Path(i18n.__file__).parent / "web"


def test_finishing_slides_says_how_much_of_the_screen_recording_is_read():
    said = []
    fut: Future = Future()
    job = types.SimpleNamespace(slides=fut, slides_read=0.0)
    mgr = types.SimpleNamespace(SLIDES_WAIT_S=10, _update=lambda j, stage, p, detail, **kw: said.append((detail, kw)))

    def capture():
        time.sleep(0.3)
        job.slides_read = 0.4
        time.sleep(1.5)
        fut.set_result([])

    threading.Thread(target=capture, daemon=True).start()
    jobs.JobManager._wait_slides(mgr, job)
    assert ("Finishing slides: 40% of the screen recording read", {"step": "slides"}) in said
    # one that never ends: given up after its time (the slides are optional: the lecture is finished without them)
    stuck = types.SimpleNamespace(slides=Future(), slides_read=0.0)
    mgr.SLIDES_WAIT_S = 0.3
    with pytest.raises(FutureTimeout):
        jobs.JobManager._wait_slides(mgr, stuck)


def test_the_slide_capture_says_how_much_of_the_screen_recording_it_read(tmp_path, isolated_home, monkeypatch):
    from studia import slides
    from studia.panopto import MediaSource
    from studia.services import Services
    from test_downloads import _services

    seen_read = []

    def extract(session, url, folder, rel, prefix, offset, cancel=None, seen=None, spans=(), progress=None):
        for i in (1, 2, 4):
            progress(i, 4)
            seen_read.append(job.slides_read)
        return []

    monkeypatch.setattr(slides, "extract_slides", extract)
    job = types.SimpleNamespace(id="r1", cancel=None, slides_read=0.0)
    screens = [(MediaSource("hls", "https://p/Sessions/s1.screen.hls/master.m3u8"), 0.1),
               (MediaSource("hls", "https://p/Sessions/s2.screen.hls/master.m3u8"), 20.8)]
    Services.slides_task(_services(tmp_path, isolated_home), job, screens, None)
    assert seen_read == [0.125, 0.25, 0.5, 0.625, 0.75, 1.0]      # two screen streams: each one half of the way


def test_a_course_file_downloading_says_its_mb():
    course = (WEB / "course.js").read_text(encoding="utf-8")
    assert 'class="dim mat-mb">${t("{done} of {total} MB"' in course
