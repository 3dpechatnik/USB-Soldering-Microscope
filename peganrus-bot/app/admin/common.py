import html
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    TelegramObject,
)

from app.config import settings
from app.db import crud
from app.db.engine import session_factory

esc = html.escape


def kb(*rows: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=text, callback_data=data) for text, data in row] for row in rows
        ]
    )


def main_menu_kb() -> InlineKeyboardMarkup:
    return kb(
        [("📊 Статистика", "menu:stats"), ("👥 Пользователи", "menu:users")],
        [("🤖 AI", "menu:ai"), ("🧩 Модули", "menu:modules")],
        [("📈 Анализ", "menu:analyze")],
    )


def back_main_kb() -> InlineKeyboardMarkup:
    return kb([("◀️ Назад", "menu:main")])


async def show(event: Message | CallbackQuery, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    """Редактирует сообщение меню (для callback) или отправляет новое (для message)."""
    if isinstance(event, CallbackQuery):
        try:
            await event.message.edit_text(text, reply_markup=markup)
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e):
                await event.message.answer(text, reply_markup=markup)
    else:
        await event.answer(text, reply_markup=markup)


class AdminOnlyMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)
        if user is None:
            return None
        if settings.ADMIN_TELEGRAM_ID and user.id == settings.ADMIN_TELEGRAM_ID:
            return await handler(event, data)

        if isinstance(event, Message):
            if not settings.ADMIN_TELEGRAM_ID:
                await event.answer(
                    f"Админ-бот ещё не настроен.\nВаш Telegram ID: <code>{user.id}</code>\n"
                    "Впишите его в файл .env: ADMIN_TELEGRAM_ID=... и перезапустите бота."
                )
            else:
                await event.answer("⛔ Нет доступа.")
        elif isinstance(event, CallbackQuery):
            await event.answer("⛔ Нет доступа.", show_alert=True)
        async with session_factory() as session:
            await crud.log_security(
                session, "admin_unauthorized", None, user.id, {"username": user.username}
            )
            await session.commit()
        return None
