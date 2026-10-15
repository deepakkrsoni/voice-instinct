"""#32 with the real CAM++ model: enrol one Kokoro voice, then that voice passes and Milo's own voice
(af_heart) and another person's (bf_emma) don't. No human recordings live in the repo.

Kokoro's voices share one vocoder, so they sit much closer together than people do: measured 2026-10-15,
am_adam 0.36 against af_heart 0.31 and bf_emma 0.27 (the owner's real voice in the #32 spike: 0.46 and up,
other people at most 0.18). So this pins the ranking and the rejections, not the owner's margin."""

from pathlib import Path

import numpy as np
import pytest
import soxr

from config import Config
from services import kokoro_paths
from voiceprint import SPEAKER_MODEL, Voiceprint, embed_with

MODELS = Path.home() / ".voice-instinct/models"
KOKORO = kokoro_paths(Config(models_dir=str(MODELS)))
pytestmark = pytest.mark.skipif(not (MODELS / SPEAKER_MODEL).exists() or not KOKORO[0].exists(),
                                reason="speaker model or Kokoro not installed")


def say(text, voice):
    from kokoro_onnx import Kokoro
    samples, rate = Kokoro(str(KOKORO[0]), str(KOKORO[1])).create(text, voice=voice)
    return (np.clip(soxr.resample(samples, rate, 16000), -1, 1) * 32767).astype(np.int16).tobytes()


def test_the_enrolled_voice_passes_and_others_do_not():
    embed = embed_with(MODELS / SPEAKER_MODEL)
    lines = ["Hey Milo", "Book a table for two at eight tonight.", "Find me a cheap flight to Bangalore."]
    v = np.mean([embed(say(t, "am_adam")) for t in lines], axis=0)
    vp = Voiceprint(v / np.linalg.norm(v), 0.35, embed)
    test = "Hey Milo, remind me to call mom at six."
    scores = {voice: vp.score(say(test, voice)) for voice in ("am_adam", "af_heart", "bf_emma")}
    assert scores["am_adam"] > max(scores["af_heart"], scores["bf_emma"]), scores
    assert not vp.is_owner(say(test, "af_heart")) and not vp.is_owner(say(test, "bf_emma")), scores
