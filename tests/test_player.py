"""The lecture player's proxy (player.py) against a fake Panopto on 127.0.0.1: the player's Range requests pass through
(206 and its headers, the end of the file first as Chromium asks for a podcast's moov box), a redirect is followed once,
a refusal (403, the sign-in page) gets a fresh sign-in and address once, a connection the player drops ends quietly,
a range Panopto no longer has is answered at once, only tokens it handed out are served (not after they end), a file on
this PC plays with ranges, and Panopto's address never reaches the log. Then what plays (open_recording): the podcast
with picture, the sound-only one, the copy on this PC, Panopto's resume point, the screens, and plain words when it
can't play."""

import datetime as dt
import http.server
import json
import logging
import os
import socket
import threading
import time
import types
from pathlib import Path

import pytest
import requests

from studia import player, store
from studia.domain import Course, Recording, Segment, Transcript
from studia.library import Library
from studia.panopto import NeedPanoptoAuth
from studia.slides import Slide

BLOB = os.urandom(3 * 1024 * 1024 + 123)
SECRET = "InvocationID=5ec2e7-dont-log-me"


class _Panopto(http.server.BaseHTTPRequestHandler):
    """/podcast.mp4 (Range), /moved.mp4 → 302 to /cdn/podcast.mp4, /login.mp4 → 302 to the sign-in page, /slow.mp4
    (the whole file in small pieces, slowly). ``refuse``: that many answers of 403 first; ``seen``: (path, Range)."""

    refuse = 0
    seen: list = []
    broken = []

    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0]
        self.seen.append((path, self.headers.get("Range")))
        if path == "/moved.mp4":
            self._go("/cdn/podcast.mp4?" + SECRET)
            return
        if path == "/login.mp4":
            self._go("/Panopto/Pages/Auth/Login.aspx?ReturnUrl=x")
            return
        if path.startswith("/Panopto/Pages/Auth/"):
            body = b"<html>Sign in</html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/gone.mp4":
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/trimmed.mp4":                      # a range it no longer has (made again meanwhile), in words
            body = b"<?xml version='1.0'?><Error><Code>InvalidRange</Code></Error>"
            self.send_response(416)
            self.send_header("Content-Type", "application/xml")
            self.send_header("Content-Range", "bytes */1000")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if type(self).refuse > 0:
            type(self).refuse -= 1
            self.send_response(403)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        rng = self.headers.get("Range")
        first, last = 0, len(BLOB) - 1
        if rng:
            a, b = rng.split("=")[1].split("-")
            first, last = (int(a), int(b or len(BLOB) - 1)) if a else (len(BLOB) - int(b), len(BLOB) - 1)
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {first}-{last}/{len(BLOB)}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(last - first + 1))
        self.end_headers()
        body = BLOB[first:last + 1]
        try:
            if path == "/slow.mp4":
                for i in range(0, len(body), 16384):
                    self.wfile.write(body[i:i + 16384])
                    time.sleep(0.01)
            else:
                self.wfile.write(body)
        except OSError:
            self.broken.append(path)

    def _go(self, where):
        self.send_response(302)
        self.send_header("Location", where)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def panopto():
    _Panopto.refuse, _Panopto.seen, _Panopto.broken = 0, [], []
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Panopto)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def proxy():
    p = player.Proxy()
    yield p
    p.close()


def _get(url, rng=None, method="GET"):
    return requests.request(method, url, headers={"Range": rng} if rng else {}, timeout=10)


def test_the_players_range_requests_pass_through(panopto, proxy):
    token = proxy.add(player.Source("video", url=f"{panopto}/podcast.mp4?{SECRET}", session=requests.Session()))
    url = proxy.url(token)
    assert url.startswith("http://127.0.0.1:") and url.endswith(f"/m/{token}/lecture.mp4")
    end = _get(url, "bytes=-4096")                      # Chromium asks for the end first (the podcast's moov box)
    assert end.status_code == 206 and end.content == BLOB[-4096:]
    assert end.headers["Content-Range"] == f"bytes {len(BLOB) - 4096}-{len(BLOB) - 1}/{len(BLOB)}"
    assert end.headers["Content-Type"] == "video/mp4" and end.headers["Accept-Ranges"] == "bytes"
    mid = _get(url, "bytes=1000000-1999999")
    assert mid.status_code == 206 and mid.content == BLOB[1000000:2000000] and mid.headers["Content-Length"] == "1000000"
    whole = _get(url)
    assert whole.status_code == 200 and whole.content == BLOB
    head = _get(url, "bytes=0-", method="HEAD")
    assert head.status_code == 206 and head.headers["Content-Length"] == str(len(BLOB)) and head.content == b""
    assert [r for _, r in _Panopto.seen] == ["bytes=-4096", "bytes=1000000-1999999", None, "bytes=0-"]
    assert proxy.get(token).total == len(BLOB)


def test_a_redirect_is_followed_once_then_its_target_is_asked(panopto, proxy):
    src = player.Source("video", url=f"{panopto}/moved.mp4", session=requests.Session())
    url = proxy.url(proxy.add(src))
    assert _get(url, "bytes=0-99").content == BLOB[:100]
    assert _get(url, "bytes=100-199").content == BLOB[100:200]
    assert [p for p, _ in _Panopto.seen] == ["/moved.mp4", "/cdn/podcast.mp4", "/cdn/podcast.mp4"]


def test_a_refusal_gets_a_fresh_sign_in_and_address_once(panopto, proxy):
    renewed = []

    def renew():
        renewed.append(1)
        return requests.Session(), f"{panopto}/podcast.mp4?fresh=1"

    src = player.Source("video", url=f"{panopto}/podcast.mp4", session=requests.Session(), renew=renew)
    url = proxy.url(proxy.add(src))
    _Panopto.refuse = 1                                 # the course's access ran out
    r = _get(url, "bytes=0-9")
    assert r.status_code == 206 and r.content == BLOB[:10] and len(renewed) == 1 and src.url.endswith("fresh=1")
    _Panopto.refuse = 5                                 # refused with the fresh one too: said, not tried for ever
    r = _get(url, "bytes=0-9")
    assert r.status_code == 403 and len(renewed) == 2


def test_the_sign_in_page_counts_as_a_refusal(panopto, proxy):
    renewed = []

    def renew():
        renewed.append(1)
        return requests.Session(), f"{panopto}/podcast.mp4"

    src = player.Source("audio", url=f"{panopto}/login.mp4", session=requests.Session(), renew=renew,
                        content_type="audio/mp4")
    r = _get(proxy.url(proxy.add(src), "lecture.m4a"), "bytes=5-14")
    assert r.status_code == 206 and r.content == BLOB[5:15] and r.headers["Content-Type"] == "audio/mp4"
    assert renewed == [1] and "text/html" not in r.headers["Content-Type"]


def test_only_its_own_tokens_are_served_and_they_end(panopto):
    clock = [1000.0]
    proxy = player.Proxy(clock=lambda: clock[0])
    try:
        token = proxy.add(player.Source("video", url=f"{panopto}/podcast.mp4", session=requests.Session()))
        base = proxy.url(token).rsplit("/m/", 1)[0]
        assert _get(f"{base}/m/{'x' * 32}/lecture.mp4", "bytes=0-9").status_code == 404
        assert _get(f"{base}/m/{token}", "bytes=0-9").status_code == 404
        assert _get(f"{base}/{token}/lecture.mp4", "bytes=0-9").status_code == 404
        assert _get(proxy.url(token), "bytes=0-9").status_code == 206
        clock[0] += player.TOKEN_LIFE_S + 1                # 6 hours later
        assert _get(proxy.url(token), "bytes=0-9").status_code == 404
        token = proxy.add(player.Source("video", url=f"{panopto}/podcast.mp4", session=requests.Session()))
        assert proxy.forget(token) and _get(proxy.url(token), "bytes=0-9").status_code == 404
        assert not _Panopto.seen[1:]                      # nothing refused ever reached Panopto
    finally:
        proxy.close()
    assert player._local("127.0.0.1") and player._local("::1") and not player._local("192.168.1.20")


def test_a_connection_the_player_drops_ends_quietly(panopto, proxy, caplog):
    caplog.set_level(logging.DEBUG, logger="studia")
    url = proxy.url(proxy.add(player.Source("video", url=f"{panopto}/slow.mp4", session=requests.Session())))
    port, path = int(url.split(":")[2].split("/")[0]), "/" + url.split("/", 3)[3]
    s = socket.create_connection(("127.0.0.1", port))
    s.sendall(f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nRange: bytes=0-\r\n\r\n".encode())
    got = s.recv(65536)
    assert b" 206 " in got.split(b"\r\n")[0]
    s.close()                                           # a seek: the player lets go of this answer
    for _ in range(100):                                # the proxy stops reading Panopto's answer too
        if _Panopto.broken:
            break
        time.sleep(0.05)
    assert _Panopto.broken == ["/slow.mp4"]
    assert _get(url, "bytes=10-19").content == BLOB[10:20]          # the next range plays
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING or r.exc_info]


def test_a_range_panopto_no_longer_has_is_answered_at_once(panopto, proxy):
    """Panopto (or its CDN) answers 416 with a few words: their length was passed on without them, so the player
    waited a minute for bytes that never came before it could ask again."""
    url = proxy.url(proxy.add(player.Source("video", url=f"{panopto}/trimmed.mp4", session=requests.Session())))
    port, path = int(url.split(":")[2].split("/")[0]), "/" + url.split("/", 3)[3]
    s = socket.create_connection(("127.0.0.1", port))
    s.settimeout(5)

    def ask(rng):                              # as the player does: the answer and every byte it promises
        s.sendall(f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nRange: {rng}\r\n\r\n".encode())
        got = b""
        while b"\r\n\r\n" not in got:
            piece = s.recv(65536)
            assert piece, "the proxy closed the connection"
            got += piece
        head, _, body = got.partition(b"\r\n\r\n")
        status, *lines = head.decode().split("\r\n")
        headers = {k.strip().lower(): v.strip() for k, v in (h.split(":", 1) for h in lines)}
        while len(body) < int(headers["content-length"]):
            body += s.recv(65536)
        return status, headers, body

    try:
        status, headers, body = ask("bytes=5000-")
        assert " 416 " in status and headers["content-range"] == "bytes */1000" and body == b""
        assert " 416 " in ask("bytes=0-99")[0]                    # the same connection, asked again at once
    finally:
        s.close()


def test_panoptos_address_never_reaches_the_log(panopto, proxy, caplog):
    caplog.set_level(logging.DEBUG, logger="studia")

    def renew():
        raise RuntimeError("no")

    def unreachable(url, **kw):
        raise requests.ConnectionError(f"Max retries exceeded with url: {url}")

    for url, session in ((f"{panopto}/moved.mp4", requests.Session()), (f"{panopto}/podcast.mp4?{SECRET}", requests.Session()),
                         (f"{panopto}/podcast.mp4?{SECRET}", types.SimpleNamespace(get=unreachable))):
        _Panopto.refuse = 1
        src = player.Source("video", url=url, session=session, renew=renew)
        assert _get(proxy.url(proxy.add(src)), "bytes=0-9").status_code in (403, 502)
    text = "\n".join(r.getMessage() + str(r.args) for r in caplog.records)
    assert "Player:" in text and SECRET not in text and "/cdn/" not in text and "podcast.mp4" not in text


def test_a_file_on_this_pc_plays_with_ranges(tmp_path, proxy):
    f = tmp_path / "lecture.m4a"
    f.write_bytes(BLOB)
    url = proxy.url(proxy.add(player._file_source(f)), "lecture.m4a")
    r = _get(url, "bytes=-100")
    assert r.status_code == 206 and r.content == BLOB[-100:] and r.headers["Content-Type"] == "audio/mp4"
    assert r.headers["Content-Range"] == f"bytes {len(BLOB) - 100}-{len(BLOB) - 1}/{len(BLOB)}"
    assert _get(url, "bytes=7-").content == BLOB[7:]
    assert _get(url).content == BLOB
    bad = _get(url, f"bytes={len(BLOB) + 5}-")
    assert bad.status_code == 416 and bad.headers["Content-Range"] == f"bytes */{len(BLOB)}"
    f.unlink()
    assert _get(url, "bytes=0-1").status_code == 404


def test_byte_ranges():
    assert player.byte_range(None, 100) is None and player.byte_range("bytes=0-9,20-29", 100) is None
    assert player.byte_range("bytes=10-19", 100) == (10, 19) and player.byte_range("bytes=90-200", 100) == (90, 99)
    assert player.byte_range("bytes=-30", 100) == (70, 99) and player.byte_range("bytes=-300", 100) == (0, 99)
    assert player.byte_range("bytes=100-", 100) is False and player.byte_range("bytes=-0", 100) is False
    assert player.byte_range("bytes=20-10", 100) is None


# ----------------------------------------------------------------------------- what plays
def _info(podcast="https://panopto.example/Content/x.mp4?InvocationID=1", audio=True, at=0):
    return {"LastViewingPosition": at, "UserName": "someone",
            "Delivery": {"Duration": 2718.0, "PodcastStreams": [{"StreamUrl": podcast}] if podcast else [],
                         "IsAudioPodcastEncodeComplete": audio, "Streams": []}}


class _Sync:
    def __init__(self):
        self.ensured, self.absorbed = [], 0

    def worker_session(self):
        return requests.Session()

    def ensure_panopto(self, course_id=None):
        self.ensured.append(course_id)

    def absorb(self, s):
        self.absorbed += 1


def _fake_panopto(monkeypatch, answers):
    """Panopto's DeliveryInfo answers, in turn: a dict, or an exception to raise."""
    calls = []

    class Fake:
        def __init__(self, s):
            pass

        def delivery_info(self, did):
            calls.append(did)
            a = answers.pop(0) if len(answers) > 1 else answers[0]
            if isinstance(a, BaseException):
                raise a
            return a

    monkeypatch.setattr(player, "Panopto", Fake)
    return calls


@pytest.fixture
def probed(monkeypatch):
    seen = []

    def probe(src):
        seen.append(src)
        src.total = 285_000_000

    monkeypatch.setattr(player, "_probe", probe)
    return seen


def _tr(did, meta=None):
    return Transcript(delivery_id=did, segments=[Segment(0, 5, "Hello", "en")], duration_s=5, speech_s=5,
                      language_seconds={"en": 5}, model="m", device="cpu", engine_version="1", created_at="2026-11-09",
                      meta=meta or {})


def _rec(did="d1", live=False):
    return Recording(delivery_id=did, course_id="c", folder_id="f", folder_path="", name="Lecture",
                     start=dt.datetime(2026, 11, 4, 11, 15), duration_s=2700.0, viewer_url=f"https://v/{did}?id=1",
                     live=live)


def test_the_podcast_with_picture_plays_from_panoptos_resume_point(monkeypatch, probed, proxy):
    sync = _Sync()
    _fake_panopto(monkeypatch, [_info(at=613.5)])
    out = player.open_recording(sync, _rec(), proxy=proxy)
    assert out["ok"] and out["kind"] == "video" and out["start"] == 613.5 and out["duration"] == 2718.0 and not out["local"]
    assert out["bytes"] == 285_000_000 and out["url"] == proxy.url(out["token"]) and out["viewer_url"] == "https://v/d1?id=1"
    src = proxy.get(out["token"])
    assert src.url.startswith("https://panopto.example/") and src.content_type == "video/mp4" and sync.absorbed == 1
    assert "someone" not in json.dumps(out)
    out = player.open_recording(sync, _rec(), avoid="video", proxy=proxy)      # the video didn't play: sound only
    src = proxy.get(out["token"])
    assert out["kind"] == "audio" and out["url"].endswith("/lecture.m4a") and src.content_type == "audio/mp4"
    assert src.url == "https://panopto.ieu.edu.tr/Panopto/Podcast/Stream/d1.mp4?mediaTargetType=audioPodcast"


def test_which_podcast():
    both, url = _info(), "https://panopto.example/Content/x.mp4?InvocationID=1"
    audio = "https://panopto.ieu.edu.tr/Panopto/Podcast/Stream/d1.mp4?mediaTargetType=audioPodcast"
    assert player.address(both, "d1") == (url, "video") and player.address(both, "d1", "video") == (audio, "audio")
    assert player.address(_info(audio=False), "d1", "video") == (url, "video")       # sound only asked, none made yet
    assert player.address(_info(podcast=""), "d1") == (audio, "audio")
    assert player.address(_info(podcast="http://elsewhere/x.mp4", audio=False), "d1") == ("", "")
    assert player.address({"Delivery": None}, "d1") == ("", "")


def test_a_signed_out_panopto_signs_in_again_once(monkeypatch, probed, proxy):
    sync = _Sync()
    calls = _fake_panopto(monkeypatch, [NeedPanoptoAuth("expired"), _info()])
    out = player.open_recording(sync, _rec(), proxy=proxy)
    assert out["ok"] and sync.ensured == ["c"] and calls == ["d1", "d1"]
    _fake_panopto(monkeypatch, [NeedPanoptoAuth("expired"), NeedPanoptoAuth("still")])
    out = player.open_recording(sync, _rec(), proxy=proxy)
    assert not out["ok"] and out["error"].startswith("IEUStudia couldn't sign in to Panopto, so the lecture can't play")


def test_why_it_cant_play_is_said_in_plain_words(monkeypatch, probed, proxy):
    sync = _Sync()
    cases = [(requests.ConnectionError("no route"), "Panopto can't be reached, so the lecture can't play. Check your "
                                                    "internet connection, then press Try again."),
             (RuntimeError("You do not have access to this session."),
              "Panopto says: “You do not have access to this session.” The recording may have been removed or moved. "
              "Its transcript stays here."),
             (player._Gone(), "This recording is no longer on Panopto (it may have been removed or moved). Its "
                              "transcript stays here.")]
    for exc, said in cases:
        _fake_panopto(monkeypatch, [exc])
        out = player.open_recording(sync, _rec(), proxy=proxy)
        assert out == {"ok": False, "error": said}
    _fake_panopto(monkeypatch, [_info(podcast="", audio=False)])
    assert player.open_recording(sync, _rec(), proxy=proxy)["error"].startswith("Panopto is still preparing this")
    assert player.open_recording(sync, _rec(live=True), proxy=proxy)["error"].startswith("This lecture is still being")
    from studia.ieulearn import NeedLogin

    _fake_panopto(monkeypatch, [NeedPanoptoAuth("expired")])
    sync.ensure_panopto = lambda course_id=None: (_ for _ in ()).throw(NeedLogin("Please sign in to IEULearn."))
    assert player.open_recording(sync, _rec(), proxy=proxy) == {"ok": False, "error": "Please sign in to IEULearn.",
                                                                "need_login": True}


def test_the_copy_on_this_pc_plays_when_panopto_cant(monkeypatch, probed, proxy):
    from studia.paths import audio_cache_dir

    (audio_cache_dir() / "d1.m4a").write_bytes(BLOB[:1000])        # kept after transcribing
    _fake_panopto(monkeypatch, [requests.ConnectionError("offline")])
    out = player.open_recording(_Sync(), _rec(), proxy=proxy)
    assert out["ok"] and out["kind"] == "audio" and out["local"] and out["note"].startswith("Panopto can't be reached right now")
    assert proxy.get(out["token"]).path == str(audio_cache_dir() / "d1.m4a")
    _fake_panopto(monkeypatch, [_info()])                           # online: Panopto's video (picture) comes first
    assert player.open_recording(_Sync(), _rec(), proxy=proxy)["kind"] == "video"


def test_a_lecture_transcribed_from_a_file_plays_that_file(tmp_path, proxy):
    f = tmp_path / "recording.mp4"
    f.write_bytes(BLOB[:5000])
    store.save_transcript(_tr("local-abc", {"source_path": str(f)}))
    out = player.open_part(types.SimpleNamespace(get_recording=lambda i: None), _Sync(), "local-abc", proxy=proxy)
    assert out["ok"] and out["kind"] == "file" and out["viewer_url"] == "" and _get(out["url"], "bytes=0-9").content == BLOB[:10]
    f.unlink()
    out = player.open_part(types.SimpleNamespace(get_recording=lambda i: None), _Sync(), "local-abc", proxy=proxy)
    assert not out["ok"] and "no longer on this PC" in out["error"]


def test_the_parts_are_numbered_as_on_the_transcript_page_with_their_screens(tmp_path, monkeypatch, probed, proxy):
    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="c", title="2026-1 EEE 215", code="EEE 215", name="Circuits")])
    recs = [_rec("p1"), _rec("p2")]
    recs[1].start = dt.datetime(2026, 11, 4, 12, 9)
    lib.merge_recordings("c", recs, first_sync=True)
    for did in ("p1", "p2"):
        store.save_transcript(_tr(did))
        lib.set_status(did, "done")
    pic = tmp_path / "s1.jpg"
    pic.write_bytes(b"\xff\xd8\xff\xd9")
    store.save_slides("p2", [Slide(2, 30.0, 95.5, "slides/2.jpg", str(pic)), Slide(1, 0.0, 30.0, "slides/1.jpg", str(pic))])
    (store.transcripts_dir() / "p2.screens.json").write_text(json.dumps(
        {"v": 1, "times": "exact", "screens": {"2": {"title": "Δ - Y  transformation", "text": "Apply superposition",
                                                    "from": "powerpoint"}}}), encoding="utf-8")
    _fake_panopto(monkeypatch, [_info()])
    out = player.open_part(lib, _Sync(), "c|2026-11-04|lecture", 2, proxy=proxy)
    assert out["ok"] and out["delivery_id"] == "p2" and out["part"] == 2
    assert [(s["n"], s["t0"], s["t1"], s["title"], s["text"]) for s in out["screens"]] == \
        [(1, 0.0, 30.0, "", ""), (2, 30.0, 95.5, "Δ - Y transformation", "Apply superposition")]
    assert out["screens"][0]["image"] == pic.as_uri()
    assert player.open_part(lib, _Sync(), "c|2026-11-04|lecture", 3, proxy=proxy) == \
        {"ok": False, "error": "This lecture is no longer in your library."}


def test_the_api_hooks(monkeypatch, probed):
    from studia import api

    seen = []
    monkeypatch.setattr(player, "open_part", lambda lib, sync, key, part, avoid: seen.append((key, part, avoid)) or {"ok": True})
    fake = types.SimpleNamespace(_s=types.SimpleNamespace(lib=None, sync=None))
    assert api.Api.player_open(fake, "c|2026-11-04|lecture", 2, "video") == {"ok": True}
    assert seen == [("c|2026-11-04|lecture", 2, "video")]
    assert api.Api.player_close(fake, "nope") == {"ok": True, "closed": False}
