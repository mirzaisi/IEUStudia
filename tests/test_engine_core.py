import math

from studia.engine.core import (SR, Decoded, choose, clean_text, foreign_script, hallucination_score,
                                       neutral_text, segment_language, speech_units, text_language)


def d(lang, text, lp=-0.1, cr=1.2):
    return Decoded(lang=lang, text=text, avg_logprob=lp, no_speech_prob=0.0, compression_ratio=cr, n_tokens=10)


def test_speech_units_merge_and_absorb_tiny():
    s = lambda a, b: {"start": int(a * SR), "end": int(b * SR)}
    units = speech_units([s(0, 3), s(3.3, 6), s(10, 10.5), s(11, 20), s(30, 33)])
    spans = [(round(u.start / SR, 1), round(u.end / SR, 1)) for u in units]
    assert spans[0] == (0.0, 6.0)          # short pause -> merged
    assert (10.0, 20.0) in spans            # 0.5 s fragment absorbed into its neighbour
    assert spans[-1] == (30.0, 33.0)        # long pause -> own unit


def test_text_language():
    assert text_language("conditional probability. What I mean?") == "en"
    assert text_language("Arkadaşlar, bu konu sınavda çıkacak, dikkat edin.") == "tr"
    assert text_language("Minus four zero minus five.") == "en"
    assert text_language("080808") is None


def test_hallucination_filters():
    assert hallucination_score(d("tr", "Altyazı M.K."), 2.0) == 1.0
    assert hallucination_score(d("tr", "İzlediğiniz için teşekkür ederim."), 5.0) == 1.0
    assert hallucination_score(d("en", "Hello everyone. Today, we will continue with the lecture."), 3.0) == 1.0
    assert hallucination_score(d("tr", "Hãy subscribe cho kênh La La School"), 4.0) >= 0.9
    assert hallucination_score(d("en", "Thank you very much. Thank you.", lp=-0.8), 4.0) >= 0.75
    assert hallucination_score(d("en", "So the Thevenin resistance is 4 ohms.", lp=-0.2), 4.0) == 0.0
    assert foreign_script("6ip até o upload.") and not foreign_script("Şimdi ğüşıöç İĞÜŞÇÖ")


def test_a_few_unsure_words_are_noise_unless_they_say_numbers():
    """Numbers in words count as digits there, in the language decoded ("on" is ten only in Turkish): "Three plus
    three." (MATH 250) was dropped, and "Three and a half." lost to the Turkish "3.30 saat." and became [unclear]."""
    unsure = lambda lang, text: Decoded(lang, text, math.log(0.42), 0.1, 1.0, 6)    # noqa: E731
    for lang, text in (("en", "Three plus three."), ("en", "You must be zero."), ("en", "Three and a half."),
                       ("en", "x equals 2"), ("tr", "Üç artı üç."), ("tr", "SIFIR."), ("tr", "3.30 saat.")):
        assert hallucination_score(unsure(lang, text), 2.0) == 0.0, text
    for lang, text in (("en", "Good afternoon, everyone."), ("en", "Thank you, Steve."), ("en", "Come on."),
                       ("en", "It's not."), ("tr", "değil."), ("tr", "One more."), ("en", "On iki.")):
        assert hallucination_score(unsure(lang, text), 2.0) == 0.76, text
    three = [unsure("en", "Three and a half."), Decoded("tr", "3.30 saat.", math.log(0.26), 0.1, 1.0, 6)]
    assert choose(three, 2.0, "en").text == "Three and a half."


def test_choose_prefers_likelihood_and_handles_identical_text():
    en, tr = d("en", "So good morning everyone.", lp=-0.03), d("tr", "Herkese merhaba.", lp=-0.08)
    assert choose([en, tr], 3.0, "en").lang == "en"
    en2, tr2 = d("en", "What do you think?", lp=-0.84), d("tr", "Siz ne dersiniz?", lp=-0.06)
    assert choose([en2, tr2], 3.0, "en").lang == "tr"
    same_en, same_tr = d("en", "Pivot.", lp=-0.25), d("tr", "Pivot.", lp=-0.23)
    assert choose([same_en, same_tr], 2.0, "en").lang == "en"


def test_same_words_in_both_languages_are_english_unless_turkish_letters_show():
    vec_en, vec_tr = d("en", "Vectors, linearly independent.", lp=-0.25), d("tr", "Vectors, linearly independent.", lp=-0.2)
    assert choose([vec_en, vec_tr], 2.0, "tr").lang == "en"              # even in a Turkish lecture
    digits_en, digits_tr = d("en", "2, 1, 0.", lp=-0.2), d("tr", "2, 1, 0.", lp=-0.3)
    assert choose([digits_en, digits_tr], 2.0, "tr").lang == "en"
    # a word or two: the lecture's main language ("Peki." and "Mesela." are known Turkish words)
    pivot_en, pivot_tr = d("en", "Pivot.", lp=-0.25), d("tr", "Pivot.", lp=-0.23)
    assert choose([pivot_en, pivot_tr], 2.0, "tr").lang == "tr" and choose([pivot_en, pivot_tr], 2.0, "en").lang == "en"
    for word in ("Peki.", "Mesela.", "Aynen."):
        assert choose([d("en", word, lp=-0.2), d("tr", word, lp=-0.3)], 1.5, "en").lang == "tr"
    # Turkish letters, but the classifier is unsure: the lecture's main language
    godel_en, godel_tr = d("en", "Gödel and a proof.", lp=-0.2), d("tr", "Gödel and a proof.", lp=-0.3)
    assert text_language(godel_en.text) is None
    assert choose([godel_en, godel_tr], 2.0, "tr").lang == "tr"
    assert choose([godel_en, godel_tr], 2.0, "en").lang == "en"
    # the classifier is sure: its language, whatever the lecture's
    sure_en, sure_tr = d("en", "Bu konu sınavda.", lp=-0.2), d("tr", "Bu konu sınavda.", lp=-0.3)
    assert choose([sure_en, sure_tr], 2.0, "en").lang == "tr"


def test_the_language_id_prior_weighs_0_2():
    en, tr = d("en", "So we take the derivative.", lp=-0.30), d("tr", "Şimdi türevini alıyoruz.", lp=-0.38)
    assert choose([en, tr], 3.0, "en").lang == "en"                       # ahead by 0.08 + 0.015
    assert choose([en, tr], 3.0, "en", {"en": 0.2, "tr": 0.8}).lang == "tr"   # 0.2 x (0.8 - 0.2) = 0.12 more
    assert choose([en, tr], 3.0, "en", {"en": 0.3, "tr": 0.7}).lang == "en"   # 0.08: not enough


def test_a_passage_of_digits_and_short_letters_is_tagged_english():
    assert segment_language(d("tr", "2 1 0 1 0 0.")) == "en"
    assert segment_language(d("tr", "C1, A1.")) == "en"
    assert segment_language(d("tr", "Bir, iki.")) == "tr"
    assert segment_language(d("en", "Tamam arkadaşlar, şimdi bir örnek yapalım.")) == "tr"
    assert neutral_text("x 2, y 3") and not neutral_text("") and not neutral_text("x eşittir 2")
    assert not neutral_text("Ne o?") and segment_language(d("tr", "Ne o?")) == "tr"      # short words show a language
    assert not neutral_text("O.") and not neutral_text("A-b.") and not neutral_text("1'i")   # letters alone, a suffix
    assert not neutral_text("[unclear]") and not neutral_text("...")


def test_clean_text_collapses_loops():
    assert clean_text("Bugün derik. Bugün derik. Bugün derik.") == "Bugün derik."
    assert clean_text("so ,  we have") == "so, we have"
    assert clean_text("go go go go go") == "go" and clean_text("the the the") == "the"
    assert clean_text("we have, we have, we have the matrix") == "we have the matrix"
    assert clean_text("the 0 the 0 the 0") == "the 0"                       # words among the numbers: a loop


def test_clean_text_keeps_numbers_read_out():
    """A matrix row by row, a binary number digit by digit: 2.0 cut "1, 0, 0, 0, 1" to "1, 0, 1" (MATH 250, EEE 242)."""
    for text in ("The identity matrix is 1, 0, 0, 0, 1, 0, 0, 0, 1.", "1, 2, 0, 3, 4, 0, 0, 0, 5.", "2, 4, 2, 4, 2, 4",
                 "How about this one? One, one, zero, zero, zero, one, zero, one.", "Matris sıfır, sıfır, sıfır, bir.",
                 "SIFIR SIFIR SIFIR", "İki, iki, iki.", "3-30-18-0-0-0-1-2", "binary number 1 1 0 0 0 1 0 1",
                 "minus 1, minus 1, minus 1", "three over two, three over two, three over two", "x, y, x, y, x, y",
                 "eksi bir, eksi bir, eksi bir"):
        assert clean_text(text) == text, text
