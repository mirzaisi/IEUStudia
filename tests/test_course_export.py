"""Export for an AI chat (course_export.py and the API): the zip's folders and files for the weeks picked or everything
up to now, all courses in one zip, course files not on this PC (downloaded first, or listed with why), links listed,
a unit for two weeks in both, two files of one name kept apart, long Turkish names within the path limit, Cancel and
a failure leaving nothing behind, an earlier zip open in another program, a disk too full; the API: one export at a
time, its events, and Show in folder only for a zip in the IEUStudia folder."""

import datetime as dt
import errno
import os
import re
import sys
import threading
import time
import types
import zipfile

import pytest

from studia import course_export as ce
from studia import store
from studia.api import Api
from studia.cancel import Cancelled
from studia.config import Settings
from studia.coursesite import Announcement, Resource, Unit
from studia.domain import Course, Recording, Segment, Transcript
from studia.library import Library
from studia.materials import MaterialStore
from studia.net import NetworkError
from studia.slides import Slide

TODAY = dt.date(2026, 10, 8)              # week 3
LONG_TR = ("Kesikli tekdüze, Binom, Negatif Binom, Hipergeometrik, Poisson dağılımları ve uygulamaları (aşı başarısı, "
           "epidemiler, bulaşıcı hastalıklar, trafik kazaları, çevre kirliliği) İŞÇİOĞLU")


def _file(path, data=b"%PDF-1.7 data"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path)


def _transcribe(lib, rec, texts):
    segs = [Segment(i * 30.0, i * 30.0 + 25, x, "tr" if "ğ" in x or "ş" in x else "en", 0.9) for i, x in enumerate(texts)]
    tr = Transcript(delivery_id=rec.delivery_id, segments=segs, duration_s=rec.duration_s, speech_s=60,
                    language_seconds={"en": 60}, model="m", device="cpu", engine_version="t",
                    created_at="2026-10-01T12:00:00")
    lib.save_transcript(tr, store.save_transcript(tr))
    lib.set_status(rec.delivery_id, "done")


@pytest.fixture
def env(tmp_path):
    lib = Library(tmp_path / "lib.db")
    files = tmp_path / "files"
    lib.upsert_courses([Course(id="c", title="2026-1 MATH 240 Olasılık 1", code="MATH 240", name="Mühendisler için Olasılık",
                               term="2026-2027 Fall", instructor="Dr Özkut"),
                        Course(id="e", title="2026-1 EEE 215 Devreler 1", code="EEE 215", name="Devreler", term="2026-2027 Fall")])
    lib.merge_units("c", [
        Unit("u1", 0, "Hafta 1", dt.date(2026, 9, 21), dt.date(2026, 9, 27), "Read chapter 1", [
            Resource("r1", "Week 1 slides", "Week01.pdf", "pdf", "pdf", "u1", "/d/r1"),
            Resource("r2", "Week 1 extra", "Extra.pdf", "pdf", "pdf", "u2", "/d/r2"),
            Resource("r3", "Simulation video", "", "link", "", "https://www.youtube.com/watch?v=abc", "")]),
        Unit("u2", 1, "Hafta 2", dt.date(2026, 9, 28), dt.date(2026, 10, 4), "", [
            Resource("r4", "Slides A", "Slides.pdf", "pdf", "pdf", "u4", "/d/r4"),
            Resource("r5", "Slides B", "slides.PDF", "pdf", "pdf", "u5", "/d/r5")]),
        Unit("u3", 2, "Hafta 3 ve 4", None, None, "", [
            Resource("r6", "Two weeks", "Hafta3-4.pdf", "pdf", "pdf", "u6", "/d/r6")]),
        Unit("u5", 3, LONG_TR, dt.date(2026, 10, 19), dt.date(2026, 10, 25), "", [
            Resource("r7", LONG_TR, LONG_TR + ".pdf", "pdf", "pdf", "u7", "/d/r7")]),
        Unit("ug", 4, "Formula Sheet", None, None, "", [
            Resource("r8", "Formula sheet", "Formül sayfası.pdf", "pdf", "pdf", "u8", "/d/r8")]),
    ], first_sync=True)
    for rid, name in (("r1", "Week01.pdf"), ("r4", "a/Slides.pdf"), ("r5", "b/slides.PDF"), ("r6", "Hafta3-4.pdf"),
                      ("r7", "long.pdf"), ("r8", "Formül sayfası.pdf")):
        lib.set_material_file(rid, _file(files / name), 13, "u")
    lib.set_course_info("c", syllabus={
        "name": "Probability for Engineers", "outcomes": ["use counting", "explain probability"],
        "grading": [{"activity": "Quizzes", "number": 4, "weight": 20.0}, {"activity": "Final Exam", "number": 1, "weight": 50.0}],
        "weeks": [{"no": n, "subject": s, "preparation": ""} for n, s in
                  ((1, "Sample Spaces"), (2, "Counting"), (3, "Bayes"), (4, "Random Variables"), (5, "Poisson"),
                   (8, "Midterm Exam"))]})
    lib.merge_announcements("c", [
        Announcement("a1", "About Quiz 1", "Dr Özkut", dt.datetime(2026, 9, 23, 10, 0),
                     "Quiz 1 will be held in week 3. It covers Learning Outcome 1."),
        Announcement("a2", "Room change", "Dr Özkut", dt.datetime(2026, 10, 1, 9, 30), "We meet in A201 from now on.")],
        first_sync=True)
    recs = [Recording(delivery_id=did, course_id="c", folder_id="f", folder_path="", name=f"MATH 240 {did}",
                      start=start, duration_s=3000, viewer_url=f"https://panopto/{did}")
            for did, start in (("d1", dt.datetime(2026, 9, 23, 8, 30)), ("d2", dt.datetime(2026, 9, 30, 8, 30)),
                               ("d3", dt.datetime(2026, 10, 7, 8, 30)), ("d3b", dt.datetime(2026, 10, 7, 9, 30)))]
    lib.merge_recordings("c", recs, first_sync=True)
    _transcribe(lib, recs[0], ["Today: sample spaces.", "Next week we have a quiz on counting.", "Bu bir değişken."])
    _transcribe(lib, recs[2], ["Bayes' rule today.", "The midterm covers weeks one to seven."])
    shots = tmp_path / "shots"
    store.save_slides("d1", [Slide(1, 0.0, 20.0, "s/1.jpg", _file(shots / "1.jpg", b"\xff\xd8\xff\xe0 one")),
                             Slide(2, 20.0, 50.0, "s/2.jpg", _file(shots / "2.jpg", b"\xff\xd8\xff\xe0 two")),
                             Slide(1, 50.0, 90.0, "s/1.jpg", str(shots / "1.jpg"))])
    # EEE 215: one lecture, one file
    lib.merge_units("e", [Unit("eu1", 0, "Hafta 1 Intro", dt.date(2026, 9, 21), dt.date(2026, 9, 27), "", [
        Resource("e1", "Intro", "Intro.pdf", "pdf", "pdf", "e1", "/d/e1")])], first_sync=True)
    lib.set_material_file("e1", _file(files / "Intro.pdf"), 13, "u")
    erec = Recording(delivery_id="x1", course_id="e", folder_id="f", folder_path="", name="EEE 215 lecture",
                     start=dt.datetime(2026, 9, 22, 10, 0), duration_s=2400, viewer_url="https://panopto/x1")
    lib.merge_recordings("e", [erec], first_sync=True)
    _transcribe(lib, erec, ["Ohm's law."])
    return types.SimpleNamespace(lib=lib, settings=Settings(), out=tmp_path / "out", tmp=tmp_path, files=files)


def build(env, course_id="c", today=TODAY, **kw):
    """The export with these choices (``download`` is the downloader; ``download_first`` the choice)."""
    fields = {k: kw.pop(k) for k in list(kw) if k in ("weeks", "transcripts", "screens", "files", "notices")}
    if "download_first" in kw:
        fields["download"] = kw.pop("download_first")
    return ce.build(env.lib, env.settings, env.out, course_id, ce.Choice(**fields), today=today, **kw)


def entries(path):
    with zipfile.ZipFile(path) as z:
        return {i.filename: i for i in z.infolist()}


def read(path, name):
    with zipfile.ZipFile(path) as z:
        return z.read(name).decode("utf-8")


ROOT = "MATH 240 - everything up to 8 Oct 2026"


# ------------------------------------------------------------------ the zip's structure
def test_everything_up_to_now_has_a_folder_per_week_with_transcripts_screens_and_files(env):
    out = build(env)
    path = env.out / "MATH 240" / "Exports" / f"{ROOT}.zip"
    assert out["path"] == str(path) and path.is_file() and out["bytes"] == path.stat().st_size and not out["renamed"]
    names = entries(path)
    assert all(n.startswith(ROOT + "/") for n in names)
    rel = {n[len(ROOT) + 1:] for n in names}
    w1 = "Week 01 - Sample Spaces"
    assert {"README.md", "Index of files.md", "Course overview.md", "Announcements.md",
            "Quizzes, exams and deadlines.md", f"{w1}/Week overview.md",
            f"{w1}/Lectures/2026-09-23 Wed - Lecture/Transcript.md",
            f"{w1}/Lectures/2026-09-23 Wed - Lecture/Transcript by screen.md",
            f"{w1}/Lectures/2026-09-23 Wed - Lecture/Screens/Part 1 - Screen 01.jpg",
            f"{w1}/Lectures/2026-09-23 Wed - Lecture/Screens/Part 1 - Screen 02.jpg",
            f"{w1}/Course files/Week01.pdf", "Week 02 - Counting/Week overview.md",
            "Week 03 - Bayes/Lectures/2026-10-07 Wed - Lecture/Transcript.md",
            "General course files/Formula Sheet/Formül sayfası.pdf"} <= rel
    # weeks begun by today only, a screen shown twice is one picture, pictures are stored as they are
    assert not any(r.startswith(("Week 04", "Week 05", "Week 08")) for r in rel)
    assert sum("/Screens/" in r for r in rel) == 2
    assert names[f"{ROOT}/{w1}/Lectures/2026-09-23 Wed - Lecture/Screens/Part 1 - Screen 01.jpg"].compress_type == zipfile.ZIP_STORED
    assert out["lectures"] == 2 and out["screens"] == 2 and out["weeks"] == [1, 2, 3]
    # the transcript with the screen marks and the legend; what was said on each screen, the screen shown again too
    text = read(path, f"{ROOT}/{w1}/Lectures/2026-09-23 Wed - Lecture/Transcript.md")
    assert "[00:30] (slide 2) ⭐ Next week we have a quiz on counting." in text and "[TR]" in text and "How to read this" in text
    by = read(path, f"{ROOT}/{w1}/Lectures/2026-09-23 Wed - Lecture/Transcript by screen.md")
    assert "## Part 1, screen 2 (00:20 to 00:50)\nPicture: Screens/Part 1 - Screen 02.jpg" in by
    assert "[00:30] ⭐ Next week we have a quiz on counting." in by.split("screen 2 (00:20")[1].split("## ")[0]
    assert "## Part 1, screen 1 (00:50 to 01:30), shown again\nPicture: Screens/Part 1 - Screen 01.jpg" in by
    assert "[01:00] [TR] Bu bir değişken." in by.split("shown again")[1]
    # the index lists every file (with itself), the README says what is inside and the first message
    index = read(path, f"{ROOT}/Index of files.md")
    for r in rel:
        folder, _, name = r.rpartition("/")
        assert f"## {ROOT}/{folder}".rstrip("/") in index and f"- `{name}`:" in index, r
    readme = read(path, f"{ROOT}/README.md")
    assert readme.startswith("# MATH 240 · Probability for Engineers: everything up to 8 October 2026")
    assert "Drag this zip into the chat" in readme and "> This zip holds my MATH 240 course" in readme
    assert out["first_message"] in readme and out["first_message"].endswith("My first question:")


def test_the_overview_announcements_and_deadlines_are_written_readably(env):
    path = build(env)["path"]
    ov = read(path, f"{ROOT}/Course overview.md")
    assert "# MATH 240 · Probability for Engineers" in ov and "Name on IEULearn: Mühendisler için Olasılık" in ov
    assert "- LO1: use counting" in ov and "- Quizzes: 20% in all (4 of them)" in ov and "- Final Exam: 50%" in ov
    assert "- * Week 1 (Mon 21 Sep to Sun 27 Sep 2026) · Sample Spaces" in ov and "- Week 8" in ov
    ann = read(path, f"{ROOT}/Announcements.md")
    assert ann.index("## Room change") < ann.index("## About Quiz 1")                # newest first
    assert "Wed 23 Sep 2026, 10:00 · Dr Özkut" in ann
    dl = read(path, f"{ROOT}/Quizzes, exams and deadlines.md")
    assert "- Quiz 1: week 3 (Mon 5 Oct to Sun 11 Oct 2026, this week). It covers LO1. As announced in “About Quiz 1”" in dl
    assert "  - LO1: use counting" in dl
    assert "Week 8" in dl and "Midterm Exam" in dl                                   # the syllabus' exam week
    assert "in class (lecture, part 1, 00:30): “Next week we have a quiz on counting.”" in dl
    assert "{'kind'" not in dl and "'los'" not in dl                                  # (no raw fields)
    week1 = read(path, f"{ROOT}/Week 01 - Sample Spaces/Week overview.md")
    assert "Dates: Mon 21 Sep to Sun 27 Sep 2026" in week1 and "Readings: Read chapter 1" in week1
    assert "- 23 Sep: About Quiz 1" in week1


def test_lectures_not_transcribed_are_listed_in_their_week_and_the_readme(env):
    path = build(env)["path"]
    week2 = read(path, f"{ROOT}/Week 02 - Counting/Week overview.md")
    assert "- Wed 30 Sep · Lecture · 50 min: not transcribed in IEUStudia yet." in week2
    assert not any("Week 02 - Counting/Lectures" in n for n in entries(path))
    week3 = read(path, f"{ROOT}/Week 03 - Bayes/Week overview.md")
    assert "2 parts" in week3 and "Not transcribed yet: part 2." in week3
    readme = read(path, f"{ROOT}/README.md")
    assert "- Week 2, Wed 30 Sep (Lecture): not transcribed in IEUStudia yet." in readme
    assert "- Week 3, Wed 7 Oct (Lecture): part 2 is not transcribed yet." in readme


def test_picked_weeks_only_and_the_name_says_which(env):
    out = build(env, weeks=[2, 4, 5])
    assert out["name"] == "MATH 240 - weeks 2, 4-5 - 8 Oct 2026.zip" and out["weeks"] == [2, 4, 5]
    rel = {n.split("/", 1)[1] for n in entries(out["path"])}
    assert {r.split("/")[0] for r in rel if r.startswith("Week")} == {
        "Week 02 - Counting", "Week 04 - Random Variables", "Week 05 - Poisson"}
    assert "README.md" in rel and "Course overview.md" in rel
    assert build(env, weeks=[3])["name"] == "MATH 240 - week 3 - 8 Oct 2026.zip"
    assert build(env, weeks=list(range(1, 17)))["name"] == "MATH 240 - all weeks - 8 Oct 2026.zip"
    with pytest.raises(ce.ExportError, match="Pick at least one week"):
        build(env, weeks=[])


def test_what_to_put_in_leaves_the_rest_out(env):
    out = build(env, screens=False, files=False, notices=False)
    rel = {n.split("/", 1)[1] for n in entries(out["path"])}
    assert not any("/Screens/" in r or "Transcript by screen" in r or r.endswith(".pdf") for r in rel)
    assert "Announcements.md" not in rel and "Quizzes, exams and deadlines.md" not in rel
    text = read(out["path"], f"{ROOT}/Week 01 - Sample Spaces/Lectures/2026-09-23 Wed - Lecture/Transcript.md")
    assert "(slide" not in text and "Transcript by screen.md" not in text
    week1 = read(out["path"], f"{ROOT}/Week 01 - Sample Spaces/Week overview.md")
    assert "- Week01.pdf (course files were left out of this export)" in week1
    msg = out["first_message"]
    assert "lecture transcripts" in msg and "screens" not in msg and "course files" not in msg


def test_all_courses_are_one_zip_with_a_folder_per_course(env):
    out = build(env, course_id="")
    assert out["name"] == "All courses - everything up to 8 Oct 2026.zip"
    assert out["path"] == str(env.out / "Exports" / out["name"]) and out["courses"] == 2
    root = "All courses - everything up to 8 Oct 2026"
    rel = {n[len(root) + 1:] for n in entries(out["path"])}
    for c in ("MATH 240", "EEE 215"):
        assert {f"{c}/README.md", f"{c}/Course overview.md"} <= rel
    assert "EEE 215/Week 01 - Hafta 1 Intro/Lectures/2026-09-22 Tue - Lecture/Transcript.md" in rel
    assert "MATH 240/Week 01 - Sample Spaces/Course files/Week01.pdf" in rel
    assert {"README.md", "Index of files.md"} <= rel
    top = read(out["path"], f"{root}/README.md")
    said = top.split("> This zip holds my courses (")[1].split(")")[0]
    assert sorted(said.split(", ")) == ["EEE 215", "MATH 240"] and "- MATH 240/:" in top and "- EEE 215/:" in top
    course = read(out["path"], f"{root}/MATH 240/README.md")
    assert "Drag this zip" not in course and "## Weeks" in course
    picked = build(env, course_id="", weeks=[1])
    assert picked["name"] == "All courses - week 1 - 8 Oct 2026.zip"
    weeks = {n.split("/")[2] for n in entries(picked["path"]) if n.count("/") > 2 and n.split("/")[2].startswith("Week")}
    assert weeks == {"Week 01 - Sample Spaces", "Week 01 - Hafta 1 Intro"}


# ------------------------------------------------------------------ course files, links, names
def test_files_not_on_this_pc_are_listed_with_why_or_downloaded_first(env):
    out = build(env)                                              # nothing to download them with: listed
    week1 = read(out["path"], f"{ROOT}/Week 01 - Sample Spaces/Week overview.md")
    assert "- Extra.pdf: not downloaded in IEUStudia, so it isn't in this zip." in week1
    assert "- Week 1: Extra.pdf isn't downloaded in IEUStudia." in read(out["path"], f"{ROOT}/README.md")
    assert out["missing"] == 1
    got, events = [], []

    def download(rid, progress):
        got.append(rid)
        progress(5, 10)
        path = _file(env.files / "new" / "Extra.pdf", b"%PDF-1.7 extra")
        env.lib.set_material_file(rid, path, 14, "u")
        return path

    out = build(env, download=download, progress=events.append)
    assert got == ["r2"] and out["missing"] == 0
    assert f"{ROOT}/Week 01 - Sample Spaces/Course files/Extra.pdf" in entries(out["path"])
    steps = [e for e in events if e.get("step") == "download"]
    assert steps[0]["i"] == 1 and steps[0]["n"] == 1 and steps[0]["name"] == "Extra.pdf"
    assert any(e.get("done") == 5 and e.get("total") == 10 for e in steps)


def test_a_file_that_fails_says_why_and_no_connection_stops_the_rest(env):
    env.lib.set_material_file("r4", "", 0, "")                   # two files to download
    calls = []

    def boom(rid, progress):
        calls.append(rid)
        raise RuntimeError("IEULearn didn't let IEUStudia download this file.")

    out = build(env, weeks=[1, 2], download=boom)
    readme = read(out["path"], "MATH 240 - weeks 1-2 - 8 Oct 2026/README.md")
    assert "couldn't be downloaded: IEULearn didn't let IEUStudia download this file." in readme
    assert calls == ["r2", "r4"] and out["missing"] == 2
    calls.clear()

    def offline(rid, progress):
        calls.append(rid)
        raise NetworkError("No internet connection.")

    out = build(env, weeks=[1, 2], download=offline)
    assert calls == ["r2"] and out["missing"] == 2                   # the second waits no longer
    assert read(out["path"], "MATH 240 - weeks 1-2 - 8 Oct 2026/README.md").count("No internet connection.") == 2


def test_links_are_listed_with_their_address_not_as_files(env):
    out = build(env)
    week1 = read(out["path"], f"{ROOT}/Week 01 - Sample Spaces/Week overview.md")
    assert "- Link (not a file): Simulation video <https://www.youtube.com/watch?v=abc>" in week1
    assert out["links"] == 1 and not any("Simulation" in n for n in entries(out["path"]))
    assert "1 link (a web page or a video) is listed in its week's overview" in read(out["path"], f"{ROOT}/README.md")


def test_a_unit_for_two_weeks_is_in_both(env):
    out = build(env, weeks=[3, 4])
    root = "MATH 240 - weeks 3-4 - 8 Oct 2026"
    names = entries(out["path"])
    assert f"{root}/Week 03 - Bayes/Course files/Hafta3-4.pdf" in names
    assert f"{root}/Week 04 - Random Variables/Course files/Hafta3-4.pdf" in names


def test_a_file_of_a_unit_for_two_weeks_is_counted_once(env):
    """The README said "13 course files" for a course with 10: the copies of a two-week unit's files were counted."""
    out = build(env, weeks=[3, 4])
    readme = read(out["path"], "MATH 240 - weeks 3-4 - 8 Oct 2026/README.md")
    assert out["files"] == 2 and "and 2 course files." in readme              # Hafta3-4.pdf and the formula sheet
    assert readme.count("· 1 course file") == 2                                 # each week holds its copy
    every = build(env, course_id="", weeks=[3, 4])
    top = read(every["path"], "All courses - weeks 3-4 - 8 Oct 2026/README.md")
    assert every["files"] == 2 and "- MATH 240/: MATH 240 · Probability for Engineers · 1 lecture · 2 course files" in top


def test_a_recording_with_no_speech_is_not_called_not_transcribed_yet(env):
    """Panopto's recording of 30 Sep had no sound: the README told ChatGPT it was "not transcribed in IEUStudia yet"
    and the dialog counted it, though there is nothing to transcribe."""
    env.lib.set_status("d2", "failed", "No speech was found (only noise or music).")
    env.lib.set_status("d3b", "failed", "")
    env.lib.set_warnings("d3b", [{"code": "no_speech", "level": "warning", "message": "No sound on Panopto."}])
    out = build(env)
    assert out["untranscribed"] == 0
    week2 = read(out["path"], f"{ROOT}/Week 02 - Counting/Week overview.md")
    assert "- Wed 30 Sep · Lecture · 50 min: Panopto's recording of it has no speech (no sound, or only noise or " \
           "music), so there is no transcript; use the week's course files for it." in week2
    week3 = read(out["path"], f"{ROOT}/Week 03 - Bayes/Week overview.md")
    assert "Part 2 has no speech on Panopto's recording, so no transcript." in week3 and "Not transcribed yet" not in week3
    readme = read(out["path"], f"{ROOT}/README.md")
    assert "not transcribed" not in readme
    assert "- Week 2, Wed 30 Sep (Lecture): Panopto's recording has no speech (no sound, or only noise or music), so " \
           "there is nothing to transcribe." in readme
    assert "- Week 3, Wed 7 Oct (Lecture): part 2 has no speech on Panopto's recording, so it has no transcript." in readme
    text = read(out["path"], f"{ROOT}/Week 03 - Bayes/Lectures/2026-10-07 Wed - Lecture/Transcript.md")
    assert "Part 2 has no speech on Panopto's recording, so no transcript (no sound, or only noise or music)." in text
    o = ce.options(env.lib, env.settings, env.out, "c", today=TODAY)
    assert all(w["untranscribed"] == 0 for w in o["weeks"])                     # the dialog says nothing is left


def test_the_weeks_topic_on_ieulearn_is_labelled_by_where_it_comes_from(env):
    """"in Turkish:" labelled English text, and the same topic again with a full stop added."""
    env.lib.set_course_info("c", overview={"weeks": [{"no": 1, "topic": "Sample spaces."}, {"no": 2, "topic": "Sayma"},
                                                     {"no": 3, "topic": "Random Variables"}]})
    out = build(env)
    ov = read(out["path"], f"{ROOT}/Course overview.md")
    assert "Week 1 (Mon 21 Sep to Sun 27 Sep 2026) · Sample Spaces\n" in ov             # the same words: once
    assert "· Counting · on IEULearn: Sayma" in ov and "· Bayes · on IEULearn: Random Variables" in ov
    assert "in Turkish" not in ov
    week2 = read(out["path"], f"{ROOT}/Week 02 - Counting/Week overview.md")
    assert "Topic on IEULearn: Sayma" in week2 and "Topic in Turkish" not in week2
    assert "Topic on IEULearn" not in read(out["path"], f"{ROOT}/Week 01 - Sample Spaces/Week overview.md")


STUDENTS = "\n".join(f"{20210600000 + 37 * n} {60 + n % 40} {55 + n % 45} 22.09.2026" for n in range(30))


def test_a_list_of_students_is_told_by_what_it_holds():
    assert ce.lists_students([STUDENTS])
    assert not ce.lists_students(["\n".join(f"{20210600000 + n} 85" for n in range(19))])         # too few
    assert not ce.lists_students([" ".join(format(n, "08b") for n in range(60, 160))])         # bytes: 10110010
    assert not ce.lists_students(["\n".join(f"x = 1,{41421356 + 7 * n} and 2.{71828182 + n}" for n in range(40))])
    assert not ce.lists_students(["Ders Notları: grades, results and the lab report template (Hafta 3)."])
    mixed = [f"{10_000_000 + 9 * n}" for n in range(12)] + [f"{100_000_000 + 9 * n}" for n in range(12)]
    assert not ce.lists_students([" ".join(mixed)])                                               # not of one length


def test_a_course_file_that_lists_students_stays_out_of_the_zip(tmp_path):
    """EEE 215's "Lab Grades 22.10.26.pdf" (classmates' student numbers and marks) went into the zip the README says
    to drag into ChatGPT."""
    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="e", title="2026-1 EEE 215 X 1", code="EEE 215", name="Circuits", term="T")])
    lib.merge_units("e", [
        Unit("u1", 0, "Hafta 1", dt.date(2026, 9, 21), dt.date(2026, 9, 27), "", [
            Resource("r1", "Week 1", "Week01.pdf", "pdf", "pdf", "u1", "/d/r1"),
            Resource("g1", "Lab Grades", "Lab Grades 22.10.26.pdf", "pdf", "pdf", "u2", "/d/g1")]),
        Unit("ug", 1, "Lab Grades Weeks 1-3", None, None, "", [
            Resource("g2", "Groups", "Groups.xlsx", "xlsx", "xlsx", "u3", "/d/g2"),
            Resource("n1", "Ders Notları", "Ders Notları.pdf", "pdf", "pdf", "u4", "/d/n1")])], first_sync=True)
    files = tmp_path / "files"
    for rid, name in (("r1", "Week01.pdf"), ("g1", "Lab Grades 22.10.26.pdf"), ("n1", "Ders Notları.pdf")):
        lib.set_material_file(rid, _file(files / name), 13, "u")
    lib.set_material_text("r1", ["Ohm's law, KVL and KCL."])
    lib.set_material_text("g1", [STUDENTS])
    lib.set_material_text("n1", ["Notlar: Thevenin, Norton; sonuçlar ve puanlar (results)."])
    xlsx = files / "Groups.xlsx"
    with zipfile.ZipFile(xlsx, "w") as z:                                        # numbers as Excel keeps them
        z.writestr("xl/sharedStrings.xml", '<sst><si><t>Student no</t></si><si><t>Group</t></si></sst>')
        z.writestr("xl/worksheets/sheet1.xml", "<worksheet><sheetData>" + "".join(
            f'<row><c><v>{2.0210600000e10 + 41 * n:.10E}</v></c><c t="s"><v>1</v></c></row>' for n in range(25))
            + "</sheetData></worksheet>")
    lib.set_material_file("g2", str(xlsx), xlsx.stat().st_size, "u")
    out = ce.build(lib, Settings(), tmp_path / "out", "e", ce.Choice(), today=TODAY)
    root = "EEE 215 - everything up to 8 Oct 2026"
    names = set(entries(out["path"]))
    assert f"{root}/Week 01 - Hafta 1/Course files/Week01.pdf" in names
    assert f"{root}/General course files/Lab Grades Weeks 1-3/Ders Notları.pdf" in names       # study material stays
    assert not any("Grades 22" in n or "Groups" in n for n in names)
    assert out["students"] == 2 and out["files"] == 2
    readme = read(out["path"], f"{root}/README.md")
    assert "- Week 1: Lab Grades 22.10.26.pdf was left out: it lists students' numbers (grades or groups), and your " \
           "classmates' details shouldn't be uploaded. Open it on IEULearn if you need it." in readme
    assert "- The whole course: Groups.xlsx was left out" in readme
    week1 = read(out["path"], f"{root}/Week 01 - Hafta 1/Week overview.md")
    assert "- Lab Grades 22.10.26.pdf: left out of this zip, as it lists students' numbers (a class's grades or " \
           "groups)." in week1


def test_two_files_of_one_name_in_a_folder_are_kept_apart(env):
    out = build(env, weeks=[2])
    names = [n.rsplit("/", 1)[1] for n in entries(out["path"]) if "/Course files/" in n]
    assert sorted(names) == ["Slides.pdf", "slides (2).PDF"]          # Windows doesn't tell case apart
    n = ce.Names()
    assert [n.take("f", "CON", ".pdf"), n.take("f", "con", ".pdf"), n.take("f", 'a:b*c?', ".txt")] == \
        ["CON_.pdf", "con_ (2).pdf", "a b c.txt"]


def test_long_turkish_names_keep_their_letters_and_every_path_fits(env):
    for course_id in ("c", ""):
        out = build(env, course_id=course_id, weeks=[5])
        with zipfile.ZipFile(out["path"]) as z:
            infos = z.infolist()
        names = [i.filename for i in infos]
        assert max(len(n) for n in names) <= ce.ZIP_PATH_MAX
        pdf = next(i for i in infos if i.filename.endswith(".pdf") and "Kesikli" in i.filename)
        folder, name = pdf.filename.rsplit("/", 1)
        assert name.startswith("Kesikli tekdüze, Binom") and name.endswith(".pdf") and pdf.flag_bits & 0x800
        assert "/Week 05 - Poisson" in folder
        assert all(not re.search(r'[<>:"\\|?*]', n) for n in names)
    assert ce.clean_name("Week 05 - " + LONG_TR, 40) == "Week 05 - Kesikli tekdüze, Binom"         # at a word
    assert ce.clean_name("İŞÇİOĞLU ğüşıöç  .", 60) == "İŞÇİOĞLU ğüşıöç"


# ------------------------------------------------------------------ nothing left behind
def test_cancel_leaves_nothing_behind(env):
    stop = threading.Event()

    def progress(e):
        if e.get("step") == "screens":
            stop.set()

    with pytest.raises(Cancelled):
        build(env, progress=progress, cancel=stop)
    folder = env.out / "MATH 240" / "Exports"
    assert not list(folder.iterdir())


def test_a_zip_open_in_another_program_keeps_its_name_and_the_new_one_gets_another(env, monkeypatch):
    first = build(env)
    assert first["name"] == f"{ROOT}.zip"
    real = ce._replace

    def locked(src, dst):
        if dst.name == f"{ROOT}.zip":
            raise PermissionError(13, "The process cannot access the file", str(dst))
        real(src, dst)

    monkeypatch.setattr(ce, "_replace", locked)
    again = build(env)
    assert again["name"] == f"{ROOT} (2).zip" and again["renamed"]
    assert sorted(p.name for p in (env.out / "MATH 240" / "Exports").iterdir()) == [f"{ROOT} (2).zip", f"{ROOT}.zip"]


@pytest.mark.skipif(sys.platform != "win32", reason="only Windows keeps an open file from being replaced")
def test_a_zip_really_open_elsewhere_on_windows(env):
    first = build(env)
    with open(first["path"], "rb"):                  # (a zip program, a browser uploading it)
        again = build(env)
    assert again["name"] == f"{ROOT} (2).zip" and os.path.getsize(first["path"]) == first["bytes"]


def test_not_enough_free_space_is_said_before_anything_is_written(env, monkeypatch):
    monkeypatch.setattr(ce.shutil, "disk_usage", lambda p: types.SimpleNamespace(total=10**9, used=10**9, free=3 * 1024))
    asked = []
    with pytest.raises(ce.ExportError, match="There isn't enough free space on the disk for this export: it needs "
                                             "about .* and 3 KB is free"):
        build(env, download=lambda rid, progress: asked.append(rid))
    assert not list((env.out / "MATH 240" / "Exports").iterdir()) and asked == []      # (nothing downloaded either)


def test_a_disk_that_fills_up_while_writing_leaves_no_half_zip(env, monkeypatch):
    from studia.net import plain_error

    def full(self, *a, **k):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(zipfile.ZipFile, "write", full)
    with pytest.raises(OSError) as err:
        build(env)
    assert plain_error(err.value).startswith("Your disk is full")
    assert not list((env.out / "MATH 240" / "Exports").iterdir())


# ------------------------------------------------------------------ the dialog's options and the choice
def test_the_options_give_each_week_its_contents_and_size(env):
    o = ce.options(env.lib, env.settings, env.out, "c", today=TODAY)
    weeks = {w["no"]: w for w in o["weeks"]}
    assert [n for n, w in weeks.items() if w["now"]] == [1, 2, 3]
    w1 = weeks[1]
    assert (w1["topic"], w1["start"], w1["lectures"], w1["files"], w1["missing"], w1["links"], w1["screens"]) == \
        ("Sample Spaces", "2026-09-21", 1, 1, ["r2"], 1, 2)
    assert w1["bytes"]["screens"] == 2 * len(b"\xff\xd8\xff\xe0 one") and w1["bytes"]["files"] == 13
    assert weeks[2]["untranscribed"] == 1 and weeks[2]["files"] == 2 and weeks[4]["files"] == 1
    assert o["general"]["files"] == 1 and o["folder"] == str(env.out / "MATH 240" / "Exports") and o["free"] > 0
    every = ce.options(env.lib, env.settings, env.out, "", today=TODAY)
    w1 = next(w for w in every["weeks"] if w["no"] == 1)
    assert every["all"] and sorted(w1["courses"]) == ["EEE 215", "MATH 240"] and w1["lectures"] == 2
    assert w1["topic"] == ""
    assert every["folder"] == str(env.out / "Exports")


def test_the_choice_from_the_page():
    c = ce.Choice.of({"weeks": [3, "1", 1, "x", 0, 99], "screens": False})
    assert (c.weeks, c.transcripts, c.screens, c.files, c.notices, c.download) == ([1, 3], True, False, True, True, True)
    assert ce.Choice.of({"weeks": None}).weeks is None and ce.Choice.of(None) == ce.Choice()
    assert ce.span_name(ce.Choice(weeks=[1, 2, 3, 7]), TODAY) == "weeks 1-3, 7 - 8 Oct 2026"


# ------------------------------------------------------------------ the API
def _api(env, materials=None):
    a = Api.__new__(Api)
    a._mat_lock, a._mat_queue, a._mat_running = threading.Lock(), [], False
    a._courses_dirty = threading.Event()
    pushed = []
    a._push = pushed.append
    svc = types.SimpleNamespace(lib=env.lib, settings=env.settings, output_root=env.out,
                                sync=types.SimpleNamespace(worker_session=lambda: object(), absorb=lambda s: None,
                                                           ensure_login=lambda: None))
    svc.materials = materials or MaterialStore(svc)
    a._services = svc
    a._ready = threading.Event()
    a._ready.set()
    return a, pushed


def _wait(pushed, phase=("done", "error", "cancelled"), timeout=20, rid=None):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        last = [e for e in pushed if e.get("type") == "course_export" and e.get("phase") in phase
                and (rid is None or e["id"] == rid)]
        if last:
            return last[-1]
        time.sleep(0.02)
    raise AssertionError(f"no {phase} event: {pushed[-3:]}")


def test_the_api_makes_one_export_at_a_time_and_says_how_far_it_is(env, monkeypatch):
    gate = threading.Event()
    real = ce.build

    def slow(*a, **k):
        gate.wait(10)
        return real(*a, today=TODAY, **k)

    monkeypatch.setattr(ce, "build", slow)
    a, pushed = _api(env)
    first = a.course_export_start("c", {"weeks": None, "screens": True})
    again = a.course_export_start("c", {"weeks": None})                 # a double click
    other = a.course_export_start("", {"weeks": [1]})                   # another while it runs
    assert first["ok"] and again == {"ok": True, "running": True, "id": first["id"], "export": again["export"]}
    assert other["running"] and other["id"] == first["id"]
    assert a.course_export_state()["running"]
    gate.set()
    done = _wait(pushed)
    assert done["phase"] == "done" and done["id"] == first["id"] and done["title"] == "MATH 240"
    assert done["name"] == f"{ROOT}.zip" and done["bytes"] > 0 and done["estimate"] > 0
    kinds = [e["phase"] for e in pushed if e.get("type") == "course_export"]
    assert kinds[0] == "start" and kinds[-1] == "done" and "progress" in kinds
    steps = {e.get("step") for e in pushed if e.get("phase") == "progress"}
    assert {"plan", "start", "week", "screens", "files", "course", "zip"} <= steps
    assert any(e.get("estimate") for e in pushed if e.get("step") == "week")      # the size said before
    state = a.course_export_state()
    assert not state["running"] and state["export"]["phase"] == "done"
    second = a.course_export_start("", {"weeks": [1]})                  # now another one may start
    assert second["ok"] and not second.get("running") and second["id"] != first["id"]
    assert _wait(pushed, rid=second["id"])["name"] == "All courses - week 1 - 8 Oct 2026.zip"
    assert a.course_export_start("c", {"weeks": []}) == {"ok": False, "error": "Pick at least one week."}


def test_the_api_downloads_missing_files_through_the_app_and_the_rows_follow(env, monkeypatch):
    monkeypatch.setattr(ce, "build", lambda *a, **k: _real_build(*a, today=TODAY, **k))
    downloaded = []

    class Store:
        def busy(self, rid):
            return False

        def download(self, rid, session, progress, cancel=None):
            downloaded.append(rid)
            progress(3, 6)
            path = _file(env.files / "dl" / "Extra.pdf")
            env.lib.set_material_file(rid, path, 13, "u")
            return path

        def index_text(self, rid):
            return 4

    a, pushed = _api(env, Store())
    a._mat_queue = ["r2", "zz"]                         # queued on the course page too: downloaded once, here
    a.course_export_start("c", {"weeks": [1]})
    done = _wait(pushed)
    assert done["phase"] == "done" and downloaded == ["r2"] and a._mat_queue == ["zz"] and done["missing"] == 0
    rows = [(e["id"], e["state"]) for e in pushed if e.get("type") == "material"]
    assert rows[0] == ("r2", "downloading") and rows[-1] == ("r2", "done") and a._courses_dirty.is_set()
    assert any(e.get("step") == "download" and e.get("name") == "Extra.pdf" for e in pushed)


_real_build = ce.build


def test_the_api_cancel_and_failures_in_plain_words(env, monkeypatch):
    a, pushed = _api(env)
    started = threading.Event()

    def stalls(*args, cancel=None, **k):
        started.set()
        while not cancel.is_set():
            time.sleep(0.01)
        raise Cancelled()

    monkeypatch.setattr(ce, "build", stalls)
    a.course_export_start("c", {})
    assert started.wait(5)
    assert a.course_export_cancel() == {"ok": True}
    assert _wait(pushed)["phase"] == "cancelled"

    monkeypatch.setattr(ce, "build", lambda *a, **k: (_ for _ in ()).throw(OSError(errno.ENOSPC, "No space left")))
    a.course_export_start("c", {})
    err = _wait(pushed, phase=("error",))
    assert err["message"].startswith("Your disk is full") and err["title"] == "MATH 240"

    monkeypatch.setattr(ce, "build", lambda *a, **k: (_ for _ in ()).throw(ce.ExportError("Pick at least one week.")))
    pushed.clear()
    a.course_export_start("", {})
    err = _wait(pushed, phase=("error",))
    assert err["message"] == "Pick at least one week." and err["title"] == "All courses"


def test_show_in_folder_only_for_a_zip_in_the_ieustudia_folder(env, monkeypatch):
    from studia import api as api_mod

    shown = []
    monkeypatch.setattr(api_mod, "reveal", shown.append)
    a, _ = _api(env)
    out = build(env, weeks=[1])
    assert a.course_export_show(out["path"]) == {"ok": True} and [str(p) for p in shown] == [out["path"]]
    outside = _file(env.tmp / "elsewhere.zip", b"PK")
    assert not a.course_export_show(outside)["ok"] and not a.course_export_show(str(env.out / "gone.zip"))["ok"]
    assert len(shown) == 1


def test_the_api_options(env):
    a, _ = _api(env)
    r = a.course_export_options("c")
    assert r["ok"] and [c["code"] for c in r["courses"]] == ["MATH 240"] and r["weeks"]
    r = a.course_export_options("")
    assert r["ok"] and r["all"] and {c["code"] for c in r["courses"]} == {"MATH 240", "EEE 215"}
    r = a.course_export_options("nope")
    assert not r["ok"] and "no longer in your list" in r["error"]
