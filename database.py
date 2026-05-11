import sqlite3
import logging
import threading
from datetime import date

log = logging.getLogger(__name__)

DB_PATH   = "bot_data.db"
_db_lock  = threading.Lock()


def get_db():
    return sqlite3.connect(DB_PATH, check_same_thread=False)


def init_db():
    with _db_lock, get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id     INTEGER PRIMARY KEY,
                user_name   TEXT,
                is_banned   INTEGER DEFAULT 0,
                is_premium  INTEGER DEFAULT 0,
                daily_count INTEGER DEFAULT 0,
                daily_limit INTEGER DEFAULT 50,
                last_reset  TEXT    DEFAULT '',
                topic_id    INTEGER,
                custom_info TEXT    DEFAULT '',
                policy      TEXT    DEFAULT ''
            );
        """)
        conn.commit()
    log.info("Database ready")


def upsert_user(user_id: int, user_name: str):
    today = str(date.today())
    with _db_lock, get_db() as conn:
        conn.execute("""
            INSERT INTO users (user_id, user_name, last_reset)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET user_name=excluded.user_name
        """, (user_id, user_name, today))
        conn.commit()


def get_user(user_id: int) -> dict | None:
    with _db_lock, get_db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE user_id=?", (user_id,)
        ).fetchone()
    if not row:
        return None
    cols = ["user_id","user_name","is_banned","is_premium",
            "daily_count","daily_limit","last_reset","topic_id","custom_info","policy"]
    return dict(zip(cols, row))


def reset_daily_if_needed(user_id: int):
    today = str(date.today())
    with _db_lock, get_db() as conn:
        conn.execute("""
            UPDATE users
            SET daily_count=0, last_reset=?
            WHERE user_id=? AND last_reset != ?
        """, (today, user_id, today))
        conn.commit()


def increment_daily_count(user_id: int):
    with _db_lock, get_db() as conn:
        conn.execute(
            "UPDATE users SET daily_count=daily_count+1 WHERE user_id=?",
            (user_id,)
        )
        conn.commit()


def set_topic_id(user_id: int, topic_id: int):
    with _db_lock, get_db() as conn:
        conn.execute(
            "UPDATE users SET topic_id=? WHERE user_id=?",
            (topic_id, user_id)
        )
        conn.commit()


def get_user_by_topic(topic_id: int) -> int | None:
    with _db_lock, get_db() as conn:
        row = conn.execute(
            "SELECT user_id FROM users WHERE topic_id=?", (topic_id,)
        ).fetchone()
    return row[0] if row else None


_ALLOWED_FIELDS = {"is_banned","is_premium","daily_limit","custom_info","policy"}

def set_user_field(user_id: int, field: str, value):
    if field not in _ALLOWED_FIELDS:
        log.warning(f"Blocked update to disallowed field: {field}")
        return
    with _db_lock, get_db() as conn:
        conn.execute(f"UPDATE users SET {field}=? WHERE user_id=?", (value, user_id))
        conn.commit()


def get_all_user_ids() -> list[int]:
    with _db_lock, get_db() as conn:
        rows = conn.execute("SELECT user_id FROM users").fetchall()
    return [r[0] for r in rows]
