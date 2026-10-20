"""Selenium wrapper.

Manages browser startup (headless or visible), instance reuse, and screenshot logging.
Selenium Manager automatically resolves and downloads matching chromedrivers pointing to binary_path.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
import shutil
import sys
from dataclasses import dataclass

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

log = logging.getLogger(__name__)


def is_valid_executable(path: Path) -> bool:
    """Checks if the file exists and is a valid executable for the current platform."""
    if not path.is_file():
        return False
    if sys.platform == "win32":
        return path.suffix.lower() in (".exe", ".cmd", ".bat")
    return os.access(path, os.X_OK)


def find_chrome_executable(configured_path: str = "") -> str | None:
    """Resolves the Chrome or Chromium executable path.

    Checks whether the configured path is valid for the current platform;
    otherwise scans standard system installation directories.
    """
    if configured_path:
        p = Path(configured_path).expanduser()
        if is_valid_executable(p):
            return str(p.resolve())
        which_path = shutil.which(configured_path)
        if which_path and is_valid_executable(Path(which_path)):
            return which_path
        log.warning(
            "Configured Chrome path '%s' is invalid or not found on %s. Searching automatically...",
            configured_path,
            sys.platform,
        )

    # 1. Search in system PATH
    names = ["chrome.exe", "msedge.exe", "brave.exe"] if sys.platform == "win32" else [
        "google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"
    ]
    for name in names:
        found = shutil.which(name)
        if found and is_valid_executable(Path(found)):
            return found

    # 2. Standard paths per operating system
    candidates: list[Path] = []
    if sys.platform == "win32":
        prog_files = os.environ.get("PROGRAMFILES", "C:\\Program Files")
        prog_files_x86 = os.environ.get("PROGRAMFILES(X86)", "C:\\Program Files (x86)")
        local_app_data = os.environ.get("LOCALAPPDATA", "")

        candidates.extend([
            Path(prog_files) / "Google/Chrome/Application/chrome.exe",
            Path(prog_files_x86) / "Google/Chrome/Application/chrome.exe",
            Path(local_app_data) / "Google/Chrome/Application/chrome.exe",
            Path(local_app_data) / "Chromium/Application/chrome.exe",
            Path(prog_files) / "BraveSoftware/Brave-Browser/Application/brave.exe",
            Path(prog_files_x86) / "Microsoft/Edge/Application/msedge.exe",
            Path(prog_files) / "Microsoft/Edge/Application/msedge.exe",
        ])
    elif sys.platform == "darwin":
        candidates.extend([
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
            Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        ])
    else:  # Linux and other Unix / Docker environments
        candidates.extend([
            Path("/usr/bin/google-chrome"),
            Path("/usr/bin/google-chrome-stable"),
            Path("/usr/bin/chromium"),
            Path("/usr/bin/chromium-browser"),
            Path("/snap/bin/chromium"),
            Path("/usr/local/bin/google-chrome"),
            Path("/usr/local/bin/chromium"),
        ])

    for c in candidates:
        if is_valid_executable(c):
            return str(c.resolve())

    return None


def find_chromedriver_executable() -> str | None:
    """Searches for a chromedriver binary in system PATH or local cache."""
    names = ["chromedriver.exe", "chromedriver"] if sys.platform == "win32" else ["chromedriver"]
    for name in names:
        found = shutil.which(name)
        if found and is_valid_executable(Path(found)):
            return found

    cache_dirs = [
        Path.home() / ".cache" / "selenium" / "chromedriver",
        Path.home() / ".local" / "share" / "selenium" / "chromedriver",
    ]
    for cdir in cache_dirs:
        if cdir.is_dir():
            driver_name = "chromedriver.exe" if sys.platform == "win32" else "chromedriver"
            matches = list(cdir.rglob(driver_name))
            for m in matches:
                if is_valid_executable(m):
                    return str(m.resolve())
    return None


def find_edge_executable() -> str | None:
    """Searches for Microsoft Edge executable on Windows as a Chromium fallback."""
    if sys.platform != "win32":
        return None
    names = ["msedge.exe", "msedge"]
    for name in names:
        found = shutil.which(name)
        if found and is_valid_executable(Path(found)):
            return found
    prog_files = os.environ.get("PROGRAMFILES", "C:\\Program Files")
    prog_files_x86 = os.environ.get("PROGRAMFILES(X86)", "C:\\Program Files (x86)")
    for p in [Path(prog_files_x86) / "Microsoft/Edge/Application/msedge.exe", Path(prog_files) / "Microsoft/Edge/Application/msedge.exe"]:
        if is_valid_executable(p):
            return str(p.resolve())
    return None


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

        o.add_argument("--no-sandbox")  # Required in containers / when running as root
        o.add_argument("--disable-dev-shm-usage")  # Smaller /dev/shm in containers
        o.add_argument("--disable-gpu")
        o.add_argument("--window-size=%d,%d" % self.settings.window_size)
        # Eager: returns as soon as the DOM is ready (does not wait for subresources)
        o.page_load_strategy = "eager"
        if self.settings.headless:
            o.add_argument("--headless=new")
        o.add_experimental_option("excludeSwitches", ["enable-automation"])
        return o

    def is_alive(self) -> bool:
        """Checks if the driver is active and responsive to WebDriver commands."""
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
        options = self._options()

        # 1. Standard attempt using Selenium Manager
        try:
            self.driver = webdriver.Chrome(options=options)
        except Exception as first_err:
            log.warning("Avvio iniziale Chrome fallito (%s). Tento strategie di recupero...", first_err)
            started = False

            # Strategy 1: If an invalid binary_path was set, retry without it or with auto-detected binary
            if getattr(options, "binary_location", None):
                detected = find_chrome_executable()
                fallback_opts = self._options()
                if detected and detected != options.binary_location:
                    log.info("Tentativo con eseguibile Chrome rilevato nel sistema: %s", detected)
                    fallback_opts.binary_location = detected
                else:
                    log.info("Tentativo rimuovendo binary_location esplicita per usare il default di sistema")
                    fallback_opts.binary_location = ""
                try:
                    self.driver = webdriver.Chrome(options=fallback_opts)
                    started = True
                except Exception as second_err:
                    log.warning("Tentativo di recupero con Chrome di sistema fallito: %s", second_err)

            # Strategy 2: If driver discovery fails, retry with locally discovered chromedriver
            if not started:
                driver_bin = find_chromedriver_executable()
                if driver_bin:
                    try:
                        log.info("Tentativo con chromedriver locale trovato in: %s", driver_bin)
                        self.driver = webdriver.Chrome(service=Service(executable_path=driver_bin), options=options)
                        started = True
                    except Exception as third_err:
                        log.warning("Fallback con chromedriver locale fallito: %s", third_err)

            # Strategy 3: If still failing on Windows, try Microsoft Edge Chromium fallback
            if not started and sys.platform == "win32":
                try:
                    from selenium.webdriver.edge.options import Options as EdgeOptions
                    edge_exe = find_edge_executable()
                    if edge_exe:
                        log.info("Tentativo fallback con Microsoft Edge Chromium (%s)...", edge_exe)
                        eo = EdgeOptions()
                        eo.binary_location = edge_exe
                        if self.settings.headless:
                            eo.add_argument("--headless=new")
                        eo.add_argument("--no-sandbox")
                        eo.add_argument("--disable-gpu")
                        self.driver = webdriver.Edge(options=eo)
                        started = True
                except Exception as edge_err:
                    log.debug("Fallback Edge non riuscito: %s", edge_err)

            if not started:
                err_msg = (
                    "Impossibile avviare il driver Chrome. "
                    "Verifica che Google Chrome sia installato nel sistema oppure imposta CHROME_BIN nel file .env.\n"
                    f"Dettaglio: {first_err}"
                )
                log.error(err_msg)
                raise RuntimeError(err_msg) from first_err

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
        """Forces a complete browser restart."""
        self.stop()
        return self.start()

    def set_headless(self, headless: bool) -> None:
        """Toggles headless mode, restarting the driver if needed."""
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
        """Captures a screenshot for debugging or inspection."""
        if self.driver is None:
            return
        try:
            self.driver.save_screenshot(path)
        except Exception as e:  # noqa: BLE001
            log.warning("screenshot fallita: %s", e)
