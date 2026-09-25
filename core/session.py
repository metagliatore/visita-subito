"""Session Manager.

Gestisce il ciclo di vita della sessione SPID:
  1. Il login avviene UNA volta in modo MANUALE (finestra visibile -> OTP).
  2. I cookie vengono salvati su disco.
  3. Un keep-alive periodico tocca il portale per non far scadere la sessione.
  4. Se la sessione risulta scaduta, espone uno stato che fa scattare la
     notifica "serve re-login" via Telegram.
"""
from __future__ import annotations

import json
import logging
import pickle
import time
from pathlib import Path

from selenium import webdriver

log = logging.getLogger(__name__)

# Si consiglia di restare sullo stesso IP (VPS/PC stabile) per non invalidare
# la sessione SPID per cambio IP.


class SessionManager:
    def __init__(self, browser, cookie_dir: Path):
        self.browser = browser
        self.cookie_dir = Path(cookie_dir)
        self.cookie_dir.mkdir(parents=True, exist_ok=True)
        self.cookie_file = self.cookie_dir / "cookies.pkl"
        self.meta_file = self.cookie_dir / "meta.json"
        self.session_valid = False
        # callback di notifica (es. invia messaggi Telegram durante il login)
        self.on_notify = None

    # ---------------- persistenze ----------------
    def save(self, driver: webdriver.Chrome) -> None:
        cookies = driver.get_cookies()
        with open(self.cookie_file, "wb") as f:
            pickle.dump(cookies, f)
        with open(self.meta_file, "w", encoding="utf-8") as f:
            json.dump({"saved_at": time.time()}, f)
        log.info("Cookie salvati (%d) su %s", len(cookies), self.cookie_file)

    def load_cookies(self, driver: webdriver.Chrome) -> bool:
        if not self.cookie_file.exists():
            return False
        with open(self.cookie_file, "rb") as f:
            cookies = pickle.load(f)
        for c in cookies:
            try:
                driver.add_cookie(c)
            except Exception as e:  # noqa: BLE001
                log.debug("cookie skip %s: %s", c.get("name"), e)
        return bool(cookies)

    # ---------------- knobs ----------------
    def needs_login(self) -> bool:
        """True se non abbiamo ancora una sessione salvata."""
        return not self.cookie_file.exists()

    def is_autenticato(self, driver: webdriver.Chrome) -> bool:
        """True se il driver corrente è autenticato su area privata/prenotaonline."""
        url = (driver.current_url or "").split("?")[0]
        return ("/web/areaprivata/" in url) or ("/prenotaonline/" in url)

    def is_expired(self, max_idle_seconds: int) -> bool:
        if not self.meta_file.exists():
            return True
        try:
            meta = json.loads(self.meta_file.read_text(encoding="utf-8"))
            return (time.time() - meta.get("saved_at", 0)) > max_idle_seconds
        except Exception:  # noqa: BLE001
            return True

    def _riavvia_con_headless(self, headless: bool) -> None:
        """Se un driver e' gia' attivo ma con modalita' headless diversa,
        lo chiude per farne ripartire Chrome con la modalita' richiesta.
        (Browser.start() riusa il driver esistente: senza questo check un
        driver headless non verrebbe mai sostituito da uno visibile o viceversa)."""
        d = self.browser.driver
        if d is None:
            return
        if self.browser.settings.headless == headless:
            return
        log.info("riavvio Chrome: headless %s -> %s", self.browser.settings.headless, headless)
        self.browser.stop()

    # ---------------- azioni ----------------
    def relogin_manual(self, url: str) -> webdriver.Chrome:
        """Apre finestra VISIBILE: l'utente fa login + OTP a mano.

        Attende il completamento con polling su `is_autenticato` (funziona
        anche sotto nohup, dove `input()` legge /dev/null e salva i cookie
        prima che il login finisca). Se stdin e' un TTY e non e' configurato
        un timeout, domanda comunque conferma manuale.
        """
        prev = self.browser.settings.headless
        self.browser.settings.headless = False  # forza visibile
        self._riavvia_con_headless(False)
        try:
            driver = self.browser.start()
            driver.get(url)
            self._attendi_login_manuale(driver)
            self.save(driver)
            self.session_valid = True
            return driver
        finally:
            self.browser.settings.headless = prev

    def _attendi_login_manuale(self, driver, timeout_s: int = 600) -> None:
        """Attende (con polling ogni 5s) che il login manuale sia completato,
        ovvero che l'URL rientri nell'area privata. Lavora anche senza TTY."""
        import sys
        t0 = time.time()
        log.info("attesa login manuale (timeout %ss): controlla la finestra Chrome e il telefono", timeout_s)
        if self.on_notify:
            try:
                self.on_notify("🔑 Login manuale richiesto: completa il login nella finestra Chrome (SPID + OTP) e torna qui.")
            except Exception:  # noqa: BLE001
                pass
        while time.time() - t0 < timeout_s:
            if self.is_autenticato(driver):
                log.info("login manuale completato")
                return
            # se stdin e' un TTY, permette anche la conferma manuale classica
            if sys.stdin.isatty():
                try:
                    import select as _sel
                    r, _, _ = _sel.select([sys.stdin], [], [], 5)
                    if r:
                        line = sys.stdin.readline().strip()
                        if line.lower() in ("", "y", "yes", "si", "invio"):
                            break
                        continue
                except Exception:  # noqa: BLE001
                    time.sleep(5)
            else:
                time.sleep(5)
        if not self.is_autenticato(driver):
            log.warning("timeout attesa login manuale: l'URL '%s' non e' nell'area privata", driver.current_url)
            if self.on_notify:
                self.on_notify("⚠️ Timeout login manuale: la sessione non è stata verificata.")
            return
        # conferma da TTY: salva solo se davvero autenticati (check già passato sopra)


    def invalidate(self) -> None:
        """Marca la sessione come non valida/scaduta."""
        self.session_valid = False

    def keep_alive(self, driver: webdriver.Chrome, url: str) -> bool:
        """Tocca una pagina del portale per mantenere viva la sessione."""
        try:
            driver.get(url)
            time.sleep(3)
            if not self.is_autenticato(driver):
                self.session_valid = False
                log.warning("keep-alive: sessione scaduta (URL: %s)", driver.current_url)
                return False
            self.save(driver)
            self.session_valid = True
            log.debug("keep-alive ok (%s)", url)
            return True
        except Exception as e:  # noqa: BLE001
            self.session_valid = False
            log.warning("keep-alive fallito: %s", e)
            return False

    # ---------------- login SielteID automatico ----------------
    def relogin_sielte(self, username: str = "", password: str = "",
                       wait_otp_sec: int = 180) -> webdriver.Chrome:
        """Login automatico SielteID (finestra visibile): credenziali, scelta
        metodo notifica push, attesa approvazione OTP + consenso dati.

        Notifica su Telegram del login in corso / necessità di approvazione
        gestita dal chiamante. Ritorna il driver autenticato.
        """
        from selenium.webdriver.common.by import By
        from core import selectors

        prev = self.browser.settings.headless
        self.browser.settings.headless = False  # serve la finestra per l'OTP
        self._riavvia_con_headless(False)
        driver = self.browser.start()
        try:
            if self.on_notify:
                self.on_notify("🔑 Sessione scaduta: avvio il re-login SielteID...")
            driver.get(selectors.LOGIN_SPID["url_accedi"])
            time.sleep(4)
            if self.is_autenticato(driver):
                self.save(driver); self.session_valid = True
                return driver
            # pagina IdPC: apri dropdown + seleziona Sielte
            try:
                driver.execute_script("var a=document.querySelector('[spid-idp-button], a.button-spid, .pulsante-spid');if(a)a.click();")
                time.sleep(1)
                boxes = driver.find_elements(By.CSS_SELECTOR, "a.home-box-fornitore")
                target = None
                for b in boxes:
                    if "Sielte" in (b.text or ""):
                        target = b; break
                if target is None and len(boxes) > 11:
                    target = boxes[10]
                if target:
                    driver.execute_script("arguments[0].click();", target)
            except Exception:  # noqa: BLE001
                pass
            time.sleep(4)
            # login form
            if "identity.sieltecloud.it" in driver.current_url:
                try:
                    driver.find_element(By.ID, "username").send_keys(username)
                    driver.find_element(By.ID, "password").send_keys(password)
                    driver.find_elements(By.ID, "autorizza")[0].click()
                except Exception:  # noqa: BLE001
                    pass
            time.sleep(4)
            # AVVISO: notifica in arrivo PRIMA del submit push
            if self.on_notify:
                self.on_notify("📲 Sto per inviare la notifica SielteID: prepara il telefono e "
                               "approvala appena arriva!")
            # scelta metodo: notifica push
            try:
                if "identity.sieltecloud.it" in driver.current_url:
                    driver.execute_script("useNotify();")
            except Exception:  # noqa: BLE001
                pass
            # attesa approvazione (push) + consenso
            for _ in range(wait_otp_sec // 5):
                time.sleep(5)
                if "identity.sieltecloud.it" in driver.current_url and "accept" in driver.page_source:
                    try:
                        driver.execute_script("var f=document.querySelector('form#piLoginForm');if(f){var b=f.querySelector('button[type=submit]');if(b)b.click();}")
                        time.sleep(3)
                    except Exception:  # noqa: BLE001
                        pass
                if self.is_autenticato(driver):
                    self.save(driver)
                    self.session_valid = True
                    return driver
            raise TimeoutError("Timeout nell'attesa approvazione SielteID (approva la push!)")
        finally:
            self.browser.settings.headless = prev
