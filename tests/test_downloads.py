"""Download side: decoding, telling a stream without sound from its header, every screen stream's slides (made once),
and the downloaded audio that is cleaned up."""

import datetime as dt
import os
import time
import types
from pathlib import Path

import numpy as np
import pytest

SR = 16000


def _write(path, fmt, seconds=70, rate=44100, video=False, opts=None, sound=True):
    """A small AAC file (a 440 Hz tone with a little noise), optionally with a picture (or only a picture)."""
    import av

    rng = np.random.default_rng(1)
    with av.open(str(path), "w", format=fmt, options=opts or {}) as out:
        vs = out.add_stream("mpeg4", rate=5) if video else None
        st = out.add_stream("aac", rate=rate, layout="stereo") if sound else None
        if vs is not None:
            vs.width, vs.height, vs.pix_fmt = 64, 48, "yuv420p"
            for i in range(10):
                frame = av.VideoFrame.from_ndarray(np.full((48, 64, 3), i * 9, np.uint8), format="rgb24")
                frame.pts = i
                for p in vs.encode(frame):
                    out.mux(p)
            for p in vs.encode(None):
                out.mux(p)
        if st is None:
            return path
        t = np.arange(seconds * rate) / rate
        sig = (0.3 * np.sin(2 * np.pi * 440 * t) + 0.01 * rng.standard_normal(t.size)).astype(np.float32)
        for i in range(0, sig.size, 1024):
            chunk = np.ascontiguousarray(np.vstack([sig[i:i + 1024]] * 2))
            frame = av.AudioFrame.from_ndarray(chunk, format="fltp", layout="stereo")
            frame.sample_rate, frame.pts = rate, i
            for p in st.encode(frame):
                out.mux(p)
        for p in st.encode(None):
            out.mux(p)
    return path


def _reference(path):
    """The decode as it was: every frame resampled on its own, the int16 pieces joined, then scaled."""
    import av

    chunks = []
    with av.open(str(path)) as c:
        res = av.AudioResampler(format="s16", layout="mono", rate=SR)
        for packet in c.demux(c.streams.audio[0]):
            for frame in packet.decode():
                frame.pts = None
                chunks += [o.to_ndarray().reshape(-1).copy() for o in res.resample(frame)]
        chunks += [o.to_ndarray().reshape(-1).copy() for o in res.resample(None)]
    return np.concatenate(chunks).astype(np.float32) * np.float32(1.0 / 32768.0)


@pytest.mark.parametrize("name,fmt", [("a.m4a", "mp4"), ("a.aac", "adts")])     # ADTS: no length in a header
def test_decode_gives_the_same_samples_as_frame_by_frame(tmp_path, name, fmt):
    from studia.audio import decode_audio

    path = _write(tmp_path / name, fmt)
    audio = decode_audio(path)
    assert audio.dtype == np.float32 and audio.flags.owndata
    assert np.array_equal(audio, _reference(path))


def test_decode_only_the_start_of_a_long_recording(tmp_path):
    from studia.audio import decode_audio

    path = _write(tmp_path / "a.m4a", "mp4", seconds=40)
    start = decode_audio(path, max_s=7)                      # (a study plan listens to the start of a long video)
    assert len(start) == 7 * SR and np.array_equal(start, _reference(path)[:7 * SR])


def test_decode_goes_on_frame_by_frame_when_the_format_changes(tmp_path, monkeypatch):
    import av

    from studia import audio as audio_mod

    writes, real = [], av.AudioFifo

    class Fifo:                                    # the 40th frame comes in another format: the fifo refuses it
        def __init__(self):
            self.fifo = real()

        def write(self, frame):
            writes.append(1)
            if len(writes) == 40:
                raise ValueError("Frame does not match AudioFifo parameters.")
            self.fifo.write(frame)

        samples = property(lambda self: self.fifo.samples)
        read = property(lambda self: self.fifo.read)

    path = _write(tmp_path / "a.m4a", "mp4", seconds=20)
    monkeypatch.setattr(audio_mod.av, "AudioFifo", Fifo)
    out = audio_mod.decode_audio(path)
    assert len(writes) == 40 and np.array_equal(out, _reference(path))


@pytest.mark.parametrize("cut", [0, 16, 500])
def test_a_file_whose_end_never_reached_the_disk_is_told_damaged(tmp_path, cut):
    """Panopto's podcasts keep their track list (moov) at the end: zeros there (a power cut while it downloaded) gave
    FFmpeg's raw "[Errno 1094995529] Invalid data found…" (all of it gone), "no audio track" (its first bytes left)
    or 0 seconds, as a recording without sound (its start left). Each is now one error the download side handles."""
    from studia.audio import NoAudioTrack, decode_audio
    from studia.jobs import _damaged

    path = _write(tmp_path / "a.m4a", "mp4", seconds=30)
    data = bytearray(path.read_bytes())
    start = data.index(b"moov") - 4 + cut
    data[start:] = bytes(len(data) - start)
    path.write_bytes(bytes(data))
    with pytest.raises(ValueError) as got:
        decode_audio(path)
    assert _damaged(got.value) and not isinstance(got.value, NoAudioTrack)
    picture = _write(tmp_path / "v.mp4", "mp4", video=True, sound=False)
    with pytest.raises(NoAudioTrack) as got:              # only a picture: that is still a stream without sound
        decode_audio(picture)
    assert not _damaged(got.value)


def test_silence_check_needs_no_copy():
    from studia.quality import is_silent, peak

    a = np.zeros(SR, dtype=np.float32)
    assert is_silent(a) and is_silent(np.zeros(0, dtype=np.float32)) and peak(a) == 0.0
    a[100] = -0.25
    assert peak(a) == 0.25 and not is_silent(a)


class RangeSession:
    """Serves local files as an HTTP server that honours byte ranges; counts the bytes sent."""

    def __init__(self, files):
        self.files, self.sent = files, 0

    def get(self, url, headers=None, **kw):
        data = self.files[url]
        rng = (headers or {}).get("Range")
        if rng is None:
            body, status, extra = data, 200, {}
        else:
            a, _, b = rng.split("=")[1].partition("-")
            body = data[int(a):int(b) + 1]
            status, extra = 206, {"Content-Range": f"bytes {a}-{b}/{len(data)}"}
        self.sent += len(body)
        return types.SimpleNamespace(status_code=status, content=body, url=url, headers=extra, text="",
                                     raise_for_status=lambda: None, __enter__=None)


@pytest.fixture
def served(monkeypatch):
    from studia import httpdl

    monkeypatch.setattr(httpdl, "_probe", lambda s, url: (url, len(s.files[url]), True, ""))
    return RangeSession


def test_an_mp4_without_sound_is_told_from_its_header(tmp_path, served):
    from studia.panopto import MediaSource, Panopto

    both = _write(tmp_path / "va.mp4", "mp4", seconds=30, video=True).read_bytes()
    picture = _write(tmp_path / "v.mp4", "mp4", video=True, sound=False).read_bytes()
    assert both.index(b"moov") > both.index(b"mdat")              # the track list at the end, as Panopto's podcasts
    files = {"https://x/va": both, "https://x/v": picture, "https://x/junk": b"\0\0\0\x10junkjunk" * 100}
    s = served(files)
    p = Panopto(s)
    assert p.has_sound(MediaSource("video_podcast", "https://x/va")) is True
    assert p.has_sound(MediaSource("video_podcast", "https://x/v")) is False
    assert p.has_sound(MediaSource("video_podcast", "https://x/junk")) is None     # can't tell: download it
    assert p.has_sound(MediaSource("video_podcast", "https://x/gone")) is None


def test_an_hls_stream_without_sound_is_told_from_its_init_segment(tmp_path, served):
    from studia.panopto import MediaSource, Panopto

    frag = {"movflags": "frag_keyframe+empty_moov"}
    with_sound = _write(tmp_path / "a.mp4", "mp4", seconds=5, video=True, opts=frag).read_bytes()
    master = ('#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=900000,CODECS="avc1.4d002a,mp4a.40.2"\nhi.m3u8\n'
              '#EXT-X-STREAM-INF:BANDWIDTH=300000,CODECS="avc1.4d002a,mp4a.40.2"\nlo.m3u8\n')
    moov_end = with_sound.index(b"moof") - 4
    media = (f'#EXTM3U\n#EXT-X-MAP:URI="f.mp4",BYTERANGE="{moov_end}@0"\n#EXTINF:5.0,\n'
             f'#EXT-X-BYTERANGE:{len(with_sound) - moov_end}@{moov_end}\nf.mp4\n#EXT-X-ENDLIST\n')

    class Hls(served):
        def get(self, url, headers=None, **kw):
            if url.endswith(".m3u8"):
                assert url.endswith("lo.m3u8") or url.endswith("master.m3u8")      # the variant a download takes
                return types.SimpleNamespace(text=master if "master" in url else media, url=url, status_code=200,
                                             raise_for_status=lambda: None)
            return super().get(url, headers, **kw)

    s = Hls({"https://x/f.mp4": with_sound})
    assert Panopto(s).has_sound(MediaSource("hls", "https://x/master.m3u8")) is True
    assert s.sent == moov_end                                       # the init segment only, not the media
    picture = _write(tmp_path / "b.mp4", "mp4", video=True, opts=frag, sound=False).read_bytes()
    s.files["https://x/f.mp4"] = picture
    media = media.replace(f'BYTERANGE="{moov_end}@0"', f'BYTERANGE="{picture.index(b"moof") - 4}@0"')
    assert Panopto(s).has_sound(MediaSource("hls", "https://x/master.m3u8")) is False


def test_screen_sources_in_time_order():
    from studia.panopto import Panopto

    info = {"Delivery": {"Streams": [
        {"Tag": "DV", "StreamUrl": "https://x/dv.m3u8", "RelativeStart": 0},
        {"Tag": "SCREEN", "StreamUrl": "https://x/s2.m3u8", "RelativeStart": 20.8},
        {"Tag": "SCREEN", "StreamUrl": "https://x/s1.m3u8", "RelativeStart": 0.09},
        {"Tag": "SCREEN", "StreamUrl": "", "RelativeStart": 5},
        {"Tag": "screen", "StreamUrl": "https://x/s3.m3u8", "RelativeStart": 2717.1}]}}
    out = Panopto(None).screen_sources(info)
    assert [(s.url.rsplit("/", 1)[1], off) for s, off in out] == [("s1.m3u8", 0.09), ("s2.m3u8", 20.8),
                                                                   ("s3.m3u8", 2717.1)]
    assert Panopto(None).screen_sources({"Delivery": {}}) == []


def test_slides_of_several_streams_share_numbers_and_duplicates(tmp_path, monkeypatch):
    from studia import slides
    from studia.hls import Segment

    shown = {"a": [1, 1, 1, 2, 2, 2], "b": [2, 2, 3, 3, 3, 3]}    # stream b shows slide 2 again, then a new one
    monkeypatch.setattr(slides, "media_playlist", lambda s, u, prefer: types.SimpleNamespace(text=u, url=u))
    monkeypatch.setattr(slides, "parse_playlist", lambda text, url: (("init", 0, 0), [
        Segment(i * 9.0, 9.0, f"{text}{i}", 0, 10) for i in range(6)]))
    monkeypatch.setattr(slides, "find_avcc", lambda b: b"avcc")
    monkeypatch.setattr(slides, "_fetch_keyframe", lambda session, seg, avcc: shown[seg.url[0]][int(seg.url[1:])])
    monkeypatch.setattr(slides, "thumb", lambda f: np.full((4, 4), f, dtype=np.float32))
    monkeypatch.setattr(slides, "big", lambda f: np.full((4, 4), f, dtype=np.int16))
    monkeypatch.setattr(slides, "changed_fraction", lambda a, b: float(abs(a - b).mean() > 0))
    monkeypatch.setattr(slides, "is_blank", lambda t: False)
    monkeypatch.setattr(slides, "added_only", lambda a, b: False)
    monkeypatch.setattr(slides, "save_jpeg", lambda frame, path, max_width=1600: path.write_bytes(b"%d" % frame))
    session = types.SimpleNamespace(get=lambda url: types.SimpleNamespace(content=b""))
    seen = []
    first = slides.extract_slides(session, "a", tmp_path, "slides/x", "s", 0.0, seen=seen, min_slide_s=1)
    second = slides.extract_slides(session, "b", tmp_path, "slides/x", "s", 100.0, seen=seen, min_slide_s=1)
    assert [(s.index, s.t) for s in first] == [(1, 0.0), (2, 27.0)]
    assert [(s.index, s.t, s.image) for s in second] == [(2, 100.0, "slides/x/s-02.jpg"), (3, 118.0, "slides/x/s-03.jpg")]
    assert sorted(os.listdir(tmp_path)) == ["s-01.jpg", "s-02.jpg", "s-03.jpg"]       # nothing written over
    assert (tmp_path / "s-03.jpg").read_bytes() == b"3"


def _services(tmp_path, isolated_home):
    """Just what Services.slides_task uses: the library (one recording with a course) and the output folder."""
    import logging

    from studia.domain import Course, Recording
    from studia.library import Library

    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="c", title="t", code="EEE 215", name="n")])
    lib.merge_recordings("c", [Recording(delivery_id="r1", course_id="c", folder_id="f", folder_path="", name="r1",
                                         start=dt.datetime(2026, 10, 13, 11, 0), duration_s=2400, viewer_url="u")],
                         first_sync=True)
    return types.SimpleNamespace(lib=lib, output_root=tmp_path / "out", log=logging.getLogger("test"))


def test_slides_come_from_every_screen_stream_and_are_made_once(tmp_path, isolated_home, monkeypatch):
    from studia import slides, store
    from studia.panopto import MediaSource
    from studia.services import Services
    from studia.slides import Slide

    calls = []

    def extract(session, url, folder, rel, prefix, offset, cancel=None, seen=None, spans=(), progress=None):
        calls.append((url, offset))
        folder.mkdir(parents=True, exist_ok=True)
        idx = len(seen) + 1
        path = folder / f"{prefix}-{idx:02d}.jpg"
        path.write_bytes(b"jpg")
        s = Slide(idx, offset, offset + 10, f"{rel}/{path.name}", str(path))
        seen.append((None, s))
        return [s]

    monkeypatch.setattr(slides, "extract_slides", extract)
    svc = _services(tmp_path, isolated_home)
    job = types.SimpleNamespace(id="r1", cancel=None)
    screens = [(MediaSource("hls", "https://p/Sessions/s1.screen.hls/master.m3u8?InvocationID=1"), 0.1),
               (MediaSource("hls", "https://p/Sessions/s2.screen.hls/master.m3u8?InvocationID=1"), 20.8)]
    out = Services.slides_task(svc, job, screens, None)
    assert [(s.index, s.t) for s in out] == [(1, 0.1), (2, 20.8)] and len(calls) == 2
    assert [s.index for s in store.load_slides("r1")] == [1, 2]
    again = [(MediaSource("hls", src.url.replace("=1", "=2")), off) for src, off in screens]    # a new request id
    assert [s.index for s in Services.slides_task(svc, job, again, None)] == [1, 2] and len(calls) == 2
    os.remove(out[1].path)                                         # an image is gone: made again
    Services.slides_task(svc, job, again, None)
    assert len(calls) == 4
    redone = [(MediaSource("hls", "https://p/Sessions/s9.screen.hls/master.m3u8"), 0.1)]   # Panopto made it again
    Services.slides_task(svc, job, redone, None)
    assert len(calls) == 5 and [s.index for s in store.load_slides("r1")] == [1]
    store.slides_path("r1").with_suffix(".key").unlink()          # slides from an older version (no key)
    Services.slides_task(svc, job, redone, None)
    assert len(calls) == 5                                         # one screen stream: what it read then too
    store.slides_path("r1").with_suffix(".key").unlink()
    Services.slides_task(svc, job, screens, None)
    assert len(calls) == 7                                         # several: it read only the first, so again


def test_two_lectures_starting_in_the_same_minute_keep_their_own_slides(tmp_path, isolated_home, monkeypatch):
    """Files uploaded together get the same start minute: the slides were named <HHMM>-NN.jpg in the day's folder, so
    the second lecture's pictures took the first one's place, and the first showed them from then on."""
    from studia import slides, store
    from studia.domain import Recording
    from studia.panopto import MediaSource
    from studia.services import Services
    from studia.slides import Slide

    def extract(session, url, folder, rel, prefix, offset, cancel=None, seen=None, spans=(), progress=None):
        folder.mkdir(parents=True, exist_ok=True)
        out = []
        for idx in (1, 2):
            path = folder / f"{prefix}-{idx:02d}.jpg"
            path.write_bytes(url.encode())
            out.append(Slide(idx, offset + idx, offset + idx + 5, f"{rel}/{path.name}", str(path)))
        return out

    monkeypatch.setattr(slides, "extract_slides", extract)
    svc = _services(tmp_path, isolated_home)
    svc.lib.merge_recordings("c", [Recording(delivery_id=d, course_id="c", folder_id="f", folder_path="", name=d,
                                             start=dt.datetime(2026, 10, 13, 11, 0, s), duration_s=d2, viewer_url="u")
                                   for d, s, d2 in (("r1", 0, 2400), ("r2aaaaaaaa", 7, 1200))], first_sync=False)
    for did in ("r1", "r2aaaaaaaa"):
        Services.slides_task(svc, types.SimpleNamespace(id=did, cancel=None),
                             [(MediaSource("hls", f"https://p/{did}.screen.hls/master.m3u8"), 0.0)], None)
    first, second = store.load_slides("r1"), store.load_slides("r2aaaaaaaa")
    assert not {s.path for s in first} & {s.path for s in second}
    assert [Path(s.path).read_bytes() for s in first] == [b"https://p/r1.screen.hls/master.m3u8"] * 2
    assert Path(second[0].path).name == "1100-r2aaaaaa-01.jpg"


def test_capturing_a_lecture_again_keeps_the_pictures_another_lecture_shows(tmp_path, isolated_home, monkeypatch):
    """Before 4.0 two lectures of a day that start in the same minute shared their pictures (<HHMM>-NN.jpg). Capturing
    one of them again deleted its old pictures, and so the other lecture's: they were gone for good."""
    from studia import slides, store
    from studia.domain import Recording
    from studia.outputs.writer import course_dir, day_stem
    from studia.panopto import MediaSource
    from studia.services import Services
    from studia.slides import Slide

    def extract(session, url, folder, rel, prefix, offset, cancel=None, seen=None, spans=(), progress=None):
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{prefix}-01.jpg"
        path.write_bytes(b"new")
        return [Slide(1, offset, offset + 5, f"{rel}/{path.name}", str(path))]

    monkeypatch.setattr(slides, "extract_slides", extract)
    svc = _services(tmp_path, isolated_home)
    svc.lib.merge_recordings("c", [Recording(delivery_id=d, course_id="c", folder_id="f", folder_path="", name=d,
                                             start=dt.datetime(2026, 10, 13, 11, 0, s), duration_s=1200, viewer_url="u")
                                   for d, s in (("r1", 0), ("r2aaaaaaaa", 7))], first_sync=False)
    course, rec = svc.lib.get_course("c"), svc.lib.get_recording("r1")
    stem = day_stem(course, rec.start.date(), rec.kind)
    folder = course_dir(svc.output_root, course, svc.lib.courses(True)) / "slides" / stem
    folder.mkdir(parents=True)
    old = [folder / f"1100-{i:02d}.jpg" for i in (1, 2, 3)]           # as 3.x named them: the start minute only
    for p in old:
        p.write_bytes(b"3.x")
    shown = [Slide(i, i * 10.0, i * 10.0 + 10, f"slides/{stem}/{p.name}", str(p)) for i, p in enumerate(old, 1)]
    store.save_slides("r1", shown)
    store.save_slides("r2aaaaaaaa", shown[:2])                        # the other lecture shows two of them
    Services.slides_task(svc, types.SimpleNamespace(id="r1", cancel=None),
                         [(MediaSource("hls", "https://p/r1.screen.hls/master.m3u8"), 0.0)], None, again=True)
    assert [Path(s.path).name for s in store.load_slides("r1")] == ["1100-r1-01.jpg"]
    assert [Path(s.path).name for s in store.load_slides("r2aaaaaaaa")] == ["1100-01.jpg", "1100-02.jpg"]
    assert not old[2].exists()                                        # only the one no other lecture shows went
    Services.slides_task(svc, types.SimpleNamespace(id="r2aaaaaaaa", cancel=None),
                         [(MediaSource("hls", "https://p/r2.screen.hls/master.m3u8"), 0.0)], None, again=True)
    assert not any(p.exists() for p in old)                           # no lecture shows them now: they go


def test_a_failed_screen_stream_keeps_the_others_and_is_tried_again(tmp_path, isolated_home, monkeypatch):
    from studia import slides, store
    from studia.panopto import MediaSource
    from studia.services import Services
    from studia.slides import Slide

    def extract(session, url, folder, rel, prefix, offset, cancel=None, seen=None, spans=(), progress=None):
        if "bad" in url:
            raise RuntimeError("Unsupported screen-recording format.")
        return [Slide(1, offset, offset + 5, "", "")]

    monkeypatch.setattr(slides, "extract_slides", extract)
    svc = _services(tmp_path, isolated_home)
    job = types.SimpleNamespace(id="r1", cancel=None)
    screens = [(MediaSource("hls", "https://p/bad.m3u8"), 0.0), (MediaSource("hls", "https://p/ok.m3u8"), 30.0)]
    assert len(Services.slides_task(svc, job, screens, None)) == 1
    assert store.slides_path("r1").with_suffix(".key").read_text(encoding="utf-8").startswith("failed 1\n")
    with pytest.raises(RuntimeError):
        Services.slides_task(svc, job, screens[:1], None)


def test_a_capture_that_lost_its_connection_keeps_its_slides_and_is_tried_again(tmp_path, isolated_home, monkeypatch):
    from studia import slides, store
    from studia.panopto import MediaSource
    from studia.services import SLIDES_TRIES, Services, _slides_done, _slides_key
    from studia.slides import Incomplete, Slide

    calls = []

    def extract(session, url, folder, rel, prefix, offset, cancel=None, seen=None, spans=(), progress=None):
        calls.append(url)
        raise Incomplete(200, 300, [Slide(1, offset, offset + 5, "", "")])    # the Wi-Fi dropped after slide 1

    monkeypatch.setattr(slides, "extract_slides", extract)
    svc = _services(tmp_path, isolated_home)
    job = types.SimpleNamespace(id="r1", cancel=None)
    screens = [(MediaSource("hls", "https://p/s.m3u8"), 0.0)]
    assert len(Services.slides_task(svc, job, screens, None)) == 1          # what came is kept meanwhile
    assert [s.index for s in store.load_slides("r1")] == [1]
    assert not _slides_done("r1", _slides_key(screens), 1)                  # not complete: made again next time
    for _ in range(SLIDES_TRIES - 1):
        Services.slides_task(svc, job, screens, None)
    assert len(calls) == SLIDES_TRIES and _slides_done("r1", _slides_key(screens), 1)    # a limit: then kept
    Services.slides_task(svc, job, screens, None)
    assert len(calls) == SLIDES_TRIES


def test_slides_of_an_edited_recording_follow_its_cuts(tmp_path, isolated_home, monkeypatch):
    from studia import slides
    from studia.panopto import MediaSource, Panopto
    from studia.services import Services, _slides_key
    from studia.slides import Slide

    # EEE215-2 Lab Week-4: 6:54 cut out at 47:19 (the editor, or the recorder paused); the screen video still has it
    url = "https://p/Sessions/a.screen.hls/master.m3u8"
    info = {"Delivery": {"Streams": [{"Tag": "SCREEN", "StreamUrl": url, "RelativeStart": 0.337, "RelativeSegments": [
        {"RelativeStart": 2839.381, "Start": 3253.031, "End": 5726.684},
        {"RelativeStart": 0.337, "Start": 0, "End": 2839.044}]}]}}
    (edited, offset), = Panopto(None).screen_sources(info)
    assert edited.spans == ((0.0, 2839.044, 0.337), (3253.031, 5726.684, 2839.381)) and offset == 0.337
    info["Delivery"]["Streams"][0]["RelativeSegments"][0]["End"] = None      # one that can't be read: as before
    assert Panopto(None).screen_sources(info)[0][0].spans == ()
    plain = MediaSource("hls", url)
    split = MediaSource("hls", url, spans=((0, 0.08, 0.337), (0.08, 5726.6, 0.417)))     # split, nothing moved
    assert _slides_key([(split, offset)]) == _slides_key([(plain, offset)])   # slides made before: kept
    assert _slides_key([(edited, offset)]) != _slides_key([(plain, offset)])  # made before the cut: made again
    got = []

    def extract(session, url, folder, rel, prefix, offset, cancel=None, seen=None, spans=(), progress=None):
        got.append(spans)
        return [Slide(1, offset, offset + 5, "", "")]

    monkeypatch.setattr(slides, "extract_slides", extract)
    Services.slides_task(_services(tmp_path, isolated_home), types.SimpleNamespace(id="r1", cancel=None),
                         [(edited, offset)], None)
    assert got == [edited.spans]


def test_old_downloaded_audio_is_swept_unless_its_lecture_is_queued(tmp_path, isolated_home):
    import json

    from studia.domain import Course, Recording
    from studia.jobs import QUEUE_KEY
    from studia.library import Library
    from studia.paths import audio_cache_dir
    from studia.services import sweep_audio_cache

    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="c", title="t", code="EEE 215", name="n")])
    lib.merge_recordings("c", [Recording(delivery_id=d, course_id="c", folder_id="f", folder_path="", name=d,
                                         start=dt.datetime(2026, 10, 13, 11, 0), duration_s=2400, viewer_url="u")
                               for d in ("old", "queued", "done", "new")], first_sync=True)
    lib.set_status("done", "done")
    lib.set_meta(QUEUE_KEY, json.dumps(["queued"]))
    cache = audio_cache_dir()
    week_ago = time.time() - 8 * 86400
    names = ["old.m4a", "old.mp4.part0", "old.mp4.parts.json", "queued.m4a", "done.m4a", "new.m4a",
             "other.m4a"]          # another profile's lecture, left from before 4.0 (the folder was shared then)
    for name in names:
        (cache / name).write_bytes(b"x")
        if not name.startswith("new"):
            os.utime(cache / name, (week_ago, week_ago))
    assert sweep_audio_cache(lib, keep_audio=True) == 3            # "Keep downloaded audio": a transcribed one stays
    assert sorted(os.listdir(cache)) == ["done.m4a", "new.m4a", "other.m4a", "queued.m4a"]
    assert sweep_audio_cache(lib, keep_audio=False) == 1 and sorted(os.listdir(cache)) == ["new.m4a", "other.m4a",
                                                                                         "queued.m4a"]
    lib.set_meta(QUEUE_KEY, "not json")
    os.utime(cache / "new.m4a", (week_ago, week_ago))
    assert sweep_audio_cache(lib, keep_audio=False) == 0           # the queue can't be read: nothing is deleted


def test_each_profile_keeps_its_own_downloaded_audio(tmp_path, isolated_home):
    """Two students on one PC who follow the same course download the same recordings: one's start swept the other's
    kept or queued audio away, and a lecture one of them transcribed or cancelled deleted the other's file."""
    from studia import accounts, paths
    from studia.domain import Course, Recording
    from studia.library import Library
    from studia.services import sweep_audio_cache

    week_ago = time.time() - 8 * 86400
    b = accounts.create("b.student")
    kept = {}
    for acc in (accounts.get("main"), b):
        accounts.activate(acc.id)
        lib = Library(paths.db_path())
        lib.upsert_courses([Course(id="c", title="t", code="EEE 215", name="n")])
        lib.merge_recordings("c", [Recording(delivery_id="x", course_id="c", folder_id="f", folder_path="", name="x",
                                             start=dt.datetime(2026, 10, 13, 11, 0), duration_s=2400, viewer_url="u")],
                             first_sync=True)
        lib.set_status("x", "done")
        f = paths.audio_cache_dir() / "x.m4a"
        f.write_bytes(acc.id.encode())
        os.utime(f, (week_ago, week_ago))
        kept[acc.id] = (lib, f)
    assert kept["main"][1].parent == isolated_home / "cache" / "audio"          # the first profile's: where it was
    assert kept[b.id][1].parent == isolated_home / "profiles" / b.id / "cache" / "audio"
    accounts.activate("main")
    assert sweep_audio_cache(kept["main"][0], keep_audio=False) == 1           # its own lecture's audio goes...
    assert kept[b.id][1].read_bytes() == b.id.encode()                         # ...the other profile's stays
    for lib, _ in kept.values():
        lib.close()
    accounts.activate("main")


def test_an_added_profiles_audio_from_before_4_0_is_kept_and_found(isolated_home):
    """Before 4.0 every profile's downloaded audio went to the first profile's folder. After the update the first
    profile's start deleted what its own library doesn't know (an added profile's audio, kept by its "Keep downloaded
    audio"), and the added profile looked only in its own new folder: its player and a transcription didn't find it."""
    from studia import accounts, paths, player
    from studia.config import load_settings, save_settings
    from studia.domain import Course, Recording
    from studia.library import Library
    from studia.services import Services

    shared, now = isolated_home / "cache" / "audio", time.time()
    b = accounts.create("b.student")

    def profile(acc_id, courses, keep):
        accounts.activate(acc_id)
        lib = Library(paths.db_path())
        lib.upsert_courses([Course(id=c, title=f"2026-1 {c}", code=c, name=c) for c in courses])
        for c, dids in courses.items():
            lib.merge_recordings(c, [Recording(delivery_id=d, course_id=c, folder_id="f", folder_path="", name=d,
                                               start=dt.datetime(2026, 10, 13, 11, 0), duration_s=2400, viewer_url="u")
                                     for d in dids], first_sync=True)
            for d in dids:
                lib.set_status(d, "done")
        lib.close()
        s = load_settings()
        s.keep_audio = keep
        save_settings(s)

    def start(acc_id):                         # as app.py: whose data this run uses, then the services
        accounts.activate(acc_id)
        s = Services()
        s.shutdown()
        s.lib.close()

    def downloaded(name, days_ago):            # (jobs.py: audio_cache_dir() / "<delivery id>.m4a")
        shared.mkdir(parents=True, exist_ok=True)
        (shared / name).write_bytes(name.encode())
        os.utime(shared / name, (now - days_ago * 86400,) * 2)

    profile("main", {"EEE 215": ["both"]}, keep=True)
    profile(b.id, {"EEE 215": ["both"], "PHYS 100": ["hers", "late"]}, keep=True)
    for f in paths.profile_dir().glob("library.db*"):
        os.utime(f, (now - 5 * 86400,) * 2)     # she last used it 5 days ago, before the update
    downloaded("hers.m4a", 10)                 # 3.x kept it for her in the shared folder
    start("main")                              # after the update the first profile starts first
    assert (shared / "hers.m4a").read_bytes() == b"hers.m4a"           # not its lecture: never its to delete
    downloaded("both.m4a", 2)                  # the first profile (Keep on) downloads a lecture both follow
    start(b.id)
    own = paths.audio_cache_dir()
    assert own == isolated_home / "profiles" / b.id / "cache" / "audio"
    assert player.local_file("hers") == own / "hers.m4a" and not (shared / "hers.m4a").exists()   # hers: moved
    assert player.local_file("both") is None and (shared / "both.m4a").is_file()   # the first profile's: stays
    downloaded("late.m4a", 10)
    start(b.id)                                # moved once: the folder isn't looked at again at later starts
    assert (shared / "late.m4a").is_file() and player.local_file("hers") == own / "hers.m4a"
    accounts.activate("main")
    assert player.local_file("both") == shared / "both.m4a"
