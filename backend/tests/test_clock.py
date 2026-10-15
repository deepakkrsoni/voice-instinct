import asyncio
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from clock import current_time, now_info


def test_now_info_says_the_time_date_and_timezone():
    info = now_info(datetime(2026, 10, 16, 1, 30, tzinfo=ZoneInfo("Asia/Kolkata")), zone="Asia/Kolkata")
    assert info == {"time": "1:30 AM", "date": "Friday, 16 October 2026", "timezone": "Asia/Kolkata (IST, UTC+05:30)",
                    "iso": "2026-10-16T01:30:00+05:30"}


def test_the_tool_answers_with_the_current_local_time():
    got = []

    async def cb(result, **kw):
        got.append(result)
    asyncio.run(current_time(SimpleNamespace(result_callback=cb)))
    assert set(got[0]) == {"time", "date", "timezone", "iso"}
    assert abs(datetime.fromisoformat(got[0]["iso"]) - datetime.now().astimezone()).total_seconds() < 5
