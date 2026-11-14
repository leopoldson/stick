"""All time handling lives here so tests can move the clock.

Timestamps are stored as naive UTC. "Days" (due dates, streaks) use Lagos time.
"""
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

LAGOS = ZoneInfo("Africa/Lagos")


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def now_local() -> datetime:
    return datetime.now(LAGOS)


def today_local() -> date:
    return now_local().date()


def local_date(utc_naive: datetime) -> date:
    return utc_naive.replace(tzinfo=timezone.utc).astimezone(LAGOS).date()


def to_local_naive(utc_naive: datetime) -> datetime:
    return utc_naive.replace(tzinfo=timezone.utc).astimezone(LAGOS).replace(tzinfo=None)


def local_to_utc(naive_local: datetime) -> datetime:
    """A Lagos wall-clock time -> naive UTC, the way times are stored."""
    return naive_local.replace(tzinfo=LAGOS).astimezone(timezone.utc).replace(tzinfo=None)
