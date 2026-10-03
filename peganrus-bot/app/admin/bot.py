from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Message

from app.admin.common import AdminOnlyMiddleware, main_menu_kb, show
from app.admin.handlers import ai_settings, analyze, modules, stats, users
from app.config import settings

base = Router()


@base.message(CommandStart())
@base.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("🛠 <b>Админ-панель «Подземелье и Горынычи»</b>", reply_markup=main_menu_kb())


@base.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Отменено.", reply_markup=main_menu_kb())


@base.callback_query(lambda cb: cb.data == "menu:main")
async def cb_main(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await cb.answer()
    await show(cb, "🛠 <b>Админ-панель «Подземелье и Горынычи»</b>", main_menu_kb())


def create_admin_bot() -> Bot:
    return Bot(token=settings.ADMIN_BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


def create_admin_dispatcher(game_bot: Bot) -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage(), game_bot=game_bot)
    guard = AdminOnlyMiddleware()
    dp.message.outer_middleware(guard)
    dp.callback_query.outer_middleware(guard)
    dp.include_routers(base, stats.router, users.router, ai_settings.router, modules.router, analyze.router)
    return dp
