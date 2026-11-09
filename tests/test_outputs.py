import datetime as dt
import sys

import pytest

from studia.domain import Course, Recording, Segment, Transcript
from studia.outputs.ai_copy import build_ai_text
from studia.outputs.highlights import detect
from studia.outputs.markdown import render_day
from studia.outputs.paragraphs import build_paragraphs
from studia.outputs.srt import render_srt
from studia.outputs.writer import course_folder_name, safe_name, write_day

COURSE = Course(id="c1", title="t", code="MATH 240", name="Probability", instructor="Dr. X")


def rec(did, h, m, dur=2400.0):
    return Recording(delivery_id=did, course_id="c1", folder_id="f", folder_path="", name="n",
                     start=dt.datetime(2026, 10, 14, h, m), duration_s=dur, viewer_url=f"https://v/{did}")


def tr(did, segs):
    return Transcript(delivery_id=did, segments=segs, duration_s=600, speech_s=500, language_seconds={},
                      model="large-v3-turbo", device="cuda (RTX)", engine_version="2.0",
                      created_at="2026-10-20T12:00:00")


SEGS = [Segment(0, 5, "So good morning everyone.", "en", 0.9),
        Segment(5.5, 9, "Today we start Bayes' theorem.", "en", 0.9),
        Segment(15, 19, "Arkadaşlar, bu konu sınavda çıkacak, dikkat edin.", "tr", 0.9),
        Segment(19.5, 25, "This will definitely be on the midterm.", "en", 0.9),
        Segment(30, 34, "[unclear]", "en", 0.3)]


def test_paragraphs_split_on_language_and_gap():
    paras = build_paragraphs(SEGS)
    assert [p.lang for p in paras][:3] == ["en", "tr", "en"]
    assert paras[0].text == "So good morning everyone. Today we start Bayes' theorem."
    assert paras[1].star and "exam" in paras[1].tags
    assert paras[2].star


def test_highlight_precision():
    assert detect("This will be on the midterm, so be careful.") == ["exam", "important"]
    assert detect("Ödevinizi haftaya teslim edin.", "tr") == ["homework", "schedule"]
    assert detect("The final answer is 42.") == []
    assert detect("We use the projection matrix here.") == []
    assert detect("Projeksiyon matrisini kullanıyoruz.", "tr") == []


def test_render_day_with_missing_part():
    md = render_day(COURSE, [rec("a", 8, 28), rec("b", 9, 33)], {"a": tr("a", SEGS)})
    assert md.startswith("# MATH 240 · Lecture · Wednesday, 14 October 2026")
    assert "**[TR]**" in md and "## ⭐ Highlights" in md and "_Not transcribed yet._" in md
    assert "[Part 2](https://v/b)" in md and "*[unclear]*" in md


def test_srt_and_ai_text():
    srt = render_srt(SEGS[:3])
    assert srt.startswith("1\n00:00:00,000 --> 00:00:05,000\nSo good morning everyone.")
    assert "[TR] Arkadaşlar" in srt
    text = build_ai_text("notes", COURSE, [([rec("a", 8, 28)], {"a": tr("a", SEGS)})])
    assert "study notes" in text.lower() and "[00:15] [TR] ⭐ Arkadaşlar" in text


def test_writer_names_and_files(tmp_path):
    assert safe_name('a<b>:c"/d|e?*') == "abcde"
    twins = [Course(id="1", title="", code="FENG 495", name="x", section="1"),
             Course(id="2", title="", code="FENG 495", name="x", section="3")]
    assert course_folder_name(twins[1], twins) == "FENG 495 (sec 3)"
    paths = write_day(tmp_path, COURSE, [COURSE], [rec("a", 8, 28)], {"a": tr("a", SEGS)},
                      [rec("a", 8, 28)], {"a": tr("a", SEGS)})
    assert paths["md"].name == "MATH 240 - 2026-10-14 Wed - Lecture.md" and paths["md"].exists()
    assert paths["pack"].exists() and (tmp_path / "PROMPTS.md").exists()
    assert (paths["folder"] / "subtitles" / "MATH 240 - 2026-10-14 Wed - Lecture - Part 1.srt").exists()


def test_a_file_held_open_for_a_moment_is_still_replaced(tmp_path, monkeypatch):
    import os

    import pytest

    from studia.outputs import writer

    target = tmp_path / "a.md"
    target.write_text("old", encoding="utf-8")
    real, tries = os.replace, []

    def busy_twice(a, b):
        tries.append(1)
        if len(tries) < 3:
            raise PermissionError(13, "Access is denied")       # e.g. OneDrive or an antivirus has it open
        return real(a, b)

    monkeypatch.setattr(writer.time, "sleep", lambda s: None)
    monkeypatch.setattr(writer.os, "replace", busy_twice)
    writer.atomic_write(target, "new")
    assert target.read_text(encoding="utf-8") == "new" and len(tries) == 3

    def always(a, b):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(writer.os, "replace", always)
    with pytest.raises(PermissionError):
        writer.atomic_write(target, "newer")
    assert target.read_text(encoding="utf-8") == "new" and not (tmp_path / "a.md.tmp").exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows paths")
def test_two_local_files_of_the_same_name_keep_both_transcripts(tmp_path):
    from studia.outputs.writer import write_local

    t = Transcript(delivery_id="local-x", segments=[Segment(0, 2, "Hello.", "en", 0.9)], duration_s=2, speech_s=2,
                   language_seconds={"en": 2}, model="m", device="cpu", engine_version="t", created_at="x")
    a = write_local(tmp_path, r"C:\Videos\EEE\Lecture.mp4", t)
    b = write_local(tmp_path, r"C:\Videos\MATH\Lecture.mp4", t)
    assert (a.name, b.name) == ("Lecture.md", "Lecture (MATH).md")
    assert write_local(tmp_path, r"C:\Videos\EEE\Lecture.mp4", t) == a        # the same file again: same transcript


@pytest.mark.skipif(sys.platform != "win32", reason="Windows paths")
def test_a_transcript_written_by_an_earlier_version_is_kept(tmp_path):
    from studia.outputs.writer import write_local

    t = Transcript(delivery_id="local-x", segments=[Segment(0, 2, "Hello.", "en", 0.9)], duration_s=2, speech_s=2,
                   language_seconds={"en": 2}, model="m", device="cpu", engine_version="t", created_at="x")
    (tmp_path / "Local files").mkdir()
    (tmp_path / "Local files" / "Lecture.md").write_text("# Lecture\n\nTranscribed 2026-10-01 with IEUStudia.\n",
                                                         encoding="utf-8")      # 2.1.1: no Source line
    assert write_local(tmp_path, r"C:\Videos\Lecture.mp4", t).name == "Lecture (Videos).md"   # it may be another
    assert sorted(p.name for p in (tmp_path / "Local files").iterdir()) == ["Lecture (Videos).md", "Lecture.md"]
    assert write_local(tmp_path, r"C:\Videos\Lecture.mp4", t).name == "Lecture (Videos).md"   # no third copy


def test_the_ai_text_leaves_out_paragraphs_with_nothing_in_them():
    from studia.outputs.ai_copy import compact_day

    segs = SEGS + [Segment(40, 41, "Stay tuned!", "en", 0.3),                      # unsure, two words
                   Segment(45, 46, "Quiz next week!", "en", 0.3),                  # unsure but emphasised: kept
                   Segment(50, 58, "Now the second example, with numbers.", "en", 0.9)]
    text = compact_day(COURSE, [rec("a", 8, 28)], {"a": tr("a", segs)})
    assert "[unclear]" not in text and "Stay tuned" not in text
    assert "⭐ ⚠ Quiz next week!" in text and "second example" in text and "[00:15] [TR] ⭐ Arkadaşlar" in text


def test_an_ai_pack_captions_a_slide_shown_twice_with_the_time_of_each_showing():
    """Slide 9 shown at 18:27 and again at 28:21 was captioned "on screen from 28:21" above the passages of 18:46."""
    import re

    from studia.export import render_pack_html
    from studia.slides import Slide

    segs = [Segment(s, s + 20, f"Passage at {s}.", "en", 0.9) for s in (10, 110, 210, 330, 420)]
    shots = [Slide(1, 0.0, 100.0, "s/1.jpg", "C:/x/1.jpg"), Slide(2, 100.0, 200.0, "s/2.jpg", "C:/x/2.jpg"),
             Slide(1, 200.0, 300.0, "s/1.jpg", "C:/x/1.jpg"),                     # slide 1 shown again
             Slide(3, 300.0, 320.0, "s/3.jpg", "C:/x/3.jpg"),
             Slide(3, 320.0, 400.0, "s/3.jpg", "C:/x/3.jpg"),                     # its next build-up step
             Slide(2, 400.0, 500.0, "s/2.jpg", "C:/x/2.jpg")]
    page = render_pack_html(COURSE, [rec("a", 8, 28)], {"a": tr("a", segs)}, {"a": shots}, {})
    captions = re.findall(r"<figcaption>Slide (\d+) · on screen from ([\d:]+)</figcaption>", page)
    assert captions == [("1", "00:00"), ("2", "01:40"), ("1", "03:20"), ("3", "05:00"), ("2", "06:40")]
    assert page.index("03:20") < page.index("Passage at 210.") < page.index("05:00") < page.index("Passage at 330.")
