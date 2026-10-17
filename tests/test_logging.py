import logging

from assistant.logging_setup import redact, setup_logging

TOKEN = "123456789:ABCdefGHIjklMNOpqrSTUvwxYZ0123456789"


def test_telegram_token_is_redacted():
    assert TOKEN not in redact(f"url https://api.telegram.org/bot{TOKEN}/getMe")


def test_anthropic_key_is_redacted():
    assert "sk-ant-abc123def456ghi789" not in redact("key=sk-ant-abc123def456ghi789")


def test_known_secret_is_redacted():
    assert "hunter2-secret" not in redact("pw hunter2-secret", ["hunter2-secret"])


def test_normal_text_unchanged():
    assert redact("Hello, this is fine.") == "Hello, this is fine."


def test_logger_hides_secret_end_to_end(capsys):
    setup_logging("INFO", [TOKEN])
    logging.getLogger("t").info("token is %s", TOKEN)
    out = capsys.readouterr().out
    assert TOKEN not in out
    assert "[REDACTED]" in out


def test_traceback_is_redacted(capsys):
    setup_logging("INFO", [])
    try:
        raise RuntimeError(f"failed calling https://api.telegram.org/bot{TOKEN}/getMe")
    except RuntimeError:
        logging.getLogger("t").exception("something broke")
    out = capsys.readouterr().out
    assert "something broke" in out
    assert TOKEN not in out
