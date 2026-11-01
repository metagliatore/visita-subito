"""Audio notification helpers for terminal/CLI mode.

Provides cross-platform sound alerts (Windows winsound, Linux PulseAudio/PipeWire/Canberra,
and generic terminal bell as fallback).
Configured via SOUND_ALERT environment variable (default: true).
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

log = logging.getLogger(__name__)

# Sound candidate paths on Linux/Freedesktop systems
LINUX_SOUND_CANDIDATES: dict[str, list[str]] = {
    "slot_found": [
        "/usr/share/sounds/freedesktop/stereo/message-new-instant.oga",
        "/usr/share/sounds/freedesktop/stereo/bell.oga",
        "/usr/share/sounds/gnome/default/alerts/glass.ogg",
        "/usr/share/sounds/gnome/default/alerts/drip.ogg",
    ],
    "alert": [
        "/usr/share/sounds/freedesktop/stereo/message-new-instant.oga",
        "/usr/share/sounds/freedesktop/stereo/bell.oga",
    ],
    "success": [
        "/usr/share/sounds/freedesktop/stereo/complete.oga",
        "/usr/share/sounds/freedesktop/stereo/message-new-instant.oga",
    ],
    "error": [
        "/usr/share/sounds/freedesktop/stereo/dialog-warning.oga",
        "/usr/share/sounds/freedesktop/stereo/dialog-error.oga",
    ],
}


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
            return _play_linux_sound(kind)
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


def _find_linux_sound_file(kind: str) -> str | None:
    """Resolve sound file path on Linux."""
    custom_path = os.environ.get("SOUND_ALERT_FILE", "").strip()
    if custom_path and Path(custom_path).is_file():
        return custom_path

    candidates = LINUX_SOUND_CANDIDATES.get(kind, LINUX_SOUND_CANDIDATES.get("slot_found", []))
    for p in candidates:
        if Path(p).is_file():
            return p
    return None


def _play_linux_sound(kind: str) -> bool:
    """Linux audio playback via PulseAudio/PipeWire/Canberra, with bell fallback."""
    try:
        sound_file = _find_linux_sound_file(kind)
        if sound_file:
            # 1. Try paplay (PulseAudio / PipeWire standard audio player)
            if shutil.which("paplay"):
                subprocess.Popen(["paplay", sound_file], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True

            # 2. Try pw-play (PipeWire native audio player)
            if shutil.which("pw-play"):
                subprocess.Popen(["pw-play", sound_file], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True

            # 3. Try canberra-gtk-play
            if shutil.which("canberra-gtk-play"):
                subprocess.Popen(["canberra-gtk-play", "-f", sound_file], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True

            # 4. Try aplay (for wav files)
            if sound_file.endswith(".wav") and shutil.which("aplay"):
                subprocess.Popen(["aplay", "-q", sound_file], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True

        # If no file found or no sound server tool, fallback to terminal bell
        return _play_terminal_bell(kind)
    except Exception as e:
        log.debug("Linux sound playback error: %s", e)
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

