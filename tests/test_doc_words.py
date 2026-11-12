"""The words IEUStudia itself writes into a study document follow the document's language (doclang.writing_in): a
Turkish study pack, cheatsheet, practice quiz or exported note doesn't say "Contents", "Example 4" or "Figure 1.2".
An English one reads as before. And "Copy for AI" asks for the notes in the language the app's own notes use."""

import datetime as dt

from studia import cheatsheet, doclang, quizzes, studypack
from studia.outputs import doc as D
from studia.outputs.ai_copy import build_ai_text

CHAPTER = """Ohm yasası gerilim ile akımı bağlar.

:::definition Direnç
Bir elemanın akıma karşı koyması.
:::

:::example Örnek 4: Gerilim bölücü
$v_2 = 6$ V.
:::

> [!NOTE]
> Birimler SI.

[[FIGURE f1]]
"""


def _pack_facts(language):
    return {"course": {"code": "EEE 215"}, "span": "weeks 4–6", "weeks": [{"no": 4}, {"no": 5}, {"no": 6}],
            "files": [{"name": "Hafta4.pdf"}], "lectures": [{"date": "2026-10-14"}], "language": language}


def _pack(language):
    outline = {"title": "Devreler", "intro": "", "chapters": [{"id": "c1", "title": "Ohm yasası",
                                                                 "figures": [{"id": "f1"}]}]}
    figures = {"f1": {"kind": "frame", "image": "a.jpg", "caption": "Bölücü", "source": "x"}}
    meta = studypack.meta_of(_pack_facts(language), dt.date(2026, 11, 12))
    return studypack.document_body(outline, {"c1": CHAPTER}, figures, meta, str), meta


def test_the_words_are_english_outside_a_document_and_turkish_in_a_turkish_one():
    assert doclang.word("Contents") == "Contents" and doclang.weeks_text([3, 5, 6, 7]) == "week 3, weeks 5–7"
    with doclang.writing_in("Turkish") as lang:
        assert lang == "tr" and doclang.writing() == "tr"
        assert doclang.word("Contents") == "İçindekiler"
        assert doclang.word("Figure {n}.", n="1.2") == "Şekil 1.2."
        assert doclang.weeks_text([3, 5, 6, 7]) == "3. hafta, 5–7. haftalar"
        assert doclang.weeks_text([]) == "şimdiye kadarki bütün haftalar"
        assert doclang.date_text(dt.date(2026, 11, 12)) == "12 Kas 2026" and doclang.day_text("2026-10-14") == "14 Eki"
        assert doclang.first_upper("ilk hafta") == "İlk hafta"
        assert doclang.word("No such word") == "No such word"
    assert doclang.writing() == "en" and doclang.date_text("2026-11-12") == "12 Nov 2026"
    assert doclang.day_text("not a date") == "not a date"


def test_a_turkish_study_pack_has_its_cover_contents_boxes_and_figures_in_turkish():
    body, meta = _pack("Turkish")
    assert meta["span"] == "4–6. haftalar" and meta["date"] == "12 Kas 2026"
    assert '<div class="sp-kicker">EEE 215 · Konu anlatımı</div>' in body
    assert "4–6. haftalar · IEUStudia ile 12 Kas 2026 tarihinde hazırlandı" in body
    assert "Dosyalar: Hafta4.pdf" in body and "Dersler: 14 Eki" in body and "yapay zeka yazdı" in body
    assert "<h2>İçindekiler</h2>" in body
    assert "<b>Tanım</b> · Direnç" in body
    assert "<b>Örnek 4</b> · Gerilim bölücü" in body            # the AI's "Örnek 4" isn't said twice
    assert "<b>Not</b>" in body and "<b>Şekil 1.1.</b>" in body and 'alt="Ders slaytı"' in body
    for english in ("Contents", "Study pack", "Definition", "Example", "Figure", "made with", "Lectures:"):
        assert english not in body
    page = studypack.document_html(body, "T", None, meta["language"])
    assert '<html lang="tr">' in page


def test_an_english_study_pack_reads_as_before():
    body, meta = _pack("English")
    assert meta["span"] == "Weeks 4–6" and meta["date"] == "12 Nov 2026"
    assert "EEE 215 · Study pack" in body and "Weeks 4–6 · made with IEUStudia on 12 Nov 2026" in body
    assert "Files: Hafta4.pdf" in body and "Lectures: 14 Oct" in body and "<h2>Contents</h2>" in body
    assert "<b>Definition</b> · Direnç" in body and "<b>Figure 1.1.</b>" in body and "<b>Note</b>" in body
    assert '<html lang="en">' in studypack.document_html(body, "T")


def test_a_pack_made_before_the_setting_takes_its_language_from_its_material():
    facts = {**_pack_facts(None), "pack": "Bu derste olasılık dağılımlarını, koşullu olasılığı ve çarpım kuralını "
                                          "gördük; örnekleri çözdük."}
    del facts["language"]
    assert studypack.meta_of(facts, dt.date(2026, 11, 12))["language"] == "Turkish"
    assert studypack._source(facts | {"files": [{"ref": "F1", "name": "H.pdf"}]},
                             {"kind": "slide", "ref": "F1", "page": 4}) == "H.pdf, sayfa 4"


def test_a_turkish_cheatsheet_has_its_title_line_and_entry_tags_in_turkish():
    facts = {**_pack_facts("Turkish"), "course": {"code": "EEE 215", "name": "Devre Analizi"},
             "files": [{"name": f"H{n}.pdf"} for n in range(7)]}
    meta = cheatsheet.meta_of(facts, dt.date(2026, 11, 12))
    sheet = {"title": "Ara sınav", "topics": [{"id": "t1", "title": "Temel yasalar"}]}
    entries = {"t1": [{"kind": "rule", "name": "Ohm yasası", "tex": "v = iR", "text": "", "where": [],
                       "when": "doğrusal eleman"}]}
    body = cheatsheet.document_body(sheet, entries, meta)
    assert '<span class="cs-kind">Formül kâğıdı</span>' in body
    assert "4–6. haftalar · H0.pdf, H1.pdf, H2.pdf, H3.pdf, H4.pdf ve 2 dosya daha · ders: 14 Eki · 12 Kas 2026" in body
    assert '<i class="cs-k">Kural</i>' in body and "<b>koşul</b> doğrusal eleman" in body
    assert '<html lang="tr">' in cheatsheet.document_html(body, "T", None, meta["language"])
    english = cheatsheet.document_body(sheet, entries, cheatsheet.meta_of({**facts, "language": "English"},
                                                                          dt.date(2026, 11, 12)))
    assert "Cheatsheet" in english and "and 2 more files · lecture 14 Oct · 12 Nov 2026" in english
    assert '<i class="cs-k">Rule</i>' in english and "<b>if</b>" in english


def _quiz(language):
    return {"title": "Q", "created_at": "2026-11-05T10:00:00", "result": {
        "language": language, "title": "Olasılık", "made": "2026-11-05", "course": {"code": "MATH 250"},
        "coverage": {"span": "week 3", "weeks": [{"no": 3}], "focus": None, "materials_missing": [],
                     "days_missing": ["2026-10-21"], "topics_missing": []},
        "questions": [
            {"n": 1, "type": "mc", "q": "Hangisi?", "options": ["a", "b", "c", "d"], "answer": 1, "why": "Çünkü.",
             "wrong": [], "source": {"kind": "lecture", "date": "2026-10-14", "part": 1, "ts": "12:30"}},
            {"n": 2, "type": "problem", "q": "Hesapla.", "steps": ["x = 2"], "model": "5",
             "source": {"kind": "file", "name": "Hafta3.pdf", "page": 4}}]}}


def test_a_turkish_practice_quiz_has_its_cover_and_headings_in_turkish():
    body = quizzes.document_body(_quiz("Turkish"))
    assert "MATH 250 · Deneme sınavı" in body and "3. hafta konularından 2 soru" in body
    assert "IEUStudia ile 5 Kas 2026 tarihinde hazırlandı" in body and "Cevaplar sonda." in body
    assert "Dışarıda kalanlar: yazıya dökülmemiş dersler: 2026-10-21" in body
    assert "Kaynak: 14 Eki dersi, 1. bölüm, 12:30" in body and "Kaynak: Hafta3.pdf, sayfa 4" in body
    assert "<h2>Sorular</h2>" in body and "<h2>Cevaplar</h2>" in body and "<b>Cevap:</b>" in body
    english = quizzes.document_body(_quiz("English"))
    assert "MATH 250 · Practice quiz" in english and "2 questions on week 3" in english
    assert "From Lecture 14 Oct, part 1, 12:30" in english and "<h2>Questions</h2>" in english


def test_an_exported_turkish_note_has_its_boxes_labels_in_turkish():
    md = ":::definition Direnç\nBir elemanın akıma karşı koyması.\n:::\n\n> [!TIP]\n> Birimleri yaz.\n"
    tr = D.Doc(title="Notlar", blocks=D.read_markdown(md), lang="tr")
    en = D.Doc(title="Notes", blocks=D.read_markdown(md), lang="en")
    assert "**Tanım · Direnç**" in D.to_markdown(tr) and "**Definition · Direnç**" in D.to_markdown(en)
    assert "Tanım: Direnç" in D.to_text(tr) and "İpucu" in D.to_text(tr) and "Tip" in D.to_text(en)
    html = D.to_html(tr)
    assert "<b>Tanım</b>" in html and "<b>İpucu</b>" in html and "Definition" not in html
    assert doclang.writing() == "en"                    # (only while it was drawn)


def test_copy_for_ai_asks_for_the_notes_in_the_language_the_apps_own_notes_use():
    from test_outputs import COURSE, SEGS, rec, tr

    days = [([rec("a", 8, 28)], {"a": tr("a", SEGS)})]
    seen = []

    def lang(body):
        seen.append(body)
        return {"language": "Turkish", "taught_in": "Turkish"}

    text = build_ai_text("notes", COURSE, days, lang=lang)
    assert "Write in Turkish, the language the course is taught in, headings included." in text
    assert seen and "[00:15] [TR]" in seen[0]
    assert "Write in English" in build_ai_text("notes", COURSE, days)          # (no setting known: as before)
