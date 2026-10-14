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


def spoken(text):
    """16 kHz 16-bit mono speech, with 0.5 s of silence before and 2 s after."""
    from kokoro_onnx import Kokoro
    samples, rate = Kokoro(str(KOKORO[0]), str(KOKORO[1])).create(text, voice="af_heart", sentence_pause=0.6)
    pcm = np.concatenate([np.zeros(8000), soxr.resample(samples, rate, 16000), np.zeros(32000)])
    return (np.clip(pcm, -1, 1) * 32767).astype(np.int16).tobytes()


@pytest.mark.parametrize("engine", list(STT_MODELS))
def test_hey_nova_wakes_it_and_the_whole_request_is_heard(engine):
    if not (MODELS / STT_MODELS[engine]).is_dir() or not KOKORO[0].exists():
        pytest.skip(f"{engine} or Kokoro is not installed (./install.sh)")
    cfg = Config(models_dir=str(MODELS), stt_engine=engine)
    pcm, sent = spoken("Hey Nova. Find me a laptop."), []

    async def send(msg):
        sent.append(msg)

    ctx = LLMContext()
    wake = make_wake(cfg.wake_phrases, 10, send)
    user, _ = LLMContextAggregatorPair(ctx, user_params=LLMUserAggregatorParams(
        vad_analyzer=SileroVADAnalyzer(),
        user_turn_strategies=UserTurnStrategies(start=[wake, *default_user_turn_start_strategies()])))
    frames = []
    for i in range(0, len(pcm), 640):  # 20 ms frames, paced like the live mic
        frames += [InputAudioRawFrame(audio=pcm[i:i + 640], sample_rate=16000, num_channels=1), SleepFrame(sleep=0.02)]
    asyncio.run(run_test(Pipeline([make_stt(cfg), user]), frames_to_send=[*frames, SleepFrame(sleep=2.0)]))

    heard = " ".join(m.get("content", "") for m in ctx.get_messages() if m.get("role") == "user").lower()
    assert {"type": "wake", "reason": "phrase"} in sent, f"{engine} never heard the wake phrase"
    assert "laptop" in heard, f"{engine} heard: {heard!r}"
