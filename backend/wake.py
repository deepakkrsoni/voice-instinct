"""Wake phrase gate: Pipecat's documented WakePhraseUserTurnStartStrategy, plus programmatic wake.

`wake()` and `keep_awake()` are ours: the strategy has no public way to wake from code, so they call
its private `_transition_to_awake` / `_refresh_timeout`, `add_phrases` extends its `_phrases` / `_patterns`,
and `hold` overrides `_transition_to_idle`
(all pinned by tests against Pipecat 1.12).
"""

import asyncio
import re

from loguru import logger

from pipecat.adapters.schemas.direct_function import tool_options
from pipecat.frames.frames import FunctionCallResultProperties
from pipecat.turns.user_start import WakePhraseUserTurnStartStrategy
from pipecat.turns.user_start.wake_phrase_user_turn_start_strategy import _WakeState


class ProgrammaticWake(WakePhraseUserTurnStartStrategy):
    def __init__(self, *, hold=lambda: False, **kwargs):
        super().__init__(**kwargs)
        self._hold = hold  # while it returns True (a question waits for an answer), never fall asleep
        self._early_wake = None
        self._dismissing = None  # a timer while a dismissal waits for the goodbye line (#32)

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
        if reason == "reply" and self._dismissing:
            return  # the goodbye line itself: stay dismissed
        if self._timeout_task is None:  # not set up yet (a reconnect with a question open)
            self._early_wake = reason
        elif not self.awake:
            self._transition_to_awake(reason)

    def sleep(self) -> None:
        """Stop listening now, even while a question waits (the user hid the orb)."""
        if self.awake:
            super()._transition_to_idle()

    def keep_awake(self) -> None:
        """Restart the awake window, e.g. when the bot finishes speaking, so the user can reply."""
        if self.awake:
            self._refresh_timeout()

    def would_wake(self, text: str) -> bool:
        """Asleep, and this text has a wake phrase: the one segment the owner check judges (#32)."""
        if self.awake:
            return False
        text = self._strip_punctuation(text)
        return any(p.search(text) for p in self._patterns)

    def add_phrases(self, phrases) -> None:
        """More wake phrases (learned at enrolment), compiled like the strategy's own in __init__."""
        for phrase in phrases:
            if phrase not in self._phrases:
                self._phrases.append(phrase)
                self._patterns.append(re.compile(
                    r"\b" + r"\s*".join(re.escape(w) for w in phrase.split()) + r"\b", re.IGNORECASE))

    def dismiss(self, grace: float = 5.0) -> None:
        """The user said stop: sleep when the goodbye line ends, or after `grace` if none comes."""
        self._cancel_dismiss()
        self._dismissing = asyncio.get_running_loop().call_later(grace, self._finish_dismiss)

    def assistant_turn_done(self) -> None:
        """Milo finished speaking: sleep if dismissed, otherwise give the user time to reply."""
        if self._dismissing:
            self._finish_dismiss()
        else:
            self.keep_awake()

    def _finish_dismiss(self):
        self._cancel_dismiss()
        self.sleep()

    def _cancel_dismiss(self):
        if self._dismissing:
            self._dismissing.cancel()
            self._dismissing = None


def make_wake(phrases, timeout, send, hold=lambda: False) -> ProgrammaticWake:
    w = ProgrammaticWake(phrases=phrases, timeout=timeout, hold=hold)

    @w.event_handler("on_wake_phrase_detected")
    async def _woke(strategy, phrase):
        logger.info(f"awake ({phrase})")  # markers only: never what was said
        await send({"type": "wake", "reason": "question" if phrase == "question" else "phrase"})

    @w.event_handler("on_wake_phrase_timeout")
    async def _slept(strategy):
        logger.info("asleep")
        await send({"type": "sleep"})

    return w


def make_dismiss(wake: ProgrammaticWake):
    @tool_options(cancel_on_interruption=False)
    async def dismiss(params):
        """End the conversation and hide when the user says stop, that's all, go away or similar.
        First say one short goodbye line. Running errands keep running; this never stops one."""
        wake.dismiss()
        await params.result_callback({"status": "dismissed"},
                                     properties=FunctionCallResultProperties(run_llm=False))
    return dismiss


def handle_client_message(wake: ProgrammaticWake, msg) -> None:
    """The desktop app sends "wake" / "sleep" when the user shows or hides the orb by hand."""
    if msg.type == "wake":
        wake.wake("shown")
    elif msg.type == "sleep":
        wake.sleep()
