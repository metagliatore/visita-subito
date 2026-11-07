"""Tests for GitHub issue reporting guidance and /bug command."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
import yaml

import pytest

from services.updater import format_issue_guide, GITHUB_NEW_ISSUE_URL, GITHUB_ISSUES_URL


def test_format_issue_guide():
    text, buttons = format_issue_guide(version="1.2.3")
    assert "Segnala un Errore" in text
    assert "v1.2.3" in text
    assert "Codice Fiscale" in text
    assert len(buttons) >= 2
    assert buttons[0][0][1] == GITHUB_NEW_ISSUE_URL
    assert buttons[1][0][1] == GITHUB_ISSUES_URL


def test_issue_templates_exist_and_valid():
    bug_yml = Path(".github/ISSUE_TEMPLATE/bug_report.yml")
    feature_yml = Path(".github/ISSUE_TEMPLATE/feature_request.yml")

    assert bug_yml.exists(), "bug_report.yml should exist"
    assert feature_yml.exists(), "feature_request.yml should exist"

    with open(bug_yml, "r", encoding="utf-8") as f:
        bug_data = yaml.safe_load(f)
    assert bug_data["name"] == "🐛 Segnalazione Bug"
    assert "body" in bug_data

    with open(feature_yml, "r", encoding="utf-8") as f:
        feature_data = yaml.safe_load(f)
    assert feature_data["name"] == "💡 Proposta di Funzionalità"
    assert "body" in feature_data


@pytest.mark.asyncio
async def test_telegram_h_bug():
    from services.telegram_bot import TelegramBot
    bot = TelegramBot(token="fake:token", chat_id="12345")
    mock_update = MagicMock()
    mock_update.effective_user.id = 12345
    mock_update.effective_chat.id = 12345
    mock_update.message.reply_text = AsyncMock()

    await bot._h_bug(mock_update, MagicMock())
    mock_update.message.reply_text.assert_awaited_once()
    args, kwargs = mock_update.message.reply_text.call_args
    assert "Segnala un Errore" in args[0]
    assert "reply_markup" in kwargs


def test_cli_ui_cmd_bug(capsys):
    from services.cli_ui import CliUI
    cli = CliUI(sound_alert=False)
    cli.controller = MagicMock()
    cli._cmd_bug()
    captured = capsys.readouterr()
    assert "Segnala un Errore" in captured.out
    assert GITHUB_NEW_ISSUE_URL in captured.out
