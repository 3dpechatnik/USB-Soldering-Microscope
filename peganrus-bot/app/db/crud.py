from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import (
    AISettings,
    Character,
    GameAnalytics,
    InventoryItem,
    MessageHistory,
    Payment,
    PromptModule,
    SecurityLog,
    StorySummary,
    User,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def local_today() -> date:
    return datetime.now(settings.tz).date()


def local_day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=settings.tz)
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


def next_local_midnight() -> datetime:
    return local_day_bounds(local_today())[1]


# ---------- users ----------

async def get_user_by_tg(session: AsyncSession, telegram_id: int) -> User | None:
    return await session.scalar(select(User).where(User.telegram_id == telegram_id))


async def get_or_create_user(
    session: AsyncSession, telegram_id: int, username: str | None, first_name: str | None
) -> User:
    user = await get_user_by_tg(session, telegram_id)
    if user is None:
        user = User(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
            subscription_status="trial",
            trial_started=utcnow(),
        )
        session.add(user)
        await session.flush()
    elif user.username != username or user.first_name != first_name:
        user.username = username
        user.first_name = first_name
    return user


def subscription_is_valid(user: User) -> bool:
    return (
        user.subscription_status == "active"
        and user.subscription_expires_at is not None
        and user.subscription_expires_at > utcnow()
    )


def grant_subscription(user: User, days: int | None = None) -> None:
    days = days or settings.SUBSCRIPTION_DAYS
    base = utcnow()
    if subscription_is_valid(user):
        base = user.subscription_expires_at
    user.subscription_status = "active"
    user.subscription_expires_at = base + timedelta(days=days)
    user.messages_today = 0
    user.daily_reset_at = next_local_midnight()


# ---------- settings / prompts ----------

async def get_ai_settings(session: AsyncSession) -> AISettings:
    row = await session.get(AISettings, 1, populate_existing=True)
    if row is None:
        raise RuntimeError("ai_settings пуста — запустите: py -m app.db.seed")
    return row


async def get_text(session: AsyncSession, name: str) -> str:
    from app.db.seed import DEFAULT_TEXTS

    content = await session.scalar(select(PromptModule.content).where(PromptModule.name == name))
    return content if content else DEFAULT_TEXTS.get(name, "")


async def get_context_modules(session: AsyncSession, triggers: list[str]) -> list[str]:
    rows = await session.scalars(
        select(PromptModule)
        .where(PromptModule.is_active.is_(True), PromptModule.trigger_type.in_(triggers))
        .order_by(PromptModule.sort_order, PromptModule.id)
    )
    return [m.content.strip() for m in rows if m.content and m.content.strip()]


# ---------- character / inventory ----------

async def get_character(session: AsyncSession, user_id: int) -> Character | None:
    return await session.scalar(select(Character).where(Character.user_id == user_id))


async def get_or_create_character(session: AsyncSession, user_id: int) -> Character:
    ch = await get_character(session, user_id)
    if ch is None:
        ch = Character(user_id=user_id)
        session.add(ch)
        await session.flush()
    return ch


async def get_inventory(session: AsyncSession, user_id: int) -> list[InventoryItem]:
    rows = await session.scalars(
        select(InventoryItem).where(InventoryItem.user_id == user_id).order_by(InventoryItem.slot_index)
    )
    return list(rows)


# ---------- history ----------

async def get_history(session: AsyncSession, user_id: int, limit: int) -> list[MessageHistory]:
    rows = await session.scalars(
        select(MessageHistory)
        .where(MessageHistory.user_id == user_id, MessageHistory.archived.is_(False))
        .order_by(MessageHistory.id.desc())
        .limit(limit)
    )
    return list(reversed(list(rows)))


async def count_history(session: AsyncSession, user_id: int) -> int:
    return (
        await session.scalar(
            select(func.count())
            .select_from(MessageHistory)
            .where(MessageHistory.user_id == user_id, MessageHistory.archived.is_(False))
        )
        or 0
    )


async def last_assistant_message(session: AsyncSession, user_id: int) -> str | None:
    return await session.scalar(
        select(MessageHistory.content)
        .where(
            MessageHistory.user_id == user_id,
            MessageHistory.role == "assistant",
            MessageHistory.archived.is_(False),
        )
        .order_by(MessageHistory.id.desc())
        .limit(1)
    )


async def latest_summary(session: AsyncSession, user_id: int) -> StorySummary | None:
    return await session.scalar(
        select(StorySummary)
        .where(StorySummary.user_id == user_id)
        .order_by(StorySummary.id.desc())
        .limit(1)
    )


# ---------- security log ----------

async def log_security(
    session: AsyncSession,
    event_type: str,
    user_id: int | None = None,
    telegram_id: int | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    session.add(
        SecurityLog(
            user_id=user_id, telegram_id=telegram_id, event_type=event_type, details=details or {}
        )
    )


async def delete_user_cascade(session: AsyncSession, user_id: int) -> None:
    await session.execute(delete(User).where(User.id == user_id))


# ---------- statistics ----------

def estimate_cost_usd(prompt: int, completion: int, cache_hit: int) -> float:
    miss = max(prompt - cache_hit, 0)
    return (
        miss * settings.DEEPSEEK_PRICE_INPUT_MISS
        + cache_hit * settings.DEEPSEEK_PRICE_INPUT_HIT
        + completion * settings.DEEPSEEK_PRICE_OUTPUT
    ) / 1_000_000


async def token_stats(session: AsyncSession, since: datetime, until: datetime | None = None) -> dict:
    cond = [MessageHistory.role == "assistant", MessageHistory.created_at >= since]
    if until is not None:
        cond.append(MessageHistory.created_at < until)
    row = (
        await session.execute(
            select(
                func.count(),
                func.coalesce(func.sum(MessageHistory.prompt_tokens), 0),
                func.coalesce(func.sum(MessageHistory.completion_tokens), 0),
                func.coalesce(func.sum(MessageHistory.cache_hit_tokens), 0),
                func.coalesce(func.sum(MessageHistory.tokens_used), 0),
            ).where(and_(*cond))
        )
    ).one()
    requests, prompt, completion, hit, total = (int(x) for x in row)
    return {
        "requests": requests,
        "prompt": prompt,
        "completion": completion,
        "cache_hit": hit,
        "total": total,
        "cache_rate": (hit / prompt * 100) if prompt else 0.0,
        "cost_usd": estimate_cost_usd(prompt, completion, hit),
    }


async def payment_stats(session: AsyncSession, since: datetime, until: datetime | None = None) -> dict:
    cond = [Payment.status == "succeeded", Payment.paid_at >= since]
    if until is not None:
        cond.append(Payment.paid_at < until)
    count, total = (
        await session.execute(
            select(func.count(), func.coalesce(func.sum(Payment.amount), 0)).where(and_(*cond))
        )
    ).one()
    return {"count": int(count), "amount": float(total)}


async def user_counts(session: AsyncSession) -> dict:
    now = utcnow()
    total = await session.scalar(select(func.count()).select_from(User)) or 0

    async def cnt(*cond) -> int:
        return await session.scalar(select(func.count()).select_from(User).where(*cond)) or 0

    return {
        "total": total,
        "active": await cnt(User.subscription_status == "active", User.subscription_expires_at > now),
        "trial": await cnt(User.subscription_status == "trial"),
        "expired": await cnt(User.subscription_status == "expired"),
        "banned": await cnt(User.is_banned.is_(True)),
    }


async def active_users(session: AsyncSession, since: datetime, until: datetime) -> int:
    return (
        await session.scalar(
            select(func.count(func.distinct(MessageHistory.user_id))).where(
                MessageHistory.role == "user",
                MessageHistory.created_at >= since,
                MessageHistory.created_at < until,
            )
        )
        or 0
    )


async def messages_count(session: AsyncSession, since: datetime, until: datetime) -> int:
    return (
        await session.scalar(
            select(func.count()).where(
                MessageHistory.role == "user",
                MessageHistory.created_at >= since,
                MessageHistory.created_at < until,
            )
        )
        or 0
    )


async def compute_snapshot(session: AsyncSession, day: date) -> dict:
    start, end = local_day_bounds(day)
    counts = await user_counts(session)
    tokens = await token_stats(session, start, end)
    pays = await payment_stats(session, start, end)
    return {
        "date": day,
        "total_users": counts["total"],
        "active_subscriptions": counts["active"],
        "trial_users": counts["trial"],
        "expired_users": counts["expired"],
        "banned_users": counts["banned"],
        "dau": await active_users(session, start, end),
        "mau": await active_users(session, end - timedelta(days=30), end),
        "messages_today": await messages_count(session, start, end),
        "total_tokens": tokens["total"],
        "prompt_tokens": tokens["prompt"],
        "completion_tokens": tokens["completion"],
        "cache_hit_tokens": tokens["cache_hit"],
        "cache_rate": tokens["cache_rate"],
        "estimated_cost_usd": tokens["cost_usd"],
        "successful_payments": pays["count"],
        "revenue_rub": pays["amount"],
    }


async def save_snapshot(session: AsyncSession, day: date) -> dict:
    data = await compute_snapshot(session, day)
    row = await session.scalar(select(GameAnalytics).where(GameAnalytics.date == day))
    if row is None:
        session.add(GameAnalytics(**data))
    else:
        for k, v in data.items():
            setattr(row, k, v)
    return data


async def analytics_since(session: AsyncSession, days: int) -> list[GameAnalytics]:
    since = local_today() - timedelta(days=days - 1)
    rows = await session.scalars(
        select(GameAnalytics).where(GameAnalytics.date >= since).order_by(GameAnalytics.date)
    )
    return list(rows)


async def mark_expired(session: AsyncSession) -> int:
    res = await session.execute(
        update(User)
        .where(
            User.subscription_status == "active",
            User.subscription_expires_at.is_not(None),
            User.subscription_expires_at <= utcnow(),
        )
        .values(subscription_status="expired")
    )
    return res.rowcount or 0
