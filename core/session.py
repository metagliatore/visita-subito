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
        # callback di fallback interattivo su Telegram (notifica vs OTP)
        self.on_auth_fallback = None
        # callback per richiesta inserimento codice OTP
        self.on_otp_prompt = None

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
        if driver is None:
            return False
        try:
            url = (driver.current_url or "").split("?")[0].lower()
            if not url or "about:blank" in url:
                return False
            # Se siamo stati rimandati all'IdPC o a pagine di login, non siamo autenticati
            if any(k in url for k in ["idpcwrapper", "identity.", "login", "/sso"]):
                return False
            return ("/web/areaprivata/" in url) or ("/prenotaonline/" in url)
        except Exception:
            return False

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

    # ---------------- helper SielteID ----------------
    def _invia_notifica_sielte(self, driver: webdriver.Chrome) -> bool:
        """Tenta di selezionare o re-inviare la notifica push su SielteID."""
        from core import selectors
        sielte_sel = selectors.LOGIN_IDP_SIELTEID.get("scelta_metodo", {})
        try:
            driver.execute_script("if(typeof useNotify === 'function'){ useNotify(); return true; }")
            time.sleep(1)
            return True
        except Exception:  # noqa: BLE001
            pass
        return self._find_and_click(driver, sielte_sel.get("notifica", []))

    def _attiva_otp_sielte(self, driver: webdriver.Chrome) -> bool:
        """Passa alla schermata di inserimento codice OTP su SielteID."""
        from core import selectors
        sielte_sel = selectors.LOGIN_IDP_SIELTEID.get("scelta_metodo", {})
        try:
            driver.execute_script("if(typeof useAPP === 'function'){ useAPP(); return true; }")
            time.sleep(1)
            return True
        except Exception:  # noqa: BLE001
            pass
        return self._find_and_click(driver, sielte_sel.get("otp_app", []))

    def _inserisci_otp_sielte(self, driver: webdriver.Chrome, code: str) -> bool:
        """Compila e sottomette il codice OTP su SielteID."""
        from core import selectors
        sielte_sel = selectors.LOGIN_IDP_SIELTEID
        filled = self._find_and_fill(driver, sielte_sel.get("otp", []), code)
        if not filled:
            try:
                driver.execute_script(
                    "var f=document.querySelector('form#piLoginForm');"
                    "if(f){"
                    "  var inp=f.querySelector('input[type=password], input[name*=otp], input[type=number], input[type=text]:not([name=username])');"
                    "  if(inp){ inp.value = arguments[0]; inp.dispatchEvent(new Event('input')); return true; }"
                    "}"
                    "return false;", code)
                filled = True
            except Exception:  # noqa: BLE001
                pass
        time.sleep(1)
        clicked = self._find_and_click(driver, sielte_sel.get("btn_otp", []))
        if not clicked:
            try:
                driver.execute_script(
                    "var f=document.querySelector('form#piLoginForm');"
                    "if(f){ var b=f.querySelector('button[type=submit], input[type=submit]'); if(b) b.click(); else f.submit(); }")
                clicked = True
            except Exception:  # noqa: BLE001
                pass
        return filled

    def _clicca_consenso_sielte(self, driver: webdriver.Chrome) -> bool:
        """Tenta di confermare il consenso dati SPID SAML se presente."""
        from core import selectors
        sielte_sel = selectors.LOGIN_IDP_SIELTEID.get("consenso", {})
        try:
            if "identity.sieltecloud.it" in driver.current_url and ("accept" in driver.page_source or "Autorizza" in driver.page_source):
                driver.execute_script(
                    "var f=document.querySelector('form#piLoginForm');"
                    "if(f){var b=f.querySelector('button[type=submit], input[type=submit]');if(b)b.click();}")
                time.sleep(2)
                return True
        except Exception:  # noqa: BLE001
            pass
        return self._find_and_click(driver, sielte_sel.get("btn_autorizza", []))

    def _ottieni_scelta_fallback(self, auth_callback=None) -> str | None:
        """Chiede all'utente via Telegram (o TTY) se inviare notifica o immettere codice OTP."""
        if auth_callback:
            try:
                res = auth_callback("ask_action")
                if res:
                    return res
            except Exception as e:  # noqa: BLE001
                log.warning("auth_callback ask_action: %s", e)
        if self.on_auth_fallback:
            try:
                res = self.on_auth_fallback()
                if res:
                    return res
            except Exception as e:  # noqa: BLE001
                log.warning("on_auth_fallback: %s", e)

        # Fallback console se avviato con terminale interattivo (TTY)
        import sys
        if sys.stdin.isatty():
            try:
                print("\n[SPID SielteID] Notifica non approvata. Scegli:")
                print("  1) Invia notifica di nuovo")
                print("  2) Immetti codice OTP")
                print("Scelta [1/2]: ", end="", flush=True)
                ans = sys.stdin.readline().strip()
                if ans == "2":
                    return "otp"
                return "notify"
            except Exception:  # noqa: BLE001
                pass
        return None

    def _chiedi_e_inserisci_otp(self, driver: webdriver.Chrome, auth_callback=None) -> bool:
        """Chiede il codice OTP all'utente, lo compila e ne attende la verifica."""
        code = None
        if auth_callback:
            try:
                code = auth_callback("ask_otp")
            except Exception as e:  # noqa: BLE001
                log.warning("auth_callback ask_otp: %s", e)
        if not code and self.on_otp_prompt:
            try:
                code = self.on_otp_prompt("🔢 Inserisci il codice OTP generato dall'app SielteID:")
            except Exception as e:  # noqa: BLE001
                log.warning("on_otp_prompt: %s", e)

        # Fallback console se su TTY
        if not code:
            import sys
            if sys.stdin.isatty():
                try:
                    print("\n[SPID SielteID] Inserisci il codice OTP generato dall'app SielteID: ", end="", flush=True)
                    code = sys.stdin.readline().strip()
                except Exception:  # noqa: BLE001
                    pass

        if not code:
            log.warning("Nessun codice OTP fornito.")
            return False

        clean_code = str(code).replace(" ", "").replace("-", "").strip()
        log.info("Inserimento codice OTP nel browser...")
        if self.on_notify:
            self.on_notify("⏳ Inserisco il codice OTP nel portale...")

        self._inserisci_otp_sielte(driver, clean_code)

        for _ in range(3):
            time.sleep(3)
            self._clicca_consenso_sielte(driver)
            if self.is_autenticato(driver):
                return True

        if not self.is_autenticato(driver):
            if self.on_notify:
                self.on_notify("❌ Codice OTP non valido o autenticazione non riuscita.")
            return False

        return True

    # ---------------- login SielteID automatico ----------------
    def relogin_sielte(self, username: str = "", password: str = "",
                       wait_otp_sec: int = 180,
                       otp_mode: str = "notifica",
                       auth_callback=None) -> webdriver.Chrome:
        """Login automatico SielteID (finestra visibile): credenziali, scelta
        metodo notifica push, con fallback su Telegram per reinvio notifica o
        immissione codice OTP.
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
            if self.is_autenticato(driver):
                self.save(driver); self.session_valid = True
                return driver

            # ----------------------------------------------------
            # 2FA: Notifica Push con Fallback Interattivo (Telegram)
            # ----------------------------------------------------
            t0 = time.time()
            deadline = t0 + wait_otp_sec

            # Se l'utente ha configurato esplicitamente la modalità OTP:
            if (otp_mode or "").lower() == "otp":
                log.info("Modalità OTP configurata: attivo inserimento codice OTP")
                self._attiva_otp_sielte(driver)
                time.sleep(2)
                self._chiedi_e_inserisci_otp(driver, auth_callback)
            else:
                # Modalità notifica (default): invia la prima notifica push
                if self.on_notify:
                    self.on_notify("📲 Sto per inviare la notifica SielteID: prepara il telefono e "
                                   "approvala appena arriva!")
                self._invia_notifica_sielte(driver)

                # Primo tentativo push: attesa breve (circa 35 secondi)
                push_wait_until = min(deadline, time.time() + 35)
                while time.time() < push_wait_until:
                    time.sleep(4)
                    self._clicca_consenso_sielte(driver)
                    if self.is_autenticato(driver):
                        self.save(driver)
                        self.session_valid = True
                        return driver

            # Se non ancora autenticati, la notifica iniziale non è bastata: avvia fallback
            log.info("Login da notifica non completato: avvio fallback (invia notifica / immetti otp)")
            while time.time() < deadline:
                self._clicca_consenso_sielte(driver)
                if self.is_autenticato(driver):
                    self.save(driver)
                    self.session_valid = True
                    return driver

                # Chiede su Telegram: Invia notifica o Immetti codice OTP
                scelta = self._ottieni_scelta_fallback(auth_callback)

                if scelta == "notify":
                    log.info("Scelta fallback utente: invia notifica di nuovo")
                    if self.on_notify:
                        self.on_notify("📲 Nuova notifica push SielteID inviata: controlla il telefono!")
                    self._invia_notifica_sielte(driver)
                    push_wait_until = min(deadline, time.time() + 35)
                    while time.time() < push_wait_until:
                        time.sleep(4)
                        self._clicca_consenso_sielte(driver)
                        if self.is_autenticato(driver):
                            self.save(driver)
                            self.session_valid = True
                            return driver

                elif scelta == "otp":
                    log.info("Scelta fallback utente: immetti codice OTP")
                    self._attiva_otp_sielte(driver)
                    time.sleep(2)
                    successo = self._chiedi_e_inserisci_otp(driver, auth_callback)
                    if successo and self.is_autenticato(driver):
                        self.save(driver)
                        self.session_valid = True
                        return driver

                elif scelta == "cancel":
                    log.info("Login annullato dall'utente.")
                    raise RuntimeError("Login SielteID annullato dall'utente.")
                else:
                    # Nessuna scelta ricevuta (es. timeout di un ciclo o canale non interattivo)
                    time.sleep(5)

            raise TimeoutError("Timeout nell'attesa approvazione SielteID (approva la notifica o inserisci l'OTP!)")
        finally:
            self.browser.settings.headless = prev

    # ---------------- helper selettori ----------------
    def _find_and_fill(self, driver, sel_list, value: str) -> bool:
        from selenium.webdriver.common.by import By
        BY_MAP = {"id": By.ID, "name": By.NAME, "css": By.CSS_SELECTOR, "xpath": By.XPATH}
        for strat, val in sel_list:
            by = BY_MAP.get(strat, By.CSS_SELECTOR)
            try:
                el = driver.find_element(by, val)
                el.clear()
                el.send_keys(value)
                return True
            except Exception:
                continue
        return False

    def _find_and_click(self, driver, sel_list) -> bool:
        from selenium.webdriver.common.by import By
        BY_MAP = {"id": By.ID, "name": By.NAME, "css": By.CSS_SELECTOR, "xpath": By.XPATH}
        for strat, val in sel_list:
            by = BY_MAP.get(strat, By.CSS_SELECTOR)
            try:
                el = driver.find_element(by, val)
                try:
                    el.click()
                except Exception:
                    driver.execute_script("arguments[0].click();", el)
                return True
            except Exception:
                continue
        return False

    # ---------------- login SPID multi-provider ----------------
    def relogin_spid(self, provider_key: str, username: str = "", password: str = "",
                      wait_otp_sec: int = 180, auth_callback=None) -> webdriver.Chrome:
        """Accesso SPID per i vari provider (PosteID, Aruba, InfoCert, Lepida, Namirial, ecc.).
        Compila automaticamente username/password se configurati, invia notifica Telegram,
        attende l'approvazione secondo fattore (push/OTP) e la schermata di consenso.
        """
        from selenium.webdriver.common.by import By
        from core import selectors

        # Selezione dei selettori specifici del provider
        prov_map = {
            "poste": selectors.LOGIN_IDP_POSTEID,
            "aruba": selectors.LOGIN_IDP_ARUBA,
            "infocert": selectors.LOGIN_IDP_INFOCERT,
            "lepida": selectors.LOGIN_IDP_LEPIDA,
            "namirial": selectors.LOGIN_IDP_NAMIRIAL,
            "sielte": selectors.LOGIN_IDP_SIELTEID,
        }
        prov_sel = prov_map.get(provider_key.lower(), {})
        prov_name = provider_key.capitalize()

        prev = self.browser.settings.headless
        self.browser.settings.headless = False
        self._riavvia_con_headless(False)
        driver = self.browser.start()
        try:
            if self.on_notify:
                self.on_notify(f"🔑 Avvio accesso SPID con {prov_name}...")
            driver.get(selectors.LOGIN_SPID["url_accedi"])
            time.sleep(4)
            if self.is_autenticato(driver):
                self.save(driver)
                self.session_valid = True
                return driver

            # Apri menu SPID e seleziona il provider
            try:
                driver.execute_script("var a=document.querySelector('[spid-idp-button], a.button-spid, .pulsante-spid');if(a)a.click();")
                time.sleep(1)
                boxes = driver.find_elements(By.CSS_SELECTOR, "a.home-box-fornitore")
                target = None
                for b in boxes:
                    if provider_key.lower() in (b.text or "").lower():
                        target = b
                        break
                if target is None:
                    idx = selectors.LOGIN_SPID["idp"].get(prov_name)
                    if idx is not None and idx < len(boxes):
                        target = boxes[idx]
                if target:
                    driver.execute_script("arguments[0].click();", target)
            except Exception as e:
                log.warning("Selezione provider %s: %s", prov_name, e)

            time.sleep(5)

            # Compilazione credenziali se disponibili
            if username and password:
                u_sel = prov_sel.get("username", [("id", "username"), ("name", "username"), ("css", "input[type='text'], input[type='email']")])
                p_sel = prov_sel.get("password", [("id", "password"), ("name", "password"), ("css", "input[type='password']")])
                self._find_and_fill(driver, u_sel, username)
                self._find_and_fill(driver, p_sel, password)

                time.sleep(1)
                btn_sel = prov_sel.get("btn_avanti", [("css", "button[type='submit'], input[type='submit']")])
                self._find_and_click(driver, btn_sel)

                if self.on_notify:
                    self.on_notify(f"📲 Credenziali {prov_name} inviate: conferma la notifica push sull'app o inserisci l'OTP.")
            else:
                if self.on_notify:
                    self.on_notify(f"🔑 Finestra {prov_name} aperta: inserisci le credenziali e autorizza l'accesso.")

            # Attesa approvazione 2FA + consenso (SAML attribute release)
            consenso_sel = prov_sel.get("consenso", [
                ("css", "button[name='confirm'], input[value*='Autorizza'], button[type='submit']"),
                ("xpath", "//button[contains(., 'Autorizza') or contains(., 'Conferma') or contains(., 'Prosegui') or contains(., 'Acconsento')]"),
            ])
            for _ in range(max(1, wait_otp_sec // 5)):
                time.sleep(5)
                # Prova click automatico su eventuale schermata di consenso
                self._find_and_click(driver, consenso_sel)
                if self.is_autenticato(driver):
                    self.save(driver)
                    self.session_valid = True
                    return driver

            raise TimeoutError(f"Timeout nell'attesa approvazione SPID ({prov_name})")
        finally:
            self.browser.settings.headless = prev

    # ---------------- login CIE ----------------
    def relogin_cie(self, username: str = "", password: str = "", mode: str = "app",
                     wait_otp_sec: int = 180, auth_callback=None) -> webdriver.Chrome:
        """Accesso tramite CIE (Carta di Identità Elettronica).
        Naviga all'IdPC, preme 'Entra con CIE' verso il portale del Ministero dell'Interno.
        Se fornite credenziali (CIE/CF + password), le compila ed effettua il submit (Livello 2),
        altrimenti lascia la finestra aperta per QR Code / App CieID o Smartcard.
        """
        from selenium.webdriver.common.by import By
        from core import selectors

        prev = self.browser.settings.headless
        self.browser.settings.headless = False
        self._riavvia_con_headless(False)
        driver = self.browser.start()
        try:
            mode_lbl = "App CieID (Livello 2)" if mode == "app" else "Smartcard (Livello 3)"
            if self.on_notify:
                self.on_notify(f"🔑 Avvio accesso con CIE ({mode_lbl})...")
            driver.get(selectors.LOGIN_SPID["url_accedi"])
            time.sleep(4)
            if self.is_autenticato(driver):
                self.save(driver)
                self.session_valid = True
                return driver

            # Submit form CIE su IdPC Regione Lombardia
            try:
                driver.execute_script(
                    "var f=document.querySelector('form[action*=\"AuthRequestCieService\"], .pulsante-cie form');"
                    "if(f){f.submit();return true;}"
                    "var c=document.querySelector('[cie-button], .pulsante-cie');if(c){c.click();return true;}"
                    "return false;"
                )
            except Exception as e:
                log.warning("Click/submit CIE: %s", e)

            time.sleep(5)

            # Se ci troviamo sul portale Ministero dell'Interno (idserver.servizicie.interno.gov.it)
            cie_sel = selectors.LOGIN_CIE
            if username and password:
                u_sel = cie_sel["username"]
                p_sel = cie_sel["password"]
                self._find_and_fill(driver, u_sel, username)
                self._find_and_fill(driver, p_sel, password)
                time.sleep(1)
                self._find_and_click(driver, cie_sel["btn_prosegui"])
                if self.on_notify:
                    self.on_notify("📲 Credenziali CIE inviate: conferma la notifica nell'app CieID o inserisci l'OTP.")
            else:
                if self.on_notify:
                    self.on_notify(f"🔑 Schermata CIE aperta: completa l'autenticazione con l'app CieID (QR/notifica) o Smartcard.")

            # Attesa approvazione e consenso
            for _ in range(max(1, wait_otp_sec // 5)):
                time.sleep(5)
                # Eventuale consenso su pagina CIE / Regione
                self._find_and_click(driver, cie_sel["consenso"])
                if self.is_autenticato(driver):
                    self.save(driver)
                    self.session_valid = True
                    return driver

            raise TimeoutError("Timeout nell'attesa autenticazione CIE (completa l'accesso con l'app CieID o Smartcard)")
        finally:
            self.browser.settings.headless = prev
