"""Reminders (#4): set by voice, kept in the registry, delivered by voice when the owner is at the laptop
and by Telegram otherwise. See docs/superpowers/specs/2026-10-16-reminders-design.md."""

import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from clock import _zone

REPEATS = ("none", "daily", "weekly")
_STEP = {"daily": timedelta(days=1), "weekly": timedelta(weeks=1)}


def local_zone() -> ZoneInfo:
    try:
        return ZoneInfo(_zone() or "UTC")
    except Exception:  # an unknown TZ name: still a working clock, in UTC
        return ZoneInfo("UTC")


def next_due(due, repeat, now, tz):
    """The same wall-clock time a day or week on (aware arithmetic keeps 9:00 across DST), never in the past."""
    t = datetime.fromtimestamp(due, tz)
    while True:
        t = t + _STEP[repeat]
        if t.timestamp() > now:
            return t.timestamp()


def spoken_time(ts, tz, now=None):
    t, today = datetime.fromtimestamp(ts, tz), datetime.fromtimestamp(now or time.time(), tz).date()
    clock = t.strftime("%-I:%M %p").lower()
    days = (t.date() - today).days
    if days == 0:
        return clock
    if days == 1:
        return f"tomorrow at {clock}"
    if 1 < days < 7:
        return f"{t.strftime('%A')} at {clock}"
    return f"{t.strftime('%-d %b')} at {clock}"
