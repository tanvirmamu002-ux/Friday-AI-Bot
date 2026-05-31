"""
Friday AI — Advanced Telegram Bot
Author  : Burhan (@hm_burhan)
Engine  : Google Gemini (gemini-flash-lite-latest)
"""

import os
import sys
import html
import time
import logging
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict, deque

import telebot

import database as db
import ai_logic as ai
import search_engine as se
import image_tools as it
import keep_alive
import formatter as fmt

# ══════════════════════════════════════════════════════════════════════════════
# LOGGING
# ══════════════════════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("friday")

# ══════════════════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════════════════

BOT_TOKEN = os.environ.get("BOT_TOKEN")
MY_ID = int(os.environ.get("ADMIN_ID", "8234592104"))
LOG_GROUP_ID = int(os.environ.get("LOG_GROUP_ID", "-1003848412289"))

GEMINI_KEYS = [
    os.environ.get("GEMINI_KEY_1"),
    os.environ.get("GEMINI_KEY_2"),
    os.environ.get("GEMINI_KEY_3"),
]

MAX_REQ_PER_MIN = 9
THREAD_WORKERS = 12

if not BOT_TOKEN:
    log.error("BOT_TOKEN not set — exiting")
    sys.exit(1)
if not any(GEMINI_KEYS):
    log.error("No Gemini keys — exiting")
    sys.exit(1)

# ══════════════════════════════════════════════════════════════════════════════
# INIT
# ══════════════════════════════════════════════════════════════════════════════

bot = telebot.TeleBot(BOT_TOKEN, parse_mode=None)
executor = ThreadPoolExecutor(max_workers=THREAD_WORKERS)

db.init_db()
db.init_memories_table()
ai.init_gemini(GEMINI_KEYS)
ai.reload_configs()

# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════


def eh(text: str) -> str:
    return html.escape(str(text))


def is_admin(user_id: int) -> bool:
    # SECURITY: only trusts real Telegram user_id, never username or message text
    return db.is_owner(user_id, MY_ID)


def _user_role(user_data: dict | None, user_id: int) -> str:
    if is_admin(user_id):
        return "owner"
    if not user_data:
        return "user"
    # Read role from DB — set by admin commands, never from user input
    return user_data.get("role", db.ROLE_USER)


# ── Rate limiter ───────────────────────────────────────────────────────────────

_rate_data: dict[int, deque] = defaultdict(lambda: deque())
_rate_lock = threading.Lock()


def is_rate_limited(user_id: int) -> bool:
    if db.is_owner(user_id, MY_ID):
        return False
    now = time.time()
    with _rate_lock:
        dq = _rate_data[user_id]
        while dq and now - dq[0] > 60:
            dq.popleft()
        if len(dq) >= MAX_REQ_PER_MIN:
            return True
        dq.append(now)
        return False


# ── Access check ───────────────────────────────────────────────────────────────


def check_user_access(message) -> bool:
    # SECURITY: uid always from message.from_user.id — Telegram-verified, never from text
    uid = message.from_user.id
    uname = message.from_user.first_name or "User"

    # Owner: unlimited
    if db.is_owner(uid, MY_ID):
        return True

    db.upsert_user(uid, uname)
    db.reset_daily_if_needed(uid)

    # Ban check (role stored in DB, set only by admin commands)
    if db.is_banned(uid):
        bot.reply_to(message, "<b>Access Denied</b>\nআপনার অ্যাকাউন্ট সাময়িকভাবে নিষিদ্ধ।", parse_mode="HTML")
        return False

    # Rate limit (burst protection)
    if is_rate_limited(uid):
        bot.reply_to(message,
            f"<b>Rate Limit</b>\nপ্রতি মিনিটে সর্বোচ্চ <code>{MAX_REQ_PER_MIN}</code> request। একটু অপেক্ষা করুন।",
            parse_mode="HTML")
        return False

    # Daily limit enforcement
    if db.at_daily_limit(uid):
        user = db.get_user(uid)
        lim  = user["daily_limit"] if user else 50
        bot.reply_to(message,
            f"<b>Daily Limit Reached</b>\nআজকের <code>{lim}</code>টি request শেষ। কাল আবার চেষ্টা করুন।",
            parse_mode="HTML")
        return False

    return True


# ── Thumbs-up ─────────────────────────────────────────────────────────────────


def react_ok(chat_id: int, message_id: int):
    try:
        bot.set_message_reaction(
            chat_id, message_id, [telebot.types.ReactionTypeEmoji("👍")]
        )
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# FORUM TOPIC LOGGING
# ══════════════════════════════════════════════════════════════════════════════

_topic_lock = threading.Lock()
TG_MAX_LEN = 4096


def _is_thread_gone(err: Exception) -> bool:
    """True if the error means the forum topic no longer exists in Telegram."""
    msg = str(err).lower()
    return (
        "message thread not found" in msg
        or "topic_deleted" in msg
        or "topic_closed" in msg
        or "chat not found" in msg
        or ("bad request" in msg and "thread" in msg)
    )


def _send_chunks(
    chat_id: int, text: str, thread_id: int | None, parse_mode: str = "HTML"
):
    # Split safely — never break an HTML tag across message boundaries
    if parse_mode == "HTML":
        chunks = fmt.split_html_safe(text, max_len=TG_MAX_LEN)
    else:
        chunks = [text[i : i + TG_MAX_LEN] for i in range(0, max(len(text), 1), TG_MAX_LEN)]

    for chunk in chunks:
        if not chunk:
            continue
        try:
            if thread_id:
                bot.send_message(
                    chat_id, chunk, message_thread_id=thread_id, parse_mode=parse_mode
                )
            else:
                bot.send_message(chat_id, chunk, parse_mode=parse_mode)
        except Exception as e:
            if _is_thread_gone(e):
                raise  # propagate so caller can recreate topic
            log.warning(f"Chunk send failed: {e}")


def get_or_create_topic(user_id: int, user_name: str) -> int | None:
    """Return existing topic_id or create a new forum topic. 0/None = no topic."""
    user = db.get_user(user_id)
    if user and user.get("topic_id"):   # 0 treated as "no topic" → recreate
        return user["topic_id"]
    with _topic_lock:
        # Re-check inside lock to avoid double-creation
        user = db.get_user(user_id)
        if user and user.get("topic_id"):
            return user["topic_id"]
        try:
            topic = bot.create_forum_topic(LOG_GROUP_ID, f"👤 {user_name} | {user_id}")
            tid = topic.message_thread_id
            db.set_topic_id(user_id, tid)
            log.info(f"Forum topic {tid} created for user {user_id}")
            return tid
        except Exception as e:
            log.warning(f"Forum topic creation failed: {e}")
            return None


def send_log(user_id: int, user_name: str, text: str):
    """Send log text to user's forum topic, auto-recreating if topic was deleted."""
    try:
        tid = get_or_create_topic(user_id, user_name)
        if not tid:
            _send_chunks(
                LOG_GROUP_ID, f"<b>👤 {eh(user_name)} ({user_id})</b>\n\n{text}", None
            )
            return
        try:
            _send_chunks(LOG_GROUP_ID, text, tid)
        except Exception as e:
            if _is_thread_gone(e):
                log.warning(f"Topic {tid} gone for user {user_id} — auto-recreating")
                db.set_topic_id(user_id, 0)   # reset so get_or_create makes a new one
                new_tid = get_or_create_topic(user_id, user_name)
                if new_tid:
                    _send_chunks(LOG_GROUP_ID, text, new_tid)
            else:
                log.warning(f"send_log chunk error: {e}")
    except Exception as e:
        log.warning(f"send_log error: {e}")


def send_log_copy(user_id: int, user_name: str, chat_id: int, message_id: int):
    """Forward a user message to their forum topic, auto-recreating if needed."""
    try:
        tid = get_or_create_topic(user_id, user_name)
        if not tid:
            return
        try:
            bot.forward_message(LOG_GROUP_ID, chat_id, message_id, message_thread_id=tid)
        except Exception as e:
            if _is_thread_gone(e):
                log.warning(f"Topic {tid} gone for user {user_id} — recreating")
                db.set_topic_id(user_id, 0)
                new_tid = get_or_create_topic(user_id, user_name)
                if new_tid:
                    bot.forward_message(
                        LOG_GROUP_ID, chat_id, message_id, message_thread_id=new_tid
                    )
            else:
                log.debug(f"Forward to topic failed: {e}")
    except Exception as e:
        log.debug(f"send_log_copy error: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# SEARCH CONTEXT HELPER
# ══════════════════════════════════════════════════════════════════════════════


def _get_search_context(query: str) -> str | None:
    """Fetch DDG results, sanitize against injection, return snippet block."""
    if not se.DDG_AVAILABLE:
        return None
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=10))
        safe = []
        for r in results:
            title = r.get("title", "")[:200]
            body  = r.get("body",  "")[:400]
            href  = r.get("href",  "")[:120]
            if not se.is_safe_result(title, body, href):
                continue
            clean = se.sanitize_snippet(body)
            if clean:
                safe.append({"title": title, "body": clean, "href": href})
        if not safe:
            return None
        snippets = ""
        for i, r in enumerate(safe[:6], 1):
            snippets += f"{i}. {r['title']}\n   {r['body']}\n   {r['href']}\n\n"
        log.info(f"Search context: {len(safe)} safe results for '{query[:50]}'")
        return snippets
    except Exception as e:
        log.warning(f"Search context error: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# ADMIN DECORATOR + RESOLVER
# ══════════════════════════════════════════════════════════════════════════════


def admin_only(func):
    import functools

    @functools.wraps(func)
    def wrapper(message):
        # SECURITY: check real Telegram user_id only
        if not db.is_owner(message.from_user.id, MY_ID):
            return
        return func(message)

    return wrapper


def resolve_target(message) -> int | None:
    if message.message_thread_id:
        uid = db.get_user_by_topic(message.message_thread_id)
        if uid:
            return uid
    parts = message.text.split()
    if len(parts) >= 2:
        try:
            return int(parts[1])
        except ValueError:
            pass
    return None


def _arg(message, n: int = 1) -> str:
    """Return nth argument from message text (0-indexed after command)."""
    parts = message.text.split(None, n + 1)
    return parts[n].strip() if len(parts) > n else ""


def _admin_target_and_text(message) -> tuple[int | None, str]:
    """
    Parse admin command from forum topic or explicit user_id.
    Returns (target_uid, text_payload).
    - In forum topic  : uid from topic,   entire text after command is payload
    - Explicit user_id: uid from text[1], text[2:] is payload
    """
    tid = message.message_thread_id
    raw_parts = message.text.split(None, 1)          # ["/cmd", "rest of text"]
    rest = raw_parts[1].strip() if len(raw_parts) >= 2 else ""

    if tid:
        uid = db.get_user_by_topic(tid)
        if uid:
            return uid, rest

    # Try to parse user_id as first token of rest
    rest_parts = rest.split(None, 1)
    if rest_parts:
        try:
            uid = int(rest_parts[0])
            payload = rest_parts[1].strip() if len(rest_parts) >= 2 else ""
            return uid, payload
        except ValueError:
            pass
    return None, rest


# ══════════════════════════════════════════════════════════════════════════════
# COMMANDS — PUBLIC
# ══════════════════════════════════════════════════════════════════════════════

# ── /id ────────────────────────────────────────────────────────────────────────

@bot.message_handler(commands=["id"])
def cmd_id(message):
    """Return the real Telegram user_id of the sender. Works in any chat."""
    uid   = message.from_user.id   # always from Telegram, never from text
    uname = message.from_user.first_name or "User"
    role  = db.get_user_role(uid)
    if db.is_owner(uid, MY_ID):
        role = "owner"
    bot.reply_to(message,
        f"🆔 <b>Your Telegram ID</b>\n\n"
        f"ID  : <code>{uid}</code>\n"
        f"Name: {eh(uname)}\n"
        f"Role: {role}",
        parse_mode="HTML")


# ── /start ─────────────────────────────────────────────────────────────────────


@bot.message_handler(commands=["start"])
def cmd_start(message):
    if message.chat.type != "private":
        return
    uid = message.from_user.id
    uname = message.from_user.first_name or "User"
    db.upsert_user(uid, uname)

    text = (
        f"<b>Friday AI</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"হ্যালো, <b>{eh(uname)}</b>!\n\n"
        "Advanced AI Assistant — powered by Gemini + real-time web search.\n"
        f"নির্মাতা: <a href='https://t.me/hm_burhan'>@hm_burhan</a>\n\n"
        "<b>Commands</b>\n"
        "<code>/search</code> ‹query› — Real-time web search\n"
        "<code>/yt</code> ‹query›     — Video search\n"
        "<code>/image</code> ‹query›  — Image search\n"
        "<code>/enhance</code>        — Photo enhancement\n"
        "<code>/status</code>         — Account info\n"
        "<code>/clear</code>          — Reset memory\n"
        "<code>/help</code>           — All commands\n\n"
        "যেকোনো প্রশ্ন বা ছবি সরাসরি পাঠান।"
    )
    bot.reply_to(message, text, parse_mode="HTML")
    send_log(
        uid,
        uname,
        f"<b>New Start</b>\n<code>{uid}</code> — {eh(uname)}",
    )


# ── /help ──────────────────────────────────────────────────────────────────────


@bot.message_handler(commands=["help"])
def cmd_help(message):
    if message.chat.type != "private":
        return
    uid = message.from_user.id

    public_help = (
        "<b>Friday AI — Help</b>\n"
        "━━━━━━━━━━━━━━\n"
        "<b>Chat</b>\n"
        "যেকোনো প্রশ্ন সরাসরি পাঠান — AI উত্তর দেবে\n"
        "News/events স্বয়ংক্রিয়ভাবে web search করে উত্তর দেয়\n\n"
        "<b>Search</b>\n"
        "<code>/search</code> ‹keyword› — Real-time web search\n"
        "<code>/image</code>  ‹keyword› — Image search\n"
        "<code>/yt</code>     ‹keyword› — Video search\n\n"
        "<b>Image</b>\n"
        "ছবি পাঠান → AI বিশ্লেষণ করবে\n"
        "<code>/enhance</code> → ছবি পাঠান → enhanced ছবি পাবেন\n\n"
        "<b>Account</b>\n"
        "<code>/status</code> — Account info ও usage\n"
        "<code>/clear</code>  — Memory reset\n\n"
        "━━━━━━━━━━━━━━\n"
        "Engine: Gemini + DuckDuckGo  |  <a href='https://t.me/hm_burhan'>@hm_burhan</a>"
    )

    admin_extra = (
        "\n\n<b>Admin Commands</b>\n"
        "━━━━━━━━━━━━━━\n"
        "Run in user topic or pass [id] explicitly.\n\n"
        "<code>/ban</code> [id]               — Ban user\n"
        "<code>/unban</code> [id]             — Unban user\n"
        "<code>/premium</code> [id]           — Grant premium (500/day)\n"
        "<code>/limit</code> [id] ‹n›         — Set limit; no number = restore\n"
        "<code>/add_info</code> ‹text›        — Append to global knowledge base\n"
        "<code>/set_user_info</code> [id] ‹t› — Personal memory for user\n"
        "<code>/set_tone</code> [id] ‹tone›   — Per-user reply tone\n"
        "<code>/set_policy</code> [id] ‹t›    — Per-user behavior policy\n"
        "<code>/clear_user_memory</code> [id] — Clear session history\n"
        "<code>/wipe_memory</code> [id]       — Wipe session + info + tone\n"
        "<code>/broadcast</code> ‹text›       — Message all users\n"
        "<code>/reload</code>                 — Reload config files\n"
        "<code>/stats</code>                  — Bot statistics"
    )

    text = public_help + (admin_extra if is_admin(uid) else "")
    bot.reply_to(message, text, parse_mode="HTML")


# ── /status ────────────────────────────────────────────────────────────────────


@bot.message_handler(commands=["status"])
def cmd_status(message):
    if message.chat.type != "private":
        return
    uid = message.from_user.id
    uname = message.from_user.first_name or "User"

    if is_admin(uid):
        bot.reply_to(
            message,
            "<b>Account Status</b>\n"
            "━━━━━━━━━━━━━━\n"
            "Role: <code>owner</code>\n"
            "Limit: Unlimited\n"
            "Rate limit: None\n"
            "━━━━━━━━━━━━━━\n"
            "<a href='https://t.me/hm_burhan'>@hm_burhan</a>",
            parse_mode="HTML",
        )
        return

    db.upsert_user(uid, uname)
    db.reset_daily_if_needed(uid)
    user = db.get_user(uid)
    if not user:
        bot.reply_to(message, "তথ্য পাওয়া যায়নি।")
        return

    remaining     = max(0, user["daily_limit"] - user["daily_count"])
    role          = user.get("role", "user")
    role_label    = "premium" if role == db.ROLE_PREMIUM else ("banned" if role == db.ROLE_BANNED else "user")
    session_msgs  = ai.session_length(uid)

    text = (
        f"<b>Account Status</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"Name   : <b>{eh(uname)}</b>\n"
        f"ID     : <code>{uid}</code>\n"
        f"Role   : <code>{role_label}</code>\n\n"
        f"Today  : <code>{user['daily_count']}</code> / <code>{user['daily_limit']}</code>\n"
        f"Left   : <code>{remaining}</code> requests\n"
        f"Memory : <code>{session_msgs}</code> messages\n"
        f"━━━━━━━━━━━━━━\n"
        f"<a href='https://t.me/hm_burhan'>@hm_burhan</a>"
    )
    bot.reply_to(message, text, parse_mode="HTML")


# ── /policy (user-friendly view of their "chat settings") ─────────────────────


@bot.message_handler(commands=["policy"])
def cmd_policy(message):
    if message.chat.type != "private":
        return
    uid = message.from_user.id
    uname = message.from_user.first_name or "User"
    db.upsert_user(uid, uname)
    user = db.get_user(uid)

    # Show configurable policy state (without revealing it's admin-set)
    cfg = ai.get_configs()
    policy_flags = ai._parse_policy(cfg.get("policy", ""))

    enabled = [
        k.replace("_allowed", "").replace("_", " ").title()
        for k, v in policy_flags.items()
        if v
    ]
    disabled = [
        k.replace("_allowed", "").replace("_", " ").title()
        for k, v in policy_flags.items()
        if not v
    ]

    per_user_note = ""
    if user and user.get("policy"):
        per_user_note = "\n✅ আপনার জন্য বিশেষ chat settings active আছে।"

    text = (
        "<b>Chat Settings</b>\n"
        "━━━━━━━━━━━━━━\n"
        + (f"Enabled  : {', '.join(enabled)}\n" if enabled else "")
        + (f"Disabled : {', '.join(disabled)}\n" if disabled else "")
        + per_user_note
        + "\n━━━━━━━━━━━━━━\n"
        "<a href='https://t.me/hm_burhan'>@hm_burhan</a>"
    )
    bot.reply_to(message, text, parse_mode="HTML")


# ── /clear ─────────────────────────────────────────────────────────────────────


@bot.message_handler(commands=["clear"])
def cmd_clear(message):
    if message.chat.type != "private":
        return
    ai.clear_session(message.from_user.id)
    bot.reply_to(message, "<b>Memory Cleared</b>\nকথোপকথনের ইতিহাস মুছে গেছে।", parse_mode="HTML")


# ── /search ────────────────────────────────────────────────────────────────────


@bot.message_handler(commands=["search"])
def cmd_search(message):
    if message.chat.type != "private":
        return
    if not check_user_access(message):
        return
    query = _arg(message, 1)
    if not query:
        bot.reply_to(message, "Usage: <code>/search</code> ‹keyword›", parse_mode="HTML")
        return
    react_ok(message.chat.id, message.message_id)
    executor.submit(_do_search, message, query)


def _do_search(message, query: str):
    uid = message.from_user.id
    uname = message.from_user.first_name or "User"
    try:
        bot.send_chat_action(message.chat.id, "typing")
        answer = se.web_search_and_summarize(query, ai.generate_ai_response)
        db.increment_daily_count(uid)
        formatted = fmt.format_telegram(answer)
        chunks    = fmt.split_html_safe(formatted, max_len=4000)
        bot.reply_to(message, chunks[0], parse_mode="HTML")
        for chunk in chunks[1:]:
            bot.send_message(message.chat.id, chunk, parse_mode="HTML")
        send_log(uid, uname, f"<b>Search</b>: {eh(query)}\n{eh(answer[:200])}")
    except Exception as e:
        log.error(f"Search error: {e}")
        bot.reply_to(message, "Search করতে সমস্যা হয়েছে। আবার চেষ্টা করুন।")


# ── /yt ────────────────────────────────────────────────────────────────────────


@bot.message_handler(commands=["yt"])
def cmd_yt(message):
    if message.chat.type != "private":
        return
    if not check_user_access(message):
        return
    query = _arg(message, 1)
    if not query:
        bot.reply_to(message, "Usage: <code>/yt</code> ‹keyword›", parse_mode="HTML")
        return
    react_ok(message.chat.id, message.message_id)
    executor.submit(_do_yt, message, query)


def _do_yt(message, query: str):
    uid = message.from_user.id
    uname = message.from_user.first_name or "User"
    try:
        bot.send_chat_action(message.chat.id, "typing")
        videos = se.video_search(query, max_results=5)
        if videos:
            text = f"<b>Video Results</b> — {eh(query)}\n━━━━━━━━━━━━━━\n"
            for v in videos[:5]:
                title = eh((v.get("title") or "No Title")[:100])
                url   = v.get("content") or v.get("embed_url", "")
                text += f"<b>{title}</b>\n<code>{eh(url)}</code>\n\n"
            bot.reply_to(message, text, parse_mode="HTML")
        else:
            bot.reply_to(message, "কোনো ভিডিও পাওয়া যায়নি।")
        send_log(uid, uname, f"<b>YT</b>: {eh(query)}")
    except Exception as e:
        log.error(f"YT error: {e}")
        bot.reply_to(message, "Video search করতে সমস্যা হয়েছে।")


# ── /image ─────────────────────────────────────────────────────────────────────


@bot.message_handler(commands=["image"])
def cmd_image(message):
    if message.chat.type != "private":
        return
    if not check_user_access(message):
        return
    query = _arg(message, 1)
    if not query:
        bot.reply_to(message, "Usage: <code>/image</code> ‹keyword›", parse_mode="HTML")
        return
    react_ok(message.chat.id, message.message_id)
    executor.submit(_do_image, message, query)


def _do_image(message, query: str):
    uid = message.from_user.id
    uname = message.from_user.first_name or "User"
    try:
        bot.send_chat_action(message.chat.id, "upload_photo")
        results = se.image_search(query, max_results=12)
        if not results:
            bot.reply_to(message, f"<b>No Results</b>\n<code>{eh(query)}</code> এর জন্য কোনো ছবি পাওয়া যায়নি।", parse_mode="HTML")
            return
        sent = it.send_photo_safe(
            bot,
            message.chat.id,
            results,
            caption=query,
            reply_to=message.message_id,
        )
        if not sent:
            bot.reply_to(message, "ছবি পাঠাতে সমস্যা হয়েছে। আবার চেষ্টা করুন।")
            return
        db.increment_daily_count(uid)
        send_log(uid, uname, f"<b>Image</b>: {eh(query)}")
    except Exception as e:
        log.error(f"Image error: {e}")
        bot.reply_to(message, "Image search error। আবার চেষ্টা করুন।")


# ── /enhance ───────────────────────────────────────────────────────────────────

_enhance_pending: dict[int, bool] = {}
_enhance_lock = threading.Lock()


@bot.message_handler(commands=["enhance"])
def cmd_enhance(message):
    if message.chat.type != "private":
        return
    if not check_user_access(message):
        return
    with _enhance_lock:
        _enhance_pending[message.from_user.id] = True
    bot.reply_to(message, "<b>Enhance Ready</b>\nএখন একটি ছবি পাঠান — sharpen, denoise ও upscale করা হবে।", parse_mode="HTML")


# ══════════════════════════════════════════════════════════════════════════════
# COMMANDS — ADMIN
# ═══════════════════════>V�══════════════════════════════════════════════════════


@bot.message_handler(commands=["reload"])
@admin_only
def cmd_reload(message):
    cfg = ai.reload_configs()
    loaded = sum(1 for v in cfg.values() if v)
    bot.reply_to(
        message,
        f"<b>Config Reloaded</b>\n<code>{loaded}/{len(cfg)}</code> files loaded.",
        parse_mode="HTML",
    )
    log.info("Config reloaded by admin")


@bot.message_handler(commands=["stats"])
@admin_only
def cmd_stats(message):
    try:
        all_ids = db.get_all_user_ids()
        total   = len(all_ids)
        bot.reply_to(
            message,
            "<b>Bot Statistics</b>\n"
            "━━━━━━━━━━━━━━\n"
            f"Total users : <code>{total}</code>\n"
            "Bot         : Friday AI\n"
            "Gemini keys : <code>3</code>\n"
            "Session cap : <code>4</code> pairs\n"
            "━━━━━━━━━━━━━━\n"
            "<a href='https://t.me/hm_burhan'>@hm_burhan</a>",
            parse_mode="HTML",
        )
    except Exception as e:
        bot.reply_to(message, f"<b>Error</b>\n<code>{eh(str(e))}</code>", parse_mode="HTML")


@bot.message_handler(commands=["ban"])
@admin_only
def cmd_ban(message):
    uid = resolve_target(message)
    if not uid:
        bot.reply_to(message, "Usage: <code>/ban</code> ‹user_id›  — or run in topic", parse_mode="HTML")
        return
    db.set_user_field(uid, "is_banned", 1)
    bot.reply_to(message, f"<b>Banned</b>\n<code>{uid}</code>", parse_mode="HTML")
    log.info(f"Admin banned user {uid}")


@bot.message_handler(commands=["unban"])
@admin_only
def cmd_unban(message):
    uid = resolve_target(message)
    if not uid:
        bot.reply_to(message, "Usage: <code>/unban</code> ‹user_id›  — or run in topic", parse_mode="HTML")
        return
    db.set_user_field(uid, "is_banned", 0)
    bot.reply_to(message, f"<b>Unbanned</b>\n<code>{uid}</code>", parse_mode="HTML")


@bot.message_handler(commands=["premium"])
@admin_only
def cmd_premium(message):
    uid = resolve_target(message)
    if not uid:
        bot.reply_to(message, "Usage: <code>/premium</code> ‹user_id›  — or run in topic", parse_mode="HTML")
        return
    # _set_role sets both role=premium AND daily_limit=500 atomically
    db.set_user_field(uid, "is_premium", 1)
    bot.reply_to(
        message,
        f"<b>Premium Granted</b>\n<code>{uid}</code>  —  500 requests/day",
        parse_mode="HTML",
    )
    log.info(f"Admin granted premium to {uid}")


@bot.message_handler(commands=["limit"])
@admin_only
def cmd_limit(message):
    uid = resolve_target(message)
    if not uid:
        bot.reply_to(message, "Usage: <code>/limit</code> ‹user_id› ‹n›  — no number = restore defaults", parse_mode="HTML")
        return
    parts = message.text.split()
    # BUGFIX: parts[-1] could be the user_id itself if no N given.
    # A valid limit is a non-negative integer ≤ 100,000 that is NOT the user_id.
    try:
        candidate = int(parts[-1])
        if candidate == uid or not (0 <= candidate <= 100_000):
            # Last token is the uid or out of range — treat as "restore defaults"
            raise ValueError("not a valid limit")
        db.set_user_field(uid, "daily_limit", candidate)
        bot.reply_to(
            message,
            f"<b>Limit Updated</b>\n<code>{uid}</code>  —  <code>{candidate}</code>/day",
            parse_mode="HTML",
        )
        log.info(f"Admin set limit {candidate}/day for {uid}")
    except (ValueError, IndexError):
        # No valid N → restore role to user, limit to 50/day
        db.set_user_field(uid, "is_premium", 0)  # _set_role sets role=user, limit=50
        bot.reply_to(
            message,
            f"<b>Restored</b>\n<code>{uid}</code>  —  50/day (free plan)",
            parse_mode="HTML",
        )
        log.info(f"Admin restored defaults for {uid}")


@bot.message_handler(commands=["add_info"])
@admin_only
def cmd_add_info(message):
    """Add text to GLOBAL knowledge_base.txt (appends, no user_id needed)."""
    parts = message.text.split(None, 1)
    info = parts[1].strip() if len(parts) >= 2 else ""
    if not info:
        bot.reply_to(message, "Usage: <code>/add_info</code> ‹text to add globally›", parse_mode="HTML")
        return
    kb_path = ai.CONFIG_DIR / "knowledge_base.txt"
    try:
        existing = kb_path.read_text(encoding="utf-8") if kb_path.exists() else ""
        separator = "\n\n" if existing.strip() else ""
        kb_path.write_text(existing + separator + info, encoding="utf-8")
        ai.reload_configs()
        bot.reply_to(message, "<b>Knowledge Base Updated</b>\nGlobal context reloaded.", parse_mode="HTML")
        log.info(f"Admin appended to knowledge_base.txt: {info[:60]}")
    except Exception as e:
        bot.reply_to(message, f"<b>Error</b>\n<code>{eh(str(e))}</code>", parse_mode="HTML")


@bot.message_handler(commands=["set_user_info"])
@admin_only
def cmd_set_user_info(message):
    """Set personal memory/behavior for a specific user (stored in DB)."""
    uid, info = _admin_target_and_text(message)
    if not uid or not info:
        bot.reply_to(message, "Usage: <code>/set_user_info</code> ‹user_id› ‹info›  — or run in topic", parse_mode="HTML")
        return
    db.set_user_field(uid, "custom_info", info)
    bot.reply_to(
        message,
        f"<b>User Info Set</b>\n<code>{uid}</code>",
        parse_mode="HTML",
    )


@bot.message_handler(commands=["set_tone"])
@admin_only
def cmd_set_tone(message):
    """Set per-user AI reply tone (overrides global reply_tone.txt)."""
    uid, tone = _admin_target_and_text(message)
    if not uid or not tone:
        bot.reply_to(
            message,
            "Usage: <code>/set_tone</code> ‹user_id› ‹tone›  — or run in topic\n"
            "Example: <code>/set_tone 12345 Reply in formal English only.</code>",
            parse_mode="HTML",
        )
        return
    db.set_user_field(uid, "custom_tone", tone)
    bot.reply_to(
        message,
        f"<b>Tone Set</b>\n<code>{uid}</code>\n━━━━━━━━━━━━━━\n{eh(tone)}",
        parse_mode="HTML",
    )
    log.info(f"Admin set custom_tone for {uid}: {tone[:60]}")


@bot.message_handler(commands=["set_policy"])
@admin_only
def cmd_set_policy(message):
    uid, pol = _admin_target_and_text(message)
    if not uid or not pol:
        bot.reply_to(message, "Usage: <code>/set_policy</code> ‹user_id› ‹policy›  — or run in topic", parse_mode="HTML")
        return
    db.set_user_field(uid, "policy", pol)
    bot.reply_to(message, f"<b>Policy Set</b>\n<code>{uid}</code>", parse_mode="HTML")


@bot.message_handler(commands=["clear_user_memory"])
@admin_only
def cmd_clear_user_memory(message):
    """Clear a specific user's AI session (conversation history only)."""
    uid, _ = _admin_target_and_text(message)
    if not uid:
        bot.reply_to(
            message,
            "Usage: <code>/clear_user_memory</code> ‹user_id›  — or run in topic\n"
            "Clears session only. Info/policy stays.",
            parse_mode="HTML",
        )
        return
    ai.clear_session(uid)
    bot.reply_to(
        message,
        f"<b>Session Cleared</b>\n<code>{uid}</code>",
        parse_mode="HTML",
    )
    log.info(f"Admin cleared session for user {uid}")


@bot.message_handler(commands=["wipe_memory"])
@admin_only
def cmd_wipe_memory(message):
    """Full memory wipe: session + custom_info + custom_tone for a user."""
    uid, _ = _admin_target_and_text(message)
    if not uid:
        bot.reply_to(
            message,
            "Usage: <code>/wipe_memory</code> ‹user_id›  — or run in topic\n"
            "Wipes session + info + tone. Policy stays.",
            parse_mode="HTML",
        )
        return
    ai.clear_session(uid)
    db.set_user_field(uid, "custom_info", "")
    db.set_user_field(uid, "custom_tone", "")
    bot.reply_to(
        message,
        f"<b>Memory Wiped</b>\n<code>{uid}</code>\n━━━━━━━━━━━━━━\nSession, info, tone cleared.",
        parse_mode="HTML",
    )
    log.info(f"Admin wiped full memory for user {uid}")


@bot.message_handler(commands=["broadcast"])
@admin_only
def cmd_broadcast(message):
    text = message.text.split(None, 1)
    if len(text) < 2 or not text[1].strip():
        bot.reply_to(message, "Usage: <code>/broadcast</code> ‹message›", parse_mode="HTML")
        return
    executor.submit(_do_broadcast, message, text[1].strip())


def _do_broadcast(message, text: str):
    all_ids = db.get_all_user_ids()
    sent = failed = 0
    for uid in all_ids:
        try:
            bot.send_message(uid, text)
            sent += 1
            time.sleep(0.05)
        except Exception:
            failed += 1
    bot.reply_to(
        message,
        f"<b>Broadcast Complete</b>\n"
        f"Sent   : <code>{sent}</code>\n"
        f"Failed : <code>{failed}</code>",
        parse_mode="HTML",
    )
    log.info(f"Broadcast: {sent} sent, {failed} failed")


@bot.message_handler(commands=["notify"])
@admin_only
def cmd_notify(message):
    """Send a direct private notification to a specific user. Backend only."""
    parts = message.text.split(None, 2)
    if len(parts) < 3:
        bot.reply_to(
            message,
            "Usage: <code>/notify</code> ‹user_id› ‹message›",
            parse_mode="HTML",
        )
        return
    try:
        target_uid = int(parts[1])
    except ValueError:
        bot.reply_to(message, "<b>Error</b>\nInvalid user_id.", parse_mode="HTML")
        return
    text = parts[2].strip()
    if not text:
        bot.reply_to(message, "<b>Error</b>\nMessage cannot be empty.", parse_mode="HTML")
        return
    try:
        bot.send_message(target_uid, text)
        bot.reply_to(
            message,
            f"<b>Notification Sent</b>\n<code>{target_uid}</code>",
            parse_mode="HTML",
        )
        log.info(f"Admin notified user {target_uid}: {text[:60]}")
    except Exception as e:
        bot.reply_to(message, f"<b>Failed</b>\n<code>{eh(str(e))}</code>", parse_mode="HTML")


# ══════════════════════════════════════════════════════════════════════════════
# PHOTO HANDLER
# ══════════════════════════════════════════════════════════════════════════════


@bot.message_handler(content_types=["photo"])
def handle_photo(message):
    # SECURITY: admin/log group must NEVER reach AI pipeline
    if message.chat.id == LOG_GROUP_ID:
        return
    if message.chat.type != "private":
        return
    uid = message.from_user.id

    with _enhance_lock:
        pending = _enhance_pending.pop(uid, False)

    if pending:
        if not check_user_access(message):
            return
        react_ok(message.chat.id, message.message_id)
        executor.submit(_do_enhance, message)
    else:
        if not check_user_access(message):
            return
        react_ok(message.chat.id, message.message_id)
        executor.submit(_do_photo_ai, message)


def _do_enhance(message):
    uid = message.from_user.id
    uname = message.from_user.first_name or "User"
    src = out = None
    try:
        bot.send_chat_action(message.chat.id, "upload_photo")
        src = it.save_telegram_photo(bot, message.photo)
        if not src:
            bot.reply_to(message, "ছবি download করা যায়নি।")
            return
        out = it.enhance_image(src)
        if not out:
            bot.reply_to(message, "<b>Enhancement Unavailable</b>\nPillow error — আবার চেষ্টা করুন।", parse_mode="HTML")
            return
        with open(out, "rb") as f:
            bot.send_photo(
                message.chat.id,
                f,
                caption="Enhanced — sharpen + denoise + upscale applied.",
                reply_to_message_id=message.message_id,
            )
        db.increment_daily_count(uid)
        send_log(uid, uname, "<b>Image Enhanced</b>")
    except Exception as e:
        log.error(f"Enhance error: {e}")
        bot.reply_to(message, "Enhancement failed। আবার চেষ্টা করুন।")
    finally:
        it.cleanup(src, out)


def _do_photo_ai(message):
    uid = message.from_user.id
    uname = message.from_user.first_name or "User"
    src = None
    try:
        bot.send_chat_action(message.chat.id, "typing")
        src = it.save_telegram_photo(bot, message.photo)
        if not src:
            bot.reply_to(message, "ছবি process করা যায়নি।")
            return

        from PIL import Image

        img = Image.open(src)
        # Use BOTH the image AND the caption text together
        caption_text = (message.caption or "").strip()
        if caption_text:
            # User sent a question/instruction along with the image
            user_query   = caption_text
            session_text = caption_text
        else:
            user_query   = "এই ছবিটি বিশ্লেষণ করো এবং বিস্তারিত বলো।"
            session_text = "[User sent an image]"

        user_data = db.get_user(uid) or {}
        role = _user_role(user_data, uid)
        prompt = ai.build_prompt(
            uid, user_query, user_data, user_name=uname, user_role=role
        )
        # image is passed alongside the prompt — Gemini reads both
        reply = ai.generate_ai_response(prompt, image=img)

        ai.add_to_session(uid, "user", session_text)
        ai.add_to_session(uid, "assistant", reply)
        db.increment_daily_count(uid)

        # Send with HTML code formatting
        formatted = fmt.format_telegram(reply)
        chunks    = fmt.split_html_safe(formatted, max_len=4000)
        bot.reply_to(message, chunks[0], parse_mode="HTML")
        for chunk in chunks[1:]:
            bot.send_message(message.chat.id, chunk, parse_mode="HTML")

        send_log_copy(uid, uname, message.chat.id, message.message_id)
        send_log(uid, uname, f"🖼 <b>Vision</b>\n{eh(reply[:400])}")
    except Exception as e:
        log.error(f"Photo AI error: {e}")
        bot.reply_to(message, "ছবিটি বিশ্লেষণ করা যায়নি। আবার চেষ্টা করুন।")
    finally:
        it.cleanup(src)


# ══════════════════════════════════════════════════════════════════════════════
# MEDIA FORWARDING — video, document, voice, audio, sticker
# ══════════════════════════════════════════════════════════════════════════════


def _forward_media_to_topic(message, media_type: str):
    """Forward any media message to the user's forum topic."""
    if message.chat.type != "private":
        return
    uid   = message.from_user.id
    uname = message.from_user.first_name or "User"
    db.upsert_user(uid, uname)
    try:
        send_log_copy(uid, uname, message.chat.id, message.message_id)
        log.info(f"Forwarded {media_type} from {uid} to topic")
    except Exception as e:
        log.warning(f"Media forward error ({media_type}): {e}")


@bot.message_handler(content_types=["video"])
def handle_video(message):
    executor.submit(_forward_media_to_topic, message, "video")


@bot.message_handler(content_types=["document"])
def handle_document(message):
    executor.submit(_forward_media_to_topic, message, "document")


@bot.message_handler(content_types=["voice"])
def handle_voice(message):
    executor.submit(_forward_media_to_topic, message, "voice")


@bot.message_handler(content_types=["audio"])
def handle_audio(message):
    executor.submit(_forward_media_to_topic, message, "audio")


@bot.message_handler(content_types=["sticker"])
def handle_sticker(message):
    executor.submit(_forward_media_to_topic, message, "sticker")


# ══════════════════════════════════════════════════════════════════════════════
# STREAMING HELPER
# ══════════════════════════════════════════════════════════════════════════════

_EDIT_INTERVAL = 1.5   # seconds between Telegram message edits (reduced API calls)
_MIN_CHARS     = 30    # don't edit until at least this many chars accumulated


def _stream_reply(chat_id: int, reply_to_id: int, prompt: str, image=None) -> str:
    """
    Stream Gemini response → edit Telegram message live as text arrives.
    Returns the complete text when done.
    """
    # Send placeholder message immediately so user sees something
    try:
        sent = bot.send_message(chat_id, "✍️", reply_to_message_id=reply_to_id)
    except Exception as e:
        log.warning(f"Placeholder send failed: {e}")
        return ai.generate_ai_response(prompt, image)  # fallback to normal

    full_text  = ""
    last_edit  = 0.0
    last_sent  = ""   # track what's currently shown to avoid no-change edits

    for chunk in ai.stream_ai_response(prompt, image):
        full_text += chunk
        now = time.time()

        # Edit every _EDIT_INTERVAL seconds, only if content changed meaningfully
        if (now - last_edit >= _EDIT_INTERVAL
                and len(full_text) >= _MIN_CHARS
                and full_text != last_sent):
            try:
                display = full_text + "▌"   # typing cursor
                bot.edit_message_text(display, chat_id, sent.message_id)
                last_sent = full_text
                last_edit = now
            except Exception:
                pass  # rate limit or no-change — skip this edit

    # Final edit: apply HTML code formatting, remove cursor
    final = full_text.strip()
    if not final:
        final = "দুঃখিত, কোনো উত্তর পাওয়া যায়নি।"

    formatted = fmt.format_telegram(final)
    chunks    = fmt.split_html_safe(formatted, max_len=4000)

    # First chunk replaces the streaming placeholder
    try:
        bot.edit_message_text(
            chunks[0], chat_id, sent.message_id, parse_mode="HTML"
        )
    except Exception:
        try:
            bot.delete_message(chat_id, sent.message_id)
        except Exception:
            pass
        bot.send_message(
            chat_id, chunks[0], parse_mode="HTML",
            reply_to_message_id=reply_to_id,
        )

    # Additional chunks (long responses)
    for chunk in chunks[1:]:
        try:
            bot.send_message(chat_id, chunk, parse_mode="HTML")
        except Exception as e:
            log.warning(f"Extra chunk send failed: {e}")

    return final


# ══════════════════════════════════════════════════════════════════════════════
# TEXT HANDLER — AI Chat with auto-search + streaming
# ══════════════════════════════════════════════════════════════════════════════


@bot.message_handler(content_types=["text"])
def handle_text(message):
    # SECURITY: admin/log group must NEVER reach AI pipeline
    if message.chat.id == LOG_GROUP_ID:
        return
    if message.chat.type != "private":
        return
    if message.text.startswith("/"):
        return
    if not check_user_access(message):
        return
    react_ok(message.chat.id, message.message_id)
    executor.submit(_do_ai_chat, message)


def _do_ai_chat(message):
    uid       = message.from_user.id
    uname     = message.from_user.first_name or "User"
    user_text = message.text

    try:
        bot.send_chat_action(message.chat.id, "typing")
        db.upsert_user(uid, uname)
        user_data = db.get_user(uid) or {}
        role      = _user_role(user_data, uid)

        # Auto-detect real-time search need (entity + temporal aware)
        search_ctx = None
        if se.should_search(user_text):
            log.info(f"Auto-search triggered: {user_text[:60]}")
            search_ctx = _get_search_context(user_text)

        prompt = ai.build_prompt(
            uid, user_text, user_data,
            user_name=uname, user_role=role,
            search_context=search_ctx,
        )

        # Streaming reply — user sees text as it's generated
        reply = _stream_reply(message.chat.id, message.message_id, prompt)

        ai.add_to_session(uid, "user",      user_text)
        ai.add_to_session(uid, "assistant", reply)
        db.increment_daily_count(uid)

        badge = "🌐 Search+AI" if search_ctx else "🤖 AI"
        send_log(uid, uname,
                 f"{badge}\n📩 {eh(user_text[:200])}\n\n💬 {eh(reply[:300])}")

    except Exception as e:
        log.error(f"AI chat error: {e}")
        try:
            bot.reply_to(message, "দুঃখিত, সমস্যা হয়েছে। আবার চেষ্টা করুন।")
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════════════
# DB BACKUP
# ══════════════════════════════════════════════════════════════════════════════


def _backup_loop():
    while True:
        time.sleep(86_400)
        try:
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            with open(db.DB_PATH, "rb") as f:
                bot.send_document(
                    LOG_GROUP_ID,
                    f,
                    caption=f"📦 <b>Auto DB Backup</b> — {now}",
                    parse_mode="HTML",
                )
            log.info("DB backup sent")
        except Exception as e:
            log.error(f"Backup failed: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# STARTUP
# ══════════════════════════════════════════════════════════════════════════════

keep_alive.start()

threading.Thread(target=_backup_loop, daemon=True, name="db-backup").start()

log.info(f"Admin ID  : {MY_ID}")
log.info(f"Log group : {LOG_GROUP_ID}")
log.info(f"DDG search: {'enabled' if se.DDG_AVAILABLE else 'DISABLED'}")
log.info(f"Image enh : {'enabled' if it.PIL_AVAILABLE else 'DISABLED'}")
log.info("✅ Friday AI Bot starting...")

bot.infinity_polling(timeout=30, long_polling_timeout=10)

