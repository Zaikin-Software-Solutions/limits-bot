"""Клиент limits API + типизированные модели ответа.

Эндпоинт (тот же rotate-proxy, что использует CCR) отдаёт массив credentials[],
по одному на аккаунт Claude. Структура — см. app/models описание ниже.
"""

from __future__ import annotations

from datetime import datetime

import httpx
from pydantic import BaseModel, ConfigDict


class Window(BaseModel):
    """Окно лимита (five_hour / seven_day)."""

    model_config = ConfigDict(extra="ignore")

    utilization_pct: float | None = None
    remaining_pct: float | None = None
    resets_at: datetime | None = None


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

    @property
    def name(self) -> str:
        return self.display_name or self.email


class LimitsResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    credentials: list[Credential] = []


async def fetch_limits(url: str, token: str, *, timeout: float = 10.0) -> LimitsResponse:
    """Забрать лимиты. Кидает httpx.HTTPError / pydantic ValidationError при проблемах."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})
        resp.raise_for_status()
        data = resp.json()
    if isinstance(data, dict) and data.get("error"):
        raise httpx.HTTPError(f"limits API error: {data['error']}")
    return LimitsResponse.model_validate(data)
