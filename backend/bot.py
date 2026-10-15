"""Voice agent: Parakeet (local) -> Gemini Flash -> Kokoro (local). Run: uv run bot.py"""

import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

import json

from autonomy import browser_mcp, sandbox_settings, with_debug_record
from budget import TimeBudget
from claude_cli import ClaudeCodeBackend
from config import REPO_ROOT, load
from delegate import make_tools
from gui import make_gui_router
from guard import refuse_other_origins
from hooks import HookBridge, hide_hook_tokens, load_token, make_router, write_run_settings
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.frameworks.rtvi.observer import RTVIObserverParams
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from conversation import ASSISTANT_PARAMS, make_context, save as save_conversation
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
from pipecat.utils.text.markdown_text_filter import MarkdownTextFilter
from pipecat.workers.runner import WorkerRunner
from registry import Registry
from relay import Relay, make_bridge_tools, start_session
from services import build_llm, kokoro_paths, make_stt
from shutdown import cancel_on_exit
from steps import StepHub
from telegram import Telegram
from enrolment import Enrolment, make_enrol_router
from voiceprint import Voiceprint, cohort_mean, learned_wake_phrases
from wake import handle_client_message, make_dismiss, make_wake

load_dotenv(REPO_ROOT / ".env")


def _quiet_logs():
    """The journal keeps everything, and at DEBUG Pipecat logs whole LLM contexts (what the user said).
    The runner's main() resets loguru to DEBUG after this module loads, so this runs per session."""
    logger.remove()
    logger.add(sys.stderr, level=os.environ.get("VI_LOG_LEVEL", "INFO"))  # VI_LOG_LEVEL=DEBUG to debug

cfg = load()
VOICEPRINT = Voiceprint.load(cfg)  # #32: no file means everyone is heard, as before enrolment
if VOICEPRINT.enrolled and cfg.stt_engine != "parakeet":  # Nemotron streams, with no segment to check
    logger.warning('voice check off: it needs [stt] engine = "parakeet"')
    VOICEPRINT = Voiceprint(None, None, cfg.voice_threshold)
DATA = Path(cfg.data_dir).expanduser()
registry = Registry(DATA / "harness.db")
registry.expire_open()  # questions left open by a previous run can no longer be answered
sessions = set()  # open sessions' runners, cancelled when the server is stopped
relay = Relay(registry)
bridge = HookBridge(registry, relay, cfg.permission_timeout)
HOOK_TOKEN = load_token(DATA / "hook-token")
WAKES = set()  # open sessions' wake strategies, told about spellings learned at enrolment (#32)
ENROLMENT = Enrolment(cfg, VOICEPRINT, embed=VOICEPRINT.embed,
                      cohort=lambda: cohort_mean(VOICEPRINT.embed, kokoro_paths(cfg)),
                      on_learned=lambda phrases: [w.add_phrases(phrases) for w in WAKES])
# Debug mode (config [debug] browser_steps): every errand browser step screenshotted (steps.py).
STEPS = StepHub(Path.home(), telegram=Telegram.from_env()) if cfg.debug_browser_steps else None
# Documented extension point (Running Bots Locally > Adding your own routes): the runner exports
# its FastAPI app, so hooks share the voice page's process, event loop and port.
BUDGET = TimeBudget(min_notice=cfg.permission_timeout + 30)  # errands are told when nearly out of time
app.include_router(make_router(bridge, HOOK_TOKEN, steps=STEPS, budget=BUDGET))
app.include_router(make_gui_router(cfg))
app.include_router(make_enrol_router(ENROLMENT, HOOK_TOKEN))  # enroll.py: owner enrolment through Milo (#32)
refuse_other_origins(app, port=7860)  # no other web page may open a voice session (guard.py)
logging.getLogger("uvicorn.access").addFilter(hide_hook_tokens)  # the token is in the URL
logging.getLogger("phonemizer").setLevel(logging.ERROR)  # Kokoro's espeak warns on every number; noise

SYSTEM_PROMPT = (
    "You are a voice-first personal agent that handles everyday errands for the user, like a "
    "concierge. Your replies are spoken aloud, so use short, plain sentences with no markdown, "
    "lists, or emoji; keep answers to one to three sentences unless asked for more. "
    "Your work is errands: researching and comparing options, planning and booking travel, "
    "restaurants and tickets, shopping and groceries, paying bills, cancelling subscriptions, "
    "reminders and follow-ups, and calling or messaging businesses. You can also chat and answer quick "
    "questions from what you know, like a fun fact, a definition or a quick calculation. Only for "
    "work outside errands, such as writing code or general computer work, say briefly that you "
    "only handle errands. "
    "For any errand that needs the web or an account, call delegate(task) and say one short line "
    "like 'on it' first. Never buy, pay, book, cancel, or change an account without the user's "
    "explicit go-ahead in this conversation, after saying what it is and how much it costs. "
    "If a delegate result is a question from the agent, such as a choice, ask the "
    "user and call delegate again with their reply and that result's errand id. For any follow-up "
    "about an earlier errand (check again, refine it, what about those), call delegate with its "
    "errand id; for a new topic leave errand out. When you are told a session needs a decision, "
    "ask the user, then call answer_question after they answer. Use list_sessions to answer what "
    "you are working on and stop_session to stop one. "
    "Whenever the user signals they are done for now, in any words (bye, bye bye, goodbye, stop, stop for "
    "now, that's all, thanks that's it, go away, later), say one short goodbye and call dismiss: "
    "that ends the conversation and never stops an errand. Use stop_session only when they ask to stop "
    "a particular errand. Never claim a task is done unless a tool "
    "result says done."
)

def _browser_config():
    """The errand browser's MCP config as inline JSON (--mcp-config takes it), or None if install.sh
    has not set the browser up."""
    servers = browser_mcp(Path.home())
    return None if servers is None else json.dumps(servers)


HOOK_PORT = 7860  # the runner's port: hooks post back to this same server

NO_AGENT = (
    " Right now Claude Code is not available on this computer, so you cannot run "
    "errands. If the user asks for one, say that Claude Code needs to be installed and logged in "
    "first (run claude once in a terminal), then restart voice-instinct."
)

transport_params = {
    "webrtc": lambda: TransportParams(audio_in_enabled=True, audio_out_enabled=True),
}


async def bot(runner_args: RunnerArguments):
    _quiet_logs()
    transport = await create_transport(runner_args, transport_params)

    backend = ClaudeCodeBackend(cmd=os.environ.get("VI_CLAUDE", "claude"), cwd=cfg.backend_cwd,
                                timeout=cfg.backend_timeout, permission_mode=cfg.permission_mode,
                                hide_env=(cfg.api_key_env, "TELEGRAM_CHAT_ID"),  # the bot token goes as *_TOKEN
                                mcp_config=_browser_config())
    prompt = f"Your name is {cfg.name}. " + SYSTEM_PROMPT  # set in config.toml; never from history
    llm = build_llm(cfg, prompt if backend.available() else prompt + NO_AGENT)
    holder = {}

    async def send_gui(msg):
        if "worker" in holder:
            await holder["worker"].rtvi.send_server_message(msg)

    wake = make_wake(cfg.wake_phrases + learned_wake_phrases(cfg), cfg.awake_timeout, send_gui,
                     hold=lambda: relay.asking)
    WAKES.add(wake)
    # As Siri does: only a wake phrase said while asleep is voice-checked (#32)
    stt = make_stt(cfg, voiceprint=VOICEPRINT, enrolment=ENROLMENT, needs_check=wake.would_wake)

    tools = [make_dismiss(wake)]  # "stop" works with or without Claude (#32)
    if backend.available():
        autonomy = sandbox_settings(Path.home(), REPO_ROOT, DATA, hook_port=HOOK_PORT)
        settings_for = lambda sid, debug_cwd=None: write_run_settings(
            DATA / "runs", sid, HOOK_TOKEN, HOOK_PORT,
            extra=autonomy if debug_cwd is None else with_debug_record(autonomy, debug_cwd, Path.home()),
            hook_timeout=int(cfg.permission_timeout) + 10)  # Claude must outwait our own question timeout
        # Tools declare their own options with @tool_options; the context's tools (make_context) are the registration.
        tools += [*make_tools(backend, registry, settings_for, on_finish=bridge.expire_session,
                             workspace_root=Path(cfg.workspace_dir).expanduser(), steps=STEPS, budget=BUDGET),
                 *make_bridge_tools(bridge, relay, registry, backend)]
    else:
        logger.warning("claude CLI not found; running front-model-only")
    model_path, voices_path = kokoro_paths(cfg)
    tts = KokoroTTSService(
        model_path=str(model_path),
        voices_path=str(voices_path),
        settings=KokoroTTSService.Settings(voice="af_heart"),
    )
    # Documented TTS text transformer: never read markdown aloud (Claude's results arrive as tables).
    markdown = MarkdownTextFilter()
    tts.add_text_transformer(lambda text, _type: markdown.filter(text), "*")

    context = make_context(registry, tools)  # the one ongoing conversation, from where it left off (#24)
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        # Defaults: Smart Turn v3 stop strategy (Speech Input & Turn Detection > Best Practices).
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(),
            # Documented: a wake phrase gates every turn; the defaults follow it.
            user_turn_strategies=UserTurnStrategies(start=[wake, *default_user_turn_start_strategies()]),
        ),
        assistant_params=ASSISTANT_PARAMS,  # older turns are summarized, so it stays bounded
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
        # Documented RTVI observer params: the orb never needs what was said, and before the wake
        # phrase that is the room's conversation; keep it off the data channel too.
        rtvi_observer_params=RTVIObserverParams(user_transcription_enabled=False, user_llm_enabled=False),
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
        wake.assistant_turn_done()  # the user can reply without the phrase, unless they said stop
        save_conversation(registry, context)

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
        save_conversation(registry, context)
        relay.detach(worker)  # graceful and cancelled alike; never a newer session's relay

    sessions.add(runner)
    cancel_on_exit(sessions)
    try:
        await runner.run()
    finally:
        sessions.discard(runner)
        WAKES.discard(wake)


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
