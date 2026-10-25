#!/usr/bin/env python3
"""Entrypoint: launches the monitor in poller and/or Telegram bot or CLI terminal mode.

Usage:
    python main.py            # poller + UI (Telegram bot or CLI interactive shell)
    python main.py --nobot    # poller only (runs UI in another process)
    python main.py --wizard   # rerun configuration wizard

Config via env (especially for Docker):
    CONTROL_INTERFACE, TG_TOKEN, TG_CHAT_ID, SOUND_ALERT
"""
from __future__ import annotations

import logging
import os
import sys

from core.config import Config, section, env_token, get_base_dir
from services.scheduler import Controller


def setup_logging(cfg: Config) -> None:
    lconf = cfg.settings.get("logging", {})
    level = getattr(logging, lconf.get("level", "INFO").upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=[logging.StreamHandler()],
    )
    # Optional file logging
    logfile = lconf.get("file")
    if logfile:
        from pathlib import Path
        try:
            Path(logfile).parent.mkdir(parents=True, exist_ok=True)
            fh = logging.FileHandler(logfile, encoding="utf-8")
            fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
            logging.getLogger().addHandler(fh)
        except Exception as e:
            logging.warning("Impossibile aprire il file di log %s: %s", logfile, e)


def main(argv: list[str] | None = None) -> int:
    try:
        os.chdir(get_base_dir())
    except Exception:
        pass

    argv = argv if argv is not None else sys.argv[1:]
    run_bot = "--nobot" not in argv
    force_wizard = any(arg in argv for arg in ("--wizard", "--setup", "-w"))

    # Avvia la configurazione guidata se il file .env non esiste o se è forzata via argomenti
    from core.wizard import check_or_run_wizard
    if not check_or_run_wizard(force=force_wizard):
        target_env = get_base_dir() / ".env"
        if force_wizard or not target_env.exists() or target_env.stat().st_size == 0:
            return 1

    cfg = Config.load()

    setup_logging(cfg)
    log = logging.getLogger("main")

    # Detect interface mode: Telegram vs CLI Terminal
    ctrl_interface = os.environ.get("CONTROL_INTERFACE", "").strip().lower()
    tg = section(cfg, "telegram")
    tg_token = env_token("TG_TOKEN", tg.get("token", ""))
    tg_chat_id = env_token("TG_CHAT_ID", tg.get("chat_id", ""))

    if ctrl_interface == "cli" or not (tg_token and tg_chat_id):
        log.info("Avvio in modalita Terminale / CLI (senza Telegram)")
        from services.cli_ui import CliUI
        from services.sound import is_sound_enabled
        ui = CliUI(sound_alert=is_sound_enabled())
    else:
        log.info("Avvio con interfaccia Telegram Bot")
        from services.telegram_bot import TelegramBot
        ui = TelegramBot(
            token=tg_token,
            chat_id=tg_chat_id,
            chat_id_secondary=env_token("TG_CHAT_ID_SECONDARY", tg.get("chat_id_secondary", "")),
        )

    ctl = Controller(cfg, ui)
    ctl.run(run_bot=run_bot)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
