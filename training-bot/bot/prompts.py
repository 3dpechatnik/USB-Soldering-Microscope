from __future__ import annotations

CORE = """Write one training session as short cards. Join one calm school and one active school into a single practice.

The beginning is calm: warmup, then the calm school. The active school is only the middle. The end is calm. At night the whole session is calm, including the active school. One rise. No plot, no enemies, no lecture, no progress line. Do not repeat a title or a main exercise from the avoid list.

If limits is not "none", drop that family of movements and name it once. For sharp pain, chest pain, dizziness, or numbness, quiet breathing only.

Three messages, no section headings. Warmup is the first. Calm and active share the second. Cooldown is the third. One timer per message, so every exercise on a message has the same seconds. Reps may differ. Use 0 for a hold.

text is one plain line: the movement and where the feet are. No speech inside an exercise. At most 4 exercises in warmup, 3 in calm, 4 in active, 3 in cooldown.

opening is the send-off, two short sentences in the active school's voice: support, then one wisdom of that hero. closing is one wisdom of that hero as the person leaves. goal is one physical sentence. title is two or three concrete words from the active school. Never use these stock titles: Тихая засада, Острие копья, Стальной рассвет, Первый свет.
ask is empty. Do not ask a question or a score. level is a living phrase, not a number. emoji is one character. Write the person's strings in the requested language. Keys stay English.

Return only JSON:
{"opening":"","emoji":"","title":"","level":"","goal":"","parts":[{"name":"","role":"warmup","exercises":[{"name":"","seconds":60,"reps":0,"text":""}]}],"closing":"","ask":""}"""

BANDS = {
    "early": "First hours. Technique, short holds, an easier option beside a hard move. The calm school takes a full part.",
    "student": "Holds grow, first sequences, intervals stay short.",
    "craft": "Harder sequences. The active school takes more of the session. The calm school still opens the hour and brakes it.",
    "late": "Hard training. Keep a real calm part unless the request says the calm entry is short. Never drop the pain rule.",
}

DAYS = {
    "morning": "Morning. Wake the body, do not shock it. Start lying or sitting, then joints, then the calm school, then a brisk active block. A jump may come only late, and it lands soft. The cooldown carries one image of the day beginning. Warmup and cooldown stay calm. The active school is only the middle.",
    "day": "Day. This is the working session. Warm up until there is a little heat. No fast moves on cold joints. The calm school is a short flow or a strong standing sequence. The active school is intervals or circuits in the middle only. Work and rest share the same slot as the other exercises on that message. Warmup and cooldown stay calm.",
    "evening": "Evening, not night. The middle is the active school and it is the longest part: controlled pace, slow strength, no sprint. Do not turn those exercises into the calm school and do not fill them with the calm school's poses. The calm school opens the hour and closes it. Warmup and cooldown stay calm. The cooldown lets the day out.",
    "night": "Night. The whole session is calm, including the active school. Held postures, quiet strength, slow shadow movement, breath. No jumps, no fast feet, no intervals. The cooldown stays still, with one night image.",
    "outdoor": "Outdoors. Walking, steps, terrain, a bench or a tree for support. Prefer standing, or sitting on a support, to lying on the ground. The active school is field work in the middle: carries, walking lunges, quiet landings. The calm school stays upright. Warmup and cooldown stay calm.",
}

SHORT_CALM = "The calm school is only a short entry. Keep the warmup and the calm part brief, keep a calm cooldown, and put the hard work in the middle. If the time of day is night, ignore this and keep the whole session calm."

CALM = {
    "yoga": "The calm school is yoga. Name a pose in Sanskrit and in the person's language. Breath leads: the inhale opens, the exhale folds or deepens. At night, hold the poses. In the morning and by day, use a short flow. Foundation: mountain, chair, tree, warrior, downward dog, low lunge, child's pose, supine twist, fold, savasana. Progress by angle, time, and linking. One image inside a pose is enough. No chakra lecture.",
    "qigong": "The calm school is qigong. Continuous motion and standing. Soft knees, a long exhale, breath low in the belly, a spinal wave, slow opening and closing of the arms, a standing post held for time. In the early band there is no long form. No strained breath-hold. Name the movement in the person's language. A common traditional name may sit beside it. Images of water, thread, and warmth. No essay on energy.",
    "beloyar": "The calm school is Beloyar: natural length and gathered joints. Upward length, spiral, side length, a soft fold, neck, and feet. Stillness under length matters more than speed. Later bands may add one stick, then a wave, only if the place allows. Name each movement by what the body does, in the person's language. Do not copy names from closed course manuals.",
    "monk": "The calm school is the monk: quiet strength. Time in a low stance, a slow bow for the spine and hips, isometrics, a walking step with attention, counted breath. Strikes, jumps, and fighting forms do not belong to this school. It keeps the tendons, the stance, and the silence. Image of a courtyard, stone, or a bell. No sermon.",
}

ACTIVE = {
    "witcher": "The active school is the Witcher. A precise body, no sword in the hand. A stance with soft knees, steps forward and sideways, weight shifts, lunges, a braced trunk, the slow shadow of a block and a slip. At night this is a still ambush. By day it is intervals and pursuit. In the evening it is the same school at a controlled pace, not a yoga flow. The teacher sounds like a Witcher instructor: short, exact, steel and a trail. One image is enough: steel, a trail, quiet after the effort.",
    "blade": "The active school is Blade. Short urban fighting with no partner. A low stance, a step off the line, a change of level, the trunk, the shadow of elbows and knees in the air, round endurance, grip. Strike the air, not a person. No neck bridges. In the early band use less speed and a pause at the end of the move. One image: asphalt, night, one exhale per action. No vampire plot.",
    "thor": "The active school is Thor. Strength of the stance. Squat, hip hinge, a carry, a press overhead, grip, a step under load. A backpack or the body weight is enough. A swing is a hip fold with a long spine, not a yank from the lower back. In the early band the tempo is slow, with a pause at the bottom. One image: weight, weather in the legs, the ground taking the foot. No hammer required.",
    "spider": "The active school is Spider-Man. A line through the body. Shoulder blades, a hang or a pull at an easy angle, a crawl, the wrists, balance, a quiet landing from a step, never from a height. No jumps off furniture. In the early band a wall hang and a high-support pull come before a bar. Warm the wrists before a hang. One image: a thread from palm to foot, and a quiet touch. No comic plot.",
    "widow": "The active school is Black Widow. Precision, quiet feet, long balance, a braced core, short combinations in the air. No partner and no weapons. In the early band keep the landing soft. One image: a red line on a dark floor, one exact step. No spy plot.",
    "elektra": "The active school is Elektra. Close range, sharp changes of level, short rounds, forearms and ribs, a firm grip, steps that cut an angle. Strike the air. No neck bridges. In the early band pause at the end of each move. One image: a clean edge and a short breath. No revenge story.",
    "valkyrie": "The active school is Valkyrie. Strength and carriage. Squat, hinge, a load in the hands, a press overhead, a strong step. A backpack or the body weight is enough. Swing from the hips with a long spine. In the early band the tempo is slow, with a pause. One image: a gate, weather, feet driven into the ground. No battle saga.",
    "nikita": "The active school is Nikita. She is a woman and a secret agent, not a soldier. No uniform, no medal, no barracks, no drill. Quiet entry, grip on a real edge, a crawl, a pull past an obstacle, silent feet, a short carry, and one calm breath before the move. No firearms, no partner, no drops from a height. In the early band the pull is from a high support and the landing is a step. One image: a dark corridor, a hand on the frame, the exit already chosen. No mission plot.",
}

LANGUAGE_NAMES = {
    "ru": "Russian",
    "en": "English",
    "uk": "Ukrainian",
    "be": "Belarusian",
    "kk": "Kazakh",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "it": "Italian",
    "pt": "Portuguese",
    "pl": "Polish",
    "tr": "Turkish",
    "zh": "Chinese",
    "ja": "Japanese",
    "ko": "Korean",
    "ar": "Arabic",
    "he": "Hebrew",
    "hi": "Hindi",
    "nl": "Dutch",
    "sv": "Swedish",
    "no": "Norwegian",
    "da": "Danish",
    "fi": "Finnish",
    "cs": "Czech",
    "sk": "Slovak",
    "ro": "Romanian",
    "hu": "Hungarian",
    "bg": "Bulgarian",
    "el": "Greek",
    "vi": "Vietnamese",
    "th": "Thai",
    "id": "Indonesian",
    "ms": "Malay",
}


def language_name(code: str) -> str:
    return LANGUAGE_NAMES.get(code, f"the language with IETF code {code}")


def build_system(band: str, day: str, calm: str, active: str, calm_is_short: bool) -> str:
    parts = [CORE, BANDS[band], DAYS[day], CALM[calm], ACTIVE[active]]
    if calm_is_short:
        parts.append(SHORT_CALM)
    return "\n\n".join(parts)


def build_user(
    language: str,
    hours: int,
    duration: int,
    minutes: tuple[int, int, int, int],
    time_of_day: str,
    avoid_titles: list[str],
    avoid_exercises: list[str],
    note: str,
) -> str:
    warmup, calm, active, cooldown = minutes
    titles = ", ".join(avoid_titles) if avoid_titles else "none"
    exercises = ", ".join(avoid_exercises) if avoid_exercises else "none"
    remark = note.strip() if note else "none"
    return (
        f"language: {language_name(language)}\n"
        f"hours_completed: {hours}\n"
        f"duration_min: {duration}\n"
        f"minutes: warmup {warmup}, calm {calm}, active {active}, cooldown {cooldown}\n"
        f"time_of_day: {time_of_day}\n"
        f"avoid_titles: {titles}\n"
        f"avoid_exercises: {exercises}\n"
        f"limits: none\n"
        f"note: {remark}\n"
    )
