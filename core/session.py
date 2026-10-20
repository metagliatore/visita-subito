"""Session Manager.

Manages SPID/CIE session lifecycle:
  1. Login is performed manually or automatically.
  2. Cookies are persisted to disk.
  3. A periodic keep-alive touches the portal to prevent session expiration.
  4. If expired, exposes state triggering re-login notifications via Telegram.
"""
from __future__ import annotations

import json
import logging
import pickle
import time
from pathlib import Path

from selenium import webdriver

log = logging.getLogger(__name__)

# Maintaining the same IP (VPS/stable PC) is recommended to avoid invalidating
# the SPID session due to IP changes.


class SessionManager:
    def __init__(self, browser, cookie_dir: Path):
        self.browser = browser
        self.cookie_dir = Path(cookie_dir)
        self.cookie_dir.mkdir(parents=True, exist_ok=True)
        self.cookie_file = self.cookie_dir / "cookies.pkl"
        self.meta_file = self.cookie_dir / "meta.json"
        self.session_valid = False
        # Notification callback (e.g. sends Telegram messages during login)
        self.on_notify = None
        # Interactive fallback callback on Telegram (push notification vs OTP)
        self.on_auth_fallback = None
        # Callback for requesting OTP code input
        self.on_otp_prompt = None

    # ---------------- persistence ----------------
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
        """True if no saved session exists yet."""
        return not self.cookie_file.exists()

    def is_autenticato(self, driver: webdriver.Chrome) -> bool:
        """True if current driver is authenticated on private area or prenotaonline."""
        if driver is None:
            return False
        try:
            url = (driver.current_url or "").split("?")[0].lower()
            if not url or "about:blank" in url:
                return False
            # If redirected to IdP or login pages, we are not authenticated
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
        """If a driver is already active with a different headless mode,
        restarts it with the requested mode."""
        self.browser.set_headless(headless)

    # ---------------- actions ----------------
    def relogin_manual(self, url: str) -> webdriver.Chrome:
        """Open a VISIBLE window: user performs login + OTP manually.

        Waits for completion by polling `is_autenticato` (works even under nohup,
        where `input()` reads /dev/null and saves cookies before login finishes).
        If stdin is a TTY and no timeout is configured, still asks for manual confirmation.
        """
        prev = self.browser.settings.headless
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
        """Wait (polling every 5s) for manual login completion,
        i.e. until the URL belongs to the private area. Works without a TTY."""
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
            # If stdin is a TTY, also allow classic manual confirmation
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
        # Confirmation from TTY: save only if actually authenticated (checked above)

    def invalidate(self) -> None:
        """Mark session as invalid/expired and delete cookies on disk."""
        self.session_valid = False
        try:
            if hasattr(self, "cookie_file") and self.cookie_file.exists():
                self.cookie_file.unlink()
        except Exception:  # noqa: BLE001
            pass
        try:
            if hasattr(self, "meta_file") and self.meta_file.exists():
                self.meta_file.unlink()
        except Exception:  # noqa: BLE001
            pass

    def keep_alive(self, driver: webdriver.Chrome, url: str) -> bool:
        """Touch a portal page to keep the session alive."""
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
            if not self.browser.is_alive():
                log.warning("keep-alive: browser non risponde, arresto il driver")
                self.browser.stop()
            return False

    # ---------------- SielteID helpers ----------------
    def _invia_notifica_sielte(self, driver: webdriver.Chrome) -> bool:
        """Attempt to select or resend push notification on SielteID."""
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
        """Switch to OTP code input screen on SielteID."""
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
        """Fill and submit OTP code on SielteID."""
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
        """Attempt to confirm SPID SAML data consent screen if present."""
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
        """Ask user via Telegram (or TTY) whether to resend push notification or enter OTP code."""
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

        # Console fallback when started with interactive terminal (TTY)
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
        """Ask user for OTP code, fill it into the form, and await verification."""
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

        # Console fallback if on TTY
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

    # ---------------- automated SielteID login ----------------
    def relogin_sielte(self, username: str = "", password: str = "",
                       wait_otp_sec: int = 180,
                       otp_mode: str = "notifica",
                       auth_callback=None) -> webdriver.Chrome:
        """Automated SielteID login (visible window): credentials, push
        notification method selection, with Telegram fallback to resend
        notification or enter OTP code.
        """
        from selenium.webdriver.common.by import By
        from core import selectors

        driver = self.browser.start()
        if self.on_notify:
            self.on_notify("🔑 Sessione scaduta: avvio il re-login SielteID...")
        driver.get(selectors.LOGIN_SPID["url_accedi"])
        time.sleep(4)
        if self.is_autenticato(driver):
            self.save(driver); self.session_valid = True
            return driver
        # IdPC page: open dropdown + select Sielte
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
        # Login form
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
        # 2FA: Push Notification with Interactive Fallback (Telegram)
        # ----------------------------------------------------
        t0 = time.time()
        deadline = t0 + wait_otp_sec

        # If user explicitly configured OTP mode:
        if (otp_mode or "").lower() == "otp":
            log.info("Modalità OTP configurata: attivo inserimento codice OTP")
            self._attiva_otp_sielte(driver)
            time.sleep(2)
            self._chiedi_e_inserisci_otp(driver, auth_callback)
        else:
            # Notification mode (default): send first push notification
            if self.on_notify:
                self.on_notify("📲 Sto per inviare la notifica SielteID: prepara il telefono e "
                               "approvala appena arriva!")
            self._invia_notifica_sielte(driver)

            # First push attempt: short wait (~35 seconds)
            push_wait_until = min(deadline, time.time() + 35)
            while time.time() < push_wait_until:
                time.sleep(4)
                self._clicca_consenso_sielte(driver)
                if self.is_autenticato(driver):
                    self.save(driver)
                    self.session_valid = True
                    return driver

        # If not yet authenticated, initial push did not succeed: trigger fallback
        log.info("Login da notifica non completato: avvio fallback (invia notifica / immetti otp)")
        abs_deadline = t0 + 600  # Absolute maximum limit of 10 minutes for interactive session
        while time.time() < deadline and time.time() < abs_deadline:
            self._clicca_consenso_sielte(driver)
            if self.is_autenticato(driver):
                self.save(driver)
                self.session_valid = True
                return driver

            # Ask on Telegram: Send push notification or enter OTP code
            scelta = self._ottieni_scelta_fallback(auth_callback)

            if scelta == "notify":
                log.info("Scelta fallback utente: invia notifica di nuovo")
                # When user requests a new push notification, extend deadline
                deadline = max(deadline, min(abs_deadline, time.time() + 65))
                if self.on_notify:
                    self.on_notify("📲 Nuova notifica push SielteID inviata: controlla il telefono!")
                self._invia_notifica_sielte(driver)
                push_wait_until = min(deadline, time.time() + 45)
                while time.time() < push_wait_until:
                    time.sleep(4)
                    self._clicca_consenso_sielte(driver)
                    if self.is_autenticato(driver):
                        self.save(driver)
                        self.session_valid = True
                        return driver

            elif scelta == "otp":
                log.info("Scelta fallback utente: immetti codice OTP")
                deadline = max(deadline, min(abs_deadline, time.time() + 130))
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
                # No choice received (e.g. cycle timeout or non-interactive channel)
                time.sleep(5)

        raise TimeoutError("Timeout nell'attesa approvazione SielteID (approva la notifica o inserisci l'OTP!)")

    # ---------------- selector helpers ----------------
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

    def _find_element(self, driver, sel_list):
        """Return first visible WebElement found in selector list, or None."""
        from selenium.webdriver.common.by import By
        BY_MAP = {"id": By.ID, "name": By.NAME, "css": By.CSS_SELECTOR, "xpath": By.XPATH}
        for strat, val in sel_list:
            by = BY_MAP.get(strat, By.CSS_SELECTOR)
            try:
                el = driver.find_element(by, val)
                if el and el.is_displayed():
                    return el
            except Exception:
                continue
        return None

    def _chiedi_e_inserisci_otp_cie(self, driver: webdriver.Chrome, auth_callback=None) -> bool:
        """Ask user for CIE OTP code, fill it into the form, and await verification."""
        prompt_txt = "🔢 Inserisci il codice OTP ricevuto via SMS o generato dall'app CieID:"
        code = None
        if auth_callback:
            try:
                code = auth_callback("ask_otp", prompt=prompt_txt)
            except Exception as e:
                log.warning("auth_callback ask_otp CIE: %s", e)
        if not code and self.on_otp_prompt:
            try:
                code = self.on_otp_prompt(prompt_txt)
            except Exception as e:
                log.warning("on_otp_prompt CIE: %s", e)

        # Console fallback if on TTY
        if not code:
            import sys
            if sys.stdin.isatty():
                try:
                    print(f"\n[CIE Livello 2] {prompt_txt} ", end="", flush=True)
                    code = sys.stdin.readline().strip()
                except Exception:
                    pass

        if not code:
            log.warning("Nessun codice OTP CIE fornito.")
            return False

        clean_code = str(code).replace(" ", "").replace("-", "").strip()
        log.info("Inserimento codice OTP CIE nel browser...")
        if self.on_notify:
            self.on_notify("⏳ Inserisco il codice OTP CIE nel portale...")

        from core import selectors
        cie_sel = selectors.LOGIN_CIE
        self._find_and_fill(driver, cie_sel["otp"], clean_code)
        time.sleep(1)
        self._find_and_click(driver, cie_sel["btn_otp"])

        for _ in range(3):
            time.sleep(3)
            self._find_and_click(driver, cie_sel["consenso"])
            if self.is_autenticato(driver):
                return True

        if not self.is_autenticato(driver):
            if self.on_notify:
                self.on_notify("❌ Codice OTP CIE non valido o autenticazione non riuscita.")
            return False

        return True

    # ---------------- multi-provider SPID login ----------------
    def relogin_spid(self, provider_key: str, username: str = "", password: str = "",
                      wait_otp_sec: int = 180, auth_callback=None) -> webdriver.Chrome:
        """SPID login for various providers (PosteID, Aruba, InfoCert, Lepida, Namirial, etc.).
        Automatically fills username/password if configured, sends Telegram notification,
        awaits second-factor approval (push/OTP), and handles the consent screen.
        """
        from selenium.webdriver.common.by import By
        from core import selectors

        # Selection of provider-specific selectors
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
        driver = self.browser.start()
        if self.on_notify:
            self.on_notify(f"🔑 Avvio accesso SPID con {prov_name}...")
        driver.get(selectors.LOGIN_SPID["url_accedi"])
        time.sleep(4)
        if self.is_autenticato(driver):
            self.save(driver)
            self.session_valid = True
            return driver

        # Open SPID menu and select provider
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

        # Fill credentials if available
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

        # Wait for 2FA approval + consent (SAML attribute release)
        consenso_sel = prov_sel.get("consenso", [
            ("css", "button[name='confirm'], input[value*='Autorizza'], button[type='submit']"),
            ("xpath", "//button[contains(., 'Autorizza') or contains(., 'Conferma') or contains(., 'Prosegui') or contains(., 'Acconsento')]"),
        ])
        for _ in range(max(1, wait_otp_sec // 5)):
            time.sleep(5)
            # Try clicking through consent screen if present
            self._find_and_click(driver, consenso_sel)
            if self.is_autenticato(driver):
                self.save(driver)
                self.session_valid = True
                return driver

        raise TimeoutError(f"Timeout nell'attesa approvazione SPID ({prov_name})")

    # ---------------- CIE login ----------------
    def relogin_cie(self, username: str = "", password: str = "", mode: str = "app",
                     wait_otp_sec: int = 180, auth_callback=None) -> webdriver.Chrome:
        """Login via CIE (Electronic Identity Card).
        Supports:
          - Level 2: credentials (Fiscal Code / CIE Number + password) + 2FA
            (CieID app push notification, OTP via SMS or app, QR code).
          - Level 3: NFC smartcard with reader or CIE software.
        Includes Telegram interaction for OTP fallback and QR code screenshot.
        """
        from selenium.webdriver.common.by import By
        from core import selectors

        driver = self.browser.start()
        mode_str = (mode or "app").lower()
        mode_lbl = {
            "app": "App CieID / Credenziali (Livello 2)",
            "credenziali": "Credenziali Livello 2 (SMS / App)",
            "smartcard": "Smartcard / NFC (Livello 3)",
        }.get(mode_str, f"CIE ({mode})")

        if self.on_notify:
            self.on_notify(f"🔑 Avvio accesso con CIE ({mode_lbl})...")

        driver.get(selectors.LOGIN_SPID["url_accedi"])
        time.sleep(4)
        if self.is_autenticato(driver):
            self.save(driver)
            self.session_valid = True
            return driver

        # Submit CIE form on Regione Lombardia IdPC
        try:
            submitted = driver.execute_script(
                "var f=document.querySelector('form[action*=\"AuthRequestCieService\"], .pulsante-cie form');"
                "if(f){f.submit();return true;}"
                "var c=document.querySelector('[cie-button], .pulsante-cie, a.button-cie, #cie-btn, a[href*=\"cie\"], a[href*=\"CIE\"], a[href*=\"Cie\"]');"
                "if(c){c.click();return true;}"
                "return false;"
            )
            if not submitted:
                self._find_and_click(driver, selectors.LOGIN_CIE["btn_entra_cie"])
        except Exception as e:
            log.warning("Click/submit CIE su IdPC Lombardia: %s", e)

        time.sleep(4)

        cie_sel = selectors.LOGIN_CIE

        # Wait until landed on CIE IdP or authenticated
        for _ in range(5):
            if self.is_autenticato(driver):
                self.save(driver)
                self.session_valid = True
                return driver
            if cie_sel.get("url_matcher", "idserver.servizicie.interno.gov.it") in (driver.current_url or ""):
                break
            time.sleep(2)

        # Level 1/2 credential handling
        if username and password:
            # If page shows mode selection first, open credentials section
            if not self._find_element(driver, cie_sel["username"]):
                log.info("Campi credenziali CIE non visibili subito: provo selettori 'btn_credenziali'")
                self._find_and_click(driver, cie_sel["btn_credenziali"])
                time.sleep(2)

            filled_u = self._find_and_fill(driver, cie_sel["username"], username)
            filled_p = self._find_and_fill(driver, cie_sel["password"], password)
            if filled_u and filled_p:
                time.sleep(1)
                self._find_and_click(driver, cie_sel["btn_prosegui"])
                time.sleep(3)
                if self.on_notify:
                    self.on_notify("📲 Credenziali CIE inviate: verifica la notifica nell'app CieID o inserisci l'OTP.")
            else:
                log.warning("Campi credenziali CIE non trovati.")
                if self.on_notify:
                    self.on_notify("⚠️ Impossibile compilare automaticamente le credenziali CIE.")
        else:
            if mode_str == "smartcard":
                self._find_and_click(driver, cie_sel["btn_computer"])
                if self.on_notify:
                    self.on_notify("💳 Modalità Smartcard (Livello 3): inserisci la CIE nel lettore NFC o completa la lettura con il software CIE.")
            else:
                self._find_and_click(driver, cie_sel["btn_smartphone"])
                if self.on_notify:
                    self.on_notify("🔑 Schermata CIE aperta: completa l'autenticazione con l'app CieID (QR/notifica) o Smartcard.")

        # 2FA, QR, OTP, approval and consent waiting loop
        t0 = time.time()
        deadline = t0 + wait_otp_sec
        abs_deadline = t0 + 600  # Max 10 minutes for human interaction
        qr_sent = False
        last_fallback_prompt = 0

        while time.time() < deadline and time.time() < abs_deadline:
            # Attempt clicking consent button
            self._find_and_click(driver, cie_sel["consenso"])
            if self.is_autenticato(driver):
                self.save(driver)
                self.session_valid = True
                return driver

            # Check if OTP input field has appeared
            if self._find_element(driver, cie_sel["otp"]):
                log.info("Rilevato campo OTP CIE sul portale")
                deadline = max(deadline, min(abs_deadline, time.time() + 130))
                successo = self._chiedi_e_inserisci_otp_cie(driver, auth_callback)
                if successo and self.is_autenticato(driver):
                    self.save(driver)
                    self.session_valid = True
                    return driver

            # Check if QR code has appeared for scanning
            if not qr_sent and mode_str != "smartcard":
                qr_el = self._find_element(driver, cie_sel["qr_code"])
                if qr_el:
                    log.info("Rilevato QR code CIE a schermo")
                    qr_sent = True
                    qr_path = "data/cie_qr.png"
                    try:
                        from pathlib import Path
                        Path(qr_path).parent.mkdir(parents=True, exist_ok=True)
                        qr_el.screenshot(qr_path)
                        if auth_callback:
                            auth_callback("send_file", path=qr_path,
                                          caption="📲 Scansiona questo QR Code con l'app CieID per completare l'accesso.")
                        elif self.on_notify:
                            self.on_notify("📲 QR Code CIE visualizzato: scansionalo con l'app CieID sul tuo smartphone.")
                    except Exception as e:
                        log.warning("Cattura screenshot QR CIE: %s", e)
                        if self.on_notify:
                            self.on_notify("📲 QR Code CIE visualizzato: scansionalo con l'app CieID sul tuo smartphone.")

            # If awaiting push notification and more than 35 seconds elapsed since submit
            if (username and password and auth_callback and
                    (time.time() - t0 > 35) and (time.time() - last_fallback_prompt > 60)):
                log.info("Notifica CIE non ancora approvata: propongo fallback interattivo Telegram")
                prompt_msg = (
                    "⚠️ <b>Accesso CIE (Livello 2)</b>\n"
                    "La notifica nell'app CieID non è stata ancora approvata.\n\n"
                    "Come vuoi procedere?"
                )
                scelta = None
                try:
                    scelta = auth_callback("ask_action", prompt=prompt_msg)
                    last_fallback_prompt = time.time()
                except Exception as e:
                    log.warning("auth_callback ask_action CIE: %s", e)

                if scelta == "otp":
                    log.info("Scelta fallback CIE: utente richiede immissione OTP")
                    self._find_and_click(driver, cie_sel["btn_richiedi_sms_otp"])
                    time.sleep(2)
                    deadline = max(deadline, min(abs_deadline, time.time() + 130))
                    successo = self._chiedi_e_inserisci_otp_cie(driver, auth_callback)
                    if successo and self.is_autenticato(driver):
                        self.save(driver)
                        self.session_valid = True
                        return driver
                elif scelta == "notify":
                    log.info("Scelta fallback CIE: attendo notifica")
                    deadline = max(deadline, min(abs_deadline, time.time() + 65))
                    if self.on_notify:
                        self.on_notify("📲 Attendo l'approvazione nell'app CieID: controlla il telefono!")
                elif scelta == "cancel":
                    log.info("Login CIE annullato dall'utente.")
                    raise RuntimeError("Login CIE annullato dall'utente.")

            time.sleep(4)

        raise TimeoutError("Timeout nell'attesa autenticazione CIE (completa l'accesso con l'app CieID o Smartcard)")
