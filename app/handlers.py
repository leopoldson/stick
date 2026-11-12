"""Channel-independent bot logic: text in, replies out.

Nothing here knows about Telegram. Button taps arrive as text starting with "cb:",
so a WhatsApp adapter can reuse all of this later.
"""
import json
import random
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import clock, srs, stats
from .export import build_workbook
from .models import Course, Question, RecallLog, Review, StudySession, Topic, User

Buttons = list[list[tuple[str, str]]]


@dataclass
class Reply:
    text: str = ""
    buttons: Optional[Buttons] = None
    document: Optional[tuple[str, bytes]] = None


WELCOME = (
    "Hi {name}, I'm Stick. I log your study sessions, then bring each topic back right before you'd forget it.\n\n"
    "study CSC301 binary trees : start a session\n"
    "done : stop the timer and log it\n"
    "due : what to review today\n"
    "quiz : test yourself\n"
    "addq question | answer : add a question to your last topic\n"
    "stats : your week and streak\n"
    "export : download your Excel log\n"
    "undo : cancel or delete your last session\n\n"
    "Start with: study <course code> <topic>"
)
PASS_WORDS = {"got it", "got", "pass", "yes", "y"}
FAIL_WORDS = {"missed", "miss", "fail", "no", "n"}


# ---------- small helpers ----------

def parse(text: str) -> tuple[str, str, bool]:
    t = text.strip()
    if t.startswith("cb:"):  # button tap, e.g. "cb:rate:3"
        return "cb", t[3:], False
    slash = t.startswith("/")
    if slash:
        t = t[1:]
    parts = t.split(None, 1)
    cmd = parts[0].split("@")[0].lower() if parts else ""
    arg = parts[1].strip() if len(parts) > 1 else ""
    return cmd, arg, slash


def set_state(user: User, state: Optional[str], **data) -> None:
    user.state = state
    user.state_data = json.dumps(data)


def get_data(user: User) -> dict:
    try:
        return json.loads(user.state_data or "{}")
    except ValueError:
        return {}


def label(topic: Topic) -> str:
    return f"{topic.course.code}: {topic.name}"


def when(d: date) -> str:
    today = clock.today_local()
    if d == today + timedelta(days=1):
        return "tomorrow"
    return d.strftime("%a %d %b")


def chunk(items: list, n: int) -> list[list]:
    return [items[i:i + n] for i in range(0, len(items), n)]


def get_user(db: Session, tg_id: int, name: str) -> User:
    user = db.scalar(select(User).where(User.tg_id == tg_id))
    if not user:
        user = User(tg_id=tg_id, name=name or "")
        db.add(user)
        db.flush()
    return user


def get_course(db: Session, user: User, code: str, create: bool = False) -> Optional[Course]:
    code = code.upper()
    course = db.scalar(select(Course).where(Course.user_id == user.id, Course.code == code))
    if not course and create:
        course = Course(user_id=user.id, code=code)
        db.add(course)
        db.flush()
    return course


def get_topic(db: Session, course: Course, name: str) -> Topic:
    topic = db.scalar(select(Topic).where(Topic.course_id == course.id, func.lower(Topic.name) == name.lower()))
    if not topic:
        topic = Topic(course_id=course.id, name=name)
        db.add(topic)
        db.flush()
    return topic


def owned_topic(db: Session, user: User, topic_id: int) -> Optional[Topic]:
    topic = db.get(Topic, topic_id)
    return topic if topic and topic.course.user_id == user.id else None


def active_session(db: Session, user: User) -> Optional[StudySession]:
    return db.scalar(
        select(StudySession).where(StudySession.user_id == user.id, StudySession.ended_at.is_(None))
    )


def due_reviews(db: Session, user: User) -> list[Review]:
    return list(db.scalars(
        select(Review)
        .where(Review.user_id == user.id, Review.due_on <= clock.today_local())
        .order_by(Review.needs_revision.desc(), Review.due_on, Review.id)
    ).all())


# ---------- entry point ----------

def handle(db: Session, tg_id: int, name: str, text: str) -> list[Reply]:
    user = get_user(db, tg_id, name)
    cmd, arg, slash = parse(text)
    lowered = text.strip().lower()

    if cmd == "cb":
        replies = on_callback(db, user, arg)
    elif cmd == "cancel":
        set_state(user, None)
        replies = [Reply("Okay, cancelled.")]
    elif user.state == "awaiting_topic" and not slash:
        replies = on_topic_typed(db, user, text.strip())
    elif user.state == "rating" and cmd in ("1", "2", "3"):
        replies = rate(db, user, int(cmd))
    elif user.state == "quiz_grading" and lowered in PASS_WORDS | FAIL_WORDS:
        replies = grade(db, user, lowered in PASS_WORDS)
    elif user.state == "quiz_answering" and not slash:
        replies = reveal_answer(db, user)
    else:
        if user.state in ("awaiting_topic", "quiz_answering"):
            set_state(user, None)  # they moved on to a different command
        replies = on_command(db, user, cmd, arg)

    db.commit()
    return replies


def on_command(db: Session, user: User, cmd: str, arg: str) -> list[Reply]:
    table = {
        "start": lambda: [Reply(WELCOME.format(name=user.name or "there"))],
        "help": lambda: [Reply(WELCOME.format(name=user.name or "there"))],
        "study": lambda: cmd_study(db, user, arg),
        "done": lambda: cmd_done(db, user, arg),
        "due": lambda: cmd_due(db, user),
        "quiz": lambda: cmd_quiz(db, user),
        "addq": lambda: cmd_addq(db, user, arg),
        "stats": lambda: cmd_stats(db, user),
        "export": lambda: cmd_export(db, user),
        "undo": lambda: cmd_undo(db, user),
        "courses": lambda: cmd_courses(db, user),
        "addcourse": lambda: cmd_addcourse(db, user, arg),
    }
    fn = table.get(cmd)
    if fn:
        return fn()
    return [Reply("I didn't catch that. Send help to see what I can do.")]


def on_callback(db: Session, user: User, arg: str) -> list[Reply]:
    kind, _, value = arg.partition(":")
    if kind == "quiz":
        return cmd_quiz(db, user)
    if kind == "course" and value.isdigit():
        course = db.get(Course, int(value))
        if course and course.user_id == user.id:
            return ask_topic(db, user, course)
    if kind == "topic" and value.isdigit():
        topic = owned_topic(db, user, int(value))
        if topic and not active_session(db, user):
            return begin(db, user, topic)
    if kind == "rate" and value in ("1", "2", "3"):
        return rate(db, user, int(value))
    if kind == "recall" and value in ("pass", "fail"):
        return grade(db, user, value == "pass")
    return [Reply("That button has expired. Send help to see your options.")]


# ---------- studying ----------

def cmd_study(db: Session, user: User, arg: str) -> list[Reply]:
    current = active_session(db, user)
    if current:
        mins = round((clock.now_utc() - current.started_at).total_seconds() / 60)
        return [Reply(f"You're already studying {label(current.topic)} ({mins} min in). Send done when you finish, or undo to cancel it.")]
    if arg:
        code, _, topic_name = arg.partition(" ")
        course = get_course(db, user, code, create=True)
        if topic_name.strip():
            return begin(db, user, get_topic(db, course, topic_name.strip()))
        return ask_topic(db, user, course)
    courses = db.scalars(select(Course).where(Course.user_id == user.id).order_by(Course.code)).all()
    if not courses:
        return [Reply("Which course? Send it like this: study CSC301 binary trees")]
    buttons = chunk([(c.code, f"cb:course:{c.id}") for c in courses], 2)
    return [Reply("Which course are you studying?", buttons=buttons)]


def ask_topic(db: Session, user: User, course: Course) -> list[Reply]:
    set_state(user, "awaiting_topic", course_id=course.id)
    recent = db.scalars(select(Topic).where(Topic.course_id == course.id).order_by(Topic.id.desc()).limit(5)).all()
    buttons = [[(t.name[:40], f"cb:topic:{t.id}")] for t in recent] or None
    hint = "Type the topic, or tap a recent one." if recent else "Type the topic (for example: binary trees)."
    return [Reply(f"{course.code}. What are you studying? {hint}", buttons=buttons)]


def on_topic_typed(db: Session, user: User, text: str) -> list[Reply]:
    course = db.get(Course, get_data(user).get("course_id", 0))
    if not course or course.user_id != user.id:
        set_state(user, None)
        return [Reply("Let's start again. Send: study CSC301 binary trees")]
    return begin(db, user, get_topic(db, course, text[:200]))


def begin(db: Session, user: User, topic: Topic) -> list[Reply]:
    db.add(StudySession(user_id=user.id, topic_id=topic.id, started_at=clock.now_utc()))
    set_state(user, None)
    return [Reply(f"Timer started: {label(topic)}. Send done when you're finished.")]


def cmd_done(db: Session, user: User, arg: str) -> list[Reply]:
    session = active_session(db, user)
    if not session:
        return [Reply("No timer running. Send study to start one.")]
    session.ended_at = clock.now_utc()
    mins = stats.minutes_between(session)
    set_state(user, "rating", session_id=session.id)
    if arg in ("1", "2", "3"):
        return rate(db, user, int(arg))
    buttons = [[("1 Shaky", "cb:rate:1"), ("2 Okay", "cb:rate:2"), ("3 Solid", "cb:rate:3")]]
    return [Reply(f"Logged {mins} min on {label(session.topic)}. How solid does it feel?", buttons=buttons)]


def rate(db: Session, user: User, rating: int) -> list[Reply]:
    session = db.get(StudySession, get_data(user).get("session_id", 0)) if user.state == "rating" else None
    if not session or session.user_id != user.id:
        return [Reply("That one's already saved.")]
    session.rating = rating
    step, due = srs.after_study(rating, clock.today_local())
    review = db.scalar(select(Review).where(Review.user_id == user.id, Review.topic_id == session.topic_id))
    if review:
        review.step, review.due_on, review.needs_revision = step, due, False
    else:
        db.add(Review(user_id=user.id, topic_id=session.topic_id, step=step, due_on=due))
    set_state(user, None)
    return [Reply(f"Saved. I'll bring {label(session.topic)} back {when(due)}.")]


# ---------- recall ----------

def cmd_due(db: Session, user: User) -> list[Reply]:
    reviews = due_reviews(db, user)
    if not reviews:
        nxt = db.scalar(
            select(func.min(Review.due_on)).where(Review.user_id == user.id, Review.due_on > clock.today_local())
        )
        tail = f" Next one comes up {when(nxt)}." if nxt else " Log a session and I'll start scheduling."
        return [Reply("Nothing due today." + tail)]
    lines = [f"- {label(r.topic)}" + (" (missed last time)" if r.needs_revision else "") for r in reviews]
    return [Reply(f"{len(reviews)} due today:\n" + "\n".join(lines), buttons=[[("Start quiz", "cb:quiz")]])]


def cmd_quiz(db: Session, user: User) -> list[Reply]:
    reviews = due_reviews(db, user)
    if not reviews:
        return cmd_due(db, user)
    review = reviews[0]
    questions = db.scalars(select(Question).where(Question.topic_id == review.topic_id)).all()
    head = f"{label(review.topic)} ({len(reviews)} due)"
    if questions:
        q = random.choice(questions)
        set_state(user, "quiz_answering", review_id=review.id, question_id=q.id)
        return [Reply(f"{head}\n\n{q.prompt}\n\nType your answer, or send skip.")]
    set_state(user, "quiz_grading", review_id=review.id)
    return [Reply(
        f"{head}\n\nRecall everything you can about this topic, out loud or on paper. Then be honest:",
        buttons=[[("Got it", "cb:recall:pass"), ("Missed", "cb:recall:fail")]],
    )]


def reveal_answer(db: Session, user: User) -> list[Reply]:
    data = get_data(user)
    q = db.get(Question, data.get("question_id", 0))
    set_state(user, "quiz_grading", review_id=data.get("review_id"))
    answer = q.answer if q else "(no answer saved)"
    return [Reply(f"Answer: {answer}\n\nHow did you do?", buttons=[[("Got it", "cb:recall:pass"), ("Missed", "cb:recall:fail")]])]


def grade(db: Session, user: User, passed: bool) -> list[Reply]:
    review = db.get(Review, get_data(user).get("review_id", 0)) if user.state == "quiz_grading" else None
    if not review or review.user_id != user.id:
        return [Reply("That one's already graded.")]
    review.step, review.due_on, review.needs_revision = srs.after_recall(review.step, passed, clock.today_local())
    db.add(RecallLog(user_id=user.id, topic_id=review.topic_id, passed=passed))
    set_state(user, None)
    db.flush()
    remaining = len(due_reviews(db, user))
    if passed:
        text = f"Nice. {label(review.topic)} comes back {when(review.due_on)}."
    else:
        text = f"No stress. {label(review.topic)} is back tomorrow and on your revise list. Study it again when you can."
    buttons = None
    if remaining:
        text += f" {remaining} more due."
        buttons = [[("Next one", "cb:quiz")]]
    return [Reply(text, buttons=buttons)]


def cmd_addq(db: Session, user: User, arg: str) -> list[Reply]:
    if "|" not in arg:
        return [Reply("Send it like this: addq What is a stack? | Last in, first out")]
    prompt, _, answer = arg.partition("|")
    prompt, answer = prompt.strip(), answer.strip()
    if not prompt or not answer:
        return [Reply("I need both a question and an answer, split with |")]
    last = db.scalar(select(StudySession).where(StudySession.user_id == user.id).order_by(StudySession.id.desc()))
    if not last:
        return [Reply("Study a topic first, then add questions to it.")]
    db.add(Question(topic_id=last.topic_id, prompt=prompt, answer=answer))
    db.flush()
    n = db.scalar(select(func.count(Question.id)).where(Question.topic_id == last.topic_id))
    return [Reply(f"Saved to {label(last.topic)}. That topic now has {n} question{'s' if n != 1 else ''}.")]


# ---------- housekeeping ----------

def cmd_stats(db: Session, user: User) -> list[Reply]:
    per_course, count = stats.week_by_course(db, user)
    if not count:
        return [Reply("No sessions in the last 7 days yet. Send study to log one.")]
    total = sum(per_course.values())
    lines = [f"Last 7 days: {stats.fmt_minutes(total)} across {count} session{'s' if count != 1 else ''}"]
    lines += [f"- {code}: {stats.fmt_minutes(m)}" for code, m in sorted(per_course.items(), key=lambda x: -x[1])]
    s = stats.streak(db, user)
    lines.append(f"Streak: {s} day{'s' if s != 1 else ''}")
    passed, attempts = stats.retention(db, user)
    if attempts:
        lines.append(f"Recall score: {round(100 * passed / attempts)}% ({passed} of {attempts})")
    return [Reply("\n".join(lines))]


def cmd_undo(db: Session, user: User) -> list[Reply]:
    session = active_session(db, user)
    if session:
        db.delete(session)
        set_state(user, None)
        return [Reply("Timer cancelled. Nothing was logged.")]
    last = db.scalar(select(StudySession).where(StudySession.user_id == user.id).order_by(StudySession.id.desc()))
    if not last:
        return [Reply("Nothing to undo.")]
    name = label(last.topic)
    db.delete(last)
    set_state(user, None)
    return [Reply(f"Deleted your last session ({name}). Its review schedule is unchanged.")]


def cmd_courses(db: Session, user: User) -> list[Reply]:
    courses = db.scalars(select(Course).where(Course.user_id == user.id).order_by(Course.code)).all()
    if not courses:
        return [Reply("No courses yet. They're added automatically the first time you send: study CSC301 binary trees")]
    lines = []
    for c in courses:
        n = db.scalar(select(func.count(Topic.id)).where(Topic.course_id == c.id))
        lines.append(f"- {c.code}" + (f" {c.title}" if c.title else "") + f" ({n} topic{'s' if n != 1 else ''})")
    return [Reply("Your courses:\n" + "\n".join(lines))]


def cmd_addcourse(db: Session, user: User, arg: str) -> list[Reply]:
    code, _, title = arg.partition(" ")
    if not code:
        return [Reply("Send it like this: addcourse CSC301 Data Structures")]
    course = get_course(db, user, code, create=True)
    course.title = title.strip()[:120]
    return [Reply(f"Saved {course.code}" + (f": {course.title}" if course.title else "") + ".")]


def cmd_export(db: Session, user: User) -> list[Reply]:
    return [Reply("Here's your study log. Open it in Excel any time.", document=("stick_study_log.xlsx", build_workbook(db, user)))]
