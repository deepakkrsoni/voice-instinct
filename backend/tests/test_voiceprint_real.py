"""#32 with the real TitaNet-small model, as enroll.py and the check use it: enrol one Kokoro voice on the
five wake-phrase lines, then that voice's "Hey Milo" scores above Milo's own voice (af_heart) and another
person's (bf_emma), and Milo's own voice is rejected. No human recordings live in the repo.
Measured 2026-10-15: am_adam 0.84, af_heart -0.02, bf_emma -0.01 (cut-off 0.6).

Kokoro's voices share one vocoder, so they sit closer together than people do; the owner's real voice in
spike 2 (#32) scored 0.62 and up against the loudest other voice's 0.59. This pins ranking and rejection."""

from pathlib import Path

import numpy as np
import pytest
import soxr

from config import Config
from enrolment import LINES
from services import kokoro_paths
from voiceprint import SPEAKER_MODEL, Voiceprint, build_vector, cohort_mean, embed_with, speech_only

MODELS = Path.home() / ".voice-instinct/models"
KOKORO = kokoro_paths(Config(models_dir=str(MODELS)))
pytestmark = pytest.mark.skipif(not (MODELS / SPEAKER_MODEL).exists() or not KOKORO[0].exists(),
                                reason="speaker model or Kokoro not installed")


def say(text, voice):
    from kokoro_onnx import Kokoro
    samples, rate = Kokoro(str(KOKORO[0]), str(KOKORO[1])).create(text, voice=voice)
    return (np.clip(soxr.resample(samples, rate, 16000), -1, 1) * 32767).astype(np.int16).tobytes()


def test_the_enrolled_voice_wakes_it_and_milos_own_voice_does_not():
    embed = embed_with(MODELS / SPEAKER_MODEL)
    mean = cohort_mean(embed, KOKORO)
    takes = [embed(speech_only(say(line.format(name="Milo"), "am_adam"))) for line in LINES]
    vp = Voiceprint(build_vector(takes, mean), mean, 0.6, embed)
    scores = {v: vp.score(say("Hey Milo, find me a flight.", v)) for v in ("am_adam", "af_heart", "bf_emma")}
    assert scores["am_adam"] >= 0.6 and scores["am_adam"] > max(scores["af_heart"], scores["bf_emma"]), scores
    assert not vp.is_owner(say("Hey Milo, find me a flight.", "af_heart")), scores
