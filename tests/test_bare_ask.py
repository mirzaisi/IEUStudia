"""A passing "I will ask" or "soracağım" (the lecturer will ask the coordinator, a student asks a question) is no exam
hint in the heads-up or the transcript's marks; with an exam named in the same paragraph it is."""
import pytest

from studia import alerts
from studia.outputs import highlights

NOT_EXAM = [
    ("Hocam, bir şey soracağım, bu formül nereden geldi?", "tr"),
    ("Ben de soracağım şimdi koordinatöre, raporlar için.", "tr"),
    ("Now I will ask you something. What is the determinant here?", "en"),
]
EXAM = [
    ("Bunu sınavda soracağım, iyi çalışın.", "tr"),
    ("I will ask this in the midterm, so practise it.", "en"),
    ("Vizede buna benzer bir soru sorarım.", "tr"),
]


@pytest.mark.parametrize("text,lang", NOT_EXAM)
def test_a_passing_ask_is_no_exam_hint(text, lang):
    assert "exam" not in alerts.matches(text, lang)
    assert "exam" not in highlights.detect(text, lang)


@pytest.mark.parametrize("text,lang", EXAM)
def test_an_ask_with_an_exam_named_is_one(text, lang):
    assert "exam" in alerts.matches(text, lang)
    assert "exam" in highlights.detect(text, lang)


def test_the_heads_ups_other_exam_cues_need_no_exam_word():
    assert "exam" in alerts.matches("You are responsible for the proof of this theorem.", "en")


def test_the_other_cues_of_a_paragraph_stay():
    text = "Ben de soracağım koordinatöre ama bu çok önemli, unutmayın."
    assert "exam" not in alerts.matches(text, "tr") and "important" in alerts.matches(text, "tr")
    assert highlights.detect(text, "tr") == ["important"]
