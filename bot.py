"""Voice agent: Parakeet (local) -> Gemini Flash -> Kokoro (local). Run: uv run bot.py"""

import asyncio
import os
from collections.abc import AsyncGenerator
from pathlib import Path

import numpy as np
import sherpa_onnx
from dotenv import load_dotenv
from loguru import logger

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import Frame, TranscriptionFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.google.llm import GoogleLLMService
from pipecat.services.kokoro.tts import KokoroTTSService
from pipecat.services.stt_service import SegmentedSTTService
from pipecat.transports.base_transport import TransportParams
from pipecat.utils.time import time_now_iso8601

load_dotenv()

PARAKEET_DIR = Path.home() / ".local/share/parakeet-dictation/models/desktop"
KOKORO_DIR = Path.home() / "kokoro"
GEMINI_MODEL = "gemini-3.8-flash"  # newest Flash on the live models list, 2026-10-11

SYSTEM_PROMPT = (
    "You are a voice assistant. Your replies are spoken aloud, so use short, "
    "plain sentences with no markdown, lists, or emoji. Keep answers to one to "
    "three sentences unless asked for more."
)


class ParakeetSTTService(SegmentedSTTService):
    """Parakeet TDT 0.6B v3 via sherpa-onnx; transcribes each VAD segment."""

    # ponytail: offline model, transcribes after VAD stop; swap for the streaming
    # cache-aware model if STT latency dominates.
    wants_wav_segments = False

    def __init__(self, **kwargs):
        super().__init__(sample_rate=16000, **kwargs)
        self._rec = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=str(PARAKEET_DIR / "encoder.int8.onnx"),
            decoder=str(PARAKEET_DIR / "decoder.int8.onnx"),
            joiner=str(PARAKEET_DIR / "joiner.int8.onnx"),
            tokens=str(PARAKEET_DIR / "tokens.txt"),
            num_threads=4,
            model_type="nemo_transducer",
        )

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


transport_params = {
    "webrtc": lambda: TransportParams(audio_in_enabled=True, audio_out_enabled=True),
}


async def bot(runner_args: RunnerArguments):
    transport = await create_transport(runner_args, transport_params)

    stt = ParakeetSTTService()
    llm = GoogleLLMService(api_key=os.environ["GEMINI_API_KEY"], model=GEMINI_MODEL)
    tts = KokoroTTSService(
        model_path=str(KOKORO_DIR / "kokoro-v1.0.onnx"),
        voices_path=str(KOKORO_DIR / "voices-v1.0.bin"),
        settings=KokoroTTSService.Settings(voice="af_heart"),
    )

    context = LLMContext([{"role": "system", "content": SYSTEM_PROMPT}])
    user_agg, assistant_agg = LLMContextAggregatorPair(
        context,
        # Default turn stop strategy is Smart Turn v3 (local).
        user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
    )

    pipeline = Pipeline(
        [transport.input(), stt, user_agg, llm, tts, transport.output(), assistant_agg]
    )
    task = PipelineTask(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
    )

    @transport.event_handler("on_client_connected")
    async def on_connected(transport, client):
        context.add_message({"role": "developer", "content": "Greet the user in one short sentence."})
        await task.queue_frames([user_agg.get_context_frame()])

    @transport.event_handler("on_client_disconnected")
    async def on_disconnected(transport, client):
        await task.cancel()

    await PipelineRunner(handle_sigint=runner_args.handle_sigint).run(task)


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
