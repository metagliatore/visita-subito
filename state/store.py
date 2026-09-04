"""State store: snapshot della lista disponibilità per evitare notifiche doppie.

Salva, per ogni monitor id, l'ultimo set di slot visti + lo stato dell'azione
(prenotato/riprogrammato) così da non riproporre la stessa cosa.
"""
from __future__ import annotations

import json
from pathlib import Path


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data: dict = {}
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                self.data = {}

    def _flush(self) -> None:
        self.path.write_text(
            json.dumps(self.data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def seen_slots(self, monitor_id: str) -> set:
        return set(self.data.get(monitor_id, {}).get("seen", []))

    def record_slots(self, monitor_id: str, slot_keys: list[str]) -> None:
        entry = self.data.setdefault(monitor_id, {})
        entry["seen"] = list(dict.fromkeys([*entry.get("seen", []), *slot_keys]))
        entry["last_seen_ts"] = None
        self._flush()

    def mark_action(self, monitor_id: str, state: str, detail: str = "") -> None:
        entry = self.data.setdefault(monitor_id, {})
        entry["action_state"] = state
        entry["action_detail"] = detail
        self._flush()

    def record_prenotazione(self, monitor_id: str, codice: str, data_ora: str,
                            info: dict = None) -> None:
        """Memorizza che per questo monitor è stata effettuata una prenotazione.

        Una volta prenotata, la ricetta non è più 'da prenotare': il monitor
        diventa un appuntamento esistente, gestibile via Flusso B (spostamento).
        """
        entry = self.data.setdefault(monitor_id, {})
        entry["prenotato"] = True
        entry["codice_prenotazione"] = codice
        entry["data_ora"] = data_ora
        entry["info_prenotazione"] = info or {}
        entry["action_state"] = "prenotato"
        self._flush()

    def is_prenotato(self, monitor_id: str) -> bool:
        return bool(self.data.get(monitor_id, {}).get("prenotato"))

    def get_prenotazione(self, monitor_id: str) -> dict:
        return {
            "codice": self.data.get(monitor_id, {}).get("codice_prenotazione"),
            "data_ora": self.data.get(monitor_id, {}).get("data_ora"),
            "info": self.data.get(monitor_id, {}).get("info_prenotazione", {}),
        }

    def get_action(self, monitor_id: str) -> dict:
        return self.data.get(monitor_id, {}).get("action_state", "idle") or "idle"

    # ---- preferenze utente (configurabili da Telegram) ----
    def set_preferenza(self, key: str, value) -> None:
        self.data.setdefault("_preferenze", {})[key] = value
        self._flush()

    def get_preferenze(self) -> dict:
        return self.data.get("_preferenze", {})

    # ---- monitor dinamici (creati da Telegram) ----
    def add_monitor(self, monitor: dict) -> None:
        self.data.setdefault("_monitors", {})
        self.data["_monitors"][monitor["id"]] = monitor
        self._flush()

    def remove_monitor(self, monitor_id: str) -> None:
        self.data.setdefault("_monitors", {}).pop(monitor_id, None)
        self._flush()

    def get_monitors(self) -> list:
        return list(self.data.get("_monitors", {}).values())
