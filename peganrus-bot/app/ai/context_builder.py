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


def render_character(ch: Character, inventory, reps, encyclopedia) -> str:
    if not ch.is_created:
        return "[CHAR: not created]"
    companion = (
        f"{ch.companion_name} HP {ch.companion_hp}/{ch.companion_max_hp} loyalty {ch.companion_loyalty}"
        if ch.companion_name
        else "none"
    )
    parts = [
        ch.name, ch.race, ch.char_class, f"Lv{ch.level}",
        f"HP {ch.hp}/{ch.max_hp}", f"AC {ch.ac}",
        f"STR {ch.strength} DEX {ch.dexterity} CON {ch.constitution} "
        f"INT {ch.intelligence} WIS {ch.wisdom} CHA {ch.charisma}",
        f"Gold {ch.gold}" + (f" Silver {ch.silver}" if ch.silver else ""),
        f"XP {ch.xp}/{_next_level_xp(ch.level)}",
        f"Loc: {ch.location or 'unknown'}",
        f"Arrows {ch.arrows} Bolts {ch.bolts} Rations {ch.rations} Oil {ch.oil}",
        f"Exh {ch.exhaustion}",
        "Inv: " + ", ".join(f"{i.item_name} x{i.quantity}" for i in inventory),
    ]
    optional = [
        ("Spells: ", ", ".join(ch.spells or [])),
        ("Companion: ", companion if ch.companion_name else ""),
        ("Rep: ", ", ".join(f"{r.faction_name} {r.reputation:+d}" for r in reps)),
        ("Tension ", str(ch.world_tension) if ch.world_tension else ""),
        ("Lore: ", "; ".join(f"{e.entry_name}: {e.value}" for e in encyclopedia)),
    ]
    parts += [label + value for label, value in optional if value]
    return "[CHAR: " + "|".join(str(p) for p in parts) + "]"


def limit_lore(entries: list) -> list:
    """Закреплённые записи (например «Эпоха») всегда, из остальных — только последние N."""
    pinned = settings.encyclopedia_pinned
    fixed = [e for e in entries if e.entry_name in pinned]
    rest = [e for e in entries if e.entry_name not in pinned]
    limit = settings.ENCYCLOPEDIA_CONTEXT_LIMIT
    return fixed + (rest[-limit:] if limit > 0 else [])


async def character_line(session: AsyncSession, user: User, ch: Character) -> str:
    if not ch.is_created:
        return render_character(ch, [], [], [])
    inventory = await crud.get_inventory(session, user.id)
    reps = (
        await session.scalars(select(FactionReputation).where(FactionReputation.user_id == user.id))
    ).all()
    entries = (
        await session.scalars(
            select(EncyclopediaEntry)
            .where(EncyclopediaEntry.user_id == user.id)
            .order_by(EncyclopediaEntry.id)
        )
    ).all()
    return render_character(ch, inventory, reps, limit_lore(entries))


async def build_messages(
    session: AsyncSession, user: User, ch: Character, new_text: str, max_history: int
) -> list[dict]:
    modules = await crud.get_context_modules(session, active_triggers(ch))
    system_prompt = "\n\n".join(modules)

    context = await character_line(session, user, ch)
    summary = await crud.latest_summary(session, user.id)
    if summary:
        context += f"\n[SUMMARY: {summary.summary}]"

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


def SAMPLE_FOR_MEASURE() -> str:
    """Типичная строка персонажа — для замеров токенов (см. README)."""
    from types import SimpleNamespace as NS

    ch = Character(
        name="Ратибор", race="Человек", char_class="Волхв", level=3, hp=13, max_hp=13, ac=12,
        strength=10, dexterity=13, constitution=15, intelligence=9, wisdom=16, charisma=11,
        gold=15, silver=18, xp=450, game_day=4, location="Деревня", arrows=0, bolts=0, rations=5,
        oil=1, exhaustion=0, spells=["Огонёк", "Лечение ран", "Благословение"], spell_slots={"1": "2/3"},
        world_tension=0, is_created=True, companion_name=None, companion_loyalty=0,
    )
    inv = [NS(item_name=n, quantity=q) for n, q in
           [("Посох", 1), ("Кинжал", 1), ("Походный набор", 1), ("Фляга", 1), ("Зелье лечения", 2)]]
    return render_character(ch, inv, [NS(faction_name="Дружина", reputation=5)], [])
