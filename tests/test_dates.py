from datetime import date

from assistant.projects.dates import format_due, parse_date, parse_task_input

TODAY = date(2026, 10, 15)  # a Thursday


def test_simple_words():
    assert parse_date("today", TODAY) == TODAY
    assert parse_date("Tomorrow", TODAY) == date(2026, 10, 16)


def test_weekday_means_the_next_one():
    assert parse_date("friday", TODAY) == date(2026, 10, 16)
    assert parse_date("next monday", TODAY) == date(2026, 10, 19)
    assert parse_date("thursday", TODAY) == date(2026, 10, 22)  # not today


def test_relative():
    assert parse_date("in 3 days", TODAY) == date(2026, 10, 18)
    assert parse_date("in 2 weeks", TODAY) == date(2026, 10, 29)


def test_numeric_formats_day_first():
    assert parse_date("2026-11-05", TODAY) == date(2026, 11, 5)
    assert parse_date("05/11/2026", TODAY) == date(2026, 11, 5)
    assert parse_date("20/10", TODAY) == date(2026, 10, 20)


def test_month_names():
    assert parse_date("20 oct", TODAY) == date(2026, 10, 20)
    assert parse_date("20th October 2026", TODAY) == date(2026, 10, 20)
    assert parse_date("oct 20", TODAY) == date(2026, 10, 20)


def test_passed_date_without_year_rolls_to_next_year():
    assert parse_date("5 oct", TODAY) == date(2027, 10, 5)


def test_unreadable_dates_return_none():
    for bad in ("", "soonish", "31/02/2026", "13/13/2026", "next", "45 oct"):
        assert parse_date(bad, TODAY) is None


def test_format_due():
    assert "today" in format_due(TODAY, TODAY)
    assert "in 3 days" in format_due(date(2026, 10, 18), TODAY)
    assert "OVERDUE by 2 days" in format_due(date(2026, 10, 13), TODAY)


def test_task_input_with_deadline():
    assert parse_task_input("Post launch thread by friday", TODAY) == (
        "Post launch thread", date(2026, 10, 16), None)
    assert parse_task_input("Send brief due 2026-11-01", TODAY)[1] == date(2026, 11, 1)


def test_task_input_without_deadline():
    assert parse_task_input("Design the banner", TODAY) == ("Design the banner", None, None)


def test_last_by_is_the_deadline():
    title, due, _ = parse_task_input("Get feedback by the team by friday", TODAY)
    assert title == "Get feedback by the team" and due == date(2026, 10, 16)


def test_unreadable_short_deadline_warns_but_keeps_task():
    title, due, warning = parse_task_input("Post thread by soonish", TODAY)
    assert title == "Post thread by soonish" and due is None and "could not read" in warning


def test_long_by_phrase_is_just_part_of_the_title():
    assert parse_task_input("Write post by the end of the whole campaign period", TODAY)[2] is None
