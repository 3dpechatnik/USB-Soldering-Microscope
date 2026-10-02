import logging
from datetime import timedelta

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from app.config import settings
from app.db import crud
from app.db.engine import session_factory
from app.db.models import User
from app.payments import robokassa

log = logging.getLogger(__name__)


async def expire_subscriptions() -> None:
    async with session_factory() as session:
        count = await crud.mark_expired(session)
        await session.commit()
    log.info("Просроченных подписок деактивировано: %s", count)


async def auto_renew_subscriptions() -> None:
    """Автопродление Robokassa — заглушка: находит кандидатов и пробует списать."""
    horizon = crud.local_day_bounds(crud.local_today())[1]
    async with session_factory() as session:
        users = (
            await session.scalars(
                select(User).where(
                    User.auto_renew.is_(True),
                    User.is_banned.is_(False),
                    User.subscription_status == "active",
                    User.subscription_expires_at <= horizon,
                )
            )
        ).all()
        renewed = 0
        for user in users:
            if not user.payment_method_id:
                continue
            ok = await robokassa.charge_recurring(
                user.id, user.payment_method_id, settings.SUBSCRIPTION_PRICE
            )
            if ok:
                crud.grant_subscription(user)
                user.last_payment_at = crud.utcnow()
                renewed += 1
        await session.commit()
    log.info("Автопродление: кандидатов %s, продлено %s", len(users), renewed)


async def renewal_reminders(bot: Bot) -> None:
    target = crud.local_today() + timedelta(days=settings.RENEW_REMIND_DAYS)
    start, end = crud.local_day_bounds(target)
    async with session_factory() as session:
        users = (
            await session.scalars(
                select(User).where(
                    User.auto_renew.is_(True),
                    User.is_banned.is_(False),
                    User.subscription_status == "active",
                    User.subscription_expires_at >= start,
                    User.subscription_expires_at < end,
                )
            )
        ).all()
    for user in users:
        try:
            await bot.send_message(
                user.telegram_id,
                f"🔔 Через {settings.RENEW_REMIND_DAYS} дня подписка продлится автоматически "
                f"(спишется {settings.SUBSCRIPTION_PRICE} ₽). Отключить автопродление: /autorenew",
            )
        except Exception as e:
            log.warning("Напоминание user_id=%s не доставлено: %s", user.id, e)


async def collect_daily_stats() -> None:
    async with session_factory() as session:
        await crud.save_snapshot(session, crud.local_today())
        await session.commit()
    log.info("Дневная статистика собрана")


def _hm(value: str) -> dict:
    hour, minute = value.split(":")
    return {"hour": int(hour), "minute": int(minute)}


def create_scheduler(game_bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=settings.tz)

    def add(func, at: str, job_id: str, *args) -> None:
        scheduler.add_job(
            func,
            CronTrigger(timezone=settings.tz, **_hm(at)),
            args=args,
            id=job_id,
            replace_existing=True,
            misfire_grace_time=3600,
        )

    add(expire_subscriptions, settings.SCHEDULER_EXPIRE_AT, "expire_subscriptions")
    add(auto_renew_subscriptions, settings.SCHEDULER_RENEW_AT, "auto_renew")
    add(renewal_reminders, settings.SCHEDULER_REMIND_AT, "renewal_reminders", game_bot)
    add(collect_daily_stats, settings.SCHEDULER_STATS_AT, "daily_stats")
    return scheduler
