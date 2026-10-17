"""Builds and runs the Telegram application."""
import logging

from telegram import BotCommand, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from assistant.config import Settings
from assistant.db.store import Store
from assistant.tools.crypto.service import TokenService
from assistant.usage.ratelimit import RateLimiter
from assistant.interfaces.telegram import handlers

log = logging.getLogger(__name__)

# Only the essentials are advertised in Telegram's command list. The rest still work.
COMMANDS = [
    BotCommand("menu", "Open the main menu"),
    BotCommand("tasks", "Show open tasks"),
    BotCommand("task", "Add a task"),
    BotCommand("token", "Research a token contract"),
    BotCommand("cancel", "Cancel what I just asked you"),
    BotCommand("help", "What works right now"),
    BotCommand("myid", "Show your Telegram user ID"),
]

COMMAND_HANDLERS = {
    "start": handlers.start,
    "help": handlers.help_command,
    "menu": handlers.menu,
    "settings": handlers.settings,
    "status": handlers.status,
    "myid": handlers.myid,
    "adduser": handlers.adduser,
    "removeuser": handlers.removeuser,
    "users": handlers.users,
    "newproject": handlers.newproject,
    "projects": handlers.projects,
    "project": handlers.project,
    "setinfo": handlers.setinfo,
    "note": handlers.note,
    "notes": handlers.notes,
    "outdate": handlers.outdate,
    "delnote": handlers.delnote,
    "deleteproject": handlers.deleteproject,
    "tasks": handlers.tasks,
    "task": handlers.task,
    "cancel": handlers.cancel,
    "token": handlers.token,
}


async def _post_init(app: Application) -> None:
    try:
        await app.bot.set_my_commands(COMMANDS)
    except Exception:
        log.warning("Could not publish the command list to Telegram (will retry next start)")


async def _post_shutdown(app: Application) -> None:
    service = app.bot_data.get("crypto")
    if service is not None:
        await service.aclose()


def build_application(settings: Settings, store: Store | None = None, crypto=None) -> Application:
    if settings.telegram_bot_token is None:
        raise ValueError("TELEGRAM_BOT_TOKEN is not set")

    if store is None:
        store = Store(settings.database_url)
    store.create_tables()
    log.info("Database ready (%s)", store.dialect)

    app = (
        Application.builder()
        .token(settings.telegram_bot_token.get_secret_value())
        .connect_timeout(10)
        .read_timeout(20)
        .write_timeout(20)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )
    app.bot_data["settings"] = settings
    app.bot_data["db"] = store
    app.bot_data["crypto"] = crypto or TokenService()
    app.bot_data["limiter"] = RateLimiter(settings.crypto_lookups_per_5_min, 300.0)

    for name, fn in COMMAND_HANDLERS.items():
        app.add_handler(CommandHandler(name, fn))
    app.add_handler(CallbackQueryHandler(handlers.on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.on_text))
    app.add_error_handler(handlers.on_error)
    return app


def run_bot(settings: Settings) -> None:
    """Start polling. Only ONE copy of the bot may run per token."""
    app = build_application(settings)
    log.info("Bot starting. Press Ctrl+C to stop it.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)
