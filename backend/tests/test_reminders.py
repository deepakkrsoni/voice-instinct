import pytest
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


import asyncio

from registry import Registry
from reminders import Reminders


class FakeRelay:
    def __init__(self, attached=True):
        self.attached, self.said = attached, []

    def say(self, text):
        self.said.append(text)


class FakeTelegram:
    def __init__(self, ok=True):
        self.ok, self.sent = ok, []

    async def send_message(self, text):
        self.sent.append(text)
        return self.ok


def make(now, attached=True, locked=False, tg=True, ok=True):
    reg = Registry(clock=lambda: now[0])
    relay, telegram = FakeRelay(attached), (FakeTelegram(ok) if tg else None)
    r = Reminders(reg, relay, telegram, locked=lambda: locked, clock=lambda: now[0], tz=IN)
    return reg, relay, telegram, r


def tick(r):
    asyncio.run(r.tick())


def test_present_it_is_spoken_once():
    now = [ts(2026, 10, 16, 15, 0, IN)]
    reg, relay, tg, r = make(now)
    reg.add_reminder("call mom", now[0], "none")
    tick(r)
    tick(r)
    assert len(relay.said) == 1 and "call mom" in relay.said[0] and tg.sent == []


def test_away_it_goes_to_telegram():
    now = [ts(2026, 10, 16, 15, 0, IN)]
    reg, relay, tg, r = make(now, locked=True)
    reg.add_reminder("call mom", now[0], "none")
    tick(r)
    assert relay.said == [] and tg.sent == ["⏰ call mom (3:00 pm)"]


def test_away_without_telegram_it_waits_then_is_spoken():
    now = [ts(2026, 10, 16, 15, 0, IN)]
    reg, relay, _, r = make(now, attached=False, tg=False)
    reg.add_reminder("call mom", now[0], "none")
    tick(r)
    assert reg.pending_reminders()  # put back, not lost
    relay.attached = True
    now[0] += 600
    tick(r)
    assert len(relay.said) == 1 and "was due at 3:00 pm" in relay.said[0]


def test_a_telegram_failure_is_retried():
    now = [ts(2026, 10, 16, 15, 0, IN)]
    reg, _, tg, r = make(now, attached=False, ok=False)
    reg.add_reminder("call mom", now[0], "none")
    tick(r)
    tg.ok = True
    tick(r)
    assert len(tg.sent) == 2 and reg.pending_reminders() == []


def test_an_undeliverable_daily_reminder_does_not_multiply():
    now = [ts(2026, 10, 16, 9, 0, IN)]
    reg, relay, _, r = make(now, attached=False, tg=False)
    reg.add_reminder("water", now[0], "daily")
    for _ in range(3):
        tick(r)
    assert len(reg.pending_reminders()) == 1


def test_a_daily_reminder_after_a_long_sleep_fires_once_then_moves_on():
    now = [ts(2026, 10, 16, 12, 0, IN)]
    reg, relay, _, r = make(now)
    reg.add_reminder("water", ts(2026, 10, 10, 9, 0, IN), "daily")
    tick(r)
    assert len(relay.said) == 1
    assert [x["due"] for x in reg.pending_reminders()] == [ts(2026, 10, 17, 9, 0, IN)]


def test_a_cancelled_reminder_never_fires():
    now = [ts(2026, 10, 16, 15, 0, IN)]
    reg, relay, tg, r = make(now)
    reg.cancel_reminder(reg.add_reminder("x", now[0], "none"))
    tick(r)
    assert relay.said == [] and tg.sent == []


from types import SimpleNamespace

from reminders import make_reminder_tools


def call(tool, **kw):
    got = []

    async def cb(result, **_):
        got.append(result)
    asyncio.run(tool(SimpleNamespace(result_callback=cb), **kw))
    return got[0]


def tools(now, tg=True):
    reg = Registry(clock=lambda: now)
    t = FakeTelegram() if tg else None
    return reg, t, {f.__name__: f for f in make_reminder_tools(reg, t, clock=lambda: now, tz=IN)}


def test_set_list_cancel():
    now = ts(2026, 10, 16, 1, 0, IN)
    reg, _, t = tools(now)
    r = call(t["set_reminder"], what="call mom", at="2026-10-16T15:00")
    assert r["status"] == "set" and r["when"] == "3:00 pm"
    listed = call(t["list_reminders"])["reminders"]
    assert listed[0]["what"] == "call mom" and listed[0]["id"] == r["id"]
    assert call(t["cancel_reminder"], reminder_id=r["id"])["status"] == "cancelled"
    assert call(t["list_reminders"])["reminders"] == []


@pytest.mark.parametrize("kw", [
    {"what": "x", "at": "2026-10-16T00:30"},           # one-off in the past
    {"what": "x", "at": "tomorrow-ish"},               # not a time
    {"what": "x", "at": "2026-10-16T15:00", "repeat": "hourly"},
    {"what": " ", "at": "2026-10-16T15:00"},
])
def test_bad_reminders_are_refused(kw):
    _, _, t = tools(ts(2026, 10, 16, 1, 0, IN))
    assert call(t["set_reminder"], **kw)["status"] == "refused"


def test_a_daily_reminder_may_start_in_the_past_and_rolls_forward():
    now = ts(2026, 10, 16, 10, 0, IN)
    _, _, t = tools(now)
    r = call(t["set_reminder"], what="water", at="2026-10-16T09:00", repeat="daily")
    assert r["status"] == "set" and r["when"] == "tomorrow at 9:00 am"


def test_send_telegram_only_with_config():
    now = ts(2026, 10, 16, 1, 0, IN)
    _, tg, t = tools(now)
    assert call(t["send_telegram"], text="hi")["status"] == "sent" and tg.sent == ["hi"]
    _, _, t2 = tools(now, tg=False)
    assert call(t2["send_telegram"], text="hi")["status"] == "not_configured"


def test_the_tools_build_valid_schemas():
    from pipecat.processors.aggregators.llm_context import LLMContext
    names = {s.name for s in LLMContext._normalize_and_validate_tools(
        make_reminder_tools(Registry(), None)).standard_tools}
    assert names == {"set_reminder", "list_reminders", "cancel_reminder", "send_telegram"}
