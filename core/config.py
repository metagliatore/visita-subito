"""Loads configuration from YAML into simple dataclasses."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


def get_base_dir() -> Path:
    """Directory base dell'applicazione.
    Se congelata con PyInstaller, punta alla cartella dell'eseguibile,
    altrimenti alla cartella radice del codice sorgente.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def get_bundle_dir() -> Path:
    """Directory dei file di risorsa distribuiti (bundled assets).
    In PyInstaller punta a sys._MEIPASS, altrimenti a BASE_DIR.
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return get_base_dir()


BASE_DIR = get_base_dir()
BUNDLE_DIR = get_bundle_dir()
ENV_FILE = BASE_DIR / ".env"


def _load_dotenv(path: Path = ENV_FILE) -> None:
    """Loads a .env file (simple parser: VAR=value, comments, no export)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        # Do not overwrite variables already present in the environment
        if k and k not in os.environ:
            os.environ[k] = v


def reload_dotenv(path: Path = ENV_FILE) -> None:
    """Ricarica le variabili d'ambiente da un file .env forzando l'aggiornamento."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k:
            os.environ[k] = v


_load_dotenv()


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}


@dataclass
class Config:
    settings: dict = field(default_factory=dict)
    monitors: list = field(default_factory=list)

    @property
    def active_monitors(self) -> list:
        return [m for m in self.monitors if m.get("enabled", True)]

    @classmethod
    def load(cls, config_dir: Path | None = None) -> "Config":
        cfg_dir = config_dir or (BASE_DIR / "config")
        bundle_cfg = BUNDLE_DIR / "config"

        # Se la cartella config/ non esiste localmente ma è presente nel bundle (es. pyinstaller),
        # prova a copiarla per rendere modificabili settings.yaml e monitors.yaml
        if config_dir is None and not cfg_dir.exists() and bundle_cfg.exists() and bundle_cfg != cfg_dir:
            try:
                import shutil
                shutil.copytree(bundle_cfg, cfg_dir)
            except Exception:
                pass

        settings_path = cfg_dir / "settings.yaml"
        if not settings_path.exists() and (bundle_cfg / "settings.yaml").exists():
            settings_path = bundle_cfg / "settings.yaml"

        monitors_path = cfg_dir / "monitors.yaml"
        if not monitors_path.exists() and (bundle_cfg / "monitors.yaml").exists():
            monitors_path = bundle_cfg / "monitors.yaml"

        settings = _load(settings_path)
        monitors = _load(monitors_path).get("monitors", [])
        return cls(settings=settings, monitors=monitors)


def section(cfg: Config, name: str) -> dict:
    return cfg.settings.get(name, {})


def env_token(var: str, cfg_value: str) -> str:
    """Considers environment variable (for Docker) with fallback to YAML config."""
    return os.environ.get(var, "") or cfg_value or ""
