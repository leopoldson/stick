"""Guards for mistakes that already happened once."""
import importlib
from datetime import datetime

from app import clock, stats


def test_now_utc_is_naive():
    """The database stores naive UTC. A timezone-aware value here breaks done, due and export."""
    assert clock.now_utc().tzinfo is None
    assert clock.now_utc() - datetime(2026, 1, 1) is not None  # subtracting a stored time must not raise


def test_durations_are_written_out_never_abbreviated():
    assert stats.fmt_minutes(60) == "1 hour"
    assert stats.fmt_minutes(90) == "1 hour 30 minutes"
    assert stats.fmt_minutes(1) == "1 minute"
    assert stats.fmt_minutes(135) == "2 hours 15 minutes"  # words, not "2h 15m"


def test_every_module_the_app_needs_is_present():
    for name in ("main", "clock", "stats", "handlers", "export", "bot", "telegram", "whatsapp", "timeparse", "textfmt", "srs", "db", "models", "config"):
        importlib.import_module(f"app.{name}")
