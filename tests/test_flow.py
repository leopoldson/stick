from datetime import timedelta
from io import BytesIO

from openpyxl import load_workbook
from sqlalchemy import select

from app import clock, handlers, models


def say(db, text, uid=1):
    return handlers.handle(db, uid, "Favour", text)


def study_now(db, uid=1):
    assert "start a session now" in say(db, "study", uid)[0].text
    return say(db, "cb:begin:now", uid)


def begin_study(db, course="csc301", topic="binary trees", uid=1):
    assert "Which course" in study_now(db, uid)[0].text
    assert "What are you studying" in say(db, course, uid)[0].text
    r = say(db, topic, uid)
    assert "Timer started" in r[0].text
    return r


def make_due(db):
    for r in db.scalars(select(models.Review)).all():
        r.due_on = clock.today_local()
    db.commit()


def test_full_loop_with_questions(db):
    r = begin_study(db, "csc301", "binary trees")
    assert "Timer started: CSC301: Binary Trees" in r[0].text

    r = say(db, "done")
    assert "How solid" in r[0].text and r[0].buttons

    r = say(db, "3")
    assert "Saved" in r[0].text
    review = db.scalars(select(models.Review)).one()
    assert review.step == 2
    assert review.due_on == clock.today_local() + timedelta(days=7)
    assert "Nothing due" in say(db, "due")[0].text

    make_due(db)
    assert "Binary Trees" in say(db, "due")[0].text
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
    begin_study(db, "mat201", "limits")
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
    begin_study(db, "CSC301", "trees")
    say(db, "done 2")
    r = study_now(db)
    assert r[0].buttons[0][0][0] == "CSC301"
    course_cb = r[0].buttons[0][0][1]
    r = say(db, course_cb)
    assert "What are you studying" in r[0].text and r[0].buttons
    r = say(db, r[0].buttons[0][0][1])  # tap the recent topic
    assert "Timer started: CSC301: Trees" in r[0].text


def test_typed_topic_flow_and_already_running(db):
    say(db, "addcourse CSC309 Artificial Intelligence")
    r = say(db, "study csc309")  # shortcut: course given up front
    assert "What are you studying" in r[0].text and "Added" not in r[0].text
    r = say(db, "search algorithms")
    assert "Timer started: CSC309: Search Algorithms" in r[0].text
    assert "already studying" in say(db, "study")[0].text
    assert "Timer cancelled" in say(db, "undo")[0].text


def test_users_cannot_touch_each_others_data(db):
    begin_study(db, "csc301", "trees", uid=1)
    say(db, "done 2", uid=1)
    topic = db.scalars(select(models.Topic)).one()
    course = db.scalars(select(models.Course)).one()
    assert "expired" in say(db, f"cb:topic:{topic.id}", uid=2)[0].text
    assert "expired" in say(db, f"cb:course:{course.id}", uid=2)[0].text
    assert "No courses yet" in say(db, "courses", uid=2)[0].text


def test_stats_streak_and_export(db):
    begin_study(db, "csc301", "trees")
    say(db, "done 3")
    text = say(db, "stats")[0].text
    assert "Streak: 1 day" in text and "CSC301" in text
    assert "1 minute" in text and "1m" not in text

    doc = say(db, "export")[0].document
    assert doc and doc[0].endswith(".xlsx")
    wb = load_workbook(BytesIO(doc[1]))
    assert {"Summary", "Daily Study Log", "Revision Tracker", "Active Recall", "Courses & Topics"} <= set(wb.sheetnames)
    log = wb["Daily Study Log"]
    assert log["B2"].value == "CSC301" and log["C2"].value == "Trees" and log["G2"].value == "Solid"


def test_unknown_and_stale_inputs(db):
    assert "didn't catch" in say(db, "blah")[0].text
    assert "How long did you study" in say(db, "done")[0].text
    assert "Nothing to undo" in say(db, "undo")[0].text
    assert "expired" in say(db, "cb:rate:9")[0].text
    assert "already" in say(db, "cb:recall:pass")[0].text
    assert "Hi Favour" in say(db, "/start")[0].text
    assert "Hi Favour" in say(db, "/start@stickstudy_bot")[0].text


def test_typed_course_is_added_to_the_list_for_next_time(db):
    begin_study(db, "csc301", "trees")
    say(db, "done 2")

    r = study_now(db)
    assert [b[0] for row in r[0].buttons for b in row] == ["CSC301"]
    r = say(db, "data structures")  # a new course typed instead of tapped
    assert "Added Data Structures to your courses" in r[0].text
    say(db, "stacks")
    say(db, "done 2")

    r = study_now(db)
    names = [b[0] for row in r[0].buttons for b in row]
    assert names == ["CSC301", "Data Structures"]
    r = say(db, "CSC301")  # typing an existing course does not duplicate it
    assert "Added" not in r[0].text
    assert db.query(models.Course).count() == 2


def test_other_command_cancels_the_course_question(db):
    assert "Which course" in study_now(db)[0].text
    assert "Nothing due" in say(db, "due")[0].text  # "due" is a command, not a course name
    assert "start a session now" in say(db, "study")[0].text


def test_command_words_are_not_taken_as_course_or_topic_names(db):
    study_now(db)
    say(db, "csc301")
    assert "How long did you study" in say(db, "done")[0].text  # not saved as a topic called "done"
    assert db.query(models.Topic).count() == 0
    assert db.query(models.Course).count() == 1


def test_parse_duration():
    p = handlers.parse_duration
    assert [p(x) for x in ["45", "45m", "45 min", "1h", "1h30", "1h 30m", "1.5h", "1 hour 30 minutes", "90 minutes"]] == [
        45, 45, 45, 60, 90, 90, 90, 90, 90]
    assert p("") is None and p("abc") is None and p("0") is None and p("13h") is None


def test_spoken_durations():
    from app import stats
    assert [stats.fmt_minutes(x) for x in (0, 1, 45, 60, 80, 125)] == [
        "0 minutes", "1 minute", "45 minutes", "1 hour", "1 hour 20 minutes", "2 hours 5 minutes"]


def test_forgot_the_timer_log_after_the_fact(db):
    r = say(db, "done 45")  # no timer running: 45 is minutes, not a rating
    assert "Got it, 45 minutes" in r[0].text and "Type its name" in r[0].text
    r = say(db, "csc301")
    assert "What did you study" in r[0].text and "Added CSC301" in r[0].text
    r = say(db, "trees")
    assert "Logged 45 minutes on CSC301: Trees" in r[0].text and r[0].buttons
    assert "Saved" in say(db, "2")[0].text
    session = db.scalars(select(models.StudySession)).one()
    assert stats_minutes(session) == 45
    assert db.scalars(select(models.Review)).one().step == 1


def stats_minutes(session):
    from app import stats
    return stats.minutes_between(session)


def test_forgot_the_timer_asks_for_the_duration_first(db):
    assert "How long did you study" in say(db, "done")[0].text
    assert "couldn't read" in say(db, "abc")[0].text
    r = say(db, "1h 30m")
    assert "1 hour 30 minutes" in r[0].text


def test_forgot_the_timer_with_buttons(db):
    begin_study(db, "csc301", "trees")
    say(db, "done 2")
    say(db, "done 20")
    r = say(db, "done 20")
    assert "Which course" in r[0].text and r[0].buttons
    r = say(db, r[0].buttons[0][0][1])  # tap CSC301
    assert "What did you study" in r[0].text
    r = say(db, r[0].buttons[0][0][1])  # tap the recent topic
    assert "Logged 20 minutes on CSC301: Trees" in r[0].text


def test_typo_suggests_the_right_command(db):
    r = say(db, "stata")
    assert r[0].text == "Did you mean stats?"
    r = say(db, r[0].buttons[0][0][1])  # tap "Run stats"
    assert "No sessions" in r[0].text
    assert say(db, "dun")[0].text.startswith("Did you mean")
    r = say(db, "xqzv")
    assert "didn't catch" in r[0].text and r[0].buttons[0][0][1] == "cb:run:help"


def test_prompts_have_no_hardcoded_example_topics(db):
    texts = [study_now(db)[0].text, say(db, "csc301")[0].text]
    assert not any("binary" in t.lower() for t in texts)


def test_tricky_questions_are_captured_and_quizzed(db):
    begin_study(db, "csc301", "trees")
    say(db, "done 1")
    make_due(db)
    say(db, "quiz")
    r = say(db, "missed")
    weak = [b for row in r[0].buttons for b in row if b[1].startswith("cb:weak:")]
    assert weak and weak[0][0] == "Add what tripped me up"

    assert "What tripped you up" in say(db, weak[0][1])[0].text
    assert "Saved" in say(db, "How do AVL rotations work?")[0].text
    assert "Saved" in say(db, "Height of a leaf? | Zero")[0].text
    assert "Nothing due" in say(db, "due")[0].text  # a command word ends the capture

    questions = db.scalars(select(models.Question)).all()
    assert {(q.prompt, q.answer) for q in questions} == {
        ("How do AVL rotations work?", ""), ("Height of a leaf?", "Zero")}

    make_due(db)
    prompts = set()
    for _ in range(12):
        prompts.add(say(db, "quiz")[0].text.split("\n\n")[1])
        say(db, "x")  # reveal
        say(db, "cb:recall:pass")
        make_due(db)
    assert prompts == {"How do AVL rotations work?", "Height of a leaf?"}


def test_question_without_an_answer_points_to_notes(db):
    begin_study(db, "csc301", "trees")
    say(db, "done 1")
    topic = db.scalars(select(models.Topic)).one()
    db.add(models.Question(topic_id=topic.id, prompt="Explain rotations", answer=""))
    db.commit()
    make_due(db)
    say(db, "quiz")
    assert "check your notes" in say(db, "no idea")[0].text


def test_cannot_save_tricky_questions_to_someone_elses_topic(db):
    begin_study(db, "csc301", "trees", uid=1)
    say(db, "done 1", uid=1)
    topic = db.scalars(select(models.Topic)).one()
    assert "expired" in say(db, f"cb:weak:{topic.id}", uid=2)[0].text


def test_export_looks_tidy(db):
    begin_study(db, "csc301", "trees")
    say(db, "done 1")
    wb = load_workbook(BytesIO(say(db, "export")[0].document[1]))
    assert wb.sheetnames[0] == "Summary" and wb["Summary"]["A1"].value == "Stick study log"
    log = wb["Daily Study Log"]
    assert log.freeze_panes == "A2" and log.auto_filter.ref.startswith("A1:G")
    assert log["G2"].fill.fgColor.rgb.endswith("FFC7CE")  # Shaky is shaded red
    assert wb["Active Recall"]["A2"].value == "Nothing here yet"


def test_study_asks_whether_to_start_now_or_log_a_past_session(db):
    r = say(db, "study")
    labels = [b[0] for b in r[0].buttons[0]]
    assert labels == ["Start a session now", "Log a past session"]

    r = say(db, "cb:begin:past")
    assert "How long did you study" in r[0].text
    r = say(db, "45")
    assert "Got it, 45 minutes" in r[0].text
    say(db, "csc301")
    r = say(db, "logic gates")
    assert "Logged 45 minutes on CSC301: Logic Gates" in r[0].text


def test_mode_can_be_typed_instead_of_tapped(db):
    say(db, "study")
    assert "Which course" in say(db, "now")[0].text
    say(db, "cancel")
    say(db, "study")
    assert "How long did you study" in say(db, "past")[0].text
    say(db, "cancel")
    say(db, "study")
    r = say(db, "hmm")
    assert "Tap one" in r[0].text and r[0].buttons


def test_start_button_does_not_start_a_second_timer(db):
    begin_study(db, "csc301", "trees")
    assert "already studying" in say(db, "cb:begin:now")[0].text
    assert "already studying" in say(db, "study")[0].text


def test_names_are_stored_in_title_case(db):
    begin_study(db, "discrete structures", "logic gates and circuits")
    say(db, "done 2")
    course = db.scalars(select(models.Course)).one()
    topic = db.scalars(select(models.Topic)).one()
    assert course.code == "Discrete Structures" and topic.name == "Logic Gates and Circuits"

    say(db, "study DISCRETE STRUCTURES")  # same course typed another way: no duplicate
    say(db, "LOGIC GATES AND CIRCUITS")
    say(db, "undo")
    assert db.query(models.Course).count() == 1 and db.query(models.Topic).count() == 1

    say(db, "addcourse csc301 data structures and algorithms")
    assert db.scalars(select(models.Course).where(models.Course.code == "CSC301")).one().title == "Data Structures and Algorithms"


def test_old_lowercase_names_are_tidied_on_startup(factory, monkeypatch):
    from app import maintenance
    monkeypatch.setattr(maintenance, "SessionLocal", factory)
    with factory() as db:
        user = models.User(tg_id=9, name="x")
        db.add(user)
        db.flush()
        course = models.Course(user_id=user.id, code="discrete structures", title="")
        db.add(course)
        db.flush()
        db.add(models.Topic(course_id=course.id, name="binary trees"))
        db.commit()
    assert maintenance.tidy_existing_names() == 2
    assert maintenance.tidy_existing_names() == 0  # safe to run again
    with factory() as db:
        assert db.scalars(select(models.Course)).one().code == "Discrete Structures"
        assert db.scalars(select(models.Topic)).one().name == "Binary Trees"


def test_excel_columns_fit_their_content(db):
    long_topic = "Everything about the history and design of very long topic names " * 2
    begin_study(db, "csc301", long_topic)
    say(db, "done 2")
    begin_study(db, "csc301", "trees")
    say(db, "done 2")
    wb = load_workbook(BytesIO(say(db, "export")[0].document[1]))
    log = wb["Daily Study Log"]
    assert log.column_dimensions["C"].width == 45          # capped, long text wraps
    assert log.row_dimensions[2].height >= 52               # the long row grows to show every line
    assert log.row_dimensions[3].height < log.row_dimensions[2].height
    assert log.column_dimensions["A"].width >= 16           # dates always fit
    assert wb["Summary"].column_dimensions["A"].width >= len("Recall checks passed")
