"""One-off clean-ups that run safely on every start-up."""
import logging

from sqlalchemy import func, select

from .db import SessionLocal
from .models import Course, Topic
from .textfmt import tidy_name

log = logging.getLogger("stick.maintenance")


def tidy_existing_names() -> int:
    """Title-case course and topic names saved before tidying existed. Returns how many rows changed."""
    changed = 0
    with SessionLocal() as db:
        for course in db.scalars(select(Course)).all():
            code = tidy_name(course.code)[:30]
            clash = db.scalar(
                select(func.count(Course.id)).where(
                    Course.user_id == course.user_id, Course.code == code, Course.id != course.id
                )
            )
            if code and code != course.code and not clash:
                course.code, changed = code, changed + 1
            title = tidy_name(course.title)[:120]
            if title != course.title:
                course.title, changed = title, changed + 1
        for topic in db.scalars(select(Topic)).all():
            name = tidy_name(topic.name)[:200]
            if name and name != topic.name:
                topic.name, changed = name, changed + 1
        db.commit()
    return changed
