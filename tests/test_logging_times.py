"""Logging a session with its real day and times, and stopping a timer you forgot about. Clock frozen: Sat 14 Nov 2026, 6pm Lagos."""
from datetime import datetime, timedelta, timezone
from io import BytesIO

import pytest
from openpyxl import load_workbook
from sqlalchemy import select

from app import clock, handlers, models

NOW_LOCAL = datetime(2026, 11, 14, 18, 0, tzinfo=clock.LAGOS)
NOW_UTC = datetime(2026, 11, 14, 17, 0)  # Lagos is UTC+1


@pytest.fixture(autouse=True)
def frozen(monkeypatch):
    monkeypatch.setattr(clock, "now_local", lambda: NOW_LOCAL)
    monkeypatch.setattr(clock, "now_utc", lambda: NOW_UTC)


def say(db, text, uid=1):
    return handlers.handle(db, uid, "Favour", text)


def log_session(db, day, start, end, course="csc301", topic="trees"):
    say(db, "log")
    say(db, day)
    say(db, start)
    r = say(db, end)
    say(db, course)
    say(db, topic)
    return r


def start_timer(db, started_utc, course="csc301", topic="trees"):
    say(db, f"study {course}")
    say(db, topic)
    session = db.scalars(select(models.StudySession)).all()[-1]
    session.started_at = started_utc
    db.commit()
    return session


# ---------- logging a past session: day, start, stop ----------

def test_log_with_day_start_and_stop(db):
    assert "Which day" in say(db, "log")[0].text
    r = say(db, "cb:day:1")
    assert "Fri 13 Nov. What time did you start?" in r[0].text
    assert "what time did you stop" in say(db, "9pm")[0].text.lower()
    r = say(db, "11:30pm")
    assert "Fri 13 Nov, 9:00 PM to 11:30 PM (2 hours 30 minutes)" in r[0].text and "Which course was it?" in r[0].text
    say(db, "csc301")
    assert "Logged 2 hours 30 minutes on CSC301: Trees" in say(db, "trees")[0].text
    assert "Saved" in say(db, "2")[0].text
    s = db.scalars(select(models.StudySession)).one()
    assert (s.started_at, s.ended_at) == (datetime(2026, 11, 13, 20, 0), datetime(2026, 11, 13, 22, 30))  # stored as UTC


def test_the_study_menu_leads_to_the_same_flow(db):
    say(db, "study")
    assert "Which day" in say(db, "cb:begin:past")[0].text
    assert "What time did you start" in say(db, "12 nov")[0].text


def test_session_that_runs_past_midnight(db):
    r = log_session(db, "12 nov", "11pm", "1am")
    assert "Thu 12 Nov, 11:00 PM to Fri 13 Nov, 1:00 AM (2 hours)" in r[0].text
    s = db.scalars(select(models.StudySession)).one()
    assert (s.started_at, s.ended_at) == (datetime(2026, 11, 12, 22, 0), datetime(2026, 11, 13, 0, 0))


def test_end_can_be_a_length_instead_of_a_time(db):
    r = log_session(db, "monday", "7:30pm", "45 minutes")
    assert "Mon 09 Nov, 7:30 PM to 8:15 PM (45 minutes)" in r[0].text
    r = log_session(db, "yesterday", "8pm", "1h30", course="ict305", topic="osi")
    assert "(1 hour 30 minutes)" in r[0].text


def test_bad_inputs_get_a_clear_message_and_a_second_chance(db):
    say(db, "log")
    assert "couldn't read that date" in say(db, "abc")[0].text
    assert "couldn't read that date" in say(db, "tomorrow")[0].text
    assert "couldn't read that date" in say(db, "15 nov")[0].text  # a day ahead is a slip, not last year
    assert "What time did you start" in say(db, "today")[0].text
    assert "couldn't read that time" in say(db, "later")[0].text
    assert "what time did you stop" in say(db, "5pm")[0].text.lower()
    assert "hasn't happened yet" in say(db, "7pm")[0].text   # it's 6pm now
    assert "hasn't happened yet" in say(db, "5 hours")[0].text
    assert "couldn't read that" in say(db, "whenever")[0].text
    assert "5:00 PM to 5:45 PM" in say(db, "45 minutes")[0].text  # still in the flow, and it works now


def test_a_session_cannot_be_absurdly_long_or_backwards(db):
    say(db, "log")
    say(db, "yesterday")
    say(db, "9am")
    assert "over 16 hours" in say(db, "8am")[0].text   # 8am is read as the next morning: 23 hours
    assert "finishes before it starts" not in say(db, "10am")[0].text


def test_a_command_word_leaves_the_flow(db):
    say(db, "log")
    say(db, "cb:day:0")
    assert "Nothing due" in say(db, "due")[0].text
    assert db.query(models.StudySession).count() == 0


# ---------- stopping a timer you forgot about ----------

def test_done_on_an_old_timer_asks_when_you_really_stopped(db):
    start_timer(db, datetime(2026, 11, 13, 20, 0))  # started Fri 9pm Lagos
    r = say(db, "done")
    assert "running since Fri 13 Nov, 9:00 PM" in r[0].text and r[0].buttons[0][0][0] == "Just now"
    r = say(db, "11:30pm")  # no day given: the day it started
    assert "Logged 2 hours 30 minutes on CSC301: Trees" in r[0].text and r[0].buttons
    s = db.scalars(select(models.StudySession)).one()
    assert s.ended_at == datetime(2026, 11, 13, 22, 30)


def test_the_rating_given_with_done_is_kept_while_asking(db):
    start_timer(db, datetime(2026, 11, 13, 20, 0))
    assert "When did you actually stop" in say(db, "done 3")[0].text
    assert "Saved" in say(db, "yesterday 11:30pm")[0].text
    assert db.scalars(select(models.StudySession)).one().rating == 3
    assert db.scalars(select(models.Review)).one().step == 1  # a Solid session


def test_just_now_keeps_the_old_behaviour(db):
    start_timer(db, datetime(2026, 11, 13, 20, 0))
    say(db, "done")
    assert "Logged 21 hours on CSC301: Trees" in say(db, "cb:stop:now")[0].text


def test_stop_times_are_checked(db):
    start_timer(db, datetime(2026, 11, 13, 20, 0))
    say(db, "done")
    assert "couldn't read" in say(db, "soon")[0].text
    assert "finishes before it starts" in say(db, "12 nov 9pm")[0].text
    assert "hasn't happened yet" in say(db, "tomorrow 9am")[0].text or "couldn't read" in say(db, "tomorrow 9am")[0].text
    assert "Logged" in say(db, "10pm")[0].text


def test_a_fresh_timer_stops_with_no_questions(db):
    start_timer(db, NOW_UTC - timedelta(hours=1))
    r = say(db, "done")
    assert "Logged 1 hour on CSC301: Trees" in r[0].text and "When did you" not in r[0].text


def test_a_timer_running_for_hours_today_is_also_questioned(db):
    start_timer(db, NOW_UTC - timedelta(hours=6))
    assert "When did you actually stop" in say(db, "done")[0].text


def test_done_with_a_stop_time_in_one_message(db):
    start_timer(db, datetime(2026, 11, 14, 14, 0))  # today 3pm
    assert "hasn't happened yet" in say(db, "done 9:30pm")[0].text
    assert "Logged 2 hours on CSC301: Trees" in say(db, "done 5pm")[0].text
    assert "couldn't read" in (say(db, "study csc301") and say(db, "x") and say(db, "done 45"))[0].text


def test_done_45_without_a_timer_says_it_assumed_the_end_was_now(db):
    r = say(db, "done 45")
    assert "Sat 14 Nov, 5:15 PM to 6:00 PM (45 minutes), ending just now" in r[0].text


# ---------- the Excel shows exactly what was logged ----------

def test_excel_shows_duration_in_words_and_marks_overnight_sessions(db):
    log_session(db, "12 nov", "11pm", "1am")
    log_session(db, "yesterday", "9pm", "9:01pm", topic="osi")
    say(db, "2")
    wb = load_workbook(BytesIO(say(db, "export")[0].document[1]))
    ws = wb["Daily Study Log"]
    headers = [c.value for c in ws[1]]
    assert headers == ["Date", "Course", "Topic", "Started", "Ended", "Minutes", "Duration", "How solid"]
    first, second = [{h: c.value for h, c in zip(headers, row)} for row in ws.iter_rows(min_row=2, max_row=3)]
    assert (first["Started"], first["Ended"], first["Duration"]) == ("23:00", "01:00 (+1 day)", "2 hours")
    assert second["Duration"] == "1 minute" and second["Ended"] == "21:01"
    assert "1h" not in str(wb["Dashboard"]["B4"].value)
