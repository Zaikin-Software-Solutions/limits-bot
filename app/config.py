"""Конфигурация бота — читается из окружения / .env через pydantic-settings.

list-поля храним как строки (raw) и парсим геттерами: pydantic-settings пытается
json.loads() любое поле-коллекцию из ENV ДО валидаторов и падает на "a, b".
Поэтому сырьё — str, разбор — в property.
"""

from __future__ import annotations

from functools import cached_property

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Telegram
    bot_token: str = Field(alias="BOT_TOKEN")
    chat_id: int = Field(alias="CHAT_ID")
    allowed_user_ids_raw: str = Field(default="", alias="ALLOWED_USER_IDS")

    # Источник лимитов Claude
    limits_api_url: str = Field(
        default="https://rotate-proxy.rodionov.uk/v1/claude/limits",
        alias="LIMITS_API_URL",
    )
    limits_token: str = Field(alias="LIMITS_TOKEN")
    accounts_filter_raw: str = Field(default="", alias="ACCOUNTS_FILTER")

    # Источник лимитов Codex (ChatGPT-подписка через CLIProxyAPI).
    # Пусто = codex не мониторим. На ns1: http://127.0.0.1:8317/v1/codex/limits
    codex_api_url: str = Field(default="", alias="CODEX_API_URL")
    codex_token: str = Field(default="", alias="CODEX_TOKEN")
    # Публичный адрес того же zsp-прокси (через nginx+TLS): ловит поломку снаружи,
    # которую внутренний cli-proxy-api:8317 не видит. Токен тот же (CODEX_TOKEN).
    zsp_public_url: str = Field(
        default="https://rotate-proxy.zspzvs.ru/v1/codex/limits", alias="ZSP_PUBLIC_URL"
    )

    # Живые пробы: /models и /limits не выявляют умершую OAuth-сессию.
    claude_probe_model: str = Field(default="claude-sonnet-5-5", alias="CLAUDE_PROBE_MODEL")
    codex_probe_model: str = Field(default="gpt-6-luna", alias="CODEX_PROBE_MODEL")
    health_poll_interval_min: int = Field(default=10, alias="HEALTH_POLL_INTERVAL_MIN")

    # Ручное напоминание об обновлении логина Claude (дату вводит /token).
    claude_login_period_days: int = Field(default=30, alias="CLAUDE_LOGIN_PERIOD_DAYS")
    claude_login_warn_days: int = Field(default=2, alias="CLAUDE_LOGIN_WARN_DAYS")

    # Поведение
    poll_interval_min: int = Field(default=30, alias="POLL_INTERVAL_MIN")
    weekly_warn_hours: float = Field(default=2.0, alias="WEEKLY_WARN_HOURS")
    thresholds_raw: str = Field(default="80,95", alias="THRESHOLDS")
    extra_usage_warn_pct: float = Field(default=80.0, alias="EXTRA_USAGE_WARN_PCT")
    state_path: str = Field(default="/data/state.json", alias="STATE_PATH")

    @cached_property
    def allowed_user_ids(self) -> list[int]:
        return [int(x) for x in self.allowed_user_ids_raw.replace(",", " ").split() if x.strip()]

    @cached_property
    def accounts_filter(self) -> list[str]:
        return [x.strip() for x in self.accounts_filter_raw.split(",") if x.strip()]

    @cached_property
    def thresholds(self) -> list[int]:
        vals = [int(x) for x in self.thresholds_raw.replace(",", " ").split() if x.strip()]
        return sorted(vals) if vals else [80, 95]


settings = Settings()  # type: ignore[call-arg]
