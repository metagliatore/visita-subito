"""Tests for RescheduleFlow calendar .ics generation and previous appointment cancellation."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from flows.reschedule import RescheduleFlow
from services.appuntamento import AppuntamentoInfo
from services.matcher import Slot


@pytest.fixture
def base_reschedule_flow():
    mon = {
        "id": "mon_reschedule_test",
        "type": "reschedule",
        "ricetta": "Visita Cardiologica",
        "data_attuale": "28/12/2026 11:00",
        "criteri": {},
    }
    browser = MagicMock()
    queue = MagicMock()
    bot = MagicMock()
    store = MagicMock()
    store.get_prenotazione.return_value = None
    return RescheduleFlow(mon, browser, queue, bot, store)


def test_reschedule_invia_riepilogo_generates_dual_vevent_ics(base_reschedule_flow, tmp_path):
    """Reschedule summary sends .ics with STATUS:CANCELLED (SEQUENCE:1) for old date and STATUS:CONFIRMED for new date."""
    flow = base_reschedule_flow
    slot = Slot(datetime=datetime(2026, 11, 10, 9, 30))

    info = AppuntamentoInfo(
        data_ora="10/11/2026 - 09:30",
        prestazione="Visita Cardiologica",
        azienda="Ospedale Niguarda",
        presentarsi_in="Padiglione 2",
        indirizzo="Piazza Ospedale 1",
        codice="NIG-998877",
    )

    with patch("services.appuntamento.parse_conferma", return_value=info), \
         patch("services.appuntamento.parse_successo", return_value="NIG-998877"):

        flow._invia_riepilogo_dopo_modifica(slot, info=info)

    # 1. Bot notifications
    flow.bot.notify.assert_called_once()
    notify_text = flow.bot.notify.call_args[0][0]
    assert "10/11/2026 - 09:30" in notify_text
    assert "NIG-998877" in notify_text

    # 2. File sent via bot.send_file
    flow.bot.send_file.assert_called_once()
    sent_path_str, sent_caption = flow.bot.send_file.call_args[0][0], flow.bot.send_file.call_args[1]["caption"]
    assert "Include richiesta cancellazione precedente appuntamento del 28/12/2026 11:00" in sent_caption

    # 3. Read generated .ics content
    ics_file = Path(sent_path_str)
    assert ics_file.exists()
    content = ics_file.read_text(encoding="utf-8")

    # Verify both VEVENT blocks
    assert content.count("BEGIN:VEVENT") == 2
    assert content.count("END:VEVENT") == 2

    # Verify Old Event Cancellation details
    old_timestamp = int(datetime(2026, 12, 28, 11, 0).timestamp())
    assert f"UID:prenotazione-{old_timestamp}@fascicolosanitario.lombardia.it" in content
    assert "STATUS:CANCELLED" in content
    assert "SEQUENCE:1" in content
    assert "DTSTART:20261228T110000" in content
    assert "SUMMARY:Visita: Visita Cardiologica [SPOSTATA / ANNULLATA]" in content

    # Verify New Event Confirmation details
    new_timestamp = int(datetime(2026, 11, 10, 9, 30).timestamp())
    assert f"UID:prenotazione-{new_timestamp}@fascicolosanitario.lombardia.it" in content
    assert "STATUS:CONFIRMED" in content
    assert "SEQUENCE:0" in content
    assert "DTSTART:20261110T093000" in content
    assert "SUMMARY:Visita: Visita Cardiologica" in content

    # 4. Verify recorded in store
    flow.store.record_prenotazione.assert_called_once_with(
        "mon_reschedule_test",
        "NIG-998877",
        f"{slot.date_str} {slot.time_str}",
        info.__dict__,
    )


def test_reschedule_fallback_data_attuale_from_criteri():
    """If data_attuale is missing from monitor dict, fallback to criteri dict."""
    mon = {
        "id": "mon_crit_test",
        "type": "reschedule",
        "ricetta": "Visita Oculistica",
        "criteri": {"data_attuale": "15/12/2026 14:00"},
    }
    browser = MagicMock()
    queue = MagicMock()
    bot = MagicMock()
    store = MagicMock()
    flow = RescheduleFlow(mon, browser, queue, bot, store)

    slot = Slot(datetime=datetime(2026, 11, 20, 10, 0))
    info = AppuntamentoInfo(data_ora="20/11/2026 - 10:00", prestazione="Visita Oculistica")

    flow._invia_riepilogo_dopo_modifica(slot, info=info)

    flow.bot.send_file.assert_called_once()
    sent_path_str, sent_caption = flow.bot.send_file.call_args[0][0], flow.bot.send_file.call_args[1]["caption"]
    assert "Include richiesta cancellazione precedente appuntamento del 15/12/2026 14:00" in sent_caption

    content = Path(sent_path_str).read_text(encoding="utf-8")
    assert content.count("BEGIN:VEVENT") == 2
    assert "DTSTART:20261215T140000" in content
    assert "STATUS:CANCELLED" in content


def test_reschedule_fallback_data_attuale_from_store():
    """If data_attuale is neither in monitor nor criteri, fallback to store.get_prenotazione()."""
    mon = {
        "id": "mon_store_test",
        "type": "reschedule",
        "ricetta": "RMN Cerebrale",
        "criteri": {},
    }
    browser = MagicMock()
    queue = MagicMock()
    bot = MagicMock()
    store = MagicMock()
    store.get_prenotazione.return_value = {"data_ora": "22/12/2026 08:30", "codice": "OLD-COD-1"}
    flow = RescheduleFlow(mon, browser, queue, bot, store)

    slot = Slot(datetime=datetime(2026, 11, 25, 9, 0))
    info = AppuntamentoInfo(data_ora="25/11/2026 - 09:00", prestazione="RMN Cerebrale")

    flow._invia_riepilogo_dopo_modifica(slot, info=info)

    flow.bot.send_file.assert_called_once()
    sent_caption = flow.bot.send_file.call_args[1]["caption"]
    assert "Include richiesta cancellazione precedente appuntamento del 22/12/2026 08:30" in sent_caption

    content = Path(flow.bot.send_file.call_args[0][0]).read_text(encoding="utf-8")
    assert content.count("BEGIN:VEVENT") == 2
    assert "DTSTART:20261222T083000" in content
    assert "STATUS:CANCELLED" in content


def test_reschedule_without_any_data_attuale_generates_single_event():
    """When no previous appointment date is available anywhere, only the new appointment is published."""
    mon = {
        "id": "mon_empty_test",
        "type": "reschedule",
        "ricetta": "Visita Fisiatrica",
        "criteri": {},
    }
    browser = MagicMock()
    queue = MagicMock()
    bot = MagicMock()
    store = MagicMock()
    store.get_prenotazione.return_value = None
    flow = RescheduleFlow(mon, browser, queue, bot, store)

    slot = Slot(datetime=datetime(2026, 11, 15, 11, 0))
    info = AppuntamentoInfo(data_ora="15/11/2026 - 11:00", prestazione="Visita Fisiatrica")

    flow._invia_riepilogo_dopo_modifica(slot, info=info)

    flow.bot.send_file.assert_called_once()
    sent_caption = flow.bot.send_file.call_args[1]["caption"]
    assert "Include richiesta cancellazione" not in sent_caption

    content = Path(flow.bot.send_file.call_args[0][0]).read_text(encoding="utf-8")
    assert content.count("BEGIN:VEVENT") == 1
    assert "STATUS:CONFIRMED" in content
    assert "STATUS:CANCELLED" not in content


def test_reschedule_resilient_to_send_file_failure():
    """If send_file raises an exception, the flow does not crash and continues to persist in store."""
    mon = {
        "id": "mon_resilient_test",
        "type": "reschedule",
        "ricetta": "Ecografia",
        "data_attuale": "10/12/2026 10:00",
        "criteri": {},
    }
    browser = MagicMock()
    queue = MagicMock()
    bot = MagicMock()
    bot.send_file.side_effect = RuntimeError("Network error sending file")
    store = MagicMock()
    flow = RescheduleFlow(mon, browser, queue, bot, store)

    slot = Slot(datetime=datetime(2026, 11, 5, 9, 0))
    info = AppuntamentoInfo(data_ora="05/11/2026 - 09:00", prestazione="Ecografia", codice="ECO-123")

    # Should not raise exception
    flow._invia_riepilogo_dopo_modifica(slot, info=info)

    # Store was still called to persist confirmed booking
    flow.store.record_prenotazione.assert_called_once()

