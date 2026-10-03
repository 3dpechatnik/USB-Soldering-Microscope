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

log = logging.getLogger(__name__)


async def expire_subscriptions() -> None:
    async with session_factory() as session:
        count = await crud.mark_expired(session)
        await session.commit()
    log.info("Просроченных подписок деактивировано: %s", count)


async def renewal_reminders(bot: Bot) -> None:
    """Напоминание без списания: подписка сама не продлевается."""
    target = crud.local_today() + timedelta(days=settings.RENEW_REMIND_DAYS)
    start, end = crud.local_day_bounds(target)
    async with session_factory() as session:
        users = (
            await session.scalars(
                select(User).where(
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
                f"🔔 Через {settings.RENEW_REMIND_DAYS} дня закончится оплаченный период. "
                f"Автоматических списаний нет. Продлить вручную: /subscribe "
                f"({settings.SUBSCRIPTION_PRICE} ₽ на {settings.SUBSCRIPTION_DAYS} дней).",
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
    add(renewal_reminders, settings.SCHEDULER_REMIND_AT, "renewal_reminders", game_bot)
    add(collect_daily_stats, settings.SCHEDULER_STATS_AT, "daily_stats")
    return scheduler
