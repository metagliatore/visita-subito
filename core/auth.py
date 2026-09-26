"""Modulo di autenticazione per l'accesso ai servizi regionali.

Supporta diverse modalità di accesso:
  - SPID: SielteID (automatizzato con push/OTP), Poste, Aruba, Infocert, Lepida, Namirial, Tim, ecc.
  - CIE: Carta di Identità Elettronica (CieID / Smartcard)
  - Manuale: Accesso guidato da finestra browser
"""
from __future__ import annotations

import logging
import os
import time
from abc import ABC, abstractmethod

from selenium import webdriver
from selenium.webdriver.common.by import By

from core import selectors

log = logging.getLogger(__name__)

# Mappatura dei provider SPID noti e dei relativi indici IdPC Regione Lombardia
SPID_PROVIDERS = {
    "sielte": {"name": "Sielte", "label": "SielteID", "index": 10},
    "poste": {"name": "Poste", "label": "PosteID", "index": 0},
    "aruba": {"name": "Aruba", "label": "Aruba ID", "index": 1},
    "infocert": {"name": "Infocert", "label": "InfoCert ID", "index": 2},
    "lepida": {"name": "Lepida", "label": "Lepida ID", "index": 3},
    "intesigroup": {"name": "IntesiGroupSpid", "label": "Intesi Group", "index": 4},
    "tim": {"name": "Tim", "label": "TIM id", "index": 5},
    "teamsystem": {"name": "TeamSystemId", "label": "TeamSystem ID", "index": 6},
    "register": {"name": "Register", "label": "SpidItalia (Register.it)", "index": 7},
    "infocamere": {"name": "InfoCamere", "label": "InfoCamere", "index": 8},
    "etna": {"name": "EtnaID", "label": "EtnaID", "index": 9},
    "namirial": {"name": "Namirial", "label": "Namirial ID", "index": 11},
}


class AuthConfig:
    """Configurazione di autenticazione letta da YAML ed env."""

    def __init__(self, method: str = "spid", spid_provider: str = "sielte",
                 cie_mode: str = "app", spid_user: str = "", spid_pwd: str = "",
                 cie_user: str = "", cie_pwd: str = ""):
        self.method = (method or "spid").lower().strip()
        self.spid_provider = (spid_provider or "sielte").lower().strip()
        self.cie_mode = (cie_mode or "app").lower().strip()
        self.spid_user = spid_user
        self.spid_pwd = spid_pwd
        self.cie_user = cie_user
        self.cie_pwd = cie_pwd

    @classmethod
    def from_settings_and_env(cls, settings: dict) -> "AuthConfig":
        auth_sec = settings.get("auth", {})
        spid_sec = auth_sec.get("spid", {})
        cie_sec = auth_sec.get("cie", {})

        method = os.environ.get("AUTH_METHOD") or auth_sec.get("method", "spid")
        spid_provider = os.environ.get("SPID_PROVIDER") or spid_sec.get("provider", "sielte")
        cie_mode = os.environ.get("CIE_MODE") or cie_sec.get("mode", "app")

        # Credenziali SPID con retrocompatibilità SIELTE_USERNAME
        spid_user = (os.environ.get("SPID_USERNAME") or
                     os.environ.get("SIELTE_USERNAME") or
                     spid_sec.get("username", ""))
        spid_pwd = (os.environ.get("SPID_PASSWORD") or
                    os.environ.get("SIELTE_PASSWORD") or
                    spid_sec.get("password", ""))

        cie_user = os.environ.get("CIE_USERNAME") or cie_sec.get("username", "")
        cie_pwd = os.environ.get("CIE_PASSWORD") or cie_sec.get("password", "")

        return cls(
            method=method,
            spid_provider=spid_provider,
            cie_mode=cie_mode,
            spid_user=spid_user,
            spid_pwd=spid_pwd,
            cie_user=cie_user,
            cie_pwd=cie_pwd,
        )

    def describe(self) -> str:
        """Descrizione human-readable per status e log."""
        if self.method == "spid":
            info = SPID_PROVIDERS.get(self.spid_provider, {})
            label = info.get("label", self.spid_provider.upper())
            status = "automatico" if self.spid_provider == "sielte" else "semi-automatico"
            return f"SPID ({label}) [{status}]"
        elif self.method == "cie":
            mode_lbl = "App CieID" if self.cie_mode == "app" else "Smartcard/NFC"
            return f"CIE ({mode_lbl}) [semi-automatico]"
        elif self.method == "manual":
            return "Manuale (apertura finestra)"
        return f"{self.method.upper()}"


class BaseAuthProvider(ABC):
    @abstractmethod
    def login(self, session_manager, browser, notify_cb=None) -> webdriver.Chrome:
        """Esegue l'accesso e ritorna l'istanza autenticata di WebDriver."""
        pass


class SielteSpidAuthProvider(BaseAuthProvider):
    """Autenticazione automatizzata SPID SielteID con notifica push/OTP."""

    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password

    def login(self, session_manager, browser, notify_cb=None) -> webdriver.Chrome:
        if self.username and self.password:
            return session_manager.relogin_sielte(username=self.username, password=self.password)
        else:
            if notify_cb:
                notify_cb("🔑 Richiesto login SPID SielteID manuale (credenziali non configurate).")
            return session_manager.relogin_manual(selectors.LOGIN_SPID["url_accedi"])


class GenericSpidAuthProvider(BaseAuthProvider):
    """Accesso SPID per altri provider (PosteID, Aruba, Infocert, Lepida, ecc.).

    Seleziona automaticamente il provider nella schermata IdPC di Regione Lombardia
    e guida l'utente per il completamento dell'autenticazione.
    """

    def __init__(self, provider_key: str, username: str = "", password: str = ""):
        self.provider_key = provider_key.lower()
        self.provider_info = SPID_PROVIDERS.get(
            self.provider_key,
            {"name": provider_key, "label": provider_key.upper(), "index": 0}
        )
        self.username = username
        self.password = password

    def login(self, session_manager, browser, notify_cb=None) -> webdriver.Chrome:
        provider_name = self.provider_info.get("name", self.provider_key)
        provider_label = self.provider_info.get("label", self.provider_key.upper())

        if notify_cb:
            notify_cb(f"🔑 Avvio accesso SPID con {provider_label}...\n"
                      f"Completa il login e l'OTP nella finestra.")

        prev = browser.settings.headless
        browser.settings.headless = False
        session_manager._riavvia_con_headless(False)
        try:
            driver = browser.start()
            driver.get(selectors.LOGIN_SPID["url_accedi"])
            time.sleep(3)
            if session_manager.is_autenticato(driver):
                session_manager.save(driver)
                session_manager.session_valid = True
                return driver

            # Apri menu SPID e clicca sul provider richiesto
            try:
                driver.execute_script("var a=document.querySelector('[spid-idp-button], a.button-spid, .pulsante-spid');if(a)a.click();")
                time.sleep(1)
                boxes = driver.find_elements(By.CSS_SELECTOR, "a.home-box-fornitore")
                target = None
                for b in boxes:
                    if provider_name.lower() in (b.text or "").lower():
                        target = b
                        break
                if target is None:
                    idx = self.provider_info.get("index")
                    if idx is not None and idx < len(boxes):
                        target = boxes[idx]
                if target:
                    driver.execute_script("arguments[0].click();", target)
            except Exception as e:  # noqa: BLE001
                log.warning("Selezione provider SPID %s: %s", provider_name, e)

            session_manager._attendi_login_manuale(driver)
            session_manager.save(driver)
            session_manager.session_valid = True
            return driver
        finally:
            browser.settings.headless = prev


class CieAuthProvider(BaseAuthProvider):
    """Accesso con Carta di Identità Elettronica (CIE / CieID).

    Naviga al portale regionale, preme 'Entra con CIE' e attende la convalida
    tramite app CieID o Smartcard.
    """

    def __init__(self, mode: str = "app", username: str = "", password: str = ""):
        self.mode = mode
        self.username = username
        self.password = password

    def login(self, session_manager, browser, notify_cb=None) -> webdriver.Chrome:
        mode_label = "App CieID" if self.mode == "app" else "Smartcard/NFC"
        if notify_cb:
            notify_cb(f"🔑 Avvio accesso con CIE ({mode_label})...\n"
                      f"Completa la verifica con l'app CieID o smartcard nella finestra.")

        prev = browser.settings.headless
        browser.settings.headless = False
        session_manager._riavvia_con_headless(False)
        try:
            driver = browser.start()
            driver.get(selectors.LOGIN_SPID["url_accedi"])
            time.sleep(3)
            if session_manager.is_autenticato(driver):
                session_manager.save(driver)
                session_manager.session_valid = True
                return driver

            # Clicca 'Entra con CIE'
            try:
                driver.execute_script(
                    "var c=document.querySelector('[cie-button], a.button-cie, .pulsante-cie, a[href*=\"cie\"], a[href*=\"CIE\"]');"
                    "if(c){c.click();return true} return false;"
                )
            except Exception as e:  # noqa: BLE001
                log.warning("Click pulsante CIE: %s", e)

            session_manager._attendi_login_manuale(driver)
            session_manager.save(driver)
            session_manager.session_valid = True
            return driver
        finally:
            browser.settings.headless = prev


class ManualAuthProvider(BaseAuthProvider):
    """Accesso puramente manuale (apertura finestra visibile)."""

    def login(self, session_manager, browser, notify_cb=None) -> webdriver.Chrome:
        if notify_cb:
            notify_cb("🔑 Richiesto login manuale: completa l'accesso nella finestra.")
        return session_manager.relogin_manual(selectors.LOGIN_SPID["url_accedi"])


def get_auth_provider(auth_config: AuthConfig) -> BaseAuthProvider:
    """Factory per istanziare l'AuthProvider idoneo alla configurazione."""
    if auth_config.method == "spid":
        if auth_config.spid_provider == "sielte":
            return SielteSpidAuthProvider(username=auth_config.spid_user, password=auth_config.spid_pwd)
        else:
            return GenericSpidAuthProvider(
                provider_key=auth_config.spid_provider,
                username=auth_config.spid_user,
                password=auth_config.spid_pwd,
            )
    elif auth_config.method == "cie":
        return CieAuthProvider(
            mode=auth_config.cie_mode,
            username=auth_config.cie_user,
            password=auth_config.cie_pwd,
        )
    elif auth_config.method == "manual":
        return ManualAuthProvider()
    else:
        log.warning("Metodo di autenticazione '%s' sconosciuto: fallback a manuale", auth_config.method)
        return ManualAuthProvider()
