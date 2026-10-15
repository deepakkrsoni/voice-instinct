"""Nova's one ongoing conversation: what is saved, how it is restored, and that it stays bounded."""

import asyncio
import json

from conversation import ASSISTANT_PARAMS, make_context, restorable, save
from pipecat.frames.frames import (
    FunctionCallFromLLM,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    FunctionCallResultProperties,
    FunctionCallsStartedFrame,
    LLMContextFrame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
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


def test_a_tool_call_with_no_result_at_all_gets_an_interrupted_result():
    kept = restorable([{"role": "user", "content": "find t-shirts"}, CALL])
    assert kept[:2] == [{"role": "user", "content": "find t-shirts"}, CALL]
    assert kept[2]["role"] == "tool" and kept[2]["tool_call_id"] == "c1"
    assert json.loads(kept[2]["content"])["status"] == "interrupted"
    assert restorable([CALL, RESULT]) == [CALL, RESULT]  # answered: kept as it is


def _aggregated(final):
    """The messages the real assistant aggregator writes for one async delegate call (as in bot.py)."""
    ctx = LLMContext(messages=[{"role": "user", "content": "find t-shirts"}])
    _, assistant = LLMContextAggregatorPair(ctx, assistant_params=ASSISTANT_PARAMS)
    args = {"task": "find t-shirts"}
    fc = FunctionCallFromLLM(function_name="delegate", tool_call_id="c1", arguments=args, context=ctx)
    frames = [FunctionCallsStartedFrame(function_calls=[fc]),
              FunctionCallInProgressFrame(function_name="delegate", tool_call_id="c1", arguments=args,
                                          cancel_on_interruption=False),
              FunctionCallResultFrame(function_name="delegate", tool_call_id="c1", arguments=args,
                                      result={"status": "started", "errand": 7}, run_llm=False,
                                      properties=FunctionCallResultProperties(run_llm=False, is_final=False))]
    if final:
        frames.append(FunctionCallResultFrame(function_name="delegate", tool_call_id="c1", arguments=args,
                                              result={"status": "done", "errand": 7, "result": "Levi's 376"}, run_llm=False,
                                              properties=FunctionCallResultProperties(run_llm=False)))
    asyncio.run(run_test(assistant, frames_to_send=[*frames, SleepFrame(sleep=0.1)]))
    return ctx.get_messages()


def test_an_errand_running_at_disconnect_is_restored_as_interrupted_not_still_running():
    # Opus review of #24: Pipecat writes a "still running, do not call it again" message for every async
    # call, so the old cut never fired and Nova said "still searching" for an errand that was dead.
    kept = restorable(_aggregated(final=False))
    text = json.dumps(kept)
    assert "still running" not in text and '"status": "running"' not in text
    (result,) = [m for m in kept if m.get("role") == "tool"]
    assert result["tool_call_id"] == "c1"
    payload = json.loads(result["content"])
    assert payload["status"] == "interrupted" and payload["errand"] == 7 and "errand id" in payload["result"]


def test_a_finished_errand_is_restored_as_it_was():
    msgs = _aggregated(final=True)
    assert restorable(msgs) == msgs


def test_each_call_of_a_multi_call_turn_is_settled_on_its_own():
    two = {"role": "assistant", "content": None, "tool_calls": [
        {"id": "a", "type": "function", "function": {"name": "list_sessions", "arguments": "{}"}},
        {"id": "b", "type": "function", "function": {"name": "delegate", "arguments": "{}"}}]}
    done = {"role": "tool", "tool_call_id": "a", "content": '{"sessions": []}'}
    kept = restorable([two, done])
    assert kept[:2] == [two, done]  # neither the call message nor the answered result is dropped
    assert [m["tool_call_id"] for m in kept if m.get("role") == "tool"] == ["a", "b"]


def test_summaries_keep_every_errand_id_and_never_turn_tool_text_into_the_users_words():
    # Opus review of #24: Pipecat's default prompt dropped the ids (so follow-ups could not resume), and
    # its summary is a user-role message, so web text must never come out as the user's request.
    summary = ASSISTANT_PARAMS.auto_context_summarization_config.summary_config
    assert "errand <id>" in summary.summarization_prompt
    assert "never" in summary.summarization_prompt and "approval" in summary.summarization_prompt
    assert "not the user's words" in summary.summary_message_template


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


def test_forget_scrubs_the_fact_from_the_conversation_nova_restores():
    # Review: a forgotten fact lived on in the saved conversation (and its summary), restored at every connect.
    from conversation import scrub
    from memory import matcher
    reg = Registry()
    remember_call = {"role": "assistant", "content": None, "tool_calls": [{"id": "r1", "type": "function", "function": {
        "name": "remember", "arguments": '{"fact": "Is vegetarian"}'}}]}
    ctx = LLMContext(messages=[
        {"role": "user", "content": "Summary of the conversation so far: errand 3: t-shirts. The user is vegetarian."},
        {"role": "user", "content": "Remember that I'm vegetarian"}, remember_call,
        {"role": "tool", "tool_call_id": "r1", "content": '{"status": "remembered"}'},
        {"role": "tool", "tool_call_id": "r2", "content": '{"found": "notes.md: - Is vegetarian"}'},
        {"role": "assistant", "content": [{"type": "text", "text": "Noted. You're vegetarian."}]},
        {"role": "user", "content": "thanks"}])
    scrub(ctx, matcher("that I'm vegetarian"), reg)
    assert "vegetarian" not in json.dumps(ctx.get_messages()).lower()
    msgs = ctx.get_messages()
    assert msgs[0]["content"] == "Summary of the conversation so far: errand 3: t-shirts."  # the rest stays
    assert msgs[2]["tool_calls"][0]["id"] == "r1" and msgs[6]["content"] == "thanks"
    assert "vegetarian" not in json.dumps(reg.load_conversation()).lower()  # and the saved copy


def test_the_ongoing_conversation_stays_bounded_by_summarization():
    assert ASSISTANT_PARAMS.enable_auto_context_summarization is True
