"""The search (search.py): whole words in any of their forms (English plurals, Turkish endings with their vowel
harmony), numbers however they are written, words of the same meaning in both languages, typos, quoted phrases and
words still being typed; rows with all the words first, titles and announcements before passing mentions; one index
that follows the library (transcripts, files, announcements, the weekly plan, chats, AI notes) and the page's results
(grouped, counted, filtered, each hit with its moment)."""

import datetime as dt
import threading
import types

import pytest

from studia import search
from studia.api import Api
from studia.coursesite import Announcement, Resource, Unit
from studia.domain import Course, Recording, Segment, Transcript
from studia.library import Library
from studia.search import Vocab, fold, parse, results, words


# ------------------------------------------------------------------ words
def test_a_word_is_folded_however_it_was_written():
    assert {fold(w) for w in ("SINAV", "Sınav", "sinav", "SİNAV", "sınav")} == {"sinav"}
    assert fold("İŞTE") == fold("işte") == "iste"
    assert [fold(w) for w in ("öğrenci", "çözüm", "Ölçüm", "ŞEKİL")] == ["ogrenci", "cozum", "olcum", "sekil"]
    assert fold("ﬁnal") == "final"                                  # a PDF's ligature


def test_numbers_letters_and_abbreviations_are_words_of_their_own():
    assert words("Quiz-1, 1. quiz, Q1 and Q-2") == ["quiz", "1", "1", "quiz", "quiz", "1", "and", "quiz", "2"]
    assert words("HW2 · Week01_Student · Ch. 3") == ["homework", "2", "week", "1", "student", "chapter", "3"]
    assert words("the 1st and the 2nd, 3'üncü quiz") == ["the", "1", "and", "the", "2", "3", "quiz"]
    assert words("Clear Q 0 1 Set Q 1 1") == ["clear", "q", "0", "1", "set", "q", "1", "1"]   # a flip-flop's Q
    assert words("MATH 240 Engineering ProbabilityWeek 14") == ["math", "240", "engineering", "probability", "week", "14"]
    assert words("LaTeX on an iPhone") == ["latex", "on", "an", "iphone"]


# ------------------------------------------------------------------ forms of a word
def _vocab(*texts):
    df = {}
    for text in texts:
        for w in set(words(text)):
            df[w] = df.get(w, 0) + 1
    return Vocab(df, len(texts))


FORMS = _vocab("exam exams example examples examine examination", "sınav sınavı sınavda sınavlarında sınıf",
               "quiz quizzes quizde quizdeki", "olasılık olasılığı olasılıkları olası", "kitap kitabı kitaplar",
               "bayes bayesian bay bays", "the then them", "kısa kişi kısım kısaca", "sin since sine",
               "probability probabilities probably", "lab labs labda laboratory", "ödev ödevler ödevi")


@pytest.mark.parametrize("word,forms", [
    ("exam", {"exam", "exams"}),                                 # never example, examine or examination
    ("exams", {"exam", "exams"}),
    ("sınav", {"sinav", "sinavi", "sinavda", "sinavlarinda"}),   # not sınıf
    ("sınavda", {"sinav", "sinavi", "sinavda", "sinavlarinda"}),
    ("quiz", {"quiz", "quizzes", "quizde", "quizdeki"}),
    ("olasılık", {"olasilik", "olasiligi", "olasiliklari"}),     # its k softens; olası is another word
    ("kitap", {"kitap", "kitabi", "kitaplar"}),
    ("bayes", {"bayes", "bayesian"}),                            # not bay, bays
    ("the", {"the"}),                                            # not then, them
    ("kısa", {"kisa", "kisaca"}),                                # not kişi, kısım
    ("sin", {"sin"}),                                            # not since, sine
    ("probability", {"probability", "probabilities"}),
    ("lab", {"lab", "labs", "labda"}),
    ("ödevi", {"odev", "odevler", "odevi"}),
])
def test_the_forms_of_a_word_are_found_and_only_those(word, forms):
    assert set(FORMS.family(fold(word))) == forms


# ------------------------------------------------------------------ an index to search
def _rec(did, cid, day, hour=9, kind="lecture"):
    return Recording(delivery_id=did, course_id=cid, folder_id="f", folder_path="", name=did,
                     start=dt.datetime(2026, 10, day, hour, 30), duration_s=3000, viewer_url=f"https://v/{did}",
                     kind=kind)


def _say(lib, did, *segs):
    lib.save_transcript(Transcript(delivery_id=did, segments=[Segment(a, b, text, lang, 0.9) for a, b, text, lang in segs],
                                   duration_s=3000, speech_s=60, language_seconds={"en": 60}, model="m", device="cpu",
                                   engine_version="t", created_at="x"), f"{did}.json")


@pytest.fixture
def lib(tmp_path):
    from studia import store

    store._cache.clear()                     # (transcripts of other tests, under the same ids)
    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="p", title="t", code="MATH 240", name="Probability"),
                        Course(id="c", title="t", code="EEE 215", name="Circuits")])
    lib.merge_recordings("p", [_rec("p1", "p", 7), _rec("p2", "p", 14), _rec("p3", "p", 21)], first_sync=True)
    lib.merge_recordings("c", [_rec("c1", "c", 8)], first_sync=True)
    _say(lib, "p1", (0, 4, "Good morning, today we start.", "en"),
         (60, 64, "Next week we have the first quiz, so please study.", "en"),
         (200, 205, "One more thing about the quiz: bring a pencil.", "en"))
    _say(lib, "p2", (0, 5, "This is an example of conditional probability.", "en"),
         (30, 35, "Probability and conditional events are different.", "en"),
         (120, 126, "Arkadaşlar ara sınavda Bayes çıkacak.", "tr"))
    _say(lib, "p3", (0, 5, "The Poisson distribution counts events.", "en"),
         (40, 46, "Ara verelim, 10 dakika sonra sınava başlıyoruz.", "tr"))
    _say(lib, "c1", (0, 5, "The Thevenin equivalent of the circuit.", "en"),
         (90, 95, "Quiz 1 and 2 will be about Thevenin.", "en"))
    lib.merge_units("p", [Unit("u1", 1, "Hafta 1", dt.date(2026, 9, 21), dt.date(2026, 9, 27), "", []),
                          Unit("u3", 3, "Hafta 3", dt.date(2026, 10, 5), dt.date(2026, 10, 11), "",
                               [Resource("w3", "Week03_Student", "Week03_Student.pdf", "pdf", "pdf", "u", "d")]),
                          Unit("u8", 8, "Hafta 8 Midterm", dt.date(2026, 11, 9), dt.date(2026, 11, 15), "", [])],
                    first_sync=True)
    lib.set_material_text("w3", ["Independence and Bayes' theorem", "Homework 2: three questions on Bayes",
                                 "Quiz 1 covers weeks 1 to 3"])
    lib.set_course_info("p", syllabus={"weeks": [{"no": 3, "subject": "Independence, Reliability & Bayes"},
                                                 {"no": 8, "subject": "Midterm Exam"}]},
                        overview={"weeks": [{"no": 8, "topic": "Ara Sınav"}]})
    lib.merge_announcements("p", [Announcement("a1", "About Quiz 1", "Dr X", dt.datetime(2026, 10, 9, 9),
                                               "The first quiz is next week."),
                                  Announcement("a2", "Vize hakkında", "Dr X", dt.datetime(2026, 10, 30, 9),
                                               "Ara sınav 9 Kasım'da.")], first_sync=True)
    return lib


def _items(res, kind):
    return next((g["items"] for g in res["groups"] if g["kind"] == kind), [])


def _marked(snip):
    return [snip["text"][a:b] for a, b in snip["marks"]]


def _lecture_moments(res):
    return [(it["day_key"].split("|")[1], m["ts"]) for it in _items(res, "lecture") for m in it["moments"]]


# ------------------------------------------------------------------ what is found
def test_exam_finds_exams_and_sinav_never_example(lib):
    res = results(lib, "exam", typing=False)
    assert all("exampl" not in " ".join(_marked(m["snippet"])).lower()
               for it in _items(res, "lecture") for m in it["moments"])
    weeks = _items(res, "week")
    assert [w["week"] for w in weeks] == [8] and _marked(weeks[0]["snippet"]) == ["Exam", "Sınav"]
    assert ("2026-10-14", "02:00") in _lecture_moments(res)                    # "ara sınavda" is an exam


def test_quiz_one_finds_quiz_1_written_any_way_and_only_next_to_its_number(lib):
    res = results(lib, "quiz one", typing=False)
    anns = _items(res, "announcement")
    assert anns[0]["id"] == "a1" and _marked(anns[0]["title"]) == ["Quiz", "1"]
    moments = _lecture_moments(res)
    assert ("2026-10-07", "01:00") in moments                                  # "the first quiz"
    assert ("2026-10-08", "01:30") in moments                                  # "Quiz 1 and 2"
    full = {(it["day_key"].split("|")[1], m["ts"]) for it in _items(res, "lecture") if it["every"] for m in it["moments"]}
    assert ("2026-10-07", "03:20") not in full            # "One more thing about the quiz" is no quiz 1
    files = _items(res, "file")
    assert files[0]["every"] and _marked(files[0]["pages"][0]["snippet"]) == ["Quiz", "1"]


def test_quiz_2_is_found_in_a_list_of_numbers(lib):
    res = results(lib, "quiz 2", typing=False)
    lec = [it for it in _items(res, "lecture") if it["every"]]
    assert [it["course_code"] for it in lec] == ["EEE 215"] and _marked(lec[0]["moments"][0]["snippet"]) == ["Quiz", "2"]


def test_midterm_ara_sinav_and_vize_are_one_thing(lib):
    for q in ("midterm", "ara sınav", "vize", "ARA SINAV"):
        res = results(lib, q, typing=False)
        assert [a["id"] for a in _items(res, "announcement")] == ["a2"], q
        assert [w["week"] for w in _items(res, "week")] == [8], q
        lec = [it for it in _items(res, "lecture") if it["every"]]
        assert [it["day_key"].split("|")[1] for it in lec] == ["2026-10-14"], q      # not "Ara verelim ... sınava"


def test_turkish_words_find_english_ones_of_the_same_meaning(lib):
    res = results(lib, "poisson dağılımı", typing=False)
    lec = _items(res, "lecture")
    assert lec[0]["every"] and _marked(lec[0]["moments"][0]["snippet"]) == ["Poisson", "distribution"]


def test_homework_2_is_a_number_of_homework_not_240(lib):
    res = results(lib, "homework 2", typing=False)
    files = _items(res, "file")
    assert files[0]["every"] and _marked(files[0]["pages"][0]["snippet"]) == ["Homework", "2"]
    assert not any(it["every"] for it in _items(res, "lecture"))


def test_a_phrase_in_quotes_must_stand_together(lib):
    res = results(lib, '"conditional probability"', typing=False)
    lec = [it for it in _items(res, "lecture") if it["every"]]
    assert [m["ts"] for it in lec for m in it["moments"]] == ["00:00"]       # not "Probability and conditional"
    assert _marked(lec[0]["moments"][0]["snippet"]) == ["conditional probability"]


def test_a_typo_is_taken_for_the_word_it_looks_like(lib):
    res = results(lib, "probabilty", typing=False)
    assert res["fixed"] == "probability"
    assert _lecture_moments(res)
    assert results(lib, "probability", typing=False)["fixed"] == ""
    assert results(lib, "zzzzqqq", typing=False)["total"] == 0


def test_the_last_word_is_a_prefix_while_it_is_typed(lib):
    lec = _items(results(lib, "thev"), "lecture")
    assert [it["course_code"] for it in lec] == ["EEE 215"] and _marked(lec[0]["moments"][0]["snippet"]) == ["Thevenin"]
    res = results(lib, "exam")                                       # a word of its own: no prefix
    assert not any("xample" in " ".join(_marked(m["snippet"])) for it in _items(res, "lecture") for m in it["moments"])
    assert _items(results(lib, "exa"), "lecture")                    # (still typing: example, exam...)
    assert _items(results(lib, "thev", typing=False), "lecture")     # finished, matching nothing else: its prefix


def test_the_moment_is_where_the_words_were_said(lib):
    _say(lib, "p3", (0, 3, "Today", "en"), (3.5, 7, "we look at", "en"), (7.5, 12, "the Poisson distribution.", "en"))
    m = _items(results(lib, "poisson", typing=False), "lecture")[0]["moments"][0]
    assert m["t"] == 7.5 and m["ts"] == "00:07" and m["delivery_id"] == "p3"


# ------------------------------------------------------------------ ranking
def test_all_the_words_rank_above_some_and_titles_above_passing_mentions(lib):
    res = results(lib, "bayes theorem", typing=False)
    files = _items(res, "file")
    assert files[0]["every"] and _marked(files[0]["pages"][0]["snippet"]) == ["Bayes", "theorem"]
    lec = _items(res, "lecture")
    assert lec and not lec[0]["every"]                               # "Bayes" alone in a lecture: some of the words
    order = [g["kind"] for g in res["groups"]]
    assert order.index("file") < order.index("lecture")


def test_of_two_equal_hits_the_newer_comes_first(lib):
    lib.merge_recordings("p", [_rec("p4", "p", 28)], first_sync=False)
    _say(lib, "p4", (0, 5, "The Poisson distribution counts events.", "en"))
    days = [it["day_key"].split("|")[1] for it in _items(results(lib, "poisson distribution", typing=False), "lecture")]
    assert days[:2] == ["2026-10-28", "2026-10-21"]


# ------------------------------------------------------------------ the page's results
def test_results_are_grouped_counted_and_filtered(lib):
    res = results(lib, "quiz", typing=False)
    assert res["counts"]["lecture"] == 2 and res["counts"]["announcement"] == 1 and res["counts"]["file"] == 1
    assert {c["code"]: c["n"] for c in res["courses"]} == {"MATH 240": 3, "EEE 215": 1}
    only = results(lib, "quiz", course="c", typing=False)
    assert [g["kind"] for g in only["groups"]] == ["lecture"] and only["counts"] == {"lecture": 1}
    assert {c["code"]: c["n"] for c in only["courses"]} == {"MATH 240": 3, "EEE 215": 1}     # (the other filter)
    files = results(lib, "quiz", kind="file", typing=False)
    assert [g["kind"] for g in files["groups"]] == ["file"] and files["counts"]["lecture"] == 2


def test_the_api_takes_the_filters(lib):
    ns = types.SimpleNamespace(_s=types.SimpleNamespace(lib=lib), _ready=threading.Event())
    res = Api.search.__wrapped__(ns, "quiz", {"course": "p", "kind": "announcement", "done": True})
    assert res["ok"] and [it["id"] for g in res["groups"] for it in g["items"]] == ["a1"]
    res = Api.search.__wrapped__(ns, "quiz", {"kind": "nonsense"})               # an unknown kind: every kind
    assert len(res["groups"]) > 1
    rules = Api.search_rules.__wrapped__(ns)["rules"]
    assert rules["numbers"]["birinci"][0] == 1 and ["ara", "sinav"] in rules["synonyms"][1]


# ------------------------------------------------------------------ the index follows the library
def test_the_index_follows_what_the_library_keeps(lib):
    assert results(lib, "thevenin", typing=False)["total"] == 1
    lib.delete_transcript("c1")
    assert results(lib, "thevenin", typing=False)["total"] == 0
    lib.set_material_text("w3", ["Thevenin, oddly"])
    assert [it["resource_id"] for it in _items(results(lib, "thevenin", typing=False), "file")] == ["w3"]
    lib.merge_announcements("p", [Announcement("a1", "About Quiz 1", "Dr X", dt.datetime(2026, 10, 9, 9),
                                               "Bring your Thevenin notes.")], first_sync=False)      # edited
    assert [a["id"] for a in _items(results(lib, "thevenin", typing=False), "announcement")] == ["a1"]
    lib.create_chat("ch", "p", {}, "sonnet", "Thevenin questions")
    lib.add_chat_message("ch", "user", "What is a Norton equivalent?")
    assert [c["chat_id"] for c in _items(results(lib, "norton", typing=False), "chat")] == ["ch"]
    lib.delete_chat("ch")
    assert not _items(results(lib, "norton", typing=False), "chat")
    lib.merge_recordings("c", [_rec("p1", "c", 7)], first_sync=False)   # (listed by another course: stays put)
    assert {it["course_id"] for it in _items(results(lib, "pencil", typing=False), "lecture")} == {"p"}


def test_answers_are_plain_text_without_the_apps_action_blocks(lib):
    lib.create_chat("ch", "p", {}, "sonnet", "")
    lib.add_chat_message("ch", "assistant", "## Bayes' theorem\n\nThe **posterior** is the answer.\n\n"
                                            '```ieustudia\n{"action": "prep_pack", "weeks": [3, 4]}\n```')
    chat = _items(results(lib, "posterior", typing=False), "chat")[0]
    assert chat["snippet"]["text"] == "Bayes' theorem The posterior is the answer." and chat["role"] == "assistant"
    assert not _items(results(lib, "prep_pack", typing=False), "chat")


def test_a_week_says_its_topics_and_a_lecture_its_moments_in_order(lib):
    week = _items(results(lib, "bayes", typing=False), "week")[0]
    assert week["week"] == 3 and week["snippet"]["text"] == "Independence, Reliability & Bayes · Hafta 3"
    _say(lib, "p2", (0, 5, "Bayes theorem, first.", "en"), (40, 45, "Bayes theorem again.", "en"),
         (90, 95, "Bayes theorem, the third time.", "en"), (130, 135, "Only Bayes here.", "en"))
    lec = _items(results(lib, "bayes theorem", kind="lecture", typing=False), "lecture")[0]
    assert [m["ts"] for m in lec["moments"]] == ["00:00", "00:40", "01:30"] and lec["more"] == 0   # (not "Only Bayes")


def test_a_library_from_before_the_index_gets_one(tmp_path, lib):
    lib.db.execute("DELETE FROM meta WHERE key=?", (search.META_KEY,))
    lib.db.commit()
    again = Library(lib.path)
    assert not again.index.ready
    assert results(again, "thevenin", typing=False)["total"] == 1 and again.index.ready
    assert again.get_meta(search.META_KEY) == search.INDEX_VERSION


def test_ai_notes_are_searched_with_their_class_day(lib):
    day = "p|2026-10-14|lecture"
    lib.index.notes = lambda d: [("notes", "Study notes", "## Bayes\n\nThe **posterior** follows from the prior.",
                                  "2026-10-15T10:00:00")] if d == day else []
    lib.index.note_days = lambda: [day]
    lib.index.rebuild()
    notes = _items(results(lib, "posterior", typing=False), "note")
    assert [(n["day_key"], n["note_kind"], n["label"]) for n in notes] == [(day, "notes", "Study notes")]
    assert "**" not in notes[0]["snippet"]["text"]


def test_without_fts5_the_whole_index_is_read(lib, monkeypatch):
    monkeypatch.setattr(lib.index, "fts", False)
    lib.index._vocab = None
    res = results(lib, "quiz one", typing=False)
    assert _items(res, "announcement")[0]["id"] == "a1"


def test_the_ask_ai_search_sends_the_matching_passages(lib):
    from studia.knowledge import retrieve

    text, sources = retrieve(lib, "p", "What is on the midterm?")
    assert "ara sınavda Bayes" in text and sources[0]["kind"] in ("file", "lecture")
    assert retrieve(lib, "p", "what is the of") == ("", [])


def test_marks_are_places_the_page_can_use_also_after_a_pdfs_math_letters():
    text = search.prep("Bayes: 𝑃(𝐴|𝐵) = 𝑃(𝐵|𝐴)𝑃(𝐴)/𝑃(𝐵) is the theorem of Bayes")
    toks = [w for w, _, _ in search.tokens(text)]
    concepts = parse("theorem", Vocab({w: 1 for w in toks}, 1), typing=False)
    s = search.snippet(text, search._occurrences(concepts, toks, search.prepare(concepts)), 400)
    a, b = s["marks"][0]
    assert s["text"].encode("utf-16-le")[2 * a:2 * b].decode("utf-16-le") == "theorem"


def test_a_snippet_leaves_out_what_a_pdfs_text_can_only_show_as_empty_boxes():
    """The owner's MATH 250 and EEE 242 slides: "inverse matrix" showed "A = □ a b c d□" and "de□sign" (a formula's
    brackets as control codes, U+FFFE for a hyphen), and a symbol font's bracket pieces and bullets are private-use
    codes."""
    hyphen, top, mid, bottom, bullet = (chr(c) for c in (0xFFFE, 0xF8EE, 0xF8EF, 0xF8F0, 0xF075))
    text = search.prep(f"Free vars = de{hyphen}sign. For a 2 × 2 matrix A = {chr(0x14)} a b c d {chr(0x15)} : adj A = "
                       f"{top}\n{mid} d −b {bottom}\n{bullet} The inverse matrix of A is A−1 (product{hyphen}of-maxterms)")
    toks = [w for w, _, _ in search.tokens(text)]
    concepts = parse("inverse matrix", Vocab({w: 1 for w in toks}, 1), typing=False)
    s = search.snippet(text, search._occurrences(concepts, toks, search.prepare(concepts)), 400)
    assert s["text"] == ("Free vars = de-sign. For a 2 × 2 matrix A = a b c d : adj A = d −b The inverse matrix of A is "
                         "A−1 (product-of-maxterms)")
    assert [s["text"][a:b] for a, b in s["marks"]] == ["matrix", "inverse", "matrix"]     # still on their words


def test_the_offline_models_passages_are_picked_with_the_same_matching():
    texts = ["Arkadaşlar sınavda Bayes çıkacak.", "This is an example.", "Quiz 2 is next week.", "Quiz 1 is next week."]
    exam = search.overlap("What is on the exam?", texts)
    assert exam[0] >= 1 and exam[1] == exam[2] == exam[3] == 0          # sınavda is an exam; example is not
    quiz = search.overlap("When is quiz 1?", texts)
    assert quiz[3] >= 2 > quiz[2] >= 1 and quiz[0] == 0                  # quiz 1 has both, quiz 2 only the quiz
    assert search.overlap("what is the", texts) == [0.0] * 4


def test_typing_is_fast_on_hundreds_of_lectures(tmp_path):
    """A term of a few hundred lectures (30 paragraphs each): every keystroke well under the 150 ms the page allows
    (here, on a test machine that may be busy, under 600 ms)."""
    import random
    import time

    rnd = random.Random(4)
    vocab = ("probability distribution conditional bayes theorem quiz midterm exam homework week lecture circuit "
             "voltage current sınav olasılık dağılım koşullu arkadaşlar bugün matrix vector determinant").split()
    lib = Library(tmp_path / "big.db")
    lib.upsert_courses([Course(id="p", title="t", code="MATH 240", name="P")])
    recs = [Recording(delivery_id=f"r{i}", course_id="p", folder_id="f", folder_path="", name="n",
                      start=dt.datetime(2026, 9, 1) + dt.timedelta(hours=7 * i), duration_s=3000, viewer_url="v")
            for i in range(300)]
    lib.merge_recordings("p", recs, first_sync=True)
    for r in recs:
        segs = [Segment(k * 10.0, k * 10.0 + 8, " ".join(rnd.choice(vocab) for _ in range(30)), "en", 0.9)
                for k in range(30)]
        lib.save_transcript(Transcript(delivery_id=r.delivery_id, segments=segs, duration_s=3000, speech_s=300,
                                       language_seconds={}, model="m", device="cpu", engine_version="t",
                                       created_at="x"), "x.json")
    lib.index.ensure()
    worst = 0.0
    for q in ("quiz one", "conditional probability", "probabilty", "ara sınav"):
        for k in range(2, len(q) + 1):
            t0 = time.perf_counter()
            results(lib, q[:k])
            worst = max(worst, time.perf_counter() - t0)
    assert worst < 0.6


# ------------------------------------------------------------------ fx-330: what the checks found
def test_average_finds_averages_and_ortalama_never_the_verb_mean(lib):
    """"average" listed 24 class days where only 5 say average or ortalama: "mean" is in its group of synonyms, and
    the verb ("what does it mean", "this means") is everywhere."""
    _say(lib, "p1", (0, 5, "What does it mean? That means the switch is here.", "en"),
         (60, 64, "So which means what?", "en"))
    _say(lib, "p2", (0, 5, "The average of the values is the expected value.", "en"),
         (40, 45, "Örneklemin ortalaması 4 çıktı.", "tr"))
    for q in ("average", "ortalama"):
        days = {it["day_key"].split("|")[1] for it in _items(results(lib, q, typing=False), "lecture")}
        assert days == {"2026-10-14"}, q
    assert "2026-10-07" in {it["day_key"].split("|")[1] for it in _items(results(lib, "mean", typing=False), "lecture")}


def test_a_rare_word_that_finds_a_common_synonym_weighs_as_the_common_one(lib):
    """"What did the lecturer say about the quiz?": "lecturer" is rare, but it finds "hoca", and the students'
    "Hocam" lines outranked the ones about the quiz."""
    _say(lib, "p1", *[(i * 30, i * 30 + 4, f"Hocam, bir sorum var {i}.", "tr") for i in range(12)],
         (400, 405, "By the way, next week we are going to have the quiz on these topics.", "en"))
    _say(lib, "p3", (0, 5, "The quiz is going to be on today's topics.", "en"))
    concepts = parse("What did the lecturer say about the quiz?", lib.index.vocab(), typing=False)
    weight = {c.typed: c.weight for c in concepts}
    assert weight["lecturer"] < weight["quiz"]
    _, hits = search.find(lib.index, "What did the lecturer say about the quiz?", typing=False)
    docs = lib.index.docs([h.id for h in hits[:2]])
    assert all("quiz" in docs[h.id][0].lower() for h in hits[:2])          # the quiz passages first, not "Hocam"
    from studia.knowledge import retrieve

    text, sources = retrieve(lib, "p", "What did the lecturer say about the quiz?")   # the Ask AI: the scene's words
    assert "Hocam" not in text and "next week we are going to have the quiz" in text   # set it, the quiz is asked
    assert "Hocam" in retrieve(lib, "p", "Who is the lecturer?")[0]          # (asked for only that: searched)


def test_the_page_gets_the_librarys_own_words_but_not_the_starts_of_longer_ones(lib):
    _say(lib, "c1", *[(i * 10, i * 10 + 4, f"Another example number {i}, for the exam and its probability.", "en")
                      for i in range(60)], (900, 904, "pro tip: probability problems and process.", "en"))
    known = set(lib.index.known())
    assert {"exam", "example", "quiz", "probability"} <= known
    assert "pro" not in known                                     # once, beside probability and problems: being typed
    a = Api.__new__(Api)
    a._services, a._ready = types.SimpleNamespace(lib=lib), threading.Event()
    a._ready.set()
    got = a.search_rules()
    assert got["ok"] and "exam" in got["rules"]["known"] and got["rules"]["slots"]


def test_formulas_and_tables_in_chats_are_plain_text_in_the_index(lib):
    """A MATH 240 chat came up for "time constant" through \\times in $0.9\\times20=18$, its snippet showed the raw
    LaTeX with "times" marked (a formula with a mark in it can't be drawn) and its table as "| |---|---|"."""
    lib.create_chat("ch", "p", {}, "sonnet", "")
    lib.add_chat_message("ch", "assistant", "So $0.9\\times20=18$ and $P(A \\mid B)=\\frac{1}{2}$.\n\n"
                                            "| Event | P |\n|---|---:|\n| A | 0.5 |")
    assert not _items(results(lib, "times", typing=False), "chat")
    assert not _items(results(lib, "frac", typing=False), "chat")
    chat = _items(results(lib, "event", typing=False), "chat")[0]
    text = chat["snippet"]["text"]
    assert "0.9×20=18" in text and "P(A | B)=(1)/(2)" in text and "$" not in text and "---" not in text
    assert "Event · P" in text


def test_a_search_while_the_index_is_still_being_built_says_so(lib, monkeypatch):
    monkeypatch.setattr(lib.index, "ensure", lambda wait=30.0: False)
    with pytest.raises(RuntimeError, match="still being prepared"):
        results(lib, "quiz", typing=False)
