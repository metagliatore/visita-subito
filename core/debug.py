"""Centralized debug dump management (PNG screenshots and HTML source code).

Allows setting the target folder via the DEBUG_DUMP_DIR environment variable (or settings.yaml).
Assigns a traceable reference with a timestamp and descriptive tag to each dump,
generating a ready-to-use notification text for Telegram and application logs.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

DEFAULT_DEBUG_DIR = "data/debug"


def get_debug_dir() -> Path:
    """Returns the debug directory configured via env (DEBUG_DUMP_DIR) or default."""
    d = os.environ.get("DEBUG_DUMP_DIR", "").strip() or DEFAULT_DEBUG_DIR
    p = Path(d)
    p.mkdir(parents=True, exist_ok=True)
    return p


def salva_debug_dump(
    driver: Any,
    tag: str = "error",
    prefix: str = "",
) -> Optional[dict]:
    """Saves a PNG screenshot and HTML source of the current page.

    Returns a dict with:
      - 'ref': unique identifier (e.g. '20261014_205500_reschedule_1_card_not_found')
      - 'png_path': Path of screenshot (.png)
      - 'html_path': Path of HTML (.html)
      - 'dir': output directory
      - 'message': informative notification text for Telegram/logs
    or None if driver is unavailable or capture fails.
    """
    if driver is None:
        return None
    try:
        dump_dir = get_debug_dir()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_tag = re.sub(r"[^A-Za-z0-9_\-]+", "_", tag).strip("_") or "dump"
        if prefix:
            safe_prefix = re.sub(r"[^A-Za-z0-9_\-]+", "_", prefix).strip("_")
            ref_id = f"{ts}_{safe_prefix}_{safe_tag}"
        else:
            ref_id = f"{ts}_{safe_tag}"

        png_file = dump_dir / f"{ref_id}.png"
        html_file = dump_dir / f"{ref_id}.html"

        # Save HTML
        try:
            src = getattr(driver, "page_source", "")
            html_text = src if isinstance(src, str) else str(src or "")
            html_file.write_text(html_text, encoding="utf-8")
        except Exception as e_html:
            log.debug("Errore salvataggio html dump %s: %s", ref_id, e_html)

        # Save Screenshot
        try:
            driver.save_screenshot(str(png_file))
        except Exception as e_scr:
            log.warning("Errore salvataggio screenshot dump %s: %s", ref_id, e_scr)
            return None

        msg_notifica = f"📸 Screenshot di debug salvato: {ref_id}.png (cartella: {dump_dir})"
        log.info("Salvato debug dump: %s in %s", ref_id, dump_dir)
        return {
            "ref": ref_id,
            "png_path": png_file,
            "html_path": html_file,
            "dir": str(dump_dir),
            "message": msg_notifica,
        }
    except Exception as e:
        log.warning("Impossibile salvare dump di debug (%s): %s", tag, e)
        return None
