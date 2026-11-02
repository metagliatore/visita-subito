from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from services.appuntamento import AppuntamentoInfo
from services.calendar import _fold, _fmt_dt, build_ics, build_cancellation_ics, write_ics


def test_fold():
    short_line = "SUMMARY:Visita breve"
    assert _fold(short_line, limit=74) == short_line

    long_line = "DESCRIPTION:" + "A" * 100
    folded = _fold(long_line, limit=50)
    assert "\r\n " in folded
    # Every line (except the first) starts with a space
    lines = folded.split("\r\n")
    assert len(lines) > 1
    assert lines[1].startswith(" ")


def test_fmt_dt():
    dt = datetime(2026, 10, 26, 14, 30, 0)
    assert _fmt_dt(dt) == "20261026T143000"


def test_build_ics():
    info = AppuntamentoInfo(
        data_ora="26/10/2026 - 11:15",
        prestazione="PRIMA VISITA OCULISTICA",
        azienda="ASST GRANDE OSPEDALE METROPOLITANO NIGUARDA",
        presentarsi_in="PADIGLIONE 1",
        indirizzo="PIAZZA OSPEDALE MAGGIORE, 3 - MILANO",
        note=["Portare tessera sanitaria", "Digiuno non necessario"],
    )
    ics_text = build_ics(info, uid="test-uid-123", durata_minuti=45)

    assert "BEGIN:VCALENDAR" in ics_text
    assert "VERSION:2.0" in ics_text
    assert "BEGIN:VEVENT" in ics_text
    assert "UID:test-uid-123" in ics_text
    assert "DTSTART:20261026T111500" in ics_text
    assert "DTEND:20261026T120000" in ics_text
    assert "SUMMARY:Visita: PRIMA VISITA OCULISTICA" in ics_text
    assert "NIGUARDA" in ics_text
    assert "PADIGLIONE 1" in ics_text
    # RFC 5545 compliance: VEVENT can have at most one DESCRIPTION property
    assert ics_text.count("DESCRIPTION:") == 1
    assert "Note:" in ics_text
    assert "END:VEVENT" in ics_text
    assert "END:VCALENDAR" in ics_text


def test_build_ics_with_vecchio_appuntamento_datetime():
    """When rescheduling, build_ics includes a VEVENT with STATUS:CANCELLED for the old appointment."""
    new_info = AppuntamentoInfo(
        data_ora="10/11/2026 - 09:30",
        prestazione="VISITA CARDIOLOGICA",
        azienda="POLICLINICO DI MILANO",
    )
    old_dt = datetime(2026, 12, 28, 11, 0)

    ics_text = build_ics(new_info, vecchio_appuntamento=old_dt, durata_minuti=30)

    # Both events must be present in the same VCALENDAR
    assert ics_text.count("BEGIN:VEVENT") == 2
    assert ics_text.count("END:VEVENT") == 2

    # Verify cancellation block
    old_uid = f"prenotazione-{int(old_dt.timestamp())}@fascicolosanitario.lombardia.it"
    assert f"UID:{old_uid}" in ics_text
    assert "DTSTART:20261228T110000" in ics_text
    assert "STATUS:CANCELLED" in ics_text
    assert "SEQUENCE:1" in ics_text
    assert "SUMMARY:Visita: VISITA CARDIOLOGICA [SPOSTATA / ANNULLATA]" in ics_text
    unfolded = ics_text.replace("\r\n ", "")
    assert "Appuntamento precedente annullato o anticipato/posticipato al 10/11/2026 alle 09:30" in unfolded

    # Verify confirmation block for the new slot
    new_uid = f"prenotazione-{int(new_info.datetime.timestamp())}@fascicolosanitario.lombardia.it"
    assert f"UID:{new_uid}" in ics_text
    assert "DTSTART:20261110T093000" in ics_text
    assert "STATUS:CONFIRMED" in ics_text
    assert "SEQUENCE:0" in ics_text
    assert "SUMMARY:Visita: VISITA CARDIOLOGICA" in ics_text


def test_build_ics_with_vecchio_appuntamento_string_and_custom_uid():
    """String representation of date_ora and custom old UID are correctly handled."""
    new_info = AppuntamentoInfo(
        data_ora="15/12/2026 - 15:00",
        prestazione="RISONANZA MAGNETICA",
    )
    custom_old_uid = "prenotazione-custom-old-uid@regionelombardia.it"

    ics_text = build_ics(
        new_info,
        vecchio_appuntamento="28/12/2026 11:00",
        vecchio_uid=custom_old_uid,
    )

    assert ics_text.count("BEGIN:VEVENT") == 2
    assert f"UID:{custom_old_uid}" in ics_text
    assert "DTSTART:20261228T110000" in ics_text
    assert "STATUS:CANCELLED" in ics_text
    assert "SEQUENCE:1" in ics_text


def test_build_ics_with_vecchio_appuntamento_info_and_dict():
    """Passing AppuntamentoInfo or dictionary as vecchio_appuntamento."""
    new_info = AppuntamentoInfo(data_ora="10/11/2026 10:00", prestazione="VISITA DERMATOLOGICA")
    old_info = AppuntamentoInfo(data_ora="30/11/2026 14:00", prestazione="VISITA DERMATOLOGICA")

    ics_from_info = build_ics(new_info, vecchio_appuntamento=old_info)
    assert ics_from_info.count("BEGIN:VEVENT") == 2
    assert "DTSTART:20261130T140000" in ics_from_info
    assert "STATUS:CANCELLED" in ics_from_info

    old_dict = {"data_ora": "05/12/2026 16:30"}
    ics_from_dict = build_ics(new_info, vecchio_appuntamento=old_dict)
    assert ics_from_dict.count("BEGIN:VEVENT") == 2
    assert "DTSTART:20261205T163000" in ics_from_dict


def test_build_ics_same_datetime_ignores_cancellation():
    """If old appointment datetime equals new appointment datetime, do not emit duplicate cancellation."""
    new_info = AppuntamentoInfo(data_ora="10/11/2026 10:00", prestazione="VISITA")
    same_dt = datetime(2026, 11, 10, 10, 0)

    ics_text = build_ics(new_info, vecchio_appuntamento=same_dt)
    assert ics_text.count("BEGIN:VEVENT") == 1
    assert "STATUS:CANCELLED" not in ics_text


def test_build_cancellation_ics_standalone():
    """Dedicated RFC 5545/5546 cancellation calendar text (METHOD:CANCEL)."""
    old_dt = datetime(2026, 12, 28, 11, 0)
    cancellation_text = build_cancellation_ics(
        old_dt_or_info=old_dt,
        prestazione="VISITA CARDIOLOGICA",
        nuova_data="10/11/2026 09:30",
    )

    assert "BEGIN:VCALENDAR" in cancellation_text
    assert "METHOD:CANCEL" in cancellation_text
    assert "BEGIN:VEVENT" in cancellation_text
    assert "STATUS:CANCELLED" in cancellation_text
    assert "SEQUENCE:1" in cancellation_text
    assert "SUMMARY:Visita: VISITA CARDIOLOGICA [ANNULLATA]" in cancellation_text
    unfolded = cancellation_text.replace("\r\n ", "")
    assert "10/11/2026 alle 09:30" in unfolded
    assert "END:VCALENDAR" in cancellation_text


def test_write_ics(tmp_path):
    info = AppuntamentoInfo(
        data_ora="15/11/2026 - 09:00",
        prestazione="ECOGRAFIA",
    )
    ics_path = write_ics(info, out_dir=tmp_path)
    assert ics_path.exists()
    assert ics_path.suffix == ".ics"
    content = ics_path.read_text(encoding="utf-8")
    assert "BEGIN:VCALENDAR" in content
    assert "ECOGRAFIA" in content


def test_write_ics_with_vecchio_appuntamento(tmp_path):
    """write_ics embeds cancellation VEVENT into saved file when vecchio_appuntamento is given."""
    info = AppuntamentoInfo(
        data_ora="10/11/2026 - 09:30",
        prestazione="VISITA CARDIOLOGICA",
    )
    ics_path = write_ics(
        info,
        out_dir=tmp_path,
        vecchio_appuntamento="28/12/2026 11:00",
    )
    assert ics_path.exists()
    content = ics_path.read_text(encoding="utf-8")
    assert content.count("BEGIN:VEVENT") == 2
    assert "STATUS:CANCELLED" in content
    assert "STATUS:CONFIRMED" in content


def test_reschedule_flow_invia_riepilogo_dopo_modifica_propagates_cancellation(tmp_path):
    """RescheduleFlow._invia_riepilogo_dopo_modifica calls write_ics with data_attuale and adds caption notice."""
    from flows.reschedule import RescheduleFlow
    from services.matcher import Slot

    mon = {
        "id": "mon_reschedule_ics_test",
        "type": "reschedule",
        "ricetta": "Visita Oculistica",
        "data_attuale": "28/12/2026 11:00",
        "criteri": {},
    }
    browser = MagicMock()
    queue = MagicMock()
    bot = MagicMock()
    store = MagicMock()

    flow = RescheduleFlow(mon, browser, queue, bot, store)
    slot = Slot(datetime=datetime(2026, 11, 10, 9, 30))

    info = AppuntamentoInfo(
        data_ora="10/11/2026 - 09:30",
        prestazione="Visita Oculistica",
        azienda="Ospedale Niguarda",
    )

    with patch("services.calendar.write_ics", wraps=write_ics) as mock_write_ics, \
         patch("services.appuntamento.parse_conferma", return_value=info), \
         patch("services.appuntamento.parse_successo", return_value="PREN-12345"):

        flow._invia_riepilogo_dopo_modifica(slot, info=info)

        # Assert write_ics was called with vecchio_appuntamento='28/12/2026 11:00'
        assert mock_write_ics.called
        call_kwargs = mock_write_ics.call_args[1]
        assert call_kwargs.get("vecchio_appuntamento") == "28/12/2026 11:00"

        # Assert bot.send_file was invoked with cancellation caption
        assert bot.send_file.called
        sent_path, sent_caption = bot.send_file.call_args[0][0], bot.send_file.call_args[1]["caption"]
        assert "Include richiesta cancellazione precedente appuntamento del 28/12/2026 11:00" in sent_caption
