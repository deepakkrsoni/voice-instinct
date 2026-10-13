"""Decide yes/no/unclear from the user's actual words; the LLM never decides approval."""

import re

_NO = {"no", "nope", "don't", "dont", "stop", "cancel", "never", "nevermind", "negative"}
_YES = {"yes", "yeah", "yep", "yup", "sure", "ok", "okay", "confirm", "approved", "affirmative"}
_YES_PHRASES = ("go ahead", "do it", "please do")


def classify(reply: str) -> str:
    text = re.sub(r"[^a-z' ]", " ", reply.lower())
    words = set(text.split())
    if words & _NO or "never mind" in text:
        return "no"  # any refusal wins: "yeah no" must not approve
    if words & _YES or any(p in text for p in _YES_PHRASES):
        return "yes"
    return "unclear"


def last_user_text(messages: list[dict]) -> str:
    for m in reversed(messages):
        if m.get("role") != "user":
            continue
        c = m.get("content")
        if isinstance(c, str):
            return c
        if isinstance(c, list):
            return " ".join(p.get("text", "") for p in c if isinstance(p, dict))
    return ""


def user_turns(messages: list[dict]) -> int:
    return sum(1 for m in messages if m.get("role") == "user")
