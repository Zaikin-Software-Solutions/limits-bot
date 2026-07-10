"""Точка входа: aiogram polling (команды) + APScheduler job (опрос лимитов)."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand, MenuButtonCommands
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .config import settings
from .handlers import router
from .poller import poll_once
from .state import State

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("limits-bot")


async def main() -> None:
    bot = Bot(token=settings.bot_token)
    dp = Dispatcher()
    dp.include_router(router)

    # Команды в меню + кнопка меню [☰] слева от поля ввода (всегда доступна).
    await bot.set_my_commands(
        [
            BotCommand(command="menu", description="Меню с кнопками"),
            BotCommand(command="status", description="Текущие лимиты"),
            BotCommand(command="next", description="Когда обнуление"),
            BotCommand(command="models", description="Доступные модели"),
        ]
    )
    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())

    state = State(settings.state_path)
    state.load()

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        poll_once,
        "interval",
        minutes=settings.poll_interval_min,
        args=(bot, state),
        id="poll_limits",
        max_instances=1,
        coalesce=True,
    )

    # Первый опрос сразу на старте (зафиксирует baseline, если снапшота нет).
    await poll_once(bot, state)
    scheduler.start()

    log.info(
        "started: poll every %d min, chat_id=%s, %d threshold(s)",
        settings.poll_interval_min,
        settings.chat_id,
        len(settings.thresholds),
    )
    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
