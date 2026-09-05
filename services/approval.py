"""Coda di approvazione: sincronizza il bot Telegram (asincrono) con il
poller Selenium (sincrono) tramite un file JSON condiviso.

Può girare su 2 processi (bot e poller separati) o 1 solo; il polling del
file è abbastanza semplice da non richiedere lock.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Optional


class ApprovalQueue:
    def __init__(self, path: Path, timeout_seconds: int, discard_on_timeout: bool = True):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout_seconds
        self.discard_on_timeout = discard_on_timeout
        self._lock = threading.Lock()

    # ---- storage ----
    def _read(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            with self._lock:
                return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}

    def _write(self, data: dict) -> None:
        with self._lock:
            self.path.write_text(
                json.dumps(data, indent=2, default=str), encoding="utf-8")

    # ---- API usate dal poller (Flusso) ----
    def create(self, monitor_id: str, payload: dict) -> str:
        """Crea una richiesta di approvazione in attesa. Ritorna un id CORTO
        (per non superare i 64 byte dei callback_data di Telegram)."""
        import uuid
        req_id = uuid.uuid4().hex[:10]
        data = self._read()
        data[req_id] = {
            "monitor_id": monitor_id,
            "payload": payload,
            "created": time.time(),
            "status": "pending",
            "decision": None,
        }
        self._write(data)
        return req_id

    def wait_decision(self, req_id: str) -> Optional[dict]:
        """Attende (polling) la decisione dell'utente via bot. Bloccante."""
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            data = self._read()
            req = data.get(req_id)
            if req and req["status"] == "decided":
                return req
            if req and req["status"] == "cancelled":
                return {"cancelled": True}
            time.sleep(2)
        if self.discard_on_timeout:
            self.cancel(req_id)
        return {"cancelled": True, "reason": "timeout"}

    def cancel(self, req_id: str) -> None:
        data = self._read()
        if req_id in data:
            data[req_id]["status"] = "cancelled"
            self._write(data)

    # ---- API usate dal bot (Telegram) ----
    def get_pending(self) -> dict:
        return {k: v for k, v in self._read().items() if v["status"] == "pending"}

    def decide(self, req_id: str, approved: bool, extra: dict = None) -> bool:
        data = self._read()
        if req_id not in data:
            return False
        data[req_id]["status"] = "decided"
        data[req_id]["decision"] = approved
        data[req_id]["extra"] = extra or {}
        self._write(data)
        return True
