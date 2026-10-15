"""OwnerVADUserTurnStartStrategy in the real user aggregator: only the owner's voice starts a turn
(and so interrupts Milo); it decides on the first second, or on a shorter utterance at its VAD stop."""

import asyncio

import pytest

from pipecat.frames.frames import InputAudioRawFrame, VADUserStartedSpeakingFrame, VADUserStoppedSpeakingFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair, LLMUserAggregatorParams
from pipecat.tests.utils import SleepFrame, run_test
from pipecat.turns.user_stop import SpeechTimeoutUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies

from voiceprint import OwnerVADUserTurnStartStrategy


class FakePrint:
    def __init__(self):
        self.heard = []

    def is_owner(self, pcm, rate=16000):
        self.heard.append(len(pcm) / (2 * rate))
        return pcm[:1] == b"\x01"


def audio(secs, who):
    frame = (b"\x01" if who == "owner" else b"\x02") + b"\x00" * 3199  # 100 ms at 16 kHz
    return [InputAudioRawFrame(audio=frame, sample_rate=16000, num_channels=1) for _ in range(round(secs * 10))]


def utterance(secs, who):
    return [VADUserStartedSpeakingFrame(), *audio(secs, who), VADUserStoppedSpeakingFrame(), SleepFrame(sleep=0.3)]


def turns(frames):
    vp, started = FakePrint(), []
    user, _ = LLMContextAggregatorPair(LLMContext(), user_params=LLMUserAggregatorParams(
        user_turn_strategies=UserTurnStrategies(
            start=[OwnerVADUserTurnStartStrategy(vp)],
            stop=[SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=0.1)])))

    @user.event_handler("on_user_turn_started")
    async def _started(agg, strategy):
        started.append(strategy)

    asyncio.run(run_test(user, frames_to_send=frames))
    return len(started), vp.heard


def test_the_owner_starts_a_turn_after_one_second():
    n, heard = turns(utterance(2.0, "owner"))
    assert n == 1 and heard == [pytest.approx(1.0)]  # checked once, on the first second


def test_someone_else_never_starts_a_turn():
    n, heard = turns(utterance(2.0, "other"))
    assert n == 0 and heard == [pytest.approx(1.0)]


def test_a_short_owner_utterance_is_checked_at_its_vad_stop():
    n, heard = turns(utterance(0.5, "owner"))
    assert n == 1 and heard == [pytest.approx(0.5)]


def test_under_min_secs_is_not_checked():
    n, heard = turns(utterance(0.3, "owner"))
    assert n == 0 and heard == []  # the transcript check still sees it (Parakeet)


def test_each_utterance_starts_a_fresh_check():
    # Someone else's half-heard audio must not leak into the owner's next check.
    n, heard = turns([VADUserStartedSpeakingFrame(), *audio(0.6, "other"), *utterance(1.0, "owner")])
    assert n == 1 and heard == [pytest.approx(1.0)]
