from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _ts(**kw):
    return mapped_column(DateTime(timezone=True), **kw)


def _bool(default: bool):
    return mapped_column(
        Boolean, default=default, server_default=text("true" if default else "false")
    )


def _int(default: int = 0):
    return mapped_column(Integer, default=default, server_default=text(str(default)))


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str | None] = mapped_column(String(128))
    subscription_status: Mapped[str] = mapped_column(
        String(16), default="trial", server_default="trial"
    )
    trial_started: Mapped[datetime | None] = _ts(server_default=func.now())
    subscription_expires_at: Mapped[datetime | None] = _ts(nullable=True)
    agreed_to_privacy: Mapped[bool] = _bool(False)
    privacy_agreed_at: Mapped[datetime | None] = _ts(nullable=True)
    auto_renew: Mapped[bool] = _bool(False)
    payment_method_id: Mapped[str | None] = mapped_column(String(128))
    last_payment_at: Mapped[datetime | None] = _ts(nullable=True)
    # Для trial — общее число сообщений за весь пробный период (не сбрасывается),
    # для active — число сообщений за текущие сутки.
    messages_today: Mapped[int] = _int(0)
    daily_reset_at: Mapped[datetime | None] = _ts(nullable=True)
    last_message_at: Mapped[datetime | None] = _ts(nullable=True)
    is_banned: Mapped[bool] = _bool(False)
    banned_at: Mapped[datetime | None] = _ts(nullable=True)
    ban_reason: Mapped[str | None] = mapped_column(Text)
    is_tester: Mapped[bool] = _bool(False)
    created_at: Mapped[datetime] = _ts(server_default=func.now())


class Character(Base):
    __tablename__ = "characters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    name: Mapped[str | None] = mapped_column(String(128))
    race: Mapped[str | None] = mapped_column(String(64))
    char_class: Mapped[str | None] = mapped_column(String(64))
    subclass: Mapped[str | None] = mapped_column(String(64))
    background: Mapped[str | None] = mapped_column(String(64))
    alignment: Mapped[str | None] = mapped_column(String(32))
    level: Mapped[int] = _int(1)
    xp: Mapped[int] = _int(0)
    game_day: Mapped[int] = _int(1)
    hp: Mapped[int] = _int(0)
    max_hp: Mapped[int] = _int(0)
    ac: Mapped[int] = _int(10)
    speed: Mapped[int] = _int(30)
    attack_bonus: Mapped[int] = _int(0)
    spell_dc: Mapped[int] = _int(0)
    strength: Mapped[int] = _int(10)
    dexterity: Mapped[int] = _int(10)
    constitution: Mapped[int] = _int(10)
    intelligence: Mapped[int] = _int(10)
    wisdom: Mapped[int] = _int(10)
    charisma: Mapped[int] = _int(10)
    gold: Mapped[int] = _int(0)
    silver: Mapped[int] = _int(0)
    arrows: Mapped[int] = _int(0)
    bolts: Mapped[int] = _int(0)
    rations: Mapped[int] = _int(0)
    oil: Mapped[int] = _int(0)
    exhaustion: Mapped[int] = _int(0)
    inspiration: Mapped[bool] = _bool(False)
    location: Mapped[str | None] = mapped_column(Text)
    weapon_name: Mapped[str | None] = mapped_column(Text)
    armor_name: Mapped[str | None] = mapped_column(Text)
    spells: Mapped[list] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    spell_slots: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    languages: Mapped[list] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    companion_name: Mapped[str | None] = mapped_column(String(128))
    companion_hp: Mapped[int | None] = mapped_column(Integer)
    companion_max_hp: Mapped[int | None] = mapped_column(Integer)
    companion_loyalty: Mapped[int] = _int(0)
    world_tension: Mapped[int] = _int(0)
    in_combat: Mapped[bool] = _bool(False)
    is_resting: Mapped[bool] = _bool(False)
    is_created: Mapped[bool] = _bool(False)
    updated_at: Mapped[datetime] = _ts(server_default=func.now(), onupdate=func.now())


class InventoryItem(Base):
    __tablename__ = "inventory"
    __table_args__ = (UniqueConstraint("user_id", "slot_index", name="uq_inventory_slot"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    slot_index: Mapped[int] = mapped_column(Integer)
    item_name: Mapped[str] = mapped_column(Text)
    quantity: Mapped[int] = _int(1)


class FactionReputation(Base):
    __tablename__ = "faction_reputation"
    __table_args__ = (UniqueConstraint("user_id", "faction_name", name="uq_faction_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    faction_name: Mapped[str] = mapped_column(String(128))
    reputation: Mapped[int] = _int(0)


class EncyclopediaEntry(Base):
    __tablename__ = "encyclopedia"
    __table_args__ = (UniqueConstraint("user_id", "entry_name", name="uq_encyclopedia_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    entry_name: Mapped[str] = mapped_column(String(256))
    value: Mapped[str] = mapped_column(Text)


class MessageHistory(Base):
    __tablename__ = "message_history"
    __table_args__ = (Index("ix_message_history_user_created", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    tokens_used: Mapped[int] = _int(0)
    prompt_tokens: Mapped[int] = _int(0)
    completion_tokens: Mapped[int] = _int(0)
    cache_hit_tokens: Mapped[int] = _int(0)
    # После сжатия истории текст стирается, а строка остаётся ради статистики токенов.
    archived: Mapped[bool] = _bool(False)
    created_at: Mapped[datetime] = _ts(server_default=func.now(), index=True)


class StorySummary(Base):
    __tablename__ = "story_summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    summary: Mapped[str] = mapped_column(Text)
    messages_covered: Mapped[int] = _int(0)
    created_at: Mapped[datetime] = _ts(server_default=func.now())


class SecurityLog(Base):
    __tablename__ = "security_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(Integer)
    telegram_id: Mapped[int | None] = mapped_column(BigInteger)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    details: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _ts(server_default=func.now())


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    robokassa_invoice_id: Mapped[str | None] = mapped_column(String(64))
    amount: Mapped[float] = mapped_column(Numeric(10, 2))
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    type: Mapped[str] = mapped_column(String(16), default="one_time", server_default="one_time")
    created_at: Mapped[datetime] = _ts(server_default=func.now())
    paid_at: Mapped[datetime | None] = _ts(nullable=True)


class AISettings(Base):
    __tablename__ = "ai_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model: Mapped[str] = mapped_column(String(64))
    temperature: Mapped[float] = mapped_column(Float, default=0.7, server_default="0.7")
    max_tokens: Mapped[int] = _int(2000)
    max_history_messages: Mapped[int] = _int(10)
    compression_threshold: Mapped[int] = _int(18)
    system_prompt_version: Mapped[str] = mapped_column(
        String(32), default="v1", server_default="v1"
    )
    updated_at: Mapped[datetime] = _ts(server_default=func.now(), onupdate=func.now())


class PromptModule(Base):
    __tablename__ = "prompt_modules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    trigger_type: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text, default="", server_default="")
    is_active: Mapped[bool] = _bool(True)
    sort_order: Mapped[int] = _int(0)
    updated_at: Mapped[datetime] = _ts(server_default=func.now(), onupdate=func.now())


class GameAnalytics(Base):
    __tablename__ = "game_analytics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[date] = mapped_column(Date, unique=True)
    total_users: Mapped[int] = _int(0)
    active_subscriptions: Mapped[int] = _int(0)
    trial_users: Mapped[int] = _int(0)
    expired_users: Mapped[int] = _int(0)
    banned_users: Mapped[int] = _int(0)
    dau: Mapped[int] = _int(0)
    mau: Mapped[int] = _int(0)
    messages_today: Mapped[int] = _int(0)
    total_tokens: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    prompt_tokens: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    completion_tokens: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    cache_hit_tokens: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    cache_rate: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    estimated_cost_usd: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    successful_payments: Mapped[int] = _int(0)
    revenue_rub: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    created_at: Mapped[datetime] = _ts(server_default=func.now())
