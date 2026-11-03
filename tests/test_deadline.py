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


def test_calculate_reschedule_deadline_christmas_new_year():
    # Christmas 2026: Dec 25 is Friday, Dec 26 is Saturday, Dec 27 is Sunday
    # Appointment Monday 28 Dec 2026 at 09:00
    # Backwards:
    # Sun 27 Dec (weekend), Sat 26 Dec (holiday), Fri 25 Dec (holiday),
    # Thu 24 Dec (working day 1), Wed 23 Dec (working day 2), Tue 22 Dec (working day 3)
    app = datetime(2026, 12, 28, 9, 0)
    dl = calculate_reschedule_deadline(app, 3)
    assert dl == datetime(2026, 12, 22, 9, 0)


def test_calculate_reschedule_deadline_easter_monday():
    # Easter 2026: Sunday April 5, Pasquetta is Monday April 6
    # Appointment Wednesday April 8 2026 at 15:00
    # Backwards:
    # Tue April 7 (working day 1), Mon April 6 (Pasquetta holiday),
    # Sun April 5 (weekend), Sat April 4 (weekend),
    # Fri April 3 (Good Friday - normal working day in Italy, working day 2),
    # Thu April 2 (working day 3)
    app = datetime(2026, 4, 8, 15, 0)
    dl = calculate_reschedule_deadline(app, 3)
    assert dl == datetime(2026, 4, 2, 15, 0)


def test_calculate_reschedule_deadline_custom_working_days():
    app = datetime(2026, 11, 16, 10, 0)  # Monday
    # 1 working day notice -> Friday 13 Nov
    assert calculate_reschedule_deadline(app, 1) == datetime(2026, 11, 13, 10, 0)
    # 5 working days notice -> Mon 9 Nov
    assert calculate_reschedule_deadline(app, 5) == datetime(2026, 11, 9, 10, 0)
    # 0 or negative -> datetime.min
    assert calculate_reschedule_deadline(app, 0) == datetime.min
    assert calculate_reschedule_deadline(app, -3) == datetime.min


def test_check_reschedule_deadline_exact_boundaries():
    # Appointment Monday 16 Nov 2026 at 10:30 -> deadline Wed 11 Nov 2026 at 10:30
    app_str = "16/11/2026 10:30"

    # Exactly 1 second before deadline -> not too late
    too_late, _, dl, _ = check_reschedule_deadline(
        app_str, min_working_days=3, now=datetime(2026, 11, 11, 10, 29, 59)
    )
    assert not too_late
    assert dl == datetime(2026, 11, 11, 10, 30)

    # Exactly on deadline -> too late!
    too_late, _, _, _ = check_reschedule_deadline(
        app_str, min_working_days=3, now=datetime(2026, 11, 11, 10, 30, 0)
    )
    assert too_late

    # 1 second after deadline -> too late!
    too_late, _, _, _ = check_reschedule_deadline(
        app_str, min_working_days=3, now=datetime(2026, 11, 11, 10, 30, 1)
    )
    assert too_late


def test_check_reschedule_deadline_format_variations():
    # ISO datetime
    assert parse_appuntamento_datetime("2026-12-15 14:30:00") == datetime(2026, 12, 15, 14, 30)
    # Separator dash
    assert parse_appuntamento_datetime("15/12/2026 - 14:30") == datetime(2026, 12, 15, 14, 30)
    # Whitespace padded
    assert parse_appuntamento_datetime("   15/12/2026  ") == datetime(2026, 12, 15, 0, 0)

    # Empty, whitespace, malformed strings
    too_late, app_dt, dl_dt, msg = check_reschedule_deadline("")
    assert not too_late and app_dt is None and dl_dt is None
    too_late, app_dt, dl_dt, msg = check_reschedule_deadline("not-a-date")
    assert not too_late and app_dt is None and dl_dt is None


def test_get_reschedule_min_working_days_fallbacks(monkeypatch):
    # Non-integer env var falls back to default 3
    monkeypatch.setenv("RESCHEDULE_MIN_WORKING_DAYS", "not_a_number")
    assert get_reschedule_min_working_days() == 3

    # Negative integer env var is accepted
    monkeypatch.setenv("RESCHEDULE_MIN_WORKING_DAYS", "-2")
    assert get_reschedule_min_working_days() == -2

    # Non-integer in criteri falls back
    monkeypatch.delenv("RESCHEDULE_MIN_WORKING_DAYS", raising=False)
    assert get_reschedule_min_working_days(criteri={"reschedule_min_working_days": "abc"}) == 3


def test_flow_poll_once_continues_when_deadline_not_passed(tmp_path):
    from flows.reschedule import RescheduleFlow
    from state.store import Store

    store = Store(tmp_path / "store.json")
    mid = "test-future-resched"
    # Appointment in 60 days
    future_date = (datetime.now() + timedelta(days=60)).strftime("%d/%m/%Y 10:30")
    store.add_monitor({
        "id": mid,
        "type": "reschedule",
        "data_attuale": future_date,
        "enabled": True,
    })

    bot = MagicMock()
    browser = MagicMock()
    queue = MagicMock()

    flow = RescheduleFlow(
        monitor={"id": mid, "type": "reschedule", "data_attuale": future_date},
        browser=browser,
        queue=queue,
        bot=bot,
        store=store,
    )
    flow._is_autenticato = MagicMock(return_value=True)
    flow.load_page = MagicMock()
    flow.check_availability = MagicMock(return_value=[])
    flow.extract_slots = MagicMock(return_value=[])

    flow.poll_once()

    # Flow must NOT be stopped or disabled
    assert not store.is_disabled(mid)
    assert store.get_action(mid) != "stopped_deadline"
    flow.load_page.assert_called_once()


def test_flow_poll_once_sources_appointment_date_from_store_if_not_in_monitor(tmp_path):
    from flows.reschedule import RescheduleFlow
    from state.store import Store

    store = Store(tmp_path / "store.json")
    mid = "test-store-sourced"
    store.add_monitor({
        "id": mid,
        "type": "reschedule",
        "enabled": True,
    })
    # data_attuale is NOT in monitor dict, but in store's recorded prenotazione
    store.record_prenotazione(mid, "0300A123", "16/11/2026 10:30")

    bot = MagicMock()
    browser = MagicMock()
    queue = MagicMock()

    flow = RescheduleFlow(
        monitor={"id": mid, "type": "reschedule"},
        browser=browser,
        queue=queue,
        bot=bot,
        store=store,
    )

    with patch("services.deadline.get_now", return_value=datetime(2026, 11, 12, 10, 0)):
        res = flow.poll_once()

    assert "arrestato" in res.lower()
    assert store.is_disabled(mid)
    assert store.get_action(mid) == "stopped_deadline"


def test_status_text_shows_warning_when_deadline_already_expired(tmp_path):
    from services.scheduler import Controller
    from state.store import Store
    from core.config import Config

    store = Store(tmp_path / "store.json")
    cfg = Config(settings={"scheduler": {"poll_interval_seconds": 60, "reschedule_min_working_days": 3}})
    bot = MagicMock()
    controller = Controller(cfg, bot=bot)
    controller.store = store

    # Add reschedule monitor with appointment in 2 days (deadline already expired)
    expired_date = (datetime.now() + timedelta(days=2)).strftime("%d/%m/%Y 10:00")
    controller.aggiungi_monitor(
        tipo="reschedule",
        descrizione="Visita Oculistica",
        nre="0300A999",
        data_attuale=expired_date,
    )

    status = controller.status_text()
    assert "Appuntamento attuale:" in status
    assert "Scaduto termine disdetta senza penale" in status


def test_cmd_anticipa_succeeds_when_appointment_is_far_in_future():
    from services.cli_ui import CliUI
    controller = MagicMock()
    controller.store.get_prenotazioni.return_value = {}
    controller.aggiungi_monitor.return_value = "mon-ok"
    ui = CliUI()
    ui.controller = controller

    future_date = (datetime.now() + timedelta(days=60)).strftime("%d/%m/%Y 09:00")
    ui._appuntamenti_cache = [
        {"prestazione": "Visita Oculistica", "data_ora": future_date, "codice": "0300A999"}
    ]

    with patch.object(ui, "_ask_criteri", return_value={"province": ["MI"], "data_dal": "", "data_a": ""}):
        ui._cmd_anticipa("1")

    controller.aggiungi_monitor.assert_called_once()
    assert controller.aggiungi_monitor.call_args[1]["tipo"] == "reschedule"
    assert controller.aggiungi_monitor.call_args[1]["data_attuale"] == future_date


def test_cli_ui_pending_postbook_choice_continues_or_blocks_based_on_deadline():
    from services.cli_ui import CliUI
    controller = MagicMock()
    ui = CliUI()
    ui.controller = controller

    # 1. Option 1 (Stop)
    ui._pending_postbook = {"mid": "mon-1"}
    ui._dispatch_input("1")
    controller.rimuovi_monitor.assert_called_with("mon-1")
    assert ui._pending_postbook is None

    # 2. Option 2 (Continue / Anticipa) with expired appointment date
    controller.store.get_prenotazione.return_value = {"data_ora": "10/11/2026 09:00"}
    ui._pending_postbook = {"mid": "mon-2"}
    with patch("services.deadline.get_now", return_value=datetime(2026, 11, 9, 10, 0)):
        ui._dispatch_input("2")

    # Should be blocked and removed
    controller.rimuovi_monitor.assert_called_with("mon-2")
    controller.converti_in_reschedule.assert_not_called()
    assert ui._pending_postbook is None

    # 3. Option 2 (Continue / Anticipa) with appointment in 60 days
    future_date = (datetime.now() + timedelta(days=60)).strftime("%d/%m/%Y 09:00")
    controller.store.get_prenotazione.return_value = {"data_ora": future_date}
    ui._pending_postbook = {"mid": "mon-3"}
    ui._dispatch_input("2")

    controller.converti_in_reschedule.assert_called_with("mon-3")
    assert ui._pending_postbook is None


def test_telegram_bot_crea_monitor_finale_blocks_expired_and_allows_valid():
    import asyncio
    from unittest.mock import AsyncMock
    from services.telegram_bot import TelegramBot

    bot = TelegramBot.__new__(TelegramBot)
    bot.controller = MagicMock()
    bot.chat_ids = ["12345"]
    bot._wizard = {}

    # Case A: Reschedule with appointment tomorrow -> blocked!
    q_mock = MagicMock()
    q_mock.edit_message_text = AsyncMock()
    update_mock = MagicMock()
    update_mock.callback_query = q_mock

    wiz_expired = {
        "tipo": "reschedule",
        "target": {"prestazione": "Visita Dermatologica", "data_ora": "10/11/2026 09:00", "codice": "0300A1"},
        "province": ["MI"],
        "data_dal": "",
        "data_a": "",
    }
    with patch("services.deadline.get_now", return_value=datetime(2026, 11, 9, 10, 0)):
        asyncio.run(bot._crea_monitor_finale(update_mock, "12345", wiz_expired))

    q_mock.edit_message_text.assert_called_once()
    assert "Impossibile avviare il monitor di anticipo" in q_mock.edit_message_text.call_args[0][0]
    bot.controller.aggiungi_monitor.assert_not_called()

    # Case B: Reschedule with appointment in 60 days -> allowed!
    future_date = (datetime.now() + timedelta(days=60)).strftime("%d/%m/%Y 09:00")
    bot.controller.aggiungi_monitor.return_value = "res-new-mid"
    q_mock.edit_message_text.reset_mock()

    wiz_valid = {
        "tipo": "reschedule",
        "target": {"prestazione": "Visita Dermatologica", "data_ora": future_date, "codice": "0300A1"},
        "province": ["MI"],
        "data_dal": "",
        "data_a": "",
    }
    asyncio.run(bot._crea_monitor_finale(update_mock, "12345", wiz_valid))

    bot.controller.aggiungi_monitor.assert_called_once()
    assert bot.controller.aggiungi_monitor.call_args[1]["data_attuale"] == future_date
    q_mock.edit_message_text.assert_called_once()
    assert "Monitor creato" in q_mock.edit_message_text.call_args[0][0]


def test_flow_request_approval_displays_anticipo_singular_day_and_same_day():
    from services.matcher import Slot
    from flows.base import Flow

    class TestFlow(Flow):
        def load_page(self): pass
        def extract_slots(self): return []
        def execute(self, slot): pass

    # Subcase 1: Exactly 1 day of advance -> "1 giorno di anticipo" (singular)
    flow1 = TestFlow(
        monitor={"id": "m1", "type": "reschedule", "data_attuale": "16/11/2026 10:00"},
        browser=MagicMock(),
        queue=MagicMock(),
        bot=MagicMock(),
        store=MagicMock(),
    )
    flow1.queue.create.return_value = "req1"
    flow1.queue.wait_decision.return_value = {"cancelled": True, "reason": "user"}

    slot1 = Slot(datetime=datetime(2026, 11, 15, 10, 0))
    flow1._request_approval(slot1)

    msg1 = flow1.bot.notify_buttons.call_args[0][0]
    assert "Nuova data proposta: 15/11/2026 ore 10:00" in msg1
    assert "📌 Appuntamento attuale da sostituire: 16/11/2026 10:00 (1 giorno di anticipo)" in msg1

    # Subcase 2: Same day, earlier time -> "stesso giorno, orario precedente"
    flow2 = TestFlow(
        monitor={"id": "m2", "type": "reschedule", "data_attuale": "15/11/2026 14:00"},
        browser=MagicMock(),
        queue=MagicMock(),
        bot=MagicMock(),
        store=MagicMock(),
    )
    flow2.queue.create.return_value = "req2"
    flow2.queue.wait_decision.return_value = {"cancelled": True, "reason": "user"}

    slot2 = Slot(datetime=datetime(2026, 11, 15, 9, 30))
    flow2._request_approval(slot2)

    msg2 = flow2.bot.notify_buttons.call_args[0][0]
    assert "Nuova data proposta: 15/11/2026 ore 09:30" in msg2
    assert "📌 Appuntamento attuale da sostituire: 15/11/2026 14:00 (stesso giorno, orario precedente)" in msg2

    # Subcase 3: New booking flow without data_attuale -> "Data:" and no "da sostituire"
    flow3 = TestFlow(
        monitor={"id": "m3", "type": "new"},
        browser=MagicMock(),
        queue=MagicMock(),
        bot=MagicMock(),
        store=MagicMock(),
    )
    flow3.store.get_prenotazione.return_value = {}
    flow3.queue.create.return_value = "req3"
    flow3.queue.wait_decision.return_value = {"cancelled": True, "reason": "user"}

    slot3 = Slot(datetime=datetime(2026, 11, 15, 10, 0))
    flow3._request_approval(slot3)

    msg3 = flow3.bot.notify_buttons.call_args[0][0]
    assert "📅 Data: 15/11/2026 ore 10:00" in msg3
    assert "da sostituire" not in msg3

