"""Audio notification helpers for terminal/CLI mode.

Provides cross-platform sound alerts (Windows winsound, Linux/macOS terminal bell).
Configured via SOUND_ALERT environment variable (default: true).
"""
from __future__ import annotations

import logging
import os
import sys
import time

log = logging.getLogger(__name__)


def is_sound_enabled(override: bool | None = None) -> bool:
    """Check if sound alerts are enabled."""
    if override is not None:
        return bool(override)
    val = os.environ.get("SOUND_ALERT", "true").strip().lower()
    return val in ("1", "true", "yes", "si", "on")


def play_alert_sound(kind: str = "alert", enabled: bool | None = None) -> bool:
    """Play an audible notification sound.

    Args:
        kind: Type of sound alert: 'slot_found', 'alert', 'success', 'error'.
        enabled: Optional explicit override for sound enablement.

    Returns:
        True if sound was played successfully, False otherwise.
    """
    if not is_sound_enabled(enabled):
        return False

    try:
        if sys.platform == "win32":
            return _play_windows_sound(kind)
        else:
            return _play_terminal_bell(kind)
    except Exception as e:
        log.debug("Sound notification failed: %s", e)
        return False


def _play_windows_sound(kind: str) -> bool:
    """Windows sound playback using standard library winsound."""
    try:
        import winsound

        if kind in ("slot_found", "alert"):
            # Multi-tone beep sequence for urgent attention
            try:
                winsound.Beep(880, 250)
                time.sleep(0.05)
                winsound.Beep(1320, 400)
            except Exception:
                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            return True

        if kind == "success":
            try:
                winsound.MessageBeep(winsound.MB_OK)
            except Exception:
                winsound.Beep(1000, 200)
            return True

        if kind == "error":
            try:
                winsound.MessageBeep(winsound.MB_ICONHAND)
            except Exception:
                winsound.Beep(440, 300)
            return True

        # Default fallback
        winsound.MessageBeep(winsound.MB_ICONASTERISK)
        return True
    except Exception as e:
        log.debug("winsound playback error: %s", e)
        return _play_terminal_bell(kind)


def _play_terminal_bell(kind: str) -> bool:
    """Generic terminal bell fallback (ANSI BEL character)."""
    try:
        repeat = 2 if kind in ("slot_found", "alert") else 1
        for _ in range(repeat):
            sys.stdout.write("\a")
            sys.stdout.flush()
            if repeat > 1:
                time.sleep(0.15)
        return True
    except Exception as e:
        log.debug("Terminal bell failed: %s", e)
        return False

