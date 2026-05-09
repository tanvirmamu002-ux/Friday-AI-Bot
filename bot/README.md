# Telegram Bot

A simple Telegram bot built with [python-telegram-bot](https://python-telegram-bot.org/).

## Project Structure

```
bot/
├── src/
│   ├── __init__.py
│   └── bot.py          # Main bot logic — add commands here
├── tests/
│   ├── __init__.py
│   └── test_bot.py     # Unit tests for handlers
└── README.md
```

## Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message |
| `/help` | List available commands |
| `/echo <text>` | Echo text back |
| `/about` | About the bot |

Any non-command message is also echoed back.

## Adding a New Command

1. Open `bot/src/bot.py`
2. Add a new async handler function:

```python
async def my_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Hello from my command!")
```

3. Register it in `main()`:

```python
app.add_handler(CommandHandler("mycommand", my_command))
```

## Running Tests

```bash
pip install pytest pytest-asyncio
python -m pytest bot/tests/ -v
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Your bot token from @BotFather |
