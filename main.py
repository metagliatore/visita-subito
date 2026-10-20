#!/usr/bin/env python3
"""Entrypoint: launches the monitor in poller and/or Telegram bot mode.

Usage:
    python main.py            # poller + bot (blocking bot in foreground)
    python main.py --nobot    # poller only (runs bot in another process)

Config via env (especially for Docker):
    TG_TOKEN, TG_CHAT_ID, TG_CHAT_ID_SECONDARY
"""
from __future__ import annotations

import logging
import os
import sys

from core.config import Config, section, env_token, get_base_dir
from services.scheduler import Controller
from services.telegram_bot import TelegramBot


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

    tg = section(cfg, "telegram")
    bot = TelegramBot(
        token=env_token("TG_TOKEN", tg.get("token", "")),
        chat_id=env_token("TG_CHAT_ID", tg.get("chat_id", "")),
        chat_id_secondary=env_token("TG_CHAT_ID_SECONDARY", tg.get("chat_id_secondary", "")),
    )

    if not (bot.token and bot.chat_ids):
        log.warning("Telegram non configurato: funziona solo senza notifiche.")

    ctl = Controller(cfg, bot)
    ctl.run(run_bot=run_bot)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
