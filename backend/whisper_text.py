"""Whisper invents text on silence and noise; this drops or trims it before it reaches the assistant."""

import re

# What Whisper says over silence (its training data was subtitled video).
INVENTED = {"thank you", "thanks for watching", "you"}


def clean(text: str) -> str:
    text = text.strip()
    if re.fullmatch(r"\[[^\]]*\]|\([^)]*\)", text):  # [MUSIC PLAYING], (crickets chirping)
        return ""
    if text.lower().rstrip(".!?,; ") in INVENTED:
        return ""
    return re.sub(r"(?<!\S)(\S+)(?:\s+\1){2,}(?!\S)", r"\1", text)  # "Nova Nova Nova" -> "Nova"
