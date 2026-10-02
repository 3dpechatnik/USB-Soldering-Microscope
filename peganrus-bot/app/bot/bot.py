from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from app.bot.handlers import commands, delete, game, start, subscription
from app.bot.middlewares import AccessMiddleware
from app.config import settings


def create_bot() -> Bot:
    return Bot(token=settings.BOT_TOKEN)


def create_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    access = AccessMiddleware()
    dp.message.outer_middleware(access)
    dp.callback_query.outer_middleware(access)
    dp.include_routers(
        start.router,
        subscription.router,
        delete.router,
        commands.router,
        game.router,
    )
    return dp
