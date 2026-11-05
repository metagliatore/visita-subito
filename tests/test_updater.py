"""Tests for update checking, startup welcome messages, and version management."""
from __future__ import annotations

import io
import json
import logging
import urllib.error
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.config import Config
from services import updater
from services.cli_ui import CliUI
from services.telegram_bot import TelegramBot
from services.updater import (
    APP_DESCRIPTION,
    APP_NAME,
    APP_VERSION,
    GITHUB_URL,
    UpdateInfo,
    check_and_log_startup_banner,
    check_for_updates,
    format_cli_welcome,
    format_log_banner,
    format_telegram_welcome,
    get_cached_or_check_updates,
    get_git_version,
    is_newer_version,
    is_tg_startup_notification_enabled,
    is_update_check_enabled,
    parse_semver,
)


@pytest.fixture(autouse=True)
def reset_updater_cache():
    """Reset singleton cache before each test."""
    updater._cached_update_info = None
    updater._banner_already_logged = False
    yield
    updater._cached_update_info = None
    updater._banner_already_logged = False


def test_parse_semver():
    assert parse_semver("v0.2.0") == (0, 2, 0)
    assert parse_semver("0.3.1") == (0, 3, 1)
    assert parse_semver("1.0") == (1, 0, 0)
    assert parse_semver("v2.1.4.5") == (2, 1, 4, 5)
    assert parse_semver("v0.2.0-beta1") == (0, 2, 0)
    assert parse_semver("") == (0, 0, 0)
    assert parse_semver("invalid") == (0, 0, 0)
    assert parse_semver(None) == (0, 0, 0)


def test_is_newer_version():
    assert is_newer_version("v0.3.0", "0.2.0") is True
    assert is_newer_version("0.2.1", "0.2.0") is True
    assert is_newer_version("1.0.0", "0.2.0") is True
    assert is_newer_version("0.2.0", "0.2.0") is False
    assert is_newer_version("v0.2.0", "0.2.0") is False
    assert is_newer_version("0.1.9", "0.2.0") is False
    assert is_newer_version("invalid", "0.2.0") is False


def test_is_update_check_enabled(monkeypatch):
    monkeypatch.delenv("CHECK_UPDATES", raising=False)
    assert is_update_check_enabled() is True

    monkeypatch.setenv("CHECK_UPDATES", "false")
    assert is_update_check_enabled() is False

    monkeypatch.setenv("CHECK_UPDATES", "0")
    assert is_update_check_enabled() is False

    monkeypatch.setenv("CHECK_UPDATES", "true")
    assert is_update_check_enabled() is True

    monkeypatch.delenv("CHECK_UPDATES", raising=False)
    cfg = MagicMock()
    cfg.settings = {"app": {"check_updates": False}}
    assert is_update_check_enabled(cfg) is False


def test_is_tg_startup_notification_enabled(monkeypatch):
    monkeypatch.delenv("TG_STARTUP_NOTIFICATION", raising=False)
    assert is_tg_startup_notification_enabled() is True

    monkeypatch.setenv("TG_STARTUP_NOTIFICATION", "false")
    assert is_tg_startup_notification_enabled() is False

    monkeypatch.setenv("TG_STARTUP_NOTIFICATION", "true")
    assert is_tg_startup_notification_enabled() is True

    monkeypatch.delenv("TG_STARTUP_NOTIFICATION", raising=False)
    cfg = MagicMock()
    cfg.settings = {"app": {"tg_startup_notification": False}}
    assert is_tg_startup_notification_enabled(cfg) is False


def test_check_for_updates_has_newer():
    mock_payload = {
        "tag_name": "v0.3.0",
        "name": "Release v0.3.0",
        "html_url": "https://github.com/metagliatore/visita-subito/releases/tag/v0.3.0",
        "published_at": "2026-11-04T12:00:00Z",
    }
    raw_bytes = json.dumps(mock_payload).encode("utf-8")
    mock_resp = io.BytesIO(raw_bytes)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        info = check_for_updates(current_version="0.2.0", force=True)

    assert info.has_update is True
    assert info.latest_version == "0.3.0"
    assert info.release_name == "Release v0.3.0"
    assert info.release_url == "https://github.com/metagliatore/visita-subito/releases/tag/v0.3.0"
    assert info.error is None


def test_check_for_updates_up_to_date():
    mock_payload = {
        "tag_name": "v0.2.0",
        "name": "v0.2.0",
        "html_url": "https://github.com/metagliatore/visita-subito/releases/tag/v0.2.0",
        "published_at": "2026-11-02T00:00:00Z",
    }
    raw_bytes = json.dumps(mock_payload).encode("utf-8")
    mock_resp = io.BytesIO(raw_bytes)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        info = check_for_updates(current_version="0.2.0", force=True)

    assert info.has_update is False
    assert info.latest_version == "0.2.0"
    assert info.error is None


def test_check_for_updates_disabled_via_env(monkeypatch):
    monkeypatch.setenv("CHECK_UPDATES", "false")
    info = check_for_updates(force=True)
    assert info.has_update is False
    assert info.error == "disabilitato da configurazione"


def test_check_for_updates_404_fallback_to_tags():
    # First call (releases/latest) raises 404, second call (/tags) returns list
    tags_payload = [{"name": "v0.3.0"}]
    tags_bytes = json.dumps(tags_payload).encode("utf-8")

    err_404 = urllib.error.HTTPError(
        url="https://api.github.com/releases/latest",
        code=404,
        msg="Not Found",
        hdrs={},
        fp=None,
    )

    def fake_urlopen(req, *args, **kwargs):
        if "releases/latest" in req.full_url:
            raise err_404
        return io.BytesIO(tags_bytes)

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        info = check_for_updates(current_version="0.2.0", force=True)

    assert info.has_update is True
    assert info.latest_version == "0.3.0"
    assert info.release_tag == "v0.3.0"


def test_check_for_updates_network_error():
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("DNS lookup failed")):
        info = check_for_updates(current_version="0.2.0", force=True)

    assert info.has_update is False
    assert info.latest_version is None
    assert "DNS lookup failed" in info.error


def test_get_cached_or_check_updates():
    fake_info = UpdateInfo(current_version="0.2.0", latest_version="0.2.0")
    updater._cached_update_info = fake_info
    assert get_cached_or_check_updates() is fake_info


def test_get_git_version():
    res = get_git_version()
    # In a git repo, returns string starting with v or commit hash
    if res is not None:
        assert isinstance(res, str)
        assert len(res) > 0

    with patch("subprocess.run", side_effect=Exception("no git")):
        assert get_git_version() is None


def test_format_log_banner_has_update():
    info = UpdateInfo(
        current_version="0.2.0",
        latest_version="0.3.0",
        has_update=True,
        release_url="https://github.com/metagliatore/visita-subito/releases/tag/v0.3.0",
    )
    banner = format_log_banner(info)
    assert APP_NAME in banner
    assert "v0.2.0" in banner
    assert "NUOVA VERSIONE DISPONIBILE: v0.3.0" in banner
    assert GITHUB_URL in banner
    assert APP_DESCRIPTION in banner


def test_format_log_banner_up_to_date():
    info = UpdateInfo(
        current_version="0.2.0",
        latest_version="0.2.0",
        has_update=False,
    )
    banner = format_log_banner(info)
    assert "Visita-Subito è aggiornato all'ultima versione" in banner


def test_format_log_banner_error():
    info = UpdateInfo(
        current_version="0.2.0",
        error="HTTP 500",
    )
    banner = format_log_banner(info)
    assert "Controllo aggiornamenti GitHub non disponibile (HTTP 500)" in banner


def test_format_cli_welcome():
    info_update = UpdateInfo(
        current_version="0.2.0",
        latest_version="0.3.0",
        has_update=True,
        release_url="https://github.com/metagliatore/visita-subito/releases/tag/v0.3.0",
    )
    cli_txt = format_cli_welcome(info_update)
    assert "Aggiornamento disponibile: v0.3.0" in cli_txt
    assert "Controllo Interattivo da Terminale" in cli_txt

    info_ok = UpdateInfo(current_version="0.2.0", has_update=False)
    cli_ok = format_cli_welcome(info_ok)
    assert "Applicativo aggiornato all'ultima versione" in cli_ok


def test_format_telegram_welcome():
    info_update = UpdateInfo(
        current_version="0.2.0",
        latest_version="0.3.0",
        has_update=True,
        release_url="https://github.com/metagliatore/visita-subito/releases/tag/v0.3.0",
    )
    text, buttons = format_telegram_welcome(info_update)
    assert "<b>Benvenuto su Visita-Subito!</b>" in text
    assert "v0.2.0" in text
    assert "Nuova versione disponibile" in text
    assert "v0.3.0" in text
    assert any("Scarica" in btn[0] for row in buttons for btn in row)
    assert any("GitHub" in btn[0] for row in buttons for btn in row)

    info_ok = UpdateInfo(current_version="0.2.0", latest_version="0.2.0", has_update=False)
    text_ok, buttons_ok = format_telegram_welcome(info_ok)
    assert "Stai utilizzando l'ultima versione disponibile" in text_ok
    assert any("GitHub" in btn[0] for row in buttons_ok for btn in row)


def test_check_and_log_startup_banner():
    mock_log = MagicMock()
    fake_info = UpdateInfo(current_version="0.2.0", latest_version="0.2.0")
    with patch("services.updater.get_cached_or_check_updates", return_value=fake_info):
        # First call logs
        res1 = check_and_log_startup_banner(logger=mock_log)
        assert res1 is fake_info
        assert mock_log.info.call_count > 0

        # Second call does not re-log
        prev_calls = mock_log.info.call_count
        res2 = check_and_log_startup_banner(logger=mock_log)
        assert res2 is fake_info
        assert mock_log.info.call_count == prev_calls


def test_cli_ui_welcome_and_version(capsys):
    ui = CliUI(sound_alert=False)
    ui.controller = MagicMock()
    ui.controller.cfg = MagicMock()

    fake_info = UpdateInfo(current_version="0.2.0", latest_version="0.2.0")
    with patch("services.updater.get_cached_or_check_updates", return_value=fake_info):
        ui._print_welcome()
        captured = capsys.readouterr()
        assert "Visita-Subito" in captured.out
        assert "Applicativo aggiornato" in captured.out

    with patch("services.updater.check_for_updates", return_value=fake_info):
        ui._cmd_version()
        captured = capsys.readouterr()
        assert "Verifica aggiornamenti su GitHub" in captured.out
        assert "v0.2.0" in captured.out


def test_telegram_bot_h_version():
    bot = TelegramBot.__new__(TelegramBot)
    bot.chat_ids = ["12345"]
    bot.controller = MagicMock()

    up = MagicMock()
    up.effective_chat.id = 12345
    up.message.reply_text = AsyncMock()

    fake_info = UpdateInfo(
        current_version="0.2.0",
        latest_version="0.3.0",
        has_update=True,
        release_url="https://github.com/metagliatore/visita-subito/releases/tag/v0.3.0",
    )

    import asyncio
    with patch("services.updater.check_for_updates", return_value=fake_info):
        asyncio.run(bot._h_version(up, MagicMock()))

    up.message.reply_text.assert_called_once()
    args, kwargs = up.message.reply_text.call_args
    sent_text = args[0]
    assert "Nuova versione disponibile" in sent_text
    assert "v0.3.0" in sent_text
    assert kwargs.get("reply_markup") is not None


def test_telegram_bot_h_start_includes_welcome():
    bot = TelegramBot.__new__(TelegramBot)
    bot.chat_ids = ["12345"]
    bot.controller = MagicMock()

    up = MagicMock()
    up.effective_chat.id = 12345
    up.message.reply_text = AsyncMock()

    import asyncio
    asyncio.run(bot._h_start(up, MagicMock()))

    up.message.reply_text.assert_called_once()
    args, kwargs = up.message.reply_text.call_args
    assert "Benvenuto su Visita-Subito" in args[0]
    assert GITHUB_URL in args[0]
    assert "/version" in args[0]


def test_telegram_send_startup_welcome():
    bot = TelegramBot.__new__(TelegramBot)
    bot.token = "12345:fake"
    bot.chat_ids = ["12345", "67890"]
    bot.controller = MagicMock()
    bot.controller.cfg = MagicMock()

    app = MagicMock()
    app.bot.send_message = AsyncMock()

    fake_info = UpdateInfo(
        current_version="0.2.0",
        latest_version="0.3.0",
        has_update=True,
        release_url="https://github.com/metagliatore/visita-subito/releases/tag/v0.3.0",
    )

    import asyncio
    with patch("services.updater.get_cached_or_check_updates", return_value=fake_info):
        asyncio.run(bot._send_startup_welcome(app))

    assert app.bot.send_message.call_count == 2
    for call in app.bot.send_message.call_args_list:
        _, kwargs = call
        assert "Benvenuto su Visita-Subito" in kwargs["text"]
        assert "Nuova versione disponibile" in kwargs["text"]


def test_telegram_send_startup_welcome_disabled(monkeypatch):
    monkeypatch.setenv("TG_STARTUP_NOTIFICATION", "false")
    bot = TelegramBot.__new__(TelegramBot)
    bot.token = "12345:fake"
    bot.chat_ids = ["12345"]
    bot.controller = MagicMock()

    app = MagicMock()
    app.bot.send_message = AsyncMock()

    import asyncio
    asyncio.run(bot._send_startup_welcome(app))

    app.bot.send_message.assert_not_called()

