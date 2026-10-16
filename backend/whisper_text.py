"""Whisper invents text on silence and noise; this drops or trims it before it reaches the assistant."""

import re

# What Whisper says over silence (its training data was subtitled video).
INVENTED = {"thank you", "thanks for watching", "you"}


def clean(text: str) -> str:
    # "Nova Nova Nova" -> "Nova", "Thank you. Thank you. Thank you." -> "Thank you."; first, so the checks see the result
    text = re.sub(r"(?<!\S)(.+?)(?:\s+\1){2,}(?!\S)", r"\1", text.strip(), flags=re.I)
    if re.fullmatch(r"\[[^\]]*\]|\([^)]*\)", text):  # [MUSIC PLAYING], (crickets chirping)
        return ""
    if text.lower().rstrip(".!?,;… ") in INVENTED:
        return ""
    return text
