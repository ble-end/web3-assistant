import asyncio
import time
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from assistant.config import Settings
from assistant.db.store import Store
from assistant.interfaces.telegram import handlers, keyboards
from assistant.interfaces.telegram.access import is_authorized
from assistant.tools.crypto.analysis import build_report
from assistant.tools.crypto.service import ResearchError
from assistant.usage.ratelimit import RateLimiter
from crypto_samples import ADDR, NOW, SOL_ADDR, goplus, pair

OWNER, FRIEND, STRANGER = 1, 2, 99


@pytest.fixture
def store(tmp_path):
    s = Store(f"sqlite:///{(tmp_path / 'bot.db').as_posix()}")
    s.create_tables()
    yield s
    s.dispose()


class FakeService:
    def __init__(self, report=None, error=None):
        self.report = report or build_report(ADDR, NOW, {"base": [pair()]}, {"base": goplus()})
        self.error, self.calls = error, []
        self.stats = {"lookups": 0, "cache_hits": 0, "dexscreener_calls": 0, "goplus_calls": 0}

    async def lookup(self, address, chain=None):
        self.calls.append((address, chain))
        if self.error:
            raise self.error
        return self.report


def make_context(store, admin_ids="1", args=None, service=None, limit=8):
    settings = Settings(_env_file=None, admin_user_ids=admin_ids)
    return SimpleNamespace(
        bot_data={
            "settings": settings, "db": store, "today": date(2026, 10, 15),
            "crypto": service or FakeService(), "limiter": RateLimiter(limit, 300.0),
        },
        args=args or [],
        user_data={},
    )


def make_update(user_id=OWNER, data=None, text=None):
    user = SimpleNamespace(id=user_id, first_name="Tester")
    message = SimpleNamespace(reply_text=AsyncMock(), text=text)
    query = None
    if data is not None:
        query = SimpleNamespace(data=data, answer=AsyncMock(), edit_message_text=AsyncMock())
    return SimpleNamespace(effective_user=user, effective_message=message, callback_query=query)


def run(coro):
    return asyncio.run(coro)


def reply_text(update):
    return update.effective_message.reply_text.call_args.args[0]


def assert_replied_once(update, message):
    assert update.effective_message.reply_text.await_count == 1
    assert reply_text(update) == message


def call(fn, store, user=OWNER, args=None, data=None, admin_ids="1", ctx=None, text=None):
    update = make_update(user, data, text)
    run(fn(update, ctx or make_context(store, admin_ids, args)))
    return update


# ------------------------------------------------------------ access control


def test_is_authorized():
    assert is_authorized(5, [5, 6])
    assert not is_authorized(7, [5, 6])
    assert not is_authorized(None, [5])
    assert not is_authorized(5, [])


def test_owner_gets_start_with_menu(store):
    u = call(handlers.start, store)
    args, kwargs = u.effective_message.reply_text.call_args
    assert "still being built" in args[0]
    assert kwargs["reply_markup"] is not None


def test_stranger_is_refused_everywhere(store):
    for fn in (
        handlers.start, handlers.help_command, handlers.menu, handlers.settings,
        handlers.status, handlers.projects, handlers.project, handlers.notes,
        handlers.deleteproject, handlers.tasks, handlers.cancel,
    ):
        u = call(fn, store, user=STRANGER)
        assert_replied_once(u, handlers.PRIVATE_MSG)


def test_stranger_cannot_create_projects_or_notes(store):
    call(handlers.newproject, store, user=STRANGER, args=["Hack"])
    assert store.list_projects(STRANGER) == []


def test_unconfigured_bot_points_to_myid(store):
    u = call(handlers.menu, store, user=STRANGER, admin_ids="")
    assert "/myid" in reply_text(u)


def test_myid_works_for_anyone(store):
    u = call(handlers.myid, store, user=424242)
    assert "424242" in reply_text(u)


# -------------------------------------------------------------------- friends


def test_owner_can_add_friend_who_then_has_access(store):
    u = call(handlers.adduser, store, args=[str(FRIEND)])
    assert "Added" in reply_text(u)
    u = call(handlers.menu, store, user=FRIEND)
    assert "Main menu" in reply_text(u)


def test_friend_cannot_use_owner_commands(store):
    store.add_user(FRIEND, OWNER)
    for fn, args in ((handlers.adduser, ["5"]), (handlers.removeuser, ["5"]), (handlers.users, [])):
        u = call(fn, store, user=FRIEND, args=args)
        assert_replied_once(u, handlers.OWNER_ONLY_MSG)
    assert not store.is_active_user(5)


def test_stranger_gets_private_message_not_owner_message(store):
    u = call(handlers.adduser, store, user=STRANGER, args=["5"])
    assert_replied_once(u, handlers.PRIVATE_MSG)


def test_removed_friend_loses_access(store):
    store.add_user(FRIEND, OWNER)
    call(handlers.removeuser, store, args=[str(FRIEND)])
    u = call(handlers.menu, store, user=FRIEND)
    assert_replied_once(u, handlers.PRIVATE_MSG)


def test_adduser_rejects_bad_input(store):
    for bad in ([], ["abc"], ["-5"], ["1", "2"]):
        u = call(handlers.adduser, store, args=bad)
        text = reply_text(u)
        assert "Usage" in text or "not a valid" in text


def test_owner_cannot_be_removed(store):
    u = call(handlers.removeuser, store, args=[str(OWNER)])
    assert "cannot be removed" in reply_text(u)


def test_users_lists_owners_and_friends(store):
    store.add_user(FRIEND, OWNER)
    text = reply_text(call(handlers.users, store))
    assert "Owners: 1" in text and str(FRIEND) in text


# ------------------------------------------------------------------- projects


def test_project_flow(store):
    assert "created" in reply_text(call(handlers.newproject, store, args=["Moon", "Token"]))
    call(handlers.setinfo, store, args=["blockchain", "Base"])
    call(handlers.setinfo, store, args=["goals", "Reach", "5k", "members"])
    call(handlers.note, store, args=["assumption:", "Audience", "is", "Nigerian"])
    text = reply_text(call(handlers.project, store))
    assert "Moon Token" in text and "Base" in text and "Reach 5k members" in text
    assert "1 active" in text
    notes_text = reply_text(call(handlers.notes, store))
    assert "[assumption]" in notes_text and "Nigerian" in notes_text


def test_commands_need_an_active_project(store):
    for fn, args in ((handlers.project, []), (handlers.notes, []), (handlers.note, ["hi"]),
                     (handlers.setinfo, ["goals", "x"])):
        assert "no active project" in reply_text(call(fn, store, args=args))


def test_setinfo_rejects_unknown_field(store):
    call(handlers.newproject, store, args=["Moon"])
    assert "Usage" in reply_text(call(handlers.setinfo, store, args=["colour", "blue"]))


def test_secrets_are_refused_and_not_stored(store):
    call(handlers.newproject, store, args=["Moon"])
    seed = "abandon ability able about above absent absorb abstract absurd abuse access accident"
    u = call(handlers.note, store, args=seed.split())
    assert "I never store" in reply_text(u)
    u = call(handlers.setinfo, store, args=["description", "key", "0x" + "a" * 64])
    assert "I never store" in reply_text(u)
    project = store.get_active_project(OWNER)
    assert store.list_notes(OWNER, project.id) == []
    assert store.get_project(OWNER, project.id).description is None


def test_outdate_and_delete_note(store):
    call(handlers.newproject, store, args=["Moon"])
    call(handlers.note, store, args=["fact:", "Launch", "Friday"])
    pid = store.get_active_project(OWNER).id
    nid = store.list_notes(OWNER, pid)[0].id
    call(handlers.outdate, store, args=[str(nid)])
    assert "OUTDATED" in reply_text(call(handlers.notes, store))
    call(handlers.delnote, store, args=[str(nid)])
    assert store.list_notes(OWNER, pid) == []


def test_delete_project_needs_confirmation(store):
    call(handlers.newproject, store, args=["Moon"])
    u = call(handlers.deleteproject, store)
    assert "cannot be undone" in reply_text(u)
    assert len(store.list_projects(OWNER)) == 1  # not deleted yet
    pid = store.list_projects(OWNER)[0].id
    call(handlers.on_button, store, data=f"proj:delyes:{pid}")
    assert store.list_projects(OWNER) == []


def test_cancel_delete_keeps_project(store):
    call(handlers.newproject, store, args=["Moon"])
    call(handlers.on_button, store, data="proj:delno")
    assert len(store.list_projects(OWNER)) == 1


# --------------------------------------------------------- isolation via buttons


def test_friend_cannot_switch_to_or_delete_owners_project_with_a_button(store):
    store.add_user(FRIEND, OWNER)
    call(handlers.newproject, store, args=["Owner Secret"])
    owner_pid = store.list_projects(OWNER)[0].id
    mine = store.create_project(FRIEND, "Friend Project")

    u = call(handlers.on_button, store, user=FRIEND, data=f"proj:use:{owner_pid}")
    assert "could not find" in u.callback_query.answer.call_args.args[0]
    assert store.get_active_project(FRIEND).id == mine.id

    call(handlers.on_button, store, user=FRIEND, data=f"proj:delyes:{owner_pid}")
    assert len(store.list_projects(OWNER)) == 1


def test_switching_projects_with_buttons(store):
    call(handlers.newproject, store, args=["One"])
    call(handlers.newproject, store, args=["Two"])
    first = store.list_projects(OWNER)[0]
    call(handlers.on_button, store, data=f"proj:use:{first.id}")
    assert store.get_active_project(OWNER).name == "One"


# --------------------------------------------------------------------- menu


def test_menu_has_ten_items_and_only_built_ones_are_ticked():
    assert len(keyboards.MENU_ITEMS) == 10
    labels = [row[0].text for row in keyboards.main_menu().inline_keyboard]
    assert sum("\U0001f51c" in label for label in labels) == 6
    assert sum("\u2705" in label for label in labels) == 4  # Crypto, Project Management, Memory, Settings


def test_soon_button_explains(store):
    u = call(handlers.on_button, store, data="soon:chat")
    assert "Phase 3" in u.callback_query.answer.call_args.args[0]
    u = call(handlers.on_button, store, data="soon:voice")
    assert "later" in u.callback_query.answer.call_args.args[0]


def test_memory_and_settings_buttons(store):
    call(handlers.newproject, store, args=["Moon"])
    u = call(handlers.on_button, store, data="menu:memory")
    assert "Your projects" in u.callback_query.edit_message_text.call_args.args[0]
    u = call(handlers.on_button, store, data="menu:settings")
    assert "Moon" in u.callback_query.edit_message_text.call_args.args[0]


def test_stranger_cannot_use_buttons(store):
    u = call(handlers.on_button, store, user=STRANGER, data="menu:settings")
    u.callback_query.edit_message_text.assert_not_awaited()


def test_unknown_button_does_not_crash(store):
    u = call(handlers.on_button, store, data="weird:thing")
    assert "did not recognise" in u.callback_query.edit_message_text.call_args.args[0]


# -------------------------------------------------------------- app building


def test_application_builds_with_fake_token_and_no_network(store):
    from assistant.interfaces.telegram.app import COMMAND_HANDLERS, build_application

    settings = Settings(_env_file=None, telegram_bot_token="123456:FAKE-token-for-tests", admin_user_ids="1")
    app = build_application(settings, store, FakeService())
    assert app.bot_data["settings"] is settings
    assert app.bot_data["db"] is store
    assert len(app.handlers[0]) == len(COMMAND_HANDLERS) + 2  # commands + button + text handlers


def test_building_without_token_raises_clear_error(store):
    from assistant.interfaces.telegram.app import build_application

    with pytest.raises(ValueError):
        build_application(Settings(_env_file=None), store)


# ------------------------------------------------------------------- tasks


def _project(store, name="Moon"):
    return store.create_project(OWNER, name)


def _all_tasks(store, status="open"):
    return store.list_tasks(OWNER, store.get_active_project(OWNER).id, status)


def test_dashboard_needs_a_project(store):
    u = call(handlers.on_button, store, data="menu:pm")
    assert "no active project" in u.callback_query.edit_message_text.call_args.args[0]


def test_dashboard_shows_counts(store):
    _project(store)
    call(handlers.task, store, args=["Post", "thread", "by", "tomorrow"])
    u = call(handlers.on_button, store, data="menu:pm")
    text = u.callback_query.edit_message_text.call_args.args[0]
    assert "Open tasks: 1" in text and "Post thread" in text and "tomorrow" in text


def test_add_task_with_buttons_and_text_reply(store):
    _project(store)
    ctx = make_context(store)
    u = call(handlers.on_button, store, data="task:add", ctx=ctx)
    assert "Send the task" in u.callback_query.edit_message_text.call_args.args[0]
    u = call(handlers.on_text, store, ctx=ctx, text="Design banner by friday")
    task = _all_tasks(store)[0]
    assert task.title == "Design banner" and task.due_date == date(2026, 10, 16)
    assert "Fri 16 Oct 2026" in reply_text(u)  # the resolved date is shown back to the user
    assert "await" not in ctx.user_data


def test_unreadable_deadline_still_saves_task_and_warns(store):
    _project(store)
    u = call(handlers.task, store, args=["Post", "thread", "by", "soonish"])
    assert "could not read the deadline" in reply_text(u)
    assert _all_tasks(store)[0].due_date is None


def test_plain_text_without_a_question_gets_honest_reply(store):
    u = call(handlers.on_text, store, text="hello bot")
    assert "cannot hold conversations" in reply_text(u)


def test_stranger_text_is_refused(store):
    u = call(handlers.on_text, store, user=STRANGER, text="hello")
    assert_replied_once(u, handlers.PRIVATE_MSG)


def test_expired_question_is_forgotten(store):
    _project(store)
    ctx = make_context(store)
    call(handlers.on_button, store, data="task:add", ctx=ctx)
    ctx.user_data["await"]["at"] = time.time() - 3600
    u = call(handlers.on_text, store, ctx=ctx, text="too late task")
    assert "cannot hold conversations" in reply_text(u)
    assert _all_tasks(store) == []


def test_any_button_cancels_pending_question(store):
    _project(store)
    ctx = make_context(store)
    call(handlers.on_button, store, data="task:add", ctx=ctx)
    call(handlers.on_button, store, data="task:cancel", ctx=ctx)
    assert "await" not in ctx.user_data


def test_cancel_command(store):
    _project(store)
    ctx = make_context(store)
    call(handlers.on_button, store, data="task:add", ctx=ctx)
    assert reply_text(call(handlers.cancel, store, ctx=ctx)) == "Cancelled."
    assert reply_text(call(handlers.cancel, store, ctx=ctx)) == "Nothing to cancel."


def test_secret_in_task_is_refused_and_question_stays_open(store):
    _project(store)
    ctx = make_context(store)
    call(handlers.on_button, store, data="task:add", ctx=ctx)
    u = call(handlers.on_text, store, ctx=ctx, text="0x" + "a" * 64)
    assert "I never store" in reply_text(u)
    assert _all_tasks(store) == [] and "await" in ctx.user_data


def test_mark_done_and_reopen_with_buttons(store):
    _project(store)
    call(handlers.task, store, args=["Ship"])
    tid = _all_tasks(store)[0].id
    u = call(handlers.on_button, store, data=f"task:done:{tid}")
    assert u.callback_query.answer.call_args.args[0] == "Marked as done"
    assert _all_tasks(store) == [] and len(_all_tasks(store, "done")) == 1
    call(handlers.on_button, store, data=f"task:reopen:{tid}")
    assert len(_all_tasks(store)) == 1


def test_set_deadline_assignee_and_notes_by_button(store):
    _project(store)
    call(handlers.task, store, args=["Ship"])
    tid = _all_tasks(store)[0].id
    ctx = make_context(store)
    call(handlers.on_button, store, data=f"task:due:{tid}", ctx=ctx)
    u = call(handlers.on_text, store, ctx=ctx, text="20/10/2026")
    assert "Tue 20 Oct 2026" in reply_text(u)
    call(handlers.on_button, store, data=f"task:assign:{tid}", ctx=ctx)
    call(handlers.on_text, store, ctx=ctx, text="Ada")
    call(handlers.on_button, store, data=f"task:notes:{tid}", ctx=ctx)
    call(handlers.on_text, store, ctx=ctx, text="needs the logo first")
    got = store.get_task(OWNER, tid)
    assert (got.due_date, got.assignee, got.notes) == (date(2026, 10, 20), "Ada", "needs the logo first")
    call(handlers.on_button, store, data=f"task:due:{tid}", ctx=ctx)
    call(handlers.on_text, store, ctx=ctx, text="none")
    assert store.get_task(OWNER, tid).due_date is None


def test_bad_date_keeps_asking(store):
    _project(store)
    call(handlers.task, store, args=["Ship"])
    tid = _all_tasks(store)[0].id
    ctx = make_context(store)
    call(handlers.on_button, store, data=f"task:due:{tid}", ctx=ctx)
    u = call(handlers.on_text, store, ctx=ctx, text="someday")
    assert "could not read that date" in reply_text(u)
    assert "await" in ctx.user_data and store.get_task(OWNER, tid).due_date is None


def test_overdue_tasks_are_flagged_in_list_and_detail(store):
    _project(store)
    call(handlers.task, store, args=["Old", "thing", "by", "2026-10-01"])
    u = call(handlers.on_button, store, data="task:list")
    labels = [row[0].text for row in u.callback_query.edit_message_text.call_args.kwargs["reply_markup"].inline_keyboard]
    assert "\U0001f534" in labels[0]
    tid = _all_tasks(store)[0].id
    u = call(handlers.on_button, store, data=f"task:view:{tid}")
    assert "OVERDUE by 14 days" in u.callback_query.edit_message_text.call_args.args[0]


def test_delete_task_asks_first(store):
    _project(store)
    call(handlers.task, store, args=["Ship"])
    tid = _all_tasks(store)[0].id
    u = call(handlers.on_button, store, data=f"task:del:{tid}")
    assert "cannot be undone" in u.callback_query.edit_message_text.call_args.args[0]
    assert len(_all_tasks(store)) == 1
    call(handlers.on_button, store, data=f"task:delyes:{tid}")
    assert _all_tasks(store) == []


def test_friend_cannot_reach_owners_tasks_through_buttons(store):
    store.add_user(FRIEND, OWNER)
    _project(store)
    call(handlers.task, store, args=["Owner", "secret"])
    tid = _all_tasks(store)[0].id
    for data in (f"task:view:{tid}", f"task:done:{tid}", f"task:del:{tid}",
                 f"task:delyes:{tid}", f"task:due:{tid}"):
        u = call(handlers.on_button, store, user=FRIEND, data=data)
        assert "could not find" in u.callback_query.answer.call_args.args[0]
    assert store.get_task(OWNER, tid).status == "open"
    assert len(_all_tasks(store)) == 1


def test_friend_cannot_hijack_a_question_for_owners_task(store):
    store.add_user(FRIEND, OWNER)
    _project(store)
    call(handlers.task, store, args=["Owner", "secret"])
    tid = _all_tasks(store)[0].id
    ctx = make_context(store)  # friend's own session state
    ctx.user_data["await"] = {"kind": "assign", "at": time.time(), "task_id": tid}
    u = call(handlers.on_text, store, user=FRIEND, ctx=ctx, text="Mallory")
    assert "could not find" in reply_text(u)
    assert store.get_task(OWNER, tid).assignee is None


def test_deleting_a_project_removes_its_tasks_from_view(store):
    _project(store)
    call(handlers.task, store, args=["Ship"])
    pid = store.get_active_project(OWNER).id
    call(handlers.on_button, store, data=f"proj:delyes:{pid}")
    u = call(handlers.tasks, store)
    assert "no active project" in reply_text(u)


# ------------------------------------------------------------------ crypto


def all_replies(update):
    return [c.args[0] for c in update.effective_message.reply_text.call_args_list]


def test_crypto_menu_button_then_address_gives_a_report(store):
    ctx = make_context(store)
    u = call(handlers.on_button, store, data="menu:crypto", ctx=ctx)
    assert "token address" in u.callback_query.edit_message_text.call_args.args[0]
    u = call(handlers.on_text, store, ctx=ctx, text=ADDR.upper().replace("0X", "0x"))
    replies = all_replies(u)
    assert "Checking that token" in replies[0] and "TOKEN REPORT" in "\n".join(replies)
    assert ctx.bot_data["crypto"].calls == [(ADDR, None)]
    assert "await" not in ctx.user_data


def test_pasting_an_address_anywhere_triggers_a_lookup(store):
    ctx = make_context(store)
    u = call(handlers.on_text, store, ctx=ctx, text=f"what about {ADDR} please?")
    assert "TOKEN REPORT" in "\n".join(all_replies(u))


def test_report_parts_fit_in_telegram_messages(store):
    u = call(handlers.on_text, store, text=ADDR)
    assert all(len(r) <= 4096 for r in all_replies(u))


def test_token_command_with_chain(store):
    ctx = make_context(store, args=[ADDR, "BNB"])
    call(handlers.token, store, ctx=ctx)
    assert ctx.bot_data["crypto"].calls == [(ADDR, "bsc")]


def test_token_command_rejects_bad_input_without_calling_providers(store):
    for args in ([], ["0x123"], [ADDR, "polygon"], [ADDR, "base", "extra"]):
        ctx = make_context(store, args=args)
        u = call(handlers.token, store, ctx=ctx)
        assert ctx.bot_data["crypto"].calls == []
        assert "Usage" in reply_text(u) or "does not look like" in reply_text(u) or "only support" in reply_text(u)


def test_bad_text_while_waiting_for_an_address_keeps_waiting(store):
    ctx = make_context(store)
    call(handlers.on_button, store, data="menu:crypto", ctx=ctx)
    u = call(handlers.on_text, store, ctx=ctx, text="hello there")
    assert "Solana" in reply_text(u) and "await" in ctx.user_data
    assert ctx.bot_data["crypto"].calls == []


def test_per_person_lookup_limit(store):
    ctx = make_context(store, limit=1)
    call(handlers.on_text, store, ctx=ctx, text=ADDR)
    u = call(handlers.on_text, store, ctx=ctx, text=ADDR)
    assert "limit of 8" in reply_text(u) or "limit of" in reply_text(u)
    assert len(ctx.bot_data["crypto"].calls) == 1


def test_lookup_limits_are_per_person(store):
    store.add_user(FRIEND, OWNER)
    ctx = make_context(store, limit=1)
    call(handlers.on_text, store, ctx=ctx, text=ADDR)
    u = call(handlers.on_text, store, user=FRIEND, ctx=ctx, text=ADDR)
    assert "TOKEN REPORT" in "\n".join(all_replies(u))


def test_friend_can_research_but_stranger_cannot(store):
    store.add_user(FRIEND, OWNER)
    ctx = make_context(store)
    call(handlers.token, store, user=FRIEND, ctx=ctx)  # no args, only checks access path
    u = call(handlers.on_text, store, user=STRANGER, ctx=ctx, text=ADDR)
    assert_replied_once(u, handlers.PRIVATE_MSG)
    assert ctx.bot_data["crypto"].calls == []


def test_research_errors_are_shown_plainly(store):
    ctx = make_context(store, service=FakeService(error=ResearchError("I am handling a lot of lookups right now.")))
    u = call(handlers.on_text, store, ctx=ctx, text=ADDR)
    assert "handling a lot" in reply_text(u)


def test_pasted_private_key_is_never_processed(store):
    ctx = make_context(store)
    u = call(handlers.on_text, store, ctx=ctx, text="0x" + "a" * 64)
    assert "Never share" in reply_text(u) and ctx.bot_data["crypto"].calls == []


def test_status_shows_lookup_counts(store):
    u = call(handlers.status, store)
    assert "Token lookups since start" in reply_text(u)


# --------------------------------------------------------------- campaigns


def _camp_ctx(store):
    _project(store)
    return make_context(store)


def _first_campaign(store):
    return store.list_campaigns(OWNER, store.get_active_project(OWNER).id)[0]


def test_campaign_guided_flow_with_skip_and_clear(store):
    ctx = _camp_ctx(store)
    call(handlers.on_button, store, data="camp:new", ctx=ctx)
    u = call(handlers.on_text, store, ctx=ctx, text="Launch push")
    assert "Objective (1 of 10)" in reply_text(u)
    u = call(handlers.on_text, store, ctx=ctx, text="Reach 3k members")
    assert "Target audience (2 of 10)" in reply_text(u)
    cid = _first_campaign(store).id
    u = call(handlers.on_button, store, data=f"camp:fill:{cid}:2", ctx=ctx)  # skip audience
    assert "Strategy (3 of 10)" in u.callback_query.edit_message_text.call_args.args[0]
    call(handlers.on_text, store, ctx=ctx, text="Twitter spaces and AMAs")
    got = store.get_campaign(OWNER, cid)
    assert (got.objective, got.audience, got.strategy) == ("Reach 3k members", None, "Twitter spaces and AMAs")
    call(handlers.on_text, store, ctx=ctx, text="none")  # clears activities, moves on
    assert store.get_campaign(OWNER, cid).activities is None


def test_campaign_guided_flow_finishes_with_the_brief(store):
    ctx = _camp_ctx(store)
    call(handlers.on_button, store, data="camp:new", ctx=ctx)
    call(handlers.on_text, store, ctx=ctx, text="Launch push")
    u = None
    for answer in range(10):
        u = call(handlers.on_text, store, ctx=ctx, text=f"answer {answer}")
    text = reply_text(u)
    assert "Campaign: Launch push" in text and "answer 9" in text and "await" not in ctx.user_data


def test_edit_one_campaign_field(store):
    ctx = _camp_ctx(store)
    store.add_campaign(OWNER, store.get_active_project(OWNER).id, "Launch push")
    cid = _first_campaign(store).id
    u = call(handlers.on_button, store, data=f"camp:edit:{cid}:metrics", ctx=ctx)
    assert "Impressions alone do not prove growth" in u.callback_query.edit_message_text.call_args.args[0]
    u = call(handlers.on_text, store, ctx=ctx, text="3k members, positive sentiment")
    assert store.get_campaign(OWNER, cid).metrics == "3k members, positive sentiment"
    assert "Success metrics: 3k members" in reply_text(u)


def test_campaign_list_and_view_screens(store):
    ctx = _camp_ctx(store)
    store.add_campaign(OWNER, store.get_active_project(OWNER).id, "Launch push")
    u = call(handlers.on_button, store, data="camp:list", ctx=ctx)
    assert "(1)" in u.callback_query.edit_message_text.call_args.args[0]
    cid = _first_campaign(store).id
    u = call(handlers.on_button, store, data=f"camp:view:{cid}", ctx=ctx)
    assert "Objective: not set" in u.callback_query.edit_message_text.call_args.args[0]


def test_campaign_delete_needs_confirmation(store):
    ctx = _camp_ctx(store)
    store.add_campaign(OWNER, store.get_active_project(OWNER).id, "Launch push")
    cid = _first_campaign(store).id
    u = call(handlers.on_button, store, data=f"camp:del:{cid}", ctx=ctx)
    assert "cannot be undone" in u.callback_query.edit_message_text.call_args.args[0]
    assert len(store.list_campaigns(OWNER, store.get_active_project(OWNER).id)) == 1
    call(handlers.on_button, store, data=f"camp:delyes:{cid}", ctx=ctx)
    assert store.list_campaigns(OWNER, store.get_active_project(OWNER).id) == []


def test_secret_in_campaign_is_refused_and_question_stays_open(store):
    ctx = _camp_ctx(store)
    call(handlers.on_button, store, data="camp:new", ctx=ctx)
    u = call(handlers.on_text, store, ctx=ctx, text="0x" + "a" * 64)
    assert "I never store" in reply_text(u) and "await" in ctx.user_data


def test_campaign_needs_an_active_project(store):
    u = call(handlers.on_button, store, data="camp:new")
    assert "no active project" in u.callback_query.answer.call_args.args[0]


def test_friend_cannot_reach_owners_campaign(store):
    store.add_user(FRIEND, OWNER)
    ctx = _camp_ctx(store)
    store.add_campaign(OWNER, store.get_active_project(OWNER).id, "Secret plan")
    cid = _first_campaign(store).id
    for data in (f"camp:view:{cid}", f"camp:edit:{cid}:objective", f"camp:fill:{cid}:0",
                 f"camp:del:{cid}", f"camp:delyes:{cid}"):
        u = call(handlers.on_button, store, user=FRIEND, data=data)
        assert "could not find" in u.callback_query.answer.call_args.args[0]
    assert len(store.list_campaigns(OWNER, store.get_active_project(OWNER).id)) == 1


def test_stranger_cannot_use_campaign_buttons(store):
    u = call(handlers.on_button, store, user=STRANGER, data="camp:list")
    u.callback_query.edit_message_text.assert_not_awaited()


def test_pasting_a_solana_address_triggers_a_lookup_with_exact_case(store):
    ctx = make_context(store)
    u = call(handlers.on_text, store, ctx=ctx, text=f"check {SOL_ADDR}")
    assert ctx.bot_data["crypto"].calls == [(SOL_ADDR, None)]
    assert "TOKEN REPORT" in "\n".join(all_replies(u))


def test_token_command_accepts_solana_with_chain_alias(store):
    ctx = make_context(store, args=[SOL_ADDR, "sol"])
    call(handlers.token, store, ctx=ctx)
    assert ctx.bot_data["crypto"].calls == [(SOL_ADDR, "solana")]


def test_a_pasted_bot_token_is_treated_as_a_secret_not_an_address(store):
    ctx = make_context(store)
    u = call(handlers.on_text, store, ctx=ctx, text="123456789:ABCdefGHIjklMNOpqrSTUvwxYZ0123456789")
    assert "Never share" in reply_text(u) and ctx.bot_data["crypto"].calls == []
