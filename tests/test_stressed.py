"""What the lecturer stressed: the cues (English and Turkish, tied to an exam, stressed, not needed), logistics left to
the Heads-up, a bare "this is important" quoted with what it points at, repeats merged (in a lecture and across
lectures), weeks with their topics, the list kept between starts, the AI's summary checked against its citations,
and the priorities a study pack gets."""

import datetime as dt

import pytest

from studia import store, stressed
from studia.coursesite import Unit
from studia.domain import Course, Recording, Segment, Transcript
from studia.library import Library


def lecture(lib, cid, did, when, lines, gap=10.0):
    """A transcribed recording whose paragraphs are ``lines`` (each its own paragraph: they are ``gap`` s apart)."""
    rec = Recording(delivery_id=did, course_id=cid, folder_id="f", folder_path="", name="n", start=when,
                    duration_s=4000, viewer_url=f"https://v/{did}")
    lib.merge_recordings(cid, [rec], first_sync=not lib.has_recordings(cid))
    segs, at = [], 0.0
    for text in lines:
        lang = "tr" if any(c in text for c in "çğışöüİ") else "en"
        segs.append(Segment(at, at + 4, text, lang, 0.9))
        at += 4 + gap
    tr = Transcript(delivery_id=did, segments=segs, duration_s=4000, speech_s=at, language_seconds={"en": at},
                    model="m", device="cpu", engine_version="t", created_at="2026-10-01T12:00:00")
    lib.save_transcript(tr, store.save_transcript(tr))
    lib.set_status(did, "done")
    return rec


@pytest.fixture
def lib(tmp_path):
    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="c", title="2026-1 MATH 250 P 1", code="MATH 250", name="Calculus", term="T")])
    lib.merge_units("c", [Unit("u1", 0, "Week 1", dt.date(2026, 9, 21), dt.date(2026, 9, 27), "", []),
                          Unit("u2", 1, "Week 2", dt.date(2026, 9, 28), dt.date(2026, 10, 4), "", [])], first_sync=True)
    return lib


@pytest.mark.parametrize("text,kind", [
    ("Be careful here: the derivative of the inverse is not one over the derivative.", "stressed"),
    ("This is a very common mistake, students always forget the chain rule.", "stressed"),
    ("I will ask this kind of integral in the midterm, so practise it.", "exam"),
    ("This will definitely be on the exam: the ratio test.", "exam"),
    ("For the quiz you need the definition of a limit.", "exam"),
    ("You are not responsible for the proof of this theorem.", "not_needed"),
    ("Bu ispatı sınavda sormayacağım, sadece sonucu bilin.", "not_needed"),
    ("Bunu mutlaka bilmeniz lazım, zincir kuralı çok önemli.", "stressed"),
    ("Bu tip bir soru sınavda çıkar, dikkat edin.", "exam"),
    ("The midterm is on November 20 at 10:00 in room B204.", None),           # when and where: the Heads-up's
    ("Don't forget, the quiz is next Wednesday, bring a calculator.", None),
    ("Quiz 2 sonuçları IEULearn'de duyuruldu.", None),
    ("In the final step we divide by two.", None),                           # "the final" of a calculation
    ("At the critical point the derivative is zero.", None),                 # maths, not stress
    ("Today we look at sequences and their limits.", None),
])
def test_cues(text, kind):
    got = stressed.classify(text)
    assert (got[0] if got else None) == kind
    if got:
        a, b = got[1]
        assert 0 <= a < b <= len(text)


@pytest.mark.parametrize("text,kind", [
    # how to learn it, or a step that doesn't matter, is not "the lecturer said this is not needed" (nor a stress)
    ("Do you remember the formula? You don't need to memorize it, we get it from the logic of the distribution.", None),
    ("Bunu koordinatöre sormam lazım, sağlık raporu istiyorlar.", None),
    ("This is not important. Since this map is linear, it has a matrix.", None),
    ("Bu adım önemli değil, sadeleştiriyoruz.", None),
    ("Formülü ezberlemenize gerek yok, türetebilirsiniz.", None),
    ("I'm trying to understand it myself, I don't have to learn it by tomorrow.", None),
    ("You don't need to memorize it, but you must understand where this formula comes from.", "stressed"),
    ("You don't need to know the proof, only the statement.", "not_needed"),
    ("Bu ispat sınav için önemli değil, sonucu bilin.", "not_needed"),
    ("We will not ask about this part in the final.", "not_needed"),
    # "I'll ask" alone: a question in class, a student's question, asking someone else
    ("Ben de soracağım şimdi koordinatöre, raporlar için.", None),
    ("Hocam, bir şey soracağım. Determinantı bulmamız yeterli mi?", None),
    ("Size bir soru soracağım, kim cevaplayacak?", None),
    ("Now I will ask you something. What is the current through the second resistor?", None),
    ("I'll ask the department about the room.", None),
    ("Bu soruyu soracağım, sınava kadar çalışın.", "exam"),
    ("I'll ask you something first. You are responsible for the proof of this theorem.", "exam"),
    ("Discrete or continuous, this is very important. I will ask always, is it discrete or continuous?", "stressed"),
    # the plainest exam hints, and a cue with a tag question after it
    ("Do it at home because definitely in the exam, one question is from here, okay? One, two, three.", "exam"),
    ("Okay, in the exam, we may ask something like this. Write the function as a sum of products.", "exam"),
    ("If I ask you a question like this in the exam. All right? Write these two equations first.", "exam"),
    ("So we are lucky. So if I ask you in the exam, for example, what is the expectation? Watch the interval.", "exam"),
    ("Üçüncü soru böyle olacak sınavda.", "exam"),
    ("Maybe in your quiz or in your meet-up. We will have a question like this.", "exam"),
    ("Be careful about this numbering, okay? Even in the exam you will put the terms here.", "stressed"),
    ("This is very important, right? A voltage source must always have a series resistor.", "stressed"),
    # … but never when denied, asked back, or only about when and where
    ("Bu soru sınavda çıkmaz.", "not_needed"),
    ("I won't ask a question like this in the exam.", "not_needed"),
    ("There won't be a question like this in the exam.", None),
    ("We may not ask a question like this in the midterm.", None),
    ("Bu soru sınavda olmayacak.", None),
    ("Is there a question like this in the exam?", None),
    ("Sınavla ilgili sorunuz var mı?", None),
    ("Next week there are no labs. The midterm is on the 9th at 2.45 in rooms C2, we cover all the topics.", None),
])
def test_what_is_and_isnt_a_cue(text, kind):
    """The phrasings a review of real lectures found misread (made-up sentences in their words)."""
    got = stressed.classify(text)
    assert (got[0] if got else None) == kind


def test_a_bare_ask_does_not_top_the_course(lib):
    lecture(lib, "c", "d1", dt.datetime(2026, 9, 23, 9), [
        "Bunu mutlaka bilmeniz lazım, zincir kuralı türev alırken her yerde kullanılır, iç fonksiyonun türevini "
        "unutan çok oluyor, her soruda kontrol edin.",
        "Şimdi bir örnek yapalım.",
        "Ben de soracağım şimdi koordinatöre, raporlar için.",
    ], gap=200)
    top = stressed.ranked(stressed.course_items(lib, "c"))
    assert [it["kind"] for it in top] == ["stressed"] and "zincir kuralı" in top[0]["text"]


def test_the_moments_of_a_course(lib):
    lecture(lib, "c", "d1", dt.datetime(2026, 9, 23, 9), [
        "Good morning everyone, let us start with limits.",
        "Okay, this is very important.",                                     # bare: quoted with what follows
        "The limit of sine x over x as x goes to zero is one, and we use it all the time.",
        "Be careful, this is important again, the same point said once more.",   # within two minutes: the same point
        "Room B204 next Wednesday for the quiz, bring a calculator.",          # logistics
        "uh",
    ])
    lecture(lib, "c", "d2", dt.datetime(2026, 9, 30, 9), [
        "Remember this: the limit of sine x over x as x goes to zero is one, we use it all the time.",
        "You are not responsible for the epsilon delta proof in the midterm.",
        "I will ask a question like this in the midterm, the squeeze theorem with sine.",
    ], gap=200)
    items = stressed.course_items(lib, "c")
    kinds = [(it["day"], it["kind"]) for it in items]
    assert kinds == [("2026-09-23", "stressed"), ("2026-09-30", "not_needed"), ("2026-09-30", "exam")]
    first = items[0]
    assert "The limit of sine x" in first["text"] and first["text"].startswith("Okay, this is very important.")
    assert first["text"][first["hl"][0]:first["hl"][1]].lower() == "very important"
    assert first["n"] == 2 and first["again"][0]["day"] == "2026-09-30"      # said again a week later: one point
    assert first["part"] == 1 and first["ts"] == "00:14" and first["viewer_url"] == "https://v/d1"
    # kept in the library: the next start reads it without the transcripts
    stressed._cache.clear()
    assert lib.get_meta("stressed:c") and stressed.course_items(lib, "c") == items
    from studia.views import course_detail
    from studia.config import Settings

    d = course_detail(lib, Settings(), "c")
    g = stressed.grouped(items, d["weeks"])
    assert [w["no"] for w in g["weeks"]] == [2, 1] and g["weeks"][1]["items"][0]["week"] == 1
    assert g["counts"] == {"exam": 1, "stressed": 1, "not_needed": 1} and g["total"] == 2
    assert len(g["not_needed"]) == 1 and "epsilon delta" in g["not_needed"][0]["text"]


def test_prompt_lines_and_the_summary_keep_to_their_citations(lib):
    items = [{"id": "s1", "kind": "exam", "text": "I will ask the ratio test.", "day_key": "c|2026-09-23|lecture",
              "day": "2026-09-23", "date": "2026-09-23T09:10", "part": 1, "t": 600.0, "ts": "10:00", "n": 2},
             {"id": "s2", "kind": "stressed", "text": "Be careful with signs.", "day_key": "c|2026-09-30|lecture",
              "day": "2026-09-30", "date": "2026-09-30T09:05", "part": 2, "t": 300.0, "ts": "05:00", "n": 1}]
    lines, by_ref = stressed.prompt_lines(stressed.ranked(items), {"c|2026-09-23|lecture": "L1"})
    assert lines.splitlines()[0] == "S1 (tied to an exam, said 2 times) [L1 part 1 10:00] “I will ask the ratio test.”"
    assert "[2026-09-30 part 2 05:00]" in lines and by_ref["S2"]["id"] == "s2"
    raw = {"items": [{"point": "Know the ratio test.", "why": "asked in the exam", "cites": ["S1", "S9"]},
                     {"point": "Something made up", "cites": []},
                     {"point": "Signs in the limit", "citations": "S2 and S1"},
                     {"point": "Not needed: the epsilon-delta proof", "cites": []}]}
    out = stressed.clean_summary(raw, by_ref)
    assert [x["cites"] for x in out] == [["s1"], ["s2", "s1"], []]
    assert out[0]["why"] == "asked in the exam"
    assert stressed.clean_summary([{"text": "Bare list", "sources": ["S2"]}], by_ref)[0]["cites"] == ["s2"]


def test_a_study_pack_gets_the_stressed_moments_as_priorities(lib):
    lecture(lib, "c", "d1", dt.datetime(2026, 9, 23, 9), [
        "Intro.", "Be careful: the derivative of a constant is zero, students often forget it.",
        "We continue with the chain rule which you need for everything later on in this course."])
    from studia import studypack
    from studia.config import Settings
    from studia.plans import Scope

    facts = studypack.gather(lib, Settings(), "c", Scope(kind="pack", weeks=[1]))
    assert facts["stressed"] and facts["stressed"][0]["ref"] == "L1"
    block = stressed.pack_block(facts)
    assert "WHAT THE LECTURER STRESSED" in block and "[L1 part 1 00:14]" in block
    near = {"sources": {"lectures": [{"ref": "L1", "part": 1, "from": 0, "to": 60}]}}
    far = {"sources": {"lectures": [{"ref": "L1", "part": 1, "from": 900, "to": 1200}]}}
    assert "derivative of a constant" in stressed.chapter_block(facts, near) and stressed.chapter_block(facts, far) == ""
    assert "WHAT THE LECTURER STRESSED" in studypack.outline_prompt(Scope(kind="pack"), facts)
    assert stressed.pack_block({"stressed": []}) == ""
