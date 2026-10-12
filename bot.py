"""Voice agent: Parakeet (local) -> Gemini Flash -> Kokoro (local). Run: uv run bot.py"""

import logging
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

from autonomy import sandbox_settings
from backend import ClaudeCodeBackend
from config import load
from delegate import make_tools
from hooks import HookBridge, hide_hook_tokens, load_token, make_router, write_run_settings
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.runner.run import app
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.kokoro.tts import KokoroTTSService
from pipecat.transports.base_transport import TransportParams
from pipecat.workers.runner import WorkerRunner
from registry import Registry
from relay import Relay, make_bridge_tools, start_session
from services import ParakeetSTTService, build_llm

load_dotenv()

KOKORO_DIR = Path.home() / "kokoro"

cfg = load()
DATA = Path(cfg.data_dir).expanduser()
registry = Registry(DATA / "harness.db")
registry.expire_open()  # questions left open by a previous run can no longer be answered
relay = Relay(registry)
bridge = HookBridge(registry, relay, cfg.permission_timeout)
HOOK_TOKEN = load_token(DATA / "hook-token")
# Documented extension point (Running Bots Locally > Adding your own routes): the runner exports
# its FastAPI app, so hooks share the voice page's process, event loop and port.
app.include_router(make_router(bridge, HOOK_TOKEN))
logging.getLogger("uvicorn.access").addFilter(hide_hook_tokens)  # the token is in the URL

SYSTEM_PROMPT = (
    "You are a voice-first personal agent that handles everyday errands for the user, like a "
    "concierge. Your replies are spoken aloud, so use short, plain sentences with no markdown, "
    "lists, or emoji; keep answers to one to three sentences unless asked for more. "
    "Your scope is errands only: researching and comparing options, planning and booking travel, "
    "restaurants and tickets, shopping and groceries, paying bills, cancelling subscriptions, "
    "reminders and follow-ups, and calling or messaging businesses. If asked for anything else, "
    "such as writing code or general computer work, say briefly that you only handle errands. "
    "For any errand that needs the web or an account, call delegate(task) and say one short line "
    "like 'on it' first. Never buy, pay, book, cancel, or change an account without the user's "
    "explicit go-ahead in this conversation, after saying what it is and how much it costs. "
    "If a delegate result is a question from the agent, such as a choice or a login code, ask the "
    "user and call delegate again with their reply. When you are told a session needs a decision, "
    "ask the user, then call answer_question after they answer. Use list_sessions to answer what "
    "you are working on and stop_session to stop one. Never claim a task is done unless a tool "
    "result says done."
)

transport_params = {
    "webrtc": lambda: TransportParams(audio_in_enabled=True, audio_out_enabled=True),
}


async def bot(runner_args: RunnerArguments):
    transport = await create_transport(runner_args, transport_params)

    stt = ParakeetSTTService()
    backend = ClaudeCodeBackend(cwd=cfg.backend_cwd, timeout=cfg.backend_timeout,
                                permission_mode=cfg.permission_mode)
    llm = build_llm(cfg, SYSTEM_PROMPT)
    tools = []
    if backend.available():
        autonomy = sandbox_settings(Path.home(), Path(__file__).parent, DATA)
        settings_for = lambda sid: write_run_settings(
            DATA / "runs", sid, HOOK_TOKEN, cfg.hook_port, extra=autonomy)
        # Tools declare their own options with @tool_options; LLMContext(tools=...) is the registration.
        tools = [*make_tools(backend, registry, settings_for, on_finish=bridge.expire_session,
                             workspace_root=Path(cfg.workspace_dir).expanduser()),
                 *make_bridge_tools(bridge, relay, registry, backend)]
    else:
        logger.warning("claude CLI not found; running front-model-only")
    tts = KokoroTTSService(
        model_path=str(KOKORO_DIR / "kokoro-v1.0.onnx"),
        voices_path=str(KOKORO_DIR / "voices-v1.0.bin"),
        settings=KokoroTTSService.Settings(voice="af_heart"),
    )

    context = LLMContext(tools=tools)
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        # Defaults: Smart Turn v3 stop strategy (Speech Input & Turn Detection > Best Practices).
        user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
    )

    pipeline = Pipeline(
        [transport.input(), stt, user_aggregator, llm, tts, transport.output(), assistant_aggregator]
    )
    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
    )
    runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)
    await runner.add_workers(worker)

    # Documented turn events (Turn Events): the relay speaks a pending Claude question only when
    # neither side is talking.
    @user_aggregator.event_handler("on_user_turn_started")
    async def on_user_turn_started(aggregator, strategy):
        relay.user_turn(True)

    @user_aggregator.event_handler("on_user_turn_stopped")
    async def on_user_turn_stopped(aggregator, strategy, message):
        relay.user_turn(False)

    @assistant_aggregator.event_handler("on_assistant_turn_started")
    async def on_assistant_turn_started(aggregator, *args):
        relay.bot_turn(True)

    @assistant_aggregator.event_handler("on_assistant_turn_stopped")
    async def on_assistant_turn_stopped(aggregator, *args):
        relay.bot_turn(False)

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        await start_session(relay, worker, context)  # attaches the relay first, then greets

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        await runner.cancel()

    @worker.event_handler("on_pipeline_finished")
    async def on_pipeline_finished(worker, frame):
        relay.detach()  # runs for graceful and cancelled shutdowns alike

    await runner.run()


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
