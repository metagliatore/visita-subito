"""Test del flusso di SPOSTAMENTO (RescheduleFlow) partendo da 'I miei appuntamenti'.

A differenza del flusso 'new' (ricerca disponibilità da ricetta), qui si lavora
su un appuntamento GIÀ PRENOTATO:
  1. login SPID (SielteID)
  2. vai in 'Gestisci Prenotazioni' -> 'I miei appuntamenti'
  3. apri il Dettaglio dell'appuntamento -> Anticipa/Posticipa
  4. (modale completa dati) -> Dove/Quando -> ricerca
  5. estrai gli slot disponibili
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.browser import Browser, BrowserSettings
from core.config import Config, _load_dotenv
from core import selectors
from core.session import SessionManager
from flows.reschedule import RescheduleFlow
from services.approval import ApprovalQueue
from services.telegram_bot import TelegramBot
from state.store import Store

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
log = logging.getLogger("e2e-resched")


def build_browser(cfg, headless=False):
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
    from selenium.webdriver.common.by import By

    d = browser.start()
    d.get(selectors.LOGIN_SPID["url_accedi"])
    time.sleep(4)
    if "/areaprivata/" in d.current_url or "/prenotaonline/" in d.current_url:
        log.info("Già autenticati: %s", d.current_url[:60])
        return d
    try:
        if "idpcwrapper" in d.current_url:
            try:
                d.execute_script("var a=document.querySelector('[spid-idp-button], a.button-spid, .pulsante-spid');if(a)a.click();return !!a;")
                time.sleep(1)
            except Exception:  # noqa: BLE001
                pass
            boxes = d.find_elements(By.CSS_SELECTOR, "a.home-box-fornitore")
            target = None
            for b in boxes:
                if "Sielte" in (b.text or "") or "Sielte" in (b.get_attribute("title") or ""):
                    target = b; break
            if target is None and len(boxes) > 11:
                target = boxes[10]
            if target:
                d.execute_script("arguments[0].click();", target)
                log.info("Clic SielteID")
    except Exception as e:  # noqa: BLE001
        log.warning("Selezione IdP: %s", e)
    time.sleep(4)
    if "identity.sieltecloud.it" in d.current_url:
        try:
            d.find_element(By.ID, "username").send_keys(username)
            d.find_element(By.ID, "password").send_keys(password)
            d.find_elements(By.ID, "autorizza")[0].click()
            log.info("Credenziali inserite, Prosegui")
        except Exception as e:  # noqa: BLE001
            log.warning("loginform: %s", e)
    time.sleep(4)
    try:
        if "identity.sieltecloud.it" in d.current_url:
            d.execute_script("useNotify();")
            log.info("Scelto metodo notifica push (approva sull'app!)")
    except Exception:  # noqa: BLE001
        pass
    log.info("⏳ Approva la notifica sull'app MySielteID...")
    for _ in range(36):
        time.sleep(5)
        try:
            if "identity.sieltecloud.it" in d.current_url and "accept" in d.page_source:
                d.execute_script("var f=document.querySelector('form#piLoginForm');if(f){var b=f.querySelector('button[type=submit]');if(b)b.click();}")
                time.sleep(3)
        except Exception:  # noqa: BLE001
            pass
        if "/areaprivata/" in d.current_url or "/prenotaonline/" in d.current_url:
            sess.save(d)
            log.info("✅ Autenticato! %s", d.current_url[:60])
            return d
    raise RuntimeError("Timeout attesa approvazione SielteID")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--provincia", default="MILANO CITTA'")
    parser.add_argument("--data-dal", default="")
    parser.add_argument("--ricetta", default="VISITA SPECIALISTICA")
    args = parser.parse_args()

    cfg = Config.load()
    _load_dotenv()
    import os
    user = os.environ.get("SIELTE_USERNAME", "")
    pwd = os.environ.get("SIELTE_PASSWORD", "")

    bot = fake_bot()
    browser = build_browser(cfg, headless=False)
    sess = SessionManager(browser, Path("data/cookies"))
    queue = ApprovalQueue(Path("data/approval.json"), timeout_seconds=120)
    store = Store(Path("data/state.json"))

    if not user or not pwd:
        log.error("Serve SIELTE_USERNAME e SIELTE_PASSWORD nel .env")
        return 1
    d = relogin_sielte_and_wait(browser, sess, user, pwd)

    # CHIUDI SUBITO la modale 'Informazioni' (avviso truffe SMS) dopo il login
    try:
        from selenium.webdriver.common.by import By
        import time as _t
        # su homepage area privata o prenotaonline può esserci la modale info
        _t.sleep(2)
        d.execute_script(
            "var el=document.querySelector('button[ng-click*=\"messaggiCtrl.close\"]');if(el)el.click();return !!el;")
        log.info("tentata chiusura modale info post-login")
    except Exception as e:  # noqa: BLE001
        log.debug("chiusura modale info post-login: %s", e)

    # ---- Flusso spostamento: parte da 'I miei appuntamenti' ----
    monitor = {
        "id": "e2e-resched",
        "type": "reschedule",
        "ricetta": args.ricetta,
        "criteri": {"province": [args.provincia], "data_dal": args.data_dal,
                     "giorni": [], "escludi_giorni": [], "fascia": ""},
    }
    flow = RescheduleFlow(monitor, browser, queue, bot, store)
    log.info("=== FLUSSO SPOSTAMENTO (dai miei appuntamenti) ===")

    # 0) naviga alla SPA prenotaonline (dove c'è il menu Gestisci Prenotazioni)
    try:
        d.get("https://www.fascicolosanitario.regione.lombardia.it/prenotaonline/riservata")
        time.sleep(5)
        log.info("Navigato a prenotaonline: %s", d.current_url[:60])
        # chiudi eventuale modale info (truffe SMS) ora presente
        d.execute_script(
            "var el=document.querySelector('button[ng-click*=\"messaggiCtrl.close\"]');if(el)el.click();return !!el;")
        time.sleep(2)
    except Exception as e:  # noqa: BLE001
        log.error("navigazione prenotaonline: %s", e)

    # 1) Gestisci Prenotazioni -> I miei appuntamenti
    try:
        menu = flow._find(flow.sel["menu_gestisci"])
        if menu is not None:
            flow._click_el(menu)
            log.info("click Gestisci Prenotazioni")
            time.sleep(3)
    except Exception as e:  # noqa: BLE001
        log.error("menu gestisci: %s", e)
        return 1

    # 2) click Dettaglio dell'appuntamento
    try:
        det = flow._find(flow.sel["btn_dettaglio"])
        if det is None:
            log.warning("nessun dettaglio trovato; url=%s", d.current_url[:80])
            # cerca se siamo su 'I miei appuntamenti' con la card
            cards = d.execute_script(
                "return document.body.innerText.indexOf('I miei appuntamenti')>-1 ? 'ON-APPUNTAMENTI' : 'NOT-ON-APPUNTAMENTI'")
            log.warning("pagina corrente: %s", cards)
            return 1
        flow._click_el(det)
        log.info("click Dettaglio")
        time.sleep(3)
    except Exception as e:  # noqa: BLE001
        log.error("dettaglio: %s", e)
        return 1

    # 3) Anticipa/Posticipa
    try:
        rip = flow._find(flow.sel["modal_dettaglio"]["btn_riprenota"])
        if rip is None:
            log.warning("nessun pulsante Anticipa/Posticipa (appuntamento non gestibile?)")
            return 1
        flow._click_el(rip)
        log.info("click Anticipa/Posticipa")
        time.sleep(3)
    except Exception as e:  # noqa: BLE001
        log.error("riprenota: %s", e)
        return 1

    # 4) modale completa dati
    try:
        flow._gestisci_completa_dati(no=True)
        log.info("modale completa dati gestita")
        time.sleep(3)
    except Exception as e:  # noqa: BLE001
        log.error("completa dati: %s", e)

    # 5) Dove/Quando + ricerca
    try:
        flow._compila_dove_quando_e_cerca()
        log.info("compila dove/quando + ricerca fatta")
        time.sleep(8)
        # chiudi eventuale modale info che copre i risultati
        closed = flow._chiudi_modale_info()
        log.info("modale info chiusa: %s", closed)
        time.sleep(3)
        # diagnostica pagina dopo ricerca
        try:
            body = d.execute_script("return document.body.innerText.substring(0,1200)")
            log.info("PAGINA dopo ricerca prima 1200:\n %s", body[:1200])
        except Exception as ex:  # noqa: BLE001
            log.warning("dump: %s", ex)
    except Exception as e:  # noqa: BLE001
        log.error("dove/quando: %s", e)

    # 6) estrai slot
    try:
        slots = flow.extract_slots()
        log.info("✅ Slot per spostamento trovati: %d", len(slots))
        for s in slots[:8]:
            log.info("   - %s %s | %s | %s", s.date_str, s.time_str,
                     getattr(s, "extra", {}).get("azienda", ""),
                     getattr(s, "extra", {}).get("comune", ""))
    except Exception as e:  # noqa: BLE001
        import traceback; traceback.print_exc()
        log.error("extract: %s", e)

    try:
        input("Premi INVIO per chiudere il test...")
    except Exception:  # noqa
        pass
    browser.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
