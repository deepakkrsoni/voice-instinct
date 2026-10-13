"""Wake phrase gate: Pipecat's documented WakePhraseUserTurnStartStrategy, plus programmatic wake.

`wake()` and `keep_awake()` are ours: the strategy has no public way to wake from code, so they call
its private `_transition_to_awake` / `_refresh_timeout`, and `hold` overrides `_transition_to_idle`
(all pinned by tests against Pipecat 1.12).
"""

from pipecat.turns.user_start import WakePhraseUserTurnStartStrategy
from pipecat.turns.user_start.wake_phrase_user_turn_start_strategy import _WakeState


class ProgrammaticWake(WakePhraseUserTurnStartStrategy):
    def __init__(self, *, hold=lambda: False, **kwargs):
        super().__init__(**kwargs)
        self._hold = hold  # while it returns True (a question waits for an answer), never fall asleep
        self._early_wake = None

    async def setup(self, setup):
        await super().setup(setup)
        if self._early_wake is not None:
            reason, self._early_wake = self._early_wake, None
            self.wake(reason)

    def _transition_to_idle(self):
        if not self._hold():
            super()._transition_to_idle()

    @property
    def awake(self) -> bool:
        return self.state == _WakeState.AWAKE

    def wake(self, reason: str = "question") -> None:
        """Wake without the phrase (for example to hear the reply to a Claude question)."""
        if self._timeout_task is None:  # not set up yet (a reconnect with a question open)
            self._early_wake = reason
        elif not self.awake:
            self._transition_to_awake(reason)

    def keep_awake(self) -> None:
        """Restart the awake window, e.g. when the bot finishes speaking, so the user can reply."""
        if self.awake:
            self._refresh_timeout()


def make_wake(phrases, timeout, send, hold=lambda: False) -> ProgrammaticWake:
    w = ProgrammaticWake(phrases=phrases, timeout=timeout, hold=hold)

    @w.event_handler("on_wake_phrase_detected")
    async def _woke(strategy, phrase):
        await send({"type": "wake", "reason": "question" if phrase == "question" else "phrase"})

    @w.event_handler("on_wake_phrase_timeout")
    async def _slept(strategy):
        await send({"type": "sleep"})

    return w


def handle_client_message(wake: ProgrammaticWake, msg) -> None:
    """The desktop app sends "wake" when the user shows the orb by hand, so it can hear them."""
    if msg.type == "wake":
        wake.wake("shown")
