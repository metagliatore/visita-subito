from unittest.mock import MagicMock
import pytest

from services.telegram_bot import TelegramBot


def test_telegram_esc():
    assert TelegramBot._esc("Mario & Luigi") == "Mario &amp; Luigi"
    assert TelegramBot._esc("<div>Test</div>") == "&lt;div&gt;Test&lt;/div&gt;"
    assert TelegramBot._esc('Visita "Speciale"') == "Visita &quot;Speciale&quot;"
    assert TelegramBot._esc(None) == ""


def test_telegram_autorizzato():
    bot = TelegramBot.__new__(TelegramBot)
    bot.chat_ids = ["12345678", 99999]

    # Utente autorizzato con int
    up1 = MagicMock()
    up1.effective_chat.id = 12345678
    assert bot._autorizzato(up1) is True

    # Utente autorizzato con str
    up2 = MagicMock()
    up2.effective_chat.id = 99999
    assert bot._autorizzato(up2) is True

    # Utente non autorizzato
    up3 = MagicMock()
    up3.effective_chat.id = 11111
    assert bot._autorizzato(up3) is False

    # Chat None
    up4 = MagicMock()
    up4.effective_chat = None
    assert bot._autorizzato(up4) is False


def test_telegram_risveglia_se_needed():
    bot = TelegramBot.__new__(TelegramBot)
    bot.notify = MagicMock()
    ctrl = MagicMock()
    bot.controller = ctrl

    # Non bloccato
    ctrl.login_bloccato = False
    assert bot._risveglia_se_needed(MagicMock()) is False
    bot.notify.assert_not_called()

    # Bloccato -> risveglio
    ctrl.login_bloccato = True
    assert bot._risveglia_se_needed(MagicMock()) is True
    bot.notify.assert_called_once()
