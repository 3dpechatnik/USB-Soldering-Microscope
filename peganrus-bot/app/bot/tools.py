"""Function calling definitions for DeepSeek. Sent with EVERY request (keep them short: they are re-sent each turn)."""

_int = {"type": "integer"}
_str = {"type": "string"}
_bool = {"type": "boolean"}
_strs = {"type": "array", "items": _str}


def _items(a: str, b: str, b_type: dict) -> dict:
    return {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {a: _str, b: b_type},
            "required": [a, b],
        },
    }


_name_qty = _items("name", "qty", _int)
_slots = {"type": "object", "description": 'level -> "left/max", e.g. {"1":"2/3"}', "additionalProperties": _str}

UPDATE_STATE = {
    "type": "function",
    "function": {
        "name": "update_state",
        "description": "After a turn with changes. Only changed fields. *_delta are signed changes; others are new absolute values.",
        "parameters": {
            "type": "object",
            "properties": {
                "hp_delta": _int,
                "gold_delta": {"type": "integer", "description": "kuna"},
                "silver_delta": _int,
                "xp_delta": _int,
                "arrows_delta": _int,
                "bolts_delta": _int,
                "rations_delta": _int,
                "oil_delta": _int,
                "exhaustion_delta": _int,
                "hp": _int,
                "max_hp": _int,
                "level": _int,
                "location": _str,
                "weapon_name": _str,
                "armor_name": _str,
                "inventory_add": _name_qty,
                "inventory_remove": _name_qty,
                "spells_add": _strs,
                "spells_remove": _strs,
                "encyclopedia_update": _items("name", "value", _str),
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
        "description": "Once, when all creation steps are done.",
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
                "starting_inventory": _name_qty,
                "starting_spells": _strs,
                "spell_slots": _slots,
                "languages": _strs,
                "subclass": _str,
                "weapon_name": _str,
                "armor_name": _str,
                "location": _str,
                "gold": {"type": "integer", "description": "kuna (main currency)"},
                "silver": _int,
                "arrows": _int,
                "bolts": _int,
                "rations": _int,
                "oil": _int,
            },
            "required": [
                "race",
                "strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma",
                "hp", "max_hp", "ac", "speed", "attack_bonus", "spell_dc",
                "starting_inventory", "starting_spells", "spell_slots", "languages",
            ],
        },
    },
}

def tools_for(character_created: bool, creation_step: int = 0) -> list[dict]:
    """Только нужные функции: после создания — update_state, на финальном шаге — finalize, до этого — без функций."""
    if character_created:
        return [UPDATE_STATE]
    return [FINALIZE_CHARACTER] if creation_step >= 5 else []
