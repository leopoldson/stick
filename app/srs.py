"""Spaced repetition: a fixed ladder of review gaps, in days."""
from datetime import date, timedelta

INTERVALS = [1, 3, 7, 14, 30]
# First review after studying. How solid it *felt* right afterwards is a weak signal (new material always feels
# familiar for a few hours), so even "solid" comes back within days: (ladder step, gap in days).
FIRST_REVIEW = {1: (0, 1), 2: (0, 2), 3: (1, 3)}  # shaky, okay, solid


def after_study(rating: int, today: date) -> tuple[int, date]:
    step, gap = FIRST_REVIEW[rating]
    return step, today + timedelta(days=gap)


def after_recall(step: int, passed: bool, today: date) -> tuple[int, date, bool]:
    """Returns (new_step, next_due, needs_revision)."""
    if passed:
        step = min(step + 1, len(INTERVALS) - 1)
        return step, today + timedelta(days=INTERVALS[step]), False
    return 0, today + timedelta(days=1), True
