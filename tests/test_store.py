import pytest
from state.store import Store


def test_store_seen_slots(tmp_path):
    store_file = tmp_path / "state.json"
    store = Store(store_file)

    assert store.seen_slots("mon-1") == set()

    store.record_slots("mon-1", ["slot_a", "slot_b"])
    assert store.seen_slots("mon-1") == {"slot_a", "slot_b"}

    # Nuova istanza dallo stesso file ricarica i dati
    store2 = Store(store_file)
    assert store2.seen_slots("mon-1") == {"slot_a", "slot_b"}

    # Verifica limite storico (max_history)
    store.record_slots("mon-1", [f"slot_{i}" for i in range(10)], max_history=5)
    seen = store.seen_slots("mon-1")
    assert len(seen) == 5
    assert "slot_9" in seen
    assert "slot_a" not in seen  # rimosso per fare spazio ai più recenti


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

    # Se riaggiunto, non deve più risultare disabilitato
    store.add_monitor(mon)
    assert store.is_disabled("dyn-1") is False
    assert len(store.get_monitors()) == 1
