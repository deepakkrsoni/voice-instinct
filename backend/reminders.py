"""Reminders (#4): set by voice, kept in the registry, delivered by voice when the owner is at the laptop
and by Telegram otherwise. See docs/superpowers/specs/2026-10-16-reminders-design.md."""

import asyncio
import subprocess
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from loguru import logger
from pipecat.adapters.schemas.direct_function import tool_options

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
        self._recovered = False

    async def tick(self):
        now = self.clock()
        if not self._recovered:  # left in flight by a previous run that stopped before saying them
            self._recovered = True
            if n := self.reg.requeue_in_flight():
                logger.info(f"{n} reminder(s) were never delivered; retrying")
        for r in self.reg.due_reminders(now):
            if not self.reg.mark_fired(r["id"], now):
                continue  # claimed elsewhere, or cancelled meanwhile
            sent = await self._deliver(r, now)
            if sent is False:
                self.reg.unfire_reminder(r["id"])  # no channel: kept, retried next tick
            elif sent is True:
                self._delivered(r, now)  # "queued": _delivered runs once Nova has actually said it

    def _delivered(self, r, now):
        self.reg.mark_delivered(r["id"])
        logger.info(f"reminder {r['id']} delivered")  # never the text
        if r["repeat"] != "none":  # only after delivery, so an undeliverable one can't multiply
            self.reg.add_reminder(r["text"], next_due(r["due"], r["repeat"], now, self.tz), r["repeat"])

    async def _deliver(self, r, now):
        """True: delivered. "queued": waiting for a quiet moment to be spoken. False: no channel."""
        when = spoken_time(r["due"], self.tz, now)
        late = f" (was due at {when})" if now - r["due"] > LATE_SECS else ""
        if self.relay.attached and not await asyncio.to_thread(self.locked):
            self.relay.say(f"A reminder the user set is due now{late}: {r['text']}",
                           on_spoken=lambda: self._delivered(r, now),
                           on_dropped=lambda: self.reg.unfire_reminder(r["id"]))
            return "queued"
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


def make_reminder_tools(registry, telegram, clock=time.time, tz=None):
    tz = tz or local_zone()

    @tool_options(cancel_on_interruption=False)
    async def set_reminder(params, what: str, at: str, repeat: str = "none"):
        """Set a reminder. Call current_time first, then pass the exact local time.

        Args:
            what: What to remind the user about, in a few words ("call mom").
            at: Local date and time, ISO 8601, e.g. "2026-10-16T15:00".
            repeat: "none", "daily" or "weekly".
        """
        now = clock()
        try:
            t = datetime.fromisoformat(at)
        except ValueError:
            return await params.result_callback({"status": "refused", "reason": "at is not a date and time"})
        due = (t if t.tzinfo else t.replace(tzinfo=tz)).timestamp()
        if not what.strip() or repeat not in REPEATS:
            return await params.result_callback({"status": "refused", "reason": "need what, and repeat none/daily/weekly"})
        if due <= now:
            if repeat == "none":
                return await params.result_callback({"status": "refused", "reason": "that time has passed; ask the user"})
            due = next_due(due, repeat, now, tz)
        rid = registry.add_reminder(what.strip(), due, repeat)
        await params.result_callback({"status": "set", "id": rid, "when": spoken_time(due, tz, now), "repeat": repeat})

    @tool_options(cancel_on_interruption=False)
    async def list_reminders(params):
        """List the user's pending reminders, soonest first."""
        now = clock()
        await params.result_callback({"reminders": [
            {"id": r["id"], "what": r["text"], "when": spoken_time(r["due"], tz, now), "repeat": r["repeat"]}
            for r in registry.pending_reminders()]})

    @tool_options(cancel_on_interruption=False)
    async def cancel_reminder(params, reminder_id: int):
        """Cancel one pending reminder. Use list_reminders to find its id.

        Args:
            reminder_id: The id from list_reminders.
        """
        ok = registry.cancel_reminder(int(reminder_id))
        await params.result_callback({"status": "cancelled" if ok else "no_such_reminder"})

    @tool_options(cancel_on_interruption=False)
    async def send_telegram(params, text: str):
        """Send the user a Telegram message (only ever to them), e.g. an address or a list they asked for.

        Args:
            text: The message.
        """
        if telegram is None:
            return await params.result_callback({"status": "not_configured"})
        ok = await telegram.send_message(text)
        await params.result_callback({"status": "sent" if ok else "failed"})

    return [set_reminder, list_reminders, cancel_reminder, send_telegram]
