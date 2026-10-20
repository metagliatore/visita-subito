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
    dt = datetime(2026, 10, 5, 9, 30)  # Monday October 5, 2026 at 09:30
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

    # Verify unpadded hours (e.g. "9:00" instead of "09:00") do not fail string comparison
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

    # ISO format (YYYY-MM-DD)
    ok_iso, _ = match_slot(slot, {"data_dal": "2026-10-01", "data_a": "2026-10-31"})
    assert ok_iso is True

    # Exclusion when data_dal ISO is after the slot
    ok_iso_late, reason = match_slot(slot, {"data_dal": "2026-10-20"})
    assert ok_iso_late is False
    assert "prima del range" in reason

    # datetime.date objects (e.g. returned by YAML parser)
    ok_date_obj, _ = match_slot(slot, {"data_dal": date(2026, 10, 1), "data_a": date(2026, 10, 31)})
    assert ok_date_obj is True

    # Exclusion when data_a is before the slot
    ok_date_early, reason2 = match_slot(slot, {"data_a": date(2026, 10, 10)})
    assert ok_date_early is False
    assert "dopo il range" in reason2


def test_best_match_ordering():
    # Prioritize days in user-specified order
    s_wed = Slot(datetime=datetime(2026, 10, 7, 10, 0))  # Wednesday
    s_mon = Slot(datetime=datetime(2026, 10, 12, 10, 0)) # Next Monday

    crit = {"giorni": ["WED", "MON"]}
    selected = best_match([s_mon, s_wed], crit)
    assert selected == s_wed


def test_nessuna_disponibilita():
    assert nessuna_disponibilita("Non sono state trovate disponibilità per i criteri selezionati") is True
    assert nessuna_disponibilita("Al momento non ci sono disponibilita' online") is True
    assert nessuna_disponibilita("Seleziona una data per prenotare") is False


def test_match_slot_same_day_time_anticipation():
    # Current appointment on 25/10/2026 at 10:00
    crit = {"data_attuale": "25/10/2026 10:00", "data_a": "25/10/2026"}

    # Slot before current date -> OK
    s_earlier_day = Slot(datetime=datetime(2026, 10, 24, 15, 0))
    ok, _ = match_slot(s_earlier_day, crit)
    assert ok is True

    # Slot same day but earlier time (09:15 < 10:00) -> OK
    s_same_day_earlier = Slot(datetime=datetime(2026, 10, 25, 9, 15))
    ok, _ = match_slot(s_same_day_earlier, crit)
    assert ok is True

    # Slot same day and same time (10:00 == 10:00) -> Discarded
    s_same_day_same_time = Slot(datetime=datetime(2026, 10, 25, 10, 0))
    ok, reason = match_slot(s_same_day_same_time, crit)
    assert ok is False
    assert "orario non migliorativo" in reason

    # Slot same day but later time (11:30 > 10:00) -> Discarded
    s_same_day_later = Slot(datetime=datetime(2026, 10, 25, 11, 30))
    ok, reason = match_slot(s_same_day_later, crit)
    assert ok is False
    assert "orario non migliorativo" in reason

    # Format with hyphen '25/10/2026 - 10:00' and datetime object
    crit_dash = {"data_attuale": "25/10/2026 - 10:00"}
    ok_dash, _ = match_slot(s_same_day_earlier, crit_dash)
    assert ok_dash is True
    ok_dash_late, reason = match_slot(s_same_day_later, crit_dash)
    assert ok_dash_late is False
    assert "orario non migliorativo" in reason

    crit_dt = {"data_attuale": datetime(2026, 10, 25, 10, 0)}
    ok_dt, _ = match_slot(s_same_day_earlier, crit_dt)
    assert ok_dt is True
    ok_dt_late, _ = match_slot(s_same_day_later, crit_dt)
    assert ok_dt_late is False

