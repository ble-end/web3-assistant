"""Stops secrets from being saved to memory. Memory must never hold keys, passwords or seed phrases."""
import re

from assistant.logging_setup import redact

_PRIVATE_KEY = re.compile(r"\b(?:0x)?[0-9a-fA-F]{64}\b")
_WORD = re.compile(r"[a-z]{3,8}")

SECRET_MESSAGE = (
    "That looks like it could be a password, API key, private key or seed phrase, "
    "so I did not save it. I never store those. If it is something else (for example a "
    "transaction hash, which looks the same as a private key), please reword it and try again."
)


def looks_like_secret(text: str) -> bool:
    if redact(text) != text:  # API keys and bot tokens
        return True
    if _PRIVATE_KEY.search(text):
        return True
    words = text.split()
    if len(words) in (12, 15, 18, 21, 24) and all(_WORD.fullmatch(w) for w in words):
        return True  # looks like a recovery (seed) phrase
    return False
