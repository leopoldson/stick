"""Reading dates and times the way people type them: '9pm', '7:30 pm', '2130', 'yesterday', '12 nov', 'monday'."""
import re
from datetime import date, time, timedelta
from typing import Optional

MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
WEEKDAY_WORDS = {"monday", "mon", "tuesday", "tue", "tues", "wednesday", "wed", "thursday", "thu", "thur", "thurs",
                 "friday", "fri", "saturday", "sat", "sunday", "sun"}

_CLOCK = re.compile(r"(\d{1,2})(?:[:.h](\d{2}))?(am|pm|a\.m\.|p\.m\.)?")  # 7, 7:30, 7.30pm, 7h30, 7pm
_COMPACT = re.compile(r"\d{3,4}")                                        # 930, 2130


def parse_time(text: str, bare_ok: bool = True) -> Optional[time]:
    """Local clock time. 24-hour unless am/pm is given. With bare_ok=False a lone '9' is refused (it could be minutes)."""
    t = re.sub(r"\s+", "", (text or "").lower())
    m = _CLOCK.fullmatch(t)
    if m:
        hour, minute, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3)
        if not bare_ok and not (m.group(2) or ap):
            return None
    elif _COMPACT.fullmatch(t):
        digits = t.zfill(4)
        hour, minute, ap = int(digits[:2]), int(digits[2:]), None
    else:
        return None
    if minute > 59:
        return None
    if ap:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if ap[0] == "p" else 0)
    elif hour > 23:
        return None
    return time(hour, minute)


def _build(year: int, month: int, day: int, today: date, year_given: bool) -> Optional[date]:
    try:
        d = date(year, month, day)
    except ValueError:
        return None
    if d > today and not year_given and (d - today).days > 60:
        try:
            d = date(year - 1, month, day)  # "28 dec" typed in January means last December...
        except ValueError:
            return None
    return d if d <= today else None  # ...but "15 nov" typed on 14 Nov is a slip, not last year


def parse_date(text: str, today: date) -> Optional[date]:
    """A day that has already happened (today or earlier), or None."""
    t = re.sub(r"\s+", " ", (text or "").strip().lower().rstrip("."))
    if not t:
        return None
    if t == "today":
        return today
    if t in ("yesterday", "yday", "yest"):
        return today - timedelta(days=1)
    if m := re.fullmatch(r"(\d{1,3}) days? ago", t):
        return today - timedelta(days=int(m.group(1)))
    if t in WEEKDAY_WORDS:  # the most recent one before today
        back = (today.weekday() - WEEKDAYS[t[:3]]) % 7 or 7
        return today - timedelta(days=back)
    if m := re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", t):
        return _build(int(m.group(1)), int(m.group(2)), int(m.group(3)), today, True)
    if m := re.fullmatch(r"(\d{1,2})[/.\-](\d{1,2})(?:[/.\-](\d{2}|\d{4}))?", t):  # day first, as in Nigeria: 12/11
        year = int(m.group(3)) if m.group(3) else today.year
        year += 2000 if year < 100 else 0
        return _build(year, int(m.group(2)), int(m.group(1)), today, bool(m.group(3)))
    if m := re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)?[ \-]([a-z]{3,9})(?:,? (\d{4}))?", t):  # 12 nov, 12th November 2026
        month = MONTHS.get(m.group(2)[:3])
        return _build(int(m.group(3) or today.year), month, int(m.group(1)), today, bool(m.group(3))) if month else None
    if m := re.fullmatch(r"([a-z]{3,9})[ \-](\d{1,2})(?:st|nd|rd|th)?(?:,? (\d{4}))?", t):  # nov 12
        month = MONTHS.get(m.group(1)[:3])
        return _build(int(m.group(3) or today.year), month, int(m.group(2)), today, bool(m.group(3))) if month else None
    return None


def parse_when(text: str, today: date) -> Optional[tuple[Optional[date], time]]:
    """'9pm' -> (None, 21:00); 'yesterday 11:30pm' -> (yesterday, 23:30). None if it can't be read."""
    tokens = (text or "").split()
    for i in range(len(tokens)):
        clock_time = parse_time(" ".join(tokens[i:]))
        if clock_time is None:
            continue
        if i == 0:
            return None, clock_time
        day = parse_date(" ".join(tokens[:i]), today)
        if day:
            return day, clock_time
    return None
