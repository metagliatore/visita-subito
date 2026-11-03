"""Unit tests for services.calendar_sync."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from services.calendar_sync import (
    CalendarEvent,
    check_calendar_conflict,
    clear_calendar_cache,
    expand_recurring_events,
    get_calendar_buffer_minutes,
    get_calendar_conflict_mode,
    get_calendar_events,
    get_calendar_status_text,
    get_calendar_urls,
    is_calendar_sync_enabled,
    parse_ics_content,
    parse_ics_datetime,
    parse_ics_duration,
    unfold_ics,
)

SAMPLE_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Google Inc//Google Calendar 70.9054//EN
CALSCALE:GREGORIAN
METHOD:PUBLISH
BEGIN:VEVENT
UID:event_work_1@google.com
DTSTART:20261118T090000Z
DTEND:20261118T103000Z
DTSTAMP:20261103T180000Z
STATUS:CONFIRMED
SUMMARY:Riunione Team Progetto\\, Ufficio
TRANSP:OPAQUE
END:VEVENT
BEGIN:VEVENT
UID:event_dentist_2@icloud.com
DTSTART;TZID=Europe/Rome:20261122T140000
DTEND;TZID=Europe/Rome:20261122T150000
STATUS:CONFIRMED
SUMMARY:Dentista
TRANSP:OPAQUE
END:VEVENT
BEGIN:VEVENT
UID:event_duration_3@outlook.com
DTSTART:20261125T110000
DURATION:PT45M
STATUS:CONFIRMED
SUMMARY:Call Rapida
TRANSP:OPAQUE
END:VEVENT
BEGIN:VEVENT
UID:event_free_4@google.com
DTSTART:20261118T140000
DTEND:20261118T160000
STATUS:CONFIRMED
SUMMARY:Studio Personale (Libero)
TRANSP:TRANSPARENT
END:VEVENT
BEGIN:VEVENT
UID:event_cancelled_5@google.com
DTSTART:20261118T170000
DTEND:20261118T180000
STATUS:CANCELLED
SUMMARY:Aperitivo Annullato
TRANSP:OPAQUE
END:VEVENT
BEGIN:VEVENT
UID:event_allday_6@google.com
DTSTART;VALUE=DATE:20261120
DTEND;VALUE=DATE:20261121
STATUS:CONFIRMED
SUMMARY:Compleanno
TRANSP:OPAQUE
END:VEVENT
END:VCALENDAR
"""


@pytest.fixture(autouse=True)
def reset_cache():
    clear_calendar_cache()
    yield
    clear_calendar_cache()


def test_unfold_ics():
    folded = "SUMMARY:Riunione molto lunga\r\n che continua qui\n\te ancora qui"
    unfolded = unfold_ics(folded)
    assert unfolded == "SUMMARY:Riunione molto lungache continua quie ancora qui"


def test_parse_ics_duration():
    assert parse_ics_duration("PT1H") == timedelta(hours=1)
    assert parse_ics_duration("PT45M") == timedelta(minutes=45)
    assert parse_ics_duration("PT1H30M") == timedelta(hours=1, minutes=30)
    assert parse_ics_duration("P1D") == timedelta(days=1)
    assert parse_ics_duration("INVALID") == timedelta(minutes=30)


def test_parse_ics_datetime_utc_to_rome():
    # 09:00:00Z in November (CET, UTC+1) corresponds to 10:00:00 local Rome time
    dt, is_all_day = parse_ics_datetime("20261118T090000Z")
    assert is_all_day is False
    assert dt == datetime(2026, 11, 18, 10, 0, 0)


def test_parse_ics_datetime_tzid_and_floating():
    dt_rome, is_all_day = parse_ics_datetime("20261122T140000", {"TZID": "Europe/Rome"})
    assert is_all_day is False
    assert dt_rome == datetime(2026, 11, 22, 14, 0, 0)

    dt_float, is_all_day_f = parse_ics_datetime("20261125T110000")
    assert is_all_day_f is False
    assert dt_float == datetime(2026, 11, 25, 11, 0, 0)


def test_parse_ics_datetime_all_day():
    dt, is_all_day = parse_ics_datetime("20261120", {"VALUE": "DATE"})
    assert is_all_day is True
    assert dt == datetime(2026, 11, 20, 0, 0, 0)


def test_parse_ics_content_and_filters():
    now = datetime(2026, 11, 1, 0, 0)
    events = parse_ics_content(SAMPLE_ICS, ref_now=now)

    # 5 events (cancelled event is skipped)
    summaries = [e.summary for e in events]
    assert "Riunione Team Progetto, Ufficio" in summaries
    assert "Dentista" in summaries
    assert "Call Rapida" in summaries
    assert "Studio Personale (Libero)" in summaries
    assert "Compleanno" in summaries
    assert "Aperitivo Annullato" not in summaries

    # Check duration event
    call_evt = next(e for e in events if e.summary == "Call Rapida")
    assert call_evt.dt_start == datetime(2026, 11, 25, 11, 0)
    assert call_evt.dt_end == datetime(2026, 11, 25, 11, 45)

    # Check transparent event
    free_evt = next(e for e in events if e.summary == "Studio Personale (Libero)")
    assert free_evt.transparent is True


def test_expand_recurring_events():
    base_evt = CalendarEvent(
        uid="rec_standup",
        summary="Standup Giornaliero",
        dt_start=datetime(2026, 11, 2, 9, 30),
        dt_end=datetime(2026, 11, 2, 10, 0),
        status="CONFIRMED",
    )
    ref_now = datetime(2026, 11, 1, 0, 0)
    rrule = "FREQ=WEEKLY;BYDAY=MO,WE,FR"
    expanded = expand_recurring_events(base_evt, rrule, horizon_days=14, ref_now=ref_now)

    # In 14 days there are multiple Mon, Wed, Fri
    assert len(expanded) >= 5
    for inst in expanded:
        assert inst.dt_start.weekday() in (0, 2, 4)
        assert inst.summary == "Standup Giornaliero"


def test_check_calendar_conflict_disabled():
    # If not enabled, always returns no conflict
    has_conf, ev, msg = check_calendar_conflict(datetime(2026, 11, 18, 10, 0))
    assert has_conf is False
    assert ev is None
    assert msg == ""


def test_check_calendar_conflict_direct_overlap():
    now = datetime(2026, 11, 1, 0, 0)
    events = parse_ics_content(SAMPLE_ICS, ref_now=now)

    # Riunione Team Progetto is 10:00 - 11:30 (from 09:00Z)
    slot_dt = datetime(2026, 11, 18, 10, 30)

    has_conf, ev, msg = check_calendar_conflict(
        slot_dt,
        durata_minuti=30,
        buffer_minuti=0,
        criteri={"calendar_ics_url": "dummy"},
        events=events,
    )
    assert has_conf is True
    assert ev is not None
    assert "Riunione Team Progetto" in ev.summary
    assert "Conflitto con 'Riunione Team Progetto" in msg


def test_check_calendar_conflict_buffer_overlap():
    now = datetime(2026, 11, 1, 0, 0)
    events = parse_ics_content(SAMPLE_ICS, ref_now=now)

    # Meeting is 10:00 - 11:30.
    # Slot is at 12:00 (outside meeting itself), but with 45m buffer window:
    # Slot window: 12:00 - 45m = 11:15 to 12:30 + 45m = 13:15.
    # Overlaps with meeting end at 11:30!
    slot_dt = datetime(2026, 11, 18, 12, 0)

    has_conf, ev, msg = check_calendar_conflict(
        slot_dt,
        durata_minuti=30,
        buffer_minuti=45,
        criteri={"calendar_ics_url": "dummy"},
        events=events,
    )
    assert has_conf is True
    assert "incluso buffer ±45m" in msg


def test_check_calendar_conflict_free_and_transparent():
    now = datetime(2026, 11, 1, 0, 0)
    events = parse_ics_content(SAMPLE_ICS, ref_now=now)

    # Slot at 15:00 on 18/11 coincides with 'Studio Personale (Libero)'
    # but that event is TRANSPARENT (show as free), so it must NOT trigger a conflict!
    slot_dt = datetime(2026, 11, 18, 15, 0)
    has_conf, ev, _ = check_calendar_conflict(
        slot_dt,
        durata_minuti=30,
        buffer_minuti=0,
        criteri={"calendar_ics_url": "dummy"},
        events=events,
    )
    assert has_conf is False
    assert ev is None


def test_check_calendar_conflict_all_day_handling():
    now = datetime(2026, 11, 1, 0, 0)
    events = parse_ics_content(SAMPLE_ICS, ref_now=now)

    # 20/11 is Compleanno (all-day). By default, ignore_all_day=True
    slot_dt = datetime(2026, 11, 20, 11, 0)
    has_conf_ignored, _, _ = check_calendar_conflict(
        slot_dt,
        durata_minuti=30,
        buffer_minuti=0,
        criteri={"calendar_ics_url": "dummy", "calendar_ignore_all_day": True},
        events=events,
    )
    assert has_conf_ignored is False

    # If calendar_ignore_all_day=False, it should block
    has_conf_blocked, ev, _ = check_calendar_conflict(
        slot_dt,
        durata_minuti=30,
        buffer_minuti=0,
        criteri={"calendar_ics_url": "dummy", "calendar_ignore_all_day": False},
        events=events,
    )
    assert has_conf_blocked is True
    assert ev.summary == "Compleanno"


def test_local_file_ics_fetch(tmp_path, monkeypatch):
    test_ics = tmp_path / "test_calendar.ics"
    test_ics.write_text(SAMPLE_ICS, encoding="utf-8")

    monkeypatch.setenv("CALENDAR_ICS_URL", str(test_ics))
    monkeypatch.setenv("CALENDAR_SYNC_ENABLED", "true")

    assert is_calendar_sync_enabled() is True
    assert get_calendar_urls() == [str(test_ics)]

    events = get_calendar_events()
    assert len(events) >= 5
    assert any(e.summary == "Dentista" for e in events)


def test_get_calendar_status_text(monkeypatch):
    monkeypatch.delenv("CALENDAR_ICS_URL", raising=False)
    monkeypatch.delenv("CALENDAR_SYNC_ENABLED", raising=False)
    assert "DISATTIVATA" in get_calendar_status_text()

    monkeypatch.setenv("CALENDAR_ICS_URL", "https://example.com/feed.ics")
    monkeypatch.setenv("CALENDAR_BUFFER_MINUTES", "60")
    monkeypatch.setenv("CALENDAR_CONFLICT_MODE", "skip")

    status = get_calendar_status_text()
    assert "ATTIVA (1 feed" in status
    assert "buffer ±60m" in status
    assert "scarta slot" in status


def test_flow_calendar_conflict_blocks_autobook_and_warns_in_manual(monkeypatch, tmp_path):
    """When calendar has a conflict, Fast-Track Auto-Booking is safety-blocked and falls back to manual approval with an alert."""
    test_ics = tmp_path / "feed.ics"
    test_ics.write_text(SAMPLE_ICS, encoding="utf-8")

    monkeypatch.setenv("CALENDAR_ICS_URL", str(test_ics))
    monkeypatch.setenv("CALENDAR_SYNC_ENABLED", "true")
    monkeypatch.setenv("AUTO_BOOK", "true")
    monkeypatch.setenv("AUTO_BOOK_MIN_DAYS", "5")
    monkeypatch.setenv("CALENDAR_CONFLICT_MODE", "warn")

    from flows.new_booking import NewBookingFlow
    from services.matcher import Slot

    mon = {
        "id": "mon_cal_conflict_test",
        "type": "new",
        "ricetta": "Visita Oculistica",
        "criteri": {"province": ["MI"]},
    }
    browser = MagicMock()
    queue = MagicMock()
    queue.wait_decision.return_value = {"decision": False, "cancelled": True, "reason": "timeout"}
    bot = MagicMock()
    store = MagicMock()

    flow = NewBookingFlow(mon, browser, queue, bot, store)

    # 18 Nov 2026 10:15 overlaps with 'Riunione Team Progetto' (10:00 - 11:30)
    slot_conflict = Slot(
        datetime=datetime(2026, 11, 18, 10, 15),
        extra={"provincia": "MI", "azienda": "Niguarda"},
    )

    now = datetime(2026, 11, 2, 10, 0)
    with patch("services.deadline.get_now", return_value=now), \
         patch.object(flow, "execute") as mock_exec:

        res = flow._gestisci_slot_trovato(slot_conflict)

        # Autobook must NOT have executed
        mock_exec.assert_not_called()

        # Approval buttons must have been dispatched with warning
        assert bot.notify_buttons.called
        sent_msg = bot.notify_buttons.call_args[0][0]
        assert "Auto-Booking escluso per sicurezza" in sent_msg
        assert "conflitto su calendario" in sent_msg
        assert "Attenzione Conflitto Calendario" in sent_msg
        assert "Riunione Team Progetto" in sent_msg


def test_flow_calendar_conflict_skip_mode_drops_slot(monkeypatch, tmp_path):
    """When CALENDAR_CONFLICT_MODE=skip, conflicting slots are skipped completely without prompt."""
    test_ics = tmp_path / "feed.ics"
    test_ics.write_text(SAMPLE_ICS, encoding="utf-8")

    monkeypatch.setenv("CALENDAR_ICS_URL", str(test_ics))
    monkeypatch.setenv("CALENDAR_SYNC_ENABLED", "true")
    monkeypatch.setenv("CALENDAR_CONFLICT_MODE", "skip")

    from flows.new_booking import NewBookingFlow
    from services.matcher import Slot

    mon = {
        "id": "mon_cal_skip_test",
        "type": "new",
        "ricetta": "Visita Oculistica",
        "criteri": {"province": ["MI"]},
    }
    browser = MagicMock()
    queue = MagicMock()
    bot = MagicMock()
    store = MagicMock()

    flow = NewBookingFlow(mon, browser, queue, bot, store)

    slot_conflict = Slot(
        datetime=datetime(2026, 11, 18, 10, 15),
        extra={"provincia": "MI", "azienda": "Niguarda"},
    )

    with patch.object(flow, "execute") as mock_exec:
        res = flow._gestisci_slot_trovato(slot_conflict)
        assert res == "skip_conflict"
        mock_exec.assert_not_called()
        bot.notify_buttons.assert_not_called()


def test_flow_calendar_no_conflict_allows_autobook(monkeypatch, tmp_path):
    """When there is no calendar conflict, Fast-Track Auto-Booking proceeds normally."""
    test_ics = tmp_path / "feed.ics"
    test_ics.write_text(SAMPLE_ICS, encoding="utf-8")

    monkeypatch.setenv("CALENDAR_ICS_URL", str(test_ics))
    monkeypatch.setenv("CALENDAR_SYNC_ENABLED", "true")
    monkeypatch.setenv("AUTO_BOOK", "true")
    monkeypatch.setenv("AUTO_BOOK_MIN_DAYS", "5")

    from flows.new_booking import NewBookingFlow
    from services.matcher import Slot

    mon = {
        "id": "mon_cal_ok_test",
        "type": "new",
        "ricetta": "Visita Oculistica",
        "criteri": {"province": ["MI"]},
    }
    browser = MagicMock()
    queue = MagicMock()
    bot = MagicMock()
    store = MagicMock()

    flow = NewBookingFlow(mon, browser, queue, bot, store)

    # Slot at 12:30 on 22/11 (Dentista is 14:00-15:00, so outside 12:30+30m+45m = 13:45)
    # With 45m buffer: slot 12:30-13:00 -> window 11:45 to 13:45. Dentista starts at 14:00 -> No conflict!
    slot_free = Slot(
        datetime=datetime(2026, 11, 22, 12, 30),
        extra={"provincia": "MI", "azienda": "Niguarda"},
    )

    now = datetime(2026, 11, 2, 10, 0)
    with patch("services.deadline.get_now", return_value=now), \
         patch.object(flow, "execute") as mock_exec, \
         patch.object(flow, "_chiedi_post_prenotazione"):

        res = flow._gestisci_slot_trovato(slot_free)
        assert res == "ok"
        mock_exec.assert_called_once_with(slot_free)

