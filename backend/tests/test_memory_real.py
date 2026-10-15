"""#25/#26 with the real front model (run with -m claude): Gemini picks the right memory tool from Nova's
instructions, and tells a fact to remember from a reminder."""

import asyncio
import json
import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

from config import Config
from memory import PROMPT
from pipecat.frames.frames import ErrorFrame, FunctionCallsStartedFrame, LLMContextFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.tests.utils import run_test
from services import build_llm

pytestmark = pytest.mark.claude
load_dotenv(Path.home() / "voice-instinct/.env")


async def remember(params, fact: str):
    """Save a fact about the user for good.

    Args:
        fact: The fact in one short sentence, in the third person.
    """


async def forget(params, text: str):
    """Delete what you remember about something, when the user asks you to forget it.

    Args:
        text: The words the facts to delete contain.
    """


async def recall(params, query: str):
    """Look up what you remember about the user.

    Args:
        query: The words to look for.
    """


async def set_reminder(params, when: str, what: str):
    """Set a reminder.

    Args:
        when: The exact local time, e.g. 2026-10-16 17:00.
        what: What to remind the user of.
    """


TOOLS = [remember, forget, recall, set_reminder]
SYSTEM = (PROMPT + "It is 2026-10-16 09:00 local time. For reminders, call set_reminder with the exact local time."
          "\n\nWhat you know about the user:\n# Noted recently\n- 2026-10-16: Is vegetarian")


def _first_call(said):
    llm = build_llm(Config(), SYSTEM)
    for f in TOOLS:
        llm.register_direct_function(f)
    ctx = LLMContext(messages=[{"role": "user", "content": said}], tools=TOOLS)
    down, up = asyncio.run(run_test(llm, frames_to_send=[LLMContextFrame(ctx)]))
    assert not [f for f in up + down if isinstance(f, ErrorFrame)]
    (calls,) = [f for f in down if isinstance(f, FunctionCallsStartedFrame)]
    return calls.function_calls[0].function_name, calls.function_calls[0].arguments


@pytest.mark.skipif(not os.environ.get("GEMINI_API_KEY"), reason="no GEMINI_API_KEY")
@pytest.mark.parametrize("said, tool, word", [
    ("Remember that my sister Priya lives in Pune.", "remember", "priya"),
    ("Remember to call mum at 5 pm today.", "set_reminder", "mum"),
    ("Forget that I'm vegetarian.", "forget", "vegetarian"),
    ("What's my sister's name again?", "recall", "sister"),
])
def test_gemini_picks_the_memory_tool_the_user_meant(said, tool, word):
    name, args = _first_call(said)
    assert name == tool, (name, args)
    assert word in json.dumps(args).lower()
