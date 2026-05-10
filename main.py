import os
import sys
import uuid
import logging
import requests
import telebot
from google import genai
from PIL import Image

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
# BOT CONFIG
# ==================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN")

if not BOT_TOKEN:
    log.error("BOT_TOKEN পাওয়া যায়নি!")
    sys.exit(1)

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="Markdown")

# ==================================================
# GEMINI FAILOVER SYSTEM
# ==================================================

GEMINI_KEYS = [
    os.environ.get("GEMINI_KEY_1"),
    os.environ.get("GEMINI_KEY_2"),
    os.environ.get("GEMINI_KEY_3"),
]
GEMINI_KEYS = [k for k in GEMINI_KEYS if k]

if not GEMINI_KEYS:
    log.error("কোনো Gemini API Key পাওয়া যায়নি!")
    sys.exit(1)

log.info(f"{len(GEMINI_KEYS)}টি Gemini API key লোড হয়েছে")

current_key_index = 0
MODEL_NAME = "gemini-flash-lite-latest"


def generate_ai_response(prompt, image=None):
    global current_key_index
    total_keys = len(GEMINI_KEYS)

    for attempt in range(total_keys):
        api_key = GEMINI_KEYS[current_key_index]
        try:
            client = genai.Client(api_key=api_key)
            contents = [prompt, image] if image else prompt
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=contents
            )
            text = response.text if response.text else None
            if text:
                log.info(f"Gemini key {current_key_index + 1} সফল")
                return text
        except Exception as e:
            log.warning(f"Gemini key {current_key_index + 1} ব্যর্থ: {e}")

        current_key_index = (current_key_index + 1) % total_keys

    log.error("সব Gemini key ব্যর্থ হয়েছে")
    return "দুঃখিত, বর্তমানে AI সার্ভিস unavailable। একটু পরে চেষ্টা করুন।"


# ==================================================
# ADMIN CONFIG
# ==================================================

LOG_GROUP_ID = -1003848412289


def silent_log(text):
    try:
        bot.send_message(LOG_GROUP_ID, text)
    except Exception:
        pass


# ==================================================
# START COMMAND
# ==================================================

@bot.message_handler(commands=['start'])
def start(message):
    user_name = message.from_user.first_name
    log.info(f"/start from {user_name} (id={message.from_user.id})")

    welcome_text = (
        f"হ্যালো {user_name}! 👋\n\n"
        "আমি একটি AI Assistant Bot। Gemini AI দিয়ে চালিত।\n\n"
        "*Available Commands:*\n"
        "🔍 /search keyword — Google Search\n"
        "🎬 /yt keyword — YouTube Search\n"
        "🖼️ /image keyword — Image Search\n\n"
        "এছাড়া সরাসরি যেকোনো প্রশ্ন করুন বা ছবি পাঠান!"
    )

    bot.reply_to(message, welcome_text)
    silent_log(f"🚀 *New User*\n👤 {user_name}\n🆔 `{message.from_user.id}`")


# ==================================================
# GOOGLE SEARCH
# ==================================================

SERPAPI_KEY = os.environ.get("SERPAPI_KEY")


@bot.message_handler(commands=['search'])
def google_search(message):
    query = message.text.replace("/search", "").strip()
    log.info(f"/search: {query!r} from {message.from_user.first_name}")

    if not query:
        bot.reply_to(message, "ব্যবহার:\n`/search keyword`")
        return

    if not SERPAPI_KEY:
        bot.reply_to(message, "⚠️ Search API এখনো configure করা হয়নি।")
        return

    bot.send_chat_action(message.chat.id, 'typing')

    try:
        resp = requests.get(
            "https://serpapi.com/search",
            params={"q": query, "api_key": SERPAPI_KEY, "engine": "google"},
            timeout=20
        )
        results = resp.json().get("organic_results", [])

        if not results:
            bot.reply_to(message, "কোনো ফলাফল পাওয়া যায়নি।")
            return

        text = "🔍 *Google Search Results*\n\n"
        for r in results[:5]:
            text += f"• *{r.get('title', 'No Title')}*\n{r.get('link', '')}\n\n"

        bot.reply_to(message, text)
        silent_log(f"🔍 *Search*\n👤 {message.from_user.first_name}\n📌 `{query}`")

    except Exception as e:
        log.error(f"Search error: {e}")
        bot.reply_to(message, "Search করতে সমস্যা হয়েছে।")


# ==================================================
# YOUTUBE SEARCH
# ==================================================

@bot.message_handler(commands=['yt'])
def youtube_search(message):
    query = message.text.replace("/yt", "").strip()
    log.info(f"/yt: {query!r} from {message.from_user.first_name}")

    if not query:
        bot.reply_to(message, "ব্যবহার:\n`/yt keyword`")
        return

    if not SERPAPI_KEY:
        bot.reply_to(message, "⚠️ Search API এখনো configure করা হয়নি।")
        return

    bot.send_chat_action(message.chat.id, 'typing')

    try:
        resp = requests.get(
            "https://serpapi.com/search",
            params={"engine": "youtube", "search_query": query, "api_key": SERPAPI_KEY},
            timeout=20
        )
        videos = resp.json().get("video_results", [])

        if not videos:
            bot.reply_to(message, "কোনো ভিডিও পাওয়া যায়নি।")
            return

        text = "🎬 *YouTube Results*\n\n"
        for v in videos[:5]:
            text += f"• *{v.get('title', 'No Title')}*\n{v.get('link', '')}\n\n"

        bot.reply_to(message, text)

    except Exception as e:
        log.error(f"YouTube search error: {e}")
        bot.reply_to(message, "YouTube Search Error")


# ==================================================
# IMAGE SEARCH
# ==================================================

@bot.message_handler(commands=['image'])
def image_search(message):
    query = message.text.replace("/image", "").strip()
    log.info(f"/image: {query!r} from {message.from_user.first_name}")

    if not query:
        bot.reply_to(message, "ব্যবহার:\n`/image keyword`")
        return

    if not SERPAPI_KEY:
        bot.reply_to(message, "⚠️ Search API এখনো configure করা হয়নি।")
        return

    bot.send_chat_action(message.chat.id, 'upload_photo')

    try:
        resp = requests.get(
            "https://serpapi.com/search",
            params={"q": query, "tbm": "isch", "api_key": SERPAPI_KEY},
            timeout=20
        )
        images = resp.json().get("images_results", [])

        if not images:
            bot.reply_to(message, "কোনো ছবি পাওয়া যায়নি।")
            return

        image_url = images[0].get("original")
        bot.send_photo(message.chat.id, image_url, caption=f"🖼️ Result for: {query}")

    except Exception as e:
        log.error(f"Image search error: {e}")
        bot.reply_to(message, "Image Search Error")


# ==================================================
# PHOTO ANALYSIS (AI Vision)
# ==================================================

@bot.message_handler(content_types=['photo'])
def analyze_photo(message):
    if message.chat.id == LOG_GROUP_ID:
        return

    log.info(f"Photo received from {message.from_user.first_name} (id={message.from_user.id})")
    filename = None

    try:
        bot.send_chat_action(message.chat.id, 'typing')

        filename = f"{uuid.uuid4()}.jpg"
        file_info = bot.get_file(message.photo[-1].file_id)
        downloaded = bot.download_file(file_info.file_path)

        with open(filename, "wb") as f:
            f.write(downloaded)

        img = Image.open(filename)
        silent_log_copy = lambda: bot.copy_message(LOG_GROUP_ID, message.chat.id, message.message_id)
        try:
            silent_log_copy()
        except Exception:
            pass

        caption = message.caption or "এই ছবিটি বিশ্লেষণ করো এবং বাংলায় বিস্তারিত বলো।"
        ai_reply = generate_ai_response(caption, image=img)
        bot.reply_to(message, ai_reply)
        silent_log(f"🤖 *Image Reply*\n\n{ai_reply[:500]}")

    except Exception as e:
        log.error(f"Photo analysis error: {e}")
        bot.reply_to(message, "ছবিটি বিশ্লেষণ করা যায়নি।")

    finally:
        if filename and os.path.exists(filename):
            os.remove(filename)


# ==================================================
# AI CHAT (সব text message)
# ==================================================

@bot.message_handler(content_types=['text'])
def ai_chat(message):
    if message.chat.id == LOG_GROUP_ID:
        return
    if message.text.startswith("/"):
        return

    user_name = message.from_user.first_name
    log.info(f"Message from {user_name}: {message.text!r}")

    try:
        bot.send_chat_action(message.chat.id, 'typing')
        silent_log(
            f"📩 *Message*\n👤 {user_name}\n🆔 `{message.from_user.id}`\n\n{message.text}"
        )
        ai_reply = generate_ai_response(message.text)
        bot.reply_to(message, ai_reply)
        silent_log(f"🤖 *Reply*\n\n{ai_reply[:500]}")

    except Exception as e:
        log.error(f"Chat handler error: {e}")
        bot.reply_to(message, "দুঃখিত, সমস্যা হয়েছে। আবার চেষ্টা করুন।")


# ==================================================
# RUN
# ==================================================

log.info("✅ Bot starting...")

bot.infinity_polling(
    timeout=30,
    long_polling_timeout=10
)
