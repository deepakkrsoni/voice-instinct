"""#24 with the real models (run with -m claude): a restored conversation is accepted by Gemini, and an errand
continued after a restart resumes the same Claude session and remembers what it did."""

import asyncio
import json
import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

from claude_cli import ClaudeCodeBackend
from config import Config
from delegate import make_tools
from pipecat.frames.frames import ErrorFrame, FunctionCallsStartedFrame, LLMContextFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.tests.utils import run_test
from registry import Registry
from services import build_llm

pytestmark = pytest.mark.claude
load_dotenv(Path.home() / "voice-instinct/.env")


async def _delegate(params, task: str, directory: str = "", errand: int = 0):
    """Hand an errand to the backend agent.

    Args:
        task: What to do.
        directory: Folder, if named.
        errand: Id of an earlier errand to continue, else 0.
    """


@pytest.mark.skipif(not os.environ.get("GEMINI_API_KEY"), reason="no GEMINI_API_KEY")
def test_gemini_accepts_a_restored_conversation_and_continues_the_errand():
    from conversation import restorable
    saved = restorable([
        {"role": "user", "content": "Find me cotton t-shirts under 500 rupees."},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {
            "name": "delegate", "arguments": json.dumps({"task": "Find cotton t-shirts under 500 rupees"})}}]},
        {"role": "tool", "tool_call_id": "c1", "content": json.dumps({"status": "done", "errand": 10,
                                                                      "result": "Levi's 376, Lux Cozi 259"})},
        {"role": "assistant", "content": "Levi's for 376 rupees and Lux Cozi for 259."},
        {"role": "user", "content": "Which of those had the best rating? Check again."}])
    llm = build_llm(Config(), "For a follow-up on an earlier errand, call delegate with its errand id.")
    llm.register_direct_function(_delegate)
    down, up = asyncio.run(run_test(llm, frames_to_send=[LLMContextFrame(LLMContext(messages=saved, tools=[_delegate]))]))
    assert not [f for f in up + down if isinstance(f, ErrorFrame)]
    (call,) = [f for f in down if isinstance(f, FunctionCallsStartedFrame)]
    assert call.function_calls[0].arguments.get("errand") == 10


def test_an_errand_continued_after_a_restart_resumes_the_same_claude_session(tmp_path):
    import shutil
    import tempfile

    db, out = tmp_path / "harness.db", []
    # Errands only run in folders inside the user's home (workspace.resolve_workdir), so not in /tmp.
    ws = Path(tempfile.mkdtemp(prefix="vi-test-continuity-", dir=Path.home() / ".voice-instinct"))

    async def cb(r, properties=None, **kw):
        out.append(r)

    params = type("P", (), {"result_callback": staticmethod(cb)})
    backend = ClaudeCodeBackend(cmd=os.environ.get("VI_CLAUDE", "claude"), timeout=120, permission_mode="auto")
    try:
        (delegate,) = make_tools(backend, Registry(db), lambda sid: None, workspace_root=ws)
        asyncio.run(delegate(params, "Remember this code word for later: PAPAYA-417. Reply only with OK."))
        assert out[-1]["status"] == "done", out[-1]
        errand = out[-1]["errand"]
        first_sid = Registry(db).get_task(errand)["session_id"]
        (delegate,) = make_tools(backend, Registry(db), lambda sid: None, workspace_root=ws)  # restarted
        out.clear()
        asyncio.run(delegate(params, "What was the code word I gave you? Reply with just the word.", errand=errand))
        assert out[-1]["status"] == "done" and "PAPAYA-417" in out[-1]["result"], out[-1]
        assert Registry(db).get_task(out[-1]["errand"])["session_id"] == first_sid
    finally:
        shutil.rmtree(ws, ignore_errors=True)
