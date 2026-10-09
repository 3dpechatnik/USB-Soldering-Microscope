from __future__ import annotations

import asyncio
import html
import json
import logging
from dataclasses import dataclass, field

from bot.db import DB
from bot.deepseek import DeepSeek
from bot.i18n import I18n, norm_lang
from bot.logic import (
    ACTIVE_IDS,
    CALM_IDS,
    TIME_IDS,
    band_for,
    block_reason,
    clock,
    deck,
    duration_for,
    exercise_names,
    minutes_for,
    progress_text,
    short_calm,
)
from bot.prompts import build_system, build_user
from bot.strings import EN, RU

LABEL_ACTIONS = {
    "btn_male": "gender:male",
    "btn_female": "gender:female",
    "btn_yoga": "calm:yoga",
    "btn_qigong": "calm:qigong",
    "btn_beloyar": "calm:beloyar",
    "btn_monk": "calm:monk",
    "btn_witcher": "active:witcher",
    "btn_blade": "active:blade",
    "btn_thor": "active:thor",
    "btn_spider": "active:spider",
    "btn_widow": "active:widow",
    "btn_elektra": "active:elektra",
    "btn_valkyrie": "active:valkyrie",
    "btn_nikita": "active:nikita",
    "btn_morning": "time:morning",
    "btn_day": "time:day",
    "btn_evening": "time:evening",
    "btn_night": "time:night",
    "btn_outdoor": "time:outdoor",
    "btn_train": "go:train",
    "btn_menu": "go:menu",
    "btn_next": "nav:next",
    "btn_timer": "nav:timer",
    "btn_time": "go:time",
    "btn_schools": "go:schools",
    "btn_exp": "go:exp",
    "btn_change": "go:change",
    "btn_r1": "rate:1",
    "btn_r2": "rate:2",
    "btn_r3": "rate:3",
    "btn_r4": "rate:4",
    "btn_r5": "rate:5",
}

log = logging.getLogger(__name__)


def esc(value) -> str:
    return html.escape(str(value or ""), quote=False)


@dataclass
class Screen:
    text: str
    actions: list[tuple[str, str]] = field(default_factory=list)
    admin_text: str | None = None
    timer_after: int | None = None
    timer_done_text: str | None = None


class Service:
    def __init__(self, db: DB, ai: DeepSeek, admin_id: int):
        self.db = db
        self.ai = ai
        self.i18n = I18n(db, ai)
        self.admin_id = admin_id
        self._locks: dict[int, asyncio.Lock] = {}
        self._on_wait = None

    def _lock(self, tg_id: int) -> asyncio.Lock:
        if tg_id not in self._locks:
            self._locks[tg_id] = asyncio.Lock()
        return self._locks[tg_id]

    def is_admin(self, tg_id: int) -> bool:
        return tg_id == self.admin_id

    async def on_message(
        self,
        tg_id: int,
        username: str,
        full_name: str,
        language: str,
        text: str,
        on_wait=None,
    ) -> Screen:
        async with self._lock(tg_id):
            language = norm_lang(language)
            self._on_wait = on_wait
            user = self.db.upsert_user(tg_id, username or "", full_name or "", language)
            if text.startswith("/start"):
                return await self._route(user)
            if text.startswith("/menu") and self.db.active_choice(tg_id):
                return await self._open_menu(user)
            action = self._match(user, text)
            if action is None:
                if user["expect_note"] and not text.startswith("/"):
                    return await self._save_note(user, text)
                screen = await self._route(user)
                hint = await self.i18n.t(user["language"], "use_buttons")
                screen.text = f"{esc(hint)}\n\n{screen.text}"
                return screen
            return await self._action(user, action)

    def _match(self, user: dict, text: str) -> str | None:
        try:
            buttons = json.loads(user["keyboard"] or "[]")
        except json.JSONDecodeError:
            return None
        for button in buttons:
            if button.get("label") == text:
                return button.get("id")
        for labels in (RU, EN):
            for key, label in labels.items():
                if label == text and key in LABEL_ACTIONS:
                    return LABEL_ACTIONS[key]
        return None

    async def _t(self, user: dict, key: str, **values) -> str:
        return await self.i18n.t(user["language"], key, **values)

    async def _heading(self, user: dict, title_key: str) -> str:
        sep = await self._t(user, "sep")
        title = await self._t(user, title_key)
        return f"{sep}\n{esc(title)}\n{sep}"

    async def _pack(self, user: dict, text: str, actions: list[tuple[str, str]], **extra) -> Screen:
        self.db.set_keyboard(user["tg_id"], actions)
        return Screen(text=text, actions=actions, **extra)

    async def _route(self, user: dict) -> Screen:
        choice = self.db.active_choice(user["tg_id"])
        session = self.db.active_session(user["tg_id"])
        if not user["gender"]:
            return await self._gender(user)
        if choice is None and not user["pending_calm"]:
            return await self._calm(user)
        if choice is None:
            return await self._active(user)
        if session and user["screen"] in {"card", "rating"}:
            return await self._card(user, session)
        if user["screen"] == "time":
            return await self._time(user)
        if user["screen"] == "menu":
            return await self._menu(user)
        if user["screen"] == "schools":
            return await self._schools(user)
        if user["screen"] == "exp":
            return await self._exp(user)
        if user["screen"] == "done":
            return await self._home(user)
        return await self._home(user)

    async def _gender(self, user: dict) -> Screen:
        self.db.update_user(user["tg_id"], screen="gender")
        actions = [
            ("gender:male", await self._t(user, "btn_male")),
            ("gender:female", await self._t(user, "btn_female")),
        ]
        return await self._pack(user, await self._heading(user, "who"), actions)

    async def _calm(self, user: dict) -> Screen:
        self.db.update_user(user["tg_id"], screen="calm")
        user = self.db.user(user["tg_id"])
        actions = [(f"calm:{item}", await self._t(user, f"btn_{item}")) for item in CALM_IDS]
        actions.append(("nav:cancel_select", await self._t(user, "btn_back")))
        return await self._pack(user, await self._heading(user, "calm"), actions)

    async def _active(self, user: dict) -> Screen:
        self.db.update_user(user["tg_id"], screen="active")
        user = self.db.user(user["tg_id"])
        actions = [
            (f"active:{item}", await self._t(user, f"btn_{item}"))
            for item in ACTIVE_IDS[user["gender"]]
        ]
        actions.append(("nav:back_calm", await self._t(user, "btn_back")))
        return await self._pack(user, await self._heading(user, "active"), actions)

    async def _time(self, user: dict) -> Screen:
        self.db.update_user(user["tg_id"], screen="time")
        user = self.db.user(user["tg_id"])
        actions = [(f"time:{item}", await self._t(user, f"btn_{item}")) for item in TIME_IDS]
        actions.append(("nav:time_back", await self._t(user, "btn_back")))
        return await self._pack(user, await self._heading(user, "where"), actions)

    async def _home(self, user: dict, lead: str = "") -> Screen:
        self.db.update_user(user["tg_id"], screen="home", expect_note=user.get("expect_note") or 0)
        user = self.db.user(user["tg_id"])
        body = await self._heading(user, "train_title")
        if self.db.active_session(user["tg_id"]):
            body += "\n\n" + esc(await self._t(user, "continue_line"))
        if lead:
            body = lead + "\n\n" + body
        actions = [
            ("go:train", await self._t(user, "btn_train")),
            ("go:menu", await self._t(user, "btn_menu")),
        ]
        return await self._pack(user, body, actions)

    async def _open_menu(self, user: dict) -> Screen:
        back_to = "card" if self.db.active_session(user["tg_id"]) else "home"
        self.db.update_user(user["tg_id"], screen="menu", pending_calm=user["pending_calm"])
        # reuse reselect column? no. store return in awaiting? I'll encode return in keyboard only
        # and a dedicated field isn't there. Use screen menu and check session on back.
        return await self._menu(self.db.user(user["tg_id"]), prefer=back_to)

    async def _menu(self, user: dict, prefer: str | None = None) -> Screen:
        self.db.update_user(user["tg_id"], screen="menu")
        user = self.db.user(user["tg_id"])
        actions = [
            ("go:time", await self._t(user, "btn_time")),
            ("go:schools", await self._t(user, "btn_schools")),
            ("go:exp", await self._t(user, "btn_exp")),
            ("nav:menu_back", await self._t(user, "btn_back")),
        ]
        return await self._pack(user, await self._heading(user, "menu_title"), actions)

    async def _schools(self, user: dict) -> Screen:
        self.db.update_user(user["tg_id"], screen="schools")
        user = self.db.user(user["tg_id"])
        choice = self.db.active_choice(user["tg_id"])
        calm = await self._t(user, f"btn_{choice['calm']}")
        active = await self._t(user, f"btn_{choice['active']}")
        count = await self._t(user, "count_line", hours=choice["hours"])
        text = (
            f"{await self._heading(user, 'schools_title')}\n\n"
            f"{esc(calm)}\n{esc(active)}\n\n{esc(count)}"
        )
        actions = [
            ("go:change", await self._t(user, "btn_change")),
            ("nav:to_menu", await self._t(user, "btn_back")),
        ]
        return await self._pack(user, text, actions)

    async def _exp(self, user: dict) -> Screen:
        self.db.update_user(user["tg_id"], screen="exp")
        user = self.db.user(user["tg_id"])
        choice = self.db.active_choice(user["tg_id"])
        template = await self._t(user, "progress")
        length = duration_for(choice["hours"])
        lines = [
            await self._heading(user, "exp_title"),
            "",
            esc(progress_text(template, choice["hours"])),
            esc(await self._t(user, "length_line", minutes=length)),
        ]
        if self.is_admin(user["tg_id"]):
            archived = self.db.archived_choices(user["tg_id"])
            if archived:
                lines.append("")
                lines.append(esc(await self._t(user, "archive_title")))
                for old in archived:
                    calm = await self._t(user, f"btn_{old['calm']}")
                    active = await self._t(user, f"btn_{old['active']}")
                    lines.append(f"{esc(calm)}  ·  {esc(active)}  ·  {old['hours']}")
        actions = [("nav:to_menu", await self._t(user, "btn_back"))]
        return await self._pack(user, "\n".join(lines), actions)

    async def _pay(self, user: dict, reason: str, actions: list[tuple[str, str]]) -> Screen:
        choice = self.db.active_choice(user["tg_id"])
        pair = ""
        hours = 0
        if choice:
            calm = await self._t(user, f"btn_{choice['calm']}")
            active = await self._t(user, f"btn_{choice['active']}")
            pair = f"{calm} / {active}"
            hours = choice["hours"]
        reasons = {
            "limit": "закончились 21 бесплатных тренировки",
            "change": "хочет сменить школы",
            "timer": "нажал таймер в бесплатном режиме",
        }
        who = self._who(user)
        admin = f"Запрос на подписку\n{who}\nпричина: {reasons[reason]}\nшколы: {pair}\nсчёт: {hours}"
        text = f"{await self._heading(user, 'pay_title')}\n\n{esc(await self._t(user, 'pay_body'))}"
        return await self._pack(user, text, actions, admin_text=admin)

    async def _card(self, user: dict, session: dict | None = None) -> Screen:
        session = session or self.db.active_session(user["tg_id"])
        payload = json.loads(session["payload"])
        cards = deck(payload)
        index = max(0, min(int(session["index_pos"]), len(cards) - 1))
        card = cards[index]
        if card["type"] == "closing":
            self.db.update_user(user["tg_id"], screen="rating")
        else:
            self.db.update_user(user["tg_id"], screen="card")
        user = self.db.user(user["tg_id"])
        text = self._render(payload, card)
        actions = await self._workout_actions(user, card["type"], index)
        return await self._pack(user, text, actions)

    def _render(self, payload: dict, card: dict) -> str:
        sep = "-__________________________/"
        if card["type"] == "title":
            return (
                f"{sep}\n<b>{esc(payload['emoji'])}  {esc(payload['title'])}</b>\n{sep}\n\n"
                f"{esc(payload['opening'])}\n\n{esc(payload['level'])}\n\n{esc(payload['goal'])}"
            )
        if card["type"] == "closing":
            return (
                f"{sep}\n<b>{esc(payload['emoji'])}  {esc(payload['title'])}</b>\n{sep}\n\n"
                f"{esc(payload['closing'])}\n\n{esc(payload['ask'])}"
            )
        return (
            f"{sep}\n<i>{esc(card['part'])}</i>\n{sep}\n\n"
            f"<b>{esc(card['name'])}</b>\n{esc(clock(card['seconds']))}\n\n{esc(card['text'])}"
        )

    async def _workout_actions(self, user: dict, kind: str, index: int) -> list[tuple[str, str]]:
        actions = []
        if index > 0:
            actions.append(("nav:prev", await self._t(user, "btn_back")))
        if kind != "closing":
            actions.append(("nav:next", await self._t(user, "btn_next")))
        else:
            for score in range(1, 6):
                actions.append((f"rate:{score}", await self._t(user, f"btn_r{score}")))
        actions.append(("nav:timer", await self._t(user, "btn_timer")))
        actions.append(("go:menu", await self._t(user, "btn_menu")))
        return actions

    async def _action(self, user: dict, action: str) -> Screen:
        if user["expect_note"]:
            self.db.update_user(user["tg_id"], expect_note=0)
            user = self.db.user(user["tg_id"])
        if action.startswith("gender:"):
            gender = action.split(":", 1)[1]
            if gender not in ACTIVE_IDS:
                return await self._gender(user)
            self.db.update_user(user["tg_id"], gender=gender, pending_calm=None, screen="calm")
            return await self._calm(self.db.user(user["tg_id"]))

        if action.startswith("calm:"):
            calm = action.split(":", 1)[1]
            if calm not in CALM_IDS:
                return await self._calm(user)
            self.db.update_user(user["tg_id"], pending_calm=calm, screen="active")
            return await self._active(self.db.user(user["tg_id"]))

        if action == "nav:back_calm":
            self.db.update_user(user["tg_id"], pending_calm=None)
            return await self._calm(self.db.user(user["tg_id"]))

        if action == "nav:cancel_select":
            if user["reselect"] and self.db.active_choice(user["tg_id"]):
                self.db.update_user(user["tg_id"], reselect=0, pending_calm=None)
                return await self._schools(self.db.user(user["tg_id"]))
            return await self._gender(user)

        if action.startswith("active:"):
            active = action.split(":", 1)[1]
            allowed = ACTIVE_IDS.get(user["gender"], ())
            if active not in allowed or not user["pending_calm"]:
                return await self._active(user)
            if user["reselect"]:
                self.db.abandon_active(user["tg_id"])
                self.db.archive_active(user["tg_id"])
            self.db.create_choice(user["tg_id"], user["pending_calm"], active)
            self.db.update_user(
                user["tg_id"],
                pending_calm=None,
                reselect=0,
                awaiting_first_time=1,
                screen="time",
            )
            return await self._time(self.db.user(user["tg_id"]))

        if action.startswith("time:"):
            time_of_day = action.split(":", 1)[1]
            if time_of_day not in TIME_IDS:
                return await self._time(user)
            autostart = bool(user["awaiting_first_time"])
            self.db.update_user(
                user["tg_id"],
                time_of_day=time_of_day,
                awaiting_first_time=0,
            )
            user = self.db.user(user["tg_id"])
            if autostart:
                return await self._begin(user)
            if self.db.active_session(user["tg_id"]):
                return await self._card(user)
            return await self._menu(user)

        if action == "nav:time_back":
            if user["awaiting_first_time"]:
                self.db.update_user(user["tg_id"], awaiting_first_time=0)
                return await self._home(self.db.user(user["tg_id"]))
            if self.db.active_session(user["tg_id"]):
                return await self._card(self.db.user(user["tg_id"]))
            return await self._menu(user)

        if action == "go:train":
            return await self._begin(user)

        if action == "go:menu":
            return await self._open_menu(user)

        if action == "go:time":
            return await self._time(user)

        if action == "go:schools":
            return await self._schools(user)

        if action == "go:exp":
            return await self._exp(user)

        if action == "go:change":
            reason = block_reason(self.is_admin(user["tg_id"]), 0, "change_schools")
            if reason:
                actions = [("nav:to_menu", await self._t(user, "btn_back"))]
                return await self._pay(user, reason, actions)
            self.db.update_user(user["tg_id"], reselect=1, pending_calm=None, screen="calm")
            return await self._calm(self.db.user(user["tg_id"]))

        if action == "nav:to_menu":
            return await self._menu(user)

        if action == "nav:menu_back":
            if self.db.active_session(user["tg_id"]):
                return await self._card(user)
            return await self._home(user)

        if action in {"nav:next", "nav:prev"}:
            return await self._move(user, action)

        if action == "nav:timer":
            return await self._timer(user)

        if action.startswith("rate:"):
            score = int(action.split(":", 1)[1])
            return await self._rate(user, score)

        return await self._route(user)

    async def _begin(self, user: dict) -> Screen:
        choice = self.db.active_choice(user["tg_id"])
        if choice is None:
            return await self._calm(user)
        if not user["time_of_day"]:
            self.db.update_user(user["tg_id"], awaiting_first_time=1)
            return await self._time(self.db.user(user["tg_id"]))
        existing = self.db.active_session(user["tg_id"])
        if existing:
            return await self._card(user, existing)
        reason = block_reason(self.is_admin(user["tg_id"]), choice["hours"], "new_workout")
        if reason:
            actions = [
                ("go:train", await self._t(user, "btn_train")),
                ("go:menu", await self._t(user, "btn_menu")),
            ]
            return await self._pay(user, reason, actions)
        return await self._generate(user, choice)

    async def _generate(self, user: dict, choice: dict) -> Screen:
        time_of_day = user["time_of_day"]
        hours = int(choice["hours"])
        duration = duration_for(hours)
        minutes = minutes_for(hours, time_of_day)
        finished = self.db.last_finished(choice["id"])
        titles = [item["title"] for item in finished if item["title"]]
        names: list[str] = []
        for item in finished:
            names.extend(exercise_names(item["payload"]))
        note = ""
        if choice["last_score"]:
            note = f"effort {choice['last_score']}"
            if choice["last_note"]:
                note += f"; {choice['last_note']}"
        system = build_system(
            band_for(hours),
            time_of_day,
            choice["calm"],
            choice["active"],
            short_calm(hours, time_of_day),
        )
        if self._on_wait:
            await self._on_wait(await self._t(user, "gathering"))
        request = build_user(
            user["language"],
            hours,
            duration,
            minutes,
            time_of_day,
            titles,
            names[:40],
            note,
        )
        try:
            payload = await self.ai.session(system, request)
        except Exception:
            log.exception("workout generation failed")
            fail = esc(await self._t(user, "fail"))
            return await self._home(user, lead=fail)
        session = self.db.create_session(
            user["tg_id"],
            choice["id"],
            time_of_day,
            duration,
            payload["title"],
            payload,
        )
        return await self._card(self.db.user(user["tg_id"]), session)

    async def _move(self, user: dict, action: str) -> Screen:
        session = self.db.active_session(user["tg_id"])
        if session is None:
            return await self._home(user)
        payload = json.loads(session["payload"])
        cards = deck(payload)
        index = int(session["index_pos"])
        if action == "nav:next":
            index = min(index + 1, len(cards) - 1)
        else:
            index = max(index - 1, 0)
        self.db.set_index(session["id"], index)
        session["index_pos"] = index
        return await self._card(user, session)

    async def _timer(self, user: dict) -> Screen:
        session = self.db.active_session(user["tg_id"])
        current = await self._card(user, session) if session else await self._home(user)
        reason = block_reason(self.is_admin(user["tg_id"]), 0, "timer")
        if reason:
            paid = await self._pay(user, reason, current.actions)
            return paid
        if session is None:
            text = esc(await self._t(user, "timer_wait"))
            return await self._pack(user, text, current.actions)
        payload = json.loads(session["payload"])
        cards = deck(payload)
        card = cards[int(session["index_pos"])]
        if card["type"] != "exercise":
            text = esc(await self._t(user, "timer_wait"))
            return await self._pack(user, text, current.actions)
        running = esc(await self._t(user, "timer_running", clock=clock(card["seconds"])))
        done = await self._t(user, "timer_done")
        return await self._pack(
            user,
            running,
            current.actions,
            timer_after=card["seconds"],
            timer_done_text=done,
        )

    async def _rate(self, user: dict, score: int) -> Screen:
        if score < 1 or score > 5:
            return await self._route(user)
        session = self.db.active_session(user["tg_id"])
        if session is None or user["screen"] != "rating":
            return await self._route(user)
        payload = json.loads(session["payload"])
        cards = deck(payload)
        if cards[int(session["index_pos"])]["type"] != "closing":
            return await self._card(user, session)
        self.db.finish_session(session["id"])
        hours = self.db.add_hour(session["choice_id"])
        self.db.set_feedback(session["choice_id"], score)
        self.db.update_user(user["tg_id"], screen="done", expect_note=1)
        user = self.db.user(user["tg_id"])
        choice = self.db.active_choice(user["tg_id"])
        calm = await self._t(user, f"btn_{choice['calm']}")
        active = await self._t(user, f"btn_{choice['active']}")
        admin = (
            f"Отзыв\n{self._who(user)}\nоценка: {score}\n"
            f"школы: {calm} / {active}\nзанятие: {payload['title']}\nсчёт: {hours}"
        )
        template = await self._t(user, "progress")
        text = (
            f"{await self._heading(user, 'hour_saved')}\n\n"
            f"{esc(progress_text(template, hours))}\n\n"
            f"{esc(await self._t(user, 'note_invite'))}"
        )
        actions = [
            ("go:train", await self._t(user, "btn_train")),
            ("go:menu", await self._t(user, "btn_menu")),
        ]
        return await self._pack(user, text, actions, admin_text=admin)

    async def _save_note(self, user: dict, text: str) -> Screen:
        choice = self.db.active_choice(user["tg_id"])
        clean = text.strip()[:1000]
        if choice and clean:
            self.db.set_feedback(choice["id"], choice["last_score"] or 0, clean)
        self.db.update_user(user["tg_id"], expect_note=0)
        user = self.db.user(user["tg_id"])
        admin = f"Уточнение к отзыву\n{self._who(user)}\n{clean}"
        accepted = esc(await self._t(user, "accepted"))
        home = await self._home(user)
        home.text = f"{accepted}\n\n{home.text}"
        home.admin_text = admin
        return home

    def _who(self, user: dict) -> str:
        handle = f"@{user['username']}" if user["username"] else "без имени"
        return f"id {user['tg_id']} {handle} {user['full_name']}".strip()
