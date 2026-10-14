"""Nova's one ongoing conversation (#24): saved after every assistant turn, restored when the orb connects,
kept bounded by Pipecat's automatic context summarization. Like Instinct, one thread that never resets;
long-term facts are #25/#26.

Only standard (dict) messages are saved: Pipecat's LLM-specific ones, such as Gemini's thought
signatures (raw bytes), are dropped. Probed: Gemini accepts a restored context without them.
"""

import json

from loguru import logger

from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMAssistantAggregatorParams

# Pipecat's documented automatic summarization, at its defaults (about 8,000 tokens or 20 new messages).
ASSISTANT_PARAMS = LLMAssistantAggregatorParams(enable_auto_context_summarization=True)


def restorable(messages):
    """The messages worth keeping: standard dicts that survive JSON, without a tool call whose result never
    came (an errand that was running when the orb disconnected)."""
    kept = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        try:
            kept.append(json.loads(json.dumps(m)))
        except (TypeError, ValueError):
            continue
    answered = {m.get("tool_call_id") for m in kept if m.get("role") == "tool"}
    return [m for m in kept
            if not (m.get("tool_calls") and any(c.get("id") not in answered for c in m["tool_calls"]))]


def make_context(registry, tools):
    return LLMContext(messages=restorable(registry.load_conversation()), tools=tools)


def save(registry, context):
    try:
        registry.save_conversation(restorable(context.get_messages()))
    except Exception as e:  # never let saving break the conversation itself
        logger.warning(f"conversation not saved: {type(e).__name__}: {e}")
