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
    deck,
    dose_line,
    format_clock,
    duration_for,
    exercise_names,
    minutes_for,
    share_time,
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
    "btn_delete_yes": "delete:yes",
    "btn_delete_no": "delete:no",
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
    timer_body: str | None = None
    timer_label: str = ""
    timer_only: bool = False
    inline: bool = False


class Service:
    def __init__(self, db: DB, ai: DeepSeek, admin_id: int):
        self.db = db
        self.ai = ai
        self.i18n = I18n(db, ai)
        self.admin_id = admin_id
        self._locks: dict[int, asyncio.Lock] = {}
        self._ai_slots = asyncio.Semaphore(8)
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
                if self.db.active_choice(tg_id) is None:
                    self.db.update_user(
                        tg_id,
                        gender=None,
                        pending_calm=None,
                        reselect=0,
                        screen="gender",
                    )
                    user = self.db.user(tg_id)
                return await self._route(user)
            if text.startswith("/menu") and self.db.active_choice(tg_id):
                return await self._open_menu(user)
            command = text.strip().split()[0].split("@", 1)[0].lower()
            if command == "/review":
                return await self._ask_review(user)
            if command == "/subscribe":
                return await self._subscribe(user)
            if command == "/delete":
                return await self._ask_delete(user)
            action = self._match(user, text)
            if action is None and text.startswith(
                ("gender:", "calm:", "active:", "time:", "go:", "nav:", "rate:")
            ):
                action = text
            if action is None:
                if user.get("expect_review") and not text.startswith("/"):
                    return await self._save_review(user, text)
                if user["expect_note"]:
                    self.db.update_user(user["tg_id"], expect_note=0)
                    user = self.db.user(user["tg_id"])
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
        return esc(await self._t(user, title_key))

    async def _pack(
        self,
        user: dict,
        text: str,
        actions: list[tuple[str, str]],
        inline: bool = False,
        **extra,
    ) -> Screen:
        self.db.set_keyboard(user["tg_id"], actions)
        return Screen(text=text, actions=actions, inline=inline, **extra)

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
        if user["screen"] == "delete":
            return await self._ask_delete(user)
        if user["screen"] == "review":
            return await self._ask_review(user)
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
        text = esc(await self._t(user, "welcome"))
        return await self._pack(user, text, actions, inline=True)

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
        template = await self._t(user, "progress")
        hours = self.db.total_hours(user["tg_id"])
        length = duration_for(hours)
        lines = [
            await self._heading(user, "exp_title"),
            "",
            esc(progress_text(template, hours)),
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
        hours = self.db.total_hours(user["tg_id"])
        if choice:
            calm = await self._t(user, f"btn_{choice['calm']}")
            active = await self._t(user, f"btn_{choice['active']}")
            pair = f"{calm} / {active}"
        reasons = {
            "limit": "закончились 21 бесплатных тренировки",
            "subscribe": "открыл подписку в меню",
            "timer": "нажал таймер в бесплатном режиме",
        }
        who = self._who(user)
        admin = f"Запрос на подписку\n{who}\nпричина: {reasons[reason]}\nшколы: {pair}\nсчёт: {hours}"
        text = f"{await self._heading(user, 'pay_title')}\n\n{esc(await self._t(user, 'pay_body'))}"
        return await self._pack(user, text, actions, admin_text=admin)

    def _position(self, session: dict) -> tuple[dict, list[dict], int]:
        payload = json.loads(session["payload"])
        cards = deck(payload)
        index = int(session["index_pos"])
        if index < 0 or index > len(cards) - 1:
            index = 0
            self.db.set_index(session["id"], 0)
            session["index_pos"] = 0
        return payload, cards, index

    async def _card(self, user: dict, session: dict | None = None) -> Screen:
        session = session or self.db.active_session(user["tg_id"])
        payload, cards, index = self._position(session)
        card = cards[index]
        last = index == len(cards) - 1
        if last and session.get("status") == "active":
            self.db.finish_session(session["id"])
            self.db.add_hour(session["choice_id"])
            session["status"] = "finished"
        self.db.update_user(user["tg_id"], screen="card")
        user = self.db.user(user["tg_id"])
        reps_line = await self._t(user, "reps_line")
        body = self._render(payload, card, index == 0, last, reps_line)
        if last:
            hours = self.db.total_hours(user["tg_id"])
            template = await self._t(user, "progress")
            body = f"{body}\n\n{esc(progress_text(template, hours))}"
        actions = await self._workout_actions(user, index, last)
        clock_line = format_clock(card["seconds"], card["seconds"], running=False)
        text = f"{body}\n\n{clock_line}"
        extra = {}
        if self._timer_allowed(user):
            extra = {
                "timer_after": card["seconds"],
                "timer_done_text": await self._t(user, "timer_done"),
                "timer_body": body,
            }
        return await self._pack(user, text, actions, **extra)

    def _timer_allowed(self, user: dict) -> bool:
        return block_reason(self.is_admin(user["tg_id"]), 0, "timer") is None

    def _render(self, payload: dict, card: dict, first: bool, last: bool, reps_line: str) -> str:
        lines: list[str] = []
        if first:
            lines.extend(
                [
                    f"<b>{esc(payload['emoji'])}  {esc(payload['title'])}</b>",
                    "",
                    esc(payload["opening"]),
                    "",
                    esc(payload["level"]),
                    "",
                    esc(payload["goal"]),
                    "",
                ]
            )
        for exercise in card["exercises"]:
            lines.append(f"<b>{esc(exercise['name'])}</b>")
            lines.append(
                esc(dose_line(exercise["seconds"], int(exercise.get("reps") or 0), reps_line))
            )
            lines.append(esc(exercise["text"]))
            lines.append("")
        if last:
            lines.append(esc(payload["closing"]))
        return "\n".join(lines).strip()

    async def _workout_actions(self, user: dict, index: int, last: bool) -> list[tuple[str, str]]:
        actions = []
        if index > 0:
            actions.append(("nav:prev", await self._t(user, "btn_back")))
        if not last:
            actions.append(("nav:next", await self._t(user, "btn_next")))
        actions.append(("nav:timer", await self._t(user, "btn_timer")))
        actions.append(("go:menu", await self._t(user, "btn_menu")))
        return actions

    async def _ask_review(self, user: dict) -> Screen:
        self.db.update_user(user["tg_id"], expect_review=1, expect_note=0, screen="review")
        user = self.db.user(user["tg_id"])
        actions = [("nav:review_cancel", await self._t(user, "btn_back"))]
        return await self._pack(user, esc(await self._t(user, "review_invite")), actions)

    async def _save_review(self, user: dict, text: str) -> Screen:
        clean = text.strip()[:1000]
        self.db.update_user(user["tg_id"], expect_review=0, screen="home")
        user = self.db.user(user["tg_id"])
        admin = f"Отзыв из меню\n{self._who(user)}\n{clean}"
        screen = await self._route(user)
        screen.admin_text = admin
        sent = esc(await self._t(user, "review_sent"))
        screen.text = f"{sent}\n\n{screen.text}"
        return screen

    async def _subscribe(self, user: dict) -> Screen:
        actions = [("nav:review_cancel", await self._t(user, "btn_back"))]
        return await self._pay(user, "subscribe", actions)

    async def _ask_delete(self, user: dict) -> Screen:
        self.db.update_user(user["tg_id"], screen="delete", expect_review=0, expect_note=0)
        user = self.db.user(user["tg_id"])
        actions = [
            ("delete:yes", await self._t(user, "btn_delete_yes")),
            ("delete:no", await self._t(user, "btn_delete_no")),
        ]
        return await self._pack(user, esc(await self._t(user, "delete_ask")), actions)

    async def _wipe(self, user: dict) -> Screen:
        tg_id = user["tg_id"]
        username = user.get("username") or ""
        full_name = user.get("full_name") or ""
        language = user.get("language") or "ru"
        self.db.delete_user(tg_id)
        fresh = self.db.upsert_user(tg_id, username, full_name, language)
        screen = await self._gender(fresh)
        screen.text = f"{esc(await self._t(fresh, 'deleted'))}\n\n{screen.text}"
        return screen

    async def _action(self, user: dict, action: str) -> Screen:
        if user["expect_note"] or user.get("expect_review"):
            self.db.update_user(user["tg_id"], expect_note=0, expect_review=0)
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
            self.db.update_user(user["tg_id"], reselect=1, pending_calm=None, screen="calm")
            return await self._calm(self.db.user(user["tg_id"]))

        if action == "nav:review_cancel":
            self.db.update_user(user["tg_id"], expect_review=0)
            user = self.db.user(user["tg_id"])
            if self.db.active_session(user["tg_id"]):
                return await self._card(user)
            if self.db.active_choice(user["tg_id"]):
                return await self._home(user)
            return await self._route(user)

        if action == "delete:no":
            self.db.update_user(user["tg_id"], screen="home")
            user = self.db.user(user["tg_id"])
            if self.db.active_session(user["tg_id"]):
                return await self._card(user)
            if self.db.active_choice(user["tg_id"]):
                return await self._home(user)
            return await self._route(user)

        if action == "delete:yes":
            return await self._wipe(user)

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
            session = self._viewing(user)
            if session:
                return await self._card(user, session)
            return await self._route(user)

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
        reason = block_reason(
            self.is_admin(user["tg_id"]), self.db.total_hours(user["tg_id"]), "new_workout"
        )
        if reason:
            actions = [
                ("go:train", await self._t(user, "btn_train")),
                ("go:menu", await self._t(user, "btn_menu")),
            ]
            return await self._pay(user, reason, actions)
        return await self._generate(user, choice)

    async def _generate(self, user: dict, choice: dict) -> Screen:
        time_of_day = user["time_of_day"]
        hours = self.db.total_hours(user["tg_id"])
        duration = duration_for(hours)
        minutes = minutes_for(hours, time_of_day)
        finished = self.db.last_finished(choice["id"])
        titles = [item["title"] for item in finished if item["title"]]
        names: list[str] = []
        for item in finished:
            names.extend(exercise_names(item["payload"]))
        note = (choice["last_note"] or "").strip()
        system = build_system(
            band_for(hours),
            time_of_day,
            choice["calm"],
            choice["active"],
            short_calm(hours, time_of_day),
        )
        if self._on_wait:
            try:
                await self._on_wait(await self._t(user, "gathering"))
            except Exception:
                log.warning("gathering note failed")
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
            async with self._ai_slots:
                payload = await self.ai.session(system, request)
        except Exception:
            log.exception("workout generation failed")
            fail = esc(await self._t(user, "fail"))
            return await self._home(user, lead=fail)
        payload = share_time(payload, minutes)
        session = self.db.create_session(
            user["tg_id"],
            choice["id"],
            time_of_day,
            duration,
            payload["title"],
            payload,
        )
        return await self._card(self.db.user(user["tg_id"]), session)

    def _viewing(self, user: dict) -> dict | None:
        session = self.db.active_session(user["tg_id"])
        if session:
            return session
        if user["screen"] in {"card", "rating"}:
            return self.db.latest_session(user["tg_id"])
        return None

    async def _move(self, user: dict, action: str) -> Screen:
        session = self._viewing(user)
        if session is None:
            return await self._home(user)
        _payload, cards, index = self._position(session)
        if action == "nav:next":
            index = min(index + 1, len(cards) - 1)
        else:
            index = max(index - 1, 0)
        self.db.set_index(session["id"], index)
        session["index_pos"] = index
        return await self._card(user, session)

    async def _timer(self, user: dict) -> Screen:
        session = self._viewing(user)
        current = await self._card(user, session) if session else await self._home(user)
        reason = block_reason(self.is_admin(user["tg_id"]), 0, "timer")
        if reason:
            paid = await self._pay(user, reason, current.actions)
            return paid
        if session is None:
            text = esc(await self._t(user, "timer_wait"))
            return await self._pack(user, text, current.actions)
        _payload, cards, index = self._position(session)
        card = cards[index]
        if card["type"] != "block":
            text = esc(await self._t(user, "timer_wait"))
            return await self._pack(user, text, current.actions)
        done = await self._t(user, "timer_done")
        text = format_clock(card["seconds"], card["seconds"], running=True)
        return await self._pack(
            user,
            text,
            current.actions,
            timer_after=card["seconds"],
            timer_done_text=done,
            timer_only=True,
        )

    def _who(self, user: dict) -> str:
        handle = f"@{user['username']}" if user["username"] else "без имени"
        return f"id {user['tg_id']} {handle} {user['full_name']}".strip()
