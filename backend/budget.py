"""Errand time budget: the agent cannot see a clock, so the PostToolUse hook tells it, once, when an errand
is nearly out of time, and it answers with what it has instead of being cut off with nothing (task 11
spent its whole limit opening product pages). Claude Code adds the hook's additionalContext to what the
agent reads after that tool call."""

import time

WARN_AT = 0.75  # of the errand's timeout


class TimeBudget:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._runs = {}  # session id -> [started, timeout, warned]

    def start(self, session_id, timeout):
        """A run of this session begins; a resumed session gets a fresh budget."""
        self._runs[session_id] = [self._clock(), timeout, False]

    def finish(self, session_id):
        self._runs.pop(session_id, None)

    def on_step(self, p):
        run = self._runs.get(p.get("session_id"))
        if run is None or run[2]:
            return {}
        started, timeout, _ = run
        elapsed = self._clock() - started
        if elapsed < timeout * WARN_AT:
            return {}
        run[2] = True
        minutes = max(1, round((timeout - elapsed) / 60))
        return {"hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": (f"Time check: this errand will be stopped in about {minutes} minute"
                                  f"{'' if minutes == 1 else 's'}. Stop researching and answer now with the best "
                                  "of what you have found, saying what you could not check."),
        }}
