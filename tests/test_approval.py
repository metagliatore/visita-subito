import time
import pytest
from services.approval import ApprovalQueue


def test_approval_create_and_decide(tmp_path):
    queue_file = tmp_path / "approval.json"
    queue = ApprovalQueue(path=queue_file, timeout_seconds=10)

    # Crea richiesta
    req_id = queue.create("mon-1", {"slot": "2026-10-15 10:00", "sede": "Milano"})
    assert isinstance(req_id, str)
    assert len(req_id) <= 10

    # Controlla pending
    pending = queue.get_pending()
    assert req_id in pending
    assert pending[req_id]["monitor_id"] == "mon-1"
    assert pending[req_id]["status"] == "pending"

    # Approva richiesta
    ok = queue.decide(req_id, approved=True, extra={"azione": "approve"})
    assert ok is True

    # Non deve più essere in pending
    assert req_id not in queue.get_pending()


def test_approval_cancel(tmp_path):
    queue_file = tmp_path / "approval.json"
    queue = ApprovalQueue(path=queue_file, timeout_seconds=10)

    req_id = queue.create("mon-2", {"slot": "2026-10-18 11:00"})
    assert req_id in queue.get_pending()

    queue.cancel(req_id)
    assert req_id not in queue.get_pending()


def test_approval_decide_nonexistent(tmp_path):
    queue_file = tmp_path / "approval.json"
    queue = ApprovalQueue(path=queue_file, timeout_seconds=10)
    assert queue.decide("fake_id", approved=True) is False
