"""Применяет вызовы функций ИИ (update_state / finalize_character_creation) к БД с клампом."""
import json
import logging
import re
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.deepseek import ToolCall
from app.db.models import (
    Character,
    EncyclopediaEntry,
    FactionReputation,
    InventoryItem,
)

log = logging.getLogger(__name__)

# Правила игры (D&D 5e), а не настройки бота.
INVENTORY_SLOTS = 36
MAX_LEVEL = 20
MAX_EXHAUSTION = 6
MAX_TENSION = 100
LOYALTY_RANGE = (-100, 100)
ABILITY_RANGE = (1, 30)

_STATE_RE = re.compile(r"\[STATE\](.*?)(?:\[/STATE\]|$)", re.DOTALL | re.IGNORECASE)


def _int(value: Any, default: int | None = None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: int, low: int, high: int | None = None) -> int:
    value = max(low, value)
    return min(value, high) if high is not None else value


def _text(value: Any, limit: int = 500) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s[:limit] if s else None


def extract_state_block(text: str) -> tuple[str, dict | None]:
    """Fallback: вырезает [STATE]{json}[/STATE] из текста и возвращает (чистый текст, данные)."""
    match = _STATE_RE.search(text)
    if not match:
        return text, None
    clean = (text[: match.start()] + text[match.end():]).strip()
    raw = match.group(1).strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
    try:
        data = json.loads(raw)
        return clean, data if isinstance(data, dict) else None
    except ValueError:
        log.warning("Не удалось разобрать [STATE]: %r", raw[:300])
        return clean, None


async def apply_tool_calls(
    session: AsyncSession, user_id: int, ch: Character, calls: list[ToolCall]
) -> list[str]:
    applied: list[str] = []
    for call in calls:
        if call.parse_error:
            continue
        try:
            if call.name == "update_state":
                await apply_update_state(session, user_id, ch, call.arguments)
                applied.append(call.name)
            elif call.name == "finalize_character_creation":
                if await apply_finalize(session, user_id, ch, call.arguments):
                    applied.append(call.name)
            else:
                log.warning("Неизвестная функция от ИИ: %s", call.name)
        except Exception:
            log.exception("Ошибка применения %s: %r", call.name, call.arguments)
    return applied


async def apply_fallback_state(
    session: AsyncSession, user_id: int, ch: Character, data: dict
) -> list[str]:
    if not ch.is_created and data.get("name") and data.get("char_class"):
        ok = await apply_finalize(session, user_id, ch, data)
        return ["finalize_character_creation"] if ok else []
    await apply_update_state(session, user_id, ch, data)
    return ["update_state"]


async def apply_update_state(
    session: AsyncSession, user_id: int, ch: Character, a: dict
) -> None:
    if (v := _int(a.get("max_hp"))) is not None:
        ch.max_hp = _clamp(v, 1)
    if (v := _int(a.get("hp"))) is not None:
        ch.hp = _clamp(v, 0, ch.max_hp)
    if (v := _int(a.get("hp_delta"))) is not None:
        ch.hp = _clamp(ch.hp + v, 0, ch.max_hp)
    if (v := _int(a.get("level"))) is not None:
        ch.level = _clamp(v, 1, MAX_LEVEL)

    for field, key in (
        ("gold", "gold_delta"), ("silver", "silver_delta"), ("xp", "xp_delta"),
        ("arrows", "arrows_delta"), ("bolts", "bolts_delta"),
        ("rations", "rations_delta"), ("oil", "oil_delta"),
    ):
        if (v := _int(a.get(key))) is not None:
            setattr(ch, field, _clamp(getattr(ch, field) + v, 0))
    if (v := _int(a.get("exhaustion_delta"))) is not None:
        ch.exhaustion = _clamp(ch.exhaustion + v, 0, MAX_EXHAUSTION)
    if (v := _int(a.get("game_day_delta"))) is not None:
        ch.game_day = _clamp(ch.game_day + v, 1)
    if (v := _int(a.get("world_tension_delta"))) is not None:
        ch.world_tension = _clamp(ch.world_tension + v, 0, MAX_TENSION)

    for field in ("location", "weapon_name", "armor_name"):
        if field in a and (s := _text(a[field], 300)) is not None:
            setattr(ch, field, s)

    if "companion" in a:
        comp = a["companion"]
        if not comp:
            ch.companion_name = ch.companion_hp = ch.companion_max_hp = None
            ch.companion_loyalty = 0
        elif isinstance(comp, dict) and (name := _text(comp.get("name"), 128)):
            max_hp = _int(comp.get("max_hp"), ch.companion_max_hp or 1)
            ch.companion_name = name
            ch.companion_max_hp = _clamp(max_hp, 1)
            ch.companion_hp = _clamp(_int(comp.get("hp"), max_hp), 0, ch.companion_max_hp)
            ch.companion_loyalty = _clamp(_int(comp.get("loyalty"), 0), *LOYALTY_RANGE)

    for item in a.get("inventory_add") or []:
        await _inventory_add(session, user_id, item)
    for item in a.get("inventory_remove") or []:
        await _inventory_remove(session, user_id, item)

    spells = list(ch.spells or [])
    for s in a.get("spells_add") or []:
        if (name := _text(s, 100)) and name.lower() not in {x.lower() for x in spells}:
            spells.append(name)
    for s in a.get("spells_remove") or []:
        if name := _text(s, 100):
            spells = [x for x in spells if x.lower() != name.lower()]
    ch.spells = spells

    if isinstance(a.get("spell_slots_update"), dict):
        slots = dict(ch.spell_slots or {})
        for level, val in a["spell_slots_update"].items():
            slots[str(level)] = str(val)
        ch.spell_slots = slots

    for rep in a.get("reputation_delta") or []:
        await _reputation(session, user_id, rep)
    for entry in a.get("encyclopedia_update") or []:
        await _encyclopedia(session, user_id, entry)

    if isinstance(a.get("in_combat"), bool):
        ch.in_combat = a["in_combat"]
    if isinstance(a.get("is_resting"), bool):
        ch.is_resting = a["is_resting"]


async def _load_inventory(session: AsyncSession, user_id: int) -> list[InventoryItem]:
    rows = await session.scalars(
        select(InventoryItem).where(InventoryItem.user_id == user_id).order_by(InventoryItem.slot_index)
    )
    return list(rows)


async def _inventory_add(session: AsyncSession, user_id: int, item: Any) -> None:
    if not isinstance(item, dict):
        return
    name, qty = _text(item.get("name"), 200), _clamp(_int(item.get("qty"), 1), 1)
    if not name:
        return
    rows = await _load_inventory(session, user_id)
    for row in rows:
        if row.item_name.lower() == name.lower():
            row.quantity += qty
            return
    used = {r.slot_index for r in rows}
    free = next((i for i in range(INVENTORY_SLOTS) if i not in used), None)
    if free is None:
        log.info("Инвентарь user_id=%s полон, '%s' не добавлен", user_id, name)
        return
    session.add(InventoryItem(user_id=user_id, slot_index=free, item_name=name, quantity=qty))
    await session.flush()


async def _inventory_remove(session: AsyncSession, user_id: int, item: Any) -> None:
    if not isinstance(item, dict):
        return
    name, qty = _text(item.get("name"), 200), _clamp(_int(item.get("qty"), 1), 1)
    if not name:
        return
    for row in await _load_inventory(session, user_id):
        if row.item_name.lower() == name.lower():
            row.quantity -= qty
            if row.quantity <= 0:
                await session.delete(row)
                await session.flush()
            return


async def _reputation(session: AsyncSession, user_id: int, rep: Any) -> None:
    if not isinstance(rep, dict):
        return
    faction, delta = _text(rep.get("faction"), 128), _int(rep.get("delta"))
    if not faction or delta is None:
        return
    row = await session.scalar(
        select(FactionReputation).where(
            FactionReputation.user_id == user_id, FactionReputation.faction_name == faction
        )
    )
    if row is None:
        session.add(FactionReputation(user_id=user_id, faction_name=faction, reputation=delta))
    else:
        row.reputation += delta
    await session.flush()


async def _encyclopedia(session: AsyncSession, user_id: int, entry: Any) -> None:
    if not isinstance(entry, dict):
        return
    name, value = _text(entry.get("name"), 256), _text(entry.get("value"), 1000)
    if not name or not value:
        return
    row = await session.scalar(
        select(EncyclopediaEntry).where(
            EncyclopediaEntry.user_id == user_id, EncyclopediaEntry.entry_name == name
        )
    )
    if row is None:
        session.add(EncyclopediaEntry(user_id=user_id, entry_name=name, value=value))
    else:
        row.value = value
    await session.flush()


async def apply_finalize(
    session: AsyncSession, user_id: int, ch: Character, a: dict
) -> bool:
    if ch.is_created:
        log.info("finalize_character_creation повторно для user_id=%s — игнорируем", user_id)
        return False
    name, race, char_class = _text(a.get("name"), 128), _text(a.get("race"), 64), _text(a.get("char_class"), 64)
    if not (name and race and char_class):
        log.warning("finalize без name/race/class: %r", a)
        return False

    ch.name, ch.race, ch.char_class = name, race, char_class
    ch.background = _text(a.get("background"), 64)
    ch.alignment = _text(a.get("alignment"), 32)
    for field in ("strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma"):
        ch_val = _int(a.get(field), 10)
        setattr(ch, field, _clamp(ch_val, *ABILITY_RANGE))
    ch.max_hp = _clamp(_int(a.get("max_hp"), _int(a.get("hp"), 1)), 1)
    ch.hp = _clamp(_int(a.get("hp"), ch.max_hp), 0, ch.max_hp)
    ch.ac = _clamp(_int(a.get("ac"), 10), 0)
    ch.speed = _clamp(_int(a.get("speed"), 30), 0)
    ch.attack_bonus = _int(a.get("attack_bonus"), 0)
    ch.spell_dc = _int(a.get("spell_dc"), 0)
    ch.level, ch.xp, ch.game_day = 1, 0, 1
    ch.subclass = _text(a.get("subclass"), 64)
    for field in ("weapon_name", "armor_name", "location"):
        setattr(ch, field, _text(a.get(field), 300))
    for field in ("gold", "silver", "arrows", "bolts", "rations", "oil"):
        setattr(ch, field, _clamp(_int(a.get(field), 0), 0))

    await session.execute(delete(InventoryItem).where(InventoryItem.user_id == user_id))
    await session.flush()
    for item in a.get("starting_inventory") or []:
        await _inventory_add(session, user_id, item)

    ch.spells = [s for s in (_text(x, 100) for x in a.get("starting_spells") or []) if s]
    slots = a.get("spell_slots")
    ch.spell_slots = {str(k): str(v) for k, v in slots.items()} if isinstance(slots, dict) else {}
    ch.languages = [s for s in (_text(x, 64) for x in a.get("languages") or []) if s]
    ch.in_combat = ch.is_resting = False
    ch.is_created = True
    return True
