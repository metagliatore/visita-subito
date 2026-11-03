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
    fetch_feed_content,
    get_calendar_buffer_minutes,
    get_calendar_cache_ttl,
    get_calendar_conflict_mode,
    get_calendar_events,
    get_calendar_status_text,
    get_calendar_urls,
    is_calendar_ignore_all_day,
    is_calendar_sync_enabled,
    parse_ics_content,
    parse_ics_datetime,
    parse_ics_duration,
    parse_ics_property,
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


def test_calendar_event_properties():
    e1 = CalendarEvent(
        uid="1",
        summary="Timed",
        dt_start=datetime(2026, 11, 20, 10, 15),
        dt_end=datetime(2026, 11, 20, 11, 45),
        is_all_day=False,
    )
    assert e1.start_str == "10:15"
    assert e1.end_str == "11:45"
    assert e1.date_str == "20/11/2026"

    e2 = CalendarEvent(
        uid="2",
        summary="AllDay",
        dt_start=datetime(2026, 11, 25, 0, 0),
        dt_end=datetime(2026, 11, 26, 0, 0),
        is_all_day=True,
    )
    assert e2.start_str == "25/11/2026"
    assert e2.end_str == "26/11/2026"


def test_parse_ics_duration_extended():
    assert parse_ics_duration("PT15S") == timedelta(seconds=15)
    assert parse_ics_duration("P2D") == timedelta(days=2)
    assert parse_ics_duration("PT1H20M30S") == timedelta(hours=1, minutes=20, seconds=30)
    assert parse_ics_duration("") == timedelta(minutes=30)
    assert parse_ics_duration("NOT_A_DURATION") == timedelta(minutes=30)


def test_parse_ics_datetime_corner_cases():
    # Invalid UTC format (e.g. invalid month or characters)
    dt_utc_invalid, is_all_day_u = parse_ics_datetime("INVALID_DATE_Z")
    assert isinstance(dt_utc_invalid, datetime)

    # Unknown TZID falls back gracefully
    dt_tz_unknown, _ = parse_ics_datetime("20261118T100000", {"TZID": "NonExistent/Timezone"})
    assert dt_tz_unknown == datetime(2026, 11, 18, 10, 0, 0)

    # Date-only fallback when time portion is malformed
    dt_date_fallback, is_all_day_f = parse_ics_datetime("20261120Tinvalidtime")
    assert is_all_day_f is True
    assert dt_date_fallback == datetime(2026, 11, 20, 0, 0, 0)

    # Completely garbage string returns a fallback datetime.now()
    dt_garbage, _ = parse_ics_datetime("totally_invalid")
    assert isinstance(dt_garbage, datetime)

    # Invalid DATE parameter string
    dt_bad_date, _ = parse_ics_datetime("BAD_DATE", {"VALUE": "DATE"})
    assert isinstance(dt_bad_date, datetime)


def test_parse_ics_property():
    name, params, val = parse_ics_property("SUMMARY;LANGUAGE=it:Visita medica")
    assert name == "SUMMARY"
    assert params == {"LANGUAGE": "it"}
    assert val == "Visita medica"

    # Line without colon
    name_no_col, params_no_col, val_no_col = parse_ics_property("END:VEVENT")
    assert name_no_col == "END"
    name_bare, params_bare, val_bare = parse_ics_property("JUST_A_LINE")
    assert name_bare == "JUST_A_LINE"
    assert params_bare == {}
    assert val_bare == ""


def test_expand_recurring_events_daily_and_until():
    ref_now = datetime(2026, 11, 1, 8, 0)
    base_evt = CalendarEvent(
        uid="daily_gym",
        summary="Palestra",
        dt_start=datetime(2026, 11, 1, 9, 0),
        dt_end=datetime(2026, 11, 1, 10, 0),
    )
    # FREQ=DAILY with INTERVAL=2 and UNTIL=20261107T000000Z
    rrule = "FREQ=DAILY;INTERVAL=2;UNTIL=20261107T000000Z"
    events = expand_recurring_events(base_evt, rrule, horizon_days=30, ref_now=ref_now)
    # Days should be 1st, 3rd, 5th Nov
    dates = [e.dt_start.date() for e in events]
    assert datetime(2026, 11, 1).date() in dates
    assert datetime(2026, 11, 3).date() in dates
    assert datetime(2026, 11, 5).date() in dates
    # After UNTIL Nov 7th, no events should exist
    assert all(e.dt_start <= datetime(2026, 11, 7, 23, 59) for e in events)


def test_parse_ics_content_corner_cases():
    ics_text = """BEGIN:VCALENDAR
VERSION:2.0

# Blank lines and malformed entries
BEGIN:VEVENT
UID:no_dtstart
SUMMARY:Manca dtstart
END:VEVENT

BEGIN:VEVENT
UID:event_no_end_timed
DTSTART:20261118T100000
SUMMARY:Senza dtend ne duration (orario)
END:VEVENT

BEGIN:VEVENT
UID:event_no_end_allday
DTSTART;VALUE=DATE:20261119
SUMMARY:Senza dtend ne duration (giornata intera)
END:VEVENT

BEGIN:VEVENT
UID:event_inverted_dates
DTSTART:20261118T150000
DTEND:20261118T140000
SUMMARY:Date invertite (end prima di start)
END:VEVENT

BEGIN:VEVENT
DTSTART:20261118T180000
DTEND:20261118T190000
END:VEVENT

BEGIN:VEVENT
UID:recurring_weekly
DTSTART:20261102T090000
DTEND:20261102T093000
RRULE:FREQ=WEEKLY;BYDAY=TU,TH
SUMMARY:Corso settimanale
END:VEVENT
END:VCALENDAR
"""
    now = datetime(2026, 11, 1, 0, 0)
    events = parse_ics_content(ics_text, ref_now=now)

    # 1. no_dtstart is omitted
    assert not any(e.uid == "no_dtstart" for e in events)

    # 2. event_no_end_timed defaults to +60m
    no_end_timed = next(e for e in events if e.uid == "event_no_end_timed")
    assert no_end_timed.dt_end == datetime(2026, 11, 18, 11, 0, 0)

    # 3. event_no_end_allday defaults to +1 day
    no_end_allday = next(e for e in events if e.uid == "event_no_end_allday")
    assert no_end_allday.dt_end == datetime(2026, 11, 20, 0, 0, 0)

    # 4. inverted dates auto-corrected to dt_start + 30m
    inverted = next(e for e in events if e.uid == "event_inverted_dates")
    assert inverted.dt_end == datetime(2026, 11, 18, 15, 30, 0)

    # 5. missing summary defaults to 'Impegno' and uid is auto-generated
    no_sum = next(e for e in events if e.dt_start == datetime(2026, 11, 18, 18, 0))
    assert no_sum.summary == "Impegno"
    assert no_sum.uid.startswith("evt_")

    # 6. recurring_weekly expanded
    recurring = [e for e in events if e.summary == "Corso settimanale"]
    assert len(recurring) > 1


def test_fetch_feed_content_webcal_and_file_url(tmp_path):
    ics_file = tmp_path / "feed.ics"
    ics_file.write_text("BEGIN:VCALENDAR\nEND:VCALENDAR", encoding="utf-8")

    # file:// scheme
    content_file = fetch_feed_content(f"file://{ics_file}")
    assert "BEGIN:VCALENDAR" in content_file

    # webcal:// rewritten to https:// and fetched via httpx mock
    with patch("httpx.Client.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.text = "BEGIN:VCALENDAR\nSUMMARY:Webcal\nEND:VCALENDAR"
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        res = fetch_feed_content("webcal://p12-caldav.icloud.com/published/12345.ics")
        assert "SUMMARY:Webcal" in res
        mock_get.assert_called_once()
        assert mock_get.call_args[0][0].startswith("https://")


def test_fetch_feed_content_http_errors():
    with patch("httpx.Client.get") as mock_get:
        import httpx
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError("404 Not Found", request=MagicMock(), response=MagicMock())
        mock_get.return_value = mock_resp

        with pytest.raises(httpx.HTTPStatusError):
            fetch_feed_content("https://example.com/notfound.ics")


def test_get_calendar_urls_delimiters(monkeypatch):
    monkeypatch.delenv("CALENDAR_ICS_URL", raising=False)
    # Empty
    assert get_calendar_urls({}) == []

    # Delimiters: comma, semicolon, newline
    raw = "https://cal1.ics, https://cal2.ics;\nhttps://cal3.ics"
    urls = get_calendar_urls({"calendar_ics_url": raw})
    assert urls == ["https://cal1.ics", "https://cal2.ics", "https://cal3.ics"]


def test_is_calendar_sync_enabled_matrix(monkeypatch):
    monkeypatch.delenv("CALENDAR_SYNC_ENABLED", raising=False)
    monkeypatch.delenv("CALENDAR_ICS_URL", raising=False)

    # 1. criteri check_calendar is False
    assert is_calendar_sync_enabled({"check_calendar": False, "calendar_ics_url": "https://cal.ics"}) is False

    # 2. CALENDAR_SYNC_ENABLED is 0, false, no
    for val in ("0", "false", "no", "False", "NO"):
        monkeypatch.setenv("CALENDAR_SYNC_ENABLED", val)
        assert is_calendar_sync_enabled({"calendar_ics_url": "https://cal.ics"}) is False

    # 3. CALENDAR_SYNC_ENABLED is 1, true, yes
    for val in ("1", "true", "yes", "True", "YES"):
        monkeypatch.setenv("CALENDAR_SYNC_ENABLED", val)
        assert is_calendar_sync_enabled({}) is True

    # 4. Implicitly enabled when calendar_ics_url exists
    monkeypatch.delenv("CALENDAR_SYNC_ENABLED", raising=False)
    assert is_calendar_sync_enabled({"calendar_ics_url": "https://cal.ics"}) is True
    assert is_calendar_sync_enabled({}) is False


def test_get_calendar_buffer_and_mode_and_ttl_fallbacks(monkeypatch):
    # Buffer from criteri
    assert get_calendar_buffer_minutes({"calendar_buffer_minutes": 20}) == 20
    assert get_calendar_buffer_minutes({"calendar_buffer_minutes": -10}) == 0
    assert get_calendar_buffer_minutes({"calendar_buffer_minutes": "invalid"}) == 45

    # Buffer from env
    monkeypatch.setenv("CALENDAR_BUFFER_MINUTES", "30")
    assert get_calendar_buffer_minutes({}) == 30
    monkeypatch.setenv("CALENDAR_BUFFER_MINUTES", "bad")
    assert get_calendar_buffer_minutes({}) == 45

    # Mode from criteri and env
    assert get_calendar_conflict_mode({"calendar_conflict_mode": "skip"}) == "skip"
    assert get_calendar_conflict_mode({"calendar_conflict_mode": "SKIP"}) == "skip"
    assert get_calendar_conflict_mode({"calendar_conflict_mode": "other"}) == "warn"
    monkeypatch.setenv("CALENDAR_CONFLICT_MODE", "skip")
    assert get_calendar_conflict_mode({}) == "skip"

    # Ignore all day
    assert is_calendar_ignore_all_day({"calendar_ignore_all_day": False}) is False
    assert is_calendar_ignore_all_day({"calendar_ignore_all_day": True}) is True
    monkeypatch.setenv("CALENDAR_IGNORE_ALL_DAY", "false")
    assert is_calendar_ignore_all_day({}) is False
    monkeypatch.setenv("CALENDAR_IGNORE_ALL_DAY", "true")
    assert is_calendar_ignore_all_day({}) is True

    # TTL
    monkeypatch.setenv("CALENDAR_CACHE_TTL", "1200")
    assert get_calendar_cache_ttl() == 1200
    monkeypatch.setenv("CALENDAR_CACHE_TTL", "10")  # min clamped to 60
    assert get_calendar_cache_ttl() == 60
    monkeypatch.setenv("CALENDAR_CACHE_TTL", "invalid")
    assert get_calendar_cache_ttl() == 600


def test_get_calendar_events_multi_feed_cache_and_resilience(tmp_path):
    clear_calendar_cache()

    # Empty urls
    assert get_calendar_events(urls=[]) == []

    feed1 = tmp_path / "feed1.ics"
    feed1.write_text(SAMPLE_ICS, encoding="utf-8")

    # 1. Fetching valid feed caches results
    events = get_calendar_events(urls=[str(feed1)], force_refresh=True)
    assert len(events) >= 5

    # Second call returns from cache without disk read
    with patch("services.calendar_sync.fetch_feed_content") as mock_fetch:
        cached_events = get_calendar_events(urls=[str(feed1)], force_refresh=False)
        assert len(cached_events) == len(events)
        mock_fetch.assert_not_called()

    # 2. Multi-feed with partial failure: feed1 succeeds, feed2 raises error
    bad_url = "https://invalid.example.com/bad.ics"
    with patch("services.calendar_sync.fetch_feed_content") as mock_fetch:
        import httpx

        def fake_fetch(url, **kwargs):
            if url == str(feed1):
                return SAMPLE_ICS
            raise httpx.ConnectError("Network down")

        mock_fetch.side_effect = fake_fetch
        combined_events = get_calendar_events(urls=[str(feed1), bad_url], force_refresh=True)
        # Should still retain feed1's events!
        assert len(combined_events) >= 5

    # 3. Total failure fallback: if cache already had events and new fetch completely fails
    with patch("services.calendar_sync.fetch_feed_content", side_effect=httpx.ConnectError("All down")):
        stale_events = get_calendar_events(urls=[str(feed1), bad_url], force_refresh=True)
        assert len(stale_events) >= 5


def test_check_calendar_conflict_exact_boundaries():
    # Event: 10:00 to 11:00
    ev = CalendarEvent(
        uid="evt_10_11",
        summary="Riunione Ufficio",
        dt_start=datetime(2026, 11, 18, 10, 0),
        dt_end=datetime(2026, 11, 18, 11, 0),
        status="CONFIRMED",
    )
    events = [ev]

    # Slot A: starts exactly at 11:00 (back-to-back), buffer=0 -> NO CONFLICT
    slot_a = datetime(2026, 11, 18, 11, 0)
    has_conf_a, _, _ = check_calendar_conflict(
        slot_a,
        durata_minuti=30,
        buffer_minuti=0,
        criteri={"calendar_ics_url": "dummy"},
        events=events,
    )
    assert has_conf_a is False

    # Slot A with buffer=5m -> window starts at 10:55, which overlaps!
    has_conf_a_buf, _, msg_a = check_calendar_conflict(
        slot_a,
        durata_minuti=30,
        buffer_minuti=5,
        criteri={"calendar_ics_url": "dummy"},
        events=events,
    )
    assert has_conf_a_buf is True
    assert "incluso buffer ±5m" in msg_a

    # Slot B: ends exactly at 10:00 (09:30 to 10:00), buffer=0 -> NO CONFLICT
    slot_b = datetime(2026, 11, 18, 9, 30)
    has_conf_b, _, _ = check_calendar_conflict(
        slot_b,
        durata_minuti=30,
        buffer_minuti=0,
        criteri={"calendar_ics_url": "dummy"},
        events=events,
    )
    assert has_conf_b is False

    # Empty events list -> no conflict
    has_conf_empty, _, _ = check_calendar_conflict(
        slot_a,
        durata_minuti=30,
        buffer_minuti=0,
        criteri={"calendar_ics_url": "dummy"},
        events=[],
    )
    assert has_conf_empty is False

    # Cancelled event in events list -> skipped
    ev_cancelled = CalendarEvent(
        uid="evt_cancelled",
        summary="Cancellato",
        dt_start=datetime(2026, 11, 18, 10, 0),
        dt_end=datetime(2026, 11, 18, 11, 0),
        status="CANCELLED",
    )
    has_conf_canc, _, _ = check_calendar_conflict(
        datetime(2026, 11, 18, 10, 15),
        durata_minuti=30,
        buffer_minuti=0,
        criteri={"calendar_ics_url": "dummy"},
        events=[ev_cancelled],
    )
    assert has_conf_canc is False


def test_reschedule_flow_calendar_conflict_integration(monkeypatch, tmp_path):
    """RescheduleFlow displays both previous appointment replacement info and calendar conflict banner."""
    test_ics = tmp_path / "feed.ics"
    test_ics.write_text(SAMPLE_ICS, encoding="utf-8")

    monkeypatch.setenv("CALENDAR_ICS_URL", str(test_ics))
    monkeypatch.setenv("CALENDAR_SYNC_ENABLED", "true")
    monkeypatch.setenv("AUTO_BOOK", "true")
    monkeypatch.setenv("AUTO_BOOK_MIN_DAYS", "5")
    monkeypatch.setenv("CALENDAR_CONFLICT_MODE", "warn")

    from flows.reschedule import RescheduleFlow
    from services.matcher import Slot

    mon = {
        "id": "mon_reschedule_cal_conflict",
        "type": "reschedule",
        "ricetta": "Visita Dermatologica",
        "criteri": {
            "province": ["MI"],
            "data_attuale": "28/11/2026 15:30",
        },
    }
    browser = MagicMock()
    queue = MagicMock()
    queue.wait_decision.return_value = {"decision": False, "cancelled": True, "reason": "timeout"}
    bot = MagicMock()
    store = MagicMock()

    flow = RescheduleFlow(mon, browser, queue, bot, store)

    # 18 Nov 2026 10:15 overlaps with 'Riunione Team Progetto' (10:00 - 11:30)
    slot_conflict = Slot(
        datetime=datetime(2026, 11, 18, 10, 15),
        extra={"provincia": "MI", "azienda": "San Raffaele"},
    )

    now = datetime(2026, 11, 2, 10, 0)
    with patch("services.deadline.get_now", return_value=now), \
         patch.object(flow, "execute") as mock_exec:

        res = flow._gestisci_slot_trovato(slot_conflict)

        # Autobook safety blocked
        mock_exec.assert_not_called()

        assert bot.notify_buttons.called
        sent_msg = bot.notify_buttons.call_args[0][0]
        # Previous appointment replacement info present
        assert "Appuntamento attuale da sostituire: 28/11/2026 15:30" in sent_msg
        assert "Nuova data proposta" in sent_msg
        # Calendar conflict warning present
        assert "Attenzione Conflitto Calendario" in sent_msg
        assert "Riunione Team Progetto" in sent_msg
        assert "Auto-Booking escluso per sicurezza" in sent_msg


def test_get_calendar_status_text_multiple_feeds(monkeypatch):
    monkeypatch.setenv("CALENDAR_ICS_URL", "https://feed1.ics, https://feed2.ics")
    monkeypatch.setenv("CALENDAR_BUFFER_MINUTES", "30")
    monkeypatch.setenv("CALENDAR_CONFLICT_MODE", "warn")

    status = get_calendar_status_text()
    assert "ATTIVA (2 feed" in status
    assert "buffer ±30m" in status
    assert "avviso in notifica" in status


