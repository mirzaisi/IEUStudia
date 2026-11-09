"""The library's shortcuts give the same results as before: paragraphs built once per transcript, a course's files
rewritten with the study pack written once, one class day read on its own, the slide list, the data folders, search
while a word is being typed, and the course list's counts."""

import dataclasses as dc
import datetime as dt
import json
import os
import shutil
import types

from studia import paths, store
from studia.api import Api
from studia.chat import ChatService
from studia.config import Settings
from studia.domain import Course, Recording, Segment, Transcript
from studia.library import Library
from studia.outputs import markdown, writer
from studia.outputs.ai_copy import compact_day
from studia.outputs.common import group_days
from studia.outputs.markdown import part_views, render_day
from studia.outputs.pack import render_study_pack
from studia.outputs.paragraphs import build_paragraphs
from studia.services import Services
from studia.slides import Slide
from studia.views import all_course_views, day_detail

DAY1, DAY2 = "c|2026-10-13|lecture", "c|2026-10-20|lecture"
SEGS = [Segment(0, 5, "So good morning everyone.", "en", 0.9),
        Segment(5.5, 9, "Today we start Bayes' theorem.", "en", 0.9),
        Segment(15, 19, "Arkadaşlar, bu konu sınavda çıkacak, dikkat edin.", "tr", 0.9),
        Segment(19.5, 25, "This will definitely be on the midterm.", "en", 0.9),
        Segment(30, 34, "[unclear]", "en", 0.3),
        Segment(40, 46, "The homework is due next week.", "en", 0.4)]


def _rec(did, day, hour, kind="lecture", course="c"):
    return Recording(delivery_id=did, course_id=course, folder_id="f", folder_path="", name=did,
                     start=dt.datetime(2026, 10, day, hour, 30), duration_s=2400, viewer_url=f"https://v/{did}",
                     kind=kind)


def _tr(did, segs=SEGS):
    return Transcript(delivery_id=did, segments=list(segs), duration_s=2400, speech_s=40, language_seconds={"en": 30},
                      model="m", device="cpu", engine_version="t", created_at="2026-10-20T10:00:00")


def _env(tmp_path):
    """A course with two class days (two parts on the first, slides on its first part) and a chat service."""
    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="c", title="t", code="MATH 240", name="Probability", instructor="Dr X")])
    lib.merge_recordings("c", [_rec("r0", 13, 8), _rec("r1", 13, 9), _rec("r2", 20, 8)], first_sync=True)
    for did in ("r0", "r1", "r2"):
        t = _tr(did)
        lib.save_transcript(t, store.save_transcript(t))
        lib.set_status(did, "done")
    img = tmp_path / "s1.jpg"
    img.write_bytes(b"jpg")
    store.save_slides("r0", [Slide(1, 0.0, 14.0, "slides/x/s1.jpg", str(img)), Slide(2, 14.0, 60.0, "slides/x/s2.jpg",
                                                                                     str(img))])
    lib.set_warnings("r1", [{"code": "quiet", "level": "warning", "message": "The audio is very quiet."}])
    ns = types.SimpleNamespace(lib=lib, emit=lambda e: None, output_root=tmp_path / "out", settings=Settings())
    for name in ("day", "write_outputs", "write_course_pack", "rewrite_course"):
        setattr(ns, name, types.MethodType(getattr(Services, name), ns))
    return lib, ns, ChatService(ns)


def test_paragraphs_are_built_once_per_transcript_and_handed_out_as_copies(monkeypatch):
    built = []
    monkeypatch.setattr(markdown, "build_paragraphs", lambda segs: built.append(1) or build_paragraphs(segs))
    t, rec = _tr("a"), _rec("a", 13, 8)
    first = part_views([rec], {"a": t}, {"a": [Slide(1, 0, 14, "s1.jpg"), Slide(2, 14, 60, "s2.jpg")]})[0].paragraphs
    assert [p.slide for p in first] == [1, 2, 2, 2, 2] and "exam" in first[1].tags
    first[1].tags.append("mine")
    again = part_views([rec], {"a": t})[0].paragraphs
    assert len(built) == 1                                           # the same transcript: built once
    assert again == build_paragraphs(t.segments)                     # no slide, no tag of the earlier caller
    assert "mine" not in again[1].tags and {p.slide for p in again} == {None}
    part_views([rec], {"a": dc.replace(t)})                          # a new transcription is a new object
    assert len(built) == 2
    t.segments = t.segments[:2]                                      # segments replaced: built again
    assert part_views([rec], {"a": t})[0].paragraphs == build_paragraphs(t.segments) and len(built) == 3


def test_files_day_page_and_chat_material_are_the_same_as_built_fresh(tmp_path, monkeypatch):
    lib, ns, svc = _env(tmp_path)
    try:
        def outputs():
            course = lib.get_course("c")
            recs = lib.day_recordings(DAY1)
            ids = [r.delivery_id for r in recs]
            trans = store.load_transcripts(ids)
            slides = {i: store.load_slides(i) for i in ids}
            warnings = {i: lib.warnings(i) for i in ids}
            course_recs = lib.recordings("c")
            every = store.load_transcripts([r.delivery_id for r in course_recs])
            days = [(d, k, rs, every) for d, k, rs in group_days(course_recs)]
            return [render_day(course, recs, trans, slides, warnings), render_study_pack(course, days, warnings),
                    compact_day(course, recs, trans, slides), compact_day(course, recs, trans),
                    render_day(course, recs, trans),
                    json.dumps(day_detail(lib, Settings(), DAY1, {}), sort_keys=True),
                    svc.context("c", [DAY1, DAY2], "full", True)["system"],
                    svc.context("c", [DAY1, DAY2], "highlights")["system"],
                    json.dumps(svc.scope_options("c"), sort_keys=True)]

        cached = outputs()
        assert outputs() == cached
        monkeypatch.setattr(markdown, "_paragraphs", lambda t: build_paragraphs(t.segments))     # as it was
        assert outputs() == cached
        assert "![Slide 2]" in cached[0] and "(slide 2)" in cached[2] and "(slide" not in cached[3]
        assert "![Slide" not in cached[4]
        chat = svc.create("c", [DAY1, DAY2], slides=True)               # the view reuses the material just built
        ctx = svc.context("c", [DAY1, DAY2], "full", True)
        assert (chat["context_tokens"], chat["images"]) == (ctx["tokens"], len(ctx["images"])) == (ctx["tokens"], 2)
        assert svc.view(chat["id"])["context_tokens"] == ctx["tokens"]
        lib.add_chat_message(chat["id"], "user", "When is the quiz?")
        assert [(c["id"], c["n_messages"], c["first_question"]) for c in lib.chats(chat["id"])] == \
            [(chat["id"], 1, "When is the quiz?")]
    finally:
        svc.shutdown()


def test_rewriting_a_course_writes_the_same_files_and_the_pack_once(tmp_path, monkeypatch):
    lib, ns, svc = _env(tmp_path)
    try:
        lib.set_warnings("r0", [{"code": "not_written", "level": "warning", "message": "Transcribed, but its files…"}])
        for did in ("r0", "r2"):                                     # as the lectures were transcribed
            ns.write_outputs(did)
        out = tmp_path / "out"
        before = {p.relative_to(out): p.read_bytes() for p in out.rglob("*") if p.is_file()}
        assert any(p.name.endswith("Study pack.md") for p in before) and any(p.suffix == ".srt" for p in before)
        shutil.rmtree(out)
        packs = []
        real = writer.render_study_pack
        monkeypatch.setattr(writer, "render_study_pack", lambda *a, **k: packs.append(1) or real(*a, **k))
        ns.rewrite_course("c")
        assert {p.relative_to(out): p.read_bytes() for p in out.rglob("*") if p.is_file()} == before
        assert len(packs) == 1
        assert "very quiet" in next(v for p, v in before.items() if "2026-10-13" in p.name and p.suffix == ".md").decode()
        assert b"Transcribed, but" not in b"".join(before.values())
    finally:
        svc.shutdown()


def test_one_class_day_is_read_without_the_rest_of_the_course(tmp_path):
    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="c", title="t", code="EEE 215", name="Circuits"),
                        Course(id="d", title="t", code="MATH 250", name="Algebra")])
    lib.merge_recordings("c", [_rec("a", 13, 8), _rec("b", 13, 9), _rec("l", 13, 14, "lab"), _rec("n", 20, 8),
                               _rec("g", 13, 11)], first_sync=True)
    lib.merge_recordings("d", [_rec("x", 13, 8, course="d")], first_sync=True)
    lib.db.execute("UPDATE recordings SET gone=1 WHERE delivery_id='g'")         # no longer in Panopto
    keys = {r.day_key for r in lib.recordings(include_gone=True)} | {"c|2026-10-13|seminar", "z|2026-10-13|lecture",
                                                                     "c", "garbage|x"}
    for key in keys:
        assert [r.delivery_id for r in lib.day_recordings(key)] == \
            [r.delivery_id for r in lib.recordings(key.split("|")[0]) if r.day_key == key], key
    assert [r.delivery_id for r in lib.day_recordings(DAY1)] == ["b", "a"]
    assert lib.get_course("d").code == "MATH 250" and lib.get_course("nope") is None
    assert lib.get_course("c") == next(c for c in lib.courses(True) if c.id == "c")


def test_the_slide_list_follows_new_slides_also_from_another_program():
    assert store.slide_ids() == frozenset()
    store.save_slides("a", [Slide(1, 0, 5, "x.jpg")])
    assert store.slide_ids() == {"a"}
    store.save_slides("b", [Slide(1, 0, 5, "x.jpg")])                # at once, even in the same clock tick
    assert store.slide_ids() == {"a", "b"}
    folder = paths.transcripts_dir()
    (folder / "c.slides.json").write_text("[]", encoding="utf-8")    # written by another program
    stamp = folder.stat().st_mtime_ns
    os.utime(folder, ns=(stamp, stamp + 10_000_000))
    assert store.slide_ids() == {"a", "b", "c"}


def test_data_folders_are_made_once_and_a_transcript_still_finds_its_folder(monkeypatch):
    made = []
    real = paths._ensure_dir
    monkeypatch.setattr(paths, "_ensure_dir", lambda p: made.append(p) or real(p))
    paths._made.clear()
    for _ in range(3):
        folder = paths.transcripts_dir()
    assert made.count(folder) == 1 and made.count(paths.app_dir()) == 1 and folder.is_dir()
    folder.rmdir()                                                   # gone while the app runs
    store.save_transcript(_tr("x"))
    assert (folder / "x.json").is_file()


def test_search_while_typing_shows_the_newest_and_looks_courses_up_once(tmp_path):
    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="c", title="t", code="EEE 215", name="Circuits")])
    lib.merge_recordings("c", [_rec("old", 13, 8), _rec("new", 20, 8)], first_sync=True)
    for did, text in (("old", "The Thevenin theorem and the theory."), ("new", "Then the Norton theorem.")):
        lib.save_transcript(Transcript(delivery_id=did, segments=[Segment(0, 5, text, "en", 0.9)], duration_s=5,
                                       speech_s=5, language_seconds={}, model="m", device="cpu", engine_version="t",
                                       created_at="2026-10-20T10:00:00"), tmp_path / f"{did}.json")
    lib.db.execute("INSERT INTO materials(resource_id, course_id, unit_id, title) VALUES('m1', 'c', 'u', 'Week 1')")
    lib.db.execute("INSERT INTO materials(resource_id, course_id, unit_id, title) VALUES('m2', 'c', 'u', 'Week 2')")
    lib.set_material_text("m1", ["Thevenin equivalent", "Theory of circuits"])
    lib.set_material_text("m2", ["Then Norton"])
    from studia.search import results

    def days(q):
        return [it["day_key"] for g in results(lib, q)["groups"] if g["kind"] == "lecture" for it in g["items"]]

    assert days("th") == [DAY2, DAY1]                                    # two letters: the newest first
    assert days("the") == [DAY1, DAY2]                                   # a word: the best match first (twice)
    lib.index.ensure()
    seen = []
    lib.db.set_trace_callback(seen.append)
    res = Api.search.__wrapped__(types.SimpleNamespace(_s=types.SimpleNamespace(lib=lib)), "th")
    lib.db.set_trace_callback(None)
    groups = {g["kind"]: g["items"] for g in res["groups"]}
    assert [it["course_code"] for it in groups["lecture"]] == ["EEE 215", "EEE 215"]
    assert sorted((it["resource_id"], it["title"]["text"], len(it["pages"]) + it["more"]) for it in groups["file"]) == \
        [("m1", "Week 1", 2), ("m2", "Week 2", 1)]                      # (one page shown of each, the rest counted)
    assert sum("FROM courses" in s for s in seen) == 1 and sum("FROM materials WHERE" in s for s in seen) == 1


def test_the_course_list_counts_are_the_same(tmp_path):
    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="c", title="t", code="EEE 215", name="C"), Course(id="d", title="t", code="X", name="D"),
                        Course(id="e", title="t", code="Y", name="E")])
    for rid, cid, path, new, gone in (("m1", "c", "", 1, 0), ("m2", "c", "C:/f.pdf", 0, 0), ("m3", "c", "", 1, 1),
                                      ("m4", "c", "C:/g.pdf", 1, 1), ("m5", "d", "C:/h.pdf", 0, 0), ("m6", "e", None, 1, 0)):
        lib.db.execute("INSERT INTO materials(resource_id, course_id, unit_id, title, local_path, is_new, gone) "
                       "VALUES(?,?,?,?,?,?,?)", (rid, cid, "u", rid, path, new, gone))
    for aid, cid, new in (("a1", "c", 1), ("a2", "c", 0), ("a3", "d", 1), ("a4", "e", 0)):
        lib.db.execute("INSERT INTO announcements(id, course_id, title, posted_at, is_new) VALUES(?,?,?,?,?)",
                       (aid, cid, aid, "2026-10-1" + aid[1], new))
    lib.db.commit()
    lib.create_chat("ch", "e", {}, "sonnet")
    lib.upsert_courses([Course(id="c", title="t", code="EEE 215", name="C")])       # d and e left the dashboard

    def as_before():
        mats, anns = {}, {}
        for m in lib.materials():
            mats.setdefault(m["course_id"], []).append(m)
        for a in lib.announcements(limit=500):
            anns[a["course_id"]] = anns.get(a["course_id"], 0) + (1 if a["is_new"] else 0)
        return {cid: {"materials": len(cm), "materials_new": sum(1 for m in cm if m["is_new"]),
                      "downloaded": sum(1 for m in cm if m.get("local_path")), "announcements_new": anns.get(cid, 0)}
                for cid, cm in mats.items()}

    views = {v["id"]: v for v in all_course_views(lib, Settings(), [])}
    assert set(views) == {"c", "d", "e"}                             # a downloaded file, a chat: still yours
    assert {k: v["content"] for k, v in views.items()} == as_before()
    assert views["c"]["content"] == {"materials": 3, "materials_new": 2, "downloaded": 2, "announcements_new": 1}
    lib.delete_chat("ch")
    assert {v["id"] for v in all_course_views(lib, Settings(), [])} == {"c", "d"}
