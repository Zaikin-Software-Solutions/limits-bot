"""Входящие команды и меню: /start, /menu, /status, /next, /models.

Плитки inline-меню разделены по провайдеру: отдельно 🟣 Claude, 🟠 Codex и «Всё».
Постоянная reply-клавиатура внизу + Menu Button слева от ввода (ставится в main).
"""

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
from ..formatter import format_models_split, format_next, format_status
from ..limits_api import Credential, fetch_limits, fetch_models

log = logging.getLogger(__name__)
router = Router()


# ── Клавиатуры ───────────────────────────────────────────────────────────────

def _menu_kb() -> InlineKeyboardMarkup:
    """Inline-меню: по строке на действие, в каждой — Claude / Codex / Всё."""
    def row(action: str, title: str) -> list[InlineKeyboardButton]:
        return [
            InlineKeyboardButton(text=f"{title}", callback_data=f"cmd:{action}:all"),
            InlineKeyboardButton(text="🟣", callback_data=f"cmd:{action}:claude"),
            InlineKeyboardButton(text="🟠", callback_data=f"cmd:{action}:codex"),
        ]

    return InlineKeyboardMarkup(
        inline_keyboard=[
            row("status", "📊 Статус"),
            row("next", "⏰ Обнуление"),
            [
                InlineKeyboardButton(text="🤖 Модели", callback_data="cmd:models:all"),
                InlineKeyboardButton(text="🔄 Обновить", callback_data="cmd:menu:all"),
            ],
        ]
    )


def _reply_kb() -> ReplyKeyboardMarkup:
    """Постоянная клавиатура внизу поля ввода."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Статус"), KeyboardButton(text="⏰ Обнуление")],
            [KeyboardButton(text="🤖 Модели"), KeyboardButton(text="☰ Меню")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


# ── Доступ ───────────────────────────────────────────────────────────────────

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


# ── Данные ───────────────────────────────────────────────────────────────────

async def _gather() -> list[Credential]:
    """Аккаунты из всех источников (claude + опц. codex), с провайдером."""
    out: list[Credential] = []
    try:
        r = await fetch_limits(settings.limits_api_url, settings.limits_token, provider="claude")
        out.extend(r.credentials)
    except (httpx.HTTPError, ValueError) as e:
        log.warning("gather claude failed: %s", e)
    if settings.codex_api_url and settings.codex_token:
        try:
            r = await fetch_limits(settings.codex_api_url, settings.codex_token, provider="codex")
            out.extend(r.credentials)
        except (httpx.HTTPError, ValueError) as e:
            log.warning("gather codex failed: %s", e)
    if settings.accounts_filter:
        out = [c for c in out if c.email in settings.accounts_filter]
    return out


def _filter(creds: list[Credential], scope: str) -> list[Credential]:
    if scope in ("claude", "codex"):
        return [c for c in creds if c.provider == scope]
    return creds


async def _status_text(scope: str = "all") -> str:
    creds = _filter(await _gather(), scope)
    return format_status(creds)


async def _next_text(scope: str = "all") -> str:
    creds = _filter(await _gather(), scope)
    return format_next(creds)


async def _models_text() -> str:
    """Модели двух прокси раздельно: rodionov (claude-url) и zspzvs (codex-url)."""
    async def grab(url: str, token: str):
        if not url or not token:
            return [], "источник не настроен"
        try:
            return await fetch_models(url, token), None
        except (httpx.HTTPError, ValueError) as e:
            log.warning("models fetch failed for %s: %s", url, e)
            return [], "недоступен"

    rodionov = await grab(settings.limits_api_url, settings.limits_token)
    zspzvs = await grab(settings.codex_api_url, settings.codex_token)
    return format_models_split(rodionov, zspzvs)


# ── Команды ──────────────────────────────────────────────────────────────────

@router.message(Command("myid"))
async def cmd_myid(message: Message) -> None:
    """Показать свой user_id (для настройки ALLOWED_USER_IDS)."""
    uid = message.from_user.id if message.from_user else "?"
    log.info("myid request from user_id=%s chat=%s", uid, message.chat.id)
    await message.answer(f"Твой user_id: <code>{uid}</code>", parse_mode="HTML")


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    uid = message.from_user.id if message.from_user else "?"
    log.info("start from user_id=%s chat_id=%s type=%s", uid, message.chat.id, message.chat.type)
    if not _allowed_message(message):
        await message.answer(
            f"Твой user_id: <code>{uid}</code>\n"
            "Добавь его в ALLOWED_USER_IDS, чтобы команды работали в личке.",
            parse_mode="HTML",
        )
        return
    await message.answer(
        "Бот следит за лимитами <b>Claude</b> 🟣 и <b>Codex</b> 🟠 и шлёт push при "
        "обнулении недельного окна, порогах утилизации, overflow и блокировке.\n\n"
        "Кнопки внизу и слева (☰). В inline-меню 🟣/🟠 — отдельный провайдер.",
        parse_mode="HTML",
        reply_markup=_reply_kb(),
    )
    await message.answer("Выбери действие:", reply_markup=_menu_kb())


@router.message(Command("menu"))
async def cmd_menu(message: Message) -> None:
    if not _allowed_message(message):
        return
    await message.answer("Меню limits-bot:", reply_markup=_reply_kb())
    await message.answer("Выбери действие:", reply_markup=_menu_kb())


@router.message(Command("status"))
@router.message(F.text == "📊 Статус")
async def cmd_status(message: Message) -> None:
    if not _allowed_message(message):
        return
    await message.answer(await _status_text("all"), parse_mode="HTML", reply_markup=_menu_kb())


@router.message(Command("next"))
@router.message(F.text == "⏰ Обнуление")
async def cmd_next(message: Message) -> None:
    if not _allowed_message(message):
        return
    await message.answer(await _next_text("all"), parse_mode="HTML", reply_markup=_menu_kb())


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


# ── Inline-кнопки: cmd:<action>:<scope> ──────────────────────────────────────

@router.callback_query(F.data.startswith("cmd:"))
async def on_menu_click(cb: CallbackQuery) -> None:
    ok = _allowed_chat(cb.message.chat.id) if cb.message else False
    if not ok and not _allowed_user(cb.from_user.id):
        await cb.answer("Нет доступа.", show_alert=True)
        return

    parts = cb.data.split(":")
    action = parts[1] if len(parts) > 1 else "menu"
    scope = parts[2] if len(parts) > 2 else "all"
    await cb.answer()  # убрать «часики»

    if action == "menu":
        text = "Выбери действие:"
    elif action == "status":
        text = await _status_text(scope)
    elif action == "next":
        text = await _next_text(scope)
    elif action == "models":
        text = await _models_text()
    else:
        text = "Неизвестная команда."

    if cb.message:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=_menu_kb())
