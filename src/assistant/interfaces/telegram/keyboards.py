"""Menu layout. Features that are not built yet are clearly marked, never shown as ready."""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

# (key, label, ready, phase when it arrives)
MENU_ITEMS = [
    ("chat", "AI Chat", False, "Phase 3"),
    ("crypto", "Crypto Analysis", True, ""),
    ("pm", "Web3 Project Management", True, ""),
    ("growth", "Growth and Marketing", False, "Phase 3"),
    ("content", "Content Creation", False, "Phase 3"),
    ("vision", "Image Analysis", False, "later, when budget allows"),
    ("imagegen", "Image Generation", False, "later, when budget allows"),
    ("voice", "Voice Assistant", False, "later, when budget allows"),
    ("memory", "Project Memory", True, ""),
    ("settings", "Settings and Usage", True, ""),
]

BACK = "\u2b05\ufe0f Back to menu"


def main_menu() -> InlineKeyboardMarkup:
    rows = []
    for key, label, ready, _phase in MENU_ITEMS:
        if ready:
            rows.append([InlineKeyboardButton(f"\u2705 {label}", callback_data=f"menu:{key}")])
        else:
            rows.append([InlineKeyboardButton(f"\U0001f51c {label}", callback_data=f"soon:{key}")])
    rows.append([InlineKeyboardButton("\u2753 Help", callback_data="menu:help")])
    return InlineKeyboardMarkup(rows)


def back_to_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(BACK, callback_data="menu:main")]])


def projects_list(projects, active_id: int | None) -> InlineKeyboardMarkup:
    rows = []
    for project in projects:
        mark = "\u25b6\ufe0f " if project.id == active_id else ""
        rows.append(
            [InlineKeyboardButton(f"{mark}{project.name}", callback_data=f"proj:use:{project.id}")]
        )
    rows.append([InlineKeyboardButton(BACK, callback_data="menu:main")])
    return InlineKeyboardMarkup(rows)


def confirm_delete(project_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Yes, delete it", callback_data=f"proj:delyes:{project_id}"),
                InlineKeyboardButton("Cancel", callback_data="proj:delno"),
            ]
        ]
    )


def phase_for(key: str) -> str | None:
    for item_key, label, ready, phase in MENU_ITEMS:
        if item_key == key and not ready:
            if phase.startswith("Phase"):
                return f"{label} is not built yet. It arrives in {phase} of the plan."
            return f"{label} is not built yet. It is planned for {phase}."
    return None


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=data)


def _short(text: str, limit: int = 28) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"


def dashboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [_btn("\u2795 Add task", "task:add")],
            [_btn("\U0001f4cb Open tasks", "task:list"), _btn("\u2714\ufe0f Completed", "task:donelist")],
            [_btn("\U0001f4e3 Campaign briefs", "camp:list")],
            [_btn(BACK, "menu:main")],
        ]
    )


def tasks_list(tasks, today, done: bool = False) -> InlineKeyboardMarkup:
    rows = []
    for t in tasks[:20]:
        overdue = (not done) and t.due_date is not None and t.due_date < today
        flag = "\U0001f534 " if overdue else ("\u2714\ufe0f " if done else "")
        due = f" \u00b7 {t.due_date.day} {t.due_date.strftime('%b')}" if t.due_date else ""
        rows.append([_btn(f"{flag}#{t.id} {_short(t.title)}{due}", f"task:view:{t.id}")])
    rows.append([_btn("\u2795 Add task", "task:add")])
    if done:
        rows.append([_btn("\U0001f4cb Open tasks", "task:list")])
    else:
        rows.append([_btn("\u2714\ufe0f Completed", "task:donelist")])
    rows.append([_btn("\u2b05\ufe0f Back", "menu:pm")])
    return InlineKeyboardMarkup(rows)


def task_detail(task) -> InlineKeyboardMarkup:
    if task.status == "done":
        first = [_btn("\u21a9\ufe0f Reopen", f"task:reopen:{task.id}")]
    else:
        first = [_btn("\u2705 Mark done", f"task:done:{task.id}")]
    return InlineKeyboardMarkup(
        [
            first,
            [_btn("\U0001f4c5 Deadline", f"task:due:{task.id}"), _btn("\U0001f464 Assign", f"task:assign:{task.id}")],
            [_btn("\U0001f4dd Notes", f"task:notes:{task.id}"), _btn("\U0001f5d1 Delete", f"task:del:{task.id}")],
            [_btn("\u2b05\ufe0f Back to tasks", "task:list")],
        ]
    )


def confirm_task_delete(task_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[_btn("Yes, delete it", f"task:delyes:{task_id}"), _btn("Cancel", f"task:view:{task_id}")]]
    )


def cancel_prompt() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[_btn("Cancel", "task:cancel")]])


def after_add(task_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[_btn("\U0001f4cb Open tasks", "task:list"), _btn("\u2795 Add another", "task:add")]]
    )


def cancel_to_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[_btn("Cancel", "menu:main")]])


def campaigns_list(campaigns) -> InlineKeyboardMarkup:
    rows = [[_btn(f"\U0001f4e3 {_short(c.name, 40)}", f"camp:view:{c.id}")] for c in campaigns[:20]]
    rows.append([_btn("\u2795 New campaign", "camp:new")])
    rows.append([_btn("\u2b05\ufe0f Back", "menu:pm")])
    return InlineKeyboardMarkup(rows)


def campaign_view(campaign, fields) -> InlineKeyboardMarkup:
    rows = [[_btn("\u25b6\ufe0f Fill in step by step", f"camp:fill:{campaign.id}:0")]]
    pair = []
    for key, (label, _question) in fields.items():
        pair.append(_btn(f"\u270f\ufe0f {_short(label, 18)}", f"camp:edit:{campaign.id}:{key}"))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    rows.append([_btn("\U0001f5d1 Delete", f"camp:del:{campaign.id}"), _btn("\u2b05\ufe0f Back", "camp:list")])
    return InlineKeyboardMarkup(rows)


def guided_prompt(campaign_id: int, next_index: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[_btn("Skip", f"camp:fill:{campaign_id}:{next_index}"), _btn("Stop for now", f"camp:view:{campaign_id}")]]
    )


def edit_prompt(campaign_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[_btn("Cancel", f"camp:view:{campaign_id}")]])


def confirm_campaign_delete(campaign_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[_btn("Yes, delete it", f"camp:delyes:{campaign_id}"), _btn("Cancel", f"camp:view:{campaign_id}")]]
    )


def cancel_to_campaigns() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[_btn("Cancel", "camp:list")]])
