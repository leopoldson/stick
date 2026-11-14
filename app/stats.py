from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import clock
from .models import Course, RecallLog, StudySession, Topic, User


def minutes_between(s: StudySession) -> int:
    if not s.ended_at:
        return 0
    return max(1, round((s.ended_at - s.started_at).total_seconds() / 60))


def fmt_minutes(m: int) -> str:
    """Spoken style, like '1 minute' or '1 hour 20 minutes'."""
    h, mm = divmod(m, 60)
    parts = []
    if h:
        parts.append(f"{h} hour{'s' if h != 1 else ''}")
    if mm or not h:
        parts.append(f"{mm} minute{'s' if mm != 1 else ''}")
    return " ".join(parts)


def finished_sessions(db: Session, user: User, since=None):
    q = select(StudySession).where(StudySession.user_id == user.id, StudySession.ended_at.is_not(None))
    if since is not None:
        q = q.where(StudySession.started_at >= since)
    return db.scalars(q.order_by(StudySession.started_at)).all()


def week_by_course(db: Session, user: User) -> tuple[dict[str, int], int]:
    since = clock.now_utc() - timedelta(days=7)
    per_course: dict[str, int] = {}
    sessions = finished_sessions(db, user, since)
    for s in sessions:
        code = s.topic.course.code
        per_course[code] = per_course.get(code, 0) + minutes_between(s)
    return per_course, len(sessions)


def streak(db: Session, user: User) -> int:
    days = {clock.local_date(s.started_at) for s in finished_sessions(db, user)}
    day = clock.today_local()
    if day not in days:
        day -= timedelta(days=1)  # today isn't over yet: a streak survives until midnight
    count = 0
    while day in days:
        count += 1
        day -= timedelta(days=1)
    return count


def retention(db: Session, user: User) -> tuple[int, int]:
    """(passed, attempts) across all recall checks."""
    total = db.scalar(select(func.count(RecallLog.id)).where(RecallLog.user_id == user.id)) or 0
    passed = db.scalar(
        select(func.count(RecallLog.id)).where(RecallLog.user_id == user.id, RecallLog.passed.is_(True))
    ) or 0
    return passed, total


def session_count(db: Session, user: User) -> int:
    return db.scalar(
        select(func.count(StudySession.id)).where(StudySession.user_id == user.id, StudySession.ended_at.is_not(None))
    ) or 0


def best_streak(db: Session, user: User) -> int:
    days = sorted({clock.local_date(s.started_at) for s in finished_sessions(db, user)})
    best = run = 0
    prev = None
    for d in days:
        run = run + 1 if prev is not None and d - prev == timedelta(days=1) else 1
        best, prev = max(best, run), d
    return best


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"
