"""Wrapper Selenium.

Gestisce l'avvio del browser (headless o visibile), il riuso della stessa
istanza e l'helper per log schermata. Selenium Manager scarica da solo il
chromedriver compatibile puntando a binary_path.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

log = logging.getLogger(__name__)


@dataclass
class BrowserSettings:
    binary_path: str = ""
    headless: bool = True
    window_size: tuple[int, int] = (1280, 900)
    timeouts: dict | None = None


class Browser:
    def __init__(self, settings: BrowserSettings):
        self.settings = settings
        self.driver: webdriver.Chrome | None = None

    def _options(self) -> Options:
        o = Options()
        if self.settings.binary_path:
            o.binary_location = self.settings.binary_path
        o.add_argument("--no-sandbox")  # necessario in container / come root
        o.add_argument("--disable-dev-shm-usage")  # /dev/shm piccolo nei container
        o.add_argument("--disable-gpu")
        o.add_argument("--window-size=%d,%d" % self.settings.window_size)
        # eager: ritorna appena il DOM è pronto (non attende subresources)
        o.page_load_strategy = "eager"
        if self.settings.headless:
            o.add_argument("--headless=new")
        o.add_experimental_option("excludeSwitches", ["enable-automation"])
        return o

    def is_alive(self) -> bool:
        """Verifica se il driver è attivo e risponde al WebDriver."""
        if self.driver is None:
            return False
        try:
            _ = self.driver.current_window_handle
            return True
        except Exception:
            return False

    def start(self, force_restart: bool = False) -> webdriver.Chrome:
        if not force_restart and self.driver is not None:
            if self.is_alive():
                return self.driver
            log.warning("Driver Chrome non più reattivo o terminato: riavvio istanza pulita")
            self.stop()
        elif force_restart and self.driver is not None:
            self.stop()

        log.info("Avvio Chrome (headless=%s)", self.settings.headless)
        self.driver = webdriver.Chrome(options=self._options())
        t = self.settings.timeouts or {}
        self.driver.set_page_load_timeout(t.get("page", 60))
        self.driver.implicitly_wait(t.get("implicit", 0))
        return self.driver

    def stop(self) -> None:
        if self.driver is not None:
            try:
                self.driver.quit()
            except Exception as e:  # noqa: BLE001
                log.debug("quit err: %s", e)
            finally:
                self.driver = None

    def restart(self) -> webdriver.Chrome:
        """Forza il riavvio completo del browser."""
        self.stop()
        return self.start()

    def set_headless(self, headless: bool) -> None:
        """Cambia la modalità headless riavviando il driver se necessario."""
        if self.settings.headless != headless:
            self.settings.headless = headless
            if self.driver is not None:
                log.info("Cambio modalità headless=%s -> riavvio browser", headless)
                self.stop()

    def wait(self, timeout: float = 15):
        return WebDriverWait(self.driver, timeout)

    def ec(self):
        return EC

    def screenshot(self, path: str) -> None:
        """Schermata per debugging / prova."""
        if self.driver is None:
            return
        try:
            self.driver.save_screenshot(path)
        except Exception as e:  # noqa: BLE001
            log.warning("screenshot fallita: %s", e)
