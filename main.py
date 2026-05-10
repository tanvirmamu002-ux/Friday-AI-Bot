import os
import uuid
import requests
import telebot
from google import genai
from google.genai import types
from PIL import Image

# ==================================================
# BOT CONFIG
# ==================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN")

if not BOT_TOKEN:
    raise Exception("BOT_TOKEN পাওয়া যায়নি")

bot = telebot.TeleBot(
    BOT_TOKEN,
    parse_mode="Markdown"
)

# ==================================================
# GEMINI FAILOVER SYSTEM
# ==================================================

GEMINI_KEYS = [
    os.environ.get("GEMINI_KEY_1"),
    os.environ.get("GEMINI_KEY_2"),
    os.environ.get("GEMINI_KEY_3")
]

GEMINI_KEYS = [k for k in GEMINI_KEYS if k]

if len(GEMINI_KEYS) == 0:
    raise Exception("কোনো Gemini API Key পাওয়া যায়নি")

current_key_index = 0


def generate_ai_response(prompt, image=None):

    global current_key_index

    total_keys = len(GEMINI_KEYS)

    for _ in range(total_keys):

        try:

            api_key = GEMINI_KEYS[current_key_index]

            client = genai.Client(api_key=api_key)

            if image:
                response = client.models.generate_content(
                    model="gemini-2.0-flash",
                    contents=[prompt, image]
                )
            else:
                response = client.models.generate_content(
                    model="gemini-2.0-flash",
                    contents=prompt
                )

            text = response.text if response.text else None

            if text:
                return text

        except Exception as e:

            print(f"[API FAILED] key index {current_key_index}: {e}")

        current_key_index = (current_key_index + 1) % total_keys

    return "দুঃখিত, বর্তমানে AI সার্ভিস unavailable।"


# ==================================================
# SEARCH CONFIG
# ==================================================

SERPAPI_KEY = os.environ.get("SERPAPI_KEY")

# ==================================================
# ADMIN CONFIG
# ==================================================

MY_ID = 8234592104

LOG_GROUP_ID = -1003848412289

# ==================================================
# START COMMAND
# ==================================================

@bot.message_handler(commands=['start'])
def start(message):

    user_name = message.from_user.first_name

    welcome_text = (
        f"হ্যালো {user_name} 👋\n\n"
        "আমি একটি Advanced AI Assistant Bot.\n\n"
        "*Available Commands:*\n"
        "🔍 /search keyword\n"
        "🎬 /yt keyword\n"
        "🖼️ /image keyword\n\n"
        "এছাড়া আপনি সরাসরি চ্যাট করতে পারেন।"
    )

    bot.reply_to(message, welcome_text)

    try:
        bot.send_message(
            LOG_GROUP_ID,
            f"🚀 *New User Joined*\n\n"
            f"👤 {user_name}\n"
            f"🆔 `{message.from_user.id}`"
        )
    except Exception as e:
        print(e)


# ==================================================
# GOOGLE SEARCH
# ==================================================

@bot.message_handler(commands=['search'])
def google_search(message):

    query = message.text.replace("/search", "").strip()

    if not query:
        bot.reply_to(message, "ব্যবহার:\n`/search keyword`")
        return

    bot.send_chat_action(message.chat.id, 'typing')

    try:

        if not SERPAPI_KEY:
            bot.reply_to(message, "Search API configure করা হয়নি")
            return

        url = "https://serpapi.com/search"
        params = {
            "q": query,
            "api_key": SERPAPI_KEY,
            "engine": "google"
        }

        response = requests.get(url, params=params, timeout=20)
        data = response.json()
        results = data.get("organic_results", [])

        if not results:
            bot.reply_to(message, "কোনো ফলাফল পাওয়া যায়নি")
            return

        text = "🔍 *Google Search Results*\n\n"
        for result in results[:5]:
            title = result.get("title", "No Title")
            link = result.get("link", "")
            text += f"• *{title}*\n{link}\n\n"

        bot.reply_to(message, text)

        try:
            bot.send_message(
                LOG_GROUP_ID,
                f"🔍 *Search Used*\n\n"
                f"👤 {message.from_user.first_name}\n"
                f"📌 Query: `{query}`"
            )
        except Exception:
            pass

    except Exception as e:
        print(e)
        bot.reply_to(message, "Search করতে সমস্যা হয়েছে")


# ==================================================
# YOUTUBE SEARCH
# ==================================================

@bot.message_handler(commands=['yt'])
def youtube_search(message):

    query = message.text.replace("/yt", "").strip()

    if not query:
        bot.reply_to(message, "ব্যবহার:\n`/yt keyword`")
        return

    bot.send_chat_action(message.chat.id, 'typing')

    try:

        if not SERPAPI_KEY:
            bot.reply_to(message, "Search API configure করা হয়নি")
            return

        url = "https://serpapi.com/search"
        params = {
            "engine": "youtube",
            "search_query": query,
            "api_key": SERPAPI_KEY
        }

        response = requests.get(url, params=params, timeout=20)
        data = response.json()
        videos = data.get("video_results", [])

        if not videos:
            bot.reply_to(message, "কোনো ভিডিও পাওয়া যায়নি")
            return

        text = "🎬 *YouTube Results*\n\n"
        for video in videos[:5]:
            title = video.get("title", "No Title")
            link = video.get("link", "")
            text += f"• *{title}*\n{link}\n\n"

        bot.reply_to(message, text)

    except Exception as e:
        print(e)
        bot.reply_to(message, "YouTube Search Error")


# ==================================================
# IMAGE SEARCH
# ==================================================

@bot.message_handler(commands=['image'])
def image_search(message):

    query = message.text.replace("/image", "").strip()

    if not query:
        bot.reply_to(message, "ব্যবহার:\n`/image keyword`")
        return

    bot.send_chat_action(message.chat.id, 'upload_photo')

    try:

        if not SERPAPI_KEY:
            bot.reply_to(message, "Search API configure করা হয়নি")
            return

        url = "https://serpapi.com/search"
        params = {
            "q": query,
            "tbm": "isch",
            "api_key": SERPAPI_KEY
        }

        response = requests.get(url, params=params, timeout=20)
        data = response.json()
        images = data.get("images_results", [])

        if not images:
            bot.reply_to(message, "কোনো ছবি পাওয়া যায়নি")
            return

        image_url = images[0].get("original")
        bot.send_photo(
            message.chat.id,
            image_url,
            caption=f"🖼️ Result for: {query}"
        )

    except Exception as e:
        print(e)
        bot.reply_to(message, "Image Search Error")


# ==================================================
# PHOTO ANALYSIS
# ==================================================

@bot.message_handler(content_types=['photo'])
def analyze_photo(message):

    if (
        message.chat.id == MY_ID or
        message.chat.id == LOG_GROUP_ID
    ):
        return

    filename = None

    try:

        bot.send_chat_action(message.chat.id, 'typing')

        filename = f"{uuid.uuid4()}.jpg"

        file_info = bot.get_file(message.photo[-1].file_id)
        downloaded_file = bot.download_file(file_info.file_path)

        with open(filename, "wb") as file:
            file.write(downloaded_file)

        img = Image.open(filename)

        try:
            bot.copy_message(
                LOG_GROUP_ID,
                message.chat.id,
                message.message_id
            )
        except Exception:
            pass

        ai_reply = generate_ai_response(
            "এই ছবিটি বিশ্লেষণ করো এবং বাংলায় বিস্তারিত বলো।",
            image=img
        )

        bot.reply_to(message, ai_reply)

        try:
            bot.send_message(
                LOG_GROUP_ID,
                f"🤖 *Bot Replied To Image*\n\n{ai_reply}"
            )
        except Exception:
            pass

    except Exception as e:
        print(e)
        bot.reply_to(message, "ছবিটি বিশ্লেষণ করা যায়নি")

    finally:
        if filename and os.path.exists(filename):
            os.remove(filename)


# ==================================================
# AUTOMATIC AI CHAT
# ==================================================

@bot.message_handler(content_types=['text'])
def ai_chat(message):

    if (
        message.chat.id == MY_ID or
        message.chat.id == LOG_GROUP_ID
    ):
        return

    if message.text.startswith("/"):
        return

    try:

        bot.send_chat_action(message.chat.id, 'typing')

        user_name = message.from_user.first_name

        try:
            bot.send_message(
                LOG_GROUP_ID,
                f"📩 *Message From User*\n\n"
                f"👤 {user_name}\n"
                f"🆔 `{message.from_user.id}`\n\n"
                f"{message.text}"
            )
        except Exception:
            pass

        ai_reply = generate_ai_response(message.text)

        bot.reply_to(message, ai_reply)

        try:
            bot.send_message(
                LOG_GROUP_ID,
                f"🤖 *Bot Replied*\n\n{ai_reply}"
            )
        except Exception:
            pass

    except Exception as e:
        print(e)
        bot.reply_to(message, "দুঃখিত, সমস্যা হয়েছে")


# ==================================================
# RUN
# ==================================================

print("✅ Bot Running...")

bot.infinity_polling(
    timeout=30,
    long_polling_timeout=10
)
