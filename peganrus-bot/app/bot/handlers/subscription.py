from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.config import settings
from app.db import crud
from app.db.engine import session_factory
from app.db.models import Payment, User
from app.payments import robokassa

router = Router()


def _offer_text() -> str:
    return (
        f"💳 Подписка на {settings.SUBSCRIPTION_DAYS} дней — {settings.SUBSCRIPTION_PRICE} ₽\n"
        f"• до {settings.DAILY_LIMIT_PAID} сообщений в день\n"
        "• разовый платёж, без автопродления и без повторных списаний"
    )


def _pay_kb(url: str, payment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"💳 Оплатить {settings.SUBSCRIPTION_PRICE} ₽", url=url)],
            [InlineKeyboardButton(text="✅ Я оплатил", callback_data=f"sub:check:{payment_id}")],
        ]
    )


async def _start_payment(user_id: int) -> tuple[str, InlineKeyboardMarkup | None]:
    if not robokassa.is_configured():
        return "🛠 Онлайн-оплата пока не подключена. Мы сообщим, как только она заработает.", None
    async with session_factory() as session:
        payment = Payment(
            user_id=user_id,
            amount=settings.SUBSCRIPTION_PRICE,
            status="pending",
            type="one_time",
        )
        session.add(payment)
        await session.flush()
        url = robokassa.build_payment_url(payment.id, settings.SUBSCRIPTION_PRICE)
        payment.robokassa_invoice_id = str(payment.id)
        await session.commit()
        payment_id = payment.id
    return "Оплатите подписку по кнопке. После оплаты нажмите «Я оплатил» — бот проверит платёж.", _pay_kb(
        url, payment_id
    )


async def _confirm(user: User, payment_id: int) -> str:
    async with session_factory() as session:
        payment = await session.get(Payment, payment_id)
        if payment is None or payment.user_id != user.id:
            return "Счёт не найден. Оформите оплату заново: /subscribe"
        if payment.status == "succeeded":
            return f"Этот платёж уже засчитан. Подписка действует {settings.SUBSCRIPTION_DAYS} дней."
        try:
            state = await robokassa.fetch_state(payment.id)
        except Exception:
            return "Не удалось связаться с Робокассой. Подождите минуту и нажмите «Я оплатил» ещё раз."
        if not state.paid:
            if state.pending:
                return "Оплата ещё не дошла. Если вы только что заплатили, подождите минуту и нажмите «Я оплатил» снова."
            return "Робокасса не подтвердила оплату. Если деньги списались, напишите в поддержку и пришлите чек."
        db_user = await session.get(User, user.id)
        db_user.auto_renew = False
        db_user.payment_method_id = None
        crud.grant_subscription(db_user)
        db_user.last_payment_at = crud.utcnow()
        payment.status = "succeeded"
        payment.paid_at = crud.utcnow()
        await session.commit()
        until = db_user.subscription_expires_at.astimezone(settings.tz).strftime("%d.%m.%Y")
    return (
        f"✅ Оплата получена. Подписка активна до {until}.\n"
        f"Это разовый платёж на {settings.SUBSCRIPTION_DAYS} дней, повторно ничего не спишется."
    )


@router.message(Command("subscribe", "subscribe_once"))
async def cmd_subscribe(message: Message, user: User) -> None:
    text, kb = await _start_payment(user.id)
    await message.answer(text if kb else f"{_offer_text()}\n\n{text}", reply_markup=kb)


@router.message(Command("subscribe_auto", "autorenew"))
async def cmd_no_autorenew(message: Message) -> None:
    await message.answer(
        "Автоматических списаний нет.\n"
        f"Подписка только разовая: {settings.SUBSCRIPTION_PRICE} ₽ на {settings.SUBSCRIPTION_DAYS} дней.\n"
        "Оформить: /subscribe"
    )


@router.callback_query(F.data == "sub:once")
async def cb_subscribe(cb: CallbackQuery, user: User) -> None:
    await cb.answer()
    text, kb = await _start_payment(user.id)
    await cb.message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("sub:check:"))
async def cb_check(cb: CallbackQuery, user: User) -> None:
    raw = (cb.data or "").rsplit(":", 1)[-1]
    if not raw.isdigit():
        await cb.answer("Счёт не найден", show_alert=True)
        return
    await cb.answer("Проверяю оплату…")
    await cb.message.answer(await _confirm(user, int(raw)))
