"""Channel-independent bot logic: text in, replies out.

Nothing here knows about Telegram. Button taps arrive as text starting with "cb:",
so a WhatsApp adapter can reuse all of this later.
"""
import difflib
import json
import random
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import clock, srs, stats
from .export import build_workbook
from .models import Course, Question, RecallLog, Review, StudySession, Topic, User
from .textfmt import tidy_name

Buttons = list[list[tuple[str, str]]]


@dataclass
class Reply:
    text: str = ""
    buttons: Optional[Buttons] = None
    document: Optional[tuple[str, bytes]] = None


WELCOME = (
    "Hi {name}, I'm Stick. I log your study sessions, then bring each topic back right before you'd forget it.\n\n"
    "study : start a session now, or log one you already finished\n"
    "done : stop the timer and log it\n"
    "due : what to review today\n"
    "quiz : test yourself\n"
    "stats : your week and streak\n"
    "export : download your Excel log\n"
    "undo : cancel or delete your last session\n\n"
    "Start with: study"
)
COMMAND_NAMES = {
    "start", "help", "study", "done", "due", "quiz", "addq", "stats",
    "export", "undo", "courses", "addcourse",
}
MODE_BUTTONS = [[("Start a session now", "cb:begin:now"), ("Log a past session", "cb:begin:past")]]
DURATION_PROMPT = "How long did you study? For example: 45 minutes or 1h 30m. Send cancel to skip."
SAFE_RUN = {"study", "due", "quiz", "stats", "export", "courses", "help"}  # buttons may run these
SUGGESTABLE = ["study", "done", "due", "quiz", "stats", "export", "undo", "courses", "help"]
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


_DURATION = re.compile(
    r"^\s*(?:(\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hour|hours))?\s*(?:(\d+)\s*(?:m|min|mins|minute|minutes)?)?\s*$", re.I
)


def parse_duration(text: str) -> Optional[int]:
    """'45', '45 min', '1h30', '1.5h', '1 hour 30 minutes' -> minutes (1 to 720), else None."""
    m = _DURATION.match(text or "")
    if not m or not (m.group(1) or m.group(2)):
        return None
    total = round(float(m.group(1) or 0) * 60) + int(m.group(2) or 0)
    return total if 1 <= total <= 720 else None


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


def get_user(db: Session, identity, name: str) -> tuple[User, bool, str]:
    """identity is a Telegram id (int) or ("wa", "<phone number>"). Returns (user, was_just_created, channel)."""
    channel, ext = ("tg", identity) if isinstance(identity, int) else identity
    column = User.tg_id if channel == "tg" else User.wa_id
    user = db.scalar(select(User).where(column == ext))
    created = False
    if not user:
        user = User(name=name or "", **({"tg_id": ext} if channel == "tg" else {"wa_id": ext}))
        db.add(user)
        db.flush()
        created = True
    return user, created, channel


def split_course(text: str) -> tuple[str, str]:
    """'csc301 data structures' -> ('CSC301', 'Data Structures'); 'discrete structures' -> ('Discrete Structures', '')."""
    name = tidy_name(text)
    first, _, rest = name.partition(" ")
    if rest and any(ch.isdigit() for ch in first) and len(first) <= 12:
        return first[:30], rest[:120]
    return name[:30], ""


def get_course(db: Session, user: User, name: str, create: bool = False) -> Optional[Course]:
    code, title = split_course(name)
    if not code:
        return None
    course = db.scalar(select(Course).where(Course.user_id == user.id, func.upper(Course.code) == code.upper()))
    if course:
        if title and not course.title:
            course.title = title
    elif create:
        course = Course(user_id=user.id, code=code, title=title)
        db.add(course)
        db.flush()
    return course


def get_topic(db: Session, course: Course, name: str) -> Topic:
    topic = db.scalar(select(Topic).where(Topic.course_id == course.id, func.lower(Topic.name) == name.lower()))
    if not topic:
        topic = Topic(course_id=course.id, name=tidy_name(name)[:200])
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

def handle(db: Session, identity, name: str, text: str) -> list[Reply]:
    user, created, channel = get_user(db, identity, name)
    if channel == "wa":
        user.last_inbound_at = clock.now_utc()  # opens WhatsApp's 24-hour reply window
    cmd, arg, slash = parse(text)
    lowered = text.strip().lower()
    # While we're asking for a course or topic, a lone word like "due" (or any "done ...") is a command, not a name.
    typed = not slash and not ((cmd in COMMAND_NAMES and not arg) or cmd == "done")

    if created and channel == "wa" and cmd != "cb" and cmd not in COMMAND_NAMES:
        # WhatsApp has no /start, so greet a brand-new person whatever their first message was
        replies = [Reply(WELCOME.format(name=user.name or "there"), buttons=[[("Start studying", "cb:run:study")]])]
    elif cmd == "cb":
        replies = on_callback(db, user, arg)
    elif cmd == "cancel":
        set_state(user, None)
        replies = [Reply("Okay, cancelled.")]
    elif user.state == "awaiting_mode" and typed:
        replies = on_mode_typed(db, user, text.strip())
    elif user.state == "awaiting_duration" and typed:
        replies = on_duration_typed(db, user, text.strip())
    elif user.state == "awaiting_weak" and typed:
        replies = on_weak_typed(db, user, text.strip())
    elif user.state == "awaiting_course" and typed:
        replies = on_course_typed(db, user, text.strip())
    elif user.state == "awaiting_topic" and typed:
        replies = on_topic_typed(db, user, text.strip())
    elif user.state == "rating" and cmd in ("1", "2", "3"):
        replies = rate(db, user, int(cmd))
    elif user.state == "quiz_grading" and lowered in PASS_WORDS | FAIL_WORDS:
        replies = grade(db, user, lowered in PASS_WORDS)
    elif user.state == "quiz_answering" and not slash:
        replies = reveal_answer(db, user)
    else:
        if user.state in ("awaiting_mode", "awaiting_duration", "awaiting_weak", "awaiting_course", "awaiting_topic", "quiz_answering"):
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
    return unknown_command(cmd)


def unknown_command(cmd: str) -> list[Reply]:
    guess = difflib.get_close_matches(cmd, SUGGESTABLE, n=1, cutoff=0.6) if cmd else []
    if guess:
        return [Reply(f"Did you mean {guess[0]}?", buttons=[[(f"Run {guess[0]}", f"cb:run:{guess[0]}")]])]
    return [Reply("I didn't catch that.", buttons=[[("Show commands", "cb:run:help")]])]


def on_callback(db: Session, user: User, arg: str) -> list[Reply]:
    kind, _, value = arg.partition(":")
    pending = get_data(user)
    if kind == "quiz":
        return cmd_quiz(db, user)
    if kind == "run" and value in SAFE_RUN:
        if user.state in TRANSIENT:
            set_state(user, None)  # tapping a command button leaves any half-finished question
        return on_command(db, user, value, "")
    if kind == "begin" and value in ("now", "past"):
        if active_session(db, user):
            return cmd_study(db, user, "")
        if value == "now":
            return course_prompt(db, user, "Which course are you studying?")
        set_state(user, "awaiting_duration")
        return [Reply(DURATION_PROMPT)]
    if kind == "course" and value.isdigit():
        course = db.get(Course, int(value))
        if course and course.user_id == user.id:
            minutes = pending.get("minutes") if user.state == "awaiting_course" else None
            return ask_topic(db, user, course, minutes)
    if kind == "topic" and value.isdigit():
        topic = owned_topic(db, user, int(value))
        minutes = pending.get("minutes") if user.state == "awaiting_topic" else None
        if topic and (minutes or not active_session(db, user)):
            return begin(db, user, topic, minutes)
    if kind == "weak" and value.isdigit():
        topic = owned_topic(db, user, int(value))
        if topic:
            set_state(user, "awaiting_weak", topic_id=topic.id)
            return [Reply(
                f"What tripped you up in {label(topic)}? Send it as a question. If you know the answer, add it after a |. "
                "I'll mix it into your next quiz. Send cancel when you're done."
            )]
    if kind == "rate" and value in ("1", "2", "3"):
        return rate(db, user, int(value))
    if kind == "recall" and value in ("pass", "fail"):
        return grade(db, user, value == "pass")
    return [Reply("That button has expired. Send help to see your options.")]


# ---------- studying ----------

def course_prompt(db: Session, user: User, intro: str, minutes: Optional[int] = None) -> list[Reply]:
    courses = db.scalars(select(Course).where(Course.user_id == user.id).order_by(Course.code)).all()
    set_state(user, "awaiting_course", minutes=minutes)
    if not courses:
        return [Reply(f"{intro} Type its name or code. I'll remember it for next time.")]
    buttons = chunk([(c.code, f"cb:course:{c.id}") for c in courses], 2)
    return [Reply(f"{intro} Tap one, or type a new course.", buttons=buttons)]


def cmd_study(db: Session, user: User, arg: str) -> list[Reply]:
    current = active_session(db, user)
    if current:
        mins = round((clock.now_utc() - current.started_at).total_seconds() / 60)
        since = f"{stats.fmt_minutes(mins)} in" if mins else "just started"
        return [Reply(f"You're already studying {label(current.topic)} ({since}). Send done when you finish, or undo to cancel it.")]
    if arg:  # shortcut: "study Physics" skips the course question
        return on_course_typed(db, user, arg)
    set_state(user, "awaiting_mode")
    return [Reply("Do you want to start a session now, or log one you already finished?", buttons=MODE_BUTTONS)]


def on_mode_typed(db: Session, user: User, text: str) -> list[Reply]:
    lowered = text.lower()
    if lowered in {"now", "start", "start now", "timer"}:
        return course_prompt(db, user, "Which course are you studying?")
    if lowered in {"past", "log", "log past", "earlier", "before", "finished", "forgot"}:
        set_state(user, "awaiting_duration")
        return [Reply(DURATION_PROMPT)]
    return [Reply("Tap one: start now, or log a session you already finished.", buttons=MODE_BUTTONS)]


def on_course_typed(db: Session, user: User, text: str) -> list[Reply]:
    minutes = get_data(user).get("minutes") if user.state == "awaiting_course" else None
    if not split_course(text)[0]:
        return [Reply("Type the course name or code.")]
    existed = get_course(db, user, text) is not None
    course = get_course(db, user, text, create=True)
    replies = ask_topic(db, user, course, minutes)
    if not existed:
        replies[0].text = f"Added {course.code} to your courses.\n\n" + replies[0].text
    return replies


def ask_topic(db: Session, user: User, course: Course, minutes: Optional[int] = None) -> list[Reply]:
    set_state(user, "awaiting_topic", course_id=course.id, minutes=minutes)
    recent = db.scalars(select(Topic).where(Topic.course_id == course.id).order_by(Topic.id.desc()).limit(5)).all()
    buttons = [[(t.name[:40], f"cb:topic:{t.id}")] for t in recent] or None
    hint = "Type the topic, or tap a recent one." if recent else "Type the topic."
    question = "What did you study?" if minutes else "What are you studying?"
    return [Reply(f"{course.code}. {question} {hint}", buttons=buttons)]


def on_topic_typed(db: Session, user: User, text: str) -> list[Reply]:
    data = get_data(user)
    course = db.get(Course, data.get("course_id", 0))
    if not course or course.user_id != user.id:
        set_state(user, None)
        return [Reply("Let's start again. Send study.")]
    return begin(db, user, get_topic(db, course, text[:200]), data.get("minutes"))


AFTER_BUTTONS = [
    [("Quiz me", "cb:run:quiz"), ("Study again", "cb:run:study")],
    [("My stats", "cb:run:stats"), ("All commands", "cb:run:help")],
]
TRANSIENT = {"awaiting_mode", "awaiting_duration", "awaiting_weak", "awaiting_course", "awaiting_topic", "quiz_answering"}
MILESTONES = {
    1: "That's your first session logged. Every streak starts here.",
    5: "That's your fifth session. This is turning into a habit.",
    10: "Tenth session logged. Double digits.",
    25: "Twenty-fifth session. A quarter of the way to a hundred.",
    50: "Your fiftieth session. That's real consistency.",
    100: "Your hundredth session logged. Serious dedication.",
}


def ordinal(n: int) -> str:
    if 10 <= n % 100 <= 13:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


def milestone_message(db: Session, user: User) -> str:
    n = stats.session_count(db, user)
    if n in MILESTONES:
        return MILESTONES[n]
    if n > 100 and n % 100 == 0:
        return f"Your {ordinal(n)} session logged. Keep going."
    return ""


RATING_BUTTONS = [[("1 Shaky", "cb:rate:1"), ("2 Okay", "cb:rate:2"), ("3 Solid", "cb:rate:3")]]


def begin(db: Session, user: User, topic: Topic, minutes: Optional[int] = None) -> list[Reply]:
    if minutes:  # logging a session after the fact: no timer, straight to the rating
        now = clock.now_utc()
        session = StudySession(user_id=user.id, topic_id=topic.id, started_at=now - timedelta(minutes=minutes), ended_at=now)
        db.add(session)
        db.flush()
        set_state(user, "rating", session_id=session.id)
        return [Reply(f"Logged {stats.fmt_minutes(minutes)} on {label(topic)}. How solid does it feel?", buttons=RATING_BUTTONS)]
    db.add(StudySession(user_id=user.id, topic_id=topic.id, started_at=clock.now_utc()))
    set_state(user, None)
    return [Reply(f"Timer started: {label(topic)}. Send done when you're finished.")]


def cmd_done(db: Session, user: User, arg: str) -> list[Reply]:
    session = active_session(db, user)
    if not session:
        minutes = parse_duration(arg) if arg and arg not in ("1", "2", "3") else None
        if minutes:
            return course_prompt(db, user, f"Got it, {stats.fmt_minutes(minutes)}. Which course was it?", minutes)
        set_state(user, "awaiting_duration")
        return [Reply("No timer was running. " + DURATION_PROMPT)]
    session.ended_at = clock.now_utc()
    mins = stats.minutes_between(session)
    set_state(user, "rating", session_id=session.id)
    if arg in ("1", "2", "3"):
        return rate(db, user, int(arg))
    return [Reply(f"Logged {stats.fmt_minutes(mins)} on {label(session.topic)}. How solid does it feel?", buttons=RATING_BUTTONS)]


def on_duration_typed(db: Session, user: User, text: str) -> list[Reply]:
    minutes = parse_duration(text)
    if not minutes:
        return [Reply("I couldn't read that. Try 45 minutes or 1h 30m, or send cancel.")]
    return course_prompt(db, user, f"Got it, {stats.fmt_minutes(minutes)}. Which course was it?", minutes)


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
    db.flush()
    text = f"Saved. I'll bring {label(session.topic)} back {when(due)}."
    milestone = milestone_message(db, user)
    if milestone:
        text += "\n\n" + milestone
    if rating == 1:  # shaky: find out what to target next time
        set_state(user, "awaiting_weak", topic_id=session.topic_id)
        text += "\n\nThat one felt shaky. What exactly was shaky? Send a topic or a question and I'll test you on it. Send as many as you like."
        return [Reply(text, buttons=[[("Skip", "cb:run:help")]])]
    set_state(user, None)
    return [Reply(text, buttons=AFTER_BUTTONS)]


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
    shown = f"Answer: {q.answer}" if q and q.answer else "That one's from your own tricky list, so check your notes."
    return [Reply(f"{shown}\n\nHow did you do?", buttons=[[("Got it", "cb:recall:pass"), ("Missed", "cb:recall:fail")]])]


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
        weak_label = "Add a tricky one"
    else:
        text = f"No stress. {label(review.topic)} is back tomorrow and on your revise list. Study it again when you can."
        weak_label = "Add what tripped me"
    buttons = [[(weak_label, f"cb:weak:{review.topic_id}")]]
    if remaining:
        text += f" {remaining} more due."
        buttons.append([("Next one", "cb:quiz")])
    return [Reply(text, buttons=buttons)]


def on_weak_typed(db: Session, user: User, text: str) -> list[Reply]:
    topic = owned_topic(db, user, get_data(user).get("topic_id", 0))
    if not topic:
        set_state(user, None)
        return [Reply("Let's start again. Send quiz.")]
    prompt, _, answer = text.partition("|")
    prompt = prompt.strip()[:500]
    if not prompt:
        return [Reply("Send it as a question.")]
    db.add(Question(topic_id=topic.id, prompt=prompt, answer=answer.strip()))
    db.flush()
    return [Reply(f"Saved to {label(topic)}. Send another, or tap a command.", buttons=AFTER_BUTTONS)]


def cmd_addq(db: Session, user: User, arg: str) -> list[Reply]:
    """Power-user shortcut (not in the help list): add a question to the last studied topic."""
    if "|" not in arg:
        return [Reply("Send it as: addq <question> | <answer>")]
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
    due_now = len(due_reviews(db, user))
    if not count:
        lines = ["No sessions in the last 7 days yet. Send study to log one."]
    else:
        total = sum(per_course.values())
        lines = [f"This week: {stats.fmt_minutes(total)} in {stats.plural(count, 'session')}"]
        top = max(per_course.values())
        for code, mins in sorted(per_course.items(), key=lambda x: -x[1]):
            bar = "█" * max(1, round(8 * mins / top))
            lines.append(f"{code}  {bar}  {stats.fmt_minutes(mins)}")
    streak, best = stats.streak(db, user), stats.best_streak(db, user)
    lines.append(f"Streak: {stats.plural(streak, 'day')} (best: {best})" if best > streak else f"Streak: {stats.plural(streak, 'day')}")
    passed, attempts = stats.retention(db, user)
    if attempts:
        lines.append(f"Recall: {round(100 * passed / attempts)}% ({passed} of {attempts})")
    lines.append(f"Due now: {due_now}")
    return [Reply("\n".join(lines), buttons=[[("Quiz me", "cb:run:quiz")]] if due_now else None)]


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
        return [Reply("No courses yet. Send study and type your first course. I'll remember it.")]
    lines = []
    for c in courses:
        n = db.scalar(select(func.count(Topic.id)).where(Topic.course_id == c.id))
        lines.append(f"- {c.code}" + (f" {c.title}" if c.title else "") + f" ({n} topic{'s' if n != 1 else ''})")
    return [Reply("Your courses:\n" + "\n".join(lines))]


def cmd_addcourse(db: Session, user: User, arg: str) -> list[Reply]:
    code, _, title = arg.partition(" ")
    if not code:
        return [Reply("Send it as: addcourse <code> <title>")]
    course = get_course(db, user, code, create=True)
    if title.strip():
        course.title = tidy_name(title)[:120]
    return [Reply(f"Saved {course.code}" + (f": {course.title}" if course.title else "") + ".")]


def cmd_export(db: Session, user: User) -> list[Reply]:
    return [Reply("Here's your study log. Open it in Excel any time.", document=("stick_study_log.xlsx", build_workbook(db, user)))]
