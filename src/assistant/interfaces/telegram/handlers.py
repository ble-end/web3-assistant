"""Telegram command and button handlers.

Handlers only talk to the user and call the Store. Who the user is always comes from
Telegram's own update data, never from message text or button data.
"""
import asyncio
import functools
import logging
import math
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from assistant import __version__
from assistant.db.store import CAMPAIGN_FIELDS, NOTE_KINDS, PROJECT_FIELDS, StoreError
from assistant.interfaces.telegram import keyboards
from assistant.interfaces.telegram.access import is_authorized
from assistant.memory.safety import SECRET_MESSAGE, looks_like_secret
from assistant.projects.dates import format_due, parse_date, parse_task_input
from assistant.providers.llm.google_ai import GoogleAIProvider
from assistant.tools.crypto.analysis import format_report, split_message
from assistant.tools.crypto.chains import chain_names, find_address, normalize_address, resolve_chain
from assistant.tools.crypto.service import ResearchError

log = logging.getLogger(__name__)

ai_provider = GoogleAIProvider()

PRIVATE_MSG = "Sorry, this bot is private."
OWNER_ONLY_MSG = "Only the bot owner can do that."
NO_ACTIVE_MSG = "You have no active project. Create one with /newproject <name>."
UNKNOWN_BUTTON_MSG = "Sorry, I did not recognise that button."
AWAIT_TTL_SECONDS = 600  # a pending question is forgotten after 10 minutes
SETUP_MSG = (
    "This bot is not set up yet.\n"
    "Send /myid to get your Telegram user ID, then put that number in the "
    "ADMIN_USER_IDS line of your .env file and restart the bot."
)

FIELD_ALIASES = {"chain": "blockchain", **{name: name for name in PROJECT_FIELDS}}

HELP_TEXT = (
    "Easiest way: tap /menu and use the buttons.\n\n"
    "Tasks\n"
    "Menu > Web3 Project Management lets you add tasks, set deadlines, assign people "
    "and mark things done, all with buttons.\n"
    "Shortcuts: /tasks to see open tasks, /task <text> to add one "
    "(end with 'by friday' for a deadline), /cancel to cancel a question I asked you.\n\n"
    "Projects and memory\n"
    "Menu > Project Memory lists and switches projects.\n"
    "/newproject <name> - create a project\n"
    "/project - show your active project\n"
    "/setinfo <field> <text> - fields: " + ", ".join(PROJECT_FIELDS) + "\n"
    "/note <text> - save a note (start with fact:, assumption:, decision: or question:)\n"
    "/notes, /outdate <number>, /delnote <number>, /deleteproject\n\n"
    "Crypto research (free data from DexScreener and GoPlus)\n"
    "Paste a token address (EVM 0x... or Solana) or tap Crypto Analysis in /menu. "
    "/token <address> [base|bsc|eth|sol] also works. Research only: I never trade or connect wallets.\n\n"
    "Campaign briefs\n"
    "Menu > Web3 Project Management > Campaign briefs.\n\n"
    "General: /settings, /status, /myid, /help\n"
    "Owner only: /adduser <id>, /removeuser <id>, /users\n\n"
    "AI conversation engine is active. You can chat with me naturally!"
)


# ------------------------------------------------------------------ helpers


def _settings(context: ContextTypes.DEFAULT_TYPE):
    return context.bot_data["settings"]


def _store(context: ContextTypes.DEFAULT_TYPE):
    return context.bot_data["db"]


async def _run(fn, *args):
    """Run a database call in a worker thread so the bot stays responsive."""
    return await asyncio.to_thread(fn, *args)


def _is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    return is_authorized(user.id if user else None, _settings(context).admin_user_ids)


async def _say(update: Update, text: str, reply_markup=None) -> None:
    """Reply to a message, or show a popup when the user tapped a button."""
    query = update.callback_query
    if query is not None:
        await query.answer(text, show_alert=True)
    elif update.effective_message is not None:
        await update.effective_message.reply_text(text, reply_markup=reply_markup)


async def _gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """True if the sender may use the bot (an owner from .env, or a friend the owner added)."""
    user = update.effective_user
    if user is None:
        return False
    store = _store(context)
    if _is_admin(update, context):
        await _run(store.ensure_user, user.id)
        return True
    if _settings(context).admin_user_ids and await _run(store.is_active_user, user.id):
        return True
    await _say(update, PRIVATE_MSG if _settings(context).admin_user_ids else SETUP_MSG)
    return False


def member(fn):
    """Only the owner and added friends get through. Friendly message for expected problems."""

    @functools.wraps(fn)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await _gate(update, context):
            return
        try:
            await fn(update, context)
        except StoreError as exc:
            await _say(update, str(exc))

    return wrapper


def admin_only(fn):
    @functools.wraps(fn)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _is_admin(update, context):
            # Unknown people get the same refusal as everywhere else.
            if not await _gate(update, context):
                return
            await _say(update, OWNER_ONLY_MSG)
            return
        await member(fn)(update, context)

    return wrapper


def _parse_user_id(args: list[str], usage: str) -> int:
    if len(args) != 1:
        raise StoreError(f"Usage: {usage}")
    try:
        number = int(args[0])
    except ValueError:
        raise StoreError("That is not a valid Telegram user ID. It should be a number.") from None
    if number <= 0:
        raise StoreError("That is not a valid Telegram user ID.")
    return number


def _parse_number(text: str, usage: str) -> int:
    try:
        return int(text.strip().lstrip("#"))
    except ValueError:
        raise StoreError(f"Usage: {usage}") from None


def settings_text(context: ContextTypes.DEFAULT_TYPE, active_name: str | None) -> str:
    s = _settings(context)
    return (
        "Your settings (read-only for now)\n\n"
        f"Active project: {active_name or 'none (create one with /newproject)'}\n"
        "Response mode: text only\n"
        f"Time zone: {s.timezone}\n"
        f"Conversation history limit: {s.history_message_limit} messages\n"
        "Voice and image features are planned for later, when budget allows.\n\n"
        "Editable settings and usage tracking arrive in later phases."
    )


def status_text(context: ContextTypes.DEFAULT_TYPE) -> str:
    s = _settings(context)
    return (
        f"Bot is running (version {__version__}, {s.app_env}).\n"
        f"Database: connected ({_store(context).dialect})\n"
        "AI engine: Google GenAI (Gemini) connected\n"
        "You have access." + _crypto_stats(context)
    )


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"


async def _projects_view(context: ContextTypes.DEFAULT_TYPE, user_id: int):
    store = _store(context)
    projects = await _run(store.list_projects, user_id)
    active = await _run(store.get_active_project, user_id)
    if not projects:
        return (
            "You have no projects yet. Create one with /newproject <name>.",
            keyboards.back_to_menu(),
        )
    text = (
        "Your projects. Tap one to make it your active project.\n"
        f"Active: {active.name if active else 'none'}"
    )
    return text, keyboards.projects_list(projects, active.id if active else None)


def _project_text(project, notes) -> str:
    def val(x):
        return x if x else "not set"

    lines = [
        f"Project: {project.name}",
        f"Blockchain: {val(project.blockchain)}",
        f"Website: {val(project.website)}",
        f"Audience: {val(project.audience)}",
        f"Brand voice: {val(project.voice)}",
        f"Description: {val(project.description)}",
        f"Goals: {val(project.goals)}",
    ]
    active = [n for n in notes if n.status == "active"]
    lines.append(f"\nNotes: {len(active)} active (see /notes)")
    return _truncate("\n".join(lines), 3800)


def _notes_text(project, notes) -> str:
    if not notes:
        return f'No notes yet for "{project.name}". Add one with /note <text>.'
    lines = [f'Notes for "{project.name}":']
    for n in notes[-30:]:
        flag = " (OUTDATED)" if n.status == "outdated" else ""
        lines.append(f"#{n.id} [{n.kind}]{flag} {_truncate(n.content, 200)}")
    if len(notes) > 30:
        lines.append(f"(showing the latest 30 of {len(notes)})")
    return _truncate("\n".join(lines), 3900)


# ---------------------------------------------------------- general commands


@member
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    name = update.effective_user.first_name or "there"
    await _say(
        update,
        f"Hi {name}! I am your Web3 assistant powered by Google AI.\n\n"
        "Try chatting with me naturally, or use /menu to see available options.",
        reply_markup=keyboards.main_menu(),
    )


@member
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _say(update, HELP_TEXT)


@member
async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _say(
        update,
        "Main menu. Items marked \U0001f51c are not built yet. Tap one to see when it arrives.",
        reply_markup=keyboards.main_menu(),
    )


@member
async def settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    active = await _run(_store(context).get_active_project, update.effective_user.id)
    await _say(update, settings_text(context, active.name if active else None))


@member
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _say(update, status_text(context))


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Open to everyone on purpose: it only shows the sender their own ID."""
    user = update.effective_user
    if user is None:
        return
    await update.effective_message.reply_text(f"Your Telegram user ID is: {user.id}")


# ---------------------------------------------------------- owner commands


@admin_only
async def adduser(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    target = _parse_user_id(context.args, "/adduser 123456789")
    if target in _settings(context).admin_user_ids:
        raise StoreError("That person is already an owner.")
    result = await _run(_store(context).add_user, target, update.effective_user.id)
    messages = {
        "added": f"Added {target}. They can now message the bot and send /start.",
        "reactivated": f"Access restored for {target}.",
        "exists": f"{target} already has access.",
    }
    await _say(update, messages[result])


@admin_only
async def removeuser(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    target = _parse_user_id(context.args, "/removeuser 123456789")
    if target in _settings(context).admin_user_ids:
        raise StoreError("Owners cannot be removed here. Edit ADMIN_USER_IDS in .env instead.")
    removed = await _run(_store(context).remove_user, target)
    if removed:
        await _say(update, f"Access removed for {target}. Their saved projects are kept but locked.")
    else:
        await _say(update, f"{target} did not have access.")


@admin_only
async def users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    owners = _settings(context).admin_user_ids
    friends = [u for u in await _run(_store(context).list_users) if u.telegram_id not in owners]
    lines = ["Owners: " + ", ".join(str(i) for i in owners)]
    lines.append("Friends: " + (", ".join(str(u.telegram_id) for u in friends) or "none yet"))
    await _say(update, "\n".join(lines))


# --------------------------------------------------------- project commands


@member
async def newproject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    name = " ".join(context.args).strip()
    if not name:
        raise StoreError("Usage: /newproject <name>")
    if looks_like_secret(name):
        raise StoreError(SECRET_MESSAGE)
    project = await _run(_store(context).create_project, update.effective_user.id, name)
    await _say(
        update,
        f'Project "{project.name}" created and set as your active project.\n'
        "Add details with /setinfo, for example: /setinfo blockchain Base",
    )


@member
async def projects(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text, markup = await _projects_view(context, update.effective_user.id)
    await _say(update, text, reply_markup=markup)


async def _active_or_error(context: ContextTypes.DEFAULT_TYPE, user_id: int):
    project = await _run(_store(context).get_active_project, user_id)
    if project is None:
        raise StoreError(NO_ACTIVE_MSG)
    return project


@member
async def project(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    current = await _active_or_error(context, uid)
    notes = await _run(_store(context).list_notes, uid, current.id)
    await _say(update, _project_text(current, notes))


@member
async def setinfo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    usage = "Usage: /setinfo <field> <text>\nFields: " + ", ".join(PROJECT_FIELDS)
    if len(context.args) < 2:
        raise StoreError(usage)
    field = FIELD_ALIASES.get(context.args[0].lower())
    if field is None:
        raise StoreError(usage)
    value = " ".join(context.args[1:])
    if looks_like_secret(value):
        raise StoreError(SECRET_MESSAGE)
    current = await _active_or_error(context, uid)
    await _run(_store(context).update_project_field, uid, current.id, field, value)
    await _say(update, f'Saved. {field.capitalize()} updated for "{current.name}".')


@member
async def note(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    text = " ".join(context.args).strip()
    kind = "fact"
    head, sep, rest = text.partition(":")
    if sep and head.strip().lower() in NOTE_KINDS:
        kind, text = head.strip().lower(), rest.strip()
    if not text:
        raise StoreError("Usage: /note <text>  (optionally start with fact:, assumption:, decision: or question:)")
    if looks_like_secret(text):
        raise StoreError(SECRET_MESSAGE)
    current = await _active_or_error(context, uid)
    saved = await _run(_store(context).add_note, uid, current.id, kind, text)
    await _say(update, f'Saved as {kind} #{saved.id} in "{current.name}".')


@member
async def notes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    current = await _active_or_error(context, uid)
    items = await _run(_store(context).list_notes, uid, current.id)
    await _say(update, _notes_text(current, items))


@member
async def outdate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    number = _parse_number(" ".join(context.args), "/outdate <note number>")
    await _run(_store(context).set_note_status, update.effective_user.id, number, "outdated")
    await _say(update, f"Note #{number} marked as outdated.")


@member
async def delnote(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    number = _parse_number(" ".join(context.args), "/delnote <note number>")
    await _run(_store(context).delete_note, update.effective_user.id, number)
    await _say(update, f"Note #{number} deleted.")


@member
async def deleteproject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    current = await _active_or_error(context, update.effective_user.id)
    await _say(
        update,
        f'Delete "{current.name}" and all of its notes? This cannot be undone.',
        reply_markup=keyboards.confirm_delete(current.id),
    )


# ------------------------------------------------------------------- tasks


def _today(context: ContextTypes.DEFAULT_TYPE) -> date:
    override = context.bot_data.get("today")  # lets tests fix the date
    if override:
        return override
    return datetime.now(ZoneInfo(_settings(context).timezone)).date()


def _set_await(context: ContextTypes.DEFAULT_TYPE, kind: str, **data) -> None:
    context.user_data["await"] = {"kind": kind, "at": time.time(), **data}


def _get_await(context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get("await")
    if not state:
        return None
    if time.time() - state["at"] > AWAIT_TTL_SECONDS:
        context.user_data.pop("await", None)
        return None
    return state


def _task_text(task, project_name: str, today: date) -> str:
    status = "done" if task.status == "done" else "open"
    lines = [
        f"Task #{task.id}: {task.title}",
        f"Project: {project_name}",
        f"Status: {status}",
        "Deadline: " + (format_due(task.due_date, today) if task.due_date else "none"),
        f"Assigned to: {task.assignee or 'nobody yet'}",
    ]
    if task.notes:
        lines.append(f"Notes: {task.notes}")
    return _truncate("\n".join(lines), 3800)


async def _task_view(context: ContextTypes.DEFAULT_TYPE, user_id: int, task_id: int):
    store = _store(context)
    task = await _run(store.get_task, user_id, task_id)
    proj = await _run(store.get_project, user_id, task.project_id)
    return _task_text(task, proj.name, _today(context)), keyboards.task_detail(task)


async def _dashboard_view(context: ContextTypes.DEFAULT_TYPE, user_id: int):
    store = _store(context)
    proj = await _run(store.get_active_project, user_id)
    if proj is None:
        return (
            "You have no active project yet. Create one with /newproject <name>, then come back here.",
            keyboards.back_to_menu(),
        )
    today = _today(context)
    counts = await _run(store.task_counts, user_id, proj.id, today)
    upcoming = (await _run(store.list_tasks, user_id, proj.id, "open"))[:3]
    lines = [
        f"Project: {proj.name}",
        f"Open tasks: {counts['open']}  |  Overdue: {counts['overdue']}  |  Completed: {counts['done']}",
    ]
    if upcoming:
        lines.append("\nNext up:")
        for t in upcoming:
            due = f" - {format_due(t.due_date, today)}" if t.due_date else ""
            lines.append(f"#{t.id} {_truncate(t.title, 60)}{due}")
    lines.append("\nPlanning campaigns with AI is ready.")
    return "\n".join(lines), keyboards.dashboard()


async def _tasks_view(context: ContextTypes.DEFAULT_TYPE, user_id: int, status: str):
    store = _store(context)
    proj = await _run(store.get_active_project, user_id)
    if proj is None:
        return NO_ACTIVE_MSG, keyboards.back_to_menu()
    today = _today(context)
    tasks = await _run(store.list_tasks, user_id, proj.id, status)
    label = "Open" if status == "open" else "Completed"
    if not tasks:
        text = f'{label} tasks for "{proj.name}": none.'
    else:
        text = f'{label} tasks for "{proj.name}" ({len(tasks)}). Tap one to open it.'
        if len(tasks) > 20:
            text += " Showing the first 20."
    return text, keyboards.tasks_list(tasks, today, done=(status == "done"))


@member
async def tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text, markup = await _tasks_view(context, update.effective_user.id, "open")
    await _say(update, text, reply_markup=markup)


@member
async def task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    text = " ".join(context.args).strip()
    if not text:
        raise StoreError("Usage: /task <text>  (end with 'by friday' to add a deadline). Or use /menu.")
    if looks_like_secret(text):
        raise StoreError(SECRET_MESSAGE)
    proj = await _active_or_error(context, uid)
    today = _today(context)
    title, due, warning = parse_task_input(text, today)
    created = await _run(_store(context).add_task, uid, proj.id, title, due)
    await _say(update, _added_message(created, due, warning, today), reply_markup=keyboards.after_add(created.id))


def _added_message(created, due, warning, today) -> str:
    lines = [f"Task #{created.id} added: {created.title}"]
    if due:
        lines.append(f"Deadline: {format_due(due, today)}")
    if warning:
        lines.append(warning)
    return "\n".join(lines)


@member
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    had_pending = context.user_data.pop("await", None) is not None
    await _say(update, "Cancelled." if had_pending else "Nothing to cancel.")


@member
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Plain messages. Right now they only answer a question the bot just asked (like a task's text)."""
    state = _get_await(context)
    if state is None or state["kind"] == "token":
        await _on_text_free(update, context, state)
        return

    uid = update.effective_user.id
    store = _store(context)
    text = (update.effective_message.text or "").strip()
    if looks_like_secret(text):
        raise StoreError(SECRET_MESSAGE)  # the question stays open so they can try again
    today = _today(context)
    kind = state["kind"]

    if kind == "add_task":
        title, due, warning = parse_task_input(text, today)
        created = await _run(store.add_task, uid, state["project_id"], title, due)
        context.user_data.pop("await", None)
        await _say(update, _added_message(created, due, warning, today), reply_markup=keyboards.after_add(created.id))
        return

    if kind in ("camp_name", "camp_field"):
        await _on_text_campaign(update, context, state, text, uid)
        return

    clearing = text.lower() in ("none", "clear", "remove", "no")
    task_id = state["task_id"]
    if kind == "due":
        if clearing:
            await _run(store.set_task_due, uid, task_id, None)
        else:
            due = parse_date(text, today)
            if due is None:
                await _say(
                    update,
                    'I could not read that date. Try "friday", "tomorrow", "in 3 days", '
                    '"2026-10-20" or "20/10/2026". Or send "none" to remove the deadline.',
                )
                return
            await _run(store.set_task_due, uid, task_id, due)
    elif kind == "assign":
        await _run(store.set_task_assignee, uid, task_id, None if clearing else text)
    elif kind == "notes":
        await _run(store.set_task_notes, uid, task_id, None if clearing else text)
    else:
        context.user_data.pop("await", None)
        return
    context.user_data.pop("await", None)
    view_text, markup = await _task_view(context, uid, task_id)
    await _say(update, "Saved.\n\n" + view_text, reply_markup=markup)


PROMPTS = {
    "due": 'Send the new deadline, for example "friday", "tomorrow", "2026-10-20" or "20/10/2026". Send "none" to remove it.',
    "assign": 'Who is responsible for this task? Send a name, or "none" to clear it.',
    "notes": 'Send the note for this task (it replaces any existing note). Send "none" to clear it.',
}


async def _task_action(context: ContextTypes.DEFAULT_TYPE, uid: int, key: str, num: int | None):
    """Returns (text, keyboard, popup message or None) for a task button."""
    store = _store(context)
    unknown = (UNKNOWN_BUTTON_MSG, keyboards.back_to_menu(), None)

    if key == "list":
        return (*await _tasks_view(context, uid, "open"), None)
    if key == "donelist":
        return (*await _tasks_view(context, uid, "done"), None)
    if key == "cancel":
        return (*await _dashboard_view(context, uid), None)
    if key == "add":
        proj = await _active_or_error(context, uid)
        _set_await(context, "add_task", project_id=proj.id)
        return (
            f'Adding a task to "{proj.name}".\n\nSend the task as one message. '
            'To add a deadline, end it with "by friday", "by tomorrow" or "by 2026-10-20".',
            keyboards.cancel_prompt(),
            None,
        )
    if num is None:
        return unknown

    if key == "view":
        return (*await _task_view(context, uid, num), None)
    if key in ("done", "reopen"):
        await _run(store.set_task_status, uid, num, "done" if key == "done" else "open")
        return (*await _task_view(context, uid, num), "Marked as done" if key == "done" else "Reopened")
    if key in PROMPTS:
        await _run(store.get_task, uid, num)  # confirms it is theirs
        _set_await(context, key, task_id=num)
        return PROMPTS[key], keyboards.cancel_prompt(), None
    if key == "del":
        found = await _run(store.get_task, uid, num)
        return f'Delete task #{found.id} "{found.title}"? This cannot be undone.', keyboards.confirm_task_delete(num), None
    if key == "delyes":
        await _run(store.delete_task, uid, num)
        return (*await _tasks_view(context, uid, "open"), "Task deleted")
    return unknown


# ------------------------------------------------------------------ buttons


@member
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    uid = update.effective_user.id
    store = _store(context)
    context.user_data.pop("await", None)  # tapping any button cancels a pending question
    kind, _, rest = (query.data or "").partition(":")
    key, _, arg = rest.partition(":")
    num = int(arg) if arg.isdigit() else None
    toast = None

    if kind == "soon":
        await query.answer(keyboards.phase_for(key) or "That feature is not available yet.", show_alert=True)
        return

    text, markup = UNKNOWN_BUTTON_MSG, keyboards.back_to_menu()

    if kind == "menu" and key == "main":
        text, markup = (
            "Main menu. Items marked \U0001f51c are not built yet.",
            keyboards.main_menu(),
        )
    elif kind == "menu" and key == "help":
        text = HELP_TEXT
    elif kind == "menu" and key == "settings":
        active = await _run(store.get_active_project, uid)
        text = settings_text(context, active.name if active else None)
    elif kind == "menu" and key == "memory":
        text, markup = await _projects_view(context, uid)
    elif kind == "menu" and key == "crypto":
        text, markup = _crypto_prompt(context)
    elif kind == "menu" and key == "pm":
        text, markup = await _dashboard_view(context, uid)
    elif kind == "task":
        text, markup, toast = await _task_action(context, uid, key, num)
    elif kind == "camp":
        text, markup, toast = await _camp_action(context, uid, key, arg)
    elif kind == "proj" and key == "use" and num is not None:
        await _run(store.set_active_project, uid, num)
        text, markup = await _projects_view(context, uid)
    elif kind == "proj" and key == "delyes" and num is not None:
        name = await _run(store.delete_project, uid, num)
        text = f'Deleted "{name}" and all of its notes and tasks.'
    elif kind == "proj" and key == "delno":
        text = "Cancelled. Nothing was deleted."

    await query.answer(toast)
    try:
        await query.edit_message_text(text, reply_markup=markup)
    except BadRequest:
        pass  # message was already showing this exact content


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log the problem (secrets are redacted by the logger) and keep the bot alive."""
    log.error("Unhandled error while processing an update", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message is not None:
        try:
            await update.effective_message.reply_text(
                "Something went wrong on my side. Please try again in a moment."
            )
        except Exception:  # never let the error handler itself crash
            log.warning("Could not send the error message to the user")


# ------------------------------------------------------------------- crypto

TOKEN_PROMPT = (
    "Send me a token address and I will research it on " + chain_names() + ". "
    "EVM addresses start with 0x (42 characters); Solana addresses are 32 to 44 letters and numbers.\n\n"
    "I only research. I never connect wallets, sign anything or trade."
)
BAD_ADDRESS_MSG = (
    "That does not look like a token address (EVM: 0x followed by 40 letters or numbers; "
    "Solana: 32 to 44 letters and numbers)."
)


def _crypto_prompt(context: ContextTypes.DEFAULT_TYPE):
    _set_await(context, "token")
    return TOKEN_PROMPT, keyboards.cancel_to_menu()


def _crypto_stats(context: ContextTypes.DEFAULT_TYPE) -> str:
    service = context.bot_data.get("crypto")
    if service is None:
        return ""
    s = service.stats
    return (
        f"\nToken lookups since start: {s['lookups']} "
        f"({s['dexscreener_calls']} DexScreener calls, {s['goplus_calls']} GoPlus calls)"
    )


async def _do_lookup(update: Update, context: ContextTypes.DEFAULT_TYPE, address: str, chain: str | None = None) -> None:
    limit = _settings(context).crypto_lookups_per_5_min
    wait = context.bot_data["limiter"].check(update.effective_user.id)
    if wait > 0:
        raise StoreError(
            f"You have reached the limit of {limit} token lookups per 5 minutes. "
            f"Try again in about {math.ceil(wait / 60)} minute(s)."
        )
    await _say(update, f"Checking that token on {chain_names()}. This takes a few seconds...")
    try:
        report = await context.bot_data["crypto"].lookup(address, chain)
    except ResearchError as exc:
        raise StoreError(str(exc)) from None
    for part in split_message(format_report(report)):
        await _say(update, part)


@member
async def token(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    if not args or len(args) > 2:
        raise StoreError("Usage: /token <address> [base|bsc|eth|sol]\nOr just paste a token address.")
    address = normalize_address(args[0])
    if address is None:
        raise StoreError(BAD_ADDRESS_MSG)
    chain = None
    if len(args) == 2:
        chain = resolve_chain(args[1])
        if chain is None:
            raise StoreError(f"I only support: {chain_names()}.")
    context.user_data.pop("await", None)
    await _do_lookup(update, context, address, chain)


async def _on_text_free(update: Update, context: ContextTypes.DEFAULT_TYPE, state) -> None:
    """A plain message when no task or campaign question is open."""
    text = (update.effective_message.text or "").strip()
    if looks_like_secret(text):
        raise StoreError(
            "That looks like a private key, seed phrase or API key. Never share those with anyone, "
            "including me. I did not save it. Please delete that message."
        )
    address = find_address(text)
    if address:
        context.user_data.pop("await", None)
        await _do_lookup(update, context, address)
        return
    if state is not None:  # we are waiting for a token address
        raise StoreError(BAD_ADDRESS_MSG + " Send another address, or tap Cancel.")
    
    # Query Google AI for natural conversation
    try:
        response_text = await ai_provider.generate_response(prompt=text)
        await _say(update, response_text)
    except Exception as e:
        log.error(f"Error generating AI response: {e}")
        await _say(update, "Sorry, I ran into an issue communicating with the AI engine. Please try again.")


# ---------------------------------------------------------------- campaigns


def _campaign_text(campaign) -> str:
    lines = [f"Campaign: {campaign.name}", ""]
    for key, (label, _question) in CAMPAIGN_FIELDS.items():
        value = getattr(campaign, key)
        lines.append(f"{label}: {_truncate(value, 300) if value else 'not set'}")
    lines += ["", "This brief is your own plan. Items marked 'not set' are still open."]
    return _truncate("\n".join(lines), 3900)


async def _campaigns_view(context: ContextTypes.DEFAULT_TYPE, user_id: int):
    store = _store(context)
    proj = await _run(store.get_active_project, user_id)
    if proj is None:
        return NO_ACTIVE_MSG, keyboards.back_to_menu()
    items = await _run(store.list_campaigns, user_id, proj.id)
    if items:
        text = f'Campaign briefs for "{proj.name}" ({len(items)}). Tap one to open it.'
    else:
        text = f'No campaign briefs yet for "{proj.name}". Tap New campaign to start one.'
    return text, keyboards.campaigns_list(items)


async def _campaign_view(context: ContextTypes.DEFAULT_TYPE, user_id: int, campaign_id: int):
    found = await _run(_store(context).get_campaign, user_id, campaign_id)
    return _campaign_text(found), keyboards.campaign_view(found, CAMPAIGN_FIELDS)


def _field_question(name: str, index: int) -> str:
    keys = list(CAMPAIGN_FIELDS)
    label, question = CAMPAIGN_FIELDS[keys[index]]
    return (
        f"{name}: {label} ({index + 1} of {len(keys)})\n{question}\n\n"
        'Send your answer, tap Skip, or send "none" to leave it empty.'
    )


async def _camp_action(context: ContextTypes.DEFAULT_TYPE, uid: int, key: str, arg: str):
    store = _store(context)
    parts = arg.split(":") if arg else []
    cid = int(parts[0]) if parts and parts[0].isdigit() else None
    keys = list(CAMPAIGN_FIELDS)
    unknown = (UNKNOWN_BUTTON_MSG, keyboards.back_to_menu(), None)

    if key == "list":
        return (*await _campaigns_view(context, uid), None)
    if key == "new":
        proj = await _active_or_error(context, uid)
        _set_await(context, "camp_name", project_id=proj.id)
        return f'New campaign for "{proj.name}". What is it called?', keyboards.cancel_to_campaigns(), None
    if cid is None:
        return unknown
    if key == "view":
        return (*await _campaign_view(context, uid, cid), None)
    if key == "fill":
        index = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        found = await _run(store.get_campaign, uid, cid)
        if index >= len(keys):
            return (*await _campaign_view(context, uid, cid), "Brief complete")
        _set_await(context, "camp_field", campaign_id=cid, field=keys[index], next=index + 1)
        return _field_question(found.name, index), keyboards.guided_prompt(cid, index + 1), None
    if key == "edit":
        field = parts[1] if len(parts) > 1 else ""
        if field not in CAMPAIGN_FIELDS:
            return unknown
        found = await _run(store.get_campaign, uid, cid)
        _set_await(context, "camp_field", campaign_id=cid, field=field, next=None)
        label, question = CAMPAIGN_FIELDS[field]
        return (
            f'{found.name}: {label}\n{question}\n\nSend your answer, or send "none" to clear it.',
            keyboards.edit_prompt(cid),
            None,
        )
    if key == "del":
        found = await _run(store.get_campaign, uid, cid)
        return f'Delete the campaign "{found.name}"? This cannot be undone.', keyboards.confirm_campaign_delete(cid), None
    if key == "delyes":
        await _run(store.delete_campaign, uid, cid)
        return (*await _campaigns_view(context, uid), "Campaign deleted")
    return unknown


async def _on_text_campaign(update: Update, context: ContextTypes.DEFAULT_TYPE, state, text: str, uid: int) -> None:
    store = _store(context)
    keys = list(CAMPAIGN_FIELDS)
    if state["kind"] == "camp_name":
        created = await _run(store.add_campaign, uid, state["project_id"], text)
        _set_await(context, "camp_field", campaign_id=created.id, field=keys[0], next=1)
        await _say(
            update,
            f'Campaign "{created.name}" created. Let us fill in the brief.\n\n' + _field_question(created.name, 0),
            reply_markup=keyboards.guided_prompt(created.id, 1),
        )
        return

    clearing = text.lower() in ("none", "clear", "remove", "no")
    cid, field, nxt = state["campaign_id"], state["field"], state.get("next")
    await _run(store.set_campaign_field, uid, cid, field, None if clearing else text)
    context.user_data.pop("await", None)
    if nxt is not None and nxt < len(keys):
        found = await _run(store.get_campaign, uid, cid)
        _set_await(context, "camp_field", campaign_id=cid, field=keys[nxt], next=nxt + 1)
        await _say(
            update,
            "Saved.\n\n" + _field_question(found.name, nxt),
            reply_markup=keyboards.guided_prompt(cid, nxt + 1),
        )
        return
    view_text, markup = await _campaign_view(context, uid, cid)
    await _say(update, "Saved.\n\n" + view_text, reply_markup=markup)