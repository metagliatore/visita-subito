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
        # 1. slot_found triggers Beep
        res = play_alert_sound("slot_found", enabled=True)
        assert res is True
        assert mock_winsound.Beep.called

        # 2. success triggers MessageBeep(MB_OK)
        res_succ = play_alert_sound("success", enabled=True)
        assert res_succ is True
        mock_winsound.MessageBeep.assert_called_with(mock_winsound.MB_OK)

        # 3. error triggers MessageBeep(MB_ICONHAND)
        res_err = play_alert_sound("error", enabled=True)
        assert res_err is True
        mock_winsound.MessageBeep.assert_called_with(mock_winsound.MB_ICONHAND)

        # 4. default unknown kind triggers MessageBeep(MB_ICONASTERISK)
        res_other = play_alert_sound("custom_unknown", enabled=True)
        assert res_other is True
        mock_winsound.MessageBeep.assert_called_with(mock_winsound.MB_ICONASTERISK)

        # 5. Beep failure falls back to MessageBeep
        mock_winsound.reset_mock()
        mock_winsound.Beep.side_effect = RuntimeError("No sound device")
        res_fallback = play_alert_sound("slot_found", enabled=True)
        assert res_fallback is True
        mock_winsound.MessageBeep.assert_called_with(mock_winsound.MB_ICONEXCLAMATION)

        # 6. Success Beep failure fallback
        mock_winsound.reset_mock()
        mock_winsound.MessageBeep.side_effect = RuntimeError("MessageBeep failed")
        mock_winsound.Beep.side_effect = None
        res_succ_fallback = play_alert_sound("success", enabled=True)
        assert res_succ_fallback is True
        mock_winsound.Beep.assert_called_with(1000, 200)

        # 7. Error Beep failure fallback
        mock_winsound.reset_mock()
        mock_winsound.MessageBeep.side_effect = RuntimeError("MessageBeep failed")
        res_err_fallback = play_alert_sound("error", enabled=True)
        assert res_err_fallback is True
        mock_winsound.Beep.assert_called_with(440, 300)


def test_play_alert_sound_windows_total_failure_falls_back_to_bell(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    mock_winsound = MagicMock()
    mock_winsound.Beep.side_effect = RuntimeError("Fatal sound error")
    mock_winsound.MessageBeep.side_effect = RuntimeError("Fatal sound error")
    with patch.dict("sys.modules", {"winsound": mock_winsound}), \
         patch("services.sound._play_terminal_bell", return_value=True) as mock_bell:
        res = play_alert_sound("slot_found", enabled=True)
        assert res is True
        mock_bell.assert_called_once_with("slot_found")


def test_find_linux_sound_file_custom_env(tmp_path, monkeypatch):
    from services.sound import _find_linux_sound_file

    custom_file = tmp_path / "custom.ogg"
    custom_file.write_text("audio")

    # Set custom valid file
    monkeypatch.setenv("SOUND_ALERT_FILE", str(custom_file))
    assert _find_linux_sound_file("slot_found") == str(custom_file)

    # Set non-existent custom file -> falls back to candidate scan
    monkeypatch.setenv("SOUND_ALERT_FILE", "/path/to/nonexistent/alert.wav")
    assert _find_linux_sound_file("slot_found") is None or isinstance(_find_linux_sound_file("slot_found"), str)


def test_play_linux_sound_pwplay(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    with patch("services.sound._find_linux_sound_file", return_value="/fake/sound.oga"), \
         patch("shutil.which", side_effect=lambda cmd: "/usr/bin/pw-play" if cmd == "pw-play" else None), \
         patch("subprocess.Popen") as mock_popen:
        res = play_alert_sound("slot_found", enabled=True)
        assert res is True
        mock_popen.assert_called_once()
        assert mock_popen.call_args[0][0] == ["pw-play", "/fake/sound.oga"]


def test_play_linux_sound_canberra(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    with patch("services.sound._find_linux_sound_file", return_value="/fake/sound.oga"), \
         patch("shutil.which", side_effect=lambda cmd: "/usr/bin/canberra-gtk-play" if cmd == "canberra-gtk-play" else None), \
         patch("subprocess.Popen") as mock_popen:
        res = play_alert_sound("slot_found", enabled=True)
        assert res is True
        mock_popen.assert_called_once()
        assert mock_popen.call_args[0][0] == ["canberra-gtk-play", "-f", "/fake/sound.oga"]


def test_play_linux_sound_aplay_wav(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    with patch("services.sound._find_linux_sound_file", return_value="/fake/sound.wav"), \
         patch("shutil.which", side_effect=lambda cmd: "/usr/bin/aplay" if cmd == "aplay" else None), \
         patch("subprocess.Popen") as mock_popen:
        res = play_alert_sound("slot_found", enabled=True)
        assert res is True
        mock_popen.assert_called_once()
        assert mock_popen.call_args[0][0] == ["aplay", "-q", "/fake/sound.wav"]


def test_play_linux_sound_player_error_falls_back_to_bell(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    with patch("services.sound._find_linux_sound_file", return_value="/fake/sound.oga"), \
         patch("shutil.which", return_value="/usr/bin/paplay"), \
         patch("subprocess.Popen", side_effect=OSError("Process spawn failed")), \
         patch("services.sound._play_terminal_bell", return_value=True) as mock_bell:
        res = play_alert_sound("slot_found", enabled=True)
        assert res is True
        mock_bell.assert_called_once_with("slot_found")


def test_play_terminal_bell_repeats_and_error(monkeypatch):
    from services.sound import _play_terminal_bell

    # 1. slot_found repeats 2 times
    with patch("sys.stdout.write") as mock_write, patch("sys.stdout.flush"), patch("time.sleep"):
        res = _play_terminal_bell("slot_found")
        assert res is True
        assert mock_write.call_count == 2

    # 2. success repeats 1 time
    with patch("sys.stdout.write") as mock_write, patch("sys.stdout.flush"):
        res = _play_terminal_bell("success")
        assert res is True
        assert mock_write.call_count == 1

    # 3. write error handled gracefully returning False
    with patch("sys.stdout.write", side_effect=IOError("Broken pipe")):
        res = _play_terminal_bell("slot_found")
        assert res is False


def test_play_alert_sound_linux_player(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    with patch("services.sound._find_linux_sound_file", return_value="/fake/sound.oga"), \
         patch("shutil.which", return_value="/usr/bin/paplay"), \
         patch("subprocess.Popen") as mock_popen:
        res = play_alert_sound("slot_found", enabled=True)
        assert res is True
        mock_popen.assert_called_once()
        cmd = mock_popen.call_args[0][0]
        assert cmd == ["paplay", "/fake/sound.oga"]


def test_play_alert_sound_linux_bell_fallback(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    with patch("services.sound._find_linux_sound_file", return_value=None), \
         patch("sys.stdout.write") as mock_write, patch("sys.stdout.flush") as mock_flush:
        res = play_alert_sound("slot_found", enabled=True)
        assert res is True
        assert mock_write.called
        assert mock_flush.called

