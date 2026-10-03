import asyncio
import csv
import io
import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from sqlalchemy import select

from app.admin.common import back_main_kb, esc, kb, show
from app.config import settings
from app.db import crud
from app.db.engine import session_factory
from app.db.models import User

log = logging.getLogger(__name__)
router = Router()


class UserFlow(StatesGroup):
    waiting_id = State()
    waiting_broadcast = State()


ACTION_PROMPTS = {
    "find": "🔍 Отправь telegram_id пользователя.",
    "ban": "🚫 Отправь telegram_id и, через пробел, причину бана (необязательно).",
    "unban": "✅ Отправь telegram_id пользователя для разбана.",
    "tester_on": "🧪 Отправь telegram_id, чтобы включить режим тестера (безлимит).",
    "tester_off": "🧪 Отправь telegram_id, чтобы выключить режим тестера.",
    "gift": "🎁 Отправь telegram_id, чтобы выдать подписку на {days} дн.",
}


def _menu_kb():
    return kb(
        [("🔍 Найти", "us:act:find"), ("📢 Рассылка", "us:bc")],
        [("🚫 Бан", "us:act:ban"), ("✅ Разбан", "us:act:unban")],
        [("🧪 Тестер вкл", "us:act:tester_on"), ("🧪 Тестер выкл", "us:act:tester_off")],
        [(f"🎁 Выдать подписку {settings.SUBSCRIPTION_DAYS}д", "us:act:gift")],
        [("📥 Выгрузить CSV", "us:csv"), ("◀️ Назад", "menu:main")],
    )


def _cancel_kb():
    return kb([("✖️ Отмена", "menu:users")])


async def user_card(session, user: User) -> str:
    ch = await crud.get_character(session, user.id)
    status = user.subscription_status
    if status == "active" and user.subscription_expires_at:
        status += f" до {user.subscription_expires_at.astimezone(settings.tz):%d.%m.%Y}"
    character = (
        f"{esc(ch.name)} — {esc(ch.race or '')} {esc(ch.char_class or '')}, ур. {ch.level}"
        if ch and ch.is_created
        else "не создан"
    )
    ban = f"да ({esc(user.ban_reason or 'без причины')})" if user.is_banned else "нет"
    return (
        f"👤 <b>{esc(user.first_name or '—')}</b> @{esc(user.username or '—')}\n"
        f"Telegram ID: <code>{user.telegram_id}</code>\n"
        f"Статус: {status}\n"
        f"Тестер: {'да' if user.is_tester else 'нет'}\n"
        f"Персонаж: {character}\n"
        f"Сообщений ({'за всё время trial' if user.subscription_status == 'trial' else 'сегодня'}): "
        f"{user.messages_today}\n"
        "Автосписания: нет\n"
        f"Бан: {ban}\n"
        f"Регистрация: {user.created_at.astimezone(settings.tz):%d.%m.%Y %H:%M}"
    )


@router.callback_query(F.data == "menu:users")
async def menu_users(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await cb.answer()
    await show(cb, "👥 <b>Пользователи</b>", _menu_kb())


@router.callback_query(F.data.startswith("us:act:"))
async def choose_action(cb: CallbackQuery, state: FSMContext) -> None:
    action = cb.data.rsplit(":", 1)[1]
    await state.set_state(UserFlow.waiting_id)
    await state.update_data(action=action)
    await cb.answer()
    await show(cb, ACTION_PROMPTS[action].format(days=settings.SUBSCRIPTION_DAYS), _cancel_kb())


@router.message(StateFilter(UserFlow.waiting_id), F.text & ~F.text.startswith("/"))
async def handle_id(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    action = data.get("action", "find")
    parts = message.text.strip().split(maxsplit=1)
    if not parts or not parts[0].lstrip("-").isdigit():
        await message.answer("Нужно число — telegram_id. Попробуй ещё раз.", reply_markup=_cancel_kb())
        return
    tg_id = int(parts[0])
    reason = parts[1] if len(parts) > 1 else None

    async with session_factory() as session:
        user = await crud.get_user_by_tg(session, tg_id)
        if user is None:
            await message.answer("Пользователь не найден. Отправь другой ID.", reply_markup=_cancel_kb())
            return
        note = ""
        if action == "ban":
            user.is_banned, user.banned_at, user.ban_reason = True, crud.utcnow(), reason
            await crud.log_security(session, "admin_ban", user.id, tg_id, {"reason": reason})
            note = "🚫 Пользователь забанен."
        elif action == "unban":
            user.is_banned, user.banned_at, user.ban_reason = False, None, None
            await crud.log_security(session, "admin_unban", user.id, tg_id)
            note = "✅ Бан снят."
        elif action == "tester_on":
            user.is_tester = True
            await crud.log_security(session, "admin_tester_on", user.id, tg_id)
            note = "🧪 Режим тестера включён."
        elif action == "tester_off":
            user.is_tester = False
            await crud.log_security(session, "admin_tester_off", user.id, tg_id)
            note = "🧪 Режим тестера выключен."
        elif action == "gift":
            crud.grant_subscription(user)
            await crud.log_security(session, "admin_gift_subscription", user.id, tg_id)
            note = f"🎁 Подписка выдана на {settings.SUBSCRIPTION_DAYS} дн."
        await session.commit()
        card = await user_card(session, user)

    await state.clear()
    await message.answer(f"{note}\n\n{card}".strip(), reply_markup=kb([("◀️ К пользователям", "menu:users")]))


@router.callback_query(F.data == "us:bc")
async def broadcast_start(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(UserFlow.waiting_broadcast)
    await cb.answer()
    await show(cb, "📢 Отправь текст рассылки (получат все не забаненные пользователи).", _cancel_kb())


@router.message(StateFilter(UserFlow.waiting_broadcast), F.text & ~F.text.startswith("/"))
async def broadcast_preview(message: Message, state: FSMContext) -> None:
    await state.update_data(text=message.text)
    async with session_factory() as session:
        recipients = len((await session.scalars(select(User.id).where(User.is_banned.is_(False)))).all())
    await message.answer(
        f"Предпросмотр:\n\n{esc(message.text)}\n\n— — —\nПолучателей: {recipients}",
        reply_markup=kb([("✅ Отправить", "us:bc_go"), ("✖️ Отмена", "menu:users")]),
    )


@router.callback_query(F.data == "us:bc_go")
async def broadcast_send(cb: CallbackQuery, state: FSMContext, game_bot: Bot) -> None:
    text = (await state.get_data()).get("text")
    await state.clear()
    await cb.answer()
    if not text:
        await show(cb, "Нечего отправлять.", back_main_kb())
        return
    await cb.message.edit_reply_markup(reply_markup=None)
    async with session_factory() as session:
        ids = list((await session.scalars(select(User.telegram_id).where(User.is_banned.is_(False)))).all())
    ok = failed = 0
    for tg_id in ids:
        try:
            await game_bot.send_message(tg_id, text)
            ok += 1
        except Exception as e:
            failed += 1
            log.info("Рассылка: %s не доставлено: %s", tg_id, e)
        await asyncio.sleep(settings.BROADCAST_DELAY)
    await cb.message.answer(
        f"📢 Рассылка завершена. Доставлено: {ok}, не доставлено: {failed}.",
        reply_markup=kb([("◀️ К пользователям", "menu:users")]),
    )


@router.callback_query(F.data == "us:csv")
async def users_csv(cb: CallbackQuery) -> None:
    await cb.answer()
    async with session_factory() as session:
        users = (await session.scalars(select(User).order_by(User.id))).all()
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(
        ["id", "telegram_id", "username", "first_name", "status", "expires_at", "tester",
         "banned", "auto_renew", "messages_today", "agreed", "created_at"]
    )
    for u in users:
        writer.writerow(
            [u.id, u.telegram_id, u.username or "", u.first_name or "", u.subscription_status,
             u.subscription_expires_at or "", u.is_tester, u.is_banned, u.auto_renew,
             u.messages_today, u.agreed_to_privacy, u.created_at]
        )
    file = BufferedInputFile(("\ufeff" + buf.getvalue()).encode("utf-8"), filename="users.csv")
    await cb.message.answer_document(file, caption=f"Пользователей: {len(users)}")


@router.message(Command("user"))
async def cmd_user(message: Message) -> None:
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Использование: /user <telegram_id>")
        return
    async with session_factory() as session:
        user = await crud.get_user_by_tg(session, int(parts[1]))
        if user is None:
            await message.answer("Не найден.")
            return
        await message.answer(await user_card(session, user))
