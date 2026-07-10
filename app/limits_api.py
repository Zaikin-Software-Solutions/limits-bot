"""Клиент limits API + типизированные модели ответа.

Эндпоинт (тот же rotate-proxy, что использует CCR) отдаёт массив credentials[],
по одному на аккаунт Claude. Структура — см. app/models описание ниже.
"""

from __future__ import annotations

from datetime import datetime

import httpx
from pydantic import BaseModel, ConfigDict


class Window(BaseModel):
    """Окно лимита (five_hour / seven_day). Codex дополнительно даёт resets_in_*."""

    model_config = ConfigDict(extra="ignore")

    utilization_pct: float | None = None
    remaining_pct: float | None = None
    resets_at: datetime | None = None
    resets_in_seconds: int | None = None
    resets_in_days: float | None = None


class Limits(BaseModel):
    model_config = ConfigDict(extra="ignore")

    five_hour: Window | None = None
    seven_day: Window | None = None
    seven_day_sonnet: Window | None = None
    seven_day_opus: Window | None = None


class ExtraUsage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    is_enabled: bool = False
    monthly_limit: float | None = None
    used_credits: float | None = None
    remaining_credits: float | None = None
    utilization_pct: float | None = None
    currency: str | None = None


class Quota(BaseModel):
    model_config = ConfigDict(extra="ignore")

    exceeded: bool = False
    next_recover_at: datetime | None = None


class Credential(BaseModel):
    model_config = ConfigDict(extra="ignore")

    email: str
    display_name: str | None = None
    plan: str | None = None
    rate_limit_tier: str | None = None
    subscription_status: str | None = None
    limits: Limits = Limits()
    extra_usage: ExtraUsage = ExtraUsage()
    quota: Quota = Quota()
    # Проставляется после fetch: "claude" | "codex". В снапшоте ключ = provider:email.
    provider: str = "claude"

    @property
    def name(self) -> str:
        return self.display_name or self.email

    @property
    def key(self) -> str:
        """Уникальный ключ аккаунта в снапшоте (provider отделяет claude от codex)."""
        return f"{self.provider}:{self.email}"

    @property
    def label(self) -> str:
        """Человекочитаемая метка с иконкой провайдера."""
        icon = "🟠" if self.provider == "codex" else "🟣"
        return f"{icon} {self.name}"


class LimitsResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    credentials: list[Credential] = []


async def _fetch(url: str, token: str, timeout: float) -> dict:
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})
        resp.raise_for_status()
        data = resp.json()
    if isinstance(data, dict) and data.get("error"):
        raise httpx.HTTPError(f"limits API error: {data['error']}")
    return data


async def fetch_limits(
    url: str, token: str, *, provider: str = "claude", timeout: float = 10.0
) -> LimitsResponse:
    """Забрать лимиты (claude или codex — форма ответа одинаковая: {credentials:[...]}).

    Кидает httpx.HTTPError / pydantic ValidationError при проблемах.
    """
    data = await _fetch(url, token, timeout)
    resp = LimitsResponse.model_validate(data)
    for c in resp.credentials:
        c.provider = provider
    return resp


def _models_url(limits_url: str) -> str:
    """Из .../v1/claude/limits собрать .../v1/models на том же хосте."""
    base = limits_url.split("/v1/")[0]
    return f"{base}/v1/models"


async def fetch_models(limits_url: str, token: str, *, timeout: float = 10.0) -> list[tuple[str, str]]:
    """Список (owned_by, id) доступных через прокси моделей."""
    url = _models_url(limits_url)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})
        resp.raise_for_status()
        data = resp.json()
    items = data.get("data", []) if isinstance(data, dict) else []
    return [(m.get("owned_by", "?"), m.get("id", "?")) for m in items]
