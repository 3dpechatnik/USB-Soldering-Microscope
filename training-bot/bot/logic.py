from __future__ import annotations

SPLITS_45 = {
    "morning": (8, 13, 14, 10),
    "day": (8, 10, 17, 10),
    "evening": (8, 14, 11, 12),
    "night": (8, 14, 13, 10),
    "outdoor": (8, 10, 17, 10),
}

CALM_IDS = ("yoga", "qigong", "beloyar", "monk")
ACTIVE_IDS = {
    "male": ("witcher", "blade", "thor", "spider"),
    "female": ("widow", "elektra", "valkyrie", "nikita"),
}
TIME_IDS = ("morning", "day", "evening", "night", "outdoor")
FREE_LIMIT = 21


def duration_for(hours: int) -> int:
    if hours < 30:
        return 45
    if hours < 100:
        return 60
    return 90


def band_for(hours: int) -> str:
    if hours < 30:
        return "early"
    if hours < 100:
        return "student"
    if hours < 250:
        return "craft"
    return "late"


def short_calm(hours: int, time_of_day: str) -> bool:
    return hours >= 300 and time_of_day != "night"


def scale_split(base: tuple[int, int, int, int], target: int) -> tuple[int, int, int, int]:
    raw = [part * target / 45 for part in base]
    floors = [int(part) for part in raw]
    remainder = target - sum(floors)
    order = sorted(range(4), key=lambda index: -(raw[index] - floors[index]))
    for index in range(remainder):
        floors[order[index]] += 1
    return (floors[0], floors[1], floors[2], floors[3])


def apply_short_calm(
    split: tuple[int, int, int, int], night: bool
) -> tuple[int, int, int, int]:
    if night:
        return split
    warmup, calm, active, cooldown = split
    extra = 0
    if calm > 5:
        extra += calm - 5
        calm = 5
    if warmup > 6:
        extra += warmup - 6
        warmup = 6
    active += extra
    return (warmup, calm, active, cooldown)


def minutes_for(hours: int, time_of_day: str) -> tuple[int, int, int, int]:
    base = SPLITS_45[time_of_day]
    scaled = scale_split(base, duration_for(hours))
    return apply_short_calm(scaled, time_of_day == "night")


def block_reason(is_admin: bool, hours: int, action: str) -> str | None:
    if is_admin:
        return None
    if action == "new_workout" and hours >= FREE_LIMIT:
        return "limit"
    if action == "change_schools":
        return "change"
    if action == "timer":
        return "timer"
    return None


def clock(seconds: int) -> str:
    seconds = max(0, int(seconds))
    minutes, rest = divmod(seconds, 60)
    return f"{minutes}:{rest:02d}"


FACES = "🕐🕑🕒🕓🕔🕕🕖🕗🕘🕙🕚🕛"
BLOCKS = (
    ("warmup", ("warmup",)),
    ("main", ("calm", "active")),
    ("ending", ("cooldown",)),
)


def timer_bar(left: int, total: int) -> str:
    total = max(1, int(total))
    left = max(0, min(int(left), total))
    filled = round(10 * left / total)
    return "▰" * filled + "▱" * (10 - filled)


def format_clock(left: int, total: int, step: int = 0, running: bool = False) -> str:
    face = FACES[step % len(FACES)] if running else "⏱"
    return f"<b>{face}  {clock(left)}</b>\n{timer_bar(left, total)}"


def tick_delay(left: int) -> int:
    if left > 60:
        return 5
    return 1


def progress_text(template: str, hours: int) -> str:
    done = max(0, min(10000, int(hours)))
    percent = f"{done / 100:.2f}%"
    return template.format(n=done, pct=percent)


def parse_model_json(raw: str) -> dict:
    import json

    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        text = text.rsplit("```", 1)[0]
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("session is not an object")
    return data


def validate_session(data: dict) -> dict:
    for key in ("opening", "emoji", "title", "level", "goal", "parts", "closing", "ask"):
        if key not in data:
            raise ValueError(f"missing {key}")
    parts = data["parts"]
    if not isinstance(parts, list) or not parts:
        raise ValueError("no parts")
    clean_parts = []
    for part in parts:
        role = str(part.get("role", "")).strip().lower()
        if role not in {"warmup", "calm", "active", "cooldown"}:
            raise ValueError("bad role")
        exercises = []
        for item in part.get("exercises") or []:
            name = str(item.get("name", "")).strip()
            text = str(item.get("text", "")).strip()
            if not name or not text:
                continue
            seconds = int(item.get("seconds", 60))
            seconds = min(3600, max(10, seconds))
            exercises.append({"name": name, "seconds": seconds, "text": text})
        if not exercises:
            raise ValueError("empty part")
        clean_parts.append(
            {
                "name": str(part.get("name", "")).strip() or role,
                "role": role,
                "exercises": exercises[:8],
            }
        )
    data = {
        "opening": str(data.get("opening", "")).strip(),
        "emoji": str(data.get("emoji", "")).strip()[:8] or "•",
        "title": str(data.get("title", "")).strip() or "Тренировка",
        "level": str(data.get("level", "")).strip(),
        "goal": str(data.get("goal", "")).strip(),
        "parts": clean_parts[:4],
        "closing": str(data.get("closing", "")).strip(),
        "ask": str(data.get("ask", "")).strip(),
    }
    if not deck(data):
        raise ValueError("empty deck")
    return data


def deck(session: dict) -> list[dict]:
    grouped: dict[str, list[dict]] = {block: [] for block, _roles in BLOCKS}
    for part in session.get("parts") or []:
        role = part.get("role")
        block = next((name for name, roles in BLOCKS if role in roles), None)
        if block is None:
            continue
        for exercise in part.get("exercises") or []:
            grouped[block].append(
                {
                    "part": part.get("name") or "",
                    "name": exercise["name"],
                    "seconds": exercise["seconds"],
                    "text": exercise["text"],
                }
            )
    cards = []
    for block, _roles in BLOCKS:
        exercises = grouped[block]
        if not exercises:
            continue
        cards.append(
            {
                "type": "block",
                "block": block,
                "seconds": sum(item["seconds"] for item in exercises),
                "exercises": exercises,
            }
        )
    return cards


def exercise_names(session: dict) -> list[str]:
    names = []
    for part in session.get("parts") or []:
        for exercise in part.get("exercises") or []:
            names.append(exercise["name"])
    return names
