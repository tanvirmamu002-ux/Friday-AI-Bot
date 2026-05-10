# Friday AI — Advanced Telegram Bot

An advanced AI Telegram bot powered by Google Gemini with smart memory, web search, rate limiting, SQLite persistence, and admin remote control.

## Run & Operate

- `python main.py` — run the bot (via the "Telegram Bot" workflow)
- Required secrets: `BOT_TOKEN`, `GEMINI_KEY_1`, `GEMINI_KEY_2`, `GEMINI_KEY_3`
- Optional secrets: `ADMIN_ID` (default: 8234592104), `LOG_GROUP_ID` (default: -1003848412289)

## Stack

- Python 3.12
- pyTelegramBotAPI (telebot) — polling mode
- google-genai — Gemini AI (gemini-flash-lite-latest)
- duckduckgo-search — free web/image/video search
- SQLite (built-in) — user persistence
- ThreadPoolExecutor — non-blocking AI calls

## Features

### Performance & Protection
- ThreadPoolExecutor (10 workers) — AI and image tasks run in separate threads
- Rate limiting: max 9 requests/minute per user
- Daily request limits (50/day default, 500 for premium)
- Banned user enforcement

### Smart Memory & Hybrid Search
- Session memory: last 10 message pairs per user (in-memory)
- Hybrid search: RAG → DuckDuckGo → Gemini summarization
- NSFW/malicious content filtering on search results
- Safe image search (moderate safesearch)

### Logging & Topics
- Forum Topics per user in LOG_GROUP_ID
- Fallback to plain group messages if topics fail
- All log messages use HTML escaping to prevent parse errors

### Database (SQLite — bot_data.db)
- Users: ban status, premium status, daily count, daily limit, topic_id, custom_info, policy
- Daily reset logic (date-based)
- 24-hour auto backup sent to LOG_GROUP_ID

### Admin Remote Control (from user topic or with user_id arg)
| Command | Description |
|---------|-------------|
| `/ban [user_id]` | Ban a user |
| `/unban [user_id]` | Unban a user |
| `/premium [user_id]` | Grant premium (500/day limit) |
| `/limit [user_id] <n>` | Set daily limit |
| `/add_info [user_id] <text>` | Add custom info injected into AI context |
| `/set_policy [user_id] <text>` | Set AI behavior policy for user |

### Bot Behavior
- AI replies only in private chats
- Groups/topics: only admin commands
- Reacts 👍 to valid messages
- Gemini 3-key failover system
- Temp image files cleaned up with os.remove

## Where things live

- `main.py` — all bot logic
- `bot_data.db` — SQLite database (auto-created)
- `bot/` — old simple echo bot (not used)

## User preferences

_Populate as you build — explicit user instructions worth remembering across sessions._

## Gotchas

- Only one bot instance at a time (Telegram rejects multiple pollers)
- LOG_GROUP_ID must be a supergroup with Forum Topics enabled for per-user topics to work
- Forum topic fallback works if topics are disabled
