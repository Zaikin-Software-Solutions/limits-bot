"""Входящие команды и меню: /start, /menu, /status, /next, /models.

Плитки inline-меню разделены по провайдеру: отдельно 🟣 Claude, 🟠 Codex и «Всё».
Постоянная reply-клавиатура внизу + Menu Button слева от ввода (ставится в main).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta

import httpx
from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    ForceReply,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from ..config import settings
from ..formatter import format_models_split, format_next, format_status
from ..health import format_models_report, format_report, run_checks, run_model_matrix
from ..limits_api import Credential, fetch_limits, fetch_models
from ..reauth import (
    format_status as format_login_status,
    login_status,
    parse_date,
    set_last_login,
    today_msk,
)
from ..state import State

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
                InlineKeyboardButton(text="🔎 Проверка", callback_data="cmd:check:all"),
                InlineKeyboardButton(text="🔑 Токен", callback_data="cmd:token:all"),
            ],
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="cmd:menu:all")],
        ]
    )


def _reply_kb() -> ReplyKeyboardMarkup:
    """Постоянная клавиатура внизу поля ввода."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Статус"), KeyboardButton(text="⏰ Обнуление")],
            [KeyboardButton(text="🤖 Модели"), KeyboardButton(text="☰ Меню")],
            [KeyboardButton(text="🔎 Проверка"), KeyboardButton(text="🔑 Токен")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


GRID_DAYS = 28  # период логина 30 дней: последние 28 дней покрывают реальные случаи
ASK_PREFIX = "✏️ Введи дату обновления логина"


def _token_kb() -> InlineKeyboardMarkup:
    """Быстрый выбор даты обновления логина Claude."""
    def ago(days: int, title: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(text=title, callback_data=f"tok:ago:{days}")

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [ago(0, "✅ Обновил сегодня"), ago(1, "Вчера")],
            [ago(2, "2 дн. назад"), ago(3, "3 дн."), ago(7, "Неделю назад")],
            [
                InlineKeyboardButton(text="📅 Выбрать дату", callback_data="tok:pick"),
                InlineKeyboardButton(text="✏️ Ввести вручную", callback_data="tok:ask"),
            ],
            [InlineKeyboardButton(text="⬅️ Меню", callback_data="cmd:menu:all")],
        ]
    )


def _date_grid_kb(today: date) -> InlineKeyboardMarkup:
    """Сетка последних GRID_DAYS дней, по 4 в ряд; свежие даты сверху."""
    buttons = [
        InlineKeyboardButton(
            text=(today - timedelta(days=n)).strftime("%d.%m"),
            callback_data=f"tok:d:{(today - timedelta(days=n)).isoformat()}",
        )
        for n in range(GRID_DAYS)
    ]
    rows = [buttons[i:i + 4] for i in range(0, len(buttons), 4)]
    rows.append([
        InlineKeyboardButton(text="✏️ Ввести вручную", callback_data="tok:ask"),
        InlineKeyboardButton(text="⬅️ Назад", callback_data="cmd:token:all"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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


def _login_text(state: State) -> str:
    status = login_status(state.login(), today_msk(), settings.claude_login_period_days)
    return format_login_status(status)


def _save_login(state: State, value: date) -> str:
    """Записать дату обновления и вернуть текст подтверждения."""
    set_last_login(state.login(), value)
    state.save()
    return (
        "✅ Сохранено.\n" + _login_text(state)
        + f"\nНапоминание придёт за {settings.claude_login_warn_days} дн. до срока."
    )


async def _check_text(state: State) -> str:
    checks, matrix = await asyncio.gather(run_checks(), run_model_matrix())
    return (
        format_report(checks, state.health())
        + "\n\n" + format_models_report(matrix)
        + "\n\n" + _login_text(state)
    )


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


@router.message(Command("check"))
@router.message(F.text == "🔎 Проверка")
async def cmd_check(message: Message, bot_state: State) -> None:
    if not _allowed_message(message):
        return
    await message.answer(
        await _check_text(bot_state), parse_mode="HTML", reply_markup=_menu_kb()
    )


@router.message(Command("token"))
@router.message(F.text == "🔑 Токен")
async def cmd_token(message: Message, bot_state: State, command: CommandObject | None = None) -> None:
    """/token — показать срок; /token 25.09.2026 | сегодня | вчера — задать дату обновления."""
    if not _allowed_message(message):
        return
    args = (command.args or "").strip() if command else ""
    if not args:
        await message.answer(
            _login_text(bot_state) + "\n\nЗадать дату: <code>/token ДД.ММ.ГГГГ</code>, "
            "<code>/token сегодня</code> или <code>/token вчера</code>",
            parse_mode="HTML",
            reply_markup=_token_kb(),
        )
        return
    try:
        value = parse_date(args, today_msk())
    except ValueError as exc:
        await message.answer(
            f"❌ {exc}. Примеры: <code>/token 25.09.2026</code>, <code>/token 2026-09-25</code>, "
            "<code>/token сегодня</code>",
            parse_mode="HTML",
        )
        return
    await message.answer(
        _save_login(bot_state, value), parse_mode="HTML", reply_markup=_menu_kb()
    )


@router.message(F.reply_to_message.text.startswith(ASK_PREFIX))
async def on_token_reply(message: Message, bot_state: State) -> None:
    """Ответ на запрос «Введи дату» после кнопки «✏️ Ввести вручную»."""
    if not _allowed_message(message):
        return
    try:
        value = parse_date(message.text or "", today_msk())
    except ValueError as exc:
        await message.answer(
            f"❌ {exc}. Нажми «✏️ Ввести вручную» и отправь дату ещё раз, например "
            "<code>25.09.2026</code>.",
            parse_mode="HTML",
            reply_markup=_token_kb(),
        )
        return
    await message.answer(
        _save_login(bot_state, value), parse_mode="HTML", reply_markup=_menu_kb()
    )


@router.message(F.text == "☰ Меню")
async def btn_menu(message: Message) -> None:
    if not _allowed_message(message):
        return
    await message.answer("Выбери действие:", reply_markup=_menu_kb())


# ── Inline-кнопки: cmd:<action>:<scope> ──────────────────────────────────────

@router.callback_query(F.data.startswith("cmd:"))
async def on_menu_click(cb: CallbackQuery, bot_state: State) -> None:
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
    elif action == "check":
        text = await _check_text(bot_state)
    elif action == "token":
        text = _login_text(bot_state) + "\n\nКогда ты последний раз обновлял логин?"
    else:
        text = "Неизвестная команда."

    if cb.message:
        markup = _token_kb() if action == "token" else _menu_kb()
        await cb.message.answer(text, parse_mode="HTML", reply_markup=markup)


# ── Inline-кнопки даты логина: tok:<ago|d|pick|ask>[:arg] ────────────────────

@router.callback_query(F.data.startswith("tok:"))
async def on_token_click(cb: CallbackQuery, bot_state: State) -> None:
    # Запись меняет состояние, поэтому только для пользователей из ALLOWED_USER_IDS.
    if not _allowed_user(cb.from_user.id):
        await cb.answer("Нет доступа.", show_alert=True)
        return
    await cb.answer()
    if not cb.message:
        return
    parts = (cb.data or "").split(":", 2)
    action = parts[1] if len(parts) > 1 else ""
    arg = parts[2] if len(parts) > 2 else ""
    today = today_msk()

    if action == "pick":
        await cb.message.answer(
            "📅 Выбери дату обновления логина:", reply_markup=_date_grid_kb(today)
        )
        return
    if action == "ask":
        await cb.message.answer(
            f"{ASK_PREFIX} ответом на это сообщение. Формат: "
            "<code>25.09.2026</code>, <code>25.09</code> или <code>2026-09-25</code>.",
            parse_mode="HTML",
            reply_markup=ForceReply(input_field_placeholder="ДД.ММ.ГГГГ", selective=True),
        )
        return

    try:
        if action == "ago":
            value = today - timedelta(days=int(arg))
        elif action == "d":
            value = date.fromisoformat(arg)
        else:
            raise ValueError
    except (ValueError, OverflowError):
        await cb.message.answer("❌ Кнопка устарела, выбери дату заново.", reply_markup=_token_kb())
        return
    if value > today:
        await cb.message.answer("❌ Дата в будущем.", reply_markup=_token_kb())
        return
    await cb.message.answer(
        _save_login(bot_state, value), parse_mode="HTML", reply_markup=_menu_kb()
    )
