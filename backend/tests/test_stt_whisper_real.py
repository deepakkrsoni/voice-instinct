"""Whisper through the real model (run with -m claude): the sample recordings that ship with it are heard, and
a Kokoro "Nova, ..." matches the wake phrase after Whisper's text filter. Skipped without the model (./install.sh)."""

import asyncio
import wave
from pathlib import Path

import numpy as np
import pytest
import soxr

from config import Config
from pipecat.frames.frames import TranscriptionFrame
from services import kokoro_paths, make_stt
from wake import make_wake

pytestmark = pytest.mark.claude
MODELS = Path.home() / ".voice-instinct/models"
BASE = MODELS / "sherpa-onnx-whisper-base"
KOKORO = kokoro_paths(Config(models_dir=str(MODELS)))
CFG = Config(models_dir=str(MODELS), stt_engine="whisper-base")

needs_base = pytest.mark.skipif(not BASE.is_dir(), reason="whisper-base is not installed (./install.sh)")


def transcribe(stt, pcm: bytes) -> str:
    async def run():
        return [f.text async for f in stt.run_stt(pcm) if isinstance(f, TranscriptionFrame)]
    return " ".join(asyncio.run(run()))


@needs_base
def test_whisper_base_transcribes_the_sample_recordings():
    stt = make_stt(CFG)
    for path in sorted((BASE / "test_wavs").glob("*.wav")):
        with wave.open(str(path)) as w:
            samples = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
            rate = w.getframerate()
        pcm = soxr.resample(samples.astype(np.float32) / 32768, rate, 16000)
        text = transcribe(stt, (np.clip(pcm, -1, 1) * 32767).astype(np.int16).tobytes())
        print(path.name, repr(text))
        assert text.strip(), f"{path.name} gave no text"


@needs_base
@pytest.mark.skipif(not KOKORO[0].exists(), reason="Kokoro is not installed (./install.sh)")
def test_a_kokoro_nova_passes_the_wake_phrase_match():
    from kokoro_onnx import Kokoro
    samples, rate = Kokoro(str(KOKORO[0]), str(KOKORO[1])).create("Nova, what's on today?", voice="af_heart")
    pcm = np.concatenate([np.zeros(8000), soxr.resample(samples, rate, 16000), np.zeros(8000)])
    text = transcribe(make_stt(CFG), (np.clip(pcm, -1, 1) * 32767).astype(np.int16).tobytes())
    print("heard", repr(text))

    async def send(msg):
        pass

    assert make_wake(CFG.wake_phrases, 10, send).would_wake(text), f"whisper-base heard: {text!r}"
