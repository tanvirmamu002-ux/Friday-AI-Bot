"""
Basic tests for the Telegram bot handlers.
Run with: python -m pytest bot/tests/
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture
def mock_update():
    update = MagicMock()
    update.effective_user = MagicMock()
    update.effective_user.first_name = "Alice"
    update.message = MagicMock()
    update.message.reply_html = AsyncMock()
    update.message.reply_text = AsyncMock()
    return update


@pytest.fixture
def mock_context():
    context = MagicMock()
    context.args = []
    return context


@pytest.mark.asyncio
async def test_start_replies_with_greeting(mock_update, mock_context):
    from bot.src.bot import start
    await start(mock_update, mock_context)
    mock_update.message.reply_html.assert_called_once()
    call_args = mock_update.message.reply_html.call_args[0][0]
    assert "Alice" in call_args
    assert "/help" in call_args


@pytest.mark.asyncio
async def test_help_command_lists_commands(mock_update, mock_context):
    from bot.src.bot import help_command
    await help_command(mock_update, mock_context)
    mock_update.message.reply_html.assert_called_once()
    call_args = mock_update.message.reply_html.call_args[0][0]
    assert "/start" in call_args
    assert "/echo" in call_args


@pytest.mark.asyncio
async def test_echo_command_with_args(mock_update, mock_context):
    from bot.src.bot import echo_command
    mock_context.args = ["hello", "world"]
    await echo_command(mock_update, mock_context)
    mock_update.message.reply_text.assert_called_once_with("hello world")


@pytest.mark.asyncio
async def test_echo_command_without_args(mock_update, mock_context):
    from bot.src.bot import echo_command
    mock_context.args = []
    await echo_command(mock_update, mock_context)
    mock_update.message.reply_text.assert_called_once()
    assert "Usage" in mock_update.message.reply_text.call_args[0][0]


@pytest.mark.asyncio
async def test_handle_message_echoes_text(mock_update, mock_context):
    from bot.src.bot import handle_message
    mock_update.message.text = "test message"
    await handle_message(mock_update, mock_context)
    mock_update.message.reply_text.assert_called_once_with("You said: test message")
