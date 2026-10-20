import time
import pytest
from state.store import Store


def test_store_seen_slots(tmp_path):
    store_file = tmp_path / "state.json"
    store = Store(store_file)

    assert store.seen_slots("mon-1") == set()

    store.record_slots("mon-1", ["slot_a", "slot_b"])
    assert store.seen_slots("mon-1") == {"slot_a", "slot_b"}

    # New instance from the same file reloads data
    store2 = Store(store_file)
    assert store2.seen_slots("mon-1") == {"slot_a", "slot_b"}

    # Verify historical limit (max_history)
    store.record_slots("mon-1", [f"slot_{i}" for i in range(10)], max_history=5)
    seen = store.seen_slots("mon-1")
    assert len(seen) == 5
    assert "slot_9" in seen
    assert "slot_a" not in seen  # removed to make room for more recent entries


def test_store_mark_action_and_prenotazione(tmp_path):
    store = Store(tmp_path / "state.json")

    assert store.get_action("mon-1") == "idle"
    assert store.is_prenotato("mon-1") is False

    store.mark_action("mon-1", "pending", "In attesa conferma utente")
    assert store.get_action("mon-1") == "pending"

    store.record_prenotazione("mon-1", codice="PREN12345", data_ora="2026-10-20 10:00")
    assert store.is_prenotato("mon-1") is True
    assert store.get_action("mon-1") == "prenotato"

    pren = store.get_prenotazione("mon-1")
    assert pren["codice"] == "PREN12345"
    assert pren["data_ora"] == "2026-10-20 10:00"


def test_store_dynamic_monitors(tmp_path):
    store = Store(tmp_path / "state.json")

    mon = {"id": "dyn-1", "ricetta": "VISITA DERMATOLOGICA", "type": "new"}
    store.add_monitor(mon)

    monitors = store.get_monitors()
    assert len(monitors) == 1
    assert monitors[0]["id"] == "dyn-1"

    store.remove_monitor("dyn-1")
    assert len(store.get_monitors()) == 0
    assert store.is_disabled("dyn-1") is True
    assert store.get_action("dyn-1") == "done"

    # If re-added, it must no longer be disabled and action_state must reset to idle
    store.add_monitor(mon)
    assert store.is_disabled("dyn-1") is False
    assert len(store.get_monitors()) == 1
    assert store.get_action("dyn-1") == "idle"


def test_store_blacklist(tmp_path):
    store = Store(tmp_path / "state.json")

    slot_dict = {
        "key": "20261015T1030|provincia=MI",
        "date_str": "15/10/2026",
        "time_str": "10:30",
        "extra": {
            "azienda": "ASST Niguarda",
            "sede": "Piazza Ospedale Maggiore 3",
            "comune": "Milano",
            "provincia": "MI",
        }
    }

    entry = store.add_blacklist_entry("mon-1", slot_dict, nre="0300A123", data_ricetta="2026-05-10")
    assert entry["slot_key"] == "20261015T1030|provincia=MI"
    assert entry["azienda"] == "ASST Niguarda"
    assert entry["nre"] == "0300A123"

    # slot_key must also be present in seen_slots
    assert "20261015T1030|provincia=MI" in store.seen_slots("mon-1")

    # get_blacklist
    bl = store.get_blacklist("mon-1")
    assert len(bl) == 1
    assert bl[0]["id"] == entry["id"]

    keys = store.get_blacklist_keys("mon-1")
    assert keys == {"20261015T1030|provincia=MI"}

    # Single entry removal
    removed = store.remove_blacklist_entry("mon-1", entry["id"])
    assert removed is not None
    assert removed["id"] == entry["id"]
    assert len(store.get_blacklist("mon-1")) == 0
    # Also removed from seen_slots!
    assert "20261015T1030|provincia=MI" not in store.seen_slots("mon-1")

    # Clear entire blacklist
    entry1 = store.add_blacklist_entry("mon-1", slot_dict)
    slot_dict2 = dict(slot_dict, key="20261022T1400", date_str="22/10/2026", time_str="14:00")
    entry2 = store.add_blacklist_entry("mon-1", slot_dict2)
    assert len(store.get_blacklist("mon-1")) == 2
    assert "20261015T1030|provincia=MI" in store.seen_slots("mon-1")
    assert "20261022T1400" in store.seen_slots("mon-1")

    count = store.clear_blacklist("mon-1")
    assert count == 2
    assert len(store.get_blacklist("mon-1")) == 0
    assert "20261015T1030|provincia=MI" not in store.seen_slots("mon-1")
    assert "20261022T1400" not in store.seen_slots("mon-1")

    # Verify recipe expiration
    # Add entry with recipe date 2 years ago
    old_entry = store.add_blacklist_entry("mon-1", slot_dict, data_ricetta="2024-01-01")
    assert old_entry["valid_until"] < time.time()
    # get_blacklist() must prune expired entries
    assert len(store.get_blacklist("mon-1")) == 0
    assert slot_dict["key"] not in store.seen_slots("mon-1")


def test_store_corrupted_json_or_invalid_structure(tmp_path):
    # Case 1: JSON is not a dict (e.g. list, string or corrupted file)
    f = tmp_path / "corrupt.json"
    f.write_text('["elemento_inatteso"]', encoding="utf-8")
    store = Store(f)
    assert store.data == {}
    assert store.get_monitors() == []
    assert store.is_disabled("m1") is False
    assert store.get_preferenze() == {}
    assert store.get_action("m1") == "idle"
    assert store.seen_slots("m1") == set()

    # Case 2: Monitor present but with None value or unexpected type
    store.data["m_broken"] = "non un dict"
    assert store.get_action("m_broken") == "idle"
    assert store.is_prenotato("m_broken") is False
    assert store.seen_slots("m_broken") == set()
    # Writing to m_broken does not fail and restores valid structure
    store.mark_action("m_broken", "idle", "ripristino")
    assert store.get_action("m_broken") == "idle"
    assert isinstance(store.data["m_broken"], dict)

