from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch
import pytest

from services.deadline import (
    easter_date,
    italian_holidays,
    is_working_day,
    parse_appuntamento_datetime,
    calculate_reschedule_deadline,
    check_reschedule_deadline,
    get_reschedule_min_working_days,
)


def test_easter_and_holidays():
    # Western Easter dates
    assert easter_date(2025) == date(2025, 4, 20)
    assert easter_date(2026) == date(2026, 4, 5)
    assert easter_date(2027) == date(2027, 3, 28)

    hols_2026 = italian_holidays(2026)
    # Check fixed holidays
    assert date(2026, 1, 1) in hols_2026
    assert date(2026, 1, 6) in hols_2026
    assert date(2026, 4, 25) in hols_2026
    assert date(2026, 5, 1) in hols_2026
    assert date(2026, 6, 2) in hols_2026
    assert date(2026, 8, 15) in hols_2026
    assert date(2026, 11, 1) in hols_2026
    assert date(2026, 12, 8) in hols_2026
    assert date(2026, 12, 25) in hols_2026
    assert date(2026, 12, 26) in hols_2026
    # Check Pasquetta (Easter Monday)
    assert date(2026, 4, 6) in hols_2026


def test_is_working_day():
    # Saturday / Sunday
    assert not is_working_day(date(2026, 11, 7))  # Saturday
    assert not is_working_day(date(2026, 11, 8))  # Sunday
    # National holidays
    assert not is_working_day(date(2026, 1, 1))   # New Year
    assert not is_working_day(date(2026, 5, 1))   # Labor day
    assert not is_working_day(date(2026, 4, 6))   # Pasquetta
    # Normal working days (Mon-Fri)
    assert is_working_day(date(2026, 11, 9))   # Monday
    assert is_working_day(date(2026, 11, 10))  # Tuesday
    assert is_working_day(date(2026, 11, 11))  # Wednesday
    assert is_working_day(date(2026, 11, 12))  # Thursday
    assert is_working_day(date(2026, 11, 13))  # Friday


def test_parse_appuntamento_datetime():
    # None or empty
    assert parse_appuntamento_datetime(None) is None
    assert parse_appuntamento_datetime("") is None

    # Already datetime or date
    dt = datetime(2026, 11, 20, 10, 30)
    assert parse_appuntamento_datetime(dt) == dt
    d = date(2026, 11, 20)
    assert parse_appuntamento_datetime(d) == datetime(2026, 11, 20, 0, 0)

    # String with DD/MM/YYYY HH:MM
    assert parse_appuntamento_datetime("20/11/2026 10:30") == datetime(2026, 11, 20, 10, 30)
    # String with separator dash
    assert parse_appuntamento_datetime("25/10/2026 - 14:15") == datetime(2026, 10, 25, 14, 15)
    # String with seconds
    assert parse_appuntamento_datetime("20/11/2026 10:30:45") == datetime(2026, 11, 20, 10, 30, 45)
    # String with YYYY-MM-DD
    assert parse_appuntamento_datetime("2026-11-20 09:00") == datetime(2026, 11, 20, 9, 0)
    # String date only
    assert parse_appuntamento_datetime("20/11/2026") == datetime(2026, 11, 20, 0, 0)


def test_calculate_reschedule_deadline_weekdays():
    # Monday 16 Nov 2026 10:30 -> 3 working days back (Fri 13, Thu 12, Wed 11)
    app = datetime(2026, 11, 16, 10, 30)
    dl = calculate_reschedule_deadline(app, 3)
    assert dl == datetime(2026, 11, 11, 10, 30)

    # Tuesday 17 Nov 2026 14:00 -> 3 working days back (Mon 16, Fri 13, Thu 12)
    app = datetime(2026, 11, 17, 14, 0)
    dl = calculate_reschedule_deadline(app, 3)
    assert dl == datetime(2026, 11, 12, 14, 0)

    # Wednesday 18 Nov 2026 09:15 -> 3 working days back (Tue 17, Mon 16, Fri 13)
    app = datetime(2026, 11, 18, 9, 15)
    dl = calculate_reschedule_deadline(app, 3)
    assert dl == datetime(2026, 11, 13, 9, 15)

    # Friday 20 Nov 2026 11:00 -> 3 working days back (Thu 19, Wed 18, Tue 17)
    app = datetime(2026, 11, 20, 11, 0)
    dl = calculate_reschedule_deadline(app, 3)
    assert dl == datetime(2026, 11, 17, 11, 0)


def test_calculate_reschedule_deadline_with_holidays():
    # Appointment Monday 4 May 2026 10:00
    # Fri 1 May is national holiday.
    # Backwards from Mon 4:
    # Sun 3 (weekend), Sat 2 (weekend), Fri 1 (holiday), Thu 30 Apr (1), Wed 29 Apr (2), Tue 28 Apr (3)
    app = datetime(2026, 5, 4, 10, 0)
    dl = calculate_reschedule_deadline(app, 3)
    assert dl == datetime(2026, 4, 28, 10, 0)


def test_calculate_reschedule_deadline_date_only():
    # Date only (00:00 time) sets deadline time to 23:59:59 on the deadline day
    app = datetime(2026, 11, 16, 0, 0)
    dl = calculate_reschedule_deadline(app, 3)
    assert dl == datetime(2026, 11, 11, 23, 59, 59)


def test_check_reschedule_deadline():
    app_str = "16/11/2026 10:30"
    # 1. Well in advance (e.g. 5 Nov 2026) -> not too late
    too_late, app_dt, dl_dt, msg = check_reschedule_deadline(
        app_str, min_working_days=3, now=datetime(2026, 11, 5, 12, 0)
    )
    assert not too_late
    assert app_dt == datetime(2026, 11, 16, 10, 30)
    assert dl_dt == datetime(2026, 11, 11, 10, 30)
    assert "Termine utile" in msg

    # 2. Before deadline time on deadline day (11 Nov 10:00) -> not too late
    too_late, _, _, _ = check_reschedule_deadline(
        app_str, min_working_days=3, now=datetime(2026, 11, 11, 10, 0)
    )
    assert not too_late

    # 3. After deadline time on deadline day (11 Nov 11:00) -> too late!
    too_late, _, _, msg = check_reschedule_deadline(
        app_str, min_working_days=3, now=datetime(2026, 11, 11, 11, 0)
    )
    assert too_late
    assert "Mancano meno di 3 giorni lavorativi" in msg
    assert "malus ticket" in msg

    # 4. Check disabled with min_working_days = 0
    too_late, _, dl_dt, _ = check_reschedule_deadline(
        app_str, min_working_days=0, now=datetime(2026, 11, 15, 12, 0)
    )
    assert not too_late
    assert dl_dt is None

    # 5. Invalid date
    too_late, app_dt, _, _ = check_reschedule_deadline("invalid-date")
    assert not too_late
    assert app_dt is None


def test_get_reschedule_min_working_days(monkeypatch):
    # Default is 3
    monkeypatch.delenv("RESCHEDULE_MIN_WORKING_DAYS", raising=False)
    assert get_reschedule_min_working_days() == 3

    # Config in criteri override
    assert get_reschedule_min_working_days(criteri={"reschedule_min_working_days": 5}) == 5

    # Environment variable override
    monkeypatch.setenv("RESCHEDULE_MIN_WORKING_DAYS", "4")
    assert get_reschedule_min_working_days() == 4


def test_flow_poll_once_auto_stops_when_deadline_passed(tmp_path):
    from flows.reschedule import RescheduleFlow
    from state.store import Store

    store = Store(tmp_path / "store.json")
    mid = "test-resched-1"
    # Appointment is Monday 16 Nov 2026 10:30, deadline is Wed 11 Nov 10:30
    store.add_monitor({
        "id": mid,
        "type": "reschedule",
        "data_attuale": "16/11/2026 10:30",
        "enabled": True,
    })

    bot = MagicMock()
    browser = MagicMock()
    queue = MagicMock()

    flow = RescheduleFlow(
        monitor={"id": mid, "type": "reschedule", "data_attuale": "16/11/2026 10:30"},
        browser=browser,
        queue=queue,
        bot=bot,
        store=store,
    )

    # Simulate now being Thursday 12 Nov 2026 (past the Wednesday deadline)
    with patch("services.deadline.get_now", return_value=datetime(2026, 11, 12, 10, 0)):
        res = flow.poll_once()

    # Verify flow stopped itself
    assert "arrestato" in res.lower()
    assert store.is_disabled(mid)
    assert store.get_action(mid) == "stopped_deadline"
    bot.notify.assert_called_once()
    assert "arrestato automaticamente" in bot.notify.call_args[0][0]
    # Browser must NOT be touched
    browser.driver.get.assert_not_called()


def test_status_text_shows_reschedule_deadline(tmp_path):
    from services.scheduler import Controller
    from state.store import Store
    from core.config import Config

    store = Store(tmp_path / "store.json")
    cfg = Config(settings={"scheduler": {"poll_interval_seconds": 60, "reschedule_min_working_days": 3}})
    bot = MagicMock()
    controller = Controller(cfg, bot=bot)
    controller.store = store

    # Add active reschedule monitor
    controller.aggiungi_monitor(
        tipo="reschedule",
        descrizione="Visita Neurologica",
        nre="0300A123",
        data_attuale="20/12/2026 10:00",
    )

    status = controller.status_text()
    assert "Appuntamento attuale: 20/12/2026 10:00" in status
    assert "Limite disdetta/spostamento senza penale:" in status


def test_cmd_anticipa_blocked_when_deadline_passed(capsys):
    from services.cli_ui import CliUI
    controller = MagicMock()
    controller.store.get_prenotazioni.return_value = {}
    ui = CliUI()
    ui.controller = controller
    # Appointment is tomorrow
    ui._appuntamenti_cache = [
        {"prestazione": "Visita Oculistica", "data_ora": "10/11/2026 09:00", "codice": "0300A999"}
    ]

    with patch("services.deadline.get_now", return_value=datetime(2026, 11, 9, 10, 0)):
        ui._cmd_anticipa("1")

    out = capsys.readouterr().out
    assert "Impossibile avviare il monitor di anticipo" in out
    assert "malus ticket" in out
    controller.aggiungi_monitor.assert_not_called()
