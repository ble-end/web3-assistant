"""Logging with automatic secret redaction.

Everything written to the log, including error tracebacks, passes through
`redact`. Text that looks like a Telegram bot token or an API key, and any
exact secret value we know about, is replaced with [REDACTED].
"""
import logging
import re
import sys

_PATTERNS = [
    re.compile(r"\d{6,}:[A-Za-z0-9_-]{30,}"),  # Telegram bot token (also inside .../bot<token>/...)
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{10,}\b"),  # Anthropic API key
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),  # other common API keys
]


def redact(text: str, known_secrets: list[str] | None = None) -> str:
    for secret in known_secrets or []:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    for pattern in _PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


class RedactingFormatter(logging.Formatter):
    """Redacts the final text, including any traceback attached to the record."""

    def __init__(self, fmt: str, known_secrets: list[str] | None = None):
        super().__init__(fmt)
        self._secrets = known_secrets or []

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record), self._secrets)


def setup_logging(level: str = "INFO", known_secrets: list[str] | None = None) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        RedactingFormatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s", known_secrets
        )
    )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Libraries can log full request URLs (which contain the bot token). Keep them quiet.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
