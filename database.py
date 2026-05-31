"""
database.py — SQLite persistence for Friday AI Bot
"""

import sqlite3
import logging
import threading
from datetime import date

log = logging.getLogger(__name__)

DB_PATH  = "bot_data.db"
_db_lock = threading.Lock()

# Roles (stored as TEXT in DB)
ROLE_OWNER   = "owner"
ROLE_PREMIUM = "premium"
ROLE_USER    = "user"
ROLE_BANNED  = "banned"

# Default daily limits per role
LIMITS = {
    ROLE_OWNER:   999_999,
    ROLE_PREMIUM: 500,
    ROLE_USER:    50,
    ROLE_BANNED:  0,
}


def _conn() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH, check_same_thread=False)


def init_db():
    with _db_lock, _conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id     INTEGER PRIMARY KEY,
                user_name   TEXT    DEFAULT '',
                role        TEXT    DEFAULT 'user',
                daily_count INTEGER DEFAULT 0,
                daily_limit INTEGER DEFAULT 50,
                last_reset  TEXT    DEFAULT '',
                topic_id    INTEGER,
                custom_info TEXT    DEFAULT '',
                policy      TEXT    DEFAULT '',
                custom_tone TEXT    DEFAULT ''
            );
        """)
        # Migrate older DBs — add missing columns
        existing = {
            row[1]
            for row in conn.execute("PRAGMA table_info(users)").fetchall()
        }
        if "is_banned" in existing and "role" not in existing:
            conn.execute("ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'user'")
            conn.execute("""
                UPDATE users SET role = CASE
                    WHEN is_banned  = 1 THEN 'banned'
                    WHEN is_premium = 1 THEN 'premium'
                    ELSE 'user'
                END
            """)
        elif "role" not in existing:
            conn.execute("ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'user'")
        if "custom_tone" not in existing:
            conn.execute("ALTER TABLE users ADD COLUMN custom_tone TEXT DEFAULT ''")
        conn.commit()
    log.info("Database ready")


# ── CRUD ──────────────────────────────────────────────────────────────────────

def upsert_user(user_id: int, user_name: str):
    today = str(date.today())
    with _db_lock, _conn() as conn:
        conn.execute("""
            INSERT INTO users (user_id, user_name, last_reset)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET user_name = excluded.user_name
        """, (user_id, user_name, today))
        conn.commit()


def get_user(user_id: int) -> dict | None:
    with _db_lock, _conn() as conn:
        row = conn.execute(
            "SELECT user_id,user_name,role,daily_count,daily_limit,"
            "last_reset,topic_id,custom_info,policy,custom_tone FROM users WHERE user_id=?",
            (user_id,)
        ).fetchone()
    if not row:
        return None
    return dict(zip(
        ["user_id","user_name","role","daily_count","daily_limit",
         "last_reset","topic_id","custom_info","policy","custom_tone"],
        row
    ))


def reset_daily_if_needed(user_id: int):
    today = str(date.today())
    with _db_lock, _conn() as conn:
        conn.execute("""
            UPDATE users SET daily_count = 0, last_reset = ?
            WHERE user_id = ? AND last_reset != ?
        """, (today, user_id, today))
        conn.commit()


def increment_daily_count(user_id: int):
    with _db_lock, _conn() as conn:
        conn.execute(
            "UPDATE users SET daily_count = daily_count + 1 WHERE user_id = ?",
            (user_id,)
        )
        conn.commit()


def set_topic_id(user_id: int, topic_id: int):
    with _db_lock, _conn() as conn:
        conn.execute(
            "UPDATE users SET topic_id = ? WHERE user_id = ?",
            (topic_id, user_id)
        )
        conn.commit()


def get_user_by_topic(topic_id: int) -> int | None:
    with _db_lock, _conn() as conn:
        row = conn.execute(
            "SELECT user_id FROM users WHERE topic_id = ?", (topic_id,)
        ).fetchone()
    return row[0] if row else None


_ALLOWED_FIELDS = {"role", "daily_limit", "custom_info", "policy", "custom_tone",
                   # legacy compat (old admin commands)
                   "is_banned", "is_premium"}

def set_user_field(user_id: int, field: str, value):
    # Translate legacy boolean fields to role column
    if field == "is_banned":
        _set_role(user_id, ROLE_BANNED if value else ROLE_USER)
        return
    if field == "is_premium":
        _set_role(user_id, ROLE_PREMIUM if value else ROLE_USER)
        return
    if field not in _ALLOWED_FIELDS:
        log.warning(f"Blocked update to disallowed field: {field}")
        return
    with _db_lock, _conn() as conn:
        conn.execute(f"UPDATE users SET {field} = ? WHERE user_id = ?",
                     (value, user_id))
        conn.commit()


def _set_role(user_id: int, role: str):
    limit = LIMITS.get(role, LIMITS[ROLE_USER])
    with _db_lock, _conn() as conn:
        conn.execute(
            "UPDATE users SET role = ?, daily_limit = ? WHERE user_id = ?",
            (role, limit, user_id)
        )
        conn.commit()


def get_all_user_ids() -> list[int]:
    with _db_lock, _conn() as conn:
        rows = conn.execute("SELECT user_id FROM users").fetchall()
    return [r[0] for r in rows]


# ── Role helpers (all checks use real user_id from DB, never from text) ────────

def get_user_role(user_id: int) -> str:
    """Return stored role string or ROLE_USER if not found."""
    user = get_user(user_id)
    return user["role"] if user else ROLE_USER


def is_owner(user_id: int, owner_id: int) -> bool:
    """True only if user_id matches the hardcoded owner_id."""
    return user_id == owner_id


def is_premium(user_id: int) -> bool:
    return get_user_role(user_id) in (ROLE_PREMIUM, ROLE_OWNER)


def is_banned(user_id: int) -> bool:
    return get_user_role(user_id) == ROLE_BANNED


def at_daily_limit(user_id: int) -> bool:
    """True if user has reached or exceeded their daily message limit."""
    user = get_user(user_id)
    if not user:
        return False
    return user["daily_count"] >= user["daily_limit"]


# ── User Memories ──────────────────────────────────────────────────────────────

def init_memories_table():
    """Create user_memories table if it doesn't exist."""
    with _db_lock, _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_memories (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                memory_text TEXT    NOT NULL,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
    log.info("Memories table ready")


def save_memory(user_id: int, memory_text: str):
    """Save a new memory entry for the given user."""
    with _db_lock, _conn() as conn:
        conn.execute(
            "INSERT INTO user_memories (user_id, memory_text) VALUES (?, ?)",
            (user_id, memory_text.strip())
        )
        conn.commit()
    log.info(f"Memory saved for user {user_id}")


def get_memories(user_id: int) -> list[dict]:
    """Return all memory entries for the given user, oldest first."""
    with _db_lock, _conn() as conn:
        rows = conn.execute(
            "SELECT id, memory_text, created_at FROM user_memories "
            "WHERE user_id = ? ORDER BY created_at ASC",
            (user_id,)
        ).fetchall()
    return [
        {"id": r[0], "memory_text": r[1], "created_at": r[2]}
        for r in rows
    ]


def delete_memory(user_id: int, memory_text: str) -> bool:
    """Delete the first matching memory entry for the given user.
    Returns True if a row was deleted, False if not found."""
    with _db_lock, _conn() as conn:
        cur = conn.execute(
            "DELETE FROM user_memories WHERE id = ("
            "  SELECT id FROM user_memories "
            "  WHERE user_id = ? AND memory_text = ? "
            "  ORDER BY created_at ASC LIMIT 1"
            ")",
            (user_id, memory_text.strip())
        )
        conn.commit()
    deleted = cur.rowcount > 0
    if deleted:
        log.info(f"Memory deleted for user {user_id}")
    return deleted
