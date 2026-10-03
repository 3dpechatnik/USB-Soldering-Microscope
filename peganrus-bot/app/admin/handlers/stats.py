import csv
import io
from datetime import timedelta

from aiogram import F, Router
from aiogram.types import BufferedInputFile, CallbackQuery
from sqlalchemy import func, select

from app.admin.common import kb, show
from app.config import settings
from app.db import crud
from app.db.engine import session_factory
from app.db.models import MessageHistory, Payment, User

router = Router()

PERIODS = (7, 30, 90)


def _menu_kb():
    return kb(
        [("📋 Общая", "st:general"), ("💳 Платежи", "st:pay:7"), ("🔢 Токены", "st:tok:7")],
        [("📥 Платежи CSV", "st:paycsv"), ("📥 Токены CSV", "st:tokcsv")],
        [("◀️ Назад", "menu:main")],
    )


def _period_kb(prefix: str):
    return kb(
        [(f"{d}д", f"{prefix}:{d}") for d in PERIODS],
        [("◀️ Назад", "menu:stats")],
    )


def _csv_file(name: str, header: list[str], rows: list[list]) -> BufferedInputFile:
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(header)
    writer.writerows(rows)
    return BufferedInputFile(("\ufeff" + buf.getvalue()).encode("utf-8"), filename=name)


@router.callback_query(F.data == "menu:stats")
async def menu_stats(cb: CallbackQuery) -> None:
    await cb.answer()
    await show(cb, "📊 <b>Статистика</b>", _menu_kb())


@router.callback_query(F.data == "st:general")
async def general(cb: CallbackQuery) -> None:
    await cb.answer()
    async with session_factory() as session:
        snap = await crud.compute_snapshot(session, crud.local_today())
    text = (
        "📋 <b>Общая статистика</b>\n"
        f"Всего: {snap['total_users']} | 💳 Платные: {snap['active_subscriptions']} | "
        f"⏳ Trial: {snap['trial_users']} | ⌛ Истекшие: {snap['expired_users']} | "
        f"🚫 Забанено: {snap['banned_users']}\n"
        f"DAU: {snap['dau']} | MAU: {snap['mau']} | 💬 Сообщений сегодня: {snap['messages_today']}"
    )
    await show(cb, text, kb([("🔄 Обновить", "st:general")], [("◀️ Назад", "menu:stats")]))


@router.callback_query(F.data.startswith("st:pay:"))
async def payments(cb: CallbackQuery) -> None:
    await cb.answer()
    days = int(cb.data.rsplit(":", 1)[1])
    since = crud.utcnow() - timedelta(days=days)
    async with session_factory() as session:
        stats = await crud.payment_stats(session, since)
    text = (
        f"💳 <b>Платежи за {days} дн.</b>\n"
        f"Успешных: {stats['count']} | Сумма: {stats['amount']:.0f}₽"
    )
    await show(cb, text, _period_kb("st:pay"))


@router.callback_query(F.data.startswith("st:tok:"))
async def tokens(cb: CallbackQuery) -> None:
    await cb.answer()
    days = int(cb.data.rsplit(":", 1)[1])
    since = crud.utcnow() - timedelta(days=days)
    async with session_factory() as session:
        t = await crud.token_stats(session, since)
    text = (
        f"🔢 <b>Токены за {days} дн.</b>\n"
        f"Запросов: {t['requests']} | Prompt: {t['prompt']} | Completion: {t['completion']} | "
        f"Cache hit: {t['cache_hit']} | Всего: {t['total']}\n"
        f"Cache rate: {t['cache_rate']:.1f}% | Примерная стоимость: ${t['cost_usd']:.4f}"
    )
    await show(cb, text, _period_kb("st:tok"))


@router.callback_query(F.data == "st:paycsv")
async def payments_csv(cb: CallbackQuery) -> None:
    await cb.answer()
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(Payment, User.telegram_id)
                .join(User, User.id == Payment.user_id)
                .order_by(Payment.id)
            )
        ).all()
    data = [
        [p.id, tg, p.robokassa_invoice_id or "", float(p.amount), p.status, p.type, p.created_at, p.paid_at or ""]
        for p, tg in rows
    ]
    file = _csv_file(
        "payments.csv",
        ["id", "telegram_id", "invoice_id", "amount", "status", "type", "created_at", "paid_at"],
        data,
    )
    await cb.message.answer_document(file, caption=f"Платежей: {len(data)}")


@router.callback_query(F.data == "st:tokcsv")
async def tokens_csv(cb: CallbackQuery) -> None:
    await cb.answer()
    day = func.date(func.timezone(settings.TIMEZONE, MessageHistory.created_at))
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(
                    day.label("day"),
                    func.count(),
                    func.sum(MessageHistory.prompt_tokens),
                    func.sum(MessageHistory.completion_tokens),
                    func.sum(MessageHistory.cache_hit_tokens),
                    func.sum(MessageHistory.tokens_used),
                )
                .where(MessageHistory.role == "assistant")
                .group_by(day)
                .order_by(day)
            )
        ).all()
    data = []
    for d, n, prompt, completion, hit, total in rows:
        prompt, completion, hit = int(prompt or 0), int(completion or 0), int(hit or 0)
        rate = hit / prompt * 100 if prompt else 0
        cost = crud.estimate_cost_usd(prompt, completion, hit)
        data.append([d, n, prompt, completion, hit, int(total or 0), f"{rate:.1f}", f"{cost:.4f}"])
    file = _csv_file(
        "tokens.csv",
        ["date", "requests", "prompt", "completion", "cache_hit", "total", "cache_rate_pct", "cost_usd"],
        data,
    )
    await cb.message.answer_document(file, caption=f"Дней: {len(data)}")
