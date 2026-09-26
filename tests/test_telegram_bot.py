import asyncio
from unittest.mock import AsyncMock, MagicMock
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


def test_cal_keyboard_structure():
    # Ottobre 2026
    kb = TelegramBot._cal_keyboard(2026, 10, sel="2026-10-15")
    # Riga 0: header con frecce
    assert len(kb.inline_keyboard[0]) == 3
    assert kb.inline_keyboard[0][0].callback_data == "cal:prev"
    assert kb.inline_keyboard[0][2].callback_data == "cal:next"

    # Riga 1: giorni settimana
    assert len(kb.inline_keyboard[1]) == 7
    assert kb.inline_keyboard[1][0].text == "Lu"

    # Verifichiamo che esista il callback per il 15 ottobre
    has_day_15 = False
    for row in kb.inline_keyboard[2:]:
        for btn in row:
            if btn.callback_data == "cal:day:2026-10-15":
                has_day_15 = True
                assert "15" in btn.text
    assert has_day_15 is True


def test_mostra_calendario():
    bot = TelegramBot.__new__(TelegramBot)
    bot._wizard = {}

    testo, kb = bot._mostra_calendario(chat_id=123, anno=2026, mese=11)
    assert "Seleziona la data" in testo
    assert 123 in bot._wizard
    assert bot._wizard[123]["cal_anno"] == 2026
    assert bot._wizard[123]["cal_mese"] == 11


def test_h_help():
    bot = TelegramBot.__new__(TelegramBot)
    bot.chat_ids = ["12345"]
    bot.controller = MagicMock()
    bot.controller.login_bloccato = False
    up = MagicMock()
    up.effective_chat.id = 12345
    up.message.reply_text = AsyncMock()

    asyncio.run(bot._h_help(up, MagicMock()))
    up.message.reply_text.assert_awaited_once()


def test_h_status():
    bot = TelegramBot.__new__(TelegramBot)
    bot.chat_ids = ["12345"]
    bot.controller = MagicMock()
    bot.controller.login_bloccato = False
    bot.controller.status_text.return_value = "Stato di prova"
    up = MagicMock()
    up.effective_chat.id = 12345
    up.message.reply_text = AsyncMock()

    asyncio.run(bot._h_status(up, MagicMock()))
    up.message.reply_text.assert_awaited_once_with("Stato di prova")


def test_h_poll():
    bot = TelegramBot.__new__(TelegramBot)
    bot.chat_ids = ["12345"]
    bot.controller = MagicMock()
    bot.controller.login_bloccato = False
    up = MagicMock()
    up.effective_chat.id = 12345
    up.message.reply_text = AsyncMock()

    asyncio.run(bot._h_poll(up, MagicMock()))
    bot.controller.force_poll.assert_called_once()
    up.message.reply_text.assert_awaited_once_with("Avvio polling forzato...")


def test_h_echo_chat():
    bot = TelegramBot.__new__(TelegramBot)
    bot.chat_ids = ["12345"]
    up = MagicMock()
    up.effective_chat.id = 12345
    up.effective_chat.type = "private"
    up.message.reply_text = AsyncMock()

    asyncio.run(bot._h_echo_chat(up, MagicMock()))
    up.message.reply_text.assert_awaited_once()
    args, _ = up.message.reply_text.call_args
    assert "12345" in args[0]


def test_h_appuntamenti_session_invalid():
    bot = TelegramBot.__new__(TelegramBot)
    bot.chat_ids = ["12345"]
    bot.controller = MagicMock()
    bot.controller.login_bloccato = False
    bot.controller.get_appuntamenti.return_value = None
    up = MagicMock()
    up.effective_chat.id = 12345
    up.message.reply_text = AsyncMock()

    asyncio.run(bot._h_appuntamenti(up, MagicMock()))
    assert up.message.reply_text.await_count == 2
    second_call_args = up.message.reply_text.await_args_list[1][0]
    assert "sessione SPID scaduta o login fallito" in second_call_args[0]


def test_h_ricette_session_invalid():
    bot = TelegramBot.__new__(TelegramBot)
    bot.chat_ids = ["12345"]
    bot.controller = MagicMock()
    bot.controller.login_bloccato = False
    bot.controller.get_ricette.return_value = None
    up = MagicMock()
    up.effective_chat.id = 12345
    up.message.reply_text = AsyncMock()

    asyncio.run(bot._h_ricette(up, MagicMock()))
    assert up.message.reply_text.await_count == 2
    second_call_args = up.message.reply_text.await_args_list[1][0]
    assert "sessione SPID scaduta o login fallito" in second_call_args[0]
