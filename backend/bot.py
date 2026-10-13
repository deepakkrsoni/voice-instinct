"""Voice agent: Parakeet (local) -> Gemini Flash -> Kokoro (local). Run: uv run bot.py"""

import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

from autonomy import sandbox_settings
from claude_cli import ClaudeCodeBackend
from config import REPO_ROOT, load
from delegate import make_tools
from gui import make_gui_router
from hooks import HookBridge, hide_hook_tokens, load_token, make_router, write_run_settings
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.runner.run import app
from pipecat.turns.user_turn_strategies import UserTurnStrategies, default_user_turn_start_strategies
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.kokoro.tts import KokoroTTSService
from pipecat.transports.base_transport import TransportParams
from pipecat.workers.runner import WorkerRunner
from registry import Registry
from relay import Relay, make_bridge_tools, start_session
from services import ParakeetSTTService, build_llm, kokoro_paths, parakeet_dir
from shutdown import cancel_on_exit
from wake import handle_client_message, make_wake

load_dotenv(REPO_ROOT / ".env")


def _quiet_logs():
    """The journal keeps everything, and at DEBUG Pipecat logs whole LLM contexts (what the user said).
    The runner's main() resets loguru to DEBUG after this module loads, so this runs per session."""
    logger.remove()
    logger.add(sys.stderr, level=os.environ.get("VI_LOG_LEVEL", "INFO"))  # VI_LOG_LEVEL=DEBUG to debug

cfg = load()
DATA = Path(cfg.data_dir).expanduser()
registry = Registry(DATA / "harness.db")
registry.expire_open()  # questions left open by a previous run can no longer be answered
sessions = set()  # open sessions' runners, cancelled when the server is stopped
relay = Relay(registry)
bridge = HookBridge(registry, relay, cfg.permission_timeout)
HOOK_TOKEN = load_token(DATA / "hook-token")
# Documented extension point (Running Bots Locally > Adding your own routes): the runner exports
# its FastAPI app, so hooks share the voice page's process, event loop and port.
app.include_router(make_router(bridge, HOOK_TOKEN))
app.include_router(make_gui_router(cfg))
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

HOOK_PORT = 7860  # the runner's port: hooks post back to this same server

NO_AGENT = (
    " Right now Claude Code is not available on this computer, so you have no tools and cannot run "
    "errands. If the user asks for one, say that Claude Code needs to be installed and logged in "
    "first (run claude once in a terminal), then restart voice-instinct."
)

transport_params = {
    "webrtc": lambda: TransportParams(audio_in_enabled=True, audio_out_enabled=True),
}


async def bot(runner_args: RunnerArguments):
    _quiet_logs()
    transport = await create_transport(runner_args, transport_params)

    stt = ParakeetSTTService(model_dir=parakeet_dir(cfg))
    backend = ClaudeCodeBackend(cmd=os.environ.get("VI_CLAUDE", "claude"), cwd=cfg.backend_cwd,
                                timeout=cfg.backend_timeout, permission_mode=cfg.permission_mode,
                                hide_env=(cfg.api_key_env,))
    llm = build_llm(cfg, SYSTEM_PROMPT if backend.available() else SYSTEM_PROMPT + NO_AGENT)
    tools = []
    if backend.available():
        autonomy = sandbox_settings(Path.home(), REPO_ROOT, DATA)
        settings_for = lambda sid: write_run_settings(
            DATA / "runs", sid, HOOK_TOKEN, HOOK_PORT, extra=autonomy,
            hook_timeout=int(cfg.permission_timeout) + 10)  # Claude must outwait our own question timeout
        # Tools declare their own options with @tool_options; LLMContext(tools=...) is the registration.
        tools = [*make_tools(backend, registry, settings_for, on_finish=bridge.expire_session,
                             workspace_root=Path(cfg.workspace_dir).expanduser()),
                 *make_bridge_tools(bridge, relay, registry, backend)]
    else:
        logger.warning("claude CLI not found; running front-model-only")
    model_path, voices_path = kokoro_paths(cfg)
    tts = KokoroTTSService(
        model_path=str(model_path),
        voices_path=str(voices_path),
        settings=KokoroTTSService.Settings(voice="af_heart"),
    )

    holder = {}

    async def send_gui(msg):
        if "worker" in holder:
            await holder["worker"].rtvi.send_server_message(msg)

    wake = make_wake(cfg.wake_phrases, cfg.awake_timeout, send_gui, hold=lambda: relay.asking)

    context = LLMContext(tools=tools)
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        # Defaults: Smart Turn v3 stop strategy (Speech Input & Turn Detection > Best Practices).
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(),
            # Documented: a wake phrase gates every turn; the defaults follow it.
            user_turn_strategies=UserTurnStrategies(start=[wake, *default_user_turn_start_strategies()]),
        ),
    )

    pipeline = Pipeline(
        [transport.input(), stt, user_aggregator, llm, tts, transport.output(), assistant_aggregator]
    )
    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        # Documented default is 5 min, then the session is cancelled: that would kill a running errand
        # and drop "Hey Nova" every few minutes. The app stays connected until it disconnects.
        idle_timeout_secs=None,
    )
    holder["worker"] = worker
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
        wake.wake("reply")  # an errand's result or a note: show the orb and listen for the answer

    @assistant_aggregator.event_handler("on_assistant_turn_stopped")
    async def on_assistant_turn_stopped(aggregator, *args):
        relay.bot_turn(False)
        wake.keep_awake()  # the user can reply without the phrase

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        await start_session(relay, worker, context, lambda: wake.wake("question"))  # no greeting

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        await runner.cancel()

    @worker.rtvi.event_handler("on_client_message")
    async def on_client_message(rtvi, msg):
        handle_client_message(wake, msg)  # documented RTVI client message, sent when the orb is shown

    # Documented (Pipeline events): an error reaching the worker. A failed model call is otherwise
    # silent: the orb thinks, then nothing, so say so.
    @worker.event_handler("on_pipeline_error")
    async def on_pipeline_error(worker, frame):
        if frame.processor is llm:
            await worker.queue_frames([TTSSpeakFrame("Sorry, I can't reach my language model right now.")])

    @worker.event_handler("on_pipeline_finished")
    async def on_pipeline_finished(worker, frame):
        relay.detach(worker)  # graceful and cancelled alike; never a newer session's relay

    sessions.add(runner)
    cancel_on_exit(sessions)
    try:
        await runner.run()
    finally:
        sessions.discard(runner)


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
