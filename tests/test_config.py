import os
import pytest
from core.config import Config, _load_dotenv, env_token, section


def test_load_dotenv(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("TEST_KEY_123=VALORE_PROVA\n# Commento\nTEST_EMPTY=\n", encoding="utf-8")

    _load_dotenv(env_file)
    assert os.environ.get("TEST_KEY_123") == "VALORE_PROVA"


def test_config_active_monitors():
    cfg = Config(
        settings={"browser": {"headless": True}},
        monitors=[
            {"id": "m1", "enabled": True},
            {"id": "m2", "enabled": False},
            {"id": "m3"},  # Default enabled True
        ]
    )
    active = cfg.active_monitors
    assert len(active) == 2
    assert [m["id"] for m in active] == ["m1", "m3"]


def test_section():
    cfg = Config(settings={"session": {"keep_alive": 300}})
    assert section(cfg, "session") == {"keep_alive": 300}
    assert section(cfg, "missing") == {}


def test_env_token(monkeypatch):
    monkeypatch.setenv("MY_TEST_VAR", "env_value")
    assert env_token("MY_TEST_VAR", "yaml_value") == "env_value"

    monkeypatch.delenv("MY_TEST_VAR", raising=False)
    assert env_token("MY_TEST_VAR", "yaml_value") == "yaml_value"
    assert env_token("MY_NONEXISTENT_VAR", "") == ""
