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


def test_hiding_the_orb_by_hand_puts_it_to_sleep_even_with_a_question_open():
    def shown_then_hidden(w):
        handle_client_message(w, SimpleNamespace(type="wake", data=None))
        handle_client_message(w, SimpleNamespace(type="sleep", data=None))

    users, _, sent, wake = asyncio.run(converse(["what's on today"], hold=lambda: True, before=shown_then_hidden))
    assert users == [] and {"type": "sleep"} in sent and not wake.awake


from pipecat.utils.asyncio.task_manager import TaskManager
from wake import make_dismiss


async def _ready_wake():
    """A wake strategy set up outside a pipeline, awake as if the orb was shown."""
    async def send(m):
        pass
    tm = TaskManager(loop=asyncio.get_running_loop())
    w = make_wake(["hey milo"], 30, send)
    await w.setup(SimpleNamespace(task_manager=tm))
    w.wake("shown")
    return w


def test_dismiss_sleeps_after_the_goodbye_and_a_later_reply_wakes_it():
    async def go():
        w = await _ready_wake()
        results = []

        async def cb(result, *, properties=None):
            results.append(properties)
        await make_dismiss(w)(SimpleNamespace(result_callback=cb))
        assert results[0].run_llm is False  # no second response that would wake the orb again
        w.wake("reply")  # the goodbye line starting
        assert w.awake  # awake until it finishes
        w.assistant_turn_done()
        assert not w.awake
        w.wake("reply")  # an errand's result after the goodbye: show the orb again
        assert w.awake
        await w.cleanup()
    asyncio.run(go())


def test_dismiss_without_a_goodbye_sleeps_after_grace():
    async def go():
        w = await _ready_wake()
        w.dismiss(grace=0.1)
        await asyncio.sleep(0.2)
        assert not w.awake
        await w.cleanup()
    asyncio.run(go())


def test_assistant_turn_done_keeps_awake_when_not_dismissing():
    async def go():
        w = await _ready_wake()
        w.assistant_turn_done()
        assert w.awake
        await w.cleanup()
    asyncio.run(go())


def test_learned_phrases_wake_it_once_added():
    users, _, sent, _ = asyncio.run(converse(["Hey Mailo, hello"], early=lambda w: w.add_phrases(["hey mailo"])))
    assert users == ["Hey Mailo, hello"] and sent[0] == {"type": "wake", "reason": "phrase"}


def test_would_wake_only_while_asleep_and_only_with_the_phrase():
    async def go():
        async def send(m):
            pass
        w = make_wake(["hey nova"], 30, send)
        await w.setup(SimpleNamespace(task_manager=TaskManager(loop=asyncio.get_running_loop())))
        asleep = (w.would_wake("Hey, Nova! What's on?"), w.would_wake("what's on today"))
        w.wake("shown")
        awake = w.would_wake("Hey Nova")
        await w.cleanup()
        return asleep, awake
    assert asyncio.run(go()) == ((True, False), False)
