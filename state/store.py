"""State store: snapshot della lista disponibilità per evitare notifiche doppie.

Salva, per ogni monitor id, l'ultimo set di slot visti + lo stato dell'azione
(prenotato/riprogrammato) così da non riproporre la stessa cosa.
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime
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

    def record_slots(self, monitor_id: str, slot_keys: list[str], max_history: int = 300) -> None:
        entry = self.data.setdefault(monitor_id, {})
        merged = list(dict.fromkeys([*entry.get("seen", []), *slot_keys]))
        if len(merged) > max_history:
            merged = merged[-max_history:]
        entry["seen"] = merged
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

    def get_action(self, monitor_id: str) -> str:
        return self.data.get(monitor_id, {}).get("action_state", "idle") or "idle"

    # ---- blacklist disponibilità rifiutate ----
    def add_blacklist_entry(
        self,
        monitor_id: str,
        slot,
        nre: str = "",
        data_ricetta: str = "",
    ) -> dict:
        """Aggiunge uno slot rifiutato alla blacklist per il monitor indicato.

        La voce resta valida per tutta la durata di validità della ricetta
        (1 anno / 365 giorni dalla data ricetta, oppure 1 anno dalla data rifiuto).
        """
        now = time.time()
        valid_until = now + 365 * 86400  # default 1 anno

        if data_ricetta:
            try:
                raw_d = str(data_ricetta).strip()
                if "-" in raw_d:
                    dt_r = datetime.strptime(raw_d[:10], "%Y-%m-%d")
                else:
                    dt_r = datetime.strptime(raw_d[:10], "%d/%m/%Y")
                valid_until = dt_r.timestamp() + 365 * 86400
            except Exception:
                valid_until = now + 365 * 86400

        # Estrai i campi da Slot o da dict
        slot_key = ""
        date_str = ""
        time_str = ""
        extra = {}
        if hasattr(slot, "key"):
            slot_key = slot.key
            date_str = getattr(slot, "date_str", "")
            time_str = getattr(slot, "time_str", "")
            extra = getattr(slot, "extra", {}) or {}
        elif isinstance(slot, dict):
            s = slot.get("slot") if "slot" in slot and isinstance(slot["slot"], dict) else slot
            slot_key = s.get("key", "")
            date_str = s.get("date_str", "")
            time_str = s.get("time_str", "")
            extra = s.get("extra", {}) or {}
            if not date_str and "datetime" in s:
                try:
                    dt_val = s["datetime"]
                    if isinstance(dt_val, str):
                        dt_obj = datetime.fromisoformat(dt_val)
                    else:
                        dt_obj = dt_val
                    date_str = dt_obj.strftime("%d/%m/%Y")
                    time_str = dt_obj.strftime("%H:%M")
                except Exception:
                    pass
            if not slot_key and date_str and time_str:
                slot_key = f"{date_str}_{time_str}"

        azienda = extra.get("azienda", "")
        sede = extra.get("sede", "")
        comune = extra.get("comune", "")
        provincia = extra.get("provincia", "")

        entry_data = self.data.setdefault(monitor_id, {})
        bl = entry_data.setdefault("blacklist", [])

        # Se già presente con la stessa slot_key, aggiorna
        for existing in bl:
            if existing.get("slot_key") and existing.get("slot_key") == slot_key:
                existing["rejected_at"] = now
                existing["valid_until"] = valid_until
                if nre:
                    existing["nre"] = nre
                self._flush()
                return existing

        item_id = uuid.uuid4().hex[:8]
        new_entry = {
            "id": item_id,
            "slot_key": slot_key,
            "date_str": date_str,
            "time_str": time_str,
            "azienda": azienda,
            "sede": sede,
            "comune": comune,
            "provincia": provincia,
            "rejected_at": now,
            "valid_until": valid_until,
            "nre": nre,
        }
        bl.append(new_entry)
        # Assicura che la slot_key sia anche in seen per non riproporla subito
        if slot_key:
            merged = list(dict.fromkeys([*entry_data.get("seen", []), slot_key]))
            entry_data["seen"] = merged
        self._flush()
        return new_entry

    def get_blacklist(self, monitor_id: str, clean_expired: bool = True) -> list[dict]:
        """Ritorna la blacklist attiva per il monitor.

        Se clean_expired=True, rimuove le voci con validità scaduta.
        """
        raw = self.data.get(monitor_id, {}).get("blacklist", [])
        if not clean_expired:
            return list(raw)

        now = time.time()
        active = []
        expired_keys = []
        for item in raw:
            if item.get("valid_until", now + 1) > now:
                active.append(item)
            else:
                expired_keys.append(item.get("slot_key"))

        if len(active) != len(raw):
            self.data.setdefault(monitor_id, {})["blacklist"] = active
            seen = self.data.get(monitor_id, {}).get("seen", [])
            if seen and expired_keys:
                self.data[monitor_id]["seen"] = [k for k in seen if k not in expired_keys]
            self._flush()
        return active

    def get_blacklist_keys(self, monitor_id: str) -> set[str]:
        """Set delle slot_key attualmente in blacklist per monitor_id."""
        return {item["slot_key"] for item in self.get_blacklist(monitor_id) if item.get("slot_key")}

    def remove_blacklist_entry(self, monitor_id: str, entry_id: str) -> dict | None:
        """Rimuove una specifica entry dalla blacklist e da seen_slots."""
        bl = self.data.get(monitor_id, {}).get("blacklist", [])
        removed = None
        remaining = []
        for item in bl:
            if item.get("id") == entry_id:
                removed = item
            else:
                remaining.append(item)

        if removed is not None:
            self.data.setdefault(monitor_id, {})["blacklist"] = remaining
            sk = removed.get("slot_key")
            seen = self.data.get(monitor_id, {}).get("seen", [])
            if sk and seen and sk in seen:
                self.data[monitor_id]["seen"] = [s for s in seen if s != sk]
            self._flush()
        return removed

    def clear_blacklist(self, monitor_id: str) -> int:
        """Svuota l'intera blacklist per il monitor e rimuove le relative chiavi da seen."""
        bl = self.data.get(monitor_id, {}).get("blacklist", [])
        count = len(bl)
        keys_to_remove = {item.get("slot_key") for item in bl if item.get("slot_key")}
        self.data.setdefault(monitor_id, {})["blacklist"] = []
        seen = self.data.get(monitor_id, {}).get("seen", [])
        if seen and keys_to_remove:
            self.data[monitor_id]["seen"] = [s for s in seen if s not in keys_to_remove]
        self._flush()
        return count

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
        # rimuove dai disabilitati se era stato disabilitato
        disabled = self.data.get("_disabled_monitors", [])
        if monitor["id"] in disabled:
            disabled = [m for m in disabled if m != monitor["id"]]
            self.data["_disabled_monitors"] = disabled
        self._flush()

    def remove_monitor(self, monitor_id: str) -> None:
        self.data.setdefault("_monitors", {}).pop(monitor_id, None)
        disabled = self.data.setdefault("_disabled_monitors", [])
        if monitor_id not in disabled:
            disabled.append(monitor_id)
        # marca anche action_state come terminato/rimosso
        self.mark_action(monitor_id, "done", "monitor rimosso/fermato")
        self._flush()

    def is_disabled(self, monitor_id: str) -> bool:
        return monitor_id in self.data.get("_disabled_monitors", [])

    def get_monitors(self) -> list:
        return list(self.data.get("_monitors", {}).values())
