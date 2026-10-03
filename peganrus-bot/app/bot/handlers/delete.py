from datetime import timedelta

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy import func, select

from app.config import settings
from app.db import crud
from app.db.engine import session_factory
from app.db.models import MessageHistory, SecurityLog, User

router = Router()


@router.message(Command("delete_account"))
async def cmd_delete_account(message: Message, user: User) -> None:
    async with session_factory() as session:
        await crud.log_security(session, "delete_requested", user.id, user.telegram_id)
        await session.commit()
    minutes = max(settings.DELETE_CONFIRM_SECONDS // 60, 1)
    await message.answer(
        "⚠️ Будут безвозвратно удалены ваш персонаж, прогресс, история игры и подписка.\n\n"
        f"Чтобы подтвердить, отправьте /confirm_delete в течение {minutes} мин.\n"
        "Чтобы передумать — просто игнорируйте это сообщение."
    )


@router.message(Command("confirm_delete"))
async def cmd_confirm_delete(message: Message, user: User) -> None:
    since = crud.utcnow() - timedelta(seconds=settings.DELETE_CONFIRM_SECONDS)
    async with session_factory() as session:
        requested = await session.scalar(
            select(SecurityLog.id)
            .where(
                SecurityLog.user_id == user.id,
                SecurityLog.event_type == "delete_requested",
                SecurityLog.created_at >= since,
            )
            .limit(1)
        )
        if requested is None:
            await message.answer("Сначала отправьте /delete_account.")
            return

        db_user = await session.get(User, user.id)
        history = await session.scalar(
            select(func.count()).select_from(MessageHistory).where(MessageHistory.user_id == user.id)
        )
        character = await crud.get_character(session, user.id)
        # security_log пишем и сохраняем ДО удаления данных
        await crud.log_security(
            session,
            "account_deleted",
            db_user.id,
            db_user.telegram_id,
            {
                "username": db_user.username,
                "subscription_status": db_user.subscription_status,
                "is_tester": db_user.is_tester,
                "messages_in_history": history,
                "character": character.name if character else None,
            },
        )
        await session.commit()

        await crud.delete_user_cascade(session, db_user.id)
        await session.commit()

    await message.answer("🗑 Аккаунт и все данные удалены. Если захотите вернуться — нажмите /start.")
