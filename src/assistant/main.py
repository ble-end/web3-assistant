"""Entry point: check configuration, start logging, then start the Telegram bot."""
import logging

from assistant import __version__
from assistant.config import get_settings
from assistant.logging_setup import setup_logging

log = logging.getLogger("assistant")


def run() -> int:
    try:
        settings = get_settings()
    except Exception as exc:  # configuration problem: explain clearly, never crash with a wall of text
        print(f"Configuration error: {exc}")
        print("Check your .env file against .env.example.")
        return 1

    setup_logging(settings.log_level, settings.secret_values())
    log.info("Web3 Assistant v%s starting (env=%s)", __version__, settings.app_env)
    log.info("Anthropic key: %s", "set" if settings.anthropic_api_key else "not set (needed in Phase 3)")
    log.info("Admin users configured: %d", len(settings.admin_user_ids))

    if settings.telegram_bot_token is None:
        log.warning("TELEGRAM_BOT_TOKEN is not set in .env, so the bot cannot start.")
        return 0
    if not settings.admin_user_ids:
        log.warning("ADMIN_USER_IDS is empty: the bot will only answer /myid until you add your ID.")

    from sqlalchemy.exc import SQLAlchemyError
    from telegram.error import InvalidToken, NetworkError

    from assistant.interfaces.telegram.app import run_bot

    try:
        run_bot(settings)
    except InvalidToken:
        log.error("Telegram rejected the bot token. Check TELEGRAM_BOT_TOKEN in .env.")
        return 1
    except SQLAlchemyError:
        log.error("Could not open the database. Check DATABASE_URL in .env.")
        return 1
    except NetworkError:
        log.error("Could not reach Telegram. Check your internet connection and try again.")
        return 1
    log.info("Bot stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
