"""Builds the downloadable Excel log (mirrors the original Study & Active Recall workbook)."""
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import clock, stats
from .models import Course, Question, RecallLog, Review, Topic, User

NAVY = "1F2A44"
RATING = {1: "Shaky", 2: "Okay", 3: "Solid"}


def _sheet(wb: Workbook, title: str, headers: list[str], rows: list[list], widths: list[int]):
    ws = wb.create_sheet(title)
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor=NAVY)
        cell.alignment = Alignment(vertical="center")
    for row in rows:
        ws.append(row)
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"
    return ws


def build_workbook(db: Session, user: User) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)

    sessions = stats.finished_sessions(db, user)
    total_min = sum(stats.minutes_between(s) for s in sessions)
    passed, attempts = stats.retention(db, user)

    _sheet(
        wb, "Summary", ["Measure", "Value"],
        [
            ["Total study time", stats.fmt_minutes(total_min)],
            ["Sessions logged", len(sessions)],
            ["Current streak (days)", stats.streak(db, user)],
            ["Recall checks passed", f"{passed} of {attempts}" if attempts else "none yet"],
            ["Retention score", f"{round(100 * passed / attempts)}%" if attempts else "n/a"],
        ],
        [28, 22],
    )

    _sheet(
        wb, "Daily Study Log",
        ["Date", "Course", "Topic", "Started", "Ended", "Minutes", "How solid"],
        [
            [
                clock.local_date(s.started_at), s.topic.course.code, s.topic.name,
                clock.to_local_naive(s.started_at).strftime("%H:%M"),
                clock.to_local_naive(s.ended_at).strftime("%H:%M"),
                stats.minutes_between(s), RATING.get(s.rating, ""),
            ]
            for s in sessions
        ],
        [12, 12, 36, 10, 10, 10, 12],
    )

    reviews = db.scalars(select(Review).where(Review.user_id == user.id).order_by(Review.due_on)).all()
    _sheet(
        wb, "Revision Tracker",
        ["Course", "Topic", "Step", "Next review", "Needs revision"],
        [[r.topic.course.code, r.topic.name, f"{r.step + 1} of 5", r.due_on, "Yes" if r.needs_revision else ""] for r in reviews],
        [12, 36, 10, 14, 16],
    )

    qs = db.scalars(
        select(Question).join(Topic).join(Course).where(Course.user_id == user.id).order_by(Course.code, Topic.name)
    ).all()
    db_topics = {t.id: t for t in db.scalars(select(Topic).join(Course).where(Course.user_id == user.id)).all()}
    _sheet(
        wb, "Active Recall", ["Course", "Topic", "Question", "Answer"],
        [[db_topics[q.topic_id].course.code, db_topics[q.topic_id].name, q.prompt, q.answer] for q in qs],
        [12, 30, 50, 50],
    )

    logs = db.scalars(select(RecallLog).where(RecallLog.user_id == user.id).order_by(RecallLog.at)).all()
    _sheet(
        wb, "Recall History", ["Date", "Course", "Topic", "Result"],
        [[clock.local_date(l.at), l.topic.course.code, l.topic.name, "Got it" if l.passed else "Missed"] for l in logs],
        [12, 12, 36, 12],
    )

    courses = db.scalars(select(Course).where(Course.user_id == user.id).order_by(Course.code)).all()
    rows = []
    for c in courses:
        topics = db.scalars(select(Topic).where(Topic.course_id == c.id).order_by(Topic.name)).all()
        rows.append([c.code, c.title, ", ".join(t.name for t in topics)])
    _sheet(wb, "Courses & Topics", ["Course", "Title", "Topics"], rows, [12, 36, 70])

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
