"""Nova's one ongoing conversation: what is saved, how it is restored, and that it stays bounded."""

import asyncio

from conversation import ASSISTANT_PARAMS, make_context, restorable, save
from pipecat.frames.frames import LLMContextFrame, TranscriptionFrame, VADUserStartedSpeakingFrame, VADUserStoppedSpeakingFrame
from pipecat.processors.aggregators.llm_context import LLMContext, LLMSpecificMessage
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair, LLMUserAggregatorParams
from pipecat.tests.utils import SleepFrame, run_test
from pipecat.turns.user_stop import SpeechTimeoutUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.utils.time import time_now_iso8601
from registry import Registry

CALL = {"role": "assistant", "content": None,
        "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "delegate", "arguments": "{}"}}]}
RESULT = {"role": "tool", "tool_call_id": "c1", "content": '{"status": "done", "errand": 3}'}


def test_gemini_thought_signatures_are_not_saved():
    sig = LLMSpecificMessage(llm="google", message={"type": "thought_signature", "signature": b"\x00\x01"})
    assert restorable([{"role": "user", "content": "hi"}, sig]) == [{"role": "user", "content": "hi"}]


def test_a_tool_call_that_never_got_its_result_is_cut():
    # The orb disconnected while an errand was running: the model must never see a call without a result.
    assert restorable([{"role": "user", "content": "find t-shirts"}, CALL]) == [{"role": "user", "content": "find t-shirts"}]
    assert restorable([CALL, RESULT]) == [CALL, RESULT]  # answered: kept


def test_a_new_connection_starts_from_the_saved_conversation():
    reg = Registry()
    ctx = LLMContext(messages=[{"role": "user", "content": "find t-shirts"}, CALL, RESULT,
                               {"role": "assistant", "content": "Levi's for 376 rupees."}])
    save(reg, ctx)
    assert make_context(reg, tools=[]).get_messages() == ctx.get_messages()


def test_the_restored_conversation_reaches_the_model_with_the_new_turn():
    reg = Registry()
    reg.save_conversation([{"role": "user", "content": "find t-shirts"},
                           {"role": "assistant", "content": "Levi's for 376 rupees."}])
    ctx = make_context(reg, tools=[])
    user, _ = LLMContextAggregatorPair(ctx, user_params=LLMUserAggregatorParams(
        user_turn_strategies=UserTurnStrategies(stop=[SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=0.2)])))
    frames = [VADUserStartedSpeakingFrame(), TranscriptionFrame("which had the best rating?", "u", time_now_iso8601()),
              VADUserStoppedSpeakingFrame(), SleepFrame(sleep=0.6)]
    down, _ = asyncio.run(run_test(user, frames_to_send=frames))
    sent = next(f for f in down if isinstance(f, LLMContextFrame)).context.get_messages()
    assert [m["content"] for m in sent] == ["find t-shirts", "Levi's for 376 rupees.", "which had the best rating?"]


def test_a_failing_save_never_breaks_the_session():
    class Broken:
        def save_conversation(self, messages):
            raise OSError("disk full")
    save(Broken(), LLMContext(messages=[{"role": "user", "content": "hi"}]))  # logged, not raised


def test_the_ongoing_conversation_stays_bounded_by_summarization():
    assert ASSISTANT_PARAMS.enable_auto_context_summarization is True
