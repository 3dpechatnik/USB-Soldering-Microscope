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
        screen = await self.send(RU["btn_male"])
        self.assertIn("calm:yoga", [item[0] for item in screen.actions])
        screen = await self.send(RU["btn_yoga"])
        screen = await self.send(RU["btn_witcher"])
        self.assertIn("time:morning", [item[0] for item in screen.actions])
        screen = await self.send(RU["btn_morning"])
        self.assertEqual(self.ai.calls, 1)
        self.assertIn("Первый свет", screen.text)
        labels = [item[0] for item in screen.actions]
        self.assertIn("nav:next", labels)
        self.assertNotIn("nav:prev", labels)

        for _ in range(5):
            screen = await self.send(RU["btn_next"])
        self.assertIn("rate:4", [item[0] for item in screen.actions])
        screen = await self.send(RU["btn_r4"])
        self.assertIn("1/10000", screen.text)
        self.assertIn("0.01%", screen.text)
        self.assertIn("оценка: 4", screen.admin_text)
        choice = self.db.active_choice(5)
        self.assertEqual(choice["hours"], 1)

        screen = await self.send("колено тянет")
        self.assertIn("Уточнение", screen.admin_text)
        self.assertEqual(self.db.active_choice(5)["last_note"], "колено тянет")

        self.db.conn.execute("UPDATE choices SET hours=21 WHERE tg_id=5")
        self.db.conn.commit()
        screen = await self.send(RU["btn_train"])
        self.assertIn("Подписка", screen.text)
        self.assertIn("21", screen.admin_text)

    async def test_admin_changes_school_and_keeps_archive(self):
        await self.send("/start", 906994986)
        await self.send(RU["btn_female"], 906994986)
        await self.send(RU["btn_monk"], 906994986)
        await self.send(RU["btn_nikita"], 906994986)
        await self.send(RU["btn_night"], 906994986)
        self.assertEqual(self.db.active_choice(906994986)["active"], "nikita")
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


if __name__ == "__main__":
    unittest.main()
