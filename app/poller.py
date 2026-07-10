"""Периодический опрос лимитов и рассылка событий."""

from __future__ import annotations

import logging

import httpx
from aiogram import Bot

from .config import settings
from .detector import detect, has_limits_data
from .limits_api import fetch_limits
from .state import State

log = logging.getLogger(__name__)


async def poll_once(bot: Bot, state: State) -> None:
    """Один цикл: fetch → diff со снапшотом → отправка событий → save."""
    try:
        resp = await fetch_limits(settings.limits_api_url, settings.limits_token)
    except (httpx.HTTPError, ValueError) as e:
        log.warning("poll: fetch failed: %s", e)
        return

    creds = resp.credentials
    if settings.accounts_filter:
        creds = [c for c in creds if c.email in settings.accounts_filter]

    empty = [c.email for c in creds if not has_limits_data(c)]
    if empty:
        # upstream 429 → лимиты пустые; снапшот не затираем, но полезно видеть в логах.
        log.info("poll: empty limits (upstream rate-limit) for: %s", ", ".join(empty))

    first_run = state.is_fresh
    all_events = []

    for cred in creds:
        prev = state.account(cred.email)
        events, new_snap = detect(
            cred,
            prev,
            thresholds=settings.thresholds,
            weekly_warn_hours=settings.weekly_warn_hours,
            extra_usage_warn_pct=settings.extra_usage_warn_pct,
        )
        state.set_account(cred.email, new_snap)
        all_events.extend(events)

    state.save()

    if first_run:
        # На первом запуске снапшота не было — молча зафиксировали baseline,
        # события не шлём (иначе прилетит ложное «обнулилось»).
        log.info("poll: baseline saved for %d account(s), no events sent", len(creds))
        return

    for ev in all_events:
        try:
            await bot.send_message(settings.chat_id, ev.text)
            log.info("sent %s for %s", ev.type, ev.email)
        except Exception as e:  # noqa: BLE001 — не роняем поллер из-за одной отправки
            log.error("send failed for %s: %s", ev.type, e)
