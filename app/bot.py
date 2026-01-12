from __future__ import annotations

import asyncio

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from loguru import logger

from app.config import settings
from app.db.base import init_db
from app.handlers import common, game, lobby


async def main() -> None:
    logger.remove()
    logger.add(lambda msg: print(msg, end=""), level=settings.log_level)

    await init_db()
    logger.info("DB initialized")

    bot = Bot(token=settings.bot_token, parse_mode="HTML")
    dp = Dispatcher(storage=MemoryStorage())

    dp.include_router(common.router)
    dp.include_router(lobby.router)
    dp.include_router(game.router)

    logger.info("Starting polling...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
