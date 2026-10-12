"""Voice agent: Parakeet (local) -> Gemini Flash -> Kokoro (local). Run: uv run bot.py"""

import asyncio
import os
from collections.abc import AsyncGenerator
from pathlib import Path

import numpy as np
import sherpa_onnx
from dotenv import load_dotenv
import logging

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
from pipecat.runner.run import app
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from backend import ClaudeCodeBackend
from config import load
from delegate import make_tools
from hooks import HookBridge, hide_hook_tokens, load_token, make_router, write_run_settings
from pipecat.services.google.llm import GoogleLLMService
from pipecat.services.kokoro.tts import KokoroTTSService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.stt_service import SegmentedSTTService
from pipecat.transports.base_transport import TransportParams
from pipecat.utils.time import time_now_iso8601
from registry import Registry
from relay import Relay, make_bridge_tools, start_session

load_dotenv()

PARAKEET_DIR = Path.home() / ".local/share/parakeet-dictation/models/desktop"
KOKORO_DIR = Path.home() / "kokoro"

cfg = load()
DATA = Path(cfg.data_dir).expanduser()
registry = Registry(DATA / "harness.db")
registry.expire_open()  # questions left open by a previous run can no longer be answered
relay = Relay(registry)
bridge = HookBridge(registry, relay, cfg.permission_timeout)
HOOK_TOKEN = load_token(DATA / "hook-token")
app.include_router(make_router(bridge, HOOK_TOKEN))  # hooks share the runner's app and port
logging.getLogger("uvicorn.access").addFilter(hide_hook_tokens)  # the token is in the URL

SYSTEM_PROMPT = (
    "You are a voice assistant. Your replies are spoken aloud, so use short, "
    "plain sentences with no markdown, lists, or emoji. Keep answers to one to "
    "three sentences unless asked for more. For anything that needs files, the "
    "shell, the web, or code, call delegate(task) and say one short line like "
    "'on it' first. If the task will create or change files and the user has not said where, "
    "ask which folder to use; if they do not care, call delegate without a directory and it "
    "works in a private default workspace. If a delegate result is a question from Claude, ask the user "
    "and call delegate again with their reply. When you are told a Claude session "
    "needs a decision, ask the user, then call answer_question after they answer. "
    "Use list_sessions to answer what Claude is doing and stop_session to stop one. "
    "Never claim a task is done unless a tool result says done."
)


def build_llm(cfg):
    key = os.environ.get(cfg.api_key_env, "ollama")
    if cfg.provider == "gemini":
        return GoogleLLMService(api_key=key, model=cfg.model)
    base = cfg.base_url or ("http://localhost:11434/v1" if cfg.provider == "ollama" else None)
    return OpenAILLMService(api_key=key, model=cfg.model, base_url=base)


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
    backend = ClaudeCodeBackend(cwd=cfg.backend_cwd, timeout=cfg.backend_timeout)
    llm = build_llm(cfg)
    tools = []
    if backend.available():
        settings_for = lambda sid: write_run_settings(DATA / "runs", sid, HOOK_TOKEN, cfg.hook_port)
        tools = [*make_tools(backend, registry, settings_for, on_finish=bridge.expire_session,
                             workspace_root=Path(cfg.workspace_dir).expanduser()),
                 *make_bridge_tools(bridge, relay, registry, backend)]
        for t in tools:
            # Spec: barge-in cancels speech, not the task. (Default True would cancel the handler.)
            llm.register_direct_function(t, cancel_on_interruption=False)
    else:
        logger.warning("claude CLI not found; running front-model-only")
    tts = KokoroTTSService(
        model_path=str(KOKORO_DIR / "kokoro-v1.0.onnx"),
        voices_path=str(KOKORO_DIR / "voices-v1.0.bin"),
        settings=KokoroTTSService.Settings(voice="af_heart"),
    )

    context = LLMContext([{"role": "system", "content": SYSTEM_PROMPT}], tools=tools)
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
        await start_session(relay, task, context)  # attaches the relay first, then greets

    @transport.event_handler("on_client_disconnected")
    async def on_disconnected(transport, client):
        relay.detach()
        await task.cancel()

    await PipelineRunner(handle_sigint=runner_args.handle_sigint).run(task)


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
