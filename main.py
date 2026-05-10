import os
import sys
import uuid
import html
import time
import sqlite3
import logging
import threading
from flask import Flask
from threading import Thread
from datetime import datetime, date
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor

import telebot
from telebot import types
from google import genai
from PIL import Image

try:
    from duckduckgo_search import DDGS
    DDG_AVAILABLE = True
except ImportError:
    DDG_AVAILABLE = False

# ==================================================
# LOGGING SETUP
# ==================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(__name__)

# ==================================================
# CONFIG
# ==================================================

BOT_TOKEN    = os.environ.get("BOT_TOKEN")
MY_ID        = int(os.environ.get("ADMIN_ID", "8234592104"))
LOG_GROUP_ID = int(os.environ.get("LOG_GROUP_ID", "-1003848412289"))
DB_PATH      = "bot_data.db"
MODEL_NAME   = "gemini-flash-lite-latest"

MAX_REQ_PER_MIN   = 9
SESSION_MAX_PAIRS = 10   # last 10 exchanges kept in memory
THREAD_WORKERS    = 10

GEMINI_KEYS = [k for k in [
    os.environ.get("GEMINI_KEY_1"),
    os.environ.get("GEMINI_KEY_2"),
    os.environ.get("GEMINI_KEY_3"),
] if k]

if not BOT_TOKEN:
    log.error("BOT_TOKEN পাওয়া যায়নি!"); sys.exit(1)
if not GEMINI_KEYS:
    log.error("কোনো Gemini key পাওয়া যায়নি!"); sys.exit(1)

bot      = telebot.TeleBot(BOT_TOKEN, parse_mode=None)
executor = ThreadPoolExecutor(max_workers=THREAD_WORKERS)

# ==================================================
# NSFW / MALICIOUS FILTER
# ==================================================

NSFW_WORDS = {
    "porn", "xxx", "nude", "naked", "sex video", "adult content",
    "hentai", "onlyfans", "escort", "hack", "crack", "warez",
    "torrent piracy", "malware", "exploit", "ransomware", "phishing",
}

def is_safe_result(title: str, snippet: str, url: str) -> bool:
    combined = f"{title} {snippet} {url}".lower()
    return not any(w in combined for w in NSFW_WORDS)

# ==================================================
# DATABASE
# ==================================================

_db_lock = threading.Lock()

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

def set_user_field(user_id: int, field: str, value):
    allowed = {"is_banned","is_premium","daily_limit","custom_info","policy"}
    if field not in allowed:
        return
    with _db_lock, get_db() as conn:
        conn.execute(f"UPDATE users SET {field}=? WHERE user_id=?", (value, user_id))
        conn.commit()

# ==================================================
# RATE LIMITING  (9 req / 60s per user)
# ==================================================

_rate_data: dict[int, deque] = defaultdict(lambda: deque())
_rate_lock = threading.Lock()

def is_rate_limited(user_id: int) -> bool:
    now = time.time()
    with _rate_lock:
        dq = _rate_data[user_id]
        while dq and now - dq[0] > 60:
            dq.popleft()
        if len(dq) >= MAX_REQ_PER_MIN:
            return True
        dq.append(now)
        return False

# ==================================================
# SESSION MEMORY
# ==================================================

_sessions: dict[int, list] = defaultdict(list)
_session_lock = threading.Lock()

def add_to_session(user_id: int, role: str, content: str):
    with _session_lock:
        session = _sessions[user_id]
        session.append({"role": role, "content": content})
        # Keep only last SESSION_MAX_PAIRS * 2 items
        if len(session) > SESSION_MAX_PAIRS * 2:
            _sessions[user_id] = session[-(SESSION_MAX_PAIRS * 2):]

def build_context_prompt(user_id: int, new_message: str, user_data: dict) -> str:
    with _session_lock:
        history = list(_sessions[user_id])

    context_lines = []
    for msg in history:
        role = "User" if msg["role"] == "user" else "Assistant"
        context_lines.append(f"{role}: {msg['content']}")

    policy = user_data.get("policy", "").strip()
    custom_info = user_data.get("custom_info", "").strip()

    system_parts = ["তুমি একটি বুদ্ধিমান AI assistant। বাংলা ও ইংরেজি উভয়ে উত্তর দিতে পারো।"]
    if policy:
        system_parts.append(f"Policy: {policy}")
    if custom_info:
        system_parts.append(f"User Info: {custom_info}")

    system_prompt = " | ".join(system_parts)

    if context_lines:
        history_str = "\n".join(context_lines[-SESSION_MAX_PAIRS * 2:])
        return f"{system_prompt}\n\nConversation so far:\n{history_str}\n\nUser: {new_message}\nAssistant:"
    else:
        return f"{system_prompt}\n\nUser: {new_message}\nAssistant:"

# ==================================================
# GEMINI AI
# ==================================================

_key_index = 0
_key_lock  = threading.Lock()

def generate_ai_response(prompt: str, image=None) -> str:
    global _key_index
    total = len(GEMINI_KEYS)
    for _ in range(total):
        with _key_lock:
            idx = _key_index
            api_key = GEMINI_KEYS[idx]
        try:
            client = genai.Client(api_key=api_key)
            contents = [prompt, image] if image else prompt
            resp = client.models.generate_content(model=MODEL_NAME, contents=contents)
            text = resp.text.strip() if resp.text else None
            if text:
                return text
        except Exception as e:
            log.warning(f"Gemini key {idx+1} failed: {e}")
            with _key_lock:
                _key_index = (_key_index + 1) % total
    return "দুঃখিত, AI সার্ভিস এখন unavailable। একটু পরে চেষ্টা করুন।"

# ==================================================
# DUCKDUCKGO HYBRID SEARCH + AI SUMMARIZATION
# ==================================================

def web_search_and_summarize(query: str) -> str:
    if not DDG_AVAILABLE:
        return generate_ai_response(query)

    try:
        with DDGS() as ddgs:
            raw_results = list(ddgs.text(query, max_results=8))
    except Exception as e:
        log.warning(f"DuckDuckGo search failed: {e}")
        return generate_ai_response(query)

    safe_results = [
        r for r in raw_results
        if is_safe_result(r.get("title",""), r.get("body",""), r.get("href",""))
    ]

    if not safe_results:
        return generate_ai_response(query)

    snippets = ""
    for i, r in enumerate(safe_results[:5], 1):
        title   = r.get("title", "")[:120]
        body    = r.get("body",  "")[:200]
        snippets += f"{i}. {title}\n   {body}\n\n"

    rag_prompt = (
        f"নিচের web search results ব্যবহার করে প্রশ্নের উত্তর দাও। "
        f"NSFW বা ক্ষতিকর তথ্য বাদ দাও। বাংলা বা ইংরেজিতে সংক্ষিপ্ত ও নির্ভুল উত্তর দাও।\n\n"
        f"Search Results:\n{snippets}\n"
        f"Question: {query}"
    )
    return generate_ai_response(rag_prompt)

# ==================================================
# FORUM TOPIC LOGGING
# ==================================================

_topic_creation_lock = threading.Lock()

def get_or_create_topic(user_id: int, user_name: str) -> int | None:
    user = get_user(user_id)
    if user and user["topic_id"]:
        return user["topic_id"]

    with _topic_creation_lock:
        # Re-check after acquiring lock
        user = get_user(user_id)
        if user and user["topic_id"]:
            return user["topic_id"]
        try:
            topic = bot.create_forum_topic(
                LOG_GROUP_ID,
                f"👤 {user_name} | {user_id}"
            )
            tid = topic.message_thread_id
            set_topic_id(user_id, tid)
            log.info(f"Created topic {tid} for user {user_id}")
            return tid
        except Exception as e:
            log.warning(f"Forum topic creation failed: {e}")
            return None

def eh(text: str) -> str:
    """Escape HTML for safe Telegram HTML messages."""
    return html.escape(str(text))

def send_log(user_id: int, user_name: str, text: str):
    """Send log message to user's forum topic (fallback: plain group message)."""
    topic_id = get_or_create_topic(user_id, user_name)
    try:
        if topic_id:
            bot.send_message(
                LOG_GROUP_ID,
                text,
                message_thread_id=topic_id,
                parse_mode="HTML"
            )
        else:
            bot.send_message(
                LOG_GROUP_ID,
                f"<b>👤 {eh(user_name)} ({user_id})</b>\n\n{text}",
                parse_mode="HTML"
            )
    except Exception as e:
        log.error(f"Log send failed: {e}")

def send_log_copy(user_id: int, user_name: str, chat_id: int, message_id: int):
    """Copy a message into the user's log topic."""
    topic_id = get_or_create_topic(user_id, user_name)
    try:
        if topic_id:
            bot.copy_message(LOG_GROUP_ID, chat_id, message_id, message_thread_id=topic_id)
        else:
            bot.copy_message(LOG_GROUP_ID, chat_id, message_id)
    except Exception as e:
        log.error(f"Log copy failed: {e}")

# ==================================================
# REACT TO MESSAGE
# ==================================================

def react_thumbs_up(chat_id: int, message_id: int):
    try:
        bot.set_message_reaction(
            chat_id, message_id,
            reaction=[types.ReactionTypeEmoji(emoji="👍")]
        )
    except Exception:
        pass  # Reaction not supported or failed silently

# ==================================================
# COMMON GUARD (ban + daily limit check)
# ==================================================

def check_user_access(message) -> bool:
    """Returns True if user can proceed. Replies with error if not."""
    user_id   = message.from_user.id
    user_name = message.from_user.first_name or "User"

    upsert_user(user_id, user_name)
    reset_daily_if_needed(user_id)
    user = get_user(user_id)

    if user and user["is_banned"]:
        bot.reply_to(message, "⛔ আপনি এই বট ব্যবহার থেকে নিষিদ্ধ।")
        return False

    if is_rate_limited(user_id):
        bot.reply_to(
            message,
            f"⚠️ আপনি প্রতি মিনিটে সর্বোচ্চ {MAX_REQ_PER_MIN}টি request করতে পারবেন। "
            f"একটু অপেক্ষা করুন।"
        )
        return False

    if user and user["daily_count"] >= user["daily_limit"]:
        bot.reply_to(
            message,
            f"📊 আজকের limit ({user['daily_limit']}) শেষ। আগামীকাল আবার চেষ্টা করুন।"
        )
        return False

    return True

# ==================================================
# HANDLERS — /start
# ==================================================

@bot.message_handler(commands=["start"])
def cmd_start(message):
    if message.chat.type != "private":
        return

    user_id   = message.from_user.id
    user_name = message.from_user.first_name or "User"
    upsert_user(user_id, user_name)

    text = (
        f"হ্যালো <b>{eh(user_name)}</b>! 👋\n\n"
        "আমি একটি Advanced AI Assistant Bot।\n\n"
        "<b>Commands:</b>\n"
        "🔍 /search ‹keyword› — Smart Web Search\n"
        "🎬 /yt ‹keyword› — YouTube Search\n"
        "🖼 /image ‹keyword› — Image Search\n"
        "🔁 /clear — কথোপকথন মুছুন\n\n"
        "সরাসরি যেকোনো প্রশ্ন করুন অথবা ছবি পাঠান!"
    )
    bot.reply_to(message, text, parse_mode="HTML")
    send_log(user_id, user_name, f"🚀 <b>New /start</b>\n👤 <b>{eh(user_name)}</b>\n🆔 <code>{user_id}</code>")

# ==================================================
# HANDLERS — /clear session
# ==================================================

@bot.message_handler(commands=["clear"])
def cmd_clear(message):
    if message.chat.type != "private":
        return
    user_id = message.from_user.id
    with _session_lock:
        _sessions[user_id].clear()
    bot.reply_to(message, "✅ কথোপকথনের ইতিহাস মুছে গেছে।")

# ==================================================
# HANDLERS — /search (hybrid RAG)
# ==================================================

@bot.message_handler(commands=["search"])
def cmd_search(message):
    if message.chat.type != "private":
        return
    if not check_user_access(message):
        return

    query = message.text.replace("/search", "", 1).strip()
    if not query:
        bot.reply_to(message, "ব্যবহার: /search ‹keyword›")
        return

    react_thumbs_up(message.chat.id, message.message_id)
    executor.submit(_do_search, message, query)

def _do_search(message, query: str):
    user_id   = message.from_user.id
    user_name = message.from_user.first_name or "User"
    bot.send_chat_action(message.chat.id, "typing")
    try:
        answer = web_search_and_summarize(query)
        increment_daily_count(user_id)
        bot.reply_to(message, answer)
        send_log(user_id, user_name,
                 f"🔍 <b>Search</b>\n<b>Query:</b> {eh(query)}\n<b>Reply:</b> {eh(answer[:400])}")
    except Exception as e:
        log.error(f"Search handler error: {e}")
        bot.reply_to(message, "Search করতে সমস্যা হয়েছে।")

# ==================================================
# HANDLERS — /yt YouTube search
# ==================================================

@bot.message_handler(commands=["yt"])
def cmd_yt(message):
    if message.chat.type != "private":
        return
    if not check_user_access(message):
        return

    query = message.text.replace("/yt", "", 1).strip()
    if not query:
        bot.reply_to(message, "ব্যবহার: /yt ‹keyword›")
        return

    react_thumbs_up(message.chat.id, message.message_id)
    executor.submit(_do_yt_search, message, query)

def _do_yt_search(message, query: str):
    user_id   = message.from_user.id
    user_name = message.from_user.first_name or "User"
    bot.send_chat_action(message.chat.id, "typing")
    try:
        if DDG_AVAILABLE:
            with DDGS() as ddgs:
                videos = list(ddgs.videos(query, max_results=5))
            if videos:
                text = "🎬 <b>YouTube / Video Results</b>\n\n"
                for v in videos[:5]:
                    title    = eh(v.get("title", "No Title")[:100])
                    embed_url = eh(v.get("content", v.get("embed_url", "")))
                    text += f"• <b>{title}</b>\n{embed_url}\n\n"
                bot.reply_to(message, text, parse_mode="HTML")
                return
        bot.reply_to(message, "কোনো ভিডিও পাওয়া যায়নি।")
        send_log(user_id, user_name, f"🎬 <b>YT Search</b>: {eh(query)}")
    except Exception as e:
        log.error(f"YT search error: {e}")
        bot.reply_to(message, "YouTube Search Error")

# ==================================================
# HANDLERS — /image Image search
# ==================================================

@bot.message_handler(commands=["image"])
def cmd_image(message):
    if message.chat.type != "private":
        return
    if not check_user_access(message):
        return

    query = message.text.replace("/image", "", 1).strip()
    if not query:
        bot.reply_to(message, "ব্যবহার: /image ‹keyword›")
        return

    react_thumbs_up(message.chat.id, message.message_id)
    executor.submit(_do_image_search, message, query)

def _do_image_search(message, query: str):
    user_id   = message.from_user.id
    user_name = message.from_user.first_name or "User"
    bot.send_chat_action(message.chat.id, "upload_photo")
    try:
        if DDG_AVAILABLE:
            with DDGS() as ddgs:
                imgs = list(ddgs.images(query, max_results=10, safesearch="moderate"))
            safe_imgs = [i for i in imgs if is_safe_result(i.get("title",""), "", i.get("image",""))]
            if safe_imgs:
                img_url = safe_imgs[0].get("image")
                bot.send_photo(message.chat.id, img_url, caption=f"🖼 {query}")
                return
        bot.reply_to(message, "কোনো ছবি পাওয়া যায়নি।")
        send_log(user_id, user_name, f"🖼 <b>Image Search</b>: {eh(query)}")
    except Exception as e:
        log.error(f"Image search error: {e}")
        bot.reply_to(message, "Image Search Error")

# ==================================================
# HANDLERS — Photo (AI Vision)
# ==================================================

@bot.message_handler(content_types=["photo"])
def handle_photo(message):
    if message.chat.type != "private":
        return
    if not check_user_access(message):
        return

    react_thumbs_up(message.chat.id, message.message_id)
    executor.submit(_do_photo_analysis, message)

def _do_photo_analysis(message):
    user_id   = message.from_user.id
    user_name = message.from_user.first_name or "User"
    filename  = None
    try:
        bot.send_chat_action(message.chat.id, "typing")
        filename = f"/tmp/{uuid.uuid4()}.jpg"
        file_info  = bot.get_file(message.photo[-1].file_id)
        downloaded = bot.download_file(file_info.file_path)
        with open(filename, "wb") as f:
            f.write(downloaded)

        img     = Image.open(filename)
        caption = message.caption or "এই ছবিটি বিশ্লেষণ করো এবং বিস্তারিত বলো।"
        reply   = generate_ai_response(caption, image=img)
        increment_daily_count(user_id)

        bot.reply_to(message, reply)
        send_log_copy(user_id, user_name, message.chat.id, message.message_id)
        send_log(user_id, user_name, f"🤖 <b>Image Reply</b>\n{eh(reply[:400])}")

    except Exception as e:
        log.error(f"Photo analysis error: {e}")
        bot.reply_to(message, "ছবিটি বিশ্লেষণ করা যায়নি।")
    finally:
        if filename and os.path.exists(filename):
            os.remove(filename)

# ==================================================
# HANDLERS — AI Text Chat (private only)
# ==================================================

@bot.message_handler(content_types=["text"])
def handle_text(message):
    # Groups: only admin commands (handled separately)
    if message.chat.type != "private":
        return

    # Ignore unknown commands
    if message.text.startswith("/"):
        return

    if not check_user_access(message):
        return

    react_thumbs_up(message.chat.id, message.message_id)
    executor.submit(_do_ai_chat, message)

def _do_ai_chat(message):
    user_id   = message.from_user.id
    user_name = message.from_user.first_name or "User"
    user_text = message.text

    try:
        bot.send_chat_action(message.chat.id, "typing")
        user_data = get_user(user_id) or {}
        prompt    = build_context_prompt(user_id, user_text, user_data)
        reply     = generate_ai_response(prompt)

        add_to_session(user_id, "user",      user_text)
        add_to_session(user_id, "assistant", reply)
        increment_daily_count(user_id)

        bot.reply_to(message, reply)
        send_log(user_id, user_name,
                 f"📩 <b>Message</b>\n{eh(user_text)}\n\n🤖 <b>Reply</b>\n{eh(reply[:400])}")

    except Exception as e:
        log.error(f"AI chat error: {e}")
        bot.reply_to(message, "দুঃখিত, সমস্যা হয়েছে। আবার চেষ্টা করুন।")

# ==================================================
# ADMIN COMMANDS  (from topic or with user_id arg)
# ==================================================

def resolve_target(message) -> int | None:
    """Find target user_id: from topic or from command arg."""
    # From topic
    if message.message_thread_id:
        uid = get_user_by_topic(message.message_thread_id)
        if uid:
            return uid

    # From arg: /ban 12345
    parts = message.text.split()
    if len(parts) >= 2:
        try:
            return int(parts[1])
        except ValueError:
            pass
    return None

def admin_only(func):
    import functools
    @functools.wraps(func)
    def wrapper(message):
        if message.from_user.id != MY_ID:
            return
        return func(message)
    return wrapper

@bot.message_handler(commands=["ban"])
@admin_only
def cmd_ban(message):
    uid = resolve_target(message)
    if not uid:
        bot.reply_to(message, "Usage: /ban ‹user_id›  or use from user topic"); return
    set_user_field(uid, "is_banned", 1)
    bot.reply_to(message, f"✅ User <code>{uid}</code> banned.", parse_mode="HTML")

@bot.message_handler(commands=["unban"])
@admin_only
def cmd_unban(message):
    uid = resolve_target(message)
    if not uid:
        bot.reply_to(message, "Usage: /unban ‹user_id›"); return
    set_user_field(uid, "is_banned", 0)
    bot.reply_to(message, f"✅ User <code>{uid}</code> unbanned.", parse_mode="HTML")

@bot.message_handler(commands=["premium"])
@admin_only
def cmd_premium(message):
    uid = resolve_target(message)
    if not uid:
        bot.reply_to(message, "Usage: /premium ‹user_id›"); return
    set_user_field(uid, "is_premium", 1)
    set_user_field(uid, "daily_limit", 500)
    bot.reply_to(message, f"⭐ User <code>{uid}</code> is now Premium.", parse_mode="HTML")

@bot.message_handler(commands=["limit"])
@admin_only
def cmd_limit(message):
    uid   = resolve_target(message)
    parts = message.text.split()
    # /limit [user_id] <number>
    try:
        new_limit = int(parts[-1])
    except (ValueError, IndexError):
        bot.reply_to(message, "Usage: /limit ‹user_id› ‹number›  e.g. /limit 12345 100"); return
    if not uid:
        bot.reply_to(message, "Usage: /limit ‹user_id› ‹number›"); return
    set_user_field(uid, "daily_limit", new_limit)
    bot.reply_to(message, f"✅ Limit for <code>{uid}</code> set to <b>{new_limit}</b>.", parse_mode="HTML")

@bot.message_handler(commands=["add_info"])
@admin_only
def cmd_add_info(message):
    uid  = resolve_target(message)
    text = message.text.split(None, 2)
    info = text[2].strip() if len(text) >= 3 else (text[1].strip() if len(text) == 2 else "")
    if not uid or not info:
        bot.reply_to(message, "Usage: /add_info ‹user_id› ‹info text›"); return
    set_user_field(uid, "custom_info", info)
    bot.reply_to(message, f"✅ Info set for <code>{uid}</code>.", parse_mode="HTML")

@bot.message_handler(commands=["set_policy"])
@admin_only
def cmd_set_policy(message):
    uid  = resolve_target(message)
    text = message.text.split(None, 2)
    policy = text[2].strip() if len(text) >= 3 else (text[1].strip() if len(text) == 2 else "")
    if not uid or not policy:
        bot.reply_to(message, "Usage: /set_policy ‹user_id› ‹policy text›"); return
    set_user_field(uid, "policy", policy)
    bot.reply_to(message, f"✅ Policy set for <code>{uid}</code>.", parse_mode="HTML")

# ==================================================
# 24H DATABASE BACKUP
# ==================================================

def _do_backup():
    try:
        with open(DB_PATH, "rb") as f:
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            bot.send_document(
                LOG_GROUP_ID, f,
                caption=f"📦 <b>Auto DB Backup</b> — {now}",
                parse_mode="HTML"
            )
        log.info("DB backup sent successfully")
    except Exception as e:
        log.error(f"Backup failed: {e}")

def _backup_loop():
    while True:
        time.sleep(86400)
        _do_backup()

# 

# STARTUP

# ==================================================

init_db()

backup_thread = threading.Thread(target=_backup_loop, daemon=True)
backup_thread.start()
log.info("Backup scheduler started (24h interval)")

log.info(f"Gemini keys loaded: {len(GEMINI_KEYS)}")
log.info(f"DuckDuckGo search: {'enabled' if DDG_AVAILABLE else 'disabled'}")

# =========================================
# KEEP ALIVE SYSTEM
# =========================================

FLASK_PORT = int(os.environ.get("PORT", 5000))

app = Flask(__name__)

@app.route('/')
def home():
    return "✅ Friday AI Bot is alive!", 200

@app.route('/health')
def health():
    return {"status": "ok", "bot": "running"}, 200

def _run_flask():
    import logging as _lg
    _lg.getLogger("werkzeug").setLevel(_lg.ERROR)
    app.run(host="0.0.0.0", port=FLASK_PORT)

flask_thread = Thread(target=_run_flask, daemon=True)
flask_thread.start()
log.info(f"Flask keep-alive running on port {FLASK_PORT}")

log.info("✅ Bot starting...")

bot.infinity_polling(timeout=30, long_polling_timeout=10)
log.info("❌ Bot stopped")