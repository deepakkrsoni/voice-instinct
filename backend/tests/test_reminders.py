from datetime import datetime
from zoneinfo import ZoneInfo

from reminders import next_due, spoken_time

NY, IN = ZoneInfo("America/New_York"), ZoneInfo("Asia/Kolkata")


def ts(y, mo, d, h, mi, tz):
    return datetime(y, mo, d, h, mi, tzinfo=tz).timestamp()


def test_daily_keeps_the_wall_clock_time_across_dst():
    due = ts(2026, 10, 31, 9, 0, NY)  # DST ends at 2am on 1 Nov in New York: a 25-hour day
    nxt = next_due(due, "daily", due, NY)
    assert datetime.fromtimestamp(nxt, NY).hour == 9 and nxt - due == 25 * 3600


def test_weekly_is_seven_days_later():
    due = ts(2026, 10, 16, 15, 0, IN)
    assert next_due(due, "weekly", due, IN) == ts(2026, 10, 23, 15, 0, IN)


def test_after_a_long_sleep_the_next_time_is_in_the_future():
    due = ts(2026, 10, 10, 9, 0, IN)
    now = ts(2026, 10, 16, 12, 0, IN)
    assert next_due(due, "daily", now, IN) == ts(2026, 10, 17, 9, 0, IN)


def test_spoken_time_is_relative_to_today():
    now = ts(2026, 10, 16, 1, 0, IN)
    assert spoken_time(ts(2026, 10, 16, 15, 0, IN), IN, now) == "3:00 pm"
    assert spoken_time(ts(2026, 10, 17, 9, 5, IN), IN, now) == "tomorrow at 9:05 am"
    assert spoken_time(ts(2026, 10, 20, 15, 0, IN), IN, now) == "Tuesday at 3:00 pm"
    assert spoken_time(ts(2026, 11, 20, 15, 0, IN), IN, now) == "20 Nov at 3:00 pm"
