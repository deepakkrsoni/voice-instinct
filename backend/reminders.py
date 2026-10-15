"""Reminders (#4): set by voice, kept in the registry, delivered by voice when the owner is at the laptop
and by Telegram otherwise. See docs/superpowers/specs/2026-10-16-reminders-design.md."""

import asyncio
import subprocess
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from loguru import logger

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

TICK_SECS = 15
LATE_SECS = 120


def screen_locked() -> bool:
    """logind's LockedHint for a graphical session, or swaylock running (it doesn't set the hint).
    Unknown counts as unlocked: a reminder then goes to the orb, which is the safer miss."""
    try:
        if subprocess.run(["pgrep", "-x", "swaylock"], capture_output=True, timeout=2).returncode == 0:
            return True
        out = subprocess.run(["loginctl", "list-sessions", "--no-legend"], capture_output=True, text=True,
                             timeout=2).stdout
        for sid in [line.split()[0] for line in out.splitlines() if line.strip()]:  # --output=json isn't everywhere
            out = subprocess.run(["loginctl", "show-session", sid, "-p", "Type", "-p", "LockedHint"],
                                 capture_output=True, text=True, timeout=2).stdout
            if ("Type=wayland" in out or "Type=x11" in out) and "LockedHint=yes" in out:
                return True
    except Exception:
        logger.warning("could not read the screen lock state; treating it as unlocked")
    return False


class Reminders:
    def __init__(self, registry, relay, telegram, locked=screen_locked, clock=time.time, tz=None):
        self.reg, self.relay, self.telegram, self.locked, self.clock = registry, relay, telegram, locked, clock
        self.tz = tz or local_zone()
        self._task = None

    async def tick(self):
        now = self.clock()
        for r in self.reg.due_reminders(now):
            if not self.reg.mark_fired(r["id"], now):
                continue  # claimed elsewhere, or cancelled meanwhile
            if not await self._deliver(r, now):
                self.reg.unfire_reminder(r["id"])  # no channel: kept, retried next tick
                continue
            logger.info(f"reminder {r['id']} delivered")  # never the text
            if r["repeat"] != "none":  # only after delivery, so an undeliverable one can't multiply
                self.reg.add_reminder(r["text"], next_due(r["due"], r["repeat"], now, self.tz), r["repeat"])

    async def _deliver(self, r, now) -> bool:
        when = spoken_time(r["due"], self.tz, now)
        late = f" (was due at {when})" if now - r["due"] > LATE_SECS else ""
        if self.relay.attached and not await asyncio.to_thread(self.locked):
            self.relay.say(f"A reminder the user set is due now{late}: {r['text']}")
            return True
        if self.telegram is not None:
            return await self.telegram.send_message(f"⏰ {r['text']} ({when})" + (" — late" if late else ""))
        return False

    async def run(self):
        while True:
            try:
                await self.tick()
            except Exception:
                logger.exception("reminder tick failed")
            await asyncio.sleep(TICK_SECS)

    def start(self):
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self.run())
