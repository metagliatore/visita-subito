"""Test end-to-end del flusso reale usando le classi del progetto (non eval remoti).

Avvio:
    python study/test_e2e.py --login        # fa il login SPID manuale se serve
    python study/test_e2e.py                # riusa la sessione salvata

Fase 1: NewBookingFlow — apre la ricetta, compila Dove/Quando, cerca ed estrae
        gli slot disponibili (NON prenota, si ferma alla lista).
Fase 2: (opzionale) RescheduleFlow — prova ad aprire dettaglio/riprenota.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.browser import Browser, BrowserSettings
from core.config import Config
from core import selectors
from core.session import SessionManager
from flows.new_booking import NewBookingFlow
from services.approval import ApprovalQueue
from services.telegram_bot import TelegramBot
from state.store import Store

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
log = logging.getLogger("e2e")


def build_browser(cfg, headless=True):
    br = cfg.settings.get("browser", {})
    return Browser(BrowserSettings(
        binary_path=br.get("binary_path", ""),
        headless=headless,
        window_size=tuple(br.get("window_size", [1400, 950])),
    ))


def fake_bot():
    class _B:
        def __init__(self):
            self.controller = None
            self.messages = []
        def notify(self, t):
            self.messages.append(t)
            print("  [TG]", t.split("\n")[0])
        def send_file(self, p, caption=""):
            print("  [TG-FILE]", p, "-", caption)
    return _B()


def relogin_sielte_and_wait(browser, sess, username, password):
    """Ottiene una sessione autenticata: usa i cookie oppure guidato via
    SielteID (finestra visibile) fino al consenso. L'OTP push va approvato
    sull'app dall'utente; si attende poi il ritorno all'area privata.
    """
    import time
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    d = browser.start()
    # 1) apri la pagina di accesso (va su SPID select se non loggato)
    d.get(selectors.LOGIN_SPID["url_accedi"])
    time.sleep(4)

    # se già autenticati -> fatto
    if "/areaprivata/" in d.current_url or "/prenotaonline/" in d.current_url:
        log.info("Già autenticati: %s", d.current_url[:60])
        return d

    # 2) siamo su IdPC: apri il dropdown "Entra con SPID" e seleziona SielteID
    try:
        if "idpcwrapper" in d.current_url:
            # apre il menu dei provider
            try:
                d.execute_script(
                    "var a=document.querySelector('[spid-idp-button], a.button-spid, .pulsante-spid');"
                    "if(a)a.click(); return !!a;")
                time.sleep(1)
            except Exception as e:  # noqa: BLE001
                log.debug("dropdown spid: %s", e)
            boxes = d.find_elements(By.CSS_SELECTOR, "a.home-box-fornitore")
            target = None
            for b in boxes:
                if "Sielte" in (b.text or "") or "Sielte" in (b.get_attribute("title") or ""):
                    target = b; break
            if target is None and len(boxes) > 11:
                target = boxes[10]
            if target is None:
                log.error("Non trovo il provider Sielte; apri tu il menu e clicca Sielte")
                input("Seleziona manualmente Sielte nella finestra, poi INVIO...")
            else:
                d.execute_script("arguments[0].click();", target)
                log.info("Clic SielteID")
    except Exception as e:  # noqa: BLE001
        log.warning("Selezione IdP: %s", e)
    time.sleep(4)

    # 3) loginform: username+password
    if "identity.sieltecloud.it" in d.current_url:
        try:
            u = d.find_element(By.ID, "username")
            u.clear(); u.send_keys(username)
            p = d.find_element(By.ID, "password")
            p.clear(); p.send_keys(password)
            btns = d.find_elements(By.ID, "autorizza")
            if btns: btns[0].click()
            log.info("Credenziali inserite, Prosegui")
        except Exception as e:  # noqa: BLE001
            log.warning("loginform: %s", e)

    # 4) scelta metodo: notifica push
    time.sleep(4)
    try:
        if "identity.sieltecloud.it" in d.current_url:
            d.execute_script("useNotify();")
            log.info("Scelto metodo notifica push (approva sull'app!)")
    except Exception as e:  # noqa: BLE001
        log.warning("metodo otp: %s", e)

    # ATTENDI che l'utente approvi sull'app e torni autenticato (max ~3 min)
    log.info("⏳ Approva la notifica sull'app MySielteID...")
    for _ in range(36):
        time.sleep(5)
        # consenso dati
        try:
            if "identity.sieltecloud.it" in d.current_url and "accept" in d.page_source:
                d.execute_script(
                    "var f=document.querySelector('form#piLoginForm');"
                    "if(f){var b=document.querySelector('form#piLoginForm button[type=submit]');if(b)b.click();}")
                time.sleep(3)
        except Exception:  # noqa: BLE001
            pass
        if "/areaprivata/" in d.current_url or "/prenotaonline/" in d.current_url:
            sess.save(d)
            log.info("✅ Autenticato! %s", d.current_url[:60])
            return d
    raise RuntimeError("Timeout nell'attesa dell'approvazione SielteID")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--ricetta", default="VISITA SPECIALISTICA")
    ap.add_argument("--provincia", default="MILANO CITTA'")
    ap.add_argument("--data-dal", default="")
    args = ap.parse_args()

    cfg = Config.load()
    monitor = {
        "id": "e2e-test",
        "type": "new",
        "ricetta": args.ricetta,
        "criteri": {
            "province": [args.provincia],
            "data_dal": args.data_dal,
            "giorni": [], "escludi_giorni": [], "fascia": "",
        },
    }

    bot = fake_bot()
    browser = build_browser(cfg, headless=args.headless)
    sess = SessionManager(browser, Path("data/cookies"))
    queue = ApprovalQueue(Path("data/approval.json"), timeout_seconds=120)
    store = Store(Path("data/state.json"))

    # credenziali da .env (usa il loader già in core.config)
    import os
    from core.config import _load_dotenv
    _load_dotenv()
    user = os.environ.get("SIELTE_USERNAME", "")
    pwd = os.environ.get("SIELTE_PASSWORD", "")

    # garantirsi un driver autenticato (usa cookie se validi, altrimenti login)
    d = None
    # Abilita SEMPRE il login guidato SielteID: i cookie non bastano a ripristinare
    # la sessione SPID (legata al browser vivo).
    if not user or not pwd:
        log.error("Serve SIELTE_USERNAME e SIELTE_PASSWORD nel .env")
        return 1
    d = relogin_sielte_and_wait(browser, sess, user, pwd)

    # ----- Fase 1: NewBookingFlow -----
    flow = NewBookingFlow(monitor, browser, queue, bot, store)
    log.info("=== FASE 1: NewBookingFlow (ricerca disponibilità) ===")
    try:
        flow.load_page()
        # se la ricetta non si è aperta, load_page ritorna senza fare nulla
        slots = flow.extract_slots()
        log.info("✅ Slot trovati: %d", len(slots))
        for s in slots[:6]:
            log.info("   - %s %s | %s | %s", s.date_str, s.time_str,
                     s.extra.get("azienda", ""), s.extra.get("comune", ""))
        if not slots:
            flow._chiudi_modale_info()
            time.sleep(2)
            slots = flow.extract_slots()
            log.info("   (dopo chiusura modale info) slot: %d", len(slots))
    except Exception as e:  # noqa: BLE001
        import traceback; traceback.print_exc()
        log.error("Errore in Fase 1: %s", e)

    try:
        input("Premi INVIO per chiudere il test...")
    except Exception:  # noqa
        pass
    browser.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
