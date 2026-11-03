import time
import pytest
from services.approval import ApprovalQueue


def test_approval_create_and_decide(tmp_path):
    queue_file = tmp_path / "approval.json"
    queue = ApprovalQueue(path=queue_file, timeout_seconds=10)

    # Create request
    req_id = queue.create("mon-1", {"slot": "2026-10-15 10:00", "sede": "Milano"})
    assert isinstance(req_id, str)
    assert len(req_id) <= 10

    # Check pending
    pending = queue.get_pending()
    assert req_id in pending
    assert pending[req_id]["monitor_id"] == "mon-1"
    assert pending[req_id]["status"] == "pending"

    # Approve request
    ok = queue.decide(req_id, approved=True, extra={"azione": "approve"})
    assert ok is True

    # Must no longer be in pending
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


def test_approval_wait_decision_decided(tmp_path):
    queue_file = tmp_path / "approval.json"
    queue = ApprovalQueue(path=queue_file, timeout_seconds=5)

    req_id = queue.create("mon-3", {"slot": "2026-10-20 09:00"})

    # In a fast thread, decide it
    import threading
    def _decide_soon():
        time.sleep(0.05)
        queue.decide(req_id, approved=True, extra={"note": "ok"})

    t = threading.Thread(target=_decide_soon)
    t.start()

    res = queue.wait_decision(req_id)
    t.join()

    assert res is not None
    assert res.get("status") == "decided"
    assert res.get("decision") is True
    assert res.get("extra") == {"note": "ok"}


def test_approval_wait_decision_cancelled(tmp_path):
    queue_file = tmp_path / "approval.json"
    queue = ApprovalQueue(path=queue_file, timeout_seconds=5)

    req_id = queue.create("mon-4", {"slot": "2026-10-20 10:00"})

    import threading
    def _cancel_soon():
        time.sleep(0.05)
        queue.cancel(req_id)

    t = threading.Thread(target=_cancel_soon)
    t.start()

    res = queue.wait_decision(req_id)
    t.join()

    assert res == {"cancelled": True}


def test_approval_wait_decision_timeout(tmp_path):
    queue_file = tmp_path / "approval.json"
    # Timeout 0.05s
    queue = ApprovalQueue(path=queue_file, timeout_seconds=0.05, discard_on_timeout=True)

    req_id = queue.create("mon-5", {"slot": "2026-10-20 11:00"})
    res = queue.wait_decision(req_id)
    assert res == {"cancelled": True, "reason": "timeout"}


def test_approval_corrupted_json(tmp_path):
    queue_file = tmp_path / "approval.json"
    queue_file.write_text("{corrupt json content", encoding="utf-8")
    queue = ApprovalQueue(path=queue_file, timeout_seconds=1)
    assert queue.get_pending() == {}

