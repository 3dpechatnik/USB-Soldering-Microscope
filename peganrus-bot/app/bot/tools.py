"""Определения function calling для DeepSeek. Передаются в КАЖДОМ запросе."""

_int = {"type": "integer"}
_str = {"type": "string"}
_bool = {"type": "boolean"}


def _named_qty(desc: str) -> dict:
    return {
        "type": "array",
        "description": desc,
        "items": {
            "type": "object",
            "properties": {"name": _str, "qty": {"type": "integer", "minimum": 1}},
            "required": ["name", "qty"],
        },
    }


UPDATE_STATE = {
    "type": "function",
    "function": {
        "name": "update_state",
        "description": (
            "Вызывай после каждого хода, где что-то изменилось. Передавай ТОЛЬКО изменившиеся "
            "поля. Арифметику считаешь ты: дельты — изменение со знаком (урон = отрицательное "
            "hp_delta), абсолютные значения — новое итоговое значение."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "hp_delta": {**_int, "description": "Изменение HP (урон < 0, лечение > 0)"},
                "gold_delta": _int,
                "silver_delta": _int,
                "xp_delta": _int,
                "arrows_delta": _int,
                "bolts_delta": _int,
                "rations_delta": _int,
                "oil_delta": _int,
                "exhaustion_delta": _int,
                "game_day_delta": {**_int, "description": "Сколько игровых дней прошло"},
                "world_tension_delta": {**_int, "description": "Изменение напряжения мира"},
                "hp": {**_int, "description": "Абсолютное HP"},
                "max_hp": _int,
                "level": _int,
                "location": {**_str, "description": "Текущая локация"},
                "weapon_name": _str,
                "armor_name": _str,
                "companion": {
                    "type": ["object", "null"],
                    "description": "Спутник или null, если спутника больше нет",
                    "properties": {
                        "name": _str,
                        "hp": _int,
                        "max_hp": _int,
                        "loyalty": _int,
                    },
                },
                "inventory_add": _named_qty("Предметы, которые добавились в инвентарь"),
                "inventory_remove": _named_qty("Предметы, которые убрались из инвентаря"),
                "spells_add": {"type": "array", "items": _str},
                "spells_remove": {"type": "array", "items": _str},
                "spell_slots_update": {
                    "type": "object",
                    "description": (
                        "Слоты заклинаний: ключ — уровень, значение — строка 'осталось/всего', "
                        "например {\"1\": \"2/3\", \"2\": \"1/2\"}"
                    ),
                    "additionalProperties": _str,
                },
                "reputation_delta": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"faction": _str, "delta": _int},
                        "required": ["faction", "delta"],
                    },
                },
                "encyclopedia_update": {
                    "type": "array",
                    "description": "Важные NPC, места, квесты, факты (имя -> описание)",
                    "items": {
                        "type": "object",
                        "properties": {"name": _str, "value": _str},
                        "required": ["name", "value"],
                    },
                },
                "in_combat": _bool,
                "is_resting": _bool,
            },
        },
    },
}

FINALIZE_CHARACTER = {
    "type": "function",
    "function": {
        "name": "finalize_character_creation",
        "description": (
            "Вызывается ОДИН раз после подтверждения игроком листа персонажа на шаге 7."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": _str,
                "race": _str,
                "char_class": _str,
                "background": _str,
                "alignment": _str,
                "strength": _int,
                "dexterity": _int,
                "constitution": _int,
                "intelligence": _int,
                "wisdom": _int,
                "charisma": _int,
                "hp": _int,
                "max_hp": _int,
                "ac": _int,
                "speed": _int,
                "attack_bonus": _int,
                "spell_dc": _int,
                "starting_inventory": _named_qty("Стартовое снаряжение"),
                "starting_spells": {"type": "array", "items": _str},
                "spell_slots": {
                    "type": "object",
                    "description": "Слоты: ключ — уровень, значение 'осталось/всего', например {\"1\": \"2/2\"}",
                    "additionalProperties": _str,
                },
                "languages": {"type": "array", "items": _str},
                "subclass": _str,
                "weapon_name": _str,
                "armor_name": _str,
                "location": {**_str, "description": "Стартовая локация"},
                "gold": {**_int, "description": "Стартовое золото (не клади деньги в инвентарь)"},
                "silver": _int,
                "arrows": _int,
                "bolts": _int,
                "rations": _int,
                "oil": _int,
            },
            "required": [
                "name", "race", "char_class", "background", "alignment",
                "strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma",
                "hp", "max_hp", "ac", "speed", "attack_bonus", "spell_dc",
                "starting_inventory", "starting_spells", "spell_slots", "languages",
            ],
        },
    },
}

TOOLS = [UPDATE_STATE, FINALIZE_CHARACTER]
