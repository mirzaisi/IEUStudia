"""Listening to YouTube videos for study plans: which captions can't show what a video teaches, its original sound
track only (never one of YouTube's automatic dubs) in the smallest format, what IEUStudia heard kept like captions,
the plan's limits with a reason for every video left out, lectures first, and the speech model lent by the lectures'
queue. No network, no speech model: yt-dlp, the downloads, the decoder and the engine are fakes."""

import contextlib
import threading
import types

import pytest

from studia import jobs as jobs_mod
from studia import listen, videos
from studia.cancel import Cancelled
from studia.config import Settings
from studia.domain import Segment, Transcript
from studia.videos import Cue, VideoError, VideoInfo


def lesson(n_words=1200, minutes=10.0, kind="auto", lang="en", text=None):
    """A video whose captions say ``n_words`` words (varied, as a teacher speaks) over ``minutes``."""
    words = text.split() if text else [f"word{i % 400}" for i in range(n_words)]
    dur = minutes * 60
    step = max(1, len(words) // 100)
    cues = [Cue(dur * i / len(words), dur * (i + step) / len(words), " ".join(words[i:i + step]))
            for i in range(0, len(words), step)]
    return VideoInfo("lesson00001", "A lesson", duration_s=dur, cues=cues, caption_kind=kind, caption_lang=lang,
                     language=lang)


# ------------------------------------------------------------------ which captions can't be used
def test_captions_that_cant_show_what_a_video_teaches():
    langs = ("en",)
    assert videos.caption_problem(lesson(), langs) == ""
    assert videos.caption_problem(VideoInfo("x", "t", duration_s=600), langs) == "none"
    assert videos.caption_problem(lesson(lang="de"), langs) == "language"           # in a language not wanted
    assert videos.caption_problem(lesson(lang="tr"), ("en", "tr")) == ""
    assert videos.caption_problem(lesson(n_words=200), langs) == "short"            # 20 words a minute
    half = lesson()
    half.cues = [c for c in half.cues if c.end < 200]                              # stop after a third of it
    half.cues += [Cue(c.start + 1, c.end + 1, c.text) for c in half.cues]         # (still enough words)
    assert videos.caption_problem(half, langs) == "short"
    music = lesson()
    music.cues = [Cue(c.start, c.end, "[Music]" if i % 2 else c.text) for i, c in enumerate(music.cues)]
    assert videos.caption_problem(music, langs) == "garbled"
    loop = lesson(text=" ".join(["the integral of x"] * 400))                      # a recognition loop
    assert videos.caption_problem(loop, langs) == "garbled"
    # a long lecture repeats its words: as a whole few are new, in every 200 of them many are - not a loop
    long = lesson(n_words=6000, minutes=45, text=" ".join(f"w{i % 90} w{(i * 7) % 113}" for i in range(3000)))
    assert videos.caption_problem(long, langs) == ""
    # a creator's own captions are trusted however short, and so is what IEUStudia heard
    assert videos.caption_problem(lesson(n_words=100, kind="manual"), langs) == ""
    assert videos.caption_problem(lesson(n_words=100, kind=videos.SPEECH), langs) == ""


def test_what_was_heard_is_kept_like_captions():
    info = VideoInfo("heard000001", "No captions", duration_s=300)
    assert videos.with_heard(info) is info and videos.heard("heard000001") is None
    videos.remember_heard("heard000001", [Cue(0, 4, "Today: the normal distribution."), Cue(4, 9, "We standardize.")],
                          "en", model="large-v3", device="cuda", why="none", seconds=12.5)
    got = videos.with_heard(info)
    assert got.caption_kind == videos.SPEECH and got.caption_lang == "en" and got.has_captions
    assert [c.text for c in got.cues] == ["Today: the normal distribution.", "We standardize."]
    assert got.heard == {"model": "large-v3", "device": "cuda", "why": "none", "at": got.heard["at"], "seconds": 12.5}
    # captions the creator added since are used instead; YouTube's automatic ones are not
    manual = VideoInfo("heard000001", "x", duration_s=300, caption_kind="manual", caption_lang="en",
                       cues=[Cue(0, 5, "Hello and welcome to this lesson about distributions.")])
    assert videos.with_heard(manual) is manual
    auto = VideoInfo("heard000001", "x", duration_s=300, caption_kind="auto", caption_lang="en", cues=[Cue(0, 5, "x")])
    assert videos.with_heard(auto).caption_kind == videos.SPEECH
    assert videos.heard("not a video") is None


def test_the_original_sound_track_in_its_smallest_format():
    fmts = [{"format_id": "139-0", "acodec": "mp4a.40.5", "vcodec": "none", "filesize": 2225105, "abr": 48.8,
             "format_note": "Arabic, low", "language_preference": -1, "protocol": "https"},          # an automatic dub
            {"format_id": "249-7", "acodec": "opus", "vcodec": "none", "filesize": 2128204, "abr": 46.7,
             "format_note": "English (US) original (default), low", "language_preference": 10, "protocol": "https"},
            {"format_id": "139-7", "acodec": "mp4a.40.5", "vcodec": "none", "filesize": 2224834, "abr": 48.8,
             "format_note": "English (US) original (default), low", "language_preference": 10, "protocol": "https"},
            {"format_id": "233-7", "acodec": "mp4a", "vcodec": "none", "format_note": "original",
             "language_preference": 10, "protocol": "m3u8_native"},                                    # HLS: left out
            {"format_id": "18", "acodec": "mp4a", "vcodec": "avc1", "filesize": 900000, "protocol": "https"}]  # video
    assert [f["format_id"] for f in videos._original_audio({"formats": fmts})] == ["249-7"]
    plain = [{"format_id": "140", "acodec": "mp4a", "vcodec": "none", "abr": 129, "protocol": "https"},
             {"format_id": "139", "acodec": "mp4a", "vcodec": "none", "abr": 48, "protocol": "https"}]
    assert [f["format_id"] for f in videos._original_audio({"formats": plain})] == ["139"]   # no languages: smallest
    assert list(videos._original_audio({"formats": []})) == []


class FakeDownloadYDL:
    """yt_dlp.YoutubeDL for download_audio: picks a format with the given selector and "downloads" it."""
    answer = None
    made = []

    def __init__(self, params):
        self.params = params
        FakeDownloadYDL.made.append(params)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=True):
        ans = FakeDownloadYDL.answer
        if isinstance(ans, BaseException):
            raise ans
        fmt = list(self.params["format"]({"formats": ans["formats"]}))
        if not fmt:
            raise RuntimeError("ERROR: [youtube] x: Requested format is not available")
        if ans.get("size", 0) > videos.AUDIO_MAX_BYTES:
            return {"filesize": ans["size"], "requested_downloads": []}
        for hook in self.params["progress_hooks"]:
            hook({"status": "downloading", "downloaded_bytes": 10, "total_bytes": 20})
        path = self.params["outtmpl"]["default"].replace("%(ext)s", "webm")
        with open(path, "wb") as f:
            f.write(b"sound")
        return {"requested_downloads": [{"filepath": path, "format_id": fmt[0]["format_id"]}]}


def test_download_audio(tmp_path, monkeypatch):
    import yt_dlp

    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeDownloadYDL)
    audio = [{"format_id": "249", "acodec": "opus", "vcodec": "none", "abr": 46, "protocol": "https",
              "language_preference": 10}]
    FakeDownloadYDL.answer = {"formats": audio}
    seen = []
    path = videos.download_audio("sound000001", tmp_path, progress=lambda d, n: seen.append((d, n)))
    assert path == tmp_path / "sound000001.webm" and path.read_bytes() == b"sound" and seen == [(10, 20)]
    params = FakeDownloadYDL.made[-1]
    assert params["skip_download"] is False and params["max_filesize"] == videos.AUDIO_MAX_BYTES
    path.unlink()
    FakeDownloadYDL.answer = {"formats": [{"format_id": "18", "acodec": "mp4a", "vcodec": "avc1"}]}   # no sound alone
    with pytest.raises(VideoError) as e:
        videos.download_audio("sound000001", tmp_path)
    assert e.value.kind == "unavailable"
    FakeDownloadYDL.answer = {"formats": audio, "size": videos.AUDIO_MAX_BYTES + 1}
    with pytest.raises(VideoError) as e:
        videos.download_audio("sound000001", tmp_path)
    assert e.value.kind == "too_big"
    FakeDownloadYDL.answer = RuntimeError("ERROR: [youtube] x: Sign in to confirm you're not a bot")
    with pytest.raises(VideoError) as e:
        videos.download_audio("sound000001", tmp_path)
    assert e.value.kind == "bot"
    stop = threading.Event()
    stop.set()
    FakeDownloadYDL.answer = {"formats": audio}
    with pytest.raises(VideoError) as e:                      # cancelled before it starts
        videos.download_audio("sound000001", tmp_path, cancel=stop)
    assert e.value.kind == "cancelled"


# ------------------------------------------------------------------ the listening
class FakeEngine:
    def __init__(self, text="So to standardize it we subtract the mean and divide by the standard deviation. " * 4,
                 fail=None, on_batch=None):
        self.text, self.fail, self.on_batch = text, fail, on_batch
        self.device, self.model_name, self.loaded = "cuda", "large-v3", False
        self.calls = []

    def ensure_model(self, progress, cancel=None):
        self.loaded = True

    def transcribe(self, audio, progress, cancel, meta=None, languages=None):
        self.calls.append(tuple(languages or ()))
        if self.on_batch:
            self.on_batch()
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        if self.fail:
            raise self.fail
        progress("transcribe", 1.0, "")
        segs = [Segment(0, 5, self.text, "en", 0.9), Segment(6, 9, "[unclear]", "en", 0.2)]
        return Transcript(delivery_id="", segments=segs, duration_s=10, speech_s=8, language_seconds={"en": 8.0},
                          model="large-v3", device="cuda (RTX)", engine_version="t", created_at="x")


class FakeJobs:
    def __init__(self, engine, busy=False):
        self.engine, self.lectures, self.lent = engine, busy, 0

    def busy(self):
        return self.lectures

    @contextlib.contextmanager
    def lend_engine(self, model, device):
        if self.lectures:
            raise jobs_mod.EngineBusy("lectures")
        self.lent += 1
        yield self.engine


@pytest.fixture
def hearing(monkeypatch, tmp_path):
    """Downloads that write a small file, a decoder that reads it, and a PC with large-v3 on its graphics card."""
    got = {"downloads": [], "files": []}

    def download(vid, folder, cancel=None, progress=None):
        if cancel is not None and cancel.is_set():
            raise VideoError("Cancelled", "cancelled")
        err = got.get("refuse", {}).get(vid)
        if err:
            raise err
        got["downloads"].append(vid)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{vid}.webm"
        path.write_bytes(b"sound")
        got["files"].append(path)
        if progress:
            progress(5, 5)
        return path

    monkeypatch.setattr(videos, "download_audio", download)
    monkeypatch.setattr(listen, "_decode", lambda path, cancel, upto=listen.LISTEN_S: got.setdefault("upto", []).append(upto)
                        or [0.0] * 16000)
    monkeypatch.setattr(listen, "speech_choice", lambda settings: ("large-v3", "auto", "cuda", ""))
    return got


def info(vid, minutes=10, language="en"):
    return types.SimpleNamespace(duration_s=minutes * 60, language=language, id=vid)


def test_a_video_is_listened_to_and_kept(hearing):
    engine = FakeEngine()
    jobs, used, steps = FakeJobs(engine), {}, []
    out = listen.listen(jobs, Settings(), [("aaaaaaaaaaa", info("aaaaaaaaaaa"), "none")], used, ("en", "tr"),
                        threading.Event(), lambda i, n, step, f: steps.append((i, n, step)))
    assert out == {"aaaaaaaaaaa": {"heard": True, "model": "large-v3", "device": "cuda", "seconds": out["aaaaaaaaaaa"]["seconds"]}}
    assert engine.calls == [("en",)]                              # spoken in English: one language, not two
    got = videos.heard("aaaaaaaaaaa")
    assert got["lang"] == "en" and got["why"] == "none" and [c.text for c in got["cues"]] == [engine.text.strip()]
    assert used["videos"] == 1 and used["sound_s"] == 600 and "wall_s" in used
    assert all(not f.exists() for f in hearing["files"])          # the sound is deleted
    assert (1, 1, "download") in steps and (1, 1, "transcribe") in steps
    # heard before: nothing is downloaded or transcribed again, and it costs nothing of the plan's limits
    again = listen.listen(jobs, Settings(), [("aaaaaaaaaaa", info("aaaaaaaaaaa"), "none")], used, ("en",), threading.Event())
    assert again == {"aaaaaaaaaaa": {"heard": True, "cached": True}} and hearing["downloads"] == ["aaaaaaaaaaa"]
    assert used["videos"] == 1


def test_the_plans_limits_and_why_a_video_is_left_out(hearing, monkeypatch):
    monkeypatch.setattr(listen, "MAX_VIDEOS", 2)
    todo = [("long0000000", info("long0000000", minutes=150), "none"),        # longer than MAX_MINUTES
            ("b0000000001", info("b0000000001", minutes=50), "short"),         # only its first 45 minutes
            ("b0000000002", info("b0000000002"), "none"),
            ("b0000000003", info("b0000000003"), "none")]                     # the plan's limit of videos
    used = {}
    out = listen.listen(FakeJobs(FakeEngine()), Settings(), todo, used, ("en",), threading.Event())
    assert {v: r.get("skipped", "heard") for v, r in out.items()} == {
        "long0000000": "too_long", "b0000000001": "heard", "b0000000002": "heard", "b0000000003": "limit"}
    assert used["sound_s"] == 45 * 60 + 10 * 60 and hearing["downloads"] == ["b0000000001", "b0000000002"]
    assert videos.heard("b0000000001")["upto"] == 45 * 60 and videos.heard("b0000000002")["upto"] == 0
    assert listen.reason_text("limit") == "the plan listens to at most 2 videos"
    assert listen.reason_text("too_long", english=True) == "it is longer than 120 minutes"
    # the plan's time for listening: what it used before counts
    used = {"videos": 0, "sound_s": 0, "wall_s": listen.MAX_WALL_S}
    out = listen.listen(FakeJobs(FakeEngine()), Settings(), [("c0000000001", info("c0000000001"), "none")], used,
                        ("en",), threading.Event())
    assert out["c0000000001"] == {"skipped": "time"}


def test_lectures_go_first(hearing):
    # lectures transcribing or waiting: nothing is downloaded at all
    out = listen.listen(FakeJobs(FakeEngine(), busy=True), Settings(), [("d0000000001", info("d0000000001"), "none")],
                        {}, ("en",), threading.Event())
    assert out == {"d0000000001": {"skipped": "lectures"}} and hearing["downloads"] == []
    # one queued while a video is being transcribed: that one stops, and so do the ones after it
    jobs = FakeJobs(None)
    jobs.engine = FakeEngine(on_batch=lambda: setattr(jobs, "lectures", True))
    out = listen.listen(jobs, Settings(), [("e0000000001", info("e0000000001"), "none"),
                                           ("e0000000002", info("e0000000002"), "none")], {}, ("en",), threading.Event())
    assert out == {"e0000000001": {"skipped": "lectures"}, "e0000000002": {"skipped": "lectures"}}
    assert videos.heard("e0000000001") is None and hearing["downloads"] == ["e0000000001"]


def test_no_speech_model_youtube_refusing_and_stopping(hearing, monkeypatch):
    monkeypatch.setattr(listen, "speech_choice", lambda settings: ("", "", "", "no_model"))
    out = listen.listen(FakeJobs(FakeEngine()), Settings(), [("f0000000001", info("f0000000001"), "none")], {}, ("en",),
                        threading.Event())
    assert out == {"f0000000001": {"skipped": "no_model"}} and hearing["downloads"] == []
    monkeypatch.setattr(listen, "speech_choice", lambda settings: ("small", "cpu", "cpu", ""))
    hearing["refuse"] = {"g0000000001": VideoError("This video is private.", "private"),
                         "g0000000002": VideoError("YouTube asked to confirm you're not a bot.", "bot")}
    todo = [(v, info(v), "none") for v in ("g0000000001", "g0000000002", "g0000000003")]
    out = listen.listen(FakeJobs(FakeEngine()), Settings(), todo, {}, ("en",), threading.Event())
    assert [r["skipped"] for r in out.values()] == ["refused", "refused", "refused"]    # a bot check: no more tries
    assert hearing["downloads"] == []
    from studia.quality import NoSpeech

    out = listen.listen(FakeJobs(FakeEngine(fail=NoSpeech("silent"))), Settings(), [("h0000000001", info("h0000000001"), "none")],
                        {}, ("en",), threading.Event())
    assert out == {"h0000000001": {"skipped": "no_speech"}}
    stop = threading.Event()
    stop.set()
    with pytest.raises(Cancelled):                                # the plan stopped: the plan stops
        listen.listen(FakeJobs(FakeEngine()), Settings(), [("i0000000001", info("i0000000001"), "none")], {}, ("en",), stop)


def test_which_speech_model_and_languages(monkeypatch):
    from studia.engine import models

    have = {"large-v3"}
    monkeypatch.setattr(models, "model_is_downloaded", lambda m: m in have)
    monkeypatch.setattr(models, "resolve_device", lambda pref: ("cuda", "float16"))
    monkeypatch.setattr(listen, "_ai_on_card", lambda: False)
    s = Settings(model="large-v3", device="auto")
    assert listen.speech_choice(s) == ("large-v3", "auto", "cuda", "")       # the lectures' own, on the card
    monkeypatch.setattr(listen, "_ai_on_card", lambda: True)                  # the offline AI is on the card
    assert listen.speech_choice(s) == ("", "", "", "no_model")
    have.add("small")
    assert listen.speech_choice(s) == ("small", "cpu", "cpu", "")             # the small model on the processor
    monkeypatch.setattr(models, "resolve_device", lambda pref: ("cpu", "int8"))
    assert listen.speech_choice(Settings(model="small")) == ("small", "auto", "cpu", "")   # the lectures' own engine
    assert listen.languages_for(types.SimpleNamespace(language="en-US"), ("en", "tr")) == ("en",)
    assert listen.languages_for(types.SimpleNamespace(language="tr"), ("en", "tr")) == ("tr", "en")
    assert listen.languages_for(types.SimpleNamespace(language=""), ("en", "tr")) == ("tr", "en")
    assert listen.languages_for(types.SimpleNamespace(language=""), ("en",)) == ("tr", "en")     # the model tells
    assert listen.languages_for(types.SimpleNamespace(language="de"), ("tr",)) == ("tr",)


# ------------------------------------------------------------------ the lectures' queue lends its speech model
class Engine:
    def __init__(self, settings):
        self.key, self.unloaded, self.loaded = (settings.model, settings.device), 0, True

    def unload(self):
        self.unloaded += 1


def test_the_queue_lends_its_speech_model_only_while_no_lecture_needs_it(tmp_path):
    from studia.library import Library

    made = []
    settings = Settings(model="large-v3", device="auto")
    jm = jobs_mod.JobManager(Library(tmp_path / "lib.db"), None, lambda: settings,
                             lambda s: made.append(Engine(s)) or made[-1], lambda e: None, lambda d: {}, lambda *a: [])
    with jm.lend_engine("large-v3", "auto") as e:                 # the lectures' own: kept for them
        assert e is jm._engine and jm._engine_users == 1
        assert jm.unload_engine() is False                        # not freed while a plan listens with it
    assert jm._engine_users == 0 and jm._engine is e and e.unloaded == 0
    with jm.lend_engine("small", "cpu") as other:                  # another model: one of its own, freed afterwards
        assert other is not e and other.key == ("small", "cpu")
    assert other.unloaded == 1 and jm._engine is e
    jm.setup_busy = lambda: True                                   # the setup still downloads: nothing is lent
    with pytest.raises(jobs_mod.EngineBusy) as e:
        with jm.lend_engine("large-v3", "auto"):
            pass
    assert e.value.why == "setup"
    jm._jobs["lecture"] = object()                                 # a lecture waits: nothing is lent
    with pytest.raises(jobs_mod.EngineBusy) as e:
        with jm.lend_engine("large-v3", "auto"):
            pass
    assert e.value.why == "lectures" and jm.busy()


def test_the_engine_transcribes_a_recording_in_the_languages_asked_for():
    import numpy as np

    from studia.engine import Engine as RealEngine
    from studia.engine.core import SR, Decoded, Unit

    class Decoder:
        def __init__(self):
            self.passes = []

        def features(self, audio, batch):
            return [u.start for u in batch]

        def encode(self, feats):
            return feats

        def generate(self, enc, batch, langs, opts):
            self.passes.append(langs[0])
            return [Decoded(langs[0], "We standardize it here.", -0.2, 0.0, 1.3, 8) for _ in batch]

    e = RealEngine("small", languages=("en", "tr"))
    e._device, e._decoder = "cpu", Decoder()
    units = [Unit(0, 2 * SR)]
    e._mine.languages = ("en",)                                     # (what transcribe(languages=…) sets)
    e._decode_units(np.zeros(3 * SR, dtype=np.float32), units, lambda *a: None, None)
    assert e._decoder.passes == ["en"]
    e._mine.languages = e.languages
    e._decoder.passes = []
    e._decode_units(np.zeros(3 * SR, dtype=np.float32), units, lambda *a: None, None)
    assert e._decoder.passes == ["en", "tr"]                        # a lecture: both, as before


# ------------------------------------------------------------------ fx-330: what the checks found
def test_on_the_processor_a_long_video_is_listened_to_through_its_start(hearing, monkeypatch):
    """On the processor every video over 40 minutes was left out ("the plan's time for listening ran out"), before
    anything was listened to: its 45 minutes were more than all the sound the processor gets."""
    monkeypatch.setattr(listen, "speech_choice", lambda settings: ("small", "cpu", "cpu", ""))
    used = {}
    out = listen.listen(FakeJobs(FakeEngine()), Settings(), [("cpu00000001", info("cpu00000001", minutes=50), "none")],
                        used, ("en",), threading.Event())
    assert out["cpu00000001"]["heard"] and hearing["upto"] == [listen.LISTEN_CPU_S]
    assert used["sound_s"] == listen.LISTEN_CPU_S and videos.heard("cpu00000001")["upto"] == listen.LISTEN_CPU_S


def test_a_video_whose_language_youtube_doesnt_say_is_heard_in_its_own(hearing):
    """"Karnaugh (Karno) Haritaları" (Turkish, no captions, no language from YouTube) in an English plan was
    transcribed in English only: large-v3 translated it, it was kept as English for 180 days, and the plan could
    recommend a Turkish lecture as checked in what it says."""
    class Turkish(FakeEngine):
        def transcribe(self, audio, progress, cancel, meta=None, languages=None):
            tr = super().transcribe(audio, progress, cancel, meta, languages)
            tr.language_seconds = {"tr": 8.0}
            return tr

    engine = Turkish(text="Merhaba arkadaşlar, bugün Karnaugh haritalarını anlatacağım ve örnek çözeceğiz. " * 4)
    out = listen.listen(FakeJobs(engine), Settings(), [("karno000001", info("karno000001", language=""), "none")], {},
                        ("en",), threading.Event())
    assert out["karno000001"]["heard"] and engine.calls == [("tr", "en")]       # not forced into English
    assert videos.heard("karno000001")["lang"] == "tr"
    got = videos.with_heard(VideoInfo("karno000001", "Karnaugh (Karno) Haritaları", duration_s=600))
    assert got.caption_lang == "tr" and not videos.in_languages(got, ("en",))      # another language for this plan
    assert videos.in_languages(got, ("tr",))


def test_a_cached_video_comes_with_what_was_heard_in_it(monkeypatch):
    """The plan reads a listened video again through inspect(), from its cache: what was heard must come with it."""
    vid = "cache000001"
    base = VideoInfo(vid, "No captions", duration_s=300)
    videos._write_json(videos._info_path(vid), {**base.to_json(), "langs": ["en"]})
    monkeypatch.setattr(videos, "_ydl", lambda **kw: (_ for _ in ()).throw(AssertionError("asked YouTube")))
    assert not videos.inspect(vid).has_captions
    videos.remember_heard(vid, [Cue(0, 4, "Today: the normal distribution.")], "en", model="large-v3")
    got = videos.inspect(vid)
    assert got.caption_kind == videos.SPEECH and [c.text for c in got.cues] == ["Today: the normal distribution."]


def test_the_original_track_is_taken_even_when_a_dub_is_smaller():
    fmts = [{"format_id": "139-0", "acodec": "mp4a.40.5", "vcodec": "none", "filesize": 900000, "abr": 30,
             "format_note": "Arabic, low", "language_preference": -1, "protocol": "https"},           # the smallest
            {"format_id": "249-7", "acodec": "opus", "vcodec": "none", "filesize": 2128204, "abr": 46.7,
             "format_note": "English (US) original (default), low", "language_preference": 10, "protocol": "https"}]
    assert [f["format_id"] for f in videos._original_audio({"formats": fmts})] == ["249-7"]


def test_transcribe_decodes_in_the_languages_it_is_given(monkeypatch):
    import numpy as np

    from studia.engine import Engine as RealEngine

    e = RealEngine("small", languages=("en", "tr"))
    e._wm, e._decoder = types.SimpleNamespace(supported_languages=["en", "tr", "de"]), object()
    seen = []

    class Stop(Exception):
        pass

    def decode(audio, units, progress, cancel, resume):
        seen.append(e._languages())
        raise Stop

    monkeypatch.setattr(e, "_decode_units", decode)
    speech = types.SimpleNamespace(units=[], warnings=[], stats={}, seconds=0.0, gain=1.0)
    for langs in (("tr", "en"), ("en",), None):
        with pytest.raises(Stop):
            e.transcribe(np.zeros(16000, dtype=np.float32), lambda *a: None, None, {}, speech, languages=langs)
    assert seen == [("tr", "en"), ("en",), ("en", "tr")]          # (none given: the engine's own, as for a lecture)
