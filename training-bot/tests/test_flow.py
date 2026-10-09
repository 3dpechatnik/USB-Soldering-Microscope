import tempfile
import unittest

from bot.db import DB
from bot.logic import validate_session
from bot.service import Service
from bot.strings import RU


SESSION = validate_session(
    {
        "opening": "Утро собирает тело.",
        "emoji": "🌅",
        "title": "Первый свет",
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
                "exercises": [{"name": "Стойка", "seconds": 45, "text": "Мягкие колени."}],
            },
            {
                "name": "Тишина",
                "role": "cooldown",
                "exercises": [{"name": "Лёжа", "seconds": 60, "text": "Дыши."}],
            },
        ],
        "closing": "Хватит на сегодня.",
        "ask": "Насколько тяжело?",
    }
)


class FakeAI:
    def __init__(self):
        self.calls = 0

    async def session(self, system, user):
        self.calls += 1
        return SESSION

    async def translate(self, language_name, mapping):
        return mapping


class FlowTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = DB(f"{self.tmp.name}/bot.db")
        self.ai = FakeAI()
        self.service = Service(self.db, self.ai, admin_id=906994986)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def send(self, text, tg_id=5):
        return await self.service.on_message(tg_id, "player", "Player", "ru", text)

    async def test_free_path_rates_and_then_paywall_blocks_timer(self):
        screen = await self.send("/start")
        self.assertIn("gender:male", [item[0] for item in screen.actions])
        self.assertIn("Школа открыта", screen.text)
        self.assertIn("Если готов", screen.text)
        self.assertNotIn("Долгая подготовка", screen.text)
        self.assertTrue(screen.inline)
        screen = await self.send(RU["btn_male"])
        self.assertIn("calm:yoga", [item[0] for item in screen.actions])
        screen = await self.send(RU["btn_yoga"])
        screen = await self.send(RU["btn_witcher"])
        self.assertIn("time:morning", [item[0] for item in screen.actions])
        screen = await self.send(RU["btn_morning"])
        self.assertEqual(self.ai.calls, 1)
        self.assertIn("Первый свет", screen.text)
        self.assertIn("Утро собирает тело.", screen.text)
        self.assertNotIn("Разминка", screen.text)
        self.assertIn("Шея", screen.text)
        self.assertNotIn("Гора", screen.text)
        self.assertIsNone(screen.timer_after)
        labels = [item[0] for item in screen.actions]
        self.assertIn("nav:next", labels)
        self.assertNotIn("nav:prev", labels)
        screen = await self.send(RU["btn_timer"])
        self.assertIn("Подписка", screen.text)
        self.assertIn("таймер", screen.admin_text)

        screen = await self.send(RU["btn_next"])
        self.assertIn("Гора", screen.text)
        self.assertIn("Стойка", screen.text)
        self.assertNotIn("Шея", screen.text)
        self.assertNotIn("Основная", screen.text)
        screen = await self.send(RU["btn_next"])
        self.assertNotIn("Концовка", screen.text)
        self.assertIn("Лёжа", screen.text)
        self.assertIn("Хватит на сегодня", screen.text)
        self.assertNotIn("Насколько тяжело", screen.text)
        self.assertNotIn("Очень легко", screen.text)
        self.assertNotIn("rate:1", [item[0] for item in screen.actions])
        self.assertIn("1/10000", screen.text)
        self.assertIn("0.01%", screen.text)
        self.assertIsNone(screen.admin_text)
        choice = self.db.active_choice(5)
        self.assertEqual(choice["hours"], 1)
        screen = await self.send(RU["btn_back"])
        screen = await self.send(RU["btn_next"])
        self.assertEqual(self.db.active_choice(5)["hours"], 1)

        screen = await self.send("колено тянет")
        self.assertNotIn("отзыв", (screen.admin_text or "").lower())
        self.assertFalse(self.db.active_choice(5)["last_note"])
        self.assertEqual(self.db.recent_efforts(5), [])

        calls = self.ai.calls
        screen = await self.send(RU["btn_train"])
        self.assertIn("time:evening", [item[0] for item in screen.actions])
        self.assertIn("Выбери час", screen.text)
        self.assertEqual(self.ai.calls, calls)

        self.db.conn.execute("UPDATE choices SET hours=21 WHERE tg_id=5")
        self.db.conn.commit()
        screen = await self.send(RU["btn_train"])
        self.assertIn("Подписка", screen.text)
        self.assertIn("21", screen.admin_text)

    async def test_admin_changes_school_and_keeps_archive(self):
        await self.send("/start", 906994986)
        await self.send(RU["btn_female"], 906994986)
        await self.send(RU["btn_monk"], 906994986)
        self.assertNotIn("🎖", RU["btn_nikita"])
        self.assertIn("🕵️", RU["btn_nikita"])
        await self.send(RU["btn_nikita"], 906994986)
        screen = await self.send(RU["btn_night"], 906994986)
        self.assertEqual(screen.timer_after, 480)
        self.assertIn("Шея", screen.text)
        self.assertNotIn("Разминка", screen.text)
        self.assertEqual(screen.timer_label, "")
        self.assertEqual(self.db.active_choice(906994986)["active"], "nikita")
        screen = await self.send(RU["btn_timer"], 906994986)
        self.assertTrue(screen.timer_only)
        self.assertEqual(screen.timer_after, 480)
        screen = await self.send(RU["btn_menu"], 906994986)
        self.assertIn("go:schools", [item[0] for item in screen.actions])
        screen = await self.send(RU["btn_schools"], 906994986)
        screen = await self.send(RU["btn_change"], 906994986)
        await self.send(RU["btn_yoga"], 906994986)
        await self.send(RU["btn_valkyrie"], 906994986)
        archived = self.db.archived_choices(906994986)
        self.assertEqual(archived[0]["active"], "nikita")
        self.assertEqual(self.db.active_choice(906994986)["active"], "valkyrie")
        self.assertEqual(self.db.active_choice(906994986)["hours"], 0)

    async def test_school_change_keeps_total_and_menu_commands(self):
        await self.send("/start")
        await self.send(RU["btn_male"])
        await self.send(RU["btn_yoga"])
        await self.send(RU["btn_witcher"])
        await self.send(RU["btn_morning"])
        self.db.conn.execute("UPDATE choices SET hours=21 WHERE tg_id=5")
        self.db.conn.commit()
        await self.send(RU["btn_menu"])
        await self.send(RU["btn_schools"])
        screen = await self.send(RU["btn_change"])
        self.assertNotIn("Подписка", screen.text)
        await self.send(RU["btn_qigong"])
        await self.send(RU["btn_blade"])
        self.assertEqual(self.db.active_choice(5)["hours"], 0)
        self.assertEqual(self.db.total_hours(5), 21)
        screen = await self.send(RU["btn_exp"])
        self.assertIn("21/10000", screen.text)
        screen = await self.send(RU["btn_train"])
        self.assertIn("Подписка", screen.text)

        screen = await self.send("/review")
        self.assertIn("отзыв", screen.text.lower())
        screen = await self.send("стало тише")
        self.assertIn("Отзыв из меню", screen.admin_text)
        self.assertIn("стало тише", screen.admin_text)

        screen = await self.send("/subscribe")
        self.assertIn("Подписка", screen.text)
        self.assertIn("открыл подписку", screen.admin_text)

        screen = await self.send("/delete")
        self.assertIn("delete:yes", [item[0] for item in screen.actions])
        screen = await self.send(RU["btn_delete_no"])
        self.assertIn("Тренировка", screen.text)
        screen = await self.send("/delete")
        screen = await self.send(RU["btn_delete_yes"])
        self.assertIn("Данные удалены", screen.text)
        self.assertIsNone(self.db.active_choice(5))
        self.assertEqual(self.db.total_hours(5), 0)

    async def test_same_hour_does_not_build_a_second_workout(self):
        await self.send("/start", 906994986)
        await self.send(RU["btn_male"], 906994986)
        await self.send(RU["btn_yoga"], 906994986)
        await self.send(RU["btn_witcher"], 906994986)
        await self.send(RU["btn_morning"], 906994986)
        self.assertEqual(self.ai.calls, 1)
        self.db.update_user(906994986, awaiting_first_time=1)
        screen = await self.send(RU["btn_morning"], 906994986)
        self.assertEqual(self.ai.calls, 1)
        self.assertIn("Первый свет", screen.text)

    async def test_repeat_active_school_opens_the_hour(self):
        await self.send("/start", 906994986)
        await self.send(RU["btn_male"], 906994986)
        await self.send(RU["btn_yoga"], 906994986)
        screen = await self.send(RU["btn_witcher"], 906994986)
        self.assertIn("time:evening", [item[0] for item in screen.actions])
        self.assertEqual(self.ai.calls, 0)
        screen = await self.send(RU["btn_witcher"], 906994986)
        self.assertIn("Выбери час", screen.text)
        self.assertIn("time:evening", [item[0] for item in screen.actions])
        self.assertEqual(self.ai.calls, 0)
        self.assertEqual(self.db.active_choice(906994986)["active"], "witcher")
        screen = await self.send(RU["btn_thor"], 906994986)
        self.assertIn("time:morning", [item[0] for item in screen.actions])
        self.assertEqual(self.db.active_choice(906994986)["calm"], "yoga")
        self.assertEqual(self.db.active_choice(906994986)["active"], "thor")
        self.assertEqual(self.db.archived_choices(906994986)[0]["active"], "witcher")


if __name__ == "__main__":
    unittest.main()
