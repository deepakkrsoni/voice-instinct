"""Pipecat services: local Parakeet STT (no built-in one exists) and the front-LLM builder."""

import asyncio
import os
from collections.abc import AsyncGenerator
from pathlib import Path

import numpy as np
from loguru import logger

from pipecat.frames.frames import Frame, TranscriptionFrame
from pipecat.services.google.llm import GoogleLLMService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.settings import STTSettings
from pipecat.services.stt_service import SegmentedSTTService
from pipecat.transcriptions.language import Language
from pipecat.utils.time import time_now_iso8601

PARAKEET_NAME = "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8"


def parakeet_dir(cfg) -> Path:
    return Path(cfg.models_dir).expanduser() / PARAKEET_NAME


def kokoro_paths(cfg) -> tuple[Path, Path]:
    d = Path(cfg.models_dir).expanduser() / "kokoro"
    return d / "kokoro-v1.0.onnx", d / "voices-v1.0.bin"


def _load_recognizer(model_dir: Path):
    import sherpa_onnx

    return sherpa_onnx.OfflineRecognizer.from_transducer(
        encoder=str(model_dir / "encoder.int8.onnx"),
        decoder=str(model_dir / "decoder.int8.onnx"),
        joiner=str(model_dir / "joiner.int8.onnx"),
        tokens=str(model_dir / "tokens.txt"),
        num_threads=4,
        model_type="nemo_transducer",
    )


class ParakeetSTTService(SegmentedSTTService):
    """Parakeet TDT 0.6B v3 via sherpa-onnx; transcribes each VAD segment.

    Pipecat has no sherpa-onnx or local Parakeet service (its local STT options are Whisper and
    Moonshine), so this subclasses SegmentedSTTService. `wants_wav_segments = False` is the
    source-documented way to receive raw 16-bit PCM instead of a WAV container.
    """

    # ponytail: offline model, transcribes after VAD stop; swap for a streaming
    # cache-aware model if STT latency dominates.
    wants_wav_segments = False

    def __init__(self, model_dir: Path | None = None, recognizer=None, **kwargs):
        super().__init__(
            sample_rate=16000,
            settings=STTSettings(model="parakeet-tdt-0.6b-v3", language=Language.EN),
            **kwargs,
        )
        self._rec = recognizer or _load_recognizer(model_dir)

    def can_generate_metrics(self) -> bool:
        return True

    def _decode(self, pcm: bytes) -> str:
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        stream = self._rec.create_stream()
        stream.accept_waveform(16000, audio)
        self._rec.decode_stream(stream)
        return stream.result.text.strip()

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame, None]:
        await self.start_processing_metrics()
        text = await asyncio.to_thread(self._decode, audio)
        await self.stop_processing_metrics()
        if text:
            logger.info(f"User: {text}")
            yield TranscriptionFrame(text, self._user_id, time_now_iso8601())


def build_llm(cfg, system_instruction: str):
    """Front LLM. Model and system prompt go through Settings (the documented 1.x way)."""
    key = os.environ.get(cfg.api_key_env, "ollama")
    if cfg.provider == "gemini":
        return GoogleLLMService(
            api_key=key,
            settings=GoogleLLMService.Settings(model=cfg.model, system_instruction=system_instruction),
        )
    base = cfg.base_url or ("http://localhost:11434/v1" if cfg.provider == "ollama" else None)
    return OpenAILLMService(
        api_key=key,
        base_url=base,
        settings=OpenAILLMService.Settings(model=cfg.model, system_instruction=system_instruction),
    )
