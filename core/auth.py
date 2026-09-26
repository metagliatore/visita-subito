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
    "infocert": {"name": "Infocert", "label": "InfoCert ID", "index": 0},
    "register": {"name": "Register", "label": "SpidItalia (Register.it)", "index": 1},
    "lepida": {"name": "Lepida", "label": "Lepida ID", "index": 2},
    "intesigroup": {"name": "IntesiGroupSpid", "label": "Intesi Group", "index": 3},
    "aruba": {"name": "Aruba", "label": "Aruba ID", "index": 4},
    "namirial": {"name": "Namirial", "label": "Namirial ID", "index": 5},
    "poste": {"name": "Poste", "label": "PosteID", "index": 6},
    "infocamere": {"name": "InfoCamere", "label": "InfoCamere", "index": 7},
    "tim": {"name": "Tim", "label": "TIM id", "index": 8},
    "sielte": {"name": "Sielte", "label": "SielteID", "index": 9},
    "teamsystem": {"name": "TeamSystemId", "label": "TeamSystem ID", "index": 10},
    "etna": {"name": "EtnaID", "label": "EtnaID", "index": 11},
}


class AuthConfig:
    """Configurazione di autenticazione letta da YAML ed env."""

    def __init__(self, method: str = "spid", spid_provider: str = "sielte",
                 cie_mode: str = "app", spid_user: str = "", spid_pwd: str = "",
                 cie_user: str = "", cie_pwd: str = "", spid_otp_mode: str = "notifica"):
        self.method = (method or "spid").lower().strip()
        self.spid_provider = (spid_provider or "sielte").lower().strip()
        self.cie_mode = (cie_mode or "app").lower().strip()
        self.spid_user = spid_user
        self.spid_pwd = spid_pwd
        self.cie_user = cie_user
        self.cie_pwd = cie_pwd
        self.spid_otp_mode = (spid_otp_mode or "notifica").lower().strip()

    @classmethod
    def from_settings_and_env(cls, settings: dict) -> "AuthConfig":
        auth_sec = settings.get("auth", {})
        spid_sec = auth_sec.get("spid", {})
        cie_sec = auth_sec.get("cie", {})

        method = os.environ.get("AUTH_METHOD") or auth_sec.get("method", "spid")
        spid_provider = os.environ.get("SPID_PROVIDER") or spid_sec.get("provider", "sielte")
        cie_mode = os.environ.get("CIE_MODE") or cie_sec.get("mode", "app")
        spid_otp_mode = (os.environ.get("SPID_OTP_MODE") or
                         os.environ.get("SIELTE_OTP_MODE") or
                         spid_sec.get("otp_mode", "notifica"))

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
            spid_otp_mode=spid_otp_mode,
        )

    def describe(self) -> str:
        """Descrizione human-readable per status e log."""
        if self.method == "spid":
            info = SPID_PROVIDERS.get(self.spid_provider, {})
            label = info.get("label", self.spid_provider.upper())
            status = "automatico" if self.spid_user and self.spid_pwd else "semi-automatico"
            mode_lbl = f", {self.spid_otp_mode}" if self.spid_otp_mode else ""
            return f"SPID ({label}{mode_lbl}) [{status}]"
        elif self.method == "cie":
            mode_lbl = "App CieID" if self.cie_mode == "app" else "Smartcard/NFC"
            status = "automatico" if self.cie_user and self.cie_pwd else "semi-automatico"
            return f"CIE ({mode_lbl}) [{status}]"
        elif self.method == "manual":
            return "Manuale (apertura finestra)"
        return f"{self.method.upper()}"


class BaseAuthProvider(ABC):
    @abstractmethod
    def login(self, session_manager, browser, notify_cb=None, auth_callback=None) -> webdriver.Chrome:
        """Esegue l'accesso e ritorna l'istanza autenticata di WebDriver."""
        pass


class SielteSpidAuthProvider(BaseAuthProvider):
    """Autenticazione automatizzata SPID SielteID con notifica push/OTP."""

    def __init__(self, username: str = "", password: str = "", otp_mode: str = "notifica"):
        self.username = username
        self.password = password
        self.otp_mode = otp_mode

    def login(self, session_manager, browser, notify_cb=None, auth_callback=None) -> webdriver.Chrome:
        if self.username and self.password:
            kwargs = {"username": self.username, "password": self.password}
            if self.otp_mode and self.otp_mode != "notifica":
                kwargs["otp_mode"] = self.otp_mode
            if auth_callback is not None:
                kwargs["auth_callback"] = auth_callback
            return session_manager.relogin_sielte(**kwargs)
        else:
            if notify_cb:
                notify_cb("🔑 Richiesto login SPID SielteID manuale (credenziali non configurate).")
            return session_manager.relogin_manual()


class PosteSpidAuthProvider(BaseAuthProvider):
    """Accesso SPID con PosteID (Poste Italiane)."""

    def __init__(self, username: str = "", password: str = ""):
        self.username = username
        self.password = password

    def login(self, session_manager, browser, notify_cb=None, auth_callback=None, **kwargs) -> webdriver.Chrome:
        kw = {"username": self.username, "password": self.password}
        if auth_callback is not None:
            kw["auth_callback"] = auth_callback
        return session_manager.relogin_spid("poste", **kw)


class ArubaSpidAuthProvider(BaseAuthProvider):
    """Accesso SPID con Aruba ID."""

    def __init__(self, username: str = "", password: str = ""):
        self.username = username
        self.password = password

    def login(self, session_manager, browser, notify_cb=None, auth_callback=None, **kwargs) -> webdriver.Chrome:
        kw = {"username": self.username, "password": self.password}
        if auth_callback is not None:
            kw["auth_callback"] = auth_callback
        return session_manager.relogin_spid("aruba", **kw)


class InfocertSpidAuthProvider(BaseAuthProvider):
    """Accesso SPID con InfoCert ID."""

    def __init__(self, username: str = "", password: str = ""):
        self.username = username
        self.password = password

    def login(self, session_manager, browser, notify_cb=None, auth_callback=None, **kwargs) -> webdriver.Chrome:
        kw = {"username": self.username, "password": self.password}
        if auth_callback is not None:
            kw["auth_callback"] = auth_callback
        return session_manager.relogin_spid("infocert", **kw)


class LepidaSpidAuthProvider(BaseAuthProvider):
    """Accesso SPID con Lepida ID."""

    def __init__(self, username: str = "", password: str = ""):
        self.username = username
        self.password = password

    def login(self, session_manager, browser, notify_cb=None, auth_callback=None, **kwargs) -> webdriver.Chrome:
        kw = {"username": self.username, "password": self.password}
        if auth_callback is not None:
            kw["auth_callback"] = auth_callback
        return session_manager.relogin_spid("lepida", **kw)


class NamirialSpidAuthProvider(BaseAuthProvider):
    """Accesso SPID con Namirial ID."""

    def __init__(self, username: str = "", password: str = ""):
        self.username = username
        self.password = password

    def login(self, session_manager, browser, notify_cb=None, auth_callback=None, **kwargs) -> webdriver.Chrome:
        kw = {"username": self.username, "password": self.password}
        if auth_callback is not None:
            kw["auth_callback"] = auth_callback
        return session_manager.relogin_spid("namirial", **kw)


class GenericSpidAuthProvider(BaseAuthProvider):
    """Accesso SPID generico per qualunque altro fornitore accreditato."""

    def __init__(self, provider_key: str, username: str = "", password: str = ""):
        self.provider_key = provider_key.lower()
        self.username = username
        self.password = password

    def login(self, session_manager, browser, notify_cb=None, auth_callback=None, **kwargs) -> webdriver.Chrome:
        kw = {"username": self.username, "password": self.password}
        if auth_callback is not None:
            kw["auth_callback"] = auth_callback
        return session_manager.relogin_spid(self.provider_key, **kw)


class CieAuthProvider(BaseAuthProvider):
    """Accesso con Carta di Identità Elettronica (CIE / CieID)."""

    def __init__(self, mode: str = "app", username: str = "", password: str = ""):
        self.mode = mode
        self.username = username
        self.password = password

    def login(self, session_manager, browser, notify_cb=None, auth_callback=None, **kwargs) -> webdriver.Chrome:
        kw = {"username": self.username, "password": self.password, "mode": self.mode}
        if auth_callback is not None:
            kw["auth_callback"] = auth_callback
        return session_manager.relogin_cie(**kw)


class ManualAuthProvider(BaseAuthProvider):
    """Accesso puramente manuale (apertura finestra visibile)."""

    def login(self, session_manager, browser, notify_cb=None, auth_callback=None, **kwargs) -> webdriver.Chrome:
        if notify_cb:
            notify_cb("🔑 Richiesto login manuale: completa l'accesso nella finestra.")
        return session_manager.relogin_manual(selectors.LOGIN_SPID["url_accedi"])


def get_auth_provider(auth_config: AuthConfig) -> BaseAuthProvider:
    """Factory per istanziare l'AuthProvider idoneo alla configurazione."""
    if auth_config.method == "spid":
        prov = auth_config.spid_provider.lower()
        mapping = {
            "sielte": lambda: SielteSpidAuthProvider(
                username=auth_config.spid_user,
                password=auth_config.spid_pwd,
                otp_mode=auth_config.spid_otp_mode,
            ),
            "poste": lambda: PosteSpidAuthProvider(username=auth_config.spid_user, password=auth_config.spid_pwd),
            "aruba": lambda: ArubaSpidAuthProvider(username=auth_config.spid_user, password=auth_config.spid_pwd),
            "infocert": lambda: InfocertSpidAuthProvider(username=auth_config.spid_user, password=auth_config.spid_pwd),
            "lepida": lambda: LepidaSpidAuthProvider(username=auth_config.spid_user, password=auth_config.spid_pwd),
            "namirial": lambda: NamirialSpidAuthProvider(username=auth_config.spid_user, password=auth_config.spid_pwd),
        }
        if prov in mapping:
            return mapping[prov]()
        return GenericSpidAuthProvider(
            provider_key=prov,
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
