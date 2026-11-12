"""Spaced repetition: a fixed ladder of review gaps, in days."""
from datetime import date, timedelta

INTERVALS = [1, 3, 7, 14, 30]
# How solid it felt after studying decides where on the ladder the first review lands.
START_STEP = {1: 0, 2: 1, 3: 2}  # shaky, okay, solid


def after_study(rating: int, today: date) -> tuple[int, date]:
    step = START_STEP[rating]
    return step, today + timedelta(days=INTERVALS[step])


def after_recall(step: int, passed: bool, today: date) -> tuple[int, date, bool]:
    """Returns (new_step, next_due, needs_revision)."""
    if passed:
        step = min(step + 1, len(INTERVALS) - 1)
        return step, today + timedelta(days=INTERVALS[step]), False
    return 0, today + timedelta(days=1), True
