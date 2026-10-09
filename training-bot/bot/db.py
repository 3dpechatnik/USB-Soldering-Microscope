from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DB:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._init()

    def _init(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                tg_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                language TEXT NOT NULL,
                gender TEXT,
                time_of_day TEXT,
                screen TEXT NOT NULL DEFAULT 'gender',
                pending_calm TEXT,
                reselect INTEGER NOT NULL DEFAULT 0,
                awaiting_first_time INTEGER NOT NULL DEFAULT 0,
                expect_note INTEGER NOT NULL DEFAULT 0,
                keyboard TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS choices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id INTEGER NOT NULL,
                calm TEXT NOT NULL,
                active TEXT NOT NULL,
                hours INTEGER NOT NULL DEFAULT 0,
                archived INTEGER NOT NULL DEFAULT 0,
                last_score INTEGER,
                last_note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id INTEGER NOT NULL,
                choice_id INTEGER NOT NULL,
                time_of_day TEXT NOT NULL,
                duration_min INTEGER NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                payload TEXT NOT NULL,
                index_pos INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS translations (
                language TEXT NOT NULL,
                key TEXT NOT NULL,
                text TEXT NOT NULL,
                PRIMARY KEY (language, key)
            );
            """
        )
        self.conn.commit()

    def upsert_user(self, tg_id: int, username: str, full_name: str, language: str) -> dict:
        row = self.user(tg_id)
        if row is None:
            self.conn.execute(
                """
                INSERT INTO users (tg_id, username, full_name, language, screen, created_at)
                VALUES (?, ?, ?, ?, 'gender', ?)
                """,
                (tg_id, username, full_name, language, now()),
            )
        else:
            self.conn.execute(
                """
                UPDATE users
                SET username=?, full_name=?, language=?
                WHERE tg_id=?
                """,
                (username, full_name, language, tg_id),
            )
        self.conn.commit()
        return self.user(tg_id)

    def user(self, tg_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,)).fetchone()
        return dict(row) if row else None

    def update_user(self, tg_id: int, **fields) -> dict:
        if not fields:
            return self.user(tg_id)
        keys = list(fields)
        sql = "UPDATE users SET " + ", ".join(f"{key}=?" for key in keys) + " WHERE tg_id=?"
        self.conn.execute(sql, [fields[key] for key in keys] + [tg_id])
        self.conn.commit()
        return self.user(tg_id)

    def set_keyboard(self, tg_id: int, actions: list[tuple[str, str]]) -> None:
        payload = json.dumps(
            [{"id": action, "label": label} for action, label in actions],
            ensure_ascii=False,
        )
        self.update_user(tg_id, keyboard=payload)

    def active_choice(self, tg_id: int) -> dict | None:
        row = self.conn.execute(
            """
            SELECT * FROM choices
            WHERE tg_id=? AND archived=0
            ORDER BY id DESC LIMIT 1
            """,
            (tg_id,),
        ).fetchone()
        return dict(row) if row else None

    def archived_choices(self, tg_id: int) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT * FROM choices
            WHERE tg_id=? AND archived=1
            ORDER BY id DESC
            """,
            (tg_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def create_choice(self, tg_id: int, calm: str, active: str) -> dict:
        cur = self.conn.execute(
            """
            INSERT INTO choices (tg_id, calm, active, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (tg_id, calm, active, now()),
        )
        self.conn.commit()
        row = self.conn.execute("SELECT * FROM choices WHERE id=?", (cur.lastrowid,)).fetchone()
        return dict(row)

    def archive_active(self, tg_id: int) -> None:
        self.conn.execute(
            "UPDATE choices SET archived=1 WHERE tg_id=? AND archived=0",
            (tg_id,),
        )
        self.conn.commit()

    def add_hour(self, choice_id: int) -> int:
        row = self.conn.execute("SELECT hours FROM choices WHERE id=?", (choice_id,)).fetchone()
        hours = min(10000, int(row["hours"]) + 1)
        self.conn.execute("UPDATE choices SET hours=? WHERE id=?", (hours, choice_id))
        self.conn.commit()
        return hours

    def set_feedback(self, choice_id: int, score: int, note: str | None = None) -> None:
        if note is None:
            self.conn.execute(
                "UPDATE choices SET last_score=? WHERE id=?",
                (score, choice_id),
            )
        else:
            self.conn.execute(
                "UPDATE choices SET last_note=? WHERE id=?",
                (note, choice_id),
            )
        self.conn.commit()

    def active_session(self, tg_id: int) -> dict | None:
        row = self.conn.execute(
            """
            SELECT * FROM sessions
            WHERE tg_id=? AND status='active'
            ORDER BY id DESC LIMIT 1
            """,
            (tg_id,),
        ).fetchone()
        return dict(row) if row else None

    def create_session(
        self,
        tg_id: int,
        choice_id: int,
        time_of_day: str,
        duration_min: int,
        title: str,
        payload: dict,
    ) -> dict:
        self.conn.execute(
            "UPDATE sessions SET status='abandoned' WHERE tg_id=? AND status='active'",
            (tg_id,),
        )
        cur = self.conn.execute(
            """
            INSERT INTO sessions
            (tg_id, choice_id, time_of_day, duration_min, title, payload, index_pos, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 0, 'active', ?)
            """,
            (
                tg_id,
                choice_id,
                time_of_day,
                duration_min,
                title,
                json.dumps(payload, ensure_ascii=False),
                now(),
            ),
        )
        self.conn.commit()
        row = self.conn.execute("SELECT * FROM sessions WHERE id=?", (cur.lastrowid,)).fetchone()
        return dict(row)

    def set_index(self, session_id: int, index_pos: int) -> None:
        self.conn.execute(
            "UPDATE sessions SET index_pos=? WHERE id=?",
            (index_pos, session_id),
        )
        self.conn.commit()

    def abandon_active(self, tg_id: int) -> None:
        self.conn.execute(
            "UPDATE sessions SET status='abandoned' WHERE tg_id=? AND status='active'",
            (tg_id,),
        )
        self.conn.commit()

    def finish_session(self, session_id: int) -> None:
        self.conn.execute(
            "UPDATE sessions SET status='finished' WHERE id=?",
            (session_id,),
        )
        self.conn.commit()

    def last_finished(self, choice_id: int, limit: int = 2) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT title, payload FROM sessions
            WHERE choice_id=? AND status='finished'
            ORDER BY id DESC LIMIT ?
            """,
            (choice_id, limit),
        ).fetchall()
        found = []
        for row in rows:
            found.append({"title": row["title"], "payload": json.loads(row["payload"])})
        return found

    def translation(self, language: str, key: str) -> str | None:
        row = self.conn.execute(
            "SELECT text FROM translations WHERE language=? AND key=?",
            (language, key),
        ).fetchone()
        return row["text"] if row else None

    def save_translation(self, language: str, key: str, text: str) -> None:
        self.conn.execute(
            """
            INSERT INTO translations (language, key, text)
            VALUES (?, ?, ?)
            ON CONFLICT(language, key) DO UPDATE SET text=excluded.text
            """,
            (language, key, text),
        )
        self.conn.commit()
