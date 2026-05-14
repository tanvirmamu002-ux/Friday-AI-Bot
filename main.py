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
ai.init_gemini(GEMINI_KEYS)
ai.reload_configs()

# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════


def eh(text: str) -> str:
    return html.escape(str(text))


def is_admin(user_id: int) -> bool:
    return user_id == MY_ID


def _user_role(user_data: dict | None, user_id: int) -> str:
    if is_admin(user_id):
        return "admin"
    if not user_data:
        return "user"
    if user_data.get("is_banned"):
        return "banned"
    if user_data.get("is_premium"):
        return "premium"
    return "user"


# ── Rate limiter ───────────────────────────────────────────────────────────────

_rate_data: dict[int, deque] = defaultdict(lambda: deque())
_rate_lock = threading.Lock()


def is_rate_limited(user_id: int) -> bool:
    if is_admin(user_id):
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
    uid = message.from_user.id
    uname = message.from_user.first_name or "User"

    if is_admin(uid):
        return True

    db.upsert_user(uid, uname)
    db.reset_daily_if_needed(uid)
    user = db.get_user(uid)

    if user and user["is_banned"]:
        bot.reply_to(message, "🚫 আপনি banned আছেন।")
        return False

    if is_rate_limited(uid):
        bot.reply_to(
            message,
            f"⏳ প্রতি মিনিটে সর্বোচ্চ {MAX_REQ_PER_MIN}টি request। একটু অপেক্ষা করুন।",
        )
        return False

    # Daily limit tracking only — enforcement disabled (uncomment to enable):
    # if user and user["daily_count"] >= user["daily_limit"]:
    #     bot.reply_to(message, f"📊 আজকের limit শেষ।")
    #     return False

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


def _send_chunks(
    chat_id: int, text: str, thread_id: int | None, parse_mode: str = "HTML"
):
    for i in range(0, max(len(text), 1), TG_MAX_LEN):
        chunk = text[i : i + TG_MAX_LEN]
        try:
            if thread_id:
                bot.send_message(
                    chat_id, chunk, message_thread_id=thread_id, parse_mode=parse_mode
                )
            else:
                bot.send_message(chat_id, chunk, parse_mode=parse_mode)
        except Exception as e:
            log.warning(f"Chunk send failed: {e}")


def get_or_create_topic(user_id: int, user_name: str) -> int | None:
    user = db.get_user(user_id)
    if user and user["topic_id"]:
        return user["topic_id"]
    with _topic_lock:
        user = db.get_user(user_id)
        if user and user["topic_id"]:
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
    try:
        tid = get_or_create_topic(user_id, user_name)
        if tid:
            _send_chunks(LOG_GROUP_ID, text, tid)
        else:
            _send_chunks(
                LOG_GROUP_ID, f"<b>👤 {eh(user_name)} ({user_id})</b>\n\n{text}", None
            )
    except Exception as e:
        log.warning(f"send_log error: {e}")


def send_log_copy(user_id: int, user_name: str, chat_id: int, message_id: int):
    try:
        tid = get_or_create_topic(user_id, user_name)
        if tid:
            bot.forward_message(
                LOG_GROUP_ID, chat_id, message_id, message_thread_id=tid
            )
    except Exception as e:
        log.debug(f"Forward to topic failed: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# SEARCH CONTEXT HELPER
# ══════════════════════════════════════════════════════════════════════════════


def _get_search_context(query: str) -> str | None:
    if not se.DDG_AVAILABLE:
        return None
    try:
        from duckduckgo_search import DDGS

        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=8))
        safe = [
            r
            for r in results
            if se.is_safe_result(
                r.get("title", ""), r.get("body", ""), r.get("href", "")
            )
        ]
        if not safe:
            return None
        snippets = ""
        for i, r in enumerate(safe[:6], 1):
            title = r.get("title", "")[:150]
            body = r.get("body", "")[:300]
            href = r.get("href", "")[:100]
            snippets += f"{i}. [{title}]({href})\n   {body}\n\n"
        log.info(f"Search context: {len(safe)} results for '{query[:50]}'")
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
        if not is_admin(message.from_user.id):
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


# ══════════════════════════════════════════════════════════════════════════════
# COMMANDS — PUBLIC
# ══════════════════════════════════════════════════════════════════════════════

# ── /start ─────────────────────────────────────────────────────────────────────


@bot.message_handler(commands=["start"])
def cmd_start(message):
    if message.chat.type != "private":
        return
    uid = message.from_user.id
    uname = message.from_user.first_name or "User"
    db.upsert_user(uid, uname)

    text = (
        f"হ্যালো <b>{eh(uname)}</b>! 👋\n\n"
        "আমি <b>Friday AI</b> — Advanced AI Assistant\n"
        "নির্মাতা: <b>বোরহান</b> (<a href='https://t.me/hm_burhan'>@hm_burhan</a>)\n\n"
        "<b>📋 Commands:</b>\n"
        "🔍 /search ‹query› — Real-time Web Search\n"
        "🎬 /yt ‹query›     — YouTube / Video\n"
        "🖼 /image ‹query›  — Image Search\n"
        "✨ /enhance         — Photo Enhancement\n"
        "📊 /status          — Account Info\n"
        "❓ /help            — All Commands\n"
        "🔁 /clear           — Reset Chat Memory\n\n"
        "সরাসরি যেকোনো প্রশ্ন করুন অথবা ছবি পাঠান! 🤖"
    )
    bot.reply_to(message, text, parse_mode="HTML")
    send_log(
        uid,
        uname,
        f"🚀 <b>New /start</b>\n👤 <b>{eh(uname)}</b>\n🆔 <code>{uid}</code>",
    )


# ── /help ──────────────────────────────────────────────────────────────────────


@bot.message_handler(commands=["help"])
def cmd_help(message):
    if message.chat.type != "private":
        return
    uid = message.from_user.id

    public_help = (
        "❓ <b>Friday AI — Help</b>\n"
        "নির্মাতা: বোরহান (@hm_burhan)\n\n"
        "<b>💬 Chat:</b>\n"
        "  যেকোনো প্রশ্ন সরাসরি পাঠান — AI উত্তর দেবে\n"
        "  Current events/news স্বয়ংক্রিয়ভাবে web search করে উত্তর দেয়\n\n"
        "<b>🔍 Search:</b>\n"
        "  /search ‹keyword›  — Real-time web search\n"
        "  /image ‹keyword›   — Image search\n"
        "  /yt ‹keyword›      — YouTube/video search\n\n"
        "<b>🖼 Image:</b>\n"
        "  ছবি পাঠান → AI বিশ্লেষণ করবে\n"
        "  /enhance → ছবি পাঠান → enhanced ছবি পাবেন\n\n"
        "<b>📊 Account:</b>\n"
        "  /status  — আপনার account status ও usage\n"
        "  /clear   — কথোপকথনের memory মুছুন\n\n"
        "<b>ℹ️ About:</b>\n"
        "  Engine: Gemini AI + DuckDuckGo Search\n"
        "  Powered by Friday AI | Made by @hm_burhan"
    )

    admin_extra = (
        "\n\n<b>👑 Admin Commands:</b>\n"
        "  /ban [id]           — User ban\n"
        "  /unban [id]         — User unban\n"
        "  /premium [id]       — Premium grant\n"
        "  /limit [id] ‹n›     — Daily limit set\n"
        "  /add_info [id] ‹t›  — Custom info set\n"
        "  /set_policy [id] ‹t› — Policy set\n"
        "  /broadcast ‹text›   — Message all users\n"
        "  /reload             — Reload config files\n"
        "  /stats              — Bot statistics"
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
            "👑 <b>Admin Account</b>\n\n"
            "✅ Unlimited usage\n"
            "✅ No rate limits\n"
            "✅ All commands enabled\n\n"
            f"<i>Friday AI | @hm_burhan</i>",
            parse_mode="HTML",
        )
        return

    db.upsert_user(uid, uname)
    db.reset_daily_if_needed(uid)
    user = db.get_user(uid)
    if not user:
        bot.reply_to(message, "তথ্য পাওয়া যায়নি।")
        return

    remaining = max(20, user["daily_limit"] - user["daily_count"])
    premium_badge = "⭐ Premium" if user["is_premium"] else "🆓 Free"
    banned_badge = "🚫 Banned" if user["is_banned"] else "✅ Active"
    session_msgs = ai.session_length(uid)

    text = (
        f"📊 <b>Account Status</b>\n\n"
        f"👤 Name  : <b>{eh(uname)}</b>\n"
        f"🆔 ID    : <code>{uid}</code>\n"
        f"🏷 Plan  : {premium_badge}\n"
        f"🔰 Status: {banned_badge}\n\n"
        f"📈 আজকের ব্যবহার : <b>{user['daily_count']}</b> / {user['daily_limit']}\n"
        f"✨ বাকি requests  : <b>{remaining}</b>\n"
        f"💬 Session memory : <b>{session_msgs}</b> messages\n\n"
        f"<i>Powered by Friday AI | Made by @hm_burhan</i>"
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
        "⚙️ <b>Chat Settings</b>\n\n"
        + (f"✅ Enabled: {', '.join(enabled)}\n" if enabled else "")
        + (f"🚫 Disabled: {', '.join(disabled)}\n" if disabled else "")
        + per_user_note
        + "\n\n<i>Friday AI | @hm_burhan</i>"
    )
    bot.reply_to(message, text, parse_mode="HTML")


# ── /clear ─────────────────────────────────────────────────────────────────────


@bot.message_handler(commands=["clear"])
def cmd_clear(message):
    if message.chat.type != "private":
        return
    ai.clear_session(message.from_user.id)
    bot.reply_to(message, "✅ কথোপকথনের ইতিহাস মুছে গেছে।")


# ── /search ────────────────────────────────────────────────────────────────────


@bot.message_handler(commands=["search"])
def cmd_search(message):
    if message.chat.type != "private":
        return
    if not check_user_access(message):
        return
    query = _arg(message, 1)
    if not query:
        bot.reply_to(message, "ব্যবহার: /search ‹keyword›")
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
        bot.reply_to(message, answer)
        send_log(uid, uname, f"🔍 <b>Search</b>\n{eh(query)}\n\n💬 {eh(answer[:300])}")
    except Exception as e:
        log.error(f"Search error: {e}")
        bot.reply_to(message, "🔍 Search করতে সমস্যা হয়েছে। আবার চেষ্টা করুন।")


# ── /yt ────────────────────────────────────────────────────────────────────────


@bot.message_handler(commands=["yt"])
def cmd_yt(message):
    if message.chat.type != "private":
        return
    if not check_user_access(message):
        return
    query = _arg(message, 1)
    if not query:
        bot.reply_to(message, "ব্যবহার: /yt ‹keyword›")
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
            text = "🎬 <b>Video Results</b>\n\n"
            for v in videos[:5]:
                title = eh((v.get("title") or "No Title")[:100])
                url = eh(v.get("content") or v.get("embed_url", ""))
                text += f"• <b>{title}</b>\n{url}\n\n"
            bot.reply_to(message, text, parse_mode="HTML")
        else:
            bot.reply_to(message, "কোনো ভিডিও পাওয়া যায়নি।")
        send_log(uid, uname, f"🎬 <b>YT Search</b>: {eh(query)}")
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
        bot.reply_to(message, "ব্যবহার: /image ‹keyword›")
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
            bot.reply_to(message, f"🖼 '{eh(query)}' এর জন্য কোনো ছবি পাওয়া যায়নি।")
            return
        sent = it.send_photo_safe(
            bot,
            message.chat.id,
            results,
            caption=f"🖼 {query}",
            reply_to=message.message_id,
        )
        if not sent:
            bot.reply_to(message, "🖼 ছবি পাঠাতে সমস্যা হয়েছে। আবার চেষ্টা করুন।")
            return
        db.increment_daily_count(uid)
        send_log(uid, uname, f"🖼 <b>Image</b>: {eh(query)}")
    except Exception as e:
        log.error(f"Image error: {e}")
        bot.reply_to(message, "🖼 Image search error। আবার চেষ্টা করুন।")


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
    bot.reply_to(message, "✨ এখন একটি ছবি পাঠান — sharpen, denoise ও upscale করা হবে।")


# ══════════════════════════════════════════════════════════════════════════════
# COMMANDS — ADMIN
# ═══════════════════════>V�══════════════════════════════════════════════════════


@bot.message_handler(commands=["reload"])
@admin_only
def cmd_reload(message):
    cfg = ai.reload_configs()
    loaded = sum(1 for v in cfg.values() if v)
    bot.reply_to(message, f"✅ Config reloaded! {loaded}/{len(cfg)} files loaded.")
    log.info("Config reloaded by admin")


@bot.message_handler(commands=["stats"])
@admin_only
def cmd_stats(message):
    try:
        all_ids = db.get_all_user_ids()
        total = len(all_ids)
        bot.reply_to(
            message,
            f"📊 <b>Bot Statistics</b>\n\n"
            f"👥 Total users: <b>{total}</b>\n"
            f"🤖 Bot: Friday AI\n"
            f"🔑 Gemini keys: 3\n"
            f"<i>@hm_burhan</i>",
            parse_mode="HTML",
        )
    except Exception as e:
        bot.reply_to(message, f"Stats error: {e}")


@bot.message_handler(commands=["ban"])
@admin_only
def cmd_ban(message):
    uid = resolve_target(message)
    if not uid:
        bot.reply_to(message, "Usage: /ban ‹user_id›  or run from user's topic")
        return
    db.set_user_field(uid, "is_banned", 1)
    bot.reply_to(message, f"✅ User <code>{uid}</code> banned.", parse_mode="HTML")
    log.info(f"Admin banned user {uid}")


@bot.message_handler(commands=["unban"])
@admin_only
def cmd_unban(message):
    uid = resolve_target(message)
    if not uid:
        bot.reply_to(message, "Usage: /unban ‹user_id›")
        return
    db.set_user_field(uid, "is_banned", 0)
    bot.reply_to(message, f"✅ User <code>{uid}</code> unbanned.", parse_mode="HTML")


@bot.message_handler(commands=["premium"])
@admin_only
def cmd_premium(message):
    uid = resolve_target(message)
    if not uid:
        bot.reply_to(
            message,
            "Usage: /premium ‹user_id›\n"
            "Or run this command inside the user's forum topic.",
        )
        return
    db.set_user_field(uid, "is_premium", 1)
    db.set_user_field(uid, "daily_limit", 500)
    bot.reply_to(
        message,
        f"⭐ User <code>{uid}</code> is now <b>Premium</b> (500 requests/day).",
        parse_mode="HTML",
    )
    log.info(f"Admin granted premium to {uid}")


@bot.message_handler(commands=["limit"])
@admin_only
def cmd_limit(message):
    uid = resolve_target(message)
    parts = message.text.split()
    try:
        new_limit = int(parts[-1])
        if new_limit < 0:
            raise ValueError
    except (ValueError, IndexError):
        bot.reply_to(
            message, "Usage: /limit ‹user_id› ‹number›\nExample: /limit 12345 200"
        )
        return
    if not uid:
        bot.reply_to(message, "Usage: /limit ‹user_id› ‹number›")
        return
    db.set_user_field(uid, "daily_limit", new_limit)
    bot.reply_to(
        message,
        f"✅ Daily limit for <code>{uid}</code> set to <b>{new_limit}</b>.",
        parse_mode="HTML",
    )


@bot.message_handler(commands=["add_info"])
@admin_only
def cmd_add_info(message):
    uid = resolve_target(message)
    text = message.text.split(None, 2)
    info = (
        text[2].strip()
        if len(text) >= 3
        else (text[1].strip() if len(text) == 2 else "")
    )
    if not uid or not info:
        bot.reply_to(message, "Usage: /add_info ‹user_id› ‹info text›")
        return
    db.set_user_field(uid, "custom_info", info)
    bot.reply_to(
        message, f"✅ Custom info set for <code>{uid}</code>.", parse_mode="HTML"
    )


@bot.message_handler(commands=["set_policy"])
@admin_only
def cmd_set_policy(message):
    uid = resolve_target(message)
    text = message.text.split(None, 2)
    pol = (
        text[2].strip()
        if len(text) >= 3
        else (text[1].strip() if len(text) == 2 else "")
    )
    if not uid or not pol:
        bot.reply_to(message, "Usage: /set_policy ‹user_id› ‹policy text›")
        return
    db.set_user_field(uid, "policy", pol)
    bot.reply_to(message, f"✅ Policy set for <code>{uid}</code>.", parse_mode="HTML")


@bot.message_handler(commands=["broadcast"])
@admin_only
def cmd_broadcast(message):
    text = message.text.split(None, 1)
    if len(text) < 2 or not text[1].strip():
        bot.reply_to(message, "Usage: /broadcast ‹message›")
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
    bot.reply_to(message, f"📢 Broadcast: ✅ {sent} sent, ❌ {failed} failed.")
    log.info(f"Broadcast: {sent} sent, {failed} failed")


# ══════════════════════════════════════════════════════════════════════════════
# PHOTO HANDLER
# ══════════════════════════════════════════════════════════════════════════════


@bot.message_handler(content_types=["photo"])
def handle_photo(message):
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
            bot.reply_to(message, "✨ Enhancement unavailable (Pillow error)।")
            return
        with open(out, "rb") as f:
            bot.send_photo(
                message.chat.id,
                f,
                caption="✨ Enhanced — sharpen + denoise + upscale applied.",
                reply_to_message_id=message.message_id,
            )
        db.increment_daily_count(uid)
        send_log(uid, uname, "✨ <b>Image Enhanced</b>")
    except Exception as e:
        log.error(f"Enhance error: {e}")
        bot.reply_to(message, "✨ Enhancement failed। আবার চেষ্টা করুন।")
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
        caption = message.caption or "এই ছবিটি বিশ্লেষণ করো এবং বিস্তারিত বলো।"
        user_data = db.get_user(uid) or {}
        role = _user_role(user_data, uid)
        prompt = ai.build_prompt(
            uid, caption, user_data, user_name=uname, user_role=role
        )
        reply = ai.generate_ai_response(prompt, image=img)

        ai.add_to_session(uid, "user", caption)
        ai.add_to_session(uid, "assistant", reply)
        db.increment_daily_count(uid)

        bot.reply_to(message, reply)
        send_log_copy(uid, uname, message.chat.id, message.message_id)
        send_log(uid, uname, f"🖼 <b>Vision</b>\n{eh(reply[:400])}")
    except Exception as e:
        log.error(f"Photo AI error: {e}")
        bot.reply_to(message, "ছবিটি বিশ্লেষণ করা যায়নি। আবার চেষ্টা করুন।")
    finally:
        it.cleanup(src)


# ══════════════════════════════════════════════════════════════════════════════
# TEXT HANDLER — AI Chat with auto-search
# ══════════════════════════════════════════════════════════════════════════════


@bot.message_handler(content_types=["text"])
def handle_text(message):
    if message.chat.type != "private":
        return
    if message.text.startswith("/"):
        return
    if not check_user_access(message):
        return
    react_ok(message.chat.id, message.message_id)
    executor.submit(_do_ai_chat, message)


def _do_ai_chat(message):
    uid = message.from_user.id
    uname = message.from_user.first_name or "User"
    user_text = message.text

    try:
        bot.send_chat_action(message.chat.id, "typing")
        db.upsert_user(uid, uname)
        user_data = db.get_user(uid) or {}
        role = _user_role(user_data, uid)

        # Auto-detect real-time search need
        search_ctx = None
        if se.needs_realtime_search(user_text):
            log.info(f"Auto-search: {user_text[:60]}")
            search_ctx = _get_search_context(user_text)

        prompt = ai.build_prompt(
            uid,
            user_text,
            user_data,
            user_name=uname,
            user_role=role,
            search_context=search_ctx,
        )
        reply = ai.generate_ai_response(prompt)

        ai.add_to_session(uid, "user", user_text)
        ai.add_to_session(uid, "assistant", reply)
        db.increment_daily_count(uid)

        bot.reply_to(message, reply)

        badge = "🌐 Search+AI" if search_ctx else "🤖 AI"
        send_log(
            uid, uname, f"{badge}\n📩 {eh(user_text[:200])}\n\n💬 {eh(reply[:300])}"
        )

    except Exception as e:
        log.error(f"AI chat error: {e}")
        bot.reply_to(message, "দুঃখিত, সমস্যা হয়েছে। আবার চেষ্টা করুন।")


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

run = "python main.py"
