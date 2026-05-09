# Telegram Bot

A simple Telegram bot built with python-telegram-bot. Responds to commands and echoes messages.

## Run & Operate

- `python bot/src/bot.py` — run the bot (via the "Telegram Bot" workflow)
- `python -m pytest bot/tests/ -v` — run unit tests
- Required env: `TELEGRAM_BOT_TOKEN` — bot token from @BotFather on Telegram

## Stack

- Python 3.12
- python-telegram-bot 22.x (polling mode)
- pytest + pytest-asyncio for testing

## Where things live

- `bot/src/bot.py` — all bot logic: command handlers and message handler
- `bot/tests/test_bot.py` — unit tests for handlers
- `bot/README.md` — usage and extension guide

## Architecture decisions

- Uses long-polling (not webhooks) — simpler setup, no public URL required
- All handlers are async functions registered via `CommandHandler` / `MessageHandler`
- Token loaded from `TELEGRAM_BOT_TOKEN` environment variable — never hardcoded

## Product

A Telegram bot that supports `/start`, `/help`, `/echo`, and `/about` commands, and echoes any plain text message back to the sender.

## User preferences

_Populate as you build — explicit user instructions worth remembering across sessions._

## Gotchas

- Bot token must be set as `TELEGRAM_BOT_TOKEN` secret before running
- Only one instance of the bot should run at a time (Telegram rejects multiple pollers)

## Pointers

- See `bot/README.md` for a guide to adding new commands
