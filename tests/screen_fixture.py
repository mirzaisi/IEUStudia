"""A made-up Panopto screen stream for the capture tests: grey frames (one a second) encoded as H.264 in one fragmented
MP4 with a keyframe every 9 frames (Panopto's ~9 s segments), served as HLS with byte ranges by a fake requests
session. The pictures are drawn with NumPy: "text" is rows of small blocks, as a slide's letters are at this size."""

import io
import struct
from typing import Optional

import numpy as np
import pytest

W, H = 384, 216
SEG = 9                         # frames (seconds) a segment


def blank(bg: int = 235) -> np.ndarray:
    return np.full((H, W), bg, np.uint8)


def write(img: np.ndarray, x: int, y: int, n: int, ink: int = 30, seed: int = 0, step: int = 1) -> np.ndarray:
    """A line of letters in ``n`` places from (x, y), 5 px apart: each a 3x7 shape of its own (a stroke and a bar
    or two), some spaces between words. ``step`` 2 writes every other place only."""
    rng = np.random.default_rng(seed)
    for i in range(0, n, step):
        if rng.random() < 0.15:                     # a space between words
            continue
        x0 = x + 5 * i
        img[y:y + 7, x0 + int(rng.integers(0, 3))] = ink          # the stroke
        for row in rng.choice(7, size=int(rng.integers(1, 3)), replace=False):
            img[y + int(row), x0:x0 + 3] = ink                    # bars
    return img


def page(lines: int, start: int = 0, bg: int = 235, ink: int = 30, x: int = 20, y: int = 16, per: int = 30,
         img: Optional[np.ndarray] = None) -> np.ndarray:
    """A page of ``lines`` lines of text (line k drawn the same on every page that has it, seeded by ``start + k``)."""
    img = blank(bg) if img is None else img.copy()
    for k in range(lines):
        write(img, x, y + 14 * k, per, ink, seed=start + k)
    return img


def _boxes(data: bytes):
    pos = 0
    while pos + 8 <= len(data):
        size, typ = struct.unpack(">I4s", data[pos:pos + 8])
        yield typ.decode("latin-1"), pos, pos + size
        pos += size


def encode(frames: list[np.ndarray], sound: bool = False) -> tuple[bytes, int, list[tuple[int, int]]]:
    """(the MP4 file, its init segment's length, each segment's (offset, length)). ``sound``: a sound track too,
    the first one (as a camera stream has its microphone: each fragment then starts with the sound's samples)."""
    import av

    if "libx264" not in av.codecs_available:
        pytest.skip("this PyAV has no H.264 encoder")
    buf = io.BytesIO()
    with av.open(buf, mode="w", format="mp4",
                 options={"movflags": "frag_keyframe+empty_moov+default_base_moof"}) as out:
        au = out.add_stream("aac", rate=16000) if sound else None
        if au is not None:
            au.layout = "mono"
        st = out.add_stream("libx264", rate=1)
        st.width, st.height, st.pix_fmt = W, H, "yuv420p"
        st.codec_context.options = {"preset": "ultrafast", "crf": "10",
                                    "x264-params": f"keyint={SEG}:min-keyint={SEG}:scenecut=0:bframes=0"}
        samples = 0
        for i, g in enumerate(frames):
            while au is not None and samples < (i + 1) * 16000:          # a second of quiet sound a picture
                a = av.AudioFrame.from_ndarray(np.zeros((1, 1024), np.float32), format="fltp", layout="mono")
                a.sample_rate, a.pts = 16000, samples
                samples += 1024
                for pkt in au.encode(a):
                    out.mux(pkt)
            f = av.VideoFrame.from_ndarray(np.ascontiguousarray(g), format="gray").reformat(format="yuv420p")
            f.pts = i
            for pkt in st.encode(f):
                out.mux(pkt)
        for pkt in st.encode(None):
            out.mux(pkt)
        if au is not None:
            for pkt in au.encode(None):
                out.mux(pkt)
    data = buf.getvalue()
    boxes = list(_boxes(data))
    init = next(e for typ, s, e in boxes if typ == "moov")
    segs, start = [], None
    for typ, s, e in boxes:
        if typ == "moof":
            start = s
        elif typ == "mdat" and start is not None:
            segs.append((start, e - start))
            start = None
    return data, init, segs


class _Resp:
    def __init__(self, url: str, text: str = "", content: bytes = b"", status: int = 200):
        self.url, self.text, self.content, self.status_code, self.headers = url, text, content, status, {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class Stream:
    """A fake session serving one screen stream at BASE + "master.m3u8"; ``served`` counts the media bytes sent."""
    BASE = "https://p.test/Sessions/x.screen.hls/"

    def __init__(self, frames: list[np.ndarray], sound: bool = False):
        self.data, init, segs = encode(frames, sound)
        self.segments = len(segs)
        self.served = 0
        lines = ["#EXTM3U", "#EXT-X-VERSION:7", f"#EXT-X-TARGETDURATION:{SEG}",
                 f'#EXT-X-MAP:URI="media.mp4",BYTERANGE="{init}@0"']
        for off, length in segs:
            lines += [f"#EXTINF:{SEG}.0,", f"#EXT-X-BYTERANGE:{length}@{off}", "media.mp4"]
        self.media = "\n".join(lines + ["#EXT-X-ENDLIST", ""])
        self.master = ("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=300000,RESOLUTION=384x216,CODECS=\"avc1.64001e\"\n"
                       "video.m3u8\n")

    @property
    def url(self) -> str:
        return self.BASE + "master.m3u8"

    def get(self, url: str, headers: Optional[dict] = None, timeout=None, **kw) -> _Resp:
        name = url.rsplit("/", 1)[-1].split("?")[0]
        if name == "master.m3u8":
            return _Resp(url, text=self.master)
        if name == "video.m3u8":
            return _Resp(url, text=self.media)
        if name == "media.mp4":
            rng = (headers or {}).get("Range", "")
            if rng.startswith("bytes="):
                a, b = (int(v) for v in rng[6:].split("-"))
                part = self.data[a:b + 1]
                self.served += len(part)
                return _Resp(url, content=part, status=206)
            self.served += len(self.data)
            return _Resp(url, content=self.data)
        return _Resp(url, status=404)


def segments(*pages: tuple[np.ndarray, int]) -> list[np.ndarray]:
    """Frames: each (picture, seconds) in turn."""
    return [p for p, n in pages for _ in range(n)]


def jpeg_gray(path: str) -> np.ndarray:
    import av

    with av.open(path) as c:
        f = next(c.decode(video=0))
    return f.reformat(width=W, height=H, format="gray").to_ndarray()


def which(path: str, candidates: dict[str, np.ndarray]) -> str:
    """The name of the candidate picture a saved JPEG shows (the closest one)."""
    g = jpeg_gray(path).astype(np.int16)
    return min(candidates, key=lambda k: float(np.mean(np.abs(g - candidates[k].astype(np.int16)))))
