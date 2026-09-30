"""Live proxy probes (rodionov, zsp internal + public), Claude status, per-model matrix."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from html import escape
from typing import Any

import httpx
from aiogram import Bot

from .config import settings
from .limits_api import _models_url, fetch_models
from .reauth import login_reminder, today_msk
from .state import State

log = logging.getLogger(__name__)
CLAUDE_STATUS_URL = "https://status.claude.com/api/v2/summary.json"
HEALTH_KEYS = ("claude", "status", "codex", "zsp_public")
MODEL_PROBE_CONCURRENCY = 6


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool | None
    detail: str
    latency_ms: int | None = None
    auth_failure: bool = False


def _chat_url(limits_url: str) -> str:
    return _models_url(limits_url).removesuffix("/models") + "/chat/completions"


def _is_chat_model(model_id: str) -> bool:
    """Image models do not serve chat completions; probing them only costs money."""
    return "image" not in model_id.lower()


async def probe_model(
    name: str, limits_url: str, token: str, model: str, *, relaxed: bool = False
) -> Check:
    """A real completion, since /models and /limits can work with dead OAuth.

    relaxed=True accepts any well-formed 200 with choices: reasoning models can
    spend the tiny token budget before emitting text and are still alive.
    """
    if not limits_url or not token or not model:
        return Check(name, None, "не настроен")
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                _chat_url(limits_url),
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "Reply with OK."}],
                    "max_tokens": 8,
                    "stream": False,
                },
            )
        latency = round((time.monotonic() - started) * 1000)
        if response.status_code != 200:
            # The upstream body may contain tokens or account data. Classify only.
            body = response.text.lower()
            auth_failure = response.status_code in (401, 403) or any(
                marker in body
                for marker in ("auth_unavailable", "invalid_grant", "reauth", "refresh token")
            )
            reason = "авторизация недоступна" if auth_failure else f"HTTP {response.status_code}"
            return Check(name, False, reason, latency, auth_failure)
        payload = response.json()
        choices = payload.get("choices", [])
        if relaxed:
            if choices:
                return Check(name, True, "отвечает", latency)
            return Check(name, False, "пустой ответ модели", latency)
        content = choices[0].get("message", {}).get("content") if choices else None
        if not content:
            return Check(name, False, "пустой ответ модели", latency)
        return Check(name, True, f"ответ модели {model}", latency)
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        log.warning("%s probe failed: %s", name, type(exc).__name__)
        return Check(name, False, "ошибка соединения или ответа")


async def check_claude_status() -> Check:
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(CLAUDE_STATUS_URL)
        response.raise_for_status()
        data = response.json()
        incidents = data.get("incidents", [])
        indicator = data.get("status", {}).get("indicator")
        if not isinstance(incidents, list) or not isinstance(indicator, str):
            raise ValueError("invalid status page response")
        if incidents or indicator != "none":
            names = ", ".join(str(item.get("name", "инцидент")) for item in incidents[:2])
            return Check("Claude Status", False, names or f"статус: {indicator}")
        return Check("Claude Status", True, "нет открытых инцидентов")
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        log.warning("Claude status fetch failed: %s", type(exc).__name__)
        return Check("Claude Status", None, "статус недоступен")


async def probe_source_models(
    limits_url: str, token: str
) -> tuple[list[Check], str | None]:
    """Probe every chat model a proxy advertises. Returns (checks, error)."""
    if not limits_url or not token:
        return [], "не настроен"
    try:
        models = await fetch_models(limits_url, token)
    except (httpx.HTTPError, ValueError):
        return [], "список моделей недоступен"
    ids = sorted({model_id for _, model_id in models if _is_chat_model(model_id)})
    semaphore = asyncio.Semaphore(MODEL_PROBE_CONCURRENCY)

    async def one(model_id: str) -> Check:
        async with semaphore:
            return await probe_model(model_id, limits_url, token, model_id, relaxed=True)

    return list(await asyncio.gather(*(one(model_id) for model_id in ids))), None


def format_check(check: Check) -> str:
    icon = "✅" if check.ok is True else "❌" if check.ok is False else "❔"
    latency = f" · {check.latency_ms} мс" if check.latency_ms is not None else ""
    return f"{icon} <b>{escape(check.name)}</b>: {escape(check.detail)}{latency}"


def format_report(checks: list[Check], history: dict[str, Any] | None = None) -> str:
    lines = ["🔎 <b>Проверка соединения</b>", "", *(format_check(c) for c in checks)]
    if history:
        samples = history.get("claude", {}).get("samples", [])
        if samples:
            good = sum(sample is True for sample in samples)
            if len(samples) < 6:
                verdict = "недостаточно истории"
            else:
                verdict = "стабилен за 6 опросов" if good == 6 else "были сбои за 6 опросов"
            lines.append(f"📈 Claude: {good}/{len(samples)} успешных · {verdict}")
    lines.extend(["", "<a href=\"https://status.claude.com/\">Claude Status</a>"])
    return "\n".join(lines)


def format_models_report(sections: list[tuple[str, list[Check], str | None]]) -> str:
    lines = ["🤖 <b>Статус моделей</b> (реальный запрос к каждой)"]
    for title, checks, error in sections:
        lines.append("")
        if error:
            lines.append(f"<b>{escape(title)}</b>: ❔ {escape(error)}")
            continue
        good = sum(check.ok is True for check in checks)
        lines.append(f"<b>{escape(title)}</b> — {good}/{len(checks)} отвечают")
        for check in checks:
            if check.ok:
                tail = f"{check.latency_ms} мс" if check.latency_ms is not None else "ok"
                lines.append(f"✅ <code>{escape(check.name)}</code> · {tail}")
            else:
                lines.append(f"❌ <code>{escape(check.name)}</code> · {escape(check.detail)}")
    return "\n".join(lines)


async def run_checks() -> list[Check]:
    claude, status, codex, zsp_public = await asyncio.gather(
        probe_model("rodionov → Claude", settings.limits_api_url, settings.limits_token,
                    settings.claude_probe_model),
        check_claude_status(),
        probe_model("zsp (внутренний) → Codex", settings.codex_api_url, settings.codex_token,
                    settings.codex_probe_model),
        probe_model("zsp (публичный) → Codex", settings.zsp_public_url, settings.codex_token,
                    settings.codex_probe_model),
    )
    return [claude, status, codex, zsp_public]


async def run_model_matrix() -> list[tuple[str, list[Check], str | None]]:
    """Per-model status for the two proxies users reach over the internet."""
    (rodionov, rodionov_err), (zsp, zsp_err) = await asyncio.gather(
        probe_source_models(settings.limits_api_url, settings.limits_token),
        probe_source_models(settings.zsp_public_url, settings.codex_token),
    )
    return [
        ("🟣 rotate-proxy rodionov", rodionov, rodionov_err),
        ("🟠 rotate-proxy zsp", zsp, zsp_err),
    ]


def _update_check_state(history: dict[str, Any], key: str, check: Check) -> str | None:
    """Deduplicate alerts; auth failures and status incidents alert immediately."""
    if check.ok is None:
        return None
    item = history.setdefault(key, {})
    samples = (item.get("samples", []) + [check.ok])[-6:]
    item["samples"] = samples
    if check.ok:
        item["fails"] = 0
        item["goods"] = item.get("goods", 0) + 1
        if item.get("alerted") and item["goods"] >= 2:
            item["alerted"] = False
            return f"✅ Восстановлено: {escape(check.name)}"
        return None
    item["goods"] = 0
    item["fails"] = item.get("fails", 0) + 1
    if not item.get("alerted") and (
        check.auth_failure or key == "status" or item["fails"] >= 2
    ):
        item["alerted"] = True
        return f"🚨 Проблема: {format_check(check)}"
    return None


async def poll_health_once(bot: Bot, state: State) -> None:
    checks = await run_checks()
    history = state.health()
    alerts = []
    for key, check in zip(HEALTH_KEYS, checks, strict=True):
        alert = _update_check_state(history, key, check)
        if alert:
            alerts.append(alert)
    reminder = login_reminder(
        state.login(), today_msk(), settings.claude_login_period_days,
        settings.claude_login_warn_days,
    )
    if reminder:
        alerts.append(reminder)
    state.save()
    for alert in alerts:
        for target in [settings.chat_id, *settings.allowed_user_ids]:
            try:
                await bot.send_message(target, alert, parse_mode="HTML")
            except Exception as exc:  # noqa: BLE001 — one Telegram failure must not stop checks
                log.error("health alert send failed for %s: %s", target, type(exc).__name__)
