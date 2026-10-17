"""Who may use the bot. Identity comes from Telegram's own update data, never from message text."""


def is_authorized(user_id: int | None, admin_user_ids: list[int]) -> bool:
    return user_id is not None and user_id in admin_user_ids
