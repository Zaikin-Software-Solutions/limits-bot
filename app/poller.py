"""Периодический опрос лимитов (Claude + Codex) и рассылка событий."""

from __future__ import annotations

import logging

import httpx
from aiogram import Bot

from .config import settings
from .detector import detect, has_limits_data
from .limits_api import Credential, fetch_limits
from .state import State

log = logging.getLogger(__name__)


async def _gather_creds() -> list[Credential]:
    """Собрать аккаунты из всех настроенных источников (claude + опц. codex)."""
    creds: list[Credential] = []

    try:
        resp = await fetch_limits(settings.limits_api_url, settings.limits_token, provider="claude")
        creds.extend(resp.credentials)
    except (httpx.HTTPError, ValueError) as e:
        log.warning("poll: claude fetch failed: %s", e)

    if settings.codex_api_url and settings.codex_token:
        try:
            resp = await fetch_limits(settings.codex_api_url, settings.codex_token, provider="codex")
            creds.extend(resp.credentials)
        except (httpx.HTTPError, ValueError) as e:
            log.warning("poll: codex fetch failed: %s", e)

    if settings.accounts_filter:
        creds = [c for c in creds if c.email in settings.accounts_filter]
    return creds


async def poll_once(bot: Bot, state: State) -> None:
    """Один цикл: fetch всех источников → diff со снапшотом → отправка событий → save."""
    creds = await _gather_creds()
    if not creds:
        log.warning("poll: no credentials from any source")
        return

    empty = [c.key for c in creds if not has_limits_data(c)]
    if empty:
        # upstream 429 → лимиты пустые; снапшот не затираем, но полезно видеть в логах.
        log.info("poll: empty limits (upstream rate-limit) for: %s", ", ".join(empty))

    first_run = state.is_fresh
    all_events = []

    for cred in creds:
        prev = state.account(cred.key)
        events, new_snap = detect(
            cred,
            prev,
            thresholds=settings.thresholds,
            weekly_warn_hours=settings.weekly_warn_hours,
            extra_usage_warn_pct=settings.extra_usage_warn_pct,
        )
        state.set_account(cred.key, new_snap)
        all_events.extend(events)

    state.save()

    if first_run:
        # На первом запуске снапшота не было — молча зафиксировали baseline,
        # события не шлём (иначе прилетит ложное «обнулилось»).
        log.info("poll: baseline saved for %d account(s), no events sent", len(creds))
        return

    for ev in all_events:
        # loud-события (обнуление лимита) — в канал И в личку каждому админу.
        targets = [settings.chat_id]
        if ev.loud:
            targets += [uid for uid in settings.allowed_user_ids if uid not in targets]
        for target in targets:
            try:
                await bot.send_message(target, ev.text, parse_mode="HTML")
                log.info("sent %s for %s → %s", ev.type, ev.email, target)
            except Exception as e:  # noqa: BLE001 — не роняем поллер из-за одной отправки
                log.error("send failed for %s → %s: %s", ev.type, target, e)
