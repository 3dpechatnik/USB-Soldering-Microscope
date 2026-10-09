import unittest

from bot.strings import EN, RU
from bot.logic import (
    apply_short_calm,
    apply_voice,
    band_for,
    block_reason,
    deck,
    dose_line,
    duration_for,
    format_clock,
    minutes_for,
    progress_text,
    scale_split,
    tick_delay,
    validate_session,
)
from bot.prompts import build_system, build_user, build_voice


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
        evening = minutes_for(0, "evening")
        self.assertGreater(evening[2], evening[1])

    def test_scale_and_night_short(self):
        scaled = scale_split((8, 10, 17, 10), 90)
        self.assertEqual(sum(scaled), 90)
        self.assertEqual(apply_short_calm(scaled, True), scaled)

    def test_paywall(self):
        self.assertIsNone(block_reason(True, 21, "timer"))
        self.assertEqual(block_reason(False, 21, "new_workout"), "limit")
        self.assertIsNone(block_reason(False, 20, "new_workout"))
        self.assertIsNone(block_reason(False, 0, "change_schools"))
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
        self.assertIn("teacher of the active school", system)
        self.assertIn("one plain line", system)
        self.assertIn("a few more reps", system)
        self.assertIn("Do not ask a question", system)
        voice = build_voice("evening", "yoga", "witcher")
        self.assertIn("four short lines", voice)
        self.assertIn("Evening, not night", voice)
        self.assertIn("Witcher instructor", voice)
        self.assertIn("Тихая засада", system)
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
        voiced = validate_session(
            {
                "opening": "Стой. Вечер твой.",
                "emoji": "🌆",
                "title": "Сталь на тропе",
                "level": "Новичок",
                "goal": "Держи колено.",
                "parts": [
                    {
                        "name": "Вход",
                        "role": "warmup",
                        "exercises": [
                            {
                                "name": "Шаг",
                                "seconds": 10,
                                "reps": 3,
                                "text": "Стопы на ширине таза.\nКолени мягкие.\nВыдох длиннее.\nПятки можно приподнять.",
                            }
                        ],
                    }
                ],
                "closing": "Опусти плечи.",
                "ask": "Как было?",
            }
        )
        spoken = apply_voice(session, voiced)
        self.assertEqual(spoken["title"], "Сталь на тропе")
        self.assertEqual(spoken["parts"][0]["exercises"][0]["seconds"], 60)
        self.assertEqual(spoken["parts"][0]["exercises"][0]["name"], "Шаг")
        self.assertIn("Стопы", spoken["parts"][0]["exercises"][0]["text"])
        self.assertEqual(spoken["ask"], "")

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

    def test_reps_sit_beside_the_time(self):
        self.assertEqual(dose_line(60, 0, "{n} раз"), "1:00")
        self.assertEqual(dose_line(60, 8, "{n} раз"), "8 раз  ·  1:00")
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
                        "exercises": [
                            {"name": "Наклон", "seconds": 40, "reps": 8, "text": "Медленно."}
                        ],
                    }
                ],
                "closing": "Хватит.",
                "ask": "Как прошло?",
            }
        )
        self.assertEqual(session["parts"][0]["exercises"][0]["reps"], 8)

    def test_recent_effort_reaches_the_master(self):
        text = build_user(
            "ru",
            2,
            45,
            (8, 13, 14, 10),
            "morning",
            [],
            [],
            "",
            [
                {"score": 1, "title": "Тихий шаг", "note": ""},
                {"score": 3, "title": "Ровная работа", "note": "колено"},
            ],
        )
        self.assertIn("recent_effort: 1 (Тихий шаг); 3 (Ровная работа) — колено", text)

    def test_clock_counts_real_time(self):
        shown = format_clock(90, 120, step=1, running=True)
        self.assertIn("1:30", shown)
        self.assertIn("🕑", shown)
        self.assertIn("▰", shown)
        self.assertEqual(tick_delay(90), 5)
        self.assertEqual(tick_delay(30), 1)


class ProtectContentTest(unittest.IsolatedAsyncioTestCase):
    async def test_outgoing_message_cannot_be_forwarded(self):
        from bot.telegram_app import Telegram

        seen = {}

        class FakeClient:
            async def post(self, url, json):
                seen["json"] = json

                class Response:
                    def json(self):
                        return {"ok": True, "result": {"message_id": 1}}

                return Response()

        telegram = Telegram("token", FakeClient(), FakeClient())
        await telegram.call("sendMessage", chat_id=1, text="занятие")
        self.assertTrue(seen["json"]["protect_content"])
        seen.clear()
        await telegram.call("sendChatAction", chat_id=1, action="typing")
        self.assertNotIn("protect_content", seen["json"])


class AddressRotationTest(unittest.IsolatedAsyncioTestCase):
    async def test_failed_address_is_skipped(self):
        from bot.telegram_app import TelegramIPv4

        backend = TelegramIPv4()
        backend._ips = ["203.0.113.1", "203.0.113.2"]
        backend._refreshed = 10**9
        calls = []

        async def connect(host, port, timeout=None, local_address=None, socket_options=None):
            calls.append(host)
            if host == "203.0.113.1":
                raise TimeoutError("down")
            return "stream"

        backend._inner.connect_tcp = connect
        stream = await backend.connect_tcp("api.telegram.org", 443, timeout=5)
        self.assertEqual(stream, "stream")
        self.assertEqual(calls, ["203.0.113.1", "203.0.113.2"])


if __name__ == "__main__":
    unittest.main()
