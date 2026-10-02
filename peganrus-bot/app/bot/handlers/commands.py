from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.config import settings
from app.db import crud
from app.db.engine import session_factory
from app.db.models import User

router = Router()


def fmt_date(dt) -> str:
    return dt.astimezone(settings.tz).strftime("%d.%m.%Y")


def status_text(user: User) -> str:
    on_off = "включено" if user.auto_renew else "выключено"
    if user.is_tester:
        return "🧪 Статус: тестер\nСообщения: без ограничений"

    status = user.subscription_status
    if status == "active" and not crud.subscription_is_valid(user):
        status = "expired"

    if status == "active":
        used = user.messages_today
        if user.daily_reset_at is None or crud.utcnow() >= user.daily_reset_at:
            used = 0
        left = max(settings.DAILY_LIMIT_PAID - used, 0)
        return (
            f"💳 Подписка активна до {fmt_date(user.subscription_expires_at)}\n"
            f"Сообщений на сегодня осталось: {left} из {settings.DAILY_LIMIT_PAID}\n"
            f"Автопродление: {on_off}"
        )
    if status == "trial":
        left = max(settings.LIMIT_TRIAL - user.messages_today, 0)
        return (
            f"⏳ Пробный период\nСообщений осталось: {left} из {settings.LIMIT_TRIAL}\n\n"
            f"Подписка: {settings.SUBSCRIPTION_PRICE} ₽/мес — /subscribe"
        )
    return f"⌛ Подписка не активна.\n\nПодписка: {settings.SUBSCRIPTION_PRICE} ₽/мес — /subscribe"


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    async with session_factory() as session:
        await message.answer(await crud.get_text(session, "text_help"))


@router.message(Command("status"))
async def cmd_status(message: Message, user: User) -> None:
    async with session_factory() as session:
        fresh = await session.get(User, user.id)
    await message.answer(status_text(fresh))


@router.message(Command("character"))
async def cmd_character(message: Message, user: User) -> None:
    async with session_factory() as session:
        ch = await crud.get_character(session, user.id)
    if ch is None or not ch.is_created:
        await message.answer("Персонаж ещё не создан. Напиши любое сообщение, и Мастер поможет его создать.")
        return
    companion = f"\nСпутник: {ch.companion_name} ({ch.companion_hp}/{ch.companion_max_hp})" if ch.companion_name else ""
    slots = ", ".join(f"{k}: {v}" for k, v in sorted((ch.spell_slots or {}).items()))
    await message.answer(
        f"🧙 {ch.name} — {ch.race}, {ch.char_class}, ур. {ch.level}\n"
        f"❤️ HP {ch.hp}/{ch.max_hp} | 🛡 КД {ch.ac} | 👟 {ch.speed} | ⭐ XP {ch.xp}\n"
        f"СИЛ {ch.strength} ЛОВ {ch.dexterity} ТЕЛ {ch.constitution} "
        f"ИНТ {ch.intelligence} МДР {ch.wisdom} ХАР {ch.charisma}\n"
        f"🪙 Золото {ch.gold} | Серебро {ch.silver}\n"
        f"🏹 Стрелы {ch.arrows} | Болты {ch.bolts} | 🍖 Рационы {ch.rations} | 🛢 Масло {ch.oil}\n"
        f"😮‍💨 Истощение {ch.exhaustion}\n"
        f"⚔️ {ch.weapon_name or '—'} | 🛡 {ch.armor_name or '—'}\n"
        f"📖 Заклинания: {', '.join(ch.spells or []) or '—'}\n"
        f"✨ Слоты: {slots or '—'}\n"
        f"🗣 Языки: {', '.join(ch.languages or []) or '—'}\n"
        f"📍 {ch.location or 'неизвестно'} | День {ch.game_day}"
        f"{companion}"
    )


@router.message(Command("inventory"))
async def cmd_inventory(message: Message, user: User) -> None:
    async with session_factory() as session:
        items = await crud.get_inventory(session, user.id)
    if not items:
        await message.answer("🎒 Инвентарь пуст.")
        return
    lines = [f"{i.slot_index + 1}. {i.item_name} x{i.quantity}" for i in items]
    await message.answer("🎒 Инвентарь:\n" + "\n".join(lines))
