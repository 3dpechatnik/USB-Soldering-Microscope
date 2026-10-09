import unittest

from bot.strings import EN, RU
from bot.logic import (
    apply_short_calm,
    band_for,
    block_reason,
    deck,
    duration_for,
    format_clock,
    minutes_for,
    progress_text,
    scale_split,
    tick_delay,
    validate_session,
)
from bot.prompts import build_system


class LogicTest(unittest.TestCase):
    def test_duration_steps(self):
        self.assertEqual(duration_for(0), 45)
        self.assertEqual(duration_for(29), 45)
        self.assertEqual(duration_for(30), 60)
        self.assertEqual(duration_for(99), 60)
        self.assertEqual(duration_for(100), 90)
        self.assertEqual(duration_for(10000), 90)

    def test_bands(self):
        self.assertEqual(band_for(0), "early")
        self.assertEqual(band_for(30), "student")
        self.assertEqual(band_for(100), "craft")
        self.assertEqual(band_for(250), "late")
        self.assertEqual(band_for(300), "late")

    def test_minutes_sum_and_short_calm(self):
        for hours in (0, 30, 100, 250):
            for time_of_day in ("morning", "day", "evening", "night", "outdoor"):
                parts = minutes_for(hours, time_of_day)
                self.assertEqual(sum(parts), duration_for(hours))
        long = minutes_for(300, "day")
        self.assertEqual(sum(long), 90)
        self.assertLessEqual(long[1], 5)
        night = minutes_for(300, "night")
        self.assertGreater(night[1], 5)

    def test_scale_and_night_short(self):
        scaled = scale_split((8, 10, 17, 10), 90)
        self.assertEqual(sum(scaled), 90)
        self.assertEqual(apply_short_calm(scaled, True), scaled)

    def test_paywall(self):
        self.assertIsNone(block_reason(True, 21, "timer"))
        self.assertEqual(block_reason(False, 21, "new_workout"), "limit")
        self.assertIsNone(block_reason(False, 20, "new_workout"))
        self.assertEqual(block_reason(False, 0, "change_schools"), "change")
        self.assertEqual(block_reason(False, 0, "timer"), "timer")

    def test_progress_line(self):
        self.assertEqual(
            progress_text("Прогресс: [{n}/10000] — {pct}", 1),
            "Прогресс: [1/10000] — 0.01%",
        )

    def test_prompt_is_the_night_pair_only(self):
        system = build_system("early", "night", "yoga", "witcher", False)
        self.assertIn("Join them into a single healthy session", system)
        self.assertIn("whole session is calm", system)
        self.assertIn("calm school is yoga", system)
        self.assertIn("active school is the Witcher", system)
        self.assertNotIn("This is the working session", system)
        self.assertNotIn("Black Widow", system)

    def test_languages_have_the_same_keys(self):
        self.assertEqual(set(RU), set(EN))

    def test_session_validation(self):
        session = validate_session(
            {
                "opening": "Ночь тихая.",
                "emoji": "🌙",
                "title": "Тихая вода",
                "level": "Новичок",
                "goal": "Собрать стопу.",
                "parts": [
                    {
                        "name": "Вход",
                        "role": "warmup",
                        "exercises": [{"name": "Шея", "seconds": 60, "text": "Медленно."}],
                    }
                ],
                "closing": "Хватит.",
                "ask": "Насколько тяжело?",
            }
        )
        self.assertEqual(session["parts"][0]["exercises"][0]["seconds"], 60)

    def test_deck_groups_three_blocks(self):
        session = validate_session(
            {
                "opening": "Утро.",
                "emoji": "🌅",
                "title": "Свет",
                "level": "Новичок",
                "goal": "Проснуться.",
                "parts": [
                    {
                        "name": "Вход",
                        "role": "warmup",
                        "exercises": [{"name": "Шея", "seconds": 60, "text": "Медленно."}],
                    },
                    {
                        "name": "Йога",
                        "role": "calm",
                        "exercises": [{"name": "Гора", "seconds": 60, "text": "Стой."}],
                    },
                    {
                        "name": "Ведьмак",
                        "role": "active",
                        "exercises": [{"name": "Стойка", "seconds": 45, "text": "Мягко."}],
                    },
                    {
                        "name": "Тишина",
                        "role": "cooldown",
                        "exercises": [{"name": "Лёжа", "seconds": 60, "text": "Дыши."}],
                    },
                ],
                "closing": "Хватит.",
                "ask": "Насколько тяжело?",
            }
        )
        cards = deck(session)
        self.assertEqual([card["block"] for card in cards], ["warmup", "main", "ending"])
        self.assertEqual([item["name"] for item in cards[1]["exercises"]], ["Гора", "Стойка"])
        self.assertEqual(cards[1]["seconds"], 105)

    def test_clock_counts_real_time(self):
        shown = format_clock(90, 120, step=1, running=True)
        self.assertIn("1:30", shown)
        self.assertIn("🕑", shown)
        self.assertIn("▰", shown)
        self.assertEqual(tick_delay(90), 5)
        self.assertEqual(tick_delay(30), 1)


if __name__ == "__main__":
    unittest.main()
