import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import main


def test_main_wizard_aborted(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "get_base_dir", lambda: tmp_path)
    with patch("core.wizard.check_or_run_wizard", return_value=False):
        # .env file does not exist
        ret = main.main(["--wizard"])
        assert ret == 1


def test_main_missing_env_non_tty(tmp_path, monkeypatch):
    monkeypatch.setattr("core.config.get_base_dir", lambda: tmp_path)
    monkeypatch.setattr("core.wizard.get_base_dir", lambda: tmp_path)
    monkeypatch.setattr(main, "get_base_dir", lambda: tmp_path)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    ret = main.main([])
    assert ret == 1


def test_main_runs_telegram_mode(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("CONTROL_INTERFACE=telegram\nTG_TOKEN=123:ABC\nTG_CHAT_ID=456\n", encoding="utf-8")
    monkeypatch.setattr(main, "get_base_dir", lambda: tmp_path)
    monkeypatch.setenv("CONTROL_INTERFACE", "telegram")
    monkeypatch.setenv("TG_TOKEN", "123:ABC")
    monkeypatch.setenv("TG_CHAT_ID", "456")

    mock_cfg = MagicMock()
    mock_cfg.settings = {}

    with patch("core.wizard.check_or_run_wizard", return_value=True), \
         patch("core.config.Config.load", return_value=mock_cfg), \
         patch("main.Controller") as mock_ctl_cls:
        mock_ctl = MagicMock()
        mock_ctl_cls.return_value = mock_ctl

        ret = main.main(["--nobot"])
        assert ret == 0
        assert mock_ctl_cls.called
        from services.telegram_bot import TelegramBot
        ui_arg = mock_ctl_cls.call_args[0][1]
        assert isinstance(ui_arg, TelegramBot)
        mock_ctl.run.assert_called_once_with(run_bot=False)


def test_main_runs_cli_mode(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("CONTROL_INTERFACE=cli\nSOUND_ALERT=true\n", encoding="utf-8")
    monkeypatch.setattr(main, "get_base_dir", lambda: tmp_path)
    monkeypatch.setenv("CONTROL_INTERFACE", "cli")
    monkeypatch.delenv("TG_TOKEN", raising=False)
    monkeypatch.delenv("TG_CHAT_ID", raising=False)

    mock_cfg = MagicMock()
    mock_cfg.settings = {}

    with patch("core.wizard.check_or_run_wizard", return_value=True), \
         patch("core.config.Config.load", return_value=mock_cfg), \
         patch("main.Controller") as mock_ctl_cls:
        mock_ctl = MagicMock()
        mock_ctl_cls.return_value = mock_ctl

        ret = main.main(["--nobot"])
        assert ret == 0
        assert mock_ctl_cls.called
        from services.cli_ui import CliUI
        ui_arg = mock_ctl_cls.call_args[0][1]
        assert isinstance(ui_arg, CliUI)
        assert ui_arg.sound_alert is True
        mock_ctl.run.assert_called_once_with(run_bot=False)
