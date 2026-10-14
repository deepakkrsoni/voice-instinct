"""The #19 bug end to end, with real models: Kokoro speaks "Hey Nova. Find me a laptop." (with a natural
pause) into the real STT engine and the real user aggregator (Silero VAD, Smart Turn, the wake gate, as
in bot.py), driven by pipecat.tests.utils.run_test. Skipped for each engine that is not installed."""

import asyncio
from pathlib import Path

import numpy as np
import pytest
import soxr

from config import Config
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import InputAudioRawFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair, LLMUserAggregatorParams
from pipecat.tests.utils import SleepFrame, run_test
from pipecat.turns.user_turn_strategies import UserTurnStrategies, default_user_turn_start_strategies
from services import STT_MODELS, kokoro_paths, make_stt
from wake import make_wake

MODELS = Path.home() / ".voice-instinct/models"
KOKORO = kokoro_paths(Config(models_dir=str(MODELS)))


def spoken(*sentences, pause=0.6):
    """16 kHz 16-bit mono speech: each sentence spoken separately with exactly `pause` seconds of silence
    between them, 0.5 s before and 3 s after."""
    from kokoro_onnx import Kokoro
    kokoro = Kokoro(str(KOKORO[0]), str(KOKORO[1]))
    parts = [np.zeros(8000)]
    for i, sentence in enumerate(sentences):
        samples, rate = kokoro.create(sentence, voice="af_heart")
        parts += [np.zeros(int(16000 * pause))] if i else []
        parts.append(soxr.resample(samples, rate, 16000))
    parts.append(np.zeros(48000))
    return (np.clip(np.concatenate(parts), -1, 1) * 32767).astype(np.int16).tobytes()


def converse(engine, *sentences, pause=0.6):
    """Each user turn as the aggregator ends it (on_user_turn_stopped), and what the GUI was told."""
    if not (MODELS / STT_MODELS[engine]).is_dir() or not KOKORO[0].exists():
        pytest.skip(f"{engine} or Kokoro is not installed (./install.sh)")
    cfg = Config(models_dir=str(MODELS), stt_engine=engine)
    pcm, sent, turns = spoken(*sentences, pause=pause), [], []

    async def send(msg):
        sent.append(msg)

    wake = make_wake(cfg.wake_phrases, 10, send)
    user, _ = LLMContextAggregatorPair(LLMContext(), user_params=LLMUserAggregatorParams(
        vad_analyzer=SileroVADAnalyzer(),
        user_turn_strategies=UserTurnStrategies(start=[wake, *default_user_turn_start_strategies()])))

    @user.event_handler("on_user_turn_stopped")
    async def on_turn(aggregator, strategy, message):
        turns.append(message.content.lower())

    frames = []
    for i in range(0, len(pcm), 640):  # 20 ms frames, paced like the live mic
        frames += [InputAudioRawFrame(audio=pcm[i:i + 640], sample_rate=16000, num_channels=1), SleepFrame(sleep=0.02)]
    asyncio.run(run_test(Pipeline([make_stt(cfg), user]), frames_to_send=[*frames, SleepFrame(sleep=2.0)]))
    return turns, sent


@pytest.mark.parametrize("engine", list(STT_MODELS))
def test_hey_nova_wakes_it_and_the_whole_request_is_heard(engine):
    turns, sent = converse(engine, "Hey Nova.", "Find me a laptop.")
    assert {"type": "wake", "reason": "phrase"} in sent, f"{engine} never heard the wake phrase"
    assert "laptop" in " ".join(turns), f"{engine} heard: {turns!r}"


@pytest.mark.parametrize("pause", [0.4, 1.2])
@pytest.mark.parametrize("engine", list(STT_MODELS))
def test_a_pause_after_the_wake_phrase_keeps_one_turn(engine, pause):
    # Opus review of #19: a 1.2 s pause split Nemotron's turn (ttfs shorter than its endpoint), and a
    # 0.4 s pause split Parakeet's (a transcript arriving after the user resumed was marked finalized).
    turns, _ = converse(engine, "Hey Nova.", "Remind me to call mom.", pause=pause)
    assert len(turns) == 1 and "nova" in turns[0] and "mom" in turns[0], f"{engine} at {pause}s: {turns!r}"
