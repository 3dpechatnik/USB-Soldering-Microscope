import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.config import settings
from app.db import crud
from app.db.engine import session_factory
from app.db.models import User

log = logging.getLogger(__name__)

GAME_CALLBACK_PREFIX = "game:"
FREE_GAME_CALLBACKS = {"game:custom"}


def subscribe_hint() -> str:
    return (
        f"💳 Подписка — {settings.SUBSCRIPTION_PRICE} ₽ в месяц, "
        f"{settings.DAILY_LIMIT_PAID} сообщений в день.\nОформить: /subscribe"
    )


def check_limits(user: User) -> str | None:
    """Шаги 8-9: подписка/триал и лимиты. Возвращает текст отказа или None. Меняет user."""
    if user.is_tester:
        return None
    now = crud.utcnow()

    if user.subscription_status == "active" and not crud.subscription_is_valid(user):
        user.subscription_status = "expired"

    if user.subscription_status == "active":
        if user.daily_reset_at is None or now >= user.daily_reset_at:
            user.messages_today = 0
            user.daily_reset_at = crud.next_local_midnight()
        if user.messages_today >= settings.DAILY_LIMIT_PAID:
            return (
                f"⏳ Дневной лимит ({settings.DAILY_LIMIT_PAID} сообщений) исчерпан. "
                "Приключение продолжится завтра."
            )
        return None

    if user.subscription_status == "trial":
        if user.messages_today < settings.LIMIT_TRIAL:
            return None
        user.subscription_status = "expired"
        return f"🎲 Пробные {settings.LIMIT_TRIAL} сообщений закончились.\n\n{subscribe_hint()}"

    return f"⌛ Подписка закончилась.\n\n{subscribe_hint()}"


class AccessMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message):
            return await self._on_message(handler, event, data)
        if isinstance(event, CallbackQuery):
            return await self._on_callback(handler, event, data)
        return await handler(event, data)

    async def _on_message(self, handler, message: Message, data: dict) -> Any:
        if message.chat.type != "private" or message.from_user is None:
            return None

        # 1. Только текст
        if not message.text:
            await message.answer("Я понимаю только текст.")
            return None
        # 2. Длина
        if len(message.text) > settings.MAX_MESSAGE_LENGTH:
            await message.answer(
                f"✂️ Слишком длинное сообщение (максимум {settings.MAX_MESSAGE_LENGTH} символов)."
            )
            return None

        async with session_factory() as session:
            # 3. Пользователь
            fu = message.from_user
            user = await crud.get_or_create_user(session, fu.id, fu.username, fu.first_name)
            # 4. Бан
            if user.is_banned:
                await session.commit()
                reason = f"\nПричина: {user.ban_reason}" if user.ban_reason else ""
                await message.answer(f"🚫 Доступ к боту ограничен.{reason}")
                return None

            data["user"] = user

            # 5. Команды без лимитов
            if message.text.startswith("/"):
                cmd = message.text[1:].split()[0].split("@")[0].lower() if message.text[1:] else ""
                await session.commit()
                if cmd in settings.free_commands:
                    return await handler(message, data)
                await message.answer("Неизвестная команда. Список команд: /help")
                return None

            # 6. Согласие с политикой
            if not user.agreed_to_privacy:
                await session.commit()
                await message.answer("Сначала нужно принять условия. Нажми /start")
                return None

            # 7. Антифлуд
            now = crud.utcnow()
            if (
                user.last_message_at is not None
                and (now - user.last_message_at).total_seconds() < settings.ANTIFLOOD_SECONDS
            ):
                await session.commit()
                await message.answer("⏳ Не так быстро — дай Мастеру подумать.")
                return None

            # 8-9. Подписка и лимиты
            denial = check_limits(user)
            if denial:
                await session.commit()
                await message.answer(denial)
                return None

            user.last_message_at = now
            await session.commit()

        return await handler(message, data)

    async def _on_callback(self, handler, cb: CallbackQuery, data: dict) -> Any:
        if cb.from_user is None or cb.message is None or cb.message.chat.type != "private":
            await cb.answer()
            return None

        async with session_factory() as session:
            fu = cb.from_user
            user = await crud.get_or_create_user(session, fu.id, fu.username, fu.first_name)
            if user.is_banned:
                await session.commit()
                await cb.answer("🚫 Доступ к боту ограничен.", show_alert=True)
                return None
            data["user"] = user

            cb_data = cb.data or ""
            if not cb_data.startswith(GAME_CALLBACK_PREFIX) or cb_data in FREE_GAME_CALLBACKS:
                await session.commit()
                return await handler(cb, data)

            if not user.agreed_to_privacy:
                await session.commit()
                await cb.answer("Сначала прими условия: /start", show_alert=True)
                return None

            now = crud.utcnow()
            if (
                user.last_message_at is not None
                and (now - user.last_message_at).total_seconds() < settings.ANTIFLOOD_SECONDS
            ):
                await session.commit()
                await cb.answer("⏳ Не так быстро", show_alert=False)
                return None

            denial = check_limits(user)
            if denial:
                await session.commit()
                await cb.answer()
                await cb.message.answer(denial)
                return None

            user.last_message_at = now
            await session.commit()

        return await handler(cb, data)
