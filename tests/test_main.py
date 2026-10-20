import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import main


def test_main_wizard_aborted(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "get_base_dir", lambda: tmp_path)
    with patch("core.wizard.check_or_run_wizard", return_value=False):
        # file .env non esiste
        ret = main.main(["--wizard"])
        assert ret == 1


def test_main_missing_env_non_tty(tmp_path, monkeypatch):
    monkeypatch.setattr("core.config.get_base_dir", lambda: tmp_path)
    monkeypatch.setattr("core.wizard.get_base_dir", lambda: tmp_path)
    monkeypatch.setattr(main, "get_base_dir", lambda: tmp_path)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    ret = main.main([])
    assert ret == 1
