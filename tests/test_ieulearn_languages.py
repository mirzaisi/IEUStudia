"""IEULearn in Turkish or in English: the weekly unit plan's dates ("21 Eyl - 27 Eyl" / "21 Sep - 27 Sep") and the
announcements' times give the same weeks and dates. The owner's IEULearn is Turkish; its sign-in page is English
until a student switches it, so another student's pages may well be English. Before, an English plan lost every
date in the fall (no weeks, no lectures in the weekly digest) and shifted the spring by two weeks."""

import datetime as dt
import logging

import pytest

from studia import coursesite
from studia.coursesite import find_dates, month_of, parse_announcements, parse_unit_plan, posted_when
from studia.materials import unit_week, week_starts


def plan(rows):
    return "\n".join(f'<article class="curriculum-item" data-item-id="{500 + i}"><span class="ovup-title-text" '
                     f'title="{title}">{title}</span><div class="item-subtitle">{sub}</div></article>'
                     for i, (title, sub) in enumerate(rows))


FALL = {"tr": [("Hafta 1", "21 Eyl - 27 Eyl"), ("Hafta 2", "28 Eyl - 04 Eki"), ("Hafta 15", "29 Ara - 4 Oca")],
        "en": [("Week 1", "21 Sep - 27 Sep"), ("Week 2", "28 Sep - 04 Oct"), ("Week 15", "29 Dec - 4 Jan")]}


@pytest.mark.parametrize("lang", ["tr", "en"])
def test_a_fall_plan_has_the_same_dates_in_either_language(lang):
    units = parse_unit_plan(plan(FALL[lang]), "2026-1 MATH 240 Olasılık 1")
    assert [(u.start, u.end) for u in units] == [(dt.date(2026, 9, 21), dt.date(2026, 9, 27)),
                                                 (dt.date(2026, 9, 28), dt.date(2026, 10, 4)),
                                                 (dt.date(2026, 12, 29), dt.date(2027, 1, 4))]


SPRING = [("Week 1", "16 Feb - 22 Feb"), ("Week 2", "23 Feb - 1 Mar"), ("Week 3", "2 Mar - 8 Mar"),
          ("Week 7", "30 Mar - 5 Apr"), ("Week 12", "4 May - 10 May"), ("Week 16", "1 Jun - 7 Jun")]


def test_a_spring_plan_in_english_starts_in_february_not_at_its_first_march_unit():
    units = parse_unit_plan(plan(SPRING), "2026-2 MATH 250 Lineer Cebir 3")
    rows = [{"title": u.title, "start_date": u.start.isoformat(), "end_date": u.end.isoformat()} for u in units]
    first = week_starts(rows)
    assert first == dt.date(2027, 2, 16)
    assert [unit_week(r, first) for r in rows] == [1, 2, 3, 7, 12, 16]


@pytest.mark.parametrize("text", ["21 Eylül - 27 Eylül", "21 EYL - 27 EYL", "21 September - 27 September",
                                  "Sep 21 - Sep 27", "September 21 - September 27", "21 Sept - 27 Sept",
                                  "21.09.2026 - 27.09.2026", "2026-09-21 - 2026-09-27", "Week 1 (21 Sep - 27 Sep)"])
def test_other_ways_of_writing_the_same_week(text):
    assert find_dates(text, 2026, 1) == [dt.date(2026, 9, 21), dt.date(2026, 9, 27)]


def test_turkish_capitals_and_words_that_are_no_month():
    assert [month_of(w) for w in ("NİSAN", "Şub", "AĞU", "Mayıs", "Ara.", "Agus")] == [4, 2, 8, 5, 12, 8]
    assert [month_of(w) for w in ("Hafta", "Week", "Konu", "Marks", "Final")] == [None] * 5
    assert find_dates("Hafta 1", 2026, 1) == [] and find_dates("Konu 3 ve 4", 2026, 1) == []


@pytest.mark.parametrize("text", ["19.10.2026 15:53", "10/19/2026 3:53 PM", "19/10/2026 15:53", "19 Oct 2026 15:53",
                                  "Oct 19, 2026, 3:53 PM", "19 Ekim 2026 15:53", "2026-10-19 15:53",
                                  "19 Oct 2026 at 15:53"])
def test_an_announcements_time_in_either_language(text):
    assert posted_when(text, dt.date(2026, 11, 6)) == dt.datetime(2026, 10, 19, 15, 53)


def test_an_announcement_card_in_english_keeps_its_time_and_one_in_turkish_its_old_id():
    card = ('<article class="ov-ann-card"><h4 class="ov-ann-card-title">Quiz 1</h4><div class="ov-ann-card-meta">'
            '<span>Dr. Ayşe Mart</span><span>{when}</span></div><div class="st-ov-ann-full"><p>Next week.</p></div></article>')
    (en,) = parse_announcements(card.format(when="Oct 19, 2026, 3:53 PM"))
    (tr,) = parse_announcements(card.format(when="19.10.2026 15:53"))
    assert en.posted_at == tr.posted_at == dt.datetime(2026, 10, 19, 15, 53) and en.author == "Dr. Ayşe Mart"
    import hashlib                                     # the id made as before: no Turkish announcement is new again

    assert tr.id == hashlib.sha1(f"Quiz 1|Dr. Ayşe Mart|{dt.datetime(2026, 10, 19, 15, 53)}".encode()).hexdigest()[:16]


def test_a_unit_whose_date_cant_be_read_is_said_once_in_the_log(caplog):
    coursesite._undated.discard("2026-1 PHYS 100 Fizik 1")
    page = plan([("Hafta 1", "Birinci hafta"), ("Hafta 2", "İkinci hafta")])
    with caplog.at_level(logging.WARNING, logger="studia"):
        for _ in range(2):
            units = parse_unit_plan(page, "2026-1 PHYS 100 Fizik 1")
    assert [u.start for u in units] == [None, None]
    assert [r.message for r in caplog.records].count(
        "A unit of 2026-1 PHYS 100 Fizik 1 has no date IEUStudia can read: 'Birinci hafta'") == 1


@pytest.mark.parametrize("title,week", [("5. Hafta", 5), ("10. hafta: Bayes", 10), ("Hafta 5", 5), ("Week 5", 5),
                                        ("ÜNİTE 4", 4), ("1. Ara Sınav", None), ("Formula Sheet", None)])
def test_numbered_titles_both_ways_round(title, week):
    assert unit_week({"title": title, "start_date": ""}, None) == week
