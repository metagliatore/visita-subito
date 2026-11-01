"""Stress and resilience tests for Wizard configuration and CLI Terminal UI."""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.wizard import (
    populate_env_content,
    prompt_choice,
    prompt_input,
    run_wizard,
)
from services.cli_ui import CliUI, safe_int, strip_html


# =============================================================================
# 1. Wizard Stress Tests
# =============================================================================

def test_wizard_unicode_and_special_character_inputs():
    """Verify prompt_choice handles unicode digits (e.g. '²'), emoji, and letters safely."""
    options = [("Opzione 1", "val1"), ("Opzione 2", "val2")]

    # Sequence: unicode superscript two ('²'), emoji ('🚀'), letters ('abc'), then valid '2'
    inputs = ["²", "🚀", "abc", "2"]
    input_iter = iter(inputs)

    with patch("builtins.input", lambda prompt="": next(input_iter)):
        res = prompt_choice("Seleziona:", options, default=1)
        assert res == "val2"


def test_wizard_prevent_env_newline_injection():
    """Verify that multiline values cannot inject arbitrary keys into the .env file."""
    template = "AUTH_METHOD=spid\nSPID_PASSWORD=\nTG_TOKEN=\n"
    malicious_values = {
        "SPID_PASSWORD": "my_pass\r\nINJECTED_KEY=malicious_val\nHACKED=true",
        "TG_TOKEN": "token123\nINJECTED_TWO=bad",
    }
    result = populate_env_content(template, malicious_values)

    lines = result.splitlines()
    for line in lines:
        if line.startswith("#") or not line:
            continue
        key, _, val = line.partition("=")
        assert key not in ("INJECTED_KEY", "HACKED", "INJECTED_TWO"), f"Newline injection succeeded: {line}"
    assert "SPID_PASSWORD=my_pass INJECTED_KEY=malicious_val HACKED=true" in result


def test_wizard_permission_error_handling(tmp_path):
    """Verify that OSError / PermissionError when saving .env returns False without unhandled crash."""
    env_file = tmp_path / ".env"
    example_file = tmp_path / ".env.example"
    example_file.write_text("CONTROL_INTERFACE=cli\n", encoding="utf-8")

    inputs = ["2", "1", "1", "user", "pass", "1", "1", "5"]
    input_iter = iter(inputs)

    with patch("builtins.input", lambda prompt="": next(input_iter)), \
         patch("getpass.getpass", lambda prompt="": next(input_iter)), \
         patch.object(Path, "write_text", side_effect=PermissionError("Permesso negato")):
        ok = run_wizard(env_path=env_file, example_path=example_file)
        assert ok is False


def test_wizard_minuti_stress_inputs(tmp_path):
    """Verify interval parsing handles floats, negatives, huge numbers, and letters."""
    env_file = tmp_path / ".env"
    example_file = tmp_path / ".env.example"
    example_file.write_text("POLL_INTERVAL_SECONDS=300\n", encoding="utf-8")

    test_cases = [
        ("2.5", "150"),  # 2.5 minutes = 150 seconds
        ("0", "60"),     # Min clamped to 60 seconds
        ("-10", "60"),   # Min clamped to 60 seconds
        ("999999999999999999999999999999999999999999999999999", "86400"),  # Clamped to 86400 (24h)
        ("invalid_str", "300"),  # Fallback
    ]

    for raw_input, expected_sec in test_cases:
        inputs = ["2", "1", "5", "1", raw_input]  # CLI -> Sound yes -> Manual login -> first_match -> minuti
        input_iter = iter(inputs)
        with patch("builtins.input", lambda prompt="": next(input_iter)):
            ok = run_wizard(env_path=env_file, example_path=example_file)
            assert ok is True
            content = env_file.read_text(encoding="utf-8")
            assert f"POLL_INTERVAL_SECONDS={expected_sec}" in content


def test_wizard_massive_repeated_runs(tmp_path):
    """Stress test running the wizard 30 consecutive times with alternating configurations."""
    env_file = tmp_path / ".env"
    example_file = tmp_path / ".env.example"
    example_file.write_text("CONTROL_INTERFACE=cli\nTG_TOKEN=\nTG_CHAT_ID=\nAUTH_METHOD=spid\n", encoding="utf-8")

    for i in range(30):
        if i % 2 == 0:
            # CLI mode
            inputs = ["2", "1", "5", "1", "5"]
        else:
            # Telegram mode
            inputs = ["1", f"bot{i}:TOKEN", f"{1000 + i}", "", "5", "1", "5"]
        input_iter = iter(inputs)
        with patch("builtins.input", lambda prompt="": next(input_iter)):
            ok = run_wizard(env_path=env_file, example_path=example_file)
            assert ok is True
            assert env_file.exists()


# =============================================================================
# 2. CLI UI Shell & safe_int Stress Tests
# =============================================================================

def test_safe_int_stress():
    """Verify safe_int behaves robustly on extreme inputs and unicode."""
    assert safe_int(None) is None
    assert safe_int("") is None
    assert safe_int("   ") is None
    assert safe_int("abc") is None
    assert safe_int("²") is None
    assert safe_int("1.5") is None
    assert safe_int("\x00") is None
    assert safe_int("42") == 42
    assert safe_int("  -10 \n") == -10
    assert safe_int(100) == 100
    # Test extreme overflow string safely
    huge = "9" * 100000
    # Safe int parses or catches ValueError/OverflowError depending on sys.set_int_max_str_digits
    res = safe_int(huge)
    assert res is None or isinstance(res, int)


def test_cli_ui_keyboard_interrupt_in_sub_prompts():
    """Verify KeyboardInterrupt inside sub-commands cancels the command without crashing the CLI loop."""
    ui = CliUI(sound_alert=False)
    ui.controller = MagicMock()
    ui._appuntamenti_cache = [{"prestazione": "Visita Oculistica", "data_ora": "20/12/2026", "codice": "OC123"}]

    # 1. Type 'anticipa', sub-prompt raises KeyboardInterrupt
    # 2. Type 'status' (should still execute normally)
    # 3. Type 'exit' to terminate CLI cleanly
    command_sequence = [
        "anticipa",
        KeyboardInterrupt(),
        "status",
        "exit",
    ]

    def input_mock(prompt=""):
        if not command_sequence:
            return "exit"
        val = command_sequence.pop(0)
        if isinstance(val, Exception):
            raise val
        return val

    with patch("builtins.input", side_effect=input_mock):
        ui.run()

    ui.controller.status_text.assert_called_once()
    assert ui._stop_event.is_set()


def test_cli_ui_subcommand_unexpected_exception_resilience():
    """Verify that an unexpected exception in a command doesn't crash the interactive shell loop."""
    ui = CliUI(sound_alert=False)
    ui.controller = MagicMock()
    # Simulate controller method raising an unexpected error
    ui.controller.get_appuntamenti.side_effect = RuntimeError("Simulated internal database crash!")

    inputs = ["appuntamenti", "status", "exit"]
    with patch("builtins.input", side_effect=inputs):
        ui.run()

    # The shell survived the RuntimeError from 'appuntamenti' and proceeded to execute 'status' and 'exit'
    ui.controller.status_text.assert_called_once()
    assert ui._stop_event.is_set()


def test_cli_ui_extreme_arguments_and_unicode_digits():
    """Verify commands with unicode digits, invalid symbols, and out-of-range indices."""
    ui = CliUI(sound_alert=False)
    ui.controller = MagicMock()
    ui._appuntamenti_cache = [{"prestazione": "Visita Dermatologica", "data_ora": "15/11/2026", "codice": "D123"}]

    stress_inputs = [
        "ics ²",
        "ics -5",
        "ics 999999999999999999999999999999999999999999999999",
        "ics \x00",
        "anticipa ²",
        "anticipa -1",
        "anticipa 999999999999999999999999999999999999999999999999",
        "blacklist !@#$%^&*()",
        "exit",
    ]

    with patch("builtins.input", side_effect=stress_inputs):
        ui.run()

    assert ui._stop_event.is_set()


def test_cli_ui_rapid_concurrent_notifications():
    """Verify thread-safety when multiple background threads bombard the CLI UI with notifications."""
    ui = CliUI(sound_alert=False)
    ui.controller = MagicMock()

    errors: list[Exception] = []

    def notify_worker():
        try:
            for i in range(100):
                ui.notify(f"<b>Notifica #{i}</b> da worker thread")
                ui.send_file(f"data/file_{i}.ics", caption="Caption test")
                ui.send_photo(f"data/photo_{i}.png", caption="Photo test")
                ui.edit_message("Proposta scaduta", [])
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=notify_worker) for _ in range(5)]
    for t in threads:
        t.start()

    # While threads are sending notifications, run commands through the UI
    inputs = ["status", "help", "poll", "pause", "resume", "exit"]
    with patch("builtins.input", side_effect=inputs):
        ui.run()

    for t in threads:
        t.join(timeout=5)

    assert not errors, f"Concurrent notification errors encountered: {errors}"


def test_strip_html_stress():
    """Verify strip_html handles deeply nested, unclosed, and massive text without hanging or crashing."""
    massive_text = "<b>Unclosed tag " + ("<a href='http://example.com'>link</a> " * 1000) + "end"
    res = strip_html(massive_text)
    assert len(res) > 0
    assert "<a href=" not in res

    weird_tags = "<><<<<>>><a href=\"test\">label</a><script>evil()</script>&quot;&amp;&apos;"
    res2 = strip_html(weird_tags)
    assert "label (test)" in res2
    assert '"' in res2
    assert "&" in res2
    assert "'" in res2
