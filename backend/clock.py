"""The current time in the user's timezone, as a tool: the front model has no clock of its own."""

import os
from datetime import datetime
from pathlib import Path

from pipecat.adapters.schemas.direct_function import tool_options


def _zone() -> str:
    """The IANA name (Asia/Kolkata) from TZ or /etc/localtime; the abbreviation alone (IST) is ambiguous."""
    if os.environ.get("TZ"):
        return os.environ["TZ"]
    target = str(Path("/etc/localtime").resolve())
    return target.split("zoneinfo/", 1)[1] if "zoneinfo/" in target else ""


def now_info(now: datetime | None = None, zone: str | None = None) -> dict:
    now = now or datetime.now().astimezone()
    zone = _zone() if zone is None else zone
    offset = now.strftime("%z")
    label = f"{now.tzname()}, UTC{offset[:3]}:{offset[3:]}"
    return {"time": now.strftime("%-I:%M %p"), "date": now.strftime("%A, %-d %B %Y"),
            "timezone": f"{zone} ({label})" if zone else label, "iso": now.isoformat(timespec="seconds")}


@tool_options(cancel_on_interruption=False)
async def current_time(params):
    """Get the current local time, date, weekday and timezone. Call it whenever the user asks the time or date,
    or whenever an answer depends on today (tomorrow, this weekend, how long until)."""
    await params.result_callback(now_info())
