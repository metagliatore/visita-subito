import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.wizard import (
    check_or_run_wizard,
    populate_env_content,
    prompt_choice,
    prompt_input,
    run_wizard,
)


def test_populate_env_content():
    template = (
        "# Header\n"
        "TG_TOKEN=\n"
        "TG_CHAT_ID=123\n"
        "# CIE_USERNAME=\n"
        "AUTH_METHOD=spid\n"
    )
    values = {
        "TG_TOKEN": "my-secret-token",
        "TG_CHAT_ID": "999999",
        "CIE_USERNAME": "CF123456",
        "NEW_CUSTOM_VAR": "custom_val",
    }
    result = populate_env_content(template, values)

    assert "TG_TOKEN=my-secret-token" in result
    assert "TG_CHAT_ID=999999" in result
    assert "CIE_USERNAME=CF123456" in result
    assert "AUTH_METHOD=spid" in result
    assert "NEW_CUSTOM_VAR=custom_val" in result
    assert "# Header" in result


def test_prompt_input_returns_input(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt: "utente_digitato")
    assert prompt_input("Inserisci valore", default="default_val") == "utente_digitato"


def test_prompt_input_returns_default_on_empty(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    assert prompt_input("Inserisci valore", default="default_val") == "default_val"


def test_prompt_input_secret_getpass(monkeypatch):
    monkeypatch.setattr("getpass.getpass", lambda prompt: "segreto123")
    assert prompt_input("Password", secret=True) == "segreto123"


def test_prompt_choice(monkeypatch):
    options = [("Opzione A", "val_a"), ("Opzione B", "val_b")]

    # Invio vuoto -> sceglie default (1)
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    assert prompt_choice("Scegli:", options, default=1) == "val_a"

    # Selezione numerica 2
    monkeypatch.setattr("builtins.input", lambda prompt: "2")
    assert prompt_choice("Scegli:", options, default=1) == "val_b"


def test_run_wizard_generates_file(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    example_file = tmp_path / ".env.example"
    example_file.write_text("TG_TOKEN=\nTG_CHAT_ID=\nAUTH_METHOD=spid\n", encoding="utf-8")

    inputs = [
        "bot123:TOKEN",      # TG_TOKEN
        "12345678",          # TG_CHAT_ID
        "",                  # TG_CHAT_ID_SECONDARY (vuoto)
        "1",                 # Metodo SPID SielteID
        "mario_rossi",       # SPID_USERNAME
        "password123",       # SPID_PASSWORD
        "1",                 # Modalità Notifica
        "1",                 # first_match
        "5",                 # 5 minuti
    ]
    input_iter = iter(inputs)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(input_iter))
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(input_iter))

    ok = run_wizard(env_path=env_file, example_path=example_file)
    assert ok is True
    assert env_file.exists()

    content = env_file.read_text(encoding="utf-8")
    assert "TG_TOKEN=bot123:TOKEN" in content
    assert "TG_CHAT_ID=12345678" in content
    assert "AUTH_METHOD=spid" in content
    assert "SPID_USERNAME=mario_rossi" in content
    assert "SPID_PASSWORD=password123" in content
    assert os.environ.get("TG_TOKEN") == "bot123:TOKEN"


def test_run_wizard_cie_qr_mode(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    example_file = tmp_path / ".env.example"
    example_file.write_text("TG_TOKEN=\nTG_CHAT_ID=\nAUTH_METHOD=spid\nCIE_MODE=app\n", encoding="utf-8")

    inputs = [
        "bot_cie:TOKEN",     # TG_TOKEN
        "87654321",          # TG_CHAT_ID
        "",                  # TG_CHAT_ID_SECONDARY
        "4",                 # Metodo CIE QR Code diretto
        "1",                 # first_match
        "10",                # 10 minuti
    ]
    input_iter = iter(inputs)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(input_iter))

    ok = run_wizard(env_path=env_file, example_path=example_file)
    assert ok is True
    content = env_file.read_text(encoding="utf-8")
    assert "AUTH_METHOD=cie" in content
    assert "CIE_MODE=qr" in content
    assert "POLL_INTERVAL_SECONDS=600" in content


def test_check_or_run_wizard_when_file_exists(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("TG_TOKEN=test\n", encoding="utf-8")

    with patch("core.wizard.run_wizard") as mock_run:
        res = check_or_run_wizard(env_path=env_file, force=False)
        assert res is True
        mock_run.assert_not_called()


def test_check_or_run_wizard_when_force(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("TG_TOKEN=test\n", encoding="utf-8")

    with patch("core.wizard.run_wizard", return_value=True) as mock_run:
        res = check_or_run_wizard(env_path=env_file, force=True)
        assert res is True
        mock_run.assert_called_once_with(env_file)


def test_check_or_run_wizard_missing_non_tty(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

    with patch("core.wizard.run_wizard") as mock_run:
        res = check_or_run_wizard(env_path=env_file, force=False)
        assert res is False
        mock_run.assert_not_called()
