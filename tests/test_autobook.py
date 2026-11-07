"""Unit and integration tests for Fast-Track Auto-Booking ('Sniper mode')."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
import pytest

from services.deadline import (
    check_autobook_eligibility,
    get_autobook_min_days,
    is_autobook_enabled,
)
from services.matcher import Slot


class DummySlot:
    """Mock slot for test cases."""
    def __init__(self, dt: datetime, extra: dict | None = None):
        self.datetime = dt
        self.date_str = dt.strftime("%d/%m/%Y")
        self.time_str = dt.strftime("%H:%M")
        self.key = f"{dt.strftime('%Y%m%dT%H%M')}|dummy"
        self.extra = extra or {"provincia": "MI", "azienda": "Ospedale Niguarda", "comune": "Milano"}

    def __str__(self):
        return f"{self.date_str} {self.time_str}"


def test_is_autobook_enabled_defaults_and_overrides(monkeypatch):
    """Test priority and defaults for auto-booking enablement."""
    monkeypatch.delenv("AUTO_BOOK", raising=False)

    # 1. Default without env or criteria is False
    assert is_autobook_enabled() is False

    # 2. Enabled via environment variable
    monkeypatch.setenv("AUTO_BOOK", "true")
    assert is_autobook_enabled() is True

    monkeypatch.setenv("AUTO_BOOK", "1")
    assert is_autobook_enabled() is True

    monkeypatch.setenv("AUTO_BOOK", "si")
    assert is_autobook_enabled() is True

    monkeypatch.setenv("AUTO_BOOK", "false")
    assert is_autobook_enabled() is False

    # 3. Monitor criteria overrides environment variable
    monkeypatch.setenv("AUTO_BOOK", "false")
    assert is_autobook_enabled(criteri={"auto_book": True}) is True
    assert is_autobook_enabled(criteri={"auto_book": "yes"}) is True

    monkeypatch.setenv("AUTO_BOOK", "true")
    assert is_autobook_enabled(criteri={"auto_book": False}) is False
    assert is_autobook_enabled(criteri={"auto_book": "0"}) is False


def test_get_autobook_min_days_defaults_and_overrides(monkeypatch):
    """Test retrieving the minimum days notice threshold."""
    monkeypatch.delenv("AUTO_BOOK_MIN_DAYS", raising=False)

    # Default is 5
    assert get_autobook_min_days() == 5

    # Environment override
    monkeypatch.setenv("AUTO_BOOK_MIN_DAYS", "7")
    assert get_autobook_min_days() == 7

    # Invalid env falls back to 5
    monkeypatch.setenv("AUTO_BOOK_MIN_DAYS", "invalid_number")
    assert get_autobook_min_days() == 5

    # Criteria overrides env
    monkeypatch.setenv("AUTO_BOOK_MIN_DAYS", "5")
    assert get_autobook_min_days(criteri={"auto_book_min_days": 10}) == 10
    assert get_autobook_min_days(criteri={"auto_book_min_days": "3"}) == 3


def test_check_autobook_eligibility():
    """Test eligibility calculation based on appointment distance."""
    base_now = datetime(2026, 11, 2, 10, 0)  # Monday

    # 1. Slot 8 days ahead (>= 5) -> eligible
    slot_future = DummySlot(base_now + timedelta(days=8))
    eligible, delta, reason = check_autobook_eligibility(slot_future, min_days=5, now=base_now)
    assert eligible is True
    assert delta == 8
    assert "8 giorni" in reason

    # 2. Slot exactly 5 days ahead (boundary) -> eligible
    slot_boundary = DummySlot(base_now + timedelta(days=5))
    eligible, delta, reason = check_autobook_eligibility(slot_boundary, min_days=5, now=base_now)
    assert eligible is True
    assert delta == 5

    # 3. Slot 2 days ahead (< 5) -> NOT eligible
    slot_soon = DummySlot(base_now + timedelta(days=2))
    eligible, delta, reason = check_autobook_eligibility(slot_soon, min_days=5, now=base_now)
    assert eligible is False
    assert delta == 2
    assert "dista solo 2 giorni" in reason
    assert "soglia minima configurata: 5 giorni" in reason

    # 4. Slot in the past -> NOT eligible
    slot_past = DummySlot(base_now - timedelta(days=1))
    eligible, delta, reason = check_autobook_eligibility(slot_past, min_days=5, now=base_now)
    assert eligible is False
    assert delta < 0
    assert "antecedente" in reason

    # 5. String date input supported
    str_dt = (base_now + timedelta(days=6)).strftime("%d/%m/%Y %H:%M")
    eligible, delta, _ = check_autobook_eligibility(str_dt, min_days=5, now=base_now)
    assert eligible is True
    assert delta == 6

    # 6. Invalid inputs
    assert check_autobook_eligibility(None)[0] is False
    assert check_autobook_eligibility("not-a-date")[0] is False


def test_flow_fast_track_autobook_execution_when_eligible(monkeypatch):
    """When AUTO_BOOK=true and slot >= min_days, books immediately without manual approval."""
    monkeypatch.setenv("AUTO_BOOK", "true")
    monkeypatch.setenv("AUTO_BOOK_MIN_DAYS", "5")

    from flows.new_booking import NewBookingFlow

    mon = {
        "id": "mon_autobook_test",
        "type": "new",
        "ricetta": "Prima Visita Oculistica",
        "criteri": {"province": ["MI"]},
    }
    browser = MagicMock()
    queue = MagicMock()
    bot = MagicMock()
    store = MagicMock()
    store.seen_slots.return_value = set()
    store.get_blacklist_keys.return_value = set()

    flow = NewBookingFlow(mon, browser, queue, bot, store)

    now = datetime(2026, 11, 2, 10, 0)
    slot_dt = now + timedelta(days=10)
    slot = Slot(
        datetime=slot_dt,
        extra={"provincia": "MI", "azienda": "ASST Niguarda", "sede": "Ospedale Niguarda", "comune": "Milano"}
    )

    with patch("services.deadline.get_now", return_value=now), \
         patch.object(flow, "execute") as mock_exec, \
         patch.object(flow, "_chiedi_post_prenotazione") as mock_postbook, \
         patch("services.sound.play_alert_sound") as mock_sound:

        result = flow._gestisci_slot_trovato(slot)

        assert result == "ok"
        # execute called immediately with the slot
        mock_exec.assert_called_once_with(slot)
        # queue was NOT asked to wait for manual decision
        queue.create.assert_not_called()
        queue.wait_decision.assert_not_called()
        # sound played
        assert mock_sound.call_count >= 1
        # store marked action
        store.mark_action.assert_called_once_with("mon_autobook_test", "done_pending_choice", str(slot))
        # postbook prompt called
        mock_postbook.assert_called_once_with(slot)

        # verify Telegram notification sent
        sent_messages = [call.args[0] for call in bot.notify.call_args_list]
        combined = " ".join(sent_messages)
        assert "[FAST-TRACK AUTO-BOOKING]" in combined
        assert "[PRENOTATO AUTOMATICAMENTE]" in combined
        assert "10 giorni" in combined


def test_flow_fast_track_fallback_to_manual_when_slot_is_too_soon(monkeypatch):
    """When AUTO_BOOK=true but slot < min_days, falls back to manual approval with safety note."""
    monkeypatch.setenv("AUTO_BOOK", "true")
    monkeypatch.setenv("AUTO_BOOK_MIN_DAYS", "5")

    from flows.new_booking import NewBookingFlow

    mon = {
        "id": "mon_fallback_test",
        "type": "new",
        "ricetta": "Prima Visita Dermatologica",
        "criteri": {"province": ["MI"]},
    }
    browser = MagicMock()
    queue = MagicMock()
    queue.create.return_value = "req_123"
    queue.wait_decision.return_value = {"decision": "approve", "cancelled": False}
    bot = MagicMock()
    store = MagicMock()
    store.seen_slots.return_value = set()
    store.get_blacklist_keys.return_value = set()

    flow = NewBookingFlow(mon, browser, queue, bot, store)

    now = datetime(2026, 11, 2, 10, 0)
    slot_dt = now + timedelta(days=2)  # 2 days away (< 5)
    slot = Slot(
        datetime=slot_dt,
        extra={"provincia": "MI", "azienda": "ASST Ovest Milanese", "comune": "Legnano"}
    )

    with patch("services.deadline.get_now", return_value=now), \
         patch.object(flow, "execute") as mock_exec, \
         patch.object(flow, "_chiedi_post_prenotazione") as mock_postbook:

        result = flow._gestisci_slot_trovato(slot)

        # Manual approval queue was used
        queue.create.assert_called_once()
        queue.wait_decision.assert_called_once_with("req_123")
        # Buttons sent to bot
        bot.notify_buttons.assert_called_once()
        sent_prompt = bot.notify_buttons.call_args[0][0]
        # Notice must contain warning about safety threshold
        assert "Auto-Booking escluso per sicurezza" in sent_prompt
        assert "dista solo 2 giorni" in sent_prompt
        assert "malus ticket" in sent_prompt

        # Since user approved, execute called
        mock_exec.assert_called_once_with(slot)
        mock_postbook.assert_called_once_with(slot)


def test_flow_fast_track_autobook_execution_failure_handled(monkeypatch):
    """When execute fails during auto-booking, handles error, notifies and resets to idle."""
    monkeypatch.setenv("AUTO_BOOK", "true")
    monkeypatch.setenv("AUTO_BOOK_MIN_DAYS", "5")

    from flows.new_booking import NewBookingFlow

    mon = {
        "id": "mon_fail_test",
        "type": "new",
        "ricetta": "RMN Cervicale",
        "criteri": {"province": ["MI"]},
    }
    browser = MagicMock()
    queue = MagicMock()
    bot = MagicMock()
    store = MagicMock()
    store.seen_slots.return_value = set()
    store.get_blacklist_keys.return_value = set()

    flow = NewBookingFlow(mon, browser, queue, bot, store)

    now = datetime(2026, 11, 2, 10, 0)
    slot = Slot(
        datetime=now + timedelta(days=12),
        extra={"provincia": "MI", "azienda": "San Raffaele", "comune": "Milano"}
    )

    with patch("services.deadline.get_now", return_value=now), \
         patch.object(flow, "execute", side_effect=RuntimeError("Slot già occupato da un altro utente")), \
         patch.object(flow, "_salva_debug_dump", return_value={"message": "Dump salvato"}):

        result = flow._gestisci_slot_trovato(slot)

        assert result == "errore"
        # Store resets to idle
        store.mark_action.assert_called_with("mon_fail_test", "idle", "fallita prenotazione automatica: Slot già occupato da un altro utente")
        # Bot notified with clear error message
        sent_messages = [call.args[0] for call in bot.notify.call_args_list]
        combined = " ".join(sent_messages)
        assert "Fast-Track Auto-Booking non riuscito" in combined
        assert "Slot già occupato da un altro utente" in combined


def test_status_text_shows_autobook_state(monkeypatch, tmp_path):
    """Scheduler status_text displays auto-book status accurately."""
    from services.scheduler import Controller
    from state.store import Store
    from core.config import Config

    store = Store(tmp_path / "store.json")
    bot = MagicMock()

    # 1. Disabled by default
    monkeypatch.delenv("AUTO_BOOK", raising=False)
    cfg1 = Config(settings={"scheduler": {"poll_interval_seconds": 300}})
    controller1 = Controller(cfg1, bot=bot)
    controller1.store = store
    txt_disabled = controller1.status_text()
    assert "Fast-Track Auto-Booking: disattivato (richiesta approvazione manuale)" in txt_disabled

    # 2. Enabled via env
    monkeypatch.setenv("AUTO_BOOK", "true")
    monkeypatch.setenv("AUTO_BOOK_MIN_DAYS", "6")
    cfg2 = Config(settings={"scheduler": {"poll_interval_seconds": 300}})
    controller2 = Controller(cfg2, bot=bot)
    controller2.store = store
    txt_enabled = controller2.status_text()
    assert "Fast-Track Auto-Booking: ATTIVO (soglia minima >= 6 giorni)" in txt_enabled


def test_flow_reschedule_fast_track_autobook_with_substitution(monkeypatch):
    """Reschedule flow autobook displays old appointment replacement text."""
    monkeypatch.setenv("AUTO_BOOK", "true")
    monkeypatch.setenv("AUTO_BOOK_MIN_DAYS", "5")

    from flows.reschedule import RescheduleFlow

    mon = {
        "id": "mon_reschedule_autobook",
        "type": "reschedule",
        "ricetta": "Visita Cardiologica",
        "data_attuale": "28/12/2026 11:00",
        "criteri": {"province": ["BG"]},
    }
    browser = MagicMock()
    queue = MagicMock()
    bot = MagicMock()
    store = MagicMock()
    store.seen_slots.return_value = set()
    store.get_blacklist_keys.return_value = set()

    flow = RescheduleFlow(mon, browser, queue, bot, store)

    now = datetime(2026, 11, 2, 10, 0)
    slot = Slot(
        datetime=now + timedelta(days=8),
        extra={"provincia": "BG", "azienda": "Papa Giovanni XXIII", "comune": "Bergamo"}
    )

    with patch("services.deadline.get_now", return_value=now), \
         patch.object(flow, "execute") as mock_exec, \
         patch.object(flow, "_chiedi_post_prenotazione") as mock_postbook:

        result = flow._gestisci_slot_trovato(slot)

        assert result == "ok"
        mock_exec.assert_called_once_with(slot)
        mock_postbook.assert_called_once_with(slot)

        sent_messages = [call.args[0] for call in bot.notify.call_args_list]
        combined = " ".join(sent_messages)
        assert "[PRENOTATO AUTOMATICAMENTE]" in combined
        assert "in sostituzione di quello del 28/12/2026 11:00" in combined
        assert "8 giorni" in combined


def test_flow_autobook_resilient_to_post_booking_notification_error(monkeypatch):
    """If Telegram notify fails AFTER successful execution, action remains recorded as done_pending_choice."""
    monkeypatch.setenv("AUTO_BOOK", "true")
    monkeypatch.setenv("AUTO_BOOK_MIN_DAYS", "5")

    from flows.new_booking import NewBookingFlow

    mon = {
        "id": "mon_notify_error_test",
        "type": "new",
        "ricetta": "Ecografia Tiroidea",
        "criteri": {"province": ["BS"]},
    }
    browser = MagicMock()
    queue = MagicMock()
    bot = MagicMock()
    # bot.notify fails on the second call (post-booking message)
    bot.notify.side_effect = ["Pre-ok", RuntimeError("Telegram connection timeout")]
    store = MagicMock()
    store.seen_slots.return_value = set()
    store.get_blacklist_keys.return_value = set()

    flow = NewBookingFlow(mon, browser, queue, bot, store)

    now = datetime(2026, 11, 2, 10, 0)
    slot = Slot(
        datetime=now + timedelta(days=9),
        extra={"provincia": "BS", "azienda": "Spedali Civili", "comune": "Brescia"}
    )

    with patch("services.deadline.get_now", return_value=now), \
         patch.object(flow, "execute") as mock_exec, \
         patch.object(flow, "_chiedi_post_prenotazione") as mock_postbook:

        result = flow._gestisci_slot_trovato(slot)

        # Still considered ok because portal booking succeeded
        assert result == "ok"
        mock_exec.assert_called_once_with(slot)
        # Store was marked done_pending_choice and NOT idle
        store.mark_action.assert_called_once_with("mon_notify_error_test", "done_pending_choice", str(slot))


def test_cerca_first_and_best_match_dispatch_autobook(monkeypatch):
    """Both first_match and best_match search strategies dispatch to _gestisci_slot_trovato."""
    monkeypatch.setenv("AUTO_BOOK", "true")
    monkeypatch.setenv("AUTO_BOOK_MIN_DAYS", "5")

    from flows.new_booking import NewBookingFlow

    mon = {
        "id": "mon_strat_test",
        "type": "new",
        "ricetta": "Visita Fisiatrica",
        "criteri": {"province": ["MI"]},
    }
    browser = MagicMock()
    queue = MagicMock()
    bot = MagicMock()
    store = MagicMock()
    store.seen_slots.return_value = set()
    store.get_blacklist_keys.return_value = set()

    flow = NewBookingFlow(mon, browser, queue, bot, store)

    now = datetime(2026, 11, 2, 10, 0)
    slot = Slot(
        datetime=now + timedelta(days=7),
        extra={"provincia": "MI", "azienda": "Gaetano Pini", "comune": "Milano"}
    )

    # 1. first_match dispatch
    flow.extract_slots_current_province = MagicMock(return_value=[slot])
    with patch.object(flow, "_gestisci_slot_trovato", return_value="autobook_ok") as mock_gest:
        res1 = flow._cerca_first_match()
        assert res1 == "autobook_ok"
        mock_gest.assert_called_once_with(slot)

    # 2. best_match dispatch
    slot2 = Slot(
        datetime=now + timedelta(days=8),
        extra={"provincia": "MI", "azienda": "Gaetano Pini 2", "comune": "Milano"}
    )
    flow.extract_slots = MagicMock(return_value=[slot2])
    with patch.object(flow, "_gestisci_slot_trovato", return_value="autobook_ok") as mock_gest2:
        res2 = flow._cerca_best_match()
        assert res2 == "autobook_ok"
        mock_gest2.assert_called_once_with(slot2)


def test_autobook_eligibility_edge_cases():
    """Verify boundary conditions and edge cases in eligibility checks."""
    now = datetime(2028, 2, 24, 10, 0)  # Leap year 2028

    # Leap day test (Feb 29 is +5 days from Feb 24)
    leap_slot = DummySlot(datetime(2028, 2, 29, 9, 0))
    eligible, delta, _ = check_autobook_eligibility(leap_slot, min_days=5, now=now)
    assert eligible is True
    assert delta == 5

    # Zero min_days (any future date is accepted)
    same_week_slot = DummySlot(now + timedelta(days=1))
    eligible0, delta0, _ = check_autobook_eligibility(same_week_slot, min_days=0, now=now)
    assert eligible0 is True
    assert delta0 == 1

    # Raw date string with Italian format
    eligible_str, delta_str, _ = check_autobook_eligibility("05/03/2028 14:30", min_days=5, now=now)
    assert eligible_str is True
    assert delta_str == 10

