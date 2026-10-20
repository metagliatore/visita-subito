"""Approval queue: synchronizes asynchronous Telegram bot with synchronous
Selenium poller via a shared JSON file.

Can run in 2 processes (separate bot and poller) or a single one; file polling
is simple enough to not require inter-process locking.
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

    # ---- API used by poller (Flow) ----
    def create(self, monitor_id: str, payload: dict) -> str:
        """Create a pending approval request. Returns a SHORT id
        (to avoid exceeding Telegram's 64-byte callback_data limit)."""
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
        """Wait (polling) for user decision via bot. Blocking."""
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

    # ---- API used by bot (Telegram) ----
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
