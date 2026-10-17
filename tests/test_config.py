import pytest

from assistant.config import Settings


def make(**kwargs):
    # _env_file=None so tests never read a real .env file
    return Settings(_env_file=None, **kwargs)


def test_defaults_work_without_any_secrets():
    s = make()
    assert s.telegram_bot_token is None
    assert s.image_generations_per_day == 2
    assert s.admin_user_ids == []


def test_blank_secret_counts_as_unset():
    assert make(telegram_bot_token="   ").telegram_bot_token is None


def test_admin_ids_parsed_from_comma_string():
    assert make(admin_user_ids="123, 456").admin_user_ids == [123, 456]


def test_bad_admin_ids_rejected():
    with pytest.raises(ValueError):
        make(admin_user_ids="abc")


def test_bad_log_level_rejected():
    with pytest.raises(ValueError):
        make(log_level="LOUD")


def test_secret_not_shown_when_settings_printed():
    s = make(telegram_bot_token="123456789:ABCdefGHIjklMNOpqrSTUvwxYZ0123456789")
    assert "ABCdef" not in repr(s)
    assert "ABCdef" not in str(s)


def test_blank_admin_ids_in_env_file_is_ok(tmp_path):
    env = tmp_path / ".env"
    env.write_text("ADMIN_USER_IDS=\nTELEGRAM_BOT_TOKEN=\n")
    s = Settings(_env_file=str(env))
    assert s.admin_user_ids == []
    assert s.telegram_bot_token is None


def test_admin_ids_from_env_file(tmp_path):
    env = tmp_path / ".env"
    env.write_text("ADMIN_USER_IDS=111,222\n")
    assert Settings(_env_file=str(env)).admin_user_ids == [111, 222]


def test_timezone_default_and_validation():
    assert make().timezone == "Africa/Lagos"
    with pytest.raises(ValueError):
        make(timezone="Mars/Base")
