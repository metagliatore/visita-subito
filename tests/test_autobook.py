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
