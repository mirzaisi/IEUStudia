"""YouTube videos for video study plans (studia.videos): search, caption choice and parsing, the cache, errors,
caption windows and ranking - against a fake yt-dlp, never the network."""

import copy
import dataclasses as dc
import importlib
import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest
import requests
import yt_dlp
from yt_dlp.utils import DownloadError

from studia import videos
from studia.paths import app_dir
from studia.videos import (Chapter, Cue, VideoError, VideoHit, VideoInfo, caption_text, fmt_ts, inspect,
                           inspect_many, rank, search, search_many, watch_url, windows)


# ------------------------------------------------------------------ a fake yt-dlp
class FakeYouTube:
    """Stands in for yt_dlp.YoutubeDL: answers extract_info / urlopen from dicts and records every call."""

    def __init__(self) -> None:
        self.infos: dict = {}        # URL -> info dict, a callable returning one, or an exception to raise
        self.files: dict = {}        # caption URL -> payload, or an exception to raise
        self.calls: list = []        # extract_info URLs
        self.kwargs: list = []
        self.fetched: list = []      # urlopen URLs
        self.params: list = []

    def ydl_class(self):
        yt = self

        class FakeYDL:
            def __init__(self, params=None):
                yt.params.append(params or {})

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def extract_info(self, url, download=True, process=True, **kwargs):
                yt.calls.append(url)
                yt.kwargs.append({"download": download, "process": process})
                answer = yt.infos.get(url)
                answer = answer() if callable(answer) else answer
                if isinstance(answer, BaseException):
                    raise answer
                if answer is None:
                    raise DownloadError(f"ERROR: [youtube] {url}: Video unavailable. This video is unavailable")
                return copy.deepcopy(answer)

            def urlopen(self, url):
                yt.fetched.append(url)
                body = yt.files.get(url)
                if isinstance(body, BaseException):
                    raise body
                if body is None:
                    raise OSError(f"HTTP Error 404: Not Found ({url})")
                return FakeResponse(body.encode("utf-8") if isinstance(body, str) else body)

        return FakeYDL


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def read(self) -> bytes:
        return self.body

    def close(self) -> None:
        pass


def no_network(*args, **kwargs):
    raise AssertionError("the network must not be used")


@pytest.fixture
def yt(monkeypatch):
    fake = FakeYouTube()
    monkeypatch.setattr(yt_dlp, "YoutubeDL", fake.ydl_class())
    monkeypatch.setattr(videos.requests, "get", no_network)
    return fake


VID = "bayes000001"


def url_of(vid: str) -> str:
    return f"https://www.youtube.com/watch?v={vid}"


def track(vid: str, lang: str, *exts: str, tlang: str = "", asr: bool = False) -> list:
    """yt-dlp's formats of one caption track (timedtext URLs like YouTube's; translations carry tlang)."""
    q = f"v={vid}&lang={lang}" + ("&kind=asr" if asr else "") + (f"&tlang={tlang}" if tlang else "")
    return [{"ext": e, "url": f"https://www.youtube.com/api/timedtext?{q}&fmt={e}", "name": lang}
            for e in exts or ("json3", "srv1", "vtt")]


def raw_video(vid: str = VID, *, subs=None, auto=None, chapters=None, audio_lang=None, title=None, **extra) -> dict:
    """What yt-dlp's extract_info(process=False) returns for a watch page (the parts used)."""
    formats = [{"format_id": "251", "acodec": "opus", "language": audio_lang, "language_preference": 10}] if audio_lang else []
    return {"id": vid, "title": title or "Bayes theorem, the geometry of changing beliefs", "channel": "3Blue1Brown",
            "channel_id": "UCYO_jab_esuFRV4b17AJtAw", "duration": 911, "view_count": 5967640, "upload_date": "20191222",
            "description": "Perhaps the most important formula in probability.", "subtitles": subs or {},
            "automatic_captions": auto or {}, "chapters": chapters, "formats": formats, **extra}


def json3(*events) -> str:
    """A json3 caption file from (start_s, duration_s, text) events; text None: a layout-only event."""
    out = []
    for start, dur, text in events:
        ev = {"tStartMs": round(start * 1000), "dDurationMs": round(dur * 1000)}
        if text is not None:
            ev["segs"] = [{"utf8": text}]
        out.append(ev)
    return json.dumps({"wireMagic": "pb3", "pens": [{}], "events": out})


# automatic captions as YouTube sends them: a window event, word segments, newline-only "append" events and
# lines that stay on screen ~2 s into the next one
ASR_JSON3 = json.dumps({"wireMagic": "pb3", "events": [
    {"tStartMs": 0, "dDurationMs": 10100, "id": 1, "wpWinPosId": 1, "wsWinStyleId": 1},
    {"tStartMs": 160, "dDurationMs": 4880, "wWinId": 1, "segs": [
        {"utf8": "today", "acAsrConf": 0}, {"utf8": " we", "tOffsetMs": 320}, {"utf8": " look", "tOffsetMs": 500},
        {"utf8": " at", "tOffsetMs": 700}, {"utf8": " bayes", "tOffsetMs": 900}, {"utf8": " theorem", "tOffsetMs": 1200}]},
    {"tStartMs": 2390, "dDurationMs": 2650, "wWinId": 1, "aAppend": 1, "segs": [{"utf8": "\n"}]},
    {"tStartMs": 2400, "dDurationMs": 4000, "wWinId": 1, "segs": [
        {"utf8": "and"}, {"utf8": " conditional", "tOffsetMs": 300}, {"utf8": " probability", "tOffsetMs": 800}]},
    {"tStartMs": 6400, "dDurationMs": 600, "wWinId": 1, "segs": [{"utf8": "okay"}]},
    {"tStartMs": 7100, "dDurationMs": 3000, "wWinId": 1, "segs": [{"utf8": "so let's start with an example"}]},
]})

# YouTube's automatic WebVTT: two rolling lines, inline word timings, 10 ms transition cues, " " lines in cues
AUTO_VTT = """WEBVTT
Kind: captions
Language: en

00:00:00.160 --> 00:00:02.389 align:start position:0%

today<00:00:00.320><c> we</c><00:00:00.640><c> look</c><00:00:01.000><c> at</c><00:00:01.400><c> bayes</c>

00:00:02.389 --> 00:00:02.399 align:start position:0%
today we look at bayes


00:00:02.399 --> 00:00:05.000 align:start position:0%
today we look at bayes
theorem<00:00:02.800><c> and</c><00:00:03.000><c> conditional</c><00:00:03.500><c> probability</c>

00:00:05.000 --> 00:00:05.010 align:start position:0%
theorem and conditional probability


00:00:05.010 --> 00:00:08.000 align:start position:0%
theorem and conditional probability
with &amp; without &lt;priors&gt;
"""


def cues_of(info: VideoInfo) -> list:
    return [(c.start, c.end, c.text) for c in info.cues]


def cache_file(vid: str = VID) -> Path:
    return app_dir() / "cache" / "videos" / f"{vid}.json"


# ------------------------------------------------------------------ search
def entry(vid, title, duration=600, **kw) -> dict:
    """One flat results-page entry, as yt-dlp's extract_flat gives it."""
    return {"_type": "url", "ie_key": "Youtube", "id": vid, "url": url_of(vid), "title": title,
            "channel": "Dr. Trefor Bazett", "duration": duration, "view_count": 2060069, "upload_date": None,
            "description": "Bayes' theorem   in\n5 minutes", **kw}


def results(*entries) -> dict:
    return {"_type": "playlist", "id": "q", "entries": list(entries)}


def test_search_normalises_filters_and_caches(yt):
    q = "bayes theorem conditional probability"
    yt.infos[f"ytsearch8:{q}"] = results(
        entry("XQoLVl31ZfQ", "Bayes' Theorem - The Simplest Case", 331),
        entry("liveliveliv", "LIVE: probability help", None, live_status="is_live"),
        entry("upcomingvid", "Premiere: Bayes", None, live_status="is_upcoming"),
        entry("shortshort1", "Bayes in 30 seconds", 30),
        {**entry("shortsurl01", "Bayes #shorts", None), "url": "https://www.youtube.com/shorts/shortsurl01"},
        entry("membersonly", "Members: Bayes", 900, availability="subscriber_only"),
        {**entry("noidnoidnoi", "no id"), "id": None},
        {"_type": "url", "ie_key": "YoutubeTab", "id": "UC9rTsvTxJnx1DNrDA3Rqa6A", "title": "a channel",
         "url": "https://www.youtube.com/channel/UC9rTsvTxJnx1DNrDA3Rqa6A"},
        entry("HZGCoVF3YvM", "Bayes theorem, the geometry of changing beliefs", 911, channel="3Blue1Brown",
              view_count=None),
        entry("XQoLVl31ZfQ", "the same video again", 331),
    )
    hits = search(f"  {q} ")
    assert [h.id for h in hits] == ["XQoLVl31ZfQ", "HZGCoVF3YvM"]
    h = hits[0]
    assert (h.title, h.channel, h.duration_s, h.view_count, h.upload_date, h.query) == (
        "Bayes' Theorem - The Simplest Case", "Dr. Trefor Bazett", 331.0, 2060069, "", q)
    assert h.url == "https://www.youtube.com/watch?v=XQoLVl31ZfQ" and h.description == "Bayes' theorem in 5 minutes"
    assert hits[1].view_count is None
    assert yt.calls == [f"ytsearch8:{q}"]
    assert yt.params[0]["extract_flat"] and yt.params[0]["quiet"] and yt.params[0]["socket_timeout"] == 20

    # cached for a week: the same search (any case or spacing, the same or a smaller limit) asks YouTube nothing
    cached = list((app_dir() / "cache" / "videos" / "search").glob("*.json"))
    assert len(cached) == 1
    assert [h.id for h in search(q.upper(), 6)] == ["XQoLVl31ZfQ", "HZGCoVF3YvM"]
    assert yt.calls == [f"ytsearch8:{q}"]
    old = time.time() - 8 * 86400                   # ... but a week later it searches again
    os.utime(cached[0], (old, old))
    search(q)
    assert yt.calls == [f"ytsearch8:{q}"] * 2
    assert search("   ") == [] and len(yt.calls) == 2


def test_search_many_keeps_every_query_apart(yt):
    yt.infos["ytsearch8:bayes"] = results(entry("XQoLVl31ZfQ", "Bayes' Theorem", 331))
    yt.infos["ytsearch8:broken"] = DownloadError(
        "ERROR: Unable to download API page: <urlopen error [Errno 11001] getaddrinfo failed>")
    seen = []
    out = search_many(["bayes", "broken", "bayes", ""], workers=2, progress=lambda *a: seen.append(a))
    assert list(out) == ["bayes", "broken", ""]
    assert [h.id for h in out["bayes"]] == ["XQoLVl31ZfQ"] and out[""] == []
    assert isinstance(out["broken"], VideoError) and out["broken"].kind == "network"
    assert str(out["broken"]) == "Can't reach YouTube. Check your internet connection."
    assert sorted(a[0] for a in seen) == [1, 2, 3] and {a[1] for a in seen} == {3}


# ------------------------------------------------------------------ inspect: which captions
def test_inspect_prefers_human_english_captions_and_parses_json3(yt):
    subs = {"en": track(VID, "en"), "fr": track(VID, "fr"),
            "live_chat": [{"ext": "json", "url": url_of(VID), "protocol": "youtube_live_chat_replay"}]}
    auto = {"en-orig": track(VID, "en", asr=True), "en": track(VID, "en", asr=True),
            "de": track(VID, "en", asr=True, tlang="de")}
    yt.infos[url_of(VID)] = raw_video(subs=subs, auto=auto, audio_lang="en-US", chapters=[
        {"start_time": 0, "title": "Intro example"}, {"start_time": 249, "title": "Generalizing as a formula"}])
    yt.files[subs["en"][0]["url"]] = json3(
        (0, 3.369, "The goal is for you to come away\nfrom this video"), (3.369, 3.471, None), (3.4, 0.5, "\n"),
        (7.48, 2.786, "understanding  Bayes' theorem."))
    info = inspect(VID)
    assert (info.caption_kind, info.caption_lang, info.language) == ("manual", "en", "en-US")
    assert cues_of(info) == [(0.0, 3.369, "The goal is for you to come away from this video"),
                             (7.48, 10.266, "understanding Bayes' theorem.")]
    assert yt.fetched == [subs["en"][0]["url"]]                  # json3, and nothing else
    assert yt.kwargs == [{"download": False, "process": False}]  # never downloads, no format selection
    assert [(c.start, c.end, c.title) for c in info.chapters] == [(0, 249, "Intro example"),
                                                                   (249, 911, "Generalizing as a formula")]
    assert (info.title, info.channel, info.channel_id, info.duration_s, info.view_count, info.upload_date) == (
        "Bayes theorem, the geometry of changing beliefs", "3Blue1Brown", "UCYO_jab_esuFRV4b17AJtAw", 911.0, 5967640,
        "20191222")
    assert info.url == url_of(VID) and info.has_captions and info.fetched_at
    assert caption_text(info) == "The goal is for you to come away from this video understanding Bayes' theorem."
    assert caption_text(info, 5, 20) == "understanding Bayes' theorem."
    assert videos.stamped_text(info) == ("[0:00] The goal is for you to come away from this video "
                                         "understanding Bayes' theorem.")
    assert videos.stamped_text(info, every_s=5) == ("[0:00] The goal is for you to come away from this video "
                                                    "[0:07] understanding Bayes' theorem.")
    assert videos.stamped_part(info, limit=60) == {"start": 0.0, "end": 3.37,          # whole cues; the real end
                                                  "text": "[0:00] The goal is for you to come away from this video"}

    # no exact "en": a regional variant beats the original automatic track; preferred languages go in order
    vid = "variant0001"
    yt.infos[url_of(vid)] = raw_video(vid, subs={"de": track(vid, "de"), "en-GB": track(vid, "en-GB"),
                                                 "tr": track(vid, "tr")}, auto={"en-orig": track(vid, "en", asr=True)})
    yt.files[track(vid, "en-GB")[0]["url"]] = json3((0, 2, "colour and flavour of probability"))
    yt.files[track(vid, "tr")[0]["url"]] = json3((0, 2, "olasılığın rengi ve tadı"))
    assert inspect(vid).caption_lang == "en-GB"
    assert (inspect(vid, langs=("tr", "en")).caption_lang, inspect(vid, langs=("tr", "en")).caption_kind) == ("tr", "manual")


def test_inspect_uses_the_original_automatic_track_never_a_translation(yt):
    auto = {"af": track(VID, "en", asr=True, tlang="af"), "en": track(VID, "en", asr=True),
            "en-orig": track(VID, "en", asr=True), "tr": track(VID, "en", asr=True, tlang="tr")}
    yt.infos[url_of(VID)] = raw_video(subs={"fr": track(VID, "fr")}, auto=auto)   # French: not wanted, not its language
    yt.files[auto["en-orig"][0]["url"]] = ASR_JSON3
    info = inspect(VID)
    assert (info.caption_kind, info.caption_lang, info.language) == ("auto", "en", "en")
    assert cues_of(info) == [(0.16, 2.4, "today we look at bayes theorem"),        # clipped at the next line
                             (2.4, 7.0, "and conditional probability okay"),       # "okay" joined its neighbour
                             (7.1, 10.1, "so let's start with an example")]
    assert yt.fetched == [auto["en-orig"][0]["url"]]


def test_inspect_falls_back_to_the_videos_own_language(yt):
    vid = "turkish0001"
    auto = {"en": track(vid, "tr", asr=True, tlang="en"), "tr-orig": track(vid, "tr", asr=True),
            "tr": track(vid, "tr", asr=True)}
    yt.infos[url_of(vid)] = raw_video(vid, auto=auto, audio_lang="tr", title="Koşullu olasılık")
    yt.files[auto["tr-orig"][0]["url"]] = json3((0, 4, "koşullu olasılık ve Bayes teoremi"))
    info = inspect(vid)                     # English exists only as a machine translation: never used
    assert (info.caption_kind, info.caption_lang, info.language) == ("auto", "tr", "tr")
    assert all("tlang" not in u for u in yt.fetched)


def test_inspect_without_usable_captions(yt):
    vid = "nocaptions1"
    yt.infos[url_of(vid)] = raw_video(vid, auto={"en": track(vid, "xx", asr=True, tlang="en")})
    info = inspect(vid)
    assert (info.caption_kind, info.caption_lang, info.cues, info.has_captions) == ("", "", [], False)
    assert yt.fetched == [] and windows(info, ["bayes"]) == [] and caption_text(info) == ""
    inspect(vid)                                         # remembered too, but only for a few days
    assert len(yt.calls) == 1
    old = time.time() - 4 * 86400
    os.utime(cache_file(vid), (old, old))
    inspect(vid)
    assert len(yt.calls) == 2


# ------------------------------------------------------------------ inspect: caption formats
def test_inspect_vtt_fallback_through_requests(yt, monkeypatch):
    auto = {"en-orig": track(VID, "en", "vtt", asr=True)}               # no json3 offered
    url = auto["en-orig"][0]["url"]
    yt.infos[url_of(VID)] = raw_video(auto=auto)
    yt.files[url] = OSError("unsupported handler")                     # yt-dlp's HTTP stack fails ...
    got = []

    class Resp:
        content = AUTO_VTT.encode("utf-8")

        def raise_for_status(self):
            pass

    monkeypatch.setattr(videos.requests, "get", lambda u, **kw: got.append(u) or Resp())   # ... plain requests works
    info = inspect(VID)
    assert got == [url]
    assert cues_of(info) == [(0.16, 2.389, "today we look at bayes"), (2.399, 5.0, "theorem and conditional probability"),
                             (5.01, 8.0, "with & without <priors>")]


def test_xml_and_srt_caption_formats():
    srv1 = ('<?xml version="1.0" encoding="utf-8" ?><transcript><text start="0" dur="3.369">of all of probability, '
            'Bayes&amp;#39; theorem.</text><text start="7.48" dur="2.786">it&amp;#39;s central to scientific discovery'
            '</text></transcript>')
    ttml = ('<?xml version="1.0" encoding="utf-8" ?><tt xml:lang="en" xmlns="http://www.w3.org/ns/ttml"><head><styling>'
            '<style xml:id="s1"/></styling></head><body><div><p begin="00:00:00.000" end="00:00:03.369" style="s2">'
            'of all of probability,<br/>Bayes&#39; theorem.</p></div></body></tt>')
    srv3 = ('<?xml version="1.0" encoding="utf-8" ?><timedtext format="3"><body><p t="0" d="3369" w="1"><s ac="0">'
            'of all</s><s t="200" ac="0"> of probability, Bayes theorem</s></p><p t="3369" d="10" w="1" a="1">\n</p>'
            '</body></timedtext>')
    srt = "1\n00:00:00,000 --> 00:00:03,369\nof all of probability,\nBayes' theorem.\n\n2\n00:00:07,480 --> 00:00:10,266\nnext\n"
    parse = videos._parse_captions
    assert [(c.start, c.end, c.text) for c in parse(srv1)] == [
        (0.0, 3.369, "of all of probability, Bayes' theorem."), (7.48, 10.266, "it's central to scientific discovery")]
    assert [(c.start, c.end, c.text) for c in parse(ttml)] == [(0.0, 3.369, "of all of probability, Bayes' theorem.")]
    assert [(c.start, c.end, c.text) for c in parse(srv3)] == [(0.0, 3.369, "of all of probability, Bayes theorem")]
    assert [c.text for c in parse(srt)] == ["of all of probability, Bayes' theorem.", "next"]
    assert parse("<!DOCTYPE html><html><body><p>Sorry, something went wrong</p></body></html>") == []
    assert parse("") == [] and parse("#EXTM3U\n") == [] and parse("{not json") == []


def test_inspect_hls_only_tracks(yt, monkeypatch):
    def hls_video(vid):
        playlist = f"https://manifest.googlevideo.com/api/manifest/hls_timedtext_playlist/id/{vid}/file/index.m3u8"
        seg = f"https://www.youtube.com/api/timedtext?caps=asr&v={vid}&lang=en&fmt=vtt&range=0-600"
        yt.infos[url_of(vid)] = raw_video(vid, auto={"en": [{"ext": "vtt", "protocol": "m3u8_native", "url": playlist}]},
                                          audio_lang="en")
        yt.files[playlist] = (f"#EXTM3U\n#EXT-X-VERSION:3\n#EXTINF:600,\n{seg}\n#EXTINF:310,\n"
                              f"{seg.replace('0-600', '600-310')}\n#EXT-X-ENDLIST\n")
        return seg, f"https://www.youtube.com/api/timedtext?caps=asr&v={vid}&lang=en&fmt=json3"

    # the segments are timedtext URLs: without their range, one json3 file is the whole track
    _, whole = hls_video(VID)
    yt.files[whole] = json3((0, 3, "the whole track in one json3 file"))
    info = inspect(VID)
    assert (info.caption_kind, [c.text for c in info.cues]) == ("auto", ["the whole track in one json3 file"])

    # else the WebVTT segments are joined (their times are absolute; the cue on the boundary comes twice)
    seg, _ = hls_video("hlssegments")
    yt.files[seg] = ("WEBVTT\n\n00:00:00.000 --> 00:00:05.000\nfirst part of the lecture\n\n"
                     "00:09:58.262 --> 00:10:00.520\nand you can read it geometrically\n")
    yt.files[seg.replace("0-600", "600-310")] = ("WEBVTT\n\n00:09:58.262 --> 00:10:00.520\nand you can read it "
                                                 "geometrically\n\n00:10:00.900 --> 00:10:04.940\np of h times p of e given h\n")

    def offline(*a, **kw):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(videos.requests, "get", offline)
    assert cues_of(inspect("hlssegments")) == [(0.0, 5.0, "first part of the lecture"),
                                               (598.262, 600.52, "and you can read it geometrically"),
                                               (600.9, 604.94, "p of h times p of e given h")]


# ------------------------------------------------------------------ inspect: cache, errors, cancelling
def test_inspect_cache_hit_needs_no_network(yt, monkeypatch):
    yt.infos[url_of(VID)] = raw_video(subs={"en": track(VID, "en")})
    yt.files[track(VID, "en")[0]["url"]] = json3((0, 3, "captions worth keeping"))
    first = inspect(VID)
    assert cache_file().is_file() and not list(cache_file().parent.glob("*.tmp"))

    class Offline:
        def __init__(self, *args, **kwargs):
            raise AssertionError("YouTube must not be asked")

    monkeypatch.setattr(yt_dlp, "YoutubeDL", Offline)
    assert inspect(f"https://youtu.be/{VID}?si=x") == first
    assert inspect(VID, langs=("en", "tr")) == first        # already in the most wanted language
    with pytest.raises(AssertionError):
        inspect(VID, langs=("tr", "en"))                     # Turkish is wanted first: asks again
    old = time.time() - 31 * 86400
    os.utime(cache_file(), (old, old))
    with pytest.raises(AssertionError):
        inspect(VID)                                         # older than 30 days
    with pytest.raises(VideoError):
        inspect("../../evil")                                # never a path outside the cache


@pytest.mark.parametrize("text, kind, message", [
    ("Private video. Sign in if you've been granted access to this video", "private", "This video is private."),
    ("Sign in to confirm you’re not a bot. Use --cookies-from-browser or --cookies for the authentication.", "bot",
     "YouTube asked to confirm you're not a bot. Try again later."),
    ("Sign in to confirm your age. This video may be inappropriate for some users.", "age",
     "This video is age-restricted."),
    ("Join this channel to get access to members-only content like this video, and other exclusive perks.",
     "members", "This video is for the channel's members only."),
    ("Video unavailable. This video has been removed by the uploader", "unavailable", "This video is unavailable."),
    ("This content isn't available, try again later. The current session has been rate-limited by YouTube for up "
     "to an hour.", "rate_limit", "YouTube is limiting requests right now. Try again in a few minutes."),
    ("Unable to download webpage: <urlopen error [Errno 11001] getaddrinfo failed> (caused by TransportError())",
     "network", "Can't reach YouTube. Check your internet connection."),
    ("Unable to download API page: The read operation timed out (caused by TransportError('timed out'))", "timeout",
     "YouTube took too long to answer. Try again."),
    ("Something nobody has seen before", "error", "Couldn't read this video from YouTube."),
])
def test_inspect_errors_are_short_and_friendly(yt, text, kind, message):
    yt.infos[url_of(VID)] = DownloadError(f"ERROR: [youtube] {VID}: {text}")
    with pytest.raises(VideoError) as e:
        inspect(VID)
    assert (e.value.kind, str(e.value)) == (kind, message)
    assert not cache_file().exists()


def test_caption_download_failure_is_an_error_not_missing_captions(yt, monkeypatch):
    yt.infos[url_of(VID)] = raw_video(subs={"en": track(VID, "en")})
    yt.files[track(VID, "en")[0]["url"]] = OSError("HTTP Error 429: Too Many Requests")

    def throttled(*a, **kw):
        raise requests.HTTPError("429 Client Error: Too Many Requests")

    monkeypatch.setattr(videos.requests, "get", throttled)
    with pytest.raises(VideoError) as e:
        inspect(VID)
    assert e.value.kind == "rate_limit" and not cache_file().exists()


def test_cancel_stops_before_each_network_step(yt):
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(VideoError, match="Cancelled") as e:
        search("bayes", cancel=cancel)
    assert e.value.kind == "cancelled" and yt.calls == []

    later = threading.Event()

    def page():                              # cancelled while the watch page loads
        later.set()
        return raw_video(subs={"en": track(VID, "en")})

    yt.infos[url_of(VID)] = page
    with pytest.raises(VideoError, match="Cancelled"):
        inspect(VID, cancel=later)
    assert yt.calls == [url_of(VID)] and yt.fetched == [] and not cache_file().exists()
    out = inspect_many([VID, "otherid0001"], cancel=later)
    assert all(isinstance(v, VideoError) and v.kind == "cancelled" for v in out.values())


def test_inspect_many_isolates_failures(yt):
    good, private, other = "goodvideo01", "privatevid1", "othervideo1"
    for vid in (good, other):
        yt.infos[url_of(vid)] = raw_video(vid, subs={"en": track(vid, "en")})
        yt.files[track(vid, "en")[0]["url"]] = json3((0, 5, f"the captions of {vid} about bayes"))
    yt.infos[url_of(private)] = DownloadError(f"ERROR: [youtube] {private}: Private video. Sign in if you've been "
                                              f"granted access to this video")
    seen = []
    out = inspect_many([good, private, other, good], workers=3, progress=lambda *a: seen.append(a))
    assert list(out) == [good, private, other]
    assert isinstance(out[good], VideoInfo) and out[good].cues[0].text == f"the captions of {good} about bayes"
    assert isinstance(out[private], VideoError) and out[private].kind == "private"
    assert isinstance(out[other], VideoInfo) and out[other].has_captions
    assert sorted(a[0] for a in seen) == [1, 2, 3] and {a[1] for a in seen} == {3}
    assert {a[2] for a in seen} == {"Bayes theorem, the geometry of changing beliefs", private}


def test_a_bot_check_stops_the_requests_not_started_yet(yt):
    first, rest = "botchecked1", ["notasked001", "notasked002"]
    yt.infos[url_of(first)] = DownloadError(f"ERROR: [youtube] {first}: Sign in to confirm you’re not a bot.")
    out = inspect_many([first, *rest], workers=1)
    assert {k: v.kind for k, v in out.items()} == {first: "bot", rest[0]: "bot", rest[1]: "bot"}
    assert yt.calls == [url_of(first)]


# ------------------------------------------------------------------ windows and ranking
def lecture(texts, chapters=()) -> VideoInfo:
    """A captioned VideoInfo with one 10-second cue per text."""
    cues = [Cue(i * 10.0, i * 10.0 + 10, t) for i, t in enumerate(texts)]
    return VideoInfo("lecture0001", "Lecture", duration_s=len(cues) * 10.0, cues=cues,
                     chapters=[Chapter(*c) for c in chapters], caption_lang="en", caption_kind="manual")


def test_windows_find_merge_and_rank_the_parts_about_the_terms():
    texts = ["welcome everyone"] * 60
    texts[2] = "today: Bayes' theorem"
    texts[3] = "Bayes theorem again"
    texts[30] = "conditional probabilities first"
    texts[31] = "then Bayes’s theorem with a conditional"           # ... continued in the next cue:
    texts[32] = "probability"
    texts[50] = "theorems in general"                               # not "Bayes' theorem"
    info = lecture(texts)
    terms = ["Bayes' theorem", "conditional probability"]
    found = windows(info, terms, window_s=60)
    # 300 s: both terms (one phrase across two cues) + the coverage bonus; the window from 310 s merges into it.
    # 20 s: two mentions; the weaker overlapping window from 30 s merges into it.
    assert [(w["start"], w["end"], w["score"]) for w in found] == [(300, 370, 5.5), (20, 90, 3.0)]
    assert found[0]["text"].startswith("conditional probabilities first then Bayes’s theorem with a conditional "
                                       "probability welcome")
    assert windows(info, terms, window_s=60, top=1) == found[:1]

    # a chapter titled with a term: a bonus, and its start can open the window
    titled = dc.replace(info, chapters=[Chapter(0, 250, "Introduction"), Chapter(250, 450, "Conditional probability"),
                                        Chapter(450, 600, "Summary")])
    assert [(w["start"], w["end"], w["score"]) for w in windows(titled, terms, window_s=60)] == [
        (250, 370, 8.5), (20, 90, 3.0)]

    assert windows(info, ["eigenvalue"]) == [] and windows(info, []) == []
    assert windows(VideoInfo("nocaptions1", "No captions"), terms) == []
    long = lecture(["Bayes theorem " * 400])
    assert len(windows(long, terms)[0]["text"]) <= 2501


def test_term_matching_ignores_case_punctuation_plurals_and_possessives():
    info = lecture(["THEOREMS, theorems!", "Bayes’ rule", "bayes's rule", "the bayesian view"])
    assert [(w["start"], w["score"]) for w in windows(info, ["theorem"], window_s=10)] == [(0, 2.0)]
    assert [w["start"] for w in windows(info, ["Bayes' rule"], window_s=10)] == [10, 20]
    assert [w["start"] for w in windows(info, ["BAYES"], window_s=10)] == [10, 20]       # not "bayesian"


def test_rank_orders_by_title_length_views_and_channel():
    def hit(vid, title, channel="Some channel", minutes=10.0, views=None, description=""):
        return VideoHit(vid * 11, title, channel, minutes * 60, watch_url(vid * 11), views, "", description, "q")

    a = hit("a", "Bayes' Theorem explained", "3Blue1Brown", views=1_000_000)
    e = hit("e", "Bayes theorem explained!", views=1_000_000)                  # the same, but not a known channel
    b = hit("b", "Bayes theorem in 60 seconds", minutes=70 / 60, views=100_000)  # too short, and a teaser's title
    d = hit("d", "Bayes' theorem: full course", minutes=180, views=10_000)     # too long
    f = hit("f", "Theorem of Bayes")                                            # the words, not the phrase
    c = hit("c", "Probability lecture 7", minutes=20, views=1000, description="we cover Bayes' theorem today")
    g, h = hit("g", "Unrelated video", minutes=0), hit("h", "Unrelated video", minutes=0)
    ranked = rank([g, c, d, b, h, f, e, a], ["Bayes' theorem"])
    assert [x.id[0] for x in ranked] == ["a", "e", "d", "f", "b", "c", "g", "h"]   # ties keep their order
    assert rank([], ["x"]) == [] and [x.id[0] for x in rank([g, h], [])] == ["g", "h"]


def test_watch_url_and_fmt_ts():
    assert watch_url("HZGCoVF3YvM") == "https://www.youtube.com/watch?v=HZGCoVF3YvM"
    assert watch_url("HZGCoVF3YvM", 249.7) == "https://www.youtube.com/watch?v=HZGCoVF3YvM&t=249s"
    assert watch_url("HZGCoVF3YvM", 0) == "https://www.youtube.com/watch?v=HZGCoVF3YvM"
    assert [fmt_ts(s) for s in (0, 5, 75, 599.9, 3725, None, float("nan"))] == [
        "0:00", "0:05", "1:15", "9:59", "1:02:05", "0:00", "0:00"]


def test_json_round_trip():
    hit = VideoHit("XQoLVl31ZfQ", "Bayes", "Dr. Trefor Bazett", 331.0, watch_url("XQoLVl31ZfQ"), 5, "", "d", "q")
    assert VideoHit.from_json(json.loads(json.dumps(hit.to_json()))) == hit
    assert VideoHit.from_json({"id": "XQoLVl31ZfQ", "title": "t", "a_future_field": 1}).id == "XQoLVl31ZfQ"
    info = VideoInfo("HZGCoVF3YvM", "Bayes", chapters=[Chapter(0, 249, "Intro")], cues=[Cue(0, 3.369, "hi")],
                     caption_lang="en", caption_kind="manual")
    assert VideoInfo.from_json(json.loads(json.dumps({**info.to_json(), "langs": ["en"]}))) == info


def test_importing_the_module_does_not_import_yt_dlp(monkeypatch):
    import studia

    for name in [m for m in sys.modules if m == "yt_dlp" or m.startswith("yt_dlp.")]:
        monkeypatch.delitem(sys.modules, name)            # all restored after the test
    monkeypatch.delitem(sys.modules, "studia.videos")
    monkeypatch.setattr(studia, "videos", videos)
    importlib.import_module("studia.videos")              # app start-up: yt-dlp is only imported when needed
    assert "yt_dlp" not in sys.modules
