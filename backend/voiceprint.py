"""The owner's voice (#32): a CAM++ speaker embedding saved by enroll.py, checked on every segment and
before every interruption. No voiceprint file means the check is off and every voice is accepted."""

import asyncio
import functools
import json
import os
import re
from pathlib import Path

import numpy as np
from loguru import logger

from pipecat.frames.frames import InputAudioRawFrame, VADUserStartedSpeakingFrame, VADUserStoppedSpeakingFrame
from pipecat.turns.types import ProcessFrameResult
from pipecat.turns.user_start.base_user_turn_start_strategy import BaseUserTurnStartStrategy

SPEAKER_MODEL = "3dspeaker_speech_campplus_sv_en_voxceleb_16k.onnx"  # spike #32: 6 ms a clip, 0 errors
WAKE_PREFIXES = ("hey", "hay", "okay", "ok", "o k")  # as Config.wake_phrases, plus Parakeet's "O K"


def voiceprint_path(cfg) -> Path:
    return Path(cfg.data_dir).expanduser() / "voiceprint.npy"


def learned_path(cfg) -> Path:
    return Path(cfg.data_dir).expanduser() / "learned_wake.json"


def speaker_model_path(cfg) -> Path:
    return Path(cfg.models_dir).expanduser() / SPEAKER_MODEL


@functools.cache  # one copy for every session, like the recognisers
def _extractor(model_path: Path):
    import sherpa_onnx

    return sherpa_onnx.SpeakerEmbeddingExtractor(
        sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(model_path), num_threads=2))


def embed_with(model_path: Path):
    def embed(pcm: bytes, rate: int = 16000) -> np.ndarray:
        ex = _extractor(model_path)
        s = ex.create_stream()
        s.accept_waveform(rate, np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0)
        s.input_finished()
        v = np.array(ex.compute(s))
        return v / np.linalg.norm(v)
    return embed


class Voiceprint:
    def __init__(self, vector, threshold: float, embed=None):
        self.vector, self.threshold, self._embed = vector, threshold, embed

    @property
    def enrolled(self) -> bool:
        return self.vector is not None

    @classmethod
    def load(cls, cfg, embed=None) -> "Voiceprint":
        path = voiceprint_path(cfg)
        if not path.exists():
            logger.info("voice check off: run `uv run enroll.py` so only your voice is heard")
            return cls(None, cfg.voice_threshold)
        if embed is None:
            model = speaker_model_path(cfg)
            if not model.exists():
                raise RuntimeError(f"The speaker model is not installed ({model}). Run ./install.sh.")
            embed = embed_with(model)
        return cls(np.load(path), cfg.voice_threshold, embed)

    def score(self, pcm: bytes, rate: int = 16000) -> float:
        return float(self._embed(pcm, rate) @ self.vector)

    def is_owner(self, pcm: bytes, rate: int = 16000) -> bool:
        if not self.enrolled:
            return True
        s = self.score(pcm, rate)
        logger.debug(f"voice check {s:.2f}: {'owner' if s >= self.threshold else 'not the owner'}")
        return s >= self.threshold


def save_voiceprint(cfg, vectors) -> None:
    v = np.mean(vectors, axis=0)
    path = voiceprint_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as f:
        np.save(f, v / np.linalg.norm(v))
    os.chmod(path, 0o600)  # an existing file keeps its old mode otherwise


def learn_spellings(transcripts, name: str) -> list[str]:
    """How Parakeet wrote the owner's wake phrase, kept only as a prefix plus exactly one word
    ("hey mailo"). Two-word spellings ("hey my luck") are where ordinary sentences live."""
    # ponytail: one-word rule only, a real word could slip in ("hey mile"); enroll.py prints what it learned.
    name = " ".join(name.lower().split())
    out = []
    for t in transcripts:
        words = re.sub(r"[^\w\s]", "", t.lower()).split()
        for prefix in WAKE_PREFIXES:
            p = prefix.split()
            if words[: len(p)] == p and len(words) == len(p) + 1:
                word = words[-1]
                phrase = f"{prefix} {word}"
                if word != name and len(word) >= 3 and phrase not in out:
                    out.append(phrase)
                break
    return out


def learned_wake_phrases(cfg) -> list[str]:
    path = learned_path(cfg)
    if not path.exists():
        return []
    try:
        phrases = json.loads(path.read_text())
        return [p for p in phrases if isinstance(p, str) and p.strip()]
    except (ValueError, TypeError):
        logger.warning(f"{path} is not a JSON list of phrases; ignoring it (run enroll.py again)")
        return []


class OwnerVADUserTurnStartStrategy(BaseUserTurnStartStrategy):
    """Pipecat's VADUserTurnStartStrategy, but the turn (and so the interruption) starts only once the
    first CHECK_SECS of speech is the owner's. The user aggregator feeds every frame, audio included, to
    its start strategies (pinned by test_owner_start against Pipecat 1.12)."""

    CHECK_SECS = 1.0  # spike #32: others' 1 s windows scored at most 0.15
    MIN_SECS = 0.4  # shorter than this is left to the transcript check

    def __init__(self, voiceprint, **kwargs):
        super().__init__(**kwargs)
        self._voiceprint = voiceprint
        self._buf: bytearray | None = None  # collecting since the last VAD start
        self._rate = 16000

    async def process_frame(self, frame) -> ProcessFrameResult:
        await super().process_frame(frame)
        if isinstance(frame, VADUserStartedSpeakingFrame):
            self._buf = bytearray()
        elif isinstance(frame, InputAudioRawFrame) and self._buf is not None:
            self._buf += frame.audio
            self._rate = frame.sample_rate
            if len(self._buf) >= self.CHECK_SECS * self._rate * 2:
                return await self._decide()
        elif isinstance(frame, VADUserStoppedSpeakingFrame) and self._buf is not None:
            if len(self._buf) >= self.MIN_SECS * self._rate * 2:
                return await self._decide()
            self._buf = None
        return ProcessFrameResult.CONTINUE

    async def _decide(self) -> ProcessFrameResult:
        pcm, self._buf = bytes(self._buf), None
        if await asyncio.to_thread(self._voiceprint.is_owner, pcm, self._rate):
            await self.trigger_user_turn_started()
            return ProcessFrameResult.STOP
        return ProcessFrameResult.CONTINUE
