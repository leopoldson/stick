from datetime import timedelta
from io import BytesIO

from openpyxl import load_workbook
from sqlalchemy import select

from app import clock, handlers, models


def say(db, text, uid=1):
    return handlers.handle(db, uid, "Favour", text)


def make_due(db):
    for r in db.scalars(select(models.Review)).all():
        r.due_on = clock.today_local()
    db.commit()


def test_full_loop_with_questions(db):
    r = say(db, "study csc301 binary trees")
    assert "Timer started: CSC301: binary trees" in r[0].text

    r = say(db, "done")
    assert "How solid" in r[0].text and r[0].buttons

    r = say(db, "3")
    assert "Saved" in r[0].text
    review = db.scalars(select(models.Review)).one()
    assert review.step == 2
    assert review.due_on == clock.today_local() + timedelta(days=7)
    assert "Nothing due" in say(db, "due")[0].text

    make_due(db)
    assert "binary trees" in say(db, "due")[0].text
    say(db, "addq What is a BST? | Ordered binary tree")
    assert "2 questions" in say(db, "addq Height of a tree? | Longest root-to-leaf path")[0].text

    r = say(db, "quiz")
    assert "Type your answer" in r[0].text
    r = say(db, "no idea")
    assert "Answer:" in r[0].text
    r = say(db, "cb:recall:pass")
    assert "comes back" in r[0].text
    db.refresh(review)
    assert review.step == 3 and review.due_on == clock.today_local() + timedelta(days=14)


def test_missed_recall_resets_and_flags_revision(db):
    say(db, "study MAT201 limits")
    say(db, "done 1")  # rating inline
    review = db.scalars(select(models.Review)).one()
    assert review.step == 0
    make_due(db)

    r = say(db, "quiz")
    assert "Recall everything" in r[0].text  # no saved questions: self-check mode
    r = say(db, "missed")  # typed answer works as well as the button
    assert "revise list" in r[0].text
    db.refresh(review)
    assert review.step == 0 and review.needs_revision is True
    assert review.due_on == clock.today_local() + timedelta(days=1)

    review.due_on = clock.today_local()
    db.commit()
    assert "missed last time" in say(db, "due")[0].text


def test_course_buttons_and_recent_topics(db):
    say(db, "study CSC301 trees")
    say(db, "done 2")
    r = say(db, "study")
    assert r[0].buttons[0][0][0] == "CSC301"
    course_cb = r[0].buttons[0][0][1]
    r = say(db, course_cb)
    assert "What are you studying" in r[0].text and r[0].buttons
    r = say(db, r[0].buttons[0][0][1])  # tap the recent topic
    assert "Timer started: CSC301: trees" in r[0].text


def test_typed_topic_flow_and_already_running(db):
    say(db, "addcourse CSC309 Artificial Intelligence")
    r = say(db, "study csc309")
    assert "What are you studying" in r[0].text
    r = say(db, "search algorithms")
    assert "Timer started: CSC309: search algorithms" in r[0].text
    assert "already studying" in say(db, "study csc309 other")[0].text
    assert "Timer cancelled" in say(db, "undo")[0].text


def test_users_cannot_touch_each_others_data(db):
    say(db, "study csc301 trees", uid=1)
    say(db, "done 2", uid=1)
    topic = db.scalars(select(models.Topic)).one()
    course = db.scalars(select(models.Course)).one()
    assert "expired" in say(db, f"cb:topic:{topic.id}", uid=2)[0].text
    assert "expired" in say(db, f"cb:course:{course.id}", uid=2)[0].text
    assert "No courses yet" in say(db, "courses", uid=2)[0].text


def test_stats_streak_and_export(db):
    say(db, "study csc301 trees")
    say(db, "done 3")
    text = say(db, "stats")[0].text
    assert "Streak: 1 day" in text and "CSC301" in text

    doc = say(db, "export")[0].document
    assert doc and doc[0].endswith(".xlsx")
    wb = load_workbook(BytesIO(doc[1]))
    assert {"Summary", "Daily Study Log", "Revision Tracker", "Active Recall", "Courses & Topics"} <= set(wb.sheetnames)
    log = wb["Daily Study Log"]
    assert log["B2"].value == "CSC301" and log["C2"].value == "trees" and log["G2"].value == "Solid"


def test_unknown_and_stale_inputs(db):
    assert "didn't catch" in say(db, "blah")[0].text
    assert "No timer running" in say(db, "done")[0].text
    assert "Nothing to undo" in say(db, "undo")[0].text
    assert "expired" in say(db, "cb:rate:9")[0].text
    assert "already" in say(db, "cb:recall:pass")[0].text
    assert "Hi Favour" in say(db, "/start")[0].text
    assert "Hi Favour" in say(db, "/start@stickstudy_bot")[0].text
