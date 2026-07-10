"""Входящие команды: /start, /status, /next."""

from __future__ import annotations

import logging

import httpx
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from ..config import settings
from ..formatter import format_next, format_status
from ..limits_api import fetch_limits

log = logging.getLogger(__name__)
router = Router()


def _allowed(message: Message) -> bool:
    """Разрешаем команды из целевого чата или от whitelисти́рованных пользователей."""
    if message.chat.id == settings.chat_id:
        return True
    if settings.allowed_user_ids and message.from_user:
        return message.from_user.id in settings.allowed_user_ids
    return False


async def _load_creds():
    resp = await fetch_limits(settings.limits_api_url, settings.limits_token)
    creds = resp.credentials
    if settings.accounts_filter:
        creds = [c for c in creds if c.email in settings.accounts_filter]
    return creds


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    if not _allowed(message):
        return
    await message.answer(
        "Бот следит за лимитами Claude и шлёт сюда push при обнулении недельного "
        "окна, порогах утилизации, платном overflow и полной блокировке.\n\n"
        "Команды:\n"
        "/status — текущие лимиты по всем аккаунтам\n"
        "/next — когда ближайшее обнуление"
    )


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    if not _allowed(message):
        return
    try:
        creds = await _load_creds()
    except (httpx.HTTPError, ValueError) as e:
        log.warning("status fetch failed: %s", e)
        await message.answer("Не удалось получить лимиты (API недоступен).")
        return
    await message.answer(format_status(creds), parse_mode="HTML")


@router.message(Command("next"))
async def cmd_next(message: Message) -> None:
    if not _allowed(message):
        return
    try:
        creds = await _load_creds()
    except (httpx.HTTPError, ValueError) as e:
        log.warning("next fetch failed: %s", e)
        await message.answer("Не удалось получить лимиты (API недоступен).")
        return
    await message.answer(format_next(creds), parse_mode="HTML")
