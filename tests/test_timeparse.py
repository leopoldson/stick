from datetime import date, time

import pytest

from app.timeparse import parse_date, parse_time, parse_when

TODAY = date(2026, 11, 14)  # a Saturday


@pytest.mark.parametrize("typed,expected", [
    ("9pm", time(21, 0)), ("7:30 pm", time(19, 30)), ("19:30", time(19, 30)), ("2130", time(21, 30)),
    ("930", time(9, 30)), ("9", time(9, 0)), ("12am", time(0, 0)), ("12pm", time(12, 0)),
    ("7.30pm", time(19, 30)), ("7h30", time(7, 30)), ("11:59 PM", time(23, 59)),
    ("13pm", None), ("25:00", None), ("9:75", None), ("abc", None), ("1.5h", None), ("", None),
])
def test_parse_time(typed, expected):
    assert parse_time(typed) == expected


def test_a_lone_number_is_not_a_time_when_minutes_are_possible():
    assert parse_time("9", bare_ok=False) is None
    assert parse_time("9pm", bare_ok=False) == time(21, 0)


@pytest.mark.parametrize("typed,expected", [
    ("today", date(2026, 11, 14)), ("yesterday", date(2026, 11, 13)), ("2 days ago", date(2026, 11, 12)),
    ("monday", date(2026, 11, 9)), ("sat", date(2026, 11, 7)),  # the most recent one *before* today
    ("12 nov", date(2026, 11, 12)), ("12th November", date(2026, 11, 12)), ("nov 12", date(2026, 11, 12)),
    ("12/11", date(2026, 11, 12)), ("12/11/26", date(2026, 11, 12)), ("2026-11-12", date(2026, 11, 12)),
    ("20 jan", date(2026, 1, 20)),             # more than two months ahead this year: it must mean the one just gone
    ("28 dec", None), ("15 nov", None),         # a few weeks or a day ahead is a slip, not last year
    ("tomorrow", None), ("31 feb", None), ("14 nov 2027", None), ("", None), ("hello", None),
])
def test_parse_date(typed, expected):
    assert parse_date(typed, TODAY) == expected


@pytest.mark.parametrize("typed,expected", [
    ("9pm", (None, time(21, 0))),
    ("yesterday 11:30pm", (date(2026, 11, 13), time(23, 30))),
    ("12 nov 9pm", (date(2026, 11, 12), time(21, 0))),
    ("12 nov 21:00", (date(2026, 11, 12), time(21, 0))),
    ("tomorrow 9pm", None), ("hello", None),
])
def test_parse_when(typed, expected):
    assert parse_when(typed, TODAY) == expected
