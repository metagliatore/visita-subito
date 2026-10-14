from datetime import datetime
import pytest

from services.matcher import Slot, _norm_day, match_slot, best_match, nessuna_disponibilita


def test_norm_day():
    assert _norm_day("lunedi") == "MON"
    assert _norm_day("lunedì") == "MON"
    assert _norm_day("lun") == "MON"
    assert _norm_day("martedi") == "TUE"
    assert _norm_day("mercoledì") == "WED"
    assert _norm_day("giovedì") == "THU"
    assert _norm_day("venerdì") == "FRI"
    assert _norm_day("sabato") == "SAT"
    assert _norm_day("domenica") == "SUN"
    assert _norm_day("MON") == "MON"
    assert _norm_day("tue") == "TUE"


def test_slot_properties():
    dt = datetime(2026, 10, 5, 9, 30)  # Lunedì 5 ottobre 2026 alle 09:30
    slot = Slot(datetime=dt, extra={"sede": "Ospedale Niguarda"})
    assert slot.weekday == "MON"
    assert slot.time_str == "09:30"
    assert slot.extra["sede"] == "Ospedale Niguarda"


def test_match_slot_days_filter():
    # 2026-10-05 is Monday, 2026-10-06 is Tuesday
    slot_mon = Slot(datetime=datetime(2026, 10, 5, 10, 0))
    slot_tue = Slot(datetime=datetime(2026, 10, 6, 10, 0))

    crit_only_mon = {"giorni": ["MON"]}
    ok, _ = match_slot(slot_mon, crit_only_mon)
    assert ok is True
    ok, _ = match_slot(slot_tue, crit_only_mon)
    assert ok is False

    crit_exclude_mon = {"escludi_giorni": ["lunedì"]}
    ok, _ = match_slot(slot_mon, crit_exclude_mon)
    assert ok is False
    ok, _ = match_slot(slot_tue, crit_exclude_mon)
    assert ok is True


def test_match_slot_hours_filter():
    slot_morning = Slot(datetime=datetime(2026, 10, 5, 9, 30))
    slot_afternoon = Slot(datetime=datetime(2026, 10, 5, 15, 0))

    crit_morning = {"ora_min": "08:00", "ora_max": "12:00"}
    ok, _ = match_slot(slot_morning, crit_morning)
    assert ok is True
    ok, _ = match_slot(slot_afternoon, crit_morning)
    assert ok is False

    # Verifica che orari senza zero padding (es. "9:00" anziché "09:00") non falliscano il confronto stringa
    crit_unpadded = {"ora_min": "9:00", "ora_max": "12"}
    ok, _ = match_slot(slot_morning, crit_unpadded)
    assert ok is True



def test_match_slot_date_range():
    slot1 = Slot(datetime=datetime(2026, 10, 10, 10, 0))
    slot2 = Slot(datetime=datetime(2026, 11, 15, 10, 0))

    crit_range = {"data_dal": "01/10/2026", "data_a": "31/10/2026"}
    ok, _ = match_slot(slot1, crit_range)
    assert ok is True
    ok, _ = match_slot(slot2, crit_range)
    assert ok is False


def test_match_slot_date_range_iso_and_date_objects():
    from datetime import date
    slot = Slot(datetime=datetime(2026, 10, 15, 10, 0))

    # Formato ISO (YYYY-MM-DD)
    ok_iso, _ = match_slot(slot, {"data_dal": "2026-10-01", "data_a": "2026-10-31"})
    assert ok_iso is True

    # Esclusione per data_dal ISO posteriore allo slot
    ok_iso_late, reason = match_slot(slot, {"data_dal": "2026-10-20"})
    assert ok_iso_late is False
    assert "prima del range" in reason

    # Oggetti datetime.date (es. restituiti da parser YAML)
    ok_date_obj, _ = match_slot(slot, {"data_dal": date(2026, 10, 1), "data_a": date(2026, 10, 31)})
    assert ok_date_obj is True

    # Esclusione per data_a anteriore allo slot
    ok_date_early, reason2 = match_slot(slot, {"data_a": date(2026, 10, 10)})
    assert ok_date_early is False
    assert "dopo il range" in reason2


def test_best_match_ordering():
    # Prioritizza i giorni nell'ordine specificato dall'utente
    s_wed = Slot(datetime=datetime(2026, 10, 7, 10, 0))  # Mercoledì
    s_mon = Slot(datetime=datetime(2026, 10, 12, 10, 0)) # Lunedì successivo

    crit = {"giorni": ["WED", "MON"]}
    selected = best_match([s_mon, s_wed], crit)
    assert selected == s_wed


def test_nessuna_disponibilita():
    assert nessuna_disponibilita("Non sono state trovate disponibilità per i criteri selezionati") is True
    assert nessuna_disponibilita("Al momento non ci sono disponibilita' online") is True
    assert nessuna_disponibilita("Seleziona una data per prenotare") is False
