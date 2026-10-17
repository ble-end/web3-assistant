from assistant.main import run


def test_app_starts_without_secrets(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # no .env here
    for var in ("TELEGRAM_BOT_TOKEN", "ANTHROPIC_API_KEY", "ADMIN_USER_IDS"):
        monkeypatch.delenv(var, raising=False)
    from assistant.config import get_settings
    get_settings.cache_clear()
    assert run() == 0


def test_bad_config_returns_error_not_crash(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LOG_LEVEL", "LOUD")
    from assistant.config import get_settings
    get_settings.cache_clear()
    assert run() == 1
    assert "Configuration error" in capsys.readouterr().out
    get_settings.cache_clear()
