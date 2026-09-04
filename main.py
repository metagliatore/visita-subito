#!/usr/bin/env python3
"""Entrypoint: avvia il monitor in modalità poller e/o bot Telegram.

Uso:
    python main.py            # poller + bot (bot bloccante in foreground)
    python main.py --nobot    # solo poller (avvia il bot in un altro processo)

Config via env (soprattutto per Docker):
    TG_TOKEN, TG_CHAT_ID, TG_CHAT_ID_SECONDARY
"""
from __future__ import annotations

import logging
import sys

from core.config import Config, section, env_token
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
    # eventuale file log
    logfile = lconf.get("file")
    if logfile:
        fh = logging.FileHandler(logfile, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
        logging.getLogger().addHandler(fh)


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    run_bot = "--nobot" not in argv

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
