"""Tests for donation configuration, message formatting, and Telegram notifications."""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.donation import (
    build_donation_buttons,
    build_donation_message,
    get_donation_config,
    invia_messaggio_donazione,
)


@pytest.fixture(autouse=True)
def clean_donation_env():
    """Ensure clean donation env variables for every test."""
    keys = [
        "DONATION_URL",
        "DONATION_ENABLED",
        "DONATION_URL_2EUR",
        "DONATION_URL_3EUR",
        "DONATION_URL_5EUR",
    ]
    old_vals = {k: os.environ.get(k) for k in keys}
    for k in keys:
        os.environ.pop(k, None)
    yield
    for k, v in old_vals.items():
        if v is not None:
            os.environ[k] = v
        else:
            os.environ.pop(k, None)


def test_donation_config_empty_default():
    cfg = get_donation_config({})
    assert cfg["enabled"] is False
    assert cfg["url"] == ""


def test_donation_config_from_settings():
    settings = {
        "donations": {
            "enabled": True,
            "url": "https://buymeacoffee.com/visitasubito",
            "url_2eur": "https://buymeacoffee.com/visitasubito/2",
        }
    }
    cfg = get_donation_config(settings)
    assert cfg["enabled"] is True
    assert cfg["url"] == "https://buymeacoffee.com/visitasubito"
    assert cfg["url_2eur"] == "https://buymeacoffee.com/visitasubito/2"


def test_donation_config_env_overrides_settings(monkeypatch):
    monkeypatch.setenv("DONATION_URL", "https://ko-fi.com/visitasubito")
    settings = {"donations": {"url": "https://buymeacoffee.com/old"}}
    cfg = get_donation_config(settings)
    assert cfg["enabled"] is True
    assert cfg["url"] == "https://ko-fi.com/visitasubito"


def test_donation_config_disabled_via_env(monkeypatch):
    monkeypatch.setenv("DONATION_URL", "https://buymeacoffee.com/test")
    monkeypatch.setenv("DONATION_ENABLED", "false")
    cfg = get_donation_config({})
    assert cfg["enabled"] is False


def test_build_donation_buttons_paypal():
    cfg = {"url": "https://paypal.me/mario"}
    buttons = build_donation_buttons(cfg)
    assert len(buttons) == 2
    row1, row2 = buttons[0], buttons[1]
    assert len(row1) == 2
    assert len(row2) == 2
    assert row1[0][0] == "☕ Offri 2€"
    assert row1[0][1] == "https://paypal.me/mario/2EUR"
    assert row1[1][0] == "🍕 Offri 5€"
    assert row1[1][1] == "https://paypal.me/mario/5EUR"
    assert row2[0][0] == "💝 Altro importo"
    assert row2[0][1] == "https://paypal.me/mario"
    assert row2[1][0] == "⭐ Stella su GitHub"
    assert "github.com/metagliatore/visita-subito" in row2[1][1]


def test_build_donation_buttons_custom_tiers():
    cfg = {
        "url": "https://buymeacoffee.com/test",
        "url_2eur": "https://buymeacoffee.com/test/2eur",
        "url_5eur": "https://buymeacoffee.com/test/5eur",
    }
    buttons = build_donation_buttons(cfg)
    assert len(buttons) == 2
    assert buttons[0][0][0] == "☕ Offri 2€"
    assert buttons[0][0][1] == "https://buymeacoffee.com/test/2eur"
    assert buttons[0][1][0] == "🍕 Offri 5€"
    assert buttons[0][1][1] == "https://buymeacoffee.com/test/5eur"
    assert buttons[1][0][0] == "💝 Altro importo"
    assert buttons[1][0][1] == "https://buymeacoffee.com/test"
    assert buttons[1][1][0] == "⭐ Stella su GitHub"


def test_build_donation_buttons_buymeacoffee():
    cfg = {"url": "https://buymeacoffee.com/metagliatore"}
    buttons = build_donation_buttons(cfg)
    assert len(buttons) == 1
    assert len(buttons[0]) == 2
    assert buttons[0][0] == ("☕ Offri un caffè", "https://buymeacoffee.com/metagliatore")
    assert buttons[0][1] == ("⭐ Dai una stella su GitHub", "https://github.com/metagliatore/visita-subito")


def test_build_donation_buttons_empty():
    assert build_donation_buttons({}) == []


def test_build_donation_message():
    msg = build_donation_message()
    assert "Prenotazione confermata e salvata con successo!" in msg
    assert "100€ e 150€" in msg
    assert "open source" in msg
    assert "offrirmi un caffè" in msg
    assert "stella su GitHub" in msg


def test_invia_messaggio_donazione_disabled():
    mock_bot = MagicMock()
    sent = invia_messaggio_donazione(mock_bot, settings={"donations": {"enabled": False}})
    assert sent is False
    mock_bot.notify_buttons.assert_not_called()
    mock_bot.notify.assert_not_called()


def test_invia_messaggio_donazione_enabled(monkeypatch):
    monkeypatch.setenv("DONATION_URL", "https://buymeacoffee.com/visitasubito")
    mock_bot = MagicMock()
    mock_bot.notify_buttons = MagicMock(return_value=[("chat", 1)])

    sent = invia_messaggio_donazione(mock_bot)
    assert sent is True
    mock_bot.notify_buttons.assert_called_once()
    args, _ = mock_bot.notify_buttons.call_args
    assert "Prenotazione confermata" in args[0]
    assert len(args[1]) == 1
    assert len(args[1][0]) == 2
    assert args[1][0][0][0] == "☕ Offri un caffè"
    assert args[1][0][1][0] == "⭐ Dai una stella su GitHub"


def test_invia_messaggio_donazione_handles_exception(monkeypatch):
    monkeypatch.setenv("DONATION_URL", "https://buymeacoffee.com/visitasubito")
    mock_bot = MagicMock()
    mock_bot.notify_buttons.side_effect = RuntimeError("Network error")
    sent = invia_messaggio_donazione(mock_bot)
    assert sent is False


@pytest.mark.asyncio
async def test_telegram_h_dona_disabled(monkeypatch):
    from services.telegram_bot import TelegramBot
    bot = TelegramBot(token="fake:token", chat_id="12345")
    mock_update = MagicMock()
    mock_update.effective_user.id = 12345
    mock_update.effective_chat.id = 12345
    mock_update.message.reply_text = AsyncMock()

    await bot._h_dona(mock_update, MagicMock())
    mock_update.message.reply_text.assert_awaited_once()
    call_args = mock_update.message.reply_text.call_args[0][0]
    assert "Nessun link di donazione o supporto configurato" in call_args


@pytest.mark.asyncio
async def test_telegram_h_dona_enabled(monkeypatch):
    monkeypatch.setenv("DONATION_URL", "https://buymeacoffee.com/testuser")
    from services.telegram_bot import TelegramBot
    bot = TelegramBot(token="fake:token", chat_id="12345")
    mock_update = MagicMock()
    mock_update.effective_user.id = 12345
    mock_update.effective_chat.id = 12345
    mock_update.message.reply_text = AsyncMock()

    await bot._h_dona(mock_update, MagicMock())
    mock_update.message.reply_text.assert_awaited_once()
    call_kwargs = mock_update.message.reply_text.call_args[1]
    assert "reply_markup" in call_kwargs
    call_text = mock_update.message.reply_text.call_args[0][0]
    assert "offrirmi un caffè" in call_text


def test_flow_base_invia_messaggio_donazione_integration():
    from flows.base import Flow
    mock_bot = MagicMock()
    mock_bot.notify_buttons = MagicMock(return_value=[("chat", 1)])

    flow = Flow(
        monitor={"id": "mon-1", "ricetta": "Dermatologia"},
        browser=MagicMock(),
        queue=MagicMock(),
        bot=mock_bot,
        store=MagicMock(),
    )
    flow.settings = {"donations": {"enabled": True, "url": "https://buymeacoffee.com/visitasubito"}}
    flow._invia_messaggio_donazione()
    mock_bot.notify_buttons.assert_called_once()
    args = mock_bot.notify_buttons.call_args[0]
    assert "Prenotazione confermata" in args[0]
