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
    h, mm = divmod(m, 60)
    if h and mm:
        return f"{h}h {mm}m"
    return f"{h}h" if h else f"{mm}m"


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
