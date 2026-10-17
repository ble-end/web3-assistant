"""Application settings, loaded from environment variables / the .env file.

Secrets are stored as SecretStr so they are hidden if a setting is ever printed.
"""
from functools import lru_cache
from typing import Annotated

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_env: str = "development"
    log_level: str = "INFO"

    # Credentials (optional until the phase that needs them)
    telegram_bot_token: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None

    # Access control: Telegram user IDs allowed as administrators
    # NoDecode: read the raw text from .env and let our validator below parse it
    admin_user_ids: Annotated[list[int], NoDecode] = Field(default_factory=list)

    database_url: str = "sqlite:///data/assistant.db"
    timezone: str = "Africa/Lagos"  # used for "today" and deadlines

    # Limits
    history_message_limit: int = Field(default=20, ge=1, le=200)
    image_generations_per_day: int = Field(default=2, ge=0)
    crypto_lookups_per_5_min: int = Field(default=8, ge=1, le=100)  # per person
    max_image_mb: int = Field(default=10, ge=1)
    max_voice_seconds: int = Field(default=120, ge=1)
    max_voice_mb: int = Field(default=20, ge=1)

    @field_validator("admin_user_ids", mode="before")
    @classmethod
    def _parse_admin_ids(cls, value):
        """Accept '123,456' from the .env file (empty string means no admins)."""
        if value is None or value == "":
            return []
        if isinstance(value, str):
            try:
                return [int(v.strip()) for v in value.split(",") if v.strip()]
            except ValueError as exc:
                raise ValueError(
                    "ADMIN_USER_IDS must be numbers separated by commas, e.g. 12345,67890"
                ) from exc
        return value

    @field_validator("telegram_bot_token", "anthropic_api_key", mode="before")
    @classmethod
    def _blank_is_none(cls, value):
        """A blank value in .env (KEY=) counts as 'not set'."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("timezone")
    @classmethod
    def _check_timezone(cls, value: str) -> str:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(
                "TIMEZONE must be a valid name such as Africa/Lagos "
                "(on Windows this needs the tzdata package: pip install tzdata)"
            ) from exc
        return value

    @field_validator("log_level")
    @classmethod
    def _check_log_level(cls, value: str) -> str:
        value = value.upper()
        if value not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR or CRITICAL")
        return value

    def secret_values(self) -> list[str]:
        """Real secret values, used so the logger can hide them."""
        secrets = [self.telegram_bot_token, self.anthropic_api_key]
        return [s.get_secret_value() for s in secrets if s is not None]


@lru_cache
def get_settings() -> Settings:
    return Settings()
