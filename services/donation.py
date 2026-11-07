"""Donation and project support messaging service.

Provides configurable donation prompts (e.g. Buy Me a Coffee, PayPal.me, Ko-fi)
sent upon successful booking confirmation on Telegram or via the /dona command.
"""
from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger(__name__)


def get_donation_config(settings: dict | None = None) -> dict[str, Any]:
    """Retrieve donation configuration merging environment variables and settings.yaml.

    Environment variables:
      - DONATION_URL: base donation URL (e.g. Buy Me a Coffee, PayPal.me, Ko-fi)
      - DONATION_ENABLED: "true" / "false" (default: true if DONATION_URL is set)
      - DONATION_URL_2EUR, DONATION_URL_3EUR, DONATION_URL_5EUR: optional direct tier URLs
    """
    cfg = (settings or {}).get("donations", {})

    url = (os.environ.get("DONATION_URL", "").strip() or cfg.get("url", "") or "").strip()
    enabled_raw = os.environ.get("DONATION_ENABLED", "").strip()
    if enabled_raw != "":
        enabled = enabled_raw.lower() in ("true", "1", "yes", "on")
    else:
        enabled = cfg.get("enabled", True) if url else False

    url_2eur = (os.environ.get("DONATION_URL_2EUR", "").strip() or cfg.get("url_2eur", "") or "").strip()
    url_3eur = (os.environ.get("DONATION_URL_3EUR", "").strip() or cfg.get("url_3eur", "") or "").strip()
    url_5eur = (os.environ.get("DONATION_URL_5EUR", "").strip() or cfg.get("url_5eur", "") or "").strip()

    return {
        "enabled": bool(enabled and url),
        "url": url,
        "url_2eur": url_2eur,
        "url_3eur": url_3eur,
        "url_5eur": url_5eur,
    }


def build_donation_buttons(donation_cfg: dict) -> list[list[tuple[str, str]]]:
    """Build inline keyboard buttons for Telegram with direct donation tiers."""
    url = donation_cfg.get("url", "").strip()
    if not url:
        return []

    url_2 = donation_cfg.get("url_2eur", "").strip()
    url_3 = donation_cfg.get("url_3eur", "").strip()
    url_5 = donation_cfg.get("url_5eur", "").strip()

    # Smart synthesis for PayPal.me if specific tier URLs are not set
    # e.g. https://paypal.me/user -> https://paypal.me/user/2EUR
    if "paypal.me" in url.lower():
        base_pp = url.rstrip("/")
        if not url_2:
            url_2 = f"{base_pp}/2EUR"
        if not url_3:
            url_3 = f"{base_pp}/3EUR"
        if not url_5:
            url_5 = f"{base_pp}/5EUR"

    url_2 = url_2 or url
    url_3 = url_3 or url
    url_5 = url_5 or url

    return [
        [("☕ Offri un caffè (2€)", url_2), ("🥐 Caffè & Cornetto (3€)", url_3)],
        [("🍕 Offri una pizza (5€)", url_5), ("💝 Importo a piacere", url)],
    ]


def build_donation_message(slot: Any = None, monitor: dict | None = None) -> str:
    """Format the post-booking appreciation and donation prompt message."""
    return (
        "🎉 <b>Prenotazione confermata e salvata con successo!</b>\n\n"
        "💡 <i>Ti ho fatto risparmiare mesi di attesa?</i>\n"
        "Grazie a <b>VisitaSubito</b> hai anticipato la tua visita medica evitando mesi di liste d'attesa "
        "o il costo di una visita privata (spesso tra 100€ e 150€).\n\n"
        "Questo bot è un progetto <b>100% open source, gratuito e senza scopo di lucro</b>, "
        "sviluppato e costantemente aggiornato nel tempo libero.\n\n"
        "Se hai apprezzato il lavoro e vuoi premiare l'impegno, puoi <b>offrirmi un caffè</b> "
        "per sostenere lo sviluppo di nuove funzionalità! ☕"
    )


def invia_messaggio_donazione(
    bot: Any,
    slot: Any = None,
    monitor: dict | None = None,
    settings: dict | None = None,
) -> bool:
    """Send the donation message and inline buttons via the bot if configured."""
    cfg = get_donation_config(settings)
    if not cfg["enabled"]:
        log.debug("Notifica donazione non inviata: DONATION_URL non configurato o disabilitato")
        return False

    msg = build_donation_message(slot, monitor)
    buttons = build_donation_buttons(cfg)

    try:
        if hasattr(bot, "notify_buttons"):
            bot.notify_buttons(msg, buttons)
        elif hasattr(bot, "notify"):
            bot.notify(msg)
        return True
    except Exception as ex:  # noqa: BLE001
        log.warning("Errore durante l'invio del messaggio di donazione: %s", ex)
        return False
