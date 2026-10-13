import asyncio

from pipecat.frames.frames import (
    LLMContextFrame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.tests.utils import SleepFrame, run_test
from pipecat.turns.user_stop import SpeechTimeoutUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies, default_user_turn_start_strategies
from pipecat.utils.time import time_now_iso8601

from types import SimpleNamespace

from wake import handle_client_message, make_wake


def said(text):
    return [VADUserStartedSpeakingFrame(), TranscriptionFrame(text, "u", time_now_iso8601()),
            VADUserStoppedSpeakingFrame(), SleepFrame(sleep=0.6)]


async def converse(texts, timeout=10, before=None, hold=None, pause=0.1, early=None):
    ctx, sent = LLMContext(), []

    async def send(msg):
        sent.append(msg)

    wake = make_wake(["hey nova", "okay nova"], timeout, send, **({"hold": hold} if hold else {}))
    if early:
        early(wake)  # before the pipeline has set the strategy up
    user, _ = LLMContextAggregatorPair(ctx, user_params=LLMUserAggregatorParams(
        user_turn_strategies=UserTurnStrategies(
            start=[wake, *default_user_turn_start_strategies()],
            stop=[SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=0.2)])))
    frames = [f for t in texts for f in said(t)]
    if before:
        frames = [SleepFrame(sleep=pause), *frames]

        async def poke():
            await asyncio.sleep(0.05)
            before(wake)
        asyncio.get_running_loop().create_task(poke())
    down, _ = await run_test(user, frames_to_send=frames)
    users = [m.get("content") for m in ctx.get_messages() if m.get("role") == "user"]
    return users, sum(isinstance(f, LLMContextFrame) for f in down), sent, wake


def test_speech_without_the_wake_phrase_never_reaches_the_llm():
    users, runs, sent, _ = asyncio.run(converse(["what's the weather like", "call my mum"]))
    assert users == [] and runs == 0 and sent == []


def test_the_wake_phrase_lets_that_turn_through_and_tells_the_gui():
    users, runs, sent, _ = asyncio.run(converse(["chit chat", "Hey, Nova. What's the weather like?"]))
    assert users == ["Hey, Nova. What's the weather like?"] and runs == 1
    assert sent[0] == {"type": "wake", "reason": "phrase"}


def test_it_falls_asleep_after_the_timeout_and_says_so():
    users, runs, sent, wake = asyncio.run(converse(["hey nova hello", "and another thing"], timeout=0.3))
    assert {"type": "sleep"} in sent and not wake.awake
    assert users == ["hey nova hello"]  # the second turn came after the timeout


def test_programmatic_wake_lets_the_next_reply_through_without_the_phrase():
    # A Claude question while asleep: the relay wakes it so the user's "yes" is heard.
    users, runs, sent, _ = asyncio.run(converse(["yes go ahead"], before=lambda w: w.wake("question")))
    assert users == ["yes go ahead"] and sent[0] == {"type": "wake", "reason": "question"}


def test_keep_awake_restarts_the_window_only_when_awake():
    async def go():
        sent = []

        async def send(m):
            sent.append(m)

        w = make_wake(["hey nova"], 10, send)
        w.keep_awake()  # asleep: must not wake it
        return w.awake

    assert asyncio.run(go()) is False


def test_keep_awake_extends_the_window_while_awake_then_it_sleeps():
    async def go():
        ctx, sent, mid = LLMContext(), [], {}

        async def send(m):
            sent.append(m)

        w = make_wake(["hey nova"], 0.3, send)
        user, _ = LLMContextAggregatorPair(ctx, user_params=LLMUserAggregatorParams(
            user_turn_strategies=UserTurnStrategies(
                start=[w, *default_user_turn_start_strategies()],
                stop=[SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=0.2)])))

        async def poke():
            await asyncio.sleep(0.05)
            w.wake("question")
            for _ in range(8):  # 0.8s, well over two 0.3s windows
                await asyncio.sleep(0.1)
                w.keep_awake()
            mid["awake"], mid["sent"] = w.awake, list(sent)

        task = asyncio.get_running_loop().create_task(poke())
        await run_test(user, frames_to_send=[SleepFrame(sleep=1.8)])
        await task
        return mid, sent, w

    mid, sent, w = asyncio.run(go())
    assert mid["awake"] and {"type": "sleep"} not in mid["sent"]
    assert {"type": "sleep"} in sent and not w.awake


def test_showing_the_orb_by_hand_wakes_it_so_it_can_hear_you():
    # Tray "Show / Hide" or `voice-instinct toggle` sends the documented RTVI client message "wake".
    msg = SimpleNamespace(type="wake", data=None)
    users, _, sent, _ = asyncio.run(converse(["what's on today"], before=lambda w: handle_client_message(w, msg)))
    assert users == ["what's on today"] and sent[0]["type"] == "wake"


def test_other_client_messages_do_not_wake_it():
    msg = SimpleNamespace(type="set-language", data={})
    users, _, sent, _ = asyncio.run(converse(["what's on today"], before=lambda w: handle_client_message(w, msg)))
    assert users == [] and sent == []


def test_it_stays_awake_while_a_question_waits_for_an_answer():
    # The user takes longer than the quiet timeout to answer; the answer must still be heard.
    users, _, sent, _ = asyncio.run(converse(
        ["yes go ahead"], timeout=0.3, hold=lambda: True, pause=0.9, before=lambda w: w.wake("question")))
    assert users == ["yes go ahead"] and {"type": "sleep"} not in sent


def test_without_an_open_question_the_timeout_still_applies():
    users, _, sent, _ = asyncio.run(converse(
        ["yes go ahead"], timeout=0.3, hold=lambda: False, pause=0.9, before=lambda w: w.wake("question")))
    assert users == [] and {"type": "sleep"} in sent


def test_a_wake_before_the_pipeline_starts_takes_effect_once_it_does():
    # A reconnect with an open question can wake the gate before the aggregator's setup ran.
    users, _, sent, _ = asyncio.run(converse(["yes go ahead"], early=lambda w: w.wake("question")))
    assert users == ["yes go ahead"] and sent[0] == {"type": "wake", "reason": "question"}
