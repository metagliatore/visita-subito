"""Caricamento config da YAML in dataclass semplici."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"


def _load_dotenv(path: Path = ENV_FILE) -> None:
    """Carica un file .env (semplice parser: VAR=value, commenti, no export)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        # non sovrascrive variabili già presenti nell'ambiente
        if k and k not in os.environ:
            os.environ[k] = v


_load_dotenv()


def _load(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


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
        settings = _load(cfg_dir / "settings.yaml")
        monitors = _load(cfg_dir / "monitors.yaml").get("monitors", [])
        return cls(settings=settings, monitors=monitors)


def section(cfg: Config, name: str) -> dict:
    return cfg.settings.get(name, {})


def env_token(var: str, cfg_value: str) -> str:
    """Tiene conto della env var (per Docker) con fallback alla config YAML."""
    return os.environ.get(var, "") or cfg_value or ""
