"""The resumable downloader against a small local server that answers Range requests."""

import http.server
import os
import threading

import pytest
import requests

from studia import httpdl


class _Ranged(http.server.BaseHTTPRequestHandler):
    data = b""

    def do_GET(self):  # noqa: N802 - http.server's naming
        rng = self.headers.get("Range")
        if rng:
            a, b = rng.split("=")[1].split("-")
            a, b = int(a), int(b or len(self.data) - 1)
            body = self.data[a:b + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {a}-{b}/{len(self.data)}")
        else:
            body = self.data
            self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    _Ranged.data = os.urandom(20 * 1024 * 1024)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Ranged)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/model.bin"
    srv.shutdown()


def test_a_download_in_parts_resumes_and_leaves_nothing_behind(server, tmp_path):
    dest = tmp_path / "model.bin"
    first = dest.with_name("model.bin.part0")
    first.write_bytes(_Ranged.data[:1000])                      # an earlier attempt got this far
    seen = []
    httpdl.download(server, dest, session=requests.Session(), parts=4, progress=lambda d, t: seen.append((d, t)))
    assert dest.read_bytes() == _Ranged.data and seen[-1] == (len(_Ranged.data), len(_Ranged.data))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["model.bin"]


def test_each_part_and_the_joined_file_are_on_the_disk_before_they_count(server, tmp_path, monkeypatch):
    """A power cut can leave a file of its full size with zeros at its end (only its size was safe): a part like that
    was trusted when the download went on, and the joined file was moved into place from Windows' cache."""
    flushed, real = [], os.fsync

    def fsync(fd):
        flushed.append(os.path.basename(open_names.get(fd, "?")))
        return real(fd)

    open_names, real_open = {}, open

    def opening(file, mode="r", *a, **k):
        fh = real_open(file, mode, *a, **k)
        if "b" in mode and ("a" in mode or "w" in mode):
            open_names[fh.fileno()] = str(file)
        return fh

    monkeypatch.setattr(os, "fsync", fsync)
    monkeypatch.setattr("builtins.open", opening)
    dest = tmp_path / "model.bin"
    httpdl.download(server, dest, session=requests.Session(), parts=4)
    assert dest.read_bytes() == _Ranged.data
    assert sorted(set(flushed) - {"model.bin.part"}) == [f"model.bin.part{i}" for i in range(4)]
    assert flushed.count("model.bin.part") == 3              # the joined file, after each part appended to it


def test_joining_frees_each_part_and_without_room_touches_nothing(tmp_path, monkeypatch):
    import errno
    import shutil
    import types

    parts = [tmp_path / f"f.part{i}" for i in range(3)]
    for i, p in enumerate(parts):
        p.write_bytes(bytes([i]) * 10)
    httpdl._join(parts, tmp_path / "f.part")
    assert (tmp_path / "f.part").read_bytes() == b"\0" * 10 + b"\1" * 10 + b"\2" * 10
    assert not any(p.exists() for p in parts)
    for i, p in enumerate(parts):
        p.write_bytes(bytes([i]) * 10)
    monkeypatch.setattr(shutil, "disk_usage", lambda p: types.SimpleNamespace(free=1000))     # a nearly full disk
    with pytest.raises(OSError) as err:
        httpdl._join(parts, tmp_path / "g.part")
    assert err.value.errno == errno.ENOSPC and all(p.exists() for p in parts)          # the next try only joins


def test_parts_of_a_file_that_changed_since_are_not_reused(server, tmp_path):
    dest = tmp_path / "a.m4a"
    dest.with_name("a.m4a.part0").write_bytes(b"\xff" * 5000)        # parts of an earlier version of the recording
    dest.with_name("a.m4a.parts.json").write_text('{"total": 123, "version": ""}', encoding="utf-8")
    httpdl.download(server, dest, session=requests.Session(), parts=4)
    assert dest.read_bytes() == _Ranged.data and sorted(p.name for p in tmp_path.iterdir()) == ["a.m4a"]


def test_a_part_left_over_is_not_taken_for_part_of_the_file_once_it_changed(server, tmp_path, monkeypatch):
    from pathlib import Path

    dest, unlink = tmp_path / "a.m4a", Path.unlink

    def held(self, missing_ok=False):                                # an antivirus still holds one part
        if self.name == "a.m4a.part2":
            raise PermissionError(13, "The process cannot access the file", str(self))
        return unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", held)
    httpdl.download(server, dest, session=requests.Session(), parts=4)
    monkeypatch.setattr(Path, "unlink", unlink)
    assert dest.read_bytes() == _Ranged.data
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.m4a", "a.m4a.part2", "a.m4a.parts.json"]
    dest.unlink()                                                    # its audio is deleted once transcribed,
    _Ranged.data = os.urandom(21 * 1024 * 1024)                      # then the lecturer edits the recording
    httpdl.download(server, dest, session=requests.Session(), parts=4)
    assert dest.read_bytes() == _Ranged.data and sorted(p.name for p in tmp_path.iterdir()) == ["a.m4a"]


# ------------------------------------------------------------------ a link that expires, a dropped connection
class _Signed(http.server.BaseHTTPRequestHandler):
    """Hugging Face as a download sees it: /model.bin redirects to a signed link, which stops working after a while
    (only new requests are refused; one under way goes on). Each answer gives at most 2 MB of what was asked for, so
    the parts come back many times. ``drop``: requests that are cut off before any answer (a Wi-Fi drop)."""
    data, token, valid_for, used, links, served, drop = b"", 0, 0, 0, 0, 0, 0

    def do_GET(self):  # noqa: N802 - http.server's naming
        cls = type(self)
        if self.path == "/model.bin":
            cls.token += 1
            cls.used, cls.links = 0, cls.links + 1
            self.send_response(302)
            self.send_header("Location", f"/blob?token={cls.token}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        a, b = (int(x or 0) for x in self.headers["Range"].split("=")[1].split("-"))
        if (a, b) != (0, 0):
            if cls.drop > 0:
                cls.drop -= 1
                self.close_connection = True                # no answer at all: the connection just ends
                return
            cls.used += 1
            if self.path != f"/blob?token={cls.token}" or cls.used > cls.valid_for:
                self.send_response(403)                       # an expired signature
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            b = min(b, a + 2 * 1024 * 1024 - 1)
            cls.served += b - a + 1
        body = cls.data[a:b + 1]
        self.send_response(206)
        self.send_header("Content-Range", f"bytes {a}-{b}/{len(cls.data)}")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def signed():
    _Signed.data, _Signed.token, _Signed.links, _Signed.served, _Signed.drop = os.urandom(24 * 1024 * 1024), 0, 0, 0, 0
    _Signed.valid_for = 1000
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Signed)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/model.bin"
    srv.shutdown()


def test_a_link_that_expires_halfway_is_renewed_once_and_every_part_goes_on(signed, tmp_path):
    """A slow line takes over an hour for a big model; a part that reconnects after that used to get 403 with the
    hour-old link and the whole download failed ('expected HTTP 206, got 403')."""
    _Signed.valid_for = 8                       # the link stops working after 8 answers (12 are needed)
    dest = tmp_path / "model.bin"
    httpdl.download(signed, dest, session=requests.Session(), parts=4)
    assert dest.read_bytes() == _Signed.data
    assert _Signed.links == 2                   # one fresh link, shared by the four parts
    assert _Signed.served == len(_Signed.data)  # nothing was downloaded twice


def test_a_dropped_connection_is_waited_out_and_a_lasting_one_says_so(signed, tmp_path, monkeypatch):
    slept = []
    monkeypatch.setattr(httpdl, "_sleep", lambda s, cancel: slept.append(s))
    _Signed.drop = 6                            # six requests cut off: more than the parts' first tries
    dest = tmp_path / "model.bin"
    httpdl.download(signed, dest, session=requests.Session(), parts=4)
    assert dest.read_bytes() == _Signed.data and slept
    dest.unlink()
    _Signed.drop = 10 ** 6                      # the connection never comes back: given up after the patience
    with pytest.raises(requests.ConnectionError):
        httpdl.download(signed, tmp_path / "other.bin", session=requests.Session(), parts=2, patience=0.3)


class _Flaky:
    """A part's requests by plan: "drop" (cut off before a byte) or n (n bytes, then cut off). Every wait between
    tries takes 100 s on a fake clock, so the patience is what is measured, not the number of tries."""

    def __init__(self, plan, data):
        self.plan, self.data, self.asked = list(plan), data, 0

    def get(self, url, headers=None, **kw):
        self.asked += 1
        step = self.plan.pop(0) if self.plan else len(self.data)
        if step == "drop":
            raise requests.ConnectionError("Connection aborted")
        start = int(headers["Range"].split("=")[1].split("-")[0])
        body = self.data[start:start + step]
        whole = start + step >= len(self.data)

        class Answer:
            status_code = 206

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def iter_content(self, size):
                yield body
                if not whole:
                    raise requests.exceptions.ChunkedEncodingError("Connection broken")

        return Answer()


def _patience_run(monkeypatch, tmp_path, plan, patience=180.0):
    clock = [1000.0]
    monkeypatch.setattr(httpdl.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(httpdl, "_sleep", lambda s, cancel: clock.__setitem__(0, clock[0] + 100))
    data = b"y" * 40
    session = _Flaky(plan, data)
    link = httpdl._Link(session, "u", "u", len(data), None)
    part = tmp_path / "f.part0"
    httpdl._fetch_range(session, link, part, 0, len(data) - 1, [0], threading.Lock(), None, patience)
    return part.read_bytes() == data, session.asked


def test_a_part_gives_up_only_after_its_patience_without_a_byte(monkeypatch, tmp_path):
    """Pinned with a fake clock: three minutes without a byte (the clock starting again with every byte), not a
    number of tries. (Twelve drops with a few bytes between them went on; two waits without any did not.)"""
    plan = ["drop", 2] * 12                              # comes and goes, a little each time: well past 8 tries
    assert _patience_run(monkeypatch, tmp_path, plan) == (True, 25)
    (tmp_path / "again").mkdir()
    with pytest.raises(requests.ConnectionError):        # 0 s, 100 s, 200 s without a byte: over 180 s
        _patience_run(monkeypatch, tmp_path / "again", ["drop"] * 3)


def test_a_slow_download_renews_its_link_as_often_as_it_needs_over_the_hours(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(httpdl.time, "monotonic", lambda: clock[0])
    looks = iter(httpdl._Look(f"https://cdn/{i}", 10, True, "", False) for i in range(1, 99))
    monkeypatch.setattr(httpdl, "_look", lambda session, url, cancel=None: next(looks))
    link = httpdl._Link(None, "https://hf/model.bin", "https://cdn/0", 10, None)
    for _ in range(6):                                   # an expiry every hour, six hours long
        assert link.renew(link.final)
        clock[0] += 3600
    for _ in range(3):                                   # three refusals in a row at once: fine
        assert link.renew(link.final)
    assert not link.renew(link.final)                    # a fourth within ten minutes: a real refusal


# ------------------------------------------------------------------ an antivirus at the finish, a web page instead
def test_a_finished_file_an_antivirus_holds_is_waited_for(server, tmp_path, monkeypatch):
    real, held = os.replace, [2]

    def replace(src, dst):
        if str(src).endswith("model.bin.part") and held[0] > 0:       # scanned for a moment just after the download
            held[0] -= 1
            raise PermissionError(13, "The process cannot access the file", str(src))
        return real(src, dst)

    monkeypatch.setattr(os, "replace", replace)
    monkeypatch.setattr(httpdl, "_sleep", lambda s, cancel: None)
    dest = tmp_path / "model.bin"
    httpdl.download(server, dest, session=requests.Session(), parts=4)
    assert dest.read_bytes() == _Ranged.data and held == [0]


def test_a_download_lost_at_the_last_step_is_only_moved_the_next_time(server, tmp_path, monkeypatch):
    """The whole file was there but couldn't be renamed: the next try used to fetch every byte again ('continues
    where it stopped', the setup said), and with an antivirus that wins every time it never finished."""
    real = os.replace

    def held(src, dst):
        if str(src).endswith("model.bin.part"):
            raise PermissionError(13, "The process cannot access the file", str(src))
        return real(src, dst)

    monkeypatch.setattr(os, "replace", held)
    monkeypatch.setattr(httpdl, "HOLD_S", 0.2)
    dest = tmp_path / "model.bin"
    with pytest.raises(PermissionError):
        httpdl.download(server, dest, session=requests.Session(), parts=4)
    assert (tmp_path / "model.bin.part").stat().st_size == len(_Ranged.data) and not dest.exists()
    monkeypatch.setattr(os, "replace", real)
    asked = []
    session = requests.Session()
    get = session.get
    session.get = lambda url, **kw: asked.append(kw.get("headers", {}).get("Range")) or get(url, **kw)
    httpdl.download(server, dest, session=session, parts=4)
    assert dest.read_bytes() == _Ranged.data and asked == ["bytes=0-0"]     # only the look, no part again
    assert sorted(p.name for p in tmp_path.iterdir()) == ["model.bin"]


class _Portal(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = b"<html><body>Sign in to the campus Wi-Fi</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_a_web_page_in_place_of_the_file_is_never_saved_as_the_file(tmp_path):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Portal)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with pytest.raises(httpdl.NotTheFile, match="sent a web page"):
            httpdl.download(f"http://127.0.0.1:{srv.server_address[1]}/model.bin", tmp_path / "model.bin",
                            session=requests.Session())
    finally:
        srv.shutdown()
    assert not (tmp_path / "model.bin").exists()


# ------------------------------------------------------------------ why it stopped, in plain words
def _response(code, url="https://cas-bridge.xethub.hf.co/x"):
    r = requests.Response()
    r.status_code, r.url = code, url
    return r


@pytest.mark.parametrize("online", [True, False])
def test_why_a_download_stopped_is_said_in_plain_words(monkeypatch, online):
    import errno

    from studia import net

    monkeypatch.setattr(net, "online", lambda timeout=5.0: online)
    lost = httpdl.explain(requests.ConnectionError("getaddrinfo failed"), "huggingface.co")
    assert isinstance(lost, net.NetworkError) and net.connection_lost(lost)          # still a connection problem
    if online:      # the university's sites answer, Hugging Face doesn't: this network blocks it
        assert "huggingface.co can't be reached from this network" in str(lost) and "hotspot" in str(lost)
    else:
        assert "can't reach the internet" in str(lost) and "Wi-Fi" in str(lost)
    refused = httpdl.explain(requests.HTTPError("x", response=_response(403)), "huggingface.co")
    assert "huggingface.co refused the download (error 403)" in net.plain_error(refused)
    gone = httpdl.explain(requests.HTTPError("x", response=_response(404)), "github.com")
    assert "github.com doesn't have this file any more" in net.plain_error(gone)
    # Hugging Face's answer for a repo that is gone, renamed or private: not "a network filter, try a hotspot"
    moved = httpdl.explain(requests.HTTPError("x", response=_response(401)), "huggingface.co")
    assert "huggingface.co doesn't have this file any more (error 401)" in net.plain_error(moved)
    assert "hotspot" not in net.plain_error(moved)
    busy = httpdl.explain(requests.HTTPError("x", response=_response(503)), "pypi.org")
    assert net.plain_error(busy).startswith("pypi.org answered with an error (503)")      # not the CDN's name
    taken = httpdl.explain(FileNotFoundError(2, "No such file", r"C:\x\model.bin.part3"), "huggingface.co")
    assert isinstance(taken, OSError) and "model.bin.part3 disappeared" in net.plain_error(taken)
    assert "antivirus" in net.plain_error(taken)
    full = OSError(errno.ENOSPC, "No space left on device")
    assert httpdl.explain(full, "huggingface.co") is full and "disk is full" in net.plain_error(full)
    with pytest.raises(net.NetworkError) as raised:                  # raised with the original chained to it
        try:
            raise requests.ConnectionError("boom")
        except requests.ConnectionError as exc:
            httpdl.raise_explained(exc, "github.com")
    assert isinstance(raised.value.__cause__, requests.ConnectionError)


def test_a_component_download_says_why_it_stopped(monkeypatch):
    from studia import components, net
    from studia.engine import models

    monkeypatch.setattr(net, "online", lambda timeout=5.0: True)

    def blocked(*args, **kwargs):
        raise requests.ConnectionError("getaddrinfo failed")

    monkeypatch.setattr(models, "download_model", blocked)
    monkeypatch.setattr(components, "download_cuda", blocked)
    with pytest.raises(net.NetworkError, match="huggingface.co can't be reached from this network"):
        components.download("speech:small", lambda d, n: None)
    with pytest.raises(net.NetworkError, match="pypi.org can't be reached from this network"):
        components.download("cuda", lambda d, n: None)
