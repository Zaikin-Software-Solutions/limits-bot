"""Входящие команды и inline-меню: /start, /menu, /status, /next, /models."""

from __future__ import annotations

import logging

import httpx
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from ..config import settings
from ..formatter import format_models, format_next, format_status
from ..limits_api import fetch_limits, fetch_models

log = logging.getLogger(__name__)
router = Router()


def _menu_kb() -> InlineKeyboardMarkup:
    """Inline-меню с командами (под сообщением)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📊 Статус", callback_data="cmd:status"),
                InlineKeyboardButton(text="⏰ Обнуление", callback_data="cmd:next"),
            ],
            [
                InlineKeyboardButton(text="🤖 Модели", callback_data="cmd:models"),
                InlineKeyboardButton(text="🔄 Обновить", callback_data="cmd:menu"),
            ],
        ]
    )


def _reply_kb() -> ReplyKeyboardMarkup:
    """Постоянная клавиатура внизу поля ввода — кнопки-команды всегда под рукой."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Статус"), KeyboardButton(text="⏰ Обнуление")],
            [KeyboardButton(text="🤖 Модели"), KeyboardButton(text="☰ Меню")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def _allowed_chat(chat_id: int) -> bool:
    return chat_id == settings.chat_id


def _allowed_user(user_id: int | None) -> bool:
    if not settings.allowed_user_ids:
        return False
    return user_id in settings.allowed_user_ids


def _allowed_message(message: Message) -> bool:
    if _allowed_chat(message.chat.id):
        return True
    return message.from_user is not None and _allowed_user(message.from_user.id)


async def _load_creds():
    resp = await fetch_limits(settings.limits_api_url, settings.limits_token)
    creds = resp.credentials
    if settings.accounts_filter:
        creds = [c for c in creds if c.email in settings.accounts_filter]
    return creds


async def _status_text() -> str:
    try:
        creds = await _load_creds()
    except (httpx.HTTPError, ValueError) as e:
        log.warning("status fetch failed: %s", e)
        return "Не удалось получить лимиты (API недоступен)."
    return format_status(creds)


async def _next_text() -> str:
    try:
        creds = await _load_creds()
    except (httpx.HTTPError, ValueError) as e:
        log.warning("next fetch failed: %s", e)
        return "Не удалось получить лимиты (API недоступен)."
    return format_next(creds)


async def _models_text() -> str:
    try:
        models = await fetch_models(settings.limits_api_url, settings.limits_token)
    except (httpx.HTTPError, ValueError) as e:
        log.warning("models fetch failed: %s", e)
        return "Не удалось получить список моделей."
    return format_models(models)


# ── Команды ──────────────────────────────────────────────────────────────────

@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    if not _allowed_message(message):
        return
    await message.answer(
        "Бот следит за лимитами <b>Claude</b> и <b>Codex</b> и шлёт push при обнулении "
        "недельного окна, порогах утилизации, платном overflow и полной блокировке.\n\n"
        "Кнопки всегда внизу. Или /menu, /status, /next, /models.",
        parse_mode="HTML",
        reply_markup=_reply_kb(),
    )


@router.message(Command("menu"))
async def cmd_menu(message: Message) -> None:
    if not _allowed_message(message):
        return
    # показываем и постоянную клавиатуру, и inline-меню
    await message.answer("Меню limits-bot:", reply_markup=_reply_kb())
    await message.answer("Выбери действие:", reply_markup=_menu_kb())


@router.message(Command("status"))
@router.message(F.text == "📊 Статус")
async def cmd_status(message: Message) -> None:
    if not _allowed_message(message):
        return
    await message.answer(await _status_text(), parse_mode="HTML", reply_markup=_menu_kb())


@router.message(Command("next"))
@router.message(F.text == "⏰ Обнуление")
async def cmd_next(message: Message) -> None:
    if not _allowed_message(message):
        return
    await message.answer(await _next_text(), parse_mode="HTML", reply_markup=_menu_kb())


@router.message(Command("models"))
@router.message(F.text == "🤖 Модели")
async def cmd_models(message: Message) -> None:
    if not _allowed_message(message):
        return
    await message.answer(await _models_text(), parse_mode="HTML", reply_markup=_menu_kb())


@router.message(F.text == "☰ Меню")
async def btn_menu(message: Message) -> None:
    if not _allowed_message(message):
        return
    await message.answer("Выбери действие:", reply_markup=_menu_kb())


# ── Inline-кнопки ────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("cmd:"))
async def on_menu_click(cb: CallbackQuery) -> None:
    ok = _allowed_chat(cb.message.chat.id) if cb.message else False
    if not ok and not _allowed_user(cb.from_user.id):
        await cb.answer("Нет доступа.", show_alert=True)
        return

    action = cb.data.split(":", 1)[1]
    await cb.answer()  # убрать «часики» на кнопке

    if action == "menu":
        text = "Меню limits-bot:"
    elif action == "status":
        text = await _status_text()
    elif action == "next":
        text = await _next_text()
    elif action == "models":
        text = await _models_text()
    else:
        text = "Неизвестная команда."

    if cb.message:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=_menu_kb())
