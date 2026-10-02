from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.config import settings
from app.db.engine import session_factory
from app.db.models import Payment, User
from app.payments import robokassa

router = Router()


def _kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"💳 Оплатить на месяц — {settings.SUBSCRIPTION_PRICE} ₽", callback_data="sub:once")],
            [InlineKeyboardButton(text="🔁 С автопродлением", callback_data="sub:auto")],
        ]
    )


def _offer_text() -> str:
    return (
        f"💳 Подписка на {settings.SUBSCRIPTION_DAYS} дней — {settings.SUBSCRIPTION_PRICE} ₽\n"
        f"• до {settings.DAILY_LIMIT_PAID} сообщений в день\n"
        "• автопродление по желанию (по умолчанию выключено)"
    )


async def _start_payment(user_id: int, recurring: bool) -> str:
    async with session_factory() as session:
        payment = Payment(
            user_id=user_id,
            amount=settings.SUBSCRIPTION_PRICE,
            status="pending",
            type="auto_renew" if recurring else "one_time",
        )
        link = await robokassa.create_invoice(user_id, settings.SUBSCRIPTION_PRICE, recurring)
        if link is None:
            return "🛠 Онлайн-оплата пока не подключена. Мы сообщим, как только она заработает."
        session.add(payment)
        await session.commit()
    return f"Ссылка для оплаты:\n{link}"


@router.message(Command("subscribe"))
async def cmd_subscribe(message: Message) -> None:
    await message.answer(_offer_text(), reply_markup=_kb())


@router.message(Command("subscribe_once"))
async def cmd_subscribe_once(message: Message, user: User) -> None:
    await message.answer(await _start_payment(user.id, recurring=False))


@router.message(Command("subscribe_auto"))
async def cmd_subscribe_auto(message: Message, user: User) -> None:
    await message.answer(await _start_payment(user.id, recurring=True))


@router.callback_query(F.data.in_({"sub:once", "sub:auto"}))
async def cb_subscribe(cb: CallbackQuery, user: User) -> None:
    await cb.answer()
    await cb.message.answer(await _start_payment(user.id, recurring=cb.data == "sub:auto"))


@router.message(Command("autorenew"))
async def cmd_autorenew(message: Message, user: User) -> None:
    async with session_factory() as session:
        db_user = await session.get(User, user.id)
        db_user.auto_renew = not db_user.auto_renew
        enabled = db_user.auto_renew
        has_method = bool(db_user.payment_method_id)
        await session.commit()
    if enabled:
        extra = "" if has_method else "\nСпособ оплаты будет привязан при следующей оплате подписки."
        await message.answer(f"🔁 Автопродление включено.{extra}\nОтключить: /autorenew")
    else:
        await message.answer("⏹ Автопродление выключено.")
