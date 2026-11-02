"""Global pytest fixtures and test environment configuration."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def silence_sound_alerts_in_tests(monkeypatch):
    """Disable sound alerts globally during all test runs to ensure quiet execution."""
    monkeypatch.setenv("SOUND_ALERT", "false")

