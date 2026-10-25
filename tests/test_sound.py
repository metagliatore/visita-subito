import sys
from unittest.mock import MagicMock, patch

import pytest

from services.sound import is_sound_enabled, play_alert_sound


def test_is_sound_enabled_default(monkeypatch):
    monkeypatch.delenv("SOUND_ALERT", raising=False)
    assert is_sound_enabled() is True


def test_is_sound_enabled_disabled_env(monkeypatch):
    monkeypatch.setenv("SOUND_ALERT", "false")
    assert is_sound_enabled() is False

    monkeypatch.setenv("SOUND_ALERT", "0")
    assert is_sound_enabled() is False

    monkeypatch.setenv("SOUND_ALERT", "no")
    assert is_sound_enabled() is False


def test_is_sound_enabled_override():
    assert is_sound_enabled(override=True) is True
    assert is_sound_enabled(override=False) is False


def test_play_alert_sound_disabled(monkeypatch):
    monkeypatch.setenv("SOUND_ALERT", "false")
    assert play_alert_sound("slot_found") is False


def test_play_alert_sound_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    mock_winsound = MagicMock()
    with patch.dict("sys.modules", {"winsound": mock_winsound}):
        res = play_alert_sound("slot_found", enabled=True)
        assert res is True
        assert mock_winsound.Beep.called or mock_winsound.MessageBeep.called

        res_succ = play_alert_sound("success", enabled=True)
        assert res_succ is True

        res_err = play_alert_sound("error", enabled=True)
        assert res_err is True


def test_play_alert_sound_non_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    with patch("sys.stdout.write") as mock_write, patch("sys.stdout.flush") as mock_flush:
        res = play_alert_sound("slot_found", enabled=True)
        assert res is True
        assert mock_write.called
        assert mock_flush.called

