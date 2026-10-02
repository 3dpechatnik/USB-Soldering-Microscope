"""Сборка messages[] для DeepSeek. Системный промпт одинаков для всех -> работает кэш."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import crud
from app.db.models import Character, EncyclopediaEntry, FactionReputation, User


def active_triggers(ch: Character) -> list[str]:
    triggers = ["always"]
    if not ch.is_created:
        triggers.append("character_creation")
    if ch.in_combat:
        triggers.append("combat")
    if ch.is_resting:
        triggers.append("rest")
    location = (ch.location or "").lower()
    if location and any(k in location for k in settings.trading_keywords):
        triggers.append("trading")
    return triggers


def _next_level_xp(level: int) -> str:
    thresholds = settings.xp_thresholds
    return str(thresholds[level]) if 0 < level < len(thresholds) else "-"


def _slots(slots: dict) -> str:
    return ",".join(f"{k}:{v}" for k, v in sorted(slots.items(), key=lambda kv: str(kv[0])))


async def character_line(session: AsyncSession, user: User, ch: Character) -> str:
    if not ch.is_created:
        return "[ПЕРСОНАЖ: не создан]"
    inventory = await crud.get_inventory(session, user.id)
    reps = (
        await session.scalars(select(FactionReputation).where(FactionReputation.user_id == user.id))
    ).all()
    encyclopedia = (
        await session.scalars(select(EncyclopediaEntry).where(EncyclopediaEntry.user_id == user.id))
    ).all()
    companion = (
        f"{ch.companion_name} HP:{ch.companion_hp}/{ch.companion_max_hp} Лоял:{ch.companion_loyalty}"
        if ch.companion_name
        else "нет"
    )
    parts = [
        ch.name, ch.race, ch.char_class, f"Ур {ch.level}",
        f"HP:{ch.hp}/{ch.max_hp}", f"AC:{ch.ac}",
        f"Сил:{ch.strength}", f"Лов:{ch.dexterity}", f"Кон:{ch.constitution}",
        f"Инт:{ch.intelligence}", f"Мдр:{ch.wisdom}", f"Хар:{ch.charisma}",
        f"Золото:{ch.gold}", f"Серебро:{ch.silver}",
        f"XP:{ch.xp}/{_next_level_xp(ch.level)}", f"День:{ch.game_day}",
        f"Локация:{ch.location or 'неизвестно'}",
        f"Стрелы:{ch.arrows}", f"Болты:{ch.bolts}", f"Рационы:{ch.rations}", f"Масло:{ch.oil}",
        f"Истощение:{ch.exhaustion}",
        "Инвентарь:[" + ", ".join(f"{i.item_name} x{i.quantity}" for i in inventory) + "]",
        "Заклинания:[" + ", ".join(ch.spells or []) + "]",
        "Слоты:[" + _slots(ch.spell_slots or {}) + "]",
        f"Спутник:{companion}",
        "Репутация:[" + ", ".join(f"{r.faction_name}:{r.reputation:+d}" for r in reps) + "]",
        f"Напряжение:{ch.world_tension}",
    ]
    if encyclopedia:
        parts.append("Энциклопедия:[" + "; ".join(f"{e.entry_name}: {e.value}" for e in encyclopedia) + "]")
    return "[ПЕРСОНАЖ: " + "|".join(str(p) for p in parts) + "]"


async def build_messages(
    session: AsyncSession, user: User, ch: Character, new_text: str, max_history: int
) -> list[dict]:
    modules = await crud.get_context_modules(session, active_triggers(ch))
    system_prompt = "\n\n".join(modules)

    context = await character_line(session, user, ch)
    summary = await crud.latest_summary(session, user.id)
    if summary:
        context += f"\n[КРАТКОЕ СОДЕРЖАНИЕ: {summary.summary}]"

    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": context},
        {"role": "assistant", "content": settings.ASSISTANT_PRIMER},
    ]
    for h in await crud.get_history(session, user.id, max_history):
        messages.append({"role": h.role, "content": h.content})
    reminder = (await crud.get_text(session, "turn_reminder")).strip()
    messages.append({"role": "user", "content": f"{new_text}\n\n{reminder}" if reminder else new_text})
    return messages
