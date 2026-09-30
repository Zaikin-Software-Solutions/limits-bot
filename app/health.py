"""Live proxy probes, Claude status, and Codex OAuth expiry monitoring."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any

import httpx
from aiogram import Bot

from .config import settings
from .limits_api import _models_url
from .state import State

log = logging.getLogger(__name__)
CLAUDE_STATUS_URL = "https://status.claude.com/api/v2/summary.json"


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool | None
    detail: str
    latency_ms: int | None = None
    auth_failure: bool = False


@dataclass(frozen=True)
class AuthExpiry:
    name: str
    expires_at: datetime


def _chat_url(limits_url: str) -> str:
    return _models_url(limits_url).removesuffix("/models") + "/chat/completions"


async def probe_model(name: str, limits_url: str, token: str, model: str) -> Check:
    """A real completion, since /models and /limits can work with dead OAuth."""
    if not limits_url or not token or not model:
        return Check(name, None, "не настроен")
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
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


def codex_auth_expiries(auth_dir: str) -> tuple[list[AuthExpiry], str | None]:
    """Read only expiry metadata; the OAuth access token may auto-refresh."""
    if not auth_dir:
        return [], "проверка срока токена не настроена"
    directory = Path(auth_dir)
    if not directory.is_dir():
        return [], "каталог авторизации недоступен"
    result: list[AuthExpiry] = []
    try:
        paths = list(directory.glob("*.json"))
        for path in paths:
            try:
                data = json.loads(path.read_text("utf-8"))
                if data.get("type") != "codex" or data.get("disabled") is True:
                    continue
                raw = data.get("expired")
                if not isinstance(raw, str):
                    continue
                expires = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=timezone.utc)
                result.append(AuthExpiry(str(data.get("email") or path.stem), expires))
            except (OSError, ValueError, TypeError):
                log.warning("invalid Codex auth metadata in %s", path.name)
    except OSError:
        return [], "каталог авторизации недоступен"
    return result, None if result else "активных Codex auth-файлов со сроком нет"


def format_check(check: Check) -> str:
    icon = "✅" if check.ok is True else "❌" if check.ok is False else "❔"
    latency = f" · {check.latency_ms} мс" if check.latency_ms is not None else ""
    return f"{icon} <b>{escape(check.name)}</b>: {escape(check.detail)}{latency}"


def format_report(checks: list[Check], expiries: list[AuthExpiry], auth_error: str | None,
                  history: dict[str, Any] | None = None) -> str:
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
    if expiries:
        lines.append("")
        for item in expiries:
            hours = (item.expires_at - datetime.now(timezone.utc)).total_seconds() / 3600
            when = item.expires_at.astimezone(timezone.utc).strftime("%d.%m %H:%M UTC")
            lines.append(
                f"🔑 Codex {escape(item.name)}: срок OAuth-токена {when} "
                f"({'истёк' if hours <= 0 else f'через {hours:.0f} ч'})"
            )
    elif auth_error:
        lines.extend(["", f"❔ Codex OAuth: {escape(auth_error)}"])
    lines.extend(["", "<a href=\"https://status.claude.com/\">Claude Status</a>"])
    return "\n".join(lines)


async def run_checks() -> tuple[list[Check], list[AuthExpiry], str | None]:
    claude, status, codex = await asyncio.gather(
        probe_model("AI Proxy → Claude", settings.limits_api_url, settings.limits_token,
                    settings.claude_probe_model),
        check_claude_status(),
        probe_model("Codex", settings.codex_api_url, settings.codex_token,
                    settings.codex_probe_model),
    )
    expiries, auth_error = codex_auth_expiries(settings.codex_auth_dir)
    return [claude, status, codex], expiries, auth_error


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


def _expiry_alerts(history: dict[str, Any], expiries: list[AuthExpiry]) -> list[str]:
    warned = history.setdefault("codex_expiry_warned", {})
    alerts = []
    for item in expiries:
        hours = (item.expires_at - datetime.now(timezone.utc)).total_seconds() / 3600
        if hours > settings.codex_reauth_warn_hours:
            continue
        key = f"{item.name}:{item.expires_at.isoformat()}"
        if warned.get(item.name) == key:
            continue
        warned[item.name] = key
        if hours <= 0:
            text = "OAuth-токен истёк; проверь работу Codex и повтори вход при ошибке"
        else:
            text = (f"до срока OAuth-токена {hours:.0f} ч; он может обновиться автоматически. "
                    "Проверь повторный вход, если OpenAI его запросит")
        alerts.append(f"🔑 <b>Codex {escape(item.name)}</b>: {text}.")
    return alerts


async def poll_health_once(bot: Bot, state: State) -> None:
    checks, expiries, _ = await run_checks()
    history = state.health()
    alerts = []
    for key, check in zip(("claude", "status", "codex"), checks, strict=True):
        alert = _update_check_state(history, key, check)
        if alert:
            alerts.append(alert)
    alerts.extend(_expiry_alerts(history, expiries))
    state.save()
    for alert in alerts:
        for target in [settings.chat_id, *settings.allowed_user_ids]:
            try:
                await bot.send_message(target, alert, parse_mode="HTML")
            except Exception as exc:  # noqa: BLE001 — one Telegram failure must not stop checks
                log.error("health alert send failed for %s: %s", target, type(exc).__name__)
