"""Windows OCR for the screen timeline (ocr.py), without Windows' OCR itself: the part of the screen that is read
(the slide or the shared screen, not a meeting's chat, tiles or taskbar), what is left out of what was read (the
chat, notices, names on tiles, student numbers), the title, and that a PC without OCR makes the timeline anyway.
Where Windows has OCR, also that reading a slide never holds up the rest of the app."""

import threading
import time
import types

import numpy as np
import pytest

from studia import ocr
from studia.ocr import Line, clean, content_box, title_and_text


def _meeting(seed, slide_changes=True, chat_changes=False, tiles_change=False):
    """A 192x108 meeting window: tiles on top (rows 10-20), the shared screen (rows 28-100, cols 8-130), the chat on
    the right (cols 140-190)."""
    rng = np.random.default_rng(seed)
    img = np.full((108, 192), 240, np.int16)
    img[10:20, 8:130] = 120 + (40 * rng.integers(0, 2, (10, 122)) if tiles_change else 0)
    if slide_changes:
        img[28:100, 8:130] = np.where(rng.random((72, 122)) < 0.2, 20, 240)
    img[28:100, 140:190] = np.where(rng.random((72, 50)) < (0.15 if chat_changes else 0.0), 30, 230)
    return img


def test_the_part_read_is_the_shared_screen_not_the_chat_tiles_or_taskbar():
    frames = [_meeting(i, chat_changes=(i % 4 == 0), tiles_change=(i % 5 == 0)) for i in range(12)]
    x0, y0, x1, y1 = content_box(frames)
    assert 0.0 <= x0 <= 8 / 192 + 0.03 and 125 / 192 <= x1 <= 140 / 192        # not the chat
    assert 20 / 108 <= y0 <= 30 / 108 and y1 >= 96 / 108                       # not the tiles
    assert content_box(frames[:2]) is None                                    # too few to tell
    still = [np.full((108, 192), 200, np.int16)] * 5
    assert content_box(still) is None


def L(text, x, y, w=200.0, h=20.0):
    return Line(text, float(x), float(y), float(w), float(h))


def test_a_meetings_chat_notices_and_names_on_tiles_are_left_out():
    width = 1600
    pic = [
        L("Second order systems", 100, 300, 500, 40),
        L("If there are more than one energy storage", 100, 380, 600, 25),
        L("Ayşe Yılmaz 09:16", 1250, 300, 180, 18),            # the chat: its column goes
        L("hocam ses gelmiyor", 1250, 330, 160, 18),
        L("Mehmet Kaya 20250602258 toplantıya davet edildi.", 1250, 420, 300, 18),
        L("Ozgun", 230, 250, 40, 13), L("qaOan", 140, 250, 40, 13), L("Beren T _", 490, 250, 60, 13),   # tiles
        L("Lobide bekliyor", 300, 160, 90, 14), L("Fatih Akan 20240602003", 290, 190, 150, 14),
        L("Reddet", 340, 256, 40, 12), L("Ka I et", 420, 256, 40, 12),
        L("Jonatha…", 600, 250, 50, 13),
        L("then the resulting", 100, 420, 200, 25),
        L("e1 = Rc (Ra + Rb)", 700, 500, 220, 25),
    ]
    other = [L("Second order systems", 100, 300, 500, 40), L("Ayşe Yılmaz 09:20", 1250, 600, 180, 18),
             L("Solution of second order systems", 100, 340, 600, 40)]
    kept = clean([pic, other], width)
    texts = [ln.text for ln in kept[0]]
    assert texts == ["Second order systems", "If there are more than one energy storage", "then the resulting",
                     "e1 = Rc (Ra + Rb)"]
    assert [ln.text for ln in kept[1]] == ["Second order systems", "Solution of second order systems"]


def test_a_name_on_every_picture_and_window_titles_are_left_out_but_a_slides_words_stay():
    width = 1600
    pics = []
    for i in range(10):
        pics.append([L("EEE 215 Sections 2/3", 30, 12, 140, 12), L("YUSUF ERTEN", 20, 920, 120, 14),
                     L("Beren", 500 + 7 * (i % 3), 250, 40, 13),            # a tile that moves a little
                     L(f"Example {i}: find i1", 100, 300, 300, 30)]
                    + ([L("then the resulting", 100, 420, 200, 25), L("Vo", 300, 500, 30, 25)] if i < 2 else []))
    kept = clean(pics, width)
    assert [ln.text for ln in kept[0]] == ["Example 0: find i1", "then the resulting", "Vo"]
    assert all([ln.text for ln in k] == [f"Example {i}: find i1"] for i, k in enumerate(kept) if i >= 2)


def test_a_meetings_top_row_of_short_lines_is_left_out_and_a_slide_title_is_not():
    width = 1600
    meeting = [[L("Ayşe Yılmaz 09:16", 1250, 300, 180, 18), L("geren", 490, 250, 40, 13),
                L("Second order systems", 100, 330, 500, 40)]]
    assert [ln.text for ln in clean(meeting, width, area=(217.0, 950.0))[0]] == ["Second order systems"]
    slides = [[L("Example 13", 100, 40, 200, 40), L("Find vo", 100, 200, 200, 25)]]          # no meeting
    assert [ln.text for ln in clean(slides, width, area=(20.0, 900.0))[0]] == ["Example 13", "Find vo"]


def test_student_numbers_and_email_addresses_are_left_out():
    kept = clean([[L("Send it to 20250602227", 100, 100), L("ayse@std.ieu.edu.tr", 100, 200),
                   L("V = IR", 100, 300)]], 1600)
    assert [ln.text for ln in kept[0]] == ["V = IR"]


def test_title_and_text():
    lines = [L("Kirchhoff's laws", 100, 50, 400, 44), L("Sum of currents is zero", 100, 150, 400, 24),
             L("Sum of voltages", 100, 190, 300, 24)]
    assert title_and_text(lines) == ("Kirchhoff's laws", "Sum of currents is zero\nSum of voltages")
    same = [L("a b c", 100, 50, 100, 24), L("d e f", 100, 90, 100, 24)]
    assert title_and_text(same) == ("", "a b c\nd e f")
    assert title_and_text([]) == ("", "")


def test_read_screens_reads_the_part_that_changes_of_the_pictures_asked(monkeypatch):
    frames = {f"p{i}.jpg": _meeting(i) for i in range(6)}
    seen = []

    def gray(path, width=None):
        g = frames[path].astype(np.uint8)
        if width == 192:
            return g
        return np.repeat(np.repeat(g, 8, axis=0), 8, axis=1)          # 1536 x 864

    def read(crop):
        seen.append(crop.shape)
        return [Line("A title", 10, 10, 300, 40), Line("Some text", 10, 80, 200, 20)], "windows en-US"

    monkeypatch.setattr(ocr, "_gray", gray)
    monkeypatch.setattr(ocr, "read_lines", read)
    monkeypatch.setattr(ocr, "max_side", lambda: 10000)
    got = ocr.read_screens(list(frames), {"p2.jpg", "p4.jpg"})
    assert sorted(got) == ["p2.jpg", "p4.jpg"]
    assert got["p2.jpg"] == ocr.Reading("", "A title\nSome text", "windows en-US")      # (no titles from OCR)
    h, w = seen[0]
    assert w < 1536 * 0.75 and h < 864 * 0.9                         # only the shared screen was read


def test_without_windows_ocr_the_timeline_is_made_without_words(monkeypatch):
    monkeypatch.setattr(ocr, "_state", {})
    monkeypatch.setattr(ocr.sys, "platform", "linux")
    assert ocr.available() is False and ocr.languages() == []
    from studia import screens

    monkeypatch.setattr(screens, "_under_test", lambda: False)
    assert screens.default_reader() is None


@pytest.mark.skipif(not ocr.available(), reason="Windows OCR isn't here")
def test_windows_ocr_answers_on_this_pc():
    lines, engine = ocr.read_lines(np.full((200, 400), 255, np.uint8))
    assert lines == [] and engine.startswith("windows ")


def _drawn_slide(path, lines):
    """A 1600x900 JPEG slide with these lines on it, drawn by Windows (System.Drawing)."""
    import clr

    clr.AddReference("System.Drawing")
    from System.Drawing import Bitmap, Brushes, Color, Font, Graphics
    from System.Drawing.Imaging import ImageFormat

    bmp = Bitmap(1600, 900)
    g = Graphics.FromImage(bmp)
    g.Clear(Color.White)
    for i, text in enumerate(lines):
        g.DrawString(text, Font("Arial", 40.0), Brushes.Black, 80.0, 80.0 + 100.0 * i)
    bmp.Save(str(path), ImageFormat.Jpeg)
    g.Dispose()
    bmp.Dispose()
    return path


@pytest.mark.skipif(not ocr.available(), reason="Windows OCR isn't here")
def test_reading_a_slide_never_holds_up_the_app(tmp_path):
    """While old lectures' screens were read in the background after an update, every click waited 0.3-1 s: the
    picture went to Windows' OCR through pythonnet's own conversion (Array[Byte](bytes)), byte by byte, holding
    Python's lock all the while. It now goes in one copy; a thread beside it never waits long."""
    gray = ocr._gray(str(_drawn_slide(tmp_path / "slide.jpg", ["Kirchhoff's voltage law",
                                                                "The sum of the voltages around a loop is zero"])))
    assert gray.shape == (900, 1600)
    ocr.read_lines(gray)                                     # (the first reading sets things up)
    gaps, stop = [], threading.Event()

    def beside():                                            # what the page's calls do meanwhile: wait for the lock
        last = time.perf_counter()
        while not stop.is_set():
            time.sleep(0.002)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    other = threading.Thread(target=beside, daemon=True)
    other.start()
    try:
        lines, _ = ocr.read_lines(gray)
    finally:
        stop.set()
        other.join()
    assert "voltage" in " ".join(ln.text for ln in lines).lower()
    assert max(gaps) < 0.15, f"the other thread waited {max(gaps) * 1000:.0f} ms"
